// Read-only views of what the original's 2D screens show (docs/units.md §10.7): resources,
// messages, defence radar, weapons status, arming, vehicle status, map extras, game status and
// game over. Every function takes a 512 KB RAM image (big-endian) and never writes it.
// tools/check_readouts.py checks a Python mirror of each helper against the text the
// original's render routines and panel callbacks draw, under Unicorn.

const u8 = (r: Uint8Array, a: number) => r[a];
const s8 = (r: Uint8Array, a: number) => (r[a] << 24) >> 24;
const u16 = (r: Uint8Array, a: number) => (r[a] << 8) | r[a + 1];
const s16 = (r: Uint8Array, a: number) => (u16(r, a) << 16) >> 16;
const s32 = (r: Uint8Array, a: number) => (r[a] << 24) | (r[a + 1] << 16) | (r[a + 2] << 8) | r[a + 3];
const u32 = (r: Uint8Array, a: number) => s32(r, a) >>> 0;

/** NUL-terminated string (control bytes < $20 kept as-is). */
function cstr(r: Uint8Array, a: number, max = 256): string {
  let s = "";
  for (let i = 0; i < max && r[a + i]; i++) s += String.fromCharCode(r[a + i]);
  return s;
}

/** The original's percentage text ($EC4A): (cur & $FFFF)·100 / max, `divu` low word. */
export function percent(cur: number, max: number): number {
  if (!max) return 0;
  return Math.floor(((cur & 0xffff) * 100) / max) & 0xffff;
}

// ---- number formatting shared by the 2D screens --------------------------------------------

/** $9FAA/$9FB6/$9FC2/$9FCE: n decimal digits of an unsigned word by repeated subtraction. The
 *  first digit isn't limited to 9 (e.g. 2 digits of 105 → ":5"). */
export function fmtDigits(v: number, n: number): string {
  v &= 0xffff;
  let s = "";
  for (let k = n - 1; k > 0; k--) {
    const p = 10 ** k, q = Math.floor(v / p);
    s += String.fromCharCode((0x30 + q) & 0xff);
    v -= q * p;
  }
  return s + String.fromCharCode(0x30 + v);
}

/** Up to n leading '0' → ' ' ($9FFE and the inline `cmpi.b #'0'` tests). */
export function blankZeros(s: string, n: number): string {
  let i = 0;
  while (i < n && s[i] === "0") i++;
  return " ".repeat(i) + s.slice(i);
}

/** The gauge text ($EC4A + "%"): "100%" only at exactly 100, else right-aligned in 3 places. */
export function percentText(cur: number, max: number): string {
  const p = percent(cur, max);
  if (p === 100) return "100%";
  const t = Math.floor(p / 10);
  return " " + (t ? String.fromCharCode((0x30 + t) & 0xff) : " ") + String.fromCharCode(0x30 + (p % 10)) + "%";
}

// ---- screen 4: messages ---------------------------------------------------------------------

export const MSG_POOL = 0x65d10, MSG_HEAD = 0x1148, MSG_SIZE = 0x30, MSG_LIFE = 300;
export const GAME_SECONDS = 0x97a;   // long, game time in seconds

export interface LogMessage {
  time: number;     // game second it was posted (+4)
  age: number;      // seconds ago (shown as M:SS)
  text: string;     // as drawn: uppercase, up to the first control byte
}

/** Screen 4 ($162B0). The log is a linked list from [$1148] through 16 records of $30 bytes at
 *  $65D10 (next.l, time.l, text from +8; free = next's top bit set), oldest first, appended by
 *  `$A206` (a-z uppercased; when all 16 are used the oldest is reused). The render drops the
 *  leading entries older than 300 s (unsigned) and lists the rest, one per 6-px line from y 28:
 *  minutes digit (age / 60), ":", seconds 2 digits, the text. Empty: "NO MESSAGES". */
export function messageLog(ram: Uint8Array): LogMessage[] {
  const now = u32(ram, GAME_SECONDS), out: LogMessage[] = [];
  let a = u32(ram, MSG_HEAD), n = 0;
  for (; a && n < 64; n++) {
    if (((now - u32(ram, a + 4)) >>> 0) <= MSG_LIFE) break;
    a = u32(ram, a);
  }
  for (; a && n < 64; n++) {
    const time = u32(ram, a + 4);
    let text = "";
    for (let i = 8; i < MSG_SIZE; i++) {
      const c = ram[a + i];
      if (c >= 0x20) text += String.fromCharCode(c);
      else if (c === 5) i++;
      else if (c === 6) { while (ram[a + i + 1] === 0x20) i++; }
      else break;
    }
    out.push({ time, age: (now - time) >>> 0, text });
    a = u32(ram, a);
  }
  return out;
}

/** "M:SS" as the log shows an age. */
export function messageAgeText(age: number): string {
  return String.fromCharCode((0x30 + Math.floor(age / 60)) & 0xff) + ":" + fmtDigits(age % 60, 2);
}

// ---- screens 11 and 12: drones and weapons ---------------------------------------------------

export const DRONE_STATIONS = 0x65e;     // 4 x 14: flags.w, pos (across, along).w, waypoint.w x2, station (across, along).w
export type DroneState = "docking" | "none" | "on station" | "in transit";

/** The drone status line of screens 11 and 12: launching/docking `$122F`, else from the 4
 *  station flags (+1 bit 0 out, bit 1 on station). */
export function droneState(ram: Uint8Array): DroneState {
  if (ram[0x122f]) return "docking";
  let out = 0, moving = 0;
  for (let k = 0; k < 4; k++) {
    const f = ram[DRONE_STATIONS + 14 * k + 1];
    if (f & 1) out++;
    if (!(f & 2)) moving++;
  }
  return out === 0 ? "none" : moving === 0 ? "on station" : "in transit";
}

export interface SystemState {
  operative: boolean;     // "OPERATIVE" / "INOPERATIVE"
  repair: number;         // "REPAIR STATE" percent (cur·100/max)
}

export type FlareState = "none" | "reloading" | "ready";

export interface WeaponsStatus {
  laser: SystemState;            // system 4: status word $B1A ≠ 0; repair $B14/$B16
  laserOverheating: boolean;     // heat $6C8 > $3000 (unsigned)
  turret: SystemState;           // system 3 (observation turret): status byte $B12; $B0C/$B0E
  flareLauncher: SystemState;    // system 5: $B22; $B1C/$B1E
  missileLauncher: SystemState;  // system 6 (SSM): $B2A; $B24/$B26
  flaresActive: number;          // $1152
  flaresInStores: number;        // item 10 on board, $C82
  flareState: FlareState;        // no stock → none; [$1152] = 2 or reload $1150 ≠ 0 → reloading
  viewersInStores: number;       // item 11 (recon drones), $C9C
  missilesInStores: number;      // item 2 (Hammerhead SSM), $BB2
  viewingDroneActive: boolean;   // object 15 live (+1 bit 0, $5EF9B)
  dronesActive: number;          // $65C (counted each pass by $1A296)
  dronesInStores: number;        // item 6, $C1A
  droneState: DroneState;
}

function systemState(ram: Uint8Array, k: number, wordStatus: boolean): SystemState {
  const a = 0xaf4 + 8 * k;
  return { operative: wordStatus ? u16(ram, a + 6) !== 0 : ram[a + 6] !== 0, repair: percent(u16(ram, a), u16(ram, a + 2)) };
}

/** Screen 12 ($16BA6). Two-digit counts blank a leading zero; the status line under the flare
 *  column reads "NO FLARES AVAILABLE" / "RELOADING LAUNCHER" / "FLARE LAUNCHER READY", the one
 *  under the drones "DRONES DOCKING" / "NO DRONES ACTIVE" / "DRONES ON STATION" / "DRONES IN
 *  TRANSIT", and "LASER OVERHEATING" / "VIEWING DRONE ACTIVE" appear only when true. */
export function weaponsStatus(ram: Uint8Array): WeaponsStatus {
  const stock = u16(ram, 0xc82), active = u16(ram, 0x1152);
  return {
    laser: systemState(ram, 4, true),
    laserOverheating: u16(ram, 0x6c8) > 0x3000,
    turret: systemState(ram, 3, false),
    flareLauncher: systemState(ram, 5, false),
    missileLauncher: systemState(ram, 6, false),
    flaresActive: active,
    flaresInStores: stock,
    flareState: stock === 0 ? "none" : active === 2 || u16(ram, 0x1150) !== 0 ? "reloading" : "ready",
    viewersInStores: u16(ram, 0xc9c),
    missilesInStores: u16(ram, 0xbb2),
    viewingDroneActive: (ram[0x5ef9b] & 1) !== 0,
    dronesActive: u16(ram, 0x65c),
    dronesInStores: u16(ram, 0xc1a),
    droneState: droneState(ram),
  };
}

export interface RadarDrone {
  index: number;                         // 0-3 (element types 66-69)
  active: boolean;                       // +1 bit 0: out of the carrier
  onStation: boolean;                    // +1 bit 1
  pos: { across: number; along: number };      // +2/+4: carrier-relative position (along = towards the bow)
  station: { across: number; along: number };  // +A/+C: its station
  marker: { x: number; y: number } | null;         // the drone icon (top-left), shown when active and radar works
  stationMarker: { x: number; y: number } | null;  // its station icon ($161A2[k]), shown while not on station
}

export interface DefenceRadar {
  radarWorking: boolean;                 // system 1 status byte $B02 ≠ 0; else no drones plotted
  drones: RadarDrone[];
  formation: number;                     // $698, 0-8 (table $1065C, 4 (across, along) per formation)
  formationPreview: { x: number; y: number }[];  // 3×3 colour-2 squares (centres) in the box (213,157)-(294,196)
  dronesActive: number;                  // $65C
  dronesInStores: number;                // $C1A
  droneState: DroneState;
}

/** Screen 11 ($15EB0). Plan view of the carrier at 1/60 scale, bow to the right, centred on the
 *  viewport centre ([$4744+$C], [$4744+$E]) = (160, 70): screen x = cx + along/60, y = cy +
 *  across/60 (`divs` truncation), icons drawn at (−4, −8) from that. The carrier outline is the
 *  fixed line list $16254 over points $161BA / 60. The formation preview (redrawn while $95A) is
 *  at 1/192: x = 254 + along/192, y = 177 + across/192. */
export function defenceRadar(ram: Uint8Array): DefenceRadar {
  const cx = s16(ram, 0x4744 + 0xc), cy = s16(ram, 0x4744 + 0xe);
  const radarWorking = ram[0xb02] !== 0;
  const t = (a: number, b: number) => Math.trunc(a / b);
  const drones: RadarDrone[] = [];
  for (let k = 0; k < 4; k++) {
    const a = DRONE_STATIONS + 14 * k, f = ram[a + 1];
    const pos = { across: s16(ram, a + 2), along: s16(ram, a + 4) };
    const station = { across: s16(ram, a + 0xa), along: s16(ram, a + 0xc) };
    const shown = (f & 1) !== 0 && radarWorking;
    drones.push({
      index: k, active: (f & 1) !== 0, onStation: (f & 2) !== 0, pos, station,
      marker: shown ? { x: t(pos.along, 60) + cx - 4, y: t(pos.across, 60) + cy - 8 } : null,
      stationMarker: shown && !(f & 2) ? { x: t(station.along, 60) + cx - 4, y: t(station.across, 60) + cy - 8 } : null,
    });
  }
  const formation = u16(ram, 0x698), formationPreview: { x: number; y: number }[] = [];
  for (let i = 0; i < 4; i++) {
    const a = 0x1065c + 16 * formation + 4 * i;
    formationPreview.push({ x: 0xfe + t(s16(ram, a + 2), 0xc0), y: 0xb1 + t(s16(ram, a), 0xc0) });
  }
  return {
    radarWorking, drones, formation, formationPreview,
    dronesActive: u16(ram, 0x65c), dronesInStores: u16(ram, 0xc1a), droneState: droneState(ram),
  };
}

// ---- screens 32, 34: game status and game over ----------------------------------------------

/** The score: 12 decimal digit bytes at $918 (most significant first), added to by `$A3EE`.
 *  Shown without leading zeros ($A418, then control 6). */
export function score(ram: Uint8Array): string {
  let s = "";
  for (let i = 0; i < 12; i++) s += String.fromCharCode((0x30 + ram[0x918 + i]) & 0xff);
  return blankZeros(s, 11).trimStart();
}

export interface GameClock {
  hours: number;    // $8F4 (0-23)
  minutes: number;  // $8F6
  seconds: number;  // $8F8
  days: number;     // $8F2 (not shown)
  text: string;     // "HH:MM:SS" ($CDC8, no blanking)
}

/** The game clock, counted by the VBL ($17BC8: 50 frames a second) while running and not on
 *  screens 32-35. */
export function gameClock(ram: Uint8Array): GameClock {
  const hours = u16(ram, 0x8f4), minutes = u16(ram, 0x8f6), seconds = u16(ram, 0x8f8);
  return { hours, minutes, seconds, days: u16(ram, 0x8f2), text: `${fmtDigits(hours, 2)}:${fmtDigits(minutes, 2)}:${fmtDigits(seconds, 2)}` };
}

export interface GameStatus {
  score: string;
  time: GameClock;
  friendlyIslands: number;   // $D54
  enemyIslands: number;      // $D56
}

/** Screen 32 overview ($CD5C, sub-mode [$708] = 0): SCORE, TIME, FRIENDLY ISLANDS, ENEMY
 *  ISLANDS (2 digits each, leading zero dropped). No difficulty is shown. */
export function gameStatus(ram: Uint8Array): GameStatus {
  return { score: score(ram), time: gameClock(ram), friendlyIslands: u16(ram, 0xd54), enemyIslands: u16(ram, 0xd56) };
}

export interface GameOptions {
  seaDots: boolean;          // $1246 = 0
  messagePing: boolean;      // $1247 = 0
  backgroundNoise: boolean;  // $1248 = 0
  soundEffects: boolean;     // $1249 = 0
}

/** Screen 32 options ($EAEE, sub-mode 7): YES lit while the byte is 0, else NO. */
export function gameOptions(ram: Uint8Array): GameOptions {
  return { seaDots: !ram[0x1246], messagePing: !ram[0x1247], backgroundNoise: !ram[0x1248], soundEffects: !ram[0x1249] };
}

export const GAME_RESULTS: Record<number, string[]> = {
  0: ["Game was aborted"],
  1: ["Aircraft Carrier Destroyed!", "All is lost"],
  2: ["All islands captured by enemy", "All is lost"],
  3: ["All islands captured!", "Congratulations on completing", "Carrier Command"],
  4: ["Enemy Carrier Destroyed!", "Congratulations on completing", "Carrier Command"],
};

export interface GameOver {
  result: number;            // $466: 0 surrendered, 1 carrier lost, 2 all islands enemy, 3 all ours, 4 enemy carrier destroyed
  lines: string[];           // the result text (table $CFCA)
  time: GameClock;
  score: string;             // "Final Score"
  islandsTaken: number;      // $D52: +1 per island captured ($1C362, $1C55C); 4 digits, leading zeros dropped
}

/** Screen 34 ($CF42): "GAME OVER" (vector font), the result, Time, Final Score, Islands Taken.
 *  Screen 35 ($EBBA) is fixed text: "Enemy Carrier Destroyed! Remaining islands can be captured
 *  for maximum points. Do you want to continue?" with NO / YES. */
export function gameOver(ram: Uint8Array): GameOver {
  const result = u16(ram, 0x466);
  return { result, lines: GAME_RESULTS[result] ?? [], time: gameClock(ram), score: score(ram), islandsTaken: u16(ram, 0xd52) };
}

// ---- screen 3: Resources (docs/units.md §10.7) ---------------------------------------------
// Render $14C6C by sub-page [$D14]; panel callbacks $D264 (0), $D2C6 (1), $DEAA (2), $E0BA (3),
// $E1B6 (4), $E392 (5). Store items: 15 × $1A bytes at $B76.

export const RES_ITEMS = 0xb76, RES_ITEM_SIZE = 0x1a, RES_N_ITEMS = 15;
/** Bar length in pixels of a full gauge on this screen. */
export const RES_BAR = 78;
/** Fuel per bar pixel: carrier tanks and transfer queues ($15234), stockpile tanks ($14E2C). */
export const RES_CARRIER_BAR_UNIT = 0x3482, RES_STOCKPILE_BAR_UNIT = 0x6905;

/** The sub-page shown ([$D14]): 0 stores, 1 item picture, 2 stores priorities, 3 fuel
 *  priorities, 4 stores transfer, 5 fuel transfer. */
export function resourcesSubPage(ram: Uint8Array): number {
  return u16(ram, 0xd14);
}

/** $15234 / $14E2C: bar length = `divs.w` quotient (on overflow the fuel's low word), at most
 *  78 (signed compare, so a negative value gives a negative length). */
export function resourcesBar(fuel: number, unit: number): number {
  const q = Math.trunc((fuel | 0) / unit);
  const lo = q >= -0x8000 && q <= 0x7fff ? q : ((fuel << 16) >> 16);
  return Math.min(lo, RES_BAR);
}

/** The original's 3-digit quantity ($9FC2 then $9FFE with 2): up to two leading zeros blanked.
 *  Values over 999 aren't produced by the game (stores are capped at 999). */
export function resourcesQty3(v: number): string {
  const s = String(v % 1000).padStart(3, "0");
  return s.replace(/^0{1,2}(?=\d)/, (z) => " ".repeat(z.length));
}

export interface TextSegment { x: number; y: number; text: string }

/** 8×8 control-coded text ($9BE2): code 3 x y positions (x in 8-px columns, y in pixels),
 *  1 x, 2 y, 5 colour, 6 skip spaces, $0D newline (+8). Returns the segments and the address
 *  after the NUL. */
function text8(ram: Uint8Array, a: number): { segments: TextSegment[]; end: number } {
  const segments: TextSegment[] = [];
  let x = 0, y = 0, cur = "", cx = -1;
  const flush = () => { if (cur) segments.push({ x: cx, y, text: cur }); cur = ""; cx = -1; };
  for (;;) {
    const c = ram[a++];
    if (c === 0) { flush(); return { segments, end: a }; }
    if (c & 0x80) { flush(); continue; }
    if (c <= 6) {
      flush();
      if (c === 1) x = ram[a++];
      else if (c === 2) y = ram[a++];
      else if (c === 3) { x = ram[a]; y = ram[a + 1]; a += 2; }
      else if (c === 5) a++;
      else if (c === 6) while (ram[a] === 0x20) a++;
      continue;
    }
    if (c === 0x0d) { flush(); y += 8; continue; }
    if (cx < 0) cx = x;
    cur += String.fromCharCode(c); x++;
  }
}

/** 5×5 lines ($9A56) from `a` until a NUL ends a line (other control bytes end a line). */
function text5Lines(ram: Uint8Array, a: number): string[] {
  const lines: string[] = [];
  for (;;) {
    let cur = "", c = 0;
    for (;;) {
      c = ram[a++];
      if (c >= 0x20) { cur += String.fromCharCode(c); continue; }
      if (c === 5) { a++; continue; }
      if (c === 6) { while (ram[a] === 0x20) a++; continue; }
      break;
    }
    lines.push(cur);
    if (c === 0) return lines;
  }
}

/** A store item's name: the 8×8 segments of the string at record +$A, joined by " - "
 *  (e.g. "Assassin - Air-to-air Missile"). */
export function storeItemName(ram: Uint8Array, k: number): string {
  return text8(ram, u32(ram, RES_ITEMS + RES_ITEM_SIZE * k + 0x0a)).segments.map((s) => s.text).join(" - ");
}

export interface StoreItemInfo {
  item: number;            // 0-14
  name: string;
  x: number;               // icon position on sub-page 0 (+0/+2); the count is drawn at (x+7, y+34)
  y: number;
  onBoard: number;         // +8: on the carrier (what loadouts and launches take)
  capacity: number;        // +$18: the carrier's limit ("Limit of ACC stores reached")
  picture: number;         // +$E: 64×64 picture index ($3B426 + $600·n)
  priority: number;        // +$10 committed class (0-2, 2 most urgent)
  target: number;          // +$12 committed target stock
  priorityEdit: number;    // +$14 working class shown on sub-page 2
  targetEdit: number;      // +$16 working target shown on sub-page 2
}

/** Sub-page 0 ($D264): every store item's icon and on-board count (`resourcesQty3(onBoard)`). */
export function resourcesStoresOnBoard(ram: Uint8Array): StoreItemInfo[] {
  const out: StoreItemInfo[] = [];
  for (let k = 0; k < RES_N_ITEMS; k++) {
    const a = RES_ITEMS + RES_ITEM_SIZE * k;
    out.push({
      item: k, name: storeItemName(ram, k), x: s16(ram, a), y: s16(ram, a + 2),
      onBoard: u16(ram, a + 8), capacity: u16(ram, a + 0x18), picture: u16(ram, a + 0x0e),
      priority: u16(ram, a + 0x10), target: u16(ram, a + 0x12),
      priorityEdit: u16(ram, a + 0x14), targetEdit: u16(ram, a + 0x16),
    });
  }
  return out;
}

export interface FuelGauge { fuel: number; bar: number }

/** Sub-page 0 render ($14C96): the carrier's three tanks as bars at y 192, x 60/148/236,
 *  78 px long, one pixel per $3482 units. */
export function resourcesCarrierFuel(ram: Uint8Array): { ship: FuelGauge; aircraft: FuelGauge; aav: FuelGauge } {
  const g = (a: number): FuelGauge => ({ fuel: u32(ram, a), bar: resourcesBar(s32(ram, a), RES_CARRIER_BAR_UNIT) });
  return { ship: g(0xcfc), aircraft: g(0xd00), aav: g(0xd04) };
}

export interface ItemPage {
  item: number;            // [$D16]
  units: number;           // +8, drawn "UNITS : nnn"
  picture: number;         // +$E
  nameSegments: TextSegment[];
  description: string[];   // 5×5 lines at (115, 14 + 8·i)
}

/** Sub-page 1 ($D2C6): the item clicked on sub-page 0, with picture, units on board, name and
 *  description (the text after the name's NUL). */
export function resourcesItemPage(ram: Uint8Array): ItemPage {
  const k = u16(ram, 0xd16), a = RES_ITEMS + RES_ITEM_SIZE * k;
  const { segments, end } = text8(ram, u32(ram, a + 0x0a));
  return { item: k, units: u16(ram, a + 8), picture: u16(ram, a + 0x0e), nameSegments: segments,
    description: text5Lines(ram, end) };
}

export interface PriorityRow { item: number; priority: number; target: number }

/** Sub-page 2 ($DEAA): three items from scroll [$D38] (0-12), each with its working class
 *  (+$14, a 3-level box, see `resourcesClassLevel`; 2 = most urgent)
 *  and working target (+$16). EXIT commits +$14/+$16 to +$10/+$12, CANCEL reloads them.
 *  `edit`: the keypad field ([$D1A], row [$D18], typed text at $D1B) drawn over the target. */
export function resourcesStoresPriorities(ram: Uint8Array): {
  first: number; rows: PriorityRow[]; edit: { row: number; text: string } | null;
} {
  const first = u16(ram, 0xd38), rows: PriorityRow[] = [];
  for (let i = 0; i < 3; i++) {
    const a = RES_ITEMS + RES_ITEM_SIZE * (first + i);
    rows.push({ item: first + i, priority: u16(ram, a + 0x14), target: u16(ram, a + 0x16) });
  }
  const edit = ram[0xd1a] ? { row: u16(ram, 0xd18), text: cstr(ram, 0xd1b, 8) } : null;
  return { first, rows, edit };
}

/** $DF84: the lit third of a class box: 0 → bottom, 1 → middle, anything else → top (2). */
export function resourcesClassLevel(cls: number): 0 | 1 | 2 {
  return cls === 0 ? 0 : cls === 1 ? 1 : 2;
}

/** Sub-page 3 ($E0BA): fuel priority classes for ship, aircraft and AAV fuel. The page shows
 *  the working copy $D0E–$D12; EXIT commits it to $D08–$D0C (what the logistics use). */
export function resourcesFuelPriorities(ram: Uint8Array): { edit: number[]; committed: number[] } {
  return {
    edit: [0, 1, 2].map((i) => u16(ram, 0xd0e + 2 * i)),
    committed: [0, 1, 2].map((i) => u16(ram, 0xd08 + 2 * i)),
  };
}

export interface TransferRow { item: number; stockpile: number; queued: number }

/** Sub-page 4 ($E1B6): three items from scroll [$D3A] (0-12): stock at the stockpile island
 *  ($D78[k], x 97) and queued for the resupply drone ($D96[k], x 206), both 3 digits. */
export function resourcesStoresTransfer(ram: Uint8Array): { first: number; rows: TransferRow[] } {
  const first = u16(ram, 0xd3a), rows: TransferRow[] = [];
  for (let i = 0; i < 3; i++) {
    const k = first + i;
    rows.push({ item: k, stockpile: u16(ram, 0xd78 + 2 * k), queued: u16(ram, 0xd96 + 2 * k) });
  }
  return { first, rows };
}

/** Sub-page 5 render ($14D28): stockpile tanks $D60/$D64/$D68 (ship, aircraft, AAV; bars at
 *  x 44, one pixel per $6905) and the queued fuel $D6C/$D70/$D74 (x 196, per $3482), rows y
 *  49/74/99. No numbers are shown. */
export function resourcesFuelTransfer(ram: Uint8Array): { stockpile: FuelGauge[]; queued: FuelGauge[] } {
  const g = (a: number, unit: number): FuelGauge => ({ fuel: s32(ram, a), bar: resourcesBar(s32(ram, a), unit) });
  return {
    stockpile: [0, 1, 2].map((i) => g(0xd60 + 4 * i, RES_STOCKPILE_BAR_UNIT)),
    queued: [0, 1, 2].map((i) => g(0xd6c + 4 * i, RES_CARRIER_BAR_UNIT)),
  };
}

export interface ResourcesGate {
  comms: boolean;                  // [$B0A] Communications status ≠ 0
  transferRunning: boolean;        // [$D3E]
  atStockpile: boolean;            // the carrier's island buffer ([$5E996] = object 10 +$3C) is the stockpile [$D4A]
  stockpileIsland: number;         // [$D4A]
  prioritiesBlocked: string | null;  // why sub-pages 2/3 are refused
  transfersBlocked: string | null;   // why sub-pages 4/5 are refused
}

/** $177DE (each pass on screen 3) and the sub-page buttons $109E8/$10B72/$10C06/$10D52:
 *  sub-pages ≥ 2 need comms; 4 and 5 also no transfer in progress and the carrier at the
 *  stockpile island. When it fails the message is shown and [$D14] = 0. */
export function resourcesGate(ram: Uint8Array): ResourcesGate {
  const comms = ram[0xb0a] !== 0, busy = ram[0xd3e] !== 0;
  const buf = u32(ram, 0x5e996), stockpileIsland = u16(ram, 0xd4a);
  const atStockpile = buf !== 0 && ram[buf + 1] === stockpileIsland;
  const prioritiesBlocked = comms ? null : "Communications are out";
  const transfersBlocked = prioritiesBlocked
    ?? (busy ? "Resupply operation in progress" : atStockpile ? null : "Stockpile island out of range");
  return { comms, transferRunning: busy, atStockpile, stockpileIsland, prioritiesBlocked, transfersBlocked };
}

// ---- Walrus / Manta arming (screens 16 / 24) and status (20 / 28) — docs/units.md §10.7 ----

export type Craft = "manta" | "walrus";
const REC_BASE = { manta: 0x496, walrus: 0x570 } as const;
const OBJ_FIRST = { manta: 2, walrus: 6 } as const;
const OBJ_BASE = 0x5dcda, OBJ_LEN = 0x140, BAY_LEN = 0x34;
const LOADOUTS = 0xcac4, LOADOUT_LEN = 0x1a;
const ISLAND_NAMES = 0x4ad20;

/** The original's number fields: `digits` = $9FAA/$9FB6/$9FC2/$9FCE (n = 5/4/3/2 digits of an
 *  unsigned word; the leading digit is not bounded, so it can run past '9'), then $9FFE blanks
 *  up to `blank` leading zeros. */
export function numberField(v: number, n: number, blank = 0): string {
  return blankZeros(fmtDigits(v, n), blank);
}

/** Split a control-coded string (5 n colour, 6 skip spaces, $0D line break, 0 end) into lines. */
function textLines(r: Uint8Array, a: number): string[] {
  const out: string[] = [];
  let cur = "";
  for (let i = 0; i < 256; i++) {
    const c = r[a++];
    if (c === 5) { a++; continue; }
    if (c === 6) { while (r[a] === 0x20) a++; continue; }
    if (c >= 0x20) { cur += String.fromCharCode(c); continue; }
    out.push(cur); cur = "";
    if (c === 0) break;
  }
  return out;
}

export interface LoadoutEntry {
  code: number;            // 0 empty, 1-3 Assassin x1-3, 4 Quaker, 5 Quasar, 6 Avatar, 7 Harbinger,
                           // 8 virus bomb, 9 ACCB, 10 comms pod, 11 fuel pod
  weight: number;          // kg, +0 (code 7: 100 + 150 x Harbingers to load, $C038)
  stockAddr: number;       // +2: the on-board item count it draws from (item record +8)
  stock: number;           // shown as QUANTITY on the arming screen
  quantity: number;        // +6: taken from stock per fit
  kind: number;            // +8: Walrus slot 1 for 8/9/11; 10 Avatar, 11 Harbinger (descriptor kind)
  mounting: string[];      // +$E (Manta list only): "SINGLE MISSILE / MOUNTING", "CENTRE / MOUNTING ONLY"
  description: string[];   // +$12: "ASSASSIN / AIR - AIR MISSILE" ...
}

/** One loadout table entry ($CAC4, 12 x 26 bytes). `harbingers`/`accbType` apply the Walrus
 *  panel's patches (weight of code 7, $C038; the ACCB's third line "POD - RESOURCE/FACTORY/DEFENCE",
 *  $C0F0 from the table $CAAC). */
export function loadoutEntry(r: Uint8Array, code: number, harbingers?: number, accbType?: number): LoadoutEntry {
  const a = LOADOUTS + LOADOUT_LEN * code;
  const stockAddr = u32(r, a + 2);
  let weight = u16(r, a);
  if (code === 7 && harbingers !== undefined) weight = (100 + 150 * harbingers) & 0xffff;
  const m = u32(r, a + 0x0e), d = u32(r, a + 0x12);
  let description = d ? textLines(r, d) : [];
  if (code === 9 && accbType !== undefined && description.length >= 3) {
    let t = "";
    for (let i = 0; i < 8; i++) t += String.fromCharCode(r[0xcaac + ((accbType << 3) & 0xffff) + i]);
    description = [...description.slice(0, 2), "POD - " + t];
  }
  return {
    code, weight, stockAddr, stock: stockAddr ? u16(r, stockAddr) : 0, quantity: u16(r, a + 6),
    kind: u16(r, a + 8), mounting: m ? textLines(r, m) : [], description,
  };
}

/** The arming list: Manta cursor $DB8 over $C770 (1, 2, 3, 4, 5, 10); Walrus $DBA over $C242
 *  (6, 7, 8, 9, 11). */
export function loadoutCatalogue(r: Uint8Array, craft: Craft): LoadoutEntry[] {
  const list = craft === "manta" ? 0xc770 : 0xc242, n = craft === "manta" ? 6 : 5;
  const out: LoadoutEntry[] = [];
  for (let i = 0; i < n; i++)
    out.push(craft === "manta" ? loadoutEntry(r, u16(r, list + 2 * i))
      : loadoutEntry(r, u16(r, list + 2 * i), u16(r, 0xcb80), u16(r, 0xdc6)));
  return out;
}

/** Arming sub-panel for a bay status ($11738/$11E60): 0 loadout (in the hangar), 1 repair or
 *  preparing, 2 the rest (lift/launching, out, destroyed). */
export function armingPanel(status: number): 0 | 1 | 2 {
  return status === 0 ? 0 : status <= 2 ? 1 : 2;
}

export interface ArmingView {
  craft: Craft;
  bay: number;              // $DB4 / $DB6 (shown + 1: "MANTA 1")
  status: number;           // record +1
  panel: 0 | 1 | 2;         // armingPanel(status) (the original's $DC0 / $DC2 on redraw)
  /** panel 1: "UNDER REPAIR" (status 1, CANCEL REPAIRS offered) or
   *  "UNDERGOING FLIGHT/LAUNCH PREPERATIONS" (status 2); panel 2: "AIRCRAFT/AAV ACTIVE",
   *  "... PREPARING TO LAUNCH" (status 3), "... DESTROYED" ($FF). */
  message: string[];
  cancelRepairs: boolean;   // type 79 shown (status 1)
  repairState: number;      // health % (+$14 x 100 / 4000), shown on panels 0 and 1
  fuelBar: number;          // 0..36 px: min(fuel / 910, 36) ($7A46 Manta +$A, $7A56 Walrus +8); panel 0
  tankBar: number;          // 0..78 px: min(carrier tank / $3482, 78) ($15200; $D00 aircraft, $D04 AAV)
  weight: number;           // CURRENT: +$12 (5-digit field; the "KG" is overwritten)
  maxWeight: number;        // MAXIMUM: 750 / 2000
  cursor: LoadoutEntry;     // the list item: description, WEIGHT, QUANTITY (= its stock)
  slots: LoadoutEntry[];    // Manta: fuselage +$10, wing +$C, wing +$E; Walrus: payload +$26, weapon +$28
  harbingersToLoad: number; // $CB80: digit beside the Harbinger icon (Walrus, cursor on code 7)
  slotMissiles: number;     // Walrus +$C: digit beside a fitted Harbinger
  accbType: number;         // $DC6: 0 resource, 1 factory, 2 defence
  unbalanced: boolean;      // Manta: |weight(+$C) - weight(+$E)| > 80 → "UNBALANCED PAYLOAD"
}

/** Screens 24 / 16 (panels $C580/$CBFE/$CCBA, $BFE8/$C3A4/$C440; bay record $48E/$568 is the
 *  arming selection's). */
export function armingView(r: Uint8Array, craft: Craft): ArmingView {
  const manta = craft === "manta";
  const bay = u16(r, manta ? 0xdb4 : 0xdb6) & 3;
  const rec = REC_BASE[craft] + BAY_LEN * bay;
  const status = u8(r, rec + 1), panel = armingPanel(status);
  const harb = u16(r, 0xcb80), accb = u16(r, 0xdc6);
  const cursorCode = u16(r, (manta ? 0xc770 : 0xc242) + 2 * u16(r, manta ? 0xdb8 : 0xdba));
  const fuel = u16(r, rec + (manta ? 0x0a : 0x08));
  const slotCodes = manta ? [u16(r, rec + 0x10), u16(r, rec + 0x0c), u16(r, rec + 0x0e)]
    : [u16(r, rec + 0x26), u16(r, rec + 0x28)];
  let d = (u16(r, LOADOUTS + LOADOUT_LEN * slotCodes[1]) - u16(r, LOADOUTS + LOADOUT_LEN * (slotCodes[2] ?? 0))) & 0xffff;
  if (d & 0x8000) d = (-d) & 0xffff;
  const what = manta ? "AIRCRAFT" : "AAV";
  const message = panel === 1
    ? (status === 1 ? ["UNDER REPAIR"] : ["UNDERGOING", manta ? "FLIGHT PREPERATIONS" : "LAUNCH PREPERATIONS"])
    : panel === 2
      ? (status & 0x80 ? [`${what} DESTROYED`] : status === 3 ? [`${what} PREPARING`, "TO LAUNCH"] : [`${what} ACTIVE`])
      : [];
  return {
    craft, bay, status, panel, message,
    cancelRepairs: status === 1,
    repairState: percent(u16(r, rec + 0x14), 4000),
    fuelBar: Math.min(Math.trunc(fuel / 0x38e), 36),
    tankBar: Math.min(Math.trunc(s32(r, manta ? 0xd00 : 0xd04) / 0x3482), 78),
    weight: u16(r, rec + 0x12), maxWeight: manta ? 750 : 2000,
    cursor: manta ? loadoutEntry(r, cursorCode) : loadoutEntry(r, cursorCode, harb, accb),
    slots: slotCodes.map((c) => (manta ? loadoutEntry(r, c) : loadoutEntry(r, c, undefined, u16(r, rec + 0x10)))),
    harbingersToLoad: harb, slotMissiles: manta ? 0 : u16(r, rec + 0x0c), accbType: accb,
    unbalanced: manta && d > 80,
  };
}

const BAY_WORDS = {
  manta: ["DESTROYED", "IN HANGER", "UNDER REPAIR", "FLIGHT PREP", "IN LIFT", "ACTIVE"],
  walrus: ["DESTROYED", "IN DOCK", "UNDER REPAIR", "LAUNCH PREP", "IN LIFT", "ACTIVE"],
};

/** The four-bay list under the hangar view ("MANTA FIGHTERS:" / "WALRUS AAV'S:", $150D2/$14F2C,
 *  drawn while $95C counts): index = signed status + 1, 6+ → ACTIVE (so 4..8 all read ACTIVE;
 *  a launching Walrus, status 3, reads "IN LIFT"). */
export function baySummary(r: Uint8Array, craft: Craft): string[] {
  const out: string[] = [];
  for (let k = 0; k < 4; k++) {
    const i = s8(r, REC_BASE[craft] + BAY_LEN * k + 1) + 1;
    out.push(BAY_WORDS[craft][i >= 0 && i < 6 ? i : 5]);
  }
  return out;
}

/** $BCFC: a world coordinate in map units, divs by $68E (1678), shown as sign + 4 digits. */
export function mapCoordinate(v: number): number {
  return Math.trunc(v / 0x68e);
}


const PAYLOAD_NAMES: Record<number, string> = {
  4: "CLUSTER BOMB", 8: "VIRUS BOMB", 9: "ACCB POD", 10: "COMMS POD", 11: "FUEL POD",
};

export interface VehicleStatusEntry {
  bay: number;
  state: "DESTROYED" | "IN HANGER" | "IN DOCK" | "ACTIVE";  // $FF / < 8 / >= 8
  // the rest only while ACTIVE (status >= 8)
  x?: number; z?: number;              // POSITION: map units (mapCoordinate), from object +$A/+$12
  island?: string | null;              // ISLAND: name of the object's island buffer (+$3C), null = "NONE"
  repairState?: number;                // health % (+$14 / 4000)
  payloadWeight?: number;              // PAYLOAD: +$12 kg
  laser?: boolean;                     // Manta: Quasar fitted (+$10 byte)
  laserShots?: number | null;          // Walrus: "LASER (n)" Avatar shots +$A (signed; < 0 → line omitted)
  missiles?: number;                   // +$C, 0 → line omitted ("MISSILES (n)", one digit)
  payload?: string | null;             // +$E low byte: 4/8/9/10/11 names, else nothing
}

/** Screens 28 / 20 ($16750 / $169FE): one block per bay, objects 2+k / 6+k. */
export function vehicleStatusPage(r: Uint8Array, craft: Craft): VehicleStatusEntry[] {
  const out: VehicleStatusEntry[] = [];
  const manta = craft === "manta";
  for (let k = 0; k < 4; k++) {
    const rec = REC_BASE[craft] + BAY_LEN * k;
    const obj = OBJ_BASE + OBJ_LEN * (OBJ_FIRST[craft] + k);
    const st = u8(r, rec + 1);
    if (st & 0x80) { out.push({ bay: k, state: "DESTROYED" }); continue; }
    if (st < 8) { out.push({ bay: k, state: manta ? "IN HANGER" : "IN DOCK" }); continue; }
    const buf = u32(r, obj + 0x3c);
    const p = u16(r, rec + 0x0e);
    out.push({
      bay: k, state: "ACTIVE",
      x: mapCoordinate(s32(r, obj + 0x0a)), z: mapCoordinate(s32(r, obj + 0x12)),
      island: buf ? cstr(r, ISLAND_NAMES + u16(r, ISLAND_NAMES + 2 * u8(r, buf + 1))) : null,
      repairState: percent(u16(r, rec + 0x14), 4000),
      payloadWeight: u16(r, rec + 0x12),
      laser: manta ? u8(r, rec + 0x10) !== 0 : s16(r, rec + 0x0a) >= 0,
      laserShots: manta ? undefined : (s16(r, rec + 0x0a) >= 0 ? u16(r, rec + 0x0a) : null),
      missiles: u16(r, rec + 0x0c),
      payload: p ? PAYLOAD_NAMES[p & 0xff] ?? null : null,
    });
  }
  return out;
}

// ---- map screens 2 / 17 / 25 (docs/units.md §10.7 "Maps") ---------------------------------
// The render $14E3C: sliders $7712, then the map $65E2 (border $68A6, links $68D0 and the
// islands panel $699E on the carrier map's islands mode, islands $6658, copyright $6A9E, units
// $6AFA / $6C36 / $6D74), or the island information box $BB10 while [$766] != 0.
// Requires the helpers at the top of readout_screens.ts (u8, s8, u16, s16, s32, cstr).

const MAP_VP = 0x47f2;                 // x0 y0 x1 y1 . . cx cy: (32, 6)-(287, 133), centre (160, 70)
const MAP_LEVEL = 0x758, MAP_ZOOM = 0x75a, MAP_X = 0x75c, MAP_Z = 0x760;
const MAP_INFO = 0x766, MAP_MODE = 0x710, NEAREST = 0x764, NEAREST_X = 0x742, NEAREST_Z = 0x746;
const M_ISLANDS = 0x69b80, M_ISLAND_SIZE = 0x192, M_OWNERS = 0x69b40, M_ECONOMY = 0x66ec0, M_ECONOMY_SIZE = 0x32;
const M_ROUTES = 0x67b40, M_NETWORK = 0x2239e, M_NAMES = 0x4ad20;
const M_OBJECTS = 0x5dcda, M_OBJECT_SIZE = 0x140, M_MANTAS = 0x496, M_WALRUSES = 0x570, M_REC = 0x34;
const M_STOCKPILE = 0xd4a, M_SINE = 0x1f5a, M_SHAPES = 0x6e96;
const M_BORDER_POINTS = 0x7564, M_BORDER_LINES = 0x7694, M_COPYRIGHT = 0x76e4;

const w16 = (v: number) => (v << 16) >> 16;
const islandName = (r: Uint8Array, i: number) => cstr(r, M_NAMES + u16(r, M_NAMES + 2 * i));

/** The original map never plots the enemy carrier or any enemy unit: the unit passes draw only
 *  the player's Mantas (objects 2-5), Walruses (6-9), the carrier (10), waypoints and the cursor,
 *  and nothing on the map reads the radar ($B02). Enemy presence shows only as island colours. */
export const MAP_SHOWS_ENEMY_CARRIER = false;

/** $65C4: (d asr 8) / zoom with `divs`, floored (quotient −1 when the remainder is negative);
 *  a quotient beyond a word gives ±$7FFF by the dividend's sign. */
function mapDiv(d: number, zoom: number): number {
  const d4 = d >> 8;
  const q = Math.trunc(d4 / zoom);
  if (q < -0x8000 || q > 0x7fff) return d4 < 0 ? -0x7fff : 0x7fff;
  return d4 - q * zoom < 0 && q !== -0x8000 ? q - 1 : q;
}

export interface MapViewState {
  level: number;            // $758 zoom level 0-10
  zoom: number;             // $75A = 1 << level: world units per pixel / 256
  x: number;                // $75C/$760 world point at the map centre (160, 70)
  z: number;
  infoBox: boolean;         // $766: the island information box replaces the map
  islandsMode: boolean;     // $710 (carrier map only): islands panel instead of the course panel
}

export function mapViewState(r: Uint8Array): MapViewState {
  return {
    level: s16(r, MAP_LEVEL), zoom: s16(r, MAP_ZOOM), x: s32(r, MAP_X), z: s32(r, MAP_Z),
    infoBox: s16(r, MAP_INFO) !== 0, islandsMode: s16(r, MAP_MODE) !== 0,
  };
}

/** $7526: world (x, z) to the map's screen pixel (320 × 200 screen; the map is (32, 6)-(287, 133)).
 *  sx = 160 + floor((x − [$75C]) / 256 / zoom), sy = 70 + floor((−(z − [$760])) / 256 / zoom);
 *  an overflowing add gives $7FFF. North (+z) is up. */
export function mapToScreen(r: Uint8Array, x: number, z: number): { sx: number; sy: number } {
  const zoom = s16(r, MAP_ZOOM);
  let sx = mapDiv((x - s32(r, MAP_X)) | 0, zoom) + s16(r, MAP_VP + 0xc);
  let sy = mapDiv((-((z - s32(r, MAP_Z)) | 0)) | 0, zoom) + s16(r, MAP_VP + 0xe);
  if (sx > 0x7fff || sx < -0x8000) sx = 0x7fff;
  if (sy > 0x7fff || sy < -0x8000) sy = 0x7fff;
  return { sx, sy };
}

export type MapMarkerKind =
  | "manta" | "walrus" | "carrier" | "cursor" | "waypoint" | "carrierWaypoint"
  | "runway" | "volcano" | "islandKind" | "stockpile";

/** A marker shape ($6E96 + 8·shape: points, line list) placed at a world point. */
export interface MapMarker {
  kind: MapMarkerKind;
  shape: number;            // 0 Manta, 1 Walrus (same diamond), 2 carrier, 3 cursor (colour 15),
                            // 4-7 bay waypoints, 8 carrier waypoint, 9 runway, $A volcano,
                            // $B-$E island kind 0-3 (base/resource/factory/defence), $F stockpile
  bay: number;              // 0-3 for units and their waypoints, else -1
  x: number;                // world position
  z: number;
  angle: number | null;     // 16-bit turn of the shape (units: (heading + $800) & $F000), null = none
  sx: number;               // screen pixel ($7526)
  sy: number;
  drawn: boolean;           // $6DFE draws it when |sx| and |sy| <= $200 (then clipped to the map)
}

function sincos(r: Uint8Array, a: number): [number, number] {
  a &= 0xfff0;
  return [s16(r, M_SINE + (a >> 3)), s16(r, M_SINE + (((a + 0x4000) & 0xffff) >> 3))];
}

function placeMarker(r: Uint8Array, kind: MapMarkerKind, shape: number, bay: number, x: number, z: number,
  angle: number | null): MapMarker {
  const { sx, sy } = mapToScreen(r, x, z);
  return { kind, shape, bay, x, z, angle, sx, sy, drawn: Math.abs(sx) <= 0x200 && Math.abs(sy) <= 0x200 };
}

/** The shape's line segments in screen pixels, as $6DFE draws them: points turned by the angle
 *  (word rotation, high word of 4·(x·cos + y·sin)), divided by 12·min(zoom, 8) (truncated), y
 *  flipped, plus (sx, sy). Each segment: [palette colour, x0, y0, x1, y1]. */
export function mapMarkerSegments(r: Uint8Array, m: MapMarker): [number, number, number, number, number][] {
  const [s, c] = m.angle === null ? [0, 0x4000] : sincos(r, m.angle);
  const div = 12 * Math.min(s16(r, MAP_ZOOM), 8);
  const pa = u32(r, M_SHAPES + 8 * m.shape), la = u32(r, M_SHAPES + 8 * m.shape + 4);
  const pts: [number, number][] = [];
  for (let k = 0, n = u16(r, pa); k < n; k++) {
    const px = s16(r, pa + 2 + 4 * k), py = s16(r, pa + 4 + 4 * k);
    const X = w16(Math.floor(((px * c + py * s) * 4) / 65536));
    const Y = w16(Math.floor(((py * c - px * s) * 4) / 65536));
    pts.push([w16(Math.trunc(X / div) + m.sx), w16(-Math.trunc(Y / div) + m.sy)]);
  }
  const out: [number, number, number, number, number][] = [];
  for (let k = 0, n = u8(r, la); k < n; k++) {
    const p = pts[u8(r, la + 2 + 3 * k)], q = pts[u8(r, la + 3 + 3 * k)];
    out.push([u8(r, la + 1 + 3 * k) & 0x7f, p[0], p[1], q[0], q[1]]);
  }
  return out;
}

export interface MapIsland {
  index: number;
  name: string;
  owner: number;            // $69B40: 0 neutral, 1 player, 2 enemy
  colour: number;           // palette index of the fill ($6E90[owner]: $B neutral, 7 player, 1 enemy)
  /** Vector-font label (colour 8) at zoom levels 0-5: x = left − 35/zoom, y = top − 32/zoom,
   *  scale $400/zoom; drawn whenever the island centre is within ±255 px of the map centre. */
  label: { x: number; y: number; scale: number } | null;
  /** The filled rectangle (centre ± half-extents), clipped to the map; null when off the map. */
  rect: { x0: number; y0: number; x1: number; y1: number } | null;
  markers: MapMarker[];     // zoom levels 0-2 only (see mapIslands)
}

/** $6658: the islands the map considers (centre within ±255 px of the map centre, unsigned test),
 *  what it draws for each, and the island nearest the map centre (the info box's island, [$764]
 *  after the render; null if none). Markers (zoom level <= 2, island rectangle visible): on the
 *  carrier map in islands mode ([$710] set), friendly islands only, the kind marker
 *  $B + (economy +0 & 3) at the island (shifted $1000 west on the stockpile island) and the
 *  stockpile marker $F $1000 east of it; otherwise slot 1 type $24 → runway (turned by its
 *  +2 angle) and slots 2/3 type 7 → volcano, at the island + the slot's +8/+$A. */
export function mapIslands(r: Uint8Array, screen: number): { islands: MapIsland[]; nearest: number | null } {
  const zoom = s16(r, MAP_ZOOM), level = s16(r, MAP_LEVEL);
  const vx0 = s16(r, MAP_VP), vy0 = s16(r, MAP_VP + 2), vx1 = s16(r, MAP_VP + 4), vy1 = s16(r, MAP_VP + 6);
  const cx = s16(r, MAP_VP + 0xc), cy = s16(r, MAP_VP + 0xe);
  const stock = s16(r, M_STOCKPILE), islandsMode = screen === 2 && s16(r, MAP_MODE) !== 0;
  let best = 0x1e8480, nearest: number | null = null;
  const out: MapIsland[] = [];
  for (let i = 0; i < 64; i++) {
    const a = M_ISLANDS + M_ISLAND_SIZE * i;
    const x = s32(r, a + 2), z = s32(r, a + 6), hx = s16(r, a + 0xa), hz = s16(r, a + 0xc);
    const owner = u8(r, M_OWNERS + i);
    const dx = (x - s32(r, MAP_X)) | 0, dz = (-((z - s32(r, MAP_Z)) | 0)) | 0;
    const mx = mapDiv(dx, zoom), my = mapDiv(dz, zoom);
    if ((Math.abs(mx) & 0xffff) >= 0x100 || (Math.abs(my) & 0xffff) >= 0x100) continue;
    if (mx * mx + my * my < best) { best = mx * mx + my * my; nearest = i; }
    const x0 = w16(mapDiv(dx - hx, zoom) + cx), y0 = w16(mapDiv(dz - hz, zoom) + cy);
    let x1 = w16(mapDiv(dx + hx, zoom) + cx), y1 = w16(mapDiv(dz + hz, zoom) + cy);
    const isl: MapIsland = {
      index: i, name: islandName(r, i), owner, colour: u16(r, 0x6e90 + 2 * owner),
      label: (level & 0xffff) <= 5
        ? { x: w16(x0 - Math.floor(35 / zoom)), y: w16(y0 - Math.floor(32 / zoom)), scale: Math.floor(0x400 / zoom) & 0xffff }
        : null,
      rect: null, markers: [],
    };
    out.push(isl);
    if (x1 === x0) x1 = w16(x1 + 1);
    if (y1 === y0) y1 = w16(y1 + 1);
    if (x1 < vx0 || x0 > vx1 || y1 < vy0 || y0 > vy1) continue;
    isl.rect = { x0: Math.max(x0, vx0), y0: Math.max(y0, vy0), x1: Math.min(x1, vx1), y1: Math.min(y1, vy1) };
    if (level > 2) continue;
    if (islandsMode) {
      if (owner !== 1) continue;
      const kind = u8(r, M_ECONOMY + M_ECONOMY_SIZE * i) & 3;
      isl.markers.push(placeMarker(r, "islandKind", 0xb + kind, -1, x - (i === stock ? 0x1000 : 0), z, null));
      if (i === stock) isl.markers.push(placeMarker(r, "stockpile", 0xf, -1, x + 0x1000, z, null));
      continue;
    }
    let sl = a + 0x12 + 0xc;
    if (u16(r, sl) === 0x24)
      isl.markers.push(placeMarker(r, "runway", 9, -1, x + s16(r, sl + 8), z + s16(r, sl + 0xa), u16(r, sl + 2)));
    for (const k of [2, 3]) {
      sl = a + 0x12 + 0xc * k;
      if (u16(r, sl) === 7)
        isl.markers.push(placeMarker(r, "volcano", 0xa, -1, x + s16(r, sl + 8), z + s16(r, sl + 0xa), 0));
    }
  }
  return { islands: out, nearest };
}

/** $BCFC: a world coordinate as the map prints it: sign and 4 digits of v / 1678 (`divs`,
 *  truncated; the sign overwrites the ten-thousands digit), e.g. −7405000 → "-4412". */
export function mapCoordText(v: number): string {
  const q = w16(Math.trunc(v / 0x68e));
  const mag = q < 0 ? (-q) & 0xffff : q;
  return (q < 0 ? "-" : "+") + String(mag % 100000).padStart(5, "0").slice(1);
}

export interface MapUnitLabel {
  text: string;             // "MANTA n" / "WALRUS n" (n = bay + 1)
  coords: string;           // "x,z" via mapCoordText
  x: number; y: number;     // name at marker + (22 >> level, −(22 >> level)); coords at + (12 >> level, −(10 >> level))
  coordsX: number; coordsY: number;
  scale: number;            // vector font scale $200 >> level (colour 8)
}

/** The unit pass of the map on `screen`: 25 Mantas ($6AFA), 17 Walruses ($6C36), 2 carrier ($6D74).
 *  Per bay (status +1 signed; destroyed → nothing): status >= 3 → the unit's marker at its object,
 *  turned to (heading + $800) & $F000 (16 directions); a label when the draw mode ($6AA Manta /
 *  $6B8 Walrus) is 2 (all) or 1 and the bay is the selected one ($48C / $566), at zoom level
 *  <= 1, and the marker was drawn; then, if the record's flag bit 1 (course set), the waypoint
 *  marker 4 + bay at the record's waypoint (Manta +$1E/+$26, Walrus +$1C/+$20). Then the carrier
 *  marker and the map cursor (Manta $69E/$6A6, Walrus $6B0/$6B4). On the carrier map: in course
 *  mode ([$710] = 0) the cursor $6BE/$6C2 and, with $640 bit 1, the waypoint (shape 8) at
 *  $64E/$652; then the carrier. */
export function mapUnits(r: Uint8Array, screen: number): { markers: MapMarker[]; labels: MapUnitLabel[] } {
  const markers: MapMarker[] = [], labels: MapUnitLabel[] = [];
  const level = s16(r, MAP_LEVEL);
  const objMarker = (kind: MapMarkerKind, shape: number, bay: number, o: number) =>
    placeMarker(r, kind, shape, bay, s32(r, o + 0xa), s32(r, o + 0x12), (u16(r, o + 6) + 0x800) & 0xf000);
  const carrierObj = M_OBJECTS + M_OBJECT_SIZE * 10;
  if (screen === 17 || screen === 25) {
    const manta = screen === 25;
    const mode = s16(r, manta ? 0x6aa : 0x6b8), sel = s16(r, manta ? 0x48c : 0x566);
    for (let k = 0; k < 4; k++) {
      const rec = (manta ? M_MANTAS : M_WALRUSES) + M_REC * k;
      const o = M_OBJECTS + M_OBJECT_SIZE * ((manta ? 2 : 6) + k);
      const st = s8(r, rec + 1);
      if (st < 0) continue;
      if (st >= 3) {
        const m = objMarker(manta ? "manta" : "walrus", manta ? 0 : 1, k, o);
        markers.push(m);
        if (mode && (mode === 2 || k === sel) && level <= 1 && m.drawn)
          labels.push({
            text: (manta ? "MANTA " : "WALRUS ") + (k + 1),
            coords: mapCoordText(s32(r, o + 0xa)) + "," + mapCoordText(s32(r, o + 0x12)),
            x: m.sx + (22 >> level), y: m.sy - (22 >> level),
            coordsX: m.sx + (12 >> level), coordsY: m.sy - (10 >> level), scale: 0x200 >> level,
          });
      }
      if (u8(r, rec) & 2) {
        const [wx, wz] = manta ? [rec + 0x1e, rec + 0x26] : [rec + 0x1c, rec + 0x20];
        markers.push(placeMarker(r, "waypoint", 4 + k, k, s32(r, wx), s32(r, wz), null));
      }
    }
    markers.push(objMarker("carrier", 2, -1, carrierObj));
    const [cx, cz] = manta ? [0x69e, 0x6a6] : [0x6b0, 0x6b4];
    markers.push(placeMarker(r, "cursor", 3, -1, s32(r, cx), s32(r, cz), null));
  } else if (screen === 2) {
    if (!s16(r, MAP_MODE)) {
      markers.push(placeMarker(r, "cursor", 3, -1, s32(r, 0x6be), s32(r, 0x6c2), null));
      if (u8(r, 0x640) & 2) markers.push(placeMarker(r, "carrierWaypoint", 8, -1, s32(r, 0x64e), s32(r, 0x652), null));
    }
    markers.push(objMarker("carrier", 2, -1, carrierObj));
  }
  return { markers, labels };
}

/** $68D0 (carrier map, islands mode only): supply links as drawn, once from each end (each
 *  network entry with a non-zero weight). Colour: both ends friendly 7; friendly and
 *  neutral/enemy $C; both enemy 1; neutral–neutral and neutral–enemy links are not drawn. */
export function mapLinksDrawn(r: Uint8Array): { from: number; to: number; colour: number }[] {
  const out: { from: number; to: number; colour: number }[] = [];
  for (let i = 0; i < 64; i++) {
    const n = M_NETWORK + 0x20 * i, oi = u8(r, M_OWNERS + i);
    for (let k = 0, c = u16(r, n); k < c; k++) {
      const w = u16(r, n + 2 + 4 * k), j = u16(r, n + 4 + 4 * k) & 0x7fff;
      if (!w) continue;
      const oj = u8(r, M_OWNERS + j), t = (oi + oj) & 0xff;
      let colour: number;
      if (t === 0) continue;
      if (t === 4) colour = 1;
      else if (oj === 1) colour = oi === 1 ? 7 : 0xc;
      else if (oi === 1) colour = 0xc;
      else continue;
      out.push({ from: i, to: j, colour });
    }
  }
  return out;
}

/** The far-corner easter egg ($6A9E): at zoom levels 0-5 the vector text at $76E4
 *  ("COPYRIGHT REALTIME GAMES SOFTWARE LTD 1987/88", colour 15) at world ($79BFFF, −$7FD800),
 *  when its screen point is within |sx| < $400, |sy| < $100. */
export function mapCopyright(r: Uint8Array): { text: string; x: number; y: number; scale: number } | null {
  if (u16(r, MAP_LEVEL) > 5) return null;
  const { sx, sy } = mapToScreen(r, 0x79bfff, -0x7fd800);
  if ((Math.abs(sx) & 0xffff) >= 0x400 || (Math.abs(sy) & 0xffff) >= 0x100) return null;
  return { text: cstr(r, M_COPYRIGHT), x: sx, y: sy, scale: Math.floor(0x400 / s16(r, MAP_ZOOM)) & 0xffff };
}

/** The carrier map's islands panel ($699E, [$710] = 1): friendly islands by kind, recounted by
 *  $CDF2 from the owners and the economy kind byte (+0: 1 resource, 2 factory, 3 defence), and
 *  the stockpile island ([$D4A]; "NONE!" in colour 15 when negative). Counts print as 2 digits,
 *  a leading 0 as a space. */
export function islandsPanel(r: Uint8Array): { resource: number; factory: number; defence: number; stockpile: number | null; stockpileName: string } {
  const by = [0, 0, 0, 0];
  for (let i = 0; i < 64; i++)
    if (u8(r, M_OWNERS + i) === 1) {
      const k = u8(r, M_ECONOMY + M_ECONOMY_SIZE * i);
      if (k < 4) by[k]++;
    }
  const st = s16(r, M_STOCKPILE);
  return { resource: by[1], factory: by[2], defence: by[3], stockpile: st < 0 ? null : st, stockpileName: st < 0 ? "NONE!" : islandName(r, st) };
}

/** A route figure as the info box prints it: the route word w ($67B40 + 128·from + 2·to);
 *  negative = "ISOLATED" (null); else (w << 16) / 1573 (`divu`; w >= 1573 overflows and prints 0). */
function routeFigure(r: Uint8Array, from: number, to: number): number | null {
  const w = s16(r, M_ROUTES + 0x80 * from + 2 * to);
  if (w < 0) return null;
  const q = Math.floor((w * 65536) / 0x625);
  return q > 0xffff ? 0 : q;
}

export interface IslandInfoBox {
  island: number;           // [$764]
  name: string;
  x: number; z: number;     // [$742]/[$746], printed by mapCoordText
  areaTenths: number;       // (halfX·halfZ / 10000) / 250 in tenths of a square km ("12.9 SQUARE KM")
  owner: number;            // [$74C]: "FREE" / "FRIENDLY" / "HOSTILE"
  kind: number | null;      // friendly only: economy +0 & 3: "BASE" / "RESOURCE" / "FACTORY" / "DEFENCE"
  reRouting: boolean;       // [$1235] set (non-base friendly): "DISTANCE TO BASE: RE-ROUTING", nothing after
  distanceToBase: number | "ISOLATED" | null;       // non-base friendly: route VULCAN → island
  distanceToStockpile: number | "ISOLATED" | null;  // friendly, not the stockpile, [$D4A] >= 0: route island → stockpile
  lines: string[];          // the text exactly as printed (5×5 font, colour 8, x 52, y 32 + 6n)
}

const strip = (v: number, n: number) => {
  let s = String(v & 0xffff).padStart(5, "0"), k = 0;
  while (k < n && s[k] === "0") k++;
  return s.slice(k);
};

/** $BB10: the island information box ([$766] = 1, opened by $FAF4 on the island nearest the map
 *  centre, which also centres the map on it). Reads [$764], [$742/$746] and [$74C] as the last
 *  map render left them. Title "ISLAND INFORMATION" (8×8). */
export function islandInfoBox(r: Uint8Array): IslandInfoBox {
  const i = s16(r, NEAREST), a = M_ISLANDS + M_ISLAND_SIZE * i;
  const q = Math.floor((u16(r, a + 0xa) * u16(r, a + 0xc)) / 10000);
  const area = q <= 0xffff && w16(q) >= 0 ? Math.floor(w16(q) / 250) : 0;
  const owner = u8(r, 0x74c);
  const box: IslandInfoBox = {
    island: i, name: islandName(r, i), x: s32(r, NEAREST_X), z: s32(r, NEAREST_Z), areaTenths: area, owner,
    kind: null, reRouting: false, distanceToBase: null, distanceToStockpile: null, lines: [],
  };
  const sa = strip(area, 3);
  box.lines.push("NAME:             " + box.name,
    "POSITION:         " + mapCoordText(box.x) + "," + mapCoordText(box.z),
    "AREA:             " + sa.slice(0, -1) + "." + sa.slice(-1) + " SQUARE KM",
    "CONTROL:          " + (["FREE", "FRIENDLY", "HOSTILE"][owner] ?? "?"));
  if (owner !== 1) return box;
  const kind = u8(r, M_ECONOMY + M_ECONOMY_SIZE * i) & 3;
  box.kind = kind;
  box.lines.push("ISLAND TYPE     : " + ["BASE", "RESOURCE", "FACTORY", "DEFENCE"][kind]);
  if (kind) {
    if (u8(r, 0x1235)) {
      box.reRouting = true;
      box.lines.push("DISTANCE TO BASE: RE-ROUTING");
      return box;
    }
    const d = routeFigure(r, 0, i);
    box.distanceToBase = d ?? "ISOLATED";
    box.lines.push("DISTANCE TO BASE: " + (d === null ? "ISOLATED" : strip(d, 4)));
  }
  const st = s16(r, M_STOCKPILE);
  if (i !== st && st >= 0) {
    const d = routeFigure(r, i, st);
    box.distanceToStockpile = d ?? "ISOLATED";
    box.lines.push("DISTANCE TO STOCKPILE: " + (d === null ? "ISOLATED" : strip(d, 4)));
  }
  return box;
}

/** $7712: the bottom-right slider bars, height in pixels (0-36) up from y 195, drawn 2 px wide
 *  in colour 2 at x 292 (cruise) and, on the Manta map, x 248 (altitude). Manta: altitude
 *  [$6A2].l / 97 (≤ 36), cruise [$69C] / 7 (its clamp tests a stale register, but 250/7 < 37);
 *  Walrus: (([$6AE] << 8) & $FFFF) / $2AA (≤ 36); carrier (course mode): ([$6BC] asr 2) + 8 (≤ 36).
 *  The numbers are not printed. */
export function mapSliders(r: Uint8Array, screen: number): { cruise?: number; altitude?: number } {
  if (screen === 25) {
    const q = Math.trunc(s32(r, 0x6a2) / 97);
    return {
      altitude: Math.min(q >= -0x8000 && q <= 0x7fff ? q : w16(s32(r, 0x6a2)), 36),
      cruise: Math.trunc(u16(r, 0x69c) / 7),
    };
  }
  if (screen === 17) return { cruise: Math.min(Math.trunc(((u16(r, 0x6ae) << 8) & 0xffff) / 0x2aa), 36) };
  if (screen === 2 && !s16(r, MAP_MODE)) return { cruise: Math.min(w16((s16(r, 0x6bc) >> 2) + 8), 36) };
  return {};
}

/** The world-edge frame ($68A6): 32 world points at $7564 (longs) through $7526, line list $7694
 *  (18 segments, colour 4). */
export function mapBorder(r: Uint8Array): [number, number, number, number, number][] {
  const pts: [number, number][] = [];
  for (let k = 0; k < 32; k++) {
    const p = mapToScreen(r, s32(r, M_BORDER_POINTS + 8 * k), s32(r, M_BORDER_POINTS + 8 * k + 4));
    pts.push([p.sx, p.sy]);
  }
  const out: [number, number, number, number, number][] = [];
  for (let k = 0, n = u8(r, M_BORDER_LINES); k < n; k++) {
    const p = pts[u8(r, M_BORDER_LINES + 2 + 3 * k)], q = pts[u8(r, M_BORDER_LINES + 3 + 3 * k)];
    out.push([u8(r, M_BORDER_LINES + 1 + 3 * k) & 0x7f, p[0], p[1], q[0], q[1]]);
  }
  return out;
}
