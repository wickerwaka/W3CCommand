#!/usr/bin/env python3
"""Check the unit-layer model (tools/stunits.py) against the original game.

Inputs are pass captures from an action game:
  uv run python tools/capture_sim.py --action --steps 20 300 1500 --ticks 0 --passes 40 --out emu/out/action
For every routine in ROUTINES and every pass_*_in.bin capture, the original routine runs
under Unicorn and the model runs on the same state; every region of stsim.STATE_REGIONS and
stunits.UNIT_REGIONS must match byte for byte. Routines are checked one at a time from the
same captured states, so a mismatch points at a single routine.

Then whole passes against the Hatari pass_*_out captures, a 4000-pass run against the
original, and perturbed multi-pass scenarios (engagement, drones, flares, turret, voyage,
debris, collisions, hangars, construction); a scenario that reaches unported code stops
there and is listed, without failing.

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

U = stunits.UnitState
ROUTINES = {          # name: (original address, model method)
    "carrier_step": (0x1E744, U.carrier_step),
    "walrus_dock_door_step": (0x1ED94, U.walrus_dock_door_step),
    "vehicles_step": (0x1CD4E, U.vehicles_step),
    "debris_step": (0x1CC86, U.debris_step),
    "island_behaviours_step": (0x1962A, U.island_behaviours_step),
    "ai_objects_step": (0x1A56A, U.ai_objects_step),
    "defence_drones_step": (0x1A296, U.defence_drones_step),
    "island_range_check": (0x1B8AE, U.island_range_check),
    "object_passes": (0x1B746, U.object_passes),
    "carrier_systems_status": (0x17578, U.carrier_systems_status),
    "sim_pass": (None, U.sim_pass),
}
PASS, PASS_END = 0x40B0, 0x413E
RECORD = dict(check_sim.RECORD, **{f"object {k}": 16 for k in range(64)})


def regions():
    return stsim.STATE_REGIONS + stunits.UNIT_REGIONS


def diff(a: bytes, b: bytes, limit: int = 4) -> list:
    lines = []
    for name, start, length in regions():
        if name.startswith("redraw"):          # UI redraw counters, changed by the UI code too
            continue
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
        if addr is None:
            run_pass(oracle)
        else:
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


def run_pass(oracle) -> None:
    """One whole game_main simulation pass on the original, $40B0 up to $413E."""
    from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_SR
    oracle.uc.reg_write(UC_M68K_REG_SR, 0x2700)
    oracle.uc.reg_write(UC_M68K_REG_A7, check_sim.STACK)
    oracle.uc.emu_start(PASS, PASS_END, count=50_000_000)
    if oracle.uc.reg_read(UC_M68K_REG_PC) != PASS_END:
        raise RuntimeError("pass did not reach $413E")


def check_hatari(files: list) -> bool:
    """The model's pass against the real machine: each pass_*_in capture stepped once and
    compared with its pass_*_out capture from Hatari."""
    for f in files:
        ram, out = f.read_bytes(), f.with_name(f.name.replace("_in", "_out")).read_bytes()
        state = stunits.UnitState.from_ram(ram)
        state.sim_pass()
        mine = bytearray(out)
        state.to_ram(mine)
        lines = diff(mine, out)
        if lines:
            print(f"hatari pass: MISMATCH from {f.name}")
            for line in lines:
                print("  " + line)
            return False
    print(f"hatari pass: OK ({len(files)} passes)")
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


def scenario_engage(m, rnd):
    """Carrier parked close to the island it's attached to: the defences engage."""
    c, buf = stunits.CARRIER, m.u32(stunits.CARRIER + 0x3C)
    m.set32(c + 0x0A, m.u32(buf + 2) + rnd.randrange(-9000, 9000))
    m.set32(c + 0x12, m.u32(buf + 0x0A) + rnd.choice((-1, 1)) * rnd.randrange(16000, 22000))
    m.set8(stunits.C_FLAGS, 0x80)


def scenario_drones(m, rnd):
    """Defence drones out at their stations, some still flying there (positions outside
    the carrier's box, where the original's corner search would loop forever)."""
    def outside():
        return rnd.choice(((rnd.choice((-1, 1)) * rnd.randrange(900, 3200), rnd.randrange(-3200, 3200)),
                           (rnd.randrange(-3200, 3200), rnd.choice((-1, 1)) * rnd.randrange(1500, 3200))))
    for k in range(4):
        rec = 0x65E + 14 * k
        m.set16(rec, rnd.choice((0, 1, 1, 3, 5)))
        for o in (2, 6, 0xA):
            x, z = outside()
            m.set16(rec + o, x)
            m.set16(rec + o + 2, z)
    if rnd.random() < 0.3:
        m.set8(0x122F, 0xFF)
        m.set16(0x69A, rnd.randrange(4))
        for k in range(4):
            m.set16(0x65E + 14 * k, 0)


def scenario_flares(m, rnd):
    """Both decoy flares in flight, and the falling object 15."""
    for k in (15, 17, 18):
        a = stunits.OBJECTS + stunits.OBJECT_SIZE * k
        m.set16(a, 0x8403)
        m.set16(a + 2, rnd.choice((0x1E, 0x1F)) if k != 15 else 32)
        for o in (0x0A, 0x0E, 0x12):
            m.set32(a + o, m.u32(stunits.CARRIER + o) + rnd.randrange(-500, 500))
        m.set16(a + 6, rnd.randrange(0x10000))
        m.set16(a + 0x1E, rnd.randrange(20, 110))
        m.set16(a + 0x20, rnd.randrange(5, 200))
        m.set16(a + 0x22, rnd.randrange(-50, 50))
        m.set16(a + 0x24, rnd.randrange(0, 30))
        m.set8(a + 0x1A, rnd.choice((0, 0xFF)))
    m.set16(0x1152, 2)
    m.set32(0x1154, stunits.OBJECTS + stunits.OBJECT_SIZE * 15)


def scenario_turret(m, rnd):
    """Turret aim rates and self-centring flags."""
    m.set16(0x6CE, rnd.randrange(-3000, 3000))
    m.set16(0x6D0, rnd.randrange(-3000, 3000))
    m.set8(0x6D5, rnd.choice((0, 0x40, 0x80, 0xC0)))
    m.set16(0x6C8, rnd.randrange(0, 0x3FFF))
    for a in (0x93E, 0x940, 0x942, 0x115E):
        m.set16(a, rnd.randrange(0, 20))
    m.set8(stunits.POINTER_MODE, rnd.choice((0, 0xFF)))
    m.set16(stunits.MOUSE_DX, rnd.randrange(-50, 50))
    m.set16(0xDD0, rnd.choice((-1, 0, 1, 2, 3)))
    m.set16(stunits.DOCKED_WALRUS, rnd.choice((-1, 0, 1, 2, 3, 4)))
    for k in range(2, 10):
        a = stunits.OBJECTS + stunits.OBJECT_SIZE * k
        for o in (0x0A, 0x12):
            m.set32(a + o, m.u32(stunits.CARRIER + o) + rnd.randrange(-3000, 3000))


def scenario_voyage(m, rnd):
    """Autopilot to a distant island: buffers are released, then another one loads."""
    c = stunits.CARRIER
    i = rnd.randrange(64)
    isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i
    m.set32(c + 0x0A, m.u32(isl + 2) + rnd.choice((-1, 1)) * 140000)
    m.set32(c + 0x12, m.u32(isl + 6))
    m.set32(stunits.C_WAYPOINT_X, m.u32(isl + 2) + 30000)
    m.set32(stunits.C_WAYPOINT_Z, m.u32(isl + 6))
    m.set8(stunits.C_FLAGS, 0x07)
    m.set8(stunits.C_SUBSTATE, 1)
    m.set16(stunits.C_CRUISE, 0x70)


def scenario_debris(m, rnd):
    """Debris bursts in flight, shards taken from the free list."""
    for b in range(4):
        a = stunits.DEBRIS + 0x12 * b
        if rnd.random() < 0.3:
            continue
        m.set8(a, 1)
        m.set8(a + 1, rnd.choice((0, 100, 250, 255)))
        for o in (2, 6, 0x0A):
            m.set32(a + o, m.u32(stunits.CARRIER + 0x0A) if o == 2 else rnd.randrange(0, 400))
        head = 0
        for _ in range(rnd.randrange(0, 8)):
            shard = m.u32(0x1194)
            if not shard:
                break
            m.set32(0x1194, m.u32(shard + 0x16))
            m.set16(0x1198, m.u16(0x1198) - 1)
            for o in range(2, 0x16, 2):
                m.set16(shard + o, rnd.randrange(-300, 300))
            m.set32(shard + 0x16, head)
            head = shard
        m.set32(a + 0x0E, head)


def scenario_collide(m, rnd):
    """The enemy carrier or a flare near the player carrier, their pair test due."""
    k = rnd.choice((17, 18, 19))
    o = stunits.OBJECTS + stunits.OBJECT_SIZE * k
    m.set16(o, 0x9E03 if k == 19 else 0x8403)
    m.set16(o + 2, 4 if k == 19 else 0x1E)
    for off, v in zip((0x30, 0x32, 0x34, 0x36), ((-1, 170, 11, 250) if k == 19 else (100, 100, 3, 250))):
        m.set16(o + off, v)
    for off in (0x0A, 0x12):
        m.set32(o + off, m.u32(stunits.CARRIER + off) + rnd.choice((1, -1)) * rnd.choice((300, 1200, 2600, 9000, 40000)))
    m.set32(o + 0x0E, m.u32(stunits.CARRIER + 0x0E) + rnd.randrange(-100, 400))
    m.set16(o + 6, rnd.randrange(0x10000))
    m.set16(o + 0xC0 + 2 * 10, m.u16(stunits.FRAME))
    m.set32(o + 0x3C, 0)


def scenario_hangars(m, rnd):
    """Bays under repair or preparation; damaged carrier systems."""
    for base, timer in ((0x496, 0x16), (0x570, 0x0E)):
        for k in range(4):
            r = base + 0x34 * k
            st = rnd.choice((0, 1, 2))
            m.set8(r + 1, st)
            m.set16(r + 0x14, rnd.randrange(0, 3500))
            m.set16(r + timer, rnd.randrange(1, 4))
    for k in range(10):
        a = stsim.SYSTEMS + 8 * k
        m.set16(a, rnd.randrange(0, m.u16(a + 2) + 1))


def scenario_build(m, rnd):
    """Structures removed or damaged on the loaded island, so it rebuilds and repairs
    (3D entries created and mirrored)."""
    buf = m.u32(stunits.CARRIER + 0x3C)
    i = m.u8(buf + 1)
    isl = stsim.Island(m, i)
    for s in isl.slots[1:]:
        if stsim.T_BUILDING_FIRST <= s.type <= stsim.T_BUILDING_LAST and rnd.random() < 0.3:
            s.type = 0
        elif s.type and rnd.random() < 0.3:
            s.value = rnd.randrange(0, 1000)
    m.set16(stsim.G_ISLAND, (i - 1) & 63)


SCENARIOS = [scenario_engage, scenario_drones, scenario_flares, scenario_turret, scenario_voyage,
             scenario_debris, scenario_collide, scenario_hangars, scenario_build]


def long_run(files: list, passes: int) -> bool:
    """From the first capture, model and original pass by pass (screen input stubbed, the
    one-second flag every 8 passes)."""
    first = min(files, key=lambda p: [int(x) for x in p.name.split("_")[1:3]])
    ram = first.read_bytes()
    oracle = check_sim.Oracle(ram)
    oracle.uc.mem_write(0x17094, b"\x4e\x75")
    state = stunits.UnitState.from_ram(ram)
    for p in range(passes):
        if p % 8 == 0:
            oracle.uc.mem_write(0x8FD, b"\xff")
            state.mem.set8(0x8FD, 0xFF)
        state.sim_pass()
        run_pass(oracle)
        ref = oracle.ram()
        mine = bytearray(ref)
        state.to_ram(mine)
        lines = diff(mine, ref)
        if lines:
            print(f"long run: MISMATCH at pass {p}")
            for line in lines:
                print("  " + line)
            return False
    print(f"long run: OK ({passes} passes from {first.name})")
    return True


def run_scenarios(files: list, runs: int, passes: int = 800) -> bool:
    """Perturbed captures run pass by pass against the original (screen input stubbed).
    Reaching code that isn't ported yet ends a run without failing it."""
    import random
    rnd = random.Random(11)
    ok, frontier = True, {}
    for n in range(runs):
        scen = SCENARIOS[n % len(SCENARIOS)]
        m = stsim.Mem(bytearray(rnd.choice(files).read_bytes()))
        scen(m, rnd)
        oracle = check_sim.Oracle(bytes(m.buf))
        oracle.uc.mem_write(0x17094, b"\x4e\x75")
        state = stunits.UnitState.from_ram(bytes(m.buf))
        for p in range(passes):
            if p % 8 == 0:
                oracle.uc.mem_write(0x8FD, b"\xff")
                state.mem.set8(0x8FD, 0xFF)
            try:
                state.sim_pass()
            except (NotImplementedError, stsim.TacticalMode, stsim.EnemyDestroyed) as e:
                frontier[str(e) or type(e).__name__] = frontier.get(str(e) or type(e).__name__, 0) + 1
                break
            run_pass(oracle)
            ref = oracle.ram()
            mine = bytearray(ref)
            state.to_ram(mine)
            lines = diff(mine, ref)
            if lines:
                print(f"scenario {scen.__name__} run {n}: MISMATCH at pass {p}")
                for line in lines:
                    print("  " + line)
                ok = False
                break
    print(f"scenarios: {'OK' if ok else 'MISMATCH'} ({runs} runs of up to {passes} passes)")
    for k, v in sorted(frontier.items(), key=lambda kv: -kv[1]):
        print(f"  stopped at unported code {v}x: {k}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=ROOT / "emu" / "out" / "action")
    ap.add_argument("--routine", action="append")
    ap.add_argument("--fuzz", type=int, default=2000, help="perturbed states per routine (where defined)")
    ap.add_argument("--scenarios", type=int, default=90, help="perturbed multi-pass runs")
    args = ap.parse_args()
    files = sorted(args.dir.glob("pass_*_in.bin"))
    if not files:
        sys.exit(f"no pass captures in {args.dir}; see the docstring")
    ok = check_helpers(files[0].read_bytes())
    for name in args.routine or ROUTINES:
        ok &= check_routine(name, files, args.fuzz)
    if not args.routine:
        ok &= check_hatari(files)
        ok &= long_run(files, 4000)
        ok &= run_scenarios(files, args.scenarios)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
