#!/usr/bin/env python3
"""Reference model of the 3D views: the bridge (screen 0), the vehicle and weapon screens
(8 turret, 9 flares, 10 viewing drone, 18 Walrus, 19 Walrus dock, 26 Manta deck, 27 Manta)
and the arming screens' small views (16 Walrus arming, 24 Manta arming):
camera, object transform, projection, horizon, sea dots, the visible-object collection and
the carrier's lift/dock attachments, from a 512 KB RAM image. docs/memory-map.md "Vehicle and
weapon views" specifies the other screens' cameras (screen_views).

The fixed-point functions follow the original exactly (word products, `swap` truncation,
prescaled long rotations, `divs` truncation); the float functions are the same geometry for a
port (three.js). docs/memory-map.md "Bridge view camera and projection" is the spec.

Coordinates: game frame x right, y up, z forward; angles are 16-bit ($10000 = 360 degrees).
Camera space: X right, Y up, Z depth. Screen: sx = cx + trunc(X*2^shx/Z),
sy = cy + trunc(-Y*2^shy/Z).

  uv run python tools/stview.py --check [captures...]
      Runs the screen's own render ([$718]: $1460A, $15A94, $165B2, $1638C, $14E56, $15496,
      $14A40, $15002, $14816, $15244) under Unicorn on each capture and compares, per $7BF6 call, with this
      model: the camera object and pairs, the depth-sorted draw list (object, Z, X, Y), the LOD
      models (and the lift/dock attachments), the horizon endpoints, the sea dots, every
      projected model vertex and the turret's model 84 overlay. It also compares the rendered
      frame with the displayed screen buffer in the capture and with the Hatari screenshot
      (shot_<label>.png, for pass_<label>_0_in.bin). Default captures: emu/out/play and
      emu/out/sim (bridge), emu/out/views (tools/capture_views.py).
      --fuzz N (default 40) adds N random viewpoints on the first two bridge captures (carrier
      heading/pitch/roll, position around the island, eye height, object angles) and N random
      states on each other screen's capture (perturb_view).
      Unicorn's `trapv` traps even with V clear, so the two in the polygon clipper ($4CE2,
      $4D0A) are emulated by a hook.
      Screens 1 (damage diagram, $156FA) and 33 (title, $15DA2) draw the model list at $64CFE
      (diagram_scene): also compared are the models drawn, the palette $136A the damage render
      leaves (diagram_palette), the title state (title_step) and the rendered frame against the
      next consecutive capture's buffer (emu/out/diagrams, tools/capture_diagrams.py).

  uv run python tools/stview.py --export-diagrams extracted/diagram_vectors.json [--fuzz N]
      The original's screen 1/33 renders on those captures plus N perturbed states each, for
      web/scripts/check-diagram.ts.
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
    cull_shx: int = None    # the frustum test's shx: [$6FE]'s ($8560), not always the viewport's
    obj: int = WORLD_OBJECTS  # the camera object [$9FA]
    screen: int = 0         # [$718]: the sea dots are one row lower on screen 27


def object_camera(ram: Ram, obj: int, viewport: tuple, cull_shx: int | None = None) -> Camera:
    """$7BF6's camera: the object at [$9FA] (position, angles, island +$3C) as it stands."""
    pitch, yaw, roll = (ram.u16(obj + o) for o in (4, 6, 8))
    cam = Camera(ram.s32(obj + 0xA), ram.s32(obj + 0xE), ram.s32(obj + 0x12), pitch, yaw, roll,
                 ram.u32(obj + 0x3C), tuple(viewport[:8]), ram.s32(0xA8C),
                 cull_shx=viewport[4] if cull_shx is None else cull_shx, obj=obj)
    cam.pitch_sc = sincos(ram, -pitch)
    cam.yaw_sc = sincos(ram, -yaw)
    cam.roll_sc = sincos(ram, -roll)
    return cam


def bridge_camera(ram: Ram) -> Camera:
    """What $1460A does to camera object 0 each frame, from the carrier (object 10)."""
    pitch, yaw, roll = (ram.u16(CARRIER + o) for o in (4, 6, 8))
    dx, dz = rot_b(0, EYE_BACK, sincos(ram, yaw))
    vp = tuple(ram.s16(BRIDGE_VIEWPORT + 2 * i) for i in range(8))
    cam = Camera(ram.s32(CARRIER + 0xA) + dx, ram.s32(WORLD_OBJECTS + 0xE), ram.s32(CARRIER + 0x12) + dz,
                 pitch, yaw, roll, ram.u32(CARRIER + 0x3C), vp, ram.s32(0xA8C), cull_shx=vp[4])
    cam.pitch_sc = sincos(ram, -pitch)
    cam.yaw_sc = sincos(ram, -yaw)
    cam.roll_sc = sincos(ram, -roll)
    return cam


# --- the other 3D screens ------------------------------------------------------------------

SCREEN_RENDER = {0: 0x1460A, 8: 0x15A94, 9: 0x165B2, 10: 0x1638C, 16: 0x14E56, 18: 0x15496,
                 19: 0x14A40, 24: 0x15002, 26: 0x14816, 27: 0x15244, 1: 0x156FA, 33: 0x15DA2}
HANGAR_VIEWPORT = 0x475A    # screens 16/24: (32, 6)-(159, 69), shx = shy = 7, centre (96, 38)
HANGAR_EYE_BACK = -1000     # screen 16: the eye 1000 astern of the carrier origin, y 140
HANGAR_SWING = 0xDC8        # screen 16: look yaw = carrier yaw + [$DC8] ($6000..$A000, aft)
VIEWPORT_PTR = 0x6FE
TURRET_VIEWPORT, HALF_VIEWPORT, INSET_VIEWPORT, TURRET_OVERLAY = 0x4786, 0x4770, 0x4834, 0x479C
TURRET_OVERLAY_POS = (0, -40, 1200)   # model 84 at this camera-space origin, viewport $479C
MANTA_REC, MANTA_OBJ, WALRUS_REC, WALRUS_OBJ = 0x48E, 0x492, 0x568, 0x56C
REAR_VIEW = 0x8F1


@dataclass
class View:
    """One scene_collect_visible pass of a screen's render: the RAM as $7BF6 sees it (object
    0 placed, visible bits changed) and its camera."""
    ram: Ram
    cam: Camera
    label: str


def viewport(ram: Ram, addr: int) -> tuple:
    return tuple(ram.s16(addr + 2 * i) for i in range(10))


def _w16(d: bytearray, a: int, v: int) -> None:
    struct.pack_into(">H", d, a, v & 0xFFFF)


def _w32(d: bytearray, a: int, v: int) -> None:
    struct.pack_into(">I", d, a, v & 0xFFFFFFFF)


def _carrier_eye(d: bytearray, ox: int, oz: int, yaw_offset: int) -> None:
    """Deck/dock/flares: object 0 x, z = carrier + formB_yaw(ox, oz); yaw = carrier + offset;
    +$3C = the carrier's. y, pitch and roll stay as the enter handler set them."""
    r = Ram(bytes(d))
    cyaw = r.u16(CARRIER + 6)
    dx, dz = rot_b(ox, oz, sincos(r, cyaw))
    _w32(d, WORLD_OBJECTS + 0xA, r.s32(CARRIER + 0xA) + dx)
    _w32(d, WORLD_OBJECTS + 0x12, r.s32(CARRIER + 0x12) + dz)
    _w16(d, WORLD_OBJECTS + 6, cyaw + yaw_offset)
    _w32(d, WORLD_OBJECTS + 0x3C, r.u32(CARRIER + 0x3C))


def screen_views(data: bytes, screen: int) -> list:
    """What the screen's render routine does before each $7BF6 call -> [View] (empty when it
    shows no 3D view: static, no telemetry, turret destroyed, not controllable)."""
    d = bytearray(data)
    r = Ram(data)
    vp = viewport(r, r.u32(VIEWPORT_PTR))
    obj0 = WORLD_OBJECTS

    def view(label, cam_obj, port=None, cull=None):
        rr = Ram(bytes(d))
        p = port or vp
        cam = object_camera(rr, cam_obj, p, cull if cull is not None else p[4])
        cam.screen = screen
        return View(rr, cam, label)

    if screen in (0, 24):            # $1460A / $15002 (Manta arming: the same, in [$6FE] = $475A)
        pitch, yaw, roll = (r.u16(CARRIER + o) for o in (4, 6, 8))
        for o, v in ((4, pitch), (6, yaw), (8, roll)):
            _w16(d, obj0 + o, v)
        _carrier_eye(d, 0, EYE_BACK, 0)
        return [view("bridge" if screen == 0 else "manta hangar", obj0)]
    if screen == 16:                 # $14E56: Walrus arming, the stern door looking aft
        # The offset is turned by the carrier's yaw (d7 at $A438), the look by yaw + [$DC8].
        # Object 0 is also "the hangar model" (type $53, live and visible from the enter
        # handler) at the eye: model 83 is empty ($99F0 rts), so nothing is drawn for it.
        _w16(d, obj0 + 4, r.u16(CARRIER + 4))
        _w16(d, obj0 + 8, r.u16(CARRIER + 8))
        _carrier_eye(d, 0, HANGAR_EYE_BACK, r.u16(HANGAR_SWING))
        return [view("walrus hangar", obj0)]
    if screen == 26:                 # $14816: Manta deck camera, turning with the lift crane $DDE
        _carrier_eye(d, 0x1A4, 0x41A, r.u16(0xDDE))
        return [view("deck", obj0)]
    if screen == 19:                 # $14A40: Walrus dock camera, turning with the dock crane $DEA
        _carrier_eye(d, -0x1A4, -0x41A, r.u16(0xDEA))
        return [view("dock", obj0)]
    if screen == 9:                  # $165B2: flare/observation turret 1000 astern, $1158/$115A
        _carrier_eye(d, 0, -1000, 0)
        _w16(d, obj0 + 6, r.u16(0x115A))
        _w16(d, obj0 + 4, r.u16(0x1158))
        return [view("flares", obj0)]
    if screen == 10:                 # $1638C: the viewing drone (object 15) is the camera
        drone = WORLD_OBJECTS + 15 * OBJ_SIZE
        return [view("drone", drone)] if r.u8(drone + 1) & 1 else []
    if screen == 8:                  # $15A94: laser turret
        if not r.u8(0xB12):
            return []
        zoom = r.u16(0x6D2) + 8
        _w16(d, TURRET_VIEWPORT + 8, zoom)
        _w16(d, TURRET_VIEWPORT + 0xA, zoom)
        heading, elev = r.u16(0x6CC), r.u16(0x6CA)
        _w32(d, obj0 + 0x3C, r.u32(CARRIER + 0x3C))
        _w16(d, obj0 + 4, elev)
        _w16(d, obj0 + 6, heading + r.u16(CARRIER + 6))
        _w16(d, obj0 + 8, r.u16(CARRIER + 8))
        u, v = rot_a(30, 0, sincos(r, elev))
        _w32(d, obj0 + 0xE, u + 0x140)
        x, z = rot_a(0, v, sincos(r, -heading))
        x, z = rot_b(x, s16(z - 350), sincos(r, r.u16(CARRIER + 6)))
        _w32(d, obj0 + 0xA, r.s32(CARRIER + 0xA) + x)
        _w32(d, obj0 + 0x12, r.s32(CARRIER + 0x12) + z)
        return [view("turret", obj0, viewport(Ram(bytes(d)), TURRET_VIEWPORT))]
    if screen == 27:                 # $15244: the Manta is the camera
        rec, obj = r.u32(MANTA_REC), r.u32(MANTA_OBJ)
        if (not r.u16(0x70C) or r.u8(rec + 1) & 0x80 or not r.u8(obj + 1) & 1
                or r.u16(rec + 0x2E) == 2):
            return []
        if r.u8(REAR_VIEW):
            _w16(d, obj + 6, r.u16(obj + 6) + 0x8000)
            _w16(d, obj + 4, -r.u16(obj + 4))
            _w16(d, obj + 8, -r.u16(obj + 8))
        return [view("manta", obj)]
    if screen == 18:                 # $15496: object 0 on the Walrus, facing its turret
        rec, obj = r.u32(WALRUS_REC), r.u32(WALRUS_OBJ)
        if not r.u16(0x70E) or not r.u8(obj + 1) & 1 or r.u16(rec + 0x2C) == 2:
            return []
        d[obj + 1] &= ~2
        _w16(d, obj0 + 4, r.u16(obj + 4))
        _w16(d, obj0 + 8, r.u16(obj + 8))
        yaw = r.u16(obj + 6) + r.u16(obj + 0x26)
        _w16(d, obj0 + 6, yaw)
        _w32(d, obj0 + 0xA, r.u32(obj + 0xA))
        _w32(d, obj0 + 0x12, r.u32(obj + 0x12))
        _w32(d, obj0 + 0xE, r.s32(obj + 0xE) + 0x2D)
        _w32(d, obj0 + 0x3C, r.u32(obj + 0x3C))
        if r.u8(REAR_VIEW):          # yaw + $8000 and pitch negated (not roll), restored after
            _w16(d, obj0 + 6, yaw + 0x8000)
            _w16(d, obj0 + 4, -r.u16(obj + 4))
        views = [view("walrus", obj0)]
        if not r.u8(rec) & 0x10:
            return views
        # The guided missile's inset ($4834, y from the guidance hold +$2E): the missile is the
        # camera and hidden, the Walrus is shown. Culling still uses [$6FE] = $4744 (shx 8).
        d[obj + 1] |= 2
        missile = r.u32(obj + 0x20)
        d[missile + 1] &= ~2
        y = r.u16(rec + 0x2E)
        y0 = s16(y - 0x20)
        y1 = min(s16(y0 + 0x3F), 0x85)
        for o, val in ((0xE, y), (2, y0), (6, y1), (0x12, y1 - y0 + 1)):
            _w16(d, INSET_VIEWPORT + o, val)
        port = viewport(Ram(bytes(d)), INSET_VIEWPORT)
        views.append(view("walrus missile inset", missile, port, cull=vp[4]))
        return views
    if screen == 1:                  # $156FA: the damage diagram, unless the priorities panel is up
        return [] if r.u16(PRIORITY_PANEL) else [view("damage diagram", obj0)]
    if screen == 33:                 # $15DA2: the title's model, unless a menu ($708) is up
        return [] if r.u16(0x708) else [view("title", obj0)]
    raise ValueError(f"screen {screen} has no 3D view in this model")


# --- the model-list pictures: damage diagram (screen 1) and title (screen 33) ---------------
#
# Both enter handlers ($1404E, $13B7C) build a fake island buffer at $64CFE (the "model list"):
# header byte +1 = $FF (bit 7: no plateau, every entry depth-sorted on its own), origin
# (0, -200, 2000), and make object 0 the camera at the world origin with angles 0, +$3C = the
# list, $A46/$A47 set (no world objects). The render is $7BF6, $4A64 (the viewport cleared to
# colour 0), $62D4: the ordinary pipeline, so the entries are culled, sorted and drawn like
# island entries (camera-space origin = (0, -200, 2000) + local; LOD by that Z).

MODEL_LIST = 0x64CFE
DIAGRAM_SCREENS = (1, 33)
PRIORITY_PANEL = 0xB62               # set while the repair-priorities panel replaces the picture
SYSTEMS = 0xAF4                      # 10 x (current.w, max.w, class.w, status.w)
SHOWN_SYSTEM, SHOWN_PART = 0xB58, 0xB5A
STATUS_COLOURS = 0xB68               # $070 whole, $007 damaged and working, $700 damaged and down
DAMAGE_PALETTE, PRIORITY_PALETTE, TITLE_PALETTE = 0x136A, 0x134A, 0x132A
DIAGRAM_GREY = 0x445                 # colour 7 as the enter handler and the colour-1 path set it
TITLE_MODEL = MODEL_LIST + 0x12      # its one entry: type +2 ($64D12), yaw +6 ($64D16)


def damage_status(ram: Ram):
    """$156FA's colour choice for the shown system [$B58] -> (k, register) or None: k indexes
    the status colours at $B68 (0 whole, 1 damaged but working, 2 damaged and down) and the
    register is the palette colour it goes to: 1 (the highlighted overlay; colour 7 is then
    reset to $445) or 7 (the whole wireframe: the superstructure 9, and the lift 8 between two
    part highlights, [$B5A] = 8). None when no system is shown ([$B58] < 0)."""
    k_sys = ram.s16(SHOWN_SYSTEM)
    if k_sys < 0:
        return None
    if k_sys > 9:
        raise ValueError(f"[$B58] = {k_sys}: the render's jump table has 10 entries")
    a = SYSTEMS + 8 * k_sys
    lift_gap = k_sys == 8 and ram.u16(SHOWN_PART) == 8
    if ram.u16(a) == ram.u16(a + 2):                  # cur == max (`cmp.w`, equality only)
        return 0, 7 if k_sys == 9 or lift_gap else 1
    if k_sys in (0, 4, 8):                             # their handlers test the status word
        k = 1 if ram.u16(a + 6) else 2
    elif k_sys == 9:
        k = 1 if ram.s16(a + 6) >= 0x40 else 2         # signed `cmpi.w #$40` / `blt`
    else:                                              # the shared handler $15870: its high byte
        k = 1 if ram.u8(a + 6) else 2
    return k, 7 if k_sys == 9 or lift_gap else 1


def diagram_palette(ram: Ram) -> list:
    """The 16 palette words the damage diagram is shown with: $136A as $156FA leaves it
    (the priorities panel uses $134A and no 3D). The render patches $136C (colour 1) or $1378
    (colour 7) from $B68 and sets $1224 (palette reload); with no system shown it writes
    nothing, and colour 7 is the enter handler's $445."""
    pal = [ram.u16(DAMAGE_PALETTE + 2 * i) for i in range(16)]
    st = damage_status(ram)
    if st is None:
        return pal
    k, reg = st
    pal[reg] = ram.u16(STATUS_COLOURS + 2 * k)
    if reg == 1:
        pal[7] = DIAGRAM_GREY
    return pal


def title_step(ram: Ram) -> dict:
    """What $15DA2 writes after drawing: the turret heading [$6CC] += $12C, the elevation [$6CA]
    swings by $320 between $C950 (-14000) and 0 (direction [$6D4], flipped when clamped), the
    model's yaw [$64D16] -= 500. (Keys: Control+A/B, the attract timer and debug +/- aren't
    picture state.)"""
    d0 = s16((-0x320 if ram.u8(0x6D4) else 0x320) + ram.u16(0x6CA))
    d1 = d0
    if d0 > 0:
        d0 = 0
    elif d0 < -0x36B0:
        d0 = -0x36B0
    flip = ram.u8(0x6D4) ^ 0xFF if d0 != d1 else ram.u8(0x6D4)
    return {0x6CA: d0 & 0xFFFF, 0x6CC: (ram.u16(0x6CC) + 0x12C) & 0xFFFF, 0x6D4: flip,
            TITLE_MODEL + 6: (ram.u16(TITLE_MODEL + 6) - 500) & 0xFFFF}


def diagram_scene(data: bytes, screen: int) -> dict | None:
    """The model-list picture as a port draws it -> None (no picture) or dict(viewport, palette,
    items): items in the game's draw order (back to front), each with the entry, its model
    (LOD by camera Z), camera-space origin (X, Y, Z) and the angles its vertices are turned by.
    Overlay models 75-82 ($A8DA / no basis) are built on the vertex array and edge vectors of
    the model drawn just before them (74, entry 0), so they take entry 0's angles, not their
    own (stale) ones. Line colours index the palette directly (the $13CA pairs are equal)."""
    views = screen_views(data, screen)
    if not views:
        return None
    ram, cam = views[0].ram, views[0].cam
    items, last = [], None
    for it in collect_visible(ram, cam):
        src = last if it["model"] in range(75, 83) and last is not None else it["obj"]
        if it["model"] not in range(75, 83):
            last = it["obj"]
        items.append(dict(obj=it["obj"], type=it["type"], model=it["model"],
                          origin=(it["X"], it["Y"], it["Z"]),
                          angles=tuple(ram.u16(src + o) for o in (4, 6, 8))))
    pal = diagram_palette(ram) if screen == 1 else [ram.u16(TITLE_PALETTE + 2 * i) for i in range(16)]
    return dict(viewport=cam.viewport, palette=pal, items=items)


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
                          cam: Camera | None = None, angles: tuple | None = None) -> list:
    """Model-space vertices of the object at obj -> world (floats): P + R(obj angles) v.
    Billboards (basis $AA24, trees) ignore the object's angles and take the camera's yaw.
    `angles` overrides the object's (the damage overlays turn with the model before them)."""
    pos = object_position(ram, obj)
    if billboard:
        m = object_matrix(0, _neg_q((cam or bridge_camera(ram)).yaw), 0)
    else:
        m = object_matrix(*(angles or tuple(ram.u16(obj + o) for o in (4, 6, 8))))
    return [None if v is None else tuple(p + r for p, r in zip(pos, _apply(m, v))) for v in model_vertices]


def object_position(ram: Ram, obj: int) -> tuple:
    """World position: world objects store longs at +$A/+$E/+$12; island-buffer entries store
    words at +$A/+$C/+$E relative to the buffer origin (header +2/+6/+$A)."""
    if WORLD_OBJECTS <= obj < WORLD_OBJECTS + 64 * OBJ_SIZE:
        return ram.s32(obj + 0xA), ram.s32(obj + 0xE), ram.s32(obj + 0x12)
    buf = island_buffer_of(obj)
    return tuple(ram.s32(buf + 2 + 4 * i) + ram.s16(obj + 0xA + 2 * i) for i in range(3))


def island_buffer_of(entry: int) -> int:
    for buf in (0x62CDA, 0x63CEC, MODEL_LIST):
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
                    sx, sy = s16(sx + cx), s16(sy + cy + (cam.screen == 27))
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
    sh = 7 - (cam.viewport[4] if cam.cull_shx is None else cam.cull_shx)
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
        self.passes = []            # per $7BF6 call: dict(nodes, projs, dots, models)
        self.pending = None
        hooks = {0x7B48: self._node, 0x7BAC: self._model, 0x5912: self._proj_in,
                 0x5902: self._proj_in, 0x599C: self._proj_out, 0x4B2C: self._dot,
                 0x7BF6: self._collect, 0x7C14: self._pairs}
        # Unicorn's trapv (polygon clipper, after `divs`) traps even with V clear: emulate it.
        hooks[0x4CE2] = hooks[0x4D0A] = self._trapv
        for addr, fn in hooks.items():
            uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d, fn=fn: fn(), begin=addr, end=addr)

    def _collect(self):
        self.nodes, self.projs, self.dots, self.models = [], [], [], []
        c = self.r32(0x9FA, False)
        rd = lambda a, n: int.from_bytes(self.uc.mem_read(a, n), "big", signed=n == 4)  # noqa: E731
        vp = self.r32(0x6FE, False)
        cam = (c, rd(c + 0xA, 4), rd(c + 0xE, 4), rd(c + 0x12, 4), rd(c + 4, 2), rd(c + 6, 2), rd(c + 8, 2),
               rd(c + 0x3C, 4) & 0xFFFFFFFF, s16(rd(vp + 8, 2)))
        self.passes.append(dict(nodes=self.nodes, projs=self.projs, dots=self.dots, models=self.models,
                                cam=cam))

    def _pairs(self):
        self.passes[-1]["pairs"] = tuple(s16(int.from_bytes(self.uc.mem_read(0xA74 + 2 * i, 2), "big"))
                                         for i in range(6))

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
        # drawn = the object whose model is being drawn ($7BAC): A4 may point at a fake object
        # by now (the carrier turret basis leaves it at $AAB4, so those are skipped below,
        # except on the model-list screens).
        drawn = self.models[-1][0] if self.models else self.reg("A4")
        self.pending = dict(caller=self.r32(sp, False), obj=self.reg("A4"), drawn=drawn, out=self.reg("A2"), n=n,
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

    def render(self, addr: int = BRIDGE_RENDER):
        R, uc = self.R, self.uc
        stack, ret = 0x58600, 0x7FF00
        uc.reg_write(R.UC_M68K_REG_SR, 0x2700)
        uc.reg_write(R.UC_M68K_REG_A7, stack - 4)
        uc.mem_write(stack - 4, ret.to_bytes(4, "big"))
        try:
            uc.emu_start(addr, ret, count=50_000_000)
        except Exception as e:
            raise RuntimeError(f"render ${addr:05X}: {e} at PC=${uc.reg_read(R.UC_M68K_REG_PC):05X}") from e
        if uc.reg_read(R.UC_M68K_REG_PC) != ret:
            raise RuntimeError(f"render ${addr:05X} did not return")
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


def model_space_vertices(data: bytes, obj: int, rec: int, parent: int | None = None) -> list:
    """The model's own basis routine and vertex ops with identity rotations (camera and object
    angles zeroed) on this RAM image: model-space vertices for this object's state. `parent`
    = the record of a model drawn first in the same vertex array (the damage overlays)."""
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

    def body_of(r):
        return r + (8 if ram.u32(r) in (stmodel.MODEL_DRAW_POLY_NEAR, stmodel.MODEL_DRAW_LINES_NEAR) else 4)
    if parent is not None:
        pbody = body_of(parent)
        e.build_vertices(ram.u32(pbody), pbody + 4)
    body = body_of(rec)
    basis = ram.u32(body)
    end = e.build_vertices(basis, body + 4)
    n = ram.u16(end)
    return [e.vertex(i) for i in range(n)], basis


def check_capture(path: Path, verbose: bool = False) -> bool:
    data = path.read_bytes()
    screen = Ram(data).u16(0x718)
    if screen not in SCREEN_RENDER:
        print(f"{path.name}: screen {screen} has no 3D view; skipped")
        return True
    lines, ok, stats = check_data(path.name, data, path, verbose)
    print("\n".join(lines))
    return ok


def attachments(ram: Ram, cam: Camera, items: list) -> list:
    """$6346: the carrier models with attachment slots (2, 3) draw the vehicle on the lift
    ([$6D6], slot 1) and in the dock ([$6DA], slot 2) inside their BSP, at the vehicle's own
    world position (long camera transform), with the LOD model for its camera Z. No culling
    and no flag test: the vehicles have +1 bit 2 set, so the world-object pass skips them."""
    out = []
    for item in items:
        if item["model"] not in (2, 3):
            continue
        for slot in (0x6D6, 0x6DA):
            o = ram.u32(slot)
            if o:
                X, Y, Z = cam_xform_long(cam, *(ram.s32(o + off) - c for off, c in
                                                 ((0xA, cam.x), (0xE, cam.y), (0x12, cam.z))))
                out.append(dict(obj=o, type=ram.u16(o + 2), X=X, Y=Y, Z=Z, carrier=item["obj"],
                                model=lod_model(ram, ram.u16(o + 2), Z)))
    return out


def object_cam_pos(ram: Ram, cam: Camera, obj: int):
    """Camera-space origin of a world object or island entry, as the game computes it."""
    if WORLD_OBJECTS <= obj < WORLD_OBJECTS + 64 * OBJ_SIZE:
        return cam_xform_long(cam, *(ram.s32(obj + o) - c for o, c in ((0xA, cam.x), (0xE, cam.y), (0x12, cam.z))))
    buf = island_buffer_of(obj)
    oc = cam_xform_long(cam, *(ram.s32(buf + 2 + 4 * i) - c for i, c in enumerate((cam.x, cam.y, cam.z))))
    if obj == buf + 0x12:
        return oc
    lc = cam_xform_word(cam, *(ram.s16(obj + 0xA + 2 * i) for i in range(3)))
    return tuple(a + b for a, b in zip(oc, lc))


def check_data(name: str, data: bytes, path: Path | None = None, verbose: bool = False,
               screen: int | None = None):
    """Compare the model with the screen's render on one RAM image -> (report lines, ok, stats)."""
    out = []
    print = out.append           # noqa: A001 - collect the report
    ram0 = Ram(data)
    screen = ram0.u16(0x718) if screen is None else screen
    views = screen_views(data, screen)
    game = _Game(data)
    after = Ram(game.render(SCREEN_RENDER[screen]))
    ok = len(views) == len(game.passes)
    print(f"{name}: screen {screen}, {len(views)} view(s) {[v.label for v in views]}"
          f"{'' if ok else f' MISMATCH: the game made {len(game.passes)} pass(es)'}")
    stats = dict(nodes=0, vertices=0, worst_rot=0.0, worst_rel=0.0, worst_far=0.0, worst_float=0.0, dots=0)
    if not ok:
        return out, ok, stats
    index = _model_index(ram0)
    for view, gp in zip(views, game.passes):
        ram, cam = view.ram, view.cam
        mine = (cam.obj, cam.x, cam.y, cam.z, cam.pitch, cam.yaw, cam.roll, cam.island, cam.cull_shx)
        same = gp["cam"] == mine and gp["pairs"] == cam.pitch_sc + cam.yaw_sc + cam.roll_sc
        print(f"  [{view.label}] camera ${cam.obj:05X} {mine[1:]} {'OK' if same else f'MISMATCH game {gp}'}")
        ok &= same

        # Draw list: the game's in-order node walk (world objects and individually sorted entries).
        items = collect_visible(ram, cam)
        sorted_items = [(i["obj"], i["Z"], i["X"], i["Y"]) for i in items if not i.get("flat")]
        same = sorted_items == gp["nodes"]
        print(f"  draw list: {len(gp['nodes'])} nodes {'OK' if same else 'MISMATCH'}")
        if not same or verbose:
            for g, m in zip(gp["nodes"] + [None] * 99, sorted_items + [None] * 99):
                if g is None and m is None:
                    break
                print(f"    game {g and (hex(g[0]),) + g[1:]}  model {m and (hex(m[0]),) + m[1:]}")
        ok &= same
        att = attachments(ram, cam, items)
        gmodels = [(o, index.get(r)) for o, r in gp["models"]]
        mmodels = [(i["obj"], i["model"]) for i in items]
        top = [m for m in gmodels if m[0] in {i[0] for i in mmodels}]
        amodels = sorted((a["obj"], a["model"]) for a in att)
        gatt = sorted(m for m in gmodels if m[0] in {a["obj"] for a in att} and m not in mmodels)
        same = top[:len(mmodels)] == mmodels and gatt == amodels
        print(f"  LOD models: {'OK' if same else 'MISMATCH'} {[(hex(o), m) for o, m in mmodels]}"
              + (f"; attachments {[(hex(o), m) for o, m in amodels]}" if att else ""))
        ok &= same

        # Horizon and sea dots.
        kind, pts = horizon(cam)
        hz = [p for p in gp["projs"] if p["caller"] == 0x6490]
        if screen in DIAGRAM_SCREENS:
            # No horizon, no sea dots: the viewport is cleared to colour 0 ($4A64) instead.
            same = not hz and not gp["dots"]
            print(f"  no horizon, no sea dots {'OK' if same else 'MISMATCH'}")
            ok &= same
        elif kind == "line":
            same = bool(hz) and hz[0]["screen"] == list(pts)
            print(f"  horizon {pts} {'OK' if same else 'MISMATCH ' + str(hz and hz[0]['screen'])}")
            ok &= same
        dots = sea_dots(cam) if not ram.u8(0x1246) and screen not in DIAGRAM_SCREENS else []
        same = dots == gp["dots"]
        print(f"  sea dots: {len(dots)} {'OK' if same else 'MISMATCH game ' + str(gp['dots'])}")
        ok &= same
        stats["dots"] += len(dots)
        stats["nodes"] += len(gp["nodes"])

        # Every projected model vertex: model-space vertices (identity build) -> float rotation ->
        # integer projection, and a fully float pipeline, against the game's projected output.
        total = proj_exact = 0
        worst_rot = worst_rel = worst_fp = worst_float = worst_far = 0.0
        pos_ok = True
        last_drawn = None
        for p in gp["projs"]:
            if p["caller"] == 0x6490 or p["model"] is None:
                continue
            obj = p["drawn"] if screen in DIAGRAM_SCREENS else p["obj"]
            if screen == 8 and p["caller"] == 0x94AE and p["pos"] == TURRET_OVERLAY_POS:
                # Model 84 (the turret wireframe) in the $479C window: its basis ($ABE8) turns
                # it by the turret's angles only (no camera pass), at a fixed origin.
                port = viewport(ram, TURRET_OVERLAY)
                ocam = Camera(0, 0, 0, 0, 0, 0, 0, port[:8], cam.near)
                verts, _ = model_space_vertices(ram.d, WORLD_OBJECTS, ram.u32(MODEL_TABLE + 4 * 84))
                good = True
                for i, v in enumerate(verts[:p["n"]]):
                    gv = p["verts"][i]
                    good &= v is None or tuple(v) == tuple(gv)
                    good &= project_cam(ocam, *(a + b for a, b in zip(TURRET_OVERLAY_POS, gv))) == p["screen"][i]
                print(f"  turret overlay: model 84 at {TURRET_OVERLAY_POS} in "
                      f"${TURRET_OVERLAY:04X}, {p['n']} vertices {'OK' if good else 'MISMATCH'}")
                ok &= good
                continue
            try:
                pos = object_cam_pos(ram, cam, obj)
            except ValueError:
                continue
            if pos != p["pos"]:
                pos_ok = False
                print(f"  object ${obj:05X}: position {pos} MISMATCH game {p['pos']}")
                continue
            # The damage-diagram overlays (75-82) are built on the vertex array and edge
            # vectors of the model drawn before them (74): its object's angles turn them.
            overlay = index.get(p["model"]) in range(75, 83) and last_drawn is not None
            src, parent_rec = (last_drawn if overlay else (obj, None))
            if not overlay:
                last_drawn = (obj, p["model"])
            verts, basis = model_space_vertices(ram.d, obj, p["model"], parent_rec)
            billboard = basis == 0xAA24
            angles = tuple(ram.u16(src + o) for o in (4, 6, 8))
            if basis == 0xA660:          # the island plateau: camera rotation only (island yaw 0)
                angles = (0, 0, 0)
            rc = game_rotation(cam, *angles, billboard)
            wverts = object_world_vertices(ram, obj, verts, billboard, cam, angles)
            for i, v in enumerate(verts[:p["n"]]):
                g, gv = p["screen"][i], p["verts"][i]
                # 1. The projection itself, on the game's own camera-space vertex: must be exact.
                gpj = project_cam(cam, pos[0] + gv[0], pos[1] + gv[1], pos[2] + gv[2])
                if gpj != g:
                    pos_ok = False
                    print(f"  object ${obj:05X} vertex {i}: projection {gpj} MISMATCH game {g}")
                if v is None or g is None:
                    continue
                total += 1
                proj_exact += gpj == g
                # 2. The rotation: float R_cam * R_obj * v against the game's fixed-point vertex.
                vf = _apply(rc, v)
                err = max(abs(a - b) for a, b in zip(vf, gv))
                if verbose and err > 8:
                    print(f"    ${obj:05X} model {index.get(p['model'])} basis ${basis:05X} v{i} {v} "
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
              f"rotation), {worst_float:.2f} px (all float), {worst_far:.2f} px at Z >= 4096 (on-screen "
              f"vertices); object positions {'OK' if pos_ok else 'MISMATCH'}")
        ok &= pos_ok and proj_exact == total and worst_rot <= 8
        stats["vertices"] += total
        for k, v in (("worst_rot", worst_rot), ("worst_rel", worst_rel), ("worst_far", worst_far),
                     ("worst_float", worst_float)):
            stats[k] = max(stats[k], v)
    if screen in DIAGRAM_SCREENS:
        ok &= _check_diagram(print, data, after, screen, [index.get(r) for _, r in
                                                           (game.passes[0]["models"] if game.passes else [])])
    if path is None or not views:
        return out, ok, stats

    # The rendered frame against the frame on display in the capture, and the screenshot.
    cam = views[0].cam
    draw = ram0.u32(0x124E)
    shown = 0x78000 if draw == 0x70000 else 0x70000
    a = decode_screen(after.d, draw)
    b = decode_screen(data, shown)
    x0, y0, x1, y1 = cam.viewport[:4]
    diff = sum(a[y][x] != b[y][x] for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))
    print(f"  frame vs displayed buffer ${shown:05X}: {diff} viewport pixels differ")
    # Captures at $413E (tools/capture_diagrams.py) are consecutive passes: the next one shows
    # the frame this one's render drew, in the same buffer.
    parts = path.name.split("_")
    nxt = path.parent / "_".join(parts[:2] + [str(int(parts[2]) + 1)] + parts[3:]) if len(parts) == 4 else None
    if screen in DIAGRAM_SCREENS and nxt and nxt.exists():
        c = decode_screen(nxt.read_bytes(), draw)
        mx, my = ram0.s16(0x474), ram0.s16(0x476)          # the mouse pointer, drawn after the render

        def pointer(x, y):
            return mx - 2 <= x <= mx + 16 and my - 2 <= y <= my + 16
        diff = sum(a[y][x] != c[y][x] and not pointer(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))
        print(f"  frame vs the next pass's displayed buffer ({nxt.name}): {diff} viewport pixels differ "
              f"(outside the pointer)")
        ok &= diff == 0
    shot = path.parent / f"shot_{path.name.split('_')[1]}.png"
    if path.name.endswith("_0_in.bin") and shot.exists():
        pal = None
        if screen in DIAGRAM_SCREENS:
            pal = [after.u16(after.u32(0x1326) + 2 * i) for i in range(16)]
        print("  " + compare_screenshot(shot, a, cam.viewport, pal))
    return out, ok, stats


def _check_diagram(print, data: bytes, after: Ram, screen: int, game_models: list) -> bool:
    """Screens 1/33: the models drawn (in order), the palette and the state the render leaves,
    against diagram_scene / diagram_palette / title_step."""
    ram0 = Ram(data)
    scene = diagram_scene(data, screen)
    ok = True
    mine = [it["model"] for it in scene["items"]] if scene else []
    same = mine == game_models
    print(f"  diagram models drawn {mine} {'OK' if same else f'MISMATCH game {game_models}'}")
    ok &= same
    if screen == 1:
        pal = [after.u16(DAMAGE_PALETTE + 2 * i) for i in range(16)]
        exp = diagram_palette(ram0) if scene else [ram0.u16(DAMAGE_PALETTE + 2 * i) for i in range(16)]
        if scene and damage_status(ram0) is None:      # nothing written: colour 7 as it was
            exp[7] = ram0.u16(DAMAGE_PALETTE + 14)
        same = pal == exp
        st = damage_status(ram0) if scene else None
        print(f"  palette $136A: system {ram0.s16(SHOWN_SYSTEM)} part {ram0.s16(SHOWN_PART)} -> "
              f"{st and (st[0], f'colour {st[1]}')} colours 1/7 ${pal[1]:03X}/${pal[7]:03X} "
              f"{'OK' if same else f'MISMATCH model ${exp[1]:03X}/${exp[7]:03X}'}")
        ok &= same
    elif scene:
        exp = title_step(ram0)
        got = {a: (after.u8(a) if a == 0x6D4 else after.u16(a)) for a in exp}
        same = got == exp
        print(f"  title step {', '.join(f'${a:X}={v:X}' for a, v in got.items())} "
              f"{'OK' if same else 'MISMATCH model ' + str({hex(a): hex(v) for a, v in exp.items()})}")
        ok &= same
    return ok


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


def compare_screenshot(shot: Path, frame: list, vp: tuple, words: list | None = None) -> str:
    """Find the 2x-scaled game screen in a Hatari screenshot and compare the viewport (palette
    $132A, or the 16 ST colour words given)."""
    from PIL import Image
    from stgfx import Payload, st_palette
    img = Image.open(shot).convert("RGB")
    pal = st_palette(Payload(), 0x132A)
    if words is not None:
        pal = [tuple(((w >> s) & 7) * 255 // 7 for s in (8, 4, 0)) for w in words]
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


def perturb_view(data: bytes, screen: int, rng) -> bytes:
    """Random state for one screen's camera: the carrier's attitude and place around its island
    and the screen's own inputs (vehicle attitude and height, turret angles and zoom, crane
    angles, observation turret, rear view, the Walrus missile inset, the no-view cases)."""
    d = bytearray(data)
    r = Ram(data)
    buf = r.u32(CARRIER + 0x3C)
    ox, oz = (r.s32(buf + 2), r.s32(buf + 0xA)) if buf else (r.s32(CARRIER + 0xA), r.s32(CARRIER + 0x12))
    hx, hz = (r.u16(buf + 0x12 + 0x1E), r.u16(buf + 0x12 + 0x20)) if buf else (20000, 20000)

    def angles(a, tilt):
        struct.pack_into(">HHH", d, a + 4, rng.randint(-tilt, tilt) & 0xFFFF, rng.randrange(0x10000),
                         rng.randint(-tilt, tilt) & 0xFFFF)

    def place(a, spread, y=None):
        dx, dz = (int(rng.uniform(-spread, spread) * h) for h in (hx, hz))
        _w32(d, a + 0xA, ox + dx)
        _w32(d, a + 0x12, oz + dz)
        if y is not None:
            _w32(d, a + 0xE, y)
    angles(CARRIER, rng.choice([0, 0x100, 0x400]))
    while True:                      # the carrier stays off the plateau
        place(CARRIER, rng.choice([1.2, 1.5, 3.0]))
        rr = Ram(bytes(d))
        if abs(rr.s32(CARRIER + 0xA) - ox) > hx + 300 or abs(rr.s32(CARRIER + 0x12) - oz) > hz + 300:
            break
    for i in range(1, 64):
        a = WORLD_OBJECTS + OBJ_SIZE * i
        if i != 10 and r.u8(a + 1) & 1 and rng.random() < 0.3:
            struct.pack_into(">HHH", d, a + 4, *(rng.randrange(0x10000) for _ in range(3)))
    d[REAR_VIEW] = rng.choice([0, 0, 0xFF])
    if screen in (26, 19):
        _w16(d, 0xDDE if screen == 26 else 0xDEA, rng.randrange(0x10000))
        _w16(d, CARRIER + 2, rng.choice([1, 2, 3]))
    elif screen in (16, 24):         # the swing (in range or not), the lift and dock in use
        _w16(d, HANGAR_SWING, rng.choice([rng.randint(0x6000, 0xA000), rng.randrange(0x10000)]))
        _w16(d, CARRIER + 2, rng.choice([1, 2, 3]))
        _w32(d, 0xDD6, rng.choice([0, 0x4000, 0x7FFF]) << 16 | rng.choice([0, 0x4000, 0x7FFF]))
        for slot, objs in ((0x6D6, range(2, 6)), (0x6DA, range(6, 10))):
            if rng.random() < 0.5:
                _w32(d, slot, 0)
            elif not r.u32(slot):
                o = WORLD_OBJECTS + OBJ_SIZE * rng.choice(list(objs))
                _w32(d, slot, o)
                d[o + 1] |= 7
    elif screen == 9:
        _w16(d, 0x1158, rng.randint(-0x3000, 0x1000))
        _w16(d, 0x115A, rng.randrange(0x10000))
    elif screen == 8:
        zoom = rng.randrange(4)
        _w16(d, 0x6D2, zoom)
        _w32(d, 0xA94, zoom * 10000)
        _w32(d, 0xA98, zoom * 14142)
        _w16(d, 0x6CA, rng.randint(-0x2000, 0x400))
        _w16(d, 0x6CC, rng.randrange(0x10000))
        d[0xB12] = rng.choice([0xFF, 0xFF, 0xFF, 0])
    elif screen == 10:
        drone = WORLD_OBJECTS + 15 * OBJ_SIZE
        angles(drone, rng.choice([0, 0x800, 0x4000]))
        place(drone, rng.choice([0.5, 1.5, 3.0]), rng.choice([300, 1500, 4000, 12000]))
    elif screen == 27:
        rec, obj = r.u32(MANTA_REC), r.u32(MANTA_OBJ)
        angles(obj, rng.choice([0, 0x400, 0x1000, 0x4000]))
        place(obj, rng.choice([0.5, 1.2, 2.0]), rng.choice([100, 300, 800, 2000]))
        d[rec + 1] = rng.choice([8] * 8 + [0xFF])
        _w16(d, rec + 0x2E, rng.choice([0] * 6 + [1, 2]))
    elif screen == 18:
        rec, obj = r.u32(WALRUS_REC), r.u32(WALRUS_OBJ)
        angles(obj, rng.choice([0, 0x200, 0x800]))
        place(obj, rng.choice([1.1, 1.5]), rng.choice([-15, 30, 80]))
        _w16(d, obj + 0x26, rng.randrange(0x10000))
        _w16(d, rec + 0x2C, rng.choice([0] * 6 + [1, 2]))
        if rng.random() < 0.6:       # steering a guided missile: the inset view
            m = WORLD_OBJECTS + OBJ_SIZE * rng.randrange(24, 64)
            d[m:m + 0x40] = bytes(0x40)
            _w16(d, m, 0x0403)
            _w16(d, m + 2, 19)
            angles(m, 0x800)
            rw = Ram(bytes(d))
            for o in (0xA, 0x12):
                _w32(d, m + o, rw.s32(obj + o) + rng.randint(-3000, 3000))
            _w32(d, m + 0xE, rng.choice([60, 200, 900]))
            _w32(d, m + 0x3C, rw.u32(obj + 0x3C))
            _w32(d, obj + 0x20, m)
            d[rec] |= 0x10
            _w16(d, rec + 0x2E, rng.randrange(0x20, 0x86))
        else:
            d[rec] &= ~0x10
    elif screen == 1:                # the shown system and lift part, the systems, the diagram's turn
        for k in range(10):
            a = SYSTEMS + 8 * k
            mx = rng.choice([r.u16(a + 2), 1500, 3000, 4000])
            _w16(d, a + 2, mx)
            _w16(d, a, rng.choice([mx, mx, rng.randrange(mx + 1)]))
            _w16(d, a + 6, rng.choice([0, 0, 0x100, 0xFF00, 0x3F, 0x40, 0x8000, rng.randrange(0x10000)]))
        shown, part = rng.randint(-1, 9), rng.randint(0, 8)
        _w16(d, SHOWN_SYSTEM, shown)
        _w16(d, SHOWN_PART, part)
        first = MODEL_LIST + 0x12 + ENTRY_SIZE   # $64D50: the parts; cmd_select_system / $1747A bits
        for k in range(8):
            d[first + ENTRY_SIZE * k + 1] &= ~2
            angles(first + ENTRY_SIZE * k, 0x8000)          # stale angles: not used by the overlays
        lit = shown if 0 <= shown <= 7 else part if shown == 8 and part != 8 else None
        if lit is not None:
            d[first + ENTRY_SIZE * lit + 1] |= 2
        struct.pack_into(">HHH", d, MODEL_LIST + 0x12 + 4, *rng.choice(
            [(0, 0, 0), (0, 0, 0), (rng.randrange(0x10000), 0, rng.randrange(0x10000))]))
        _w16(d, MODEL_LIST + 0x12 + 6, rng.randrange(0x10000))
        _w16(d, DAMAGE_PALETTE + 14, rng.choice([DIAGRAM_GREY, 0x070, 0x700]))
        _w16(d, PRIORITY_PANEL, rng.choice([0] * 7 + [1]))
    elif screen == 33:               # the turn, the turret swing, the model type (debug +/-), a menu
        _w16(d, TITLE_MODEL + 6, rng.randrange(0x10000))
        _w16(d, 0x6CA, rng.choice([rng.randint(-0x36B0, 0), 2000, -0x36B0, -0x320, 0x10]))
        _w16(d, 0x6CC, rng.randrange(0x10000))
        d[0x6D4] = rng.choice([0, 0xFF])
        _w16(d, TITLE_MODEL + 2, rng.choice([1] * 4 + [rng.randint(1, 0x49)]))
        _w16(d, 0x708, rng.choice([0] * 7 + [1]))
    return bytes(d)


def fuzz_view(path: Path, count: int, seed: int, verbose: bool = False) -> bool:
    """Random states (perturb_view) on one capture of a non-bridge 3D screen."""
    import random
    rng = random.Random(seed)
    base = path.read_bytes()
    screen = Ram(base).u16(0x718)
    ok = True
    tot = dict(nodes=0, vertices=0, dots=0, worst_rot=0.0, worst_float=0.0)
    trapped = views = 0
    for k in range(count):
        d = perturb_view(base, screen, rng)
        try:
            lines, good, st = check_data(f"{path.name} fuzz {k}", d, verbose=verbose)
        except RuntimeError as e:
            if "TRAPV" not in str(e):
                raise
            trapped += 1
            continue
        views += len(screen_views(d, screen))
        for key in ("nodes", "vertices", "dots"):
            tot[key] += st[key]
        for key in ("worst_rot", "worst_float"):
            tot[key] = max(tot[key], st[key])
        if not good or verbose:
            print("\n".join(lines))
        ok &= good
    print(f"{path.name}: screen {screen}, {count} fuzzed states ({trapped} trapped in the game's clipper), "
          f"{views} views, {tot['nodes']} draw-list nodes, {tot['dots']} sea dots, {tot['vertices']} vertices: "
          f"rotation error max {tot['worst_rot']:.2f} units, on-screen all-float error max "
          f"{tot['worst_float']:.2f} px: {'OK' if ok else 'MISMATCH'}")
    return ok


DEFAULT_CAPTURES = ["emu/out/play/pass_quiet_0_in.bin", "emu/out/play/pass_flight_0_in.bin",
                    "emu/out/play/pass_combat_0_in.bin", "emu/out/play/pass_late_0_in.bin",
                    "emu/out/play/pass_late_30_in.bin", "emu/out/sim/ai_3000_0_in.bin",
                    "emu/out/sim/ai_120000_0_in.bin"]
# tools/capture_views.py: one capture per vehicle/weapon screen (fuzzed with fuzz_view).
VIEW_CAPTURES = ["emu/out/views/pass_decklift_0_in.bin", "emu/out/views/pass_docklaunch_0_in.bin",
                 "emu/out/views/pass_walrushangar_0_in.bin", "emu/out/views/pass_walrushangar2_0_in.bin",
                 "emu/out/views/pass_walrushangardock_0_in.bin", "emu/out/views/pass_mantahangar_0_in.bin",
                 "emu/out/views/pass_mantahangarlift_0_in.bin",
                 "emu/out/views/pass_dock_0_in.bin", "emu/out/views/pass_manta_0_in.bin",
                 "emu/out/views/pass_walrus_0_in.bin", "emu/out/views/pass_turret_0_in.bin",
                 "emu/out/views/pass_flares_0_in.bin", "emu/out/views/pass_drone_0_in.bin"]
# tools/capture_diagrams.py: the damage diagram and the title (consecutive passes at $413E).
DIAGRAM_CAPTURES = [f"emu/out/diagrams/pass_{label}_{i}_in.bin" for label, n in
                    (("damage", 2), ("damagethree", 2), ("damagethreehit", 2), ("damagelift", 11),
                     ("damagehull", 2), ("damageengines", 2), ("title", 5)) for i in range(n)]


def export_diagrams(out: Path, count: int, seed: int = 3) -> int:
    """The original's screen 1/33 renders under Unicorn, for web/scripts/check-diagram.ts: per
    case the base capture, the perturbation (patches), and what the render did: the models
    drawn, every projected vertex, the palette $136A after it (screen 1) and the title state it
    leaves (screen 33)."""
    import json
    import random
    rng = random.Random(seed)
    cases = []
    bases = [ROOT / c for c in DIAGRAM_CAPTURES if (ROOT / c).exists()]
    for base_path in bases:
        base = base_path.read_bytes()
        screen = Ram(base).u16(0x718)
        variants = [base] + ([perturb_view(base, screen, rng) for _ in range(count)]
                             if base_path.name.endswith("_0_in.bin") else [])
        for data in variants:
            game = _Game(data)
            after = Ram(game.render(SCREEN_RENDER[screen]))
            index = _model_index(Ram(data))
            patches, a = [], 0
            while a < len(data):              # changed byte runs
                if data[a] != base[a]:
                    b = a
                    while b < len(data) and data[b] != base[b]:
                        b += 1
                    patches.append([a, data[a:b].hex()])
                    a = b
                else:
                    a += 1
            gp = game.passes[0] if game.passes else None
            cases.append(dict(
                base=str(base_path.relative_to(ROOT)), patches=patches, screen=screen,
                views=len(game.passes),
                models=[index.get(r) for _, r in gp["models"]] if gp else [],
                projs=[dict(model=index.get(p["model"]), n=p["n"], screen=p["screen"]) for p in gp["projs"]
                       if p["caller"] != 0x6490] if gp else [],
                palette=[after.u16(DAMAGE_PALETTE + 2 * i) for i in range(16)],
                palette_before=[Ram(data).u16(DAMAGE_PALETTE + 2 * i) for i in range(16)],
                title={f"{k:x}": (after.u8(k) if k == 0x6D4 else after.u16(k)) for k in (0x6CA, 0x6CC, 0x6D4, TITLE_MODEL + 6)}))
    out.write_text(json.dumps(cases))
    print(f"{out}: {len(cases)} cases from {len(bases)} captures")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--export-diagrams", type=Path, metavar="JSON",
                    help="write the original's screen 1/33 renders (captures + --fuzz perturbed states "
                         "each) for web/scripts/check-diagram.ts")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--fuzz", type=int, default=40, help="random viewpoints per capture (0 = none)")
    ap.add_argument("captures", nargs="*")
    args = ap.parse_args()
    if args.export_diagrams:
        return export_diagrams(args.export_diagrams, args.fuzz)
    if not args.check:
        ap.print_help()
        return 0
    paths = [Path(c) for c in args.captures] or [ROOT / c for c in DEFAULT_CAPTURES + VIEW_CAPTURES
                                                  + DIAGRAM_CAPTURES]
    ok = True
    for p in paths:
        if p.exists():
            ok &= check_capture(p, args.verbose)
    bridge = [p for p in paths if p.exists() and Ram(p.read_bytes()[:0x800]).u16(0x718) == 0]
    for p in bridge[:2] if args.fuzz else []:
        ok &= fuzz(p, args.fuzz, seed=1, verbose=args.verbose)
    for p in paths if args.fuzz else []:
        # the diagram captures are runs of passes: fuzz the first of each run
        if p.exists() and Ram(p.read_bytes()[:0x800]).u16(0x718) in SCREEN_RENDER.keys() - {0} and not (
                p.parent.name == "diagrams" and not p.name.endswith("_0_in.bin")):
            ok &= fuzz_view(p, args.fuzz, seed=2, verbose=args.verbose)
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
