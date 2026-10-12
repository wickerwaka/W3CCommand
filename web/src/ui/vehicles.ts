// The Manta and Walrus screens: the four bays (status, fuel, damage, weapons) and each screen's
// actions, all through the model's commands (the original's panel handlers; docs/units.md §10.4):
//   deck 26 / dock 19       select, to deck / launch, take off, land / dock, to hangar,
//                           repair, replace, prepare, refuel, defuel, abort landing / docking
//   arming 24 / 16          select, fit and remove loadouts per slot, to deck (Manta), +/-
//   control 27 / 18         select, weapon, autopilot, centre, rear view, sight, instruments,
//                           fire (held keys steer: app/controls.ts)
//   map 25 / 17             select (the map itself sets the waypoint)
import type { Controls } from "../app/controls.ts";
import { STATUS_NAMES, vehicle, type VehicleKind } from "../app/readout.ts";
import { h } from "./shell.ts";

export const VEHICLE_SCREEN_KIND: Record<number, VehicleKind> = {
  24: "manta", 25: "manta", 26: "manta", 27: "manta", 16: "walrus", 17: "walrus", 18: "walrus", 19: "walrus",
};
export const VEHICLE_SCREENS = new Set(Object.keys(VEHICLE_SCREEN_KIND).map(Number));

/** Loadout codes (docs/units.md §6.2). */
export const LOADOUTS: Record<number, string> = {
  1: "Assassin ×1", 2: "Assassin ×2", 3: "Assassin ×3", 4: "Quaker bomb", 5: "Quasar laser",
  6: "Avatar laser", 7: "Harbinger missiles", 8: "Virus bomb", 9: "ACCB", 10: "Comms pod", 11: "Fuel pod",
};
const MANTA_SLOTS = ["Fuselage", "Left wing", "Right wing"];
const MANTA_CODES = [[1, 2, 3, 4, 5, 10], [1, 2], [1, 2]];
// $11ACE: slot 0 (+$26) takes payloads, slot 1 (+$28) weapons; Manta wings take Assassins (<= 2)
const WALRUS_SLOTS = ["Payload", "Weapon"];
const WALRUS_CODES = [[8, 9, 11], [6, 7]];
const WEAPONS = ["Laser", "Missile", "Payload"];

type S = Controls["state"];

interface Card {
  root: HTMLElement;
  status: HTMLElement;
  fuel: HTMLElement;
  health: HTMLElement;
  detail: HTMLElement;
}

export class VehiclePanel {
  readonly el: HTMLElement;
  private title: HTMLElement;
  private cards: Card[] = [];
  private actions: HTMLElement;
  private slots: HTMLElement;
  private controls: Controls;
  private built = "";

  constructor(controls: Controls) {
    this.controls = controls;
    this.title = h("h2");
    const grid = h("div", { class: "bays" });
    for (let k = 0; k < 4; k++) {
      const meter = () => {
        const bar = h("div", { class: "bar" });
        return [h("div", { class: "meter" }, bar), bar] as const;
      };
      const [fm, fuel] = meter(), [hm, health] = meter();
      const status = h("output", { class: "status" }), detail = h("div", { class: "detail" });
      const root = h("button", { class: "bay", title: "Select" },
        h("div", { class: "bay-head" }, h("strong", {}, String(k + 1)), status),
        h("label", {}, "Fuel"), fm, h("label", {}, "Damage"), hm, detail);
      root.onclick = () => this.select(k);
      this.cards.push({ root, status, fuel, health, detail });
      grid.append(root);
    }
    this.actions = h("div", { class: "actions" });
    this.slots = h("div", { class: "slots" });
    this.el = h("section", { class: "panel vehicles" }, this.title, grid, this.slots, this.actions);
  }

  private screen = -1;

  private get s(): S {
    return this.controls.state;
  }

  private act(fn: () => unknown): void {
    this.controls.run(fn);
  }

  private select(bay: number): void {
    const s = this.s;
    const fn: Record<number, () => unknown> = {
      26: () => s.cmd_manta_deck_select(bay), 27: () => s.cmd_manta_select(bay), 24: () => s.cmd_manta_arming_select(bay),
      25: () => s.cmd_manta_map_select(bay), 19: () => s.cmd_walrus_dock_select(bay), 18: () => s.cmd_walrus_select(bay),
      16: () => s.cmd_walrus_arming_select(bay), 17: () => s.cmd_walrus_map_select(bay),
    };
    if (fn[this.screen]) this.act(fn[this.screen]);
  }

  /** The bay the screen works on (its own selection variable). */
  private selected(): number {
    const ui = this.s.vehicle_ui() as Record<string, number>;
    const key: Record<number, string> = {
      26: "manta_deck", 24: "manta_arming", 27: "manta", 25: "manta",
      19: "walrus_dock", 16: "walrus_arming", 18: "walrus", 17: "walrus",
    };
    return (ui[key[this.screen]] ?? 0) & 3;
  }

  private buildActions(): void {
    const s = this.s, b = () => this.selected();
    const btn = (label: string, title: string, fn: () => unknown) => {
      const e = h("button", { title }, label);
      e.onclick = () => this.act(fn);
      return e;
    };
    /** Acts every pass while pressed, like a held key (throttle, fire). */
    const hold = (label: string, title: string, what: "faster" | "slower" | "fire") => {
      const e = h("button", { title }, label);
      e.onpointerdown = (ev) => {
        e.setPointerCapture(ev.pointerId);
        this.controls.hold(what, true);
      };
      e.onpointerup = e.onpointercancel = () => this.controls.hold(what, false);
      return e;
    };
    const rows: Record<number, HTMLElement[]> = {
      26: [
        btn("To deck", "Send the selected Manta up the lift", () => s.cmd_manta_to_deck(b())),
        btn("Take off", "Launch the Manta on the deck", () => s.cmd_manta_take_off()),
        btn("Land", "Bring the selected Manta home", () => s.cmd_manta_land(b())),
        btn("Abort landing", "", () => s.cmd_manta_abort_landing()),
        btn("To hangar", "Take the deck Manta down", () => s.cmd_manta_to_hangar()),
        btn("Repair", "", () => s.cmd_manta_repair(b())),
        btn("Replace", "Replace a destroyed Manta from stores", () => s.cmd_manta_replace(b())),
        btn("Prepare", "Flight preparations", () => s.cmd_manta_prepare(b())),
        btn("Refuel", "", () => s.cmd_manta_refuel(b())),
        btn("Defuel", "", () => s.cmd_manta_defuel(b())),
      ],
      19: [
        btn("Launch", "Launch the selected Walrus", () => s.cmd_walrus_launch(b())),
        btn("Dock", "Bring the selected Walrus home", () => s.cmd_walrus_dock(b())),
        btn("Abort docking", "", () => s.cmd_walrus_abort_docking()),
        btn("Repair", "", () => s.cmd_walrus_repair(b())),
        btn("Replace", "Replace a destroyed Walrus from stores", () => s.cmd_walrus_replace(b())),
        btn("Prepare", "", () => s.cmd_walrus_prepare(b())),
        btn("Refuel", "", () => s.cmd_walrus_refuel(b())),
        btn("Defuel", "", () => s.cmd_walrus_defuel(b())),
      ],
      27: [
        hold("▲", "Throttle up (W; hold)", "faster"), hold("▼", "Throttle down (S; hold)", "slower"),
        ...WEAPONS.map((w, k) => btn(w, `Select the ${w.toLowerCase()} (${k + 1})`, () => s.cmd_manta_weapon(k))),
        hold("Fire", "Fire (F or Enter; hold to repeat). Drag on the view to steer", "fire"),
        btn("Autopilot", "Fly to the waypoint (Q)", () => s.cmd_manta_autopilot()),
        btn("Centre", "Centre rudder and pitch (E)", () => {
          s.cmd_manta_centre_rudder();
          s.cmd_manta_centre_pitch();
        }),
        btn("Rear view", "", () => s.cmd_manta_rear_view()),
        btn("Sight", "", () => s.cmd_manta_sight()),
        btn("Runway take-off", "Take off from an island runway", () => s.cmd_manta_runway_take_off()),
      ],
      18: [
        hold("▲", "Throttle up (W; hold)", "faster"), hold("▼", "Throttle down (S; hold)", "slower"),
        ...WEAPONS.map((w, k) => btn(w, `Select the ${w.toLowerCase()}`, () => s.cmd_walrus_weapon(k))),
        hold("Fire", "Fire (F or Enter; hold to repeat). Drag on the view to steer", "fire"),
        btn("Autopilot", "Drive to the waypoint (Q)", () => s.cmd_walrus_autopilot()),
        btn("Centre", "Centre the rudder (E)", () => s.cmd_walrus_centre_rudder()),
        btn("Turret mode", "Steering turns the turret", () => s.cmd_walrus_turret_mode()),
        btn("Rear view", "", () => s.cmd_walrus_rear_view()),
        btn("Sight", "", () => s.cmd_walrus_sight()),
      ],
      24: [btn("To deck", "Send the armed Manta up the lift", () => s.cmd_manta_arming_to_deck())],
      16: [
        btn("−", "Fewer Harbingers / previous ACCB type", () => s.cmd_walrus_loadout_minus()),
        btn("+", "More Harbingers / next ACCB type", () => s.cmd_walrus_loadout_plus()),
      ],
    };
    this.actions.replaceChildren(...(rows[this.screen] ?? []));
    this.slots.replaceChildren();
    if (this.screen === 24 || this.screen === 16) {
      const manta = this.screen === 24;
      (manta ? MANTA_SLOTS : WALRUS_SLOTS).forEach((name, slot) => {
        const sel = h("select", { title: `${name} loadout` }) as HTMLSelectElement;
        sel.append(new Option("— empty —", "0"), ...(manta ? MANTA_CODES : WALRUS_CODES)[slot].map((c) => new Option(LOADOUTS[c], String(c))));
        sel.onchange = () => {
          const code = Number(sel.value);
          this.act(() => manta
            ? (code ? s.cmd_manta_fit(slot, code) : s.cmd_manta_unfit(slot))
            : (code ? s.cmd_walrus_fit(slot, code) : s.cmd_walrus_unfit(slot)));
        };
        this.slots.append(h("label", { class: "slot" }, name, sel));
      });
    }
  }

  update(ram: Uint8Array, screen: number): void {
    const kind = VEHICLE_SCREEN_KIND[screen];
    if (!kind) return;
    if (this.screen !== screen || this.built !== `${screen}`) {
      this.screen = screen;
      this.built = `${screen}`;
      this.buildActions();
    }
    const names: Record<number, string> = {
      26: "Manta deck", 27: "Manta control", 24: "Manta arming", 25: "Manta map",
      19: "Walrus dock", 18: "Walrus control", 16: "Walrus arming", 17: "Walrus map",
    };
    if (this.title.textContent !== names[screen]) this.title.textContent = names[screen];
    this.el.classList.toggle("compact", screen === 27 || screen === 18);
    const sel = this.selected();
    this.cards.forEach((c, k) => {
      const v = vehicle(ram, kind, k);
      const st = STATUS_NAMES[v.status] ?? `State ${v.status}`;
      if (c.status.textContent !== st) c.status.textContent = st;
      c.fuel.style.width = `${v.fuel * 100}%`;
      c.health.style.width = `${v.health * 100}%`;
      c.health.classList.toggle("low", v.health < 0.5);
      const out = v.status === 8;
      const detail = out
        ? `${WEAPONS[v.weapon] ?? "—"} · speed ${v.speed}${v.automatic ? " · auto" : ""}${v.telemetry ? ` · telemetry ${v.telemetry === 1 ? "weak" : "lost"}` : ""}`
        : v.status === 0xff ? "" : this.hangarLoadout(ram, kind, k);
      if (c.detail.textContent !== detail) c.detail.textContent = detail;
      c.root.classList.toggle("viewed", k === sel);
      c.root.classList.toggle("lost", v.status === 0xff);
    });
    // arming: show the selected vehicle's slot codes in the pickers
    if (screen === 24 || screen === 16) {
      const rec = (screen === 24 ? 0x496 : 0x570) + 0x34 * sel;
      const offs = screen === 24 ? [0x0c, 0x0e, 0x10] : [0x26, 0x28];
      this.slots.querySelectorAll("select").forEach((e, i) => {
        const code = String((ram[rec + offs[i]] << 8) | ram[rec + offs[i] + 1]);
        if (document.activeElement !== e && (e as HTMLSelectElement).value !== code) (e as HTMLSelectElement).value = code;
      });
    }
  }

  /** In the hangar the weapon fields hold the loadout codes (docs/units.md §6.1). */
  private hangarLoadout(ram: Uint8Array, kind: VehicleKind, bay: number): string {
    const rec = (kind === "manta" ? 0x496 : 0x570) + 0x34 * bay;
    const offs = kind === "manta" ? [0x0c, 0x0e, 0x10] : [0x26, 0x28];
    const items = offs.map((o) => (ram[rec + o] << 8) | ram[rec + o + 1]).filter((c) => LOADOUTS[c]).map((c) => LOADOUTS[c]);
    return items.join(", ") || "No loadout";
  }
}
