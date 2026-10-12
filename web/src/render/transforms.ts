// Game orientation -> three.js rotation.
//
// Angles are 16-bit ($10000 = 360 degrees). An object's model -> world rotation is
// Yaw(+$06) . Pitch(+$04) . Roll(+$08) (roll first), as the game's renderer applies it
// (docs/memory-map.md, "Bridge view camera and projection"; tools/stview.py):
//   roll:  x' = x cos r - y sin r,  y' = y cos r + x sin r   (positive roll lifts +x)
//   pitch: y' = y cos p - z sin p,  z' = z cos p + y sin p   ($4000 points down)
//   yaw:   x' = x cos w + z sin w,  z' = z cos w - x sin w   (0 faces +z, $4000 faces +x)
// The camera uses the same angles; world -> camera is the exact inverse.
import * as THREE from "three";

export const ANGLE = (2 * Math.PI) / 0x10000;

/** The game rotation (model -> world, in the game's left-handed frame) as a row-major 3x3. */
export function gameRotation(pitch: number, yaw: number, roll: number): number[] {
  const cy = Math.cos(yaw * ANGLE), sy = Math.sin(yaw * ANGLE);
  const cp = Math.cos(pitch * ANGLE), sp = Math.sin(pitch * ANGLE);
  const cr = Math.cos(roll * ANGLE), sr = Math.sin(roll * ANGLE);
  const Y = [cy, 0, sy, 0, 1, 0, -sy, 0, cy];
  const P = [1, 0, 0, 0, cp, -sp, 0, sp, cp];
  const R = [cr, -sr, 0, sr, cr, 0, 0, 0, 1];
  return mul3(Y, mul3(P, R));
}

function mul3(a: number[], b: number[]): number[] {
  const r = new Array<number>(9).fill(0);
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++) for (let k = 0; k < 3; k++) r[3 * i + j] += a[3 * i + k] * b[3 * k + j];
  return r;
}

/** A game rotation as a three.js quaternion: conjugated by the z flip S = diag(1, 1, -1). */
export function toThreeQuaternion(m: number[], out = new THREE.Quaternion()): THREE.Quaternion {
  // S m S flips the sign of the elements that mix z with x or y.
  const e = [m[0], m[1], -m[2], m[3], m[4], -m[5], -m[6], -m[7], m[8]];
  const mat = new THREE.Matrix4().set(e[0], e[1], e[2], 0, e[3], e[4], e[5], 0, e[6], e[7], e[8], 0, 0, 0, 0, 1);
  return out.setFromRotationMatrix(mat);
}
