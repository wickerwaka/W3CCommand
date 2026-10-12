// Viewpoints. A camera produces an eye position (game units, doubles) and a three.js
// orientation; the world is drawn relative to the eye (world_view.ts).
import * as THREE from "three";
import { CARRIER } from "../sim/units_core.ts";
import type { Eye } from "./world_view.ts";
import { gameRotation, toThreeQuaternion } from "./transforms.ts";

export interface ViewCamera {
  readonly name: string;
  /** The view frustum: tangents of the half angles across and up, and the near plane. The
   * window shows at least this much (wider or taller to fill it). */
  readonly frustum: { tanX: number; tanY: number; near: number };
  update(ram: Uint8Array, dt: number): void;
  eye(): Eye;
  orientation(out: THREE.Quaternion): void;
}

const s32 = (b: Uint8Array, a: number) => (b[a] << 24) | (b[a + 1] << 16) | (b[a + 2] << 8) | b[a + 3];
const u16 = (b: Uint8Array, a: number) => (b[a] << 8) | b[a + 1];

/** The bridge view (screen 0), as the game sets up its camera, object 0:
 * - entering the screen ($13C24) puts the eye at world y = 240 (hulls sit at 160);
 * - every frame ($1460A) it takes the carrier's pitch, yaw and roll, and x/z 100 units behind
 *   the carrier's origin along its heading;
 * - projection ($5912, viewport $4744): 256 x 128 pixels, focal length 256, near plane 64.
 * See docs/memory-map.md, "Bridge view camera and projection". */
export class CarrierCamera implements ViewCamera {
  readonly name = "Carrier bridge";
  readonly frustum = { tanX: 128 / 256, tanY: 64 / 256, near: 64 };
  static EYE_Y = 240;
  static BEHIND = 100;
  private pos: Eye = { x: 0, y: 0, z: 0 };
  private rot = gameRotation(0, 0, 0);
  yaw = 0;

  update(ram: Uint8Array): void {
    const o = CARRIER;
    this.yaw = u16(ram, o + 6);
    this.rot = gameRotation(u16(ram, o + 4), this.yaw, u16(ram, o + 8));
    const a = (this.yaw * 2 * Math.PI) / 0x10000;
    this.pos = {
      x: s32(ram, o + 0x0a) - CarrierCamera.BEHIND * Math.sin(a),
      y: CarrierCamera.EYE_Y,
      z: s32(ram, o + 0x12) - CarrierCamera.BEHIND * Math.cos(a),
    };
  }

  eye(): Eye {
    return this.pos;
  }

  orientation(out: THREE.Quaternion): void {
    toThreeQuaternion(this.rot, out);
  }
}

/** A free-flying camera: drag to look, WASD / arrows to move, R/F (or Q/E) up and down, Shift
 * for speed, wheel to change speed. Touch: drag to look, two fingers to move forward/back. */
export class FreeCamera implements ViewCamera {
  readonly name = "Free camera";
  readonly frustum = { tanX: Math.tan((35 * Math.PI) / 180), tanY: Math.tan((25 * Math.PI) / 180), near: 5 };
  enabled = false;
  speed = 4000;                 // game units per second
  private pos: Eye = { x: 0, y: 2000, z: 0 };
  private yaw = 0;              // radians, 0 = facing game +z
  private pitch = -0.2;
  private keys = new Set<string>();
  private drag: { x: number; y: number } | null = null;
  private pinch: number | null = null;
  private pinchMove = 0;

  constructor(dom: HTMLElement) {
    addEventListener("keydown", (e) => {
      if (this.enabled && !(e.target instanceof HTMLInputElement)) this.keys.add(e.code);
    });
    addEventListener("keyup", (e) => this.keys.delete(e.code));
    addEventListener("blur", () => this.keys.clear());
    dom.addEventListener("pointerdown", (e) => {
      if (!this.enabled || e.pointerType === "touch") return;
      this.drag = { x: e.clientX, y: e.clientY };
      dom.setPointerCapture(e.pointerId);
    });
    dom.addEventListener("pointermove", (e) => {
      if (!this.drag) return;
      this.look(e.clientX - this.drag.x, e.clientY - this.drag.y);
      this.drag = { x: e.clientX, y: e.clientY };
    });
    dom.addEventListener("pointerup", () => (this.drag = null));
    dom.addEventListener(
      "wheel",
      (e) => {
        if (!this.enabled) return;
        e.preventDefault();
        this.speed = Math.min(2e6, Math.max(100, this.speed * Math.exp(-e.deltaY * 0.002)));
      },
      { passive: false },
    );
    dom.addEventListener("touchstart", (e) => this.touch(e), { passive: false });
    dom.addEventListener("touchmove", (e) => this.touch(e), { passive: false });
    dom.addEventListener("touchend", () => {
      this.drag = null;
      this.pinch = null;
      this.pinchMove = 0;
    });
  }

  private touch(e: TouchEvent): void {
    if (!this.enabled) return;
    e.preventDefault();
    if (e.touches.length === 1) {
      const t = e.touches[0];
      if (this.drag) this.look(t.clientX - this.drag.x, t.clientY - this.drag.y);
      this.drag = { x: t.clientX, y: t.clientY };
    } else if (e.touches.length === 2) {
      this.drag = null;
      const [a, b] = [e.touches[0], e.touches[1]];
      const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      if (this.pinch !== null) this.pinchMove = (d - this.pinch) * 0.02;
      this.pinch = d;
    }
  }

  private look(dx: number, dy: number): void {
    this.yaw += dx * 0.004;
    this.pitch = Math.max(-1.55, Math.min(1.55, this.pitch - dy * 0.004));
  }

  /** Start from another camera's view. */
  placeAt(eye: Eye, q: THREE.Quaternion): void {
    this.pos = { ...eye };
    const dir = new THREE.Vector3(0, 0, -1).applyQuaternion(q);   // three forward = game +z
    this.yaw = Math.atan2(dir.x, -dir.z);
    this.pitch = Math.asin(Math.max(-1, Math.min(1, dir.y)));
  }

  update(_ram: Uint8Array, dt: number): void {
    if (!this.enabled) return;
    const k = this.keys;
    const fast = k.has("ShiftLeft") || k.has("ShiftRight") ? 5 : 1;
    const step = this.speed * fast * dt;
    let f = (k.has("KeyW") || k.has("ArrowUp") ? 1 : 0) - (k.has("KeyS") || k.has("ArrowDown") ? 1 : 0);
    const r = (k.has("KeyD") || k.has("ArrowRight") ? 1 : 0) - (k.has("KeyA") || k.has("ArrowLeft") ? 1 : 0);
    const u = (k.has("KeyR") || k.has("KeyE") ? 1 : 0) - (k.has("KeyF") || k.has("KeyQ") ? 1 : 0);
    f += this.pinchMove;
    this.pinchMove *= 0.8;
    const cp = Math.cos(this.pitch);
    // forward in game coordinates: heading yaw turns +z towards +x
    const fx = Math.sin(this.yaw) * cp, fy = Math.sin(this.pitch), fz = Math.cos(this.yaw) * cp;
    const rx = Math.cos(this.yaw), rz = -Math.sin(this.yaw);
    this.pos.x += (fx * f + rx * r) * step;
    this.pos.y += (fy * f + u) * step;
    this.pos.z += (fz * f + rz * r) * step;
  }

  eye(): Eye {
    return this.pos;
  }

  orientation(out: THREE.Quaternion): void {
    out.setFromEuler(new THREE.Euler(this.pitch, -this.yaw, 0, "YXZ"));
  }
}
