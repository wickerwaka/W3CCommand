#!/usr/bin/env python3
"""Extract the three in-game fonts. Writes, under extracted/font/:

font_5x5.png/.json   Small HUD/message font (renderer $9A56..$9B78, data $4B8A8).
                     Characters '!' ($21)..'Z' ($5A), 5 bytes each (one per row), pixels in
                     bits 4..0 (bit 4 leftmost), 6 px advance. Lower case is folded to upper
                     case before drawing (message log, $A206).
font_8x8.png/.json   Screen-text font (renderer $9C8A, data $4B9CA). ASCII $20..$7F, 8 bytes
                     per glyph (one per row, bit 7 leftmost), 8 px cells.
font_vector.json     Scalable stroke font (renderer $9CEE/$9D6E, offset table $9DF0).
                     Characters $20..$5A. Glyph = turtle commands, one byte each:
                       high nibble 0..7: move up, down, left, right, up-right, down-right,
                                         down-left, up-left
                       low bits 0..2:    length - 1;  bit 3: pen up (move without drawing)
                       high nibble 8..14: no-op;  $Fx: end of glyph
                     then a width byte; advance = width * scale / 256 + 1. Unit = 1 px at
                     scale 256. Exported as polylines starting from the pen origin (0, 0).
font_vector.png      Preview of the vector font.
sample.png           Text rendered with each font.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import ROOT, Payload

OUT = ROOT / "extracted" / "font"

BITMAP_FONTS = {
    # name: (address, first char, last char, rows, width, cell width)
    "font_5x5": (0x4B8A8, 0x21, 0x5A, 5, 5, 6),
    "font_8x8": (0x4B9CA, 0x20, 0x7F, 8, 8, 8),
}

VECTOR_TABLE = 0x9DF0
VECTOR_FIRST = 0x20
DIRECTIONS = [(0, -1), (0, 1), (-1, 0), (1, 0), (1, -1), (1, 1), (-1, 1), (-1, -1)]


def bitmap_glyph(p: Payload, font: str, ch: int) -> list:
    addr, first, _, rows, width, _ = BITMAP_FONTS[font]
    data = p.bytes(addr + (ch - first) * rows, rows)
    return [[(r >> (width - 1 - x)) & 1 for x in range(width)] for r in data]


def export_bitmap_font(p: Payload, name: str) -> None:
    addr, first, last, rows, width, cell = BITMAP_FONTS[name]
    chars = list(range(first, last + 1))
    cols = 16
    atlas = Image.new("RGBA", (cols * cell, ((len(chars) + cols - 1) // cols) * (rows + 1)), (0, 0, 0, 0))
    meta = {"source": f"CARRIER.TOS ${addr:05X}", "glyph_width": width, "glyph_height": rows,
            "advance": cell, "cell": [cell, rows + 1], "glyphs": {}}
    for i, ch in enumerate(chars):
        cx, cy = (i % cols) * cell, (i // cols) * (rows + 1)
        for y, row in enumerate(bitmap_glyph(p, name, ch)):
            for x, bit in enumerate(row):
                if bit:
                    atlas.putpixel((cx + x, cy + y), (255, 255, 255, 255))
        meta["glyphs"][chr(ch)] = {"x": cx, "y": cy}
    atlas.save(OUT / f"{name}.png")
    (OUT / f"{name}.json").write_text(json.dumps(meta, indent=1) + "\n")


def vector_count(p: Payload) -> int:
    # The offset table ends where the first glyph's data begins.
    return p.s16(VECTOR_TABLE) // 2


def vector_glyph(p: Payload, ch: int) -> tuple:
    """Returns (polylines, width) in pen units for character `ch`."""
    a = VECTOR_TABLE + p.s16(VECTOR_TABLE + 2 * (ch - VECTOR_FIRST))
    x = y = 0
    lines, current = [], None
    while True:
        b = p.bytes(a, 1)[0]
        a += 1
        cmd = b >> 4
        if cmd == 0xF:
            break
        if cmd >= 8:
            continue
        dx, dy = DIRECTIONS[cmd]
        n = (b & 7) + 1
        nx, ny = x + dx * n, y + dy * n
        if b & 8:
            current = None
        else:
            if current is None:
                current = [[x, y]]
                lines.append(current)
            current.append([nx, ny])
        x, y = nx, ny
    return lines, p.bytes(a, 1)[0]


def export_vector_font(p: Payload) -> dict:
    glyphs = {}
    for ch in range(VECTOR_FIRST, VECTOR_FIRST + vector_count(p)):
        lines, width = vector_glyph(p, ch)
        glyphs[chr(ch)] = {"width": width, "lines": lines}
    meta = {"source": f"CARRIER.TOS offset table ${VECTOR_TABLE:05X}",
            "advance": "width * scale / 256 + 1 (unit = 1 px at scale 256)", "glyphs": glyphs}
    (OUT / "font_vector.json").write_text(json.dumps(meta) + "\n")

    s, cols = 6, 16
    cell = 12 * s
    img = Image.new("RGB", (cols * cell, ((len(glyphs) + cols - 1) // cols) * cell), (40, 0, 40))
    draw = ImageDraw.Draw(img)
    for i, g in enumerate(glyphs.values()):
        ox, oy = (i % cols) * cell + s * 2, (i // cols) * cell + s * 2
        for line in g["lines"]:
            draw.line([(ox + x * s, oy + y * s) for x, y in line], fill=(255, 255, 0), width=2)
    img.save(OUT / "font_vector.png")
    return glyphs


def main() -> None:
    p = Payload()
    OUT.mkdir(parents=True, exist_ok=True)
    for name in BITMAP_FONTS:
        export_bitmap_font(p, name)
    vector = export_vector_font(p)

    text_upper = "CARRIER COMMAND 0123456789 !?:.,-+()/"
    text_mixed = "Carrier Command: Set Priorities 1988"
    sample = Image.new("RGB", (320, 48), (40, 0, 40))
    for i, ch in enumerate(text_upper):
        if 0x21 <= ord(ch) <= 0x5A:
            for y, row in enumerate(bitmap_glyph(p, "font_5x5", ord(ch))):
                for x, bit in enumerate(row):
                    if bit:
                        sample.putpixel((2 + i * 6 + x, 2 + y), (255, 255, 0))
    for i, ch in enumerate(text_mixed):
        for y, row in enumerate(bitmap_glyph(p, "font_8x8", ord(ch))):
            for x, bit in enumerate(row):
                if bit:
                    sample.putpixel((2 + i * 8 + x, 12 + y), (255, 255, 255))
    draw = ImageDraw.Draw(sample)
    px = 2
    for ch in "CARRIER COMMAND":
        g = vector.get(ch)
        if g:
            for line in g["lines"]:
                draw.line([(px + x, 34 + y) for x, y in line], fill=(0, 255, 0))
            px += g["width"] + 1
    sample.resize((sample.width * 3, sample.height * 3), Image.NEAREST).save(OUT / "sample.png")
    print(f"fonts -> {OUT.relative_to(ROOT)}: 5x5 (58), 8x8 (96), vector ({len(vector)})")


if __name__ == "__main__":
    main()
