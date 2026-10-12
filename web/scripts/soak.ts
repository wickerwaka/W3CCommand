// Soak test: new games played with random carrier commands and screen switches for many passes,
// in Node, the way the app runs them (commands, ui_frame, game_frame, frame_flip_flags). Fails on any exception
// (unported code included) and reports what each game reached.
//
//   node scripts/soak.ts [--games 8] [--frames 20000] [--seed 1]
// Needs public/data/title.bin (npm run data).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { NotPorted, UnitState } from "../src/sim/index.ts";

const arg = (name: string, d: number) => {
  const i = process.argv.indexOf(name);
  return i >= 0 ? Number(process.argv[i + 1]) : d;
};
const GAMES = arg("--games", 8), FRAMES = arg("--frames", 20000);
let seed = arg("--seed", 1);
const rnd = () => {
  seed = (Math.imul(seed, 1103515245) + 12345) >>> 0;
  return seed / 2 ** 32;
};

const title = new Uint8Array(readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "public", "data", "title.bin")));
const ISLAND_TABLE = 0x69b80, ISLAND_SIZE = 0x192;
let failed = 0;

for (let g = 0; g < GAMES; g++) {
  const strategy = g % 2 === 0;
  const ram = title.slice();
  for (let k = 0; k < 8; k++) ram[0x1236 + k] = Math.floor(rnd() * 256);
  const s = UnitState.from_ram(ram);
  const events: string[] = [];
  let frame = 0, ended = "";
  try {
    s.new_game(strategy);
    s.new_game_screen();
    for (; frame < FRAMES; frame++) {
      const r = rnd();
      if (r < 0.002) {
        const i = Math.floor(rnd() * 64), isl = ISLAND_TABLE + ISLAND_SIZE * i;
        s.cmd_set_carrier_waypoint(s.mem.s32(isl + 2), s.mem.s32(isl + 6) - s.mem.u16(isl + 0x0c) - 4000);
        s.cmd_carrier_autopilot();
      } else if (r < 0.004) s.cmd_carrier_autopilot();
      else if (r < 0.006) s.cmd_carrier_stop();
      else if (r < 0.0065) s.cmd_carrier_centre_rudder();
      else if (r < 0.05) s.cmd_carrier_speed_up();
      else if (r < 0.06) {
        if (s.mem.u8(0x457)) s.cmd_toggle_mouse_steer();
        s.cmd_steer_to(Math.floor(rnd() * 511) - 255);
      } else if (r < 0.061) s.cmd_set_repair_priority(Math.floor(rnd() * 10), Math.floor(rnd() * 3));
      else if (r < 0.0625) s.cmd_switch_screen([0, 0, 1, 2][Math.floor(rnd() * 4)]);
      if (frame % 8 === 0) {
        s.mem.set8(0x8fc, 0xff);
        s.mem.set8(0x8fd, 0xff);
      }
      s.ui_frame();
      s.game_frame();
      s.frame_flip_flags();
      for (const e of s.events.splice(0)) {
        events.push(e.join(" "));
        if (e[0] === "game over") {
          ended = ` (game over at frame ${frame}, screen ${e[1]}, result ${e[2]})`;
          frame = FRAMES;
        }
      }
    }
    const counts = `player ${s.mem.u16(0xd54)} enemy ${s.mem.u16(0xd56)}`;
    const msgs = new Map<string, number>();
    for (const e of events) msgs.set(e, (msgs.get(e) ?? 0) + 1);
    const top = [...msgs].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, v]) => `${v}x ${k}`).join("; ");
    console.log(`game ${g} (${strategy ? "strategy" : "action"}): OK${ended || `, ${frame} frames`}, islands ${counts}; ${top}`);
  } catch (e) {
    failed++;
    const what = e instanceof NotPorted ? `unported: ${e.message}` : (e as Error).stack;
    console.log(`game ${g} (${strategy ? "strategy" : "action"}): FAILED at frame ${frame}: ${what}`);
  }
}
console.log(failed ? `${failed} of ${GAMES} games failed` : `all ${GAMES} games OK`);
process.exit(failed ? 1 : 0);
