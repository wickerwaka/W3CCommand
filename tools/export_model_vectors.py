#!/usr/bin/env python3
"""Export test vectors for the TypeScript model decoder (web/src/render/models.ts).

For every model and a set of contexts (object fields and globals; several per runtime-
dependent model, including random ones), runs the game's own vertex building under Unicorn
(the record's basis routine, then the vertex-op interpreter $5A02; models 75-82 after model
74's) and records the whole vertex array V and where the vertex-op stream ends. The primitives
(polygons, lines, discs, attachments, wireframe faces) come from the Python decoder
(tools/stmodel.py); a lines model's list is cut to the low byte of object +$38 and the disc
radius is object +$1E, as the handlers do.

Writes extracted/model_vectors.json; check with `node web/scripts/check-models.ts`.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stemu import OBJ, Emu
from stgfx import ROOT, Payload
from stmodel import (MODEL_COUNT, MODEL_DRAW_DISC, MODEL_DRAW_LINES, MODEL_DRAW_LINES_NEAR,
                     MODEL_DRAW_POLY, MODEL_DRAW_POLY_NEAR, MODEL_DRAW_WIRE, decode, model_addresses)

OUT = ROOT / "extracted" / "model_vectors.json"
VERTEX_HANDLERS = (MODEL_DRAW_POLY, MODEL_DRAW_POLY_NEAR, MODEL_DRAW_WIRE, MODEL_DRAW_LINES,
                   MODEL_DRAW_LINES_NEAR)
PARENT = {i: 74 for i in range(75, 83)}
FUZZ = 24                                       # random contexts per dynamic model

ANGLES = [0, 0x10, 0x1234, 0x4000, 0x7FF8, 0x8000, 0xA5A5, 0xFFF0, 0xFFFF]


def contexts(p: Payload, index: int, rng: random.Random) -> list:
    """(object word pokes, global word pokes) pairs for a model."""
    addr = model_addresses(p)[index]
    handler = p.u32(addr)
    w = lambda: rng.randrange(0x10000)
    if handler == MODEL_DRAW_DISC:
        return [({0x1E: r}, {}) for r in (0, 1, 40, 0x7FFF, 0xFFFF)] + [({0x1E: w()}, {}) for _ in range(4)]
    if handler not in VERTEX_HANDLERS:
        return [({}, {})]
    body = addr + (8 if handler in (MODEL_DRAW_POLY_NEAR, MODEL_DRAW_LINES_NEAR) else 4)
    basis = p.u32(body)
    out = []
    if basis == 0xA660:
        out += [({0x1E: x, 0x20: z}, {}) for x, z in
                [(0, 0), (100, 100), (80, 80), (1000, 3000), (2600, 400), (0x7FFF, 0x7FFF),
                 (0x8000, 0x8000), (0x8040, 0x10), (0xFFB0, 0x50)]]
        out += [({0x1E: w(), 0x20: w()}, {}) for _ in range(FUZZ)]
    elif basis in (0xAA78, 0xAABE, 0xAB42, 0xABE8):
        lifts = [(0, 0), (0xD000, 0xD000), (0x4000, 0xC000), (0x7FFF, 0x8000), (0x1234, 0xFEDC)]
        for k, a in enumerate(ANGLES):
            dd6, dd8 = lifts[k % len(lifts)]
            out.append(({}, {0x6CC: a, 0x6CA: ANGLES[(k * 4 + 3) % len(ANGLES)], 0xDD6: dd6, 0xDD8: dd8}))
        out += [({}, {0x6CC: w(), 0x6CA: w(), 0xDD6: w(), 0xDD8: w()}) for _ in range(FUZZ)]
    elif basis in (0xAC16, 0xADA4):
        blends = [0, 0x40, 0x80, 0xFF, 0x100, 0x101, 0x180, 0x1FF, 0x200, 0x2C0, 0x7FFF, 0x8000,
                  0xFF80, 0xFFFF]
        out += [({0x24: b, 0x26: ANGLES[k % len(ANGLES)]}, {}) for k, b in enumerate(blends)]
        out += [({0x24: w(), 0x26: w()}, {}) for _ in range(FUZZ)]
    else:
        out.append(({}, {}))
    if handler in (MODEL_DRAW_LINES, MODEL_DRAW_LINES_NEAR):
        # The line count is the low byte of +$38 (high byte ignored); test both ends.
        out = [({**o, 0x38: n}, g) for o, g in out for n in (0, 1, 7, 0x1203, 0xFF, w())]
    return out


def emulate(p: Payload, index: int, obj: dict, glob: dict):
    e = Emu(p)
    for off, v in obj.items():
        e.w16(OBJ + off, v)
    for a, v in glob.items():
        e.w16(a, v)
    end = None
    for i in ([PARENT[index]] if index in PARENT else []) + [index]:
        addr = model_addresses(p)[i]
        body = addr + (8 if p.u32(addr) in (MODEL_DRAW_POLY_NEAR, MODEL_DRAW_LINES_NEAR) else 4)
        end = e.build_vertices(p.u32(body), body + 4)
    return [e.vertex(i) for i in range(256)], end


def primitives(m) -> dict:
    return {
        "handler": m.handler,
        "basis": m.basis,
        "nearDist": m.near_dist,
        "planes": m.planes,
        "polys": [[idx, c] for idx, c in m.polys],
        "lines": [[a, b, c] for a, b, c in m.lines],
        "discs": [[v, r, c] for v, r, c in m.discs],
        "attachments": m.attachments,
        "faces": [[a, b, c, ls] for a, b, c, ls in m.faces],
        "conditionalFrom": None,    # $9496: index of the first conditional line
    }


def main() -> None:
    p = Payload()
    rng = random.Random(0x3D0DE1)
    models, vectors = [], []
    for i in range(MODEL_COUNT):
        m = decode(p, i)
        prim = primitives(m)
        if m.handler == MODEL_DRAW_WIRE:
            # the always-drawn line count is the byte after the nverts word
            prim["conditionalFrom"] = p.data[emulate(p, i, {}, {})[1] + 2 - 0x400]
        models.append(prim)
        for obj, glob in contexts(p, i, rng):
            V, end = (None, None)
            if m.handler in VERTEX_HANDLERS:
                V, end = emulate(p, i, obj, glob)
            vectors.append({"model": i, "obj": {f"{k}": v for k, v in obj.items()},
                            "globals": {f"{k}": v for k, v in glob.items()}, "V": V, "end": end})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"models": models, "vectors": vectors}, separators=(",", ":")))
    print(f"{len(vectors)} vectors for {MODEL_COUNT} models -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
