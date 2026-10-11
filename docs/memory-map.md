# CARRIER.TOS runtime memory map

Addresses are runtime addresses on a 512 KB ST. For the payload, runtime address == offset in `CARRIER.TOS` (see CLAUDE.md). "Payload" means `extracted/carrier_payload.bin`, which is loaded at `$400`.

Status: first pass. Entries marked *(guess)* are unconfirmed.

## Overview

| Range | Contents |
|---|---|
| `$000000–$0003FF` | 68000 exception vectors (installed by `hw_init`) |
| `$000400–$000403` | `JMP $405A` |
| `$000404–$00405A` | "Atari Bundle Version 16/02/90" header and game globals (TOS sysvar area, reused) |
| `$00405A` | `game_main`: init, then the main loop |
| `$000400–~$001300` | Game globals, accessed as abs.w (flags at `$461–$466`, timers etc.). Interleaved with code from `$405A` |
| `$00405A–$022E61` | **Code**, with embedded strings and tables (see below) |
| `$022E62–$040E25` | **Static data**, ~120 KB, low entropy (40–75% zero bytes). Includes the UI sprite bitmaps (for example `$2BBA2–$2F18x`); the rest is unidentified |
| `$040E26–$0412xx` | Mouse pointer sprites (16×16, mask + image words) and small UI tables |
| `$041278–$043800` | UI layout / menu data ("CANCEL", "Start Action Game", …) |
| `$0438E6–$04A069` | **3D models** (99 records, see "3D renderer") |
| `$04A06A–$04A1F5` | `model_table`: 99 model pointers |
| `$04A1F6–$04B000` | High-entropy data (7.3 bits/byte); purpose unknown |
| `$04ADA0–$04AFB5` | Island name table (VULCAN … NEMESIS) |
| `$04B000–$04BFFF` | Mixed tables |
| `$04C000–$04CBD3` | Zero fill (end of payload) |
| `$04CBD4–$06FFFF` | Runtime RAM (BSS-like, not in file) |
| `$0586AA` | Initial stack top (SP set in `game_main`) |
| `$058E2A` | Depth-sorted draw tree for the current frame (18-byte nodes, max 128) |
| `$05DCDA` | World object list walked by the renderer: `$140` bytes each, count in `$A48`; type word at +2 |
| `$069B80–$06FFFF` | Object table: 64 entries × `$192` bytes (positions as 32-bit X at +2, Y at +6). Relation to `$5DCDA` unknown |
| `$069B40` | Per-object type/state byte array (64 bytes) |
| `$070000`, `$078000` | Screen buffers (double-buffered, 32000 bytes each) |
| `$07FF9C` | Loader copy stub (only during boot) |

## Code-area landmarks

| Address | Name / meaning |
|---|---|
| `$405A` | `game_main` |
| `$17966` | `hw_init`: vectors, MFP/ACIA setup, scanline table at `$164A` (200 × 160-byte rows → low res 320×200×16) |
| `$17A90` | `vbl_handler` (writes palette `$FF8240`) |
| `$17CB2` | `mfp_vec_timer_c_handler` |
| `$17CF8` | `mfp_vec_acia_handler` (keyboard/mouse) |
| `$20668` | `mfp_vec_timer_a_handler` (installed separately at `$20550`; sound *(guess)*) |
| `$20E94` | "Andy's exception handler" strings: crash reporter |
| `$7526`, `$C312`–`$CD49` | Carrier/AAV/Manta UI screens |
| `$F0EC`–`$F44A` | Language select (EN/DE/FR) and manual-word copy protection prompts |
| `$E53E`–`$E809` | Save/load game to floppy (formats its own disk) |
| `$2239E`, `$22B9E` | Tables inside the code area used by the `$22C20` proximity/threat routine |

## Graphics formats (confirmed)

- **Palette**: 16 ST words (`0RGB`, 3 bits per channel). The VBL copies the palette pointed to by `$1326` into `$FF8240` when `$1224` is set. The default palette is `$132A`, and about 25 code sites switch palettes. Colour 15 is cycled from `$13AA` while `$1231` is set.
- **Masked UI sprite**: drawn by `$B316` (32 px wide) or `$B292` (16 px wide). Each row is width/16 columns of `mask, p0, p1, p2, p3` words, so 20 or 10 bytes per row. A mask bit of 1 keeps the background. Decoded by `stgfx.masked_sprite`.
- **Sprite placement table**: walked by `$F0BE` (32-px sprites) or `$F0D2` (16-px sprites). 10-byte records `x.w, y.w, rows-1.w, ptr.l`, terminated by a negative x. Callers load the table with `lea table(pc),A1`. `tools/extract_ui_sprites.py` finds all 22 tables this way (77 unique sprites), and a structural scan confirms none are missed. `$C512` is the Walrus/AAV bay icon set (launch, repair, refuel, fuel supply) with bitmaps at `$2BBA2–$2F18x`. `$BFAA` patches two of its pointers (`$C52C`, `$C536`) to swap icon variants (`$2E32A`/`$2E4F6` ↔ `$3562A`/`$357F6`).
- An 8-byte record list walked by `$54A4` → `$54B6` follows each sprite table (text or box drawing *(guess)*).

## Screens

`screen_dispatch` (`$14472`) is called each frame. It jumps through `screen_handler_table` (`$144A0`, 24 longs) indexed by the view number at `$718`. Entries 5–7, 13–15 and 21–23 point to a no-op (`$1449E`). Identified so far: 8 = `screen_laser_turret` (`$15A94`). Other handlers: `$1460A`, `$156FA` (near "Damage Status"), `$14E3C`, `$14C6C`, `$162B0` (near the drone strings), `$165B2`, `$1638C`, `$15EB0`, `$16BA6`, `$14E56`, `$15496`, `$14A40`, `$169FE`.

## 3D renderer (partially understood)

Per frame, a 3D view (for example `screen_laser_turret`) sets the camera, then runs:

1. `scene_collect_visible` (`$7BF6`). It walks the world objects at `$5DCDA` and the current island's sub-objects (stride `$40`), and culls each one against `object_cull_table` (`$8598`, 16 bytes per object type: range and distance limits). Visible objects go to `depth_tree_insert` (`$6246`), which builds a binary tree at `$58E2A` keyed on depth. More than 128 nodes → `trap #12`.
2. `draw_horizon_sea` (`$6402`): clips the horizon line to the view and fills it through the polygon engine at `$55D4`.
3. `draw_sea_dots` (`$91EC`): the 8×8 "Sea Dots" grid of water points (an option in the settings menu).
4. `draw_depth_tree` (`$62D4`): an in-order (back-to-front) walk calling `draw_scene_object` (`$7B48`) for each node.
5. `draw_scene_object` → `$7B72` picks the level of detail. `object_lod_table` (`$8B58`) has 16 bytes per object type: `near_dist.l, mid_dist.l, near_model.w, mid_model.w, far_model.w, pad`. The chosen model is looked up in `model_table` (`$4A06A`).

**Model record**: the first long is the address of the model's draw handler, and `draw_scene_object` does `movea.l (a0)+,a1; jmp (a1)`. Handlers by use count: `$935E` = `model_draw_poly` (57), `$9550` (16), `$9496` (11), `$9A1C` (8), `$99F2` (3), `$935C` (2, possibly an `rts` stub), `$AF16`, `$99F0`.

**`model_draw_poly` format** (confirmed; the full spec is in the `tools/stmodel.py` docstring):
- `handler.l`, then `basis_routine.l`: `model_basis_box` `$A78C` (46 models), or `$A89A` (5), `$AA24` (4), `$AC16`, `$AF4E`. For `$A78C`, three half-extent words give 8 box corners in V[`$F8–$FF`] (`A1+$5D0`).
- Vertex builder `$5A02`: a count word, then (op, index) byte pairs. The ops are load, store, midpoint, add, sub and negate on an accumulator, dispatched via `jmp (pc,op)` into 24-byte handler slots at `$5A1C`.
- A count word of vertices to project (`$5912`). The game frame is x right, y up, z forward.
- Back-face planes `$98E6`: a count, then (a, b, c) index triples. Each plane gets a facing flag.
- BSP tree `$9382`: nodes are `plane.b off.w <inline child>`. `$FF` leaves hold polygon groups (polygons, lines, discs, optional group back-face test). `$80–$FE` are attachment slots (table at `$6D6`).
- Polygon colour → `$13CA` table of palette-index pairs (equal = solid, different = dither). Fill via `$5F18`; span/pattern tables at `$1D5A`.

`tools/extract_models.py` exports the 46 `$935E`/`$A78C` models to OBJ, with previews. Each decoded record ends exactly where the next model starts (allowing for 1 byte of word padding), which validates the format. Identified: 5 Manta, 14 Walrus, 17/18/28/29 missiles, 19/34 larger missiles, 85 carrier hull, 88 runway, 90–98 island structures (91 comms tower), 58–73 flat debris shards.

Not yet decoded: models using handlers `$9550` (16), `$9496` (11), `$9A1C` (8), `$99F2` (3), `$AF16`, `$99F0`, and `$935E` models with the non-box basis routines (11). `$55D4` is the scan-line span filler: it patches a `jmp (a2)` (`$4ED2`) into its unrolled loop at run time. That, plus the vertex-op slots that follow, is why Ghidra decoded bogus `CAS2` instructions there.

## Known gaps

- Auto-analysis missed code reached only through pointer tables (for example `$BFAA`). `AtariSTFindCode.java` added 239 functions, then 163 more from orphan code runs (about 890 in total). Some orphan runs may be tails of larger functions; merge them when found. About 41 KB of the code area is still undefined, mostly embedded tables.
- `$55D4`'s Ghidra function body is wrong. It includes the vertex-op handler slots at `$5A1C–$5AA6` and their zero padding, and it has bogus `CAS2` decodes. Use `tools/dis68k.py` for this area.
- Most of `$22E62–$40E25` beyond the UI sprites is still unidentified. Candidates: island terrain/heightmaps, full-screen backgrounds, fonts, sound.
