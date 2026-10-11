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
| `$022E62–$040E25` | **Static data**, ~120 KB, low entropy (40–75% zero bytes); 3D models / graphics *(guess)* |
| `$040E26–$0412xx` | Mouse pointer sprites (16×16, mask + image words) and small UI tables |
| `$041278–$043800` | UI layout / menu data ("CANCEL", "Start Action Game", …) |
| `$043800–$04A000` | Medium-entropy data with few zeros; bitmap graphics *(guess)* |
| `$04A000–$04B000` | High-entropy data (7.3 bits/byte): compressed data, a random/noise table or samples *(guess)* |
| `$04ADA0–$04AFB5` | Island name table (VULCAN … NEMESIS) |
| `$04B000–$04BFFF` | Mixed tables |
| `$04C000–$04CBD3` | Zero fill (end of payload) |
| `$04CBD4–$06FFFF` | Runtime RAM (BSS-like, not in file) |
| `$0586AA` | Initial stack top (SP set in `game_main`) |
| `$069B80–$06FFFF` | Object table: 64 entries × `$192` bytes (positions as 32-bit X at +2, Y at +6) |
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

## Known gaps

- Auto-analysis missed code reached only through pointer tables (for example `$BFAA`). `AtariSTFindCode.java` added 239 functions (about 660 in total). About 41 KB of the code area is still undefined, mostly embedded tables.
- Some code is disassembled but not inside a function, for example the palette switches at `$13AA8–$142F8` and the screen setup before `$C512`.
- `$55D4` contains bytes that decode as 68020-only instructions (`CAS2`), which is probably data inside the function. Needs manual review.
- The internal formats of the `$22E62–$40E25` block are not yet identified. Next step: follow the `move.l #$2E32A…` / `#$3562A…` pointer pairs from `$BFAE`/`$C4D8` and the renderer's model loads.
