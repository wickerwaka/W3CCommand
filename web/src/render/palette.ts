// Colours, read from the game's own tables in the RAM image.
//
// The 3D views use the default palette at $132A (16 ST words, 0RGB with 3 bits per channel).
// A model's colour byte indexes the table at $13CA: a pair of palette indices per colour. Equal
// indices draw solid; different ones draw a 2x2 checker dither of the two, which we show as
// their average (the ST's dither reads as a blend on screen).
import * as THREE from "three";

export const PALETTE_3D = 0x132a;
export const COLOUR_TABLE = 0x13ca;

/** A palette entry as linear-space RGB for three.js. */
export function paletteColour(ram: Uint8Array, index: number, palette = PALETTE_3D): THREE.Color {
  const w = (ram[palette + 2 * index] << 8) | ram[palette + 2 * index + 1];
  const ch = (shift: number) => ((w >> shift) & 7) / 7;
  return new THREE.Color().setRGB(ch(8), ch(4), ch(0), THREE.SRGBColorSpace);
}

/** The palette indices a model colour byte draws with. */
export function colourPair(ram: Uint8Array, colour: number): [number, number] {
  const c = colour & 0x7f;
  return [ram[COLOUR_TABLE + 2 * c] & 15, ram[COLOUR_TABLE + 2 * c + 1] & 15];
}

/** A model colour byte as a single colour (dithered pairs averaged). */
export function modelColour(ram: Uint8Array, colour: number): THREE.Color {
  const [a, b] = colourPair(ram, colour);
  const ca = paletteColour(ram, a);
  return a === b ? ca : ca.lerp(paletteColour(ram, b), 0.5);
}
