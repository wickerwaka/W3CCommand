#!/usr/bin/env python3
"""Check the simulation model (tools/stsim.py) against the original game.

Inputs are RAM captures from tools/capture_sim.py (emu/out/sim/): the warm-up, single
island steps (world_sim_step) and single logistics ticks ($F7A0). Checks:
  hatari   from each *_in capture, run the model (601 warm-up steps, one step or one tick)
           and compare the simulation state with the matching *_out capture from Hatari.
  oracle   the same, but the reference is the game's own routine run under Unicorn on the
           *_in capture. This isolates model errors from emulator differences.
  long     from each capture, run model and Unicorn side by side for --steps seconds of
           game time (an island step then a logistics tick each), comparing after every
           second; stops at the first divergence and reports what differs.
  enemy    --enemy short runs from perturbed copies that start the enemy mid-assault, mid-trip
           or next to a player unit.
  fuzz     long runs (half length) from --fuzz randomly perturbed copies of each capture:
           ownership flips, damage, random stocks, builds and builders, production targets
           and priorities, shipments and production lines in flight.
Compared: every region in stsim.STATE_REGIONS, byte for byte.

  uv run python tools/check_sim.py [--dir emu/out/sim] [--steps 1280] [--fuzz 6]
"""
import argparse
import contextlib
import io
import random
import sys
from pathlib import Path

from unicorn import UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, Uc, UcError
from unicorn.m68k_const import UC_CPU_M68K_M68000, UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_SR

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stsim
from stgfx import ROOT

SIM_STEP, WARMUP, TICK, RELAX, ENEMY_AI = 0x1848E, 0x18474, 0xF7A0, 0x2173A, 0x1AFAA
FPS = 8                 # game_main passes per second of game time (measured in the 3D view)
STACK, RETURN = 0x7F000, 0x7FF00
UNIT_ROUTINES = (0x1B8AE, 0x1761C, 0x176AC)
RECORD = {"islands": stsim.ISLAND_SIZE, "economy": stsim.ECONOMY_SIZE, "routes": 128,
          "network": stsim.NETWORK_SIZE, "goods pool": 6, "carrier pool": 6, "raw pool": 6,
          "quota table": 10, "quota work": 10, "carrier systems": 8, "store items": stsim.ITEM_SIZE}


class Oracle:
    """The game's own code under Unicorn, on a full RAM image."""

    def __init__(self, ram: bytes, stub_units: bool = False):
        self.uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_M68K_M68000)
        self.uc.mem_map(0, 0x100000)
        self.uc.mem_map(0xFF0000, 0x10000)          # I/O, 24-bit absolute
        self.uc.mem_map(0xFFFF0000, 0x10000)        # I/O, sign-extended .w absolute
        self.uc.mem_write(0, bytes(ram))
        if stub_units:
            # The unit-layer routines the tick calls but the model leaves out. Long and fuzzed
            # runs start from states where their object data isn't valid.
            for addr in UNIT_ROUTINES:
                self.uc.mem_write(addr, b"\x4e\x75")              # rts

    def call(self, addr: int) -> None:
        self.uc.reg_write(UC_M68K_REG_SR, 0x2700)
        self.uc.reg_write(UC_M68K_REG_A7, STACK - 4)
        self.uc.mem_write(STACK - 4, RETURN.to_bytes(4, "big"))
        try:
            self.uc.emu_start(addr, RETURN, count=20_000_000)
        except UcError as e:
            raise RuntimeError(f"${addr:05X}: {e} at PC=${self.uc.reg_read(UC_M68K_REG_PC):05X}") from e
        if self.uc.reg_read(UC_M68K_REG_PC) != RETURN:
            raise RuntimeError(f"${addr:05X} did not return")

    def ram(self) -> bytearray:
        return bytearray(self.uc.mem_read(0, stsim.RAM_SIZE))


def model_ram(ram: bytes, run) -> tuple:
    state = stsim.WorldState.from_ram(ram)
    run(state)
    out = bytearray(ram)
    state.to_ram(out)
    return out, state


def diff(a: bytes, b: bytes, limit: int = 6) -> list:
    lines = []
    for name, start, length in stsim.STATE_REGIONS:
        if a[start:start + length] == b[start:start + length]:
            continue
        rec = RECORD.get(name, 16)
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
    for kind, routine, run in (("step", SIM_STEP, stsim.WorldState.step),
                               ("tick", TICK, stsim.WorldState.logistics_tick),
                               ("ai", ENEMY_AI, stsim.WorldState.enemy_ai)):
        for p in sorted(d.glob(f"{kind}_*_in.bin"), key=lambda p: [int(x) for x in p.name.split("_")[1:-1]]):
            out.append((p.name[:-7], p, p.with_name(p.name[:-7] + "_out.bin"), routine, run))
    return out


def oracle_frame(oracle: Oracle, new_second: bool) -> None:
    """game_main's strategic calls ($40D8-$4122), in order."""
    if oracle.uc.mem_read(stsim.ROUTES_PENDING, 1)[0]:
        oracle.call(RELAX)
    if new_second:
        oracle.uc.mem_write(0x8FD, b"\xff")
        oracle.call(TICK)
    oracle.call(SIM_STEP)
    oracle.call(ENEMY_AI)


def long_run(name: str, ram: bytes, seconds: int, fps: int = FPS) -> bool:
    """Model and original side by side, frame by frame, compared after every frame. Stops
    early (not a failure) when the enemy enters close-range combat or is destroyed: what
    follows belongs to the unit layer."""
    oracle = Oracle(ram, stub_units=True)
    state = stsim.WorldState.from_ram(ram)
    events = []
    for n in range(seconds * fps):
        new_second = n % fps == 0
        stop = None
        try:
            state.frame(new_second)
        except (stsim.TacticalMode, stsim.EnemyDestroyed) as e:
            stop = type(e).__name__
        oracle_frame(oracle, new_second)
        ref = oracle.ram()
        mine = bytearray(ref)
        state.to_ram(mine)
        lines = diff(mine, ref) if stop != "EnemyDestroyed" else []
        if lines:
            return report(f"long {name}: frame {n} ({n // fps} s, island {state.island_index}, "
                          f"enemy state {state.mem.u16(stsim.E_STATE)})", lines)
        events += [(n // fps, *e) for e in state.events]
        state.events.clear()
        if stop:
            return report(f"long {name}: {n // fps} s, stopped at {stop}{summary(events)}", [])
    return report(f"long {name}: {seconds} s{summary(events)}", [])


def summary(events: list) -> str:
    kinds = {}
    for e in events:
        kinds[e[1]] = kinds.get(e[1], 0) + 1
    return f" ({', '.join(f'{v} {k}' for k, v in sorted(kinds.items()))})" if kinds else ""


def perturb(ram: bytes, seed: int) -> bytes:
    """A randomised but well-formed variant of a capture, to reach the paths a quiet game
    rarely takes."""
    rnd = random.Random(seed)
    m = stsim.Mem(bytearray(ram))
    for i in range(stsim.ISLANDS):
        if rnd.random() < 0.3:
            m.set8(stsim.OWNERS + i, rnd.choice((0, 1, 2)))
        e = stsim.Economy(m, i)
        if rnd.random() < 0.3:
            e.kind = rnd.randrange(4)
        e.raw_stock = rnd.randrange(e.raw_cap + 1)
        e.goods = rnd.randrange(e.goods_cap + 1)
        for k in range(min(e.lines, 3)):
            ln = e.line(k)
            r = rnd.random()
            if r < 0.3:
                m.set16(ln, 0xFFFF)
            elif r < 0.6:
                m.set16(ln, rnd.randrange(3))
                p = rnd.choice(list(range(18)) + [18])
                m.set16(ln + 2, p)
                m.set16(ln + 4, rnd.randrange(1, 20))
                m.set16(ln + 6, rnd.randrange(64) if p == 18 else 0xFFFF)
                m.set16(ln + 8, rnd.randrange(1, 10))
                m.set16(ln + 10, rnd.choice((25, 50, 100)))
        for j, s in enumerate(stsim.Island(m, i).slots):
            t, r = s.type, rnd.random()
            if stsim.T_BUILDING_FIRST <= t <= stsim.T_BUILDING_LAST and r < 0.3:
                s.value = rnd.randrange(-500, 6000)                      # damage
            elif stsim.T_BUILDING_FIRST <= t <= stsim.T_BUILDING_LAST and r < 0.4:
                s.type = t + stsim.UNDER_CONSTRUCTION                    # reverted
                s.value = rnd.randrange(0, 60)
            elif t == 0 and r < 0.1:
                s.type = rnd.choice((0x25, 0x26, 0x27, 0x28, 0x2A, 0x2C, 0x2F, 0x30, 0x33, 0x39))
                s.value = rnd.randrange(0, 60)
                s.x, s.z = rnd.randrange(-3000, 3000), rnd.randrange(-3000, 3000)
            elif t == 0 and r < 0.13:
                s.type = stsim.T_COMMAND_CENTRE_CONSTRUCTION            # builder
                s.value = rnd.randrange(0, 61)
                s.word6 = rnd.choice((0x01, 0x02, 0x03, 0x11, 0x12, 0x13))
    for k in range(stsim.N_ITEMS):                                       # player's targets
        item = stsim.ITEMS + stsim.ITEM_SIZE * k
        m.set16(item + 0x10, rnd.randrange(3))
        m.set16(item + 0x12, rnd.choice((0, 0, 5, 20, 200)))
        m.set16(stsim.STORES + 2 * k, rnd.randrange(30))
    for j in range(3):
        m.set16(stsim.FUEL_CLASS + 2 * j, rnd.randrange(3))
        m.set32(stsim.FUEL + 4 * j, rnd.randrange(stsim.FUEL_MAX + 1))
    m.set16(stsim.STOCKPILE, rnd.choice((0, 0, 0, rnd.randrange(64))))
    m.set8(stsim.IN_TRANSIT, rnd.choice((0, 0xFF)))
    m.set16(0x718, rnd.choice((0, 1)))                                   # repair screen open
    player = [i for i in range(64) if m.u8(stsim.OWNERS + i) == 1] or [0]
    full = rnd.random() < 0.3                                           # pools filled up
    for k in range(stsim.RAW_POOL_N):                                    # shipments in flight
        if rnd.random() < 0.2 or full:
            e = stsim.RAW_POOL + 6 * k
            m.set8(e, rnd.choice(player) if full else rnd.randrange(64))
            m.set8(e + 1, rnd.randrange(1, 60 if full else 6))
            m.set16(e + 2, 200)
            m.set16(e + 4, rnd.randrange(64))
    for k in range(stsim.GOODS_POOL_N):
        if rnd.random() < 0.15 or (full and k < stsim.FREE_GOODS_SEARCH):
            e = stsim.GOODS_POOL + 6 * k
            kind = rnd.randrange(19)
            m.set8(e, rnd.choice(player) if full else rnd.randrange(64))
            m.set8(e + 1, rnd.randrange(1, 60 if full else 6))
            m.set8(e + 2, kind)
            m.set8(e + 3, rnd.randrange(1, 255))
            m.set16(e + 4, rnd.randrange(64) if kind == 18 or rnd.random() < 0.3 else 0xFFFF)
    for k in range(stsim.N_SYSTEMS):                                     # carrier damage
        a = stsim.SYSTEMS + 8 * k
        m.set16(a, rnd.randrange(m.u16(a + 2) + 1))
        m.set16(a + 4, rnd.randrange(3))
    m.set16(stsim.REPAIR_RATE, rnd.randrange(64, 1024))
    # The enemy, part way through something: assaulting a player island, travelling,
    # waiting or refitting, with random strength, aircraft, reserve and bombard timer.
    player = [i for i in range(64) if m.u8(stsim.OWNERS + i) == 1 and i != stsim.VULCAN] or [1]
    state = rnd.choice((1, 2, 3, 3, 3, 4, 5))
    at = rnd.choice(player) if state == 3 else rnd.randrange(1, 64)
    last_stand = state == 3 and rnd.random() < 0.2                     # weak enemy at NEMESIS
    if last_stand:
        at = stsim.NEMESIS
        m.set8(stsim.OWNERS + at, 1)
    isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * at
    m.set16(stsim.E_STATE, state)
    m.set16(stsim.E_ISLAND, at)
    m.set16(stsim.E_TARGET, rnd.randrange(1, 64))
    m.set16(stsim.E_TIMER, rnd.randrange(1, 40))
    m.set16(stsim.E_STRENGTH, rnd.randrange(100, 1024) if last_stand else
            rnd.randrange(500, 12001) if rnd.random() < 0.3 else rnd.randrange(3000, 12001))
    m.set16(stsim.E_AIRCRAFT, rnd.randrange(9))
    m.set16(stsim.E_RESERVE, rnd.randrange(13))
    m.set16(stsim.B_POOL, rnd.randrange(0, 1500) if state == 3 else rnd.randrange(0, 5001))
    m.set16(stsim.B_TIMER, rnd.randrange(1, 30))
    m.set32(stsim.ENEMY_CARRIER + 0x0A, m.u32(isl + 2))
    m.set32(stsim.ENEMY_CARRIER + 0x12, m.u32(isl + 6))
    r = rnd.random()
    if r < 0.1:
        m.set16(stsim.E_STATE, 0)
    elif r < 0.25 and state in (2, 3, 4):                            # a player unit is near
        unit = stsim.OBJECTS + stsim.OBJECT_SIZE * 2
        m.buf[unit + 1] |= 1
        m.set32(stsim.ENEMY_CARRIER + 0x0A, m.u32(unit + 0x0A) + 100000)
        m.set32(stsim.ENEMY_CARRIER + 0x12, m.u32(unit + 0x12) - 100000)
    if state == 3:
        if rnd.random() < 0.3:
            m.set16(stsim.STOCKPILE, at)
        extra = rnd.randrange(3, 7)
        for s in stsim.Island(m, at).slots[1:]:                     # extra defences
            if s.type == 0 and extra:
                extra -= 1
                s.type, s.value = rnd.choice((0x25, 0x25, 0x26, 0x27, 0x28)), 4000
                s.x, s.z = rnd.randrange(-3000, 3000), rnd.randrange(-3000, 3000)
    return bytes(m.buf)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=ROOT / "emu" / "out" / "sim")
    ap.add_argument("--steps", type=int, default=1280, help="seconds of game time per long run")
    ap.add_argument("--fuzz", type=int, default=6, help="perturbed variants per capture")
    ap.add_argument("--enemy", type=int, default=40, help="short perturbed runs aimed at the enemy AI")
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
        if name.startswith(("tick", "ai")):
            continue                     # long runs from the step captures cover ticks
        ok &= long_run(name, fout.read_bytes(), args.steps)
        for seed in range(args.fuzz):
            ok &= long_run(f"{name} fuzz {seed}", perturb(fout.read_bytes(), seed), args.steps // 2)
    if caps and args.enemy:
        base = [fout for name, _, fout, _, _ in caps if name.startswith("step")][-1].read_bytes()
        stops = 0
        for seed in range(100, 100 + args.enemy):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                good = long_run(f"enemy {seed}", perturb(base, seed), 240)
            ok &= good
            stops += "stopped" in out.getvalue()
            if not good:
                print(out.getvalue(), end="")
        print(f"enemy scenarios: {args.enemy} runs of 240 s, {stops} reached close-range combat or "
              f"destruction{'' if ok else ' (with mismatches above)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
