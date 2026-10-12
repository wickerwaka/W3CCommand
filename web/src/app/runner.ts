// Runs the simulation model on a loaded RAM image in real time.
//
// The original is locked to the display: a game_main pass takes a whole number of VBLs (50 Hz),
// 6 of them on the bridge 3D view, and nothing is scaled by elapsed time (docs/units.md §1).
// Only the game clock ($8F2-$8FA) and the once-a-second flags ($8FC/$8FD, which trigger the
// logistics tick) run per VBL, in the VBL handler. The runner reproduces that: a 50 Hz VBL
// clock, and a simulation pass every `vblsPerPass` VBLs.
import { NotPorted, UnitState } from "../sim/index.ts";

const IN_GAME = 0x8f0;          // set while a game is on (frame_flip paces passes by it)
const RUNNING = 0x462;          // simulation running (PAUSE clears it); the clock needs it
const CLOCK = 0x8f2;            // days, hours, minutes, seconds, frames (words)
const NEW_SECOND = 0x8fc;       // $8FC and $8FD, set every 50 frames
const VBL_HZ = 50;

/** The result [$466] the end-of-game screens report (docs/units.md §2, §10.2). */
const GAME_OVER: Record<number, string> = {
  0: "Game over",
  1: "Game over: the carrier has been destroyed",
  2: "Game over: the enemy holds every island",
  3: "Victory: you hold every island",
  4: "Victory: the enemy carrier has been destroyed",
};
export const resultText = (r: number) => GAME_OVER[r] ?? "Game over";

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
  /** Runs before each pass: held controls feed their input then, as the original's IKBD
   * interrupt would have between passes. */
  beforePass: (() => void) | null = null;
  /** Runs after each completed pass (e.g. the engine sound, $20730, follows each pass). */
  afterPass: (() => void) | null = null;
  /** Set when the game ends: screen 35 (enemy carrier destroyed: the player may continue)
   * or 34 (game over), with the result [$466]. */
  gameOver: { screen: number; result: number } | null = null;
  private vblDebt = 0;
  private vblsInPass = 0;

  constructor(ram: Uint8Array) {
    this.ram = ram;
    this.state = UnitState.from_ram(ram);
    this.restart();
  }

  /** Re-read whether the state is a running game (after the model changed it, e.g. new_game). */
  restart(): void {
    this.status = "paused";
    this.stopReason = null;
    // $8F0 isn't a state region: a loaded image has it in the RAM, new_game() in the model
    if (!this.ram[IN_GAME] && !this.state.mem.u8(IN_GAME)) this.stop("No game is running in this state (title or game-over screen)");
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
    if (!ram[RUNNING]) return;
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

  /** One game_main loop, as the original: the UI's commands (beforePass), ui_frame (screen
   * requests), game_frame (pause/menu, screen_input, sim_pass, end-of-game timer), then
   * frame_flip's state part. */
  private pass(): void {
    const events = this.state.events.length;
    try {
      this.beforePass?.();
      this.state.ui_frame();          // $F636: screen and tab requests (the UI's clicks are commands)
      this.state.game_frame();
      // the original then renders the screen: the state its render writes (map counts and
      // cursors, radar elements, static noise RNG, ...; docs/units.md §10.8), then frame_flip
      // $17F48, whose state part puts the pointer back after a full redraw ($458)
      this.state.render_effects();
      this.state.frame_flip_flags();
    } catch (e) {
      this.state.to_ram(this.ram);
      const msg = e instanceof NotPorted ? `Reached code that isn't ported yet: ${e.message}` : `${(e as Error).name}: ${(e as Error).message}`;
      this.stop(msg);
      if (!(e instanceof NotPorted)) console.error(e);
      return;
    }
    this.state.to_ram(this.ram);
    this.passes++;
    this.afterPass?.();
    const over = this.state.events.slice(events).find((e) => e[0] === "game over");
    if (over) {
      this.gameOver = { screen: Number(over[1]), result: Number(over[2]) };
      // screen 35 asks whether to continue (the game loop keeps running, the simulation is
      // held by the status flag $461); screen 34 is the end
      if (this.gameOver.screen === 34) this.stop(resultText(this.gameOver.result));
    }
  }

  stop(reason: string): void {
    this.status = "stopped";
    this.stopReason = reason;
  }
}
