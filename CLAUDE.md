# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

W3CCommand is a planned port of the 1988 Atari ST game Carrier Command to a modern browser app (desktop and mobile). The repository is moving from decompilation to the web build: there is a Ghidra project and its setup tooling, a Python reference model of the simulation verified against the original, its TypeScript port, and a first web app that runs it on states loaded from the emulator and renders the world (all below). Update this file when more tooling is added.

## Source material

- `roms/CARRIER.TOS` — the Atari ST executable (the decompilation target).
- `roms/Carrier_Command_1988_Rainbird.st` — the ST floppy disk image (game assets, data files).

These are the original game binaries; treat them as read-only inputs. The disk holds only `CARRIER.TOS` and `DESKTOP.INF`, so all game data lives inside the executable.

## How CARRIER.TOS loads

The GEMDOS text segment is only a loader. It enters supervisor mode, masks interrupts, and copies file bytes from offset `$400` onward to absolute address `$400`, then jumps there (`$400: JMP $405A`). From then on the game owns the machine and drives the hardware directly. **Payload file offset == runtime address.** TOS/EmuTOS ROM is not mapped because the game doesn't use it after takeover.

## Python tooling

The Python tooling is a uv project. The dependencies are in `pyproject.toml` and `uv.lock`, and the virtual env is `.venv/` (git-ignored).

- Run tools with uv: `uv sync` once, then `uv run python tools/<script>.py`. Don't use bare `python3`.
- Add a dependency with `uv add <pkg>`, never with `pip install`.
- `tools/stgfx.py` is the shared library for decoding ST graphics: it reads the payload, decodes palettes and masked sprites, and outputs images with Pillow. Put new format decoders there, so the extraction scripts stay thin.
- `tools/stmodel.py` decodes 3D models; its docstring is the format spec. `tools/stemu.py` runs payload routines under Unicorn (68000), with the payload loaded at `$400` and identity rotations. Use it to evaluate runtime-dependent code and to validate decoders. `uv run python tools/check_models.py` cross-checks the model decoder against the emulator; run it after changing `stmodel.py`.
- `uv run python tools/dis68k.py <addr> [len]` disassembles payload bytes with Capstone, independent of Ghidra's function bounds. Use it where Ghidra's analysis is wrong: computed jumps, handler slots padded with zeros, self-modifying code.
- Extraction scripts write to `extracted/` (git-ignored and regenerable). `uv run python tools/extract_all.py` regenerates everything (palettes, fonts, UI sprites/panels, items, models, islands, sounds, copy protection) and runs the model cross-check. Each `extract_*.py` docstring documents its format. Run `tools/split_tos.py` first, then for example `uv run python tools/extract_ui_sprites.py` → `extracted/ui/`.

## Simulation model

- `tools/stsim.py` is a Python reference model of the strategic layer: the island step (`world_sim_step`), the once-a-second logistics tick (`$F7A0`), route relaxation, and the enemy carrier AI outside close-range combat. `WorldState.frame()` runs one `game_main` pass. Its state is a 512 KB image in the game's own memory layout, with only `STATE_REGIONS` populated. Islands, slots and economy records are views onto that image, and `WorldState.from_ram` / `to_ram` exchange state with emulator RAM. Port new routines the same way: follow the original's signed and unsigned compares, word wrap and leftover-register quirks, then add any new state to `STATE_REGIONS`. `docs/simulation.md` documents the rules.
- `uv run python tools/capture_sim.py` starts a strategy game in Hatari headless (it pokes the menu mouse variables) and saves RAM before and after the warm-up, single sim steps, logistics ticks and enemy-AI calls to `emu/out/sim/`. It takes about 50 s.
- `uv run python tools/check_sim.py` compares the model byte for byte against those Hatari captures and against the game's own code under Unicorn, including long, fuzzed and enemy-scenario runs (about 40 s). Run it after changing `stsim.py`; it must print no MISMATCH.
- `tools/stunits.py` extends the model with the unit layer (`UnitState`; `docs/units.md` §14 lists what's ported). `uv run python tools/check_units.py` checks each ported routine against the original under Unicorn: the shared maths on random inputs, then every routine on the action-game pass captures plus fuzzed states. Make those captures with `uv run python tools/capture_sim.py --action --steps 20 300 1500 --ticks 0 --passes 40 --out emu/out/action`. An action game needs no input: the carrier sails to a waypoint and stops.
- `uv run python tools/capture_play.py` plays a scripted session in Hatari: clicking panel buttons the way a player would (Manta to deck and take-off, Walrus launch, supply transfer) and poking waypoints. It captures runs of passes to `emu/out/play/`; `check_units.py` compares the model with those too.
- `web/src/sim/` is the TypeScript port of the model for the web build: the same names, with the Python mixins as one class chain (its README has the conventions). `uv run python tools/export_sim_vectors.py` (several minutes) runs `check_sim.py` and `check_units.py` with a recorder attached and writes every model state and call they make to `extracted/sim_vectors/`; `cd web && npm run check-sim` (about 15 s) replays them on the port: same exceptions, return values, events and state CRC per call. After changing either model, run both.

## Web app (`web/`)

Vite + TypeScript + three.js. `cd web && npm install && npm run data`, then `npm run dev` (`npm run build` type-checks and builds to `web/dist/`).
- `npm run data` (`tools/export_web_data.py`, needs Hatari) writes the title-screen RAM image to `web/public/data/title.bin` (git-ignored game data, personal use). New games start from it: `src/app/game.ts` seeds the RNG, calls the model's `new_game(strategy)` and `new_game_screen()` (the original's `$42A8`; docs/units.md §16).
- The repo ships no game data. States exported from the emulator also load: a 512 KB RAM image (`tools/hatari.py snapshot`, the capture tools, Hatari's `savebin <f> 0 $80000`) or a Hatari memory snapshot (`.sav`, gzip; the RAM is found by the payload signature at `$404`). The payload is in the image, so models, palettes and tables are decoded from it (`src/app/state_file.ts`). Export writes the RAM back (Hatari `loadbin <f> 0`). The dev server lists the captures in `emu/out` (`vite.config.ts`, dev only).
- `src/app/runner.ts` runs one `game_main` loop per pass on the original's clock (VBLs per pass follow the screen, docs/units.md §1): the UI's held controls, `ui_frame()`, `game_frame()` (pause/menu, `screen_input`, `sim_pass`, end-of-game timer), `frame_flip_flags()`; the game clock and `$8FC/$8FD` per VBL. It stops with a message on unported code (`NotPorted`) or game over.
- The UI (`src/ui/`, modern HTML/canvas) never writes state: `src/app/controls.ts` calls the model's `cmd_*` methods, ported from the original's click handlers (docs/units.md §10). Screens change through `cmd_switch_screen` (the original's leave/enter handlers). Saves are whole RAM images in IndexedDB (`src/app/store.ts`).
- `npm run soak` plays new games with random commands for many passes in Node and fails on any exception or unported code.
- `src/render/models.ts` decodes all 99 models from RAM, including the runtime basis routines (carrier turret/lifts, plateau, animated, overlays). `uv run python tools/export_model_vectors.py` then `npm run check-models` compares it with the game's own vertex code under Unicorn and with `stmodel.py`.
- `src/render/world_view.ts` draws world objects and the loaded island buffers as the game does, plus every other island built with the model's own `load_island_buffer`. LOD uses camera-space depth like `$7B72`. It's camera-relative (positions are ~2^25), game (x, y, z) → three (x, y, −z). The game has no depth buffer; decal polygons get a layer (pulled towards the camera in the vertex shader), and BSP group visibility tests (`$9902`) run in the shader.
- `src/render/cameras.ts`: the bridge camera follows `tools/stview.py` exactly (eye y = 240, 100 behind the carrier, focal 256 on a 256×128 viewport, near 64; `docs/memory-map.md` "Bridge view camera and projection"); `uv run python tools/stview.py --check` verifies that reference against the game's render code under Unicorn. The free camera flies anywhere.
- In dev builds `window.app` exposes the session, camera, renderer and `frame()` (renders one frame; rAF pauses in hidden tabs).

## Hatari (running the original)

Hatari 2.6.1 is installed with `brew install hatari`, and its bundled EmuTOS boots on a 512 KB ST. `tools/hatari.py` builds the GEMDOS drive `emu/hd/` from `roms/` and auto-starts `CARRIER.TOS`. `emu/hd/` and `emu/out/` are git-ignored.
- `uv run python tools/hatari.py play`: GUI with sound.
- `uv run python tools/hatari.py snapshot --vbls N --out emu/out/<name>`: runs headless (about 2700 frames/s). N frames after `game_main` it writes `screenshot.png`, `ram.bin` (`$0–$7FFFF`) and `regs.txt`. 1500 frames reaches the title menu.
- Debugger scripting gotchas:
  - Use the breakpoint options `:once :trace :file <cmds>`. Without `:trace`, Hatari drops into the interactive debugger after the file runs, even if the file says `c`.
  - Always run with stdin from `/dev/null`.
  - `VBL` is Hatari's frame counter, and expressions in quotes are evaluated at definition time (`b VBL="VBL+100"`).
- Verified live: at `game_main` the RAM from `$400` is byte-identical to `extracted/carrier_payload.bin`.

## Ghidra project

- `uv run python tools/split_tos.py` writes `extracted/loader.bin` (GEMDOS text before $400) and `extracted/carrier_payload.bin` (loaded at $400).
- Project: `ghidra/W3CCommand.gpr`, program `carrier_payload.bin`, language `68000:BE:32:default` (Ghidra's 68k default is a 68040 superset; there is no plain 68000 ID, so beware 020+ opcodes decoded from data).
- `ghidra/scripts/AtariSTMemoryMap.java` (headless pre-script) builds the memory map:
  - `vectors` $0–$3FF, with exception, autovector, trap and MFP vectors labeled.
  - `payload` from $400.
  - `ram_hi` up to $7FFFF (512 KB).
  - `loader` at $F00000.
  - `io` $FF8000–$FFFFFF, plus the `io_absw` byte-mapped alias at $FFFF8000 for sign-extended `.w` addressing. Shifter, PSG, MFP, ACIA and DMA registers are labeled.
- `ghidra/scripts/AtariSTVectors.java` (post-script) finds vector installs (`move.l #h,vec` or `lea h(pc),An; move.l An,vec`) and names the handler functions.
- `ghidra/scripts/AtariSTFindCode.java` (run after the above) recovers code that auto-analysis misses. That's mainly routines reached only through pointer tables. It validates candidates with `PseudoDisassembler` and loops until nothing new turns up. It then creates functions for "orphan" runs of disassembled code that sit outside any function, such as jump-table targets. It assumes code ends at `$22E62`.
- `docs/memory-map.md` is the running map of code, data and runtime RAM. Keep it updated as regions are identified.
- Rebuild from scratch:
  ```
  /opt/homebrew/Cellar/ghidra/12.1.4/libexec/support/analyzeHeadless ghidra W3CCommand \
    -import extracted/carrier_payload.bin -overwrite -loader BinaryLoader -loader-baseAddr 0x400 \
    -processor 68000:BE:32:default -cspec default -scriptPath ghidra/scripts \
    -preScript AtariSTMemoryMap.java -postScript AtariSTVectors.java -postScript AtariSTFindCode.java
  ```
  `-overwrite` discards manual renames and comments made in the GUI/MCP; don't rebuild once real analysis work exists.
- Re-run a script on the existing program: `analyzeHeadless ghidra W3CCommand -process carrier_payload.bin -noanalysis -scriptPath ghidra/scripts -postScript <Script>.java`. Ghidra must not have the project open (it's locked).
- The GhidraMCP tools need the GUI (`ghidraRun`) with this program open in CodeBrowser and the GhidraMCP plugin enabled (backend at 127.0.0.1:8084).

## Planned phases (from `PLAN.md`)

1. **Decompilation and extraction** — decompile `CARRIER.TOS` with Ghidra (a `ghidra` MCP server is available for inspecting the open program: `get_program_info` first, then list/decompile/rename tools). Extract world state, simulation logic, enemy AI, procedural world generation, UI, plus images, 3D models and audio. Output should be recreatable in JS/TS or via WebAssembly.
2. **Verification** — run the recreated world simulation side by side with the original in an emulator and keep world state in sync. Must be able to export state from the emulator into the recreation and vice versa.
3. **Web build** — rebuild as a web app, likely with three.js or similar.
4. **Modernize** — updated UI, higher 3D fidelity, better audio, touch controls.

Goals to keep in mind: flat, colorful polygonal look with a refreshed UI, and instant save/restore of game state to local or cloud storage (which implies the simulation state should be cleanly serializable).
