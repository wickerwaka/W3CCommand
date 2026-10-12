# Simulation model (TypeScript)

A line-by-line port of the verified Python reference model:

| TypeScript | Python | Class |
|---|---|---|
| `world.ts` | `tools/stsim.py` | `WorldState` (strategic layer) |
| `units_core.ts` | `tools/stunits.py` | `UnitCore extends WorldState` |
| `units_render.ts` | `tools/stunits_render.py` | `RenderUnits extends UnitCore` (state the renders write) |
| `units_carrier.ts` | `tools/stunits_carrier.py` | `CarrierUnits extends UnitCore` |
| `units_enemy.ts` | `tools/stunits_enemy.py` | `EnemyUnits extends CarrierUnits` |
| `units_vehicles.ts` | `tools/stunits_vehicles.py` | `UnitState extends EnemyUnits` (the whole model) |

`index.ts` exports `UnitState` and the region list. Python's mixin order (vehicles, enemy,
carrier, core) becomes this single inheritance chain; the mixins override disjoint methods,
so the behaviour is the same.

## Conventions (so the files can be ported and read side by side)

- **Names are the Python names**: snake_case methods and fields, UPPER_CASE constants,
  the same class roles. `self.x` is `this.x`. Docstrings become `/** */` comments.
- **Arithmetic** uses `mem.ts`: `w16/s16/s8/u32/s32`, `pyDiv` for `//`, `pyMod` for `%`,
  `shr`/`shl` for `>>`/`<<` on values that can exceed 32 bits, `Math.trunc` for
  `int(a / b)`, `divu` for 68000 `divu.w`. JS bitwise operators are only safe on values that
  fit in 32 bits (`& 0xFFFF`, `>> 3` of a word, ...). `& 0xFFFFFFFF` must be `u32(...)`.
- **Errors**: `NotImplementedError` is `NotPorted`; `TacticalMode` and `EnemyDestroyed`
  are exported from `world.ts`.
- **Events**: `self.events.append(("message", "x"))` is `this.events.push(["message", "x"])`.
- **State** is a 512 KB `Uint8Array` behind `Mem`. `UnitState.from_ram(ram)` copies only the
  regions (each class adds its own in `load_regions`, Python's `from_ram` overrides), and
  `to_ram(ram)` writes them back. `STATE_REGIONS` (world.ts) and `UNIT_REGIONS` (units_core.ts;
  the carrier/enemy/vehicle modules push their extra regions at import, as in Python) define
  the state. Modules load in a different order than in Python, so the list order differs; the
  checker hashes in Python's order and requires the same set.
- **Dispatch tables** (`ISLAND_BEHAVIOURS`, `AI_BEHAVIOURS`, `HIT_HANDLERS`, static on
  `UnitCore`) map an original address to a method name, called as `(this as any)[name](...)`.
- Methods a class calls but only a later module defines are declared `abstract` there.
- Only erasable TypeScript syntax (no enums, namespaces or parameter properties): Node runs
  the checker by stripping types.

## Verification

`tools/export_sim_vectors.py` runs the Python model's own checks (`check_sim.py`,
`check_units.py`: Hatari captures, the original under Unicorn, long runs, scenarios, commands)
with a recorder attached, and writes every model state they create and every call made on it
to `extracted/sim_vectors/`. `npm run check-sim` (`scripts/check-sim.ts`) replays them: each
call must raise the same exception, return the same value, record the same events and leave
the same CRC-32 over the state regions. A failing state is dumped to `web/.sim-fail/` with
the command that diffs it against Python region by region.
