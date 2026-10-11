// Shapes of web/public/data/world.json, written by tools/export_web.py (see its docstring).
// All coordinates are in the game frame: x right, y up, z forward (left-handed).

export type RGB = [number, number, number];

export interface ModelData {
  handler: number;
  vertices: [number, number, number][];
  polys: [number[], number][];                 // vertex indices, colour byte
  lines: [number, number, number][];           // v0, v1, colour byte
  discs: [number, number | null, number][];    // vertex, radius, colour byte
}

export interface ObjectType {
  near_dist: number;
  mid_dist: number;
  models: [number, number, number];            // near, mid, far
}

export interface Slot {
  slot: number;
  type: number;
  heading: number;                             // 16-bit angle
  a: number;
  b: number;
  x: number;                                   // island-local
  z: number;
}

export interface Island {
  index: number;
  name: string;
  x: number;
  z: number;
  half_x: number;
  half_z: number;
  owner: number;                               // 0 neutral, 1 player, 2 enemy
  links: { island: number; weight: number }[];
  volcanoes: { x: number; z: number }[];
  slots: Slot[];
}

export interface World {
  seed: string;
  palette: RGB[];
  colours: [number, number][];                 // colour byte -> two palette indices
  object_types: ObjectType[];
  models: Record<string, ModelData>;
  islands: Island[];
}

export async function loadWorld(url = "data/world.json"): Promise<World> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status} ${res.statusText}. Run "npm run data" first.`);
  return res.json();
}

export const OWNER_NAMES = ["Neutral", "Player", "Enemy"];

// Names for object types placed on islands. Only what the decompilation supports so far
// (docs/memory-map.md); the rest are shown by number.
export function typeName(t: number): string {
  if (t === 7) return "Volcano";
  if (t >= 24 && t <= 27) return "Tree";
  if (t === 36) return "Runway";
  if (t === 41) return "Control centre?";             // placed at every start-list spot
  if (t === 47) return "Runway (building)";
  if (t >= 48 && t <= 57) return `Structure ${t} (building)`;
  return `Structure ${t}`;
}
