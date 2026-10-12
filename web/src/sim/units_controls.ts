/**
 * Unit layer, part of tools/stunits.py: the player's control of the carrier and the game flow
 * around sim_pass ("Sail" milestone).
 *
 * TypeScript port of tools/stunits_controls.py (the verified reference; its docstring lists
 * every command with its original handler). A browser UI calls these instead of the original's
 * mouse/keyboard handlers; each writes exactly what its handler writes (redraws, palettes and
 * sounds left out; messages become ["message", text] events). A cmd_ method returns false when
 * the original's hit test ($B46E) would not have reached the handler (panel not shown, element
 * hidden or disabled, or the game paused: only PAUSE works then). The current screen is not
 * checked: the UI decides which controls it offers.
 *
 *   raw input        input_mouse_move(dx, dy), input_mouse_button(right, down), input_key(sc, down)
 *   bridge (0)       cmd_carrier_throttle(y) $FE3C, cmd_carrier_speed_up/down() $FDCE/$FDD4,
 *                    cmd_carrier_stop() $12A26, cmd_carrier_centre_rudder() $12A16,
 *                    cmd_carrier_autopilot(held) $12BAC, cmd_toggle_mouse_steer(), cmd_steer_to(r)
 *   maps (2/17/25)   map_to_world(px, py) $F91E, world_to_map(x, z), cmd_map_click(px, py) $F8D8,
 *                    cmd_map_recentre(px, py) $F8B4, cmd_map_zoom_in/out() $F984/$F99A,
 *                    cmd_map_scroll_up/down/left/right() $FAB0/$FAC8/$FA84/$FA6C,
 *                    cmd_map_centre_unit() $F9BE, cmd_map_centre_carrier() $FA50,
 *                    cmd_map_island_info() $FAF4, cmd_map_island_info_exit() $FB1A,
 *                    cmd_carrier_set_course(held) $131A8, cmd_carrier_clear_course() $1326C,
 *                    cmd_carrier_map_mode() $1336E, cmd_carrier_cruise_up/down() $12E7A/$12E8C,
 *                    cmd_carrier_cruise(y) $12E9E, cmd_carrier_toggle_announce() $12E44,
 *                    cmd_set_carrier_waypoint(x, z)
 *   damage (1)       cmd_select_system(k) $FB3A, cmd_priorities_open() $FBBE,
 *                    cmd_priority_click(px, py) $FC64, cmd_priorities_cancel() $FC08,
 *                    cmd_priorities_exit() $FC24, cmd_set_repair_priority(k, cls)
 *   screens          ui_frame() $F636 minus the hit test (forced request $1227, pending $71A/$716),
 *                    cmd_select_tab(tab) $F844, cmd_select_screen(screen) $F880,
 *                    cmd_switch_screen(screen), cmd_game_status_menu(mode) $101F2/$10200/$101E4/$1020E,
 *                    cmd_game_status_back() $1021C, cmd_surrender() $101D8, cmd_continue(yes)
 *                    $13990/$1399C; enter $139D4 / leave $14318 handlers (docs/units.md §10.5)
 *   game flow        cmd_pause() $FD66, game_frame() ($408A loop without ui_frame/rendering),
 *                    screen_input() $17094 (screens 0, 1, 2/17/25 and the empty handlers)
 */
import { NotPorted, divu, pyDiv, s8, s16, s32, u32, w16 } from "./mem.ts";
import type { Region } from "./mem.ts";
import { ISLAND_TABLE, ISLAND_SIZE, SYSTEMS } from "./world.ts";
import {
  CARRIER, C_CRUISE, C_DEPTH, C_FLAGS, C_RUDDER, C_SUBSTATE, C_TARGET, C_WAYPOINT_X, C_WAYPOINT_Z, KEY_SPEED_DOWN,
  KEY_SPEED_UP, MOUSE_DX, OBJECTS, OBJECT_SIZE, POINTER_MODE, SCREEN, UNIT_INPUT_REGIONS, UNIT_REGIONS, VIEWED,
} from "./units_core.ts";
import { SetupUnits } from "./units_setup.ts";

export const FLAG_TABLE = 0x40eb2; // panel element flags, word per type: 15 hidden, 14 disabled,
//                                    13 highlight, 12 works while paused, 5 selected
export const RUNNING = 0x462, MENU = 0x461, MODAL = 0x460;
export const END_REQUEST = 0x463, RESULT = 0x466, END_TIMER = 0x732;
export const SCREEN_REQUEST = 0x1227, REQUEST_TAB = 0x1228;
export const TAB = 0x714, END_TAB = 0x730;
export const LAST_CHAR = 0x1270, CHEAT_PTR = 0x116e, CHEAT_CODE = 0x1172, DEBUG = 0x1189, CHEAT = 0x118a;
export const KEYS = 0x798; // 2 bytes per scancode: held, new
export const MAP_RECT = 0x47f2; // x0 y0 x1 y1, +$C/+$E centre pixel
export const MAP_ZOOM_LEVEL = 0x758, MAP_ZOOM = 0x75a, MAP_X = 0x75c, MAP_Z = 0x760, MAP_ISLAND = 0x764, MAP_INFO = 0x766;
export const CARRIER_MAP_MODE = 0x710;
export const CURSORS: Record<number, [number, number]> = { 25: [0x69e, 0x6a6], 17: [0x6b0, 0x6b4] };
export const CURSOR_CARRIER: [number, number] = [0x6be, 0x6c2];
export const CRUISE = 0x6bc, ANNOUNCE = 0x6c6;
export const PRIORITIES = 0xb44, PRIORITY_PANEL = 0xb62, SYSTEM_SHOWN = 0xb58, PART_SHOWN = 0xb5a;
export const T_AUTOPILOT = 90, T_ANNOUNCE = 92, T_PAUSE = 99, T_SYSTEM = 48;
export const PRIORITY_GRID: [number, number, number, number] = [200, 16, 200 + 0x53, 16 + 0x63];
export const THROTTLE_Y: [number, number] = [159, 159 + 0x24]; // bridge and carrier-map speed sliders
export const MAP_KEYPAD = [0x79d, 0x873, 0x79f, 0x875, 0x7a1, 0x877, 0x7a3, 0x86d];
export const EDGE_FLAGS = [0x883, 0x79d, 0x79f, 0x7a1, 0x7a3, 0x873, 0x875, 0x877, 0x86d, 0x83d, 0x827];
export const PENDING_TAB = 0x716, SCREEN_SELECT = 0x71a, PANEL_LIST = 0x71c, TAB_SCREENS = 0x720;
export const CAMERA = 0x9fa, CONTROLLED = 0xa02, CAM_MODE = 0xa14, VIEWPORT = 0x6fe;
export const GAME_STATUS = 0x708; // game-status screen sub-mode: 0 menu, 1 format, 3 save, 5 error, 6 surrender, 7 options
export const STATION_SNAPSHOT = 0x141de; // enter 11 copies the drone stations' offsets here (in the code)
export const MODEL_LIST = 0x64cfe; // the damage diagram / title model list object 0 points at
export const T_TAB = 32, T_STATUS_MENU = 100; // tab icons 32 + tab; game-status buttons 100-103
// The screens of each tab (the left icon column: tab icon type 32 + tab); tabs 5-7 have no icon.
export const TAB_OF: Record<number, number> = { 32: 0, 33: 5, 34: 6, 35: 7 };
for (let s = 0; s < 29; s++) if (s % 8 < 5) TAB_OF[s] = 1 + Math.floor(s / 8);
// $43856: each screen's panel list ($71C).
export const PANEL_LISTS = [
  0x43606, 0x43620, 0x43636, 0x43660, 0x4368a, 0x43604, 0x43604, 0x43604, 0x4369c, 0x436ae,
  0x436c0, 0x436d6, 0x436ec, 0x43604, 0x43604, 0x43604, 0x436fe, 0x4371c, 0x4373e, 0x43764,
  0x43776, 0x43604, 0x43604, 0x43604, 0x43788, 0x437a6, 0x437c8, 0x437da, 0x43804, 0x43604,
  0x43604, 0x43604, 0x43816, 0x4383c, 0x4384a, 0x43850,
];
export const DAMAGE_PARTS = [0x4b, 0x4c, 0x4d, 0x4e, 0x4f, 0x50, 0x51, 0x52]; // $14148: the damage diagram's models
export const SUBPANEL = [0, 1, 1, 2]; // $11756: arming sub-panel by bay status (0, 1-2, 3+)
export const STATUS_ICON = [0, 3, 3, 2, 2, 0, 1, 4]; // $13B6C: game-status button lit per [$708] (1 + index from 100)

// State these routines read or write that the other regions don't cover.
for (const _region of [
  ["game flags $460", MODAL, 3], // modal pointer, menu open, running
  ["current tab $714", TAB, 2],
  ["panel overlay $702", 0x702, 2],
  ["end screen tab $730", END_TAB, 2],
  ["carrier map mode $710", CARRIER_MAP_MODE, 2],
  ["screen select $71A", 0x71a, 2],
  ["logistics pass $8FE", 0x8fe, 1],
  ["joystick $888", 0x888, 1],
  ["screen request $1227", SCREEN_REQUEST, 1],
  ["screen request tab $1228", REQUEST_TAB, 2],
  ["palette cycle $1231", 0x1231, 1],
  ["last key $1270", LAST_CHAR, 1],
  ["key characters $1280", 0x1280, 0x80],
  ["cheat code $116E", CHEAT_PTR, 0x1188 - CHEAT_PTR],
  // display state the UI code also changes: check_units.diff skips "redraw" regions
  ["redraw: panel flags $40EB2", FLAG_TABLE, 0xd4],
  ["map view $758", MAP_ZOOM_LEVEL, 0x10],
  ["map island $742", 0x742, 8],
  ["map rect $47F2", MAP_RECT, 0x10],
  ["map cursors $69C", 0x69c, 0x6c8 - 0x69c],
  ["priority panel $B62", PRIORITY_PANEL, 4],
  // Screen switching (ui_frame $F636, enter $139D4 and leave $14318 handlers).
  ["panel states $704", 0x704, 6], // tab icons shown, screen icons shown, game-status mode $708
  ["vehicle controllable $70C", 0x70c, 4], // $70C Manta, $70E Walrus
  ["pending tab $716", PENDING_TAB, 2],
  ["panel list $71C", PANEL_LIST, 4],
  ["tab screens $720", TAB_SCREENS, 0x10],
  ["viewport $6FE", VIEWPORT, 4],
  ["frame lock $8F0", 0x8f0, 1],
  ["camera mode $A14", CAM_MODE, 2],
  ["turret zoom view $A94", 0xa94, 8],
  ["vehicle view $1230", 0x1230, 1],
  ["screen flag $122D", 0x122d, 1],
  ["selected Manta $48C", 0x48c, 2],
  ["bay selection $DB4", 0xdb4, 4],
  ["station snapshot $141DE", STATION_SNAPSHOT, 0x10],
] as Region[]) {
  if (!UNIT_REGIONS.some((r) => r[0] === _region[0])) UNIT_REGIONS.push(_region);
}
for (const n of ["current tab $714", "panel overlay $702", "key characters $1280", "map island $742", "map rect $47F2"]) {
  UNIT_INPUT_REGIONS.add(n);
}

// $170D4, the per-screen input handlers (screen: address; the rest are $170D2, rts).
export const INPUT_TABLE: Record<number, number> = {
  0: 0x17164, 1: 0x1747a, 2: 0x17244, 3: 0x177de, 8: 0x1741a, 10: 0x1778e, 16: 0x17748,
  17: 0x17244, 18: 0x17374, 19: 0x17782, 24: 0x17610, 25: 0x17244, 26: 0x1773c, 27: 0x172bc,
};
// Screens whose input handler is ported ("" = $170D2, rts).
export const SCREEN_INPUT: Record<number, string> = { 0: "bridge_input", 1: "damage_input", 2: "map_input", 17: "map_input", 25: "map_input" };
for (let s = 0; s < 36; s++) if (!(s in INPUT_TABLE)) SCREEN_INPUT[s] = "";
// $139D4 / $14318: the enter and leave handlers that write state (the others only set the
// palette or nothing: enter 3, 5-7, 12-15, 20-23, 28-31; leave 3-7, 9, 11-15, 19-23, 26, 28-31).
export const SCREEN_ENTER: Record<number, string> = {
  0: "enter_bridge", 1: "enter_damage", 2: "enter_carrier_map", 4: "enter_messages",
  8: "enter_turret", 9: "enter_flares", 10: "enter_drone_view", 11: "enter_drone_radar",
  16: "enter_walrus_arming", 17: "enter_walrus_map", 18: "enter_walrus_control",
  19: "enter_walrus_dock", 24: "enter_manta_arming", 25: "enter_manta_map",
  26: "enter_manta_deck", 27: "enter_manta_control", 32: "enter_game_status",
  33: "enter_title", 34: "enter_game_over", 35: "enter_destroyed",
};
export const SCREEN_LEAVE: Record<number, string> = {
  0: "leave_bridge", 1: "leave_damage", 2: "leave_carrier_map", 8: "leave_turret",
  10: "leave_drone_view", 16: "leave_walrus_arming", 17: "leave_walrus_map",
  18: "leave_vehicle_control", 24: "leave_bridge", 25: "leave_manta_map",
  27: "leave_vehicle_control", 32: "leave_game_status", 33: "leave_title",
  34: "leave_game_over", 35: "leave_destroyed",
};

export abstract class ControlsUnits extends SetupUnits {
  // ---- the hit test's conditions ($B46E) ------------------------------------------------
  /** The panel is shown ([state addr] == value; null: always), the element type is
   * neither hidden nor disabled, and the game runs unless the type works while paused
   * (bit 12). */
  _clickable(etype: number, state: [number, number] | null = [0x702, 0]): boolean {
    const m = this.mem;
    if (state !== null && m.u16(state[0]) !== state[1]) return false;
    const f = m.u16(FLAG_TABLE + 2 * etype);
    if (f & 0xc000) return false;
    return Boolean(m.u8(RUNNING) || f & 0x1000);
  }

  _message(text: string): void {
    this.events.push(["message", text]);
  }

  /** bclr.b #0: clears bit 0 and says whether it was set. */
  _bclr0(a: number): boolean {
    const v = this.mem.u8(a);
    this.mem.set8(a, v & 0xfe);
    return Boolean(v & 1);
  }

  // ---- raw input (IKBD $17D80) -----------------------------------------------------------
  /** A mouse packet: the deltas add up in $1268/$126A until a routine consumes them
   * (carrier_step in mouse-steer mode on screen 0, the turret, the vehicles). */
  input_mouse_move(dx: number, dy: number): void {
    const m = this.mem;
    m.set16(MOUSE_DX, m.u16(MOUSE_DX) + dx);
    m.set16(MOUSE_DX + 2, m.u16(MOUSE_DX + 2) + dy);
  }

  /** A press sets held and new ($0101) unless already held; a release clears held. */
  input_mouse_button(right: number, down: number): void {
    const m = this.mem;
    const a = right ? 0x882 : 0x880;
    if (!down) m.set8(a, 0);
    else if (!m.u8(a)) {
      m.set16(a, 0x101);
      m.set8(0x889, 0xff);
    }
  }

  /** A key press sets held and new and the last character $1270; a release clears held. */
  input_key(scancode: number, down: number): void {
    const m = this.mem;
    const a = KEYS + 2 * (scancode & 0x7f);
    if (!down) {
      m.set8(a, 0);
      return;
    }
    m.set16(a, 0x101);
    m.set8(0x889, 0xff);
    const c = m.u8(0x1280 + (scancode & 0x7f));
    if (0x31 <= c && c <= 0x33) m.set8(0x1273, 0xff);
    m.set8(LAST_CHAR, c);
  }

  // ---- bridge, screen 0 ------------------------------------------------------------------
  /** $FE3C: the throttle slider (x 288..295, y 159..195): target speed (187 - y) * 4,
   * 112 full ahead to -32 full astern, unless the autopilot steers. Works on auto-repeat,
   * so dragging it applies every pass. */
  cmd_carrier_throttle(y: number): boolean {
    const m = this.mem;
    if (!(THROTTLE_Y[0] <= y && y <= THROTTLE_Y[1] && this._clickable(38))) return false;
    if (!(m.u8(C_FLAGS) & 1)) m.set16(C_TARGET, w16((0xbb - y) * 4));
    return true;
  }

  /** $FDCE: the up chevron sets $88A; carrier_step makes the target speed + 1 and
   * vehicles_step clears it (hold it by calling this every pass). */
  cmd_carrier_speed_up(): boolean {
    if (!this._clickable(38)) return false;
    this.mem.set8(KEY_SPEED_UP, 0xff);
    return true;
  }

  /** $FDD4: the down chevron, $88B. */
  cmd_carrier_speed_down(): boolean {
    if (!this._clickable(38)) return false;
    this.mem.set8(KEY_SPEED_DOWN, 0xff);
    return true;
  }

  /** $12A26: target speed 0 unless the autopilot steers. */
  cmd_carrier_stop(): boolean {
    const m = this.mem;
    if (!this._clickable(38)) return false;
    if (!(m.u8(C_FLAGS) & 1)) m.set16(C_TARGET, 0);
    return true;
  }

  /** $12A16: $640 bit 7, the rudder self-centres (carrier_move) until mouse steering
   * moves it again; not under autopilot. */
  cmd_carrier_centre_rudder(): boolean {
    const m = this.mem;
    if (!this._clickable(38)) return false;
    if (!(m.u8(C_FLAGS) & 1)) m.set8(C_FLAGS, m.u8(C_FLAGS) | 0x80);
    return true;
  }

  /** $12C16: the auto button's selected bit (type 90) follows $640 bit 0. */
  _sync_auto_button(): void {
    this.mem.set16(FLAG_TABLE + 2 * T_AUTOPILOT, this.mem.u8(C_FLAGS) & 1 ? 0x2000 : 0);
  }

  /** $12BAC (fresh press only): with the carrier under way ($641 = 1), the auto button
   * (its selected bit, which the bridge input keeps equal to $640 bit 0) turns the
   * autopilot off, or on if a course is set ($640 bit 1). */
  cmd_carrier_autopilot(held = 0): boolean {
    const m = this.mem;
    if (!this._clickable(T_AUTOPILOT)) return false;
    if (held) return true;
    if (m.u8(C_SUBSTATE) !== 1) return true;
    const flag = FLAG_TABLE + 2 * T_AUTOPILOT;
    if (m.u8(flag) & 0x20) {
      m.set8(flag, m.u8(flag) & 0xdf);
      m.set8(C_FLAGS, m.u8(C_FLAGS) & 0xfe);
    } else if (m.u8(C_FLAGS) & 2) {
      m.set8(flag, m.u8(flag) | 0x20);
      m.set8(C_FLAGS, m.u8(C_FLAGS) | 1);
      this._message("Autopilot on");
    } else {
      this._message("No course set");
      return true;
    }
    this._sync_auto_button();
    return true;
  }

  /** A right click (press and release): on screen 0 the next screen_input toggles
   * mouse-steer mode ($457) unless the autopilot is on; on a map it recentres there. */
  cmd_toggle_mouse_steer(): void {
    this.input_mouse_button(1, 1);
    this.input_mouse_button(1, 0);
  }

  /** Mouse-steer input that makes the next carrier_step set the rudder $642 to
   * `rudder` (clamped to +-255): it adds the delta to $642. Only on screen 0 in
   * mouse-steer mode ($457 clear) without autopilot; vehicles_step drops it otherwise. */
  cmd_steer_to(rudder: number): void {
    const m = this.mem;
    const r = Math.max(-255, Math.min(255, rudder));
    m.set16(MOUSE_DX, w16(r - m.s16(C_RUDDER)));
  }

  // ---- maps, screens 2/17/25 ---------------------------------------------------------------
  /** $F91E: the map pixel (px, py) as world (x, z), or null outside the map or beyond
   * +-$7FFFFF. x = (px - cx) * zoom * 256 + [$75C], z = -(py - cy) * zoom * 256 + [$760]. */
  map_to_world(px: number, py: number): [number, number] | null {
    const m = this.mem;
    px = s16(px);
    py = s16(py);
    if (px < m.s16(MAP_RECT) || px > m.s16(MAP_RECT + 4) || py < m.s16(MAP_RECT + 2) || py > m.s16(MAP_RECT + 6)) {
      return null;
    }
    const zoom = m.s16(MAP_ZOOM);
    let x = s32(s16(px - m.u16(MAP_RECT + 0xc)) * zoom * 256);
    let z = s32(-s32(s16(py - m.u16(MAP_RECT + 0xe)) * zoom * 256));
    x = s32(x + m.u32(MAP_X));
    z = s32(z + m.u32(MAP_Z));
    if (!(-0x7fffff <= x && x <= 0x7fffff) || !(-0x7fffff <= z && z <= 0x7fffff)) return null;
    return [x, z];
  }

  /** The map pixel nearest to world (x, z) (the inverse of map_to_world), or null if
   * it is off the map. */
  world_to_map(x: number, z: number): [number, number] | null {
    const m = this.mem;
    const step = m.s16(MAP_ZOOM) * 256;
    if (step <= 0) return null;
    const px = m.s16(MAP_RECT + 0xc) + pyDiv(s32(x - m.u32(MAP_X)) + pyDiv(step, 2), step);
    const py = m.s16(MAP_RECT + 0xe) - pyDiv(s32(z - m.u32(MAP_Z)) + pyDiv(step, 2), step);
    if (px < m.s16(MAP_RECT) || px > m.s16(MAP_RECT + 4) || py < m.s16(MAP_RECT + 2) || py > m.s16(MAP_RECT + 6)) {
      return null;
    }
    return [px, py];
  }

  _set_pointer(px: number, py: number): void {
    this.mem.set16(0x474, px);
    this.mem.set16(0x476, py);
  }

  /** $F8D8 (map element at (32, 6), 256 x 128; works on auto-repeat): the pointer moves
   * to (px, py) and the map point under it becomes the cursor of the current map: the
   * Manta map (screen 25) $69E/$6A6, the Walrus map (17) $6B0/$6B4, otherwise the
   * carrier's $6BE/$6C2 while its map shows the course panel ($710 = 0). */
  cmd_map_click(px: number, py: number): boolean {
    const m = this.mem;
    if (!(32 <= px && px <= 32 + 0xff && 6 <= py && py <= 6 + 0x7f && this._clickable(38, [MAP_INFO, 0]))) return false;
    this._set_pointer(px, py);
    const p = this.map_to_world(px, py);
    if (p === null) return true;
    const screen = m.u16(SCREEN);
    let ax: number, az: number;
    if (screen in CURSORS) [ax, az] = CURSORS[screen];
    else if (m.u16(CARRIER_MAP_MODE) === 0) [ax, az] = CURSOR_CARRIER;
    else return true;
    m.set32(ax, p[0]);
    m.set32(az, p[1]);
    return true;
  }

  /** A right click on a map at (px, py): screen_input calls $F8B4 (centre the map on
   * that point, zoom in one step, pointer to the map's centre). Here directly, as the
   * next screen_input on screens 2/17/25 would (pointer moved, right click consumed). */
  cmd_map_recentre(px: number, py: number): void {
    this._set_pointer(px, py);
    if (this.mem.u16(MAP_INFO) === 0) this.map_recentre_at_pointer();
  }

  /** $F8B4. */
  map_recentre_at_pointer(): void {
    const m = this.mem;
    const p = this.map_to_world(m.u16(0x474), m.u16(0x476));
    if (p === null) return;
    m.set32(MAP_X, p[0]);
    m.set32(MAP_Z, p[1]);
    this.map_zoom_in();
    m.set16(0x474, m.u16(MAP_RECT + 0xc));
    m.set16(0x476, m.u16(MAP_RECT + 0xe));
  }

  /** $F9B0: zoom level $758 and factor $75A = 1 << level. */
  _map_zoom_set(level: number): void {
    this.mem.set16(MAP_ZOOM_LEVEL, level);
    this.mem.set16(MAP_ZOOM, (level & 63) < 16 ? 1 << (level & 63) : 0);
  }

  /** $F984. */
  map_zoom_in(): void {
    const m = this.mem;
    if (m.u16(MAP_INFO) || m.u16(MAP_ZOOM_LEVEL) === 0) return;
    this._map_zoom_set(w16(m.u16(MAP_ZOOM_LEVEL) - 1));
  }

  /** $F99A. */
  map_zoom_out(): void {
    const m = this.mem;
    if (m.u16(MAP_INFO) || m.u16(MAP_ZOOM_LEVEL) === 10) return;
    this._map_zoom_set(w16(m.u16(MAP_ZOOM_LEVEL) + 1));
  }

  /** $FA6C/$FA84/$FAB0/$FAC8: the map centre moves by zoom * $400, within +-$7FFFFF. */
  _map_scroll(a: number, sign: number): void {
    const m = this.mem;
    if (m.u16(MAP_INFO)) return;
    const v = s32(m.u32(a) + sign * m.u16(MAP_ZOOM) * 0x400);
    if (-0x7fffff <= v && v <= 0x7fffff) m.set32(a, v);
  }

  map_scroll_up(): void {
    this._map_scroll(MAP_Z, 1);
  }

  map_scroll_down(): void {
    this._map_scroll(MAP_Z, -1);
  }

  map_scroll_left(): void {
    this._map_scroll(MAP_X, -1);
  }

  map_scroll_right(): void {
    this._map_scroll(MAP_X, 1);
  }

  cmd_map_zoom_in(): boolean {
    if (!this._clickable(38)) return false;
    this.map_zoom_in();
    return true;
  }

  cmd_map_zoom_out(): boolean {
    if (!this._clickable(38)) return false;
    this.map_zoom_out();
    return true;
  }

  cmd_map_scroll_up(): boolean {
    if (!this._clickable(40)) return false;
    this.map_scroll_up();
    return true;
  }

  cmd_map_scroll_down(): boolean {
    if (!this._clickable(42)) return false;
    this.map_scroll_down();
    return true;
  }

  cmd_map_scroll_left(): boolean {
    if (!this._clickable(43)) return false;
    this.map_scroll_left();
    return true;
  }

  cmd_map_scroll_right(): boolean {
    if (!this._clickable(41)) return false;
    this.map_scroll_right();
    return true;
  }

  /** $F9E8: centre on (x, z), zoom level at most 1. */
  _map_centre_on(x: number, z: number): void {
    const m = this.mem;
    m.set32(MAP_X, x);
    m.set32(MAP_Z, z);
    const level = m.u16(MAP_ZOOM_LEVEL);
    this._map_zoom_set(1 <= s16(level) ? 1 : level);
  }

  /** $FA56: the carrier, zoom level 1. */
  _map_centre_carrier(): void {
    const m = this.mem;
    m.set32(MAP_X, m.u32(CARRIER + 0x0a));
    m.set32(MAP_Z, m.u32(CARRIER + 0x12));
    this._map_zoom_set(1);
  }

  /** $F9BE: centre on the selected Manta (screen 25) or Walrus (17) if it is out,
   * else on the carrier; on the carrier map on the carrier, or on the island [$D4A]
   * in the second sub-panel ($710 = 1). */
  cmd_map_centre_unit(): boolean {
    const m = this.mem;
    if (!this._clickable(38)) return false;
    if (m.u16(MAP_INFO)) return true;
    const screen = m.u16(SCREEN);
    let o: number;
    if (screen === 0x19 || screen === 0x11) {
      const [rec, obj] = screen === 0x19 ? [0x48e, 0x492] : [0x568, 0x56c];
      const status = m.u8(m.u32(rec) + 1);
      if (status & 0x80) return true;
      if (status < 8) {
        this._map_centre_carrier();
        return true;
      }
      o = m.u32(obj);
    } else if (m.u16(CARRIER_MAP_MODE)) {
      const k = m.u16(0xd4a);
      if (k & 0x8000) return true;
      const isl = ISLAND_TABLE + k * ISLAND_SIZE;
      this._map_centre_on(m.u32(isl + 2), m.u32(isl + 6));
      return true;
    } else {
      o = CARRIER;
    }
    this._map_centre_on(m.u32(o + 0x0a), m.u32(o + 0x12));
    return true;
  }

  /** $FA50 (Walrus map): centre on the carrier, zoom level 1. */
  cmd_map_centre_carrier(): boolean {
    if (!this._clickable(38)) return false;
    if (!this.mem.u16(MAP_INFO)) this._map_centre_carrier();
    return true;
  }

  /** $FAF4: show the island under the map cursor ([$764], position $742/$746). */
  cmd_map_island_info(): boolean {
    const m = this.mem;
    if (!this._clickable(38)) return false;
    if (m.u16(MAP_INFO) || m.u16(MAP_ISLAND) & 0x8000) return true;
    m.set32(MAP_X, m.u32(0x742));
    m.set32(MAP_Z, m.u32(0x746));
    m.set16(MAP_INFO, 1);
    return true;
  }

  /** $FB1A: EXIT of the island panel. */
  cmd_map_island_info_exit(): boolean {
    if (!this._clickable(38, [MAP_INFO, 1])) return false;
    this.mem.set16(MAP_INFO, 0);
    return true;
  }

  /** $131A8, the carrier map's course button. A fresh press sets the course: waypoint
   * $64E/$652 from the cursor $6BE/$6C2, cruise speed $656 from $6BC, $640 bit 1, and
   * bit 2 (report arrival) from $6C6. Held (auto-repeat), it engages the autopilot. */
  cmd_carrier_set_course(held = 0): boolean {
    const m = this.mem;
    if (!this._clickable(38, [CARRIER_MAP_MODE, 0])) return false;
    if (m.u16(MAP_INFO)) return true;
    let f = m.u8(C_FLAGS);
    if (held) {
      if (!(f & 2) || f & 1) return true;
      m.set8(C_FLAGS, f | 1);
      this._message("Carrier autopilot engaged");
      return true;
    }
    f = (f | 2) & ~4;
    if (m.u8(ANNOUNCE)) f |= 4;
    m.set8(C_FLAGS, f);
    m.set16(C_CRUISE, m.u16(CRUISE));
    m.set32(C_WAYPOINT_X, m.u32(CURSOR_CARRIER[0]));
    m.set32(C_WAYPOINT_Z, m.u32(CURSOR_CARRIER[1]));
    this._message(f & 1 ? "Carrier course set" : "Carrier course set - engage autopilot");
    return true;
  }

  /** $1326C: clears $640 bit 1 (the autopilot then stops the carrier). */
  cmd_carrier_clear_course(): boolean {
    const m = this.mem;
    if (!this._clickable(38, [CARRIER_MAP_MODE, 0])) return false;
    if (m.u16(MAP_INFO)) return true;
    if (m.u8(C_FLAGS) & 2) {
      m.set8(C_FLAGS, m.u8(C_FLAGS) & 0xfd);
      this._message("Carrier course cleared");
    } else {
      this._message("Carrier has no course to clear");
    }
    return true;
  }

  /** $1336E: switch the carrier map's sub-panel ($710: 0 course, 1 islands) and
   * request the screen again ($71A). */
  cmd_carrier_map_mode(): boolean {
    const m = this.mem;
    if (!this._clickable(38, null)) return false;
    if (!m.u16(MAP_INFO)) {
      m.set16(CARRIER_MAP_MODE, m.u16(CARRIER_MAP_MODE) ^ 1);
      m.set16(0x71a, m.u16(SCREEN));
    }
    return true;
  }

  /** $12EB0: cruise speed $6BC, 0..$70. */
  _set_cruise(v: number): void {
    v = s16(v);
    this.mem.set16(CRUISE, v > 0x70 ? 0x70 : v < 0 ? 0 : v);
  }

  /** $12E7A: cruise speed + 8 (auto-repeats). */
  cmd_carrier_cruise_up(): boolean {
    if (!this._clickable(38, [CARRIER_MAP_MODE, 0])) return false;
    if (!this.mem.u16(MAP_INFO)) this._set_cruise(this.mem.u16(CRUISE) + 8);
    return true;
  }

  /** $12E8C: cruise speed - 8. */
  cmd_carrier_cruise_down(): boolean {
    if (!this._clickable(38, [CARRIER_MAP_MODE, 0])) return false;
    if (!this.mem.u16(MAP_INFO)) this._set_cruise(this.mem.u16(CRUISE) - 8);
    return true;
  }

  /** $12E9E: the cruise slider (y 159..195): (187 - y) * 4, clamped to 0..$70. */
  cmd_carrier_cruise(y: number): boolean {
    if (!(THROTTLE_Y[0] <= y && y <= THROTTLE_Y[1] && this._clickable(38, [CARRIER_MAP_MODE, 0]))) return false;
    if (!this.mem.u16(MAP_INFO)) this._set_cruise((0xbb - y) * 4);
    return true;
  }

  /** $12E44: report arrival at the waypoint ($6C6; set course copies it to $640 bit 2).
   * No fresh-press check: holding it toggles every pass. */
  cmd_carrier_toggle_announce(): boolean {
    const m = this.mem;
    if (!this._clickable(T_ANNOUNCE, [CARRIER_MAP_MODE, 0])) return false;
    if (!m.u16(MAP_INFO)) {
      m.set8(ANNOUNCE, ~m.u8(ANNOUNCE));
      m.set16(FLAG_TABLE + 2 * T_ANNOUNCE, m.u8(ANNOUNCE) ? 0x2000 : 0);
    }
    return true;
  }

  /** The UI's "sail to (x, z)": the carrier map cursor $6BE/$6C2 at world (x, z), as a
   * map click there writes it, then a fresh press of set course. The original's map
   * only produces points on its zoom grid ($75C + k * zoom * 256); any x/z within
   * +-$7FFFFF is accepted here. Engage the autopilot with cmd_carrier_autopilot() or
   * cmd_carrier_set_course(1). */
  cmd_set_carrier_waypoint(x: number, z: number): boolean {
    const m = this.mem;
    if (!(this._clickable(38, [MAP_INFO, 0]) && m.u16(CARRIER_MAP_MODE) === 0)) return false;
    m.set32(CURSOR_CARRIER[0], s32(x));
    m.set32(CURSOR_CARRIER[1], s32(z));
    return this.cmd_carrier_set_course(0);
  }

  // ---- damage screen 1 ------------------------------------------------------------------
  /** $FB3A: show system k (0-9) on the damage screen: $B58 (and the lift's part cycle
   * $B5A from 0); the 3D highlight bits at $64D50 are left out (display only). */
  cmd_select_system(k: number): boolean {
    const m = this.mem;
    if (!(0 <= k && k <= 9 && this._clickable(T_SYSTEM + k, [0xb64, 0]))) return false;
    const a = FLAG_TABLE + 2 * (T_SYSTEM + k);
    if (m.u8(a) & 0x20) return true;
    m.set8(a, m.u8(a) | 0x20);
    const old = m.s16(SYSTEM_SHOWN);
    if (old >= 0) {
      const b = FLAG_TABLE + s16(2 * old + 0x60); // bclr #5,(a1,d0.w)
      m.set8(b, m.u8(b) & 0xdf);
    }
    m.set16(SYSTEM_SHOWN, k);
    if (k === 8) m.set16(PART_SHOWN, 0);
    return true;
  }

  /** $FBF2: the classes (+4 of each $AF4 record) into the work table $B44. */
  _copy_priorities(): void {
    const m = this.mem;
    for (let k = 0; k < 10; k++) m.set16(PRIORITIES + 2 * k, m.u16(SYSTEMS + 8 * k + 4));
  }

  /** $FBBE: the set-priorities panel ($B62 = $B64 = 1) on a copy of the classes. */
  cmd_priorities_open(): boolean {
    const m = this.mem;
    if (!this._clickable(38, [0xb64, 0])) return false;
    this._copy_priorities();
    m.set16(PRIORITY_PANEL, 1);
    m.set16(PRIORITY_PANEL + 2, 1);
    return true;
  }

  /** $FC64: the grid at (200, 16), 84 x 100: row (py - 16) / 10 is the system, column
   * (px - 200) / 28 its class (0-2) in the work table. */
  cmd_priority_click(px: number, py: number): boolean {
    const m = this.mem;
    if (!(PRIORITY_GRID[0] <= px && px <= PRIORITY_GRID[2] && PRIORITY_GRID[1] <= py && py <= PRIORITY_GRID[3] &&
      this._clickable(38, [PRIORITY_PANEL, 1]))) return false;
    const row = divu(s16(py - 16), 10) & 0xffff;
    const col = divu(s16(px - 200), 0x1c) & 0xffff;
    m.set16(PRIORITIES + s16(w16(2 * row)), col);
    return true;
  }

  /** $FC08: CANCEL, the work table back to the current classes. */
  cmd_priorities_cancel(): boolean {
    if (!this._clickable(38, [PRIORITY_PANEL, 1])) return false;
    this._copy_priorities();
    return true;
  }

  /** $FC24: EXIT, the work table becomes the classes; the panel closes. */
  cmd_priorities_exit(): boolean {
    const m = this.mem;
    if (!this._clickable(38, [PRIORITY_PANEL, 1])) return false;
    for (let k = 0; k < 10; k++) m.set16(SYSTEMS + 8 * k + 4, m.u16(PRIORITIES + 2 * k));
    m.set16(PRIORITY_PANEL, 0);
    m.set16(PRIORITY_PANEL + 2, 0);
    return true;
  }

  /** System k (0-9) to repair class cls (0-2) the way a player does it: open the
   * panel, click its row and column, EXIT. */
  cmd_set_repair_priority(k: number, cls: number): boolean {
    if (!(0 <= k && k <= 9 && 0 <= cls && cls <= 2)) return false;
    if (this.mem.u16(PRIORITY_PANEL) !== 1 && !this.cmd_priorities_open()) return false;
    this.cmd_priority_click(PRIORITY_GRID[0] + 28 * cls, PRIORITY_GRID[1] + 10 * k);
    return this.cmd_priorities_exit();
  }

  // ---- screen switching (ui_frame $F636) -----------------------------------------------
  /** $F636 without the hit test (the UI calls the cmd_ methods for clicks): a forced
   * tab request ($1227, tab [$1228]: new game, game over, continue) is taken first;
   * otherwise the pointer button state ($17EEA), and unless a drag is in progress
   * ($460), the pending screen $71A or tab $716. Call it once per pass before
   * game_frame, after the commands. */
  ui_frame(): void {
    const m = this.mem;
    if (m.u8(SCREEN_REQUEST)) {
      this._take_request();
      return;
    }
    this.pointer_button();
    if (m.u8(MODAL)) {
      if (m.u8(0x880)) return;
      throw new NotPorted("the drop at the end of a drag ($F6EA: jmp [$468])");
    }
    this._switch_pending();
  }

  /** Replaces SetupUnits' (which ports only the bridge): after new_game, the forced
   * request $42A8 leaves (tab 1, the bridge), as ui_frame takes it. */
  new_game_screen(): void {
    if (this.mem.u8(SCREEN_REQUEST)) this._take_request();
  }

  /** $17EEA: in pointer mode the left button becomes the click state $45A (2 a new
   * press, consuming $881; 1 when held for 6 passes; else 0). */
  pointer_button(): void {
    const m = this.mem;
    if (!m.u8(POINTER_MODE)) return;
    if (this._bclr0(0x881)) {
      m.set16(0x45a, 2);
      m.set16(0x45c, 0);
      m.set16(0x45e, 0);
      return;
    }
    m.set16(0x45a, 0);
    if (!m.u8(0x880)) return;
    if (m.s16(0x45c) < 6) {
      m.set16(0x45c, m.u16(0x45c) + 1);
    } else {
      m.set16(0x45a, 1);
      m.set16(0x45e, 0);
    }
  }

  /** $F63C: the forced request: tab [$1228]. */
  _take_request(): void {
    const m = this.mem;
    m.set8(SCREEN_REQUEST, 0);
    this._switch_tab(m.u16(REQUEST_TAB));
  }

  /** $F67A, the rest of ui_frame after a click handler: a forced request it made,
   * else the pending screen or tab. */
  _after_click(): void {
    if (this.mem.u8(SCREEN_REQUEST)) this._take_request();
    else this._switch_pending();
  }

  /** $F68C: a pending screen $71A (in the current tab, which remembers it in $720),
   * else a pending tab $716 (its remembered screen). */
  _switch_pending(): void {
    const m = this.mem;
    const screen = m.u16(SCREEN_SELECT);
    if (!(screen & 0x8000)) {
      m.set16(TAB_SCREENS + s16(w16(2 * m.u16(TAB))), screen);
      this._switch(screen);
      return;
    }
    const tab = m.u16(PENDING_TAB);
    if (!(tab & 0x8000)) this._switch_tab(tab);
  }

  /** $F6A8: tab $714 = tab, to its remembered screen [$720 + 2 * tab]. */
  _switch_tab(tab: number): void {
    const m = this.mem;
    m.set16(TAB, tab);
    this._switch(m.u16(TAB_SCREENS + s16(w16(2 * tab))));
  }

  /** $F6B6: leave the current screen, enter the new one ($139AE first zeroes $122D and
   * the view pointers $9FA camera, $9FE viewed, $A02 controlled), its panel list $71C,
   * no request pending. The panel drawing $782A is left out. */
  _switch(screen: number): void {
    const m = this.mem;
    if (screen > 35) throw new NotPorted(`switch to screen ${screen} (not in the tables)`);
    this.screen_leave();
    m.set16(SCREEN, screen);
    m.set8(0x122d, 0);
    for (const a of [CAMERA, VIEWED, CONTROLLED]) m.set32(a, 0);
    const handler = SCREEN_ENTER[screen];
    if (handler) (this as any)[handler]();
    m.set32(PANEL_LIST, PANEL_LISTS[screen]);
    m.set8(PENDING_TAB, 0xff); // st.b: the high bytes
    m.set8(SCREEN_SELECT, 0xff);
  }

  /** $14302: the current screen's leave handler (table $14318; none while [$718] < 0). */
  screen_leave(): void {
    const screen = this.mem.u16(SCREEN);
    if (screen & 0x8000) return;
    if (screen > 35) throw new NotPorted(`leave handler of screen ${screen} (not in the table)`);
    const handler = SCREEN_LEAVE[screen];
    if (handler) (this as any)[handler]();
  }

  // ---- the click handlers of the icon columns ------------------------------------------
  /** $F844, a tab icon (left column, type 32 + tab: 1 carrier, 2 weapons, 3 Walrus,
   * 4 Manta, 0 game status): unless it is lit already, it is lit, the current tab's
   * icon cleared and the tab requested ($716); ui_frame then switches to the tab's
   * remembered screen [$720 + 2 * tab] (leave, enter). */
  cmd_select_tab(tab: number): boolean {
    const m = this.mem;
    if (!(tab >= 0 && tab <= 4 && this._clickable(T_TAB + tab, [0x704, 0]))) return false;
    const a = FLAG_TABLE + 2 * (T_TAB + tab);
    if (!(m.u8(a) & 0x20)) {
      m.set8(a, m.u8(a) | 0x20);
      m.set16(PENDING_TAB, tab);
      const b = FLAG_TABLE + s16(w16(2 * (m.u16(TAB) + T_TAB)));
      m.set8(b, m.u8(b) & 0xdf);
    }
    this._after_click();
    return true;
  }

  /** $F880, a screen icon (right column, type = screen; the UI offers the current
   * tab's screens): unless it is lit, it is lit, the current screen's icon cleared and
   * the screen requested ($71A); ui_frame records it as the tab's screen and switches. */
  cmd_select_screen(screen: number): boolean {
    const m = this.mem;
    if (!(screen >= 0 && screen <= 28 && this._clickable(screen, [0x706, 0]))) return false;
    const a = FLAG_TABLE + 2 * screen;
    if (!(m.u8(a) & 0x20)) {
      m.set8(a, m.u8(a) | 0x20);
      m.set16(SCREEN_SELECT, screen);
      const b = FLAG_TABLE + s16(w16(2 * m.u16(SCREEN)));
      m.set8(b, m.u8(b) & 0xdf);
    }
    this._after_click();
    return true;
  }

  /** What a player clicks to get to `screen` (0-4, 8-12, 16-20, 24-28 or 32): its tab
   * icon if another tab is shown (which enters that tab's remembered screen first), then
   * the screen's icon if it isn't the one shown. False if a click wasn't possible. */
  cmd_switch_screen(screen: number): boolean {
    const m = this.mem;
    const tab = TAB_OF[screen] ?? 5;
    if (tab > 4) return false;
    if (m.u16(TAB) !== tab && !this.cmd_select_tab(tab)) return false;
    if (m.u16(SCREEN) !== screen) return this.cmd_select_screen(screen);
    return true;
  }

  // ---- game status (32) and enemy carrier destroyed (35) ---------------------------------
  /** $101F2 / $10200 / $101E4 / $1020E (game-status buttons, types 100-103): the
   * sub-mode $708 = 6 surrender / 3 save / 1 format / 7 options, and the screen
   * re-entered ($71A = 32). Saving, formatting and the options panel themselves are
   * not ported. */
  cmd_game_status_menu(mode: number): boolean {
    const k = ({ 6: 0, 3: 1, 1: 2, 7: 3 } as Record<number, number>)[mode];
    if (k === undefined || !this._clickable(T_STATUS_MENU + k, [0x706, 0])) return false;
    this.mem.set16(GAME_STATUS, mode);
    this.mem.set16(SCREEN_SELECT, 0x20);
    this._after_click();
    return true;
  }

  /** $1021C: CANCEL / NO / OK / EXIT of a game-status sub-panel ($708 = 1, 3, 5, 6
   * or 7): back to the menu ($708 = 0), the screen re-entered. */
  cmd_game_status_back(): boolean {
    const m = this.mem;
    if (![1, 3, 5, 6, 7].includes(m.u16(GAME_STATUS)) || !this._clickable(38, null)) return false;
    m.set16(GAME_STATUS, 0);
    m.set16(SCREEN_SELECT, 0x20);
    this._after_click();
    return true;
  }

  /** $101D8, YES on the surrender panel ($708 = 6): the end request $463 with result
   * 0; the next game_frame requests the game-over screen 34. */
  cmd_surrender(): boolean {
    const m = this.mem;
    if (!this._clickable(38, [GAME_STATUS, 6])) return false;
    m.set8(END_REQUEST, 0xff);
    m.set16(RESULT, 0);
    this._after_click();
    return true;
  }

  /** Screen 35 (enemy carrier destroyed, "continue?"): YES $13990 goes back to the tab
   * [$730] the game was on; NO $1399C sets the result $466 = 4 and goes to the game-over
   * screen 34 (tab 6). */
  cmd_continue(yes: number): boolean {
    const m = this.mem;
    if (!this._clickable(38)) return false;
    if (yes) {
      m.set16(REQUEST_TAB, m.u16(END_TAB));
    } else {
      m.set16(RESULT, 4);
      m.set16(REQUEST_TAB, 6);
    }
    m.set8(SCREEN_REQUEST, 0xff);
    this._after_click();
    return true;
  }

  // ---- screen enter handlers ($139D4) ----------------------------------------------------
  /** The carrier view of the bridge, weapons, hangar and deck screens: world objects
   * drawn ($A46/$A47 = 0), camera = object 0, viewed = controlled = the carrier, camera
   * mode $A14 = 2. Returns object 0. */
  _carrier_view(): number {
    const m = this.mem;
    m.set8(0xa46, 0);
    m.set8(0xa47, 0);
    const cam = this.obj(0);
    m.set32(CAMERA, cam);
    m.set32(VIEWED, CARRIER);
    m.set32(CONTROLLED, CARRIER);
    m.set16(CAM_MODE, 2);
    return cam;
  }

  /** The header of the model list at $64CFE (damage diagram, title): n entries, 2000
   * ahead, 200 down. Returns the first entry. */
  _model_list(n: number): number {
    const m = this.mem;
    m.set8(MODEL_LIST, 1);
    m.set8(MODEL_LIST + 1, 0xff);
    m.set32(MODEL_LIST + 2, 0);
    m.set32(MODEL_LIST + 6, 0xffffff38);
    m.set32(MODEL_LIST + 0xa, 0x7d0);
    m.set16(MODEL_LIST + 0xe, n);
    return MODEL_LIST + 0x12;
  }

  /** Object 0 at the origin looking at the model list ($64CFE), as the camera. */
  _camera_at_list(): void {
    const m = this.mem;
    const cam = this.obj(0);
    m.set32(CAMERA, cam);
    for (const o of [4, 6, 8]) m.set16(cam + o, 0);
    for (const o of [0xa, 0xe, 0x12]) m.set32(cam + o, 0);
    m.set32(cam + 0x3c, MODEL_LIST);
  }

  _set_gauges(...timers: number[]): void {
    for (const a of timers) this.mem.set16(a, 2);
  }

  /** $13C24 (screen 0): the carrier view, object 0 the camera 240 up on the carrier's
   * island buffer, $1230 set; the gauges redraw, the auto button follows $640 bit 0. */
  enter_bridge(): void {
    const m = this.mem;
    m.set8(0x1230, 0xff);
    const cam = this._carrier_view();
    m.set16(cam, 0);
    m.set32(cam + 0xe, 0xf0);
    m.set32(cam + 0x3c, m.u32(CARRIER + 0x3c));
    m.set32(VIEWPORT, 0x4744);
    this._set_gauges(0x954, 0x958, 0x956, 0x962);
    m.set16(0x480, m.u16(C_DEPTH));
    this._sync_auto_button();
  }

  /** $1404E (screen 1): no world objects ($A46/$A47 set); the damage diagram (the
   * carrier and parts $4B-$52) as the model list, the shown system [$B58] highlighted
   * (the lift, 8: part 0, $B5A = 0); object 0 the camera at the origin. */
  enter_damage(): void {
    const m = this.mem;
    m.set8(0xa46, 0xff);
    m.set8(0xa47, 0xff);
    m.set16(0x952, 0);
    const a = this._model_list(DAMAGE_PARTS.length + 1);
    m.set16(a, 3);
    m.set16(a + 2, 0x4a);
    for (const o of [4, 6, 8, 0xa, 0xc, 0xe]) m.set16(a + o, 0);
    const first = a + 0x40;
    DAMAGE_PARTS.forEach((part, k) => {
      const e = first + 0x40 * k;
      m.set16(e, 1);
      m.set16(e + 2, part);
      m.set16(e + 0xa, 0);
      m.set16(e + 0xc, 0);
      m.set16(e + 0xe, 0xffff);
    });
    let k = m.s16(SYSTEM_SHOWN);
    if (k >= 0 && k <= 8) {
      if (k === 8) {
        k = 0;
        m.set16(PART_SHOWN, 0);
      }
      const e = first + 0x40 * k;
      m.set8(e + 1, m.u8(e + 1) | 2);
    }
    this._camera_at_list();
    m.set32(VIEWPORT, 0x4744);
  }

  _map_view_from(copy: number): void {
    this.mem.buf.copyWithin(MAP_ZOOM_LEVEL, copy, copy + 0x10);
  }

  _icon(etype: number, on: number): void {
    this.mem.set16(FLAG_TABLE + 2 * etype, on ? 0x2000 : 0);
  }

  /** $13AB2 (screen 2): the announce icon (type 92) from $6C6, the carrier map's view
   * $758 from its copy $768. */
  enter_carrier_map(): void {
    const m = this.mem;
    this._icon(T_ANNOUNCE, m.u8(ANNOUNCE));
    m.set16(0x95c, 2);
    this._map_view_from(0x768);
  }

  /** $14288 (screen 4): the message log redraws. */
  enter_messages(): void {
    this.mem.set16(0x966, 2);
    this.mem.set32(VIEWPORT, 0x4744);
  }

  /** $1415A (screen 8): the carrier view (object 0 on the carrier's island buffer),
   * object 1 off; the turret zoom's view offsets $A94 = z * 10000, $A98 = z * 14142
   * ($FCD4, z = [$6D2]). */
  enter_turret(): void {
    const m = this.mem;
    m.set8(0xa46, 0);
    m.set8(0xa47, 0);
    m.set16(this.obj(1), 0);
    const cam = this._carrier_view();
    m.set16(cam, 0);
    m.set32(cam + 0x3c, m.u32(CARRIER + 0x3c));
    m.set32(VIEWPORT, 0x4786);
    m.set16(0x968, 2);
    this.turret_zoom_view(m.u16(0x6d2));
  }

  /** $FCD4: the turret view's offsets for zoom z. */
  turret_zoom_view(z: number): void {
    this.mem.set32(0xa94, (z & 0xffff) * 0x2710);
    this.mem.set32(0xa98, (z & 0xffff) * 0x373e);
  }

  /** $13A64 (screen 9): the carrier view, object 0 the camera 300 up (not 240). */
  enter_flares(): void {
    const m = this.mem;
    const cam = this._carrier_view();
    m.set16(cam, 0);
    m.set32(cam + 0xe, 0x12c);
    m.set16(cam + 8, 0);
    m.set32(cam + 0x3c, m.u32(CARRIER + 0x3c));
    m.set32(VIEWPORT, 0x4770);
  }

  /** $142A0 (screen 10): camera = viewed = controlled = the viewing drone (object 15),
   * made invisible (it's the camera); camera mode 3; SSM disarmed ($1160 = 0). */
  enter_drone_view(): void {
    const m = this.mem;
    m.set8(0xa46, 0);
    m.set8(0xa47, 0);
    const o = this.obj(15);
    for (const a of [CAMERA, VIEWED, CONTROLLED]) m.set32(a, o);
    m.set8(o + 1, m.u8(o + 1) & 0xfd);
    m.set16(CAM_MODE, 3);
    m.set8(0x1160, 0);
    m.set32(VIEWPORT, 0x4770);
  }

  /** $141B2 (screen 11): snapshot the 4 drone stations' offsets ($65E + 14i, +$A/+$C)
   * into $141DE (RESET restores them, $10602). */
  enter_drone_radar(): void {
    const m = this.mem;
    for (let i = 0; i < 4; i++) {
      const a = 0x65e + 14 * i;
      m.set16(STATION_SNAPSHOT + 4 * i, m.u16(a + 0xa));
      m.set16(STATION_SNAPSHOT + 4 * i + 2, m.u16(a + 0xc));
    }
    m.set16(0x95a, 2);
  }

  /** mulu.w + adda.w: base + the low word of k * $34, sign-extended. */
  _bay_record(base: number, k: number): number {
    return u32(base + s16(w16((k & 0xffff) * 0x34)));
  }

  _bay_object(base: number, k: number): number {
    return u32(base + s16(w16((k & 0xffff) * 0x140)));
  }

  /** $11738/$11E60: the arming sub-panel for a bay's status byte +1 (0, 1-2, 3+). */
  _subpanel(rec: number): number {
    const s = this.mem.u8(rec + 1);
    return SUBPANEL[s < 4 ? s : 3];
  }

  /** $13C94 (screen 16): the carrier view with object 0 as the hangar (flags 3, type
   * $53, 140 up); the Walrus record $568 of the arming bay [$DB6], sub-panel $DC2. */
  enter_walrus_arming(): void {
    const m = this.mem;
    const cam = this._carrier_view();
    m.set16(cam, 3);
    m.set16(cam + 2, 0x53);
    m.set32(cam + 0xe, 0x8c);
    m.set32(cam + 0x3c, m.u32(CARRIER + 0x3c));
    const rec = this._bay_record(0x570, m.u16(0xdb6));
    m.set32(0x568, rec);
    m.set16(0xdc2, this._subpanel(rec));
    m.set16(0x960, 0);
    m.set16(0x95c, 2);
    m.set32(VIEWPORT, 0x475a);
  }

  /** $14224: the records of the selected Walrus [$566]: $568 record, $56C object. */
  select_walrus(): [number, number] {
    const m = this.mem;
    const k = m.u16(0x566);
    const rec = this._bay_record(0x570, k), o = this._bay_object(0x5e45a, k);
    m.set32(0x568, rec);
    m.set32(0x56c, o);
    return [rec, o];
  }

  /** $141FE: the records of the selected Manta [$48C]: $48E record, $492 object. */
  select_manta(): [number, number] {
    const m = this.mem;
    const k = m.u16(0x48c);
    const rec = this._bay_record(0x496, k), o = this._bay_object(0x5df5a, k);
    m.set32(0x48e, rec);
    m.set32(0x492, o);
    return [rec, o];
  }

  /** $13AC2 (screen 17): the selected Walrus's records, the draw-mode and announce
   * icons (types 91/92 from $6B8/$6BA), the view $758 from its copy $778. */
  enter_walrus_map(): void {
    const m = this.mem;
    this.select_walrus();
    this._icon(91, m.u16(0x6b8));
    this._icon(92, m.u8(0x6ba));
    this._map_view_from(0x778);
  }

  /** $13F06 (screen 18): viewed = the selected Walrus (made invisible; the camera $9FA
   * stays 0), camera mode 1, objects 0 and 1 off, $1230 set, missile lock timer $116C
   * = 0; controllable / controlled via walrus_view_update. Not controllable: pointer
   * mode. */
  enter_walrus_control(): void {
    const m = this.mem;
    m.set8(0xa46, 0);
    m.set8(0xa47, 0);
    m.set8(0x1230, 0xff);
    m.set16(this.obj(1), 0);
    m.set16(0x116c, 0);
    const [rec, o] = this.select_walrus();
    m.set32(VIEWED, o);
    m.set16(CAM_MODE, 1);
    m.set8(o + 1, m.u8(o + 1) & 0xfd);
    m.set16(this.obj(0), 0);
    m.set32(VIEWPORT, 0x4744);
    this.walrus_view_update(rec);
    if (!m.u16(0x70e)) m.set8(POINTER_MODE, 0xff);
  }

  /** $13FBA/$14004: weapon icons 62-64, auto (90) and sight (104) icons. */
  _vehicle_icons(rec: number, controllable: number, weapon: number): void {
    const m = this.mem;
    m.set32(FLAG_TABLE + 2 * 62, 0);
    m.set16(FLAG_TABLE + 2 * 64, 0);
    m.set16(FLAG_TABLE + 2 * T_AUTOPILOT, 0);
    m.set16(FLAG_TABLE + 2 * 104, 0);
    if (!controllable) return;
    if (m.u8(rec) & 1) m.set16(FLAG_TABLE + 2 * T_AUTOPILOT, 0x2000);
    if (m.u8(rec + 0x32)) m.set16(FLAG_TABLE + 2 * 104, 0x2000);
    m.set16(FLAG_TABLE + 2 * 62 + s16(w16(2 * m.u16(rec + weapon))), 0x2000);
  }

  /** $13F5A: rear view off ($8F1), the Walrus controllable ($70E) if its status
   * (+1, signed) is >= 8, then controlled $A02 = viewed, else 0; icons (turret icon 80
   * from bit 5, $13FA6), gauges. */
  walrus_view_update(rec: number): void {
    const m = this.mem;
    m.set8(0x8f1, 0);
    m.set16(FLAG_TABLE + 2 * 105, 0);
    m.set16(0x70e, 0);
    let d0 = 0;
    if (s8(m.u8(rec + 1)) >= 8) {
      m.set16(0x70e, 1);
      d0 = m.u32(VIEWED);
    }
    m.set32(CONTROLLED, d0);
    this._icon(80, m.u8(rec) & 0x20);
    this._vehicle_icons(rec, m.u16(0x70e), 0x18);
    this._set_gauges(0x954, 0x958, 0x962);
    m.set16(0x984, 1);
  }

  /** $13EBA: as walrus_view_update for the Manta ($70C, weapon +$1A). */
  manta_view_update(rec: number): void {
    const m = this.mem;
    m.set8(0x8f1, 0);
    m.set16(FLAG_TABLE + 2 * 105, 0);
    m.set16(0x70c, 0);
    let d0 = 0;
    if (s8(m.u8(rec + 1)) >= 8) {
      m.set16(0x70c, 1);
      d0 = m.u32(VIEWED);
    }
    m.set32(CONTROLLED, d0);
    this._vehicle_icons(rec, m.u16(0x70c), 0x1a);
    this._set_gauges(0x954, 0x956, 0x958, 0x962);
    m.set16(0x984, 1);
  }

  /** $13D08 (screen 19): the records of the dock selection [$DE4]; the carrier view,
   * object 0 the camera 50 up (its island buffer pointer kept), object 1 off. */
  enter_walrus_dock(): void {
    const m = this.mem;
    const k = m.u16(0xde4);
    m.set32(0x56c, this._bay_object(0x5e45a, k));
    m.set32(0x568, this._bay_record(0x570, k));
    m.set8(0xa46, 0);
    m.set8(0xa47, 0);
    m.set16(this.obj(1), 0);
    const cam = this._carrier_view();
    m.set16(cam, 0);
    m.set16(cam + 4, 0);
    m.set16(cam + 8, 0);
    m.set32(cam + 0xe, 0x32);
    m.set16(0x95c, 2);
    m.set32(VIEWPORT, 0x4744);
  }

  /** $13D82 (screen 24): the carrier view, object 0 the camera 240 up; the Manta
   * record $48E of the arming bay [$DB4], sub-panel $DC0. */
  enter_manta_arming(): void {
    const m = this.mem;
    const cam = this._carrier_view();
    m.set16(cam, 0);
    m.set32(cam + 0xe, 0xf0);
    m.set32(cam + 0x3c, m.u32(CARRIER + 0x3c));
    const rec = this._bay_record(0x496, m.u16(0xdb4));
    m.set32(0x48e, rec);
    m.set16(0xdc0, this._subpanel(rec));
    m.set16(0x95e, 0);
    m.set16(0x95c, 2);
    m.set32(VIEWPORT, 0x475a);
  }

  /** $13AD2 (screen 25): the selected Manta's records, icons 91/92 from $6AA/$6AC,
   * the view $758 from its copy $788. */
  enter_manta_map(): void {
    const m = this.mem;
    this.select_manta();
    this._icon(91, m.u16(0x6aa));
    this._icon(92, m.u8(0x6ac));
    this._map_view_from(0x788);
  }

  /** $13DEE (screen 26): the records of the deck selection [$DCC]; the carrier view,
   * object 0 the camera 230 up (island buffer pointer kept), object 1 off. */
  enter_manta_deck(): void {
    const m = this.mem;
    const k = m.u16(0xdcc);
    m.set32(0x492, this._bay_object(0x5df5a, k));
    m.set32(0x48e, this._bay_record(0x496, k));
    m.set8(0xa46, 0);
    m.set8(0xa47, 0);
    m.set16(this.obj(1), 0);
    const cam = this._carrier_view();
    m.set16(cam, 0);
    m.set16(cam + 4, 0);
    m.set16(cam + 8, 0);
    m.set32(cam + 0xe, 0xe6);
    m.set16(0x95c, 2);
    m.set32(VIEWPORT, 0x4744);
  }

  /** $13E68 (screen 27): camera = viewed = the selected Manta (made invisible), camera
   * mode 0, object 1 off, $1230 set; controllable / controlled via manta_view_update.
   * Not controllable: pointer mode. Missile lock timer $116C = 0. */
  enter_manta_control(): void {
    const m = this.mem;
    m.set8(0xa46, 0);
    m.set8(0xa47, 0);
    m.set8(0x1230, 0xff);
    m.set16(this.obj(1), 0);
    const [rec, o] = this.select_manta();
    m.set32(CAMERA, o);
    m.set32(VIEWED, o);
    m.set16(CAM_MODE, 0);
    m.set8(o + 1, m.u8(o + 1) & 0xfd);
    m.set32(VIEWPORT, 0x4744);
    this.manta_view_update(rec);
    if (!m.u16(0x70c)) m.set8(POINTER_MODE, 0xff);
    m.set16(0x116c, 0);
  }

  /** $13AFA (screen 32): from a tab click ($716 >= 0) the menu ($708 = 0), the lit
   * button for [$708]; the simulation stops ($461 set: game_frame skips it, the game
   * clock stops, PAUSE does nothing). Sound off ($20716) left out. */
  enter_game_status(): void {
    const m = this.mem;
    if (!(m.u16(PENDING_TAB) & 0x8000)) m.set16(GAME_STATUS, 0);
    for (let t = 0; t < 4; t++) {
      const a = FLAG_TABLE + 2 * (T_STATUS_MENU + t);
      m.set8(a, m.u8(a) & 0xdf);
    }
    if (m.u16(GAME_STATUS) >= 8) {
      throw new NotPorted(`game-status mode ${m.u16(GAME_STATUS)} (beyond the table $13B6C)`);
    }
    const k = STATUS_ICON[m.u16(GAME_STATUS)];
    if (k) {
      const a = FLAG_TABLE + 2 * (T_STATUS_MENU + k - 1);
      m.set8(a, m.u8(a) | 0x20);
    }
    m.set32(VIEWPORT, 0x47dc);
    m.set8(MENU, 0xff);
  }

  /** $13B7C (screen 33): no 3-VBL frame lock ($8F0 = 0), no world objects, the title
   * model as the model list, turret elevation $6CA = 2000, object 0 the camera; the
   * simulation stops ($461). */
  enter_title(): void {
    const m = this.mem;
    m.set8(0x8f0, 0);
    m.set8(0xa46, 0xff);
    m.set8(0xa47, 0xff);
    const a = this._model_list(1);
    m.set16(a, 3);
    m.set16(a + 2, 1);
    for (const o of [4, 8, 0xa, 0xc, 0xe]) m.set16(a + o, 0);
    m.set16(0x6ca, 0x7d0);
    this._camera_at_list();
    m.set32(VIEWPORT, 0x47c8);
    m.set8(MENU, 0xff);
    m.set16(0x93c, m.u16(0x93a));
  }

  /** $1424A (screen 34): the pointer reset ($F712), pointer mode, the simulation
   * stopped; and at once the title requested ($4272: tab 5, $708 = 0), which the next
   * ui_frame takes through leave_game_over (the key wait). */
  enter_game_over(): void {
    const m = this.mem;
    this.pointer_reset();
    m.set8(POINTER_MODE, 0xff);
    m.set8(MENU, 0xff);
    m.set8(SCREEN_REQUEST, 0xff);
    m.set16(REQUEST_TAB, 5);
    m.set16(GAME_STATUS, 0);
  }

  /** $14266 (screen 35): the pointer reset, pointer mode, the simulation stopped. */
  enter_destroyed(): void {
    const m = this.mem;
    this.pointer_reset();
    m.set8(POINTER_MODE, 0xff);
    m.set8(MENU, 0xff);
  }

  // ---- screen leave handlers ($14318) ----------------------------------------------------
  _set_visible(o: number): void {
    this.mem.set8(o + 1, this.mem.u8(o + 1) | 2);
  }

  /** $143D4 (0), $14464 (24): the carrier visible again, $1230 = 0. */
  leave_bridge(): void {
    this._set_visible(CARRIER);
    this.mem.set8(0x1230, 0);
  }

  /** $14414 (1): object 0's flags cleared. */
  leave_damage(): void {
    this.mem.set16(this.obj(0), 0);
  }

  _map_view_to(copy: number): void {
    this.mem.buf.copyWithin(copy, MAP_ZOOM_LEVEL, MAP_ZOOM_LEVEL + 0x10);
  }

  /** $143A8 (2): the map view back to its copy $768. */
  leave_carrier_map(): void {
    this._map_view_to(0x768);
  }

  /** $143AE (17): $778. */
  leave_walrus_map(): void {
    this._map_view_to(0x778);
  }

  /** $143B4 (25): $788. */
  leave_manta_map(): void {
    this._map_view_to(0x788);
  }

  /** $1441C (8): the turret view offsets cleared. */
  leave_turret(): void {
    this.mem.set32(0xa94, 0);
    this.mem.set32(0xa98, 0);
  }

  /** $14428 (10): the drone visible again. */
  leave_drone_view(): void {
    this._set_visible(this.obj(15));
  }

  /** $143E2 (16): object 0 byte 1 bit 0 cleared, the carrier visible. */
  leave_walrus_arming(): void {
    const o = this.obj(0);
    this.mem.set8(o + 1, this.mem.u8(o + 1) & 0xfe);
    this._set_visible(CARRIER);
  }

  /** $14404 (18), $143F4 (27): the viewed vehicle [$9FE] visible again, $1230 = 0. */
  leave_vehicle_control(): void {
    this._set_visible(this.mem.u32(VIEWED));
    this.mem.set8(0x1230, 0);
  }

  /** $143CA (32): the simulation runs again ($461 = 0), $706 = 0. */
  leave_game_status(): void {
    this.mem.set8(MENU, 0);
    this.mem.set16(0x706, 0);
  }

  /** $14432 (33): the 3-VBL frame lock back on. */
  leave_title(): void {
    this.mem.set8(0x8f0, 0xff);
  }

  /** $14438 (34): a tune if the carrier was destroyed ([$466] = 1), then the original
   * waits for a key ($889); the UI does that before the next ui_frame. The left-button
   * press is consumed. */
  leave_game_over(): void {
    this._bclr0(0x881);
  }

  /** $1445E (35): the simulation runs again. */
  leave_destroyed(): void {
    this.mem.set8(MENU, 0);
  }

  // ---- pause and the game flow ----------------------------------------------------------
  /** $FD84: stop the simulation and the game clock. */
  pause(): void {
    const m = this.mem;
    m.set8(RUNNING, 0);
    m.set8(0x1231, 0);
    m.set8(LAST_CHAR, 0xff);
  }

  /** $FD98: run again; the keyboard and mouse state is cleared ($17EAE, $17EC6). */
  resume(): void {
    const m = this.mem;
    const a = FLAG_TABLE + 2 * T_PAUSE;
    m.set8(a, m.u8(a) & 0xdf);
    m.set8(RUNNING, 0xff);
    m.set8(0x1231, 0xff);
    m.buf.fill(0, KEYS, 0x884);
    m.set8(0x888, 0);
    m.set8(LAST_CHAR, 0xff);
    m.set32(MOUSE_DX, 0);
    m.set32(0x880, 0);
  }

  /** $FD66: PAUSE toggles the game (not while the game-status menu is open). */
  cmd_pause(): boolean {
    const m = this.mem;
    if (!this._clickable(T_PAUSE)) return false;
    if (m.u8(MENU)) return true;
    if (m.u8(RUNNING)) {
      const a = FLAG_TABLE + 2 * T_PAUSE;
      m.set8(a, m.u8(a) | 0x20);
      this.pause();
    } else {
      this.resume();
    }
    return true;
  }

  /** $4194: Control-Q resumes, Control-S pauses; Alt-Help prints a message. */
  misc_keys(): void {
    const m = this.mem;
    if (!m.u8(MENU)) {
      if (this._bclr0(0x7b9)) {
        if (m.u8(0x7d2)) {
          const ctrl = m.u16(0x7d2);
          this.resume();
          m.set16(0x7d2, ctrl);
        }
      } else if (this._bclr0(0x7d7)) {
        if (m.u8(0x7d2)) this.pause();
      }
    }
    if (m.u8(0x808) && this._bclr0(0x85d)) this._message("Yes, I have kicked out the OS");
  }

  /** $44EE: while paused, typing the cheat code ("GROW OLD ALONG WITH ME", stored
   * inverted at $1172) turns on debug mode $1189 and resumes. In debug mode keypad keys
   * refuel ($873), heal ($875) or move units to their waypoints ($877). */
  paused_frame(): void {
    const m = this.mem;
    if (!m.u8(DEBUG)) {
      const c = m.u8(LAST_CHAR);
      if (c & 0x80) return;
      m.set8(LAST_CHAR, 0xff);
      let p = m.u32(CHEAT_PTR);
      if ((~c & 0xff) !== m.u8(p)) {
        m.set32(CHEAT_PTR, CHEAT_CODE);
        return;
      }
      p += 1;
      if (m.u8(p)) {
        m.set32(CHEAT_PTR, p);
        return;
      }
      m.set8(DEBUG, 0xff);
      this.resume();
      this._message("Cheat Mode active");
      return;
    }
    if (this._bclr0(0x873)) {
      for (let k = 0; k < 4; k++) {
        m.set16(0x496 + 0x34 * k + 0x0a, 0x7fff);
        m.set16(0x570 + 0x34 * k + 0x08, 0x7fff);
      }
      return;
    }
    if (this._bclr0(0x875)) {
      for (let k = 0; k < 4; k++) {
        m.set16(0x496 + 0x34 * k + 0x14, 0xfa0);
        m.set16(0x570 + 0x34 * k + 0x14, 0xfa0);
      }
      return;
    }
    if (this._bclr0(0x877)) {
      this._debug_teleport();
      return;
    }
    if (m.u8(0x7d2)) {
      for (let k = 0; k < 10; k++) {
        if (m.u8(0x80f + 2 * k)) throw new NotPorted("debug screen dump to disk ($4672: Control + F-key while paused)");
      }
      return;
    }
    m.set32(CHEAT_PTR, CHEAT_CODE);
  }

  /** $45B8: units flying or driving to a waypoint jump there. */
  _debug_teleport(): void {
    const m = this.mem;
    for (let k = 0; k < 4; k++) {
      const r = 0x496 + 0x34 * k, o = OBJECTS + OBJECT_SIZE * (2 + k);
      if ((m.u8(r) & 3) === 3 && m.u8(r + 0x1c) === 3) m.buf.copyWithin(o + 0x0a, r + 0x1e, r + 0x2a);
    }
    for (let k = 0; k < 4; k++) {
      const r = 0x570 + 0x34 * k, o = OBJECTS + OBJECT_SIZE * (6 + k);
      if ((m.u8(r) & 3) === 3 && m.u8(r + 0x1a) === 3) {
        m.set32(o + 0x0a, m.u32(r + 0x1c));
        m.set32(o + 0x12, m.u32(r + 0x20));
      }
    }
    if ((m.u8(C_FLAGS) & 3) === 3 && m.u8(C_SUBSTATE)) {
      m.set32(CARRIER + 0x0a, m.u32(C_WAYPOINT_X));
      m.set32(CARRIER + 0x12, m.u32(C_WAYPOINT_Z));
    }
  }

  /** $46E8: in debug mode keypad + / - switch the cheat $118A (invulnerable Manta, no
   * stall) and keypad 9 ($86B) shows the difficulty. */
  debug_keys(): void {
    const m = this.mem;
    if (!m.u8(DEBUG)) return;
    if (this._bclr0(0x835)) m.set8(CHEAT, 0xff);
    if (this._bclr0(0x82d)) m.set8(CHEAT, 0);
    if (this._bclr0(0x86b)) {
      let d = m.u16(0x444), digits = "";
      for (const div of [1000, 100, 10, 1]) {
        // $9FAA: digits by repeated subtraction
        let n = 0;
        while (d >= div) {
          d -= div;
          n += 1;
        }
        digits += String.fromCharCode(0x30 + n);
      }
      this._message(`Difficulty level is ${digits}`);
    }
  }

  /** $17094: the current screen's input handler ($170D4), then the edge flags of the
   * right button and the keys the handlers use are cleared. */
  screen_input(): void {
    const m = this.mem;
    const screen = m.u16(SCREEN);
    const handler = SCREEN_INPUT[screen];
    if (handler === undefined) {
      throw new NotPorted(`screen input for screen ${screen} ($${(INPUT_TABLE[screen] ?? 0).toString(16).toUpperCase().padStart(5, "0")})`);
    }
    if (handler) (this as any)[handler]();
    for (const a of EDGE_FLAGS) m.set8(a, 0);
  }

  /** $17164 (screen 0): gauge timers $954-$962 (redraws), the auto button follows
   * $640 bit 0, a right click toggles mouse-steer mode unless the autopilot is on;
   * Insert / Clr-Home zoom the view (display only) and Control-V shows the version. */
  bridge_input(): void {
    const m = this.mem;
    for (const t of [0x954, 0x956, 0x958, 0x962]) if (m.u16(t)) m.set16(t, m.u16(t) - 1);
    const selected = m.u8(FLAG_TABLE + 2 * T_AUTOPILOT) & 0x20;
    if (!(m.u8(C_FLAGS) & 1)) {
      if (selected) this._sync_auto_button();
      if (this._bclr0(0x883)) m.set8(POINTER_MODE, ~m.u8(POINTER_MODE));
    } else if (!selected) {
      this._sync_auto_button();
    }
    this._bclr0(0x83d); // zoom $8B4/$8B2: rendering only
    this._bclr0(0x827);
    if (this._bclr0(0x7f7) && this._bclr0(0x7d3)) this._message("Atari Bundle Version    16/02/90");
  }

  /** $1747A (screen 1): with the lift shown ($B58 = 8) its parts are highlighted in turn
   * ($B5A 0-7); the damage message timer $952 counts down (redraw). */
  damage_input(): void {
    const m = this.mem;
    if (m.s16(SYSTEM_SHOWN) === 8) {
      let k = m.s16(PART_SHOWN) + 1;
      if (k > 8) k = 0;
      m.set16(PART_SHOWN, k);
    }
    if (m.u16(0x952)) m.set16(0x952, m.u16(0x952) - 1);
  }

  /** $17244 (maps, screens 2/17/25): cursor keys scroll, Insert / Clr-Home zoom (held),
   * a right click recentres ($F8B4); on the Walrus and Manta maps the keypad selects a
   * unit ($132D6, not ported). */
  map_input(): void {
    const m = this.mem;
    const keys: [number, () => void][] = [
      [0x828, () => this.map_scroll_up()], [0x838, () => this.map_scroll_down()],
      [0x82e, () => this.map_scroll_left()], [0x832, () => this.map_scroll_right()],
      [0x83c, () => this.map_zoom_in()], [0x826, () => this.map_zoom_out()],
    ];
    for (const [key, f] of keys) if (m.u8(key)) f();
    if (!m.u16(MAP_INFO) && this._bclr0(0x883)) this.map_recentre_at_pointer();
    const screen = m.u16(SCREEN);
    if ((screen === 0x11 || screen === 0x19) && m.u8(POINTER_MODE) && !m.u8(MODAL) && MAP_KEYPAD.some((a) => m.u8(a) & 1)) {
      throw new NotPorted("unit selection keys on the Walrus/Manta map ($132D6)");
    }
  }

  /** One game_main loop ($408A-$4190) without ui_frame $F636 (the UI calls the cmd_
   * methods instead, before this), rendering and sound: Control-Q/S, then paused_frame
   * while paused, or (unless the game-status menu is open, $461) the debug keys,
   * screen_input and sim_pass; then the end-of-game timer $732 (set to 100 when the enemy
   * carrier is destroyed) and the end request $463. Either requests the game-over tab
   * ($1227/$1228: tab 7 = screen 35, enemy carrier destroyed; tab 6 = screen 34, result
   * [$466]) and records ["game over", screen, result]; the app then stops. */
  game_frame(): void {
    const m = this.mem;
    this.misc_keys();
    if (!m.u8(RUNNING)) {
      this.paused_frame();
    } else if (!m.u8(MENU)) {
      this.debug_keys();
      this.screen_input();
      this.sim_pass();
    }
    if (m.u16(END_TIMER)) {
      m.set16(END_TIMER, m.u16(END_TIMER) - 1);
      if (!m.u16(END_TIMER)) {
        m.set16(END_TAB, m.u16(TAB));
        m.set16(REQUEST_TAB, 7);
        m.set8(SCREEN_REQUEST, 0xff);
        this.events.push(["game over", 35, m.u16(RESULT)]);
      }
    }
    if (m.u8(END_REQUEST)) {
      m.set8(END_REQUEST, 0);
      m.set8(SCREEN_REQUEST, 0xff);
      m.set16(REQUEST_TAB, 6);
      this.events.push(["game over", 34, m.u16(RESULT)]);
    }
  }
}
