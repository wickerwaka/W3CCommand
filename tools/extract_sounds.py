#!/usr/bin/env python3
"""Render every sound effect to WAV by running the game's own sound engine (stsound.py)
and synthesising the logged YM2149 register writes (ym2149.py).

Sound records are reached through two tables:
  $20BD0  effect table, indexed by the word after `jsr $20B90` at each call site
          (positive long = sound record; 0 = none; negative = routine)
  $20CB4  9 distance-graded variants, chosen by $20C?? from range ($20CA8)
A record is a 6-byte header (read into channel +$26 long and +$2A word) followed by the
channel script (see the opcode list in docs/memory-map.md).
Writes extracted/sounds/*.wav and sounds.txt (record, length, table ids, call sites with the
nearest following message text as a hint to what the sound is). The graded set rises in
level from 0 to 8 (explosions by distance, nearest = 8). The YM2149 model is simplified;
compare against a real ST / emulator recording before relying on exact timbre.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import ROOT, Payload
from stsound import render_sound
from ym2149 import render_log, write_wav

EFFECTS, EFFECT_COUNT = 0x20BD0, 14
GRADED, GRADED_COUNT = 0x20CB4, 9
PLAY_EFFECT = 0x20B90
OUT = ROOT / "extracted" / "sounds"


def records(p: Payload) -> dict:
    recs = {}
    for i in range(EFFECT_COUNT):
        v = p.u32(EFFECTS + 4 * i)
        if v and not v & 0x80000000:
            recs.setdefault(v, []).append(f"effect {i}")
    for i in range(GRADED_COUNT):
        recs.setdefault(p.u32(GRADED + 4 * i), []).append(f"graded {i}")
    return recs


def call_sites(p: Payload) -> dict:
    sites = {}
    for a in range(0x400, 0x22E62, 2):
        if p.u16(a) == 0x4EB9 and p.u32(a + 2) == PLAY_EFFECT:
            sites.setdefault(p.u16(a + 6), []).append(f"${a:05X}")
    return sites


def nearby_text(p: Payload, addr: int, span: int = 0x140) -> str:
    """First printable string (>= 8 chars) after a call site: a hint at what plays it."""
    run = bytearray()
    for a in range(addr, addr + span):
        b = p.data[a - 0x400]
        if 0x20 <= b < 0x7F:
            run.append(b)
        else:
            if len(run) >= 8 and sum(c.isalpha() for c in run.decode()) >= 6:
                return run.decode()
            run.clear()
    return ""


def main() -> None:
    p = Payload()
    OUT.mkdir(parents=True, exist_ok=True)
    sites = call_sites(p)
    lines = []
    recs = records(p)
    for rec, uses in sorted(recs.items()):
        log, length = render_sound(p, rec)
        samples = render_log(log, length + 0.1)
        write_wav(OUT / f"sound_{rec:05X}.wav", samples)
        effect_sites = [s for u in uses if u.startswith("effect") for s in sites.get(int(u.split()[1]), [])]
        lines.append(f"${rec:05X} {length:5.2f}s  {', '.join(uses)}")
        for site in effect_sites:
            hint = nearby_text(p, int(site[1:], 16))
            lines.append(f"    played from {site}" + (f'  near "{hint}"' if hint else ""))
    (OUT / "sounds.txt").write_text("\n".join(lines) + "\n")
    print(f"{len(recs)} sounds -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
