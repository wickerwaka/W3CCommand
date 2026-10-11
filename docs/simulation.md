# World state and simulation

This is a reverse-engineering reference for the strategy simulation in `CARRIER.TOS`. Addresses are runtime addresses; `[x]` means "the value at x". Entries marked *(guess)* are unconfirmed. Ghidra names are given in `code` where assigned.

## Timing

- `vbl_handler` (50 Hz) runs the game clock at `$8F2–$8FA` (days, hours, minutes, seconds, frames). Every 50 frames (1 s) it sets `[$8FC]` and `[$8FD]`.
- `game_main` calls `world_sim_step` (`$1848E`) once when `[$8FD]` is set. Each call simulates **one island**, so the world advances one island per second and completes a full pass in 64 s.
- At new game, `world_warmup` (`$18474`) runs `world_sim_step` 601 times with `[$8DC] = $FF` (fast-build mode, see below).

## Island state

| Address | Size | Contents |
|---|---|---|
| `$069B80` | 64 × `$192` | **Island table** (see below) |
| `$069B40` | 64 × 1 | **Owner**: 0 neutral, 1 player, 2 enemy |
| `$069AC0` | 64 × 2 | Per-island word, tested `>= 0` with owner 2 in `world_sim_step` *(enemy target / threat?)* |
| `$066EC0` | 64 × `$32` | **Island economy state** (see below) |
| `$066690` | 32 × 4 | Slot visiting order: (slot index, slot offset) pairs; built by `$18924`, shuffled with the RNG every 64 steps |
| `$062CDA`, `$063CEC` | `$12` + 32 × `$40` each | 3D object buffers for the island loaded near the camera. Island byte `+1` chooses the buffer. Slot changes are mirrored here by `$18276` |

**Island table entry** (`$192` bytes; static values come from `island_records`, see `docs/memory-map.md`):

| Offset | Contents |
|---|---|
| `+$00.b` | 3D-loaded flag *(guess)* |
| `+$01.b` | selects the 3D object buffer |
| `+$02.l`, `+$06.l` | world x, z |
| `+$0A.w`, `+$0C.w` | half-extents x, z |
| `+$0E.w` | area = hx·hz >> 16. Islands with area ≥ 2000 are "large" |
| `+$12` | **32 structure slots**, 12 bytes each |

**Structure slot** (12 bytes):

| Offset | Contents |
|---|---|
| `+0.w` | object type, 0 = empty. Same numbering as `object_lod_table` / models |
| `+2.w` | heading |
| `+4.w` | value: build progress, or health for finished structures |
| `+6.b` | active flag, set each step by the census |
| `+6.w` | on type `$34`, the start code before completion |
| `+8.w`, `+A.w` | local x, z |

**Island economy state** (`$32` bytes at `$66EC0` + 50·i):

| Offset | Contents |
|---|---|
| `+$00.b` | island type: 0 none/base *(guess)*, 1 resource, 2 factory, 3 defence (from the start code's low 2 bits) |
| `+$02.w` | resource A. The command centre consumes 100 per step (`$186CA`) |
| `+$04.w` | resource A capacity = area × 2 *(guess: capacity)* |
| `+$06.b` | count of types `$2A`/`$2B`/`$2E` (census) |
| `+$07.b` | has a command centre (type `$29` or `$34` present) |
| `+$08.w` | resource B. Construction consumes 30 per step (`$186B2`); `$186E2` adds to it, capped at `+$0A` |
| `+$0A.w` | resource B capacity = area × 2 |
| `+$0C.w` | count of types `$2C`/`$2D` (census) |
| `+$0E`, `+$1A`, `+$26` | bytes set to `$FF` at init *(three sub-records?)* |

## Structure types

| Types | Meaning | Simulation handler (`$18C28`, 8 bytes per type: handler.l, arg.l) |
|---|---|---|
| `$18–$1B` | trees | `$18874`: once there are ≥ 6 active buildings, an inactive tree has a 25% chance per step of being removed |
| `$24–$2E` | finished buildings (models 36–46) | `$1876E`: inactive → reverts to its under-construction type (+`$B`) with progress = arg.lo. Active → health +100 per step up to arg.hi |
| `$29` | command centre | `$18784`: health +100 per step up to arg.hi |
| `$2F–$39` | buildings under construction (type − `$B` = finished type) | `$186F6`, below |
| `$34` | command centre under construction (the start-list structure) | `$187A0`, below |

handler arg = (max health.w, build target.w); for example `$24`: 4000, 48.

**Under construction** (`$186F6`):
- Active: progress +1 per step if 30 units of resource B can be paid. When progress reaches the build target, or immediately in warm-up mode, the slot becomes the finished type at full health.
- Inactive: progress −1 per step (on neutral islands only with a 1-in-4 chance). At 0 the slot is cleared, except the runway (`$2F`).

**Command centre construction** (`$187A0`):
- Owned island: +4 per step if 100 units of resource A can be paid, capped at the target. On completion (`$187D6`):
  - the slot becomes `$29` at full health;
  - the island type = code & 3;
  - resource B = 5000;
  - owner = (code >> 4) + 1;
  - the game prints "X is now a friendly/an enemy island", then recomputes the class (`$1851A`) and the network (`$216C6`).
- Neutral island: −8 per step, and the slot is cleared at 0.

## One simulation step (`world_sim_step`)

1. Advance `[$8D0]` (island index, 0–63). On wrap, shuffle the slot order table.
2. `select_island` (`$18940`): `[$8C4]` = island entry, `[$8C8]` = economy record, `[$8CC]` = 3D object buffer.
3. In warm-up mode: economy `+2` = 500, `+8` = `+$A` / 4. An enemy island with `[$69AC0+2i] >= 0` gets `+8` = 500.
4. `island_census` (`$1854E`):
   1. Copy the quota table `$18DF8` → `$586AA`: 47 rows × 10 bytes, one per type `0–$2E`, of `[priority, quota per class × 9]`.
   2. Compute the island class (`$1851A`), which selects the quota column:
      - 0 if the island is neutral;
      - otherwise the economy `+0` type, +3 if the island is large;
      - 7 if the type is 0.
   3. For every slot, take one unit of its type's quota. Under-construction types use the finished type's row; a negative quota uses the fallback row `$18EE8`. A slot with quota left is active; otherwise it's inactive.
   4. Count the following:
      - active constructions (`[$8D4]`);
      - stalled constructions (`[$8D6]`);
      - active buildings of types `$24`+ (`[$8D8]`);
      - empty slots (`[$8DA]`).
5. `island_structures_step` (`$18662`): run each slot's type handler, in the shuffled slot order.
6. `island_plan_construction` (`$18898`): if fewer than 2 constructions are active, pick the buildable type (`$24–$2E`, except the runway) with the highest priority and remaining quota. Place it as its under-construction type at `find_free_island_spot`, and create its 3D object if the island is loaded (`$182A4`).

Islands therefore grow towards a fixed per-class template of buildings. Supply limits construction (resource B) and command-centre capture (resource A).

## Still to decode

- Where resources A and B come from, and the supply network (`$216C6`, `$2239E` routes, `$21xxx–$22xxx` code that uses `$66EC0` heavily).
- Enemy strategy (carrier AI, island attacks). `[$69AC0]` and the threat routine at `$22C20`.
- The unit/object list at `$5DCDA` (`$140` bytes per object, count `[$A48]`): the carrier, Mantas, Walruses and enemy units.
- The per-frame vehicle simulation called from `game_main` (`$17094`, `$1962A`, `$1A56A`, `$1AFAA`, `$1A296`, `$1B746`, `$20730`).
