#!/usr/bin/env python3
"""Extract the masked UI sprites drawn through sprite placement tables.

$F0BE draws tables of 32-px sprites (via $B316), $F0D2 tables of 16-px sprites
(via $B292). Tables are found from the `lea table,A1` before each call to either
drawer; a structural scan reports plausible tables that no caller claims. Writes,
under extracted/ui/:
  tables/<addr>.png       each table composited on a 320x200 screen
  sprites/<addr>_<w>.png  each unique sprite bitmap (RGBA, transparent where masked)
  index.txt               table -> records listing
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import ROOT, Payload, masked_sprite, screen, sprite_table, st_palette, to_image

DRAWERS = {0xF0BE: 32, 0xF0D2: 16}
DATA_START = 0x22E62
DEFAULT_PALETTE = 0x132A
OUT = ROOT / "extracted" / "ui"


def table_length(p: Payload, a: int, width: int = 16) -> int:
    """Number of records if `a` starts a plausible sprite table, else 0."""
    end = 0x400 + len(p.data)
    n = 0
    while a + 2 <= end:
        if p.s16(a) < 0:
            return n
        if a + 10 > end:
            return 0
        x, y, rows, ptr = p.u16(a), p.u16(a + 2), p.u16(a + 4) + 1, p.u32(a + 6)
        if not (x < 320 and y < 200 and rows <= 200 and DATA_START <= ptr <= end - rows * width * 10 // 16):
            return 0
        n += 1
        a += 10
    return 0


def tables_from_callers(p: Payload) -> dict:
    """table address -> sprite width."""
    found = {}
    for a in range(0x400, DATA_START, 2):
        op = p.u16(a)
        if op in (0x6100, 0x6000):
            target = a + 2 + p.s16(a + 2)
        elif op in (0x4EB9, 0x4EF9):
            target = p.u32(a + 2)
        else:
            continue
        if target not in DRAWERS:
            continue
        for back in range(2, 24, 2):
            b = a - back
            if p.u16(b) == 0x43FA:      # lea d16(pc),a1
                t = b + 2 + p.s16(b + 2)
            elif p.u16(b) == 0x43F9:    # lea abs.l,a1
                t = p.u32(b + 2)
            else:
                continue
            if table_length(p, t, DRAWERS[target]):
                found[t] = DRAWERS[target]
            break
    return found


def unclaimed_tables(p: Payload, claimed: dict) -> list:
    lengths = {a: n for a in range(0x400, 0x400 + len(p.data) - 10, 2) if (n := table_length(p, a)) >= 2}
    heads = [a for a, n in lengths.items()
             if not any(b < a and (a - b) % 10 == 0 and b + 10 * m > a for b, m in lengths.items())]
    return [a for a in heads
            if not any(t <= a < t + 10 * table_length(p, t, w) + 2 for t, w in claimed.items())]


def main() -> None:
    p = Payload()
    palette = st_palette(p, DEFAULT_PALETTE)
    tables = tables_from_callers(p)
    (OUT / "tables").mkdir(parents=True, exist_ok=True)
    (OUT / "sprites").mkdir(parents=True, exist_ok=True)

    sprites = {}
    lines = []
    for t, width in sorted(tables.items()):
        scr = screen()
        lines.append(f"table ${t:05X} width={width}")
        for x, y, rows, ptr in sprite_table(p, t):
            scr.alpha_composite(to_image(masked_sprite(p, ptr, rows, width), palette), (x, y))
            key = (ptr, width)
            sprites[key] = max(sprites.get(key, 0), rows)
            lines.append(f"  x={x:3d} y={y:3d} rows={rows:3d} sprite=${ptr:05X}")
        scr.save(OUT / "tables" / f"{t:05X}.png")

    for (ptr, width), rows in sorted(sprites.items()):
        to_image(masked_sprite(p, ptr, rows, width), palette).save(OUT / "sprites" / f"{ptr:05X}_{width}.png")

    (OUT / "index.txt").write_text("\n".join(lines) + "\n")
    print(f"{len(tables)} tables, {len(sprites)} unique sprites -> {OUT.relative_to(ROOT)}")
    for a in unclaimed_tables(p, tables):
        print(f"warning: plausible table ${a:05X} has no recognised caller")


if __name__ == "__main__":
    main()
