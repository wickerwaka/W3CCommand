"""Reference model of the strategy simulation: the island step (world_sim_step, $1848E)
and the once-a-second logistics tick ($F7A0).

A Python port of the game's code, written to be checked byte for byte against the original
(tools/check_sim.py). docs/simulation.md describes the rules; this file is the executable
version.

State is the game's own memory layout: a 512 KB image in which only STATE_REGIONS are
populated (WorldState.from_ram copies just those, so anything the model reads outside them
reads as zero and shows up as a mismatch). Islands, slots and economy records are views
onto that image. The image is trivially serialisable and moves both ways between the model
and an emulator. Arithmetic follows the 68000: 16-bit wrap, and signed or unsigned
compares exactly where the original uses them. A few routines depend on a register value
left over by the previous routine; the model carries it (see `logistics_tick`).

Not modelled: the parts of the tick that act on units (`$1B8AE`, and the hangar repairs
`$1761C`/`$176AC`), the 3D object buffers of the island loaded around the camera (island
byte +0), and on-screen messages, which are recorded in WorldState.events instead.
"""
from dataclasses import dataclass, field

RAM_SIZE = 0x80000
ISLANDS, SLOTS, TYPES = 64, 32, 0x3C

# island step
ISLAND_TABLE, ISLAND_SIZE, SLOT_BASE, SLOT_SIZE = 0x69B80, 0x192, 0x12, 12
ECONOMY, ECONOMY_SIZE = 0x66EC0, 0x32
OWNERS = 0x69B40
SLOT_ORDER = 0x66690
QUOTA_ROM, QUOTA_WORK, QUOTA_ROW = 0x18DF8, 0x586AA, 10
QUOTA_FALLBACK = 0x18EE8            # the tree row ($18) inside QUOTA_ROM; decremented in place
HANDLERS = 0x18C28                  # 8 bytes per type: handler.l, max_health.w, build_target.w
RADIUS = 0x18BB4                    # clearance radius per type, for find_free_island_spot
SPOT_CUT_LIST = 0x58E2A             # find_free_island_spot: trees in the way (scratch)
NETWORK, NETWORK_SIZE = 0x2239E, 0x20
ROUTES = 0x67B40                    # route matrix, 64 x 64 words; row 0 VULCAN, row 63 NEMESIS
RNG = 0x1236
OBJ_BUFFERS = (0x62CDA, 0x63CEC)    # 3D object buffers, chosen by island byte +1
G_ISLAND_PTR, G_ECONOMY_PTR, G_BUFFER_PTR = 0x8C4, 0x8C8, 0x8CC
G_ISLAND, G_CLASS = 0x8D0, 0x8D2
G_BUILDING, G_STALLED, G_ACTIVE_BUILDINGS, G_EMPTY = 0x8D4, 0x8D6, 0x8D8, 0x8DA
G_WARMUP = 0x8DC
ROUTES_PENDING = 0x1235             # set by network_invalidate until game_main has relaxed

# logistics
RAW_POOL, RAW_POOL_N = 0x66BC0, 128         # at.b timer.b amount.w dest.w
GOODS_POOL, GOODS_POOL_N = 0x66710, 200     # at.b timer.b kind.b qty.b dest.w; the last 72
CARRIER_POOL, CARRIER_POOL_N = 0x66A10, 72  # entries are the carrier-delivery pool
FREE_GOODS_SEARCH = 128                     # production only allocates from the first 128
POOL_ENTRY = 6
LINES = 0x0E                                # production lines in the economy record, 12 bytes
PRODUCTS = 0x21FAE                          # steps.w qty.w cost.w per product
ITEMS, ITEM_SIZE, N_ITEMS = 0xB76, 0x1A, 15  # store items: +$10 class.w, +$12 target.w
FUEL_CLASS = 0xD08                          # 3 words
STOCKPILE = 0xD4A                           # island where goods for the carrier collect
CARRIER_ISLAND = 0xD4E                      # where carrier-pool shipments start ($21D46)
IN_TRANSIT = 0xD50                          # "Stocks still in transit" flag
COUNT_PLAYER, COUNT_ENEMY, COUNT_BY_KIND = 0xD54, 0xD56, 0xD58
FUEL, FUEL_PENDING, STORES, STORES_PENDING = 0xD60, 0xD6C, 0xD78, 0xD96
FUEL_MAX, STORE_MAX = 0x1FFFC0, 999
PLAN_CLASS, PLAN_COUNT, TOTALS, ISLAND_GOODS_TOTAL = 0xDF6, 0xDF8, 0xDFA, 0xE18
PLAN_LIST, FUEL_TOTALS, RAW_TOTAL, SHIP_LIST = 0xE98, 0xF3C, 0xF48, 0xFC8
SYSTEMS, N_SYSTEMS = 0xAF4, 10              # carrier systems: cur.w max.w class.w ?.w
REPAIR_RATE = 0xB3A
REPAIR_CLASS, REPAIR_STEP, REPAIR_COUNT = 0xB5C, 0xB5E, 0xB60
TICK_COUNT = 0x97A                          # long, seconds of game time
TIMER_REPAIR, TIMER_MANTA, TIMER_WALRUS, TIMER_984, TIMER_LOGISTICS = 0x97E, 0x980, 0x982, 0x984, 0x986

# enemy strategy
ENEMY, ENEMY_END = 0x11A6, 0x11D6
E_POS_X, E_POS_Z, E_VULCAN_X, E_VULCAN_Z, E_NEMESIS_X, E_NEMESIS_Z = 0x11A6, 0x11AA, 0x11AE, 0x11B2, 0x11B6, 0x11BA
E_TARGET, E_ISLAND, E_STATE, E_TIMER, E_STRENGTH = 0x11BE, 0x11C0, 0x11C2, 0x11C4, 0x11C6
E_AIRCRAFT, E_RESERVE = 0x11CA, 0x11D2
ENEMY_SCORES, ENEMY_COIN = 0x22B9E, 0x22C1E     # scratch in the code area; the coin persists
AI_ENABLED, GAME_END_TIMER = 0x464, 0x732
B_DEFENCE, B_ATTACK, B_TARGETS, B_TIMER, B_POOL = 0x948, 0x94A, 0x94C, 0x94E, 0x950
DEFENCE_WEIGHTS = (0x1000, 0x200, 0x200, 0x300)  # $1B73E, building types $25-$28
OBJECTS, OBJECT_SIZE = 0x5DCDA, 0x140
OBJECT_HEADER = 0x40                            # +$40 on: per-object 3D caches (unit layer)
ENEMY_CARRIER = OBJECTS + OBJECT_SIZE * 19      # object 19
NEMESIS, VULCAN = 63, 0
FAR = 3_000_000

# Regions that make up the simulation state: (name, start, length). from_ram/to_ram copy
# these; check_sim compares them except for the inputs the model never writes.
STATE_REGIONS = [
    ("current screen $718", 0x718, 2),
    ("step globals", G_ISLAND_PTR, 0x8DE - G_ISLAND_PTR),
    ("second flags $8FC", 0x8FC, 2),
    ("redraw flags $952", 0x952, 2),
    ("redraw flag $962", 0x962, 2),
    ("redraw flag $966", 0x966, 2),
    ("tick counters", TICK_COUNT, 0x988 - TICK_COUNT),
    ("carrier systems", SYSTEMS, 0xB62 - SYSTEMS),
    ("store items", ITEMS, N_ITEMS * ITEM_SIZE),
    ("fuel classes", FUEL_CLASS, 6),
    ("logistics flags $D3E", 0xD3E, 2),
    ("logistics globals", STOCKPILE, 0xD60 - STOCKPILE),
    ("fuel and stores", FUEL, 0xDB4 - FUEL),
    ("totals", PLAN_CLASS, FUEL_TOTALS - PLAN_CLASS),
    ("fuel totals, raw totals", FUEL_TOTALS, SHIP_LIST - FUEL_TOTALS),
    ("ship list", SHIP_LIST, 0x180),
    ("route flags, rng", 0x1234, 10),
    ("radius table", RADIUS, TYPES * 2),
    ("handler table", HANDLERS, TYPES * 8),
    ("quota table", QUOTA_ROM, 64 * QUOTA_ROW),
    ("product table", PRODUCTS, 19 * 6),
    ("network", NETWORK, ISLANDS * NETWORK_SIZE),
    ("quota work", QUOTA_WORK, 64 * QUOTA_ROW),
    ("slot order", SLOT_ORDER, GOODS_POOL - SLOT_ORDER),
    ("goods pool", GOODS_POOL, CARRIER_POOL - GOODS_POOL),
    ("carrier pool", CARRIER_POOL, RAW_POOL - CARRIER_POOL),
    ("raw pool", RAW_POOL, ECONOMY - RAW_POOL),
    ("economy", ECONOMY, ROUTES - ECONOMY),
    ("routes", ROUTES, OWNERS - ROUTES),
    ("route relax state", 0xDEC, 0xA),
    ("owners", OWNERS, ISLANDS),
    ("islands", ISLAND_TABLE, ISLANDS * ISLAND_SIZE),
    ("difficulty", 0x444, 0x12),
    ("ai flags $464", 0x464, 2),
    ("game end timer", 0x732, 2),
    ("bombard state", 0x948, 0xA),
    ("enemy globals", ENEMY, ENEMY_END - ENEMY),
    ("enemy scores", ENEMY_SCORES, 0x82),
] + [(f"unit {k}", OBJECTS + OBJECT_SIZE * k, OBJECT_HEADER) for k in range(2, 11)] + [
    ("enemy carrier", ENEMY_CARRIER, OBJECT_HEADER),
]
# Read but never written by the model (set by the UI, the unit layer or game setup).
INPUT_REGIONS = {"current screen $718", "store items", "fuel classes", "radius table",
                 "handler table", "product table", "difficulty"} | {f"unit {k}" for k in range(2, 11)}

T_TREE_FIRST, T_TREE_LAST = 0x18, 0x1B
T_BUILDING_FIRST, T_BUILDING_LAST = 0x24, 0x2E
T_COMMAND_CENTRE, T_CONSTRUCTION_FIRST, T_RUNWAY_CONSTRUCTION = 0x29, 0x2F, 0x2F
T_COMMAND_CENTRE_CONSTRUCTION = 0x34
UNDER_CONSTRUCTION = 0xB            # construction type = finished type + $B

H_TREE, H_BUILDING, H_COMMAND_CENTRE = 0x18874, 0x1876E, 0x18784
H_CONSTRUCTION, H_COMMAND_CENTRE_CONSTRUCTION = 0x186F6, 0x187A0


def w16(v: int) -> int:
    return v & 0xFFFF


def s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def s8(v: int) -> int:
    v &= 0xFF
    return v - 0x100 if v & 0x80 else v


class Mem:
    """Big-endian accessors over a bytearray; addresses are absolute."""

    def __init__(self, buf: bytearray):
        self.buf = buf

    def u8(self, a): return self.buf[a]
    def s8(self, a): return s8(self.buf[a])
    def u16(self, a): return int.from_bytes(self.buf[a:a + 2], "big")
    def s16(self, a): return s16(self.u16(a))
    def u32(self, a): return int.from_bytes(self.buf[a:a + 4], "big")
    def set8(self, a, v): self.buf[a] = v & 0xFF
    def set16(self, a, v): self.buf[a:a + 2] = (v & 0xFFFF).to_bytes(2, "big")
    def set32(self, a, v): self.buf[a:a + 4] = (v & 0xFFFFFFFF).to_bytes(4, "big")


def _field(offset: int, kind: str):
    get, put = {"b": (Mem.u8, Mem.set8), "w": (Mem.s16, Mem.set16), "uw": (Mem.u16, Mem.set16)}[kind]
    return property(lambda s: get(s.m, s.a + offset), lambda s, v: put(s.m, s.a + offset, v))


class Slot:
    """Structure slot (12 bytes): type, heading, value (progress or health), word6 (high
    byte = active flag; type $34: start code), x, z."""

    def __init__(self, m: Mem, a: int):
        self.m, self.a = m, a

    type = _field(0, "uw")
    heading = _field(2, "uw")
    value = _field(4, "w")
    word6 = _field(6, "uw")
    x = _field(8, "w")
    z = _field(10, "w")

    @property
    def active(self) -> bool:
        return self.m.u8(self.a + 6) != 0

    def set_active(self, on: bool) -> None:
        self.m.set8(self.a + 6, 0xFF if on else 0)


class Island:
    def __init__(self, m: Mem, i: int):
        self.m, self.a = m, ISLAND_TABLE + ISLAND_SIZE * i
        self.slots = [Slot(m, self.a + SLOT_BASE + SLOT_SIZE * j) for j in range(SLOTS)]

    loaded = _field(0, "b")
    buffer = _field(1, "b")
    half_x = _field(0x0A, "w")
    half_z = _field(0x0C, "w")
    area = _field(0x0E, "w")


class Economy:
    """Economy record ($32 bytes). Production lines follow at +$0E, `lines` of them."""

    def __init__(self, m: Mem, i: int):
        self.m, self.a = m, ECONOMY + ECONOMY_SIZE * i

    kind = _field(0x00, "b")             # 0 base, 1 resource, 2 factory, 3 defence
    raw_stock = _field(0x02, "uw")
    raw_cap = _field(0x04, "uw")
    mines = _field(0x06, "b")
    has_command_centre = _field(0x07, "b")
    goods = _field(0x08, "uw")
    goods_cap = _field(0x0A, "uw")
    lines = _field(0x0C, "uw")           # = factories counted by the census

    def line(self, k: int) -> int:
        return self.a + LINES + 12 * k


@dataclass
class WorldState:
    mem: Mem
    events: list = field(default_factory=list)

    @classmethod
    def from_ram(cls, ram: bytes) -> "WorldState":
        buf = bytearray(RAM_SIZE)
        for _, a, n in STATE_REGIONS:
            buf[a:a + n] = ram[a:a + n]
        return cls(Mem(buf))

    def to_ram(self, ram: bytearray) -> None:
        for _, a, n in STATE_REGIONS:
            ram[a:a + n] = self.mem.buf[a:a + n]

    # ---- views and globals ---------------------------------------------------------
    def island(self, i: int) -> Island: return Island(self.mem, i)
    def economy(self, i: int) -> Economy: return Economy(self.mem, i)
    def owner(self, i: int) -> int: return self.mem.u8(OWNERS + i)
    def route(self, i: int, j: int) -> int: return self.mem.s16(ROUTES + 128 * i + 2 * j)

    island_index = property(lambda s: s.mem.u16(G_ISLAND), lambda s, v: s.mem.set16(G_ISLAND, v))
    island_class = property(lambda s: s.mem.u16(G_CLASS), lambda s, v: s.mem.set16(G_CLASS, v))
    building = property(lambda s: s.mem.s16(G_BUILDING), lambda s, v: s.mem.set16(G_BUILDING, v))
    stalled = property(lambda s: s.mem.s16(G_STALLED), lambda s, v: s.mem.set16(G_STALLED, v))
    active_buildings = property(lambda s: s.mem.s16(G_ACTIVE_BUILDINGS),
                                lambda s, v: s.mem.set16(G_ACTIVE_BUILDINGS, v))
    empty = property(lambda s: s.mem.s16(G_EMPTY), lambda s, v: s.mem.set16(G_EMPTY, v))
    warmup = property(lambda s: s.mem.u8(G_WARMUP) != 0,
                      lambda s, v: s.mem.set8(G_WARMUP, 0xFF if v else 0))

    def rand(self) -> int:
        """$195FE: two-word additive generator over the longs at $1236/$123A."""
        m = self.mem
        a, b = m.u32(RNG), m.u32(RNG + 4)
        lo = (a & 0xFFFF) * 2
        x, lo = lo >> 16, lo & 0xFFFF
        s = lo + (b & 0xFFFF) + x
        x, new_lo = s >> 16, s & 0xFFFF
        new_hi = ((a >> 16) + (b >> 16) + x) & 0xFFFF
        m.set32(RNG, (new_hi << 16) | new_lo)
        m.set32(RNG + 4, (a & 0xFFFF0000) | lo)
        return m.u32(RNG)

    def shuffle_words(self, base: int, n: int) -> int:
        """Fisher-Yates over n words at base, as $21BA6/$22304. Returns d0 (2 x last index)."""
        d0 = 0
        for k in range(n - 1, -1, -1):
            idx = ((self.rand() >> 16) * n) >> 16
            d0 = w16(2 * idx)
            a, b = base + 2 * idx, base + 2 * k
            va, vb = self.mem.u16(a), self.mem.u16(b)
            self.mem.set16(a, vb)
            self.mem.set16(b, va)
        return d0

    # ---- world_sim_step ------------------------------------------------------------
    def warmup_run(self) -> None:
        """world_warmup ($18474): 601 steps in fast-build mode."""
        self.warmup = True
        for _ in range(601):
            self.step()
        self.warmup = False

    def step(self) -> None:
        m = self.mem
        i = (self.island_index + 1) & 63
        if i == 0:
            for k in range(SLOTS):
                r = self.rand() & 31
                a, b = SLOT_ORDER + 4 * k, SLOT_ORDER + 4 * r
                va, vb = m.u32(a), m.u32(b)
                m.set32(a, vb)
                m.set32(b, va)
        self.island_index = i
        isl, eco = self.island(i), self.economy(i)
        m.set32(G_ISLAND_PTR, isl.a)
        m.set32(G_ECONOMY_PTR, eco.a)
        m.set32(G_BUFFER_PTR, OBJ_BUFFERS[1 if isl.buffer else 0])
        if self.warmup:
            eco.raw_stock = 500
            eco.goods = eco.goods_cap >> 2
        if self.owner(i) == 2 and self.route(63, i) >= 0:     # enemy, connected to NEMESIS
            eco.goods = 500
        self.census()
        self.structures_step()
        self.plan_construction()

    def compute_class(self) -> None:
        """$1851A: quota column. 0 neutral; else type (7 for type 0), +3 if large."""
        i = self.island_index
        c = self.economy(i).kind
        if c == 0:
            c = 7
        elif self.island(i).area >= 2000:
            c += 3
        self.island_class = 0 if self.owner(i) == 0 else c

    def census(self) -> None:
        """$1854E: mark each slot active while its type still has quota for this class."""
        m = self.mem
        self.building = self.stalled = self.empty = self.active_buildings = 0
        m.buf[QUOTA_WORK:QUOTA_WORK + 64 * QUOTA_ROW] = m.buf[QUOTA_ROM:QUOTA_ROM + 64 * QUOTA_ROW]
        self.compute_class()
        isl, eco, c = self.island(self.island_index), self.economy(self.island_index), self.island_class
        eco.lines = 0
        eco.mines = 0
        eco.has_command_centre = 0
        for s in isl.slots:
            t = s.type
            if t == 0:
                self.empty += 1
                continue
            if t in (0x2C, 0x2D):
                eco.lines += 1
            if t in (0x2A, 0x2E, 0x2B):
                eco.mines += 1
            if t in (0x34, 0x29):
                eco.has_command_centre = 0xFF
            row = QUOTA_WORK + QUOTA_ROW * t
            if t >= T_CONSTRUCTION_FIRST:
                row -= QUOTA_ROW * UNDER_CONSTRUCTION
            quota = m.s8(row + 1 + c)
            if quota < 0:
                row = QUOTA_FALLBACK
                quota = m.s8(row + 1 + c)
            if m.u8(row) == 0:
                continue
            if quota != 0:
                m.set8(row + 1 + c, quota - 1)
                s.set_active(True)
                if t >= T_CONSTRUCTION_FIRST:
                    self.building += 1
                elif t >= T_BUILDING_FIRST:
                    self.active_buildings += 1
            else:
                s.set_active(False)
                if t >= T_CONSTRUCTION_FIRST:
                    self.stalled += 1

    def structures_step(self) -> None:
        """$18662: run each slot's type handler, in the shuffled slot order."""
        m = self.mem
        isl = self.island(self.island_index)
        for k in range(SLOTS):
            s = Slot(m, isl.a + SLOT_BASE + m.u16(SLOT_ORDER + 4 * k + 2))
            t = s.type
            if t >= TYPES:
                raise NotImplementedError(f"slot type ${t:02X}")
            handler = m.u32(HANDLERS + 8 * t)
            max_health, target = m.u16(HANDLERS + 8 * t + 4), m.u16(HANDLERS + 8 * t + 6)
            if handler == H_TREE:
                self.tree(s)
            elif handler == H_BUILDING:
                self.building_step(s, max_health, target)
            elif handler == H_COMMAND_CENTRE:
                self.repair(s, max_health)
            elif handler == H_CONSTRUCTION:
                self.construction(s, max_health, target)
            elif handler == H_COMMAND_CENTRE_CONSTRUCTION:
                self.command_centre_construction(s, max_health, target)
            elif handler:
                raise NotImplementedError(f"handler ${handler:05X} for type ${t:02X}")

    def pay(self, field_addr: int, n: int) -> bool:
        """$186B2 / $186CA: take n from a stock if there is enough (signed compare)."""
        v = self.mem.s16(field_addr)
        if n > v:
            return False
        self.mem.set16(field_addr, v - n)
        return True

    def tree(self, s: Slot) -> None:
        """$18874: once the island has 6 active buildings, idle trees get cleared."""
        if self.active_buildings >= 6 and not s.active and self.rand() & 3 == 0:
            s.type = 0

    def building_step(self, s: Slot, max_health: int, target: int) -> None:
        """$1876E: over quota -> back to construction at full progress; else repair."""
        if not s.active:
            s.type = s.type + UNDER_CONSTRUCTION
            s.value = target
        else:
            self.repair(s, max_health)

    def repair(self, s: Slot, max_health: int) -> None:
        """$18784: health +100 per step up to max_health."""
        if s.value >= s16(max_health):
            return
        s.value = min(s16(s.value + 100), s16(max_health))

    def construction(self, s: Slot, max_health: int, target: int) -> None:
        """$186F6: active -> +1 progress per 30 goods; idle -> decays, refunding 20 goods."""
        eco = self.economy(self.island_index)
        if not s.active:
            v = s.value
            if v == 0:
                if s.type != T_RUNWAY_CONSTRUCTION:
                    s.type = 0
                return
            if self.island_class == 0 and self.rand() & 3:
                return
            s.value = v - 1
            eco.goods = min(s16(eco.goods + 20), s16(eco.goods_cap))
            return
        if self.warmup or s.value == s16(target):
            s.type = s.type - UNDER_CONSTRUCTION
            s.value = max_health
            return
        if self.pay(eco.a + 8, 30):
            s.value = min(s16(s.value + 1), s16(target))

    def command_centre_construction(self, s: Slot, max_health: int, target: int) -> None:
        """$187A0: neutral island -> +4 per 100 raw, completing flips ownership; owned -> -8."""
        v = s.value
        if self.island_class != 0:
            v = s16(v - 8)
            if v > 0:
                s.value = v
            else:
                s.type = 0
            return
        i = self.island_index
        eco = self.economy(i)
        if v != s16(target):
            if self.pay(eco.a + 2, 100):
                s.value = min(s16(v + 4), s16(target))
            return
        s.type = T_COMMAND_CENTRE
        s.value = max_health
        code = s.word6
        eco.kind = code & 3
        eco.goods = 5000
        owner = (code >> 4) + 1
        self.mem.set8(OWNERS + i, owner)
        if not self.warmup:
            self.events.append(("captured", i, owner & 0xFF))
        self.compute_class()
        self.network_invalidate()

    def plan_construction(self) -> None:
        """$18898: with fewer than 2 active constructions, start the highest-priority
        building type ($25-$2E) that still has quota, at a free spot."""
        if self.building >= 2:
            return
        m = self.mem
        best, best_type = 0, 0
        for t in range(T_BUILDING_FIRST + 1, T_BUILDING_LAST + 1):
            row = QUOTA_WORK + QUOTA_ROW * t
            pri = m.s8(row)
            if pri > best and m.u8(row + 1 + self.island_class):
                best, best_type = pri, t
        if not best:
            return
        spot = self.find_free_spot(best_type)
        if spot is None:
            return
        j, x, z = spot
        s = self.island(self.island_index).slots[j]
        s.type = best_type + UNDER_CONSTRUCTION
        s.x, s.z = x, z
        s.value = 0

    def find_free_spot(self, new_type: int, island: int = None):
        """find_free_island_spot ($18A48): up to 16 random positions; a spot is rejected if it
        is within clearance of a non-tree structure. Trees in the way are cut down. Returns
        (slot, x, z): the last empty slot, else the last tree slot clear of the spot."""
        m = self.mem
        isl = self.island(self.island_index if island is None else island)
        r_new = m.u16(RADIUS + 2 * new_type)
        for _ in range(16):
            r = self.rand()
            x = w16(((s16(r) * s16(isl.half_x - 1000 - r_new)) >> 16) * 2)
            z = w16(((s16(r >> 16) * s16(isl.half_z - 1000 - r_new)) >> 16) * 2)
            empty = free_tree = None
            cut = []
            for j, s in enumerate(isl.slots):
                if s.type == 0:
                    empty = j
                    continue
                rad = m.u16(RADIUS + 2 * s.type)
                if rad == 0:
                    continue
                dist = max(abs(s16(s.x - x)), abs(s16(s.z - z)))
                is_tree = T_TREE_FIRST <= s.type <= T_TREE_LAST
                if s16(rad + r_new) > s16(dist):
                    if not is_tree:
                        break
                    cut.append(j)
                elif is_tree:
                    free_tree = j
            else:
                j = empty if empty is not None else free_tree
                if j is None:
                    return None
                for n, k in enumerate(cut):
                    m.set16(SPOT_CUT_LIST + 2 * n, k)
                    isl.slots[k].type = 0
                return j, x, z
        return None

    def network_invalidate(self) -> None:
        """$216C6: refilter links by ownership, reset the route matrix (all -1, zero
        diagonal) and flag it for relaxation by game_main ($2173A, not modelled)."""
        m = self.mem
        network_filter_links(m, [self.owner(i) for i in range(ISLANDS)])
        m.buf[ROUTES:ROUTES + ISLANDS * 128] = b"\xff" * (ISLANDS * 128)
        for i in range(ISLANDS):
            m.set16(ROUTES + 130 * i, 0)
        m.set8(ROUTES_PENDING, 0xFF)
        m.set8(0x1234, 0xFF)
        m.set16(0xDF4, 7)
        m.set32(0xDEC, ROUTES)
        m.set32(0xDF0, NETWORK)

    # ---- logistics tick ($F7A0) ----------------------------------------------------
    def logistics_tick(self) -> None:
        """$F7A0, once per second from game_main. d0 is threaded through the 4-second
        chain because distribute_raw starts its stock array from the value
        run_production leaves in that register."""
        m = self.mem
        m.set8(0x8FD, 0)
        m.set32(TICK_COUNT, m.u32(TICK_COUNT) + 1)
        m.set8(0xD3F, 0xFF)
        # $1B8AE (objects) is not modelled.
        self.count_islands()
        if self.countdown(TIMER_REPAIR, 10):
            self.carrier_repair()
        self.countdown(TIMER_MANTA, 10)       # $1761C, hangar repairs: not modelled
        self.countdown(TIMER_WALRUS, 10)      # $176AC
        if self.countdown(TIMER_984, 1):
            m.set16(0x962, 2)
        m.set16(0x966, 2)
        self.move_raw()
        self.move_goods()
        self.check_carrier_pool()
        if self.countdown(TIMER_LOGISTICS, 4):
            self.produce_raw()
            d0 = self.totals()
            d0 = self.plan_production(d0)
            d0 = self.run_production(d0)
            self.distribute_raw(d0)

    def countdown(self, a: int, reload: int) -> bool:
        v = w16(self.mem.u16(a) - 1)
        self.mem.set16(a, reload if v == 0 else v)
        return v == 0

    def count_islands(self) -> None:
        """$CDF2: owned-island counts for the win/lose check; player islands by kind.
        The kind-0 counter at $D58 is never cleared, so it keeps growing."""
        m = self.mem
        for a in (COUNT_PLAYER, COUNT_ENEMY, 0xD5A, 0xD5C, 0xD5E):
            m.set16(a, 0)
        for i in range(ISLANDS):
            o = self.owner(i)
            if o == 0:
                continue
            if o == 1:
                m.set16(COUNT_PLAYER, m.u16(COUNT_PLAYER) + 1)
                a = COUNT_BY_KIND + 2 * self.economy(i).kind
                m.set16(a, m.u16(a) + 1)
            else:
                m.set16(COUNT_ENEMY, m.u16(COUNT_ENEMY) + 1)

    def carrier_repair(self) -> None:
        """$174E4: share the repair rate between damaged systems of the most urgent class."""
        m = self.mem
        counts = [0, 0, 0]
        for k in range(N_SYSTEMS):
            a = SYSTEMS + 8 * k
            if m.u16(a) == m.u16(a + 2):
                continue
            c = m.u16(a + 4)
            counts[c if c < 2 else 2] += 1
        cls = next((c for c in range(2) if counts[c]), 2)
        n = counts[cls]
        m.set16(REPAIR_CLASS, cls)
        if n == 0:
            if m.u16(REPAIR_COUNT) == 0:
                return
            m.set16(REPAIR_COUNT, 0)
        else:
            m.set16(REPAIR_COUNT, n)
            step = w16((150 * m.u16(REPAIR_RATE) >> 8) // n)
            m.set16(REPAIR_STEP, step)
            for k in range(N_SYSTEMS):
                a = SYSTEMS + 8 * k
                cur, mx = m.u16(a), m.u16(a + 2)
                if m.u16(a + 4) == cls and mx != cur:
                    cur = w16(cur + step)
                    m.set16(a, mx if cur > mx else cur)
        if m.u16(0x718) == 1:
            m.set16(0x952, 2)

    def next_hop(self, cur: int, dest: int):
        """route_next_hop ($21A30): the enabled neighbour of `cur` strictly closer to `dest`
        that minimises weight + distance (unsigned compares). Returns (neighbour word, weight)
        or None."""
        m = self.mem
        d1 = m.u16(ROUTES + 128 * cur + 2 * dest)
        if d1 & 0x8000:
            return None
        net = NETWORK + NETWORK_SIZE * cur
        best, best_w, best_cost = None, 0xFFFF, 0xFFFF
        for k in range(m.u16(net)):
            weight, nb = m.u16(net + 2 + 4 * k), m.u16(net + 4 + 4 * k)
            if nb & 0x8000:
                continue
            d = m.u16(ROUTES + w16(nb << 7) + 2 * dest)
            if d >= d1:
                continue
            d = w16(d + weight)
            if d >= best_cost:
                continue
            best, best_w, best_cost = nb, weight, d
        return None if best is None else (best, best_w)

    def move_raw(self) -> None:
        """$21976: advance raw-material shipments (pool $66BC0) one second."""
        m = self.mem
        for k in range(RAW_POOL_N):
            e = RAW_POOL + POOL_ENTRY * k
            if m.u8(e) & 0x80:
                continue
            m.set8(e + 1, m.u8(e + 1) - 1)
            if m.u8(e + 1):
                continue
            at = m.u8(e)
            if self.owner(at) == 0:
                m.set8(e, 0xFF)
            elif at == m.u16(e + 4):
                eco = self.economy(at)
                v = w16(m.u16(e + 2) + eco.raw_stock)
                if v > eco.raw_cap:
                    m.set8(e + 1, 1)
                else:
                    eco.raw_stock = v
                    m.set8(e, 0xFF)
            elif m.u8(ROUTES_PENDING):
                m.set8(e + 1, 1)
            else:
                hop = self.next_hop(at, m.u16(e + 4))
                if hop is None:
                    # Delivered where it is. On overflow the original stores the
                    # shipment's destination index, not the capacity (a bug).
                    eco = self.economy(at)
                    v = w16(m.u16(e + 2) + eco.raw_stock)
                    eco.raw_stock = m.u16(e + 4) if v > eco.raw_cap else v
                    m.set8(e, 0xFF)
                else:
                    m.set8(e, hop[0])
                    m.set8(e + 1, hop[1])

    def move_goods(self) -> None:
        """$21C28: advance goods shipments (pool $66710, including the carrier pool)."""
        m = self.mem
        for k in range(GOODS_POOL_N):
            e = GOODS_POOL + POOL_ENTRY * k
            if m.u8(e) & 0x80:
                continue
            m.set8(e + 1, m.u8(e + 1) - 1)
            if m.u8(e + 1):
                continue
            at = m.u8(e)
            if self.owner(at) == 0:
                m.set8(e, 0xFF)
                continue
            dest = m.u16(e + 4)
            if dest & 0x8000:
                dest = m.u16(STOCKPILE)
            if dest == at:
                m.set8(e, 0xFF)
                kind, qty = m.u8(e + 2), m.u8(e + 3)
                if not m.u16(e + 4) & 0x8000:
                    eco = self.economy(at)
                    v = w16(qty + eco.goods)
                    eco.goods = eco.goods_cap if v > eco.goods_cap else v
                elif kind < 15:
                    a = STORES + 2 * kind
                    v = w16(m.u16(a) + qty)
                    m.set16(a, STORE_MAX if v > STORE_MAX else v)
                elif kind != 18:
                    a = FUEL + 4 * (kind - 15)
                    v = (m.u32(a) + (qty << 12)) & 0xFFFFFFFF
                    m.set32(a, FUEL_MAX if v > FUEL_MAX else v)
            elif m.u8(ROUTES_PENDING):
                m.set8(e + 1, 1)
            else:
                hop = self.next_hop(at, dest)
                if hop is None:
                    m.set8(e, 0xFF)               # no route: the shipment is lost
                else:
                    m.set8(e, hop[0])
                    m.set8(e + 1, hop[1])

    def check_carrier_pool(self) -> None:
        """$21E8C: clear "Stocks still in transit" once the carrier pool is empty."""
        m = self.mem
        if m.u8(IN_TRANSIT) and all(m.u8(CARRIER_POOL + POOL_ENTRY * k) & 0x80
                                    for k in range(CARRIER_POOL_N)):
            m.set8(IN_TRANSIT, 0)

    def produce_raw(self) -> None:
        """$21906: owned islands mine raw material; neutral islands with a command-centre
        builder accumulate capacity/16 (which pays for the builder)."""
        m = self.mem
        for i in range(ISLANDS):
            eco = self.economy(i)
            if self.owner(i):
                kind = eco.kind & 3
                if kind == 3:
                    continue
                add = (100, eco.mines * 100, eco.lines * 25)[kind]
            elif eco.has_command_centre:
                add = eco.raw_cap >> 4
            else:
                continue
            v = w16(eco.raw_stock + add)
            eco.raw_stock = eco.raw_cap if v > eco.raw_cap else v

    def player_lines(self):
        """(island, line address) for production lines on player bases and factories."""
        m = self.mem
        for i in range(ISLANDS):
            if self.owner(i) != 1:
                continue
            eco = self.economy(i)
            if eco.kind & 3 not in (0, 2):
                continue
            for k in range(eco.lines):
                yield i, eco.line(k)

    def totals(self) -> int:
        """$22020: per-product quantities in stores, pending, in transit and in production;
        per-island goods including deliveries on the way. Returns d0."""
        m = self.mem
        m.buf[TOTALS:TOTALS + 30] = bytes(30)
        m.buf[ISLAND_GOODS_TOTAL:ISLAND_GOODS_TOTAL + 128] = bytes(128)
        m.buf[FUEL_TOTALS:FUEL_TOTALS + 12] = bytes(12)

        def add(kind, qty, dest):
            if kind < 15:
                a = TOTALS + 2 * kind
                m.set16(a, m.u16(a) + qty)
            elif kind == 18:
                a = ISLAND_GOODS_TOTAL + s16(2 * dest)
                m.set16(a, m.u16(a) + qty)
            else:
                a = FUEL_TOTALS + 4 * (kind - 15)
                m.set32(a, m.u32(a) + (qty << 12))

        for k in range(GOODS_POOL_N):
            e = GOODS_POOL + POOL_ENTRY * k
            if not m.u8(e) & 0x80:
                add(m.u8(e + 2), m.u8(e + 3), m.u16(e + 4))
        for _, ln in self.player_lines():
            if not m.u16(ln) & 0x8000:
                add(m.u16(ln + 2), m.u16(ln + 8), m.u16(ln + 6))
        for k in range(N_ITEMS):
            a = TOTALS + 2 * k
            m.set16(a, m.u16(a) + m.u16(STORES_PENDING + 2 * k) + m.u16(STORES + 2 * k))
        for j in range(3):
            a = FUEL_TOTALS + 4 * j
            m.set32(a, m.u32(a) + m.u32(FUEL + 4 * j) + m.u32(FUEL_PENDING + 4 * j))
        for i in range(ISLANDS):
            a = ISLAND_GOODS_TOTAL + 2 * i
            m.set16(a, m.u16(a) + self.economy(i).goods)
        return self.economy(63).goods

    def island_needs(self, i: int) -> int:
        """Planning class of an island's own supply: 1 if its goods (with deliveries) are
        below capacity/4, else 0. (The capacity/2 case tests the economy word +0 == 3,
        which needs kind 0 with byte +1 = 3, so it never applies in practice.)"""
        eco = self.economy(i)
        threshold = eco.goods_cap >> 1
        if self.mem.u16(eco.a) != 3:
            threshold >>= 1
        return 1 if threshold > self.mem.u16(ISLAND_GOODS_TOTAL + 2 * i) else 0

    def plan_production(self, d0: int) -> int:
        """$22180: choose the most urgent class of need and hand its products, shuffled, to
        idle production lines. Returns d0."""
        m = self.mem
        mask = 0
        for k in range(N_ITEMS):
            item = ITEMS + ITEM_SIZE * k
            if m.u16(TOTALS + 2 * k) < m.u16(item + 0x12):
                mask |= 1 << (m.u16(item + 0x10) & 31)
        for j in range(3):
            if m.u32(FUEL_TOTALS + 4 * j) < FUEL_MAX:
                mask |= 1 << (m.u16(FUEL_CLASS + 2 * j) & 31)
        for i in range(ISLANDS):
            if self.owner(i) == 1 and self.route(0, i) >= 0:
                mask |= 1 << self.island_needs(i)
        if not mask & 0xFF:
            return w16(mask)
        cls = 2 if mask & 4 else 1 if mask & 2 else 0
        m.set16(PLAN_CLASS, cls)
        cands = []
        for k in range(N_ITEMS):
            item = ITEMS + ITEM_SIZE * k
            if m.u16(item + 0x10) == cls and m.u16(TOTALS + 2 * k) < m.u16(item + 0x12):
                cands.append(k)
        for j in range(3):
            if m.u16(FUEL_CLASS + 2 * j) == cls and m.u32(FUEL_TOTALS + 4 * j) < FUEL_MAX:
                cands.append(15 + j)
        for i in range(ISLANDS):
            if self.owner(i) == 1 and self.route(0, i) >= 0 and self.island_needs(i) == cls:
                cands.append(0x40 + i)
        if not cands:
            raise NotImplementedError("plan_production with no candidates (needs a class >= 8)")
        m.set16(PLAN_COUNT, len(cands))
        for n, c in enumerate(cands):
            m.set16(PLAN_LIST + 2 * n, c)
        d0 = self.shuffle_words(PLAN_LIST, len(cands))
        left = len(cands)
        for n, (_, ln) in enumerate((x for x in self.player_lines() if m.u16(x[1]) & 0x8000)):
            m.set16(ln, 0)
            c = m.u16(PLAN_LIST + 2 * n)
            if c < 0x40:
                m.set16(ln + 2, c)
                m.set16(ln + 6, 0xFFFF)
                d0 = c
            else:
                m.set16(ln + 2, 18)
                d0 = c - 0x40
                m.set16(ln + 6, d0)
            left -= 1
            if left == 0:
                break
        return d0

    def run_production(self, d0: int) -> int:
        """$21EE8: production lines on player bases and factories. 0 = load the product's
        steps/qty/cost, 1 = pay cost raw per step, 2 = ship the product, then idle (-1).
        Returns d0 (see logistics_tick)."""
        m = self.mem
        for i in range(ISLANDS):
            if self.owner(i) != 1:
                continue
            eco = self.economy(i)
            kind = eco.kind & 3
            d0 = (d0 & 0xFF00) | ((kind - 2) & 0xFF if kind else 0)     # andi.b #3 / subq.b #2
            if kind not in (0, 2):
                continue
            for k in range(eco.lines):
                ln = eco.line(k)
                state = m.s16(ln)
                d0 = w16(state)
                if state < 0:
                    continue
                if state == 0:
                    p = m.u16(ln + 2)
                    d0 = w16(6 * p)
                    t = PRODUCTS + d0
                    m.set16(ln + 4, m.u16(t))
                    m.set16(ln + 8, m.u16(t + 2))
                    m.set16(ln + 10, m.u16(t + 4))
                    m.set16(ln, 1)
                elif state == 1:
                    d0 = w16(eco.raw_stock - m.u16(ln + 10))
                    if d0 & 0x8000:
                        continue
                    eco.raw_stock = d0
                    m.set16(ln + 4, m.u16(ln + 4) - 1)
                    if m.u16(ln + 4) == 0:
                        m.set16(ln, 2)
                else:
                    slot = self.free_goods_slot()
                    if slot is None:
                        d0 = 0xFFFF
                        continue
                    m.set16(slot + 4, m.u16(ln + 6))
                    m.set8(slot + 2, m.u16(ln + 2))
                    m.set8(slot + 3, m.u16(ln + 8))
                    d0 = 0x3F - (63 - i)
                    m.set8(slot, i)
                    m.set8(slot + 1, 1)
                    m.set16(ln, 0xFFFF)
        return d0

    def free_goods_slot(self):
        """$21D2C: first free entry among the first 128 of the goods pool."""
        for k in range(FREE_GOODS_SEARCH):
            e = GOODS_POOL + POOL_ENTRY * k
            if self.mem.u8(e) & 0x80:
                return e
        return None

    def distribute_raw(self, d0: int) -> None:
        """$21AA0: resource islands ship 200 raw to bases and factories connected to VULCAN,
        preferring ones at or below capacity/4, weighted by production lines. The stock
        array starts from the leftover d0 (an original quirk)."""
        m = self.mem
        for i in range(ISLANDS):
            m.set16(RAW_TOTAL + 2 * i, d0)
        for k in range(RAW_POOL_N):
            e = RAW_POOL + POOL_ENTRY * k
            if not m.u8(e) & 0x80:
                a = RAW_TOTAL + s16(2 * m.u16(e + 4))
                m.set16(a, m.u16(a) + m.u16(e + 2))
        for i in range(ISLANDS):
            a = RAW_TOTAL + 2 * i
            m.set16(a, m.u16(a) + self.economy(i).raw_stock)

        def eligible(i):
            eco = self.economy(i)
            return self.owner(i) == 1 and self.route(0, i) >= 0 and eco.kind in (0, 2)

        def needy(i):
            return (self.economy(i).raw_cap >> 2) >= m.u16(RAW_TOTAL + 2 * i)

        any_needy = any(eligible(i) and needy(i) for i in range(ISLANDS))
        n = 0
        for i in range(ISLANDS):
            if eligible(i) and (needy(i) or not any_needy):
                for _ in range(self.economy(i).lines):
                    m.set16(SHIP_LIST + 2 * n, i)
                    n += 1
        if n == 0:
            return
        self.shuffle_words(SHIP_LIST, n)
        left = n
        for i in range(ISLANDS):
            eco = self.economy(i)
            if self.owner(i) != 1 or eco.kind != 1 or eco.raw_stock < 200:
                continue
            eco.raw_stock -= 200
            slot = next((RAW_POOL + POOL_ENTRY * k for k in range(RAW_POOL_N)
                         if m.u8(RAW_POOL + POOL_ENTRY * k) & 0x80), None)
            if slot is None:
                return                  # the 200 already taken are lost
            m.set16(slot + 4, m.u16(SHIP_LIST + 2 * (n - left)))
            m.set16(slot + 2, 200)
            m.set8(slot, i)
            m.set8(slot + 1, 1)
            left -= 1
            if left == 0:
                return

    # ---- per frame (game_main) ----------------------------------------------------
    def frame(self, new_second: bool) -> None:
        """The strategic part of one game_main pass ($40D8): route relaxation while pending,
        the logistics tick when a second has passed, an island step, the enemy AI. How many
        passes make a second depends on the renderer (about 8 in the 3D view)."""
        if self.mem.u8(ROUTES_PENDING):
            self.relax_routes()
        if new_second:
            self.mem.set8(0x8FD, 0xFF)
            self.logistics_tick()
        self.step()
        self.enemy_ai()

    def relax_routes(self) -> None:
        """$2173A: one slice (8 rows) of the Bellman-Ford pass over the route matrix. After 8
        slices, a pass that changed nothing clears the pending flag; otherwise it restarts."""
        m = self.mem
        row, net = m.u32(0xDEC), m.u32(0xDF0)
        for _ in range(8):
            for k in range(m.u16(net)):
                w, nb = m.u16(net + 2 + 4 * k), m.u16(net + 4 + 4 * k)
                if nb & 0x8000:
                    continue
                src = ROUTES + s16(w16(nb << 7))
                for j in range(64):
                    d1, d2 = m.u16(row + 2 * j), m.u16(src + 2 * j)
                    if d2 & 0x8000:
                        continue
                    d2 = w16(d2 + w)
                    if d1 > d2:
                        m.set16(row + 2 * j, d2)
                        m.set8(0x1234, 0)
            row += 128
            net += NETWORK_SIZE
        n = w16(m.u16(0xDF4) - 1)
        if n == 0xFFFF:
            if m.u8(0x1234):
                m.set8(ROUTES_PENDING, 0)
                return
            n, row, net = 7, ROUTES, NETWORK
            m.set8(0x1234, 0xFF)
        m.set16(0xDF4, n)
        m.set32(0xDEC, row)
        m.set32(0xDF0, net)

    # ---- enemy strategy ($1AFAA) ----------------------------------------------------
    def enemy_ai(self) -> None:
        """enemy_ai_step: the enemy carrier's state machine, every frame while [$464] is set.
        States 10/11 (close-range combat with a live carrier) belong to the unit layer."""
        m = self.mem
        if not m.u8(AI_ENABLED):
            return
        state = m.u16(E_STATE)
        if state == 0:
            m.buf[ENEMY_CARRIER + 1] &= 0xFE
            m.set16(E_STATE, 1)
            m.set16(E_STRENGTH, 12000)
            m.set16(0x11C8, 0x7FFF)
            m.set16(E_AIRCRAFT, 8)
            m.set16(0x11CE, 0)
        elif state == 1:
            self.enemy_choose_route()
        elif state == 2:
            if self.player_near_enemy():
                return self.enemy_engage()
            m.set16(E_STATE, 1)
            m.set16(E_STRENGTH, 12000)
            if self.route(NEMESIS, m.u16(E_ISLAND)) >= 0:
                m.set16(E_AIRCRAFT, 8)
                m.set16(E_RESERVE, 12)
        elif state == 3:
            self.enemy_assault()
        elif state == 4:
            if self.player_near_enemy():
                return self.enemy_engage()
            if self.countdown16(E_TIMER):
                m.set16(E_STATE, 1)
        elif state == 5:
            self.enemy_travel()
        elif state in (10, 11):
            raise TacticalMode(state)
        elif state > 11:
            raise NotImplementedError(f"enemy state {state}")

    def countdown16(self, a: int) -> bool:
        v = w16(self.mem.u16(a) - 1)
        self.mem.set16(a, v)
        return v == 0

    def enemy_choose_route(self) -> None:
        """State 1: once routes are settled, pick a target; travel time = distance / 500."""
        m = self.mem
        if m.u8(ROUTES_PENDING):
            return
        self.enemy_choose_target()
        isl = ISLAND_TABLE + ISLAND_SIZE * m.u16(E_TARGET)
        d = approx_distance(m.u32(E_POS_X), m.u32(E_POS_Z), m.u32(isl + 2), m.u32(isl + 6))
        q = d // 500
        m.set16(E_TIMER, 0xFFFF if q > 0xFFFF else q)
        m.set16(E_STATE, 5)

    def enemy_travel(self) -> None:
        """State 5: on arrival, resolve the island abstractly unless it's loaded (player there)."""
        m = self.mem
        if not self.countdown16(E_TIMER):
            return
        i = m.u16(E_TARGET)
        m.set16(E_ISLAND, i)
        isl = ISLAND_TABLE + ISLAND_SIZE * i
        m.set32(ENEMY_CARRIER + 0x0A, m.u32(isl + 2))
        m.set32(ENEMY_CARRIER + 0x12, m.u32(isl + 6))
        if m.u8(isl):
            m.set16(E_STATE, 1)
            return
        m.buf[ENEMY_CARRIER + 1] &= 0xFE
        o = self.owner(i)
        if o == 0:
            self.enemy_drop_builder(i)
        elif o == 1:
            if i != NEMESIS and m.s16(E_STRENGTH) < 3000:
                m.set16(E_STATE, 1)
                return
            m.set16(E_STATE, 3)
            m.set16(B_POOL, 5000)
            m.set16(B_TIMER, 400)
            self.events.append(("attacked", i))
        else:
            m.set16(E_STATE, 2)

    def enemy_drop_builder(self, i: int) -> None:
        """$1B1B6: place an enemy command-centre builder ($34, code $11-$13), then wait 400."""
        m = self.mem
        isl, eco = self.island(i), self.economy(i)
        m.set32(G_ISLAND_PTR, isl.a)
        m.set32(G_ECONOMY_PTR, eco.a)
        m.set32(G_BUFFER_PTR, OBJ_BUFFERS[1 if isl.buffer else 0])
        spot = self.find_free_spot(T_COMMAND_CENTRE_CONSTRUCTION, i)
        if spot is not None:
            j, x, z = spot
            s = isl.slots[j]
            s.type = T_COMMAND_CENTRE_CONSTRUCTION
            s.x, s.z = x, z
            s.value = 0
            while True:
                code = self.rand() & 3
                if code:
                    break
            s.word6 = code + 0x10
        m.set16(E_TIMER, 400)
        m.set16(E_STATE, 4)

    def enemy_assault(self) -> None:
        """State 3: bombard the island; when its command centre falls it turns neutral and
        the enemy drops its own builder. Retreats when weak (except at NEMESIS)."""
        m = self.mem
        if self.player_near_enemy():
            return self.enemy_engage()
        i = m.u16(E_ISLAND)
        if self.enemy_bombard():
            self.events.append(("fallen", i))
            m.set8(OWNERS + i, 0)
            self.network_invalidate()
            if i == m.u16(STOCKPILE):
                m.set8(STOCKPILE, 0xFF)          # st.b on the word's high byte
                self.stores_clear()
            self.enemy_drop_builder(i)
        elif i != NEMESIS and m.s16(E_STRENGTH) <= 2400:
            m.set16(E_STATE, 1)
            self.events.append(("repulsed", i))

    def enemy_bombard(self) -> bool:
        """$1B558, every 200 calls: attack vs. defence, random hits on structures, return
        fire on the enemy. True when the island has no command centre ($29) left."""
        m = self.mem
        if not self.countdown16(B_TIMER):
            return False
        m.set16(B_TIMER, 200)
        i = m.u16(E_ISLAND)
        slots = self.island(i).slots[1:]
        ac = m.s16(E_AIRCRAFT)
        attack = w16((4 if ac > 4 else ac) * 0x400 + (m.u16(E_STRENGTH) >> 1))
        m.set16(B_ATTACK, attack)
        defence, targets = 0, 0
        for s in slots:
            t = s.type
            if t in (0, 7, 0x24):
                continue
            targets += 1
            if 0x25 <= t <= 0x28:
                defence = w16(defence + DEFENCE_WEIGHTS[t - 0x25])
        m.set16(B_DEFENCE, defence)
        m.set16(B_TARGETS, targets)
        d2, d3 = attack, defence
        if m.u16(E_RESERVE) > 4:
            if (self.rand() & 0xFFFF) < 0x4000:
                m.set16(E_RESERVE, m.u16(E_RESERVE) - 1)
                m.set16(B_ATTACK, m.u16(B_ATTACK) + 0x400)
            if d3 > d2:
                d2 = w16(d2 + 0x800)
                if d3 > d2:
                    n = (((self.rand() & 0xFFFF) * 8) >> 16) + 5
                    if n <= m.u16(E_RESERVE):
                        m.set16(E_RESERVE, m.u16(E_RESERVE) - 1)
                        d2 = w16(d2 + 0x400)
        m.set16(B_ATTACK, d2)
        hits = scaled_count(self.rand(), d2)
        returns = scaled_count(self.rand(), d3)
        if hits:
            left = targets
            for _ in range(hits):
                if left == 0:
                    raise NotImplementedError("bombard: no targets on the island")
                n = ((self.rand() & 0xFFFF) * left) >> 16
                live = [s for s in slots if s.type not in (0, 7, 0x24)]
                s = live[n]
                dmg = 4 if s.type >= T_CONSTRUCTION_FIRST else 0x400
                v = s.value & 0xFFFF
                s.value = v - dmg
                if not v > dmg:
                    s.type = 0
                    left -= 1
                    if left == 0:
                        break
        for _ in range(returns):
            weak = i != NEMESIS and m.u16(E_STRENGTH) < 2400
            if not weak:
                if (self.rand() & 0xFFFF) < 0x4000:
                    v = m.s16(E_STRENGTH) - 0x400
                    m.set16(E_STRENGTH, v)
                    if s16(v) < 0:
                        m.set8(AI_ENABLED, 0)
                        m.set16(GAME_END_TIMER, 100)
                        raise EnemyDestroyed()
                    continue
                if m.u16(E_AIRCRAFT) == 0:
                    continue
            pool = m.s16(B_POOL) - 0x400
            m.set16(B_POOL, pool)
            if s16(pool) >= 0:
                continue
            ac = w16(m.u16(E_AIRCRAFT) - 1)
            m.set16(E_AIRCRAFT, ac)
            if ac:
                m.set16(B_POOL, 5000)
        return not any(s.type == T_COMMAND_CENTRE for s in slots)

    def player_near_enemy(self) -> bool:
        """$1B4FA: any active player unit (objects 2-10) within 270000 (Chebyshev)."""
        m = self.mem
        ex, ez = m.u32(ENEMY_CARRIER + 0x0A), m.u32(ENEMY_CARRIER + 0x12)
        for k in range(2, 11):
            o = OBJECTS + OBJECT_SIZE * k
            if not m.u8(o + 1) & 1:
                continue
            dx, dz = abs(s32(m.u32(o + 0x0A) - ex)), abs(s32(m.u32(o + 0x12) - ez))
            if s32(max(dx, dz)) < 270000:
                return True
        return False

    def enemy_engage(self) -> None:
        """$1B250: the carrier becomes a live object 2000 + [$454] beyond a random side of
        its island; state 10. What follows is the unit layer (TacticalMode is raised)."""
        m = self.mem
        m.set16(E_STATE, 10)
        isl = self.island(m.u16(E_ISLAND))
        d0 = m.u16(0x454) + 2000
        dx, dz = isl.half_x + d0, isl.half_z + d0
        r = self.rand() & 0xFFFF
        o = ENEMY_CARRIER
        if not r & 0x8000:
            heading = 0
            if not r & 0x4000:
                dz, heading = -dz, 0x8000
            m.set32(o + 0x12, m.u32(o + 0x12) + dz)
        else:
            heading = 0x4000
            if not r & 0x4000:
                dx, heading = -dx, 0xC000
            m.set32(o + 0x0A, m.u32(o + 0x0A) + dx)
        m.set16(o + 6, heading)
        m.set16(o + 0x1E, heading)
        m.buf[o + 1] |= 1
        m.set16(0x11CE, 0)
        m.set8(0x11D0, 0)
        raise TacticalMode(10)

    def enemy_choose_target(self) -> None:
        """$22C20: NEMESIS first if it isn't enemy-held; when strong, the best-scoring
        island; when weak, the nearest enemy island still connected to NEMESIS."""
        m = self.mem
        ex, ez = m.u32(ENEMY_CARRIER + 0x0A), m.u32(ENEMY_CARRIER + 0x12)
        m.set32(E_POS_X, ex)
        m.set32(E_POS_Z, ez)
        if self.owner(NEMESIS) != 2:
            m.set16(E_TARGET, NEMESIS)
            return
        if m.s16(E_STRENGTH) < 3000 or m.u16(E_AIRCRAFT) == 0:
            best, best_d = None, 32_000_000
            for i in range(ISLANDS):
                isl = ISLAND_TABLE + ISLAND_SIZE * i
                if self.owner(i) != 2 or self.route(i, NEMESIS) < 0:
                    continue
                d = approx_distance(m.u32(isl + 2), m.u32(isl + 6), ex, ez)
                if d <= best_d:
                    best, best_d = i, d
            if best is None:
                raise NotImplementedError("enemy retreat with no island connected to NEMESIS")
            m.set16(E_TARGET, best)
            return
        m.set8(ENEMY_COIN, 0xFF if self.rand() & 0x8000 else 0)
        vul, nem = ISLAND_TABLE + 2, ISLAND_TABLE + ISLAND_SIZE * NEMESIS + 2
        for a, b in ((E_VULCAN_X, vul), (E_VULCAN_Z, vul + 4), (E_NEMESIS_X, nem), (E_NEMESIS_Z, nem + 4)):
            m.set32(a, m.u32(b))
        for i in range(ISLANDS):
            isl = ISLAND_TABLE + ISLAND_SIZE * i
            x, z = m.u32(isl + 2), m.u32(isl + 6)
            d = approx_distance(x, z, ex, ez)
            score = 0xC568 if d > FAR else (-d) >> 11
            dn = approx_distance(x, z, m.u32(E_NEMESIS_X), m.u32(E_NEMESIS_Z)) >> 14
            o = self.owner(i)
            score += dn if o == 2 else -dn
            large = m.u16(isl + 0x0E) > 2000
            score += {0: 3000 if large else 2000, 2: 0xF060}.get(o, 1500 if large else 1000)
            if o != 2:
                net = NETWORK + NETWORK_SIZE * i
                n = m.u16(net)
                if n == 0:
                    raise NotImplementedError("island without links")
                if any(self.route(NEMESIS, m.u16(net + 4 + 4 * k) & 0x3FFF) >= 0 for k in range(n)):
                    score += 4000
            elif m.u8(ENEMY_COIN):
                score = 0x8002
            m.set16(ENEMY_SCORES + 2 * i, score)
        if self.owner(VULCAN) != 2:
            if approx_distance(m.u32(E_VULCAN_X), m.u32(E_VULCAN_Z), ex, ez) <= FAR:
                m.set16(ENEMY_SCORES, m.u16(ENEMY_SCORES) + 5000)
        m.set16(ENEMY_SCORES + 2 * m.u16(E_ISLAND), 0x8001)
        best, best_score = 0, -0x7FFF
        for i in range(ISLANDS):
            sc = m.s16(ENEMY_SCORES + 2 * i)
            if sc > best_score:
                best, best_score = i, sc
        m.set16(E_TARGET, best)

    def stores_clear(self) -> None:
        """$21EAC: the stockpile is lost: empty carrier stores and fuel, and the pending
        stores too unless [$D3E] is set."""
        m = self.mem
        m.buf[STORES:STORES + 30] = bytes(30)
        m.buf[FUEL:FUEL + 12] = bytes(12)
        if not m.u8(0xD3E):
            m.buf[STORES_PENDING:STORES_PENDING + 30] = bytes(30)
            m.buf[FUEL_PENDING:FUEL_PENDING + 12] = bytes(12)


class TacticalMode(Exception):
    """The enemy entered close-range combat (states 10/11), which needs the unit layer."""


class EnemyDestroyed(Exception):
    """Enemy strength went negative: the AI stops and the game-end timer starts."""


def s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def approx_distance(x0: int, z0: int, x1: int, z1: int) -> int:
    """The game's distance estimate: max(|dx|, |dz|) + min/2 (32-bit, unsigned compare)."""
    dx, dz = abs(s32(x0 - x1)) & 0xFFFFFFFF, abs(s32(z0 - z1)) & 0xFFFFFFFF
    hi, lo = (dx, dz) if dz < dx else (dz, dx)
    return (hi + (s32(lo) >> 1)) & 0xFFFFFFFF


def divu(d: int, s: int) -> int:
    """68000 divu.w: 32/16 -> remainder:quotient; on overflow the destination is unchanged."""
    d &= 0xFFFFFFFF
    q, r = divmod(d, s)
    return d if q > 0xFFFF else (r << 16) | q


def scaled_count(r: int, n: int) -> int:
    """$1B62A: (rand.lo * n) >> 16, sign-extended, divu #$600; the low word of the result."""
    hi = (((r & 0xFFFF) * n) >> 16) & 0xFFFF
    return divu(s16(hi) & 0xFFFFFFFF, 0x600) & 0xFFFF



def network_filter_links(m: Mem, owners: list) -> None:
    """$2184E: a link is enabled (bit 15 clear) only when both ends share a non-zero owner."""
    for i in range(ISLANDS):
        base = NETWORK + NETWORK_SIZE * i
        for k in range(m.u16(base)):
            a = base + 4 + 4 * k
            o = owners[m.u16(a) & 0x3FFF]
            if owners[i] != 0 and o != 0 and (o == 1) == (owners[i] == 1):
                m.buf[a] &= 0x7F
            else:
                m.buf[a] |= 0x80
