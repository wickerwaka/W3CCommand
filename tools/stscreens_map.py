"""Group D: what the map screens (2 carrier, 17 Walrus, 25 Manta) draw ($14E3C -> $7712 sliders,
$65E2 map; $B418 of panel $4203A -> $BB10 island info box while [$766] != 0).

Python mirror of the readouts + a check against the original's render under Unicorn
(Tracer from stscreens.py with extra hooks on the draw primitives).

  uv run python tools/stscreens_map.py [--fuzz N] [captures...]
"""
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stscreens import Tracer  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.m68k_const import (UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_D0,  # noqa: E402
                                UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_D3, UC_M68K_REG_D4)

from stgfx import ROOT  # noqa: E402
NAMES = 0x4AD20
ISLANDS, ISLAND_SIZE, OWNERS, ECONOMY = 0x69B80, 0x192, 0x69B40, 0x66EC0
ROUTES = 0x67B40
OBJECTS, OBJ = 0x5DCDA, 0x140
MANTA, WALRUS, REC = 0x496, 0x570, 0x34
MAP_VP = 0x47F2                 # x0 y0 x1 y1 . . cx cy (32, 6, 287, 133, ., ., 160, 70)
MAP_ZOOM_LEVEL, MAP_ZOOM, MAP_X, MAP_Z = 0x758, 0x75A, 0x75C, 0x760
OWNER_COLOUR = 0x6E90           # word per owner: 0 neutral $B, 1 player 7, 2 enemy 1
SHAPES = 0x6E96                 # 8 bytes per marker: points.l (count.w, (x.w, y.w)*), lines.l
BORDER_POINTS, BORDER_LINES = 0x7564, 0x7694
SINE = 0x1F5A


def s16(v): v &= 0xFFFF; return v - 0x10000 if v & 0x8000 else v
def s32(v): v &= 0xFFFFFFFF; return v - (1 << 32) if v & 0x80000000 else v


class R:
    def __init__(self, d): self.d = d
    def u8(self, a): return self.d[a]
    def s8(self, a): return self.d[a] - 256 if self.d[a] & 0x80 else self.d[a]
    def u16(self, a): return struct.unpack_from(">H", self.d, a)[0]
    def s16(self, a): return struct.unpack_from(">h", self.d, a)[0]
    def u32(self, a): return struct.unpack_from(">I", self.d, a)[0]
    def s32(self, a): return struct.unpack_from(">i", self.d, a)[0]
    def cstr(self, a):
        s = ""
        while self.d[a]:
            s += chr(self.d[a]); a += 1
        return s


def name(r, i): return r.cstr(NAMES + r.u16(NAMES + 2 * i))


# ---- the map transform ($65C4, $7526) ------------------------------------------------------

def map_div(d, zoom):
    """$65C4: (d asr 8) / zoom, floored (the quotient is decremented when the remainder is
    negative); a quotient beyond a word gives +-$7FFF (sign of the dividend)."""
    d4 = s32(d) >> 8
    q = abs(d4) // zoom * (1 if d4 >= 0 else -1)          # divs: truncate
    if not -0x8000 <= q <= 0x7FFF:
        return -0x7FFF if d4 < 0 else 0x7FFF
    if d4 - q * zoom < 0:
        q = q - 1 if q != -0x8000 else q
    return q


def world_to_screen(r, x, z):
    """$7526: world (x, z) -> screen (sx, sy) on the map viewport; an overflowing add gives $7FFF."""
    zoom = r.s16(MAP_ZOOM)
    dx = map_div(s32(x - r.s32(MAP_X)), zoom)
    dy = map_div(s32(-s32(z - r.s32(MAP_Z))), zoom)
    sx, sy = dx + r.s16(MAP_VP + 0xC), dy + r.s16(MAP_VP + 0xE)
    if not -0x8000 <= sx <= 0x7FFF: sx = 0x7FFF
    if not -0x8000 <= sy <= 0x7FFF: sy = 0x7FFF
    return sx, sy


def sincos(r, a):
    a &= 0xFFF0
    return r.s16(SINE + (a >> 3)), r.s16(SINE + (((a + 0x4000) & 0xFFFF) >> 3))


def marker(r, kind, x, z, angle=None):
    """$6DFE: marker shape `kind` at world (x, z), turned by angle (None: unturned). Returns
    (sx, sy, segments) or None when |sx| or |sy| > $200. Segments: (colour, x0, y0, x1, y1)."""
    sx, sy = world_to_screen(r, x, z)
    if abs(sx) > 0x200 or abs(sy) > 0x200:
        return None
    s, c = (0, 0x4000) if angle is None else sincos(r, angle)
    div = 12 * min(r.s16(MAP_ZOOM), 8)
    pts_a, lines = r.u32(SHAPES + 8 * kind), r.u32(SHAPES + 8 * kind + 4)
    pts = []
    for k in range(r.u16(pts_a)):
        px, py = r.s16(pts_a + 2 + 4 * k), r.s16(pts_a + 4 + 4 * k)
        X = s16(((px * c + py * s) * 4 & 0xFFFFFFFF) >> 16)
        Y = s16(((py * c - px * s) * 4 & 0xFFFFFFFF) >> 16)
        X, Y = int(X / div), int(Y / div)
        pts.append((s16(X + sx), s16(-Y + sy)))
    return sx, sy, line_list(r, lines, pts)


def line_list(r, a, pts):
    out = []
    for k in range(r.u8(a)):
        col, i, j = r.u8(a + 1 + 3 * k) & 0x7F, r.u8(a + 2 + 3 * k), r.u8(a + 3 + 3 * k)
        out.append((col, *pts[i], *pts[j]))
    return out


# ---- readouts ---------------------------------------------------------------------------------

def map_view(r):
    return {"level": r.s16(MAP_ZOOM_LEVEL), "zoom": r.s16(MAP_ZOOM), "x": r.s32(MAP_X), "z": r.s32(MAP_Z),
            "infoBox": r.s16(0x766) != 0}


def islands_on_map(r):
    """$6658 per island: centre offset, nearest-to-centre pick ($764), label, rectangle, markers."""
    vp = [r.s16(MAP_VP + 2 * i) for i in range(4)]
    cx, cy = r.s16(MAP_VP + 0xC), r.s16(MAP_VP + 0xE)
    zoom, level, screen = r.s16(MAP_ZOOM), r.s16(MAP_ZOOM_LEVEL), r.s16(0x718)
    best, nearest = 0x1E8480, None
    out = []
    for i in range(64):
        a = ISLANDS + ISLAND_SIZE * i
        x, z, hx, hz = r.s32(a + 2), r.s32(a + 6), r.s16(a + 0xA), r.s16(a + 0xC)
        owner = r.u8(OWNERS + i)
        dx, dz = s32(x - r.s32(MAP_X)), s32(-s32(z - r.s32(MAP_Z)))
        x0, y0 = map_div(dx - hx, zoom), map_div(dz - hz, zoom)
        x1, y1 = map_div(dx + hx, zoom), map_div(dz + hz, zoom)
        mx, my = map_div(dx, zoom), map_div(dz, zoom)
        if (abs(mx) & 0xFFFF) >= 0x100 or (abs(my) & 0xFFFF) >= 0x100:
            continue
        d2 = mx * mx + my * my
        if d2 < best:
            best, nearest = d2, i
        x0, x1, y0, y1 = s16(x0 + cx), s16(x1 + cx), s16(y0 + cy), s16(y1 + cy)
        isl = {"index": i, "name": name(r, i), "owner": owner, "centre": (s16(mx + cx), s16(my + cy)),
               "label": None, "rect": None, "markers": []}
        if (level & 0xFFFF) <= 5:
            isl["label"] = (s16(x0 - 35 // zoom), s16(y0 - 32 // zoom), (0x400 // zoom) & 0xFFFF)
        if x1 == x0: x1 = s16(x1 + 1)
        if y1 == y0: y1 = s16(y1 + 1)
        out.append(isl)
        if x1 < vp[0] or x0 > vp[2] or y1 < vp[1] or y0 > vp[3]:
            continue
        isl["rect"] = (max(x0, vp[0]), max(y0, vp[1]), min(x1, vp[2]), min(y1, vp[3]),
                       r.u16(OWNER_COLOUR + 2 * owner))
        if level > 2:
            continue
        stock = r.s16(0xD4A)
        if screen == 2 and r.s16(0x710):
            if owner != 1:
                continue
            kind = r.u8(ECONOMY + 0x32 * i) & 3
            isl["markers"].append(("kind", 0xB + kind, marker(r, 0xB + kind, x - (0x1000 if i == stock else 0), z)))
            if i == stock:
                isl["markers"].append(("stockpile", 0xF, marker(r, 0xF, x + 0x1000, z)))
            continue
        sl = a + 0x12 + 0xC
        if r.u16(sl) == 0x24:
            isl["markers"].append(("runway", 9, marker(r, 9, x + r.s16(sl + 8), z + r.s16(sl + 0xA), r.u16(sl + 2))))
        for k in (2, 3):
            sl = a + 0x12 + 0xC * k
            if r.u16(sl) == 7:
                isl["markers"].append(("volcano", 0xA, marker(r, 0xA, x + r.s16(sl + 8), z + r.s16(sl + 0xA), 0)))
    return out, nearest


def copyright_label(r):
    """$6A9E: at zoom levels 0-5, the text at $76E4 ("COPYRIGHT REALTIME GAMES SOFTWARE LTD
    1987/88", colour $F) at world ($79BFFF, -$7FD800), the far south-east corner, when its
    screen point is within |sx| < $400, |sy| < $100."""
    if (r.u16(MAP_ZOOM_LEVEL)) > 5:
        return None
    sx, sy = world_to_screen(r, 0x79BFFF, -0x7FD800)
    if (abs(sx) & 0xFFFF) >= 0x400 or (abs(sy) & 0xFFFF) >= 0x100:
        return None
    return (r.cstr(0x76E4), sx, sy, (0x400 // r.s16(MAP_ZOOM)) & 0xFFFF)


def coord_text(v):
    """$BCFC: a world coordinate as sign + 4 digits of v / 1678 (divs; the sign overwrites the
    ten-thousands digit)."""
    q = int(v / 0x68E)
    if not -0x8000 <= q <= 0x7FFF:
        q = v                                       # divs overflow: d0 unchanged (not reachable)
    q16 = s16(q)
    mag = (-q16) & 0xFFFF if q16 < 0 else q16
    return ("-" if q16 < 0 else "+") + f"{mag % 100000:05d}"[1:]


def units_on_map(r, screen):
    """Unit markers and labels: $6AFA (25 Mantas), $6C36 (17 Walruses), $6D74 (2 carrier)."""
    out = []
    level = r.s16(MAP_ZOOM_LEVEL)

    def obj_marker(kind, o):
        return marker(r, kind, r.s32(o + 0xA), r.s32(o + 0x12), (r.u16(o + 6) + 0x800) & 0xF000)

    if screen in (17, 25):
        manta = screen == 25
        mode, sel = (r.s16(0x6AA), r.s16(0x48C)) if manta else (r.s16(0x6B8), r.s16(0x566))
        for k in range(4):
            rec = (MANTA if manta else WALRUS) + REC * k
            o = OBJECTS + OBJ * ((2 if manta else 6) + k)
            st = r.s8(rec + 1)
            if st < 0:
                continue
            if st >= 3:
                m = obj_marker(0 if manta else 1, o)
                out.append(("manta" if manta else "walrus", k, m))
                if mode and (mode == 2 or k == sel) and level <= 1 and m is not None:
                    ax, ay = m[0], m[1]
                    lbl = ("MANTA " if manta else "WALRUS ") + chr(0x31 + k)
                    xy = coord_text(r.s32(o + 0xA)) + "," + coord_text(r.s32(o + 0x12))
                    out.append(("label", k, (lbl, ax + (22 >> level), ay - (22 >> level), 0x200 >> level),
                                (xy, ax + (12 >> level), ay - (10 >> level), 0x200 >> level)))
            if r.u8(rec) & 2:
                wx, wz = (rec + 0x1E, rec + 0x26) if manta else (rec + 0x1C, rec + 0x20)
                out.append(("waypoint", k, marker(r, 4 + k, r.s32(wx), r.s32(wz))))
        out.append(("carrier", 0, obj_marker(2, OBJECTS + OBJ * 10)))
        cur = (0x69E, 0x6A6) if manta else (0x6B0, 0x6B4)
        out.append(("cursor", 0, marker(r, 3, r.s32(cur[0]), r.s32(cur[1]))))
    elif screen == 2:
        if not r.s16(0x710):
            out.append(("cursor", 0, marker(r, 3, r.s32(0x6BE), r.s32(0x6C2))))
            if r.u8(0x640) & 2:
                out.append(("waypoint", 8, marker(r, 8, r.s32(0x64E), r.s32(0x652))))
        out.append(("carrier", 0, obj_marker(2, OBJECTS + OBJ * 10)))
    return out


def links_on_map(r):
    """$68D0 (screen 2 islands panel only): each network entry with a non-zero weight, from both
    ends. Colour: both player 7; player and other $C; both enemy 1; otherwise not drawn."""
    pos = [world_to_screen(r, r.s32(ISLANDS + ISLAND_SIZE * i + 2), r.s32(ISLANDS + ISLAND_SIZE * i + 6))
           for i in range(64)]
    out = []
    for i in range(64):
        n = 0x2239E + 0x20 * i
        oi = r.u8(OWNERS + i)
        for k in range(r.u16(n)):
            w, j = r.u16(n + 2 + 4 * k), r.u16(n + 4 + 4 * k) & 0x7FFF
            if not w:
                continue
            oj = r.u8(OWNERS + (j & 0xFFFF))
            t = (oi + oj) & 0xFF
            if t == 0:
                continue
            if t == 4:
                col = 1
            elif oj == 1:
                col = 7 if oi == 1 else 0xC
            elif oi == 1:
                col = 0xC
            else:
                continue
            out.append((i, j, col, (col, *pos[i], *pos[j & 0xFF])))
    return out


def island_counts_panel(r):
    """$699E + $CDF2: friendly islands by kind (economy +0 byte: 1 resource, 2 factory,
    3 defence) and the stockpile island's name ("NONE!" if [$D4A] < 0)."""
    by = [0, 0, 0, 0]
    for i in range(64):
        if r.u8(OWNERS + i) == 1:
            k = r.u8(ECONOMY + 0x32 * i)
            if k < 4: by[k] += 1
    st = r.s16(0xD4A)
    return {"factory": by[2], "resource": by[1], "defence": by[3],
            "stockpile": None if st < 0 else st, "stockpileName": "NONE!" if st < 0 else name(r, st)}


def route_distance(r, frm, to):
    """The info box's route figures: route word w ($67B40 + 128*frm + 2*to); negative = isolated;
    shown as (w << 16) / 1573 (divu; an overflowing quotient leaves 0 in the low word)."""
    w = r.s16(ROUTES + 0x80 * frm + 2 * to)
    if w < 0:
        return None
    q = (w << 16) // 0x625
    return q if q <= 0xFFFF else 0


def island_info(r):
    """$BB10: the island information box for island [$764] (the island nearest the map centre)."""
    i = r.s16(0x764)
    a = ISLANDS + ISLAND_SIZE * i
    owner = r.u8(0x74C)
    hx, hz = r.u16(a + 0xA), r.u16(a + 0xC)
    q = (hx * hz) // 10000
    area10 = None
    if q <= 0xFFFF:
        qs = s16(q)
        if qs >= 0:
            area10 = (qs // 250) & 0xFFFF
    info = {"island": i, "name": name(r, i), "x": r.s32(0x742), "z": r.s32(0x746), "area10": area10,
            "owner": owner, "kind": None, "distanceToBase": None, "distanceToStockpile": None,
            "reRouting": False}
    if owner != 1:
        return info
    kind = r.u8(ECONOMY + 0x32 * i) & 3
    info["kind"] = kind
    if kind:
        if r.u8(0x1235):
            info["reRouting"] = True
            return info
        info["distanceToBase"] = route_distance(r, 0, i) if r.s16(ROUTES + 2 * i) >= 0 else "ISOLATED"
    st = r.s16(0xD4A)
    if i != st and st >= 0:
        d = route_distance(r, i, st)
        info["distanceToStockpile"] = "ISOLATED" if d is None else d
    return info


def slider_bars(r, screen):
    """$7712: the cruise/altitude bars (height in pixels up from y $C3, 0..36)."""
    if screen == 25:
        q = int(r.s32(0x6A2) / 97)
        alt = min(s16(q), 36) if -0x8000 <= q <= 0x7FFF else min(s16(r.s32(0x6A2)), 36)
        cr = int(r.u16(0x69C) / 7)    # no clamp: the code tests the stale d3
        return {"altitude": alt, "cruise": cr}
    if screen == 17:
        return {"cruise": min(int(((r.u16(0x6AE) << 8) & 0xFFFF) / 0x2AA), 36)}
    if screen == 2 and not r.s16(0x710):
        return {"cruise": min(s16((r.s16(0x6BC) >> 2) + 8), 36)}
    return {}


# ---- expected draw output ---------------------------------------------------------------------

def num2(v):
    s = f"{v & 0xFFFF:05d}"[-2:] if v < 100 else chr(0x30 + v // 10) + chr(0x30 + v % 10)
    return (" " + s[1]) if s[0] == "0" else s


def num5_strip(v, n):
    """$9FAA (5 digits of a word) then $9FFE: up to n leading zeros skipped."""
    s = f"{v & 0xFFFF:05d}"
    k = 0
    while k < n and s[k] == "0":
        k += 1
    return s[k:]


def area_text(area10):
    """The info box area: tenths of a square km, e.g. 129 -> "12.9"."""
    s = num5_strip(area10, 3)
    return s[:-1] + "." + s[-1]


def info_box_lines(info):
    """The lines $BB10 prints (5x5 font, colour 8, x 52, y 32 + 6n), label and value joined."""
    out = ["NAME:             " + info["name"],
           "POSITION:         " + coord_text(info["x"]) + "," + coord_text(info["z"]),
           "AREA:             " + area_text(info["area10"] or 0) + " SQUARE KM",
           "CONTROL:          " + ["FREE", "FRIENDLY", "HOSTILE"][info["owner"]]]
    if info["owner"] != 1:
        return out
    out.append("ISLAND TYPE     : " + ["BASE", "RESOURCE", "FACTORY", "DEFENCE"][info["kind"]])
    if info["kind"]:
        d = info["distanceToBase"]
        if info["reRouting"]:
            out.append("DISTANCE TO BASE: RE-ROUTING")
            return out
        out.append("DISTANCE TO BASE: " + (d if d == "ISOLATED" else num5_strip(d, 4)))
    d = info["distanceToStockpile"]
    if d is not None:
        out.append("DISTANCE TO STOCKPILE: " + (d if d == "ISOLATED" else num5_strip(d, 4)))
    return out


def expected(r, screen):
    """(vector labels, 5x5 strings, rects, line segments) the render should draw."""
    vec, text, rects, segs = [], [], [], []
    if r.s16(0x766):                                    # info box (+ sliders)
        info = island_info(r)
        text += info_box_lines(info)
        return vec, text, rects, segs, info
    pts = [world_to_screen(r, r.s32(BORDER_POINTS + 8 * k), r.s32(BORDER_POINTS + 8 * k + 4)) for k in range(32)]
    segs += line_list(r, BORDER_LINES, pts)
    if screen == 2 and r.s16(0x710):
        for *_, seg in links_on_map(r):
            segs.append(seg)
        c = island_counts_panel(r)
        text += [f"FACTORY ISLANDS  {num2(c['factory'])}", f"RESOURCE ISLANDS {num2(c['resource'])}",
                 f"DEFENCE ISLANDS  {num2(c['defence'])}", "STOCKPILE", c["stockpileName"]]
    isl, nearest = islands_on_map(r)
    for d in isl:
        if d["label"]:
            vec.append((d["name"], *d["label"]))
        if d["rect"]:
            rects.append(d["rect"])
        for _, _, m in d["markers"]:
            if m: segs += m[2]
    c = copyright_label(r)
    if c:
        vec.append(c)
    for u in units_on_map(r, screen):
        if u[0] == "label":
            vec.append(u[2]); vec.append(u[3])
        elif u[2]:
            segs += u[2][2]
    return vec, text, rects, segs, nearest


# ---- the original under Unicorn ---------------------------------------------------------------

class MapTracer(Tracer):
    def __init__(self, ram):
        super().__init__(ram)
        self.rects, self.segs, self.vec = [], [], []
        uc = self.uc
        uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d: self._rect(), begin=0x5308, end=0x5308)
        uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d: self._lines(), begin=0x5C60, end=0x5C60)
        self.bars = []
        uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d: self._bar(), begin=0x546C, end=0x546C)

    def _bar(self):
        self.bars.append((s16(self.r(UC_M68K_REG_D0)), s16(self.r(UC_M68K_REG_D1)), s16(self.r(UC_M68K_REG_D2)),
                          self.u16(0x9DA)))

    def _rect(self):
        g = lambda reg: s16(self.r(reg))
        self.rects.append((g(UC_M68K_REG_D0), g(UC_M68K_REG_D1), g(UC_M68K_REG_D2), g(UC_M68K_REG_D3),
                           self.r(UC_M68K_REG_D4) & 0xFFFF))

    def _cv(self):
        a = self.r(UC_M68K_REG_A0)
        s = bytes(self.uc.mem_read(a, 64)).split(b"\0")[0]
        s = "".join(chr(c) for c in s if c >= 0x20)
        self.vec.append((s, s16(self.r(UC_M68K_REG_D1)), s16(self.r(UC_M68K_REG_D2)), self.r(UC_M68K_REG_D3) & 0xFFFF))

    def _lines(self):
        a0, a1 = self.r(UC_M68K_REG_A0), self.r(UC_M68K_REG_A1)
        n = self.uc.mem_read(a0, 1)[0]
        lst = bytes(self.uc.mem_read(a0 + 1, 3 * n))
        for k in range(n):
            col, i, j = lst[3 * k] & 0x7F, lst[3 * k + 1], lst[3 * k + 2]
            p = lambda q: tuple(s16(int.from_bytes(self.uc.mem_read(a1 + 4 * q + 2 * t, 2), "big")) for t in (0, 1))
            self.segs.append((col, *p(i), *p(j)))


def run_original(ram, screen):
    t = MapTracer(ram)
    t.uc.mem_write(0x718, screen.to_bytes(2, "big"))
    t.call(0x14E3C)
    return t


def compare(ram, screen, label):
    r = R(bytes(ram))
    t = run_original(ram, screen)
    vec, text, rects, segs, extra = expected(r, screen)
    bad = []
    if vec != t.vec:
        bad.append(f"labels: want {vec[:6]} got {t.vec[:6]}")
    got_text = [s for f, x, y, s in t.strings() if f == "5" and s != "EXIT"]
    if text != got_text:
        bad.append(f"text: want {text} got {got_text}")
    got_rects = t.rects[1:] if not r.s16(0x766) and screen == 2 and r.s16(0x710) else t.rects
    if not r.s16(0x766) and rects != got_rects:
        bad.append(f"rects: want {rects[:4]} got {got_rects[:4]} ({len(rects)}/{len(got_rects)})")
    if not r.s16(0x766):
        if segs != t.segs:
            k = next((k for k in range(min(len(segs), len(t.segs))) if segs[k] != t.segs[k]), None)
            bad.append(f"segs: {len(segs)}/{len(t.segs)} first diff {k}: "
                       f"{segs[k] if k is not None else ''} vs {t.segs[k] if k is not None else ''}")
        nearest = extra
        got = t.u16(0x764)
        if (nearest if nearest is not None else None) != (got if got < 0x8000 else None):
            bad.append(f"$764 nearest: want {nearest} got {got:04X}")
    sb = slider_bars(r, screen)
    want_bars = []
    if screen == 25:
        want_bars = [(0xF8, 0xC3, 0x9F, 0), (0x124, 0xC3, 0x9F, 0), (0xF8, 0xC3, 0xC3 - sb["altitude"], 2),
                     (0x124, 0xC3, 0xC3 - sb["cruise"], 2)]
    elif sb:
        want_bars = [(0x124, 0xC3, 0x9F, 0), (0x124, 0xC3, 0xC3 - sb["cruise"], 2)]
    if want_bars != t.bars:
        bad.append(f"bars: want {want_bars} got {t.bars}")
    if bad:
        print(f"{label} screen {screen}: MISMATCH")
        for b in bad: print("   ", b)
        return False
    return True


def perturb(ram, rng, screen):
    m = bytearray(ram)
    w16 = lambda a, v: m.__setitem__(slice(a, a + 2), (v & 0xFFFF).to_bytes(2, "big"))
    w32 = lambda a, v: m.__setitem__(slice(a, a + 4), (v & 0xFFFFFFFF).to_bytes(4, "big"))
    lvl = rng.choice([0, 0, 1, 1, 2, 3, 4, 5, 6, 8, 10])
    w16(0x758, lvl); w16(0x75A, 1 << lvl)
    isl = rng.randrange(64)
    ix, iz = struct.unpack_from(">ii", m, ISLANDS + ISLAND_SIZE * isl + 2)
    if rng.random() < 0.7:
        w32(0x75C, ix + rng.randint(-40000, 40000)); w32(0x760, iz + rng.randint(-40000, 40000))
    else:
        w32(0x75C, rng.randint(-0x7FFFFF, 0x7FFFFF)); w32(0x760, rng.randint(-0x7FFFFF, 0x7FFFFF))
    for _ in range(rng.randrange(12)):
        m[OWNERS + rng.randrange(64)] = rng.choice([0, 1, 2])
    for _ in range(rng.randrange(12)):
        e = ECONOMY + 0x32 * rng.randrange(64); m[e] = rng.randrange(4)
    if rng.random() < 0.3: w16(0x710, rng.choice([0, 1]))
    if rng.random() < 0.3: w16(0xD4A, rng.choice([-1, rng.randrange(64)]))
    if rng.random() < 0.3: m[0x1235] = rng.choice([0, 0xFF])
    if rng.random() < 0.3: w16(0x6AA, rng.randrange(3)); w16(0x6B8, rng.randrange(3))
    if rng.random() < 0.3: w16(0x48C, rng.randrange(4)); w16(0x566, rng.randrange(4))
    for k in range(4):
        if rng.random() < 0.3:
            for rec, o in ((MANTA + REC * k, 2 + k), (WALRUS + REC * k, 6 + k)):
                m[rec + 1] = rng.choice([0, 2, 3, 8, 8, 0xFF]); m[rec] = rng.randrange(256)
                b = OBJECTS + OBJ * o
                w32(b + 0xA, ix + rng.randint(-20000, 20000)); w32(b + 0x12, iz + rng.randint(-20000, 20000))
                w16(b + 6, rng.randrange(65536))
                w32(rec + 0x1E, ix + rng.randint(-20000, 20000)); w32(rec + 0x26, iz + rng.randint(-20000, 20000))
                w32(rec + 0x1C, ix + rng.randint(-20000, 20000)); w32(rec + 0x20, iz + rng.randint(-20000, 20000))
    if rng.random() < 0.5:
        b = OBJECTS + OBJ * 10
        w32(b + 0xA, ix + rng.randint(-30000, 30000)); w32(b + 0x12, iz + rng.randint(-30000, 30000))
        w16(b + 6, rng.randrange(65536))
        m[0x640] = rng.randrange(256)
        w32(0x64E, ix + rng.randint(-30000, 30000)); w32(0x652, iz + rng.randint(-30000, 30000))
        for a in (0x6BE, 0x69E, 0x6B0): w32(a, ix + rng.randint(-30000, 30000))
        for a in (0x6C2, 0x6A6, 0x6B4): w32(a, iz + rng.randint(-30000, 30000))
    if rng.random() < 0.3: w16(0x6BC, rng.randrange(0x71)); w16(0x6AE, rng.randrange(97)); w16(0x69C, rng.randrange(50, 251))
    if rng.random() < 0.3: w32(0x6A2, rng.randrange(250, 3501))
    if rng.random() < 0.3:          # route matrix entries (info box)
        for _ in range(20):
            w16(ROUTES + 2 * rng.randrange(64 * 64), rng.choice([-1, rng.randrange(0x8000), rng.randrange(1600)]))
    if rng.random() < 0.3:          # the stockpile panel's island markers
        w16(0x710, 1); m[OWNERS + isl] = 1; w16(0xD4A, isl); lvl = rng.randrange(3)
        w16(0x758, lvl); w16(0x75A, 1 << lvl)
        w32(0x75C, ix + rng.randint(-8000, 8000)); w32(0x760, iz + rng.randint(-8000, 8000))
    if rng.random() < 0.25:         # island info box on: centre on an island first
        w32(0x75C, ix); w32(0x760, iz)
    return bytes(m)


def info_box_state(ram):
    """Make [$766] = 1 the way $FAF4 does: run the map render once for $764/$742/$746."""
    t = run_original(ram, struct.unpack_from(">H", ram, 0x718)[0])
    m = bytearray(t.uc.mem_read(0, 0x80000))
    if struct.unpack_from(">h", m, 0x764)[0] < 0:
        return None
    m[0x75C:0x760] = m[0x742:0x746]; m[0x760:0x764] = m[0x746:0x74A]
    m[0x766:0x768] = b"\0\x01"
    return bytes(m)


DEFAULT_CAPTURES = ["units-frame/ram_2_2.bin", "units-frame/ram_11_17.bin", "units-frame/ram_16_25.bin",
                    "play/pass_late_0_in.bin", "play/pass_combat_0_in.bin", "play/pass_flight_0_in.bin",
                    "play/pass_supply_0_in.bin", "sim/ai_120000_0_in.bin", "action/pass_1500_0_in.bin"]


def check(paths=None, fuzz=30, seed=1):
    paths = paths or [ROOT / "emu/out" / p for p in DEFAULT_CAPTURES]
    rng = random.Random(seed)
    n = ok = 0
    for p in paths:
        ram = Path(p).read_bytes()[:0x80000]
        for screen in (2, 17, 25):
            m = bytearray(ram); m[0x718:0x71A] = screen.to_bytes(2, "big"); m[0x766:0x768] = b"\0\0"
            states = [bytes(m)] + [perturb(bytes(m), rng, screen) for _ in range(fuzz)]
            for k, st in enumerate(states):
                n += 1; ok += compare(st, screen, f"{Path(p).name}#{k}")
                if k % 3 == 0:
                    ib = info_box_state(st)
                    if ib is not None:
                        n += 1; ok += compare(ib, screen, f"{Path(p).name}#{k} info")
    print(f"{ok}/{n} states match")
    return ok == n


if __name__ == "__main__":
    args = sys.argv[1:]
    fuzz = 30
    if "--fuzz" in args:
        i = args.index("--fuzz"); fuzz = int(args[i + 1]); del args[i:i + 2]
    paths = args or None
    sys.exit(0 if check(paths, fuzz) else 1)
