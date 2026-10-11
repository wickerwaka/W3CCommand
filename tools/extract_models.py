#!/usr/bin/env python3
"""Export the 3D models that tools/stmodel.py can decode to Wavefront OBJ, plus previews.

Writes, under extracted/models/:
  model_NN.obj / model_NN.mtl   geometry (game frame converted to right-handed: z negated)
  model_NN.png                  flat-shaded preview from an elevated three-quarter view
  index.txt                     summary per model, including skipped (unsupported) ones

Colours: a polygon colour byte indexes the table at $13CA (two palette indices per
colour; equal = solid, different = two-colour dither). Materials use the default
palette at $132A (models 74..82, drawn on the damage screen, use $136A with colour 7 =
$445), with dithered colours exported as the average of the pair.
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import ROOT, Payload, st_palette
from stmodel import MODEL_COUNT, decode, model_addresses

COLOUR_TABLE = 0x13CA
DEFAULT_PALETTE = 0x132A
DAMAGE_PALETTE = 0x136A        # damage screen ($1404E); colour 7 patched to $445 at runtime
# Models drawn only on the damage screen (carrier schematic and system overlays).
MODEL_PALETTE = {i: DAMAGE_PALETTE for i in range(74, 83)}
OUT = ROOT / "extracted" / "models"


def colour_pair(p: Payload, c: int) -> tuple:
    return p.data[COLOUR_TABLE - 0x400 + 2 * c], p.data[COLOUR_TABLE - 0x400 + 2 * c + 1]


def material(p: Payload, palette: list, c: int) -> tuple:
    a, b = colour_pair(p, c)
    rgb = tuple((x + y) / 2 / 255 for x, y in zip(palette[a], palette[b]))
    name = f"col{c:02d}_{a}" if a == b else f"col{c:02d}_{a}_{b}"
    return name, rgb


def write_obj(p: Payload, palette: list, m, path: Path) -> None:
    mats = {}
    for _, c in m.polys:
        mats[c] = material(p, palette, c)
    for *_, c in m.lines:
        mats[c] = material(p, palette, c)
    for *_, c in m.discs:
        mats[c] = material(p, palette, c)
    mtl = [f"newmtl {name}\nKd {r:.4f} {g:.4f} {b:.4f}\n" for name, (r, g, b) in mats.values()]
    path.with_suffix(".mtl").write_text("".join(mtl))

    lines = [f"# CARRIER.TOS model {m.index} at ${m.addr:05X}, half-extents {m.extents}",
             f"mtllib {path.with_suffix('.mtl').name}", f"o model_{m.index:02d}"]
    for v in m.vertices:
        x, y, z = v if v is not None else (0, 0, 0)
        lines.append(f"v {x} {y} {-z}")
    for idx, c in m.polys:
        lines.append(f"usemtl {mats[c][0]}")
        lines.append("f " + " ".join(str(i + 1) for i in reversed(idx)))
    for v0, v1, c in m.lines:
        lines.append(f"usemtl {mats[c][0]}")
        lines.append(f"l {v0 + 1} {v1 + 1}")
    for v, radius, c in m.discs:
        # Screen-space disc (always faces the camera); OBJ has no circle, so emit a point.
        lines.append(f"# disc at vertex {v + 1}, radius {radius if radius is not None else 'object +$1E'}, colour {c}")
        lines.append(f"usemtl {mats[c][0]}")
        lines.append(f"p {v + 1}")
    path.write_text("\n".join(lines) + "\n")


def render_preview(p: Payload, palette: list, m, path: Path, size: int = 256) -> None:
    yaw, pitch = math.radians(35), math.radians(25)
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)

    def view(v):
        x, y, z = v
        x, z = x * cy - z * sy, x * sy + z * cy
        y, z = y * cp - z * sp, y * sp + z * cp
        return x, y, z

    pts = [view(v) if v is not None else (0, 0, 0) for v in m.vertices]
    span = max(1, max(max(abs(x), abs(y)) for x, y, _ in pts))
    s = size * 0.42 / span

    def screen(i):
        x, y, _ = pts[i]
        return size / 2 + x * s, size / 2 - y * s

    img = Image.new("RGB", (size, size), (24, 24, 40))
    draw = ImageDraw.Draw(img)
    # Painter's order by average depth (far first); the game uses its BSP tree instead.
    for idx, c in sorted(m.polys, key=lambda pc: -sum(pts[i][2] for i in pc[0]) / len(pc[0])):
        a, b = colour_pair(p, c)
        fill = tuple((x + y) // 2 for x, y in zip(palette[a], palette[b]))
        if len(idx) >= 3:
            draw.polygon([screen(i) for i in idx], fill=fill, outline=(0, 0, 0))
        else:
            draw.line([screen(i) for i in idx], fill=fill)
    for v0, v1, c in m.lines:
        draw.line([screen(v0), screen(v1)], fill=palette[colour_pair(p, c)[0]], width=1)
    for v, radius, c in m.discs:
        x, y = screen(v)
        rr = radius * s if radius else size / 6
        draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=palette[colour_pair(p, c)[0]])
    img.save(path)


def main() -> None:
    p = Payload()
    default = st_palette(p, DEFAULT_PALETTE)
    damage = st_palette(p, DAMAGE_PALETTE)
    damage[7] = tuple(((0x445 >> s) & 7) * 255 // 7 for s in (8, 4, 0))
    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    exported = 0
    for i, addr in enumerate(model_addresses(p)):
        m = decode(p, i)
        if m is None:
            handler = p.u32(addr)
            basis_at = {0x935E: 4, 0x9496: 4, 0x9550: 8}.get(handler)
            summary.append(f"{i:2d} ${addr:05X} skipped: handler ${handler:05X}"
                           + (f" basis ${p.u32(addr + basis_at):05X}" if basis_at else ""))
            continue
        if not m.vertices:
            summary.append(f"{i:2d} ${addr:05X} handler=${m.handler:05X} empty (draws nothing)")
            continue
        palette = damage if MODEL_PALETTE.get(i) == DAMAGE_PALETTE else default
        write_obj(p, palette, m, OUT / f"model_{i:02d}.obj")
        render_preview(p, palette, m, OUT / f"model_{i:02d}.png")
        exported += 1
        basis = f"${m.basis:05X}" if m.basis is not None else "-"
        summary.append(f"{i:2d} ${addr:05X} handler=${m.handler:05X} basis={basis} extents={m.extents} "
                       f"verts={len(m.vertices)} polys={len(m.polys)} lines={len(m.lines)} discs={len(m.discs)} "
                       f"attachments={m.attachments}" + (f" near_dist={m.near_dist}" if m.near_dist else ""))
    (OUT / "index.txt").write_text("\n".join(summary) + "\n")
    print(f"exported {exported} of {MODEL_COUNT} models -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
