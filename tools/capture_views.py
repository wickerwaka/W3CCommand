#!/usr/bin/env python3
"""Capture passes with each vehicle and weapon 3D screen up, for tools/stview.py --check and
the Manta lock-on check (stunits_vehicles.check_lock_on).

The same scripted Hatari session as tools/capture_play.py (clicks are poked pointer + button,
captures are pass_<label>_<n>_in/out.bin plus shot_<label>.png): Manta 1 to the deck (on the
lift, on deck, taking off), Manta control flying for island 27 (it is shot down later), the
Walrus dock (docked, launching), Walrus control, the laser turret, flares (one launched) and
the viewing drone (launched). Leave a few frames between a capture and a click: a click
straight after a capture is lost.

  uv run python tools/capture_views.py [--out emu/out/views]
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_play as cp
from hatari import BASE_ARGS, EMU, prepare

cp.SCREENS.update({"turret": (8, 2), "flares": (9, 2), "drone": (10, 2)})
cp.BUTTONS.update({"flare": (176, 164), "recon_drone": (220, 164)})    # elements $435AC, $43558
I27 = cp.ISLAND_27
SCRIPT = [
    ("wait", 2500),
    ("screen", "manta_deck"), ("wait", 60), ("click", "manta_select_1"), ("wait", 30),
    ("click", "manta_to_deck"), ("wait", 300),
    ("capture", ("decklift", 4)),
    ("wait", 1200), ("capture", ("deck", 4)), ("wait", 20),
    ("click", "manta_take_off"), ("wait", 150),
    ("capture", ("decktakeoff", 4)),
    ("poke", f"$4b4 {I27[0]}"), ("poke", f"$4bc {I27[1]}"),
    ("screen", "manta_control"), ("wait", 400), ("capture", ("manta", 8)),
    ("wait", 800), ("capture", ("mantanear", 8)),
    ("screen", "walrus_dock"), ("wait", 60), ("click", "walrus_select_1"), ("wait", 30),
    ("capture", ("dock", 4)), ("wait", 20),
    ("click", "walrus_launch"), ("wait", 200), ("capture", ("docklaunch", 4)),
    ("poke", f"$58c {I27[0]}"), ("poke", f"$590 {I27[1]}"),
    ("screen", "walrus_control"), ("wait", 300), ("capture", ("walrus", 8)),
    ("wait", 20), ("screen", "turret"), ("wait", 60), ("capture", ("turret", 4)),
    ("screen", "flares"), ("wait", 30), ("click", "flare"), ("wait", 20), ("capture", ("flares", 4)),
    ("screen", "drone"), ("wait", 30), ("click", "recon_drone"), ("wait", 100), ("capture", ("drone", 4)),
    ("wait", 300), ("capture", ("dronelate", 4)),
    ("screen", "manta_control"), ("wait", 200), ("capture", ("mantalate", 8)),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=EMU / "out" / "views")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("*.bin")) + list(out.glob("s*.txt")):
        old.unlink()
    start = cp.build_scripts(out, SCRIPT)
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
