// The two "model list" pictures, as data: the damage screen's carrier diagram (screen 1, render
// $156FA) and the title's turning carrier (screen 33, render $15DA2). No three.js here, so
// web/scripts/check-diagram.ts can run it under node; render/diagram.ts draws it.
//
// Spec (docs/memory-map.md "Model-list pictures"; tools/stview.py diagram_scene, checked against
// the original renders under Unicorn):
// - Both enter handlers ($1404E, $13B7C) build a fake island buffer at $64CFE: header +1 = $FF
//   (no plateau: every entry is depth-sorted on its own), origin (0, -200, 2000), count +$E,
//   entries of $40 bytes from +$12. Object 0 ([$9FA]) is the camera at the origin, angles 0.
//   Screen 1: entry 0 = type $4A (model 74, the carrier wireframe; yaw $64D16 turns by $100 a
//   pass), entries 1-8 = types $4B-$52 (models 75-82, the system overlays) at local z = -1, so
//   they sort in front of 74; only the lit one (+1 bit 1) is drawn: [$B58] 0-7 -> part [$B58],
//   [$B58] = 8 (repair system) -> part [$B5A] cycling 0-7 then 8 = none, 9 -> none.
//   Screen 33: one entry, type [$64D12] (1: model 1, the carrier with its turret), yaw [$64D16]
//   -= 500 a pass, turret [$6CC] += $12C, [$6CA] swinging 0..-14000 (titleStep).
// - The render is the ordinary pipeline ($7BF6 cull/sort, $62D4 draw) after clearing the
//   viewport to colour 0 ($4A64): no horizon, no sea dots, no world objects ($A46/$A47 set).
//   Viewports: screen 1 $4744 = (32, 6)-(287, 133), focal 256, centre (160, 70); screen 33
//   $47C8 = (0, 90)-(319, 199), focal 256, centre (160, 105).
// - Overlays 75-82 are built on the vertex array / edge vectors model 74 leaves ($A8DA, or no
//   basis), so they turn with entry 0's angles; their own angle fields are stale and unused.
// - Colours: model 74's lines are colour 7, the overlays' colour 1 (the $13CA pairs are equal, so
//   line colours are palette indices). Screen 1 shows palette $136A with colour 1 or 7 patched
//   from the status colours at $B68 (damageStatus); screen 33 uses $132A.
import { gameRotation } from "./transforms.ts";
import {
  BASIS_PLATEAU, BASIS_QUAD, MODEL_DRAW_DISC, MODEL_EMPTY, MODEL_EMPTY_2, PARENT_MODEL,
  decodeModel, modelAddress, modelDependsOn, type Model, type Vec3,
} from "./models.ts";

export const MODEL_LIST = 0x64cfe;
export const DIAGRAM_SCREENS = [1, 33] as const;
const OBJ0 = 0x5dcda;
const CAMERA = 0x9fa;
const CULL = 0x8598, LOD = 0x8b58;
const NEAR = 0xa8c, LOD_BIAS = 0xa94, RANGE_BIAS = 0xa98;
const PRIORITY_PANEL = 0xb62, MENU = 0x708;
const SYSTEMS = 0xaf4, SHOWN_SYSTEM = 0xb58, SHOWN_PART = 0xb5a, STATUS_COLOURS = 0xb68;
export const DAMAGE_PALETTE = 0x136a, TITLE_PALETTE = 0x132a;
const DIAGRAM_GREY = 0x445;
const VIEWPORTS: Record<number, number> = { 1: 0x4744, 33: 0x47c8 };
export const TITLE_ENTRY = MODEL_LIST + 0x12; // type +2 ($64D12), yaw +6 ($64D16)

const u8 = (b: Uint8Array, a: number) => b[a];
const u16 = (b: Uint8Array, a: number) => (b[a] << 8) | b[a + 1];
const s16 = (b: Uint8Array, a: number) => (u16(b, a) << 16) >> 16;
const s32 = (b: Uint8Array, a: number) => (b[a] << 24) | (b[a + 1] << 16) | (b[a + 2] << 8) | b[a + 3];
const u32 = (b: Uint8Array, a: number) => s32(b, a) >>> 0;
const w16 = (b: Uint8Array, a: number, v: number) => {
  b[a] = (v >> 8) & 0xff;
  b[a + 1] = v & 0xff;
};

export interface Viewport {
  x0: number; y0: number; x1: number; y1: number; shx: number; shy: number; cx: number; cy: number;
}

export interface DiagramItem {
  /** The model-list entry and its type. */
  entry: number;
  type: number;
  /** The model drawn (LOD by camera-space Z, $7B72). */
  model: number;
  /** Camera-space origin (game frame: X right, Y up, Z depth). */
  origin: Vec3;
  /** Model -> camera rotation, row-major 3x3 (game frame). */
  rot: number[];
  /** The model, decoded for this entry's state (model space). */
  decoded: Model;
  /** Key of the decoded geometry (model + the state it depends on). */
  key: string;
}

export interface DiagramScene {
  screen: number;
  viewport: Viewport;
  near: number;
  /** 16 ST palette words (0RGB, 3 bits a channel) the picture is shown with. */
  palette: number[];
  /** Back to front, as the game draws them. */
  items: DiagramItem[];
}

/** $156FA's colour for the shown system [$B58] -> {k, reg} or null (none shown): k indexes the
 * status colours at $B68 (0 whole $070, 1 damaged but working $007, 2 damaged and down $700),
 * reg is the palette register patched: 1 (the lit overlay; colour 7 goes back to $445) or 7 (the
 * whole wireframe: system 9, and system 8 between two part highlights, [$B5A] = 8). */
export function damageStatus(ram: Uint8Array): { k: number; reg: 1 | 7 } | null {
  const sys = s16(ram, SHOWN_SYSTEM);
  if (sys < 0 || sys > 9) return null; // > 9 would jump through code bytes in the original
  const a = SYSTEMS + 8 * sys;
  const reg: 1 | 7 = sys === 9 || (sys === 8 && u16(ram, SHOWN_PART) === 8) ? 7 : 1;
  if (u16(ram, a) === u16(ram, a + 2)) return { k: 0, reg };
  let working: boolean;
  if (sys === 0 || sys === 4 || sys === 8) working = u16(ram, a + 6) !== 0;
  else if (sys === 9) working = s16(ram, a + 6) >= 0x40;
  else working = u8(ram, a + 6) !== 0; // the shared handler tests the status word's high byte
  return { k: working ? 1 : 2, reg };
}

/** The palette screen 1 is shown with: $136A as $156FA leaves it. */
export function diagramPalette(ram: Uint8Array): number[] {
  const pal = Array.from({ length: 16 }, (_, i) => u16(ram, DAMAGE_PALETTE + 2 * i));
  pal[7] = DIAGRAM_GREY; // the enter handler's (and the colour-1 path's) value
  const st = damageStatus(ram);
  if (st) pal[st.reg] = u16(ram, STATUS_COLOURS + 2 * st.k);
  return pal;
}

/** One title pass of $15DA2 after its drawing (for the app's menu, where no game runs): the
 * turret heading [$6CC] += $12C, the elevation [$6CA] swings by $320 between -14000 and 0
 * (direction [$6D4], flipped when clamped), the model's yaw [$64D16] -= 500. A running game
 * does this itself (render_effects(33)). */
export function titleStep(ram: Uint8Array): void {
  const dir = ram[0x6d4];
  let d0 = ((((dir ? -0x320 : 0x320) + u16(ram, 0x6ca)) << 16) >> 16);
  const d1 = d0;
  if (d0 > 0) d0 = 0;
  else if (d0 < -0x36b0) d0 = -0x36b0;
  w16(ram, 0x6ca, d0 & 0xffff);
  if (d0 !== d1) ram[0x6d4] = dir ^ 0xff;
  w16(ram, 0x6cc, (u16(ram, 0x6cc) + 0x12c) & 0xffff);
  w16(ram, TITLE_ENTRY + 6, (u16(ram, TITLE_ENTRY + 6) - 500) & 0xffff);
}

/** The game drops the low 4 bits of every angle; the camera's (negated) round up. */
const q = (a: number) => a & 0xfff0;
const qc = (a: number) => (-((-a) & 0xfff0)) & 0xffff;

function transpose(m: number[]): number[] {
  return [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]];
}

function mul(a: number[], b: number[]): number[] {
  const r = new Array<number>(9).fill(0);
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) for (let k = 0; k < 3; k++) r[3 * i + j] += a[3 * i + k] * b[3 * k + j];
  return r;
}

export function apply(m: number[], v: Vec3): Vec3 {
  return [
    m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
    m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
    m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
  ];
}

/** Decoded models by state key; small, the pictures show one or two models. */
const decodedCache = new Map<string, Model | null>();

function decodeFor(ram: Uint8Array, index: number, rec: Uint8Array): { model: Model | null; key: string } {
  const deps = modelDependsOn(ram, index);
  let key = `${index}`;
  if (deps.length) {
    const bytes = [...rec.subarray(0x1e, 0x28), ...rec.subarray(0x38, 0x3a), ...ram.subarray(0x6ca, 0x6ce), ...ram.subarray(0xdd6, 0xdda)];
    key += `:${bytes.join(",")}`;
  }
  if (!decodedCache.has(key)) {
    if (decodedCache.size > 32) decodedCache.clear();
    let m: Model | null = null;
    try {
      m = decodeModel(ram, index, { obj: rec, ram });
    } catch {
      m = null;
    }
    decodedCache.set(key, m);
  }
  return { model: decodedCache.get(key)!, key };
}

/** The picture screen 1 or 33 shows now, or null (no picture: the priorities panel on 1, a menu
 * on 33). Reads only state the simulation model keeps (the model list, $9FA, $B58/$B5A, the
 * systems, $6CA/$6CC) plus the game's tables. */
export function diagramScene(ram: Uint8Array, screen: number): DiagramScene | null {
  if (screen === 1 && u16(ram, PRIORITY_PANEL)) return null;
  if (screen === 33 && u16(ram, MENU)) return null;
  const vpa = VIEWPORTS[screen];
  if (vpa === undefined) return null;
  const vw = (i: number) => s16(ram, vpa + 2 * i);
  const viewport: Viewport = { x0: vw(0), y0: vw(1), x1: vw(2), y1: vw(3), shx: vw(4), shy: vw(5), cx: vw(6), cy: vw(7) };
  const cam = u32(ram, CAMERA) || OBJ0;
  const buf = u32(ram, cam + 0x3c);
  const palette = screen === 1 ? diagramPalette(ram) : Array.from({ length: 16 }, (_, i) => u16(ram, TITLE_PALETTE + 2 * i));
  const scene: DiagramScene = { screen, viewport, near: s32(ram, NEAR), palette, items: [] };
  if (!buf) return scene;

  // world -> camera: Roll(-r) Pitch(-p) Yaw(-w) (docs/memory-map.md "World -> camera")
  const cp = qc(u16(ram, cam + 4)), cy = qc(u16(ram, cam + 6)), cr = qc(u16(ram, cam + 8));
  const camYaw = u16(ram, cam + 6); // $A472 subtracts the raw camera heading
  const C = transpose(gameRotation(cp, cy, cr));
  const Cpr = transpose(gameRotation(cp, 0, cr));
  const cx = s32(ram, cam + 0x0a), cyy = s32(ram, cam + 0x0e), cz = s32(ram, cam + 0x12);
  const ox = s32(ram, buf + 2), oy = s32(ram, buf + 6), oz = s32(ram, buf + 0x0a);
  const count = u16(ram, buf + 0x0e);
  const rangeBias = s32(ram, RANGE_BIAS);
  const cullShift = 7 - viewport.shx;

  const visible: { e: number; type: number; X: Vec3 }[] = [];
  for (let k = 0; k < Math.min(count, 64); k++) {
    const e = buf + 0x12 + 0x40 * k;
    if ((ram[e + 1] & 3) !== 3) continue;
    const type = u16(ram, e + 2);
    const dx = ox + s16(ram, e + 0x0a) - cx, dy = oy + s16(ram, e + 0x0c) - cyy, dz = oz + s16(ram, e + 0x0e) - cz;
    // $7BF6's cull: range (Chebyshev, x/z), min Z, frustum with the radius
    const range = (u32(ram, CULL + 16 * type) + rangeBias) >>> 0;
    if (Math.abs(dx) > range || Math.abs(dz) > range) continue;
    const X = apply(C, [dx, dy, dz]);
    const minZ = s32(ram, CULL + 16 * type + 8), radius = s32(ram, CULL + 16 * type + 12);
    const d = Math.max(X[2], 0) * 2 ** cullShift;
    if (X[2] < minZ || Math.abs(X[0]) - radius > d || Math.abs(X[1]) - radius > d / 2) continue;
    visible.push({ e, type, X });
  }
  // depth tree ($6246): larger Z first; equal keys go to the near side, so later entries draw later
  visible.sort((a, b) => b.X[2] - a.X[2]);

  let parentAngles: [number, number, number] | null = null;
  for (const { e, type, X } of visible) {
    const t = LOD + 16 * type, z = X[2] - s32(ram, LOD_BIAS);
    const model = z <= s32(ram, t) ? u16(ram, t + 8) : z <= s32(ram, t + 4) ? u16(ram, t + 10) : u16(ram, t + 12);
    const handler = u32(ram, modelAddress(ram, model));
    if (handler === MODEL_EMPTY || handler === MODEL_EMPTY_2) continue;
    const rec = ram.slice(e, e + 0x40);
    const { model: decoded, key } = decodeFor(ram, model, rec);
    if (!decoded) continue;
    let angles: [number, number, number] = [u16(ram, e + 4), u16(ram, e + 6), u16(ram, e + 8)];
    if (PARENT_MODEL[model] !== undefined && parentAngles) angles = parentAngles;
    else parentAngles = angles;
    let rot: number[];
    if (decoded.basis === BASIS_PLATEAU) rot = C; // the island plateau: camera rotation only
    else if (decoded.basis === BASIS_QUAD) rot = Cpr; // billboards: camera pitch and roll only
    else if (handler === MODEL_DRAW_DISC) rot = C;
    // as the box basis composes it: object roll, pitch, one yaw by (yaw - camera yaw), camera
    else rot = mul(Cpr, gameRotation(q(angles[0]), q((angles[1] - camYaw) & 0xffff), q(angles[2])));
    scene.items.push({ entry: e, type, model, origin: X, rot, decoded, key });
  }
  return scene;
}

/** Camera-space position of a model-space vertex of an item. */
export function cameraVertex(item: DiagramItem, v: Vec3): Vec3 {
  const r = apply(item.rot, v);
  return [item.origin[0] + r[0], item.origin[1] + r[1], item.origin[2] + r[2]];
}

/** $5912 in floats: camera space -> ST screen pixel (unrounded), or null nearer than [$A8C]. */
export function projectST(scene: DiagramScene, p: Vec3): [number, number] | null {
  if (p[2] < scene.near) return null;
  const v = scene.viewport;
  return [v.cx + (p[0] * 2 ** v.shx) / p[2], v.cy - (p[1] * 2 ** v.shy) / p[2]];
}

/** The lines a line model ($9496 wireframe, $99F2/$9A1C) draws this frame: the always-drawn
 * ones, and for $9496 the conditional ones revealed by a face turned towards the camera ($61B2:
 * (ya - yb)(xc - xb) - (xa - xb)(yc - yb) >= 0 on the projected points; no vertex behind the
 * near plane). Returns [v0, v1, colour] in camera space. */
export function itemLines(scene: DiagramScene, item: DiagramItem): { a: Vec3; b: Vec3; colour: number }[] {
  const m = item.decoded;
  const cv = m.vertices.map((v) => cameraVertex(item, v));
  const revealed = new Set<number>();
  for (const f of m.faces ?? []) {
    const pa = projectST(scene, cv[f.a]), pb = projectST(scene, cv[f.b]), pc = projectST(scene, cv[f.c]);
    if (!pa || !pb || !pc) continue;
    if ((pa[1] - pb[1]) * (pc[0] - pb[0]) - (pa[0] - pb[0]) * (pc[1] - pb[1]) >= 0) for (const l of f.lines) revealed.add(l);
  }
  const out: { a: Vec3; b: Vec3; colour: number }[] = [];
  m.lines.forEach((l, i) => {
    if (l.conditional && !revealed.has(i)) return;
    if (l.test) return; // BSP-group lines belong to polygon models (drawn as meshes)
    out.push({ a: cv[l.v0], b: cv[l.v1], colour: l.colour & 0x7f });
  });
  return out;
}
