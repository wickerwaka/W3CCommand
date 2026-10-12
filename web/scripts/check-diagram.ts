// Check render/diagram_scene.ts (the damage diagram, screen 1, and the title picture, screen 33)
// against the original's renders ($156FA, $15DA2) run under Unicorn.
//
// Replays extracted/diagram_vectors.json (regenerate with
// `uv run python tools/stview.py --export-diagrams extracted/diagram_vectors.json`, which needs
// the emu/out/diagrams captures from tools/capture_diagrams.py): per case the capture plus a
// perturbation (shown system and lift part, system health and status, the diagram's turn, the
// title's turn, turret and model type, the priorities panel / menu). Compares the models drawn
// in order, the palette screen 1 is shown with, the title state titleStep leaves, and every
// projected vertex (float pipeline vs the game's integer one: at most 2 px).
//
//   node web/scripts/check-diagram.ts [--verbose]
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { cameraVertex, diagramPalette, diagramScene, damageStatus, projectST, titleStep } from "../src/render/diagram_scene.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const verbose = process.argv.includes("--verbose");

interface Case {
  base: string;
  patches: [number, string][];
  screen: number;
  views: number;
  models: (number | null)[];
  projs: { model: number; n: number; screen: ([number, number] | null)[] }[];
  palette: number[];
  palette_before: number[];
  title: Record<string, number>;
}

const cases = JSON.parse(readFileSync(join(ROOT, "extracted", "diagram_vectors.json"), "utf8")) as Case[];
const bases = new Map<string, Uint8Array>();
let bad = 0, vertices = 0, worst = 0;
const u16 = (b: Uint8Array, a: number) => (b[a] << 8) | b[a + 1];

for (const [i, c] of cases.entries()) {
  let base = bases.get(c.base);
  if (!base) bases.set(c.base, (base = new Uint8Array(readFileSync(join(ROOT, c.base)))));
  const ram = base.slice();
  for (const [a, hex] of c.patches) ram.set(Buffer.from(hex, "hex"), a);
  const problems: string[] = [];
  const scene = diagramScene(ram, c.screen);
  if ((scene ? 1 : 0) !== c.views) problems.push(`picture ${!!scene}, game views ${c.views}`);
  if (scene) {
    const models = scene.items.map((it) => it.model);
    if (models.join() !== c.models.join()) problems.push(`models ${models} game ${c.models}`);
    if (c.screen === 1) {
      const pal = diagramPalette(ram);
      // with no system shown the render writes nothing (colour 7 is the enter handler's $445,
      // which the port assumes; the perturbation may have changed it)
      const skip7 = !damageStatus(ram);
      const diff = pal.findIndex((w, k) => !(skip7 && k === 7) && w !== c.palette[k]);
      if (diff >= 0) problems.push(`palette colour ${diff} $${pal[diff].toString(16)} game $${c.palette[diff].toString(16)}`);
    }
    // vertices, matched to the game's projections in draw order
    let p = 0;
    for (const it of scene.items) {
      const gp = c.projs[p++];
      if (!gp || gp.model !== it.model) {
        problems.push(`projection of model ${it.model} missing (game ${gp?.model})`);
        break;
      }
      for (let k = 0; k < Math.min(gp.n, it.decoded.vertices.length); k++) {
        const g = gp.screen[k];
        if (!g || it.decoded.undefinedVertices.includes(k)) continue;
        const m = projectST(scene, cameraVertex(it, it.decoded.vertices[k]));
        if (!m) {
          problems.push(`model ${it.model} vertex ${k}: behind the near plane, game ${g}`);
          continue;
        }
        const e = Math.max(Math.abs(m[0] - g[0]), Math.abs(m[1] - g[1]));
        vertices++;
        worst = Math.max(worst, e);
        if (e > 2) problems.push(`model ${it.model} vertex ${k}: (${m[0].toFixed(1)}, ${m[1].toFixed(1)}) game ${g}`);
      }
    }
    if (c.screen === 33) {
      titleStep(ram);
      const got: Record<string, number> = { "6ca": u16(ram, 0x6ca), "6cc": u16(ram, 0x6cc), "6d4": ram[0x6d4], "64d16": u16(ram, 0x64d16) };
      for (const k of Object.keys(c.title))
        if (got[k] !== c.title[k]) problems.push(`title $${k} = ${got[k].toString(16)} game ${c.title[k].toString(16)}`);
    }
  }
  if (problems.length) bad++;
  if (problems.length || verbose)
    console.log(`case ${i} (${c.base.split("/").pop()}, screen ${c.screen}, ${c.patches.length} patches): ${problems.length ? problems.slice(0, 6).join("; ") : "OK"}`);
}
console.log(`${cases.length} cases, ${vertices} vertices (max error ${worst.toFixed(2)} px): ${bad ? `${bad} MISMATCH` : "all OK"}`);
process.exit(bad ? 1 : 0);
