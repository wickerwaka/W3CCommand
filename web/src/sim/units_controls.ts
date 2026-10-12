/**
 * Unit layer, part of tools/stunits.py: the player's control of the carrier and the game flow
 * around sim_pass ("Sail" milestone).
 *
 * TypeScript port of tools/stunits_controls.py (the verified reference; its docstring lists
 * every command with its original handler). A browser UI calls these instead of the original's
 * mouse/keyboard handlers; each writes exactly what its handler writes (redraws, palettes and
 * sounds left out; messages become ["message", text, ping] events). A cmd_ method returns false when
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
 *                    screen_input() $17094 (every screen)
 *   Mantas (27/26/25/24) cmd_manta_select/weapon/autopilot/centre_rudder/centre_pitch/instruments/
 *                    throttle(y)/sight/rear_view/runway_take_off, cmd_manta_steer_to, cmd_vehicle_fire,
 *                    cmd_manta_deck_select, cmd_manta_abort_landing, cmd_manta_map_select,
 *                    cmd_manta_set_course(held)/clear_course/cruise/altitude/map_draw_mode/
 *                    toggle_announce, cmd_set_manta_waypoint, cmd_manta_arming_select/arming_to_deck/
 *                    loadout_next/prev/pick_loadout/pick_slot/fit/unfit
 *   Walruses (18/19/17/16) the same set (turret_mode, missile_steer_to, abort_docking,
 *                    loadout_plus/minus), cmd_drag_release(px, py) + ui_frame drop, vehicle_ui()
 *   War (3/4/8-11/32/33) cmd_bridge_zoom_in/out, turret_input $1741A, cmd_turret_up/down/left/right/
 *                    fire/zoom_in/zoom_out/centre_heading/centre_elevation, cmd_turret_steer_to,
 *                    cmd_flare_launch, drone_view_input $1778E, cmd_recon_launch, cmd_ssm_fire_at(px, py),
 *                    cmd_drones_launch/recall, cmd_drone_stations_reset, cmd_drone_formation_next/prev/
 *                    apply, cmd_drone_station_pick(k) (drop $104E6), cmd_drone_station_move(k, px, py),
 *                    resources_input $177DE, cmd_resupply_abort, cmd_resources_transfer,
 *                    cmd_item_picture(k)/_back, cmd_stores_priorities_open/reset/exit,
 *                    cmd_stores_scroll_up/down, cmd_stores_class(row, cls), cmd_stores_target(row),
 *                    cmd_keypad(key), cmd_fuel_priorities_open/reset/exit, cmd_fuel_class(k, cls),
 *                    cmd_stores_queue_open/back/scroll_up/scroll_down, cmd_stores_unqueue/queue(row, held),
 *                    cmd_fuel_queue_open/back, cmd_fuel_unqueue/queue(k), cmd_clear_message_log(logged),
 *                    cmd_game_option(k, on), cmd_title_new_game(strategy) (python docstring: addresses)
 */
import { NotPorted, divu, pyDiv, s8, s16, s32, u32, w16 } from "./mem.ts";
import type { Region } from "./mem.ts";
import {
  CARRIER_ISLAND, CARRIER_POOL, FUEL, FUEL_CLASS, FUEL_PENDING, IN_TRANSIT, ISLAND_TABLE, ISLAND_SIZE, ITEMS, ITEM_SIZE,
  N_ITEMS, OWNERS, ROUTES, ROUTES_PENDING, STOCKPILE, STORES, STORES_PENDING, SYSTEMS,
} from "./world.ts";
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
// Mantas and Walruses (screens 16-19, 24-27).
export const MANTA_RECS = 0x496, WALRUS_RECS = 0x570, REC = 0x34;
export const MANTA_OBJS = 0x5df5a, WALRUS_OBJS = 0x5e45a;
export const LOADOUT = 0xcac4; // 26-byte entries: +0 weight, +2 stock pointer, +6 quantity, +8 Walrus slot (1: payload)
export const MANTA_CODES = 0xc770, WALRUS_CODES = 0xc242; // the arming screens' loadout lists (6 / 5 codes)
export const LOADOUT_CURSOR = 0xdb8, DRAG_ELEMENT = 0xdbc; // $DB8 Manta, $DBA Walrus list cursor; the slot element being dragged
export const DROP = 0x468; // drag: the drop callback ($F73C), run by ui_frame on release ($F6EA)
export const HARBINGER_LOAD = 0x11bd2; // weight, stock pointer ($B98), quantity: the old Harbinger load (code)
export const HARBINGERS = 0xcb80; // loadout entry 7's quantity: the Harbingers to load
export const KEY_ELEMENT = 0x13352; // $132D6 writes its element here (type, index)
export const PLUS_HANDLER = 0x4281c, MINUS_HANDLER = 0x42832; // the Walrus arming +/- elements' handlers, patched by the panel draw
export const VEHICLE_THROTTLE_Y: [number, number] = [158, 159 + 0x26]; // the Manta/Walrus control screens' throttle slider
export const MAP_SLIDER_Y: [number, number] = [159, 159 + 0x24]; // the map screens' cruise and altitude sliders
export const T_BUTTON = 38, T_WEAPON = 62, T_TURRET = 80, T_HARBINGERS = 81, T_HANGAR = 78, T_DRAW = 91, T_SIGHT = 104, T_REAR = 105;
export const T_MANTA = 58, T_WALRUS = 70; // select icons on the control and map screens (+ bay)
export const T_MANTA_ARMING = 74, T_WALRUS_ARMING = 82, T_MANTA_DECK = 86, T_WALRUS_DOCK = 93;
// The arming screens' slot elements (panel data): slot index, pointer box x0, y0, x1, y1 ($B52C).
export const SLOT_ELEMENTS: Record<number, [number, number, number, number, number]> = {
  0x429ba: [0, 219, 70, 233, 93], 0x429d0: [1, 196, 90, 210, 113], 0x429e6: [2, 241, 90, 255, 113],
  0x42836: [0, 173, 96, 188, 106], 0x4284c: [1, 201, 96, 248, 106],
};
export const MANTA_SLOTS = [0x429ba, 0x429d0, 0x429e6], WALRUS_SLOTS = [0x42836, 0x4284c];
export const STORES_BOX: [number, number, number, number] = [0x21, 0x4b, 0xa2, 0x84]; // drop a fitted item here to return it to stores

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
  // Mantas and Walruses (milestone 2): arming drags, number keys, map edit globals.
  ["loadout cursors $DB8", LOADOUT_CURSOR, 8], // $DB8 Manta, $DBA Walrus, $DBC dragged slot element
  ["drop callback $468", DROP, 4],
  ["harbinger load $11BD2", HARBINGER_LOAD, 8], // in the code: the old Harbinger load as a loadout entry
  ["key element $13352", KEY_ELEMENT, 4], // in the code: $132D6's fake panel element
  ["arming +/- handlers $4281C", PLUS_HANDLER, 0x1a], // panel data: the type-81 buttons' handlers (+$12)
  ["loadout codes $C770", MANTA_CODES, 12],
  ["loadout codes $C242", WALRUS_CODES, 10],
  ["slot offsets $11448", 0x11448, 6],
  ["slot offsets $11C12", 0x11c12, 4],
] as Region[]) {
  if (!UNIT_REGIONS.some((r) => r[0] === _region[0])) UNIT_REGIONS.push(_region);
}
for (const n of ["current tab $714", "panel overlay $702", "key characters $1280", "map island $742", "map rect $47F2",
  "loadout codes $C770", "loadout codes $C242", "slot offsets $11448", "slot offsets $11C12"]) {
  UNIT_INPUT_REGIONS.add(n);
}

// Milestone 3 ("War"): the weapons screens 8-11, resources 3, messages 4, options and title.
export const RESOURCES = 0xd14; // resources sub-panel: 0 main, 1 item picture, 2 stores priorities,
//                                 3 fuel priorities, 4 stores queue, 5 fuel queue
export const FUEL_WORK = 0xd0e; // fuel classes being edited (EXIT copies them to $D08)
export const ENTRY = 0xd1a, ENTRY_DIGITS = 0xd1b, ENTRY_COUNT = 0xd2a, ENTRY_MAX = 0xd2c; // keypad number entry ($11142)
export const ENTRY_OLD = 0xd32, ENTRY_CALLBACK = 0xd34, ENTRY_DONE = 0x10b5a;
export const STORES_SCROLL = 0xd38, QUEUE_SCROLL = 0xd3a, QUEUE_REPEAT = 0xd3c;
export const ITEM_PANEL = 0xd16, ITEM_EDITED = 0xd18;
// $111D0: the keypad's key boxes relative to its element (216, 44): 0-9, 10 ENTER, 11 CANCEL, 12 DELETE.
export const KEYPAD: [number, number] = [216, 44];
export const KEYPAD_BOXES: [number, number, number, number][] = [
  [0, 0x30, 0xe, 0x3e], [0, 0x20, 0xe, 0x2e], [0x10, 0x20, 0x1e, 0x2e], [0x20, 0x20, 0x2e, 0x2e],
  [0, 0x10, 0xe, 0x1e], [0x10, 0x10, 0x1e, 0x1e], [0x20, 0x10, 0x2e, 0x1e], [0, 0, 0xe, 0xe],
  [0x10, 0, 0x1e, 0xe], [0x20, 0, 0x2e, 0xe], [0x30, 0, 0x3e, 0x1e], [0x30, 0x20, 0x3e, 0x3e],
  [0x10, 0x30, 0x2e, 0x3e],
];
export const TARGET_ENTRY: [number, number][] = [[120, 31], [120, 67], [120, 103]]; // $E026: the target fields of the stores priority rows
export const CLASS_Y = [26, 16, 5]; // $10AE6: a pointer offset in a class field for class 0, 1, 2
export const FORMATIONS = 0x1065c; // 9 drone formations: 4 stations x (+$A, +$C)
export const T_STATION = 66; // the drone radar's station markers, types 66-69
export const SSM_VIEW: [number, number, number, number] = [32, 6, 32 + 0xff, 6 + 0x7f]; // screen 10: the drone picture's element ($13598)
export const OPTIONS = 0x1246; // sea dots, message ping, background noise, sound effects
export const ISLAND_NAMES = 0x4ad20; // word offsets from here to each island's name
for (const _region of [
  ["resources panel $D0E", FUEL_WORK, 0xd3c - FUEL_WORK], // fuel work, sub-panel, item, keypad entry, scrolls
  ["bridge zoom $8B2", 0x8b2, 4], // $8B2 view scale, $8B4 zoom (renderer)
  ["options $1246", OPTIONS, 4],
  ["drone formations $1065C", FORMATIONS, 0x90], // in the code: read-only
  ["island names $4AD20", ISLAND_NAMES, 0x295], // 64 word offsets, then the names
] as Region[]) {
  if (!UNIT_REGIONS.some((r) => r[0] === _region[0])) UNIT_REGIONS.push(_region);
}
UNIT_INPUT_REGIONS.add("drone formations $1065C");
UNIT_INPUT_REGIONS.add("island names $4AD20");

// $170D4, the per-screen input handlers (screen: address; the rest are $170D2, rts).
export const INPUT_TABLE: Record<number, number> = {
  0: 0x17164, 1: 0x1747a, 2: 0x17244, 3: 0x177de, 8: 0x1741a, 10: 0x1778e, 16: 0x17748,
  17: 0x17244, 18: 0x17374, 19: 0x17782, 24: 0x17610, 25: 0x17244, 26: 0x1773c, 27: 0x172bc,
};
// Screens whose input handler is ported ("" = $170D2, rts).
export const SCREEN_INPUT: Record<number, string> = {
  0: "bridge_input", 1: "damage_input", 2: "map_input", 17: "map_input", 25: "map_input",
  3: "resources_input", 8: "turret_input", 10: "drone_view_input",
  16: "walrus_arming_input", 18: "walrus_control_input", 19: "walrus_dock_input",
  24: "manta_arming_input", 26: "manta_deck_input", 27: "manta_control_input",
};
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

// $F6EA: the drop callbacks of the drags that are ported (jmp [$468]).
export const DROPS: Record<number, string> = {
  0x113a4: "_manta_drop_loadout", 0x11500: "_manta_drop_return",
  0x11ace: "_walrus_drop_loadout", 0x11c4e: "_walrus_drop_return",
  0x104e6: "_drone_station_drop",
};

export abstract class ControlsUnits extends SetupUnits {
  // ---- the hit test's conditions ($B46E) ------------------------------------------------
  /** The panel is shown ([state addr] == value; null: always), the element type is
   * neither hidden nor disabled, and the game runs unless the type works while paused
   * (bit 12). No hit test runs during a drag ($460). */
  _clickable(etype: number, state: [number, number] | null = [0x702, 0]): boolean {
    const m = this.mem;
    if (m.u8(MODAL)) return false;
    if (state !== null && m.u16(state[0]) !== state[1]) return false;
    const f = m.u16(FLAG_TABLE + 2 * etype);
    if (f & 0xc000) return false;
    return Boolean(m.u8(RUNNING) || f & 0x1000);
  }

  _message(text: string): void {
    this.events.push(["message", text, this._ping()]);
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
      this.drag_drop();
      return;
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
   * no request pending; of the panel drawing $782A only the game state two panel
   * callbacks write (panels_drawn). */
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
    this.panels_drawn(screen);
  }

  /** $782A draws the new screen's panels; two panel callbacks write game state: the
   * arming screens' loadout panels (24: _manta_panel, 16: _walrus_panel). */
  panels_drawn(screen: number): void {
    if (screen === 24) this._manta_panel();
    else if (screen === 16) this._walrus_panel();
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
   * press is consumed. The tune is the event ["sound", "game over"] (`lea $4B72E,a0;
   * jsr $2019A`, ungated). */
  leave_game_over(): void {
    if (this.mem.u16(RESULT) === 1) this.events.push(["sound", "game over"]);
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
    if (this._bclr0(0x83d)) this.bridge_zoom(1); // zoom $8B4/$8B2 (renderer)
    if (this._bclr0(0x827)) this.bridge_zoom(-1);
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
   * a right click recentres ($F8B4); on the Walrus and Manta maps the number keys select
   * a unit ($132D6 -> $FFB8 / $FF1E). */
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
    if (screen === 0x11) this.key_select(T_WALRUS, (e, i) => this._walrus_map_select(e, i));
    else if (screen === 0x19) this.key_select(T_MANTA, (e, i) => this._manta_map_select(e, i));
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

  // ---- Mantas and Walruses: shared helpers (milestone 2) ------------------------------------
  // Defined by UnitState (units_vehicles.ts).
  abstract _manta_balance(a1: number): void;
  abstract cmd_manta_to_deck(bay?: number | null): void;

  /** $A044 / $A02A: "Manta N " / "Walrus N " + text (N = bay + 1 as a digit byte). */
  _unit_message(kind: string, bay: number, text: string): void {
    this._message(`${kind} ${String.fromCharCode((bay + 0x31) & 0xff)} ${text}`);
  }

  /** The flag word of an element type, as the handlers address it (adda.w 2 * type). */
  _flag(etype: number): number {
    return FLAG_TABLE + s16(w16(2 * etype));
  }

  /** bset #5 on the type's flag byte; true if it was set already. */
  _icon_set(etype: number): boolean {
    const a = this._flag(etype);
    const v = this.mem.u8(a);
    this.mem.set8(a, v | 0x20);
    return Boolean(v & 0x20);
  }

  _icon_clear(etype: number): void {
    const a = this._flag(etype);
    this.mem.set8(a, this.mem.u8(a) & 0xdf);
  }

  /** $132D6: in pointer mode and not dragging, the first of 1-4 / keypad 1-4 with its
   * new flag set (cleared here) selects unit i: the handler runs as a fresh click on a
   * fake element at $13352 (type base + i, index i). */
  key_select(base: number, handler: (etype: number, i: number) => void): void {
    const m = this.mem;
    if (!m.u8(POINTER_MODE) || m.u8(MODAL)) return;
    let i = 0;
    for (; i < 4; i++) {
      if (this._bclr0(MAP_KEYPAD[2 * i]) || this._bclr0(MAP_KEYPAD[2 * i + 1])) break;
    }
    if (i === 4) return;
    m.set16(KEY_ELEMENT + 2, i);
    m.set16(KEY_ELEMENT, w16(i + base));
    handler(w16(i + base), i);
  }

  _manta_rec(): number {
    return this.mem.u32(0x48e);
  }

  _walrus_rec(): number {
    return this.mem.u32(0x568);
  }

  /** $13FBA with a2 = [$48E]. */
  _manta_icons(): void {
    this._vehicle_icons(this._manta_rec(), this.mem.u16(0x70c), 0x1a);
  }

  /** $14004 with a2 = [$568]. */
  _walrus_icons(): void {
    this._vehicle_icons(this._walrus_rec(), this.mem.u16(0x70e), 0x18);
  }

  /** $13FA6: icon 80 (the rudder turns the turret) = Walrus [$568] bit 5. */
  _turret_icon(): void {
    this._icon(T_TURRET, this.mem.u8(this._walrus_rec()) & 0x20);
  }

  _slider(y: number, rng: [number, number], etype = T_BUTTON): boolean {
    return rng[0] <= y && y <= rng[1] && this._clickable(etype);
  }

  // ---- Manta control, screen 27 (input $172BC; panels $4229A, $423C6, $42386, $42346, $42306, $42446)
  /** $FE54: select Manta i in place: the old icon off and the old viewed object
   * [$492] visible again; $48C = i, its records ($141FE); camera and viewed = it
   * (invisible), camera mode 0; controllable / controlled and icons ($13EBA). */
  _manta_select(etype: number, i: number): void {
    const m = this.mem;
    if (this._icon_set(etype)) return;
    this._icon_clear(m.u16(0x48c) + T_MANTA);
    this._set_visible(m.u32(0x492));
    m.set16(0x48c, i);
    const [rec, o] = this.select_manta();
    m.set32(VIEWED, o);
    m.set16(CAM_MODE, 0);
    m.set32(CAMERA, o);
    m.set8(o + 1, m.u8(o + 1) & 0xfd);
    this.manta_view_update(rec);
  }

  /** $FE54, the Manta icons 1-4 (types 58-61) on the control screen 27 (the number keys
   * do the same in pointer mode): fly Manta `bay` (0-3) from now on. */
  cmd_manta_select(bay: number): boolean {
    if (!(0 <= bay && bay <= 3 && this._clickable(T_MANTA + bay))) return false;
    this._manta_select(T_MANTA + bay, bay);
    return true;
  }

  /** $FEE2 (types 62-64): weapon +$1A = 0 laser, 1 Assassin missiles, 2 payload (bomb
   * or comms pod), unless that icon is lit already; icons ($13FBA). */
  cmd_manta_weapon(w: number): boolean {
    const m = this.mem;
    if (!(0 <= w && w <= 2 && this._clickable(T_WEAPON + w))) return false;
    if (!(m.u8(this._flag(T_WEAPON + w)) & 0x20)) {
      m.set16(this._manta_rec() + 0x1a, w);
      this._manta_icons();
    }
    return true;
  }

  /** $12A44 (auto button, type 90): needs a controllable Manta ($70C) that isn't
   * crashing (bit 4). Off: automatic (bit 0), mode 3 (fly to the waypoint): "Autopilot on"
   * or, without a waypoint, "Autopilot on - set course". On: in mode 3 off; in the
   * take-off modes 2/6 only +$33 is set (deferred); other modes (landing) nothing. Then
   * the self-centre bits 6/7 are cleared. */
  cmd_manta_autopilot(): boolean {
    const m = this.mem;
    if (!this._clickable(T_AUTOPILOT)) return false;
    if (!m.u16(0x70c)) return true;
    const a2 = this._manta_rec();
    if (m.u8(a2) & 0x10) return true;
    const a0 = this._flag(T_AUTOPILOT);
    if (!(m.u8(a0) & 0x20)) {
      m.set8(a0, m.u8(a0) | 0x20);
      m.set8(a2, m.u8(a2) | 1);
      m.set8(a2 + 0x1c, 3);
      this._message(m.u8(a2) & 2 ? "Autopilot on" : "Autopilot on - set course");
    } else {
      const mode = m.u8(a2 + 0x1c);
      if (mode === 2 || mode === 6) {
        m.set8(a2 + 0x33, 0xff);
        return true;
      }
      if (mode !== 3) return true;
      m.set8(a0, m.u8(a0) & 0xdf);
      m.set8(a2, m.u8(a2) & 0xfe);
    }
    m.set8(a2, m.u8(a2) & 0x3f);
    this._manta_icons();
    return true;
  }

  /** $129E6: manual flight: +0 bit 7, the rudder self-centres. */
  cmd_manta_centre_rudder(): boolean {
    return this._manta_bit(0x80);
  }

  /** $129FE: manual flight: +0 bit 6, the pitch self-centres. */
  cmd_manta_centre_pitch(): boolean {
    return this._manta_bit(0x40);
  }

  _manta_bit(bit: number): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON)) return false;
    const a2 = this._manta_rec();
    if (!(m.u8(a2) & 1)) m.set8(a2, m.u8(a2) | bit);
    return true;
  }

  /** $100A8: the instrument panel's page +$18 = (+1) mod 3 (display). */
  cmd_manta_instruments(): boolean {
    return this._instrument_page(this._manta_rec() + 0x18);
  }

  _instrument_page(a: number): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON)) return false;
    const v = s16(m.u16(a) + 1);
    m.set16(a, v > 2 ? 0 : v);
    m.set16(0x962, 2);
    m.set16(0x984, 1);
    return true;
  }

  /** $FDDA, the throttle slider (x 288, y 158..197; repeats while held): manual flight:
   * target speed +8 = (195 - y) * 7, clamped to 0..250. */
  cmd_manta_throttle(y: number): boolean {
    const m = this.mem;
    if (!this._slider(y, VEHICLE_THROTTLE_Y)) return false;
    const a0 = this._manta_rec();
    if (!(m.u8(a0) & 1)) {
      const v = s16(w16(s16(w16(0xc3 - y)) * 7));
      m.set16(a0 + 8, v > 0xfa ? 0xfa : v < 0 ? 0 : v);
    }
    return true;
  }

  /** $10100 (type 104): controllable: toggle +$32 (the sight overlay *(guess)*), icons. */
  cmd_manta_sight(): boolean {
    const m = this.mem;
    if (!this._clickable(T_SIGHT)) return false;
    if (m.u16(0x70c)) {
      const a2 = this._manta_rec();
      m.set8(a2 + 0x32, ~m.u8(a2 + 0x32));
      this._manta_icons();
    }
    return true;
  }

  /** $1015C (type 105): controllable: toggle the rear view $8F1 (no firing while set). */
  cmd_manta_rear_view(): boolean {
    return this._rear_view(0x70c);
  }

  _rear_view(controllable: number): boolean {
    const m = this.mem;
    if (!this._clickable(T_REAR)) return false;
    if (m.u16(controllable)) {
      m.set16(this._flag(T_REAR), 0);
      m.set8(0x8f1, ~m.u8(0x8f1));
      if (m.u8(0x8f1)) m.set16(this._flag(T_REAR), 0x2000);
    }
    return true;
  }

  /** $13356 (135,117): a Manta landed on a runway (automatic, mode 5) is ordered to
   * take off (+0 bit 5). */
  cmd_manta_runway_take_off(): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON)) return false;
    const a0 = this._manta_rec();
    if (m.u8(a0) & 1 && m.u8(a0 + 0x1c) === 5) m.set8(a0, m.u8(a0) | 0x20);
    return true;
  }

  /** Mouse-steer input that makes the next manta_step set the selected Manta's rudder
   * +2 and pitch +4 (each clamped to +-255): the mouse deltas are the differences. It
   * applies when that Manta is the viewed one, manual, in mouse-steer mode ($457 clear). */
  cmd_manta_steer_to(rudder: number, pitch: number): void {
    const m = this.mem;
    const a = this._manta_rec();
    m.set16(MOUSE_DX, w16(Math.max(-255, Math.min(255, rudder)) - m.s16(a + 2)));
    m.set16(MOUSE_DX + 2, w16(Math.max(-255, Math.min(255, pitch)) - m.s16(a + 4)));
  }

  /** A left click in mouse-steer mode: the new-press flag $881 that the viewed Manta or
   * Walrus fires with on its next step (its selected weapon; not in the rear view). */
  cmd_vehicle_fire(): void {
    this.input_mouse_button(0, 1);
    this.input_mouse_button(0, 0);
  }

  /** $172BC (screen 27): the Assassin lock timer $116C counts down (only here); the
   * number keys select a Manta ($FE54); the screen is re-entered ($71A = 27) when the
   * Manta's status crosses 8 (controllable $70C); gauge timers; automatic: the auto button
   * follows bit 0; manual: a right click toggles mouse-steer mode. */
  manta_control_input(): void {
    const m = this.mem;
    if (m.u16(0x116c)) m.set16(0x116c, m.u16(0x116c) - 1);
    this.key_select(T_MANTA, (e, i) => this._manta_select(e, i));
    if (this._vehicle_reenter(this._manta_rec(), 0x70c, 0x1b)) return;
    for (const t of [0x954, 0x956, 0x958, 0x962]) if (m.u16(t)) m.set16(t, m.u16(t) - 1);
    const a2 = this._manta_rec();
    if (m.u8(a2) & 1) {
      if (!(m.u8(this._flag(T_AUTOPILOT)) & 0x20)) this._manta_icons();
      return;
    }
    if (this._bclr0(0x883)) m.set8(POINTER_MODE, ~m.u8(POINTER_MODE));
    if (m.u8(this._flag(T_AUTOPILOT)) & 0x20) this._manta_icons();
  }

  /** The control screens re-enter themselves when the unit's status (signed) crosses 8
   * relative to the controllable flag. True when the handler stops there. */
  _vehicle_reenter(a0: number, controllable: number, screen: number): boolean {
    const m = this.mem;
    const out = s8(m.u8(a0 + 1)) >= 8;
    if (!m.u16(controllable)) {
      if (out) m.set16(SCREEN_SELECT, screen);
      return true;
    }
    if (!out) {
      m.set16(SCREEN_SELECT, screen);
      return true;
    }
    return false;
  }

  // ---- Manta deck, screen 26 (input $1773C; panel $42B40) ---------------------------------
  /** $11F92: the deck screen's own selection $DCC (records $48E/$492; $48C kept). */
  _manta_deck_select(etype: number, i: number): void {
    const m = this.mem;
    const d0 = w16(m.u16(0xdcc) + T_MANTA_DECK);
    if (etype === d0) return;
    this._icon_clear(d0);
    this._icon_set(etype);
    const k = w16(etype - T_MANTA_DECK);
    m.set16(0xdcc, k);
    m.set32(0x492, this._bay_object(MANTA_OBJS, k));
    m.set32(0x48e, this._bay_record(MANTA_RECS, k));
    m.set16(0x95c, 2);
  }

  /** $11F92 (types 86-89): the Manta the deck commands act on (to deck, land). */
  cmd_manta_deck_select(bay: number): boolean {
    if (!(0 <= bay && bay <= 3 && this._clickable(T_MANTA_DECK + bay))) return false;
    this._manta_deck_select(T_MANTA_DECK + bay, bay);
    return true;
  }

  /** $12360 (144,155): abort the landing of Manta [$DDC] while it is still in the
   * approach (step +$1D <= 3): it flies on (mode 3); without a waypoint it gets one ahead
   * of the carrier ($1207A). */
  cmd_manta_abort_landing(): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON)) return false;
    if (!m.u8(0x1232)) {
      this._message("No aircraft is currently landing");
      return true;
    }
    const d1 = m.u16(0xddc);
    const a0 = this._bay_record(MANTA_RECS, d1);
    if (m.u8(a0 + 0x1d) > 3) {
      this._message("Too late to abort landing");
      return true;
    }
    m.set8(0x1232, 0);
    m.set8(a0 + 0x1c, 3);
    m.set8(a0 + 0x1d, 0);
    if (!(m.u8(a0) & 2)) {
      m.set8(a0, (m.u8(a0) | 2) & 0xfb);
      this._manta_waypoint_ahead(a0, d1);
    }
    m.set16(0x95c, 2);
    this._unit_message("Manta", m.u16(0xddc), "- Landing aborted");
    return true;
  }

  /** $1207A: cruise 60, height (bay + 1) * 300 + 500, 6000 ahead of the carrier. */
  _manta_waypoint_ahead(a0: number, d1: number): void {
    const m = this.mem;
    const c = CARRIER;
    m.set16(a0 + 0x2a, 0x3c);
    m.set32(a0 + 0x22, s16(w16(w16(d1 + 1) * 0x12c + 0x1f4)));
    this.set_matrix(m.u16(c + 6));
    const [x, z] = this.matrix_rotate(0, 0xe890);
    m.set32(a0 + 0x1e, s32(x + m.u32(c + 0x0a)));
    m.set32(a0 + 0x26, s32(z + m.u32(c + 0x12)));
  }

  /** $1773C (screen 26): the number keys select ($11F92). */
  manta_deck_input(): void {
    this.key_select(T_MANTA_DECK, (e, i) => this._manta_deck_select(e, i));
  }

  // ---- Manta map, screen 25 (panels $41B50, $41CD6) ---------------------------------------
  /** $FF1E: select Manta i ($48C, records); its icon lit already: recall its course. */
  _manta_map_select(etype: number, i: number): void {
    const m = this.mem;
    if (m.u16(MAP_INFO)) return;
    if (this._icon_set(etype)) {
      this._manta_recall();
      return;
    }
    this._icon_clear(m.u16(0x48c) + T_MANTA);
    m.set16(0x48c, i);
    this.select_manta();
  }

  /** $FF1E (types 58-61 on the Manta map): select Manta `bay`; clicking the selected
   * one again recalls its course into the map's edit values ($12ECC). */
  cmd_manta_map_select(bay: number): boolean {
    if (!(0 <= bay && bay <= 3 && this._clickable(T_MANTA + bay))) return false;
    this._manta_map_select(T_MANTA + bay, bay);
    return true;
  }

  /** $12ECC: the Manta's course (+$1E/+$22/+$26, cruise +$2A, announce bit 2) into the
   * edit values $69E/$6A2/$6A6, $69C, $6AC; the map centred on it at zoom 0. */
  _manta_recall(): void {
    const m = this.mem;
    const a0 = this._manta_rec();
    const bay = m.u16(0x48c);
    if (m.u8(a0 + 1) & 0x80) return this._unit_message("Manta", bay, "no longer exists");
    if (!(m.u8(a0) & 2)) return this._unit_message("Manta", bay, "has no course to recall");
    m.set32(0x69e, m.u32(a0 + 0x1e));
    m.set32(MAP_X, m.u32(a0 + 0x1e));
    m.set32(0x6a2, m.u32(a0 + 0x22));
    m.set32(0x6a6, m.u32(a0 + 0x26));
    m.set32(MAP_Z, m.u32(a0 + 0x26));
    m.set16(0x69c, m.u16(a0 + 0x2a));
    m.set8(0x6ac, m.u8(a0) & 4 ? 0xff : 0);
    this._manta_map_icons();
    this._map_zoom_set(0);
    this._unit_message("Manta", bay, "course recalled");
  }

  /** $12D72: icons 91 (draw mode $6AA) and 92 (announce $6AC). */
  _manta_map_icons(): void {
    const m = this.mem;
    this._icon(T_DRAW, m.u16(0x6aa));
    this._icon(T_ANNOUNCE, m.u8(0x6ac));
  }

  /** $12E1E: icons 91 ($6B8) and 92 ($6BA). */
  _walrus_map_icons(): void {
    const m = this.mem;
    this._icon(T_DRAW, m.u16(0x6b8));
    this._icon(T_ANNOUNCE, m.u8(0x6ba));
  }

  /** $12FC4 (13,178). A fresh press gives the selected Manta the edited course: bit 1,
   * bit 2 = announce $6AC, cruise +$2A = $69C, waypoint +$1E/+$22/+$26 = $69E/$6A2/$6A6
   * ("course set", or "course set - engage autopilot" when manual). Held (auto-repeat),
   * a Manta with a course becomes automatic ("autopilot engaged"; its mode isn't set). */
  cmd_manta_set_course(held = 0): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON)) return false;
    if (m.u16(MAP_INFO)) return true;
    const a0 = this._manta_rec();
    const bay = m.u16(0x48c);
    let d0 = m.u8(a0);
    if (held) {
      if (!(d0 & 2) || d0 & 1) return true;
      m.set8(a0, d0 | 1);
      this._unit_message("Manta", bay, "autopilot engaged");
      return true;
    }
    if (m.u8(a0 + 1) & 0x80) {
      this._unit_message("Manta", bay, "no longer exists");
      return true;
    }
    d0 = (d0 | 2) & 0xfb;
    if (m.u8(0x6ac)) d0 |= 4;
    m.set8(a0, d0);
    m.set16(a0 + 0x2a, m.u16(0x69c));
    m.set32(a0 + 0x1e, m.u32(0x69e));
    m.set32(a0 + 0x22, m.u32(0x6a2));
    m.set32(a0 + 0x26, m.u32(0x6a6));
    this._unit_message("Manta", bay, d0 & 1 ? "course set" : "course set - engage autopilot");
    return true;
  }

  /** $12F8E (57,178): the selected Manta's course cleared (bit 1). */
  cmd_manta_clear_course(): boolean {
    return this._clear_course(this._manta_rec(), "Manta", 0x48c);
  }

  _clear_course(a0: number, kind: string, sel: number): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON)) return false;
    if (m.u16(MAP_INFO)) return true;
    const bay = m.u16(sel);
    if (m.u8(a0 + 1) & 0x80) {
      this._unit_message(kind, bay, "no longer exists");
    } else if (m.u8(a0) & 2) {
      m.set8(a0, m.u8(a0) & 0xfd);
      this._unit_message(kind, bay, "course cleared");
    } else {
      this._unit_message(kind, bay, "course already clear");
    }
    return true;
  }

  _edit(a: number, v: number, lo: number, hi: number): void {
    v = s16(v);
    this.mem.set16(a, v > hi ? hi : v < lo ? lo : v);
  }

  /** $12C90: the course's cruise speed $69C + 16 (50..250; repeats). */
  cmd_manta_cruise_up(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    if (!this.mem.u16(MAP_INFO)) this._edit(0x69c, this.mem.u16(0x69c) + 0x10, 0x32, 0xfa);
    return true;
  }

  /** $12CA2: $69C - 16. */
  cmd_manta_cruise_down(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    if (!this.mem.u16(MAP_INFO)) this._edit(0x69c, this.mem.u16(0x69c) - 0x10, 0x32, 0xfa);
    return true;
  }

  /** $12CB4, the cruise slider (x 288, y 159..195): $69C = (195 - y) * 7, 50..250. */
  cmd_manta_cruise(y: number): boolean {
    if (!this._slider(y, MAP_SLIDER_Y)) return false;
    if (!this.mem.u16(MAP_INFO)) this._edit(0x69c, s16(w16(0xc3 - y)) * 7, 0x32, 0xfa);
    return true;
  }

  /** $12D1C: the course altitude $6A2 (long), 250..3500. */
  _altitude(v: number): void {
    v = s32(v);
    this.mem.set32(0x6a2, v > 0xdac ? 0xdac : v < 0xfa ? 0xfa : v);
  }

  /** $12CE2: the course's altitude $6A2 + 230 (250..3500; repeats). */
  cmd_manta_altitude_up(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    if (!this.mem.u16(MAP_INFO)) this._altitude(this.mem.u32(0x6a2) + 0xe6);
    return true;
  }

  /** $12CF6: $6A2 - 230. */
  cmd_manta_altitude_down(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    if (!this.mem.u16(MAP_INFO)) this._altitude(this.mem.u32(0x6a2) - 0xe6);
    return true;
  }

  /** $12D0A, the altitude slider (x 244, y 159..195): $6A2 = (195 - y) * 97. */
  cmd_manta_altitude(y: number): boolean {
    if (!this._slider(y, MAP_SLIDER_Y)) return false;
    if (!this.mem.u16(MAP_INFO)) this._altitude(s16(w16(0xc3 - y)) * 0x61);
    return true;
  }

  /** $12D40 (type 91): toggle $6AA (what the Manta map draws; display), icons. No
   * fresh-press check: holding it toggles every pass. */
  cmd_manta_map_draw_mode(): boolean {
    const m = this.mem;
    if (!this._clickable(T_DRAW)) return false;
    if (!m.u16(MAP_INFO)) {
      m.set8(0x6ab, m.u8(0x6ab) ^ 1);
      this._manta_map_icons();
    }
    return true;
  }

  /** $12D50 (type 92): toggle $6AC, the next course's "report arrival" bit, icons. */
  cmd_manta_toggle_announce(): boolean {
    const m = this.mem;
    if (!this._clickable(T_ANNOUNCE)) return false;
    if (!m.u16(MAP_INFO)) {
      m.set8(0x6ac, ~m.u8(0x6ac));
      this._manta_map_icons();
    }
    return true;
  }

  /** The UI's "fly to (x, z)": the Manta map cursor $69E/$6A6 at world (x, z), as a map
   * click writes it, then a fresh press of set course (altitude $6A2 and cruise $69C as
   * edited). Engage with cmd_manta_set_course(1) or cmd_manta_autopilot(). */
  cmd_set_manta_waypoint(x: number, z: number): boolean {
    const m = this.mem;
    if (!(this._clickable(T_BUTTON, [MAP_INFO, 0]) && this._clickable(T_BUTTON))) return false;
    m.set32(0x69e, s32(x));
    m.set32(0x6a6, s32(z));
    return this.cmd_manta_set_course(0);
  }

  // ---- Manta arming, screen 24 (input $17610; panels $428A2, $42966, $429FE) ------------
  /** $116C6: the arming screen's selection $DB4, record $48E, sub-panel $DC0. */
  _manta_arming_select(etype: number, i: number): void {
    const m = this.mem;
    const d0 = w16(m.u16(0xdb4) + T_MANTA_ARMING);
    if (etype === d0) return;
    this._icon_clear(d0);
    this._icon_set(etype);
    const k = w16(etype - T_MANTA_ARMING);
    m.set16(0xdb4, k);
    const rec = this._bay_record(MANTA_RECS, k);
    m.set32(0x48e, rec);
    m.set16(0xdc0, this._subpanel(rec));
    this._manta_panel();
    m.set16(0x95c, 2);
  }

  /** $116C6 (types 74-77): the bay the hangar commands and loadout act on. */
  cmd_manta_arming_select(bay: number): boolean {
    if (!(0 <= bay && bay <= 3 && this._clickable(T_MANTA_ARMING + bay))) return false;
    this._manta_arming_select(T_MANTA_ARMING + bay, bay);
    return true;
  }

  /** $17610 (screen 24): the number keys select ($116C6). */
  manta_arming_input(): void {
    this.key_select(T_MANTA_ARMING, (e, i) => this._manta_arming_select(e, i));
  }

  /** $11886 (12,178, type 78): send the arming screen's Manta ([$48E], bay [$DB4]) up
   * the lift: the body of $11894 (cmd_manta_to_deck) with d1 = [$DB4]. */
  cmd_manta_arming_to_deck(): boolean {
    const m = this.mem;
    if (!this._clickable(T_HANGAR)) return false;
    const keep = m.u16(0xdcc);
    m.set16(0xdcc, m.u16(0xdb4));
    try {
      this.cmd_manta_to_deck();
    } finally {
      m.set16(0xdcc, keep);
    }
    return true;
  }

  /** The Manta loadout panel's redraw ($B418 with $42966, callback $C580, while
   * [$DC0] = 0): besides drawing, it marks the Manta [$48E] unbalanced (+$1C) when the
   * wing loads differ by more than 80 ($C640, _manta_balance). */
  _manta_panel(): void {
    const m = this.mem;
    if (m.u16(0xdc0)) return;
    if (m.u16(LOADOUT_CURSOR) >= 6) {
      throw new NotPorted(`loadout cursor ${m.u16(LOADOUT_CURSOR)} beyond the list $C770 (panel draw)`);
    }
    this._manta_balance(this._manta_rec());
  }

  /** The Walrus loadout panel's redraw ($B418 with $427B6, callback $BFE8, while
   * [$DC2] = 0): with the list on the Harbinger, its entry's weight $CB7A = 100 + 150 n
   * (n = [$CB80]) and the +/- buttons (type 81) patched to the Harbinger count
   * ($11CE0/$11D0C); on the ACCB to its building type ($11D1C/$11D40). The buttons are
   * shown (flag 0) only for those two, else hidden ($8000). */
  _walrus_panel(): void {
    const m = this.mem;
    if (m.u16(0xdc2)) return;
    const k = m.u16(LOADOUT_CURSOR + 2);
    if (k >= 5) throw new NotPorted(`loadout cursor ${k} beyond the list $C242`);
    const code = m.u16(WALRUS_CODES + 2 * k);
    let d1 = 0x8000;
    if (code === 7) {
      d1 = 0;
      m.set16(LOADOUT + 26 * 7, w16(m.u16(HARBINGERS) * 0x96 + 0x64));
      m.set32(PLUS_HANDLER, 0x11ce0);
      m.set32(MINUS_HANDLER, 0x11d0c);
    }
    if (code === 9) {
      d1 = 0;
      m.set32(PLUS_HANDLER, 0x11d1c);
      m.set32(MINUS_HANDLER, 0x11d40);
    }
    m.set16(this._flag(T_HARBINGERS), d1);
  }

  /** $1132E/$11352, $11A30/$11A76: a list cursor + 1 (unsigned wrap at n) / - 1. */
  _cursor_step(a: number, n: number, d: number): void {
    const m = this.mem;
    let v = w16(m.u16(a) + d);
    if (d > 0) v = v < n ? v : 0;
    else if (v & 0x8000) v = n - 1;
    m.set16(a, v);
  }

  /** $1132E (142,84): the stores list cursor $DB8 + 1 (0..5: Assassin x1, x2, x3,
   * Quaker, Quasar, comms pod; repeats). */
  cmd_manta_loadout_next(): boolean {
    if (!this._clickable(T_BUTTON, [0xdc0, 0])) return false;
    this._cursor_step(LOADOUT_CURSOR, 6, 1);
    this._manta_panel();
    return true;
  }

  /** $11352 (142,108): $DB8 - 1. */
  cmd_manta_loadout_prev(): boolean {
    if (!this._clickable(T_BUTTON, [0xdc0, 0])) return false;
    this._cursor_step(LOADOUT_CURSOR, 6, -1);
    this._manta_panel();
    return true;
  }

  _code(table: number, k: number, n: number): number {
    if (k >= n) throw new NotPorted(`loadout cursor ${k} beyond the list $${table.toString(16).toUpperCase()}`);
    return this.mem.u16(table + 2 * k);
  }

  /** mulu.w #$1A + adda.w: the loadout entry of a code. */
  _entry(code: number): number {
    if (code > 11) throw new NotPorted(`loadout code ${code} beyond the table $CAC4`);
    return LOADOUT + s16(w16(code * 0x1a));
  }

  /** $F73C: the drop callback $468, modal mode $460 (the pointer clip is UI). */
  _start_drag(callback: number): void {
    this.mem.set32(DROP, callback);
    this.mem.set8(MODAL, 0xff);
  }

  /** $1135E (123,85): pick up the stores item at the cursor to drag it onto a slot
   * ("Stores unable to supply payload" if fewer are on board than it holds). Then
   * cmd_drag_release(px, py) over a slot; the next ui_frame fits it (drop $113A4). */
  cmd_manta_pick_loadout(): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON, [0xdc0, 0])) return false;
    const a1 = this._entry(this._code(MANTA_CODES, m.u16(LOADOUT_CURSOR), 6));
    if (m.u16(a1 + 6) > m.u16(m.u32(a1 + 2))) {
      this._message("Stores unable to supply payload");
      return true;
    }
    this._start_drag(0x113a4);
    return true;
  }

  /** $114C8 (slots 0 fuselage at 218,70, 1/2 wings at 195,90 / 240,90): pick up a
   * fitted item to drag it back to the stores box (drop $11500). */
  cmd_manta_pick_slot(k: number): boolean {
    const m = this.mem;
    if (!(0 <= k && k <= 2 && this._clickable(T_BUTTON, [0xdc0, 0]))) return false;
    const off = m.u16(0x11448 + 2 * k);
    if (m.u16(this._manta_rec() + off)) {
      m.set32(DRAG_ELEMENT, MANTA_SLOTS[k]);
      this._start_drag(0x11500);
    }
    return true;
  }

  /** $B52C over the slot elements: the first whose box holds the pointer. */
  _slot_at(slots: number[], px: number, py: number): number | null {
    for (const a of slots) {
      const [k, x0, y0, x1, y1] = SLOT_ELEMENTS[a];
      if (x0 <= px && px <= x1 && y0 <= py && py <= y1) return k;
    }
    return null;
  }

  _dragged_slot(): number {
    const a = this.mem.u32(DRAG_ELEMENT);
    if (!(a in SLOT_ELEMENTS)) throw new NotPorted(`dragged element $${a.toString(16).toUpperCase()} is not a slot`);
    return SLOT_ELEMENTS[a][0];
  }

  _in_stores_box(px: number, py: number): boolean {
    const [x0, y0, x1, y1] = STORES_BOX;
    return x0 <= px && px <= x1 && y0 <= py && py <= y1;
  }

  /** $113A4: dropped on slot k: wings (k >= 1) take Assassins only (codes <= 2); the
   * weight +$12 - old item + new must stay <= 750; the old item goes back to stock and
   * the new one is taken from it. */
  _manta_drop_loadout(px: number, py: number): void {
    const m = this.mem;
    const k = this._slot_at(MANTA_SLOTS, px, py);
    if (k === null) return;
    const a0 = this._manta_rec();
    const d1 = this._code(MANTA_CODES, m.u16(LOADOUT_CURSOR), 6);
    const a1 = this._entry(d1);
    if (k && d1 > 2) return this._message("Payload cannot be wing mounted");
    const off = m.u16(0x11448 + 2 * k);
    const d3 = m.u16(a0 + off);
    const a2 = this._entry(d3);
    const d0 = w16(m.u16(a0 + 0x12) - m.u16(a2) + m.u16(a1));
    if (s16(d0) > 0x2ee) return this._message("Payload capacity exceeded");
    m.set16(a0 + 0x12, d0);
    if (d3) {
      const p = m.u32(a2 + 2);
      m.set16(p, m.u16(p) + m.u16(a2 + 6));
    }
    m.set16(a0 + off, d1);
    const p = m.u32(a1 + 2);
    m.set16(p, m.u16(p) - m.u16(a1 + 6));
    this._manta_panel();
  }

  /** $11500: dropped in the stores box: the slot emptied, its item back to stock, the
   * weight reduced, "Payload returned to stores". */
  _manta_drop_return(px: number, py: number): void {
    const m = this.mem;
    if (!this._in_stores_box(px, py)) return;
    const a0 = this._manta_rec();
    const off = m.u16(0x11448 + 2 * this._dragged_slot());
    const d0 = m.u16(a0 + off);
    m.set16(a0 + off, 0);
    const a1 = this._entry(d0);
    const p = m.u32(a1 + 2);
    m.set16(p, m.u16(p) + m.u16(a1 + 6));
    m.set16(a0 + 0x12, m.u16(a0 + 0x12) - m.u16(a1));
    this._message("Payload returned to stores");
    this._manta_panel();
  }

  /** The end of a drag: the pointer at (px, py) and the left button released; the next
   * ui_frame runs the drop ($F6EA). */
  cmd_drag_release(px: number, py: number): void {
    this._set_pointer(px, py);
    this.input_mouse_button(0, 0);
  }

  /** $F6EA, ui_frame during a drag: once the button is up, modal mode ends ($F712) and
   * the drop callback [$468] runs with the pointer (the hit test it repeats only marks
   * the element under the pointer, UI). */
  drag_drop(): void {
    const m = this.mem;
    if (m.u8(0x880)) return;
    m.set8(MODAL, 0);
    const cb = m.u32(DROP);
    const handler = DROPS[cb];
    if (handler === undefined) throw new NotPorted(`drop callback $${cb.toString(16).toUpperCase()} ($F6EA: jmp [$468])`);
    (this as any)[handler](m.u16(0x474), m.u16(0x476));
  }

  /** Cursor clicks until the list shows `code`, pick it up, drop it on `slot`. */
  _fit(kind: string, slot: number, code: number): boolean {
    const m = this.mem;
    const manta = kind === "Manta";
    const [codes, n, a] = manta ? [MANTA_CODES, 6, LOADOUT_CURSOR] : [WALRUS_CODES, 5, LOADOUT_CURSOR + 2];
    const slots = manta ? MANTA_SLOTS : WALRUS_SLOTS;
    if (!(0 <= slot && slot < slots.length)) return false;
    const nxt = manta ? () => this.cmd_manta_loadout_next() : () => this.cmd_walrus_loadout_next();
    const pick = manta ? () => this.cmd_manta_pick_loadout() : () => this.cmd_walrus_pick_loadout();
    let found = false;
    for (let j = 0; j < n + 1; j++) {
      if (m.u16(a) < n && m.u16(codes + 2 * m.u16(a)) === code) {
        found = true;
        break;
      }
      if (!nxt()) return false;
    }
    if (!found) return false;
    if (!pick() || !m.u8(MODAL)) return false;
    const [, x0, y0, x1, y1] = SLOT_ELEMENTS[slots[slot]];
    this.cmd_drag_release(pyDiv(x0 + x1, 2), pyDiv(y0 + y1, 2));
    this.ui_frame();
    return true;
  }

  _unfit(kind: string, slot: number): boolean {
    const m = this.mem;
    const ok = kind === "Manta" ? this.cmd_manta_pick_slot(slot) : this.cmd_walrus_pick_slot(slot);
    if (!ok || !m.u8(MODAL)) return false;
    const [x0, y0, x1, y1] = STORES_BOX;
    this.cmd_drag_release(pyDiv(x0 + x1, 2), pyDiv(y0 + y1, 2));
    this.ui_frame();
    return true;
  }

  /** Load `code` (1-3 Assassins, 4 Quaker, 5 Quasar, 10 comms pod) into slot 0
   * (fuselage) or 1/2 (wings) of the arming screen's Manta as a player does it: cursor
   * clicks, pick up, drop on the slot (ui_frame runs the drop). False if a click wasn't
   * possible; refusals are messages. */
  cmd_manta_fit(slot: number, code: number): boolean {
    return this._fit("Manta", slot, code);
  }

  /** Drag slot 0-2's item back to the stores box. */
  cmd_manta_unfit(slot: number): boolean {
    return this._unfit("Manta", slot);
  }

  // ---- Walrus control, screen 18 (input $17374; panels $42470, $4259C, $4255C, $4251C, $424DC)
  /** $10016: select Walrus i in place: old icon off, the old object [$56C] visible;
   * $566 = i, records ($14224); camera and viewed = it (invisible), camera mode 1;
   * controllable / controlled and icons ($13F5A). */
  _walrus_select(etype: number, i: number): void {
    const m = this.mem;
    if (this._icon_set(etype)) return;
    this._icon_clear(m.u16(0x566) + T_WALRUS);
    this._set_visible(m.u32(0x56c));
    m.set16(0x566, i);
    const [rec, o] = this.select_walrus();
    m.set32(VIEWED, o);
    m.set16(CAM_MODE, 1);
    m.set32(CAMERA, o);
    m.set8(o + 1, m.u8(o + 1) & 0xfd);
    this.walrus_view_update(rec);
  }

  /** $10016 (types 70-73 on the control screen 18; number keys alike): drive Walrus
   * `bay` from now on. */
  cmd_walrus_select(bay: number): boolean {
    if (!(0 <= bay && bay <= 3 && this._clickable(T_WALRUS + bay))) return false;
    this._walrus_select(T_WALRUS + bay, bay);
    return true;
  }

  /** $FF7C (types 62-64): weapon +$18 = 0 Avatar laser, 1 Harbinger missile, 2 payload
   * (virus bomb, ACCB, fuel pod); icons ($14004). */
  cmd_walrus_weapon(w: number): boolean {
    const m = this.mem;
    if (!(0 <= w && w <= 2 && this._clickable(T_WEAPON + w))) return false;
    if (!(m.u8(this._flag(T_WEAPON + w)) & 0x20)) {
      m.set16(this._walrus_rec() + 0x18, w);
      this._walrus_icons();
    }
    return true;
  }

  /** $12B00 (type 90): needs a controllable Walrus ($70E). Off: needs a course ("No
   * course set"): automatic, mode 3, "Autopilot on". On: in launch mode 2 only +$33 is
   * set; in mode 3 off. Then bits 5 and 7 cleared (turret mode, rudder centring). */
  cmd_walrus_autopilot(): boolean {
    const m = this.mem;
    if (!this._clickable(T_AUTOPILOT)) return false;
    if (!m.u16(0x70e)) return true;
    const a2 = this._walrus_rec();
    const a0 = this._flag(T_AUTOPILOT);
    if (!(m.u8(a0) & 0x20)) {
      if (!(m.u8(a2) & 2)) {
        this._message("No course set");
        return true;
      }
      m.set8(a0, m.u8(a0) | 0x20);
      m.set8(a2, m.u8(a2) | 1);
      m.set8(a2 + 0x1a, 3);
      this._message("Autopilot on");
    } else {
      const mode = m.u8(a2 + 0x1a);
      if (mode === 2) {
        m.set8(a2 + 0x33, 0xff);
        return true;
      }
      if (mode !== 3) return true;
      m.set8(a0, m.u8(a0) & 0xdf);
      m.set8(a2, m.u8(a2) & 0xfe);
    }
    m.set8(a2, m.u8(a2) & 0x5f);
    this._walrus_icons();
    this._turret_icon();
    return true;
  }

  /** $12982 (100,155): manual: a fresh press sets bit 7 (the rudder self-centres);
   * held, bit 6 and the rudder +2 = 0. */
  cmd_walrus_centre_rudder(held = 0): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON)) return false;
    const a2 = this._walrus_rec();
    if (m.u8(a2) & 1) return true;
    if (held) {
      m.set8(a2, m.u8(a2) | 0x40);
      m.set16(a2 + 2, 0);
    } else {
      m.set8(a2, m.u8(a2) | 0x80);
    }
    return true;
  }

  /** $129A2 (type 80): controllable and manual: toggle bit 5 (the mouse turns the
   * turret, not the hull); off also clears bit 6. The rudder +2 = 0, bit 7 cleared. */
  cmd_walrus_turret_mode(): boolean {
    const m = this.mem;
    if (!this._clickable(T_TURRET)) return false;
    const a2 = this._walrus_rec();
    if (!m.u16(0x70e) || m.u8(a2) & 1) return true;
    let f = m.u8(a2) ^ 0x20;
    if (!(f & 0x20)) f &= 0xbf;
    m.set8(a2, f);
    this._turret_icon();
    m.set16(a2 + 2, 0);
    m.set8(a2, m.u8(a2) & 0x7f);
    return true;
  }

  /** $100D4: the instrument page +$16 = (+1) mod 3 (display). */
  cmd_walrus_instruments(): boolean {
    return this._instrument_page(this._walrus_rec() + 0x16);
  }

  /** $FE0A, the throttle slider (x 288, y 158..197; repeats): manual: target speed
   * +6 = (195 - y) * $2AA >> 8 (asr), clamped to 0..96. */
  cmd_walrus_throttle(y: number): boolean {
    const m = this.mem;
    if (!this._slider(y, VEHICLE_THROTTLE_Y)) return false;
    const a0 = this._walrus_rec();
    if (!(m.u8(a0) & 1)) {
      const v = s16(w16(s16(w16(0xc3 - y)) * 0x2aa)) >> 8;
      m.set16(a0 + 6, v > 0x60 ? 0x60 : v < 0 ? 0 : v);
    }
    return true;
  }

  /** $1012E (type 104): controllable: toggle +$32, icons. */
  cmd_walrus_sight(): boolean {
    const m = this.mem;
    if (!this._clickable(T_SIGHT)) return false;
    if (m.u16(0x70e)) {
      const a2 = this._walrus_rec();
      m.set8(a2 + 0x32, ~m.u8(a2 + 0x32));
      this._walrus_icons();
    }
    return true;
  }

  /** $10190 (type 105): controllable: toggle the rear view $8F1. */
  cmd_walrus_rear_view(): boolean {
    return this._rear_view(0x70e);
  }

  /** Mouse-steer input that makes the next walrus_step set the selected Walrus's rudder
   * +2 (clamped to +-255) when it is the viewed one, manual, in mouse-steer mode and not
   * steering a missile. */
  cmd_walrus_steer_to(rudder: number): void {
    const m = this.mem;
    const a = this._walrus_rec();
    m.set16(MOUSE_DX, w16(Math.max(-255, Math.min(255, rudder)) - m.s16(a + 2)));
  }

  /** While the selected Walrus steers a Harbinger (bit 4), the mouse turns the missile:
   * deltas that make its turn rates (missile +$1E / +$20) yaw and pitch (+-255) on the
   * next walrus_step. False (nothing written) when it isn't steering one. */
  cmd_walrus_missile_steer_to(yaw: number, pitch: number): boolean {
    const m = this.mem;
    const o = m.u32(m.u32(0x56c) + 0x20);
    if (!(m.u8(this._walrus_rec()) & 0x10) || !(OBJECTS <= o && o < OBJECTS + 64 * OBJECT_SIZE)) return false;
    m.set16(MOUSE_DX, w16(Math.max(-255, Math.min(255, yaw)) - m.s16(o + 0x1e)));
    m.set16(MOUSE_DX + 2, w16(Math.max(-255, Math.min(255, pitch)) - m.s16(o + 0x20)));
    return true;
  }

  /** $17374 (screen 18): number keys select ($10016); re-enter when the status crosses
   * 8 ($70E); gauge timers; automatic: the auto button follows bit 0; manual: a right
   * click toggles mouse-steer mode, except while steering a missile (bit 4). */
  walrus_control_input(): void {
    const m = this.mem;
    this.key_select(T_WALRUS, (e, i) => this._walrus_select(e, i));
    if (this._vehicle_reenter(this._walrus_rec(), 0x70e, 0x12)) return;
    for (const t of [0x954, 0x958, 0x962]) if (m.u16(t)) m.set16(t, m.u16(t) - 1);
    const a2 = this._walrus_rec();
    if (m.u8(a2) & 1) {
      if (!(m.u8(this._flag(T_AUTOPILOT)) & 0x20)) this._walrus_icons();
      return;
    }
    if (this._bclr0(0x883)) {
      if (m.u8(a2) & 0x10) return;
      m.set8(POINTER_MODE, ~m.u8(POINTER_MODE));
    }
    if (m.u8(this._flag(T_AUTOPILOT)) & 0x20) this._walrus_icons();
  }

  // ---- Walrus dock, screen 19 (input $17782; panel $42C1A) --------------------------------
  /** $1249E: the dock screen's own selection $DE4 (records $568/$56C; $566 kept). */
  _walrus_dock_select(etype: number, i: number): void {
    const m = this.mem;
    const d0 = w16(m.u16(0xde4) + T_WALRUS_DOCK);
    if (etype === d0) return;
    this._icon_clear(d0);
    this._icon_set(etype);
    const k = w16(etype - T_WALRUS_DOCK);
    m.set16(0xde4, k);
    m.set32(0x56c, this._bay_object(WALRUS_OBJS, k));
    m.set32(0x568, this._bay_record(WALRUS_RECS, k));
    m.set16(0x95c, 2);
  }

  /** $1249E (types 93-96): the Walrus launch and dock act on. */
  cmd_walrus_dock_select(bay: number): boolean {
    if (!(0 <= bay && bay <= 3 && this._clickable(T_WALRUS_DOCK + bay))) return false;
    this._walrus_dock_select(T_WALRUS_DOCK + bay, bay);
    return true;
  }

  /** $128AE (144,155): abort Walrus [$6E0]'s docking while it is still approaching
   * (mode 0, step 0): it drives on (mode 3); without a waypoint it gets one astern of the
   * carrier ($12786). */
  cmd_walrus_abort_docking(): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON)) return false;
    if (!m.u8(0x1233)) {
      this._message("No AAV is currently docking");
      return true;
    }
    const d0 = m.u16(0x6e0);
    const a1 = this._bay_record(WALRUS_RECS, d0);
    const a0 = this._bay_object(WALRUS_OBJS, d0);
    if (m.u8(a1 + 0x1a)) {
      this._message("No AAV is currently docking");
      return true;
    }
    if (m.u8(a1 + 0x1b)) {
      this._message("Too late to abort docking");
      return true;
    }
    m.set8(0x1233, 0);
    m.set8(a1 + 0x1a, 3);
    m.set8(a1 + 0x1b, 0);
    if (!(m.u8(a1) & 2)) {
      m.set8(a1, (m.u8(a1) | 2) & 0xfb);
      this._walrus_waypoint_astern(a0, a1);
    }
    m.set16(0x95c, 2);
    this._unit_message("Walrus", m.u16(0x6e0), "- Docking aborted");
    return true;
  }

  /** $12786: cruise 60, a waypoint astern of the carrier, by the bay's lane. */
  _walrus_waypoint_astern(a0: number, a1: number): void {
    const m = this.mem;
    const c = CARRIER;
    m.set16(a1 + 0x24, 0x3c);
    this.set_matrix(m.u16(c + 6));
    const [x, z] = this.matrix_rotate(w16(m.u16(a0 + 0x1a) * 0x7d0 - 0xbb8), 0xf060);
    m.set32(a1 + 0x1c, s32(x + m.u32(c + 0x0a)));
    m.set32(a1 + 0x20, s32(z + m.u32(c + 0x12)));
  }

  /** $17782 (screen 19): the number keys select ($1249E). */
  walrus_dock_input(): void {
    this.key_select(T_WALRUS_DOCK, (e, i) => this._walrus_dock_select(e, i));
  }

  // ---- Walrus map, screen 17 (panels $41C3E, $41DC6) --------------------------------------
  /** $FFB8: select Walrus i ($566, records); selected already: recall its course. */
  _walrus_map_select(etype: number, i: number): void {
    const m = this.mem;
    if (m.u16(MAP_INFO)) return;
    if (this._icon_set(etype)) {
      this._walrus_recall();
      return;
    }
    this._icon_clear(m.u16(0x566) + T_WALRUS);
    m.set16(0x566, i);
    this.select_walrus();
  }

  /** $FFB8 (types 70-73 on the Walrus map): select Walrus `bay`; again: recall its
   * course ($12F30). */
  cmd_walrus_map_select(bay: number): boolean {
    if (!(0 <= bay && bay <= 3 && this._clickable(T_WALRUS + bay))) return false;
    this._walrus_map_select(T_WALRUS + bay, bay);
    return true;
  }

  /** $12F30: the course (+$1C/+$20, cruise +$24, bit 2) into $6B0/$6B4, $6AE, $6BA. */
  _walrus_recall(): void {
    const m = this.mem;
    const a0 = this._walrus_rec();
    const bay = m.u16(0x566);
    if (m.u8(a0 + 1) & 0x80) return this._unit_message("Walrus", bay, "no longer exists");
    if (!(m.u8(a0) & 2)) return this._unit_message("Walrus", bay, "has no course to recall");
    m.set32(0x6b0, m.u32(a0 + 0x1c));
    m.set32(MAP_X, m.u32(a0 + 0x1c));
    m.set32(0x6b4, m.u32(a0 + 0x20));
    m.set32(MAP_Z, m.u32(a0 + 0x20));
    m.set16(0x6ae, m.u16(a0 + 0x24));
    m.set8(0x6ba, m.u8(a0) & 4 ? 0xff : 0);
    this._walrus_map_icons();
    this._map_zoom_set(0);
    this._unit_message("Walrus", bay, "course recalled");
  }

  /** $13122 (13,178): fresh: the edited course (bit 1, bit 2 = $6BA, cruise +$24 = $6AE,
   * waypoint +$1C/+$20 = $6B0/$6B4); held: engage the autopilot (bit 0). */
  cmd_walrus_set_course(held = 0): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON)) return false;
    if (m.u16(MAP_INFO)) return true;
    const a0 = this._walrus_rec();
    const bay = m.u16(0x566);
    let d0 = m.u8(a0);
    if (held) {
      if (!(d0 & 2) || d0 & 1) return true;
      m.set8(a0, d0 | 1);
      this._unit_message("Walrus", bay, "autopilot engaged");
      return true;
    }
    if (m.u8(a0 + 1) & 0x80) {
      this._unit_message("Walrus", bay, "no longer exists");
      return true;
    }
    d0 = (d0 | 2) & 0xfb;
    if (m.u8(0x6ba)) d0 |= 4;
    m.set8(a0, d0);
    m.set16(a0 + 0x24, m.u16(0x6ae));
    m.set32(a0 + 0x1c, m.u32(0x6b0));
    m.set32(a0 + 0x20, m.u32(0x6b4));
    this._unit_message("Walrus", bay, d0 & 1 ? "course set" : "course set - engage autopilot");
    return true;
  }

  /** $130EE (57,178): clear the selected Walrus's course. */
  cmd_walrus_clear_course(): boolean {
    return this._clear_course(this._walrus_rec(), "Walrus", 0x566);
  }

  /** $12D98: the course's cruise speed $6AE + 8 (0..96; repeats). */
  cmd_walrus_cruise_up(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    if (!this.mem.u16(MAP_INFO)) this._edit(0x6ae, this.mem.u16(0x6ae) + 8, 0, 0x60);
    return true;
  }

  /** $12DAA: $6AE - 8. */
  cmd_walrus_cruise_down(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    if (!this.mem.u16(MAP_INFO)) this._edit(0x6ae, this.mem.u16(0x6ae) - 8, 0, 0x60);
    return true;
  }

  /** $12DBC, the slider (y 159..195): $6AE = (195 - y) * $2AA >> 8 (lsr.w), 0..96. */
  cmd_walrus_cruise(y: number): boolean {
    if (!this._slider(y, MAP_SLIDER_Y)) return false;
    if (!this.mem.u16(MAP_INFO)) this._edit(0x6ae, w16(s16(w16(0xc3 - y)) * 0x2aa) >> 8, 0, 0x60);
    return true;
  }

  /** $12DEC (type 91): toggle $6B8 (display), icons. */
  cmd_walrus_map_draw_mode(): boolean {
    const m = this.mem;
    if (!this._clickable(T_DRAW)) return false;
    if (!m.u16(MAP_INFO)) {
      m.set8(0x6b9, m.u8(0x6b9) ^ 1);
      this._walrus_map_icons();
    }
    return true;
  }

  /** $12DFC (type 92): toggle $6BA (report arrival for the next course), icons. */
  cmd_walrus_toggle_announce(): boolean {
    const m = this.mem;
    if (!this._clickable(T_ANNOUNCE)) return false;
    if (!m.u16(MAP_INFO)) {
      m.set8(0x6ba, ~m.u8(0x6ba));
      this._walrus_map_icons();
    }
    return true;
  }

  /** The UI's "drive to (x, z)": the Walrus map cursor $6B0/$6B4 at (x, z), then a
   * fresh press of set course. */
  cmd_set_walrus_waypoint(x: number, z: number): boolean {
    const m = this.mem;
    if (!(this._clickable(T_BUTTON, [MAP_INFO, 0]) && this._clickable(T_BUTTON))) return false;
    m.set32(0x6b0, s32(x));
    m.set32(0x6b4, s32(z));
    return this.cmd_walrus_set_course(0);
  }

  // ---- Walrus arming, screen 16 (input $17748; panels $42708, $427B6, $42864) -------------
  /** $11DEE: the arming selection $DB6, record $568, sub-panel $DC2. */
  _walrus_arming_select(etype: number, i: number): void {
    const m = this.mem;
    const d0 = w16(m.u16(0xdb6) + T_WALRUS_ARMING);
    if (etype === d0) return;
    this._icon_clear(d0);
    this._icon_set(etype);
    const k = w16(etype - T_WALRUS_ARMING);
    m.set16(0xdb6, k);
    const rec = this._bay_record(WALRUS_RECS, k);
    m.set32(0x568, rec);
    m.set16(0xdc2, this._subpanel(rec));
    this._walrus_panel();
    m.set16(0x95c, 2);
  }

  /** $11DEE (types 82-85). */
  cmd_walrus_arming_select(bay: number): boolean {
    if (!(0 <= bay && bay <= 3 && this._clickable(T_WALRUS_ARMING + bay))) return false;
    this._walrus_arming_select(T_WALRUS_ARMING + bay, bay);
    return true;
  }

  /** $17748 (screen 16): the number keys select ($11DEE); the hangar animation $DC8
   * bounces by $DCA between $6000 and $A000. */
  walrus_arming_input(): void {
    const m = this.mem;
    this.key_select(T_WALRUS_ARMING, (e, i) => this._walrus_arming_select(e, i));
    const d1 = m.u16(0xdca);
    let d0 = w16(m.u16(0xdc8) + d1);
    if (!(d1 & 0x8000)) {
      if (d0 >= 0xa000) {
        d0 = 0xa000;
        m.set16(0xdca, w16(-d1));
      }
    } else if (d0 <= 0x6000) {
      d0 = 0x6000;
      m.set16(0xdca, w16(-d1));
    }
    m.set16(0xdc8, d0);
  }

  /** $11A30/$11A76: cursor $DBA (0..4: Avatar, Harbinger, virus bomb, ACCB, fuel pod);
   * on the Harbinger, $CB80 = 1 if any are on board ($B98) else 0. */
  _walrus_cursor_step(d: number): void {
    const m = this.mem;
    this._cursor_step(LOADOUT_CURSOR + 2, 5, d);
    const k = m.u16(LOADOUT_CURSOR + 2);
    if (k >= 5) throw new NotPorted(`loadout cursor ${k} beyond the list $C242`);
    if (m.u16(WALRUS_CODES + 2 * k) === 7) m.set16(HARBINGERS, m.u16(0xb98) ? 1 : 0);
    this._walrus_panel();
  }

  /** $11A30 (142,84): $DBA + 1 (repeats). */
  cmd_walrus_loadout_next(): boolean {
    if (!this._clickable(T_BUTTON, [0xdc2, 0])) return false;
    this._walrus_cursor_step(1);
    return true;
  }

  /** $11A76 (142,108): $DBA - 1. */
  cmd_walrus_loadout_prev(): boolean {
    if (!this._clickable(T_BUTTON, [0xdc2, 0])) return false;
    this._walrus_cursor_step(-1);
    return true;
  }

  /** The "+" button (type 81 at 114,86; repeats): whatever the panel draw installed
   * ([$4281C]): $11CE0 the Harbingers to load $CB80 + 1 (at most the stock $B98 and 9),
   * or $11D1C the ACCB's building type $DC6 + 1 (0..2). */
  cmd_walrus_loadout_plus(): boolean {
    return this._plus_minus(PLUS_HANDLER);
  }

  /** The "-" button (type 81 at 43,86): $11D0C Harbingers - 1 (at least 1) or $11D40
   * the ACCB type - 1. */
  cmd_walrus_loadout_minus(): boolean {
    return this._plus_minus(MINUS_HANDLER);
  }

  _plus_minus(a: number): boolean {
    const m = this.mem;
    if (!this._clickable(T_HARBINGERS, [0xdc2, 0])) return false;
    const h = m.u32(a);
    if (h === 0x11ce0) {
      const d0 = w16(m.u16(HARBINGERS) + 1);
      if (d0 > m.u16(0xb98) || d0 > 9) return true;
      m.set16(HARBINGERS, d0);
    } else if (h === 0x11d0c) {
      const d0 = m.u16(HARBINGERS);
      if (d0 <= 1) return true;
      m.set16(HARBINGERS, d0 - 1);
    } else if (h === 0x11d1c) {
      const d0 = w16(m.u16(0xdc6) + 1);
      m.set16(0xdc6, d0 === 3 ? 0 : d0);
    } else if (h === 0x11d40) {
      const d0 = w16(m.u16(0xdc6) - 1);
      m.set16(0xdc6, d0 & 0x8000 ? 2 : d0);
    } else {
      throw new NotPorted(`arming +/- handler $${h.toString(16).toUpperCase()}`);
    }
    this._walrus_panel();
    return true;
  }

  /** $11A82 (35,86): pick up the item at the cursor (none or too few on board: "Stores
   * unable to supply payload"); drop $11ACE. */
  cmd_walrus_pick_loadout(): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON, [0xdc2, 0])) return false;
    const a1 = this._entry(this._code(WALRUS_CODES, m.u16(LOADOUT_CURSOR + 2), 5));
    const stock = m.u16(m.u32(a1 + 2));
    if (!stock || m.u16(a1 + 6) > stock) {
      this._message("Stores unable to supply payload");
      return true;
    }
    this._start_drag(0x11ace);
    return true;
  }

  /** $11C16 (slot 0 payload at 173,96, slot 1 weapon at 201,96): pick up a fitted item
   * to drag back to stores (drop $11C4E). */
  cmd_walrus_pick_slot(k: number): boolean {
    const m = this.mem;
    if (!(0 <= k && k <= 1 && this._clickable(T_BUTTON, [0xdc2, 0]))) return false;
    const off = m.u16(0x11c12 + 2 * k);
    if (m.u16(this._walrus_rec() + off)) {
      m.set32(DRAG_ELEMENT, WALRUS_SLOTS[k]);
      this._start_drag(0x11c4e);
    }
    return true;
  }

  /** $11ACE: slot 0 (+$26) takes the payloads (entry +8 = 1: virus bomb, ACCB, fuel
   * pod), slot 1 (+$28) the weapons (Avatar, Harbinger), else "Payload cannot be mounted
   * here". Weight <= 2000; an old Harbinger load weighs 100 + 150 n (the entry $11BD2 is
   * rewritten for it); stock exchanged. Harbingers: +$C = [$CB80] (then at most the
   * stock left); ACCB: +$10 = the building type [$DC6]. */
  _walrus_drop_loadout(px: number, py: number): void {
    const m = this.mem;
    const k = this._slot_at(WALRUS_SLOTS, px, py);
    if (k === null) return;
    const a0 = this._walrus_rec();
    const d1 = this._code(WALRUS_CODES, m.u16(LOADOUT_CURSOR + 2), 5);
    const a1 = this._entry(d1);
    let off: number;
    if (k) {
      if (m.u16(a1 + 8) === 1) return this._message("Payload cannot be mounted here");
      off = 0x28;
    } else {
      if (m.u16(a1 + 8) !== 1) return this._message("Payload cannot be mounted here");
      off = 0x26;
    }
    const d3 = m.u16(a0 + off);
    let a2: number;
    if (d3 === 7) {
      a2 = HARBINGER_LOAD;
      const n = m.u16(a0 + 0x0c);
      m.set16(a2 + 6, n);
      m.set16(a2, w16(n * 0x96 + 0x64));
    } else {
      a2 = this._entry(d3);
    }
    const d0 = w16(m.u16(a0 + 0x12) - m.u16(a2) + m.u16(a1));
    if (s16(d0) > 0x7d0) return this._message("Payload capacity exceeded");
    m.set16(a0 + 0x12, d0);
    if (d3) {
      if (d3 === 7) m.set16(a0 + 0x0c, 0);
      const p = m.u32(a2 + 2);
      m.set16(p, m.u16(p) + m.u16(a2 + 6));
    }
    m.set16(a0 + off, d1);
    const p = m.u32(a1 + 2);
    m.set16(p, m.u16(p) - m.u16(a1 + 6));
    if (d1 === 7) {
      const h = m.u16(HARBINGERS);
      m.set16(a0 + 0x0c, h);
      if (h > m.u16(p)) m.set16(HARBINGERS, m.u16(p));
    }
    if (d1 === 9) m.set16(a0 + 0x10, m.u16(0xdc6));
    this._walrus_panel();
  }

  /** $11C4E: dropped in the stores box: the slot emptied, the item back to stock (a
   * Harbinger load: its count +$C to $B98, weight 100 + 150 n; +$C itself is kept). */
  _walrus_drop_return(px: number, py: number): void {
    const m = this.mem;
    if (!this._in_stores_box(px, py)) return;
    const a0 = this._walrus_rec();
    const off = m.u16(0x11c12 + 2 * this._dragged_slot());
    const d0 = m.u16(a0 + off);
    m.set16(a0 + off, 0);
    if (d0 === 7) {
      const n = m.u16(a0 + 0x0c);
      m.set16(0xb98, m.u16(0xb98) + n);
      m.set16(a0 + 0x12, m.u16(a0 + 0x12) - w16(n * 0x96 + 0x64));
    } else {
      const a1 = this._entry(d0);
      const p = m.u32(a1 + 2);
      m.set16(p, m.u16(p) + m.u16(a1 + 6));
      m.set16(a0 + 0x12, m.u16(a0 + 0x12) - m.u16(a1));
    }
    this._message("Payload returned to stores");
    this._walrus_panel();
  }

  /** Load `code` (6 Avatar or 7 Harbingers into slot 1; 8 virus bomb, 9 ACCB, 11 fuel
   * pod into slot 0) into the arming screen's Walrus as a player does it (cursor clicks,
   * pick up, drop). Harbingers: stepping the cursor onto them resets the count $CB80 to
   * 1, so move the cursor there first, set the count (cmd_walrus_loadout_plus/minus),
   * then fit (the cursor isn't moved when it shows the code already). */
  cmd_walrus_fit(slot: number, code: number): boolean {
    return this._fit("Walrus", slot, code);
  }

  /** Drag slot 0/1's item back to the stores box. */
  cmd_walrus_unfit(slot: number): boolean {
    return this._unfit("Walrus", slot);
  }

  // ---- read helpers for the UI ---------------------------------------------------------------
  /** What the vehicle screens show that isn't obvious from the records: the selections
   * (control/map Manta $48C and Walrus $566; arming $DB4/$DB6; deck $DCC; dock $DE4),
   * whether the selected unit can be controlled ($70C/$70E), the loadout list cursors and
   * the codes they show, the Harbingers to load ($CB80), a drag in progress, and each
   * bay's telemetry and status. */
  vehicle_ui(): Record<string, number | boolean | number[]> {
    const m = this.mem;
    const mk = m.u16(LOADOUT_CURSOR);
    const wk = m.u16(LOADOUT_CURSOR + 2);
    return {
      manta: m.u16(0x48c), walrus: m.u16(0x566),
      manta_arming: m.u16(0xdb4), walrus_arming: m.u16(0xdb6),
      manta_deck: m.u16(0xdcc), walrus_dock: m.u16(0xde4),
      manta_controllable: m.u16(0x70c) !== 0, walrus_controllable: m.u16(0x70e) !== 0,
      manta_loadout: mk < 6 ? m.u16(MANTA_CODES + 2 * mk) : -1,
      walrus_loadout: wk < 5 ? m.u16(WALRUS_CODES + 2 * wk) : -1,
      harbingers: m.u16(HARBINGERS),
      dragging: m.u8(MODAL) ? m.u32(DROP) : 0,
      lock_timer: m.u16(0x116c),
      // telemetry per bay (0 OK, 1 weak, 2 lost) and status (+1: 0 ready ... 8 out, $FF destroyed)
      manta_telemetry: [0, 1, 2, 3].map((k) => m.u16(MANTA_RECS + REC * k + 0x2e)),
      walrus_telemetry: [0, 1, 2, 3].map((k) => m.u16(WALRUS_RECS + REC * k + 0x2c)),
      manta_status: [0, 1, 2, 3].map((k) => m.u8(MANTA_RECS + REC * k + 1)),
      walrus_status: [0, 1, 2, 3].map((k) => m.u8(WALRUS_RECS + REC * k + 1)),
    };
  }

  // ---- milestone 3 "War": bridge view zoom (screen 0, panel $42672) -----------------------
  abstract cmd_launch_flare(): void;
  abstract cmd_launch_recon_drone(): void;
  abstract cmd_fire_ssm(): void;
  abstract cmd_launch_defence_drone(): void;
  abstract cmd_recall_defence_drones(): void;
  abstract _drone_station_clash(rec: number, x: number, z: number): boolean;
  abstract cmd_supply_transfer(): boolean;

  /** $FCE8 / $FCF8: the bridge view's zoom $8B4 + d (0-4), scale $8B2 = $2E8 asr zoom
   * (read by the renderer only). */
  bridge_zoom(d: number): void {
    const m = this.mem;
    let z = s16(w16(m.u16(0x8b4) + d));
    z = z > 4 ? 4 : z < 0 ? 0 : z;
    m.set16(0x8b4, z);
    m.set16(0x8b2, 0x2e8 >> z);
  }

  /** $FCE8 (56,155; Insert): zoom + 1. */
  cmd_bridge_zoom_in(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.bridge_zoom(1);
    return true;
  }

  /** $FCF8 (56,178; Clr-Home): zoom - 1. */
  cmd_bridge_zoom_out(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.bridge_zoom(-1);
    return true;
  }

  // ---- carrier map, islands panel ([$710] = 1): the stockpile island -------------------------
  /** The name of island i ($4AD20 + its word offset). */
  island_name(i: number): string {
    const m = this.mem;
    const a = u32(ISLAND_NAMES + s16(m.u16(ISLAND_NAMES + s16(w16(2 * i)))));
    let out = "";
    for (let k = 0; m.u8(a + k); k++) out += String.fromCharCode(m.u8(a + k));
    return out;
  }

  /** $137C2 (screen 2, islands panel, 13,178), fresh press: the island the map is
   * centred on ([$764]) becomes the stockpile island ($D4E = the old one, $D4A), and
   * the goods queued or held at the old one ship there ($21D46). Refused while routes
   * are being recomputed ($1235), without communications, with stocks in transit
   * ($D50, unless there was no stockpile), off an island, on the stockpile already, on a
   * non-player island, or one with no route from Vulcan. */
  cmd_set_stockpile(): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON, [CARRIER_MAP_MODE, 1])) return false;
    const i = m.s16(MAP_ISLAND);
    if (m.u8(ROUTES_PENDING)) this._message("Re-routing");
    else if (!m.u8(0xb0a)) this._message("Communications Inoperative");
    else if (!(m.u16(STOCKPILE) & 0x8000) && m.u8(IN_TRANSIT)) this._message("Stocks still in transit");
    else if (i < 0) this._message("Not centred on an island");
    else if (i === m.s16(STOCKPILE)) this._message("Already the stockpile island");
    else if (m.u8(OWNERS + i) !== 1) this._message("Not a friendly island");
    else if (m.u16(ROUTES + 2 * i) & 0x8000) this._message("Island is isolated from Base");
    else {
      m.set16(CARRIER_ISLAND, m.u16(STOCKPILE));
      m.set16(STOCKPILE, i);
      this._message(`${this.island_name(i)} is now stockpile island`);
      this.stockpile_reship();
    }
    return true;
  }

  /** $21D46: the queued goods go back to the stockpile counts, then everything held
   * there ships from the old stockpile [$D4E] to the new one: carrier-pool entries
   * ($66A10, written from the first one on, unchecked) of up to 255 per item (kinds
   * 0-14) and fuel / 4096 (kinds 15-17; low word only); the counts are cleared and
   * "in transit" $D50 is set. */
  stockpile_reship(): void {
    const m = this.mem;
    for (let k = 0; k < N_ITEMS; k++) {
      const a = STORES + 2 * k;
      const q = STORES_PENDING + 2 * k;
      m.set16(a, m.u16(a) + m.u16(q));
      m.set16(q, 0);
    }
    for (let k = 0; k < 3; k++) {
      const a = FUEL + 4 * k;
      const q = FUEL_PENDING + 4 * k;
      m.set32(a, m.u32(a) + m.u32(q));
      m.set32(q, 0);
    }
    let e = CARRIER_POOL;
    const ship = (kind: number, d0: number): void => {
      for (;;) {
        const d1 = d0 >= 0xff ? 0xff : d0;
        d0 = w16(d0 - d1);
        m.set16(e + 4, 0xffff);
        m.set8(e + 2, kind);
        m.set8(e + 3, d1);
        m.set8(e, m.u16(CARRIER_ISLAND));
        m.set8(e + 1, 1);
        e += 6;
        if (!d0) return;
      }
    };
    for (let k = 0; k < N_ITEMS; k++) {
      const a = STORES + 2 * k;
      if (m.u16(a)) ship(k, m.u16(a));
      m.set16(a, 0);
    }
    for (let k = 0; k < 3; k++) {
      const a = FUEL + 4 * k;
      const f = Math.floor(m.u32(a) / 0x1000);
      if (f) ship(0xf + k, f & 0xffff);
      m.set32(a, 0);
    }
    m.set8(IN_TRANSIT, 0xff);
  }

  // ---- laser turret, screen 8 (input $1741A; panel $421C0) ---------------------------------
  /** $FD2C-$FD44: heading $6CC or elevation $6CA moves by 300 >> zoom [$6D2] ($FD54),
   * and both aim rates ($6CE, $6D0) stop. */
  _turret_aim(a: number, sign: number): void {
    const m = this.mem;
    const n = m.u16(0x6d2) & 63;
    const step = n >= 16 ? 0 : 0x12c >> n;
    m.set16(a, m.u16(a) + sign * step);
    m.set16(0x6ce, 0);
    m.set16(0x6d0, 0);
  }

  /** $FD3C: elevation $6CA - step (cursor up). */
  turret_aim_up(): void {
    this._turret_aim(0x6ca, -1);
  }

  /** $FD44: elevation + step. */
  turret_aim_down(): void {
    this._turret_aim(0x6ca, 1);
  }

  /** $FD2C: heading $6CC - step. */
  turret_aim_left(): void {
    this._turret_aim(0x6cc, -1);
  }

  /** $FD34: heading + step. */
  turret_aim_right(): void {
    this._turret_aim(0x6cc, 1);
  }

  /** $FD3C (100,155), works while held. */
  cmd_turret_up(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.turret_aim_up();
    return true;
  }

  /** $FD44 (122,178), works while held. */
  cmd_turret_down(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.turret_aim_down();
    return true;
  }

  /** $FD2C (100,178), works while held. */
  cmd_turret_left(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.turret_aim_left();
    return true;
  }

  /** $FD34 (122,155), works while held. */
  cmd_turret_right(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.turret_aim_right();
    return true;
  }

  /** $FD60 (117,170, between the arrows; works while held): the fire key $88C; the
   * turret (carrier_laser_turret_step) fires on its next step unless it is cooling down. */
  cmd_turret_fire(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.mem.set8(0x88c, 0xff);
    return true;
  }

  /** $FC9C / $FCAC: the turret zoom $6D2 + d (0-3; a higher zoom aims in finer steps)
   * and the view offsets $A94/$A98 ($FCD4). */
  turret_zoom(d: number): void {
    const m = this.mem;
    let z = s16(w16(m.u16(0x6d2) + d));
    z = z > 3 ? 3 : z < 0 ? 0 : z;
    m.set16(0x6d2, z);
    this.turret_zoom_view(z);
  }

  /** $FC9C (144,155; Insert), fresh press. */
  cmd_turret_zoom_in(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.turret_zoom(1);
    return true;
  }

  /** $FCAC (144,178; Clr-Home), fresh press. */
  cmd_turret_zoom_out(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.turret_zoom(-1);
    return true;
  }

  /** $12A34 (166,155): $6D5 bit 7, the heading rate $6CE self-centres to 0. */
  cmd_turret_centre_heading(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.mem.set8(0x6d5, this.mem.u8(0x6d5) | 0x80);
    return true;
  }

  /** $12A3C (166,178): $6D5 bit 6, the elevation rate $6D0 self-centres. */
  cmd_turret_centre_elevation(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.mem.set8(0x6d5, this.mem.u8(0x6d5) | 0x40);
    return true;
  }

  /** Mouse-steer deltas that make the next turret step set the aim rates $6CE (heading)
   * and $6D0 (elevation) to these values (clamped to +-255): only on screen 8 in
   * mouse-steer mode ($457 clear) with the observation turret working; a left click
   * (cmd_vehicle_fire) fires. */
  cmd_turret_steer_to(heading_rate: number, elevation_rate: number): void {
    const m = this.mem;
    m.set16(MOUSE_DX, w16(Math.max(-255, Math.min(255, heading_rate)) - m.s16(0x6ce)));
    m.set16(MOUSE_DX + 2, w16(Math.max(-255, Math.min(255, elevation_rate)) - m.s16(0x6d0)));
  }

  /** $1741A (screen 8): the gauge timer $968 (redraw); held cursor keys aim, Insert /
   * Clr-Home zoom, a right click toggles mouse-steer mode $457. */
  turret_input(): void {
    const m = this.mem;
    if (m.u16(0x968)) m.set16(0x968, m.u16(0x968) - 1);
    const keys: [number, () => void][] = [
      [0x828, () => this.turret_aim_up()], [0x838, () => this.turret_aim_down()],
      [0x82e, () => this.turret_aim_left()], [0x832, () => this.turret_aim_right()],
    ];
    for (const [key, f] of keys) if (m.u8(key)) f();
    if (this._bclr0(0x83d)) this.turret_zoom(1);
    if (this._bclr0(0x827)) this.turret_zoom(-1);
    if (this._bclr0(0x883)) m.set8(POINTER_MODE, ~m.u8(POINTER_MODE));
  }

  // ---- flares 9, viewing drone 10 (input $1778E), defence drones 11 -----------------------
  /** $1338C (screen 9, 166,155), fresh press: cmd_launch_flare. */
  cmd_flare_launch(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.cmd_launch_flare();
    return true;
  }

  /** $134C2 (screen 10, 210,155), fresh press: cmd_launch_recon_drone. */
  cmd_recon_launch(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.cmd_launch_recon_drone();
    return true;
  }

  /** $13598 (screen 10, the drone picture at 32,6, 256 x 128), fresh press: the pointer
   * moves to (px, py) and, with the SSM armed ($1160, a right click), cmd_fire_ssm aims a
   * cruise missile at the point under it. While armed the original clips the pointer to
   * the picture. */
  cmd_ssm_fire_at(px: number, py: number): boolean {
    const [x0, y0, x1, y1] = SSM_VIEW;
    if (!(x0 <= px && px <= x1 && y0 <= py && py <= y1 && this._clickable(T_BUTTON))) return false;
    this._set_pointer(px, py);
    this.cmd_fire_ssm();
    return true;
  }

  /** $F712: modal mode $460 off (the pointer sprite and its clip are UI). */
  pointer_free(): void {
    this.mem.set8(MODAL, 0);
  }

  /** $1778E (screen 10): without the viewing drone (object 15) out the SSM is disarmed;
   * otherwise a right click toggles SSM armed $1160 (arming points the pointer at the
   * picture, disarming frees it, $F712). */
  drone_view_input(): void {
    const m = this.mem;
    if (!(m.u8(this.obj(15) + 1) & 1)) {
      if (m.u8(0x1160)) {
        m.set8(0x1160, 0);
        this.pointer_free();
      }
      return;
    }
    if (!this._bclr0(0x883)) return;
    if (m.u8(0x1160)) {
      m.set8(0x1160, 0);
      this.pointer_free();
      return;
    }
    m.set8(0x1160, 0xff);
  }

  /** $10738 (screen 11, 166,155; repeats while held): cmd_launch_defence_drone. */
  cmd_drones_launch(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.cmd_launch_defence_drone();
    return true;
  }

  /** $1088C (screen 11, 166,178), fresh press: cmd_recall_defence_drones. */
  cmd_drones_recall(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this.cmd_recall_defence_drones();
    return true;
  }

  /** $10552: during a drone recall ($122F) the stations can't change. */
  _no_override(): boolean {
    if (this.mem.u8(0x122f)) {
      this._message("Cannot override docking sequence");
      return true;
    }
    return false;
  }

  /** $10614: each station's state (a0) &= 1 and its offsets +$A/+$C from src. */
  _set_stations(src: number): void {
    const m = this.mem;
    for (let k = 0; k < 4; k++) {
      const a = 0x65e + 14 * k;
      m.set16(a, m.u16(a) & 1);
      m.set16(a + 0xa, m.u16(src + 4 * k));
      m.set16(a + 0xc, m.u16(src + 4 * k + 2));
    }
  }

  /** $10602 (12,155), fresh press: the stations as they were when the screen was
   * entered ($141DE); not during a recall ("Cannot cancel docking sequence"). */
  cmd_drone_stations_reset(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    if (this.mem.u8(0x122f)) this._message("Cannot cancel docking sequence");
    else this._set_stations(STATION_SNAPSHOT);
    return true;
  }

  _formation(d: number): void {
    const m = this.mem;
    let k = s16(w16(m.u16(0x698) + d));
    if (d > 0 && k === 9) k = 0;
    else if (d < 0 && k < 0) k = 8;
    m.set16(0x698, k);
    m.set16(0x95a, 2);
  }

  /** $106EC (298,155; repeats): formation $698 + 1 (wraps 9 to 0). */
  cmd_drone_formation_next(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this._formation(1);
    return true;
  }

  /** $10706 (298,178; repeats): formation - 1 (below 0: 8). */
  cmd_drone_formation_prev(): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    this._formation(-1);
    return true;
  }

  /** $10714 (188,155), fresh press: the stations of formation [$698] (table $1065C). */
  cmd_drone_formation_apply(): boolean {
    const m = this.mem;
    if (!this._clickable(T_BUTTON)) return false;
    if (this._no_override()) return true;
    const k = m.u16(0x698);
    if (k > 8) throw new NotPorted(`drone formation ${k} beyond the table $1065C`);
    this._set_stations(FORMATIONS + 16 * k);
    return true;
  }

  /** $104C8 (the radar marker of station k, types 66-69): start dragging it ($696 = k,
   * drop $104E6); not during a recall. */
  cmd_drone_station_pick(k: number): boolean {
    if (!(0 <= k && k <= 3 && this._clickable(T_STATION + k))) return false;
    if (this._no_override()) return true;
    this.mem.set16(0x696, k);
    this._start_drag(0x104e6);
    return true;
  }

  /** $104E6: the station [$696] to the radar point: +$A = (py - cy + 1) * 60, +$C =
   * (px - cx - 4) * 60 (centre $4750/$4752); refused inside the red box (+-900 x
   * +-1500) or within about 384 of another active station; its state &= 1. */
  _drone_station_drop(px: number, py: number): void {
    const m = this.mem;
    const a0 = u32(0x65e + s16(w16(m.u16(0x696) * 14)));
    const d0 = s32(s16(w16(px - m.u16(0x4750) - 4)) * 60);
    const d1 = s32(s16(w16(py - m.u16(0x4752) + 1)) * 60);
    const lo0 = s16(w16(d0));
    const lo1 = s16(w16(d1));
    if (-900 <= lo1 && lo1 <= 900 && -1500 <= lo0 && lo0 <= 1500) {
      this._message("Drone must be outside red boundary");
      return;
    }
    if (this._drone_station_clash(a0, w16(d1), w16(d0))) {
      this._message("Cannot put drones on top of each other");
      return;
    }
    m.set16(a0 + 0xa, d1);
    m.set16(a0 + 0xc, d0);
    m.set16(a0, m.u16(a0) & 1);
  }

  /** The radar point of station k's offsets (the drop's inverse, for the UI). */
  drone_station_pixel(k: number): [number, number] {
    const m = this.mem;
    const a = 0x65e + 14 * k;
    return [m.s16(0x4750) + 4 + pyDiv(m.s16(a + 0xc) + 30, 60), m.s16(0x4752) - 1 + pyDiv(m.s16(a + 0xa) + 30, 60)];
  }

  /** Drag station k's marker to the radar point (px, py): pick it up, release there,
   * ui_frame drops it. False if the pick-up wasn't possible. */
  cmd_drone_station_move(k: number, px: number, py: number): boolean {
    if (!this.cmd_drone_station_pick(k) || !this.mem.u8(MODAL)) return false;
    this.cmd_drag_release(px, py);
    this.ui_frame();
    return true;
  }

  // ---- resources, screen 3 (input $177DE; panels $42CC8-$4314A by sub-panel [$D14]) ------
  /** The carrier's island buffer ([$5E996], carrier +$3C) is the stockpile island's. */
  _at_stockpile(): boolean {
    const m = this.mem;
    const buf = m.u32(CARRIER + 0x3c);
    return buf !== 0 && m.u8(buf + 1) === m.u16(STOCKPILE);
  }

  /** $17812: the message, back to the main panel, the tab re-entered ($1227/$1228). */
  _resources_refused(text: string): void {
    const m = this.mem;
    this._message(text);
    m.set16(RESOURCES, 0);
    m.set16(REQUEST_TAB, m.u16(TAB));
    m.set8(SCREEN_REQUEST, 0xff);
  }

  /** $177DE (screen 3): the priority panels (2, 3) need communications ($B0A), the
   * queue panels (4, 5) also no transfer running ($D3E) and the carrier at the stockpile
   * island; otherwise back to the main panel. */
  resources_input(): void {
    const m = this.mem;
    const mode = m.u16(RESOURCES);
    if (mode < 2) return;
    if (!m.u8(0xb0a)) return this._resources_refused("Communications are out");
    if (mode < 4) return;
    if (m.u8(0xd3e)) return this._resources_refused("Resupply operation in progress");
    if (!this._at_stockpile()) return this._resources_refused("Stockpile island out of range");
  }

  _res(mode: number): boolean {
    return this._clickable(T_BUTTON, [RESOURCES, mode]);
  }

  /** Class and target of the 15 store items (+$10/+$12 committed, +$14/+$16 edited). */
  _items_copy(src: number, dst: number): void {
    const m = this.mem;
    for (let k = 0; k < N_ITEMS; k++) {
      const a = ITEMS + ITEM_SIZE * k;
      m.set16(a + dst, m.u16(a + src));
      m.set16(a + dst + 2, m.u16(a + src + 2));
    }
  }

  /** $1102E (12,155), fresh press: recall the supply drone ($D42 = $D46 = 2, $D40 = 0)
   * unless no transfer runs, it has completed or been aborted, or it is docking
   * ($D40 with $DE6 = 4). */
  cmd_resupply_abort(): boolean {
    const m = this.mem;
    if (!this._res(0)) return false;
    if (!m.u8(0xd3e)) this._message("No resupply operation is in progress");
    else if (m.u16(0xd46) === 1) this._message("Resupply already completed");
    else if (m.u16(0xd46) === 2) this._message("Resupply operation has been aborted");
    else if (m.u8(0xd40) && m.u16(0xde6) === 4) this._message("Too late to abort resupply");
    else {
      m.set16(0xd42, 2);
      m.set16(0xd46, 2);
      m.set8(0xd40, 0);
      this._message("Resupply aborted - drone recalled");
    }
    return true;
  }

  /** $10E4A (34,155), fresh press: cmd_supply_transfer (stunits_enemy). */
  cmd_resources_transfer(): boolean {
    if (!this._res(0)) return false;
    this.cmd_supply_transfer();
    return true;
  }

  /** $1098A (the 15 item pictures, 44..244 x 8..92): sub-panel 1 showing item k ($D16). */
  cmd_item_picture(k: number): boolean {
    if (!(0 <= k && k < N_ITEMS && this._res(0))) return false;
    this.mem.set16(ITEM_PANEL, k);
    this.mem.set16(RESOURCES, 1);
    return true;
  }

  /** $109BC (142,115): back to the main panel. */
  cmd_item_picture_back(): boolean {
    if (!this._res(1)) return false;
    this.mem.set16(RESOURCES, 0);
    return true;
  }

  /** $109E8 (56,155): needs communications; sub-panel 2, scroll $D38 = 0, no entry,
   * the items' classes and targets copied to their edit fields (+$14/+$16). */
  cmd_stores_priorities_open(): boolean {
    const m = this.mem;
    if (!this._res(0)) return false;
    if (!m.u8(0xb0a)) {
      this._message("Communications are out");
      return true;
    }
    m.set16(RESOURCES, 2);
    m.set16(STORES_SCROLL, 0);
    m.set8(ENTRY, 0);
    this._items_copy(0x10, 0x14);
    return true;
  }

  /** $10A66 (228,22): the edit fields back to the committed values (not while a
   * number is being typed). */
  cmd_stores_priorities_reset(): boolean {
    if (!this._res(2)) return false;
    if (!this.mem.u8(ENTRY)) this._items_copy(0x10, 0x14);
    return true;
  }

  /** $10A3C (228,114): commit the edit fields (+$14/+$16 -> +$10/+$12), main panel. */
  cmd_stores_priorities_exit(): boolean {
    if (!this._res(2)) return false;
    if (!this.mem.u8(ENTRY)) {
      this.mem.set16(RESOURCES, 0);
      this._items_copy(0x14, 0x10);
    }
    return true;
  }

  /** $10AA6 (176,44): the three rows show items $D38.. ; $D38 - 1 (>= 0). */
  cmd_stores_scroll_up(): boolean {
    const m = this.mem;
    if (!this._res(2)) return false;
    if (!m.u8(ENTRY) && m.u16(STORES_SCROLL)) m.set16(STORES_SCROLL, m.u16(STORES_SCROLL) - 1);
    return true;
  }

  /** $10A8A (176,76): $D38 + 1 (up to 12). */
  cmd_stores_scroll_down(): boolean {
    const m = this.mem;
    if (!this._res(2)) return false;
    if (!m.u8(ENTRY) && m.u16(STORES_SCROLL) !== 12) m.set16(STORES_SCROLL, m.u16(STORES_SCROLL) + 1);
    return true;
  }

  /** The item record of a row: $B76 + (row + scroll) * $1A (mulu.w, index .w). */
  _item(row: number, scroll: number): number {
    const m = this.mem;
    return u32(ITEMS + s16(w16(w16(row + m.u16(scroll)) * ITEM_SIZE)));
  }

  /** $10ABC (80, 24/60/96), fresh press: the class field of row 0-2 (item $D38 + row):
   * edit class +$14 = cls (0-2, by where the pointer is in the field, $10AE6). */
  cmd_stores_class(row: number, cls: number): boolean {
    if (!(0 <= row && row <= 2 && 0 <= cls && cls <= 2 && this._res(2))) return false;
    if (!this.mem.u8(ENTRY)) this.mem.set16(this._item(row, STORES_SCROLL) + 0x14, cls);
    return true;
  }

  /** $11142: start typing a number on the keypad (layout $D22-$D30 for the panel
   * drawing, the old value $D32, the callback $D34, no digits yet: "0"). */
  _entry_start(x: number, y: number, old: number, digits: number, d4: number, d5: number, callback: number): void {
    const m = this.mem;
    m.set8(ENTRY, 0xff);
    m.set32(ENTRY_CALLBACK, callback);
    m.set16(0xd22, x);
    m.set16(0xd24, y);
    m.set16(ENTRY_OLD, old);
    m.set16(ENTRY_MAX, digits);
    m.set16(0xd2e, d4);
    m.set16(0xd30, d5);
    m.set16(0xd26, x + 6 * digits - 1);
    m.set16(0xd28, y + 5);
    m.set16(ENTRY_COUNT, 0);
    m.set8(ENTRY_DIGITS, 0x30);
    m.set8(ENTRY_DIGITS + 1, 0);
  }

  /** $10B02 (120, 31/67/103), fresh press: type a new target for row 0-2's item on the
   * keypad (cmd_keypad), $D18 = row. */
  cmd_stores_target(row: number): boolean {
    const m = this.mem;
    if (!(0 <= row && row <= 2 && this._res(2))) return false;
    if (m.u8(ENTRY)) return true;
    m.set16(ITEM_EDITED, row);
    const [x, y] = TARGET_ENTRY[row];
    this._entry_start(x + 0xe, y + 0xa, m.u16(this._item(row, STORES_SCROLL) + 0x16), 3, 10, 8, ENTRY_DONE);
    return true;
  }

  /** jmp [$D34]: only $10B5A: the edited row's item target +$16 = v. */
  _entry_done(v: number): void {
    const m = this.mem;
    const cb = m.u32(ENTRY_CALLBACK);
    if (cb !== ENTRY_DONE) {
      throw new NotPorted(`keypad entry callback $${cb.toString(16).toUpperCase()} ($11142 has only the caller $10B02)`);
    }
    m.set16(this._item(m.u16(ITEM_EDITED), STORES_SCROLL) + 0x16, v);
  }

  /** $11196 (the keypad at 216,44 in the stores priorities), fresh press, while a
   * number is being typed ($D1A): key 0-9 a digit (at most [$D2C], no leading 0), 10
   * ENTER (the number to the callback), 11 CANCEL (the old value), 12 DELETE. */
  cmd_keypad(key: number): boolean {
    const m = this.mem;
    if (!(0 <= key && key <= 12 && this._res(2))) return false;
    if (!m.u8(ENTRY)) return true;
    if (key === 10) {
      m.set8(ENTRY, 0);
      let v = 0;
      let d1 = 0;
      const n = m.u16(ENTRY_COUNT);
      for (let i = 0; i < n; i++) {
        // move.b into d1, subi.w #'0': its high byte stays
        d1 = w16((d1 & 0xff00) + m.u8(ENTRY_DIGITS + i) - 0x30);
        v = w16(v * 10 + d1);
      }
      this._entry_done(v);
    } else if (key === 11) {
      m.set8(ENTRY, 0);
      this._entry_done(m.u16(ENTRY_OLD));
    } else if (key === 12) {
      let n = m.u16(ENTRY_COUNT);
      if (n) {
        n -= 1;
        m.set16(ENTRY_COUNT, n);
        if (n) m.set8(ENTRY_DIGITS + s16(n), 0);
        else m.set8(ENTRY_DIGITS, 0x30);
      }
    } else {
      const n = m.u16(ENTRY_COUNT);
      if ((n || key) && n !== m.u16(ENTRY_MAX)) {
        const a = u32(ENTRY_DIGITS + s16(n));
        m.set8(a, key + 0x30);
        m.set8(a + 1, 0);
        m.set16(ENTRY_COUNT, n + 1);
      }
    }
    return true;
  }

  /** $10B72 (122,155): needs communications; sub-panel 3, the fuel classes $D08-$D0C
   * copied to the edit fields $D0E-$D12. */
  cmd_fuel_priorities_open(): boolean {
    const m = this.mem;
    if (!this._res(0)) return false;
    if (!m.u8(0xb0a)) {
      this._message("Communications are out");
      return true;
    }
    m.set16(RESOURCES, 3);
    m.buf.copyWithin(FUEL_WORK, FUEL_CLASS, FUEL_CLASS + 6);
    return true;
  }

  /** $10BC6 (228,22): the edit fields back to $D08-$D0C. */
  cmd_fuel_priorities_reset(): boolean {
    if (!this._res(3)) return false;
    this.mem.buf.copyWithin(FUEL_WORK, FUEL_CLASS, FUEL_CLASS + 6);
    return true;
  }

  /** $10BAC (228,114): commit $D0E-$D12 to the fuel classes, main panel. */
  cmd_fuel_priorities_exit(): boolean {
    const m = this.mem;
    if (!this._res(3)) return false;
    m.set16(RESOURCES, 0);
    m.buf.copyWithin(FUEL_CLASS, FUEL_WORK, FUEL_WORK + 6);
    return true;
  }

  /** $10BDA (136, 24/60/96), fresh press: fuel k's (0-2) edit class $D0E + 2k = cls. */
  cmd_fuel_class(k: number, cls: number): boolean {
    if (!(0 <= k && k <= 2 && 0 <= cls && cls <= 2 && this._res(3))) return false;
    this.mem.set16(FUEL_WORK + 2 * k, cls);
    return true;
  }

  /** $10C06 / $10D52: needs communications, no transfer running and the carrier at
   * the stockpile island. */
  _queue_open(mode: number): boolean {
    const m = this.mem;
    if (!this._res(0)) return false;
    if (!m.u8(0xb0a)) this._message("Communications are out");
    else if (m.u8(0xd3e)) this._message("Resupply operation in progress");
    else if (!this._at_stockpile()) this._message("Stockpile island out of range");
    else m.set16(RESOURCES, mode);
    return true;
  }

  /** $10C06 (188,155): sub-panel 4, the stores to ship from the stockpile. */
  cmd_stores_queue_open(): boolean {
    return this._queue_open(4);
  }

  /** $10D52 (254,155): sub-panel 5, the fuel to ship. */
  cmd_fuel_queue_open(): boolean {
    return this._queue_open(5);
  }

  /** $10C62 (244,116): main panel. */
  cmd_stores_queue_back(): boolean {
    if (!this._res(4)) return false;
    this.mem.set16(RESOURCES, 0);
    return true;
  }

  /** $10D9C (140,116): main panel. */
  cmd_fuel_queue_back(): boolean {
    if (!this._res(5)) return false;
    this.mem.set16(RESOURCES, 0);
    return true;
  }

  /** $10C7C (251,47): the rows show items $D3A..; $D3A - 1 (>= 0). */
  cmd_stores_queue_scroll_up(): boolean {
    const m = this.mem;
    if (!this._res(4)) return false;
    if (m.u16(QUEUE_SCROLL)) m.set16(QUEUE_SCROLL, m.u16(QUEUE_SCROLL) - 1);
    return true;
  }

  /** $10C6A (251,79): $D3A + 1 (up to 12). */
  cmd_stores_queue_scroll_down(): boolean {
    const m = this.mem;
    if (!this._res(4)) return false;
    if (m.u16(QUEUE_SCROLL) !== 12) m.set16(QUEUE_SCROLL, m.u16(QUEUE_SCROLL) + 1);
    return true;
  }

  /** 1 on a press ($D3C = 5); held: 1 for 5 more passes, then 10 a pass. */
  _queue_step(held: number): number {
    const m = this.mem;
    if (!held) {
      m.set16(QUEUE_REPEAT, 5);
      return 1;
    }
    if (m.u16(QUEUE_REPEAT)) {
      m.set16(QUEUE_REPEAT, m.u16(QUEUE_REPEAT) - 1);
      return 1;
    }
    return 10;
  }

  /** $10C88 (134, 35/69/103; repeats): move row 0-2's item (item $D3A + row) from the
   * queue $D96 back to the stockpile $D78, 1 at a time, 10 once held a while. */
  cmd_stores_unqueue(row: number, held = 0): boolean {
    const m = this.mem;
    if (!(0 <= row && row <= 2 && this._res(4))) return false;
    let d1 = this._queue_step(held);
    const i = s16(w16(2 * w16(row + m.u16(QUEUE_SCROLL))));
    const q = u32(STORES_PENDING + i);
    const s = u32(STORES + i);
    if (m.s16(q) < d1) d1 = m.s16(q);
    m.set16(q, m.u16(q) - d1);
    m.set16(s, m.u16(s) + d1);
    return true;
  }

  /** $10CCA (163, 35/69/103; repeats): move row 0-2's item from the stockpile to the
   * queue, up to the carrier's room (capacity +$18 - on board +8 - queued): "Limit of
   * ACC stores reached". */
  cmd_stores_queue(row: number, held = 0): boolean {
    const m = this.mem;
    if (!(0 <= row && row <= 2 && this._res(4))) return false;
    let d1 = this._queue_step(held);
    const idx = w16(row + m.u16(QUEUE_SCROLL));
    const i = s16(w16(2 * idx));
    const q = u32(STORES_PENDING + i);
    const s = u32(STORES + i);
    const a2 = u32(ITEMS + s16(w16(idx * ITEM_SIZE)));
    if (m.s16(s) < d1) d1 = m.s16(s);
    const room = s16(w16(m.u16(a2 + 0x18) - m.u16(a2 + 8) - m.u16(q)));
    if (room === 0) {
      this._message("Limit of ACC stores reached");
      return true;
    }
    if (d1 > room) d1 = room;
    m.set16(s, m.u16(s) - d1);
    m.set16(q, m.u16(q) + d1);
    return true;
  }

  /** $10DA4 / $10E18 / $10E30 (134, 36/61/86; repeats): fuel k's queue ($D6C + 4k)
   * back to the stockpile ($D60 + 4k), $4000 a pass. */
  cmd_fuel_unqueue(k: number): boolean {
    const m = this.mem;
    if (!(0 <= k && k <= 2 && this._res(5))) return false;
    const s = FUEL + 4 * k;
    const q = FUEL_PENDING + 4 * k;
    const d1 = Math.min(0x4000, s32(m.u32(q)));
    m.set32(q, m.u32(q) - d1);
    m.set32(s, m.u32(s) + d1);
    return true;
  }

  /** $10DC0 / $10E22 / $10E3C (163, 36/61/86; repeats): stockpile fuel k to the queue,
   * $4000 a pass, up to the room in the carrier's tank ($FFFE0 - queued - tank $CFC +
   * 4k): "Limit of ACC tanks reached". */
  cmd_fuel_queue(k: number): boolean {
    const m = this.mem;
    if (!(0 <= k && k <= 2 && this._res(5))) return false;
    const s = FUEL + 4 * k;
    const q = FUEL_PENDING + 4 * k;
    const tank = 0xcfc + 4 * k;
    let d1 = Math.min(0x4000, s32(m.u32(s)));
    const room = s32(0xfffe0 - m.u32(q) - m.u32(tank));
    if (room === 0) {
      this._message("Limit of ACC tanks reached");
      return true;
    }
    if (room < d1) d1 = room;
    m.set32(s, m.u32(s) - d1);
    m.set32(q, m.u32(q) + d1);
    return true;
  }

  // ---- messages 4, game status 32, title 33 ----------------------------------------------------
  /** $12C2C (screen 4, 144,155), fresh press. The message log ($1148, $65D10: the
   * messages $A128 records) is the app's, not the model's: `logged` says whether it has
   * entries. Then it is cleared ($A1E8, the app's), $966 = 2 and "Message log
   * cleared"; else "Message log is already clear". */
  cmd_clear_message_log(logged: number): boolean {
    if (!this._clickable(T_BUTTON)) return false;
    if (logged) {
      this.mem.set16(0x966, 2);
      this._message("Message log cleared");
    } else {
      this._message("Message log is already clear");
    }
    return true;
  }

  /** $13778 (the options panel, $708 = 7; rows at y 29 + 16k, OFF at x 217-242, ON at
   * 252-277): option k (0 sea dots, 1 message ping, 2 background noise, 3 sound effects)
   * $1246 + k = on ($FF) or off. Quirk: the element reaches a fifth row (y 93-106), k = 4,
   * which writes $124A, the strategy flag. */
  cmd_game_option(k: number, on: number): boolean {
    if (!(0 <= k && k <= 4 && this._clickable(T_BUTTON, [GAME_STATUS, 7]))) return false;
    this.mem.set8(OPTIONS + k, on ? 0xff : 0);
    return true;
  }

  /** $103B2 / $103A0 (title menu, $708 = 0): a strategy / action game ($124A), new_game
   * ($42A8); the next ui_frame enters the bridge. */
  cmd_title_new_game(strategy: number): boolean {
    if (!this._clickable(T_BUTTON, [GAME_STATUS, 0])) return false;
    this.new_game(Boolean(strategy));
    return true;
  }
}
