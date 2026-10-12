// Draws the model-list pictures (render/diagram_scene.ts is the spec): the damage screen's
// carrier diagram (screen 1) and the title's turning carrier (screen 33), into a rectangle of
// the shared WebGL canvas (scissored viewport, like main.ts renderInset).
//
// The scene is built in camera space: the three.js camera sits at the origin looking down -z
// (game +z) with an off-centre frustum equal to the game viewport's projection, so the picture
// matches the ST's pixel for pixel when the rectangle has the viewport's shape (it is
// letterboxed otherwise). Line models (the wireframe 74 and the overlays 75-82) are drawn as
// thick lines, one ST pixel wide, in the picture's palette (screen 1: $136A with the status
// colour patched), painted in the game's order without depth (the original has no depth
// buffer). Polygon models (the title's carrier) use the world view's meshes and depth.
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { MODEL_DRAW_LINES, MODEL_DRAW_LINES_NEAR, MODEL_DRAW_WIRE, type Model } from "./models.ts";
import { buildModelMeshes, disposeModelMeshes, lineMaterial, polyMaterial, type ModelMeshes } from "./meshes.ts";
import { toThreeQuaternion } from "./transforms.ts";
import { diagramScene, itemLines, titleStep, type DiagramScene } from "./diagram_scene.ts";

export { titleStep };

/** A rectangle of the canvas in CSS pixels, top-left origin (getBoundingClientRect() of a
 * placeholder element, when the canvas fills the window). */
export interface CanvasRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

const LINE_MODELS = new Set([MODEL_DRAW_WIRE, MODEL_DRAW_LINES, MODEL_DRAW_LINES_NEAR]);
const MAX_SEGMENTS = 512;

function stColour(word: number): THREE.Color {
  const ch = (s: number) => ((word >> s) & 7) / 7;
  return new THREE.Color().setRGB(ch(8), ch(4), ch(0), THREE.SRGBColorSpace);
}

export class ModelListView {
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera();
  private lines: LineSegments2;
  private lineGeo = new LineSegmentsGeometry();
  private lineMat = new LineMaterial({ vertexColors: true, linewidth: 1, depthTest: false, depthWrite: false });
  private polyMat = polyMaterial();
  private polyLineMat = lineMaterial();
  private meshes = new Map<string, { meshes: ModelMeshes; group: THREE.Group }>();
  private positions = new Float32Array(MAX_SEGMENTS * 6);
  private colours = new Float32Array(MAX_SEGMENTS * 6);
  private clear = new THREE.Color();

  constructor() {
    this.lines = new LineSegments2(this.lineGeo, this.lineMat);
    this.lines.frustumCulled = false;
    this.lines.renderOrder = 1000;
    this.scene.add(this.lines);
    this.camera.near = 16;
    this.camera.far = 1e6;
  }

  dispose(): void {
    for (const m of this.meshes.values()) disposeModelMeshes(m.meshes);
    this.meshes.clear();
    this.lineGeo.dispose();
    this.lineMat.dispose();
    this.polyMat.dispose();
    this.polyLineMat.dispose();
  }

  /** Draws screen 1's or 33's picture from the RAM image into rect (CSS pixels of the
   * renderer's canvas). The rect is cleared to palette colour 0 and the picture letterboxed to
   * the game viewport's shape. Returns false (nothing drawn) when the screen shows no picture
   * (the repair-priorities panel on 1, a menu on 33). Leaves the viewport, scissor and clear
   * colour as it found them. */
  render(renderer: THREE.WebGLRenderer, ram: Uint8Array, screen: number, rect: CanvasRect): boolean {
    const scene = diagramScene(ram, screen);
    if (!scene || rect.w < 2 || rect.h < 2) return false;
    const v = scene.viewport;
    const vw = v.x1 - v.x0 + 1, vh = v.y1 - v.y0 + 1;
    // the largest rectangle of the viewport's shape inside rect, centred
    const s = Math.min(rect.w / vw, rect.h / vh);
    const w = vw * s, h = vh * s, x = rect.x + (rect.w - w) / 2, y = rect.y + (rect.h - h) / 2;
    this.build(scene, ram, s * renderer.getPixelRatio());
    this.project(scene);

    const size = renderer.getSize(new THREE.Vector2());
    const oldViewport = renderer.getViewport(new THREE.Vector4());
    const oldScissor = renderer.getScissor(new THREE.Vector4());
    const oldScissorTest = renderer.getScissorTest();
    const oldClear = renderer.getClearColor(this.clear.clone());
    const oldAlpha = renderer.getClearAlpha();
    const flip = (yTop: number, hh: number) => size.y - yTop - hh; // WebGL's origin is bottom-left
    renderer.setScissorTest(true);
    renderer.setScissor(rect.x, flip(rect.y, rect.h), rect.w, rect.h);
    renderer.setViewport(rect.x, flip(rect.y, rect.h), rect.w, rect.h);
    renderer.setClearColor(stColour(scene.palette[0]), 1);
    renderer.clear(true, true, false);
    renderer.setScissor(x, flip(y, h), w, h);
    renderer.setViewport(x, flip(y, h), w, h);
    const dpr = renderer.getPixelRatio();
    this.lineMat.resolution.set(w * dpr, h * dpr);
    renderer.render(this.scene, this.camera);
    renderer.setViewport(oldViewport);
    renderer.setScissor(oldScissor);
    renderer.setScissorTest(oldScissorTest);
    renderer.setClearColor(oldClear, oldAlpha);
    return true;
  }

  /** The game's projection as an off-centre frustum: pixel columns x0..x1 and rows y0..y1
   * (inclusive) at X/Z = (px - cx) / 2^shx and Y/Z = (cy - py) / 2^shy. */
  private project(scene: DiagramScene): void {
    const v = scene.viewport, n = this.camera.near;
    const left = ((v.x0 - v.cx) / 2 ** v.shx) * n, right = ((v.x1 + 1 - v.cx) / 2 ** v.shx) * n;
    const top = ((v.cy - v.y0) / 2 ** v.shy) * n, bottom = ((v.cy - v.y1 - 1) / 2 ** v.shy) * n;
    this.camera.projectionMatrix.makePerspective(left, right, top, bottom, n, this.camera.far);
    this.camera.projectionMatrixInverse.copy(this.camera.projectionMatrix).invert();
    this.camera.position.set(0, 0, 0);
    this.camera.quaternion.identity();
  }

  /** Rebuilds the scene for this frame: line models into the shared thick-line geometry, polygon
   * models as cached mesh groups placed in camera space. stPixel = device pixels per ST pixel. */
  private build(scene: DiagramScene, ram: Uint8Array, stPixel: number): void {
    const colour = scene.palette.map(stColour);
    let n = 0;
    const used = new Set<string>();
    scene.items.forEach((item, order) => {
      if (LINE_MODELS.has(item.decoded.handler)) {
        for (const l of itemLines(scene, item)) {
          if (n >= MAX_SEGMENTS) break;
          this.positions.set([l.a[0], l.a[1], -l.a[2], l.b[0], l.b[1], -l.b[2]], 6 * n);
          const c = colour[l.colour & 15];
          this.colours.set([c.r, c.g, c.b, c.r, c.g, c.b], 6 * n);
          n++;
        }
        return;
      }
      // polygon models: the decoded geometry is cached by the state it depends on
      let m = this.meshes.get(item.key);
      if (!m) {
        m = this.makeMeshes(ram, item.decoded);
        this.meshes.set(item.key, m);
        this.scene.add(m.group);
      }
      used.add(item.key);
      m.group.visible = true;
      m.group.renderOrder = order;
      m.group.position.set(item.origin[0], item.origin[1], -item.origin[2]);
      toThreeQuaternion(item.rot, m.group.quaternion);
    });
    for (const [k, m] of this.meshes)
      if (!used.has(k)) {
        if (this.meshes.size > 8) {
          this.scene.remove(m.group);
          disposeModelMeshes(m.meshes);
          this.meshes.delete(k);
        } else m.group.visible = false;
      }
    this.lineGeo.setPositions(this.positions.subarray(0, Math.max(n, 1) * 6));
    this.lineGeo.setColors(this.colours.subarray(0, Math.max(n, 1) * 6));
    this.lines.visible = n > 0;
    this.lineMat.linewidth = Math.max(1, stPixel);
  }

  /** A polygon model's meshes (palette $132A, the title's) as a group. */
  private makeMeshes(ram: Uint8Array, model: Model): { meshes: ModelMeshes; group: THREE.Group } {
    const meshes = buildModelMeshes(ram, model);
    const group = new THREE.Group();
    if (meshes.polys) group.add(new THREE.Mesh(meshes.polys, this.polyMat));
    if (meshes.lines) group.add(new THREE.LineSegments(meshes.lines, this.polyLineMat));
    for (const d of meshes.discs) {
      const sphere = new THREE.Mesh(new THREE.SphereGeometry(Math.max(d.radius, 1), 12, 8), new THREE.MeshBasicMaterial({ color: d.colour }));
      sphere.position.copy(d.centre);
      group.add(sphere);
    }
    return { meshes, group };
  }
}
