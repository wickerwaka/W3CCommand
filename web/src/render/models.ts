// 3D model decoder for CARRIER.TOS (model_table at $4A06A), working directly on a 512 KB game
// RAM image (the payload sits at $400, so all addresses are absolute).
//
// A TypeScript port of tools/stmodel.py (its docstring is the record format spec) that also
// ports the runtime-dependent basis routines stmodel only evaluates under Unicorn:
//   $A660 island plateau (object +$1E/+$20), $AA78/$AABE/$AB42 carrier with turret boxes
//   (globals $6CC heading, $6CA elevation) and lifts ($DD6/$DD8), $AC16/$ADA4 animated
//   (object +$24 blends vertex pairs, $AC16 also turns its second box by object +$26),
//   $ABE8 turret ($6CC/$6CA), $A8DA (the $A89A vertex list reusing the edge vectors that the
//   previous $A89A left in $ADA/$AE0/$AE6) and $935C (no basis: the vertex ops work on the
//   vertex array the previous model left). Models 75-82 are the damage-screen overlays drawn
//   after model 74, so they are built on top of model 74's vertex array.
//
// The routines are ported instruction by instruction with the game's integer arithmetic
// (16-bit wrap, muls.w, add.l doubling, swap = high word, asr rounding towards -inf). The
// game's basis routines also apply the object's and camera's rotation (2.14 sin/cos pairs from
// the sine table at $1F5A); here both are zero, as in tools/stemu.py, which gives model space
// (game frame: x right, y up, z forward). Rotations that are part of the model itself are kept:
// the turret boxes turn by $6CC/$6CA (through the fake objects at $AAB4/$AF80/$AD9A) and the
// $AC16 second box by object +$26. Object +4/+6/+8 (the object's own angles) are taken as 0.
// Note that $AA24 (trees) is a camera-facing billboard in the game (camera pitch/roll only):
// in model space it is a quad in the z = 0 plane.
//
// tools/export_model_vectors.py + web/scripts/check-models.ts verify this module against the
// game's code run under Unicorn (vertices) and against stmodel.py (primitives).

export const MODEL_TABLE = 0x4a06a;
export const MODEL_COUNT = 99;
export const MODEL_DRAW_POLY = 0x935e;
export const MODEL_DRAW_POLY_NEAR = 0x9550;
export const MODEL_DRAW_WIRE = 0x9496;
export const MODEL_DRAW_LINES = 0x99f2;
export const MODEL_DRAW_LINES_NEAR = 0x9a1c;
export const MODEL_DRAW_DISC = 0xaf16;
export const MODEL_EMPTY = 0x935c;
export const MODEL_EMPTY_2 = 0x99f0;

export const BASIS_PLATEAU = 0xa660;
export const BASIS_BOX = 0xa78c;
export const BASIS_BOX_LIST = 0xa89a;
export const BASIS_LIST = 0xa8da; // $A89A entered past its box: reuses the edge vectors
export const BASIS_TURRET_BOX = 0xa946; // box without camera (only via $ABE8)
export const BASIS_QUAD = 0xaa24;
export const BASIS_CARRIER = 0xaa78;
export const BASIS_CARRIER_LIFT = 0xaabe;
export const BASIS_CARRIER_LIFTS = 0xab42;
export const BASIS_TURRET = 0xabe8;
export const BASIS_ANIM_TWO_BOX = 0xac16;
export const BASIS_ANIM_BOX = 0xada4;
export const BASIS_TWO_BOX = 0xaf4e;
export const BASIS_NONE = 0x935c; // rts

const SINE_TABLE = 0x1f5a;
const DISC_RECORD = 0xaf48; // inline disc record of $AF16 (radius patched from object +$1E)
// Globals read by the basis routines.
const G_TURRET_HEADING = 0x6cc;
const G_TURRET_ELEVATION = 0x6ca;
const G_LIFT_A = 0xdd6;
const G_LIFT_B = 0xdd8;
const G_EDGES = 0xada; // $ADA/$AE0/$AE6: edge vectors left by $A89A
/** Models drawn over another model's vertex array (damage-screen overlays after model 74). */
export const PARENT_MODEL: Readonly<Record<number, number>> = {
  75: 74, 76: 74, 77: 74, 78: 74, 79: 74, 80: 74, 81: 74, 82: 74,
};

export interface ModelContext {
  /** The object's $40-byte record, for +$1E/+$20/+$24/+$26/+$38. */
  obj: Uint8Array;
  /** For globals like $6CC/$6CA/$DD6/$DD8 (absolute addresses). */
  ram: Uint8Array;
}

export type Vec3 = [number, number, number];

export interface Poly {
  /** Vertex indices, in the game's winding. */
  idx: number[];
  /** Colour byte: index into the $13CA table of palette-index pairs. */
  colour: number;
  /** Index into `planes` of a plane all its vertices lie on (within 1 unit), else -1. */
  plane: number;
  /** Position in the BSP draw order: the traversal that draws a node's inline child before
   * its other child (what the game draws when every split plane faces the camera), groups and
   * polygons in record order. Equal to the index in `polys`. */
  order: number;
  /** 0 for base polygons; otherwise 1 + the highest layer among earlier polygons (in `order`)
   * that are coplanar with it (same orientation) and overlap it in their plane. */
  layer: number;
  /** The group's visibility test (BSP group flag bit 0, $9902): vertices a, b, c; the group is
   * drawn only when ((b - a) x (c - a)) . (eye - a) >= 0 in model space (game frame). */
  test?: Vec3;
}

export interface Line {
  v0: number;
  v1: number;
  colour: number;
  /** $9496 only: drawn only when one of the faces listing it faces the camera. */
  conditional?: boolean;
  /** The group's visibility test (BSP group flag bit 0, $9902): vertices a, b, c; the group is
   * drawn only when ((b - a) x (c - a)) . (eye - a) >= 0 in model space (game frame). */
  test?: Vec3;
}

export interface Disc {
  v: number;
  radius: number;
  colour: number;
  /** The group's visibility test (BSP group flag bit 0, $9902): vertices a, b, c; the group is
   * drawn only when ((b - a) x (c - a)) . (eye - a) >= 0 in model space (game frame). */
  test?: Vec3;
}

export interface Model {
  index: number;
  handler: number;
  vertices: Vec3[];
  polys: Poly[];
  lines: Line[];
  discs: Disc[];
  attachments: number[];
  nearDist: number | null;
  dynamic: boolean;
  // Additions to the requested shape:
  /** The basis routine (second long of the body), null for empty/disc models. */
  basis: number | null;
  /** Back-face/BSP split planes: vertex index triples (a, b, c). */
  planes: Vec3[];
  /** $9496 only: faces (vertex triple a, b, c, and indices into `lines` of the conditional
   * lines this face reveals when it faces the camera). */
  faces?: { a: number; b: number; c: number; lines: number[] }[];
  /** Indices `i < vertices.length` whose V entry was never written (filled with [0, 0, 0]). */
  undefinedVertices: number[];
}

// ---------------------------------------------------------------------------------------
// 68000 arithmetic

function s16(v: number): number {
  const u = ((v % 0x10000) + 0x10000) % 0x10000;
  return u & 0x8000 ? u - 0x10000 : u;
}

function u32(v: number): number {
  return ((v % 0x100000000) + 0x100000000) % 0x100000000;
}

/** swap after a 32-bit sum: the (signed) high word. */
function hiWord(v: number): number {
  return s16(Math.floor(u32(v) / 0x10000));
}

function rd8(ram: Uint8Array, a: number): number {
  return ram[a];
}
function rd16(ram: Uint8Array, a: number): number {
  return (ram[a] << 8) | ram[a + 1];
}
function rds16(ram: Uint8Array, a: number): number {
  return s16(rd16(ram, a));
}
function rd32(ram: Uint8Array, a: number): number {
  return ((ram[a] << 24) | (ram[a + 1] << 16) | (ram[a + 2] << 8) | ram[a + 3]) >>> 0;
}

/** A (sin, cos) pair as $A438 builds it from an angle word. */
interface Pair {
  s: number;
  c: number;
}

function anglePair(ram: Uint8Array, angle: number): Pair {
  const a = angle & 0xfff0; // andi.b #$f0
  return {
    s: rds16(ram, SINE_TABLE + (a >>> 3)),
    c: rds16(ram, SINE_TABLE + (((a + 0x4000) & 0xffff) >>> 3)),
  };
}

/** $A4FA/$A5C2 form: x' = x cos - y sin, y' = y cos + x sin (muls, add.l x2, swap). */
function rotA(x: number, y: number, p: Pair): [number, number] {
  return [hiWord(4 * u32(x * p.c - y * p.s)), hiWord(4 * u32(y * p.c + x * p.s))];
}

/** $A55E form: x' = x cos + y sin, y' = y cos - x sin. */
function rotB(x: number, y: number, p: Pair): [number, number] {
  return [hiWord(4 * u32(x * p.c + y * p.s)), hiWord(4 * u32(y * p.c - x * p.s))];
}

function neg3(v: Vec3): Vec3 {
  return [s16(-v[0]), s16(-v[1]), s16(-v[2])];
}

function sub3(a: Vec3, b: Vec3): Vec3 {
  return [s16(a[0] - b[0]), s16(a[1] - b[1]), s16(a[2] - b[2])];
}

function add3(a: Vec3, b: Vec3): Vec3 {
  return [s16(a[0] + b[0]), s16(a[1] + b[1]), s16(a[2] + b[2])];
}

/** muls.w by a word, add.l (x2), swap: (e * k * 2) >> 16 per component. */
function scaleEdge(e: Vec3, k: number): Vec3 {
  return [hiWord(2 * e[0] * k), hiWord(2 * e[1] * k), hiWord(2 * e[2] * k)];
}

/** Rotation pairs a box routine uses ($A68 pitch, $A6C heading, $A70 roll, camera $A74/$A78/$A7C). */
interface Rotation {
  a68: Pair;
  a6c: Pair;
  a70: Pair;
  a74: Pair;
  a78: Pair;
  a7c: Pair;
}

// ---------------------------------------------------------------------------------------
// Record reader and vertex builder

class Reader {
  ram: Uint8Array;
  a: number;
  constructor(ram: Uint8Array, a: number) {
    this.ram = ram;
    this.a = a;
  }
  u8(): number {
    return this.ram[this.a++];
  }
  u16(): number {
    const v = rd16(this.ram, this.a);
    this.a += 2;
    return v;
  }
  s16(): number {
    return s16(this.u16());
  }
}

/** Object angles of a (possibly fake) object: +4 pitch, +6 heading, +8 roll. */
interface Angles {
  p: number;
  h: number;
  r: number;
}

const ZERO_ANGLES: Angles = { p: 0, h: 0, r: 0 };

export class VertexBuilder {
  /** The vertex array V (256 entries of 3 words at A1 = *$8E2); null = never written. */
  V: (Vec3 | null)[] = new Array(256).fill(null);
  /** Edge vectors $ADA/$AE0/$AE6 (globals; $A89A sets them, $A8DA/$AABE/$AB42 read them). */
  ada: Vec3;
  ae0: Vec3;
  ae6: Vec3;
  /** d0-d2 between vertex ops (null until a basis routine or a load sets it). */
  cur: Vec3 | null = null;
  private ram: Uint8Array;
  private obj: Uint8Array;
  private g: Uint8Array;

  constructor(ram: Uint8Array, ctx?: ModelContext) {
    this.ram = ram;
    this.obj = ctx?.obj ?? new Uint8Array(0x40);
    this.g = ctx?.ram ?? ram;
    const e = (k: number): Vec3 => [rds16(this.g, G_EDGES + 6 * k), rds16(this.g, G_EDGES + 6 * k + 2), rds16(this.g, G_EDGES + 6 * k + 4)];
    this.ada = e(0);
    this.ae0 = e(1);
    this.ae6 = e(2);
  }

  private objWord(off: number): number {
    return rds16(this.obj, off);
  }

  private global(addr: number): number {
    return rds16(this.g, addr);
  }

  private get(i: number): Vec3 {
    const v = this.V[i];
    if (!v) throw new Error(`reads vertex V[$${i.toString(16)}] that was never written`);
    return v;
  }

  private rotation(o: Angles): Rotation {
    // Camera at zero ($A484/$A494/$A4A4 negate the camera's angles); $A472 subtracts the
    // camera heading from the object's.
    const zero = anglePair(this.ram, 0);
    return {
      a68: anglePair(this.ram, o.p),
      a6c: anglePair(this.ram, o.h),
      a70: anglePair(this.ram, o.r),
      a74: zero,
      a78: zero,
      a7c: zero,
    };
  }

  /** Object then (optionally) camera rotation of a corner whose (x, y) is already rolled. */
  private objXform(x: number, y: number, z: number, R: Rotation, camera: boolean): Vec3 {
    const [y2, z2] = rotA(y, z, R.a68); // $A4F4
    const [x3, z3] = rotB(x, z2, R.a6c); // $A558
    if (!camera) return [x3, y2, z3];
    const [y4, z4] = rotA(y2, z3, R.a74); // $A4EA
    const [x5, y5] = rotA(x3, y4, R.a7c); // $A5B2
    return [x5, y5, z4];
  }

  /** $A78C (camera = true) / $A946 (camera = false): box corners at V[base..base+7]. */
  private box(r: Reader, base: number, o: Angles, camera: boolean): Vec3 {
    const R = this.rotation(o);
    const sx = r.s16(), sy = r.s16(), sz = r.s16();
    const rolled = rotA(sx, sy, R.a70); // $A5BC
    // Partial write of V[base+5] (x, y) that the routine later overwrites.
    const a = this.objXform(s16(-rolled[0]), s16(-rolled[1]), sz, R, camera);
    this.V[base + 2] = a;
    this.V[base + 4] = neg3(a);
    const rolled2 = rotA(sx, s16(-sy), R.a70);
    const b = this.objXform(rolled2[0], rolled2[1], sz, R, camera);
    this.V[base + 1] = b;
    this.V[base + 7] = neg3(b);
    const c = this.objXform(rolled[0], rolled[1], sz, R, camera);
    this.V[base + 5] = c;
    const nc = neg3(c);
    this.V[base + 3] = nc;
    const d = sub3(b, a);
    this.V[base + 0] = add3(nc, d);
    const last = sub3(c, d);
    this.V[base + 6] = last;
    return last; // d0-d2 on return
  }

  /** $A660: island plateau from object +$1E (x half-size) and +$20 (z half-size). */
  private plateau(): void {
    const R = this.rotation(ZERO_ANGLES);
    const cam = (x: number, y: number, z: number): Vec3 => {
      const [x1, z1] = rotB(x, z, R.a78); // $A54E
      const [y1, z2] = rotA(y, z1, R.a74); // $A4EA
      const [x2, y2] = rotA(x1, y1, R.a7c); // $A5B2
      return [x2, y2, z2];
    };
    let hx = this.objWord(0x1e);
    let hz = this.objWord(0x20);
    for (const [base, y] of [[0, 0], [4, 0x50]]) {
      const v0 = cam(hx, y, s16(-hz));
      const v1 = cam(hx, y, hz);
      const v2 = cam(s16(-hx), y, hz);
      this.V[base] = v0;
      this.V[base + 1] = v1;
      this.V[base + 2] = v2;
      // Long arithmetic, stored as words.
      this.V[base + 3] = [s16(v0[0] - v1[0] + v2[0]), s16(v0[1] - v1[1] + v2[1]), s16(v0[2] - v1[2] + v2[2])];
      hx = s16(hx - 0x50);
      hz = s16(hz - 0x50);
    }
    this.cur = null; // d0-d2 are left as longs here (no vertex-op stream relies on them)
  }

  /** $A89A box + edge vectors, then the $A8DA vertex list. */
  private boxList(r: Reader): void {
    this.cur = this.box(r, 0xf8, ZERO_ANGLES, true);
    const f8 = this.get(0xf8), f9 = this.get(0xf9), fb = this.get(0xfb), ff = this.get(0xff);
    this.ae6 = sub3(f9, f8);
    this.ada = sub3(f8, fb);
    this.ae0 = sub3(ff, fb);
    this.list(r);
  }

  /** $A8DA: n.w * (fx.w fy.w fz.w idx.w), V[idx] = (fx*ADA + fy*AE0 + fz*AE6) * 2 >> 16. */
  private list(r: Reader): void {
    const n = r.u16();
    for (let k = 0; k < n; k++) {
      const fx = r.s16(), fy = r.s16(), fz = r.s16();
      const v: Vec3 = [0, 0, 0];
      for (let c = 0; c < 3; c++) {
        let acc = u32(2 * fx * this.ada[c]);
        acc = u32(acc + 2 * fy * this.ae0[c]);
        acc = u32(acc + 2 * fz * this.ae6[c]);
        v[c] = hiWord(acc);
      }
      const off = (r.u16() * 6) & 0xffff; // add.w: idx*6 as a word
      if (off % 6 !== 0 || off / 6 > 255) throw new Error(`vertex list index out of range`);
      this.V[off / 6] = v;
    }
    this.cur = null;
  }

  /** $AA24: flat quad in the z = 0 plane (camera pitch/roll only: a billboard in the game). */
  private quad(r: Reader): void {
    const R = this.rotation(ZERO_ANGLES);
    const sx = r.s16(), sy = r.s16();
    const corner = (y: number): Vec3 => {
      const [y1, z1] = rotA(y, 0, R.a74);
      const [x2, y2] = rotA(sx, y1, R.a7c);
      return [x2, y2, z1];
    };
    const a = corner(sy);
    this.V[0xf9] = a;
    this.V[0xfb] = neg3(a);
    const b = corner(s16(-sy));
    this.V[0xf8] = b;
    const nb = neg3(b);
    this.V[0xfa] = nb;
    this.cur = nb;
  }

  /** The turret part of $AA78/$AABE/$AB42: two boxes turned by $6CC (and $6CA). */
  private carrierTurret(r: Reader): void {
    const heading = s16(0 + this.global(G_TURRET_HEADING)); // object +6 (0) + $6CC
    this.box(r, 0xf0, { p: 0, h: heading, r: 0 }, true);
    this.cur = this.box(r, 0xe8, { p: this.global(G_TURRET_ELEVATION), h: heading, r: 0 }, true);
  }

  /** $AC16 (two = true) / $ADA4: box(es), the first vertex-op stream, then the +$24 blend. */
  private animated(r: Reader, two: boolean): void {
    const blend = this.objWord(0x24); // copied to $118C
    if (two) {
      this.box(r, 0xf0, ZERO_ANGLES, true);
      // Fake object at $AD9A: the object's angles, heading + object +$26.
      this.box(r, 0xf8, { p: 0, h: s16(0 + this.objWord(0x26)), r: 0 }, true);
    } else {
      this.box(r, 0xf8, ZERO_ANGLES, true);
    }
    this.cur = null;
    this.vertexOps(r);
    const V = (i: number) => this.get(i);
    const lerp = (a: Vec3, b: Vec3, f: number): Vec3 => {
      // sub.w, muls.w, asr.l #8: the low word is used
      const d = sub3(a, b);
      return [s16(Math.floor((d[0] * f) / 256)), s16(Math.floor((d[1] * f) / 256)), s16(Math.floor((d[2] * f) / 256))];
    };
    this.cur = null;
    if (blend === 0x100) {
      for (let i = 0; i < 4; i++) this.V[i] = V(4 + i);
      return;
    }
    if (blend > 0x100) {
      const f = blend & 0xff;
      if (f === 0) return;
      if (two) {
        const d = lerp(V(0), V(4), f);
        this.V[0] = add3(V(4), d);
        this.V[3] = add3(V(7), d);
        const e = lerp(V(1), V(5), f);
        this.V[1] = add3(V(5), e);
        this.V[2] = add3(V(6), e);
      } else {
        for (let i = 0; i < 4; i++) this.V[i] = add3(V(4 + i), lerp(V(i), V(4 + i), f));
      }
      return;
    }
    if (two) {
      const d = lerp(V(4), V(8), blend);
      this.V[4] = this.V[0] = add3(V(8), d);
      this.V[7] = this.V[3] = add3(V(11), d);
      const e = lerp(V(5), V(9), blend);
      this.V[5] = this.V[1] = add3(V(9), e);
      this.V[6] = this.V[2] = add3(V(10), e);
    } else {
      const d = lerp(V(4), V(8), blend);
      for (let i = 0; i < 4; i++) this.V[4 + i] = this.V[i] = add3(V(8 + i), d);
    }
  }

  /** Runs the basis routine at `basis` with its parameters at r. */
  basis(basis: number, r: Reader): void {
    switch (basis) {
      case BASIS_NONE:
        return;
      case BASIS_PLATEAU:
        return this.plateau();
      case BASIS_BOX:
        this.cur = this.box(r, 0xf8, ZERO_ANGLES, true);
        return;
      case BASIS_BOX_LIST:
        return this.boxList(r);
      case BASIS_LIST:
        return this.list(r);
      case BASIS_QUAD:
        return this.quad(r);
      case BASIS_TWO_BOX:
        // Fake object at $AF80: heading = object +6, then pitch = object +4 (both 0 here).
        this.box(r, 0xf8, ZERO_ANGLES, true);
        this.cur = this.box(r, 0xf0, ZERO_ANGLES, true);
        return;
      case BASIS_CARRIER:
        this.boxList(r);
        return this.carrierTurret(r);
      case BASIS_CARRIER_LIFT:
        this.boxList(r);
        this.V[0x60] = scaleEdge(this.ae0, this.global(G_LIFT_B));
        // (also sets BSP plane flag $41 from the object at $6DA and $DE6; not geometry)
        return this.carrierTurret(r);
      case BASIS_CARRIER_LIFTS:
        this.boxList(r);
        this.V[0x61] = scaleEdge(this.ae0, this.global(G_LIFT_A));
        this.V[0x5c] = scaleEdge(this.ae0, this.global(G_LIFT_B));
        return this.carrierTurret(r);
      case BASIS_TURRET: {
        const heading = this.global(G_TURRET_HEADING);
        this.box(r, 0xf8, { p: 0, h: heading, r: 0 }, false);
        this.cur = this.box(r, 0xf0, { p: this.global(G_TURRET_ELEVATION), h: heading, r: 0 }, false);
        return;
      }
      case BASIS_ANIM_TWO_BOX:
        return this.animated(r, true);
      case BASIS_ANIM_BOX:
        return this.animated(r, false);
      default:
        throw new Error(`unknown basis routine $${basis.toString(16)}`);
    }
  }

  /** $5A02: count.w then (op.b, idx.b) pairs; cur is d0-d2. */
  vertexOps(r: Reader): void {
    const n = r.u16();
    const need = (): Vec3 => {
      if (!this.cur) throw new Error(`vertex op at $${(r.a - 2).toString(16)} uses d0-d2 left by the basis routine`);
      return this.cur;
    };
    for (let k = 0; k < n; k++) {
      const op = r.u8(), idx = r.u8();
      switch (op) {
        case 0x00:
          this.cur = this.get(idx);
          break;
        case 0x18:
          this.V[idx] = need();
          break;
        case 0x30: {
          const c = need(), v = this.get(idx);
          this.cur = [s16(c[0] + v[0]) >> 1, s16(c[1] + v[1]) >> 1, s16(c[2] + v[2]) >> 1];
          break;
        }
        case 0x48:
          this.cur = add3(need(), this.get(idx));
          break;
        case 0x60:
          this.cur = sub3(need(), this.get(idx));
          break;
        case 0x78:
          this.cur = neg3(need());
          break;
        default:
          throw new Error(`unknown vertex op $${op.toString(16)} at $${(r.a - 2).toString(16)}`);
      }
    }
  }
}

// ---------------------------------------------------------------------------------------
// Records

export function modelAddress(ram: Uint8Array, index: number): number {
  return rd32(ram, MODEL_TABLE + 4 * index);
}

/** Start of the next record (records are contiguous): where a lines model's lines end. */
function recordEnd(ram: Uint8Array, addr: number): number {
  let end = MODEL_TABLE;
  for (let i = 0; i < MODEL_COUNT; i++) {
    const a = modelAddress(ram, i);
    if (a > addr && a < end) end = a;
  }
  return end;
}

function bodyStart(ram: Uint8Array, addr: number): number {
  const h = rd32(ram, addr);
  return addr + (h === MODEL_DRAW_POLY_NEAR || h === MODEL_DRAW_LINES_NEAR ? 8 : 4);
}

const VERTEX_HANDLERS = new Set([MODEL_DRAW_POLY, MODEL_DRAW_POLY_NEAR, MODEL_DRAW_WIRE, MODEL_DRAW_LINES, MODEL_DRAW_LINES_NEAR]);

export interface BuiltVertices {
  /** The whole vertex array after the basis routine and the vertex ops. */
  V: (Vec3 | null)[];
  /** Address of the nverts word (after the vertex-op stream). */
  end: number;
  basis: number;
}

/** Runs a model's basis routine and vertex ops (after its parent's, for 75-82). */
export function buildVertexArray(ram: Uint8Array, index: number, ctx?: ModelContext): BuiltVertices | null {
  const addr = modelAddress(ram, index);
  if (!VERTEX_HANDLERS.has(rd32(ram, addr))) return null;
  const b = new VertexBuilder(ram, ctx);
  const run = (i: number): BuiltVertices => {
    const body = bodyStart(ram, modelAddress(ram, i));
    const basis = rd32(ram, body);
    const r = new Reader(ram, body + 4);
    b.basis(basis, r);
    b.vertexOps(r);
    return { V: b.V, end: r.a, basis };
  };
  const parent = PARENT_MODEL[index];
  if (parent !== undefined) run(parent);
  return run(index);
}

/** Which object fields / globals a model's geometry reads (for cache keys). Models 75-82
 * also depend on being drawn after model 74, whose geometry is static. */
export function modelDependsOn(ram: Uint8Array, index: number): string[] {
  const addr = modelAddress(ram, index);
  const h = rd32(ram, addr);
  if (h === MODEL_DRAW_DISC) return ["obj+$1E"];
  if (!VERTEX_HANDLERS.has(h)) return [];
  const deps: string[] = [];
  switch (rd32(ram, bodyStart(ram, addr))) {
    case BASIS_PLATEAU:
      deps.push("obj+$1E", "obj+$20");
      break;
    case BASIS_CARRIER:
      deps.push("ram+$6CC", "ram+$6CA");
      break;
    case BASIS_CARRIER_LIFT:
      deps.push("ram+$6CC", "ram+$6CA", "ram+$DD8");
      break;
    case BASIS_CARRIER_LIFTS:
      deps.push("ram+$6CC", "ram+$6CA", "ram+$DD6", "ram+$DD8");
      break;
    case BASIS_TURRET:
      deps.push("ram+$6CC", "ram+$6CA");
      break;
    case BASIS_ANIM_TWO_BOX:
      deps.push("obj+$24", "obj+$26");
      break;
    case BASIS_ANIM_BOX:
      deps.push("obj+$24");
      break;
  }
  if (h === MODEL_DRAW_LINES || h === MODEL_DRAW_LINES_NEAR) deps.push("obj+$38");
  return deps;
}

/** Decodes a model. Without ctx the object record is zero and globals come from `ram`,
 * except that lines models ($99F2/$9A1C) then return all their lines (with ctx, only the
 * first `obj +$39` (low byte of +$38), as the game draws). */
export function decodeModel(ram: Uint8Array, index: number, ctx?: ModelContext): Model | null {
  if (index < 0 || index >= MODEL_COUNT) return null;
  const addr = modelAddress(ram, index);
  const handler = rd32(ram, addr);
  const dynamic = modelDependsOn(ram, index).length > 0;
  const m: Model = {
    index, handler, vertices: [], polys: [], lines: [], discs: [], attachments: [],
    nearDist: null, dynamic, basis: null, planes: [], undefinedVertices: [],
  };
  if (handler === MODEL_EMPTY || handler === MODEL_EMPTY_2) return m;
  if (handler === MODEL_DRAW_DISC) {
    const obj = ctx?.obj ?? new Uint8Array(0x40);
    m.vertices.push([0, 0, 0]); // $AF16 projects (0, 0, 0)
    const n = rd8(ram, DISC_RECORD);
    for (let k = 0; k < n; k++)
      m.discs.push({ v: rd8(ram, DISC_RECORD + 1), radius: rd16(obj, 0x1e), colour: rd8(ram, DISC_RECORD + 4) });
    return m;
  }
  if (!VERTEX_HANDLERS.has(handler)) return null;
  if (handler === MODEL_DRAW_POLY_NEAR || handler === MODEL_DRAW_LINES_NEAR) m.nearDist = rd32(ram, addr + 4);
  const built = buildVertexArray(ram, index, ctx)!;
  m.basis = built.basis;
  const r = new Reader(ram, built.end);
  const nverts = r.u16();
  parsePrimitives(ram, r, m, addr, ctx);
  // Models drawing over a parent's vertices reference V entries beyond nverts.
  let count = nverts;
  for (const p of m.polys) for (const i of p.idx) count = Math.max(count, i + 1);
  for (const l of m.lines) count = Math.max(count, l.v0 + 1, l.v1 + 1);
  for (const d of m.discs) count = Math.max(count, d.v + 1);
  for (let i = 0; i < count; i++) {
    const v = built.V[i];
    if (!v) m.undefinedVertices.push(i);
    m.vertices.push(v ? [v[0], v[1], v[2]] : [0, 0, 0]);
  }
  assignPlanes(m);
  assignLayers(m);
  return m;
}

function parsePrimitives(ram: Uint8Array, r: Reader, m: Model, addr: number, ctx?: ModelContext): void {
  const h = m.handler;
  if (h === MODEL_DRAW_LINES || h === MODEL_DRAW_LINES_NEAR) {
    r.u8(); // placeholder count (the handler overwrites it with the low byte of object +$38)
    const end = recordEnd(ram, addr);
    while (r.a + 3 <= end) {
      const c = r.u8(), v0 = r.u8(), v1 = r.u8();
      m.lines.push({ v0, v1, colour: c & 0x7f });
    }
    if (ctx) m.lines.length = Math.min(m.lines.length, ctx.obj[0x39]);
    return;
  }
  if (h === MODEL_DRAW_WIRE) {
    for (let n = r.u8(), k = 0; k < n; k++) {
      const c = r.u8(), v0 = r.u8(), v1 = r.u8();
      m.lines.push({ v0, v1, colour: c & 0x7f });
    }
    const base = m.lines.length;
    for (let n = r.u8(), k = 0; k < n; k++) {
      const c = r.u8(), v0 = r.u8(), v1 = r.u8();
      m.lines.push({ v0, v1, colour: c & 0x7f, conditional: true });
    }
    m.faces = [];
    for (let n = r.u8(), k = 0; k < n; k++) {
      const a = r.u8(), b = r.u8(), c = r.u8();
      const lines: number[] = [];
      for (let j = 0, nl = r.u8(); j < nl; j++) lines.push(base + r.u8());
      m.faces.push({ a, b, c, lines });
    }
    return;
  }
  for (let n = r.u8(), k = 0; k < n; k++) m.planes.push([r.u8(), r.u8(), r.u8()]);
  bsp(r, r.a, m);
}

/** Walks both children of every node (inline child first); returns the end of the subtree. */
function bsp(r: Reader, node: number, m: Model): number {
  r.a = node;
  const b = r.u8();
  if (b < 0x80) {
    const off = r.u16();
    const inlineEnd = bsp(r, r.a, m);
    const other = node + 3 + off;
    if (other < inlineEnd) throw new Error(`model ${m.index}: BSP child $${other.toString(16)} overlaps inline subtree`);
    return bsp(r, other, m);
  }
  if (b !== 0xff) {
    m.attachments.push(~b & 0xff);
    return r.a;
  }
  for (let ng = r.u8(), g = 0; g < ng; g++) {
    const flags = r.u8();
    let test: Vec3 | undefined;
    if (flags & 1) {
      test = [r.u8(), r.u8(), r.u8()];
      r.a += 1;                                // skip count (bytes to the next group)
    }
    const t = test ? { test } : {};
    if (flags & 2)
      for (let n = r.u8(), k = 0; k < n; k++) {
        const nv = r.u8();
        const idx: number[] = [];
        for (let j = 0; j < nv; j++) idx.push(r.u8());
        m.polys.push({ idx, colour: r.u8(), plane: -1, order: m.polys.length, layer: 0, ...t });
      }
    if (flags & 4)
      for (let n = r.u8(), k = 0; k < n; k++) {
        const c = r.u8(), v0 = r.u8(), v1 = r.u8();
        m.lines.push({ v0, v1, colour: c & 0x7f, ...t });
      }
    if (flags & 8)
      for (let n = r.u8(), k = 0; k < n; k++) {
        const v = r.u8(), radius = r.u16();
        m.discs.push({ v, radius, colour: r.u8(), ...t });
      }
    if (flags & ~0xf) throw new Error(`model ${m.index}: unknown group flags $${flags.toString(16)}`);
  }
  return r.a;
}

// ---------------------------------------------------------------------------------------
// Geometry: planes and decal layers (floating point; only for rendering hints)

interface Plane {
  n: Vec3; // unit normal
  d: number; // n . p
}

const PLANE_TOLERANCE = 1.0; // model units
const NORMAL_TOLERANCE = 0.995; // cos of the max angle between "coplanar" normals
const OVERLAP_EPSILON = 0.5; // minimum overlap depth, in model units

function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function planeFrom(n: Vec3, p: Vec3): Plane | null {
  const len = Math.hypot(n[0], n[1], n[2]);
  if (len === 0) return null;
  const u: Vec3 = [n[0] / len, n[1] / len, n[2] / len];
  return { n: u, d: dot(u, p) };
}

/** Newell normal of a polygon (null if degenerate or fewer than 3 vertices). */
function polyPlane(pts: Vec3[]): Plane | null {
  if (pts.length < 3) return null;
  const n: Vec3 = [0, 0, 0];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    n[0] += (a[1] - b[1]) * (a[2] + b[2]);
    n[1] += (a[2] - b[2]) * (a[0] + b[0]);
    n[2] += (a[0] - b[0]) * (a[1] + b[1]);
  }
  const c: Vec3 = [0, 0, 0];
  for (const p of pts) for (let k = 0; k < 3; k++) c[k] += p[k] / pts.length;
  return planeFrom(n, c);
}

function onPlane(pl: Plane, pts: Vec3[]): boolean {
  return pts.every((p) => Math.abs(dot(pl.n, p) - pl.d) <= PLANE_TOLERANCE);
}

function assignPlanes(m: Model): void {
  const planes = m.planes.map(([a, b, c]) => {
    const va = m.vertices[a], vb = m.vertices[b], vc = m.vertices[c];
    if (!va || !vb || !vc) return null;
    const e1: Vec3 = [vb[0] - va[0], vb[1] - va[1], vb[2] - va[2]];
    const e2: Vec3 = [vc[0] - va[0], vc[1] - va[1], vc[2] - va[2]];
    return planeFrom(cross(e1, e2), va);
  });
  for (const p of m.polys) {
    const pts = p.idx.map((i) => m.vertices[i]);
    p.plane = planes.findIndex((pl) => pl !== null && onPlane(pl, pts));
  }
}

/** 2D projection onto the plane (drop the dominant normal axis). */
function project(n: Vec3, pts: Vec3[]): [number, number][] {
  const ax = Math.abs(n[0]), ay = Math.abs(n[1]), az = Math.abs(n[2]);
  const [i, j] = ax >= ay && ax >= az ? [1, 2] : ay >= az ? [2, 0] : [0, 1];
  return pts.map((p) => [p[i], p[j]]);
}

/** Separating-axis test for convex polygons/segments: overlap deeper than OVERLAP_EPSILON. */
function overlaps2d(a: [number, number][], b: [number, number][]): boolean {
  const axes: [number, number][] = [];
  for (const poly of [a, b]) {
    const n = poly.length;
    const edges = n === 2 ? 1 : n;
    for (let i = 0; i < edges; i++) {
      const p = poly[i], q = poly[(i + 1) % n];
      const ex = q[0] - p[0], ey = q[1] - p[1];
      const len = Math.hypot(ex, ey);
      if (len === 0) continue;
      axes.push([-ey / len, ex / len]);
      if (n === 2) axes.push([ex / len, ey / len]);
    }
  }
  for (const [x, y] of axes) {
    let amin = Infinity, amax = -Infinity, bmin = Infinity, bmax = -Infinity;
    for (const p of a) {
      const t = p[0] * x + p[1] * y;
      amin = Math.min(amin, t);
      amax = Math.max(amax, t);
    }
    for (const p of b) {
      const t = p[0] * x + p[1] * y;
      bmin = Math.min(bmin, t);
      bmax = Math.max(bmax, t);
    }
    const depth = Math.min(amax, bmax) - Math.max(amin, bmin);
    // A segment has no extent across itself: only require it not to be separated there.
    const degenerate = (a.length === 2 && amax - amin < 1e-9) || (b.length === 2 && bmax - bmin < 1e-9);
    if (degenerate ? depth < 0 : depth < OVERLAP_EPSILON) return false;
  }
  return axes.length > 0;
}

function assignLayers(m: Model): void {
  const pts = m.polys.map((p) => p.idx.map((i) => m.vertices[i]));
  const planes = pts.map(polyPlane);
  for (let j = 0; j < m.polys.length; j++) {
    let layer = 0;
    for (let i = 0; i < j; i++) {
      // The plane both lie on: the one of a proper polygon (same orientation if both are).
      const pi = planes[i], pj = planes[j];
      let pl: Plane | null;
      if (pi && pj) pl = dot(pi.n, pj.n) >= NORMAL_TOLERANCE ? pi : null;
      else pl = pi ?? pj;
      if (!pl || !onPlane(pl, pts[i]) || !onPlane(pl, pts[j])) continue;
      if (!overlaps2d(project(pl.n, pts[i]), project(pl.n, pts[j]))) continue;
      layer = Math.max(layer, m.polys[i].layer + 1);
    }
    m.polys[j].layer = layer;
  }
}
