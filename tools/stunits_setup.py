"""Unit layer, part of tools/stunits.py: starting a new game: the new-game routine $42A8 (globals restore, session reset, init_islands $18370 with trees and start structures, census, object-store init $43D2, route init), strategy and action variants.

The mixin is combined into stunits.UnitState ahead of stunits.UnitCore, so methods defined
here replace the core's stubs and hooks. Use only `import stsim` / `import stunits` at module
level (stunits imports this module while it is still loading; read its constants at run time).

new_game(strategy) takes a title-screen state (from_ram of a RAM image with the title menu
up, e.g. emu/out/title/ram.bin) whose RNG ($1236/$123A) the caller has seeded, and leaves
exactly the simulation state the original has when the title menu's click handler
($103A0 action / $103B2 strategy) returns from $42A8. new_game_screen() then does the state
part of the screen request $42A8 leaves (ui_frame enters the bridge, screen 0): the result is
the state the first simulation pass starts from. UI-only work is left out: the screen clear
$4B04, the "Creating..." message $9BC0, the message log $A1E8, sound $20716, the MFP $17944,
the VBL wait in frame_flip $17F48 and the panel drawing. docs/units.md §16 says what the app
has to do around these calls.

For tools/check_units.py, this module may define:
  ROUTINES  {"name": (original address, "method name")}: checked on every capture
  SCENARIOS [function(mem, rnd)]: perturbations run pass by pass against the original
  CHECKS    [function(files) -> bool]: any other checks (e.g. commands called with registers)
"""
import stsim
import stunits
from stsim import s16, w16  # noqa: F401

SNAPSHOT = 0x4BCCA                  # globals_snapshot $1A4C6 at boot: $400-$1235, then $40EB2
GLOBALS, GLOBALS_SIZE = 0x400, 0xE36
FLAG_TABLE, FLAG_TABLE_SIZE = 0x40EB2, 0xD4
ISLAND_RECORDS, ISLAND_RECORD = 0x4AFB6, 22    # x, z, hx, hz, runway x/z/heading, 2 volcanoes
STRATEGY = 0x124A                   # set: strategy game; clear: action game
START_STRATEGY, START_ACTION = 0x18FCE, 0x19008  # carrier, enemy carrier; then the start list
NOISE, NOISE_WORDS = 0x5C2DA, 0xD00  # static-noise table for the screen effect $5540
ISLAND_SCRATCH = 0xA80

# State new_game reads or writes that the other regions don't cover ($758 and the map cursors
# $69C are stunits_controls' regions; $768-$797 hold three copies of $758-$767).
for _region in (("strategy flag $124A", STRATEGY, 1),
                ("globals snapshot $4BCCA", SNAPSHOT, GLOBALS_SIZE + FLAG_TABLE_SIZE),
                ("island records $4AFB6", ISLAND_RECORDS, stsim.ISLANDS * ISLAND_RECORD),
                ("start lists $19078", 0x19078, 0x190EE - 0x19078),   # the rest is in the quota table
                ("map views $768", 0x768, 0x798 - 0x768)):
    if _region not in stunits.UNIT_REGIONS:
        stunits.UNIT_REGIONS.append(_region)
stunits.UNIT_INPUT_REGIONS |= {"globals snapshot $4BCCA", "island records $4AFB6", "start lists $19078"}


class SetupMixin:
    def new_game(self, strategy: bool) -> None:
        """$42A8 (with [$124A] set by the title menu: $103B2 strategy, $103A0 action)."""
        m = self.mem
        m.set8(STRATEGY, 0xFF if strategy else 0)
        self.globals_restore()
        self.session_reset()
        self.game_flags_reset()
        m.set8(0x1227, 0xFF)                    # screen request: tab 1 (carrier, screen 0)
        m.set16(0x1228, 1)
        if not strategy:                        # action: sail to a waypoint, harder islands
            m.set32(stunits.C_WAYPOINT_X, 0xFFE66600)
            m.set32(stunits.C_WAYPOINT_Z, 0xFFE77748)
            m.set16(stunits.C_CRUISE, 0x70)
            m.set8(stunits.C_FLAGS, 7)
            m.set8(0x6C6, 0xFF)
        m.set16(0x444, 0 if strategy else 0x2000)
        self.difficulty_apply()
        m.set8(stsim.AI_ENABLED, 0xFF)
        m.set8(stunits.POINTER_MODE, 0)
        self.frame_flip_flags()                 # after the screen clear and "Creating..."
        m.set8(stunits.FULL_REDRAW, 0xFF)
        self.init_islands()
        self.count_islands()
        self.noise_table_init()
        m.set8(0x8FD, 0xFF)
        self.object_store_init()
        self.network_invalidate()
        self.map_views_init()

    def globals_restore(self) -> None:
        """$1A518: the globals and the panel flag table as snapshotted at boot."""
        b = self.mem.buf
        b[GLOBALS:GLOBALS + GLOBALS_SIZE] = b[SNAPSHOT:SNAPSHOT + GLOBALS_SIZE]
        s = SNAPSHOT + GLOBALS_SIZE
        b[FLAG_TABLE:FLAG_TABLE + FLAG_TABLE_SIZE] = b[s:s + FLAG_TABLE_SIZE]

    def session_reset(self) -> None:
        """$4206, the parts that write simulation state: input $17EC6/$17EAE, the tick
        count, the pools $218D8, the debris $1C6E2, the pointer $F712 and the pointer mode."""
        m = self.mem
        m.set16(stunits.MOUSE_DX, 0)            # $17EC6
        m.set16(stunits.MOUSE_DX + 2, 0)
        m.set32(0x880, 0)
        m.buf[0x798:0x884] = bytes(0x884 - 0x798)   # $17EAE: key table ($880 buttons included)
        m.set8(0x888, 0)
        m.set8(0x1270, 0xFF)
        m.set32(stsim.TICK_COUNT, 0)
        self.pools_init()
        self.debris_init()
        m.set8(0x459, 0)
        self.pointer_reset()
        m.set8(stunits.POINTER_MODE, 0xFF)

    def pointer_reset(self) -> None:
        """$F712: no modal pointer handler, the default pointer, full-screen pointer limits."""
        m = self.mem
        m.set8(0x460, 0)
        m.set32(0x46E, 0x40E26)
        m.set16(0x472, 0)
        m.set16(0x478, 0)
        m.set16(0x47A, 0)
        m.set16(0x47C, 0x13F)
        m.set16(0x47E, 0xC7)

    def pools_init(self) -> None:
        """$218D8: every raw-material and goods/carrier pool entry free."""
        m = self.mem
        for k in range(stsim.RAW_POOL_N):
            m.set8(stsim.RAW_POOL + stsim.POOL_ENTRY * k, 0xFF)
        for k in range(stsim.GOODS_POOL_N):
            m.set8(stsim.GOODS_POOL + stsim.POOL_ENTRY * k, 0xFF)

    def debris_init(self) -> None:
        """$1C6E2: bursts idle, the 64 shards chained into the free list."""
        m = self.mem
        for k in range(4):
            a = stunits.DEBRIS + 0x12 * k
            m.set8(a, 0)
            m.set32(a + 0xE, 0)
        m.set32(0x1194, stunits.SHARDS)
        for k in range(63):
            a = stunits.SHARDS + 0x1A * k
            m.set32(a + 0x16, a + 0x1A)
        m.set32(stunits.SHARDS + 0x1A * 63 + 0x16, 0)
        m.set16(0x1198, 0x40)

    def game_flags_reset(self) -> None:
        """$4260: in game, end request cleared."""
        m = self.mem
        m.set8(0x8F0, 0xFF)
        m.set8(0x1230, 0)
        m.set8(0x1231, 0xFF)
        m.set8(0x463, 0)

    def frame_flip_flags(self) -> None:
        """The state part of frame_flip $17F48: a pending full redraw ([$458]) puts the
        pointer back on."""
        m = self.mem
        if m.u8(stunits.FULL_REDRAW):
            m.set32(0x46E, 0x40E26)
            m.set8(stunits.POINTER_MODE, 0xFF)
            m.set8(stunits.FULL_REDRAW, 0)
        m.set16(0x8EE, 0)

    # ---- init_islands $18370 ------------------------------------------------------------
    def init_islands(self) -> None:
        """$18370: islands from their records, 21 trees each, the start structures, then
        world_warmup."""
        m = self.mem
        self.slot_order_init()
        self.island_index = 0
        m.buf[stsim.OWNERS:stsim.OWNERS + stsim.ISLANDS] = bytes(stsim.ISLANDS)
        m.buf[stsim.ECONOMY:stsim.ECONOMY + 0xC80] = bytes(0xC80)
        for i in range(stsim.ISLANDS):
            r = ISLAND_RECORDS + ISLAND_RECORD * i
            a = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i
            e = stsim.ECONOMY + stsim.ECONOMY_SIZE * i
            m.set8(a, 0)
            m.set16(a + 0x10, 0)
            m.set32(a + 2, m.s16(r) << 9)
            m.set32(a + 6, m.s16(r + 2) << 9)
            hx, hz = m.u16(r + 4), m.u16(r + 6)
            m.set16(a + 0xA, hx)
            m.set16(a + 0xC, hz)
            area = (hx * hz) >> 16
            m.set16(a + 0xE, area)
            m.set16(e + 4, 2 * area)
            m.set16(e + 2, 0)
            m.set16(e + 0xA, 2 * area)
            m.set16(e + 8, 0)
            for o in (0xE, 0x1A, 0x26):
                m.set8(e + o, 0xFF)
            m.buf[a + 0x12:a + 0x12 + 0x180] = bytes(0x180)
            m.set16(a + 0x12, 8)                        # slot 0: the island itself
            s = a + 0x1E
            x, z, h = m.u16(r + 8), m.u16(r + 10), m.u16(r + 12)
            if x | z:                                   # slot 1: the runway, to be built
                m.set16(s, stsim.T_RUNWAY_CONSTRUCTION)
                m.set16(s + 8, x)
                m.set16(s + 10, z)
                m.set16(s + 2, h)
            s += 0xC
            for k in range(2):                          # volcanoes
                x, z = m.u16(r + 14 + 4 * k), m.u16(r + 16 + 4 * k)
                if x | z:
                    m.set16(s, 7)
                    m.set16(s + 8, x)
                    m.set16(s + 10, z)
                    m.set16(s + 2, self.rand())
                    s += 0xC
        self.plant_trees()
        self.place_start_structures()
        self.warmup_run()

    def slot_order_init(self) -> None:
        """$18924: the slot visiting order 0..31 (index, offset) before any shuffle."""
        for k in range(stsim.SLOTS):
            self.mem.set16(stsim.SLOT_ORDER + 4 * k, k)
            self.mem.set16(stsim.SLOT_ORDER + 4 * k + 2, stsim.SLOT_SIZE * k)

    def select_island(self, i: int) -> None:
        """$18940: the island, economy and 3D buffer pointers of island i."""
        m = self.mem
        isl = stsim.ISLAND_TABLE + stsim.ISLAND_SIZE * i
        m.set32(stsim.G_ISLAND_PTR, isl)
        m.set32(stsim.G_ECONOMY_PTR, stsim.ECONOMY + stsim.ECONOMY_SIZE * i)
        m.set32(stsim.G_BUFFER_PTR, stsim.OBJ_BUFFERS[1 if m.u8(isl + 1) else 0])

    def _free_spot(self, t: int, i: int):
        """find_free_island_spot $18A48 until it finds a spot (the original loops on -1).
        -2 (no empty or tree slot left) is a trap #7 in $189CA."""
        while True:
            spot = self.find_free_spot(t, i)
            if spot is not None:
                return spot
            if all(s.type for s in self.island(i).slots):
                raise NotImplementedError("new game: island full, find_free_island_spot -2 (trap #7)")

    def plant_trees(self) -> None:
        """$1897E: 21 random trees ($18-$1B) on every island, from island 63 down."""
        for i in range(stsim.ISLANDS - 1, -1, -1):
            self.island_index = i
            self.select_island(i)
            for _ in range(21):
                j, x, z = self._free_spot(stsim.T_TREE_FIRST, i)
                s = self.island(i).slots[j]
                s.x, s.z = x, z
                s.type = (self.rand() & 3) + stsim.T_TREE_FIRST

    def place_start_structures(self) -> None:
        """$189CA: the command centres under construction ($34, progress $3B) of the start
        list, (island, start code) pairs: $18FE2 in a strategy game, else $1901C."""
        m = self.mem
        a = START_STRATEGY + 0x14 if m.u8(STRATEGY) else START_ACTION + 0x14
        while not m.u16(a) & 0x8000:
            i, code = m.u16(a), m.u16(a + 2)
            a += 4
            m.set16(ISLAND_SCRATCH, i)
            self.select_island(i)
            j, x, z = self._free_spot(stsim.T_COMMAND_CENTRE_CONSTRUCTION, i)
            s = self.island(i).slots[j]
            s.type = stsim.T_COMMAND_CENTRE_CONSTRUCTION
            s.x, s.z = x, z
            s.value = 0x3B
            s.word6 = code

    # ---- the first screen -----------------------------------------------------------------
    def new_game_screen(self) -> None:
        """The state part of what follows $42A8 in the same game_main pass, before the
        simulation block: ui_frame ($F636) takes the screen request $42A8 left ([$1227], tab
        [$1228] = 1, whose last screen [$722] is 0), leaves the title (screen [$718] = -1: no
        leave handler) and enters the bridge ($139AE, $13C24). The panel and gauge drawing
        ($782A, $7A66) is left out."""
        m = self.mem
        if not m.u8(0x1227):
            return
        m.set8(0x1227, 0)
        tab = m.u16(0x1228)
        if not m.u16(stunits.SCREEN) & 0x8000:
            raise NotImplementedError(f"screen leave handler for screen {m.u16(stunits.SCREEN)}")
        m.set16(0x714, tab)
        screen = m.u16(0x720 + 2 * tab)
        m.set16(stunits.SCREEN, screen)
        m.set8(0x122D, 0)                              # $139AE
        for a in (0x9FA, stunits.VIEWED, 0xA02):
            m.set32(a, 0)
        if screen != 0:
            raise NotImplementedError(f"screen enter handler for screen {screen}")
        cam = self.obj(0)                              # $13C24: the bridge view
        m.set8(0x1230, 0xFF)
        m.set8(0xA46, 0)
        m.set8(0xA47, 0)
        m.set32(0x9FA, cam)
        m.set32(stunits.VIEWED, stunits.CARRIER)
        m.set32(0xA02, stunits.CARRIER)
        m.set16(0xA14, 2)
        m.set16(cam, 0)
        m.set32(cam + 0xE, 0xF0)
        m.set32(cam + 0x3C, m.u32(stunits.CARRIER + 0x3C))
        for a in (0x954, 0x958, 0x956, 0x962):
            m.set16(a, 2)
        m.set16(0x480, m.u16(stunits.C_DEPTH))
        m.set16(0x40F66, 0x2000 if m.u8(stunits.C_FLAGS) & 1 else 0)   # $12C16: autopilot button lit
        m.set8(0x716, 0xFF)                            # back in $F6DC
        m.set8(0x71A, 0xFF)

    # ---- the rest of $42A8 ----------------------------------------------------------------
    def noise_table_init(self) -> None:
        """$55BC: $D00 random words for the screen-noise effect (not simulation state, but
        it draws the RNG)."""
        for k in range(NOISE_WORDS):
            self.mem.set16(NOISE + 2 * k, self.rand())

    def object_store_init(self) -> None:
        """$43D2: no objects but the two carriers at their start positions."""
        m = self.mem
        m.buf[stsim.OBJECTS:stsim.OBJECTS + 64 * stsim.OBJECT_SIZE] = bytes(64 * stsim.OBJECT_SIZE)
        m.set16(0xA48, 0x40)
        for b in stunits.ISLAND_BUFFERS:
            m.set8(b, m.u8(b) & 0xFE)
        for k in range(4):
            m.set16(self.obj(2 + k) + 0x1A, k)
            m.set16(self.obj(6 + k) + 0x1A, k)
        t = START_STRATEGY if m.u8(STRATEGY) else START_ACTION
        for k, typ, x, z, h in ((10, 1, 0, 4, 8), (19, 4, 0xA, 0xE, 0x12)):
            o = self.obj(k)
            m.set16(stunits.NEW_INDEX, k)
            self.init_world_object(o, typ)
            self.reset_pair_schedules()
            m.set32(o + 0xE, 0xA0)
            m.set32(o + 0xA, m.u32(t + x))
            m.set32(o + 0x12, m.u32(t + z))
            m.set16(o + 6, m.u16(t + h))

    def map_views_init(self) -> None:
        """The end of $42A8: map views centred on the carrier, the map cursors ($69E Manta,
        $6AC Walrus, $6BA carrier *(guess)*) 1000 off it."""
        m = self.mem
        x, z = m.u32(stunits.CARRIER + 0xA), m.u32(stunits.CARRIER + 0x12)
        m.set32(0x75C, x)
        m.set32(0x760, z)
        x, z = x + 1000, z + 1000
        for a in (0x69E, 0x6B0, 0x6BE):
            m.set32(a, x)
        for a in (0x6A6, 0x6B4, 0x6C2):
            m.set32(a, z)
        for a in (0x768, 0x778, 0x788):
            m.buf[a:a + 0x10] = m.buf[0x758:0x768]


# ---- checks -------------------------------------------------------------------------------
NEW_GAME, UI_FRAME = 0x42A8, 0xF636
FRAME_FLIP_WAIT, FRAME_FLIP_AFTER = 0x17F6A, 0x17F84
SEEDS = (0, 1, 0x12345678, 0xDEADBEEF, 0x0BADF00D, 0x7FFF8000, 0xFFFFFFFF, 0x55AA33CC)


def title_ram() -> bytes:
    from stgfx import ROOT
    p = ROOT / "emu" / "out" / "title" / "ram.bin"
    return p.read_bytes() if p.exists() else None


def seeded(ram: bytes, strategy: bool, seed: int) -> bytearray:
    """The title image with the game type and the RNG ($1236 and the second long $123A)
    seeded as the app does."""
    r = bytearray(ram)
    r[STRATEGY] = 0xFF if strategy else 0
    r[stsim.RNG:stsim.RNG + 4] = (seed & 0xFFFFFFFF).to_bytes(4, "big")
    r[stsim.RNG + 4:stsim.RNG + 8] = ((seed * 0x9E3779B9 + 1) & 0xFFFFFFFF).to_bytes(4, "big")
    return r


def oracle_new_game(ram: bytes):
    """$42A8 on the original under Unicorn. Only frame_flip's VBL waits are skipped; the UI
    routines (screen clear, message, sound, MFP) run, they don't touch simulation state. The
    stack sits at the game's own ($4B04 clears the screen up to $7FD03)."""
    import check_sim
    o = check_sim.Oracle(ram)
    o.uc.mem_write(FRAME_FLIP_WAIT, (0x6000 | (FRAME_FLIP_AFTER - FRAME_FLIP_WAIT - 2)).to_bytes(2, "big"))
    stack = check_sim.STACK
    check_sim.STACK = 0x58600
    try:
        o.call(NEW_GAME)
    finally:
        check_sim.STACK = stack
    o.uc.mem_write(FRAME_FLIP_WAIT, ram[FRAME_FLIP_WAIT:FRAME_FLIP_WAIT + 2])
    return o


def oracle_screen(o) -> None:
    """ui_frame $F636 on the original, after $42A8: it takes the screen request."""
    import check_sim
    stack = check_sim.STACK
    check_sim.STACK = 0x58600
    try:
        o.call(UI_FRAME)
    finally:
        check_sim.STACK = stack


def check_new_game(files) -> bool:
    """new_game and new_game_screen against $42A8 and ui_frame from the title image, both
    game types, several seeds; then 3000 passes of the model from its new-game state against
    the original's passes."""
    import check_units
    ram = title_ram()
    if ram is None:
        print("new game: SKIPPED (no emu/out/title/ram.bin; tools/hatari.py snapshot --vbls 1500 --out emu/out/title)")
        return True
    n = 0
    for strategy in (True, False):
        for seed in SEEDS:
            r = seeded(ram, strategy, seed)
            o = oracle_new_game(bytes(r))
            state = stunits.UnitState.from_ram(bytes(r))
            state.new_game(strategy)
            ref = o.ram()
            mine = bytearray(ref)
            state.to_ram(mine)
            lines = check_units.diff(mine, ref)
            if not lines:
                oracle_screen(o)
                state.new_game_screen()
                ref = o.ram()
                mine = bytearray(ref)
                state.to_ram(mine)
                lines = check_units.diff(mine, ref)
            if lines:
                print(f"new game: MISMATCH ({'strategy' if strategy else 'action'}, seed {seed:08X})")
                for line in lines:
                    print("  " + line)
                return False
            n += 1
    print(f"new game: OK ({n} starts, strategy and action, then the first screen)")
    ok = True
    for strategy, seed in ((True, 0x2468ACE1), (False, 0x13579BDF), (True, 0x0F1E2D3C), (False, 0x8BADF00D)):
        ok &= new_game_run(ram, strategy, seed, 3000)
    return ok


def new_game_run(ram: bytes, strategy: bool, seed: int, passes: int) -> bool:
    """Model and original pass by pass from a new game (screen input stubbed, the one-second
    flag every 8 passes)."""
    import check_units
    r = seeded(ram, strategy, seed)
    o = oracle_new_game(bytes(r))
    oracle_screen(o)
    o.uc.mem_write(0x17094, b"\x4e\x75")
    state = stunits.UnitState.from_ram(bytes(r))
    state.new_game(strategy)
    state.new_game_screen()
    kind = "strategy" if strategy else "action"
    for p in range(passes):
        if p % 8 == 0:
            o.uc.mem_write(0x8FD, b"\xff")
            state.mem.set8(0x8FD, 0xFF)
        try:
            state.sim_pass()
        except (NotImplementedError, stsim.TacticalMode, stsim.EnemyDestroyed) as e:
            print(f"new game run ({kind}): stopped at pass {p}: {e or type(e).__name__}")
            return True
        check_units.run_pass(o)
        ref = o.ram()
        mine = bytearray(ref)
        state.to_ram(mine)
        lines = check_units.diff(mine, ref)
        if lines:
            print(f"new game run ({kind}): MISMATCH at pass {p}")
            for line in lines:
                print("  " + line)
            return False
    print(f"new game run ({kind}): OK ({passes} passes)")
    return True


def check_hatari_new_game(files) -> bool:
    """Against the real machine: Hatari captures of the original starting each game type
    (`uv run python tools/stunits_setup.py`): $42A8 from the RAM at its entry, then the
    first screen, then the first pass."""
    import check_units
    from stgfx import ROOT
    ok, n = True, 0
    for kind in ("strategy", "action"):
        d = ROOT / "emu" / "out" / "newgame" / kind
        if not (d / "pass0_out.bin").exists():
            continue
        rd = lambda name: (d / f"{name}.bin").read_bytes()
        state = stunits.UnitState.from_ram(rd("newgame_in"))
        for label, run, ref in (("$42A8", lambda: state.new_game(kind == "strategy"), rd("newgame_out")),
                                ("first screen", state.new_game_screen, rd("pass0_in")),
                                ("first pass", state.sim_pass, rd("pass0_out"))):
            run()
            mine = bytearray(ref)
            state.to_ram(mine)
            lines = check_units.diff(mine, ref)
            if lines:
                print(f"hatari new game ({kind}): MISMATCH after {label}")
                for line in lines:
                    print("  " + line)
                ok = False
                break
        n += 1
    print(f"hatari new game: {'OK' if ok else 'MISMATCH'} ({n} game types; capture with tools/stunits_setup.py)")
    return ok


def capture_hatari() -> None:
    """Hatari: click a title-menu button and save RAM at $42A8's entry (newgame_in), at its
    return to the menu handler (newgame_out) and around the first simulation pass ($40B0
    pass0_in, $413E pass0_out), to emu/out/newgame/<strategy|action>/."""
    import os
    import subprocess
    from capture_sim import ACTION_BUTTON, STRATEGY_BUTTON, TITLE_VBLS
    from hatari import BASE_ARGS, EMU, GAME_MAIN, prepare
    prepare()
    for kind, button, ret in (("strategy", STRATEGY_BUTTON, 0x103C2), ("action", ACTION_BUTTON, 0x103B0)):
        out = (EMU / "out" / "newgame" / kind).resolve()
        out.mkdir(parents=True, exist_ok=True)

        def script(name, body):
            p = out / f"{name}.txt"
            p.write_text(body)
            return p
        save = lambda n: f"savebin {out / n}.bin 0 $80000\n"
        then = lambda c, n: f"b {c} :once :trace :file {n}\n"
        nxt = script("finish", f"screenshot {out / 'final.png'}\nquit\n")
        nxt = script("p_out", save("pass0_out") + then("VBL>0", nxt))
        nxt = script("p_in", save("pass0_in") + then("pc=$413e", nxt))
        nxt = script("ng_out", save("newgame_out") + then("pc=$40b0", nxt))
        nxt = script("ng_in", save("newgame_in") + then(f"pc=${ret:x}", nxt))
        x, y = button
        rel = script("release", "w $880 0 0\n")
        click = script("click", f"w $474 {x >> 8} {x & 255} {y >> 8} {y & 255}\nw $880 1 1\n"
                                + then('VBL="VBL+10"', rel) + then("pc=$42a8", nxt))
        title = script("title", then(f'VBL="VBL+{TITLE_VBLS}"', click))
        start = script("start", then(f"pc=${GAME_MAIN:x}", title))
        env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
        with open(out / "hatari.log", "w") as log:
            subprocess.run(["hatari", *BASE_ARGS, "--sound", "off", "--fast-forward", "on", "--parse", str(start)],
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
                           timeout=300, cwd=out)
        print(f"{out}: {' '.join(sorted(p.name for p in out.glob('*.bin')))}")


ROUTINES = {}
SCENARIOS = []
CHECKS = [check_new_game, check_hatari_new_game]


if __name__ == "__main__":
    capture_hatari()
