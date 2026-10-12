/**
 * Unit layer: the state the screens' render routines write (docs/units.md §10.8).
 *
 * Port of tools/stunits_render.py (the verified reference). The app doesn't run the original
 * render, so it calls render_effects() once per frame after the pass, for the screen shown:
 * screen_dispatch $14472 without the drawing. map_render_effects() is the maps' part (screens
 * 2, 17, 25: the island nearest the map centre $764 and its owner/position, the islands
 * panel's recount). The class sits between UnitCore and SetupUnits in the inheritance chain.
 */
import { NotPorted, s16, s32, w16 } from "./mem.ts";
import type { Region } from "./mem.ts";
import { ECONOMY, ECONOMY_SIZE, ISLAND_SIZE, ISLAND_TABLE, OWNERS } from "./world.ts";
import { CARRIER, SCREEN, SINE, UNIT_REGIONS, UnitCore } from "./units_core.ts";

export const MAP_SCREENS = [2, 17, 25];
export const MAP_ZOOM = 0x75a, MAP_X = 0x75c, MAP_Z = 0x760, MAP_INFO = 0x766;
export const NEAREST = 0x764, NEAREST_D2 = 0x73e, NEAREST_OWNER = 0x74c, NEAREST_X = 0x742,
  NEAREST_Z = 0x746, MARKER_ZOOM = 0x750;
export const DRONE_TYPE = 66; // screen 11's drone elements $42A4E + $16 k have types 66-69
export const FLAG_TABLE = 0x40eb2;
export const TITLE_TIMER = 0x93a, TITLE_FLAG = 0x124c;

// State the renders write that the other regions don't cover.
for (const _region of [
  ["map render $73E", NEAREST_D2, 4],
  ["map render owner $74C", NEAREST_OWNER, 1],
  ["map render $750", MARKER_ZOOM, 2],
  ["title timer $93A", TITLE_TIMER, 4],
  ["title flag $124C", TITLE_FLAG, 2],
] as Region[]) {
  if (!UNIT_REGIONS.some((r) => r[0] === _region[0] && r[1] === _region[1] && r[2] === _region[2])) {
    UNIT_REGIONS.push(_region);
  }
}

/** $65C4: (d asr 8) divs.w zoom, floored (decremented when the remainder is negative,
 * unless that overflows); a quotient beyond a word gives $7FFF / -$7FFF by the dividend's
 * sign. */
export function map_div(d: number, zoom: number): number {
  const d4 = s32(d) >> 8;
  const z = s16(zoom);
  if (z === 0) throw new NotPorted("map zoom 0 (divs by zero)");
  let q = Math.trunc(d4 / z);
  if (!(-0x8000 <= q && q <= 0x7fff)) return d4 < 0 ? -0x7fff : 0x7fff;
  if (d4 - q * z < 0 && q !== -0x8000) q -= 1;
  return q;
}

// $144A0: the screens whose render writes state that is ported.
export const RENDER_EFFECTS: Record<number, string> = {
  2: "map_render_effects", 17: "map_render_effects", 25: "map_render_effects",
  4: "_messages_render", 11: "_radar_render", 8: "_turret_render",
  18: "_walrus_view_render", 27: "_manta_view_render", 33: "_title_render",
};

export abstract class RenderUnits extends UnitCore {
  /** screen_dispatch $14472 for [$718] (or `screen`) without the drawing: the debug-mode
   * keys ($210F0, $17874), then the screen's render ($144A0) and the panel callbacks it
   * runs. Ported: the maps (2, 17, 25: map_render_effects), messages (4), defence radar
   * (11), the static noise of the turret (8) and vehicle views (18, 27), and the title
   * (33). Not ported (no simulation effect found, docs/units.md §10.8): the 3D views'
   * camera and pipeline writes and the arming screens' panel redraws. */
  render_effects(screen: number | null = null): void {
    const m = this.mem;
    if (screen === null) screen = m.u16(SCREEN);
    if (m.u8(0x1189)) m.set8(0x871, m.u8(0x871) & 0xfe); // $210F0: debug display key
    const fn = RENDER_EFFECTS[screen];
    if (fn) (this as any)[fn]();
    // $17874: in a running game in debug mode, key $869 toggles the frame-rate display.
    if (m.u8(0x462) && !m.u8(0x461) && m.u8(0x1189) && m.u8(0x869) & 1) {
      m.set8(0x869, m.u8(0x869) & 0xfe);
      m.set8(0x122d, ~m.u8(0x122d) & 0xff);
    }
  }

  /** $14E3C (screens 2, 17, 25) without the drawing. With the island information box up
   * ([$766] != 0) it only draws. Otherwise $65E2: [$764] high byte = $FF (st.b), [$73E] =
   * 2000000, [$750] = min(zoom, 8); on the carrier map in islands mode ([$710] != 0) the
   * islands panel's recount $CDF2; then each island within +-255 px of the centre (unsigned,
   * after neg) whose mx^2 + my^2 (mulu) is below [$73E] (unsigned) becomes the nearest:
   * [$73E], [$764] index, [$74C] owner, [$742/$746] position. Last, the unit markers leave
   * their turn pair in $A6C/$A6E: on screen 2 the carrier's ((heading + $800) & $F000)
   * sin/cos; on 17/25 the cursor's (0, $4000). */
  map_render_effects(): void {
    const m = this.mem;
    if (m.u16(MAP_INFO)) return;
    m.set8(NEAREST, 0xff);
    m.set32(NEAREST_D2, 0x1e8480);
    const zoom = m.u16(MAP_ZOOM);
    m.set16(MARKER_ZOOM, s16(zoom) > 8 ? 8 : zoom);
    const screen = m.u16(SCREEN);
    if (screen === 2 && m.u16(0x710)) this.island_counts();
    const cx = m.u32(MAP_X), cz = m.u32(MAP_Z);
    for (let i = 0; i < 64; i++) {
      const isl = ISLAND_TABLE + ISLAND_SIZE * i;
      const mx = map_div(s32(m.u32(isl + 2) - cx), zoom);
      const my = map_div(s32(-s32(m.u32(isl + 6) - cz)), zoom);
      const ax = mx < 0 ? -mx & 0xffff : mx, ay = my < 0 ? -my & 0xffff : my;
      if (ax >= 0x100 || ay >= 0x100) continue;
      const d2 = ax * ax + ay * ay;
      if (d2 >= m.u32(NEAREST_D2)) continue;
      m.set32(NEAREST_D2, d2);
      m.set16(NEAREST, i);
      m.set8(NEAREST_OWNER, m.u8(OWNERS + i));
      m.set32(NEAREST_X, m.u32(isl + 2));
      m.set32(NEAREST_Z, m.u32(isl + 6));
    }
    if (screen === 2) {
      const d7 = w16(m.u16(CARRIER + 6) + 0x800) & 0xf000;
      m.set16(0xa6c, m.u16(SINE + (d7 >> 3)));
      m.set16(0xa6e, m.u16(SINE + (w16(d7 + 0x4000) >> 3)));
    } else {
      m.set32(0xa6c, 0x4000);
    }
  }

  /** $CDF2: [$D54] friendly islands, [$D56] hostile, and per friendly island
   * [$D58 + 2 * (economy +0 byte)] + 1, after clearing $D54, $D56, $D5A, $D5C, $D5E. */
  island_counts(): void {
    const m = this.mem;
    for (const a of [0xd54, 0xd56, 0xd5a, 0xd5c, 0xd5e]) m.set16(a, 0);
    for (let i = 0; i < 64; i++) {
      const o = m.u8(OWNERS + i);
      if (o === 1) {
        m.set16(0xd54, m.u16(0xd54) + 1);
        const a = 0xd58 + 2 * m.u8(ECONOMY + ECONOMY_SIZE * i);
        m.set16(a, m.u16(a) + 1);
      } else if (o) {
        m.set16(0xd56, m.u16(0xd56) + 1);
      }
    }
  }

  /** $162B0: the log is redrawn while $966 counts down. (Its expiry of old records
   * changes the log at $65D10, which the app keeps itself.) */
  _messages_render(): void {
    const m = this.mem;
    if (m.u16(0x966)) m.set16(0x966, m.u16(0x966) - 1);
  }

  /** $15EB0: drone k's element (type [$42A4E + $16 k] = 66 + k) is shown (flag bit 15
   * clear) while the drone is out (station +1 bit 0) and the radar works ([$B02] != 0),
   * else hidden. (The element positions it writes are UI.) */
  _radar_render(): void {
    const m = this.mem;
    for (let k = 0; k < 4; k++) {
      const a = FLAG_TABLE + 2 * (DRONE_TYPE + k);
      if (m.u8(0x65e + 14 * k + 1) & 1 && m.u8(0xb02)) m.set8(a, m.u8(a) & 0x7f);
      else m.set8(a, m.u8(a) | 0x80);
    }
  }

  /** $15A94: with the observation turret working ([$B12] != 0) the zoom view record's
   * +8/+$A = [$6D2] + 8 (and the 3D view, not ported); otherwise static noise ($5538:
   * one rand). */
  _turret_render(): void {
    const m = this.mem;
    if (m.u8(0xb12)) {
      m.set16(0x478e, w16(m.u16(0x6d2) + 8));
      m.set16(0x4790, w16(m.u16(0x6d2) + 8));
    } else {
      this.rand();
    }
  }

  /** $15496 (screen 18): static noise (one rand) when [$70E] != 0, the Walrus object
   * [$56C] is live (+1 bit 0) and its record's telemetry [$568] +$2C ($712) != 0. The 3D
   * view is not ported. */
  _walrus_view_render(): void {
    const m = this.mem;
    if (m.u16(0x70e) && m.u8(m.u32(0x56c) + 1) & 1 && m.u16(m.u32(0x568) + 0x2c)) this.rand();
  }

  /** $15244 (screen 27): as screen 18 for the Manta: [$70C] != 0, record [$48E] not
   * destroyed (+1 >= 0), object [$492] live, telemetry +$2E != 0. */
  _manta_view_render(): void {
    const m = this.mem;
    const rec = m.u32(0x48e);
    if (m.u16(0x70c) && !(m.u8(rec + 1) & 0x80) && m.u8(m.u32(0x492) + 1) & 1 && m.u16(rec + 0x2e)) {
      this.rand();
    }
  }

  /** $15DA2 (screen 33, title menu [$708] = 0): one rand; the carrier model's turret
   * ($6CC + 300, $6CA swinging between 0 and -14000 by 800, $6D4 the direction); Control
   * with A / B sets $124C to 0 / 1 (with M it ends the wait at once); in debug mode keypad
   * +/- (new flags $835/$82D, consumed) restart the wait at 1000; the wait $93C counts down
   * and at 0 grows $93A by 180 (to at most 3600), restarts and goes back to the title
   * ($1231 = $FF, $4272: tab 5 requested, $708 = 0; $458 = $FF, $881 = 0). The 3D model
   * view is not ported. */
  _title_render(): void {
    const m = this.mem;
    if (m.u16(0x708)) return;
    this.rand();
    m.set16(0x6cc, w16(m.u16(0x6cc) + 0x12c));
    let d0 = s16(w16((m.u8(0x6d4) ? -0x320 : 0x320) + m.u16(0x6ca)));
    const d1 = d0;
    d0 = d0 > 0 ? 0 : d0 < -0x36b0 ? -0x36b0 : d0;
    m.set16(0x6ca, d0);
    if (d1 !== d0) m.set8(0x6d4, ~m.u8(0x6d4) & 0xff);
    let expire = false;
    if (m.u8(0x7d2)) {
      if (m.u8(0x7fc)) expire = true;
      else if (m.u8(0x7d4)) m.set16(TITLE_FLAG, 0);
      else if (m.u8(0x7f8)) m.set16(TITLE_FLAG, 1);
    }
    if (!expire) {
      if (m.u8(0x1189)) {
        if (m.u8(0x835) & 1) {
          m.set8(0x835, m.u8(0x835) & 0xfe);
          m.set16(TITLE_TIMER + 2, 1000);
        } else if (m.u8(0x82d) & 1) {
          m.set8(0x82d, m.u8(0x82d) & 0xfe);
          m.set16(TITLE_TIMER + 2, 1000);
        }
      }
      const t = w16(m.u16(TITLE_TIMER + 2) - 1);
      m.set16(TITLE_TIMER + 2, t);
      if (t) return;
    }
    let d = w16(m.u16(TITLE_TIMER) + 0xb4);
    if (s16(d) > 0xe10) d = 0xe10;
    m.set16(TITLE_TIMER, d);
    m.set16(TITLE_TIMER + 2, d);
    m.set8(0x1231, 0xff);
    m.set8(0x1227, 0xff);
    m.set16(0x1228, 5);
    m.set16(0x708, 0);
    m.set8(0x458, 0xff);
    m.set8(0x881, 0);
  }
}
