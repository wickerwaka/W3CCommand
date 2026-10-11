"""Reference model of the unit layer (docs/units.md), built on tools/stsim.py.

Same conventions as stsim: the state is the game's memory layout (UnitState extends
WorldState and adds UNIT_REGIONS), arithmetic follows the 68000 exactly, and every routine
is checked against the original under Unicorn (tools/check_units.py).

Ported so far (verified on the action-game captures):
  carrier_step $1E744 with carrier_speed_governor $1EA3E, the autopilot and grounding;
  manta_lift_step $1EAA0 (idle) and walrus_dock_door_step $1ED94;
  the shared maths: rotate_yaw $A5E0, rotate_pitch $A620, atan2 $AF8E, turn_towards $1A18A,
  self_centre $1E734, yaw_vector $1A140, island_box_distance $1A1A2.
"""
import stsim
from stsim import Mem, WorldState, s16, s32, w16

OBJECTS, OBJECT_SIZE, OBJECT_HEADER = stsim.OBJECTS, stsim.OBJECT_SIZE, stsim.OBJECT_HEADER
CARRIER = OBJECTS + OBJECT_SIZE * 10
ISLAND_BUFFERS, ISLAND_BUFFER_SIZE = (0x62CDA, 0x63CEC), 0x12 + 64 * 0x40
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
] + [(f"object {k}", OBJECTS + OBJECT_SIZE * k, OBJECT_HEADER) for k in (0, 1, 10)]
UNIT_INPUT_REGIONS = {"sine table", "atan table", "key table", "view pointers"}


class UnitState(WorldState):
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
                self.events.append(("message", "carrier reached waypoint"))
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
        self.events.append(("message", "Carrier has grounded"))
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
            raise NotImplementedError("dock door closed: $1DC6C")
        m.set16(DOOR_POS, pos)
