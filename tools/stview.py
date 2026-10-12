#!/usr/bin/env python3
"""Reference model of the bridge 3D view (screen 0): camera, object transform, projection,
horizon, sea dots and the visible-object collection, from a 512 KB RAM image.

The fixed-point functions follow the original exactly (word products, `swap` truncation,
prescaled long rotations, `divs` truncation); the float functions are the same geometry for a
port (three.js). docs/memory-map.md "Bridge view camera and projection" is the spec.

Coordinates: game frame x right, y up, z forward; angles are 16-bit ($10000 = 360 degrees).
Camera space: X right, Y up, Z depth. Screen: sx = cx + trunc(X*2^shx/Z),
sy = cy + trunc(-Y*2^shy/Z).

  uv run python tools/stview.py --check [captures...]
      Runs the game's bridge render ($1460A) under Unicorn on each capture and compares, with
      this model: the camera object, the depth-sorted draw list (object, Z, X, Y), the LOD
      model, the horizon endpoints, the sea dots, and every projected model vertex. It also
      compares the rendered frame with the displayed screen buffer in the capture and with the
      Hatari screenshot (emu/out/play/shot_<label>.png, for pass_<label>_0_in.bin).
      --fuzz N (default 40) adds N random viewpoints on the first two captures: carrier
      heading/pitch/roll, position around the island, eye height, object angles.
      Unicorn's `trapv` traps even with V clear, so the two in the polygon clipper ($4CE2,
      $4D0A) are emulated by a hook.
"""
import argparse
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import ROOT

SIN_TABLE = 0x1F5A          # 4096 words, amplitude 16384; index = angle >> 4
WORLD_OBJECTS = 0x5DCDA     # 64 x $140
OBJ_SIZE = 0x140
ENTRY_SIZE = 0x40
CARRIER = WORLD_OBJECTS + 10 * OBJ_SIZE
CULL_TABLE = 0x8598         # per type: range.l, (unused here).l, min_z.l, radius.l
LOD_TABLE = 0x8B58          # per type: near.l, mid.l, near_model.w, mid_model.w, far_model.w, pad
MODEL_TABLE = 0x4A06A
BRIDGE_VIEWPORT = 0x4744    # x0, y0, x1, y1, shx, shy, cx, cy, mode ($100 = full-size fill)
BRIDGE_RENDER = 0x1460A
EYE_BACK = -100             # camera = carrier + yaw-rotated (0, -100) in x/z
EYE_Y = 240                 # camera y, set absolutely by the bridge's enter routine ($13C24)
SKY, SEA, SEA_DOT = 2, 10, 8  # palette indices (default palette $132A)
SEA_DOT_GRID = 0x2000


# --- integer helpers ---------------------------------------------------------------------

def s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def _hi(v: int) -> int:
    """`add.l d,d` twice then `swap`: the high word of 4*v (32-bit wrap)."""
    return s16(((v * 4) & 0xFFFFFFFF) >> 16)


class Ram:
    def __init__(self, data: bytes):
        self.d = bytes(data)

    def u8(self, a): return self.d[a]
    def u16(self, a): return struct.unpack_from(">H", self.d, a)[0]
    def s16(self, a): return struct.unpack_from(">h", self.d, a)[0]
    def u32(self, a): return struct.unpack_from(">I", self.d, a)[0]
    def s32(self, a): return struct.unpack_from(">i", self.d, a)[0]


def sincos(ram: Ram, angle: int) -> tuple:
    """$A438: (sin, cos) of a 16-bit angle from the 4096-entry table (low 4 bits dropped)."""
    a = angle & 0xFFF0
    return ram.s16(SIN_TABLE + (a >> 3)), ram.s16(SIN_TABLE + (((a + 0x4000) & 0xFFFF) >> 3))


def rot_a(x: int, y: int, sc: tuple) -> tuple:
    """$A4F4 form (pitch pairs act on (y, z), roll pairs on (x, y)): word inputs."""
    s, c = sc
    x, y = s16(x), s16(y)
    return _hi(x * c - y * s), _hi(y * c + x * s)


def rot_b(x: int, y: int, sc: tuple) -> tuple:
    """$A558 form (yaw pairs act on (x, z)): word inputs."""
    s, c = sc
    x, y = s16(x), s16(y)
    return _hi(x * c + y * s), _hi(y * c - x * s)


def rot_long(f, x: int, y: int, sc: tuple) -> tuple:
    """$A4B4/$A518/$A57C: halve both longs until within +-$4844, rotate, shift back."""
    k = 0
    while not (-0x4844 <= x <= 0x4844 and -0x4844 <= y <= 0x4844):
        x >>= 1
        y >>= 1
        k += 1
    a, b = f(x, y, sc)
    return s32(a << k), s32(b << k)


# --- camera ------------------------------------------------------------------------------

@dataclass
class Camera:
    x: int
    y: int
    z: int
    pitch: int
    yaw: int
    roll: int
    island: int             # attached island buffer (+$3C), or 0
    viewport: tuple         # x0, y0, x1, y1, shx, shy, cx, cy
    near: int               # [$A8C]: vertices with Z < near are not projected
    yaw_sc: tuple = None    # camera pairs $A74/$A78/$A7C: (sin, cos) of the NEGATED angles
    pitch_sc: tuple = None
    roll_sc: tuple = None


def bridge_camera(ram: Ram) -> Camera:
    """What $1460A does to camera object 0 each frame, from the carrier (object 10)."""
    pitch, yaw, roll = (ram.u16(CARRIER + o) for o in (4, 6, 8))
    dx, dz = rot_b(0, EYE_BACK, sincos(ram, yaw))
    vp = tuple(ram.s16(BRIDGE_VIEWPORT + 2 * i) for i in range(8))
    cam = Camera(ram.s32(CARRIER + 0xA) + dx, ram.s32(WORLD_OBJECTS + 0xE), ram.s32(CARRIER + 0x12) + dz,
                 pitch, yaw, roll, ram.u32(CARRIER + 0x3C), vp, ram.s32(0xA8C))
    cam.pitch_sc = sincos(ram, -pitch)
    cam.yaw_sc = sincos(ram, -yaw)
    cam.roll_sc = sincos(ram, -roll)
    return cam


def cam_xform_long(cam: Camera, dx: int, dy: int, dz: int) -> tuple:
    """Camera-relative world vector (longs) -> camera space, as $7C68: yaw, pitch, roll."""
    x, z = rot_long(rot_b, dx, dz, cam.yaw_sc)
    y, z = rot_long(rot_a, dy, z, cam.pitch_sc)
    x, y = rot_long(rot_a, x, y, cam.roll_sc)
    return x, y, z


def cam_xform_word(cam: Camera, x: int, y: int, z: int) -> tuple:
    """Island-entry local offset (words) -> camera space, as $81DC (no prescale)."""
    x, z = rot_b(x, z, cam.yaw_sc)
    y, z = rot_a(y, z, cam.pitch_sc)
    x, y = rot_a(x, y, cam.roll_sc)
    return x, y, z


def project_cam(cam: Camera, X: int, Y: int, Z: int):
    """$5912 for one point in camera space -> (sx, sy) longs, or None when Z < near."""
    if Z < cam.near:
        return None
    shx, shy, cx, cy = cam.viewport[4:8]
    x, y, z = X, -Y, Z
    d = s16(((z * 2) & 0xFFFFFFFF) >> 16)
    while d:
        d >>= 1
        x >>= 1
        y >>= 1
        z >>= 1

    def div(n):
        q = abs(n) // z             # divs, or its long fallback: truncates towards zero
        return -q if n < 0 else q
    return cx + div(s32(x << shx)), cy + div(s32(y << shy))


def project(ram: Ram, world: tuple, cam: Camera | None = None):
    """World point (x, y, z) -> bridge screen (sx, sy), or None behind the near plane."""
    cam = cam or bridge_camera(ram)
    return project_cam(cam, *cam_xform_long(cam, world[0] - cam.x, world[1] - cam.y, world[2] - cam.z))


# --- float geometry (for a port) ----------------------------------------------------------

def _rad(a: int) -> float:
    """The game's trig drops the low 4 bits of every angle (4096-step sine table)."""
    return (a & 0xFFF0) * 2 * math.pi / 65536


def object_matrix(pitch: int, yaw: int, roll: int) -> list:
    """Model -> world rotation, R = Yaw(yaw) * Pitch(pitch) * Roll(roll) (roll applied first):
         roll : x' = x cos r - y sin r,  y' = y cos r + x sin r
         pitch: y' = y cos p - z sin p,  z' = z cos p + y sin p
         yaw  : x' = x cos w + z sin w,  z' = z cos w - x sin w
    Forward (0,0,1) -> (sin w cos p, -sin p, cos w cos p): yaw $4000 faces +x, pitch $4000 down."""
    sp, cp = math.sin(_rad(pitch)), math.cos(_rad(pitch))
    sw, cw = math.sin(_rad(yaw)), math.cos(_rad(yaw))
    sr, cr = math.sin(_rad(roll)), math.cos(_rad(roll))
    roll_m = [[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]]
    pitch_m = [[1, 0, 0], [0, cp, -sp], [0, sp, cp]]
    yaw_m = [[cw, 0, sw], [0, 1, 0], [-sw, 0, cw]]
    return _mul(yaw_m, _mul(pitch_m, roll_m))


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def _neg_q(a: int) -> int:
    """The camera pairs hold sin/cos of the NEGATED angle, so the camera's angles are
    quantized upwards: -((-a) & $FFF0)."""
    return -((-a) & 0xFFF0) & 0xFFFF


def camera_matrix(cam: Camera) -> list:
    """World -> camera rotation: the transpose of object_matrix(cam angles), i.e.
    Roll(-roll) * Pitch(-pitch) * Yaw(-yaw) (yaw applied first)."""
    m = object_matrix(_neg_q(cam.pitch), _neg_q(cam.yaw), _neg_q(cam.roll))
    return [[m[j][i] for j in range(3)] for i in range(3)]


def game_rotation(cam: Camera, pitch: int, yaw: int, roll: int, billboard: bool = False) -> list:
    """Model -> camera rotation exactly as the box basis ($A78C) composes it: object roll,
    object pitch, then ONE yaw by (object yaw - camera yaw) quantized, camera pitch, camera
    roll. Billboards ($AA24) skip the object rotations and the yaw."""
    def t(m):
        return [[m[j][i] for j in range(3)] for i in range(3)]
    cp = t(object_matrix(_neg_q(cam.pitch), 0, 0))
    cr = t(object_matrix(0, 0, _neg_q(cam.roll)))
    if billboard:
        return _mul(cr, cp)
    obj = object_matrix(0, (yaw - cam.yaw) & 0xFFFF, 0)
    obj = _mul(obj, object_matrix(pitch, 0, roll))
    return _mul(cr, _mul(cp, obj))


def object_world_vertices(ram: Ram, obj: int, model_vertices: list, billboard: bool = False,
                          cam: Camera | None = None) -> list:
    """Model-space vertices of the object at obj -> world (floats): P + R(obj angles) v.
    Billboards (basis $AA24, trees) ignore the object's angles and take the camera's yaw."""
    pos = object_position(ram, obj)
    if billboard:
        m = object_matrix(0, _neg_q((cam or bridge_camera(ram)).yaw), 0)
    else:
        m = object_matrix(*(ram.u16(obj + o) for o in (4, 6, 8)))
    return [None if v is None else tuple(p + r for p, r in zip(pos, _apply(m, v))) for v in model_vertices]


def object_position(ram: Ram, obj: int) -> tuple:
    """World position: world objects store longs at +$A/+$E/+$12; island-buffer entries store
    words at +$A/+$C/+$E relative to the buffer origin (header +2/+6/+$A)."""
    if WORLD_OBJECTS <= obj < WORLD_OBJECTS + 64 * OBJ_SIZE:
        return ram.s32(obj + 0xA), ram.s32(obj + 0xE), ram.s32(obj + 0x12)
    buf = island_buffer_of(obj)
    return tuple(ram.s32(buf + 2 + 4 * i) + ram.s16(obj + 0xA + 2 * i) for i in range(3))


def island_buffer_of(entry: int) -> int:
    for buf in (0x62CDA, 0x63CEC):
        if buf + 0x12 <= entry < buf + 0x12 + 64 * ENTRY_SIZE:
            return buf
    raise ValueError(f"${entry:05X} is not an island-buffer entry")


def project_float(cam: Camera, world: tuple):
    """Float pipeline for a port: (sx, sy) unrounded, or None when Z < near."""
    m = camera_matrix(cam)
    X, Y, Z = _apply(m, (world[0] - cam.x, world[1] - cam.y, world[2] - cam.z))
    if Z < cam.near:
        return None
    shx, shy, cx, cy = cam.viewport[4:8]
    return cx + X * (1 << shx) / Z, cy - Y * (1 << shy) / Z


# --- horizon and sea dots -------------------------------------------------------------------

def horizon(cam: Camera):
    """$6402: the horizon line as two projected points, or ('sky'|'sea', None) when the camera
    looks nearly straight up/down. The fill is sky (colour 2) on and above the line, sea
    (colour 10) below it; the line itself is not drawn in a separate colour."""
    y, z = rot_a(0, 0x1000, cam.pitch_sc)
    if abs(z) <= cam.near:
        return ("sky" if cam.pitch & 0x8000 else "sea"), None
    xa, ya = rot_a(-0x2000, y, cam.roll_sc)
    xb, yb = rot_a(0x2000, y, cam.roll_sc)
    if z < 0:
        xa, ya, xb, yb, z = -xa, -ya, -xb, -yb, -z
    return "line", (project_cam(cam, xa, ya, z), project_cam(cam, xb, yb, z))


def sea_dots(cam: Camera) -> list:
    """$91EC: 8x8 world grid of sea-level points, spacing $2000, centred on the camera (unless
    the Sea Dots option $1246 is off). Each drawn as one pixel of colour 8."""
    def xf(x, y, z):
        x, z = rot_b(x, z, cam.yaw_sc)
        y, z = rot_a(y, z, cam.pitch_sc)
        x, y = rot_a(x, y, cam.roll_sc)
        return x, -y, z
    row = xf(0, 0, SEA_DOT_GRID)
    col = xf(SEA_DOT_GRID, 0, 0)
    p = xf((-cam.x) & 0x1FFF, -cam.y, (-cam.z) & 0x1FFF)
    p = tuple(a - 4 * b - 4 * c for a, b, c in zip(p, col, row))
    x0, y0, x1, y1, shx, _, cx, cy = cam.viewport
    dots = []
    for _ in range(8):
        q = p
        for _ in range(8):
            x, y, z = q
            if z >= 100:
                while s16(z) != z:
                    x, y, z = x >> 1, y >> 1, z >> 1
                sx, sy = abs(s32(x << shx)) // z, abs(s32(y << shx)) // z
                sx = -sx if s32(x << shx) < 0 else sx
                sy = -sy if s32(y << shx) < 0 else sy
                if -0x8000 <= sx < 0x8000 and -0x8000 <= sy < 0x8000:
                    sx, sy = s16(sx + cx), s16(sy + cy)
                    if x0 <= sx <= x1 and y0 <= sy <= y1:
                        dots.append((sx, sy))
            q = tuple(a + b for a, b in zip(q, col))
        p = tuple(a + b for a, b in zip(p, row))
    return dots


# --- visible-object collection ($7BF6) and draw order ---------------------------------------

def cull(ram: Ram, typ: int) -> tuple:
    b = CULL_TABLE + 16 * typ
    return ram.u32(b), ram.s32(b + 8), ram.s32(b + 12)


def lod_model(ram: Ram, typ: int, z: int) -> int:
    """$7B72: Z (camera-space depth, minus [$A94]) <= near -> near model; <= mid -> mid; else far."""
    b = LOD_TABLE + 16 * typ
    z -= ram.s32(0xA94)
    if z <= ram.s32(b):
        return ram.u16(b + 8)
    if z <= ram.s32(b + 4):
        return ram.u16(b + 10)
    return ram.u16(b + 12)


def _in_frustum(cam: Camera, X: int, Y: int, Z: int, radius: int) -> bool:
    """$853A: |X| - r <= max(Z,0) * 2^(7-shx) and |Y| - r <= that / 2."""
    d = max(Z, 0)
    sh = 7 - cam.viewport[4]
    d = d << sh if sh >= 0 else d >> -sh
    if abs(X) - radius > d:
        return False
    return abs(Y) - radius <= d >> 1


class _Tree:
    """depth_tree_insert ($6246): key Z; larger Z goes to the +6 side, drawn first."""
    def __init__(self):
        self.nodes = []        # [key, far(+6), near(+4), payload]

    def insert(self, key, payload, start=None):
        self.nodes.append([key, None, None, payload])
        n = len(self.nodes) - 1
        if n == 0:
            return
        if start is None:
            cur = 0
        else:                   # $622E (+6 of root, behind the island) / $623A (+4, in front)
            side = 1 if start == "behind" else 2
            if self.nodes[0][side] is None:
                self.nodes[0][side] = n
                return
            cur = self.nodes[0][side]
        while True:
            side = 2 if key <= self.nodes[cur][0] else 1
            if self.nodes[cur][side] is None:
                self.nodes[cur][side] = n
                return
            cur = self.nodes[cur][side]

    def in_order(self):
        out = []

        def walk(i):
            if i is None:
                return
            walk(self.nodes[i][1])
            out.append(self.nodes[i])
            walk(self.nodes[i][2])
        if self.nodes:
            walk(0)
        return out


def collect_visible(ram: Ram, cam: Camera | None = None) -> list:
    """$7BF6 + $62D4: the draw list, back to front. Items are dicts with obj, type, X/Y/Z
    (camera space) and model; 'flat' items are island entries drawn right after the island."""
    cam = cam or bridge_camera(ram)
    tree = _Tree()
    flat = []
    a98 = ram.s32(0xA98)

    def visible(typ, X, Y, Z):
        _, min_z, radius = cull(ram, typ)
        return Z >= min_z and _in_frustum(cam, X, Y, Z, radius)

    def in_range(typ, dx, dz):
        rng = (cull(ram, typ)[0] + a98) & 0xFFFFFFFF
        return abs(dx) <= rng and abs(dz) <= rng

    def live(a):
        return (ram.u8(a + 1) & 3) == 3

    buf = cam.island
    rect = None
    camera_high = cam.y >= 0x50
    forced = None                    # where world objects go relative to the island root
    entries = []                     # remaining island entries, inserted individually
    if buf:
        origin = tuple(ram.s32(buf + 2 + 4 * i) for i in range(3))
        n_flat = ram.u16(buf + 0x10)
        count = ram.u16(buf + 0xE)
        rel = (origin[0] - cam.x, origin[1] - cam.y, origin[2] - cam.z)
        oX, oY, oZ = cam_xform_long(cam, rel[0], rel[1], rel[2])
        e0 = buf + 0x12
        all_entries = [e0 + ENTRY_SIZE * k for k in range(count)]

        def entry_cam(e):
            lx, ly, lz = (ram.s16(e + 0xA + 2 * i) for i in range(3))
            if not in_range(ram.u16(e + 2), lx + rel[0], lz + rel[2]):
                return None
            x, y, z = cam_xform_word(cam, lx, ly, lz)
            return x + oX, y + oY, z + oZ
        if ram.u8(buf + 1) & 0x80 or not count:
            entries = all_entries if count else []
            buf = 0
        elif not (live(e0) and in_range(8, rel[0], rel[2]) and visible(8, oX, oY, oZ)):
            entries = all_entries[1 + n_flat:]
            buf = 0
        else:
            tree.insert(oZ, dict(obj=e0, type=8, X=oX, Y=oY, Z=oZ))
            hx, hz = (ram.u16(e0 + 0x1E) - 0x50) & 0xFFFF, (ram.u16(e0 + 0x20) - 0x50) & 0xFFFF
            rect = (rel[0] - hx, rel[0] + hx, rel[2] - hz, rel[2] + hz)
            rest = all_entries[1:]
            if camera_high:
                for e in rest[:n_flat]:
                    if live(e) and (p := entry_cam(e)) and visible(ram.u16(e + 2), *p):
                        flat.append(dict(obj=e, type=ram.u16(e + 2), X=p[0], Y=p[1], Z=p[2]))
                entries = rest[n_flat:]
            else:
                entries = rest[n_flat:]

            def outcode(x, z):
                return ((x < rect[0]) | (x > rect[1]) << 1 | (z < rect[2]) << 2 | (z > rect[3]) << 3)
            cam_out = outcode(0, 0)
            if cam_out:
                entry_side = "front" if camera_high else "behind"
            else:
                entry_side = "front"
            forced = (outcode, cam_out, entry_side)
        for e in entries:
            if live(e) and (p := entry_cam(e)) and visible(ram.u16(e + 2), *p):
                side = forced[2] if forced else None
                tree.insert(p[2], dict(obj=e, type=ram.u16(e + 2), X=p[0], Y=p[1], Z=p[2]), side)
    if not ram.u8(0xA46):
        for i in range(ram.u16(0xA48)):
            a = WORLD_OBJECTS + OBJ_SIZE * i
            if not live(a) or ram.u8(a + 1) & 4:
                continue
            typ = ram.u16(a + 2)
            d = (ram.s32(a + 0xA) - cam.x, ram.s32(a + 0xE) - cam.y, ram.s32(a + 0x12) - cam.z)
            if not in_range(typ, d[0], d[2]):
                continue
            side = None
            if forced:
                outcode, cam_out, entry_side = forced
                oc = outcode(d[0], d[2])
                if cam_out:
                    side = entry_side if oc == 0 else ("front" if oc & cam_out else "behind")
                else:
                    side = "front" if oc == 0 else "behind"
            X, Y, Z = cam_xform_long(cam, *d)
            if visible(typ, X, Y, Z):
                tree.insert(Z, dict(obj=a, type=typ, X=X, Y=Y, Z=Z), side)
    # The four linked lists at $11D8 ($84A4) are not modelled: none is active in the captures.
    out = []
    for node in tree.in_order():
        item = node[3]
        item["model"] = lod_model(ram, item["type"], item["Z"])
        out.append(item)
        if item["type"] == 8 and camera_high:
            for f in flat:
                f["model"] = lod_model(ram, f["type"], f["Z"])
                f["flat"] = True
                out.append(f)
    return out


# --- check against the game ---------------------------------------------------------------

def _model_index(ram: Ram) -> dict:
    return {ram.u32(MODEL_TABLE + 4 * i): i for i in range(99)}


class _Game:
    """The game's bridge render under Unicorn, with hooks on the draw list and projections."""

    def __init__(self, data: bytes):
        from unicorn import UC_ARCH_M68K, UC_HOOK_CODE, UC_MODE_BIG_ENDIAN, Uc
        from unicorn import m68k_const as R
        self.R = R
        uc = self.uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(R.UC_CPU_M68K_M68000)
        uc.mem_map(0, 0x100000)
        uc.mem_map(0xFF0000, 0x10000)
        uc.mem_map(0xFFFF0000, 0x10000)
        uc.mem_write(0, bytes(data))
        self.nodes, self.projs, self.dots, self.models = [], [], [], []
        self.pending = None
        hooks = {0x7B48: self._node, 0x7BAC: self._model, 0x5912: self._proj_in,
                 0x5902: self._proj_in, 0x599C: self._proj_out, 0x4B2C: self._dot}
        # Unicorn's trapv (polygon clipper, after `divs`) traps even with V clear: emulate it.
        hooks[0x4CE2] = hooks[0x4D0A] = self._trapv
        for addr, fn in hooks.items():
            uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d, fn=fn: fn(), begin=addr, end=addr)

    def _trapv(self):
        if self.reg("SR") & 2:
            raise RuntimeError("TRAPV: divs overflow in the polygon clipper")
        self.uc.reg_write(self.R.UC_M68K_REG_PC, self.reg("PC") + 2)

    def reg(self, name):
        return self.uc.reg_read(getattr(self.R, "UC_M68K_REG_" + name))

    def r32(self, a, signed=True):
        return int.from_bytes(self.uc.mem_read(a, 4), "big", signed=signed)

    def _node(self):
        n = bytes(self.uc.mem_read(self.reg("A1"), 18))
        key, off, X, Y = struct.unpack(">i4xHii", n)
        self.nodes.append((WORLD_OBJECTS + off, key, X, Y))

    def _model(self):
        self.models.append((self.reg("A4"), self.reg("A0") - 4))

    def _proj_in(self):
        sp = self.reg("A7")
        n = int.from_bytes(self.uc.mem_read(0x8A0, 2), "big")
        a1 = self.reg("A1")
        verts = [struct.unpack(">3h", self.uc.mem_read(a1 + 6 * i, 6)) for i in range(n)]
        self.pending = dict(caller=self.r32(sp, False), obj=self.reg("A4"), out=self.reg("A2"), n=n,
                            pos=tuple(self.r32(0xA80 + 4 * i) for i in range(3)), verts=verts,
                            model=self.models[-1][1] if self.models else None)

    def _proj_out(self):
        p = self.pending
        if p is None:
            return
        raw = [struct.unpack(">ii", self.uc.mem_read(p["out"] + 8 * i, 8)) for i in range(p["n"])]
        p["screen"] = [None if y == -0x80000000 else (x, y) for x, y in raw]
        self.projs.append(p)
        self.pending = None

    def _dot(self):
        if self.r32(self.reg("A7"), False) == 0x932C:          # called from draw_sea_dots
            self.dots.append((s16(self.reg("D4")), s16(self.reg("D5"))))

    def render(self):
        R, uc = self.R, self.uc
        stack, ret = 0x58600, 0x7FF00
        uc.reg_write(R.UC_M68K_REG_SR, 0x2700)
        uc.reg_write(R.UC_M68K_REG_A7, stack - 4)
        uc.mem_write(stack - 4, ret.to_bytes(4, "big"))
        try:
            uc.emu_start(BRIDGE_RENDER, ret, count=50_000_000)
        except Exception as e:
            raise RuntimeError(f"bridge render: {e} at PC=${uc.reg_read(R.UC_M68K_REG_PC):05X}") from e
        if uc.reg_read(R.UC_M68K_REG_PC) != ret:
            raise RuntimeError("bridge render did not return")
        return bytes(uc.mem_read(0, 0x80000))


def decode_screen(data: bytes, base: int) -> list:
    """ST low-res screen at base -> 200 rows of 320 colour indices."""
    rows = []
    for y in range(200):
        row = []
        for g in range(20):
            planes = struct.unpack_from(">4H", data, base + 160 * y + 8 * g)
            for bit in range(15, -1, -1):
                row.append(sum(((pl >> bit) & 1) << i for i, pl in enumerate(planes)))
        rows.append(row)
    return rows


def model_space_vertices(data: bytes, obj: int, rec: int) -> list:
    """The model's own basis routine and vertex ops with identity rotations (camera and object
    angles zeroed) on this RAM image: model-space vertices for this object's state."""
    from stemu import CAM, OBJ, Emu
    from stgfx import Payload
    import stmodel
    e = Emu(Payload())
    e.uc.mem_write(0x400, data[0x400:0x60000])          # globals (turret, lifts) and tables
    e.uc.mem_write(OBJ, data[obj:obj + 0x40])
    for o in (4, 6, 8):
        e.w16(OBJ + o, 0)
    e.uc.mem_write(CAM, bytes(0x40))
    e.w32(0x9FA, CAM)
    for setup in (0xA484, 0xA494, 0xA4A4):
        e.call(setup, 0)
    e.clear_vertices()
    ram = Ram(data)
    handler = ram.u32(rec)
    body = rec + (8 if handler in (stmodel.MODEL_DRAW_POLY_NEAR, stmodel.MODEL_DRAW_LINES_NEAR) else 4)
    basis = ram.u32(body)
    end = e.build_vertices(basis, body + 4)
    n = ram.u16(end)
    return [e.vertex(i) for i in range(n)], basis


def check_capture(path: Path, verbose: bool = False) -> bool:
    data = path.read_bytes()
    if Ram(data).u16(0x718) != 0:
        print(f"{path.name}: screen {Ram(data).u16(0x718)}, not the bridge; skipped")
        return True
    lines, ok, stats = check_data(path.name, data, path, verbose)
    print("\n".join(lines))
    return ok


def check_data(name: str, data: bytes, path: Path | None = None, verbose: bool = False):
    """Compare the model with the game's render on one RAM image -> (report lines, ok, stats)."""
    out = []
    print = out.append           # noqa: A001 - collect the report
    ram = Ram(data)
    ok = True
    cam = bridge_camera(ram)
    game = _Game(data)
    after = Ram(game.render())
    gcam = (after.s32(WORLD_OBJECTS + 0xA), after.s32(WORLD_OBJECTS + 0xE), after.s32(WORLD_OBJECTS + 0x12),
            *(after.u16(WORLD_OBJECTS + o) for o in (4, 6, 8)))
    mine = (cam.x, cam.y, cam.z, cam.pitch, cam.yaw, cam.roll)
    pairs = tuple(after.s16(0xA74 + 2 * i) for i in range(6))
    print(f"{name}: camera {mine} {'OK' if gcam == mine else f'MISMATCH game {gcam}'}; "
          f"pairs {'OK' if pairs == cam.pitch_sc + cam.yaw_sc + cam.roll_sc else 'MISMATCH'}")
    ok &= gcam == mine and pairs == cam.pitch_sc + cam.yaw_sc + cam.roll_sc

    # Draw list: the game's in-order node walk (world objects and individually sorted entries).
    items = collect_visible(ram, cam)
    sorted_items = [(i["obj"], i["Z"], i["X"], i["Y"]) for i in items if not i.get("flat")]
    same = sorted_items == game.nodes
    print(f"  draw list: {len(game.nodes)} nodes {'OK' if same else 'MISMATCH'}")
    if not same or verbose:
        for g, m in zip(game.nodes + [None] * 99, sorted_items + [None] * 99):
            if g is None and m is None:
                break
            print(f"    game {g and (hex(g[0]),) + g[1:]}  model {m and (hex(m[0]),) + m[1:]}")
    ok &= same
    index = _model_index(ram)
    gmodels = [(o, index.get(r)) for o, r in game.models]
    mmodels = [(i["obj"], i["model"]) for i in items]
    top = [m for m in gmodels if m[0] in {i[0] for i in mmodels}]
    same = top[:len(mmodels)] == mmodels
    print(f"  LOD models: {'OK' if same else 'MISMATCH'} {[(hex(o), m) for o, m in mmodels]}")
    ok &= same

    # Horizon and sea dots.
    kind, pts = horizon(cam)
    hz = [p for p in game.projs if p["caller"] == 0x6490]
    if kind == "line":
        same = bool(hz) and hz[0]["screen"] == list(pts)
        print(f"  horizon {pts} {'OK' if same else 'MISMATCH ' + str(hz and hz[0]['screen'])}")
        ok &= same
    dots = sea_dots(cam) if not ram.u8(0x1246) else []
    same = dots == game.dots
    print(f"  sea dots: {len(dots)} {'OK' if same else 'MISMATCH game ' + str(game.dots)}")
    ok &= same

    # Every projected model vertex: model-space vertices (identity build) -> float rotation ->
    # integer projection, and a fully float pipeline, against the game's projected output.
    total = proj_exact = 0
    worst_rot = worst_rel = worst_fp = worst_float = worst_far = 0.0
    pos_ok = True
    for p in game.projs:
        if p["caller"] == 0x6490 or p["model"] is None:
            continue
        obj = p["obj"]
        if WORLD_OBJECTS <= obj < WORLD_OBJECTS + 64 * OBJ_SIZE:
            d = [ram.s32(obj + o) - c for o, c in ((0xA, cam.x), (0xE, cam.y), (0x12, cam.z))]
            pos = cam_xform_long(cam, *d)
        else:
            try:
                buf = island_buffer_of(obj)
            except ValueError:
                continue
            o = [ram.s32(buf + 2 + 4 * i) - c for i, c in enumerate((cam.x, cam.y, cam.z))]
            oc = cam_xform_long(cam, *o)
            if obj == buf + 0x12:
                pos = oc
            else:
                lc = cam_xform_word(cam, *(ram.s16(obj + 0xA + 2 * i) for i in range(3)))
                pos = tuple(a + b for a, b in zip(oc, lc))
        if pos != p["pos"]:
            pos_ok = False
            print(f"  object ${obj:05X}: position {pos} MISMATCH game {p['pos']}")
            continue
        verts, basis = model_space_vertices(data, obj, p["model"])
        billboard = basis == 0xAA24
        rc = game_rotation(cam, *(ram.u16(obj + o) for o in (4, 6, 8)), billboard)
        wverts = object_world_vertices(ram, obj, verts, billboard, cam)
        for i, v in enumerate(verts[:p["n"]]):
            g, gv = p["screen"][i], p["verts"][i]
            # 1. The projection itself, on the game's own camera-space vertex: must be exact.
            gp = project_cam(cam, pos[0] + gv[0], pos[1] + gv[1], pos[2] + gv[2])
            if gp != g:
                pos_ok = False
                print(f"  object ${obj:05X} vertex {i}: projection {gp} MISMATCH game {g}")
            if v is None or g is None:
                continue
            total += 1
            proj_exact += gp == g
            # 2. The rotation: float R_cam * R_obj * v against the game's fixed-point vertex.
            vf = _apply(rc, v)
            err = max(abs(a - b) for a, b in zip(vf, gv))
            if verbose and err > 8:
                print(f"    ${obj:05X} model {_model_index(ram).get(p['model'])} basis ${basis:05X} v{i} {v} "
                      f"float {tuple(round(c, 1) for c in vf)} game {gv} angles "
                      f"{[hex(ram.u16(obj + o)) for o in (4, 6, 8)]}")
            worst_rot = max(worst_rot, err)
            worst_rel = max(worst_rel, err / max(1.0, math.dist(v, (0, 0, 0))))
            # 3. Screen error of the model pipeline (fixed-point position + float rotation, and
            #    all float): only rotation rounding, magnified near the camera.
            vc = [round(c) for c in vf]
            mine = project_cam(cam, pos[0] + vc[0], pos[1] + vc[1], pos[2] + vc[2])
            f = project_float(cam, wverts[i])
            vx0, vy0, vx1, vy1 = cam.viewport[:4]
            for got, which in ((mine, "fp"), (f, "float")):
                if got is None or not (vx0 <= g[0] <= vx1 and vy0 <= g[1] <= vy1):
                    continue
                e = max(abs(a - b) for a, b in zip(got, g))
                if which == "fp":
                    worst_fp = max(worst_fp, e)
                else:
                    worst_float = max(worst_float, e)
                if pos[2] + gv[2] >= 4096:
                    worst_far = max(worst_far, e)
    print(f"  vertices: {total} projected; projection of the game's camera-space vertices "
          f"{'exact' if proj_exact == total else 'MISMATCH'}; rotation error max {worst_rot:.2f} units "
          f"({100 * worst_rel:.2f}% of |v|); screen error max {worst_fp:.0f} px (fixed position + float "
          f"rotation), {worst_float:.2f} px (all float), {worst_far:.2f} px at Z >= 4096 (on-screen vertices); "
          f"object positions {'OK' if pos_ok else 'MISMATCH'}")
    ok &= pos_ok and proj_exact == total and worst_rot <= 8
    stats = dict(nodes=len(game.nodes), vertices=total, worst_rot=worst_rot, worst_rel=worst_rel,
                 worst_far=worst_far, worst_float=worst_float, dots=len(dots))
    if path is None:
        return out, ok, stats

    # The rendered frame against the frame on display in the capture, and the screenshot.
    draw = ram.u32(0x124E)
    shown = 0x78000 if draw == 0x70000 else 0x70000
    a = decode_screen(after.d, draw)
    b = decode_screen(data, shown)
    x0, y0, x1, y1 = cam.viewport[:4]
    diff = sum(a[y][x] != b[y][x] for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))
    print(f"  frame vs displayed buffer ${shown:05X}: {diff} viewport pixels differ")
    shot = path.parent / f"shot_{path.name.split('_')[1]}.png"
    if path.name.endswith("_0_in.bin") and shot.exists():
        print("  " + compare_screenshot(shot, a, cam.viewport))
    return out, ok, stats


def fuzz(path: Path, count: int, seed: int, verbose: bool = False) -> bool:
    """Random viewpoints on one capture: carrier heading, pitch and roll, position around the
    island (including over it), camera height (also below $50), and random object angles."""
    import random
    rng = random.Random(seed)
    base = bytearray(path.read_bytes())
    ok = True
    tot = dict(nodes=0, vertices=0, dots=0, worst_rot=0.0, worst_rel=0.0, worst_far=0.0, worst_float=0.0)
    trapped = 0
    for k in range(count):
        d = bytearray(base)
        r = Ram(d)
        buf = r.u32(CARRIER + 0x3C)
        ox, oz = (r.s32(buf + 2), r.s32(buf + 0xA)) if buf else (r.s32(CARRIER + 0xA), r.s32(CARRIER + 0x12))
        hx, hz = (r.u16(buf + 0x12 + 0x1E), r.u16(buf + 0x12 + 0x20)) if buf else (20000, 20000)
        tilt = rng.choice([0, 0x100, 0x400, 0x1000, 0x4000])
        struct.pack_into(">HHH", d, CARRIER + 4, rng.randint(-tilt, tilt) & 0xFFFF,
                         rng.randrange(0x10000), rng.randint(-tilt, tilt) & 0xFFFF)
        eye = rng.choice([240, 240, 240, 40, 600, 3000])
        spread = rng.choice([1.2, 1.5, 3.0])
        while True:          # at sea (the carrier can't be over the plateau) unless the eye is high
            dx, dz = (int(rng.uniform(-spread, spread) * h) for h in (hx, hz))
            if eye > 240 or abs(dx) > hx + 300 or abs(dz) > hz + 300:
                break
        struct.pack_into(">i", d, CARRIER + 0xA, ox + dx)
        struct.pack_into(">i", d, CARRIER + 0x12, oz + dz)
        struct.pack_into(">i", d, WORLD_OBJECTS + 0xE, eye)
        for i in range(1, 64):
            a = WORLD_OBJECTS + OBJ_SIZE * i
            if i != 10 and r.u8(a + 1) & 1 and rng.random() < 0.5:
                struct.pack_into(">HHH", d, a + 4, *(rng.randrange(0x10000) for _ in range(3)))
        if buf:
            for e in range(1, r.u16(buf + 0xE)):
                a = buf + 0x12 + ENTRY_SIZE * e
                if r.u8(a + 1) & 1 and rng.random() < 0.3:
                    struct.pack_into(">H", d, a + 6, rng.randrange(0x10000))
        try:
            lines, good, st = check_data(f"{path.name} fuzz {k}", bytes(d), verbose=verbose)
        except RuntimeError as e:
            # The polygon clipper's `divs; trapv` ($4CE0) overflows on some extreme views (the
            # game has no TRAPV handler: TOS would crash too), so skip those.
            if "TRAPV" not in str(e):
                raise
            trapped += 1
            if verbose:
                rr = Ram(bytes(d))
                print(f"  fuzz {k} trapped: carrier at island {rr.s32(CARRIER + 0xA) - ox} "
                      f"{rr.s32(CARRIER + 0x12) - oz} angles {[hex(rr.u16(CARRIER + o)) for o in (4, 6, 8)]} "
                      f"eye y {rr.s32(WORLD_OBJECTS + 0xE)}")
            continue
        for key in ("nodes", "vertices", "dots"):
            tot[key] += st[key]
        for key in ("worst_rot", "worst_rel", "worst_far", "worst_float"):
            tot[key] = max(tot[key], st[key])
        if not good:
            print("\n".join(lines))
        ok &= good
    print(f"{path.name}: {count} fuzzed viewpoints ({trapped} trapped in the game's clipper), "
          f"{tot['nodes']} draw-list nodes, {tot['dots']} sea dots, {tot['vertices']} vertices: rotation "
          f"error max {tot['worst_rot']:.2f} units, on-screen all-float error max {tot['worst_float']:.2f} px, "
          f"{tot['worst_far']:.2f} px at Z >= 4096: {'OK' if ok else 'MISMATCH'}")
    return ok


def compare_screenshot(shot: Path, frame: list, vp: tuple) -> str:
    """Find the 2x-scaled game screen in a Hatari screenshot and compare the viewport."""
    from PIL import Image
    from stgfx import Payload, st_palette
    img = Image.open(shot).convert("RGB")
    pal = st_palette(Payload(), 0x132A)
    x0, y0, x1, y1 = vp[:4]

    def score(ox, oy):
        bad = 0
        for y in range(y0, y1 + 1, 7):
            for x in range(x0, x1 + 1, 5):
                px = img.getpixel((ox + 2 * x, oy + 2 * y))
                bad += max(abs(c - d) for c, d in zip(px, pal[frame[y][x]])) > 40
        return bad
    best = min(((score(ox, oy), ox, oy) for ox in range(80, 112) for oy in range(30, 60)))
    _, ox, oy = best
    bad = sum(max(abs(c - d) for c, d in zip(img.getpixel((ox + 2 * x, oy + 2 * y)), pal[frame[y][x]])) > 40
              for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))
    return (f"screenshot {shot.name}: game pixel (x, y) at PNG ({ox} + 2x, {oy} + 2y); "
            f"{bad} of {(x1 - x0 + 1) * (y1 - y0 + 1)} viewport pixels differ")


DEFAULT_CAPTURES = ["emu/out/play/pass_quiet_0_in.bin", "emu/out/play/pass_flight_0_in.bin",
                    "emu/out/play/pass_combat_0_in.bin", "emu/out/play/pass_late_0_in.bin",
                    "emu/out/play/pass_late_30_in.bin", "emu/out/sim/ai_3000_0_in.bin",
                    "emu/out/sim/ai_120000_0_in.bin"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--fuzz", type=int, default=40, help="random viewpoints per capture (0 = none)")
    ap.add_argument("captures", nargs="*")
    args = ap.parse_args()
    if not args.check:
        ap.print_help()
        return 0
    paths = [Path(c) for c in args.captures] or [ROOT / c for c in DEFAULT_CAPTURES]
    ok = True
    for p in paths:
        if p.exists():
            ok &= check_capture(p, args.verbose)
    for p in paths[:2] if args.fuzz else []:
        if p.exists():
            ok &= fuzz(p, args.fuzz, seed=1, verbose=args.verbose)
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
