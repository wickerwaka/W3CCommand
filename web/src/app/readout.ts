// Read-only views of the game state for the UI. Addresses and meanings are from docs/units.md
// and docs/simulation.md; the UI never writes state (commands go through the model).
import { CARRIER, C_SPEED, C_TARGET, C_WAYPOINT_X, C_WAYPOINT_Z, C_FLAGS, SHIP_FUEL } from "../sim/units_core.ts";
import { ISLAND_SIZE, ISLAND_TABLE, NETWORK, NETWORK_SIZE, OBJECTS, OBJECT_SIZE, OWNERS, SYSTEMS, N_SYSTEMS, COUNT_PLAYER, COUNT_ENEMY } from "../sim/world.ts";

const NAMES = 0x4ad20;              // 64 word offsets (from $4AD20) to NUL-terminated names
export const ENEMY_CARRIER_OBJ = OBJECTS + OBJECT_SIZE * 19;

const u8 = (r: Uint8Array, a: number) => r[a];
const u16 = (r: Uint8Array, a: number) => (r[a] << 8) | r[a + 1];
const s16 = (r: Uint8Array, a: number) => (u16(r, a) << 16) >> 16;
const s32 = (r: Uint8Array, a: number) => (r[a] << 24) | (r[a + 1] << 16) | (r[a + 2] << 8) | r[a + 3];
const u32 = (r: Uint8Array, a: number) => s32(r, a) >>> 0;

export interface IslandInfo {
  index: number;
  name: string;
  x: number;
  z: number;
  halfX: number;
  halfZ: number;
  owner: number;           // 0 neutral, 1 player, 2 enemy
}

export function islandNames(ram: Uint8Array): string[] {
  const names: string[] = [];
  for (let i = 0; i < 64; i++) {
    let a = NAMES + u16(ram, NAMES + 2 * i), s = "";
    while (ram[a]) s += String.fromCharCode(ram[a++]);
    names.push(s);
  }
  return names;
}

export function islands(ram: Uint8Array, names: string[]): IslandInfo[] {
  const out: IslandInfo[] = [];
  for (let i = 0; i < 64; i++) {
    const a = ISLAND_TABLE + ISLAND_SIZE * i;
    out.push({
      index: i, name: names[i],
      x: s32(ram, a + 2), z: s32(ram, a + 6),
      halfX: u16(ram, a + 0x0a), halfZ: u16(ram, a + 0x0c),
      owner: u8(ram, OWNERS + i),
    });
  }
  return out;
}

/** Supply network links (i < j once each): count.w then (weight.w, island.w) pairs. */
export function links(ram: Uint8Array): [number, number][] {
  const out: [number, number][] = [];
  for (let i = 0; i < 64; i++) {
    const n = NETWORK + NETWORK_SIZE * i;
    for (let k = 0, c = u16(ram, n); k < c && k < 7; k++) {
      const weight = u16(ram, n + 2 + 4 * k), j = u16(ram, n + 4 + 4 * k) & 0x7fff;
      if (weight && j > i && j < 64) out.push([i, j]);
    }
  }
  return out;
}

export interface UnitInfo {
  obj: number;
  type: number;
  x: number;
  z: number;
  heading: number;         // 16-bit angle
}

/** A live object (flags byte +1 bit 0). */
export function unit(ram: Uint8Array, k: number): UnitInfo | null {
  const o = OBJECTS + OBJECT_SIZE * k;
  if (!(ram[o + 1] & 1)) return null;
  return { obj: k, type: u16(ram, o + 2), x: s32(ram, o + 0x0a), z: s32(ram, o + 0x12), heading: u16(ram, o + 6) };
}

export interface CarrierInfo {
  x: number;
  z: number;
  heading: number;         // degrees, 0 = +z ("north" on the map), clockwise
  speed: number;           // current speed (units per pass)
  rudder: number;          // -255..255
  targetSpeed: number;
  fuel: number;
  autopilot: boolean;
  waypoint: { x: number; z: number };
  island: number | null;   // island whose 3D buffer the carrier is attached to
}

export function carrier(ram: Uint8Array): CarrierInfo {
  const o = CARRIER;
  const buf = u32(ram, o + 0x3c);
  return {
    x: s32(ram, o + 0x0a), z: s32(ram, o + 0x12),
    heading: (u16(ram, o + 6) * 360) / 0x10000,
    speed: s16(ram, C_SPEED), targetSpeed: s16(ram, C_TARGET), rudder: s16(ram, 0x642),
    fuel: u32(ram, SHIP_FUEL),
    autopilot: (ram[C_FLAGS] & 1) !== 0,
    waypoint: { x: s32(ram, C_WAYPOINT_X), z: s32(ram, C_WAYPOINT_Z) },
    island: buf ? ram[buf + 1] : null,
  };
}

export interface SystemInfo {
  index: number;
  current: number;
  max: number;
  priority: number;
}

/** Carrier systems ($AF4, 10 x 8: cur.w max.w class.w status.w). */
export function systems(ram: Uint8Array): SystemInfo[] {
  const out: SystemInfo[] = [];
  for (let k = 0; k < N_SYSTEMS; k++) {
    const a = SYSTEMS + 8 * k;
    out.push({ index: k, current: u16(ram, a), max: u16(ram, a + 2), priority: u16(ram, a + 4) });
  }
  return out;
}

export function islandCounts(ram: Uint8Array): { player: number; enemy: number } {
  return { player: u16(ram, COUNT_PLAYER), enemy: u16(ram, COUNT_ENEMY) };
}

// ---- vehicles (docs/units.md §6.1) ---------------------------------------------------------
export const MANTA_RECS = 0x496, WALRUS_RECS = 0x570, REC_SIZE = 0x34;

export type VehicleKind = "manta" | "walrus";

export const STATUS_NAMES: Record<number, string> = {
  0: "Ready", 1: "Repairing", 2: "Preparing", 3: "On the lift", 8: "Out", 0xff: "Destroyed",
};

export interface VehicleInfo {
  kind: VehicleKind;
  bay: number;              // 0-3 (shown as 1-4)
  obj: number;              // world object index
  status: number;           // +1: 0 ready, 1 repair, 2 preparing, 3 lift/launching, 8 out, $FF destroyed
  fuel: number;             // fraction 0..1
  health: number;           // fraction 0..1 (max 4000)
  speed: number;
  targetSpeed: number;
  automatic: boolean;
  weapon: number;           // selected weapon: 0 laser, 1 missile, 2 payload
  missiles: number;
  payload: number;          // payload code
  telemetry: number;        // 0 OK, 1 weak, 2 lost
  x: number;
  y: number;
  z: number;
  heading: number;          // 16-bit angle
}

export function vehicle(ram: Uint8Array, kind: VehicleKind, bay: number): VehicleInfo {
  const manta = kind === "manta";
  const r = (manta ? MANTA_RECS : WALRUS_RECS) + REC_SIZE * bay;
  const objIndex = (manta ? 2 : 6) + bay;
  const o = OBJECTS + OBJECT_SIZE * objIndex;
  const fuelMax = manta ? 0x7fff : 0x7fff;
  return {
    kind, bay, obj: objIndex,
    status: ram[r + 1],
    fuel: Math.min(1, u16(ram, r + (manta ? 0x0a : 0x08)) / fuelMax),
    health: Math.min(1, u16(ram, r + 0x14) / 4000),
    speed: s16(ram, r + (manta ? 6 : 4)),
    targetSpeed: s16(ram, r + (manta ? 8 : 6)),
    automatic: (ram[r] & 1) !== 0,
    weapon: u16(ram, r + (manta ? 0x1a : 0x18)),
    missiles: u16(ram, r + 0x0c),
    payload: u16(ram, r + 0x0e),
    telemetry: u16(ram, r + (manta ? 0x2e : 0x2c)),
    x: s32(ram, o + 0x0a), y: s32(ram, o + 0x0e), z: s32(ram, o + 0x12),
    heading: u16(ram, o + 6),
  };
}
