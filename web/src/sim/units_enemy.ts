/**
 * Unit layer, part of tools/stunits.py: the enemy carrier's close-range combat (states 10
 * $1B2D2 and 11 $1B49E, via the enemy_tactical/enemy_engaged hooks) and supply transfers by the
 * supply drone ($1F316, object 16; supply_drone_destroyed), with the Resources-screen command
 * that starts a transfer ($10E4A).
 *
 * Port of tools/stunits_enemy.py. In Python the mixin is combined into stunits.UnitState ahead
 * of stunits.UnitCore, so methods defined here replace the core's stubs and hooks; here the
 * class sits between CarrierUnits and UnitState in the inheritance chain.
 */
import { Mem, NotPorted, s16, s32, u32, w16, shr } from "./mem.ts";
import type { Region } from "./mem.ts";
import {
  B_POOL,
  B_TIMER,
  E_AIRCRAFT,
  E_ISLAND,
  E_RESERVE,
  E_STATE,
  E_STRENGTH,
  E_TARGET,
  ENEMY_CARRIER as WORLD_ENEMY_CARRIER,
  ISLAND_SIZE,
  ISLAND_TABLE,
  ITEMS,
  ITEM_SIZE,
  NEMESIS,
  OBJECTS,
  OBJECT_SIZE,
  OWNERS,
  STOCKPILE,
} from "./world.ts";
import { DOCKED_WALRUS, DOOR_STATE, NEW_INDEX, UNIT_REGIONS } from "./units_core.ts";
import { CarrierUnits } from "./units_carrier.ts";

export const ENEMY_CARRIER = WORLD_ENEMY_CARRIER; // object 19
export const PLAYER_CARRIER = OBJECTS + OBJECT_SIZE * 10;
export const SUPPLY_DRONE = OBJECTS + OBJECT_SIZE * 16;
export const E_COOLDOWN_MISSILE = 0x11d4;
export const E_COOLDOWN_FIGHTER = 0x11cc;
export const E_AIRBORNE = 0x11ce;
export const E_RECALL = 0x11d0;
export const E_RETREAT_SPEED = 0x11d6;
export const S_ACTIVE = 0xd3e;
export const S_SECOND = 0xd3f;
export const S_DOCKING = 0xd40;
export const S_STATE = 0xd42;
export const S_TIMER = 0xd44;
export const S_DONE = 0xd46;
export const S_SPEED = 0xd48;
/** Table $1F938: (tank, queued). */
export const FUEL_TRANSFERS: [number, number][] = [
  [0xcfc, 0xd6c],
  [0xd00, 0xd70],
  [0xd04, 0xd74],
];
export const TANK_MAX = 0xfffe0;

// State this part reads or writes that the other regions don't cover.
for (const _region of [
  ["aircraft and AAV fuel", 0xd00, 8],
  ["enemy retreat speed", E_RETREAT_SPEED, 2],
] as Region[]) {
  if (!UNIT_REGIONS.some((r) => r[0] === _region[0] && r[1] === _region[1] && r[2] === _region[2])) {
    UNIT_REGIONS.push(_region);
  }
}

// Read-only windows copied by from_ram but never compared or written back.
// $1B18E reads an "owner" byte at $69B40 + (low word of the enemy carrier's x), a leftover
// register; this window makes that byte available without being simulation state.
export const LEFTOVER_WINDOW: [number, number] = [0x69b40 - 0x8000, 0x10000];

export abstract class EnemyUnits extends CarrierUnits {
  load_regions(ram: Uint8Array): void {
    super.load_regions(ram);
    const [a, n] = LEFTOVER_WINDOW;
    this.mem.buf.set(ram.subarray(a, a + n), a); // populated regions there already match
    this.mem.buf.set(ram.subarray(0, 0x40), 0); // read by $1F4DA as a null island buffer
  }

  // ---- enemy close-range combat ---------------------------------------------------------
  /** The game simply continues in state 10 (stsim stops the run here). */
  enemy_engaged(): void {}

  enemy_tactical(state: number): void {
    if (state === 10) {
      this.enemy_state10();
    } else {
      this.enemy_state11();
    }
  }

  /** $1B2D2: keep the stern to the player carrier and fire cruise missiles at it from the
   * reserve; launch fighters; retreat (state 11) when weak or out of missiles. */
  enemy_state10(): void {
    const m = this.mem;
    const e = ENEMY_CARRIER;
    if (!this.player_near_enemy()) {
      this._enemy_lost_contact();
      return;
    }
    if (m.s16(E_STRENGTH) < 3000 || m.u16(E_RESERVE) === 0) {
      m.set16(E_STATE, 11);
      m.set8(E_RECALL, 0xff);
      m.set16(E_RETREAT_SPEED, 0);
      return;
    }
    for (const a of [E_COOLDOWN_MISSILE, E_COOLDOWN_FIGHTER]) {
      if (m.u16(a)) {
        m.set16(a, m.u16(a) - 1);
      }
    }
    const c = PLAYER_CARRIER;
    const dx = s32(m.u32(c + 0x0a) - m.u32(e + 0x0a));
    const dz = s32(m.u32(c + 0x12) - m.u32(e + 0x12));
    let d = this.absl(dz);
    if (this.absl(dx) > d) {
      d = this.absl(dx);
    }
    if (d > 30000) {
      m.set16(e + 6, this.turn_towards(m.u16(e + 0x1e), m.u16(e + 6), 200));
    } else {
      const want = w16(this.atan2(dx, dz) + 0x8000);
      m.set16(e + 6, this.turn_towards(want, m.u16(e + 6), 200));
      if (this.absw(m.u16(e + 6) - want) > 0x800) {
        return;
      }
      if (m.u16(E_RESERVE) === 0 || m.u16(E_COOLDOWN_MISSILE)) {
        return;
      }
      const a = this.find_free_object();
      if (a === null || a === undefined) {
        return;
      }
      this.init_world_object(a, 0x13);
      this.reset_pair_schedules();
      const heading = m.u16(e + 6);
      m.set16(a + 6, heading + 0x8000);
      const [x, z] = this.rotate_yaw(0, -200, heading);
      m.set32(a + 0x0a, m.u32(e + 0x0a) + x);
      m.set32(a + 0x12, m.u32(e + 0x12) + z);
      m.set32(a + 0x0e, 0x8c);
      m.set8(a + 0xe6, 0xff); // never collides with its launcher
      m.set32(a + 0x1e, m.u32(c + 0x0a));
      m.set32(a + 0x22, m.u32(c + 0x12));
      m.set8(a + 0x1b, 0xff);
      m.set16(E_RESERVE, m.u16(E_RESERVE) - 1);
      m.set16(E_COOLDOWN_MISSILE, 200);
    }
    this._enemy_launch_fighter();
  }

  /** $1B400: a fighter (type 33) from the reserve into a free slot 20-23. The airborne
   * count $11CE is tested but never incremented (original bug). */
  _enemy_launch_fighter(): void {
    const m = this.mem;
    const e = ENEMY_CARRIER;
    if (m.u16(E_AIRCRAFT) === 0 || m.u16(E_COOLDOWN_FIGHTER) || m.s16(E_AIRBORNE) >= 4) {
      return;
    }
    let k = 0;
    let a = 0;
    let found = false;
    for (k = 0; k < 4; k++) {
      a = this.obj(20 + k);
      if (!(m.u8(a + 1) & 1)) {
        found = true;
        break;
      }
    }
    if (!found) {
      return;
    }
    m.set16(0x6f0, 20 + k);
    this.init_world_object(a, 0x21);
    this.reset_pair_schedules(); // uses [$6EE], whatever it holds
    const x = m.u32(e + 0x0a);
    let y = m.u32(e + 0x0a + 4);
    const z = m.u32(e + 0x0a + 8);
    y = y + 0xfa;
    [x, y, z].forEach((v, j) => m.set32(a + 0x0a + 4 * j, v));
    [x, y + 0x320, z].forEach((v, j) => m.set32(a + 0x22 + 4 * j, v));
    m.set16(a + 0x18, 0x0a);
    m.set16(a + 0x38, 0x1388);
    m.set16(a + 0x16, 0x7d0);
    m.set16(a + 4, 0xc000);
    m.set16(E_AIRCRAFT, m.u16(E_AIRCRAFT) - 1);
    m.set16(E_COOLDOWN_FIGHTER, 0x1e);
  }

  /** $1B18E entered from state 10: hide the carrier and resolve its island. The owner
   * byte comes from $69B40 + the low word of d0, which still holds the carrier's x from
   * the proximity test (a leftover register, not the island index). */
  _enemy_lost_contact(): void {
    const m = this.mem;
    const e = ENEMY_CARRIER;
    m.set8(e + 1, m.u8(e + 1) & 0xfe);
    const owner = m.u8(OWNERS + s16(m.u32(e + 0x0a)));
    if (owner === 0) {
      this.enemy_drop_builder(m.u16(E_TARGET));
    } else if (owner === 1) {
      if (m.u16(E_ISLAND) !== NEMESIS && m.s16(E_STRENGTH) < 3000) {
        m.set16(E_STATE, 1);
        return;
      }
      m.set16(E_STATE, 3);
      m.set16(B_POOL, 5000);
      m.set16(B_TIMER, 400);
      this.events.push(["attacked", m.u16(E_ISLAND)]);
    } else {
      m.set16(E_STATE, 2);
    }
  }

  /** $1B49E: retreat: turn to the engage heading, then move away at about 250;
   * once no player unit is near, back to state 1, hidden. */
  enemy_state11(): void {
    const m = this.mem;
    const e = ENEMY_CARRIER;
    if (!this.player_near_enemy()) {
      m.set16(E_STATE, 1);
      m.set8(e + 1, m.u8(e + 1) & 0xfe);
      m.set16(E_AIRBORNE, 0);
      return;
    }
    const want = m.u16(e + 0x1e);
    m.set16(e + 6, this.turn_towards(want, m.u16(e + 6), 200));
    if (m.u16(e + 6) !== want) {
      return;
    }
    // $1A18A with the operands swapped: 250 moves 2 towards the old speed, so from 0 the
    // speed is 248 on the first step and stays there (original bug).
    const speed = this.turn_towards(m.u16(E_RETREAT_SPEED), 0xfa, 2);
    m.set16(E_RETREAT_SPEED, speed);
    const [x, z] = this.yaw_vector(e, speed);
    m.set32(e + 0x0a, m.u32(e + 0x0a) + x);
    m.set32(e + 0x12, m.u32(e + 0x12) + z);
  }

  // ---- supply transfers -------------------------------------------------------------------
  /** $1F316: the transfer state machine while [$D3E] is set; otherwise the return-trip
   * timer [$D44] counts seconds. [$D3F] (the tick's once-a-second flag) is cleared. */
  supply_transfer_step(): void {
    const m = this.mem;
    if (m.u8(S_ACTIVE)) {
      const st = m.u16(S_STATE);
      if (st > 15) {
        throw new NotPorted(`supply state ${st}`);
      }
      (this as any)[EnemyUnits.SUPPLY_STATES[st]]();
    } else if (m.u16(S_TIMER) && m.u8(S_SECOND)) {
      m.set16(S_TIMER, m.u16(S_TIMER) - 1);
    }
    m.set8(S_SECOND, 0);
  }

  /** y = (v * scale) >> 16, rounded on bit 15, plus offset. */
  _drone_height(v: number, scale: number, offset: number): number {
    const p = (v & 0xffff) * scale;
    const y = shr(p, 16) + (p & 0x8000 ? 1 : 0);
    return s32(y + offset);
  }

  _bump(): void {
    this.mem.set16(S_STATE, this.mem.u16(S_STATE) + 1);
  }

  // 0 $1F386
  _supply_rise(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const v = w16(m.u16(a + 0x24) + 0x10);
    if (v === 0x100) {
      this._bump();
    }
    m.set16(a + 0x24, v);
    m.set32(a + 0x0e, this._drone_height(v, 0x5a00, -60));
  }

  // 1 $1F3C0
  _supply_climb(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const v = w16(m.u16(a + 0x24) + 0x20);
    if (v === 0x200) {
      m.set16(S_STATE, 5);
    }
    m.set16(a + 0x24, v);
    m.set32(a + 0x0e, this._drone_height(w16(v - 0x100), 0x1e00, 30));
  }

  // 2 $1F400
  _supply_descend(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    let v = m.s16(a + 0x24);
    if (v <= 0x100) {
      this._bump();
      return;
    }
    v = w16(v - 0x20);
    if (v === 0x100) {
      this._bump();
    }
    m.set16(a + 0x24, v);
    m.set32(a + 0x0e, this._drone_height(w16(v - 0x100), 0x1e00, 30));
  }

  // 3 $1F44A
  _supply_land(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    let v = m.s16(a + 0x24) - 0x10; // subi.w; bgt (true difference, as N=V)
    if (v <= 0) {
      this._bump();
      v = 0;
    }
    m.set16(a + 0x24, v);
    m.set32(a + 0x0e, this._drone_height(v, 0x5a00, -60));
  }

  // 4 $1F482
  _supply_finish(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    m.set8(S_ACTIVE, 0);
    m.set8(a + 1, m.u8(a + 1) & 0xfe);
    m.set16(S_TIMER, this._supply_flight_time());
  }

  /** $1F49A: Chebyshev distance to the stockpile island / 256, at least 300 (1 if the
   * stockpile is invalid). */
  _supply_flight_time(): number {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const i = m.s16(STOCKPILE);
    if (i < 0) {
      return 1;
    }
    const isl = ISLAND_TABLE + ISLAND_SIZE * i;
    const dx = this.absl(m.u32(isl + 2) - m.u32(a + 0x0a));
    const dz = this.absl(m.u32(isl + 6) - m.u32(a + 0x12));
    const d = shr(u32(dx >= dz ? dx : dz), 8);
    return s16(d) < 0x12c ? 0x12c : w16(d);
  }

  /** $1F9A4: which sides of the island's box (buffer +$30/+$32) an object is beyond. */
  static _box_side(m: Mem, o: number, buf: number): number {
    let c = 0;
    const dx = s32(m.u32(o + 0x0a) - m.u32(buf + 2));
    const hx = m.u16(buf + 0x30);
    if (dx > hx) {
      c |= 1;
    }
    if (dx < -hx) {
      c |= 2;
    }
    const dz = s32(m.u32(o + 0x12) - m.u32(buf + 0x0a));
    const hz = m.u16(buf + 0x32);
    if (dz > hz) {
      c |= 4;
    }
    if (dz < -hz) {
      c |= 8;
    }
    return c;
  }

  // 5 $1F4DA
  /** Fly to the carrier once both are beyond the same side of the island's box; within
   * 2500, start docking (state 7) in the carrier's frame. */
  _supply_fly(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const c = PLAYER_CARRIER;
    // movea doesn't set flags: only the carrier's buffer is tested, and a drone without
    // one tests its box against address 0 (the exception vectors; original bug).
    const cb = m.u32(c + 0x3c);
    const db = m.u32(a + 0x3c);
    if (!cb) {
      m.set16(S_STATE, 2);
      this.events.push(["message", "Too far from island - drone returning", this._ping()]);
      return;
    }
    if (!(EnemyUnits._box_side(m, c, db) & EnemyUnits._box_side(m, a, db))) {
      return;
    }
    const dx = s32(m.u32(c + 0x0a) - m.u32(a + 0x0a));
    const dz = s32(m.u32(c + 0x12) - m.u32(a + 0x12));
    let d = this.absl(dz);
    if (this.absl(dx) > d) {
      d = this.absl(dx);
    }
    if (d < 0x9c4) {
      if (m.u8(0x122f) || m.u8(0x1233)) {
        return;
      }
      m.set8(S_DOCKING, 0xff);
      m.set16(S_STATE, 7);
      this.set_matrix(w16(-m.u16(c + 6)));
      const [x, z] = this.matrix_rotate(
        s32(m.u32(a + 0x0a) - m.u32(c + 0x0a)),
        s32(m.u32(a + 0x12) - m.u32(c + 0x12)),
      );
      m.set16(a + 0x1c, x);
      m.set16(a + 0x1e, z);
      return;
    }
    const heading = this.atan2(dx, dz);
    const speed = this.absw(heading - m.u16(a + 6)) <= 0x400 ? 0x40 : 0;
    m.set16(S_SPEED, speed);
    m.set16(a + 6, this.turn_towards(heading, m.u16(a + 6), 0x100));
    const [vx, vz] = this.yaw_vector(a, m.u16(S_SPEED));
    m.set32(a + 0x0a, m.u32(a + 0x0a) + vx);
    m.set32(a + 0x12, m.u32(a + 0x12) + vz);
  }

  // 6 $1F5FA
  _supply_idle(): void {}

  /** $1F66C: the drone's position from its offset (+$1C, +$1E) in the carrier's frame. */
  _supply_follow(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const c = PLAYER_CARRIER;
    this.set_matrix(m.u16(c + 6));
    const [x, z] = this.matrix_rotate(m.u16(a + 0x1c), m.u16(a + 0x1e));
    m.set32(a + 0x0a, m.u32(c + 0x0a) + x);
    m.set32(a + 0x12, m.u32(c + 0x12) + z);
  }

  /** Turn to a side ($4000 off the carrier's heading) and slide sideways by 64. */
  _supply_slide(sign_from_offset: boolean, invert: boolean): void {
    void sign_from_offset;
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const c = PLAYER_CARRIER;
    const off = m.s16(a + 0x1c);
    let d1 = 0x4000;
    let d2 = 0x40;
    let neg = off < 0;
    if (invert) {
      neg = !neg;
    }
    if (neg) {
      d1 = w16(-d1);
      d2 = -d2;
    }
    const want = w16(d1 + m.u16(c + 6));
    if (this.absw(want - m.u16(a + 6)) > 0x400) {
      d2 = 0;
    }
    m.set16(S_SPEED, d2);
    m.set16(a + 6, this.turn_towards(want, m.u16(a + 6), 0x100));
    m.set16(a + 0x1c, m.u16(a + 0x1c) + m.u16(S_SPEED));
  }

  // 7 $1F5FC
  _supply_clear_side(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    if (m.s16(a + 0x1e) <= -2000 || this.absw(m.u16(a + 0x1c)) >= 0x5dc) {
      this._bump();
    } else {
      this._supply_slide(true, false);
    }
    this._supply_follow();
  }

  // 8 $1F69E
  _supply_go_astern(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const c = PLAYER_CARRIER;
    if (m.s16(a + 0x1e) <= -2000) {
      this._bump();
    } else {
      const want = w16(m.u16(c + 6) + 0x8000);
      const d2 = this.absw(want - m.u16(a + 6)) <= 0x400 ? 0x40 : 0;
      m.set16(S_SPEED, d2);
      m.set16(a + 6, this.turn_towards(want, m.u16(a + 6), 0x100));
      m.set16(a + 0x1e, m.u16(a + 0x1e) - m.u16(S_SPEED));
    }
    this._supply_follow();
  }

  // 9 $1F6F8
  _supply_centre(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    if (this.absw(m.u16(a + 0x1c)) <= 0x40) {
      this._bump();
    } else {
      this._supply_slide(true, true);
    }
    this._supply_follow();
  }

  // 10 $1F764
  _supply_align(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const c = PLAYER_CARRIER;
    m.set16(a + 6, this.turn_towards(m.u16(c + 6), m.u16(a + 6), 0x100));
    if (this.absw(m.u16(a + 6) - m.u16(c + 6)) <= 0x100) {
      this._bump();
    }
    this._supply_follow();
  }

  // 11 $1F79E
  _supply_line_up(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const c = PLAYER_CARRIER;
    m.set16(a + 6, m.u16(c + 6));
    const off = m.s16(a + 0x1c);
    let v = this.absw(off);
    if (v > 0x18) {
      v -= 0x18;
      m.set16(a + 0x1c, off < 0 ? -v : v);
    } else {
      m.set16(a + 0x1c, 0);
      m.set16(DOCKED_WALRUS, 4);
      m.set16(DOOR_STATE, 1);
      this.dock_attach();
      this._bump();
    }
    this._supply_follow();
  }

  // 12 $1F7EA
  _supply_enter(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const c = PLAYER_CARRIER;
    m.set16(a + 6, m.u16(c + 6));
    const z = s16(m.u16(a + 0x1e) + 0x40);
    m.set16(a + 0x1e, z);
    if (z >= -1000) {
      this._bump();
      m.set16(S_TIMER, 0);
      this.events.push(["message", "Drone docked - transferring stores", this._ping()]);
    }
    this._supply_follow();
  }

  // 13 $1F84C
  /** One store type per second: the queued quantity joins the carrier's on-board
   * count (item record +8). */
  _supply_stores(): void {
    const m = this.mem;
    let k = m.u16(S_TIMER);
    const q = 0xd96 + 2 * k;
    let moved = true;
    if (m.u16(q)) {
      if (m.u8(S_SECOND)) {
        const item = ITEMS + ITEM_SIZE * k;
        m.set16(item + 8, m.u16(item + 8) + m.u16(q));
        m.set16(q, 0);
      } else {
        moved = false;
      }
    }
    if (moved) {
      k = w16(k + 1);
      m.set16(S_TIMER, k);
      if (k === 0x0f) {
        this._bump();
        m.set16(S_TIMER, 0);
      }
    }
    m.set16(SUPPLY_DRONE + 6, m.u16(PLAYER_CARRIER + 6));
    this._supply_follow();
  }

  // 14 $1F8AE
  /** One fuel type per second into its carrier tank, capped at $FFFE0. */
  _supply_fuel(): void {
    const m = this.mem;
    let k = m.u16(S_TIMER);
    if (k > 2) {
      throw new NotPorted("supply fuel index out of the table");
    }
    const [tank, queued] = FUEL_TRANSFERS[k];
    let moved = true;
    if (m.u32(queued)) {
      if (m.u8(S_SECOND)) {
        let v = s32(m.u32(tank) + m.u32(queued));
        v = v > TANK_MAX ? TANK_MAX : v < 0 ? 0 : v;
        m.set32(tank, v);
        m.set32(queued, 0);
      } else {
        moved = false;
      }
    }
    if (moved) {
      k = w16(k + 1);
      m.set16(S_TIMER, k);
      if (k === 3) {
        this._bump();
        m.set16(S_TIMER, 0);
        m.set16(S_DONE, 1);
        this.events.push(["message", "Transfer complete", this._ping()]);
      }
    }
    m.set16(SUPPLY_DRONE + 6, m.u16(PLAYER_CARRIER + 6));
    this._supply_follow();
  }

  // 15 $1F960
  _supply_leave(): void {
    const m = this.mem;
    const a = SUPPLY_DRONE;
    const c = PLAYER_CARRIER;
    m.set16(a + 6, m.u16(c + 6));
    const z = s16(m.u16(a + 0x1e) - 0x40);
    m.set16(a + 0x1e, z);
    if (z <= -2000) {
      m.set16(S_STATE, 2);
      m.set8(a + 1, m.u8(a + 1) & ~4);
      m.set16(DOCKED_WALRUS, 0xffff);
      m.set16(DOOR_STATE, 3);
      this.dock_attach();
      m.set8(S_DOCKING, 0);
    }
    this._supply_follow();
  }

  /** The transfer states, by [$D42] (method names). */
  static SUPPLY_STATES: readonly string[] = [
    "_supply_rise", "_supply_climb", "_supply_descend", "_supply_land", "_supply_finish",
    "_supply_fly", "_supply_idle", "_supply_clear_side", "_supply_go_astern",
    "_supply_centre", "_supply_align", "_supply_line_up", "_supply_enter",
    "_supply_stores", "_supply_fuel", "_supply_leave",
  ];

  /** $1C0CA's destroyed path and $1925C: the transfer is lost (queues emptied) and the
   * next one can't start for 900 s. */
  supply_drone_destroyed(v: number): void {
    const m = this.mem;
    m.set16(v, 0);
    this.events.push(["message", "Supply drone has been destroyed", this._ping()]);
    if (m.u16(DOCKED_WALRUS) === 4) {
      m.set16(DOCKED_WALRUS, 0xffff);
      this.dock_attach();
    }
    m.set8(S_ACTIVE, 0);
    m.set8(S_DOCKING, 0);
    m.buf.fill(0, 0xd96, 0xd96 + 30);
    for (const a of [0xd6c, 0xd70, 0xd74]) {
      m.set32(a, 0);
    }
    m.set16(S_TIMER, 0x384);
    this.spawn_debris_world(v);
  }

  /** $10E4A (Resources screen, new click): send the supply drone from the stockpile
   * island with the queued stores and fuel. Returns false (with a message) when it can't:
   * a transfer is running, the carrier isn't at the stockpile island, the drone is still
   * returning ([$D44]), or nothing is queued. */
  cmd_supply_transfer(): boolean {
    const m = this.mem;
    const c = PLAYER_CARRIER;
    const a = SUPPLY_DRONE;
    if (m.u8(S_ACTIVE)) {
      this.events.push(["message", "Transfer already in progress", this._ping()]);
      return false;
    }
    const buf = m.u32(c + 0x3c);
    if (!buf || m.u8(buf + 1) !== m.u16(STOCKPILE)) {
      this.events.push(["message", "Not at stockpile island", this._ping()]);
      return false;
    }
    if (m.u16(S_TIMER)) {
      this.events.push(["message", "Supply drone not available", this._ping()]);
      return false;
    }
    let queued = 0;
    for (let k = 0; k < 15; k++) {
      queued = queued || m.u16(0xd96 + 2 * k);
    }
    for (const q of [0xd6c, 0xd74, 0xd70]) {
      queued = queued || m.u32(q);
    }
    if (!queued) {
      this.events.push(["message", "Nothing to transfer", this._ping()]);
      return false;
    }
    this.init_world_object(a, 0x23);
    m.set16(NEW_INDEX, 0x10);
    this.reset_pair_schedules();
    m.set16(a + 0x38, 0x7d0);
    for (const [axis, half] of [[0x0a, 0x30], [0x12, 0x32]]) {
      const origin = m.u32(buf + (axis === 0x0a ? 2 : 0x0a));
      const d = s32(m.u32(c + axis) - origin);
      const h = m.u16(buf + half);
      let p: number;
      if (h < d) {
        p = h + 0x258;
      } else if (-h > d) {
        p = -h - 0x258;
      } else {
        p = 0;
      }
      m.set32(a + axis, p + origin);
    }
    m.set32(a + 0x0e, -60);
    m.set16(a + 6, 0x8000);
    m.set16(a + 0x24, 0);
    m.set16(S_STATE, 0);
    m.set8(S_ACTIVE, 0xff);
    m.set16(S_DONE, 0);
    this.events.push(["message", "Resupply drone launched", this._ping()]);
    return true;
  }
}

// Test-only code (ROUTINES, SCENARIOS, CHECKS, scenario_*, check_*, _enemy_alongside,
// _oracle_call) lives in tools/stunits_enemy.py.
