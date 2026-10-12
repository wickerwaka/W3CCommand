// Check the TypeScript model decoder (src/render/models.ts) against the game.
//
// Replays extracted/model_vectors.json (regenerate with
// `uv run python tools/export_model_vectors.py`): for every vector (model + object fields +
// globals) the whole vertex array V and the end of the vertex-op stream must equal what the
// game's own code produced under Unicorn, and the decoded model's vertices and primitives must
// equal tools/stmodel.py's decode.
//
//   node web/scripts/check-models.ts [--ram path/to/ram.bin] [--verbose]
//
// The RAM image defaults to extracted/carrier_payload.bin placed at $400 in 512 KB; --ram takes
// a 512 KB capture (for example emu/out/<name>/ram.bin) instead.
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  MODEL_COUNT, MODEL_DRAW_DISC, buildVertexArray, decodeModel, modelDependsOn,
  type Model, type ModelContext, type Vec3,
} from "../src/render/models.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

interface Prim {
  handler: number;
  basis: number | null;
  nearDist: number | null;
  planes: Vec3[];
  polys: [number[], number][];
  lines: [number, number, number][];
  discs: [number, number | null, number][];
  attachments: number[];
  faces: [number, number, number, number[]][];
  conditionalFrom: number | null;
}
interface Vector {
  model: number;
  obj: Record<string, number>;
  globals: Record<string, number>;
  V: (Vec3 | null)[] | null;
  end: number | null;
}

const data = JSON.parse(readFileSync(join(ROOT, "extracted", "model_vectors.json"), "utf8")) as {
  models: Prim[];
  vectors: Vector[];
};

const ramPath = arg("--ram");
let ram: Uint8Array;
if (ramPath) {
  ram = new Uint8Array(readFileSync(ramPath));
  if (ram.length !== 0x80000) throw new Error(`${ramPath}: expected 512 KB, got ${ram.length} bytes`);
} else {
  ram = new Uint8Array(0x80000);
  ram.set(readFileSync(join(ROOT, "extracted", "carrier_payload.bin")), 0x400);
}
const verbose = process.argv.includes("--verbose");

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function check(vec: Vector): string[] {
  const prim = data.models[vec.model];
  const obj = new Uint8Array(0x40);
  for (const [k, v] of Object.entries(vec.obj)) {
    obj[+k] = (v >> 8) & 0xff;
    obj[+k + 1] = v & 0xff;
  }
  const g = new Uint8Array(ram);
  for (const [k, v] of Object.entries(vec.globals)) {
    g[+k] = (v >> 8) & 0xff;
    g[+k + 1] = v & 0xff;
  }
  const ctx: ModelContext = { obj, ram: g };
  const problems: string[] = [];

  // Vertex array against the game's code.
  if (vec.V) {
    const built = buildVertexArray(ram, vec.model, ctx);
    if (!built) return ["buildVertexArray returned null"];
    const diff: number[] = [];
    for (let i = 0; i < 256; i++) if (!same(built.V[i] ?? null, vec.V[i])) diff.push(i);
    if (diff.length) {
      const i = diff[0];
      problems.push(`${diff.length} V entries differ, first V[$${i.toString(16)}] = ${JSON.stringify(built.V[i])}, game ${JSON.stringify(vec.V[i])}`);
    }
    if (built.end !== vec.end) problems.push(`vertex ops end at $${built.end.toString(16)}, game $${vec.end?.toString(16)}`);
  }

  // Decoded model against stmodel.py.
  const m = decodeModel(ram, vec.model, ctx);
  if (!m) return [...problems, "decodeModel returned null"];
  const expectLines = [...prim.lines];
  if (prim.handler === 0x99f2 || prim.handler === 0x9a1c) expectLines.length = Math.min(expectLines.length, (vec.obj["56"] ?? 0) & 0xff);
  const expectDiscs = prim.discs.map(([v, r, c]) => [v, prim.handler === MODEL_DRAW_DISC ? (vec.obj["30"] ?? 0) : r, c]);
  const cmp = (what: string, ours: unknown, theirs: unknown) => {
    if (!same(ours, theirs)) problems.push(`${what}: ${JSON.stringify(ours).slice(0, 200)} != ${JSON.stringify(theirs).slice(0, 200)}`);
  };
  cmp("handler", m.handler, prim.handler);
  cmp("basis", m.basis, prim.basis);
  cmp("nearDist", m.nearDist, prim.nearDist);
  cmp("planes", m.planes, prim.planes);
  cmp("polys", m.polys.map((p) => [p.idx, p.colour]), prim.polys);
  cmp("order", m.polys.map((p) => p.order), prim.polys.map((_, i) => i));
  cmp("lines", m.lines.map((l) => [l.v0, l.v1, l.colour]), expectLines);
  if (prim.conditionalFrom !== null)
    cmp("conditional", m.lines.map((l) => !!l.conditional), expectLines.map((_, i) => i >= prim.conditionalFrom!));
  else cmp("conditional", m.lines.some((l) => l.conditional), false);
  cmp("discs", m.discs.map((d) => [d.v, d.radius, d.colour]), expectDiscs);
  cmp("attachments", m.attachments, prim.attachments);
  cmp("faces", (m.faces ?? []).map((f) => [f.a, f.b, f.c, f.lines]), prim.faces);
  cmp("dynamic", m.dynamic, modelDependsOn(ram, vec.model).length > 0);

  // Model vertices = V[0..n) as the game left it, n = max(nverts, highest referenced index + 1).
  if (vec.V) {
    const expectVerts = m.vertices.map((_, i) => vec.V![i] ?? [0, 0, 0]);
    cmp("vertices", m.vertices, expectVerts);
    cmp("undefinedVertices", m.undefinedVertices, m.vertices.map((_, i) => i).filter((i) => vec.V![i] === null));
    const used = [...m.polys.flatMap((p) => p.idx), ...m.lines.flatMap((l) => [l.v0, l.v1]), ...m.discs.map((d) => d.v)];
    const bad = used.filter((i) => vec.V![i] === null);
    if (bad.length) problems.push(`primitives reference unwritten vertices ${[...new Set(bad)].join(",")}`);
  }
  return problems;
}

let failures = 0;
const perModel = new Map<number, number>();
const layered: string[] = [];
for (const vec of data.vectors) {
  let problems: string[];
  try {
    problems = check(vec);
  } catch (e) {
    problems = [`threw ${(e as Error).message}`];
  }
  perModel.set(vec.model, (perModel.get(vec.model) ?? 0) + 1);
  if (problems.length) {
    failures++;
    if (failures <= 20)
      console.log(`model ${vec.model} obj ${JSON.stringify(vec.obj)} globals ${JSON.stringify(vec.globals)}:\n  ${problems.join("\n  ")}`);
  }
}
for (let i = 0; i < MODEL_COUNT; i++) {
  const m: Model | null = decodeModel(ram, i);
  if (!m) continue;
  const n = m.polys.filter((p) => p.layer > 0).length;
  if (n) layered.push(`${i}:${n}`);
  if (verbose)
    console.log(`model ${i}: ${m.vertices.length} verts, ${m.polys.length} polys (${n} layered, max ${Math.max(0, ...m.polys.map((p) => p.layer))}), ` +
      `${m.polys.filter((p) => p.plane >= 0).length} on a split plane, deps [${modelDependsOn(ram, i).join(" ")}]`);
}
const missing = [...Array(MODEL_COUNT).keys()].filter((i) => !perModel.has(i));
if (missing.length) {
  console.log(`no vectors for models ${missing.join(",")}`);
  failures++;
}
console.log(`models with layered (decal) polygons: ${layered.join(" ") || "none"}`);
console.log(`${data.vectors.length} vectors over ${perModel.size} models, ${failures} failures`);
process.exit(failures ? 1 : 0);
