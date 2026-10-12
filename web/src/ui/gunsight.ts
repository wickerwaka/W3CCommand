// The control screens' gunsight (docs/memory-map.md, "Vehicle and weapon views", screen 27):
// fixed brackets at game pixels (115,25)-(204,114) and a cross at the viewport centre plus the
// sight offset ($1162/$1164; for missiles (rudder >> 3, -pitch >> 3), for the laser (0, 0), none
// for payloads), which changes shape while a missile is locked ($116C). Game pixels are mapped
// through the viewport's projection into our (wider) view, so the sight covers the same angles.
const SIGHT_X = 0x1162, SIGHT_Y = 0x1164, LOCK_TIMER = 0x116c, VIEWPORT = 0x6fe;
const MANTA_RECS = 0x496, WALRUS_RECS = 0x570, REC = 0x34;

const SVG = "http://www.w3.org/2000/svg";

export class Gunsight {
  readonly el: SVGSVGElement;
  private brackets: SVGPathElement;
  private cross: SVGPathElement;

  constructor() {
    this.el = document.createElementNS(SVG, "svg");
    this.el.setAttribute("class", "gunsight");
    this.brackets = document.createElementNS(SVG, "path");
    this.cross = document.createElementNS(SVG, "path");
    this.el.append(this.brackets, this.cross);
  }

  /** fov: the three.js vertical field of view (degrees) and aspect of the 3D view. */
  update(ram: Uint8Array, screen: number, fov: number, aspect: number): void {
    const show = screen === 27 || screen === 18;
    this.el.style.display = show ? "" : "none";
    if (!show) return;
    const w = this.el.clientWidth, h = this.el.clientHeight;
    const u16 = (a: number) => (ram[a] << 8) | ram[a + 1];
    const s16 = (a: number) => (u16(a) << 16) >> 16;
    const vp = ((ram[VIEWPORT] << 24) | (ram[VIEWPORT + 1] << 16) | (ram[VIEWPORT + 2] << 8) | ram[VIEWPORT + 3]) >>> 0;
    const shx = vp ? s16(vp + 8) : 8, shy = vp ? s16(vp + 10) : 8;
    const cx = vp ? s16(vp + 12) : 160, cy = vp ? s16(vp + 14) : 70;
    const tanY = Math.tan((fov * Math.PI) / 360), tanX = tanY * aspect;
    /** game screen pixel -> our CSS pixel */
    const at = (px: number, py: number): [number, number] => [
      (w / 2) * (1 + (px - cx) / 2 ** shx / tanX),
      (h / 2) * (1 + (py - cy) / 2 ** shy / tanY),
    ];
    const [x0, y0] = at(115, 25), [x1, y1] = at(205, 115);
    const l = Math.min(x1 - x0, y1 - y0) * 0.12;
    this.brackets.setAttribute("d",
      `M${x0},${y0 + l}V${y0}H${x0 + l} M${x1 - l},${y0}H${x1}V${y0 + l} ` +
      `M${x0},${y1 - l}V${y1}H${x0 + l} M${x1 - l},${y1}H${x1}V${y1 - l}`);
    // the selected weapon decides the cross: Manta +$1A, Walrus +$18 (0 laser, 1 missile)
    const viewed = ((ram[0x9fe] << 24) | (ram[0x9ff] << 16) | (ram[0xa00] << 8) | ram[0xa01]) >>> 0;
    const bay = viewed ? u16(viewed + 0x1a) & 3 : 0;
    const weapon = screen === 27 ? u16(MANTA_RECS + REC * bay + 0x1a) : u16(WALRUS_RECS + REC * bay + 0x18);
    if (weapon > 1) {
      this.cross.setAttribute("d", "");
      return;
    }
    const [px, py] = at(cx + s16(SIGHT_X), cy + s16(SIGHT_Y));
    const s = l * 0.6, locked = u16(LOCK_TIMER) > 0;
    this.cross.setAttribute("d", locked
      ? `M${px - s},${py - s}L${px + s},${py + s}M${px - s},${py + s}L${px + s},${py - s}`
      : `M${px - s},${py}H${px + s}M${px},${py - s}V${py + s}`);
  }
}
