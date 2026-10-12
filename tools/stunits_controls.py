"""Unit layer, part of tools/stunits.py: the player's control of the carrier and the game flow around sim_pass ("Sail" milestone).

The original reads the mouse and keyboard in interrupts (IKBD $17D80: deltas $1268/$126A,
buttons $880-$883, keys $798 + 2*scancode, last character $1270) and runs panel click handlers
from ui_frame ($F636 -> hit test $B46E: d0/d1 = pointer x/y, d2 = [$45A] = 2 for a fresh press,
1 for auto-repeat after 6 passes held, a3 = element). A browser UI calls the methods here
instead; each writes exactly what its handler writes (redraws, palettes and sounds left out;
messages become ["message", text, ping] events). A command returns False when the original's hit test
would not have reached the handler (panel not shown, element hidden or disabled, or the game
paused: only PAUSE, flag bit 12, works then). The current screen is not checked: the UI decides
which controls it offers (the original's screen for each is listed).

Raw input, as the interrupts write it (screen_input then reads it on the next game_frame):
    input_mouse_move(dx, dy)          $1268/$126A += dx/dy
    input_mouse_button(right, down)   $880/$881 (left) or $882/$883 (right) held/new
    input_key(scancode, down)         $798 + 2*sc held/new, $1270 the character
Bridge, screen 0 (panels $42672, $4261C) and its input handler $17164:
    cmd_carrier_throttle(y)           $FE3C  slider at x 288, y 159..195: $646 = (187 - y) * 4
    cmd_carrier_speed_up/down()       $FDCE/$FDD4  chevrons: $88A/$88B (target = speed +-1)
    cmd_carrier_stop()                $12A26 $646 = 0
    cmd_carrier_centre_rudder()       $12A16 $640 bit 7: the rudder self-centres
    cmd_carrier_autopilot(held)       $12BAC autopilot on/off ($640 bit 0; needs a course)
    cmd_toggle_mouse_steer()          right click: screen 0 toggles $457 (not under autopilot)
    cmd_steer_to(rudder)              mouse-steer delta so the next carrier_step sets $642
Maps, screens 2/17/25 (input $17244, panels $41B26, $41F76, $41BE8, $41E74, $4203A):
    map_to_world(px, py)              $F91E pointer -> world x/z (rect $47F2, zoom $75A, centre $75C/$760)
    world_to_map(x, z)                its inverse (nearest pixel), for the UI
    cmd_map_click(px, py)             $F8D8  map cursor: carrier $6BE/$6C2, Walrus $6B0/$6B4, Manta $69E/$6A6
    cmd_map_recentre(px, py)          $F8B4  right click: centre on the pointer, zoom in
    cmd_map_zoom_in/out()             $F984/$F99A
    cmd_map_scroll_up/down/left/right() $FAB0/$FAC8/$FA84/$FA6C
    cmd_map_centre_unit()             $F9BE;  cmd_map_centre_carrier() $FA50
    cmd_map_island_info() / cmd_map_island_info_exit()   $FAF4 / $FB1A
    cmd_carrier_set_course(held)      $131A8 waypoint $64E/$652 <- cursor, cruise $656 <- $6BC
    cmd_carrier_clear_course()        $1326C
    cmd_carrier_map_mode()            $1336E carrier map sub-panel $710
    cmd_carrier_cruise_up/down()      $12E7A/$12E8C  $6BC +-8 (0..$70)
    cmd_carrier_cruise(y)             $12E9E slider: $6BC = (187 - y) * 4, 0..$70
    cmd_carrier_toggle_announce()     $12E44 $6C6: report arrival
    cmd_set_carrier_waypoint(x, z)    the cursor at world x/z, then set course (fresh press)
Damage screen 1 (input $1747A, panels $42064, $4216A):
    cmd_select_system(k)              $FB3A  system k (0-9) shown, $B58
    cmd_priorities_open()             $FBBE  copy the classes to the work table $B44, $B62 = 1
    cmd_priority_click(px, py)        $FC64  grid at (200, 16): row = system, column = class
    cmd_priorities_cancel()           $FC08
    cmd_priorities_exit()             $FC24  the work table back into the classes $AF4 + 8k + 4
    cmd_set_repair_priority(k, cls)   open, click, exit
Screens (ui_frame $F636, enter table $139D4, leave table $14318; docs/units.md §10.5):
    ui_frame()                        $F636 minus the hit test: the forced request $1227/$1228,
                                      else $17EEA and the pending screen $71A / tab $716
    cmd_select_tab(tab)               $F844  tab icon (0 game status, 1 carrier, 2 weapons,
                                      3 Walrus, 4 Manta): its remembered screen $720 + 2*tab
    cmd_select_screen(screen)         $F880  screen icon of the current tab
    cmd_switch_screen(screen)         the tab and screen clicks that reach a screen
    cmd_game_status_menu(mode)        $101F2/$10200/$101E4/$1020E  $708 = 6/3/1/7, re-enter 32
    cmd_game_status_back()            $1021C CANCEL/NO/OK/EXIT: $708 = 0, re-enter 32
    cmd_surrender()                   $101D8 YES: end request, result 0
    cmd_continue(yes)                 $13990/$1399C on screen 35
    new_game_screen()                 replaces stunits_setup's: the request $42A8 leaves
Game flow:
    cmd_pause()                       $FD66  PAUSE: toggles $462 (pause $FD84 / resume $FD98)
    game_frame()                      one game_main loop ($408A) minus ui_frame and rendering:
                                      $4194 keys, paused_frame $44EE or debug keys $46E8,
                                      screen_input $17094, sim_pass, the end timer $732 and the
                                      end request $463. Events ("game over", screen, [$466]).
    screen_input()                    $17094 for every screen (all input handlers are ported).
Mantas and Walruses ("Fly and drive"; docs/units.md §10.4, §10.6):
    Manta control 27 (input $172BC: lock timer $116C, number keys, re-entry, auto button):
    cmd_manta_select(bay) $FE54, cmd_manta_weapon(w) $FEE2, cmd_manta_autopilot() $12A44,
    cmd_manta_centre_rudder/pitch() $129E6/$129FE, cmd_manta_instruments() $100A8,
    cmd_manta_throttle(y) $FDDA, cmd_manta_sight() $10100, cmd_manta_rear_view() $1015C,
    cmd_manta_runway_take_off() $13356, cmd_manta_steer_to(rudder, pitch), cmd_vehicle_fire()
    Manta deck 26: cmd_manta_deck_select(bay) $11F92, cmd_manta_abort_landing() $12360
    Manta map 25: cmd_manta_map_select(bay) $FF1E (+ recall $12ECC), cmd_manta_set_course(held)
    $12FC4, cmd_manta_clear_course() $12F8E, cmd_manta_cruise_up/down() / cmd_manta_cruise(y)
    $12C90/$12CA2/$12CB4, cmd_manta_altitude_up/down() / cmd_manta_altitude(y)
    $12CE2/$12CF6/$12D0A, cmd_manta_map_draw_mode() $12D40, cmd_manta_toggle_announce() $12D50,
    cmd_set_manta_waypoint(x, z)
    Manta arming 24: cmd_manta_arming_select(bay) $116C6, cmd_manta_arming_to_deck() $11886,
    cmd_manta_loadout_next/prev() $1132E/$11352, cmd_manta_pick_loadout() $1135E,
    cmd_manta_pick_slot(k) $114C8, cmd_manta_fit(slot, code), cmd_manta_unfit(slot)
    Walrus control 18 (input $17374): cmd_walrus_select(bay) $10016, cmd_walrus_weapon(w) $FF7C,
    cmd_walrus_autopilot() $12B00, cmd_walrus_centre_rudder(held) $12982,
    cmd_walrus_turret_mode() $129A2, cmd_walrus_instruments() $100D4, cmd_walrus_throttle(y)
    $FE0A, cmd_walrus_sight() $1012E, cmd_walrus_rear_view() $10190, cmd_walrus_steer_to(r),
    cmd_walrus_missile_steer_to(yaw, pitch)
    Walrus dock 19: cmd_walrus_dock_select(bay) $1249E, cmd_walrus_abort_docking() $128AE
    Walrus map 17: cmd_walrus_map_select(bay) $FFB8 (+ recall $12F30), cmd_walrus_set_course(held)
    $13122, cmd_walrus_clear_course() $130EE, cmd_walrus_cruise_up/down() / cmd_walrus_cruise(y)
    $12D98/$12DAA/$12DBC, cmd_walrus_map_draw_mode() $12DEC, cmd_walrus_toggle_announce()
    $12DFC, cmd_set_walrus_waypoint(x, z)
    Walrus arming 16: cmd_walrus_arming_select(bay) $11DEE, cmd_walrus_loadout_next/prev()
    $11A30/$11A76, cmd_walrus_loadout_plus/minus() (Harbingers $11CE0/$11D0C or ACCB type
    $11D1C/$11D40), cmd_walrus_pick_loadout() $11A82, cmd_walrus_pick_slot(k) $11C16,
    cmd_walrus_fit(slot, code), cmd_walrus_unfit(slot)
    drags: cmd_drag_release(px, py), then ui_frame drops ($F6EA, callbacks DROPS)
    read helper: vehicle_ui() (selections, controllable, list cursors, Harbingers, drag)
Weapons, resources, messages, options, title ("War"; docs/units.md §10.4, §10.6):
    bridge 0: cmd_bridge_zoom_in/out() $FCE8/$FCF8 (Insert / Clr-Home in bridge_input too)
    laser turret 8 (input $1741A: held cursor keys, Insert / Clr-Home, right click):
    cmd_turret_up/down/left/right() $FD3C/$FD44/$FD2C/$FD34, cmd_turret_fire() $FD60,
    cmd_turret_zoom_in/out() $FC9C/$FCAC, cmd_turret_centre_heading/elevation() $12A34/$12A3C,
    cmd_turret_steer_to(heading_rate, elevation_rate) (mouse-steer; cmd_vehicle_fire fires)
    flares 9: cmd_flare_launch() $1338C; viewing drone 10 (input $1778E: a right click arms
    the SSM $1160): cmd_recon_launch() $134C2, cmd_ssm_fire_at(px, py) $13598
    defence drones 11: cmd_drones_launch() $10738, cmd_drones_recall() $1088C,
    cmd_drone_stations_reset() $10602, cmd_drone_formation_next/prev() $106EC/$10706,
    cmd_drone_formation_apply() $10714, cmd_drone_station_pick(k) $104C8 (drop $104E6),
    cmd_drone_station_move(k, px, py), drone_station_pixel(k)
    resources 3 (input $177DE; sub-panel $D14): cmd_resupply_abort() $1102E,
    cmd_resources_transfer() $10E4A, cmd_item_picture(k) / _back() $1098A/$109BC,
    stores priorities cmd_stores_priorities_open/reset/exit() $109E8/$10A66/$10A3C,
    cmd_stores_scroll_up/down() $10AA6/$10A8A, cmd_stores_class(row, cls) $10ABC,
    cmd_stores_target(row) $10B02 + cmd_keypad(key) $11196, fuel priorities
    cmd_fuel_priorities_open/reset/exit() $10B72/$10BC6/$10BAC, cmd_fuel_class(k, cls) $10BDA,
    queues cmd_stores_queue_open/back() $10C06/$10C62, cmd_stores_queue_scroll_up/down()
    $10C7C/$10C6A, cmd_stores_unqueue/queue(row, held) $10C88/$10CCA,
    cmd_fuel_queue_open/back() $10D52/$10D9C, cmd_fuel_unqueue/queue(k) $10DA4.../$10DC0...
    messages 4: cmd_clear_message_log(logged) $12C2C; options 32: cmd_game_option(k, on)
    $13778; title 33: cmd_title_new_game(strategy) $103B2/$103A0

The mixin is combined into stunits.UnitState ahead of stunits.UnitCore, so methods defined
here replace the core's stubs and hooks. Use only `import stsim` / `import stunits` at module
level (stunits imports this module while it is still loading; read its constants at run time).

For tools/check_units.py, this module may define:
  ROUTINES  {"name": (original address, "method name")}: checked on every capture
  SCENARIOS [function(mem, rnd)]: perturbations run pass by pass against the original
  CHECKS    [function(files) -> bool]: any other checks (e.g. commands called with registers)
"""
import stsim  # noqa: F401
import stunits  # noqa: F401
from stsim import s16, s32, w16  # noqa: F401

FLAG_TABLE = 0x40EB2                  # panel element flags, word per type: 15 hidden, 14 disabled,
#                                       13 highlight, 12 works while paused, 5 selected
RUNNING, MENU, MODAL = 0x462, 0x461, 0x460
END_REQUEST, RESULT, END_TIMER = 0x463, 0x466, 0x732
SCREEN_REQUEST, REQUEST_TAB = 0x1227, 0x1228
TAB, END_TAB = 0x714, 0x730
LAST_CHAR, CHEAT_PTR, CHEAT_CODE, DEBUG, CHEAT = 0x1270, 0x116E, 0x1172, 0x1189, 0x118A
KEYS = 0x798                          # 2 bytes per scancode: held, new
MAP_RECT = 0x47F2                     # x0 y0 x1 y1, +$C/+$E centre pixel
MAP_ZOOM_LEVEL, MAP_ZOOM, MAP_X, MAP_Z, MAP_ISLAND, MAP_INFO = 0x758, 0x75A, 0x75C, 0x760, 0x764, 0x766
CARRIER_MAP_MODE = 0x710
CURSORS = {25: (0x69E, 0x6A6), 17: (0x6B0, 0x6B4)}
CURSOR_CARRIER = (0x6BE, 0x6C2)
CRUISE, ANNOUNCE = 0x6BC, 0x6C6
PRIORITIES, PRIORITY_PANEL, SYSTEM_SHOWN, PART_SHOWN = 0xB44, 0xB62, 0xB58, 0xB5A
T_AUTOPILOT, T_ANNOUNCE, T_PAUSE, T_SYSTEM = 90, 92, 99, 48
PRIORITY_GRID = (200, 16, 200 + 0x53, 16 + 0x63)
THROTTLE_Y = (159, 159 + 0x24)        # bridge and carrier-map speed sliders
MAP_KEYPAD = (0x79D, 0x873, 0x79F, 0x875, 0x7A1, 0x877, 0x7A3, 0x86D)
EDGE_FLAGS = (0x883, 0x79D, 0x79F, 0x7A1, 0x7A3, 0x873, 0x875, 0x877, 0x86D, 0x83D, 0x827)
PENDING_TAB, SCREEN_SELECT, PANEL_LIST, TAB_SCREENS = 0x716, 0x71A, 0x71C, 0x720
CAMERA, CONTROLLED, CAM_MODE, VIEWPORT = 0x9FA, 0xA02, 0xA14, 0x6FE
GAME_STATUS = 0x708                   # game-status screen sub-mode: 0 menu, 1 format, 3 save, 5 error, 6 surrender, 7 options
STATION_SNAPSHOT = 0x141DE            # enter 11 copies the drone stations' offsets here (in the code)
MODEL_LIST = 0x64CFE                  # the damage diagram / title model list object 0 points at
T_TAB, T_STATUS_MENU = 32, 100        # tab icons 32 + tab; game-status buttons 100-103
# The screens of each tab (the left icon column: tab icon type 32 + tab); tabs 5-7 have no icon.
TAB_OF = {**{s: 1 + s // 8 for s in range(29) if s % 8 < 5}, 32: 0, 33: 5, 34: 6, 35: 7}
# $43856: each screen's panel list ($71C).
PANEL_LISTS = (0x43606, 0x43620, 0x43636, 0x43660, 0x4368A, 0x43604, 0x43604, 0x43604, 0x4369C, 0x436AE,
               0x436C0, 0x436D6, 0x436EC, 0x43604, 0x43604, 0x43604, 0x436FE, 0x4371C, 0x4373E, 0x43764,
               0x43776, 0x43604, 0x43604, 0x43604, 0x43788, 0x437A6, 0x437C8, 0x437DA, 0x43804, 0x43604,
               0x43604, 0x43604, 0x43816, 0x4383C, 0x4384A, 0x43850)
DAMAGE_PARTS = (0x4B, 0x4C, 0x4D, 0x4E, 0x4F, 0x50, 0x51, 0x52)    # $14148: the damage diagram's models
SUBPANEL = (0, 1, 1, 2)               # $11756: arming sub-panel by bay status (0, 1-2, 3+)
STATUS_ICON = (0, 3, 3, 2, 2, 0, 1, 4)   # $13B6C: game-status button lit per [$708] (1 + index from 100)
# Mantas and Walruses (screens 16-19, 24-27).
MANTA_RECS, WALRUS_RECS, REC = 0x496, 0x570, 0x34
MANTA_OBJS, WALRUS_OBJS = 0x5DF5A, 0x5E45A
LOADOUT = 0xCAC4                      # 26-byte entries: +0 weight, +2 stock pointer, +6 quantity, +8 Walrus slot (1: payload)
MANTA_CODES, WALRUS_CODES = 0xC770, 0xC242   # the arming screens' loadout lists (6 / 5 codes)
LOADOUT_CURSOR, DRAG_ELEMENT = 0xDB8, 0xDBC  # $DB8 Manta, $DBA Walrus list cursor; the slot element being dragged
DROP = 0x468                          # drag: the drop callback ($F73C), run by ui_frame on release ($F6EA)
HARBINGER_LOAD = 0x11BD2              # weight, stock pointer ($B98), quantity: the old Harbinger load (code)
HARBINGERS = 0xCB80                   # loadout entry 7's quantity: the Harbingers to load
KEY_ELEMENT = 0x13352                 # $132D6 writes its element here (type, index)
PLUS_HANDLER, MINUS_HANDLER = 0x4281C, 0x42832   # the Walrus arming +/- elements' handlers, patched by the panel draw
MAP_EDIT_MANTA = (0x69C, 0x69E, 0x6A2, 0x6A6, 0x6AA, 0x6AC)   # cruise, x, altitude (long), z, draw mode, announce
MAP_EDIT_WALRUS = (0x6AE, 0x6B0, 0x6B4, 0x6B8, 0x6BA)         # cruise, x, z, draw mode, announce
VEHICLE_THROTTLE_Y = (158, 159 + 0x26)   # the Manta/Walrus control screens' throttle slider
MAP_SLIDER_Y = (159, 159 + 0x24)         # the map screens' cruise and altitude sliders
T_BUTTON, T_WEAPON, T_TURRET, T_HARBINGERS, T_HANGAR, T_DRAW, T_SIGHT, T_REAR = 38, 62, 80, 81, 78, 91, 104, 105
T_MANTA, T_WALRUS = 58, 70            # select icons on the control and map screens (+ bay)
T_MANTA_ARMING, T_WALRUS_ARMING, T_MANTA_DECK, T_WALRUS_DOCK = 74, 82, 86, 93
# The arming screens' slot elements (panel data): slot index, pointer box x0, y0, x1, y1 ($B52C).
SLOT_ELEMENTS = {0x429BA: (0, 219, 70, 233, 93), 0x429D0: (1, 196, 90, 210, 113), 0x429E6: (2, 241, 90, 255, 113),
                 0x42836: (0, 173, 96, 188, 106), 0x4284C: (1, 201, 96, 248, 106)}
MANTA_SLOTS, WALRUS_SLOTS = (0x429BA, 0x429D0, 0x429E6), (0x42836, 0x4284C)
STORES_BOX = (0x21, 0x4B, 0xA2, 0x84)    # drop a fitted item here to return it to stores

# State these routines read or write that the other regions don't cover.
for _region in [
    ("game flags $460", MODAL, 3),                       # modal pointer, menu open, running
    ("current tab $714", TAB, 2),
    ("panel overlay $702", 0x702, 2),
    ("end screen tab $730", END_TAB, 2),
    ("carrier map mode $710", CARRIER_MAP_MODE, 2),
    ("screen select $71A", 0x71A, 2),
    ("logistics pass $8FE", 0x8FE, 1),
    ("joystick $888", 0x888, 1),
    ("screen request $1227", SCREEN_REQUEST, 1),
    ("screen request tab $1228", REQUEST_TAB, 2),
    ("palette cycle $1231", 0x1231, 1),
    ("last key $1270", LAST_CHAR, 1),
    ("key characters $1280", 0x1280, 0x80),
    ("cheat code $116E", CHEAT_PTR, 0x1188 - CHEAT_PTR),
    # Display state the UI code (not modelled) also changes; check_units.diff skips "redraw"
    # regions, so this module's checks compare it themselves (_diff).
    ("redraw: panel flags $40EB2", FLAG_TABLE, 0xD4),
    ("map view $758", MAP_ZOOM_LEVEL, 0x10),
    ("map island $742", 0x742, 8),
    ("map rect $47F2", MAP_RECT, 0x10),
    ("map cursors $69C", 0x69C, 0x6C8 - 0x69C),
    ("priority panel $B62", PRIORITY_PANEL, 4),
    # Screen switching (ui_frame $F636, enter $139D4 and leave $14318 handlers).
    ("panel states $704", 0x704, 6),                      # tab icons shown, screen icons shown, game-status mode $708
    ("vehicle controllable $70C", 0x70C, 4),             # $70C Manta, $70E Walrus
    ("pending tab $716", PENDING_TAB, 2),
    ("panel list $71C", PANEL_LIST, 4),
    ("tab screens $720", TAB_SCREENS, 0x10),
    ("viewport $6FE", VIEWPORT, 4),
    ("frame lock $8F0", 0x8F0, 1),
    ("camera mode $A14", CAM_MODE, 2),
    ("turret zoom view $A94", 0xA94, 8),
    ("vehicle view $1230", 0x1230, 1),
    ("screen flag $122D", 0x122D, 1),
    ("selected Manta $48C", 0x48C, 2),
    ("bay selection $DB4", 0xDB4, 4),
    ("station snapshot $141DE", STATION_SNAPSHOT, 0x10),
    # Mantas and Walruses (milestone 2): arming drags, number keys, map edit globals.
    ("loadout cursors $DB8", LOADOUT_CURSOR, 8),            # $DB8 Manta, $DBA Walrus, $DBC dragged slot element
    ("drop callback $468", DROP, 4),
    ("harbinger load $11BD2", HARBINGER_LOAD, 8),            # in the code: the old Harbinger load as a loadout entry
    ("key element $13352", KEY_ELEMENT, 4),                  # in the code: $132D6's fake panel element
    ("arming +/- handlers $4281C", PLUS_HANDLER, 0x1A),      # panel data: the type-81 buttons' handlers (+$12)
    ("loadout codes $C770", MANTA_CODES, 12),
    ("loadout codes $C242", WALRUS_CODES, 10),
    ("slot offsets $11448", 0x11448, 6),
    ("slot offsets $11C12", 0x11C12, 4),
]:
    if _region[0] not in {r[0] for r in stunits.UNIT_REGIONS}:
        stunits.UNIT_REGIONS.append(_region)
stunits.UNIT_INPUT_REGIONS |= {"current tab $714", "panel overlay $702", "key characters $1280", "map island $742", "map rect $47F2",
                               "loadout codes $C770", "loadout codes $C242", "slot offsets $11448", "slot offsets $11C12"}

# Milestone 3 ("War"): the weapons screens 8-11, resources 3, messages 4, options and title.
RESOURCES = 0xD14                     # resources sub-panel: 0 main, 1 item picture, 2 stores priorities,
#                                       3 fuel priorities, 4 stores queue, 5 fuel queue
FUEL_WORK = 0xD0E                     # fuel classes being edited (EXIT copies them to $D08)
ENTRY, ENTRY_DIGITS, ENTRY_COUNT, ENTRY_MAX = 0xD1A, 0xD1B, 0xD2A, 0xD2C   # keypad number entry ($11142)
ENTRY_OLD, ENTRY_CALLBACK, ENTRY_DONE = 0xD32, 0xD34, 0x10B5A
STORES_SCROLL, QUEUE_SCROLL, QUEUE_REPEAT = 0xD38, 0xD3A, 0xD3C
ITEM_PANEL, ITEM_EDITED = 0xD16, 0xD18
# $111D0: the keypad's key boxes relative to its element (216, 44): 0-9, 10 ENTER, 11 CANCEL, 12 DELETE.
KEYPAD = (216, 44)
KEYPAD_BOXES = ((0, 0x30, 0xE, 0x3E), (0, 0x20, 0xE, 0x2E), (0x10, 0x20, 0x1E, 0x2E), (0x20, 0x20, 0x2E, 0x2E),
                (0, 0x10, 0xE, 0x1E), (0x10, 0x10, 0x1E, 0x1E), (0x20, 0x10, 0x2E, 0x1E), (0, 0, 0xE, 0xE),
                (0x10, 0, 0x1E, 0xE), (0x20, 0, 0x2E, 0xE), (0x30, 0, 0x3E, 0x1E), (0x30, 0x20, 0x3E, 0x3E),
                (0x10, 0x30, 0x2E, 0x3E))
TARGET_ENTRY = ((120, 31), (120, 67), (120, 103))   # $E026: the target fields of the stores priority rows
CLASS_Y = (26, 16, 5)                 # $10AE6: a pointer offset in a class field for class 0, 1, 2
FORMATIONS = 0x1065C                  # 9 drone formations: 4 stations x (+$A, +$C)
T_STATION = 66                        # the drone radar's station markers, types 66-69
SSM_VIEW = (32, 6, 32 + 0xFF, 6 + 0x7F)   # screen 10: the drone picture's element ($13598)
OPTIONS = 0x1246                      # sea dots, message ping, background noise, sound effects
ISLAND_NAMES = 0x4AD20                # word offsets from here to each island's name
for _region in [
    ("resources panel $D0E", FUEL_WORK, 0xD3C - FUEL_WORK),  # fuel work, sub-panel, item, keypad entry, scrolls
    ("bridge zoom $8B2", 0x8B2, 4),                         # $8B2 view scale, $8B4 zoom (renderer)
    ("options $1246", OPTIONS, 4),
    ("drone formations $1065C", FORMATIONS, 0x90),          # in the code: read-only
    ("island names $4AD20", ISLAND_NAMES, 0x295),           # 64 word offsets, then the names
]:
    if _region[0] not in {r[0] for r in stunits.UNIT_REGIONS}:
        stunits.UNIT_REGIONS.append(_region)
stunits.UNIT_INPUT_REGIONS |= {"drone formations $1065C", "island names $4AD20"}


class ControlsMixin:
    # ---- the hit test's conditions ($B46E) ------------------------------------------------
    def _clickable(self, etype: int, state=(0x702, 0)) -> bool:
        """The panel is shown ([state addr] == value; None: always), the element type is
        neither hidden nor disabled, and the game runs unless the type works while paused
        (bit 12). No hit test runs during a drag ($460)."""
        m = self.mem
        if m.u8(MODAL):
            return False
        if state is not None and m.u16(state[0]) != state[1]:
            return False
        f = m.u16(FLAG_TABLE + 2 * etype)
        if f & 0xC000:
            return False
        return bool(m.u8(RUNNING) or f & 0x1000)

    def _message(self, text: str) -> None:
        self.events.append(("message", text, self._ping()))

    def _bclr0(self, a: int) -> bool:
        """bclr.b #0: clears bit 0 and says whether it was set."""
        v = self.mem.u8(a)
        self.mem.set8(a, v & 0xFE)
        return bool(v & 1)

    # ---- raw input (IKBD $17D80) -----------------------------------------------------------
    def input_mouse_move(self, dx: int, dy: int) -> None:
        """A mouse packet: the deltas add up in $1268/$126A until a routine consumes them
        (carrier_step in mouse-steer mode on screen 0, the turret, the vehicles)."""
        m = self.mem
        m.set16(stunits.MOUSE_DX, m.u16(stunits.MOUSE_DX) + dx)
        m.set16(stunits.MOUSE_DX + 2, m.u16(stunits.MOUSE_DX + 2) + dy)

    def input_mouse_button(self, right: int, down: int) -> None:
        """A press sets held and new ($0101) unless already held; a release clears held."""
        m = self.mem
        a = 0x882 if right else 0x880
        if not down:
            m.set8(a, 0)
        elif not m.u8(a):
            m.set16(a, 0x101)
            m.set8(0x889, 0xFF)

    def input_key(self, scancode: int, down: int) -> None:
        """A key press sets held and new and the last character $1270; a release clears held."""
        m = self.mem
        a = KEYS + 2 * (scancode & 0x7F)
        if not down:
            m.set8(a, 0)
            return
        m.set16(a, 0x101)
        m.set8(0x889, 0xFF)
        c = m.u8(0x1280 + (scancode & 0x7F))
        if 0x31 <= c <= 0x33:
            m.set8(0x1273, 0xFF)
        m.set8(LAST_CHAR, c)

    # ---- bridge, screen 0 ------------------------------------------------------------------
    def cmd_carrier_throttle(self, y: int) -> bool:
        """$FE3C: the throttle slider (x 288..295, y 159..195): target speed (187 - y) * 4,
        112 full ahead to -32 full astern, unless the autopilot steers. Works on auto-repeat,
        so dragging it applies every pass."""
        m = self.mem
        if not (THROTTLE_Y[0] <= y <= THROTTLE_Y[1] and self._clickable(38)):
            return False
        if not m.u8(stunits.C_FLAGS) & 1:
            m.set16(stunits.C_TARGET, w16((0xBB - y) * 4))
        return True

    def cmd_carrier_speed_up(self) -> bool:
        """$FDCE: the up chevron sets $88A; carrier_step makes the target speed + 1 and
        vehicles_step clears it (hold it by calling this every pass)."""
        if not self._clickable(38):
            return False
        self.mem.set8(stunits.KEY_SPEED_UP, 0xFF)
        return True

    def cmd_carrier_speed_down(self) -> bool:
        """$FDD4: the down chevron, $88B."""
        if not self._clickable(38):
            return False
        self.mem.set8(stunits.KEY_SPEED_DOWN, 0xFF)
        return True

    def cmd_carrier_stop(self) -> bool:
        """$12A26: target speed 0 unless the autopilot steers."""
        m = self.mem
        if not self._clickable(38):
            return False
        if not m.u8(stunits.C_FLAGS) & 1:
            m.set16(stunits.C_TARGET, 0)
        return True

    def cmd_carrier_centre_rudder(self) -> bool:
        """$12A16: $640 bit 7, the rudder self-centres (carrier_move) until mouse steering
        moves it again; not under autopilot."""
        m = self.mem
        if not self._clickable(38):
            return False
        if not m.u8(stunits.C_FLAGS) & 1:
            m.set8(stunits.C_FLAGS, m.u8(stunits.C_FLAGS) | 0x80)
        return True

    def _sync_auto_button(self) -> None:
        """$12C16: the auto button's selected bit (type 90) follows $640 bit 0."""
        self.mem.set16(FLAG_TABLE + 2 * T_AUTOPILOT, 0x2000 if self.mem.u8(stunits.C_FLAGS) & 1 else 0)

    def cmd_carrier_autopilot(self, held: int = 0) -> bool:
        """$12BAC (fresh press only): with the carrier under way ($641 = 1), the auto button
        (its selected bit, which the bridge input keeps equal to $640 bit 0) turns the
        autopilot off, or on if a course is set ($640 bit 1)."""
        m = self.mem
        if not self._clickable(T_AUTOPILOT):
            return False
        if held:
            return True
        if m.u8(stunits.C_SUBSTATE) != 1:
            return True
        flag = FLAG_TABLE + 2 * T_AUTOPILOT
        if m.u8(flag) & 0x20:
            m.set8(flag, m.u8(flag) & 0xDF)
            m.set8(stunits.C_FLAGS, m.u8(stunits.C_FLAGS) & 0xFE)
        elif m.u8(stunits.C_FLAGS) & 2:
            m.set8(flag, m.u8(flag) | 0x20)
            m.set8(stunits.C_FLAGS, m.u8(stunits.C_FLAGS) | 1)
            self._message("Autopilot on")
        else:
            self._message("No course set")
            return True
        self._sync_auto_button()
        return True

    def cmd_toggle_mouse_steer(self) -> None:
        """A right click (press and release): on screen 0 the next screen_input toggles
        mouse-steer mode ($457) unless the autopilot is on; on a map it recentres there."""
        self.input_mouse_button(1, 1)
        self.input_mouse_button(1, 0)

    def cmd_steer_to(self, rudder: int) -> None:
        """Mouse-steer input that makes the next carrier_step set the rudder $642 to
        `rudder` (clamped to +-255): it adds the delta to $642. Only on screen 0 in
        mouse-steer mode ($457 clear) without autopilot; vehicles_step drops it otherwise."""
        m = self.mem
        r = max(-255, min(255, rudder))
        m.set16(stunits.MOUSE_DX, w16(r - m.s16(stunits.C_RUDDER)))

    # ---- maps, screens 2/17/25 ---------------------------------------------------------------
    def map_to_world(self, px: int, py: int):
        """$F91E: the map pixel (px, py) as world (x, z), or None outside the map or beyond
        +-$7FFFFF. x = (px - cx) * zoom * 256 + [$75C], z = -(py - cy) * zoom * 256 + [$760]."""
        m = self.mem
        px, py = s16(px), s16(py)
        if px < m.s16(MAP_RECT) or px > m.s16(MAP_RECT + 4) or py < m.s16(MAP_RECT + 2) or py > m.s16(MAP_RECT + 6):
            return None
        zoom = m.s16(MAP_ZOOM)
        x = s32((s16(px - m.u16(MAP_RECT + 0xC)) * zoom) << 8)
        z = s32(-s32((s16(py - m.u16(MAP_RECT + 0xE)) * zoom) << 8))
        x = s32(x + m.u32(MAP_X))
        z = s32(z + m.u32(MAP_Z))
        if not -0x7FFFFF <= x <= 0x7FFFFF or not -0x7FFFFF <= z <= 0x7FFFFF:
            return None
        return x, z

    def world_to_map(self, x: int, z: int):
        """The map pixel nearest to world (x, z) (the inverse of map_to_world), or None if
        it is off the map."""
        m = self.mem
        step = m.s16(MAP_ZOOM) * 256
        if step <= 0:
            return None
        px = m.s16(MAP_RECT + 0xC) + (s32(x - m.u32(MAP_X)) + step // 2) // step
        py = m.s16(MAP_RECT + 0xE) - (s32(z - m.u32(MAP_Z)) + step // 2) // step
        if px < m.s16(MAP_RECT) or px > m.s16(MAP_RECT + 4) or py < m.s16(MAP_RECT + 2) or py > m.s16(MAP_RECT + 6):
            return None
        return px, py

    def _set_pointer(self, px: int, py: int) -> None:
        self.mem.set16(0x474, px)
        self.mem.set16(0x476, py)

    def cmd_map_click(self, px: int, py: int) -> bool:
        """$F8D8 (map element at (32, 6), 256 x 128; works on auto-repeat): the pointer moves
        to (px, py) and the map point under it becomes the cursor of the current map: the
        Manta map (screen 25) $69E/$6A6, the Walrus map (17) $6B0/$6B4, otherwise the
        carrier's $6BE/$6C2 while its map shows the course panel ($710 = 0)."""
        m = self.mem
        if not (32 <= px <= 32 + 0xFF and 6 <= py <= 6 + 0x7F and self._clickable(38, (MAP_INFO, 0))):
            return False
        self._set_pointer(px, py)
        p = self.map_to_world(px, py)
        if p is None:
            return True
        screen = m.u16(stunits.SCREEN)
        if screen in CURSORS:
            ax, az = CURSORS[screen]
        elif m.u16(CARRIER_MAP_MODE) == 0:
            ax, az = CURSOR_CARRIER
        else:
            return True
        m.set32(ax, p[0])
        m.set32(az, p[1])
        return True

    def cmd_map_recentre(self, px: int, py: int) -> None:
        """A right click on a map at (px, py): screen_input calls $F8B4 (centre the map on
        that point, zoom in one step, pointer to the map's centre). Here directly, as the
        next screen_input on screens 2/17/25 would (pointer moved, right click consumed)."""
        self._set_pointer(px, py)
        if self.mem.u16(MAP_INFO) == 0:
            self.map_recentre_at_pointer()

    def map_recentre_at_pointer(self) -> None:
        """$F8B4."""
        m = self.mem
        p = self.map_to_world(m.u16(0x474), m.u16(0x476))
        if p is None:
            return
        m.set32(MAP_X, p[0])
        m.set32(MAP_Z, p[1])
        self.map_zoom_in()
        m.set16(0x474, m.u16(MAP_RECT + 0xC))
        m.set16(0x476, m.u16(MAP_RECT + 0xE))

    def _map_zoom_set(self, level: int) -> None:
        """$F9B0: zoom level $758 and factor $75A = 1 << level."""
        self.mem.set16(MAP_ZOOM_LEVEL, level)
        self.mem.set16(MAP_ZOOM, 1 << (level & 63) if (level & 63) < 16 else 0)

    def map_zoom_in(self) -> None:
        """$F984."""
        m = self.mem
        if m.u16(MAP_INFO) or m.u16(MAP_ZOOM_LEVEL) == 0:
            return
        self._map_zoom_set(w16(m.u16(MAP_ZOOM_LEVEL) - 1))

    def map_zoom_out(self) -> None:
        """$F99A."""
        m = self.mem
        if m.u16(MAP_INFO) or m.u16(MAP_ZOOM_LEVEL) == 10:
            return
        self._map_zoom_set(w16(m.u16(MAP_ZOOM_LEVEL) + 1))

    def _map_scroll(self, a: int, sign: int) -> None:
        """$FA6C/$FA84/$FAB0/$FAC8: the map centre moves by zoom * $400, within +-$7FFFFF."""
        m = self.mem
        if m.u16(MAP_INFO):
            return
        v = s32(m.u32(a) + sign * m.u16(MAP_ZOOM) * 0x400)
        if -0x7FFFFF <= v <= 0x7FFFFF:
            m.set32(a, v)

    def map_scroll_up(self) -> None:
        self._map_scroll(MAP_Z, 1)

    def map_scroll_down(self) -> None:
        self._map_scroll(MAP_Z, -1)

    def map_scroll_left(self) -> None:
        self._map_scroll(MAP_X, -1)

    def map_scroll_right(self) -> None:
        self._map_scroll(MAP_X, 1)

    def cmd_map_zoom_in(self) -> bool:
        if not self._clickable(38):
            return False
        self.map_zoom_in()
        return True

    def cmd_map_zoom_out(self) -> bool:
        if not self._clickable(38):
            return False
        self.map_zoom_out()
        return True

    def cmd_map_scroll_up(self) -> bool:
        if not self._clickable(40):
            return False
        self.map_scroll_up()
        return True

    def cmd_map_scroll_down(self) -> bool:
        if not self._clickable(42):
            return False
        self.map_scroll_down()
        return True

    def cmd_map_scroll_left(self) -> bool:
        if not self._clickable(43):
            return False
        self.map_scroll_left()
        return True

    def cmd_map_scroll_right(self) -> bool:
        if not self._clickable(41):
            return False
        self.map_scroll_right()
        return True

    def _map_centre_on(self, x: int, z: int) -> None:
        """$F9E8: centre on (x, z), zoom level at most 1."""
        m = self.mem
        m.set32(MAP_X, x)
        m.set32(MAP_Z, z)
        level = m.u16(MAP_ZOOM_LEVEL)
        self._map_zoom_set(1 if 1 <= s16(level) else level)

    def _map_centre_carrier(self) -> None:
        """$FA56: the carrier, zoom level 1."""
        m = self.mem
        m.set32(MAP_X, m.u32(stunits.CARRIER + 0x0A))
        m.set32(MAP_Z, m.u32(stunits.CARRIER + 0x12))
        self._map_zoom_set(1)

    def cmd_map_centre_unit(self) -> bool:
        """$F9BE: centre on the selected Manta (screen 25) or Walrus (17) if it is out,
        else on the carrier; on the carrier map on the carrier, or on the island [$D4A]
        in the second sub-panel ($710 = 1)."""
        m = self.mem
        if not self._clickable(38):
            return False
        if m.u16(MAP_INFO):
            return True
        screen = m.u16(stunits.SCREEN)
        if screen in (0x19, 0x11):
            rec, obj = (0x48E, 0x492) if screen == 0x19 else (0x568, 0x56C)
            status = m.u8(m.u32(rec) + 1)
            if status & 0x80:
                return True
            if status < 8:
                self._map_centre_carrier()
                return True
            o = m.u32(obj)
        elif m.u16(CARRIER_MAP_MODE):
            k = m.u16(0xD4A)
            if k & 0x8000:
                return True
            isl = stsim.ISLAND_TABLE + k * stsim.ISLAND_SIZE
            self._map_centre_on(m.u32(isl + 2), m.u32(isl + 6))
            return True
        else:
            o = stunits.CARRIER
        self._map_centre_on(m.u32(o + 0x0A), m.u32(o + 0x12))
        return True

    def cmd_map_centre_carrier(self) -> bool:
        """$FA50 (Walrus map): centre on the carrier, zoom level 1."""
        if not self._clickable(38):
            return False
        if not self.mem.u16(MAP_INFO):
            self._map_centre_carrier()
        return True

    def cmd_map_island_info(self) -> bool:
        """$FAF4: show the island under the map cursor ([$764], position $742/$746)."""
        m = self.mem
        if not self._clickable(38):
            return False
        if m.u16(MAP_INFO) or m.u16(MAP_ISLAND) & 0x8000:
            return True
        m.set32(MAP_X, m.u32(0x742))
        m.set32(MAP_Z, m.u32(0x746))
        m.set16(MAP_INFO, 1)
        return True

    def cmd_map_island_info_exit(self) -> bool:
        """$FB1A: EXIT of the island panel."""
        if not self._clickable(38, (MAP_INFO, 1)):
            return False
        self.mem.set16(MAP_INFO, 0)
        return True

    def cmd_carrier_set_course(self, held: int = 0) -> bool:
        """$131A8, the carrier map's course button. A fresh press sets the course: waypoint
        $64E/$652 from the cursor $6BE/$6C2, cruise speed $656 from $6BC, $640 bit 1, and
        bit 2 (report arrival) from $6C6. Held (auto-repeat), it engages the autopilot."""
        m = self.mem
        if not self._clickable(38, (CARRIER_MAP_MODE, 0)):
            return False
        if m.u16(MAP_INFO):
            return True
        f = m.u8(stunits.C_FLAGS)
        if held:
            if not f & 2 or f & 1:
                return True
            m.set8(stunits.C_FLAGS, f | 1)
            self._message("Carrier autopilot engaged")
            return True
        f = (f | 2) & ~4
        if m.u8(ANNOUNCE):
            f |= 4
        m.set8(stunits.C_FLAGS, f)
        m.set16(stunits.C_CRUISE, m.u16(CRUISE))
        m.set32(stunits.C_WAYPOINT_X, m.u32(CURSOR_CARRIER[0]))
        m.set32(stunits.C_WAYPOINT_Z, m.u32(CURSOR_CARRIER[1]))
        self._message("Carrier course set" if f & 1 else "Carrier course set - engage autopilot")
        return True

    def cmd_carrier_clear_course(self) -> bool:
        """$1326C: clears $640 bit 1 (the autopilot then stops the carrier)."""
        m = self.mem
        if not self._clickable(38, (CARRIER_MAP_MODE, 0)):
            return False
        if m.u16(MAP_INFO):
            return True
        if m.u8(stunits.C_FLAGS) & 2:
            m.set8(stunits.C_FLAGS, m.u8(stunits.C_FLAGS) & 0xFD)
            self._message("Carrier course cleared")
        else:
            self._message("Carrier has no course to clear")
        return True

    def cmd_carrier_map_mode(self) -> bool:
        """$1336E: switch the carrier map's sub-panel ($710: 0 course, 1 islands) and
        request the screen again ($71A)."""
        m = self.mem
        if not self._clickable(38, None):
            return False
        if not m.u16(MAP_INFO):
            m.set16(CARRIER_MAP_MODE, m.u16(CARRIER_MAP_MODE) ^ 1)
            m.set16(0x71A, m.u16(stunits.SCREEN))
        return True

    def _set_cruise(self, v: int) -> None:
        """$12EB0: cruise speed $6BC, 0..$70."""
        v = s16(v)
        self.mem.set16(CRUISE, 0x70 if v > 0x70 else 0 if v < 0 else v)

    def cmd_carrier_cruise_up(self) -> bool:
        """$12E7A: cruise speed + 8 (auto-repeats)."""
        if not self._clickable(38, (CARRIER_MAP_MODE, 0)):
            return False
        if not self.mem.u16(MAP_INFO):
            self._set_cruise(self.mem.u16(CRUISE) + 8)
        return True

    def cmd_carrier_cruise_down(self) -> bool:
        """$12E8C: cruise speed - 8."""
        if not self._clickable(38, (CARRIER_MAP_MODE, 0)):
            return False
        if not self.mem.u16(MAP_INFO):
            self._set_cruise(self.mem.u16(CRUISE) - 8)
        return True

    def cmd_carrier_cruise(self, y: int) -> bool:
        """$12E9E: the cruise slider (y 159..195): (187 - y) * 4, clamped to 0..$70."""
        if not (THROTTLE_Y[0] <= y <= THROTTLE_Y[1] and self._clickable(38, (CARRIER_MAP_MODE, 0))):
            return False
        if not self.mem.u16(MAP_INFO):
            self._set_cruise((0xBB - y) * 4)
        return True

    def cmd_carrier_toggle_announce(self) -> bool:
        """$12E44: report arrival at the waypoint ($6C6; set course copies it to $640 bit 2).
        No fresh-press check: holding it toggles every pass."""
        m = self.mem
        if not self._clickable(T_ANNOUNCE, (CARRIER_MAP_MODE, 0)):
            return False
        if not m.u16(MAP_INFO):
            m.set8(ANNOUNCE, ~m.u8(ANNOUNCE))
            m.set16(FLAG_TABLE + 2 * T_ANNOUNCE, 0x2000 if m.u8(ANNOUNCE) else 0)
        return True

    def cmd_set_carrier_waypoint(self, x: int, z: int) -> bool:
        """The UI's "sail to (x, z)": the carrier map cursor $6BE/$6C2 at world (x, z), as a
        map click there writes it, then a fresh press of set course. The original's map
        only produces points on its zoom grid ($75C + k * zoom * 256); any x/z within
        +-$7FFFFF is accepted here. Engage the autopilot with cmd_carrier_autopilot() or
        cmd_carrier_set_course(1)."""
        m = self.mem
        if not (self._clickable(38, (MAP_INFO, 0)) and m.u16(CARRIER_MAP_MODE) == 0):
            return False
        m.set32(CURSOR_CARRIER[0], s32(x))
        m.set32(CURSOR_CARRIER[1], s32(z))
        return self.cmd_carrier_set_course(0)

    # ---- damage screen 1 ------------------------------------------------------------------
    def cmd_select_system(self, k: int) -> bool:
        """$FB3A: show system k (0-9) on the damage screen: $B58 (and the lift's part cycle
        $B5A from 0), and its part of the damage diagram (the model list's entries from
        $64D50) highlighted (visible bit) instead of the old one's."""
        m = self.mem
        if not (0 <= k <= 9 and self._clickable(T_SYSTEM + k, (0xB64, 0))):
            return False
        a = FLAG_TABLE + 2 * (T_SYSTEM + k)
        if m.u8(a) & 0x20:
            return True
        m.set8(a, m.u8(a) | 0x20)
        old = m.s16(SYSTEM_SHOWN)
        parts = MODEL_LIST + 0x52                               # $64D50: the diagram's parts
        if old >= 0:
            b = FLAG_TABLE + s16(2 * old + 0x60)                # bclr #5,(a1,d0.w)
            m.set8(b, m.u8(b) & 0xDF)
            part = old if old != 8 else m.s16(PART_SHOWN)
            if old <= 8 and part != 8:
                e = parts + s16(w16((part & 0xFFFF) * 0x40))
                m.set8(e + 1, m.u8(e + 1) & 0xFD)
        m.set16(SYSTEM_SHOWN, k)
        if k <= 8:
            if k == 8:
                m.set16(PART_SHOWN, 0)
            e = parts + 0x40 * (k if k != 8 else 0)
            m.set8(e + 1, m.u8(e + 1) | 2)
        return True

    def _copy_priorities(self) -> None:
        """$FBF2: the classes (+4 of each $AF4 record) into the work table $B44."""
        m = self.mem
        for k in range(10):
            m.set16(PRIORITIES + 2 * k, m.u16(stsim.SYSTEMS + 8 * k + 4))

    def cmd_priorities_open(self) -> bool:
        """$FBBE: the set-priorities panel ($B62 = $B64 = 1) on a copy of the classes."""
        m = self.mem
        if not self._clickable(38, (0xB64, 0)):
            return False
        self._copy_priorities()
        m.set16(PRIORITY_PANEL, 1)
        m.set16(PRIORITY_PANEL + 2, 1)
        return True

    def cmd_priority_click(self, px: int, py: int) -> bool:
        """$FC64: the grid at (200, 16), 84 x 100: row (py - 16) / 10 is the system, column
        (px - 200) / 28 its class (0-2) in the work table."""
        m = self.mem
        if not (PRIORITY_GRID[0] <= px <= PRIORITY_GRID[2] and PRIORITY_GRID[1] <= py <= PRIORITY_GRID[3]
                and self._clickable(38, (PRIORITY_PANEL, 1))):
            return False
        row = stsim.divu(s16(py - 16), 10) & 0xFFFF
        col = stsim.divu(s16(px - 200), 0x1C) & 0xFFFF
        m.set16(PRIORITIES + s16(w16(2 * row)), col)
        return True

    def cmd_priorities_cancel(self) -> bool:
        """$FC08: CANCEL, the work table back to the current classes."""
        if not self._clickable(38, (PRIORITY_PANEL, 1)):
            return False
        self._copy_priorities()
        return True

    def cmd_priorities_exit(self) -> bool:
        """$FC24: EXIT, the work table becomes the classes; the panel closes."""
        m = self.mem
        if not self._clickable(38, (PRIORITY_PANEL, 1)):
            return False
        for k in range(10):
            m.set16(stsim.SYSTEMS + 8 * k + 4, m.u16(PRIORITIES + 2 * k))
        m.set16(PRIORITY_PANEL, 0)
        m.set16(PRIORITY_PANEL + 2, 0)
        return True

    def cmd_set_repair_priority(self, k: int, cls: int) -> bool:
        """System k (0-9) to repair class cls (0-2) the way a player does it: open the
        panel, click its row and column, EXIT."""
        if not (0 <= k <= 9 and 0 <= cls <= 2):
            return False
        if self.mem.u16(PRIORITY_PANEL) != 1 and not self.cmd_priorities_open():
            return False
        self.cmd_priority_click(PRIORITY_GRID[0] + 28 * cls, PRIORITY_GRID[1] + 10 * k)
        return self.cmd_priorities_exit()

    # ---- screen switching (ui_frame $F636) -----------------------------------------------
    def ui_frame(self) -> None:
        """$F636 without the hit test (the UI calls the cmd_ methods for clicks): a forced
        tab request ($1227, tab [$1228]: new game, game over, continue) is taken first;
        otherwise the pointer button state ($17EEA), and unless a drag is in progress
        ($460), the pending screen $71A or tab $716. Call it once per pass before
        game_frame, after the commands."""
        m = self.mem
        if m.u8(SCREEN_REQUEST):
            self._take_request()
            return
        self.pointer_button()
        if m.u8(MODAL):
            self.drag_drop()
            return
        self._switch_pending()

    def new_game_screen(self) -> None:
        """Replaces stunits_setup's (which ports only the bridge): after new_game, the
        forced request $42A8 leaves (tab 1, the bridge), as ui_frame takes it."""
        if self.mem.u8(SCREEN_REQUEST):
            self._take_request()

    def pointer_button(self) -> None:
        """$17EEA: in pointer mode the left button becomes the click state $45A (2 a new
        press, consuming $881; 1 when held for 6 passes; else 0)."""
        m = self.mem
        if not m.u8(stunits.POINTER_MODE):
            return
        if self._bclr0(0x881):
            m.set16(0x45A, 2)
            m.set16(0x45C, 0)
            m.set16(0x45E, 0)
            return
        m.set16(0x45A, 0)
        if not m.u8(0x880):
            return
        if m.s16(0x45C) < 6:
            m.set16(0x45C, m.u16(0x45C) + 1)
        else:
            m.set16(0x45A, 1)
            m.set16(0x45E, 0)

    def _take_request(self) -> None:
        """$F63C: the forced request: tab [$1228]."""
        m = self.mem
        m.set8(SCREEN_REQUEST, 0)
        self._switch_tab(m.u16(REQUEST_TAB))

    def _after_click(self) -> None:
        """$F67A, the rest of ui_frame after a click handler: a forced request it made,
        else the pending screen or tab."""
        if self.mem.u8(SCREEN_REQUEST):
            self._take_request()
        else:
            self._switch_pending()

    def _switch_pending(self) -> None:
        """$F68C: a pending screen $71A (in the current tab, which remembers it in $720),
        else a pending tab $716 (its remembered screen)."""
        m = self.mem
        screen = m.u16(SCREEN_SELECT)
        if not screen & 0x8000:
            m.set16(TAB_SCREENS + s16(w16(2 * m.u16(TAB))), screen)
            self._switch(screen)
            return
        tab = m.u16(PENDING_TAB)
        if not tab & 0x8000:
            self._switch_tab(tab)

    def _switch_tab(self, tab: int) -> None:
        """$F6A8: tab $714 = tab, to its remembered screen [$720 + 2 * tab]."""
        m = self.mem
        m.set16(TAB, tab)
        self._switch(m.u16(TAB_SCREENS + s16(w16(2 * tab))))

    def _switch(self, screen: int) -> None:
        """$F6B6: leave the current screen, enter the new one ($139AE first zeroes $122D and
        the view pointers $9FA camera, $9FE viewed, $A02 controlled), its panel list $71C,
        no request pending; of the panel drawing $782A only the game state two panel
        callbacks write (panels_drawn)."""
        m = self.mem
        if screen > 35:
            raise NotImplementedError(f"switch to screen {screen} (not in the tables)")
        self.screen_leave()
        m.set16(stunits.SCREEN, screen)
        m.set8(0x122D, 0)
        for a in (CAMERA, stunits.VIEWED, CONTROLLED):
            m.set32(a, 0)
        handler = SCREEN_ENTER.get(screen)
        if handler:
            getattr(self, handler)()
        m.set32(PANEL_LIST, PANEL_LISTS[screen])
        m.set8(PENDING_TAB, 0xFF)                         # st.b: the high bytes
        m.set8(SCREEN_SELECT, 0xFF)
        self.panels_drawn(screen)

    def panels_drawn(self, screen: int) -> None:
        """$782A draws the new screen's panels; two panel callbacks write game state: the
        arming screens' loadout panels (24: _manta_panel, 16: _walrus_panel)."""
        if screen == 24:
            self._manta_panel()
        elif screen == 16:
            self._walrus_panel()

    def screen_leave(self) -> None:
        """$14302: the current screen's leave handler (table $14318; none while [$718] < 0)."""
        screen = self.mem.u16(stunits.SCREEN)
        if screen & 0x8000:
            return
        if screen > 35:
            raise NotImplementedError(f"leave handler of screen {screen} (not in the table)")
        handler = SCREEN_LEAVE.get(screen)
        if handler:
            getattr(self, handler)()

    # ---- the click handlers of the icon columns ------------------------------------------
    def cmd_select_tab(self, tab: int) -> bool:
        """$F844, a tab icon (left column, type 32 + tab: 1 carrier, 2 weapons, 3 Walrus,
        4 Manta, 0 game status): unless it is lit already, it is lit, the current tab's
        icon cleared and the tab requested ($716); ui_frame then switches to the tab's
        remembered screen [$720 + 2 * tab] (leave, enter)."""
        m = self.mem
        if not (0 <= tab <= 4 and self._clickable(T_TAB + tab, (0x704, 0))):
            return False
        a = FLAG_TABLE + 2 * (T_TAB + tab)
        if not m.u8(a) & 0x20:
            m.set8(a, m.u8(a) | 0x20)
            m.set16(PENDING_TAB, tab)
            b = FLAG_TABLE + s16(w16(2 * (m.u16(TAB) + T_TAB)))
            m.set8(b, m.u8(b) & 0xDF)
        self._after_click()
        return True

    def cmd_select_screen(self, screen: int) -> bool:
        """$F880, a screen icon (right column, type = screen; the UI offers the current
        tab's screens): unless it is lit, it is lit, the current screen's icon cleared and
        the screen requested ($71A); ui_frame records it as the tab's screen and switches."""
        m = self.mem
        if not (0 <= screen <= 28 and self._clickable(screen, (0x706, 0))):
            return False
        a = FLAG_TABLE + 2 * screen
        if not m.u8(a) & 0x20:
            m.set8(a, m.u8(a) | 0x20)
            m.set16(SCREEN_SELECT, screen)
            b = FLAG_TABLE + s16(w16(2 * m.u16(stunits.SCREEN)))
            m.set8(b, m.u8(b) & 0xDF)
        self._after_click()
        return True

    def cmd_switch_screen(self, screen: int) -> bool:
        """What a player clicks to get to `screen` (0-4, 8-12, 16-20, 24-28 or 32): its tab
        icon if another tab is shown (which enters that tab's remembered screen first), then
        the screen's icon if it isn't the one shown. False if a click wasn't possible."""
        m = self.mem
        tab = TAB_OF.get(screen, 5)
        if tab > 4:
            return False
        if m.u16(TAB) != tab and not self.cmd_select_tab(tab):
            return False
        if m.u16(stunits.SCREEN) != screen:
            return self.cmd_select_screen(screen)
        return True

    # ---- game status (32) and enemy carrier destroyed (35) ---------------------------------
    def cmd_game_status_menu(self, mode: int) -> bool:
        """$101F2 / $10200 / $101E4 / $1020E (game-status buttons, types 100-103): the
        sub-mode $708 = 6 surrender / 3 save / 1 format / 7 options, and the screen
        re-entered ($71A = 32). Saving, formatting and the options panel themselves are
        not ported."""
        k = {6: 0, 3: 1, 1: 2, 7: 3}.get(mode)
        if k is None or not self._clickable(T_STATUS_MENU + k, (0x706, 0)):
            return False
        self.mem.set16(GAME_STATUS, mode)
        self.mem.set16(SCREEN_SELECT, 0x20)
        self._after_click()
        return True

    def cmd_game_status_back(self) -> bool:
        """$1021C: CANCEL / NO / OK / EXIT of a game-status sub-panel ($708 = 1, 3, 5, 6
        or 7): back to the menu ($708 = 0), the screen re-entered."""
        m = self.mem
        if m.u16(GAME_STATUS) not in (1, 3, 5, 6, 7) or not self._clickable(38, None):
            return False
        m.set16(GAME_STATUS, 0)
        m.set16(SCREEN_SELECT, 0x20)
        self._after_click()
        return True

    def cmd_surrender(self) -> bool:
        """$101D8, YES on the surrender panel ($708 = 6): the end request $463 with result
        0; the next game_frame requests the game-over screen 34."""
        m = self.mem
        if not self._clickable(38, (GAME_STATUS, 6)):
            return False
        m.set8(END_REQUEST, 0xFF)
        m.set16(RESULT, 0)
        self._after_click()
        return True

    def cmd_continue(self, yes: int) -> bool:
        """Screen 35 (enemy carrier destroyed, "continue?"): YES $13990 goes back to the tab
        [$730] the game was on; NO $1399C sets the result $466 = 4 and goes to the game-over
        screen 34 (tab 6)."""
        m = self.mem
        if not self._clickable(38):
            return False
        if yes:
            m.set16(REQUEST_TAB, m.u16(END_TAB))
        else:
            m.set16(RESULT, 4)
            m.set16(REQUEST_TAB, 6)
        m.set8(SCREEN_REQUEST, 0xFF)
        self._after_click()
        return True

    # ---- screen enter handlers ($139D4) ----------------------------------------------------
    def _carrier_view(self) -> int:
        """The carrier view of the bridge, weapons, hangar and deck screens: world objects
        drawn ($A46/$A47 = 0), camera = object 0, viewed = controlled = the carrier, camera
        mode $A14 = 2. Returns object 0."""
        m = self.mem
        m.set8(0xA46, 0)
        m.set8(0xA47, 0)
        cam = self.obj(0)
        m.set32(CAMERA, cam)
        m.set32(stunits.VIEWED, stunits.CARRIER)
        m.set32(CONTROLLED, stunits.CARRIER)
        m.set16(CAM_MODE, 2)
        return cam

    def _model_list(self, n: int) -> int:
        """The header of the model list at $64CFE (damage diagram, title): n entries, 2000
        ahead, 200 down. Returns the first entry."""
        m = self.mem
        m.set8(MODEL_LIST, 1)
        m.set8(MODEL_LIST + 1, 0xFF)
        m.set32(MODEL_LIST + 2, 0)
        m.set32(MODEL_LIST + 6, 0xFFFFFF38)
        m.set32(MODEL_LIST + 0xA, 0x7D0)
        m.set16(MODEL_LIST + 0xE, n)
        return MODEL_LIST + 0x12

    def _camera_at_list(self) -> None:
        """Object 0 at the origin looking at the model list ($64CFE), as the camera."""
        m = self.mem
        cam = self.obj(0)
        m.set32(CAMERA, cam)
        for o in (4, 6, 8):
            m.set16(cam + o, 0)
        for o in (0xA, 0xE, 0x12):
            m.set32(cam + o, 0)
        m.set32(cam + 0x3C, MODEL_LIST)

    def _set_gauges(self, *timers: int) -> None:
        for a in timers:
            self.mem.set16(a, 2)

    def enter_bridge(self) -> None:
        """$13C24 (screen 0): the carrier view, object 0 the camera 240 up on the carrier's
        island buffer, $1230 set; the gauges redraw, the auto button follows $640 bit 0."""
        m = self.mem
        m.set8(0x1230, 0xFF)
        cam = self._carrier_view()
        m.set16(cam, 0)
        m.set32(cam + 0xE, 0xF0)
        m.set32(cam + 0x3C, m.u32(stunits.CARRIER + 0x3C))
        m.set32(VIEWPORT, 0x4744)
        self._set_gauges(0x954, 0x958, 0x956, 0x962)
        m.set16(0x480, m.u16(stunits.C_DEPTH))
        self._sync_auto_button()

    def enter_damage(self) -> None:
        """$1404E (screen 1): no world objects ($A46/$A47 set); the damage diagram (the
        carrier and parts $4B-$52) as the model list, the shown system [$B58] highlighted
        (the lift, 8: part 0, $B5A = 0); object 0 the camera at the origin."""
        m = self.mem
        m.set8(0xA46, 0xFF)
        m.set8(0xA47, 0xFF)
        m.set16(0x952, 0)
        a = self._model_list(len(DAMAGE_PARTS) + 1)
        m.set16(a, 3)
        m.set16(a + 2, 0x4A)
        for o in (4, 6, 8, 0xA, 0xC, 0xE):
            m.set16(a + o, 0)
        first = a + 0x40
        for k, part in enumerate(DAMAGE_PARTS):
            e = first + 0x40 * k
            m.set16(e, 1)
            m.set16(e + 2, part)
            m.set16(e + 0xA, 0)
            m.set16(e + 0xC, 0)
            m.set16(e + 0xE, 0xFFFF)
        k = m.s16(SYSTEM_SHOWN)
        if 0 <= k <= 8:
            if k == 8:
                k = 0
                m.set16(PART_SHOWN, 0)
            e = first + 0x40 * k
            m.set8(e + 1, m.u8(e + 1) | 2)
        self._camera_at_list()
        m.set32(VIEWPORT, 0x4744)

    def _map_view_from(self, copy: int) -> None:
        self.mem.buf[MAP_ZOOM_LEVEL:MAP_ZOOM_LEVEL + 0x10] = self.mem.buf[copy:copy + 0x10]

    def _icon(self, etype: int, on) -> None:
        self.mem.set16(FLAG_TABLE + 2 * etype, 0x2000 if on else 0)

    def enter_carrier_map(self) -> None:
        """$13AB2 (screen 2): the announce icon (type 92) from $6C6, the carrier map's view
        $758 from its copy $768."""
        m = self.mem
        self._icon(T_ANNOUNCE, m.u8(ANNOUNCE))
        m.set16(0x95C, 2)
        self._map_view_from(0x768)

    def enter_messages(self) -> None:
        """$14288 (screen 4): the message log redraws."""
        self.mem.set16(0x966, 2)
        self.mem.set32(VIEWPORT, 0x4744)

    def enter_turret(self) -> None:
        """$1415A (screen 8): the carrier view (object 0 on the carrier's island buffer),
        object 1 off; the turret zoom's view offsets $A94 = z * 10000, $A98 = z * 14142
        ($FCD4, z = [$6D2])."""
        m = self.mem
        m.set8(0xA46, 0)
        m.set8(0xA47, 0)
        m.set16(self.obj(1), 0)
        cam = self._carrier_view()
        m.set16(cam, 0)
        m.set32(cam + 0x3C, m.u32(stunits.CARRIER + 0x3C))
        m.set32(VIEWPORT, 0x4786)
        m.set16(0x968, 2)
        self.turret_zoom_view(m.u16(0x6D2))

    def turret_zoom_view(self, z: int) -> None:
        """$FCD4: the turret view's offsets for zoom z."""
        self.mem.set32(0xA94, (z & 0xFFFF) * 0x2710)
        self.mem.set32(0xA98, (z & 0xFFFF) * 0x373E)

    def enter_flares(self) -> None:
        """$13A64 (screen 9): the carrier view, object 0 the camera 300 up (not 240)."""
        m = self.mem
        cam = self._carrier_view()
        m.set16(cam, 0)
        m.set32(cam + 0xE, 0x12C)
        m.set16(cam + 8, 0)
        m.set32(cam + 0x3C, m.u32(stunits.CARRIER + 0x3C))
        m.set32(VIEWPORT, 0x4770)

    def enter_drone_view(self) -> None:
        """$142A0 (screen 10): camera = viewed = controlled = the viewing drone (object 15),
        made invisible (it's the camera); camera mode 3; SSM disarmed ($1160 = 0)."""
        m = self.mem
        m.set8(0xA46, 0)
        m.set8(0xA47, 0)
        o = self.obj(15)
        for a in (CAMERA, stunits.VIEWED, CONTROLLED):
            m.set32(a, o)
        m.set8(o + 1, m.u8(o + 1) & 0xFD)
        m.set16(CAM_MODE, 3)
        m.set8(0x1160, 0)
        m.set32(VIEWPORT, 0x4770)

    def enter_drone_radar(self) -> None:
        """$141B2 (screen 11): snapshot the 4 drone stations' offsets ($65E + 14i, +$A/+$C)
        into $141DE (RESET restores them, $10602)."""
        m = self.mem
        for i in range(4):
            a = 0x65E + 14 * i
            m.set16(STATION_SNAPSHOT + 4 * i, m.u16(a + 0xA))
            m.set16(STATION_SNAPSHOT + 4 * i + 2, m.u16(a + 0xC))
        m.set16(0x95A, 2)

    def _bay_record(self, base: int, k: int) -> int:
        """mulu.w + adda.w: base + the low word of k * $34, sign-extended."""
        return (base + s16(w16((k & 0xFFFF) * 0x34))) & 0xFFFFFFFF

    def _bay_object(self, base: int, k: int) -> int:
        return (base + s16(w16((k & 0xFFFF) * 0x140))) & 0xFFFFFFFF

    def _subpanel(self, rec: int) -> int:
        """$11738/$11E60: the arming sub-panel for a bay's status byte +1 (0, 1-2, 3+)."""
        s = self.mem.u8(rec + 1)
        return SUBPANEL[s if s < 4 else 3]

    def enter_walrus_arming(self) -> None:
        """$13C94 (screen 16): the carrier view with object 0 as the hangar (flags 3, type
        $53, 140 up); the Walrus record $568 of the arming bay [$DB6], sub-panel $DC2."""
        m = self.mem
        cam = self._carrier_view()
        m.set16(cam, 3)
        m.set16(cam + 2, 0x53)
        m.set32(cam + 0xE, 0x8C)
        m.set32(cam + 0x3C, m.u32(stunits.CARRIER + 0x3C))
        rec = self._bay_record(0x570, m.u16(0xDB6))
        m.set32(0x568, rec)
        m.set16(0xDC2, self._subpanel(rec))
        m.set16(0x960, 0)
        m.set16(0x95C, 2)
        m.set32(VIEWPORT, 0x475A)

    def select_walrus(self):
        """$14224: the records of the selected Walrus [$566]: $568 record, $56C object."""
        m = self.mem
        k = m.u16(0x566)
        rec, o = self._bay_record(0x570, k), self._bay_object(0x5E45A, k)
        m.set32(0x568, rec)
        m.set32(0x56C, o)
        return rec, o

    def select_manta(self):
        """$141FE: the records of the selected Manta [$48C]: $48E record, $492 object."""
        m = self.mem
        k = m.u16(0x48C)
        rec, o = self._bay_record(0x496, k), self._bay_object(0x5DF5A, k)
        m.set32(0x48E, rec)
        m.set32(0x492, o)
        return rec, o

    def enter_walrus_map(self) -> None:
        """$13AC2 (screen 17): the selected Walrus's records, the draw-mode and announce
        icons (types 91/92 from $6B8/$6BA), the view $758 from its copy $778."""
        m = self.mem
        self.select_walrus()
        self._icon(91, m.u16(0x6B8))
        self._icon(92, m.u8(0x6BA))
        self._map_view_from(0x778)

    def enter_walrus_control(self) -> None:
        """$13F06 (screen 18): viewed = the selected Walrus (made invisible; the camera $9FA
        stays 0), camera mode 1, objects 0 and 1 off, $1230 set, missile lock timer $116C
        = 0; controllable / controlled via walrus_view_update. Not controllable: pointer
        mode."""
        m = self.mem
        m.set8(0xA46, 0)
        m.set8(0xA47, 0)
        m.set8(0x1230, 0xFF)
        m.set16(self.obj(1), 0)
        m.set16(0x116C, 0)
        rec, o = self.select_walrus()
        m.set32(stunits.VIEWED, o)
        m.set16(CAM_MODE, 1)
        m.set8(o + 1, m.u8(o + 1) & 0xFD)
        m.set16(self.obj(0), 0)
        m.set32(VIEWPORT, 0x4744)
        self.walrus_view_update(rec)
        if not m.u16(0x70E):
            m.set8(stunits.POINTER_MODE, 0xFF)

    def _vehicle_icons(self, rec: int, controllable: int, weapon: int) -> None:
        """$13FBA/$14004: weapon icons 62-64, auto (90) and sight (104) icons."""
        m = self.mem
        m.set32(FLAG_TABLE + 2 * 62, 0)
        m.set16(FLAG_TABLE + 2 * 64, 0)
        m.set16(FLAG_TABLE + 2 * T_AUTOPILOT, 0)
        m.set16(FLAG_TABLE + 2 * 104, 0)
        if not controllable:
            return
        if m.u8(rec) & 1:
            m.set16(FLAG_TABLE + 2 * T_AUTOPILOT, 0x2000)
        if m.u8(rec + 0x32):
            m.set16(FLAG_TABLE + 2 * 104, 0x2000)
        m.set16(FLAG_TABLE + 2 * 62 + s16(w16(2 * m.u16(rec + weapon))), 0x2000)

    def walrus_view_update(self, rec: int) -> None:
        """$13F5A: rear view off ($8F1), the Walrus controllable ($70E) if its status
        (+1, signed) is >= 8, then controlled $A02 = viewed, else 0; icons (turret icon 80
        from bit 5, $13FA6), gauges."""
        m = self.mem
        m.set8(0x8F1, 0)
        m.set16(FLAG_TABLE + 2 * 105, 0)
        m.set16(0x70E, 0)
        d0 = 0
        if stsim.s8(m.u8(rec + 1)) >= 8:
            m.set16(0x70E, 1)
            d0 = m.u32(stunits.VIEWED)
        m.set32(CONTROLLED, d0)
        self._icon(80, m.u8(rec) & 0x20)
        self._vehicle_icons(rec, m.u16(0x70E), 0x18)
        self._set_gauges(0x954, 0x958, 0x962)
        m.set16(0x984, 1)

    def manta_view_update(self, rec: int) -> None:
        """$13EBA: as walrus_view_update for the Manta ($70C, weapon +$1A)."""
        m = self.mem
        m.set8(0x8F1, 0)
        m.set16(FLAG_TABLE + 2 * 105, 0)
        m.set16(0x70C, 0)
        d0 = 0
        if stsim.s8(m.u8(rec + 1)) >= 8:
            m.set16(0x70C, 1)
            d0 = m.u32(stunits.VIEWED)
        m.set32(CONTROLLED, d0)
        self._vehicle_icons(rec, m.u16(0x70C), 0x1A)
        self._set_gauges(0x954, 0x956, 0x958, 0x962)
        m.set16(0x984, 1)

    def enter_walrus_dock(self) -> None:
        """$13D08 (screen 19): the records of the dock selection [$DE4]; the carrier view,
        object 0 the camera 50 up (its island buffer pointer kept), object 1 off."""
        m = self.mem
        k = m.u16(0xDE4)
        m.set32(0x56C, self._bay_object(0x5E45A, k))
        m.set32(0x568, self._bay_record(0x570, k))
        m.set8(0xA46, 0)
        m.set8(0xA47, 0)
        m.set16(self.obj(1), 0)
        cam = self._carrier_view()
        m.set16(cam, 0)
        m.set16(cam + 4, 0)
        m.set16(cam + 8, 0)
        m.set32(cam + 0xE, 0x32)
        m.set16(0x95C, 2)
        m.set32(VIEWPORT, 0x4744)

    def enter_manta_arming(self) -> None:
        """$13D82 (screen 24): the carrier view, object 0 the camera 240 up; the Manta
        record $48E of the arming bay [$DB4], sub-panel $DC0."""
        m = self.mem
        cam = self._carrier_view()
        m.set16(cam, 0)
        m.set32(cam + 0xE, 0xF0)
        m.set32(cam + 0x3C, m.u32(stunits.CARRIER + 0x3C))
        rec = self._bay_record(0x496, m.u16(0xDB4))
        m.set32(0x48E, rec)
        m.set16(0xDC0, self._subpanel(rec))
        m.set16(0x95E, 0)
        m.set16(0x95C, 2)
        m.set32(VIEWPORT, 0x475A)

    def enter_manta_map(self) -> None:
        """$13AD2 (screen 25): the selected Manta's records, icons 91/92 from $6AA/$6AC,
        the view $758 from its copy $788."""
        m = self.mem
        self.select_manta()
        self._icon(91, m.u16(0x6AA))
        self._icon(92, m.u8(0x6AC))
        self._map_view_from(0x788)

    def enter_manta_deck(self) -> None:
        """$13DEE (screen 26): the records of the deck selection [$DCC]; the carrier view,
        object 0 the camera 230 up (island buffer pointer kept), object 1 off."""
        m = self.mem
        k = m.u16(0xDCC)
        m.set32(0x492, self._bay_object(0x5DF5A, k))
        m.set32(0x48E, self._bay_record(0x496, k))
        m.set8(0xA46, 0)
        m.set8(0xA47, 0)
        m.set16(self.obj(1), 0)
        cam = self._carrier_view()
        m.set16(cam, 0)
        m.set16(cam + 4, 0)
        m.set16(cam + 8, 0)
        m.set32(cam + 0xE, 0xE6)
        m.set16(0x95C, 2)
        m.set32(VIEWPORT, 0x4744)

    def enter_manta_control(self) -> None:
        """$13E68 (screen 27): camera = viewed = the selected Manta (made invisible), camera
        mode 0, object 1 off, $1230 set; controllable / controlled via manta_view_update.
        Not controllable: pointer mode. Missile lock timer $116C = 0."""
        m = self.mem
        m.set8(0xA46, 0)
        m.set8(0xA47, 0)
        m.set8(0x1230, 0xFF)
        m.set16(self.obj(1), 0)
        rec, o = self.select_manta()
        m.set32(CAMERA, o)
        m.set32(stunits.VIEWED, o)
        m.set16(CAM_MODE, 0)
        m.set8(o + 1, m.u8(o + 1) & 0xFD)
        m.set32(VIEWPORT, 0x4744)
        self.manta_view_update(rec)
        if not m.u16(0x70C):
            m.set8(stunits.POINTER_MODE, 0xFF)
        m.set16(0x116C, 0)

    def enter_game_status(self) -> None:
        """$13AFA (screen 32): from a tab click ($716 >= 0) the menu ($708 = 0), the lit
        button for [$708]; the simulation stops ($461 set: game_frame skips it, the game
        clock stops, PAUSE does nothing). Sound off ($20716) left out."""
        m = self.mem
        if not m.u16(PENDING_TAB) & 0x8000:
            m.set16(GAME_STATUS, 0)
        for t in range(4):
            a = FLAG_TABLE + 2 * (T_STATUS_MENU + t)
            m.set8(a, m.u8(a) & 0xDF)
        k = STATUS_ICON[m.u16(GAME_STATUS)] if m.u16(GAME_STATUS) < 8 else None
        if k is None:
            raise NotImplementedError(f"game-status mode {m.u16(GAME_STATUS)} (beyond the table $13B6C)")
        if k:
            a = FLAG_TABLE + 2 * (T_STATUS_MENU + k - 1)
            m.set8(a, m.u8(a) | 0x20)
        m.set32(VIEWPORT, 0x47DC)
        m.set8(MENU, 0xFF)

    def enter_title(self) -> None:
        """$13B7C (screen 33): no 3-VBL frame lock ($8F0 = 0), no world objects, the title
        model as the model list, turret elevation $6CA = 2000, object 0 the camera; the
        simulation stops ($461)."""
        m = self.mem
        m.set8(0x8F0, 0)
        m.set8(0xA46, 0xFF)
        m.set8(0xA47, 0xFF)
        a = self._model_list(1)
        m.set16(a, 3)
        m.set16(a + 2, 1)
        for o in (4, 8, 0xA, 0xC, 0xE):
            m.set16(a + o, 0)
        m.set16(0x6CA, 0x7D0)
        self._camera_at_list()
        m.set32(VIEWPORT, 0x47C8)
        m.set8(MENU, 0xFF)
        m.set16(0x93C, m.u16(0x93A))

    def enter_game_over(self) -> None:
        """$1424A (screen 34): the pointer reset ($F712), pointer mode, the simulation
        stopped; and at once the title requested ($4272: tab 5, $708 = 0), which the next
        ui_frame takes through leave_game_over (the key wait)."""
        m = self.mem
        self.pointer_reset()
        m.set8(stunits.POINTER_MODE, 0xFF)
        m.set8(MENU, 0xFF)
        m.set8(SCREEN_REQUEST, 0xFF)
        m.set16(REQUEST_TAB, 5)
        m.set16(GAME_STATUS, 0)

    def enter_destroyed(self) -> None:
        """$14266 (screen 35): the pointer reset, pointer mode, the simulation stopped."""
        m = self.mem
        self.pointer_reset()
        m.set8(stunits.POINTER_MODE, 0xFF)
        m.set8(MENU, 0xFF)

    # ---- screen leave handlers ($14318) ----------------------------------------------------
    def _set_visible(self, o: int) -> None:
        self.mem.set8(o + 1, self.mem.u8(o + 1) | 2)

    def leave_bridge(self) -> None:
        """$143D4 (0), $14464 (24): the carrier visible again, $1230 = 0."""
        self._set_visible(stunits.CARRIER)
        self.mem.set8(0x1230, 0)

    def leave_damage(self) -> None:
        """$14414 (1): object 0's flags cleared."""
        self.mem.set16(self.obj(0), 0)

    def _map_view_to(self, copy: int) -> None:
        self.mem.buf[copy:copy + 0x10] = self.mem.buf[MAP_ZOOM_LEVEL:MAP_ZOOM_LEVEL + 0x10]

    def leave_carrier_map(self) -> None:
        """$143A8 (2): the map view back to its copy $768."""
        self._map_view_to(0x768)

    def leave_walrus_map(self) -> None:
        """$143AE (17): $778."""
        self._map_view_to(0x778)

    def leave_manta_map(self) -> None:
        """$143B4 (25): $788."""
        self._map_view_to(0x788)

    def leave_turret(self) -> None:
        """$1441C (8): the turret view offsets cleared."""
        self.mem.set32(0xA94, 0)
        self.mem.set32(0xA98, 0)

    def leave_drone_view(self) -> None:
        """$14428 (10): the drone visible again."""
        self._set_visible(self.obj(15))

    def leave_walrus_arming(self) -> None:
        """$143E2 (16): object 0 byte 1 bit 0 cleared, the carrier visible."""
        o = self.obj(0)
        self.mem.set8(o + 1, self.mem.u8(o + 1) & 0xFE)
        self._set_visible(stunits.CARRIER)

    def leave_vehicle_control(self) -> None:
        """$14404 (18), $143F4 (27): the viewed vehicle [$9FE] visible again, $1230 = 0."""
        self._set_visible(self.mem.u32(stunits.VIEWED))
        self.mem.set8(0x1230, 0)

    def leave_game_status(self) -> None:
        """$143CA (32): the simulation runs again ($461 = 0), $706 = 0."""
        self.mem.set8(MENU, 0)
        self.mem.set16(0x706, 0)

    def leave_title(self) -> None:
        """$14432 (33): the 3-VBL frame lock back on."""
        self.mem.set8(0x8F0, 0xFF)

    def leave_game_over(self) -> None:
        """$14438 (34): a tune if the carrier was destroyed ([$466] = 1), then the original
        waits for a key ($889); the UI does that before the next ui_frame. The left-button
        press is consumed. The tune is the event ("sound", "game over") (`lea $4B72E,a0;
        jsr $2019A`, ungated)."""
        if self.mem.u16(RESULT) == 1:
            self.events.append(("sound", "game over"))
        self._bclr0(0x881)

    def leave_destroyed(self) -> None:
        """$1445E (35): the simulation runs again."""
        self.mem.set8(MENU, 0)

    # ---- pause and the game flow ----------------------------------------------------------
    def pause(self) -> None:
        """$FD84: stop the simulation and the game clock."""
        m = self.mem
        m.set8(RUNNING, 0)
        m.set8(0x1231, 0)
        m.set8(LAST_CHAR, 0xFF)

    def resume(self) -> None:
        """$FD98: run again; the keyboard and mouse state is cleared ($17EAE, $17EC6)."""
        m = self.mem
        a = FLAG_TABLE + 2 * T_PAUSE
        m.set8(a, m.u8(a) & 0xDF)
        m.set8(RUNNING, 0xFF)
        m.set8(0x1231, 0xFF)
        m.buf[KEYS:0x884] = bytes(0x884 - KEYS)
        m.set8(0x888, 0)
        m.set8(LAST_CHAR, 0xFF)
        m.set32(stunits.MOUSE_DX, 0)
        m.set32(0x880, 0)

    def cmd_pause(self) -> bool:
        """$FD66: PAUSE toggles the game (not while the game-status menu is open)."""
        m = self.mem
        if not self._clickable(T_PAUSE):
            return False
        if m.u8(MENU):
            return True
        if m.u8(RUNNING):
            a = FLAG_TABLE + 2 * T_PAUSE
            m.set8(a, m.u8(a) | 0x20)
            self.pause()
        else:
            self.resume()
        return True

    def misc_keys(self) -> None:
        """$4194: Control-Q resumes, Control-S pauses; Alt-Help prints a message."""
        m = self.mem
        if not m.u8(MENU):
            if self._bclr0(0x7B9):
                if m.u8(0x7D2):
                    ctrl = m.u16(0x7D2)
                    self.resume()
                    m.set16(0x7D2, ctrl)
            elif self._bclr0(0x7D7):
                if m.u8(0x7D2):
                    self.pause()
        if m.u8(0x808) and self._bclr0(0x85D):
            self._message("Yes, I have kicked out the OS")

    def paused_frame(self) -> None:
        """$44EE: while paused, typing the cheat code ("GROW OLD ALONG WITH ME", stored
        inverted at $1172) turns on debug mode $1189 and resumes. In debug mode keypad keys
        refuel ($873), heal ($875) or move units to their waypoints ($877)."""
        m = self.mem
        if not m.u8(DEBUG):
            c = m.u8(LAST_CHAR)
            if c & 0x80:
                return
            m.set8(LAST_CHAR, 0xFF)
            p = m.u32(CHEAT_PTR)
            if (~c & 0xFF) != m.u8(p):
                m.set32(CHEAT_PTR, CHEAT_CODE)
                return
            p += 1
            if m.u8(p):
                m.set32(CHEAT_PTR, p)
                return
            m.set8(DEBUG, 0xFF)
            self.resume()
            self._message("Cheat Mode active")
            return
        if self._bclr0(0x873):
            for k in range(4):
                m.set16(0x496 + 0x34 * k + 0x0A, 0x7FFF)
                m.set16(0x570 + 0x34 * k + 0x08, 0x7FFF)
            return
        if self._bclr0(0x875):
            for k in range(4):
                m.set16(0x496 + 0x34 * k + 0x14, 0xFA0)
                m.set16(0x570 + 0x34 * k + 0x14, 0xFA0)
            return
        if self._bclr0(0x877):
            self._debug_teleport()
            return
        if m.u8(0x7D2):
            if any(m.u8(0x80F + 2 * k) for k in range(10)):
                raise NotImplementedError("debug screen dump to disk ($4672: Control + F-key while paused)")
            return
        m.set32(CHEAT_PTR, CHEAT_CODE)

    def _debug_teleport(self) -> None:
        """$45B8: units flying or driving to a waypoint jump there."""
        m = self.mem
        for k in range(4):
            r, o = 0x496 + 0x34 * k, stunits.OBJECTS + stunits.OBJECT_SIZE * (2 + k)
            if m.u8(r) & 3 == 3 and m.u8(r + 0x1C) == 3:
                m.buf[o + 0x0A:o + 0x16] = m.buf[r + 0x1E:r + 0x2A]
        for k in range(4):
            r, o = 0x570 + 0x34 * k, stunits.OBJECTS + stunits.OBJECT_SIZE * (6 + k)
            if m.u8(r) & 3 == 3 and m.u8(r + 0x1A) == 3:
                m.set32(o + 0x0A, m.u32(r + 0x1C))
                m.set32(o + 0x12, m.u32(r + 0x20))
        if m.u8(stunits.C_FLAGS) & 3 == 3 and m.u8(stunits.C_SUBSTATE):
            m.set32(stunits.CARRIER + 0x0A, m.u32(stunits.C_WAYPOINT_X))
            m.set32(stunits.CARRIER + 0x12, m.u32(stunits.C_WAYPOINT_Z))

    def debug_keys(self) -> None:
        """$46E8: in debug mode keypad + / - switch the cheat $118A (invulnerable Manta, no
        stall) and keypad 9 ($86B) shows the difficulty."""
        m = self.mem
        if not m.u8(DEBUG):
            return
        if self._bclr0(0x835):
            m.set8(CHEAT, 0xFF)
        if self._bclr0(0x82D):
            m.set8(CHEAT, 0)
        if self._bclr0(0x86B):
            d, digits = m.u16(0x444), ""
            for div in (1000, 100, 10, 1):                # $9FAA: digits by repeated subtraction
                n = 0
                while d >= div:
                    d -= div
                    n += 1
                digits += chr(0x30 + n)
            self._message(f"Difficulty level is {digits}")

    def screen_input(self) -> None:
        """$17094: the current screen's input handler ($170D4), then the edge flags of the
        right button and the keys the handlers use are cleared."""
        m = self.mem
        screen = m.u16(stunits.SCREEN)
        handler = SCREEN_INPUT.get(screen)
        if handler is None:
            raise NotImplementedError(f"screen input for screen {screen} (${INPUT_TABLE.get(screen, 0):05X})")
        if handler:
            getattr(self, handler)()
        for a in EDGE_FLAGS:
            m.set8(a, 0)

    def bridge_input(self) -> None:
        """$17164 (screen 0): gauge timers $954-$962 (redraws), the auto button follows
        $640 bit 0, a right click toggles mouse-steer mode unless the autopilot is on;
        Insert / Clr-Home zoom the view (display only) and Control-V shows the version."""
        m = self.mem
        for t in (0x954, 0x956, 0x958, 0x962):
            if m.u16(t):
                m.set16(t, m.u16(t) - 1)
        selected = m.u8(FLAG_TABLE + 2 * T_AUTOPILOT) & 0x20
        if not m.u8(stunits.C_FLAGS) & 1:
            if selected:
                self._sync_auto_button()
            if self._bclr0(0x883):
                m.set8(stunits.POINTER_MODE, ~m.u8(stunits.POINTER_MODE))
        elif not selected:
            self._sync_auto_button()
        if self._bclr0(0x83D):                             # zoom $8B4/$8B2 (renderer)
            self.bridge_zoom(1)
        if self._bclr0(0x827):
            self.bridge_zoom(-1)
        if self._bclr0(0x7F7) and self._bclr0(0x7D3):
            self._message("Atari Bundle Version    16/02/90")

    def damage_input(self) -> None:
        """$1747A (screen 1): the diagram (model list entry 0) turns by $100; with the lift
        shown ($B58 = 8) its parts are highlighted in turn ($B5A 0-7, the visible bits from
        $64D50); the damage message timer $952 counts down (redraw)."""
        m = self.mem
        m.set16(MODEL_LIST + 0x18, m.u16(MODEL_LIST + 0x18) + 0x100)   # the diagram turns
        if m.s16(SYSTEM_SHOWN) == 8:
            parts = MODEL_LIST + 0x52                   # the highlighted part (visible bit) moves on
            k = m.s16(PART_SHOWN)
            if k != 8:
                e = parts + s16(w16((k & 0xFFFF) * 0x40))
                m.set8(e + 1, m.u8(e + 1) & 0xFD)
            k = s16(w16(k + 1))
            if k != 8:
                if k > 8:
                    k = 0
                e = parts + s16(w16((k & 0xFFFF) * 0x40))
                m.set8(e + 1, m.u8(e + 1) | 2)
            m.set16(PART_SHOWN, k)
        if m.u16(0x952):
            m.set16(0x952, m.u16(0x952) - 1)

    def map_input(self) -> None:
        """$17244 (maps, screens 2/17/25): cursor keys scroll, Insert / Clr-Home zoom (held),
        a right click recentres ($F8B4); on the Walrus and Manta maps the number keys select
        a unit ($132D6 -> $FFB8 / $FF1E)."""
        m = self.mem
        for key, f in ((0x828, self.map_scroll_up), (0x838, self.map_scroll_down),
                       (0x82E, self.map_scroll_left), (0x832, self.map_scroll_right),
                       (0x83C, self.map_zoom_in), (0x826, self.map_zoom_out)):
            if m.u8(key):
                f()
        if not m.u16(MAP_INFO) and self._bclr0(0x883):
            self.map_recentre_at_pointer()
        screen = m.u16(stunits.SCREEN)
        if screen == 0x11:
            self.key_select(T_WALRUS, self._walrus_map_select)
        elif screen == 0x19:
            self.key_select(T_MANTA, self._manta_map_select)

    def game_frame(self) -> None:
        """One game_main loop ($408A-$4190) without ui_frame $F636 (the UI calls the cmd_
        methods instead, before this), rendering and sound: Control-Q/S, then paused_frame
        while paused, or (unless the game-status menu is open, $461) the debug keys,
        screen_input and sim_pass (screen_input reads only input and UI state, so it may run
        ahead of sim_pass's win check and route relaxation); then the end-of-game timer $732
        (set to 100 when the enemy carrier is destroyed) and the end request $463. Either
        requests the game-over tab ($1227/$1228: tab 7 = screen 35, enemy carrier destroyed;
        tab 6 = screen 34, result [$466]: 0 quit, 1 carrier destroyed, 2/3 enemy/player holds
        every island) and records ("game over", screen, result); the app then stops."""
        m = self.mem
        self.misc_keys()
        if not m.u8(RUNNING):
            self.paused_frame()
        elif not m.u8(MENU):
            self.debug_keys()
            self.screen_input()
            self.sim_pass()
        if m.u16(END_TIMER):
            m.set16(END_TIMER, m.u16(END_TIMER) - 1)
            if not m.u16(END_TIMER):
                m.set16(END_TAB, m.u16(TAB))
                m.set16(REQUEST_TAB, 7)
                m.set8(SCREEN_REQUEST, 0xFF)
                self.events.append(("game over", 35, m.u16(RESULT)))
        if m.u8(END_REQUEST):
            m.set8(END_REQUEST, 0)
            m.set8(SCREEN_REQUEST, 0xFF)
            m.set16(REQUEST_TAB, 6)
            self.events.append(("game over", 34, m.u16(RESULT)))

    # ---- Mantas and Walruses: shared helpers (milestone 2) ------------------------------------
    def _unit_message(self, kind: str, bay: int, text: str) -> None:
        """$A044 / $A02A: "Manta N " / "Walrus N " + text (N = bay + 1 as a digit byte)."""
        self._message(f"{kind} {chr((bay + 0x31) & 0xFF)} {text}")

    def _flag(self, etype: int) -> int:
        """The flag word of an element type, as the handlers address it (adda.w 2 * type)."""
        return FLAG_TABLE + s16(w16(2 * etype))

    def _icon_set(self, etype: int) -> bool:
        """bset #5 on the type's flag byte; True if it was set already."""
        a = self._flag(etype)
        v = self.mem.u8(a)
        self.mem.set8(a, v | 0x20)
        return bool(v & 0x20)

    def _icon_clear(self, etype: int) -> None:
        a = self._flag(etype)
        self.mem.set8(a, self.mem.u8(a) & 0xDF)

    def key_select(self, base: int, handler) -> None:
        """$132D6: in pointer mode and not dragging, the first of 1-4 / keypad 1-4 with its
        new flag set (cleared here) selects unit i: the handler runs as a fresh click on a
        fake element at $13352 (type base + i, index i)."""
        m = self.mem
        if not m.u8(stunits.POINTER_MODE) or m.u8(MODAL):
            return
        for i in range(4):
            if self._bclr0(MAP_KEYPAD[2 * i]) or self._bclr0(MAP_KEYPAD[2 * i + 1]):
                break
        else:
            return
        m.set16(KEY_ELEMENT + 2, i)
        m.set16(KEY_ELEMENT, w16(i + base))
        handler(w16(i + base), i)

    def _manta_rec(self) -> int:
        return self.mem.u32(0x48E)

    def _walrus_rec(self) -> int:
        return self.mem.u32(0x568)

    def _manta_icons(self) -> None:
        """$13FBA with a2 = [$48E]."""
        self._vehicle_icons(self._manta_rec(), self.mem.u16(0x70C), 0x1A)

    def _walrus_icons(self) -> None:
        """$14004 with a2 = [$568]."""
        self._vehicle_icons(self._walrus_rec(), self.mem.u16(0x70E), 0x18)

    def _turret_icon(self) -> None:
        """$13FA6: icon 80 (the rudder turns the turret) = Walrus [$568] bit 5."""
        self._icon(T_TURRET, self.mem.u8(self._walrus_rec()) & 0x20)

    def _slider(self, y: int, rng, etype: int = T_BUTTON) -> bool:
        return rng[0] <= y <= rng[1] and self._clickable(etype)

    # ---- Manta control, screen 27 (input $172BC; panels $4229A, $423C6, $42386, $42346, $42306, $42446)
    def _manta_select(self, etype: int, i: int) -> None:
        """$FE54: select Manta i in place: the old icon off and the old viewed object
        [$492] visible again; $48C = i, its records ($141FE); camera and viewed = it
        (invisible), camera mode 0; controllable / controlled and icons ($13EBA)."""
        m = self.mem
        if self._icon_set(etype):
            return
        self._icon_clear(m.u16(0x48C) + T_MANTA)
        self._set_visible(m.u32(0x492))
        m.set16(0x48C, i)
        rec, o = self.select_manta()
        m.set32(stunits.VIEWED, o)
        m.set16(CAM_MODE, 0)
        m.set32(CAMERA, o)
        m.set8(o + 1, m.u8(o + 1) & 0xFD)
        self.manta_view_update(rec)

    def cmd_manta_select(self, bay: int) -> bool:
        """$FE54, the Manta icons 1-4 (types 58-61) on the control screen 27 (the number keys
        do the same in pointer mode): fly Manta `bay` (0-3) from now on."""
        if not (0 <= bay <= 3 and self._clickable(T_MANTA + bay)):
            return False
        self._manta_select(T_MANTA + bay, bay)
        return True

    def cmd_manta_weapon(self, w: int) -> bool:
        """$FEE2 (types 62-64): weapon +$1A = 0 laser, 1 Assassin missiles, 2 payload (bomb
        or comms pod), unless that icon is lit already; icons ($13FBA)."""
        m = self.mem
        if not (0 <= w <= 2 and self._clickable(T_WEAPON + w)):
            return False
        if not m.u8(self._flag(T_WEAPON + w)) & 0x20:
            m.set16(self._manta_rec() + 0x1A, w)
            self._manta_icons()
        return True

    def cmd_manta_autopilot(self) -> bool:
        """$12A44 (auto button, type 90): needs a controllable Manta ($70C) that isn't
        crashing (bit 4). Off: automatic (bit 0), mode 3 (fly to the waypoint): "Autopilot on"
        or, without a waypoint, "Autopilot on - set course". On: in mode 3 off; in the
        take-off modes 2/6 only +$33 is set (deferred); other modes (landing) nothing. Then
        the self-centre bits 6/7 are cleared."""
        m = self.mem
        if not self._clickable(T_AUTOPILOT):
            return False
        if not m.u16(0x70C):
            return True
        a2 = self._manta_rec()
        if m.u8(a2) & 0x10:
            return True
        a0 = self._flag(T_AUTOPILOT)
        if not m.u8(a0) & 0x20:
            m.set8(a0, m.u8(a0) | 0x20)
            m.set8(a2, m.u8(a2) | 1)
            m.set8(a2 + 0x1C, 3)
            self._message("Autopilot on" if m.u8(a2) & 2 else "Autopilot on - set course")
        else:
            mode = m.u8(a2 + 0x1C)
            if mode in (2, 6):
                m.set8(a2 + 0x33, 0xFF)
                return True
            if mode != 3:
                return True
            m.set8(a0, m.u8(a0) & 0xDF)
            m.set8(a2, m.u8(a2) & 0xFE)
        m.set8(a2, m.u8(a2) & 0x3F)
        self._manta_icons()
        return True

    def cmd_manta_centre_rudder(self) -> bool:
        """$129E6: manual flight: +0 bit 7, the rudder self-centres."""
        return self._manta_bit(0x80)

    def cmd_manta_centre_pitch(self) -> bool:
        """$129FE: manual flight: +0 bit 6, the pitch self-centres."""
        return self._manta_bit(0x40)

    def _manta_bit(self, bit: int) -> bool:
        m = self.mem
        if not self._clickable(T_BUTTON):
            return False
        a2 = self._manta_rec()
        if not m.u8(a2) & 1:
            m.set8(a2, m.u8(a2) | bit)
        return True

    def cmd_manta_instruments(self) -> bool:
        """$100A8: the instrument panel's page +$18 = (+1) mod 3 (display)."""
        return self._instrument_page(self._manta_rec() + 0x18)

    def _instrument_page(self, a: int) -> bool:
        m = self.mem
        if not self._clickable(T_BUTTON):
            return False
        v = s16(m.u16(a) + 1)
        m.set16(a, 0 if v > 2 else v)
        m.set16(0x962, 2)
        m.set16(0x984, 1)
        return True

    def cmd_manta_throttle(self, y: int) -> bool:
        """$FDDA, the throttle slider (x 288, y 158..197; repeats while held): manual flight:
        target speed +8 = (195 - y) * 7, clamped to 0..250."""
        m = self.mem
        if not self._slider(y, VEHICLE_THROTTLE_Y):
            return False
        a0 = self._manta_rec()
        if not m.u8(a0) & 1:
            v = s16(w16(s16(w16(0xC3 - y)) * 7))
            m.set16(a0 + 8, 0xFA if v > 0xFA else 0 if v < 0 else v)
        return True

    def cmd_manta_sight(self) -> bool:
        """$10100 (type 104): controllable: toggle +$32 (the sight overlay *(guess)*), icons."""
        m = self.mem
        if not self._clickable(T_SIGHT):
            return False
        if m.u16(0x70C):
            a2 = self._manta_rec()
            m.set8(a2 + 0x32, ~m.u8(a2 + 0x32))
            self._manta_icons()
        return True

    def cmd_manta_rear_view(self) -> bool:
        """$1015C (type 105): controllable: toggle the rear view $8F1 (no firing while set)."""
        return self._rear_view(0x70C)

    def _rear_view(self, controllable: int) -> bool:
        m = self.mem
        if not self._clickable(T_REAR):
            return False
        if m.u16(controllable):
            m.set16(self._flag(T_REAR), 0)
            m.set8(0x8F1, ~m.u8(0x8F1))
            if m.u8(0x8F1):
                m.set16(self._flag(T_REAR), 0x2000)
        return True

    def cmd_manta_runway_take_off(self) -> bool:
        """$13356 (135,117): a Manta landed on a runway (automatic, mode 5) is ordered to
        take off (+0 bit 5)."""
        m = self.mem
        if not self._clickable(T_BUTTON):
            return False
        a0 = self._manta_rec()
        if m.u8(a0) & 1 and m.u8(a0 + 0x1C) == 5:
            m.set8(a0, m.u8(a0) | 0x20)
        return True

    def cmd_manta_steer_to(self, rudder: int, pitch: int) -> None:
        """Mouse-steer input that makes the next manta_step set the selected Manta's rudder
        +2 and pitch +4 (each clamped to +-255): the mouse deltas are the differences. It
        applies when that Manta is the viewed one, manual, in mouse-steer mode ($457 clear)."""
        m = self.mem
        a = self._manta_rec()
        m.set16(stunits.MOUSE_DX, w16(max(-255, min(255, rudder)) - m.s16(a + 2)))
        m.set16(stunits.MOUSE_DX + 2, w16(max(-255, min(255, pitch)) - m.s16(a + 4)))

    def cmd_vehicle_fire(self) -> None:
        """A left click in mouse-steer mode: the new-press flag $881 that the viewed Manta or
        Walrus fires with on its next step (its selected weapon; not in the rear view)."""
        self.input_mouse_button(0, 1)
        self.input_mouse_button(0, 0)

    def manta_control_input(self) -> None:
        """$172BC (screen 27): the Assassin lock timer $116C counts down (only here); the
        number keys select a Manta ($FE54); the screen is re-entered ($71A = 27) when the
        Manta's status crosses 8 (controllable $70C); gauge timers; automatic: the auto button
        follows bit 0; manual: a right click toggles mouse-steer mode."""
        m = self.mem
        if m.u16(0x116C):
            m.set16(0x116C, m.u16(0x116C) - 1)
        self.key_select(T_MANTA, self._manta_select)
        if self._vehicle_reenter(self._manta_rec(), 0x70C, 0x1B):
            return
        for t in (0x954, 0x956, 0x958, 0x962):
            if m.u16(t):
                m.set16(t, m.u16(t) - 1)
        a2 = self._manta_rec()
        if m.u8(a2) & 1:
            if not m.u8(self._flag(T_AUTOPILOT)) & 0x20:
                self._manta_icons()
            return
        if self._bclr0(0x883):
            m.set8(stunits.POINTER_MODE, ~m.u8(stunits.POINTER_MODE))
        if m.u8(self._flag(T_AUTOPILOT)) & 0x20:
            self._manta_icons()

    def _vehicle_reenter(self, a0: int, controllable: int, screen: int) -> bool:
        """The control screens re-enter themselves when the unit's status (signed) crosses 8
        relative to the controllable flag. True when the handler stops there."""
        m = self.mem
        out = stsim.s8(m.u8(a0 + 1)) >= 8
        if not m.u16(controllable):
            if out:
                m.set16(SCREEN_SELECT, screen)
            return True
        if not out:
            m.set16(SCREEN_SELECT, screen)
            return True
        return False

    # ---- Manta deck, screen 26 (input $1773C; panel $42B40) ---------------------------------
    def _manta_deck_select(self, etype: int, i: int) -> None:
        """$11F92: the deck screen's own selection $DCC (records $48E/$492; $48C kept)."""
        m = self.mem
        d0 = w16(m.u16(0xDCC) + T_MANTA_DECK)
        if etype == d0:
            return
        self._icon_clear(d0)
        self._icon_set(etype)
        k = w16(etype - T_MANTA_DECK)
        m.set16(0xDCC, k)
        m.set32(0x492, self._bay_object(MANTA_OBJS, k))
        m.set32(0x48E, self._bay_record(MANTA_RECS, k))
        m.set16(0x95C, 2)

    def cmd_manta_deck_select(self, bay: int) -> bool:
        """$11F92 (types 86-89): the Manta the deck commands act on (to deck, land)."""
        if not (0 <= bay <= 3 and self._clickable(T_MANTA_DECK + bay)):
            return False
        self._manta_deck_select(T_MANTA_DECK + bay, bay)
        return True

    def cmd_manta_abort_landing(self) -> bool:
        """$12360 (144,155): abort the landing of Manta [$DDC] while it is still in the
        approach (step +$1D <= 3): it flies on (mode 3); without a waypoint it gets one ahead
        of the carrier ($1207A)."""
        m = self.mem
        if not self._clickable(T_BUTTON):
            return False
        if not m.u8(0x1232):
            self._message("No aircraft is currently landing")
            return True
        d1 = m.u16(0xDDC)
        a0 = self._bay_record(MANTA_RECS, d1)
        if m.u8(a0 + 0x1D) > 3:
            self._message("Too late to abort landing")
            return True
        m.set8(0x1232, 0)
        m.set8(a0 + 0x1C, 3)
        m.set8(a0 + 0x1D, 0)
        if not m.u8(a0) & 2:
            m.set8(a0, (m.u8(a0) | 2) & 0xFB)
            self._manta_waypoint_ahead(a0, d1)
        m.set16(0x95C, 2)
        self._unit_message("Manta", m.u16(0xDDC), "- Landing aborted")
        return True

    def _manta_waypoint_ahead(self, a0: int, d1: int) -> None:
        """$1207A: cruise 60, height (bay + 1) * 300 + 500, 6000 ahead of the carrier."""
        m, c = self.mem, stunits.CARRIER
        m.set16(a0 + 0x2A, 0x3C)
        m.set32(a0 + 0x22, s16(w16(w16(d1 + 1) * 0x12C + 0x1F4)))
        self.set_matrix(m.u16(c + 6))
        x, z = self.matrix_rotate(0, 0xE890)
        m.set32(a0 + 0x1E, s32(x + m.u32(c + 0x0A)))
        m.set32(a0 + 0x26, s32(z + m.u32(c + 0x12)))

    def manta_deck_input(self) -> None:
        """$1773C (screen 26): the number keys select ($11F92)."""
        self.key_select(T_MANTA_DECK, self._manta_deck_select)

    # ---- Manta map, screen 25 (panels $41B50, $41CD6) ---------------------------------------
    def _manta_map_select(self, etype: int, i: int) -> None:
        """$FF1E: select Manta i ($48C, records); its icon lit already: recall its course."""
        m = self.mem
        if m.u16(MAP_INFO):
            return
        if self._icon_set(etype):
            return self._manta_recall()
        self._icon_clear(m.u16(0x48C) + T_MANTA)
        m.set16(0x48C, i)
        self.select_manta()

    def cmd_manta_map_select(self, bay: int) -> bool:
        """$FF1E (types 58-61 on the Manta map): select Manta `bay`; clicking the selected
        one again recalls its course into the map's edit values ($12ECC)."""
        if not (0 <= bay <= 3 and self._clickable(T_MANTA + bay)):
            return False
        self._manta_map_select(T_MANTA + bay, bay)
        return True

    def _manta_recall(self) -> None:
        """$12ECC: the Manta's course (+$1E/+$22/+$26, cruise +$2A, announce bit 2) into the
        edit values $69E/$6A2/$6A6, $69C, $6AC; the map centred on it at zoom 0."""
        m = self.mem
        a0, bay = self._manta_rec(), m.u16(0x48C)
        if m.u8(a0 + 1) & 0x80:
            return self._unit_message("Manta", bay, "no longer exists")
        if not m.u8(a0) & 2:
            return self._unit_message("Manta", bay, "has no course to recall")
        m.set32(0x69E, m.u32(a0 + 0x1E))
        m.set32(MAP_X, m.u32(a0 + 0x1E))
        m.set32(0x6A2, m.u32(a0 + 0x22))
        m.set32(0x6A6, m.u32(a0 + 0x26))
        m.set32(MAP_Z, m.u32(a0 + 0x26))
        m.set16(0x69C, m.u16(a0 + 0x2A))
        m.set8(0x6AC, 0xFF if m.u8(a0) & 4 else 0)
        self._manta_map_icons()
        self._map_zoom_set(0)
        self._unit_message("Manta", bay, "course recalled")

    def _manta_map_icons(self) -> None:
        """$12D72: icons 91 (draw mode $6AA) and 92 (announce $6AC)."""
        m = self.mem
        self._icon(T_DRAW, m.u16(0x6AA))
        self._icon(T_ANNOUNCE, m.u8(0x6AC))

    def _walrus_map_icons(self) -> None:
        """$12E1E: icons 91 ($6B8) and 92 ($6BA)."""
        m = self.mem
        self._icon(T_DRAW, m.u16(0x6B8))
        self._icon(T_ANNOUNCE, m.u8(0x6BA))

    def cmd_manta_set_course(self, held: int = 0) -> bool:
        """$12FC4 (13,178). A fresh press gives the selected Manta the edited course: bit 1,
        bit 2 = announce $6AC, cruise +$2A = $69C, waypoint +$1E/+$22/+$26 = $69E/$6A2/$6A6
        ("course set", or "course set - engage autopilot" when manual). Held (auto-repeat),
        a Manta with a course becomes automatic ("autopilot engaged"; its mode isn't set)."""
        m = self.mem
        if not self._clickable(T_BUTTON):
            return False
        if m.u16(MAP_INFO):
            return True
        a0, bay = self._manta_rec(), m.u16(0x48C)
        d0 = m.u8(a0)
        if held:
            if not d0 & 2 or d0 & 1:
                return True
            m.set8(a0, d0 | 1)
            self._unit_message("Manta", bay, "autopilot engaged")
            return True
        if m.u8(a0 + 1) & 0x80:
            self._unit_message("Manta", bay, "no longer exists")
            return True
        d0 = (d0 | 2) & 0xFB
        if m.u8(0x6AC):
            d0 |= 4
        m.set8(a0, d0)
        m.set16(a0 + 0x2A, m.u16(0x69C))
        m.set32(a0 + 0x1E, m.u32(0x69E))
        m.set32(a0 + 0x22, m.u32(0x6A2))
        m.set32(a0 + 0x26, m.u32(0x6A6))
        self._unit_message("Manta", bay, "course set" if d0 & 1 else "course set - engage autopilot")
        return True

    def cmd_manta_clear_course(self) -> bool:
        """$12F8E (57,178): the selected Manta's course cleared (bit 1)."""
        return self._clear_course(self._manta_rec(), "Manta", 0x48C)

    def _clear_course(self, a0: int, kind: str, sel: int) -> bool:
        m = self.mem
        if not self._clickable(T_BUTTON):
            return False
        if m.u16(MAP_INFO):
            return True
        bay = m.u16(sel)
        if m.u8(a0 + 1) & 0x80:
            self._unit_message(kind, bay, "no longer exists")
        elif m.u8(a0) & 2:
            m.set8(a0, m.u8(a0) & 0xFD)
            self._unit_message(kind, bay, "course cleared")
        else:
            self._unit_message(kind, bay, "course already clear")
        return True

    def _edit(self, a: int, v: int, lo: int, hi: int) -> None:
        v = s16(v)
        self.mem.set16(a, hi if v > hi else lo if v < lo else v)

    def _map_edit(self, y=None) -> bool:
        """The map edit buttons: clickable (sliders: y in 159..195), and no island info box."""
        if y is not None:
            ok = self._slider(y, MAP_SLIDER_Y)
        else:
            ok = self._clickable(T_BUTTON)
        return ok and not self.mem.u16(MAP_INFO)

    def cmd_manta_cruise_up(self) -> bool:
        """$12C90: the course's cruise speed $69C + 16 (50..250; repeats)."""
        if not self._clickable(T_BUTTON):
            return False
        if not self.mem.u16(MAP_INFO):
            self._edit(0x69C, self.mem.u16(0x69C) + 0x10, 0x32, 0xFA)
        return True

    def cmd_manta_cruise_down(self) -> bool:
        """$12CA2: $69C - 16."""
        if not self._clickable(T_BUTTON):
            return False
        if not self.mem.u16(MAP_INFO):
            self._edit(0x69C, self.mem.u16(0x69C) - 0x10, 0x32, 0xFA)
        return True

    def cmd_manta_cruise(self, y: int) -> bool:
        """$12CB4, the cruise slider (x 288, y 159..195): $69C = (195 - y) * 7, 50..250."""
        if not self._slider(y, MAP_SLIDER_Y):
            return False
        if not self.mem.u16(MAP_INFO):
            self._edit(0x69C, s16(w16(0xC3 - y)) * 7, 0x32, 0xFA)
        return True

    def _altitude(self, v: int) -> None:
        """$12D1C: the course altitude $6A2 (long), 250..3500."""
        v = s32(v)
        self.mem.set32(0x6A2, 0xDAC if v > 0xDAC else 0xFA if v < 0xFA else v)

    def cmd_manta_altitude_up(self) -> bool:
        """$12CE2: the course's altitude $6A2 + 230 (250..3500; repeats)."""
        if not self._clickable(T_BUTTON):
            return False
        if not self.mem.u16(MAP_INFO):
            self._altitude(self.mem.u32(0x6A2) + 0xE6)
        return True

    def cmd_manta_altitude_down(self) -> bool:
        """$12CF6: $6A2 - 230."""
        if not self._clickable(T_BUTTON):
            return False
        if not self.mem.u16(MAP_INFO):
            self._altitude(self.mem.u32(0x6A2) - 0xE6)
        return True

    def cmd_manta_altitude(self, y: int) -> bool:
        """$12D0A, the altitude slider (x 244, y 159..195): $6A2 = (195 - y) * 97."""
        if not self._slider(y, MAP_SLIDER_Y):
            return False
        if not self.mem.u16(MAP_INFO):
            self._altitude(s16(w16(0xC3 - y)) * 0x61)
        return True

    def cmd_manta_map_draw_mode(self) -> bool:
        """$12D40 (type 91): toggle $6AA (what the Manta map draws; display), icons. No
        fresh-press check: holding it toggles every pass."""
        m = self.mem
        if not self._clickable(T_DRAW):
            return False
        if not m.u16(MAP_INFO):
            m.set8(0x6AB, m.u8(0x6AB) ^ 1)
            self._manta_map_icons()
        return True

    def cmd_manta_toggle_announce(self) -> bool:
        """$12D50 (type 92): toggle $6AC, the next course's "report arrival" bit, icons."""
        m = self.mem
        if not self._clickable(T_ANNOUNCE):
            return False
        if not m.u16(MAP_INFO):
            m.set8(0x6AC, ~m.u8(0x6AC))
            self._manta_map_icons()
        return True

    def cmd_set_manta_waypoint(self, x: int, z: int) -> bool:
        """The UI's "fly to (x, z)": the Manta map cursor $69E/$6A6 at world (x, z), as a map
        click writes it, then a fresh press of set course (altitude $6A2 and cruise $69C as
        edited). Engage with cmd_manta_set_course(1) or cmd_manta_autopilot()."""
        m = self.mem
        if not (self._clickable(T_BUTTON, (MAP_INFO, 0)) and self._clickable(T_BUTTON)):
            return False
        m.set32(0x69E, s32(x))
        m.set32(0x6A6, s32(z))
        return self.cmd_manta_set_course(0)

    # ---- Manta arming, screen 24 (input $17610; panels $428A2, $42966, $429FE) ------------
    def _manta_arming_select(self, etype: int, i: int) -> None:
        """$116C6: the arming screen's selection $DB4, record $48E, sub-panel $DC0."""
        m = self.mem
        d0 = w16(m.u16(0xDB4) + T_MANTA_ARMING)
        if etype == d0:
            return
        self._icon_clear(d0)
        self._icon_set(etype)
        k = w16(etype - T_MANTA_ARMING)
        m.set16(0xDB4, k)
        rec = self._bay_record(MANTA_RECS, k)
        m.set32(0x48E, rec)
        m.set16(0xDC0, self._subpanel(rec))
        self._manta_panel()
        m.set16(0x95C, 2)

    def cmd_manta_arming_select(self, bay: int) -> bool:
        """$116C6 (types 74-77): the bay the hangar commands and loadout act on."""
        if not (0 <= bay <= 3 and self._clickable(T_MANTA_ARMING + bay)):
            return False
        self._manta_arming_select(T_MANTA_ARMING + bay, bay)
        return True

    def manta_arming_input(self) -> None:
        """$17610 (screen 24): the number keys select ($116C6)."""
        self.key_select(T_MANTA_ARMING, self._manta_arming_select)

    def cmd_manta_arming_to_deck(self) -> bool:
        """$11886 (12,178, type 78): send the arming screen's Manta ([$48E], bay [$DB4]) up
        the lift: the body of $11894 (cmd_manta_to_deck) with d1 = [$DB4]."""
        m = self.mem
        if not (self._clickable(T_HANGAR) and self._arming_rec("Manta")):
            return False
        keep = m.u16(0xDCC)
        m.set16(0xDCC, m.u16(0xDB4))
        try:
            self.cmd_manta_to_deck()
        finally:
            m.set16(0xDCC, keep)
        return True

    def _arming_rec(self, kind: str) -> bool:
        """[$48E] / [$568] is the arming bay's record ($DB4 / $DB6), as the original keeps it
        while screen 24 / 16 is shown (only $116C6 / $11DEE and the enter handlers write them
        there). The model's commands work from any screen, and other screens' selections and
        the hangar commands' bay arguments repoint these records (docs/units.md §10.6)."""
        m = self.mem
        cur, base, sel = (0x48E, MANTA_RECS, 0xDB4) if kind == "Manta" else (0x568, WALRUS_RECS, 0xDB6)
        return m.u32(cur) == self._bay_record(base, m.u16(sel))

    def _loadout_clickable(self, kind: str, etype: int = T_BUTTON) -> bool:
        """The loadout panel's elements: shown in sub-panel 0 ($DC0 / $DC2), and acting on the
        arming bay's record in the hangar (status 0). On the original's arming screen both
        always hold when sub-panel 0 is shown: it is chosen from the status ($11738), and the
        status leaves 0 only by repair (sub-panel 1) or the lift (sub-panel 2). Elsewhere the
        record may be a vehicle that is out, whose +$C/+$E/+$10 hold counts, not codes."""
        if not self._clickable(etype, (0xDC0 if kind == "Manta" else 0xDC2, 0)):
            return False
        rec = self._manta_rec() if kind == "Manta" else self._walrus_rec()
        return self._arming_rec(kind) and not self.mem.u8(rec + 1)

    def _manta_panel(self) -> None:
        """The Manta loadout panel's redraw ($B418 with $42966, callback $C580, while
        [$DC0] = 0): besides drawing, it marks the Manta [$48E] unbalanced (+$1C) when the
        wing loads differ by more than 80 ($C640, _manta_balance)."""
        m = self.mem
        if m.u16(0xDC0):
            return
        if m.u16(LOADOUT_CURSOR) >= 6:
            raise NotImplementedError(f"loadout cursor {m.u16(LOADOUT_CURSOR)} beyond the list $C770 (panel draw)")
        self._manta_balance(self._manta_rec())

    def _walrus_panel(self) -> None:
        """The Walrus loadout panel's redraw ($B418 with $427B6, callback $BFE8, while
        [$DC2] = 0): with the list on the Harbinger, its entry's weight $CB7A = 100 + 150 n
        (n = [$CB80]) and the +/- buttons (type 81) patched to the Harbinger count
        ($11CE0/$11D0C); on the ACCB to its building type ($11D1C/$11D40). The buttons are
        shown (flag 0) only for those two, else hidden ($8000)."""
        m = self.mem
        if m.u16(0xDC2):
            return
        k = m.u16(LOADOUT_CURSOR + 2)
        if k >= 5:
            raise NotImplementedError(f"loadout cursor {k} beyond the list $C242")
        code, d1 = m.u16(WALRUS_CODES + 2 * k), 0x8000
        if code == 7:
            d1 = 0
            m.set16(LOADOUT + 26 * 7, w16(m.u16(HARBINGERS) * 0x96 + 0x64))
            m.set32(PLUS_HANDLER, 0x11CE0)
            m.set32(MINUS_HANDLER, 0x11D0C)
        if code == 9:
            d1 = 0
            m.set32(PLUS_HANDLER, 0x11D1C)
            m.set32(MINUS_HANDLER, 0x11D40)
        m.set16(self._flag(T_HARBINGERS), d1)

    def _cursor_step(self, a: int, n: int, d: int) -> None:
        """$1132E/$11352, $11A30/$11A76: a list cursor + 1 (unsigned wrap at n) / - 1."""
        m = self.mem
        v = w16(m.u16(a) + d)
        if d > 0:
            v = v if v < n else 0
        elif v & 0x8000:
            v = n - 1
        m.set16(a, v)

    def cmd_manta_loadout_next(self) -> bool:
        """$1132E (142,84): the stores list cursor $DB8 + 1 (0..5: Assassin x1, x2, x3,
        Quaker, Quasar, comms pod; repeats)."""
        if not self._loadout_clickable("Manta"):
            return False
        self._cursor_step(LOADOUT_CURSOR, 6, 1)
        self._manta_panel()
        return True

    def cmd_manta_loadout_prev(self) -> bool:
        """$11352 (142,108): $DB8 - 1."""
        if not self._loadout_clickable("Manta"):
            return False
        self._cursor_step(LOADOUT_CURSOR, 6, -1)
        self._manta_panel()
        return True

    def _code(self, table: int, k: int, n: int) -> int:
        if k >= n:
            raise NotImplementedError(f"loadout cursor {k} beyond the list ${table:X}")
        return self.mem.u16(table + 2 * k)

    def _entry(self, code: int) -> int:
        """mulu.w #$1A + adda.w: the loadout entry of a code."""
        if code > 11:
            raise NotImplementedError(f"loadout code {code} beyond the table $CAC4")
        return LOADOUT + s16(w16(code * 0x1A))

    def _start_drag(self, callback: int) -> None:
        """$F73C: the drop callback $468, modal mode $460 (the pointer clip is UI)."""
        self.mem.set32(DROP, callback)
        self.mem.set8(MODAL, 0xFF)

    def cmd_manta_pick_loadout(self) -> bool:
        """$1135E (123,85): pick up the stores item at the cursor to drag it onto a slot
        ("Stores unable to supply payload" if fewer are on board than it holds). Then
        cmd_drag_release(px, py) over a slot; the next ui_frame fits it (drop $113A4)."""
        m = self.mem
        if not self._loadout_clickable("Manta"):
            return False
        a1 = self._entry(self._code(MANTA_CODES, m.u16(LOADOUT_CURSOR), 6))
        if m.u16(a1 + 6) > m.u16(m.u32(a1 + 2)):
            self._message("Stores unable to supply payload")
            return True
        self._start_drag(0x113A4)
        return True

    def cmd_manta_pick_slot(self, k: int) -> bool:
        """$114C8 (slots 0 fuselage at 218,70, 1/2 wings at 195,90 / 240,90): pick up a
        fitted item to drag it back to the stores box (drop $11500)."""
        m = self.mem
        if not (0 <= k <= 2 and self._loadout_clickable("Manta")):
            return False
        off = m.u16(0x11448 + 2 * k)
        if m.u16(self._manta_rec() + off):
            m.set32(DRAG_ELEMENT, MANTA_SLOTS[k])
            self._start_drag(0x11500)
        return True

    def _slot_at(self, slots, px: int, py: int):
        """$B52C over the slot elements: the first whose box holds the pointer."""
        for a in slots:
            k, x0, y0, x1, y1 = SLOT_ELEMENTS[a]
            if x0 <= px <= x1 and y0 <= py <= y1:
                return k
        return None

    def _dragged_slot(self) -> int:
        a = self.mem.u32(DRAG_ELEMENT)
        if a not in SLOT_ELEMENTS:
            raise NotImplementedError(f"dragged element ${a:X} is not a slot")
        return SLOT_ELEMENTS[a][0]

    def _in_stores_box(self, px: int, py: int) -> bool:
        x0, y0, x1, y1 = STORES_BOX
        return x0 <= px <= x1 and y0 <= py <= y1

    def _manta_drop_loadout(self, px: int, py: int) -> None:
        """$113A4: dropped on slot k: wings (k >= 1) take Assassins only (codes <= 2); the
        weight +$12 - old item + new must stay <= 750; the old item goes back to stock and
        the new one is taken from it."""
        m = self.mem
        k = self._slot_at(MANTA_SLOTS, px, py)
        if k is None:
            return
        a0 = self._manta_rec()
        d1 = self._code(MANTA_CODES, m.u16(LOADOUT_CURSOR), 6)
        a1 = self._entry(d1)
        if k and d1 > 2:
            return self._message("Payload cannot be wing mounted")
        off = m.u16(0x11448 + 2 * k)
        d3 = m.u16(a0 + off)
        a2 = self._entry(d3)
        d0 = w16(m.u16(a0 + 0x12) - m.u16(a2) + m.u16(a1))
        if s16(d0) > 0x2EE:
            return self._message("Payload capacity exceeded")
        m.set16(a0 + 0x12, d0)
        if d3:
            p = m.u32(a2 + 2)
            m.set16(p, m.u16(p) + m.u16(a2 + 6))
        m.set16(a0 + off, d1)
        p = m.u32(a1 + 2)
        m.set16(p, m.u16(p) - m.u16(a1 + 6))
        self._manta_panel()

    def _manta_drop_return(self, px: int, py: int) -> None:
        """$11500: dropped in the stores box: the slot emptied, its item back to stock, the
        weight reduced, "Payload returned to stores"."""
        m = self.mem
        if not self._in_stores_box(px, py):
            return
        a0 = self._manta_rec()
        off = m.u16(0x11448 + 2 * self._dragged_slot())
        d0 = m.u16(a0 + off)
        m.set16(a0 + off, 0)
        a1 = self._entry(d0)
        p = m.u32(a1 + 2)
        m.set16(p, m.u16(p) + m.u16(a1 + 6))
        m.set16(a0 + 0x12, m.u16(a0 + 0x12) - m.u16(a1))
        self._message("Payload returned to stores")
        self._manta_panel()

    def cmd_drag_release(self, px: int, py: int) -> None:
        """The end of a drag: the pointer at (px, py) and the left button released; the next
        ui_frame runs the drop ($F6EA)."""
        self._set_pointer(px, py)
        self.input_mouse_button(0, 0)

    def drag_drop(self) -> None:
        """$F6EA, ui_frame during a drag: once the button is up, modal mode ends ($F712) and
        the drop callback [$468] runs with the pointer (the hit test it repeats only marks
        the element under the pointer, UI)."""
        m = self.mem
        if m.u8(0x880):
            return
        m.set8(MODAL, 0)
        cb = m.u32(DROP)
        handler = DROPS.get(cb)
        if handler is None:
            raise NotImplementedError(f"drop callback ${cb:X} ($F6EA: jmp [$468])")
        getattr(self, handler)(m.u16(0x474), m.u16(0x476))

    def _fit(self, kind: str, slot: int, code: int) -> bool:
        """Cursor clicks until the list shows `code`, pick it up, drop it on `slot`."""
        m = self.mem
        codes, n, a = (MANTA_CODES, 6, LOADOUT_CURSOR) if kind == "Manta" else (WALRUS_CODES, 5, LOADOUT_CURSOR + 2)
        slots = MANTA_SLOTS if kind == "Manta" else WALRUS_SLOTS
        if not 0 <= slot < len(slots):
            return False
        nxt = self.cmd_manta_loadout_next if kind == "Manta" else self.cmd_walrus_loadout_next
        pick = self.cmd_manta_pick_loadout if kind == "Manta" else self.cmd_walrus_pick_loadout
        for _ in range(n + 1):
            if m.u16(a) < n and m.u16(codes + 2 * m.u16(a)) == code:
                break
            if not nxt():
                return False
        else:
            return False
        if not pick() or not m.u8(MODAL):
            return False
        _, x0, y0, x1, y1 = SLOT_ELEMENTS[slots[slot]]
        self.cmd_drag_release((x0 + x1) // 2, (y0 + y1) // 2)
        self.ui_frame()
        return True

    def _unfit(self, kind: str, slot: int) -> bool:
        m = self.mem
        pick = self.cmd_manta_pick_slot if kind == "Manta" else self.cmd_walrus_pick_slot
        if not pick(slot) or not m.u8(MODAL):
            return False
        x0, y0, x1, y1 = STORES_BOX
        self.cmd_drag_release((x0 + x1) // 2, (y0 + y1) // 2)
        self.ui_frame()
        return True

    def cmd_manta_fit(self, slot: int, code: int) -> bool:
        """Load `code` (1-3 Assassins, 4 Quaker, 5 Quasar, 10 comms pod) into slot 0
        (fuselage) or 1/2 (wings) of the arming screen's Manta as a player does it: cursor
        clicks, pick up, drop on the slot (ui_frame runs the drop). False if a click wasn't
        possible; refusals are messages."""
        return self._fit("Manta", slot, code)

    def cmd_manta_unfit(self, slot: int) -> bool:
        """Drag slot 0-2's item back to the stores box."""
        return self._unfit("Manta", slot)

    # ---- Walrus control, screen 18 (input $17374; panels $42470, $4259C, $4255C, $4251C, $424DC)
    def _walrus_select(self, etype: int, i: int) -> None:
        """$10016: select Walrus i in place: old icon off, the old object [$56C] visible;
        $566 = i, records ($14224); camera and viewed = it (invisible), camera mode 1;
        controllable / controlled and icons ($13F5A)."""
        m = self.mem
        if self._icon_set(etype):
            return
        self._icon_clear(m.u16(0x566) + T_WALRUS)
        self._set_visible(m.u32(0x56C))
        m.set16(0x566, i)
        rec, o = self.select_walrus()
        m.set32(stunits.VIEWED, o)
        m.set16(CAM_MODE, 1)
        m.set32(CAMERA, o)
        m.set8(o + 1, m.u8(o + 1) & 0xFD)
        self.walrus_view_update(rec)

    def cmd_walrus_select(self, bay: int) -> bool:
        """$10016 (types 70-73 on the control screen 18; number keys alike): drive Walrus
        `bay` from now on."""
        if not (0 <= bay <= 3 and self._clickable(T_WALRUS + bay)):
            return False
        self._walrus_select(T_WALRUS + bay, bay)
        return True

    def cmd_walrus_weapon(self, w: int) -> bool:
        """$FF7C (types 62-64): weapon +$18 = 0 Avatar laser, 1 Harbinger missile, 2 payload
        (virus bomb, ACCB, fuel pod); icons ($14004)."""
        m = self.mem
        if not (0 <= w <= 2 and self._clickable(T_WEAPON + w)):
            return False
        if not m.u8(self._flag(T_WEAPON + w)) & 0x20:
            m.set16(self._walrus_rec() + 0x18, w)
            self._walrus_icons()
        return True

    def cmd_walrus_autopilot(self) -> bool:
        """$12B00 (type 90): needs a controllable Walrus ($70E). Off: needs a course ("No
        course set"): automatic, mode 3, "Autopilot on". On: in launch mode 2 only +$33 is
        set; in mode 3 off. Then bits 5 and 7 cleared (turret mode, rudder centring)."""
        m = self.mem
        if not self._clickable(T_AUTOPILOT):
            return False
        if not m.u16(0x70E):
            return True
        a2 = self._walrus_rec()
        a0 = self._flag(T_AUTOPILOT)
        if not m.u8(a0) & 0x20:
            if not m.u8(a2) & 2:
                self._message("No course set")
                return True
            m.set8(a0, m.u8(a0) | 0x20)
            m.set8(a2, m.u8(a2) | 1)
            m.set8(a2 + 0x1A, 3)
            self._message("Autopilot on")
        else:
            mode = m.u8(a2 + 0x1A)
            if mode == 2:
                m.set8(a2 + 0x33, 0xFF)
                return True
            if mode != 3:
                return True
            m.set8(a0, m.u8(a0) & 0xDF)
            m.set8(a2, m.u8(a2) & 0xFE)
        m.set8(a2, m.u8(a2) & 0x5F)
        self._walrus_icons()
        self._turret_icon()
        return True

    def cmd_walrus_centre_rudder(self, held: int = 0) -> bool:
        """$12982 (100,155): manual: a fresh press sets bit 7 (the rudder self-centres);
        held, bit 6 and the rudder +2 = 0."""
        m = self.mem
        if not self._clickable(T_BUTTON):
            return False
        a2 = self._walrus_rec()
        if m.u8(a2) & 1:
            return True
        if held:
            m.set8(a2, m.u8(a2) | 0x40)
            m.set16(a2 + 2, 0)
        else:
            m.set8(a2, m.u8(a2) | 0x80)
        return True

    def cmd_walrus_turret_mode(self) -> bool:
        """$129A2 (type 80): controllable and manual: toggle bit 5 (the mouse turns the
        turret, not the hull); off also clears bit 6. The rudder +2 = 0, bit 7 cleared."""
        m = self.mem
        if not self._clickable(T_TURRET):
            return False
        a2 = self._walrus_rec()
        if not m.u16(0x70E) or m.u8(a2) & 1:
            return True
        f = m.u8(a2) ^ 0x20
        if not f & 0x20:
            f &= 0xBF
        m.set8(a2, f)
        self._turret_icon()
        m.set16(a2 + 2, 0)
        m.set8(a2, m.u8(a2) & 0x7F)
        return True

    def cmd_walrus_instruments(self) -> bool:
        """$100D4: the instrument page +$16 = (+1) mod 3 (display)."""
        return self._instrument_page(self._walrus_rec() + 0x16)

    def cmd_walrus_throttle(self, y: int) -> bool:
        """$FE0A, the throttle slider (x 288, y 158..197; repeats): manual: target speed
        +6 = (195 - y) * $2AA >> 8 (asr), clamped to 0..96."""
        m = self.mem
        if not self._slider(y, VEHICLE_THROTTLE_Y):
            return False
        a0 = self._walrus_rec()
        if not m.u8(a0) & 1:
            v = s16(w16(s16(w16(0xC3 - y)) * 0x2AA)) >> 8
            m.set16(a0 + 6, 0x60 if v > 0x60 else 0 if v < 0 else v)
        return True

    def cmd_walrus_sight(self) -> bool:
        """$1012E (type 104): controllable: toggle +$32, icons."""
        m = self.mem
        if not self._clickable(T_SIGHT):
            return False
        if m.u16(0x70E):
            a2 = self._walrus_rec()
            m.set8(a2 + 0x32, ~m.u8(a2 + 0x32))
            self._walrus_icons()
        return True

    def cmd_walrus_rear_view(self) -> bool:
        """$10190 (type 105): controllable: toggle the rear view $8F1."""
        return self._rear_view(0x70E)

    def cmd_walrus_steer_to(self, rudder: int) -> None:
        """Mouse-steer input that makes the next walrus_step set the selected Walrus's rudder
        +2 (clamped to +-255) when it is the viewed one, manual, in mouse-steer mode and not
        steering a missile."""
        m = self.mem
        a = self._walrus_rec()
        m.set16(stunits.MOUSE_DX, w16(max(-255, min(255, rudder)) - m.s16(a + 2)))

    def cmd_walrus_missile_steer_to(self, yaw: int, pitch: int) -> bool:
        """While the selected Walrus steers a Harbinger (bit 4), the mouse turns the missile:
        deltas that make its turn rates (missile +$1E / +$20) yaw and pitch (+-255) on the
        next walrus_step. False (nothing written) when it isn't steering one."""
        m = self.mem
        o = m.u32(m.u32(0x56C) + 0x20)
        if not m.u8(self._walrus_rec()) & 0x10 or not stunits.OBJECTS <= o < stunits.OBJECTS + 64 * stunits.OBJECT_SIZE:
            return False
        m.set16(stunits.MOUSE_DX, w16(max(-255, min(255, yaw)) - m.s16(o + 0x1E)))
        m.set16(stunits.MOUSE_DX + 2, w16(max(-255, min(255, pitch)) - m.s16(o + 0x20)))
        return True

    def walrus_control_input(self) -> None:
        """$17374 (screen 18): number keys select ($10016); re-enter when the status crosses
        8 ($70E); gauge timers; automatic: the auto button follows bit 0; manual: a right
        click toggles mouse-steer mode, except while steering a missile (bit 4)."""
        m = self.mem
        self.key_select(T_WALRUS, self._walrus_select)
        if self._vehicle_reenter(self._walrus_rec(), 0x70E, 0x12):
            return
        for t in (0x954, 0x958, 0x962):
            if m.u16(t):
                m.set16(t, m.u16(t) - 1)
        a2 = self._walrus_rec()
        if m.u8(a2) & 1:
            if not m.u8(self._flag(T_AUTOPILOT)) & 0x20:
                self._walrus_icons()
            return
        if self._bclr0(0x883):
            if m.u8(a2) & 0x10:
                return
            m.set8(stunits.POINTER_MODE, ~m.u8(stunits.POINTER_MODE))
        if m.u8(self._flag(T_AUTOPILOT)) & 0x20:
            self._walrus_icons()

    # ---- Walrus dock, screen 19 (input $17782; panel $42C1A) --------------------------------
    def _walrus_dock_select(self, etype: int, i: int) -> None:
        """$1249E: the dock screen's own selection $DE4 (records $568/$56C; $566 kept)."""
        m = self.mem
        d0 = w16(m.u16(0xDE4) + T_WALRUS_DOCK)
        if etype == d0:
            return
        self._icon_clear(d0)
        self._icon_set(etype)
        k = w16(etype - T_WALRUS_DOCK)
        m.set16(0xDE4, k)
        m.set32(0x56C, self._bay_object(WALRUS_OBJS, k))
        m.set32(0x568, self._bay_record(WALRUS_RECS, k))
        m.set16(0x95C, 2)

    def cmd_walrus_dock_select(self, bay: int) -> bool:
        """$1249E (types 93-96): the Walrus launch and dock act on."""
        if not (0 <= bay <= 3 and self._clickable(T_WALRUS_DOCK + bay)):
            return False
        self._walrus_dock_select(T_WALRUS_DOCK + bay, bay)
        return True

    def cmd_walrus_abort_docking(self) -> bool:
        """$128AE (144,155): abort Walrus [$6E0]'s docking while it is still approaching
        (mode 0, step 0): it drives on (mode 3); without a waypoint it gets one astern of the
        carrier ($12786)."""
        m = self.mem
        if not self._clickable(T_BUTTON):
            return False
        if not m.u8(0x1233):
            self._message("No AAV is currently docking")
            return True
        d0 = m.u16(0x6E0)
        a1 = self._bay_record(WALRUS_RECS, d0)
        a0 = self._bay_object(WALRUS_OBJS, d0)
        if m.u8(a1 + 0x1A):
            self._message("No AAV is currently docking")
            return True
        if m.u8(a1 + 0x1B):
            self._message("Too late to abort docking")
            return True
        m.set8(0x1233, 0)
        m.set8(a1 + 0x1A, 3)
        m.set8(a1 + 0x1B, 0)
        if not m.u8(a1) & 2:
            m.set8(a1, (m.u8(a1) | 2) & 0xFB)
            self._walrus_waypoint_astern(a0, a1)
        m.set16(0x95C, 2)
        self._unit_message("Walrus", m.u16(0x6E0), "- Docking aborted")
        return True

    def _walrus_waypoint_astern(self, a0: int, a1: int) -> None:
        """$12786: cruise 60, a waypoint astern of the carrier, by the bay's lane."""
        m, c = self.mem, stunits.CARRIER
        m.set16(a1 + 0x24, 0x3C)
        self.set_matrix(m.u16(c + 6))
        x, z = self.matrix_rotate(w16(m.u16(a0 + 0x1A) * 0x7D0 - 0xBB8), 0xF060)
        m.set32(a1 + 0x1C, s32(x + m.u32(c + 0x0A)))
        m.set32(a1 + 0x20, s32(z + m.u32(c + 0x12)))

    def walrus_dock_input(self) -> None:
        """$17782 (screen 19): the number keys select ($1249E)."""
        self.key_select(T_WALRUS_DOCK, self._walrus_dock_select)

    # ---- Walrus map, screen 17 (panels $41C3E, $41DC6) --------------------------------------
    def _walrus_map_select(self, etype: int, i: int) -> None:
        """$FFB8: select Walrus i ($566, records); selected already: recall its course."""
        m = self.mem
        if m.u16(MAP_INFO):
            return
        if self._icon_set(etype):
            return self._walrus_recall()
        self._icon_clear(m.u16(0x566) + T_WALRUS)
        m.set16(0x566, i)
        self.select_walrus()

    def cmd_walrus_map_select(self, bay: int) -> bool:
        """$FFB8 (types 70-73 on the Walrus map): select Walrus `bay`; again: recall its
        course ($12F30)."""
        if not (0 <= bay <= 3 and self._clickable(T_WALRUS + bay)):
            return False
        self._walrus_map_select(T_WALRUS + bay, bay)
        return True

    def _walrus_recall(self) -> None:
        """$12F30: the course (+$1C/+$20, cruise +$24, bit 2) into $6B0/$6B4, $6AE, $6BA."""
        m = self.mem
        a0, bay = self._walrus_rec(), m.u16(0x566)
        if m.u8(a0 + 1) & 0x80:
            return self._unit_message("Walrus", bay, "no longer exists")
        if not m.u8(a0) & 2:
            return self._unit_message("Walrus", bay, "has no course to recall")
        m.set32(0x6B0, m.u32(a0 + 0x1C))
        m.set32(MAP_X, m.u32(a0 + 0x1C))
        m.set32(0x6B4, m.u32(a0 + 0x20))
        m.set32(MAP_Z, m.u32(a0 + 0x20))
        m.set16(0x6AE, m.u16(a0 + 0x24))
        m.set8(0x6BA, 0xFF if m.u8(a0) & 4 else 0)
        self._walrus_map_icons()
        self._map_zoom_set(0)
        self._unit_message("Walrus", bay, "course recalled")

    def cmd_walrus_set_course(self, held: int = 0) -> bool:
        """$13122 (13,178): fresh: the edited course (bit 1, bit 2 = $6BA, cruise +$24 = $6AE,
        waypoint +$1C/+$20 = $6B0/$6B4); held: engage the autopilot (bit 0)."""
        m = self.mem
        if not self._clickable(T_BUTTON):
            return False
        if m.u16(MAP_INFO):
            return True
        a0, bay = self._walrus_rec(), m.u16(0x566)
        d0 = m.u8(a0)
        if held:
            if not d0 & 2 or d0 & 1:
                return True
            m.set8(a0, d0 | 1)
            self._unit_message("Walrus", bay, "autopilot engaged")
            return True
        if m.u8(a0 + 1) & 0x80:
            self._unit_message("Walrus", bay, "no longer exists")
            return True
        d0 = (d0 | 2) & 0xFB
        if m.u8(0x6BA):
            d0 |= 4
        m.set8(a0, d0)
        m.set16(a0 + 0x24, m.u16(0x6AE))
        m.set32(a0 + 0x1C, m.u32(0x6B0))
        m.set32(a0 + 0x20, m.u32(0x6B4))
        self._unit_message("Walrus", bay, "course set" if d0 & 1 else "course set - engage autopilot")
        return True

    def cmd_walrus_clear_course(self) -> bool:
        """$130EE (57,178): clear the selected Walrus's course."""
        return self._clear_course(self._walrus_rec(), "Walrus", 0x566)

    def cmd_walrus_cruise_up(self) -> bool:
        """$12D98: the course's cruise speed $6AE + 8 (0..96; repeats)."""
        if not self._clickable(T_BUTTON):
            return False
        if not self.mem.u16(MAP_INFO):
            self._edit(0x6AE, self.mem.u16(0x6AE) + 8, 0, 0x60)
        return True

    def cmd_walrus_cruise_down(self) -> bool:
        """$12DAA: $6AE - 8."""
        if not self._clickable(T_BUTTON):
            return False
        if not self.mem.u16(MAP_INFO):
            self._edit(0x6AE, self.mem.u16(0x6AE) - 8, 0, 0x60)
        return True

    def cmd_walrus_cruise(self, y: int) -> bool:
        """$12DBC, the slider (y 159..195): $6AE = (195 - y) * $2AA >> 8 (lsr.w), 0..96."""
        if not self._slider(y, MAP_SLIDER_Y):
            return False
        if not self.mem.u16(MAP_INFO):
            self._edit(0x6AE, w16(s16(w16(0xC3 - y)) * 0x2AA) >> 8, 0, 0x60)
        return True

    def cmd_walrus_map_draw_mode(self) -> bool:
        """$12DEC (type 91): toggle $6B8 (display), icons."""
        m = self.mem
        if not self._clickable(T_DRAW):
            return False
        if not m.u16(MAP_INFO):
            m.set8(0x6B9, m.u8(0x6B9) ^ 1)
            self._walrus_map_icons()
        return True

    def cmd_walrus_toggle_announce(self) -> bool:
        """$12DFC (type 92): toggle $6BA (report arrival for the next course), icons."""
        m = self.mem
        if not self._clickable(T_ANNOUNCE):
            return False
        if not m.u16(MAP_INFO):
            m.set8(0x6BA, ~m.u8(0x6BA))
            self._walrus_map_icons()
        return True

    def cmd_set_walrus_waypoint(self, x: int, z: int) -> bool:
        """The UI's "drive to (x, z)": the Walrus map cursor $6B0/$6B4 at (x, z), then a
        fresh press of set course."""
        m = self.mem
        if not (self._clickable(T_BUTTON, (MAP_INFO, 0)) and self._clickable(T_BUTTON)):
            return False
        m.set32(0x6B0, s32(x))
        m.set32(0x6B4, s32(z))
        return self.cmd_walrus_set_course(0)

    # ---- Walrus arming, screen 16 (input $17748; panels $42708, $427B6, $42864) -------------
    def _walrus_arming_select(self, etype: int, i: int) -> None:
        """$11DEE: the arming selection $DB6, record $568, sub-panel $DC2."""
        m = self.mem
        d0 = w16(m.u16(0xDB6) + T_WALRUS_ARMING)
        if etype == d0:
            return
        self._icon_clear(d0)
        self._icon_set(etype)
        k = w16(etype - T_WALRUS_ARMING)
        m.set16(0xDB6, k)
        rec = self._bay_record(WALRUS_RECS, k)
        m.set32(0x568, rec)
        m.set16(0xDC2, self._subpanel(rec))
        self._walrus_panel()
        m.set16(0x95C, 2)

    def cmd_walrus_arming_select(self, bay: int) -> bool:
        """$11DEE (types 82-85)."""
        if not (0 <= bay <= 3 and self._clickable(T_WALRUS_ARMING + bay)):
            return False
        self._walrus_arming_select(T_WALRUS_ARMING + bay, bay)
        return True

    def walrus_arming_input(self) -> None:
        """$17748 (screen 16): the number keys select ($11DEE); the hangar animation $DC8
        bounces by $DCA between $6000 and $A000."""
        m = self.mem
        self.key_select(T_WALRUS_ARMING, self._walrus_arming_select)
        d0, d1 = m.u16(0xDC8), m.u16(0xDCA)
        d0 = w16(d0 + d1)
        if not d1 & 0x8000:
            if d0 >= 0xA000:
                d0 = 0xA000
                m.set16(0xDCA, w16(-d1))
        elif d0 <= 0x6000:
            d0 = 0x6000
            m.set16(0xDCA, w16(-d1))
        m.set16(0xDC8, d0)

    def _walrus_cursor_step(self, d: int) -> None:
        """$11A30/$11A76: cursor $DBA (0..4: Avatar, Harbinger, virus bomb, ACCB, fuel pod);
        on the Harbinger, $CB80 = 1 if any are on board ($B98) else 0."""
        m = self.mem
        self._cursor_step(LOADOUT_CURSOR + 2, 5, d)
        k = m.u16(LOADOUT_CURSOR + 2)
        if k >= 5:
            raise NotImplementedError(f"loadout cursor {k} beyond the list $C242")
        if m.u16(WALRUS_CODES + 2 * k) == 7:
            m.set16(HARBINGERS, 1 if m.u16(0xB98) else 0)
        self._walrus_panel()

    def cmd_walrus_loadout_next(self) -> bool:
        """$11A30 (142,84): $DBA + 1 (repeats)."""
        if not self._loadout_clickable("Walrus"):
            return False
        self._walrus_cursor_step(1)
        return True

    def cmd_walrus_loadout_prev(self) -> bool:
        """$11A76 (142,108): $DBA - 1."""
        if not self._loadout_clickable("Walrus"):
            return False
        self._walrus_cursor_step(-1)
        return True

    def cmd_walrus_loadout_plus(self) -> bool:
        """The "+" button (type 81 at 114,86; repeats): whatever the panel draw installed
        ([$4281C]): $11CE0 the Harbingers to load $CB80 + 1 (at most the stock $B98 and 9),
        or $11D1C the ACCB's building type $DC6 + 1 (0..2)."""
        return self._plus_minus(PLUS_HANDLER)

    def cmd_walrus_loadout_minus(self) -> bool:
        """The "-" button (type 81 at 43,86): $11D0C Harbingers - 1 (at least 1) or $11D40
        the ACCB type - 1."""
        return self._plus_minus(MINUS_HANDLER)

    def _plus_minus(self, a: int) -> bool:
        m = self.mem
        if not self._loadout_clickable("Walrus", T_HARBINGERS):
            return False
        h = m.u32(a)
        if h == 0x11CE0:
            d0 = w16(m.u16(HARBINGERS) + 1)
            if d0 > m.u16(0xB98) or d0 > 9:
                return True
            m.set16(HARBINGERS, d0)
        elif h == 0x11D0C:
            d0 = m.u16(HARBINGERS)
            if d0 <= 1:
                return True
            m.set16(HARBINGERS, d0 - 1)
        elif h == 0x11D1C:
            d0 = w16(m.u16(0xDC6) + 1)
            m.set16(0xDC6, 0 if d0 == 3 else d0)
        elif h == 0x11D40:
            d0 = w16(m.u16(0xDC6) - 1)
            m.set16(0xDC6, 2 if d0 & 0x8000 else d0)
        else:
            raise NotImplementedError(f"arming +/- handler ${h:X}")
        self._walrus_panel()
        return True

    def cmd_walrus_pick_loadout(self) -> bool:
        """$11A82 (35,86): pick up the item at the cursor (none or too few on board: "Stores
        unable to supply payload"); drop $11ACE."""
        m = self.mem
        if not self._loadout_clickable("Walrus"):
            return False
        a1 = self._entry(self._code(WALRUS_CODES, m.u16(LOADOUT_CURSOR + 2), 5))
        stock = m.u16(m.u32(a1 + 2))
        if not stock or m.u16(a1 + 6) > stock:
            self._message("Stores unable to supply payload")
            return True
        self._start_drag(0x11ACE)
        return True

    def cmd_walrus_pick_slot(self, k: int) -> bool:
        """$11C16 (slot 0 payload at 173,96, slot 1 weapon at 201,96): pick up a fitted item
        to drag back to stores (drop $11C4E)."""
        m = self.mem
        if not (0 <= k <= 1 and self._loadout_clickable("Walrus")):
            return False
        off = m.u16(0x11C12 + 2 * k)
        if m.u16(self._walrus_rec() + off):
            m.set32(DRAG_ELEMENT, WALRUS_SLOTS[k])
            self._start_drag(0x11C4E)
        return True

    def _walrus_drop_loadout(self, px: int, py: int) -> None:
        """$11ACE: slot 0 (+$26) takes the payloads (entry +8 = 1: virus bomb, ACCB, fuel
        pod), slot 1 (+$28) the weapons (Avatar, Harbinger), else "Payload cannot be mounted
        here". Weight <= 2000; an old Harbinger load weighs 100 + 150 n (the entry $11BD2 is
        rewritten for it); stock exchanged. Harbingers: +$C = [$CB80] (then at most the
        stock left); ACCB: +$10 = the building type [$DC6]."""
        m = self.mem
        k = self._slot_at(WALRUS_SLOTS, px, py)
        if k is None:
            return
        a0 = self._walrus_rec()
        d1 = self._code(WALRUS_CODES, m.u16(LOADOUT_CURSOR + 2), 5)
        a1 = self._entry(d1)
        if k:
            if m.u16(a1 + 8) == 1:
                return self._message("Payload cannot be mounted here")
            off = 0x28
        else:
            if m.u16(a1 + 8) != 1:
                return self._message("Payload cannot be mounted here")
            off = 0x26
        d3 = m.u16(a0 + off)
        if d3 == 7:
            a2 = HARBINGER_LOAD
            n = m.u16(a0 + 0x0C)
            m.set16(a2 + 6, n)
            m.set16(a2, w16(n * 0x96 + 0x64))
        else:
            a2 = self._entry(d3)
        d0 = w16(m.u16(a0 + 0x12) - m.u16(a2) + m.u16(a1))
        if s16(d0) > 0x7D0:
            return self._message("Payload capacity exceeded")
        m.set16(a0 + 0x12, d0)
        if d3:
            if d3 == 7:
                m.set16(a0 + 0x0C, 0)
            p = m.u32(a2 + 2)
            m.set16(p, m.u16(p) + m.u16(a2 + 6))
        m.set16(a0 + off, d1)
        p = m.u32(a1 + 2)
        m.set16(p, m.u16(p) - m.u16(a1 + 6))
        if d1 == 7:
            d0 = m.u16(HARBINGERS)
            m.set16(a0 + 0x0C, d0)
            if d0 > m.u16(p):
                m.set16(HARBINGERS, m.u16(p))
        if d1 == 9:
            m.set16(a0 + 0x10, m.u16(0xDC6))
        self._walrus_panel()

    def _walrus_drop_return(self, px: int, py: int) -> None:
        """$11C4E: dropped in the stores box: the slot emptied, the item back to stock (a
        Harbinger load: its count +$C to $B98, weight 100 + 150 n; +$C itself is kept)."""
        m = self.mem
        if not self._in_stores_box(px, py):
            return
        a0 = self._walrus_rec()
        off = m.u16(0x11C12 + 2 * self._dragged_slot())
        d0 = m.u16(a0 + off)
        m.set16(a0 + off, 0)
        if d0 == 7:
            n = m.u16(a0 + 0x0C)
            m.set16(0xB98, m.u16(0xB98) + n)
            m.set16(a0 + 0x12, m.u16(a0 + 0x12) - w16(n * 0x96 + 0x64))
        else:
            a1 = self._entry(d0)
            p = m.u32(a1 + 2)
            m.set16(p, m.u16(p) + m.u16(a1 + 6))
            m.set16(a0 + 0x12, m.u16(a0 + 0x12) - m.u16(a1))
        self._message("Payload returned to stores")
        self._walrus_panel()

    def cmd_walrus_fit(self, slot: int, code: int) -> bool:
        """Load `code` (6 Avatar or 7 Harbingers into slot 1; 8 virus bomb, 9 ACCB, 11 fuel
        pod into slot 0) into the arming screen's Walrus as a player does it (cursor clicks,
        pick up, drop). Harbingers: stepping the cursor onto them resets the count $CB80 to
        1, so move the cursor there first, set the count (cmd_walrus_loadout_plus/minus),
        then fit (the cursor isn't moved when it shows the code already)."""
        return self._fit("Walrus", slot, code)

    def cmd_walrus_unfit(self, slot: int) -> bool:
        """Drag slot 0/1's item back to the stores box."""
        return self._unfit("Walrus", slot)

    # ---- read helpers for the UI ---------------------------------------------------------------
    def vehicle_ui(self) -> dict:
        """What the vehicle screens show that isn't obvious from the records: the selections
        (control/map Manta $48C and Walrus $566; arming $DB4/$DB6; deck $DCC; dock $DE4),
        whether the selected unit can be controlled ($70C/$70E), the loadout list cursors and
        the codes they show, the Harbingers to load ($CB80), a drag in progress, and each
        bay's telemetry and status."""
        m = self.mem
        mk, wk = m.u16(LOADOUT_CURSOR), m.u16(LOADOUT_CURSOR + 2)
        return {
            "manta": m.u16(0x48C), "walrus": m.u16(0x566),
            "manta_arming": m.u16(0xDB4), "walrus_arming": m.u16(0xDB6),
            "manta_deck": m.u16(0xDCC), "walrus_dock": m.u16(0xDE4),
            "manta_controllable": m.u16(0x70C) != 0, "walrus_controllable": m.u16(0x70E) != 0,
            "manta_loadout": m.u16(MANTA_CODES + 2 * mk) if mk < 6 else -1,
            "walrus_loadout": m.u16(WALRUS_CODES + 2 * wk) if wk < 5 else -1,
            "harbingers": m.u16(HARBINGERS),
            "dragging": m.u32(DROP) if m.u8(MODAL) else 0,
            "lock_timer": m.u16(0x116C),
            # telemetry per bay (0 OK, 1 weak, 2 lost) and status (+1: 0 ready ... 8 out, $FF destroyed)
            "manta_telemetry": [m.u16(MANTA_RECS + REC * k + 0x2E) for k in range(4)],
            "walrus_telemetry": [m.u16(WALRUS_RECS + REC * k + 0x2C) for k in range(4)],
            "manta_status": [m.u8(MANTA_RECS + REC * k + 1) for k in range(4)],
            "walrus_status": [m.u8(WALRUS_RECS + REC * k + 1) for k in range(4)],
        }

    # ---- milestone 3 "War": bridge view zoom (screen 0, panel $42672) -----------------------
    def bridge_zoom(self, d: int) -> None:
        """$FCE8 / $FCF8: the bridge view's zoom $8B4 + d (0-4), scale $8B2 = $2E8 asr zoom
        (read by the renderer only)."""
        m = self.mem
        z = s16(w16(m.u16(0x8B4) + d))
        z = 4 if z > 4 else 0 if z < 0 else z
        m.set16(0x8B4, z)
        m.set16(0x8B2, 0x2E8 >> z)

    def cmd_bridge_zoom_in(self) -> bool:
        """$FCE8 (56,155; Insert): zoom + 1."""
        if not self._clickable(T_BUTTON):
            return False
        self.bridge_zoom(1)
        return True

    def cmd_bridge_zoom_out(self) -> bool:
        """$FCF8 (56,178; Clr-Home): zoom - 1."""
        if not self._clickable(T_BUTTON):
            return False
        self.bridge_zoom(-1)
        return True

    # ---- carrier map, islands panel ([$710] = 1): the stockpile island -------------------------
    def island_name(self, i: int) -> str:
        """The name of island i ($4AD20 + its word offset)."""
        m = self.mem
        a = (ISLAND_NAMES + s16(m.u16(ISLAND_NAMES + s16(w16(2 * i))))) & 0xFFFFFFFF
        out = bytearray()
        while m.u8(a + len(out)):
            out.append(m.u8(a + len(out)))
        return out.decode("latin-1")

    def cmd_set_stockpile(self) -> bool:
        """$137C2 (screen 2, islands panel, 13,178), fresh press: the island the map is
        centred on ([$764]) becomes the stockpile island ($D4E = the old one, $D4A), and
        the goods queued or held at the old one ship there ($21D46). Refused while routes
        are being recomputed ($1235), without communications, with stocks in transit
        ($D50, unless there was no stockpile), off an island, on the stockpile already, on a
        non-player island, or one with no route from Vulcan."""
        m = self.mem
        if not self._clickable(T_BUTTON, (CARRIER_MAP_MODE, 1)):
            return False
        i = m.s16(MAP_ISLAND)
        if m.u8(stsim.ROUTES_PENDING):
            self._message("Re-routing")
        elif not m.u8(0xB0A):
            self._message("Communications Inoperative")
        elif not m.u16(stsim.STOCKPILE) & 0x8000 and m.u8(stsim.IN_TRANSIT):
            self._message("Stocks still in transit")
        elif i < 0:
            self._message("Not centred on an island")
        elif i == m.s16(stsim.STOCKPILE):
            self._message("Already the stockpile island")
        elif m.u8(stsim.OWNERS + i) != 1:
            self._message("Not a friendly island")
        elif m.u16(stsim.ROUTES + 2 * i) & 0x8000:
            self._message("Island is isolated from Base")
        else:
            m.set16(stsim.CARRIER_ISLAND, m.u16(stsim.STOCKPILE))
            m.set16(stsim.STOCKPILE, i)
            self._message(f"{self.island_name(i)} is now stockpile island")
            self.stockpile_reship()
        return True

    def stockpile_reship(self) -> None:
        """$21D46: the queued goods go back to the stockpile counts, then everything held
        there ships from the old stockpile [$D4E] to the new one: carrier-pool entries
        ($66A10, written from the first one on, unchecked) of up to 255 per item (kinds
        0-14) and fuel / 4096 (kinds 15-17; low word only); the counts are cleared and
        "in transit" $D50 is set."""
        m = self.mem
        for k in range(stsim.N_ITEMS):
            a, q = stsim.STORES + 2 * k, stsim.STORES_PENDING + 2 * k
            m.set16(a, m.u16(a) + m.u16(q))
            m.set16(q, 0)
        for k in range(3):
            a, q = stsim.FUEL + 4 * k, stsim.FUEL_PENDING + 4 * k
            m.set32(a, m.u32(a) + m.u32(q))
            m.set32(q, 0)
        e = stsim.CARRIER_POOL

        def ship(kind: int, d0: int) -> None:
            nonlocal e
            while True:
                d1 = 0xFF if d0 >= 0xFF else d0
                d0 = w16(d0 - d1)
                m.set16(e + 4, 0xFFFF)
                m.set8(e + 2, kind)
                m.set8(e + 3, d1)
                m.set8(e, m.u16(stsim.CARRIER_ISLAND))
                m.set8(e + 1, 1)
                e += 6
                if not d0:
                    return
        for k in range(stsim.N_ITEMS):
            a = stsim.STORES + 2 * k
            if m.u16(a):
                ship(k, m.u16(a))
            m.set16(a, 0)
        for k in range(3):
            a = stsim.FUEL + 4 * k
            d0 = (m.u32(a) >> 12) & 0xFFFF
            if m.u32(a) >> 12:
                ship(0xF + k, d0)
            m.set32(a, 0)
        m.set8(stsim.IN_TRANSIT, 0xFF)

    # ---- laser turret, screen 8 (input $1741A; panel $421C0) ---------------------------------
    def _turret_aim(self, a: int, sign: int) -> None:
        """$FD2C-$FD44: heading $6CC or elevation $6CA moves by 300 >> zoom [$6D2] ($FD54),
        and both aim rates ($6CE, $6D0) stop."""
        m = self.mem
        step = (0x12C >> (m.u16(0x6D2) & 63)) & 0xFFFF
        m.set16(a, m.u16(a) + sign * step)
        m.set16(0x6CE, 0)
        m.set16(0x6D0, 0)

    def turret_aim_up(self) -> None:
        """$FD3C: elevation $6CA - step (cursor up)."""
        self._turret_aim(0x6CA, -1)

    def turret_aim_down(self) -> None:
        """$FD44: elevation + step."""
        self._turret_aim(0x6CA, 1)

    def turret_aim_left(self) -> None:
        """$FD2C: heading $6CC - step."""
        self._turret_aim(0x6CC, -1)

    def turret_aim_right(self) -> None:
        """$FD34: heading + step."""
        self._turret_aim(0x6CC, 1)

    def cmd_turret_up(self) -> bool:
        """$FD3C (100,155), works while held."""
        if not self._clickable(T_BUTTON):
            return False
        self.turret_aim_up()
        return True

    def cmd_turret_down(self) -> bool:
        """$FD44 (122,178), works while held."""
        if not self._clickable(T_BUTTON):
            return False
        self.turret_aim_down()
        return True

    def cmd_turret_left(self) -> bool:
        """$FD2C (100,178), works while held."""
        if not self._clickable(T_BUTTON):
            return False
        self.turret_aim_left()
        return True

    def cmd_turret_right(self) -> bool:
        """$FD34 (122,155), works while held."""
        if not self._clickable(T_BUTTON):
            return False
        self.turret_aim_right()
        return True

    def cmd_turret_fire(self) -> bool:
        """$FD60 (117,170, between the arrows; works while held): the fire key $88C; the
        turret (carrier_laser_turret_step) fires on its next step unless it is cooling down."""
        if not self._clickable(T_BUTTON):
            return False
        self.mem.set8(0x88C, 0xFF)
        return True

    def turret_zoom(self, d: int) -> None:
        """$FC9C / $FCAC: the turret zoom $6D2 + d (0-3; a higher zoom aims in finer steps)
        and the view offsets $A94/$A98 ($FCD4)."""
        m = self.mem
        z = s16(w16(m.u16(0x6D2) + d))
        z = 3 if z > 3 else 0 if z < 0 else z
        m.set16(0x6D2, z)
        self.turret_zoom_view(z)

    def cmd_turret_zoom_in(self) -> bool:
        """$FC9C (144,155; Insert), fresh press."""
        if not self._clickable(T_BUTTON):
            return False
        self.turret_zoom(1)
        return True

    def cmd_turret_zoom_out(self) -> bool:
        """$FCAC (144,178; Clr-Home), fresh press."""
        if not self._clickable(T_BUTTON):
            return False
        self.turret_zoom(-1)
        return True

    def cmd_turret_centre_heading(self) -> bool:
        """$12A34 (166,155): $6D5 bit 7, the heading rate $6CE self-centres to 0."""
        if not self._clickable(T_BUTTON):
            return False
        self.mem.set8(0x6D5, self.mem.u8(0x6D5) | 0x80)
        return True

    def cmd_turret_centre_elevation(self) -> bool:
        """$12A3C (166,178): $6D5 bit 6, the elevation rate $6D0 self-centres."""
        if not self._clickable(T_BUTTON):
            return False
        self.mem.set8(0x6D5, self.mem.u8(0x6D5) | 0x40)
        return True

    def cmd_turret_steer_to(self, heading_rate: int, elevation_rate: int) -> None:
        """Mouse-steer deltas that make the next turret step set the aim rates $6CE (heading)
        and $6D0 (elevation) to these values (clamped to +-255): only on screen 8 in
        mouse-steer mode ($457 clear) with the observation turret working; a left click
        (cmd_vehicle_fire) fires."""
        m = self.mem
        m.set16(stunits.MOUSE_DX, w16(max(-255, min(255, heading_rate)) - m.s16(0x6CE)))
        m.set16(stunits.MOUSE_DX + 2, w16(max(-255, min(255, elevation_rate)) - m.s16(0x6D0)))

    def turret_input(self) -> None:
        """$1741A (screen 8): the gauge timer $968 (redraw); held cursor keys aim, Insert /
        Clr-Home zoom, a right click toggles mouse-steer mode $457."""
        m = self.mem
        if m.u16(0x968):
            m.set16(0x968, m.u16(0x968) - 1)
        for key, f in ((0x828, self.turret_aim_up), (0x838, self.turret_aim_down),
                       (0x82E, self.turret_aim_left), (0x832, self.turret_aim_right)):
            if m.u8(key):
                f()
        if self._bclr0(0x83D):
            self.turret_zoom(1)
        if self._bclr0(0x827):
            self.turret_zoom(-1)
        if self._bclr0(0x883):
            m.set8(stunits.POINTER_MODE, ~m.u8(stunits.POINTER_MODE))

    # ---- flares 9, viewing drone 10 (input $1778E), defence drones 11 -----------------------
    def cmd_flare_launch(self) -> bool:
        """$1338C (screen 9, 166,155), fresh press: cmd_launch_flare."""
        if not self._clickable(T_BUTTON):
            return False
        self.cmd_launch_flare()
        return True

    def cmd_recon_launch(self) -> bool:
        """$134C2 (screen 10, 210,155), fresh press: cmd_launch_recon_drone."""
        if not self._clickable(T_BUTTON):
            return False
        self.cmd_launch_recon_drone()
        return True

    def cmd_ssm_fire_at(self, px: int, py: int) -> bool:
        """$13598 (screen 10, the drone picture at 32,6, 256 x 128), fresh press: the pointer
        moves to (px, py) and, with the SSM armed ($1160, a right click), cmd_fire_ssm aims a
        cruise missile at the point under it. While armed the original clips the pointer to
        the picture."""
        x0, y0, x1, y1 = SSM_VIEW
        if not (x0 <= px <= x1 and y0 <= py <= y1 and self._clickable(T_BUTTON)):
            return False
        self._set_pointer(px, py)
        self.cmd_fire_ssm()
        return True

    def pointer_free(self) -> None:
        """$F712: modal mode $460 off (the pointer sprite and its clip are UI)."""
        self.mem.set8(MODAL, 0)

    def drone_view_input(self) -> None:
        """$1778E (screen 10): without the viewing drone (object 15) out the SSM is disarmed;
        otherwise a right click toggles SSM armed $1160 (arming points the pointer at the
        picture, disarming frees it, $F712)."""
        m = self.mem
        if not m.u8(self.obj(15) + 1) & 1:
            if m.u8(0x1160):
                m.set8(0x1160, 0)
                self.pointer_free()
            return
        if not self._bclr0(0x883):
            return
        if m.u8(0x1160):
            m.set8(0x1160, 0)
            self.pointer_free()
            return
        m.set8(0x1160, 0xFF)

    def cmd_drones_launch(self) -> bool:
        """$10738 (screen 11, 166,155; repeats while held): cmd_launch_defence_drone."""
        if not self._clickable(T_BUTTON):
            return False
        self.cmd_launch_defence_drone()
        return True

    def cmd_drones_recall(self) -> bool:
        """$1088C (screen 11, 166,178), fresh press: cmd_recall_defence_drones."""
        if not self._clickable(T_BUTTON):
            return False
        self.cmd_recall_defence_drones()
        return True

    def _no_override(self) -> bool:
        """$10552: during a drone recall ($122F) the stations can't change."""
        if self.mem.u8(0x122F):
            self._message("Cannot override docking sequence")
            return True
        return False

    def _set_stations(self, src: int) -> None:
        """$10614: each station's state (a0) &= 1 and its offsets +$A/+$C from src."""
        m = self.mem
        for k in range(4):
            a = 0x65E + 14 * k
            m.set16(a, m.u16(a) & 1)
            m.set16(a + 0xA, m.u16(src + 4 * k))
            m.set16(a + 0xC, m.u16(src + 4 * k + 2))

    def cmd_drone_stations_reset(self) -> bool:
        """$10602 (12,155), fresh press: the stations as they were when the screen was
        entered ($141DE); not during a recall ("Cannot cancel docking sequence")."""
        if not self._clickable(T_BUTTON):
            return False
        if self.mem.u8(0x122F):
            self._message("Cannot cancel docking sequence")
        else:
            self._set_stations(STATION_SNAPSHOT)
        return True

    def _formation(self, d: int) -> None:
        m = self.mem
        k = s16(w16(m.u16(0x698) + d))
        if d > 0 and k == 9:
            k = 0
        elif d < 0 and k < 0:
            k = 8
        m.set16(0x698, k)
        m.set16(0x95A, 2)

    def cmd_drone_formation_next(self) -> bool:
        """$106EC (298,155; repeats): formation $698 + 1 (wraps 9 to 0)."""
        if not self._clickable(T_BUTTON):
            return False
        self._formation(1)
        return True

    def cmd_drone_formation_prev(self) -> bool:
        """$10706 (298,178; repeats): formation - 1 (below 0: 8)."""
        if not self._clickable(T_BUTTON):
            return False
        self._formation(-1)
        return True

    def cmd_drone_formation_apply(self) -> bool:
        """$10714 (188,155), fresh press: the stations of formation [$698] (table $1065C)."""
        m = self.mem
        if not self._clickable(T_BUTTON):
            return False
        if self._no_override():
            return True
        k = m.u16(0x698)
        if k > 8:
            raise NotImplementedError(f"drone formation {k} beyond the table $1065C")
        self._set_stations(FORMATIONS + 16 * k)
        return True

    def cmd_drone_station_pick(self, k: int) -> bool:
        """$104C8 (the radar marker of station k, types 66-69): start dragging it ($696 = k,
        drop $104E6); not during a recall."""
        if not (0 <= k <= 3 and self._clickable(T_STATION + k)):
            return False
        if self._no_override():
            return True
        self.mem.set16(0x696, k)
        self._start_drag(0x104E6)
        return True

    def _drone_station_drop(self, px: int, py: int) -> None:
        """$104E6: the station [$696] to the radar point: +$A = (py - cy + 1) * 60, +$C =
        (px - cx - 4) * 60 (centre $4750/$4752); refused inside the red box (+-900 x
        +-1500) or within about 384 of another active station; its state &= 1."""
        m = self.mem
        a0 = (0x65E + s16(w16(m.u16(0x696) * 14))) & 0xFFFFFFFF
        d0 = s32(s16(w16(px - m.u16(0x4750) - 4)) * 60)
        d1 = s32(s16(w16(py - m.u16(0x4752) + 1)) * 60)
        lo0, lo1 = s16(w16(d0)), s16(w16(d1))
        if -900 <= lo1 <= 900 and -1500 <= lo0 <= 1500:
            return self._message("Drone must be outside red boundary")
        if self._drone_station_clash(a0, w16(d1), w16(d0)):
            return self._message("Cannot put drones on top of each other")
        m.set16(a0 + 0xA, d1)
        m.set16(a0 + 0xC, d0)
        m.set16(a0, m.u16(a0) & 1)

    def drone_station_pixel(self, k: int):
        """The radar point of station k's offsets (the drop's inverse, for the UI)."""
        m = self.mem
        a = 0x65E + 14 * k
        return (m.s16(0x4750) + 4 + (m.s16(a + 0xC) + 30) // 60, m.s16(0x4752) - 1 + (m.s16(a + 0xA) + 30) // 60)

    def cmd_drone_station_move(self, k: int, px: int, py: int) -> bool:
        """Drag station k's marker to the radar point (px, py): pick it up, release there,
        ui_frame drops it. False if the pick-up wasn't possible."""
        if not self.cmd_drone_station_pick(k) or not self.mem.u8(MODAL):
            return False
        self.cmd_drag_release(px, py)
        self.ui_frame()
        return True

    # ---- resources, screen 3 (input $177DE; panels $42CC8-$4314A by sub-panel [$D14]) ------
    def _at_stockpile(self) -> bool:
        """The carrier's island buffer ([$5E996], carrier +$3C) is the stockpile island's."""
        m = self.mem
        buf = m.u32(stunits.CARRIER + 0x3C)
        return bool(buf) and m.u8(buf + 1) == m.u16(stsim.STOCKPILE)

    def _resources_refused(self, text: str) -> None:
        """$17812: the message, back to the main panel, the tab re-entered ($1227/$1228)."""
        m = self.mem
        self._message(text)
        m.set16(RESOURCES, 0)
        m.set16(REQUEST_TAB, m.u16(TAB))
        m.set8(SCREEN_REQUEST, 0xFF)

    def resources_input(self) -> None:
        """$177DE (screen 3): the priority panels (2, 3) need communications ($B0A), the
        queue panels (4, 5) also no transfer running ($D3E) and the carrier at the stockpile
        island; otherwise back to the main panel."""
        m = self.mem
        mode = m.u16(RESOURCES)
        if mode < 2:
            return
        if not m.u8(0xB0A):
            return self._resources_refused("Communications are out")
        if mode < 4:
            return
        if m.u8(0xD3E):
            return self._resources_refused("Resupply operation in progress")
        if not self._at_stockpile():
            return self._resources_refused("Stockpile island out of range")

    def _res(self, mode: int) -> bool:
        return self._clickable(T_BUTTON, (RESOURCES, mode))

    def _items_copy(self, src: int, dst: int) -> None:
        """Class and target of the 15 store items (+$10/+$12 committed, +$14/+$16 edited)."""
        m = self.mem
        for k in range(stsim.N_ITEMS):
            a = stsim.ITEMS + stsim.ITEM_SIZE * k
            m.set16(a + dst, m.u16(a + src))
            m.set16(a + dst + 2, m.u16(a + src + 2))

    def cmd_resupply_abort(self) -> bool:
        """$1102E (12,155), fresh press: recall the supply drone ($D42 = $D46 = 2, $D40 = 0)
        unless no transfer runs, it has completed or been aborted, or it is docking
        ($D40 with $DE6 = 4)."""
        m = self.mem
        if not self._res(0):
            return False
        if not m.u8(0xD3E):
            self._message("No resupply operation is in progress")
        elif m.u16(0xD46) == 1:
            self._message("Resupply already completed")
        elif m.u16(0xD46) == 2:
            self._message("Resupply operation has been aborted")
        elif m.u8(0xD40) and m.u16(0xDE6) == 4:
            self._message("Too late to abort resupply")
        else:
            m.set16(0xD42, 2)
            m.set16(0xD46, 2)
            m.set8(0xD40, 0)
            self._message("Resupply aborted - drone recalled")
        return True

    def cmd_resources_transfer(self) -> bool:
        """$10E4A (34,155), fresh press: cmd_supply_transfer (stunits_enemy)."""
        if not self._res(0):
            return False
        self.cmd_supply_transfer()
        return True

    def cmd_item_picture(self, k: int) -> bool:
        """$1098A (the 15 item pictures, 44..244 x 8..92): sub-panel 1 showing item k ($D16)."""
        if not (0 <= k < stsim.N_ITEMS and self._res(0)):
            return False
        self.mem.set16(ITEM_PANEL, k)
        self.mem.set16(RESOURCES, 1)
        return True

    def cmd_item_picture_back(self) -> bool:
        """$109BC (142,115): back to the main panel."""
        if not self._res(1):
            return False
        self.mem.set16(RESOURCES, 0)
        return True

    def cmd_stores_priorities_open(self) -> bool:
        """$109E8 (56,155): needs communications; sub-panel 2, scroll $D38 = 0, no entry,
        the items' classes and targets copied to their edit fields (+$14/+$16)."""
        m = self.mem
        if not self._res(0):
            return False
        if not m.u8(0xB0A):
            self._message("Communications are out")
            return True
        m.set16(RESOURCES, 2)
        m.set16(STORES_SCROLL, 0)
        m.set8(ENTRY, 0)
        self._items_copy(0x10, 0x14)
        return True

    def cmd_stores_priorities_reset(self) -> bool:
        """$10A66 (228,22): the edit fields back to the committed values (not while a
        number is being typed)."""
        if not self._res(2):
            return False
        if not self.mem.u8(ENTRY):
            self._items_copy(0x10, 0x14)
        return True

    def cmd_stores_priorities_exit(self) -> bool:
        """$10A3C (228,114): commit the edit fields (+$14/+$16 -> +$10/+$12), main panel."""
        if not self._res(2):
            return False
        if not self.mem.u8(ENTRY):
            self.mem.set16(RESOURCES, 0)
            self._items_copy(0x14, 0x10)
        return True

    def cmd_stores_scroll_up(self) -> bool:
        """$10AA6 (176,44): the three rows show items $D38.. ; $D38 - 1 (>= 0)."""
        m = self.mem
        if not self._res(2):
            return False
        if not m.u8(ENTRY) and m.u16(STORES_SCROLL):
            m.set16(STORES_SCROLL, m.u16(STORES_SCROLL) - 1)
        return True

    def cmd_stores_scroll_down(self) -> bool:
        """$10A8A (176,76): $D38 + 1 (up to 12)."""
        m = self.mem
        if not self._res(2):
            return False
        if not m.u8(ENTRY) and m.u16(STORES_SCROLL) != 12:
            m.set16(STORES_SCROLL, m.u16(STORES_SCROLL) + 1)
        return True

    def _item(self, row: int, scroll: int) -> int:
        """The item record of a row: $B76 + (row + scroll) * $1A (mulu.w, index .w)."""
        m = self.mem
        return (stsim.ITEMS + s16(w16(w16(row + m.u16(scroll)) * stsim.ITEM_SIZE))) & 0xFFFFFFFF

    def cmd_stores_class(self, row: int, cls: int) -> bool:
        """$10ABC (80, 24/60/96), fresh press: the class field of row 0-2 (item $D38 + row):
        edit class +$14 = cls (0-2, by where the pointer is in the field, $10AE6)."""
        if not (0 <= row <= 2 and 0 <= cls <= 2 and self._res(2)):
            return False
        if not self.mem.u8(ENTRY):
            self.mem.set16(self._item(row, STORES_SCROLL) + 0x14, cls)
        return True

    def _entry_start(self, x: int, y: int, old: int, digits: int, d4: int, d5: int, callback: int) -> None:
        """$11142: start typing a number on the keypad (layout $D22-$D30 for the panel
        drawing, the old value $D32, the callback $D34, no digits yet: "0")."""
        m = self.mem
        m.set8(ENTRY, 0xFF)
        m.set32(ENTRY_CALLBACK, callback)
        m.set16(0xD22, x)
        m.set16(0xD24, y)
        m.set16(ENTRY_OLD, old)
        m.set16(ENTRY_MAX, digits)
        m.set16(0xD2E, d4)
        m.set16(0xD30, d5)
        m.set16(0xD26, x + 6 * digits - 1)
        m.set16(0xD28, y + 5)
        m.set16(ENTRY_COUNT, 0)
        m.set8(ENTRY_DIGITS, 0x30)
        m.set8(ENTRY_DIGITS + 1, 0)

    def cmd_stores_target(self, row: int) -> bool:
        """$10B02 (120, 31/67/103), fresh press: type a new target for row 0-2's item on the
        keypad (cmd_keypad), $D18 = row."""
        m = self.mem
        if not (0 <= row <= 2 and self._res(2)):
            return False
        if m.u8(ENTRY):
            return True
        m.set16(ITEM_EDITED, row)
        x, y = TARGET_ENTRY[row]
        self._entry_start(x + 0xE, y + 0xA, m.u16(self._item(row, STORES_SCROLL) + 0x16), 3, 10, 8, ENTRY_DONE)
        return True

    def _entry_done(self, v: int) -> None:
        """jmp [$D34]: only $10B5A: the edited row's item target +$16 = v."""
        m = self.mem
        cb = m.u32(ENTRY_CALLBACK)
        if cb != ENTRY_DONE:
            raise NotImplementedError(f"keypad entry callback ${cb:X} ($11142 has only the caller $10B02)")
        m.set16(self._item(m.u16(ITEM_EDITED), STORES_SCROLL) + 0x16, v)

    def cmd_keypad(self, key: int) -> bool:
        """$11196 (the keypad at 216,44 in the stores priorities), fresh press, while a
        number is being typed ($D1A): key 0-9 a digit (at most [$D2C], no leading 0), 10
        ENTER (the number to the callback), 11 CANCEL (the old value), 12 DELETE."""
        m = self.mem
        if not (0 <= key <= 12 and self._res(2)):
            return False
        if not m.u8(ENTRY):
            return True
        if key == 10:
            m.set8(ENTRY, 0)
            v, d1 = 0, 0
            for i in range(m.u16(ENTRY_COUNT)):          # move.b into d1, subi.w #'0': its high byte stays
                d1 = w16((d1 & 0xFF00) + m.u8(ENTRY_DIGITS + i) - 0x30)
                v = w16(v * 10 + d1)
            self._entry_done(v)
        elif key == 11:
            m.set8(ENTRY, 0)
            self._entry_done(m.u16(ENTRY_OLD))
        elif key == 12:
            n = m.u16(ENTRY_COUNT)
            if n:
                n -= 1
                m.set16(ENTRY_COUNT, n)
                if n:
                    m.set8(ENTRY_DIGITS + s16(n), 0)
                else:
                    m.set8(ENTRY_DIGITS, 0x30)
        else:
            n = m.u16(ENTRY_COUNT)
            if (n or key) and n != m.u16(ENTRY_MAX):
                a = (ENTRY_DIGITS + s16(n)) & 0xFFFFFFFF
                m.set8(a, key + 0x30)
                m.set8(a + 1, 0)
                m.set16(ENTRY_COUNT, n + 1)
        return True

    def cmd_fuel_priorities_open(self) -> bool:
        """$10B72 (122,155): needs communications; sub-panel 3, the fuel classes $D08-$D0C
        copied to the edit fields $D0E-$D12."""
        m = self.mem
        if not self._res(0):
            return False
        if not m.u8(0xB0A):
            self._message("Communications are out")
            return True
        m.set16(RESOURCES, 3)
        m.buf[FUEL_WORK:FUEL_WORK + 6] = m.buf[stsim.FUEL_CLASS:stsim.FUEL_CLASS + 6]
        return True

    def cmd_fuel_priorities_reset(self) -> bool:
        """$10BC6 (228,22): the edit fields back to $D08-$D0C."""
        m = self.mem
        if not self._res(3):
            return False
        m.buf[FUEL_WORK:FUEL_WORK + 6] = m.buf[stsim.FUEL_CLASS:stsim.FUEL_CLASS + 6]
        return True

    def cmd_fuel_priorities_exit(self) -> bool:
        """$10BAC (228,114): commit $D0E-$D12 to the fuel classes, main panel."""
        m = self.mem
        if not self._res(3):
            return False
        m.set16(RESOURCES, 0)
        m.buf[stsim.FUEL_CLASS:stsim.FUEL_CLASS + 6] = m.buf[FUEL_WORK:FUEL_WORK + 6]
        return True

    def cmd_fuel_class(self, k: int, cls: int) -> bool:
        """$10BDA (136, 24/60/96), fresh press: fuel k's (0-2) edit class $D0E + 2k = cls."""
        if not (0 <= k <= 2 and 0 <= cls <= 2 and self._res(3)):
            return False
        self.mem.set16(FUEL_WORK + 2 * k, cls)
        return True

    def _queue_open(self, mode: int) -> bool:
        """$10C06 / $10D52: needs communications, no transfer running and the carrier at
        the stockpile island."""
        m = self.mem
        if not self._res(0):
            return False
        if not m.u8(0xB0A):
            self._message("Communications are out")
        elif m.u8(0xD3E):
            self._message("Resupply operation in progress")
        elif not self._at_stockpile():
            self._message("Stockpile island out of range")
        else:
            m.set16(RESOURCES, mode)
        return True

    def cmd_stores_queue_open(self) -> bool:
        """$10C06 (188,155): sub-panel 4, the stores to ship from the stockpile."""
        return self._queue_open(4)

    def cmd_fuel_queue_open(self) -> bool:
        """$10D52 (254,155): sub-panel 5, the fuel to ship."""
        return self._queue_open(5)

    def cmd_stores_queue_back(self) -> bool:
        """$10C62 (244,116): main panel."""
        if not self._res(4):
            return False
        self.mem.set16(RESOURCES, 0)
        return True

    def cmd_fuel_queue_back(self) -> bool:
        """$10D9C (140,116): main panel."""
        if not self._res(5):
            return False
        self.mem.set16(RESOURCES, 0)
        return True

    def cmd_stores_queue_scroll_up(self) -> bool:
        """$10C7C (251,47): the rows show items $D3A..; $D3A - 1 (>= 0)."""
        m = self.mem
        if not self._res(4):
            return False
        if m.u16(QUEUE_SCROLL):
            m.set16(QUEUE_SCROLL, m.u16(QUEUE_SCROLL) - 1)
        return True

    def cmd_stores_queue_scroll_down(self) -> bool:
        """$10C6A (251,79): $D3A + 1 (up to 12)."""
        m = self.mem
        if not self._res(4):
            return False
        if m.u16(QUEUE_SCROLL) != 12:
            m.set16(QUEUE_SCROLL, m.u16(QUEUE_SCROLL) + 1)
        return True

    def _queue_step(self, held: int) -> int:
        """1 on a press ($D3C = 5); held: 1 for 5 more passes, then 10 a pass."""
        m = self.mem
        if not held:
            m.set16(QUEUE_REPEAT, 5)
            return 1
        if m.u16(QUEUE_REPEAT):
            m.set16(QUEUE_REPEAT, m.u16(QUEUE_REPEAT) - 1)
            return 1
        return 10

    def cmd_stores_unqueue(self, row: int, held: int = 0) -> bool:
        """$10C88 (134, 35/69/103; repeats): move row 0-2's item (item $D3A + row) from the
        queue $D96 back to the stockpile $D78, 1 at a time, 10 once held a while."""
        m = self.mem
        if not (0 <= row <= 2 and self._res(4)):
            return False
        d1 = self._queue_step(held)
        i = s16(w16(2 * w16(row + m.u16(QUEUE_SCROLL))))
        q, s = (stsim.STORES_PENDING + i) & 0xFFFFFFFF, (stsim.STORES + i) & 0xFFFFFFFF
        if m.s16(q) < d1:
            d1 = m.s16(q)
        m.set16(q, m.u16(q) - d1)
        m.set16(s, m.u16(s) + d1)
        return True

    def cmd_stores_queue(self, row: int, held: int = 0) -> bool:
        """$10CCA (163, 35/69/103; repeats): move row 0-2's item from the stockpile to the
        queue, up to the carrier's room (capacity +$18 - on board +8 - queued): "Limit of
        ACC stores reached"."""
        m = self.mem
        if not (0 <= row <= 2 and self._res(4)):
            return False
        d1 = self._queue_step(held)
        idx = w16(row + m.u16(QUEUE_SCROLL))
        i = s16(w16(2 * idx))
        q, s = (stsim.STORES_PENDING + i) & 0xFFFFFFFF, (stsim.STORES + i) & 0xFFFFFFFF
        a2 = (stsim.ITEMS + s16(w16(idx * stsim.ITEM_SIZE))) & 0xFFFFFFFF
        if m.s16(s) < d1:
            d1 = m.s16(s)
        room = s16(w16(m.u16(a2 + 0x18) - m.u16(a2 + 8) - m.u16(q)))
        if room == 0:
            self._message("Limit of ACC stores reached")
            return True
        if d1 > room:
            d1 = room
        m.set16(s, m.u16(s) - d1)
        m.set16(q, m.u16(q) + d1)
        return True

    def cmd_fuel_unqueue(self, k: int) -> bool:
        """$10DA4 / $10E18 / $10E30 (134, 36/61/86; repeats): fuel k's queue ($D6C + 4k)
        back to the stockpile ($D60 + 4k), $4000 a pass."""
        m = self.mem
        if not (0 <= k <= 2 and self._res(5)):
            return False
        s, q = stsim.FUEL + 4 * k, stsim.FUEL_PENDING + 4 * k
        d1 = min(0x4000, s32(m.u32(q)))
        m.set32(q, m.u32(q) - d1)
        m.set32(s, m.u32(s) + d1)
        return True

    def cmd_fuel_queue(self, k: int) -> bool:
        """$10DC0 / $10E22 / $10E3C (163, 36/61/86; repeats): stockpile fuel k to the queue,
        $4000 a pass, up to the room in the carrier's tank ($FFFE0 - queued - tank $CFC +
        4k): "Limit of ACC tanks reached"."""
        m = self.mem
        if not (0 <= k <= 2 and self._res(5)):
            return False
        s, q, tank = stsim.FUEL + 4 * k, stsim.FUEL_PENDING + 4 * k, 0xCFC + 4 * k
        d1 = min(0x4000, s32(m.u32(s)))
        room = s32(0xFFFE0 - m.u32(q) - m.u32(tank))
        if room == 0:
            self._message("Limit of ACC tanks reached")
            return True
        if room < d1:
            d1 = room
        m.set32(s, m.u32(s) - d1)
        m.set32(q, m.u32(q) + d1)
        return True

    # ---- messages 4, game status 32, title 33 ----------------------------------------------------
    def cmd_clear_message_log(self, logged: int) -> bool:
        """$12C2C (screen 4, 144,155), fresh press. The message log ($1148, $65D10: the
        messages $A128 records) is the app's, not the model's: `logged` says whether it has
        entries. Then it is cleared ($A1E8, the app's), $966 = 2 and "Message log
        cleared"; else "Message log is already clear"."""
        if not self._clickable(T_BUTTON):
            return False
        if logged:
            self.mem.set16(0x966, 2)
            self._message("Message log cleared")
        else:
            self._message("Message log is already clear")
        return True

    def cmd_game_option(self, k: int, on: int) -> bool:
        """$13778 (the options panel, $708 = 7; rows at y 29 + 16k, OFF at x 217-242, ON at
        252-277): option k (0 sea dots, 1 message ping, 2 background noise, 3 sound effects)
        $1246 + k = on ($FF) or off. Quirk: the element reaches a fifth row (y 93-106), k = 4,
        which writes $124A, the strategy flag."""
        if not (0 <= k <= 4 and self._clickable(T_BUTTON, (GAME_STATUS, 7))):
            return False
        self.mem.set8(OPTIONS + k, 0xFF if on else 0)
        return True

    def cmd_title_new_game(self, strategy: int) -> bool:
        """$103B2 / $103A0 (title menu, $708 = 0): a strategy / action game ($124A), new_game
        ($42A8); the next ui_frame enters the bridge."""
        if not self._clickable(T_BUTTON, (GAME_STATUS, 0)):
            return False
        self.new_game(bool(strategy))
        return True



# $170D4, the per-screen input handlers (screen: address; the rest are $170D2, rts).
INPUT_TABLE = {0: 0x17164, 1: 0x1747A, 2: 0x17244, 3: 0x177DE, 8: 0x1741A, 10: 0x1778E, 16: 0x17748,
               17: 0x17244, 18: 0x17374, 19: 0x17782, 24: 0x17610, 25: 0x17244, 26: 0x1773C, 27: 0x172BC}
# $170D4: screens whose input handler is ported ("" = $170D2, rts).
SCREEN_INPUT = {0: "bridge_input", 1: "damage_input", 2: "map_input", 17: "map_input", 25: "map_input",
                3: "resources_input", 8: "turret_input", 10: "drone_view_input",
                16: "walrus_arming_input", 18: "walrus_control_input", 19: "walrus_dock_input",
                24: "manta_arming_input", 26: "manta_deck_input", 27: "manta_control_input"}
SCREEN_INPUT.update({s: "" for s in range(36) if s not in INPUT_TABLE})
# $139D4 / $14318: the enter and leave handlers that write state (the others only set the
# palette or nothing: enter 3, 5-7, 12-15, 20-23, 28-31; leave 3-7, 9, 11-15, 19-23, 26, 28-31).
SCREEN_ENTER = {0: "enter_bridge", 1: "enter_damage", 2: "enter_carrier_map", 4: "enter_messages",
                8: "enter_turret", 9: "enter_flares", 10: "enter_drone_view", 11: "enter_drone_radar",
                16: "enter_walrus_arming", 17: "enter_walrus_map", 18: "enter_walrus_control",
                19: "enter_walrus_dock", 24: "enter_manta_arming", 25: "enter_manta_map",
                26: "enter_manta_deck", 27: "enter_manta_control", 32: "enter_game_status",
                33: "enter_title", 34: "enter_game_over", 35: "enter_destroyed"}
SCREEN_LEAVE = {0: "leave_bridge", 1: "leave_damage", 2: "leave_carrier_map", 8: "leave_turret",
                10: "leave_drone_view", 16: "leave_walrus_arming", 17: "leave_walrus_map",
                18: "leave_vehicle_control", 24: "leave_bridge", 25: "leave_manta_map",
                27: "leave_vehicle_control", 32: "leave_game_status", 33: "leave_title",
                34: "leave_game_over", 35: "leave_destroyed"}

# $F6EA: the drop callbacks of the drags that are ported (jmp [$468]).
DROPS = {0x113A4: "_manta_drop_loadout", 0x11500: "_manta_drop_return",
         0x11ACE: "_walrus_drop_loadout", 0x11C4E: "_walrus_drop_return",
         0x104E6: "_drone_station_drop"}

ROUTINES = {}


# ---- verification ----------------------------------------------------------------------------
GAME_STACK = 0x586AA                  # game_main's own stack ($7F000 is inside the screen the panels draw on)


def _call(oracle, addr: int) -> None:
    """check_sim.Oracle.call on the game's stack."""
    from unicorn import UcError
    from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_SR
    import check_sim
    oracle.uc.reg_write(UC_M68K_REG_SR, 0x2700)
    oracle.uc.reg_write(UC_M68K_REG_A7, GAME_STACK - 4)
    oracle.uc.mem_write(GAME_STACK - 4, check_sim.RETURN.to_bytes(4, "big"))
    try:
        oracle.uc.emu_start(addr, check_sim.RETURN, count=20_000_000)
    except UcError as e:
        raise RuntimeError(f"${addr:05X}: {e} at PC=${oracle.uc.reg_read(UC_M68K_REG_PC):05X}") from e
    if oracle.uc.reg_read(UC_M68K_REG_PC) != check_sim.RETURN:
        raise RuntimeError(f"${addr:05X} did not return")


def _regs(oracle, **regs) -> None:
    from unicorn.m68k_const import UC_M68K_REG_A0, UC_M68K_REG_D0
    for k, v in regs.items():
        base = UC_M68K_REG_D0 if k[0] == "d" else UC_M68K_REG_A0
        oracle.uc.reg_write(base + int(k[1]), v & 0xFFFFFFFF)


def _handler(addr: int, **fixed):
    """An oracle action: the click handler at addr, with the registers the hit test passes
    (d2 = 2 unless given); args name the pointer registers."""
    def act(oracle, state, args, result):
        if result is False:
            return
        regs = dict(d2=2, **fixed)
        for name, v in zip(("d0", "d1"), args):
            regs[name] = v
        _regs(oracle, **regs)
        _call(oracle, addr)
    return act


def _with_pointer(addr: int, gate=None):
    def act(oracle, state, args, result):
        if result is False:
            return
        oracle.uc.mem_write(0x474, (args[0] & 0xFFFF).to_bytes(2, "big") + (args[1] & 0xFFFF).to_bytes(2, "big"))
        if gate is None or gate(oracle):
            _regs(oracle, d2=2)
            _call(oracle, addr)
    return act


def _diff(mine: bytes, ref: bytes) -> list:
    """check_units.diff plus the panel flag table (the auto, announce, pause and system
    buttons' selected bits are read by the commands)."""
    import check_units
    lines = check_units.diff(mine, ref)
    a, n = FLAG_TABLE, 0xD4
    if mine[a:a + n] != ref[a:a + n]:
        lines.append(f"panel flags: want {bytes(ref[a:a + n]).hex()}")
        lines.append(f"             got  {bytes(mine[a:a + n]).hex()}")
    return lines


def _u16(oracle, a: int) -> int:
    return int.from_bytes(oracle.uc.mem_read(a, 2), "big")


def _u32(oracle, a: int) -> int:
    return int.from_bytes(oracle.uc.mem_read(a, 4), "big")


def _copy_input(oracle, state, args, result) -> None:
    """Raw input: the bytes the interrupt writes (keys, buttons, deltas, last character)."""
    for a, n in ((KEYS, 0x884 - KEYS), (stunits.MOUSE_DX, 4), (LAST_CHAR, 1)):
        oracle.uc.mem_write(a, bytes(state.mem.buf[a:a + n]))


def _held(addr: int):
    def act(oracle, state, args, result):
        if result is not False:
            _regs(oracle, d2=1 if args and args[0] else 2)
            _call(oracle, addr)
    return act


def _waypoint(oracle, state, args, result) -> None:
    if result is False:
        return
    oracle.uc.mem_write(CURSOR_CARRIER[0], bytes(state.mem.buf[CURSOR_CARRIER[0]:CURSOR_CARRIER[0] + 4]))
    oracle.uc.mem_write(CURSOR_CARRIER[1], bytes(state.mem.buf[CURSOR_CARRIER[1]:CURSOR_CARRIER[1] + 4]))
    _regs(oracle, d2=2)
    _call(oracle, 0x131A8)


def _repair_priority(oracle, state, args, result) -> None:
    """The panel handlers in the order a player clicks them (the gates as the model's)."""
    k, cls = args
    if not (0 <= k <= 9 and 0 <= cls <= 2):
        return
    probe = stunits.UnitState.from_ram(bytes(oracle.ram()))
    if _u16(oracle, PRIORITY_PANEL) != 1:
        if not probe._clickable(38, (0xB64, 0)):
            return
        _regs(oracle, d2=2)
        _call(oracle, 0xFBBE)
        probe = stunits.UnitState.from_ram(bytes(oracle.ram()))
    if probe._clickable(38, (PRIORITY_PANEL, 1)):
        _regs(oracle, d0=PRIORITY_GRID[0] + 28 * cls, d1=PRIORITY_GRID[1] + 10 * k, d2=2)
        _call(oracle, 0xFC64)
        _regs(oracle, d2=2)
        _call(oracle, 0xFC24)


# name: (oracle action, argument generator)
COMMANDS = {
    "cmd_carrier_throttle": (_handler(0xFE3C), lambda r: (0, r.randrange(150, 200))),
    "cmd_carrier_speed_up": (_handler(0xFDCE), None),
    "cmd_carrier_speed_down": (_handler(0xFDD4), None),
    "cmd_carrier_stop": (_handler(0x12A26), None),
    "cmd_carrier_centre_rudder": (_handler(0x12A16), None),
    "cmd_carrier_autopilot": (_held(0x12BAC), lambda r: (r.choice((0, 0, 1)),)),
    "cmd_map_click": (_with_pointer(0xF8D8), lambda r: (r.randrange(20, 300), r.randrange(0, 150))),
    "cmd_map_recentre": (_with_pointer(0xF8B4, lambda o: not _u16(o, MAP_INFO)),
                         lambda r: (r.randrange(20, 300), r.randrange(0, 150))),
    "cmd_map_zoom_in": (_handler(0xF984), None),
    "cmd_map_zoom_out": (_handler(0xF99A), None),
    "cmd_map_scroll_up": (_handler(0xFAB0), None),
    "cmd_map_scroll_down": (_handler(0xFAC8), None),
    "cmd_map_scroll_left": (_handler(0xFA84), None),
    "cmd_map_scroll_right": (_handler(0xFA6C), None),
    "cmd_map_centre_unit": (_handler(0xF9BE), None),
    "cmd_map_centre_carrier": (_handler(0xFA50), None),
    "cmd_map_island_info": (_handler(0xFAF4), None),
    "cmd_map_island_info_exit": (_handler(0xFB1A), None),
    "cmd_carrier_set_course": (_held(0x131A8), lambda r: (r.choice((0, 0, 1)),)),
    "cmd_carrier_clear_course": (_handler(0x1326C), None),
    "cmd_carrier_map_mode": (_handler(0x1336E), None),
    "cmd_carrier_cruise_up": (_handler(0x12E7A), None),
    "cmd_carrier_cruise_down": (_handler(0x12E8C), None),
    "cmd_carrier_cruise": (_handler(0x12E9E), lambda r: (0, r.randrange(150, 200))),
    "cmd_carrier_toggle_announce": (_handler(0x12E44), None),
    "cmd_set_carrier_waypoint": (_waypoint, lambda r: (r.randrange(-0x900000, 0x900000), r.randrange(-0x900000, 0x900000))),
    "cmd_priorities_open": (_handler(0xFBBE), None),
    "cmd_priority_click": (_handler(0xFC64), lambda r: (r.randrange(190, 290), r.randrange(10, 120))),
    "cmd_priorities_cancel": (_handler(0xFC08), None),
    "cmd_priorities_exit": (_handler(0xFC24), None),
    "cmd_set_repair_priority": (_repair_priority, lambda r: (r.randrange(-1, 11), r.randrange(-1, 4))),
    "cmd_pause": (_handler(0xFD66), None),
    "cmd_steer_to": (_copy_input, lambda r: (r.randrange(-300, 300),)),
    "cmd_toggle_mouse_steer": (_copy_input, None),
    "input_mouse_move": (_copy_input, lambda r: (r.randrange(-128, 128), r.randrange(-128, 128))),
    "input_mouse_button": (_copy_input, lambda r: (r.randrange(2), r.randrange(2))),
    "input_key": (_copy_input, lambda r: (r.choice((0x10, 0x1F, 0x1D, 0x2F, 0x38, 0x62, 0x47, 0x52, 0x48, 0x50,
                                                    0x4B, 0x4D, 0x6D, 0x6E, 0x6F, 0x4E, 0x4A, 0x69, 0x22, 0x13)),
                                          r.choice((0, 1, 1)))),
}
# cmd_select_system takes the element in a3.
COMMANDS["cmd_select_system"] = (
    lambda o, st, args, res: None if res is False else (_regs(o, a3=0x42076 + 22 * args[0], d2=2), _call(o, 0xFB3A)),
    lambda r: (r.randrange(10),))
_Y_ONLY = ("cmd_carrier_throttle", "cmd_carrier_cruise")      # the model takes y; the handler reads d1


def _call_args(name: str, rnd) -> tuple:
    gen = COMMANDS[name][1]
    args = gen(rnd) if gen else ()
    return args[1:] if name in _Y_ONLY else args


def apply_command(state, oracle, name: str, args: tuple):
    """The command on the model, then what the original does for it on the oracle."""
    result = getattr(state, name)(*args)
    COMMANDS[name][0](oracle, state, (0,) + args if name in _Y_ONLY else args, result)
    return result


def _perturb_ui(m, rnd) -> None:
    """Random UI and input state around the controls: screens 0/1/2/4/17/25, pointer and
    mouse-steer mode, autopilot and course bits, the auto button, map view and cursors,
    damage screen and priority panel, pause and menu flags, keys and the cheat code."""
    m.set16(stunits.SCREEN, rnd.choice((0, 0, 0, 1, 2, 2, 4, 17, 25)))
    m.set8(stunits.POINTER_MODE, rnd.choice((0, 0xFF)))
    m.set8(stunits.C_FLAGS, rnd.choice((0, 1, 2, 3, 4, 6, 7, 0x80, 0x83)))
    m.set8(stunits.C_SUBSTATE, rnd.choice((0, 1, 1, 1)))
    m.set16(FLAG_TABLE + 2 * T_AUTOPILOT, rnd.choice((0, 0x2000)))
    m.set16(FLAG_TABLE + 2 * T_ANNOUNCE, rnd.choice((0, 0x2000)))
    m.set16(FLAG_TABLE + 2 * T_PAUSE, rnd.choice((0x1000, 0x1000, 0x3000)))
    for k in range(10):
        m.set16(FLAG_TABLE + 2 * (T_SYSTEM + k), rnd.choice((0, 0, 0, 0x2000)))
        m.set16(stsim.SYSTEMS + 8 * k + 4, rnd.randrange(3))
        m.set16(PRIORITIES + 2 * k, rnd.randrange(3))
    if rnd.random() < 0.1:
        m.set16(FLAG_TABLE + 2 * 38, rnd.choice((0x4000, 0x8000)))
    m.set16(SYSTEM_SHOWN, rnd.choice((0xFFFF, 0, 3, 8, 8, 9)))
    m.set16(PART_SHOWN, rnd.randrange(9))
    m.set16(PRIORITY_PANEL, rnd.choice((0, 0, 1)))
    m.set16(PRIORITY_PANEL + 2, m.u16(PRIORITY_PANEL) if rnd.random() < 0.9 else 1 - m.u16(PRIORITY_PANEL))
    m.set16(0x952, rnd.choice((0, 0, 3)))
    for t in (0x954, 0x956, 0x958, 0x962):
        m.set16(t, rnd.choice((0, 0, 2)))
    m.set16(0x702, rnd.choice((0, 0, 0, 0, 1)))
    m.set16(MAP_INFO, rnd.choice((0, 0, 0, 1)))
    m.set16(CARRIER_MAP_MODE, rnd.choice((0, 0, 1)))
    level = rnd.choice((0, 1, 5, 9, 10))
    m.set16(MAP_ZOOM_LEVEL, level)
    m.set16(MAP_ZOOM, 1 << level)
    c = stunits.CARRIER
    if rnd.random() < 0.5:
        m.set32(MAP_X, m.u32(c + 0x0A) + rnd.randrange(-50000, 50000))
        m.set32(MAP_Z, m.u32(c + 0x12) + rnd.randrange(-50000, 50000))
    else:
        m.set32(MAP_X, rnd.choice((0, 0x7FF000, -0x7FF000, rnd.randrange(-0x800000, 0x800000))))
        m.set32(MAP_Z, rnd.choice((0, 0x7FF000, -0x7FF000, rnd.randrange(-0x800000, 0x800000))))
    m.set16(MAP_ISLAND, rnd.choice((0xFFFF, rnd.randrange(64))))
    m.set32(0x742, rnd.randrange(-0x400000, 0x400000))
    m.set32(0x746, rnd.randrange(-0x400000, 0x400000))
    m.set16(0xD4A, rnd.choice((0xFFFF, rnd.randrange(64))))
    m.set16(CRUISE, rnd.choice((0, 0x40, 0x64, 0x70)))
    m.set8(ANNOUNCE, rnd.choice((0, 0xFF)))
    m.set16(0x474, rnd.randrange(0, 320))
    m.set16(0x476, rnd.randrange(0, 200))
    for k in range(4):                                     # selected Manta/Walrus state
        m.set8(0x496 + 0x34 * k + 1, rnd.choice((0, 2, 8, 8, 0xFF)))
        m.set8(0x570 + 0x34 * k + 1, rnd.choice((0, 2, 8, 8, 0xFF)))
    m.set8(RUNNING, rnd.choice((0xFF, 0xFF, 0xFF, 0)))
    m.set8(MENU, rnd.choice((0, 0, 0, 0, 0xFF)))
    m.set8(MODAL, rnd.choice((0, 0, 0, 0xFF)))
    for a in (0x7B9, 0x7D7, 0x7D2, 0x7D3, 0x7F7, 0x808, 0x85D, 0x883, 0x83D, 0x827, 0x828, 0x838,
              0x82E, 0x832, 0x83C, 0x826):
        m.set8(a, 1 if rnd.random() < 0.12 else 0)
    if rnd.random() < 0.15:
        m.set8(rnd.choice(MAP_KEYPAD), 1)
    m.set8(DEBUG, rnd.choice((0, 0, 0, 0xFF)))
    for a in (0x873, 0x875, 0x877, 0x835, 0x82D, 0x86B):
        m.set8(a, 1 if rnd.random() < 0.1 else 0)
    if rnd.random() < 0.05:
        m.set8(0x80F + 2 * rnd.randrange(10), 1)
    i = rnd.randrange(22)
    m.set32(CHEAT_PTR, CHEAT_CODE + i)
    m.set8(LAST_CHAR, rnd.choice((0xFF, 0xFF, ~m.u8(CHEAT_CODE + i) & 0xFF, 0x41)))
    m.set16(END_TIMER, rnd.choice((0, 0, 0, 1, 7)))
    m.set8(END_REQUEST, rnd.choice((0, 0, 0, 0xFF)))
    m.set16(RESULT, rnd.randrange(4))
    m.set8(0x8FD, rnd.choice((0, 0, 0xFF)))


def _files(files) -> list:
    """Action captures plus the real-play ones (other screens, a Manta out)."""
    from stgfx import ROOT
    play = sorted((ROOT / "emu" / "out" / "play").glob("pass_*_in.bin"))
    return list(files[::4]) + play[::25]


def check_commands(files) -> bool:
    """Every command against its original click handler(s) (registers as the hit test
    passes them: d0/d1 pointer, d2 = 2 or 1, a3 element) on captured and perturbed states."""
    import random
    import check_sim
    import check_units
    rnd = random.Random(31)
    ok = True
    states = _files(files)
    for name in COMMANDS:
        runs = 0
        for f in states:
            for _ in range(4):
                m = stsim.Mem(bytearray(f.read_bytes()))
                _perturb_ui(m, rnd)
                ram = bytes(m.buf)
                oracle = check_sim.Oracle(ram)
                state = stunits.UnitState.from_ram(ram)
                apply_command(state, oracle, name, _call_args(name, rnd))
                ref = oracle.ram()
                mine = bytearray(ref)
                state.to_ram(mine)
                lines = _diff(mine, ref)
                runs += 1
                if lines:
                    print(f"{name}: MISMATCH from {f.name}")
                    for line in lines:
                        print("  " + line)
                    ok = False
                    break
            else:
                continue
            break
        else:
            print(f"{name}: OK ({runs} states)")
    return ok


FRAME_START, FRAME_END = 0x4090, 0x408A        # after ui_frame, back to the top of the loop


def oracle_game_frame(oracle) -> None:
    """One game_main loop on the original from $4090 (ui_frame is the UI's), rendering
    $14472 and frame_flip $17F48 stubbed."""
    from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_SR
    oracle.uc.reg_write(UC_M68K_REG_SR, 0x2700)
    oracle.uc.reg_write(UC_M68K_REG_A7, GAME_STACK)
    oracle.uc.emu_start(FRAME_START, FRAME_END, count=60_000_000)
    if oracle.uc.reg_read(UC_M68K_REG_PC) != FRAME_END:
        raise RuntimeError(f"game_main loop did not return to ${FRAME_END:X}")


def _frame_oracle(ram: bytes):
    import check_sim
    oracle = check_sim.Oracle(ram)
    for a in (0x14472, 0x17F48):
        oracle.uc.mem_write(a, b"\x4e\x75")
    return oracle


STOP = (NotImplementedError, stsim.TacticalMode, stsim.EnemyDestroyed)


def check_game_frame(files) -> bool:
    """game_frame against the original game_main loop: single frames on perturbed states
    (pause, menu, keys, cheat code, debug keys, end timer and request, screens 0/1/2/17/25),
    then multi-frame runs with random commands and raw input between frames, the original
    running its own screen_input."""
    import random
    import check_units
    rnd = random.Random(37)
    states = _files(files)
    ok, stopped, single = True, {}, 0
    for f in states:
        for _ in range(12):
            m = stsim.Mem(bytearray(f.read_bytes()))
            _perturb_ui(m, rnd)
            ram = bytes(m.buf)
            state = stunits.UnitState.from_ram(ram)
            try:
                state.game_frame()
            except STOP as e:
                stopped[str(e) or type(e).__name__] = stopped.get(str(e) or type(e).__name__, 0) + 1
                continue
            oracle = _frame_oracle(ram)
            oracle_game_frame(oracle)
            ref = oracle.ram()
            mine = bytearray(ref)
            state.to_ram(mine)
            lines = _diff(mine, ref)
            single += 1
            if lines:
                print(f"game_frame: MISMATCH from {f.name}")
                for line in lines:
                    print("  " + line)
                return False
    print(f"game_frame: OK ({single} perturbed single frames)")
    names = [n for n in COMMANDS if n not in ("cmd_pause", "input_key")] * 4 + ["cmd_pause", "input_key"]
    runs, frames = 0, 0
    for n in range(40):
        f = rnd.choice(states)
        m = stsim.Mem(bytearray(f.read_bytes()))
        _perturb_ui(m, rnd)
        m.set8(RUNNING, 0xFF)
        m.set8(MENU, 0)
        m.set8(DEBUG, 0)
        m.set16(stunits.SCREEN, rnd.choice((0, 0, 1, 2)))
        ram = bytes(m.buf)
        state = stunits.UnitState.from_ram(ram)
        oracle = _frame_oracle(ram)
        runs += 1
        for p in range(200):
            try:
                for _ in range(rnd.choice((0, 0, 1, 2))):
                    name = rnd.choice(names)
                    apply_command(state, oracle, name, _call_args(name, rnd))
                if p % 8 == 0:
                    oracle.uc.mem_write(0x8FD, b"\xff")
                    state.mem.set8(0x8FD, 0xFF)
                state.game_frame()
            except STOP as e:
                stopped[str(e) or type(e).__name__] = stopped.get(str(e) or type(e).__name__, 0) + 1
                break
            oracle_game_frame(oracle)
            frames += 1
            ref = oracle.ram()
            mine = bytearray(ref)
            state.to_ram(mine)
            lines = _diff(mine, ref)
            if lines:
                print(f"game_frame run {n} from {f.name}: MISMATCH at frame {p}")
                for line in lines:
                    print("  " + line)
                ok = False
                break
        if not ok:
            break
    print(f"game_frame runs: {'OK' if ok else 'MISMATCH'} ({runs} runs, {frames} frames with commands)")
    for k, v in sorted(stopped.items(), key=lambda kv: -kv[1]):
        print(f"  stopped at unported code {v}x: {k}")
    return ok


# ---- screen switching checks -------------------------------------------------------------------
SWITCH_SCREENS = (0, 1, 2, 3, 4, 8, 9, 10, 11, 12, 16, 17, 18, 19, 20, 24, 25, 26, 27, 28, 32)
ALL_SCREENS = SWITCH_SCREENS + (33, 34, 35)
UI_FRAME, AFTER_CLICK = 0xF636, 0xF67A
ELEMENT = GAME_STACK - 0x200          # a fake panel element for the icon handlers (they read its type)
STATUS_HANDLERS = {6: 0x101F2, 3: 0x10200, 1: 0x101E4, 7: 0x1020E}


def _switch_oracle(ram: bytes):
    """The original with rendering and frame_flip stubbed, the game-over leave handler's
    key wait ($14450) skipped, and the screen input handlers this module doesn't port
    stubbed (as the model's are in _stub_inputs)."""
    oracle = _frame_oracle(ram)
    oracle.uc.mem_write(0x14454, b"\x4e\x71")
    for s in range(36):
        if s not in SCREEN_INPUT:
            oracle.uc.mem_write(0x170D4 + 4 * s, (0x170D2).to_bytes(4, "big"))
    return oracle


class _stub_inputs:
    """The model's unported screen input handlers as no-ops while a check runs passes."""
    def __enter__(self):
        self.added = [s for s in range(36) if s not in SCREEN_INPUT]
        for s in self.added:
            SCREEN_INPUT[s] = ""

    def __exit__(self, *exc):
        for s in self.added:
            del SCREEN_INPUT[s]


def _click(oracle, handler: int, etype: int = 0) -> None:
    """A click on an element of type etype: its handler (d2 = 2, a3 = the element), then
    the rest of ui_frame ($F67A)."""
    oracle.uc.mem_write(ELEMENT, (etype & 0xFFFF).to_bytes(2, "big"))
    _regs(oracle, d2=2, a3=ELEMENT)
    _call(oracle, handler)
    _call(oracle, AFTER_CLICK)


def _oracle_switch(oracle, screen: int) -> None:
    """cmd_switch_screen's clicks on the original (decided from its own state)."""
    tab = TAB_OF.get(screen, 5)
    if tab > 4:
        return
    probe = stunits.UnitState.from_ram(bytes(oracle.ram()))
    if _u16(oracle, TAB) != tab:
        if not probe._clickable(T_TAB + tab, (0x704, 0)):
            return
        _click(oracle, 0xF844, T_TAB + tab)
        probe = stunits.UnitState.from_ram(bytes(oracle.ram()))
    if _u16(oracle, stunits.SCREEN) != screen and probe._clickable(screen, (0x706, 0)):
        _click(oracle, 0xF880, screen)


def _when(handler: int, etype=None):
    def act(oracle, state, args, result):
        if result:
            _click(oracle, handler, etype(args) if etype else 0)
    return act


SWITCH_COMMANDS = {
    "ui_frame": (lambda o, st, args, res: _call(o, UI_FRAME), None),
    "cmd_select_tab": (_when(0xF844, lambda a: T_TAB + a[0]), lambda r, m: (r.randrange(-1, 6),)),
    "cmd_select_screen": (_when(0xF880, lambda a: a[0]),
                          lambda r, m: (r.choice([s for s in SWITCH_SCREENS if TAB_OF[s] == m.u16(TAB)] or [0])
                                        if r.random() < 0.8 else r.randrange(-1, 30),)),
    "cmd_switch_screen": (lambda o, st, args, res: _oracle_switch(o, args[0]), lambda r, m: (r.choice(ALL_SCREENS),)),
    "cmd_game_status_menu": (lambda o, st, args, res: res and _click(o, STATUS_HANDLERS[args[0]]),
                             lambda r, m: (r.choice((1, 3, 6, 7, 2)),)),
    "cmd_game_status_back": (_when(0x1021C), None),
    "cmd_surrender": (_when(0x101D8), None),
    "cmd_continue": (lambda o, st, args, res: res and _click(o, 0x13990 if args[0] else 0x1399C),
                     lambda r, m: (r.randrange(2),)),
}


def _perturb_screens(m, rnd) -> None:
    """_perturb_ui plus the screen state: current screen and tab, remembered screens, icon
    flags, panel states, pending tab/screen and forced requests, selections and bay
    records, game-status mode, the inputs the enter handlers read."""
    _perturb_ui(m, rnd)
    screen = rnd.choice(ALL_SCREENS + SWITCH_SCREENS)
    m.set16(stunits.SCREEN, screen)
    tab = TAB_OF[screen] if rnd.random() < 0.9 else rnd.randrange(8)
    m.set16(TAB, tab)
    for t in range(1, 5):
        m.set16(TAB_SCREENS + 2 * t, rnd.choice([s for s in SWITCH_SCREENS if TAB_OF[s] == t]))
    for t, s in ((0, 32), (5, 33), (6, 34), (7, 35)):
        m.set16(TAB_SCREENS + 2 * t, s)
    if TAB_OF[screen] == tab and rnd.random() < 0.9:
        m.set16(TAB_SCREENS + 2 * tab, screen)
    odd = lambda v: rnd.choice((0x2000, 0x4000, 0x8000, 0x1000)) if rnd.random() < 0.05 else v
    for t in range(5):
        m.set16(FLAG_TABLE + 2 * (T_TAB + t), odd(0x2000 if t == tab else 0))
    for s in range(29):
        m.set16(FLAG_TABLE + 2 * s, odd(0x2000 if s == screen else 0))
    for t in range(4):
        m.set16(FLAG_TABLE + 2 * (T_STATUS_MENU + t), rnd.choice((0, 0, 0x2000)))
    for t in (62, 63, 64, 80, 91, 104, 105):
        m.set16(FLAG_TABLE + 2 * t, rnd.choice((0, 0x2000)))
    m.set16(0x704, rnd.choice((0, 0, 0, 0, 0, 1)))
    m.set16(0x706, rnd.choice((0, 0, 0, 0, 0, 1)))
    m.set16(GAME_STATUS, rnd.randrange(8))
    m.set16(PENDING_TAB, rnd.choice((0xFFFF, 0xFFFF, 0xFF02, 0xFF00, rnd.randrange(5))))
    m.set16(SCREEN_SELECT, rnd.choice((0xFFFF, 0xFFFF, 0xFF00, rnd.choice(SWITCH_SCREENS), screen)))
    m.set8(SCREEN_REQUEST, rnd.choice((0, 0, 0, 0xFF)))
    m.set16(REQUEST_TAB, rnd.randrange(8))
    for a in (0x48C, 0x566, 0xDB4, 0xDB6, 0xDCC, 0xDE4):
        m.set16(a, rnd.randrange(4))
    for k in range(4):
        for rec, weapon in ((0x496 + 0x34 * k, 0x1A), (0x570 + 0x34 * k, 0x18)):
            m.set8(rec, rnd.randrange(256))
            m.set8(rec + 0x32, rnd.choice((0, 0, 0xFF)))
            m.set16(rec + weapon, rnd.randrange(3))
            if rnd.random() < 0.3:
                m.set8(rec + 1, rnd.choice((1, 3, 4, 7, 9, 0x80)))
    viewed = {18: lambda: 0x5E45A + 0x140 * m.u16(0x566), 27: lambda: 0x5DF5A + 0x140 * m.u16(0x48C)}
    if screen in viewed and rnd.random() < 0.9:
        m.set32(stunits.VIEWED, viewed[screen]())
    else:
        m.set32(stunits.VIEWED, rnd.choice((stunits.CARRIER, 0x5DF5A + 0x140 * rnd.randrange(8), 0x5EF9A)))
    m.set16(0x6D2, rnd.randrange(4))
    m.set16(0xD14, rnd.randrange(6))
    m.set16(SYSTEM_SHOWN, rnd.choice((0xFFFF, 0, 3, 7, 8, 9)))
    for a in (0x6C6, 0x6BA, 0x6AC, 0x1230, 0x8F0, 0x8F1):
        m.set8(a, rnd.choice((0, 0xFF)))
    for a in (0x6B8, 0x6AA):
        m.set16(a, rnd.choice((0, 1)))
    m.set16(RESULT, rnd.randrange(5))
    m.set16(END_TAB, rnd.randrange(1, 5))
    m.set32(0xA94, rnd.choice((0, 30000)))
    for i in range(4):
        m.set16(0x65E + 14 * i + 0xA, rnd.randrange(-3000, 3000))
        m.set16(0x65E + 14 * i + 0xC, rnd.randrange(-3000, 3000))
    m.set8(0x5EF9B, m.u8(0x5EF9B) ^ (2 if rnd.random() < 0.5 else 0))
    if m.u8(MODAL):
        m.set8(0x880, 1)                    # dragging: the button is held
    else:
        m.set8(0x880, 0)                    # no click for the hit test (the model's clicks are commands)
        m.set8(0x881, 0)


DRAWN_FLAGS = (79, 81)        # hidden bits set by panel draw callbacks ($C3D4, $C076: Walrus arming), not modelled


def _compare(state, oracle, label: str) -> bool:
    ref = oracle.ram()
    mine = bytearray(ref)
    state.to_ram(mine)
    for t in DRAWN_FLAGS:
        a = FLAG_TABLE + 2 * t
        mine[a:a + 2] = ref[a:a + 2]
    lines = _diff(mine, ref)
    if lines:
        print(f"{label}: MISMATCH")
        for line in lines:
            print("  " + line)
    return not lines


def _switch_states(files) -> list:
    from stgfx import ROOT
    newgame = sorted((ROOT / "emu" / "out" / "newgame").glob("*/pass0_in.bin"))
    return _files(files) + newgame


def _run_passes(state, oracle, rnd, n: int, label: str, stopped: dict) -> int:
    """n passes of ui_frame + game_frame on both (screen input stubbed where not ported),
    with a random switch command now and then. Returns the passes done, -1 on a mismatch."""
    names = list(SWITCH_COMMANDS)
    if state.mem.u8(MODAL) or state.mem.u8(0x880):
        return 0                            # a drag: the button held would reach the hit test later
    for p in range(n):
        try:
            if rnd.random() < 0.15:
                name = rnd.choice(names[1:])
                gen = SWITCH_COMMANDS[name][1]
                args = gen(rnd, state.mem) if gen else ()
                res = getattr(state, name)(*args)
                SWITCH_COMMANDS[name][0](oracle, state, args, res)
            if p % 8 == 0:
                oracle.uc.mem_write(0x8FD, b"\xff")
                state.mem.set8(0x8FD, 0xFF)
            state.ui_frame()
            if state.mem.u16(stunits.SCREEN) not in SCREEN_INPUT:
                state._vec = None           # tools/export_sim_vectors.py: stop recording (the stub is check-only)
            with _stub_inputs():
                state.game_frame()
        except STOP as e:
            stopped[str(e) or type(e).__name__] = stopped.get(str(e) or type(e).__name__, 0) + 1
            return p
        _call(oracle, UI_FRAME)
        oracle_game_frame(oracle)
        if not _compare(state, oracle, f"{label}, pass {p}"):
            return -1
    return n


def check_screen_switch(files) -> bool:
    """Screen switching against ui_frame $F636 and the enter/leave handlers: every target
    screen by clicks (cmd_switch_screen) from every screen, forced requests to every tab,
    then random commands (tab and screen icons, game-status buttons, continue) and
    ui_frame on perturbed states, each followed by passes of ui_frame + game_frame."""
    import random
    from collections import Counter
    rnd = random.Random(41)
    states = _switch_states(files)
    entered, ok, stopped, passes = Counter(), True, {}, 0
    # 1. Every target from captured states with a random current screen (game running).
    for i, f in enumerate(states):
        for target in ALL_SCREENS:
            m = stsim.Mem(bytearray(f.read_bytes()))
            _perturb_screens(m, rnd)
            m.set8(RUNNING, 0xFF)
            m.set16(0x704, 0)
            m.set16(0x706, 0)
            m.set8(MODAL, 0)
            ram = bytes(m.buf)
            state, oracle = stunits.UnitState.from_ram(ram), _switch_oracle(ram)
            if target in (33, 34, 35):              # forced request (tab 5-7), as game_frame makes them
                state.mem.set8(SCREEN_REQUEST, 0xFF)
                state.mem.set16(REQUEST_TAB, TAB_OF[target])
                oracle.uc.mem_write(SCREEN_REQUEST, b"\xff" + bytes(1) + bytes([TAB_OF[target]]))
                state.ui_frame()
                _call(oracle, UI_FRAME)
            else:
                state.mem.set8(SCREEN_REQUEST, 0)
                oracle.uc.mem_write(SCREEN_REQUEST, b"\x00")
                state.cmd_switch_screen(target)
                _oracle_switch(oracle, target)
            entered[state.mem.u16(stunits.SCREEN)] += 1
            if not _compare(state, oracle, f"switch to {target} from {f.name}"):
                return False
            if i % 2 == 0:
                n = _run_passes(state, oracle, rnd, 8, f"switch to {target} from {f.name}", stopped)
                if n < 0:
                    return False
                passes += n
    print(f"screen switch: OK (every screen from {len(states)} states; entered {dict(sorted(entered.items()))})")
    # 2. Random commands on perturbed states.
    runs = Counter()
    for f in states:
        for _ in range(16):
            m = stsim.Mem(bytearray(f.read_bytes()))
            _perturb_screens(m, rnd)
            ram = bytes(m.buf)
            state, oracle = stunits.UnitState.from_ram(ram), _switch_oracle(ram)
            name = rnd.choice(list(SWITCH_COMMANDS))
            gen = SWITCH_COMMANDS[name][1]
            args = gen(rnd, state.mem) if gen else ()
            try:
                res = getattr(state, name)(*args)
            except STOP as e:
                stopped[str(e) or type(e).__name__] = stopped.get(str(e) or type(e).__name__, 0) + 1
                continue
            SWITCH_COMMANDS[name][0](oracle, state, args, res)
            runs[name] += 1
            if not _compare(state, oracle, f"{name}{args} from {f.name}"):
                return False
            if rnd.random() < 0.3:
                n = _run_passes(state, oracle, rnd, 12, f"{name}{args} from {f.name}", stopped)
                if n < 0:
                    return False
                passes += n
    print(f"screen commands: OK ({dict(runs)}; {passes} passes after switches)")
    for k, v in sorted(stopped.items(), key=lambda kv: -kv[1]):
        print(f"  stopped at unported code {v}x: {k}")
    return ok



# ---- Manta and Walrus control checks (milestone 2) ------------------------------------------------
VEHICLE_SCREENS = (16, 17, 18, 19, 24, 25, 26, 27)
STOCKS = (0xB7E, 0xB98, 0xBCC, 0xBE6, 0xC00, 0xC34, 0xC4E, 0xC68, 0xCB6)   # the loadout entries' stock counts


def _el(handler: int, base: int = 0, step: int = 0x16, d2=None):
    """An oracle action: the click handler with a3 = the real panel element (base + step *
    the first argument when step, else base), d2 = 2 (1 when the first argument says held)."""
    def act(oracle, state, args, result):
        if result is False:
            return
        a3 = base + step * args[0] if step and args else base
        held = d2(args) if d2 else 0
        _regs(oracle, d2=1 if held else 2, a3=a3)
        _call(oracle, handler)
    return act


def _y(handler: int, x: int):
    def act(oracle, state, args, result):
        if result is not False:
            _regs(oracle, d0=x, d1=args[0], d2=2)
            _call(oracle, handler)
    return act


def _cursor_then(handler: int, cursors):
    """cmd_set_*_waypoint: the cursor values as the model wrote them, then set course."""
    def act(oracle, state, args, result):
        if result is False:
            return
        for a in cursors:
            oracle.uc.mem_write(a, bytes(state.mem.buf[a:a + 4]))
        _regs(oracle, d2=2)
        _call(oracle, handler)
    return act


def _oracle_release(oracle, px: int, py: int) -> None:
    """cmd_drag_release + ui_frame on the original: pointer, left button up, $F636."""
    oracle.uc.mem_write(0x474, (px & 0xFFFF).to_bytes(2, "big") + (py & 0xFFFF).to_bytes(2, "big"))
    oracle.uc.mem_write(0x880, b"\x00")
    _call(oracle, UI_FRAME)


def _release(state, px: int, py: int) -> None:
    state.cmd_drag_release(px, py)
    state.ui_frame()


def _oracle_fit(kind: str):
    """cmd_manta_fit / cmd_walrus_fit / *_unfit, click by click on the original (each
    decided from its own state, as the model's)."""
    manta = kind == "Manta"
    codes, n, cur = (MANTA_CODES, 6, LOADOUT_CURSOR) if manta else (WALRUS_CODES, 5, LOADOUT_CURSOR + 2)
    nxt, pick = (0x1132E, 0x1135E) if manta else (0x11A30, 0x11A82)
    slots, slot_handler = (MANTA_SLOTS, 0x114C8) if manta else (WALRUS_SLOTS, 0x11C16)

    def probe(o):
        return stunits.UnitState.from_ram(bytes(o.ram()))

    def fit(oracle, state, args, result):
        slot, code = args
        if not 0 <= slot < len(slots):
            return
        for _ in range(n + 1):
            k = _u16(oracle, cur)
            if k < n and _u16(oracle, codes + 2 * k) == code:
                break
            if not probe(oracle)._loadout_clickable(kind):
                return
            _regs(oracle, d2=2)
            _call(oracle, nxt)
        else:
            return
        if not probe(oracle)._loadout_clickable(kind):
            return
        _regs(oracle, d2=2)
        _call(oracle, pick)
        if not oracle.uc.mem_read(MODAL, 1)[0]:
            return
        _, x0, y0, x1, y1 = SLOT_ELEMENTS[slots[slot]]
        _oracle_release(oracle, (x0 + x1) // 2, (y0 + y1) // 2)

    def unfit(oracle, state, args, result):
        slot = args[0]
        if not (0 <= slot < len(slots) and probe(oracle)._loadout_clickable(kind)):
            return
        _regs(oracle, d2=2, a3=slots[slot])
        _call(oracle, slot_handler)
        if not oracle.uc.mem_read(MODAL, 1)[0]:
            return
        x0, y0, x1, y1 = STORES_BOX
        _oracle_release(oracle, (x0 + x1) // 2, (y0 + y1) // 2)
    return fit, unfit


_MFIT, _MUNFIT = _oracle_fit("Manta")
_WFIT, _WUNFIT = _oracle_fit("Walrus")


def _drop_point(r, m):
    """Over a slot of either screen, the stores box, or elsewhere."""
    pts = [((x0 + x1) // 2, (y0 + y1) // 2) for _, x0, y0, x1, y1 in SLOT_ELEMENTS.values()]
    pts += [((STORES_BOX[0] + STORES_BOX[2]) // 2, (STORES_BOX[1] + STORES_BOX[3]) // 2),
            (STORES_BOX[0], STORES_BOX[3]), (STORES_BOX[2] + 1, 100), (r.randrange(320), r.randrange(200))]
    return r.choice(pts)


_bay = lambda r, m: (r.choice((0, 1, 2, 3, 0, 1, 2, 3, -1, 4)),)
_w = lambda r, m: (r.choice((0, 1, 2, 0, 1, 2, 3)),)
_held = lambda r, m: (r.choice((0, 0, 1)),)
_sy = lambda r, m: (r.randrange(150, 205),)
_wp = lambda r, m: (r.randrange(-0x900000, 0x900000), r.randrange(-0x900000, 0x900000))
_slot3 = lambda r, m: (r.choice((0, 1, 2, 0, 1, 2, 3)),)
_slot2 = lambda r, m: (r.choice((0, 1, 0, 1, 2)),)

# name: (oracle action, argument generator(rnd, mem), model call or None)
VCOMMANDS = {
    # Manta control (27)
    "cmd_manta_select": (_el(0xFE54, 0x422AC), _bay, None),
    "cmd_manta_weapon": (_el(0xFEE2, 0x423D8), _w, None),
    "cmd_manta_autopilot": (_el(0x12A44, 0x4241A, 0), None, None),
    "cmd_manta_centre_rudder": (_el(0x129E6), None, None),
    "cmd_manta_centre_pitch": (_el(0x129FE), None, None),
    "cmd_manta_instruments": (_el(0x100A8), None, None),
    "cmd_manta_throttle": (_y(0xFDDA, 290), _sy, None),
    "cmd_manta_sight": (_el(0x10100, 0x42318, 0), None, None),
    "cmd_manta_rear_view": (_el(0x1015C, 0x4232E, 0), None, None),
    "cmd_manta_runway_take_off": (_el(0x13356), None, None),
    # Manta deck (26) and map (25)
    "cmd_manta_deck_select": (_el(0x11F92, 0x42B52), _bay, None),
    "cmd_manta_abort_landing": (_el(0x12360), None, None),
    "cmd_manta_map_select": (_el(0xFF1E, 0x41B62), _bay, None),
    "cmd_manta_set_course": (_el(0x12FC4, d2=lambda a: a[0]), _held, None),
    "cmd_manta_clear_course": (_el(0x12F8E), None, None),
    "cmd_manta_cruise_up": (_el(0x12C90), None, None),
    "cmd_manta_cruise_down": (_el(0x12CA2), None, None),
    "cmd_manta_cruise": (_y(0x12CB4, 290), _sy, None),
    "cmd_manta_altitude_up": (_el(0x12CE2), None, None),
    "cmd_manta_altitude_down": (_el(0x12CF6), None, None),
    "cmd_manta_altitude": (_y(0x12D0A, 246), _sy, None),
    "cmd_manta_map_draw_mode": (_el(0x12D40, 0x41D82, 0), None, None),
    "cmd_manta_toggle_announce": (_el(0x12D50, 0x41D98, 0), None, None),
    "cmd_set_manta_waypoint": (_cursor_then(0x12FC4, (0x69E, 0x6A6)), _wp, None),
    # Manta arming (24)
    "cmd_manta_arming_select": (_el(0x116C6, 0x428B4), _bay, None),
    "cmd_manta_arming_to_deck": (_el(0x11886, 0x4294E, 0), None, None),
    "cmd_manta_loadout_next": (_el(0x1132E), None, None),
    "cmd_manta_loadout_prev": (_el(0x11352), None, None),
    "cmd_manta_pick_loadout": (_el(0x1135E), None, None),
    "cmd_manta_pick_slot": (lambda o, st, a, res: None if res is False else (_regs(o, d2=2, a3=MANTA_SLOTS[a[0]]), _call(o, 0x114C8)),
                            _slot3, None),
    "cmd_manta_fit": (_MFIT, lambda r, m: (r.choice((0, 1, 2, 0, 3)), r.choice((1, 2, 3, 4, 5, 10, 0))), None),
    "cmd_manta_unfit": (_MUNFIT, _slot3, None),
    # Walrus control (18)
    "cmd_walrus_select": (_el(0x10016, 0x42482), _bay, None),
    "cmd_walrus_weapon": (_el(0xFF7C, 0x425AE), _w, None),
    "cmd_walrus_autopilot": (_el(0x12B00, 0x425F0, 0), None, None),
    "cmd_walrus_centre_rudder": (_el(0x12982, d2=lambda a: a[0]), _held, None),
    "cmd_walrus_turret_mode": (_el(0x129A2, 0x42584, 0), None, None),
    "cmd_walrus_instruments": (_el(0x100D4), None, None),
    "cmd_walrus_throttle": (_y(0xFE0A, 290), _sy, None),
    "cmd_walrus_sight": (_el(0x1012E, 0x424EE, 0), None, None),
    "cmd_walrus_rear_view": (_el(0x10190, 0x42504, 0), None, None),
    # Walrus dock (19) and map (17)
    "cmd_walrus_dock_select": (_el(0x1249E, 0x42C2C), _bay, None),
    "cmd_walrus_abort_docking": (_el(0x128AE), None, None),
    "cmd_walrus_map_select": (_el(0xFFB8, 0x41C50), _bay, None),
    "cmd_walrus_set_course": (_el(0x13122, d2=lambda a: a[0]), _held, None),
    "cmd_walrus_clear_course": (_el(0x130EE), None, None),
    "cmd_walrus_cruise_up": (_el(0x12D98), None, None),
    "cmd_walrus_cruise_down": (_el(0x12DAA), None, None),
    "cmd_walrus_cruise": (_y(0x12DBC, 290), _sy, None),
    "cmd_walrus_map_draw_mode": (_el(0x12DEC, 0x41E30, 0), None, None),
    "cmd_walrus_toggle_announce": (_el(0x12DFC, 0x41E46, 0), None, None),
    "cmd_set_walrus_waypoint": (_cursor_then(0x13122, (0x6B0, 0x6B4)), _wp, None),
    # Walrus arming (16)
    "cmd_walrus_arming_select": (_el(0x11DEE, 0x4271A), _bay, None),
    "cmd_walrus_loadout_next": (_el(0x11A30), None, None),
    "cmd_walrus_loadout_prev": (_el(0x11A76), None, None),
    "cmd_walrus_loadout_plus": (lambda o, st, a, res: None if res is False else (_regs(o, d2=2, a3=0x42820), _call(o, _u32(o, PLUS_HANDLER))), None, None),
    "cmd_walrus_loadout_minus": (lambda o, st, a, res: None if res is False else (_regs(o, d2=2, a3=0x4280A), _call(o, _u32(o, MINUS_HANDLER))), None, None),
    "cmd_walrus_refuel": (_el(0x11D4C), None, None),            # stunits_vehicles; here for the panel redraw
    "cmd_walrus_defuel": (_el(0x11DA4), None, None),
    "cmd_walrus_pick_loadout": (_el(0x11A82), None, None),
    "cmd_walrus_pick_slot": (lambda o, st, a, res: None if res is False else (_regs(o, d2=2, a3=WALRUS_SLOTS[a[0]]), _call(o, 0x11C16)),
                             _slot2, None),
    "cmd_walrus_fit": (_WFIT, lambda r, m: (r.choice((0, 1, 0, 1, 2)), r.choice((6, 7, 8, 9, 11, 1))), None),
    "cmd_walrus_unfit": (_WUNFIT, _slot2, None),
    # drags, raw mouse
    "cmd_drag_release": (lambda o, st, a, res: _oracle_release(o, *a), _drop_point, _release),
    "cmd_manta_steer_to": (_copy_input, lambda r, m: (r.randrange(-300, 300), r.randrange(-300, 300)), None),
    "cmd_walrus_steer_to": (_copy_input, lambda r, m: (r.randrange(-300, 300),), None),
    "cmd_walrus_missile_steer_to": (_copy_input, lambda r, m: (r.randrange(-300, 300), r.randrange(-300, 300)), None),
    "cmd_vehicle_fire": (_copy_input, None, None),
}
# Pick-ups that start a drag are followed by a release in the checks (the drop runs in ui_frame).
DRAG_STARTS = ("cmd_manta_pick_loadout", "cmd_manta_pick_slot", "cmd_walrus_pick_loadout", "cmd_walrus_pick_slot")


def apply_vcommand(state, oracle, name: str, rnd):
    """A vehicle command on the model and what the original does for it."""
    act, gen, call = VCOMMANDS[name]
    args = gen(rnd, state.mem) if gen else ()
    result = call(state, *args) if call else getattr(state, name)(*args)
    act(oracle, state, args, result)
    return args, result


def _perturb_vehicles(m, rnd) -> None:
    """_perturb_screens plus the vehicle screens' state: a vehicle screen, the select and
    button icons' flags, controllable flags, records (flags, modes, loadout codes, weights,
    Harbingers), stocks, landing/docking in progress, the map edit values, list cursors,
    drags, number keys, gauges and the lock timer."""
    _perturb_screens(m, rnd)
    screen = rnd.choice(VEHICLE_SCREENS)
    m.set16(stunits.SCREEN, screen)
    m.set16(TAB, TAB_OF[screen])
    m.set16(0x702, 0 if rnd.random() < 0.9 else 1)
    odd = lambda v: rnd.choice((0x4000, 0x8000)) if rnd.random() < 0.04 else v
    for base in (T_MANTA, T_WALRUS, T_MANTA_ARMING, T_WALRUS_ARMING, T_MANTA_DECK, T_WALRUS_DOCK):
        lit = rnd.randrange(5)
        for i in range(4):
            m.set16(FLAG_TABLE + 2 * (base + i), odd(0x2000 if i == lit else 0))
    for t in (T_BUTTON, T_HANGAR, T_HARBINGERS, T_AUTOPILOT, T_DRAW, T_ANNOUNCE, T_SIGHT, T_REAR, T_TURRET, 62, 63, 64):
        m.set16(FLAG_TABLE + 2 * t, odd(rnd.choice((0, 0, 0x2000))))
    for a in (0x70C, 0x70E):
        m.set16(a, rnd.choice((0, 1, 1)))
    m.set16(MAP_INFO, rnd.choice((0, 0, 0, 0, 1)))
    m.set16(0xDC0, rnd.choice((0, 0, 0, 1, 2)))
    m.set16(0xDC2, rnd.choice((0, 0, 0, 1, 2)))
    m.set16(LOADOUT_CURSOR, rnd.choice((0, 1, 2, 3, 4, 5, 5, 0xFFFF)) if rnd.random() < 0.97 else 6)
    m.set16(LOADOUT_CURSOR + 2, rnd.choice((0, 1, 2, 3, 4, 4, 0xFFFF)) if rnd.random() < 0.97 else 5)
    m.set16(HARBINGERS, rnd.choice((0, 1, 2, 3, 9)))
    for a in STOCKS:
        m.set16(a, rnd.choice((0, 0, 1, 2, 3, 5, 12)))
    m.set16(0xDC6, rnd.randrange(3))
    m.set32(0xD04, rnd.choice((0, 0x10000, 0xFFFE0)))     # AAV fuel tank (refuel / defuel redraw the panel)
    plus = rnd.choice((0x11CE0, 0x11D1C))
    m.set32(PLUS_HANDLER, plus)
    m.set32(MINUS_HANDLER, 0x11D0C if plus == 0x11CE0 else 0x11D40)
    for k in range(4):
        r = MANTA_RECS + REC * k
        m.set8(r, rnd.choice((0, 1, 2, 3, 0x41, 0x82, 0x13, 0xC2, rnd.randrange(256))))
        m.set8(r + 0x1C, rnd.choice((0, 1, 2, 3, 3, 4, 5, 6)))
        m.set8(r + 0x1D, rnd.randrange(7))
        m.set16(r + 0x0C, rnd.choice((0, 1, 2, 3, 4)))
        m.set16(r + 0x0E, rnd.choice((0, 1, 2, 3, 4)))
        m.set16(r + 0x10, rnd.choice((0, 1, 4, 5, 10)))
        m.set16(r + 0x12, rnd.choice((0, 300, 600, 700, 750)))
        m.set16(r + 0x18, rnd.choice((0, 1, 2, 3)))
        m.set16(r + 2, rnd.randrange(-255, 256))
        m.set16(r + 4, rnd.randrange(-255, 256))
        w = WALRUS_RECS + REC * k
        m.set8(w, rnd.choice((0, 1, 2, 3, 0x21, 0x23, 0x61, 0x13, 0x10, rnd.randrange(256))))
        m.set8(w + 0x1A, rnd.choice((0, 0, 2, 3, 3)))
        m.set8(w + 0x1B, rnd.choice((0, 0, 1, 2)))
        m.set16(w + 0x0C, rnd.choice((0, 1, 3, 9)))
        m.set16(w + 0x26, rnd.choice((0, 8, 9, 11, 6)))
        m.set16(w + 0x28, rnd.choice((0, 6, 7, 7, 9)))
        m.set16(w + 0x12, rnd.choice((0, 400, 1500, 1900, 2000)))
        m.set16(w + 0x16, rnd.choice((0, 1, 2, 3)))
        m.set16(w + 2, rnd.randrange(-255, 256))
        m.set16(w + 8, rnd.choice((0, 100, 0x7000, 0x7FFF)))
    for a in (0x1232, 0x1233):
        m.set8(a, rnd.choice((0, 0xFF)))
    m.set16(0xDDC, rnd.randrange(4))
    m.set16(0x6E0, rnd.randrange(4))
    m.set16(0x69C, rnd.choice((0, 50, 100, 240, 250, 300)))
    m.set32(0x6A2, rnd.choice((0, 250, 1000, 3400, 3500, 4000, -100)))
    m.set16(0x6AE, rnd.choice((0, 8, 60, 96, 120)))
    for a in (0x69E, 0x6A6, 0x6B0, 0x6B4):
        m.set32(a, rnd.randrange(-0x400000, 0x400000))
    for a in (0x6AA, 0x6B8):
        m.set16(a, rnd.choice((0, 1, 0x100, 0x101)))
    for a in (0x6AC, 0x6BA, 0x8F1):
        m.set8(a, rnd.choice((0, 0xFF)))
    m.set16(0x116C, rnd.choice((0, 0, 1, 25)))
    for t in (0x954, 0x956, 0x958, 0x962):
        m.set16(t, rnd.choice((0, 0, 2)))
    m.set16(0xDC8, rnd.choice((0x6000, 0x8000, 0xA000, 0x9FF0, 0x6010)))
    m.set16(0xDCA, rnd.choice((0x100, 0xFF00, 0x40, 0xFFC0)))
    mk, wk = rnd.randrange(4), rnd.randrange(4)
    m.set16(0x48C, mk)
    m.set16(0x566, wk)
    m.set32(0x48E, MANTA_RECS + REC * (mk if rnd.random() < 0.8 else rnd.randrange(4)))
    m.set32(0x492, MANTA_OBJS + 0x140 * (mk if rnd.random() < 0.8 else rnd.randrange(4)))
    m.set32(0x568, WALRUS_RECS + REC * (wk if rnd.random() < 0.8 else rnd.randrange(4)))
    m.set32(0x56C, WALRUS_OBJS + 0x140 * (wk if rnd.random() < 0.8 else rnd.randrange(4)))
    # The arming screens: their bay ($DB4 / $DB6) mostly the selected record, as the original
    # keeps it on screens 24 / 16, in any status; elsewhere sometimes too.
    for sel, cur, base, arming in ((0xDB4, 0x48E, MANTA_RECS, 24), (0xDB6, 0x568, WALRUS_RECS, 16)):
        k = rnd.randrange(4)
        m.set16(sel, k)
        if rnd.random() < (0.9 if screen == arming else 0.7):
            m.set32(cur, base + REC * k)
            m.set8(base + REC * k + 1, rnd.choice((0, 0, 0, 0, 0, 0, 1, 2, 3, 8, 0xFF)))
    for k in range(4):                     # out (or lost): weapon counts and flags, not codes ($1ED46)
        r = MANTA_RECS + REC * k
        if m.u8(r + 1) in (8, 0xFF) and rnd.random() < 0.7:
            m.set16(r + 0x0C, rnd.choice((0, 1, 2, 3, 6)))
            m.set8(r + 0x10, rnd.choice((0, 0xFF)))
            m.set8(r + 0x11, rnd.randrange(256))
    if screen == 27:
        m.set32(stunits.VIEWED, MANTA_OBJS + 0x140 * mk)
    elif screen == 18:
        m.set32(stunits.VIEWED, WALRUS_OBJS + 0x140 * wk)
    if rnd.random() < 0.3:                                 # the selected Walrus steering a Harbinger
        w = WALRUS_RECS + REC * wk
        m.set8(w, m.u8(w) | 0x10)
        m.set32(WALRUS_OBJS + 0x140 * wk + 0x20, stunits.OBJECTS + stunits.OBJECT_SIZE * rnd.randrange(20, 64))
    m.set8(MODAL, 0xFF if rnd.random() < 0.05 else 0)
    m.set32(DROP, rnd.choice(list(DROPS)))
    m.set32(DRAG_ELEMENT, rnd.choice(list(SLOT_ELEMENTS)))
    m.set8(0x880, 0)
    m.set8(0x881, 0)
    for a in MAP_KEYPAD:
        m.set8(a, 1 if rnd.random() < 0.06 else 0)
    m.set8(0x883, 1 if rnd.random() < 0.2 else 0)


def _vehicle_states(files) -> list:
    """Action captures and the play captures (Manta and Walrus out, landing, docking)."""
    from stgfx import ROOT
    play = sorted((ROOT / "emu" / "out" / "play").glob("pass_*_in.bin"))
    return list(files[::6]) + play[::12]


def check_vehicle_commands(files) -> bool:
    """Every Manta / Walrus command against its original click handler(s) (registers as the
    hit test passes them: d0/d1 pointer, d2 = 2 or 1, a3 the real panel element; drags
    released over slots or the stores box and dropped by ui_frame) on perturbed captured
    states."""
    import random
    import check_sim
    rnd = random.Random(53)
    ok = True
    states = _vehicle_states(files)
    for name in VCOMMANDS:
        runs, stopped = 0, 0
        # the loadout panels act only on the arming bay's record in the hangar: more tries
        reps = 8 if any(w in name for w in ("loadout", "fit", "pick", "arming")) else 3
        for f in states:
            for _ in range(reps):
                m = stsim.Mem(bytearray(f.read_bytes()))
                _perturb_vehicles(m, rnd)
                ram = bytes(m.buf)
                oracle = check_sim.Oracle(ram)
                state = stunits.UnitState.from_ram(ram)
                try:
                    args, _ = apply_vcommand(state, oracle, name, rnd)
                    if name in DRAG_STARTS and state.mem.u8(MODAL):
                        px, py = _drop_point(rnd, state.mem)
                        _release(state, px, py)
                        _oracle_release(oracle, px, py)
                except NotImplementedError:
                    stopped += 1
                    continue
                runs += 1
                if not _compare(state, oracle, f"{name}{args} from {f.name} (screen {m.u16(stunits.SCREEN)})"):
                    ok = False
                    break
            else:
                continue
            break
        else:
            print(f"{name}: OK ({runs} states{f', {stopped} stopped at unported code' if stopped else ''})")
    return ok


def check_vehicle_input(files) -> bool:
    """screen_input ($17094) on the vehicle screens 16-19 and 24-27 against the original:
    number keys (pointer mode, drags), re-entry when a unit's status crosses 8, gauges, the
    auto button sync, the right button, the lock timer and the hangar animation."""
    import random
    from collections import Counter
    import check_sim
    rnd = random.Random(59)
    seen = Counter()
    for f in _vehicle_states(files):
        for _ in range(16):
            m = stsim.Mem(bytearray(f.read_bytes()))
            _perturb_vehicles(m, rnd)
            if rnd.random() < 0.3:
                m.set8(MODAL, 0)
                m.set8(stunits.POINTER_MODE, 0xFF)
                m.set8(rnd.choice(MAP_KEYPAD), 1)
            ram = bytes(m.buf)
            state = stunits.UnitState.from_ram(ram)
            try:
                state.screen_input()
            except NotImplementedError:
                continue
            oracle = check_sim.Oracle(ram)
            _call(oracle, 0x17094)
            seen[m.u16(stunits.SCREEN)] += 1
            if not _compare(state, oracle, f"screen_input on {m.u16(stunits.SCREEN)} from {f.name}"):
                return False
    print(f"vehicle screen input: OK ({dict(sorted(seen.items()))})")
    return True


def check_vehicle_runs(files) -> bool:
    """Play: from captures with Mantas and Walruses out (and the action captures), switch to
    a vehicle screen by clicks, then 120 passes of ui_frame + game_frame on both, with random
    vehicle commands, drags, mouse steering, firing, throttle keys and right clicks between
    passes (the original running its own ui_frame and screen_input)."""
    import random
    rnd = random.Random(61)
    from stgfx import ROOT
    play = sorted((ROOT / "emu" / "out" / "play").glob("pass_*_in.bin"))
    states = play[::8] + list(files[::15])
    names = [n for n in VCOMMANDS if n != "cmd_drag_release"]
    from collections import Counter
    stopped, passes, runs, reached = {}, 0, 0, Counter()
    for i, f in enumerate(states):
        m = stsim.Mem(bytearray(f.read_bytes()))
        m.set8(RUNNING, 0xFF)
        for a in (MENU, MODAL, 0x880, 0x881, 0x882, 0x883, SCREEN_REQUEST, 0x704, 0x706, 0x702):
            m.set8(a, 0)
        m.set16(0x702, 0)
        for s in range(29):                                  # icons lit as the screen shown
            a = FLAG_TABLE + 2 * s
            m.set8(a, (m.u8(a) & 0xDF) | (0x20 if s == m.u16(stunits.SCREEN) else 0))
        for k in range(5):
            a = FLAG_TABLE + 2 * (T_TAB + k)
            m.set8(a, (m.u8(a) & 0xDF) | (0x20 if k == m.u16(TAB) else 0))
        ram = bytes(m.buf)
        state, oracle = stunits.UnitState.from_ram(ram), _switch_oracle(ram)
        target = VEHICLE_SCREENS[i % len(VEHICLE_SCREENS)]
        state.cmd_switch_screen(target)
        _oracle_switch(oracle, target)
        if not _compare(state, oracle, f"switch to {target} from {f.name}"):
            return False
        reached[state.mem.u16(stunits.SCREEN)] += 1
        runs += 1
        for p in range(120):
            try:
                r = rnd.random()
                mm = state.mem
                if mm.u8(MODAL):
                    if r < 0.5:
                        px, py = _drop_point(rnd, mm)
                        state.cmd_drag_release(px, py)
                        oracle.uc.mem_write(0x474, px.to_bytes(2, "big") + py.to_bytes(2, "big"))
                        oracle.uc.mem_write(0x880, b"\x00")
                elif r < 0.3:
                    apply_vcommand(state, oracle, rnd.choice(names), rnd)
                elif r < 0.45 and not mm.u8(stunits.POINTER_MODE):
                    name = rnd.choice(("cmd_manta_steer_to", "cmd_walrus_steer_to", "cmd_walrus_missile_steer_to", "cmd_vehicle_fire"))
                    apply_vcommand(state, oracle, name, rnd)
                elif r < 0.55:
                    sc, down = rnd.choice((0x48, 0x50, 0x02, 0x03, 0x04, 0x05, 0x6D, 0x6E)), rnd.choice((0, 1))
                    state.input_key(sc, down)
                    _copy_input(oracle, state, (), None)
                elif r < 0.6:
                    state.cmd_toggle_mouse_steer()
                    _copy_input(oracle, state, (), None)
                if p % 8 == 0:
                    oracle.uc.mem_write(0x8FD, b"\xff")
                    state.mem.set8(0x8FD, 0xFF)
                if state.mem.u8(stunits.POINTER_MODE):          # no click for the original's hit test
                    state.mem.set8(0x881, 0)
                    oracle.uc.mem_write(0x881, b"\x00")
                state.ui_frame()
                if state.mem.u16(stunits.SCREEN) not in SCREEN_INPUT:
                    state._vec = None           # tools/export_sim_vectors.py: stop recording (the stub is check-only)
                with _stub_inputs():
                    state.game_frame()
            except STOP as e:
                stopped[str(e) or type(e).__name__] = stopped.get(str(e) or type(e).__name__, 0) + 1
                break
            _call(oracle, UI_FRAME)
            oracle_game_frame(oracle)
            passes += 1
            if not _compare(state, oracle, f"run from {f.name} on {target}, pass {p}"):
                return False
    print(f"vehicle runs: OK ({runs} runs on screens {dict(sorted(reached.items()))}, {passes} passes)")
    for k, v in sorted(stopped.items(), key=lambda kv: -kv[1]):
        print(f"  stopped at unported code {v}x: {k}")
    return True


# ---- milestone 3 "War" checks: weapons, resources, messages, options, title -------------------
WAR_SCREENS = (3, 4, 8, 9, 10, 11, 12, 32)


def _pointer_call(handler: int, point, a3: int = 0):
    """An oracle action: the handler with d0/d1 = point(args), d2 = 2, a3."""
    def act(oracle, state, args, result):
        if result is False:
            return
        px, py = point(args)
        _regs(oracle, d0=px, d1=py, d2=2, a3=a3(args) if callable(a3) else a3)
        _call(oracle, handler)
    return act


def _keypad_point(args):
    x0, y0, x1, y1 = KEYPAD_BOXES[args[0]]
    return KEYPAD[0] + (x0 + x1) // 2, KEYPAD[1] + (y0 + y1) // 2


def _fuel_call(handlers):
    def act(oracle, state, args, result):
        if result is not False:
            _regs(oracle, d2=2)
            _call(oracle, handlers[args[0]])
    return act


def _message_log(oracle, state, args, result) -> None:
    if result is False:
        return
    oracle.uc.mem_write(0x1148, (0x65D10 if args[0] else 0).to_bytes(4, "big"))
    _regs(oracle, d2=2)
    _call(oracle, 0x12C2C)


def _oracle_station_move(oracle, state, args, result) -> None:
    """cmd_drone_station_move click by click on the original (decided from its own state)."""
    k, px, py = args
    probe = stunits.UnitState.from_ram(bytes(oracle.ram()))
    if not (0 <= k <= 3 and probe._clickable(T_STATION + k)):
        return
    _regs(oracle, d2=2, a3=0x42A4E + 0x16 * k)
    _call(oracle, 0x104C8)
    if oracle.uc.mem_read(MODAL, 1)[0]:
        _oracle_release(oracle, px, py)


def _radar_point(r, m):
    cx, cy = m.s16(0x4750), m.s16(0x4752)
    return r.randrange(cx - 70, cx + 70), r.randrange(cy - 50, cy + 50)


_row = lambda r, m: (r.choice((0, 1, 2, 0, 1, 2, 3, -1)),)
_row_cls = lambda r, m: (r.choice((0, 1, 2, 0, 1, 2, 3)), r.choice((0, 1, 2, 0, 1, 2, 3)))
_row_held = lambda r, m: (r.choice((0, 1, 2, 0, 1, 2, 3)), r.choice((0, 0, 1)))
_fuel_k = lambda r, m: (r.choice((0, 1, 2, 0, 1, 2, 3)),)

# name: (oracle action, argument generator(rnd, mem), model call or None)
WCOMMANDS = {
    # bridge (0)
    "cmd_bridge_zoom_in": (_el(0xFCE8), None, None),
    "cmd_bridge_zoom_out": (_el(0xFCF8), None, None),
    # laser turret (8)
    "cmd_turret_up": (_el(0xFD3C), None, None),
    "cmd_turret_down": (_el(0xFD44), None, None),
    "cmd_turret_left": (_el(0xFD2C), None, None),
    "cmd_turret_right": (_el(0xFD34), None, None),
    "cmd_turret_fire": (_el(0xFD60), None, None),
    "cmd_turret_zoom_in": (_el(0xFC9C), None, None),
    "cmd_turret_zoom_out": (_el(0xFCAC), None, None),
    "cmd_turret_centre_heading": (_el(0x12A34), None, None),
    "cmd_turret_centre_elevation": (_el(0x12A3C), None, None),
    "cmd_turret_steer_to": (_copy_input, lambda r, m: (r.randrange(-300, 300), r.randrange(-300, 300)), None),
    # flares (9), viewing drone (10), defence drones (11)
    "cmd_flare_launch": (_el(0x1338C), None, None),
    "cmd_recon_launch": (_el(0x134C2), None, None),
    "cmd_ssm_fire_at": (_with_pointer(0x13598), lambda r, m: (r.randrange(20, 300), r.randrange(0, 150)), None),
    "cmd_drones_launch": (_el(0x10738), None, None),
    "cmd_drones_recall": (_el(0x1088C), None, None),
    "cmd_drone_stations_reset": (_el(0x10602), None, None),
    "cmd_drone_formation_next": (_el(0x106EC), None, None),
    "cmd_drone_formation_prev": (_el(0x10706), None, None),
    "cmd_drone_formation_apply": (_el(0x10714), None, None),
    "cmd_drone_station_pick": (_el(0x104C8, 0x42A4E), lambda r, m: (r.choice((0, 1, 2, 3, 0, 1, 2, 3, -1, 4)),), None),
    "cmd_drone_station_move": (_oracle_station_move,
                               lambda r, m: (r.choice((0, 1, 2, 3, 0, 1, 2, 3, 4)),) + _radar_point(r, m), None),
    # resources (3)
    "cmd_resupply_abort": (_el(0x1102E), None, None),
    "cmd_resources_transfer": (_el(0x10E4A), None, None),
    "cmd_item_picture": (_el(0x1098A, 0x42D72), lambda r, m: (r.choice((0, 3, 7, 14, r.randrange(15), -1, 15)),), None),
    "cmd_item_picture_back": (_el(0x109BC), None, None),
    "cmd_stores_priorities_open": (_el(0x109E8), None, None),
    "cmd_stores_priorities_reset": (_el(0x10A66), None, None),
    "cmd_stores_priorities_exit": (_el(0x10A3C), None, None),
    "cmd_stores_scroll_up": (_el(0x10AA6), None, None),
    "cmd_stores_scroll_down": (_el(0x10A8A), None, None),
    "cmd_stores_class": (_pointer_call(0x10ABC, lambda a: (90, 24 + 36 * a[0] + CLASS_Y[a[1]]), lambda a: 0x42F26 + 0x16 * a[0]),
                         _row_cls, None),
    "cmd_stores_target": (_el(0x10B02, 0x42F68), _row, None),
    "cmd_keypad": (_pointer_call(0x11196, _keypad_point, 0x42FD6), lambda r, m: (r.randrange(-1, 14),), None),
    "cmd_fuel_priorities_open": (_el(0x10B72), None, None),
    "cmd_fuel_priorities_reset": (_el(0x10BC6), None, None),
    "cmd_fuel_priorities_exit": (_el(0x10BAC), None, None),
    "cmd_fuel_class": (_pointer_call(0x10BDA, lambda a: (146, 24 + 36 * a[0] + CLASS_Y[a[1]]), lambda a: 0x4302C + 0x16 * a[0]),
                       _row_cls, None),
    "cmd_stores_queue_open": (_el(0x10C06), None, None),
    "cmd_fuel_queue_open": (_el(0x10D52), None, None),
    "cmd_stores_queue_back": (_el(0x10C62), None, None),
    "cmd_fuel_queue_back": (_el(0x10D9C), None, None),
    "cmd_stores_queue_scroll_up": (_el(0x10C7C), None, None),
    "cmd_stores_queue_scroll_down": (_el(0x10C6A), None, None),
    "cmd_stores_unqueue": (_el(0x10C88, 0x430C4, 0x2C, d2=lambda a: a[1]), _row_held, None),
    "cmd_stores_queue": (_el(0x10CCA, 0x430DA, 0x2C, d2=lambda a: a[1]), _row_held, None),
    "cmd_fuel_unqueue": (_fuel_call((0x10DA4, 0x10E18, 0x10E30)), _fuel_k, None),
    "cmd_fuel_queue": (_fuel_call((0x10DC0, 0x10E22, 0x10E3C)), _fuel_k, None),
    # carrier map (2), islands panel
    "cmd_set_stockpile": (_el(0x137C2), None, None),
    # messages (4), options (32)
    "cmd_clear_message_log": (_message_log, lambda r, m: (r.randrange(2),), None),
    "cmd_game_option": (_pointer_call(0x13778, lambda a: (0xFC if a[1] else 0xE6, 29 + 16 * a[0] + 7)),
                        lambda r, m: (r.choice((0, 1, 2, 3, 0, 1, 2, 3, 4, 5)), r.randrange(2)), None),
}
WDRAG_STARTS = ("cmd_drone_station_pick",)
# The resources sub-panel each command's panel needs (the checks bias the state towards it).
RES_MODE = {**{n: 0 for n in ("cmd_resupply_abort", "cmd_resources_transfer", "cmd_item_picture",
                              "cmd_stores_priorities_open", "cmd_fuel_priorities_open", "cmd_stores_queue_open",
                              "cmd_fuel_queue_open")},
            "cmd_item_picture_back": 1,
            **{n: 2 for n in ("cmd_stores_priorities_reset", "cmd_stores_priorities_exit", "cmd_stores_scroll_up",
                              "cmd_stores_scroll_down", "cmd_stores_class", "cmd_stores_target", "cmd_keypad")},
            **{n: 3 for n in ("cmd_fuel_priorities_reset", "cmd_fuel_priorities_exit", "cmd_fuel_class")},
            **{n: 4 for n in ("cmd_stores_queue_back", "cmd_stores_queue_scroll_up", "cmd_stores_queue_scroll_down",
                              "cmd_stores_unqueue", "cmd_stores_queue")},
            **{n: 5 for n in ("cmd_fuel_queue_back", "cmd_fuel_unqueue", "cmd_fuel_queue")}}


def apply_wcommand(state, oracle, name: str, rnd):
    act, gen, call = WCOMMANDS[name]
    args = gen(rnd, state.mem) if gen else ()
    result = call(state, *args) if call else getattr(state, name)(*args)
    act(oracle, state, args, result)
    return args, result


def _perturb_war(m, rnd) -> None:
    """_perturb_screens plus the war screens' state: a screen 3, 4, 8-12 or 32, the button
    flags, the turret (zoom, angles, rates, keys), the bridge zoom, the drones (object 15, the
    stations, formation, recall), the weapon stocks and timers, the SSM, the resources panels
    (sub-panel, keypad entry, scrolls, items, stocks and queues, fuel, comms, transfer state,
    the stockpile), the game-status mode and the options."""
    _perturb_screens(m, rnd)
    screen = rnd.choice(WAR_SCREENS)
    m.set16(stunits.SCREEN, screen)
    m.set16(TAB, TAB_OF[screen])
    m.set16(0x702, 0 if rnd.random() < 0.9 else 1)
    odd = lambda v: rnd.choice((0x4000, 0x8000)) if rnd.random() < 0.04 else v
    for t in (T_BUTTON, 66, 67, 68, 69):
        m.set16(FLAG_TABLE + 2 * t, odd(rnd.choice((0, 0, 0x2000))))
    m.set16(0x6D2, rnd.choice((0, 1, 2, 3, 3)) if rnd.random() < 0.95 else rnd.choice((4, 0xFFFF, 20)))
    for a in (0x6CA, 0x6CC):
        m.set16(a, rnd.randrange(0x10000))
    for a in (0x6CE, 0x6D0):
        m.set16(a, rnd.randrange(-255, 256))
    m.set8(0x6D5, rnd.choice((0, 0x40, 0x80, 0xC0)))
    m.set16(0x8B4, rnd.choice((0, 1, 2, 3, 4)) if rnd.random() < 0.95 else rnd.choice((5, 0xFFFF)))
    m.set16(0x968, rnd.choice((0, 0, 2)))
    for a in (0x828, 0x838, 0x82E, 0x832, 0x83D, 0x827, 0x883):
        m.set8(a, 1 if rnd.random() < 0.15 else 0)
    o15 = stunits.OBJECTS + stunits.OBJECT_SIZE * 15
    if rnd.random() < 0.5:
        m.set16(o15, 0x8403)
        m.set32(o15 + 0x0E, rnd.choice((200, 1500, 3000)))
        for off in (0x0A, 0x12):
            m.set32(o15 + off, m.u32(stunits.CARRIER + off) + rnd.randrange(-4000, 4000))
    else:
        m.set8(o15 + 1, m.u8(o15 + 1) & 0xFE)
    m.set8(0x1160, rnd.choice((0, 0, 0xFF)))
    m.set16(0x4778, rnd.choice((0, 1, 2, 3)))
    for a, v in ((0xC82, (0, 1, 5)), (0xC9C, (0, 1)), (0xBB2, (0, 3)), (0xC1A, (0, 2, 4))):
        m.set16(a, rnd.choice(v))
    for a in (0x1150, 0x115E):
        m.set16(a, rnd.choice((0, 0, 7)))
    for a in (0xB22, 0xB2A, 0xB12):
        m.set8(a, rnd.choice((0xFF, 0xFF, 0)))
    m.set16(0xB1A, rnd.choice((0x100, 0x100, 0x80, 0)))
    m.set8(0x122F, rnd.choice((0, 0, 0, 0xFF)))
    active = 0
    for k in range(4):
        rec = 0x65E + 14 * k
        on = rnd.random() < 0.4
        active += on
        m.set16(rec, rnd.choice((1, 3, 5)) if on else rnd.choice((0, 2)))
        for off in (2, 4, 0xA, 0xC):
            m.set16(rec + off, rnd.choice((0, -1500, -1884, rnd.randrange(-3000, 3000))))
    m.set16(0x65C, active)
    m.set16(0x698, rnd.randrange(9))
    m.set16(0x696, rnd.randrange(4))
    # resources
    m.set16(RESOURCES, rnd.randrange(6))
    m.set8(0xB0A, rnd.choice((0xFF, 0xFF, 0xFF, 0)))
    entry = rnd.random() < 0.4
    m.set8(ENTRY, 0xFF if entry else 0)
    m.set32(ENTRY_CALLBACK, ENTRY_DONE if entry or rnd.random() < 0.5 else 0)
    n = rnd.randrange(4)
    m.set16(ENTRY_COUNT, n)
    m.set16(ENTRY_MAX, 3)
    for i in range(4):
        m.set8(ENTRY_DIGITS + i, 0x30 + rnd.randrange(10) if i < n else 0)
    if n == 0:
        m.set8(ENTRY_DIGITS, 0x30)
    m.set16(ENTRY_OLD, rnd.randrange(1000))
    m.set16(ITEM_EDITED, rnd.randrange(3))
    m.set16(STORES_SCROLL, rnd.choice((0, 0, 5, 12, 12)))
    m.set16(QUEUE_SCROLL, rnd.choice((0, 0, 5, 12, 12)))
    m.set16(QUEUE_REPEAT, rnd.choice((0, 0, 1, 5)))
    for k in range(stsim.N_ITEMS):
        a = stsim.ITEMS + stsim.ITEM_SIZE * k
        for off in (0x10, 0x14):
            m.set16(a + off, rnd.randrange(3))
        for off in (0x12, 0x16):
            m.set16(a + off, rnd.choice((0, 5, 20, 999)))
        if rnd.random() < 0.5:
            m.set16(a + 0x18, rnd.choice((10, 50, 100)))
            m.set16(a + 8, rnd.choice((0, 5, 10, 40, 100)))
        m.set16(stsim.STORES + 2 * k, rnd.choice((0, 1, 7, 30, 200)))
        m.set16(stsim.STORES_PENDING + 2 * k, rnd.choice((0, 0, 1, 9, 30)))
    for k in range(3):
        m.set32(stsim.FUEL + 4 * k, rnd.choice((0, 0x1000, 0x10000, 0x80000)))
        m.set32(stsim.FUEL_PENDING + 4 * k, rnd.choice((0, 0x2000, 0x40000)))
        m.set32(0xCFC + 4 * k, rnd.choice((0, 0x10000, 0xFFFE0 - m.u32(stsim.FUEL_PENDING + 4 * k), 0xF0000)))
        m.set16(stsim.FUEL_CLASS + 2 * k, rnd.randrange(3))
        m.set16(FUEL_WORK + 2 * k, rnd.randrange(3))
    m.set8(0xD3E, rnd.choice((0, 0, 0xFF)))
    m.set8(0xD40, rnd.choice((0, 0xFF)))
    m.set16(0xD46, rnd.choice((0, 0, 1, 2)))
    m.set16(0xDE6, rnd.choice((0, 4)))
    buf = m.u32(stunits.CARRIER + 0x3C)
    if buf and rnd.random() < 0.6:
        m.set16(stsim.STOCKPILE, m.u8(buf + 1))
    elif rnd.random() < 0.3:
        m.set32(stunits.CARRIER + 0x3C, 0)
    m.set16(GAME_STATUS, rnd.choice((0, 7, 7, rnd.randrange(8))))
    for k in range(4):
        m.set8(OPTIONS + k, rnd.choice((0, 0xFF)))
    if rnd.random() < 0.3:
        m.set8(MODAL, 0)
    m.set32(DROP, rnd.choice((0x104E6, 0x104E6, rnd.choice(list(DROPS)))))
    # the stockpile island: the map centred on a player island with a route, nothing in transit
    m.set8(stsim.ROUTES_PENDING, rnd.choice((0, 0, 0, 0xFF)))
    m.set8(stsim.IN_TRANSIT, rnd.choice((0, 0, 0xFF)))
    if rnd.random() < 0.15:
        m.set16(stsim.STOCKPILE, 0xFFFF)
    mine = [i for i in range(stsim.ISLANDS) if m.u8(stsim.OWNERS + i) == 1]
    if mine and rnd.random() < 0.6:
        m.set16(MAP_ISLAND, rnd.choice(mine))
        m.set16(CARRIER_MAP_MODE, 1 if rnd.random() < 0.8 else 0)
    if rnd.random() < 0.7:                                 # mostly clickable
        m.set8(MODAL, 0)
        m.set8(RUNNING, 0xFF)
        m.set16(0x702, 0)
        m.set16(FLAG_TABLE + 2 * T_BUTTON, rnd.choice((0, 0x2000)))


def _war_states(files) -> list:
    return list(files[::6]) + _vehicle_states(files)[len(files[::6])::2]


def check_war_commands(files) -> bool:
    """Every command of the weapons, resources, messages and options screens (and the bridge
    zoom) against its original click handler(s) (d0/d1 pointer, d2 = 2 or 1, a3 the real
    panel element; the station drag released on the radar and dropped by ui_frame) on
    perturbed captured states."""
    import random
    import check_sim
    rnd = random.Random(67)
    ok = True
    states = _war_states(files)
    for name in WCOMMANDS:
        runs, stopped = 0, 0
        for f in states:
            for _ in range(8):
                m = stsim.Mem(bytearray(f.read_bytes()))
                _perturb_war(m, rnd)
                if name in RES_MODE and rnd.random() < 0.6:
                    m.set16(RESOURCES, RES_MODE[name])
                ram = bytes(m.buf)
                oracle = check_sim.Oracle(ram)
                state = stunits.UnitState.from_ram(ram)
                try:
                    args, res = apply_wcommand(state, oracle, name, rnd)
                    if name in WDRAG_STARTS and res and state.mem.u8(MODAL):
                        px, py = _radar_point(rnd, state.mem)
                        _release(state, px, py)
                        _oracle_release(oracle, px, py)
                except NotImplementedError:
                    stopped += 1
                    continue
                runs += 1
                if not _compare(state, oracle, f"{name}{args} from {f.name} (screen {m.u16(stunits.SCREEN)})"):
                    ok = False
                    break
            else:
                continue
            break
        else:
            print(f"{name}: OK ({runs} states{f', {stopped} stopped at unported code' if stopped else ''})")
    return ok


def check_war_input(files) -> bool:
    """screen_input ($17094) on screens 3, 8 and 10 (and the bridge's zoom keys) against the
    original on perturbed states: the resources guards, the turret keys, zoom and right
    click, the SSM toggle with and without the drone out."""
    import random
    from collections import Counter
    import check_sim
    rnd = random.Random(71)
    seen = Counter()
    for f in _war_states(files):
        for _ in range(24):
            m = stsim.Mem(bytearray(f.read_bytes()))
            _perturb_war(m, rnd)
            m.set16(stunits.SCREEN, rnd.choice((3, 3, 8, 8, 10, 10, 0)))
            ram = bytes(m.buf)
            state = stunits.UnitState.from_ram(ram)
            state.screen_input()
            oracle = check_sim.Oracle(ram)
            _call(oracle, 0x17094)
            seen[m.u16(stunits.SCREEN)] += 1
            if not _compare(state, oracle, f"screen_input on {m.u16(stunits.SCREEN)} from {f.name}"):
                return False
    print(f"war screen input: OK ({dict(sorted(seen.items()))})")
    return True


def check_war_runs(files) -> bool:
    """Play: from action and play captures, switch to a war screen by clicks, then 120
    passes of ui_frame + game_frame on both with random commands of that screen (and
    others), keys (arrows, Insert, Clr-Home), right clicks and mouse moves between passes,
    the original running its own ui_frame and screen_input."""
    import random
    from collections import Counter
    from stgfx import ROOT
    rnd = random.Random(73)
    play = sorted((ROOT / "emu" / "out" / "play").glob("pass_*_in.bin"))
    states = play[::10] + list(files[::12])
    names = list(WCOMMANDS)
    stopped, passes, runs, reached = {}, 0, 0, Counter()
    for i, f in enumerate(states):
        m = stsim.Mem(bytearray(f.read_bytes()))
        m.set8(RUNNING, 0xFF)
        for a in (MENU, MODAL, 0x880, 0x881, 0x882, 0x883, SCREEN_REQUEST, 0x704, 0x706, 0x702):
            m.set8(a, 0)
        m.set16(0x702, 0)
        m.set8(stunits.POINTER_MODE, 0xFF)
        for s in range(29):
            a = FLAG_TABLE + 2 * s
            m.set8(a, (m.u8(a) & 0xDF) | (0x20 if s == m.u16(stunits.SCREEN) else 0))
        for k in range(5):
            a = FLAG_TABLE + 2 * (T_TAB + k)
            m.set8(a, (m.u8(a) & 0xDF) | (0x20 if k == m.u16(TAB) else 0))
        # stocks to work with: flares, recon drones, missiles, defence drones; comms on
        for a, v in ((0xC82, 5), (0xC9C, 2), (0xBB2, 4), (0xC1A, 4)):
            m.set16(a, v)
        m.set8(0xB0A, 0xFF)
        if i % 3 == 0:
            buf = m.u32(stunits.CARRIER + 0x3C)
            if buf:
                m.set16(stsim.STOCKPILE, m.u8(buf + 1))
        ram = bytes(m.buf)
        state, oracle = stunits.UnitState.from_ram(ram), _switch_oracle(ram)
        target = (3, 8, 10, 11, 9, 4, 12, 0)[i % 8]
        state.cmd_switch_screen(target)
        _oracle_switch(oracle, target)
        if not _compare(state, oracle, f"switch to {target} from {f.name}"):
            return False
        reached[state.mem.u16(stunits.SCREEN)] += 1
        runs += 1
        for p in range(120):
            try:
                r = rnd.random()
                mm = state.mem
                if mm.u8(MODAL):
                    if r < 0.5:
                        px, py = _radar_point(rnd, mm)
                        state.cmd_drag_release(px, py)
                        oracle.uc.mem_write(0x474, (px & 0xFFFF).to_bytes(2, "big") + (py & 0xFFFF).to_bytes(2, "big"))
                        oracle.uc.mem_write(0x880, b"\x00")
                elif r < 0.4:
                    apply_wcommand(state, oracle, rnd.choice(names), rnd)
                elif r < 0.5:
                    sc, down = rnd.choice((0x48, 0x50, 0x4B, 0x4D, 0x52, 0x47)), rnd.choice((0, 1))
                    state.input_key(sc, down)
                    _copy_input(oracle, state, (), None)
                elif r < 0.55:
                    state.cmd_toggle_mouse_steer()
                    _copy_input(oracle, state, (), None)
                elif r < 0.62 and not mm.u8(stunits.POINTER_MODE):
                    apply_wcommand(state, oracle, "cmd_turret_steer_to", rnd)
                elif r < 0.65 and not mm.u8(stunits.POINTER_MODE):
                    state.cmd_vehicle_fire()
                    _copy_input(oracle, state, (), None)
                if p % 8 == 0:
                    oracle.uc.mem_write(0x8FD, b"\xff")
                    state.mem.set8(0x8FD, 0xFF)
                if state.mem.u8(stunits.POINTER_MODE):
                    state.mem.set8(0x881, 0)
                    oracle.uc.mem_write(0x881, b"\x00")
                state.ui_frame()
                state.game_frame()
            except STOP as e:
                stopped[str(e) or type(e).__name__] = stopped.get(str(e) or type(e).__name__, 0) + 1
                break
            _call(oracle, UI_FRAME)
            oracle_game_frame(oracle)
            passes += 1
            if not _compare(state, oracle, f"run from {f.name} on {target}, pass {p}"):
                return False
    print(f"war runs: OK ({runs} runs on screens {dict(sorted(reached.items()))}, {passes} passes)")
    for k, v in sorted(stopped.items(), key=lambda kv: -kv[1]):
        print(f"  stopped at unported code {v}x: {k}")
    return True


def check_title_new_game(files) -> bool:
    """cmd_title_new_game against the title menu's handlers $103B2 / $103A0 from the title
    image (the menu shown or the load panel up, the button hidden), then ui_frame."""
    import random
    import check_sim
    import check_units
    import stunits_setup as su
    ram = su.title_ram()
    if ram is None:
        print("title new game: SKIPPED (no emu/out/title/ram.bin)")
        return True
    rnd = random.Random(79)
    n = started = 0
    for i in range(8):
        strategy = i % 2
        r = su.seeded(ram, not strategy, rnd.randrange(1 << 32))
        r[GAME_STATUS:GAME_STATUS + 2] = bytes((0, rnd.choice((0, 0, 0, 1))))
        if rnd.random() < 0.15:
            r[FLAG_TABLE + 2 * T_BUTTON] = 0x40
        state = stunits.UnitState.from_ram(bytes(r))
        res = state.cmd_title_new_game(strategy)
        o = check_sim.Oracle(bytes(r))
        if res:
            o.uc.mem_write(su.FRAME_FLIP_WAIT, (0x6000 | (su.FRAME_FLIP_AFTER - su.FRAME_FLIP_WAIT - 2)).to_bytes(2, "big"))
            stack = check_sim.STACK
            check_sim.STACK = 0x58600
            try:
                _regs(o, d2=2)
                o.call(0x103B2 if strategy else 0x103A0)
                o.call(UI_FRAME)
            finally:
                check_sim.STACK = stack
            state.ui_frame()
        ref = o.ram()
        mine = bytearray(ref)
        state.to_ram(mine)
        lines = check_units.diff(mine, ref)
        if lines:
            print(f"title new game: MISMATCH (run {i}, {'strategy' if strategy else 'action'})")
            for line in lines:
                print("  " + line)
            return False
        n += 1
        started += state.mem.u16(stunits.SCREEN) == 0
    print(f"title new game: OK ({n} clicks, {started} new games)")
    return True


SCENARIOS = []
CHECKS = [check_commands, check_game_frame, check_screen_switch, check_vehicle_commands, check_vehicle_input,
          check_vehicle_runs, check_war_commands, check_war_input, check_war_runs, check_title_new_game]
