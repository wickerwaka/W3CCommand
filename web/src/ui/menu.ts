// The game menu: new games, save and load slots, emulator states, export, view options.
import { listSaves, SLOTS, type Slot } from "../app/store.ts";
import { h } from "./shell.ts";

export interface MenuHandlers {
  newGame(strategy: boolean): void;
  save(slot: Slot): void;
  load(slot: Slot): void;
  openFile(file: File): void;
  openLocal?(name: string): void;          // dev server captures
  localStates?: string[];
  exportState(): void;
  canSave(): boolean;
  options: { label: string; get(): boolean; set(v: boolean): void }[];
}

export class Menu {
  readonly el: HTMLDialogElement;
  private body: HTMLElement;
  private handlers: MenuHandlers;

  constructor(handlers: MenuHandlers) {
    this.handlers = handlers;
    this.body = h("div", { class: "menu-body" });
    const close = h("button", { class: "close", title: "Close (Esc)" }, "✕");
    close.onclick = () => this.el.close();
    this.el = h("dialog", { class: "menu" }, close, h("h1", {}, "Carrier Command"), this.body);
    this.el.addEventListener("click", (e) => {
      if (e.target === this.el) this.el.close();      // backdrop
    });
  }

  async open(): Promise<void> {
    await this.render();
    if (!this.el.open) this.el.showModal();
  }

  private async render(): Promise<void> {
    const hd = this.handlers;
    const act = (label: string, fn: () => void, title = "") => {
      const b = h("button", { title }, label);
      b.onclick = () => {
        this.el.close();
        fn();
      };
      return b;
    };
    const saves = await listSaves();
    const when = (t?: number) => (t ? new Date(t).toLocaleString() : "");
    const slotLabel = (s: Slot) => (s === "auto" ? "Autosave" : s === "last" ? "Last loaded file" : `Slot ${s}`);

    const loadRows = ([...SLOTS, "last"] as Slot[]).map((s) => {
      const info = saves.get(s);
      const b = act(info ? `${slotLabel(s)} — ${info.name}` : `${slotLabel(s)} — empty`, () => hd.load(s), when(info?.savedAt));
      b.disabled = !info;
      return b;
    });
    const saveRows = SLOTS.filter((s) => s !== "auto").map((s) => {
      const info = saves.get(s);
      const b = act(`Save to slot ${s}${info ? ` (replaces ${info.name})` : ""}`, () => hd.save(s));
      b.disabled = !hd.canSave();
      return b;
    });

    const file = h("input", { type: "file", accept: ".bin,.sav,.gz", hidden: "" }) as HTMLInputElement;
    file.onchange = () => {
      const f = file.files?.[0];
      if (f) {
        this.el.close();
        hd.openFile(f);
      }
      file.value = "";
    };
    const pick = h("button", { title: "A 512 KB RAM image or a Hatari memory snapshot" }, "Load emulator state…");
    pick.onclick = () => file.click();

    const local: HTMLElement[] = [];
    if (hd.localStates?.length && hd.openLocal) {
      const sel = h("select") as HTMLSelectElement;
      sel.append(new Option("Local captures (emu/out)…", ""), ...hd.localStates.map((n) => new Option(n, n)));
      sel.onchange = () => {
        if (!sel.value) return;
        this.el.close();
        hd.openLocal!(sel.value);
      };
      local.push(sel);
    }

    const exp = act("Export state", () => hd.exportState(), "Save the RAM image; Hatari loads it with loadbin <file> 0");
    exp.disabled = !hd.canSave();

    const opts = hd.options.map((o) => {
      const box = h("input", { type: "checkbox" }) as HTMLInputElement;
      box.checked = o.get();
      box.onchange = () => o.set(box.checked);
      return h("label", { class: "option" }, box, o.label);
    });

    this.body.replaceChildren(
      h("section", {}, h("h2", {}, "New game"),
        act("Strategy game", () => hd.newGame(true), "Take the archipelago, starting from your base island VULCAN"),
        act("Action game", () => hd.newGame(false), "Start at sea near an enemy island")),
      h("section", {}, h("h2", {}, "Save"), ...saveRows),
      h("section", {}, h("h2", {}, "Load"), ...loadRows, pick, file, ...local, exp),
      h("section", {}, h("h2", {}, "Options"), ...opts),
    );
  }
}
