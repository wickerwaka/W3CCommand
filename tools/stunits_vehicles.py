"""Unit layer, part of tools/stunits.py: Mantas and Walruses out of the hangar: flight ($1CDA2), driving ($1DCFC), the Manta lift ($1EAA0) and dock sequences, loadouts ($1ED46), firing ($1F9E8, $1FB6E), destruction ($19188, $191F2), and the hangar commands (launch, prepare, refuel, replace).

The mixin is combined into stunits.UnitState ahead of stunits.UnitCore, so methods defined
here replace the core's stubs and hooks. Use only `import stsim` / `import stunits` at module
level (stunits imports this module while it is still loading; read its constants at run time).

For tools/check_units.py, this module may define:
  ROUTINES  {"name": (original address, "method name")}: checked on every capture
  SCENARIOS [function(mem, rnd)]: perturbations run pass by pass against the original
  CHECKS    [function(files) -> bool]: any other checks (e.g. commands called with registers)

Records ($34 bytes; Mantas at $496, Walruses at $570):
  Manta  +0 flags (0 auto, 1 waypoint, 2 report arrival, 4 crashing, 5 take-off ordered,
            6/7 self-centre pitch/rudder), +1 status (0 ready, 1 repair, 2 preparing,
            3 on the lift, 8 out, $FF destroyed), +2 rudder, +4 pitch, +6 speed, +8 target
            speed, +$A fuel, +$C missiles, +$E payload (4 bomb, $A comms pod), +$10 laser,
            +$14 health, +$1A weapon (0 laser, 1 missile, else payload), +$1C/+$1D auto mode
            and step, +$1E/+$22/+$26 waypoint x/y/z, +$2A cruise speed, +$2C wing toggle,
            +$2E telemetry (1 weak, 2 lost), +$30 damage-message hold, +$33.
  Walrus +0 flags (0 auto, 1 waypoint, 2 report, 3 beached, 4 steering a guided missile,
            5 rudder turns the turret, 6/7 self-centre turret/rudder), +1 status, +2 rudder,
            +4 speed, +6 target speed, +8 fuel, +$A laser shots (-1 none), +$C missiles,
            +$E payload (8 drop, 9 ACCB, $B fuel pod), +$14 health, +$18 weapon, +$1A/+$1B
            auto mode and step, +$1C/+$20 waypoint x/z, +$24 cruise speed, +$26/+$28 loadout
            codes, +$2C telemetry, +$2E missile guidance hold.
Manta auto modes: 0 land on the carrier, 1 on deck, 2 deck take-off, 3 fly to waypoint,
4 land on a runway, 5 landed, 6 runway take-off. Walrus: 0 dock, 1 docked, 2 launch, 3 drive
to waypoint. UI-only side effects (gauges $7A1A/$7A46/$7A56, redraw flags, panels $B418,
sounds $20B90) are left out; messages go to self.events.
"""
import stsim
import stunits
from stsim import s16, s32, w16

MANTA_RECS, WALRUS_RECS, REC = 0x496, 0x570, 0x34
MANTA_OBJ, WALRUS_OBJ = 2, 6
CARRIER_OBJ = 0x5E95A
LOADOUT = 0xCAC4                    # 26-byte items: +2 stock counter, +$16 fitting handler
RAMP_FORWARD, RAMP_REVERSE = 0x1E5E0, 0x1E638
LIFT_POS, LIFT_BAY, LIFT_NEXT = 0xDD6, 0xDD0, 0xDC4
COMMS_POD, COMMS_STATUS = 0xDE0, 0xB0A
VIEWED, POINTER_MODE, FIRE_KEYS, FIRE_INHIBIT = 0x9FE, 0x457, 0x881, 0x8F1
MOUSE_DX, MOUSE_DY, KEY_UP, KEY_DOWN = 0x1268, 0x126A, 0x828, 0x838
MANTA_LANDING, WALRUS_DOCKING = 0x1232, 0x1233
LOCK_TIMER, LOCK_TARGET = 0x116C, 0x1168
FUEL_MANTA, FUEL_WALRUS, FUEL_MAX = 0xD00, 0xD04, 0xFFFE0
STOCK_MANTA, STOCK_WALRUS = 0xCD0, 0xCEA

# State these routines read or write that the other regions don't cover.
for _region in (("aircraft and AAV fuel", 0xD00, 8),
                ("hangar selection $48E", 0x48E, 8),
                ("hangar panels $DC0", 0xDC0, 4),
                ("fire inhibit $8F1", FIRE_INHIBIT, 1),
                ("missile lock $1162", 0x1162, 12),
                ("loadout table $CAC4", LOADOUT, 12 * 26),
                ("walrus ramp tables", RAMP_FORWARD, 0x1E674 - RAMP_FORWARD)):
    if _region not in stunits.UNIT_REGIONS:
        stunits.UNIT_REGIONS.append(_region)
stunits.UNIT_INPUT_REGIONS |= {"hangar selection $48E", "fire inhibit $8F1", "loadout table $CAC4",
                               "walrus ramp tables"}

LOADOUT_HANDLERS = {0x1ED5A: "missiles 1", 0x1ED60: "missiles 2", 0x1ED66: "missiles 3",
                    0x1ED6C: "bomb", 0x1ED74: "laser", 0x1ED7A: "avatar", 0x1ED82: "payload",
                    0x1ED88: "comms pod", 0xCBFC: None}


def _clamp255(v: int) -> int:
    v = s16(v)
    return 0xFF if v > 0xFF else w16(-0xFF) if v < -0xFF else w16(v)


def _divs(n: int, d: int) -> int:
    """divs.w quotient (truncates towards zero); the callers never overflow."""
    q = abs(n) // abs(d)
    return w16(-q if (n < 0) != (d < 0) else q)


class VehicleMixin:
    # ---- shared ---------------------------------------------------------------------------
    def bays_step(self, cooldown: int, first: int, records: int, kind: str) -> None:
        """$1CDA2 (Mantas) / $1DCFC (Walruses): every bay whose vehicle is out flies or drives."""
        m = self.mem
        if m.u16(cooldown):
            m.set16(cooldown, m.u16(cooldown) - 1)
        m.set16(0x6F0, first)
        step = self._manta_step if kind == "Manta" else self._walrus_step
        for k in range(4):
            step(records + REC * k, self.obj(first + k))
            m.set16(0x6F0, m.u16(0x6F0) + 1)

    def manta_bays_step(self) -> None:
        self.bays_step(0x940, MANTA_OBJ, MANTA_RECS, "Manta")

    def walrus_bays_step(self) -> None:
        self.bays_step(0x942, WALRUS_OBJ, WALRUS_RECS, "Walrus")

    def _msg(self, text: str) -> None:
        self.events.append(("message", text))

    def _max_abs(self, dx: int, dz: int) -> int:
        """|dx|, |dz| by neg.l, the larger by a signed compare."""
        a, b = self.absl(dx), self.absl(dz)
        return a if a >= b else b

    def _cheb(self, a: int, b: int) -> int:
        m = self.mem
        return self._max_abs(m.u32(a + 0x0A) - m.u32(b + 0x0A), m.u32(a + 0x12) - m.u32(b + 0x12))

    def _centre(self, a: int) -> int:
        """$1E72C: self-centre the word at a."""
        v = self.self_centre(self.mem.u16(a))
        self.mem.set16(a, v)
        return v

    def _deck_pos(self, o: int) -> None:
        """$1DBE0 / $1E3A0: position = carrier + local (+$1C, +$1E) turned by its heading."""
        m, c = self.mem, CARRIER_OBJ
        self.set_matrix(m.u16(c + 6))
        x, z = self.matrix_rotate(m.u16(o + 0x1C), m.u16(o + 0x1E))
        m.set32(o + 0x0A, x + m.u32(c + 0x0A))
        m.set32(o + 0x12, z + m.u32(c + 0x12))

    def _viewed_pointer(self, o: int) -> None:
        """$1CD40: hand the pointer back when the viewed vehicle is taken over by the autopilot."""
        if self.mem.u32(VIEWED) == o:
            self.mem.set8(POINTER_MODE, 0xFF)

    def _telemetry(self, rec: int, o: int, d5: int, field: int, kind: str) -> bool:
        """Range to the carrier, or to a deployed comms pod while long-range comms work:
        beyond 200000 the signal is weak (1), beyond 260000 lost (2). A Walrus out of range
        is destroyed (False)."""
        m = self.mem
        d0 = self._cheb(o, CARRIER_OBJ)
        if d0 <= 200000:
            m.set8(stunits.C_NEAR_UNIT, 0xFF)
            return True
        if m.u8(COMMS_STATUS) and m.u32(COMMS_POD):
            pod = m.u32(COMMS_POD)
            if pod == o:
                return True
            d0 = self._cheb(o, pod)
            if d0 <= 200000:
                return True
        bay = m.u16(o + 0x1A) + 1
        if d5 == 0:
            self._msg(f"{kind} {bay} telemetry signal weak")
        m.set16(rec + field, 1)
        if d0 <= 0x3F7A0:
            return True
        if kind == "Walrus":
            self._msg(f"{kind} {bay} telemetry lost")
            m.set16(rec + field, 2)
            self.vehicle_destroyed(kind, o)
            return False
        if d5 != 2:
            self._msg(f"{kind} {bay} telemetry lost")
        m.set16(rec + field, 2)
        return True

    def _new_object(self, t: int):
        """find_free_object, then $192DA/$195DA, and the shooter's pair schedule held off."""
        m = self.mem
        b = self.find_free_object()
        if b is None:
            return None
        self.init_world_object(b, t)
        self.reset_pair_schedules()
        m.set8(b + s16(w16(2 * m.u16(0x6F0) + 0xC0)), 0xFF)
        return b

    def _copy_angles(self, src: int, dst: int) -> None:
        """$1A0C8."""
        for o in (4, 6, 8):
            self.mem.set16(dst + o, self.mem.u16(src + o))

    def _offset_from(self, o: int, x: int, y: int):
        """$A454/$A45E/$A468 then $A5BC, $A4F4, $A558: a body offset (x, y, 0) turned by the
        object's roll, pitch and heading."""
        m = self.mem
        self._set_pair(0xA68, m.u16(o + 4))
        self._set_pair(0xA6C, m.u16(o + 6))
        self._set_pair(0xA70, m.u16(o + 8))
        x1, y1 = self._rot(0xA70, x, y, False)
        y2, z2 = self._rot(0xA68, y1, 0, False)
        x3, z3 = self._rot(0xA6C, x1, z2, True)
        return x3, y2, z3

    def _place(self, b: int, o: int, dx: int, dy: int, dz: int) -> None:
        m = self.mem
        m.set32(b + 0x0A, dx + m.u32(o + 0x0A))
        m.set32(b + 0x12, dz + m.u32(o + 0x12))
        m.set32(b + 0x0E, dy + m.u32(o + 0x0E))

    def _door(self, state: int) -> None:
        """$1EDE8 (1, opening) / $1EDF2 (3, closing)."""
        self.mem.set16(stunits.DOOR_STATE, state)
        self.dock_attach()

    # ---- destruction -------------------------------------------------------------------------
    def vehicle_destroyed(self, kind: str, v: int) -> None:
        """$19188 (Manta) / $191F2 (Walrus): drop the comms pod link, remove the object and
        release its island buffer, cancel its landing/docking, mark the bay destroyed ($FF),
        and empty the lift or dock if it was on it."""
        m = self.mem
        manta = kind == "Manta"
        self._msg(f"{kind} {m.u16(v + 0x1A) + 1} has been destroyed!")
        if m.u32(COMMS_POD) == v:
            m.set32(COMMS_POD, 0)
        old = m.u16(v)
        m.set16(v, 0)
        self.release_island_buffer(v)
        bay = m.u16(v + 0x1A)
        flag, current = (MANTA_LANDING, 0x6DE) if manta else (WALRUS_DOCKING, 0x6E0)
        if m.u8(flag) and bay == m.u16(current):
            m.set8(flag, 0)
        rec = (MANTA_RECS if manta else WALRUS_RECS) + s16(w16(bay * REC))
        m.set8(rec, 0)
        m.set8(rec + 1, 0xFF)
        if old & 4:
            m.set16(LIFT_BAY if manta else stunits.DOCKED_WALRUS, 0xFFFF)
            self.dock_attach()

    # ---- Manta --------------------------------------------------------------------------------
    def _manta_step(self, a1: int, a4: int) -> None:
        m = self.mem
        if m.s8(a1 + 1) < 3 or not m.u8(a4 + 1) & 1:
            return
        if m.u16(a1 + 0x14) == 0:
            return self._manta_kill(a1, a4)
        if m.u16(a1 + 0x30):
            m.set16(a1 + 0x30, m.u16(a1 + 0x30) - 1)
        d5 = m.u16(a1 + 0x2E)
        m.set16(a1 + 0x2E, 0)
        if not m.u8(a4 + 1) & 4:
            self._telemetry(a1, a4, d5, 0x2E, "Manta")
        if m.u8(a1) & 1:
            return self._manta_auto(a1, a4)
        if a4 == m.u32(VIEWED):
            if not m.u8(POINTER_MODE):
                m.set8(a1, m.u8(a1) & 0x3F)
                m.set16(a1 + 2, _clamp255(m.u16(a1 + 2) + m.u16(MOUSE_DX)))
                m.set16(MOUSE_DX, 0)
                m.set16(a1 + 4, _clamp255(m.u16(a1 + 4) + m.u16(MOUSE_DY)))
                m.set16(MOUSE_DY, 0)
                keys = m.u8(FIRE_KEYS)
                m.set8(FIRE_KEYS, keys & 0xFE)
                if keys & 1 and not m.u8(FIRE_INHIBIT):
                    self.manta_fire(a1, a4)
            d0 = m.u16(a1 + 6)
            if m.u8(KEY_UP):
                m.set16(a1 + 8, d0 + 0x10)
            elif m.u8(KEY_DOWN):
                m.set16(a1 + 8, d0 - 0x10)
        if s32(m.u32(a4 + 0x0E)) > 0xD48 and not m.u16(a1 + 4) & 0x8000:
            m.set8(a1, m.u8(a1) | 0x40)
        self._manta_terrain(a1, a4)

    def _manta_kill(self, a1: int, a4: int) -> None:
        """$1DC4C."""
        self.spawn_debris_world(a4)
        self.vehicle_destroyed("Manta", a4)

    def _manta_damage(self, a1: int, a4: int, d: int) -> bool:
        """$1DC40: health -= d (destroyed at 0 or below); True while the object is live."""
        m = self.mem
        h = m.s16(a1 + 0x14)
        if h > s16(d):
            m.set16(a1 + 0x14, h - d)
        else:
            m.set16(a1 + 0x14, 0)
            self._manta_kill(a1, a4)
        return bool(m.u8(a4 + 1) & 1)

    def _manta_terrain(self, a1: int, a4: int) -> None:
        """$1CF9A: ground contact: below 35 hit the ground; between 35 and 150 inside an
        island's box, the runway (buffer entry 1) lands it if lined up and slow, else it
        crashes."""
        m = self.mem
        if m.u8(a1) & 1 and m.u8(a1 + 0x1C) >= 4:
            if m.u16(a1 + 0x2E) == 2:
                return self._manta_crash_start(a1, a4)
            return self._manta_normal(a1, a4)
        y = s32(m.u32(a4 + 0x0E))
        if y >= 150:
            return self._manta_fuel(a1, a4)
        if y <= 35:
            if not self._manta_damage(a1, a4, 2000):
                return
            return self._manta_crash_tail(a1, a4)
        buf = m.u32(a4 + 0x3C)
        if not buf:
            return self._manta_fuel(a1, a4)
        dx = s32(m.u32(a4 + 0x0A) - m.u32(buf + 2))
        if s32(self.absl(dx) - m.s16(buf + 0x30)) >= 0:
            return self._manta_fuel(a1, a4)
        dz = s32(m.u32(a4 + 0x12) - m.u32(buf + 0x0A))
        if s32(self.absl(dz) - m.s16(buf + 0x32)) >= 0:
            return self._manta_fuel(a1, a4)
        a5 = buf + 0x52
        if m.u16(a5 + 2) != 0x24:
            return self._manta_crash(a1, a4, 500)
        self.set_matrix(w16(-m.u16(a5 + 6)))
        x, z = self.matrix_rotate(dx - m.u16(a5 + 0x0A), dz - m.u16(a5 + 0x0E))
        if self.absw(x) > 200 or z < -4000 or z > 2000:
            return self._manta_crash(a1, a4, 500)
        if self.absw(m.u16(a1 + 2)) > 120 or self.absw(m.u16(a1 + 4)) > 80:
            return self._manta_crash(a1, a4, 100)
        hd = w16(m.u16(a4 + 6) - m.u16(a5 + 6))
        if hd == 0x8000 or self.absw(hd) > 0xFA0 or m.s16(a1 + 6) > 100:
            return self._manta_crash(a1, a4, 100)
        self._msg(f"Manta {m.u16(a4 + 0x1A) + 1} has landed on runway")
        m.set8(a1, 1)
        m.set8(a1 + 0x1C, 4)
        m.set8(a1 + 0x1D, 0)
        self._viewed_pointer(a4)
        return self._manta_auto(a1, a4)

    def _manta_crash(self, a1: int, a4: int, d: int) -> None:
        m = self.mem
        if not self._manta_damage(a1, a4, d):
            return
        if s32(m.u32(a4 + 0x0E)) < 0x91:
            m.set32(a4 + 0x0E, 0x91)
        self._manta_crash_tail(a1, a4)

    def _manta_crash_tail(self, a1: int, a4: int) -> None:
        """$1D0FE: bounce: nose up, rudder kicked at random."""
        m = self.mem
        m.set16(a1 + 4, self.turn_towards(100, m.u16(a1 + 4), 30))
        kick = w16((self.rand() & 0x7F) - 0x40)
        m.set16(a1 + 2, _clamp255(m.u16(a1 + 2) + kick))
        self._manta_fuel(a1, a4)

    def _manta_fuel(self, a1: int, a4: int) -> None:
        """$1D13C: fuel use on the slow tick (more when badly damaged), then $1D170."""
        m = self.mem
        if m.u8(stunits.SLOW_TICK):
            d0 = ((m.u16(a1 + 6) << 4) & 0xFFFF) // 50
            if m.s16(a1 + 0x14) <= 500:
                d0 += 0x100
            f = s16(m.u16(a1 + 0x0A) - d0)
            m.set16(a1 + 0x0A, f if f >= 0 else 0)
        if m.u8(a1) & 0x10:
            return self._manta_crash_fall(a1, a4)
        if m.u16(a1 + 0x0A):
            if m.u16(a1 + 0x2E) == 2:
                return self._manta_crash_start(a1, a4)
            return self._manta_normal(a1, a4)
        self._msg(f"Manta {m.u16(a4 + 0x1A) + 1} has run out of fuel")
        self._manta_crash_start(a1, a4)

    def _manta_crash_start(self, a1: int, a4: int) -> None:
        """$1D19E: out of fuel or telemetry: fall with the current velocity."""
        m = self.mem
        for o, v in zip((0x20, 0x22, 0x24), self.velocity_from_angles(a4, m.u16(a1 + 6))):
            m.set16(a4 + o, v)
        m.set8(a1, (m.u8(a1) | 0x10) & 0xFE)
        self._manta_crash_fall(a1, a4)

    def _manta_crash_fall(self, a1: int, a4: int) -> None:
        """$1D1B4: gravity on the falling Manta, spinning nose down."""
        m = self.mem
        vx, vy, vz = m.s16(a4 + 0x20), m.s16(a4 + 0x22), m.s16(a4 + 0x24)
        vy -= 5
        m.set16(a4 + 0x22, vy)
        m.set32(a4 + 0x0A, m.u32(a4 + 0x0A) + vx)
        m.set32(a4 + 0x0E, m.u32(a4 + 0x0E) + vy)
        m.set32(a4 + 0x12, m.u32(a4 + 0x12) + vz)
        m.set16(a4 + 8, m.u16(a4 + 8) + (0x7D0 if m.u16(a1 + 2) & 0x8000 else -0x7D0))
        m.set16(a4 + 4, self.turn_towards(0x3E80, m.u16(a4 + 4), 0x258))

    def _manta_normal(self, a1: int, a4: int) -> None:
        """$1D1F4: speed governor, self-centring, attitude from the controls, move."""
        m = self.mem
        self._manta_governor(a1)
        for bit, o in ((0x80, 2), (0x40, 4)):
            if m.u8(a1) & bit:
                if m.u16(a1 + o) == 0:
                    m.set8(a1, m.u8(a1) & ~bit)
                else:
                    m.set16(a1 + o, self.self_centre(m.u16(a1 + o)))
        rudder, pitch = m.s16(a1 + 2), m.s16(a1 + 4)
        m.set16(a4 + 8, rudder * -25)
        m.set16(a4 + 4, pitch * -14)
        m.set16(a4 + 6, m.u16(a4 + 6) + _divs(s16(rudder << 4), 6))
        self._manta_move(a1, a4)

    def _manta_move(self, a1: int, a4: int) -> None:
        """$1D230: twice the speed far from everything (unless on the Manta screen), at most
        200 in the island's range (bit 9), altitude capped at 3500."""
        m = self.mem
        d1 = m.u16(a1 + 6)
        if not m.u32(a4 + 0x3C) and not m.u8(stunits.C_NEAR_UNIT) and m.u16(stunits.SCREEN) != 0x1B:
            d1 = w16(d1 * 2)
        if m.u8(a4) & 2 and s16(d1) > 200:
            d1 = 200
        vx, vy, vz = self.velocity_from_angles(a4, d1)
        m.set32(a4 + 0x0A, m.u32(a4 + 0x0A) + vx)
        y = s32(m.u32(a4 + 0x0E) + vy)
        m.set32(a4 + 0x0E, 0xDAC if y > 0xDAC else y)
        m.set32(a4 + 0x12, m.u32(a4 + 0x12) + vz)

    def _manta_governor(self, a1: int) -> None:
        """$1DB5A: target speed within [50 (0 with the cheat or runway modes), health/16 + 50
        capped at 250]; speed follows it by 4 up, 16 down."""
        m = self.mem
        hi = s16((m.u16(a1 + 0x14) >> 4) + 0x32)
        lo = 0 if m.u8(0x118A) else 0x32
        if hi > 0xFA:
            hi = 0xFA
        d0 = m.s16(a1 + 8)
        if m.u8(a1) & 1 and m.u8(a1 + 0x1C) >= 4:
            lo = 0
        if d0 > hi:
            d0 = hi
        elif d0 < lo:
            d0 = lo
        m.set16(a1 + 8, d0)
        d1 = m.u16(a1 + 6)
        if d1 == w16(d0):
            return
        step = 4 if w16(d1 - d0) & 0x8000 else 0x10
        m.set16(a1 + 6, self.turn_towards(d0, d1, step))

    def _manta_steer(self, a1: int, a4: int, dx: int, dz: int, div: int) -> None:
        """$1D702: rudder towards the bearing (error / div), then $1CF9A."""
        m = self.mem
        e = s16(self.atan2(dx, dz) - m.u16(a4 + 6))
        m.set16(a1 + 2, self.turn_towards(_clamp255(_divs(e, div)), m.u16(a1 + 2), 0x20))
        self._manta_terrain(a1, a4)

    def _manta_hover(self, a1: int, a4: int) -> None:
        """$1D642: no waypoint: circle at cruise 70, climbing when below 500."""
        m = self.mem
        if s32(m.u32(a4 + 0x0E)) > 500:
            self._centre(a1 + 4)
        else:
            m.set16(a1 + 4, self.turn_towards(0x96, m.u16(a1 + 4), 0x14))
        m.set16(a1 + 8, 0x46)
        m.set16(a1 + 2, self.turn_towards(0x80, m.u16(a1 + 2), 0x10))
        self._manta_terrain(a1, a4)

    def _manta_auto(self, a1: int, a4: int) -> None:
        """$1D280."""
        mode = self.mem.u8(a1 + 0x1C)
        if mode <= 6:
            (self._manta_land_carrier, self._manta_on_deck, self._manta_deck_takeoff, self._manta_fly,
             self._manta_runway_landing, self._manta_landed, self._manta_runway_takeoff)[mode](a1, a4)

    def _manta_land_carrier(self, a1: int, a4: int) -> None:
        """Mode 0: approach at altitude 570, match the carrier's motion, line up, settle on
        the lift (local (0, 250)), descend to 180 (mode 1)."""
        m, c = self.mem, CARRIER_OBJ
        self._centre(a4 + 4)
        self._centre(a4 + 8)
        self._manta_governor(a1)
        self._centre(a1 + 4)
        self._centre(a1 + 2)
        sub = m.u8(a1 + 0x1D)
        ch = m.u16(c + 6)
        if sub == 0:
            d1 = s32(0x23A - m.u32(a4 + 0x0E))
            d0 = 0
            if self.absl(d1) >= 0x14:
                d0 = w16(-0x64) if d1 & 0x8000 else 0x64
            m.set16(a1 + 4, self.turn_towards(d0, m.u16(a1 + 4), 0x14))
            dx = s32(m.u32(c + 0x0A) - m.u32(a4 + 0x0A))
            dz = s32(m.u32(c + 0x12) - m.u32(a4 + 0x12))
            d3 = self._max_abs(dx, dz)
            if (d3 & 0xFFFFFFFF) >= 0x258:
                m.set16(a1 + 8, 0xC8 if s32(d3 - 0x258) >= 0xDAC else 0x41)
                return self._manta_steer(a1, a4, dx, dz, 10)
            if m.u16(a1 + 4):
                return self._manta_steer(a1, a4, dx, dz, 0x20)
            m.set16(a4 + 0x1C, -dx)
            m.set16(a4 + 0x1E, -dz)
            self.set_matrix(ch)
            cx, cz = self.matrix_rotate(0, m.u16(stunits.C_SPEED))
            self.set_matrix(m.u16(a4 + 6))
            mx, mz = self.matrix_rotate(0, m.u16(a1 + 6))
            m.set16(a4 + 0x20, mx - cx)
            m.set16(a4 + 0x24, mz - cz)
            m.set8(a1 + 0x1D, 1)
            m.set16(a1 + 8, 0)
            sub = 1
        if sub == 1:
            m.set16(a4 + 0x1C, m.u16(a4 + 0x1C) + self._centre(a4 + 0x20))
            m.set16(a4 + 0x1E, m.u16(a4 + 0x1E) + self._centre(a4 + 0x24))
            m.set32(a4 + 0x0A, m.s16(a4 + 0x1C) + m.u32(c + 0x0A))       # $1DBBE
            m.set32(a4 + 0x12, m.s16(a4 + 0x1E) + m.u32(c + 0x12))
            if w16(m.u16(a4 + 0x20) + m.u16(a4 + 0x24)):
                return
            m.set8(a1 + 0x1D, 2)
            self.set_matrix(w16(-ch))
            x, z = self.matrix_rotate(m.u16(a4 + 0x1C), m.u16(a4 + 0x1E))
            m.set16(a4 + 0x1C, x)
            m.set16(a4 + 0x1E, z)
        elif sub == 2:
            m.set16(a4 + 6, self.turn_towards(ch, m.u16(a4 + 6), 0x200))
            self._deck_pos(a4)
            if m.u16(a4 + 6) == ch:
                m.set8(a1 + 0x1D, 3)
        elif sub == 3:
            if m.u16(a4 + 0x1E) == 0xFA and m.u16(a4 + 0x1C) == 0:
                m.set16(LIFT_BAY, m.u16(a4 + 0x1A))
                self.dock_attach()
                m.set8(a1 + 0x1D, 4)
                return
            m.set16(a4 + 0x1E, self.turn_towards(0xFA, m.u16(a4 + 0x1E), 10))
            m.set16(a4 + 0x1C, self.turn_towards(0, m.u16(a4 + 0x1C), 10))
            m.set16(a4 + 6, ch)
            self._deck_pos(a4)
        elif sub == 4:
            y = s32(m.u32(a4 + 0x0E) - 10)
            if y <= 0xB4:
                y = 0xB4
                m.set16(a4 + 4, 0xFD4A)
                m.set8(a1 + 0x1D, 0)
                m.set8(a1 + 0x1C, 1)
                m.set8(MANTA_LANDING, 0)
            m.set32(a4 + 0x0E, y)
            self._manta_on_deck(a1, a4)

    def _manta_on_deck(self, a1: int, a4: int) -> None:
        """Mode 1: parked on the lift (12 fuel per slow tick for the systems)."""
        m = self.mem
        if m.u8(stunits.SLOW_TICK):
            f = s16(m.u16(a1 + 0x0A) - 0x0C)
            m.set16(a1 + 0x0A, f if f >= 0 else 0)
        m.set16(a4 + 6, m.u16(CARRIER_OBJ + 6))
        self._deck_pos(a4)

    def _manta_deck_takeoff(self, a1: int, a4: int) -> None:
        """Mode 2: rise off the lift, roll forward, climb out over the bow (mode 3)."""
        m = self.mem
        sub = m.u8(a1 + 0x1D)
        if sub > 2:
            return
        if sub == 0:
            y = (m.u32(a4 + 0x0E) + 2) & 0xFFFFFFFF
            m.set32(a4 + 0x0E, y)
            if y >= 0xC8:
                m.set8(a1 + 0x1D, 1)
                m.set16(a1 + 6, 0)
        elif sub == 1:
            v = w16(m.u16(a1 + 6) + 8)
            if v > 0x3C:
                v = 0x3C
            m.set16(a1 + 6, v)
            z = w16(m.u16(a4 + 0x1E) + v)
            m.set16(a4 + 0x1E, z)
            if z >= 0x28A:
                m.set8(a1 + 0x1D, 2)
        else:
            p = s16(m.u16(a4 + 4) - 0xF0)
            m.set16(a4 + 4, p if p > -0x677 else 0xF989)
            m.set32(a4 + 0x0E, m.u32(a4 + 0x0E) + 0x0A)
            z = w16(m.u16(a4 + 0x1E) + m.u16(a1 + 6))
            m.set16(a4 + 0x1E, z)
            if z >= 0x7D0:
                m.set16(a1 + 2, 0)
                m.set16(a1 + 4, _divs(m.s16(a4 + 4), -14))
                m.set16(a1 + 6, m.u16(a1 + 6) + m.u16(stunits.C_SPEED))
                m.set16(a1 + 8, m.u16(a1 + 6))
                m.set8(a1 + 0x1C, 3)
                if m.u8(a1 + 0x33):
                    m.set8(a1, m.u8(a1) & 0xFE)
                m.set8(a4 + 1, m.u8(a4 + 1) & ~4)
                m.set8(LIFT_BAY, 0xFF)
                self.dock_attach()
        m.set16(a4 + 6, m.u16(CARRIER_OBJ + 6))
        self._deck_pos(a4)

    def _manta_fly(self, a1: int, a4: int) -> None:
        """Mode 3: climb or dive to the waypoint altitude and steer to it; slow to 50 within
        2000; on arrival (600) the waypoint is cleared and it circles."""
        m = self.mem
        if not m.u8(a1) & 2:
            return self._manta_hover(a1, a4)
        d1 = s32(m.u32(a1 + 0x22) - m.u32(a4 + 0x0E))
        d0 = 0
        if self.absw(d1) >= 0x14:
            d0 = 0x96 if s16(d1) > 0x96 else w16(-0x96) if s16(d1) < -0x96 else w16(d1)
        m.set16(a1 + 4, self.turn_towards(d0, m.u16(a1 + 4), 10))
        dx = s32(m.u32(a1 + 0x1E) - m.u32(a4 + 0x0A))
        dz = s32(m.u32(a1 + 0x26) - m.u32(a4 + 0x12))
        d3 = self._max_abs(dx, dz)
        m.set16(a1 + 8, m.u16(a1 + 0x2A))
        if d3 <= 0x7D0:
            m.set16(a1 + 8, 0x32)
        if d3 < 0x258:
            m.set8(a1, m.u8(a1) & ~2)
            if m.u8(a1) & 4:
                self._msg(f"Manta {m.u16(a4 + 0x1A) + 1} has reached its destination")
            return self._manta_hover(a1, a4)
        self._manta_steer(a1, a4, dx, dz, 10)

    def _runway(self, a4: int):
        buf = self.mem.u32(a4 + 0x3C)
        if not buf:
            raise NotImplementedError("Manta runway mode without an island buffer")
        return buf, buf + 0x52

    def _runway_target(self, a4: int, a5: int, a6: int, px: int, pz: int):
        """$1DAAE: offset to a point in the runway's frame; returns (dx, dz, |dx.w|, |dz.w|)."""
        m = self.mem
        self.set_matrix(m.u16(a6 + 6))
        x, z = self.matrix_rotate(px, pz)
        dx = s32(s16(x + m.u16(a6 + 0x0A)) + m.u32(a5 + 2) - m.u32(a4 + 0x0A))
        dz = s32(s16(z + m.u16(a6 + 0x0E)) + m.u32(a5 + 0x0A) - m.u32(a4 + 0x12))
        return dx, dz, self.absw(dx), self.absw(dz)

    def _manta_runway_end(self, a1: int, a4: int, dx: int, dz: int) -> None:
        """$1DAEC: head for the point, then $1DB02."""
        m = self.mem
        m.set16(a4 + 6, self.turn_towards(self.atan2(dx, dz), m.u16(a4 + 6), 0x200))
        self._manta_governor(a1)
        self._manta_move(a1, a4)

    def _manta_runway_landing(self, a1: int, a4: int) -> None:
        """Mode 4: roll to the runway end at 120, taxi to the bay's parking spot, turn round,
        settle to 100 (mode 5)."""
        m = self.mem
        sub = m.u8(a1 + 0x1D)
        if sub > 3:
            return
        a5, a6 = self._runway(a4)
        if sub == 0:
            m.set16(a1 + 8, 0x40)
            for o in (a1 + 4, a1 + 2, a4 + 8, a4 + 4):
                self._centre(o)
            m.set32(a4 + 0x0E, s16(self.turn_towards(0x78, m.u16(a4 + 0x10), 8)))
            dx, dz, d2, d3 = self._runway_target(a4, a5, a6, 0, 0xA5A)
            if d2 <= 0x96 and d3 <= 0x96:
                m.set8(a1 + 0x1D, 1)
                m.set32(a4 + 0x0E, 0x78)
                for o in (a1 + 2, a1 + 4, a4 + 4, a4 + 8):
                    m.set16(o, 0)
                dx = s32(dx & 0xFFFF0000)        # clr.w d0 (for the zero stores) clips the bearing
            return self._manta_runway_end(a1, a4, dx, dz)
        if sub == 1:
            m.set16(a1 + 8, 8)
            bay = m.u16(a4 + 0x1A)
            px = w16(bay * 0x258 - 0x384)
            pz = 0xCC6 if bay in (0, 3) else 0xE88
            dx, dz, d2, d3 = self._runway_target(a4, a5, a6, px, pz)
            if d2 <= 0x14 and d3 <= 0x14:
                m.set8(a1 + 0x1D, 2)
                m.set16(a1 + 8, 0)
                m.set16(a1 + 6, 0)
            return self._manta_runway_end(a1, a4, dx, dz)
        if sub == 2:
            self._centre(a1 + 2)
            h = m.u16(a4 + 6)
            t = w16(m.u16(a6 + 6) + 0x8000)
            if h == t:
                m.set8(a1 + 0x1D, 3)
            m.set16(a4 + 6, self.turn_towards(t, h, 0x400))
            return self._manta_move(a1, a4)
        y = s32(m.u32(a4 + 0x0E) - 1)
        if y <= 0x64:
            y = 0x64
            m.set16(a4 + 4, 0xFD4A)
            m.set8(a1 + 0x1C, 5)
        m.set32(a4 + 0x0E, y)

    def _manta_landed(self, a1: int, a4: int) -> None:
        """Mode 5: parked on the runway, refuelling; a take-off order (bit 5) starts mode 6
        if the runway is intact."""
        m = self.mem
        if m.u16(a1 + 0x2E) == 2:
            return self._manta_kill(a1, a4)
        f = w16(m.u16(a1 + 0x0A) + 0x40)
        m.set16(a1 + 0x0A, 0x7FFF if f & 0x8000 else f)
        if not m.u8(a1) & 0x20:
            return
        m.set8(a1, m.u8(a1) & ~0x20)
        _, a6 = self._runway(a4)
        if m.u16(a6 + 2) == 0x24:
            m.set8(a1 + 0x1C, 6)
            m.set8(a1 + 0x1D, 0)
            m.set8(a1 + 0x33, 0)
            self._msg(f"Manta {m.u16(a4 + 0x1A) + 1} is taking off from runway")
        else:
            self._msg("Runway damaged - cannot take off")

    def _manta_runway_takeoff(self, a1: int, a4: int) -> None:
        """Mode 6: taxi to the runway end, then accelerate down it and climb out (mode 3),
        with a waypoint 6000 beyond the runway unless one is set."""
        m = self.mem
        a5, a6 = self._runway(a4)
        if m.u8(a1 + 0x1D) == 0:
            m.set16(a1 + 8, 0x0C)
            self._centre(a4 + 4)
            m.set32(a4 + 0x0E, s16(self.turn_towards(0x78, m.u16(a4 + 0x10), 1)))
            dx, dz, d2, d3 = self._runway_target(a4, a5, a6, 0, 0xA8C)
            if d2 <= 0x64 and d3 <= 0x64:
                m.set8(a1 + 0x1D, 1)
            return self._manta_runway_end(a1, a4, dx, dz)
        m.set32(a4 + 0x0E, s16(self.turn_towards(0x9B, m.u16(a4 + 0x10), 2)))
        dx, dz, d2, d3 = self._runway_target(a4, a5, a6, 0, 0xF448)
        if d2 <= 0x96 and d3 <= 0x96:
            m.set8(a1 + 0x1C, 3)
            if m.u8(a1 + 0x33):
                m.set8(a1, m.u8(a1) & 0xFE)
            elif not m.u8(a1) & 2:
                m.set8(a1, (m.u8(a1) | 2) & ~4)
                m.set16(a1 + 0x2A, 0x3C)
                m.set32(a1 + 0x22, s16(w16((m.u16(a4 + 0x1A) + 1) * 0x12C + 0x1F4)))
                self.set_matrix(m.u16(a6 + 6))
                x, z = self.matrix_rotate(0, 0x1770)
                m.set32(a1 + 0x1E, s16(x + m.u16(a6 + 0x0A)) + m.u32(a5 + 2))
                m.set32(a1 + 0x26, s16(z + m.u16(a6 + 0x0E)) + m.u32(a5 + 0x0A))
        else:
            d4 = self.atan2(dx, dz)
            h = self.turn_towards(d4, m.u16(a4 + 6), 0x200)
            m.set16(a4 + 6, h)
            if h == d4:
                m.set16(a1 + 8, 0xC8)
        self._manta_governor(a1)
        self._manta_move(a1, a4)

    # ---- Manta weapons ($1F9E8) ------------------------------------------------------------
    def manta_fire(self, a1: int, a4: int) -> None:
        """$1F9E8: weapon 0 laser, 1 missile, else the payload (a bomb)."""
        m = self.mem
        w = m.u16(a1 + 0x1A)
        if w == 0:
            return self._manta_laser(a1, a4)
        if w == 1:
            if not m.u16(a1 + 0x0C):
                return self._msg("No missile to launch")
            if not m.u16(0x940):
                self._manta_missile(a1, a4)
            return
        if m.u16(a1 + 0x0E) != 4:
            return self._msg("No cargo to drop")
        m.set16(a1 + 0x0E, 0)
        m.set16(a1 + 0x12, m.u16(a1 + 0x12) - 0x258)
        m.set16(0xA80, m.u16(a1 + 6))
        b = self._new_object(0x14)
        if b is None:
            return
        for o in (0x0A, 0x0E, 0x12):
            m.set32(b + o, m.u32(a4 + o))
        m.set16(b + 6, m.u16(a4 + 6))
        m.set16(b + 4, 0x4000)
        speed = m.s16(0xA80)
        for o, v in zip((0x20, 0x22, 0x24), self.velocity_from_angles(a4, 0x96 if speed > 0x96 else speed)):
            m.set16(b + o, v)
        self._msg("Bomb dropped")

    def _manta_laser(self, a1: int, a4: int) -> None:
        """$1FA9C: a type-16 bolt from 50 below the nose, cooldown 4."""
        m = self.mem
        if not m.u8(a1 + 0x10):
            return self._msg("No laser to fire")
        if m.u16(0x940):
            return
        m.set16(0x940, 4)
        b = self._new_object(0x10)
        if b is None:
            return
        self._place(b, a4, *self._offset_from(a4, 0, -0x32))
        self._copy_angles(a4, b)
        m.set16(b + 0x34, 2)
        m.set8(b + 0x1B, 0x14)

    def _manta_missile(self, a1: int, a4: int) -> None:
        """$1FFEA: the second press fires at the locked target ($1168) from alternating wings
        (the first press scans the 3D view, $6290, which isn't modelled)."""
        m = self.mem
        if m.u16(LOCK_TIMER) == 0:
            raise NotImplementedError("Manta missile lock-on scan ($6290, the 3D view)")
        m.set16(LOCK_TIMER, 0)
        b = self._new_object(0x11)
        if b is None:
            return
        wing = (~m.u8(a1 + 0x2C)) & 0xFF
        m.set8(a1 + 0x2C, wing)
        self._place(b, a4, *self._offset_from(a4, 0x12C if wing == 0 else -0x12C, 0))
        self._copy_angles(a4, b)
        m.set16(b + 8, 0)
        m.set32(b + 0x1E, m.u32(LOCK_TARGET))
        m.set16(0x940, 4 if m.s16(a1 + 6) < 0x78 else 0x0A)
        m.set16(a1 + 0x0C, m.u16(a1 + 0x0C) - 1)
        m.set16(a1 + 0x12, m.u16(a1 + 0x12) - 0x37)

    # ---- Walrus -------------------------------------------------------------------------------
    def _walrus_step(self, a1: int, a4: int) -> None:
        m = self.mem
        if m.s8(a1 + 1) < 3 or not m.u8(a4 + 1) & 1:
            return
        if m.u16(a1 + 0x30):
            m.set16(a1 + 0x30, m.u16(a1 + 0x30) - 1)
        d5 = m.u16(a1 + 0x2C)
        m.set16(a1 + 0x2C, 0)
        if not self._telemetry(a1, a4, d5, 0x2C, "Walrus"):
            return
        if m.u8(a1) & 1:
            return self._walrus_auto(a1, a4)
        if a4 == m.u32(VIEWED):
            if m.u8(a1) & 0x10:
                a0 = m.u32(a4 + 0x20)
                if not stunits.OBJECTS <= a0 < stunits.OBJECTS + 64 * stunits.OBJECT_SIZE:
                    raise NotImplementedError(f"Walrus guided missile pointer ${a0:X}")
                if not m.u8(a0 + 1) & 1 or w16(m.u16(a0 + 0x1A) - 4) != m.u16(a4 + 0x1A):
                    m.set8(a1, m.u8(a1) & ~0x10)
                    self._walrus_fire_check(a1, a4)
                elif not m.u8(POINTER_MODE):
                    m.set8(FIRE_KEYS, m.u8(FIRE_KEYS) & 0xFE)
                    m.set8(a1, m.u8(a1) & 0x3F)
                    m.set16(a0 + 0x1E, _clamp255(m.u16(a0 + 0x1E) + m.u16(MOUSE_DX)))
                    m.set16(MOUSE_DX, 0)
                    m.set16(a0 + 0x20, _clamp255(m.u16(a0 + 0x20) + m.u16(MOUSE_DY)))
                    m.set16(MOUSE_DY, 0)
                    t = m.u16(a1 + 0x2E)
                    if t >= 0x64:
                        m.set16(a1 + 0x2E, t - 8)
            elif not m.u8(POINTER_MODE):
                m.set8(a1, m.u8(a1) & 0x3F)
                m.set16(a1 + 2, _clamp255(m.u16(a1 + 2) + m.u16(MOUSE_DX)))
                m.set16(MOUSE_DX, 0)
                m.set16(MOUSE_DY, 0)
                self._walrus_fire_check(a1, a4)
            d0 = m.u16(a1 + 4)
            if m.u8(KEY_UP):
                m.set16(a1 + 6, d0 + 8)
            elif m.u8(KEY_DOWN):
                m.set16(a1 + 6, d0 - 8)
        self._walrus_shore(a1, a4)

    def _walrus_fire_check(self, a1: int, a4: int) -> None:
        """$1DF12."""
        m = self.mem
        keys = m.u8(FIRE_KEYS)
        m.set8(FIRE_KEYS, keys & 0xFE)
        if keys & 1 and not m.u8(FIRE_INHIBIT):
            self.walrus_fire(a1, a4)

    def _walrus_shore(self, a1: int, a4: int) -> None:
        """$1DF46: within 650 of the island box edge the beach ramp sets height and pitch."""
        m = self.mem
        buf = m.u32(a4 + 0x3C)
        if buf:
            dx = s32(m.u32(a4 + 0x0A) - m.u32(buf + 2))
            dz = s32(m.u32(a4 + 0x12) - m.u32(buf + 0x0A))
            d2 = s32(self.absl(dx) - m.s16(buf + 0x30))
            d3 = s32(self.absl(dz) - m.s16(buf + 0x32))
            if d2 <= 0x28A and d3 <= 0x28A and (d2 > -0x28A or d3 > -0x28A):
                return self._walrus_ramp(a1, a4, dx, dz, d2, d3)
        self._walrus_off_ramp(a1, a4)

    def _walrus_off_ramp(self, a1: int, a4: int) -> None:
        """$1DF7C."""
        self.mem.set8(a1, self.mem.u8(a1) & ~8)
        self._walrus_drive(a1, a4)

    def _ramp_height(self, a4: int, d5: int) -> None:
        """$1E5BA: past 130 into the ramp the height drops from 15 (1 per 10, at most 30)."""
        d0 = w16(d5 - 0x82)
        if d0 & 0x8000:
            return
        d0 = min(s16(d0), 0x12C)
        self.mem.set32(a4 + 0x0E, s16(0x0F - d0 // 10))

    def _walrus_ramp(self, a1: int, a4: int, dx: int, dz: int, d2: int, d3: int) -> None:
        """$1E4D0: the nearer box edge gives the ramp direction; facing up it (within $4000)
        or backing down it, the heading snaps towards it and height/pitch come from a table
        indexed by the depth into the ramp."""
        m = self.mem
        if s16(d3) > s16(d2):
            d2 = d3
            d0 = 0x8000 if not dz & 0x8000 else 0
        else:
            d0 = 0xC000 if not dx & 0x8000 else 0x4000
        d5 = w16(d2)
        h = m.u16(a4 + 6)
        diff = w16(h - d0)
        facing = diff != 0x8000 and self.absw(diff) <= 0x4000
        if facing:
            nh = self.turn_towards(d0, h, 0x400)
            self._ramp_height(a4, d5)
            q = s16(_divs(s16(-s16(d5)), 10))
            if q >= 0x10:
                return self._walrus_off_ramp(a1, a4)
            m.set16(a4 + 6, nh)
            idx, table = w16(q + 6), RAMP_FORWARD
        else:
            nh = self.turn_towards(w16(d0 + 0x8000), h, 0x400)
            self._ramp_height(a4, d5)
            q = s16(_divs(s16(d5), 10))
            if q >= 7:
                return self._walrus_off_ramp(a1, a4)
            m.set16(a4 + 6, nh)
            idx, table = w16(q + 8), RAMP_REVERSE
        if not idx & 0x8000:
            e = table + s16(w16(idx * 4))
            m.set32(a4 + 0x0E, m.s16(e))
            m.set16(a4 + 4, m.u16(e + 2))
        if not m.u8(a1) & 8:
            self._msg(f"Walrus {m.u16(a4 + 0x1A) + 1} has encountered a beach")
        m.set8(a1, (m.u8(a1) | 0xC8) & 0xEF)
        self._viewed_pointer(a4)
        self._walrus_drive(a1, a4)

    def _walrus_drive(self, a1: int, a4: int) -> None:
        """$1DF80: wake roll, fuel, speed governor, self-centring, rudder, move."""
        m = self.mem
        d0 = w16(m.u32(a4 + 0x0E) - 0x0F)
        d1 = 0x200
        if d0 & 0x8000:
            d1 = w16((s16(d0) * 0x1111 >> 8) + 0x200)
        m.set16(a4 + 0x24, d1)
        if m.u8(stunits.SLOW_TICK):
            sp = m.s16(a1 + 4)
            d0 = ((max(sp, 0) << 4) & 0xFFFF) // 0x50
            if m.s16(a1 + 0x14) <= 500:
                d0 += 0x80
            f = m.u16(a1 + 8)
            if f:
                if f > w16(d0):
                    m.set16(a1 + 8, f - d0)
                else:
                    self._msg(f"Walrus {m.u16(a4 + 0x1A) + 1} has run out of fuel")
                    m.set16(a1 + 8, 0)
        self._walrus_governor(a1, a4)
        if m.u8(a1) & 0x80:
            if m.u16(a1 + 2) == 0:
                m.set8(a1, m.u8(a1) & ~0x80)
            else:
                m.set16(a1 + 2, self.self_centre(m.u16(a1 + 2)))
        if m.u8(a1) & 0x40:
            t = m.u16(a4 + 0x26)
            if t == 0:
                m.set8(a1, m.u8(a1) & ~0x40)
            else:
                m.set16(a4 + 0x26, self.turn_towards(0, t, 0x400))
        turn = _divs(s16(m.u16(a1 + 2) << 4), 6)
        o = a4 + 0x26 if m.u8(a1) & 0x20 else a4 + 6
        m.set16(o, m.u16(o) + turn)
        vx, vz = self.yaw_vector(a4, m.u16(a1 + 4))
        m.set32(a4 + 0x0A, m.u32(a4 + 0x0A) + vx)
        m.set32(a4 + 0x12, m.u32(a4 + 0x12) + vz)

    def _walrus_governor(self, a1: int, a4: int) -> None:
        """$1E674: target speed 0..health/32 (at most 96, 48 when below sea level), 10 on a
        beach, 0 without fuel; speed follows by 2 up, 8 down."""
        m = self.mem
        if m.u16(a1 + 8) == 0:
            d0 = 0
            m.set16(a1 + 6, 0)
        elif m.u8(a1) & 8:
            d0 = 0x0A
        else:
            d1 = m.u16(a1 + 0x14) >> 5
            if d1 > 0x60:
                d1 = 0x60
            if s32(m.u32(a4 + 0x0E)) < 0 and d1 > 0x30:
                d1 = 0x30
            d0 = m.s16(a1 + 6)
            d0 = d1 if d0 > d1 else 0 if d0 < 0 else d0
            m.set16(a1 + 6, d0)
        d1 = m.u16(a1 + 4)
        if d1 == w16(d0):
            return
        step = 2 if w16(d1 - d0) & 0x8000 else 8
        m.set16(a1 + 4, self.turn_towards(d0, d1, step))

    def _walrus_steer(self, a1: int, a4: int, dx: int, dz: int, stop: bool = True) -> int:
        """Rudder towards (dx, dz): error/32, clamped; target speed 0 if more than $400 off
        (when stop). Returns the error."""
        m = self.mem
        e = w16(self.atan2(dx, dz) - m.u16(a4 + 6))
        if stop and self.absw(e) > 0x400:
            m.set16(a1 + 6, 0)
        m.set16(a1 + 2, self.turn_towards(_clamp255(s16(e) >> 5), m.u16(a1 + 2), 0x20))
        return e

    def _walrus_auto(self, a1: int, a4: int) -> None:
        """$1E060."""
        mode = self.mem.u8(a1 + 0x1A)
        if mode == 0:
            return self._walrus_dock(a1, a4)
        if mode == 1:
            return
        if mode == 2:
            return self._walrus_launch(a1, a4)
        if mode == 3:
            return self._walrus_waypoint(a1, a4)
        raise NotImplementedError(f"Walrus auto mode {mode} (the original corrupts its stack)")

    def _walrus_dock(self, a1: int, a4: int) -> None:
        """Mode 0: steer to the stern ramp (local (0, -1500)), enter, door open, drive in to
        -650, door closes; when shut the Walrus is stowed and its weapons go back to stock."""
        m, c = self.mem, CARRIER_OBJ
        sub = m.u8(a1 + 0x1B)
        ch = m.u16(c + 6)
        if sub > 3:
            return
        if sub == 0:
            self.set_matrix(ch)
            x, z = self.matrix_rotate(0, 0xFA24)
            dx = s32(x + m.u32(c + 0x0A) - m.u32(a4 + 0x0A))
            dz = s32(z + m.u32(c + 0x12) - m.u32(a4 + 0x12))
            d3 = self._max_abs(dx, dz)
            if (d3 & 0xFFFFFFFF) > 0x4000:
                m.set8(WALRUS_DOCKING, 0)
                m.set8(a1, m.u8(a1) & 0xFC)
                m.set16(a1 + 6, 0)
                self._msg(f"Walrus {m.u16(0x6E0) + 1} - Docking aborted")
                return
            m.set16(a1 + 6, 0x18 if d3 <= 0x3E8 else 0x3C)
            if d3 < 0xC8:
                self.set_matrix(w16(-ch))
                x, z = self.matrix_rotate(m.u32(a4 + 0x0A) - m.u32(c + 0x0A),
                                          m.u32(a4 + 0x12) - m.u32(c + 0x12))
                m.set16(a4 + 0x1C, x)
                m.set16(a4 + 0x1E, z)
                m.set8(a1 + 0x1B, sub + 1)
                m.set16(stunits.DOCKED_WALRUS, m.u16(a4 + 0x1A))
                self.dock_attach()
                return
            self._walrus_steer(a1, a4, dx, dz)
            return self._walrus_shore(a1, a4)
        if sub == 1:
            m.set16(a4 + 6, self.turn_towards(ch, m.u16(a4 + 6), 0x200))
            self._deck_pos(a4)
            if m.u16(a4 + 6) == ch:
                m.set8(a1 + 0x1B, sub + 1)
                self._door(1)
            return
        if sub == 2:
            z = w16(m.u16(a4 + 0x1E) + 0x20)
            m.set16(a4 + 0x1E, z)
            if s16(z) >= -0x28A:
                self._door(3)
                m.set8(a1 + 0x1B, sub + 1)
            return self._deck_pos(a4)
        if m.u16(stunits.DOOR_POS) != 0xD000:
            return self._deck_pos(a4)
        m.set8(a4 + 1, m.u8(a4 + 1) & 0xFE)
        m.set8(WALRUS_DOCKING, 0)
        m.set16(0xB98, m.u16(0xB98) + m.u16(a1 + 0x0C))
        m.set16(a1 + 0x0C, 0)
        if not m.u16(a1 + 0x0A) & 0x8000:
            m.set16(0xC00, m.u16(0xC00) + 1)
        code = m.s16(a1 + 0x0E)
        if code > 0:
            if code > 11:
                raise NotImplementedError(f"Walrus payload code {code}")
            stock = m.u32(LOADOUT + 26 * code + 2)
            m.set16(stock, m.u16(stock) + 1)
        for o in (0x26, 0x28, 0x12):
            m.set16(a1 + o, 0)
        m.set8(a1 + 1, 0)
        m.set8(stunits.DOCKED_WALRUS, 0xFF)
        self.dock_attach()

    def _walrus_launch(self, a1: int, a4: int) -> None:
        """Mode 2: back out down the stern ramp (door closes at -1500), turn to the waypoint,
        then drive (mode 3)."""
        m, c = self.mem, CARRIER_OBJ
        sub = m.u8(a1 + 0x1B)
        if sub > 2:
            return
        if sub == 0:
            v = w16(m.u16(a1 + 4) + 8)
            if v > 0x3C:
                v = 0x3C
            m.set16(a1 + 4, v)
            z = w16(m.u16(a4 + 0x1E) - v)
            m.set16(a4 + 0x1E, z)
            if s16(z) <= -0x5DC:
                m.set8(a1 + 0x1B, 1)
                self._door(3)
            m.set16(a4 + 6, m.u16(c + 6) + 0x8000)
            return self._deck_pos(a4)
        if sub == 1:
            dx = s32(m.u32(a1 + 0x1C) - m.u32(a4 + 0x0A))
            dz = s32(m.u32(a1 + 0x20) - m.u32(a4 + 0x12))
            e = w16(self.atan2(dx, dz) - m.u16(a4 + 6))
            if self.absw(e) <= 0x400:
                m.set8(a1 + 0x1B, 2)
            m.set16(a1 + 2, self.turn_towards(_clamp255(s16(e) >> 5), m.u16(a1 + 2), 0x20))
            m.set16(a1 + 6, 0)
            self._deck_pos(a4)
            return self._walrus_shore(a1, a4)
        m.set16(a1 + 4, m.u16(a1 + 4) + m.u16(stunits.C_SPEED))
        m.set16(a1 + 6, m.u16(a1 + 4))
        m.set8(a4 + 1, m.u8(a4 + 1) & ~4)
        m.set8(a1 + 0x1A, 3)
        m.set8(a1 + 0x1B, 0)
        m.set8(stunits.DOCKED_WALRUS, 0xFF)
        m.set8(WALRUS_DOCKING, 0)
        self.dock_attach()
        if m.u8(a1 + 0x33):
            m.set8(a1, m.u8(a1) & 0xFE)

    def _walrus_waypoint(self, a1: int, a4: int) -> None:
        """Mode 3: drive to the waypoint at cruise speed (32 within 1000); stop at 250."""
        m = self.mem
        if m.u8(a1) & 2:
            dx = s32(m.u32(a1 + 0x1C) - m.u32(a4 + 0x0A))
            dz = s32(m.u32(a1 + 0x20) - m.u32(a4 + 0x12))
            d3 = self._max_abs(dx, dz)
            d4 = m.s16(a1 + 0x24)
            if d3 <= 0x3E8 and d4 > 0x20:
                d4 = 0x20
            m.set16(a1 + 6, d4)
            if d3 >= 0xFA:
                self._walrus_steer(a1, a4, dx, dz)
                return self._walrus_shore(a1, a4)
            m.set8(a1, m.u8(a1) & 0xFC)
            if m.u8(a1) & 4:
                self._msg(f"Walrus {m.u16(a4 + 0x1A) + 1} has reached its destination")
        m.set8(a1, m.u8(a1) | 0x80)
        m.set16(a1 + 6, 0)
        self._walrus_shore(a1, a4)

    # ---- Walrus weapons ($1FB6E) ---------------------------------------------------------
    def walrus_fire(self, a1: int, a4: int) -> None:
        """$1FB6E: weapon 0 laser (Avatar), 1 guided missile (Harbinger), else the payload:
        8 drop, 9 ACCB, $B fuel pod."""
        m = self.mem
        w = m.u16(a1 + 0x18)
        if w == 0:
            return self._walrus_laser(a1, a4)
        if w == 1:
            return self._walrus_missile(a1, a4)
        code = m.u16(a1 + 0x0E)
        if code == 8:
            return self._walrus_drop(a1, a4)
        if code == 9:
            return self._walrus_accb(a1, a4)
        if code == 0x0B:
            return self._walrus_refuel(a1, a4)
        self._msg("No cargo to drop")

    def _turret_offset(self, a4: int):
        """$A438 with the turret heading, then (0, 100) turned: the muzzle 100 ahead."""
        m = self.mem
        h = w16(m.u16(a4 + 6) + m.u16(a4 + 0x26))
        self.set_matrix(h)
        x, z = self.matrix_rotate(0, 0x64)
        return h, x, z

    def _walrus_laser(self, a1: int, a4: int) -> None:
        m = self.mem
        if m.s16(a1 + 0x0A) <= 0:
            return self._msg("No laser to fire")
        if m.u16(0x942):
            return
        m.set16(0x942, 8)
        b = self._new_object(0x10)
        if b is None:
            return
        _, x, z = self._turret_offset(a4)
        self._place(b, a4, x, 0x64, z)
        self._copy_angles(a4, b)
        m.set8(b + 0x1B, 0x14)
        m.set16(a1 + 0x0A, m.u16(a1 + 0x0A) - 1)

    def _walrus_missile(self, a1: int, a4: int) -> None:
        """$1FEEA: a guided missile (type 18) the player steers ($1DE74); its +$1A is the
        Walrus bay + 4."""
        m = self.mem
        if not m.u16(a1 + 0x0C):
            return self._msg("No missile to launch")
        if m.u16(0x942):
            return
        m.set16(0x942, 0x0F)
        b = self._new_object(0x12)
        if b is None:
            raise NotImplementedError("Walrus missile with no free object (the original returns through the saved a1)")
        self._copy_angles(a4, b)
        h, x, z = self._turret_offset(a4)
        m.set16(b + 6, h)
        self._place(b, a4, x, 0x64, z)
        m.set8(b + 0x1C, 0x78)
        m.set16(b + 0x1A, m.u16(a4 + 0x1A) + 4)
        m.set32(a4 + 0x20, b)
        m.set16(a1 + 0x0C, m.u16(a1 + 0x0C) - 1)
        m.set16(a1 + 0x12, m.u16(a1 + 0x12) - 0x96)
        m.set16(a1, m.u16(a1) & 0x3F)                   # andi.w: clears the whole flag byte
        m.set8(a1, m.u8(a1) | 0x10)
        m.set16(a1 + 0x2E, 0xA3)

    def _walrus_drop(self, a1: int, a4: int) -> None:
        """$1FBA4: cargo 8 dropped (type 21) along the turret."""
        m = self.mem
        m.set16(a1 + 0x0E, 0)
        m.set16(a1 + 0x12, m.u16(a1 + 0x12) - 0xC8)
        b = self._new_object(0x15)
        if b is None:
            return
        for o in (0x0A, 0x0E, 0x12):
            m.set32(b + o, m.u32(a4 + o))
        m.set32(b + 0x0E, m.u32(b + 0x0E) + 0x28)
        m.set16(b + 0x20, m.u16(a4 + 6) + m.u16(a4 + 0x26))
        m.set16(b + 0x1E, 0x32)

    def _walrus_accb(self, a1: int, a4: int) -> None:
        """$1FC10: an automatic command-centre builder: on a neutral island, 2000 inside its
        box, at a clear spot 1000 behind the Walrus: slot type $34 (code = +$10 + 1)."""
        m = self.mem
        buf = m.u32(a4 + 0x3C)
        fail = "Not a suitable place for an ACCB"
        if not buf:
            return self._msg(fail)
        d0, d1 = self.island_box_distance(a4, buf)
        if s32(d0 + 0x7D0) >= 0 or s32(d1 + 0x7D0) >= 0:
            return self._msg(fail)
        vx, vz = self.yaw_vector(a4, w16(-0x3E8))
        x = w16(vx + m.u32(a4 + 0x0A) - m.u32(buf + 2))
        z = w16(vz + m.u32(a4 + 0x12) - m.u32(buf + 0x0A))
        code = m.u16(a1 + 0x10)
        i = m.u8(buf + 1)
        if m.u8(stsim.OWNERS + i):
            return self._msg(fail)
        isl_a = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i            # $18940
        m.set32(stsim.G_ISLAND_PTR, isl_a)
        m.set32(stsim.G_ECONOMY_PTR, stsim.ECONOMY + stsim.ECONOMY_SIZE * i)
        m.set32(stsim.G_BUFFER_PTR, stsim.OBJ_BUFFERS[1 if m.u8(isl_a + 1) else 0])
        j = self._spot_at(0x29, i, x, z)
        if j is None:
            return self._msg(fail)
        s = isl_a + stsim.SLOT_BASE + stsim.SLOT_SIZE * j
        m.set16(s, stsim.T_COMMAND_CENTRE_CONSTRUCTION)
        m.set16(s + 8, x)
        m.set16(s + 0x0A, z)
        m.set16(s + 4, 0)
        m.set16(s + 6, code + 1)
        self._slot_create_at(isl_a, s, buf, j)
        self._msg("ACCB dropped successfully")
        self.difficulty_add(0x400)
        self.score(0x9C4)
        m.set16(a1 + 0x0E, 0)

    def _spot_at(self, new_type: int, i: int, x: int, z: int):
        """$18A28: find_free_island_spot for one given position (no random tries)."""
        m = self.mem
        isl = self.island(i)
        r_new = m.u16(stsim.RADIUS + 2 * new_type)
        empty = free_tree = None
        cut = []
        for j, s in enumerate(isl.slots):
            if s.type == 0:
                empty = j
                continue
            rad = m.u16(stsim.RADIUS + 2 * s.type)
            if rad == 0:
                continue
            dist = max(self.absw(s.x - x), self.absw(s.z - z))
            is_tree = stsim.T_TREE_FIRST <= s.type <= stsim.T_TREE_LAST
            if s16(rad + r_new) > dist:
                if not is_tree:
                    return None
                cut.append(j)
            elif is_tree:
                free_tree = j
        j = empty if empty is not None else free_tree
        if j is None:
            return None
        for n, k in enumerate(cut):
            m.set16(stsim.SPOT_CUT_LIST + 2 * n, k)
            isl.slots[k].type = 0
            self.tree_cut(isl, k)
        return j

    def _slot_create_at(self, isl_a: int, s: int, buf: int, j: int) -> None:
        """$182A4 with explicit island entry, slot and 3D buffer."""
        m = self.mem
        if not m.u8(isl_a):
            return
        m.set16(stunits.NEW_INDEX, j)
        e = buf + 0x12 + 0x40 * j
        t = m.u16(s)
        self.init_island_object(e, t)
        self.reset_island_schedule()
        m.set16(e + 0x38, m.u16(s + 4))
        m.set16(e + 0x0A, m.u16(s + 8))
        m.set16(e + 0x0E, m.u16(s + 0x0A))
        m.set16(e + 0x0C, w16(m.u32(stunits.SLOT_ENTRY + 8 * t + 4) + 0x50))

    def _walrus_refuel(self, a1: int, a4: int) -> None:
        """$1FD22: the fuel pod fills the nearest other Walrus within 500, if the two face
        each other (both within $1770)."""
        m = self.mem
        best, near = 0xFFFFFFFF, None
        x, z = m.u32(a4 + 0x0A), m.u32(a4 + 0x12)
        for k in range(4):
            a5 = self.obj(WALRUS_OBJ + k)
            if not m.u8(a5 + 1) & 1 or a5 == a4:
                continue
            d = self._max_abs(x - m.u32(a5 + 0x0A), z - m.u32(a5 + 0x12)) & 0xFFFFFFFF
            if d <= best:
                best, near = d, a5
        if s32(best) < 0 or s32(best) > 0x1F4:
            return self._msg("No walrus in refuelling range")
        h = self.atan2(m.u32(near + 0x0A) - x, m.u32(near + 0x12) - z)
        if self.absw(h - m.u16(a4 + 6)) > 0x1770 or self.absw(h + 0x8000 - m.u16(near + 6)) > 0x1770:
            return self._msg("Walruses must be facing to refuel")
        m.set16(a1 + 0x0E, 0)
        m.set16(a1 + 0x12, m.u16(a1 + 0x12) - 0x1C2)
        m.set16(WALRUS_RECS + s16(w16(m.u16(near + 0x1A) * REC)) + 8, 0x7FFF)
        self._msg("Refuelling complete")

    # ---- lift and loadouts ------------------------------------------------------------------
    def manta_lift_step(self) -> None:
        """$1EAA0: 1 lowering, 2 at the hangar (stow the Manta on it, or bring up [$DC4]),
        3 raising; the lift object follows the platform."""
        m = self.mem
        state = m.u16(stunits.LIFT_STATE)
        if state == 0:
            return
        if state > 3:
            raise NotImplementedError(f"lift state {state}")
        if state == 1:
            p = w16(m.u16(LIFT_POS) - 0x668)
            if s16(p) <= -0x4000:
                m.set16(stunits.LIFT_STATE, state + 1)
                p = 0xC000
            m.set16(LIFT_POS, p)
            if m.s16(LIFT_BAY) >= 0:
                self._lift_object_height(m.u32(0x6D6))
            return
        if state == 3:
            p = w16(m.u16(LIFT_POS) + 0x668)
            if not p & 0x8000:
                p = 0
                m.set16(stunits.LIFT_STATE, 0)
            m.set16(LIFT_POS, p)
            if m.s16(LIFT_BAY) >= 0:
                self._lift_object_height(m.u32(0x6D6))
            if m.u16(stunits.LIFT_STATE):
                return
            bay = m.s16(LIFT_BAY)
            if bay >= 0:
                m.set8(MANTA_RECS + REC * bay + 1, 8)
                self._msg(f"Manta {bay + 1} is now on the flight deck")
            self.dock_attach()
            return
        bay = m.s16(LIFT_BAY)
        if bay >= 0:
            a1 = MANTA_RECS + REC * bay
            lift = m.u32(0x6D6)
            if not lift:
                raise NotImplementedError("lift stow with no lift object")
            m.set16(lift, 0)
            m.set16(0xB7E, m.u16(0xB7E) + m.u16(a1 + 0x0C))
            if m.u16(a1 + 0x0E) == 4:
                m.set16(0xBCC, m.u16(0xBCC) + 1)
            if m.u16(a1 + 0x0E) == 0x0A:
                m.set16(0xC34, m.u16(0xC34) + 1)
                m.set32(COMMS_POD, 0)
            if m.u8(a1 + 0x10):
                m.set16(0xBE6, m.u16(0xBE6) + 1)
            for o in (0x0C, 0x0E, 0x10, 0x12):
                m.set16(a1 + o, 0)
            m.set8(a1 + 1, 0)
            m.set8(a1 + 0x1C, 0)
            m.set8(a1, m.u8(a1) & ~2)
            self._msg(f"Manta {bay + 1} returned to hangar")
            m.set16(LIFT_BAY, 0xFFFF)
            self.dock_attach()
            m.set16(stunits.LIFT_STATE, 3)
            return
        d3 = m.u16(LIFT_NEXT)
        a0 = self.obj(MANTA_OBJ) + s16(w16(d3 * 0x140))
        a1 = MANTA_RECS + s16(w16(d3 * REC))
        m.set16(LIFT_BAY, d3)
        self.init_world_object(a0, 5)
        m.set16(a0 + 0x1A, d3)
        m.set16(stunits.NEW_INDEX, d3 + 2)
        self.reset_pair_schedules()
        for o in (0x0A, 0x0E, 0x12):
            m.set32(a0 + o, m.u32(CARRIER_OBJ + o))
        self._lift_object_height(a0)
        m.set16(a0 + 4, 0xFD4A)
        m.set16(a0 + 0x1E, 0xFA)
        m.set8(a1, m.u8(a1) | 1)
        m.set8(a1 + 1, 3)
        m.set8(a1 + 0x1C, 1)
        for o in (0x2E, 0x2C, 0x1A, 0x18):
            m.set16(a1 + o, 0)
        m.set8(a1 + 0x32, 0)
        codes = m.u16(a1 + 0x0C), m.u16(a1 + 0x0E), m.u16(a1 + 0x10)
        m.set16(a1 + 0x0C, 0)
        for code in codes:
            self.loadout_fit(code, a1, a0)
        self.dock_attach()
        m.set16(stunits.LIFT_STATE, 3)

    def _lift_object_height(self, a: int) -> None:
        m = self.mem
        if not a:
            raise NotImplementedError("lift object pointer is 0")
        p = (0x140 * m.s16(LIFT_POS) * 2) & 0xFFFFFFFF
        m.set32(a + 0x0E, s16((p >> 16) + 0xB4))

    def loadout_fit(self, code: int, rec: int, obj: int) -> None:
        """$1ED46: fit a loadout item (table $CAC4) to a record: missiles add to +$C, the bomb
        and payloads set +$E, the laser +$10, the Avatar laser +$A = 40; the comms pod also
        becomes the relay [$DE0]."""
        m = self.mem
        if code > 11:
            raise NotImplementedError(f"loadout code {code}")
        h = m.u32(LOADOUT + 26 * code + 0x16)
        kind = LOADOUT_HANDLERS.get(h, "?")
        if kind == "?":
            raise NotImplementedError(f"loadout handler ${h:X}")
        if kind is None:
            return
        if kind.startswith("missiles"):
            m.set16(rec + 0x0C, m.u16(rec + 0x0C) + int(kind[-1]))
        elif kind == "bomb":
            m.set16(rec + 0x0E, 4)
        elif kind == "laser":
            m.set8(rec + 0x10, 0xFF)
        elif kind == "avatar":
            m.set16(rec + 0x0A, 0x28)
        elif kind == "payload":
            m.set16(rec + 0x0E, code)
        else:
            m.set16(rec + 0x0E, 0x0A)
            m.set32(COMMS_POD, obj)

    # ---- hangar commands (click handlers; the original takes d2 = 2, a fresh click) ----------
    def _manta_selected(self, bay):
        m = self.mem
        if bay is not None:
            m.set32(0x48E, MANTA_RECS + REC * bay)
            m.set32(0x492, self.obj(MANTA_OBJ + bay))
            m.set16(0xDCC, bay)
        return m.u32(0x48E)

    def _walrus_selected(self, bay):
        m = self.mem
        if bay is not None:
            m.set32(0x568, WALRUS_RECS + REC * bay)
            m.set32(0x56C, self.obj(WALRUS_OBJ + bay))
            m.set16(0xDE4, bay)
        return m.u32(0x568)

    def _refuel(self, a: int, tank: int) -> bool:
        """$1158A / $11D5A: move up to $71C from the ship's tank into the vehicle (word, at
        most $7FFF; any excess goes back)."""
        m = self.mem
        d0, d1 = m.u16(a), s32(m.u32(tank))
        if d0 == 0x7FFF:
            return self._msg("Fuel tanks full")
        if d1 == 0:
            return self._msg("Fuel supply exhausted")
        d2 = 0x71C
        d1 -= d2
        if d1 < 0:
            d2 += d1
            d1 = 0
        d0 = w16(d0 + d2)
        if d0 & 0x8000:
            d1 = s32(d1 + s16(d0 - 0x7FFF))
            d0 = 0x7FFF
        m.set16(a, d0)
        m.set32(tank, d1)
        return True

    def _defuel(self, a: int, tank: int) -> bool:
        """$11676 / $11DA4: the reverse, the ship's tank capped at $FFFE0."""
        m = self.mem
        d0, d1 = m.u16(a), s32(m.u32(tank))
        if d0 == 0:
            return self._msg("Fuel tanks empty")
        if d1 == FUEL_MAX:
            return self._msg("Fuel supply at full capacity")
        d2 = 0x71C
        d0 = w16(d0 - d2)
        if d0 & 0x8000:
            d2 = w16(d2 + d0)
            d0 = 0
        d1 = s32(d1 + s16(d2))
        if d1 > FUEL_MAX:
            d0 = w16(d0 + (d1 - FUEL_MAX))
            d1 = FUEL_MAX
        m.set16(a, d0)
        m.set32(tank, d1)
        return True

    def _manta_balance(self, a1: int) -> None:
        """$C640, in the Manta panel redraw ($B418 with $42966, drawn while [$DC0] = 0):
        +$1C is set (unbalanced) when the weights (item +0) of slots 1 and 2 differ by more
        than 80."""
        m = self.mem
        if m.u16(0xDC0):
            return
        if max(m.u16(a1 + 0x0C), m.u16(a1 + 0x0E), m.u16(a1 + 0x10)) > 11:
            raise NotImplementedError("loadout code beyond the item table")
        d = w16(m.u16(LOADOUT + 26 * m.u16(a1 + 0x0C)) - m.u16(LOADOUT + 26 * m.u16(a1 + 0x0E)))
        m.set8(a1 + 0x1C, 0xFF if w16(self.absw(d)) > 0x50 else 0)

    def cmd_manta_refuel(self, bay: int = None) -> None:
        """$1158A (held button: no click test)."""
        a0 = self._manta_selected(bay)
        if not self.mem.u8(a0 + 1) and self._refuel(a0 + 0x0A, FUEL_MANTA):
            self._manta_balance(a0)

    def cmd_manta_defuel(self, bay: int = None) -> None:
        """$11676."""
        a0 = self._manta_selected(bay)
        if not self.mem.u8(a0 + 1) and self._defuel(a0 + 0x0A, FUEL_MANTA):
            self._manta_balance(a0)

    def cmd_walrus_refuel(self, bay: int = None) -> None:
        """$11D4C."""
        a0 = self._walrus_selected(bay)
        if not self.mem.u8(a0 + 1):
            self._refuel(a0 + 8, FUEL_WALRUS)

    def cmd_walrus_defuel(self, bay: int = None) -> None:
        """$11DA4."""
        a0 = self._walrus_selected(bay)
        if not self.mem.u8(a0 + 1):
            self._defuel(a0 + 8, FUEL_WALRUS)

    def _repair(self, a0: int, kind: str) -> None:
        m = self.mem
        st = m.u8(a0 + 1)
        if st & 0x80:
            return self._replace(a0, kind)
        if st:
            return
        if m.u16(a0 + 0x14) == 0xFA0:
            return self._msg(f"{'Manta' if kind == 'Manta' else 'AAV'} is fully functional")
        m.set8(a0 + 1, 1)
        m.set16(0xDC0 if kind == "Manta" else 0xDC2, 1)

    def _replace(self, a0: int, kind: str) -> None:
        """$117D4 / $11EF0: a destroyed bay is refilled from storage, empty and repaired."""
        m = self.mem
        stock = STOCK_MANTA if kind == "Manta" else STOCK_WALRUS
        if not m.u16(stock):
            return self._msg("No Mantas in storage" if kind == "Manta" else "No AAV's in storage")
        m.set16(stock, m.u16(stock) - 1)
        m.set8(a0, 0)
        m.set8(a0 + 1, 0)
        fields = (0x0A, 0x0C, 0x0E, 0x10, 0x12) if kind == "Manta" else (8, 0x12, 0x26, 0x28)
        for o in fields:
            m.set16(a0 + o, 0)
        m.set16(a0 + 0x14, 0xFA0)
        if kind == "Manta":
            m.set8(a0 + 0x1C, 0)

    def cmd_manta_repair(self, bay: int = None) -> None:
        """$1176A: repair (status 1), or replace a destroyed Manta."""
        self._repair(self._manta_selected(bay), "Manta")

    def cmd_walrus_repair(self, bay: int = None) -> None:
        """$11E8A."""
        self._repair(self._walrus_selected(bay), "Walrus")

    def cmd_manta_replace(self, bay: int = None) -> None:
        """$117D4 (a0 = the record)."""
        self._replace(self._manta_selected(bay), "Manta")

    def cmd_walrus_replace(self, bay: int = None) -> None:
        """$11EF0 (a0 = the record)."""
        self._replace(self._walrus_selected(bay), "Walrus")

    def cmd_manta_prepare(self, bay: int = None) -> None:
        """$1182A: start flight preparation (status 2)."""
        a0 = self._manta_selected(bay)
        self.mem.set8(a0 + 1, 2)
        self.preparation_time(a0, "Manta")

    def cmd_walrus_prepare(self, bay: int = None) -> None:
        """$11F3E."""
        a0 = self._walrus_selected(bay)
        self.mem.set8(a0 + 1, 2)
        self.preparation_time(a0, "Walrus")

    def _bay_status_message(self, st: int, bay: int, kind: str) -> None:
        if st & 0x80:
            self._msg(f"{kind} {bay + 1} destroyed")
        elif st <= 2:
            self._msg(f"{kind} {bay + 1} is under repair")
        else:
            self._msg(f"{kind} {bay + 1} is not in the hangar")

    def cmd_manta_to_deck(self, bay: int = None) -> None:
        """$11894: send the selected Manta [$DCC] up the lift (status 3, lift state 1)."""
        m = self.mem
        a0 = self._manta_selected(bay)
        d1 = m.u16(0xDCC)
        if not m.u8(0xB32):
            return self._msg("Lift is out of order")
        st = m.u8(a0 + 1)
        if st:
            return self._bay_status_message(st, d1, "Manta")
        if m.u8(MANTA_LANDING):
            return self._msg("Plane landing - flight deck in use")
        if m.s16(LIFT_BAY) >= 0 or m.u16(stunits.LIFT_STATE) not in (0, 3):
            return self._msg("Lift in use")
        if m.u8(a0 + 0x1C):
            return self._msg(f"Manta {d1 + 1} has an unbalanced payload")
        if m.u16(a0 + 0x10) == 0x0A and m.u32(COMMS_POD):
            return self._msg(f"Manta {d1 + 1} Long range communications are in use")
        m.set8(a0 + 1, 3)
        m.set16(stunits.LIFT_STATE, 1)
        m.set16(LIFT_NEXT, d1)
        self.dock_attach()
        m.set16(0xDC0, 2)

    def cmd_manta_to_hangar(self) -> None:
        """$1242A: take the Manta on deck (mode 1) down the lift."""
        m = self.mem
        if not m.u8(0xB32):
            return self._msg("Lift is out of order")
        bay = m.s16(LIFT_BAY)
        if m.u16(stunits.LIFT_STATE) or bay < 0 or m.u8(MANTA_RECS + REC * bay + 0x1C) != 1:
            return self._msg("No aircraft on the flight deck")
        m.set8(MANTA_RECS + REC * bay + 1, 3)
        m.set16(stunits.LIFT_STATE, 1)
        self.dock_attach()

    def cmd_manta_take_off(self) -> None:
        """$12002: the Manta on deck takes off (mode 2); without a waypoint it gets one
        6000 ahead of the carrier at (bay + 1) * 300 + 500."""
        m = self.mem
        bay = m.s16(LIFT_BAY)
        if m.u16(stunits.LIFT_STATE) or bay < 0 or m.u8(MANTA_RECS + REC * bay + 0x1C) != 1:
            return self._msg("No aircraft available for launch")
        a0 = MANTA_RECS + REC * bay
        m.set8(a0 + 0x1C, 2)
        m.set8(a0 + 0x1D, 0)
        m.set8(a0 + 0x33, 0)
        f = m.u8(a0)
        if not f & 2:
            m.set8(a0, (f | 2) & ~4)
            m.set16(a0 + 0x2A, 0x3C)
            m.set32(a0 + 0x22, s16(w16((bay + 1) * 0x12C + 0x1F4)))
            c = CARRIER_OBJ
            self.set_matrix(m.u16(c + 6))
            x, z = self.matrix_rotate(0, 0xE890)
            m.set32(a0 + 0x1E, x + m.u32(c + 0x0A))
            m.set32(a0 + 0x26, z + m.u32(c + 0x12))
        self._msg(f"Launching Manta {bay + 1}")

    def cmd_manta_land(self, bay: int = None) -> None:
        """$120F4: order the selected airborne Manta to land on the carrier (mode 0), if the
        deck and lift are free and it is within 16384."""
        m, c = self.mem, CARRIER_OBJ
        a1 = self._manta_selected(bay)
        a0 = m.u32(0x492)
        n = m.u16(0xDCC) + 1
        st = m.u8(a1 + 1)
        if st & 0x80:
            return self._msg(f"Manta {n} destroyed")
        if st < 8:
            return self._msg(f"Manta {n} is not airborne")
        if m.u8(a1) & 1 and m.u8(a1 + 0x1C) == 1:
            return self._msg(f"Manta {n} is already on deck")
        if m.u8(MANTA_LANDING):
            return self._msg(f"Manta {n} is already landing" if m.u8(a1) & 1 and m.u8(a1 + 0x1C) == 0
                             else "Another Manta is landing")
        dx = s32(m.u32(a0 + 0x0A) - m.u32(c + 0x0A))
        dz = s32(m.u32(a0 + 0x12) - m.u32(c + 0x12))
        if dx != s16(dx) or dz != s16(dz) or s32(dx * dx + dz * dz) > 0x10000000:
            return self._msg(f"Manta {n} is out of range")
        if m.u16(stunits.LIFT_STATE):
            return self._msg("Lift in use")
        if m.s16(LIFT_BAY) >= 0:
            return self._msg("Flight deck in use")
        m.set8(MANTA_LANDING, 0xFF)
        m.set16(0x6DE, m.u16(a0 + 0x1A))
        m.set8(a1, m.u8(a1) | 1)
        m.set8(a1 + 0x1C, 0)
        m.set8(a1 + 0x1D, 0)
        m.set16(0xDDC, m.u16(0xDCC))
        self._msg(f"Manta {n} is landing")

    def cmd_walrus_launch(self, bay: int = None) -> None:
        """$1250E: launch the selected Walrus from the dock (mode 2, status 8), fitting its
        two loadout slots; without a waypoint it gets one 4000 astern."""
        m, c = self.mem, CARRIER_OBJ
        self._walrus_selected(bay)
        d0 = m.u16(0xDE4)
        a0, a1 = m.u32(0x56C), m.u32(0x568)
        st = m.u8(a1 + 1)
        if st:
            return self._bay_status_message(st, d0, "Walrus")
        if m.u8(0x122F):
            return self._msg("Cannot launch while drones are docking")
        if m.u8(0xD40):
            return self._msg("Docking bay is in use")
        if m.u8(WALRUS_DOCKING) or m.u16(stunits.DOOR_STATE):
            return self._msg("Docking bay is in use")
        m.set16(stunits.DOCKED_WALRUS, d0)
        self.init_world_object(a0, 6)
        m.set16(a0 + 0x1A, d0)
        m.set16(stunits.NEW_INDEX, d0 + 6)
        self.reset_pair_schedules()
        for o in (0x0A, 0x0E, 0x12):
            m.set32(a0 + o, m.u32(c + o))
        m.set32(a0 + 0x0E, 0xFFFFFFF1)
        m.set16(a0 + 0x24, 0)
        m.set16(a0 + 0x1C, 0)
        m.set16(a0 + 0x1E, 0xFDBC)
        f = m.u8(a1)
        if not f & 2:
            m.set8(a1, (f | 2) & ~4)
            m.set16(a1 + 0x24, 0x3C)
            self.set_matrix(m.u16(c + 6))
            x, z = self.matrix_rotate(w16(m.u16(a0 + 0x1A) * 0x7D0 - 0xBB8), 0xF060)
            m.set32(a1 + 0x1C, x + m.u32(c + 0x0A))
            m.set32(a1 + 0x20, z + m.u32(c + 0x12))
        m.set8(a1 + 1, 8)
        m.set8(a1 + 0x1A, 2)
        m.set8(a1 + 0x1B, 0)
        m.set16(a1 + 4, 0)
        m.set8(a1, m.u8(a1) | 1)
        for o in (0x2C, 0x18, 0x16):
            m.set16(a1 + o, 0)
        m.set8(a1 + 0x32, 0)
        m.set8(a1 + 0x33, 0)
        codes = m.u16(a1 + 0x26), m.u16(a1 + 0x28)
        m.set16(a1 + 0x0A, 0xFFFF)
        m.set16(a1 + 0x0E, 0xFFFF)
        for code in codes:
            self.loadout_fit(code, a1, a0)
        m.set8(WALRUS_DOCKING, 0xFF)
        m.set16(0x6E0, m.u16(a0 + 0x1A))
        self._door(1)
        self._msg(f"Launching walrus {d0 + 1}")

    def cmd_walrus_dock(self, bay: int = None) -> None:
        """$127C4: order the selected Walrus home (mode 0), if it is inside the docking cone
        astern (local z -1500..-8600, |x| <= -(z + 600))."""
        m, c = self.mem, CARRIER_OBJ
        self._walrus_selected(bay)
        n = m.u16(0xDE4) + 1
        a0, a1 = m.u32(0x56C), m.u32(0x568)
        st = m.u8(a1 + 1)
        if st != 8:
            return self._msg(f"Walrus {n} destroyed" if st & 0x80 else f"Walrus {n} is already in dock")
        if m.u8(0x122F):
            return self._msg("Cannot dock while drones are docking")
        if m.u8(0xD40):
            return self._msg("Docking bay is in use")
        cone = f"Walrus {n} is outside the docking cone"
        if m.u8(a1) & 1 and m.u8(a1 + 0x1A) == 2:
            return self._msg(cone)
        if m.u8(WALRUS_DOCKING) or m.u16(stunits.DOOR_STATE):
            return self._msg("Docking bay is in use")
        dx = s32(m.u32(a0 + 0x0A) - m.u32(c + 0x0A))
        dz = s32(m.u32(a0 + 0x12) - m.u32(c + 0x12))
        if dx != s16(dx) or dz != s16(dz):
            return self._msg(cone)
        self.set_matrix(w16(-m.u16(c + 6)))
        x, z = self.matrix_rotate(dx, dz)
        ax = self.absw(x)
        if z > -0x5DC or z < -0x2198 or ax > 0x1F40 or s16(-(z + 0x258)) < ax:
            return self._msg(cone)
        m.set8(a1, 0x41)
        m.set8(a1 + 0x1A, 0)
        m.set8(a1 + 0x1B, 0)
        m.set8(WALRUS_DOCKING, 0xFF)
        m.set16(0x6E0, m.u16(a0 + 0x1A))
        self._msg(f"Walrus {n} is docking")


ROUTINES = {
    "manta_bays_step": (0x1CDA2, "manta_bays_step"),
    "walrus_bays_step": (0x1DCFC, "walrus_bays_step"),
    "manta_lift_step": (0x1EAA0, "manta_lift_step"),
}


# ---- check_units: scenarios -----------------------------------------------------------------
def _objaddr(k: int) -> int:
    return stunits.OBJECTS + stunits.OBJECT_SIZE * k


def _vehicle(m, kind: str, bay: int, status: int = 8):
    """A fresh vehicle object (type 5 or 6, as $192E2 makes it) and an empty record."""
    t = 5 if kind == "Manta" else 6
    o = _objaddr((MANTA_OBJ if kind == "Manta" else WALRUS_OBJ) + bay)
    m.buf[o:o + 0x40] = bytes(0x40)
    m.set16(o, m.u16(stunits.OBJ_FLAGS + 2 * t))
    m.set16(o + 2, t)
    for j in range(4):
        m.set16(o + 0x30 + 2 * j, m.u16(stunits.OBJ_PARAMS + 8 * t + 2 * j))
    for j in range(128):
        m.set16(o + 0x40 + 2 * j, m.u16(stunits.FRAME))
    m.set16(o + 0x1A, bay)
    rec = (MANTA_RECS if kind == "Manta" else WALRUS_RECS) + REC * bay
    m.buf[rec:rec + REC] = bytes(REC)
    m.set8(rec + 1, status)
    m.set16(rec + 0x14, 4000)
    return o, rec


def _put(m, o, x, y, z, buf=None):
    m.set32(o + 0x0A, x)
    m.set32(o + 0x0E, y)
    m.set32(o + 0x12, z)
    m.set32(o + 0x3C, buf or 0)


def _carrier_local(m, lx, lz):
    """World (x, z) of a point in the carrier's frame."""
    st = stunits.UnitState.from_ram(bytes(m.buf))
    st.set_matrix(m.u16(CARRIER_OBJ + 6))
    x, z = st.matrix_rotate(lx, lz)
    return m.u32(CARRIER_OBJ + 0x0A) + x, m.u32(CARRIER_OBJ + 0x12) + z


def _controls(m, rnd, o, rec, kind):
    """The vehicle is the one on screen: mouse, speed keys, the fire button."""
    m.set32(VIEWED, o)
    m.set8(POINTER_MODE, rnd.choice((0, 0, 0, 0xFF)))
    m.set16(MOUSE_DX, rnd.choice((0, rnd.randrange(-300, 300))))
    m.set16(MOUSE_DY, rnd.choice((0, rnd.randrange(-300, 300))))
    m.set8(KEY_UP, rnd.choice((0, 0, 0xFF)))
    m.set8(KEY_DOWN, rnd.choice((0, 0, 0xFF)))
    m.set8(FIRE_KEYS, (m.u8(FIRE_KEYS) & 0xFE) | rnd.choice((0, 1, 1)))
    m.set8(FIRE_INHIBIT, rnd.choice((0, 0, 0, 0xFF)))
    m.set16(0x940, rnd.choice((0, 0, 3)))
    m.set16(0x942, rnd.choice((0, 0, 3)))


def scenario_manta_flight(m, rnd):
    """Mantas flying by hand or to waypoints around the island and the carrier, some low,
    short of fuel or damaged, some at the edge of telemetry range (with a comms-pod relay);
    one on screen, firing."""
    c, buf = CARRIER_OBJ, m.u32(CARRIER_OBJ + 0x3C)
    objs = []
    for bay in range(4):
        if rnd.random() < 0.3:
            continue
        o, rec = _vehicle(m, "Manta", bay)
        objs.append((o, rec))
        where = rnd.random()
        if where < 0.5 and buf:
            x = m.u32(buf + 2) + rnd.randrange(-20000, 20000)
            z = m.u32(buf + 0x0A) + rnd.randrange(-20000, 20000)
            y = rnd.choice((rnd.randrange(20, 160), rnd.randrange(150, 3600)))
            _put(m, o, x, y, z, buf)
        elif where < 0.8:
            _put(m, o, m.u32(c + 0x0A) + rnd.randrange(-9000, 9000), rnd.randrange(30, 3400),
                 m.u32(c + 0x12) + rnd.randrange(-9000, 9000))
        else:
            d = rnd.choice((190000, 205000, 255000, 265000, 300000)) * rnd.choice((1, -1))
            _put(m, o, m.u32(c + 0x0A) + d, rnd.randrange(300, 3000), m.u32(c + 0x12) + rnd.randrange(-50000, 50000))
        for off in (4, 6, 8):
            m.set16(o + off, rnd.randrange(0x10000))
        m.set8(rec, rnd.choice((0, 0x80, 0x40, 0xC0, 1, 3, 7, 0x83)))
        m.set8(rec + 0x1C, 3)
        m.set16(rec + 2, rnd.randrange(-255, 256))
        m.set16(rec + 4, rnd.randrange(-255, 256))
        m.set16(rec + 6, rnd.randrange(0, 250))
        m.set16(rec + 8, rnd.randrange(-20, 300))
        m.set16(rec + 0x0A, rnd.choice((0, 30, 200, 3000, 0x7FFF)))
        m.set16(rec + 0x14, rnd.choice((4000, 4000, 600, 300, 40)))
        m.set16(rec + 0x2E, rnd.choice((0, 0, 1, 2)))
        m.set16(rec + 0x30, rnd.choice((0, 3)))
        near = rnd.random() < 0.3
        m.set32(rec + 0x1E, m.u32(o + 0x0A) + rnd.randrange(-20000, 20000) // (40 if near else 1))
        m.set32(rec + 0x22, rnd.randrange(100, 3000))
        m.set32(rec + 0x26, m.u32(o + 0x12) + rnd.randrange(-20000, 20000) // (40 if near else 1))
        m.set16(rec + 0x2A, rnd.randrange(50, 250))
        m.set16(rec + 0x1A, rnd.choice((0, 1, 2)))
        m.set8(rec + 0x10, rnd.choice((0, 0xFF)))
        m.set16(rec + 0x0C, rnd.choice((0, 2)))
        m.set16(rec + 0x0E, rnd.choice((0, 4, 4, 0x0A)))
        m.set8(rec + 0x2C, rnd.choice((0, 0xFF)))
    if not objs:
        return
    m.set8(COMMS_STATUS, rnd.choice((0, 0xFF, 0xFF)))
    m.set32(COMMS_POD, rnd.choice([0] + [o for o, _ in objs]))
    if len(objs) > 1 and rnd.random() < 0.5:                  # a relay out beyond the carrier's range
        (p, prec), (q, _) = rnd.sample(objs, 2)
        d = rnd.choice((230000, 280000))
        _put(m, p, m.u32(c + 0x0A) + d, 1500, m.u32(c + 0x12))
        _put(m, q, m.u32(c + 0x0A) + d + rnd.choice((150000, 210000, -210000)), 1500, m.u32(c + 0x12))
        m.set8(COMMS_STATUS, 0xFF)
        m.set32(COMMS_POD, p)
    m.set8(0x118A, rnd.choice((0, 0, 0, 0xFF)))
    m.set16(stunits.SCREEN, rnd.choice((0, 0, 0x1B)))
    o, rec = rnd.choice(objs)
    if rnd.random() < 0.8:
        _controls(m, rnd, o, rec, "Manta")
        m.set8(rec, m.u8(rec) & 0xFE)
    m.set16(LOCK_TIMER, rnd.choice((0, 25, 25, 25)))
    m.set32(LOCK_TARGET, rnd.choice((CARRIER_OBJ, stsim.ENEMY_CARRIER, 0xFFFFFFFF)))


def scenario_manta_carrier(m, rnd):
    """A Manta landing on the carrier (every step of mode 0), parked on the lift, or taking
    off from it (mode 2)."""
    c = CARRIER_OBJ
    bay = rnd.randrange(4)
    o, rec = _vehicle(m, "Manta", bay)
    mode = rnd.choice((0, 0, 0, 1, 2, 2))
    sub = rnd.choice((0, 0, 1, 2, 3, 4)) if mode == 0 else rnd.choice((0, 1, 2)) if mode == 2 else 0
    m.set8(rec, rnd.choice((1, 3, 0x43, 0x81)))
    m.set8(rec + 0x1C, mode)
    m.set8(rec + 0x1D, sub)
    m.set16(rec + 6, rnd.randrange(0, 200))
    m.set16(rec + 8, rnd.randrange(0, 200))
    m.set16(rec + 2, rnd.randrange(-200, 200))
    m.set16(rec + 4, rnd.choice((0, rnd.randrange(-200, 200))))
    m.set16(rec + 0x0A, rnd.choice((5, 3000)))
    m.set8(rec + 0x33, rnd.choice((0, 0xFF)))
    m.set16(stunits.C_SPEED, rnd.randrange(0, 100))
    for off in (4, 6, 8):
        m.set16(o + off, rnd.randrange(0x10000))
    m.set8(MANTA_LANDING, 0xFF if mode == 0 else 0)
    m.set16(0x6DE, bay)
    if mode == 0 and sub == 0:
        x, z = _carrier_local(m, rnd.randrange(-9000, 9000), rnd.randrange(-9000, 9000))
        y = rnd.randrange(300, 900)
        if rnd.random() < 0.5:
            x, z = _carrier_local(m, rnd.randrange(-500, 500), rnd.randrange(-500, 500))
            y = rnd.choice((570, 575, y))
            m.set16(rec + 4, rnd.choice((0, 0, 30)))
        _put(m, o, x, y, z)
        return
    if rnd.random() < 0.1:
        m.set8(rec + 0x1D, rnd.choice((5, 7)))
    if rnd.random() < 0.15:
        m.set16(rec + 0x14, 0)
    lx, lz = rnd.randrange(-400, 400), rnd.randrange(-300, 900)
    if mode == 0 and sub == 1:
        m.set16(o + 0x20, rnd.randrange(-80, 80))
        m.set16(o + 0x24, rnd.randrange(-80, 80))
    if mode == 0 and sub == 3:
        lx, lz = rnd.choice(((0, 250), (rnd.randrange(-30, 30), rnd.randrange(220, 280))))
    if mode == 0 and sub == 4 or mode != 0:
        lx, lz = 0, 250 if mode != 2 or sub == 0 else rnd.randrange(250, 2100)
        m.set16(LIFT_BAY, bay)
        m.set8(o + 1, m.u8(o + 1) | 4)
        m.set32(0x6D6, o)
        m.set16(stunits.LIFT_STATE, 0)
        m.set16(CARRIER_OBJ + 2, 2)
        m.set16(o + 6, m.u16(c + 6))
    m.set16(o + 0x1C, lx)
    m.set16(o + 0x1E, lz)
    x, z = _carrier_local(m, lx, lz)
    y = rnd.randrange(180, 400) if mode == 0 else 180 if mode == 1 or sub == 0 else rnd.randrange(180, 600)
    _put(m, o, x, y, z)


def scenario_manta_runway(m, rnd):
    """Mantas over the island runway (buffer entry 1): approaching low (lined up or not),
    landing (mode 4), parked (mode 5, some ordered to take off) and taking off (mode 6)."""
    buf = m.u32(CARRIER_OBJ + 0x3C)
    if not buf:
        return
    rw = buf + 0x52
    if m.u16(rw + 2) != 0x24:
        return
    st = stunits.UnitState.from_ram(bytes(m.buf))
    st.set_matrix(m.u16(rw + 6))
    for bay in range(4):
        if rnd.random() < 0.4:
            continue
        o, rec = _vehicle(m, "Manta", bay)
        mode = rnd.choice((None, None, 4, 4, 5, 6, 6))
        px, pz = rnd.choice(((rnd.randrange(-300, 300), rnd.randrange(-4500, 2500)),
                             (rnd.randrange(-1500, 1500), rnd.randrange(-6000, 6000)),
                             (0, 0xA5A), (bay * 600 - 900, 0xCC6), (0, 0xA8C)))
        x, z = st.matrix_rotate(px, pz)
        _put(m, o, m.u32(buf + 2) + s16(x + m.u16(rw + 0x0A)), rnd.randrange(60, 160),
             m.u32(buf + 0x0A) + s16(z + m.u16(rw + 0x0E)), buf)
        h = w16(m.u16(rw + 6) + rnd.choice((0, 0, 0, rnd.randrange(-5000, 5000), 0x8000)))
        m.set16(o + 6, h)
        m.set16(rec + 6, rnd.choice((40, 90, 90, 120)))
        m.set16(rec + 8, rnd.choice((0, 40, 90)))
        m.set16(rec + 2, rnd.choice((0, 0, 50, 150)))
        m.set16(rec + 4, rnd.choice((0, 0, 30, 120)))
        m.set16(rec + 0x0A, rnd.choice((100, 3000, 0x7FD0)))
        m.set16(rec + 0x14, rnd.choice((4000, 400)))
        if mode is None:
            m.set8(rec, 0)
            if rnd.random() < 0.5:                              # lined up for a landing
                x, z = st.matrix_rotate(rnd.randrange(-150, 150), rnd.randrange(-3500, 1500))
                _put(m, o, m.u32(buf + 2) + s16(x + m.u16(rw + 0x0A)), rnd.randrange(60, 140),
                     m.u32(buf + 0x0A) + s16(z + m.u16(rw + 0x0E)), buf)
                m.set16(o + 6, w16(m.u16(rw + 6) + rnd.randrange(-3000, 3000)))
                m.set16(rec + 2, rnd.randrange(-100, 100))
                m.set16(rec + 4, rnd.randrange(-60, 60))
                m.set16(rec + 6, rnd.randrange(50, 100))
                m.set16(rec + 8, 50)
            continue
        m.set8(rec, rnd.choice((1, 0x21, 0x23)))
        m.set8(rec + 0x1C, mode)
        m.set8(rec + 0x1D, rnd.choice((0, 1, 2, 3, 0, 1, 2, 3, 5)) if mode == 4 else rnd.choice((0, 1)))
        m.set8(rec + 0x33, rnd.choice((0, 0xFF)))
        m.set16(rec + 0x2E, rnd.choice((0, 0, 2)))
        if mode == 5:
            m.set32(o + 0x0E, 100)
    if rnd.random() < 0.2:
        m.set16(rw + 2, 0x25)
    if rnd.random() < 0.15:                                   # out of the carrier's telemetry range
        m.set32(CARRIER_OBJ + 0x0A, m.u32(buf + 2) + 280000)
        m.set8(COMMS_STATUS, 0)


def scenario_lift(m, rnd):
    """The lift moving: a Manta coming up with its loadout fitted, or one being stowed."""
    c = CARRIER_OBJ
    state = rnd.choice((1, 1, 2, 2, 3))
    m.set16(stunits.LIFT_STATE, state)
    m.set16(LIFT_POS, rnd.choice((0, 0xF000, 0xC800, 0xC000)) if state != 3 else rnd.choice((0xC000, 0xF800)))
    bay = rnd.randrange(4)
    if rnd.random() < 0.5:                               # a Manta on the lift going down
        o, rec = _vehicle(m, "Manta", bay, 3)
        m.set8(rec, 1)
        m.set8(rec + 0x1C, 1)
        m.set16(rec + 0x0C, rnd.choice((0, 2)))
        m.set16(rec + 0x0E, rnd.choice((0, 4, 0x0A)))
        m.set8(rec + 0x10, rnd.choice((0, 0xFF)))
        m.set16(o + 0x1E, 0xFA)
        x, z = _carrier_local(m, 0, 0xFA)
        _put(m, o, x, 180, z)
        m.set8(o + 1, m.u8(o + 1) | 4)
        m.set16(LIFT_BAY, bay)
        m.set32(0x6D6, o)
        if m.u16(rec + 0x0E) == 0x0A:
            m.set32(COMMS_POD, o)
    else:
        m.set16(LIFT_BAY, 0xFFFF)
        m.set32(0x6D6, 0)
        m.set16(LIFT_NEXT, bay)
        rec = MANTA_RECS + REC * bay
        m.set8(rec + 1, 3)
        m.set16(rec + 0x0C, rnd.choice((0, 1, 2, 3)))
        m.set16(rec + 0x0E, rnd.choice((0, 4, 5, 10, 1)))
        m.set16(rec + 0x10, rnd.choice((0, 5, 6, 10, 3)))
        m.set16(rec + 0x0A, rnd.choice((0, 0x7FFF)))
    m.set16(c + 2, 3)


def scenario_walrus(m, rnd):
    """Walruses at sea, on the beach ramps and ashore, driven by hand or to waypoints; one
    on screen firing its laser, a guided missile (and steering it), or dropping its
    payload (cargo, ACCB, fuel pod to a Walrus facing it)."""
    c, buf = CARRIER_OBJ, m.u32(CARRIER_OBJ + 0x3C)
    if not buf:
        return
    objs = []
    hx, hz = m.s16(buf + 0x30), m.s16(buf + 0x32)
    for bay in range(4):
        if rnd.random() < 0.3:
            continue
        o, rec = _vehicle(m, "Walrus", bay)
        objs.append((o, rec))
        side = rnd.choice(("x", "z"))
        edge = rnd.choice((rnd.randrange(-700, 700), rnd.randrange(-700, 700), rnd.randrange(-6000, -700),
                           rnd.randrange(700, 4000)))
        along = rnd.randrange(-12000, 12000)
        sgn = rnd.choice((1, -1))
        dx, dz = (sgn * (hx + edge), along) if side == "x" else (along, sgn * (hz + edge))
        _put(m, o, m.u32(buf + 2) + dx, rnd.choice((-15, 0, 15, 10)), m.u32(buf + 0x0A) + dz, buf)
        m.set16(o + 6, rnd.choice((rnd.randrange(0x10000), (0x4000 if dx < 0 else 0xC000) if side == "x"
                                   else (0 if dz < 0 else 0x8000))))
        m.set16(o + 0x26, rnd.choice((0, rnd.randrange(0x10000))))
        m.set8(rec, rnd.choice((0, 0x80, 0x40, 0x20, 0x08, 0xE0, 3, 7, 0x83)))
        m.set8(rec + 0x1A, 3)
        m.set16(rec + 2, rnd.randrange(-255, 256))
        m.set16(rec + 4, rnd.randrange(-10, 96))
        m.set16(rec + 6, rnd.randrange(-20, 120))
        m.set16(rec + 8, rnd.choice((0, 20, 300, 0x7FFF)))
        m.set16(rec + 0x14, rnd.choice((4000, 4000, 400, 100)))
        near = 30 if rnd.random() < 0.3 else 1
        m.set32(rec + 0x1C, m.u32(o + 0x0A) + rnd.randrange(-8000, 8000) // near)
        m.set32(rec + 0x20, m.u32(o + 0x12) + rnd.randrange(-8000, 8000) // near)
        m.set16(rec + 0x24, rnd.randrange(20, 96))
        m.set16(rec + 0x18, rnd.choice((0, 1, 2)))
        m.set16(rec + 0x0A, rnd.choice((0xFFFF, 0, 40)))
        m.set16(rec + 0x0C, rnd.choice((0, 2)))
        m.set16(rec + 0x0E, rnd.choice((0xFFFF, 8, 9, 9, 0x0B, 0x0B)))
        m.set16(rec + 0x10, rnd.choice((0x11, 0x12)))
        m.set16(rec + 0x2C, rnd.choice((0, 0, 1)))
        if rnd.random() < 0.15:
            _put(m, o, m.u32(c + 0x0A) + rnd.choice((205000, 270000)), -15, m.u32(c + 0x12), 0)
    if not objs:
        return
    o, rec = rnd.choice(objs)
    if rnd.random() < 0.8:
        _controls(m, rnd, o, rec, "Walrus")
        m.set8(rec, m.u8(rec) & 0xFE)
    if len(objs) > 1 and rnd.random() < 0.5:                  # a second Walrus to refuel
        o2, rec2 = rnd.choice([v for v in objs if v[0] != o])
        st = stunits.UnitState.from_ram(bytes(m.buf))
        vx, vz = st.yaw_vector(o, rnd.choice((300, 450, 700)))
        _put(m, o2, m.u32(o + 0x0A) + vx, m.u32(o + 0x0E), m.u32(o + 0x12) + vz, m.u32(o + 0x3C))
        m.set16(o2 + 6, w16(m.u16(o + 6) + 0x8000 + rnd.choice((0, 0, 3000, 9000))))
        m.set16(rec + 0x18, 2)
        m.set16(rec + 0x0E, 0x0B)
    if rnd.random() < 0.4:                                      # ashore on a neutral island
        m.set8(stsim.OWNERS + m.u8(buf + 1), 0)
        _put(m, o, m.u32(buf + 2) + rnd.randrange(-hx + 2500, hx - 2500), 15,
             m.u32(buf + 0x0A) + rnd.randrange(-hz + 2500, hz - 2500), buf)
        m.set16(rec + 0x18, 2)
        m.set16(rec + 0x0E, 9)
    if rnd.random() < 0.4:                                      # steering a guided missile
        mis = _objaddr(rnd.randrange(24, 40))
        m.buf[mis:mis + 0x40] = bytes(0x40)
        m.set16(mis, m.u16(stunits.OBJ_FLAGS + 2 * 0x12))
        m.set16(mis + 2, 0x12)
        for j in range(4):
            m.set16(mis + 0x30 + 2 * j, m.u16(stunits.OBJ_PARAMS + 8 * 0x12 + 2 * j))
        for j in range(128):
            m.set16(mis + 0x40 + 2 * j, m.u16(stunits.FRAME))
        _put(m, mis, m.u32(o + 0x0A), 100, m.u32(o + 0x12), m.u32(o + 0x3C))
        m.set16(mis + 0x1A, w16(m.u16(o + 0x1A) + rnd.choice((4, 4, 5))))
        m.set8(mis + 0x1C, 0x78)
        m.set32(o + 0x20, mis)
        m.set8(rec, (m.u8(rec) | 0x10) & 0xFE)
        m.set16(rec + 0x2E, rnd.choice((0x50, 0xA3)))


def scenario_walrus_dock(m, rnd):
    """A Walrus docking (every step of mode 0) or launching (mode 2) at the stern."""
    c = CARRIER_OBJ
    bay = rnd.randrange(4)
    o, rec = _vehicle(m, "Walrus", bay)
    mode = rnd.choice((0, 0, 2, 2, 1))
    sub = rnd.choice((0, 0, 1, 2, 3)) if mode == 0 else rnd.choice((0, 1, 2))
    if rnd.random() < 0.08:
        sub = rnd.choice((4, 9))
    m.set8(rec, rnd.choice((1, 3, 0x41, 7)))
    m.set8(rec + 0x1A, mode)
    m.set8(rec + 0x1B, sub)
    m.set16(rec + 4, rnd.randrange(0, 60))
    m.set16(rec + 6, rnd.randrange(0, 60))
    m.set16(rec + 2, rnd.randrange(-200, 200))
    m.set16(rec + 8, 3000)
    m.set16(rec + 0x0C, rnd.choice((0, 2)))
    m.set16(rec + 0x0A, rnd.choice((0xFFFF, 40)))
    m.set16(rec + 0x0E, rnd.choice((0xFFFF, 0, 8, 9, 0x0B)))
    m.set8(rec + 0x33, rnd.choice((0, 0xFF)))
    m.set32(rec + 0x1C, m.u32(c + 0x0A) + rnd.randrange(-9000, 9000))
    m.set32(rec + 0x20, m.u32(c + 0x12) + rnd.randrange(-9000, 9000))
    m.set16(stunits.C_SPEED, rnd.randrange(0, 60))
    m.set8(WALRUS_DOCKING, 0xFF)
    m.set16(0x6E0, bay)
    m.set16(o + 6, rnd.randrange(0x10000))
    if mode == 0 and sub == 0:
        lx, lz = rnd.choice(((rnd.randrange(-3000, 3000), rnd.randrange(-9000, -1000)),
                             (rnd.randrange(-150, 150), rnd.randrange(-1650, -1350)),
                             (rnd.randrange(-20000, 20000), rnd.randrange(-20000, 20000))))
        x, z = _carrier_local(m, lx, lz)
        _put(m, o, x, -15, z, m.u32(c + 0x3C) if rnd.random() < 0.5 else 0)
        return
    lz = {(0, 1): -1500, (0, 2): rnd.randrange(-1500, -600), (0, 3): -650, (2, 0): rnd.randrange(-1600, -500),
          (2, 1): -1600, (2, 2): -1700}.get((mode, sub), -650)
    m.set16(o + 0x1C, 0)
    m.set16(o + 0x1E, lz)
    m.set16(stunits.DOCKED_WALRUS, bay)
    m.set8(o + 1, m.u8(o + 1) | 4)
    m.set32(0x6DA, o)
    m.set16(stunits.DOOR_STATE, rnd.choice((0, 1, 2, 3)))
    m.set16(stunits.DOOR_POS, rnd.choice((0xD000, 0xE000, 0)))
    x, z = _carrier_local(m, 0, lz)
    _put(m, o, x, -15, z)


def _fill_objects(m, rnd):
    """Sometimes no free object (24 up to [$A48]) for a new shot."""
    if rnd.random() < 0.15:
        for k in range(24, m.u16(0xA48)):
            o = _objaddr(k)
            m.set8(o + 1, m.u8(o + 1) | 1)


def scenario_manta_weapons(m, rnd):
    """A Manta on screen at altitude firing: laser (fitted or not), missile (locked, with
    or without missiles, cooldown) or bomb."""
    c = CARRIER_OBJ
    bay = rnd.randrange(4)
    o, rec = _vehicle(m, "Manta", bay)
    _put(m, o, m.u32(c + 0x0A) + rnd.randrange(-6000, 6000), rnd.randrange(800, 2500),
         m.u32(c + 0x12) + rnd.randrange(-6000, 6000))
    for off in (4, 6, 8):
        m.set16(o + off, rnd.randrange(0x10000))
    m.set16(rec + 6, rnd.choice((60, 119, 120, 200)))
    m.set16(rec + 8, 100)
    m.set16(rec + 0x0A, 3000)
    m.set16(rec + 0x1A, rnd.choice((0, 0, 1, 1, 2, 2)))
    m.set8(rec + 0x10, rnd.choice((0, 0xFF, 0xFF)))
    m.set16(rec + 0x0C, rnd.choice((0, 2, 2)))
    m.set16(rec + 0x0E, rnd.choice((0, 4, 4)))
    m.set8(rec + 0x2C, rnd.choice((0, 0xFF)))
    _controls(m, rnd, o, rec, "Manta")
    m.set8(POINTER_MODE, 0)
    m.set8(FIRE_KEYS, m.u8(FIRE_KEYS) | 1)
    m.set8(FIRE_INHIBIT, rnd.choice((0, 0, 0, 0xFF)))
    m.set16(LOCK_TIMER, 25)
    m.set32(LOCK_TARGET, rnd.choice((CARRIER_OBJ, stsim.ENEMY_CARRIER)))
    _fill_objects(m, rnd)


def scenario_walrus_weapons(m, rnd):
    """A Walrus on screen firing: laser, guided missile, cargo drop, ACCB (ashore on a
    neutral island, or not suitable) or fuel pod (to a Walrus alongside, facing or not)."""
    c, buf = CARRIER_OBJ, m.u32(CARRIER_OBJ + 0x3C)
    if not buf:
        return
    bay = rnd.randrange(4)
    o, rec = _vehicle(m, "Walrus", bay)
    hx, hz = m.s16(buf + 0x30), m.s16(buf + 0x32)
    ashore = rnd.random() < 0.5
    if ashore:
        _put(m, o, m.u32(buf + 2) + rnd.randrange(-hx + 1500, hx - 1500), 15,
             m.u32(buf + 0x0A) + rnd.randrange(-hz + 1500, hz - 1500), buf)
        if rnd.random() < 0.7:
            m.set8(stsim.OWNERS + m.u8(buf + 1), 0)
    else:
        x, z = _carrier_local(m, rnd.randrange(-4000, 4000), rnd.randrange(-9000, -3000))
        _put(m, o, x, -15, z, rnd.choice((0, 0, buf)))
    m.set16(o + 6, rnd.randrange(0x10000))
    m.set16(o + 0x26, rnd.choice((0, rnd.randrange(0x10000))))
    m.set16(rec + 8, 3000)
    m.set16(rec + 0x18, rnd.choice((0, 1, 2, 2, 2)))
    m.set16(rec + 0x0A, rnd.choice((0xFFFF, 0, 40, 40)))
    m.set16(rec + 0x0C, rnd.choice((0, 2, 2)))
    m.set16(rec + 0x0E, rnd.choice((0xFFFF, 8, 9, 9, 0x0B, 0x0B)))
    m.set16(rec + 0x10, rnd.choice((0x11, 0x12, 0x13)))
    m.set8(rec, rnd.choice((0, 0x20, 0x80)))
    if m.u16(rec + 0x0E) == 0x0B:                               # a second Walrus alongside
        o2, rec2 = _vehicle(m, "Walrus", (bay + rnd.randrange(1, 4)) & 3)
        st = stunits.UnitState.from_ram(bytes(m.buf))
        vx, vz = st.yaw_vector(o, rnd.choice((300, 450, 700)))
        _put(m, o2, m.u32(o + 0x0A) + vx, m.u32(o + 0x0E), m.u32(o + 0x12) + vz, m.u32(o + 0x3C))
        m.set16(o2 + 6, w16(m.u16(o + 6) + 0x8000 + rnd.choice((0, 0, 3000, 9000))))
        m.set16(rec2 + 8, rnd.choice((0, 300)))
    _controls(m, rnd, o, rec, "Walrus")
    m.set8(POINTER_MODE, 0)
    m.set8(FIRE_KEYS, m.u8(FIRE_KEYS) | 1)
    m.set8(FIRE_INHIBIT, rnd.choice((0, 0, 0, 0xFF)))
    _fill_objects(m, rnd)


SCENARIOS = [scenario_manta_weapons, scenario_walrus_weapons, scenario_manta_flight, scenario_manta_carrier, scenario_manta_runway, scenario_lift,
             scenario_walrus, scenario_walrus_dock]


# ---- check_units: hangar commands -----------------------------------------------------------------
COMMANDS = {
    "cmd_manta_refuel": 0x1158A, "cmd_manta_defuel": 0x11676, "cmd_manta_repair": 0x1176A,
    "cmd_manta_replace": 0x117D4, "cmd_manta_prepare": 0x1182A, "cmd_manta_to_deck": 0x11894,
    "cmd_manta_to_hangar": 0x1242A, "cmd_manta_take_off": 0x12002, "cmd_manta_land": 0x120F4,
    "cmd_walrus_refuel": 0x11D4C, "cmd_walrus_defuel": 0x11DA4, "cmd_walrus_repair": 0x11E8A,
    "cmd_walrus_replace": 0x11EF0, "cmd_walrus_prepare": 0x11F3E, "cmd_walrus_launch": 0x1250E,
    "cmd_walrus_dock": 0x127C4,
}


def _perturb_hangar(m, rnd):
    """Selected bays in every state, tanks and stores at their limits, the lift, deck and
    dock busy or free, vehicles out near the carrier."""
    mb, wb = rnd.randrange(4), rnd.randrange(4)
    m.set32(0x48E, MANTA_RECS + REC * mb)
    m.set32(0x492, _objaddr(MANTA_OBJ + mb))
    m.set16(0xDCC, mb)
    m.set32(0x568, WALRUS_RECS + REC * wb)
    m.set32(0x56C, _objaddr(WALRUS_OBJ + wb))
    m.set16(0xDE4, wb)
    for base, n in ((MANTA_RECS, "Manta"), (WALRUS_RECS, "Walrus")):
        for k in range(4):
            r = base + REC * k
            m.set8(r, rnd.choice((0, 1, 3, 0x41)))
            m.set8(r + 1, rnd.choice((0, 0, 0, 1, 2, 3, 8, 8, 0xFF)))
            m.set16(r + 0x14, rnd.choice((0xFA0, 0xFA0, 1200)))
            fuel = rnd.choice((0, 0x7FFF, 0x7F00, 100, 3000))
            m.set16(r + (0x0A if n == "Manta" else 8), fuel)
            m.set8(r + 0x1C, rnd.choice((0, 0, 1)))
            m.set8(r + 0x1A, rnd.choice((0, 2, 3)) if n == "Walrus" else m.u8(r + 0x1A))
            m.set16(r + 0x10, rnd.choice((0, 0, 0x0A)) if n == "Manta" else m.u16(r + 0x10))
            m.set16(r + 0x26, rnd.choice((0, 6, 8, 9, 10, 11)) if n == "Walrus" else m.u16(r + 0x26))
            m.set16(r + 0x28, rnd.choice((0, 6, 7, 9)) if n == "Walrus" else m.u16(r + 0x28))
            m.set16(r + 0x0C, rnd.choice((0, 1, 3)) if n == "Manta" else m.u16(r + 0x0C))
            m.set16(r + 0x0E, rnd.choice((0, 4, 10)) if n == "Manta" else m.u16(r + 0x0E))
    for tank in (FUEL_MANTA, FUEL_WALRUS):
        m.set32(tank, rnd.choice((0, 100, 0x71C, FUEL_MAX, FUEL_MAX - 50, 0x50000)))
    m.set16(STOCK_MANTA, rnd.choice((0, 2)))
    m.set16(STOCK_WALRUS, rnd.choice((0, 2)))
    m.set8(0xB32, rnd.choice((0, 0xFF, 0xFF)))
    m.set8(MANTA_LANDING, rnd.choice((0, 0, 0xFF)))
    m.set8(WALRUS_DOCKING, rnd.choice((0, 0, 0xFF)))
    m.set16(LIFT_BAY, rnd.choice((0xFFFF, 0xFFFF, mb, (mb + 1) & 3)))
    m.set16(stunits.LIFT_STATE, rnd.choice((0, 0, 0, 1, 3)))
    m.set16(stunits.DOOR_STATE, rnd.choice((0, 0, 0, 2)))
    m.set8(0x122F, rnd.choice((0, 0, 0, 0xFF)))
    m.set8(0xD40, rnd.choice((0, 0, 0, 0xFF)))
    m.set32(COMMS_POD, rnd.choice((0, 0, _objaddr(2))))
    m.set16(0xDC0, rnd.choice((0, 0, 1, 2)))
    m.set16(0xDC2, rnd.choice((0, 0, 1)))
    for k in range(2, 10):
        o = _objaddr(k)
        lx = rnd.choice((rnd.randrange(-2000, 2000), rnd.randrange(-40000, 40000)))
        lz = rnd.choice((rnd.randrange(-9000, -1000), rnd.randrange(-20000, 20000), rnd.randrange(-40000, 40000)))
        x, z = _carrier_local(m, lx, lz)
        m.set32(o + 0x0A, x)
        m.set32(o + 0x12, z)
        m.set16(o + 0x1A, (k - 2) & 3)


def check_commands(files) -> bool:
    """Each hangar command against its original click handler (d2 = 2; a0 = the record for
    the replace entries) on perturbed captured states."""
    import random
    import check_sim
    import check_units
    from unicorn.m68k_const import UC_M68K_REG_A0
    rnd = random.Random(31)
    ok = True
    for name, addr in COMMANDS.items():
        runs = 0
        for f in files[::4]:
            for _ in range(8):
                m = stsim.Mem(bytearray(f.read_bytes()))
                _perturb_hangar(m, rnd)
                ram = bytes(m.buf)
                oracle = check_sim.Oracle(ram)
                state = stunits.UnitState.from_ram(ram)
                if name.endswith("replace"):
                    oracle.uc.reg_write(UC_M68K_REG_A0, m.u32(0x48E if "manta" in name else 0x568))
                check_units.call_regs(oracle, addr, d2=2)
                try:
                    getattr(state, name)()
                except NotImplementedError:
                    continue
                ref = oracle.ram()
                mine = bytearray(ref)
                state.to_ram(mine)
                lines = check_units.diff(mine, ref)
                runs += 1
                if lines:
                    print(f"{name}: MISMATCH from {f.name}")
                    for line in lines:
                        print("  " + line)
                    ok = False
                    break
            else:
                continue
            break
        else:
            print(f"{name}: OK ({runs} states)")
    return ok


CHECKS = [check_commands]
