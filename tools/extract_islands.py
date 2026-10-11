#!/usr/bin/env python3
"""Extract the static island world: positions, sizes, fixed structures, names, the supply
network and the two starting-structure lists. Writes, under extracted/islands/:
  islands.json   all static island data (see below)
  map.png        archipelago map (network links, names; islands drawn at a minimum size,
                 since true island extents are ~0.25% of the map span)

Island records: 64 x 22 bytes at $4AFB6, expanded into the island table at $69B80
(64 x $192 bytes) by init_islands ($18370):
    x.w z.w             world position / $200 (table +2/+6 hold x*$200, z*$200 as longs)
    hx.w hz.w           half-extents in world units (table +10/+12). In 3D the island is
                        model 8 ($A660): a plateau with base (+-hx, +-hz) at height 0 and
                        top inset by 80 at height 80.
    rx.w rz.w rh.w      if rx or rz nonzero: structure type $2F (runway under construction)
                        at local (rx, rz), heading rh (slot 1)
    v1x.w v1z.w         if nonzero: volcano (object type 7) at local position
    v2x.w v2z.w         second volcano
Names: word offsets at $4AD20 (64) relative to $4AD20.
Supply network: 32 bytes per island at $2239E: count.w, then count x (weight.w, island.w).
Start lists ($18FE2 when [$124A] != 0, else $1901C): (island.w, code.w) pairs until a
negative word; each places a structure (slot type $34, value code) at a random spot.
Not static: 21 trees per island (types 24..27) and the start structures are placed at random
positions by the RNG at $195FE (state $1236, initial 31415926 53589793), which the sound
interrupt (Timer A, $20668) also advances, so layouts differ between games.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import ROOT, Payload

RECORDS, RECORD_SIZE = 0x4AFB6, 22
NAMES = 0x4AD20
NETWORK, NETWORK_STRIDE = 0x2239E, 32
START_LISTS = {"list_a_flag_124A_set": 0x18FE2, "list_b_flag_124A_clear": 0x1901C}
COUNT = 64
OUT = ROOT / "extracted" / "islands"


def cstr(p: Payload, a: int) -> str:
    s = bytearray()
    while p.data[a - 0x400]:
        s.append(p.data[a - 0x400])
        a += 1
    return s.decode("latin1")


def islands(p: Payload) -> list:
    out = []
    for i in range(COUNT):
        r = RECORDS + i * RECORD_SIZE
        w = [p.s16(r + 2 * k) for k in range(11)]
        isl = {"index": i, "name": cstr(p, NAMES + p.u16(NAMES + 2 * i)),
               "x": w[0] * 0x200, "z": w[1] * 0x200, "half_x": w[2] & 0xFFFF, "half_z": w[3] & 0xFFFF,
               "runway_construction": None, "volcanoes": [], "links": []}
        if w[4] or w[5]:
            isl["runway_construction"] = {"x": w[4], "z": w[5], "heading": w[6]}
        for vx, vz in ((w[7], w[8]), (w[9], w[10])):
            if vx or vz:
                isl["volcanoes"].append({"x": vx, "z": vz})
        n = NETWORK + i * NETWORK_STRIDE
        for k in range(p.u16(n)):
            weight, other = p.u16(n + 2 + 4 * k), p.u16(n + 4 + 4 * k)
            if weight:
                isl["links"].append({"island": other & 0x7FFF, "weight": weight})
        out.append(isl)
    return out


def start_lists(p: Payload) -> dict:
    lists = {}
    for name, a in START_LISTS.items():
        entries = []
        while p.s16(a) >= 0:
            entries.append({"island": p.u16(a), "code": p.u16(a + 2)})
            a += 4
        lists[name] = entries
    return lists


def render_map(isl: list, path: Path, size: int = 1024) -> None:
    xs = [i["x"] + s * i["half_x"] for i in isl for s in (-1, 1)]
    zs = [i["z"] + s * i["half_z"] for i in isl for s in (-1, 1)]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    scale = (size - 40) / max(x1 - x0, z1 - z0)

    def pt(x, z):
        return 20 + (x - x0) * scale, size - 20 - (z - z0) * scale

    img = Image.new("RGB", (size, size), (16, 32, 96))
    d = ImageDraw.Draw(img)
    for i in isl:
        for link in i["links"]:
            j = isl[link["island"]]
            d.line([pt(i["x"], i["z"]), pt(j["x"], j["z"])], fill=(90, 120, 180))
    for i in isl:
        cx, cz = pt(i["x"], i["z"])
        hx, hz = max(3, i["half_x"] * scale), max(3, i["half_z"] * scale)   # true size is ~2 px
        d.rectangle([cx - hx, cz - hz, cx + hx, cz + hz], fill=(60, 150, 60), outline=(200, 200, 120))
        for v in i["volcanoes"]:
            vx, vz = pt(i["x"] + v["x"], i["z"] + v["z"])
            d.ellipse([vx - 3, vz - 3, vx + 3, vz + 3], fill=(200, 60, 30))
        d.text((cx - 3 * len(i["name"]), cz + 6), i["name"], fill=(255, 255, 255))
    img.save(path)


def main() -> None:
    p = Payload()
    OUT.mkdir(parents=True, exist_ok=True)
    isl = islands(p)
    asym = [(i["index"], l["island"]) for i in isl for l in i["links"]
            if not any(m["island"] == i["index"] for m in isl[l["island"]]["links"])]
    data = {"islands": isl, "start_lists": start_lists(p),
            "notes": "Static data only; trees and start-structure positions are random per game."}
    (OUT / "islands.json").write_text(json.dumps(data, indent=1) + "\n")
    render_map(isl, OUT / "map.png")
    print(f"{len(isl)} islands, {sum(len(i['links']) for i in isl) // 2} links"
          f"{f', {len(asym)} asymmetric links' if asym else ''} -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
