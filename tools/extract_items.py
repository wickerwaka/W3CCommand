#!/usr/bin/env python3
"""Extract the store/equipment items: 64x64 pictures and 32x32 icons.

Item records: 15 x 26 bytes at $B76 (initial values in the payload):
    +$0 x.w  +$2 y.w  +$4 icon.l (32-px masked sprite, 32 rows)  +$8 ?.w
    +$A name.l (control-coded string)  +$E picture.w  ... +$18 ?.w
The equipment info screen ($D2E0) draws picture n from $3B426 + n*$600 with $B832:
64x64, opaque, 3 stored planes (see stgfx.picture3). Writes, under extracted/items/:
  picture_NN_<name>.png, icon_NN_<name>.png, items.txt
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import ROOT, Payload, masked_sprite, picture3, st_palette, to_image

ITEMS = 0xB76
ITEM_SIZE = 0x1A
ITEM_COUNT = 15
PICTURES = 0x3B426
PICTURE_SIZE = 0x600
DEFAULT_PALETTE = 0x132A
OUT = ROOT / "extracted" / "items"


def item_name(p: Payload, a: int) -> str:
    raw = bytearray()
    while p.data[a - 0x400]:
        raw.append(p.data[a - 0x400])
        a += 1
    # \x03 <column> <colour letter> introduces each text segment.
    parts = [seg[2:] for seg in raw.split(b"\x03") if len(seg) > 2]
    return " - ".join(s.decode("latin1") for s in parts)


def main() -> None:
    p = Payload()
    palette = st_palette(p, DEFAULT_PALETTE)
    OUT.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(ITEM_COUNT):
        rec = ITEMS + i * ITEM_SIZE
        icon, name_ptr, pic = p.u32(rec + 4), p.u32(rec + 0xA), p.u16(rec + 0xE)
        name = item_name(p, name_ptr)
        slug = re.sub(r"[^a-z0-9]+", "_", name.split(" - ")[0].lower()).strip("_")
        img = to_image(picture3(p, PICTURES + pic * PICTURE_SIZE, 64, 64), palette)
        img.resize((128, 128), 0).save(OUT / f"picture_{pic:02d}_{slug}.png")
        to_image(masked_sprite(p, icon, 32, 32), palette).save(OUT / f"icon_{i:02d}_{slug}.png")
        lines.append(f"{i:2d} picture={pic:2d} icon=${icon:05X} pos=({p.u16(rec)},{p.u16(rec + 2)}) {name}")
    (OUT / "items.txt").write_text("\n".join(lines) + "\n")
    print(f"{ITEM_COUNT} items -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
