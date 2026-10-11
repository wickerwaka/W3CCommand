#!/usr/bin/env python3
"""Capture world-simulation RAM states from the original game running in Hatari.

Starts a strategy game (or with --action, an action game, which needs no further input:
the carrier sails to a waypoint off an enemy island and stops) by poking the menu's mouse
position ($474/$476) and left-button state ($880) through the Hatari debugger, then saves
full RAM ($0-$7FFFF):
  warmup_in.bin / warmup_out.bin   at world_warmup ($18474) entry and after it returns
  step_<vbl>_in.bin / _out.bin     at entry to and rts of the first world_sim_step ($1848E)
                                   that starts at least <vbl> frames after the warm-up
  tick_<vbl>_<n>_in.bin / _out.bin the next --ticks logistics ticks ($F7A0 to its rts $F842),
  ai_<vbl>_<n>_in.bin / _out.bin   each followed by one enemy AI call ($1AFAA to $4122)
  pass_<vbl>_<n>_in.bin / _out.bin then --passes consecutive game_main simulation passes
                                   ($40B0 to $413E: everything but input/UI and rendering)
  final.png                        screenshot after the last capture
tools/check_sim.py steps the Python model and the game's own code (Unicorn) from each
*_in state and compares both against *_out.

  uv run python tools/capture_sim.py [--steps 3000 30000 120000] [--ticks 8] [--passes 0] [--out emu/out/sim]
  uv run python tools/capture_sim.py --action --steps 20 300 1500 --ticks 0 --passes 40 --out emu/out/action
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
ACTION_BUTTON = (152, 66)           # "Start Action Game"
PASS, PASS_END = 0x40B0, 0x413E     # game_main's simulation block, up to the render
WARMUP, WARMUP_RET = 0x18474, 0x18472
SIM_STEP, SIM_STEP_RTS = 0x1848E, 0x18518
TICK, TICK_RTS = 0xF7A0, 0xF842
ENEMY_AI, ENEMY_AI_RET = 0x1AFAA, 0x4122


def build_scripts(out: Path, steps: list, ticks: int, passes: int = 0, button=STRATEGY_BUTTON) -> Path:
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
        for n in reversed(range(passes)):
            p_out = script(f"pass{i}_{n}_out", save(f"pass_{vbl}_{n}_out") + then("VBL>0", nxt))
            p_in = script(f"pass{i}_{n}_in", save(f"pass_{vbl}_{n}_in") + then(f"pc=${PASS_END:x}", p_out))
            nxt = script(f"pass{i}_{n}_arm", then(f"pc=${PASS:x}", p_in))
        for n in reversed(range(ticks)):
            a_out = script(f"ai{i}_{n}_out", save(f"ai_{vbl}_{n}_out") + then("VBL>0", nxt))
            a_in = script(f"ai{i}_{n}_in", save(f"ai_{vbl}_{n}_in") + then(f"pc=${ENEMY_AI_RET:x}", a_out))
            nxt = script(f"ai{i}_{n}_arm", then(f"pc=${ENEMY_AI:x}", a_in))
            t_out = script(f"tick{i}_{n}_out", save(f"tick_{vbl}_{n}_out") + then("VBL>0", nxt))
            t_in = script(f"tick{i}_{n}_in", save(f"tick_{vbl}_{n}_in") + then(f"pc=${TICK_RTS:x}", t_out))
            nxt = script(f"tick{i}_{n}_arm", then(f"pc=${TICK:x}", t_in))
        rts = script(f"step{i}_out", save(f"step_{vbl}_out") + then("VBL>0", nxt))
        entry = script(f"step{i}_in", save(f"step_{vbl}_in") + then(f"pc=${SIM_STEP_RTS:x}", rts))
        arm = script(f"step{i}_arm", then(f"pc=${SIM_STEP:x}", entry))
        nxt = script(f"step{i}_wait", then(f'VBL="VBL+{delta}"', arm))
    warm_out = script("warm_out", save("warmup_out") + then("VBL>0", nxt))
    warm_in = script("warm_in", save("warmup_in") + then(f"pc=${WARMUP_RET:x}", warm_out))
    x, y = button
    release = script("release", "w $880 0 0\n")
    click = script("click", f"w $474 {x >> 8} {x & 255} {y >> 8} {y & 255}\nw $880 1 1\n"
                            + then('VBL="VBL+10"', release) + then(f"pc=${WARMUP:x}", warm_in))
    title = script("title", then(f'VBL="VBL+{TITLE_VBLS}"', click))
    return script("start", then(f"pc=${GAME_MAIN:x}", title))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", type=int, nargs="*", default=[3000, 30000, 120000],
                    help="frames after the warm-up at which to capture one world_sim_step")
    ap.add_argument("--ticks", type=int, default=8, help="consecutive logistics ticks after each step")
    ap.add_argument("--passes", type=int, default=0, help="consecutive simulation passes after the ticks")
    ap.add_argument("--action", action="store_true", help="start an action game instead")
    ap.add_argument("--out", type=Path, default=EMU / "out" / "sim")
    ap.add_argument("--timeout", type=int, default=600)
    args, extra = ap.parse_known_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.bin"):
        old.unlink()
    start = build_scripts(out, sorted(set(args.steps)), args.ticks, args.passes,
                          ACTION_BUTTON if args.action else STRATEGY_BUTTON)
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
