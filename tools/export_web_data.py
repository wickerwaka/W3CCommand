#!/usr/bin/env python3
"""Write the game data the web app starts from: web/public/data/ (git-ignored; it is game
memory, for personal use only).

  title.bin   the 512 KB RAM image at the title screen (1500 VBLs after game_main, as
              `tools/hatari.py snapshot` takes it). The app starts new games from it (the
              payload, its tables and the pristine globals snapshot at $4BCCA are all inside)
              and decodes models, palettes and names from it.

  uv run python tools/export_web_data.py        (or: cd web && npm run data)
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hatari
from stgfx import ROOT

OUT = ROOT / "web" / "public" / "data"
TITLE_VBLS = 1500
RAM_SIZE = 0x80000


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "title"
        if hatari.snapshot(TITLE_VBLS, snap, 120, []) != 0:
            return 1
        ram = (snap / "ram.bin").read_bytes()
    if len(ram) != RAM_SIZE or ram[0x404:0x418] != b"Atari Bundle Version":
        sys.exit("unexpected title snapshot (no game payload at $400)")
    (OUT / "title.bin").write_bytes(ram)
    print(f"{OUT / 'title.bin'}")
    return 0


if __name__ == "__main__":
    if shutil.which("hatari") is None:
        sys.exit("hatari not found (brew install hatari): it boots the game to take the title snapshot")
    sys.exit(main())
