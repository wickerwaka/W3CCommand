// Turns decoded CARRIER.TOS models into three.js geometry.
//
// The game draws flat, unlit polygons in a BSP order. Each colour byte maps to two palette
// indices through the $13CA table; when they differ the game fills with a two-colour
// dither. Here every vertex carries both colours, and the fragment shader either draws the
// checkerboard (ST look) or their average.
//
// The game has no depth buffer, so decals (runway markings, painted panels) are polygons
// lying exactly on other polygons and drawn after them. To keep them on top, each polygon
// gets a "layer": one more than any earlier coplanar polygon in the same model. The vertex
// shader pulls layered vertices towards the camera by a tiny fraction of their distance,
// which works with the logarithmic depth buffer (where polygonOffset does not).

import * as THREE from "three";
import type { ModelData, World } from "./data";

const vertexShader = /* glsl */ `
#include <common>
#include <logdepthbuf_pars_vertex>
attribute vec3 colorA;
attribute vec3 colorB;
attribute float layer;
uniform float layerBias;
varying vec3 vA;
varying vec3 vB;
void main() {
  vA = colorA;
  vB = colorB;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  mv.xyz *= 1.0 - layer * layerBias;
  gl_Position = projectionMatrix * mv;
  #include <logdepthbuf_vertex>
}`;

// Colours are the ST palette's sRGB values and are written out unconverted.
const fragmentShader = /* glsl */ `
#include <common>
#include <logdepthbuf_pars_fragment>
uniform float ditherScale;
uniform float dither;
varying vec3 vA;
varying vec3 vB;
void main() {
  #include <logdepthbuf_fragment>
  vec2 c = floor(gl_FragCoord.xy / ditherScale);
  float checker = mod(c.x + c.y, 2.0);
  vec3 col = dither > 0.5 ? mix(vA, vB, checker) : 0.5 * (vA + vB);
  gl_FragColor = vec4(col, 1.0);
}`;

export const uniforms = {
  layerBias: { value: 2e-4 },
  ditherScale: { value: 2 },
  dither: { value: 1 },
};

export const polyMaterial = new THREE.ShaderMaterial({
  vertexShader, fragmentShader, uniforms, side: THREE.DoubleSide,
});
export const lineMaterial = new THREE.ShaderMaterial({ vertexShader, fragmentShader, uniforms });

export type ColourFn = (c: number) => [THREE.Vector3Tuple, THREE.Vector3Tuple];

export function colourResolver(world: World): ColourFn {
  const pal = world.palette.map(([r, g, b]) => [r / 255, g / 255, b / 255] as THREE.Vector3Tuple);
  return (c) => {
    const [a, b] = world.colours[c] ?? [0, 0];
    return [pal[a], pal[b]];
  };
}

// Game frame (left-handed, z forward) -> three.js (right-handed): negate z.
const toThree = ([x, y, z]: number[]): THREE.Vector3Tuple => [x, y, -z];

function plane(pts: THREE.Vector3[]): { n: THREE.Vector3; d: number } {
  // Newell's method: robust for any planar polygon.
  const n = new THREE.Vector3();
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    n.x += (a.y - b.y) * (a.z + b.z);
    n.y += (a.z - b.z) * (a.x + b.x);
    n.z += (a.x - b.x) * (a.y + b.y);
  }
  n.normalize();
  return { n, d: n.dot(pts[0]) };
}

class Buffers {
  position: number[] = [];
  colorA: number[] = [];
  colorB: number[] = [];
  layer: number[] = [];

  push(p: THREE.Vector3Tuple, a: THREE.Vector3Tuple, b: THREE.Vector3Tuple, layer: number) {
    this.position.push(...p);
    this.colorA.push(...a);
    this.colorB.push(...b);
    this.layer.push(layer);
  }

  geometry(): THREE.BufferGeometry | null {
    if (!this.position.length) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(this.position, 3));
    g.setAttribute("colorA", new THREE.Float32BufferAttribute(this.colorA, 3));
    g.setAttribute("colorB", new THREE.Float32BufferAttribute(this.colorB, 3));
    g.setAttribute("layer", new THREE.Float32BufferAttribute(this.layer, 1));
    g.computeBoundingSphere();
    return g;
  }
}

export interface BuiltModel {
  polys: THREE.BufferGeometry | null;
  lines: THREE.BufferGeometry | null;
  minY: number;                      // lowest vertex, used to stand objects on the ground
}

export function buildModel(m: ModelData, colour: ColourFn, baseLayer = 1): BuiltModel {
  const V = m.vertices.map((v) => new THREE.Vector3(...toThree(v)));
  const used = new Set<number>();
  const polys = new Buffers();
  const planes: { n: THREE.Vector3; d: number; layer: number }[] = [];
  let maxLayer = baseLayer;
  for (const [idx, c] of m.polys) {
    if (idx.length < 3) continue;
    const pts = idx.map((i) => V[i]);
    const pl = plane(pts);
    let layer = baseLayer;
    for (const q of planes) {
      const dot = pl.n.dot(q.n);
      if (Math.abs(dot) > 0.999 && Math.abs(pl.d - Math.sign(dot) * q.d) < 1) layer = Math.max(layer, q.layer + 1);
    }
    planes.push({ ...pl, layer });
    maxLayer = Math.max(maxLayer, layer);
    const [ca, cb] = colour(c);
    for (let k = 1; k < idx.length - 1; k++) {
      for (const i of [idx[0], idx[k], idx[k + 1]]) {
        polys.push(V[i].toArray() as THREE.Vector3Tuple, ca, cb, layer);
        used.add(i);
      }
    }
  }
  const lines = new Buffers();
  for (const [a, b, c] of m.lines) {
    const [ca] = colour(c);
    lines.push(V[a].toArray() as THREE.Vector3Tuple, ca, ca, maxLayer + 1);
    lines.push(V[b].toArray() as THREE.Vector3Tuple, ca, ca, maxLayer + 1);
    used.add(a);
    used.add(b);
  }
  let minY = Infinity;
  for (const i of used) minY = Math.min(minY, V[i].y);
  return { polys: polys.geometry(), lines: lines.geometry(), minY: Number.isFinite(minY) ? minY : 0 };
}

export function modelObject(b: BuiltModel): THREE.Object3D {
  const g = new THREE.Group();
  if (b.polys) g.add(new THREE.Mesh(b.polys, polyMaterial));
  if (b.lines) g.add(new THREE.LineSegments(b.lines, lineMaterial));
  return g;
}

/** Caches built geometry per model index. */
export class ModelLibrary {
  private cache = new Map<number, BuiltModel | null>();
  readonly colour: ColourFn;

  constructor(private world: World) {
    this.colour = colourResolver(world);
  }

  get(index: number): BuiltModel | null {
    if (!this.cache.has(index)) {
      const m = this.world.models[String(index)];
      this.cache.set(index, m ? buildModel(m, this.colour) : null);
    }
    return this.cache.get(index)!;
  }
}
