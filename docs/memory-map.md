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
| `$040E26–$040EB1` | 2 mouse pointers (arrow, crosshair): header + 16 rows of mask/image words (`extract_ui_sprites.py`) |
| `$040EB2–$040F85` | UI element type flag table |
| `$040F86–$0437FF` | UI descriptors (from `$40F86`) and panels (element lists) |
| `$043800–$0438E5` | Static panel lists (count + panel pointers) and a pointer table |
| `$0438E6–$04A069` | **3D models** (99 records, see "3D renderer") |
| `$04A06A–$04A1F5` | `model_table`: 99 model pointers |
| `$04A1F6–$04A8B5` | Copy-protection questions: 3 languages × 64 × 9 bytes (page/paragraph/word), XOR-encrypted |
| `$04A8B6–$04AD1F` | Copy-protection answers: length-prefixed, XOR-encrypted (`tools/extract_protection.py`) |
| `$04AD20–$04AD9F` | Island name offsets (64 words) |
| `$04ADA0–$04AFB5` | Island names (VULCAN … NEMESIS) |
| `$04AFB6–$04B535` | Island records (64 × 22 bytes) |
| `$04B5B6–$04B8A7` | Sound effect records (18): a 6-byte header + channel script |
| `$04BCCA–$04CBD3` | Snapshot buffer (zero in the file) for globals `$400–$1235` plus the UI flag table (`$1A4C6`/`$1A518`) |
| `$04B536–$04B5B5` | Word offset table (`$80`, `$96`, …) followed by `$80` fill; consumer not found |
| `$04B8A8–$04B9C9` | 5×5 font |
| `$04B9CA–$04BCC9` | 8×8 font |
| `$04CBD4–$06FFFF` | Runtime RAM (BSS-like, not in file) |
| `$0586AA` | Initial stack top (SP set in `game_main`) |
| `$058E2A` | Depth-sorted draw tree for the current frame (18-byte nodes, max 128) |
| `$05C2DA` | Static-noise texture: `$D00` random words (`$55BC` at new game), drawn by `$5540` |
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
| `$20668` | `mfp_vec_timer_a_handler`: sound buzz mode (installed at `$20550`, see "Sound") |
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
- `tools/extract_ui_panels.py` renders the panels found from code and from the static panel lists at `$43800` (two candidates don't parse; they're probably built at run time). It dumps every descriptor bitmap, including the 91 found by a structural scan of the descriptor area, plus the runtime-addressed 16-px strips. Those are split into individual icons at pointers from the drone-marker table (`$161A2`) and the Manta/Walrus payload records (`$CAC4`, 12 × 26 bytes, icon at `+10`), then at fully transparent rows. Frames, boxes and polygons are approximated.
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
- **RNG:** `$195FE` is a 64-bit add-with-carry generator with its state at `$1236` (initial `31415926 53589793`). The sound interrupt (Timer A, `$20668`) also advances it, and so does the title screen's static-noise drawing (`$5540`). Tree and structure layouts vary from game to game with the RNG at the click; no interrupt changes it during the new-game setup itself (`docs/units.md` §16). The same interrupt drives YM2149 volume registers from the RNG output, which is the noise/sample sound path.
- **Map screen:** `$6658` draws the islands as owner-coloured rectangles and labels them with the vector font. `$68D0` draws the network links.

## Sound

The sound effects are channel scripts run by a small bytecode engine (`$20188–$20CD8`). There's no sample data. "Buzz" and noise effects come from switching PSG volumes on and off from MFP Timer A at random intervals. The engine is fully decoded, and `web/src/audio/engine.ts` ports it. `npm run check-sound` checks the port write for write against the original under Unicorn (see "Verification" below).

**Entry points**

| Address | Routine |
|---|---|
| `$20188` | Free all 6 channels. Called from `hw_init` (`$17A18`) and `$20716`. |
| `$2019A` | Start the sound record at A0 (see "Starting a record"). |
| `$20434` / `$2045E` | Sound off / on: the pause button (`$FD8C` / `$FDAA`). Off clears the enable byte `$20484`, so `$20486` returns at once and the channels freeze. It also writes reg 7 = `$7F` and vol A/B/C = 0. Timer A isn't stopped. |
| `$20486` | Per VBL, called first thing in `vbl_handler` (`$17A94`). |
| `$20668` | Timer A interrupt. Installed at `$134` by `$20550`. |
| `$20716` | Reset the vehicle sounds (`$A16` = −1, `$A06`/`$A0A` high bytes = `$FF`, `$A10` = 0), then `$20188`. Called at new game `$4212`/`$4280`, `$13AFA` and `$14266`. |
| `$20730` | Once per game pass (`game_main` `$412E`). Calls `$2073A` (engine sound), then `$2092A` (nearest other vehicle). |
| `$20B90` | `jsr $20B90` + inline word id. The id indexes the 14-long effect table `$20BD0`: positive = record (start it), 0 = nothing, negative = routine at −value (no entry uses this). The routine saves A0/D0 in `$A80`/`$A84` (so it clobbers that scratch). It has no gate of its own: every call site tests `$1249` (sound effects off) itself. |
| `$20C30` | Graded explosion at the object A1 (longs `+$A/+$E/+$12`), level D0.w. |
| `$20C08` | Graded explosion at an island-buffer entry A1: words `+$A/+$C/+$E` sign-extended and added to the island base `$A4A/$A4E/$A52`. |
| `$20CD8` | A bare `rts`, the target of three stubbed-out sound calls. |

**Effect table** `$20BD0`. Ids 2/3 and 5/6 share records.

| id | Record | Sound |
|---|---|---|
| 0 | `$4B5B6` | Message ping |
| 1 | `$4B5CC` | Second message ping (`$A128`); also carrier grounding |
| 2, 3 | `$4B758` | Manta / Walrus laser |
| 4 | `$4B77E` | Carrier laser turret |
| 5, 6 | `$4B888` | Missile launch (SSM, Manta homing, Walrus guided): buzz |
| 7, 8 | 0 | Bomb / virus bomb drop: nothing |
| 9 | `$4B7A4` | Manta missile lock-on |
| 10 | `$4B7BE` | Flare / recon drone launch (noise sweep) |
| 11 | `$4B7E2` | Virus bomb captures an island: random-pitch tune |
| 12 | `$4B864` | "Missile heading for Carrier" alarm |
| 13 | 0 | Nothing |

**Graded explosions** `$20CB4`: 9 records, `$4B5E2 … $4B72E`. They run from quiet and short to loud and long: start volume 8 … 15, noise period 10 … 24. Variants 5–8 use buzz (noise bytes `$32`, `$50`, `$64`, `$78`).

**Choosing the variant.** Both `$20C30` and `$20C08` do the following:
1. Return if `[$1249]` ≠ 0 or `[$A02]` (the controlled object) = 0.
2. Take each long delta between the explosion and the controlled object. If a delta doesn't fit a signed word, return.
3. Take `neg.w` of each delta (−32768 stays negative), then the signed maximum: d.
4. Compute index = level + 6. If d − 5000 (a word) isn't negative, index −= (d − 5000) >>> 13. If that borrows, return.
5. Start `$20CB4[index]`.

The game only passes levels 0–2, so the index stays within 3–8. The game-over screen (`$14446`, when `[$466]` = 1) starts `$4B72E` directly.

**Starting a record** (`$2019A`). A record has a 6-byte header (priority words for sub-voices 0, 1 and 2), then the script.
- **Finding a channel.** Take the first channel whose word `+0` = 0 (`$201C0`).
- **Stealing a channel.** If none is free, `$201D0` takes the channel with the lowest sub-voice-0 priority `+$26`. That priority must be ≤ the record's (signed; on ties the later channel wins), and channels with word `+0` = 1 are skipped. The stolen channel is cleared. If no channel qualifies, nothing plays.
- **Initialising.** Set `+$26` = the header long, `+$2A` = the header word, `+2` = the script address, `+0` = `$FFFF`.

**Channel structure** (`$4A` bytes). The six channels are at `$4CBD4 + $4A·k`, with the pointer list at `$20466` ending in −1.

| Offset | Contents |
|---|---|
| `+0.w` | 0 = free; `$FFFF` = script running; 1 = owned by a vehicle sound (no script; never stolen) |
| `+2.l` | Script pc |
| `+6` | R0–R15 (words) |
| `+$26/+$28/+$2A` | Priorities of sub-voices 0–2 (≤ 0 never sounds) |
| `+$2C.b` | Wait counter |
| `+$2E` | P0–P15 (words). See below. |
| `+$3E/+$40/+$42` | Tone enable of sub-voices 0–2 (≠ 0 = on) |
| `+$44/+$46/+$48` | Noise enable of sub-voices 0–2 |

The P registers are parameters:
- P0–P2 (`+$2E/+$30/+$32`) are the tone periods of sub-voices 0–2. The PSG gets the low byte, then the high byte.
- P3 (`+$34`, low byte `+$35`) is the noise byte. Bits 0–4 go to PSG reg 6; bits 5–7 are the Timer A prescaler (buzz).
- P4–P6 (`+$36/+$38/+$3A`, low bytes used) are the volumes of sub-voices 0–2. Bit 4 selects envelope mode.
- P7 (`+$3C`) is the envelope period: reg 12 = high byte, reg 11 = low byte.

So a channel has three sub-voices, which share one noise byte and one envelope period. Channels are cleared with 37 `clr.w` at `$201F8`.

**Scripts** (`$2020A`, run only while `+0` is negative). If the wait counter is nonzero, it's decremented and nothing else happens. Otherwise ops run until a wait or the end. Each op is an op byte and an operand byte. The op class is `op & $7C` (`$40` and above means end). Bit 7 of op selects P instead of R as the destination. `op & 3` is the source mode: R[a>>4], P[a>>4], the immediate a>>4 (0 = 16), or the next word. Operand bits 0–3 give the destination index.

| op & `$7C` | Meaning |
|---|---|
| `$00/$04/$08/$0C/$10/$14` | dst = src / dst += src / −= / &= / \|= / ^= (words) |
| `$18/$1C/$20/$24` | Skip the next op if src = dst / ≠ / < / > (signed). The skip passes the next op word, plus a word operand if that op's bit 9 (source mode bit 1) is set and its class isn't `$20`. Immediate mode 2 is skipped wrongly the same way. |
| `$28` | pc += signed operand |
| `$2C` | R[op & 3] −= 1; if the result ≠ 0, pc += signed operand |
| `$30` | Tone enable (bit 7 clear) or noise enable (bit 7 set) of sub-voice a & 15 = op bit 0 ? `$FFFF` : 0 |
| `$34` | Wait: save pc, counter = operand. The script resumes `operand + 1` VBLs later. |
| `$38` | Priority of sub-voice a & 15 = src |
| `$3C` | End: clear the channel |

Explosions set R15 with a word immediate (`$030F`, as if they meant P3) and clear tone 0, which was never on. Both are no-ops.

**Per VBL** (`$20486`). If `$20484` = 0, return. Otherwise run the six scripts in order, clear the 18 allocation flags (`$20640`), clear the "noise written" and "envelope written" bytes (`$20482`/`$20483`), and set mixer = `$7F` and buzz mask = 0. Then, for PSG voice 2, 1, 0:
- **Allocation** (`$20600`). Scan channels 5 → 0, skipping free ones (flag pointer += 3). For each, scan sub-voices 2 → 0. Pick the highest priority > the best so far (signed, starting at 0) whose flag is clear. So the first one seen wins ties. Set its flag; when nothing is picked, a dummy at `$20654` is set instead.
- **Nothing picked:** vol = 0.
- **Tone enabled:** clear mixer bit v; write reg 2v = P low byte, reg 2v+1 = P high byte.
- **Noise enabled:**
  - Only the first such voice in the VBL writes reg 6 (the noise byte) and handles Timer A. If bits 5–7 differ from `$20714`, Timer A is reprogrammed: IMRA, IERA and ISRA bit 5 cleared, TACR = bits 5–7 (stored in `$20714`). Unless that's 0, the vector `$134` = `$20668` is installed and IERA/IMRA are set again. `$20713` = (noise & 31) × 6 is unused.
  - Every noise voice clears mixer bit v+3 and sets buzz-mask bit v.
- **Volume:** write vol reg 8+v = the volume byte, also saved in `$2070E+v`. With bit 4 set, the first such voice writes reg 11/12 from its channel's P7.

After the three voices:
- If no noise was written, TACR = 0, IMRA bit 5 is cleared, and `$20714` = 0.
- Write reg 7 = mixer (bits 6–7 = 01: port A is output, port B is input).
- Write `$20715` = buzz mask.
- PSG write order: voice 2, 1, 0 (tone lo, tone hi, [reg 6], vol, [11, 12]), then reg 7.

**Timer A** (`$20668`) works as follows:
- It advances the 64-bit RNG at `$1236` the way `$195FE` does: `add.w`, then `addx.w` chained over the two longs.
- It writes TADR = the new low byte. It's a delay-mode timer, so a random period of prescale × (1–256) / 2.4576 MHz.
- It toggles `$20712` with `not.b`. When the result is negative, it restores the buzz-mask voices' volumes from `$2070E`; otherwise it writes 0 to them.
- It ends with `bclr #5,ISRA` (software end-of-interrupt).

TACR 1/2/3/4 (÷4, ÷10, ÷16, ÷50) give interrupts every 1.6 µs–21 ms. At ÷4 most periods are shorter than the handler (about 430 CPU cycles), so the real rate is CPU-bound, at roughly 18 kHz. Because the handler advances the **game RNG**, sound changes the game's random numbers. This is the reason island layouts differ between games (`docs/units.md` §16). The port keeps a private RNG copy, so the simulation stays deterministic.

**Vehicle sounds** (`$20730`, per game pass). These use channels marked 1, driven by code instead of a script.
- **Engine sound** (`$2073A`). When `[$A02]` changes:
  - Stop the proximity sound (`$20B72`), then stop the old kind `[$A16]` through `$2088C`: 0 frees `[$A18]`, 1 frees `[$A1E]`, 2 does nothing, and 3 calls `$2092A`.
  - If `[$A02]` = 0, or `$1248` (background noise off) is set, `[$A16]` = −1. Otherwise `[$A16]` = `[$A14]` (0 Manta, 1 Walrus, 2 carrier, 3 drone).
  - Start through `$2089C`. A Manta (`$208AC`) or Walrus (`$208E0`) takes a free channel, or channel 5 if none is free; that channel isn't cleared. It stores the pointer in `$A18`/`$A1E`, sets speed memory `$A1C`/`$A22` = −1, `+0` = 1, priorities `$B4`, `$23`, 0, P3 = 4 (Manta) or 2 (Walrus), tone on sub-voice 0, noise on sub-voice 1. Carrier and drone have no engine sound.
- **Every pass** (`$207A8`):
  - **Manta** (needs `$70C`). The bay record is `$496 + $34·[obj+$1A]`. Speed = rec`+6` + 100, or 50 when on autopilot (bit 0) and in mode 1 or 5. It's smoothed with `turn_towards` (`$1A18A`, ±2 per pass). P0 = 120 − (speed − 100) >> 3, and both volumes = 6.
  - **Walrus** (needs `$70E`). Speed = rec(`$570+…`)`+4`, smoothed the same way. P0 = 400 − speed, volumes = 6.
  - Without control, the volumes are 0. For a drone, `$A16` is set to −1.
- **Nearest other vehicle** (`$2092A`; skipped if `$1248` is set or `[$A02]` = 0). Every 16 passes (`$A10`), it rescans objects 2–9 (`$5DF5A`, the Mantas and Walruses) for the nearest visible one (`+1` bit 0) other than `[$A02]`. Distance is the Chebyshev distance on longs; on an unsigned tie the later object wins.
  - None: stop.
  - Same as before: update only.
  - Otherwise stop the old one, then take a channel for the new one: `+0` = 1, priorities `$2D`, `$1E`, 0, P3 = `$1A`, the P0 high byte = `$FF` (no smoothing the first time), tone on sub-voice 0, noise on sub-voice 1, `$A12` = 0 for type 5 or 1 for type 6.
  - **Bug:** `$201C0` leaves A1 pointing into its channel list. The update that follows therefore measures from there instead of from `[$A02]`, and normally finds no valid distance (volume 0) on that pass.
  - **Update** (`$2094A`). d = the Chebyshev distance. If d doesn't fit a word, volume = 0. Otherwise volume = max(0, 6 − d>>>11), and the closing speed = d − the previous d (`$A0E`).
    - Manta: P0 = clamp(`$50` + (speed if \|speed\| > 32, else 0), `$2D`, `$96`).
    - Walrus: P0 = clamp(`$190` + (speed if \|speed\| > 32, else speed>>4), `$12C`, `$1F4`).
    - Either way, P0 moves ±1 per pass towards the target.

**Variables.** `$A06` previous `[$A02]`, `$A0A` proximity object (high byte `$FF` = none), `$A0E` its last distance, `$A10` rescan timer, `$A12` its kind, `$A16` engine kind, `$A18`/`$A1E`/`$A24` the Manta/Walrus/proximity channel pointers, `$A1C`/`$A22` smoothed speeds. In the code area: `$20482` noise written, `$20483` envelope written, `$20484` enabled, `$20640` allocation flags, `$2070E` saved volumes, `$20712` toggle, `$20713` unused, `$20714` current TACR, `$20715` buzz mask.

**PSG register 13** (envelope shape) is never written by the game. The one envelope-volume effect (id 9, two frames of volume `$10`) therefore plays whatever TOS left there, which is a finished single-shot shape (keyclick 3, bell 9). That voice is silent, so the lock-on is a "pip – pip".

**Extraction and verification**
- `tools/stsound.py` runs the engine under Unicorn with an MFP Timer A model, in integer MFP cycles (49152 per frame):
  - The timer is free-running, and a counter reload takes the data register written by the previous interrupt.
  - Changing TACR restarts the prescaler.
  - The handler takes 132 MFP cycles. Timeouts during it collapse into one pending interrupt.
- `tools/extract_sounds.py` renders WAVs with `tools/ym2149.py`.
- `tools/export_sound_vectors.py` records scenarios to `extracted/sound_vectors.json.gz`: every id, every level at 12 distances, overlaps with stealing, pause, vehicle sounds, and 6 fuzz runs. `npm run check-sound` (`web/scripts/check-sound.ts`) replays them through `engine.ts`: identical PSG writes (time, register, value), Timer A state, and a CRC of the engine state after every event.
- `web/src/audio/` plays the engine live: `sound.ts` is the API, `worklet.ts` runs the engine and a YM2149 (`ym2149.ts`) in an AudioWorklet.

## Palettes

`tools/extract_palettes.py` extracts four 16-colour palettes stored back to back at `$132A`, `$134A`, `$136A` and `$138A`, plus the colour-15 cycle at `$13AA`. The VBL copies the palette that `$1326` points to.
- **`$132A`:** the default, used by most screens and the 3D views.
- **`$136A`:** the damage screen (`$1404E`), with colour 7 patched to `$445`. The model export uses it for models 74–82.
- **`$134A`:** the damage screen when `[$B62]` is set; also `$FBD6` and `$13AF0`.
- **`$138A`:** `$142E6` when `[$D14] == 1`; also `$10996`.

## Copy protection

`$A2C0` picks a question by language (`$127E`) × 64 + random. Questions and answers are XOR-encrypted with key `(i + 1) ^ $61`, and answers also with the byte position. `tools/extract_protection.py` decodes all 192.

## Fonts

Extracted by `tools/extract_font.py`; the formats are in its docstring.
- **5×5** at `$4B8A8` (`!`–`Z`, 6 px advance), drawn by `$9A56–$9B78`. The text stream uses control codes (`5 n` sets colour n, `6` …). The routine is self-modifying: the plane operations for the current colour are patched into the zeroed slots (`$9B78`). Messages are uppercased first (`$A206`, which copies into the message log list at `$65D10`).
- **8×8** at `$4B9CA` (ASCII `$20–$7F`), drawn by `$9C8A`, in 8-px character cells.
- **Vector** (scalable stroke font): offset table `$9DF0`, characters `$20–$5A`, drawn by `$9CEE`/`$9D6E` with line segments (`$5CE2`).

## Screens

`screen_dispatch` (`$14472`) is called each frame. It jumps through `screen_handler_table` (`$144A0`, 24 longs) indexed by the view number at `$718`. Entries 5–7, 13–15 and 21–23 point to a no-op (`$1449E`). Identified so far: 8 = `screen_laser_turret` (`$15A94`). Other handlers: `$1460A`, `$156FA` (near "Damage Status"), `$14E3C`, `$14C6C`, `$162B0` (near the drone strings), `$165B2`, `$1638C`, `$15EB0`, `$16BA6`, `$14E56`, `$15496`, `$14A40`, `$169FE`.

## 3D renderer (partially understood)

Per frame, a 3D view (for example `screen_laser_turret`) sets the camera, then runs:

1. `scene_collect_visible` (`$7BF6`). It walks the camera's island buffer (`$40`-byte entries) and the world objects at `$5DCDA`, and culls each one against `object_cull_table` (`$8598`). Visible objects go to `depth_tree_insert` (`$6246`), which builds a binary tree at `$58E2A` keyed on camera-space Z. More than 128 nodes → `trap #12`. Details in "Bridge view camera and projection".
2. `draw_horizon_sea` (`$6402`): clips the horizon line to the view and fills sky above, sea below, straight into the screen (`$55D4`).
3. `draw_sea_dots` (`$91EC`): the 8×8 "Sea Dots" grid of water points (an option in the settings menu).
4. `draw_depth_tree` (`$62D4`): an in-order (back-to-front) walk calling `draw_scene_object` (`$7B48`) for each node.
5. `draw_scene_object` → `$7B72` picks the level of detail from the node's camera-space Z. `object_lod_table` (`$8B58`) has 16 bytes per object type: `near_dist.l, mid_dist.l, near_model.w, mid_model.w, far_model.w, pad`. The chosen model is looked up in `model_table` (`$4A06A`).

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

Vertex builder `$5A02`: a count word, then (op, index) pairs with the ops load, store, midpoint, add, sub and negate. They're dispatched via `jmp (pc,op)` into 24-byte slots at `$5A1C`. Because the basis routine has already rotated the corners into camera space and the ops are linear, V[] is in camera space relative to the object. Projection is `$5912`. The game frame is x right, y up, z forward. Rotation helpers `$A4EA–$A5DE` apply (sin, cos) pairs from `$A68–$A7E` (2.14 fixed point; sine table at `$1F5A`). The camera pairs `$A74–$A7E` are set per frame by `$A484`/`$A494`/`$A4A4`. All of this is specified below.

Polygon colour → `$13CA` table of palette-index pairs (equal = solid, different = dither). Fill via `$5F18`; span/pattern tables at `$1D5A`. `$55D4` is the scan-line span filler: it patches a `jmp (a2)` (`$4ED2`) into its unrolled loop at run time.

**Extraction**: `tools/extract_models.py` exports all 96 non-empty models to OBJ with previews.
- 80 are decoded in Python. `tools/check_models.py` checks them against the game's own code run in Unicorn (`tools/stemu.py`), and all match.
- The 19 with runtime-dependent basis routines are evaluated in the emulator. The context used is in `stmodel.EMULATION_CONTEXT`:
  - 75–82 are drawn after parent model 74. This is inferred: their vertices are fractions of 74's box, or index 74's vertices directly.
  - Animated models use `+$24 = $200` (fully deployed).
  - Model 8 uses sample object values (100, 100).
  - Lifts are at 0 and the turret faces forward.
- `web/src/render/models.ts` ports every basis routine to TypeScript and builds all 99 models from a RAM image. `uv run python tools/export_model_vectors.py` writes `extracted/model_vectors.json` (the game's vertex arrays under Unicorn for 396 contexts, including fuzzed object fields and globals), and `node web/scripts/check-models.ts` checks the port against them; all match.

Basis routine details (from the port):
- The box routines rotate by the object's angles (`$A472`/`$A454`/`$A468` → pairs `$A6C` heading minus camera heading, `$A68` pitch, `$A70` roll) and then by the camera (`$A74`, `$A7C`). `$A946` (used only by `$ABE8`) is the same box without the camera pass. `$AA24` (trees) and `$A660` use only the camera pairs, so trees are billboards.
- Turret and two-box routines point A4 at fake object records inside the code and patch their angles: `$AAB4` (carrier turret: heading = object `+6` + `$6CC`, then pitch `$6CA` for the second box, at V[`$F0`] and V[`$E8`]), `$AF80` (`$AF4E`: first box heading only, second box also the object's pitch), `$AD9A` (`$AC16`: second box heading = object `+6` + object `+$26`).
- `$AC16`/`$ADA4` copy object `+$24` to `$118C`, run their own first vertex-op stream (the handler then runs a second one), then blend: `$100` copies V[4–7] to V[0–3]; above `$100` the low byte moves V[0–3] towards V[4–7] (0 = unchanged, so `$200` keeps the built vertices); below `$100` (signed) V[4–7] = V[8–11] + (V[4/5]−V[8/9])·f/256, copied to V[0–3].
- `$AABE`/`$AB42` lifts: V[`$60`] = `$AE0`·`$DD8`, (`$AB42`) V[`$61`] = `$AE0`·`$DD6`, V[`$5C`] = `$AE0`·`$DD8` (Q15 of the box's height edge). They also set the BSP flag `$8E6+$41` (object at `$6DA` with `+$1E` ≤ −1000, or `$DE6` = 4). BSP nodes may test flags `$40` (always set by `$98E6` when the model has planes) and `$41` (models 2, 3). The walker draws both children either way, so these flags only change the draw order.

**Identified models**: 1–4/86 carrier (near detail, with turret/lifts), 85 carrier hull, 74 carrier wireframe, 75–82 carrier-system overlays (8, matching the 8 systems on the damage screen), 84 turret wireframe, 5 Manta, 6 Walrus (the object type equals the model number; see `docs/units.md`), 17/18/28/29 and 19/34 missiles, 88/36/47 runway (built / near / under construction), 90–98 island buildings with 41–46 near and 48–57 under-construction variants, 7 volcano, 24–27 trees, 10–13 carrier defence drones, 58–73 debris shards, 31 flare/flash disc.

## Bridge view camera and projection

Screen 0 renders with `$1460A`: camera setup, `scene_collect_visible`, horizon, sea dots, depth tree, then 2D overlays (`$146A4`, sprites). `tools/stview.py` is a Python reference for all of it. `uv run python tools/stview.py --check` runs the game's own `$1460A` under Unicorn on the bridge captures plus 40 random viewpoints on two of them (`--fuzz N`), and compares the camera, pairs, draw list, LOD models, horizon, sea dots and every projected vertex. Everything below is confirmed that way unless marked *(guess)*.

**Angles and trig.** Angles are 16-bit (`$10000` = 360°). `$A438` looks up (sin, cos) in the 4096-word table at `$1F5A` (exactly round(16384·sin), index = angle >> 4), so the low 4 bits of every angle are dropped. Two rotation forms, on words, result = high word of 4·(…) (floor of /16384, 32-bit wrap):
- form A (`$A4F4` pitch, `$A5BC` roll, camera `$A4EA`/`$A5B2`) on (u, v): u' = u·c − v·s, v' = v·c + u·s. Pitch acts on (y, z), roll on (x, y).
- form B (`$A558` yaw, camera `$A54E`) on (x, z): x' = x·c + z·s, z' = z·c − x·s.
- `$A4B4`/`$A518`/`$A57C` are the long versions: halve both inputs until within ±`$4844`, rotate, shift back.

**Object → world.** world = P + Yaw(+6)·Pitch(+4)·Roll(+8)·v (roll applied first, then pitch, then yaw; forms above with the object's angles). Forward (0,0,1) → (sin yaw·cos pitch, −sin pitch, cos yaw·cos pitch): yaw 0 faces +z, yaw `$4000` faces +x, pitch `$4000` points down, positive roll lifts the +x side. Model units are world units (no scale). P is `+$A/+$E/+$12` (longs) for world objects; island-buffer entries are buffer origin (header `+2/+6/+$A`) + local words `+$A/+$C/+$E`. The box basis (`$A78C`) composes the whole chain in one go: object roll, object pitch, a single yaw by (object yaw − camera yaw), camera pitch, camera roll. Exceptions: `$AA24` (trees) is a **billboard**: corners (±sx, ±sy, 0) get only the camera pitch and roll, so the quad always faces the camera's heading. `$A660` (island plateau) uses the camera pairs only (island yaw 0).

**Camera.** `$9FA` is the camera object (object 0 on the bridge), `$9FE` the viewed object (the carrier, object 10). `$13C24` (enter screen 0) sets `$9FA` = object 0, `$9FE` = `$A02` = object 10, object 0 `+$E` = **240** (absolute world y; carrier hulls ride at y = 160), viewport `$6FE` = `$4744`, palette `$132A`, and `$1230` (radar list at `$5892A`). Each frame `$1460A` copies the carrier's pitch, yaw, roll and island pointer (`+$3C`) into object 0 and sets x, z = carrier + formB_yaw(0, −100): the eye is **100 units behind the carrier origin**, at y = 240, with the carrier's orientation and no look offset. The leave routine `$143D4` re-sets the visible bit of `[$9FE]`.

**World → camera.** `$A484`/`$A494`/`$A4A4` store (sin, cos) of the **negated** camera pitch, yaw, roll at `$A74`/`$A78`/`$A7C`. For d = point − camera (longs): (x, z) ← formB(x, z) with −yaw, then (y, z) ← formA(y, z) with −pitch, then (x, y) ← formA(x, y) with −roll. That's the exact inverse of the object transform: camera space = Roll(−r)·Pitch(−p)·Yaw(−w)·d, X right, Y up, Z depth. Because the pairs hold the negated angle, the camera's effective angles round up to a multiple of 16: −((−a) & `$FFF0`).

**Projection** (`$5912`; `$5902` is the same with `$A90` = near − Z for the clipping models). Per vertex, x = V.x + X, y = −(V.y + Y), z = V.z + Z (X, Y, Z = object origin in camera space, `$A80/$A84/$A88`). If z < `[$A8C]` (**64**), the vertex is flagged (y = `$80000000`). Otherwise halve x, y and z together until z < `$8000`, then sx = cx + trunc(x·2^shx / z), sy = cy + trunc(y·2^shy / z) (divs, truncation towards zero; a long fallback on overflow). Outputs are longs; `$1220` is cleared if any vertex is off-plane or doesn't fit 15 bits.

**Viewport record** (`[$6FA]`, from `$6FE`), 11 words: `x0, y0, x1, y1, shx, shy, cx, cy, width, height, ?`. The bridge's `$4744` = 32, 6, 287, 133, 8, 8, 160, 70, 256, 128: 256×128 pixels (inclusive bounds), focal length **256 px** both ways, centre (160, 70), so 64 rows above the centre and 63 below. `$4770` (flares and drone screens) is the same rectangle with shx = shy = 7: focal length **128 px**, a wider view. `$475A` (the hangars) is 32, 6, 159, 69, shift 7, centre (96, 38). In Hatari screenshots (832×588) the game pixel (x, y) is at PNG (96 + 2x, 58 + 2y).

**Horizon** (`$6402`): (y, z) = formA_campitch(0, `$1000`). If |z| ≤ 64, the view is all sky when camera pitch < 0 (looking up), all sea otherwise. Else the points (∓8192, y) are rolled (camera roll), negated if z < 0, and projected at depth z with a zero origin: the vanishing line of horizontal directions (camera height is ignored). Everything on and above the line in the viewport is **colour 2** (sky, `$057`), everything below is **colour 10** (sea, `$026`), drawn straight to the screen; no line colour. At pitch 0 the horizon is row 70.

**Sea dots** (`$91EC`, skipped while `$1246` is set): world points on y = 0 at x, z multiples of `$2000` (8192), 8×8 of them from the grid line just ahead of the camera −4…+3 steps in each axis. Camera rotation on words (no prescale), drawn if z ≥ 100 and inside the viewport, as one pixel of **colour 8** (`$777`) via `$4B2C` (colour `[$9DE]`). Both x and y use shx.

**Visibility** (`$7BF6`). `object_cull_table` `$8598`, per type: `range.l, ?.l, min_z.l, radius.l`. An object is visible when its live and visible flag bits (byte `+1` bits 0 and 1) are set, |dx| and |dz| (world, relative to the camera) ≤ range + `[$A98]` (Chebyshev, unsigned), camera Z ≥ min_z (negative for big objects, = radius for small ones), and |X| − radius ≤ max(Z,0)·2^(7−shx) and |Y| − radius ≤ half that (`$853A`; the shift at `$8540` is patched by `$8560`). The second long (≈ range/√2) isn't used here. Examples: carrier 46400/−1088/1088 (`$1` type), island 131072/−32800/32800, trees 17400/245/245.
- Only **one island** is drawn: the buffer at `[camera+$3C]` (copied from the carrier). Its origin goes through the long camera transform; each entry's local words go through the word transform and are added.
- Entry 0 (the plateau, type 8) is inserted first, so it's the tree root. Then, if the camera is at y ≥ `$50` (`$8A6`), entries 1…n with n = header `+$10` (the runway-slot count, `$74E` at `load_island_buffer`) are not sorted: they get bit 2 and their camera position in `+$1E/+$22/+$26` and are drawn right after the plateau, in buffer order. The other entries are sorted individually.
- **Island partition:** with the plateau in the tree, the remaining entries and the world objects are forced into the root's far subtree (`$622E`, drawn before the island) or near subtree (`$623A`, after it), then sorted by Z inside it. The rule uses the rectangle island centre ± (half-extent − `$50`) in camera-relative x/z: when the camera is outside it, objects over the rectangle (and the entries) go in front if the camera is high and behind if low; other objects go in front only if they share an outside side with the camera, otherwise behind. When the camera is over the rectangle, objects over it go in front and the rest behind. Without a visible plateau, everything is sorted plainly.
- World objects need bit 2 of byte `+1` clear (vehicles on the lift or in the dock are drawn as carrier attachments instead). `$A46`/`$A47` skip the world objects and the four extra lists at `$11D8`: the **debris bursts** (18-byte records: flag, origin, list head; shards at `$66010` linked by `+$16`, local word positions, culled per shard and inserted by plain sorting, `$852A` = `$6246`, so not forced into the island partition). None was active in the captures, so they're unchecked.
- Tree node (18 bytes): `Z.l, near.w, far.w, object offset from $5DCDA .w, X.l, Y.l`. A key ≤ the node's goes to the near side (`+4`); the walk draws far side, node, near side, so ties draw later.

**LOD** (`$7B72`): with d = Z − `[$A94]`: d ≤ near_dist → near model, ≤ mid_dist → mid, else far. The metric is camera-space depth, not distance. `$A94`/`$A98` are zoom biases (the laser turret); the bridge's leave routine clears them, so they're 0 here.

**Island plateau** (model 8, `$A660`, half-extents from entry 0 `+$1E/+$20`): a base ring at the buffer origin's y (0) with the full half-extents, and a top ring at y = +80 inset by 80 on each side.

## Vehicle and weapon views (screens 8, 9, 10, 16, 18, 19, 24, 26, 27)

The render dispatch `$14472` calls `[$144A0 + 4·screen]`: 0 `$1460A`, 8 `$15A94`, 9 `$165B2`, 10 `$1638C`, 16 `$14E56`, 18 `$15496`, 19 `$14A40`, 24 `$15002`, 26 `$14816`, 27 `$15244` (the 2D screens are not covered here). Each one places its camera object, then runs the bridge pipeline unchanged: `$7BF6` with camera `[$9FA]` (its position `+$A/+$E/+$12`, angles `+4/+6/+8` and island buffer `+$3C`), `$6FA` = `[$6FE]`, horizon `$6402`, sea dots `$91EC`, depth tree `$62D4`, then 2D overlays. So everything in "Bridge view camera and projection" holds with the camera below. `tools/stview.py` models each screen (`screen_views`); `--check` runs the screen's own render under Unicorn on the `emu/out/views` captures (`tools/capture_views.py`) plus 40 random states each (`perturb_view`: carrier attitude and place, the vehicle's attitude and height, turret angles and zoom, crane angles, rear view, the Walrus missile inset, the no-view cases) and compares the camera object and pairs, the draw list, LOD and attachment models, horizon, sea dots and every projected vertex: all exact.

Formulas use the bridge's fixed-point forms: formB_yaw(x, z) with the carrier's heading = the object→world yaw (x' = x·cos + z·sin, z' = z·cos − x·sin, high word of 4·…); "local (x, z)" is in the carrier's frame (x right, z forward). y values are absolute world y (sea level 0, carrier hull at 160). Camera angles follow the object convention (pitch `$4000` = looking down, yaw `$4000` = facing +x).

| Scr | Camera object | Eye | Angles (pitch, yaw, roll) | Viewport | Island |
|---|---|---|---|---|---|
| 0 bridge | object 0 | carrier + formB(0, −100); y 240 | the carrier's | `$4744` (f 256) | carrier's |
| 26 Manta deck | object 0 | carrier + formB(420, 1050); y 230 | 0, carrier yaw + `[$DDE]`, 0 | `$4744` | carrier's |
| 19 Walrus dock | object 0 | carrier + formB(−420, −1050); y 50 | 0, carrier yaw + `[$DEA]`, 0 | `$4744` | carrier's |
| 9 flares | object 0 | carrier + formB(0, −1000); y 300 | `[$1158]`, `[$115A]` (absolute), 0 | `$4770` (f 128) | carrier's |
| 8 laser turret | object 0 | see below; y = 320 + 30·cos e | `[$6CA]`, carrier yaw + `[$6CC]`, carrier roll | `$4786`, f = 256·2^`[$6D2]` | carrier's |
| 10 viewing drone | object 15 itself | its position | its own | `$4770` | its own |
| 27 Manta | the Manta (`[$492]`) itself | its origin | its own | `$4744` | its own |
| 18 Walrus | object 0 | Walrus x, z; Walrus y + 45 | Walrus pitch, Walrus yaw + turret `+$26`, Walrus roll | `$4744` | Walrus's |
| 24 Manta arming | object 0 | as the bridge: carrier + formB(0, −100); y 240 | the carrier's | `$475A` (f 128) | carrier's |
| 16 Walrus arming | object 0 | carrier + formB(0, −1000); y 140 | the carrier's pitch and roll, carrier yaw + `[$DC8]` | `$475A` (f 128) | carrier's |

The enter handlers (`docs/units.md` §10.5) set object 0's y (and zero its flags, pitch and roll where the table says 0); the render writes the rest every frame. The camera object is never drawn: object 0's flags are 0, and entering 10/18/27 clears the viewed object's visible bit (screen 18 clears the Walrus's again every frame).
- **Deck and dock cameras** are fixed on the carrier (ignoring its pitch and roll) and pan: `$DDE` (`crane_step` `$1F070`) turns by up to `$400` a pass towards the bearing from the deck camera point to the Manta on the lift (`$DD0` ≥ 0), else back to `$93A0` by `$100`; `$DEA` towards the docked object (Walrus, or defence drone / supply drone) from the dock point, at least `$4000`, else back to `$4000`. Below the 3D view both screens print the four bays' status every `[$95C]` frames (UI).
- **Arming screens (hangar views)**: no hangar interior and no vehicle model: both draw the ordinary world (the full bridge pipeline: `$7BF6`, horizon, sea dots, depth tree) into the small viewport `$475A` = (32, 6)–(159, 69), shx = shy = 7, centre (96, 38), 128 × 64 (the same angles as the full view: tan half-angles 0.5 across, 0.25 up). **24** (`$15002`) is the bridge camera exactly (the code is `$1460A`'s), so it shows the bow, the deck and a Manta on the lift (attachments as on the bridge). **16** (`$14E56`) puts the eye 1000 astern of the carrier origin (the offset turned by the carrier's yaw: `$A438` takes d7 = the carrier's yaw, not the look yaw) at y 140, looking along carrier yaw + `[$DC8]`; `$DC8` swings by `[$DCA]` = ±`$100` a pass between `$6000` and `$A000` (`$17748`, screen 16's input routine), so the view pans ±45° around dead astern (the swing runs through the model's `screen_input`): out of the stern door, over the wake (anything astern is drawn, e.g. a Walrus leaving the dock). The carrier itself is behind the eye (camera Z −1000; nothing of it is projected). The enter handler makes object 0 type `$53` with flags 3, so `$7BF6` also inserts the camera object itself (Z 0; cull record 83: range and radius 65536, min Z −65536), but **model 83 is empty** (`$99F0` = `rts`): the "hangar model" draws nothing. The palette is the default `$132A`. Then the 2D panels (UI): the redraw while `$95E`/`$960`, the carrier tank bar `$15200` and the bay list (docs/units.md "Screens 24 / 16"). `tools/stview.py --check` covers both on the `emu/out/views` captures (`walrushangar*`, `mantahangar*`: a bay with a loadout selected, the lift and the dock in use) plus 40 fuzzed states each (swing, carrier attitude and type, lift, attachments): all exact; each rendered frame equals the frame on display in its capture, and the Hatari screenshots (a frame earlier, while the stern view pans) differ in at most 84 of 8192 pixels.
- **Flares camera**: the observation turret 1000 astern; `$1158/$115A` turn by `$800` a pass to aim at `[$1154]` (the flare) from (0, 300, −1000) carrier-local, else back to pitch 0 and the carrier's heading by `$100`.
- **Laser turret** (`$15A94`), only while the turret system works (`[$B12]` ≠ 0; else no 3D and static `$5538`). shx = shy = `[$6D2]` + 8 are written into `$4786` (zoom 0–3: ×1, ×2, ×4, ×8, shown as "x1"… in the box at (53, 16)); the enter handler sets the LOD bias `$A94` = zoom·10000 and the range bias `$A98` = zoom·14142. Eye, with e = `[$6CA]` (gun elevation, + down), h = `[$6CC]` (turret heading relative to the hull): (u, v) = formA_e(30, 0) (u ≈ 30 cos e, v ≈ 30 sin e); y = 320 + u; (p, q) = formA_{−h}(0, v) (p ≈ v sin h, q ≈ v cos h); local (x, z) = (p, word(q − 350)), then formB with the carrier's yaw: the eye sits 30 from the pivot (0, 320, −350) along the gun's "up", turning with heading and elevation. Overlays: the cross-hair line list (`$15D1A`/`$15C9A`), a colour-0 dot at (160, 70), and **model 84** (the turret wireframe) drawn by its handler directly into window `$479C` (15, 157)–(96, 196), focal 256, centre (56, 177), cleared to colour 0, at camera-space origin (0, −40, 1200): its basis `$ABE8` turns it by `$6CC` and `$6CA` only (no camera rotation), so it shows the turret's attitude.
- **Viewing drone** (`$1638C`): 3D only while object 15 is live; otherwise static (`$4A64`) and a message. Under the view: counters `$C9C`, `$BB2` and a status line (UI).
- **Manta** (`$15244`): 3D only if `[$70C]` ≠ 0 (controllable), the record's status `+1` ≥ 0 and the object live; else static. Telemetry `+$2E` = 2 (lost): no 3D, only noise `$5538`; `+$2E` = 1 (weak): noise over the view. **Rear view** (`$8F1`): the Manta's yaw + `$8000`, pitch and roll negated for the frame (restored after). **Sea dots are drawn one row lower on this screen** (`$9306`: `cmpi.w #$1B,$718`). Overlays (not in rear view): the fixed sight brackets (line list `$15416` over points `$153E6`, relative to (x0, y0): corners of the box (115, 25)–(204, 114)), and the moving cross at (cx, cy) + sight, sight = `$1162/$1164` written here: weapon 1 (missile) → (rudder `+2` >> 3, −pitch `+4` >> 3); weapon 0 → (0, 0); other weapons: no cross (and `$1162` kept). The cross shape changes while locked (`$116C` ≠ 0: list `$15479` instead of `$15460`). In mode 5 (landed on a runway) a box (135, 116)–(184, 126) with a caption. `+$32` set: the radar scope (`$146A4`, record `$481E`: (39, 82)–(81, 126)) with the unit as a colour-8 dot at its centre.
- **Walrus** (`$15496`): 3D only if `[$70E]` ≠ 0 and the Walrus is live (else static); `+$2C` = 2: noise only. `$9FA` is set to object 0 here (the enter handler leaves it 0). **Rear view**: object 0's yaw + `$8000` and pitch negated (roll kept). Not guiding a missile and not in rear view: the Manta's brackets and the cross at the centre (sight (0, 0)). `+$32`: the radar scope as for the Manta.
- **Walrus guided-missile inset** (record `+0` bit 4): after the main view, the Walrus's visible bit is set, `$9FA` = the missile (Walrus `+$20`), its visible bit cleared for `$7BF6` and set again after. The viewport `$4834`, patched each frame with g = record `+$2E` (the guidance hold, counting down by 8 while steering): x 144–271, y0 = g − 32, y1 = min(y0 + 63, 133), cx = 208, cy = g, shx = shy = 7 (focal 128), height y1 − y0 + 1; a frame is drawn round it (`$5C60`). **Culling still uses `[$6FE]` = `$4744` (shx 8)**: `$6FE` points at `$4834` only after `$7BF6`, so objects near the inset's left/right edges are culled as for a 256-focal view. Then horizon, sea dots and the tree in the inset, a colour-8 dot at (208, g) if g ≤ 133, and the missile's `+$1C` byte as two digits in the box `$484A` (262, 14)–(281, 22). The camera stays on the missile after the frame (`$9FA`), and the Walrus stays visible until the next frame clears it.

**State the views leave** (ported by `tools/stunits_render.py` `render_effects`, docs/units.md §10.8): the camera object's fields, `$A4A–$A65` (island origin − camera, partition rectangle), `$8A6`, the runway entries' bit 2 and `+$1E/+$22/+$26`, the object pairs `$A68–$A73` of the last box-based model drawn (or the radar overlay's camera-yaw pair, the turret wireframe's, the dock's status pair), the inset viewport and `$9FA` on screen 18. The order of the lift and dock vehicles in the carrier's BSP (root plane 0 of model 2, plane 3 of model 3) decides which of them leaves its pairs.

**Vehicles on the lift and in the dock** are not world-pass objects: `dock_attach` (`$1DC6C`) sets byte `+1` bit 2 on the Manta on the lift (`[$6D6]`, from `$DD0`) and the object in the dock (`[$6DA]`: a Walrus, a defence drone while `$122F`, or the supply drone for `$DE6` = 4), which `$7BF6` skips, and sets the carrier's type: 3 while the lift moves (`$DCE`), 2 while something is on the lift or in the dock or the door moves (`$DDA`), else 1. Types 2 and 3 select models 2 and 3 (near; 86 beyond 8192) whose BSP trees hold attachment slots (`$80–$FE`, slot = ~byte): `$95C2` → `$6346` draws slot 1 = `[$6D6]`, slot 2 = `[$6DA]` (zero: nothing) inside the carrier's BSP walk, so it's painter-ordered with the carrier's own polygons. The vehicle is drawn with the world-object transform (its own position and angles, long camera transform from `[$9FA]`), the LOD model for its camera Z (`$7B72`), no range, frustum or flag test, using the second vertex buffers (`$8DE/$8E2/$8E6` swapped to `$5AEDA…` and back). For a port: when the carrier is drawn with model 2 or 3, also draw `[$6D6]` and `[$6DA]` at their own transforms (a depth buffer makes the BSP position irrelevant). The model's lift/door basis (`$AABE`/`$AB42`) uses `$DD6/$DD8` for the lift platforms.

## Runtime-modified bytes in the code area

A title-screen snapshot (1500 frames after `game_main`) differs from the file in 18 small ranges of the code area. These are self-modifying patch sites and variables embedded in code, so don't treat them as constants:
- `$48E6–$49F4` (9 ranges);
- `$4D65`, `$4D77`;
- `$539E–$541A` (3 ranges);
- `$8540`;
- `$AAB8` (the turret basis angle block at `$AAB4`);
- `$B187`;
- `$20654`.

Once a game starts, two more change, and both are simulation state (see `docs/simulation.md`):
- `$18D74`/`$18DCC`: command-centre max health, patched by `difficulty_apply`;
- `$18EE9–$18EF1`: the tree quota row, decremented by the island census.

## Known gaps

- Auto-analysis missed code reached only through pointer tables (for example `$BFAA`). `AtariSTFindCode.java` added 239 functions, then 163 more from orphan code runs (about 890 in total). Some orphan runs may be tails of larger functions; merge them when found. About 41 KB of the code area is still undefined, mostly embedded tables.
- `$55D4`'s Ghidra function body is wrong. It includes the vertex-op handler slots at `$5A1C–$5AA6` and their zero padding, and it has bogus `CAS2` decodes. Use `tools/dis68k.py` for this area.
- `$22E62–$40E25` is fully accounted for as graphics: UI bitmaps, item icons and pictures. The only remainder is 620 zero bytes at `$3B1BA–$3B425`. Sound and title/background screens are not in this block. Island terrain doesn't exist as data: islands are procedural plateaus (see "Island world").
