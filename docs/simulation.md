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

## Supply network

- **Network:** `island_network` (`$2239E`, 32 bytes per island) holds a count, then (weight, neighbour) entries. Bit 15 of the neighbour word disables the link.
- **Link filtering** (`$2184E`): a link is enabled only if both islands have the same non-zero owner, so each side has its own network.
- **Route matrix** `[$67B40]` (64 × 64 words): `M[i][j]` is the shortest same-side route length from i to j, −1 if unreachable, 0 on the diagonal.
  - `$216C6` resets it and sets `[$1235]` = recompute pending. This happens whenever ownership changes.
  - `$2173A`, called from `game_main` while `[$1235]` is set, relaxes 8 rows per frame (`M[i][k] = min(M[i][k], w + M[n][k])` over i's enabled links) and repeats passes until a pass changes nothing.
  - Row 0 is VULCAN (the player base), so `M[0][i] >= 0` means island i is connected to the base. The island screen shows this as "DISTANCE TO BASE" / "ISOLATED".
- **Next hop** (`$21A30`): the enabled neighbour n that minimises `w + M[dest][n]`, among neighbours closer to the destination. Shipments wait the link weight in seconds at each hop.

## Logistics (`$F7A0`, called once per second from `game_main`)

Schedule:
- **every second:** `$1B8AE`, `$CDF2`, then the shipment movers `$21976`, `$21C28` and `$21E8C`;
- **every 10 seconds:** `$174E4`, `$1761C` and `$176AC`;
- **every 4 seconds:** `$21906`, `$22020`, `$22180`, `$21EE8` and `$21AA0`, in that order.

The economy is built around **raw material** (economy `+$02`, capacity `+$04`) and **goods** (`+$08`, capacity `+$0A`).

| Routine | Every | What it does |
|---|---|---|
| `$21906` produce | 4 s | All owned islands add raw material, capped. Base (type 0): +100. Resource (1): +100 × count of `$2A/$2B/$2E` buildings (mines). Factory (2): +25 × count of `$2C/$2D`. Defence (3): +0. Neutral islands with a command centre under construction: +capacity / 16 |
| `$21AA0` distribute raw | 4 s | Player side only. Raw stock is counted, including shipments in transit (`$F48`). Needy islands are bases and factories connected to VULCAN with stock ≤ capacity / 4, weighted by their production-line count. Each resource island with stock ≥ 200 ships 200 to a random needy island |
| `$21976` move raw | 1 s | Advances raw shipments in pool `$66BC0` (128 × 6 bytes: `at.b, timer.b, amount.w, dest.w`). On arrival it adds to the destination's raw stock, capped. A shipment is dropped if the island it's at becomes neutral |
| `$22180` plan production | 4 s | Player side only. Finds products below target: store items (`$B76` records `+$12` target, `+$10` priority class), the three fuels (`$D60–$D68` vs `$1FFFC0`, classes at `$D08–$D0C`), and owned connected islands whose goods are below capacity / 2 (defence) or capacity / 4. Picks the highest class with a need, shuffles the candidates, and assigns them to idle production lines on bases and factories |
| `$21EE8` run production | 4 s | Production lines at economy `+$0E`, count in `+$0C`, 12 bytes each: `state.w, product.w, steps.w, dest.w, qty.w, cost.w`. State 0 loads steps, qty and cost from product table `$21FAE`. State 1 pays `cost` raw material per step and counts steps down. State 2 ships the product into the goods pool, then the line goes idle (`state = −1`) |
| `$21C28` move goods | 1 s | Goods pool `$66710` (200 × 6 bytes: `at.b, timer.b, kind.b, qty.b, dest.w`). With `dest < 0` the shipment goes to the stockpile island `[$D4A]` and then the carrier: kind < 15 adds to carrier stores `$D78[kind]` (capped at 999); kinds 15–17 add qty × `$1000` to fuel `$D60[kind − 15]` (capped at `$1FFFC0`). Otherwise kind 18 adds qty to the destination island's goods, capped |
| `$22020` totals | 4 s | Per-product totals of stores, in-transit and in-production quantities (`$DFA`, `$F3C–$F44`, per-island `$E18`), used by planning |
| `$21E8C` | 1 s | Clears `[$D50]` ("Stocks still in transit") once pool `$66A10` (72 × 6) is empty |

**Product table** (`$21FAE`, 6 bytes each: `steps, qty, cost per step`):

| # | Product | Steps | Qty | Cost/step | | # | Product | Steps | Qty | Cost/step |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Assassin | 15 | 4 | 100 | | 10 | Decoy flares | 10 | 5 | 25 |
| 1 | Harbinger | 25 | 3 | 100 | | 11 | Recon drone | 15 | 2 | 50 |
| 2 | Hammerhead | 40 | 1 | 180 | | 12 | Fuel pod | 10 | 1 | 100 |
| 3 | Quaker | 40 | 1 | 150 | | 13 | Manta | 30 | 1 | 100 |
| 4 | Quasar | 15 | 1 | 100 | | 14 | Walrus | 30 | 1 | 100 |
| 5 | Avatar | 15 | 1 | 100 | | 15–17 | fuel (carrier, aircraft, AAV) | 10 | 8 | 100 |
| 6 | Defence drones | 10 | 2 | 25 | | 18 | island supply | 1 | 255 | 100 |
| 7 | Comms pod | 15 | 2 | 50 | | | | | | |
| 8 | Virus bomb | 20 | 2 | 100 | | | | | | |
| 9 | ACCB | 40 | 1 | 150 | | | | | | |

The item numbering matches the store-item records at `$B76` (see `extract_items.py`). Target stock levels and priority classes are 0 in the file, so they're set at game start or from the priority screens.

## Still to decode

- How the enemy side gets resources: production and the island census run for both owners, but distribution, planning and production lines are player-only.
- Enemy strategy (carrier AI, island attacks). `[$69AC0]` and the threat routine at `$22C20`.
- The unit/object list at `$5DCDA` (`$140` bytes per object, count `[$A48]`): the carrier, Mantas, Walruses and enemy units.
- The per-frame vehicle simulation called from `game_main` (`$17094`, `$1962A`, `$1A56A`, `$1AFAA`, `$1A296`, `$1B746`, `$20730`).
