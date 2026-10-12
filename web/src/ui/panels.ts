// Screen panels over the 3D view: the bridge HUD and the damage screen. They read the state
// through app/readout.ts and act only through the callbacks they're given (the model's commands).
import { carrier, islandCounts, systems } from "../app/readout.ts";
import { h } from "./shell.ts";

export const SYSTEM_NAMES = [
  "Engines", "Radar", "Communications", "Observation turret", "Laser cannon",
  "Flare launcher", "Surface-to-surface missiles", "Lift", "Automatic repair", "Superstructure",
];

/** Carrier controls offered on the bridge (app/controls.ts). */
export interface CarrierActions {
  hold(what: "faster" | "slower" | "left" | "right", on: boolean): void;
  stop(): void;
  centreRudder(): void;
  autopilot(): void;
}

const fmt = (n: number) => Math.round(n).toLocaleString("en");

export class BridgeHud {
  readonly el: HTMLElement;
  private fields: Record<string, HTMLElement> = {};
  private auto: HTMLButtonElement;

  constructor(names: string[], actions: CarrierActions) {
    void names;
    const field = (k: string, label: string) => {
      this.fields[k] = h("output");
      return h("div", { class: "field" }, h("label", {}, label), this.fields[k]);
    };
    const button = (label: string, title: string, fn: () => void) => {
      const b = h("button", { title }, label);
      b.onclick = fn;
      return b;
    };
    /** A button that acts while pressed (mouse or touch), like a held key. */
    const holdButton = (label: string, title: string, what: "faster" | "slower" | "left" | "right") => {
      const b = h("button", { title }, label);
      b.onpointerdown = (e) => {
        b.setPointerCapture(e.pointerId);
        actions.hold(what, true);
      };
      b.onpointerup = b.onpointercancel = () => actions.hold(what, false);
      return b;
    };
    this.auto = button("Autopilot", "Steer for the waypoint set on the map (Q)", () => actions.autopilot());
    this.el = h(
      "section", { class: "panel bridge" },
      h("div", { class: "fields" },
        field("speed", "Speed"), field("rudder", "Rudder"), field("heading", "Bearing"), field("fuel", "Fuel"),
        field("position", "Position"), field("island", "Island"), field("islands", "Islands")),
      h("div", { class: "controls" },
        h("div", { class: "group" },
          holdButton("▲", "Faster: hold (W / ↑)", "faster"),
          button("■", "Stop (X)", () => actions.stop()),
          holdButton("▼", "Slower: hold (S / ↓)", "slower")),
        h("div", { class: "group" },
          holdButton("◀", "Rudder left: hold (A / ←)", "left"),
          button("●", "Centre the rudder (E)", () => actions.centreRudder()),
          holdButton("▶", "Rudder right: hold (D / →)", "right")),
        this.auto),
    );
  }

  update(ram: Uint8Array, names: string[]): void {
    const c = carrier(ram), n = islandCounts(ram);
    const set = (k: string, v: string) => {
      if (this.fields[k].textContent !== v) this.fields[k].textContent = v;
    };
    set("speed", `${c.speed} → ${c.targetSpeed}`);
    set("rudder", c.rudder === 0 ? "0" : `${c.rudder < 0 ? "◀" : "▶"} ${Math.abs(c.rudder)}`);
    set("heading", String(Math.round(c.heading) % 360).padStart(3, "0") + "°");
    set("fuel", fmt(c.fuel));
    set("position", `${fmt(c.x / 512)}, ${fmt(c.z / 512)}`);
    set("island", c.island === null ? "—" : names[c.island]);
    set("islands", `${n.player} ours · ${n.enemy} enemy`);
    this.auto.classList.toggle("on", c.autopilot);
  }
}

export class DamagePanel {
  readonly el: HTMLElement;
  private rows: { bar: HTMLElement; value: HTMLElement; prio: HTMLButtonElement }[] = [];
  private classes: number[] = [];

  /** setPriority(system, class 0-2) changes a system's repair class (the priorities panel). */
  constructor(setPriority: (system: number, cls: number) => void) {
    const table = h("div", { class: "systems" });
    for (const [k, name] of SYSTEM_NAMES.entries()) {
      const bar = h("div", { class: "bar" }), value = h("output");
      const prio = h("button", { class: "prio", title: "Repair priority class: click to change" });
      prio.onclick = () => setPriority(k, ((this.classes[k] ?? 0) + 1) % 3);
      this.rows.push({ bar, value, prio });
      table.append(h("div", { class: "system" }, h("label", {}, name), h("div", { class: "meter" }, bar), value, prio));
    }
    this.el = h("section", { class: "panel damage" }, h("h2", {}, "Damage status"), table);
  }

  update(ram: Uint8Array): void {
    systems(ram).forEach((s, k) => {
      const r = this.rows[k], f = s.max ? s.current / s.max : 0;
      r.bar.style.width = `${Math.max(0, Math.min(1, f)) * 100}%`;
      r.bar.classList.toggle("low", f < 0.5);
      this.classes[k] = s.priority;
      const v = `${Math.round(f * 100)}%`, p = `P${s.priority}`;
      if (r.value.textContent !== v) r.value.textContent = v;
      if (r.prio.textContent !== p) r.prio.textContent = p;
    });
  }
}
