#!/usr/bin/env python3
"""Capture simulation passes from a scripted play session in Hatari.

Starts an action game (no input needed to get going), then plays a script of steps: switch
screens, click panel buttons (the pointer position and left button are poked, exactly as a
real click arrives), poke state, wait, and capture runs of consecutive simulation passes
(pass_<label>_<n>_in/out.bin, $40B0 to $413E, as tools/capture_sim.py does). Clicks happen
outside the captured passes, so the model (which leaves out the UI handlers) can be compared
pass by pass: uv run python tools/check_units.py --dir emu/out/play ...

  uv run python tools/capture_play.py [--out emu/out/play]
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_sim import ACTION_BUTTON, PASS, PASS_END, TITLE_VBLS
from hatari import BASE_ARGS, EMU, GAME_MAIN, prepare

# Screen ids and their tab ($720 + 2 * tab holds the screen shown in each tab).
SCREENS = {"bridge": (0, 1), "resources": (3, 1), "walrus_dock": (19, 3), "walrus_control": (18, 3),
           "manta_deck": (26, 4), "manta_control": (27, 4)}
# Button centres (element position + hit box) from the panel lists (docs/units.md §10).
BUTTONS = {"manta_to_deck": (66, 187), "manta_take_off": (110, 164), "manta_land": (22, 187),
           "walrus_launch": (110, 164), "walrus_dock": (22, 187), "supply_transfer": (42, 175),
           "manta_select_1": (21, 164), "walrus_select_1": (21, 164)}

# (step, argument): wait N frames, screen NAME, click BUTTON, poke "addr bytes...",
# capture LABEL (N passes).
ISLAND_27 = "$ff $e6 $66 $00", "$ff $e8 $00 $00"     # THERMOPYLAE's x, z (longs)
SCRIPT = [
    ("wait", 2500),                       # the carrier reaches its waypoint off island 27
    ("capture", ("quiet", 20)),
    # Manta 1: hangar -> lift -> deck, then take off and fly over the island.
    ("screen", "manta_deck"), ("wait", 60), ("click", "manta_select_1"), ("wait", 30),
    ("click", "manta_to_deck"), ("wait", 1500),
    ("click", "manta_take_off"), ("wait", 100),
    ("capture", ("takeoff", 40)),
    ("poke", f"$4b4 {ISLAND_27[0]}"), ("poke", f"$4bc {ISLAND_27[1]}"),
    # Walrus 1: launch from the dock and drive for the island's shore.
    ("screen", "walrus_dock"), ("wait", 60), ("click", "walrus_select_1"), ("wait", 30),
    ("click", "walrus_launch"), ("wait", 200),
    ("capture", ("launch", 40)),
    ("poke", f"$58c {ISLAND_27[0]}"), ("poke", f"$590 {ISLAND_27[1]}"),
    # Supply: make island 27 the stockpile, queue 4 Assassins, and send the drone.
    ("poke", "$d4a 0 27"), ("poke", "$d96 0 4"),
    ("screen", "resources"), ("wait", 60), ("click", "supply_transfer"), ("wait", 100),
    ("capture", ("supply", 40)),
    ("screen", "bridge"), ("wait", 1500),
    ("capture", ("flight", 40)),
    ("wait", 3000),
    ("capture", ("combat", 60)),
    ("wait", 6000),
    ("capture", ("late", 60))]


def build_scripts(out: Path, script: list) -> Path:
    """Chained debugger scripts, built back to front (each arms the next stage)."""
    n = 0

    def script_file(body: str) -> Path:
        nonlocal n
        n += 1
        path = out / f"s{n:03d}.txt"
        path.write_text(body)
        return path

    def then(cond: str, nxt: Path) -> str:
        return f"b {cond} :once :trace :file {nxt}\n"

    def save(name: str) -> str:
        return f"savebin {out / name}.bin 0 $80000\n"

    nxt = script_file(f"screenshot {out / 'final.png'}\nquit\n")
    for step, arg in reversed(script):
        if step == "wait":
            nxt = script_file(then(f'VBL="VBL+{arg}"', nxt))
        elif step == "capture":
            label, count = arg
            for i in reversed(range(count)):
                p_out = script_file(save(f"pass_{label}_{i}_out") + then("VBL>0", nxt))
                p_in = script_file(save(f"pass_{label}_{i}_in") + then(f"pc=${PASS_END:x}", p_out))
                nxt = script_file(then(f"pc=${PASS:x}", p_in))
            nxt = script_file(f"screenshot {out / ('shot_' + label + '.png')}\n" + then("VBL>0", nxt))
        elif step == "screen":
            screen, tab = SCREENS[arg]
            nxt = script_file(f"w ${0x720 + 2 * tab:x} 0 {screen}\nw $1228 0 {tab}\nw $1227 $ff\n"
                              + then("VBL>0", nxt))
        elif step == "click":
            x, y = BUTTONS[arg]
            release = script_file("w $880 0 0\n" + then("VBL>0", nxt))
            nxt = script_file(f"w $474 {x >> 8} {x & 255} {y >> 8} {y & 255}\nw $457 $ff\nw $880 1 1\n"
                              + then('VBL="VBL+6"', release))
        elif step == "poke":
            nxt = script_file(f"w {arg}\n" + then("VBL>0", nxt))
    x, y = ACTION_BUTTON
    release = script_file("w $880 0 0\n" + then('VBL="VBL+300"', nxt))
    click = script_file(f"w $474 {x >> 8} {x & 255} {y >> 8} {y & 255}\nw $880 1 1\n"
                        + then('VBL="VBL+10"', release))
    title = script_file(then(f'VBL="VBL+{TITLE_VBLS}"', click))
    return script_file(then(f"pc=${GAME_MAIN:x}", title))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=EMU / "out" / "play")
    ap.add_argument("--timeout", type=int, default=900)
    args, extra = ap.parse_known_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("*.bin")) + list(out.glob("s*.txt")):
        old.unlink()
    start = build_scripts(out, SCRIPT)
    prepare()
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    with open(out / "hatari.log", "w") as log:
        try:
            subprocess.run(["hatari", *BASE_ARGS, "--sound", "off", "--fast-forward", "on",
                            "--parse", str(start), *extra],
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
                           timeout=args.timeout, cwd=out)
        except subprocess.TimeoutExpired:
            print(f"timed out after {args.timeout}s; see {out / 'hatari.log'}", file=sys.stderr)
    passes = len(list(out.glob("pass_*_in.bin")))
    print(f"{out}: {passes} passes captured")
    return 0 if passes else 1


if __name__ == "__main__":
    sys.exit(main())
