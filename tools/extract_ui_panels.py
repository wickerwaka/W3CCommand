#!/usr/bin/env python3
"""Render the UI panels (see stui.py) and extract every bitmap their descriptors use.

Panels are found from the `lea panel,A5` before each call to $B418 and from the panel
lists passed to $B400 in A6. Writes, under extracted/ui/:
  panels/<addr>.png     each panel composited on a 320x200 screen (frames approximated)
  bitmaps/<addr>_<w>.png  every bitmap referenced by a descriptor (RGBA)
  panels.txt            panel/element listing
Bevelled frames ($54B6) and boxes are approximated; polygons (kind 3) are outlined.
"""
import sys
from pathlib import Path

from PIL import ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_font import bitmap_glyph
from stgfx import ROOT, Payload, masked_sprite, screen, st_palette, to_image
from stui import parse_panel, text_at

DATA_START = 0x22E62
PANEL = 0xB418
PANEL_LIST = 0xB400
DEFAULT_PALETTE = 0x132A
OUT = ROOT / "extracted" / "ui"


def find_panels(p: Payload) -> set:
    panels, lists = set(), set()
    for a in range(0x400, DATA_START, 2):
        op = p.u16(a)
        if op in (0x6100, 0x6000):
            target = a + 2 + p.s16(a + 2)
        elif op in (0x4EB9, 0x4EF9):
            target = p.u32(a + 2)
        else:
            continue
        if target not in (PANEL, PANEL_LIST):
            continue
        reg = 5 if target == PANEL else 6
        for back in range(2, 30, 2):
            b = a - back
            o = p.u16(b)
            if o & 0xF1FF in (0x41FA, 0x41F9) and (o >> 9) & 7 == reg:
                t = b + 2 + p.s16(b + 2) if o & 0xF1FF == 0x41FA else p.u32(b + 2)
                (panels if target == PANEL else lists).add(t)
                break
    for lst in lists:
        for i in range(p.u16(lst)):
            panels.add(p.u32(lst + 2 + 4 * i))
    return panels


def draw_text(img, p, font, x, y, lines, colour, palette):
    step, h = (6, 7) if font == "font_5x5" else (8, 8)
    first = 0x21 if font == "font_5x5" else 0x20
    last = 0x5A if font == "font_5x5" else 0x7F
    for ln, (c, text) in enumerate(lines):
        rgb = palette[(c if c is not None else colour) & 15]
        for i, ch in enumerate(text.upper() if font == "font_5x5" else text):
            if first <= ord(ch) <= last:
                for gy, row in enumerate(bitmap_glyph(p, font, ord(ch))):
                    for gx, bit in enumerate(row):
                        px, py = x + i * step + gx, y + ln * h + gy
                        if bit and 0 <= px < 320 and 0 <= py < 200:
                            img.putpixel((px, py), rgb + (255,))


def bitmap(p, payload, width):
    rows, ptr = p.u16(payload), p.u32(payload + 2)
    if width == 48:
        strips = [masked_sprite(p, ptr + i * 0xA0, rows, 16) for i in range(3)]
        return [sum((s[r] for s in strips), []) for r in range(rows)], ptr
    return masked_sprite(p, ptr, rows, width), ptr


def render_panel(p, palette, panel, bitmaps) -> object:
    img = screen()
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = panel.rect
    if panel.flag == 0:
        draw.rectangle([x0, y0, x1, y1], fill=palette[2] + (255,), outline=palette[1] + (255,))
    for e in panel.elements:
        if e.hidden:
            continue
        k, a = e.kind, e.payload
        bx0, by0, bx1, by1 = e.box
        try:
            if k in (1, 2, 10, 11):
                width = {1: 16, 2: 32, 10: 48, 11: 48}[k]
                pix, ptr = bitmap(p, a, width)
                if DATA_START <= ptr < 0x400 + len(p.data):
                    bitmaps[(ptr, width)] = pix
                    img.alpha_composite(to_image(pix, palette), (e.x, e.y))
                if k == 11:
                    a += 12
                    dx, dy, colour = p.s16(a), p.s16(a + 2), p.data[a + 4 - 0x400]
                    draw_text(img, p, "font_5x5", e.x + dx, e.y + dy, text_at(p, a + 5), colour, palette)
            elif k == 3:
                dx, dy, n = p.s16(a), p.s16(a + 2), p.u16(a + 4)
                pts = [(e.x + dx + p.s16(a + 6 + 4 * i), e.y + dy + p.s16(a + 8 + 4 * i)) for i in range(min(n, 64))]
                if len(pts) >= 2:
                    draw.line(pts + pts[:1], fill=palette[15] + (255,))
            elif k in (4, 6):
                font = "font_8x8" if k == 4 else "font_5x5"
                draw_text(img, p, font, e.x, e.y, text_at(p, a + 1), p.data[a - 0x400], palette)
            elif k in (5, 7, 8, 9):
                dx, dy = p.s16(a), p.s16(a + 2)
                rect = [e.x + bx0, e.y + by0, e.x + bx1, e.y + by1]
                if k in (5, 7):
                    draw.rectangle(rect, fill=palette[p.data[a + 4 - 0x400] & 15] + (255,))
                    a += 5
                else:
                    draw.rectangle(rect, fill=palette[2] + (255,), outline=palette[1] + (255,))
                    a += 4
                font = "font_8x8" if k in (5, 8) else "font_5x5"
                draw_text(img, p, font, e.x + dx, e.y + dy, text_at(p, a + 1), p.data[a - 0x400], palette)
        except (IndexError, ValueError):
            pass
    return img


def descriptor_bitmaps(p: Payload) -> list:
    """Structural scan of the descriptor area for bitmap descriptors (kinds 1, 2, 10, 11),
    including ones used only by panels built at runtime. Returns (desc addr, width, rows, ptr)."""
    found = []
    for a in range(0x40F86, 0x43800, 2):
        kind = p.u16(a)
        width = {1: 16, 2: 32, 10: 48, 11: 48}.get(kind)
        if width is None:
            continue
        box = [p.s16(a + 2 + 2 * i) for i in range(4)]
        rows, ptr = p.u16(a + 10), p.u32(a + 12)
        if 0 <= box[0] <= box[2] < 64 and 0 <= box[1] <= box[3] < 64 and 0 < rows <= 64 \
                and DATA_START <= ptr < 0x40E26:
            found.append((a, width, rows, ptr))
    return found


# Sprites whose addresses are computed at runtime (not found through tables or descriptors).
# Strips of stacked 16-px masked sprites (individual heights not determined): payload
# loadout icons, numbered drone markers, hangar icons.
LOOSE_STRIPS_16 = [(0x36192, 0x36426), (0x366F6, 0x36C46), (0x36F2A, 0x37146),
                   (0x371B4, 0x37326), (0x374D4, 0x37826), (0x37934, 0x37AA6)]
# 32-px, 23-row icon variants patched into sprite table $C512 by $BFAA.
LOOSE_32 = [0x3562A, 0x357F6, 0x359C2, 0x35B8E]


def main() -> None:
    p = Payload()
    palette = st_palette(p, DEFAULT_PALETTE)
    (OUT / "panels").mkdir(parents=True, exist_ok=True)
    (OUT / "bitmaps").mkdir(parents=True, exist_ok=True)
    bitmaps, lines = {}, []
    panels = sorted(find_panels(p))
    for addr in panels:
        try:
            panel = parse_panel(p, addr)
        except (ValueError, IndexError, Exception) as e:
            lines.append(f"panel ${addr:05X} not parsed: {e}")
            continue
        render_panel(p, palette, panel, bitmaps).save(OUT / "panels" / f"{addr:05X}.png")
        lines.append(f"panel ${addr:05X} shown when [${panel.state_addr:04X}].w == {panel.state_value} "
                     f"callback=${panel.callback:05X} frame={'yes' if panel.flag == 0 else 'no'} rect={panel.rect}")
        for e in panel.elements:
            lines.append(f"  type={e.type:3d} x={e.x:3d} y={e.y:3d} desc=+${e.desc:03X} kind={e.kind:2d} "
                         f"box={e.box}{' hidden' if e.hidden else ''}")
    for desc, width, rows, ptr in descriptor_bitmaps(p):
        if (ptr, width) not in bitmaps:
            bitmaps[(ptr, width)] = bitmap(p, desc + 10, width)[0]
    for start, end in LOOSE_STRIPS_16:
        bitmaps[(start, 16)] = masked_sprite(p, start, (end - start) // 10, 16)
    for ptr in LOOSE_32:
        bitmaps[(ptr, 32)] = masked_sprite(p, ptr, 23, 32)
    for (ptr, width), pix in sorted(bitmaps.items()):
        to_image(pix, palette).save(OUT / "bitmaps" / f"{ptr:05X}_{width}.png")
    (OUT / "panels.txt").write_text("\n".join(lines) + "\n")
    print(f"{len(panels)} panels, {len(bitmaps)} bitmaps -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
