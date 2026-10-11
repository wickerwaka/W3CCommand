"""Atari ST graphics decoding for CARRIER.TOS payload data.

Addresses are runtime addresses; the payload is loaded at $400, so payload
offset = address - $400.
"""
import struct
from pathlib import Path

from PIL import Image

PAYLOAD_BASE = 0x400
ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_PATH = ROOT / "extracted" / "carrier_payload.bin"


class Payload:
    def __init__(self, path: Path = PAYLOAD_PATH):
        self.data = path.read_bytes()

    def bytes(self, addr: int, n: int) -> bytes:
        o = addr - PAYLOAD_BASE
        return self.data[o:o + n]

    def u16(self, addr: int) -> int:
        return struct.unpack_from(">H", self.data, addr - PAYLOAD_BASE)[0]

    def s16(self, addr: int) -> int:
        return struct.unpack_from(">h", self.data, addr - PAYLOAD_BASE)[0]

    def u32(self, addr: int) -> int:
        return struct.unpack_from(">I", self.data, addr - PAYLOAD_BASE)[0]


def st_palette(p: Payload, addr: int) -> list:
    """16 ST palette words (0RGB, 3 bits per channel) -> RGB tuples."""
    out = []
    for i in range(16):
        w = p.u16(addr + i * 2)
        out.append(tuple(((w >> s) & 7) * 255 // 7 for s in (8, 4, 0)))
    return out


def masked_sprite(p: Payload, addr: int, rows: int, width: int = 32) -> list:
    """Masked UI sprite, drawn by $B316 (32 px wide) or $B292 (16 px wide). Each row
    is width/16 columns of (mask, plane0, plane1, plane2, plane3) words; a mask bit
    of 1 keeps the background. Returns rows of colour indices, None for transparent."""
    out = []
    a = addr
    for _ in range(rows):
        row = []
        for _col in range(width // 16):
            mask, *planes = struct.unpack(">5H", p.bytes(a, 10))
            a += 10
            for bit in range(15, -1, -1):
                if (mask >> bit) & 1:
                    row.append(None)
                else:
                    row.append(sum(((pl >> bit) & 1) << i for i, pl in enumerate(planes)))
        out.append(row)
    return out


def picture3(p: Payload, addr: int, width: int, rows: int) -> list:
    """Opaque 3-plane picture drawn by $B832: per row, width/16 columns of (p0, p2, p3)
    words; plane 1 is not stored but computed as p0 AND p2."""
    out = []
    a = addr
    for _ in range(rows):
        row = []
        for _col in range(width // 16):
            p0, p2, p3 = struct.unpack(">3H", p.bytes(a, 6))
            a += 6
            p1 = p0 & p2
            for bit in range(15, -1, -1):
                row.append(sum(((pl >> bit) & 1) << i for i, pl in enumerate((p0, p1, p2, p3))))
        out.append(row)
    return out


def to_image(pixels: list, palette: list) -> Image.Image:
    """Colour-index rows (None = transparent) -> RGBA image."""
    img = Image.new("RGBA", (len(pixels[0]), len(pixels)), (0, 0, 0, 0))
    for y, row in enumerate(pixels):
        for x, c in enumerate(row):
            if c is not None:
                img.putpixel((x, y), palette[c] + (255,))
    return img


def screen(bg=(40, 0, 40)) -> Image.Image:
    """Blank 320x200 ST low-res screen."""
    return Image.new("RGBA", (320, 200), bg + (255,))


def sprite_table(p: Payload, addr: int):
    """10-byte records consumed by $F0BE (32 px) / $F0D2 (16 px): x.w, y.w, rows-1.w,
    ptr.l; ends at x < 0."""
    while p.s16(addr) >= 0:
        yield p.u16(addr), p.u16(addr + 2), p.u16(addr + 4) + 1, p.u32(addr + 6)
        addr += 10
