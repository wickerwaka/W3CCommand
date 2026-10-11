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
    $99F2 model_draw_lines: handler.l basis.l <params> nops.w ops nverts.w
        nlines.b (colour.b v0.b v1.b) * ...  The handler overwrites nlines with the low byte
        of the object's +$38 word before drawing, so the stored count is only a placeholder;
        the lines run to the end of the record.
    $9A1C model_draw_lines_near: handler.l dist.l, then the $99F2 body (near: $5902/$5B1A).
    $935C / $99F0: rts; empty models (4-byte records).
    $AF16 model_draw_disc: no record data; one screen-space disc at the object origin,
        radius = object +$1E, colour 15 (inline disc record at $AF48).
Basis routines without a Python port ($AA78/$AABE/$AB42 carrier with turret and lifts,
$AC16/$ADA4 animated, $A660 procedural, $ABE8 turret, $A8DA/$935C state-dependent
overlays) are evaluated by running the game's code under Unicorn (stemu.py); see
EMULATION_CONTEXT for the parent model / object values used.

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
MODEL_DRAW_LINES = 0x99F2
MODEL_DRAW_LINES_NEAR = 0x9A1C
MODEL_DRAW_DISC = 0xAF16
MODEL_EMPTY = 0x935C
MODEL_EMPTY_2 = 0x99F0
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


def record_ends(p: Payload) -> dict:
    """Model address -> start of the next record (records are contiguous, word aligned)."""
    starts = sorted(set(model_addresses(p)) | {MODEL_TABLE})
    return dict(zip(starts, starts[1:]))


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def decode(p: Payload, index: int) -> Model | None:
    addr = model_addresses(p)[index]
    handler = p.u32(addr)
    body = addr + 4
    near_dist = None
    if handler in (MODEL_DRAW_POLY_NEAR, MODEL_DRAW_LINES_NEAR):
        near_dist = p.u32(body)
        body += 4
    elif handler in (MODEL_EMPTY, MODEL_EMPTY_2):
        return Model(index, addr, None, None, [], handler=handler, end=addr + 4)
    elif handler == MODEL_DRAW_DISC:
        m = Model(index, addr, None, None, [(0, 0, 0)], handler=handler, end=addr + 4)
        m.discs.append((0, None, 15))            # radius comes from the object (+$1E)
        return m
    elif handler not in (MODEL_DRAW_POLY, MODEL_DRAW_WIRE, MODEL_DRAW_LINES):
        return None
    basis = p.u32(body)
    r = _Reader(p, body + 4)
    built = _build_vertices(p, r, basis)
    if built is None:
        V, ext, r.a = _build_vertices_emulated(p, index, basis, body + 4)
    else:
        V, ext = built
    nverts = r.u16()
    m = Model(index, addr, basis, ext, [V.get(i) for i in range(nverts)], handler=handler, near_dist=near_dist)
    _parse_primitives(p, r, m)
    # Models drawing over a parent's vertices (nverts 0) reference V entries directly.
    used = [i for idx, _ in m.polys for i in idx] + [i for a, b, _ in m.lines for i in (a, b)]
    if used and max(used) >= len(m.vertices):
        m.vertices += [V.get(i) for i in range(len(m.vertices), max(used) + 1)]
    return m


# Models whose vertex build depends on state left by drawing another model first, or on
# object fields. Values are (parent model, {object offset: word}).
EMULATION_CONTEXT = {
    **{i: (74, {}) for i in range(75, 83)},    # carrier system overlays drawn after model 74
    8: (None, {0x1E: 100, 0x20: 100}),         # procedural basis $A660 reads object +$1E/+$20
    # Animated ($AC16/$ADA4): object +$24 blends vertex pairs; $200 leaves the vertices as
    # built (fully deployed), 0 collapses them.
    6: (None, {0x24: 0x200}),
    35: (None, {0x24: 0x200}),
}


def _build_vertices_emulated(p: Payload, index: int, basis: int, params: int):
    from stemu import Emu                      # optional dependency (unicorn)
    e = Emu(p)
    parent, pokes = EMULATION_CONTEXT.get(index, (None, {}))
    if parent is not None:
        paddr = model_addresses(p)[parent]
        pbody = paddr + (8 if p.u32(paddr) in (MODEL_DRAW_POLY_NEAR, MODEL_DRAW_LINES_NEAR) else 4)
        e.build_vertices(p.u32(pbody), pbody + 4)
    from stemu import OBJ
    for off, v in pokes.items():
        e.w16(OBJ + off, v)
    end = e.build_vertices(basis, params)
    V = {i: v for i in range(256) if (v := e.vertex(i)) is not None}
    return V, None, end


def _build_vertices(p: Payload, r: "_Reader", basis: int):
    """Python implementation of the basis routines and vertex ops; None if unsupported."""
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
            raise ValueError(f"unknown vertex op ${op:02X} at ${r.a - 2:05X}")
    return V, ext


def _parse_primitives(p: Payload, r: "_Reader", m: Model) -> None:
    handler = m.handler
    if handler in (MODEL_DRAW_LINES, MODEL_DRAW_LINES_NEAR):
        r.u8()                                   # placeholder count
        record_end = record_ends(p)[m.addr]
        while r.a + 3 <= record_end:
            c, v0, v1 = r.u8(), r.u8(), r.u8()
            m.lines.append((v0, v1, c & 0x7F))
        m.end = r.a
        return
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
        return
    m.planes = [(r.u8(), r.u8(), r.u8()) for _ in range(r.u8())]
    m.end = _bsp(r, r.a, m)


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
