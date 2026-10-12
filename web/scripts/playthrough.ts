// A scripted play-through of the MVP loop, with the model's own commands, the way the app
// runs a game (commands, ui_frame, game_frame, render_effects, frame_flip_flags): start a
// strategy game, sail to a neutral island, arm a Walrus with an ACCB, drive it ashore, drop
// the ACCB and wait until the island is ours. Exits non-zero if any step doesn't happen.
//
//   node scripts/playthrough.ts [--seed N]
// Needs public/data/title.bin (npm run data).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { UnitState } from "../src/sim/index.ts";

const seedArg = process.argv.indexOf("--seed");
const seed = seedArg >= 0 ? Number(process.argv[seedArg + 1]) : 7;
const title = new Uint8Array(readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "public", "data", "title.bin")));
const ram = title.slice();
for (let k = 0; k < 8; k++) ram[0x1236 + k] = (seed * 37 + k * 101) & 0xff;
const s = UnitState.from_ram(ram);
s.new_game(true);
s.new_game_screen();

const ISLAND_TABLE = 0x69b80, ISLAND_SIZE = 0x192, OWNERS = 0x69b40, CARRIER = 0x5e95a, WALRUS0 = 0x5dcda + 0x140 * 6;
const names: string[] = [];
for (let i = 0; i < 64; i++) {
  let a = 0x4ad20 + s.mem.u16(0x4ad20 + 2 * i), t = "";
  while (s.mem.u8(a)) t += String.fromCharCode(s.mem.u8(a++));
  names.push(t);
}
const isl = (i: number) => { const a = ISLAND_TABLE + ISLAND_SIZE * i; return { x: s.mem.s32(a + 2), z: s.mem.s32(a + 6), hx: s.mem.u16(a + 0x0a), hz: s.mem.u16(a + 0x0c) }; };
const log: string[] = [];
let pass = 0;
function run(n: number, until?: () => boolean): boolean {
  for (let k = 0; k < n; k++, pass++) {
    if (pass % 8 === 0) {
      s.mem.set8(0x8fc, 0xff);
      s.mem.set8(0x8fd, 0xff);
    }
    s.ui_frame();
    s.game_frame();
    s.render_effects();
    if (!(s.mem.u8(0x1189) && s.mem.u8(0x866))) s.frame_flip_flags();
    for (const e of s.events.splice(0)) if (e[0] === "message" || e[0] === "captured" || e[0] === "game over") log.push(`${pass} ${e.join(" ")}`);
    if (until?.()) return true;
  }
  return false;
}
const step = (what: string, ok: boolean) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${what} (pass ${pass})`);
  if (!ok) {
    console.log(log.slice(-15).join("\n"));
    process.exit(1);
  }
};

// 1. the nearest neutral island to VULCAN
const home = isl(0);
const target = [...Array(64).keys()].filter((i) => s.mem.u8(OWNERS + i) === 0)
  .sort((a, b) => Math.hypot(isl(a).x - home.x, isl(a).z - home.z) - Math.hypot(isl(b).x - home.x, isl(b).z - home.z))[0];
const T = isl(target);
console.log(`target: ${names[target]} (${target})`);

// 2. sail there with the autopilot, which steers straight for each waypoint (and runs aground
// on any island in the way, as in the original): first out of VULCAN's harbour, then legs
// towards a point off the target's near shore, with a dog-leg after each grounding.
const carrierPos = () => ({ x: s.mem.s32(CARRIER + 0x0a), z: s.mem.s32(CARRIER + 0x12) });
function sail(x: number, z: number, passes: number): "arrived" | "grounded" | "timeout" {
  const n = log.length;
  s.cmd_set_carrier_waypoint(x, z);
  if (!(s.mem.u8(0x640) & 1)) s.cmd_carrier_autopilot();
  const near = () => { const c = carrierPos(); return Math.hypot(c.x - x, c.z - z) < 5000; };
  run(passes, () => near() || log.slice(n).some((l) => /reached waypoint|grounded/.test(l)));
  const tail = log.slice(n).join(" ");
  if (tail.includes("grounded")) {
    run(80);                                     // the recoil astern
    return "grounded";
  }
  return near() || tail.includes("reached waypoint") ? "arrived" : "timeout";
}
{
  // the strategy game starts 30000 south of VULCAN's centre facing it: back off astern first
  s.cmd_carrier_throttle(195);                   // full astern (-32)
  run(400);
  s.cmd_carrier_stop();
  run(100);
  const c = carrierPos();
  step(`backed off VULCAN (${Math.round(Math.hypot(c.x - home.x, c.z - home.z))} from its centre)`, Math.hypot(c.x - home.x, c.z - home.z) > 35000);
}
const dest = (() => {
  const c = carrierPos(), dx = c.x - T.x, dz = c.z - T.z, d = Math.hypot(dx, dz);
  const r = Math.max(T.hx, T.hz) + 14000;               // off the shore facing us
  return { x: T.x + (dx / d) * r, z: T.z + (dz / d) * r };
})();
let result = "timeout";
for (let attempt = 0; attempt < 6 && result !== "arrived"; attempt++) {
  result = sail(dest.x, dest.z, 90000);
  if (result === "grounded") {
    // turn 90 degrees off the line to the destination and sail clear first
    const c = carrierPos(), dx = dest.x - c.x, dz = dest.z - c.z, d = Math.hypot(dx, dz) || 1;
    const side = attempt % 2 ? 1 : -1;
    sail(c.x + (side * dz / d) * 25000 - (dx / d) * 8000, c.z - (side * dx / d) * 25000 - (dz / d) * 8000, 6000);
  }
}
// stop there (autopilot off, engines stop) and let her come to rest
if (s.mem.u8(0x640) & 1) s.cmd_carrier_autopilot();
s.cmd_carrier_stop();
run(600, () => s.mem.s16(0x644) === 0);
run(50);
const cp = carrierPos();
step(`carrier off ${names[target]} (${Math.round(Math.hypot(cp.x - T.x, cp.z - T.z))} from its centre)`, result === "arrived");

// 3. arm Walrus 1 with an ACCB and launch it
s.cmd_switch_screen(16);
run(2);
s.cmd_walrus_arming_select(0);
step("ACCB fitted", s.cmd_walrus_fit(0, 9) && s.mem.u16(0x570 + 0x26) === 9);
s.cmd_switch_screen(19);
run(2);
s.cmd_walrus_refuel(0);
s.cmd_walrus_launch(0);
step("Walrus launched", run(600, () => s.mem.u8(0x570 + 1) === 8));
// drive mode +$1A: 2 = the launch sequence down the ramp; wait until it's clear of the dock
step("Walrus clear of the dock", run(2000, () => s.mem.u8(0x570 + 0x1a) !== 2));
run(100);

// 4. drive to the island's centre on the Walrus autopilot
s.cmd_switch_screen(17);
run(2);
s.cmd_walrus_map_select(0);
s.cmd_set_walrus_waypoint(T.x, T.z);
s.cmd_switch_screen(18);
run(2);
if (!(s.mem.u8(0x570) & 1)) s.cmd_walrus_autopilot();
const ashore = run(20000, () => {
  const wx = s.mem.s32(WALRUS0 + 0x0a), wz = s.mem.s32(WALRUS0 + 0x12);
  return Math.abs(wx - T.x) < T.hx - 2500 && Math.abs(wz - T.z) < T.hz - 2500;
});
step("Walrus inland", ashore);

// 5. take over, select the payload and fire: the ACCB drops
if (s.mem.u8(0x570) & 1) s.cmd_walrus_autopilot();
s.cmd_walrus_weapon(2);
run(2);
if (s.mem.u8(0x457)) s.cmd_toggle_mouse_steer();
run(2);
s.cmd_vehicle_fire();
run(30);
step("ACCB dropped", s.mem.u16(0x570 + 0x0e) !== 9);

// 6. the builder grows into a command centre
const captured = run(20000, () => s.mem.u8(OWNERS + target) === 1);
step(`${names[target]} is ours`, captured);
console.log(log.filter((l) => / friendly| captured/.test(l)).slice(-3).join("\n"));
