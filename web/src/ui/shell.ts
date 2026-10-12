// The app's chrome: tab bar (the original's four screen groups), the status/clock area, the
// message line and log, the game menu, and per-screen panels. Plain DOM; screens hook in
// through `Screen` objects. Nothing here touches game state directly.
export interface Tab {
  id: string;
  label: string;
  screens: { id: number; label: string; ready: boolean }[];
}

export const TABS: Tab[] = [
  {
    id: "carrier", label: "Carrier", screens: [
      { id: 0, label: "Bridge", ready: true },
      { id: 2, label: "Map", ready: true },
      { id: 1, label: "Damage", ready: true },
      { id: 3, label: "Resources", ready: false },
    ],
  },
  {
    id: "weapons", label: "Weapons", screens: [
      { id: 8, label: "Laser turret", ready: false },
      { id: 9, label: "Flares", ready: false },
      { id: 10, label: "Viewing drone", ready: false },
      { id: 11, label: "Defence radar", ready: false },
    ],
  },
  {
    id: "walrus", label: "Walrus", screens: [
      { id: 19, label: "Dock", ready: false },
      { id: 16, label: "Arming", ready: false },
      { id: 18, label: "Control", ready: false },
      { id: 17, label: "Map", ready: false },
    ],
  },
  {
    id: "manta", label: "Manta", screens: [
      { id: 26, label: "Deck", ready: false },
      { id: 24, label: "Arming", ready: false },
      { id: 27, label: "Control", ready: false },
      { id: 25, label: "Map", ready: false },
    ],
  },
];

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}, ...kids: (Node | string)[]) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else el.setAttribute(k, v);
  }
  el.append(...kids);
  return el;
};
export { h };

export interface ShellHandlers {
  screen(id: number): void;
  playPause(): void;
  speed(factor: number): void;
  menu(): void;
}

export class Shell {
  readonly root: HTMLElement;
  readonly content: HTMLElement;
  readonly hud: HTMLElement;
  private tabs = new Map<string, HTMLButtonElement>();
  private subbar: HTMLElement;
  private clock: HTMLElement;
  private play: HTMLButtonElement;
  private messageLine: HTMLElement;
  private log: HTMLElement;
  private notice: HTMLElement;
  private current = -1;
  private handlers: ShellHandlers;

  constructor(root: HTMLElement, handlers: ShellHandlers) {
    this.root = root;
    this.handlers = handlers;
    const tabbar = h("nav", { class: "tabs" });
    for (const t of TABS) {
      const b = h("button", { class: "tab" }, t.label);
      b.onclick = () => this.handlers.screen(t.screens[0].id);
      this.tabs.set(t.id, b);
      tabbar.append(b);
    }
    this.subbar = h("nav", { class: "subtabs" });
    this.clock = h("span", { class: "clock" });
    this.play = h("button", { title: "Pause / run (P or Space)" }, "Run");
    this.play.onclick = () => this.handlers.playPause();
    const speed = h("select", { title: "Game speed" });
    for (const [v, l] of [[0.5, "½×"], [1, "1×"], [2, "2×"], [4, "4×"], [16, "16×"]] as const) {
      const o = new Option(l, String(v));
      if (v === 1) o.selected = true;
      speed.append(o);
    }
    speed.onchange = () => this.handlers.speed(Number(speed.value));
    const menu = h("button", { title: "Game menu (Esc)" }, "☰");
    menu.onclick = () => this.handlers.menu();
    const top = h("header", { class: "top" }, tabbar, h("div", { class: "spacer" }), this.clock, this.play, speed, menu);
    this.content = h("main", { class: "content" });
    this.hud = h("div", { class: "hud" });
    this.messageLine = h("div", { class: "message" });
    this.log = h("ol", { class: "log", hidden: "" });
    this.messageLine.onclick = () => (this.log.hidden = !this.log.hidden);
    this.notice = h("div", { class: "notice", hidden: "" });
    root.append(top, this.subbar, this.content, this.hud, this.log, h("footer", { class: "bottom" }, this.messageLine), this.notice);
  }

  /** Highlight the tab and sub-screen; returns false if the screen isn't available yet. */
  showScreen(id: number): void {
    this.current = id;
    const tab = TABS.find((t) => t.screens.some((s) => s.id === id));
    for (const [k, b] of this.tabs) b.classList.toggle("active", k === tab?.id);
    this.subbar.replaceChildren(
      ...(tab?.screens ?? []).map((s) => {
        const b = h("button", { class: s.id === this.current ? "active" : "" }, s.label);
        b.disabled = !s.ready;
        if (!s.ready) b.title = "Not available yet";
        b.onclick = () => this.handlers.screen(s.id);
        return b;
      }),
    );
  }

  setStatus(clock: string, running: boolean, stopped: boolean): void {
    if (this.clock.textContent !== clock) this.clock.textContent = clock;
    const label = running ? "Pause" : "Run";
    if (this.play.textContent !== label) this.play.textContent = label;
    this.play.disabled = stopped;
  }

  message(text: string, time: string): void {
    this.messageLine.textContent = text;
    this.log.prepend(h("li", {}, h("span", { class: "time" }, time), " ", text));
    while (this.log.childElementCount > 100) this.log.lastElementChild?.remove();
  }

  clearMessages(): void {
    this.messageLine.textContent = "";
    this.log.replaceChildren();
  }

  /** A banner for problems (unported code reached, load errors); null hides it. */
  setNotice(text: string | null): void {
    this.notice.hidden = !text;
    if (text && this.notice.textContent !== text) this.notice.textContent = text;
  }
}
