// The 3D world, rebuilt from the RAM image every frame.
//
// What the original draws (docs/memory-map.md, "3D renderer"): the world objects at $5DCDA and
// the island buffers ($62CDA, $63CEC) of the islands loaded around the units, each object by
// type through object_lod_table ($8B58: near/mid distance, near/mid/far model). We draw the same,
// and also every other island as load_island_buffer ($17FB4) would build it from its structure
// slots, so the whole archipelago is visible from a free camera.
//
// Positions are large (world x/z reach ~2^25), so the scene is camera-relative: the eye sits at
// the three.js origin and objects are placed at (position - eye), computed in doubles.
import * as THREE from "three";
import { ISLAND_BUFFERS } from "../sim/units_core.ts";
import { ISLAND_SIZE, ISLAND_TABLE, OBJECTS, OBJECT_SIZE } from "../sim/world.ts";
import { UnitState } from "../sim/index.ts";
import { BASIS_PLATEAU, BASIS_QUAD, decodeModel, modelDependsOn, type Model } from "./models.ts";
import { buildModelMeshes, disposeModelMeshes, lineMaterial, polyMaterial, type ModelMeshes } from "./meshes.ts";
import { paletteColour } from "./palette.ts";
import { gameRotation, toThreeQuaternion } from "./transforms.ts";

export const LOD_TABLE = 0x8b58;
const RECORD = 0x40;                 // the shared part of world objects and island entries
const ISLANDS = 64;

/** Something to draw: a world object or an island entry, with world position in game units. */
export interface Drawable {
  key: string;
  type: number;
  x: number;
  y: number;
  z: number;
  pitch: number;
  yaw: number;
  roll: number;
  rec: Uint8Array;                   // its $40-byte record (model parameters)
}

export interface Eye {
  x: number;
  y: number;
  z: number;
}

const u16 = (b: Uint8Array, a: number) => (b[a] << 8) | b[a + 1];
const s16 = (b: Uint8Array, a: number) => (u16(b, a) << 16) >> 16;
const s32 = (b: Uint8Array, a: number) => (b[a] << 24) | (b[a + 1] << 16) | (b[a + 2] << 8) | b[a + 3];
const u32 = (b: Uint8Array, a: number) => s32(b, a) >>> 0;

/** Entries of an island buffer (the $12-byte header, then 64 x $40). */
function bufferEntries(mem: Uint8Array, buf: number, key: string, out: Drawable[]): void {
  const ox = s32(mem, buf + 2), oy = s32(mem, buf + 6), oz = s32(mem, buf + 0x0a);
  for (let k = 0; k < 64; k++) {
    const e = buf + 0x12 + RECORD * k;
    if ((mem[e + 1] & 3) !== 3) continue;
    out.push({
      key: `${key}:${k}`,
      type: u16(mem, e + 2),
      x: ox + s16(mem, e + 0x0a),
      y: oy + s16(mem, e + 0x0c),
      z: oz + s16(mem, e + 0x0e),
      pitch: u16(mem, e + 4),
      yaw: u16(mem, e + 6),
      roll: u16(mem, e + 8),
      rec: mem.slice(e, e + RECORD),
    });
  }
}

/** Islands that aren't loaded, built from their slots by the model's own load_island_buffer
 * on a scratch copy of the state; rebuilt only when an island's slots change. */
class IslandCache {
  private scratch: UnitState;
  private cached = new Map<number, { slots: string; entries: Drawable[] }>();

  constructor(ram: Uint8Array) {
    this.scratch = UnitState.from_ram(ram);
  }

  entries(ram: Uint8Array, i: number): Drawable[] {
    const isl = ISLAND_TABLE + ISLAND_SIZE * i;
    const slots = String.fromCharCode(...ram.subarray(isl, isl + ISLAND_SIZE));
    const hit = this.cached.get(i);
    if (hit && hit.slots === slots) return hit.entries;
    const mem = this.scratch.mem.buf;
    mem.set(ram.subarray(isl, isl + ISLAND_SIZE), isl);
    const buf = ISLAND_BUFFERS[0];
    const entries: Drawable[] = [];
    try {
      this.scratch.load_island_buffer(buf, i);
      bufferEntries(mem, buf, `island${i}`, entries);
    } catch (e) {
      console.warn(`island ${i}:`, e);
    }
    this.cached.set(i, { slots, entries });
    return entries;
  }
}

export function gatherDrawables(ram: Uint8Array, islands: IslandCache | null): Drawable[] {
  const out: Drawable[] = [];
  // Units on the lift or in the dock (byte +1 bit 2) are skipped by the world pass and drawn as
  // the carrier's attachments instead: [$6D6] and [$6DA] (BSP slots of carrier models 2/3).
  const attached = new Set([u32(ram, 0x6d6), u32(ram, 0x6da)]);
  for (let k = 0; k < 64; k++) {
    const o = OBJECTS + OBJECT_SIZE * k;
    if ((ram[o + 1] & 3) !== 3) continue;
    if (ram[o + 1] & 4 && !attached.has(o)) continue;
    out.push({
      key: `obj${k}`,
      type: u16(ram, o + 2),
      x: s32(ram, o + 0x0a),
      y: s32(ram, o + 0x0e),
      z: s32(ram, o + 0x12),
      pitch: u16(ram, o + 4),
      yaw: u16(ram, o + 6),
      roll: u16(ram, o + 8),
      rec: ram.slice(o, o + RECORD),
    });
  }
  const loaded = new Set<number>();
  ISLAND_BUFFERS.forEach((buf, n) => {
    if (!ram[buf]) return;
    loaded.add(ram[buf + 1]);
    bufferEntries(ram, buf, `buffer${n}`, out);
  });
  if (islands) for (let i = 0; i < ISLANDS; i++) if (!loaded.has(i)) out.push(...islands.entries(ram, i));
  return out;
}

/** The model an object type draws at a depth: object_lod_table near/mid/far. The game's
 * metric ($7B72) is the camera-space depth Z (minus a zoom bias that is 0 on the bridge). */
export function lodModel(ram: Uint8Array, type: number, dist: number): number {
  const t = LOD_TABLE + 16 * type;
  if (dist < u32(ram, t)) return u16(ram, t + 8);
  if (dist < u32(ram, t + 4)) return u16(ram, t + 10);
  return u16(ram, t + 12);
}

interface Slot {
  group: THREE.Group;
  modelKey: string;
}

/** Decoded model geometry plus how it is oriented. */
interface Entry {
  meshes: ModelMeshes;
  /** $AA24 (trees): a quad that always faces the camera's heading. */
  billboard: boolean;
  /** $A660 (island plateau): drawn unrotated. */
  fixed: boolean;
}

export class WorldView {
  readonly scene = new THREE.Scene();
  showAllIslands = true;
  private ram: Uint8Array;
  private islands: IslandCache;
  private geometry = new Map<string, Entry | null>();
  private dynamic = new Map<number, boolean>();
  private slots = new Map<string, Slot>();
  private polyMat = polyMaterial();
  private lineMat = lineMaterial();
  private sea: THREE.Mesh;
  private discGeo = new THREE.SphereGeometry(1, 12, 8);
  /** Sea dots ($91EC): 8 x 8 points on y = 0 at multiples of $2000, -4..+3 grid steps around
   * the camera, white (colour 8); drawn while the game option [$1246] is 0. */
  private seaDots: THREE.Points;

  constructor(ram: Uint8Array) {
    this.ram = ram;
    this.islands = new IslandCache(ram);
    this.scene.background = paletteColour(ram, 2);         // $6402: sky colour 2, sea 10
    this.sea = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: paletteColour(ram, 10), depthWrite: true }),
    );
    this.sea.scale.setScalar(4e6);
    this.sea.renderOrder = -1;
    this.scene.add(this.sea);
    const dots = new THREE.BufferGeometry();
    dots.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(64 * 3), 3));
    this.seaDots = new THREE.Points(dots, new THREE.PointsMaterial({ color: paletteColour(ram, 8), size: 2, sizeAttenuation: false }));
    this.seaDots.frustumCulled = false;
    this.scene.add(this.seaDots);
  }

  dispose(): void {
    for (const g of this.geometry.values()) if (g) disposeModelMeshes(g.meshes);
    this.polyMat.dispose();
    this.lineMat.dispose();
    this.discGeo.dispose();
  }

  /** Rebuild the scene for an eye position (game units) and view orientation. */
  update(eye: Eye, view: THREE.Quaternion): void {
    const ram = this.ram;
    this.sea.position.set(0, -eye.y, 0);
    this.seaDots.visible = ram[0x1246] === 0;
    if (this.seaDots.visible) {
      const pos = this.seaDots.geometry.getAttribute("position") as THREE.BufferAttribute;
      const gx = Math.floor(eye.x / 0x2000) * 0x2000, gz = Math.floor(eye.z / 0x2000) * 0x2000;
      let k = 0;
      for (let i = -4; i < 4; i++)
        for (let j = -4; j < 4; j++) pos.setXYZ(k++, gx + i * 0x2000 - eye.x, -eye.y, -(gz + j * 0x2000 - eye.z));
      pos.needsUpdate = true;
    }
    // camera forward in game coordinates (three's -z is game +z)
    const f = new THREE.Vector3(0, 0, -1).applyQuaternion(view);
    const fx = f.x, fy = f.y, fz = -f.z;
    const camYaw = (Math.atan2(fx, fz) * 0x10000) / (2 * Math.PI);
    const seen = new Set<string>();
    for (const d of gatherDrawables(ram, this.showAllIslands ? this.islands : null)) {
      const depth = (d.x - eye.x) * fx + (d.y - eye.y) * fy + (d.z - eye.z) * fz;
      const index = lodModel(ram, d.type, depth);
      const key = this.modelKey(index, d.rec);
      const entry = this.meshes(key, index, d.rec);
      if (!entry) continue;
      const meshes = entry.meshes;
      seen.add(d.key);
      let slot = this.slots.get(d.key);
      if (!slot || slot.modelKey !== key) {
        if (slot) this.scene.remove(slot.group);
        slot = { group: this.makeGroup(meshes), modelKey: key };
        this.slots.set(d.key, slot);
        this.scene.add(slot.group);
      }
      slot.group.position.set(d.x - eye.x, d.y - eye.y, -(d.z - eye.z));
      const rot = entry.fixed ? gameRotation(0, 0, 0) : entry.billboard ? gameRotation(0, camYaw, 0) : gameRotation(d.pitch, d.yaw, d.roll);
      toThreeQuaternion(rot, slot.group.quaternion);
    }
    for (const [k, slot] of this.slots)
      if (!seen.has(k)) {
        this.scene.remove(slot.group);
        this.slots.delete(k);
      }
  }

  private modelKey(index: number, rec: Uint8Array): string {
    let dyn = this.dynamic.get(index);
    if (dyn === undefined) {
      dyn = modelDependsOn(this.ram, index).length > 0;
      this.dynamic.set(index, dyn);
    }
    if (!dyn) return `${index}`;
    // the object fields and globals the dynamic basis routines read (models.ts)
    const g = this.ram;
    const bytes = [...rec.subarray(0x1e, 0x28), ...rec.subarray(0x38, 0x3a), ...g.subarray(0x6ca, 0x6ce), ...g.subarray(0xdd6, 0xdda)];
    return `${index}:${bytes.join(",")}`;
  }

  private meshes(key: string, index: number, rec: Uint8Array): Entry | null {
    if (this.geometry.has(key)) return this.geometry.get(key)!;
    let model: Model | null = null;
    try {
      model = decodeModel(this.ram, index, { obj: rec, ram: this.ram });
    } catch (e) {
      console.warn(`model ${index}:`, e);
    }
    const m = model ? buildModelMeshes(this.ram, model) : null;
    const usable =
      model && m && (m.polys || m.lines || m.discs.length)
        ? { meshes: m, billboard: model.basis === BASIS_QUAD, fixed: model.basis === BASIS_PLATEAU }
        : null;
    this.geometry.set(key, usable);
    return usable;
  }

  private makeGroup(m: ModelMeshes): THREE.Group {
    const g = new THREE.Group();
    if (m.polys) g.add(new THREE.Mesh(m.polys, this.polyMat));
    if (m.lines) g.add(new THREE.LineSegments(m.lines, this.lineMat));
    for (const d of m.discs) {
      const s = new THREE.Mesh(this.discGeo, new THREE.MeshBasicMaterial({ color: d.colour }));
      s.position.copy(d.centre);
      s.scale.setScalar(Math.max(d.radius, 1));
      g.add(s);
    }
    return g;
  }
}

export { IslandCache };
