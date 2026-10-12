// The player's controls, mapped onto the model's own commands (web/src/sim/units_controls.ts,
// ported from the original's click and key handlers; docs/units.md §10). Held controls apply
// before every pass, like a key held on the ST; what they do depends on the screen.
import type { Game } from "./game.ts";

const C_RUDDER = 0x642, POINTER_MODE = 0x457, VIEWED = 0x9fe;
const MANTA_RECS = 0x496, WALRUS_RECS = 0x570, REC = 0x34;
const RUDDER_RATE = 24;            // rudder/pitch change per pass while a steering key is held

export type Held = "faster" | "slower" | "left" | "right" | "up" | "down" | "fire";

export class Controls {
  private game: Game | null = null;
  private held = new Set<Held>();

  attach(game: Game): void {
    this.game = game;
    this.held.clear();
    game.runner.beforePass = () => this.beforePass();
  }

  get state() {
    return this.game!.runner.state;
  }

  /** After a command, show its effect at once (the renderer and UI read the RAM image). */
  private sync(): void {
    this.game?.runner.state.to_ram(this.game.ram);
  }

  /** Run a model command and show its effect; returns its result. */
  run<T>(fn: () => T): T {
    const r = fn();
    this.sync();
    return r;
  }

  hold(what: Held, on: boolean): void {
    if (on) this.held.add(what);
    else this.held.delete(what);
  }

  releaseAll(): void {
    this.held.clear();
  }

  /** The vehicle on the control screen: its record address and bay (from [$9FE] +$1A). */
  private viewedRecord(base: number): { rec: number; bay: number } | null {
    const o = this.state.mem.u32(VIEWED);
    if (!o) return null;
    const bay = this.state.mem.u16(o + 0x1a) & 3;
    return { rec: base + REC * bay, bay };
  }

  /** The control screens steer with the mouse: make sure mouse-steer mode is on ($457 clear). */
  private mouseSteer(): void {
    if (this.state.mem.u8(POINTER_MODE)) this.state.cmd_toggle_mouse_steer();
  }

  private beforePass(): void {
    const s = this.state, m = s.mem, screen = this.game!.screen;
    const h = (k: Held) => (this.held.has(k) ? 1 : 0);
    const turn = h("right") - h("left"), climb = h("up") - h("down"), speed = h("faster") - h("slower");
    const clamp = (v: number) => Math.max(-255, Math.min(255, v));
    if (screen === 0) {
      const sp = speed || climb;              // the arrow keys also change speed on the bridge
      if (sp > 0) s.cmd_carrier_speed_up();
      if (sp < 0) s.cmd_carrier_speed_down();
      if (turn) {
        this.mouseSteer();
        s.cmd_steer_to(m.s16(C_RUDDER) + turn * RUDDER_RATE);
      }
    } else if (screen === 27) {
      const v = this.viewedRecord(MANTA_RECS);
      if (!v) return;
      if (speed) {
        // throttle slider: target = (195 - y) * 7, y 158..197
        const target = m.s16(v.rec + 8) + speed * 7;
        s.cmd_manta_throttle(Math.max(158, Math.min(197, Math.round(195 - target / 7))));
      }
      if (turn || climb) {
        this.mouseSteer();
        s.cmd_manta_steer_to(clamp(m.s16(v.rec + 2) + turn * RUDDER_RATE), clamp(m.s16(v.rec + 4) - climb * RUDDER_RATE));
      }
      if (this.held.has("fire")) {
        this.mouseSteer();
        s.cmd_vehicle_fire();
      }
    } else if (screen === 18) {
      const v = this.viewedRecord(WALRUS_RECS);
      if (!v) return;
      if (speed) {
        // throttle slider: target = (195 - y) * $2AA >> 8, y 158..197
        const target = m.s16(v.rec + 6) + speed * 3;
        s.cmd_walrus_throttle(Math.max(158, Math.min(197, Math.round(195 - (target * 256) / 0x2aa))));
      }
      if (turn || climb) {
        this.mouseSteer();
        if (m.u8(v.rec) & 0x10) {
          // a Harbinger in flight: its turn rates (missile +$1E yaw, +$20 pitch)
          const missile = m.u32(m.u32(0x56c) + 0x20);
          s.cmd_walrus_missile_steer_to(clamp(m.s16(missile + 0x1e) + turn * RUDDER_RATE), clamp(m.s16(missile + 0x20) - climb * RUDDER_RATE));
        }
        else if (turn) s.cmd_walrus_steer_to(clamp(m.s16(v.rec + 2) + turn * RUDDER_RATE));
      }
      if (this.held.has("fire")) {
        this.mouseSteer();
        s.cmd_vehicle_fire();
      }
    }
  }

  stop(): void {
    this.run(() => this.state.cmd_carrier_stop());
  }

  /** Centre the rudder (and pitch) of whatever the screen controls. */
  centreRudder(): void {
    const s = this.state, screen = this.game!.screen;
    this.run(() => {
      if (screen === 27) {
        s.cmd_manta_centre_rudder();
        s.cmd_manta_centre_pitch();
      } else if (screen === 18) s.cmd_walrus_centre_rudder();
      else s.cmd_carrier_centre_rudder();
    });
  }

  /** The autopilot of whatever the screen controls. */
  autopilot(): void {
    const s = this.state, screen = this.game!.screen;
    this.run(() => (screen === 27 ? s.cmd_manta_autopilot() : screen === 18 ? s.cmd_walrus_autopilot() : s.cmd_carrier_autopilot()));
  }

  /** A waypoint from the map: the carrier's (screen 2), the selected Manta's (25) or Walrus's
   * (17), as picking it on that map and pressing "set course" does. */
  waypoint(x: number, z: number): boolean {
    const s = this.state, screen = this.game!.screen;
    return this.run(() =>
      screen === 25 ? s.cmd_set_manta_waypoint(x, z) : screen === 17 ? s.cmd_set_walrus_waypoint(x, z) : s.cmd_set_carrier_waypoint(x, z));
  }

  repairPriority(system: number, cls: number): boolean {
    return this.run(() => this.state.cmd_set_repair_priority(system, cls));
  }
}
