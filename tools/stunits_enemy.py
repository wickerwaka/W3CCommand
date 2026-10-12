"""Unit layer, part of tools/stunits.py: the enemy carrier's close-range combat (states 10
$1B2D2 and 11 $1B49E, via the enemy_tactical/enemy_engaged hooks) and supply transfers by the
supply drone ($1F316, object 16; supply_drone_destroyed), with the Resources-screen command
that starts a transfer ($10E4A).

The mixin is combined into stunits.UnitState ahead of stunits.UnitCore, so methods defined
here replace the core's stubs and hooks. Use only `import stsim` / `import stunits` at module
level (stunits imports this module while it is still loading; read its constants at run time).

For tools/check_units.py, this module may define:
  ROUTINES  {"name": (original address, "method name")}: checked on every capture
  SCENARIOS [function(mem, rnd)]: perturbations run pass by pass against the original
  CHECKS    [function(files) -> bool]: any other checks (e.g. commands called with registers)
"""
import stsim
import stunits
from stsim import s16, s32, w16

ENEMY_CARRIER = stsim.ENEMY_CARRIER                 # object 19
PLAYER_CARRIER = stsim.OBJECTS + stsim.OBJECT_SIZE * 10
SUPPLY_DRONE = stsim.OBJECTS + stsim.OBJECT_SIZE * 16
E_COOLDOWN_MISSILE, E_COOLDOWN_FIGHTER = 0x11D4, 0x11CC
E_AIRBORNE, E_RECALL, E_RETREAT_SPEED = 0x11CE, 0x11D0, 0x11D6
S_ACTIVE, S_SECOND, S_DOCKING, S_STATE, S_TIMER, S_DONE, S_SPEED = 0xD3E, 0xD3F, 0xD40, 0xD42, 0xD44, 0xD46, 0xD48
FUEL_TRANSFERS = ((0xCFC, 0xD6C), (0xD00, 0xD70), (0xD04, 0xD74))   # table $1F938: (tank, queued)
TANK_MAX = 0xFFFE0

# State this part reads or writes that the other regions don't cover.
for _region in (("aircraft and AAV fuel", 0xD00, 8), ("enemy retreat speed", E_RETREAT_SPEED, 2)):
    if _region not in stunits.UNIT_REGIONS:
        stunits.UNIT_REGIONS.append(_region)

# Read-only windows copied by from_ram but never compared or written back.
# $1B18E reads an "owner" byte at $69B40 + (low word of the enemy carrier's x), a leftover
# register; this window makes that byte available without being simulation state.
LEFTOVER_WINDOW = (0x69B40 - 0x8000, 0x10000)


class EnemyMixin:
    @classmethod
    def from_ram(cls, ram: bytes):
        state = super().from_ram(ram)
        a, n = LEFTOVER_WINDOW
        state.mem.buf[a:a + n] = ram[a:a + n]       # populated regions there already match
        state.mem.buf[0:0x40] = ram[0:0x40]           # read by $1F4DA as a null island buffer
        return state

    # ---- enemy close-range combat ---------------------------------------------------------
    def enemy_engaged(self) -> None:
        """The game simply continues in state 10 (stsim stops the run here)."""

    def enemy_tactical(self, state: int) -> None:
        if state == 10:
            self.enemy_state10()
        else:
            self.enemy_state11()

    def enemy_state10(self) -> None:
        """$1B2D2: keep the stern to the player carrier and fire cruise missiles at it from the
        reserve; launch fighters; retreat (state 11) when weak or out of missiles."""
        m, e = self.mem, ENEMY_CARRIER
        if not self.player_near_enemy():
            return self._enemy_lost_contact()
        if m.s16(stsim.E_STRENGTH) < 3000 or m.u16(stsim.E_RESERVE) == 0:
            m.set16(stsim.E_STATE, 11)
            m.set8(E_RECALL, 0xFF)
            m.set16(E_RETREAT_SPEED, 0)
            return
        for a in (E_COOLDOWN_MISSILE, E_COOLDOWN_FIGHTER):
            if m.u16(a):
                m.set16(a, m.u16(a) - 1)
        c = PLAYER_CARRIER
        dx = s32(m.u32(c + 0x0A) - m.u32(e + 0x0A))
        dz = s32(m.u32(c + 0x12) - m.u32(e + 0x12))
        d = self.absl(dz)
        if self.absl(dx) > d:
            d = self.absl(dx)
        if d > 30000:
            m.set16(e + 6, self.turn_towards(m.u16(e + 0x1E), m.u16(e + 6), 200))
        else:
            want = w16(self.atan2(dx, dz) + 0x8000)
            m.set16(e + 6, self.turn_towards(want, m.u16(e + 6), 200))
            if self.absw(m.u16(e + 6) - want) > 0x800:
                return
            if m.u16(stsim.E_RESERVE) == 0 or m.u16(E_COOLDOWN_MISSILE):
                return
            a = self.find_free_object()
            if a is None:
                return
            self.init_world_object(a, 0x13)
            self.reset_pair_schedules()
            heading = m.u16(e + 6)
            m.set16(a + 6, heading + 0x8000)
            x, z = self.rotate_yaw(0, -200, heading)
            m.set32(a + 0x0A, m.u32(e + 0x0A) + x)
            m.set32(a + 0x12, m.u32(e + 0x12) + z)
            m.set32(a + 0x0E, 0x8C)
            m.set8(a + 0xE6, 0xFF)                       # never collides with its launcher
            m.set32(a + 0x1E, m.u32(c + 0x0A))
            m.set32(a + 0x22, m.u32(c + 0x12))
            m.set8(a + 0x1B, 0xFF)
            m.set16(stsim.E_RESERVE, m.u16(stsim.E_RESERVE) - 1)
            m.set16(E_COOLDOWN_MISSILE, 200)
        self._enemy_launch_fighter()

    def _enemy_launch_fighter(self) -> None:
        """$1B400: a fighter (type 33) from the reserve into a free slot 20-23. The airborne
        count $11CE is tested but never incremented (original bug)."""
        m, e = self.mem, ENEMY_CARRIER
        if m.u16(stsim.E_AIRCRAFT) == 0 or m.u16(E_COOLDOWN_FIGHTER) or m.s16(E_AIRBORNE) >= 4:
            return
        for k in range(4):
            a = self.obj(20 + k)
            if not m.u8(a + 1) & 1:
                break
        else:
            return
        m.set16(0x6F0, 20 + k)
        self.init_world_object(a, 0x21)
        self.reset_pair_schedules()                      # uses [$6EE], whatever it holds
        x, y, z = (m.u32(e + 0x0A + 4 * j) for j in range(3))
        y = y + 0xFA
        for j, v in enumerate((x, y, z)):
            m.set32(a + 0x0A + 4 * j, v)
        for j, v in enumerate((x, y + 0x320, z)):
            m.set32(a + 0x22 + 4 * j, v)
        m.set16(a + 0x18, 0x0A)
        m.set16(a + 0x38, 0x1388)
        m.set16(a + 0x16, 0x7D0)
        m.set16(a + 4, 0xC000)
        m.set16(stsim.E_AIRCRAFT, m.u16(stsim.E_AIRCRAFT) - 1)
        m.set16(E_COOLDOWN_FIGHTER, 0x1E)

    def _enemy_lost_contact(self) -> None:
        """$1B18E entered from state 10: hide the carrier and resolve its island. The owner
        byte comes from $69B40 + the low word of d0, which still holds the carrier's x from
        the proximity test (a leftover register, not the island index)."""
        m, e = self.mem, ENEMY_CARRIER
        m.set8(e + 1, m.u8(e + 1) & 0xFE)
        owner = m.u8(stsim.OWNERS + s16(m.u32(e + 0x0A)))
        if owner == 0:
            self.enemy_drop_builder(m.u16(stsim.E_TARGET))
        elif owner == 1:
            if m.u16(stsim.E_ISLAND) != stsim.NEMESIS and m.s16(stsim.E_STRENGTH) < 3000:
                m.set16(stsim.E_STATE, 1)
                return
            m.set16(stsim.E_STATE, 3)
            m.set16(stsim.B_POOL, 5000)
            m.set16(stsim.B_TIMER, 400)
            self.events.append(("attacked", m.u16(stsim.E_ISLAND)))
        else:
            m.set16(stsim.E_STATE, 2)

    def enemy_state11(self) -> None:
        """$1B49E: retreat: turn to the engage heading, then move away at about 250;
        once no player unit is near, back to state 1, hidden."""
        m, e = self.mem, ENEMY_CARRIER
        if not self.player_near_enemy():
            m.set16(stsim.E_STATE, 1)
            m.set8(e + 1, m.u8(e + 1) & 0xFE)
            m.set16(E_AIRBORNE, 0)
            return
        want = m.u16(e + 0x1E)
        m.set16(e + 6, self.turn_towards(want, m.u16(e + 6), 200))
        if m.u16(e + 6) != want:
            return
        # $1A18A with the operands swapped: 250 moves 2 towards the old speed, so from 0 the
        # speed is 248 on the first step and stays there (original bug).
        speed = self.turn_towards(m.u16(E_RETREAT_SPEED), 0xFA, 2)
        m.set16(E_RETREAT_SPEED, speed)
        x, z = self.yaw_vector(e, speed)
        m.set32(e + 0x0A, m.u32(e + 0x0A) + x)
        m.set32(e + 0x12, m.u32(e + 0x12) + z)

    # ---- supply transfers -------------------------------------------------------------------
    def supply_transfer_step(self) -> None:
        """$1F316: the transfer state machine while [$D3E] is set; otherwise the return-trip
        timer [$D44] counts seconds. [$D3F] (the tick's once-a-second flag) is cleared."""
        m = self.mem
        if m.u8(S_ACTIVE):
            st = m.u16(S_STATE)
            if st > 15:
                raise NotImplementedError(f"supply state {st}")
            self.SUPPLY_STATES[st](self)
        elif m.u16(S_TIMER) and m.u8(S_SECOND):
            m.set16(S_TIMER, m.u16(S_TIMER) - 1)
        m.set8(S_SECOND, 0)

    def _drone_height(self, v: int, scale: int, offset: int) -> int:
        """y = (v * scale) >> 16, rounded on bit 15, plus offset."""
        p = (v & 0xFFFF) * scale
        y = (p >> 16) + (1 if p & 0x8000 else 0)
        return s32(y + offset)

    def _bump(self) -> None:
        self.mem.set16(S_STATE, self.mem.u16(S_STATE) + 1)

    def _supply_rise(self) -> None:              # 0 $1F386
        m, a = self.mem, SUPPLY_DRONE
        v = w16(m.u16(a + 0x24) + 0x10)
        if v == 0x100:
            self._bump()
        m.set16(a + 0x24, v)
        m.set32(a + 0x0E, self._drone_height(v, 0x5A00, -60))

    def _supply_climb(self) -> None:             # 1 $1F3C0
        m, a = self.mem, SUPPLY_DRONE
        v = w16(m.u16(a + 0x24) + 0x20)
        if v == 0x200:
            m.set16(S_STATE, 5)
        m.set16(a + 0x24, v)
        m.set32(a + 0x0E, self._drone_height(w16(v - 0x100), 0x1E00, 30))

    def _supply_descend(self) -> None:           # 2 $1F400
        m, a = self.mem, SUPPLY_DRONE
        v = m.s16(a + 0x24)
        if v <= 0x100:
            return self._bump()
        v = w16(v - 0x20)
        if v == 0x100:
            self._bump()
        m.set16(a + 0x24, v)
        m.set32(a + 0x0E, self._drone_height(w16(v - 0x100), 0x1E00, 30))

    def _supply_land(self) -> None:              # 3 $1F44A
        m, a = self.mem, SUPPLY_DRONE
        v = m.s16(a + 0x24) - 0x10                  # subi.w; bgt (true difference, as N=V)
        if v <= 0:
            self._bump()
            v = 0
        m.set16(a + 0x24, v)
        m.set32(a + 0x0E, self._drone_height(v, 0x5A00, -60))

    def _supply_finish(self) -> None:            # 4 $1F482
        m, a = self.mem, SUPPLY_DRONE
        m.set8(S_ACTIVE, 0)
        m.set8(a + 1, m.u8(a + 1) & 0xFE)
        m.set16(S_TIMER, self._supply_flight_time())

    def _supply_flight_time(self) -> int:
        """$1F49A: Chebyshev distance to the stockpile island / 256, at least 300 (1 if the
        stockpile is invalid)."""
        m, a = self.mem, SUPPLY_DRONE
        i = m.s16(stsim.STOCKPILE)
        if i < 0:
            return 1
        isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i
        dx = self.absl(m.u32(isl + 2) - m.u32(a + 0x0A))
        dz = self.absl(m.u32(isl + 6) - m.u32(a + 0x12))
        d = ((dx if dx >= dz else dz) & 0xFFFFFFFF) >> 8
        return 0x12C if s16(d) < 0x12C else w16(d)

    @staticmethod
    def _box_side(m, o: int, buf: int) -> int:
        """$1F9A4: which sides of the island's box (buffer +$30/+$32) an object is beyond."""
        c = 0
        dx = s32(m.u32(o + 0x0A) - m.u32(buf + 2))
        hx = m.u16(buf + 0x30)
        if dx > hx:
            c |= 1
        if dx < -hx:
            c |= 2
        dz = s32(m.u32(o + 0x12) - m.u32(buf + 0x0A))
        hz = m.u16(buf + 0x32)
        if dz > hz:
            c |= 4
        if dz < -hz:
            c |= 8
        return c

    def _supply_fly(self) -> None:               # 5 $1F4DA
        """Fly to the carrier once both are beyond the same side of the island's box; within
        2500, start docking (state 7) in the carrier's frame."""
        m, a, c = self.mem, SUPPLY_DRONE, PLAYER_CARRIER
        # movea doesn't set flags: only the carrier's buffer is tested, and a drone without
        # one tests its box against address 0 (the exception vectors; original bug).
        cb, db = m.u32(c + 0x3C), m.u32(a + 0x3C)
        if not cb:
            m.set16(S_STATE, 2)
            self.events.append(("message", "Too far from island - drone returning", self._ping()))
            return
        if not self._box_side(m, c, db) & self._box_side(m, a, db):
            return
        dx = s32(m.u32(c + 0x0A) - m.u32(a + 0x0A))
        dz = s32(m.u32(c + 0x12) - m.u32(a + 0x12))
        d = self.absl(dz)
        if self.absl(dx) > d:
            d = self.absl(dx)
        if d < 0x9C4:
            if m.u8(0x122F) or m.u8(0x1233):
                return
            m.set8(S_DOCKING, 0xFF)
            m.set16(S_STATE, 7)
            self.set_matrix(w16(-m.u16(c + 6)))
            x, z = self.matrix_rotate(s32(m.u32(a + 0x0A) - m.u32(c + 0x0A)),
                                      s32(m.u32(a + 0x12) - m.u32(c + 0x12)))
            m.set16(a + 0x1C, x)
            m.set16(a + 0x1E, z)
            return
        heading = self.atan2(dx, dz)
        speed = 0x40 if self.absw(heading - m.u16(a + 6)) <= 0x400 else 0
        m.set16(S_SPEED, speed)
        m.set16(a + 6, self.turn_towards(heading, m.u16(a + 6), 0x100))
        vx, vz = self.yaw_vector(a, m.u16(S_SPEED))
        m.set32(a + 0x0A, m.u32(a + 0x0A) + vx)
        m.set32(a + 0x12, m.u32(a + 0x12) + vz)

    def _supply_idle(self) -> None:              # 6 $1F5FA
        pass

    def _supply_follow(self) -> None:
        """$1F66C: the drone's position from its offset (+$1C, +$1E) in the carrier's frame."""
        m, a, c = self.mem, SUPPLY_DRONE, PLAYER_CARRIER
        self.set_matrix(m.u16(c + 6))
        x, z = self.matrix_rotate(m.u16(a + 0x1C), m.u16(a + 0x1E))
        m.set32(a + 0x0A, m.u32(c + 0x0A) + x)
        m.set32(a + 0x12, m.u32(c + 0x12) + z)

    def _supply_slide(self, sign_from_offset: bool, invert: bool) -> None:
        """Turn to a side ($4000 off the carrier's heading) and slide sideways by 64."""
        m, a, c = self.mem, SUPPLY_DRONE, PLAYER_CARRIER
        off = m.s16(a + 0x1C)
        d1, d2 = 0x4000, 0x40
        neg = off < 0
        if invert:
            neg = not neg
        if neg:
            d1, d2 = w16(-d1), -d2
        want = w16(d1 + m.u16(c + 6))
        if self.absw(want - m.u16(a + 6)) > 0x400:
            d2 = 0
        m.set16(S_SPEED, d2)
        m.set16(a + 6, self.turn_towards(want, m.u16(a + 6), 0x100))
        m.set16(a + 0x1C, m.u16(a + 0x1C) + m.u16(S_SPEED))

    def _supply_clear_side(self) -> None:        # 7 $1F5FC
        m, a = self.mem, SUPPLY_DRONE
        if m.s16(a + 0x1E) <= -2000 or self.absw(m.u16(a + 0x1C)) >= 0x5DC:
            self._bump()
        else:
            self._supply_slide(True, False)
        self._supply_follow()

    def _supply_go_astern(self) -> None:         # 8 $1F69E
        m, a, c = self.mem, SUPPLY_DRONE, PLAYER_CARRIER
        if m.s16(a + 0x1E) <= -2000:
            self._bump()
        else:
            want = w16(m.u16(c + 6) + 0x8000)
            d2 = 0x40 if self.absw(want - m.u16(a + 6)) <= 0x400 else 0
            m.set16(S_SPEED, d2)
            m.set16(a + 6, self.turn_towards(want, m.u16(a + 6), 0x100))
            m.set16(a + 0x1E, m.u16(a + 0x1E) - m.u16(S_SPEED))
        self._supply_follow()

    def _supply_centre(self) -> None:            # 9 $1F6F8
        m, a = self.mem, SUPPLY_DRONE
        if self.absw(m.u16(a + 0x1C)) <= 0x40:
            self._bump()
        else:
            self._supply_slide(True, True)
        self._supply_follow()

    def _supply_align(self) -> None:             # 10 $1F764
        m, a, c = self.mem, SUPPLY_DRONE, PLAYER_CARRIER
        m.set16(a + 6, self.turn_towards(m.u16(c + 6), m.u16(a + 6), 0x100))
        if self.absw(m.u16(a + 6) - m.u16(c + 6)) <= 0x100:
            self._bump()
        self._supply_follow()

    def _supply_line_up(self) -> None:           # 11 $1F79E
        m, a, c = self.mem, SUPPLY_DRONE, PLAYER_CARRIER
        m.set16(a + 6, m.u16(c + 6))
        off = m.s16(a + 0x1C)
        v = self.absw(off)
        if v > 0x18:
            v -= 0x18
            m.set16(a + 0x1C, -v if off < 0 else v)
        else:
            m.set16(a + 0x1C, 0)
            m.set16(stunits.DOCKED_WALRUS, 4)
            m.set16(stunits.DOOR_STATE, 1)
            self.dock_attach()
            self._bump()
        self._supply_follow()

    def _supply_enter(self) -> None:             # 12 $1F7EA
        m, a, c = self.mem, SUPPLY_DRONE, PLAYER_CARRIER
        m.set16(a + 6, m.u16(c + 6))
        z = s16(m.u16(a + 0x1E) + 0x40)
        m.set16(a + 0x1E, z)
        if z >= -1000:
            self._bump()
            m.set16(S_TIMER, 0)
            self.events.append(("message", "Drone docked - transferring stores", self._ping()))
        self._supply_follow()

    def _supply_stores(self) -> None:            # 13 $1F84C
        """One store type per second: the queued quantity joins the carrier's on-board
        count (item record +8)."""
        m = self.mem
        k = m.u16(S_TIMER)
        q = 0xD96 + 2 * k
        moved = True
        if m.u16(q):
            if m.u8(S_SECOND):
                item = stsim.ITEMS + stsim.ITEM_SIZE * k
                m.set16(item + 8, m.u16(item + 8) + m.u16(q))
                m.set16(q, 0)
            else:
                moved = False
        if moved:
            k = w16(k + 1)
            m.set16(S_TIMER, k)
            if k == 0x0F:
                self._bump()
                m.set16(S_TIMER, 0)
        m.set16(SUPPLY_DRONE + 6, m.u16(PLAYER_CARRIER + 6))
        self._supply_follow()

    def _supply_fuel(self) -> None:              # 14 $1F8AE
        """One fuel type per second into its carrier tank, capped at $FFFE0."""
        m = self.mem
        k = m.u16(S_TIMER)
        if k > 2:
            raise NotImplementedError("supply fuel index out of the table")
        tank, queued = FUEL_TRANSFERS[k]
        moved = True
        if m.u32(queued):
            if m.u8(S_SECOND):
                v = s32(m.u32(tank) + m.u32(queued))
                v = TANK_MAX if v > TANK_MAX else 0 if v < 0 else v
                m.set32(tank, v)
                m.set32(queued, 0)
            else:
                moved = False
        if moved:
            k = w16(k + 1)
            m.set16(S_TIMER, k)
            if k == 3:
                self._bump()
                m.set16(S_TIMER, 0)
                m.set16(S_DONE, 1)
                self.events.append(("message", "Transfer complete", self._ping()))
        m.set16(SUPPLY_DRONE + 6, m.u16(PLAYER_CARRIER + 6))
        self._supply_follow()

    def _supply_leave(self) -> None:             # 15 $1F960
        m, a, c = self.mem, SUPPLY_DRONE, PLAYER_CARRIER
        m.set16(a + 6, m.u16(c + 6))
        z = s16(m.u16(a + 0x1E) - 0x40)
        m.set16(a + 0x1E, z)
        if z <= -2000:
            m.set16(S_STATE, 2)
            m.set8(a + 1, m.u8(a + 1) & ~4)
            m.set16(stunits.DOCKED_WALRUS, 0xFFFF)
            m.set16(stunits.DOOR_STATE, 3)
            self.dock_attach()
            m.set8(S_DOCKING, 0)
        self._supply_follow()

    SUPPLY_STATES = (_supply_rise, _supply_climb, _supply_descend, _supply_land, _supply_finish,
                     _supply_fly, _supply_idle, _supply_clear_side, _supply_go_astern,
                     _supply_centre, _supply_align, _supply_line_up, _supply_enter,
                     _supply_stores, _supply_fuel, _supply_leave)

    def supply_drone_destroyed(self, v: int) -> None:
        """$1C0CA's destroyed path and $1925C: the transfer is lost (queues emptied) and the
        next one can't start for 900 s."""
        m = self.mem
        m.set16(v, 0)
        self.events.append(("message", "Supply drone has been destroyed", self._ping()))
        if m.u16(stunits.DOCKED_WALRUS) == 4:
            m.set16(stunits.DOCKED_WALRUS, 0xFFFF)
            self.dock_attach()
        m.set8(S_ACTIVE, 0)
        m.set8(S_DOCKING, 0)
        m.buf[0xD96:0xD96 + 30] = bytes(30)
        for a in (0xD6C, 0xD70, 0xD74):
            m.set32(a, 0)
        m.set16(S_TIMER, 0x384)
        self.spawn_debris_world(v)

    def cmd_supply_transfer(self) -> bool:
        """$10E4A (Resources screen, new click): send the supply drone from the stockpile
        island with the queued stores and fuel. Returns False (with a message) when it can't:
        a transfer is running, the carrier isn't at the stockpile island, the drone is still
        returning ([$D44]), or nothing is queued."""
        m, c, a = self.mem, PLAYER_CARRIER, SUPPLY_DRONE
        if m.u8(S_ACTIVE):
            self.events.append(("message", "Transfer already in progress", self._ping()))
            return False
        buf = m.u32(c + 0x3C)
        if not buf or m.u8(buf + 1) != m.u16(stsim.STOCKPILE):
            self.events.append(("message", "Not at stockpile island", self._ping()))
            return False
        if m.u16(S_TIMER):
            self.events.append(("message", "Supply drone not available", self._ping()))
            return False
        queued = 0
        for k in range(15):
            queued |= m.u16(0xD96 + 2 * k)
        for q in (0xD6C, 0xD74, 0xD70):
            queued |= m.u32(q)
        if not queued:
            self.events.append(("message", "Nothing to transfer", self._ping()))
            return False
        self.init_world_object(a, 0x23)
        m.set16(stunits.NEW_INDEX, 0x10)
        self.reset_pair_schedules()
        m.set16(a + 0x38, 0x7D0)
        for axis, half in ((0x0A, 0x30), (0x12, 0x32)):
            origin = m.u32(buf + (2 if axis == 0x0A else 0x0A))
            d = s32(m.u32(c + axis) - origin)
            h = m.u16(buf + half)
            if h < d:
                p = h + 0x258
            elif -h > d:
                p = -h - 0x258
            else:
                p = 0
            m.set32(a + axis, p + origin)
        m.set32(a + 0x0E, -60)
        m.set16(a + 6, 0x8000)
        m.set16(a + 0x24, 0)
        m.set16(S_STATE, 0)
        m.set8(S_ACTIVE, 0xFF)
        m.set16(S_DONE, 0)
        self.events.append(("message", "Resupply drone launched", self._ping()))
        return True


ROUTINES = {"supply_transfer_step": (0x1F316, "supply_transfer_step")}


# ---- scenarios ------------------------------------------------------------------------------
def _enemy_alongside(m, rnd, state: int) -> None:
    c, e = PLAYER_CARRIER, ENEMY_CARRIER
    m.set8(stsim.AI_ENABLED, 0xFF)
    m.set16(stsim.E_STATE, state)
    m.set16(e, 0x9E03 | 0x200 if rnd.random() < 0.5 else 0x9E03)
    near = rnd.random() < 0.85
    for off in (0x0A, 0x12):
        span = rnd.choice((3000, 25000, 40000)) if near else 400000
        m.set32(e + off, m.u32(c + off) + rnd.randrange(-span, span))
    if not near and rnd.random() < 0.7:
        # $1B18E indexes the owner table with the low word of x: make it an island index
        m.set32(e + 0x0A, (m.u32(e + 0x0A) & 0xFFFF0000) | rnd.randrange(64))
    m.set32(e + 0x0E, 160)
    m.set16(e + 6, rnd.randrange(0x10000))
    m.set16(e + 0x1E, rnd.randrange(0x10000))
    m.set16(stsim.E_STRENGTH, rnd.choice((1000, 2999, 3000, 8000, 12000)))
    m.set16(stsim.E_RESERVE, rnd.choice((0, 1, 5, 12)))
    m.set16(stsim.E_AIRCRAFT, rnd.choice((0, 2, 8)))
    m.set16(E_COOLDOWN_MISSILE, rnd.choice((0, 0, 3, 200)))
    m.set16(E_COOLDOWN_FIGHTER, rnd.choice((0, 0, 3, 30)))
    m.set16(E_AIRBORNE, rnd.choice((0, 0, 3, 4, 0xFFFE)))
    m.set8(E_RECALL, rnd.choice((0, 0xFF)))
    m.set16(E_RETREAT_SPEED, rnd.choice((0, 100, 250)))
    m.set16(stsim.E_ISLAND, rnd.randrange(64))
    m.set16(stsim.E_TARGET, rnd.randrange(64))
    for off in range(0xC0, 0x140, 2):
        m.set16(e + off, m.u16(stunits.FRAME))
    m.set32(e + 0x3C, m.u32(c + 0x3C) if near and rnd.random() < 0.5 else 0)


def scenario_enemy_state10(m, rnd):
    """The enemy carrier in close combat (state 10): near or just beyond range, with or
    without missiles, fighters and strength."""
    _enemy_alongside(m, rnd, 10)


def scenario_enemy_state11(m, rnd):
    """The enemy carrier retreating (state 11)."""
    _enemy_alongside(m, rnd, 11)


def scenario_enemy_engage(m, rnd):
    """The enemy in a strategic state (2, 3, 4) next to a player unit: it engages and the
    game continues in state 10."""
    _enemy_alongside(m, rnd, rnd.choice((2, 3, 4)))
    for off in (0x0A, 0x12):
        m.set32(ENEMY_CARRIER + off, m.u32(PLAYER_CARRIER + off) + rnd.randrange(-20000, 20000))


def scenario_supply(m, rnd):
    """A supply transfer in progress, in any state, with stores and fuel queued."""
    c, a = PLAYER_CARRIER, SUPPLY_DRONE
    buf = m.u32(c + 0x3C)
    m.set16(stsim.STOCKPILE, m.u8(buf + 1) if rnd.random() < 0.9 else 0xFFFF)
    for k in range(15):
        m.set16(0xD96 + 2 * k, rnd.choice((0, 0, 3, 40)))
    for q in (0xD6C, 0xD70, 0xD74):
        m.set32(q, rnd.choice((0, 0x8000, 0x80000)))
    for tank in (0xCFC, 0xD00, 0xD04):
        m.set32(tank, rnd.choice((0, 0x50000, 0xFFF00)))
    st = rnd.randrange(16)
    m.set8(S_ACTIVE, 0xFF if st != 6 or rnd.random() < 0.5 else 0)
    m.set16(S_STATE, st)
    m.set16(S_TIMER, rnd.randrange(0, 15) if st == 13 else rnd.randrange(0, 3) if st == 14 else rnd.choice((0, 0, 5, 300)))
    m.set8(S_SECOND, rnd.choice((0, 0xFF)))
    m.set16(a, 0x9603)
    m.set16(a + 2, 0x23)
    for off, v in zip((0x30, 0x32, 0x34, 0x36), (150, 150, 5, 100)):
        m.set16(a + off, v)
    m.set16(a + 0x38, 2000)
    if st in (0, 1, 2, 3, 4, 5):
        bx, bz = m.u32(buf + 2), m.u32(buf + 0x0A)
        hx, hz = m.u16(buf + 0x30), m.u16(buf + 0x32)
        side = rnd.choice((-1, 1))
        m.set32(a + 0x0A, bx + side * (hx + 600))
        m.set32(a + 0x12, bz + rnd.randrange(-hz, hz))
        m.set16(a + 0x24, rnd.choice((0, 0x80, 0x100, 0x180, 0x200)))
        if st == 5 and rnd.random() < 0.5:
            m.set32(a + 0x0A, m.u32(c + 0x0A) + rnd.randrange(-6000, 6000))
            m.set32(a + 0x12, m.u32(c + 0x12) + rnd.randrange(-6000, 6000))
        m.set32(a + 0x3C, buf if rnd.random() < 0.9 else 0)
    else:
        m.set16(a + 0x1C, rnd.choice((0, 30, -500, 1400, -1600)))
        m.set16(a + 0x1E, rnd.choice((-2100, -1500, -1000, -900, 0)))
        m.set32(a + 0x3C, buf)
    m.set16(a + 6, rnd.randrange(0x10000))
    for off in range(0xC0, 0x140, 2):
        m.set16(a + off, m.u16(stunits.FRAME))
    m.set8(0x122F, rnd.choice((0, 0, 0xFF)))
    m.set8(0x1233, rnd.choice((0, 0, 0xFF)))
    m.set16(stunits.DOOR_STATE, rnd.choice((0, 0, 1, 2)))
    m.set16(stunits.DOCKED_WALRUS, rnd.choice((0xFFFF, 0xFFFF, 4)))


SCENARIOS = [scenario_enemy_state10, scenario_enemy_state11, scenario_enemy_engage, scenario_supply]


# ---- checks -----------------------------------------------------------------------------------
def _oracle_call(oracle, addr: int, **regs) -> None:
    from unicorn.m68k_const import (UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A4,
                                    UC_M68K_REG_D0, UC_M68K_REG_D2, UC_M68K_REG_D3)
    names = {"a0": UC_M68K_REG_A0, "a1": UC_M68K_REG_A1, "a4": UC_M68K_REG_A4,
             "d0": UC_M68K_REG_D0, "d2": UC_M68K_REG_D2, "d3": UC_M68K_REG_D3}
    for k, v in regs.items():
        oracle.uc.reg_write(names[k], v & 0xFFFFFFFF)
    oracle.call(addr)


def check_supply_command(files: list, n: int = 400) -> bool:
    """cmd_supply_transfer against $10E4A (a new click, d2 = 2) on perturbed captures: at or
    away from the stockpile island, with or without queued stores, drone timer, transfer."""
    import random
    import check_sim
    import check_units
    rnd = random.Random(21)
    for i in range(n):
        m = stsim.Mem(bytearray(rnd.choice(files).read_bytes()))
        buf = m.u32(PLAYER_CARRIER + 0x3C)
        m.set16(stsim.STOCKPILE, m.u8(buf + 1) if rnd.random() < 0.8 else rnd.randrange(64))
        if rnd.random() < 0.2:
            m.set32(PLAYER_CARRIER + 0x3C, 0)
        for k in range(15):
            m.set16(0xD96 + 2 * k, rnd.choice((0, 0, 0, 0, 7)))
        for q in (0xD6C, 0xD70, 0xD74):
            m.set32(q, rnd.choice((0, 0, 0, 0x8000)))
        m.set16(S_TIMER, rnd.choice((0, 0, 0, 9)))
        m.set8(S_ACTIVE, rnd.choice((0, 0, 0, 0xFF)))
        for axis in (0x0A, 0x12):
            m.set32(PLAYER_CARRIER + axis, m.u32(buf + (2 if axis == 0x0A else 0x0A)) + rnd.randrange(-40000, 40000))
        ram = bytes(m.buf)
        oracle = check_sim.Oracle(ram)
        _oracle_call(oracle, 0x10E4A, d2=2)
        state = stunits.UnitState.from_ram(ram)
        state.cmd_supply_transfer()
        mine = bytearray(oracle.ram())
        state.to_ram(mine)
        lines = check_units.diff(mine, oracle.ram())
        if lines:
            print(f"supply command: MISMATCH on case {i}")
            for line in lines:
                print("  " + line)
            return False
    print(f"supply command: OK ({n} cases)")
    return True


def check_supply_drone_hit(files: list, n: int = 300) -> bool:
    """A hit on the supply drone (collision_response $1BD30 with a1 = object 16) against the
    original: damage, or destruction with the transfer cancelled ($1925C)."""
    import random
    import check_sim
    import check_units
    rnd = random.Random(22)
    for i in range(n):
        m = stsim.Mem(bytearray(rnd.choice(files).read_bytes()))
        scenario_supply(m, rnd)
        a = SUPPLY_DRONE
        m.set16(a + 0x38, rnd.choice((100, 500, 1500, 2000)))
        h = stsim.OBJECTS + stsim.OBJECT_SIZE * 30
        m.buf[h:h + 0x40] = bytes(0x40)
        m.set16(h, 0x8703)
        m.set16(h + 2, rnd.choice((16, 17, 19, 34)))
        m.set16(h + 0x34, rnd.choice((0, 2, 4, 8, 11)))
        ram = bytes(m.buf)
        oracle = check_sim.Oracle(ram)
        _oracle_call(oracle, 0x1BD30, a1=a, a4=h)
        state = stunits.UnitState.from_ram(ram)
        state.collision_response(h, a)
        mine = bytearray(oracle.ram())
        state.to_ram(mine)
        lines = check_units.diff(mine, oracle.ram())
        if lines:
            print(f"supply drone hit: MISMATCH on case {i}")
            for line in lines:
                print("  " + line)
            return False
    print(f"supply drone hit: OK ({n} cases)")
    return True


def check_supply_step(files: list, n: int = 3000) -> bool:
    """One supply_transfer_step against $1F316 from every transfer state, including the
    edges the multi-pass runs rarely reach (no island buffer, the stockpile flight time)."""
    import random
    import check_sim
    import check_units
    rnd = random.Random(23)
    for i in range(n):
        m = stsim.Mem(bytearray(rnd.choice(files).read_bytes()))
        scenario_supply(m, rnd)
        for o in (PLAYER_CARRIER, SUPPLY_DRONE):
            if rnd.random() < 0.15:
                m.set32(o + 0x3C, 0)
        if rnd.random() < 0.3:
            m.set16(SUPPLY_DRONE + 0x24, rnd.choice((0, 0x10, 0x100, 0x110, 0x1E0, 0x8005, 0xFFF0)))
        if rnd.random() < 0.3:
            m.set16(SUPPLY_DRONE + 0x1C, rnd.choice((0x8000, 0x7FFF, 24, -24, 25, -25, 64, -64, 65, 1500, -1499)))
        ram = bytes(m.buf)
        oracle = check_sim.Oracle(ram)
        oracle.call(0x1F316)
        state = stunits.UnitState.from_ram(ram)
        state.supply_transfer_step()
        mine = bytearray(oracle.ram())
        state.to_ram(mine)
        lines = check_units.diff(mine, oracle.ram())
        if lines:
            print(f"supply step: MISMATCH on case {i}")
            for line in lines:
                print("  " + line)
            return False
    print(f"supply step: OK ({n} cases)")
    return True


def check_enemy_states(files: list, n: int = 3000) -> bool:
    """One enemy_ai pass ($1B0xx via stsim's enemy_ai) in states 10 and 11 against the
    original routines $1B2D2 / $1B49E called directly."""
    import random
    import check_sim
    import check_units
    rnd = random.Random(24)
    for i in range(n):
        m = stsim.Mem(bytearray(rnd.choice(files).read_bytes()))
        st = rnd.choice((10, 11))
        _enemy_alongside(m, rnd, st)
        if rnd.random() < 0.3:
            m.set16(stunits.NEW_INDEX, rnd.randrange(64))
        for k in range(20, 24):
            if rnd.random() < 0.4:
                a = stsim.OBJECTS + stsim.OBJECT_SIZE * k
                m.set8(a + 1, m.u8(a + 1) | 1)
        ram = bytes(m.buf)
        oracle = check_sim.Oracle(ram)
        oracle.call(0x1B2D2 if st == 10 else 0x1B49E)
        state = stunits.UnitState.from_ram(ram)
        state.enemy_tactical(st)
        mine = bytearray(oracle.ram())
        state.to_ram(mine)
        lines = check_units.diff(mine, oracle.ram())
        if lines:
            print(f"enemy state {st}: MISMATCH on case {i}")
            for line in lines:
                print("  " + line)
            return False
    print(f"enemy states 10/11: OK ({n} cases)")
    return True


def _victory_start(seed: int, rnd, kind: str) -> bytes:
    """A strategy game from the title image (new_game is checked on its own), set up for the
    enemy carrier's destruction:
      "bombard": NEMESIS player-held and fortified, a weak enemy assaulting it (return fire
                 is the only way the bombardment destroys it: elsewhere a weak enemy can't
                 lose strength);
      "hit":     island 1 player-held, the player carrier 150000 off it; the enemy is sent
                 there at pass 100, engages (state 10, fighters out), and a player cruise
                 missile is placed beside it later (see _victory_inject)."""
    from stgfx import ROOT
    import stunits_setup
    ram = (ROOT / "emu" / "out" / "title" / "ram.bin").read_bytes()
    r = stunits_setup.seeded(ram, True, 0x2468ACE1 + seed)
    state = stunits.UnitState.from_ram(bytes(r))
    state.new_game(True)
    state.new_game_screen()
    m = state.mem
    i = stsim.NEMESIS if kind == "bombard" else 1
    m.set8(stsim.OWNERS + i, 1)
    slots = stsim.Island(m, i).slots[1:]
    for s in slots:
        if s.type in (stsim.T_COMMAND_CENTRE, stsim.T_COMMAND_CENTRE_CONSTRUCTION):
            s.type = s.value = 0
    free = [s for s in slots if s.type == 0]
    free[0].type, free[0].value = stsim.T_COMMAND_CENTRE, 2560
    if kind == "bombard":
        for s in free[1:rnd.randrange(3, 9)]:
            s.type, s.value = rnd.choice((0x25, 0x25, 0x26, 0x27, 0x28)), 4000
            s.x, s.z = rnd.randrange(-3000, 3000), rnd.randrange(-3000, 3000)
        m.set16(stsim.E_STATE, 3)
        m.set16(stsim.E_ISLAND, i)
        m.set16(stsim.E_STRENGTH, rnd.randrange(100, 3000))
        m.set16(stsim.E_AIRCRAFT, rnd.randrange(9))
        m.set16(stsim.E_RESERVE, rnd.randrange(13))
        m.set16(stsim.B_POOL, rnd.randrange(5001))
        m.set16(stsim.B_TIMER, rnd.randrange(1, 30))
        a = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i
        m.set32(ENEMY_CARRIER + 0x0A, m.u32(a + 2))
        m.set32(ENEMY_CARRIER + 0x12, m.u32(a + 6))
        if rnd.random() < 0.5:
            m.set16(stsim.STOCKPILE, i)
    else:
        m.set32(PLAYER_CARRIER + 0x0A, (-7224304 + rnd.randrange(-20000, 20000)) & 0xFFFFFFFF)
        m.set32(PLAYER_CARRIER + 0x12, (-6116864 + rnd.randrange(-20000, 20000)) & 0xFFFFFFFF)
    state.network_invalidate()
    out = bytearray(r)
    state.to_ram(out)
    return bytes(out)


def _victory_inject(ram: bytearray, rnd) -> None:
    """A player cruise missile (type 19, as the carrier's SSM launches) 150-400 beyond the
    engaged enemy carrier, heading for it, and the enemy's strength low enough for its blast."""
    m = stsim.Mem(ram)
    m.set16(stsim.E_STRENGTH, rnd.randrange(1, 200))
    k = next(k for k in range(63, 23, -1) if not m.u8(stsim.OBJECTS + stsim.OBJECT_SIZE * k + 1) & 1)
    o = stsim.OBJECTS + stsim.OBJECT_SIZE * k
    ram[o:o + 0x40] = bytes(0x40)
    m.set16(o, 0x9703 | 0x400)
    m.set16(o + 2, 19)
    for off, v in zip((0x30, 0x32, 0x34, 0x36), (150, 150, 3, 150)):
        m.set16(o + off, v)
    ex, ez = m.u32(ENEMY_CARRIER + 0x0A), m.u32(ENEMY_CARRIER + 0x12)
    m.set32(o + 0x0A, ex + rnd.randrange(150, 400))
    m.set32(o + 0x12, ez)
    m.set32(o + 0x0E, 150)
    m.set32(o + 0x3C, m.u32(ENEMY_CARRIER + 0x3C))
    m.set16(o + 6, 0xC000)
    m.set8(o + 0x1B, 255)
    m.set32(o + 0x1E, ex)
    m.set32(o + 0x22, ez)
    for off in range(0xC0, 0x140, 2):
        m.set16(o + off, m.u16(stunits.FRAME))


def check_enemy_destroyed(files: list, after: int = 3000) -> bool:
    """The enemy carrier destroyed, end to end: ui_frame + game_frame on the model against
    the original's ui_frame and game_main loop, pass by pass, through the destruction (by
    the bombardment's return fire at a player-held NEMESIS, $1B728, or by a hit, $1C0F4,
    with its fighters airborne), the end timer, screen 35, YES (continue without the enemy,
    `after` passes) or NO (result 4, game over)."""
    import random
    import stunits_controls as sc
    ok, runs = True, []
    for seed, kind, yes in ((1, "bombard", 1), (2, "bombard", 1), (3, "bombard", 0),
                            (4, "hit", 1), (5, "hit", 1), (6, "hit", 0)):
        rnd = random.Random(seed)
        ram = _victory_start(seed, rnd, kind)
        state = stunits.UnitState.from_ram(ram)
        oracle = sc._switch_oracle(ram)
        phase, p, cont_at, end, label = "fight", 0, None, None, f"enemy destroyed ({kind}, seed {seed})"
        while not (phase == "after" and p >= end):
            if kind == "hit" and p == 100:              # send the enemy to island 1
                for a, v in ((stsim.E_STATE, 5), (stsim.E_TARGET, 1), (stsim.E_TIMER, 10)):
                    state.mem.set16(a, v)
                    oracle.uc.mem_write(a, v.to_bytes(2, "big"))
            if kind == "hit" and p == 400:
                if state.mem.u16(stsim.E_STATE) != 10:
                    print(f"{label}: MISMATCH (setup: the enemy hasn't engaged)")
                    return False
                ref = bytearray(oracle.ram())
                _victory_inject(ref, rnd)
                oracle.uc.mem_write(0, bytes(ref))
                state = stunits.UnitState.from_ram(bytes(ref))
            if phase == "won" and p == cont_at:
                res = state.cmd_continue(yes)
                sc.SWITCH_COMMANDS["cmd_continue"][0](oracle, state, (yes,), res)
                phase, end = "after", p + (after if yes else 50)
            if p % 8 == 0:
                oracle.uc.mem_write(0x8FD, b"\xff")
                state.mem.set8(0x8FD, 0xFF)
            try:
                state.ui_frame()
                if state.mem.u16(stunits.SCREEN) not in sc.SCREEN_INPUT:
                    state._vec = None       # tools/export_sim_vectors.py: stop recording (the stub is check-only)
                with sc._stub_inputs():
                    state.game_frame()
            except sc.STOP as e:
                print(f"{label}: stopped at pass {p}: {e or type(e).__name__}")
                return False
            sc._call(oracle, sc.UI_FRAME)
            sc.oracle_game_frame(oracle)
            if not sc._compare(state, oracle, f"{label}, pass {p} ({phase})"):
                return False
            if phase == "fight" and any(e[0] == "game over" and e[1] == 35 for e in state.events):
                phase, cont_at = "won", p + 5
            state.events.clear()
            p += 1
            if phase == "fight" and p > 6000:
                print(f"{label}: MISMATCH (setup: no destruction in 6000 passes)")
                return False
        m = state.mem
        ok &= not m.u8(stsim.AI_ENABLED) and (m.u16(sc.RESULT) == 4) != bool(yes)
        runs.append(p)
    print(f"enemy destroyed: {'OK' if ok else 'MISMATCH'} ({len(runs)} games, {sum(runs)} passes: "
          f"bombardment and hit, continue and quit)")
    return ok


CHECKS = [check_supply_command, check_supply_drone_hit, check_supply_step, check_enemy_states,
          check_enemy_destroyed]
