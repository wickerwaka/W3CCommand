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
| `$069AC0` | 64 × 2 | Not separate state: row 63 of the route matrix, the route distance from NEMESIS (−1 = not connected) |
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
| `$18–$1B` | trees | `$18874`: once there are ≥ 6 active buildings, an inactive tree has a 25% chance per step of being removed. Types `$19–$1B` have quota −1 and fall back to the tree row (see census) |
| `$24–$2E` | finished buildings (models 36–46) | `$1876E`: inactive → reverts to its under-construction type (+`$B`) with progress = arg.lo. Active → health +100 per step up to arg.hi |
| `$29` | command centre | `$18784`: health +100 per step up to arg.hi. Its max health (also used by `$34`) is patched at game start, see below |
| `$2F–$39` | buildings under construction (type − `$B` = finished type) | `$186F6`, below |
| `$34` | command centre under construction (the start-list structure) | `$187A0`, below |

handler arg = (max health.w, build target.w); for example `$24`: 4000, 48.

**Difficulty** (`difficulty_apply`, `$190FE`): the new-game setup (`$42A8`) sets `[$444]` = 0 for a strategy game (`[$124A]` set) or `$2000` for an action game. `difficulty_apply` derives `$446–$454` from it, and patches the command-centre max health in the handler table (`$18D74`, `$18DCC`) to `$A00 + [$444] >> 4`: 2560 in a strategy game, where the file has 0. `difficulty_add` (`$190EE`) raises `[$444]` with saturation. It's called from four places that aren't decoded yet.

**Under construction** (`$186F6`):
- Active: progress +1 per step if 30 units of resource B can be paid. When progress reaches the build target, or immediately in warm-up mode, the slot becomes the finished type at full health.
- Inactive: progress −1 per step (on neutral islands only with a 1-in-4 chance), refunding 20 goods (capped). At 0 the slot is cleared, except the runway (`$2F`).

**Command centre construction** (`$187A0`). A builder (`$34`) only works on a **neutral** island:
- Neutral island (class 0): +4 per step if 100 raw material can be paid, capped at the target (61). Once the target is reached, the next step completes it (`$187D6`):
  - the slot becomes `$29` at full health;
  - the island type = code & 3;
  - goods = 5000, not capped;
  - owner = (code >> 4) + 1;
  - the game prints "X is now a friendly/an enemy island", then recomputes the class (`$1851A`) and the network (`$216C6`).
- Owned island: −8 per step, and the slot is cleared at 0.

## One simulation step (`world_sim_step`)

1. Advance `[$8D0]` (island index, 0–63). On wrap, shuffle the slot order table.
2. `select_island` (`$18940`): `[$8C4]` = island entry, `[$8C8]` = economy record, `[$8CC]` = 3D object buffer.
3. In warm-up mode: economy `+2` = 500, `+8` = `+$A` / 4. Then, on every step, an enemy island that's connected to NEMESIS (route matrix `M[63][i] >= 0`) is reset to 500 goods. Distribution and production are player-only, so this is how enemy islands get goods.
4. `island_census` (`$1854E`):
   1. Copy the quota table `$18DF8` → `$586AA`: 64 rows × 10 bytes, one per type, of `[priority, quota per class × 9]`. Only rows `$18–$2E` are meaningful.
   2. Compute the island class (`$1851A`), which selects the quota column:
      - 0 if the island is neutral;
      - otherwise the economy `+0` type, +3 if the island is large;
      - 7 if the type is 0.
   3. For every slot, take one unit of its type's quota. Under-construction types use the finished type's row. A slot with quota left is active; otherwise it's inactive. Rows with priority 0 (types below `$18`, the command centre `$29` and its builder `$34`) are skipped, so their `+6` byte is left alone.
      - A negative quota (trees `$19–$1B`) uses the fallback row `$18EE8`. That is the tree row of the **original** table in the code area, not the copy, so the decrements are permanent. Step 1 copies the same row for type `$18` too, so tree quotas drain to 0 for the rest of the game and trees end up inactive. This is persistent simulation state (a save game must keep it).
   4. Count the following:
      - active constructions (`[$8D4]`);
      - stalled constructions (`[$8D6]`);
      - active buildings of types `$24`+ (`[$8D8]`);
      - empty slots (`[$8DA]`).
5. `island_structures_step` (`$18662`): run each slot's type handler, in the shuffled slot order.
6. `island_plan_construction` (`$18898`): if fewer than 2 constructions are active, pick the buildable type (`$25–$2E`; the runway is skipped) with the highest priority and remaining quota. Ties go to the lower type. Place it as its under-construction type with progress 0 at `find_free_island_spot`, and create its 3D object if the island is loaded (`$182A4`).

**`find_free_island_spot`** (`$18A48`, type in d0): makes up to 16 tries.
- Each try takes one RNG value r. x = ((r.lo × (half_x − 1000 − R)) >> 16) × 2, and z is the same with r.hi and half_z, where R is the clearance radius for the type (table `$18BB4`).
- Each structure with a radius conflicts if the Chebyshev distance < its radius + R. A conflict with a non-tree rejects the try. Conflicting trees are noted.
- The try succeeds with the last empty slot, or failing that the last non-conflicting tree slot. The noted trees are then cut down (type 0).
- If no slot is available it returns −2 without retrying; after 16 rejected tries it returns −1.

Islands therefore grow towards a fixed per-class template of buildings. Supply limits construction (resource B) and command-centre capture (resource A).

## Supply network

- **Network:** `island_network` (`$2239E`, 32 bytes per island) holds a count, then (weight, neighbour) entries. Bit 15 of the neighbour word disables the link.
- **Link filtering** (`$2184E`): a link is enabled only if both islands have the same non-zero owner, so each side has its own network.
- **Route matrix** `[$67B40]` (64 × 64 words): `M[i][j]` is the shortest same-side route length from i to j, −1 if unreachable, 0 on the diagonal.
  - `$216C6` resets it and sets `[$1235]` = recompute pending. This happens whenever ownership changes.
  - `$2173A`, called from `game_main` while `[$1235]` is set, relaxes 8 rows per frame (`M[i][k] = min(M[i][k], w + M[n][k])` over i's enabled links) and repeats passes until a pass changes nothing.
  - Row 0 is VULCAN (the player base), so `M[0][i] >= 0` means island i is connected to the base. The island screen shows this as "DISTANCE TO BASE" / "ISOLATED".
- **Next hop** (`$21A30`): from island c towards d, the enabled neighbour n with `M[n][d] < M[c][d]` (strictly closer) that minimises `w + M[n][d]`. All compares are unsigned, so unreachable (−1) neighbours never qualify. A shipment moves to n and waits w seconds there.

## Logistics (`$F7A0`, called once per second from `game_main`)

Schedule. The countdowns are `$97E`/`$980`/`$982` (10), `$984` (1) and `$986` (4), and `[$97A]` counts seconds:
- **every second:**
  - objects `$1B8AE`;
  - island counts `$CDF2`;
  - the shipment movers `$21976` and `$21C28`;
  - the carrier-pool check `$21E8C`.
- **every 10 seconds:** carrier repair `$174E4`, and the Manta and Walrus hangar repairs `$1761C`/`$176AC`.
- **every 4 seconds:** produce `$21906`, totals `$22020`, plan `$22180`, run production `$21EE8`, then distribute `$21AA0`, in that order.

The economy is built around **raw material** (economy `+$02`, capacity `+$04`) and **goods** (`+$08`, capacity `+$0A`). Raw material is mined and spent by production lines; goods are what islands build with (see the census). Stock compares in this layer are unsigned. While the route matrix is being recomputed (`[$1235]` set), shipments that are due wait one more second.

| Routine | Every | What it does |
|---|---|---|
| `$21906` produce | 4 s | Owned islands add raw material, capped at capacity: base (kind 0) +100; resource (1) +100 × mines (`$2A/$2B/$2E`); factory (2) +25 × production lines; defence (3) nothing. A neutral island with a command-centre builder gains capacity / 16, which pays for the builder |
| `$22020` totals | 4 s | Per product: stores + pending (`$D96`) + in the goods pool + in production lines → `$DFA` (items), `$F3C` (fuels, ×`$1000`). Per island: goods + island-supply deliveries on the way or in production → `$E18` |
| `$22180` plan production | 4 s | Player side only; see below |
| `$21EE8` run production | 4 s | Lines on player bases and factories, at economy `+$0E` (count `+$0C` = the factory buildings `$2C/$2D`), 12 bytes each: `state.w, product.w, steps.w, dest.w, qty.w, cost.w`. State 0 loads steps, qty and cost from `$21FAE`. State 1 pays `cost` raw per step (it waits if the stock is short) and counts down. State 2 puts the product into the goods pool at this island, then the line goes idle (−1). If the pool is full, it retries next time |
| `$21AA0` distribute raw | 4 s | Player side only; see below |
| `$21976` move raw | 1 s | Raw shipments, pool `$66BC0`: 128 × `at.b, timer.b, amount.w, dest.w`. If the island it's at turns neutral, it's dropped. At the destination it's added to stock, but waits a second if that would overflow. With no route onward it's delivered where it is. If that overflows, the original stores the destination's island number as the stock (a bug) |
| `$21C28` move goods | 1 s | Goods shipments, pool `$66710`: 200 × `at.b, timer.b, kind.b, qty.b, dest.w`. Dropped if its island turns neutral, or if there's no route. `dest >= 0`: qty is added to that island's goods, capped (kind isn't checked). `dest < 0`: it travels to the stockpile island `[$D4A]`. There, kind < 15 adds to carrier stores `$D78[kind]` (cap 999), kinds 15–17 add qty × `$1000` to fuel `$D60` (cap `$1FFFC0`), and kind 18 is lost |
| `$21E8C` | 1 s | Clears `[$D50]` ("Stocks still in transit") once the carrier pool is empty |

**Pools.** The last 72 entries of the goods pool are the **carrier pool** `$66A10`. `$21D46` fills it. It adds the pending stores (`$D96`, `$D6C`) to the carrier's stores and fuel, then turns all of them into shipments from island `[$D4E]` to the stockpile (dest −1), in chunks of up to 255. It empties the stores and sets `[$D50]` ("Stocks still in transit"); they're re-added on arrival. Production only allocates from the first 128 entries (`$21D2C`). The movers and the totals cover all 200.

**Planning** (`$22180`). The routine builds a bit mask of the priority classes that have a need:
- **store items** (`$B76` records): a need when the total is below the target (`+$12`). It sets bit `+$10`, the class from the priority screen;
- **fuels:** a need when below `$1FFFC0`. It sets bit `[$D08 + 2k]`;
- **player islands connected to VULCAN:** bit 1 if their goods total is below capacity / 4, bit 0 otherwise. There's a capacity / 2 variant, but it tests the economy *word* `+0` for 3, so it never applies. Idle lines therefore top up islands even when nothing is needed.

The most urgent class wins (2, then 1, then 0) and goes to `[$DF6]`. Its candidates are written to `$E98`, with island supplies as `$40 + island`, and the count to `[$DF8]`. The list is shuffled and handed to idle lines in island order: an item or fuel ships to the carrier (dest −1), an island supply (product 18) to its island.

**Distribution** (`$21AA0`):
1. Raw stock per island is counted into `$F48`: stock + raw shipments heading there. The array starts from the value the previous routine left in register d0 rather than 0 (an original quirk the model reproduces).
2. Eligible destinations are player bases and factories connected to VULCAN. If any of them has stock ≤ capacity / 4, only those qualify.
3. Each destination is listed once per production line (`$FC8`), and the list is shuffled.
4. Every player resource island with ≥ 200 raw then ships 200 to the next destination on the list; its own connectivity isn't checked. If the raw pool is full, the 200 already deducted is lost.

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

## Enemy strategy (`$1AFAA`, every frame while `[$464]` is set)

The enemy carrier is **object 19** of the object list (`$5F49A`: x at `+$0A` = `$5F4A4`, z at `+$12` = `$5F4AC`, heading `$5F4A0`). Byte `+1` bit 0 makes it a live 3D object. While no player unit is close, the enemy moves abstractly between islands on timers. It only appears in the world when the player is near.

**Enemy globals:**

| Address | Contents |
|---|---|
| `$11BE` | target island |
| `$11C0` | current island |
| `$11C2` | state |
| `$11C4` | timer |
| `$11C6` | strength (12000 at start) |
| `$11CA` | aircraft / attack waves (8) |
| `$11D2` | reserve (12) |
| `$950` | attack pool (5000) |

**Player proximity** (`$1B4FA`): true if any of the first 9 objects (the player's units, active bit `+1.0`) is within 270000 (Chebyshev distance) of the enemy carrier.

**States** (jump table `$1AFC4`, 12 entries; 6–9 are no-ops):

| State | Routine | Behaviour |
|---|---|---|
| 0 | `$1AFF4` | Init: hide the carrier, strength = 12000, aircraft = 8, go to 1 |
| 1 | `$1B01A` | Once the route matrix is settled, choose a target (`$22C20`). Travel timer = distance / 500. Go to 5 |
| 5 | `$1B152` | Travel: count down, then move the carrier to the target island. If the island isn't loaded (the player isn't there), it's resolved abstractly: **neutral** → drop an enemy command-centre builder (`$34`, random code `$11–$13`), wait 400 (go to 4); **enemy-owned** → 2; **player-owned** → 3 (assault) if the target is NEMESIS or strength ≥ 3000, else 1 |
| 2 | `$1B06E` | At its own island: player near → 10 (materialise). Otherwise refit (strength 12000, aircraft 8, reserve 12) and go to 1 |
| 3 | `$1B0A4` | Assault: player near → 10. Otherwise bombard (`$1B558`). When no command centre (`$29`) is left, the island **falls**: owner = 0, network invalidated, the stockpile is reset if it was the stockpile island, an enemy command-centre builder is dropped, then wait 400 (go to 4). If strength < 2401 and the target isn't NEMESIS, retreat to 1 |
| 4 | `$1B13C` | Wait: player near → 10, else count down to 1 |
| 10, 11 | `$1B2D2`, `$1B49E` | Tactical mode: the carrier is placed 2000 + island size off a random side of the island and becomes a live object *(not yet decoded)* |

**Target selection** (`$22C20`, result in `$11BE`):
- If NEMESIS (63) isn't enemy-owned, the target is 63.
- Otherwise, if strength ≥ 3000 and aircraft > 0, each island gets a score and the highest wins (the current island is excluded). The score is the sum of:
  - −(distance from the enemy carrier) >> 11, or −15000 beyond 3,000,000;
  - ±(distance from NEMESIS) >> 14: positive for enemy-owned islands, negative for the others;
  - ownership: neutral +3000 (large) / +2000 (small), player +1500 / +1000, enemy −4000. On a coin flip, enemy islands are excluded entirely;
  - +4000 if a neighbour is connected to NEMESIS (`M[63][n] >= 0`, read through `$69AC0`);
  - +5000 for VULCAN if the enemy carrier is within 3,000,000 of it.
- Otherwise (low strength), retreat to the nearest enemy island connected to NEMESIS (route matrix row 63).

**Bombardment** (`$1B558`, every 200 frames):
1. Attack power = strength / 2 + min(aircraft, 4) × `$400`, sometimes boosted from the reserve.
2. Defence = the sum of per-type weights (`$1B73E`) for defence buildings `$25–$28` on the island.
3. Random hits on random structures: `$400` damage to buildings, 4 to constructions. A structure is removed at 0.
4. Return fire costs the enemy strength (in `$400` steps) or attack pool / aircraft.
5. If strength goes negative, the enemy AI stops (`[$464] = 0`) and the game-end timer starts (`[$732] = 100`).

## Win/lose bookkeeping

`$CDF2` (every second) counts owned islands: player in `[$D54]`, enemy in `[$D56]`, and player islands per kind in `$D58`. The kind-0 counter `$D58` is never cleared, so it grows by the number of player bases every second. `game_main` ends the game when either count reaches 64 (`[$466]` = 2 or 3).

## Carrier repair

`$174E4` (every 10 s) spreads the repair rate `[$B3A]` × 150 / 256 evenly over the damaged carrier systems in the most urgent priority class. There are 10 systems at `$AF4`, 8 bytes each: current, max, priority class (0–2; higher values count as 2). The step goes to `[$B5E]`, the class to `[$B5C]` and the count to `[$B60]`. This is the "Set Priorities" repair screen. If it's open (`[$718]` = 1), it asks for a redraw.

## Reference model and verification

`tools/stsim.py` is a Python port of `world_sim_step` and the logistics tick (`$F7A0`). The state is the game's own memory layout: a 512 KB image in which only `STATE_REGIONS` are populated. Islands, slots and economy records are views onto it, so the state serialises trivially and moves either way between the model and an emulator.

Not modelled:
- the route relaxation `game_main` runs per frame;
- the unit-layer parts of the tick (`$1B8AE`, `$1761C`, `$176AC`);
- the 3D buffers of the island loaded around the camera.

Tools:
- `tools/capture_sim.py` runs the original in Hatari and starts a strategy game by poking the menu mouse (`$474/$476`) and left button (`$880`). It saves RAM around `world_warmup`, around single `world_sim_step` calls, and around 8 consecutive logistics ticks at chosen frames.
- `tools/check_sim.py` compares every state region byte for byte against:
  - the Hatari *out* captures;
  - the game's own code under Unicorn from the same *in* captures;
  - Unicorn, second by second (step plus tick), over long runs from each capture and from randomly perturbed copies (ownership, damage, stocks, builds, production targets and classes, lines and shipments in flight, full pools). For these, the unit routines are stubbed out in the reference.

Status:
- Hatari: the 601-step warm-up, single steps at 1, 10 and 40 minutes of game time, and 24 logistics ticks match exactly.
- Unicorn: 1280-second runs, and six 640-second fuzzed runs per capture, match exactly. No interrupt touched the RNG during the captures.
- Not exercised: `find_free_island_spot` failing (all 32 slots full), and planning with a priority class ≥ 8 (the model raises; the original would shuffle an empty list over memory).

## Still to decode

- Enemy tactical states 10/11, and the enemy unit AI (object handlers `$1A5B6` for object types 16–23, 33 and 34: missiles, drones, the enemy aircraft (33), and the homing missile (34, "Missile heading for Carrier!")).
- How the enemy side's economy feeds its strength. Production and the island census run for both owners, but distribution, planning and production lines are player-only; enemy islands get goods from the NEMESIS resupply in `world_sim_step`.
- The unit/object list at `$5DCDA` (`$140` bytes per object, count `[$A48]`): the carrier, Mantas, Walruses and enemy units.
- The per-frame vehicle simulation called from `game_main` (`$17094`, `$1962A`, `$1A56A`, `$1AFAA`, `$1A296`, `$1B746`, `$20730`).
