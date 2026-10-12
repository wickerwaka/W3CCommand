// YM2149 (Atari ST PSG, 2 MHz) emulation, stepped at the chip's internal rate of 250 kHz
// (master clock / 8), as in MAME's ay8910.cpp for the YM2149:
// - tone: a 12-bit counter per channel; the square output toggles every TP steps, so
//   f = 2 MHz / (16 TP); TP 0 acts as 1;
// - noise: a 17-bit LFSR (taps 0 and 3) shifted every 2 NP steps (f = 2 MHz / (16 NP)), NP 0
//   acts as 1; the output is bit 0;
// - mixer (register 7, active low): a channel's gate is (tone | tone disabled) & (noise | noise
//   disabled), so a channel with both disabled outputs its volume as DC;
// - envelope: 32 steps per ramp, one step every EP chip steps (a ramp lasts 256 EP / 2 MHz);
//   the 16 shapes come from the attack/alternate/hold/continue bits; writing register 13
//   restarts it;
// - DAC: 32 logarithmic levels 1.5 dB apart (Hatari's table measured on an ST; level 0 is
//   silence). A fixed volume v uses level 2v + 1 (v = 0: silence).
// Deviations: the three channels are summed linearly (the ST ties the outputs together through
// resistors, which compresses loud chords slightly), and there is no analogue output filter
// beyond what the caller applies (worklet.ts: box decimation and a DC blocker).
//
// The envelope starts held at 0 (shape 9, finished): the game never writes register 13, so its
// one envelope-volume effect (id 9, the Manta missile lock) gets whatever TOS left, which is a
// finished single-shot shape (keyclick 3 / bell 9): that voice is silent, as on the ST.

export const CHIP_STEP_HZ = 250000;

/** The 32 DAC levels, normalised to 1 at full scale. */
export function levelTable(): Float64Array {
  const t = new Float64Array(32);
  for (let n = 1; n < 32; n++) t[n] = 10 ** ((-(31 - n) * 1.5) / 20);
  return t;
}
const LEVEL = levelTable();

export class YM2149 {
  readonly r = new Uint8Array(16);
  private toneCount = [0, 0, 0];
  private toneOut = [0, 0, 0];
  private noiseCount = 0;
  private noisePrescale = 0;
  private lfsr = 1;
  private envCount = 0;
  private envStep = 0;
  private envAttack = 0;
  private envHold = 0;
  private envAlternate = 0;
  private envHolding = 1;

  constructor() {
    this.write(13, 9);
    this.envHolding = 1;
    this.envStep = 0;
  }

  write(reg: number, v: number): void {
    reg &= 15;
    v &= 0xff;
    this.r[reg] = v;
    if (reg === 13) {
      this.envAttack = v & 4 ? 0x1f : 0;
      if (!(v & 8)) {
        this.envHold = 1;
        this.envAlternate = this.envAttack;
      } else {
        this.envHold = v & 1;
        this.envAlternate = v & 2;
      }
      this.envStep = 0x1f;
      this.envHolding = 0;
      this.envCount = 0;
    }
  }

  /** Advance the noise LFSR one shift; returns the new output bit. */
  stepNoiseLfsr(): number {
    const l = this.lfsr;
    this.lfsr = (l ^ ((((l & 1) ^ ((l >> 3) & 1)) << 17))) >>> 1;
    return this.lfsr & 1;
  }

  /** One chip step (8 master clocks). */
  step(): void {
    const r = this.r;
    for (let ch = 0; ch < 3; ch++) {
      const period = (r[2 * ch] | ((r[2 * ch + 1] & 15) << 8)) || 1;
      if (++this.toneCount[ch] >= period) {
        this.toneCount[ch] = 0;
        this.toneOut[ch] ^= 1;
      }
    }
    if (++this.noiseCount >= ((r[6] & 31) || 1)) {
      this.noiseCount = 0;
      this.noisePrescale ^= 1;
      if (!this.noisePrescale) this.stepNoiseLfsr();
    }
    if (!this.envHolding && ++this.envCount >= ((r[11] | (r[12] << 8)) || 1)) {
      this.envCount = 0;
      if (--this.envStep < 0) {
        if (this.envHold) {
          if (this.envAlternate) this.envAttack ^= 0x1f;
          this.envHolding = 1;
          this.envStep = 0;
        } else {
          if (this.envAlternate && this.envStep & 0x20) this.envAttack ^= 0x1f;
          this.envStep &= 0x1f;
        }
      }
    }
  }

  /** Current envelope level (0..31). */
  envLevel(): number {
    return this.envStep ^ this.envAttack;
  }

  /** The mixed output (0..1): the three channels' DAC levels summed, / 3. */
  output(): number {
    const r = this.r, mix = r[7], noise = this.lfsr & 1;
    let sum = 0;
    for (let ch = 0; ch < 3; ch++) {
      const gate = (this.toneOut[ch] | ((mix >> ch) & 1)) & (noise | ((mix >> (ch + 3)) & 1));
      if (!gate) continue;
      const v = r[8 + ch];
      sum += LEVEL[v & 0x10 ? this.envLevel() : v & 15 ? 2 * (v & 15) + 1 : 0];
    }
    return sum / 3;
  }

  /** Alias of output() (used by the checks). */
  levelOut(): number {
    return this.output();
  }
}
