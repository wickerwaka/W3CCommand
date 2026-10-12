"""Reference model of the unit layer (docs/units.md), built on tools/stsim.py.

Same conventions as stsim: the state is the game's memory layout (UnitState extends
WorldState and adds UNIT_REGIONS), arithmetic follows the 68000 exactly, and every routine
is checked against the original under Unicorn (tools/check_units.py).

UnitState.sim_pass() is one game_main simulation pass ($40B0-$413E without the UI, sound
and message-line routines, which don't touch simulation state). Ported:
  vehicles_step $1CD4E: carrier_step $1E744 (speed governor, autopilot, grounding), the
    Manta/Walrus loops (idle bays only), laser turret (outside its screen), crane and
    observation-turret angles $1F070, flares and falling drone $1F214, supply transfer (idle),
    lift (idle) and dock door;
  debris_step $1CC86; defence_drones_step $1A296;
  island_behaviours_step $1962A with the AA turret, SAM site creation, command centre,
    drone pad and anti-ship site handlers;
  ai_objects_step $1A56A (dispatch only: no AI handler is ported yet);
  object_passes $1B746: island attach/load/release, both collision passes and their
    scheduling (a contact raises NotImplementedError: the hit handlers aren't ported);
  carrier_systems_status $17578; island_range_check $1B8AE and the hangar timers in the tick;
  the island step's 3D buffer mirroring ($18276, $182A4);
  the shared maths: rotate_yaw $A5E0, rotate_pitch $A620, atan2 $AF8E, aim_angles $1A80C,
  turn_towards $1A18A, self_centre $1E734, yaw_vector $1A140.
Vehicles out of the hangar, the enemy's close-range combat and supply transfers, and the
carrier's weapons live in the mixins stunits_vehicles / stunits_enemy / stunits_carrier
(see the end of this file). Anything not ported raises NotImplementedError rather than
guessing.
"""
import stsim
from stsim import Mem, WorldState, s16, s32, w16

OBJECTS, OBJECT_SIZE, OBJECT_HEADER = stsim.OBJECTS, stsim.OBJECT_SIZE, stsim.OBJECT_HEADER
CARRIER = OBJECTS + OBJECT_SIZE * 10
ISLAND_BUFFERS, ISLAND_BUFFER_SIZE = (0x62CDA, 0x63CEC), 0x12 + 64 * 0x40
DEBRIS, SHARDS = 0x11D8, 0x66010
OBJ_FLAGS, OBJ_PARAMS = 0x19362, 0x193D6
SLOT_ENTRY = 0x18066
ISLAND_HANDLERS, AI_HANDLERS, HIT_HANDLERS = 0x19778, 0x1A5B6, 0x1BD70
FRAME = 0x944
NEW_INDEX = 0x6EE
SINE = 0x1F5A                       # 4096 words, amplitude 16384
ATAN = 0x3F5A                       # atan2 divisor table

# carrier globals
C_FLAGS, C_SUBSTATE, C_RUDDER, C_SPEED, C_TARGET = 0x640, 0x641, 0x642, 0x644, 0x646
C_DEPTH, C_RECOIL, C_WAYPOINT_X, C_WAYPOINT_Z, C_CRUISE = 0x64A, 0x64C, 0x64E, 0x652, 0x656
C_DAMAGE_MSG, C_NEAR_UNIT, C_FAST, C_65C = 0x658, 0x65A, 0x65B, 0x65C
SHIP_FUEL = 0xCFC
ENGINES_STATUS, SUPERSTRUCTURE = 0xAFA, 0xB3C
SLOW_TICK = 0x8FF
MOUSE_DX = 0x1268
POINTER_MODE, FULL_REDRAW = 0x457, 0x458
KEY_SPEED_UP, KEY_SPEED_DOWN, KEY_UP_SCAN, KEY_DOWN_SCAN = 0x88A, 0x88B, 0x828, 0x838
SCREEN = 0x718
VIEWED = 0x9FE
MANTA_LANDING = 0x1232
LIFT_STATE, DOOR_STATE, DOOR_POS, DOCKED_WALRUS = 0xDCE, 0xDDA, 0xDD8, 0xDE6

UNIT_REGIONS = [
    ("pass counters $8EC", 0x8EC, 2),
    ("slow tick $8FF", 0x8FF, 1),
    ("carrier globals", C_FLAGS, 0x65E - C_FLAGS),
    ("ship fuel", SHIP_FUEL, 4),
    ("mouse deltas", MOUSE_DX, 4),
    ("speed keys", KEY_SPEED_UP, 2),
    ("pointer mode", POINTER_MODE, 2),
    ("lift and dock", 0xDC4, 0xDEC - 0xDC4),
    ("landing flag $1232", MANTA_LANDING, 2),
    ("sine table", SINE, 0x2000),
    ("atan table", ATAN, 0x102),
    ("key table", 0x798, 0x884 - 0x798),
    ("view pointers", 0x9FA, 0xC),              # camera $9FA, viewed $9FE, controlled $A02
    ("island buffer 0", ISLAND_BUFFERS[0], ISLAND_BUFFER_SIZE),
    ("island buffer 1", ISLAND_BUFFERS[1], ISLAND_BUFFER_SIZE),
    ("game over $463", 0x463, 5),     # $463-$466, $466 a word (result)
    ("object scratch $6EE", 0x6EE, 4),
    ("turret $6C8", 0x6C8, 0x10),
    ("vehicle bays", 0x496, 0x640 - 0x496),
    ("drone stations", 0x65C, 0x69C - 0x65C),
    ("lift object $6D6", 0x6D6, 0x6E2 - 0x6D6),
    ("score", 0x918, 12),
    ("debug flags $1188", 0x1188, 4),
    ("debris patch $1CBF4", 0x1CBF4, 2),
    ("debris recipes", 0x1C726, 58 * 18),
    ("vector $2C", 0x2C, 4),
    ("hit handler table", HIT_HANDLERS, 58 * 4),
    ("damage table", 0x1BD58, 24),
    ("island count $74E", 0x74E, 2),
    ("cooldowns $93E", 0x93E, 6),
    ("frame counter $944", 0x944, 2),
    ("island context", 0xA28, 0xA58 - 0xA28),
    ("matrices $A68", 0xA68, 12),
    ("collision scratch", 0xA9C, 0xAB6 - 0xA9C),
    ("deck drones $C1A", 0xC1A, 2),
    ("supply transfer", 0xD3C, 0xD4A - 0xD3C),
    ("flares and turret", 0x1150, 0x10),
    ("debris free list", 0x1194, 6),
    ("debris bursts", DEBRIS, 4 * 0x12),
    ("drone launch $122F", 0x122F, 1),
    ("debris shards", SHARDS, 64 * 0x1A),
    ("objects", OBJECTS, 64 * OBJECT_SIZE),
    ("object tables", OBJ_FLAGS, 58 * 2 + 58 * 8),
    ("slot entry table", SLOT_ENTRY, 0x3A * 8),
    ("island handler table", ISLAND_HANDLERS, 58 * 4),
    ("ai handler table", AI_HANDLERS, 58 * 4),
    ("drone tables", 0x1A4AA, 0x1C),
]
UNIT_INPUT_REGIONS = {"sine table", "atan table", "key table", "view pointers", "object tables",
                      "debris recipes", "vector $2C", "hit handler table", "damage table", "debug flags $1188",
                      "slot entry table", "island handler table", "ai handler table", "drone tables"}


class UnitCore(WorldState):
    @classmethod
    def from_ram(cls, ram: bytes) -> "UnitState":
        state = super().from_ram(ram)
        for _, a, n in UNIT_REGIONS:
            state.mem.buf[a:a + n] = ram[a:a + n]
        return state

    def to_ram(self, ram: bytearray) -> None:
        super().to_ram(ram)
        for _, a, n in UNIT_REGIONS:
            ram[a:a + n] = self.mem.buf[a:a + n]

    # ---- maths --------------------------------------------------------------------
    def sin_cos(self, angle: int):
        a = angle & 0xFFF0
        return self.mem.s16(SINE + (a >> 3)), self.mem.s16(SINE + (w16(a + 0x4000) >> 3))

    def rotate_yaw(self, x: int, z: int, angle: int):
        """$A5E0: (x, z) rotated by a 16-bit angle; words in, words out."""
        s, c = self.sin_cos(angle)
        x, z = s16(x), s16(z)
        nx = s16(((x * c + z * s) * 4 & 0xFFFFFFFF) >> 16)
        nz = s16(((z * c - x * s) * 4 & 0xFFFFFFFF) >> 16)
        return nx, nz

    def rotate_pitch(self, x: int, y: int, angle: int):
        """$A620: the pitch counterpart (signs swapped)."""
        s, c = self.sin_cos(angle)
        x, y = s16(x), s16(y)
        nx = s16(((x * c - y * s) * 4 & 0xFFFFFFFF) >> 16)
        ny = s16(((y * c + x * s) * 4 & 0xFFFFFFFF) >> 16)
        return nx, ny

    def atan2(self, dx: int, dz: int) -> int:
        """$AF8E: heading of (dx, dz), 0 = +z, $4000 = +x. Longs are halved until both fit
        in a word."""
        dx, dz = s32(dx), s32(dz)
        while not (-0x8000 <= dx < 0x8000 and -0x8000 <= dz < 0x8000):
            dx >>= 1
            dz >>= 1
        return self._atan_sx(dx, dz)

    def _atan_sx(self, d0: int, d1: int) -> int:       # $AF9A
        if s16(d0) < 0:
            return w16(-self._atan_sz(s16(-d0), d1))
        return self._atan_sz(d0, d1)

    def _atan_sz(self, d0: int, d1: int) -> int:       # $AFA6
        if s16(d1) < 0:
            return w16(-self._atan_oct(d0, s16(-d1)) + 0x8000)
        return self._atan_oct(d0, d1)

    def _atan_oct(self, d0: int, d1: int) -> int:      # $AFB6
        if s16(d0) >= s16(d1):
            return w16(-self._atan_core(d1, d0) + 0x4000)
        return self._atan_core(d0, d1)

    def _atan_core(self, d0: int, d1: int) -> int:     # $AFC6
        if w16(d1) == 0:
            return w16(d0)
        q = ((w16(d0) << 16) >> 1)                          # swap/clr/lsr.l #1 (unsigned)
        n = s16(d1)
        qq = int(q / n) if n else 0                         # divs.w: truncates towards zero
        if not -0x8000 <= qq < 0x8000:
            return 0x2000
        q = w16(qq)
        v = q << 8
        t = self.mem.u16(ATAN + 2 * (v >> 16))
        res = v // t if t else 0
        lo = w16(res) if res <= 0xFFFF else w16(v)          # divu overflow leaves d1
        return w16(lo << 4)

    @staticmethod
    def turn_towards(target: int, current: int, step: int) -> int:
        """$1A18A: move current towards target by at most step (unsigned clamp)."""
        d = w16(target - current)
        mag = w16(-s16(d)) if d & 0x8000 else d
        if mag > w16(step):
            mag = w16(step)
        return w16(current + (w16(-mag) if d & 0x8000 else mag))

    @staticmethod
    def self_centre(v: int) -> int:
        """$1E734: v - v/8 (at least 1 towards 0)."""
        v = s16(v)
        if v == 0:
            return 0
        d = v >> 3
        if d == 0:
            d = 1
        return w16(v - d)

    def yaw_vector(self, obj: int, speed: int):
        """$1A140: (0, speed) rotated by the object's heading, as longs."""
        return self.rotate_yaw(0, speed, self.mem.u16(obj + 6))

    def island_box_distance(self, obj: int, buf: int):
        """$1A1A2: |dx| - half_x, |dz| - half_z from the island buffer's origin."""
        m = self.mem
        dx = abs(s32(m.u32(buf + 2) - m.u32(obj + 0x0A)))
        dz = abs(s32(m.u32(buf + 0x0A) - m.u32(obj + 0x12)))
        return s32(dx - m.s16(buf + 0x30)), s32(dz - m.s16(buf + 0x32))

    # ---- carrier -------------------------------------------------------------------
    def carrier_step(self) -> None:
        """$1E744: player carrier control and motion (object 10)."""
        m, o = self.mem, CARRIER
        if m.u16(SUPERSTRUCTURE) == 0:
            m.set8(0x463, 0xFF)
            m.set16(0x466, 1)
            self.events.append(("carrier destroyed",))
        self.manta_lift_step()
        self.walrus_dock_door_step()
        if m.u16(C_DAMAGE_MSG):
            m.set16(C_DAMAGE_MSG, m.u16(C_DAMAGE_MSG) - 1)
        if not m.u8(o + 1) & 1:
            return
        if m.u8(C_FLAGS) & 1:
            self.carrier_scripted()
        elif m.u16(SCREEN) == 0:
            if not m.u8(POINTER_MODE) and not m.u8(FULL_REDRAW):
                m.set8(C_FLAGS, m.u8(C_FLAGS) & 0x7F)
                r = s16(m.u16(C_RUDDER) + m.u16(MOUSE_DX))
                m.set16(MOUSE_DX, 0)
                m.set16(C_RUDDER, max(-255, min(255, r)))
            if m.u8(KEY_SPEED_UP) or m.u8(KEY_UP_SCAN):
                m.set16(C_TARGET, m.u16(C_SPEED) + 1)
            elif m.u8(KEY_SPEED_DOWN) or m.u8(KEY_DOWN_SCAN):
                m.set16(C_TARGET, m.u16(C_SPEED) - 1)
        self.carrier_move()

    def carrier_scripted(self) -> None:
        """$1E95C: recoil after grounding ($641 = 0), else the autopilot."""
        m, o = self.mem, CARRIER
        if not m.u8(C_SUBSTATE):
            if self.countdown16(C_RECOIL):
                m.set8(C_FLAGS, m.u8(C_FLAGS) & 0x7E)
                m.set8(C_SUBSTATE, 1)
            return
        if not m.u8(C_FLAGS) & 2:
            return self.carrier_stop()
        dx = s32(m.u32(C_WAYPOINT_X) - m.u32(o + 0x0A))
        dz = s32(m.u32(C_WAYPOINT_Z) - m.u32(o + 0x12))
        d = max(abs(dx), abs(dz))
        cruise = m.s16(C_CRUISE)
        if d <= 2000 and cruise > 0x20:
            cruise = 0x20
        m.set16(C_TARGET, cruise)
        if d < 1000:
            if m.u8(C_FLAGS) & 4:
                self.events.append(("message", "carrier reached waypoint", self._ping()))
            m.set8(C_FLAGS, m.u8(C_FLAGS) & 0xFC)
            return self.carrier_stop()
        err = s16(self.atan2(dx, dz) - m.u16(o + 6))
        if abs(err) > 0x100:
            m.set16(C_TARGET, 0x10)
        want = max(-255, min(255, err >> 2))
        m.set16(C_RUDDER, self.turn_towards(want, m.u16(C_RUDDER), 0x20))

    def carrier_stop(self) -> None:
        """$1E986: rudder self-centres, target speed 0."""
        self.mem.set8(C_FLAGS, self.mem.u8(C_FLAGS) | 0x80)
        self.mem.set16(C_TARGET, 0)

    def carrier_move(self) -> None:
        """$1E802: fuel, speed governor, heading and position, depth and grounding."""
        m, o = self.mem, CARRIER
        if m.u8(SLOW_TICK):
            burn = abs(m.s16(C_SPEED)) * 4
            if m.u8(C_FAST):
                burn *= 4
            burn = s16(burn)                            # add.w, then ext.l
            fuel = m.u32(SHIP_FUEL) - (burn & 0xFFFFFFFF)
            m.set32(SHIP_FUEL, 0 if fuel < 0 else fuel)
        self.carrier_speed_governor()
        if m.u8(C_FLAGS) & 0x80:
            if m.u16(C_RUDDER) == 0:
                m.set8(C_FLAGS, m.u8(C_FLAGS) & 0x7F)
            else:
                m.set16(C_RUDDER, self.self_centre(m.u16(C_RUDDER)))
        m.set16(o + 6, m.u16(o + 6) + int(m.s16(C_RUDDER) / 6))
        m.set8(C_FAST, 0)
        speed = m.u16(C_SPEED)
        if not (m.u8(C_NEAR_UNIT) or m.u16(C_65C) or m.u8(MANTA_LANDING) or m.u32(o + 0x3C)):
            speed = w16(speed * 4)
            m.set8(C_FAST, 0xFF)
        vx, vz = self.yaw_vector(o, speed)
        m.set32(o + 0x0A, m.u32(o + 0x0A) + vx)
        m.set32(o + 0x12, m.u32(o + 0x12) + vz)
        m.set16(C_DEPTH, 0x24)
        buf = m.u32(o + 0x3C)
        if buf:
            dx, dz = self.island_box_distance(o, buf)
            d = s32(max(dx, dz) - 10000)
            q = int(d / 256)
            if not -0x8000 <= q < 0x8000:
                raise NotImplementedError("depth divide overflow")
            depth = s16(q)
            if depth < 0 and not (m.u8(C_FLAGS) & 1 and not m.u8(C_SUBSTATE)):
                self.carrier_ground()
                depth = 0
            m.set16(C_DEPTH, max(0, min(0x24, depth)))

    def carrier_ground(self) -> None:
        """$1E8CE: run aground: superstructure -1000, message and sound, reverse recoil."""
        m = self.mem
        v = m.u16(SUPERSTRUCTURE) - 1000
        m.set16(SUPERSTRUCTURE, 0 if v < 0 else v)
        m.set16(0x952, 2)
        self.events.append(("message", "Carrier has grounded", self._ping(True)))
        self._sfx(1)
        m.set8(C_FLAGS, m.u8(C_FLAGS) | 0x81)
        m.set8(C_SUBSTATE, 0)
        m.set16(C_RECOIL, 50)
        back = 0x20 if m.s16(C_SPEED) < 0 else -0x20
        m.set16(C_SPEED, 0)
        m.set16(C_TARGET, back)
        if m.u32(VIEWED) == CARRIER:
            m.set8(POINTER_MODE, 0xFF)

    def carrier_speed_governor(self) -> None:
        """$1EA3E: clamp the target to what engines, fuel and surroundings allow, then
        approach it by 1/32 of the difference (at least 1)."""
        m, o = self.mem, CARRIER
        if m.u32(SHIP_FUEL) == 0:
            target = 0
        else:
            status = m.u16(ENGINES_STATUS)
            fwd = status >> 1
            if s16(fwd) > 0x80:
                fwd = 0x80
            if (m.u16(C_65C) or m.u8(MANTA_LANDING) or m.u32(o + 0x3C)) and s16(fwd) > 0x3C:
                fwd = 0x3C
            back = s16(w16(-status)) >> 3
            target = m.s16(C_TARGET)
            if target > s16(fwd):
                target = s16(fwd)
            elif target < back:
                target = back
        d = s16(target - m.u16(C_SPEED))
        if d:
            step = d >> 5
            if step == 0:
                step = 1
            m.set16(C_SPEED, m.u16(C_SPEED) + step)

    # ---- lift and dock --------------------------------------------------------------
    def manta_lift_step(self) -> None:
        """$1EAA0: the Manta lift state machine (only the idle state is ported)."""
        if self.mem.u16(LIFT_STATE):
            raise NotImplementedError(f"lift state {self.mem.u16(LIFT_STATE)}")

    def walrus_dock_door_step(self) -> None:
        """$1ED94: the Walrus dock door: 1 opening (closes again if no Walrus is docked),
        2 open, 3 closing."""
        m = self.mem
        state = m.u16(DOOR_STATE)
        if state == 0:
            return
        if state != 3:
            if m.s16(DOCKED_WALRUS) >= 0:
                if state == 1:
                    pos = s16(m.u16(DOOR_POS) + 0x5DC)
                    if pos >= 0:
                        pos = 0
                        m.set16(DOOR_STATE, 2)
                    m.set16(DOOR_POS, pos)
                return
            m.set16(DOOR_STATE, 3)
        pos = s16(m.u16(DOOR_POS) - 0x5DC)
        if pos < -0x3000:
            self.dock_attach()
            pos = -0x3000
            m.set16(DOOR_STATE, 0)
        m.set16(DOOR_POS, pos)


    # ---- objects --------------------------------------------------------------------
    def obj(self, k: int) -> int:
        return OBJECTS + OBJECT_SIZE * k

    def init_world_object(self, a: int, t: int) -> None:
        """$192E2: clear the header, stamp all 128 schedule words with the frame, set
        type, flags and the per-type collision parameters."""
        m = self.mem
        m.buf[a:a + 0x40] = bytes(0x40)
        f = m.u16(FRAME)
        for k in range(128):
            m.set16(a + 0x40 + 2 * k, f)
        self._init_common(a, t)

    def init_island_object(self, a: int, t: int) -> None:
        """$1932C: the same for a $40-byte island-buffer entry (no schedules)."""
        self.mem.buf[a:a + 0x40] = bytes(0x40)
        self._init_common(a, t)

    def _init_common(self, a: int, t: int) -> None:
        m = self.mem
        m.set16(a + 2, t)
        m.set16(a, m.u16(OBJ_FLAGS + 2 * t))
        for k in range(4):
            m.set16(a + 0x30 + 2 * k, m.u16(OBJ_PARAMS + 8 * t + 2 * k))

    def reset_island_schedule(self) -> None:
        """$195A6: a new island entry [$6EE]: every colliding object attached to this buffer
        tests it next pass."""
        m = self.mem
        buf, k, f = m.u32(0xA28), m.u16(NEW_INDEX), m.u16(FRAME)
        for i in range(64):
            o = self.obj(i)
            if m.u16(o) & 0x401 == 0x401 and m.u32(o + 0x3C) == buf:
                m.set16(o + 0x40 + 2 * k, f)

    def reset_pair_schedules(self) -> None:
        """$195DA: a new world object [$6EE]: every object tests it next pass."""
        m = self.mem
        off, f = 0xC0 + 2 * m.u16(NEW_INDEX), m.u16(FRAME)
        for i in range(64):
            m.set16(self.obj(i) + off, f)

    def find_free_object(self):
        """$1A10E: the first object 24..[$A48]-1 with byte +1 bit 0 clear, index to $6EE."""
        m = self.mem
        n = s16(m.u16(0xA48) - 24)
        if n <= 0:
            return None
        m.set16(NEW_INDEX, 24)
        for k in range(n):
            a = self.obj(24 + k)
            if not m.u8(a + 1) & 1:
                return a
            m.set16(NEW_INDEX, 25 + k)
        return None

    def find_free_island_object(self):
        """$1A0DC: a free dynamic entry (32 up) of the current buffer [$A28]."""
        m = self.mem
        buf = m.u32(0xA28)
        n = s16(m.u16(buf + 0x0E) - 0x21)
        if n < 0:
            return None
        m.set16(NEW_INDEX, 0x1F)
        a = buf + 0x812
        for _ in range(n + 1):
            m.set16(NEW_INDEX, m.u16(NEW_INDEX) + 1)
            if not m.u8(a + 1) & 1:
                return a
            a += 0x40
        return None

    def aim_angles(self, dx: int, dy: int, dz: int):
        """$1A80C: (pitch, heading) towards a 32-bit offset; scratch at $A80-$A85."""
        m = self.mem
        dx, dy, dz = s32(dx) >> 1, s32(dy) >> 1, s32(dz) >> 1
        m.set16(0xA84, -dy)
        m.set16(0xA80, dx)
        m.set16(0xA82, dz)
        heading = self.atan2(dx, dz)
        _, z = self.rotate_yaw(m.s16(0xA80), m.s16(0xA82), w16(-heading))
        pitch = self.atan2(m.s16(0xA84), z)
        return pitch, heading

    # ---- pass ----------------------------------------------------------------------
    def sim_pass(self) -> None:
        """One game_main simulation pass ($40B0-$413E) minus UI, sound and messages."""
        m = self.mem
        if m.u16(stsim.COUNT_ENEMY) == 0x40:
            m.set16(0x466, 2)
            m.set8(0x463, 0xFF)
        elif m.u16(stsim.COUNT_PLAYER) == 0x40:
            m.set16(0x466, 3)
            m.set8(0x463, 0xFF)
        if m.u8(stsim.ROUTES_PENDING):
            self.relax_routes()
        m.set8(0x8FE, 0)                     # $8FE: set on passes that run the tick (never read)
        if m.u8(0x8FD):
            m.set8(0x8FE, 0xFF)
            self.logistics_tick()
        self.step()
        self.debris_step()
        self.vehicles_step()
        self.island_behaviours_step()
        self.ai_objects_step()
        self.enemy_ai()
        self.defence_drones_step()
        self.object_passes()
        self.carrier_systems_status()

    # ---- vehicles -------------------------------------------------------------------
    def vehicles_step(self) -> None:
        """$1CD4E."""
        m = self.mem
        c = (m.u16(0x8EC) + 1) & 15
        m.set16(0x8EC, c)
        m.set8(SLOW_TICK, 0xFF if c == 0 else 0)
        if m.u16(0x115E):
            m.set16(0x115E, m.u16(0x115E) - 1)
        self.carrier_step()
        m.set8(C_NEAR_UNIT, 0)
        self.bays_step(0x940, 2, 0x496, "Manta")
        self.bays_step(0x942, 6, 0x570, "Walrus")
        self.carrier_laser_turret_step()
        self.crane_step()
        self.flares_step()
        self.supply_transfer_step()
        m.set8(KEY_SPEED_UP, 0)
        m.set8(KEY_SPEED_DOWN, 0)
        if not m.u8(POINTER_MODE):
            m.set16(MOUSE_DX, 0)
            m.set16(MOUSE_DX + 2, 0)

    def bays_step(self, cooldown: int, first: int, records: int, kind: str) -> None:
        """$1CDA2 / $1DCFC: per bay, vehicles that are out (status >= 3) fly or drive."""
        m = self.mem
        if m.u16(cooldown):
            m.set16(cooldown, m.u16(cooldown) - 1)
        m.set16(0x6F0, first)
        for k in range(4):
            if m.s8(records + 0x34 * k + 1) >= 3:
                raise NotImplementedError(f"{kind} {k} out of the hangar")
            m.set16(0x6F0, first + k + 1)

    def carrier_laser_turret_step(self) -> None:
        """$1EDFC: cooldown and heat always; aiming and firing on screen 8 (not ported);
        otherwise the aim rates self-centre and integrate into elevation/heading."""
        m = self.mem
        if m.u16(0x93E):
            m.set16(0x93E, m.u16(0x93E) - 1)
        h = m.u16(0x6C8)
        if h:
            h = s16(h - 16)
            m.set16(0x6C8, 0 if h < 0 else h)
        if m.u16(SCREEN) == 8:
            raise NotImplementedError("laser turret screen")
        for bit, rate in ((0x80, 0x6CE), (0x40, 0x6D0)):
            if m.u8(0x6D5) & bit:
                if m.u16(rate) == 0:
                    m.set8(0x6D5, m.u8(0x6D5) & ~bit)
                else:
                    m.set16(rate, self.self_centre(m.u16(rate)))
        e = s16(-self.quantise(m.u16(0x6D0)) + m.u16(0x6CA))
        m.set16(0x6CA, 0x1F4 if e > 0x1F4 else -16000 if e < -16000 else e)
        m.set16(0x6CC, m.u16(0x6CC) + 2 * self.quantise(m.u16(0x6CE)))

    @staticmethod
    def quantise(v: int) -> int:
        """$1F03E: drop the low 4 bits of the magnitude."""
        v = s16(v)
        return w16(-((-v) & 0xFFF0)) if v < 0 else v & 0xFFF0

    def set_matrix(self, angle: int) -> None:
        """$A438 with a2 = $A6C: (sin, cos) of the angle."""
        s, c = self.sin_cos(angle)
        self.mem.set16(0xA6C, s)
        self.mem.set16(0xA6E, c)

    def matrix_rotate(self, x: int, z: int):
        """$A558: rotate by the (sin, cos) pair at $A6C."""
        m = self.mem
        s, c = m.s16(0xA6C), m.s16(0xA6E)
        x, z = s16(x), s16(z)
        return (s16(((x * c + z * s) * 4 & 0xFFFFFFFF) >> 16),
                s16(((z * c - x * s) * 4 & 0xFFFFFFFF) >> 16))

    def _bearing_from_carrier(self, target: int, ox: int, oz: int) -> int:
        """Shared by $1F070: heading from a point on the carrier to an object, relative to
        the carrier's heading."""
        m, c = self.mem, CARRIER
        x, z = self.matrix_rotate(ox, oz)
        dx = s32(m.u32(target + 0x0A) - (x + m.u32(c + 0x0A)))
        dz = s32(m.u32(target + 0x12) - (z + m.u32(c + 0x12)))
        return w16(self.atan2(dx, dz) - m.u16(c + 6))

    def crane_step(self) -> None:
        """$1F070: animation angles: lift crane $DDE, dock crane $DEA, observation turret
        $1158/$115A tracking the object [$1154]."""
        m, c = self.mem, CARRIER
        self.set_matrix(m.u16(c + 6))
        bay = m.s16(0xDD0)
        if bay >= 0:
            want, step = self._bearing_from_carrier(self.obj(2 + bay), 0x1A4, 0x41A), 0x400
        else:
            want, step = 0x93A0, 0x100
        m.set16(0xDDE, self.turn_towards(want, m.u16(0xDDE), step))
        d = m.s16(DOCKED_WALRUS)
        if d >= 0:
            if d == 4:
                t = self.obj(16)
            else:
                t = (self.obj(11) if m.u8(0x122F) else self.obj(6)) + OBJECT_SIZE * d
            want = self._bearing_from_carrier(t, -0x1A4, -0x41A)
            if want < 0x4000:
                want = 0x4000
            step = 0x400
        else:
            want, step = 0x4000, 0x100
        m.set16(0xDEA, self.turn_towards(want, m.u16(0xDEA), step))
        t = m.u32(0x1154)
        if m.u8(t + 1) & 1:
            x, z = self.matrix_rotate(0, -1000)
            dx = s32(m.u32(t + 0x0A) - (x + m.u32(c + 0x0A)))
            dz = s32(m.u32(t + 0x12) - (z + m.u32(c + 0x12)))
            dy = s32(m.u32(t + 0x0E) - 300)
            if all(-0x8000 <= v < 0x8000 for v in (dx, dy, dz)):
                pitch, heading = self.aim_angles(dx, dy, dz)
                m.set16(0x1158, self.turn_towards(pitch, m.u16(0x1158), 0x800))
                m.set16(0x115A, self.turn_towards(heading, m.u16(0x115A), 0x800))
                return
        m.set16(0x1158, self.turn_towards(0, m.u16(0x1158), 0x100))
        m.set16(0x115A, self.turn_towards(m.u16(c + 6), m.u16(0x115A), 0x100))

    def flares_step(self) -> None:
        """$1F214: decoy flares (objects 17, 18) and the falling object 15."""
        m = self.mem
        if m.u16(0x1150):
            m.set16(0x1150, m.u16(0x1150) - 1)
        if m.u16(0x1152):
            if m.u16(0x1152) == 2:
                m.set16(0x1150, 1)
            for k in (17, 18):
                a = self.obj(k)
                if not m.u8(a + 1) & 1:
                    continue
                if m.u16(a + 2) != 0x1E:
                    dy = -15
                else:
                    dy = m.s16(a + 0x22)
                    v = s16(dy - 10)
                    if v < -10:
                        m.set16(a + 2, 0x1F)
                        m.set16(a + 0x1E, 0x32)
                    m.set16(a + 0x22, v)
                m.set32(a + 0x0E, m.u32(a + 0x0E) + dy)
                vx, vz = self.yaw_vector(a, 15)
                m.set32(a + 0x0A, m.u32(a + 0x0A) + vx)
                m.set32(a + 0x12, m.u32(a + 0x12) + vz)
                v = m.s16(a + 0x1E)
                if m.u8(a + 0x1A):
                    v = s16(v - 5)
                    if v < 0x1E:
                        m.set8(a + 0x1A, 0)
                else:
                    v = s16(v + 5)
                    if v > 0x64:
                        m.set8(a + 0x1A, 0xFF)
                m.set16(a + 0x1E, v)
                if self.decrement_borrows(a + 0x20):
                    m.set16(a, 0)
                    m.set16(0x1152, m.u16(0x1152) - 1)
        a = self.obj(15)
        if m.u8(a + 1) & 1:
            dy = m.s16(a + 0x22)
            v = s16(dy - 2)
            m.set16(a + 0x22, -40 if v < -40 else v)
            m.set32(a + 0x0E, m.u32(a + 0x0E) + dy)
            sp = s16(m.u16(a + 0x24) - 1)
            sp = 0 if sp < 0 else sp
            m.set16(a + 0x24, sp)
            vx, vz = self.yaw_vector(a, sp)
            m.set32(a + 0x0A, m.u32(a + 0x0A) + vx)
            m.set32(a + 0x12, m.u32(a + 0x12) + vz)
            if self.decrement_borrows(a + 0x20):
                m.set16(a, 0)

    def decrement_borrows(self, a: int) -> bool:
        """subq.w #1 / bcc: decrement a word; True if it was 0 (borrow)."""
        v = self.mem.u16(a)
        self.mem.set16(a, v - 1)
        return v == 0

    def supply_transfer_step(self) -> None:
        """$1F316: the supply drone's transfer state machine (only the idle path)."""
        m = self.mem
        if m.u8(0xD3E):
            raise NotImplementedError("supply transfer")
        if m.u16(0xD44) and m.u8(0xD3F):
            m.set16(0xD44, m.u16(0xD44) - 1)
        m.set8(0xD3F, 0)

    # ---- debris ---------------------------------------------------------------------
    def debris_step(self) -> None:
        """$1CC86: four bursts of shards falling under gravity."""
        m = self.mem
        for b in range(4):
            a = DEBRIS + 0x12 * b
            if not m.u8(a) & 1:
                continue
            age = (m.u8(a + 1) + 1) & 0xFF
            m.set8(a + 1, age)
            if age == 0:
                self.free_burst(a)
                continue
            if m.u32(a + 0x0E) == 0:
                m.set8(a, m.u8(a) & 0xFE)
                continue
            floor = s16(-m.u32(a + 6) + 10)
            prev = a - 8                     # prev + $16 aliases the burst's list head
            cur = m.u32(a + 0x0E)
            while cur:
                m.set16(cur + 0x0A, m.u16(cur + 0x0A) + m.u16(cur + 0x10))
                m.set16(cur + 0x0E, m.u16(cur + 0x0E) + m.u16(cur + 0x14))
                vy = m.s16(cur + 0x12)
                y = s16(m.u16(cur + 0x0C) + vy)
                if y < floor:
                    m.set32(prev + 0x16, m.u32(cur + 0x16))
                    m.set32(cur + 0x16, m.u32(0x1194))
                    m.set32(0x1194, cur)
                    m.set16(0x1198, m.u16(0x1198) + 1)
                else:
                    m.set16(cur + 0x0C, y)
                    if vy < -40:
                        vy = s16(vy - ((vy + 40) >> 2))
                    else:
                        vy = s16(vy - 10)
                        if vy < -40:
                            vy = -40
                    m.set16(cur + 0x12, vy)
                    for o in (4, 6, 8):
                        m.set16(cur + o, m.u16(cur + o) + 0x1000)
                    prev = cur
                cur = m.u32(prev + 0x16)

    def free_burst(self, a: int) -> None:
        """$1CC5C: return a burst's shards to the free list."""
        m = self.mem
        if not m.u8(a) & 1:
            return
        m.set8(a, m.u8(a) & 0xFE)
        cur = m.u32(a + 0x0E)
        if not cur:
            return
        old = m.u32(0x1194)
        m.set32(0x1194, cur)
        while True:
            m.set16(0x1198, m.u16(0x1198) + 1)
            nxt = m.u32(cur + 0x16)
            if not nxt:
                break
            cur = nxt
        m.set32(cur + 0x16, old)

    # ---- defence drones -------------------------------------------------------------
    DRONE_CORNERS = ((1400, -2000), (-1400, -2000), (1400, 2000), (-1400, 2000))

    def defence_drones_step(self) -> None:
        """$1A296: the carrier's four defence drones (objects 11-14) and their stations."""
        m = self.mem
        if m.u8(0x122F):
            k = m.u16(0x69A)
            rec = 0x65E + 14 * k
            flags = m.u16(rec)
            if not flags & 1:
                k += 1
                m.set16(0x69A, k)
                if k >= 4:
                    m.set8(0x122F, 0)
                    return
            elif flags & 2:
                if not flags & 8:
                    m.set16(DOCKED_WALRUS, k)
                    m.set16(DOOR_STATE, 1)
                    self.dock_attach()
                    m.set8(rec + 1, m.u8(rec + 1) | 8)
                z = m.s16(rec + 4)
                if z >= -600:
                    if m.u16(DOOR_STATE) == 0:
                        m.set8(rec + 1, m.u8(rec + 1) & 0xFE)
                        m.set16(DOCKED_WALRUS, 0xFFFF)
                        m.set16(0xC1A, m.u16(0xC1A) + 1)
                        self.dock_attach()
                else:
                    z = s16(z + 30)
                    m.set16(rec + 4, z)
                    if z >= -600:
                        m.set16(DOOR_STATE, 3)
                        self.dock_attach()
        m.set16(0x65C, 0)
        for k in range(4):
            a, rec = self.obj(11 + k), 0x65E + 14 * k
            m.set16(a + 6, m.u16(a + 6) + 0x1F4)
            flags = m.u16(rec)
            m.set8(a + 1, m.u8(a + 1) & 0xFE)
            if not flags & 1:
                continue
            m.set16(0x65C, m.u16(0x65C) + 1)
            m.set8(a + 1, m.u8(a + 1) | 1)
            if not flags & 2:
                if flags & 4:
                    self._drone_move(rec)
                else:
                    self._drone_route(rec)
            c = CARRIER
            x, z = m.u16(rec + 2), m.u16(rec + 4)
            m.set16(a + 0x1E, z)
            vx, vz = self.rotate_yaw(x, z, m.u16(c + 6))
            m.set32(a + 0x0A, m.u32(c + 0x0A) + vx)
            m.set32(a + 0x12, m.u32(c + 0x12) + vz)

    @staticmethod
    def _near(d0, d1, d2, d3, lim) -> bool:
        """$1A466: |d2-d0| < lim and |d3-d1| < lim, unsigned."""
        def adiff(a, b):
            d = (b - a) & 0xFFFF
            return d if b & 0xFFFF >= a & 0xFFFF else w16(-d)
        return adiff(d0, d2) < lim and adiff(d1, d3) < lim

    def _drone_move(self, rec: int) -> None:
        m = self.mem
        x, z, tx, tz = m.u16(rec + 2), m.u16(rec + 4), m.u16(rec + 6), m.u16(rec + 8)
        if self._near(x, z, tx, tz, 10):
            m.set8(rec + 1, m.u8(rec + 1) & ~4)
            if tx == m.u16(rec + 0x0A) and tz == m.u16(rec + 0x0C):
                m.set16(rec + 2, m.u16(rec + 0x0A))
                m.set16(rec + 4, m.u16(rec + 0x0C))
                m.set8(rec + 1, m.u8(rec + 1) | 2)
            return
        div = 10 if self._near(x, z, tx, tz, 40) else 30
        dx, dz = s16(tx - x), s16(tz - z)
        steps = s16(abs(dx) + abs(dz))
        steps = int(steps / div)
        if steps == 0:
            raise NotImplementedError("drone divide by zero")
        m.set16(rec + 2, x + int(dx / steps))
        m.set16(rec + 4, z + int(dz / steps))

    @staticmethod
    def _region(x: int, z: int) -> int:
        """$1A47E: which side of the carrier's box a point is on."""
        x, z = s16(x), s16(z)
        c = 0
        if x <= -900:
            c |= 1
        if x >= 900:
            c |= 2
        if z >= 1500:
            c |= 8
        if z <= -1500:
            c |= 4
        return c

    def _drone_route(self, rec: int) -> None:
        m = self.mem
        here = self._region(m.u16(rec + 2), m.u16(rec + 4))
        there = self._region(m.u16(rec + 0x0A), m.u16(rec + 0x0C))
        if here & there:
            tx, tz = m.u16(rec + 0x0A), m.u16(rec + 0x0C)
        else:
            a, b = m.u8(0x1A4BA + here), m.u8(0x1A4BA + there)
            sel = (a & b) or a
            if sel == 0:
                raise NotImplementedError("drone route: no corner (original loops forever)")
            tx, tz = self.DRONE_CORNERS[(sel & -sel).bit_length() - 1]
        m.set16(rec + 6, tx)
        m.set16(rec + 8, tz)
        m.set8(rec + 1, m.u8(rec + 1) | 4)

    # ---- island behaviours ------------------------------------------------------------
    def island_behaviours_step(self) -> None:
        """$1962A: run the behaviours of both loaded island buffers."""
        for buf in ISLAND_BUFFERS:
            if self.mem.u8(buf) & 1:
                self._island_behaviours(buf)

    def _island_behaviours(self, buf: int) -> None:
        m = self.mem
        m.set8(0xA43, 0)
        m.set8(0xA42, 0)
        cam = m.u32(0x9FA)
        if cam:
            m.set8(0xA43, 0xFF if m.u32(cam + 0x3C) == buf else 0)
        m.set32(0xA28, buf)
        for k in range(3):
            m.set32(0xA4A + 4 * k, m.u32(buf + 2 + 4 * k))
        i = m.u8(buf + 1)
        m.set16(0xA34, i)
        owner = self.owner(i)
        m.set32(0xA2C, stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i)
        m.set8(0xA45, 0xFF if owner == 0 else 0)
        if owner:
            m.set8(0xA44, 0xFF if owner == 1 else 0)
            first, n = (19, 5) if owner == 1 else (2, 9)
            for k in range(n):
                o = self.obj(first + k)
                if m.u16(o) & 0x201 == 0x201 and m.u32(o + 0x3C) == buf:
                    m.set8(0xA42, 0xFF)
                    break
        for j in range(m.u16(buf + 0x0E)):
            e = buf + 0x12 + 0x40 * j
            f = m.u16(e)
            if f & 0x101 != 0x101:
                continue
            if not m.u8(0xA43) and f & 0x200:
                continue
            for k in range(3):
                m.set32(0xA36 + 4 * k, m.u32(buf + 2 + 4 * k) + m.s16(e + 0x0A + 2 * k))
            h = m.u32(ISLAND_HANDLERS + 4 * m.u16(e + 2))
            if h == 0x19860:
                continue
            fn = self.ISLAND_BEHAVIOURS.get(h)
            if fn is None:
                raise NotImplementedError(f"island behaviour ${h:05X} (type {m.u16(e + 2)})")
            fn(self, e)

    def nearest_hostile(self, mantas_only: bool = False):
        """$1A1CC / $1A1D8: the nearest opposing unit attached to this buffer and in range
        (flag bit 9). Returns (object, Chebyshev distance) or (None, -1)."""
        m = self.mem
        if m.u8(0xA44):
            first, n = 20, 4
        else:
            first, n = 2, 4 if mantas_only else 8
        buf, x0, z0 = m.u32(0xA28), m.u32(0xA36), m.u32(0xA3E)
        best, best_d = None, 0xFFFFFFFF
        for k in range(n):
            o = self.obj(first + k)
            if not m.u8(o + 1) & 1 or not m.u16(o) & 0x200 or m.u32(o + 0x3C) != buf:
                continue
            dx = abs(s32(m.u32(o + 0x0A) - x0)) & 0xFFFFFFFF
            dz = abs(s32(m.u32(o + 0x12) - z0)) & 0xFFFFFFFF
            d = dz if s32(dz) > s32(dx) else dx
            if d <= best_d:
                best, best_d = o, d
        return best, (-1 if best is None else s32(best_d))

    def target_local(self, t: int):
        """$1A262: the target's offset from its buffer origin, or None if it has no buffer
        or the offset doesn't fit in 16 bits."""
        m = self.mem
        buf = m.u32(t + 0x3C)
        if not buf:
            return None
        d = [s32(m.u32(t + 0x0A + 4 * k) - m.u32(buf + 2 + 4 * k)) for k in range(3)]
        return d if all(-0x8000 <= v < 0x8000 for v in d) else None

    def _byte_timer(self, a: int) -> bool:
        """tst.b / subq.b / bne: True when the countdown runs out this pass."""
        v = self.mem.u8(a)
        if v:
            v = (v - 1) & 0xFF
            self.mem.set8(a, v)
        return v == 0

    def _word_timer(self, a: int) -> bool:
        v = self.mem.u16(a)
        if v:
            v = w16(v - 1)
            self.mem.set16(a, v)
        return v == 0

    def aa_turret(self, e: int) -> None:
        """$199B4: track the nearest hostile in range and fire SAMs (type 28)."""
        m = self.mem
        if m.u8(0xA45):
            return
        if self._byte_timer(e + 0x1B):
            m.set32(e + 0x1E, 0)
            m.set8(e + 0x1B, ((self.rand() & 7) + 8) & 0xFF)
            t, d = self.nearest_hostile()
            if d >= 0:
                y = s32(m.u32(t + 0x0E))
                if d > 8000 or y > 2300 or y < 60:
                    m.set8(e + 0x1C, 0)
                else:
                    m.set32(e + 0x1E, t)
        t = m.u32(e + 0x1E)
        if not t:
            m.set16(e + 6, m.u16(e + 6) + 0x100)
            return
        loc = self.target_local(t)
        if loc is None:
            return
        pitch, heading = self.aim_angles(*(loc[k] - m.s16(e + 0x0A + 2 * k) for k in range(3)))
        m.set16(e + 4, self.turn_towards(pitch, m.u16(e + 4), 0x200))
        m.set16(e + 6, self.turn_towards(heading, m.u16(e + 6), 0x800))
        if abs(s16(m.u16(e + 6) - heading)) > 0x200:
            return
        if not self._byte_timer(e + 0x1C):
            return
        m.set8(e + 0x1C, ((self.rand() & 0x1F) + 0x20) & 0xFF)
        a = self.find_free_island_object()
        if a is None:
            return
        self.create_sam_ahead(e, a)
        m.set8(a + 0x1B, 0x64)
        m.set32(a + 0x1E, t)
        m.set16(a + 0x22, 0x3C)

    def command_centre_defence(self, e: int) -> None:
        """$19B44: at difficulty >= $4000, SAMs at low Mantas within 4000."""
        m = self.mem
        if m.u8(0xA45):
            return
        if m.s16(0x444) >= 0x4000 and m.u8(e + 0x1B) == 0:
            m.set8(e + 0x1B, ((self.rand() & 0xF) + 0x10) & 0xFF)
            t, d = self.nearest_hostile(mantas_only=True)
            if d >= 0 and d <= 4000 and s32(m.u32(t + 0x0E)) <= 1500:
                a = self.find_free_island_object()
                if a is not None:
                    self.init_island_object(a, 0x1C)
                    self.reset_island_schedule()
                    m.set32(a + 0x1E, t)
                    m.set16(a + 0x0A, m.u16(e + 0x0A))
                    m.set16(a + 0x0C, 0x118)
                    m.set16(a + 0x0E, m.u16(e + 0x0E))
                    m.set16(a + 4, 0xC000)
                    m.set8(a + 0x1B, 0x3C)
                    m.set16(a + 0x22, 0x1E)
        m.set8(e + 0x1B, m.u8(e + 0x1B) - 1)

    def drone_pad(self, e: int) -> None:
        """$19F00: launch island drones (type 22) while hostiles are present."""
        m = self.mem
        if not self._word_timer(e + 0x1E):
            return
        if m.u8(0xA45) or not m.u8(0xA42):
            return
        count = m.u32(0xA2C) + 0x10
        if m.u16(0x446) == m.u16(count):
            return
        a = self.find_free_island_object()
        if a is None:
            return
        m.set16(count, m.u16(count) + 1)
        m.set16(e + 0x1E, 0x64)
        self.init_island_object(a, 0x16)
        self.reset_island_schedule()
        m.set16(a + 6, self.rand())
        m.set16(a + 4, 0xC000)
        m.set16(a + 0x26, m.u16(0x448))
        m.set16(a + 0x38, m.u16(0x44E))
        m.set16(a + 0x28, 0x3E8)
        x, y, z = m.u16(e + 0x0A), m.u16(e + 0x0C) + 0x15E, m.u16(e + 0x0E)
        for o, v in ((0x0A, x), (0x0C, y), (0x0E, z), (0x1E, x), (0x20, y + 0x320), (0x22, z)):
            m.set16(a + o, v)
        m.set32(a + 0x2A, e)

    def antiship_site(self, e: int) -> None:
        """$19F8A: fire an anti-ship missile (type 34) at the opposing carrier."""
        m = self.mem
        if not self._word_timer(e + 0x1E):
            return
        if m.u8(0xA45):
            return
        m.set16(e + 0x1E, (self.rand() & 0x7F) + 0x80)
        c = self.obj(19) if m.u8(0xA44) else CARRIER
        if not m.u8(c + 1) & 1 or m.u32(c + 0x3C) != m.u32(0xA28):
            return
        dx = s32(m.u32(c + 0x0A) - m.u32(0xA36))
        dz = s32(m.u32(c + 0x12) - m.u32(0xA3E))
        d = max(abs(dx), abs(dz))
        if d > m.u16(0x454):
            return
        heading = self.atan2(dx, dz)
        m.set16(0xA80, heading)
        a = self.find_free_object()
        if a is None:
            return
        self.init_world_object(a, 0x22)
        self.reset_pair_schedules()
        m.set16(a + 6, heading)
        m.set16(a + 4, 0xC000)
        m.set32(a + 0x0A, m.u32(0xA36))
        m.set32(a + 0x12, m.u32(0xA3E))
        m.set32(a + 0x0E, m.u32(0xA3A) + 0x64)
        m.set16(a + 0x1E, 0x190)
        m.set8(a + 0x1A, 0)
        m.set8(a + 0x1C, m.u8(0xA44))

    ISLAND_BEHAVIOURS = {}       # filled in after the class (some handlers are defined below)

    # ---- AI objects -------------------------------------------------------------------
    def ai_objects_step(self) -> None:
        """$1A56A: objects 20 and up with a behaviour (bits 0 and 8) run their handler."""
        m = self.mem
        n = s16(m.u16(0xA48) - 20)
        if n <= 0:
            return
        m.set16(0x6F0, 20)
        for k in range(n):
            a = self.obj(20 + k)
            if m.u16(a) & 0x101 == 0x101:
                h = m.u32(AI_HANDLERS + 4 * m.u16(a + 2))
                if h not in (0x1A69E, 0x19860):
                    fn = self.AI_BEHAVIOURS.get(h)
                    if fn is None:
                        raise NotImplementedError(f"AI object handler ${h:05X} (type {m.u16(a + 2)})")
                    fn(self, a)
            m.set16(0x6F0, 21 + k)

    # ---- object passes ----------------------------------------------------------------
    def object_passes(self) -> None:
        """$1B746: island attach, island-object and object-pair collisions, frame counter."""
        self.island_attach_step()
        self.collide_island_objects()
        self.collide_objects()
        self.mem.set16(FRAME, (self.mem.u16(FRAME) + 1) & 0x7FFF)

    def island_attach_step(self) -> None:
        """$1B762: every 24-31 passes each object re-checks which island it's near."""
        m = self.mem
        for k in range(2, 64):
            o = self.obj(k)
            if not m.u8(o + 1) & 1:
                continue
            t = m.u16(o + 0x3A)
            if t:
                m.set16(o + 0x3A, t - 1)
                continue
            m.set16(o + 0x3A, (self.rand() & 7) + 0x18)
            buf = m.u32(o + 0x3C)
            if buf:
                dx = abs(s32(m.u32(o + 0x0A) - m.u32(buf + 2)))
                dz = abs(s32(m.u32(o + 0x12) - m.u32(buf + 0x0A)))
                if not (dx <= 0x1FBD0 and dz < 0x1FBD0):
                    self.release_island_buffer(o)
                    m.set8(o, m.u8(o) & ~2)
                    m.set32(o + 0x3C, 0)
                continue
            for i in range(64):
                isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i
                if abs(s32(m.u32(o + 0x0A) - m.u32(isl + 2))) > 0x1FBD0:
                    continue
                if not abs(s32(m.u32(o + 0x12) - m.u32(isl + 6))) < 0x1FBD0:
                    continue
                if m.u8(isl):
                    b = ISLAND_BUFFERS[1 if m.u8(isl + 1) else 0]
                    if m.u8(b + 1) != i:
                        raise NotImplementedError("trap #5: island buffer mismatch")
                    m.set32(o + 0x3C, b)
                elif o == CARRIER or m.u16(o + 2) in (5, 6):
                    if not m.u8(ISLAND_BUFFERS[0]) & 1:
                        n = 0
                    elif not m.u8(ISLAND_BUFFERS[1]) & 1:
                        n = 1
                    else:
                        raise NotImplementedError("trap #6: no free island buffer")
                    m.set8(isl + 1, n)
                    m.set32(o + 0x3C, ISLAND_BUFFERS[n])
                    self.load_island_buffer(ISLAND_BUFFERS[n], i)
                break

    def release_island_buffer(self, o: int) -> None:
        """$182F6: when no other player unit uses the object's buffer, detach everything from
        it and free it."""
        m = self.mem
        buf = m.u32(o + 0x3C)
        if not buf:
            return
        for k in range(2, 11):
            u = self.obj(k)
            if m.u8(u + 1) & 1 and u != o and m.u32(u + 0x3C) == buf:
                return
        for k in range(2, 64):
            u = self.obj(k)
            if m.u32(u + 0x3C) == buf:
                m.set32(u + 0x3C, 0)
        m.set8(buf, 0)
        isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * m.u8(buf + 1)
        m.set8(isl, 0)
        m.set16(isl + 0x10, 0)

    def load_island_buffer(self, buf: int, i: int) -> None:
        """$17FB4: build the 3D buffer of island i from its structure slots."""
        m = self.mem
        isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i
        m.set8(isl, 0xFF)
        m.set8(buf, 1)
        m.set8(buf + 1, i)
        m.set32(buf + 2, m.u32(isl + 2))
        m.set32(buf + 6, 0)
        m.set32(buf + 0x0A, m.u32(isl + 6))
        m.set16(buf + 0x0E, 0x40)
        m.set16(0x74E, 0)
        m.buf[buf + 0x12:buf + 0x12 + 0x1000] = bytes(0x1000)
        e = buf + 0x12
        m.set16(e, 3)
        m.set16(e + 2, 8)
        m.set16(e + 0x0C, 0x28)
        m.set16(e + 0x1E, m.u16(isl + 0x0A))
        m.set16(e + 0x20, m.u16(isl + 0x0C))
        for j in range(32):
            s = isl + stsim.SLOT_BASE + stsim.SLOT_SIZE * j
            t = m.u16(s)
            h = m.u32(SLOT_ENTRY + 8 * t)
            y = w16(m.u32(SLOT_ENTRY + 8 * t + 4) + 0x50)
            if h & 0x80000000:
                self._spawn_entry(e, s, w16(-h), y)
            elif h in (0x18236, 0x18248):
                self._spawn_entry(e, s, 0x24 if h == 0x18236 else 0x2F, y)
                m.set16(0x74E, m.u16(0x74E) + 1)
            elif h:
                raise NotImplementedError(f"slot entry handler ${h:05X}")
            e += 0x40
        m.set16(buf + 0x10, m.u16(0x74E))

    def _spawn_entry(self, e: int, s: int, t: int, y: int) -> None:
        """$1825A + the slot value: an island entry for a structure slot."""
        m = self.mem
        self.init_island_object(e, t)
        m.set16(e + 0x0A, m.u16(s + 8))
        m.set16(e + 0x0C, y)
        m.set16(e + 0x0E, m.u16(s + 0x0A))
        m.set16(e + 6, m.u16(s + 2))
        m.set16(e + 0x38, m.u16(s + 4))

    @staticmethod
    def absw(v: int) -> int:
        """neg.w if negative: |v| as a signed word ($8000 stays negative)."""
        v = s16(v)
        return s16(-v) if v < 0 else v

    @staticmethod
    def absl(v: int) -> int:
        v = s32(v)
        return s32(-v) if v < 0 else v

    def collide_island_objects(self) -> None:
        """$1B90E: objects (2-63, flags $601) against the entries of their island buffer.
        Pairs are only tested on their scheduled pass."""
        m = self.mem
        f = m.u16(FRAME)
        nxt = (f + 1) & 0x7FFF
        for k in range(2, 64):
            o = self.obj(k)
            if m.u16(o) & 0x601 != 0x601:
                continue
            buf = m.u32(o + 0x3C)
            if not buf:
                continue
            m.set32(0xA9C, buf)
            m.set32(0xAA0, stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * m.u8(buf + 1))
            dx = s32(m.u32(o + 0x0A) - m.u32(buf + 2))
            if not -0x8000 <= dx < 0x8000:
                m.set8(o, m.u8(o) & ~2)
                continue
            for j in range(3):
                m.set32(0xA4A + 4 * j, m.u32(buf + 2 + 4 * j))
            dz = s32(m.u32(o + 0x12) - m.u32(buf + 0x0A))
            dy = s32(m.u32(o + 0x0E) - m.u32(buf + 6))
            if not (-0x8000 <= dz < 0x8000 and -0x8000 <= dy < 0x8000):
                m.set8(o, m.u8(o) & ~2)
                continue
            m.set16(0xAAA, dx)
            m.set16(0xAAC, dy)
            m.set16(0xAAE, dz)
            for j in range(64):
                sa = o + 0x40 + 2 * j
                if m.u16(sa) != f:
                    continue
                m.set16(0xAA4, j)
                e = buf + 0x12 + 0x40 * j
                if m.u16(e) & 0x401 != 0x401:
                    m.set8(sa, 0xFF)
                    continue
                ex, ey, ez = s16(dx - m.u16(e + 0x0A)), s16(dy - m.u16(e + 0x0C)), s16(dz - m.u16(e + 0x0E))
                m.set16(0xAB0, ex)
                m.set16(0xAB4, ey)
                m.set16(0xAB2, ez)
                ax, ay, az = self.absw(ex), self.absw(ey), self.absw(ez)
                d = ax
                if ay >= d:
                    d = ay
                if az >= d:
                    d = az
                d = w16(d)
                near = False
                for side in (o, e):
                    if m.u8(side) & 8:
                        if d < 1000:
                            near = True
                            break
                        d = w16(d - 1000)
                if not near and s16(d) > 1500:
                    speed = w16(m.u16(o + 0x36) + m.u16(e + 0x36))
                    if speed == 0:
                        m.set8(sa, 0xFF)
                        continue
                    q = (s16(w16(d - 750)) & 0xFFFFFFFF) // speed
                    q = w16(q) if q <= 0xFFFF else w16(d - 750)
                    m.set16(sa, ((q or 1) + f) & 0x7FFF)
                    continue
                m.set16(sa, nxt)
                if ay > s16(m.u16(e + 0x32) + m.u16(o + 0x32)):
                    continue
                re = m.s16(e + 0x30)
                if re < 0:
                    t = m.u16(e + 2)
                    if t == 7:
                        re = s16((s16(700 - ey) >> 1) + 300)
                    elif t in (0x25, 0x30):
                        re = s16(750 - ey)
                    else:
                        continue
                if m.s16(o + 0x30) < 0:
                    angle = w16(-m.u16(o + 6))
                    hx, hz = self.rotate_yaw(w16(-ex), w16(-ez), angle)
                    m.set16(0xAB2, hz)
                    # Quirk: the rotation leaves cos(angle) in d3, so the along-hull limit is
                    # cos + 1000 instead of radius + 1000 (up to 17384 for a carrier).
                    if self.absw(hx) > s16(re + 400) or self.absw(hz) > s16(self.sin_cos(angle)[1] + 1000):
                        continue
                else:
                    mx = ax if ax >= az else az
                    if mx > s16(m.u16(o + 0x30) + re):
                        continue
                self.collision_response(o, e)
                self.collision_response(e, o)

    def collide_objects(self) -> None:
        """$1BB40: world-object pairs (j < i), each tested on its scheduled pass."""
        m = self.mem
        f = m.u16(FRAME)
        nxt = (f + 1) & 0x7FFF
        for i in range(1, 64):
            o = self.obj(i)
            if m.u16(o) & 0x401 != 0x401:
                continue
            for j in range(i):
                sa = o + 0xC0 + 2 * j
                if m.u16(sa) != f:
                    continue
                p = self.obj(j)
                if m.u16(p) & 0x401 != 0x401:
                    m.set8(sa, 0xFF)
                    continue
                dx = s32(m.u32(o + 0x0A) - m.u32(p + 0x0A))
                dy = s32(m.u32(o + 0x0E) - m.u32(p + 0x0E))
                dz = s32(m.u32(o + 0x12) - m.u32(p + 0x12))
                m.set16(0xAB0, dx)
                m.set16(0xAB2, dz)
                # The original negates x and z on the sign of their LOW WORD (the flags come
                # from the move.w to $AB0/$AB2), so e.g. -40000 ($FFFF63C0) stays negative.
                ax = s32(-dx) if dx & 0x8000 else dx
                az = s32(-dz) if dz & 0x8000 else dz
                ay = self.absl(dy)
                d = ax
                if ay >= d:
                    d = ay
                if az >= d:
                    d = az
                d &= 0xFFFFFFFF
                near = False
                for side in (o, p):
                    if m.u8(side) & 8:
                        if d < 1000:
                            near = True
                            break
                        d = (d - 1000) & 0xFFFFFFFF
                if not near and s32(d) > 1500:
                    speed = s16(m.u16(o + 0x36) + m.u16(p + 0x36))
                    if speed == 0:
                        m.set8(sa, 0xFF)
                        continue
                    qq = int(s32(d - 900) / speed)
                    q = 5000 if not -0x8000 <= qq < 0x8000 else (w16(qq) or 1)
                    m.set16(sa, (q + f) & 0x7FFF)
                    continue
                m.set16(sa, nxt)
                if s16(ay) > s16(m.u16(p + 0x32) + m.u16(o + 0x32)):
                    continue
                rp, ro = m.s16(p + 0x30), m.s16(o + 0x30)
                if ro < 0:
                    if m.u8(p + 1) & 4:
                        continue
                    self.set_matrix(w16(-m.u16(o + 6)))
                    hx, hz = self.matrix_rotate(m.u16(0xAB0), m.u16(0xAB2))
                    r = rp
                elif rp < 0:
                    if m.u8(o + 1) & 4:
                        continue
                    hx, hz = self.rotate_yaw(m.u16(0xAB0), m.u16(0xAB2), w16(-m.u16(p + 6)))
                    m.set16(0xAB2, hz)
                    r = ro
                else:
                    mx = s16(ax) if s16(ax) >= s16(az) else s16(az)
                    if mx > s16(ro + rp):
                        continue
                    r = None
                if r is not None and (self.absw(hx) > s16(r + 400) or self.absw(hz) > s16(r + 1000)):
                    continue
                self.collision_response(o, p)
                self.collision_response(p, o)

    # ---- per second, per 10 seconds -----------------------------------------------------
    def island_range_check(self) -> None:
        """$1B8AE: set bit 9 once an attached object's offset fits in 16 bits, and test it
        against every island entry next pass."""
        m = self.mem
        for k in range(2, 64):
            o = self.obj(k)
            f = m.u16(o)
            if f & 0x601 != 0x401:
                continue
            buf = m.u32(o + 0x3C)
            if not buf:
                continue
            dx = s32(m.u32(o + 0x0A) - m.u32(buf + 2))
            dz = s32(m.u32(o + 0x12) - m.u32(buf + 0x0A))
            if -0x8000 <= dx < 0x8000 and -0x8000 <= dz < 0x8000:
                m.set8(o, m.u8(o) | 2)
                fr = m.u16(FRAME)
                for j in range(64):
                    m.set16(o + 0x40 + 2 * j, fr)

    def manta_hangar_step(self) -> None:
        self._hangar(0x496, 0x16, "Manta")

    def walrus_hangar_step(self) -> None:
        self._hangar(0x570, 0x0E, "Walrus")

    def _hangar(self, records: int, timer: int, kind: str) -> None:
        """$1761C / $176AC: preparation countdown, then shared repair of bays under repair."""
        m = self.mem
        for k in range(4):
            r = records + 0x34 * k
            if m.u8(r + 1) == 2:
                m.set16(r + timer, m.u16(r + timer) - 1)
                if m.u16(r + timer) == 0:
                    m.set8(r + 1, 0)
        n = sum(1 for k in range(4) if m.u8(records + 0x34 * k + 1) == 1)
        if n:
            step = 400 // n
            for k in range(4):
                r = records + 0x34 * k
                if m.u8(r + 1) != 1:
                    continue
                h = w16(m.u16(r + 0x14) + step)
                m.set16(r + 0x14, h)
                if h >= 0xFA0:
                    m.set16(r + 0x14, 0xFA0)
                    m.set8(r + 1, 2)
                    self.preparation_time(r, kind)

    def preparation_time(self, r: int, kind: str) -> None:
        """$11856 (Manta) / $11F6A (Walrus): flight preparation in 10 s units: damage left
        to repair, plus one per loadout slot in use (Manta slot 3 and Walrus slot 2 cost
        more)."""
        m = self.mem
        d = stsim.divu((s16(0xFA0 - m.u16(r + 0x14)) << 4) & 0xFFFFFFFF, 0xFA0) & 0xFFFF
        if kind == "Manta":
            d += (1 if m.u16(r + 0x0C) else 0) + (1 if m.u16(r + 0x0E) else 0) + (2 if m.u16(r + 0x10) else 0)
            m.set16(r + 0x16, d + 1)
        else:
            d += (1 if m.u16(r + 0x26) else 0) + (3 if m.u16(r + 0x28) else 0)
            m.set16(r + 0x0E, d + 1)

    def carrier_systems_status(self) -> None:
        """$17578: each carrier system's status word from (current, max)."""
        m = self.mem
        for k in range(10):
            a = stsim.SYSTEMS + 8 * k
            cur, mx = m.u16(a), m.u16(a + 2)
            if k in (0, 4):
                if cur == mx:
                    m.set16(a + 6, 0x100)
                else:
                    half = mx >> 1
                    v = (cur << 8) // half if half else None
                    if v is None:
                        raise NotImplementedError("divide by zero")
                    v = w16(v) if v <= 0xFFFF else w16(cur << 8)
                    v = s16(v - 0x100)
                    m.set16(a + 6, 0 if v < 0 else v)
            elif k in (8, 9):
                v = (cur << 8) // mx if mx else None
                if v is None:
                    raise NotImplementedError("divide by zero")
                m.set16(a + 6, w16(v) if v <= 0xFFFF else w16(cur << 8))
            else:
                m.set8(a + 6, 0xFF if cur > (mx >> 1) else 0)

    # ---- island step: 3D buffer mirroring ---------------------------------------------
    def _slot_index(self, s) -> int:
        isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * self.island_index
        return (s.a - isl - stsim.SLOT_BASE) // stsim.SLOT_SIZE

    def slot_mirror(self, s) -> None:
        """$18276: copy a changed slot to its entry in the loaded island's buffer."""
        m = self.mem
        isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * self.island_index
        if not m.u8(isl):
            return
        e = m.u32(stsim.G_BUFFER_PTR) + 0x12 + 0x40 * self._slot_index(s)
        if s.type:
            m.set16(e + 2, s.type)
            m.set16(e + 0x38, s.value)
            m.set16(e + 0x0A, s.x)
            m.set16(e + 0x0E, s.z)
        else:
            m.set16(e, 0)

    def slot_create(self, j: int) -> None:
        """$182A4: a new construction's entry in the loaded island's buffer."""
        m = self.mem
        isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * self.island_index
        if not m.u8(isl):
            return
        m.set16(NEW_INDEX, j)
        e = m.u32(stsim.G_BUFFER_PTR) + 0x12 + 0x40 * j
        s = isl + stsim.SLOT_BASE + stsim.SLOT_SIZE * j
        t = m.u16(s)
        self.init_island_object(e, t)
        self.reset_island_schedule()
        m.set16(e + 0x38, m.u16(s + 4))
        m.set16(e + 0x0A, m.u16(s + 8))
        m.set16(e + 0x0E, m.u16(s + 0x0A))
        m.set16(e + 0x0C, w16(m.u32(SLOT_ENTRY + 8 * t + 4) + 0x50))

    def tree_cut(self, isl, k: int) -> None:
        """$18B50 (find_free_island_spot, slot k already cleared): on a loaded island the
        tree's buffer entry ([$8CC] + $12 + $40k) throws its debris ($1C6D6, origin = the
        buffer's, copied to $A4A) and is removed."""
        m = self.mem
        if not m.u8(isl.a):
            return
        buf = m.u32(stsim.G_BUFFER_PTR)
        for j in range(3):
            m.set32(0xA4A + 4 * j, m.u32(buf + 2 + 4 * j))
        e = buf + 0x12 + 0x40 * k
        self.spawn_debris_entry(e)
        m.set16(e, 0)


    # ---- shared helpers for island AI ---------------------------------------------------
    def velocity_from_angles(self, a: int, speed: int):
        """$1A164: (dx, dy, dz) for a speed along the object's pitch and heading."""
        m = self.mem
        y, h = self.rotate_pitch(0, speed, m.u16(a + 4))
        x, z = self.rotate_yaw(0, h, m.u16(a + 6))
        return x, y, z

    def offset_aim(self, e: int, dx: int, dy: int, dz: int):
        """$1A062: aim from island entry e at a local point (longs); pitch/heading to
        +$3A/+$3C. Returns (Chebyshev distance as a word, heading)."""
        m = self.mem
        d = [s32(v - m.s16(e + 0x0A + 2 * k)) for k, v in enumerate((dx, dy, dz))]
        pitch, heading = self.aim_angles(*d)
        m.set16(e + 0x3A, pitch)
        m.set16(e + 0x3C, heading)
        a = [self.absw(v) for v in d]
        dist = a[0]
        if a[1] >= dist:
            dist = a[1]
        if a[2] >= dist:
            dist = a[2]
        return dist, heading

    def create_sam_ahead(self, e: int, a: int) -> None:
        """$19A96: a SAM (type 28) 100 ahead of the launcher, same angles."""
        m = self.mem
        self.init_island_object(a, 0x1C)
        self.reset_island_schedule()
        m.set16(a + 0x0C, m.u16(e + 0x0C))
        vx, vz = self.yaw_vector(e, 0x64)
        m.set16(a + 0x0A, m.u16(e + 0x0A) + vx)
        m.set16(a + 0x0E, m.u16(e + 0x0E) + vz)
        for o in (4, 6, 8):
            m.set16(a + o, m.u16(e + o))

    def _move_local(self, e: int, speed: int) -> None:
        dx, dy, dz = self.velocity_from_angles(e, speed)
        m = self.mem
        m.set16(e + 0x0A, m.u16(e + 0x0A) + dx)
        m.set16(e + 0x0C, m.u16(e + 0x0C) + dy)
        m.set16(e + 0x0E, m.u16(e + 0x0E) + dz)

    # ---- island behaviours (continued) ----------------------------------------------------
    def dish_spin(self, e: int) -> None:
        """$19862: type 14 spins."""
        if not self.mem.u8(0xA45):
            self.mem.set16(e + 6, self.mem.u16(e + 6) + 1000)

    def dish_track(self, e: int) -> None:
        """$19872: type 15 faces Manta 0 (object 2)."""
        m = self.mem
        if m.u8(0xA45):
            return
        o = self.obj(2)
        m.set16(e + 6, self.atan2(s32(m.u32(o + 0x0A) - m.u32(0xA36)), s32(m.u32(o + 0x12) - m.u32(0xA3E))))

    def volcano(self, e: int) -> None:
        """$1989C: dormant (2) / erupting (1) cycles; erupting throws rocks (type 9)."""
        m = self.mem
        state = m.u8(e + 0x1A)
        if state == 1:
            if self._word_timer(e + 0x1E):
                return self._volcano_dormant(e)
            if self._dec_byte(e + 0x1B):
                return
            a = self.find_free_island_object()
            if a is not None:
                self.init_island_object(a, 9)
                self.reset_island_schedule()
                for o in (0x0A, 0x0C, 0x0E):
                    m.set16(a + o, m.u16(e + o))
                m.set16(a + 0x0C, m.u16(a + 0x0C) + 600)
                for o in (4, 6, 8):
                    m.set16(a + o, self.rand())
                angle = self.rand()
                vx, vz = self.rotate_yaw((self.rand() & 0xF) + 0x1E, 0, angle)
                m.set16(a + 0x20, vx)
                m.set16(a + 0x22, vz)
                m.set16(a + 0x1E, (self.rand() & 0x7FF) + 6000)
            m.set8(e + 0x1B, (self.rand() & 0xF) + 0x10)
        elif state == 0:
            self._volcano_dormant(e)
        else:
            if self._word_timer(e + 0x1E):
                m.set8(e + 0x1A, 1)
                m.set16(e + 0x1E, (self.rand() & 0x7FF) + 0x800)

    def _volcano_dormant(self, e: int) -> None:
        m = self.mem
        m.set8(e + 0x1A, 2)
        m.set16(e + 0x1E, (self.rand() & 0x7FF) + 0x800)
        m.set8(e + 0x1B, (self.rand() & 0xF) + 0x10)

    def volcano_rock(self, e: int) -> None:
        """$1996C: tumbling ballistic rock; gone below local height 100."""
        m = self.mem
        for o, d in ((4, 0x78A), (6, 0x3B1), (8, 0xFDEF)):
            m.set16(e + o, m.u16(e + o) + d)
        vy = s16(m.u16(e + 0x1E) - 0x10A)
        m.set16(e + 0x1E, vy)
        m.set16(e + 0x0C, m.u16(e + 0x0C) + (vy >> 6))
        if m.s16(e + 0x0C) < 100:
            m.set8(e + 1, m.u8(e + 1) & 0xFE)
            return
        m.set16(e + 0x0A, m.u16(e + 0x0A) + m.u16(e + 0x20))
        m.set16(e + 0x0E, m.u16(e + 0x0E) + m.u16(e + 0x22))

    def sam_launcher(self, e: int) -> None:
        """$19AC4: vertical SAM launch at Mantas (or enemy fighters) within 7000."""
        m = self.mem
        if m.u8(0xA45):
            return
        if m.u8(e + 0x1B) == 0:
            m.set8(e + 0x1B, ((self.rand() & 0x1F) + 0x40) & 0xFF)
            t, d = self.nearest_hostile(mantas_only=True)
            if d >= 0 and d <= 7000 and 250 <= s32(m.u32(t + 0x0E)) <= 2300:
                a = self.find_free_island_object()
                if a is not None:
                    self.init_island_object(a, 0x1C)
                    self.reset_island_schedule()
                    m.set32(a + 0x1E, t)
                    m.set16(a + 0x0A, m.u16(e + 0x0A))
                    m.set16(a + 0x0C, 0xB4)
                    m.set16(a + 0x0E, m.u16(e + 0x0E))
                    m.set16(a + 4, 0xC000)
                    m.set8(a + 0x1B, 0x3C)
                    m.set16(a + 0x22, 0x1E)
        m.set8(e + 0x1B, m.u8(e + 0x1B) - 1)

    def sam(self, e: int) -> None:
        """$19BC6: SAM: life +$1B; re-aims every 4 passes; turns at [$452], accelerates by 5
        up to [$450]."""
        m = self.mem
        life = (m.u8(e + 0x1B) - 1) & 0xFF
        m.set8(e + 0x1B, life)
        if life == 0:
            m.set16(e, 0)
        if m.u8(e + 0x1C) == 0:
            m.set8(e + 0x1C, 4)
            loc = self.target_local(m.u32(e + 0x1E))
            if loc is not None:
                pitch, heading = self.aim_angles(*(loc[k] - m.s16(e + 0x0A + 2 * k) for k in range(3)))
                m.set16(e + 0x3A, pitch)
                m.set16(e + 0x3C, heading)
        m.set16(e + 4, self.turn_towards(m.u16(e + 0x3A), m.u16(e + 4), m.u16(0x452)))
        m.set16(e + 6, self.turn_towards(m.u16(e + 0x3C), m.u16(e + 6), m.u16(0x452)))
        v = s16(m.u16(e + 0x22) + 5)
        if v > m.s16(0x450):
            v = m.s16(0x450)
        m.set16(e + 0x22, v)
        self._move_local(e, v)
        m.set8(e + 0x1C, m.u8(e + 0x1C) - 1)

    def island_drone(self, e: int) -> None:
        """$19C46: island defence drone. +$1A state (0 init, 1 fly to waypoint, 2 search,
        3 attack, 4+ land), +$1D state after the waypoint, +$28 fuel, +$26 missiles,
        +$38 health, +$2A launch pad."""
        m = self.mem
        st = m.u8(e + 0x1A)
        if st == 0:
            m.set8(e + 0x1A, 1)
            m.set8(e + 0x1B, 0xFF)
            m.set8(e + 0x1D, 2)
            return
        if st == 1:
            return self._drone_fly(e)
        if st == 2:
            if not m.u8(0xA45):
                t, d = self.nearest_hostile()
                if d >= 0 and d <= 25000:
                    m.set32(e + 0x1E, t)
                    m.set8(e + 0x1A, 3)
                    return self._drone_steer(e, False)
            m.set8(e + 0x1B, 0x14)
            return self._drone_wander(e)
        if st == 3:
            t = m.u32(e + 0x1E)
            if not m.u16(t) & 1:
                m.set8(e + 0x1A, 2)
                return self._drone_steer(e, False)
            loc = self.target_local(t)
            if loc is not None:
                dist, heading = self.offset_aim(e, *loc)
                if m.u16(e + 0x26):
                    if s16(dist) > 5000:
                        return self._drone_steer(e, False)
                    if self.absw(heading - m.u16(e + 6)) <= 0x100:
                        a = self.find_free_island_object()
                        if a is not None:
                            self.create_sam_ahead(e, a)
                            m.set8(a + 0x1B, 0x64)
                            m.set32(a + 0x1E, m.u32(e + 0x1E))
                            m.set16(a + 0x22, 0xC8)
                            m.set16(e + 0x26, m.u16(e + 0x26) - 1)
            m.set8(e + 0x1B, 0x64)
            return self._drone_wander(e)
        pad = m.u32(e + 0x2A)
        if not self._pad_ok(pad):
            return self._drone_lost_pad(e)
        m.set16(e + 4, self.turn_towards(0x4000, m.u16(e + 4), 0x1000))
        if m.s16(e + 0x0C) > 0x2DA:
            return self._island_drone_move(e)
        m.set8(e + 1, m.u8(e + 1) & 0xFE)
        isl = m.u32(0xA2C)
        m.set16(isl + 0x10, m.u16(isl + 0x10) - 1)

    def _pad_ok(self, pad: int) -> bool:
        m = self.mem
        return not pad & 0x80000000 and m.u8(pad + 1) & 1 and m.u16(pad + 2) == 0x25

    def _drone_fly(self, e: int) -> None:
        m = self.mem
        fuel = m.u16(e + 0x28)
        if fuel == 0 or fuel == 1:
            m.set16(e, 0)
            isl = m.u32(0xA2C)
            m.set16(isl + 0x10, m.u16(isl + 0x10) - 1)
            return
        fuel -= 1
        m.set16(e + 0x28, fuel)
        if m.u8(e + 0x1D) != 4 and not m.u8(e + 0x2A) & 0x80:
            if (m.u8(0xA45) or s16(fuel) <= 0xC8 or m.s16(e + 0x38) <= 0x1F4
                    or m.u16(e + 0x26) == 0):
                return self._drone_go_home(e)
        dist, _ = self.offset_aim(e, *(m.s16(e + 0x1E + 2 * k) for k in range(3)))
        left = (m.u8(e + 0x1B) - 1) & 0xFF
        m.set8(e + 0x1B, left)
        if left == 0 or s16(dist) <= 0xF0:
            m.set8(e + 0x1A, m.u8(e + 0x1D))
        m.set8(e + 0x1C, 0xFF if s16(dist) < 0x1F4 else 0)
        self._drone_steer(e, True)

    def _drone_go_home(self, e: int) -> None:
        """$19E64: head for the pad (and process the fly state again this pass)."""
        m = self.mem
        pad = m.u32(e + 0x2A)
        if not self._pad_ok(pad):
            return self._drone_lost_pad(e)
        x, y, z = m.u16(pad + 0x0A), m.u16(pad + 0x0C) + 0x320, m.u16(pad + 0x0E)
        m.set16(e + 0x1E, x)
        m.set16(e + 0x20, y)
        m.set16(e + 0x22, z)
        m.set8(e + 0x1A, 1)
        m.set8(e + 0x1B, 0xFF)
        m.set8(e + 0x1D, 4)
        self._drone_fly(e)

    def _drone_lost_pad(self, e: int) -> None:
        m = self.mem
        m.set8(e + 0x2A, 0xFF)
        m.set8(e + 0x1A, 2)
        self._drone_steer(e, False)

    def _drone_wander(self, e: int) -> None:
        """$19D10: a random waypoint near the drone, then fly there and search again."""
        m = self.mem
        def around(v, rng, sub, lo, hi):
            v = s16(v + ((self.rand() & rng) - sub))
            return hi if v > hi else lo if v < lo else v
        m.set16(e + 0x1E, around(m.u16(e + 0x0A), 0xFFF, 0x800, -24000, 24000))
        m.set16(e + 0x22, around(m.u16(e + 0x0E), 0xFFF, 0x800, -24000, 24000))
        m.set16(e + 0x20, around(m.u16(e + 0x0C), 0x7FF, 0x400, 200, 2500))
        m.set8(e + 0x1A, 1)
        m.set8(e + 0x1D, 2)
        self._drone_steer(e, False)

    def _drone_steer(self, e: int, keep_slow: bool) -> None:
        """$19EB2/$19EB6: turn towards +$3A/+$3C at [$44C], then move."""
        m = self.mem
        if not keep_slow:
            m.set8(e + 0x1C, 0)
        m.set16(e + 4, self.turn_towards(m.u16(e + 0x3A), m.u16(e + 4), m.u16(0x44C)))
        m.set16(e + 6, self.turn_towards(m.u16(e + 0x3C), m.u16(e + 6), m.u16(0x44C)))
        self._island_drone_move(e)

    def _island_drone_move(self, e: int) -> None:
        """$19EE2: speed [$44A], halved when close (+$1C)."""
        m = self.mem
        speed = m.u16(0x44A)
        if m.u8(e + 0x1C):
            speed >>= 1
        self._move_local(e, speed)

    # ---- AI objects -------------------------------------------------------------------
    def antiship_missile(self, a: int) -> None:
        """$1AABA: climbs to 800, then skims at 75 once clear of its island, homing on the
        player carrier or deck drones (or the enemy carrier when +$1C is set)."""
        m = self.mem
        if self._word_timer(a + 0x1E):
            return self.explode_world(a, 1)
        alt = 0x4B if m.u8(a + 0x1A) else 0x320
        want = w16((m.u16(a + 0x10) - alt) << 3)
        m.set16(a + 4, self.turn_towards(want, m.u16(a + 4), 0x400))
        if not m.u8(a + 0x1A):
            buf = m.u32(a + 0x3C)
            if not buf:
                return self._missile_fly(a)
            dx, dz = self.island_box_distance(a, buf)
            if dx < 0 and dz < 0:
                return self._missile_fly(a)
            m.set8(a + 0x1A, 1)
            m.set8(a + 0x1B, 1)
            if not m.u8(a + 0x1C):
                self.events.append(("message", "Missile heading for Carrier!", self._ping(True, True)))
                self._sfx(12)
            return self._missile_turn(a)
        left = (m.u8(a + 0x1B) - 1) & 0xFF
        m.set8(a + 0x1B, left)
        if left:
            return self._missile_turn(a)
        m.set8(a + 0x1B, 0x14)
        x0, z0 = m.u32(a + 0x0A), m.u32(a + 0x12)
        t = self.obj(19)
        if not m.u8(a + 0x1C):
            t = best = CARRIER
            best_d = 0xFFFFFFFF
            for k in range(10, 15):
                o = self.obj(k)
                if not m.u8(o + 1) & 1:
                    continue
                dx = self.absl(m.u32(o + 0x0A) - x0) & 0xFFFFFFFF
                dz = self.absl(m.u32(o + 0x12) - z0) & 0xFFFFFFFF
                if s16(dz) < s16(dx):
                    dz = (dz & 0xFFFF0000) | (dx & 0xFFFF)      # cmp.w / move.w quirk
                if dz <= best_d:
                    best_d, t = dz, o
        m.set16(a + 0x20, self.atan2(s32(m.u32(t + 0x0A) - x0), s32(m.u32(t + 0x12) - z0)))
        self._missile_turn(a)

    def _missile_turn(self, a: int) -> None:
        m = self.mem
        m.set16(a + 6, self.turn_towards(m.u16(a + 0x20), m.u16(a + 6), 0x400))
        self._missile_fly(a)

    def _missile_fly(self, a: int) -> None:
        m = self.mem
        dx, dy, dz = self.velocity_from_angles(a, 0xB4)
        for o, d in ((0x0A, dx), (0x0E, dy), (0x12, dz)):
            m.set32(a + o, m.u32(a + o) + d)

    def blast(self, a: int) -> None:
        """$1A9A6: the explosion volume lasts +$1B passes."""
        m = self.mem
        v = (m.u8(a + 0x1B) - 1) & 0xFF
        m.set8(a + 0x1B, v)
        if v == 0:
            m.set16(a, 0)

    def _fly_with_floor(self, a: int, speed: int) -> None:
        """Laser/homing-missile motion: x, z always; y too, but a nose-down object that
        would go below 0 dies (it still moves)."""
        m = self.mem
        dx, dy, dz = self.velocity_from_angles(a, speed)
        m.set32(a + 0x0A, m.u32(a + 0x0A) + dx)
        m.set32(a + 0x12, m.u32(a + 0x12) + dz)
        y = s32(m.u32(a + 0x0E) + dy)
        if y < 0 and m.s16(a + 4) > 0:
            m.set16(a, 0)
        m.set32(a + 0x0E, y)

    def _dec_byte(self, a: int) -> int:
        """subq.b #1 (no zero test first: 0 wraps to $FF)."""
        v = (self.mem.u8(a) - 1) & 0xFF
        self.mem.set8(a, v)
        return v

    def laser_bolt(self, a: int) -> None:
        """$1A6A0: straight line at 350 per pass for +$1B passes."""
        m = self.mem
        life = (m.u8(a + 0x1B) - 1) & 0xFF
        m.set8(a + 0x1B, life)
        if life == 0:
            m.set16(a, 0)
            return
        self._fly_with_floor(a, 0x15E)

    def homing_missile(self, a: int) -> None:
        """$1A6D0: 5 passes straight, then homes on +$1E (re-aimed every 5 passes; a decoy
        flare within 6000 steals it). Lost target: 100 more passes straight."""
        m = self.mem
        st = m.u8(a + 0x1A)
        if st == 0:
            m.set8(a + 0x1B, 0x64)
            m.set8(a + 0x1C, 5)
            m.set8(a + 0x1A, 1)
            return
        life = (m.u8(a + 0x1B) - 1) & 0xFF
        m.set8(a + 0x1B, life)
        if life == 0:
            m.set16(a, 0)
            return
        if st == 1:
            if self._dec_byte(a + 0x1C) == 0:
                m.set8(a + 0x1A, 2)
                m.set8(a + 0x1C, 1)
            return self._fly_with_floor(a, 0xFA)
        if self._dec_byte(a + 0x1C) == 0:
            m.set8(a + 0x1C, 5)
            for k in (17, 18):
                f = self.obj(k)
                if m.u8(f + 1) & 1 and self.cheb2d(a, f) <= 6000:
                    m.set32(a + 0x1E, f)
            t = m.u32(a + 0x1E)
            if t & 0x80000000 or not m.u16(t) & 1:
                m.set8(a + 0x1A, 1)
                m.set8(a + 0x1C, 0x64)
                return
            if m.u16(t) & 0x8000:
                p = [m.u32(t + 0x0A + 4 * k) for k in range(3)]
            else:
                buf = m.u32(a + 0x3C)
                if not buf:
                    m.set8(a + 0x1A, 1)
                    m.set8(a + 0x1C, 0x64)
                    return
                p = [m.s16(t + 0x0A + 2 * k) + m.u32(buf + 2 + 4 * k) for k in range(3)]
            d = [s32(p[k] - m.u32(a + 0x0A + 4 * k)) for k in range(3)]
            pitch, heading = self.aim_angles(*d)
            m.set16(a + 0x22, pitch)
            m.set16(a + 0x24, heading)
        m.set16(a + 4, self.turn_towards(m.u16(a + 0x22), m.u16(a + 4), 0x800))
        m.set16(a + 6, self.turn_towards(m.u16(a + 0x24), m.u16(a + 6), 0x800))
        self._fly_with_floor(a, 0xFA)

    def cheb2d(self, a: int, b: int) -> int:
        """$1A866: max(|dx|, |dz|) between two world objects."""
        m = self.mem
        dx = self.absl(m.u32(a + 0x0A) - m.u32(b + 0x0A))
        dz = self.absl(m.u32(a + 0x12) - m.u32(b + 0x12))
        return dx if dx >= dz else dz

    def guided_missile(self, a: int) -> None:
        """$1A886: steered by its Walrus through +$1E (yaw) and +$20 (pitch); explodes low
        or over an island."""
        m = self.mem
        if self._dec_byte(a + 0x1C) == 0:
            m.set16(a, 0)
            return
        m.set16(a + 6, m.u16(a + 6) + 2 * m.u16(a + 0x1E))
        m.set16(a + 4, m.u16(a + 4) - m.u16(a + 0x20))
        dx, dy, dz = self.velocity_from_angles(a, 0x96)
        m.set32(a + 0x0A, m.u32(a + 0x0A) + dx)
        m.set32(a + 0x12, m.u32(a + 0x12) + dz)
        y = s32(m.u32(a + 0x0E) + dy)
        m.set32(a + 0x0E, y)
        if y >= 0x96:
            return
        if y > 0x1E:
            buf = m.u32(a + 0x3C)
            if not buf:
                return
            bx, bz = self.island_box_distance(a, buf)
            if bx >= 0 or bz >= 0:
                return
        m.set16(a, 0)
        self._explosion(1, a)

    def cruise_missile(self, a: int) -> None:
        """$1A8F6: flies to the point (+$1E, +$22) at 1000, dropping to 140 within 3000;
        explodes (blast) within 200 or when its life runs out."""
        m = self.mem
        if self._dec_byte(a + 0x1B) == 0:
            return self.explode_blast(a)
        alt = 0x8C if m.u8(a + 0x1A) else 0x3E8
        want = w16((m.u16(a + 0x10) - alt) << 3)
        m.set16(a + 4, self.turn_towards(want, m.u16(a + 4), 0x400))
        dx = s32(m.u32(a + 0x1E) - m.u32(a + 0x0A))
        dz = s32(m.u32(a + 0x22) - m.u32(a + 0x12))
        d = self.absl(dx)
        if self.absl(dz) > d:
            d = self.absl(dz)
        heading = self.atan2(dx, dz)
        if d <= 0xBB8:
            if d <= 0xC8:
                return self.explode_blast(a)
            m.set8(a + 0x1A, 1)
        if self.absw(m.u16(a + 6) - heading) <= 0x3E80:
            m.set16(a + 6, self.turn_towards(heading, m.u16(a + 6), 0x800))
        vx, vy, vz = self.velocity_from_angles(a, 0xC8)
        for o, v in ((0x0A, vx), (0x0E, vy), (0x12, vz)):
            m.set32(a + o, m.u32(a + o) + v)

    def explode_blast(self, a: int) -> None:
        """$1C5EC: the object itself explodes into a blast volume."""
        self.hit_blast(a, a, 0)

    def bomb(self, a: int) -> None:
        """$1A9B0: falls, tumbling; bounces off an island top (3/4 of the speed back, with
        debris) until the bounce is under 30, then explodes; over the sea it explodes."""
        m = self.mem
        if s32(m.u32(a + 0x0E)) < 0x64 and m.u32(a + 0x3C):
            bx, bz = self.island_box_distance(a, m.u32(a + 0x3C))
            if bx < 0 and bz < 0:
                m.set32(a + 0x0E, 0x64)
                vy = w16(-m.u16(a + 0x22))
                m.set16(a + 0x1E, 0x1000)
                if s16(vy) < 0x1E:
                    return self.explode_blast(a)
                m.set16(a + 0x22, vy - (s16(vy) >> 2))
                self.spawn_debris_world(a)
                self._explosion(1, a)
            elif s32(m.u32(a + 0x0E)) < 0x14:
                m.set32(a + 0x0E, 0)
                return self.explode_blast(a)
        m.set16(a + 4, m.u16(a + 4) + m.u16(a + 0x1E))
        vx, vy, vz = m.s16(a + 0x20), s16(m.u16(a + 0x22) - 10), m.s16(a + 0x24)
        if vy < -0x8C:
            vy = -0x8C
        m.set16(a + 0x22, vy)
        for o, v in ((0x0A, vx), (0x0E, vy), (0x12, vz)):
            m.set32(a + o, m.u32(a + o) + v)

    def virus_bomb(self, a: int) -> None:
        """$1AA52: tumbles along heading +$20 at 100 per pass; gone (with debris) when its
        timer runs out or it's low over an island."""
        m = self.mem
        gone = self._word_timer(a + 0x1E)
        if not gone and s32(m.u32(a + 0x0E)) < 0x50 and m.u32(a + 0x3C):
            bx, bz = self.island_box_distance(a, m.u32(a + 0x3C))
            gone = bx < 0 and bz < 0
        if gone:
            m.set16(a, 0)
            self.spawn_debris_world(a)
            return
        m.set16(a + 6, m.u16(a + 6) + 0x1F4)
        m.set16(a + 4, m.u16(a + 4) - 0x300)
        m.set16(a + 8, m.u16(a + 8) + 0x172)
        vx, vz = self.rotate_yaw(0, 0x64, m.u16(a + 0x20))
        m.set32(a + 0x0A, m.u32(a + 0x0A) + vx)
        m.set32(a + 0x12, m.u32(a + 0x12) + vz)

    # enemy fighter ($1AC00)
    def enemy_fighter(self, a: int) -> None:
        """$1AC00: +$1A state (1 fly to waypoint +$22/+$26/+$2A, 2 search, 3 attack target
        +$22, 4 return, 5 land), +$1D next state, +$16 fuel, +$18 missiles, +$38 health."""
        m = self.mem
        st = m.u8(a + 0x1A)
        if st == 0:
            m.set8(a + 0x1A, 1)
            m.set8(a + 0x1B, 0xFF)
            m.set8(a + 0x1D, 2)
            return
        if st == 1:
            return self._fighter_cruise(a)
        if st == 2:
            t = self._fighter_find_unit(a)
            if t is None:
                t = self._fighter_find_structure(a)
            if t is None:
                return self._fighter_wander(a)
            m.set32(a + 0x22, t)
            m.set8(a + 0x1A, 3)
            return self._fighter_steer(a)
        if st == 3:
            return self._fighter_attack(a)
        if st == 4:
            return self._fighter_home(a)
        return self._fighter_land(a)

    def _fighter_cruise(self, a: int) -> None:
        m = self.mem
        if m.s8(a + 0x1D) < 4:
            fuel = m.s16(a + 0x16)
            if m.u8(0x11D0) or fuel < 0xC8:
                return self._fighter_home(a)
            fuel -= 1
            m.set16(a + 0x16, fuel)
            if fuel == 0:
                m.set16(a, 0)
                m.set16(0x11CE, m.u16(0x11CE) - 1)
                return
            if m.s16(a + 0x38) <= 0x3E8 or m.u16(a + 0x18) == 0:
                return self._fighter_home(a)
        d = self._fighter_aim(a, *(m.u32(a + 0x22 + 4 * k) for k in range(3)))
        left = (m.u8(a + 0x1B) - 1) & 0xFF
        m.set8(a + 0x1B, left)
        if left == 0 or s32(d) <= 0x12C:
            m.set8(a + 0x1A, m.u8(a + 0x1D))
        m.set8(a + 0x1C, 0xFF if s32(d) < 0x258 else 0)
        self._fighter_steer(a)

    def _fighter_aim(self, a: int, x: int, y: int, z: int) -> int:
        """$1AE96: aim at a point (pitch +$1E, heading +$20); returns the 3D Chebyshev
        distance. Offsets are quartered until they fit in words ($1A7E4)."""
        m = self.mem
        d = [s32(v - m.u32(a + 0x0A + 4 * k)) for k, v in enumerate((x, y, z))]
        ad = [self.absl(v) for v in d]
        dist = ad[0]
        if ad[1] > dist:
            dist = ad[1]
        if ad[2] > dist:
            dist = ad[2]
        while not all(-0x8000 <= v < 0x8000 for v in d):
            d = [v >> 2 for v in d]
        pitch, heading = self.aim_angles(*d)
        m.set16(a + 0x1E, pitch)
        m.set16(a + 0x20, heading)
        return dist

    def _fighter_steer(self, a: int, slow: bool = None) -> None:
        """$1AE50: turn (pitch $5DC, heading $800) and fly at 220 (110 when close)."""
        m = self.mem
        m.set16(a + 4, self.turn_towards(m.u16(a + 0x1E), m.u16(a + 4), 0x5DC))
        m.set16(a + 6, self.turn_towards(m.u16(a + 0x20), m.u16(a + 6), 0x800))
        speed = 0xDC >> 1 if m.u8(a + 0x1C) else 0xDC
        vx, vy, vz = self.velocity_from_angles(a, speed)
        for o, v in ((0x0A, vx), (0x0E, vy), (0x12, vz)):
            m.set32(a + o, m.u32(a + o) + v)

    def _fighter_wander(self, a: int, timer: int = 0x14) -> None:
        """$1ACB2/$1ACB8: a random waypoint within 7000, altitude 500-2547; fly there."""
        m = self.mem
        m.set8(a + 0x1B, timer)
        x = s32(s16(w16(((s16(self.rand()) * 0x1B58) >> 16) * 2)) + m.u32(a + 0x0A))
        m.set32(a + 0x22, x)
        z = s32(s16(w16(((s16(self.rand()) * 0x1B58) >> 16) * 2)) + m.u32(a + 0x12))
        m.set32(a + 0x2A, z)
        m.set32(a + 0x26, (z & 0x7FF) + 0x1F4)
        m.set8(a + 0x1A, 1)
        m.set8(a + 0x1D, 2)
        m.set8(a + 0x1C, 0xFF)
        self._fighter_steer(a)

    def _fighter_find_unit(self, a: int):
        """$1AED0: nearest player unit (objects 2-10) within 20000: Walruses count 5000
        further, the carrier 5000 nearer."""
        m = self.mem
        best, best_d = None, 0xFFFFFFFF
        for k in range(2, 11):
            o = self.obj(k)
            if not m.u8(o + 1) & 1:
                continue
            d = self.cheb2d(a, o)
            if m.u16(o + 2) == 6:
                d += 5000
            if o == CARRIER:
                d -= 5000
            d = s32(d)
            if d > 20000:
                continue
            if d & 0xFFFFFFFF <= best_d:
                best, best_d = o, d & 0xFFFFFFFF
        # The caller tests the best distance's sign (bpl): the carrier closer than 5000
        # (negative) counts as nothing found.
        return best if best_d < 0x80000000 else None

    def _fighter_find_structure(self, a: int):
        """$1AF20: nearest attackable structure (flag bit 14) on a player island the fighter
        is attached to."""
        m = self.mem
        buf = m.u32(a + 0x3C)
        if not buf or self.owner(m.u8(buf + 1)) != 1:
            return None
        best, best_d = None, 0xFFFFFFFF
        for j in range(64):
            e = buf + 0x12 + 0x40 * j
            f = m.u16(e)
            if f & 0x4001 != 0x4001:
                continue
            dx = self.absl(m.s16(e + 0x0A) + m.u32(buf + 2) - m.u32(a + 0x0A))
            dz = self.absl(m.s16(e + 0x0E) + m.u32(buf + 0x0A) - m.u32(a + 0x12))
            d = (dx if dx >= dz else dz) & 0xFFFFFFFF
            if d <= best_d:
                best, best_d = e, d
        return best

    def _fighter_attack(self, a: int) -> None:
        m = self.mem
        t = m.u32(a + 0x22)
        if not m.u16(t) & 1:
            return self._fighter_wander(a)
        if m.u16(t) & 0x8000:
            p = [m.u32(t + 0x0A + 4 * k) for k in range(3)]
        else:
            buf = m.u32(a + 0x3C)
            if not buf:
                return self._fighter_wander(a)
            p = [m.s16(t + 0x0A) + m.u32(buf + 2), m.s16(t + 0x0C), m.s16(t + 0x0E) + m.u32(buf + 0x0A)]
        d = s32(self._fighter_aim(a, *p))
        if m.u16(a + 0x18):
            if d > 40000:
                return self._fighter_wander(a)
            if d > 5000:
                m.set8(a + 0x1C, 0xFF)
                return self._fighter_steer(a)
            if self.absw(m.u16(a + 0x20) - m.u16(a + 6)) <= 0x100:
                b = self.find_free_object()
                if b is not None:
                    self.init_world_object(b, 0x11)
                    self.reset_pair_schedules()
                    m.set8(b + 0xC0 + 2 * m.u16(0x6F0), 0xFF)
                    m.set32(b + 0x0E, m.u32(a + 0x0E))
                    vx, vz = self.yaw_vector(a, 0x64)
                    m.set32(b + 0x0A, m.u32(a + 0x0A) + vx)
                    m.set32(b + 0x12, m.u32(a + 0x12) + vz)
                    for o in (4, 6, 8):
                        m.set16(b + o, m.u16(a + o))
                    m.set16(b + 8, 0)
                    m.set32(b + 0x1E, m.u32(a + 0x22))
                    m.set16(a + 0x18, m.u16(a + 0x18) - 1)
        self._fighter_wander(a, 0x64)

    def _fighter_home(self, a: int) -> None:
        """$1ADD4: waypoint 1500 above the enemy carrier; next state 4 (far) or land."""
        m = self.mem
        c = self.obj(19)
        m.set32(a + 0x22, m.u32(c + 0x0A))
        m.set32(a + 0x26, m.u32(c + 0x0E) + 0x5DC)
        m.set32(a + 0x2A, m.u32(c + 0x12))
        if self.cheb2d(a, c) > 0x1F4:
            m.set8(a + 0x1D, 4)
            return self._fighter_recruise(a)
        self._fighter_land(a)

    def _fighter_land(self, a: int) -> None:
        """$1ADF8: within 500 and below 240: lands (back in the reserve)."""
        m = self.mem
        c = self.obj(19)
        for k in range(3):
            m.set32(a + 0x22 + 4 * k, m.u32(c + 0x0A + 4 * k))
        if self.cheb2d(a, c) <= 0x1F4 and s32(m.u32(a + 0x0E)) <= 0xF0:
            m.set16(a, 0)
            m.set16(0x11CE, m.u16(0x11CE) - 1)
            m.set16(stsim.E_AIRCRAFT, m.u16(stsim.E_AIRCRAFT) + 1)
            return
        m.set8(a + 0x1D, 5)
        self._fighter_recruise(a)

    def _fighter_recruise(self, a: int) -> None:
        """$1AE3C: back to state 1 and run it again this pass."""
        m = self.mem
        m.set8(a + 0x1A, 1)
        m.set8(a + 0x1B, 0xFF)
        self._fighter_cruise(a)

    AI_BEHAVIOURS = {}

    # ---- debris spawning --------------------------------------------------------------------
    def explode_world(self, a: int, sound: int) -> None:
        """$1C2BA and friends: remove a world object (an explosion of level `sound`) and
        throw its debris."""
        self.mem.set16(a, 0)
        self._explosion(sound, a)
        self.spawn_debris_world(a)

    def spawn_debris_world(self, a: int) -> None:
        """$1CA8E: debris at a world object's position."""
        m = self.mem
        self._spawn_debris(a, m.u32(a + 0x0A), m.u32(a + 0x0E), m.u32(a + 0x12))

    def spawn_debris_entry(self, e: int):
        """$1C6D6 / $1CA74: debris at an island entry (local position + origin $A4A).
        Returns the z offset of the last shard (left in d1), or None."""
        m = self.mem
        x, y, z = (s32(m.s16(e + 0x0A + 2 * k) + m.u32(0xA4A + 4 * k)) for k in range(3))
        return self._spawn_debris(e, x, y, z)

    def _oldest_burst(self):
        """$1CC38: the active burst with the greatest age (later ones win ties)."""
        m = self.mem
        best, age = None, 0
        for b in range(4):
            a = DEBRIS + 0x12 * b
            if m.u8(a) & 1 and m.u8(a + 1) >= age:
                best, age = a, m.u8(a + 1)
        if best is None:
            raise NotImplementedError("no debris burst to recycle")
        return best

    def _set_pair(self, addr: int, angle: int) -> None:
        s, c = self.sin_cos(angle)
        self.mem.set16(addr, s)
        self.mem.set16(addr + 2, c)

    def _rot(self, addr: int, x: int, y: int, yaw: bool):
        m = self.mem
        s, c = m.s16(addr), m.s16(addr + 2)
        x, y = s16(x), s16(y)
        if yaw:
            return (s16(((x * c + y * s) * 4 & 0xFFFFFFFF) >> 16), s16(((y * c - x * s) * 4 & 0xFFFFFFFF) >> 16))
        return (s16(((x * c - y * s) * 4 & 0xFFFFFFFF) >> 16), s16(((y * c + x * s) * 4 & 0xFFFFFFFF) >> 16))

    @staticmethod
    def _divs_rem(r: int, d: int) -> int:
        """ext.l of the low word, divs.w, swap: the remainder (or the high word on overflow)."""
        n, d = s16(r), s16(d)
        if d == 0:
            raise NotImplementedError("debris recipe divides by zero")
        q = int(n / d)
        if not -0x8000 <= q < 0x8000:
            return w16(n >> 16)
        return w16(n - q * d)

    def _spawn_debris(self, a: int, x: int, y: int, z: int) -> None:
        """$1CA9C: a burst of shards from the object type's recipe ($1C726, 18 bytes).
        debris_a1 = what the routine leaves in A1: the burst used, or the object if none."""
        m = self.mem
        self.debris_a1 = a
        self._set_pair(0xA68, m.u16(a + 4))
        self._set_pair(0xA6C, m.u16(a + 6))
        self._set_pair(0xA70, m.u16(a + 8))
        t = m.u16(a + 2)
        if t > m.u16(0x2E):
            return None
        rec = 0x1C726 + 18 * t
        count = m.u16(rec)
        if count == 0:
            return None
        burst = next((DEBRIS + 0x12 * b for b in range(4) if not m.u8(DEBRIS + 0x12 * b) & 1), None)
        if burst is None:
            burst = self._oldest_burst()
            self.free_burst(burst)
        self.debris_a1 = burst
        m.set8(burst + 1, 0)
        m.set32(burst + 2, x)
        m.set32(burst + 6, y)
        m.set32(burst + 0x0A, z)
        while count > m.u16(0x1198):
            self.free_burst(self._oldest_burst())
        m.set16(0x1198, m.u16(0x1198) - count)
        if count < 2:
            raise NotImplementedError("debris count < 2 (the original walks 65536 links)")
        head = m.u32(0x1194)
        last = head
        for _ in range(count - 1):
            last = m.u32(last + 0x16)
        m.set32(0x1194, m.u32(last + 0x16))
        m.set32(last + 0x16, 0)
        m.set32(burst + 0x0E, head)
        m.set8(burst, 1)
        low = s32(m.u32(burst + 6) - m.s16(rec + 6)) <= 0x32
        m.set16(0x1CBF4, (m.u16(0x1CBF4) & 0xF0FF) | (0 if low else 0xA00))
        pairs = rec + 0x0A
        model, left = 0, 0
        shard = head
        z3 = None
        while shard:
            if left == 0:
                while True:
                    model, n = m.u8(pairs), m.u8(pairs + 1)
                    pairs += 2
                    if n:
                        break
                left = n
            left -= 1
            zr = self._divs_rem(self.rand(), m.u16(rec + 8))
            yr = self._divs_rem(self.rand(), m.u16(rec + 6))
            xr = self._divs_rem(self.rand(), m.u16(rec + 4))
            x1, y1 = self._rot(0xA70, xr, yr, False)
            y2, z2 = self._rot(0xA68, y1, zr, False)
            x3, z3 = self._rot(0xA6C, x1, z2, True)
            m.set16(shard + 0x0A, x3)
            m.set16(shard + 0x0C, y2)
            m.set16(shard + 0x0E, z3)
            div = m.u16(rec + 2)
            if div == 0:
                raise NotImplementedError("debris recipe divides by zero")
            vx = (self.rand() >> 16) % div
            m.set16(shard + 0x10, w16(-vx) if x3 < 0 else vx)
            vy = (self.rand() >> 16) % div
            # $1CBF4 is the bpl after tst.w d6: patched to bra near the ground, so low
            # bursts always throw their shards upwards.
            neg = y2 < 0 and m.u16(0x1CBF4) & 0x0F00 == 0x0A00
            m.set16(shard + 0x12, w16(-vy) if neg else vy)
            vz = (self.rand() >> 16) % div
            m.set16(shard + 0x14, w16(-vz) if z3 < 0 else vz)
            r = self.rand()
            m.set16(shard + 4, r)
            m.set16(shard + 6, r >> 16)
            m.set16(shard + 8, self.rand())
            m.set16(shard + 2, model)
            shard = m.u32(shard + 0x16)
        return z3

    # ---- dock -----------------------------------------------------------------------------
    def dock_attach(self) -> None:
        """$1DC6C: mark the objects on the lift and in the dock (byte +1 bit 2), and pick
        the carrier model for the lift/door state (types 1-3)."""
        m = self.mem
        d = m.s16(DOCKED_WALRUS)
        a = 0
        if d >= 0:
            if d == 4:
                a = self.obj(16)
            else:
                a = (self.obj(11) if m.u8(0x122F) else self.obj(6)) + OBJECT_SIZE * d
            m.set8(a + 1, m.u8(a + 1) | 4)
        m.set32(0x6DA, a)
        bay = m.s16(0xDD0)
        a = 0
        if bay >= 0:
            a = self.obj(2 + bay)
            m.set8(a + 1, m.u8(a + 1) | 4)
        m.set32(0x6D6, a)
        if m.u16(LIFT_STATE):
            t = 3
        elif (d & bay) >= 0 or m.u16(DOOR_STATE):
            t = 2
        else:
            t = 1
        m.set16(CARRIER + 2, t)

    # ---- collisions: responses ------------------------------------------------------------
    def collision_response(self, hitter: int, victim: int) -> None:
        """$1BD30: [$754] = the hitter's damage class, [$756] = its damage; then the victim
        type's handler ($1BD70)."""
        m = self.mem
        cls = m.u16(hitter + 0x34)
        m.set16(0x754, cls)
        m.set16(0x756, m.u16(0x1BD58 + 2 * cls))
        m.set16(0xAA6, 0)
        m.set16(0xAA8, 0)
        h = m.u32(HIT_HANDLERS + 4 * m.u16(victim + 2))
        if h == 0x1BE58:
            return
        fn = self.HIT_HANDLERS.get(h)
        if fn is None:
            raise NotImplementedError(f"hit handler ${h:05X} (type {m.u16(victim + 2)})")
        fn(self, hitter, victim, cls)

    # ---- sound events (docs/units.md §17) ---------------------------------------------------
    def _sfx(self, n: int) -> None:
        """`tst.b $1249; bne; jsr $20B90; dc.w n`: effect n, unless sound effects are off."""
        if not self.mem.u8(0x1249):
            self.events.append(("sound", n))

    def _explosion(self, level: int, a: int) -> None:
        """`move.w #level,d0; jsr $20C30` with A1 = world object a (position +$A/+$E/+$12).
        The routine itself is gated by $1249 and graded by the distance to [$A02]."""
        self.events.append(("explosion", level, a))

    def _explosion_entry(self, level: int, e: int) -> None:
        """`jsr $20C08` with A1 = island entry e: its words +$A/+$C/+$E plus the island
        origin [$A4A/$A4E/$A52] (ext.l, add.l)."""
        m = self.mem
        x, y, z = (s32(m.s16(e + 0x0A + 2 * k) + m.u32(0xA4A + 4 * k)) for k in range(3))
        self.events.append(("explosion", level, x, y, z))

    def score(self, points: int) -> None:
        """$A3EE: add to the decimal score digits $918-$923."""
        m = self.mem
        a = 0x923
        carry = points
        for _ in range(12):
            carry = (carry + m.u8(a)) & 0xFFFF
            q, r = divmod(carry, 10)
            m.set8(a, r)
            a -= 1
            carry = q
            if carry == 0:
                break

    def buffer_island_owner(self) -> int:
        """$1C680: owner of the island of buffer [$A9C]."""
        m = self.mem
        return self.owner(m.u8(m.u32(0xA9C) + 1))

    def score_if_enemy(self, points: int) -> None:
        """$1C66A."""
        if self.buffer_island_owner() == 2:
            self.score(points)

    def entry_to_slot(self, e: int) -> None:
        """$1C69C: write an island entry back to its structure slot ([$AA0] island, [$AA4])."""
        m = self.mem
        s = m.u32(0xAA0) + 0x12 + 0x0C * m.u16(0xAA4)
        m.set16(s, m.u16(e + 2))
        m.set16(s + 2, m.u16(e + 6))
        m.set16(s + 4, m.u16(e + 0x38))
        m.set16(s + 8, m.u16(e + 0x0A))
        m.set16(s + 0x0A, m.u16(e + 0x0E))

    def remove_entry(self, e: int) -> None:
        """$1C696: clear the entry and its slot."""
        self.mem.set16(e, 0)
        self.mem.set16(e + 2, 0)
        self.entry_to_slot(e)

    def _sub_floor(self, a: int, d: int) -> None:
        """$1C180: subtract with a floor of 0 (unsigned)."""
        v = self.mem.u16(a) - d
        self.mem.set16(a, 0 if v < 0 else v)

    def hit_carrier(self, hitter, v, cls) -> None:
        """$1C188: damage spread over the carrier systems by where the hull was hit."""
        m = self.mem
        if cls == 0:
            return
        m.set16(0x952, 2)
        if m.u16(C_DAMAGE_MSG) == 0:
            self.events.append(("message", "Carrier has been damaged!", self._ping()))
        m.set16(C_DAMAGE_MSG, 0x1F4)
        dmg = m.u16(0x756)
        d = dmg >> 4
        self._sub_floor(0xB34, d)
        self._sub_floor(0xB3C, w16(3 * d))
        pos = m.s16(0xAB2)
        def systems_1_4(x):
            for a in (0xAFC, 0xB04, 0xB0C, 0xB14):
                self._sub_floor(a, x)
        def bow(x):
            self._sub_floor(0xB1C, x)
            self._sub_floor(0xB2C, x)
            bay = m.s16(0xDD0)
            if bay >= 0:
                self._sub_floor(0x4AA + 0x34 * bay, x)
        def stern(x):
            self._sub_floor(0xAF4, x)
            self._sub_floor(0xB24, x)
        if pos > 300:
            systems_1_4((dmg >> 3) >> 2)
            bow(dmg >> 1)
        elif pos < -300:
            systems_1_4((dmg >> 3) >> 2)
            stern(dmg >> 1)
        else:
            bow((dmg >> 3) >> 1)
            stern((dmg >> 3) >> 1)
            systems_1_4(dmg >> 2)

    def hit_enemy_carrier(self, hitter, v, cls) -> None:
        """$1C0F4: strength -= damage (not from its own fighters); destroyed at <= 0."""
        m = self.mem
        if cls == 0 or m.u16(hitter + 2) == 0x21:
            return
        st = s16(m.u16(stsim.E_STRENGTH) - m.u16(0x756))
        if st > 0:
            m.set16(stsim.E_STRENGTH, st)
            return
        m.set8(stsim.AI_ENABLED, 0)
        m.set16(v, 0)
        self._explosion(2, v)
        self.spawn_debris_world(v)
        for _ in range(4):
            self.score(25000)
        self.events.append(("message", "Enemy carrier destroyed", self._ping()))
        m.set16(stsim.GAME_END_TIMER, 100)

    def _vehicle_damage(self, rec: int, d: int, kind: str, v: int) -> bool:
        """$1BF42: health -= d; True if it survives."""
        m = self.mem
        h = s16(m.u16(rec + 0x14) - d)
        if h > 0:
            m.set16(rec + 0x14, h)
            return True
        m.set16(rec + 0x14, 0)
        self._explosion(1, v)
        self.spawn_debris_world(v)
        self.vehicle_destroyed(kind, v)
        return False

    def vehicle_destroyed(self, kind: str, v: int) -> None:
        """Hook: $19188 (Manta) / $191F2 (Walrus), ported in stunits_vehicles."""
        raise NotImplementedError(f"{kind} destroyed")

    def hit_walrus(self, hitter, v, cls) -> None:
        """$1BE5A: rammed (class 11): pushed back and 1000/2000 damage; otherwise damage."""
        m = self.mem
        if cls == 0:
            return
        bay = m.u16(v + 0x1A)
        rec = 0x570 + 0x34 * bay
        if cls == 11:
            speed = m.u16(rec + 4)
            m.set16(rec + 4, (s16(-s16(speed)) >> 2) - 0x14)
            m.set16(rec + 6, 0)
            m.set16(rec + 2, 0)
            m.set8(rec, m.u8(rec) & 0xFC)
            if m.u8(0x1233) and bay == m.u16(0x6E0):
                m.set8(0x1233, 0)
            t = m.u16(hitter + 2)
            vx, vz = self.yaw_vector(v, speed)
            m.set32(v + 0x0A, m.u32(v + 0x0A) - vx)
            m.set32(v + 0x12, m.u32(v + 0x12) - vz)
            self.events.append(("message", f"Walrus {bay + 1} motion blocked by object", self._ping(True, True)))
            self._sfx(0)
            self._vehicle_damage(rec, 2000 if 1 <= t < 4 else 1000, "Walrus", v)
            return
        if m.u8(0x118A):
            return
        if self._vehicle_damage(rec, m.u16(0x756), "Walrus", v):
            hold = m.u16(rec + 0x30)
            m.set16(rec + 0x30, 0x1F4)
            if hold == 0:
                self.events.append(("message", f"Walrus {bay + 1} has been damaged!", self._ping()))

    def hit_manta(self, hitter, v, cls) -> None:
        """$1BF84."""
        m = self.mem
        if cls == 0 or m.u8(0x118A):
            return
        bay = m.u16(v + 0x1A)
        rec = 0x496 + 0x34 * bay
        if self._vehicle_damage(rec, m.u16(0x756), "Manta", v):
            hold = m.u16(rec + 0x30)
            m.set16(rec + 0x30, 0x1F4)
            if hold == 0:
                self.events.append(("message", f"Manta {bay + 1} has been damaged!", self._ping()))

    def hit_laser(self, hitter, v, cls) -> None:
        """$1BFFA: a laser bolt is removed (unless hit by another bolt)."""
        if self.mem.u16(hitter + 2) != 0x10:
            self.mem.set16(v, 0)
            self._explosion(0, v)

    def hit_deck_drone(self, hitter, v, cls) -> None:
        """$1C010: a carrier defence drone is destroyed by anything but another drone."""
        m = self.mem
        if 0x0A <= m.s16(hitter + 2) <= 0x0D:
            return
        self.events.append(("message", "Defence drone destroyed", self._ping()))
        m.set8(DOCKED_WALRUS, 0xFF if m.u8(v) & 4 else 0)
        m.set16(DOOR_STATE, 3)
        self.dock_attach()
        m.set16(0x65E + 14 * m.u16(v + 0x1A), 0)
        self._explosion(0, v)
        m.set16(v, 0)
        self.spawn_debris_world(v)

    def hit_world_simple(self, hitter, v, cls) -> None:
        """$1C06E/$1C080/$1C312: removed, debris. The explosion is level 1 from the guided
        missile's handler $1C080, else 0."""
        m = self.mem
        self._explosion(1 if m.u32(HIT_HANDLERS + 4 * m.u16(v + 2)) == 0x1C080 else 0, v)
        m.set16(v, 0)
        self.spawn_debris_world(v)

    def hit_flare(self, hitter, v, cls) -> None:
        """$1C2A4."""
        m = self.mem
        m.set16(v, 0)
        self._explosion(0, v)
        m.set16(0x1152, m.u16(0x1152) - 1)
        self.spawn_debris_world(v)

    def hit_antiship(self, hitter, v, cls) -> None:
        """$1C2D2: destroyed; 250 points on an enemy island."""
        self.mem.set16(v, 0)
        self._explosion(1, v)
        self.spawn_debris_world(v)
        self.score_if_enemy(250)

    def hit_island_drone(self, hitter, v, cls) -> None:
        """$1C092."""
        m = self.mem
        if cls == 0:
            return
        h = s16(m.u16(v + 0x38) - m.u16(0x756))
        if h > 0:
            m.set16(v + 0x38, h)
            return
        m.set16(v, 0)
        self.score_if_enemy(750)
        isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * m.u8(m.u32(0xA9C) + 1)
        m.set16(isl + 0x10, m.u16(isl + 0x10) - 1)
        self._explosion_entry(1, v)
        self.spawn_debris_entry(v)

    def hit_sam(self, hitter, v, cls) -> None:
        """$1C292."""
        self.mem.set16(v, 0)
        self._explosion_entry(0, v)
        self.spawn_debris_entry(v)

    def hit_rock(self, hitter, v, cls) -> None:
        """$1C2EE: rocks pass through the volcano."""
        if self.mem.u16(hitter + 2) == 7:
            return
        self.mem.set16(v, 0)
        self._explosion_entry(0, v)
        self.spawn_debris_entry(v)
        self.score(10)

    def hit_tree(self, hitter, v, cls) -> None:
        """$1C272."""
        self.mem.set16(v, 0)
        self._explosion_entry(1, v)
        self.spawn_debris_entry(v)
        self.remove_entry(v)
        self.score(1)

    def hit_enemy_fighter(self, hitter, v, cls) -> None:
        """$1C4E0."""
        m = self.mem
        if m.u16(hitter + 2) == 4:
            return
        h = s16(m.u16(v + 0x38) - m.u16(0x756))
        if h > 0:
            m.set16(v + 0x38, h)
            return
        m.set16(v, 0)
        self.spawn_debris_world(v)
        self._explosion(2, self.debris_a1)          # quirk: A1 is what $1CA8E left there
        m.set16(0x11CE, m.u16(0x11CE) - 1)

    BUILDING_REWARDS = {0x1C3D6: (0x400, 2500), 0x1C3E6: (0, 1000), 0x1C3F4: (0, 1000),
                        0x1C402: (0x200, 2000), 0x1C412: (0x100, 1000), 0x1C422: (0x100, 1000),
                        0x1C432: (0x100, 2000), 0x1C442: (0x100, 2000), 0x1C452: (0x100, 1500)}

    def hit_building(self, hitter, v, cls) -> None:
        """$1C3D6..$1C45E: health written back to the slot; destroyed buildings score and
        raise the difficulty on enemy islands."""
        m = self.mem
        h_addr = m.u32(HIT_HANDLERS + 4 * m.u16(v + 2))
        diff, points = self.BUILDING_REWARDS[h_addr]
        m.set16(0xAA8, diff)
        m.set16(0xAA6, points)
        h = s16(m.u16(v + 0x38) - m.u16(0x756))
        if h > 0:
            m.set16(v + 0x38, h)
            return self.entry_to_slot(v)
        self._explosion_entry(2 if diff else 1, v)
        left = self.spawn_debris_entry(v)
        self.remove_entry(v)
        if self.buffer_island_owner() == 2:
            self.score(points)
            if diff:
                self.difficulty_add(diff)
        else:
            # Quirk: this branch lands on the "store health" code, so the destroyed entry
            # gets d1 as its health and is written back to its slot again. By then d1 is the
            # last debris shard's z offset (or the negative health if there was no debris).
            m.set16(v + 0x38, h if left is None else left)
            self.entry_to_slot(v)

    def hit_command_centre(self, hitter, v, cls) -> None:
        """$1C324: lasers do 1/8; at 0 the island turns neutral."""
        m = self.mem
        t = m.u16(hitter + 2)
        if t == 0x15:
            return
        d = m.u16(0x756)
        if t == 0x10:
            d = s16(d) >> 3
        h = s16(m.u16(v + 0x38) - d)
        if h > 0:
            m.set16(v + 0x38, h)
            return self.entry_to_slot(v)
        if self.buffer_island_owner() == 2:
            self.score(5000)
            self.difficulty_add(0x800)
            m.set16(0xD52, m.u16(0xD52) + 1)
        i = m.u8(m.u32(0xA9C) + 1)
        m.set8(stsim.OWNERS + i, 0)
        if i == m.u16(stsim.STOCKPILE):
            m.set8(stsim.STOCKPILE, 0xFF)
            self.stores_clear()
        self.events.append(("neutral", i))
        self._explosion_entry(2, v)
        self.spawn_debris_entry(v)
        self.remove_entry(v)
        self.network_invalidate()

    def hit_construction(self, hitter, v, cls) -> None:
        """$1C4B6: progress -= the damage class; 15 points per hit on enemy islands."""
        m = self.mem
        self._explosion_entry(0, v)
        h = s16(m.u16(v + 0x38) - m.u16(0x754))
        if h <= 0:
            return self.remove_entry(v)
        m.set16(v + 0x38, h)
        self.score_if_enemy(15)
        self.entry_to_slot(v)

    def hit_virus_bomb(self, hitter, v, cls) -> None:
        """$1C512: entering a command centre from the front captures the island."""
        m = self.mem
        if m.u16(hitter + 2) == 0x29:
            a = self.atan2(m.s16(0xAB0), m.s16(0xAB2))
            if self.absw(a - m.u16(hitter + 6)) <= 10000:
                i = m.u8(m.u32(0xA9C) + 1)
                if self.owner(i) == 1:
                    self.events.append(("message", "Friendly control centre bombed!", self._ping()))
                else:
                    m.set8(stsim.OWNERS + i, 1)
                    m.set16(0xD52, m.u16(0xD52) + 1)
                    if i == m.u16(stsim.STOCKPILE):
                        m.set8(stsim.STOCKPILE, 0xFF)
                        self.stores_clear()
                    self.events.append(("captured", i, 1))
                    m.set16(v, 0)
                    self.spawn_debris_world(v)
                    self.network_invalidate()
                    self.score(10000)
                    self._sfx(11)
                    return
                self._explosion(0, v)
                m.set16(v, 0)
                self.spawn_debris_world(v)
                return
        self._explosion(0, v)
        m.set16(v, 0)
        self.spawn_debris_world(v)

    def hit_blast(self, hitter, v, cls) -> None:
        """$1C5EE explode_blast: debris, then a type 23 blast volume where it was."""
        m = self.mem
        self._explosion(2, v)
        self.spawn_debris_world(v)
        pos = [m.u32(v + o) for o in (0x0A, 0x0E, 0x12)]
        m.set16(v, 0)
        a = self.find_free_object()
        if a is None:
            raise NotImplementedError("blast with no free object")
        f = m.u16(FRAME)
        m.set16(FRAME, (f + 1) & 0x7FFF)
        self.init_world_object(a, 0x17)
        self.reset_pair_schedules()
        m.set16(FRAME, f)
        for o, val in zip((0x0A, 0x0E, 0x12), pos):
            m.set32(a + o, val)
        m.set16(a + 0x30, 0x7D0)
        m.set16(a + 0x32, 0x1F4)
        m.set8(a + 0x1B, 5)

    def hit_supply_drone(self, hitter, v, cls) -> None:
        """$1C0CA."""
        m = self.mem
        if cls == 0:
            return
        h = s16(m.u16(v + 0x38) - m.u16(0x756))
        if h > 0:
            m.set16(v + 0x38, h)
            return
        self.supply_drone_destroyed(v)
        self._explosion(1, v)

    def supply_drone_destroyed(self, v: int) -> None:
        """Hook: the rest of $1C0CA ($1925C), ported in stunits_enemy."""
        raise NotImplementedError("supply drone destroyed")

    HIT_HANDLERS = {}

    def difficulty_add(self, d: int) -> None:
        """difficulty_add $190EE then difficulty_apply $190FE."""
        m = self.mem
        v = m.u16(0x444) + d
        m.set16(0x444, 0xFFFF if v > 0xFFFF else v)
        self.difficulty_apply()

    def difficulty_apply(self) -> None:
        """$190FE: the defence parameters $446-$454 and command-centre health from [$444]."""
        m = self.mem
        d = m.u16(0x444)
        m.set16(0x446, 3 if s16(d) >= 0x4000 else 2)
        n = 3
        if d & 0x8000:
            n += w16(d - 0x8000) // 10000
        m.set16(0x448, n)
        d6 = d >> 6
        d10 = d6 >> 4
        m.set16(0x44A, (d10 >> 1) + d10 + 0x96)
        m.set16(0x450, d10 + 0xC8)
        d2 = w16(d6 + 0x400)
        m.set16(0x452, d2)
        d2 >>= 1
        m.set16(0x44C, d2 + (d2 >> 1))
        m.set16(0x454, d if d >= 0x4000 else 0x4000)
        cc = (d >> 4) + 0x200
        m.set16(0x44E, cc)
        m.set16(0x18D74, cc + 0x800)
        m.set16(0x18DCC, cc + 0x800)


# The unit layer is split across modules: UnitCore (this file) and three mixins that
# override its hooks and stubs. Mixins come first in the MRO, so their methods win.
from stunits_vehicles import VehicleMixin      # noqa: E402  Mantas and Walruses out of the hangar
from stunits_enemy import EnemyMixin            # noqa: E402  enemy close-range combat, supply transfers
from stunits_carrier import CarrierMixin        # noqa: E402  carrier turret, SSM, flares, drones
from stunits_controls import ControlsMixin      # noqa: E402  player commands, screen input, game flow
from stunits_setup import SetupMixin            # noqa: E402  new game
from stunits_render import RenderMixin          # noqa: E402  state the screens' renders write


class UnitState(VehicleMixin, EnemyMixin, CarrierMixin, ControlsMixin, SetupMixin, RenderMixin, UnitCore):
    """The whole unit layer: UnitCore plus the mixins."""


UnitState.ISLAND_BEHAVIOURS = {
    0x199B4: UnitState.aa_turret, 0x19B44: UnitState.command_centre_defence,
    0x19F00: UnitState.drone_pad, 0x19F8A: UnitState.antiship_site,
    0x19862: UnitState.dish_spin, 0x19872: UnitState.dish_track,
    0x1989C: UnitState.volcano, 0x1996C: UnitState.volcano_rock,
    0x19AC4: UnitState.sam_launcher, 0x19BC6: UnitState.sam, 0x19C46: UnitState.island_drone,
}
UnitState.AI_BEHAVIOURS = {
    0x1AABA: UnitState.antiship_missile, 0x1A9A6: UnitState.blast,
    0x1A6A0: UnitState.laser_bolt, 0x1A6D0: UnitState.homing_missile,
    0x1A886: UnitState.guided_missile, 0x1A8F6: UnitState.cruise_missile,
    0x1A9B0: UnitState.bomb, 0x1AA52: UnitState.virus_bomb, 0x1AC00: UnitState.enemy_fighter,
}
UnitState.HIT_HANDLERS = {0x1C188: UnitState.hit_carrier, 0x1C0F4: UnitState.hit_enemy_carrier, 0x1BF84: UnitState.hit_manta,
                0x1BE5A: UnitState.hit_walrus, 0x1C2EE: UnitState.hit_rock, 0x1C010: UnitState.hit_deck_drone,
                0x1BFFA: UnitState.hit_laser, 0x1C06E: UnitState.hit_world_simple, 0x1C080: UnitState.hit_world_simple,
                0x1C5EE: UnitState.hit_blast, 0x1C512: UnitState.hit_virus_bomb, 0x1C092: UnitState.hit_island_drone,
                0x1C272: UnitState.hit_tree, 0x1C292: UnitState.hit_sam, 0x1C2A4: UnitState.hit_flare,
                0x1C312: UnitState.hit_world_simple, 0x1C4E0: UnitState.hit_enemy_fighter, 0x1C2D2: UnitState.hit_antiship,
                0x1C0CA: UnitState.hit_supply_drone, 0x1C324: UnitState.hit_command_centre,
                0x1C4B6: UnitState.hit_construction,
                **{a: UnitState.hit_building for a in (0x1C3D6, 0x1C3E6, 0x1C3F4, 0x1C402, 0x1C412,
                                             0x1C422, 0x1C432, 0x1C442, 0x1C452)}}

