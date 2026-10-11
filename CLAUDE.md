# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

W3CCommand is a planned port of the 1988 Atari ST game Carrier Command to a modern browser app (desktop and mobile). The repository is in the decompilation phase: there is a Ghidra project and its setup tooling (below), but no game code, build system, test suite or git history yet. Update this file when that tooling is added.

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
- Extraction scripts write to `extracted/` (git-ignored and regenerable). Run `tools/split_tos.py` first, then for example `uv run python tools/extract_ui_sprites.py` → `extracted/ui/`.

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
- `ghidra/scripts/AtariSTFindCode.java` (run after the above) recovers code that auto-analysis misses. That's mainly routines reached only through pointer tables. It validates candidates with `PseudoDisassembler` and loops until nothing new turns up. It assumes code ends at `$22E62`.
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
