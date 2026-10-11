#!/usr/bin/env python3
"""Export the island world and the 3D models as one JSON file for the browser viewer (web/).

Writes web/public/data/world.json:
  palette        default 16-colour palette ($132A) as [r, g, b]
  colours        $13CA colour table: per colour byte, the two palette indices (equal = solid,
                 different = two-colour dither)
  object_types   object_lod_table ($8B58), 16 bytes per type 0..83: near_dist.l mid_dist.l
                 near_model.w mid_model.w far_model.w (the renderer picks by distance)
  models         every non-empty model: vertices (game frame: x right, y up, z forward),
                 polys [indices, colour], lines [v0, v1, colour], discs [v, radius, colour]
  islands        static island data (as extract_islands.py) plus, per island, the owner and
                 the 32 structure slots after world generation

World generation runs the game's own init_islands ($18370) under Unicorn: it fills the island
table at $69B80 from the records, places 21 trees per island and the start-list structures
with the RNG at $195FE, then runs the world simulation step 601 times (which builds the
start structures). In the real game the sound interrupt also advances the RNG, so every game
differs; here the RNG starts from its initial state in the file (or --seed), which gives one
valid, reproducible layout.

Structure slot (12 bytes at island +$12 + 12n): type.w heading.w a.w b.w x.w z.w, where type
is an object type (8 = the island itself, 7 volcano, 24..27 trees, 36 runway, 47..57 under
construction, ...), heading is a 16-bit angle, x/z are island-local. a/b are kept raw
(a looks like strength, b holds the start-list code before the structure is built).
Owners at $69B40: 0 neutral, 1 player, 2 enemy.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_islands import islands as static_islands
from extract_models import COLOUR_TABLE, DEFAULT_PALETTE
from stemu import RETURN, STACK, Emu
from stgfx import ROOT, Payload, st_palette
from stmodel import MODEL_COUNT, decode
from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC

OUT = ROOT / "web" / "public" / "data" / "world.json"
INIT_ISLANDS = 0x18370
RNG_STATE = 0x1236
ISLAND_TABLE, ISLAND_STRIDE, SLOTS = 0x69B80, 0x192, 32
OWNERS = 0x69B40
OBJECT_LOD_TABLE, OBJECT_TYPES = 0x8B58, 84
COLOURS = 64                     # colour bytes used by models are < $40


def generate(p: Payload, seed: int | None) -> tuple[list, list]:
    """Run init_islands; returns (owners, slots per island)."""
    e = Emu(p)
    if seed is not None:
        e.uc.mem_write(RNG_STATE, seed.to_bytes(8, "big"))
    e.uc.reg_write(UC_M68K_REG_A7, STACK - 4)
    e.w32(STACK - 4, RETURN)
    e.uc.emu_start(INIT_ISLANDS, RETURN, count=500_000_000)
    if e.uc.reg_read(UC_M68K_REG_PC) != RETURN:
        raise RuntimeError("init_islands did not return")
    owners = list(e.uc.mem_read(OWNERS, 64))
    slots = []
    for i in range(64):
        raw = bytes(e.uc.mem_read(ISLAND_TABLE + i * ISLAND_STRIDE + 0x12, SLOTS * 12))
        isl = []
        for s in range(SLOTS):
            t, heading, a, b, x, z = struct.unpack(">hhhhhh", raw[12 * s:12 * s + 12])
            if t:
                isl.append({"slot": s, "type": t, "heading": heading & 0xFFFF, "a": a, "b": b, "x": x, "z": z})
        slots.append(isl)
    return owners, slots


def models(p: Payload) -> dict:
    out = {}
    for i in range(MODEL_COUNT):
        m = decode(p, i)
        if m is None or not m.vertices:
            continue
        out[i] = {
            "handler": m.handler,
            "vertices": [list(v) if v is not None else [0, 0, 0] for v in m.vertices],
            "polys": [[list(idx), c & 0x7F] for idx, c in m.polys],
            "lines": [[a, b, c & 0x7F] for a, b, c in m.lines],
            "discs": [[v, r, c & 0x7F] for v, r, c in m.discs],
        }
    return out


def object_types(p: Payload) -> list:
    out = []
    for t in range(OBJECT_TYPES):
        a = OBJECT_LOD_TABLE + 16 * t
        out.append({"near_dist": p.u32(a), "mid_dist": p.u32(a + 4),
                    "models": [p.u16(a + 8), p.u16(a + 10), p.u16(a + 12)]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=lambda s: int(s, 16), help="RNG state, 16 hex digits")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    p = Payload()
    owners, slots = generate(p, args.seed)
    isl = static_islands(p)
    for i in isl:
        i["owner"] = owners[i["index"]]
        i["slots"] = slots[i["index"]]
    data = {
        "source": "CARRIER.TOS (tools/export_web.py)",
        "seed": f"{args.seed:016X}" if args.seed is not None else f"{p.u32(RNG_STATE):08X}{p.u32(RNG_STATE + 4):08X}",
        "palette": [list(c) for c in st_palette(p, DEFAULT_PALETTE)],
        "colours": [[p.data[COLOUR_TABLE - 0x400 + 2 * c], p.data[COLOUR_TABLE - 0x400 + 2 * c + 1]]
                    for c in range(COLOURS)],
        "object_types": object_types(p),
        "models": models(p),
        "islands": isl,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, separators=(",", ":")))
    n = sum(len(s) for s in slots)
    print(f"{len(isl)} islands, {n} structures, {len(data['models'])} models -> {args.out.relative_to(ROOT)}"
          f" ({args.out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
