"""Decoder for CARRIER.TOS 3D models (model_table at $4A06A).

Each record starts with the address of its draw handler. Decoded handlers:

    $935E model_draw_poly:
        handler.l  basis.l <basis params>
        nops.w  (op.b, idx.b) * nops        vertex builder, $5A02
        nverts.w                            V[0..nverts-1] are projected ($5912)
        nplanes.b  (a.b, b.b, c.b) * nplanes  back-face planes ($98E6)
        BSP tree ($9382)
    $9550 model_draw_poly_near: handler.l dist.l, then the $935E body. Beyond dist it
        branches to $935E; nearer it uses a clipping projection ($5902) and its own BSP
        walker ($9582) over the same data.
    $9496 model_draw_wire (hidden-line wireframe):
        handler.l  basis.l <basis params>  nops.w ops  nverts.w
        nlines.b (colour.b v0.b v1.b) * n   always drawn ($5AA6)
        nlines.b (colour.b v0.b v1.b) * n   conditional lines, drawn by $5AA0 only when
                                            flagged (colour bit 7)
        nfaces.b, per face a.b b.b c.b m.b line.b*m  (if the face is towards the camera, set
                                            bit 7 on those conditional lines)

Vertex builder ops (idx*6 indexes the 3-word vertex array V, cur is an accumulator):
    $00 cur = V[idx]   $18 V[idx] = cur   $30 cur = (cur + V[idx]) >> 1
    $48 cur += V[idx]  $60 cur -= V[idx]  $78 cur = -cur
The basis routine (second long) reads its parameters and fills basis vertices; the object's
runtime rotation is omitted here (identity), giving model-space coordinates:
    $A78C box:      sx.w sy.w sz.w -> V[$F8..$FF] = corners of the box (+-sx, +-sy, +-sz)
    $A89A box+list: box, then n.w * (fx.w fy.w fz.w idx.w): V[idx] = (fx*2sx, fy*2sy, fz*2sz)
                    with f in signed Q15
    $AA24 quad:     sx.w sy.w -> V[$F8..$FB] = corners of a flat quad in the z=0 plane
    $AF4E two box:  box -> V[$F8..$FF], then box -> V[$F0..$F7]
    $AC16:          articulated/animated (two boxes, own vertex stream, interpolation); unsupported

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
MODEL_DRAW_POLY_NEAR = 0x9550
MODEL_DRAW_WIRE = 0x9496
BASIS_BOX = 0xA78C
BASIS_BOX_LIST = 0xA89A
BASIS_QUAD = 0xAA24
BASIS_TWO_BOX = 0xAF4E


def box_corners(sx: int, sy: int, sz: int, base: int = 0xF8) -> dict:
    corners = [(sx, -sy, -sz), (sx, -sy, sz), (-sx, -sy, sz), (-sx, -sy, -sz),
               (sx, sy, -sz), (sx, sy, sz), (-sx, sy, sz), (-sx, sy, -sz)]
    return {base + i: c for i, c in enumerate(corners)}


def _q15(f: int, extent: int) -> int:
    # muls.w, add.l (x2), swap: (f * v * 2) >> 16 with v = 2 * extent
    return (f * 2 * extent * 2) >> 16


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
    handler: int = MODEL_DRAW_POLY
    near_dist: int | None = None                     # $9550 only
    faces: list = field(default_factory=list)        # $9496 only: (a, b, c, [line indices])
    end: int = 0                                     # address after the record data


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
    handler = p.u32(addr)
    body = addr + 4
    near_dist = None
    if handler == MODEL_DRAW_POLY_NEAR:
        near_dist = p.u32(body)
        body += 4
    elif handler not in (MODEL_DRAW_POLY, MODEL_DRAW_WIRE):
        return None
    basis = p.u32(body)
    r = _Reader(p, body + 4)
    if basis in (BASIS_BOX, BASIS_BOX_LIST, BASIS_TWO_BOX):
        ext = (r.s16(), r.s16(), r.s16())
        V = box_corners(*ext)
        if basis == BASIS_BOX_LIST:
            for _ in range(r.u16()):
                fx, fy, fz, idx = r.s16(), r.s16(), r.s16(), r.u16()
                V[idx] = (_q15(fx, ext[0]), _q15(fy, ext[1]), _q15(fz, ext[2]))
        elif basis == BASIS_TWO_BOX:
            ext2 = (r.s16(), r.s16(), r.s16())
            V.update(box_corners(*ext2, base=0xF0))
            ext = ext + ext2
    elif basis == BASIS_QUAD:
        sx, sy = r.s16(), r.s16()
        ext = (sx, sy, 0)
        V = {0xF8: (sx, -sy, 0), 0xF9: (sx, sy, 0), 0xFA: (-sx, sy, 0), 0xFB: (-sx, -sy, 0)}
    else:
        return None
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
    m = Model(index, addr, basis, ext, [V.get(i) for i in range(nverts)], handler=handler, near_dist=near_dist)
    if handler == MODEL_DRAW_WIRE:
        for _ in range(r.u8()):
            c, v0, v1 = r.u8(), r.u8(), r.u8()
            m.lines.append((v0, v1, c & 0x7F))
        conditional_base = len(m.lines)
        for _ in range(r.u8()):
            c, v0, v1 = r.u8(), r.u8(), r.u8()
            m.lines.append((v0, v1, c & 0x7F))
        for _ in range(r.u8()):
            a, b, c = r.u8(), r.u8(), r.u8()
            m.faces.append((a, b, c, [conditional_base + r.u8() for _ in range(r.u8())]))
        m.end = r.a
        return m
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
