#!/usr/bin/env python3
"""Extract the game's palettes. The VBL copies 16 colours from the pointer at $1326 into
the Shifter palette; the code sets that pointer to one of four consecutive palettes:

    $132A  default: most screens and the 3D views (set at ~20 sites, e.g. $17A6E hw_init)
    $134A  set by $FBD6, $13AF0, and $1404E when [$B62] != 0
    $136A  damage screen ($1404E, carrier schematic models 74..82), with colour 7 patched to
           $445 at runtime; also $FC48
    $138A  set by $10996, and $142E6 when [$D14] == 1 (same colours as $136A)

Colour 15 is cycled by the VBL through the 16 words at $13AA while [$1231] is set.
ST colours are 3 bits per channel (0RGB words); converted here as v * 255 / 7.
Writes extracted/palettes/palettes.json and palettes.png (swatches).
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import ROOT, Payload, st_palette

PALETTES = {"default": 0x132A, "alt_134a": 0x134A, "damage_136a": 0x136A, "alt_138a": 0x138A}
RUNTIME_PATCHES = {"damage_136a": {7: 0x445}}
CYCLE = 0x13AA
OUT = ROOT / "extracted" / "palettes"


def rgb(word: int) -> list:
    return [((word >> s) & 7) * 255 // 7 for s in (8, 4, 0)]


def main() -> None:
    p = Payload()
    OUT.mkdir(parents=True, exist_ok=True)
    data = {}
    for name, addr in PALETTES.items():
        words = [p.u16(addr + 2 * i) for i in range(16)]
        data[name] = {"address": f"${addr:05X}", "st_words": [f"{w:03x}" for w in words],
                      "rgb": [rgb(w) for w in words],
                      "runtime_patches": {str(k): f"{v:03x}" for k, v in RUNTIME_PATCHES.get(name, {}).items()}}
    cycle = [p.u16(CYCLE + 2 * i) for i in range(16)]
    data["colour15_cycle"] = {"address": f"${CYCLE:05X}", "st_words": [f"{w:03x}" for w in cycle],
                              "rgb": [rgb(w) for w in cycle]}
    (OUT / "palettes.json").write_text(json.dumps(data, indent=1) + "\n")

    rows = list(PALETTES) + ["colour15_cycle"]
    img = Image.new("RGB", (16 * 32 + 120, len(rows) * 36), (30, 30, 30))
    d = ImageDraw.Draw(img)
    for r, name in enumerate(rows):
        d.text((4, r * 36 + 12), name, fill=(255, 255, 255))
        for i, c in enumerate(data[name]["rgb"]):
            d.rectangle([120 + i * 32, r * 36 + 2, 120 + i * 32 + 30, r * 36 + 32], fill=tuple(c))
    img.save(OUT / "palettes.png")
    assert st_palette(p, PALETTES["default"])[1] == tuple(data["default"]["rgb"][1])
    print(f"{len(PALETTES)} palettes + cycle table -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
