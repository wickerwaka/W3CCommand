#!/usr/bin/env python3
"""Cross-check the Python model decoder against the game's own code under emulation.

For every model whose vertices stmodel builds natively, run the record's basis routine and
the vertex-op interpreter ($5A02) in Unicorn and compare the resulting vertex arrays. Also
checks that every record decodes to exactly its record boundary (allowing 1 pad byte) and
that all primitive indices refer to defined vertices.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stemu import Emu
from stgfx import Payload
from stmodel import (MODEL_DRAW_LINES_NEAR, MODEL_DRAW_POLY_NEAR, _build_vertices, _Reader, decode,
                     model_addresses, record_ends)


def main() -> int:
    p = Payload()
    addrs, ends = model_addresses(p), record_ends(p)
    failures = native = 0
    for i, addr in enumerate(addrs):
        m = decode(p, i)
        if m is None:
            print(f"model {i}: unsupported")
            failures += 1
            continue
        if ends[addr] - m.end not in (0, 1):
            print(f"model {i}: decoded to ${m.end:05X}, record ends ${ends[addr]:05X}")
            failures += 1
        used = [x for idx, _ in m.polys for x in idx] + [x for a, b, _ in m.lines for x in (a, b)]
        if any(x >= len(m.vertices) or m.vertices[x] is None for x in used):
            print(f"model {i}: primitive references an undefined vertex")
            failures += 1
        if m.basis is None:
            continue
        body = addr + (8 if m.handler in (MODEL_DRAW_POLY_NEAR, MODEL_DRAW_LINES_NEAR) else 4)
        if _build_vertices(p, _Reader(p, body + 4), m.basis) is None:
            continue                             # emulated anyway; nothing to compare
        native += 1
        e = Emu(p)
        e.build_vertices(m.basis, body + 4)
        diff = [k for k, v in enumerate(m.vertices) if v != e.vertex(k)]
        if diff:
            print(f"model {i}: {len(diff)} vertices differ from emulation, first V[{diff[0]}]")
            failures += 1
    print(f"{len(addrs)} models, {native} native decodes cross-checked, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
