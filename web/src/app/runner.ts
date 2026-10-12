// Runs the simulation model on a loaded RAM image in real time.
//
// The original is locked to the display: a game_main pass takes a whole number of VBLs (50 Hz),
// 6 of them on the bridge 3D view, and nothing is scaled by elapsed time (docs/units.md §1).
// Only the game clock ($8F2-$8FA) and the once-a-second flags ($8FC/$8FD, which trigger the
// logistics tick) run per VBL, in the VBL handler. The runner reproduces that: a 50 Hz VBL
// clock, and a simulation pass every `vblsPerPass` VBLs.
import { NotPorted, UnitState } from "../sim/index.ts";

const IN_GAME = 0x462;          // simulation running (PAUSE clears it)
const STATUS_MENU = 0x461;      // game-status menu open: simulation skipped
const END_REQUEST = 0x463;
const CLOCK = 0x8f2;            // days, hours, minutes, seconds, frames (words)
const NEW_SECOND = 0x8fc;       // $8FC and $8FD, set every 50 frames
const VBL_HZ = 50;

export type RunnerStatus = "running" | "paused" | "stopped";

export class Runner {
  /** The whole RAM image: game code and data, plus the state the model writes back after each
   * pass. The renderer reads it. */
  readonly ram: Uint8Array;
  readonly state: UnitState;
  passes = 0;
  vblsPerPass = 6;
  speed = 1;
  status: RunnerStatus = "paused";
  /** Why the simulation stopped (unported code reached, game over, ...). */
  stopReason: string | null = null;
  private vblDebt = 0;
  private vblsInPass = 0;

  constructor(ram: Uint8Array) {
    this.ram = ram;
    this.state = UnitState.from_ram(ram);
    if (!ram[IN_GAME]) this.stop("The game isn't running in this state (title screen or paused)");
    else if (ram[STATUS_MENU]) this.stop("The game-status menu is open in this state");
  }

  get clock(): { days: number; hours: number; minutes: number; seconds: number } {
    const w = (k: number) => (this.ram[CLOCK + 2 * k] << 8) | this.ram[CLOCK + 2 * k + 1];
    return { days: w(0), hours: w(1), minutes: w(2), seconds: w(3) };
  }

  play(): void {
    if (this.status !== "stopped") this.status = "running";
  }

  pause(): void {
    if (this.status !== "stopped") this.status = "paused";
  }

  /** Advances by real time (seconds); runs the passes that fall due. */
  advance(dt: number): void {
    if (this.status !== "running") return;
    this.vblDebt += Math.min(dt, 0.25) * VBL_HZ * this.speed;
    while (this.vblDebt >= 1 && this.status === "running") {
      this.vblDebt -= 1;
      this.vbl();
      if (++this.vblsInPass >= this.vblsPerPass) {
        this.vblsInPass = 0;
        this.pass();
      }
    }
  }

  /** One simulation pass now (single-stepping). */
  step(): void {
    if (this.status === "stopped") return;
    for (let k = this.vblsInPass; k < this.vblsPerPass; k++) this.vbl();
    this.vblsInPass = 0;
    this.pass();
  }

  private vbl(): void {
    const ram = this.ram;
    if (!ram[IN_GAME]) return;
    const w = (k: number) => (ram[CLOCK + 2 * k] << 8) | ram[CLOCK + 2 * k + 1];
    const set = (k: number, v: number) => {
      ram[CLOCK + 2 * k] = v >> 8;
      ram[CLOCK + 2 * k + 1] = v & 0xff;
    };
    // frames -> seconds -> minutes -> hours -> days
    const limits = [0, 24, 60, 60, VBL_HZ];
    let k = 4;
    set(k, w(k) + 1);
    while (k > 0 && w(k) >= limits[k]) {
      set(k, 0);
      k--;
      set(k, w(k) + 1);
      if (k === 3) {
        this.state.mem.set8(NEW_SECOND, 0xff);
        this.state.mem.set8(NEW_SECOND + 1, 0xff);
      }
    }
  }

  private pass(): void {
    try {
      this.state.sim_pass();
    } catch (e) {
      this.state.to_ram(this.ram);
      const msg = e instanceof NotPorted ? `Reached code that isn't ported yet: ${e.message}` : `${(e as Error).name}: ${(e as Error).message}`;
      this.stop(msg);
      if (!(e instanceof NotPorted)) console.error(e);
      return;
    }
    this.state.to_ram(this.ram);
    this.passes++;
    if (this.ram[END_REQUEST]) this.stop("Game over");
  }

  private stop(reason: string): void {
    this.status = "stopped";
    this.stopReason = reason;
  }
}
