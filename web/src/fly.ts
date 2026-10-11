// Free flight over the sea: drag to look, WASD/arrows to move, R/F (or E/Q) for altitude,
// Shift to go faster. Speed scales with altitude so both low passes and long hops work.
// On touch screens, one finger looks and two fingers move forward.

import * as THREE from "three";

export class FlyControls {
  enabled = false;
  yaw = 0;
  pitch = -0.15;
  minHeight = 20;
  private keys = new Set<string>();
  private drag: { x: number; y: number } | null = null;
  private touches = 0;

  constructor(private camera: THREE.PerspectiveCamera, private dom: HTMLElement) {
    addEventListener("keydown", (e) => {
      if (this.enabled && !(e.target instanceof HTMLInputElement)) this.keys.add(e.code);
    });
    addEventListener("keyup", (e) => this.keys.delete(e.code));
    addEventListener("blur", () => this.keys.clear());
    dom.addEventListener("pointerdown", (e) => {
      if (!this.enabled) return;
      this.touches++;
      this.drag = { x: e.clientX, y: e.clientY };
      dom.setPointerCapture(e.pointerId);
    });
    dom.addEventListener("pointermove", (e) => {
      if (!this.enabled || !this.drag) return;
      this.yaw -= (e.clientX - this.drag.x) * 0.004;
      this.pitch = THREE.MathUtils.clamp(this.pitch - (e.clientY - this.drag.y) * 0.004, -1.5, 1.5);
      this.drag = { x: e.clientX, y: e.clientY };
    });
    const up = () => {
      this.touches = Math.max(0, this.touches - 1);
      if (!this.touches) this.drag = null;
    };
    dom.addEventListener("pointerup", up);
    dom.addEventListener("pointercancel", up);
  }

  /** Take over from the current camera orientation. */
  syncFromCamera() {
    const e = new THREE.Euler().setFromQuaternion(this.camera.quaternion, "YXZ");
    this.yaw = e.y;
    this.pitch = e.x;
  }

  update(dt: number) {
    if (!this.enabled) return;
    const k = this.keys;
    const alt = Math.max(this.minHeight, this.camera.position.y);
    const speed = Math.max(300, alt * 1.2) * (k.has("ShiftLeft") || k.has("ShiftRight") ? 8 : 1);
    const fwd = (k.has("KeyW") || k.has("ArrowUp") || this.touches >= 2 ? 1 : 0) - (k.has("KeyS") || k.has("ArrowDown") ? 1 : 0);
    const side = (k.has("KeyD") || k.has("ArrowRight") ? 1 : 0) - (k.has("KeyA") || k.has("ArrowLeft") ? 1 : 0);
    const lift = (k.has("KeyR") || k.has("KeyE") ? 1 : 0) - (k.has("KeyF") || k.has("KeyQ") ? 1 : 0);

    this.camera.quaternion.setFromEuler(new THREE.Euler(this.pitch, this.yaw, 0, "YXZ"));
    const dir = new THREE.Vector3(0, 0, -1).applyQuaternion(this.camera.quaternion);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion);
    const p = this.camera.position;
    p.addScaledVector(dir, fwd * speed * dt);
    p.addScaledVector(right, side * speed * dt);
    p.y += lift * speed * dt;
    p.y = Math.max(this.minHeight, p.y);
  }
}
