// Reading and writing game states: 512 KB images of the ST's RAM ($0-$7FFFF), as the emulator
// exports them. The game's code and data are part of the image (the payload loads at $400), so
// one file carries both the state and the assets the renderer decodes.
//
// Accepted:
//   - raw RAM dumps of exactly 512 KB: Hatari's debugger `savebin <file> 0 $80000`
//     (tools/hatari.py snapshot, tools/capture_*.py) and this app's own exports;
//   - Hatari memory snapshots (.sav, usually gzip-compressed): the RAM is found inside by the
//     payload's signature at $404.
import { RAM_SIZE } from "../sim/index.ts";

const SIGNATURE = "Atari Bundle Version";
const SIGNATURE_AT = 0x404;

export class StateFileError extends Error {}

/** The RAM image in a state file. */
export async function readStateFile(data: Uint8Array): Promise<Uint8Array> {
  if (data[0] === 0x1f && data[1] === 0x8b) data = await gunzip(data);
  if (data.length === RAM_SIZE) {
    checkPayload(data);
    return data;
  }
  const at = find(data, new TextEncoder().encode(SIGNATURE));
  if (at < SIGNATURE_AT || at - SIGNATURE_AT + RAM_SIZE > data.length)
    throw new StateFileError(
      data.length > RAM_SIZE
        ? "No Carrier Command RAM found in this file (is it a snapshot of the game running?)"
        : `Not a state file: ${data.length} bytes (a RAM image is ${RAM_SIZE})`,
    );
  return data.slice(at - SIGNATURE_AT, at - SIGNATURE_AT + RAM_SIZE);
}

/** A RAM image as a file the emulator can load back (`loadbin <file> 0` in Hatari's debugger). */
export function writeStateFile(ram: Uint8Array): Blob {
  return new Blob([ram.slice()], { type: "application/octet-stream" });
}

function checkPayload(ram: Uint8Array): void {
  const sig = new TextDecoder().decode(ram.subarray(SIGNATURE_AT, SIGNATURE_AT + SIGNATURE.length));
  if (sig !== SIGNATURE) throw new StateFileError("This RAM image doesn't contain Carrier Command (no payload at $400)");
}

function find(hay: Uint8Array, needle: Uint8Array): number {
  outer: for (let i = 0; i + needle.length <= hay.length; i++) {
    for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

async function gunzip(data: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([data.slice()]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
