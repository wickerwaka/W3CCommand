#!/usr/bin/env python3
"""Capture world-simulation RAM states from the original game running in Hatari.

Starts a strategy game by poking the menu's mouse position ($474/$476) and left-button
state ($880) through the Hatari debugger, then saves full RAM ($0-$7FFFF):
  warmup_in.bin / warmup_out.bin   at world_warmup ($18474) entry and after it returns
  step_<vbl>_in.bin / _out.bin     at entry to and rts of the first world_sim_step ($1848E)
                                   that starts at least <vbl> frames after the warm-up
  final.png                        screenshot after the last capture
tools/check_sim.py steps the Python model and the game's own code (Unicorn) from each
*_in state and compares both against *_out.

  uv run python tools/capture_sim.py [--steps 3000 30000 120000] [--out emu/out/sim]
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hatari import BASE_ARGS, EMU, GAME_MAIN, prepare

TITLE_VBLS = 1500                   # the title menu is up and accepting clicks
STRATEGY_BUTTON = (152, 51)         # "Start Strategy Game", screen coordinates
WARMUP, WARMUP_RET = 0x18474, 0x18472
SIM_STEP, SIM_STEP_RTS = 0x1848E, 0x18518


def build_scripts(out: Path, steps: list) -> Path:
    """Write the chained debugger scripts; returns the first one. Each script saves its
    state and arms the breakpoint of the next stage, so they are built back to front."""
    def script(name: str, body: str) -> Path:
        path = out / f"{name}.txt"
        path.write_text(body)
        return path

    def save(name: str) -> str:
        return f"savebin {out / name}.bin 0 $80000\n"

    def then(cond: str, nxt: Path) -> str:
        return f"b {cond} :once :trace :file {nxt}\n"

    nxt = script("finish", f"screenshot {out / 'final.png'}\nquit\n")
    prev = [0] + steps
    for i in reversed(range(len(steps))):
        vbl, delta = steps[i], steps[i] - prev[i]
        rts = script(f"step{i}_out", save(f"step_{vbl}_out") + then("VBL>0", nxt))
        entry = script(f"step{i}_in", save(f"step_{vbl}_in") + then(f"pc=${SIM_STEP_RTS:x}", rts))
        arm = script(f"step{i}_arm", then(f"pc=${SIM_STEP:x}", entry))
        nxt = script(f"step{i}_wait", then(f'VBL="VBL+{delta}"', arm))
    warm_out = script("warm_out", save("warmup_out") + then("VBL>0", nxt))
    warm_in = script("warm_in", save("warmup_in") + then(f"pc=${WARMUP_RET:x}", warm_out))
    x, y = STRATEGY_BUTTON
    release = script("release", "w $880 0 0\n")
    click = script("click", f"w $474 {x >> 8} {x & 255} {y >> 8} {y & 255}\nw $880 1 1\nw $889 ff\n"
                            + then('VBL="VBL+10"', release) + then(f"pc=${WARMUP:x}", warm_in))
    title = script("title", then(f'VBL="VBL+{TITLE_VBLS}"', click))
    return script("start", then(f"pc=${GAME_MAIN:x}", title))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", type=int, nargs="*", default=[3000, 30000, 120000],
                    help="frames after the warm-up at which to capture one world_sim_step")
    ap.add_argument("--out", type=Path, default=EMU / "out" / "sim")
    ap.add_argument("--timeout", type=int, default=600)
    args, extra = ap.parse_known_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.bin"):
        old.unlink()
    start = build_scripts(out, sorted(set(args.steps)))
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
    have = sorted(p.name for p in out.glob("*.bin"))
    print(f"{out}: {' '.join(have)}")
    return 0 if (out / "warmup_out.bin").exists() else 1


if __name__ == "__main__":
    sys.exit(main())
