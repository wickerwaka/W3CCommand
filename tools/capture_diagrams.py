#!/usr/bin/env python3
"""Capture passes with the two model-list 3D pictures up, for tools/stview.py --check: the
damage screen's carrier diagram (screen 1, render $156FA) and the title's turning carrier
(screen 33, render $15DA2).

The same scripted Hatari session as tools/capture_views.py (an action game; screens are
switched by a forced request, clicks are poked pointer + button), but every capture is taken
at $413E, the end of game_main's simulation block, right before screen_dispatch ($14472) runs
the render: pass_<label>_<n>_in.bin is exactly the RAM the render sees, and consecutive
captures are consecutive passes (the chain arms $14472, then the next $413E). That also works
on the title, where the simulation block ($40B0) is skipped ([$461] set). shot_<label>.png is
a screenshot a frame before the first capture.

Steps: the damage screen with no system shown, system 3 (whole, then damaged), the lift
(8, its parts highlighted in turn), the superstructure (9), system 0 damaged and down; then
the title by a forced request for tab 5.

  uv run python tools/capture_diagrams.py [--out emu/out/diagrams]
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_play as cp
from capture_sim import ACTION_BUTTON, PASS_END, TITLE_VBLS
from hatari import BASE_ARGS, EMU, GAME_MAIN, prepare

DISPATCH = 0x14472                     # screen_dispatch, called right after $413E
cp.SCREENS.update({"damage": (1, 1), "title": (33, 5)})
for _k in range(10):                   # the system buttons, types 48-57 at (13 + 28k, 155), box 2..24
    cp.BUTTONS[f"system_{_k}"] = (25 + 28 * _k, 167)
SYSTEM = 0xAF4                         # 10 x (current.w, max.w, class.w, status.w)
SCRIPT = [
    ("wait", 600),
    ("screen", "damage"), ("wait", 40), ("capture", ("damage", 3)),
    ("click", "system_3"), ("wait", 20), ("capture", ("damagethree", 3)),
    ("poke", f"${SYSTEM + 8 * 3:x} 0 100"), ("wait", 10), ("capture", ("damagethreehit", 3)),
    ("click", "system_8"), ("wait", 20), ("capture", ("damagelift", 12)),
    ("click", "system_9"), ("wait", 20), ("capture", ("damagehull", 3)),
    ("poke", f"${SYSTEM:x} 0 50"), ("poke", f"${SYSTEM + 6:x} 0 0"),
    ("click", "system_0"), ("wait", 6), ("capture", ("damageengines", 3)),
    ("screen", "title"), ("wait", 100), ("capture", ("title", 6)),
]


def build_scripts(out: Path, script: list) -> Path:
    """capture_play.build_scripts with the captures taken at $413E (see the docstring)."""
    n = 0

    def script_file(body: str) -> Path:
        nonlocal n
        n += 1
        path = out / f"s{n:03d}.txt"
        path.write_text(body)
        return path

    def then(cond: str, nxt: Path) -> str:
        return f"b {cond} :once :trace :file {nxt}\n"

    nxt = script_file(f"screenshot {out / 'final.png'}\nquit\n")
    for step, arg in reversed(script):
        if step == "wait":
            nxt = script_file(then(f'VBL="VBL+{arg}"', nxt))
        elif step == "capture":
            label, count = arg
            follow = script_file(then("VBL>0", nxt))
            for i in reversed(range(count)):
                save = script_file(f"savebin {out / f'pass_{label}_{i}_in'}.bin 0 $80000\n"
                                   + then(f"pc=${DISPATCH:x}", follow))
                follow = script_file(then(f"pc=${PASS_END:x}", save))
            nxt = script_file(f"screenshot {out / ('shot_' + label + '.png')}\n" + then("VBL>0", follow))
        elif step == "screen":
            screen, tab = cp.SCREENS[arg]
            nxt = script_file(f"w ${0x720 + 2 * tab:x} 0 {screen}\nw $1228 0 {tab}\nw $1227 $ff\n"
                              + then("VBL>0", nxt))
        elif step == "click":
            x, y = cp.BUTTONS[arg]
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
    ap.add_argument("--out", type=Path, default=EMU / "out" / "diagrams")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("*.bin")) + list(out.glob("s*.txt")):
        old.unlink()
    start = build_scripts(out, SCRIPT)
    prepare()
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    with open(out / "hatari.log", "w") as log:
        try:
            subprocess.run(["hatari", *BASE_ARGS, "--sound", "off", "--fast-forward", "on", "--parse", str(start)],
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
                           timeout=args.timeout, cwd=out)
        except subprocess.TimeoutExpired:
            print(f"timed out after {args.timeout}s; see {out / 'hatari.log'}", file=sys.stderr)
    passes = len(list(out.glob("pass_*_in.bin")))
    print(f"{out}: {passes} passes captured")
    return 0 if passes else 1


if __name__ == "__main__":
    sys.exit(main())
