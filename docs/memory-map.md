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
| `$022E62–$037AA5` | UI bitmaps: 32/16/48-px masked sprites used by sprite tables, UI descriptors and runtime code (see "UI panels") |
| `$037FA6–$03A025` | 15 store-item icons (32×32 masked, `$280` each) |
| `$03A026–$03B1B9` | UI sprite-table bitmaps |
| `$03B426–$040E25` | 15 equipment pictures (64×64, 3 stored planes, `$600` each) |
| `$040E26–$040EB1` | Mouse pointer sprites (16×16, mask + image words) |
| `$040EB2–$040F85` | UI element type flag table |
| `$040F86–$0437FF` | UI descriptors (from `$40F86`) and panels (element lists) |
| `$0438E6–$04A069` | **3D models** (99 records, see "3D renderer") |
| `$04A06A–$04A1F5` | `model_table`: 99 model pointers |
| `$04A1F6–$04B000` | High-entropy data (7.3 bits/byte); purpose unknown |
| `$04AD20–$04AD9F` | Island name offsets (64 words) |
| `$04ADA0–$04AFB5` | Island names (VULCAN … NEMESIS) |
| `$04AFB6–$04B535` | Island records (64 × 22 bytes) |
| `$04B72E` | Sound/tune data passed to `$2019A` *(guess)* |
| `$04BCCA` | Snapshot buffer for globals `$400–$1235` plus the UI flag table (`$1A4C6`/`$1A518`) |
| `$04B000–$04BFFF` | Mixed tables |
| `$04C000–$04CBD3` | Zero fill (end of payload) |
| `$04CBD4–$06FFFF` | Runtime RAM (BSS-like, not in file) |
| `$0586AA` | Initial stack top (SP set in `game_main`) |
| `$058E2A` | Depth-sorted draw tree for the current frame (18-byte nodes, max 128) |
| `$05DCDA` | World object list walked by the renderer: `$140` bytes each, count in `$A48`; type word at +2 |
| `$066690` | 32-entry table built by `$18924` |
| `$066EC0` | Per-island state, 64 × 50 bytes (initialised from the island area) |
| `$069B40` | Island owner bytes (64); the map colours islands by these |
| `$069B80–$06FFFF` | Island table: 64 × `$192` bytes. `+2/+6` x/z (32-bit), `+10/+12` half-extents, `+$E` area, `+$12` 32 structure slots of 12 bytes (`type.w … x.w z.w`; slot types are object types) |
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
| `$2239E–$22B9D` | Island supply network: 64 × 32 bytes, `count.w` then (weight, island) pairs. Also used by `$22C20` |

## Graphics formats (confirmed)

- **Palette**: 16 ST words (`0RGB`, 3 bits per channel). The VBL copies the palette pointed to by `$1326` into `$FF8240` when `$1224` is set. The default palette is `$132A`, and about 25 code sites switch palettes. Colour 15 is cycled from `$13AA` while `$1231` is set.
- **Masked UI sprite**: drawn by `$B316` (32 px wide) or `$B292` (16 px wide). Each row is width/16 columns of `mask, p0, p1, p2, p3` words, so 20 or 10 bytes per row. A mask bit of 1 keeps the background. Decoded by `stgfx.masked_sprite`.
- **Sprite placement table**: walked by `$F0BE` (32-px sprites) or `$F0D2` (16-px sprites). 10-byte records `x.w, y.w, rows-1.w, ptr.l`, terminated by a negative x. Callers load the table with `lea table(pc),A1`. `tools/extract_ui_sprites.py` finds all 22 tables this way (77 unique sprites), and a structural scan confirms none are missed. `$C512` is the Walrus/AAV bay icon set (launch, repair, refuel, fuel supply) with bitmaps at `$2BBA2–$2F18x`. `$BFAA` patches two of its pointers (`$C52C`, `$C536`) to swap icon variants (`$2E32A`/`$2E4F6` ↔ `$3562A`/`$357F6`).
- An 8-byte record list walked by `$54A4` → `$54B6` follows each sprite table (text or box drawing *(guess)*).

## UI panels

The panel system (`$B400`/`$B418`/`$B014`) draws most screens. The format is in the `tools/stui.py` docstring.
- A **panel** has a state variable/value (it's shown only while they match), a draw callback, an optional bevelled frame, and 22-byte elements.
- Each **element** references a **descriptor** at `$40F86+n`. Descriptor kinds: 16/32/48-px bitmaps, a filled polygon, 8×8 or 5×5 text, and boxed/bevelled text (dispatch table `$B06A`).

Extraction:
- `tools/extract_ui_panels.py` renders the 48 panels found from code (two candidates don't parse; they're probably built at run time). It dumps every descriptor bitmap, including the 91 found by a structural scan of the descriptor area, plus the runtime-addressed sprite strips. Frames, boxes and polygons are approximated.
- `tools/extract_items.py` exports the 15 store items. Their records are at `$B76` (26 bytes): position, icon, name and picture index. The 64×64 equipment pictures (`$3B426`, drawn by `$B832`) store planes 0, 2 and 3, with plane 1 = p0 AND p2.

## Island world

`tools/extract_islands.py` writes `islands.json` and an archipelago map. The format is in its docstring.
- **Static data:** 64 island records at `$4AFB6` (position, rectangular half-extents, an optional runway under construction, up to two volcanoes), names, the supply network at `$2239E`, and two start lists of (island, code) pairs:
  - `$18FE2` (9 entries) is used when `$124A` is set;
  - `$1901C` (52 entries) is used otherwise.
  - In both, island 0 VULCAN gets code 0 (player base) and island 63 NEMESIS gets code 16 (enemy base). Codes 1–3 and 17–19 are probably the player and enemy island types (resource, factory, defence) *(guess)*.
- **3D shape:** each island is drawn as model 8 (`$A660`), a rectangular plateau with an 80-unit beach slope. There is no stored terrain or coastline.
- **Per-game generation:** `init_islands` (`$18370`) does the following:
  - fills the island table from the records;
  - places 21 trees per island (types 24–27) at random free spots (`$1897E`, `$18A48`);
  - places the start-list structures (`$189CA`), clearing any overlapping trees;
  - runs the world-simulation step `$1848E` 601 times (`$18474`).
- **RNG:** `$195FE` is a 64-bit add-with-carry generator with its state at `$1236` (initial `31415926 53589793`). The sound interrupt (Timer A, `$20668`) also advances it, so tree and structure layouts vary from game to game. The same interrupt drives YM2149 volume registers from the RNG output, which is the noise/sample sound path.
- **Map screen:** `$6658` draws the islands as owner-coloured rectangles and labels them with the vector font. `$68D0` draws the network links.

## Fonts

Extracted by `tools/extract_font.py`; the formats are in its docstring.
- **5×5** at `$4B8A8` (`!`–`Z`, 6 px advance), drawn by `$9A56–$9B78`. The text stream uses control codes (`5 n` sets colour n, `6` …). The routine is self-modifying: the plane operations for the current colour are patched into the zeroed slots (`$9B78`). Messages are uppercased first (`$A206`, which copies into the message log list at `$65D10`).
- **8×8** at `$4B9CA` (ASCII `$20–$7F`), drawn by `$9C8A`, in 8-px character cells.
- **Vector** (scalable stroke font): offset table `$9DF0`, characters `$20–$5A`, drawn by `$9CEE`/`$9D6E` with line segments (`$5CE2`).

## Screens

`screen_dispatch` (`$14472`) is called each frame. It jumps through `screen_handler_table` (`$144A0`, 24 longs) indexed by the view number at `$718`. Entries 5–7, 13–15 and 21–23 point to a no-op (`$1449E`). Identified so far: 8 = `screen_laser_turret` (`$15A94`). Other handlers: `$1460A`, `$156FA` (near "Damage Status"), `$14E3C`, `$14C6C`, `$162B0` (near the drone strings), `$165B2`, `$1638C`, `$15EB0`, `$16BA6`, `$14E56`, `$15496`, `$14A40`, `$169FE`.

## 3D renderer (partially understood)

Per frame, a 3D view (for example `screen_laser_turret`) sets the camera, then runs:

1. `scene_collect_visible` (`$7BF6`). It walks the world objects at `$5DCDA` and the current island's sub-objects (stride `$40`), and culls each one against `object_cull_table` (`$8598`, 16 bytes per object type: range and distance limits). Visible objects go to `depth_tree_insert` (`$6246`), which builds a binary tree at `$58E2A` keyed on depth. More than 128 nodes → `trap #12`.
2. `draw_horizon_sea` (`$6402`): clips the horizon line to the view and fills it through the polygon engine at `$55D4`.
3. `draw_sea_dots` (`$91EC`): the 8×8 "Sea Dots" grid of water points (an option in the settings menu).
4. `draw_depth_tree` (`$62D4`): an in-order (back-to-front) walk calling `draw_scene_object` (`$7B48`) for each node.
5. `draw_scene_object` → `$7B72` picks the level of detail. `object_lod_table` (`$8B58`) has 16 bytes per object type: `near_dist.l, mid_dist.l, near_model.w, mid_model.w, far_model.w, pad`. The chosen model is looked up in `model_table` (`$4A06A`).

**Model record**: the first long is the address of the model's draw handler, and `draw_scene_object` does `movea.l (a0)+,a1; jmp (a1)`. Models are reached only by object type: `object_lod_table` covers types 0–83, and each type maps to its own model (plus distance-based near/far substitutes). The one exception is the laser turret screen, which calls model 84's handler directly.

All 99 models are decoded. The full format is in the `tools/stmodel.py` docstring. Handlers:

| Handler | Models | Meaning |
|---|---|---|
| `$935E` `model_draw_poly` | 57 | basis, vertex ops, back-face planes, BSP tree of polygon/line/disc groups |
| `$9550` | 16 | `dist.l` + the `$935E` body; nearer than `dist` uses a clipping projection (`$5902`) and BSP walker (`$9582`) |
| `$9496` | 11 | hidden-line wireframe: always-drawn lines, conditional lines, faces that reveal conditional lines |
| `$9A1C` / `$99F2` | 8 / 3 | lines only, with the drawn line count overwritten from object `+$38` (construction-progress wireframes of the island buildings) |
| `$AF16` | 1 | a single screen-space disc, radius from object `+$1E` |
| `$935C` / `$99F0` | 3 | `rts`: empty |

**Basis routines** fill V[`$F8–$FF`] (and sometimes below) before the vertex ops:
- `$A78C` box (three half-extents → 8 corners in V[`$F8–$FF`] at `A1+$5D0`).
- `$A89A` box plus explicit Q15 vertices.
- `$AA24` flat quad (trees).
- `$AF4E` two boxes.
- `$AA78`/`$AABE`/`$AB42` carrier with turret boxes (heading `$6CC`, gun elevation `$6CA`) and lifts (`$DD6`/`$DD8`).
- `$AC16`/`$ADA4` animated (object `+$24` blends vertex pairs).
- `$A660` procedural (object `+$1E`/`+$20`).
- `$ABE8` turret.
- `$A8DA` re-enters `$A89A` past its box setup, reusing the edge vectors left by the previous model.

Vertex builder `$5A02`: a count word, then (op, index) pairs with the ops load, store, midpoint, add, sub and negate. They're dispatched via `jmp (pc,op)` into 24-byte slots at `$5A1C`. Projection is `$5912`. The game frame is x right, y up, z forward. Rotation helpers `$A4EA–$A5DE` apply (sin, cos) pairs from `$A68–$A7E` (2.14 fixed point; sine table at `$1F5A`). The camera pairs `$A74–$A7E` are set per frame by `$A484`/`$A494`/`$A4A4`.

Polygon colour → `$13CA` table of palette-index pairs (equal = solid, different = dither). Fill via `$5F18`; span/pattern tables at `$1D5A`. `$55D4` is the scan-line span filler: it patches a `jmp (a2)` (`$4ED2`) into its unrolled loop at run time.

**Extraction**: `tools/extract_models.py` exports all 96 non-empty models to OBJ with previews.
- 80 are decoded in Python. `tools/check_models.py` checks them against the game's own code run in Unicorn (`tools/stemu.py`), and all match.
- The 19 with runtime-dependent basis routines are evaluated in the emulator. The context used is in `stmodel.EMULATION_CONTEXT`:
  - 75–82 are drawn after parent model 74. This is inferred: their vertices are fractions of 74's box, or index 74's vertices directly.
  - Animated models use `+$24 = $200` (fully deployed).
  - Model 8 uses sample object values (100, 100).
  - Lifts are at 0 and the turret faces forward.

**Identified models**: 1–4/86 carrier (near detail, with turret/lifts), 85 carrier hull, 74 carrier wireframe, 75–82 carrier-system overlays (8, matching the 8 systems on the damage screen), 84 turret wireframe, 5 Manta, 14 Walrus, 17/18/28/29 and 19/34 missiles, 88/36/47 runway (built / near / under construction), 90–98 island buildings with 41–46 near and 48–57 under-construction variants, 7 volcano, 24–27 trees, 10–13 marked pyramids, 58–73 debris shards, 31 flare/flash disc.

## Known gaps

- Auto-analysis missed code reached only through pointer tables (for example `$BFAA`). `AtariSTFindCode.java` added 239 functions, then 163 more from orphan code runs (about 890 in total). Some orphan runs may be tails of larger functions; merge them when found. About 41 KB of the code area is still undefined, mostly embedded tables.
- `$55D4`'s Ghidra function body is wrong. It includes the vertex-op handler slots at `$5A1C–$5AA6` and their zero padding, and it has bogus `CAS2` decodes. Use `tools/dis68k.py` for this area.
- `$22E62–$40E25` is fully accounted for as graphics: UI bitmaps, item icons and pictures. The only remainder is 620 zero bytes at `$3B1BA–$3B425`. The 16-px strips have no recorded per-sprite heights. Sound and title/background screens are not in this block. Island terrain doesn't exist as data: islands are procedural plateaus (see "Island world").
