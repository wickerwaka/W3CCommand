"""Decoder for CARRIER.TOS 3D models (model_table at $4A06A).

Only models drawn by model_draw_poly ($935E) are decoded. Record layout:

    handler.l ($935E)  basis.l  <basis params>
    nops.w  (op.b, idx.b) * nops        vertex builder, $5A02
    nverts.w                            V[0..nverts-1] are projected ($5912)
    nplanes.b  (a.b, b.b, c.b) * nplanes  back-face planes ($98E6)
    BSP tree ($9382)

Vertex builder ops (idx*6 indexes the 3-word vertex array V, cur is an accumulator):
    $00 cur = V[idx]   $18 V[idx] = cur   $30 cur = (cur + V[idx]) >> 1
    $48 cur += V[idx]  $60 cur -= V[idx]  $78 cur = -cur
V[$F8..$FF] are filled by the basis routine; for model_basis_box ($A78C) they are the
corners of a box with half-extents (sx, sy, sz) (rotation applied at runtime omitted).

BSP node: plane.b off.w <inline child>; the other child starts at node+3+off.
$FF = leaf: ngroups.b, per group flags.b and:
    bit0: a.b b.b c.b skip.b                group back-face test (skip.b bytes if hidden)
    bit1: npolys.b, per poly n.b idx.b*n colour.b
    bit2: nlines.b, per line colour.b v0.b v1.b   (colour bit 7 is a runtime flag)
    bit3: ndiscs.b, per disc v.b radius.w colour.b
$80..$FE = attachment slot (~byte), drawn from the object slot table at $6D6.
"""
from dataclasses import dataclass, field

from stgfx import Payload

MODEL_TABLE = 0x4A06A
MODEL_COUNT = 99
MODEL_DRAW_POLY = 0x935E
BASIS_BOX = 0xA78C


def box_corners(sx: int, sy: int, sz: int) -> dict:
    return {
        0xF8: (sx, -sy, -sz), 0xF9: (sx, -sy, sz), 0xFA: (-sx, -sy, sz), 0xFB: (-sx, -sy, -sz),
        0xFC: (sx, sy, -sz), 0xFD: (sx, sy, sz), 0xFE: (-sx, sy, sz), 0xFF: (-sx, sy, -sz),
    }


@dataclass
class Model:
    index: int
    addr: int
    basis: int
    extents: tuple
    vertices: list                                   # V[0..nverts-1]
    planes: list = field(default_factory=list)       # (a, b, c)
    polys: list = field(default_factory=list)        # (indices, colour)
    lines: list = field(default_factory=list)        # (v0, v1, colour)
    discs: list = field(default_factory=list)        # (v, radius, colour)
    attachments: list = field(default_factory=list)  # slot numbers
    end: int = 0                                     # address after the BSP tree


class _Reader:
    def __init__(self, p: Payload, addr: int):
        self.p, self.a = p, addr

    def u8(self) -> int:
        v = self.p.data[self.a - 0x400]
        self.a += 1
        return v

    def u16(self) -> int:
        v = self.p.u16(self.a)
        self.a += 2
        return v

    def s16(self) -> int:
        v = self.p.s16(self.a)
        self.a += 2
        return v


def model_addresses(p: Payload) -> list:
    return [p.u32(MODEL_TABLE + 4 * i) for i in range(MODEL_COUNT)]


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def decode(p: Payload, index: int) -> Model | None:
    addr = model_addresses(p)[index]
    if p.u32(addr) != MODEL_DRAW_POLY or p.u32(addr + 4) != BASIS_BOX:
        return None
    r = _Reader(p, addr + 8)
    ext = (r.s16(), r.s16(), r.s16())
    V = dict(box_corners(*ext))
    cur = (0, 0, 0)
    for _ in range(r.u16()):
        op, idx = r.u8(), r.u8()
        if op == 0x00:
            cur = V[idx]
        elif op == 0x18:
            V[idx] = cur
        elif op == 0x30:
            cur = tuple(_s16(c + v) >> 1 for c, v in zip(cur, V[idx]))
        elif op == 0x48:
            cur = tuple(_s16(c + v) for c, v in zip(cur, V[idx]))
        elif op == 0x60:
            cur = tuple(_s16(c - v) for c, v in zip(cur, V[idx]))
        elif op == 0x78:
            cur = tuple(_s16(-c) for c in cur)
        else:
            raise ValueError(f"model {index}: unknown vertex op ${op:02X} at ${r.a - 2:05X}")
    nverts = r.u16()
    m = Model(index, addr, BASIS_BOX, ext, [V.get(i) for i in range(nverts)])
    m.planes = [(r.u8(), r.u8(), r.u8()) for _ in range(r.u8())]
    m.end = _bsp(r, r.a, m)
    return m


def _bsp(r: _Reader, node: int, m: Model) -> int:
    """Walk both children of every node; returns the address after the subtree."""
    r.a = node
    b = r.u8()
    if b < 0x80:
        off = r.u16()
        inline_end = _bsp(r, r.a, m)
        other = node + 3 + off
        if other < inline_end:
            raise ValueError(f"model {m.index}: BSP child ${other:05X} overlaps inline subtree")
        return _bsp(r, other, m)
    if b != 0xFF:
        m.attachments.append(~b & 0xFF)
        return r.a
    for _ in range(r.u8()):
        flags = r.u8()
        if flags & 1:
            r.a += 4
        if flags & 2:
            for _ in range(r.u8()):
                n = r.u8()
                idx = [r.u8() for _ in range(n)]
                m.polys.append((idx, r.u8()))
        if flags & 4:
            for _ in range(r.u8()):
                c, v0, v1 = r.u8(), r.u8(), r.u8()
                m.lines.append((v0, v1, c & 0x7F))
        if flags & 8:
            for _ in range(r.u8()):
                v = r.u8()
                rad = r.u16()
                m.discs.append((v, rad, r.u8()))
        if flags & ~0xF:
            raise ValueError(f"model {m.index}: unknown group flags ${flags:02X}")
    return r.a
