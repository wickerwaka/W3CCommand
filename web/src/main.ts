// Carrier Command in the browser. Starts new games from the title-screen RAM image
// (public/data/title.bin, `npm run data`), or loads states exported from the emulator; runs the
// simulation model (src/sim) on the original's clock; draws the world (src/render) and a modern
// UI (src/ui) that acts only through the model's own commands.
import * as THREE from "three";
import "./style.css";
import { Controls, type Held } from "./app/controls.ts";
import { Game, SCREENS, vblsPerPass } from "./app/game.ts";
import { resultText } from "./app/runner.ts";
import { islandNames, islands } from "./app/readout.ts";
import { readStateFile, writeStateFile } from "./app/state_file.ts";
import { loadState, saveState, type Slot } from "./app/store.ts";
import { FreeCamera, GameCamera, type ViewCamera } from "./render/cameras.ts";
import { WorldView } from "./render/world_view.ts";
import { ModelListView, titleStep } from "./render/diagram.ts";
import { ditherCell } from "./render/meshes.ts";
import { Sound } from "./audio/sound.ts";
import { MapView } from "./ui/map.ts";
import { Menu } from "./ui/menu.ts";
import { BridgeHud, DamagePanel } from "./ui/panels.ts";
import { Gunsight } from "./ui/gunsight.ts";
import { Shell } from "./ui/shell.ts";
import { VEHICLE_SCREENS, VehiclePanel } from "./ui/vehicles.ts";
import { WEAPON_SCREENS, WeaponsPanel } from "./ui/weapons.ts";
import { INFO_SCREENS, InfoPanel } from "./ui/screens.ts";

const canvas = document.getElementById("view") as HTMLCanvasElement;
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, logarithmicDepthBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const camera = new THREE.PerspectiveCamera(50, 1, 5, 3e6);
const carrierCam = new GameCamera();
const freeCam = new FreeCamera(canvas);

const AUTOSAVE_SECONDS = 30;
const MAP_SCREENS = new Set<number>([SCREENS.map, SCREENS.mantaMap, SCREENS.walrusMap]);
/** The hangar arming screens: their 3D hangar view isn't modelled; the panel stands alone. */
const ARMING_SCREENS = new Set<number>([SCREENS.mantaArming, SCREENS.walrusArming]);
/** 2D screens: no 3D view behind their panel. */
const FLAT_SCREENS = new Set<number>([3, 4, 12, 20, 28]);

let title: Uint8Array | null = null;
let game: Game | null = null;
let view: WorldView | null = null;
let names: string[] = [];
let freeCamera = false;
let dither = true;
let showEnemy = false;
let sinceAutosave = 0;
let notice: string | null = null;
/** The original's sound engine and YM2149 (src/audio), started on the first user gesture. */
let sound: Sound | null = null;
let muted = false;
let soundOn = false;
const startSound = () => {
  if (sound && !sound.started) void sound.start().catch(() => {});
};
addEventListener("pointerdown", startSound);
addEventListener("keydown", startSound);

// ---- UI --------------------------------------------------------------------------------
const shell = new Shell(document.getElementById("ui")!, {
  screen: (id) => setScreen(id),
  playPause: () => togglePause(),
  speed: (f) => game && (game.runner.speed = f),
  menu: () => void menu.open(),
});

const controls = new Controls();
let bridge = new BridgeHud(names, controls);
const vehicles = new VehiclePanel(controls);
vehicles.onRefused = () => shell.message("Not available now", clockText());
const weapons = new WeaponsPanel(controls);
const info = new InfoPanel(controls);
const damage = new DamagePanel((k, cls) => controls.repairPriority(k, cls),
  (k) => controls.run(() => game?.runner.state.cmd_select_system(k)));
const diagram = new ModelListView();
const map = new MapView(names, {
  onWaypoint: (x, z) => setWaypoint(x, z),
  onIsland: (i) => {
    if (!game || game.screen !== SCREENS.map) return;
    const isl = islands(game.ram, names)[i];
    controls.setStockpile(isl.x, isl.z);
  },
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
    { label: "ST dithering (else blended colours)", get: () => dither, set: (v) => { dither = v; setDither(); } },
    { label: "Sound", get: () => !muted, set: (v) => { muted = !v; if (sound) sound.muted = muted; } },
    // the original never plots the enemy on the map (docs/units.md §10.7); a debugging aid
    { label: "Show the enemy carrier on the map (debug)", get: () => showEnemy, set: (v) => (showEnemy = v) },
  ],
});
document.body.append(menu.el);
/** Shown over 3D screens that have no picture (lost telemetry, no vehicle, damaged turret). */
const noSignal = document.createElement("div");
noSignal.className = "no-signal";
noSignal.textContent = "NO PICTURE";
noSignal.hidden = true;
document.body.append(noSignal);

/** The end of a game: the result, and after the enemy carrier is destroyed the original's
 * "continue?" (screen 35, cmd_continue). */
const gameOverDialog = document.createElement("dialog");
gameOverDialog.className = "menu";
document.body.append(gameOverDialog);
let shownGameOver: object | null = null;
function showGameOver(): void {
  const g = game, over = g?.runner.gameOver;
  if (!g || !over || shownGameOver === over) return;
  shownGameOver = over;
  const r = g.runner;
  const title = document.createElement("h1");
  title.textContent = over.screen === 35 ? "Enemy carrier destroyed" : resultText(over.result);
  const p = document.createElement("p");
  p.textContent = over.screen === 35 ? "Continue to take the remaining islands, or end the game here?" : clockText();
  const buttons = document.createElement("section");
  const button = (label: string, fn: () => void) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = () => {
      gameOverDialog.close();
      fn();
    };
    buttons.append(b);
  };
  if (over.screen === 35) {
    button("Continue", () => {
      controls.run(() => r.state.cmd_continue(1));
      r.gameOver = null;
      r.play();
    });
    button("End the game", () => {
      controls.run(() => r.state.cmd_continue(0));
      r.stop(resultText(4));
    });
  }
  button("New strategy game", () => newGame(true));
  button("New action game", () => newGame(false));
  gameOverDialog.replaceChildren(title, p, buttons);
  gameOverDialog.showModal();
}
const gunsight = new Gunsight();
document.body.append(gunsight.el);

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
  if (!sound) sound = new Sound(g.ram);
  sound.ram = g.ram;
  sound.muted = muted;
  sound.reset();
  g.runner.afterPass = () => sound?.pass();
  showScreen();
}

function newGame(strategy: boolean): void {
  if (!title) {
    setNotice("No title-screen image: load a state exported from Hatari from the menu, or run `npm run data` (needs Hatari) to create public/data/title.bin");
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

let shownScreen = -1;
function showScreen(): void {
  const id = game?.screen ?? SCREENS.bridge;
  if (shownScreen !== id) sound?.reset();       // $20716 runs on screen changes
  shownScreen = id;
  if (game) game.runner.vblsPerPass = vblsPerPass(id);
  shell.showScreen(id);
  const panels: HTMLElement[] = [];
  if (MAP_SCREENS.has(id)) panels.push(map.el);
  if (id === SCREENS.mantaMap || id === SCREENS.walrusMap) panels.push(vehicles.el);
  else if (id === SCREENS.damage) panels.push(damage.el);
  else if (id === SCREENS.bridge && !freeCamera) panels.push(bridge.el);
  else if (VEHICLE_SCREENS.has(id) && !MAP_SCREENS.has(id) && !freeCamera) panels.push(vehicles.el);
  else if (WEAPON_SCREENS.has(id) && !freeCamera) panels.push(weapons.el);
  if (INFO_SCREENS.has(id) && !freeCamera) panels.push(info.el);
  shell.content.replaceChildren(...panels);
  shell.content.classList.toggle("opaque", MAP_SCREENS.has(id) || ARMING_SCREENS.has(id) || FLAT_SCREENS.has(id) || id === SCREENS.damage);
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

/** The island events are $A128 messages in the original ("<name> is being attacked!" etc.). */
function islandMessage(ev: (string | number)[]): string | null {
  const name = names[Number(ev[1])] ?? "";
  switch (ev[0]) {
    case "attacked": return `${name} is being attacked!`;
    case "repulsed": return `${name} - attack repulsed`;
    case "fallen": return `${name} is now a free island`;      // its command centre fell: neutral
    case "neutral": return `${name} is now a free island`;
    case "captured": return Number(ev[2]) === 1 ? `${name} is now a friendly island` : `${name} is now an enemy island!`;
    default: return null;
  }
}

// ---- input -------------------------------------------------------------------------------
const HELD: Record<string, Held> = {
  KeyW: "faster", KeyS: "slower", ArrowUp: "up", ArrowDown: "down",
  KeyA: "left", ArrowLeft: "left", KeyD: "right", ArrowRight: "right", KeyF: "fire", Enter: "fire",
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
/** Drag on the 3D view to steer (bridge rudder, Manta/Walrus turn and climb, turret aim): the
 * drag offset from where it started is an analogue stick, like holding the keys partly. */
const STEER_SCREENS = new Set([0, 8, 18, 27]);
let steerDrag: { x: number; y: number; id: number } | null = null;
canvas.addEventListener("pointerdown", (e) => {
  if (!game || freeCamera || !STEER_SCREENS.has(game.screen)) return;
  steerDrag = { x: e.clientX, y: e.clientY, id: e.pointerId };
  canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener("pointermove", (e) => {
  if (!steerDrag || e.pointerId !== steerDrag.id) return;
  const r = Math.min(innerWidth, innerHeight) / 5;
  const clamp = (v: number) => Math.max(-1, Math.min(1, v));
  controls.stick = { x: clamp((e.clientX - steerDrag.x) / r), y: clamp((e.clientY - steerDrag.y) / r) };
});
const endSteer = () => {
  steerDrag = null;
  controls.stick = { x: 0, y: 0 };
};
canvas.addEventListener("pointerup", endSteer);
canvas.addEventListener("pointercancel", endSteer);

/** Screen 10: a click in the drone's picture fires the armed SSM at that point ($13598). Our
 * view is wider than the game's, so the click goes back through the viewport's projection. */
canvas.addEventListener("pointerdown", (e) => {
  if (!game || freeCamera || game.screen !== 10 || !game.ram[0x1160]) return;
  const ram = game.ram, u16 = (a: number) => (ram[a] << 8) | ram[a + 1], s16 = (a: number) => (u16(a) << 16) >> 16;
  const vp = ((ram[0x6fe] << 24) | (ram[0x6ff] << 16) | (ram[0x700] << 8) | ram[0x701]) >>> 0;
  if (!vp) return;
  const tanY = Math.tan((camera.fov * Math.PI) / 360), tanX = tanY * camera.aspect;
  const nx = (e.clientX / innerWidth) * 2 - 1, ny = (e.clientY / innerHeight) * 2 - 1;
  const px = Math.round(s16(vp + 12) + nx * tanX * 2 ** s16(vp + 8));
  const py = Math.round(s16(vp + 14) + ny * tanY * 2 ** s16(vp + 10));
  if (!controls.run(() => game!.runner.state.cmd_ssm_fire_at(px, py))) shell.message("Target outside the drone's picture", clockText());
});
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("drop", (e) => {
  e.preventDefault();
  const f = e.dataTransfer?.files[0];
  if (f) void f.arrayBuffer().then((b) => openState(f.name, new Uint8Array(b)));
});

/** The dither checker: about one ST pixel (the game's 128-row view across the window). */
function setDither(): void {
  ditherCell.value = dither ? Math.max(1, Math.round((innerHeight * renderer.getPixelRatio()) / 200)) : 0;
}

function resize(): void {
  renderer.setSize(innerWidth, innerHeight, false);
  setDither();
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

/** The world in a 2:1 box at the top of the screen (CSS pixels), for the arming screens. */
function renderInset(g: Game, v: WorldView): void {
  const w = Math.min(innerWidth - 32, 640), h = Math.round(w / 2), x = 16, top = 104;
  carrierCam.update(g.ram);
  carrierCam.orientation(q);
  v.update(carrierCam.eye(), q);
  camera.quaternion.copy(q);
  const fullAspect = camera.aspect;
  camera.aspect = w / h;
  fitFrustum(carrierCam);
  camera.updateProjectionMatrix();
  const y = innerHeight - top - h;                 // WebGL's viewport origin is bottom-left
  renderer.setViewport(x, y, w, h);
  renderer.setScissor(x, y, w, h);
  renderer.setScissorTest(true);
  renderer.render(v.scene, camera);
  renderer.setScissorTest(false);
  renderer.setViewport(0, 0, innerWidth, innerHeight);
  camera.aspect = fullAspect;
  camera.updateProjectionMatrix();
}

/** No game yet: the title screen's turning carrier ($15DA2) across the top, behind the menu,
 * on a copy of the title image advanced a title pass at a time (about 12 a second). */
let titleImage: Uint8Array | null = null;
let titleTime = 0;
function showTitle(dt: number): void {
  if (!title) return;
  titleImage ??= title.slice();
  for (titleTime += dt; titleTime >= 0.08; titleTime -= 0.08) titleStep(titleImage);
  const w = innerWidth, h = Math.min(innerHeight * 0.45, (w * 110) / 320);
  diagram.render(renderer, titleImage, 33, { x: 0, y: 0, w, h });
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
    showTitle(dt);
    return;
  }
  const r = game.runner;
  r.advance(dt);
  for (const ev of r.state.events.splice(0)) {
    // sounds the original plays at these points (docs/units.md §17): effects, graded
    // explosions at an object, and the message ping (third element 1)
    if (ev[0] === "sound") ev[1] === "game over" ? sound?.gameOver() : sound?.play(Number(ev[1]));
    else if (ev[0] === "explosion") {
      if (ev.length >= 5) sound?.explosion(Number(ev[1]), Number(ev[2]), Number(ev[3]), Number(ev[4]));
      else sound?.explosionAt(Number(ev[1]), Number(ev[2]));
    }
    if (ev[0] === "message") {
      shell.message(String(ev[1]), clockText());
      if (ev[2] === 1) sound?.play(0);
    }
    const island = islandMessage(ev);
    if (island) {
      shell.message(island, clockText());
      if (!r.state.mem.u8(0x1247)) sound?.play(1);    // $A128's ping, unless the option is off
    }
    // where the original would crash the ST, the model does something safe (docs/units.md §12)
    else if (ev[0] === "deviation") shell.message(`[port] ${ev[1]}`, clockText());
  }
  if (r.gameOver) showGameOver();
  else if (r.stopReason && r.stopReason !== notice) setNotice(r.stopReason);
  if (r.status === "running" && (sinceAutosave += dt) > AUTOSAVE_SECONDS) {
    sinceAutosave = 0;
    void save("auto");
  }
  shell.setStatus(clockText(), r.status === "running", r.status === "stopped");
  if (sound && soundOn !== (r.status === "running")) {
    soundOn = r.status === "running";
    sound.setEnabled(soundOn);
  }

  const screen = game.screen;
  if (screen !== shownScreen) showScreen();       // the game switched screens itself
  noSignal.hidden = true;
  if (MAP_SCREENS.has(screen) || ARMING_SCREENS.has(screen) || FLAT_SCREENS.has(screen) || screen === SCREENS.damage) {
    if (INFO_SCREENS.has(screen)) info.update(game.ram, screen);
    gunsight.update(game.ram, -1, 0, 1);
    if (MAP_SCREENS.has(screen)) map.draw(game.ram, screen);
    else {
      renderer.setClearColor(0x0b1220);
      renderer.clear();
      // the arming screens draw the world into a small 2:1 window ($475A): Manta arming the
      // bridge view, Walrus arming a view astern panning +-45 degrees ([$DC8])
      if (ARMING_SCREENS.has(screen)) renderInset(game, view);
      // the damage screen's carrier diagram ($156FA): the model list at $64CFE
      if (screen === SCREENS.damage) {
        damage.update(game.ram);
        const rect = damage.diagramRect();
        if (rect) diagram.render(renderer, game.ram, 1, rect);
      }
    }
    if (VEHICLE_SCREENS.has(screen)) vehicles.update(game.ram, screen);
  } else {
    if (WEAPON_SCREENS.has(screen)) weapons.update(game.ram, screen);
    if (INFO_SCREENS.has(screen)) info.update(game.ram, screen);
    if (screen === SCREENS.bridge) bridge.update(game.ram, names);
    if (screen === SCREENS.damage) damage.update(game.ram);
    if (VEHICLE_SCREENS.has(screen)) vehicles.update(game.ram, screen);
    carrierCam.update(game.ram);
    noSignal.hidden = freeCamera || carrierCam.live || screen === SCREENS.damage;
    const cam: ViewCamera = freeCamera ? freeCam : carrierCam;
    cam.update(game.ram, dt);
    cam.orientation(q);
    view.update(cam.eye(), q);
    camera.quaternion.copy(q);
    fitFrustum(cam);
    renderer.render(view.scene, camera);
    gunsight.update(game.ram, freeCamera || !carrierCam.live ? -1 : screen, camera.fov, camera.aspect);
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
    get game() { return game; }, get view() { return view; }, get sound() { return sound; }, camera, renderer, frame, freeCam, carrierCam, setScreen, newGame, openLocal,
    /** Render at the game's 256x128 viewport shape, scaled 2x, for comparing with Hatari. */
    gameSize() {
      Object.assign(canvas.style, { width: "512px", height: "256px", top: "60px", left: "20px" });
      renderer.setPixelRatio(1);
      renderer.setSize(512, 256, false);
      camera.aspect = 2;
      camera.updateProjectionMatrix();
    },
  };
