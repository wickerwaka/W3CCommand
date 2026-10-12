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
const s16 = (b: Uint8Array, a: number) => (u16(b, a) << 16) >> 16;

const OBJ0 = 0x5dcda;
const SCREEN = 0x718, VIEWPORT = 0x6fe, CAMERA = 0x9fa, VIEWED = 0x9fe, REAR_VIEW = 0x8f1;
const ANG = (a: number) => (a * 2 * Math.PI) / 0x10000;

/** The game's own camera on every 3D screen: where the screen's render routine puts its camera
 * object, and the projection of its viewport record. Spec: docs/memory-map.md, "Bridge view
 * camera and projection" and "Vehicle and weapon views"; tools/stview.py is the reference,
 * checked against each screen's render under Unicorn.
 *   0 bridge      carrier + formB(0, -100), y 240; the carrier's angles
 *   26 Manta deck carrier + formB(420, 1050), y 230; yaw carrier + [$DDE]
 *   19 dock       carrier + formB(-420, -1050), y 50; yaw carrier + [$DEA]
 *   9 flares      carrier + formB(0, -1000), y 300; pitch [$1158], yaw [$115A]
 *   8 turret      pivot (0, 320, -350) in the carrier frame, 30 out along the gun; pitch [$6CA],
 *                 yaw carrier + [$6CC], carrier roll; focal 256 * 2^[$6D2]
 *   24 Manta arming   the bridge camera, in the small viewport $475A ((32, 6)-(159, 69), focal 128)
 *   16 Walrus arming  carrier + formB(0, -1000) (the carrier's yaw), y 140; pitch and roll the
 *                 carrier's, yaw carrier + [$DC8] (swinging $6000..$A000: looking aft out of the
 *                 stern), viewport $475A. Object 0 is also type $53 there, whose model 83 is empty.
 *   10 drone      object 15 itself;  27 Manta: the Manta itself (rear view $8F1: yaw + $8000,
 *                 pitch and roll negated);  18 Walrus: Walrus y + 45, yaw + turret +$26 (rear
 *                 view: yaw + $8000, pitch negated). */
export class GameCamera implements ViewCamera {
  name = "Carrier bridge";
  frustum = { tanX: 128 / 256, tanY: 64 / 256, near: 64 };
  /** False when the screen shows no 3D picture (2D screens, no telemetry, ...). */
  live = true;
  /** The 3D window on the ST screen (320 x 200, inclusive corners), from the viewport record
   * [$6FE]: (32, 6)-(287, 133) full size, (32, 6)-(159, 69) on the arming screens 16/24 ($475A). */
  rect = { x0: 32, y0: 6, x1: 287, y1: 133 };
  private pos: Eye = { x: 0, y: 0, z: 0 };
  private rot = gameRotation(0, 0, 0);

  update(ram: Uint8Array): void {
    const screen = u16(ram, SCREEN), c = CARRIER;
    const cx = s32(ram, c + 0x0a), cz = s32(ram, c + 0x12);
    const cp = u16(ram, c + 4), cyaw = u16(ram, c + 6), croll = u16(ram, c + 8);
    /** carrier position + formB(carrier yaw)(x, z) */
    const local = (x: number, z: number, y: number): Eye => {
      const a = ANG(cyaw), co = Math.cos(a), si = Math.sin(a);
      return { x: cx + x * co + z * si, y, z: cz + z * co - x * si };
    };
    const obj = (a: number) => ({
      x: s32(ram, a + 0x0a), y: s32(ram, a + 0x0e), z: s32(ram, a + 0x12),
      p: u16(ram, a + 4), w: u16(ram, a + 6), r: u16(ram, a + 8),
    });
    let p = 0, w = 0, r = 0;
    this.live = true;
    switch (screen) {
      case 26:
        this.name = "Manta deck";
        this.pos = local(420, 1050, 230);
        w = cyaw + u16(ram, 0xdde);
        break;
      case 19:
        this.name = "Walrus dock";
        this.pos = local(-420, -1050, 50);
        w = cyaw + u16(ram, 0xdea);
        break;
      case 16:
        this.name = "Walrus hangar";
        this.pos = local(0, -1000, s32(ram, OBJ0 + 0x0e));    // y 140 (enter handler $13C94)
        [p, w, r] = [cp, cyaw + u16(ram, 0xdc8), croll];
        break;
      case 24:
        this.name = "Manta hangar";
        this.pos = local(0, -100, s32(ram, OBJ0 + 0x0e));     // y 240 (enter handler $13D82)
        [p, w, r] = [cp, cyaw, croll];
        break;
      case 9:
        this.name = "Flares";
        this.pos = local(0, -1000, 300);
        p = u16(ram, 0x1158);
        w = u16(ram, 0x115a);
        break;
      case 8: {
        this.name = "Laser turret";
        const e = ANG(u16(ram, 0x6ca)), h = ANG(u16(ram, 0x6cc));
        const u = 30 * Math.cos(e), v = 30 * Math.sin(e);
        this.pos = local(v * Math.sin(h), v * Math.cos(h) - 350, 320 + u);
        p = u16(ram, 0x6ca);
        w = cyaw + u16(ram, 0x6cc);
        r = croll;
        this.live = ram[0xb12] !== 0;            // observation turret system working
        break;
      }
      case 10:
      case 27: {
        this.name = screen === 10 ? "Viewing drone" : "Manta";
        const a = s32(ram, screen === 10 ? CAMERA : VIEWED) >>> 0;
        if (!a) {
          this.live = false;
          break;
        }
        const o = obj(a);
        this.pos = { x: o.x, y: o.y, z: o.z };
        [p, w, r] = [o.p, o.w, o.r];
        if (screen === 27 && ram[REAR_VIEW]) [p, w, r] = [-p, w + 0x8000, -r];
        break;
      }
      case 18: {
        this.name = "Walrus";
        const a = s32(ram, VIEWED) >>> 0;
        if (!a) {
          this.live = false;
          break;
        }
        const o = obj(a), bay = u16(ram, a + 0x1a) & 3;
        this.pos = { x: o.x, y: o.y + 45, z: o.z };
        [p, w, r] = [o.p, o.w + u16(ram, 0x570 + 0x34 * bay + 0x26), o.r];
        if (ram[REAR_VIEW]) [p, w] = [-p, w + 0x8000];
        break;
      }
      default:
        this.name = "Carrier bridge";
        this.pos = local(0, -100, CarrierCamera.EYE_Y);
        [p, w, r] = [cp, cyaw, croll];
        this.live = screen === 0;
    }
    this.rot = gameRotation(p & 0xffff, w & 0xffff, r & 0xffff);
    // the projection from the viewport record [$6FE]: x0 y0 x1 y1 shx shy cx cy ...
    const vp = s32(ram, VIEWPORT) >>> 0;
    if (vp) {
      const x0 = s16(ram, vp), y0 = s16(ram, vp + 2), x1 = s16(ram, vp + 4), y1 = s16(ram, vp + 6);
      let shx = s16(ram, vp + 8), shy = s16(ram, vp + 10);
      if (screen === 8) shx = shy = 8 + u16(ram, 0x6d2);
      if (x1 > x0 && y1 > y0 && shx > 0 && shx < 16) {
        this.frustum = { tanX: (x1 - x0 + 1) / 2 / 2 ** shx, tanY: (y1 - y0 + 1) / 2 / 2 ** shy, near: 64 };
        this.rect = { x0, y0, x1, y1 };
      }
    }
  }

  eye(): Eye {
    return this.pos;
  }

  orientation(out: THREE.Quaternion): void {
    toThreeQuaternion(this.rot, out);
  }
}

/** Constants of the bridge camera, kept for reference. */
export class CarrierCamera {
  static EYE_Y = 240;
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
