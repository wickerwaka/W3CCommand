// The player's controls, mapped onto the model's own commands (web/src/sim/units_controls.ts,
// ported from the original's click and key handlers; docs/units.md §10). Held controls (speed
// keys, steering) apply before every pass, like a key held on the ST.
import type { Game } from "./game.ts";

const C_RUDDER = 0x642, POINTER_MODE = 0x457;
const RUDDER_RATE = 24;            // rudder change per pass while a steering key is held

export class Controls {
  private game: Game | null = null;
  private held = new Set<"faster" | "slower" | "left" | "right">();

  attach(game: Game): void {
    this.game = game;
    this.held.clear();
    game.runner.beforePass = () => this.beforePass();
  }

  private get state() {
    return this.game!.runner.state;
  }

  /** After a command, show its effect at once (the renderer and UI read the RAM image). */
  private sync(): void {
    this.game?.runner.state.to_ram(this.game.ram);
  }

  hold(what: "faster" | "slower" | "left" | "right", on: boolean): void {
    if (on) this.held.add(what);
    else this.held.delete(what);
  }

  private beforePass(): void {
    const s = this.state;
    if (this.held.has("faster")) s.cmd_carrier_speed_up();
    if (this.held.has("slower")) s.cmd_carrier_speed_down();
    const dir = (this.held.has("right") ? 1 : 0) - (this.held.has("left") ? 1 : 0);
    if (dir) {
      // the bridge steers with the mouse: make sure mouse-steer mode is on ($457 clear)
      if (s.mem.u8(POINTER_MODE)) s.cmd_toggle_mouse_steer();
      s.cmd_steer_to(s.mem.s16(C_RUDDER) + dir * RUDDER_RATE);
    }
  }

  stop(): void {
    this.state.cmd_carrier_stop();
    this.sync();
  }

  centreRudder(): void {
    this.state.cmd_carrier_centre_rudder();
    this.sync();
  }

  autopilot(): void {
    this.state.cmd_carrier_autopilot();
    this.sync();
  }

  /** Set the carrier's course to a world position, as picking it on the map and pressing
   * "set course" does. */
  waypoint(x: number, z: number): boolean {
    const ok = this.state.cmd_set_carrier_waypoint(x, z);
    this.sync();
    return ok;
  }

  repairPriority(system: number, cls: number): boolean {
    const ok = this.state.cmd_set_repair_priority(system, cls);
    this.sync();
    return ok;
  }
}
