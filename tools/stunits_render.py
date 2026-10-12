"""Unit layer: state the screens' render routines write, and the sound-event check.

RenderMixin.render_effects(screen) is the state the original's render for that screen
(table $144A0, run by screen_dispatch $14472 after every pass) and its panel draw callbacks
leave in memory, without the drawing: the app doesn't run the original render, so it calls
this once per frame for the screen shown (docs/units.md §10.7).

check_sound_events compares the model's sound events (docs/units.md §17: ("sound", id),
("explosion", level, object) / ("explosion", level, x, y, z), the ping element of
("message", text, ping)) with the calls the original makes ($20B90, $20C30, $20C08, the
message routines $A0DE/$A128 and the ping they play, the game-over tune), pass by pass.
"""
import random

import stsim  # noqa: F401
import stunits  # noqa: F401
from stsim import s16, s32, w16  # noqa: F401


MAP_SCREENS = (2, 17, 25)
MAP_ZOOM, MAP_X, MAP_Z, MAP_INFO = 0x75A, 0x75C, 0x760, 0x766
NEAREST, NEAREST_D2, NEAREST_OWNER, NEAREST_X, NEAREST_Z, MARKER_ZOOM = 0x764, 0x73E, 0x74C, 0x742, 0x746, 0x750
DRONE_TYPE = 66                       # screen 11's drone elements $42A4E + $16 k have types 66-69
FLAG_TABLE = 0x40EB2
TITLE_TIMER, TITLE_FLAG = 0x93A, 0x124C

# State the renders write that the other regions don't cover.
for _region in (("map render $73E", NEAREST_D2, 4), ("map render owner $74C", NEAREST_OWNER, 1),
                ("map render $750", MARKER_ZOOM, 2), ("title timer $93A", TITLE_TIMER, 4),
                ("title flag $124C", TITLE_FLAG, 2)):
    if _region not in stunits.UNIT_REGIONS:
        stunits.UNIT_REGIONS.append(_region)


def map_div(d: int, zoom: int) -> int:
    """$65C4: (d asr 8) divs.w zoom, floored (decremented when the remainder is negative,
    unless that overflows); a quotient beyond a word gives $7FFF / -$7FFF by the dividend's
    sign."""
    d4 = s32(d) >> 8
    z = s16(zoom)
    if z == 0:
        raise NotImplementedError("map zoom 0 (divs by zero)")
    q = abs(d4) // abs(z)
    if (d4 < 0) != (z < 0):
        q = -q
    if not -0x8000 <= q <= 0x7FFF:
        return -0x7FFF if d4 < 0 else 0x7FFF
    if d4 - q * z < 0 and q != -0x8000:
        q -= 1
    return q


class RenderMixin:
    """The state the screens' render routines write (docs/units.md §10.7). The app doesn't run
    the original render, so it calls render_effects() once per frame after the pass, for the
    screen shown."""

    def render_effects(self, screen: int = None) -> None:
        """screen_dispatch $14472 for [$718] (or `screen`) without the drawing: the debug-mode
        keys ($210F0, $17874), then the screen's render ($144A0) and the panel callbacks it
        runs. Ported: the maps (2, 17, 25: map_render_effects), messages (4), defence radar
        (11), the static noise of the turret (8) and vehicle views (18, 27), and the title
        (33). Not ported (no simulation effect found, docs/units.md §10.8): the 3D views'
        camera and pipeline writes and the arming screens' panel redraws."""
        m = self.mem
        if screen is None:
            screen = m.u16(stunits.SCREEN)
        if m.u8(0x1189):                                   # $210F0: debug display key
            m.set8(0x871, m.u8(0x871) & 0xFE)
        fn = RENDER_EFFECTS.get(screen)
        if fn:
            getattr(self, fn)()
        # $17874: in a running game in debug mode, key $869 toggles the frame-rate display.
        if m.u8(0x462) and not m.u8(0x461) and m.u8(0x1189) and m.u8(0x869) & 1:
            m.set8(0x869, m.u8(0x869) & 0xFE)
            m.set8(0x122D, ~m.u8(0x122D) & 0xFF)

    def map_render_effects(self) -> None:
        """$14E3C (screens 2, 17, 25) without the drawing. With the island information box up
        ([$766] != 0) it only draws. Otherwise $65E2: [$764] high byte = $FF (st.b), [$73E] =
        2000000, [$750] = min(zoom, 8); on the carrier map in islands mode ([$710] != 0) the
        islands panel's recount $CDF2 ([$D54] friendly, [$D56] hostile, [$D58 + 2 kind] for
        each friendly island's economy +0 byte; $D58 itself isn't cleared); then each island
        within +-255 px of the centre (unsigned, after neg) whose mx^2 + my^2 (mulu) is below
        [$73E] (unsigned) becomes the nearest: [$73E], [$764] index, [$74C] owner, [$742/$746]
        position. Last, the unit markers leave their turn pair in $A6C/$A6E: on screen 2 the
        carrier's ((heading + $800) & $F000) sin/cos; on 17/25 the cursor's (0, $4000)."""
        m = self.mem
        if m.u16(MAP_INFO):
            return
        m.set8(NEAREST, 0xFF)
        m.set32(NEAREST_D2, 0x1E8480)
        zoom = m.u16(MAP_ZOOM)
        m.set16(MARKER_ZOOM, 8 if s16(zoom) > 8 else zoom)
        screen = m.u16(stunits.SCREEN)
        if screen == 2 and m.u16(0x710):
            self.island_counts()
        cx, cz = m.u32(MAP_X), m.u32(MAP_Z)
        for i in range(64):
            isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i
            mx = map_div(s32(m.u32(isl + 2) - cx), zoom)
            my = map_div(s32(-s32(m.u32(isl + 6) - cz)), zoom)
            ax, ay = (-mx) & 0xFFFF if mx < 0 else mx, (-my) & 0xFFFF if my < 0 else my
            if ax >= 0x100 or ay >= 0x100:
                continue
            d2 = ax * ax + ay * ay
            if d2 >= m.u32(NEAREST_D2):
                continue
            m.set32(NEAREST_D2, d2)
            m.set16(NEAREST, i)
            m.set8(NEAREST_OWNER, m.u8(stsim.OWNERS + i))
            m.set32(NEAREST_X, m.u32(isl + 2))
            m.set32(NEAREST_Z, m.u32(isl + 6))
        if screen == 2:
            d7 = w16(m.u16(stunits.CARRIER + 6) + 0x800) & 0xF000
            m.set16(0xA6C, m.u16(stunits.SINE + (d7 >> 3)))
            m.set16(0xA6E, m.u16(stunits.SINE + (w16(d7 + 0x4000) >> 3)))
        else:
            m.set32(0xA6C, 0x4000)

    def island_counts(self) -> None:
        """$CDF2: [$D54] friendly islands, [$D56] hostile, and per friendly island
        [$D58 + 2 * (economy +0 byte)] + 1, after clearing $D54, $D56, $D5A, $D5C, $D5E."""
        m = self.mem
        for a in (0xD54, 0xD56, 0xD5A, 0xD5C, 0xD5E):
            m.set16(a, 0)
        for i in range(64):
            o = m.u8(stsim.OWNERS + i)
            if o == 1:
                m.set16(0xD54, m.u16(0xD54) + 1)
                a = 0xD58 + 2 * m.u8(stsim.ECONOMY + stsim.ECONOMY_SIZE * i)
                m.set16(a, m.u16(a) + 1)
            elif o:
                m.set16(0xD56, m.u16(0xD56) + 1)

    def _messages_render(self) -> None:
        """$162B0: the log is redrawn while $966 counts down. (Its expiry of old records
        changes the log at $65D10, which the app keeps itself.)"""
        m = self.mem
        if m.u16(0x966):
            m.set16(0x966, m.u16(0x966) - 1)

    def _radar_render(self) -> None:
        """$15EB0: drone k's element (type [$42A4E + $16 k] = 66 + k) is shown (flag bit 15 clear) while
        the drone is out (station +1 bit 0) and the radar works ([$B02] != 0), else hidden.
        (The element positions it writes are UI.)"""
        m = self.mem
        for k in range(4):
            a = FLAG_TABLE + 2 * (DRONE_TYPE + k)
            if m.u8(0x65E + 14 * k + 1) & 1 and m.u8(0xB02):
                m.set8(a, m.u8(a) & 0x7F)
            else:
                m.set8(a, m.u8(a) | 0x80)

    def _turret_render(self) -> None:
        """$15A94: with the observation turret working ([$B12] != 0) the zoom view record's
        +8/+$A = [$6D2] + 8 (and the 3D view, not ported); otherwise static noise ($5538:
        one rand)."""
        m = self.mem
        if m.u8(0xB12):
            m.set16(0x478E, w16(m.u16(0x6D2) + 8))
            m.set16(0x4790, w16(m.u16(0x6D2) + 8))
        else:
            self.rand()

    def _walrus_view_render(self) -> None:
        """$15496 (screen 18): static noise (one rand) when [$70E] != 0, the Walrus object
        [$56C] is live (+1 bit 0) and its record's telemetry [$568] +$2C ($712) != 0. The 3D
        view is not ported."""
        m = self.mem
        if m.u16(0x70E) and m.u8(m.u32(0x56C) + 1) & 1 and m.u16(m.u32(0x568) + 0x2C):
            self.rand()

    def _manta_view_render(self) -> None:
        """$15244 (screen 27): as screen 18 for the Manta: [$70C] != 0, record [$48E] not
        destroyed (+1 >= 0), object [$492] live, telemetry +$2E != 0."""
        m = self.mem
        rec = m.u32(0x48E)
        if m.u16(0x70C) and not m.u8(rec + 1) & 0x80 and m.u8(m.u32(0x492) + 1) & 1 and m.u16(rec + 0x2E):
            self.rand()

    def _title_render(self) -> None:
        """$15DA2 (screen 33, title menu [$708] = 0): one rand; the carrier model's turret
        ($6CC + 300, $6CA swinging between 0 and -14000 by 800, $6D4 the direction); Control
        with A / B sets $124C to 0 / 1 (with M it ends the wait at once); in debug mode keypad
        +/- (new flags $835/$82D, consumed) restart the wait at 1000; the wait $93C counts down
        and at 0 grows $93A by 180 (to at most 3600), restarts and goes back to the title
        ($1231 = $FF, $4272: tab 5 requested, $708 = 0; $458 = $FF, $881 = 0). The 3D model
        view is not ported."""
        m = self.mem
        if m.u16(0x708):
            return
        self.rand()
        m.set16(0x6CC, w16(m.u16(0x6CC) + 0x12C))
        d0 = s16(w16((-0x320 if m.u8(0x6D4) else 0x320) + m.u16(0x6CA)))
        d1 = d0
        d0 = 0 if d0 > 0 else -0x36B0 if d0 < -0x36B0 else d0
        m.set16(0x6CA, d0)
        if d1 != d0:
            m.set8(0x6D4, ~m.u8(0x6D4) & 0xFF)
        expire = False
        if m.u8(0x7D2):
            if m.u8(0x7FC):
                expire = True
            elif m.u8(0x7D4):
                m.set16(TITLE_FLAG, 0)
            elif m.u8(0x7F8):
                m.set16(TITLE_FLAG, 1)
        if not expire:
            if m.u8(0x1189):
                if m.u8(0x835) & 1:
                    m.set8(0x835, m.u8(0x835) & 0xFE)
                    m.set16(TITLE_TIMER + 2, 1000)
                elif m.u8(0x82D) & 1:
                    m.set8(0x82D, m.u8(0x82D) & 0xFE)
                    m.set16(TITLE_TIMER + 2, 1000)
            t = w16(m.u16(TITLE_TIMER + 2) - 1)
            m.set16(TITLE_TIMER + 2, t)
            if t:
                return
        d0 = w16(m.u16(TITLE_TIMER) + 0xB4)
        if s16(d0) > 0xE10:
            d0 = 0xE10
        m.set16(TITLE_TIMER, d0)
        m.set16(TITLE_TIMER + 2, d0)
        m.set8(0x1231, 0xFF)
        m.set8(0x1227, 0xFF)
        m.set16(0x1228, 5)
        m.set16(0x708, 0)
        m.set8(0x458, 0xFF)
        m.set8(0x881, 0)


# $144A0: the screens whose render writes state that is ported.
RENDER_EFFECTS = {2: "map_render_effects", 17: "map_render_effects", 25: "map_render_effects",
                  4: "_messages_render", 11: "_radar_render", 8: "_turret_render",
                  18: "_walrus_view_render", 27: "_manta_view_render", 33: "_title_render"}


# ---- the sound-event check -----------------------------------------------------------------
# The world events that are message calls ($A128, header bit 7 clear) in the original.
MESSAGE_EVENTS = ("attacked", "fallen", "repulsed", "captured", "neutral")


def sound_hooks(oracle) -> list:
    """Log the original's sound calls on `oracle`: ("message", ping), ("sound", id),
    ("explosion", level, a1) / ("explosion", level, x, y, z)."""
    from unicorn import UC_HOOK_CODE
    from unicorn.m68k_const import UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A7, UC_M68K_REG_D0
    uc, log = oracle.uc, []

    def u16(a): return int.from_bytes(uc.mem_read(a, 2), "big")
    def u32(a): return int.from_bytes(uc.mem_read(a, 4), "big")

    def message(uc_, addr, size, _):
        log.append(["message", 0])

    def effect(uc_, addr, size, _):
        ret = u32(uc.reg_read(UC_M68K_REG_A7))
        if ret - 6 in (0xA11E, 0xA168):
            for e in reversed(log):
                if e[0] == "message":
                    e[1] = 1
                    break
            return
        log.append(["sound", u16(ret)])

    def explosion(uc_, addr, size, _):
        level = stsim.s16(uc.reg_read(UC_M68K_REG_D0) & 0xFFFF)
        a1 = uc.reg_read(UC_M68K_REG_A1) & 0xFFFFFF
        if addr == 0x20C30:
            log.append(["explosion", level, a1])
        else:
            log.append(["explosion", level] + [s32(s16(u16(a1 + 0x0A + 2 * k)) + u32(0xA4A + 4 * k)) for k in range(3)])

    def tune(uc_, addr, size, _):
        if uc.reg_read(UC_M68K_REG_A0) == 0x4B72E:
            log.append(["sound", "game over"])

    for a in (0xA0DE, 0xA128):
        uc.hook_add(UC_HOOK_CODE, message, begin=a, end=a)
    uc.hook_add(UC_HOOK_CODE, effect, begin=0x20B90, end=0x20B90)
    for a in (0x20C30, 0x20C08):
        uc.hook_add(UC_HOOK_CODE, explosion, begin=a, end=a)
    uc.hook_add(UC_HOOK_CODE, tune, begin=0x14446, end=0x14446)
    return log


def model_sounds(state, events) -> list:
    """The model's events in the form sound_hooks logs."""
    out = []
    quiet = state.mem.u8(0x1247)
    for e in events:
        if e[0] == "message":
            out.append(["message", e[2]])
        elif e[0] in MESSAGE_EVENTS:
            out.append(["message", 0 if quiet else 1])
        elif e[0] in ("sound", "explosion"):
            out.append(list(e))
    return out


def _sound_options(m, rnd) -> None:
    """Message ping and sound effects on or off ($1247, $1249), and a view pointer."""
    m.set8(0x1247, rnd.choice((0, 0, 0xFF)))
    m.set8(0x1249, rnd.choice((0, 0, 0xFF)))


KINDS = {}


def _count(log) -> None:
    for e in log:
        k = (e[0], e[1]) if e[0] != "explosion" or len(e) == 3 else ("explosion entry", e[1])
        KINDS[k] = KINDS.get(k, 0) + 1


def _kinds() -> str:
    return ", ".join(f"{k[0]} {k[1]} x{v}" for k, v in sorted(KINDS.items(), key=str))


def _run_compare(label: str, ram: bytes, passes: int, frontier: dict) -> tuple:
    """Model and original pass by pass (screen input stubbed, the one-second flag every 8
    passes); returns (ok, number of sound calls compared)."""
    import check_sim
    import check_units
    oracle = check_sim.Oracle(ram)
    oracle.uc.mem_write(0x17094, b"\x4e\x75")
    log = sound_hooks(oracle)
    state = stunits.UnitState.from_ram(ram)
    n = 0
    for p in range(passes):
        if p % 8 == 0:
            oracle.uc.mem_write(0x8FD, b"\xff")
            state.mem.set8(0x8FD, 0xFF)
        state.events.clear()
        try:
            state.sim_pass()
            dev = [e for e in state.events if e[0] == "deviation"]
            if dev:
                raise NotImplementedError(f"deviation: {dev[0][1]}")
        except (NotImplementedError, stsim.TacticalMode, stsim.EnemyDestroyed) as e:
            k = str(e) or type(e).__name__
            frontier[k] = frontier.get(k, 0) + 1
            return True, n
        del log[:]
        check_units.run_pass(oracle)
        mine = model_sounds(state, state.events)
        if mine != log:
            print(f"sound events {label}: MISMATCH at pass {p}")
            print(f"    original {log}")
            print(f"    model    {mine}")
            print(f"    events   {state.events}")
            return False, n
        n += len(log)
        _count(log)
        ref = oracle.ram()
        out = bytearray(ref)
        state.to_ram(out)
        if check_units.diff(out, ref):
            print(f"sound events {label}: state MISMATCH at pass {p}")
            return False, n
    return True, n


def check_sound_events(files: list, runs: int = 120, passes: int = 400) -> bool:
    """The model's sound events against the original's calls, pass by pass: a long run from
    the first action capture, the play-session captures, and the perturbed scenarios of
    every module (with the ping and sound-effect options switched at random)."""
    import check_units
    from stgfx import ROOT
    rnd = random.Random(23)
    ok, frontier, calls, kinds = True, {}, 0, 0
    first = min(files, key=lambda p: [int(x) for x in p.name.split("_")[1:3]])
    good, n = _run_compare("long run", first.read_bytes(), 3000, frontier)
    ok &= good
    calls += n
    play = sorted((ROOT / "emu" / "out" / "play").glob("pass_*_in.bin"))
    for f in play[::12]:
        good, n = _run_compare(f.name, f.read_bytes(), 60, frontier)
        ok &= good
        calls += n
    scen = check_units.all_scenarios()
    for r in range(runs):
        sc = scen[r % len(scen)]
        m = stsim.Mem(bytearray(rnd.choice(files + play).read_bytes()))
        sc(m, rnd)
        _sound_options(m, rnd)
        good, n = _run_compare(f"{sc.__name__} run {r}", bytes(m.buf), passes, frontier)
        ok &= good
        calls += n
    import stunits_vehicles
    weapons = (stunits_vehicles.scenario_manta_weapons, stunits_vehicles.scenario_walrus_weapons)
    for r in range(runs // 2):                 # the vehicles' weapons, sound effects on
        sc = weapons[r % 2]
        m = stsim.Mem(bytearray(rnd.choice(files + play).read_bytes()))
        sc(m, rnd)
        m.set8(0x1249, 0)
        good, n = _run_compare(f"{sc.__name__} run {r}", bytes(m.buf), 40, frontier)
        ok &= good
        calls += n
    print(f"sound events: {'OK' if ok else 'MISMATCH'} ({calls} calls compared; long run, "
          f"{len(play[::12])} play captures, {runs} scenario runs, {runs // 2} weapon runs)")
    for k, v in sorted(frontier.items(), key=lambda kv: -kv[1]):
        print(f"  stopped at unported code {v}x: {k}")
    print("  calls: " + _kinds())
    return ok


class _SoundTap:
    """While installed, every check_sim.Oracle logs its sound calls, and every
    check_units.diff (the state comparison all the module checks end in) also compares the
    sound events the model recorded since the last comparison with the calls the original
    logged since then. The pair compared is the state last written out (to_ram) and the
    oracle last read (ram()): what that diff compares."""

    def __init__(self):
        self.oracle = self.state = None
        self.calls, self.compared, self.fails, self.nfail, self.label = 0, 0, [], 0, ""

    def __enter__(self):
        import check_sim
        import check_units
        tap, U, O = self, stunits.UnitState, check_sim.Oracle
        self.saved = (O.__init__, O.ram, U.__dict__.get("to_ram"), check_units.diff)
        init, ram, _, diff = self.saved
        to_ram = U.to_ram

        def oracle_init(o, *a, **k):
            init(o, *a, **k)
            o.sound_log = sound_hooks(o)

        def oracle_ram(o):
            tap.oracle = o
            return ram(o)

        def state_to_ram(st, buf):
            tap.state = st
            return to_ram(st, buf)

        def tapped_diff(*a, **k):
            lines = diff(*a, **k)
            tap.compare()
            return lines
        O.__init__, O.ram, U.to_ram, check_units.diff = oracle_init, oracle_ram, state_to_ram, tapped_diff
        return self

    def __exit__(self, *exc):
        import check_sim
        import check_units
        O, U = check_sim.Oracle, stunits.UnitState
        O.__init__, O.ram, to_ram, check_units.diff = self.saved
        if to_ram is None:
            del U.to_ram
        else:
            U.to_ram = to_ram

    def compare(self) -> None:
        st, o = self.state, self.oracle
        if st is None or o is None or not hasattr(o, "sound_log"):
            return
        new = st.events[min(getattr(st, "_snd", 0), len(st.events)):]
        mine = model_sounds(st, new)
        if mine != o.sound_log:
            self.nfail += 1
            if len(self.fails) < 8:
                self.fails.append((self.label, list(o.sound_log), mine, new))
        self.compared += 1
        self.calls += len(o.sound_log)
        _count(o.sound_log)
        st._snd = len(st.events)
        del o.sound_log[:]
        self.state = self.oracle = None


def check_sound_commands(files: list) -> bool:
    """The module checks that issue commands and run passes with input (the carrier,
    hangar, controls, vehicle and war commands, screen switching, game frames, vehicle and
    war runs, the lock-on), re-run with the sound tap: every state comparison they make
    also compares the sound calls."""
    import contextlib
    import io
    import stunits_carrier
    import stunits_controls as sc
    import stunits_vehicles
    checks = [stunits_carrier.check_commands, stunits_vehicles.check_commands, stunits_vehicles.check_lock_on,
              stunits_vehicles.check_accb_capture,
              sc.check_commands, sc.check_screen_switch, sc.check_vehicle_commands, sc.check_vehicle_runs,
              sc.check_war_commands, sc.check_war_runs]
    ok = True
    with _SoundTap() as tap:
        for check in checks:
            tap.label = f"{check.__module__}.{check.__name__}"
            with contextlib.redirect_stdout(io.StringIO()) as out:
                good = check(files)
            if not good:
                print(out.getvalue())
                ok = False
    for label, orig, mine, events in tap.fails:
        print(f"  {label}: MISMATCH")
        print(f"    original {orig}")
        print(f"    model    {mine}")
        print(f"    events   {events}")
    ok &= not tap.fails
    print(f"sound events (commands and runs): {'OK' if ok else 'MISMATCH'} ({tap.compared} comparisons, "
          f"{tap.calls} calls, {len(checks)} module checks{f', {tap.nfail} mismatches' if tap.nfail else ''})")
    print("  calls so far: " + _kinds())
    return ok


def check_virus_capture(files: list, n: int = 300) -> bool:
    """hit_virus_bomb ($1C512) called directly on both, a virus bomb (object 30, type $15)
    hit by a command centre (type $29) of the carrier's island, at angles inside and outside
    the capture cone, on islands of every owner, sound effects on or off: the capture sound
    11 and the level-0 explosions (the general runs rarely reach a capture)."""
    import check_sim
    from unicorn.m68k_const import UC_M68K_REG_A1, UC_M68K_REG_A4, UC_M68K_REG_D3
    rnd = random.Random(29)
    ok, runs = True, 0
    for k in range(n):
        m = stsim.Mem(bytearray(files[k % len(files)].read_bytes()))
        buf = m.u32(stunits.CARRIER + 0x3C)
        if not buf:
            continue
        e = buf + 0x12 + 0x40 * rnd.randrange(1, 32)
        m.set16(e, 0x0403)
        m.set16(e + 2, 0x29)
        v = stunits.OBJECTS + stunits.OBJECT_SIZE * 30
        m.set16(v, 0x8703)
        m.set16(v + 2, 0x15)
        for o in (0x0A, 0x0E, 0x12):
            m.set32(v + o, m.u32(stunits.CARRIER + o) + rnd.randrange(-3000, 3000))
        m.set32(0xA9C, buf)
        m.set16(0xAB0, rnd.randrange(-2000, 2000))
        m.set16(0xAB2, rnd.randrange(-2000, 2000))
        st = stunits.UnitState.from_ram(bytes(m.buf))
        m.set16(e + 6, st.atan2(m.s16(0xAB0), m.s16(0xAB2)) + rnd.choice((0, 3000, -9000, 12000, 0x8000)))
        m.set8(stsim.OWNERS + m.u8(buf + 1), rnd.randrange(3))
        m.set16(stsim.STOCKPILE, rnd.choice((m.u8(buf + 1), 0xFFFF, 3)))
        _sound_options(m, rnd)
        ram = bytes(m.buf)
        oracle = check_sim.Oracle(ram)
        log = sound_hooks(oracle)
        for r, val in ((UC_M68K_REG_A1, v), (UC_M68K_REG_A4, e), (UC_M68K_REG_D3, 3)):
            oracle.uc.reg_write(r, val)
        oracle.call(0x1C512)
        st = stunits.UnitState.from_ram(ram)
        st.hit_virus_bomb(e, v, 3)
        ref = oracle.ram()
        out = bytearray(ref)
        st.to_ram(out)
        import check_units
        mine = model_sounds(st, st.events)
        runs += 1
        _count(log)
        if mine != log or check_units.diff(out, ref):
            print(f"virus capture: MISMATCH from {files[k % len(files)].name}: original {log}, model {mine}")
            ok = False
            break
    print(f"virus capture: {'OK' if ok else 'MISMATCH'} ({runs} hits)")
    return ok


# ---- the render-effects check ------------------------------------------------------------------
VIEW_SCREENS = (0, 1, 8, 9, 10, 16, 18, 19, 24, 26, 27, 33)
# What the 3D views write and render_effects leaves out (docs/units.md §10.8): the camera
# object and the viewed units' visible bits, island-entry display fields, the island context
# and rotation matrices of the 3D pipeline, the view pointers and viewport records.
UNPORTED_3D = ("objects", "unit ", "enemy carrier", "island buffer", "island context", "matrices $A68",
               "view pointers", "viewport $6FE", "viewport records $4744")
# The arming screens' panel redraws while $95E/$960 (not modelled) are set.
UNPORTED_ARMING = ("hangar panels $DC0", "vehicle bays", "redraw: panel flags $40EB2")


def _skipped(screen: int, name: str, addr: int) -> bool:
    if screen in VIEW_SCREENS and name.startswith(UNPORTED_3D):
        return not (screen == 8 and 0x478E <= addr < 0x4792)
    return screen in (16, 24) and name.startswith(UNPORTED_ARMING)


def _render_original(ram: bytes) -> bytes:
    import stscreens
    t = stscreens.Tracer(ram)
    t.call(0x14472)
    out = bytearray(ram)
    out[:0x80000] = bytes(t.uc.mem_read(0, 0x80000))
    return bytes(out)


def _render_states(files: list, rnd, n_switch: int) -> list:
    """(label, ram) on every screen: the Hatari units-frame captures (taken on each screen),
    and action/play captures switched by the original's clicks to a random screen (after
    random UI, war or vehicle perturbations), the stubs the switch oracle uses undone."""
    import stunits_controls as sc
    from stgfx import ROOT, Payload
    pay = Payload()
    out = [(f.name, f.read_bytes()) for f in sorted((ROOT / "emu" / "out" / "units-frame").glob("ram_*.bin"))]
    play = sorted((ROOT / "emu" / "out" / "play").glob("pass_*_in.bin"))
    pool = list(files[::10]) + play[::20]
    for k in range(n_switch):
        f = rnd.choice(pool)
        target = rnd.choice(sc.ALL_SCREENS + MAP_SCREENS * 4 + (8, 11, 18, 27, 33))
        m = stsim.Mem(bytearray(f.read_bytes()))
        rnd.choice((sc._perturb_screens, sc._perturb_war, sc._perturb_vehicles))(m, rnd)
        m.set8(sc.RUNNING, 0xFF)
        m.set8(sc.MODAL, 0)
        m.set16(0x704, 0)
        m.set16(0x706, 0)
        o = sc._switch_oracle(bytes(m.buf))
        try:
            if target in (33, 34, 35):
                o.uc.mem_write(sc.SCREEN_REQUEST, b"\xff" + bytes(1) + bytes([sc.TAB_OF[target]]))
                sc._call(o, sc.UI_FRAME)
            else:
                sc._oracle_switch(o, target)
        except RuntimeError:
            continue
        ram = bytearray(o.ram())
        for a, n in ((0x14472, 2), (0x17F48, 2), (0x14454, 2), (0x170D4, 36 * 4)):
            ram[a:a + n] = pay.bytes(a, n)
        out.append((f"{f.name} -> {target}", bytes(ram)))
    return out


def _perturb_render(m, rnd, screen: int) -> None:
    """The inputs the ported renders read: map view, owners, kinds, islands mode, info box;
    $966; drone stations and the radar; the turret; telemetry; the title's keys and timers;
    debug mode and its keys."""
    import stscreens_map
    if screen in MAP_SCREENS and rnd.random() < 0.8:
        m.buf[:0x80000] = stscreens_map.perturb(bytes(m.buf[:0x80000]), rnd, screen)
        if rnd.random() < 0.2:
            m.set16(MAP_INFO, 1)
        if rnd.random() < 0.05:
            level = rnd.choice((11, 12, 15))
            m.set16(0x758, level)
            m.set16(MAP_ZOOM, rnd.choice((1 << level, 3, 0xFFFF, 0x8000)))
    m.set16(0x966, rnd.choice((0, 0, 1, 2)))
    for k in range(4):
        m.set8(0x65E + 14 * k + 1, rnd.choice((0, 1, 3)))
    m.set8(0xB02, rnd.choice((0, 0xFF, 0xFF)))
    m.set8(0xB12, rnd.choice((0, 0xFF, 0xFF)))
    m.set16(0x6D2, rnd.randrange(4))
    for rec, off in ((m.u32(0x568), 0x2C), (m.u32(0x48E), 0x2E)):
        if 0x400 <= rec < 0x80000:
            m.set16(rec + off, rnd.choice((0, 0, 1, 2)))
    m.set16(0x6CA, rnd.choice((0, -0x36B0, rnd.randrange(-0x36B0, 0), 0x100)))
    m.set8(0x6D4, rnd.choice((0, 0xFF)))
    m.set16(TITLE_TIMER, rnd.choice((0xE10, 0x100, rnd.randrange(0x1000))))
    m.set16(TITLE_TIMER + 2, rnd.choice((1, 1, 2, 500)))
    for a in (0x7D2, 0x7FC, 0x7D4, 0x7F8, 0x835, 0x82D, 0x869, 0x871):
        m.set8(a, rnd.choice((0, 0, 0, 1)))
    m.set8(0x1189, rnd.choice((0, 0, 0xFF)))
    m.set8(0x122D, rnd.choice((0, 0xFF)))


def check_render_effects(files: list, n_switch: int = 600, fuzz: int = 3) -> bool:
    """render_effects against screen_dispatch $14472 (the render and the panel callbacks it
    runs) under Unicorn: every state region after the render, except what the 3D views and the
    arming screens' panel redraws write (UNPORTED_3D, UNPORTED_ARMING), on the units-frame
    captures (every screen) and captures switched to random screens, each also perturbed."""
    import check_units
    from collections import Counter
    rnd = random.Random(43)
    states = _render_states(files, rnd, n_switch)
    ok, screens, unported, errors = True, Counter(), Counter(), 0
    for label, ram in states:
        variants = [ram]
        for _ in range(fuzz):
            m = stsim.Mem(bytearray(ram))
            _perturb_render(m, rnd, m.u16(stunits.SCREEN))
            variants.append(bytes(m.buf))
        for v, ram2 in enumerate(variants):
            screen = int.from_bytes(ram2[stunits.SCREEN:stunits.SCREEN + 2], "big")
            state = stunits.UnitState.from_ram(ram2)
            try:
                state.render_effects()
            except NotImplementedError as e:
                unported[str(e)] += 1
                continue
            try:
                ref = _render_original(ram2)
            except RuntimeError:
                errors += 1                      # the original faults on this perturbed state
                continue
            mine = bytearray(ref)
            state.to_ram(mine)
            for name, a, n in check_units.regions():
                for o in range(n):
                    if mine[a + o] != ref[a + o] and not _skipped(screen, name, a + o):
                        print(f"render effects: MISMATCH on screen {screen} from {label} (variant {v}) at "
                              f"${a + o:05X} ({name}): want {ref[a + o]:02X} got {mine[a + o]:02X}")
                        return False
            screens[screen] += 1
    print(f"render effects: {'OK' if ok else 'MISMATCH'} ({sum(screens.values())} renders, screens "
          f"{dict(sorted(screens.items()))}{f', {errors} faulted in the original' if errors else ''})")
    for k, v in unported.items():
        print(f"  stopped at unported code {v}x: {k}")
    return ok


ROUTINES = {}
SCENARIOS = []
CHECKS = [check_sound_events, check_virus_capture, check_sound_commands, check_render_effects]
