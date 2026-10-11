#!/usr/bin/env python3
"""Regenerate every extracted asset from roms/CARRIER.TOS, then run the model cross-check."""
import runpy
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
STEPS = ["split_tos", "extract_palettes", "extract_font", "extract_ui_sprites", "extract_ui_panels",
         "extract_items", "extract_models", "extract_islands", "extract_sounds", "extract_protection"]


def main() -> int:
    sys.path.insert(0, str(TOOLS))
    for step in STEPS:
        print(f"== {step}")
        runpy.run_path(str(TOOLS / f"{step}.py"), run_name="__main__")
    print("== check_models")
    try:
        runpy.run_path(str(TOOLS / "check_models.py"), run_name="__main__")
    except SystemExit as e:
        return int(e.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
