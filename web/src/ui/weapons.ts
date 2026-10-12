// The Weapons tab: laser turret (8), flares (9), viewing drone (10), defence radar (11) and
// weapons status (12). Actions are the model's carrier weapon commands (stunits_carrier):
// flare, recon drone, SSM, defence drones launch/recall; aiming the turret and the drone uses
// the held controls (app/controls.ts). Counts come from the stores ($B76 items).
import type { Controls } from "../app/controls.ts";
import { h } from "./shell.ts";

export const WEAPON_SCREENS = new Set([8, 9, 10, 11, 12]);

const ITEMS = 0xb76, ITEM_SIZE = 0x1a;
/** Store items by index (docs/memory-map.md "UI panels"; units.md §5.3): 2 SSM, 6 defence
 * drones, 10 flares, 11 recon drone. The on-board count is the word at item +8 (the loadout
 * table's stock pointers point there). */
const STOCK = { ssm: 2, drones: 6, flares: 10, recon: 11 };

export class WeaponsPanel {
  readonly el: HTMLElement;
  private title: HTMLElement;
  private actions: HTMLElement;
  private counts: HTMLElement;
  private controls: Controls;
  private screen = -1;

  constructor(controls: Controls) {
    this.controls = controls;
    this.title = h("h2");
    this.counts = h("div", { class: "fields" });
    this.actions = h("div", { class: "actions" });
    this.el = h("section", { class: "panel vehicles compact-weapons" }, this.title, this.counts, this.actions);
  }

  private build(screen: number): void {
    const s = this.controls.state;
    const btn = (label: string, title: string, fn: () => unknown) => {
      const e = h("button", { title }, label);
      e.onclick = () => this.controls.run(fn);
      return e;
    };
    const rows: Record<number, HTMLElement[]> = {
      8: [
        btn("Fire", "Fire the laser (F or Enter, hold to repeat; arrows aim)", () => s.cmd_turret_fire()),
        btn("Zoom in", "", () => s.cmd_turret_zoom_in()),
        btn("Zoom out", "", () => s.cmd_turret_zoom_out()),
        btn("Centre", "Centre heading and elevation", () => {
          s.cmd_turret_centre_heading();
          s.cmd_turret_centre_elevation();
        }),
      ],
      9: [btn("Launch flare", "Decoy flare against incoming missiles", () => s.cmd_flare_launch())],
      10: [
        btn("Launch recon drone", "The viewing drone falls on its own once launched", () => s.cmd_recon_launch()),
        btn("Arm SSM", "Arm the Hammerhead, then click the target in the drone's picture", () => s.cmd_toggle_mouse_steer()),
      ],
      11: [
        btn("Launch drone", "Launch a defence drone (hold for more)", () => s.cmd_drones_launch()),
        btn("Recall drones", "", () => s.cmd_drones_recall()),
        btn("◀ Formation", "", () => s.cmd_drone_formation_prev()),
        btn("Formation ▶", "", () => s.cmd_drone_formation_next()),
        btn("Apply formation", "", () => s.cmd_drone_formation_apply()),
        btn("Reset stations", "", () => s.cmd_drone_stations_reset()),
      ],
      12: [],
    };
    this.actions.replaceChildren(...(rows[screen] ?? []));
  }

  update(ram: Uint8Array, screen: number): void {
    if (screen !== this.screen) {
      this.screen = screen;
      this.build(screen);
      const names: Record<number, string> = { 8: "Laser turret", 9: "Flares", 10: "Viewing drone", 11: "Defence radar", 12: "Weapons status" };
      this.title.textContent = names[screen] ?? "";
    }
    const count = (k: number) => (ram[ITEMS + ITEM_SIZE * k + 8] << 8) | ram[ITEMS + ITEM_SIZE * k + 9];
    const armed = screen === 10 && ram[0x1160] ? " · SSM ARMED: click the target" : "";
    const text = `SSM ${count(STOCK.ssm)} · Defence drones ${count(STOCK.drones)} · Flares ${count(STOCK.flares)} · Recon drones ${count(STOCK.recon)}${armed}`;
    if (this.counts.textContent !== text) this.counts.textContent = text;
  }
}
