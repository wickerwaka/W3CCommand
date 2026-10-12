// The map: islands by owner, the supply network, the carriers and units, the carrier's
// waypoint. Drag to pan, wheel or pinch to zoom, click (tap) to set the carrier waypoint.
// World x is east, z north (the game's +z, heading 0).
import { carrier, ENEMY_CARRIER_OBJ, islands, links, unit, type IslandInfo } from "../app/readout.ts";

const OWNER_COLOURS = ["#9a9a8a", "#3fa9f5", "#e5533d"];   // neutral, player, enemy

export interface MapOptions {
  /** Called with a world position when the player picks a waypoint. */
  onWaypoint: (x: number, z: number) => void;
  /** Show the enemy carrier (the original only shows what radar sees; debug aid). */
  showEnemy: () => boolean;
}

export class MapView {
  readonly el: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  names: string[];
  private cx = 0;
  private cz = 0;
  private scale = 0;          // pixels per world unit (0 = fit on first draw)
  private drag: { x: number; y: number; moved: boolean } | null = null;
  private pointers = new Map<number, { x: number; y: number }>();
  private pinch = 0;
  private opts: MapOptions;

  constructor(names: string[], opts: MapOptions) {
    this.names = names;
    this.opts = opts;
    this.el = document.createElement("canvas");
    this.el.className = "map";
    this.ctx = this.el.getContext("2d")!;
    this.el.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.zoomAt(e.offsetX, e.offsetY, Math.exp(-e.deltaY * 0.0015));
    }, { passive: false });
    this.el.addEventListener("pointerdown", (e) => {
      this.el.setPointerCapture(e.pointerId);
      this.pointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      if (this.pointers.size === 1) this.drag = { x: e.offsetX, y: e.offsetY, moved: false };
      else {
        this.drag = null;
        this.pinch = this.pinchDistance();
      }
    });
    this.el.addEventListener("pointermove", (e) => {
      if (!this.pointers.has(e.pointerId)) return;
      this.pointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      if (this.pointers.size === 2) {
        const d = this.pinchDistance();
        const [a, b] = [...this.pointers.values()];
        if (this.pinch) this.zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, d / this.pinch);
        this.pinch = d;
      } else if (this.drag) {
        const dx = e.offsetX - this.drag.x, dy = e.offsetY - this.drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) this.drag.moved = true;
        if (this.drag.moved) {
          this.cx -= dx / this.scale;
          this.cz += dy / this.scale;
          this.drag.x = e.offsetX;
          this.drag.y = e.offsetY;
        }
      }
    });
    const up = (e: PointerEvent) => {
      if (this.drag && !this.drag.moved && this.pointers.size === 1) {
        const [x, z] = this.toWorld(e.offsetX, e.offsetY);
        this.opts.onWaypoint(Math.round(x), Math.round(z));
      }
      this.pointers.delete(e.pointerId);
      this.drag = null;
      this.pinch = 0;
    };
    this.el.addEventListener("pointerup", up);
    this.el.addEventListener("pointercancel", (e) => {
      this.pointers.delete(e.pointerId);
      this.drag = null;
    });
  }

  private pinchDistance(): number {
    const [a, b] = [...this.pointers.values()];
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  private zoomAt(px: number, py: number, f: number): void {
    const [x, z] = this.toWorld(px, py);
    this.scale = Math.min(0.05, Math.max(2e-5, this.scale * f));
    const [x2, z2] = this.toWorld(px, py);
    this.cx += x - x2;
    this.cz += z - z2;
  }

  private toWorld(px: number, py: number): [number, number] {
    const w = this.el.clientWidth, h = this.el.clientHeight;
    return [this.cx + (px - w / 2) / this.scale, this.cz - (py - h / 2) / this.scale];
  }

  private toScreen(x: number, z: number): [number, number] {
    const w = this.el.clientWidth, h = this.el.clientHeight;
    return [w / 2 + (x - this.cx) * this.scale, h / 2 - (z - this.cz) * this.scale];
  }

  private fit(isl: IslandInfo[]): void {
    let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
    for (const i of isl) {
      x0 = Math.min(x0, i.x); x1 = Math.max(x1, i.x);
      z0 = Math.min(z0, i.z); z1 = Math.max(z1, i.z);
    }
    this.cx = (x0 + x1) / 2;
    this.cz = (z0 + z1) / 2;
    this.scale = 0.9 * Math.min(this.el.clientWidth / (x1 - x0), this.el.clientHeight / (z1 - z0));
  }

  /** Centre on a world position. */
  centre(x: number, z: number): void {
    this.cx = x;
    this.cz = z;
  }

  draw(ram: Uint8Array): void {
    const dpr = Math.min(devicePixelRatio, 2);
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (!w || !h) return;
    if (this.el.width !== Math.round(w * dpr) || this.el.height !== Math.round(h * dpr)) {
      this.el.width = Math.round(w * dpr);
      this.el.height = Math.round(h * dpr);
    }
    const isl = islands(ram, this.names);
    if (!this.scale) this.fit(isl);
    const c = this.ctx;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.fillStyle = "#0b2a5a";
    c.fillRect(0, 0, w, h);

    c.strokeStyle = "rgba(255,255,255,0.18)";
    c.lineWidth = 1;
    c.beginPath();
    for (const [i, j] of links(ram)) {
      const [ax, ay] = this.toScreen(isl[i].x, isl[i].z), [bx, by] = this.toScreen(isl[j].x, isl[j].z);
      c.moveTo(ax, ay);
      c.lineTo(bx, by);
    }
    c.stroke();

    c.font = "11px system-ui, sans-serif";
    c.textAlign = "center";
    for (const i of isl) {
      const [x, y] = this.toScreen(i.x, i.z);
      const hw = Math.max(3, i.halfX * this.scale), hh = Math.max(3, i.halfZ * this.scale);
      c.fillStyle = OWNER_COLOURS[i.owner] ?? "#fff";
      c.fillRect(x - hw, y - hh, 2 * hw, 2 * hh);
      if (this.scale > 4e-4 || hw > 6) {
        c.fillStyle = "rgba(255,255,255,0.85)";
        c.fillText(i.name, x, y + hh + 12);
      }
    }

    const car = carrier(ram);
    const [wx, wy] = this.toScreen(car.waypoint.x, car.waypoint.z);
    const [px, py] = this.toScreen(car.x, car.z);
    c.strokeStyle = "rgba(255,220,90,0.8)";
    c.setLineDash([4, 4]);
    c.beginPath();
    c.moveTo(px, py);
    c.lineTo(wx, wy);
    c.stroke();
    c.setLineDash([]);
    c.beginPath();
    c.arc(wx, wy, 6, 0, 2 * Math.PI);
    c.stroke();

    for (let k = 2; k < 10; k++) {
      const u = unit(ram, k);
      if (u) this.marker(u.x, u.z, u.heading, k < 6 ? "#b8f0ff" : "#ffd27a", 5);
    }
    this.marker(car.x, car.z, (car.heading * 0x10000) / 360, "#ffe060", 9);
    if (this.opts.showEnemy()) {
      const e = unit(ram, (ENEMY_CARRIER_OBJ - 0x5dcda) / 0x140);
      if (e) this.marker(e.x, e.z, e.heading, "#ff6040", 9);
    }
  }

  /** A heading arrow: angle 0 points north (+z), $4000 east. */
  private marker(x: number, z: number, heading: number, colour: string, size: number): void {
    const [sx, sy] = this.toScreen(x, z);
    const a = (heading * 2 * Math.PI) / 0x10000;
    const c = this.ctx;
    c.save();
    c.translate(sx, sy);
    c.rotate(a);
    c.fillStyle = colour;
    c.strokeStyle = "#000";
    c.beginPath();
    c.moveTo(0, -size);
    c.lineTo(size * 0.6, size * 0.7);
    c.lineTo(-size * 0.6, size * 0.7);
    c.closePath();
    c.fill();
    c.stroke();
    c.restore();
  }
}
