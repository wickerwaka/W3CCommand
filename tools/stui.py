"""Decoder for the UI panel system (drawn by $B400/$B418/$B014).

Panel list ($B400):  count.w, then count * panel.l
Panel ($B418):       state_addr.w state_value.w draw_callback.l flag.b colour.b rect.w*4
                     then elements (22 bytes each) until a $FFFF word. The panel is drawn
                     only while the word at state_addr equals state_value; draw_callback is
                     called first. flag 0 = draw the bevelled frame `rect` ($54B6).
Element ($B014):     type.w ?.w x.w y.w desc.w  + 12 more bytes (input handling, not decoded)
                     type indexes the flag table $40EB2 (bit 15: hidden; bit 13: draw the
                     descriptor box highlighted).
Descriptor:          at $40F86 + desc: kind.w box.w*4 (x0 y0 x1 y1, relative to x/y), then a
                     payload depending on kind (dispatch table $B06A):
    1  16-px masked bitmap   rows.w ptr.l                         ($B292)
    2  32-px masked bitmap   rows.w ptr.l                         ($B316)
    3  filled polygon        dx.w dy.w n.w points                 ($B18A, not decoded)
    4  8x8 text              colour.b string                      ($B156)
    5  box + 8x8 text        dx.w dy.w boxcolour.b colour.b string
    6  5x5 text              colour.b string (control < $20 = newline, 5 n = colour)
    7  box + 5x5 text        dx.w dy.w boxcolour.b colour.b string
    8  bevel + 8x8 text      dx.w dy.w colour.b string
    9  bevel + 5x5 text      dx.w dy.w colour.b string
    10 48-px bitmap          rows.w ptr.l; three 16-px strips at ptr, ptr+$A0, ptr+$140
    11 48-px bitmap + text   rows.w ptr.l, 6 bytes, dx.w dy.w colour.b string
"""
from dataclasses import dataclass

from stgfx import Payload

FLAG_TABLE = 0x40EB2
DESCRIPTORS = 0x40F86
ELEMENT_SIZE = 22


@dataclass
class Element:
    type: int
    x: int
    y: int
    desc: int
    hidden: bool
    kind: int
    box: tuple
    payload: int          # address of the kind-specific payload


@dataclass
class Panel:
    addr: int
    state_addr: int
    state_value: int
    callback: int
    flag: int
    colour: int
    rect: tuple
    elements: list


def parse_panel(p: Payload, addr: int) -> Panel:
    state_addr, state_value = p.u16(addr), p.u16(addr + 2)
    callback = p.u32(addr + 4)
    flag, colour = p.data[addr + 8 - 0x400], p.data[addr + 9 - 0x400]
    rect = tuple(p.s16(addr + 10 + 2 * i) for i in range(4))
    elements = []
    a = addr + 18
    while p.u16(a) != 0xFFFF:
        if len(elements) >= 64:
            raise ValueError(f"panel ${addr:05X}: no element terminator")
        etype, _, x, y, desc = (p.s16(a + 2 * i) for i in range(5))
        if not 0 <= etype < 128 or not 0 <= desc < 0x1000:
            raise ValueError(f"panel ${addr:05X}: implausible element at ${a:05X}")
        flags = p.u16(FLAG_TABLE + 2 * etype)
        d = DESCRIPTORS + desc
        elements.append(Element(etype, x, y, desc, bool(flags & 0x8000), p.u16(d),
                                tuple(p.s16(d + 2 + 2 * i) for i in range(4)), d + 10))
        a += ELEMENT_SIZE
    return Panel(addr, state_addr, state_value, callback, flag, colour, rect, elements)


def text_at(p: Payload, a: int) -> list:
    """Split a control-coded string into (colour or None, text) lines; stops at 0."""
    lines, cur, colour = [], "", None
    while True:
        b = p.data[a - 0x400]
        a += 1
        if b == 0:
            break
        if b == 5:
            colour = p.data[a - 0x400]
            a += 1
        elif b < 0x20:
            lines.append((colour, cur))
            cur = ""
        else:
            cur += chr(b)
    lines.append((colour, cur))
    return lines
