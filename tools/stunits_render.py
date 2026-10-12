"""Unit layer: state the screens' render routines write, and the sound-event check.

RenderMixin.render_effects(screen) is the state the original's render for that screen
(table $144A0, run by screen_dispatch $14472 after every pass) and its panel draw callbacks
leave in memory, without the drawing: the app doesn't run the original render, so it calls
this once per frame for the screen shown (docs/units.md §10.8). For the 3D views that is the
camera placement, the pipeline's visible-object collection ($7BF6) and depth-tree draw ($62D4)
as far as they write state (island context, runway entries, object pairs $A68-$A73, attached
vehicles' order in the carrier's BSP), and the overlays' writes.

check_sound_events compares the model's sound events (docs/units.md §17: ("sound", id),
("explosion", level, object) / ("explosion", level, x, y, z), the ping element of
("message", text, ping)) with the calls the original makes ($20B90, $20C30, $20C08, the
message routines $A0DE/$A128 and the ping they play, the game-over tune), pass by pass.
"""
import random
from collections import Counter

import stsim  # noqa: F401
import stunits  # noqa: F401
from stsim import s16, s32, w16  # noqa: F401


MAP_SCREENS = (2, 17, 25)
MAP_ZOOM, MAP_X, MAP_Z, MAP_INFO = 0x75A, 0x75C, 0x760, 0x766
NEAREST, NEAREST_D2, NEAREST_OWNER, NEAREST_X, NEAREST_Z, MARKER_ZOOM = 0x764, 0x73E, 0x74C, 0x742, 0x746, 0x750
DRONE_TYPE = 66                       # screen 11's drone elements $42A4E + $16 k have types 66-69
FLAG_TABLE = 0x40EB2
TITLE_TIMER, TITLE_FLAG = 0x93A, 0x124C

# The 3D views ($7BF6 scene_collect_visible, $62D4 draw_depth_tree; docs/units.md §10.8).
CAMERA, VIEWPORT = 0x9FA, 0x6FE
CULL, LOD = 0x8598, 0x8B58            # per type: range, -, min Z, radius / near, mid, near/mid/far model
CAM_PAIRS = 0xA74                     # (sin, cos) of -pitch, -yaw, -roll of the camera ($A484-$A4A4)
OBJ_PAIRS = 0xA68                     # pitch $A68, yaw $A6C, roll $A70 of the last box drawn
ISLAND_REL = 0xA4A                    # island origin - camera: x $A4A, y $A4E, z $A52 (longs)
RECT = 0xA56                          # the island's partition rectangle: x0 $A56, z0 $A5A, x1 $A5E, z1 $A62
LOD_BIAS, RANGE_BIAS = 0xA94, 0xA98      # the turret zoom's biases
HIDE_OBJECTS, HIDE_DEBRIS, OBJECT_COUNT = 0xA46, 0xA47, 0xA48
CAMERA_HIGH = 0x8A6                   # $7BF6: camera y >= $50 (st.b), kept between frames
RADAR_ON = 0x1230
MODEL_LIST = 0x64CFE                  # the damage diagram / title model list (stunits_controls)
REDRAW = 0x95C                        # $95C bay-status redraw countdown, $95E/$960 arming panel redraws
# Per model ($4A06A): the object pairs its basis routine leaves in $A68-$A73 (B box $A78C/$A89A/
# $ADA4, C carrier with turret $AA78/$AABE/$AB42, T turret $ABE8, W Walrus $AC16, 2 two boxes
# $AF4E; '.' none: empty, disc, trees $AA24, island $A660, overlays $A8DA).
MODEL_PAIRS = (".CCCBBWB.BBBBBBBBBBBBBB.....BBB.BBBBBB2BBBBBBBBBB2BBBBBBBBBBBBBBBBBBBBBBBBB.........T"
               "BBBBBBBBBBBBBB")
SHIFT_MAX = 15                        # $8578: the frustum shift table covers shx 0-15
STATS = Counter()                     # what the checks exercised (printed by them)
CARRIER_MODELS = {2: 0x43D3C, 3: 0x44264}   # model records (handler, dist, basis $AABE/$AB42, ...)

# State the renders write that the other regions don't cover.
for _region in (("map render $73E", NEAREST_D2, 4), ("map render owner $74C", NEAREST_OWNER, 1),
                ("map render $750", MARKER_ZOOM, 2), ("title timer $93A", TITLE_TIMER, 4),
                ("title flag $124C", TITLE_FLAG, 2),
                ("object lod table $8B58", LOD, 84 * 16),
                ("camera high $8A6", CAMERA_HIGH, 1),
                ("model list $64CFE", MODEL_LIST, 0x12 + 9 * 0x40),
                ("carrier models 2-3 $43D3C", CARRIER_MODELS[2], 0x448C0 - CARRIER_MODELS[2]),
                ("bay redraw countdowns $95C", REDRAW, 6)):
    if _region not in stunits.UNIT_REGIONS:
        stunits.UNIT_REGIONS.append(_region)
stunits.UNIT_INPUT_REGIONS |= {"object lod table $8B58", "carrier models 2-3 $43D3C"}


def map_div(d: int, zoom: int) -> int:
    """$65C4: (d asr 8) divs.w zoom, floored (decremented when the remainder is negative,
    unless that overflows); a quotient beyond a word gives $7FFF / -$7FFF by the dividend's
    sign."""
    d4 = s32(d) >> 8
    z = s16(zoom)
    if z == 0:
        raise NotImplementedError("map zoom 0 (divs by zero)")
    q = abs(d4) // abs(z)
    if (d4 < 0) != (z < 0):
        q = -q
    if not -0x8000 <= q <= 0x7FFF:
        return -0x7FFF if d4 < 0 else 0x7FFF
    if d4 - q * z < 0 and q != -0x8000:
        q -= 1
    return q


# ---- the 3D pipeline's fixed-point maths (docs/memory-map.md "Bridge view camera and projection")
def _hi4(v: int) -> int:
    """add.l d,d twice then swap: the high word of 4 v (32-bit wrap), signed."""
    return s16(((v * 4) & 0xFFFFFFFF) >> 16)


def _abs32(v: int) -> int:
    """move.l; bpl; neg.l: |v| as an unsigned long ($80000000 stays)."""
    return (-v if v < 0 else v) & 0xFFFFFFFF


def rot_a(m, u: int, v: int, pair: int) -> tuple:
    """$A4F4/$A4EA/$A5BC/$A5B2 (pair at `pair`: sin, cos) on words: u' = u c - v s,
    v' = v c + u s, each the high word of 4 times the long."""
    s, c = m.s16(pair), m.s16(pair + 2)
    u, v = s16(u), s16(v)
    return _hi4(u * c - v * s), _hi4(v * c + u * s)


def rot_b(m, x: int, z: int, pair: int) -> tuple:
    """$A558/$A54E: x' = x c + z s, z' = z c - x s."""
    s, c = m.s16(pair), m.s16(pair + 2)
    x, z = s16(x), s16(z)
    return _hi4(x * c + z * s), _hi4(z * c - x * s)


def rot_long(m, f, x: int, y: int, pair: int) -> tuple:
    """$A4B4/$A518/$A57C: halve both longs (asr) until within +-$4844, rotate the words,
    ext.l and shift back (asl.l)."""
    x, y, k = s32(x), s32(y), 0
    while not (-0x4844 <= x <= 0x4844 and -0x4844 <= y <= 0x4844):
        x, y, k = x >> 1, y >> 1, k + 1
    a, b = f(m, x, y, pair)
    return s32(a << k), s32(b << k)


def cam_long(m, dx: int, dy: int, dz: int) -> tuple:
    """$7C68: a camera-relative world vector (longs) to camera space: yaw, pitch, roll by the
    camera pairs $A78, $A74, $A7C."""
    x, z = rot_long(m, rot_b, dx, dz, CAM_PAIRS + 4)
    y, z = rot_long(m, rot_a, dy, z, CAM_PAIRS)
    x, y = rot_long(m, rot_a, x, y, CAM_PAIRS + 8)
    return x, y, z


def cam_word(m, x: int, y: int, z: int) -> tuple:
    """$7DB0: an island entry's local words to camera space (no prescale)."""
    x, z = rot_b(m, x, z, CAM_PAIRS + 4)
    y, z = rot_a(m, y, z, CAM_PAIRS)
    x, y = rot_a(m, x, y, CAM_PAIRS + 8)
    return x, y, z


class DepthTree:
    """depth_tree_insert $6246 (and $622E / $623A, which start below the root's far (+6) /
    near (+4) link): 18-byte nodes keyed on camera Z; a key <= the node's goes near (+4),
    else far (+6). draw_depth_tree $62D4 walks far side, node, near side."""

    def __init__(self):
        self.nodes = []                     # [key, near, far, object, X, Y]

    def insert(self, key: int, obj: int, x: int, y: int, start=None) -> None:
        if start is None and len(self.nodes) == 0x80:
            raise NotImplementedError("depth tree full: trap #12 ($6254)")
        if len(self.nodes) >= 0x80:
            raise NotImplementedError("more than 128 depth-tree nodes (the buffer at $58E2A overflows)")
        n = len(self.nodes)
        if n == 0 and start is not None:
            raise NotImplementedError("forced depth-tree insert without a root")
        self.nodes.append([key, None, None, obj, x, y])
        if n == 0:
            return
        if start is None:
            cur = 0
        else:
            side = 2 if start == "far" else 1
            if self.nodes[0][side] is None:
                self.nodes[0][side] = n
                return
            cur = self.nodes[0][side]
        while True:
            side = 1 if key <= self.nodes[cur][0] else 2
            if self.nodes[cur][side] is None:
                self.nodes[cur][side] = n
                return
            cur = self.nodes[cur][side]

    def in_order(self) -> list:
        out, stack, cur = [], [], 0 if self.nodes else None
        while stack or cur is not None:
            while cur is not None:
                stack.append(cur)
                cur = self.nodes[cur][2]
            cur = stack.pop()
            out.append(self.nodes[cur])
            cur = self.nodes[cur][1]
        return out


class RenderMixin:
    """The state the screens' render routines write (docs/units.md §10.7). The app doesn't run
    the original render, so it calls render_effects() once per frame after the pass, for the
    screen shown."""

    def render_effects(self, screen: int = None) -> None:
        """screen_dispatch $14472 for [$718] (or `screen`) without the drawing: the debug-mode
        keys ($210F0, $17874), then the screen's render ($144A0) and the panel callbacks it
        runs (RENDER_EFFECTS: the 3D views 0, 1, 8, 9, 10, 16, 18, 19, 24, 26, 27, 33 with
        their camera and pipeline writes, the maps 2, 17, 25, messages 4, defence radar 11;
        the other screens' renders write no state). With the debug freeze on ([$1189] and
        key $866 held) game_main skips the render (and frame_flip)."""
        m = self.mem
        if screen is None:
            screen = m.u16(stunits.SCREEN)
        if m.u8(0x1189) and m.u8(0x866):                   # $413E: the debug freeze
            return
        if m.u8(0x1189):                                   # $210F0: debug display key
            m.set8(0x871, m.u8(0x871) & 0xFE)
        fn = RENDER_EFFECTS.get(screen)
        if fn:
            getattr(self, fn)()
        # $17874: in a running game in debug mode, key $869 toggles the frame-rate display.
        if m.u8(0x462) and not m.u8(0x461) and m.u8(0x1189) and m.u8(0x869) & 1:
            m.set8(0x869, m.u8(0x869) & 0xFE)
            m.set8(0x122D, ~m.u8(0x122D) & 0xFF)

    def map_render_effects(self) -> None:
        """$14E3C (screens 2, 17, 25) without the drawing. With the island information box up
        ([$766] != 0) it only draws. Otherwise $65E2: [$764] high byte = $FF (st.b), [$73E] =
        2000000, [$750] = min(zoom, 8); on the carrier map in islands mode ([$710] != 0) the
        islands panel's recount $CDF2 ([$D54] friendly, [$D56] hostile, [$D58 + 2 kind] for
        each friendly island's economy +0 byte; $D58 itself isn't cleared); then each island
        within +-255 px of the centre (unsigned, after neg) whose mx^2 + my^2 (mulu) is below
        [$73E] (unsigned) becomes the nearest: [$73E], [$764] index, [$74C] owner, [$742/$746]
        position. Last, the unit markers leave their turn pair in $A6C/$A6E: on screen 2 the
        carrier's ((heading + $800) & $F000) sin/cos; on 17/25 the cursor's (0, $4000)."""
        m = self.mem
        if m.u16(MAP_INFO):
            return
        m.set8(NEAREST, 0xFF)
        m.set32(NEAREST_D2, 0x1E8480)
        zoom = m.u16(MAP_ZOOM)
        m.set16(MARKER_ZOOM, 8 if s16(zoom) > 8 else zoom)
        screen = m.u16(stunits.SCREEN)
        if screen == 2 and m.u16(0x710):
            self.island_counts()
        cx, cz = m.u32(MAP_X), m.u32(MAP_Z)
        for i in range(64):
            isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i
            mx = map_div(s32(m.u32(isl + 2) - cx), zoom)
            my = map_div(s32(-s32(m.u32(isl + 6) - cz)), zoom)
            ax, ay = (-mx) & 0xFFFF if mx < 0 else mx, (-my) & 0xFFFF if my < 0 else my
            if ax >= 0x100 or ay >= 0x100:
                continue
            d2 = ax * ax + ay * ay
            if d2 >= m.u32(NEAREST_D2):
                continue
            m.set32(NEAREST_D2, d2)
            m.set16(NEAREST, i)
            m.set8(NEAREST_OWNER, m.u8(stsim.OWNERS + i))
            m.set32(NEAREST_X, m.u32(isl + 2))
            m.set32(NEAREST_Z, m.u32(isl + 6))
        if screen == 2:
            d7 = w16(m.u16(stunits.CARRIER + 6) + 0x800) & 0xF000
            m.set16(0xA6C, m.u16(stunits.SINE + (d7 >> 3)))
            m.set16(0xA6E, m.u16(stunits.SINE + (w16(d7 + 0x4000) >> 3)))
        else:
            m.set32(0xA6C, 0x4000)

    def island_counts(self) -> None:
        """$CDF2: [$D54] friendly islands, [$D56] hostile, and per friendly island
        [$D58 + 2 * (economy +0 byte)] + 1, after clearing $D54, $D56, $D5A, $D5C, $D5E."""
        m = self.mem
        for a in (0xD54, 0xD56, 0xD5A, 0xD5C, 0xD5E):
            m.set16(a, 0)
        for i in range(64):
            o = m.u8(stsim.OWNERS + i)
            if o == 1:
                m.set16(0xD54, m.u16(0xD54) + 1)
                a = 0xD58 + 2 * m.u8(stsim.ECONOMY + stsim.ECONOMY_SIZE * i)
                m.set16(a, m.u16(a) + 1)
            elif o:
                m.set16(0xD56, m.u16(0xD56) + 1)

    # ---- the 3D pipeline: what $7BF6 and the depth-tree draw leave in the state ------------
    def _pair(self, addr: int, angle: int) -> None:
        """$A438: (sin, cos) of a 16-bit angle (andi.b #$F0: the low 4 bits dropped) at addr."""
        m = self.mem
        a = angle & 0xFFF0
        m.set16(addr, m.u16(stunits.SINE + (a >> 3)))
        m.set16(addr + 2, m.u16(stunits.SINE + (w16(a + 0x4000) >> 3)))

    def scene_collect(self) -> tuple:
        """scene_collect_visible $7BF6 for the camera [$9FA] -> (depth tree, radar list or
        None, n_flat). State it writes: the camera pairs $A74-$A7F; with an island buffer
        (+$3C) holding entries, the island's camera-relative origin $A4A/$A4E/$A52; with its
        plateau (entry 0) visible, the partition rectangle $A56-$A65, [$8A6] = camera y >=
        $50 and, when that is set, the runway entries 1..n (n = header +$10): bit 2 of +1
        cleared, then set with their camera X, Y, Z in +$1E/+$22/+$26 when visible. The radar
        list ($5892A, while [$1230]) and the tree are drawing scratch, returned."""
        m = self.mem
        tree = DepthTree()
        radar = [] if m.u8(RADAR_ON) else None
        shx = m.u16(m.u32(VIEWPORT) + 8)            # $8560 patches the frustum shift
        if shx > SHIFT_MAX:
            raise NotImplementedError(f"viewport shx {shx} beyond the shift table $8578")
        cam = m.u32(CAMERA)
        for k in range(3):
            self._pair(CAM_PAIRS + 4 * k, -m.u16(cam + 4 + 2 * k))
        cx, cy, cz = (s32(m.u32(cam + o)) for o in (0xA, 0xE, 0x12))
        a98 = m.u32(RANGE_BIAS)

        def cull(typ):
            b = CULL + s16(w16(typ << 4))
            return m.u32(b), s32(m.u32(b + 8)), m.u32(b + 12)

        def in_range(typ, dx, dz):
            d5 = (cull(typ)[0] + a98) & 0xFFFFFFFF
            return _abs32(dx) <= d5 and _abs32(dz) <= d5

        def visible(typ, x, y, z):
            """Z >= min Z, then the frustum test $853A."""
            _, min_z, radius = cull(typ)
            if z < min_z:
                return False
            d5 = max(z, 0)
            d5 = s32(d5 << (7 - shx)) if shx <= 7 else d5 >> min(shx - 7, 8)
            if s32(_abs32(x) - radius) > d5:
                return False
            return s32(_abs32(y) - radius) <= d5 >> 1

        def radar_add(typ, x, z):
            if radar is not None and len(radar) < 0x7F:          # $6324
                radar.append((typ, x, z))

        def entry(e, side, rel, origin):
            """$818A / $829A: an island entry, culled and inserted."""
            f = m.u8(e + 1)
            if not (f & 1 and f & 2):
                return
            lx, ly, lz = m.s16(e + 0xA), m.s16(e + 0xC), m.s16(e + 0xE)
            typ = m.u16(e + 2)
            if not in_range(typ, s32(lx + rel[0]), s32(lz + rel[2])):
                return
            x, y, z = cam_word(m, lx, ly, lz)
            x, y, z = s32(x + origin[0]), s32(y + origin[1]), s32(z + origin[2])
            radar_add(typ, x, z)
            if visible(typ, x, y, z):
                tree.insert(z, e, x, y, side)

        def check_entry(e, buf):
            if not buf + 0x12 <= e < buf + 0x12 + 0x40 * (64 if buf in stunits.ISLAND_BUFFERS else 9):
                raise NotImplementedError(f"island entry ${e:05X} outside the buffer ${buf:05X}")

        rect = None
        n_flat = None
        mode = "plain"                       # world objects and debris: plain / inside / outside
        cam_out = entry_side = None
        buf = m.u32(cam + 0x3C)
        if buf:
            if not (buf in stunits.ISLAND_BUFFERS or buf == MODEL_LIST):
                raise NotImplementedError(f"camera island buffer ${buf:X}")
            n_flat = m.u16(buf + 0x10)
            count = m.u16(buf + 0xE)
            if count:
                rel = (s32(m.u32(buf + 2) - cx), s32(m.u32(buf + 6) - cy), s32(m.u32(buf + 0xA) - cz))
                m.set32(ISLAND_REL, rel[0])
                m.set32(ISLAND_REL + 8, rel[2])
                m.set32(ISLAND_REL + 4, rel[1])
                origin = cam_long(m, *rel)
                e0 = buf + 0x12
                if m.u8(buf + 1) & 0x80:                         # $824A: every entry sorted
                    for k in range(count):
                        check_entry(e0 + 0x40 * k, buf)
                        entry(e0 + 0x40 * k, None, rel, origin)
                else:
                    d7 = count - 1
                    f = m.u8(e0 + 1)
                    t0 = m.u16(e0 + 2)
                    root = False
                    if f & 1 and f & 2 and in_range(t0, rel[0], rel[2]):
                        radar_add(t0, origin[0], origin[2])
                        root = visible(t0, *origin)
                    if not root:                                 # $8238: entries past the runway ones
                        d7 = w16(d7 - n_flat)
                        e = (e0 + s16(w16((n_flat + 1) * 0x40))) & 0xFFFFFFFF
                        while True:
                            d7 = w16(d7 - 1)
                            if d7 == 0xFFFF:
                                break
                            check_entry(e, buf)
                            entry(e, None, rel, origin)
                            e += 0x40
                    else:
                        tree.insert(origin[2], e0, origin[0], origin[1])
                        hx = w16(m.u16(e0 + 0x1E) - 0x50)
                        hz = w16(m.u16(e0 + 0x20) - 0x50)
                        rect = (s32(rel[0] - hx), s32(rel[2] - hz), s32(rel[0] + hx), s32(rel[2] + hz))
                        for k, v in enumerate(rect):
                            m.set32(RECT + 4 * k, v)
                        e = e0 + 0x40
                        d7 = w16(d7 - 1)
                        high = cy >= 0x50
                        m.set8(CAMERA_HIGH, 0xFF if high else 0)
                        if not high:
                            d7 = w16(d7 - n_flat)
                            e = (e + s16(w16(n_flat * 0x40))) & 0xFFFFFFFF
                        else:
                            for _ in range(n_flat):                  # $7D5A: the runway entries
                                check_entry(e, buf)
                                m.set8(e + 1, m.u8(e + 1) & 0xFB)
                                f = m.u8(e + 1)
                                if f & 1 and f & 2:
                                    lx, ly, lz = m.s16(e + 0xA), m.s16(e + 0xC), m.s16(e + 0xE)
                                    typ = m.u16(e + 2)
                                    if in_range(typ, s32(lx + rel[0]), s32(lz + rel[2])):
                                        x, y, z = cam_word(m, lx, ly, lz)
                                        x, y, z = s32(x + origin[0]), s32(y + origin[1]), s32(z + origin[2])
                                        if visible(typ, x, y, z):
                                            STATS["runway entries"] += 1
                                            m.set8(e + 1, f | 4)
                                            m.set32(e + 0x1E, x)
                                            m.set32(e + 0x22, y)
                                            m.set32(e + 0x26, z)
                                e += 0x40
                            d7 = w16(d7 - n_flat)
                        cam_out = self._outcode(rect, 0, 0)
                        if cam_out:                              # $7FCC
                            mode, entry_side = "outside", "near" if high else "far"
                        else:
                            mode, entry_side = "inside", "near"
                        if not d7 & 0x8000:
                            for _ in range(d7 + 1):
                                check_entry(e, buf)
                                entry(e, entry_side, rel, origin)
                                e += 0x40

        def side_of(dx, dz):
            STATS[f"partition {mode}"] += 1
            if mode == "plain":
                return None
            oc = self._outcode(rect, dx, dz)
            if mode == "inside":
                return "far" if oc else "near"
            if not oc:
                return entry_side
            return "near" if oc & cam_out else "far"

        n = m.u16(OBJECT_COUNT)
        if m.u8(HIDE_OBJECTS) or not n:                          # $8402: debris sorted plainly
            mode = "plain"
        else:                                                    # world objects
            if n > 64:
                raise NotImplementedError(f"world object count [$A48] = {n}")
            for i in range(n):
                a = stunits.OBJECTS + stunits.OBJECT_SIZE * i
                f = m.u8(a + 1)
                if not (f & 1 and f & 2) or f & 4:
                    continue
                dx, dy, dz = (s32(m.u32(a + o) - c) for o, c in ((0xA, cx), (0xE, cy), (0x12, cz)))
                typ = m.u16(a + 2)
                if not in_range(typ, dx, dz):
                    continue
                side = side_of(dx, dz)
                x, y, z = cam_long(m, dx, dy, dz)
                radar_add(typ, x, z)
                if visible(typ, x, y, z):
                    tree.insert(z, a, x, y, side)
        if not m.u8(HIDE_DEBRIS):                                # the debris bursts $11D8
            for k in range(4):
                b = stunits.DEBRIS + 0x12 * k
                if not m.u8(b) & 1 or not m.u32(b + 0xE):
                    continue
                dx, dy, dz = (s32(m.u32(b + o) - c) for o, c in ((2, cx), (6, cy), (0xA, cz)))
                if _abs32(dx) >> 16 or _abs32(dz) >> 16 or _abs32(dy) >> 16:
                    continue
                side = side_of(dx, dz)
                origin = cam_long(m, dx, dy, dz)
                s = m.u32(b + 0xE)
                while s:                                         # $84A4: the shards
                    if not stunits.SHARDS <= s < stunits.SHARDS + 64 * 0x1A:
                        raise NotImplementedError(f"debris shard pointer ${s:X}")
                    lx, ly, lz = m.s16(s + 0xA), m.s16(s + 0xC), m.s16(s + 0xE)
                    typ = m.u16(s + 2)
                    if in_range(typ, s32(lx + dx), s32(lz + dz)):
                        x, y, z = cam_word(m, lx, ly, lz)
                        x, y, z = s32(x + origin[0]), s32(y + origin[1]), s32(z + origin[2])
                        if visible(typ, x, y, z):
                            STATS["debris shards"] += 1
                            tree.insert(z, s, x, y, side)
                    s = m.u32(s + 0x16)
        return tree, radar, n_flat

    @staticmethod
    def _outcode(rect, x: int, z: int) -> int:
        """$7FA0: bit 0 x < x0, 1 x > x1, 2 z < z0, 3 z > z1 (signed longs)."""
        x0, z0, x1, z1 = rect
        return (x < x0) | (x > x1) << 1 | (z < z0) << 2 | (z > z1) << 3

    def scene_draw(self, tree: DepthTree, radar, n_flat) -> None:
        """draw_depth_tree $62D4 without the drawing: the island plateau (type 8) is followed by
        its runway entries with bit 2 set while [$8A6]; each object's model ($7B72: LOD by Z -
        [$A94]) leaves its basis routine's object pairs in $A68-$A73; the carrier models 2/3
        draw the vehicles on the lift [$6D6] and in the dock [$6DA] inside their BSP."""
        m = self.mem
        for key, _, _, obj, x, y in tree.in_order():
            typ = m.u16(obj + 2)
            self._draw_object(obj, typ, key, (x, y, key), radar)
            if typ == 8 and m.u8(CAMERA_HIGH) and n_flat:
                e = obj
                for _ in range(n_flat):
                    e += 0x40
                    if m.u8(e + 1) & 4:
                        self._draw_object(e, m.u16(e + 2), s32(m.u32(e + 0x26)), None, radar)

    def _lod_model(self, typ: int, z: int) -> int:
        """$7B72: d = Z - [$A94]; <= near -> near model, <= mid -> mid, else far."""
        m = self.mem
        b = LOD + s16(w16(typ << 4))
        d = s32(z - m.u32(LOD_BIAS))
        model = m.u16(b + 8) if d <= s32(m.u32(b)) else m.u16(b + 10) if d <= s32(m.u32(b + 4)) else m.u16(b + 12)
        if model >= len(MODEL_PAIRS):
            raise NotImplementedError(f"type {typ}: model {model} beyond the model table")
        return model

    def _draw_object(self, obj: int, typ: int, z: int, origin, radar) -> None:
        model = self._lod_model(typ, z)
        self._model_pairs(model, obj)
        if model in (2, 3):
            self._attachments(model, obj, origin, radar)

    def _model_pairs(self, model: int, obj: int) -> None:
        """The object pairs the model's basis routine leaves: $A472 (yaw - camera yaw, $A6C),
        $A454 (pitch, $A68), $A468 (roll, $A70); the carrier's and turret's second boxes are
        fake records patched with the turret angles $6CA/$6CC."""
        m = self.mem
        kind = MODEL_PAIRS[model]
        if kind == ".":
            return
        cam_yaw = m.u16(m.u32(CAMERA) + 6)
        pitch, yaw, roll = m.u16(obj + 4), m.u16(obj + 6), m.u16(obj + 8)
        if kind == "C":
            pitch, yaw, roll = m.u16(0x6CA), w16(yaw + m.u16(0x6CC)), 0
        elif kind == "T":                    # $ABE8 via $A946: no camera yaw
            pitch, yaw, roll, cam_yaw = m.u16(0x6CA), m.u16(0x6CC), 0, 0
        elif kind == "W":
            yaw = w16(yaw + m.u16(obj + 0x26))
        elif kind == "2":
            roll = 0
        self._pair(OBJ_PAIRS + 4, w16(yaw - cam_yaw))
        self._pair(OBJ_PAIRS, pitch)
        self._pair(OBJ_PAIRS + 8, roll)

    def _attachments(self, model: int, carrier: int, origin, radar) -> None:
        """$6346 from the carrier model's BSP: slot 1 = [$6D6] (the lift), slot 2 = [$6DA]
        (the dock), each at its own position (long camera transform from [$9FA]), radar-listed
        while [$1230], drawn with the LOD model for its Z. Both hang off the BSP root (plane 0
        of model 2, plane 3 of model 3), slot 1 first when that plane faces the camera."""
        m = self.mem
        a, b = m.u32(0x6D6), m.u32(0x6DA)
        if not a and not b:
            return
        order = (1, 2)
        if a and b:
            STATS["both attachments"] += 1
            if not self._carrier_plane(model, carrier, origin):
                order = (2, 1)
                STATS["dock before lift"] += 1
        cam = m.u32(CAMERA)
        for slot in order:
            o = m.u32(0x6D6 + 4 * (slot - 1))
            if not o:
                continue
            x, y, z = cam_long(m, *(s32(m.u32(o + k) - m.u32(cam + k)) for k in (0xA, 0xE, 0x12)))
            if radar is not None and len(radar) < 0x7F:
                radar.append((m.u16(o + 2), x, z))
            self._draw_object(o, m.u16(o + 2), z, (x, y, z), radar)

    def _carrier_plane(self, model: int, obj: int, origin) -> bool:
        """$98E6/$9902 for the BSP root plane of carrier model 2 (plane 0) or 3 (plane 3): the
        model's camera-space vertices (basis $AABE/$AB42 then the vertex ops $5A02), the
        plane's normal (V[b] - V[a]) x (V[c] - V[a]) (halved, word products) against -(origin
        + V[a]), both scaled down to words: True (spl) when the dot product is >= 0."""
        if origin is None:
            raise NotImplementedError("carrier model drawn as a runway entry")
        m = self.mem
        rec = CARRIER_MODELS[model]
        V = {}
        a0 = self._carrier_basis(model, obj, rec + 12, V)    # handler, dist, basis, params
        a0 = self._vertex_ops(a0, V)
        a0 += 2                                          # nverts
        n = m.u8(a0)
        plane = 0 if model == 2 else 3
        if plane >= n:
            raise NotImplementedError(f"model {model} has no plane {plane}")
        ia, ib, ic = (m.u8(a0 + 1 + 3 * plane + k) for k in range(3))
        for i in (ia, ib, ic):
            if i not in V:
                raise NotImplementedError(f"model {model} plane vertex {i} not built")
        va, vb, vc = V[ia], V[ib], V[ic]
        d = [(vb[k] - va[k]) >> 1 for k in range(3)] + [(vc[k] - va[k]) >> 1 for k in range(3)]
        w = [s16(v) for v in d]
        nx = s32(w[1] * w[5] - w[2] * w[4])
        ny = s32(w[2] * w[3] - w[0] * w[5])
        nz = s32(w[0] * w[4] - w[1] * w[3])
        p = [s32(-s32(origin[k] + va[k])) for k in range(3)]

        def fit(v):
            while any(s16(c) != c for c in v):
                v = [c >> 1 for c in v]
            return v
        p, nrm = fit(p), fit([nx, ny, nz])
        return s32(nrm[0] * p[0] + nrm[1] * p[1] + nrm[2] * p[2]) >= 0

    def _box(self, a0: int, obj_pairs: tuple, V: dict, base: int) -> int:
        """$A78C: the object pairs (pitch, yaw - camera yaw, roll), then the box of half
        extents (sx, sy, sz) at a0 rotated into camera space: corners V[base + $F8..$FF]."""
        m = self.mem
        pitch, yaw, roll = obj_pairs
        self._pair(OBJ_PAIRS + 4, yaw)
        self._pair(OBJ_PAIRS, pitch)
        self._pair(OBJ_PAIRS + 8, roll)
        sx, sy, sz = m.s16(a0), m.s16(a0 + 2), m.s16(a0 + 4)

        def chain(d0, d1):
            """(x, y) already rolled, sz: object pitch, object yaw, camera pitch, camera roll."""
            a2 = d0
            d0, d1 = rot_a(m, d1, sz, OBJ_PAIRS)
            a3 = d0
            d0, d1 = rot_b(m, a2, d1, OBJ_PAIRS + 4)
            a2 = d0
            d0, d1 = rot_a(m, a3, d1, CAM_PAIRS)
            a3 = d1
            d0, d1 = rot_a(m, a2, d0, CAM_PAIRS + 8)
            return d0, d1, a3
        r0, r1 = rot_a(m, sx, sy, OBJ_PAIRS + 8)
        fa = chain(s16(-r0), s16(-r1))
        V[base + 0xFA] = fa
        V[base + 0xFC] = tuple(s16(-c) for c in fa)
        q0, q1 = rot_a(m, sx, s16(-sy), OBJ_PAIRS + 8)
        f9 = chain(q0, q1)
        V[base + 0xF9] = f9
        V[base + 0xFF] = tuple(s16(-c) for c in f9)
        fd = chain(r0, r1)
        V[base + 0xFD] = fd
        V[base + 0xFB] = tuple(s16(-c) for c in fd)
        d = [s16(f9[k] - fa[k]) for k in range(3)]
        V[base + 0xF8] = tuple(s16(-fd[k] + d[k]) for k in range(3))     # [$FB] + d (the regs hold -FD)
        V[base + 0xFE] = tuple(s16(fd[k] - d[k]) for k in range(3))
        return a0 + 6

    def _carrier_basis(self, model: int, obj: int, a0: int, V: dict) -> int:
        """$AABE (model 2) / $AB42 (model 3): $A89A (the hull box and its Q15 list, edge
        vectors $ADA/$AE0/$AE6), the lift platform vertices from $DD8 (and $DD6), then the
        turret's two boxes (heading carrier yaw + [$6CC], the second also pitched by [$6CA])
        at V[$F0..] and V[$E8..]. Returns the address after the basis parameters."""
        m = self.mem
        cam_yaw = m.u16(m.u32(CAMERA) + 6)
        yaw = m.u16(obj + 6)
        a0 = self._box(a0, (m.u16(obj + 4), w16(yaw - cam_yaw), m.u16(obj + 8)), V, 0)
        f8, f9, fb, ff = V[0xF8], V[0xF9], V[0xFB], V[0xFF]
        e1 = [s16(f9[k] - f8[k]) for k in range(3)]       # $AE6
        e2 = [s16(f8[k] - fb[k]) for k in range(3)]       # $ADA
        e3 = [s16(ff[k] - fb[k]) for k in range(3)]       # $AE0
        n = m.u16(a0)
        a0 += 2
        for _ in range(n):
            f = [m.s16(a0 + 2 * k) for k in range(3)]
            idx = m.u16(a0 + 6)
            a0 += 8
            V[idx] = tuple(
                s16(((2 * e2[k] * f[0] + 2 * e3[k] * f[1] + 2 * e1[k] * f[2]) & 0xFFFFFFFF) >> 16) for k in range(3))

        def lift(v):
            return tuple(s16(((2 * e3[k] * s16(v)) & 0xFFFFFFFF) >> 16) for k in range(3))
        if model == 2:
            V[0x60] = lift(m.u16(0xDD8))
        else:
            V[0x61] = lift(m.u16(0xDD6))
            V[0x5C] = lift(m.u16(0xDD8))
        ty = w16(yaw + m.u16(0x6CC))
        a0 = self._box(a0, (0, w16(ty - cam_yaw), 0), V, -8)
        return self._box(a0, (m.u16(0x6CA), w16(ty - cam_yaw), 0), V, -16)

    def _vertex_ops(self, a0: int, V: dict) -> int:
        """$5A02: count, then (op, index) bytes: $00 load, $18 store, $30 midpoint (add, asr),
        $48 add, $60 subtract, $78 negate, on words."""
        m = self.mem
        n = m.u16(a0)
        a0 += 2
        cur = (0, 0, 0)
        for _ in range(n):
            op, i = m.u8(a0), m.u8(a0 + 1)
            a0 += 2
            if op == 0x18:
                V[i] = cur
                continue
            if op == 0x78:
                cur = tuple(s16(-c) for c in cur)
                continue
            if op not in (0, 0x30, 0x48, 0x60):
                raise NotImplementedError(f"vertex op ${op:02X}")
            if i not in V:
                raise NotImplementedError(f"vertex op ${op:02X} reads V[{i}], not built")
            v = V[i]
            if op == 0:
                cur = v
            elif op == 0x30:
                cur = tuple(s16(c + d) >> 1 for c, d in zip(cur, v))
            elif op == 0x48:
                cur = tuple(s16(c + d) for c, d in zip(cur, v))
            else:
                cur = tuple(s16(c - d) for c, d in zip(cur, v))
        return a0

    def _radar_overlay(self, record: int, radar) -> None:
        """$146A4's state write: for each radar entry with a colour ($9098: type 8 has one) whose
        X / Z divided by the record's +8 fit words (divs, bvs), an island (type 8) sets the yaw
        pair $A6C from the camera's yaw ($A45E with a4 = [$9FA])."""
        m = self.mem
        if radar is None:
            raise NotImplementedError("radar overlay with the radar list off ([$1230] = 0: stale list)")
        d = m.s16(record + 8)
        if d == 0:
            raise NotImplementedError("radar scale 0 (divs by zero)")
        for typ, x, z in radar:
            if typ != 8:
                continue

            def fits(v):
                q = abs(v) // abs(d)
                return -0x8000 <= (q if (v < 0) == (d < 0) else -q) <= 0x7FFF
            if fits(x) and fits(z):
                STATS["radar island"] += 1
                self._pair(OBJ_PAIRS + 4, m.u16(m.u32(CAMERA) + 6))

    def view_3d(self) -> tuple:
        """$7BF6 + $62D4 for the current camera -> the radar list."""
        tree, radar, n_flat = self.scene_collect()
        self.scene_draw(tree, radar, n_flat)
        return radar

    def _eye(self, ox: int, oz: int) -> tuple:
        """The renders' eye offset: the carrier yaw pair in $A6C ($A438), then $A558 on
        (ox, oz) -> carrier x, z + the turned offset."""
        m = self.mem
        car = stunits.CARRIER
        self._pair(OBJ_PAIRS + 4, m.u16(car + 6))
        dx, dz = rot_b(m, ox, oz, OBJ_PAIRS + 4)
        return s32(m.u32(car + 0xA) + dx), s32(m.u32(car + 0x12) + dz)

    def _bridge_view(self) -> None:
        """$1460A (screen 0) / $15002 (24): object 0 takes the carrier's angles and island
        buffer, the eye 100 behind its origin; the 3D view."""
        m = self.mem
        o0, car = stunits.OBJECTS, stunits.CARRIER
        for k in (4, 6, 8):
            m.set16(o0 + k, m.u16(car + k))
        m.set32(o0 + 0x3C, m.u32(car + 0x3C))
        x, z = self._eye(0, -100)
        m.set32(o0 + 0xA, x)
        m.set32(o0 + 0x12, z)
        return self.view_3d()

    def _bridge_render(self) -> None:
        """$1460A: the bridge view, then the radar scope's record $4808 gets the zoom [$8B2]
        and ($146A4, while the radar works [$B02]) its overlay."""
        m = self.mem
        radar = self._bridge_view()
        m.set16(0x4808 + 8, m.u16(0x8B2))
        if m.u8(0xB02):
            self._radar_overlay(0x4808, radar)

    def _redraw_countdown(self) -> bool:
        """The bay status lines under the deck, dock and arming views: [$95C] counts down;
        True while it was still above 1 (the lines are recomputed)."""
        m = self.mem
        if not m.u16(REDRAW):
            return False
        m.set16(REDRAW, m.u16(REDRAW) - 1)
        return m.u16(REDRAW) != 0

    def _manta_arming_render(self) -> None:
        """$15002 (screen 24): the bridge view in the hangar viewport; while [$95E]: cleared,
        the arming sub-panel $DC0 from the bay record [$48E] ($11738) and the panels redrawn
        (the loadout panel's callback, _manta_panel), [$95C] = 2; the status countdown."""
        m = self.mem
        self._bridge_view()
        if m.u16(REDRAW + 2):
            STATS["arming redraw"] += 1
            m.set16(REDRAW + 2, 0)
            m.set16(0xDC0, self._subpanel(m.u32(0x48E)))
            self._manta_panel()
            m.set16(REDRAW, 2)
        self._redraw_countdown()

    def _walrus_arming_render(self) -> None:
        """$14E56 (screen 16): object 0 takes the carrier's +$3C, pitch and roll, yaw = carrier
        yaw + [$DC8], the eye 1000 behind the carrier origin; the 3D view; while [$960]: the
        sub-panel $DC2 ($11E60, record [$568]) and panels redrawn (_walrus_panel), [$95C] = 2;
        the status countdown."""
        m = self.mem
        o0, car = stunits.OBJECTS, stunits.CARRIER
        m.set32(o0 + 0x3C, m.u32(car + 0x3C))
        m.set16(o0 + 4, m.u16(car + 4))
        m.set16(o0 + 6, w16(m.u16(car + 6) + m.u16(0xDC8)))
        m.set16(o0 + 8, m.u16(car + 8))
        x, z = self._eye(0, -1000)
        m.set32(o0 + 0xA, x)
        m.set32(o0 + 0x12, z)
        self.view_3d()
        if m.u16(REDRAW + 4):
            STATS["arming redraw"] += 1
            m.set16(REDRAW + 4, 0)
            m.set16(0xDC2, self._subpanel(m.u32(0x568)))
            self._walrus_panel()
            m.set16(REDRAW, 2)
        self._redraw_countdown()

    def _crane_view(self, ox: int, oz: int, crane: int) -> None:
        """$14816 / $14A40: object 0 at the carrier + the turned offset, yaw = carrier yaw +
        the crane angle, the carrier's island buffer; the 3D view."""
        m = self.mem
        o0, car = stunits.OBJECTS, stunits.CARRIER
        x, z = self._eye(ox, oz)
        m.set32(o0 + 0xA, x)
        m.set32(o0 + 0x12, z)
        m.set16(o0 + 6, w16(m.u16(crane) + m.u16(car + 6)))
        m.set32(o0 + 0x3C, m.u32(car + 0x3C))
        self.view_3d()

    def _deck_render(self) -> None:
        """$14816 (screen 26): the deck camera turning with the lift crane $DDE."""
        self._crane_view(0x1A4, 0x41A, 0xDDE)
        self._redraw_countdown()

    def _dock_render(self) -> None:
        """$14A40 (screen 19): the dock camera turning with the dock crane $DEA; while the
        countdown runs, the Walrus status lines turn by the carrier's -yaw (pair $A6C)."""
        self._crane_view(-0x1A4, -0x41A, 0xDEA)
        if self._redraw_countdown():
            STATS["dock status pair"] += 1
            self._pair(OBJ_PAIRS + 4, -self.mem.u16(stunits.CARRIER + 6))

    def _flares_render(self) -> None:
        """$165B2 (screen 9): object 0 1000 astern, pitch [$1158], yaw [$115A], the carrier's
        island buffer; the 3D view."""
        m = self.mem
        o0, car = stunits.OBJECTS, stunits.CARRIER
        m.set32(o0 + 0x3C, m.u32(car + 0x3C))
        x, z = self._eye(0, -1000)
        m.set32(o0 + 0xA, x)
        m.set32(o0 + 0x12, z)
        m.set16(o0 + 6, m.u16(0x115A))
        m.set16(o0 + 4, m.u16(0x1158))
        self.view_3d()

    def _drone_view_render(self) -> None:
        """$1638C (screen 10): the 3D view from the camera [$9FA] while the viewing drone
        (object 15) is live."""
        if self.mem.u8(stunits.OBJECTS + 15 * stunits.OBJECT_SIZE + 1) & 1:
            self.view_3d()

    def _damage_render(self) -> None:
        """$156FA (screen 1): with the priorities panel closed ([$B62] = 0), the damage diagram
        (the model list) from the camera [$9FA]."""
        if not self.mem.u16(0xB62):
            self.view_3d()

    def _messages_render(self) -> None:
        """$162B0: the log is redrawn while $966 counts down. (Its expiry of old records
        changes the log at $65D10, which the app keeps itself.)"""
        m = self.mem
        if m.u16(0x966):
            m.set16(0x966, m.u16(0x966) - 1)

    def _radar_render(self) -> None:
        """$15EB0: drone k's element (type [$42A4E + $16 k] = 66 + k) is shown (flag bit 15 clear) while
        the drone is out (station +1 bit 0) and the radar works ([$B02] != 0), else hidden.
        (The element positions it writes are UI.)"""
        m = self.mem
        for k in range(4):
            a = FLAG_TABLE + 2 * (DRONE_TYPE + k)
            if m.u8(0x65E + 14 * k + 1) & 1 and m.u8(0xB02):
                m.set8(a, m.u8(a) & 0x7F)
            else:
                m.set8(a, m.u8(a) | 0x80)

    def _turret_render(self) -> None:
        """$15A94 (screen 8). With the observation turret working ([$B12] != 0): the zoom view
        record's shx/shy +8/+$A = [$6D2] + 8; object 0 on the carrier's island buffer with
        pitch [$6CA], yaw carrier yaw + [$6CC], the carrier's roll; the pairs $A70 = -[$6CC],
        $A68 = [$6CA], $A6C = carrier yaw place the eye 30 from the pivot (0, 320, -350)
        along the gun's up; the 3D view. Always: the turret wireframe (model 84, basis $ABE8)
        in its box, which leaves the pairs ([$6CA], [$6CC], 0). Broken: static noise ($5538:
        one rand)."""
        m = self.mem
        if m.u8(0xB12):
            m.set16(0x478E, w16(m.u16(0x6D2) + 8))
            m.set16(0x4790, w16(m.u16(0x6D2) + 8))
            o0, car = stunits.OBJECTS, stunits.CARRIER
            m.set32(o0 + 0x3C, m.u32(car + 0x3C))
            m.set16(o0 + 4, m.u16(0x6CA))
            m.set16(o0 + 6, w16(m.u16(0x6CC) + m.u16(car + 6)))
            m.set16(o0 + 8, m.u16(car + 8))
            self._pair(OBJ_PAIRS + 8, -m.u16(0x6CC))
            self._pair(OBJ_PAIRS, m.u16(0x6CA))
            self._pair(OBJ_PAIRS + 4, m.u16(car + 6))
            u, v = rot_a(m, 0x1E, 0, OBJ_PAIRS)
            m.set32(o0 + 0xE, u + 0x140)
            x, z = rot_a(m, 0, v, OBJ_PAIRS + 8)
            x, z = rot_b(m, x, w16(z - 0x15E), OBJ_PAIRS + 4)
            m.set32(o0 + 0xA, m.u32(car + 0xA) + x)
            m.set32(o0 + 0x12, m.u32(car + 0x12) + z)
            self.view_3d()
        self._model_pairs(84, stunits.OBJECTS)
        if not m.u8(0xB12):
            self.rand()

    def _walrus_view_render(self) -> None:
        """$15496 (screen 18), while [$70E] (controllable) and the Walrus object [$56C] is
        live, unless its telemetry ([$568] +$2C) is lost (2): the Walrus's visible bit
        cleared; object 0 (the camera $9FA) at the Walrus + 45 up with its pitch and roll and
        yaw + turret +$26 and its island buffer; the 3D view (rear view [$8F1]: yaw + $8000
        and pitch negated for it). Guiding a missile (record +0 bit 4): the Walrus visible
        again, the camera $9FA = the missile, which is hidden for the inset's $7BF6 and shown
        again; the inset viewport $4834 follows the guidance hold +$2E; $6FE = $4744. Radar
        scope (+$32): the overlay with $481E. Telemetry != 0: static noise (one rand)."""
        m = self.mem
        if not m.u16(0x70E):
            return
        rec, obj = m.u32(0x568), m.u32(0x56C)
        tele = m.u16(rec + 0x2C)
        if not m.u8(obj + 1) & 1:
            return
        if tele != 2:
            o0 = stunits.OBJECTS
            m.set8(obj + 1, m.u8(obj + 1) & 0xFD)
            m.set16(o0 + 4, m.u16(obj + 4))
            m.set16(o0 + 8, m.u16(obj + 8))
            m.set16(o0 + 6, w16(m.u16(obj + 6) + m.u16(obj + 0x26)))
            m.set32(o0 + 0xA, m.u32(obj + 0xA))
            m.set32(o0 + 0x12, m.u32(obj + 0x12))
            m.set32(o0 + 0xE, m.u32(obj + 0xE) + 0x2D)
            m.set32(o0 + 0x3C, m.u32(obj + 0x3C))
            m.set32(CAMERA, o0)
            rear = m.u8(0x8F1)
            if rear:
                m.set16(o0 + 6, m.u16(o0 + 6) + 0x8000)
                m.set16(o0 + 4, -m.u16(o0 + 4))
            radar = self.view_3d()
            if rear:
                m.set16(o0 + 6, m.u16(o0 + 6) + 0x8000)
                m.set16(o0 + 4, -m.u16(o0 + 4))
            if m.u8(rec) & 0x10:
                STATS["missile inset"] += 1
                m.set8(obj + 1, m.u8(obj + 1) | 2)
                missile = m.u32(obj + 0x20)
                if not (stunits.OBJECTS <= missile < stunits.OBJECTS + 64 * stunits.OBJECT_SIZE
                        and (missile - stunits.OBJECTS) % stunits.OBJECT_SIZE == 0):
                    raise NotImplementedError(f"Walrus guided missile pointer ${missile:X}")
                m.set32(CAMERA, missile)
                m.set8(missile + 1, m.u8(missile + 1) & 0xFD)
                tree, radar, n_flat = self.scene_collect()
                m.set8(missile + 1, m.u8(missile + 1) | 2)
                y = m.u16(rec + 0x2E)
                vp = 0x4834
                m.set16(vp + 0xE, y)
                y0 = w16(y - 0x20)
                m.set16(vp + 2, y0)
                y1 = w16(y0 + 0x3F)
                if s16(y1) > 0x85:
                    y1 = 0x85
                m.set16(vp + 6, y1)
                m.set16(vp + 0x12, w16(y1 - y0 + 1))
                m.set32(VIEWPORT, vp)
                self.scene_draw(tree, radar, n_flat)
                m.set32(VIEWPORT, 0x4744)
            if m.u8(rec + 0x32):
                self._radar_overlay(0x481E, radar)
        if tele:
            self.rand()

    def _manta_view_render(self) -> None:
        """$15244 (screen 27), while [$70C] (controllable), the record [$48E] isn't destroyed
        (+1 >= 0) and the Manta object [$492] is live, unless its telemetry +$2E is lost (2):
        the 3D view from the camera [$9FA] (rear view [$8F1]: the Manta's yaw + $8000, pitch
        and roll negated for it); not in rear view, the sight $1162/$1164: weapon (+$1A) 1:
        (rudder +2 asr 3, -pitch +4 asr 3), 0: (0, 0); radar scope (+$32): the overlay with
        $481E. Telemetry != 0: static noise (one rand)."""
        m = self.mem
        if not m.u16(0x70C):
            return
        rec, obj = m.u32(0x48E), m.u32(0x492)
        if m.u8(rec + 1) & 0x80 or not m.u8(obj + 1) & 1:
            return
        tele = m.u16(rec + 0x2E)
        if tele != 2:
            rear = m.u8(0x8F1)

            def flip():
                m.set16(obj + 6, m.u16(obj + 6) + 0x8000)
                m.set16(obj + 4, -m.u16(obj + 4))
                m.set16(obj + 8, -m.u16(obj + 8))
            if rear:
                STATS["rear view"] += 1
                flip()
            radar = self.view_3d()
            if rear:
                flip()
            else:
                w = m.u16(rec + 0x1A)
                if w == 1:
                    m.set16(0x1162, s16(m.u16(rec + 2)) >> 3)
                    m.set16(0x1164, s16(w16(-m.u16(rec + 4))) >> 3)
                elif w == 0:
                    m.set32(0x1162, 0)
            if m.u8(rec + 0x32):
                self._radar_overlay(0x481E, radar)
        if tele:
            self.rand()

    def _title_render(self) -> None:
        """$15DA2 (screen 33, title menu [$708] = 0): one rand; the carrier model's turret
        ($6CC + 300, $6CA swinging between 0 and -14000 by 800, $6D4 the direction); Control
        with A / B sets $124C to 0 / 1 (with M it ends the wait at once); in debug mode keypad
        +/- (new flags $835/$82D, consumed) restart the wait at 1000; the wait $93C counts down
        and at 0 grows $93A by 180 (to at most 3600), restarts and goes back to the title
        ($1231 = $FF, $4272: tab 5 requested, $708 = 0; $458 = $FF, $881 = 0). The 3D view
        of the model list (the carrier, turning by -500 a frame; keypad +/- in debug mode step
        its type through 1-$49) runs after the rand."""
        m = self.mem
        if m.u16(0x708):
            return
        self.rand()
        self.view_3d()
        m.set16(0x6CC, w16(m.u16(0x6CC) + 0x12C))
        d0 = s16(w16((-0x320 if m.u8(0x6D4) else 0x320) + m.u16(0x6CA)))
        d1 = d0
        d0 = 0 if d0 > 0 else -0x36B0 if d0 < -0x36B0 else d0
        m.set16(0x6CA, d0)
        if d1 != d0:
            m.set8(0x6D4, ~m.u8(0x6D4) & 0xFF)
        title = MODEL_LIST + 0x12                       # the model shown turns by -500
        m.set16(title + 6, m.u16(title + 6) - 0x1F4)
        expire = False
        if m.u8(0x7D2):
            if m.u8(0x7FC):
                expire = True
            elif m.u8(0x7D4):
                m.set16(TITLE_FLAG, 0)
            elif m.u8(0x7F8):
                m.set16(TITLE_FLAG, 1)
        if not expire:
            if m.u8(0x1189):
                typ = m.u16(title + 2)                  # keypad +/-: the next / previous type
                if m.u8(0x835) & 1:
                    m.set8(0x835, m.u8(0x835) & 0xFE)
                    m.set16(TITLE_TIMER + 2, 1000)
                    typ = w16(typ + 1)
                    m.set16(title + 2, 1 if typ == 0x4A else typ)
                elif m.u8(0x82D) & 1:
                    m.set8(0x82D, m.u8(0x82D) & 0xFE)
                    m.set16(TITLE_TIMER + 2, 1000)
                    typ = w16(typ - 1)
                    m.set16(title + 2, typ or 0x49)
            t = w16(m.u16(TITLE_TIMER + 2) - 1)
            m.set16(TITLE_TIMER + 2, t)
            if t:
                return
        d0 = w16(m.u16(TITLE_TIMER) + 0xB4)
        if s16(d0) > 0xE10:
            d0 = 0xE10
        m.set16(TITLE_TIMER, d0)
        m.set16(TITLE_TIMER + 2, d0)
        m.set8(0x1231, 0xFF)
        m.set8(0x1227, 0xFF)
        m.set16(0x1228, 5)
        m.set16(0x708, 0)
        m.set8(0x458, 0xFF)
        m.set8(0x881, 0)


# $144A0: the screens whose render writes state (the others write none).
RENDER_EFFECTS = {0: "_bridge_render", 1: "_damage_render", 2: "map_render_effects", 4: "_messages_render",
                  8: "_turret_render", 9: "_flares_render", 10: "_drone_view_render", 11: "_radar_render",
                  16: "_walrus_arming_render", 17: "map_render_effects", 18: "_walrus_view_render",
                  19: "_dock_render", 24: "_manta_arming_render", 25: "map_render_effects",
                  26: "_deck_render", 27: "_manta_view_render", 33: "_title_render"}


# ---- the sound-event check -----------------------------------------------------------------
# The world events that are message calls ($A128, header bit 7 clear) in the original.
MESSAGE_EVENTS = ("attacked", "fallen", "repulsed", "captured", "neutral")


def sound_hooks(oracle) -> list:
    """Log the original's sound calls on `oracle`: ("message", ping), ("sound", id),
    ("explosion", level, a1) / ("explosion", level, x, y, z)."""
    from unicorn import UC_HOOK_CODE
    from unicorn.m68k_const import UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A7, UC_M68K_REG_D0
    uc, log = oracle.uc, []

    def u16(a): return int.from_bytes(uc.mem_read(a, 2), "big")
    def u32(a): return int.from_bytes(uc.mem_read(a, 4), "big")

    def message(uc_, addr, size, _):
        log.append(["message", 0])

    def effect(uc_, addr, size, _):
        ret = u32(uc.reg_read(UC_M68K_REG_A7))
        if ret - 6 in (0xA11E, 0xA168):
            for e in reversed(log):
                if e[0] == "message":
                    e[1] = 1
                    break
            return
        log.append(["sound", u16(ret)])

    def explosion(uc_, addr, size, _):
        level = stsim.s16(uc.reg_read(UC_M68K_REG_D0) & 0xFFFF)
        a1 = uc.reg_read(UC_M68K_REG_A1) & 0xFFFFFF
        if addr == 0x20C30:
            log.append(["explosion", level, a1])
        else:
            log.append(["explosion", level] + [s32(s16(u16(a1 + 0x0A + 2 * k)) + u32(0xA4A + 4 * k)) for k in range(3)])

    def tune(uc_, addr, size, _):
        if uc.reg_read(UC_M68K_REG_A0) == 0x4B72E:
            log.append(["sound", "game over"])

    for a in (0xA0DE, 0xA128):
        uc.hook_add(UC_HOOK_CODE, message, begin=a, end=a)
    uc.hook_add(UC_HOOK_CODE, effect, begin=0x20B90, end=0x20B90)
    for a in (0x20C30, 0x20C08):
        uc.hook_add(UC_HOOK_CODE, explosion, begin=a, end=a)
    uc.hook_add(UC_HOOK_CODE, tune, begin=0x14446, end=0x14446)
    return log


def model_sounds(state, events) -> list:
    """The model's events in the form sound_hooks logs."""
    out = []
    quiet = state.mem.u8(0x1247)
    for e in events:
        if e[0] == "message":
            out.append(["message", e[2]])
        elif e[0] in MESSAGE_EVENTS:
            out.append(["message", 0 if quiet else 1])
        elif e[0] in ("sound", "explosion"):
            out.append(list(e))
    return out


def _sound_options(m, rnd) -> None:
    """Message ping and sound effects on or off ($1247, $1249), and a view pointer."""
    m.set8(0x1247, rnd.choice((0, 0, 0xFF)))
    m.set8(0x1249, rnd.choice((0, 0, 0xFF)))


KINDS = {}


def _count(log) -> None:
    for e in log:
        k = (e[0], e[1]) if e[0] != "explosion" or len(e) == 3 else ("explosion entry", e[1])
        KINDS[k] = KINDS.get(k, 0) + 1


def _kinds() -> str:
    return ", ".join(f"{k[0]} {k[1]} x{v}" for k, v in sorted(KINDS.items(), key=str))


def _run_compare(label: str, ram: bytes, passes: int, frontier: dict) -> tuple:
    """Model and original pass by pass (screen input stubbed, the one-second flag every 8
    passes); returns (ok, number of sound calls compared)."""
    import check_sim
    import check_units
    oracle = check_sim.Oracle(ram)
    oracle.uc.mem_write(0x17094, b"\x4e\x75")
    log = sound_hooks(oracle)
    state = stunits.UnitState.from_ram(ram)
    n = 0
    for p in range(passes):
        if p % 8 == 0:
            oracle.uc.mem_write(0x8FD, b"\xff")
            state.mem.set8(0x8FD, 0xFF)
        state.events.clear()
        try:
            state.sim_pass()
            dev = [e for e in state.events if e[0] == "deviation"]
            if dev:
                raise NotImplementedError(f"deviation: {dev[0][1]}")
        except (NotImplementedError, stsim.TacticalMode, stsim.EnemyDestroyed) as e:
            k = str(e) or type(e).__name__
            frontier[k] = frontier.get(k, 0) + 1
            return True, n
        del log[:]
        check_units.run_pass(oracle)
        mine = model_sounds(state, state.events)
        if mine != log:
            print(f"sound events {label}: MISMATCH at pass {p}")
            print(f"    original {log}")
            print(f"    model    {mine}")
            print(f"    events   {state.events}")
            return False, n
        n += len(log)
        _count(log)
        ref = oracle.ram()
        out = bytearray(ref)
        state.to_ram(out)
        if check_units.diff(out, ref):
            print(f"sound events {label}: state MISMATCH at pass {p}")
            return False, n
    return True, n


def check_sound_events(files: list, runs: int = 120, passes: int = 400) -> bool:
    """The model's sound events against the original's calls, pass by pass: a long run from
    the first action capture, the play-session captures, and the perturbed scenarios of
    every module (with the ping and sound-effect options switched at random)."""
    import check_units
    from stgfx import ROOT
    rnd = random.Random(23)
    ok, frontier, calls, kinds = True, {}, 0, 0
    first = min(files, key=lambda p: [int(x) for x in p.name.split("_")[1:3]])
    good, n = _run_compare("long run", first.read_bytes(), 3000, frontier)
    ok &= good
    calls += n
    play = sorted((ROOT / "emu" / "out" / "play").glob("pass_*_in.bin"))
    for f in play[::12]:
        good, n = _run_compare(f.name, f.read_bytes(), 60, frontier)
        ok &= good
        calls += n
    scen = check_units.all_scenarios()
    for r in range(runs):
        sc = scen[r % len(scen)]
        m = stsim.Mem(bytearray(rnd.choice(files + play).read_bytes()))
        sc(m, rnd)
        _sound_options(m, rnd)
        good, n = _run_compare(f"{sc.__name__} run {r}", bytes(m.buf), passes, frontier)
        ok &= good
        calls += n
    import stunits_vehicles
    weapons = (stunits_vehicles.scenario_manta_weapons, stunits_vehicles.scenario_walrus_weapons)
    for r in range(runs // 2):                 # the vehicles' weapons, sound effects on
        sc = weapons[r % 2]
        m = stsim.Mem(bytearray(rnd.choice(files + play).read_bytes()))
        sc(m, rnd)
        m.set8(0x1249, 0)
        good, n = _run_compare(f"{sc.__name__} run {r}", bytes(m.buf), 40, frontier)
        ok &= good
        calls += n
    print(f"sound events: {'OK' if ok else 'MISMATCH'} ({calls} calls compared; long run, "
          f"{len(play[::12])} play captures, {runs} scenario runs, {runs // 2} weapon runs)")
    for k, v in sorted(frontier.items(), key=lambda kv: -kv[1]):
        print(f"  stopped at unported code {v}x: {k}")
    print("  calls: " + _kinds())
    return ok


class _SoundTap:
    """While installed, every check_sim.Oracle logs its sound calls, and every
    check_units.diff (the state comparison all the module checks end in) also compares the
    sound events the model recorded since the last comparison with the calls the original
    logged since then. The pair compared is the state last written out (to_ram) and the
    oracle last read (ram()): what that diff compares."""

    def __init__(self):
        self.oracle = self.state = None
        self.calls, self.compared, self.fails, self.nfail, self.label = 0, 0, [], 0, ""

    def __enter__(self):
        import check_sim
        import check_units
        tap, U, O = self, stunits.UnitState, check_sim.Oracle
        self.saved = (O.__init__, O.ram, U.__dict__.get("to_ram"), check_units.diff)
        init, ram, _, diff = self.saved
        to_ram = U.to_ram

        def oracle_init(o, *a, **k):
            init(o, *a, **k)
            o.sound_log = sound_hooks(o)

        def oracle_ram(o):
            tap.oracle = o
            return ram(o)

        def state_to_ram(st, buf):
            tap.state = st
            return to_ram(st, buf)

        def tapped_diff(*a, **k):
            lines = diff(*a, **k)
            tap.compare()
            return lines
        O.__init__, O.ram, U.to_ram, check_units.diff = oracle_init, oracle_ram, state_to_ram, tapped_diff
        return self

    def __exit__(self, *exc):
        import check_sim
        import check_units
        O, U = check_sim.Oracle, stunits.UnitState
        O.__init__, O.ram, to_ram, check_units.diff = self.saved
        if to_ram is None:
            del U.to_ram
        else:
            U.to_ram = to_ram

    def compare(self) -> None:
        st, o = self.state, self.oracle
        if st is None or o is None or not hasattr(o, "sound_log"):
            return
        new = st.events[min(getattr(st, "_snd", 0), len(st.events)):]
        mine = model_sounds(st, new)
        if mine != o.sound_log:
            self.nfail += 1
            if len(self.fails) < 8:
                self.fails.append((self.label, list(o.sound_log), mine, new))
        self.compared += 1
        self.calls += len(o.sound_log)
        _count(o.sound_log)
        st._snd = len(st.events)
        del o.sound_log[:]
        self.state = self.oracle = None


def check_sound_commands(files: list) -> bool:
    """The module checks that issue commands and run passes with input (the carrier,
    hangar, controls, vehicle and war commands, screen switching, game frames, vehicle and
    war runs, the lock-on), re-run with the sound tap: every state comparison they make
    also compares the sound calls."""
    import contextlib
    import io
    import stunits_carrier
    import stunits_controls as sc
    import stunits_vehicles
    checks = [stunits_carrier.check_commands, stunits_vehicles.check_commands, stunits_vehicles.check_lock_on,
              stunits_vehicles.check_accb_capture,
              sc.check_commands, sc.check_screen_switch, sc.check_vehicle_commands, sc.check_vehicle_runs,
              sc.check_war_commands, sc.check_war_runs]
    ok = True
    with _SoundTap() as tap:
        for check in checks:
            tap.label = f"{check.__module__}.{check.__name__}"
            with contextlib.redirect_stdout(io.StringIO()) as out:
                good = check(files)
            if not good:
                print(out.getvalue())
                ok = False
    for label, orig, mine, events in tap.fails:
        print(f"  {label}: MISMATCH")
        print(f"    original {orig}")
        print(f"    model    {mine}")
        print(f"    events   {events}")
    ok &= not tap.fails
    print(f"sound events (commands and runs): {'OK' if ok else 'MISMATCH'} ({tap.compared} comparisons, "
          f"{tap.calls} calls, {len(checks)} module checks{f', {tap.nfail} mismatches' if tap.nfail else ''})")
    print("  calls so far: " + _kinds())
    return ok


def check_virus_capture(files: list, n: int = 300) -> bool:
    """hit_virus_bomb ($1C512) called directly on both, a virus bomb (object 30, type $15)
    hit by a command centre (type $29) of the carrier's island, at angles inside and outside
    the capture cone, on islands of every owner, sound effects on or off: the capture sound
    11 and the level-0 explosions (the general runs rarely reach a capture)."""
    import check_sim
    from unicorn.m68k_const import UC_M68K_REG_A1, UC_M68K_REG_A4, UC_M68K_REG_D3
    rnd = random.Random(29)
    ok, runs = True, 0
    for k in range(n):
        m = stsim.Mem(bytearray(files[k % len(files)].read_bytes()))
        buf = m.u32(stunits.CARRIER + 0x3C)
        if not buf:
            continue
        e = buf + 0x12 + 0x40 * rnd.randrange(1, 32)
        m.set16(e, 0x0403)
        m.set16(e + 2, 0x29)
        v = stunits.OBJECTS + stunits.OBJECT_SIZE * 30
        m.set16(v, 0x8703)
        m.set16(v + 2, 0x15)
        for o in (0x0A, 0x0E, 0x12):
            m.set32(v + o, m.u32(stunits.CARRIER + o) + rnd.randrange(-3000, 3000))
        m.set32(0xA9C, buf)
        m.set16(0xAB0, rnd.randrange(-2000, 2000))
        m.set16(0xAB2, rnd.randrange(-2000, 2000))
        st = stunits.UnitState.from_ram(bytes(m.buf))
        m.set16(e + 6, st.atan2(m.s16(0xAB0), m.s16(0xAB2)) + rnd.choice((0, 3000, -9000, 12000, 0x8000)))
        m.set8(stsim.OWNERS + m.u8(buf + 1), rnd.randrange(3))
        m.set16(stsim.STOCKPILE, rnd.choice((m.u8(buf + 1), 0xFFFF, 3)))
        _sound_options(m, rnd)
        ram = bytes(m.buf)
        oracle = check_sim.Oracle(ram)
        log = sound_hooks(oracle)
        for r, val in ((UC_M68K_REG_A1, v), (UC_M68K_REG_A4, e), (UC_M68K_REG_D3, 3)):
            oracle.uc.reg_write(r, val)
        oracle.call(0x1C512)
        st = stunits.UnitState.from_ram(ram)
        st.hit_virus_bomb(e, v, 3)
        ref = oracle.ram()
        out = bytearray(ref)
        st.to_ram(out)
        import check_units
        mine = model_sounds(st, st.events)
        runs += 1
        _count(log)
        if mine != log or check_units.diff(out, ref):
            print(f"virus capture: MISMATCH from {files[k % len(files)].name}: original {log}, model {mine}")
            ok = False
            break
    print(f"virus capture: {'OK' if ok else 'MISMATCH'} ({runs} hits)")
    return ok


# ---- the render-effects check ------------------------------------------------------------------
VIEW_SCREENS = (0, 1, 8, 9, 10, 16, 18, 19, 24, 26, 27, 33)


def _skipped(screen: int, name: str, addr: int) -> bool:
    """The element flags only the arming panels' draw callbacks write ($C3D4, $C076; also
    left out of the controls checks, stunits_controls.DRAWN_FLAGS)."""
    import stunits_controls as sc
    return any(sc.FLAG_TABLE + 2 * t <= addr < sc.FLAG_TABLE + 2 * t + 2 for t in sc.DRAWN_FLAGS)


def _render_original(ram: bytes, planes: list = None) -> bytes:
    """$14472 under Unicorn; with `planes`, each carrier model 2/3 drawn appends (object,
    model, its camera-space origin, the BSP root plane's facing flag from $9902)."""
    import stscreens
    t = stscreens.Tracer(ram)
    if planes is not None:
        from unicorn import UC_HOOK_CODE
        from unicorn.m68k_const import UC_M68K_REG_A0, UC_M68K_REG_A4, UC_M68K_REG_D0
        cur = {}

        def draw(uc, a, size, data):
            model = {CARRIER_MODELS[2] + 4: 2, CARRIER_MODELS[3] + 4: 3}.get(uc.reg_read(UC_M68K_REG_A0))
            cur.clear()
            if model:
                cur.update(obj=uc.reg_read(UC_M68K_REG_A4), model=model, n=-1)

        def tests(uc, a, size, data):
            if cur and cur["n"] < 0:
                cur["n"] = 0
                cur["origin"] = tuple(s32(int.from_bytes(uc.mem_read(0xA80 + 4 * k, 4), "big")) for k in range(3))

        def flag(uc, a, size, data):
            if cur and cur["n"] >= 0:
                if cur["n"] == (0 if cur["model"] == 2 else 3):
                    planes.append((cur["obj"], cur["model"], cur["origin"], not uc.reg_read(UC_M68K_REG_D0) & 0x80000000))
                    cur.clear()
                    return
                cur["n"] += 1
        t.uc.hook_add(UC_HOOK_CODE, draw, begin=0x7BAC, end=0x7BAC)
        t.uc.hook_add(UC_HOOK_CODE, tests, begin=0x98E6, end=0x98E6)
        t.uc.hook_add(UC_HOOK_CODE, flag, begin=0x98F8, end=0x98F8)
    t.call(0x14472)
    out = bytearray(ram)
    out[:0x80000] = bytes(t.uc.mem_read(0, 0x80000))
    return bytes(out)


def _render_states(files: list, rnd, n_switch: int) -> list:
    """(label, ram) on every screen: the Hatari units-frame captures (taken on each screen),
    and action/play captures switched by the original's clicks to a random screen (after
    random UI, war or vehicle perturbations), the stubs the switch oracle uses undone."""
    import stunits_controls as sc
    from stgfx import ROOT, Payload
    pay = Payload()
    out = [(f.name, f.read_bytes()) for f in sorted((ROOT / "emu" / "out" / "units-frame").glob("ram_*.bin"))]
    play = sorted((ROOT / "emu" / "out" / "play").glob("pass_*_in.bin"))
    pool = list(files[::10]) + play[::20]
    for k in range(n_switch):
        f = rnd.choice(pool)
        target = rnd.choice(sc.ALL_SCREENS + MAP_SCREENS * 4 + (8, 11, 18, 27, 33))
        m = stsim.Mem(bytearray(f.read_bytes()))
        rnd.choice((sc._perturb_screens, sc._perturb_war, sc._perturb_vehicles))(m, rnd)
        m.set8(sc.RUNNING, 0xFF)
        m.set8(sc.MODAL, 0)
        m.set16(0x704, 0)
        m.set16(0x706, 0)
        o = sc._switch_oracle(bytes(m.buf))
        try:
            if target in (33, 34, 35):
                o.uc.mem_write(sc.SCREEN_REQUEST, b"\xff" + bytes(1) + bytes([sc.TAB_OF[target]]))
                sc._call(o, sc.UI_FRAME)
            else:
                sc._oracle_switch(o, target)
        except RuntimeError:
            continue
        ram = bytearray(o.ram())
        for a, n in ((0x14472, 2), (0x17F48, 2), (0x14454, 2), (0x170D4, 36 * 4)):
            ram[a:a + n] = pay.bytes(a, n)
        out.append((f"{f.name} -> {target}", bytes(ram)))
    return out


def _perturb_render(m, rnd, screen: int) -> None:
    """The inputs the ported renders read: map view, owners, kinds, islands mode, info box;
    $966; drone stations and the radar; the turret; telemetry; the title's keys and timers;
    debug mode and its keys."""
    import stscreens_map
    if screen in MAP_SCREENS and rnd.random() < 0.8:
        m.buf[:0x80000] = stscreens_map.perturb(bytes(m.buf[:0x80000]), rnd, screen)
        if rnd.random() < 0.2:
            m.set16(MAP_INFO, 1)
        if rnd.random() < 0.05:
            level = rnd.choice((11, 12, 15))
            m.set16(0x758, level)
            m.set16(MAP_ZOOM, rnd.choice((1 << level, 3, 0xFFFF, 0x8000)))
    m.set16(0x966, rnd.choice((0, 0, 1, 2)))
    for k in range(4):
        m.set8(0x65E + 14 * k + 1, rnd.choice((0, 1, 3)))
    m.set8(0xB02, rnd.choice((0, 0xFF, 0xFF)))
    m.set8(0xB12, rnd.choice((0, 0xFF, 0xFF)))
    m.set16(0x6D2, rnd.randrange(4))
    for rec, off in ((m.u32(0x568), 0x2C), (m.u32(0x48E), 0x2E)):
        if 0x400 <= rec < 0x80000:
            m.set16(rec + off, rnd.choice((0, 0, 1, 2)))
    m.set16(0x6CA, rnd.choice((0, -0x36B0, rnd.randrange(-0x36B0, 0), 0x100)))
    m.set8(0x6D4, rnd.choice((0, 0xFF)))
    m.set16(TITLE_TIMER, rnd.choice((0xE10, 0x100, rnd.randrange(0x1000))))
    m.set16(TITLE_TIMER + 2, rnd.choice((1, 1, 2, 500)))
    for a in (0x7D2, 0x7FC, 0x7D4, 0x7F8, 0x835, 0x82D, 0x869, 0x871):
        m.set8(a, rnd.choice((0, 0, 0, 1)))
    m.set8(0x1189, rnd.choice((0, 0, 0xFF)))
    m.set8(0x122D, rnd.choice((0, 0xFF)))
    for k in range(3):                       # the bay status / arming panel redraws
        m.set16(REDRAW + 2 * k, rnd.choice((0, 0, 1, 2, 3)))
    m.set16(0x8B2, rnd.choice((1, 2, 4, 8, 0x40)))
    import stview
    if screen in stview.SCREEN_RENDER and rnd.random() < 0.6:
        m.buf[:0x80000] = stview.perturb_view(bytes(m.buf[:0x80000]), screen, rnd)


def _view_states(rnd, n: int, names=None) -> list:
    """(label, ram): the views captures (tools/capture_views.py, every 3D screen but the
    bridge and title; or those named), each perturbed n times by stview.perturb_view."""
    import stview
    from stgfx import ROOT
    out = []
    for f in sorted((ROOT / "emu" / "out" / "views").glob("pass_*_0_in.bin")):
        if names and f.name.split("_")[1] not in names:
            continue
        ram = f.read_bytes()
        screen = int.from_bytes(ram[stunits.SCREEN:stunits.SCREEN + 2], "big")
        for k in range(n):
            out.append((f"{f.name} view {k}", stview.perturb_view(ram, screen, rnd)))
    return out


def check_render_effects(files: list, n_switch: int = 600, fuzz: int = 3, views: int = 40) -> bool:
    """render_effects against screen_dispatch $14472 (the render and the panel callbacks it
    runs) under Unicorn: every state region after the render (the redraw countdowns $95C
    included; only the element flags of _skipped left out), on the units-frame captures
    (every screen) and captures switched to random screens, each also perturbed (the 3D
    views by stview.perturb_view: carrier and vehicle attitudes and places, turret, cranes,
    rear view, missile inset, lift and dock attachments), and the views captures perturbed
    `views` times each."""
    import check_units
    from collections import Counter
    rnd = random.Random(43)
    states = _render_states(files, rnd, n_switch) + _view_states(rnd, views)
    ok, screens, unported, errors = True, Counter(), Counter(), 0
    for label, ram in states:
        variants = [ram]
        for _ in range(fuzz):
            m = stsim.Mem(bytearray(ram))
            _perturb_render(m, rnd, m.u16(stunits.SCREEN))
            variants.append(bytes(m.buf))
        for v, ram2 in enumerate(variants):
            screen = int.from_bytes(ram2[stunits.SCREEN:stunits.SCREEN + 2], "big")
            state = stunits.UnitState.from_ram(ram2)
            try:
                state.render_effects()
            except NotImplementedError as e:
                unported[str(e)] += 1
                continue
            try:
                planes = []
                ref = _render_original(ram2, planes)
            except RuntimeError:
                errors += 1                      # the original faults on this perturbed state
                continue
            mine = bytearray(ref)
            state.to_ram(mine)
            for name, a, n in check_units.regions():
                for o in range(n):
                    if mine[a + o] != ref[a + o] and not _skipped(screen, name, a + o):
                        print(f"render effects: MISMATCH on screen {screen} from {label} (variant {v}) at "
                              f"${a + o:05X} ({name}): want {ref[a + o]:02X} got {mine[a + o]:02X}")
                        return False
            if screen != 18:                     # one camera: the camera pairs the model left are the view's
                for obj, model, origin, want in planes:
                    if state._carrier_plane(model, obj, origin) != want:
                        print(f"render effects: MISMATCH on screen {screen} from {label} (variant {v}): carrier "
                              f"model {model} BSP root plane flag, want {want}")
                        return False
                    STATS["carrier plane tests"] += 1
            screens[screen] += 1
    print(f"render effects: {'OK' if ok else 'MISMATCH'} ({sum(screens.values())} renders, screens "
          f"{dict(sorted(screens.items()))}{f', {errors} faulted in the original' if errors else ''})")
    for k, v in unported.items():
        print(f"  stopped at unported code {v}x: {k}")
    print("  exercised: " + ", ".join(f"{k} {v}" for k, v in sorted(STATS.items())))
    STATS.clear()
    return ok


# ---- the whole loop with the render: model against the original -------------------------------
def loop_oracle(ram: bytes):
    """The original running its whole game_main loop, render $14472 included: only frame_flip's
    waits are skipped (the VBL flag $1222 and the 3-VBL lock on $8EE, which interrupts end on
    the machine) and the game-over screen's key wait (as everywhere), and Unicorn's trapv after divs in the polygon clipper ($4CE2, $4D0A; it traps
    with V clear) is emulated."""
    import check_sim
    from unicorn import UC_HOOK_CODE
    from unicorn.m68k_const import UC_M68K_REG_PC, UC_M68K_REG_SR
    o = check_sim.Oracle(ram)
    o.uc.mem_write(0x17F72, b"\x4e\x71")
    o.uc.mem_write(0x17F82, b"\x4e\x71")
    o.uc.mem_write(0x14454, b"\x4e\x71")    # leave 34's wait for a key (skipped by the model too)

    def trapv(uc, a, size, data):
        if uc.reg_read(UC_M68K_REG_SR) & 2:
            raise RuntimeError("TRAPV: divs overflow in the polygon clipper")
        uc.reg_write(UC_M68K_REG_PC, a + 2)
    for a in (0x4CE2, 0x4D0A):
        o.uc.hook_add(UC_HOOK_CODE, trapv, begin=a, end=a)
    return o


def oracle_loop(o) -> None:
    """One whole game_main loop on the original: ui_frame $F636, then $4090 back to $408A."""
    import stunits_controls as sc
    from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_SR
    sc._call(o, sc.UI_FRAME)
    o.uc.reg_write(UC_M68K_REG_SR, 0x2700)
    o.uc.reg_write(UC_M68K_REG_A7, sc.GAME_STACK)
    o.uc.emu_start(0x4090, 0x408A, count=80_000_000)
    if o.uc.reg_read(UC_M68K_REG_PC) != 0x408A:
        raise RuntimeError("game_main loop did not return to $408A")


def model_loop(state) -> None:
    """The app's loop: ui_frame, game_frame, render_effects, frame_flip_flags (the last two
    skipped under the debug freeze, $1189 with key $866 held, as game_main does at $413E)."""
    state.ui_frame()
    state.game_frame()
    m = state.mem
    if m.u8(0x1189) and m.u8(0x866):
        return
    state.render_effects()
    state.frame_flip_flags()


# Display timers no simulation or render state depends on, set by routines the model leaves
# to the app (the message log $A266, gauges, the damage message); check_units.diff skips them.
UI_TIMERS = ("redraw flag $966", "redraw flags $952", "redraw flag $962")


def _loop_compare(state, oracle, label: str) -> bool:
    """Every state region, the panel flags included (only the element flags the arming
    panels' draw callbacks write, and the UI_TIMERS, are left out)."""
    import check_units
    ref = oracle.ram()
    mine = bytearray(ref)
    state.to_ram(mine)
    bad = []
    for name, a, n in check_units.regions():
        if name in UI_TIMERS:
            continue
        if mine[a:a + n] != ref[a:a + n]:
            offs = [a + o for o in range(n) if mine[a + o] != ref[a + o] and not _skipped(0, name, a + o)]
            if offs:
                bad.append((name, offs))
    if bad:
        print(f"render loop: MISMATCH {label}")
        for name, offs in bad[:8]:
            print(f"  {name}: {len(offs)} bytes, " + ", ".join(f"${x:05X} want {ref[x]:02X} got {mine[x]:02X}"
                                                         for x in offs[:6]))
    return not bad


LOOP_SCREENS = (0, 0, 1, 8, 8, 9, 10, 16, 16, 18, 18, 19, 19, 24, 24, 26, 26, 27, 27, 2, 3, 4, 11, 12, 17, 20, 25, 28)


def _loop_sources(files: list, rnd) -> list:
    """(label, ram, setup): Hatari new games, model new games from the title image (checked
    against $42A8 + ui_frame first), the title screen, play, action and views captures, the
    views captures perturbed (stview.perturb_view), and the modules' perturbed scenarios
    switched by clicks to a 3D view."""
    import stunits_setup as su
    from stgfx import ROOT
    out = []
    for f in sorted((ROOT / "emu" / "out" / "newgame").glob("*/pass0_in.bin")):
        out.append((f"newgame/{f.parent.name}", f.read_bytes(), None))
    title = su.title_ram()
    if title is not None:
        for k, strategy in enumerate((True, False, True, False)):
            out.append((f"title new {'strategy' if strategy else 'action'} game {k}",
                        bytes(su.seeded(title, strategy, rnd.randrange(1 << 32))), strategy))
        out.append(("title screen", title, None))
    out += [(f"play/{f.name}", f.read_bytes(), None) for f in sorted((ROOT / "emu" / "out" / "play").glob("pass_*_in.bin"))[::25]]
    out += [(f"action/{f.name}", f.read_bytes(), None) for f in files[::30]]
    out += [(f"views/{f.name}", f.read_bytes(), None) for f in sorted((ROOT / "emu" / "out" / "views").glob("pass_*_0_in.bin"))]
    out += [(f"views/{label}", ram, None) for label, ram in _view_states(rnd, 2)]
    out += [(f"views/{label}", ram, None) for label, ram in _view_states(rnd, 8, ("walrus", "manta"))]
    import check_units
    scen = check_units.all_scenarios()
    play = sorted((ROOT / "emu" / "out" / "play").glob("pass_*_in.bin"))
    for k in range(24):                      # perturbed scenarios (weapons, raids, lift, ...) on a 3D view
        m = stsim.Mem(bytearray(rnd.choice(list(files) + play).read_bytes()))
        sc = scen[k * 7 % len(scen)]
        sc(m, rnd)
        out.append((f"{sc.__name__} {k}", bytes(m.buf), ("switch", rnd.choice(VIEW_SCREENS[:-1]))))
    return out


def _start(label, ram, strategy):
    """The model and the original at the source's first loop top (a new game: $42A8 and the
    first ui_frame on both, compared)."""
    import stunits_setup as su
    import stunits_controls as sc
    if strategy is None:
        return stunits.UnitState.from_ram(ram), loop_oracle(ram)
    if isinstance(strategy, tuple):          # a scenario state, switched to a screen by clicks
        m = stsim.Mem(bytearray(ram))
        m.set8(sc.RUNNING, 0xFF)
        for a in (sc.MENU, sc.MODAL, 0x880, 0x881, 0x882, 0x883, sc.SCREEN_REQUEST, 0x704, 0x706, 0x702, 0x703):
            m.set8(a, 0)
        state, o = stunits.UnitState.from_ram(bytes(m.buf)), loop_oracle(bytes(m.buf))
        state.cmd_switch_screen(strategy[1])
        sc._oracle_switch(o, strategy[1])
        return state, o
    o = su.oracle_new_game(ram)
    su.oracle_screen(o)
    state = stunits.UnitState.from_ram(ram)
    state.new_game(strategy)
    state.new_game_screen()
    o2 = loop_oracle(bytes(o.ram()))
    return state, o2


def check_render_loop(files: list, passes: int = 400) -> bool:
    """The model's loop (ui_frame, game_frame, render_effects, frame_flip_flags) against the
    original's whole game_main loop with the render ($14472) running, pass by pass, every
    state region compared (the panel flags included, the UI_TIMERS left out), from
    Hatari and model new games, the title screen, and play, action and views captures; each
    run switches screens by clicks now and then (every 3D view, biased to them) and issues
    random vehicle and war commands, rear-view, turret and key input between passes."""
    import stunits_controls as sc
    from collections import Counter
    rnd = random.Random(97)
    sources = _loop_sources(files, rnd)
    vnames = [n for n in sc.VCOMMANDS if n not in ("cmd_drag_release",)]
    wnames = list(sc.WCOMMANDS)
    ok, total, screens, stopped, runs = True, 0, Counter(), Counter(), 0
    for label, ram, strategy in sources:
        state, o = _start(label, ram, strategy)
        if not _loop_compare(state, o, f"{label}: start"):
            return False
        runs += 1
        n = passes if strategy in (True, False) or label.startswith("newgame") else passes // 2
        if label == "title screen":
            n = passes // 4
        for p in range(n):
            try:
                mm = state.mem
                title = mm.u16(stunits.SCREEN) >= 32
                r = rnd.random()
                if not title and not mm.u8(sc.MODAL):
                    if r < 0.04:
                        target = rnd.choice(LOOP_SCREENS)
                        state.cmd_switch_screen(target)
                        sc._oracle_switch(o, target)
                    elif r < 0.10:
                        sc.apply_vcommand(state, o, rnd.choice(vnames), rnd)
                    elif r < 0.14:
                        sc.apply_wcommand(state, o, rnd.choice(wnames), rnd)
                    elif r < 0.16:
                        v = rnd.choice((0, 0xFF))           # rear view
                        mm.set8(0x8F1, v)
                        o.uc.mem_write(0x8F1, bytes([v]))
                    elif r < 0.20:
                        key, down = rnd.choice((0x48, 0x50, 0x4B, 0x4D, 0x52, 0x47, 0x02, 0x03)), rnd.choice((0, 1))
                        state.input_key(key, down)
                        sc._copy_input(o, state, (), None)
                if mm.u8(sc.MODAL) and rnd.random() < 0.5:
                    px, py = rnd.randrange(320), rnd.randrange(200)
                    state.cmd_drag_release(px, py)
                    o.uc.mem_write(0x474, px.to_bytes(2, "big") + py.to_bytes(2, "big"))
                    o.uc.mem_write(0x880, b"\x00")
                if p % 8 == 0:
                    o.uc.mem_write(0x8FD, b"\xff")
                    mm.set8(0x8FD, 0xFF)
                if mm.u8(stunits.POINTER_MODE):             # no click for the original's hit test
                    mm.set8(0x881, 0)
                    o.uc.mem_write(0x881, b"\x00")
                screen = mm.u16(stunits.SCREEN)
                model_loop(state)
            except STOP as e:
                stopped[str(e) or type(e).__name__] += 1
                break
            except RuntimeError as e:            # the original's handler didn't return
                print(f"render loop: {label}, pass {p} (screen {mm.u16(stunits.SCREEN)}): original {e}")
                return False
            oracle_loop(o)
            total += 1
            screens[screen] += 1
            if not _loop_compare(state, o, f"{label}, pass {p} (screen {screen})"):
                return False
            if state.mem.u16(stunits.SCREEN) in (34, 35):
                break
    print(f"render loop: {'OK' if ok else 'MISMATCH'} ({runs} runs, {total} passes; screens "
          f"{dict(sorted(screens.items()))})")
    for k, v in stopped.most_common():
        print(f"  stopped at unported code {v}x: {k}")
    print("  exercised: " + ", ".join(f"{k} {v}" for k, v in sorted(STATS.items())))
    STATS.clear()
    return ok


STOP = (NotImplementedError, stsim.TacticalMode, stsim.EnemyDestroyed)
ROUTINES = {}
SCENARIOS = []
CHECKS = [check_sound_events, check_virus_capture, check_sound_commands, check_render_effects, check_render_loop]
