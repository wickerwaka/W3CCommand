// Information screens, each showing what the original's screen shows (app/readout_screens.ts,
// checked against the original's own drawing; docs/units.md §10.7):
//   3 resources (stores on board, carrier fuel, stockpile transfers), 4 messages (the game's log,
//   with ages), 11 defence radar (drones around the carrier), 12 weapons status, 20/28 vehicle
//   status. Actions are added where the model has the commands.
import type { Controls } from "../app/controls.ts";
import {
  resourcesFuelTransfer, resourcesSubPage, defenceRadar, messageAgeText, messageLog, resourcesCarrierFuel, resourcesGate, resourcesStoresOnBoard,
  resourcesStoresTransfer, storeItemName, vehicleStatusPage, weaponsStatus, type Craft,
} from "../app/readout_screens.ts";
import { h } from "./shell.ts";

export const INFO_SCREENS = new Set([3, 4, 11, 12, 20, 28]);

/** Replace an element's children only when the content changed (cheap per-frame updates). */
function setRows(el: HTMLElement, key: { last?: string }, rows: () => (HTMLElement | string)[], sig: string): void {
  if (key.last === sig) return;
  key.last = sig;
  el.replaceChildren(...rows());
}

const yesNo = (b: boolean) => (b ? "operative" : "inoperative");

export class InfoPanel {
  readonly el: HTMLElement;
  private title = h("h2");
  private body = h("div", { class: "info" });
  private memo: { last?: string } = {};
  private screen = -1;
  private radar = document.createElementNS("http://www.w3.org/2000/svg", "svg");

  private controls: Controls;
  private actions = h("div", { class: "actions" });

  constructor(controls: Controls) {
    this.controls = controls;
    this.el = h("section", { class: "panel info-panel" }, this.title, this.actions, this.body);
    this.radar.setAttribute("viewBox", "0 0 256 128");
    this.radar.setAttribute("class", "radar");
  }

  update(ram: Uint8Array, screen: number): void {
    if (screen !== this.screen) {
      this.screen = screen;
      this.memo = {};
      this.actions.replaceChildren();
      const names: Record<number, string> = {
        3: "Resources", 4: "Messages", 11: "Defence radar", 12: "Weapons status", 20: "Walrus status", 28: "Manta status",
      };
      this.title.textContent = names[screen] ?? "";
    }
    if (screen === 4) this.messages(ram);
    else if (screen === 12) this.weapons(ram);
    else if (screen === 11) this.defence(ram);
    else if (screen === 3) this.resources(ram);
    else if (screen === 20 || screen === 28) this.status(ram, screen === 28 ? "manta" : "walrus");
  }

  private messages(ram: Uint8Array): void {
    const log = messageLog(ram);
    const sig = log.map((m) => `${m.time}${m.text}`).join("|") + (log[0]?.age ?? "");
    setRows(this.body, this.memo, () =>
      log.length ? log.slice().reverse().map((m) => h("div", { class: "msg" }, h("span", { class: "time" }, messageAgeText(m.age)), " ", m.text)) : ["No messages"], sig);
  }

  private weapons(ram: Uint8Array): void {
    const w = weaponsStatus(ram);
    const sys = (name: string, s: { operative: boolean; repair: number }) => `${name}: ${yesNo(s.operative)}, repair ${s.repair}%`;
    const lines = [
      sys("Laser", w.laser) + (w.laserOverheating ? " — overheating" : ""),
      sys("Observation turret", w.turret),
      sys("Flare launcher", w.flareLauncher) + ` — ${w.flaresInStores} in stores, ${w.flaresActive} active, ${w.flareState}`,
      sys("Missile launcher", w.missileLauncher) + ` — ${w.missilesInStores} Hammerheads`,
      `Viewing drones: ${w.viewersInStores} in stores${w.viewingDroneActive ? ", one active" : ""}`,
      `Defence drones: ${w.dronesInStores} in stores, ${w.dronesActive} active — ${w.droneState}`,
    ];
    setRows(this.body, this.memo, () => lines.map((l) => h("div", {}, l)), lines.join("|"));
  }

  private defence(ram: Uint8Array): void {
    const r = defenceRadar(ram);
    const sig = JSON.stringify(r);
    if (this.memo.last === sig) return;
    this.memo.last = sig;
    const svg = this.radar, ns = "http://www.w3.org/2000/svg";
    // the plan view at 1/60 scale, bow to the right, centred on the viewport centre (160, 70)
    const shape = (tag: string, attrs: Record<string, string | number>) => {
      const e = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
      return e;
    };
    const kids: Element[] = [shape("rect", { x: 0, y: 0, width: 256, height: 128, class: "radar-bg" }),
      shape("rect", { x: 128 - 1100 / 60, y: 64 - 6, width: 2200 / 60, height: 12, class: "radar-carrier" })];
    for (const d of r.drones) {
      if (d.stationMarker) kids.push(shape("circle", { cx: d.stationMarker.x - 32 + 4, cy: d.stationMarker.y - 6 + 8, r: 3, class: "radar-station" }));
      if (d.marker) kids.push(shape("circle", { cx: d.marker.x - 32 + 4, cy: d.marker.y - 6 + 8, r: 3, class: "radar-drone" }));
    }
    svg.replaceChildren(...kids);
    this.body.replaceChildren(svg, h("div", {}, r.radarWorking
      ? `${r.dronesActive} drones active, ${r.dronesInStores} in stores — ${r.droneState}`
      : "Radar inoperative"));
  }

  /** Screen 3's actions follow the original's sub-pages ([$D14]): 0 main (transfer, abort),
   * 4 the stores queue and 5 the fuel queue (move goods from the stockpile to the transfer). */
  private resourceActions(ram: Uint8Array, page: number): void {
    const s = this.controls.state;
    const btn = (label: string, title: string, fn: () => unknown) => {
      const e = h("button", { title }, label);
      e.onclick = () => this.controls.run(fn);
      return e;
    };
    const key = `res${page}`;
    if (this.actions.dataset.key === key) return;
    this.actions.dataset.key = key;
    void ram;
    if (page === 4)
      this.actions.replaceChildren(
        btn("▲", "Scroll", () => s.cmd_stores_queue_scroll_up()), btn("▼", "Scroll", () => s.cmd_stores_queue_scroll_down()),
        btn("Back", "", () => s.cmd_stores_queue_back()));
    else if (page === 5) this.actions.replaceChildren(btn("Back", "", () => s.cmd_fuel_queue_back()));
    else
      this.actions.replaceChildren(
        btn("Transfer to carrier", "Send the queued stores and fuel with the supply drone", () => s.cmd_resources_transfer()),
        btn("Abort resupply", "", () => s.cmd_resupply_abort()),
        btn("Queue stores…", "Choose stores to bring from the stockpile island", () => s.cmd_stores_queue_open()),
        btn("Queue fuel…", "Choose fuel to bring from the stockpile island", () => s.cmd_fuel_queue_open()));
  }

  private resources(ram: Uint8Array): void {
    const page = resourcesSubPage(ram);
    this.resourceActions(ram, page);
    if (page === 4 || page === 5) return this.resourceQueue(ram, page);
    const items = resourcesStoresOnBoard(ram), fuel = resourcesCarrierFuel(ram), gate = resourcesGate(ram);
    const transfer = resourcesStoresTransfer(ram);
    const sig = JSON.stringify([page, items.map((i) => i.onBoard), fuel, gate, transfer]);
    setRows(this.body, this.memo, () => [
      h("div", { class: "fuel" },
        ...([["Carrier fuel", fuel.ship], ["Aircraft fuel", fuel.aircraft], ["AAV fuel", fuel.aav]] as const).map(([l, g]) =>
          h("div", { class: "field" }, h("label", {}, l), h("output", {}, g.fuel.toLocaleString("en"))))),
      h("table", { class: "stores" },
        h("tr", {}, h("th", {}, "Item"), h("th", {}, "On board"), h("th", {}, "Limit")),
        ...items.map((i) => h("tr", {}, h("td", {}, i.name), h("td", {}, String(i.onBoard)), h("td", {}, String(i.capacity))))),
      h("p", { class: "hint" }, gate.transfersBlocked
        ? `Transfers: ${gate.transfersBlocked}`
        : `At the stockpile: ${transfer.rows.map((r) => `${storeItemName(ram, r.item)} ${r.stockpile}`).join(", ")}`),
    ], sig);
  }

  /** The queues: what's at the stockpile island and what's queued for the next transfer;
   * +/- move goods (the original's buttons repeat while held: click repeatedly). */
  private resourceQueue(ram: Uint8Array, page: number): void {
    const s = this.controls.state;
    const row = (label: string, stock: string, queued: string, minus: () => unknown, plus: () => unknown) => {
      const b = (t: string, fn: () => unknown) => {
        const e = h("button", {}, t);
        e.onclick = () => this.controls.run(fn);
        return e;
      };
      return h("tr", {}, h("td", {}, label), h("td", {}, stock), h("td", {}, b("−", minus)), h("td", {}, queued), h("td", {}, b("+", plus)));
    };
    const head = h("tr", {}, h("th", {}, "Item"), h("th", {}, "At stockpile"), h("th", {}), h("th", {}, "Queued"), h("th", {}));
    if (page === 4) {
      const t = resourcesStoresTransfer(ram);
      setRows(this.body, this.memo, () => [h("table", { class: "stores" }, head, ...t.rows.map((r, i) =>
        row(storeItemName(ram, r.item), String(r.stockpile), String(r.queued), () => s.cmd_stores_unqueue(i), () => s.cmd_stores_queue(i))))], JSON.stringify(t));
    } else {
      const f = resourcesFuelTransfer(ram);
      const names = ["Carrier fuel", "Aircraft fuel", "AAV fuel"];
      setRows(this.body, this.memo, () => [h("table", { class: "stores" }, head, ...names.map((n, k) =>
        row(n, f.stockpile[k].fuel.toLocaleString("en"), f.queued[k].fuel.toLocaleString("en"), () => s.cmd_fuel_unqueue(k), () => s.cmd_fuel_queue(k))))], JSON.stringify(f));
    }
  }

  private status(ram: Uint8Array, craft: Craft): void {
    const page = vehicleStatusPage(ram, craft);
    const sig = JSON.stringify(page);
    setRows(this.body, this.memo, () => page.map((v) => {
      const lines = [`${craft === "manta" ? "Manta" : "Walrus"} ${v.bay + 1}: ${v.state.toLowerCase()}`];
      if (v.state === "ACTIVE") {
        lines.push(`Position ${v.x}, ${v.z}${v.island ? ` · ${v.island}` : ""} · repair ${v.repairState}% · payload ${v.payloadWeight} kg`);
        const arms = [v.laser ? "laser" : "", v.laserShots ? `laser (${v.laserShots})` : "", v.missiles ? `missiles (${v.missiles})` : "", v.payload ?? ""].filter(Boolean);
        if (arms.length) lines.push(arms.join(" · "));
      }
      return h("div", { class: "status-block" }, ...lines.map((l) => h("div", {}, l)));
    }), sig);
  }
}
