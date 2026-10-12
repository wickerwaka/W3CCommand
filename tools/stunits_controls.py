"""Unit layer, part of tools/stunits.py: the player's control of the carrier and the game flow around sim_pass ("Sail" milestone).

The original reads the mouse and keyboard in interrupts (IKBD $17D80: deltas $1268/$126A,
buttons $880-$883, keys $798 + 2*scancode, last character $1270) and runs panel click handlers
from ui_frame ($F636 -> hit test $B46E: d0/d1 = pointer x/y, d2 = [$45A] = 2 for a fresh press,
1 for auto-repeat after 6 passes held, a3 = element). A browser UI calls the methods here
instead; each writes exactly what its handler writes (redraws, palettes and sounds left out;
messages become ["message", text] events). A command returns False when the original's hit test
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
    screen_input()                    $17094 for screens 0, 1, 2/17/25 and the empty ones.

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
]:
    if _region[0] not in {r[0] for r in stunits.UNIT_REGIONS}:
        stunits.UNIT_REGIONS.append(_region)
stunits.UNIT_INPUT_REGIONS |= {"current tab $714", "panel overlay $702", "key characters $1280", "map island $742", "map rect $47F2"}


class ControlsMixin:
    # ---- the hit test's conditions ($B46E) ------------------------------------------------
    def _clickable(self, etype: int, state=(0x702, 0)) -> bool:
        """The panel is shown ([state addr] == value; None: always), the element type is
        neither hidden nor disabled, and the game runs unless the type works while paused
        (bit 12)."""
        m = self.mem
        if state is not None and m.u16(state[0]) != state[1]:
            return False
        f = m.u16(FLAG_TABLE + 2 * etype)
        if f & 0xC000:
            return False
        return bool(m.u8(RUNNING) or f & 0x1000)

    def _message(self, text: str) -> None:
        self.events.append(("message", text))

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
        $B5A from 0); the 3D highlight bits at $64D50 are left out (display only)."""
        m = self.mem
        if not (0 <= k <= 9 and self._clickable(T_SYSTEM + k, (0xB64, 0))):
            return False
        a = FLAG_TABLE + 2 * (T_SYSTEM + k)
        if m.u8(a) & 0x20:
            return True
        m.set8(a, m.u8(a) | 0x20)
        old = m.s16(SYSTEM_SHOWN)
        if old >= 0:
            b = FLAG_TABLE + s16(2 * old + 0x60)                # bclr #5,(a1,d0.w)
            m.set8(b, m.u8(b) & 0xDF)
        m.set16(SYSTEM_SHOWN, k)
        if k == 8:
            m.set16(PART_SHOWN, 0)
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
            if m.u8(0x880):
                return
            raise NotImplementedError("the drop at the end of a drag ($F6EA: jmp [$468])")
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
        no request pending. The panel drawing $782A is left out."""
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
        press is consumed."""
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
        self._bclr0(0x83D)                                 # zoom $8B4/$8B2: rendering only
        self._bclr0(0x827)
        if self._bclr0(0x7F7) and self._bclr0(0x7D3):
            self._message("Atari Bundle Version    16/02/90")

    def damage_input(self) -> None:
        """$1747A (screen 1): with the lift shown ($B58 = 8) its parts are highlighted in turn
        ($B5A 0-7); the damage message timer $952 counts down (redraw)."""
        m = self.mem
        if m.s16(SYSTEM_SHOWN) == 8:
            k = m.s16(PART_SHOWN) + 1
            if k > 8:
                k = 0
            m.set16(PART_SHOWN, k)
        if m.u16(0x952):
            m.set16(0x952, m.u16(0x952) - 1)

    def map_input(self) -> None:
        """$17244 (maps, screens 2/17/25): cursor keys scroll, Insert / Clr-Home zoom (held),
        a right click recentres ($F8B4); on the Walrus and Manta maps the keypad selects a
        unit ($132D6, not ported)."""
        m = self.mem
        for key, f in ((0x828, self.map_scroll_up), (0x838, self.map_scroll_down),
                       (0x82E, self.map_scroll_left), (0x832, self.map_scroll_right),
                       (0x83C, self.map_zoom_in), (0x826, self.map_zoom_out)):
            if m.u8(key):
                f()
        if not m.u16(MAP_INFO) and self._bclr0(0x883):
            self.map_recentre_at_pointer()
        if m.u16(stunits.SCREEN) in (0x11, 0x19) and m.u8(stunits.POINTER_MODE) and not m.u8(MODAL) \
                and any(m.u8(a) & 1 for a in MAP_KEYPAD):
            raise NotImplementedError("unit selection keys on the Walrus/Manta map ($132D6)")

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


# $170D4, the per-screen input handlers (screen: address; the rest are $170D2, rts).
INPUT_TABLE = {0: 0x17164, 1: 0x1747A, 2: 0x17244, 3: 0x177DE, 8: 0x1741A, 10: 0x1778E, 16: 0x17748,
               17: 0x17244, 18: 0x17374, 19: 0x17782, 24: 0x17610, 25: 0x17244, 26: 0x1773C, 27: 0x172BC}
# $170D4: screens whose input handler is ported ("" = $170D2, rts).
SCREEN_INPUT = {0: "bridge_input", 1: "damage_input", 2: "map_input", 17: "map_input", 25: "map_input"}
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


SCENARIOS = []
CHECKS = [check_commands, check_game_frame, check_screen_switch]
