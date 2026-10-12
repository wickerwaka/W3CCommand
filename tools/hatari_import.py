#!/usr/bin/env python3
"""Load a game state (a 512 KB RAM image, e.g. the web app's Export) into the original game
running in Hatari, and continue it there: the browser -> emulator direction of PLAN.md phase 2.

Procedure: boot CARRIER.TOS, click "Start Action Game" on the title menu (as
tools/capture_play.py does; this puts the machine in its in-game hardware setup), wait 300
frames, then at the top of the game_main loop ($408A: game_main's own frame, SSP = $586AA, no
subroutine or interrupt handler active) load the image with the debugger's `loadbin` and
continue. $408A is where a web pass ends: the app's pass is one whole loop (ui_frame,
game_frame, render state, frame_flip), so an export is a loop-top state.

What is loaded (--load):
  full     (default) $400-$7FFFF, except the static-noise texture $5C2DA-$5DCD9 (random words
           drawn by the static effect, no simulation state; a new game made by the app from the
           title image has the title's data there, because the app's export carries only the
           state regions over the image it started from). The vectors $0-$3FF and the CPU
           registers stay the emulator's (the game installs the same vectors; nothing in the
           loop carries a register across $408A). Everything else comes from the image:
           interrupt-handler state, screen buffers, the dead stack below SSP.
  regions  only the model's state regions (stsim.STATE_REGIONS + stunits.UNIT_REGIONS), over
           the emulator's own running action game. The simulation continues the same way;
           the display and UI state are the other game's.
--redraw (default for play) then sets [$71A] = [$718]: the next ui_frame re-enters the
current screen and draws its panels, as a click on the screen's icon would (a new game made
from the title image otherwise shows the title picture around the 3D view until the player
changes screens).

  uv run python tools/hatari_import.py play STATE.bin            # GUI with sound: play on
  uv run python tools/hatari_import.py capture STATE.bin [--out emu/out/import] [--passes 40]
      headless: before.bin / imported.bin (RAM just before / after the load), regs.txt,
      pass_<n>_in/out.bin ($40B0/$413E, as tools/capture_sim.py), then --vbls frames later
      screenshot.png and ram.bin
  uv run python tools/hatari_import.py verify [--passes 40] [--redraw] [--load regions]
      makes exports the way the web app does (a play capture advanced 50 passes by the model;
      new strategy/action games made from the title image, after 0, 20 and 200 passes),
      imports each into Hatari (emu/out/import/<case>_<load>/) and compares Hatari's passes
      with the model continuing from the same export (see compare()).
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_sim import ACTION_BUTTON, PASS, PASS_END, TITLE_VBLS
from hatari import BASE_ARGS, EMU, GAME_MAIN, prepare

LOOP_TOP = 0x408A           # game_main's loop: jsr ui_frame ($F636)
RAM_SIZE = 0x80000
LOAD_FROM = 0x400
NOISE, NOISE_END = 0x5C2DA, 0x5C2DA + 2 * 0xD00   # the static-noise texture (stunits_setup)
SCREEN_SELECT = 0x71A       # pending screen: [$718] here re-enters the current screen
GAME_VBLS = 300             # after the action-game click, before the import


# ---- the load -----------------------------------------------------------------------------
def regions() -> list:
    import stsim
    import stunits
    spans = sorted((a, a + n) for _, a, n in stsim.STATE_REGIONS + stunits.UNIT_REGIONS)
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def spans(mode: str) -> list:
    """[start, end) ranges loaded from the state."""
    if mode == "full":
        return [[LOAD_FROM, NOISE], [NOISE_END, RAM_SIZE]]
    return regions()


def redraw_command(state: bytes) -> str:
    """Re-enter the state's screen ([$71A] = [$718]): the next ui_frame leaves and enters it
    and draws its panels, as a click on the screen's own icon does."""
    return f"w ${SCREEN_SELECT:x} {state[0x718]} {state[0x719]}\n"


def load_commands(state: bytes, out: Path, mode: str) -> str:
    """The debugger commands that load `state`; the pieces are written to out/load/."""
    if len(state) != RAM_SIZE:
        sys.exit(f"a state is a {RAM_SIZE}-byte RAM image ($0-$7FFFF), got {len(state)} bytes")
    if state[0x404:0x418] != b"Atari Bundle Version":
        sys.exit("not a Carrier Command RAM image (no payload at $400)")
    d = out / "load"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.bin"):
        old.unlink()
    cmds = []
    for a, b in spans(mode):
        f = d / f"{a:05x}.bin"
        f.write_bytes(state[a:b])
        cmds.append(f"loadbin {f} ${a:x}\n")
    return "".join(cmds)


class Chain:
    """Chained debugger scripts, built back to front (each one arms the next stage)."""

    def __init__(self, out: Path):
        self.out, self.n = out, 0
        for old in out.glob("s*.txt"):
            old.unlink()

    def file(self, body: str) -> Path:
        self.n += 1
        path = self.out / f"s{self.n:03d}.txt"
        path.write_text(body)
        return path

    @staticmethod
    def then(cond: str, nxt: Path) -> str:
        return f"b {cond} :once :trace :file {nxt}\n"

    def boot(self, nxt: Path) -> Path:
        """game_main, the title menu, a click on "Start Action Game", GAME_VBLS frames, then
        the next loop top runs `nxt`."""
        at_top = self.file(self.then(f"pc=${LOOP_TOP:x}", nxt))
        x, y = ACTION_BUTTON
        release = self.file("w $880 0 0\n" + self.then(f'VBL="VBL+{GAME_VBLS}"', at_top))
        click = self.file(f"w $474 {x >> 8} {x & 255} {y >> 8} {y & 255}\nw $880 1 1\n"
                          + self.then('VBL="VBL+10"', release))
        title = self.file(self.then(f'VBL="VBL+{TITLE_VBLS}"', click))
        return self.file(self.then(f"pc=${GAME_MAIN:x}", title))


def run_hatari(start: Path, out: Path, timeout: int, extra: list, gui: bool) -> None:
    prepare()
    args = ["hatari", *BASE_ARGS, "--fast-forward", "on", "--parse", str(start), *extra]
    if gui:
        subprocess.call(args, stdin=subprocess.DEVNULL, cwd=out)
        return
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    with open(out / "hatari.log", "w") as log:
        try:
            subprocess.run(args[:1] + ["--sound", "off"] + args[1:], stdin=subprocess.DEVNULL, stdout=log,
                           stderr=subprocess.STDOUT, env=env, timeout=timeout, cwd=out)
        except subprocess.TimeoutExpired:
            print(f"timed out after {timeout}s; see {out / 'hatari.log'}", file=sys.stderr)


def play(state: bytes, out: Path, mode: str, redraw: bool, extra: list) -> int:
    """GUI: boot in fast-forward, import, then normal speed with sound."""
    out.mkdir(parents=True, exist_ok=True)
    c = Chain(out)
    load = c.file(load_commands(state, out, mode) + (redraw_command(state) if redraw else "")
                  + "setopt --fast-forward off\n")
    run_hatari(c.boot(load), out, 0, extra, gui=True)
    return 0


def capture(state: bytes, out: Path, mode: str, passes: int, vbls: int, redraw: bool = False,
            timeout: int = 300, extra: list = ()) -> bool:
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.bin"):
        old.unlink()
    c = Chain(out)
    save = lambda name: f"savebin {out / name}.bin 0 $80000\n"
    nxt = c.file(f"screenshot {out / 'screenshot.png'}\n" + save("ram") + "quit\n")
    nxt = c.file(c.then(f'VBL="VBL+{vbls}"', nxt))
    for i in reversed(range(passes)):
        p_out = c.file(save(f"pass_{i}_out") + c.then("VBL>0", nxt))
        p_in = c.file(save(f"pass_{i}_in") + c.then(f"pc=${PASS_END:x}", p_out))
        nxt = c.file(c.then(f"pc=${PASS:x}", p_in))
    load = c.file(save("before") + load_commands(state, out, mode) + save("imported")
                  + (redraw_command(state) if redraw else "")
                  + f"f {out / 'regs.txt'}\nr\n" + c.then("VBL>0", nxt))
    run_hatari(c.boot(load), out, timeout, list(extra), gui=False)
    got = len(list(out.glob("pass_*_out.bin")))
    print(f"{out}: imported, {got} of {passes} passes captured")
    return (out / "imported.bin").exists() and got == passes


# ---- exports made the way the web app makes them ------------------------------------------
# web/src/app/game.ts VBLS_PER_PASS and runner.ts
VBLS_PER_PASS = {0: 6, 1: 4, 2: 4, 3: 3, 4: 3, 8: 7, 9: 7, 10: 3, 11: 3, 12: 5,
                 17: 4, 18: 3, 19: 6, 20: 3, 25: 4, 26: 6, 27: 3, 28: 3}
RUNNING, CLOCK, NEW_SECOND, SCREEN, RNG = 0x462, 0x8F2, 0x8FC, 0x718, 0x1236


class WebRunner:
    """Runner (web/src/app/runner.ts) and Game (game.ts) in Python: the RAM image, the model
    on it, a 50 Hz VBL clock and a whole game_main loop every vbls_per_pass VBLs."""

    def __init__(self, ram: bytes):
        import stunits
        self.ram = bytearray(ram)
        self.state = stunits.UnitState.from_ram(bytes(self.ram))
        self.vpp = VBLS_PER_PASS.get(self.screen, 4)

    @classmethod
    def new_game(cls, title: bytes, strategy: bool, seed: bytes) -> "WebRunner":
        ram = bytearray(title)
        ram[RNG:RNG + 8] = seed
        r = cls(bytes(ram))
        r.state.new_game(strategy)
        r.state.new_game_screen()
        r.state.to_ram(r.ram)
        r.vpp = VBLS_PER_PASS.get(r.screen, 4)
        return r

    @property
    def screen(self) -> int:
        return int.from_bytes(self.ram[SCREEN:SCREEN + 2], "big")

    def vbl(self) -> None:
        ram = self.ram
        if not ram[RUNNING]:
            return
        w = lambda k: int.from_bytes(ram[CLOCK + 2 * k:CLOCK + 2 * k + 2], "big")

        def put(k, v):
            ram[CLOCK + 2 * k:CLOCK + 2 * k + 2] = (v & 0xFFFF).to_bytes(2, "big")
        limits = [0, 24, 60, 60, 50]
        k = 4
        put(k, w(k) + 1)
        while k > 0 and w(k) >= limits[k]:
            put(k, 0)
            k -= 1
            put(k, w(k) + 1)
            if k == 3:
                self.state.mem.set8(NEW_SECOND, 0xFF)
                self.state.mem.set8(NEW_SECOND + 1, 0xFF)

    def step(self) -> None:
        for _ in range(self.vpp):
            self.vbl()
        s = self.state
        s.ui_frame()
        s.game_frame()
        s.render_effects()
        s.frame_flip_flags()
        s.to_ram(self.ram)


# ---- the comparison -----------------------------------------------------------------------
def render_mask() -> bytearray:
    """Bit masks of the state bytes the original's 3D-view renders write and the app's
    render_effects doesn't port (docs/units.md §10.8 "Not ported"): camera object 0, the
    view pointer $9FA, $6FE and the viewport records, the pipeline's island context and
    matrices, island-buffer entries' display fields (+1, +$1E-$29), and the units' visible
    bit (+1 bit 1)."""
    import stsim
    import stunits
    mask = bytearray(RAM_SIZE)

    def full(a, n):
        mask[a:a + n] = b"\xff" * n
    full(stsim.OBJECTS, stsim.OBJECT_SIZE)
    full(0x9FA, 4)
    full(0x6FE, 4)
    full(0x4744, 0x11C)
    full(0xA28, 0x30)
    full(0xA68, 0xC)
    for b in stunits.ISLAND_BUFFERS:
        for k in range(64):
            e = b + 0x12 + 0x40 * k
            full(e + 1, 1)
            full(e + 0x1E, 12)
    for k in range(1, 64):
        mask[stsim.OBJECTS + stsim.OBJECT_SIZE * k + 1] |= 0x02
    return mask


def compare(export: bytes, cap: Path, passes: int, mode: str, sync_render: bool = True,
            redraw: bool = False) -> list:
    """The model continuing from `export`, loop by loop, against Hatari's passes. Taken from
    Hatari: the VBL handler's second flags $8FC/$8FD (at the simulation block's entry, at its
    tick test and after it: the VBLs per loop are the machine's timing, which the app only
    approximates) and, at the block's entry, with
    sync_render, the bytes of render_mask() (the 3D render's writes the app doesn't port;
    without sync_render they are only left out of the comparison, so their effect on the
    simulation shows). Everything else in the state regions must match before and after
    every simulation block. Returns report lines; the first mismatch ends the run."""
    import check_units
    import stunits
    from stunits_controls import END_REQUEST, END_TIMER, MENU
    lines = []
    mask = render_mask()
    synced = set()
    imported = (cap / "imported.bin").read_bytes()
    loaded = spans(mode)
    bad = sum(imported[a:b] != export[a:b] for a, b in loaded)
    lines.append(f"load: {'OK' if not bad else f'{bad} spans differ'} "
                 f"({sum(b - a for a, b in loaded)} bytes loaded at ${LOOP_TOP:X})")

    def render_diffs(mine, ref):
        return [i for i, (x, y) in enumerate(zip(mine, ref)) if x != y and mask[i]]

    s = stunits.UnitState.from_ram(export)
    m = s.mem
    if redraw:
        m.set16(SCREEN_SELECT, int.from_bytes(export[0x718:0x71A], "big"))
    for n in range(passes):
        cin = (cap / f"pass_{n}_in.bin").read_bytes()
        cout = (cap / f"pass_{n}_out.bin").read_bytes()
        s.ui_frame()                          # the loop up to the simulation block
        s.misc_keys()
        if not m.u8(RUNNING) or m.u8(MENU):
            lines.append(f"pass {n}: the game is paused or in the menu; stopped comparing")
            break
        s.debug_keys()
        s.screen_input()
        m.set8(0x8FC, cin[0x8FC])
        m.set8(0x8FD, cin[0x8FD])
        if sync_render:
            mine = bytearray(cin)
            s.to_ram(mine)
            for a in render_diffs(mine, cin):
                v = (m.buf[a] & ~mask[a] | cin[a] & mask[a]) & 0xFF
                if v != m.buf[a]:
                    m.buf[a] = v
                    synced.add(a)
        for label in ("before", "after"):
            ref = cin
            if label == "after":
                # $8FD as the block's tick test ($40EE) saw it: a VBL may set it after $40B0;
                # $8FE (cleared at $40EA, set when the tick runs) says which way the test went
                m.set8(0x8FD, 0xFF if cout[0x8FE] else 0)
                s.sim_pass()
                ref = cout
                if cout[CLOCK:CLOCK + 8] != cin[CLOCK:CLOCK + 8]:   # a new second's VBL in the block
                    m.set8(0x8FC, cout[0x8FC])
                    m.set8(0x8FD, cout[0x8FD])
            mine = bytearray(ref)
            s.to_ram(mine)
            if sync_render:
                for a in render_diffs(mine, ref):                 # not compared
                    mine[a] = (mine[a] & ~mask[a] | ref[a] & mask[a]) & 0xFF
            d = check_units.diff(mine, ref)
            if d:
                lines.append(f"pass {n} ({label} the simulation block): MISMATCH")
                lines += ["  " + x for x in d]
                return lines
        s.render_effects()                    # the rest of the loop
        s.frame_flip_flags()
        if m.u16(END_TIMER) or m.u8(END_REQUEST):
            lines.append(f"pass {n}: end of game; stopped comparing")
            break
    else:
        lines.append(f"passes: OK ({passes} loops, model and Hatari identical over the state regions)")
    if sync_render:
        lines.append(f"render-owned bytes taken from Hatari: {len(synced)}"
                     + (f" (e.g. {', '.join(f'${a:05X}' for a in sorted(synced)[:6])})" if synced else ""))
    return lines


def verify(passes: int, mode: str, redraw: bool, only: list) -> int:
    root = EMU / "out" / "import"
    play_cap = EMU / "out" / "play" / "pass_flight_0_in.bin"
    title = EMU / "out" / "title" / "ram.bin"
    cases = {}
    if play_cap.exists():
        def a():
            r = WebRunner(play_cap.read_bytes())
            for _ in range(50):
                r.step()
            return r.ram
        cases["play_capture_plus_50"] = a
    if title.exists():
        def b(k, strategy=True, seed=bytes.fromhex("2468ACE113579BDF")):
            def make():
                r = WebRunner.new_game(title.read_bytes(), strategy, seed)
                for _ in range(k):
                    r.step()
                return r.ram
            return make
        cases["new_strategy_game"] = b(0)
        cases["new_strategy_game_plus_200"] = b(200)
        cases["new_action_game_plus_20"] = b(20, False, bytes.fromhex("0F1E2D3C8BADF00D"))
    if not cases:
        sys.exit("need emu/out/play (tools/capture_play.py) or emu/out/title (tools/hatari.py snapshot)")
    ok = True
    for name, make in cases.items():
        if only and name not in only:
            continue
        out = (root / f"{name}_{mode}{'_redraw' if redraw else ''}").resolve()
        out.mkdir(parents=True, exist_ok=True)
        export = bytes(make())
        print(f"== {name} ({mode} load{', redraw' if redraw else ''})")
        captured = capture(export, out, mode, passes, 50, redraw)
        (out / "export.bin").write_bytes(export)
        if not captured:
            print("  Hatari capture failed")
            ok = False
            continue
        # the model alone, render_effects included (docs/units.md §10.8): every state byte
        # compared, nothing taken from Hatari but the VBL's second flags
        lines = compare(export, out, passes, mode, sync_render=False, redraw=redraw)
        for line in lines:
            print("  " + line)
        ok &= not any("MISMATCH" in x or "differ" in x for x in lines)
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("play", "capture"):
        sp = sub.add_parser(name)
        sp.add_argument("state", type=Path, help="512 KB RAM image (the web app's Export)")
        sp.add_argument("--load", choices=("full", "regions"), default="full")
        sp.add_argument("--redraw", action=argparse.BooleanOptionalAction, default=name == "play",
                        help="re-enter the state's screen after the load, so its panels are drawn "
                             "(default: on for play)")
        sp.add_argument("--out", type=Path, default=EMU / "out" / ("import_play" if name == "play" else "import"))
    cp = sub.choices["capture"]
    cp.add_argument("--passes", type=int, default=40)
    cp.add_argument("--vbls", type=int, default=50, help="frames after the passes for screenshot.png/ram.bin")
    cp.add_argument("--timeout", type=int, default=300)
    vp = sub.add_parser("verify")
    vp.add_argument("--passes", type=int, default=40)
    vp.add_argument("--load", choices=("full", "regions"), default="full")
    vp.add_argument("--redraw", action="store_true", help="re-enter the screen after the load (as play does)")
    vp.add_argument("--case", action="append", help="run only this case")
    args, extra = ap.parse_known_args()
    if args.cmd == "verify":
        return verify(args.passes, args.load, args.redraw, args.case or [])
    state = args.state.read_bytes()
    if args.cmd == "play":
        return play(state, args.out.resolve(), args.load, args.redraw, extra)
    return 0 if capture(state, args.out.resolve(), args.load, args.passes, args.vbls, args.redraw, args.timeout,
                        extra) else 1


if __name__ == "__main__":
    sys.exit(main())
