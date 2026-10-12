// Carrier Command in the browser: load a state exported from the emulator, run the simulation
// model on it (src/sim), and draw the world from the carrier's bridge camera or a free camera.
import * as THREE from "three";
import "./style.css";
import { Runner } from "./app/runner.ts";
import { readStateFile, writeStateFile } from "./app/state_file.ts";
import { loadLastState, saveLastState } from "./app/store.ts";
import { CarrierCamera, FreeCamera, type ViewCamera } from "./render/cameras.ts";
import { WorldView } from "./render/world_view.ts";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const canvas = $<HTMLCanvasElement>("view");
const ui = {
  file: $<HTMLInputElement>("file"),
  local: $<HTMLSelectElement>("local"),
  play: $<HTMLButtonElement>("play"),
  step: $<HTMLButtonElement>("step"),
  speed: $<HTMLSelectElement>("speed"),
  camera: $<HTMLButtonElement>("camera"),
  export: $<HTMLButtonElement>("export"),
  status: $<HTMLElement>("status"),
  drop: $<HTMLElement>("drop"),
  empty: $<HTMLElement>("empty"),
};

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, logarithmicDepthBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const camera = new THREE.PerspectiveCamera(50, 1, 5, 3e6);
const carrierCam = new CarrierCamera();
const freeCam = new FreeCamera(canvas);

interface Session {
  name: string;
  runner: Runner;
  view: WorldView;
}
let session: Session | null = null;
let active: ViewCamera = carrierCam;
let error: string | null = null;

function resize(): void {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

/** The camera's frustum, widened to the window's shape (never cropped). */
function fitFrustum(cam: ViewCamera): void {
  const { tanX, tanY, near } = cam.frustum;
  const tan = Math.max(tanY, tanX / camera.aspect);
  const fov = (2 * Math.atan(tan) * 180) / Math.PI;
  if (camera.fov !== fov || camera.near !== near) {
    camera.fov = fov;
    camera.near = near;
    camera.updateProjectionMatrix();
  }
}
addEventListener("resize", resize);
resize();

async function open(name: string, data: Uint8Array, remember = true): Promise<void> {
  try {
    const ram = await readStateFile(data);
    session?.view.dispose();
    session = { name, runner: new Runner(ram), view: new WorldView(ram) };
    error = null;
    carrierCam.update(ram);
    if (remember) void saveLastState({ name, ram: ram.slice() });
  } catch (e) {
    error = `${name}: ${(e as Error).message}`;
  }
  refresh();
}

function setCamera(cam: ViewCamera): void {
  if (cam === freeCam && active !== freeCam) {
    const q = new THREE.Quaternion();
    active.orientation(q);
    freeCam.placeAt(active.eye(), q);
  }
  active = cam;
  freeCam.enabled = cam === freeCam;
  refresh();
}

function refresh(): void {
  const r = session?.runner;
  ui.empty.hidden = !!session;
  ui.play.textContent = r?.status === "running" ? "Pause" : "Run";
  ui.play.disabled = ui.step.disabled = !r || r.status === "stopped";
  ui.export.disabled = !r;
  ui.camera.textContent = active === freeCam ? "Carrier camera" : "Free camera";
}

function statusLine(): void {
  const parts: string[] = [];
  if (session) {
    const r = session.runner, c = r.clock;
    const two = (n: number) => String(n).padStart(2, "0");
    parts.push(session.name, `day ${c.days} ${two(c.hours)}:${two(c.minutes)}:${two(c.seconds)}`, `${r.passes} passes`, active.name);
    if (r.stopReason) parts.push(`<span class="error">${escape(r.stopReason)}</span>`);
  }
  if (error) parts.push(`<span class="error">${escape(error)}</span>`);
  const html = parts.join(" · ");
  if (ui.status.innerHTML !== html) ui.status.innerHTML = html;
}

const escape = (s: string) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!);

// ---- input ---------------------------------------------------------------------------
ui.file.addEventListener("change", async () => {
  const f = ui.file.files?.[0];
  if (f) await open(f.name, new Uint8Array(await f.arrayBuffer()));
  ui.file.value = "";
});
ui.play.addEventListener("click", () => {
  const r = session?.runner;
  if (r) r.status === "running" ? r.pause() : r.play();
  refresh();
});
ui.step.addEventListener("click", () => {
  session?.runner.pause();
  session?.runner.step();
  refresh();
});
ui.speed.addEventListener("change", () => {
  if (session) session.runner.speed = Number(ui.speed.value);
});
ui.camera.addEventListener("click", () => setCamera(active === freeCam ? carrierCam : freeCam));
ui.export.addEventListener("click", () => {
  if (!session) return;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(writeStateFile(session.runner.ram));
  a.download = `${session.name.replace(/\.[^.]*$/, "")}_pass${session.runner.passes}.bin`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});
addEventListener("keydown", (e) => {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
  if (e.code === "Space") ui.play.click();
  else if (e.code === "KeyN") ui.step.click();
  else if (e.code === "KeyC") ui.camera.click();
  else return;
  e.preventDefault();
});
addEventListener("dragover", (e) => {
  e.preventDefault();
  ui.drop.hidden = false;
});
addEventListener("dragleave", (e) => {
  if (e.relatedTarget === null) ui.drop.hidden = true;
});
addEventListener("drop", async (e) => {
  e.preventDefault();
  ui.drop.hidden = true;
  const f = e.dataTransfer?.files[0];
  if (f) await open(f.name, new Uint8Array(await f.arrayBuffer()));
});

// Dev server: the emulator captures in emu/out, listed by vite.config.ts.
if (import.meta.env.DEV) {
  fetch("/__states")
    .then((r) => (r.ok ? r.json() : []))
    .then((names: string[]) => {
      if (!names.length) return;
      ui.local.hidden = false;
      ui.local.append(new Option("Local captures…", ""), ...names.map((n) => new Option(n, n)));
      ui.local.addEventListener("change", async () => {
        const name = ui.local.value;
        if (!name) return;
        const r = await fetch(`/__states?name=${encodeURIComponent(name)}`);
        await open(name, new Uint8Array(await r.arrayBuffer()));
        ui.local.value = "";
      });
    })
    .catch(() => {});
}

// ---- frame loop ------------------------------------------------------------------------
const q = new THREE.Quaternion();
let last = performance.now();
function frame(now = performance.now()): void {
  const dt = Math.min((now - last) / 1000, 0.25);
  last = now;
  if (session) {
    const { runner, view } = session;
    const was = runner.status;
    runner.advance(dt);
    if (runner.status !== was) refresh();
    carrierCam.update(runner.ram);
    active.update(runner.ram, dt);
    active.orientation(q);
    view.update(active.eye(), q);
    camera.quaternion.copy(q);
    fitFrustum(active);
    renderer.render(view.scene, camera);
  } else {
    renderer.clear();
  }
  statusLine();
}
renderer.setAnimationLoop(frame);

if (import.meta.env.DEV) (window as unknown as { app: unknown }).app = { get session() { return session; }, camera, renderer, frame, setCamera, carrierCam, freeCam };

void loadLastState().then((s) => {
  if (s && !session) void open(s.name, s.ram, false);
});
refresh();
