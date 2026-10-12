/**
 * Reference model of the strategy simulation: the island step (world_sim_step, $1848E)
 * and the once-a-second logistics tick ($F7A0).
 *
 * A TypeScript port of tools/stsim.py, itself a port of the game's code, written to be checked
 * byte for byte against the original (tools/check_sim.py). docs/simulation.md describes the
 * rules; this file is the executable version.
 *
 * State is the game's own memory layout: a 512 KB image in which only STATE_REGIONS are
 * populated (WorldState.from_ram copies just those, so anything the model reads outside them
 * reads as zero and shows up as a mismatch). Islands, slots and economy records are views
 * onto that image. The image is trivially serialisable and moves both ways between the model
 * and an emulator. Arithmetic follows the 68000: 16-bit wrap, and signed or unsigned
 * compares exactly where the original uses them. A few routines depend on a register value
 * left over by the previous routine; the model carries it (see `logistics_tick`).
 *
 * Not modelled: the parts of the tick that act on units (`$1B8AE`, and the hangar repairs
 * `$1761C`/`$176AC`), the 3D object buffers of the island loaded around the camera (island
 * byte +0), and on-screen messages, which are recorded in WorldState.events instead.
 */
import { Mem, RAM_SIZE, NotPorted, pyDiv, shr, u32, w16, s16, s8, s32, divu } from "./mem.ts";
import type { Region, SimEvent } from "./mem.ts";

// stsim defines its own w16/s16/s8/s32/divu/Mem; the mem.ts versions have the same semantics,
// so they are re-exported under the stsim names.
export { Mem, RAM_SIZE, w16, s16, s8, s32, divu };

export const ISLANDS = 64, SLOTS = 32, TYPES = 0x3c;

// island step
export const ISLAND_TABLE = 0x69b80, ISLAND_SIZE = 0x192, SLOT_BASE = 0x12, SLOT_SIZE = 12;
export const ECONOMY = 0x66ec0, ECONOMY_SIZE = 0x32;
export const OWNERS = 0x69b40;
export const SLOT_ORDER = 0x66690;
export const QUOTA_ROM = 0x18df8, QUOTA_WORK = 0x586aa, QUOTA_ROW = 10;
export const QUOTA_FALLBACK = 0x18ee8; // the tree row ($18) inside QUOTA_ROM; decremented in place
export const HANDLERS = 0x18c28; // 8 bytes per type: handler.l, max_health.w, build_target.w
export const RADIUS = 0x18bb4; // clearance radius per type, for find_free_island_spot
export const SPOT_CUT_LIST = 0x58e2a; // find_free_island_spot: trees in the way (scratch)
export const NETWORK = 0x2239e, NETWORK_SIZE = 0x20;
export const ROUTES = 0x67b40; // route matrix, 64 x 64 words; row 0 VULCAN, row 63 NEMESIS
export const RNG = 0x1236;
export const OBJ_BUFFERS: readonly [number, number] = [0x62cda, 0x63cec]; // 3D object buffers, chosen by island byte +1
export const G_ISLAND_PTR = 0x8c4, G_ECONOMY_PTR = 0x8c8, G_BUFFER_PTR = 0x8cc;
export const G_ISLAND = 0x8d0, G_CLASS = 0x8d2;
export const G_BUILDING = 0x8d4, G_STALLED = 0x8d6, G_ACTIVE_BUILDINGS = 0x8d8, G_EMPTY = 0x8da;
export const G_WARMUP = 0x8dc;
export const ROUTES_PENDING = 0x1235; // set by network_invalidate until game_main has relaxed

// logistics
export const RAW_POOL = 0x66bc0, RAW_POOL_N = 128; // at.b timer.b amount.w dest.w
export const GOODS_POOL = 0x66710, GOODS_POOL_N = 200; // at.b timer.b kind.b qty.b dest.w; the last 72
export const CARRIER_POOL = 0x66a10, CARRIER_POOL_N = 72; // entries are the carrier-delivery pool
export const FREE_GOODS_SEARCH = 128; // production only allocates from the first 128
export const POOL_ENTRY = 6;
export const LINES = 0x0e; // production lines in the economy record, 12 bytes
export const PRODUCTS = 0x21fae; // steps.w qty.w cost.w per product
export const ITEMS = 0xb76, ITEM_SIZE = 0x1a, N_ITEMS = 15; // store items: +$10 class.w, +$12 target.w
export const FUEL_CLASS = 0xd08; // 3 words
export const STOCKPILE = 0xd4a; // island where goods for the carrier collect
export const CARRIER_ISLAND = 0xd4e; // where carrier-pool shipments start ($21D46)
export const IN_TRANSIT = 0xd50; // "Stocks still in transit" flag
export const COUNT_PLAYER = 0xd54, COUNT_ENEMY = 0xd56, COUNT_BY_KIND = 0xd58;
export const FUEL = 0xd60, FUEL_PENDING = 0xd6c, STORES = 0xd78, STORES_PENDING = 0xd96;
export const FUEL_MAX = 0x1fffc0, STORE_MAX = 999;
export const PLAN_CLASS = 0xdf6, PLAN_COUNT = 0xdf8, TOTALS = 0xdfa, ISLAND_GOODS_TOTAL = 0xe18;
export const PLAN_LIST = 0xe98, FUEL_TOTALS = 0xf3c, RAW_TOTAL = 0xf48, SHIP_LIST = 0xfc8;
export const SYSTEMS = 0xaf4, N_SYSTEMS = 10; // carrier systems: cur.w max.w class.w ?.w
export const REPAIR_RATE = 0xb3a;
export const REPAIR_CLASS = 0xb5c, REPAIR_STEP = 0xb5e, REPAIR_COUNT = 0xb60;
export const TICK_COUNT = 0x97a; // long, seconds of game time
export const TIMER_REPAIR = 0x97e, TIMER_MANTA = 0x980, TIMER_WALRUS = 0x982, TIMER_984 = 0x984, TIMER_LOGISTICS = 0x986;

// enemy strategy
export const ENEMY = 0x11a6, ENEMY_END = 0x11d6;
export const E_POS_X = 0x11a6, E_POS_Z = 0x11aa, E_VULCAN_X = 0x11ae, E_VULCAN_Z = 0x11b2, E_NEMESIS_X = 0x11b6, E_NEMESIS_Z = 0x11ba;
export const E_TARGET = 0x11be, E_ISLAND = 0x11c0, E_STATE = 0x11c2, E_TIMER = 0x11c4, E_STRENGTH = 0x11c6;
export const E_AIRCRAFT = 0x11ca, E_RESERVE = 0x11d2;
export const ENEMY_SCORES = 0x22b9e, ENEMY_COIN = 0x22c1e; // scratch in the code area; the coin persists
export const AI_ENABLED = 0x464, GAME_END_TIMER = 0x732;
export const B_DEFENCE = 0x948, B_ATTACK = 0x94a, B_TARGETS = 0x94c, B_TIMER = 0x94e, B_POOL = 0x950;
export const DEFENCE_WEIGHTS: readonly number[] = [0x1000, 0x200, 0x200, 0x300]; // $1B73E, building types $25-$28
export const OBJECTS = 0x5dcda, OBJECT_SIZE = 0x140;
export const OBJECT_HEADER = 0x40; // +$40 on: per-object 3D caches (unit layer)
export const ENEMY_CARRIER = OBJECTS + OBJECT_SIZE * 19; // object 19
export const NEMESIS = 63, VULCAN = 0;
export const FAR = 3_000_000;

const unitRegions: Region[] = [];
for (let k = 2; k < 11; k++) unitRegions.push([`unit ${k}`, OBJECTS + OBJECT_SIZE * k, OBJECT_HEADER]);

// Regions that make up the simulation state: (name, start, length). from_ram/to_ram copy
// these; check_sim compares them except for the inputs the model never writes.
export const STATE_REGIONS: Region[] = [
  ["current screen $718", 0x718, 2],
  ["step globals", G_ISLAND_PTR, 0x8de - G_ISLAND_PTR],
  ["second flags $8FC", 0x8fc, 2],
  ["redraw flags $952", 0x952, 2],
  ["redraw flag $962", 0x962, 2],
  ["redraw flag $966", 0x966, 2],
  ["tick counters", TICK_COUNT, 0x988 - TICK_COUNT],
  ["carrier systems", SYSTEMS, 0xb62 - SYSTEMS],
  ["store items", ITEMS, N_ITEMS * ITEM_SIZE],
  ["fuel classes", FUEL_CLASS, 6],
  ["logistics flags $D3E", 0xd3e, 2],
  ["logistics globals", STOCKPILE, 0xd60 - STOCKPILE],
  ["fuel and stores", FUEL, 0xdb4 - FUEL],
  ["totals", PLAN_CLASS, FUEL_TOTALS - PLAN_CLASS],
  ["fuel totals, raw totals", FUEL_TOTALS, SHIP_LIST - FUEL_TOTALS],
  ["ship list", SHIP_LIST, 0x180],
  ["route flags, rng", 0x1234, 10],
  ["radius table", RADIUS, TYPES * 2],
  ["handler table", HANDLERS, TYPES * 8],
  ["quota table", QUOTA_ROM, 64 * QUOTA_ROW],
  ["product table", PRODUCTS, 19 * 6],
  ["network", NETWORK, ISLANDS * NETWORK_SIZE],
  ["quota work", QUOTA_WORK, 64 * QUOTA_ROW],
  ["slot order", SLOT_ORDER, GOODS_POOL - SLOT_ORDER],
  ["goods pool", GOODS_POOL, CARRIER_POOL - GOODS_POOL],
  ["carrier pool", CARRIER_POOL, RAW_POOL - CARRIER_POOL],
  ["raw pool", RAW_POOL, ECONOMY - RAW_POOL],
  ["economy", ECONOMY, ROUTES - ECONOMY],
  ["routes", ROUTES, OWNERS - ROUTES],
  ["route relax state", 0xdec, 0xa],
  ["owners", OWNERS, ISLANDS],
  ["islands", ISLAND_TABLE, ISLANDS * ISLAND_SIZE],
  ["difficulty", 0x444, 0x12],
  ["ai flags $464", 0x464, 2],
  ["game end timer", 0x732, 2],
  ["bombard state", 0x948, 0xa],
  ["enemy globals", ENEMY, ENEMY_END - ENEMY],
  ["enemy scores", ENEMY_SCORES, 0x82],
  ...unitRegions,
  ["enemy carrier", ENEMY_CARRIER, OBJECT_HEADER],
];
// Read but never written by the model (set by the UI, the unit layer or game setup).
export const INPUT_REGIONS: Set<string> = new Set([
  "current screen $718", "store items", "fuel classes", "radius table",
  "handler table", "product table", "difficulty",
  ...unitRegions.map((r) => r[0]),
]);

export const T_TREE_FIRST = 0x18, T_TREE_LAST = 0x1b;
export const T_BUILDING_FIRST = 0x24, T_BUILDING_LAST = 0x2e;
export const T_COMMAND_CENTRE = 0x29, T_CONSTRUCTION_FIRST = 0x2f, T_RUNWAY_CONSTRUCTION = 0x2f;
export const T_COMMAND_CENTRE_CONSTRUCTION = 0x34;
export const UNDER_CONSTRUCTION = 0xb; // construction type = finished type + $B

export const H_TREE = 0x18874, H_BUILDING = 0x1876e, H_COMMAND_CENTRE = 0x18784;
export const H_CONSTRUCTION = 0x186f6, H_COMMAND_CENTRE_CONSTRUCTION = 0x187a0;

// (stsim's `_field(offset, kind)` descriptor becomes explicit accessors below:
//  b -> u8/set8, w -> s16/set16, uw -> u16/set16.)

/** Structure slot (12 bytes): type, heading, value (progress or health), word6 (high
 * byte = active flag; type $34: start code), x, z. */
export class Slot {
  m: Mem;
  a: number;

  constructor(m: Mem, a: number) {
    this.m = m;
    this.a = a;
  }

  get type(): number { return this.m.u16(this.a + 0); }
  set type(v: number) { this.m.set16(this.a + 0, v); }
  get heading(): number { return this.m.u16(this.a + 2); }
  set heading(v: number) { this.m.set16(this.a + 2, v); }
  get value(): number { return this.m.s16(this.a + 4); }
  set value(v: number) { this.m.set16(this.a + 4, v); }
  get word6(): number { return this.m.u16(this.a + 6); }
  set word6(v: number) { this.m.set16(this.a + 6, v); }
  get x(): number { return this.m.s16(this.a + 8); }
  set x(v: number) { this.m.set16(this.a + 8, v); }
  get z(): number { return this.m.s16(this.a + 10); }
  set z(v: number) { this.m.set16(this.a + 10, v); }

  get active(): boolean {
    return this.m.u8(this.a + 6) !== 0;
  }

  set_active(on: boolean): void {
    this.m.set8(this.a + 6, on ? 0xff : 0);
  }
}

export class Island {
  m: Mem;
  a: number;
  slots: Slot[];

  constructor(m: Mem, i: number) {
    this.m = m;
    this.a = ISLAND_TABLE + ISLAND_SIZE * i;
    this.slots = [];
    for (let j = 0; j < SLOTS; j++) this.slots.push(new Slot(m, this.a + SLOT_BASE + SLOT_SIZE * j));
  }

  get loaded(): number { return this.m.u8(this.a + 0); }
  set loaded(v: number) { this.m.set8(this.a + 0, v); }
  get buffer(): number { return this.m.u8(this.a + 1); }
  set buffer(v: number) { this.m.set8(this.a + 1, v); }
  get half_x(): number { return this.m.s16(this.a + 0x0a); }
  set half_x(v: number) { this.m.set16(this.a + 0x0a, v); }
  get half_z(): number { return this.m.s16(this.a + 0x0c); }
  set half_z(v: number) { this.m.set16(this.a + 0x0c, v); }
  get area(): number { return this.m.s16(this.a + 0x0e); }
  set area(v: number) { this.m.set16(this.a + 0x0e, v); }
}

/** Economy record ($32 bytes). Production lines follow at +$0E, `lines` of them. */
export class Economy {
  m: Mem;
  a: number;

  constructor(m: Mem, i: number) {
    this.m = m;
    this.a = ECONOMY + ECONOMY_SIZE * i;
  }

  /** 0 base, 1 resource, 2 factory, 3 defence */
  get kind(): number { return this.m.u8(this.a + 0x00); }
  set kind(v: number) { this.m.set8(this.a + 0x00, v); }
  get raw_stock(): number { return this.m.u16(this.a + 0x02); }
  set raw_stock(v: number) { this.m.set16(this.a + 0x02, v); }
  get raw_cap(): number { return this.m.u16(this.a + 0x04); }
  set raw_cap(v: number) { this.m.set16(this.a + 0x04, v); }
  get mines(): number { return this.m.u8(this.a + 0x06); }
  set mines(v: number) { this.m.set8(this.a + 0x06, v); }
  get has_command_centre(): number { return this.m.u8(this.a + 0x07); }
  set has_command_centre(v: number) { this.m.set8(this.a + 0x07, v); }
  get goods(): number { return this.m.u16(this.a + 0x08); }
  set goods(v: number) { this.m.set16(this.a + 0x08, v); }
  get goods_cap(): number { return this.m.u16(this.a + 0x0a); }
  set goods_cap(v: number) { this.m.set16(this.a + 0x0a, v); }
  /** = factories counted by the census */
  get lines(): number { return this.m.u16(this.a + 0x0c); }
  set lines(v: number) { this.m.set16(this.a + 0x0c, v); }

  line(k: number): number {
    return this.a + LINES + 12 * k;
  }
}

export class WorldState {
  mem: Mem;
  events: SimEvent[] = [];
  /** The X flag the 68000 is left with after `rand` (set by rand). */
  rand_carry = 0;

  constructor(mem: Mem) {
    this.mem = mem;
  }

  static from_ram<T extends WorldState>(this: new (mem: Mem) => T, ram: Uint8Array): T {
    const state = new this(new Mem(new Uint8Array(RAM_SIZE)));
    state.load_regions(ram);
    return state;
  }

  /** The from_ram copy: subclasses add their regions (Python's from_ram overrides). */
  load_regions(ram: Uint8Array): void {
    for (const [, a, n] of STATE_REGIONS) this.mem.buf.set(ram.subarray(a, a + n), a);
  }

  to_ram(ram: Uint8Array): void {
    for (const [, a, n] of STATE_REGIONS) ram.set(this.mem.buf.subarray(a, a + n), a);
  }

  // ---- views and globals ---------------------------------------------------------
  island(i: number): Island { return new Island(this.mem, i); }
  economy(i: number): Economy { return new Economy(this.mem, i); }
  owner(i: number): number { return this.mem.u8(OWNERS + i); }
  route(i: number, j: number): number { return this.mem.s16(ROUTES + 128 * i + 2 * j); }

  /** 1 if the message routine plays its ping (jsr $20B90, id 0 from $A0DE (first),
   * 1 from $A128): message ping on ([$1247] = 0) and the header byte's bit 7 clear;
   * $A0DE also pings a bit-7 message while sound effects are off ([$1249] != 0).
   * docs/units.md §17. The third element of every ["message", text, ping] event. */
  _ping(bit7 = false, first = false): number {
    const m = this.mem;
    if (m.u8(0x1247)) return 0;
    return !bit7 || (first && m.u8(0x1249)) ? 1 : 0;
  }

  get island_index(): number { return this.mem.u16(G_ISLAND); }
  set island_index(v: number) { this.mem.set16(G_ISLAND, v); }
  get island_class(): number { return this.mem.u16(G_CLASS); }
  set island_class(v: number) { this.mem.set16(G_CLASS, v); }
  get building(): number { return this.mem.s16(G_BUILDING); }
  set building(v: number) { this.mem.set16(G_BUILDING, v); }
  get stalled(): number { return this.mem.s16(G_STALLED); }
  set stalled(v: number) { this.mem.set16(G_STALLED, v); }
  get active_buildings(): number { return this.mem.s16(G_ACTIVE_BUILDINGS); }
  set active_buildings(v: number) { this.mem.set16(G_ACTIVE_BUILDINGS, v); }
  get empty(): number { return this.mem.s16(G_EMPTY); }
  set empty(v: number) { this.mem.set16(G_EMPTY, v); }
  get warmup(): boolean { return this.mem.u8(G_WARMUP) !== 0; }
  set warmup(v: boolean) { this.mem.set8(G_WARMUP, v ? 0xff : 0); }

  /** $195FE: two-word additive generator over the longs at $1236/$123A. */
  rand(): number {
    const m = this.mem;
    const a = m.u32(RNG), b = m.u32(RNG + 4);
    let lo = (a & 0xffff) * 2;
    let x = lo >> 16;
    lo = lo & 0xffff;
    const s = lo + (b & 0xffff) + x;
    x = s >> 16;
    const new_lo = s & 0xffff;
    const hi = (a >>> 16) + (b >>> 16) + x;
    this.rand_carry = hi >> 16; // the X flag the 68000 is left with
    const new_hi = hi & 0xffff;
    m.set32(RNG, new_hi * 0x10000 + new_lo);
    m.set32(RNG + 4, ((a & 0xffff0000) | lo) >>> 0);
    return m.u32(RNG);
  }

  /** Fisher-Yates over n words at base, as $21BA6/$22304. Returns d0 (2 x last index). */
  shuffle_words(base: number, n: number): number {
    let d0 = 0;
    for (let k = n - 1; k >= 0; k--) {
      const idx = ((this.rand() >>> 16) * n) >> 16;
      d0 = w16(2 * idx);
      const a = base + 2 * idx, b = base + 2 * k;
      const va = this.mem.u16(a), vb = this.mem.u16(b);
      this.mem.set16(a, vb);
      this.mem.set16(b, va);
    }
    return d0;
  }

  // ---- world_sim_step ------------------------------------------------------------
  /** world_warmup ($18474): 601 steps in fast-build mode. */
  warmup_run(): void {
    this.warmup = true;
    for (let n = 0; n < 601; n++) this.step();
    this.warmup = false;
  }

  step(): void {
    const m = this.mem;
    const i = (this.island_index + 1) & 63;
    if (i === 0) {
      for (let k = 0; k < SLOTS; k++) {
        const r = this.rand() & 31;
        const a = SLOT_ORDER + 4 * k, b = SLOT_ORDER + 4 * r;
        const va = m.u32(a), vb = m.u32(b);
        m.set32(a, vb);
        m.set32(b, va);
      }
    }
    this.island_index = i;
    const isl = this.island(i), eco = this.economy(i);
    m.set32(G_ISLAND_PTR, isl.a);
    m.set32(G_ECONOMY_PTR, eco.a);
    m.set32(G_BUFFER_PTR, OBJ_BUFFERS[isl.buffer ? 1 : 0]);
    if (this.warmup) {
      eco.raw_stock = 500;
      eco.goods = eco.goods_cap >> 2;
    }
    if (this.owner(i) === 2 && this.route(63, i) >= 0) {
      // enemy, connected to NEMESIS
      eco.goods = 500;
    }
    this.census();
    this.structures_step();
    this.plan_construction();
  }

  /** $1851A: quota column. 0 neutral; else type (7 for type 0), +3 if large. */
  compute_class(): void {
    const i = this.island_index;
    let c = this.economy(i).kind;
    if (c === 0) c = 7;
    else if (this.island(i).area >= 2000) c += 3;
    this.island_class = this.owner(i) === 0 ? 0 : c;
  }

  /** $1854E: mark each slot active while its type still has quota for this class. */
  census(): void {
    const m = this.mem;
    this.building = this.stalled = this.empty = this.active_buildings = 0;
    m.buf.set(m.buf.slice(QUOTA_ROM, QUOTA_ROM + 64 * QUOTA_ROW), QUOTA_WORK);
    this.compute_class();
    const isl = this.island(this.island_index), eco = this.economy(this.island_index), c = this.island_class;
    eco.lines = 0;
    eco.mines = 0;
    eco.has_command_centre = 0;
    for (const s of isl.slots) {
      const t = s.type;
      if (t === 0) {
        this.empty += 1;
        continue;
      }
      if (t === 0x2c || t === 0x2d) eco.lines += 1;
      if (t === 0x2a || t === 0x2e || t === 0x2b) eco.mines += 1;
      if (t === 0x34 || t === 0x29) eco.has_command_centre = 0xff;
      let row = QUOTA_WORK + QUOTA_ROW * t;
      if (t >= T_CONSTRUCTION_FIRST) row -= QUOTA_ROW * UNDER_CONSTRUCTION;
      let quota = m.s8(row + 1 + c);
      if (quota < 0) {
        row = QUOTA_FALLBACK;
        quota = m.s8(row + 1 + c);
      }
      if (m.u8(row) === 0) continue;
      if (quota !== 0) {
        m.set8(row + 1 + c, quota - 1);
        s.set_active(true);
        if (t >= T_CONSTRUCTION_FIRST) this.building += 1;
        else if (t >= T_BUILDING_FIRST) this.active_buildings += 1;
      } else {
        s.set_active(false);
        if (t >= T_CONSTRUCTION_FIRST) this.stalled += 1;
      }
    }
  }

  /** $18662: run each slot's type handler, in the shuffled slot order. */
  structures_step(): void {
    const m = this.mem;
    const isl = this.island(this.island_index);
    for (let k = 0; k < SLOTS; k++) {
      const s = new Slot(m, isl.a + SLOT_BASE + m.u16(SLOT_ORDER + 4 * k + 2));
      const t = s.type;
      if (t >= TYPES) throw new NotPorted(`slot type $${hex(t, 2)}`);
      const handler = m.u32(HANDLERS + 8 * t);
      const max_health = m.u16(HANDLERS + 8 * t + 4), target = m.u16(HANDLERS + 8 * t + 6);
      if (handler === H_TREE) this.tree(s);
      else if (handler === H_BUILDING) this.building_step(s, max_health, target);
      else if (handler === H_COMMAND_CENTRE) this.repair(s, max_health);
      else if (handler === H_CONSTRUCTION) this.construction(s, max_health, target);
      else if (handler === H_COMMAND_CENTRE_CONSTRUCTION) this.command_centre_construction(s, max_health, target);
      else if (handler) throw new NotPorted(`handler $${hex(handler, 5)} for type $${hex(t, 2)}`);
    }
  }

  /** $186B2 / $186CA: take n from a stock if there is enough (signed compare). */
  pay(field_addr: number, n: number): boolean {
    const v = this.mem.s16(field_addr);
    if (n > v) return false;
    this.mem.set16(field_addr, v - n);
    return true;
  }

  /** $18874: once the island has 6 active buildings, idle trees get cleared. */
  tree(s: Slot): void {
    if (this.active_buildings >= 6 && !s.active && (this.rand() & 3) === 0) {
      s.type = 0;
      this.slot_mirror(s);
    }
  }

  /** $1876E: over quota -> back to construction at full progress; else repair. */
  building_step(s: Slot, max_health: number, target: number): void {
    if (!s.active) {
      s.type = s.type + UNDER_CONSTRUCTION;
      s.value = target;
      this.slot_mirror(s);
    } else {
      this.repair(s, max_health);
    }
  }

  /** $18784: health +100 per step up to max_health. */
  repair(s: Slot, max_health: number): void {
    if (s.value >= s16(max_health)) return;
    s.value = Math.min(s16(s.value + 100), s16(max_health));
    this.slot_mirror(s);
  }

  /** $186F6: active -> +1 progress per 30 goods; idle -> decays, refunding 20 goods. */
  construction(s: Slot, max_health: number, target: number): void {
    const eco = this.economy(this.island_index);
    if (!s.active) {
      const v = s.value;
      if (v === 0) {
        if (s.type !== T_RUNWAY_CONSTRUCTION) s.type = 0;
        this.slot_mirror(s);
        return;
      }
      if (this.island_class === 0 && (this.rand() & 3) !== 0) return;
      s.value = v - 1;
      eco.goods = Math.min(s16(eco.goods + 20), s16(eco.goods_cap));
      this.slot_mirror(s);
      return;
    }
    if (this.warmup || s.value === s16(target)) {
      s.type = s.type - UNDER_CONSTRUCTION;
      s.value = max_health;
      this.slot_mirror(s);
      return;
    }
    if (this.pay(eco.a + 8, 30)) {
      s.value = Math.min(s16(s.value + 1), s16(target));
      this.slot_mirror(s);
    }
  }

  /** $187A0: neutral island -> +4 per 100 raw, completing flips ownership; owned -> -8. */
  command_centre_construction(s: Slot, max_health: number, target: number): void {
    let v = s.value;
    if (this.island_class !== 0) {
      v = s16(v - 8);
      if (v > 0) s.value = v;
      else s.type = 0;
      this.slot_mirror(s);
      return;
    }
    const i = this.island_index;
    const eco = this.economy(i);
    if (v !== s16(target)) {
      if (this.pay(eco.a + 2, 100)) {
        s.value = Math.min(s16(v + 4), s16(target));
        this.slot_mirror(s);
      }
      return;
    }
    s.type = T_COMMAND_CENTRE;
    s.value = max_health;
    const code = s.word6;
    eco.kind = code & 3;
    eco.goods = 5000;
    const owner = (code >> 4) + 1;
    this.mem.set8(OWNERS + i, owner);
    this.slot_mirror(s);
    if (!this.warmup) this.events.push(["captured", i, owner & 0xff]);
    this.compute_class();
    this.network_invalidate();
  }

  /** $18898: with fewer than 2 active constructions, start the highest-priority
   * building type ($25-$2E) that still has quota, at a free spot. */
  plan_construction(): void {
    if (this.building >= 2) return;
    const m = this.mem;
    let best = 0, best_type = 0;
    for (let t = T_BUILDING_FIRST + 1; t < T_BUILDING_LAST + 1; t++) {
      const row = QUOTA_WORK + QUOTA_ROW * t;
      const pri = m.s8(row);
      if (pri > best && m.u8(row + 1 + this.island_class)) {
        best = pri;
        best_type = t;
      }
    }
    if (!best) return;
    const spot = this.find_free_spot(best_type);
    if (spot === null) return;
    const [j, x, z] = spot;
    const s = this.island(this.island_index).slots[j];
    s.type = best_type + UNDER_CONSTRUCTION;
    s.x = x;
    s.z = z;
    s.value = 0;
    this.slot_create(j);
  }

  /** find_free_island_spot ($18A48): up to 16 random positions; a spot is rejected if it
   * is within clearance of a non-tree structure. Trees in the way are cut down. Returns
   * (slot, x, z): the last empty slot, else the last tree slot clear of the spot. */
  find_free_spot(new_type: number, island: number | null = null): [number, number, number] | null {
    const m = this.mem;
    const isl = this.island(island === null ? this.island_index : island);
    const r_new = m.u16(RADIUS + 2 * new_type);
    for (let n_try = 0; n_try < 16; n_try++) {
      const r = this.rand();
      const x = w16(((s16(r) * s16(isl.half_x - 1000 - r_new)) >> 16) * 2);
      const z = w16(((s16(r >>> 16) * s16(isl.half_z - 1000 - r_new)) >> 16) * 2);
      let empty: number | null = null, free_tree: number | null = null;
      const cut: number[] = [];
      let broke = false;
      for (let j = 0; j < isl.slots.length; j++) {
        const s = isl.slots[j];
        if (s.type === 0) {
          empty = j;
          continue;
        }
        const rad = m.u16(RADIUS + 2 * s.type);
        if (rad === 0) continue;
        const dist = Math.max(Math.abs(s16(s.x - x)), Math.abs(s16(s.z - z)));
        const is_tree = T_TREE_FIRST <= s.type && s.type <= T_TREE_LAST;
        if (s16(rad + r_new) > s16(dist)) {
          if (!is_tree) {
            broke = true;
            break;
          }
          cut.push(j);
        } else if (is_tree) {
          free_tree = j;
        }
      }
      if (!broke) {
        const j = empty !== null ? empty : free_tree;
        if (j === null) return null;
        for (let n = 0; n < cut.length; n++) {
          const k = cut[n];
          m.set16(SPOT_CUT_LIST + 2 * n, k);
          isl.slots[k].type = 0;
          this.tree_cut(isl, k);
        }
        return [j, x, z];
      }
    }
    return null;
  }

  /** $216C6: refilter links by ownership, reset the route matrix (all -1, zero
   * diagonal) and flag it for relaxation by game_main ($2173A, not modelled). */
  network_invalidate(): void {
    const m = this.mem;
    const owners: number[] = [];
    for (let i = 0; i < ISLANDS; i++) owners.push(this.owner(i));
    network_filter_links(m, owners);
    m.buf.fill(0xff, ROUTES, ROUTES + ISLANDS * 128);
    for (let i = 0; i < ISLANDS; i++) m.set16(ROUTES + 130 * i, 0);
    m.set8(ROUTES_PENDING, 0xff);
    m.set8(0x1234, 0xff);
    m.set16(0xdf4, 7);
    m.set32(0xdec, ROUTES);
    m.set32(0xdf0, NETWORK);
  }

  // ---- logistics tick ($F7A0) ----------------------------------------------------
  /** $F7A0, once per second from game_main. d0 is threaded through the 4-second
   * chain because distribute_raw starts its stock array from the value
   * run_production leaves in that register. */
  logistics_tick(): void {
    const m = this.mem;
    m.set8(0x8fd, 0);
    m.set32(TICK_COUNT, m.u32(TICK_COUNT) + 1);
    m.set8(0xd3f, 0xff);
    this.island_range_check(); // $1B8AE: unit layer (stunits)
    this.count_islands();
    if (this.countdown(TIMER_REPAIR, 10)) this.carrier_repair();
    if (this.countdown(TIMER_MANTA, 10)) this.manta_hangar_step(); // $1761C: unit layer
    if (this.countdown(TIMER_WALRUS, 10)) this.walrus_hangar_step(); // $176AC: unit layer
    if (this.countdown(TIMER_984, 1)) m.set16(0x962, 2);
    m.set16(0x966, 2);
    this.move_raw();
    this.move_goods();
    this.check_carrier_pool();
    if (this.countdown(TIMER_LOGISTICS, 4)) {
      this.produce_raw();
      let d0 = this.totals();
      d0 = this.plan_production(d0);
      d0 = this.run_production(d0);
      this.distribute_raw(d0);
    }
  }

  // Unit-layer hooks: no-ops here, implemented by stunits.UnitState.
  /** $1891A: mirror a changed slot into the 3D buffer of a loaded island. */
  slot_mirror(_s: Slot): void {}

  /** $182A4: create the 3D entry for a new construction on a loaded island. */
  slot_create(_j: number): void {}

  /** $1C6D6 path of find_free_island_spot: debris for a cut tree on a loaded island. */
  tree_cut(_isl: Island, _k: number): void {}

  /** $1B8AE (in the tick). */
  island_range_check(): void {}

  /** $1761C (every 10 s). */
  manta_hangar_step(): void {}

  /** $176AC (every 10 s). */
  walrus_hangar_step(): void {}

  countdown(a: number, reload: number): boolean {
    const v = w16(this.mem.u16(a) - 1);
    this.mem.set16(a, v === 0 ? reload : v);
    return v === 0;
  }

  /** $CDF2: owned-island counts for the win/lose check; player islands by kind.
   * The kind-0 counter at $D58 is never cleared, so it keeps growing. */
  count_islands(): void {
    const m = this.mem;
    for (const a of [COUNT_PLAYER, COUNT_ENEMY, 0xd5a, 0xd5c, 0xd5e]) m.set16(a, 0);
    for (let i = 0; i < ISLANDS; i++) {
      const o = this.owner(i);
      if (o === 0) continue;
      if (o === 1) {
        m.set16(COUNT_PLAYER, m.u16(COUNT_PLAYER) + 1);
        const a = COUNT_BY_KIND + 2 * this.economy(i).kind;
        m.set16(a, m.u16(a) + 1);
      } else {
        m.set16(COUNT_ENEMY, m.u16(COUNT_ENEMY) + 1);
      }
    }
  }

  /** $174E4: share the repair rate between damaged systems of the most urgent class. */
  carrier_repair(): void {
    const m = this.mem;
    const counts = [0, 0, 0];
    for (let k = 0; k < N_SYSTEMS; k++) {
      const a = SYSTEMS + 8 * k;
      if (m.u16(a) === m.u16(a + 2)) continue;
      const c = m.u16(a + 4);
      counts[c < 2 ? c : 2] += 1;
    }
    let cls = 2;
    for (let c = 0; c < 2; c++) {
      if (counts[c]) {
        cls = c;
        break;
      }
    }
    const n = counts[cls];
    m.set16(REPAIR_CLASS, cls);
    if (n === 0) {
      if (m.u16(REPAIR_COUNT) === 0) return;
      m.set16(REPAIR_COUNT, 0);
    } else {
      m.set16(REPAIR_COUNT, n);
      const step = w16(pyDiv((150 * m.u16(REPAIR_RATE)) >> 8, n));
      m.set16(REPAIR_STEP, step);
      for (let k = 0; k < N_SYSTEMS; k++) {
        const a = SYSTEMS + 8 * k;
        let cur = m.u16(a);
        const mx = m.u16(a + 2);
        if (m.u16(a + 4) === cls && mx !== cur) {
          cur = w16(cur + step);
          m.set16(a, cur > mx ? mx : cur);
        }
      }
    }
    if (m.u16(0x718) === 1) m.set16(0x952, 2);
  }

  /** route_next_hop ($21A30): the enabled neighbour of `cur` strictly closer to `dest`
   * that minimises weight + distance (unsigned compares). Returns (neighbour word, weight)
   * or None. */
  next_hop(cur: number, dest: number): [number, number] | null {
    const m = this.mem;
    // Row and column are word-indexed (d0.w = 2 * dest, signed): an invalid stockpile
    // ($D4A = $FFxx after `st.b`, see enemy_assault) reads 512 bytes before the row.
    const col = w16(2 * dest);
    const d1 = m.u16(ROUTES + 128 * cur + s16(col));
    if (d1 & 0x8000) return null;
    const net = NETWORK + NETWORK_SIZE * cur;
    let best: number | null = null, best_w = 0xffff, best_cost = 0xffff;
    const cnt = m.u16(net);
    for (let k = 0; k < cnt; k++) {
      const weight = m.u16(net + 2 + 4 * k), nb = m.u16(net + 4 + 4 * k);
      if (nb & 0x8000) continue;
      let d = m.u16(ROUTES + s16(w16(w16(nb << 7) + col)));
      if (d >= d1) continue;
      d = w16(d + weight);
      if (d >= best_cost) continue;
      best = nb;
      best_w = weight;
      best_cost = d;
    }
    return best === null ? null : [best, best_w];
  }

  /** $21976: advance raw-material shipments (pool $66BC0) one second. */
  move_raw(): void {
    const m = this.mem;
    for (let k = 0; k < RAW_POOL_N; k++) {
      const e = RAW_POOL + POOL_ENTRY * k;
      if (m.u8(e) & 0x80) continue;
      m.set8(e + 1, m.u8(e + 1) - 1);
      if (m.u8(e + 1)) continue;
      const at = m.u8(e);
      if (this.owner(at) === 0) {
        m.set8(e, 0xff);
      } else if (at === m.u16(e + 4)) {
        const eco = this.economy(at);
        const v = w16(m.u16(e + 2) + eco.raw_stock);
        if (v > eco.raw_cap) {
          m.set8(e + 1, 1);
        } else {
          eco.raw_stock = v;
          m.set8(e, 0xff);
        }
      } else if (m.u8(ROUTES_PENDING)) {
        m.set8(e + 1, 1);
      } else {
        const hop = this.next_hop(at, m.u16(e + 4));
        if (hop === null) {
          // Delivered where it is. On overflow the original stores the
          // shipment's destination index, not the capacity (a bug).
          const eco = this.economy(at);
          const v = w16(m.u16(e + 2) + eco.raw_stock);
          eco.raw_stock = v > eco.raw_cap ? m.u16(e + 4) : v;
          m.set8(e, 0xff);
        } else {
          m.set8(e, hop[0]);
          m.set8(e + 1, hop[1]);
        }
      }
    }
  }

  /** $21C28: advance goods shipments (pool $66710, including the carrier pool). */
  move_goods(): void {
    const m = this.mem;
    for (let k = 0; k < GOODS_POOL_N; k++) {
      const e = GOODS_POOL + POOL_ENTRY * k;
      if (m.u8(e) & 0x80) continue;
      m.set8(e + 1, m.u8(e + 1) - 1);
      if (m.u8(e + 1)) continue;
      const at = m.u8(e);
      if (this.owner(at) === 0) {
        m.set8(e, 0xff);
        continue;
      }
      let dest = m.u16(e + 4);
      if (dest & 0x8000) dest = m.u16(STOCKPILE);
      if (dest === at) {
        m.set8(e, 0xff);
        const kind = m.u8(e + 2), qty = m.u8(e + 3);
        if (!(m.u16(e + 4) & 0x8000)) {
          const eco = this.economy(at);
          const v = w16(qty + eco.goods);
          eco.goods = v > eco.goods_cap ? eco.goods_cap : v;
        } else if (kind < 15) {
          const a = STORES + 2 * kind;
          const v = w16(m.u16(a) + qty);
          m.set16(a, v > STORE_MAX ? STORE_MAX : v);
        } else if (kind !== 18) {
          const a = FUEL + 4 * (kind - 15);
          const v = u32(m.u32(a) + qty * 0x1000);
          m.set32(a, v > FUEL_MAX ? FUEL_MAX : v);
        }
      } else if (m.u8(ROUTES_PENDING)) {
        m.set8(e + 1, 1);
      } else {
        const hop = this.next_hop(at, dest);
        if (hop === null) {
          m.set8(e, 0xff); // no route: the shipment is lost
        } else {
          m.set8(e, hop[0]);
          m.set8(e + 1, hop[1]);
        }
      }
    }
  }

  /** $21E8C: clear "Stocks still in transit" once the carrier pool is empty. */
  check_carrier_pool(): void {
    const m = this.mem;
    if (!m.u8(IN_TRANSIT)) return;
    for (let k = 0; k < CARRIER_POOL_N; k++) {
      if (!(m.u8(CARRIER_POOL + POOL_ENTRY * k) & 0x80)) return;
    }
    m.set8(IN_TRANSIT, 0);
  }

  /** $21906: owned islands mine raw material; neutral islands with a command-centre
   * builder accumulate capacity/16 (which pays for the builder). */
  produce_raw(): void {
    for (let i = 0; i < ISLANDS; i++) {
      const eco = this.economy(i);
      let add: number;
      if (this.owner(i)) {
        const kind = eco.kind & 3;
        if (kind === 3) continue;
        add = [100, eco.mines * 100, eco.lines * 25][kind];
      } else if (eco.has_command_centre) {
        add = eco.raw_cap >> 4;
      } else {
        continue;
      }
      const v = w16(eco.raw_stock + add);
      eco.raw_stock = v > eco.raw_cap ? eco.raw_cap : v;
    }
  }

  /** (island, line address) for production lines on player bases and factories. */
  *player_lines(): Generator<[number, number]> {
    for (let i = 0; i < ISLANDS; i++) {
      if (this.owner(i) !== 1) continue;
      const eco = this.economy(i);
      const kind = eco.kind & 3;
      if (kind !== 0 && kind !== 2) continue;
      for (let k = 0; k < eco.lines; k++) yield [i, eco.line(k)];
    }
  }

  /** $22020: per-product quantities in stores, pending, in transit and in production;
   * per-island goods including deliveries on the way. Returns d0. */
  totals(): number {
    const m = this.mem;
    m.buf.fill(0, TOTALS, TOTALS + 30);
    m.buf.fill(0, ISLAND_GOODS_TOTAL, ISLAND_GOODS_TOTAL + 128);
    m.buf.fill(0, FUEL_TOTALS, FUEL_TOTALS + 12);

    const add = (kind: number, qty: number, dest: number): void => {
      if (kind < 15) {
        const a = TOTALS + 2 * kind;
        m.set16(a, m.u16(a) + qty);
      } else if (kind === 18) {
        const a = ISLAND_GOODS_TOTAL + s16(2 * dest);
        m.set16(a, m.u16(a) + qty);
      } else {
        const a = FUEL_TOTALS + 4 * (kind - 15);
        m.set32(a, m.u32(a) + qty * 0x1000);
      }
    };

    for (let k = 0; k < GOODS_POOL_N; k++) {
      const e = GOODS_POOL + POOL_ENTRY * k;
      if (!(m.u8(e) & 0x80)) add(m.u8(e + 2), m.u8(e + 3), m.u16(e + 4));
    }
    for (const [, ln] of this.player_lines()) {
      if (!(m.u16(ln) & 0x8000)) add(m.u16(ln + 2), m.u16(ln + 8), m.u16(ln + 6));
    }
    for (let k = 0; k < N_ITEMS; k++) {
      const a = TOTALS + 2 * k;
      m.set16(a, m.u16(a) + m.u16(STORES_PENDING + 2 * k) + m.u16(STORES + 2 * k));
    }
    for (let j = 0; j < 3; j++) {
      const a = FUEL_TOTALS + 4 * j;
      m.set32(a, m.u32(a) + m.u32(FUEL + 4 * j) + m.u32(FUEL_PENDING + 4 * j));
    }
    for (let i = 0; i < ISLANDS; i++) {
      const a = ISLAND_GOODS_TOTAL + 2 * i;
      m.set16(a, m.u16(a) + this.economy(i).goods);
    }
    return this.economy(63).goods;
  }

  /** Planning class of an island's own supply: 1 if its goods (with deliveries) are
   * below capacity/4, else 0. (The capacity/2 case tests the economy word +0 == 3,
   * which needs kind 0 with byte +1 = 3, so it never applies in practice.) */
  island_needs(i: number): number {
    const eco = this.economy(i);
    let threshold = eco.goods_cap >> 1;
    if (this.mem.u16(eco.a) !== 3) threshold >>= 1;
    return threshold > this.mem.u16(ISLAND_GOODS_TOTAL + 2 * i) ? 1 : 0;
  }

  /** $22180: choose the most urgent class of need and hand its products, shuffled, to
   * idle production lines. Returns d0. */
  plan_production(d0: number): number {
    const m = this.mem;
    // Bits up to 31 only: an int32 mask is exact for every use below (& 0xFF, w16, & 4, & 2).
    let mask = 0;
    for (let k = 0; k < N_ITEMS; k++) {
      const item = ITEMS + ITEM_SIZE * k;
      if (m.u16(TOTALS + 2 * k) < m.u16(item + 0x12)) mask |= 1 << (m.u16(item + 0x10) & 31);
    }
    for (let j = 0; j < 3; j++) {
      if (m.u32(FUEL_TOTALS + 4 * j) < FUEL_MAX) mask |= 1 << (m.u16(FUEL_CLASS + 2 * j) & 31);
    }
    for (let i = 0; i < ISLANDS; i++) {
      if (this.owner(i) === 1 && this.route(0, i) >= 0) mask |= 1 << this.island_needs(i);
    }
    if (!(mask & 0xff)) return w16(mask);
    const cls = mask & 4 ? 2 : mask & 2 ? 1 : 0;
    m.set16(PLAN_CLASS, cls);
    const cands: number[] = [];
    for (let k = 0; k < N_ITEMS; k++) {
      const item = ITEMS + ITEM_SIZE * k;
      if (m.u16(item + 0x10) === cls && m.u16(TOTALS + 2 * k) < m.u16(item + 0x12)) cands.push(k);
    }
    for (let j = 0; j < 3; j++) {
      if (m.u16(FUEL_CLASS + 2 * j) === cls && m.u32(FUEL_TOTALS + 4 * j) < FUEL_MAX) cands.push(15 + j);
    }
    for (let i = 0; i < ISLANDS; i++) {
      if (this.owner(i) === 1 && this.route(0, i) >= 0 && this.island_needs(i) === cls) cands.push(0x40 + i);
    }
    if (cands.length === 0) throw new NotPorted("plan_production with no candidates (needs a class >= 8)");
    m.set16(PLAN_COUNT, cands.length);
    for (let n = 0; n < cands.length; n++) m.set16(PLAN_LIST + 2 * n, cands[n]);
    d0 = this.shuffle_words(PLAN_LIST, cands.length);
    let left = cands.length;
    let n = 0;
    // The filter is evaluated lazily, line by line, as in the Python generator.
    for (const x of this.player_lines()) {
      if (!(m.u16(x[1]) & 0x8000)) continue;
      const ln = x[1];
      m.set16(ln, 0);
      const c = m.u16(PLAN_LIST + 2 * n);
      if (c < 0x40) {
        m.set16(ln + 2, c);
        m.set16(ln + 6, 0xffff);
        d0 = c;
      } else {
        m.set16(ln + 2, 18);
        d0 = c - 0x40;
        m.set16(ln + 6, d0);
      }
      left -= 1;
      if (left === 0) break;
      n += 1;
    }
    return d0;
  }

  /** $21EE8: production lines on player bases and factories. 0 = load the product's
   * steps/qty/cost, 1 = pay cost raw per step, 2 = ship the product, then idle (-1).
   * Returns d0 (see logistics_tick). */
  run_production(d0: number): number {
    const m = this.mem;
    for (let i = 0; i < ISLANDS; i++) {
      if (this.owner(i) !== 1) continue;
      const eco = this.economy(i);
      const kind = eco.kind & 3;
      d0 = (d0 & 0xff00) | (kind ? (kind - 2) & 0xff : 0); // andi.b #3 / subq.b #2
      if (kind !== 0 && kind !== 2) continue;
      for (let k = 0; k < eco.lines; k++) {
        const ln = eco.line(k);
        const state = m.s16(ln);
        d0 = w16(state);
        if (state < 0) continue;
        if (state === 0) {
          const p = m.u16(ln + 2);
          d0 = w16(6 * p);
          const t = PRODUCTS + d0;
          m.set16(ln + 4, m.u16(t));
          m.set16(ln + 8, m.u16(t + 2));
          m.set16(ln + 10, m.u16(t + 4));
          m.set16(ln, 1);
        } else if (state === 1) {
          d0 = w16(eco.raw_stock - m.u16(ln + 10));
          if (d0 & 0x8000) continue;
          eco.raw_stock = d0;
          m.set16(ln + 4, m.u16(ln + 4) - 1);
          if (m.u16(ln + 4) === 0) m.set16(ln, 2);
        } else {
          const slot = this.free_goods_slot();
          if (slot === null) {
            d0 = 0xffff;
            continue;
          }
          m.set16(slot + 4, m.u16(ln + 6));
          m.set8(slot + 2, m.u16(ln + 2));
          m.set8(slot + 3, m.u16(ln + 8));
          d0 = 0x3f - (63 - i);
          m.set8(slot, i);
          m.set8(slot + 1, 1);
          m.set16(ln, 0xffff);
        }
      }
    }
    return d0;
  }

  /** $21D2C: first free entry among the first 128 of the goods pool. */
  free_goods_slot(): number | null {
    for (let k = 0; k < FREE_GOODS_SEARCH; k++) {
      const e = GOODS_POOL + POOL_ENTRY * k;
      if (this.mem.u8(e) & 0x80) return e;
    }
    return null;
  }

  /** $21AA0: resource islands ship 200 raw to bases and factories connected to VULCAN,
   * preferring ones at or below capacity/4, weighted by production lines. The stock
   * array starts from the leftover d0 (an original quirk). */
  distribute_raw(d0: number): void {
    const m = this.mem;
    for (let i = 0; i < ISLANDS; i++) m.set16(RAW_TOTAL + 2 * i, d0);
    for (let k = 0; k < RAW_POOL_N; k++) {
      const e = RAW_POOL + POOL_ENTRY * k;
      if (!(m.u8(e) & 0x80)) {
        const a = RAW_TOTAL + s16(2 * m.u16(e + 4));
        m.set16(a, m.u16(a) + m.u16(e + 2));
      }
    }
    for (let i = 0; i < ISLANDS; i++) {
      const a = RAW_TOTAL + 2 * i;
      m.set16(a, m.u16(a) + this.economy(i).raw_stock);
    }

    const eligible = (i: number): boolean => {
      const eco = this.economy(i);
      return this.owner(i) === 1 && this.route(0, i) >= 0 && (eco.kind === 0 || eco.kind === 2);
    };

    const needy = (i: number): boolean => (this.economy(i).raw_cap >> 2) >= m.u16(RAW_TOTAL + 2 * i);

    let any_needy = false;
    for (let i = 0; i < ISLANDS; i++) {
      if (eligible(i) && needy(i)) {
        any_needy = true;
        break;
      }
    }
    let n = 0;
    for (let i = 0; i < ISLANDS; i++) {
      if (eligible(i) && (needy(i) || !any_needy)) {
        const lines = this.economy(i).lines;
        for (let q = 0; q < lines; q++) {
          m.set16(SHIP_LIST + 2 * n, i);
          n += 1;
        }
      }
    }
    if (n === 0) return;
    this.shuffle_words(SHIP_LIST, n);
    let left = n;
    for (let i = 0; i < ISLANDS; i++) {
      const eco = this.economy(i);
      if (this.owner(i) !== 1 || eco.kind !== 1 || eco.raw_stock < 200) continue;
      eco.raw_stock -= 200;
      let slot: number | null = null;
      for (let k = 0; k < RAW_POOL_N; k++) {
        if (m.u8(RAW_POOL + POOL_ENTRY * k) & 0x80) {
          slot = RAW_POOL + POOL_ENTRY * k;
          break;
        }
      }
      if (slot === null) return; // the 200 already taken are lost
      m.set16(slot + 4, m.u16(SHIP_LIST + 2 * (n - left)));
      m.set16(slot + 2, 200);
      m.set8(slot, i);
      m.set8(slot + 1, 1);
      left -= 1;
      if (left === 0) return;
    }
  }

  // ---- per frame (game_main) ----------------------------------------------------
  /** The strategic part of one game_main pass ($40D8): route relaxation while pending,
   * the logistics tick when a second has passed, an island step, the enemy AI. How many
   * passes make a second depends on the renderer (about 8 in the 3D view). */
  frame(new_second: boolean): void {
    if (this.mem.u8(ROUTES_PENDING)) this.relax_routes();
    if (new_second) {
      this.mem.set8(0x8fd, 0xff);
      this.logistics_tick();
    }
    this.step();
    this.enemy_ai();
  }

  /** $2173A: one slice (8 rows) of the Bellman-Ford pass over the route matrix. After 8
   * slices, a pass that changed nothing clears the pending flag; otherwise it restarts. */
  relax_routes(): void {
    const m = this.mem;
    let row = m.u32(0xdec), net = m.u32(0xdf0);
    for (let r = 0; r < 8; r++) {
      const cnt = m.u16(net);
      for (let k = 0; k < cnt; k++) {
        const w = m.u16(net + 2 + 4 * k), nb = m.u16(net + 4 + 4 * k);
        if (nb & 0x8000) continue;
        const src = ROUTES + s16(w16(nb << 7));
        for (let j = 0; j < 64; j++) {
          const d1 = m.u16(row + 2 * j);
          let d2 = m.u16(src + 2 * j);
          if (d2 & 0x8000) continue;
          d2 = w16(d2 + w);
          if (d1 > d2) {
            m.set16(row + 2 * j, d2);
            m.set8(0x1234, 0);
          }
        }
      }
      row += 128;
      net += NETWORK_SIZE;
    }
    let n = w16(m.u16(0xdf4) - 1);
    if (n === 0xffff) {
      if (m.u8(0x1234)) {
        m.set8(ROUTES_PENDING, 0);
        return;
      }
      n = 7;
      row = ROUTES;
      net = NETWORK;
      m.set8(0x1234, 0xff);
    }
    m.set16(0xdf4, n);
    m.set32(0xdec, row);
    m.set32(0xdf0, net);
  }

  // ---- enemy strategy ($1AFAA) ----------------------------------------------------
  /** enemy_ai_step: the enemy carrier's state machine, every frame while [$464] is set.
   * States 10/11 (close-range combat with a live carrier) belong to the unit layer. */
  enemy_ai(): void {
    const m = this.mem;
    if (!m.u8(AI_ENABLED)) return;
    const state = m.u16(E_STATE);
    if (state === 0) {
      m.buf[ENEMY_CARRIER + 1] &= 0xfe;
      m.set16(E_STATE, 1);
      m.set16(E_STRENGTH, 12000);
      m.set16(0x11c8, 0x7fff);
      m.set16(E_AIRCRAFT, 8);
      m.set16(0x11ce, 0);
    } else if (state === 1) {
      this.enemy_choose_route();
    } else if (state === 2) {
      if (this.player_near_enemy()) {
        this.enemy_engage();
        return;
      }
      m.set16(E_STATE, 1);
      m.set16(E_STRENGTH, 12000);
      if (this.route(NEMESIS, m.u16(E_ISLAND)) >= 0) {
        m.set16(E_AIRCRAFT, 8);
        m.set16(E_RESERVE, 12);
      }
    } else if (state === 3) {
      this.enemy_assault();
    } else if (state === 4) {
      if (this.player_near_enemy()) {
        this.enemy_engage();
        return;
      }
      if (this.countdown16(E_TIMER)) m.set16(E_STATE, 1);
    } else if (state === 5) {
      this.enemy_travel();
    } else if (state === 10 || state === 11) {
      this.enemy_tactical(state);
    } else if (state > 11) {
      throw new NotPorted(`enemy state ${state}`);
    }
  }

  countdown16(a: number): boolean {
    const v = w16(this.mem.u16(a) - 1);
    this.mem.set16(a, v);
    return v === 0;
  }

  /** State 1: once routes are settled, pick a target; travel time = distance / 500. */
  enemy_choose_route(): void {
    const m = this.mem;
    if (m.u8(ROUTES_PENDING)) return;
    this.enemy_choose_target();
    const isl = ISLAND_TABLE + ISLAND_SIZE * m.u16(E_TARGET);
    const d = approx_distance(m.u32(E_POS_X), m.u32(E_POS_Z), m.u32(isl + 2), m.u32(isl + 6));
    const q = pyDiv(d, 500);
    m.set16(E_TIMER, q > 0xffff ? 0xffff : q);
    m.set16(E_STATE, 5);
  }

  /** State 5: on arrival, resolve the island abstractly unless it's loaded (player there). */
  enemy_travel(): void {
    const m = this.mem;
    if (!this.countdown16(E_TIMER)) return;
    const i = m.u16(E_TARGET);
    m.set16(E_ISLAND, i);
    const isl = ISLAND_TABLE + ISLAND_SIZE * i;
    m.set32(ENEMY_CARRIER + 0x0a, m.u32(isl + 2));
    m.set32(ENEMY_CARRIER + 0x12, m.u32(isl + 6));
    if (m.u8(isl)) {
      m.set16(E_STATE, 1);
      return;
    }
    m.buf[ENEMY_CARRIER + 1] &= 0xfe;
    const o = this.owner(i);
    if (o === 0) {
      this.enemy_drop_builder(i);
    } else if (o === 1) {
      if (i !== NEMESIS && m.s16(E_STRENGTH) < 3000) {
        m.set16(E_STATE, 1);
        return;
      }
      m.set16(E_STATE, 3);
      m.set16(B_POOL, 5000);
      m.set16(B_TIMER, 400);
      this.events.push(["attacked", i]);
    } else {
      m.set16(E_STATE, 2);
    }
  }

  /** $1B1B6: place an enemy command-centre builder ($34, code $11-$13), then wait 400. */
  enemy_drop_builder(i: number): void {
    const m = this.mem;
    const isl = this.island(i), eco = this.economy(i);
    m.set32(G_ISLAND_PTR, isl.a);
    m.set32(G_ECONOMY_PTR, eco.a);
    m.set32(G_BUFFER_PTR, OBJ_BUFFERS[isl.buffer ? 1 : 0]);
    const spot = this.find_free_spot(T_COMMAND_CENTRE_CONSTRUCTION, i);
    if (spot !== null) {
      const [j, x, z] = spot;
      const s = isl.slots[j];
      s.type = T_COMMAND_CENTRE_CONSTRUCTION;
      s.x = x;
      s.z = z;
      s.value = 0;
      let code: number;
      for (;;) {
        code = this.rand() & 3;
        if (code) break;
      }
      s.word6 = code + 0x10;
    }
    m.set16(E_TIMER, 400);
    m.set16(E_STATE, 4);
  }

  /** State 3: bombard the island; when its command centre falls it turns neutral and
   * the enemy drops its own builder. Retreats when weak (except at NEMESIS). */
  enemy_assault(): void {
    const m = this.mem;
    if (this.player_near_enemy()) {
      this.enemy_engage();
      return;
    }
    const i = m.u16(E_ISLAND);
    if (this.enemy_bombard()) {
      this.events.push(["fallen", i]);
      m.set8(OWNERS + i, 0);
      this.network_invalidate();
      if (i === m.u16(STOCKPILE)) {
        m.set8(STOCKPILE, 0xff); // st.b on the word's high byte
        this.stores_clear();
      }
      this.enemy_drop_builder(i);
    } else if (i !== NEMESIS && m.s16(E_STRENGTH) <= 2400) {
      m.set16(E_STATE, 1);
      this.events.push(["repulsed", i]);
    }
  }

  /** $1B558, every 200 calls: attack vs. defence, random hits on structures, return
   * fire on the enemy. True when the island has no command centre ($29) left. */
  enemy_bombard(): boolean {
    const m = this.mem;
    if (!this.countdown16(B_TIMER)) return false;
    m.set16(B_TIMER, 200);
    const i = m.u16(E_ISLAND);
    const slots = this.island(i).slots.slice(1);
    const ac0 = m.s16(E_AIRCRAFT);
    const attack = w16((ac0 > 4 ? 4 : ac0) * 0x400 + (m.u16(E_STRENGTH) >> 1));
    m.set16(B_ATTACK, attack);
    let defence = 0, targets = 0;
    for (const s of slots) {
      const t = s.type;
      if (t === 0 || t === 7 || t === 0x24) continue;
      targets += 1;
      if (0x25 <= t && t <= 0x28) defence = w16(defence + DEFENCE_WEIGHTS[t - 0x25]);
    }
    m.set16(B_DEFENCE, defence);
    m.set16(B_TARGETS, targets);
    let d2 = attack;
    const d3 = defence;
    if (m.u16(E_RESERVE) > 4) {
      if ((this.rand() & 0xffff) < 0x4000) {
        m.set16(E_RESERVE, m.u16(E_RESERVE) - 1);
        m.set16(B_ATTACK, m.u16(B_ATTACK) + 0x400);
      }
      if (d3 > d2) {
        d2 = w16(d2 + 0x800);
        if (d3 > d2) {
          const n = (((this.rand() & 0xffff) * 8) >> 16) + 5;
          if (n <= m.u16(E_RESERVE)) {
            m.set16(E_RESERVE, m.u16(E_RESERVE) - 1);
            d2 = w16(d2 + 0x400);
          }
        }
      }
    }
    m.set16(B_ATTACK, d2);
    const hits = scaled_count(this.rand(), d2);
    const returns = scaled_count(this.rand(), d3);
    if (hits) {
      let left = targets;
      for (let h = 0; h < hits; h++) {
        if (left === 0) throw new NotPorted("bombard: no targets on the island");
        const n = ((this.rand() & 0xffff) * left) >> 16;
        const live = slots.filter((s) => !(s.type === 0 || s.type === 7 || s.type === 0x24));
        if (n >= live.length) throw new RangeError("list index out of range"); // Python IndexError
        const s = live[n];
        const dmg = s.type >= T_CONSTRUCTION_FIRST ? 4 : 0x400;
        const v = s.value & 0xffff;
        s.value = v - dmg;
        if (!(v > dmg)) {
          s.type = 0;
          left -= 1;
          if (left === 0) break;
        }
      }
    }
    for (let r = 0; r < returns; r++) {
      const weak = i !== NEMESIS && m.u16(E_STRENGTH) < 2400;
      if (!weak) {
        if ((this.rand() & 0xffff) < 0x4000) {
          const v = m.s16(E_STRENGTH) - 0x400;
          m.set16(E_STRENGTH, v);
          if (s16(v) < 0) {
            // $1B728: destroyed. The AI stops, the end timer starts and the message
            // routine $A128 shows it; it leaves d0.w = 0 (the message's terminator),
            // so the bombardment returns "not fallen" without its remaining return
            // fire or the command-centre check, and enemy_assault, seeing strength
            // <= 2400, calls the attack repulsed (state 1) unless at NEMESIS.
            m.set8(AI_ENABLED, 0);
            m.set16(GAME_END_TIMER, 100);
            this.events.push(["message", "Enemy carrier destroyed", this._ping()]);
            return false;
          }
          continue;
        }
        if (m.u16(E_AIRCRAFT) === 0) continue;
      }
      const pool = m.s16(B_POOL) - 0x400;
      m.set16(B_POOL, pool);
      if (s16(pool) >= 0) continue;
      const ac = w16(m.u16(E_AIRCRAFT) - 1);
      m.set16(E_AIRCRAFT, ac);
      if (ac) m.set16(B_POOL, 5000);
    }
    return !slots.some((s) => s.type === T_COMMAND_CENTRE);
  }

  /** $1B4FA: any active player unit (objects 2-10) within 270000 (Chebyshev). */
  player_near_enemy(): boolean {
    const m = this.mem;
    const ex = m.u32(ENEMY_CARRIER + 0x0a), ez = m.u32(ENEMY_CARRIER + 0x12);
    for (let k = 2; k < 11; k++) {
      const o = OBJECTS + OBJECT_SIZE * k;
      if (!(m.u8(o + 1) & 1)) continue;
      const dx = Math.abs(s32(m.u32(o + 0x0a) - ex)), dz = Math.abs(s32(m.u32(o + 0x12) - ez));
      if (s32(Math.max(dx, dz)) < 270000) return true;
    }
    return false;
  }

  /** $1B250: the carrier becomes a live object 2000 + [$454] beyond a random side of
   * its island; state 10. What follows is the unit layer (TacticalMode is raised). */
  enemy_engage(): void {
    const m = this.mem;
    m.set16(E_STATE, 10);
    const isl = this.island(m.u16(E_ISLAND));
    const d0 = m.u16(0x454) + 2000;
    let dx = isl.half_x + d0, dz = isl.half_z + d0;
    const r = this.rand() & 0xffff;
    const o = ENEMY_CARRIER;
    let heading: number;
    if (!(r & 0x8000)) {
      heading = 0;
      if (!(r & 0x4000)) {
        dz = -dz;
        heading = 0x8000;
      }
      m.set32(o + 0x12, m.u32(o + 0x12) + dz);
    } else {
      heading = 0x4000;
      if (!(r & 0x4000)) {
        dx = -dx;
        heading = 0xc000;
      }
      m.set32(o + 0x0a, m.u32(o + 0x0a) + dx);
    }
    m.set16(o + 6, heading);
    m.set16(o + 0x1e, heading);
    m.buf[o + 1] |= 1;
    m.set16(0x11ce, 0);
    m.set8(0x11d0, 0);
    this.enemy_engaged();
  }

  /** Hook: the enemy has just engaged (state 10). The unit layer continues; here it
   * stops the run. */
  enemy_engaged(): void {
    throw new TacticalMode(10);
  }

  /** Hook: close-range combat states 10 ($1B2D2) and 11 ($1B49E), unit layer. */
  enemy_tactical(state: number): void {
    throw new TacticalMode(state);
  }

  /** $22C20: NEMESIS first if it isn't enemy-held; when strong, the best-scoring
   * island; when weak, the nearest enemy island still connected to NEMESIS. */
  enemy_choose_target(): void {
    const m = this.mem;
    const ex = m.u32(ENEMY_CARRIER + 0x0a), ez = m.u32(ENEMY_CARRIER + 0x12);
    m.set32(E_POS_X, ex);
    m.set32(E_POS_Z, ez);
    if (this.owner(NEMESIS) !== 2) {
      m.set16(E_TARGET, NEMESIS);
      return;
    }
    if (m.s16(E_STRENGTH) < 3000 || m.u16(E_AIRCRAFT) === 0) {
      let best: number | null = null, best_d = 32_000_000;
      for (let i = 0; i < ISLANDS; i++) {
        const isl = ISLAND_TABLE + ISLAND_SIZE * i;
        if (this.owner(i) !== 2 || this.route(i, NEMESIS) < 0) continue;
        const d = approx_distance(m.u32(isl + 2), m.u32(isl + 6), ex, ez);
        if (d <= best_d) {
          best = i;
          best_d = d;
        }
      }
      if (best === null) throw new NotPorted("enemy retreat with no island connected to NEMESIS");
      m.set16(E_TARGET, best);
      return;
    }
    m.set8(ENEMY_COIN, this.rand() & 0x8000 ? 0xff : 0);
    const vul = ISLAND_TABLE + 2, nem = ISLAND_TABLE + ISLAND_SIZE * NEMESIS + 2;
    const pairs: [number, number][] = [[E_VULCAN_X, vul], [E_VULCAN_Z, vul + 4], [E_NEMESIS_X, nem], [E_NEMESIS_Z, nem + 4]];
    for (const [a, b] of pairs) m.set32(a, m.u32(b));
    for (let i = 0; i < ISLANDS; i++) {
      const isl = ISLAND_TABLE + ISLAND_SIZE * i;
      const x = m.u32(isl + 2), z = m.u32(isl + 6);
      const d = approx_distance(x, z, ex, ez);
      let score = d > FAR ? 0xc568 : shr(-d, 11);
      const dn = shr(approx_distance(x, z, m.u32(E_NEMESIS_X), m.u32(E_NEMESIS_Z)), 14);
      const o = this.owner(i);
      score += o === 2 ? dn : -dn;
      const large = m.u16(isl + 0x0e) > 2000;
      score += o === 0 ? (large ? 3000 : 2000) : o === 2 ? 0xf060 : large ? 1500 : 1000;
      if (o !== 2) {
        const net = NETWORK + NETWORK_SIZE * i;
        const n = m.u16(net);
        if (n === 0) throw new NotPorted("island without links");
        let linked = false;
        for (let k = 0; k < n; k++) {
          if (this.route(NEMESIS, m.u16(net + 4 + 4 * k) & 0x3fff) >= 0) {
            linked = true;
            break;
          }
        }
        if (linked) score += 4000;
      } else if (m.u8(ENEMY_COIN)) {
        score = 0x8002;
      }
      m.set16(ENEMY_SCORES + 2 * i, score);
    }
    if (this.owner(VULCAN) !== 2) {
      if (approx_distance(m.u32(E_VULCAN_X), m.u32(E_VULCAN_Z), ex, ez) <= FAR) {
        m.set16(ENEMY_SCORES, m.u16(ENEMY_SCORES) + 5000);
      }
    }
    m.set16(ENEMY_SCORES + 2 * m.u16(E_ISLAND), 0x8001);
    let best = 0, best_score = -0x7fff;
    for (let i = 0; i < ISLANDS; i++) {
      const sc = m.s16(ENEMY_SCORES + 2 * i);
      if (sc > best_score) {
        best = i;
        best_score = sc;
      }
    }
    m.set16(E_TARGET, best);
  }

  /** $21EAC: the stockpile is lost: empty carrier stores and fuel, and the pending
   * stores too unless [$D3E] is set. */
  stores_clear(): void {
    const m = this.mem;
    m.buf.fill(0, STORES, STORES + 30);
    m.buf.fill(0, FUEL, FUEL + 12);
    if (!m.u8(0xd3e)) {
      m.buf.fill(0, STORES_PENDING, STORES_PENDING + 30);
      m.buf.fill(0, FUEL_PENDING, FUEL_PENDING + 12);
    }
  }
}

/** The enemy entered close-range combat (states 10/11), which needs the unit layer. */
export class TacticalMode extends Error {
  /** Python's exception args: (state,). */
  args: number[];
  state: number;

  constructor(state: number) {
    super(String(state));
    this.name = "TacticalMode";
    this.state = state;
    this.args = [state];
  }
}

/** No longer thrown: the destruction is modelled (enemy_bombard). Kept for callers that
 * still list it among the stops. */
export class EnemyDestroyed extends Error {
  constructor() {
    super("");
    this.name = "EnemyDestroyed";
  }
}

/** Upper-case hex, zero-padded (Python's f"{v:0NX}"). */
function hex(v: number, width: number): string {
  return v.toString(16).toUpperCase().padStart(width, "0");
}

/** The game's distance estimate: max(|dx|, |dz|) + min/2 (32-bit, unsigned compare). */
export function approx_distance(x0: number, z0: number, x1: number, z1: number): number {
  const dx = u32(Math.abs(s32(x0 - x1))), dz = u32(Math.abs(s32(z0 - z1)));
  const [hi, lo] = dz < dx ? [dx, dz] : [dz, dx];
  return u32(hi + shr(s32(lo), 1));
}

/** $1B62A: (rand.lo * n) >> 16, sign-extended, divu #$600; the low word of the result. */
export function scaled_count(r: number, n: number): number {
  const hi = shr((r & 0xffff) * n, 16) & 0xffff;
  return divu(u32(s16(hi)), 0x600) & 0xffff;
}

/** $2184E: a link is enabled (bit 15 clear) only when both ends share a non-zero owner. */
export function network_filter_links(m: Mem, owners: number[]): void {
  for (let i = 0; i < ISLANDS; i++) {
    const base = NETWORK + NETWORK_SIZE * i;
    const cnt = m.u16(base);
    for (let k = 0; k < cnt; k++) {
      const a = base + 4 + 4 * k;
      const idx = m.u16(a) & 0x3fff;
      if (idx >= owners.length) throw new RangeError("list index out of range"); // Python IndexError
      const o = owners[idx];
      if (owners[i] !== 0 && o !== 0 && (o === 1) === (owners[i] === 1)) m.buf[a] &= 0x7f;
      else m.buf[a] |= 0x80;
    }
  }
}
