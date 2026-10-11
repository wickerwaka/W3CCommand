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
Anything not ported raises NotImplementedError rather than guessing.
"""
import stsim
from stsim import Mem, WorldState, s16, s32, w16

OBJECTS, OBJECT_SIZE, OBJECT_HEADER = stsim.OBJECTS, stsim.OBJECT_SIZE, stsim.OBJECT_HEADER
CARRIER = OBJECTS + OBJECT_SIZE * 10
ISLAND_BUFFERS, ISLAND_BUFFER_SIZE = (0x62CDA, 0x63CEC), 0x12 + 64 * 0x40
DEBRIS, SHARDS = 0x11D8, 0x66010
OBJ_FLAGS, OBJ_PARAMS = 0x19362, 0x193D6
SLOT_ENTRY = 0x18066
ISLAND_HANDLERS, AI_HANDLERS = 0x19778, 0x1A5B6
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
    ("game over $463", 0x463, 4),
    ("object scratch $6EE", 0x6EE, 4),
    ("turret $6C8", 0x6C8, 0x10),
    ("vehicle bays", 0x496, 0x640 - 0x496),
    ("drone stations", 0x65C, 0x69C - 0x65C),
    ("lift object $6D6", 0x6D6, 8),
    ("island count $74E", 0x74E, 2),
    ("cooldowns $93E", 0x93E, 6),
    ("frame counter $944", 0x944, 2),
    ("island context", 0xA28, 0xA58 - 0xA28),
    ("matrix $A6C", 0xA6C, 4),
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
                      "slot entry table", "island handler table", "ai handler table", "drone tables"}


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
        if m.u8(0x8FD):
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
                raise NotImplementedError("defence drone launch sequence")
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
        self.init_island_object(a, 0x1C)
        self.reset_island_schedule()
        m.set16(a + 0x0C, m.u16(e + 0x0C))
        vx, vz = self.yaw_vector(e, 0x64)
        m.set16(a + 0x0A, m.u16(e + 0x0A) + vx)
        m.set16(a + 0x0E, m.u16(e + 0x0E) + vz)
        for o in (4, 6, 8):
            m.set16(a + o, m.u16(e + o))
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

    ISLAND_BEHAVIOURS = {0x199B4: aa_turret, 0x19B44: command_centre_defence,
                         0x19F00: drone_pad, 0x19F8A: antiship_site}

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
                    raise NotImplementedError(f"AI object handler ${h:05X} (type {m.u16(a + 2)})")
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
                    hx, hz = self.rotate_yaw(w16(-ex), w16(-ez), w16(-m.u16(o + 6)))
                    m.set16(0xAB2, hz)
                    if self.absw(hx) > s16(re + 400) or self.absw(hz) > s16(re + 1000):
                        continue
                else:
                    mx = ax if ax >= az else az
                    if mx > s16(m.u16(o + 0x30) + re):
                        continue
                raise NotImplementedError(f"contact: object {k} with island entry {j}")

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
                    raise NotImplementedError(f"contact: objects {i} and {j}")
                if self.absw(hx) > s16(r + 400) or self.absw(hz) > s16(r + 1000):
                    continue
                raise NotImplementedError(f"contact: objects {i} and {j}")

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
        if self.mem.u8(isl.a):
            raise NotImplementedError("tree cut on a loaded island (debris)")
