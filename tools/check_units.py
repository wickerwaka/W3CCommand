#!/usr/bin/env python3
"""Check the unit-layer model (tools/stunits.py) against the original game.

Inputs are pass captures from an action game:
  uv run python tools/capture_sim.py --action --steps 20 300 1500 --ticks 0 --passes 40 --out emu/out/action
For every routine in ROUTINES and every pass_*_in.bin capture, the original routine runs
under Unicorn and the model runs on the same state; every region of stsim.STATE_REGIONS and
stunits.UNIT_REGIONS must match byte for byte. Routines are checked one at a time from the
same captured states, so a mismatch points at a single routine.

  uv run python tools/check_units.py [--dir emu/out/action] [--routine carrier_step]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_sim
import stsim
import stunits
from stgfx import ROOT

ROUTINES = {          # name: (original address, model method)
    "carrier_step": (0x1E744, stunits.UnitState.carrier_step),
    "walrus_dock_door_step": (0x1ED94, stunits.UnitState.walrus_dock_door_step),
}
RECORD = dict(check_sim.RECORD, **{f"object {k}": 16 for k in range(64)})


def regions():
    return stsim.STATE_REGIONS + stunits.UNIT_REGIONS


def diff(a: bytes, b: bytes, limit: int = 4) -> list:
    lines = []
    for name, start, length in regions():
        if a[start:start + length] == b[start:start + length]:
            continue
        rec = RECORD.get(name, 16)
        bad = [o for o in range(length) if a[start + o] != b[start + o]]
        recs = sorted({o // rec for o in bad})
        lines.append(f"{name}: {len(bad)} bytes in {len(recs)} records")
        for r in recs[:limit]:
            o = start + r * rec
            lines.append(f"    ${o:05X} want {bytes(b[o:o + rec]).hex(' ')}")
            lines.append(f"    ${o:05X} got  {bytes(a[o:o + rec]).hex(' ')}")
    return lines


def perturb_carrier(ram: bytes, rnd) -> bytes:
    """Steering, grounding, recoil, manual control, damage and fuel variants."""
    m = stsim.Mem(bytearray(ram))
    c = stunits.CARRIER
    m.set8(stunits.C_FLAGS, rnd.choice((0, 0x01, 0x03, 0x07, 0x80, 0x83)))
    m.set8(stunits.C_SUBSTATE, rnd.choice((0, 1, 1)))
    m.set16(stunits.C_RECOIL, rnd.randrange(1, 4))
    m.set16(stunits.C_RUDDER, rnd.randrange(-255, 256))
    m.set16(stunits.C_SPEED, rnd.randrange(-40, 140))
    m.set16(stunits.C_TARGET, rnd.randrange(-40, 140))
    m.set16(c + 6, rnd.randrange(0x10000))
    if rnd.random() < 0.5:                                   # waypoint somewhere around
        m.set32(stunits.C_WAYPOINT_X, m.u32(c + 0xA) + rnd.randrange(-40000, 40000))
        m.set32(stunits.C_WAYPOINT_Z, m.u32(c + 0x12) + rnd.randrange(-40000, 40000))
    if rnd.random() < 0.4 and m.u32(c + 0x3C):               # inside the island's shallows
        buf = m.u32(c + 0x3C)
        m.set32(c + 0xA, m.u32(buf + 2) + rnd.randrange(-25000, 25000))
        m.set32(c + 0x12, m.u32(buf + 0xA) + rnd.randrange(-25000, 25000))
    m.set16(stunits.SCREEN, rnd.choice((0, 0, 1)))
    m.set8(stunits.POINTER_MODE, rnd.choice((0, 0xFF)))
    m.set16(stunits.MOUSE_DX, rnd.randrange(-300, 300))
    for k in (stunits.KEY_SPEED_UP, stunits.KEY_SPEED_DOWN):
        m.set8(k, rnd.choice((0, 0, 0xFF)))
    m.set8(stunits.SLOW_TICK, rnd.choice((0, 0xFF)))
    m.set8(stunits.C_NEAR_UNIT, rnd.choice((0, 0, 0xFF)))
    m.set32(stunits.SHIP_FUEL, rnd.choice((0, 10, m.u32(stunits.SHIP_FUEL))))
    m.set16(stunits.ENGINES_STATUS, rnd.choice((0, 100, 256, 400)))
    m.set16(stunits.SUPERSTRUCTURE, rnd.choice((0, 500, 4000)))
    m.set16(stunits.C_DAMAGE_MSG, rnd.choice((0, 0, 3)))
    m.set8(stunits.C_FAST, rnd.choice((0, 0xFF)))
    if rnd.random() < 0.3:                                   # open sea: fast cruise
        m.set32(c + 0x3C, 0)
    return bytes(m.buf)


FUZZ = {"carrier_step": perturb_carrier}


def check_routine(name: str, files: list, fuzz: int = 0) -> bool:
    import random
    addr, method = ROUTINES[name]
    rnd = random.Random(7)
    inputs = [f.read_bytes() for f in files]
    if name in FUZZ:
        inputs += [FUZZ[name](rnd.choice(inputs), rnd) for _ in range(fuzz)]
    for i, ram in enumerate(inputs):
        f = files[i] if i < len(files) else Path(f"fuzz {i - len(files)}")
        oracle = check_sim.Oracle(ram)
        oracle.call(addr)
        state = stunits.UnitState.from_ram(ram)
        method(state)
        mine = bytearray(oracle.ram())
        state.to_ram(mine)
        lines = diff(mine, oracle.ram())
        if lines:
            print(f"{name}: MISMATCH from {f.name}")
            for line in lines:
                print("  " + line)
            return False
    print(f"{name}: OK ({len(files)} captured states, {len(inputs) - len(files)} fuzzed)")
    return True


def call_regs(oracle, addr: int, **regs) -> dict:
    """Call a register-convention routine; returns d0-d3 afterwards (32-bit)."""
    from unicorn.m68k_const import UC_M68K_REG_D0
    for k, v in regs.items():
        oracle.uc.reg_write(UC_M68K_REG_D0 + int(k[1]), v & 0xFFFFFFFF)
    oracle.call(addr)
    return {f"d{i}": oracle.uc.reg_read(UC_M68K_REG_D0 + i) for i in range(4)}


def check_helpers(ram: bytes, n: int = 3000) -> bool:
    """The shared maths against the original routines, on random inputs (including the
    edges: zero, equal magnitudes, word overflow for atan2)."""
    import random
    rnd = random.Random(1)
    oracle = check_sim.Oracle(ram)
    state = stunits.UnitState.from_ram(ram)
    word = lambda: rnd.choice((0, 1, -1, 0x7FFF, -0x8000, rnd.randrange(-0x8000, 0x8000), rnd.randrange(-300, 300)))
    big = lambda: rnd.choice((word(), rnd.randrange(-0x10000000, 0x10000000), rnd.randrange(-200000, 200000)))
    bad = []
    for _ in range(n):
        x, z, a = word(), word(), rnd.randrange(0x10000)
        r = call_regs(oracle, 0xA5E0, d0=x, d1=z, d2=a)
        if state.rotate_yaw(x, z, a) != (stsim.s16(r["d0"]), stsim.s16(r["d1"])):
            bad.append(("rotate_yaw", x, z, a))
        r = call_regs(oracle, 0xA620, d0=x, d1=z, d2=a)
        if state.rotate_pitch(x, z, a) != (stsim.s16(r["d0"]), stsim.s16(r["d1"])):
            bad.append(("rotate_pitch", x, z, a))
        dx, dz = big(), big()
        if rnd.random() < 0.1:
            dz = dx * rnd.choice((1, -1))
        r = call_regs(oracle, 0xAF8E, d0=dx, d1=dz)
        if state.atan2(dx, dz) != r["d0"] & 0xFFFF:
            bad.append(("atan2", dx, dz, hex(r["d0"] & 0xFFFF), hex(state.atan2(dx, dz))))
        t, c, step = word(), word(), rnd.choice((0x20, 0x100, 0x800, rnd.randrange(0x10000)))
        r = call_regs(oracle, 0x1A18A, d0=t, d1=c, d2=step)
        if stunits.UnitState.turn_towards(t, c, step) != r["d1"] & 0xFFFF:
            bad.append(("turn_towards", t, c, step))
        v = word()
        r = call_regs(oracle, 0x1E734, d0=v)
        if stunits.UnitState.self_centre(v) != r["d0"] & 0xFFFF:
            bad.append(("self_centre", v))
    print(f"helpers: {'OK' if not bad else 'MISMATCH'} ({n} random inputs each)")
    for b in bad[:10]:
        print("  ", b)
    return not bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=ROOT / "emu" / "out" / "action")
    ap.add_argument("--routine", action="append")
    ap.add_argument("--fuzz", type=int, default=2000, help="perturbed states per routine (where defined)")
    args = ap.parse_args()
    files = sorted(args.dir.glob("pass_*_in.bin"))
    if not files:
        sys.exit(f"no pass captures in {args.dir}; see the docstring")
    ok = check_helpers(files[0].read_bytes())
    for name in args.routine or ROUTINES:
        ok &= check_routine(name, files, args.fuzz)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
