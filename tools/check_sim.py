#!/usr/bin/env python3
"""Check the island simulation model (tools/stsim.py) against the original game.

Inputs are RAM captures from tools/capture_sim.py (emu/out/sim/). Three checks:
  hatari   from each *_in capture, step the model (601 warm-up steps, or one step) and
           compare the simulation state with the matching *_out capture from Hatari.
  oracle   the same, but the reference is the game's own world_sim_step run under Unicorn
           on the *_in capture. This isolates model errors from emulator differences.
  long     from each capture, run model and Unicorn side by side for --steps steps
           (default 1280, 20 passes over all islands), comparing after every step;
           stops at the first divergence and reports the step and the differing fields.
  fuzz     long runs (half length) from --fuzz randomly perturbed copies of each capture:
           ownership flips, damage, random stocks, reverted and abandoned builds, builders.
Compared: island table, economy, owners, slot order, quota tables, network
links, route matrix, RNG and the step globals ($8D0-$8DC).

  uv run python tools/check_sim.py [--dir emu/out/sim] [--steps 1280] [--fuzz 3]
"""
import argparse
import random
import sys
from pathlib import Path

from unicorn import UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, Uc, UcError
from unicorn.m68k_const import UC_CPU_M68K_M68000, UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_SR

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stsim
from stgfx import ROOT

RAM_SIZE = 0x80000
SIM_STEP, WARMUP = 0x1848E, 0x18474
STACK, RETURN = 0x7F000, 0x7FF00

REGIONS = [  # (name, start, length, record size)
    ("globals $8D0", stsim.G_ISLAND, 0xE, 2),
    ("rng", stsim.RNG, 8, 4),
    ("owners", stsim.OWNERS, 64, 1),
    ("islands", stsim.ISLAND_TABLE, 64 * stsim.ISLAND_SIZE, stsim.ISLAND_SIZE),
    ("economy", stsim.ECONOMY, 64 * stsim.ECONOMY_SIZE, stsim.ECONOMY_SIZE),
    ("slot order", stsim.SLOT_ORDER, 128, 4),
    ("quota rom", stsim.QUOTA_ROM, 640, 10),
    ("quota work", stsim.QUOTA_WORK, 640, 10),
    ("network", stsim.NETWORK, 64 * stsim.NETWORK_SIZE, stsim.NETWORK_SIZE),
    ("routes", stsim.ROUTES, 64 * 128, 128),
    ("route flags $1234", 0x1234, 2, 1),
]


class Oracle:
    """The game's own code under Unicorn, on a full RAM image."""

    def __init__(self, ram: bytes):
        self.uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_M68K_M68000)
        self.uc.mem_map(0, 0x100000)
        self.uc.mem_map(0xFF0000, 0x10000)          # I/O, 24-bit absolute
        self.uc.mem_map(0xFFFF0000, 0x10000)        # I/O, sign-extended .w absolute
        self.uc.mem_write(0, bytes(ram))

    def call(self, addr: int) -> None:
        self.uc.reg_write(UC_M68K_REG_SR, 0x2700)
        self.uc.reg_write(UC_M68K_REG_A7, STACK - 4)
        self.uc.mem_write(STACK - 4, RETURN.to_bytes(4, "big"))
        try:
            self.uc.emu_start(addr, RETURN, count=50_000_000)
        except UcError as e:
            raise RuntimeError(f"${addr:05X}: {e} at PC=${self.uc.reg_read(UC_M68K_REG_PC):05X}") from e

    def ram(self) -> bytearray:
        return bytearray(self.uc.mem_read(0, RAM_SIZE))


def model_ram(ram: bytes, run) -> tuple:
    state = stsim.WorldState.from_ram(ram)
    run(state)
    out = bytearray(ram)
    state.to_ram(out)
    return out, state


def diff(a: bytes, b: bytes, limit: int = 8) -> list:
    lines = []
    for name, start, length, rec in REGIONS:
        if a[start:start + length] == b[start:start + length]:
            continue
        bad = [o for o in range(length) if a[start + o] != b[start + o]]
        recs = sorted({o // rec for o in bad})
        lines.append(f"{name}: {len(bad)} bytes in {len(recs)} records")
        for r in recs[:limit]:
            o = start + r * rec
            lines.append(f"    #{r} ${o:05X} offsets {[f'+{x - r * rec:X}' for x in bad if x // rec == r][:12]}")
            if rec <= 64:
                lines.append(f"      want {bytes(b[o:o + rec]).hex(' ')}")
                lines.append(f"      got  {bytes(a[o:o + rec]).hex(' ')}")
    return lines


def report(label: str, lines: list) -> bool:
    print(f"{label}: {'OK' if not lines else 'MISMATCH'}")
    for line in lines:
        print("  " + line)
    return not lines


def captures(d: Path) -> list:
    """(name, in_ram, out_ram, routine, model runner)."""
    out = []
    if (d / "warmup_in.bin").exists():
        out.append(("warmup", d / "warmup_in.bin", d / "warmup_out.bin", WARMUP, stsim.WorldState.warmup_run))
    for p in sorted(d.glob("step_*_in.bin"), key=lambda p: int(p.name.split("_")[1])):
        out.append((p.name[:-7], p, p.with_name(p.name.replace("_in", "_out")), SIM_STEP, stsim.WorldState.step))
    return out


def long_run(name: str, ram: bytes, steps: int) -> bool:
    oracle = Oracle(ram)
    state = stsim.WorldState.from_ram(ram)
    for n in range(1, steps + 1):
        oracle.call(SIM_STEP)
        state.step()
        ref = oracle.ram()
        mine = bytearray(ref)
        # Start from the reference image so only the modelled regions can differ.
        state.to_ram(mine)
        state.network_invalidated = False
        lines = diff(mine, ref)
        if lines:
            return report(f"long {name}: step {n} (island {state.island_index})", lines)
    return report(f"long {name}: {steps} steps", [])


def perturb(ram: bytes, seed: int) -> bytes:
    """A randomised but well-formed variant of a capture, to reach the paths a quiet game
    rarely takes: ownership flips, damage, low stocks, over-quota and abandoned builds."""
    rnd = random.Random(seed)
    m = bytearray(ram)
    for i in range(stsim.ISLANDS):
        if rnd.random() < 0.3:
            m[stsim.OWNERS + i] = rnd.choice((0, 1, 2))
        e = stsim.ECONOMY + stsim.ECONOMY_SIZE * i
        if rnd.random() < 0.3:
            m[e] = rnd.randrange(4)
        for off, cap in ((2, 4), (8, 0xA)):
            stsim.wr16(m, e + off, rnd.randrange(max(1, stsim.rd16(m, e + cap)) + 1))
        isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i
        for j in range(stsim.SLOTS):
            a = isl + stsim.SLOT_BASE + stsim.SLOT_SIZE * j
            t = stsim.rd16(m, a)
            r = rnd.random()
            if stsim.T_BUILDING_FIRST <= t <= stsim.T_BUILDING_LAST and r < 0.3:
                stsim.wr16(m, a + 4, rnd.randrange(-500, 6000))                 # damage
            elif stsim.T_BUILDING_FIRST <= t <= stsim.T_BUILDING_LAST and r < 0.4:
                stsim.wr16(m, a, t + stsim.UNDER_CONSTRUCTION)                  # reverted
                stsim.wr16(m, a + 4, rnd.randrange(0, 60))
            elif t == 0 and r < 0.1:
                stsim.wr16(m, a, rnd.choice((0x25, 0x26, 0x27, 0x28, 0x2A, 0x2C, 0x2F, 0x30, 0x33, 0x39)))
                stsim.wr16(m, a + 4, rnd.randrange(0, 60))
                stsim.wr16(m, a + 8, rnd.randrange(-3000, 3000))
                stsim.wr16(m, a + 10, rnd.randrange(-3000, 3000))
            elif t == 0 and r < 0.13:
                stsim.wr16(m, a, stsim.T_COMMAND_CENTRE_CONSTRUCTION)            # builder
                stsim.wr16(m, a + 4, rnd.randrange(0, 61))
                stsim.wr16(m, a + 6, rnd.choice((0x01, 0x02, 0x03, 0x11, 0x12, 0x13)))
    return bytes(m)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=ROOT / "emu" / "out" / "sim")
    ap.add_argument("--steps", type=int, default=1280)
    ap.add_argument("--fuzz", type=int, default=3, help="perturbed variants per capture")
    args = ap.parse_args()
    caps = captures(args.dir)
    if not caps:
        sys.exit(f"no captures in {args.dir}; run tools/capture_sim.py first")
    ok = True
    for name, fin, fout, routine, run in caps:
        ram_in, ram_out = fin.read_bytes(), fout.read_bytes()
        mine, state = model_ram(ram_in, run)
        ok &= report(f"hatari {name}", diff(mine, ram_out))
        oracle = Oracle(ram_in)
        oracle.call(routine)
        ok &= report(f"oracle {name}", diff(mine, oracle.ram()))
        if state.events:
            print(f"  events: {state.events}")
    for name, fin, fout, _, _ in caps:
        ok &= long_run(name, fout.read_bytes(), args.steps)
        for seed in range(args.fuzz):
            ok &= long_run(f"{name} fuzz {seed}", perturb(fout.read_bytes(), seed), args.steps // 2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
