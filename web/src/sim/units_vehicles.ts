/**
 * Unit layer, part of tools/stunits.py: Mantas and Walruses out of the hangar: flight ($1CDA2), driving ($1DCFC), the Manta lift ($1EAA0) and dock sequences, loadouts ($1ED46), firing ($1F9E8, $1FB6E), destruction ($19188, $191F2), and the hangar commands (launch, prepare, refuel, replace).
 *
 * TypeScript port of tools/stunits_vehicles.py (the verified reference). In Python this is a
 * mixin combined into stunits.UnitState ahead of stunits.UnitCore, so its methods replace the
 * core's stubs and hooks; here it is the last class of the chain, UnitState extends EnemyUnits.
 *
 * Records ($34 bytes; Mantas at $496, Walruses at $570):
 *   Manta  +0 flags (0 auto, 1 waypoint, 2 report arrival, 4 crashing, 5 take-off ordered,
 *             6/7 self-centre pitch/rudder), +1 status (0 ready, 1 repair, 2 preparing,
 *             3 on the lift, 8 out, $FF destroyed), +2 rudder, +4 pitch, +6 speed, +8 target
 *             speed, +$A fuel, +$C missiles, +$E payload (4 bomb, $A comms pod), +$10 laser,
 *             +$14 health, +$1A weapon (0 laser, 1 missile, else payload), +$1C/+$1D auto mode
 *             and step, +$1E/+$22/+$26 waypoint x/y/z, +$2A cruise speed, +$2C wing toggle,
 *             +$2E telemetry (1 weak, 2 lost), +$30 damage-message hold, +$33.
 *   Walrus +0 flags (0 auto, 1 waypoint, 2 report, 3 beached, 4 steering a guided missile,
 *             5 rudder turns the turret, 6/7 self-centre turret/rudder), +1 status, +2 rudder,
 *             +4 speed, +6 target speed, +8 fuel, +$A laser shots (-1 none), +$C missiles,
 *             +$E payload (8 drop, 9 ACCB, $B fuel pod), +$14 health, +$18 weapon, +$1A/+$1B
 *             auto mode and step, +$1C/+$20 waypoint x/z, +$24 cruise speed, +$26/+$28 loadout
 *             codes, +$2C telemetry, +$2E missile guidance hold.
 * Manta auto modes: 0 land on the carrier, 1 on deck, 2 deck take-off, 3 fly to waypoint,
 * 4 land on a runway, 5 landed, 6 runway take-off. Walrus: 0 dock, 1 docked, 2 launch, 3 drive
 * to waypoint. UI-only side effects (gauges $7A1A/$7A46/$7A56, redraw flags, panels $B418,
 * sounds $20B90) are left out; messages go to self.events.
 */
import { NotPorted, s16, s32, u32, w16, shr, pyDiv } from "./mem.ts";
import type { Region } from "./mem.ts";
import {
  OWNERS, ISLAND_TABLE, ISLAND_SIZE, G_ISLAND_PTR, G_ECONOMY_PTR, G_BUFFER_PTR, ECONOMY, ECONOMY_SIZE,
  OBJ_BUFFERS, SLOT_BASE, SLOT_SIZE, T_COMMAND_CENTRE_CONSTRUCTION, RADIUS, T_TREE_FIRST, T_TREE_LAST,
  SPOT_CUT_LIST,
} from "./world.ts";
import {
  UnitCore, UNIT_REGIONS, UNIT_INPUT_REGIONS, OBJECTS, OBJECT_SIZE, C_NEAR_UNIT, C_SPEED, SLOW_TICK, SCREEN,
  DOOR_STATE, DOOR_POS, DOCKED_WALRUS, LIFT_STATE, NEW_INDEX, SLOT_ENTRY,
} from "./units_core.ts";
import { EnemyUnits } from "./units_enemy.ts";

export const MANTA_RECS = 0x496, WALRUS_RECS = 0x570, REC = 0x34;
export const MANTA_OBJ = 2, WALRUS_OBJ = 6;
export const CARRIER_OBJ = 0x5E95A;
export const LOADOUT = 0xCAC4;                    // 26-byte items: +2 stock counter, +$16 fitting handler
export const RAMP_FORWARD = 0x1E5E0, RAMP_REVERSE = 0x1E638;
export const LIFT_POS = 0xDD6, LIFT_BAY = 0xDD0, LIFT_NEXT = 0xDC4;
export const COMMS_POD = 0xDE0, COMMS_STATUS = 0xB0A;
export const VIEWED = 0x9FE, POINTER_MODE = 0x457, FIRE_KEYS = 0x881, FIRE_INHIBIT = 0x8F1;
export const MOUSE_DX = 0x1268, MOUSE_DY = 0x126A, KEY_UP = 0x828, KEY_DOWN = 0x838;
export const MANTA_LANDING = 0x1232, WALRUS_DOCKING = 0x1233;
export const LOCK_TIMER = 0x116C, LOCK_TARGET = 0x1168;
export const FUEL_MANTA = 0xD00, FUEL_WALRUS = 0xD04, FUEL_MAX = 0xFFFE0;
export const STOCK_MANTA = 0xCD0, STOCK_WALRUS = 0xCEA;

// State these routines read or write that the other regions don't cover.
for (const _region of [
  ["aircraft and AAV fuel", 0xD00, 8],
  ["hangar selection $48E", 0x48E, 8],
  ["hangar panels $DC0", 0xDC0, 4],
  ["fire inhibit $8F1", FIRE_INHIBIT, 1],
  ["missile lock $1162", 0x1162, 12],
  ["loadout table $CAC4", LOADOUT, 12 * 26],
  ["walrus ramp tables", RAMP_FORWARD, 0x1E674 - RAMP_FORWARD],
] as Region[]) {
  if (!UNIT_REGIONS.some(([n, a, l]: Region) => n ===_region[0] && a === _region[1] && l === _region[2])) {
    UNIT_REGIONS.push(_region);
  }
}
for (const _name of ["hangar selection $48E", "fire inhibit $8F1", "loadout table $CAC4", "walrus ramp tables"]) {
  UNIT_INPUT_REGIONS.add(_name);
}

export const LOADOUT_HANDLERS: Map<number, string | null> = new Map<number, string | null>([
  [0x1ED5A, "missiles 1"], [0x1ED60, "missiles 2"], [0x1ED66, "missiles 3"],
  [0x1ED6C, "bomb"], [0x1ED74, "laser"], [0x1ED7A, "avatar"], [0x1ED82, "payload"],
  [0x1ED88, "comms pod"], [0xCBFC, null],
]);

export function _clamp255(v: number): number {
  v = s16(v);
  return v > 0xFF ? 0xFF : v < -0xFF ? w16(-0xFF) : w16(v);
}

/** divs.w quotient (truncates towards zero); the callers never overflow. */
export function _divs(n: number, d: number): number {
  const q = pyDiv(Math.abs(n), Math.abs(d));
  return w16((n < 0) !== (d < 0) ? -q : q);
}

function hex(v: number): string {
  return v.toString(16).toUpperCase();
}

type Step = (a1: number, a4: number) => void;

/** The whole unit layer: UnitCore plus the carrier, enemy and vehicle parts. */
export class UnitState extends EnemyUnits {
  // ---- shared ---------------------------------------------------------------------------
  /** $1CDA2 (Mantas) / $1DCFC (Walruses): every bay whose vehicle is out flies or drives. */
  bays_step(cooldown: number, first: number, records: number, kind: string): void {
    const m = this.mem;
    if (m.u16(cooldown)) {
      m.set16(cooldown, m.u16(cooldown) - 1);
    }
    m.set16(0x6F0, first);
    const step: Step = kind === "Manta"
      ? (a1, a4) => this._manta_step(a1, a4)
      : (a1, a4) => this._walrus_step(a1, a4);
    for (let k = 0; k < 4; k++) {
      step(records + REC * k, this.obj(first + k));
      m.set16(0x6F0, m.u16(0x6F0) + 1);
    }
  }

  manta_bays_step(): void {
    this.bays_step(0x940, MANTA_OBJ, MANTA_RECS, "Manta");
  }

  walrus_bays_step(): void {
    this.bays_step(0x942, WALRUS_OBJ, WALRUS_RECS, "Walrus");
  }

  _msg(text: string): void {
    this.events.push(["message", text]);
  }

  /** |dx|, |dz| by neg.l, the larger by a signed compare. */
  _max_abs(dx: number, dz: number): number {
    const a = UnitCore.absl(dx), b = UnitCore.absl(dz);
    return a >= b ? a : b;
  }

  _cheb(a: number, b: number): number {
    const m = this.mem;
    return this._max_abs(m.u32(a + 0x0A) - m.u32(b + 0x0A), m.u32(a + 0x12) - m.u32(b + 0x12));
  }

  /** $1E72C: self-centre the word at a. */
  _centre(a: number): number {
    const v = UnitCore.self_centre(this.mem.u16(a));
    this.mem.set16(a, v);
    return v;
  }

  /** $1DBE0 / $1E3A0: position = carrier + local (+$1C, +$1E) turned by its heading. */
  _deck_pos(o: number): void {
    const m = this.mem, c = CARRIER_OBJ;
    this.set_matrix(m.u16(c + 6));
    const [x, z] = this.matrix_rotate(m.u16(o + 0x1C), m.u16(o + 0x1E));
    m.set32(o + 0x0A, x + m.u32(c + 0x0A));
    m.set32(o + 0x12, z + m.u32(c + 0x12));
  }

  /** $1CD40: hand the pointer back when the viewed vehicle is taken over by the autopilot. */
  _viewed_pointer(o: number): void {
    if (this.mem.u32(VIEWED) === o) {
      this.mem.set8(POINTER_MODE, 0xFF);
    }
  }

  /** Range to the carrier, or to a deployed comms pod while long-range comms work:
   * beyond 200000 the signal is weak (1), beyond 260000 lost (2). A Walrus out of range
   * is destroyed (False). */
  _telemetry(rec: number, o: number, d5: number, field: number, kind: string): boolean {
    const m = this.mem;
    let d0 = this._cheb(o, CARRIER_OBJ);
    if (d0 <= 200000) {
      m.set8(C_NEAR_UNIT, 0xFF);
      return true;
    }
    if (m.u8(COMMS_STATUS) && m.u32(COMMS_POD)) {
      const pod = m.u32(COMMS_POD);
      if (pod === o) {
        return true;
      }
      d0 = this._cheb(o, pod);
      if (d0 <= 200000) {
        return true;
      }
    }
    const bay = m.u16(o + 0x1A) + 1;
    if (d5 === 0) {
      this._msg(`${kind} ${bay} telemetry signal weak`);
    }
    m.set16(rec + field, 1);
    if (d0 <= 0x3F7A0) {
      return true;
    }
    if (kind === "Walrus") {
      this._msg(`${kind} ${bay} telemetry lost`);
      m.set16(rec + field, 2);
      this.vehicle_destroyed(kind, o);
      return false;
    }
    if (d5 !== 2) {
      this._msg(`${kind} ${bay} telemetry lost`);
    }
    m.set16(rec + field, 2);
    return true;
  }

  /** find_free_object, then $192DA/$195DA, and the shooter's pair schedule held off. */
  _new_object(t: number): number | null {
    const m = this.mem;
    const b = this.find_free_object();
    if (b == null) {
      return null;
    }
    this.init_world_object(b, t);
    this.reset_pair_schedules();
    m.set8(b + s16(w16(2 * m.u16(0x6F0) + 0xC0)), 0xFF);
    return b;
  }

  /** $1A0C8. */
  _copy_angles(src: number, dst: number): void {
    for (const o of [4, 6, 8]) {
      this.mem.set16(dst + o, this.mem.u16(src + o));
    }
  }

  /** $A454/$A45E/$A468 then $A5BC, $A4F4, $A558: a body offset (x, y, 0) turned by the
   * object's roll, pitch and heading. */
  _offset_from(o: number, x: number, y: number): [number, number, number] {
    const m = this.mem;
    this._set_pair(0xA68, m.u16(o + 4));
    this._set_pair(0xA6C, m.u16(o + 6));
    this._set_pair(0xA70, m.u16(o + 8));
    const [x1, y1] = this._rot(0xA70, x, y, false);
    const [y2, z2] = this._rot(0xA68, y1, 0, false);
    const [x3, z3] = this._rot(0xA6C, x1, z2, true);
    return [x3, y2, z3];
  }

  _place(b: number, o: number, dx: number, dy: number, dz: number): void {
    const m = this.mem;
    m.set32(b + 0x0A, dx + m.u32(o + 0x0A));
    m.set32(b + 0x12, dz + m.u32(o + 0x12));
    m.set32(b + 0x0E, dy + m.u32(o + 0x0E));
  }

  /** $1EDE8 (1, opening) / $1EDF2 (3, closing). */
  _door(state: number): void {
    this.mem.set16(DOOR_STATE, state);
    this.dock_attach();
  }

  // ---- destruction -------------------------------------------------------------------------
  /** $19188 (Manta) / $191F2 (Walrus): drop the comms pod link, remove the object and
   * release its island buffer, cancel its landing/docking, mark the bay destroyed ($FF),
   * and empty the lift or dock if it was on it. */
  vehicle_destroyed(kind: string, v: number): void {
    const m = this.mem;
    const manta = kind === "Manta";
    this._msg(`${kind} ${m.u16(v + 0x1A) + 1} has been destroyed!`);
    if (m.u32(COMMS_POD) === v) {
      m.set32(COMMS_POD, 0);
    }
    const old = m.u16(v);
    m.set16(v, 0);
    this.release_island_buffer(v);
    const bay = m.u16(v + 0x1A);
    const [flag, current] = manta ? [MANTA_LANDING, 0x6DE] : [WALRUS_DOCKING, 0x6E0];
    if (m.u8(flag) && bay === m.u16(current)) {
      m.set8(flag, 0);
    }
    const rec = (manta ? MANTA_RECS : WALRUS_RECS) + s16(w16(bay * REC));
    m.set8(rec, 0);
    m.set8(rec + 1, 0xFF);
    if (old & 4) {
      m.set16(manta ? LIFT_BAY : DOCKED_WALRUS, 0xFFFF);
      this.dock_attach();
    }
  }

  // ---- Manta --------------------------------------------------------------------------------
  _manta_step(a1: number, a4: number): void {
    const m = this.mem;
    if (m.s8(a1 + 1) < 3 || !(m.u8(a4 + 1) & 1)) {
      return;
    }
    if (m.u16(a1 + 0x14) === 0) {
      return this._manta_kill(a1, a4);
    }
    if (m.u16(a1 + 0x30)) {
      m.set16(a1 + 0x30, m.u16(a1 + 0x30) - 1);
    }
    const d5 = m.u16(a1 + 0x2E);
    m.set16(a1 + 0x2E, 0);
    if (!(m.u8(a4 + 1) & 4)) {
      this._telemetry(a1, a4, d5, 0x2E, "Manta");
    }
    if (m.u8(a1) & 1) {
      return this._manta_auto(a1, a4);
    }
    if (a4 === m.u32(VIEWED)) {
      if (!m.u8(POINTER_MODE)) {
        m.set8(a1, m.u8(a1) & 0x3F);
        m.set16(a1 + 2, _clamp255(m.u16(a1 + 2) + m.u16(MOUSE_DX)));
        m.set16(MOUSE_DX, 0);
        m.set16(a1 + 4, _clamp255(m.u16(a1 + 4) + m.u16(MOUSE_DY)));
        m.set16(MOUSE_DY, 0);
        const keys = m.u8(FIRE_KEYS);
        m.set8(FIRE_KEYS, keys & 0xFE);
        if (keys & 1 && !m.u8(FIRE_INHIBIT)) {
          this.manta_fire(a1, a4);
        }
      }
      const d0 = m.u16(a1 + 6);
      if (m.u8(KEY_UP)) {
        m.set16(a1 + 8, d0 + 0x10);
      } else if (m.u8(KEY_DOWN)) {
        m.set16(a1 + 8, d0 - 0x10);
      }
    }
    if (s32(m.u32(a4 + 0x0E)) > 0xD48 && !(m.u16(a1 + 4) & 0x8000)) {
      m.set8(a1, m.u8(a1) | 0x40);
    }
    this._manta_terrain(a1, a4);
  }

  /** $1DC4C. */
  _manta_kill(a1: number, a4: number): void {
    this.spawn_debris_world(a4);
    this.vehicle_destroyed("Manta", a4);
  }

  /** $1DC40: health -= d (destroyed at 0 or below); True while the object is live. */
  _manta_damage(a1: number, a4: number, d: number): boolean {
    const m = this.mem;
    const h = m.s16(a1 + 0x14);
    if (h > s16(d)) {
      m.set16(a1 + 0x14, h - d);
    } else {
      m.set16(a1 + 0x14, 0);
      this._manta_kill(a1, a4);
    }
    return (m.u8(a4 + 1) & 1) !== 0;
  }

  /** $1CF9A: ground contact: below 35 hit the ground; between 35 and 150 inside an
   * island's box, the runway (buffer entry 1) lands it if lined up and slow, else it
   * crashes. */
  _manta_terrain(a1: number, a4: number): void {
    const m = this.mem;
    if (m.u8(a1) & 1 && m.u8(a1 + 0x1C) >= 4) {
      if (m.u16(a1 + 0x2E) === 2) {
        return this._manta_crash_start(a1, a4);
      }
      return this._manta_normal(a1, a4);
    }
    const y = s32(m.u32(a4 + 0x0E));
    if (y >= 150) {
      return this._manta_fuel(a1, a4);
    }
    if (y <= 35) {
      if (!this._manta_damage(a1, a4, 2000)) {
        return;
      }
      return this._manta_crash_tail(a1, a4);
    }
    const buf = m.u32(a4 + 0x3C);
    if (!buf) {
      return this._manta_fuel(a1, a4);
    }
    const dx = s32(m.u32(a4 + 0x0A) - m.u32(buf + 2));
    if (s32(UnitCore.absl(dx) - m.s16(buf + 0x30)) >= 0) {
      return this._manta_fuel(a1, a4);
    }
    const dz = s32(m.u32(a4 + 0x12) - m.u32(buf + 0x0A));
    if (s32(UnitCore.absl(dz) - m.s16(buf + 0x32)) >= 0) {
      return this._manta_fuel(a1, a4);
    }
    const a5 = buf + 0x52;
    if (m.u16(a5 + 2) !== 0x24) {
      return this._manta_crash(a1, a4, 500);
    }
    this.set_matrix(w16(-m.u16(a5 + 6)));
    const [x, z] = this.matrix_rotate(dx - m.u16(a5 + 0x0A), dz - m.u16(a5 + 0x0E));
    if (UnitCore.absw(x) > 200 || z < -4000 || z > 2000) {
      return this._manta_crash(a1, a4, 500);
    }
    if (UnitCore.absw(m.u16(a1 + 2)) > 120 || UnitCore.absw(m.u16(a1 + 4)) > 80) {
      return this._manta_crash(a1, a4, 100);
    }
    const hd = w16(m.u16(a4 + 6) - m.u16(a5 + 6));
    if (hd === 0x8000 || UnitCore.absw(hd) > 0xFA0 || m.s16(a1 + 6) > 100) {
      return this._manta_crash(a1, a4, 100);
    }
    this._msg(`Manta ${m.u16(a4 + 0x1A) + 1} has landed on runway`);
    m.set8(a1, 1);
    m.set8(a1 + 0x1C, 4);
    m.set8(a1 + 0x1D, 0);
    this._viewed_pointer(a4);
    return this._manta_auto(a1, a4);
  }

  _manta_crash(a1: number, a4: number, d: number): void {
    const m = this.mem;
    if (!this._manta_damage(a1, a4, d)) {
      return;
    }
    if (s32(m.u32(a4 + 0x0E)) < 0x91) {
      m.set32(a4 + 0x0E, 0x91);
    }
    this._manta_crash_tail(a1, a4);
  }

  /** $1D0FE: bounce: nose up, rudder kicked at random. */
  _manta_crash_tail(a1: number, a4: number): void {
    const m = this.mem;
    m.set16(a1 + 4, UnitCore.turn_towards(100, m.u16(a1 + 4), 30));
    const kick = w16((this.rand() & 0x7F) - 0x40);
    m.set16(a1 + 2, _clamp255(m.u16(a1 + 2) + kick));
    this._manta_fuel(a1, a4);
  }

  /** $1D13C: fuel use on the slow tick (more when badly damaged), then $1D170. */
  _manta_fuel(a1: number, a4: number): void {
    const m = this.mem;
    if (m.u8(SLOW_TICK)) {
      let d0 = pyDiv((m.u16(a1 + 6) << 4) & 0xFFFF, 50);
      if (m.s16(a1 + 0x14) <= 500) {
        d0 += 0x100;
      }
      const f = s16(m.u16(a1 + 0x0A) - d0);
      m.set16(a1 + 0x0A, f >= 0 ? f : 0);
    }
    if (m.u8(a1) & 0x10) {
      return this._manta_crash_fall(a1, a4);
    }
    if (m.u16(a1 + 0x0A)) {
      if (m.u16(a1 + 0x2E) === 2) {
        return this._manta_crash_start(a1, a4);
      }
      return this._manta_normal(a1, a4);
    }
    this._msg(`Manta ${m.u16(a4 + 0x1A) + 1} has run out of fuel`);
    this._manta_crash_start(a1, a4);
  }

  /** $1D19E: out of fuel or telemetry: fall with the current velocity. */
  _manta_crash_start(a1: number, a4: number): void {
    const m = this.mem;
    const v = this.velocity_from_angles(a4, m.u16(a1 + 6));
    const offs = [0x20, 0x22, 0x24];
    for (let i = 0; i < 3; i++) {
      m.set16(a4 + offs[i], v[i]);
    }
    m.set8(a1, (m.u8(a1) | 0x10) & 0xFE);
    this._manta_crash_fall(a1, a4);
  }

  /** $1D1B4: gravity on the falling Manta, spinning nose down. */
  _manta_crash_fall(a1: number, a4: number): void {
    const m = this.mem;
    const vx = m.s16(a4 + 0x20), vz = m.s16(a4 + 0x24);
    let vy = m.s16(a4 + 0x22);
    vy -= 5;
    m.set16(a4 + 0x22, vy);
    m.set32(a4 + 0x0A, m.u32(a4 + 0x0A) + vx);
    m.set32(a4 + 0x0E, m.u32(a4 + 0x0E) + vy);
    m.set32(a4 + 0x12, m.u32(a4 + 0x12) + vz);
    m.set16(a4 + 8, m.u16(a4 + 8) + (m.u16(a1 + 2) & 0x8000 ? 0x7D0 : -0x7D0));
    m.set16(a4 + 4, UnitCore.turn_towards(0x3E80, m.u16(a4 + 4), 0x258));
  }

  /** $1D1F4: speed governor, self-centring, attitude from the controls, move. */
  _manta_normal(a1: number, a4: number): void {
    const m = this.mem;
    this._manta_governor(a1);
    for (const [bit, o] of [[0x80, 2], [0x40, 4]]) {
      if (m.u8(a1) & bit) {
        if (m.u16(a1 + o) === 0) {
          m.set8(a1, m.u8(a1) & ~bit);
        } else {
          m.set16(a1 + o, UnitCore.self_centre(m.u16(a1 + o)));
        }
      }
    }
    const rudder = m.s16(a1 + 2), pitch = m.s16(a1 + 4);
    m.set16(a4 + 8, rudder * -25);
    m.set16(a4 + 4, pitch * -14);
    m.set16(a4 + 6, m.u16(a4 + 6) + _divs(s16(rudder << 4), 6));
    this._manta_move(a1, a4);
  }

  /** $1D230: twice the speed far from everything (unless on the Manta screen), at most
   * 200 in the island's range (bit 9), altitude capped at 3500. */
  _manta_move(a1: number, a4: number): void {
    const m = this.mem;
    let d1 = m.u16(a1 + 6);
    if (!m.u32(a4 + 0x3C) && !m.u8(C_NEAR_UNIT) && m.u16(SCREEN) !== 0x1B) {
      d1 = w16(d1 * 2);
    }
    if (m.u8(a4) & 2 && s16(d1) > 200) {
      d1 = 200;
    }
    const [vx, vy, vz] = this.velocity_from_angles(a4, d1);
    m.set32(a4 + 0x0A, m.u32(a4 + 0x0A) + vx);
    const y = s32(m.u32(a4 + 0x0E) + vy);
    m.set32(a4 + 0x0E, y > 0xDAC ? 0xDAC : y);
    m.set32(a4 + 0x12, m.u32(a4 + 0x12) + vz);
  }

  /** $1DB5A: target speed within [50 (0 with the cheat or runway modes), health/16 + 50
   * capped at 250]; speed follows it by 4 up, 16 down. */
  _manta_governor(a1: number): void {
    const m = this.mem;
    let hi = s16((m.u16(a1 + 0x14) >> 4) + 0x32);
    let lo = m.u8(0x118A) ? 0 : 0x32;
    if (hi > 0xFA) {
      hi = 0xFA;
    }
    let d0 = m.s16(a1 + 8);
    if (m.u8(a1) & 1 && m.u8(a1 + 0x1C) >= 4) {
      lo = 0;
    }
    if (d0 > hi) {
      d0 = hi;
    } else if (d0 < lo) {
      d0 = lo;
    }
    m.set16(a1 + 8, d0);
    const d1 = m.u16(a1 + 6);
    if (d1 === w16(d0)) {
      return;
    }
    const step = w16(d1 - d0) & 0x8000 ? 4 : 0x10;
    m.set16(a1 + 6, UnitCore.turn_towards(d0, d1, step));
  }

  /** $1D702: rudder towards the bearing (error / div), then $1CF9A. */
  _manta_steer(a1: number, a4: number, dx: number, dz: number, div: number): void {
    const m = this.mem;
    const e = s16(this.atan2(dx, dz) - m.u16(a4 + 6));
    m.set16(a1 + 2, UnitCore.turn_towards(_clamp255(_divs(e, div)), m.u16(a1 + 2), 0x20));
    this._manta_terrain(a1, a4);
  }

  /** $1D642: no waypoint: circle at cruise 70, climbing when below 500. */
  _manta_hover(a1: number, a4: number): void {
    const m = this.mem;
    if (s32(m.u32(a4 + 0x0E)) > 500) {
      this._centre(a1 + 4);
    } else {
      m.set16(a1 + 4, UnitCore.turn_towards(0x96, m.u16(a1 + 4), 0x14));
    }
    m.set16(a1 + 8, 0x46);
    m.set16(a1 + 2, UnitCore.turn_towards(0x80, m.u16(a1 + 2), 0x10));
    this._manta_terrain(a1, a4);
  }

  /** $1D280. */
  _manta_auto(a1: number, a4: number): void {
    const mode = this.mem.u8(a1 + 0x1C);
    if (mode <= 6) {
      const modes: Step[] = [this._manta_land_carrier, this._manta_on_deck, this._manta_deck_takeoff,
        this._manta_fly, this._manta_runway_landing, this._manta_landed, this._manta_runway_takeoff];
      modes[mode].call(this, a1, a4);
    }
  }

  /** Mode 0: approach at altitude 570, match the carrier's motion, line up, settle on
   * the lift (local (0, 250)), descend to 180 (mode 1). */
  _manta_land_carrier(a1: number, a4: number): void {
    const m = this.mem, c = CARRIER_OBJ;
    this._centre(a4 + 4);
    this._centre(a4 + 8);
    this._manta_governor(a1);
    this._centre(a1 + 4);
    this._centre(a1 + 2);
    let sub = m.u8(a1 + 0x1D);
    const ch = m.u16(c + 6);
    if (sub === 0) {
      const d1 = s32(0x23A - m.u32(a4 + 0x0E));
      let d0 = 0;
      if (UnitCore.absl(d1) >= 0x14) {
        d0 = d1 & 0x8000 ? w16(-0x64) : 0x64;
      }
      m.set16(a1 + 4, UnitCore.turn_towards(d0, m.u16(a1 + 4), 0x14));
      const dx = s32(m.u32(c + 0x0A) - m.u32(a4 + 0x0A));
      const dz = s32(m.u32(c + 0x12) - m.u32(a4 + 0x12));
      const d3 = this._max_abs(dx, dz);
      if (u32(d3) >= 0x258) {
        m.set16(a1 + 8, s32(d3 - 0x258) >= 0xDAC ? 0xC8 : 0x41);
        return this._manta_steer(a1, a4, dx, dz, 10);
      }
      if (m.u16(a1 + 4)) {
        return this._manta_steer(a1, a4, dx, dz, 0x20);
      }
      m.set16(a4 + 0x1C, -dx);
      m.set16(a4 + 0x1E, -dz);
      this.set_matrix(ch);
      const [cx, cz] = this.matrix_rotate(0, m.u16(C_SPEED));
      this.set_matrix(m.u16(a4 + 6));
      const [mx, mz] = this.matrix_rotate(0, m.u16(a1 + 6));
      m.set16(a4 + 0x20, mx - cx);
      m.set16(a4 + 0x24, mz - cz);
      m.set8(a1 + 0x1D, 1);
      m.set16(a1 + 8, 0);
      sub = 1;
    }
    if (sub === 1) {
      m.set16(a4 + 0x1C, m.u16(a4 + 0x1C) + this._centre(a4 + 0x20));
      m.set16(a4 + 0x1E, m.u16(a4 + 0x1E) + this._centre(a4 + 0x24));
      m.set32(a4 + 0x0A, m.s16(a4 + 0x1C) + m.u32(c + 0x0A));       // $1DBBE
      m.set32(a4 + 0x12, m.s16(a4 + 0x1E) + m.u32(c + 0x12));
      if (w16(m.u16(a4 + 0x20) + m.u16(a4 + 0x24))) {
        return;
      }
      m.set8(a1 + 0x1D, 2);
      this.set_matrix(w16(-ch));
      const [x, z] = this.matrix_rotate(m.u16(a4 + 0x1C), m.u16(a4 + 0x1E));
      m.set16(a4 + 0x1C, x);
      m.set16(a4 + 0x1E, z);
    } else if (sub === 2) {
      m.set16(a4 + 6, UnitCore.turn_towards(ch, m.u16(a4 + 6), 0x200));
      this._deck_pos(a4);
      if (m.u16(a4 + 6) === ch) {
        m.set8(a1 + 0x1D, 3);
      }
    } else if (sub === 3) {
      if (m.u16(a4 + 0x1E) === 0xFA && m.u16(a4 + 0x1C) === 0) {
        m.set16(LIFT_BAY, m.u16(a4 + 0x1A));
        this.dock_attach();
        m.set8(a1 + 0x1D, 4);
        return;
      }
      m.set16(a4 + 0x1E, UnitCore.turn_towards(0xFA, m.u16(a4 + 0x1E), 10));
      m.set16(a4 + 0x1C, UnitCore.turn_towards(0, m.u16(a4 + 0x1C), 10));
      m.set16(a4 + 6, ch);
      this._deck_pos(a4);
    } else if (sub === 4) {
      let y = s32(m.u32(a4 + 0x0E) - 10);
      if (y <= 0xB4) {
        y = 0xB4;
        m.set16(a4 + 4, 0xFD4A);
        m.set8(a1 + 0x1D, 0);
        m.set8(a1 + 0x1C, 1);
        m.set8(MANTA_LANDING, 0);
      }
      m.set32(a4 + 0x0E, y);
      this._manta_on_deck(a1, a4);
    }
  }

  /** Mode 1: parked on the lift (12 fuel per slow tick for the systems). */
  _manta_on_deck(a1: number, a4: number): void {
    const m = this.mem;
    if (m.u8(SLOW_TICK)) {
      const f = s16(m.u16(a1 + 0x0A) - 0x0C);
      m.set16(a1 + 0x0A, f >= 0 ? f : 0);
    }
    m.set16(a4 + 6, m.u16(CARRIER_OBJ + 6));
    this._deck_pos(a4);
  }

  /** Mode 2: rise off the lift, roll forward, climb out over the bow (mode 3). */
  _manta_deck_takeoff(a1: number, a4: number): void {
    const m = this.mem;
    const sub = m.u8(a1 + 0x1D);
    if (sub > 2) {
      return;
    }
    if (sub === 0) {
      const y = u32(m.u32(a4 + 0x0E) + 2);
      m.set32(a4 + 0x0E, y);
      if (y >= 0xC8) {
        m.set8(a1 + 0x1D, 1);
        m.set16(a1 + 6, 0);
      }
    } else if (sub === 1) {
      let v = w16(m.u16(a1 + 6) + 8);
      if (v > 0x3C) {
        v = 0x3C;
      }
      m.set16(a1 + 6, v);
      const z = w16(m.u16(a4 + 0x1E) + v);
      m.set16(a4 + 0x1E, z);
      if (z >= 0x28A) {
        m.set8(a1 + 0x1D, 2);
      }
    } else {
      const p = s16(m.u16(a4 + 4) - 0xF0);
      m.set16(a4 + 4, p > -0x677 ? p : 0xF989);
      m.set32(a4 + 0x0E, m.u32(a4 + 0x0E) + 0x0A);
      const z = w16(m.u16(a4 + 0x1E) + m.u16(a1 + 6));
      m.set16(a4 + 0x1E, z);
      if (z >= 0x7D0) {
        m.set16(a1 + 2, 0);
        m.set16(a1 + 4, _divs(m.s16(a4 + 4), -14));
        m.set16(a1 + 6, m.u16(a1 + 6) + m.u16(C_SPEED));
        m.set16(a1 + 8, m.u16(a1 + 6));
        m.set8(a1 + 0x1C, 3);
        if (m.u8(a1 + 0x33)) {
          m.set8(a1, m.u8(a1) & 0xFE);
        }
        m.set8(a4 + 1, m.u8(a4 + 1) & ~4);
        m.set8(LIFT_BAY, 0xFF);
        this.dock_attach();
      }
    }
    m.set16(a4 + 6, m.u16(CARRIER_OBJ + 6));
    this._deck_pos(a4);
  }

  /** Mode 3: climb or dive to the waypoint altitude and steer to it; slow to 50 within
   * 2000; on arrival (600) the waypoint is cleared and it circles. */
  _manta_fly(a1: number, a4: number): void {
    const m = this.mem;
    if (!(m.u8(a1) & 2)) {
      return this._manta_hover(a1, a4);
    }
    const d1 = s32(m.u32(a1 + 0x22) - m.u32(a4 + 0x0E));
    let d0 = 0;
    if (UnitCore.absw(d1) >= 0x14) {
      d0 = s16(d1) > 0x96 ? 0x96 : s16(d1) < -0x96 ? w16(-0x96) : w16(d1);
    }
    m.set16(a1 + 4, UnitCore.turn_towards(d0, m.u16(a1 + 4), 10));
    const dx = s32(m.u32(a1 + 0x1E) - m.u32(a4 + 0x0A));
    const dz = s32(m.u32(a1 + 0x26) - m.u32(a4 + 0x12));
    const d3 = this._max_abs(dx, dz);
    m.set16(a1 + 8, m.u16(a1 + 0x2A));
    if (d3 <= 0x7D0) {
      m.set16(a1 + 8, 0x32);
    }
    if (d3 < 0x258) {
      m.set8(a1, m.u8(a1) & ~2);
      if (m.u8(a1) & 4) {
        this._msg(`Manta ${m.u16(a4 + 0x1A) + 1} has reached its destination`);
      }
      return this._manta_hover(a1, a4);
    }
    this._manta_steer(a1, a4, dx, dz, 10);
  }

  _runway(a4: number): [number, number] {
    const buf = this.mem.u32(a4 + 0x3C);
    if (!buf) {
      throw new NotPorted("Manta runway mode without an island buffer");
    }
    return [buf, buf + 0x52];
  }

  /** $1DAAE: offset to a point in the runway's frame; returns (dx, dz, |dx.w|, |dz.w|). */
  _runway_target(a4: number, a5: number, a6: number, px: number, pz: number): [number, number, number, number] {
    const m = this.mem;
    this.set_matrix(m.u16(a6 + 6));
    const [x, z] = this.matrix_rotate(px, pz);
    const dx = s32(s16(x + m.u16(a6 + 0x0A)) + m.u32(a5 + 2) - m.u32(a4 + 0x0A));
    const dz = s32(s16(z + m.u16(a6 + 0x0E)) + m.u32(a5 + 0x0A) - m.u32(a4 + 0x12));
    return [dx, dz, UnitCore.absw(dx), UnitCore.absw(dz)];
  }

  /** $1DAEC: head for the point, then $1DB02. */
  _manta_runway_end(a1: number, a4: number, dx: number, dz: number): void {
    const m = this.mem;
    m.set16(a4 + 6, UnitCore.turn_towards(this.atan2(dx, dz), m.u16(a4 + 6), 0x200));
    this._manta_governor(a1);
    this._manta_move(a1, a4);
  }

  /** Mode 4: roll to the runway end at 120, taxi to the bay's parking spot, turn round,
   * settle to 100 (mode 5). */
  _manta_runway_landing(a1: number, a4: number): void {
    const m = this.mem;
    const sub = m.u8(a1 + 0x1D);
    if (sub > 3) {
      return;
    }
    const [a5, a6] = this._runway(a4);
    if (sub === 0) {
      m.set16(a1 + 8, 0x40);
      for (const o of [a1 + 4, a1 + 2, a4 + 8, a4 + 4]) {
        this._centre(o);
      }
      m.set32(a4 + 0x0E, s16(UnitCore.turn_towards(0x78, m.u16(a4 + 0x10), 8)));
      let [dx, dz, d2, d3] = this._runway_target(a4, a5, a6, 0, 0xA5A);
      if (d2 <= 0x96 && d3 <= 0x96) {
        m.set8(a1 + 0x1D, 1);
        m.set32(a4 + 0x0E, 0x78);
        for (const o of [a1 + 2, a1 + 4, a4 + 4, a4 + 8]) {
          m.set16(o, 0);
        }
        dx = s32(dx & 0xFFFF0000);        // clr.w d0 (for the zero stores) clips the bearing
      }
      return this._manta_runway_end(a1, a4, dx, dz);
    }
    if (sub === 1) {
      m.set16(a1 + 8, 8);
      const bay = m.u16(a4 + 0x1A);
      const px = w16(bay * 0x258 - 0x384);
      const pz = bay === 0 || bay === 3 ? 0xCC6 : 0xE88;
      const [dx, dz, d2, d3] = this._runway_target(a4, a5, a6, px, pz);
      if (d2 <= 0x14 && d3 <= 0x14) {
        m.set8(a1 + 0x1D, 2);
        m.set16(a1 + 8, 0);
        m.set16(a1 + 6, 0);
      }
      return this._manta_runway_end(a1, a4, dx, dz);
    }
    if (sub === 2) {
      this._centre(a1 + 2);
      const h = m.u16(a4 + 6);
      const t = w16(m.u16(a6 + 6) + 0x8000);
      if (h === t) {
        m.set8(a1 + 0x1D, 3);
      }
      m.set16(a4 + 6, UnitCore.turn_towards(t, h, 0x400));
      return this._manta_move(a1, a4);
    }
    let y = s32(m.u32(a4 + 0x0E) - 1);
    if (y <= 0x64) {
      y = 0x64;
      m.set16(a4 + 4, 0xFD4A);
      m.set8(a1 + 0x1C, 5);
    }
    m.set32(a4 + 0x0E, y);
  }

  /** Mode 5: parked on the runway, refuelling; a take-off order (bit 5) starts mode 6
   * if the runway is intact. */
  _manta_landed(a1: number, a4: number): void {
    const m = this.mem;
    if (m.u16(a1 + 0x2E) === 2) {
      return this._manta_kill(a1, a4);
    }
    const f = w16(m.u16(a1 + 0x0A) + 0x40);
    m.set16(a1 + 0x0A, f & 0x8000 ? 0x7FFF : f);
    if (!(m.u8(a1) & 0x20)) {
      return;
    }
    m.set8(a1, m.u8(a1) & ~0x20);
    const [, a6] = this._runway(a4);
    if (m.u16(a6 + 2) === 0x24) {
      m.set8(a1 + 0x1C, 6);
      m.set8(a1 + 0x1D, 0);
      m.set8(a1 + 0x33, 0);
      this._msg(`Manta ${m.u16(a4 + 0x1A) + 1} is taking off from runway`);
    } else {
      this._msg("Runway damaged - cannot take off");
    }
  }

  /** Mode 6: taxi to the runway end, then accelerate down it and climb out (mode 3),
   * with a waypoint 6000 beyond the runway unless one is set. */
  _manta_runway_takeoff(a1: number, a4: number): void {
    const m = this.mem;
    const [a5, a6] = this._runway(a4);
    if (m.u8(a1 + 0x1D) === 0) {
      m.set16(a1 + 8, 0x0C);
      this._centre(a4 + 4);
      m.set32(a4 + 0x0E, s16(UnitCore.turn_towards(0x78, m.u16(a4 + 0x10), 1)));
      const [dx, dz, d2, d3] = this._runway_target(a4, a5, a6, 0, 0xA8C);
      if (d2 <= 0x64 && d3 <= 0x64) {
        m.set8(a1 + 0x1D, 1);
      }
      return this._manta_runway_end(a1, a4, dx, dz);
    }
    m.set32(a4 + 0x0E, s16(UnitCore.turn_towards(0x9B, m.u16(a4 + 0x10), 2)));
    const [dx, dz, d2, d3] = this._runway_target(a4, a5, a6, 0, 0xF448);
    if (d2 <= 0x96 && d3 <= 0x96) {
      m.set8(a1 + 0x1C, 3);
      if (m.u8(a1 + 0x33)) {
        m.set8(a1, m.u8(a1) & 0xFE);
      } else if (!(m.u8(a1) & 2)) {
        m.set8(a1, (m.u8(a1) | 2) & ~4);
        m.set16(a1 + 0x2A, 0x3C);
        m.set32(a1 + 0x22, s16(w16((m.u16(a4 + 0x1A) + 1) * 0x12C + 0x1F4)));
        this.set_matrix(m.u16(a6 + 6));
        const [x, z] = this.matrix_rotate(0, 0x1770);
        m.set32(a1 + 0x1E, s16(x + m.u16(a6 + 0x0A)) + m.u32(a5 + 2));
        m.set32(a1 + 0x26, s16(z + m.u16(a6 + 0x0E)) + m.u32(a5 + 0x0A));
      }
    } else {
      const d4 = this.atan2(dx, dz);
      const h = UnitCore.turn_towards(d4, m.u16(a4 + 6), 0x200);
      m.set16(a4 + 6, h);
      if (h === d4) {
        m.set16(a1 + 8, 0xC8);
      }
    }
    this._manta_governor(a1);
    this._manta_move(a1, a4);
  }

  // ---- Manta weapons ($1F9E8) ------------------------------------------------------------
  /** $1F9E8: weapon 0 laser, 1 missile, else the payload (a bomb). */
  manta_fire(a1: number, a4: number): void {
    const m = this.mem;
    const w = m.u16(a1 + 0x1A);
    if (w === 0) {
      return this._manta_laser(a1, a4);
    }
    if (w === 1) {
      if (!m.u16(a1 + 0x0C)) {
        return this._msg("No missile to launch");
      }
      if (!m.u16(0x940)) {
        this._manta_missile(a1, a4);
      }
      return;
    }
    if (m.u16(a1 + 0x0E) !== 4) {
      return this._msg("No cargo to drop");
    }
    m.set16(a1 + 0x0E, 0);
    m.set16(a1 + 0x12, m.u16(a1 + 0x12) - 0x258);
    m.set16(0xA80, m.u16(a1 + 6));
    const b = this._new_object(0x14);
    if (b === null) {
      return;
    }
    for (const o of [0x0A, 0x0E, 0x12]) {
      m.set32(b + o, m.u32(a4 + o));
    }
    m.set16(b + 6, m.u16(a4 + 6));
    m.set16(b + 4, 0x4000);
    const speed = m.s16(0xA80);
    const v = this.velocity_from_angles(a4, speed > 0x96 ? 0x96 : speed);
    const offs = [0x20, 0x22, 0x24];
    for (let i = 0; i < 3; i++) {
      m.set16(b + offs[i], v[i]);
    }
    this._msg("Bomb dropped");
  }

  /** $1FA9C: a type-16 bolt from 50 below the nose, cooldown 4. */
  _manta_laser(a1: number, a4: number): void {
    const m = this.mem;
    if (!m.u8(a1 + 0x10)) {
      return this._msg("No laser to fire");
    }
    if (m.u16(0x940)) {
      return;
    }
    m.set16(0x940, 4);
    const b = this._new_object(0x10);
    if (b === null) {
      return;
    }
    this._place(b, a4, ...this._offset_from(a4, 0, -0x32));
    this._copy_angles(a4, b);
    m.set16(b + 0x34, 2);
    m.set8(b + 0x1B, 0x14);
  }

  /** $1FFEA: the second press fires at the locked target ($1168) from alternating wings
   * (the first press scans the 3D view, $6290, which isn't modelled). */
  _manta_missile(a1: number, a4: number): void {
    const m = this.mem;
    if (m.u16(LOCK_TIMER) === 0) {
      throw new NotPorted("Manta missile lock-on scan ($6290, the 3D view)");
    }
    m.set16(LOCK_TIMER, 0);
    const b = this._new_object(0x11);
    if (b === null) {
      return;
    }
    const wing = (~m.u8(a1 + 0x2C)) & 0xFF;
    m.set8(a1 + 0x2C, wing);
    this._place(b, a4, ...this._offset_from(a4, wing === 0 ? 0x12C : -0x12C, 0));
    this._copy_angles(a4, b);
    m.set16(b + 8, 0);
    m.set32(b + 0x1E, m.u32(LOCK_TARGET));
    m.set16(0x940, m.s16(a1 + 6) < 0x78 ? 4 : 0x0A);
    m.set16(a1 + 0x0C, m.u16(a1 + 0x0C) - 1);
    m.set16(a1 + 0x12, m.u16(a1 + 0x12) - 0x37);
  }

  // ---- Walrus -------------------------------------------------------------------------------
  _walrus_step(a1: number, a4: number): void {
    const m = this.mem;
    if (m.s8(a1 + 1) < 3 || !(m.u8(a4 + 1) & 1)) {
      return;
    }
    if (m.u16(a1 + 0x30)) {
      m.set16(a1 + 0x30, m.u16(a1 + 0x30) - 1);
    }
    const d5 = m.u16(a1 + 0x2C);
    m.set16(a1 + 0x2C, 0);
    if (!this._telemetry(a1, a4, d5, 0x2C, "Walrus")) {
      return;
    }
    if (m.u8(a1) & 1) {
      return this._walrus_auto(a1, a4);
    }
    if (a4 === m.u32(VIEWED)) {
      if (m.u8(a1) & 0x10) {
        const a0 = m.u32(a4 + 0x20);
        if (!(OBJECTS <= a0 && a0 < OBJECTS + 64 * OBJECT_SIZE)) {
          throw new NotPorted(`Walrus guided missile pointer $${hex(a0)}`);
        }
        if (!(m.u8(a0 + 1) & 1) || w16(m.u16(a0 + 0x1A) - 4) !== m.u16(a4 + 0x1A)) {
          m.set8(a1, m.u8(a1) & ~0x10);
          this._walrus_fire_check(a1, a4);
        } else if (!m.u8(POINTER_MODE)) {
          m.set8(FIRE_KEYS, m.u8(FIRE_KEYS) & 0xFE);
          m.set8(a1, m.u8(a1) & 0x3F);
          m.set16(a0 + 0x1E, _clamp255(m.u16(a0 + 0x1E) + m.u16(MOUSE_DX)));
          m.set16(MOUSE_DX, 0);
          m.set16(a0 + 0x20, _clamp255(m.u16(a0 + 0x20) + m.u16(MOUSE_DY)));
          m.set16(MOUSE_DY, 0);
          const t = m.u16(a1 + 0x2E);
          if (t >= 0x64) {
            m.set16(a1 + 0x2E, t - 8);
          }
        }
      } else if (!m.u8(POINTER_MODE)) {
        m.set8(a1, m.u8(a1) & 0x3F);
        m.set16(a1 + 2, _clamp255(m.u16(a1 + 2) + m.u16(MOUSE_DX)));
        m.set16(MOUSE_DX, 0);
        m.set16(MOUSE_DY, 0);
        this._walrus_fire_check(a1, a4);
      }
      const d0 = m.u16(a1 + 4);
      if (m.u8(KEY_UP)) {
        m.set16(a1 + 6, d0 + 8);
      } else if (m.u8(KEY_DOWN)) {
        m.set16(a1 + 6, d0 - 8);
      }
    }
    this._walrus_shore(a1, a4);
  }

  /** $1DF12. */
  _walrus_fire_check(a1: number, a4: number): void {
    const m = this.mem;
    const keys = m.u8(FIRE_KEYS);
    m.set8(FIRE_KEYS, keys & 0xFE);
    if (keys & 1 && !m.u8(FIRE_INHIBIT)) {
      this.walrus_fire(a1, a4);
    }
  }

  /** $1DF46: within 650 of the island box edge the beach ramp sets height and pitch. */
  _walrus_shore(a1: number, a4: number): void {
    const m = this.mem;
    const buf = m.u32(a4 + 0x3C);
    if (buf) {
      const dx = s32(m.u32(a4 + 0x0A) - m.u32(buf + 2));
      const dz = s32(m.u32(a4 + 0x12) - m.u32(buf + 0x0A));
      const d2 = s32(UnitCore.absl(dx) - m.s16(buf + 0x30));
      const d3 = s32(UnitCore.absl(dz) - m.s16(buf + 0x32));
      if (d2 <= 0x28A && d3 <= 0x28A && (d2 > -0x28A || d3 > -0x28A)) {
        return this._walrus_ramp(a1, a4, dx, dz, d2, d3);
      }
    }
    this._walrus_off_ramp(a1, a4);
  }

  /** $1DF7C. */
  _walrus_off_ramp(a1: number, a4: number): void {
    this.mem.set8(a1, this.mem.u8(a1) & ~8);
    this._walrus_drive(a1, a4);
  }

  /** $1E5BA: past 130 into the ramp the height drops from 15 (1 per 10, at most 30). */
  _ramp_height(a4: number, d5: number): void {
    let d0 = w16(d5 - 0x82);
    if (d0 & 0x8000) {
      return;
    }
    d0 = Math.min(s16(d0), 0x12C);
    this.mem.set32(a4 + 0x0E, s16(0x0F - pyDiv(d0, 10)));
  }

  /** $1E4D0: the nearer box edge gives the ramp direction; facing up it (within $4000)
   * or backing down it, the heading snaps towards it and height/pitch come from a table
   * indexed by the depth into the ramp. */
  _walrus_ramp(a1: number, a4: number, dx: number, dz: number, d2: number, d3: number): void {
    const m = this.mem;
    let d0: number;
    if (s16(d3) > s16(d2)) {
      d2 = d3;
      d0 = !(dz & 0x8000) ? 0x8000 : 0;
    } else {
      d0 = !(dx & 0x8000) ? 0xC000 : 0x4000;
    }
    const d5 = w16(d2);
    const h = m.u16(a4 + 6);
    const diff = w16(h - d0);
    const facing = diff !== 0x8000 && UnitCore.absw(diff) <= 0x4000;
    let idx: number, table: number;
    if (facing) {
      const nh = UnitCore.turn_towards(d0, h, 0x400);
      this._ramp_height(a4, d5);
      const q = s16(_divs(s16(-s16(d5)), 10));
      if (q >= 0x10) {
        return this._walrus_off_ramp(a1, a4);
      }
      m.set16(a4 + 6, nh);
      idx = w16(q + 6);
      table = RAMP_FORWARD;
    } else {
      const nh = UnitCore.turn_towards(w16(d0 + 0x8000), h, 0x400);
      this._ramp_height(a4, d5);
      const q = s16(_divs(s16(d5), 10));
      if (q >= 7) {
        return this._walrus_off_ramp(a1, a4);
      }
      m.set16(a4 + 6, nh);
      idx = w16(q + 8);
      table = RAMP_REVERSE;
    }
    if (!(idx & 0x8000)) {
      const e = table + s16(w16(idx * 4));
      m.set32(a4 + 0x0E, m.s16(e));
      m.set16(a4 + 4, m.u16(e + 2));
    }
    if (!(m.u8(a1) & 8)) {
      this._msg(`Walrus ${m.u16(a4 + 0x1A) + 1} has encountered a beach`);
    }
    m.set8(a1, (m.u8(a1) | 0xC8) & 0xEF);
    this._viewed_pointer(a4);
    this._walrus_drive(a1, a4);
  }

  /** $1DF80: wake roll, fuel, speed governor, self-centring, rudder, move. */
  _walrus_drive(a1: number, a4: number): void {
    const m = this.mem;
    let d0 = w16(m.u32(a4 + 0x0E) - 0x0F);
    let d1 = 0x200;
    if (d0 & 0x8000) {
      d1 = w16(((s16(d0) * 0x1111) >> 8) + 0x200);
    }
    m.set16(a4 + 0x24, d1);
    if (m.u8(SLOW_TICK)) {
      const sp = m.s16(a1 + 4);
      d0 = pyDiv((Math.max(sp, 0) << 4) & 0xFFFF, 0x50);
      if (m.s16(a1 + 0x14) <= 500) {
        d0 += 0x80;
      }
      const f = m.u16(a1 + 8);
      if (f) {
        if (f > w16(d0)) {
          m.set16(a1 + 8, f - d0);
        } else {
          this._msg(`Walrus ${m.u16(a4 + 0x1A) + 1} has run out of fuel`);
          m.set16(a1 + 8, 0);
        }
      }
    }
    this._walrus_governor(a1, a4);
    if (m.u8(a1) & 0x80) {
      if (m.u16(a1 + 2) === 0) {
        m.set8(a1, m.u8(a1) & ~0x80);
      } else {
        m.set16(a1 + 2, UnitCore.self_centre(m.u16(a1 + 2)));
      }
    }
    if (m.u8(a1) & 0x40) {
      const t = m.u16(a4 + 0x26);
      if (t === 0) {
        m.set8(a1, m.u8(a1) & ~0x40);
      } else {
        m.set16(a4 + 0x26, UnitCore.turn_towards(0, t, 0x400));
      }
    }
    const turn = _divs(s16(m.u16(a1 + 2) << 4), 6);
    const o = m.u8(a1) & 0x20 ? a4 + 0x26 : a4 + 6;
    m.set16(o, m.u16(o) + turn);
    const [vx, vz] = this.yaw_vector(a4, m.u16(a1 + 4));
    m.set32(a4 + 0x0A, m.u32(a4 + 0x0A) + vx);
    m.set32(a4 + 0x12, m.u32(a4 + 0x12) + vz);
  }

  /** $1E674: target speed 0..health/32 (at most 96, 48 when below sea level), 10 on a
   * beach, 0 without fuel; speed follows by 2 up, 8 down. */
  _walrus_governor(a1: number, a4: number): void {
    const m = this.mem;
    let d0: number;
    if (m.u16(a1 + 8) === 0) {
      d0 = 0;
      m.set16(a1 + 6, 0);
    } else if (m.u8(a1) & 8) {
      d0 = 0x0A;
    } else {
      let d1 = m.u16(a1 + 0x14) >> 5;
      if (d1 > 0x60) {
        d1 = 0x60;
      }
      if (s32(m.u32(a4 + 0x0E)) < 0 && d1 > 0x30) {
        d1 = 0x30;
      }
      d0 = m.s16(a1 + 6);
      d0 = d0 > d1 ? d1 : d0 < 0 ? 0 : d0;
      m.set16(a1 + 6, d0);
    }
    const d1 = m.u16(a1 + 4);
    if (d1 === w16(d0)) {
      return;
    }
    const step = w16(d1 - d0) & 0x8000 ? 2 : 8;
    m.set16(a1 + 4, UnitCore.turn_towards(d0, d1, step));
  }

  /** Rudder towards (dx, dz): error/32, clamped; target speed 0 if more than $400 off
   * (when stop). Returns the error. */
  _walrus_steer(a1: number, a4: number, dx: number, dz: number, stop: boolean = true): number {
    const m = this.mem;
    const e = w16(this.atan2(dx, dz) - m.u16(a4 + 6));
    if (stop && UnitCore.absw(e) > 0x400) {
      m.set16(a1 + 6, 0);
    }
    m.set16(a1 + 2, UnitCore.turn_towards(_clamp255(s16(e) >> 5), m.u16(a1 + 2), 0x20));
    return e;
  }

  /** $1E060. */
  _walrus_auto(a1: number, a4: number): void {
    const mode = this.mem.u8(a1 + 0x1A);
    if (mode === 0) {
      return this._walrus_dock(a1, a4);
    }
    if (mode === 1) {
      return;
    }
    if (mode === 2) {
      return this._walrus_launch(a1, a4);
    }
    if (mode === 3) {
      return this._walrus_waypoint(a1, a4);
    }
    throw new NotPorted(`Walrus auto mode ${mode} (the original corrupts its stack)`);
  }

  /** Mode 0: steer to the stern ramp (local (0, -1500)), enter, door open, drive in to
   * -650, door closes; when shut the Walrus is stowed and its weapons go back to stock. */
  _walrus_dock(a1: number, a4: number): void {
    const m = this.mem, c = CARRIER_OBJ;
    const sub = m.u8(a1 + 0x1B);
    const ch = m.u16(c + 6);
    if (sub > 3) {
      return;
    }
    if (sub === 0) {
      this.set_matrix(ch);
      const [x, z] = this.matrix_rotate(0, 0xFA24);
      const dx = s32(x + m.u32(c + 0x0A) - m.u32(a4 + 0x0A));
      const dz = s32(z + m.u32(c + 0x12) - m.u32(a4 + 0x12));
      const d3 = this._max_abs(dx, dz);
      if (u32(d3) > 0x4000) {
        m.set8(WALRUS_DOCKING, 0);
        m.set8(a1, m.u8(a1) & 0xFC);
        m.set16(a1 + 6, 0);
        this._msg(`Walrus ${m.u16(0x6E0) + 1} - Docking aborted`);
        return;
      }
      m.set16(a1 + 6, d3 <= 0x3E8 ? 0x18 : 0x3C);
      if (d3 < 0xC8) {
        this.set_matrix(w16(-ch));
        const [lx, lz] = this.matrix_rotate(m.u32(a4 + 0x0A) - m.u32(c + 0x0A),
          m.u32(a4 + 0x12) - m.u32(c + 0x12));
        m.set16(a4 + 0x1C, lx);
        m.set16(a4 + 0x1E, lz);
        m.set8(a1 + 0x1B, sub + 1);
        m.set16(DOCKED_WALRUS, m.u16(a4 + 0x1A));
        this.dock_attach();
        return;
      }
      this._walrus_steer(a1, a4, dx, dz);
      return this._walrus_shore(a1, a4);
    }
    if (sub === 1) {
      m.set16(a4 + 6, UnitCore.turn_towards(ch, m.u16(a4 + 6), 0x200));
      this._deck_pos(a4);
      if (m.u16(a4 + 6) === ch) {
        m.set8(a1 + 0x1B, sub + 1);
        this._door(1);
      }
      return;
    }
    if (sub === 2) {
      const z = w16(m.u16(a4 + 0x1E) + 0x20);
      m.set16(a4 + 0x1E, z);
      if (s16(z) >= -0x28A) {
        this._door(3);
        m.set8(a1 + 0x1B, sub + 1);
      }
      return this._deck_pos(a4);
    }
    if (m.u16(DOOR_POS) !== 0xD000) {
      return this._deck_pos(a4);
    }
    m.set8(a4 + 1, m.u8(a4 + 1) & 0xFE);
    m.set8(WALRUS_DOCKING, 0);
    m.set16(0xB98, m.u16(0xB98) + m.u16(a1 + 0x0C));
    m.set16(a1 + 0x0C, 0);
    if (!(m.u16(a1 + 0x0A) & 0x8000)) {
      m.set16(0xC00, m.u16(0xC00) + 1);
    }
    const code = m.s16(a1 + 0x0E);
    if (code > 0) {
      if (code > 11) {
        throw new NotPorted(`Walrus payload code ${code}`);
      }
      const stock = m.u32(LOADOUT + 26 * code + 2);
      m.set16(stock, m.u16(stock) + 1);
    }
    for (const o of [0x26, 0x28, 0x12]) {
      m.set16(a1 + o, 0);
    }
    m.set8(a1 + 1, 0);
    m.set8(DOCKED_WALRUS, 0xFF);
    this.dock_attach();
  }

  /** Mode 2: back out down the stern ramp (door closes at -1500), turn to the waypoint,
   * then drive (mode 3). */
  _walrus_launch(a1: number, a4: number): void {
    const m = this.mem, c = CARRIER_OBJ;
    const sub = m.u8(a1 + 0x1B);
    if (sub > 2) {
      return;
    }
    if (sub === 0) {
      let v = w16(m.u16(a1 + 4) + 8);
      if (v > 0x3C) {
        v = 0x3C;
      }
      m.set16(a1 + 4, v);
      const z = w16(m.u16(a4 + 0x1E) - v);
      m.set16(a4 + 0x1E, z);
      if (s16(z) <= -0x5DC) {
        m.set8(a1 + 0x1B, 1);
        this._door(3);
      }
      m.set16(a4 + 6, m.u16(c + 6) + 0x8000);
      return this._deck_pos(a4);
    }
    if (sub === 1) {
      const dx = s32(m.u32(a1 + 0x1C) - m.u32(a4 + 0x0A));
      const dz = s32(m.u32(a1 + 0x20) - m.u32(a4 + 0x12));
      const e = w16(this.atan2(dx, dz) - m.u16(a4 + 6));
      if (UnitCore.absw(e) <= 0x400) {
        m.set8(a1 + 0x1B, 2);
      }
      m.set16(a1 + 2, UnitCore.turn_towards(_clamp255(s16(e) >> 5), m.u16(a1 + 2), 0x20));
      m.set16(a1 + 6, 0);
      this._deck_pos(a4);
      return this._walrus_shore(a1, a4);
    }
    m.set16(a1 + 4, m.u16(a1 + 4) + m.u16(C_SPEED));
    m.set16(a1 + 6, m.u16(a1 + 4));
    m.set8(a4 + 1, m.u8(a4 + 1) & ~4);
    m.set8(a1 + 0x1A, 3);
    m.set8(a1 + 0x1B, 0);
    m.set8(DOCKED_WALRUS, 0xFF);
    m.set8(WALRUS_DOCKING, 0);
    this.dock_attach();
    if (m.u8(a1 + 0x33)) {
      m.set8(a1, m.u8(a1) & 0xFE);
    }
  }

  /** Mode 3: drive to the waypoint at cruise speed (32 within 1000); stop at 250. */
  _walrus_waypoint(a1: number, a4: number): void {
    const m = this.mem;
    if (m.u8(a1) & 2) {
      const dx = s32(m.u32(a1 + 0x1C) - m.u32(a4 + 0x0A));
      const dz = s32(m.u32(a1 + 0x20) - m.u32(a4 + 0x12));
      const d3 = this._max_abs(dx, dz);
      let d4 = m.s16(a1 + 0x24);
      if (d3 <= 0x3E8 && d4 > 0x20) {
        d4 = 0x20;
      }
      m.set16(a1 + 6, d4);
      if (d3 >= 0xFA) {
        this._walrus_steer(a1, a4, dx, dz);
        return this._walrus_shore(a1, a4);
      }
      m.set8(a1, m.u8(a1) & 0xFC);
      if (m.u8(a1) & 4) {
        this._msg(`Walrus ${m.u16(a4 + 0x1A) + 1} has reached its destination`);
      }
    }
    m.set8(a1, m.u8(a1) | 0x80);
    m.set16(a1 + 6, 0);
    this._walrus_shore(a1, a4);
  }

  // ---- Walrus weapons ($1FB6E) ---------------------------------------------------------
  /** $1FB6E: weapon 0 laser (Avatar), 1 guided missile (Harbinger), else the payload:
   * 8 drop, 9 ACCB, $B fuel pod. */
  walrus_fire(a1: number, a4: number): void {
    const m = this.mem;
    const w = m.u16(a1 + 0x18);
    if (w === 0) {
      return this._walrus_laser(a1, a4);
    }
    if (w === 1) {
      return this._walrus_missile(a1, a4);
    }
    const code = m.u16(a1 + 0x0E);
    if (code === 8) {
      return this._walrus_drop(a1, a4);
    }
    if (code === 9) {
      return this._walrus_accb(a1, a4);
    }
    if (code === 0x0B) {
      return this._walrus_refuel(a1, a4);
    }
    this._msg("No cargo to drop");
  }

  /** $A438 with the turret heading, then (0, 100) turned: the muzzle 100 ahead. */
  _turret_offset(a4: number): [number, number, number] {
    const m = this.mem;
    const h = w16(m.u16(a4 + 6) + m.u16(a4 + 0x26));
    this.set_matrix(h);
    const [x, z] = this.matrix_rotate(0, 0x64);
    return [h, x, z];
  }

  _walrus_laser(a1: number, a4: number): void {
    const m = this.mem;
    if (m.s16(a1 + 0x0A) <= 0) {
      return this._msg("No laser to fire");
    }
    if (m.u16(0x942)) {
      return;
    }
    m.set16(0x942, 8);
    const b = this._new_object(0x10);
    if (b === null) {
      return;
    }
    const [, x, z] = this._turret_offset(a4);
    this._place(b, a4, x, 0x64, z);
    this._copy_angles(a4, b);
    m.set8(b + 0x1B, 0x14);
    m.set16(a1 + 0x0A, m.u16(a1 + 0x0A) - 1);
  }

  /** $1FEEA: a guided missile (type 18) the player steers ($1DE74); its +$1A is the
   * Walrus bay + 4. */
  _walrus_missile(a1: number, a4: number): void {
    const m = this.mem;
    if (!m.u16(a1 + 0x0C)) {
      return this._msg("No missile to launch");
    }
    if (m.u16(0x942)) {
      return;
    }
    m.set16(0x942, 0x0F);
    const b = this._new_object(0x12);
    if (b === null) {
      throw new NotPorted("Walrus missile with no free object (the original returns through the saved a1)");
    }
    this._copy_angles(a4, b);
    const [h, x, z] = this._turret_offset(a4);
    m.set16(b + 6, h);
    this._place(b, a4, x, 0x64, z);
    m.set8(b + 0x1C, 0x78);
    m.set16(b + 0x1A, m.u16(a4 + 0x1A) + 4);
    m.set32(a4 + 0x20, b);
    m.set16(a1 + 0x0C, m.u16(a1 + 0x0C) - 1);
    m.set16(a1 + 0x12, m.u16(a1 + 0x12) - 0x96);
    m.set16(a1, m.u16(a1) & 0x3F);                   // andi.w: clears the whole flag byte
    m.set8(a1, m.u8(a1) | 0x10);
    m.set16(a1 + 0x2E, 0xA3);
  }

  /** $1FBA4: cargo 8 dropped (type 21) along the turret. */
  _walrus_drop(a1: number, a4: number): void {
    const m = this.mem;
    m.set16(a1 + 0x0E, 0);
    m.set16(a1 + 0x12, m.u16(a1 + 0x12) - 0xC8);
    const b = this._new_object(0x15);
    if (b === null) {
      return;
    }
    for (const o of [0x0A, 0x0E, 0x12]) {
      m.set32(b + o, m.u32(a4 + o));
    }
    m.set32(b + 0x0E, m.u32(b + 0x0E) + 0x28);
    m.set16(b + 0x20, m.u16(a4 + 6) + m.u16(a4 + 0x26));
    m.set16(b + 0x1E, 0x32);
  }

  /** $1FC10: an automatic command-centre builder: on a neutral island, 2000 inside its
   * box, at a clear spot 1000 behind the Walrus: slot type $34 (code = +$10 + 1). */
  _walrus_accb(a1: number, a4: number): void {
    const m = this.mem;
    const buf = m.u32(a4 + 0x3C);
    const fail = "Not a suitable place for an ACCB";
    if (!buf) {
      return this._msg(fail);
    }
    const [d0, d1] = this.island_box_distance(a4, buf);
    if (s32(d0 + 0x7D0) >= 0 || s32(d1 + 0x7D0) >= 0) {
      return this._msg(fail);
    }
    const [vx, vz] = this.yaw_vector(a4, w16(-0x3E8));
    const x = w16(vx + m.u32(a4 + 0x0A) - m.u32(buf + 2));
    const z = w16(vz + m.u32(a4 + 0x12) - m.u32(buf + 0x0A));
    const code = m.u16(a1 + 0x10);
    const i = m.u8(buf + 1);
    if (m.u8(OWNERS + i)) {
      return this._msg(fail);
    }
    const isl_a = ISLAND_TABLE + ISLAND_SIZE * i;            // $18940
    m.set32(G_ISLAND_PTR, isl_a);
    m.set32(G_ECONOMY_PTR, ECONOMY + ECONOMY_SIZE * i);
    m.set32(G_BUFFER_PTR, OBJ_BUFFERS[m.u8(isl_a + 1) ? 1 : 0]);
    const j = this._spot_at(0x29, i, x, z);
    if (j === null) {
      return this._msg(fail);
    }
    const s = isl_a + SLOT_BASE + SLOT_SIZE * j;
    m.set16(s, T_COMMAND_CENTRE_CONSTRUCTION);
    m.set16(s + 8, x);
    m.set16(s + 0x0A, z);
    m.set16(s + 4, 0);
    m.set16(s + 6, code + 1);
    this._slot_create_at(isl_a, s, buf, j);
    this._msg("ACCB dropped successfully");
    this.difficulty_add(0x400);
    this.score(0x9C4);
    m.set16(a1 + 0x0E, 0);
  }

  /** $18A28: find_free_island_spot for one given position (no random tries). */
  _spot_at(new_type: number, i: number, x: number, z: number): number | null {
    const m = this.mem;
    const isl = this.island(i);
    const r_new = m.u16(RADIUS + 2 * new_type);
    let empty: number | null = null, free_tree: number | null = null;
    const cut: number[] = [];
    for (let j = 0; j < isl.slots.length; j++) {
      const s = isl.slots[j];
      if (s.type === 0) {
        empty = j;
        continue;
      }
      const rad = m.u16(RADIUS + 2 * s.type);
      if (rad === 0) {
        continue;
      }
      const dist = Math.max(UnitCore.absw(s.x - x), UnitCore.absw(s.z - z));
      const is_tree = T_TREE_FIRST <= s.type && s.type <= T_TREE_LAST;
      if (s16(rad + r_new) > dist) {
        if (!is_tree) {
          return null;
        }
        cut.push(j);
      } else if (is_tree) {
        free_tree = j;
      }
    }
    const j = empty !== null ? empty : free_tree;
    if (j === null) {
      return null;
    }
    for (let n = 0; n < cut.length; n++) {
      const k = cut[n];
      m.set16(SPOT_CUT_LIST + 2 * n, k);
      isl.slots[k].type = 0;
      this.tree_cut(isl, k);
    }
    return j;
  }

  /** $182A4 with explicit island entry, slot and 3D buffer. */
  _slot_create_at(isl_a: number, s: number, buf: number, j: number): void {
    const m = this.mem;
    if (!m.u8(isl_a)) {
      return;
    }
    m.set16(NEW_INDEX, j);
    const e = buf + 0x12 + 0x40 * j;
    const t = m.u16(s);
    this.init_island_object(e, t);
    this.reset_island_schedule();
    m.set16(e + 0x38, m.u16(s + 4));
    m.set16(e + 0x0A, m.u16(s + 8));
    m.set16(e + 0x0E, m.u16(s + 0x0A));
    m.set16(e + 0x0C, w16(m.u32(SLOT_ENTRY + 8 * t + 4) + 0x50));
  }

  /** $1FD22: the fuel pod fills the nearest other Walrus within 500, if the two face
   * each other (both within $1770). */
  _walrus_refuel(a1: number, a4: number): void {
    const m = this.mem;
    let best = 0xFFFFFFFF, near: number | null = null;
    const x = m.u32(a4 + 0x0A), z = m.u32(a4 + 0x12);
    for (let k = 0; k < 4; k++) {
      const a5 = this.obj(WALRUS_OBJ + k);
      if (!(m.u8(a5 + 1) & 1) || a5 === a4) {
        continue;
      }
      const d = u32(this._max_abs(x - m.u32(a5 + 0x0A), z - m.u32(a5 + 0x12)));
      if (d <= best) {
        best = d;
        near = a5;
      }
    }
    if (s32(best) < 0 || s32(best) > 0x1F4 || near === null) {
      return this._msg("No walrus in refuelling range");
    }
    const h = this.atan2(m.u32(near + 0x0A) - x, m.u32(near + 0x12) - z);
    if (UnitCore.absw(h - m.u16(a4 + 6)) > 0x1770 || UnitCore.absw(h + 0x8000 - m.u16(near + 6)) > 0x1770) {
      return this._msg("Walruses must be facing to refuel");
    }
    m.set16(a1 + 0x0E, 0);
    m.set16(a1 + 0x12, m.u16(a1 + 0x12) - 0x1C2);
    m.set16(WALRUS_RECS + s16(w16(m.u16(near + 0x1A) * REC)) + 8, 0x7FFF);
    this._msg("Refuelling complete");
  }

  // ---- lift and loadouts ------------------------------------------------------------------
  /** $1EAA0: 1 lowering, 2 at the hangar (stow the Manta on it, or bring up [$DC4]),
   * 3 raising; the lift object follows the platform. */
  manta_lift_step(): void {
    const m = this.mem;
    const state = m.u16(LIFT_STATE);
    if (state === 0) {
      return;
    }
    if (state > 3) {
      throw new NotPorted(`lift state ${state}`);
    }
    if (state === 1) {
      let p = w16(m.u16(LIFT_POS) - 0x668);
      if (s16(p) <= -0x4000) {
        m.set16(LIFT_STATE, state + 1);
        p = 0xC000;
      }
      m.set16(LIFT_POS, p);
      if (m.s16(LIFT_BAY) >= 0) {
        this._lift_object_height(m.u32(0x6D6));
      }
      return;
    }
    if (state === 3) {
      let p = w16(m.u16(LIFT_POS) + 0x668);
      if (!(p & 0x8000)) {
        p = 0;
        m.set16(LIFT_STATE, 0);
      }
      m.set16(LIFT_POS, p);
      if (m.s16(LIFT_BAY) >= 0) {
        this._lift_object_height(m.u32(0x6D6));
      }
      if (m.u16(LIFT_STATE)) {
        return;
      }
      const bay = m.s16(LIFT_BAY);
      if (bay >= 0) {
        m.set8(MANTA_RECS + REC * bay + 1, 8);
        this._msg(`Manta ${bay + 1} is now on the flight deck`);
      }
      this.dock_attach();
      return;
    }
    const bay = m.s16(LIFT_BAY);
    if (bay >= 0) {
      const a1 = MANTA_RECS + REC * bay;
      const lift = m.u32(0x6D6);
      if (!lift) {
        throw new NotPorted("lift stow with no lift object");
      }
      m.set16(lift, 0);
      m.set16(0xB7E, m.u16(0xB7E) + m.u16(a1 + 0x0C));
      if (m.u16(a1 + 0x0E) === 4) {
        m.set16(0xBCC, m.u16(0xBCC) + 1);
      }
      if (m.u16(a1 + 0x0E) === 0x0A) {
        m.set16(0xC34, m.u16(0xC34) + 1);
        m.set32(COMMS_POD, 0);
      }
      if (m.u8(a1 + 0x10)) {
        m.set16(0xBE6, m.u16(0xBE6) + 1);
      }
      for (const o of [0x0C, 0x0E, 0x10, 0x12]) {
        m.set16(a1 + o, 0);
      }
      m.set8(a1 + 1, 0);
      m.set8(a1 + 0x1C, 0);
      m.set8(a1, m.u8(a1) & ~2);
      this._msg(`Manta ${bay + 1} returned to hangar`);
      m.set16(LIFT_BAY, 0xFFFF);
      this.dock_attach();
      m.set16(LIFT_STATE, 3);
      return;
    }
    const d3 = m.u16(LIFT_NEXT);
    const a0 = this.obj(MANTA_OBJ) + s16(w16(d3 * 0x140));
    const a1 = MANTA_RECS + s16(w16(d3 * REC));
    m.set16(LIFT_BAY, d3);
    this.init_world_object(a0, 5);
    m.set16(a0 + 0x1A, d3);
    m.set16(NEW_INDEX, d3 + 2);
    this.reset_pair_schedules();
    for (const o of [0x0A, 0x0E, 0x12]) {
      m.set32(a0 + o, m.u32(CARRIER_OBJ + o));
    }
    this._lift_object_height(a0);
    m.set16(a0 + 4, 0xFD4A);
    m.set16(a0 + 0x1E, 0xFA);
    m.set8(a1, m.u8(a1) | 1);
    m.set8(a1 + 1, 3);
    m.set8(a1 + 0x1C, 1);
    for (const o of [0x2E, 0x2C, 0x1A, 0x18]) {
      m.set16(a1 + o, 0);
    }
    m.set8(a1 + 0x32, 0);
    const codes = [m.u16(a1 + 0x0C), m.u16(a1 + 0x0E), m.u16(a1 + 0x10)];
    m.set16(a1 + 0x0C, 0);
    for (const code of codes) {
      this.loadout_fit(code, a1, a0);
    }
    this.dock_attach();
    m.set16(LIFT_STATE, 3);
  }

  _lift_object_height(a: number): void {
    const m = this.mem;
    if (!a) {
      throw new NotPorted("lift object pointer is 0");
    }
    const p = u32(0x140 * m.s16(LIFT_POS) * 2);
    m.set32(a + 0x0E, s16(shr(p, 16) + 0xB4));
  }

  /** $1ED46: fit a loadout item (table $CAC4) to a record: missiles add to +$C, the bomb
   * and payloads set +$E, the laser +$10, the Avatar laser +$A = 40; the comms pod also
   * becomes the relay [$DE0]. */
  loadout_fit(code: number, rec: number, obj: number): void {
    const m = this.mem;
    if (code > 11) {
      throw new NotPorted(`loadout code ${code}`);
    }
    const h = m.u32(LOADOUT + 26 * code + 0x16);
    const kind = LOADOUT_HANDLERS.has(h) ? LOADOUT_HANDLERS.get(h)! : "?";
    if (kind === "?") {
      throw new NotPorted(`loadout handler $${hex(h)}`);
    }
    if (kind === null) {
      return;
    }
    if (kind.startsWith("missiles")) {
      m.set16(rec + 0x0C, m.u16(rec + 0x0C) + parseInt(kind[kind.length - 1], 10));
    } else if (kind === "bomb") {
      m.set16(rec + 0x0E, 4);
    } else if (kind === "laser") {
      m.set8(rec + 0x10, 0xFF);
    } else if (kind === "avatar") {
      m.set16(rec + 0x0A, 0x28);
    } else if (kind === "payload") {
      m.set16(rec + 0x0E, code);
    } else {
      m.set16(rec + 0x0E, 0x0A);
      m.set32(COMMS_POD, obj);
    }
  }

  // ---- hangar commands (click handlers; the original takes d2 = 2, a fresh click) ----------
  _manta_selected(bay: number | null): number {
    const m = this.mem;
    if (bay !== null) {
      m.set32(0x48E, MANTA_RECS + REC * bay);
      m.set32(0x492, this.obj(MANTA_OBJ + bay));
      m.set16(0xDCC, bay);
    }
    return m.u32(0x48E);
  }

  _walrus_selected(bay: number | null): number {
    const m = this.mem;
    if (bay !== null) {
      m.set32(0x568, WALRUS_RECS + REC * bay);
      m.set32(0x56C, this.obj(WALRUS_OBJ + bay));
      m.set16(0xDE4, bay);
    }
    return m.u32(0x568);
  }

  /** $1158A / $11D5A: move up to $71C from the ship's tank into the vehicle (word, at
   * most $7FFF; any excess goes back). */
  _refuel(a: number, tank: number): boolean | undefined {
    const m = this.mem;
    let d0 = m.u16(a), d1 = s32(m.u32(tank));
    if (d0 === 0x7FFF) {
      this._msg("Fuel tanks full");
      return undefined;
    }
    if (d1 === 0) {
      this._msg("Fuel supply exhausted");
      return undefined;
    }
    let d2 = 0x71C;
    d1 -= d2;
    if (d1 < 0) {
      d2 += d1;
      d1 = 0;
    }
    d0 = w16(d0 + d2);
    if (d0 & 0x8000) {
      d1 = s32(d1 + s16(d0 - 0x7FFF));
      d0 = 0x7FFF;
    }
    m.set16(a, d0);
    m.set32(tank, d1);
    return true;
  }

  /** $11676 / $11DA4: the reverse, the ship's tank capped at $FFFE0. */
  _defuel(a: number, tank: number): boolean | undefined {
    const m = this.mem;
    let d0 = m.u16(a), d1 = s32(m.u32(tank));
    if (d0 === 0) {
      this._msg("Fuel tanks empty");
      return undefined;
    }
    if (d1 === FUEL_MAX) {
      this._msg("Fuel supply at full capacity");
      return undefined;
    }
    let d2 = 0x71C;
    d0 = w16(d0 - d2);
    if (d0 & 0x8000) {
      d2 = w16(d2 + d0);
      d0 = 0;
    }
    d1 = s32(d1 + s16(d2));
    if (d1 > FUEL_MAX) {
      d0 = w16(d0 + (d1 - FUEL_MAX));
      d1 = FUEL_MAX;
    }
    m.set16(a, d0);
    m.set32(tank, d1);
    return true;
  }

  /** $C640, in the Manta panel redraw ($B418 with $42966, drawn while [$DC0] = 0):
   * +$1C is set (unbalanced) when the weights (item +0) of slots 1 and 2 differ by more
   * than 80. */
  _manta_balance(a1: number): void {
    const m = this.mem;
    if (m.u16(0xDC0)) {
      return;
    }
    if (Math.max(m.u16(a1 + 0x0C), m.u16(a1 + 0x0E), m.u16(a1 + 0x10)) > 11) {
      throw new NotPorted("loadout code beyond the item table");
    }
    const d = w16(m.u16(LOADOUT + 26 * m.u16(a1 + 0x0C)) - m.u16(LOADOUT + 26 * m.u16(a1 + 0x0E)));
    m.set8(a1 + 0x1C, w16(UnitCore.absw(d)) > 0x50 ? 0xFF : 0);
  }

  /** $1158A (held button: no click test). */
  cmd_manta_refuel(bay: number | null = null): void {
    const a0 = this._manta_selected(bay);
    if (!this.mem.u8(a0 + 1) && this._refuel(a0 + 0x0A, FUEL_MANTA)) {
      this._manta_balance(a0);
    }
  }

  /** $11676. */
  cmd_manta_defuel(bay: number | null = null): void {
    const a0 = this._manta_selected(bay);
    if (!this.mem.u8(a0 + 1) && this._defuel(a0 + 0x0A, FUEL_MANTA)) {
      this._manta_balance(a0);
    }
  }

  /** $11D4C. */
  cmd_walrus_refuel(bay: number | null = null): void {
    const a0 = this._walrus_selected(bay);
    if (!this.mem.u8(a0 + 1)) {
      this._refuel(a0 + 8, FUEL_WALRUS);
    }
  }

  /** $11DA4. */
  cmd_walrus_defuel(bay: number | null = null): void {
    const a0 = this._walrus_selected(bay);
    if (!this.mem.u8(a0 + 1)) {
      this._defuel(a0 + 8, FUEL_WALRUS);
    }
  }

  _repair(a0: number, kind: string): void {
    const m = this.mem;
    const st = m.u8(a0 + 1);
    if (st & 0x80) {
      return this._replace(a0, kind);
    }
    if (st) {
      return;
    }
    if (m.u16(a0 + 0x14) === 0xFA0) {
      return this._msg(`${kind === "Manta" ? "Manta" : "AAV"} is fully functional`);
    }
    m.set8(a0 + 1, 1);
    m.set16(kind === "Manta" ? 0xDC0 : 0xDC2, 1);
  }

  /** $117D4 / $11EF0: a destroyed bay is refilled from storage, empty and repaired. */
  _replace(a0: number, kind: string): void {
    const m = this.mem;
    const stock = kind === "Manta" ? STOCK_MANTA : STOCK_WALRUS;
    if (!m.u16(stock)) {
      return this._msg(kind === "Manta" ? "No Mantas in storage" : "No AAV's in storage");
    }
    m.set16(stock, m.u16(stock) - 1);
    m.set8(a0, 0);
    m.set8(a0 + 1, 0);
    const fields = kind === "Manta" ? [0x0A, 0x0C, 0x0E, 0x10, 0x12] : [8, 0x12, 0x26, 0x28];
    for (const o of fields) {
      m.set16(a0 + o, 0);
    }
    m.set16(a0 + 0x14, 0xFA0);
    if (kind === "Manta") {
      m.set8(a0 + 0x1C, 0);
    }
  }

  /** $1176A: repair (status 1), or replace a destroyed Manta. */
  cmd_manta_repair(bay: number | null = null): void {
    this._repair(this._manta_selected(bay), "Manta");
  }

  /** $11E8A. */
  cmd_walrus_repair(bay: number | null = null): void {
    this._repair(this._walrus_selected(bay), "Walrus");
  }

  /** $117D4 (a0 = the record). */
  cmd_manta_replace(bay: number | null = null): void {
    this._replace(this._manta_selected(bay), "Manta");
  }

  /** $11EF0 (a0 = the record). */
  cmd_walrus_replace(bay: number | null = null): void {
    this._replace(this._walrus_selected(bay), "Walrus");
  }

  /** $1182A: start flight preparation (status 2). */
  cmd_manta_prepare(bay: number | null = null): void {
    const a0 = this._manta_selected(bay);
    this.mem.set8(a0 + 1, 2);
    this.preparation_time(a0, "Manta");
  }

  /** $11F3E. */
  cmd_walrus_prepare(bay: number | null = null): void {
    const a0 = this._walrus_selected(bay);
    this.mem.set8(a0 + 1, 2);
    this.preparation_time(a0, "Walrus");
  }

  _bay_status_message(st: number, bay: number, kind: string): void {
    if (st & 0x80) {
      this._msg(`${kind} ${bay + 1} destroyed`);
    } else if (st <= 2) {
      this._msg(`${kind} ${bay + 1} is under repair`);
    } else {
      this._msg(`${kind} ${bay + 1} is not in the hangar`);
    }
  }

  /** $11894: send the selected Manta [$DCC] up the lift (status 3, lift state 1). */
  cmd_manta_to_deck(bay: number | null = null): void {
    const m = this.mem;
    const a0 = this._manta_selected(bay);
    const d1 = m.u16(0xDCC);
    if (!m.u8(0xB32)) {
      return this._msg("Lift is out of order");
    }
    const st = m.u8(a0 + 1);
    if (st) {
      return this._bay_status_message(st, d1, "Manta");
    }
    if (m.u8(MANTA_LANDING)) {
      return this._msg("Plane landing - flight deck in use");
    }
    const ls = m.u16(LIFT_STATE);
    if (m.s16(LIFT_BAY) >= 0 || !(ls === 0 || ls === 3)) {
      return this._msg("Lift in use");
    }
    if (m.u8(a0 + 0x1C)) {
      return this._msg(`Manta ${d1 + 1} has an unbalanced payload`);
    }
    if (m.u16(a0 + 0x10) === 0x0A && m.u32(COMMS_POD)) {
      return this._msg(`Manta ${d1 + 1} Long range communications are in use`);
    }
    m.set8(a0 + 1, 3);
    m.set16(LIFT_STATE, 1);
    m.set16(LIFT_NEXT, d1);
    this.dock_attach();
    m.set16(0xDC0, 2);
  }

  /** $1242A: take the Manta on deck (mode 1) down the lift. */
  cmd_manta_to_hangar(): void {
    const m = this.mem;
    if (!m.u8(0xB32)) {
      return this._msg("Lift is out of order");
    }
    const bay = m.s16(LIFT_BAY);
    if (m.u16(LIFT_STATE) || bay < 0 || m.u8(MANTA_RECS + REC * bay + 0x1C) !== 1) {
      return this._msg("No aircraft on the flight deck");
    }
    m.set8(MANTA_RECS + REC * bay + 1, 3);
    m.set16(LIFT_STATE, 1);
    this.dock_attach();
  }

  /** $12002: the Manta on deck takes off (mode 2); without a waypoint it gets one
   * 6000 ahead of the carrier at (bay + 1) * 300 + 500. */
  cmd_manta_take_off(): void {
    const m = this.mem;
    const bay = m.s16(LIFT_BAY);
    if (m.u16(LIFT_STATE) || bay < 0 || m.u8(MANTA_RECS + REC * bay + 0x1C) !== 1) {
      return this._msg("No aircraft available for launch");
    }
    const a0 = MANTA_RECS + REC * bay;
    m.set8(a0 + 0x1C, 2);
    m.set8(a0 + 0x1D, 0);
    m.set8(a0 + 0x33, 0);
    const f = m.u8(a0);
    if (!(f & 2)) {
      m.set8(a0, (f | 2) & ~4);
      m.set16(a0 + 0x2A, 0x3C);
      m.set32(a0 + 0x22, s16(w16((bay + 1) * 0x12C + 0x1F4)));
      const c = CARRIER_OBJ;
      this.set_matrix(m.u16(c + 6));
      const [x, z] = this.matrix_rotate(0, 0xE890);
      m.set32(a0 + 0x1E, x + m.u32(c + 0x0A));
      m.set32(a0 + 0x26, z + m.u32(c + 0x12));
    }
    this._msg(`Launching Manta ${bay + 1}`);
  }

  /** $120F4: order the selected airborne Manta to land on the carrier (mode 0), if the
   * deck and lift are free and it is within 16384. */
  cmd_manta_land(bay: number | null = null): void {
    const m = this.mem, c = CARRIER_OBJ;
    const a1 = this._manta_selected(bay);
    const a0 = m.u32(0x492);
    const n = m.u16(0xDCC) + 1;
    const st = m.u8(a1 + 1);
    if (st & 0x80) {
      return this._msg(`Manta ${n} destroyed`);
    }
    if (st < 8) {
      return this._msg(`Manta ${n} is not airborne`);
    }
    if (m.u8(a1) & 1 && m.u8(a1 + 0x1C) === 1) {
      return this._msg(`Manta ${n} is already on deck`);
    }
    if (m.u8(MANTA_LANDING)) {
      return this._msg(m.u8(a1) & 1 && m.u8(a1 + 0x1C) === 0
        ? `Manta ${n} is already landing` : "Another Manta is landing");
    }
    const dx = s32(m.u32(a0 + 0x0A) - m.u32(c + 0x0A));
    const dz = s32(m.u32(a0 + 0x12) - m.u32(c + 0x12));
    if (dx !== s16(dx) || dz !== s16(dz) || s32(dx * dx + dz * dz) > 0x10000000) {
      return this._msg(`Manta ${n} is out of range`);
    }
    if (m.u16(LIFT_STATE)) {
      return this._msg("Lift in use");
    }
    if (m.s16(LIFT_BAY) >= 0) {
      return this._msg("Flight deck in use");
    }
    m.set8(MANTA_LANDING, 0xFF);
    m.set16(0x6DE, m.u16(a0 + 0x1A));
    m.set8(a1, m.u8(a1) | 1);
    m.set8(a1 + 0x1C, 0);
    m.set8(a1 + 0x1D, 0);
    m.set16(0xDDC, m.u16(0xDCC));
    this._msg(`Manta ${n} is landing`);
  }

  /** $1250E: launch the selected Walrus from the dock (mode 2, status 8), fitting its
   * two loadout slots; without a waypoint it gets one 4000 astern. */
  cmd_walrus_launch(bay: number | null = null): void {
    const m = this.mem, c = CARRIER_OBJ;
    this._walrus_selected(bay);
    const d0 = m.u16(0xDE4);
    const a0 = m.u32(0x56C), a1 = m.u32(0x568);
    const st = m.u8(a1 + 1);
    if (st) {
      return this._bay_status_message(st, d0, "Walrus");
    }
    if (m.u8(0x122F)) {
      return this._msg("Cannot launch while drones are docking");
    }
    if (m.u8(0xD40)) {
      return this._msg("Docking bay is in use");
    }
    if (m.u8(WALRUS_DOCKING) || m.u16(DOOR_STATE)) {
      return this._msg("Docking bay is in use");
    }
    m.set16(DOCKED_WALRUS, d0);
    this.init_world_object(a0, 6);
    m.set16(a0 + 0x1A, d0);
    m.set16(NEW_INDEX, d0 + 6);
    this.reset_pair_schedules();
    for (const o of [0x0A, 0x0E, 0x12]) {
      m.set32(a0 + o, m.u32(c + o));
    }
    m.set32(a0 + 0x0E, 0xFFFFFFF1);
    m.set16(a0 + 0x24, 0);
    m.set16(a0 + 0x1C, 0);
    m.set16(a0 + 0x1E, 0xFDBC);
    const f = m.u8(a1);
    if (!(f & 2)) {
      m.set8(a1, (f | 2) & ~4);
      m.set16(a1 + 0x24, 0x3C);
      this.set_matrix(m.u16(c + 6));
      const [x, z] = this.matrix_rotate(w16(m.u16(a0 + 0x1A) * 0x7D0 - 0xBB8), 0xF060);
      m.set32(a1 + 0x1C, x + m.u32(c + 0x0A));
      m.set32(a1 + 0x20, z + m.u32(c + 0x12));
    }
    m.set8(a1 + 1, 8);
    m.set8(a1 + 0x1A, 2);
    m.set8(a1 + 0x1B, 0);
    m.set16(a1 + 4, 0);
    m.set8(a1, m.u8(a1) | 1);
    for (const o of [0x2C, 0x18, 0x16]) {
      m.set16(a1 + o, 0);
    }
    m.set8(a1 + 0x32, 0);
    m.set8(a1 + 0x33, 0);
    const codes = [m.u16(a1 + 0x26), m.u16(a1 + 0x28)];
    m.set16(a1 + 0x0A, 0xFFFF);
    m.set16(a1 + 0x0E, 0xFFFF);
    for (const code of codes) {
      this.loadout_fit(code, a1, a0);
    }
    m.set8(WALRUS_DOCKING, 0xFF);
    m.set16(0x6E0, m.u16(a0 + 0x1A));
    this._door(1);
    this._msg(`Launching walrus ${d0 + 1}`);
  }

  /** $127C4: order the selected Walrus home (mode 0), if it is inside the docking cone
   * astern (local z -1500..-8600, |x| <= -(z + 600)). */
  cmd_walrus_dock(bay: number | null = null): void {
    const m = this.mem, c = CARRIER_OBJ;
    this._walrus_selected(bay);
    const n = m.u16(0xDE4) + 1;
    const a0 = m.u32(0x56C), a1 = m.u32(0x568);
    const st = m.u8(a1 + 1);
    if (st !== 8) {
      return this._msg(st & 0x80 ? `Walrus ${n} destroyed` : `Walrus ${n} is already in dock`);
    }
    if (m.u8(0x122F)) {
      return this._msg("Cannot dock while drones are docking");
    }
    if (m.u8(0xD40)) {
      return this._msg("Docking bay is in use");
    }
    const cone = `Walrus ${n} is outside the docking cone`;
    if (m.u8(a1) & 1 && m.u8(a1 + 0x1A) === 2) {
      return this._msg(cone);
    }
    if (m.u8(WALRUS_DOCKING) || m.u16(DOOR_STATE)) {
      return this._msg("Docking bay is in use");
    }
    const dx = s32(m.u32(a0 + 0x0A) - m.u32(c + 0x0A));
    const dz = s32(m.u32(a0 + 0x12) - m.u32(c + 0x12));
    if (dx !== s16(dx) || dz !== s16(dz)) {
      return this._msg(cone);
    }
    this.set_matrix(w16(-m.u16(c + 6)));
    const [x, z] = this.matrix_rotate(dx, dz);
    const ax = UnitCore.absw(x);
    if (z > -0x5DC || z < -0x2198 || ax > 0x1F40 || s16(-(z + 0x258)) < ax) {
      return this._msg(cone);
    }
    m.set8(a1, 0x41);
    m.set8(a1 + 0x1A, 0);
    m.set8(a1 + 0x1B, 0);
    m.set8(WALRUS_DOCKING, 0xFF);
    m.set16(0x6E0, m.u16(a0 + 0x1A));
    this._msg(`Walrus ${n} is docking`);
  }
}

// ROUTINES and the check_units scenarios/checks (test-only) live in tools/stunits_vehicles.py.
