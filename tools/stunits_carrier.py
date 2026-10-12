"""Unit layer, part of tools/stunits.py: the carrier's own weapons.

- carrier_laser_turret_step ($1EDFC) in full, including its own screen ([$718] = 8): mouse
  aiming, firing type-16 bolts, heat and overheating damage.
- UI commands (the original runs these from panel click handlers; d2 = 2, a fresh click):
    cmd_launch_flare           $1338C  decoy flare (objects 17/18, item 10)
    cmd_launch_recon_drone     $134C2  viewing drone (object 15, item 11)
    cmd_fire_ssm               $13598  surface-to-surface missile (item 2) at the point under
                                       the pointer in the viewing drone's picture
    cmd_launch_defence_drone   $10738  one carrier defence drone (objects 11-14, item 6)
    cmd_recall_defence_drones  $1088C  start the recall/launch sequence ($122F)
  Messages become events; sounds are dropped (they write no simulation state).

The mixin is combined into stunits.UnitState ahead of stunits.UnitCore, so methods defined
here replace the core's stubs and hooks. Use only `import stsim` / `import stunits` at module
level (stunits imports this module while it is still loading; read its constants at run time).

For tools/check_units.py, this module may define:
  ROUTINES  {"name": (original address, "method name")}: checked on every capture
  SCENARIOS [function(mem, rnd)]: perturbations run pass by pass against the original
  CHECKS    [function(files) -> bool]: any other checks (e.g. commands called with registers)
"""
import stsim  # noqa: F401
import stunits  # noqa: F401
from stsim import s16, s32, w16

# State these commands read or write that UnitCore doesn't list yet.
stunits.UNIT_REGIONS += [
    ("turret fire key $88C", 0x88C, 1),
    ("pointer position", 0x474, 4),             # $474/$476: aiming the SSM in the drone view
    ("ssm view $4770", 0x4770, 0x10),           # drone view panel: +8 zoom, +$C/+$E centre
    ("ssm armed $1160", 0x1160, 2),
    ("selected drone station $698", 0x698, 2),
]
stunits.UNIT_INPUT_REGIONS |= {"pointer position", "ssm view $4770", "ssm armed $1160"}

TURRET_SCREEN = 8
OBJ_RECON, OBJ_FLARES = 15, (17, 18)
STOCK_FLARES, STOCK_RECON, STOCK_SSM, STOCK_DRONES = 0xC82, 0xC9C, 0xBB2, 0xC1A


class CarrierMixin:
    # ---- laser turret ($1EDFC) ------------------------------------------------------------
    def carrier_laser_turret_step(self) -> None:
        """$1EDFC: cooldown $93E and heat $6C8 every pass; on the turret screen, fire on
        $88C or a left click in mouse-steer mode, then take the aim rates from the mouse;
        then the rates self-centre and integrate into elevation $6CA and heading $6CC."""
        m = self.mem
        if m.u16(0x93E):
            m.set16(0x93E, m.u16(0x93E) - 1)
        h = m.u16(0x6C8)
        if h:
            h = s16(h - 16)
            m.set16(0x6C8, 0 if h < 0 else h)
        if m.u16(stunits.SCREEN) == TURRET_SCREEN:
            self._turret_screen()
        for bit, rate in ((0x80, 0x6CE), (0x40, 0x6D0)):
            if m.u8(0x6D5) & bit:
                if m.u16(rate) == 0:
                    m.set8(0x6D5, m.u8(0x6D5) & ~bit)
                else:
                    m.set16(rate, self.self_centre(m.u16(rate)))
        e = s16(-self.quantise(m.u16(0x6D0)) + m.u16(0x6CA))
        m.set16(0x6CA, 0x1F4 if e > 0x1F4 else -16000 if e < -16000 else e)
        m.set16(0x6CC, m.u16(0x6CC) + 2 * self.quantise(m.u16(0x6CE)))

    def _turret_screen(self) -> None:
        m = self.mem
        if not m.u16(0x93E):
            fire = bool(m.u8(0x88C))
            if not fire and not m.u8(stunits.POINTER_MODE):
                fire = bool(m.u8(0x881) & 1)
                m.set8(0x881, m.u8(0x881) & 0xFE)
            if fire:
                self._turret_fire()
        if not m.u8(0xB12):                       # observation turret broken: self-centre
            m.set8(0x6D5, m.u8(0x6D5) | 0xC0)
            return
        if m.u8(stunits.POINTER_MODE):
            return
        m.set8(0x6D5, m.u8(0x6D5) & 0x3F)
        for rate, delta in ((0x6CE, stunits.MOUSE_DX), (0x6D0, stunits.MOUSE_DX + 2)):
            r = s16(m.u16(rate) + m.u16(delta))
            m.set16(delta, 0)
            m.set16(rate, 255 if r > 255 else -255 if r < -255 else r)

    def _turret_fire(self) -> None:
        m = self.mem
        m.set8(0x88C, 0)
        if m.u16(0xB1A) == 0:
            self.events.append(("message", "No laser"))
            return
        a = self.find_free_object()
        if a is None:
            return
        m.set16(0x93E, 5)
        self.init_world_object(a, 0x10)
        self.reset_pair_schedules()
        heat = w16(m.u16(0x6C8) + 0x200)
        if m.u16(0xB1A) != 0x100:
            heat = w16(heat + 0x200)
        if s16(heat) > 0x3FFF:
            heat = 0x3FFF
        m.set16(0x6C8, heat)
        if heat > 0x3000:
            self._sub_floor(0xB14, 100)
        # The bolt ignores the carrier, and anything on the lift or in the dock, for 20 passes.
        f = (m.u16(stunits.FRAME) + 0x14) & 0x7FFF
        m.set16(a + 0xD4, f)
        for ptr, base in ((0x6D6, 2), (0x6DA, 6)):
            o = m.u32(ptr)
            if o:
                m.set16(a + 0xC0 + 2 * w16(m.u16(o + 0x1A) + base), f)
        c = stunits.CARRIER
        m.set16(a + 8, 0)
        m.set16(a + 6, m.u16(c + 6) + m.u16(0x6CC))
        m.set16(a + 4, m.u16(0x6CA))
        x, z = self.rotate_yaw(0, -0x15E, m.u16(c + 6))
        m.set32(a + 0x0A, m.u32(c + 0x0A) + x)
        m.set32(a + 0x12, m.u32(c + 0x12) + z)
        m.set32(a + 0x0E, 0x14A)
        m.set8(a + 0x1B, 0x4B)

    # ---- commands ---------------------------------------------------------------------------
    def cmd_launch_flare(self) -> None:
        """$1338C: a decoy flare from the carrier (needs flares on board, the reload timer
        $1150 at 0 and the flare launcher working)."""
        m = self.mem
        if not m.u16(STOCK_FLARES):
            return self.events.append(("message", "No decoy flares"))
        if m.u16(0x1150):
            return self.events.append(("message", "Flare launcher busy"))
        if not m.u8(0xB22):
            return self.events.append(("message", "Flare launcher too damaged"))
        a, k = self.obj(OBJ_FLARES[0]), OBJ_FLARES[0]
        if m.u8(a + 1) & 1:
            a, k = self.obj(OBJ_FLARES[1]), OBJ_FLARES[1]
            if m.u8(a + 1) & 1:
                return self.events.append(("message", "Flare launcher busy"))
        m.set16(stunits.NEW_INDEX, k)
        self.init_world_object(a, 0x1E)
        self.reset_pair_schedules()
        c = stunits.CARRIER
        m.set32(a + 0x0A, m.u32(c + 0x0A))
        m.set32(a + 0x0E, 0x1F4)
        m.set32(a + 0x12, m.u32(c + 0x12))
        m.set16(a + 0x22, 0xFA)
        m.set16(a + 0x20, 0xC8)
        m.set16(a + 6, self.rand())
        m.set32(0x1154, a)
        self.events.append(("message", "Decoy flare launched"))
        m.set16(STOCK_FLARES, m.u16(STOCK_FLARES) - 1)
        m.set16(0x1152, m.u16(0x1152) + 1)
        m.set16(0x1150, 0x32)

    def cmd_launch_recon_drone(self) -> None:
        """$134C2: the viewing drone (object 15), launched astern."""
        m = self.mem
        if not m.u16(STOCK_RECON):
            return self.events.append(("message", "No Recon drone available"))
        a = self.obj(OBJ_RECON)
        if m.u8(a + 1) & 1:
            return self.events.append(("message", "Flare launcher busy"))
        m.set16(stunits.NEW_INDEX, OBJ_RECON)
        self.init_world_object(a, 0x20)
        self.reset_pair_schedules()
        c = stunits.CARRIER
        m.set32(a + 0x0A, m.u32(c + 0x0A))
        m.set32(a + 0x0E, 0x1F4)
        m.set32(a + 0x12, m.u32(c + 0x12))
        m.set16(a + 6, m.u16(c + 6) + 0x8000)
        m.set16(a + 4, 0x4000)
        m.set16(a + 0x22, 0xFA)
        m.set16(a + 0x24, 0xC8)
        m.set16(a + 0x20, 0x12C)
        self.events.append(("message", "Recon drone launched"))
        m.set16(STOCK_RECON, m.u16(STOCK_RECON) - 1)

    def cmd_fire_ssm(self) -> None:
        """$13598: a cruise missile (type 19) at the point under the pointer in the
        viewing drone's picture (pointer $474/$476 relative to the view panel at $4770,
        rotated by the drone's heading and scaled by its altitude and the zoom)."""
        m = self.mem
        if not m.u8(0x1160):
            return
        if not m.u16(STOCK_SSM):
            return self.events.append(("message", "No missiles"))
        if m.u16(0x115E):
            return self.events.append(("message", "Flare launcher busy"))
        if not m.u8(0xB2A):
            return self.events.append(("message", "Missile system too damaged"))
        d = self.obj(OBJ_RECON)
        if not m.u8(d + 1) & 1:
            return
        x = w16((m.u16(0x474) - m.u16(0x477C)) * 4)
        z = w16(-w16(m.u16(0x476) - m.u16(0x477E)) * 4)
        x, z = self.rotate_yaw(x, z, m.u16(d + 6))
        alt = m.s16(d + 0x10)
        shift = (m.u16(0x4778) + 2) & 63
        tx = s32(s32(x * alt) >> shift)                   # muls.w, asr.l by a register
        tz = s32(s32(z * alt) >> shift)
        tx = s32(tx + m.u32(d + 0x0A))
        tz = s32(tz + m.u32(d + 0x12))
        c = stunits.CARRIER
        if not abs(s32(tx - m.u32(c + 0x0A))) > 0x7D0 and abs(s32(tz - m.u32(c + 0x12))) < 0x7D0:
            return self.events.append(("message", "Target too close"))
        m.set32(0xA80, tx)
        m.set32(0xA84, tz)
        a = self.find_free_object()
        if a is None:
            return
        self.init_world_object(a, 0x13)
        self.reset_pair_schedules()
        hd = m.u16(c + 6)
        m.set16(a + 6, hd + 0x8000)
        ox, oz = self.rotate_yaw(0, -0xC8, hd)
        m.set32(a + 0x0A, m.u32(c + 0x0A) + ox)
        m.set32(a + 0x12, m.u32(c + 0x12) + oz)
        m.set32(a + 0x0E, 0x8C)
        m.set16(a + 0xD4, (m.u16(stunits.FRAME) + 5) & 0x7FFF)
        m.set32(a + 0x1E, tx)
        m.set32(a + 0x22, tz)
        # Quirk: meant for the missile's life, this writes the CARRIER's +$1B (a4 is still the
        # carrier). The missile's life starts at 0 and its plain subq wraps it to 255.
        m.set8(c + 0x1B, 0xFF)
        m.set16(STOCK_SSM, m.u16(STOCK_SSM) - 1)
        m.set16(0x115E, 0x32)
        self.events.append(("message", "Missile launched"))

    def _drone_station_clash(self, rec: int, x: int, z: int) -> bool:
        """$1055C: another active station's target within sqrt($24000) (about 384)."""
        m = self.mem
        for k in range(4):
            r = 0x65E + 14 * k
            if r == rec or not m.u8(r + 1) & 1:
                continue
            dz = s16(m.u16(r + 0x0C) - z)
            dx = s16(m.u16(r + 0x0A) - x)
            if dz * dz + dx * dx < 0x24000:
                return True
        return False

    def cmd_launch_defence_drone(self) -> None:
        """$10738: one defence drone to a free station; its target slides back by $180
        until it's clear of the other stations."""
        m = self.mem
        if m.u8(0x122F):
            return self.events.append(("message", "Drone recall in progress"))
        if m.u8(0x1233):
            return self.events.append(("message", "Dock busy"))
        if m.u8(0xD40):
            return self.events.append(("message", "Dock busy"))
        if not m.u16(STOCK_DRONES):
            return self.events.append(("message", "No drones"))
        k = next((j for j in range(4) if not m.u8(0x65E + 14 * j + 1) & 1), None)
        if k is None:
            return self.events.append(("message", "All drones launched"))
        rec = 0x65E + 14 * k
        m.set16(rec, 1)
        m.set16(rec + 2, 0)
        m.set16(rec + 4, 0xFA24)
        if self._drone_station_clash(rec, m.u16(rec + 0x0A), m.u16(rec + 0x0C)):
            z = 0xFA24
            while True:
                for o, v in ((0x0A, 0), (0x0C, z), (2, 0), (4, z)):
                    m.set16(rec + o, v)
                if not self._drone_station_clash(rec, 0, z):
                    break
                z = w16(z - 0x180)
            self.events.append(("message", "Drone station moved"))
        m.set16(STOCK_DRONES, m.u16(STOCK_DRONES) - 1)
        m.set16(stunits.NEW_INDEX, 11 + k)
        a = self.obj(11 + k)
        self.init_world_object(a, 10 + k)
        self.reset_pair_schedules()
        m.set16(a + 0x1A, k)

    def cmd_recall_defence_drones(self) -> None:
        """$1088C: start the sequence ($122F) that brings the drones back to the deck."""
        m = self.mem
        if not m.u16(0x65C):
            return self.events.append(("message", "No drones launched"))
        if m.u8(0x122F):
            return self.events.append(("message", "Drone recall in progress"))
        if m.u8(0x1233):
            return self.events.append(("message", "Dock busy"))
        if m.u8(0xD40):
            return self.events.append(("message", "Dock busy"))
        m.set8(0x122F, 0xFF)
        m.set16(0x69A, 0)
        z = 0xFA24
        for k in range(4):
            rec = 0x65E + 14 * k
            m.set16(rec + 0x0A, 0)
            m.set16(rec + 0x0C, z)
            m.set16(rec, m.u16(rec) & 1)
            z = w16(z - 0x180)
        self.events.append(("message", "Recalling drones"))


ROUTINES = {}


# ---- verification ----------------------------------------------------------------------------
def _obj(k: int) -> int:
    return stunits.OBJECTS + stunits.OBJECT_SIZE * k


def scenario_turret(m, rnd):
    """On the turret screen: aiming from the mouse, firing (fire key or clicks), heat up to
    overheating, a broken laser or observation turret, objects on the lift/in the dock."""
    m.set16(stunits.SCREEN, TURRET_SCREEN)
    m.set16(0x93E, rnd.choice((0, 0, 3)))
    m.set16(0x6C8, rnd.choice((0, 0x2E00, 0x3800, 0x3F80)))
    m.set8(0x88C, rnd.choice((0, 0xFF)))
    m.set8(0x881, rnd.choice((0, 1)))
    m.set8(stunits.POINTER_MODE, rnd.choice((0, 0, 0xFF)))
    m.set16(stunits.MOUSE_DX, rnd.randrange(-300, 300))
    m.set16(stunits.MOUSE_DX + 2, rnd.randrange(-300, 300))
    m.set16(0x6CE, rnd.randrange(-255, 256))
    m.set16(0x6D0, rnd.randrange(-255, 256))
    m.set8(0x6D5, rnd.choice((0, 0x40, 0x80, 0xC0)))
    m.set16(0xB1A, rnd.choice((0x100, 0x100, 0x80, 0)))
    m.set8(0xB12, rnd.choice((0xFF, 0xFF, 0)))
    m.set16(0xB14, rnd.choice((2000, 1500, 50)))
    if rnd.random() < 0.3:
        m.set32(0x6D6, _obj(2 + rnd.randrange(4)))
        m.set16(0xDD0, 0)
    if rnd.random() < 0.3:
        m.set32(0x6DA, _obj(6 + rnd.randrange(4)))


def scenario_firing(m, rnd):
    """The turret firing every pass (fire key held each pass by the scenario runner's state)."""
    scenario_turret(m, rnd)
    m.set8(0x88C, 0xFF)
    m.set16(0x93E, 0)


def _launch_scenario(command):
    def scenario(m, rnd):
        """Stores on board, then the command issued on the captured state."""
        m.set16(STOCK_FLARES, rnd.choice((0, 3)))
        m.set16(STOCK_RECON, rnd.choice((0, 2)))
        m.set16(STOCK_SSM, rnd.choice((0, 4)))
        m.set16(STOCK_DRONES, rnd.choice((0, 4)))
        m.set16(0x1150, rnd.choice((0, 0, 5)))
        m.set16(0x115E, rnd.choice((0, 0, 5)))
        m.set8(0xB22, rnd.choice((0xFF, 0xFF, 0)))
        m.set8(0xB2A, rnd.choice((0xFF, 0xFF, 0)))
        state = stunits.UnitState.from_ram(bytes(m.buf))
        if command == "cmd_fire_ssm":
            state.cmd_launch_recon_drone()
            for _ in range(rnd.randrange(5, 60)):
                state.flares_step()
            state.mem.set8(0x1160, 0xFF)
            state.mem.set16(0x474, rnd.randrange(0, 320))
            state.mem.set16(0x476, rnd.randrange(0, 200))
        getattr(state, command)()
        state.to_ram(m.buf)
    scenario.__name__ = f"scenario_{command}"
    return scenario


SCENARIOS = [scenario_turret, scenario_firing] + [
    _launch_scenario(c) for c in ("cmd_launch_flare", "cmd_launch_recon_drone", "cmd_fire_ssm",
                                  "cmd_launch_defence_drone", "cmd_recall_defence_drones")]

COMMANDS = {"cmd_launch_flare": 0x1338C, "cmd_launch_recon_drone": 0x134C2,
            "cmd_fire_ssm": 0x13598, "cmd_launch_defence_drone": 0x10738,
            "cmd_recall_defence_drones": 0x1088C}


def _perturb_for_commands(m, rnd):
    """Random preconditions for the commands: stocks, timers, launcher damage, objects busy,
    stations, the recon drone in the air at some altitude, the pointer and the view panel."""
    m.set16(STOCK_FLARES, rnd.choice((0, 1, 5)))
    m.set16(STOCK_RECON, rnd.choice((0, 1)))
    m.set16(STOCK_SSM, rnd.choice((0, 3)))
    m.set16(STOCK_DRONES, rnd.choice((0, 2, 4)))
    m.set16(0x1150, rnd.choice((0, 0, 7)))
    m.set16(0x115E, rnd.choice((0, 0, 7)))
    m.set8(0xB22, rnd.choice((0xFF, 0xFF, 0)))
    m.set8(0xB2A, rnd.choice((0xFF, 0xFF, 0)))
    m.set8(0x1160, rnd.choice((0, 0xFF, 0xFF)))
    m.set8(0x122F, rnd.choice((0, 0, 0xFF)))
    m.set8(0x1233, rnd.choice((0, 0, 0, 0xFF)))
    m.set8(0xD40, rnd.choice((0, 0, 0, 0xFF)))
    m.set16(0x474, rnd.randrange(0, 320))
    m.set16(0x476, rnd.randrange(0, 200))
    m.set16(0x4778, rnd.choice((0, 1, 2, 3)))
    for k in (15, 17, 18):
        a = _obj(k)
        if rnd.random() < 0.4:
            m.set16(a, 0x8403)
            m.set16(a + 6, rnd.randrange(0x10000))
            m.set32(a + 0x0E, rnd.choice((200, 1500, 3000, 6000)))
            for off in (0x0A, 0x12):
                m.set32(a + off, m.u32(stunits.CARRIER + off) + rnd.randrange(-4000, 4000))
    active = 0
    for k in range(4):
        rec = 0x65E + 14 * k
        on = rnd.random() < 0.4
        active += on
        m.set16(rec, rnd.choice((1, 3, 5)) if on else rnd.choice((0, 2)))
        for off in (2, 4, 6, 8, 0xA, 0xC):
            m.set16(rec + off, rnd.choice((0, -1500, -1884, rnd.randrange(-3000, 3000))))
    m.set16(0x65C, active if rnd.random() < 0.8 else 0)


def check_commands(files) -> bool:
    """Each command against its original click handler (called with d2 = 2, a fresh
    click) on captured and perturbed states."""
    import random
    import check_sim
    import check_units
    rnd = random.Random(23)
    ok = True
    for name, addr in COMMANDS.items():
        runs = 0
        for f in files[::3]:
            for _ in range(6):
                m = stsim.Mem(bytearray(f.read_bytes()))
                _perturb_for_commands(m, rnd)
                ram = bytes(m.buf)
                oracle = check_sim.Oracle(ram)
                check_units.call_regs(oracle, addr, d2=2)
                state = stunits.UnitState.from_ram(ram)
                getattr(state, name)()
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
