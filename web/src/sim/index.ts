// The simulation model: WorldState (strategic layer) extended by the unit layer into UnitState.
// See README.md; tools/stsim.py and tools/stunits*.py are the verified reference.
export { UnitState } from "./units_vehicles.ts";
export { UNIT_REGIONS, UNIT_INPUT_REGIONS } from "./units_core.ts";
export { STATE_REGIONS, INPUT_REGIONS, WorldState, TacticalMode, EnemyDestroyed } from "./world.ts";
export { Mem, NotPorted, RAM_SIZE } from "./mem.ts";
export type { Region, SimEvent } from "./mem.ts";
