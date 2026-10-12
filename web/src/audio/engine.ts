// The game's sound engine ($20188-$20CD8), ported routine by routine.
//
// It runs on a private copy of the 512 KB RAM image (`mem`), at the original addresses: the
// six channel structures at $4CBD4, the engine's variables in the code area ($20482-$20485,
// $20640-$20655, $2070E-$20715), the vehicle-sound globals $A06-$A27 and the RNG at $1236
// (Timer A advances it; in the original that is the game's RNG, here it's a private copy so the
// simulation stays deterministic). The game state the vehicle sounds read ($A02, $A14, $70C,
// the bay records, objects, option bytes) is copied in by the caller (sound.ts: syncInputs).
// docs/memory-map.md "Sound" documents the engine; tools/stsound.py runs the original under
// Unicorn and web/scripts/check-sound.ts compares the two write for write.
//
// Output: PSG register writes as flat (time, register, value) triples in `writes`, time in MFP
// clock cycles (2.4576 MHz, 49152 per 50 Hz frame). frame() runs one VBL: the VBL routine at
// the frame start, then the Timer A interrupts that fall inside the frame.
//
// Only erasable TypeScript syntax: Node runs the checker by stripping types.

export const MFP_CLOCK = 2457600;
export const FRAME_CYCLES = MFP_CLOCK / 50;
/** Timer A handler duration: about 430 CPU cycles at 8 MHz, in MFP cycles. */
export const HANDLER_CYCLES = 132;
const PRESCALE = [0, 4, 10, 16, 50, 64, 100, 200];

export const CHANNELS = [0x4cbd4, 0x4cc1e, 0x4cc68, 0x4ccb2, 0x4ccfc, 0x4cd46]; // $20466
export const CHANNEL_SIZE = 0x4a;
export const EFFECT_TABLE = 0x20bd0; // 14 longs: record, 0 = none, negative = routine
export const EFFECT_COUNT = 14;
export const GRADED_TABLE = 0x20cb4; // 9 explosion records, quietest first
export const GAME_OVER_RECORD = 0x4b72e; // played by $14446 when the carrier is destroyed

// engine variables (code area)
const NOISE_SET = 0x20482, ENV_SET = 0x20483, ENABLED = 0x20484;
const ALLOC_FLAGS = 0x20640, ALLOC_DUMMY = 0x20654;
const SAVED_VOL = 0x2070e, TOGGLE = 0x20712, NOISE6 = 0x20713, CUR_TACR = 0x20714, BUZZ_MASK = 0x20715;
const RNG = 0x1236;
// game state read by the vehicle sounds
const CONTROLLED = 0xa02, CAM_MODE = 0xa14, MANTA_OK = 0x70c, WALRUS_OK = 0x70e;
const OPT_NOISE = 0x1248, OPT_SFX = 0x1249;
const MANTA_RECS = 0x496, WALRUS_RECS = 0x570, BAY_SIZE = 0x34;
export const VEHICLE_OBJECTS = 0x5df5a; // objects 2..9: Mantas 0-3, Walruses 0-3
const OBJECT_SIZE = 0x140;
// vehicle-sound globals
const PREV_CONTROLLED = 0xa06, NEAR_OBJ = 0xa0a, NEAR_DIST = 0xa0e, NEAR_TIMER = 0xa10, NEAR_KIND = 0xa12;
const ENGINE_KIND = 0xa16, MANTA_CH = 0xa18, MANTA_SPEED = 0xa1c, WALRUS_CH = 0xa1e, WALRUS_SPEED = 0xa22;
const NEAR_CH = 0xa24;

/** Regions of `mem` that hold the engine's state (compared against the original). */
export const STATE_REGIONS: [string, number, number][] = [
  ["channels", CHANNELS[0], 6 * CHANNEL_SIZE],
  ["flags $20482", NOISE_SET, 4],
  ["voice allocation $20640", ALLOC_FLAGS, 0x16],
  ["timer A vars $2070E", SAVED_VOL, 8],
  ["vehicle sound $A06", PREV_CONTROLLED, 0xa14 - PREV_CONTROLLED],
  ["vehicle sound $A16", ENGINE_KIND, 0xa28 - ENGINE_KIND],
  ["rng $1236", RNG, 8],
];

/** Regions of the game RAM the engine reads (besides the object a play/explosion names). */
export const INPUT_REGIONS: [number, number][] = [
  [CONTROLLED, 4], [CAM_MODE, 2], [MANTA_OK, 4], [OPT_NOISE, 2],
  [MANTA_RECS, WALRUS_RECS + 4 * BAY_SIZE - MANTA_RECS],
  [VEHICLE_OBJECTS, 8 * OBJECT_SIZE],
];

const w16 = (v: number) => v & 0xffff;
const s16 = (v: number) => ((v & 0xffff) ^ 0x8000) - 0x8000;
const s8 = (v: number) => ((v & 0xff) ^ 0x80) - 0x80;
const u32 = (v: number) => v >>> 0;
const s32 = (v: number) => v | 0;
const MASK = 0x7ffff;

/** MFP Timer A in delay mode; times are absolute MFP cycles (see tools/stsound.py). */
export class TimerA {
  tacr = 0;
  tdr = 0;
  counter = 256;
  phase = 0;
  busyUntil = 0;
  pending = -1;

  setControl(v: number): void {
    v &= 7;
    if (v !== this.tacr) {
      this.tacr = v;
      this.phase = PRESCALE[v];
      this.pending = -1;
    }
    if (!v) this.pending = -1;
  }

  setData(v: number): void {
    this.tdr = v & 0xff;
    if (!this.tacr) this.counter = this.tdr || 256;
  }

  private advance(cycles: number): void {
    if (!this.tacr || cycles <= 0) return;
    const p = PRESCALE[this.tacr];
    if (cycles < this.phase) this.phase -= cycles;
    else {
      const e = cycles - this.phase;
      this.counter -= 1 + Math.floor(e / p);
      this.phase = p - (e % p);
    }
  }

  /** Runs the interrupts in [now, end); handler(t) runs the routine at time t. */
  run(now: number, end: number, handler: (t: number) => void): number {
    for (;;) {
      const tt = this.tacr ? now + this.phase + (this.counter - 1) * PRESCALE[this.tacr] : Infinity;
      const ts = this.pending >= 0 ? this.pending : Infinity;
      if (Math.min(tt, ts) >= end) {
        this.advance(end - now);
        return end;
      }
      if (ts <= tt) {
        this.advance(ts - now);
        now = ts;
        this.pending = -1;
        this.busyUntil = now + HANDLER_CYCLES;
        handler(now);
      } else {
        now = tt;
        this.counter = this.tdr || 256;
        this.phase = PRESCALE[this.tacr];
        if (now >= this.busyUntil) {
          this.busyUntil = now + HANDLER_CYCLES;
          handler(now);
        } else if (this.pending < 0) this.pending = this.busyUntil;
      }
    }
  }
}

export class SoundEngine {
  readonly mem: Uint8Array;
  readonly timer = new TimerA();
  /** MFP cycles since the engine started. */
  now = 0;
  /** PSG writes since the last take(): time, register, value. */
  writes: number[] = [];

  /** `image`: a 512 KB RAM image with the payload (sound records, tables, RNG seed). */
  constructor(image: Uint8Array) {
    this.mem = image.slice(0, 0x80000);
  }

  // ---- memory ------------------------------------------------------------------------------
  r8(a: number): number {
    return this.mem[a & MASK];
  }
  r16(a: number): number {
    return (this.mem[a & MASK] << 8) | this.mem[(a + 1) & MASK];
  }
  r32(a: number): number {
    return u32(this.r16(a) * 0x10000 + this.r16(a + 2));
  }
  w8(a: number, v: number): void {
    this.mem[a & MASK] = v & 0xff;
  }
  w16(a: number, v: number): void {
    this.mem[a & MASK] = (v >> 8) & 0xff;
    this.mem[(a + 1) & MASK] = v & 0xff;
  }
  w32(a: number, v: number): void {
    this.w16(a, (v >>> 16) & 0xffff);
    this.w16(a + 2, v & 0xffff);
  }
  private psg(reg: number, v: number): void {
    this.writes.push(this.now, reg & 15, v & 0xff);
  }

  /** Returns and clears the writes logged so far. */
  take(): number[] {
    const w = this.writes;
    this.writes = [];
    return w;
  }

  // ---- channels -----------------------------------------------------------------------------
  /** $20188: free all six channels. */
  init(): void {
    for (const c of CHANNELS) this.clear(c);
  }

  /** $201F8: zero a channel structure (37 words). */
  private clear(a6: number): void {
    for (let i = 0; i < CHANNEL_SIZE; i++) this.w8(a6 + i, 0);
  }

  /** $201C0: the first free channel; if none, `found` is false and a6 is the last channel. */
  private findFree(): { a6: number; found: boolean; a1: number } {
    let a6 = 0;
    for (const [i, c] of CHANNELS.entries()) {
      a6 = c;
      if (this.r16(c) === 0) return { a6, found: true, a1: 0x20466 + 4 * (i + 1) };
    }
    return { a6, found: false, a1: 0x20482 }; // A1 is left past the list's terminator
  }

  /** $201D0: the script channel of lowest first priority that is <= the record's (later wins
   * ties); 0 if none. Vehicle-sound channels (word 1) are never taken. */
  private steal(rec: number): number {
    let d0 = this.r16(rec), a6 = 0;
    for (const c of CHANNELS) {
      if (this.r16(c) === 1) continue;
      if (s16(d0) < s16(this.r16(c + 0x26))) continue;
      d0 = this.r16(c + 0x26);
      a6 = c;
    }
    return a6;
  }

  /** $2019A: start a sound record (6-byte header: priorities of sub-voices 0-2, then script). */
  start(rec: number): void {
    let { a6, found } = this.findFree();
    if (!found) {
      a6 = this.steal(rec);
      if (a6 === 0) return;
      this.clear(a6);
    }
    this.w32(a6 + 0x26, this.r32(rec));
    this.w16(a6 + 0x2a, this.r16(rec + 4));
    this.w32(a6 + 2, u32(rec + 6));
    this.w16(a6, 0xffff);
  }

  /** $20B90: play an entry of the effect table. */
  play(id: number): void {
    const v = this.r32(EFFECT_TABLE + s16(id * 4));
    if (v === 0) return;
    if (v & 0x80000000) throw new Error(`effect ${id}: routine entries aren't used by the game`);
    this.start(v);
  }

  /** $20C30 / $20C08 after loading the position: one of the 9 graded explosions by the
   * Chebyshev distance to the controlled object [$A02]. x, y, z: world longs. */
  explosion(level: number, x: number, y: number, z: number): void {
    if (this.r8(OPT_SFX)) return;
    const a0 = this.r32(CONTROLLED);
    if (a0 === 0) return;
    const d: number[] = [];
    for (const [v, off] of [[x, 0xa], [y, 0xe], [z, 0x12]]) {
      const delta = s32(v - this.r32(a0 + off));
      if (delta !== s16(delta)) return;
      d.push(s16(delta < 0 ? -delta : delta)); // neg.w: -32768 stays negative
    }
    let [d1, d2, d3] = d;
    if (d2 < d1) d2 = d1;
    if (d3 < d2) d3 = d2;
    let d0 = w16(level + 6);
    const e = w16(d3 - 5000);
    if (!(e & 0x8000)) {
      const k = e >> 13;
      if (d0 < k) return;
      d0 -= k;
    }
    this.start(this.r32(GRADED_TABLE + s16(d0 * 4)));
  }

  /** $20C30 with A1 = an object in `mem` (longs at +$A, +$E, +$12). */
  explosionObject(level: number, a1: number): void {
    if (this.r8(OPT_SFX)) return;
    this.explosion(level, this.r32(a1 + 0xa), this.r32(a1 + 0xe), this.r32(a1 + 0x12));
  }

  /** $20C08 with A1 = an island-buffer entry (words at +$A, +$C, +$E plus $A4A/$A4E/$A52). */
  explosionWord(level: number, a1: number): void {
    if (this.r8(OPT_SFX)) return;
    this.explosion(level, s16(this.r16(a1 + 0xa)) + this.r32(0xa4a), s16(this.r16(a1 + 0xc)) + this.r32(0xa4e),
      s16(this.r16(a1 + 0xe)) + this.r32(0xa52));
  }

  /** $20434: sound off (pause): stops the VBL routine and silences the PSG. */
  off(): void {
    this.w8(ENABLED, 0);
    this.psg(7, 0x7f);
    this.psg(8, 0);
    this.psg(9, 0);
    this.psg(10, 0);
  }

  /** $2045E: sound on. */
  on(): void {
    this.w8(ENABLED, 0xff);
  }

  // ---- channel scripts ($2020A) -----------------------------------------------------------
  private src(a6: number, op: number, arg: number, pc: { a5: number }): number {
    switch (op & 3) {
      case 0: return this.r16(a6 + 6 + 2 * (arg >> 4));
      case 1: return this.r16(a6 + 0x2e + 2 * (arg >> 4));
      case 2: return arg >> 4 || 16;
      default: {
        const v = this.r16(pc.a5);
        pc.a5 = u32(pc.a5 + 2);
        return v;
      }
    }
  }

  private runChannel(a6: number): void {
    if (!(this.r16(a6) & 0x8000)) return;
    const wait = this.r8(a6 + 0x2c);
    if (wait) {
      this.w8(a6 + 0x2c, wait - 1);
      return;
    }
    const pc = { a5: this.r32(a6 + 2) };
    for (;;) {
      const op = this.r8(pc.a5), arg = this.r8(pc.a5 + 1);
      pc.a5 = u32(pc.a5 + 2);
      const k = op & 0x7c;
      if (k >= 0x40 || k === 0x3c) {
        this.clear(a6);
        return;
      }
      const dest = a6 + (op & 0x80 ? 0x2e : 6) + 2 * (arg & 15);
      if (k <= 0x24) {
        const s = this.src(a6, op, arg, pc);
        const d = this.r16(dest);
        let skip = false;
        switch (k) {
          case 0x00: this.w16(dest, s); break;
          case 0x04: this.w16(dest, d + s); break;
          case 0x08: this.w16(dest, d - s); break;
          case 0x0c: this.w16(dest, d & s); break;
          case 0x10: this.w16(dest, d | s); break;
          case 0x14: this.w16(dest, d ^ s); break;
          case 0x18: skip = s === d; break;
          case 0x1c: skip = s !== d; break;
          case 0x20: skip = s16(s) < s16(d); break;
          case 0x24: skip = s16(s) > s16(d); break;
        }
        if (skip) {
          // $203CE: skip the next op, and its word operand if its source bit 1 is set (both
          // immediate modes), except for op $20
          const w = this.r16(pc.a5);
          pc.a5 = u32(pc.a5 + 2);
          if ((w & 0x7c00) !== 0x2000 && w & 0x200) pc.a5 = u32(pc.a5 + 2);
        }
        continue;
      }
      switch (k) {
        case 0x28: // jump
          pc.a5 = u32(pc.a5 + s8(arg));
          break;
        case 0x2c: { // decrement R[op & 3], jump if not zero
          const r = a6 + 6 + 2 * (op & 3);
          const v = w16(this.r16(r) - 1);
          this.w16(r, v);
          if (v !== 0) pc.a5 = u32(pc.a5 + s8(arg));
          break;
        }
        case 0x30: // tone (bit 7 clear) / noise enable of sub-voice arg & 15
          this.w16(a6 + (op & 0x80 ? 0x44 : 0x3e) + 2 * (arg & 15), op & 1 ? 0xffff : 0);
          break;
        case 0x34: // wait arg frames
          this.w32(a6 + 2, pc.a5);
          this.w8(a6 + 0x2c, arg);
          return;
        case 0x38: { // priority of sub-voice arg & 15
          const s = this.src(a6, op, arg, pc);
          this.w16(a6 + 0x26 + 2 * (arg & 15), s);
          break;
        }
      }
    }
  }

  // ---- per VBL ($20486) ---------------------------------------------------------------------
  /** $20600: the highest-priority sub-voice not yet given a PSG voice (channels 5..0,
   * sub-voices 2..0; the first found wins ties). Priority <= 0 never plays. */
  private allocate(): { chan: number; off: number } | null {
    let a1 = ALLOC_FLAGS, a2 = ALLOC_DUMMY, d2 = 0, chan = 0, off = 0;
    for (let d7 = 5; d7 >= 0; d7--) {
      const a6 = CHANNELS[d7];
      if (this.r16(a6) === 0) {
        a1 += 3;
        continue;
      }
      for (let d6 = 4; d6 >= 0; d6 -= 2, a1++) {
        if (this.r8(a1) === 0 && s16(this.r16(a6 + 0x26 + d6)) > s16(d2)) {
          a2 = a1;
          d2 = this.r16(a6 + 0x26 + d6);
          chan = d7;
          off = d6;
        }
      }
    }
    this.w8(a2, 0xff);
    return d2 ? { chan, off } : null;
  }

  /** $20486: run the scripts and write the PSG. */
  vbl(): void {
    if (!this.r8(ENABLED)) return;
    for (const c of CHANNELS) this.runChannel(c);
    for (let i = 0; i < 20; i++) this.w8(ALLOC_FLAGS + i, 0);
    this.w16(NOISE_SET, 0);
    let mixer = 0x7f, buzz = 0;
    for (let d3 = 2; d3 >= 0; d3--) {
      const v = this.allocate();
      if (!v) {
        this.psg(8 + d3, 0);
        continue;
      }
      const a0 = CHANNELS[v.chan], d1 = v.off;
      if (this.r16(a0 + 0x3e + d1)) {
        mixer &= ~(1 << d3);
        this.psg(2 * d3, this.r8(a0 + 0x2f + d1));
        this.psg(2 * d3 + 1, this.r8(a0 + 0x2e + d1));
      }
      if (this.r16(a0 + 0x44 + d1)) {
        if (!this.r8(NOISE_SET)) {
          this.w8(NOISE_SET, 0xff);
          const n = this.r8(a0 + 0x35);
          this.psg(6, n);
          const d2 = n >> 5;
          let buzzOn = true;
          if (d2 !== this.r8(CUR_TACR)) {
            this.timer.setControl(d2);
            this.w8(CUR_TACR, d2);
            buzzOn = d2 !== 0;
          }
          if (buzzOn) this.w8(NOISE6, (n & 0x1f) * 6);
        }
        mixer &= ~(1 << (d3 + 3));
        buzz |= 1 << d3;
      }
      const vol = this.r8(a0 + 0x37 + d1);
      this.w8(SAVED_VOL + d3, vol);
      this.psg(8 + d3, vol);
      if (vol & 0x10 && !this.r8(ENV_SET)) {
        this.psg(11, this.r8(a0 + 0x3d));
        this.psg(12, this.r8(a0 + 0x3c));
        this.w8(ENV_SET, 0xff);
      }
    }
    if (!this.r8(NOISE_SET)) {
      this.timer.setControl(0);
      this.w8(CUR_TACR, 0);
    }
    this.psg(7, mixer);
    this.w8(BUZZ_MASK, buzz);
  }

  /** $20668: Timer A. Advances the RNG, reloads the timer from it, and switches the volumes of
   * the noise voices off and back on (every other interrupt). */
  timerA(t: number): void {
    this.now = t;
    let d0 = this.r32(RNG), d1 = this.r32(RNG + 4);
    const lo0 = d0 & 0xffff, lo1 = d1 & 0xffff, hi0 = d0 >>> 16, hi1 = d1 >>> 16;
    const dbl = lo0 * 2, x1 = dbl >> 16, lo0n = dbl & 0xffff;
    const s1 = lo0n + lo1 + x1, x2 = s1 >> 16;
    const s2 = hi0 + hi1 + x2;
    d0 = u32((s2 & 0xffff) * 0x10000 + (s1 & 0xffff));
    d1 = u32(hi0 * 0x10000 + lo0n);
    this.w32(RNG, d0);
    this.w32(RNG + 4, d1);
    this.timer.setData(d0 & 0xff);
    const mask = this.r8(BUZZ_MASK);
    const tog = this.r8(TOGGLE) ^ 0xff;
    this.w8(TOGGLE, tog);
    for (let v = 0; v < 3; v++)
      if (mask & (1 << v)) this.psg(8 + v, tog & 0x80 ? this.r8(SAVED_VOL + v) : 0);
  }

  /** One 50 Hz frame: the VBL routine, then Timer A until the next frame. */
  frame(): void {
    const t0 = this.now;
    this.vbl();
    this.now = this.timer.run(t0, t0 + FRAME_CYCLES, (t) => this.timerA(t));
  }

  // ---- vehicle sounds, once per game pass ($20730) ------------------------------------------
  /** $20716: reset the vehicle sounds and free all channels (new game, screen changes). */
  vehicleReset(): void {
    this.w16(ENGINE_KIND, 0xffff);
    this.w8(PREV_CONTROLLED, 0xff);
    this.w16(NEAR_TIMER, 0);
    this.w8(NEAR_OBJ, 0xff);
    this.init();
  }

  /** $20730: engine sound of the controlled vehicle, then the nearest other vehicle. */
  vehiclePass(): void {
    this.engineSound();
    this.proximity();
  }

  private turnTowards(target: number, cur: number, step: number): number {
    let d0 = w16(target - cur);
    const neg = d0 & 0x8000;
    if (neg) d0 = w16(-d0);
    if (d0 > step) d0 = step;
    if (neg) d0 = w16(-d0);
    return w16(cur + d0);
  }

  /** $2073A */
  private engineSound(): void {
    const a1 = this.r32(CONTROLLED);
    if (a1 !== this.r32(PREV_CONTROLLED)) {
      this.w32(PREV_CONTROLLED, a1);
      this.proximityStop();
      const old = s16(this.r16(ENGINE_KIND));
      if (old >= 0) {
        // $2088C
        if (old === 0) this.clear(this.r32(MANTA_CH));
        else if (old === 1) this.clear(this.r32(WALRUS_CH));
        else if (old === 3) this.proximity();
        else if (old !== 2) throw new Error(`engine kind ${old}`);
      }
      if (a1 === 0) {
        this.w16(ENGINE_KIND, 0xffff);
        return;
      }
      const kind = this.r16(CAM_MODE);
      this.w16(ENGINE_KIND, kind);
      if (this.r8(OPT_NOISE)) {
        this.w16(ENGINE_KIND, 0xffff);
        return;
      }
      // $2089C: 0 Manta, 1 Walrus; 2 carrier and 3 drone have none
      if (kind === 0) this.vehicleChannel(MANTA_CH, MANTA_SPEED, 4);
      else if (kind === 1) this.vehicleChannel(WALRUS_CH, WALRUS_SPEED, 2);
      else if (kind !== 2 && kind !== 3) throw new Error(`engine kind ${kind}`);
    }
    const kind = s16(this.r16(ENGINE_KIND));
    if (kind < 0) return;
    // $207A8
    if (kind === 0) this.engineFrame(a1, MANTA_CH, MANTA_OK, MANTA_RECS, MANTA_SPEED, true);
    else if (kind === 1) this.engineFrame(a1, WALRUS_CH, WALRUS_OK, WALRUS_RECS, WALRUS_SPEED, false);
    else if (kind === 3) this.w16(ENGINE_KIND, 0xffff);
  }

  /** $208AC / $208E0: take a channel (channel 5 if none is free) for a vehicle sound. */
  private vehicleChannel(ptr: number, speed: number, noise: number): void {
    const { a6 } = this.findFree();
    this.w32(ptr, a6);
    this.w16(speed, 0xffff);
    this.w16(a6, 1);
    this.w16(a6 + 0x26, 0xb4);
    this.w16(a6 + 0x28, 0x23);
    this.w16(a6 + 0x2a, 0);
    this.w16(a6 + 0x34, noise);
    this.w8(a6 + 0x3e, 0xff);
    this.w8(a6 + 0x46, 0xff);
  }

  /** $207B8 (Manta) / $2082E (Walrus) */
  private engineFrame(a1: number, ptr: number, ok: number, recs: number, speedVar: number, manta: boolean): void {
    const a6 = this.r32(ptr);
    if (!this.r16(ok)) {
      this.w16(a6 + 0x36, 0);
      this.w16(a6 + 0x38, 0);
      return;
    }
    const rec = u32(recs + s16(this.r16(a1 + 0x1a) * BAY_SIZE));
    let d0: number;
    if (manta) {
      d0 = w16(this.r16(rec + 6) + 100);
      if (this.r8(rec) & 1) {
        const mode = this.r8(rec + 0x1c);
        if (mode === 1 || mode === 5) d0 = 50;
      }
    } else d0 = this.r16(rec + 4);
    const cur = this.r16(speedVar);
    if (!(cur & 0x8000)) d0 = this.turnTowards(d0, cur, 2);
    this.w16(speedVar, d0);
    this.w16(a6 + 0x2e, manta ? w16(120 - (s16(d0 - 100) >> 3)) : w16(400 - d0));
    this.w16(a6 + 0x36, 6);
    this.w16(a6 + 0x38, 6);
  }

  /** $20B72 */
  private proximityStop(): void {
    this.w16(NEAR_TIMER, 0);
    this.w16(NEAR_DIST, 0);
    if (!(this.r8(NEAR_OBJ) & 0x80)) {
      this.w8(NEAR_OBJ, 0xff);
      this.clear(this.r32(NEAR_CH));
    }
  }

  private static abs32(v: number): number {
    return s32(v < 0 ? -v : v);
  }

  /** $2092A: the sound of the nearest other Manta/Walrus (rescanned every 16 passes). */
  private proximity(): void {
    if (this.r8(OPT_NOISE)) return;
    const a1 = this.r32(CONTROLLED);
    if (a1 === 0) return;
    const t = this.r16(NEAR_TIMER);
    let rescan = true;
    if (t !== 0) {
      this.w16(NEAR_TIMER, t - 1);
      rescan = t - 1 === 0;
    }
    if (rescan) this.proximityScan(a1);
    else this.proximityUpdate(a1);
  }

  /** $20A6C */
  private proximityScan(a1: number): void {
    this.w16(NEAR_TIMER, 16);
    const x = this.r32(a1 + 0xa), y = this.r32(a1 + 0xe), z = this.r32(a1 + 0x12);
    let d6 = 0xffffffff, a0 = 0, d5 = -1;
    for (let k = 0, a2 = VEHICLE_OBJECTS; k < 8; k++, a2 += OBJECT_SIZE) {
      if (a2 === a1 || !(this.r8(a2 + 1) & 1)) continue;
      let d3 = SoundEngine.abs32(s32(this.r32(a2 + 0xa) - x));
      let d4 = SoundEngine.abs32(s32(this.r32(a2 + 0xe) - y));
      d5 = SoundEngine.abs32(s32(this.r32(a2 + 0x12) - z));
      if (d4 < d5) d4 = d5;
      if (d3 < d4) d3 = d4;
      if (!(u32(d3) > d6)) {
        d6 = u32(d3);
        a0 = a2;
      }
    }
    if (d5 < 0) {
      this.proximityStop();
      return;
    }
    const cur = this.r32(NEAR_OBJ);
    if (!(cur & 0x80000000) && a0 === cur) {
      this.proximityUpdate(a1);
      return;
    }
    this.proximityStop();
    this.w16(NEAR_TIMER, 16);
    this.w32(NEAR_OBJ, a0);
    const type = this.r16(a0 + 2);
    if (type !== 5 && type !== 6) {
      this.proximityStop();
      return;
    }
    // $201C0 leaves A1 pointing into its channel list, and the update that follows reads the
    // "controlled object" position from there (so it normally finds no valid distance)
    const { a6, a1: list } = this.findFree();
    this.w32(NEAR_CH, a6);
    this.w16(a6, 1);
    this.w16(a6 + 0x26, 0x2d);
    this.w16(a6 + 0x28, 0x1e);
    this.w16(a6 + 0x2a, 0);
    this.w16(a6 + 0x34, 0x1a);
    this.w8(a6 + 0x2e, 0xff);
    this.w8(a6 + 0x3e, 0xff);
    this.w8(a6 + 0x46, 0xff);
    this.w16(NEAR_KIND, type === 5 ? 0 : 1);
    this.proximityUpdate(list);
  }

  /** $2094A: pitch from the closing speed (a Doppler-like bend), volume from the distance. */
  private proximityUpdate(a1: number): void {
    const a0 = this.r32(NEAR_OBJ);
    if (a0 & 0x80000000) return;
    const a6 = this.r32(NEAR_CH);
    let d0 = SoundEngine.abs32(s32(this.r32(a1 + 0xa) - this.r32(a0 + 0xa)));
    let d1 = SoundEngine.abs32(s32(this.r32(a1 + 0xe) - this.r32(a0 + 0xe)));
    const d2 = SoundEngine.abs32(s32(this.r32(a1 + 0x12) - this.r32(a0 + 0x12)));
    if (d1 < d2) d1 = d2;
    if (d0 < d1) d0 = d1;
    let d4 = 0;
    if (d0 === s16(d0)) {
      const kind = this.r16(NEAR_KIND);
      if (kind !== 0 && kind !== 1) return;
      let vol = s16(6 - (w16(d0) >> 11));
      if (vol < 0) vol = 0;
      let d3 = this.r16(NEAR_DIST);
      if (d3 === 0) d3 = w16(d0);
      this.w16(NEAR_DIST, d0);
      d4 = vol;
      let p = w16(d0 - d3);
      const mag = s16(p) < 0 ? s16(-p) : s16(p); // neg.w: $8000 stays negative
      let lo: number, hi: number;
      if (kind === 0) {
        if (!(mag > 0x20)) p = 0;
        p = w16(p + 0x50);
        lo = 0x2d;
        hi = 0x96;
      } else {
        if (!(mag > 0x20)) p = w16(s16(p) >> 4);
        p = w16(p + 0x190);
        lo = 0x12c;
        hi = 0x1f4;
      }
      if (s16(p) > hi) p = hi;
      else if (s16(p) < lo) p = lo;
      const cur = this.r16(a6 + 0x2e);
      if (!(cur & 0x8000)) p = this.turnTowards(p, cur, 1);
      this.w16(a6 + 0x2e, p);
    }
    this.w16(a6 + 0x36, d4);
    this.w16(a6 + 0x38, d4);
  }
}
