// Game sound: the original sound engine (engine.ts) and a YM2149 (ym2149.ts) running in an
// AudioWorklet (worklet.ts), clocked at 50 Hz by the audio hardware.
//
//   const sound = new Sound(game.ram);
//   button.onclick = () => sound.start();     // browsers need a user gesture
//   sound.play(4);                            // jsr $20B90 + id (effect table $20BD0)
//   sound.explosion(level, x, y, z);          // $20C30/$20C08: graded by distance to [$A02]
//   sound.pass();                             // $20730, once per game pass (vehicle sounds)
//   sound.muted = true;
//
// Every call copies the game state the engine reads (engine.ts INPUT_REGIONS: the controlled
// object [$A02] and its kind [$A14], the bay records, vehicles 2-9, the option bytes
// $1248/$1249) from `ram`, so the engine sees the game as it is at the call. The engine's own
// state (channels, RNG copy for Timer A) lives in the worklet. docs/units.md §17 lists where the
// game makes each sound.
import { GAME_OVER_RECORD, INPUT_REGIONS } from "./engine.ts";
import type { SoundCommand, WorkletMessage } from "./worklet.ts";
import workletUrl from "./worklet.ts?worker&url";

const CONTROLLED = 0xa02;

export class Sound {
  /** The game's RAM image (set it again when a new game or a loaded state replaces it). */
  ram: Uint8Array;
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private gain: GainNode | null = null;
  private starting: Promise<void> | null = null;
  private _muted = false;
  private _volume = 0.5;

  constructor(ram: Uint8Array) {
    this.ram = ram;
  }

  /** Creates the audio graph; call from a user gesture (click/key). Safe to call again. */
  start(): Promise<void> {
    this.starting ??= this.open();
    if (this.ctx?.state === "suspended") void this.ctx.resume();
    return this.starting;
  }

  private async open(): Promise<void> {
    const ctx = new AudioContext({ latencyHint: "interactive" });
    this.ctx = ctx;
    await ctx.audioWorklet.addModule(workletUrl);
    const node = new AudioWorkletNode(ctx, "carrier-sound", { numberOfInputs: 0, outputChannelCount: [1] });
    const gain = ctx.createGain();
    gain.gain.value = this._muted ? 0 : this._volume;
    node.connect(gain).connect(ctx.destination);
    this.node = node;
    this.gain = gain;
    this.post({ type: "init", image: this.ram.slice() });
    if (typeof document !== "undefined")
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) void ctx.suspend();
        else if (!this._muted) void ctx.resume();
      });
  }

  get started(): boolean {
    return this.node !== null;
  }

  get muted(): boolean {
    return this._muted;
  }
  set muted(m: boolean) {
    this._muted = m;
    if (this.gain) this.gain.gain.value = m ? 0 : this._volume;
  }

  /** Output volume, 0..1. */
  get volume(): number {
    return this._volume;
  }
  set volume(v: number) {
    this._volume = Math.max(0, Math.min(1, v));
    if (this.gain && !this._muted) this.gain.gain.value = this._volume;
  }

  /** `jsr $20B90` with an inline effect id (0..13; 7, 8 and 13 are silent). */
  play(effect: number): void {
    this.cmd(["play", effect]);
  }

  /** The distance-graded explosion ($20C30 / $20C08), level 0..2, at world position x, y, z
   * (an object's longs +$A/+$E/+$12; for an island-buffer entry, its words + [$A4A/$A4E/$A52]). */
  explosion(level: number, x: number, y: number, z: number): void {
    this.cmd(["explosion", level, x | 0, y | 0, z | 0]);
  }

  /** explosion() at an object in `ram` ($20C30 with A1 = the object). */
  explosionAt(level: number, object: number): void {
    const v = new DataView(this.ram.buffer, this.ram.byteOffset);
    this.explosion(level, v.getInt32(object + 0xa), v.getInt32(object + 0xe), v.getInt32(object + 0x12));
  }

  /** The loudest explosion, as the game-over screen plays it when the carrier was destroyed
   * ($14446: `[$466]` = 1). Not gated by the sound-effects option. */
  gameOver(): void {
    this.cmd(["record", GAME_OVER_RECORD]);
  }

  /** $20730, once per game pass: the controlled Manta/Walrus engine and the nearest other
   * vehicle's sound (background-noise option $1248). */
  pass(): void {
    this.cmd(["pass"]);
  }

  /** $20716: stop everything and reset the vehicle sounds (new game, loaded state). */
  reset(): void {
    this.cmd(["reset"]);
  }

  /** $20434 / $2045E: the game's pause silences the PSG and freezes the channels. */
  setEnabled(on: boolean): void {
    this.cmd(["enabled", on]);
  }

  private cmd(cmd: SoundCommand): void {
    if (!this.node) return;
    const inputs: [number, Uint8Array][] = INPUT_REGIONS.map(([a, n]) => [a, this.ram.slice(a, a + n)]);
    const a02 = (this.ram[CONTROLLED] << 24 | this.ram[CONTROLLED + 1] << 16 | this.ram[CONTROLLED + 2] << 8 |
      this.ram[CONTROLLED + 3]) >>> 0;
    if (a02 && a02 + 0x20 <= this.ram.length) inputs.push([a02, this.ram.slice(a02, a02 + 0x20)]);
    this.post({ type: "cmd", inputs, cmd });
  }

  private post(m: WorkletMessage): void {
    this.node?.port.postMessage(m);
  }
}
