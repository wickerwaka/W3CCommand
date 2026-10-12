// Model geometry -> three.js objects.
//
// Game frame: x right, y up, z forward (left-handed). three.js is right-handed with -z
// forward, so every game vector maps to three as (x, y, -z) (GAME_TO_THREE). Rotations
// conjugate the same way (see transforms.ts).
//
// The original has no depth buffer: it paints polygons back to front, BSP order within a model,
// and coplanar "decal" polygons (markings on hulls and decks) are simply painted later. With a
// depth buffer those z-fight, so each polygon carries its decal layer (models.ts) and the
// vertex shader pulls layer k slightly towards the camera.
import * as THREE from "three";
import type { Model } from "./models.ts";
import { colourPair, modelColour, paletteColour } from "./palette.ts";

/** The ST's dither: a two-colour entry of the $13CA table is drawn as a 2x2 checker of its two
 * palette colours. ditherCell.value = the checker cell in device pixels (about one ST pixel
 * at the window's size), or 0 to blend the two colours instead. */
export const ditherCell = { value: 0 };

/** Fraction of the view distance each decal layer is pulled towards the camera. */
const LAYER_PULL = 0.0015;

/** Decal pull plus the game's per-group visibility test ($9902): a primitive in a tested group
 * is drawn only when ((b - a) x (c - a)) . (eye - a) >= 0 in model space; testN is that normal
 * and testP the point a (both already in three's frame), tested = 0 for untested groups. */
function patchShader(m: THREE.Material): void {
  m.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float layer;\nattribute vec3 testN;\nattribute vec3 testP;\nattribute float tested;")
      .replace(
        "#include <project_vertex>",
        `vec4 mvPosition = modelViewMatrix * vec4( transformed, 1.0 );
         mvPosition.xyz *= 1.0 - layer * ${LAYER_PULL.toFixed(6)};
         gl_Position = projectionMatrix * mvPosition;
         if (tested > 0.5) {
           vec3 eyeM = (inverse(modelMatrix) * vec4(cameraPosition, 1.0)).xyz;
           if (dot(testN, eyeM - testP) < 0.0) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
         }`,
      );
  };
}

export function polyMaterial(): THREE.Material {
  const m = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide });
  patchShader(m);
  const vertex = m.onBeforeCompile;
  m.onBeforeCompile = (shader, renderer) => {
    vertex(shader, renderer);
    shader.uniforms.ditherCell = ditherCell;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec3 color2;\nvarying vec3 vColor2;")
      .replace("#include <color_vertex>", "#include <color_vertex>\nvColor2 = color2;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vColor2;\nuniform float ditherCell;")
      .replace(
        "#include <color_fragment>",
        `if (ditherCell > 0.0) {
           vec2 c = floor(gl_FragCoord.xy / ditherCell);
           diffuseColor.rgb = mod(c.x + c.y, 2.0) < 1.0 ? vColor.rgb : vColor2;
         } else diffuseColor.rgb = mix(vColor.rgb, vColor2, 0.5);`,
      );
  };
  return m;
}

export function lineMaterial(): THREE.LineBasicMaterial {
  const m = new THREE.LineBasicMaterial({ vertexColors: true });
  patchShader(m);
  return m;
}

/** A model as a group of meshes (polygons, lines, discs). Shared by every object drawing it. */
export interface ModelMeshes {
  polys: THREE.BufferGeometry | null;
  lines: THREE.BufferGeometry | null;
  discs: { centre: THREE.Vector3; radius: number; colour: THREE.Color }[];
}

const toThree = (v: [number, number, number]) => new THREE.Vector3(v[0], v[1], -v[2]);

/** Lines are drawn after the polygons of their group: pull them like a first decal layer. */
const LINE_LAYER = 1;

/** The group test as three-frame (normal, point), or null. The cross product is taken in the
 * game frame, then both vectors are mirrored to three's (the dot product is unchanged). */
function groupTest(model: Model, test: [number, number, number] | undefined): [THREE.Vector3, THREE.Vector3] | null {
  if (!test) return null;
  const [a, b, c] = test.map((i) => model.vertices[i]);
  if (!a || !b || !c) return null;
  const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
  return [new THREE.Vector3(n[0], n[1], -n[2]), toThree(a)];
}

function pushTest(t: [THREE.Vector3, THREE.Vector3] | null, n: number[], p: number[], on: number[], count: number): void {
  for (let k = 0; k < count; k++) {
    if (t) {
      n.push(t[0].x, t[0].y, t[0].z);
      p.push(t[1].x, t[1].y, t[1].z);
    } else {
      n.push(0, 0, 0);
      p.push(0, 0, 0);
    }
    on.push(t ? 1 : 0);
  }
}

function setTestAttributes(g: THREE.BufferGeometry, n: number[], p: number[], on: number[]): void {
  g.setAttribute("testN", new THREE.Float32BufferAttribute(n, 3));
  g.setAttribute("testP", new THREE.Float32BufferAttribute(p, 3));
  g.setAttribute("tested", new THREE.Float32BufferAttribute(on, 1));
}

export function buildModelMeshes(ram: Uint8Array, model: Model): ModelMeshes {
  const verts = model.vertices.map(toThree);
  let polys: THREE.BufferGeometry | null = null;
  if (model.polys.length) {
    const pos: number[] = [], col: number[] = [], col2: number[] = [], layer: number[] = [];
    const tn: number[] = [], tp: number[] = [], ton: number[] = [];
    for (const p of model.polys) {
      const [ia, ib] = colourPair(ram, p.colour);
      const c = paletteColour(ram, ia), c2 = paletteColour(ram, ib);
      const pts = p.idx.map((i) => verts[i]).filter((v) => v !== undefined);
      if (pts.length < 3) continue;
      pushTest(groupTest(model, p.test), tn, tp, ton, 3 * (pts.length - 2));
      for (let k = 1; k + 1 < pts.length; k++) {
        for (const v of [pts[0], pts[k], pts[k + 1]]) {
          pos.push(v.x, v.y, v.z);
          col.push(c.r, c.g, c.b);
          col2.push(c2.r, c2.g, c2.b);
          layer.push(p.layer);
        }
      }
    }
    if (pos.length) {
      polys = new THREE.BufferGeometry();
      polys.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      polys.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
      polys.setAttribute("color2", new THREE.Float32BufferAttribute(col2, 3));
      polys.setAttribute("layer", new THREE.Float32BufferAttribute(layer, 1));
      setTestAttributes(polys, tn, tp, ton);
      polys.computeBoundingSphere();
    }
  }
  let lines: THREE.BufferGeometry | null = null;
  if (model.lines.length) {
    const pos: number[] = [], col: number[] = [], layer: number[] = [];
    const tn: number[] = [], tp: number[] = [], ton: number[] = [];
    for (const l of model.lines) {
      const a = verts[l.v0], b = verts[l.v1];
      if (!a || !b || l.conditional) continue;
      const c = modelColour(ram, l.colour);
      pos.push(a.x, a.y, a.z, b.x, b.y, b.z);
      col.push(c.r, c.g, c.b, c.r, c.g, c.b);
      layer.push(LINE_LAYER, LINE_LAYER);
      pushTest(groupTest(model, l.test), tn, tp, ton, 2);
    }
    if (pos.length) {
      lines = new THREE.BufferGeometry();
      lines.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      lines.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
      lines.setAttribute("layer", new THREE.Float32BufferAttribute(layer, 1));
      setTestAttributes(lines, tn, tp, ton);
      lines.computeBoundingSphere();
    }
  }
  const discs = model.discs
    .filter((d) => verts[d.v])
    .map((d) => ({ centre: verts[d.v], radius: d.radius, colour: modelColour(ram, d.colour) }));
  return { polys, lines, discs };
}

export function disposeModelMeshes(m: ModelMeshes): void {
  m.polys?.dispose();
  m.lines?.dispose();
}
