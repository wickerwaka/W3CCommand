# World state and simulation

This is a reverse-engineering reference for the strategy simulation in `CARRIER.TOS`. Addresses are runtime addresses; `[x]` means "the value at x". Entries marked *(guess)* are unconfirmed. Ghidra names are given in `code` where assigned.

## Timing

- `vbl_handler` (50 Hz) runs the game clock at `$8F2–$8FA` (days, hours, minutes, seconds, frames). Every 50 frames (1 s) it sets `[$8FC]` and `[$8FD]`.
- `game_main`'s strategic block (`$40D8`) runs on **every main-loop pass** while a game is in progress and not paused. It does the following, in order:
  1. route relaxation `$2173A`, while `[$1235]` is set;
  2. the logistics tick `$F7A0`, only when `[$8FD]` is set (once per second);
  3. `world_sim_step` (`$1848E`), one island per call;
  4. the enemy AI `$1AFAA`.
- The pass rate depends on rendering. Hatari measured 80 island steps and 10 ticks in 10 s of game time in the 3D view: about 8 passes per second, so a full pass over the islands takes about 8 s. The strategic simulation therefore runs faster when the screen is cheaper to draw (map or menus). The bombardment and wait timers count passes, not seconds.
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
  - `$2173A`, called from `game_main` while `[$1235]` is set, relaxes 8 rows per call: `M[i][k] = min(M[i][k], w + M[n][k])` over i's enabled links (unsigned compare, skipping unreachable `M[n][k]`), updating in place. The cursor is `$DEC` (row), `$DF0` (network record) and `$DF4` (8 slices). After the 8th slice, a pass with no change (`[$1234]` still set) clears `[$1235]`; otherwise a new pass starts.
  - Row 0 is VULCAN (the player base), so `M[0][i] >= 0` means island i is connected to the base. The island screen shows this as "DISTANCE TO BASE" / "ISOLATED".
- **Next hop** (`$21A30`): from island c towards d, the enabled neighbour n with `M[n][d] < M[c][d]` (strictly closer) that minimises `w + M[n][d]`. All compares are unsigned, so unreachable (−1) neighbours never qualify. A shipment moves to n and waits w seconds there. The column offset is the word 2·d, used signed: when the enemy takes the stockpile island, `[$D4A]` becomes `$FFxx` and shipments to the stockpile read the matrix 512 bytes before their rows (rows c − 4, or the end of the economy records for c < 4), so they wander or are dropped. The model reproduces this.

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
| `$21C28` move goods | 1 s | Goods shipments, pool `$66710`: 200 × `at.b, timer.b, kind.b, qty.b, dest.w`. Dropped if its island turns neutral, or if there's no route. `dest >= 0`: qty is added to that island's goods, capped (kind isn't checked). `dest < 0`: it travels to the stockpile island `[$D4A]`. There, kind < 15 adds to the **stockpile** stores `$D78[kind]` (cap 999), kinds 15–17 add qty × `$1000` to stockpile fuel `$D60` (cap `$1FFFC0`), and kind 18 is lost. The stockpile isn't on board: the supply drone ferries it to the carrier (`docs/units.md` §6.7) |
| `$21E8C` | 1 s | Clears `[$D50]` ("Stocks still in transit") once the carrier pool is empty |

**Pools.** The last 72 entries of the goods pool are the **carrier pool** `$66A10`. `$21D46` fills it. It adds the queued transfers (`$D96`, `$D6C`) back to the stockpile stores and fuel, then turns all of them into shipments from island `[$D4E]` to the stockpile (dest −1), in chunks of up to 255. It empties the stores and sets `[$D50]` ("Stocks still in transit"); they're re-added on arrival. Production only allocates from the first 128 entries (`$21D2C`). The movers and the totals cover all 200.

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

**Player proximity** (`$1B4FA`): true if any active player unit (objects 2–10, active bit `+1.0`) is within 270000 (Chebyshev distance) of the enemy carrier's position. The carrier's own `+1.0` bit is set while it's a live 3D object.

**States** (jump table `$1AFC4`, 12 entries; 6–9 are no-ops). Timers count main-loop passes:

| State | Routine | Behaviour |
|---|---|---|
| 0 | `$1AFF4` | Init: hide the carrier, strength 12000, `$11C8` = `$7FFF`, aircraft 8, go to 1 |
| 1 | `$1B01A` | Waits while the route matrix is pending. Then choose a target (`$22C20`); travel timer = (max(\|dx\|,\|dz\|) + min/2) / 500, `$FFFF` on overflow. Go to 5 |
| 5 | `$1B152` | Travel: count down, then the carrier is at the target (current island, object position = island position). If the island is loaded (the player is there): go to 1. Otherwise hide the carrier, then: **neutral** → drop a builder; **enemy** → 2; **player** → assault (3) if it's NEMESIS or strength ≥ 3000, else 1. An assault resets the attack pool to 5000 and the bombard timer to 400 |
| 2 | `$1B06E` | At its own island: player near → engage. Otherwise strength = 12000. If the island is connected to NEMESIS, aircraft = 8 and reserve = 12. Go to 1 |
| 3 | `$1B0A4` | Assault: player near → engage. Otherwise bombard. If the island **falls** (no `$29` in slots 1–31): owner = 0, network invalidated. If it was the stockpile, `st.b` marks `[$D4A]` invalid (high byte `$FF`) and `stores_clear` empties the stores. Then drop a builder. If it doesn't fall and the island isn't NEMESIS and strength ≤ 2400, the attack is repulsed (go to 1) |
| 4 | `$1B13C` | Wait: player near → engage, else count down to 1 |
| 10, 11 | `$1B2D2`, `$1B49E` | Close-range combat with the live carrier (unit layer, *not yet decoded*) |

**Drop a builder** (`$1B1B6`): place an enemy command-centre builder (`$34`, progress 0, code `$10` + a random 1–3) with `find_free_island_spot`, then wait 400 (state 4). It calls `select_island`, which repoints `$8C4–$8CC` at that island. On a neutral island the builder grows with the island's raw material, which a builder makes accumulate at capacity / 16. On completion the island turns enemy.

**Engage** (`$1B250`): state 10. The carrier is moved 2000 + `[$454]` + the island half-extent off one of the four sides of its island, chosen by the RNG's top two bits, facing the island (heading 0/`$4000`/`$8000`/`$C000`). It becomes a live object.

**Target selection** (`$22C20`, result in `$11BE`). It first copies the carrier position to `$11A6`/`$11AA`.
- If NEMESIS (63) isn't enemy-owned, the target is 63.
- **Strong** (strength ≥ 3000 and aircraft > 0): each island gets a word score in `$22B9E` (inside the code area), and the highest wins, ties going to the lowest island. The score is the sum of:
  - −(distance from the carrier) >> 11, or −15000 beyond 3,000,000 (distance = max + min/2);
  - (distance from NEMESIS) >> 14: added for enemy islands, subtracted for others;
  - neutral +3000 (area > 2000) / +2000; enemy −4000; any other owner +1500 / +1000;
  - non-enemy islands: +4000 if any neighbour (enabled or not) is connected to NEMESIS (`M[63][n] >= 0`);
  - enemy islands: excluded (`$8002`) when this call's coin flip is set (`$22C1E`, the RNG's sign bit);
  - VULCAN: +5000 if it isn't enemy-owned and the carrier is within 3,000,000 of it;
  - the current island: `$8001` (excluded).
- **Weak:** the nearest enemy island whose route to NEMESIS exists (`M[i][63] >= 0`). Ties go to the higher island. NEMESIS itself always qualifies.

**Bombardment** (`$1B558`, every 200 passes of state 3; timer `$94E`):
1. **Attack** = min(aircraft, 4) × `$400` + strength / 2. **Defence** = the sum of `$1000/$200/$200/$300` for building types `$25–$28` in slots 1–31. **Targets** = slots 1–31 that aren't empty, the island (7) or a runway (`$24`); trees count. These go to `$94A/$948/$94C`.
2. **Reserve** (only while > 4):
   - 25%: −1 reserve (its `+$400` to `$94A` is then overwritten).
   - If defence > attack, attack +`$800`. If it's still lower, −1 reserve and attack +`$400` when 5 + rand(8) ≤ reserve.
3. **Hits** = ((rand × attack) >> 16) / `$600`, and **return fire** = the same with defence. These use `divu`; on overflow the 68000 leaves the register unchanged, and the model copies that.
4. Each hit strikes a random target, uniformly among those left. It does `$400` damage to buildings and 4 to constructions; when health falls to 0 or below (unsigned), the structure is removed. Hits stop when no targets are left.
5. Each return fire, unless the enemy is weak (strength < 2400 and not at NEMESIS):
   - 25%: −`$400` strength. If that goes negative, the enemy is **destroyed** (`$1B728`): the AI stops (`[$464]` = 0), the game-end timer `[$732]` = 100, and "Enemy Carrier destroyed!!!!" shows (`$A128`). The routine returns at once: the remaining return fire and the command-centre check are skipped. `$A128` always leaves d0.w = 0 (its last byte copied is the message's terminator, and the sound call `$20B90` restores d0), so the island never "falls" here; state 3 then sees strength ≤ 2400 and calls the attack repulsed (state 1, message) unless the island is NEMESIS. From the next pass on the AI is off.
   - otherwise, if the enemy has aircraft: the attack pool takes the hit.

   When weak, the pool always takes it. The pool (`$950`) loses `$400`. When it goes negative, an aircraft is lost and, if any are left, the pool refills to 5000.

## Win/lose bookkeeping

`$CDF2` (every second) counts owned islands: player in `[$D54]`, enemy in `[$D56]`, and player islands per kind in `$D58`. The kind-0 counter `$D58` is never cleared, so it grows by the number of player bases every second. `game_main` ends the game when either count reaches 64 (`[$466]` = 2 or 3).

## Carrier repair

`$174E4` (every 10 s) spreads the repair rate `[$B3A]` × 150 / 256 evenly over the damaged carrier systems in the most urgent priority class. There are 10 systems at `$AF4`, 8 bytes each: current, max, priority class (0–2; higher values count as 2). The step goes to `[$B5E]`, the class to `[$B5C]` and the count to `[$B60]`. This is the "Set Priorities" repair screen. If it's open (`[$718]` = 1), it asks for a redraw.

## Reference model and verification

`tools/stsim.py` is a Python port of the whole strategic layer: `world_sim_step`, the logistics tick, route relaxation, and the enemy carrier AI (states 0–5, targeting, bombardment). `WorldState.frame(new_second)` runs one `game_main` pass. The state is the game's own memory layout: a 512 KB image in which only `STATE_REGIONS` are populated. Islands, slots and economy records are views onto it, so the state serialises trivially and moves either way between the model and an emulator.

Not modelled:
- the unit layer: objects, vehicles, the tactical enemy states 10/11, the tick's `$1B8AE`/`$1761C`/`$176AC`, and the per-object island caches at object `+$40`;
- the 3D buffers of the island loaded around the camera.

The model raises `TacticalMode` when the enemy engages (the unit layer, `tools/stunits.py`, continues from there). The enemy's destruction is modelled; `EnemyDestroyed` is no longer raised.

Tools:
- `tools/capture_sim.py` runs the original in Hatari and starts a strategy game by poking the menu mouse (`$474/$476`) and left button (`$880`). It saves RAM around `world_warmup`, single `world_sim_step` calls, 8 consecutive logistics ticks and 8 enemy-AI calls at chosen frames.
- `tools/check_sim.py` compares every state region byte for byte against:
  - the Hatari *out* captures;
  - the game's own code under Unicorn from the same *in* captures;
  - Unicorn, frame by frame (8 passes per game second), over long runs from each capture and from randomly perturbed copies. The perturbations cover ownership, damage, stocks, builds, production targets, shipments, full pools, and the enemy part way through assaults, trips and refits;
  - 40 short enemy scenarios: assaults on fortified islands, last stands at NEMESIS, a player unit nearby. Some destroy the enemy carrier and run on with the AI off.

  The unit routines are stubbed out in the reference for these runs.

Status:
- Hatari: the 601-step warm-up, three single steps, 24 logistics ticks and 24 enemy-AI calls match exactly.
- Unicorn: 1280-second runs, six 640-second fuzzed runs per capture, and the enemy scenarios match exactly. These include island captures and falls, repulses, stockpile loss, aircraft losses and the enemy's destruction.
- Not exercised:
  - `find_free_island_spot` failing (all 32 slots full);
  - planning with a priority class ≥ 8;
  - a single bombardment round that destroys every target.

## Still to decode

- How the enemy side's economy feeds its strength. Production and the island census run for both owners, but distribution, planning and production lines are player-only; enemy islands get goods from the NEMESIS resupply in `world_sim_step`.
- The unit layer (object list, vehicles, weapons, close-range combat): analysed in `docs/units.md`, not yet modelled.
