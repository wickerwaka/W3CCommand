// Check the TypeScript sound engine (src/audio/engine.ts) against the original under Unicorn,
// and the YM2149 synthesiser (src/audio/ym2149.ts) against tools/ym2149.py and the chip's
// documented behaviour.
//
// Engine: replays extracted/sound_vectors.json.gz (uv run python tools/export_sound_vectors.py):
// for every event the PSG writes (time in MFP cycles, register, value) must be identical, and
// so must the Timer A state and the CRC-32 of the engine state regions.
//
//   npm run check-sound [-- --vectors FILE] [--max-fail 10]
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { crc32, gunzipSync } from "node:zlib";
import { MFP_CLOCK, STATE_REGIONS, SoundEngine } from "../src/audio/engine.ts";
import { CHIP_STEP_HZ, YM2149, levelTable } from "../src/audio/ym2149.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const args = process.argv.slice(2);
const arg = (k: string) => {
  const i = args.indexOf(k);
  return i >= 0 ? args[i + 1] : undefined;
};
const VECTORS = resolve(arg("--vectors") ?? join(ROOT, "extracted", "sound_vectors.json.gz"));
const MAX_FAIL = Number(arg("--max-fail") ?? 10);

interface Event { op: string; a: (number | string)[]; w: number[]; t: number[]; c: number }
interface Scenario { name: string; events: Event[]; state: string }
interface SynthRec { log: number[]; length: number; rms: number[] }
const data = JSON.parse(gunzipSync(readFileSync(VECTORS)).toString("utf8")) as {
  scenarios: Scenario[];
  synth: { records: Record<string, SynthRec>; lfsr: string };
};

const payload = readFileSync(join(ROOT, "extracted", "carrier_payload.bin"));
const image = new Uint8Array(0x80000);
image.set(payload.subarray(0, 0x80000 - 0x400), 0x400);

function stateBytes(e: SoundEngine): Uint8Array {
  const parts = STATE_REGIONS.map(([, a, n]) => e.mem.subarray(a, a + n));
  const out = new Uint8Array(parts.reduce((s, p) => s + p.length, 0));
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}
const hex = (b: Uint8Array) => Buffer.from(b).toString("hex");

let failures = 0, events = 0, writes = 0;
function fail(msg: string): void {
  failures++;
  if (failures <= MAX_FAIL) console.log("MISMATCH " + msg);
}

// ---- engine ---------------------------------------------------------------------------------
for (const sc of data.scenarios) {
  const e = new SoundEngine(image);
  let bad = false;
  for (const [i, ev] of sc.events.entries()) {
    const a = ev.a as number[];
    switch (ev.op) {
      case "init": e.init(); break;
      case "poke": e.mem.set(Buffer.from(ev.a[1] as string, "hex"), a[0]); break;
      case "frame": e.frame(); break;
      case "play": e.play(a[0]); break;
      case "start": e.start(a[0]); break;
      case "explosion": e.explosionObject(a[0], a[1]); break;
      case "explosionWord": e.explosionWord(a[0], a[1]); break;
      case "pass": e.vehiclePass(); break;
      case "reset": e.vehicleReset(); break;
      case "off": e.off(); break;
      case "on": e.on(); break;
      default: throw new Error(`unknown op ${ev.op}`);
    }
    const w = e.take();
    events++;
    writes += w.length / 3;
    const t = e.timer;
    const timer = [t.tacr, t.tdr, t.counter, t.phase, t.busyUntil, t.pending];
    const where = `${sc.name} event ${i} (${ev.op} ${JSON.stringify(ev.a)})`;
    if (w.length !== ev.w.length || w.some((v, k) => v !== ev.w[k])) {
      let k = 0;
      while (k < w.length && w[k] === ev.w[k]) k++;
      k -= k % 3;
      fail(`${where}: PSG writes differ at write ${k / 3}: ts ${JSON.stringify(w.slice(k, k + 9))} ` +
        `original ${JSON.stringify(ev.w.slice(k, k + 9))} (${w.length / 3} vs ${ev.w.length / 3} writes)`);
      bad = true;
    } else if (timer.some((v, k) => v !== ev.t[k])) {
      fail(`${where}: timer A ${JSON.stringify(timer)} vs original ${JSON.stringify(ev.t)}`);
      bad = true;
    } else if (crc32(stateBytes(e)) !== ev.c) {
      fail(`${where}: engine state CRC differs`);
      bad = true;
    }
    if (bad) break;
  }
  if (!bad && hex(stateBytes(e)) !== sc.state) fail(`${sc.name}: final state differs`);
}
console.log(`engine: ${data.scenarios.length} scenarios, ${events} events, ${writes} PSG writes, ` +
  `${failures ? failures + " MISMATCH" : "all match"}`);

// ---- synthesiser ----------------------------------------------------------------------------
let synthFail = 0;
function check(ok: boolean, msg: string): void {
  console.log(`${ok ? "ok  " : "FAIL"} ${msg}`);
  if (!ok) synthFail++;
}

// 1. noise LFSR: the same 17-bit sequence (taps 0 and 3) as tools/ym2149.py / MAME
{
  const ym = new YM2149();
  let bits = "";
  for (let i = 0; i < data.synth.lfsr.length; i++) bits += ym.stepNoiseLfsr();
  check(bits === data.synth.lfsr, "noise LFSR matches tools/ym2149.py (4000 bits)");
}

// 2. tone frequency = 2 MHz / (16 * TP), from output transitions
{
  const ym = new YM2149();
  const rate = 250000;
  let okAll = true;
  for (const tp of [1, 30, 284, 1000, 4095]) {
    ym.write(7, 0x3e); // tone A only
    ym.write(8, 15);
    ym.write(0, tp & 0xff);
    ym.write(1, tp >> 8);
    const n = rate * 2;
    let last = ym.levelOut(), edges = 0;
    for (let i = 0; i < n; i++) {
      ym.step();
      const v = ym.levelOut();
      if (v > last) edges++;
      last = v;
    }
    const f = edges / 2, want = 2e6 / (16 * tp);
    if (Math.abs(f - want) > Math.max(1, want * 0.002)) okAll = false;
  }
  check(okAll, "tone frequency 2 MHz / (16 TP) for TP 1..4095");
}

// 3. envelope shapes: the 16 shapes over three ramps against the datasheet drawings
{
  const SHAPES: Record<number, string> = {
    0: "\\_", 4: "/_", 8: "\\\\\\", 9: "\\_", 10: "\\/\\", 11: "\\~", 12: "///", 13: "/~", 14: "/\\/", 15: "/_",
  };
  let okAll = true;
  for (let shape = 0; shape < 16; shape++) {
    const want = SHAPES[shape] ?? (shape < 4 ? "\\_" : "/_");
    const ym = new YM2149();
    ym.write(11, 1);
    ym.write(12, 0);
    ym.write(13, shape);
    const seq: number[] = [];
    for (let i = 0; i < 32 * 3; i++) {
      seq.push(ym.envLevel());
      ym.step();
    }
    const ramp = (k: number) => seq.slice(32 * k, 32 * k + 32);
    const kind = (r: number[]) =>
      r.every((v, i) => v === 31 - i) ? "\\" : r.every((v, i) => v === i) ? "/" : r.every((v) => v === 0) ? "_" :
      r.every((v) => v === 31) ? "~" : "?";
    let got = kind(ramp(0)) + kind(ramp(1));
    if (want.length === 3) got += kind(ramp(2));
    if (got !== want) {
      okAll = false;
      console.log(`  shape ${shape}: ${got} want ${want}`);
    }
  }
  check(okAll, "envelope shapes 0-15 (32 steps per ramp, hold/alternate)");
}

// 4. DAC: 32 levels, 1.5 dB apart (fixed volume v uses level 2v+1)
{
  const t = levelTable();
  let ok = t[0] === 0 && Math.abs(t[31] - 1) < 1e-9;
  for (let i = 2; i < 32; i++) ok &&= Math.abs(20 * Math.log10(t[i] / t[i - 1]) - 1.5) < 1e-6;
  check(ok, "DAC levels: 1.5 dB per step, 31 = full scale");
}

// 5. effects rendered from the same register log: per-frame RMS against tools/ym2149.py
{
  const rate = 44100;
  for (const [rec, r] of Object.entries(data.synth.records)) {
    const ym = new YM2149();
    const n = Math.floor(r.length * rate);
    const out = new Float32Array(n);
    const stepsPerSample = CHIP_STEP_HZ / rate;
    let li = 0, step = 0;
    const log = r.log;
    for (let i = 0; i < n; i++) {
      const target = (i + 1) * stepsPerSample;
      let acc = 0, cnt = 0;
      while (step < target) {
        const tCycles = (step * MFP_CLOCK) / CHIP_STEP_HZ;
        while (li < log.length && log[li] <= tCycles) {
          ym.write(log[li + 1], log[li + 2]);
          li += 3;
        }
        ym.step();
        acc += ym.output();
        cnt++;
        step++;
      }
      out[i] = acc / cnt;
    }
    let mean = 0;
    for (const v of out) mean += v;
    mean /= n;
    const block = rate / 50;
    const rms: number[] = [];
    for (let i = 0; i + block <= n; i += block) {
      let s = 0;
      for (let k = i; k < i + block; k++) s += (out[k] - mean) ** 2;
      rms.push(Math.sqrt(s / block));
    }
    const m = Math.min(rms.length, r.rms.length);
    const a = rms.slice(0, m), b = r.rms.slice(0, m);
    const ma = a.reduce((s, v) => s + v, 0) / m, mb = b.reduce((s, v) => s + v, 0) / m;
    let sab = 0, saa = 0, sbb = 0;
    for (let i = 0; i < m; i++) {
      sab += (a[i] - ma) * (b[i] - mb);
      saa += (a[i] - ma) ** 2;
      sbb += (b[i] - mb) ** 2;
    }
    const ratio = ma / mb;
    if (Math.sqrt(sbb / m) / mb > 0.1) {
      const corr = sab / Math.sqrt(saa * sbb);
      check(corr > 0.9 && ratio > 0.7 && ratio < 1.4,
        `record $${rec}: frame RMS vs tools/ym2149.py, correlation ${corr.toFixed(3)}, level ratio ${ratio.toFixed(2)}`);
    } else {
      // a steady level (only the pitch changes): compare frame by frame
      const worst = Math.max(...a.map((v, i) => Math.abs(v - b[i]) / mb));
      check(worst < 0.3 && ratio > 0.7 && ratio < 1.4,
        `record $${rec}: steady frame RMS vs tools/ym2149.py, worst frame ${(100 * worst).toFixed(0)}%, level ratio ${ratio.toFixed(2)}`);
    }
  }
}

console.log(failures || synthFail ? `FAILED: ${failures} engine mismatches, ${synthFail} synth checks` : "all checks passed");
process.exit(failures || synthFail ? 1 : 0);
