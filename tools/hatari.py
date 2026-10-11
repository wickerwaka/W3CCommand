#!/usr/bin/env python3
"""Run CARRIER.TOS in Hatari (brew install hatari), interactively or headless.

The game is auto-started from an emulated GEMDOS drive (emu/hd, built from roms/) on a
512 KB ST with Hatari's bundled EmuTOS. The game takes over the machine at game_main ($405A),
so TOS only matters for loading it.

  uv run python tools/hatari.py play                 # GUI with sound
  uv run python tools/hatari.py snapshot --vbls 1500 --out emu/out/title
      headless: when game_main is reached, run N more VBLs, then save
      screenshot.png, ram.bin (0..$80000), regs.txt, and quit. Prints the output dir.
  uv run python tools/hatari.py snapshot --vbls 0 ...  # state at game_main itself

Debugger scripts use breakpoint option ':trace :file f' (run commands without stopping) and
stdin is /dev/null, so a stray debugger prompt cannot hang a headless run.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EMU = ROOT / "emu"
HD = EMU / "hd"
GAME_MAIN = 0x405A
BASE_ARGS = ["--machine", "st", "--memsize", "0", "--harddrive", str(HD),
             "--auto", "C:\\CARRIER.TOS", "--confirm-quit", "off"]


def prepare() -> None:
    if shutil.which("hatari") is None:
        sys.exit("hatari not found; install it with: brew install hatari")
    HD.mkdir(parents=True, exist_ok=True)
    src = ROOT / "roms" / "CARRIER.TOS"
    if not (HD / "CARRIER.TOS").exists() or (HD / "CARRIER.TOS").read_bytes() != src.read_bytes():
        shutil.copyfile(src, HD / "CARRIER.TOS")


def play(extra: list) -> int:
    prepare()
    return subprocess.call(["hatari", *BASE_ARGS, *extra])


def snapshot(vbls: int, out: Path, timeout: int, extra: list) -> int:
    prepare()
    out.mkdir(parents=True, exist_ok=True)
    final = out / "final.txt"
    final.write_text(f"screenshot {out / 'screenshot.png'}\n"
                     f"savebin {out / 'ram.bin'} 0 $80000\n"
                     f"f {out / 'regs.txt'}\nr\nquit\n")
    entry = out / "entry.txt"
    if vbls:
        entry.write_text(f'b VBL="VBL+{vbls}" :once :trace :file {final}\n')
    else:
        entry = final
    start = out / "start.txt"
    start.write_text(f"b pc=${GAME_MAIN:x} :once :trace :file {entry}\n")
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    with open(out / "hatari.log", "w") as log:
        try:
            subprocess.run(["hatari", *BASE_ARGS, "--sound", "off", "--fast-forward", "on",
                            "--parse", str(start), *extra],
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
                           timeout=timeout, cwd=out)
        except subprocess.TimeoutExpired:
            print(f"timed out after {timeout}s; see {out / 'hatari.log'}", file=sys.stderr)
            return 1
    ok = (out / "ram.bin").exists() and (out / "screenshot.png").exists()
    print(out if ok else f"snapshot failed; see {out / 'hatari.log'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("play")
    sp = sub.add_parser("snapshot")
    sp.add_argument("--vbls", type=int, default=1500, help="VBLs to run after game_main (50/s)")
    sp.add_argument("--out", type=Path, default=EMU / "out" / "snapshot")
    sp.add_argument("--timeout", type=int, default=120)
    args, extra = ap.parse_known_args()
    if args.cmd == "play":
        return play(extra)
    return snapshot(args.vbls, args.out.resolve(), args.timeout, extra)


if __name__ == "__main__":
    sys.exit(main())
