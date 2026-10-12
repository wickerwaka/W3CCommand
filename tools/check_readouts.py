#!/usr/bin/env python3
"""Check the screen read helpers (web/src/app/readout_screens.ts, docs/units.md section 10.7)
against what the original draws.

Each stscreens_* module is the Python mirror of a group of TS helpers: structured readers with
the same meaning, plus the exact strings (and bars, markers, lines) the original draws from
them. The check runs the screen's own render routine and panel callbacks under Unicorn
(stscreens.Tracer, which hooks the 5x5, 8x8 and vector text routines and, per module, the
bar, rectangle, line and icon primitives) on captures and perturbed states, and compares:

  resources  screen 3, sub-pages 0-5 (stscreens_resources)
  misc       screens 4 messages, 11 defence radar, 12 weapons status, 32 game status, 34 game
             over (stscreens_misc)
  vehicles   screens 16/24 arming and 20/28 status (stscreens_vehicles)
  map        screens 2/17/25: islands, labels, links, unit markers, info box, panels, sliders
             (stscreens_map)

  uv run python tools/check_readouts.py [--fuzz N] [group ...]

It must print no MISMATCH. Takes about a minute with the default fuzz.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stscreens_map
import stscreens_misc
import stscreens_resources
import stscreens_vehicles


def resources(fuzz):
    ok, bad = stscreens_resources.check(fuzz=fuzz)
    print(f"screen 3 resources: {ok} sub-page draws OK, {len(bad)} MISMATCH")
    for b in bad[:5]:
        print("MISMATCH", b[0], b[1], "sub", b[2])
        print("  got ", b[3])
        print("  want", b[4])
    return not bad


GROUPS = {
    "resources": (resources, 12),
    "misc": (lambda n: stscreens_misc.check(fuzz=n), 40),
    "vehicles": (lambda n: stscreens_vehicles.check(nfuzz=n), 30),
    "map": (lambda n: stscreens_map.check(fuzz=n), 30),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("groups", nargs="*", help=f"any of {', '.join(GROUPS)} (default all)")
    ap.add_argument("--fuzz", type=int, help="perturbed states per capture (default per group)")
    a = ap.parse_args()
    for g in a.groups:
        if g not in GROUPS:
            ap.error(f"unknown group {g}")
    good = True
    for name in a.groups or GROUPS:
        fn, n = GROUPS[name]
        print(f"== {name}")
        good &= bool(fn(a.fuzz if a.fuzz is not None else n))
    print("all OK" if good else "MISMATCH")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
