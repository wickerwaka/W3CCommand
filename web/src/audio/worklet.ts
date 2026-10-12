// AudioWorklet processor: runs the sound engine (engine.ts) at 50 Hz on the audio clock and
// synthesises the PSG (ym2149.ts) sample-accurately, including the Timer A volume toggling.
//
// The chip is stepped at its internal 250 kHz rate; register writes land on the chip step of
// their MFP-cycle time; each output sample is the mean of the chip steps it covers (a box
// decimation filter), followed by a DC blocker (the ST's output capacitor).
// Commands from sound.ts are queued and run just before the next VBL, which is where the
// original's game code would have made them between two VBLs.
import { MFP_CLOCK, SoundEngine } from "./engine.ts";
import { CHIP_STEP_HZ, YM2149 } from "./ym2149.ts";

declare const sampleRate: number;
declare function registerProcessor(name: string, ctor: unknown): void;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}

export type SoundCommand =
  | ["play", number]
  | ["explosion", number, number, number, number]
  | ["record", number]
  | ["pass"]
  | ["reset"]
  | ["enabled", boolean];

export type WorkletMessage =
  | { type: "init"; image: Uint8Array }
  | { type: "cmd"; inputs: [number, Uint8Array][]; cmd: SoundCommand };

const CYCLES_PER_STEP = MFP_CLOCK / CHIP_STEP_HZ;
const GAIN = 0.9;

class CarrierSound extends AudioWorkletProcessor {
  private engine: SoundEngine | null = null;
  private readonly ym = new YM2149();
  private readonly queue: { inputs: [number, Uint8Array][]; cmd: SoundCommand }[] = [];
  private writes: number[] = [];
  private wi = 0;
  private step = 0;
  private target = 0;
  private readonly stepsPerSample = CHIP_STEP_HZ / sampleRate;
  private x1 = 0;
  private y1 = 0;
  private readonly r = 1 - (2 * Math.PI * 5) / sampleRate;

  constructor() {
    super();
    this.port.onmessage = (e: MessageEvent<WorkletMessage>) => {
      const m = e.data;
      if (m.type === "init") {
        this.engine = new SoundEngine(m.image);
        this.engine.vehicleReset();
        this.engine.on();
        this.engine.now = Math.floor(this.step * CYCLES_PER_STEP);
      } else this.queue.push(m);
    };
  }

  private runFrame(e: SoundEngine): void {
    for (const { inputs, cmd } of this.queue.splice(0)) {
      for (const [a, bytes] of inputs) e.mem.set(bytes, a);
      switch (cmd[0]) {
        case "play": e.play(cmd[1]); break;
        case "explosion": e.explosion(cmd[1], cmd[2], cmd[3], cmd[4]); break;
        case "record": e.start(cmd[1]); break;
        case "pass": e.vehiclePass(); break;
        case "reset": e.vehicleReset(); break;
        case "enabled": if (cmd[1]) e.on(); else e.off(); break;
      }
    }
    e.frame();
    this.writes = this.writes.slice(this.wi).concat(e.take());
    this.wi = 0;
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const out = outputs[0];
    const e = this.engine;
    const ym = this.ym;
    for (let i = 0; i < out[0].length; i++) {
      this.target += this.stepsPerSample;
      let acc = 0, n = 0;
      while (this.step < this.target) {
        if (e) {
          const cycle = this.step * CYCLES_PER_STEP;
          while (cycle >= e.now) this.runFrame(e);
          const w = this.writes;
          while (this.wi < w.length && w[this.wi] <= cycle) {
            ym.write(w[this.wi + 1], w[this.wi + 2]);
            this.wi += 3;
          }
        }
        ym.step();
        acc += ym.output();
        n++;
        this.step++;
      }
      const x = n ? acc / n : this.x1;
      const y = x - this.x1 + this.r * this.y1;
      this.x1 = x;
      this.y1 = y;
      for (const ch of out) ch[i] = y * GAIN;
    }
    return true;
  }
}

registerProcessor("carrier-sound", CarrierSound);
