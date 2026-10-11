"""Reference model of the island simulation: world_sim_step ($1848E) and what it calls.

A Python port of the game's per-island step, written to be checked byte for byte against
the original (tools/check_sim.py). State is plain data (WorldState) loaded from, and written
back to, a 512 KB RAM image, so it can be compared with Hatari captures or with the game's
own code run under Unicorn. docs/simulation.md describes the rules; this file is the
executable version. Word arithmetic follows the 68000 (16-bit wrap, signed compares).

Not modelled (they only matter for the island loaded around the camera, island byte +0
set): mirroring slot changes into the 3D object buffers ($18276/$182A4/$1C6D6), and the
"X is now a friendly/an enemy island" message, which is recorded in WorldState.events.
"""
from dataclasses import dataclass, field

ISLANDS, SLOTS, TYPES = 64, 32, 0x3C
ISLAND_TABLE, ISLAND_SIZE, SLOT_BASE, SLOT_SIZE = 0x69B80, 0x192, 0x12, 12
ECONOMY, ECONOMY_SIZE = 0x66EC0, 0x32
OWNERS = 0x69B40
SLOT_ORDER = 0x66690
QUOTA_ROM, QUOTA_WORK, QUOTA_ROWS, QUOTA_ROW = 0x18DF8, 0x586AA, 64, 10
QUOTA_FALLBACK = 0x18EE8            # the tree row ($18) inside QUOTA_ROM; decremented in place
HANDLERS = 0x18C28                  # 8 bytes per type: handler.l, max_health.w, build_target.w
RADIUS = 0x18BB4                    # clearance radius per type, for find_free_island_spot
NETWORK, NETWORK_SIZE = 0x2239E, 0x20
ROUTES = 0x67B40
RNG = 0x1236
G_ISLAND, G_CLASS = 0x8D0, 0x8D2
G_BUILDING, G_STALLED, G_ACTIVE_BUILDINGS, G_EMPTY = 0x8D4, 0x8D6, 0x8D8, 0x8DA
G_WARMUP = 0x8DC

T_TREE_FIRST, T_TREE_LAST = 0x18, 0x1B
T_RUNWAY, T_BUILDING_FIRST, T_BUILDING_LAST = 0x24, 0x24, 0x2E
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


def rd16(m, a): return int.from_bytes(m[a:a + 2], "big")
def rd32(m, a): return int.from_bytes(m[a:a + 4], "big")
def wr16(m, a, v): m[a:a + 2] = w16(v).to_bytes(2, "big")
def wr32(m, a, v): m[a:a + 4] = (v & 0xFFFFFFFF).to_bytes(4, "big")


@dataclass
class Slot:
    type: int = 0
    heading: int = 0
    value: int = 0          # build progress, or health once finished
    word6: int = 0          # high byte: active flag (census); type $34: start code
    x: int = 0
    z: int = 0

    @property
    def active(self) -> bool:
        return bool(self.word6 & 0xFF00)

    def set_active(self, on: bool) -> None:
        self.word6 = (self.word6 & 0xFF) | (0xFF00 if on else 0)


@dataclass
class Island:
    raw: bytearray          # the full $192-byte entry; header fields are read from it
    slots: list

    @property
    def loaded(self) -> bool:
        return self.raw[0] != 0

    @property
    def half_x(self) -> int: return s16(rd16(self.raw, 0x0A))
    @property
    def half_z(self) -> int: return s16(rd16(self.raw, 0x0C))
    @property
    def area(self) -> int: return s16(rd16(self.raw, 0x0E))


@dataclass
class Economy:
    raw: bytearray          # the full $32-byte record

    def _w(o): return property(lambda s: s16(rd16(s.raw, o)), lambda s, v: wr16(s.raw, o, v))
    def _b(o): return property(lambda s: s.raw[o], lambda s, v: s.raw.__setitem__(o, v & 0xFF))
    kind = _b(0x00)          # 1 resource, 2 factory, 3 defence, 0 base
    raw_stock = _w(0x02)
    raw_cap = _w(0x04)
    mines = _b(0x06)
    has_command_centre = _b(0x07)
    goods = _w(0x08)
    goods_cap = _w(0x0A)
    factories = _w(0x0C)
    del _w, _b


@dataclass
class WorldState:
    islands: list
    economy: list
    owners: list                    # 0 neutral, 1 player, 2 enemy
    routes: list                    # $67B40 route matrix, 64 rows of 64 signed words;
                                    # row 0 = distance from VULCAN, row 63 = from NEMESIS
    slot_order: list                # (slot index, slot offset) pairs
    quota_rom: bytearray            # $18DF8 table, persistently modified via the fallback row
    quota_work: bytearray           # $586AA per-step working copy
    network: bytearray              # $2239E, 64 x 32 bytes
    rng: list                       # two longs at $1236
    handlers: list                  # $18C28 per type: (handler, max_health, build_target);
                                    # patched at game start (command centre health)
    radius: list                    # $18BB4 clearance radius per type
    island_index: int = 0
    island_class: int = 0
    building: int = 0               # active constructions
    stalled: int = 0                # inactive constructions
    active_buildings: int = 0
    empty: int = 0
    warmup: bool = False
    network_invalidated: bool = False
    events: list = field(default_factory=list)

    # ---- RAM image <-> state -------------------------------------------------------
    @classmethod
    def from_ram(cls, m: bytes) -> "WorldState":
        islands = []
        for i in range(ISLANDS):
            raw = bytearray(m[ISLAND_TABLE + ISLAND_SIZE * i:ISLAND_TABLE + ISLAND_SIZE * (i + 1)])
            slots = [Slot(*(rd16(raw, SLOT_BASE + SLOT_SIZE * j + 2 * k) for k in range(6)))
                     for j in range(SLOTS)]
            islands.append(Island(raw, slots))
        return cls(
            islands=islands,
            economy=[Economy(bytearray(m[ECONOMY + ECONOMY_SIZE * i:ECONOMY + ECONOMY_SIZE * (i + 1)]))
                     for i in range(ISLANDS)],
            owners=list(m[OWNERS:OWNERS + ISLANDS]),
            routes=[[s16(rd16(m, ROUTES + 128 * i + 2 * j)) for j in range(ISLANDS)] for i in range(ISLANDS)],
            slot_order=[(rd16(m, SLOT_ORDER + 4 * k), rd16(m, SLOT_ORDER + 4 * k + 2)) for k in range(SLOTS)],
            quota_rom=bytearray(m[QUOTA_ROM:QUOTA_ROM + QUOTA_ROWS * QUOTA_ROW]),
            quota_work=bytearray(m[QUOTA_WORK:QUOTA_WORK + QUOTA_ROWS * QUOTA_ROW]),
            network=bytearray(m[NETWORK:NETWORK + ISLANDS * NETWORK_SIZE]),
            rng=[rd32(m, RNG), rd32(m, RNG + 4)],
            handlers=[(rd32(m, HANDLERS + 8 * t), rd16(m, HANDLERS + 8 * t + 4), rd16(m, HANDLERS + 8 * t + 6))
                      for t in range(TYPES)],
            radius=[rd16(m, RADIUS + 2 * t) for t in range(TYPES)],
            island_index=rd16(m, G_ISLAND), island_class=rd16(m, G_CLASS),
            building=rd16(m, G_BUILDING), stalled=rd16(m, G_STALLED),
            active_buildings=rd16(m, G_ACTIVE_BUILDINGS), empty=rd16(m, G_EMPTY),
            warmup=m[G_WARMUP] != 0)

    def to_ram(self, m: bytearray) -> None:
        for i, isl in enumerate(self.islands):
            for j, s in enumerate(isl.slots):
                for k, v in enumerate((s.type, s.heading, s.value, s.word6, s.x, s.z)):
                    wr16(isl.raw, SLOT_BASE + SLOT_SIZE * j + 2 * k, v)
            m[ISLAND_TABLE + ISLAND_SIZE * i:ISLAND_TABLE + ISLAND_SIZE * (i + 1)] = isl.raw
        for i, e in enumerate(self.economy):
            m[ECONOMY + ECONOMY_SIZE * i:ECONOMY + ECONOMY_SIZE * (i + 1)] = e.raw
        m[OWNERS:OWNERS + ISLANDS] = bytes(self.owners)
        for i, row in enumerate(self.routes):
            for j, v in enumerate(row):
                wr16(m, ROUTES + 128 * i + 2 * j, v)
        for k, (idx, off) in enumerate(self.slot_order):
            wr16(m, SLOT_ORDER + 4 * k, idx)
            wr16(m, SLOT_ORDER + 4 * k + 2, off)
        m[QUOTA_ROM:QUOTA_ROM + len(self.quota_rom)] = self.quota_rom
        m[QUOTA_WORK:QUOTA_WORK + len(self.quota_work)] = self.quota_work
        m[NETWORK:NETWORK + len(self.network)] = self.network
        wr32(m, RNG, self.rng[0])
        wr32(m, RNG + 4, self.rng[1])
        for a, v in ((G_ISLAND, self.island_index), (G_CLASS, self.island_class),
                     (G_BUILDING, self.building), (G_STALLED, self.stalled),
                     (G_ACTIVE_BUILDINGS, self.active_buildings), (G_EMPTY, self.empty)):
            wr16(m, a, v)
        m[G_WARMUP] = 0xFF if self.warmup else 0
        if self.network_invalidated:
            set_route_relax_pending(m)

    # ---- primitives ----------------------------------------------------------------
    def rand(self) -> int:
        """$195FE: two-word additive generator over the longs at $1236/$123A."""
        a, b = self.rng
        lo = (a & 0xFFFF) * 2
        x, lo = lo >> 16, lo & 0xFFFF
        t = lo
        s = lo + (b & 0xFFFF) + x
        x, new_lo = s >> 16, s & 0xFFFF
        new_hi = ((a >> 16) + (b >> 16) + x) & 0xFFFF
        self.rng = [(new_hi << 16) | new_lo, (a & 0xFFFF0000) | t]
        return self.rng[0]

    # ---- world_sim_step ------------------------------------------------------------
    def warmup_run(self) -> None:
        """world_warmup ($18474): 601 steps in fast-build mode."""
        self.warmup = True
        for _ in range(601):
            self.step()
        self.warmup = False

    def step(self) -> None:
        i = (self.island_index + 1) & 63
        if i == 0:
            for k in range(SLOTS):
                r = self.rand() & 31
                self.slot_order[k], self.slot_order[r] = self.slot_order[r], self.slot_order[k]
        self.island_index = i
        eco = self.economy[i]
        if self.warmup:
            eco.raw_stock = 500
            eco.goods = w16(eco.goods_cap) >> 2
        if self.owners[i] == 2 and self.routes[63][i] >= 0:     # enemy, connected to NEMESIS
            eco.goods = 500
        self.census()
        self.structures_step()
        self.plan_construction()

    def compute_class(self) -> None:
        """$1851A: quota column. 0 neutral; else type (7 for type 0), +3 if large."""
        i = self.island_index
        c = self.economy[i].kind
        if c == 0:
            c = 7
        elif self.islands[i].area >= 2000:
            c += 3
        self.island_class = 0 if self.owners[i] == 0 else c

    def census(self) -> None:
        """$1854E: mark each slot active while its type still has quota for this class."""
        self.building = self.stalled = self.empty = self.active_buildings = 0
        self.quota_work[:] = self.quota_rom
        self.compute_class()
        isl, eco, c = self.islands[self.island_index], self.economy[self.island_index], self.island_class
        eco.factories = 0
        eco.mines = 0
        eco.has_command_centre = 0
        for s in isl.slots:
            t = s.type
            if t == 0:
                self.empty += 1
                continue
            if t in (0x2C, 0x2D):
                eco.factories += 1
            if t in (0x2A, 0x2E, 0x2B):
                eco.mines += 1
            if t in (0x34, 0x29):
                eco.has_command_centre = 0xFF
            table, row = self.quota_work, QUOTA_ROW * t
            if t >= T_CONSTRUCTION_FIRST:
                row -= QUOTA_ROW * UNDER_CONSTRUCTION
            quota = s8(table[row + 1 + c])
            if quota < 0:
                table, row = self.quota_rom, QUOTA_FALLBACK - QUOTA_ROM
                quota = s8(table[row + 1 + c])
            if table[row] == 0:
                continue
            if quota != 0:
                table[row + 1 + c] = (quota - 1) & 0xFF
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
        for _, off in self.slot_order:
            s = self.islands[self.island_index].slots[off // SLOT_SIZE]
            handler, max_health, target = self.handlers[s.type] if s.type < TYPES else (0, 0, 0)
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
                raise NotImplementedError(f"handler ${handler:05X} for type ${s.type:02X}")

    def pay_goods(self, n: int) -> bool:
        eco = self.economy[self.island_index]
        if n > eco.goods:
            return False
        eco.goods -= n
        return True

    def pay_raw(self, n: int) -> bool:
        eco = self.economy[self.island_index]
        if n > eco.raw_stock:
            return False
        eco.raw_stock -= n
        return True

    def tree(self, s: Slot) -> None:
        """$18874: once the island has 6 active buildings, idle trees get cleared."""
        if s16(self.active_buildings) >= 6 and not s.active and self.rand() & 3 == 0:
            s.type = 0

    def building_step(self, s: Slot, max_health: int, target: int) -> None:
        """$1876E: over quota -> back to construction at full progress; else repair."""
        if not s.active:
            s.type = w16(s.type + UNDER_CONSTRUCTION)
            s.value = target
        else:
            self.repair(s, max_health)

    def repair(self, s: Slot, max_health: int) -> None:
        """$18784: health +100 per step up to max_health."""
        v = s16(s.value)
        if v >= s16(max_health):
            return
        s.value = min(s16(v + 100), s16(max_health))

    def construction(self, s: Slot, max_health: int, target: int) -> None:
        """$186F6: active -> +1 progress per 30 goods; idle -> decays, refunding 20 goods."""
        eco = self.economy[self.island_index]
        if not s.active:
            v = s16(s.value)
            if v == 0:
                if s.type != T_RUNWAY_CONSTRUCTION:
                    s.type = 0
                return
            if self.island_class == 0 and self.rand() & 3:
                return
            s.value = w16(v - 1)
            eco.goods = min(s16(eco.goods + 20), s16(eco.goods_cap))
            return
        if self.warmup or s16(s.value) == s16(target):
            s.type = w16(s.type - UNDER_CONSTRUCTION)
            s.value = max_health
            return
        if self.pay_goods(30):
            s.value = min(s16(s.value + 1), s16(target))

    def command_centre_construction(self, s: Slot, max_health: int, target: int) -> None:
        """$187A0: neutral island -> +4 per 100 raw, completing flips ownership; owned -> -8."""
        v = s16(s.value)
        if self.island_class != 0:
            v = s16(v - 8)
            if v > 0:
                s.value = w16(v)
            else:
                s.type = 0
            return
        if v != s16(target):
            if self.pay_raw(100):
                s.value = min(s16(v + 4), s16(target))
            return
        i = self.island_index
        s.type = T_COMMAND_CENTRE
        s.value = max_health
        code = s.word6
        eco = self.economy[i]
        eco.kind = code & 3
        eco.goods = 5000
        owner = ((code >> 4) + 1) & 0xFF
        self.owners[i] = owner
        if not self.warmup:
            self.events.append(("captured", i, owner))
        self.compute_class()
        self.network_invalidate()

    def plan_construction(self) -> None:
        """$18898: with fewer than 2 active constructions, start the highest-priority
        building type ($25-$2E) that still has quota, at a free spot."""
        if s16(self.building) >= 2:
            return
        best, best_type = 0, 0
        for t in range(T_BUILDING_FIRST + 1, T_BUILDING_LAST + 1):
            row = QUOTA_ROW * t
            pri = s8(self.quota_work[row])
            if pri > best and self.quota_work[row + 1 + self.island_class]:
                best, best_type = pri, t
        if not best:
            return
        spot = self.find_free_spot(best_type)
        if spot is None:
            return
        j, x, z = spot
        s = self.islands[self.island_index].slots[j]
        s.type = best_type + UNDER_CONSTRUCTION
        s.x, s.z = x, z
        s.value = 0

    def find_free_spot(self, new_type: int):
        """find_free_island_spot ($18A48): up to 16 random positions; a spot is rejected if it
        is within clearance of a non-tree structure. Trees in the way are cut down. Returns
        (slot, x, z): the last empty slot, else the last tree slot clear of the spot."""
        isl = self.islands[self.island_index]
        r_new = self.radius[new_type]
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
                rad = self.radius[s.type] if s.type < TYPES else 0
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
                for k in cut:
                    isl.slots[k].type = 0
                return j, x, z
        return None

    def network_invalidate(self) -> None:
        """$216C6: refilter links by ownership, reset the route matrix (all -1, zero
        diagonal) and flag it for relaxation by game_main ($2173A, not modelled here)."""
        network_filter_links(self.network, self.owners)
        self.routes = [[0 if i == j else -1 for j in range(ISLANDS)] for i in range(ISLANDS)]
        self.network_invalidated = True


def network_filter_links(net: bytearray, owners: list) -> None:
    """$2184E: a link is enabled (bit 15 clear) only when both ends share a non-zero owner."""
    for i in range(ISLANDS):
        base = NETWORK_SIZE * i
        for k in range(rd16(net, base)):
            a = base + 4 + 4 * k
            o = owners[rd16(net, a) & 0x3FFF]
            if owners[i] != 0 and o != 0 and (o == 1) == (owners[i] == 1):
                net[a] &= 0x7F
            else:
                net[a] |= 0x80


def set_route_relax_pending(m: bytearray) -> None:
    """The relaxer state $216C6 sets up for game_main's incremental route recompute."""
    m[0x1235] = 0xFF
    m[0x1234] = 0xFF
    wr16(m, 0xDF4, 7)
    wr32(m, 0xDEC, ROUTES)
    wr32(m, 0xDF0, NETWORK)

