/**
 * Unit layer: the state the screens' render routines write (docs/units.md §10.8).
 *
 * Port of tools/stunits_render.py (the verified reference). The app doesn't run the original
 * render, so it calls render_effects() once per frame after the pass, for the screen shown:
 * screen_dispatch $14472 without the drawing. map_render_effects() is the maps' part (screens
 * 2, 17, 25: the island nearest the map centre $764 and its owner/position, the islands
 * panel's recount). For the 3D views that is the camera placement, the pipeline's
 * visible-object collection ($7BF6) and depth-tree draw ($62D4) as far as they write state
 * (island context, runway entries, object pairs $A68-$A73, attached vehicles' order in the
 * carrier's BSP), and the overlays' writes. The class sits between UnitCore and SetupUnits in
 * the inheritance chain.
 */
import { NotPorted, s16, s32, u32, w16 } from "./mem.ts";
import type { Mem, Region } from "./mem.ts";
import { ECONOMY, ECONOMY_SIZE, ISLAND_SIZE, ISLAND_TABLE, OWNERS } from "./world.ts";
import {
  CARRIER, DEBRIS, ISLAND_BUFFERS, OBJECTS, OBJECT_SIZE, SCREEN, SHARDS, SINE, UNIT_INPUT_REGIONS, UNIT_REGIONS,
  UnitCore,
} from "./units_core.ts";

export const MAP_SCREENS = [2, 17, 25];
export const MAP_ZOOM = 0x75a, MAP_X = 0x75c, MAP_Z = 0x760, MAP_INFO = 0x766;
export const NEAREST = 0x764, NEAREST_D2 = 0x73e, NEAREST_OWNER = 0x74c, NEAREST_X = 0x742,
  NEAREST_Z = 0x746, MARKER_ZOOM = 0x750;
export const DRONE_TYPE = 66; // screen 11's drone elements $42A4E + $16 k have types 66-69
export const FLAG_TABLE = 0x40eb2;
export const TITLE_TIMER = 0x93a, TITLE_FLAG = 0x124c;

// The 3D views ($7BF6 scene_collect_visible, $62D4 draw_depth_tree; docs/units.md §10.8).
export const CAMERA = 0x9fa, VIEWPORT = 0x6fe;
export const CULL = 0x8598, LOD = 0x8b58; // per type: range, -, min Z, radius / near, mid, near/mid/far model
export const CAM_PAIRS = 0xa74; // (sin, cos) of -pitch, -yaw, -roll of the camera ($A484-$A4A4)
export const OBJ_PAIRS = 0xa68; // pitch $A68, yaw $A6C, roll $A70 of the last box drawn
export const ISLAND_REL = 0xa4a; // island origin - camera: x $A4A, y $A4E, z $A52 (longs)
export const RECT = 0xa56; // the island's partition rectangle: x0 $A56, z0 $A5A, x1 $A5E, z1 $A62
export const LOD_BIAS = 0xa94, RANGE_BIAS = 0xa98; // the turret zoom's biases
export const HIDE_OBJECTS = 0xa46, HIDE_DEBRIS = 0xa47, OBJECT_COUNT = 0xa48;
export const CAMERA_HIGH = 0x8a6; // $7BF6: camera y >= $50 (st.b), kept between frames
export const RADAR_ON = 0x1230;
export const MODEL_LIST = 0x64cfe; // the damage diagram / title model list (units_controls)
export const REDRAW = 0x95c; // $95C bay-status redraw countdown, $95E/$960 arming panel redraws
// Per model ($4A06A): the object pairs its basis routine leaves in $A68-$A73 (B box $A78C/$A89A/
// $ADA4, C carrier with turret $AA78/$AABE/$AB42, T turret $ABE8, W Walrus $AC16, 2 two boxes
// $AF4E; '.' none: empty, disc, trees $AA24, island $A660, overlays $A8DA).
export const MODEL_PAIRS = ".CCCBBWB.BBBBBBBBBBBBBB.....BBB.BBBBBB2BBBBBBBBBB2BBBBBBBBBBBBBBBBBBBBBBBBB.........T" +
  "BBBBBBBBBBBBBB";
export const SHIFT_MAX = 15; // $8578: the frustum shift table covers shx 0-15
export const CARRIER_MODELS: Record<number, number> = { 2: 0x43d3c, 3: 0x44264 }; // handler, dist, basis, ...

// State the renders write that the other regions don't cover.
for (const _region of [
  ["map render $73E", NEAREST_D2, 4],
  ["map render owner $74C", NEAREST_OWNER, 1],
  ["map render $750", MARKER_ZOOM, 2],
  ["title timer $93A", TITLE_TIMER, 4],
  ["title flag $124C", TITLE_FLAG, 2],
  ["object lod table $8B58", LOD, 84 * 16],
  ["camera high $8A6", CAMERA_HIGH, 1],
  ["model list $64CFE", MODEL_LIST, 0x12 + 9 * 0x40],
  ["carrier models 2-3 $43D3C", CARRIER_MODELS[2], 0x448c0 - CARRIER_MODELS[2]],
  ["bay redraw countdowns $95C", REDRAW, 6],
] as Region[]) {
  if (!UNIT_REGIONS.some((r) => r[0] === _region[0] && r[1] === _region[1] && r[2] === _region[2])) {
    UNIT_REGIONS.push(_region);
  }
}
for (const n of ["object lod table $8B58", "carrier models 2-3 $43D3C"]) UNIT_INPUT_REGIONS.add(n);

/** $65C4: (d asr 8) divs.w zoom, floored (decremented when the remainder is negative,
 * unless that overflows); a quotient beyond a word gives $7FFF / -$7FFF by the dividend's
 * sign. */
export function map_div(d: number, zoom: number): number {
  const d4 = s32(d) >> 8;
  const z = s16(zoom);
  if (z === 0) throw new NotPorted("map zoom 0 (divs by zero)");
  let q = Math.trunc(d4 / z);
  if (!(-0x8000 <= q && q <= 0x7fff)) return d4 < 0 ? -0x7fff : 0x7fff;
  if (d4 - q * z < 0 && q !== -0x8000) q -= 1;
  return q;
}

// ---- the 3D pipeline's fixed-point maths (docs/memory-map.md "Bridge view camera and projection")
/** add.l d,d twice then swap: the high word of 4 v (32-bit wrap), signed. */
export function _hi4(v: number): number {
  return s16(Math.floor(u32(v * 4) / 0x10000));
}

/** move.l; bpl; neg.l: |v| as an unsigned long ($80000000 stays). */
export function _abs32(v: number): number {
  return u32(v < 0 ? -v : v);
}

type Vec3 = [number, number, number];

/** $A4F4/$A4EA/$A5BC/$A5B2 (pair at `pair`: sin, cos) on words: u' = u c - v s,
 * v' = v c + u s, each the high word of 4 times the long. */
export function rot_a(m: Mem, u: number, v: number, pair: number): [number, number] {
  const s = m.s16(pair), c = m.s16(pair + 2);
  u = s16(u);
  v = s16(v);
  return [_hi4(u * c - v * s), _hi4(v * c + u * s)];
}

/** $A558/$A54E: x' = x c + z s, z' = z c - x s. */
export function rot_b(m: Mem, x: number, z: number, pair: number): [number, number] {
  const s = m.s16(pair), c = m.s16(pair + 2);
  x = s16(x);
  z = s16(z);
  return [_hi4(x * c + z * s), _hi4(z * c - x * s)];
}

/** $A4B4/$A518/$A57C: halve both longs (asr) until within +-$4844, rotate the words,
 * ext.l and shift back (asl.l). */
export function rot_long(m: Mem, f: typeof rot_a, x: number, y: number, pair: number): [number, number] {
  x = s32(x);
  y = s32(y);
  let k = 0;
  while (!(-0x4844 <= x && x <= 0x4844 && -0x4844 <= y && y <= 0x4844)) {
    x = Math.floor(x / 2);
    y = Math.floor(y / 2);
    k += 1;
  }
  const [a, b] = f(m, x, y, pair);
  return [s32(a * 2 ** k), s32(b * 2 ** k)];
}

/** $7C68: a camera-relative world vector (longs) to camera space: yaw, pitch, roll by the
 * camera pairs $A78, $A74, $A7C. */
export function cam_long(m: Mem, dx: number, dy: number, dz: number): Vec3 {
  let x: number, y: number, z: number;
  [x, z] = rot_long(m, rot_b, dx, dz, CAM_PAIRS + 4);
  [y, z] = rot_long(m, rot_a, dy, z, CAM_PAIRS);
  [x, y] = rot_long(m, rot_a, x, y, CAM_PAIRS + 8);
  return [x, y, z];
}

/** $7DB0: an island entry's local words to camera space (no prescale). */
export function cam_word(m: Mem, x: number, y: number, z: number): Vec3 {
  [x, z] = rot_b(m, x, z, CAM_PAIRS + 4);
  [y, z] = rot_a(m, y, z, CAM_PAIRS);
  [x, y] = rot_a(m, x, y, CAM_PAIRS + 8);
  return [x, y, z];
}

/** A depth-tree node: [key, near, far, object, X, Y]. */
type TreeNode = [number, number | null, number | null, number, number, number];

/** depth_tree_insert $6246 (and $622E / $623A, which start below the root's far (+6) /
 * near (+4) link): 18-byte nodes keyed on camera Z; a key <= the node's goes near (+4),
 * else far (+6). draw_depth_tree $62D4 walks far side, node, near side. */
export class DepthTree {
  nodes: TreeNode[] = [];

  insert(key: number, obj: number, x: number, y: number, start: string | null = null): void {
    if (start === null && this.nodes.length === 0x80) throw new NotPorted("depth tree full: trap #12 ($6254)");
    if (this.nodes.length >= 0x80) throw new NotPorted("more than 128 depth-tree nodes (the buffer at $58E2A overflows)");
    const n = this.nodes.length;
    if (n === 0 && start !== null) throw new NotPorted("forced depth-tree insert without a root");
    this.nodes.push([key, null, null, obj, x, y]);
    if (n === 0) return;
    let cur: number;
    if (start === null) {
      cur = 0;
    } else {
      const side = start === "far" ? 2 : 1;
      const child = this.nodes[0][side];
      if (child === null) {
        this.nodes[0][side] = n;
        return;
      }
      cur = child;
    }
    for (;;) {
      const side = key <= this.nodes[cur][0] ? 1 : 2;
      const child = this.nodes[cur][side];
      if (child === null) {
        this.nodes[cur][side] = n;
        return;
      }
      cur = child;
    }
  }

  in_order(): TreeNode[] {
    const out: TreeNode[] = [];
    const stack: number[] = [];
    let cur: number | null = this.nodes.length ? 0 : null;
    while (stack.length || cur !== null) {
      while (cur !== null) {
        stack.push(cur);
        cur = this.nodes[cur][2];
      }
      const i = stack.pop() as number;
      out.push(this.nodes[i]);
      cur = this.nodes[i][1];
    }
    return out;
  }
}

/** A radar list entry ($5892A): type, camera X, Z. */
type RadarEntry = [number, number, number];

// $144A0: the screens whose render writes state (the others write none).
export const RENDER_EFFECTS: Record<number, string> = {
  0: "_bridge_render", 1: "_damage_render", 2: "map_render_effects", 4: "_messages_render",
  8: "_turret_render", 9: "_flares_render", 10: "_drone_view_render", 11: "_radar_render",
  16: "_walrus_arming_render", 17: "map_render_effects", 18: "_walrus_view_render",
  19: "_dock_render", 24: "_manta_arming_render", 25: "map_render_effects",
  26: "_deck_render", 27: "_manta_view_render", 33: "_title_render",
};

export abstract class RenderUnits extends UnitCore {
  // units_controls: the arming sub-panel by bay status ($11738/$11E60) and the loadout panels'
  // redraw callbacks.
  abstract _subpanel(rec: number): number;
  abstract _manta_panel(): void;
  abstract _walrus_panel(): void;

  /** screen_dispatch $14472 for [$718] (or `screen`) without the drawing: the debug-mode
   * keys ($210F0, $17874), then the screen's render ($144A0) and the panel callbacks it
   * runs (RENDER_EFFECTS: the 3D views 0, 1, 8, 9, 10, 16, 18, 19, 24, 26, 27, 33 with
   * their camera and pipeline writes, the maps 2, 17, 25, messages 4, defence radar 11;
   * the other screens' renders write no state). With the debug freeze on ([$1189] and
   * key $866 held) game_main skips the render (and frame_flip). */
  render_effects(screen: number | null = null): void {
    const m = this.mem;
    if (screen === null) screen = m.u16(SCREEN);
    if (m.u8(0x1189) && m.u8(0x866)) return; // $413E: the debug freeze
    if (m.u8(0x1189)) m.set8(0x871, m.u8(0x871) & 0xfe); // $210F0: debug display key
    const fn = RENDER_EFFECTS[screen];
    if (fn) (this as any)[fn]();
    // $17874: in a running game in debug mode, key $869 toggles the frame-rate display.
    if (m.u8(0x462) && !m.u8(0x461) && m.u8(0x1189) && m.u8(0x869) & 1) {
      m.set8(0x869, m.u8(0x869) & 0xfe);
      m.set8(0x122d, ~m.u8(0x122d) & 0xff);
    }
  }

  /** $14E3C (screens 2, 17, 25) without the drawing. With the island information box up
   * ([$766] != 0) it only draws. Otherwise $65E2: [$764] high byte = $FF (st.b), [$73E] =
   * 2000000, [$750] = min(zoom, 8); on the carrier map in islands mode ([$710] != 0) the
   * islands panel's recount $CDF2; then each island within +-255 px of the centre (unsigned,
   * after neg) whose mx^2 + my^2 (mulu) is below [$73E] (unsigned) becomes the nearest:
   * [$73E], [$764] index, [$74C] owner, [$742/$746] position. Last, the unit markers leave
   * their turn pair in $A6C/$A6E: on screen 2 the carrier's ((heading + $800) & $F000)
   * sin/cos; on 17/25 the cursor's (0, $4000). */
  map_render_effects(): void {
    const m = this.mem;
    if (m.u16(MAP_INFO)) return;
    m.set8(NEAREST, 0xff);
    m.set32(NEAREST_D2, 0x1e8480);
    const zoom = m.u16(MAP_ZOOM);
    m.set16(MARKER_ZOOM, s16(zoom) > 8 ? 8 : zoom);
    const screen = m.u16(SCREEN);
    if (screen === 2 && m.u16(0x710)) this.island_counts();
    const cx = m.u32(MAP_X), cz = m.u32(MAP_Z);
    for (let i = 0; i < 64; i++) {
      const isl = ISLAND_TABLE + ISLAND_SIZE * i;
      const mx = map_div(s32(m.u32(isl + 2) - cx), zoom);
      const my = map_div(s32(-s32(m.u32(isl + 6) - cz)), zoom);
      const ax = mx < 0 ? -mx & 0xffff : mx, ay = my < 0 ? -my & 0xffff : my;
      if (ax >= 0x100 || ay >= 0x100) continue;
      const d2 = ax * ax + ay * ay;
      if (d2 >= m.u32(NEAREST_D2)) continue;
      m.set32(NEAREST_D2, d2);
      m.set16(NEAREST, i);
      m.set8(NEAREST_OWNER, m.u8(OWNERS + i));
      m.set32(NEAREST_X, m.u32(isl + 2));
      m.set32(NEAREST_Z, m.u32(isl + 6));
    }
    if (screen === 2) {
      const d7 = w16(m.u16(CARRIER + 6) + 0x800) & 0xf000;
      m.set16(0xa6c, m.u16(SINE + (d7 >> 3)));
      m.set16(0xa6e, m.u16(SINE + (w16(d7 + 0x4000) >> 3)));
    } else {
      m.set32(0xa6c, 0x4000);
    }
  }

  /** $CDF2: [$D54] friendly islands, [$D56] hostile, and per friendly island
   * [$D58 + 2 * (economy +0 byte)] + 1, after clearing $D54, $D56, $D5A, $D5C, $D5E. */
  island_counts(): void {
    const m = this.mem;
    for (const a of [0xd54, 0xd56, 0xd5a, 0xd5c, 0xd5e]) m.set16(a, 0);
    for (let i = 0; i < 64; i++) {
      const o = m.u8(OWNERS + i);
      if (o === 1) {
        m.set16(0xd54, m.u16(0xd54) + 1);
        const a = 0xd58 + 2 * m.u8(ECONOMY + ECONOMY_SIZE * i);
        m.set16(a, m.u16(a) + 1);
      } else if (o) {
        m.set16(0xd56, m.u16(0xd56) + 1);
      }
    }
  }

  // ---- the 3D pipeline: what $7BF6 and the depth-tree draw leave in the state ------------
  /** $A438: (sin, cos) of a 16-bit angle (andi.b #$F0: the low 4 bits dropped) at addr. */
  _pair(addr: number, angle: number): void {
    const m = this.mem;
    const a = angle & 0xfff0;
    m.set16(addr, m.u16(SINE + (a >> 3)));
    m.set16(addr + 2, m.u16(SINE + (w16(a + 0x4000) >> 3)));
  }

  /** scene_collect_visible $7BF6 for the camera [$9FA] -> [depth tree, radar list or null,
   * n_flat]. State it writes: the camera pairs $A74-$A7F; with an island buffer (+$3C)
   * holding entries, the island's camera-relative origin $A4A/$A4E/$A52; with its plateau
   * (entry 0) visible, the partition rectangle $A56-$A65, [$8A6] = camera y >= $50 and, when
   * that is set, the runway entries 1..n (n = header +$10): bit 2 of +1 cleared, then set
   * with their camera X, Y, Z in +$1E/+$22/+$26 when visible. The radar list ($5892A, while
   * [$1230]) and the tree are drawing scratch, returned. */
  scene_collect(): [DepthTree, RadarEntry[] | null, number | null] {
    const m = this.mem;
    const tree = new DepthTree();
    const radar: RadarEntry[] | null = m.u8(RADAR_ON) ? [] : null;
    const shx = m.u16(m.u32(VIEWPORT) + 8); // $8560 patches the frustum shift
    if (shx > SHIFT_MAX) throw new NotPorted(`viewport shx ${shx} beyond the shift table $8578`);
    const cam = m.u32(CAMERA);
    for (let k = 0; k < 3; k++) this._pair(CAM_PAIRS + 4 * k, -m.u16(cam + 4 + 2 * k));
    const cx = s32(m.u32(cam + 0xa)), cy = s32(m.u32(cam + 0xe)), cz = s32(m.u32(cam + 0x12));
    const a98 = m.u32(RANGE_BIAS);

    const cull = (typ: number): [number, number, number] => {
      const b = CULL + s16(w16(typ * 16));
      return [m.u32(b), s32(m.u32(b + 8)), m.u32(b + 12)];
    };
    const in_range = (typ: number, dx: number, dz: number): boolean => {
      const d5 = u32(cull(typ)[0] + a98);
      return _abs32(dx) <= d5 && _abs32(dz) <= d5;
    };
    /** Z >= min Z, then the frustum test $853A. */
    const visible = (typ: number, x: number, y: number, z: number): boolean => {
      const [, min_z, radius] = cull(typ);
      if (z < min_z) return false;
      let d5 = Math.max(z, 0);
      d5 = shx <= 7 ? s32(d5 * 2 ** (7 - shx)) : Math.floor(d5 / 2 ** Math.min(shx - 7, 8));
      if (s32(_abs32(x) - radius) > d5) return false;
      return s32(_abs32(y) - radius) <= Math.floor(d5 / 2);
    };
    const radar_add = (typ: number, x: number, z: number): void => {
      if (radar !== null && radar.length < 0x7f) radar.push([typ, x, z]); // $6324
    };
    /** $818A / $829A: an island entry, culled and inserted. */
    const entry = (e: number, side: string | null, rel: Vec3, origin: Vec3): void => {
      const f = m.u8(e + 1);
      if (!(f & 1 && f & 2)) return;
      const lx = m.s16(e + 0xa), ly = m.s16(e + 0xc), lz = m.s16(e + 0xe);
      const typ = m.u16(e + 2);
      if (!in_range(typ, s32(lx + rel[0]), s32(lz + rel[2]))) return;
      let [x, y, z] = cam_word(m, lx, ly, lz);
      x = s32(x + origin[0]);
      y = s32(y + origin[1]);
      z = s32(z + origin[2]);
      radar_add(typ, x, z);
      if (visible(typ, x, y, z)) tree.insert(z, e, x, y, side);
    };
    const check_entry = (e: number, buf: number): void => {
      const n = ISLAND_BUFFERS.includes(buf) ? 64 : 9;
      if (!(buf + 0x12 <= e && e < buf + 0x12 + 0x40 * n)) {
        throw new NotPorted(`island entry $${e.toString(16).toUpperCase()} outside the buffer $${buf.toString(16).toUpperCase()}`);
      }
    };

    let rect: number[] | null = null;
    let n_flat: number | null = null;
    let mode = "plain"; // world objects and debris: plain / inside / outside
    let cam_out = 0;
    let entry_side: string | null = null;
    const buf = m.u32(cam + 0x3c);
    if (buf) {
      if (!(ISLAND_BUFFERS.includes(buf) || buf === MODEL_LIST)) {
        throw new NotPorted(`camera island buffer $${buf.toString(16).toUpperCase()}`);
      }
      n_flat = m.u16(buf + 0x10);
      const count = m.u16(buf + 0xe);
      if (count) {
        const rel: Vec3 = [s32(m.u32(buf + 2) - cx), s32(m.u32(buf + 6) - cy), s32(m.u32(buf + 0xa) - cz)];
        m.set32(ISLAND_REL, rel[0]);
        m.set32(ISLAND_REL + 8, rel[2]);
        m.set32(ISLAND_REL + 4, rel[1]);
        const origin = cam_long(m, rel[0], rel[1], rel[2]);
        const e0 = buf + 0x12;
        if (m.u8(buf + 1) & 0x80) { // $824A: every entry sorted
          for (let k = 0; k < count; k++) {
            check_entry(e0 + 0x40 * k, buf);
            entry(e0 + 0x40 * k, null, rel, origin);
          }
        } else {
          let d7 = count - 1;
          let f = m.u8(e0 + 1);
          const t0 = m.u16(e0 + 2);
          let root = false;
          if (f & 1 && f & 2 && in_range(t0, rel[0], rel[2])) {
            radar_add(t0, origin[0], origin[2]);
            root = visible(t0, origin[0], origin[1], origin[2]);
          }
          if (!root) { // $8238: entries past the runway ones
            d7 = w16(d7 - n_flat);
            let e = u32(e0 + s16(w16((n_flat + 1) * 0x40)));
            for (;;) {
              d7 = w16(d7 - 1);
              if (d7 === 0xffff) break;
              check_entry(e, buf);
              entry(e, null, rel, origin);
              e += 0x40;
            }
          } else {
            tree.insert(origin[2], e0, origin[0], origin[1]);
            const hx = w16(m.u16(e0 + 0x1e) - 0x50);
            const hz = w16(m.u16(e0 + 0x20) - 0x50);
            rect = [s32(rel[0] - hx), s32(rel[2] - hz), s32(rel[0] + hx), s32(rel[2] + hz)];
            for (let k = 0; k < 4; k++) m.set32(RECT + 4 * k, rect[k]);
            let e = e0 + 0x40;
            d7 = w16(d7 - 1);
            const high = cy >= 0x50;
            m.set8(CAMERA_HIGH, high ? 0xff : 0);
            if (!high) {
              d7 = w16(d7 - n_flat);
              e = u32(e + s16(w16(n_flat * 0x40)));
            } else {
              for (let k = 0; k < n_flat; k++) { // $7D5A: the runway entries
                check_entry(e, buf);
                m.set8(e + 1, m.u8(e + 1) & 0xfb);
                f = m.u8(e + 1);
                if (f & 1 && f & 2) {
                  const lx = m.s16(e + 0xa), ly = m.s16(e + 0xc), lz = m.s16(e + 0xe);
                  const typ = m.u16(e + 2);
                  if (in_range(typ, s32(lx + rel[0]), s32(lz + rel[2]))) {
                    let [x, y, z] = cam_word(m, lx, ly, lz);
                    x = s32(x + origin[0]);
                    y = s32(y + origin[1]);
                    z = s32(z + origin[2]);
                    if (visible(typ, x, y, z)) {
                      m.set8(e + 1, f | 4);
                      m.set32(e + 0x1e, x);
                      m.set32(e + 0x22, y);
                      m.set32(e + 0x26, z);
                    }
                  }
                }
                e += 0x40;
              }
              d7 = w16(d7 - n_flat);
            }
            cam_out = this._outcode(rect, 0, 0);
            if (cam_out) { // $7FCC
              mode = "outside";
              entry_side = high ? "near" : "far";
            } else {
              mode = "inside";
              entry_side = "near";
            }
            if (!(d7 & 0x8000)) {
              for (let k = 0; k < d7 + 1; k++) {
                check_entry(e, buf);
                entry(e, entry_side, rel, origin);
                e += 0x40;
              }
            }
          }
        }
      }
    }

    const side_of = (dx: number, dz: number): string | null => {
      if (mode === "plain") return null;
      const oc = this._outcode(rect as number[], dx, dz);
      if (mode === "inside") return oc ? "far" : "near";
      if (!oc) return entry_side;
      return oc & cam_out ? "near" : "far";
    };

    const n = m.u16(OBJECT_COUNT);
    if (m.u8(HIDE_OBJECTS) || !n) { // $8402: debris sorted plainly
      mode = "plain";
    } else { // world objects
      if (n > 64) throw new NotPorted(`world object count [$A48] = ${n}`);
      for (let i = 0; i < n; i++) {
        const a = OBJECTS + OBJECT_SIZE * i;
        const f = m.u8(a + 1);
        if (!(f & 1 && f & 2) || f & 4) continue;
        const dx = s32(m.u32(a + 0xa) - cx), dy = s32(m.u32(a + 0xe) - cy), dz = s32(m.u32(a + 0x12) - cz);
        const typ = m.u16(a + 2);
        if (!in_range(typ, dx, dz)) continue;
        const side = side_of(dx, dz);
        const [x, y, z] = cam_long(m, dx, dy, dz);
        radar_add(typ, x, z);
        if (visible(typ, x, y, z)) tree.insert(z, a, x, y, side);
      }
    }
    if (!m.u8(HIDE_DEBRIS)) { // the debris bursts $11D8
      for (let k = 0; k < 4; k++) {
        const b = DEBRIS + 0x12 * k;
        if (!(m.u8(b) & 1) || !m.u32(b + 0xe)) continue;
        const dx = s32(m.u32(b + 2) - cx), dy = s32(m.u32(b + 6) - cy), dz = s32(m.u32(b + 0xa) - cz);
        if (_abs32(dx) >= 0x10000 || _abs32(dz) >= 0x10000 || _abs32(dy) >= 0x10000) continue;
        const side = side_of(dx, dz);
        const origin = cam_long(m, dx, dy, dz);
        let sh = m.u32(b + 0xe);
        while (sh) { // $84A4: the shards
          if (!(SHARDS <= sh && sh < SHARDS + 64 * 0x1a)) {
            throw new NotPorted(`debris shard pointer $${sh.toString(16).toUpperCase()}`);
          }
          const lx = m.s16(sh + 0xa), ly = m.s16(sh + 0xc), lz = m.s16(sh + 0xe);
          const typ = m.u16(sh + 2);
          if (in_range(typ, s32(lx + dx), s32(lz + dz))) {
            let [x, y, z] = cam_word(m, lx, ly, lz);
            x = s32(x + origin[0]);
            y = s32(y + origin[1]);
            z = s32(z + origin[2]);
            if (visible(typ, x, y, z)) tree.insert(z, sh, x, y, side);
          }
          sh = m.u32(sh + 0x16);
        }
      }
    }
    return [tree, radar, n_flat];
  }

  /** $7FA0: bit 0 x < x0, 1 x > x1, 2 z < z0, 3 z > z1 (signed longs). */
  _outcode(rect: number[], x: number, z: number): number {
    const [x0, z0, x1, z1] = rect;
    return (x < x0 ? 1 : 0) | (x > x1 ? 2 : 0) | (z < z0 ? 4 : 0) | (z > z1 ? 8 : 0);
  }

  /** draw_depth_tree $62D4 without the drawing: the island plateau (type 8) is followed by
   * its runway entries with bit 2 set while [$8A6]; each object's model ($7B72: LOD by Z -
   * [$A94]) leaves its basis routine's object pairs in $A68-$A73; the carrier models 2/3
   * draw the vehicles on the lift [$6D6] and in the dock [$6DA] inside their BSP. */
  scene_draw(tree: DepthTree, radar: RadarEntry[] | null, n_flat: number | null): void {
    const m = this.mem;
    for (const [key, , , obj, x, y] of tree.in_order()) {
      const typ = m.u16(obj + 2);
      this._draw_object(obj, typ, key, [x, y, key], radar);
      if (typ === 8 && m.u8(CAMERA_HIGH) && n_flat) {
        let e = obj;
        for (let k = 0; k < n_flat; k++) {
          e += 0x40;
          if (m.u8(e + 1) & 4) this._draw_object(e, m.u16(e + 2), s32(m.u32(e + 0x26)), null, radar);
        }
      }
    }
  }

  /** $7B72: d = Z - [$A94]; <= near -> near model, <= mid -> mid, else far. */
  _lod_model(typ: number, z: number): number {
    const m = this.mem;
    const b = LOD + s16(w16(typ * 16));
    const d = s32(z - m.u32(LOD_BIAS));
    const model = d <= s32(m.u32(b)) ? m.u16(b + 8) : d <= s32(m.u32(b + 4)) ? m.u16(b + 10) : m.u16(b + 12);
    if (model >= MODEL_PAIRS.length) throw new NotPorted(`type ${typ}: model ${model} beyond the model table`);
    return model;
  }

  _draw_object(obj: number, typ: number, z: number, origin: Vec3 | null, radar: RadarEntry[] | null): void {
    const model = this._lod_model(typ, z);
    this._model_pairs(model, obj);
    if (model === 2 || model === 3) this._attachments(model, obj, origin, radar);
  }

  /** The object pairs the model's basis routine leaves: $A472 (yaw - camera yaw, $A6C),
   * $A454 (pitch, $A68), $A468 (roll, $A70); the carrier's and turret's second boxes are
   * fake records patched with the turret angles $6CA/$6CC. */
  _model_pairs(model: number, obj: number): void {
    const m = this.mem;
    const kind = MODEL_PAIRS[model];
    if (kind === ".") return;
    let cam_yaw = m.u16(m.u32(CAMERA) + 6);
    let pitch = m.u16(obj + 4), yaw = m.u16(obj + 6), roll = m.u16(obj + 8);
    if (kind === "C") {
      pitch = m.u16(0x6ca);
      yaw = w16(yaw + m.u16(0x6cc));
      roll = 0;
    } else if (kind === "T") { // $ABE8 via $A946: no camera yaw
      pitch = m.u16(0x6ca);
      yaw = m.u16(0x6cc);
      roll = 0;
      cam_yaw = 0;
    } else if (kind === "W") {
      yaw = w16(yaw + m.u16(obj + 0x26));
    } else if (kind === "2") {
      roll = 0;
    }
    this._pair(OBJ_PAIRS + 4, w16(yaw - cam_yaw));
    this._pair(OBJ_PAIRS, pitch);
    this._pair(OBJ_PAIRS + 8, roll);
  }

  /** $6346 from the carrier model's BSP: slot 1 = [$6D6] (the lift), slot 2 = [$6DA]
   * (the dock), each at its own position (long camera transform from [$9FA]), radar-listed
   * while [$1230], drawn with the LOD model for its Z. Both hang off the BSP root (plane 0
   * of model 2, plane 3 of model 3), slot 1 first when that plane faces the camera. */
  _attachments(model: number, carrier: number, origin: Vec3 | null, radar: RadarEntry[] | null): void {
    const m = this.mem;
    const a = m.u32(0x6d6), b = m.u32(0x6da);
    if (!a && !b) return;
    let order = [1, 2];
    if (a && b && !this._carrier_plane(model, carrier, origin)) order = [2, 1];
    const cam = m.u32(CAMERA);
    for (const slot of order) {
      const o = m.u32(0x6d6 + 4 * (slot - 1));
      if (!o) continue;
      const [x, y, z] = cam_long(m, s32(m.u32(o + 0xa) - m.u32(cam + 0xa)), s32(m.u32(o + 0xe) - m.u32(cam + 0xe)),
        s32(m.u32(o + 0x12) - m.u32(cam + 0x12)));
      if (radar !== null && radar.length < 0x7f) radar.push([m.u16(o + 2), x, z]);
      this._draw_object(o, m.u16(o + 2), z, [x, y, z], radar);
    }
  }

  /** $98E6/$9902 for the BSP root plane of carrier model 2 (plane 0) or 3 (plane 3): the
   * model's camera-space vertices (basis $AABE/$AB42 then the vertex ops $5A02), the
   * plane's normal (V[b] - V[a]) x (V[c] - V[a]) (halved, word products) against -(origin
   * + V[a]), both scaled down to words: true (spl) when the dot product is >= 0. */
  _carrier_plane(model: number, obj: number, origin: Vec3 | null): boolean {
    if (origin === null) throw new NotPorted("carrier model drawn as a runway entry");
    const m = this.mem;
    const rec = CARRIER_MODELS[model];
    const V = new Map<number, Vec3>();
    let a0 = this._carrier_basis(model, obj, rec + 12, V); // handler, dist, basis, params
    a0 = this._vertex_ops(a0, V);
    a0 += 2; // nverts
    const n = m.u8(a0);
    const plane = model === 2 ? 0 : 3;
    if (plane >= n) throw new NotPorted(`model ${model} has no plane ${plane}`);
    const idx = [0, 1, 2].map((k) => m.u8(a0 + 1 + 3 * plane + k));
    for (const i of idx) if (!V.has(i)) throw new NotPorted(`model ${model} plane vertex ${i} not built`);
    const va = V.get(idx[0]) as Vec3, vb = V.get(idx[1]) as Vec3, vc = V.get(idx[2]) as Vec3;
    const w = [0, 1, 2].map((k) => s16(Math.floor((vb[k] - va[k]) / 2)))
      .concat([0, 1, 2].map((k) => s16(Math.floor((vc[k] - va[k]) / 2))));
    const nx = s32(w[1] * w[5] - w[2] * w[4]);
    const ny = s32(w[2] * w[3] - w[0] * w[5]);
    const nz = s32(w[0] * w[4] - w[1] * w[3]);
    const fit = (v: number[]): number[] => {
      while (v.some((c) => s16(c) !== c)) v = v.map((c) => Math.floor(c / 2));
      return v;
    };
    const p = fit([0, 1, 2].map((k) => s32(-s32(origin[k] + va[k]))));
    const nrm = fit([nx, ny, nz]);
    return s32(nrm[0] * p[0] + nrm[1] * p[1] + nrm[2] * p[2]) >= 0;
  }

  /** $A78C: the object pairs (pitch, yaw - camera yaw, roll), then the box of half
   * extents (sx, sy, sz) at a0 rotated into camera space: corners V[base + $F8..$FF]. */
  _box(a0: number, obj_pairs: Vec3, V: Map<number, Vec3>, base: number): number {
    const m = this.mem;
    const [pitch, yaw, roll] = obj_pairs;
    this._pair(OBJ_PAIRS + 4, yaw);
    this._pair(OBJ_PAIRS, pitch);
    this._pair(OBJ_PAIRS + 8, roll);
    const sx = m.s16(a0), sy = m.s16(a0 + 2), sz = m.s16(a0 + 4);
    /** (x, y) already rolled, sz: object pitch, object yaw, camera pitch, camera roll. */
    const chain = (d0: number, d1: number): Vec3 => {
      let a2 = d0;
      [d0, d1] = rot_a(m, d1, sz, OBJ_PAIRS);
      const a3 = d0;
      [d0, d1] = rot_b(m, a2, d1, OBJ_PAIRS + 4);
      a2 = d0;
      [d0, d1] = rot_a(m, a3, d1, CAM_PAIRS);
      const a3b = d1;
      [d0, d1] = rot_a(m, a2, d0, CAM_PAIRS + 8);
      return [d0, d1, a3b];
    };
    const neg = (v: Vec3): Vec3 => [s16(-v[0]), s16(-v[1]), s16(-v[2])];
    const [r0, r1] = rot_a(m, sx, sy, OBJ_PAIRS + 8);
    const fa = chain(s16(-r0), s16(-r1));
    V.set(base + 0xfa, fa);
    V.set(base + 0xfc, neg(fa));
    const [q0, q1] = rot_a(m, sx, s16(-sy), OBJ_PAIRS + 8);
    const f9 = chain(q0, q1);
    V.set(base + 0xf9, f9);
    V.set(base + 0xff, neg(f9));
    const fd = chain(r0, r1);
    V.set(base + 0xfd, fd);
    V.set(base + 0xfb, neg(fd));
    const d = [0, 1, 2].map((k) => s16(f9[k] - fa[k]));
    V.set(base + 0xf8, [0, 1, 2].map((k) => s16(-fd[k] + d[k])) as Vec3); // [$FB] + d (the regs hold -FD)
    V.set(base + 0xfe, [0, 1, 2].map((k) => s16(fd[k] - d[k])) as Vec3);
    return a0 + 6;
  }

  /** $AABE (model 2) / $AB42 (model 3): $A89A (the hull box and its Q15 list, edge
   * vectors $ADA/$AE0/$AE6), the lift platform vertices from $DD8 (and $DD6), then the
   * turret's two boxes (heading carrier yaw + [$6CC], the second also pitched by [$6CA])
   * at V[$F0..] and V[$E8..]. Returns the address after the basis parameters. */
  _carrier_basis(model: number, obj: number, a0: number, V: Map<number, Vec3>): number {
    const m = this.mem;
    const cam_yaw = m.u16(m.u32(CAMERA) + 6);
    const yaw = m.u16(obj + 6);
    a0 = this._box(a0, [m.u16(obj + 4), w16(yaw - cam_yaw), m.u16(obj + 8)], V, 0);
    const f8 = V.get(0xf8) as Vec3, f9 = V.get(0xf9) as Vec3, fb = V.get(0xfb) as Vec3, ff = V.get(0xff) as Vec3;
    const e1 = [0, 1, 2].map((k) => s16(f9[k] - f8[k])); // $AE6
    const e2 = [0, 1, 2].map((k) => s16(f8[k] - fb[k])); // $ADA
    const e3 = [0, 1, 2].map((k) => s16(ff[k] - fb[k])); // $AE0
    const n = m.u16(a0);
    a0 += 2;
    for (let j = 0; j < n; j++) {
      const f = [m.s16(a0), m.s16(a0 + 2), m.s16(a0 + 4)];
      const idx = m.u16(a0 + 6);
      a0 += 8;
      V.set(idx, [0, 1, 2].map((k) =>
        s16(Math.floor(u32(2 * e2[k] * f[0] + 2 * e3[k] * f[1] + 2 * e1[k] * f[2]) / 0x10000))) as Vec3);
    }
    const lift = (v: number): Vec3 =>
      [0, 1, 2].map((k) => s16(Math.floor(u32(2 * e3[k] * s16(v)) / 0x10000))) as Vec3;
    if (model === 2) {
      V.set(0x60, lift(m.u16(0xdd8)));
    } else {
      V.set(0x61, lift(m.u16(0xdd6)));
      V.set(0x5c, lift(m.u16(0xdd8)));
    }
    const ty = w16(yaw + m.u16(0x6cc));
    a0 = this._box(a0, [0, w16(ty - cam_yaw), 0], V, -8);
    return this._box(a0, [m.u16(0x6ca), w16(ty - cam_yaw), 0], V, -16);
  }

  /** $5A02: count, then (op, index) bytes: $00 load, $18 store, $30 midpoint (add, asr),
   * $48 add, $60 subtract, $78 negate, on words. */
  _vertex_ops(a0: number, V: Map<number, Vec3>): number {
    const m = this.mem;
    const n = m.u16(a0);
    a0 += 2;
    let cur: Vec3 = [0, 0, 0];
    for (let j = 0; j < n; j++) {
      const op = m.u8(a0), i = m.u8(a0 + 1);
      a0 += 2;
      if (op === 0x18) {
        V.set(i, cur);
        continue;
      }
      if (op === 0x78) {
        cur = [s16(-cur[0]), s16(-cur[1]), s16(-cur[2])];
        continue;
      }
      if (!(op === 0 || op === 0x30 || op === 0x48 || op === 0x60)) {
        throw new NotPorted(`vertex op $${op.toString(16).toUpperCase().padStart(2, "0")}`);
      }
      const v = V.get(i);
      if (v === undefined) {
        throw new NotPorted(`vertex op $${op.toString(16).toUpperCase().padStart(2, "0")} reads V[${i}], not built`);
      }
      if (op === 0) cur = v;
      else if (op === 0x30) cur = [0, 1, 2].map((k) => s16(cur[k] + v[k]) >> 1) as Vec3;
      else if (op === 0x48) cur = [0, 1, 2].map((k) => s16(cur[k] + v[k])) as Vec3;
      else cur = [0, 1, 2].map((k) => s16(cur[k] - v[k])) as Vec3;
    }
    return a0;
  }

  /** $146A4's state write: for each radar entry with a colour ($9098: type 8 has one) whose
   * X / Z divided by the record's +8 fit words (divs, bvs), an island (type 8) sets the yaw
   * pair $A6C from the camera's yaw ($A45E with a4 = [$9FA]). */
  _radar_overlay(record: number, radar: RadarEntry[] | null): void {
    const m = this.mem;
    if (radar === null) throw new NotPorted("radar overlay with the radar list off ([$1230] = 0: stale list)");
    const d = m.s16(record + 8);
    if (d === 0) throw new NotPorted("radar scale 0 (divs by zero)");
    const fits = (v: number): boolean => {
      const q = Math.floor(Math.abs(v) / Math.abs(d));
      const r = (v < 0) === (d < 0) ? q : -q;
      return -0x8000 <= r && r <= 0x7fff;
    };
    for (const [typ, x, z] of radar) {
      if (typ !== 8) continue;
      if (fits(x) && fits(z)) this._pair(OBJ_PAIRS + 4, m.u16(m.u32(CAMERA) + 6));
    }
  }

  /** $7BF6 + $62D4 for the current camera -> the radar list. */
  view_3d(): RadarEntry[] | null {
    const [tree, radar, n_flat] = this.scene_collect();
    this.scene_draw(tree, radar, n_flat);
    return radar;
  }

  /** The renders' eye offset: the carrier yaw pair in $A6C ($A438), then $A558 on
   * (ox, oz) -> carrier x, z + the turned offset. */
  _eye(ox: number, oz: number): [number, number] {
    const m = this.mem;
    this._pair(OBJ_PAIRS + 4, m.u16(CARRIER + 6));
    const [dx, dz] = rot_b(m, ox, oz, OBJ_PAIRS + 4);
    return [s32(m.u32(CARRIER + 0xa) + dx), s32(m.u32(CARRIER + 0x12) + dz)];
  }

  /** $1460A (screen 0) / $15002 (24): object 0 takes the carrier's angles and island
   * buffer, the eye 100 behind its origin; the 3D view. */
  _bridge_view(): RadarEntry[] | null {
    const m = this.mem;
    const o0 = OBJECTS;
    for (const k of [4, 6, 8]) m.set16(o0 + k, m.u16(CARRIER + k));
    m.set32(o0 + 0x3c, m.u32(CARRIER + 0x3c));
    const [x, z] = this._eye(0, -100);
    m.set32(o0 + 0xa, x);
    m.set32(o0 + 0x12, z);
    return this.view_3d();
  }

  /** $1460A: the bridge view, then the radar scope's record $4808 gets the zoom [$8B2]
   * and ($146A4, while the radar works [$B02]) its overlay. */
  _bridge_render(): void {
    const m = this.mem;
    const radar = this._bridge_view();
    m.set16(0x4808 + 8, m.u16(0x8b2));
    if (m.u8(0xb02)) this._radar_overlay(0x4808, radar);
  }

  /** The bay status lines under the deck, dock and arming views: [$95C] counts down;
   * true while it was still above 1 (the lines are recomputed). */
  _redraw_countdown(): boolean {
    const m = this.mem;
    if (!m.u16(REDRAW)) return false;
    m.set16(REDRAW, m.u16(REDRAW) - 1);
    return m.u16(REDRAW) !== 0;
  }

  /** $15002 (screen 24): the bridge view in the hangar viewport; while [$95E]: cleared,
   * the arming sub-panel $DC0 from the bay record [$48E] ($11738) and the panels redrawn
   * (the loadout panel's callback, _manta_panel), [$95C] = 2; the status countdown. */
  _manta_arming_render(): void {
    const m = this.mem;
    this._bridge_view();
    if (m.u16(REDRAW + 2)) {
      m.set16(REDRAW + 2, 0);
      m.set16(0xdc0, this._subpanel(m.u32(0x48e)));
      this._manta_panel();
      m.set16(REDRAW, 2);
    }
    this._redraw_countdown();
  }

  /** $14E56 (screen 16): object 0 takes the carrier's +$3C, pitch and roll, yaw = carrier
   * yaw + [$DC8], the eye 1000 behind the carrier origin; the 3D view; while [$960]: the
   * sub-panel $DC2 ($11E60, record [$568]) and panels redrawn (_walrus_panel), [$95C] = 2;
   * the status countdown. */
  _walrus_arming_render(): void {
    const m = this.mem;
    const o0 = OBJECTS;
    m.set32(o0 + 0x3c, m.u32(CARRIER + 0x3c));
    m.set16(o0 + 4, m.u16(CARRIER + 4));
    m.set16(o0 + 6, w16(m.u16(CARRIER + 6) + m.u16(0xdc8)));
    m.set16(o0 + 8, m.u16(CARRIER + 8));
    const [x, z] = this._eye(0, -1000);
    m.set32(o0 + 0xa, x);
    m.set32(o0 + 0x12, z);
    this.view_3d();
    if (m.u16(REDRAW + 4)) {
      m.set16(REDRAW + 4, 0);
      m.set16(0xdc2, this._subpanel(m.u32(0x568)));
      this._walrus_panel();
      m.set16(REDRAW, 2);
    }
    this._redraw_countdown();
  }

  /** $14816 / $14A40: object 0 at the carrier + the turned offset, yaw = carrier yaw +
   * the crane angle, the carrier's island buffer; the 3D view. */
  _crane_view(ox: number, oz: number, crane: number): void {
    const m = this.mem;
    const o0 = OBJECTS;
    const [x, z] = this._eye(ox, oz);
    m.set32(o0 + 0xa, x);
    m.set32(o0 + 0x12, z);
    m.set16(o0 + 6, w16(m.u16(crane) + m.u16(CARRIER + 6)));
    m.set32(o0 + 0x3c, m.u32(CARRIER + 0x3c));
    this.view_3d();
  }

  /** $14816 (screen 26): the deck camera turning with the lift crane $DDE. */
  _deck_render(): void {
    this._crane_view(0x1a4, 0x41a, 0xdde);
    this._redraw_countdown();
  }

  /** $14A40 (screen 19): the dock camera turning with the dock crane $DEA; while the
   * countdown runs, the Walrus status lines turn by the carrier's -yaw (pair $A6C). */
  _dock_render(): void {
    this._crane_view(-0x1a4, -0x41a, 0xdea);
    if (this._redraw_countdown()) this._pair(OBJ_PAIRS + 4, -this.mem.u16(CARRIER + 6));
  }

  /** $165B2 (screen 9): object 0 1000 astern, pitch [$1158], yaw [$115A], the carrier's
   * island buffer; the 3D view. */
  _flares_render(): void {
    const m = this.mem;
    const o0 = OBJECTS;
    m.set32(o0 + 0x3c, m.u32(CARRIER + 0x3c));
    const [x, z] = this._eye(0, -1000);
    m.set32(o0 + 0xa, x);
    m.set32(o0 + 0x12, z);
    m.set16(o0 + 6, m.u16(0x115a));
    m.set16(o0 + 4, m.u16(0x1158));
    this.view_3d();
  }

  /** $1638C (screen 10): the 3D view from the camera [$9FA] while the viewing drone
   * (object 15) is live. */
  _drone_view_render(): void {
    if (this.mem.u8(OBJECTS + 15 * OBJECT_SIZE + 1) & 1) this.view_3d();
  }

  /** $156FA (screen 1): with the priorities panel closed ([$B62] = 0), the damage diagram
   * (the model list) from the camera [$9FA]. */
  _damage_render(): void {
    if (!this.mem.u16(0xb62)) this.view_3d();
  }

  /** $162B0: the log is redrawn while $966 counts down. (Its expiry of old records
   * changes the log at $65D10, which the app keeps itself.) */
  _messages_render(): void {
    const m = this.mem;
    if (m.u16(0x966)) m.set16(0x966, m.u16(0x966) - 1);
  }

  /** $15EB0: drone k's element (type [$42A4E + $16 k] = 66 + k) is shown (flag bit 15
   * clear) while the drone is out (station +1 bit 0) and the radar works ([$B02] != 0),
   * else hidden. (The element positions it writes are UI.) */
  _radar_render(): void {
    const m = this.mem;
    for (let k = 0; k < 4; k++) {
      const a = FLAG_TABLE + 2 * (DRONE_TYPE + k);
      if (m.u8(0x65e + 14 * k + 1) & 1 && m.u8(0xb02)) m.set8(a, m.u8(a) & 0x7f);
      else m.set8(a, m.u8(a) | 0x80);
    }
  }

  /** $15A94 (screen 8). With the observation turret working ([$B12] != 0): the zoom view
   * record's shx/shy +8/+$A = [$6D2] + 8; object 0 on the carrier's island buffer with
   * pitch [$6CA], yaw carrier yaw + [$6CC], the carrier's roll; the pairs $A70 = -[$6CC],
   * $A68 = [$6CA], $A6C = carrier yaw place the eye 30 from the pivot (0, 320, -350)
   * along the gun's up; the 3D view. Always: the turret wireframe (model 84, basis $ABE8)
   * in its box, which leaves the pairs ([$6CA], [$6CC], 0). Broken: static noise ($5538:
   * one rand). */
  _turret_render(): void {
    const m = this.mem;
    if (m.u8(0xb12)) {
      m.set16(0x478e, w16(m.u16(0x6d2) + 8));
      m.set16(0x4790, w16(m.u16(0x6d2) + 8));
      const o0 = OBJECTS;
      m.set32(o0 + 0x3c, m.u32(CARRIER + 0x3c));
      m.set16(o0 + 4, m.u16(0x6ca));
      m.set16(o0 + 6, w16(m.u16(0x6cc) + m.u16(CARRIER + 6)));
      m.set16(o0 + 8, m.u16(CARRIER + 8));
      this._pair(OBJ_PAIRS + 8, -m.u16(0x6cc));
      this._pair(OBJ_PAIRS, m.u16(0x6ca));
      this._pair(OBJ_PAIRS + 4, m.u16(CARRIER + 6));
      const [u, v] = rot_a(m, 0x1e, 0, OBJ_PAIRS);
      m.set32(o0 + 0xe, u + 0x140);
      let [x, z] = rot_a(m, 0, v, OBJ_PAIRS + 8);
      [x, z] = rot_b(m, x, w16(z - 0x15e), OBJ_PAIRS + 4);
      m.set32(o0 + 0xa, m.u32(CARRIER + 0xa) + x);
      m.set32(o0 + 0x12, m.u32(CARRIER + 0x12) + z);
      this.view_3d();
    }
    this._model_pairs(84, OBJECTS);
    if (!m.u8(0xb12)) this.rand();
  }

  /** $15496 (screen 18), while [$70E] (controllable) and the Walrus object [$56C] is
   * live, unless its telemetry ([$568] +$2C) is lost (2): the Walrus's visible bit
   * cleared; object 0 (the camera $9FA) at the Walrus + 45 up with its pitch and roll and
   * yaw + turret +$26 and its island buffer; the 3D view (rear view [$8F1]: yaw + $8000
   * and pitch negated for it). Guiding a missile (record +0 bit 4): the Walrus visible
   * again, the camera $9FA = the missile, which is hidden for the inset's $7BF6 and shown
   * again; the inset viewport $4834 follows the guidance hold +$2E; $6FE = $4744. Radar
   * scope (+$32): the overlay with $481E. Telemetry != 0: static noise (one rand). */
  _walrus_view_render(): void {
    const m = this.mem;
    if (!m.u16(0x70e)) return;
    const rec = m.u32(0x568), obj = m.u32(0x56c);
    const tele = m.u16(rec + 0x2c);
    if (!(m.u8(obj + 1) & 1)) return;
    if (tele !== 2) {
      const o0 = OBJECTS;
      m.set8(obj + 1, m.u8(obj + 1) & 0xfd);
      m.set16(o0 + 4, m.u16(obj + 4));
      m.set16(o0 + 8, m.u16(obj + 8));
      m.set16(o0 + 6, w16(m.u16(obj + 6) + m.u16(obj + 0x26)));
      m.set32(o0 + 0xa, m.u32(obj + 0xa));
      m.set32(o0 + 0x12, m.u32(obj + 0x12));
      m.set32(o0 + 0xe, m.u32(obj + 0xe) + 0x2d);
      m.set32(o0 + 0x3c, m.u32(obj + 0x3c));
      m.set32(CAMERA, o0);
      const rear = m.u8(0x8f1);
      if (rear) {
        m.set16(o0 + 6, m.u16(o0 + 6) + 0x8000);
        m.set16(o0 + 4, -m.u16(o0 + 4));
      }
      let radar = this.view_3d();
      if (rear) {
        m.set16(o0 + 6, m.u16(o0 + 6) + 0x8000);
        m.set16(o0 + 4, -m.u16(o0 + 4));
      }
      if (m.u8(rec) & 0x10) {
        m.set8(obj + 1, m.u8(obj + 1) | 2);
        const missile = m.u32(obj + 0x20);
        if (!(OBJECTS <= missile && missile < OBJECTS + 64 * OBJECT_SIZE && (missile - OBJECTS) % OBJECT_SIZE === 0)) {
          throw new NotPorted(`Walrus guided missile pointer $${missile.toString(16).toUpperCase()}`);
        }
        m.set32(CAMERA, missile);
        m.set8(missile + 1, m.u8(missile + 1) & 0xfd);
        const [tree, radar2, n_flat] = this.scene_collect();
        radar = radar2;
        m.set8(missile + 1, m.u8(missile + 1) | 2);
        const y = m.u16(rec + 0x2e);
        const vp = 0x4834;
        m.set16(vp + 0xe, y);
        const y0 = w16(y - 0x20);
        m.set16(vp + 2, y0);
        let y1 = w16(y0 + 0x3f);
        if (s16(y1) > 0x85) y1 = 0x85;
        m.set16(vp + 6, y1);
        m.set16(vp + 0x12, w16(y1 - y0 + 1));
        m.set32(VIEWPORT, vp);
        this.scene_draw(tree, radar, n_flat);
        m.set32(VIEWPORT, 0x4744);
      }
      if (m.u8(rec + 0x32)) this._radar_overlay(0x481e, radar);
    }
    if (tele) this.rand();
  }

  /** $15244 (screen 27), while [$70C] (controllable), the record [$48E] isn't destroyed
   * (+1 >= 0) and the Manta object [$492] is live, unless its telemetry +$2E is lost (2):
   * the 3D view from the camera [$9FA] (rear view [$8F1]: the Manta's yaw + $8000, pitch
   * and roll negated for it); not in rear view, the sight $1162/$1164: weapon (+$1A) 1:
   * (rudder +2 asr 3, -pitch +4 asr 3), 0: (0, 0); radar scope (+$32): the overlay with
   * $481E. Telemetry != 0: static noise (one rand). */
  _manta_view_render(): void {
    const m = this.mem;
    if (!m.u16(0x70c)) return;
    const rec = m.u32(0x48e), obj = m.u32(0x492);
    if (m.u8(rec + 1) & 0x80 || !(m.u8(obj + 1) & 1)) return;
    const tele = m.u16(rec + 0x2e);
    if (tele !== 2) {
      const rear = m.u8(0x8f1);
      const flip = (): void => {
        m.set16(obj + 6, m.u16(obj + 6) + 0x8000);
        m.set16(obj + 4, -m.u16(obj + 4));
        m.set16(obj + 8, -m.u16(obj + 8));
      };
      if (rear) flip();
      const radar = this.view_3d();
      if (rear) {
        flip();
      } else {
        const w = m.u16(rec + 0x1a);
        if (w === 1) {
          m.set16(0x1162, s16(m.u16(rec + 2)) >> 3);
          m.set16(0x1164, s16(w16(-m.u16(rec + 4))) >> 3);
        } else if (w === 0) {
          m.set32(0x1162, 0);
        }
      }
      if (m.u8(rec + 0x32)) this._radar_overlay(0x481e, radar);
    }
    if (tele) this.rand();
  }

  /** $15DA2 (screen 33, title menu [$708] = 0): one rand; the carrier model's turret
   * ($6CC + 300, $6CA swinging between 0 and -14000 by 800, $6D4 the direction); Control
   * with A / B sets $124C to 0 / 1 (with M it ends the wait at once); in debug mode keypad
   * +/- (new flags $835/$82D, consumed) restart the wait at 1000; the wait $93C counts down
   * and at 0 grows $93A by 180 (to at most 3600), restarts and goes back to the title
   * ($1231 = $FF, $4272: tab 5 requested, $708 = 0; $458 = $FF, $881 = 0). The 3D view
   * of the model list (the carrier, turning by -500 a frame; keypad +/- in debug mode step
   * its type through 1-$49) runs after the rand. */
  _title_render(): void {
    const m = this.mem;
    if (m.u16(0x708)) return;
    this.rand();
    this.view_3d();
    m.set16(0x6cc, w16(m.u16(0x6cc) + 0x12c));
    let d0 = s16(w16((m.u8(0x6d4) ? -0x320 : 0x320) + m.u16(0x6ca)));
    const d1 = d0;
    d0 = d0 > 0 ? 0 : d0 < -0x36b0 ? -0x36b0 : d0;
    m.set16(0x6ca, d0);
    if (d1 !== d0) m.set8(0x6d4, ~m.u8(0x6d4) & 0xff);
    const title = MODEL_LIST + 0x12; // the model shown turns by -500
    m.set16(title + 6, m.u16(title + 6) - 0x1f4);
    let expire = false;
    if (m.u8(0x7d2)) {
      if (m.u8(0x7fc)) expire = true;
      else if (m.u8(0x7d4)) m.set16(TITLE_FLAG, 0);
      else if (m.u8(0x7f8)) m.set16(TITLE_FLAG, 1);
    }
    if (!expire) {
      if (m.u8(0x1189)) {
        let typ = m.u16(title + 2); // keypad +/-: the next / previous type
        if (m.u8(0x835) & 1) {
          m.set8(0x835, m.u8(0x835) & 0xfe);
          m.set16(TITLE_TIMER + 2, 1000);
          typ = w16(typ + 1);
          m.set16(title + 2, typ === 0x4a ? 1 : typ);
        } else if (m.u8(0x82d) & 1) {
          m.set8(0x82d, m.u8(0x82d) & 0xfe);
          m.set16(TITLE_TIMER + 2, 1000);
          typ = w16(typ - 1);
          m.set16(title + 2, typ || 0x49);
        }
      }
      const t = w16(m.u16(TITLE_TIMER + 2) - 1);
      m.set16(TITLE_TIMER + 2, t);
      if (t) return;
    }
    let d = w16(m.u16(TITLE_TIMER) + 0xb4);
    if (s16(d) > 0xe10) d = 0xe10;
    m.set16(TITLE_TIMER, d);
    m.set16(TITLE_TIMER + 2, d);
    m.set8(0x1231, 0xff);
    m.set8(0x1227, 0xff);
    m.set16(0x1228, 5);
    m.set16(0x708, 0);
    m.set8(0x458, 0xff);
    m.set8(0x881, 0);
  }
}
