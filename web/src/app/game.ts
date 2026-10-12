// A game session: the RAM image, the simulation runner, the screen the player is on, and saves.
//
// New games start from the title-screen RAM image (web/public/data/title.bin, written by
// tools/export_web_data.py): the model's new_game() does what the original's new-game routine
// $42A8 does when "Start Strategy/Action Game" is clicked. Emulator states load as they are.
import { Runner } from "./runner.ts";

export const SCREEN = 0x718;
const RNG = 0x1236;               // two longs, $1236/$123A; setup depends only on them

/** Screen ids ([$718]) and their tabs, as the original groups them (docs/units.md §10). */
export const SCREENS = {
  bridge: 0, damage: 1, map: 2, resources: 3, messages: 4,
  turret: 8, flares: 9, drone: 10, radar: 11, weapons: 12,
  walrusArming: 16, walrusMap: 17, walrusControl: 18, walrusDock: 19, walrusStatus: 20,
  mantaArming: 24, mantaMap: 25, mantaDeck: 26, mantaControl: 27, mantaStatus: 28,
} as const;

/** VBLs per simulation pass on each screen, measured in Hatari (docs/units.md §1): the
 * original runs as fast as the screen draws. Unmeasured screens use 4. */
export const vblsPerPass = (screen: number) => VBLS_PER_PASS[screen] ?? 4;
const VBLS_PER_PASS: Record<number, number> = {
  0: 6, 1: 4, 2: 4, 3: 3, 4: 3, 8: 7, 9: 7, 10: 3, 11: 3, 12: 5,
  17: 4, 18: 3, 19: 6, 20: 3, 25: 4, 26: 6, 27: 3, 28: 3,
};

export type GameKind = "strategy" | "action" | "loaded";

export class Game {
  readonly runner: Runner;
  readonly name: string;
  readonly kind: GameKind;

  private constructor(ram: Uint8Array, name: string, kind: GameKind) {
    this.runner = new Runner(ram);
    this.name = name;
    this.kind = kind;
    this.runner.vblsPerPass = VBLS_PER_PASS[this.screen] ?? 4;
  }

  /** A state exported from the emulator (or one of our saves). */
  static load(ram: Uint8Array, name: string): Game {
    return new Game(ram, name, "loaded");
  }

  /** A fresh game from the title-screen image. */
  static create(title: Uint8Array, strategy: boolean, seed = randomSeed()): Game {
    const ram = title.slice();
    ram.set(seed, RNG);
    const game = new Game(ram, strategy ? "New strategy game" : "New action game", strategy ? "strategy" : "action");
    // $42A8 (the click on "Start Strategy/Action Game"), then the bridge screen it requests
    // (ui_frame picks up the tab request; the bridge's enter handler sets the camera). The first
    // pass follows in the runner. docs/units.md §16.
    const state = game.runner.state;
    const before = state.mem.buf.slice();
    state.new_game(strategy);
    state.new_game_screen();
    game.runner.state.to_ram(game.runner.ram);
    // the setup also writes memory outside the state regions (the static-noise texture
    // $5C2DA, globals restored from the boot snapshot, gauge flags): keep those too, so the
    // image (and an export of it) is what the original holds after $42A8
    const after = state.mem.buf, image = game.runner.ram;
    for (let i = 0; i < after.length; i++) if (after[i] !== before[i]) image[i] = after[i];
    game.runner.restart();
    game.runner.vblsPerPass = VBLS_PER_PASS[game.screen] ?? 4;
    return game;
  }

  get ram(): Uint8Array {
    return this.runner.ram;
  }

  get screen(): number {
    return (this.ram[SCREEN] << 8) | this.ram[SCREEN + 1];
  }

  /** Switch screens the way the player's tab and icon clicks do (the leave and enter handlers
   * set the camera, view pointers and more; docs/units.md §10.5). The pass rate follows. */
  setScreen(id: number): void {
    this.runner.state.cmd_switch_screen(id);
    this.runner.state.to_ram(this.ram);
    this.runner.vblsPerPass = VBLS_PER_PASS[this.screen] ?? 4;
  }
}

function randomSeed(): Uint8Array {
  const seed = new Uint8Array(8);
  crypto.getRandomValues(seed);
  return seed;
}
