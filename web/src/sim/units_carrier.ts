/**
 * Unit layer, part of tools/stunits.py: the carrier's own weapons.
 * (TypeScript port of tools/stunits_carrier.py; that file is the verified reference.)
 *
 * - carrier_laser_turret_step ($1EDFC) in full, including its own screen ([$718] = 8): mouse
 *   aiming, firing type-16 bolts, heat and overheating damage.
 * - UI commands (the original runs these from panel click handlers; d2 = 2, a fresh click):
 *     cmd_launch_flare           $1338C  decoy flare (objects 17/18, item 10)
 *     cmd_launch_recon_drone     $134C2  viewing drone (object 15, item 11)
 *     cmd_fire_ssm               $13598  surface-to-surface missile (item 2) at the point under
 *                                        the pointer in the viewing drone's picture
 *     cmd_launch_defence_drone   $10738  one carrier defence drone (objects 11-14, item 6)
 *     cmd_recall_defence_drones  $1088C  start the recall/launch sequence ($122F)
 *   Messages become events; sounds are dropped (they write no simulation state).
 *
 * The Python mixin is combined into UnitState ahead of UnitCore; here CarrierUnits extends
 * UnitCore, so methods defined here replace the core's stubs and hooks.
 */
import { s16, s32, w16, shr } from "./mem.ts";
import { UnitCore, UNIT_REGIONS, UNIT_INPUT_REGIONS, SCREEN, POINTER_MODE, MOUSE_DX, FRAME, CARRIER, NEW_INDEX } from "./units_core.ts";
import { ControlsUnits } from "./units_controls.ts";

// State these commands read or write that UnitCore doesn't list yet.
UNIT_REGIONS.push(
  ["turret fire key $88C", 0x88c, 1],
  ["pointer position", 0x474, 4], // $474/$476: aiming the SSM in the drone view
  ["ssm view $4770", 0x4770, 0x10], // drone view panel: +8 zoom, +$C/+$E centre
  ["ssm armed $1160", 0x1160, 2],
  ["selected drone station $698", 0x698, 2],
);
for (const name of ["pointer position", "ssm view $4770", "ssm armed $1160"]) UNIT_INPUT_REGIONS.add(name);

export const TURRET_SCREEN = 8;
export const OBJ_RECON = 15;
export const OBJ_FLARES: [number, number] = [17, 18];
export const STOCK_FLARES = 0xc82;
export const STOCK_RECON = 0xc9c;
export const STOCK_SSM = 0xbb2;
export const STOCK_DRONES = 0xc1a;

export abstract class CarrierUnits extends ControlsUnits {
  // ---- laser turret ($1EDFC) ------------------------------------------------------------
  /** $1EDFC: cooldown $93E and heat $6C8 every pass; on the turret screen, fire on
   * $88C or a left click in mouse-steer mode, then take the aim rates from the mouse;
   * then the rates self-centre and integrate into elevation $6CA and heading $6CC. */
  carrier_laser_turret_step(): void {
    const m = this.mem;
    if (m.u16(0x93e)) m.set16(0x93e, m.u16(0x93e) - 1);
    let h = m.u16(0x6c8);
    if (h) {
      h = s16(h - 16);
      m.set16(0x6c8, h < 0 ? 0 : h);
    }
    if (m.u16(SCREEN) === TURRET_SCREEN) this._turret_screen();
    for (const [bit, rate] of [[0x80, 0x6ce], [0x40, 0x6d0]]) {
      if (m.u8(0x6d5) & bit) {
        if (m.u16(rate) === 0) m.set8(0x6d5, m.u8(0x6d5) & ~bit);
        else m.set16(rate, UnitCore.self_centre(m.u16(rate)));
      }
    }
    const e = s16(-UnitCore.quantise(m.u16(0x6d0)) + m.u16(0x6ca));
    m.set16(0x6ca, e > 0x1f4 ? 0x1f4 : e < -16000 ? -16000 : e);
    m.set16(0x6cc, m.u16(0x6cc) + 2 * UnitCore.quantise(m.u16(0x6ce)));
  }

  _turret_screen(): void {
    const m = this.mem;
    if (!m.u16(0x93e)) {
      let fire = m.u8(0x88c) !== 0;
      if (!fire && !m.u8(POINTER_MODE)) {
        fire = (m.u8(0x881) & 1) !== 0;
        m.set8(0x881, m.u8(0x881) & 0xfe);
      }
      if (fire) this._turret_fire();
    }
    if (!m.u8(0xb12)) {
      // observation turret broken: self-centre
      m.set8(0x6d5, m.u8(0x6d5) | 0xc0);
      return;
    }
    if (m.u8(POINTER_MODE)) return;
    m.set8(0x6d5, m.u8(0x6d5) & 0x3f);
    for (const [rate, delta] of [[0x6ce, MOUSE_DX], [0x6d0, MOUSE_DX + 2]]) {
      const r = s16(m.u16(rate) + m.u16(delta));
      m.set16(delta, 0);
      m.set16(rate, r > 255 ? 255 : r < -255 ? -255 : r);
    }
  }

  _turret_fire(): void {
    const m = this.mem;
    m.set8(0x88c, 0);
    if (m.u16(0xb1a) === 0) {
      this.events.push(["message", "No laser", this._ping()]);
      return;
    }
    const a = this.find_free_object();
    if (a == null) return;
    m.set16(0x93e, 5);
    this.init_world_object(a, 0x10);
    this.reset_pair_schedules();
    let heat = w16(m.u16(0x6c8) + 0x200);
    if (m.u16(0xb1a) !== 0x100) heat = w16(heat + 0x200);
    if (s16(heat) > 0x3fff) heat = 0x3fff;
    m.set16(0x6c8, heat);
    if (heat > 0x3000) this._sub_floor(0xb14, 100);
    // The bolt ignores the carrier, and anything on the lift or in the dock, for 20 passes.
    const f = (m.u16(FRAME) + 0x14) & 0x7fff;
    m.set16(a + 0xd4, f);
    for (const [ptr, base] of [[0x6d6, 2], [0x6da, 6]]) {
      const o = m.u32(ptr);
      if (o) m.set16(a + 0xc0 + 2 * w16(m.u16(o + 0x1a) + base), f);
    }
    const c = CARRIER;
    m.set16(a + 8, 0);
    m.set16(a + 6, m.u16(c + 6) + m.u16(0x6cc));
    m.set16(a + 4, m.u16(0x6ca));
    const [x, z] = this.rotate_yaw(0, -0x15e, m.u16(c + 6));
    m.set32(a + 0x0a, m.u32(c + 0x0a) + x);
    m.set32(a + 0x12, m.u32(c + 0x12) + z);
    m.set32(a + 0x0e, 0x14a);
    m.set8(a + 0x1b, 0x4b);
    this._sfx(4);
  }

  // ---- commands ---------------------------------------------------------------------------
  /** $1338C: a decoy flare from the carrier (needs flares on board, the reload timer
   * $1150 at 0 and the flare launcher working). */
  cmd_launch_flare(): void {
    const m = this.mem;
    if (!m.u16(STOCK_FLARES)) {
      this.events.push(["message", "No decoy flares", this._ping()]);
      return;
    }
    if (m.u16(0x1150)) {
      this.events.push(["message", "Flare launcher busy", this._ping()]);
      return;
    }
    if (!m.u8(0xb22)) {
      this.events.push(["message", "Flare launcher too damaged", this._ping()]);
      return;
    }
    let a = this.obj(OBJ_FLARES[0]);
    let k = OBJ_FLARES[0];
    if (m.u8(a + 1) & 1) {
      a = this.obj(OBJ_FLARES[1]);
      k = OBJ_FLARES[1];
      if (m.u8(a + 1) & 1) {
        this.events.push(["message", "Flare launcher busy", this._ping()]);
        return;
      }
    }
    m.set16(NEW_INDEX, k);
    this.init_world_object(a, 0x1e);
    this.reset_pair_schedules();
    const c = CARRIER;
    m.set32(a + 0x0a, m.u32(c + 0x0a));
    m.set32(a + 0x0e, 0x1f4);
    m.set32(a + 0x12, m.u32(c + 0x12));
    m.set16(a + 0x22, 0xfa);
    m.set16(a + 0x20, 0xc8);
    m.set16(a + 6, this.rand());
    m.set32(0x1154, a);
    this.events.push(["message", "Decoy flare launched", this._ping(true, true)]);
    this._sfx(10);
    m.set16(STOCK_FLARES, m.u16(STOCK_FLARES) - 1);
    m.set16(0x1152, m.u16(0x1152) + 1);
    m.set16(0x1150, 0x32);
  }

  /** $134C2: the viewing drone (object 15), launched astern. */
  cmd_launch_recon_drone(): void {
    const m = this.mem;
    if (!m.u16(STOCK_RECON)) {
      this.events.push(["message", "No Recon drone available", this._ping()]);
      return;
    }
    const a = this.obj(OBJ_RECON);
    if (m.u8(a + 1) & 1) {
      this.events.push(["message", "Flare launcher busy", this._ping()]);
      return;
    }
    m.set16(NEW_INDEX, OBJ_RECON);
    this.init_world_object(a, 0x20);
    this.reset_pair_schedules();
    const c = CARRIER;
    m.set32(a + 0x0a, m.u32(c + 0x0a));
    m.set32(a + 0x0e, 0x1f4);
    m.set32(a + 0x12, m.u32(c + 0x12));
    m.set16(a + 6, m.u16(c + 6) + 0x8000);
    m.set16(a + 4, 0x4000);
    m.set16(a + 0x22, 0xfa);
    m.set16(a + 0x24, 0xc8);
    m.set16(a + 0x20, 0x12c);
    this.events.push(["message", "Recon drone launched", this._ping(true, true)]);
    this._sfx(10);
    m.set16(STOCK_RECON, m.u16(STOCK_RECON) - 1);
  }

  /** $13598: a cruise missile (type 19) at the point under the pointer in the
   * viewing drone's picture (pointer $474/$476 relative to the view panel at $4770,
   * rotated by the drone's heading and scaled by its altitude and the zoom). */
  cmd_fire_ssm(): void {
    const m = this.mem;
    if (!m.u8(0x1160)) return;
    if (!m.u16(STOCK_SSM)) {
      this.events.push(["message", "No missiles", this._ping()]);
      return;
    }
    if (m.u16(0x115e)) {
      this.events.push(["message", "Flare launcher busy", this._ping()]);
      return;
    }
    if (!m.u8(0xb2a)) {
      this.events.push(["message", "Missile system too damaged", this._ping()]);
      return;
    }
    const d = this.obj(OBJ_RECON);
    if (!(m.u8(d + 1) & 1)) return;
    let x = w16((m.u16(0x474) - m.u16(0x477c)) * 4);
    let z = w16(-w16(m.u16(0x476) - m.u16(0x477e)) * 4);
    [x, z] = this.rotate_yaw(x, z, m.u16(d + 6));
    const alt = m.s16(d + 0x10);
    const shift = (m.u16(0x4778) + 2) & 63;
    let tx = s32(shr(s32(x * alt), shift)); // muls.w, asr.l by a register
    let tz = s32(shr(s32(z * alt), shift));
    tx = s32(tx + m.u32(d + 0x0a));
    tz = s32(tz + m.u32(d + 0x12));
    const c = CARRIER;
    // Python: `not abs(...) > 0x7D0 and abs(...) < 0x7D0`
    if (!(Math.abs(s32(tx - m.u32(c + 0x0a))) > 0x7d0) && Math.abs(s32(tz - m.u32(c + 0x12))) < 0x7d0) {
      this.events.push(["message", "Target too close", this._ping()]);
      return;
    }
    m.set32(0xa80, tx);
    m.set32(0xa84, tz);
    const a = this.find_free_object();
    if (a == null) return;
    this.init_world_object(a, 0x13);
    this.reset_pair_schedules();
    const hd = m.u16(c + 6);
    m.set16(a + 6, hd + 0x8000);
    const [ox, oz] = this.rotate_yaw(0, -0xc8, hd);
    m.set32(a + 0x0a, m.u32(c + 0x0a) + ox);
    m.set32(a + 0x12, m.u32(c + 0x12) + oz);
    m.set32(a + 0x0e, 0x8c);
    m.set16(a + 0xd4, (m.u16(FRAME) + 5) & 0x7fff);
    m.set32(a + 0x1e, tx);
    m.set32(a + 0x22, tz);
    // Quirk: meant for the missile's life, this writes the CARRIER's +$1B (a4 is still the
    // carrier). The missile's life starts at 0 and its plain subq wraps it to 255.
    m.set8(c + 0x1b, 0xff);
    m.set16(STOCK_SSM, m.u16(STOCK_SSM) - 1);
    m.set16(0x115e, 0x32);
    this.events.push(["message", "Missile launched", this._ping(true, true)]);
    this._sfx(5);
  }

  /** $1055C: another active station's target within sqrt($24000) (about 384). */
  _drone_station_clash(rec: number, x: number, z: number): boolean {
    const m = this.mem;
    for (let k = 0; k < 4; k++) {
      const r = 0x65e + 14 * k;
      if (r === rec || !(m.u8(r + 1) & 1)) continue;
      const dz = s16(m.u16(r + 0x0c) - z);
      const dx = s16(m.u16(r + 0x0a) - x);
      if (dz * dz + dx * dx < 0x24000) return true;
    }
    return false;
  }

  /** $10738: one defence drone to a free station; its target slides back by $180
   * until it's clear of the other stations. */
  cmd_launch_defence_drone(): void {
    const m = this.mem;
    if (m.u8(0x122f)) {
      this.events.push(["message", "Drone recall in progress", this._ping()]);
      return;
    }
    if (m.u8(0x1233)) {
      this.events.push(["message", "Dock busy", this._ping()]);
      return;
    }
    if (m.u8(0xd40)) {
      this.events.push(["message", "Dock busy", this._ping()]);
      return;
    }
    if (!m.u16(STOCK_DRONES)) {
      this.events.push(["message", "No drones", this._ping()]);
      return;
    }
    let k: number | null = null;
    for (let j = 0; j < 4; j++) {
      if (!(m.u8(0x65e + 14 * j + 1) & 1)) {
        k = j;
        break;
      }
    }
    if (k === null) {
      this.events.push(["message", "All drones launched", this._ping()]);
      return;
    }
    const rec = 0x65e + 14 * k;
    m.set16(rec, 1);
    m.set16(rec + 2, 0);
    m.set16(rec + 4, 0xfa24);
    if (this._drone_station_clash(rec, m.u16(rec + 0x0a), m.u16(rec + 0x0c))) {
      let z = 0xfa24;
      while (true) {
        for (const [o, v] of [[0x0a, 0], [0x0c, z], [2, 0], [4, z]]) m.set16(rec + o, v);
        if (!this._drone_station_clash(rec, 0, z)) break;
        z = w16(z - 0x180);
      }
      this.events.push(["message", "Drone station moved", this._ping()]);
    }
    m.set16(STOCK_DRONES, m.u16(STOCK_DRONES) - 1);
    m.set16(NEW_INDEX, 11 + k);
    const a = this.obj(11 + k);
    this.init_world_object(a, 10 + k);
    this.reset_pair_schedules();
    m.set16(a + 0x1a, k);
  }

  /** $1088C: start the sequence ($122F) that brings the drones back to the deck. */
  cmd_recall_defence_drones(): void {
    const m = this.mem;
    if (!m.u16(0x65c)) {
      this.events.push(["message", "No drones launched", this._ping()]);
      return;
    }
    if (m.u8(0x122f)) {
      this.events.push(["message", "Drone recall in progress", this._ping()]);
      return;
    }
    if (m.u8(0x1233)) {
      this.events.push(["message", "Dock busy", this._ping()]);
      return;
    }
    if (m.u8(0xd40)) {
      this.events.push(["message", "Dock busy", this._ping()]);
      return;
    }
    m.set8(0x122f, 0xff);
    m.set16(0x69a, 0);
    let z = 0xfa24;
    for (let k = 0; k < 4; k++) {
      const rec = 0x65e + 14 * k;
      m.set16(rec + 0x0a, 0);
      m.set16(rec + 0x0c, z);
      m.set16(rec, m.u16(rec) & 1);
      z = w16(z - 0x180);
    }
    this.events.push(["message", "Recalling drones", this._ping()]);
  }
}

// ROUTINES (empty), the verification scenarios (scenario_turret, scenario_firing, _launch_scenario,
// SCENARIOS), COMMANDS, _perturb_for_commands, check_commands and CHECKS live in
// tools/stunits_carrier.py.
