/**
 * Unit layer, part of tools/stunits.py: starting a new game: the new-game routine $42A8 (globals
 * restore, session reset, init_islands $18370 with trees and start structures, census,
 * object-store init $43D2, route init), strategy and action variants.
 *
 * Port of tools/stunits_setup.py (the verified reference; its docstring has the details).
 * new_game(strategy) takes a title-screen state (from_ram of a RAM image with the title menu up)
 * whose RNG ($1236/$123A) the caller has seeded, and leaves exactly the simulation state the
 * original has when $42A8 returns; new_game_screen() then takes the screen request it leaves
 * (the bridge), giving the state of the first simulation pass. See docs/units.md §16.
 */
import { NotPorted, shr } from "./mem.ts";
import type { Region } from "./mem.ts";
import {
  AI_ENABLED,
  ECONOMY,
  ECONOMY_SIZE,
  GOODS_POOL,
  GOODS_POOL_N,
  G_BUFFER_PTR,
  G_ECONOMY_PTR,
  G_ISLAND_PTR,
  ISLANDS,
  ISLAND_SIZE,
  ISLAND_TABLE,
  OBJECTS,
  OBJECT_SIZE,
  OBJ_BUFFERS,
  OWNERS,
  POOL_ENTRY,
  RAW_POOL,
  RAW_POOL_N,
  SLOTS,
  SLOT_ORDER,
  SLOT_SIZE,
  TICK_COUNT,
  T_COMMAND_CENTRE_CONSTRUCTION,
  T_RUNWAY_CONSTRUCTION,
  T_TREE_FIRST,
} from "./world.ts";
import {
  CARRIER,
  C_CRUISE,
  C_DEPTH,
  C_FLAGS,
  C_WAYPOINT_X,
  C_WAYPOINT_Z,
  DEBRIS,
  FULL_REDRAW,
  ISLAND_BUFFERS,
  MOUSE_DX,
  NEW_INDEX,
  POINTER_MODE,
  SCREEN,
  SHARDS,
  UNIT_INPUT_REGIONS,
  UNIT_REGIONS,
  UnitCore,
  VIEWED,
} from "./units_core.ts";

export const SNAPSHOT = 0x4bcca; // globals_snapshot $1A4C6 at boot: $400-$1235, then $40EB2
export const GLOBALS = 0x400, GLOBALS_SIZE = 0xe36;
export const FLAG_TABLE = 0x40eb2, FLAG_TABLE_SIZE = 0xd4;
export const ISLAND_RECORDS = 0x4afb6, ISLAND_RECORD = 22; // x, z, hx, hz, runway x/z/heading, 2 volcanoes
export const STRATEGY = 0x124a; // set: strategy game; clear: action game
export const START_STRATEGY = 0x18fce, START_ACTION = 0x19008; // carrier, enemy carrier; then the start list
export const NOISE = 0x5c2da, NOISE_WORDS = 0xd00; // static-noise table for the screen effect $5540
export const ISLAND_SCRATCH = 0xa80;

// State new_game reads or writes that the other regions don't cover ($758 and the map cursors
// $69C are units_controls' regions; $768-$797 hold three copies of $758-$767).
for (const _region of [
  ["strategy flag $124A", STRATEGY, 1],
  ["globals snapshot $4BCCA", SNAPSHOT, GLOBALS_SIZE + FLAG_TABLE_SIZE],
  ["island records $4AFB6", ISLAND_RECORDS, ISLANDS * ISLAND_RECORD],
  ["start lists $19078", 0x19078, 0x190ee - 0x19078], // the rest is in the quota table
  ["map views $768", 0x768, 0x798 - 0x768],
] as Region[]) {
  if (!UNIT_REGIONS.some((r) => r[0] === _region[0] && r[1] === _region[1] && r[2] === _region[2])) {
    UNIT_REGIONS.push(_region);
  }
}
for (const n of ["globals snapshot $4BCCA", "island records $4AFB6", "start lists $19078"]) UNIT_INPUT_REGIONS.add(n);

export abstract class SetupUnits extends UnitCore {
  /** $42A8 (with [$124A] set by the title menu: $103B2 strategy, $103A0 action). */
  new_game(strategy: boolean): void {
    const m = this.mem;
    m.set8(STRATEGY, strategy ? 0xff : 0);
    this.globals_restore();
    this.session_reset();
    this.game_flags_reset();
    m.set8(0x1227, 0xff); // screen request: tab 1 (carrier, screen 0)
    m.set16(0x1228, 1);
    if (!strategy) {
      // action: sail to a waypoint, harder islands
      m.set32(C_WAYPOINT_X, 0xffe66600);
      m.set32(C_WAYPOINT_Z, 0xffe77748);
      m.set16(C_CRUISE, 0x70);
      m.set8(C_FLAGS, 7);
      m.set8(0x6c6, 0xff);
    }
    m.set16(0x444, strategy ? 0 : 0x2000);
    this.difficulty_apply();
    m.set8(AI_ENABLED, 0xff);
    m.set8(POINTER_MODE, 0);
    this.frame_flip_flags(); // after the screen clear and "Creating..."
    m.set8(FULL_REDRAW, 0xff);
    this.init_islands();
    this.count_islands();
    this.noise_table_init();
    m.set8(0x8fd, 0xff);
    this.object_store_init();
    this.network_invalidate();
    this.map_views_init();
  }

  /** $1A518: the globals and the panel flag table as snapshotted at boot. */
  globals_restore(): void {
    const b = this.mem.buf;
    b.copyWithin(GLOBALS, SNAPSHOT, SNAPSHOT + GLOBALS_SIZE);
    const s = SNAPSHOT + GLOBALS_SIZE;
    b.copyWithin(FLAG_TABLE, s, s + FLAG_TABLE_SIZE);
  }

  /** $4206, the parts that write simulation state: input $17EC6/$17EAE, the tick count, the
   * pools $218D8, the debris $1C6E2, the pointer $F712 and the pointer mode. */
  session_reset(): void {
    const m = this.mem;
    m.set16(MOUSE_DX, 0); // $17EC6
    m.set16(MOUSE_DX + 2, 0);
    m.set32(0x880, 0);
    m.buf.fill(0, 0x798, 0x884); // $17EAE: key table ($880 buttons included)
    m.set8(0x888, 0);
    m.set8(0x1270, 0xff);
    m.set32(TICK_COUNT, 0);
    this.pools_init();
    this.debris_init();
    m.set8(0x459, 0);
    this.pointer_reset();
    m.set8(POINTER_MODE, 0xff);
  }

  /** $F712: no modal pointer handler, the default pointer, full-screen pointer limits. */
  pointer_reset(): void {
    const m = this.mem;
    m.set8(0x460, 0);
    m.set32(0x46e, 0x40e26);
    m.set16(0x472, 0);
    m.set16(0x478, 0);
    m.set16(0x47a, 0);
    m.set16(0x47c, 0x13f);
    m.set16(0x47e, 0xc7);
  }

  /** $218D8: every raw-material and goods/carrier pool entry free. */
  pools_init(): void {
    const m = this.mem;
    for (let k = 0; k < RAW_POOL_N; k++) m.set8(RAW_POOL + POOL_ENTRY * k, 0xff);
    for (let k = 0; k < GOODS_POOL_N; k++) m.set8(GOODS_POOL + POOL_ENTRY * k, 0xff);
  }

  /** $1C6E2: bursts idle, the 64 shards chained into the free list. */
  debris_init(): void {
    const m = this.mem;
    for (let k = 0; k < 4; k++) {
      const a = DEBRIS + 0x12 * k;
      m.set8(a, 0);
      m.set32(a + 0xe, 0);
    }
    m.set32(0x1194, SHARDS);
    for (let k = 0; k < 63; k++) {
      const a = SHARDS + 0x1a * k;
      m.set32(a + 0x16, a + 0x1a);
    }
    m.set32(SHARDS + 0x1a * 63 + 0x16, 0);
    m.set16(0x1198, 0x40);
  }

  /** $4260: in game, end request cleared. */
  game_flags_reset(): void {
    const m = this.mem;
    m.set8(0x8f0, 0xff);
    m.set8(0x1230, 0);
    m.set8(0x1231, 0xff);
    m.set8(0x463, 0);
  }

  /** The state part of frame_flip $17F48: a pending full redraw ([$458]) puts the pointer
   * back on. */
  frame_flip_flags(): void {
    const m = this.mem;
    if (m.u8(FULL_REDRAW)) {
      m.set32(0x46e, 0x40e26);
      m.set8(POINTER_MODE, 0xff);
      m.set8(FULL_REDRAW, 0);
    }
    m.set16(0x8ee, 0);
  }

  // ---- init_islands $18370 ------------------------------------------------------------
  /** $18370: islands from their records, 21 trees each, the start structures, then
   * world_warmup. */
  init_islands(): void {
    const m = this.mem;
    this.slot_order_init();
    this.island_index = 0;
    m.buf.fill(0, OWNERS, OWNERS + ISLANDS);
    m.buf.fill(0, ECONOMY, ECONOMY + 0xc80);
    for (let i = 0; i < ISLANDS; i++) {
      const r = ISLAND_RECORDS + ISLAND_RECORD * i;
      const a = ISLAND_TABLE + ISLAND_SIZE * i;
      const e = ECONOMY + ECONOMY_SIZE * i;
      m.set8(a, 0);
      m.set16(a + 0x10, 0);
      m.set32(a + 2, m.s16(r) * 512);
      m.set32(a + 6, m.s16(r + 2) * 512);
      const hx = m.u16(r + 4), hz = m.u16(r + 6);
      m.set16(a + 0xa, hx);
      m.set16(a + 0xc, hz);
      const area = shr(hx * hz, 16);
      m.set16(a + 0xe, area);
      m.set16(e + 4, 2 * area);
      m.set16(e + 2, 0);
      m.set16(e + 0xa, 2 * area);
      m.set16(e + 8, 0);
      for (const o of [0xe, 0x1a, 0x26]) m.set8(e + o, 0xff);
      m.buf.fill(0, a + 0x12, a + 0x12 + 0x180);
      m.set16(a + 0x12, 8); // slot 0: the island itself
      let s = a + 0x1e;
      let x = m.u16(r + 8), z = m.u16(r + 10);
      const h = m.u16(r + 12);
      if (x | z) {
        // slot 1: the runway, to be built
        m.set16(s, T_RUNWAY_CONSTRUCTION);
        m.set16(s + 8, x);
        m.set16(s + 10, z);
        m.set16(s + 2, h);
      }
      s += 0xc;
      for (let k = 0; k < 2; k++) {
        // volcanoes
        x = m.u16(r + 14 + 4 * k);
        z = m.u16(r + 16 + 4 * k);
        if (x | z) {
          m.set16(s, 7);
          m.set16(s + 8, x);
          m.set16(s + 10, z);
          m.set16(s + 2, this.rand());
          s += 0xc;
        }
      }
    }
    this.plant_trees();
    this.place_start_structures();
    this.warmup_run();
  }

  /** $18924: the slot visiting order 0..31 (index, offset) before any shuffle. */
  slot_order_init(): void {
    for (let k = 0; k < SLOTS; k++) {
      this.mem.set16(SLOT_ORDER + 4 * k, k);
      this.mem.set16(SLOT_ORDER + 4 * k + 2, SLOT_SIZE * k);
    }
  }

  /** $18940: the island, economy and 3D buffer pointers of island i. */
  select_island(i: number): void {
    const m = this.mem;
    const isl = ISLAND_TABLE + ISLAND_SIZE * i;
    m.set32(G_ISLAND_PTR, isl);
    m.set32(G_ECONOMY_PTR, ECONOMY + ECONOMY_SIZE * i);
    m.set32(G_BUFFER_PTR, OBJ_BUFFERS[m.u8(isl + 1) ? 1 : 0]);
  }

  /** find_free_island_spot $18A48 until it finds a spot (the original loops on -1). -2 (no
   * empty or tree slot left) is a trap #7 in $189CA. */
  _free_spot(t: number, i: number): [number, number, number] {
    for (;;) {
      const spot = this.find_free_spot(t, i);
      if (spot !== null) return spot;
      if (this.island(i).slots.every((s) => s.type)) {
        throw new NotPorted("new game: island full, find_free_island_spot -2 (trap #7)");
      }
    }
  }

  /** $1897E: 21 random trees ($18-$1B) on every island, from island 63 down. */
  plant_trees(): void {
    for (let i = ISLANDS - 1; i >= 0; i--) {
      this.island_index = i;
      this.select_island(i);
      for (let n = 0; n < 21; n++) {
        const [j, x, z] = this._free_spot(T_TREE_FIRST, i);
        const s = this.island(i).slots[j];
        s.x = x;
        s.z = z;
        s.type = (this.rand() & 3) + T_TREE_FIRST;
      }
    }
  }

  /** $189CA: the command centres under construction ($34, progress $3B) of the start list,
   * (island, start code) pairs: $18FE2 in a strategy game, else $1901C. */
  place_start_structures(): void {
    const m = this.mem;
    let a = m.u8(STRATEGY) ? START_STRATEGY + 0x14 : START_ACTION + 0x14;
    while (!(m.u16(a) & 0x8000)) {
      const i = m.u16(a), code = m.u16(a + 2);
      a += 4;
      m.set16(ISLAND_SCRATCH, i);
      this.select_island(i);
      const [j, x, z] = this._free_spot(T_COMMAND_CENTRE_CONSTRUCTION, i);
      const s = this.island(i).slots[j];
      s.type = T_COMMAND_CENTRE_CONSTRUCTION;
      s.x = x;
      s.z = z;
      s.value = 0x3b;
      s.word6 = code;
    }
  }

  // ---- the first screen -----------------------------------------------------------------
  /** The state part of what follows $42A8 in the same game_main pass, before the simulation
   * block: ui_frame ($F636) takes the screen request $42A8 left ([$1227], tab [$1228] = 1, whose
   * last screen [$722] is 0), leaves the title (screen [$718] = -1: no leave handler) and enters
   * the bridge ($139AE, $13C24). The panel and gauge drawing ($782A, $7A66) is left out. */
  new_game_screen(): void {
    const m = this.mem;
    if (!m.u8(0x1227)) return;
    m.set8(0x1227, 0);
    const tab = m.u16(0x1228);
    if (!(m.u16(SCREEN) & 0x8000)) {
      throw new NotPorted(`screen leave handler for screen ${m.u16(SCREEN)}`);
    }
    m.set16(0x714, tab);
    const screen = m.u16(0x720 + 2 * tab);
    m.set16(SCREEN, screen);
    m.set8(0x122d, 0); // $139AE
    for (const a of [0x9fa, VIEWED, 0xa02]) m.set32(a, 0);
    if (screen !== 0) throw new NotPorted(`screen enter handler for screen ${screen}`);
    const cam = this.obj(0); // $13C24: the bridge view
    m.set8(0x1230, 0xff);
    m.set8(0xa46, 0);
    m.set8(0xa47, 0);
    m.set32(0x9fa, cam);
    m.set32(VIEWED, CARRIER);
    m.set32(0xa02, CARRIER);
    m.set16(0xa14, 2);
    m.set16(cam, 0);
    m.set32(cam + 0xe, 0xf0);
    m.set32(cam + 0x3c, m.u32(CARRIER + 0x3c));
    for (const a of [0x954, 0x958, 0x956, 0x962]) m.set16(a, 2);
    m.set16(0x480, m.u16(C_DEPTH));
    m.set16(0x40f66, m.u8(C_FLAGS) & 1 ? 0x2000 : 0); // $12C16: autopilot button lit
    m.set8(0x716, 0xff); // back in $F6DC
    m.set8(0x71a, 0xff);
  }

  // ---- the rest of $42A8 ----------------------------------------------------------------
  /** $55BC: $D00 random words for the screen-noise effect (not simulation state, but it draws
   * the RNG). */
  noise_table_init(): void {
    for (let k = 0; k < NOISE_WORDS; k++) this.mem.set16(NOISE + 2 * k, this.rand());
  }

  /** $43D2: no objects but the two carriers at their start positions. */
  object_store_init(): void {
    const m = this.mem;
    m.buf.fill(0, OBJECTS, OBJECTS + 64 * OBJECT_SIZE);
    m.set16(0xa48, 0x40);
    for (const b of ISLAND_BUFFERS) m.set8(b, m.u8(b) & 0xfe);
    for (let k = 0; k < 4; k++) {
      m.set16(this.obj(2 + k) + 0x1a, k);
      m.set16(this.obj(6 + k) + 0x1a, k);
    }
    const t = m.u8(STRATEGY) ? START_STRATEGY : START_ACTION;
    for (const [k, typ, x, z, h] of [
      [10, 1, 0, 4, 8],
      [19, 4, 0xa, 0xe, 0x12],
    ]) {
      const o = this.obj(k);
      m.set16(NEW_INDEX, k);
      this.init_world_object(o, typ);
      this.reset_pair_schedules();
      m.set32(o + 0xe, 0xa0);
      m.set32(o + 0xa, m.u32(t + x));
      m.set32(o + 0x12, m.u32(t + z));
      m.set16(o + 6, m.u16(t + h));
    }
  }

  /** The end of $42A8: map views centred on the carrier, the map cursors ($69E Manta, $6AC
   * Walrus, $6BA carrier (guess)) 1000 off it. */
  map_views_init(): void {
    const m = this.mem;
    let x = m.u32(CARRIER + 0xa), z = m.u32(CARRIER + 0x12);
    m.set32(0x75c, x);
    m.set32(0x760, z);
    x += 1000;
    z += 1000;
    for (const a of [0x69e, 0x6b0, 0x6be]) m.set32(a, x);
    for (const a of [0x6a6, 0x6b4, 0x6c2]) m.set32(a, z);
    for (const a of [0x768, 0x778, 0x788]) m.buf.copyWithin(a, 0x758, 0x768);
  }
}
