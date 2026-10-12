/**
 * Reference model of the unit layer (docs/units.md), built on world.ts (tools/stsim.py).
 * A line-by-line port of tools/stunits.py.
 *
 * Same conventions as stsim: the state is the game's memory layout (UnitState extends
 * WorldState and adds UNIT_REGIONS), arithmetic follows the 68000 exactly, and every routine
 * is checked against the original under Unicorn (tools/check_units.py).
 *
 * UnitState.sim_pass() is one game_main simulation pass ($40B0-$413E without the UI, sound
 * and message-line routines, which don't touch simulation state). Ported:
 *   vehicles_step $1CD4E: carrier_step $1E744 (speed governor, autopilot, grounding), the
 *     Manta/Walrus loops (idle bays only), laser turret (outside its screen), crane and
 *     observation-turret angles $1F070, flares and falling drone $1F214, supply transfer (idle),
 *     lift (idle) and dock door;
 *   debris_step $1CC86; defence_drones_step $1A296;
 *   island_behaviours_step $1962A with the AA turret, SAM site creation, command centre,
 *     drone pad and anti-ship site handlers;
 *   ai_objects_step $1A56A (dispatch only: no AI handler is ported yet);
 *   object_passes $1B746: island attach/load/release, both collision passes and their
 *     scheduling (a contact raises NotImplementedError: the hit handlers aren't ported);
 *   carrier_systems_status $17578; island_range_check $1B8AE and the hangar timers in the tick;
 *   the island step's 3D buffer mirroring ($18276, $182A4);
 *   the shared maths: rotate_yaw $A5E0, rotate_pitch $A620, atan2 $AF8E, aim_angles $1A80C,
 *   turn_towards $1A18A, self_centre $1E734, yaw_vector $1A140.
 * Vehicles out of the hangar, the enemy's close-range combat and supply transfers, and the
 * carrier's weapons live in the mixins stunits_vehicles / stunits_enemy / stunits_carrier
 * (units_vehicles.ts / units_enemy.ts / units_carrier.ts). Anything not ported raises
 * NotPorted rather than guessing.
 */
import { NotPorted, divu, s16, s32, u32, w16, pyDiv } from "./mem.ts";
import type { Region } from "./mem.ts";
import {
  WorldState,
  OBJECTS as _OBJECTS,
  OBJECT_SIZE as _OBJECT_SIZE,
  OBJECT_HEADER as _OBJECT_HEADER,
  COUNT_ENEMY,
  COUNT_PLAYER,
  ROUTES_PENDING,
  ISLAND_TABLE,
  ISLAND_SIZE,
  SLOT_BASE,
  SLOT_SIZE,
  SYSTEMS,
  G_BUFFER_PTR,
  E_AIRCRAFT,
  E_STRENGTH,
  AI_ENABLED,
  GAME_END_TIMER,
  OWNERS,
  STOCKPILE,
} from "./world.ts";
import type { Slot, Island } from "./world.ts";

export const OBJECTS = _OBJECTS;
export const OBJECT_SIZE = _OBJECT_SIZE;
export const OBJECT_HEADER = _OBJECT_HEADER;
export const CARRIER = OBJECTS + OBJECT_SIZE * 10;
export const ISLAND_BUFFERS: [number, number] = [0x62cda, 0x63cec];
export const ISLAND_BUFFER_SIZE = 0x12 + 64 * 0x40;
export const DEBRIS = 0x11d8;
export const SHARDS = 0x66010;
export const OBJ_FLAGS = 0x19362;
export const OBJ_PARAMS = 0x193d6;
export const SLOT_ENTRY = 0x18066;
export const ISLAND_HANDLERS = 0x19778;
export const AI_HANDLERS = 0x1a5b6;
export const HIT_HANDLERS = 0x1bd70;
export const FRAME = 0x944;
export const NEW_INDEX = 0x6ee;
export const SINE = 0x1f5a; // 4096 words, amplitude 16384
export const ATAN = 0x3f5a; // atan2 divisor table

// carrier globals
export const C_FLAGS = 0x640;
export const C_SUBSTATE = 0x641;
export const C_RUDDER = 0x642;
export const C_SPEED = 0x644;
export const C_TARGET = 0x646;
export const C_DEPTH = 0x64a;
export const C_RECOIL = 0x64c;
export const C_WAYPOINT_X = 0x64e;
export const C_WAYPOINT_Z = 0x652;
export const C_CRUISE = 0x656;
export const C_DAMAGE_MSG = 0x658;
export const C_NEAR_UNIT = 0x65a;
export const C_FAST = 0x65b;
export const C_65C = 0x65c;
export const SHIP_FUEL = 0xcfc;
export const ENGINES_STATUS = 0xafa;
export const SUPERSTRUCTURE = 0xb3c;
export const SLOW_TICK = 0x8ff;
export const MOUSE_DX = 0x1268;
export const POINTER_MODE = 0x457;
export const FULL_REDRAW = 0x458;
export const KEY_SPEED_UP = 0x88a;
export const KEY_SPEED_DOWN = 0x88b;
export const KEY_UP_SCAN = 0x828;
export const KEY_DOWN_SCAN = 0x838;
export const SCREEN = 0x718;
export const VIEWED = 0x9fe;
export const MANTA_LANDING = 0x1232;
export const LIFT_STATE = 0xdce;
export const DOOR_STATE = 0xdda;
export const DOOR_POS = 0xdd8;
export const DOCKED_WALRUS = 0xde6;

export const UNIT_REGIONS: Region[] = [
  ["pass counters $8EC", 0x8ec, 2],
  ["slow tick $8FF", 0x8ff, 1],
  ["carrier globals", C_FLAGS, 0x65e - C_FLAGS],
  ["ship fuel", SHIP_FUEL, 4],
  ["mouse deltas", MOUSE_DX, 4],
  ["speed keys", KEY_SPEED_UP, 2],
  ["pointer mode", POINTER_MODE, 2],
  ["lift and dock", 0xdc4, 0xdec - 0xdc4],
  ["landing flag $1232", MANTA_LANDING, 2],
  ["sine table", SINE, 0x2000],
  ["atan table", ATAN, 0x102],
  ["key table", 0x798, 0x884 - 0x798],
  ["view pointers", 0x9fa, 0xc], // camera $9FA, viewed $9FE, controlled $A02
  ["island buffer 0", ISLAND_BUFFERS[0], ISLAND_BUFFER_SIZE],
  ["island buffer 1", ISLAND_BUFFERS[1], ISLAND_BUFFER_SIZE],
  ["game over $463", 0x463, 5], // $463-$466, $466 a word (result)
  ["object scratch $6EE", 0x6ee, 4],
  ["turret $6C8", 0x6c8, 0x10],
  ["vehicle bays", 0x496, 0x640 - 0x496],
  ["drone stations", 0x65c, 0x69c - 0x65c],
  ["lift object $6D6", 0x6d6, 0x6e2 - 0x6d6],
  ["score", 0x918, 12],
  ["debug flags $1188", 0x1188, 4],
  ["debris patch $1CBF4", 0x1cbf4, 2],
  ["debris recipes", 0x1c726, 58 * 18],
  ["vector $2C", 0x2c, 4],
  ["hit handler table", HIT_HANDLERS, 58 * 4],
  ["damage table", 0x1bd58, 24],
  ["island count $74E", 0x74e, 2],
  ["cooldowns $93E", 0x93e, 6],
  ["frame counter $944", 0x944, 2],
  ["island context", 0xa28, 0xa58 - 0xa28],
  ["matrices $A68", 0xa68, 12],
  ["collision scratch", 0xa9c, 0xab6 - 0xa9c],
  ["deck drones $C1A", 0xc1a, 2],
  ["supply transfer", 0xd3c, 0xd4a - 0xd3c],
  ["flares and turret", 0x1150, 0x10],
  ["debris free list", 0x1194, 6],
  ["debris bursts", DEBRIS, 4 * 0x12],
  ["drone launch $122F", 0x122f, 1],
  ["debris shards", SHARDS, 64 * 0x1a],
  ["objects", OBJECTS, 64 * OBJECT_SIZE],
  ["object tables", OBJ_FLAGS, 58 * 2 + 58 * 8],
  ["slot entry table", SLOT_ENTRY, 0x3a * 8],
  ["island handler table", ISLAND_HANDLERS, 58 * 4],
  ["ai handler table", AI_HANDLERS, 58 * 4],
  ["drone tables", 0x1a4aa, 0x1c],
];
export const UNIT_INPUT_REGIONS: Set<string> = new Set([
  "sine table", "atan table", "key table", "view pointers", "object tables",
  "debris recipes", "vector $2C", "hit handler table", "damage table", "debug flags $1188",
  "slot entry table", "island handler table", "ai handler table", "drone tables",
]);

/** `s16(((v) & 0xFFFFFFFF) >> 16)`: the high word of a 68000 muls result (v exact, < 2^53). */
function hi16(v: number): number {
  return s16(u32(v) >>> 16);
}

/** Python's `-0x8000 <= v < 0x8000`. */
function fitsWord(v: number): boolean {
  return -0x8000 <= v && v < 0x8000;
}

/** Python's `int.bit_length() - 1` of a positive power of two: the index of its bit. */
function lowBitIndex(v: number): number {
  return 31 - Math.clz32(v & -v);
}

function hex5(h: number): string {
  return h.toString(16).toUpperCase().padStart(5, "0");
}

export abstract class UnitCore extends WorldState {
  /** Python's from_ram override: copy UNIT_REGIONS too. */
  load_regions(ram: Uint8Array): void {
    super.load_regions(ram);
    for (const [, a, n] of UNIT_REGIONS) this.mem.buf.set(ram.subarray(a, a + n), a);
  }

  to_ram(ram: Uint8Array): void {
    super.to_ram(ram);
    for (const [, a, n] of UNIT_REGIONS) ram.set(this.mem.buf.subarray(a, a + n), a);
  }

  // ---- maths --------------------------------------------------------------------
  sin_cos(angle: number): [number, number] {
    const a = angle & 0xfff0;
    return [this.mem.s16(SINE + (a >> 3)), this.mem.s16(SINE + (w16(a + 0x4000) >> 3))];
  }

  /** $A5E0: (x, z) rotated by a 16-bit angle; words in, words out. */
  rotate_yaw(x: number, z: number, angle: number): [number, number] {
    const [s, c] = this.sin_cos(angle);
    x = s16(x);
    z = s16(z);
    const nx = hi16((x * c + z * s) * 4);
    const nz = hi16((z * c - x * s) * 4);
    return [nx, nz];
  }

  /** $A620: the pitch counterpart (signs swapped). */
  rotate_pitch(x: number, y: number, angle: number): [number, number] {
    const [s, c] = this.sin_cos(angle);
    x = s16(x);
    y = s16(y);
    const nx = hi16((x * c - y * s) * 4);
    const ny = hi16((y * c + x * s) * 4);
    return [nx, ny];
  }

  /** $AF8E: heading of (dx, dz), 0 = +z, $4000 = +x. Longs are halved until both fit
   * in a word. */
  atan2(dx: number, dz: number): number {
    dx = s32(dx);
    dz = s32(dz);
    while (!(fitsWord(dx) && fitsWord(dz))) {
      dx >>= 1;
      dz >>= 1;
    }
    return this._atan_sx(dx, dz);
  }

  _atan_sx(d0: number, d1: number): number { // $AF9A
    if (s16(d0) < 0) return w16(-this._atan_sz(s16(-d0), d1));
    return this._atan_sz(d0, d1);
  }

  _atan_sz(d0: number, d1: number): number { // $AFA6
    if (s16(d1) < 0) return w16(-this._atan_oct(d0, s16(-d1)) + 0x8000);
    return this._atan_oct(d0, d1);
  }

  _atan_oct(d0: number, d1: number): number { // $AFB6
    if (s16(d0) >= s16(d1)) return w16(-this._atan_core(d1, d0) + 0x4000);
    return this._atan_core(d0, d1);
  }

  _atan_core(d0: number, d1: number): number { // $AFC6
    if (w16(d1) === 0) return w16(d0);
    let q = (w16(d0) * 0x10000) / 2; // swap/clr/lsr.l #1 (unsigned)
    const n = s16(d1);
    const qq = n ? Math.trunc(q / n) : 0; // divs.w: truncates towards zero
    if (!fitsWord(qq)) return 0x2000;
    q = w16(qq);
    const v = q * 256;
    const t = this.mem.u16(ATAN + 2 * (v >> 16));
    const res = t ? Math.floor(v / t) : 0;
    const lo = res <= 0xffff ? w16(res) : w16(v); // divu overflow leaves d1
    return w16(lo << 4);
  }

  /** $1A18A: move current towards target by at most step (unsigned clamp). */
  static turn_towards(target: number, current: number, step: number): number {
    const d = w16(target - current);
    let mag = d & 0x8000 ? w16(-s16(d)) : d;
    if (mag > w16(step)) mag = w16(step);
    return w16(current + (d & 0x8000 ? w16(-mag) : mag));
  }

  turn_towards(target: number, current: number, step: number): number {
    return UnitCore.turn_towards(target, current, step);
  }

  /** $1E734: v - v/8 (at least 1 towards 0). */
  static self_centre(v: number): number {
    v = s16(v);
    if (v === 0) return 0;
    let d = v >> 3;
    if (d === 0) d = 1;
    return w16(v - d);
  }

  self_centre(v: number): number {
    return UnitCore.self_centre(v);
  }

  /** $1A140: (0, speed) rotated by the object's heading, as longs. */
  yaw_vector(obj: number, speed: number): [number, number] {
    return this.rotate_yaw(0, speed, this.mem.u16(obj + 6));
  }

  /** $1A1A2: |dx| - half_x, |dz| - half_z from the island buffer's origin. */
  island_box_distance(obj: number, buf: number): [number, number] {
    const m = this.mem;
    const dx = Math.abs(s32(m.u32(buf + 2) - m.u32(obj + 0x0a)));
    const dz = Math.abs(s32(m.u32(buf + 0x0a) - m.u32(obj + 0x12)));
    return [s32(dx - m.s16(buf + 0x30)), s32(dz - m.s16(buf + 0x32))];
  }

  // ---- carrier -------------------------------------------------------------------
  /** $1E744: player carrier control and motion (object 10). */
  carrier_step(): void {
    const m = this.mem;
    const o = CARRIER;
    if (m.u16(SUPERSTRUCTURE) === 0) {
      m.set8(0x463, 0xff);
      m.set16(0x466, 1);
      this.events.push(["carrier destroyed"]);
    }
    this.manta_lift_step();
    this.walrus_dock_door_step();
    if (m.u16(C_DAMAGE_MSG)) m.set16(C_DAMAGE_MSG, m.u16(C_DAMAGE_MSG) - 1);
    if (!(m.u8(o + 1) & 1)) return;
    if (m.u8(C_FLAGS) & 1) {
      this.carrier_scripted();
    } else if (m.u16(SCREEN) === 0) {
      if (!m.u8(POINTER_MODE) && !m.u8(FULL_REDRAW)) {
        m.set8(C_FLAGS, m.u8(C_FLAGS) & 0x7f);
        const r = s16(m.u16(C_RUDDER) + m.u16(MOUSE_DX));
        m.set16(MOUSE_DX, 0);
        m.set16(C_RUDDER, Math.max(-255, Math.min(255, r)));
      }
      if (m.u8(KEY_SPEED_UP) || m.u8(KEY_UP_SCAN)) {
        m.set16(C_TARGET, m.u16(C_SPEED) + 1);
      } else if (m.u8(KEY_SPEED_DOWN) || m.u8(KEY_DOWN_SCAN)) {
        m.set16(C_TARGET, m.u16(C_SPEED) - 1);
      }
    }
    this.carrier_move();
  }

  /** $1E95C: recoil after grounding ($641 = 0), else the autopilot. */
  carrier_scripted(): void {
    const m = this.mem;
    const o = CARRIER;
    if (!m.u8(C_SUBSTATE)) {
      if (this.countdown16(C_RECOIL)) {
        m.set8(C_FLAGS, m.u8(C_FLAGS) & 0x7e);
        m.set8(C_SUBSTATE, 1);
      }
      return;
    }
    if (!(m.u8(C_FLAGS) & 2)) return this.carrier_stop();
    const dx = s32(m.u32(C_WAYPOINT_X) - m.u32(o + 0x0a));
    const dz = s32(m.u32(C_WAYPOINT_Z) - m.u32(o + 0x12));
    const d = Math.max(Math.abs(dx), Math.abs(dz));
    let cruise = m.s16(C_CRUISE);
    if (d <= 2000 && cruise > 0x20) cruise = 0x20;
    m.set16(C_TARGET, cruise);
    if (d < 1000) {
      if (m.u8(C_FLAGS) & 4) this.events.push(["message", "carrier reached waypoint", this._ping()]);
      m.set8(C_FLAGS, m.u8(C_FLAGS) & 0xfc);
      return this.carrier_stop();
    }
    const err = s16(this.atan2(dx, dz) - m.u16(o + 6));
    if (Math.abs(err) > 0x100) m.set16(C_TARGET, 0x10);
    const want = Math.max(-255, Math.min(255, err >> 2));
    m.set16(C_RUDDER, this.turn_towards(want, m.u16(C_RUDDER), 0x20));
  }

  /** $1E986: rudder self-centres, target speed 0. */
  carrier_stop(): void {
    this.mem.set8(C_FLAGS, this.mem.u8(C_FLAGS) | 0x80);
    this.mem.set16(C_TARGET, 0);
  }

  /** $1E802: fuel, speed governor, heading and position, depth and grounding. */
  carrier_move(): void {
    const m = this.mem;
    const o = CARRIER;
    if (m.u8(SLOW_TICK)) {
      let burn = Math.abs(m.s16(C_SPEED)) * 4;
      if (m.u8(C_FAST)) burn *= 4;
      burn = s16(burn); // add.w, then ext.l
      const fuel = m.u32(SHIP_FUEL) - u32(burn);
      m.set32(SHIP_FUEL, fuel < 0 ? 0 : fuel);
    }
    this.carrier_speed_governor();
    if (m.u8(C_FLAGS) & 0x80) {
      if (m.u16(C_RUDDER) === 0) {
        m.set8(C_FLAGS, m.u8(C_FLAGS) & 0x7f);
      } else {
        m.set16(C_RUDDER, this.self_centre(m.u16(C_RUDDER)));
      }
    }
    m.set16(o + 6, m.u16(o + 6) + Math.trunc(m.s16(C_RUDDER) / 6));
    m.set8(C_FAST, 0);
    let speed = m.u16(C_SPEED);
    if (!(m.u8(C_NEAR_UNIT) || m.u16(C_65C) || m.u8(MANTA_LANDING) || m.u32(o + 0x3c))) {
      speed = w16(speed * 4);
      m.set8(C_FAST, 0xff);
    }
    const [vx, vz] = this.yaw_vector(o, speed);
    m.set32(o + 0x0a, m.u32(o + 0x0a) + vx);
    m.set32(o + 0x12, m.u32(o + 0x12) + vz);
    m.set16(C_DEPTH, 0x24);
    const buf = m.u32(o + 0x3c);
    if (buf) {
      const [dx, dz] = this.island_box_distance(o, buf);
      const d = s32(Math.max(dx, dz) - 10000);
      const q = Math.trunc(d / 256);
      if (!fitsWord(q)) throw new NotPorted("depth divide overflow");
      let depth = s16(q);
      if (depth < 0 && !(m.u8(C_FLAGS) & 1 && !m.u8(C_SUBSTATE))) {
        this.carrier_ground();
        depth = 0;
      }
      m.set16(C_DEPTH, Math.max(0, Math.min(0x24, depth)));
    }
  }

  /** $1E8CE: run aground: superstructure -1000, message and sound, reverse recoil. */
  carrier_ground(): void {
    const m = this.mem;
    const v = m.u16(SUPERSTRUCTURE) - 1000;
    m.set16(SUPERSTRUCTURE, v < 0 ? 0 : v);
    m.set16(0x952, 2);
    this.events.push(["message", "Carrier has grounded", this._ping(true)]);
    this._sfx(1);
    m.set8(C_FLAGS, m.u8(C_FLAGS) | 0x81);
    m.set8(C_SUBSTATE, 0);
    m.set16(C_RECOIL, 50);
    const back = m.s16(C_SPEED) < 0 ? 0x20 : -0x20;
    m.set16(C_SPEED, 0);
    m.set16(C_TARGET, back);
    if (m.u32(VIEWED) === CARRIER) m.set8(POINTER_MODE, 0xff);
  }

  /** $1EA3E: clamp the target to what engines, fuel and surroundings allow, then
   * approach it by 1/32 of the difference (at least 1). */
  carrier_speed_governor(): void {
    const m = this.mem;
    const o = CARRIER;
    let target: number;
    if (m.u32(SHIP_FUEL) === 0) {
      target = 0;
    } else {
      const status = m.u16(ENGINES_STATUS);
      let fwd = status >> 1;
      if (s16(fwd) > 0x80) fwd = 0x80;
      if ((m.u16(C_65C) || m.u8(MANTA_LANDING) || m.u32(o + 0x3c)) && s16(fwd) > 0x3c) fwd = 0x3c;
      const back = s16(w16(-status)) >> 3;
      target = m.s16(C_TARGET);
      if (target > s16(fwd)) {
        target = s16(fwd);
      } else if (target < back) {
        target = back;
      }
    }
    const d = s16(target - m.u16(C_SPEED));
    if (d) {
      let step = d >> 5;
      if (step === 0) step = 1;
      m.set16(C_SPEED, m.u16(C_SPEED) + step);
    }
  }

  // ---- lift and dock --------------------------------------------------------------
  /** $1EAA0: the Manta lift state machine (only the idle state is ported). */
  manta_lift_step(): void {
    if (this.mem.u16(LIFT_STATE)) throw new NotPorted(`lift state ${this.mem.u16(LIFT_STATE)}`);
  }

  /** $1ED94: the Walrus dock door: 1 opening (closes again if no Walrus is docked),
   * 2 open, 3 closing. */
  walrus_dock_door_step(): void {
    const m = this.mem;
    const state = m.u16(DOOR_STATE);
    if (state === 0) return;
    if (state !== 3) {
      if (m.s16(DOCKED_WALRUS) >= 0) {
        if (state === 1) {
          let pos = s16(m.u16(DOOR_POS) + 0x5dc);
          if (pos >= 0) {
            pos = 0;
            m.set16(DOOR_STATE, 2);
          }
          m.set16(DOOR_POS, pos);
        }
        return;
      }
      m.set16(DOOR_STATE, 3);
    }
    let pos = s16(m.u16(DOOR_POS) - 0x5dc);
    if (pos < -0x3000) {
      this.dock_attach();
      pos = -0x3000;
      m.set16(DOOR_STATE, 0);
    }
    m.set16(DOOR_POS, pos);
  }

  // ---- objects --------------------------------------------------------------------
  obj(k: number): number {
    return OBJECTS + OBJECT_SIZE * k;
  }

  /** $192E2: clear the header, stamp all 128 schedule words with the frame, set
   * type, flags and the per-type collision parameters. */
  init_world_object(a: number, t: number): void {
    const m = this.mem;
    m.buf.fill(0, a, a + 0x40);
    const f = m.u16(FRAME);
    for (let k = 0; k < 128; k++) m.set16(a + 0x40 + 2 * k, f);
    this._init_common(a, t);
  }

  /** $1932C: the same for a $40-byte island-buffer entry (no schedules). */
  init_island_object(a: number, t: number): void {
    this.mem.buf.fill(0, a, a + 0x40);
    this._init_common(a, t);
  }

  _init_common(a: number, t: number): void {
    const m = this.mem;
    m.set16(a + 2, t);
    m.set16(a, m.u16(OBJ_FLAGS + 2 * t));
    for (let k = 0; k < 4; k++) m.set16(a + 0x30 + 2 * k, m.u16(OBJ_PARAMS + 8 * t + 2 * k));
  }

  /** $195A6: a new island entry [$6EE]: every colliding object attached to this buffer
   * tests it next pass. */
  reset_island_schedule(): void {
    const m = this.mem;
    const buf = m.u32(0xa28);
    const k = m.u16(NEW_INDEX);
    const f = m.u16(FRAME);
    for (let i = 0; i < 64; i++) {
      const o = this.obj(i);
      if ((m.u16(o) & 0x401) === 0x401 && m.u32(o + 0x3c) === buf) m.set16(o + 0x40 + 2 * k, f);
    }
  }

  /** $195DA: a new world object [$6EE]: every object tests it next pass. */
  reset_pair_schedules(): void {
    const m = this.mem;
    const off = 0xc0 + 2 * m.u16(NEW_INDEX);
    const f = m.u16(FRAME);
    for (let i = 0; i < 64; i++) m.set16(this.obj(i) + off, f);
  }

  /** $1A10E: the first object 24..[$A48]-1 with byte +1 bit 0 clear, index to $6EE. */
  find_free_object(): number | null {
    const m = this.mem;
    const n = s16(m.u16(0xa48) - 24);
    if (n <= 0) return null;
    m.set16(NEW_INDEX, 24);
    for (let k = 0; k < n; k++) {
      const a = this.obj(24 + k);
      if (!(m.u8(a + 1) & 1)) return a;
      m.set16(NEW_INDEX, 25 + k);
    }
    return null;
  }

  /** $1A0DC: a free dynamic entry (32 up) of the current buffer [$A28]. */
  find_free_island_object(): number | null {
    const m = this.mem;
    const buf = m.u32(0xa28);
    const n = s16(m.u16(buf + 0x0e) - 0x21);
    if (n < 0) return null;
    m.set16(NEW_INDEX, 0x1f);
    let a = buf + 0x812;
    for (let i = 0; i < n + 1; i++) {
      m.set16(NEW_INDEX, m.u16(NEW_INDEX) + 1);
      if (!(m.u8(a + 1) & 1)) return a;
      a += 0x40;
    }
    return null;
  }

  /** $1A80C: (pitch, heading) towards a 32-bit offset; scratch at $A80-$A85. */
  aim_angles(dx: number, dy: number, dz: number): [number, number] {
    const m = this.mem;
    dx = s32(dx) >> 1;
    dy = s32(dy) >> 1;
    dz = s32(dz) >> 1;
    m.set16(0xa84, -dy);
    m.set16(0xa80, dx);
    m.set16(0xa82, dz);
    const heading = this.atan2(dx, dz);
    const [, z] = this.rotate_yaw(m.s16(0xa80), m.s16(0xa82), w16(-heading));
    const pitch = this.atan2(m.s16(0xa84), z);
    return [pitch, heading];
  }

  // ---- pass ----------------------------------------------------------------------
  /** One game_main simulation pass ($40B0-$413E) minus UI, sound and messages. */
  sim_pass(): void {
    const m = this.mem;
    if (m.u16(COUNT_ENEMY) === 0x40) {
      m.set16(0x466, 2);
      m.set8(0x463, 0xff);
    } else if (m.u16(COUNT_PLAYER) === 0x40) {
      m.set16(0x466, 3);
      m.set8(0x463, 0xff);
    }
    if (m.u8(ROUTES_PENDING)) this.relax_routes();
    m.set8(0x8fe, 0); // $8FE: set on passes that run the tick (never read)
    if (m.u8(0x8fd)) {
      m.set8(0x8fe, 0xff);
      this.logistics_tick();
    }
    this.step();
    this.debris_step();
    this.vehicles_step();
    this.island_behaviours_step();
    this.ai_objects_step();
    this.enemy_ai();
    this.defence_drones_step();
    this.object_passes();
    this.carrier_systems_status();
  }

  // ---- vehicles -------------------------------------------------------------------
  /** $1CD4E. */
  vehicles_step(): void {
    const m = this.mem;
    const c = (m.u16(0x8ec) + 1) & 15;
    m.set16(0x8ec, c);
    m.set8(SLOW_TICK, c === 0 ? 0xff : 0);
    if (m.u16(0x115e)) m.set16(0x115e, m.u16(0x115e) - 1);
    this.carrier_step();
    m.set8(C_NEAR_UNIT, 0);
    this.bays_step(0x940, 2, 0x496, "Manta");
    this.bays_step(0x942, 6, 0x570, "Walrus");
    this.carrier_laser_turret_step();
    this.crane_step();
    this.flares_step();
    this.supply_transfer_step();
    m.set8(KEY_SPEED_UP, 0);
    m.set8(KEY_SPEED_DOWN, 0);
    if (!m.u8(POINTER_MODE)) {
      m.set16(MOUSE_DX, 0);
      m.set16(MOUSE_DX + 2, 0);
    }
  }

  /** $1CDA2 / $1DCFC: per bay, vehicles that are out (status >= 3) fly or drive. */
  bays_step(cooldown: number, first: number, records: number, kind: string): void {
    const m = this.mem;
    if (m.u16(cooldown)) m.set16(cooldown, m.u16(cooldown) - 1);
    m.set16(0x6f0, first);
    for (let k = 0; k < 4; k++) {
      if (m.s8(records + 0x34 * k + 1) >= 3) throw new NotPorted(`${kind} ${k} out of the hangar`);
      m.set16(0x6f0, first + k + 1);
    }
  }

  /** $1EDFC: cooldown and heat always; aiming and firing on screen 8 (not ported);
   * otherwise the aim rates self-centre and integrate into elevation/heading. */
  carrier_laser_turret_step(): void {
    const m = this.mem;
    if (m.u16(0x93e)) m.set16(0x93e, m.u16(0x93e) - 1);
    let h = m.u16(0x6c8);
    if (h) {
      h = s16(h - 16);
      m.set16(0x6c8, h < 0 ? 0 : h);
    }
    if (m.u16(SCREEN) === 8) throw new NotPorted("laser turret screen");
    for (const [bit, rate] of [[0x80, 0x6ce], [0x40, 0x6d0]]) {
      if (m.u8(0x6d5) & bit) {
        if (m.u16(rate) === 0) {
          m.set8(0x6d5, m.u8(0x6d5) & ~bit);
        } else {
          m.set16(rate, this.self_centre(m.u16(rate)));
        }
      }
    }
    const e = s16(-this.quantise(m.u16(0x6d0)) + m.u16(0x6ca));
    m.set16(0x6ca, e > 0x1f4 ? 0x1f4 : e < -16000 ? -16000 : e);
    m.set16(0x6cc, m.u16(0x6cc) + 2 * this.quantise(m.u16(0x6ce)));
  }

  /** $1F03E: drop the low 4 bits of the magnitude. */
  static quantise(v: number): number {
    v = s16(v);
    return v < 0 ? w16(-(-v & 0xfff0)) : v & 0xfff0;
  }

  quantise(v: number): number {
    return UnitCore.quantise(v);
  }

  /** $A438 with a2 = $A6C: (sin, cos) of the angle. */
  set_matrix(angle: number): void {
    const [s, c] = this.sin_cos(angle);
    this.mem.set16(0xa6c, s);
    this.mem.set16(0xa6e, c);
  }

  /** $A558: rotate by the (sin, cos) pair at $A6C. */
  matrix_rotate(x: number, z: number): [number, number] {
    const m = this.mem;
    const s = m.s16(0xa6c);
    const c = m.s16(0xa6e);
    x = s16(x);
    z = s16(z);
    return [hi16((x * c + z * s) * 4), hi16((z * c - x * s) * 4)];
  }

  /** Shared by $1F070: heading from a point on the carrier to an object, relative to
   * the carrier's heading. */
  _bearing_from_carrier(target: number, ox: number, oz: number): number {
    const m = this.mem;
    const c = CARRIER;
    const [x, z] = this.matrix_rotate(ox, oz);
    const dx = s32(m.u32(target + 0x0a) - (x + m.u32(c + 0x0a)));
    const dz = s32(m.u32(target + 0x12) - (z + m.u32(c + 0x12)));
    return w16(this.atan2(dx, dz) - m.u16(c + 6));
  }

  /** $1F070: animation angles: lift crane $DDE, dock crane $DEA, observation turret
   * $1158/$115A tracking the object [$1154]. */
  crane_step(): void {
    const m = this.mem;
    const c = CARRIER;
    this.set_matrix(m.u16(c + 6));
    const bay = m.s16(0xdd0);
    let want: number;
    let step: number;
    if (bay >= 0) {
      want = this._bearing_from_carrier(this.obj(2 + bay), 0x1a4, 0x41a);
      step = 0x400;
    } else {
      want = 0x93a0;
      step = 0x100;
    }
    m.set16(0xdde, this.turn_towards(want, m.u16(0xdde), step));
    const d = m.s16(DOCKED_WALRUS);
    if (d >= 0) {
      let t: number;
      if (d === 4) {
        t = this.obj(16);
      } else {
        t = (m.u8(0x122f) ? this.obj(11) : this.obj(6)) + OBJECT_SIZE * d;
      }
      want = this._bearing_from_carrier(t, -0x1a4, -0x41a);
      if (want < 0x4000) want = 0x4000;
      step = 0x400;
    } else {
      want = 0x4000;
      step = 0x100;
    }
    m.set16(0xdea, this.turn_towards(want, m.u16(0xdea), step));
    const t = m.u32(0x1154);
    if (m.u8(t + 1) & 1) {
      const [x, z] = this.matrix_rotate(0, -1000);
      const dx = s32(m.u32(t + 0x0a) - (x + m.u32(c + 0x0a)));
      const dz = s32(m.u32(t + 0x12) - (z + m.u32(c + 0x12)));
      const dy = s32(m.u32(t + 0x0e) - 300);
      if (fitsWord(dx) && fitsWord(dy) && fitsWord(dz)) {
        const [pitch, heading] = this.aim_angles(dx, dy, dz);
        m.set16(0x1158, this.turn_towards(pitch, m.u16(0x1158), 0x800));
        m.set16(0x115a, this.turn_towards(heading, m.u16(0x115a), 0x800));
        return;
      }
    }
    m.set16(0x1158, this.turn_towards(0, m.u16(0x1158), 0x100));
    m.set16(0x115a, this.turn_towards(m.u16(c + 6), m.u16(0x115a), 0x100));
  }

  /** $1F214: decoy flares (objects 17, 18) and the falling object 15. */
  flares_step(): void {
    const m = this.mem;
    if (m.u16(0x1150)) m.set16(0x1150, m.u16(0x1150) - 1);
    if (m.u16(0x1152)) {
      if (m.u16(0x1152) === 2) m.set16(0x1150, 1);
      for (const k of [17, 18]) {
        const a = this.obj(k);
        if (!(m.u8(a + 1) & 1)) continue;
        let dy: number;
        if (m.u16(a + 2) !== 0x1e) {
          dy = -15;
        } else {
          dy = m.s16(a + 0x22);
          const v = s16(dy - 10);
          if (v < -10) {
            m.set16(a + 2, 0x1f);
            m.set16(a + 0x1e, 0x32);
          }
          m.set16(a + 0x22, v);
        }
        m.set32(a + 0x0e, m.u32(a + 0x0e) + dy);
        const [vx, vz] = this.yaw_vector(a, 15);
        m.set32(a + 0x0a, m.u32(a + 0x0a) + vx);
        m.set32(a + 0x12, m.u32(a + 0x12) + vz);
        let v = m.s16(a + 0x1e);
        if (m.u8(a + 0x1a)) {
          v = s16(v - 5);
          if (v < 0x1e) m.set8(a + 0x1a, 0);
        } else {
          v = s16(v + 5);
          if (v > 0x64) m.set8(a + 0x1a, 0xff);
        }
        m.set16(a + 0x1e, v);
        if (this.decrement_borrows(a + 0x20)) {
          m.set16(a, 0);
          m.set16(0x1152, m.u16(0x1152) - 1);
        }
      }
    }
    const a = this.obj(15);
    if (m.u8(a + 1) & 1) {
      const dy = m.s16(a + 0x22);
      const v = s16(dy - 2);
      m.set16(a + 0x22, v < -40 ? -40 : v);
      m.set32(a + 0x0e, m.u32(a + 0x0e) + dy);
      let sp = s16(m.u16(a + 0x24) - 1);
      sp = sp < 0 ? 0 : sp;
      m.set16(a + 0x24, sp);
      const [vx, vz] = this.yaw_vector(a, sp);
      m.set32(a + 0x0a, m.u32(a + 0x0a) + vx);
      m.set32(a + 0x12, m.u32(a + 0x12) + vz);
      if (this.decrement_borrows(a + 0x20)) m.set16(a, 0);
    }
  }

  /** subq.w #1 / bcc: decrement a word; True if it was 0 (borrow). */
  decrement_borrows(a: number): boolean {
    const v = this.mem.u16(a);
    this.mem.set16(a, v - 1);
    return v === 0;
  }

  /** $1F316: the supply drone's transfer state machine (only the idle path). */
  supply_transfer_step(): void {
    const m = this.mem;
    if (m.u8(0xd3e)) throw new NotPorted("supply transfer");
    if (m.u16(0xd44) && m.u8(0xd3f)) m.set16(0xd44, m.u16(0xd44) - 1);
    m.set8(0xd3f, 0);
  }

  // ---- debris ---------------------------------------------------------------------
  /** $1CC86: four bursts of shards falling under gravity. */
  debris_step(): void {
    const m = this.mem;
    for (let b = 0; b < 4; b++) {
      const a = DEBRIS + 0x12 * b;
      if (!(m.u8(a) & 1)) continue;
      const age = (m.u8(a + 1) + 1) & 0xff;
      m.set8(a + 1, age);
      if (age === 0) {
        this.free_burst(a);
        continue;
      }
      if (m.u32(a + 0x0e) === 0) {
        m.set8(a, m.u8(a) & 0xfe);
        continue;
      }
      const floor = s16(-m.u32(a + 6) + 10);
      let prev = a - 8; // prev + $16 aliases the burst's list head
      let cur = m.u32(a + 0x0e);
      while (cur) {
        m.set16(cur + 0x0a, m.u16(cur + 0x0a) + m.u16(cur + 0x10));
        m.set16(cur + 0x0e, m.u16(cur + 0x0e) + m.u16(cur + 0x14));
        let vy = m.s16(cur + 0x12);
        const y = s16(m.u16(cur + 0x0c) + vy);
        if (y < floor) {
          m.set32(prev + 0x16, m.u32(cur + 0x16));
          m.set32(cur + 0x16, m.u32(0x1194));
          m.set32(0x1194, cur);
          m.set16(0x1198, m.u16(0x1198) + 1);
        } else {
          m.set16(cur + 0x0c, y);
          if (vy < -40) {
            vy = s16(vy - ((vy + 40) >> 2));
          } else {
            vy = s16(vy - 10);
            if (vy < -40) vy = -40;
          }
          m.set16(cur + 0x12, vy);
          for (const o of [4, 6, 8]) m.set16(cur + o, m.u16(cur + o) + 0x1000);
          prev = cur;
        }
        cur = m.u32(prev + 0x16);
      }
    }
  }

  /** $1CC5C: return a burst's shards to the free list. */
  free_burst(a: number): void {
    const m = this.mem;
    if (!(m.u8(a) & 1)) return;
    m.set8(a, m.u8(a) & 0xfe);
    let cur = m.u32(a + 0x0e);
    if (!cur) return;
    const old = m.u32(0x1194);
    m.set32(0x1194, cur);
    for (;;) {
      m.set16(0x1198, m.u16(0x1198) + 1);
      const nxt = m.u32(cur + 0x16);
      if (!nxt) break;
      cur = nxt;
    }
    m.set32(cur + 0x16, old);
  }

  // ---- defence drones -------------------------------------------------------------
  static DRONE_CORNERS: [number, number][] = [[1400, -2000], [-1400, -2000], [1400, 2000], [-1400, 2000]];

  /** $1A296: the carrier's four defence drones (objects 11-14) and their stations. */
  defence_drones_step(): void {
    const m = this.mem;
    if (m.u8(0x122f)) {
      let k = m.u16(0x69a);
      const rec = 0x65e + 14 * k;
      const flags = m.u16(rec);
      if (!(flags & 1)) {
        k += 1;
        m.set16(0x69a, k);
        if (k >= 4) {
          m.set8(0x122f, 0);
          return;
        }
      } else if (flags & 2) {
        if (!(flags & 8)) {
          m.set16(DOCKED_WALRUS, k);
          m.set16(DOOR_STATE, 1);
          this.dock_attach();
          m.set8(rec + 1, m.u8(rec + 1) | 8);
        }
        let z = m.s16(rec + 4);
        if (z >= -600) {
          if (m.u16(DOOR_STATE) === 0) {
            m.set8(rec + 1, m.u8(rec + 1) & 0xfe);
            m.set16(DOCKED_WALRUS, 0xffff);
            m.set16(0xc1a, m.u16(0xc1a) + 1);
            this.dock_attach();
          }
        } else {
          z = s16(z + 30);
          m.set16(rec + 4, z);
          if (z >= -600) {
            m.set16(DOOR_STATE, 3);
            this.dock_attach();
          }
        }
      }
    }
    m.set16(0x65c, 0);
    for (let k = 0; k < 4; k++) {
      const a = this.obj(11 + k);
      const rec = 0x65e + 14 * k;
      m.set16(a + 6, m.u16(a + 6) + 0x1f4);
      const flags = m.u16(rec);
      m.set8(a + 1, m.u8(a + 1) & 0xfe);
      if (!(flags & 1)) continue;
      m.set16(0x65c, m.u16(0x65c) + 1);
      m.set8(a + 1, m.u8(a + 1) | 1);
      if (!(flags & 2)) {
        if (flags & 4) {
          this._drone_move(rec);
        } else {
          this._drone_route(rec);
        }
      }
      const c = CARRIER;
      const x = m.u16(rec + 2);
      const z = m.u16(rec + 4);
      m.set16(a + 0x1e, z);
      const [vx, vz] = this.rotate_yaw(x, z, m.u16(c + 6));
      m.set32(a + 0x0a, m.u32(c + 0x0a) + vx);
      m.set32(a + 0x12, m.u32(c + 0x12) + vz);
    }
  }

  /** $1A466: |d2-d0| < lim and |d3-d1| < lim, unsigned. */
  static _near(d0: number, d1: number, d2: number, d3: number, lim: number): boolean {
    const adiff = (a: number, b: number): number => {
      const d = (b - a) & 0xffff;
      return (b & 0xffff) >= (a & 0xffff) ? d : w16(-d);
    };
    return adiff(d0, d2) < lim && adiff(d1, d3) < lim;
  }

  _near(d0: number, d1: number, d2: number, d3: number, lim: number): boolean {
    return UnitCore._near(d0, d1, d2, d3, lim);
  }

  _drone_move(rec: number): void {
    const m = this.mem;
    const x = m.u16(rec + 2);
    const z = m.u16(rec + 4);
    const tx = m.u16(rec + 6);
    const tz = m.u16(rec + 8);
    if (this._near(x, z, tx, tz, 10)) {
      m.set8(rec + 1, m.u8(rec + 1) & ~4);
      if (tx === m.u16(rec + 0x0a) && tz === m.u16(rec + 0x0c)) {
        m.set16(rec + 2, m.u16(rec + 0x0a));
        m.set16(rec + 4, m.u16(rec + 0x0c));
        m.set8(rec + 1, m.u8(rec + 1) | 2);
      }
      return;
    }
    const div = this._near(x, z, tx, tz, 40) ? 10 : 30;
    const dx = s16(tx - x);
    const dz = s16(tz - z);
    let steps = s16(Math.abs(dx) + Math.abs(dz));
    steps = Math.trunc(steps / div);
    if (steps === 0) throw new NotPorted("drone divide by zero");
    m.set16(rec + 2, x + Math.trunc(dx / steps));
    m.set16(rec + 4, z + Math.trunc(dz / steps));
  }

  /** $1A47E: which side of the carrier's box a point is on. */
  static _region(x: number, z: number): number {
    x = s16(x);
    z = s16(z);
    let c = 0;
    if (x <= -900) c |= 1;
    if (x >= 900) c |= 2;
    if (z >= 1500) c |= 8;
    if (z <= -1500) c |= 4;
    return c;
  }

  _region(x: number, z: number): number {
    return UnitCore._region(x, z);
  }

  _drone_route(rec: number): void {
    const m = this.mem;
    const here = this._region(m.u16(rec + 2), m.u16(rec + 4));
    const there = this._region(m.u16(rec + 0x0a), m.u16(rec + 0x0c));
    let tx: number;
    let tz: number;
    if (here & there) {
      tx = m.u16(rec + 0x0a);
      tz = m.u16(rec + 0x0c);
    } else {
      const a = m.u8(0x1a4ba + here);
      const b = m.u8(0x1a4ba + there);
      const sel = (a & b) || a;
      if (sel === 0) throw new NotPorted("drone route: no corner (original loops forever)");
      [tx, tz] = UnitCore.DRONE_CORNERS[lowBitIndex(sel)];
    }
    m.set16(rec + 6, tx);
    m.set16(rec + 8, tz);
    m.set8(rec + 1, m.u8(rec + 1) | 4);
  }

  // ---- island behaviours ------------------------------------------------------------
  /** $1962A: run the behaviours of both loaded island buffers. */
  island_behaviours_step(): void {
    for (const buf of ISLAND_BUFFERS) {
      if (this.mem.u8(buf) & 1) this._island_behaviours(buf);
    }
  }

  _island_behaviours(buf: number): void {
    const m = this.mem;
    m.set8(0xa43, 0);
    m.set8(0xa42, 0);
    const cam = m.u32(0x9fa);
    if (cam) m.set8(0xa43, m.u32(cam + 0x3c) === buf ? 0xff : 0);
    m.set32(0xa28, buf);
    for (let k = 0; k < 3; k++) m.set32(0xa4a + 4 * k, m.u32(buf + 2 + 4 * k));
    const i = m.u8(buf + 1);
    m.set16(0xa34, i);
    const owner = this.owner(i);
    m.set32(0xa2c, ISLAND_TABLE + ISLAND_SIZE * i);
    m.set8(0xa45, owner === 0 ? 0xff : 0);
    if (owner) {
      m.set8(0xa44, owner === 1 ? 0xff : 0);
      const [first, n] = owner === 1 ? [19, 5] : [2, 9];
      for (let k = 0; k < n; k++) {
        const o = this.obj(first + k);
        if ((m.u16(o) & 0x201) === 0x201 && m.u32(o + 0x3c) === buf) {
          m.set8(0xa42, 0xff);
          break;
        }
      }
    }
    const count = m.u16(buf + 0x0e);
    for (let j = 0; j < count; j++) {
      const e = buf + 0x12 + 0x40 * j;
      const f = m.u16(e);
      if ((f & 0x101) !== 0x101) continue;
      if (!m.u8(0xa43) && f & 0x200) continue;
      for (let k = 0; k < 3; k++) m.set32(0xa36 + 4 * k, m.u32(buf + 2 + 4 * k) + m.s16(e + 0x0a + 2 * k));
      const h = m.u32(ISLAND_HANDLERS + 4 * m.u16(e + 2));
      if (h === 0x19860) continue;
      const fn = UnitCore.ISLAND_BEHAVIOURS[h];
      if (fn === undefined) throw new NotPorted(`island behaviour $${hex5(h)} (type ${m.u16(e + 2)})`);
      (this as any)[fn](e);
    }
  }

  /** $1A1CC / $1A1D8: the nearest opposing unit attached to this buffer and in range
   * (flag bit 9). Returns (object, Chebyshev distance) or (None, -1). */
  nearest_hostile(mantas_only: boolean = false): [number | null, number] {
    const m = this.mem;
    let first: number;
    let n: number;
    if (m.u8(0xa44)) {
      first = 20;
      n = 4;
    } else {
      first = 2;
      n = mantas_only ? 4 : 8;
    }
    const buf = m.u32(0xa28);
    const x0 = m.u32(0xa36);
    const z0 = m.u32(0xa3e);
    let best: number | null = null;
    let best_d = 0xffffffff;
    for (let k = 0; k < n; k++) {
      const o = this.obj(first + k);
      if (!(m.u8(o + 1) & 1) || !(m.u16(o) & 0x200) || m.u32(o + 0x3c) !== buf) continue;
      const dx = u32(Math.abs(s32(m.u32(o + 0x0a) - x0)));
      const dz = u32(Math.abs(s32(m.u32(o + 0x12) - z0)));
      const d = s32(dz) > s32(dx) ? dz : dx;
      if (d <= best_d) {
        best = o;
        best_d = d;
      }
    }
    return [best, best === null ? -1 : s32(best_d)];
  }

  /** $1A262: the target's offset from its buffer origin, or None if it has no buffer
   * or the offset doesn't fit in 16 bits. */
  target_local(t: number): number[] | null {
    const m = this.mem;
    const buf = m.u32(t + 0x3c);
    if (!buf) return null;
    const d: number[] = [];
    for (let k = 0; k < 3; k++) d.push(s32(m.u32(t + 0x0a + 4 * k) - m.u32(buf + 2 + 4 * k)));
    return d.every(fitsWord) ? d : null;
  }

  /** tst.b / subq.b / bne: True when the countdown runs out this pass. */
  _byte_timer(a: number): boolean {
    let v = this.mem.u8(a);
    if (v) {
      v = (v - 1) & 0xff;
      this.mem.set8(a, v);
    }
    return v === 0;
  }

  _word_timer(a: number): boolean {
    let v = this.mem.u16(a);
    if (v) {
      v = w16(v - 1);
      this.mem.set16(a, v);
    }
    return v === 0;
  }

  /** $199B4: track the nearest hostile in range and fire SAMs (type 28). */
  aa_turret(e: number): void {
    const m = this.mem;
    if (m.u8(0xa45)) return;
    if (this._byte_timer(e + 0x1b)) {
      m.set32(e + 0x1e, 0);
      m.set8(e + 0x1b, ((this.rand() & 7) + 8) & 0xff);
      const [t, d] = this.nearest_hostile();
      if (d >= 0) {
        const y = s32(m.u32(t! + 0x0e));
        if (d > 8000 || y > 2300 || y < 60) {
          m.set8(e + 0x1c, 0);
        } else {
          m.set32(e + 0x1e, t!);
        }
      }
    }
    const t = m.u32(e + 0x1e);
    if (!t) {
      m.set16(e + 6, m.u16(e + 6) + 0x100);
      return;
    }
    const loc = this.target_local(t);
    if (loc === null) return;
    const [pitch, heading] = this.aim_angles(
      loc[0] - m.s16(e + 0x0a), loc[1] - m.s16(e + 0x0c), loc[2] - m.s16(e + 0x0e));
    m.set16(e + 4, this.turn_towards(pitch, m.u16(e + 4), 0x200));
    m.set16(e + 6, this.turn_towards(heading, m.u16(e + 6), 0x800));
    if (Math.abs(s16(m.u16(e + 6) - heading)) > 0x200) return;
    if (!this._byte_timer(e + 0x1c)) return;
    m.set8(e + 0x1c, ((this.rand() & 0x1f) + 0x20) & 0xff);
    const a = this.find_free_island_object();
    if (a === null) return;
    this.create_sam_ahead(e, a);
    m.set8(a + 0x1b, 0x64);
    m.set32(a + 0x1e, t);
    m.set16(a + 0x22, 0x3c);
  }

  /** $19B44: at difficulty >= $4000, SAMs at low Mantas within 4000. */
  command_centre_defence(e: number): void {
    const m = this.mem;
    if (m.u8(0xa45)) return;
    if (m.s16(0x444) >= 0x4000 && m.u8(e + 0x1b) === 0) {
      m.set8(e + 0x1b, ((this.rand() & 0xf) + 0x10) & 0xff);
      const [t, d] = this.nearest_hostile(true);
      if (d >= 0 && d <= 4000 && s32(m.u32(t! + 0x0e)) <= 1500) {
        const a = this.find_free_island_object();
        if (a !== null) {
          this.init_island_object(a, 0x1c);
          this.reset_island_schedule();
          m.set32(a + 0x1e, t!);
          m.set16(a + 0x0a, m.u16(e + 0x0a));
          m.set16(a + 0x0c, 0x118);
          m.set16(a + 0x0e, m.u16(e + 0x0e));
          m.set16(a + 4, 0xc000);
          m.set8(a + 0x1b, 0x3c);
          m.set16(a + 0x22, 0x1e);
        }
      }
    }
    m.set8(e + 0x1b, m.u8(e + 0x1b) - 1);
  }

  /** $19F00: launch island drones (type 22) while hostiles are present. */
  drone_pad(e: number): void {
    const m = this.mem;
    if (!this._word_timer(e + 0x1e)) return;
    if (m.u8(0xa45) || !m.u8(0xa42)) return;
    const count = m.u32(0xa2c) + 0x10;
    if (m.u16(0x446) === m.u16(count)) return;
    const a = this.find_free_island_object();
    if (a === null) return;
    m.set16(count, m.u16(count) + 1);
    m.set16(e + 0x1e, 0x64);
    this.init_island_object(a, 0x16);
    this.reset_island_schedule();
    m.set16(a + 6, this.rand());
    m.set16(a + 4, 0xc000);
    m.set16(a + 0x26, m.u16(0x448));
    m.set16(a + 0x38, m.u16(0x44e));
    m.set16(a + 0x28, 0x3e8);
    const x = m.u16(e + 0x0a);
    const y = m.u16(e + 0x0c) + 0x15e;
    const z = m.u16(e + 0x0e);
    for (const [o, v] of [[0x0a, x], [0x0c, y], [0x0e, z], [0x1e, x], [0x20, y + 0x320], [0x22, z]]) {
      m.set16(a + o, v);
    }
    m.set32(a + 0x2a, e);
  }

  /** $19F8A: fire an anti-ship missile (type 34) at the opposing carrier. */
  antiship_site(e: number): void {
    const m = this.mem;
    if (!this._word_timer(e + 0x1e)) return;
    if (m.u8(0xa45)) return;
    m.set16(e + 0x1e, (this.rand() & 0x7f) + 0x80);
    const c = m.u8(0xa44) ? this.obj(19) : CARRIER;
    if (!(m.u8(c + 1) & 1) || m.u32(c + 0x3c) !== m.u32(0xa28)) return;
    const dx = s32(m.u32(c + 0x0a) - m.u32(0xa36));
    const dz = s32(m.u32(c + 0x12) - m.u32(0xa3e));
    const d = Math.max(Math.abs(dx), Math.abs(dz));
    if (d > m.u16(0x454)) return;
    const heading = this.atan2(dx, dz);
    m.set16(0xa80, heading);
    const a = this.find_free_object();
    if (a === null) return;
    this.init_world_object(a, 0x22);
    this.reset_pair_schedules();
    m.set16(a + 6, heading);
    m.set16(a + 4, 0xc000);
    m.set32(a + 0x0a, m.u32(0xa36));
    m.set32(a + 0x12, m.u32(0xa3e));
    m.set32(a + 0x0e, m.u32(0xa3a) + 0x64);
    m.set16(a + 0x1e, 0x190);
    m.set8(a + 0x1a, 0);
    m.set8(a + 0x1c, m.u8(0xa44));
  }

  /** Island behaviour handlers by address -> method name (UnitState.ISLAND_BEHAVIOURS). */
  static ISLAND_BEHAVIOURS: Record<number, string> = {
    0x199b4: "aa_turret", 0x19b44: "command_centre_defence",
    0x19f00: "drone_pad", 0x19f8a: "antiship_site",
    0x19862: "dish_spin", 0x19872: "dish_track",
    0x1989c: "volcano", 0x1996c: "volcano_rock",
    0x19ac4: "sam_launcher", 0x19bc6: "sam", 0x19c46: "island_drone",
  };

  // ---- AI objects -------------------------------------------------------------------
  /** $1A56A: objects 20 and up with a behaviour (bits 0 and 8) run their handler. */
  ai_objects_step(): void {
    const m = this.mem;
    const n = s16(m.u16(0xa48) - 20);
    if (n <= 0) return;
    m.set16(0x6f0, 20);
    for (let k = 0; k < n; k++) {
      const a = this.obj(20 + k);
      if ((m.u16(a) & 0x101) === 0x101) {
        const h = m.u32(AI_HANDLERS + 4 * m.u16(a + 2));
        if (h !== 0x1a69e && h !== 0x19860) {
          const fn = UnitCore.AI_BEHAVIOURS[h];
          if (fn === undefined) throw new NotPorted(`AI object handler $${hex5(h)} (type ${m.u16(a + 2)})`);
          (this as any)[fn](a);
        }
      }
      m.set16(0x6f0, 21 + k);
    }
  }

  // ---- object passes ----------------------------------------------------------------
  /** $1B746: island attach, island-object and object-pair collisions, frame counter. */
  object_passes(): void {
    this.island_attach_step();
    this.collide_island_objects();
    this.collide_objects();
    this.mem.set16(FRAME, (this.mem.u16(FRAME) + 1) & 0x7fff);
  }

  /** $1B762: every 24-31 passes each object re-checks which island it's near. */
  island_attach_step(): void {
    const m = this.mem;
    for (let k = 2; k < 64; k++) {
      const o = this.obj(k);
      if (!(m.u8(o + 1) & 1)) continue;
      const t = m.u16(o + 0x3a);
      if (t) {
        m.set16(o + 0x3a, t - 1);
        continue;
      }
      m.set16(o + 0x3a, (this.rand() & 7) + 0x18);
      const buf = m.u32(o + 0x3c);
      if (buf) {
        const dx = Math.abs(s32(m.u32(o + 0x0a) - m.u32(buf + 2)));
        const dz = Math.abs(s32(m.u32(o + 0x12) - m.u32(buf + 0x0a)));
        if (!(dx <= 0x1fbd0 && dz < 0x1fbd0)) {
          this.release_island_buffer(o);
          m.set8(o, m.u8(o) & ~2);
          m.set32(o + 0x3c, 0);
        }
        continue;
      }
      for (let i = 0; i < 64; i++) {
        const isl = ISLAND_TABLE + ISLAND_SIZE * i;
        if (Math.abs(s32(m.u32(o + 0x0a) - m.u32(isl + 2))) > 0x1fbd0) continue;
        if (!(Math.abs(s32(m.u32(o + 0x12) - m.u32(isl + 6))) < 0x1fbd0)) continue;
        if (m.u8(isl)) {
          const b = ISLAND_BUFFERS[m.u8(isl + 1) ? 1 : 0];
          if (m.u8(b + 1) !== i) throw new NotPorted("trap #5: island buffer mismatch");
          m.set32(o + 0x3c, b);
        } else if (o === CARRIER || m.u16(o + 2) === 5 || m.u16(o + 2) === 6) {
          let n: number;
          if (!(m.u8(ISLAND_BUFFERS[0]) & 1)) {
            n = 0;
          } else if (!(m.u8(ISLAND_BUFFERS[1]) & 1)) {
            n = 1;
          } else {
            throw new NotPorted("trap #6: no free island buffer");
          }
          m.set8(isl + 1, n);
          m.set32(o + 0x3c, ISLAND_BUFFERS[n]);
          this.load_island_buffer(ISLAND_BUFFERS[n], i);
        }
        break;
      }
    }
  }

  /** $182F6: when no other player unit uses the object's buffer, detach everything from
   * it and free it. */
  release_island_buffer(o: number): void {
    const m = this.mem;
    const buf = m.u32(o + 0x3c);
    if (!buf) return;
    for (let k = 2; k < 11; k++) {
      const u = this.obj(k);
      if (m.u8(u + 1) & 1 && u !== o && m.u32(u + 0x3c) === buf) return;
    }
    for (let k = 2; k < 64; k++) {
      const u = this.obj(k);
      if (m.u32(u + 0x3c) === buf) m.set32(u + 0x3c, 0);
    }
    m.set8(buf, 0);
    const isl = ISLAND_TABLE + ISLAND_SIZE * m.u8(buf + 1);
    m.set8(isl, 0);
    m.set16(isl + 0x10, 0);
  }

  /** $17FB4: build the 3D buffer of island i from its structure slots. */
  load_island_buffer(buf: number, i: number): void {
    const m = this.mem;
    const isl = ISLAND_TABLE + ISLAND_SIZE * i;
    m.set8(isl, 0xff);
    m.set8(buf, 1);
    m.set8(buf + 1, i);
    m.set32(buf + 2, m.u32(isl + 2));
    m.set32(buf + 6, 0);
    m.set32(buf + 0x0a, m.u32(isl + 6));
    m.set16(buf + 0x0e, 0x40);
    m.set16(0x74e, 0);
    m.buf.fill(0, buf + 0x12, buf + 0x12 + 0x1000);
    let e = buf + 0x12;
    m.set16(e, 3);
    m.set16(e + 2, 8);
    m.set16(e + 0x0c, 0x28);
    m.set16(e + 0x1e, m.u16(isl + 0x0a));
    m.set16(e + 0x20, m.u16(isl + 0x0c));
    for (let j = 0; j < 32; j++) {
      const s = isl + SLOT_BASE + SLOT_SIZE * j;
      const t = m.u16(s);
      const h = m.u32(SLOT_ENTRY + 8 * t);
      const y = w16(m.u32(SLOT_ENTRY + 8 * t + 4) + 0x50);
      if (h >= 0x80000000) {
        this._spawn_entry(e, s, w16(-h), y);
      } else if (h === 0x18236 || h === 0x18248) {
        this._spawn_entry(e, s, h === 0x18236 ? 0x24 : 0x2f, y);
        m.set16(0x74e, m.u16(0x74e) + 1);
      } else if (h) {
        throw new NotPorted(`slot entry handler $${hex5(h)}`);
      }
      e += 0x40;
    }
    m.set16(buf + 0x10, m.u16(0x74e));
  }

  /** $1825A + the slot value: an island entry for a structure slot. */
  _spawn_entry(e: number, s: number, t: number, y: number): void {
    const m = this.mem;
    this.init_island_object(e, t);
    m.set16(e + 0x0a, m.u16(s + 8));
    m.set16(e + 0x0c, y);
    m.set16(e + 0x0e, m.u16(s + 0x0a));
    m.set16(e + 6, m.u16(s + 2));
    m.set16(e + 0x38, m.u16(s + 4));
  }

  /** neg.w if negative: |v| as a signed word ($8000 stays negative). */
  static absw(v: number): number {
    v = s16(v);
    return v < 0 ? s16(-v) : v;
  }

  absw(v: number): number {
    return UnitCore.absw(v);
  }

  static absl(v: number): number {
    v = s32(v);
    return v < 0 ? s32(-v) : v;
  }

  absl(v: number): number {
    return UnitCore.absl(v);
  }

  /** $1B90E: objects (2-63, flags $601) against the entries of their island buffer.
   * Pairs are only tested on their scheduled pass. */
  collide_island_objects(): void {
    const m = this.mem;
    const f = m.u16(FRAME);
    const nxt = (f + 1) & 0x7fff;
    for (let k = 2; k < 64; k++) {
      const o = this.obj(k);
      if ((m.u16(o) & 0x601) !== 0x601) continue;
      const buf = m.u32(o + 0x3c);
      if (!buf) continue;
      m.set32(0xa9c, buf);
      m.set32(0xaa0, ISLAND_TABLE + ISLAND_SIZE * m.u8(buf + 1));
      const dx = s32(m.u32(o + 0x0a) - m.u32(buf + 2));
      if (!fitsWord(dx)) {
        m.set8(o, m.u8(o) & ~2);
        continue;
      }
      for (let j = 0; j < 3; j++) m.set32(0xa4a + 4 * j, m.u32(buf + 2 + 4 * j));
      const dz = s32(m.u32(o + 0x12) - m.u32(buf + 0x0a));
      const dy = s32(m.u32(o + 0x0e) - m.u32(buf + 6));
      if (!(fitsWord(dz) && fitsWord(dy))) {
        m.set8(o, m.u8(o) & ~2);
        continue;
      }
      m.set16(0xaaa, dx);
      m.set16(0xaac, dy);
      m.set16(0xaae, dz);
      for (let j = 0; j < 64; j++) {
        const sa = o + 0x40 + 2 * j;
        if (m.u16(sa) !== f) continue;
        m.set16(0xaa4, j);
        const e = buf + 0x12 + 0x40 * j;
        if ((m.u16(e) & 0x401) !== 0x401) {
          m.set8(sa, 0xff);
          continue;
        }
        const ex = s16(dx - m.u16(e + 0x0a));
        const ey = s16(dy - m.u16(e + 0x0c));
        const ez = s16(dz - m.u16(e + 0x0e));
        m.set16(0xab0, ex);
        m.set16(0xab4, ey);
        m.set16(0xab2, ez);
        const ax = this.absw(ex);
        const ay = this.absw(ey);
        const az = this.absw(ez);
        let d = ax;
        if (ay >= d) d = ay;
        if (az >= d) d = az;
        d = w16(d);
        let near = false;
        for (const side of [o, e]) {
          if (m.u8(side) & 8) {
            if (d < 1000) {
              near = true;
              break;
            }
            d = w16(d - 1000);
          }
        }
        if (!near && s16(d) > 1500) {
          const speed = w16(m.u16(o + 0x36) + m.u16(e + 0x36));
          if (speed === 0) {
            m.set8(sa, 0xff);
            continue;
          }
          let q = Math.floor(u32(s16(w16(d - 750))) / speed);
          q = q <= 0xffff ? w16(q) : w16(d - 750);
          m.set16(sa, ((q || 1) + f) & 0x7fff);
          continue;
        }
        m.set16(sa, nxt);
        if (ay > s16(m.u16(e + 0x32) + m.u16(o + 0x32))) continue;
        let re = m.s16(e + 0x30);
        if (re < 0) {
          const t = m.u16(e + 2);
          if (t === 7) {
            re = s16((s16(700 - ey) >> 1) + 300);
          } else if (t === 0x25 || t === 0x30) {
            re = s16(750 - ey);
          } else {
            continue;
          }
        }
        if (m.s16(o + 0x30) < 0) {
          const angle = w16(-m.u16(o + 6));
          const [hx, hz] = this.rotate_yaw(w16(-ex), w16(-ez), angle);
          m.set16(0xab2, hz);
          // Quirk: the rotation leaves cos(angle) in d3, so the along-hull limit is
          // cos + 1000 instead of radius + 1000 (up to 17384 for a carrier).
          if (this.absw(hx) > s16(re + 400) || this.absw(hz) > s16(this.sin_cos(angle)[1] + 1000)) continue;
        } else {
          const mx = ax >= az ? ax : az;
          if (mx > s16(m.u16(o + 0x30) + re)) continue;
        }
        this.collision_response(o, e);
        this.collision_response(e, o);
      }
    }
  }

  /** $1BB40: world-object pairs (j < i), each tested on its scheduled pass. */
  collide_objects(): void {
    const m = this.mem;
    const f = m.u16(FRAME);
    const nxt = (f + 1) & 0x7fff;
    for (let i = 1; i < 64; i++) {
      const o = this.obj(i);
      if ((m.u16(o) & 0x401) !== 0x401) continue;
      for (let j = 0; j < i; j++) {
        const sa = o + 0xc0 + 2 * j;
        if (m.u16(sa) !== f) continue;
        const p = this.obj(j);
        if ((m.u16(p) & 0x401) !== 0x401) {
          m.set8(sa, 0xff);
          continue;
        }
        const dx = s32(m.u32(o + 0x0a) - m.u32(p + 0x0a));
        const dy = s32(m.u32(o + 0x0e) - m.u32(p + 0x0e));
        const dz = s32(m.u32(o + 0x12) - m.u32(p + 0x12));
        m.set16(0xab0, dx);
        m.set16(0xab2, dz);
        // The original negates x and z on the sign of their LOW WORD (the flags come
        // from the move.w to $AB0/$AB2), so e.g. -40000 ($FFFF63C0) stays negative.
        const ax = dx & 0x8000 ? s32(-dx) : dx;
        const az = dz & 0x8000 ? s32(-dz) : dz;
        const ay = this.absl(dy);
        let d = ax;
        if (ay >= d) d = ay;
        if (az >= d) d = az;
        d = u32(d);
        let near = false;
        for (const side of [o, p]) {
          if (m.u8(side) & 8) {
            if (d < 1000) {
              near = true;
              break;
            }
            d = u32(d - 1000);
          }
        }
        if (!near && s32(d) > 1500) {
          const speed = s16(m.u16(o + 0x36) + m.u16(p + 0x36));
          if (speed === 0) {
            m.set8(sa, 0xff);
            continue;
          }
          const qq = Math.trunc(s32(d - 900) / speed);
          const q = !fitsWord(qq) ? 5000 : (w16(qq) || 1);
          m.set16(sa, (q + f) & 0x7fff);
          continue;
        }
        m.set16(sa, nxt);
        if (s16(ay) > s16(m.u16(p + 0x32) + m.u16(o + 0x32))) continue;
        const rp = m.s16(p + 0x30);
        const ro = m.s16(o + 0x30);
        let r: number | null;
        let hx = 0;
        let hz = 0;
        if (ro < 0) {
          if (m.u8(p + 1) & 4) continue;
          this.set_matrix(w16(-m.u16(o + 6)));
          [hx, hz] = this.matrix_rotate(m.u16(0xab0), m.u16(0xab2));
          r = rp;
        } else if (rp < 0) {
          if (m.u8(o + 1) & 4) continue;
          [hx, hz] = this.rotate_yaw(m.u16(0xab0), m.u16(0xab2), w16(-m.u16(p + 6)));
          m.set16(0xab2, hz);
          r = ro;
        } else {
          const mx = s16(ax) >= s16(az) ? s16(ax) : s16(az);
          if (mx > s16(ro + rp)) continue;
          r = null;
        }
        if (r !== null && (this.absw(hx) > s16(r + 400) || this.absw(hz) > s16(r + 1000))) continue;
        this.collision_response(o, p);
        this.collision_response(p, o);
      }
    }
  }

  // ---- per second, per 10 seconds -----------------------------------------------------
  /** $1B8AE: set bit 9 once an attached object's offset fits in 16 bits, and test it
   * against every island entry next pass. */
  island_range_check(): void {
    const m = this.mem;
    for (let k = 2; k < 64; k++) {
      const o = this.obj(k);
      const f = m.u16(o);
      if ((f & 0x601) !== 0x401) continue;
      const buf = m.u32(o + 0x3c);
      if (!buf) continue;
      const dx = s32(m.u32(o + 0x0a) - m.u32(buf + 2));
      const dz = s32(m.u32(o + 0x12) - m.u32(buf + 0x0a));
      if (fitsWord(dx) && fitsWord(dz)) {
        m.set8(o, m.u8(o) | 2);
        const fr = m.u16(FRAME);
        for (let j = 0; j < 64; j++) m.set16(o + 0x40 + 2 * j, fr);
      }
    }
  }

  manta_hangar_step(): void {
    this._hangar(0x496, 0x16, "Manta");
  }

  walrus_hangar_step(): void {
    this._hangar(0x570, 0x0e, "Walrus");
  }

  /** $1761C / $176AC: preparation countdown, then shared repair of bays under repair. */
  _hangar(records: number, timer: number, kind: string): void {
    const m = this.mem;
    for (let k = 0; k < 4; k++) {
      const r = records + 0x34 * k;
      if (m.u8(r + 1) === 2) {
        m.set16(r + timer, m.u16(r + timer) - 1);
        if (m.u16(r + timer) === 0) m.set8(r + 1, 0);
      }
    }
    let n = 0;
    for (let k = 0; k < 4; k++) if (m.u8(records + 0x34 * k + 1) === 1) n += 1;
    if (n) {
      const step = pyDiv(400, n);
      for (let k = 0; k < 4; k++) {
        const r = records + 0x34 * k;
        if (m.u8(r + 1) !== 1) continue;
        const h = w16(m.u16(r + 0x14) + step);
        m.set16(r + 0x14, h);
        if (h >= 0xfa0) {
          m.set16(r + 0x14, 0xfa0);
          m.set8(r + 1, 2);
          this.preparation_time(r, kind);
        }
      }
    }
    m.set16(0x95c, 2); // redraw countdowns (units_render): bay status,
    m.set16(kind === "Manta" ? 0x95e : 0x960, 2); // the arming panels
  }

  /** $11856 (Manta) / $11F6A (Walrus): flight preparation in 10 s units: damage left
   * to repair, plus one per loadout slot in use (Manta slot 3 and Walrus slot 2 cost
   * more). */
  preparation_time(r: number, kind: string): void {
    const m = this.mem;
    let d = divu(u32(s16(0xfa0 - m.u16(r + 0x14)) * 16), 0xfa0) & 0xffff;
    if (kind === "Manta") {
      d += (m.u16(r + 0x0c) ? 1 : 0) + (m.u16(r + 0x0e) ? 1 : 0) + (m.u16(r + 0x10) ? 2 : 0);
      m.set16(r + 0x16, d + 1);
    } else {
      d += (m.u16(r + 0x26) ? 1 : 0) + (m.u16(r + 0x28) ? 3 : 0);
      m.set16(r + 0x0e, d + 1);
    }
  }

  /** $17578: each carrier system's status word from (current, max). */
  carrier_systems_status(): void {
    const m = this.mem;
    for (let k = 0; k < 10; k++) {
      const a = SYSTEMS + 8 * k;
      const cur = m.u16(a);
      const mx = m.u16(a + 2);
      if (k === 0 || k === 4) {
        if (cur === mx) {
          m.set16(a + 6, 0x100);
        } else {
          const half = mx >> 1;
          if (!half) throw new NotPorted("divide by zero");
          let v = Math.floor((cur << 8) / half);
          v = v <= 0xffff ? w16(v) : w16(cur << 8);
          v = s16(v - 0x100);
          m.set16(a + 6, v < 0 ? 0 : v);
        }
      } else if (k === 8 || k === 9) {
        if (!mx) throw new NotPorted("divide by zero");
        const v = Math.floor((cur << 8) / mx);
        m.set16(a + 6, v <= 0xffff ? w16(v) : w16(cur << 8));
      } else {
        m.set8(a + 6, cur > mx >> 1 ? 0xff : 0);
      }
    }
  }

  // ---- island step: 3D buffer mirroring ---------------------------------------------
  _slot_index(s: Slot): number {
    const isl = ISLAND_TABLE + ISLAND_SIZE * this.island_index;
    return pyDiv(s.a - isl - SLOT_BASE, SLOT_SIZE);
  }

  /** $18276: copy a changed slot to its entry in the loaded island's buffer. */
  slot_mirror(s: Slot): void {
    const m = this.mem;
    const isl = ISLAND_TABLE + ISLAND_SIZE * this.island_index;
    if (!m.u8(isl)) return;
    const e = m.u32(G_BUFFER_PTR) + 0x12 + 0x40 * this._slot_index(s);
    if (s.type) {
      m.set16(e + 2, s.type);
      m.set16(e + 0x38, s.value);
      m.set16(e + 0x0a, s.x);
      m.set16(e + 0x0e, s.z);
    } else {
      m.set16(e, 0);
    }
  }

  /** $182A4: a new construction's entry in the loaded island's buffer. */
  slot_create(j: number): void {
    const m = this.mem;
    const isl = ISLAND_TABLE + ISLAND_SIZE * this.island_index;
    if (!m.u8(isl)) return;
    m.set16(NEW_INDEX, j);
    const e = m.u32(G_BUFFER_PTR) + 0x12 + 0x40 * j;
    const s = isl + SLOT_BASE + SLOT_SIZE * j;
    const t = m.u16(s);
    this.init_island_object(e, t);
    this.reset_island_schedule();
    m.set16(e + 0x38, m.u16(s + 4));
    m.set16(e + 0x0a, m.u16(s + 8));
    m.set16(e + 0x0e, m.u16(s + 0x0a));
    m.set16(e + 0x0c, w16(m.u32(SLOT_ENTRY + 8 * t + 4) + 0x50));
  }

  /** $18B50 (find_free_island_spot, slot k already cleared): on a loaded island the
   * tree's buffer entry ([$8CC] + $12 + $40k) throws its debris ($1C6D6, origin = the
   * buffer's, copied to $A4A) and is removed. */
  tree_cut(isl: Island, k: number): void {
    const m = this.mem;
    if (!m.u8(isl.a)) return;
    const buf = m.u32(G_BUFFER_PTR);
    for (let j = 0; j < 3; j++) m.set32(0xa4a + 4 * j, m.u32(buf + 2 + 4 * j));
    const e = buf + 0x12 + 0x40 * k;
    this.spawn_debris_entry(e);
    m.set16(e, 0);
  }

  // ---- shared helpers for island AI ---------------------------------------------------
  /** $1A164: (dx, dy, dz) for a speed along the object's pitch and heading. */
  velocity_from_angles(a: number, speed: number): [number, number, number] {
    const m = this.mem;
    const [y, h] = this.rotate_pitch(0, speed, m.u16(a + 4));
    const [x, z] = this.rotate_yaw(0, h, m.u16(a + 6));
    return [x, y, z];
  }

  /** $1A062: aim from island entry e at a local point (longs); pitch/heading to
   * +$3A/+$3C. Returns (Chebyshev distance as a word, heading). */
  offset_aim(e: number, dx: number, dy: number, dz: number): [number, number] {
    const m = this.mem;
    const d = [dx, dy, dz].map((v, k) => s32(v - m.s16(e + 0x0a + 2 * k)));
    const [pitch, heading] = this.aim_angles(d[0], d[1], d[2]);
    m.set16(e + 0x3a, pitch);
    m.set16(e + 0x3c, heading);
    const a = d.map((v) => this.absw(v));
    let dist = a[0];
    if (a[1] >= dist) dist = a[1];
    if (a[2] >= dist) dist = a[2];
    return [dist, heading];
  }

  /** $19A96: a SAM (type 28) 100 ahead of the launcher, same angles. */
  create_sam_ahead(e: number, a: number): void {
    const m = this.mem;
    this.init_island_object(a, 0x1c);
    this.reset_island_schedule();
    m.set16(a + 0x0c, m.u16(e + 0x0c));
    const [vx, vz] = this.yaw_vector(e, 0x64);
    m.set16(a + 0x0a, m.u16(e + 0x0a) + vx);
    m.set16(a + 0x0e, m.u16(e + 0x0e) + vz);
    for (const o of [4, 6, 8]) m.set16(a + o, m.u16(e + o));
  }

  _move_local(e: number, speed: number): void {
    const [dx, dy, dz] = this.velocity_from_angles(e, speed);
    const m = this.mem;
    m.set16(e + 0x0a, m.u16(e + 0x0a) + dx);
    m.set16(e + 0x0c, m.u16(e + 0x0c) + dy);
    m.set16(e + 0x0e, m.u16(e + 0x0e) + dz);
  }

  // ---- island behaviours (continued) ----------------------------------------------------
  /** $19862: type 14 spins. */
  dish_spin(e: number): void {
    if (!this.mem.u8(0xa45)) this.mem.set16(e + 6, this.mem.u16(e + 6) + 1000);
  }

  /** $19872: type 15 faces Manta 0 (object 2). */
  dish_track(e: number): void {
    const m = this.mem;
    if (m.u8(0xa45)) return;
    const o = this.obj(2);
    m.set16(e + 6, this.atan2(s32(m.u32(o + 0x0a) - m.u32(0xa36)), s32(m.u32(o + 0x12) - m.u32(0xa3e))));
  }

  /** $1989C: dormant (2) / erupting (1) cycles; erupting throws rocks (type 9). */
  volcano(e: number): void {
    const m = this.mem;
    const state = m.u8(e + 0x1a);
    if (state === 1) {
      if (this._word_timer(e + 0x1e)) return this._volcano_dormant(e);
      if (this._dec_byte(e + 0x1b)) return;
      const a = this.find_free_island_object();
      if (a !== null) {
        this.init_island_object(a, 9);
        this.reset_island_schedule();
        for (const o of [0x0a, 0x0c, 0x0e]) m.set16(a + o, m.u16(e + o));
        m.set16(a + 0x0c, m.u16(a + 0x0c) + 600);
        for (const o of [4, 6, 8]) m.set16(a + o, this.rand());
        const angle = this.rand();
        const [vx, vz] = this.rotate_yaw((this.rand() & 0xf) + 0x1e, 0, angle);
        m.set16(a + 0x20, vx);
        m.set16(a + 0x22, vz);
        m.set16(a + 0x1e, (this.rand() & 0x7ff) + 6000);
      }
      m.set8(e + 0x1b, (this.rand() & 0xf) + 0x10);
    } else if (state === 0) {
      this._volcano_dormant(e);
    } else {
      if (this._word_timer(e + 0x1e)) {
        m.set8(e + 0x1a, 1);
        m.set16(e + 0x1e, (this.rand() & 0x7ff) + 0x800);
      }
    }
  }

  _volcano_dormant(e: number): void {
    const m = this.mem;
    m.set8(e + 0x1a, 2);
    m.set16(e + 0x1e, (this.rand() & 0x7ff) + 0x800);
    m.set8(e + 0x1b, (this.rand() & 0xf) + 0x10);
  }

  /** $1996C: tumbling ballistic rock; gone below local height 100. */
  volcano_rock(e: number): void {
    const m = this.mem;
    for (const [o, d] of [[4, 0x78a], [6, 0x3b1], [8, 0xfdef]]) m.set16(e + o, m.u16(e + o) + d);
    const vy = s16(m.u16(e + 0x1e) - 0x10a);
    m.set16(e + 0x1e, vy);
    m.set16(e + 0x0c, m.u16(e + 0x0c) + (vy >> 6));
    if (m.s16(e + 0x0c) < 100) {
      m.set8(e + 1, m.u8(e + 1) & 0xfe);
      return;
    }
    m.set16(e + 0x0a, m.u16(e + 0x0a) + m.u16(e + 0x20));
    m.set16(e + 0x0e, m.u16(e + 0x0e) + m.u16(e + 0x22));
  }

  /** $19AC4: vertical SAM launch at Mantas (or enemy fighters) within 7000. */
  sam_launcher(e: number): void {
    const m = this.mem;
    if (m.u8(0xa45)) return;
    if (m.u8(e + 0x1b) === 0) {
      m.set8(e + 0x1b, ((this.rand() & 0x1f) + 0x40) & 0xff);
      const [t, d] = this.nearest_hostile(true);
      if (d >= 0 && d <= 7000) {
        const y = s32(m.u32(t! + 0x0e));
        if (250 <= y && y <= 2300) {
          const a = this.find_free_island_object();
          if (a !== null) {
            this.init_island_object(a, 0x1c);
            this.reset_island_schedule();
            m.set32(a + 0x1e, t!);
            m.set16(a + 0x0a, m.u16(e + 0x0a));
            m.set16(a + 0x0c, 0xb4);
            m.set16(a + 0x0e, m.u16(e + 0x0e));
            m.set16(a + 4, 0xc000);
            m.set8(a + 0x1b, 0x3c);
            m.set16(a + 0x22, 0x1e);
          }
        }
      }
    }
    m.set8(e + 0x1b, m.u8(e + 0x1b) - 1);
  }

  /** $19BC6: SAM: life +$1B; re-aims every 4 passes; turns at [$452], accelerates by 5
   * up to [$450]. */
  sam(e: number): void {
    const m = this.mem;
    const life = (m.u8(e + 0x1b) - 1) & 0xff;
    m.set8(e + 0x1b, life);
    if (life === 0) m.set16(e, 0);
    if (m.u8(e + 0x1c) === 0) {
      m.set8(e + 0x1c, 4);
      const loc = this.target_local(m.u32(e + 0x1e));
      if (loc !== null) {
        const [pitch, heading] = this.aim_angles(
          loc[0] - m.s16(e + 0x0a), loc[1] - m.s16(e + 0x0c), loc[2] - m.s16(e + 0x0e));
        m.set16(e + 0x3a, pitch);
        m.set16(e + 0x3c, heading);
      }
    }
    m.set16(e + 4, this.turn_towards(m.u16(e + 0x3a), m.u16(e + 4), m.u16(0x452)));
    m.set16(e + 6, this.turn_towards(m.u16(e + 0x3c), m.u16(e + 6), m.u16(0x452)));
    let v = s16(m.u16(e + 0x22) + 5);
    if (v > m.s16(0x450)) v = m.s16(0x450);
    m.set16(e + 0x22, v);
    this._move_local(e, v);
    m.set8(e + 0x1c, m.u8(e + 0x1c) - 1);
  }

  /** $19C46: island defence drone. +$1A state (0 init, 1 fly to waypoint, 2 search,
   * 3 attack, 4+ land), +$1D state after the waypoint, +$28 fuel, +$26 missiles,
   * +$38 health, +$2A launch pad. */
  island_drone(e: number): void {
    const m = this.mem;
    const st = m.u8(e + 0x1a);
    if (st === 0) {
      m.set8(e + 0x1a, 1);
      m.set8(e + 0x1b, 0xff);
      m.set8(e + 0x1d, 2);
      return;
    }
    if (st === 1) return this._drone_fly(e);
    if (st === 2) {
      if (!m.u8(0xa45)) {
        const [t, d] = this.nearest_hostile();
        if (d >= 0 && d <= 25000) {
          m.set32(e + 0x1e, t!);
          m.set8(e + 0x1a, 3);
          return this._drone_steer(e, false);
        }
      }
      m.set8(e + 0x1b, 0x14);
      return this._drone_wander(e);
    }
    if (st === 3) {
      const t = m.u32(e + 0x1e);
      if (!(m.u16(t) & 1)) {
        m.set8(e + 0x1a, 2);
        return this._drone_steer(e, false);
      }
      const loc = this.target_local(t);
      if (loc !== null) {
        const [dist, heading] = this.offset_aim(e, loc[0], loc[1], loc[2]);
        if (m.u16(e + 0x26)) {
          if (s16(dist) > 5000) return this._drone_steer(e, false);
          if (this.absw(heading - m.u16(e + 6)) <= 0x100) {
            const a = this.find_free_island_object();
            if (a !== null) {
              this.create_sam_ahead(e, a);
              m.set8(a + 0x1b, 0x64);
              m.set32(a + 0x1e, m.u32(e + 0x1e));
              m.set16(a + 0x22, 0xc8);
              m.set16(e + 0x26, m.u16(e + 0x26) - 1);
            }
          }
        }
      }
      m.set8(e + 0x1b, 0x64);
      return this._drone_wander(e);
    }
    const pad = m.u32(e + 0x2a);
    if (!this._pad_ok(pad)) return this._drone_lost_pad(e);
    m.set16(e + 4, this.turn_towards(0x4000, m.u16(e + 4), 0x1000));
    if (m.s16(e + 0x0c) > 0x2da) return this._island_drone_move(e);
    m.set8(e + 1, m.u8(e + 1) & 0xfe);
    const isl = m.u32(0xa2c);
    m.set16(isl + 0x10, m.u16(isl + 0x10) - 1);
  }

  _pad_ok(pad: number): boolean {
    const m = this.mem;
    return pad < 0x80000000 && (m.u8(pad + 1) & 1) !== 0 && m.u16(pad + 2) === 0x25;
  }

  _drone_fly(e: number): void {
    const m = this.mem;
    let fuel = m.u16(e + 0x28);
    if (fuel === 0 || fuel === 1) {
      m.set16(e, 0);
      const isl = m.u32(0xa2c);
      m.set16(isl + 0x10, m.u16(isl + 0x10) - 1);
      return;
    }
    fuel -= 1;
    m.set16(e + 0x28, fuel);
    if (m.u8(e + 0x1d) !== 4 && !(m.u8(e + 0x2a) & 0x80)) {
      if (m.u8(0xa45) || s16(fuel) <= 0xc8 || m.s16(e + 0x38) <= 0x1f4 || m.u16(e + 0x26) === 0) {
        return this._drone_go_home(e);
      }
    }
    const [dist] = this.offset_aim(e, m.s16(e + 0x1e), m.s16(e + 0x20), m.s16(e + 0x22));
    const left = (m.u8(e + 0x1b) - 1) & 0xff;
    m.set8(e + 0x1b, left);
    if (left === 0 || s16(dist) <= 0xf0) m.set8(e + 0x1a, m.u8(e + 0x1d));
    m.set8(e + 0x1c, s16(dist) < 0x1f4 ? 0xff : 0);
    this._drone_steer(e, true);
  }

  /** $19E64: head for the pad (and process the fly state again this pass). */
  _drone_go_home(e: number): void {
    const m = this.mem;
    const pad = m.u32(e + 0x2a);
    if (!this._pad_ok(pad)) return this._drone_lost_pad(e);
    const x = m.u16(pad + 0x0a);
    const y = m.u16(pad + 0x0c) + 0x320;
    const z = m.u16(pad + 0x0e);
    m.set16(e + 0x1e, x);
    m.set16(e + 0x20, y);
    m.set16(e + 0x22, z);
    m.set8(e + 0x1a, 1);
    m.set8(e + 0x1b, 0xff);
    m.set8(e + 0x1d, 4);
    this._drone_fly(e);
  }

  _drone_lost_pad(e: number): void {
    const m = this.mem;
    m.set8(e + 0x2a, 0xff);
    m.set8(e + 0x1a, 2);
    this._drone_steer(e, false);
  }

  /** $19D10: a random waypoint near the drone, then fly there and search again. */
  _drone_wander(e: number): void {
    const m = this.mem;
    const around = (v: number, rng: number, sub: number, lo: number, hi: number): number => {
      v = s16(v + ((this.rand() & rng) - sub));
      return v > hi ? hi : v < lo ? lo : v;
    };
    m.set16(e + 0x1e, around(m.u16(e + 0x0a), 0xfff, 0x800, -24000, 24000));
    m.set16(e + 0x22, around(m.u16(e + 0x0e), 0xfff, 0x800, -24000, 24000));
    m.set16(e + 0x20, around(m.u16(e + 0x0c), 0x7ff, 0x400, 200, 2500));
    m.set8(e + 0x1a, 1);
    m.set8(e + 0x1d, 2);
    this._drone_steer(e, false);
  }

  /** $19EB2/$19EB6: turn towards +$3A/+$3C at [$44C], then move. */
  _drone_steer(e: number, keep_slow: boolean): void {
    const m = this.mem;
    if (!keep_slow) m.set8(e + 0x1c, 0);
    m.set16(e + 4, this.turn_towards(m.u16(e + 0x3a), m.u16(e + 4), m.u16(0x44c)));
    m.set16(e + 6, this.turn_towards(m.u16(e + 0x3c), m.u16(e + 6), m.u16(0x44c)));
    this._island_drone_move(e);
  }

  /** $19EE2: speed [$44A], halved when close (+$1C). */
  _island_drone_move(e: number): void {
    const m = this.mem;
    let speed = m.u16(0x44a);
    if (m.u8(e + 0x1c)) speed >>= 1;
    this._move_local(e, speed);
  }

  // ---- AI objects -------------------------------------------------------------------
  /** $1AABA: climbs to 800, then skims at 75 once clear of its island, homing on the
   * player carrier or deck drones (or the enemy carrier when +$1C is set). */
  antiship_missile(a: number): void {
    const m = this.mem;
    if (this._word_timer(a + 0x1e)) return this.explode_world(a, 1);
    const alt = m.u8(a + 0x1a) ? 0x4b : 0x320;
    const want = w16((m.u16(a + 0x10) - alt) * 8);
    m.set16(a + 4, this.turn_towards(want, m.u16(a + 4), 0x400));
    if (!m.u8(a + 0x1a)) {
      const buf = m.u32(a + 0x3c);
      if (!buf) return this._missile_fly(a);
      const [dx, dz] = this.island_box_distance(a, buf);
      if (dx < 0 && dz < 0) return this._missile_fly(a);
      m.set8(a + 0x1a, 1);
      m.set8(a + 0x1b, 1);
      if (!m.u8(a + 0x1c)) {
        this.events.push(["message", "Missile heading for Carrier!", this._ping(true, true)]);
        this._sfx(12);
      }
      return this._missile_turn(a);
    }
    const left = (m.u8(a + 0x1b) - 1) & 0xff;
    m.set8(a + 0x1b, left);
    if (left) return this._missile_turn(a);
    m.set8(a + 0x1b, 0x14);
    const x0 = m.u32(a + 0x0a);
    const z0 = m.u32(a + 0x12);
    let t = this.obj(19);
    if (!m.u8(a + 0x1c)) {
      t = CARRIER;
      let best_d = 0xffffffff;
      for (let k = 10; k < 15; k++) {
        const o = this.obj(k);
        if (!(m.u8(o + 1) & 1)) continue;
        const dx = u32(this.absl(m.u32(o + 0x0a) - x0));
        let dz = u32(this.absl(m.u32(o + 0x12) - z0));
        if (s16(dz) < s16(dx)) {
          dz = ((dz & 0xffff0000) | (dx & 0xffff)) >>> 0; // cmp.w / move.w quirk
        }
        if (dz <= best_d) {
          best_d = dz;
          t = o;
        }
      }
    }
    m.set16(a + 0x20, this.atan2(s32(m.u32(t + 0x0a) - x0), s32(m.u32(t + 0x12) - z0)));
    this._missile_turn(a);
  }

  _missile_turn(a: number): void {
    const m = this.mem;
    m.set16(a + 6, this.turn_towards(m.u16(a + 0x20), m.u16(a + 6), 0x400));
    this._missile_fly(a);
  }

  _missile_fly(a: number): void {
    const m = this.mem;
    const [dx, dy, dz] = this.velocity_from_angles(a, 0xb4);
    for (const [o, d] of [[0x0a, dx], [0x0e, dy], [0x12, dz]]) m.set32(a + o, m.u32(a + o) + d);
  }

  /** $1A9A6: the explosion volume lasts +$1B passes. */
  blast(a: number): void {
    const m = this.mem;
    const v = (m.u8(a + 0x1b) - 1) & 0xff;
    m.set8(a + 0x1b, v);
    if (v === 0) m.set16(a, 0);
  }

  /** Laser/homing-missile motion: x, z always; y too, but a nose-down object that
   * would go below 0 dies (it still moves). */
  _fly_with_floor(a: number, speed: number): void {
    const m = this.mem;
    const [dx, dy, dz] = this.velocity_from_angles(a, speed);
    m.set32(a + 0x0a, m.u32(a + 0x0a) + dx);
    m.set32(a + 0x12, m.u32(a + 0x12) + dz);
    const y = s32(m.u32(a + 0x0e) + dy);
    if (y < 0 && m.s16(a + 4) > 0) m.set16(a, 0);
    m.set32(a + 0x0e, y);
  }

  /** subq.b #1 (no zero test first: 0 wraps to $FF). */
  _dec_byte(a: number): number {
    const v = (this.mem.u8(a) - 1) & 0xff;
    this.mem.set8(a, v);
    return v;
  }

  /** $1A6A0: straight line at 350 per pass for +$1B passes. */
  laser_bolt(a: number): void {
    const m = this.mem;
    const life = (m.u8(a + 0x1b) - 1) & 0xff;
    m.set8(a + 0x1b, life);
    if (life === 0) {
      m.set16(a, 0);
      return;
    }
    this._fly_with_floor(a, 0x15e);
  }

  /** $1A6D0: 5 passes straight, then homes on +$1E (re-aimed every 5 passes; a decoy
   * flare within 6000 steals it). Lost target: 100 more passes straight. */
  homing_missile(a: number): void {
    const m = this.mem;
    const st = m.u8(a + 0x1a);
    if (st === 0) {
      m.set8(a + 0x1b, 0x64);
      m.set8(a + 0x1c, 5);
      m.set8(a + 0x1a, 1);
      return;
    }
    const life = (m.u8(a + 0x1b) - 1) & 0xff;
    m.set8(a + 0x1b, life);
    if (life === 0) {
      m.set16(a, 0);
      return;
    }
    if (st === 1) {
      if (this._dec_byte(a + 0x1c) === 0) {
        m.set8(a + 0x1a, 2);
        m.set8(a + 0x1c, 1);
      }
      return this._fly_with_floor(a, 0xfa);
    }
    if (this._dec_byte(a + 0x1c) === 0) {
      m.set8(a + 0x1c, 5);
      for (const k of [17, 18]) {
        const f = this.obj(k);
        if (m.u8(f + 1) & 1 && this.cheb2d(a, f) <= 6000) m.set32(a + 0x1e, f);
      }
      const t = m.u32(a + 0x1e);
      if (t >= 0x80000000 || !(m.u16(t) & 1)) {
        m.set8(a + 0x1a, 1);
        m.set8(a + 0x1c, 0x64);
        return;
      }
      const p: number[] = [];
      if (m.u16(t) & 0x8000) {
        for (let k = 0; k < 3; k++) p.push(m.u32(t + 0x0a + 4 * k));
      } else {
        const buf = m.u32(a + 0x3c);
        if (!buf) {
          m.set8(a + 0x1a, 1);
          m.set8(a + 0x1c, 0x64);
          return;
        }
        for (let k = 0; k < 3; k++) p.push(m.s16(t + 0x0a + 2 * k) + m.u32(buf + 2 + 4 * k));
      }
      const d = [0, 1, 2].map((k) => s32(p[k] - m.u32(a + 0x0a + 4 * k)));
      const [pitch, heading] = this.aim_angles(d[0], d[1], d[2]);
      m.set16(a + 0x22, pitch);
      m.set16(a + 0x24, heading);
    }
    m.set16(a + 4, this.turn_towards(m.u16(a + 0x22), m.u16(a + 4), 0x800));
    m.set16(a + 6, this.turn_towards(m.u16(a + 0x24), m.u16(a + 6), 0x800));
    this._fly_with_floor(a, 0xfa);
  }

  /** $1A866: max(|dx|, |dz|) between two world objects. */
  cheb2d(a: number, b: number): number {
    const m = this.mem;
    const dx = this.absl(m.u32(a + 0x0a) - m.u32(b + 0x0a));
    const dz = this.absl(m.u32(a + 0x12) - m.u32(b + 0x12));
    return dx >= dz ? dx : dz;
  }

  /** $1A886: steered by its Walrus through +$1E (yaw) and +$20 (pitch); explodes low
   * or over an island. */
  guided_missile(a: number): void {
    const m = this.mem;
    if (this._dec_byte(a + 0x1c) === 0) {
      m.set16(a, 0);
      return;
    }
    m.set16(a + 6, m.u16(a + 6) + 2 * m.u16(a + 0x1e));
    m.set16(a + 4, m.u16(a + 4) - m.u16(a + 0x20));
    const [dx, dy, dz] = this.velocity_from_angles(a, 0x96);
    m.set32(a + 0x0a, m.u32(a + 0x0a) + dx);
    m.set32(a + 0x12, m.u32(a + 0x12) + dz);
    const y = s32(m.u32(a + 0x0e) + dy);
    m.set32(a + 0x0e, y);
    if (y >= 0x96) return;
    if (y > 0x1e) {
      const buf = m.u32(a + 0x3c);
      if (!buf) return;
      const [bx, bz] = this.island_box_distance(a, buf);
      if (bx >= 0 || bz >= 0) return;
    }
    m.set16(a, 0);
    this._explosion(1, a);
  }

  /** $1A8F6: flies to the point (+$1E, +$22) at 1000, dropping to 140 within 3000;
   * explodes (blast) within 200 or when its life runs out. */
  cruise_missile(a: number): void {
    const m = this.mem;
    if (this._dec_byte(a + 0x1b) === 0) return this.explode_blast(a);
    const alt = m.u8(a + 0x1a) ? 0x8c : 0x3e8;
    const want = w16((m.u16(a + 0x10) - alt) * 8);
    m.set16(a + 4, this.turn_towards(want, m.u16(a + 4), 0x400));
    const dx = s32(m.u32(a + 0x1e) - m.u32(a + 0x0a));
    const dz = s32(m.u32(a + 0x22) - m.u32(a + 0x12));
    let d = this.absl(dx);
    if (this.absl(dz) > d) d = this.absl(dz);
    const heading = this.atan2(dx, dz);
    if (d <= 0xbb8) {
      if (d <= 0xc8) return this.explode_blast(a);
      m.set8(a + 0x1a, 1);
    }
    if (this.absw(m.u16(a + 6) - heading) <= 0x3e80) {
      m.set16(a + 6, this.turn_towards(heading, m.u16(a + 6), 0x800));
    }
    const [vx, vy, vz] = this.velocity_from_angles(a, 0xc8);
    for (const [o, v] of [[0x0a, vx], [0x0e, vy], [0x12, vz]]) m.set32(a + o, m.u32(a + o) + v);
  }

  /** $1C5EC: the object itself explodes into a blast volume. */
  explode_blast(a: number): void {
    this.hit_blast(a, a, 0);
  }

  /** $1A9B0: falls, tumbling; bounces off an island top (3/4 of the speed back, with
   * debris) until the bounce is under 30, then explodes; over the sea it explodes. */
  bomb(a: number): void {
    const m = this.mem;
    if (s32(m.u32(a + 0x0e)) < 0x64 && m.u32(a + 0x3c)) {
      const [bx, bz] = this.island_box_distance(a, m.u32(a + 0x3c));
      if (bx < 0 && bz < 0) {
        m.set32(a + 0x0e, 0x64);
        const vy = w16(-m.u16(a + 0x22));
        m.set16(a + 0x1e, 0x1000);
        if (s16(vy) < 0x1e) return this.explode_blast(a);
        m.set16(a + 0x22, vy - (s16(vy) >> 2));
        this.spawn_debris_world(a);
        this._explosion(1, a);
      } else if (s32(m.u32(a + 0x0e)) < 0x14) {
        m.set32(a + 0x0e, 0);
        return this.explode_blast(a);
      }
    }
    m.set16(a + 4, m.u16(a + 4) + m.u16(a + 0x1e));
    const vx = m.s16(a + 0x20);
    let vy = s16(m.u16(a + 0x22) - 10);
    const vz = m.s16(a + 0x24);
    if (vy < -0x8c) vy = -0x8c;
    m.set16(a + 0x22, vy);
    for (const [o, v] of [[0x0a, vx], [0x0e, vy], [0x12, vz]]) m.set32(a + o, m.u32(a + o) + v);
  }

  /** $1AA52: tumbles along heading +$20 at 100 per pass; gone (with debris) when its
   * timer runs out or it's low over an island. */
  virus_bomb(a: number): void {
    const m = this.mem;
    let gone = this._word_timer(a + 0x1e);
    if (!gone && s32(m.u32(a + 0x0e)) < 0x50 && m.u32(a + 0x3c)) {
      const [bx, bz] = this.island_box_distance(a, m.u32(a + 0x3c));
      gone = bx < 0 && bz < 0;
    }
    if (gone) {
      m.set16(a, 0);
      this.spawn_debris_world(a);
      return;
    }
    m.set16(a + 6, m.u16(a + 6) + 0x1f4);
    m.set16(a + 4, m.u16(a + 4) - 0x300);
    m.set16(a + 8, m.u16(a + 8) + 0x172);
    const [vx, vz] = this.rotate_yaw(0, 0x64, m.u16(a + 0x20));
    m.set32(a + 0x0a, m.u32(a + 0x0a) + vx);
    m.set32(a + 0x12, m.u32(a + 0x12) + vz);
  }

  // enemy fighter ($1AC00)
  /** $1AC00: +$1A state (1 fly to waypoint +$22/+$26/+$2A, 2 search, 3 attack target
   * +$22, 4 return, 5 land), +$1D next state, +$16 fuel, +$18 missiles, +$38 health. */
  enemy_fighter(a: number): void {
    const m = this.mem;
    const st = m.u8(a + 0x1a);
    if (st === 0) {
      m.set8(a + 0x1a, 1);
      m.set8(a + 0x1b, 0xff);
      m.set8(a + 0x1d, 2);
      return;
    }
    if (st === 1) return this._fighter_cruise(a);
    if (st === 2) {
      let t = this._fighter_find_unit(a);
      if (t === null) t = this._fighter_find_structure(a);
      if (t === null) return this._fighter_wander(a);
      m.set32(a + 0x22, t);
      m.set8(a + 0x1a, 3);
      return this._fighter_steer(a);
    }
    if (st === 3) return this._fighter_attack(a);
    if (st === 4) return this._fighter_home(a);
    return this._fighter_land(a);
  }

  _fighter_cruise(a: number): void {
    const m = this.mem;
    if (m.s8(a + 0x1d) < 4) {
      let fuel = m.s16(a + 0x16);
      if (m.u8(0x11d0) || fuel < 0xc8) return this._fighter_home(a);
      fuel -= 1;
      m.set16(a + 0x16, fuel);
      if (fuel === 0) {
        m.set16(a, 0);
        m.set16(0x11ce, m.u16(0x11ce) - 1);
        return;
      }
      if (m.s16(a + 0x38) <= 0x3e8 || m.u16(a + 0x18) === 0) return this._fighter_home(a);
    }
    const d = this._fighter_aim(a, m.u32(a + 0x22), m.u32(a + 0x26), m.u32(a + 0x2a));
    const left = (m.u8(a + 0x1b) - 1) & 0xff;
    m.set8(a + 0x1b, left);
    if (left === 0 || s32(d) <= 0x12c) m.set8(a + 0x1a, m.u8(a + 0x1d));
    m.set8(a + 0x1c, s32(d) < 0x258 ? 0xff : 0);
    this._fighter_steer(a);
  }

  /** $1AE96: aim at a point (pitch +$1E, heading +$20); returns the 3D Chebyshev
   * distance. Offsets are quartered until they fit in words ($1A7E4). */
  _fighter_aim(a: number, x: number, y: number, z: number): number {
    const m = this.mem;
    let d = [x, y, z].map((v, k) => s32(v - m.u32(a + 0x0a + 4 * k)));
    const ad = d.map((v) => this.absl(v));
    let dist = ad[0];
    if (ad[1] > dist) dist = ad[1];
    if (ad[2] > dist) dist = ad[2];
    while (!d.every(fitsWord)) d = d.map((v) => v >> 2);
    const [pitch, heading] = this.aim_angles(d[0], d[1], d[2]);
    m.set16(a + 0x1e, pitch);
    m.set16(a + 0x20, heading);
    return dist;
  }

  /** $1AE50: turn (pitch $5DC, heading $800) and fly at 220 (110 when close). */
  _fighter_steer(a: number, slow: boolean | null = null): void {
    void slow;
    const m = this.mem;
    m.set16(a + 4, this.turn_towards(m.u16(a + 0x1e), m.u16(a + 4), 0x5dc));
    m.set16(a + 6, this.turn_towards(m.u16(a + 0x20), m.u16(a + 6), 0x800));
    const speed = m.u8(a + 0x1c) ? 0xdc >> 1 : 0xdc;
    const [vx, vy, vz] = this.velocity_from_angles(a, speed);
    for (const [o, v] of [[0x0a, vx], [0x0e, vy], [0x12, vz]]) m.set32(a + o, m.u32(a + o) + v);
  }

  /** $1ACB2/$1ACB8: a random waypoint within 7000, altitude 500-2547; fly there. */
  _fighter_wander(a: number, timer: number = 0x14): void {
    const m = this.mem;
    m.set8(a + 0x1b, timer);
    const x = s32(s16(w16(((s16(this.rand()) * 0x1b58) >> 16) * 2)) + m.u32(a + 0x0a));
    m.set32(a + 0x22, x);
    const z = s32(s16(w16(((s16(this.rand()) * 0x1b58) >> 16) * 2)) + m.u32(a + 0x12));
    m.set32(a + 0x2a, z);
    m.set32(a + 0x26, (z & 0x7ff) + 0x1f4);
    m.set8(a + 0x1a, 1);
    m.set8(a + 0x1d, 2);
    m.set8(a + 0x1c, 0xff);
    this._fighter_steer(a);
  }

  /** $1AED0: nearest player unit (objects 2-10) within 20000: Walruses count 5000
   * further, the carrier 5000 nearer. */
  _fighter_find_unit(a: number): number | null {
    const m = this.mem;
    let best: number | null = null;
    let best_d = 0xffffffff;
    for (let k = 2; k < 11; k++) {
      const o = this.obj(k);
      if (!(m.u8(o + 1) & 1)) continue;
      let d = this.cheb2d(a, o);
      if (m.u16(o + 2) === 6) d += 5000;
      if (o === CARRIER) d -= 5000;
      d = s32(d);
      if (d > 20000) continue;
      if (u32(d) <= best_d) {
        best = o;
        best_d = u32(d);
      }
    }
    // The caller tests the best distance's sign (bpl): the carrier closer than 5000
    // (negative) counts as nothing found.
    return best_d < 0x80000000 ? best : null;
  }

  /** $1AF20: nearest attackable structure (flag bit 14) on a player island the fighter
   * is attached to. */
  _fighter_find_structure(a: number): number | null {
    const m = this.mem;
    const buf = m.u32(a + 0x3c);
    if (!buf || this.owner(m.u8(buf + 1)) !== 1) return null;
    let best: number | null = null;
    let best_d = 0xffffffff;
    for (let j = 0; j < 64; j++) {
      const e = buf + 0x12 + 0x40 * j;
      const f = m.u16(e);
      if ((f & 0x4001) !== 0x4001) continue;
      const dx = this.absl(m.s16(e + 0x0a) + m.u32(buf + 2) - m.u32(a + 0x0a));
      const dz = this.absl(m.s16(e + 0x0e) + m.u32(buf + 0x0a) - m.u32(a + 0x12));
      const d = u32(dx >= dz ? dx : dz);
      if (d <= best_d) {
        best = e;
        best_d = d;
      }
    }
    return best;
  }

  _fighter_attack(a: number): void {
    const m = this.mem;
    const t = m.u32(a + 0x22);
    if (!(m.u16(t) & 1)) return this._fighter_wander(a);
    let p: number[];
    if (m.u16(t) & 0x8000) {
      p = [m.u32(t + 0x0a), m.u32(t + 0x0e), m.u32(t + 0x12)];
    } else {
      const buf = m.u32(a + 0x3c);
      if (!buf) return this._fighter_wander(a);
      p = [m.s16(t + 0x0a) + m.u32(buf + 2), m.s16(t + 0x0c), m.s16(t + 0x0e) + m.u32(buf + 0x0a)];
    }
    const d = s32(this._fighter_aim(a, p[0], p[1], p[2]));
    if (m.u16(a + 0x18)) {
      if (d > 40000) return this._fighter_wander(a);
      if (d > 5000) {
        m.set8(a + 0x1c, 0xff);
        return this._fighter_steer(a);
      }
      if (this.absw(m.u16(a + 0x20) - m.u16(a + 6)) <= 0x100) {
        const b = this.find_free_object();
        if (b !== null) {
          this.init_world_object(b, 0x11);
          this.reset_pair_schedules();
          m.set8(b + 0xc0 + 2 * m.u16(0x6f0), 0xff);
          m.set32(b + 0x0e, m.u32(a + 0x0e));
          const [vx, vz] = this.yaw_vector(a, 0x64);
          m.set32(b + 0x0a, m.u32(a + 0x0a) + vx);
          m.set32(b + 0x12, m.u32(a + 0x12) + vz);
          for (const o of [4, 6, 8]) m.set16(b + o, m.u16(a + o));
          m.set16(b + 8, 0);
          m.set32(b + 0x1e, m.u32(a + 0x22));
          m.set16(a + 0x18, m.u16(a + 0x18) - 1);
        }
      }
    }
    this._fighter_wander(a, 0x64);
  }

  /** $1ADD4: waypoint 1500 above the enemy carrier; next state 4 (far) or land. */
  _fighter_home(a: number): void {
    const m = this.mem;
    const c = this.obj(19);
    m.set32(a + 0x22, m.u32(c + 0x0a));
    m.set32(a + 0x26, m.u32(c + 0x0e) + 0x5dc);
    m.set32(a + 0x2a, m.u32(c + 0x12));
    if (this.cheb2d(a, c) > 0x1f4) {
      m.set8(a + 0x1d, 4);
      return this._fighter_recruise(a);
    }
    this._fighter_land(a);
  }

  /** $1ADF8: within 500 and below 240: lands (back in the reserve). */
  _fighter_land(a: number): void {
    const m = this.mem;
    const c = this.obj(19);
    for (let k = 0; k < 3; k++) m.set32(a + 0x22 + 4 * k, m.u32(c + 0x0a + 4 * k));
    if (this.cheb2d(a, c) <= 0x1f4 && s32(m.u32(a + 0x0e)) <= 0xf0) {
      m.set16(a, 0);
      m.set16(0x11ce, m.u16(0x11ce) - 1);
      m.set16(E_AIRCRAFT, m.u16(E_AIRCRAFT) + 1);
      return;
    }
    m.set8(a + 0x1d, 5);
    this._fighter_recruise(a);
  }

  /** $1AE3C: back to state 1 and run it again this pass. */
  _fighter_recruise(a: number): void {
    const m = this.mem;
    m.set8(a + 0x1a, 1);
    m.set8(a + 0x1b, 0xff);
    this._fighter_cruise(a);
  }

  /** AI object handlers by address -> method name (UnitState.AI_BEHAVIOURS). */
  static AI_BEHAVIOURS: Record<number, string> = {
    0x1aaba: "antiship_missile", 0x1a9a6: "blast",
    0x1a6a0: "laser_bolt", 0x1a6d0: "homing_missile",
    0x1a886: "guided_missile", 0x1a8f6: "cruise_missile",
    0x1a9b0: "bomb", 0x1aa52: "virus_bomb", 0x1ac00: "enemy_fighter",
  };

  // ---- debris spawning --------------------------------------------------------------------
  /** $1C2BA and friends: remove a world object (an explosion of level `sound`) and
   * throw its debris. */
  explode_world(a: number, sound: number): void {
    this.mem.set16(a, 0);
    this._explosion(sound, a);
    this.spawn_debris_world(a);
  }

  /** $1CA8E: debris at a world object's position. */
  spawn_debris_world(a: number): void {
    const m = this.mem;
    this._spawn_debris(a, m.u32(a + 0x0a), m.u32(a + 0x0e), m.u32(a + 0x12));
  }

  /** $1C6D6 / $1CA74: debris at an island entry (local position + origin $A4A).
   * Returns the z offset of the last shard (left in d1), or None. */
  spawn_debris_entry(e: number): number | null {
    const m = this.mem;
    const [x, y, z] = [0, 1, 2].map((k) => s32(m.s16(e + 0x0a + 2 * k) + m.u32(0xa4a + 4 * k)));
    return this._spawn_debris(e, x, y, z);
  }

  /** $1CC38: the active burst with the greatest age (later ones win ties). */
  _oldest_burst(): number {
    const m = this.mem;
    let best: number | null = null;
    let age = 0;
    for (let b = 0; b < 4; b++) {
      const a = DEBRIS + 0x12 * b;
      if (m.u8(a) & 1 && m.u8(a + 1) >= age) {
        best = a;
        age = m.u8(a + 1);
      }
    }
    if (best === null) throw new NotPorted("no debris burst to recycle");
    return best;
  }

  _set_pair(addr: number, angle: number): void {
    const [s, c] = this.sin_cos(angle);
    this.mem.set16(addr, s);
    this.mem.set16(addr + 2, c);
  }

  _rot(addr: number, x: number, y: number, yaw: boolean): [number, number] {
    const m = this.mem;
    const s = m.s16(addr);
    const c = m.s16(addr + 2);
    x = s16(x);
    y = s16(y);
    if (yaw) return [hi16((x * c + y * s) * 4), hi16((y * c - x * s) * 4)];
    return [hi16((x * c - y * s) * 4), hi16((y * c + x * s) * 4)];
  }

  /** ext.l of the low word, divs.w, swap: the remainder (or the high word on overflow). */
  static _divs_rem(r: number, d: number): number {
    const n = s16(r);
    d = s16(d);
    if (d === 0) throw new NotPorted("debris recipe divides by zero");
    const q = Math.trunc(n / d);
    if (!fitsWord(q)) return w16(n >> 16);
    return w16(n - q * d);
  }

  _divs_rem(r: number, d: number): number {
    return UnitCore._divs_rem(r, d);
  }

  /** What $1CA8E leaves in A1: the burst used, or the object if none (set by _spawn_debris). */
  debris_a1 = 0;

  /** $1CA9C: a burst of shards from the object type's recipe ($1C726, 18 bytes).
   * debris_a1 = what the routine leaves in A1: the burst used, or the object if none. */
  _spawn_debris(a: number, x: number, y: number, z: number): number | null {
    const m = this.mem;
    this.debris_a1 = a;
    this._set_pair(0xa68, m.u16(a + 4));
    this._set_pair(0xa6c, m.u16(a + 6));
    this._set_pair(0xa70, m.u16(a + 8));
    const t = m.u16(a + 2);
    if (t > m.u16(0x2e)) return null;
    const rec = 0x1c726 + 18 * t;
    const count = m.u16(rec);
    if (count === 0) return null;
    let burst: number | null = null;
    for (let b = 0; b < 4; b++) {
      if (!(m.u8(DEBRIS + 0x12 * b) & 1)) {
        burst = DEBRIS + 0x12 * b;
        break;
      }
    }
    if (burst === null) {
      burst = this._oldest_burst();
      this.free_burst(burst);
    }
    this.debris_a1 = burst;
    m.set8(burst + 1, 0);
    m.set32(burst + 2, x);
    m.set32(burst + 6, y);
    m.set32(burst + 0x0a, z);
    while (count > m.u16(0x1198)) this.free_burst(this._oldest_burst());
    m.set16(0x1198, m.u16(0x1198) - count);
    if (count < 2) throw new NotPorted("debris count < 2 (the original walks 65536 links)");
    const head = m.u32(0x1194);
    let last = head;
    for (let i = 0; i < count - 1; i++) last = m.u32(last + 0x16);
    m.set32(0x1194, m.u32(last + 0x16));
    m.set32(last + 0x16, 0);
    m.set32(burst + 0x0e, head);
    m.set8(burst, 1);
    const low = s32(m.u32(burst + 6) - m.s16(rec + 6)) <= 0x32;
    m.set16(0x1cbf4, (m.u16(0x1cbf4) & 0xf0ff) | (low ? 0 : 0xa00));
    let pairs = rec + 0x0a;
    let model = 0;
    let left = 0;
    let shard = head;
    let z3: number | null = null;
    while (shard) {
      if (left === 0) {
        let n: number;
        for (;;) {
          model = m.u8(pairs);
          n = m.u8(pairs + 1);
          pairs += 2;
          if (n) break;
        }
        left = n;
      }
      left -= 1;
      const zr = this._divs_rem(this.rand(), m.u16(rec + 8));
      const yr = this._divs_rem(this.rand(), m.u16(rec + 6));
      const xr = this._divs_rem(this.rand(), m.u16(rec + 4));
      const [x1, y1] = this._rot(0xa70, xr, yr, false);
      const [y2, z2] = this._rot(0xa68, y1, zr, false);
      const [x3, z3v] = this._rot(0xa6c, x1, z2, true);
      z3 = z3v;
      m.set16(shard + 0x0a, x3);
      m.set16(shard + 0x0c, y2);
      m.set16(shard + 0x0e, z3v);
      const div = m.u16(rec + 2);
      if (div === 0) throw new NotPorted("debris recipe divides by zero");
      const vx = (this.rand() >>> 16) % div;
      m.set16(shard + 0x10, x3 < 0 ? w16(-vx) : vx);
      const vy = (this.rand() >>> 16) % div;
      // $1CBF4 is the bpl after tst.w d6: patched to bra near the ground, so low
      // bursts always throw their shards upwards.
      const neg = y2 < 0 && (m.u16(0x1cbf4) & 0x0f00) === 0x0a00;
      m.set16(shard + 0x12, neg ? w16(-vy) : vy);
      const vz = (this.rand() >>> 16) % div;
      m.set16(shard + 0x14, z3v < 0 ? w16(-vz) : vz);
      const r = this.rand();
      m.set16(shard + 4, r);
      m.set16(shard + 6, r >>> 16);
      m.set16(shard + 8, this.rand());
      m.set16(shard + 2, model);
      shard = m.u32(shard + 0x16);
    }
    return z3;
  }

  // ---- dock -----------------------------------------------------------------------------
  /** $1DC6C: mark the objects on the lift and in the dock (byte +1 bit 2), and pick
   * the carrier model for the lift/door state (types 1-3). */
  dock_attach(): void {
    const m = this.mem;
    const d = m.s16(DOCKED_WALRUS);
    let a = 0;
    if (d >= 0) {
      if (d === 4) {
        a = this.obj(16);
      } else {
        a = (m.u8(0x122f) ? this.obj(11) : this.obj(6)) + OBJECT_SIZE * d;
      }
      m.set8(a + 1, m.u8(a + 1) | 4);
    }
    m.set32(0x6da, a);
    const bay = m.s16(0xdd0);
    a = 0;
    if (bay >= 0) {
      a = this.obj(2 + bay);
      m.set8(a + 1, m.u8(a + 1) | 4);
    }
    m.set32(0x6d6, a);
    let t: number;
    if (m.u16(LIFT_STATE)) {
      t = 3;
    } else if ((d & bay) >= 0 || m.u16(DOOR_STATE)) {
      t = 2;
    } else {
      t = 1;
    }
    m.set16(CARRIER + 2, t);
  }

  // ---- collisions: responses ------------------------------------------------------------
  /** $1BD30: [$754] = the hitter's damage class, [$756] = its damage; then the victim
   * type's handler ($1BD70). */
  collision_response(hitter: number, victim: number): void {
    const m = this.mem;
    const cls = m.u16(hitter + 0x34);
    m.set16(0x754, cls);
    m.set16(0x756, m.u16(0x1bd58 + 2 * cls));
    m.set16(0xaa6, 0);
    m.set16(0xaa8, 0);
    const h = m.u32(HIT_HANDLERS + 4 * m.u16(victim + 2));
    if (h === 0x1be58) return;
    const fn = UnitCore.HIT_HANDLERS[h];
    if (fn === undefined) throw new NotPorted(`hit handler $${hex5(h)} (type ${m.u16(victim + 2)})`);
    (this as any)[fn](hitter, victim, cls);
  }

  // ---- sound events (docs/units.md §17) ---------------------------------------------------
  /** `tst.b $1249; bne; jsr $20B90; dc.w n`: effect n, unless sound effects are off. */
  _sfx(n: number): void {
    if (!this.mem.u8(0x1249)) this.events.push(["sound", n]);
  }

  /** `move.w #level,d0; jsr $20C30` with A1 = world object a (position +$A/+$E/+$12).
   * The routine itself is gated by $1249 and graded by the distance to [$A02]. */
  _explosion(level: number, a: number): void {
    this.events.push(["explosion", level, a]);
  }

  /** `jsr $20C08` with A1 = island entry e: its words +$A/+$C/+$E plus the island
   * origin [$A4A/$A4E/$A52] (ext.l, add.l). */
  _explosion_entry(level: number, e: number): void {
    const m = this.mem;
    const [x, y, z] = [0, 1, 2].map((k) => s32(m.s16(e + 0x0a + 2 * k) + m.u32(0xa4a + 4 * k)));
    this.events.push(["explosion", level, x, y, z]);
  }

  /** $A3EE: add to the decimal score digits $918-$923. */
  score(points: number): void {
    const m = this.mem;
    let a = 0x923;
    let carry = points;
    for (let i = 0; i < 12; i++) {
      carry = (carry + m.u8(a)) & 0xffff;
      const q = Math.floor(carry / 10);
      const r = carry - q * 10;
      m.set8(a, r);
      a -= 1;
      carry = q;
      if (carry === 0) break;
    }
  }

  /** $1C680: owner of the island of buffer [$A9C]. */
  buffer_island_owner(): number {
    const m = this.mem;
    return this.owner(m.u8(m.u32(0xa9c) + 1));
  }

  /** $1C66A. */
  score_if_enemy(points: number): void {
    if (this.buffer_island_owner() === 2) this.score(points);
  }

  /** $1C69C: write an island entry back to its structure slot ([$AA0] island, [$AA4]). */
  entry_to_slot(e: number): void {
    const m = this.mem;
    const s = m.u32(0xaa0) + 0x12 + 0x0c * m.u16(0xaa4);
    m.set16(s, m.u16(e + 2));
    m.set16(s + 2, m.u16(e + 6));
    m.set16(s + 4, m.u16(e + 0x38));
    m.set16(s + 8, m.u16(e + 0x0a));
    m.set16(s + 0x0a, m.u16(e + 0x0e));
  }

  /** $1C696: clear the entry and its slot. */
  remove_entry(e: number): void {
    this.mem.set16(e, 0);
    this.mem.set16(e + 2, 0);
    this.entry_to_slot(e);
  }

  /** $1C180: subtract with a floor of 0 (unsigned). */
  _sub_floor(a: number, d: number): void {
    const v = this.mem.u16(a) - d;
    this.mem.set16(a, v < 0 ? 0 : v);
  }

  /** $1C188: damage spread over the carrier systems by where the hull was hit. */
  hit_carrier(hitter: number, v: number, cls: number): void {
    void hitter;
    void v;
    const m = this.mem;
    if (cls === 0) return;
    m.set16(0x952, 2);
    if (m.u16(C_DAMAGE_MSG) === 0) this.events.push(["message", "Carrier has been damaged!", this._ping()]);
    m.set16(C_DAMAGE_MSG, 0x1f4);
    const dmg = m.u16(0x756);
    const d = dmg >> 4;
    this._sub_floor(0xb34, d);
    this._sub_floor(0xb3c, w16(3 * d));
    const pos = m.s16(0xab2);
    const systems_1_4 = (x: number): void => {
      for (const a of [0xafc, 0xb04, 0xb0c, 0xb14]) this._sub_floor(a, x);
    };
    const bow = (x: number): void => {
      this._sub_floor(0xb1c, x);
      this._sub_floor(0xb2c, x);
      const bay = m.s16(0xdd0);
      if (bay >= 0) this._sub_floor(0x4aa + 0x34 * bay, x);
    };
    const stern = (x: number): void => {
      this._sub_floor(0xaf4, x);
      this._sub_floor(0xb24, x);
    };
    if (pos > 300) {
      systems_1_4((dmg >> 3) >> 2);
      bow(dmg >> 1);
    } else if (pos < -300) {
      systems_1_4((dmg >> 3) >> 2);
      stern(dmg >> 1);
    } else {
      bow((dmg >> 3) >> 1);
      stern((dmg >> 3) >> 1);
      systems_1_4(dmg >> 2);
    }
  }

  /** $1C0F4: strength -= damage (not from its own fighters); destroyed at <= 0. */
  hit_enemy_carrier(hitter: number, v: number, cls: number): void {
    const m = this.mem;
    if (cls === 0 || m.u16(hitter + 2) === 0x21) return;
    const st = s16(m.u16(E_STRENGTH) - m.u16(0x756));
    if (st > 0) {
      m.set16(E_STRENGTH, st);
      return;
    }
    m.set8(AI_ENABLED, 0);
    m.set16(v, 0);
    this._explosion(2, v);
    this.spawn_debris_world(v);
    for (let i = 0; i < 4; i++) this.score(25000);
    this.events.push(["message", "Enemy carrier destroyed", this._ping()]);
    m.set16(GAME_END_TIMER, 100);
  }

  /** $1BF42: health -= d; True if it survives. */
  _vehicle_damage(rec: number, d: number, kind: string, v: number): boolean {
    const m = this.mem;
    const h = s16(m.u16(rec + 0x14) - d);
    if (h > 0) {
      m.set16(rec + 0x14, h);
      return true;
    }
    m.set16(rec + 0x14, 0);
    this._explosion(1, v);
    this.spawn_debris_world(v);
    this.vehicle_destroyed(kind, v);
    return false;
  }

  /** Hook: $19188 (Manta) / $191F2 (Walrus), ported in stunits_vehicles. */
  vehicle_destroyed(kind: string, v: number): void {
    void v;
    throw new NotPorted(`${kind} destroyed`);
  }

  /** $1BE5A: rammed (class 11): pushed back and 1000/2000 damage; otherwise damage. */
  hit_walrus(hitter: number, v: number, cls: number): void {
    const m = this.mem;
    if (cls === 0) return;
    const bay = m.u16(v + 0x1a);
    const rec = 0x570 + 0x34 * bay;
    if (cls === 11) {
      const speed = m.u16(rec + 4);
      m.set16(rec + 4, (s16(-s16(speed)) >> 2) - 0x14);
      m.set16(rec + 6, 0);
      m.set16(rec + 2, 0);
      m.set8(rec, m.u8(rec) & 0xfc);
      if (m.u8(0x1233) && bay === m.u16(0x6e0)) m.set8(0x1233, 0);
      const t = m.u16(hitter + 2);
      const [vx, vz] = this.yaw_vector(v, speed);
      m.set32(v + 0x0a, m.u32(v + 0x0a) - vx);
      m.set32(v + 0x12, m.u32(v + 0x12) - vz);
      this.events.push(["message", `Walrus ${bay + 1} motion blocked by object`, this._ping(true, true)]);
      this._sfx(0);
      this._vehicle_damage(rec, 1 <= t && t < 4 ? 2000 : 1000, "Walrus", v);
      return;
    }
    if (m.u8(0x118a)) return;
    if (this._vehicle_damage(rec, m.u16(0x756), "Walrus", v)) {
      const hold = m.u16(rec + 0x30);
      m.set16(rec + 0x30, 0x1f4);
      if (hold === 0) this.events.push(["message", `Walrus ${bay + 1} has been damaged!`, this._ping()]);
    }
  }

  /** $1BF84. */
  hit_manta(hitter: number, v: number, cls: number): void {
    void hitter;
    const m = this.mem;
    if (cls === 0 || m.u8(0x118a)) return;
    const bay = m.u16(v + 0x1a);
    const rec = 0x496 + 0x34 * bay;
    if (this._vehicle_damage(rec, m.u16(0x756), "Manta", v)) {
      const hold = m.u16(rec + 0x30);
      m.set16(rec + 0x30, 0x1f4);
      if (hold === 0) this.events.push(["message", `Manta ${bay + 1} has been damaged!`, this._ping()]);
    }
  }

  /** $1BFFA: a laser bolt is removed (unless hit by another bolt). */
  hit_laser(hitter: number, v: number, cls: number): void {
    void cls;
    if (this.mem.u16(hitter + 2) !== 0x10) {
      this.mem.set16(v, 0);
      this._explosion(0, v);
    }
  }

  /** $1C010: a carrier defence drone is destroyed by anything but another drone. */
  hit_deck_drone(hitter: number, v: number, cls: number): void {
    void cls;
    const m = this.mem;
    const ht = m.s16(hitter + 2);
    if (0x0a <= ht && ht <= 0x0d) return;
    m.set16(0x95c, 2); // bay status redraw (units_render)
    this.events.push(["message", "Defence drone destroyed", this._ping()]);
    m.set8(DOCKED_WALRUS, m.u8(v) & 4 ? 0xff : 0);
    m.set16(DOOR_STATE, 3);
    this.dock_attach();
    m.set16(0x65e + 14 * m.u16(v + 0x1a), 0);
    this._explosion(0, v);
    m.set16(v, 0);
    this.spawn_debris_world(v);
  }

  /** $1C06E/$1C080/$1C312: removed, debris. The explosion is level 1 from the guided
   * missile's handler $1C080, else 0. */
  hit_world_simple(hitter: number, v: number, cls: number): void {
    void hitter;
    void cls;
    const m = this.mem;
    this._explosion(m.u32(HIT_HANDLERS + 4 * m.u16(v + 2)) === 0x1c080 ? 1 : 0, v);
    m.set16(v, 0);
    this.spawn_debris_world(v);
  }

  /** $1C2A4. */
  hit_flare(hitter: number, v: number, cls: number): void {
    void hitter;
    void cls;
    const m = this.mem;
    m.set16(v, 0);
    this._explosion(0, v);
    m.set16(0x1152, m.u16(0x1152) - 1);
    this.spawn_debris_world(v);
  }

  /** $1C2D2: destroyed; 250 points on an enemy island. */
  hit_antiship(hitter: number, v: number, cls: number): void {
    void hitter;
    void cls;
    this.mem.set16(v, 0);
    this._explosion(1, v);
    this.spawn_debris_world(v);
    this.score_if_enemy(250);
  }

  /** $1C092. */
  hit_island_drone(hitter: number, v: number, cls: number): void {
    void hitter;
    const m = this.mem;
    if (cls === 0) return;
    const h = s16(m.u16(v + 0x38) - m.u16(0x756));
    if (h > 0) {
      m.set16(v + 0x38, h);
      return;
    }
    m.set16(v, 0);
    this.score_if_enemy(750);
    const isl = ISLAND_TABLE + ISLAND_SIZE * m.u8(m.u32(0xa9c) + 1);
    m.set16(isl + 0x10, m.u16(isl + 0x10) - 1);
    this._explosion_entry(1, v);
    this.spawn_debris_entry(v);
  }

  /** $1C292. */
  hit_sam(hitter: number, v: number, cls: number): void {
    void hitter;
    void cls;
    this.mem.set16(v, 0);
    this._explosion_entry(0, v);
    this.spawn_debris_entry(v);
  }

  /** $1C2EE: rocks pass through the volcano. */
  hit_rock(hitter: number, v: number, cls: number): void {
    void cls;
    if (this.mem.u16(hitter + 2) === 7) return;
    this.mem.set16(v, 0);
    this._explosion_entry(0, v);
    this.spawn_debris_entry(v);
    this.score(10);
  }

  /** $1C272. */
  hit_tree(hitter: number, v: number, cls: number): void {
    void hitter;
    void cls;
    this.mem.set16(v, 0);
    this._explosion_entry(1, v);
    this.spawn_debris_entry(v);
    this.remove_entry(v);
    this.score(1);
  }

  /** $1C4E0. */
  hit_enemy_fighter(hitter: number, v: number, cls: number): void {
    void cls;
    const m = this.mem;
    if (m.u16(hitter + 2) === 4) return;
    const h = s16(m.u16(v + 0x38) - m.u16(0x756));
    if (h > 0) {
      m.set16(v + 0x38, h);
      return;
    }
    m.set16(v, 0);
    this.spawn_debris_world(v);
    this._explosion(2, this.debris_a1); // quirk: A1 is what $1CA8E left there
    m.set16(0x11ce, m.u16(0x11ce) - 1);
  }

  static BUILDING_REWARDS: Record<number, [number, number]> = {
    0x1c3d6: [0x400, 2500], 0x1c3e6: [0, 1000], 0x1c3f4: [0, 1000],
    0x1c402: [0x200, 2000], 0x1c412: [0x100, 1000], 0x1c422: [0x100, 1000],
    0x1c432: [0x100, 2000], 0x1c442: [0x100, 2000], 0x1c452: [0x100, 1500],
  };

  /** $1C3D6..$1C45E: health written back to the slot; destroyed buildings score and
   * raise the difficulty on enemy islands. */
  hit_building(hitter: number, v: number, cls: number): void {
    void hitter;
    void cls;
    const m = this.mem;
    const h_addr = m.u32(HIT_HANDLERS + 4 * m.u16(v + 2));
    const reward = UnitCore.BUILDING_REWARDS[h_addr];
    if (reward === undefined) throw new Error(`KeyError: ${h_addr}`); // Python dict lookup
    const [diff, points] = reward;
    m.set16(0xaa8, diff);
    m.set16(0xaa6, points);
    const h = s16(m.u16(v + 0x38) - m.u16(0x756));
    if (h > 0) {
      m.set16(v + 0x38, h);
      return this.entry_to_slot(v);
    }
    this._explosion_entry(diff ? 2 : 1, v);
    const left = this.spawn_debris_entry(v);
    this.remove_entry(v);
    if (this.buffer_island_owner() === 2) {
      this.score(points);
      if (diff) this.difficulty_add(diff);
    } else {
      // Quirk: this branch lands on the "store health" code, so the destroyed entry
      // gets d1 as its health and is written back to its slot again. By then d1 is the
      // last debris shard's z offset (or the negative health if there was no debris).
      m.set16(v + 0x38, left === null ? h : left);
      this.entry_to_slot(v);
    }
  }

  /** $1C324: lasers do 1/8; at 0 the island turns neutral. */
  hit_command_centre(hitter: number, v: number, cls: number): void {
    void cls;
    const m = this.mem;
    const t = m.u16(hitter + 2);
    if (t === 0x15) return;
    let d = m.u16(0x756);
    if (t === 0x10) d = s16(d) >> 3;
    const h = s16(m.u16(v + 0x38) - d);
    if (h > 0) {
      m.set16(v + 0x38, h);
      return this.entry_to_slot(v);
    }
    if (this.buffer_island_owner() === 2) {
      this.score(5000);
      this.difficulty_add(0x800);
      m.set16(0xd52, m.u16(0xd52) + 1);
    }
    const i = m.u8(m.u32(0xa9c) + 1);
    m.set8(OWNERS + i, 0);
    if (i === m.u16(STOCKPILE)) {
      m.set8(STOCKPILE, 0xff);
      this.stores_clear();
    }
    this.events.push(["neutral", i]);
    this._explosion_entry(2, v);
    this.spawn_debris_entry(v);
    this.remove_entry(v);
    this.network_invalidate();
  }

  /** $1C4B6: progress -= the damage class; 15 points per hit on enemy islands. */
  hit_construction(hitter: number, v: number, cls: number): void {
    void hitter;
    void cls;
    const m = this.mem;
    this._explosion_entry(0, v);
    const h = s16(m.u16(v + 0x38) - m.u16(0x754));
    if (h <= 0) return this.remove_entry(v);
    m.set16(v + 0x38, h);
    this.score_if_enemy(15);
    this.entry_to_slot(v);
  }

  /** $1C512: entering a command centre from the front captures the island. */
  hit_virus_bomb(hitter: number, v: number, cls: number): void {
    void cls;
    const m = this.mem;
    if (m.u16(hitter + 2) === 0x29) {
      const a = this.atan2(m.s16(0xab0), m.s16(0xab2));
      if (this.absw(a - m.u16(hitter + 6)) <= 10000) {
        const i = m.u8(m.u32(0xa9c) + 1);
        if (this.owner(i) === 1) {
          this.events.push(["message", "Friendly control centre bombed!", this._ping()]);
        } else {
          m.set8(OWNERS + i, 1);
          m.set16(0xd52, m.u16(0xd52) + 1);
          if (i === m.u16(STOCKPILE)) {
            m.set8(STOCKPILE, 0xff);
            this.stores_clear();
          }
          this.events.push(["captured", i, 1]);
          m.set16(v, 0);
          this.spawn_debris_world(v);
          this.network_invalidate();
          this.score(10000);
          this._sfx(11);
          return;
        }
        this._explosion(0, v);
        m.set16(v, 0);
        this.spawn_debris_world(v);
        return;
      }
    }
    this._explosion(0, v);
    m.set16(v, 0);
    this.spawn_debris_world(v);
  }

  /** $1C5EE explode_blast: debris, then a type 23 blast volume where it was. */
  hit_blast(hitter: number, v: number, cls: number): void {
    void hitter;
    void cls;
    const m = this.mem;
    this._explosion(2, v);
    this.spawn_debris_world(v);
    const pos = [0x0a, 0x0e, 0x12].map((o) => m.u32(v + o));
    m.set16(v, 0);
    const a = this.find_free_object();
    if (a === null) throw new NotPorted("blast with no free object");
    const f = m.u16(FRAME);
    m.set16(FRAME, (f + 1) & 0x7fff);
    this.init_world_object(a, 0x17);
    this.reset_pair_schedules();
    m.set16(FRAME, f);
    [0x0a, 0x0e, 0x12].forEach((o, k) => m.set32(a + o, pos[k]));
    m.set16(a + 0x30, 0x7d0);
    m.set16(a + 0x32, 0x1f4);
    m.set8(a + 0x1b, 5);
  }

  /** $1C0CA. */
  hit_supply_drone(hitter: number, v: number, cls: number): void {
    void hitter;
    const m = this.mem;
    if (cls === 0) return;
    const h = s16(m.u16(v + 0x38) - m.u16(0x756));
    if (h > 0) {
      m.set16(v + 0x38, h);
      return;
    }
    this.supply_drone_destroyed(v);
    this._explosion(1, v);
  }

  /** Hook: the rest of $1C0CA ($1925C), ported in stunits_enemy. */
  supply_drone_destroyed(v: number): void {
    void v;
    throw new NotPorted("supply drone destroyed");
  }

  /** Hit handlers by address -> method name (UnitState.HIT_HANDLERS). */
  static HIT_HANDLERS: Record<number, string> = {
    0x1c188: "hit_carrier", 0x1c0f4: "hit_enemy_carrier", 0x1bf84: "hit_manta",
    0x1be5a: "hit_walrus", 0x1c2ee: "hit_rock", 0x1c010: "hit_deck_drone",
    0x1bffa: "hit_laser", 0x1c06e: "hit_world_simple", 0x1c080: "hit_world_simple",
    0x1c5ee: "hit_blast", 0x1c512: "hit_virus_bomb", 0x1c092: "hit_island_drone",
    0x1c272: "hit_tree", 0x1c292: "hit_sam", 0x1c2a4: "hit_flare",
    0x1c312: "hit_world_simple", 0x1c4e0: "hit_enemy_fighter", 0x1c2d2: "hit_antiship",
    0x1c0ca: "hit_supply_drone", 0x1c324: "hit_command_centre",
    0x1c4b6: "hit_construction",
    0x1c3d6: "hit_building", 0x1c3e6: "hit_building", 0x1c3f4: "hit_building",
    0x1c402: "hit_building", 0x1c412: "hit_building", 0x1c422: "hit_building",
    0x1c432: "hit_building", 0x1c442: "hit_building", 0x1c452: "hit_building",
  };

  /** difficulty_add $190EE then difficulty_apply $190FE. */
  difficulty_add(d: number): void {
    const m = this.mem;
    const v = m.u16(0x444) + d;
    m.set16(0x444, v > 0xffff ? 0xffff : v);
    this.difficulty_apply();
  }

  /** $190FE: the defence parameters $446-$454 and command-centre health from [$444]. */
  difficulty_apply(): void {
    const m = this.mem;
    const d = m.u16(0x444);
    m.set16(0x446, s16(d) >= 0x4000 ? 3 : 2);
    let n = 3;
    if (d & 0x8000) n += pyDiv(w16(d - 0x8000), 10000);
    m.set16(0x448, n);
    const d6 = d >> 6;
    const d10 = d6 >> 4;
    m.set16(0x44a, (d10 >> 1) + d10 + 0x96);
    m.set16(0x450, d10 + 0xc8);
    let d2 = w16(d6 + 0x400);
    m.set16(0x452, d2);
    d2 >>= 1;
    m.set16(0x44c, d2 + (d2 >> 1));
    m.set16(0x454, d >= 0x4000 ? d : 0x4000);
    const cc = (d >> 4) + 0x200;
    m.set16(0x44e, cc);
    m.set16(0x18d74, cc + 0x800);
    m.set16(0x18dcc, cc + 0x800);
  }
}

// The unit layer is split across modules: UnitCore (this file) and three subclasses that
// override its hooks and stubs (units_carrier.ts, units_enemy.ts, units_vehicles.ts, where
// UnitState is defined). The dispatch tables (ISLAND_BEHAVIOURS, AI_BEHAVIOURS, HIT_HANDLERS)
// live on UnitCore and name the methods, so the most derived override runs, as in Python.
