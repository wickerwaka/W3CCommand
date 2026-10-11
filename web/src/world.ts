// Builds the archipelago scene from world.json: island plateaus, their structures (with the
// game's distance-based model substitution), the sea, map markers, supply links and labels.

import * as THREE from "three";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import type { Island, World } from "./data";
import { ModelLibrary, buildModel, modelObject } from "./models";

export const ISLAND_MODEL = 8;
export const BEACH = 80;             // model 8: plateau top inset and height
const TREE_TYPES = new Set([24, 25, 26, 27]);

// Sky and sea colours: palette indices assumed from the ST version's look (light blue sky,
// deep blue sea); draw_horizon_sea ($6402) has not been decoded yet.
export const SKY = 11;
export const SEA = 10;
// Map marker colours by owner (neutral, player, enemy), palette indices.
export const OWNER_COLOURS = [12, 9, 13];

const angle = (heading: number) => (heading / 65536) * Math.PI * 2;

export function srgb(world: World, index: number): THREE.Color {
  const [r, g, b] = world.palette[index];
  return new THREE.Color().setRGB(r / 255, g / 255, b / 255, THREE.SRGBColorSpace);
}

/** Island-local game coordinates -> three.js world position. */
export function islandWorld(isl: Island, x = 0, y = 0, z = 0): THREE.Vector3 {
  return new THREE.Vector3(isl.x + x, y, -(isl.z + z));
}

export interface IslandView {
  island: Island;
  group: THREE.Group;
  trees: THREE.Object3D[];
  label: CSS2DObject;
  centre: THREE.Vector3;
  radius: number;
}

export class WorldScene {
  readonly root = new THREE.Group();
  readonly islands: IslandView[] = [];
  readonly markers: THREE.Points;
  readonly links: THREE.LineSegments;
  readonly sea: THREE.Mesh;
  readonly seaDots: THREE.Points;
  readonly bounds = new THREE.Box3();
  private lib: ModelLibrary;

  constructor(readonly world: World) {
    this.lib = new ModelLibrary(world);
    for (const isl of world.islands) this.islands.push(this.buildIsland(isl));
    for (const v of this.islands) this.bounds.expandByPoint(v.centre);

    this.sea = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: srgb(world, SEA) }),
    );
    this.sea.scale.setScalar(4e8);
    this.sea.renderOrder = -1;
    this.root.add(this.sea);

    this.seaDots = this.buildSeaDots();
    this.root.add(this.seaDots);
    this.markers = this.buildMarkers();
    this.root.add(this.markers);
    this.links = this.buildLinks();
    this.root.add(this.links);
  }

  private buildIsland(isl: Island): IslandView {
    const group = new THREE.Group();
    group.position.copy(islandWorld(isl));
    group.name = isl.name;

    // Model 8 is procedural ($A660): base corners at (+-hx, 0, +-hz), top inset by 80 at
    // height 80. Reuse its faces and colours with this island's extents.
    const m8 = this.world.models[String(ISLAND_MODEL)];
    const plateau = buildModel({
      ...m8,
      vertices: m8.vertices.map(([x, y, z]) => {
        const inset = y === 0 ? 0 : BEACH;
        return [Math.sign(x) * (isl.half_x - inset), y, Math.sign(z) * (isl.half_z - inset)];
      }),
    }, this.lib.colour, 0);
    group.add(modelObject(plateau));

    const trees: THREE.Object3D[] = [];
    for (const s of isl.slots) {
      if (s.type === ISLAND_MODEL) continue;
      const obj = this.structure(s.type);
      if (!obj) continue;
      obj.position.set(s.x, BEACH, -s.z);
      obj.rotation.y = -angle(s.heading);
      obj.userData = { slot: s };
      group.add(obj);
      if (TREE_TYPES.has(s.type)) trees.push(obj);
    }

    const el = document.createElement("div");
    el.className = `label owner-${isl.owner}`;
    el.textContent = isl.name;
    el.dataset.island = String(isl.index);
    const label = new CSS2DObject(el);
    label.position.set(0, 400, 0);
    label.center.set(0.5, 1.4);
    group.add(label);

    this.root.add(group);
    return {
      island: isl, group, trees, label,
      centre: islandWorld(isl, 0, BEACH),
      radius: Math.hypot(isl.half_x, isl.half_z),
    };
  }

  /** An object of the given type, with the game's near/mid/far model substitution. */
  private structure(type: number): THREE.Object3D | null {
    const t = this.world.object_types[type];
    if (!t) return null;
    const [near, mid, far] = t.models;
    const level = (model: number) => {
      const b = this.lib.get(model);
      if (!b) return null;
      const o = modelObject(b);
      o.position.y = -b.minY;          // stand it on the plateau
      return o;
    };
    const first = level(near);
    if (!first) return null;
    if (mid === near && far === near) return first;
    const lod = new THREE.LOD();
    lod.addLevel(first, 0);
    if (mid !== near) {
      const o = level(mid);
      if (o) lod.addLevel(o, t.near_dist);
    }
    if (far !== mid) {
      const o = level(far);
      if (o) lod.addLevel(o, t.mid_dist);
    }
    return lod;
  }

  private buildMarkers(): THREE.Points {
    const pos: number[] = [];
    const col: number[] = [];
    for (const v of this.islands) {
      pos.push(v.centre.x, 200, v.centre.z);
      const c = srgb(this.world, OWNER_COLOURS[v.island.owner] ?? 12);
      col.push(c.r, c.g, c.b);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    return new THREE.Points(g, new THREE.PointsMaterial({
      size: 9, sizeAttenuation: false, vertexColors: true, depthTest: false,
    }));
  }

  private buildLinks(): THREE.LineSegments {
    const pos: number[] = [];
    for (const v of this.islands) {
      for (const l of v.island.links) {
        if (l.island < v.island.index) continue;      // each link once
        const w = this.islands[l.island];
        pos.push(v.centre.x, 150, v.centre.z, w.centre.x, 150, w.centre.z);
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    return new THREE.LineSegments(g, new THREE.LineBasicMaterial({
      color: srgb(this.world, 8), transparent: true, opacity: 0.35, depthTest: false,
    }));
  }

  // Approximation of the game's "sea dots" option: a grid of points on the water that
  // gives a sense of motion. Spacing scales with altitude.
  private static DOTS = 41;
  private buildSeaDots(): THREE.Points {
    const n = WorldScene.DOTS;
    const pos = new Float32Array(n * n * 3);
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        pos.set([i - (n - 1) / 2, 0, j - (n - 1) / 2], 3 * (i * n + j));
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return new THREE.Points(g, new THREE.PointsMaterial({
      color: srgb(this.world, 11), size: 2, sizeAttenuation: false,
    }));
  }

  /** Per-frame updates that depend on the camera. */
  update(camera: THREE.Camera, opts: { mapOverlay: boolean; seaDots: boolean; labels: boolean }) {
    const cam = camera.position;
    const alt = Math.max(1, cam.y);
    this.sea.position.set(cam.x, 0, cam.z);

    const spacing = 2 ** Math.ceil(Math.log2(Math.max(128, alt * 0.4)));
    this.seaDots.visible = opts.seaDots && alt < 60000;
    this.seaDots.scale.set(spacing, 1, spacing);
    this.seaDots.position.set(Math.round(cam.x / spacing) * spacing, 1, Math.round(cam.z / spacing) * spacing);

    const overview = alt > 40000;
    this.markers.visible = opts.mapOverlay && overview;
    this.links.visible = opts.mapOverlay && overview;

    for (const v of this.islands) {
      const d = cam.distanceTo(v.centre);
      v.label.visible = opts.labels && (overview || d < 400000);
      // Trees are flat quads; turn them to face the camera (only near ones matter).
      if (d < 120000) {
        for (const t of v.trees) {
          const p = t.getWorldPosition(tmp);
          t.rotation.y = Math.atan2(cam.x - p.x, cam.z - p.z);
        }
      }
    }
  }
}

const tmp = new THREE.Vector3();
