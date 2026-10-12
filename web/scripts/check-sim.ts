// Check the TypeScript simulation model (src/sim) against the Python reference model.
//
// Replays the vectors that tools/export_sim_vectors.py records while the Python model runs its
// own checks against Hatari and the original code (extracted/sim_vectors/, regenerate with
// `uv run python tools/export_sim_vectors.py`). For every call: same exception, return value,
// events, and CRC-32 of the state regions afterwards.
//
//   npm run check-sim [-- --suite units] [--vector N] [--max-fail 10] [--vectors DIR]
//
// The first failing call of a vector is dumped to web/.sim-fail/, and the replay command that
// diffs it against Python region by region is printed.
import { createReadStream, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { createGunzip, crc32, inflateSync } from "node:zlib";
import { NotPorted, type Region } from "../src/sim/mem.ts";
import { STATE_REGIONS, WorldState } from "../src/sim/world.ts";
import { UNIT_REGIONS, UnitState } from "../src/sim/index.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const VECTORS = resolve(arg("--vectors") ?? join(ROOT, "extracted", "sim_vectors"));
const FAIL_DIR = join(ROOT, "web", ".sim-fail");

type Json = null | number | string | Json[];
interface Call {
  f: string;
  a: Json[];
  p: [number, string][];
  k?: Record<string, Json>;
  r?: Json;
  x?: [string, string];
  e: Json[];
  c: number;
}
interface Vector {
  name: string;
  cls: "WorldState" | "UnitState";
  base: number;
  delta: string;
  calls: Call[];
}

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

const index = JSON.parse(readFileSync(join(VECTORS, "index.json"), "utf8")) as {
  regions: Region[];
  bases: number;
  suites: Record<string, { vectors: number; calls: number }>;
};

// The model must copy exactly the regions Python does (the order may differ: modules append
// theirs in import order); the hash uses Python's order.
const key = (r: Region) => `${r[0]}|${r[1]}|${r[2]}`;
const ours = new Set([...STATE_REGIONS, ...UNIT_REGIONS].map(key));
const theirs = new Set(index.regions.map(key));
const missing = [...theirs].filter((k) => !ours.has(k));
const extra = [...ours].filter((k) => !theirs.has(k));
if (missing.length || extra.length) {
  console.log("region lists differ from Python's:");
  for (const k of missing) console.log(`  missing ${k}`);
  for (const k of extra) console.log(`  extra   ${k}`);
  process.exit(1);
}

function stateCrc(buf: Uint8Array): number {
  let crc = 0;
  for (const [, a, n] of index.regions) crc = crc32(buf.subarray(a, a + n), crc);
  return crc >>> 0;
}

/** As export_sim_vectors.plain: arrays for tuples, 0/1 for booleans, null for None. */
function plain(v: unknown): Json {
  if (v === undefined || v === null) return null;
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number" || typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(plain);
  return `<${(v as object).constructor?.name ?? typeof v}>`;
}

const same = (a: Json, b: Json) => JSON.stringify(a) === JSON.stringify(b);

const bases = new Map<number, Uint8Array>();
function initialState(vec: Vector): Uint8Array {
  let base = bases.get(vec.base);
  if (!base) {
    base = new Uint8Array(inflateSync(readFileSync(join(VECTORS, "bases", `${vec.base}.bin.z`))));
    bases.set(vec.base, base);
  }
  const delta = inflateSync(Buffer.from(vec.delta, "base64"));
  const ram = new Uint8Array(base.length);
  for (let i = 0; i < ram.length; i++) ram[i] = base[i] ^ delta[i];
  return ram;
}

/** Replays one vector; returns null, or a description of its first failing call. */
function runVector(suite: string, n: number, vec: Vector): string | null {
  const Cls = vec.cls === "UnitState" ? UnitState : WorldState;
  const state = Cls.from_ram(initialState(vec));
  for (let k = 0; k < vec.calls.length; k++) {
    const c = vec.calls[k];
    for (const [a, hex] of c.p) state.mem.buf.set(Buffer.from(hex, "hex"), a);
    const what = `${c.f}(${c.a.map((x) => JSON.stringify(x)).join(", ")})`;
    if (c.k) return `call ${k} ${what}: keyword arguments ${JSON.stringify(c.k)} not supported`;
    const fn = (state as unknown as Record<string, unknown>)[c.f];
    if (typeof fn !== "function") return `call ${k}: no method ${c.f}`;
    let result: unknown, error: [string, string] | null = null;
    const events = state.events.length;
    try {
      result = fn.apply(state, c.a);
    } catch (e) {
      if (!(e instanceof Error)) throw e;
      error = [e instanceof NotPorted ? "NotPorted" : e.name, e.message];
      if (!c.x) {
        dump(suite, n, k, state.mem.buf);
        return `call ${k} ${what}: threw ${error.join(": ")}\n${e.stack?.split("\n").slice(1, 6).join("\n")}`;
      }
    }
    const problems: string[] = [];
    if (c.x && !error) problems.push(`expected ${c.x.join(": ")}, returned normally`);
    if (c.x && error && (c.x[0] !== error[0] || c.x[1] !== error[1]))
      problems.push(`expected ${c.x.join(": ")}, threw ${error.join(": ")}`);
    if (!c.x && !(typeof c.r === "string" && c.r.startsWith("<")) && !same(plain(result), c.r ?? null))
      problems.push(`returned ${JSON.stringify(plain(result))}, expected ${JSON.stringify(c.r)}`);
    const ev = plain(state.events.slice(events));
    if (!same(ev, c.e)) problems.push(`events ${JSON.stringify(ev)}, expected ${JSON.stringify(c.e)}`);
    if (stateCrc(state.mem.buf) !== c.c) problems.push("state differs");
    if (problems.length) {
      dump(suite, n, k, state.mem.buf);
      return `call ${k} ${what}: ${problems.join("; ")}`;
    }
  }
  return null;
}

function dump(suite: string, n: number, k: number, buf: Uint8Array): void {
  mkdirSync(FAIL_DIR, { recursive: true });
  const path = join(FAIL_DIR, `${suite}_${n}.bin`);
  writeFileSync(path, buf);
  writeFileSync(path.replace(/\.bin$/, ".call"), String(k));
}

async function main(): Promise<number> {
  const only = arg("--suite");
  const one = arg("--vector") === undefined ? null : Number(arg("--vector"));
  const maxFail = Number(arg("--max-fail") ?? 10);
  let ok = true;
  for (const suite of Object.keys(index.suites)) {
    if (only && suite !== only) continue;
    const file = join(VECTORS, `${suite}.jsonl.gz`);
    if (!existsSync(file)) continue;
    const lines = createInterface({ input: createReadStream(file).pipe(createGunzip()), crlfDelay: Infinity });
    const t0 = Date.now();
    let n = -1, vectors = 0, calls = 0, failed = 0;
    const kinds = new Map<string, number>();
    for await (const line of lines) {
      n++;
      if (one !== null && n !== one) continue;
      const vec = JSON.parse(line) as Vector;
      vectors++;
      calls += vec.calls.length;
      let fail: string | null;
      try {
        fail = runVector(suite, n, vec);
      } catch (e) {
        fail = `crashed: ${(e as Error).stack}`;
      }
      if (fail) {
        failed++;
        const kind = `${vec.name.replace(/ \d+$/, "")}: ${fail.split(":")[0].replace(/^call \d+ /, "").replace(/\(.*/, "")}`;
        kinds.set(kind, (kinds.get(kind) ?? 0) + 1);
        if (failed <= maxFail) {
          console.log(`${suite} vector ${n} (${vec.name}, ${vec.cls}): ${fail}`);
          console.log(`  replay: uv run python tools/export_sim_vectors.py --replay ${suite} ${n} --dump web/.sim-fail/${suite}_${n}.bin`);
        }
      }
    }
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    console.log(`${suite}: ${failed ? "MISMATCH" : "OK"} (${vectors - failed}/${vectors} vectors, ${calls} calls, ${secs} s)`);
    for (const [k, v] of [...kinds].sort((a, b) => b[1] - a[1])) console.log(`  ${v}x ${k}`);
    ok &&= failed === 0;
  }
  return ok ? 0 : 1;
}

process.exit(await main());
