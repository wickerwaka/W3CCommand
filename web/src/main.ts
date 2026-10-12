// Carrier Command in the browser. Starts new games from the title-screen RAM image
// (public/data/title.bin, `npm run data`), or loads states exported from the emulator; runs the
// simulation model (src/sim) on the original's clock; draws the world (src/render) and a modern
// UI (src/ui) that acts only through the model's own commands.
import * as THREE from "three";
import "./style.css";
import { Controls } from "./app/controls.ts";
import { Game, SCREENS } from "./app/game.ts";
import { islandNames } from "./app/readout.ts";
import { readStateFile, writeStateFile } from "./app/state_file.ts";
import { loadState, saveState, type Slot } from "./app/store.ts";
import { CarrierCamera, FreeCamera, type ViewCamera } from "./render/cameras.ts";
import { WorldView } from "./render/world_view.ts";
import { MapView } from "./ui/map.ts";
import { Menu } from "./ui/menu.ts";
import { BridgeHud, DamagePanel } from "./ui/panels.ts";
import { Shell } from "./ui/shell.ts";

const canvas = document.getElementById("view") as HTMLCanvasElement;
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, logarithmicDepthBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const camera = new THREE.PerspectiveCamera(50, 1, 5, 3e6);
const carrierCam = new CarrierCamera();
const freeCam = new FreeCamera(canvas);

const AUTOSAVE_SECONDS = 30;

let title: Uint8Array | null = null;
let game: Game | null = null;
let view: WorldView | null = null;
let names: string[] = [];
let freeCamera = false;
let showEnemy = false;
let sinceAutosave = 0;
let notice: string | null = null;

// ---- UI --------------------------------------------------------------------------------
const shell = new Shell(document.getElementById("ui")!, {
  screen: (id) => setScreen(id),
  playPause: () => togglePause(),
  speed: (f) => game && (game.runner.speed = f),
  menu: () => void menu.open(),
});

const controls = new Controls();
let bridge = new BridgeHud(names, controls);
const damage = new DamagePanel((k, cls) => controls.repairPriority(k, cls));
const map = new MapView(names, {
  onWaypoint: (x, z) => setWaypoint(x, z),
  showEnemy: () => showEnemy,
});

const localStates: string[] = [];
const menu = new Menu({
  newGame: (strategy) => newGame(strategy),
  save: (slot) => void save(slot),
  load: (slot) => void loadSlot(slot),
  openFile: (f) => void f.arrayBuffer().then((b) => openState(f.name, new Uint8Array(b))),
  openLocal: import.meta.env.DEV ? (n) => void openLocal(n) : undefined,
  localStates,
  exportState: () => exportState(),
  canSave: () => !!game,
  options: [
    { label: "Free camera (C)", get: () => freeCamera, set: (v) => setFreeCamera(v) },
    { label: "Show the enemy carrier on the map", get: () => showEnemy, set: (v) => (showEnemy = v) },
  ],
});
document.body.append(menu.el);

function setNotice(text: string | null): void {
  notice = text;
  shell.setNotice(text);
}

// ---- sessions ----------------------------------------------------------------------------
function start(g: Game): void {
  view?.dispose();
  game = g;
  view = new WorldView(g.ram);
  names = islandNames(g.ram);
  map.names = names;
  bridge = new BridgeHud(names, controls);
  controls.attach(g);
  shell.clearMessages();
  setNotice(g.runner.stopReason);
  sinceAutosave = 0;
  carrierCam.update(g.ram);
  showScreen();
}

function newGame(strategy: boolean): void {
  if (!title) {
    setNotice("No title-screen image: run `npm run data` (needs Hatari) to create public/data/title.bin");
    return;
  }
  try {
    start(Game.create(title, strategy));
    game!.runner.play();
  } catch (e) {
    setNotice((e as Error).message);
  }
}

async function openState(name: string, data: Uint8Array): Promise<void> {
  try {
    const ram = await readStateFile(data);
    start(Game.load(ram, name));
    void saveState("last", { name, ram });
  } catch (e) {
    setNotice(`${name}: ${(e as Error).message}`);
  }
}

async function openLocal(name: string): Promise<void> {
  const r = await fetch(`/__states?name=${encodeURIComponent(name)}`);
  await openState(name, new Uint8Array(await r.arrayBuffer()));
}

async function save(slot: Slot): Promise<void> {
  if (!game) return;
  const ok = await saveState(slot, { name: `${game.name} · ${clockText()}`, ram: game.ram });
  if (slot !== "auto") shell.message(ok ? `Saved to slot ${slot}` : "Saving failed (browser storage unavailable)", clockText());
}

async function loadSlot(slot: Slot): Promise<void> {
  const s = await loadState(slot);
  if (s) start(Game.load(s.ram, s.name));
}

function exportState(): void {
  if (!game) return;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(writeStateFile(game.ram));
  a.download = `carrier_${clockText().replace(/[^0-9]/g, "")}.bin`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function togglePause(): void {
  const r = game?.runner;
  if (!r) return;
  if (r.status === "running") r.pause();
  else r.play();
}

function setWaypoint(x: number, z: number): void {
  if (game && !controls.waypoint(x, z)) shell.message("Can't set a course now", clockText());
}

// ---- screens -----------------------------------------------------------------------------
function setScreen(id: number): void {
  game?.setScreen(id);
  showScreen();
}

function showScreen(): void {
  const id = game?.screen ?? SCREENS.bridge;
  shell.showScreen(id);
  const panels: HTMLElement[] = [];
  if (id === SCREENS.map) panels.push(map.el);
  else if (id === SCREENS.damage) panels.push(damage.el);
  else if (id === SCREENS.bridge && !freeCamera) panels.push(bridge.el);
  shell.content.replaceChildren(...panels);
  shell.content.classList.toggle("opaque", id === SCREENS.map);
}

function setFreeCamera(on: boolean): void {
  if (on && !freeCamera) {
    const q = new THREE.Quaternion();
    carrierCam.orientation(q);
    freeCam.placeAt(carrierCam.eye(), q);
  }
  freeCamera = on;
  freeCam.enabled = on;
  showScreen();
}

const clockText = () => {
  if (!game) return "";
  const c = game.runner.clock, two = (n: number) => String(n).padStart(2, "0");
  return `Day ${c.days + 1} ${two(c.hours)}:${two(c.minutes)}:${two(c.seconds)}`;
};

// ---- input -------------------------------------------------------------------------------
const HELD: Record<string, "faster" | "slower" | "left" | "right"> = {
  KeyW: "faster", ArrowUp: "faster", KeyS: "slower", ArrowDown: "slower",
  KeyA: "left", ArrowLeft: "left", KeyD: "right", ArrowRight: "right",
};
addEventListener("keyup", (e) => {
  if (HELD[e.code]) controls.hold(HELD[e.code], false);
});
addEventListener("blur", () => Object.values(HELD).forEach((k) => controls.hold(k, false)));
addEventListener("keydown", (e) => {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || menu.el.open) return;
  if (game && !freeCamera && HELD[e.code]) {
    controls.hold(HELD[e.code], true);
    e.preventDefault();
    return;
  }
  if (game && !freeCamera) {
    if (e.code === "KeyX") return controls.stop();
    if (e.code === "KeyE") return controls.centreRudder();
    if (e.code === "KeyQ") return controls.autopilot();
  }
  switch (e.code) {
    case "Space":
    case "KeyP":
      togglePause();
      break;
    case "Escape":
      void menu.open();
      break;
    case "KeyC":
      setFreeCamera(!freeCamera);
      break;
    case "KeyM":
      setScreen(SCREENS.map);
      break;
    case "KeyB":
      setScreen(SCREENS.bridge);
      break;
    default:
      return;
  }
  e.preventDefault();
});
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("drop", (e) => {
  e.preventDefault();
  const f = e.dataTransfer?.files[0];
  if (f) void f.arrayBuffer().then((b) => openState(f.name, new Uint8Array(b)));
});

function resize(): void {
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();

/** The camera's frustum, widened to the window's shape (never cropped). */
function fitFrustum(cam: ViewCamera): void {
  const { tanX, tanY, near } = cam.frustum;
  const fov = (2 * Math.atan(Math.max(tanY, tanX / camera.aspect)) * 180) / Math.PI;
  if (camera.fov !== fov || camera.near !== near) {
    camera.fov = fov;
    camera.near = near;
    camera.updateProjectionMatrix();
  }
}

// ---- frame loop --------------------------------------------------------------------------
const q = new THREE.Quaternion();
let last = performance.now();
function frame(now = performance.now()): void {
  const dt = Math.min((now - last) / 1000, 0.25);
  last = now;
  if (!game || !view) {
    renderer.setClearColor(0x05070c);
    renderer.clear();
    return;
  }
  const r = game.runner;
  r.advance(dt);
  for (const ev of r.state.events.splice(0)) if (ev[0] === "message") shell.message(String(ev[1]), clockText());
  if (r.stopReason && r.stopReason !== notice) setNotice(r.stopReason);
  if (r.status === "running" && (sinceAutosave += dt) > AUTOSAVE_SECONDS) {
    sinceAutosave = 0;
    void save("auto");
  }
  shell.setStatus(clockText(), r.status === "running", r.status === "stopped");

  const screen = game.screen;
  if (screen === SCREENS.map) map.draw(game.ram);
  else {
    if (screen === SCREENS.bridge) bridge.update(game.ram, names);
    if (screen === SCREENS.damage) damage.update(game.ram);
    carrierCam.update(game.ram);
    const cam: ViewCamera = freeCamera ? freeCam : carrierCam;
    cam.update(game.ram, dt);
    cam.orientation(q);
    view.update(cam.eye(), q);
    camera.quaternion.copy(q);
    fitFrustum(cam);
    renderer.render(view.scene, camera);
  }
}
renderer.setAnimationLoop(frame);

// ---- start-up ----------------------------------------------------------------------------
void (async () => {
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}data/title.bin`);
    if (res.ok) title = await readStateFile(new Uint8Array(await res.arrayBuffer()));
  } catch {
    // no data: emulator states only
  }
  if (title) names = islandNames(title);
  if (import.meta.env.DEV) {
    try {
      localStates.push(...((await (await fetch("/__states")).json()) as string[]));
    } catch {
      // not the dev server
    }
  }
  const auto = await loadState("auto");
  if (auto) start(Game.load(auto.ram, auto.name));
  else void menu.open();
  if (!title) setNotice("No title-screen image: run `npm run data` to start new games; emulator states still load");
})();

if (import.meta.env.DEV)
  (window as unknown as { app: unknown }).app = {
    get game() { return game; }, get view() { return view; }, camera, renderer, frame, freeCam, carrierCam, setScreen, newGame,
  };
