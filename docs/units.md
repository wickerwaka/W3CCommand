# Unit layer

This is a reverse-engineering reference for everything below the strategic simulation (`docs/simulation.md`): world objects, the carrier and its vehicles, weapons, AI units, collisions and damage, island defences, the enemy carrier's close-range combat, screens and controls, and the save format.
- It's static analysis (Ghidra, `tools/dis68k.py`) checked against RAM captures (`emu/out/sim/*.bin`, a quiet strategy game) and Hatari screen measurements.
- Nothing here is modelled or verified the way the strategic layer is (see "Plan" at the end).
- Unmarked statements were read from the code; *(guess)* marks inferences.
- Names in `code` with an address are proposals unless they already exist in Ghidra.

## 1. Timing: everything runs per pass

`game_main` is a single loop locked to the display. `frame_flip` (`$17F48`) waits for a VBL. While `[$8F0]` is set (in game), it keeps waiting until `[$8EE]` (VBLs since the last flip) is at least 3. So a pass takes a whole number of VBLs, **at most 16.7 passes per second**.

**There is no delta-time.** `$8EE` is read only by `frame_flip`; the Timer C counter only feeds the frame-rate display.
- Per pass: unit motion, timers, collision scheduling, the island step and the enemy AI.
- In real time (VBL): only the game clock (`$8F2–$8FA`) and the logistics tick.

The game therefore runs faster on cheap screens. Measured in Hatari (500 VBLs per screen, at 50 VBLs per game second):

| Passes per game second | VBLs per pass | Screens |
|---|---|---|
| 16.7 | 3 | resources, messages, viewing drone, defence radar, Walrus control and status, Manta control and status |
| 12.5 | 4 | damage status, maps |
| 10 | 5 | carrier weapons status |
| 8.0–8.2 | 6 | bridge 3D view, Walrus dock, Manta deck |
| 7.1 | 7 | laser turret, flares view |
| 0 | — | game status, title, game over (paused) |

Counters:
- `$944`: pass counter mod `$8000` (`$1B746`), used for collision scheduling;
- `$8EC`: pass counter mod 16 (`$1CD4E`); `[$8FF]` is set on its wrap, and that "slow tick" runs fuel burn;
- countdowns per pass: `$115E` (SSM), `$940`/`$942` (Manta and Walrus laser), `$93E` (turret), `$1150` (flares), `$658` (damage message).

**Port implication:** run the strategic step and the whole unit layer on a fixed pass clock. Run the logistics tick and the game clock on a separate 1 Hz clock. The ratio is a design choice: the original's was 7–17 passes per game second depending on the screen.

## 2. The frame loop (`$408A`)

The init calls are listed in the frame report: interrupts `$17966`, floppy `$213B0`, `session_reset` `$4206`, score `$A3E0`, `globals_snapshot` `$1A4C6`, and `goto_title` `$4272`. One pass does the following:
1. `$F636` `ui_frame`: screen changes and the mouse hit test (§10).
2. `$4194`: misc toggles.
3. If `[$462]` = 0 (paused or not in a game): `$44EE` `paused_frame` (keyboard while paused, debug-code entry), then go to step 6.
4. If `[$461]` (the game-status menu is open): go to step 6. Otherwise `$46E8` (debug keys), then the **simulation block**:
   1. win/lose by island counts (`$40B0`);
   2. `$2173A` route relaxation, while pending;
   3. `$17094` `screen_input`: the per-screen input handler, then edge-flag cleanup;
   4. `$F7A0` logistics tick, when `[$8FD]` is set;
   5. `$1848E` `world_sim_step`;
   6. `$1CC86` `debris_step` (§5.4);
   7. `$1CD4E` `vehicles_step` (§6);
   8. `$1962A` `island_behaviours_step` (§8);
   9. `$1A56A` `ai_objects_step` (§7);
   10. `$1AFAA` `enemy_ai_step` (`docs/simulation.md`, §9);
   11. `$1A296` `defence_drones_step` (§6.6);
   12. `$1B746` `object_passes` (§5);
   13. `$20730` engine sound;
   14. `$17578` `carrier_systems_status` (§6.2);
   15. `$A1A4` message line.
5. —
6. Unless the debug freeze is on: `$14472` `screen_dispatch` (render), then `$17F48` `frame_flip`.
7. End-of-game timer `$732`. At 0 it requests the game-over screen 35.
8. `[$463]` (end requested) → screen 34.

**Game state flags:**

| Flag | Meaning |
|---|---|
| `$460` | modal pointer mode (`jmp [$468]`) |
| `$461` | game-status menu open; simulation skipped |
| `$462` | simulation running; PAUSE toggles it; the clock only runs while it's set |
| `$463` | end-of-game request |
| `$464` | enemy AI on |
| `$466` | result: 0 quit, 1 carrier destroyed, 2 enemy holds 64 islands, 3 player holds 64 |
| `$457` | pointer mode; clear = mouse-steer (right button toggles on screens 0, 8, 18, 27) |
| `$124A` | strategy game |
| `$1189` | **debug mode**: type "GROW OLD ALONG WITH ME" while paused |
| `$118A` | debug cheat: Manta invulnerable, no stall |

## 3. World objects

### 3.1 Two stores

| Store | Address | Layout | Coordinates |
|---|---|---|---|
| World objects | `$5DCDA` | 64 × `$140` (`[$A48]` = 64) | 32-bit x/y/z at `+$A/+$E/+$12` |
| Island buffers | `$62CDA`, `$63CEC` | `$12` header + **64** × `$40` | 16-bit, relative to the buffer origin, at `+$A/+$C/+$E` |

Both share the first `$40` bytes of the layout. Island-buffer entries 0–31 mirror the island's structure slots; 32–63 are dynamic (rocks, drones, SAMs).

**Island buffer header:** `+0` in use, `+1` island, `+2/+6/+$A.l` origin x, y, z, `+$E.w` entry count (`$40`), `+$10.w` runway-slot count (`$74E`; the renderer draws that many entries after the island unsorted, see `docs/memory-map.md`). The drone pad's "drones out" counter is at `[$A2C]+$10`, in the island table, not here.

`load_island_buffer` (`$17FB4`) builds one:
- **Entry 0** is the island itself (type 8, local y `$28`, `+$1E/+$20` = half-extents).
- **Each slot** goes through `slot_entry_table` `$18066` (8 bytes per slot type: `handler.l, param.l`):
  - a negative handler creates the entry type −handler at the slot's x/z and heading, with **local y = param + `$50`**, and health `+$38` = slot value;
  - the y params are: volcano 600; types `$0E`/`$0F` 60/45; trees 200/250/100/175; `$25` 300, `$26` 30, `$27` 40, `$28`/`$29` 100, `$2A` 500, `$2B`/`$2C` 200, `$2D`/`$2E` 100 (construction types are the same);
  - this gives each object's vertical placement on the island.
- **Sync:** `mirror_slot_to_buffer` (`$18276`) and `create_slot_object` (`$182A4`) keep entries in step with the slots.

### 3.2 Object record (`$140` bytes)

| Offset | Meaning |
|---|---|
| `+$00.w` | flags (§3.3) |
| `+$02.w` | type; **type = model number** (`object_lod_table` near model = type, except type 23 = invisible) |
| `+$04/+$06/+$08.w` | pitch, yaw (heading), roll. 16-bit, `$10000` = 360°. `$4000` pitch = straight down, `$C000` = straight up |
| `+$0A/+$0E/+$12.l` | world x, **y (up)**, z. Sea-level hulls have y = 160 |
| `+$1A.w` | vehicles: bay index 0–3. AI objects: `+$1A.b` state, `+$1B.b` lifetime. Island entries: sub-state |
| `+$1C` | vehicles: local offset while on the carrier. AI: timer/flag |
| `+$1E` | target object pointer or target point; desired heading on the enemy carrier |
| `+$20–$2A` | per type: velocity (ballistic `+$20/+$22/+$24`), waypoints, the Walrus's guided missile (`+$20`) |
| `+$26.w` | Walrus turret heading (relative) |
| `+$30.w` | collision radius (Chebyshev); negative = special shape (carriers: oriented box) |
| `+$32.w` | collision half-height |
| `+$34.w` | damage class when this object hits something (§5.3) |
| `+$36.w` | max speed per pass (collision scheduling only) |
| `+$38.w` | health: island entries mirror the slot value; enemy aircraft; drones |
| `+$3A.w` | island re-attach countdown (24–31 passes) |
| `+$3C.l` | attached island buffer, or 0 |
| `+$40–$BF` | 64 words: the pass at which to next test against island-buffer entry k |
| `+$C0–$13F` | 64 words: the pass at which to next test against world object j (j < own index) |

### 3.3 Flags (`+$00`, word bit numbers)

| Bit | Meaning |
|---|---|
| 0 | live; the allocator looks for it clear; `clr.w (a4)` despawns |
| 1 | visible (the renderer needs bits 0 and 1) |
| 2 | no box test against carriers; extra render fields on island entries |
| 3 | *(guess)* set by the drone code |
| 8 | has a per-pass behaviour (AI pass and island behaviours) |
| 9 | inside the attached island buffer's 16-bit range. Island entries: active only while the viewed unit is at the island |
| 10 | collides (both sides need `$401`) |
| 11 | large: −1000 on the proximity distance per large side. The code tests it as `btst #3` on the high byte |
| 12 | lockable by the Manta missile sight (`$200F6`) |
| 14 | attackable structure for enemy aircraft (`$1AF20`) |
| 15 | *(guess)* absolute world coordinates / mobile unit |

Byte `+1`, bit 0 is the live/visible test used by many routines. Byte `+1`, bit 2 marks a vehicle on the lift or in the dock (it doesn't collide with the carrier).

### 3.4 Slot roles

| Objects | Role |
|---|---|
| 0 | camera / viewpoint (`[$9FA]`) |
| 1 | secondary view object *(guess)*; the object–object pass starts here |
| **2–5** | **Mantas** 0–3 (type 5), bay records `$496 + $34·k` |
| **6–9** | **Walruses** 0–3 (type 6), bay records `$570 + $34·k` |
| 10 | **player carrier** (type 1). Objects 2–10 are the units the enemy's proximity test checks |
| 11–14 | carrier **defence drones** (types 10–13), stationed by `$1A296` at offsets (0, +3000), (+3000, 0), (0, −3000), (−3000, 0) |
| 15 | recon / viewing drone (type 32), store item 11 |
| 16 | supply drone (type 35); ferries stores and fuel from the stockpile island (§6.7) |
| 17, 18 | decoy flares (type 30), store item 10 |
| 19 | enemy carrier (type 4) |
| 20–23 | enemy fighters (type 33) |
| 24–63 | dynamic pool (`find_free_object` `$1A10E`): missiles, bombs, laser bolts, blasts |

`docs/memory-map.md` lists model 14 as the Walrus; the code uses type and model 6.

### 3.5 Types and creation

- **`init_world_object`** (`$192E2`, a0 = object, d0 = type):
  1. clears `+$00–$3F`;
  2. fills all 128 schedule words with the current pass, so every pair is tested next pass;
  3. sets the type, flags from `$19362[type]`, and `+$30–$36` from `$193D6 + 8·type`.
- **`init_island_object`** (`$1932C`): the same without the schedules.
- **`find_free_object`** (`$1A10E`): the first object 24–63 with byte `+1` bit 0 clear; index in `[$6EE]`, Z set if full. Spawners then call `$195DA` and usually `st.b` their own schedule entry, so a missile never hits its launcher.
- **Per-type collision parameters** (`$193D6`):

| Type | What | radius | half-height | damage class | speed |
|---|---|---|---|---|---|
| 1–3 | player carrier | −1 (box) | 170 | 11 | 250 |
| 4 | enemy carrier | −1 | 170 | 11 | 250 |
| 5 | Manta | 150 | 75 | 4 | 250 |
| 6 | Walrus | 110 | 75 | 4 | 96 |
| 7 | volcano | −1 (cone) | 700 | 11 | 0 |
| 9 | volcano rock | 50 | 50 | 1 | 75 |
| 10–13 | defence drones | 100 | 100 | 2 | 40 |
| 16 | laser bolt | 230 | 100 | 4 | 350 |
| 17 | homing missile (Assassin) | 125 | 60 | 5 | 250 |
| 18 | Walrus guided missile (Harbinger) | 150 | 150 | 8 | 150 |
| 19 | cruise missile (Hammerhead) | 150 | 150 | 3 | 150 |
| 20 | Manta bomb (Quaker) | 200 | 200 | 3 | 300 |
| 21 | virus bomb | 100 | 100 | 2 | 150 |
| 22 | island defence drone | 200 | 60 | 5 | 250 |
| 23 | explosion blast | 0 | 0 | 3 | 200 |
| 24–27 | trees | 120 | 170 | 2 | 0 |
| 28 | island SAM | 100 | 75 | 7 | 265 |
| 30 | decoy flare | 100 | 100 | 3 | 250 |
| 32 | recon drone | 100 | 100 | 2 | 250 |
| 33 | enemy fighter | 150 | 75 | 5 | 200 |
| 34 | anti-ship missile | 150 | 150 | 8 | 200 |
| 35 | supply drone | 150 | 150 | 5 | 100 |
| 37–46 / 48–57 | buildings / constructions | per type | | 11 | 0 |

### 3.6 Units and helpers

- **Axes:** x right, y up, z forward. Island records store x and z as word × 512.
- **Angles:** 4096-entry sine table at `$1F5A` (amplitude 16384); cos(a) = sin(a + `$4000`).
- **Rotation:** `$A5E0` (yaw) and `$A620` (pitch) rotate (0, s) exactly, so **speed s = s world units per pass**.
- **Helpers:**
  - `yaw_vector` `$1A140`;
  - `velocity_from_angles` `$1A164` (pitch and yaw);
  - `turn_towards` `$1A18A` (approach a value by at most d2);
  - `aim_angles` `$1A80C` (atan2 at `$AF8E`);
  - `cheb_dist` `$1A866`;
  - `over_island` `$1A1A2` (the position against the island rectangle `+$30/+$32`);
  - `nearest_hostile` `$1A1CC`/`$1A1D8`.

## 4. Per-pass object processing (`$1B746`)

1. **`island_attach_step`** (`$1B762`), objects 2–63 that are live, every 24–31 passes:
   - **Attached:** an object more than 130000 from the buffer origin on either axis detaches; `release_island_buffer` (`$182F6`) frees the buffer when no other player unit uses it.
   - **Unattached:** the object finds the first island within 130000 (Chebyshev) and attaches to its buffer if it's loaded.
   - **Loading:** only the carrier or a Manta/Walrus may *load* an island into a free buffer (`load_island_buffer`; `trap #6` if both buffers are busy). Missiles and the enemy carrier only attach to islands that are already loaded.
2. **`collide_island_objects`** (`$1B90E`): world objects with `$601` set against the entries of their buffer.
3. **`collide_objects`** (`$1BB40`): world-object pairs (1–63, j < i).
4. `[$944]` = (`[$944]` + 1) & `$7FFF`.

`island_range_check` (`$1B8AE`, once per second in the tick) sets bit 9 once an attached object's offset fits in 16 bits.

### 4.1 Collision scheduling

A pair is tested only on the pass stored in its schedule word. After a miss it reschedules itself:
- **far** (Chebyshev d > 1500, minus 1000 per large side): the next test is pass + max(1, (d − 750) / (speed_a + speed_b)) for island entries, or (d − 900) / … with `divs` (5000 on overflow) for object pairs;
- if both speeds are 0, it's never tested again.

This is a time-to-contact bound, so distant objects cost nothing. An `st.b` on a schedule word writes `$FFxx`, which never matches: that pair never collides.

### 4.2 Contact test

| Shape | Contact when |
|---|---|
| default | \|dy\| ≤ h_a + h_b and max(\|dx\|, \|dz\|) ≤ r_a + r_b |
| volcano (type 7) | cone: r = (700 − dy)/2 + 300 |
| types `$25` and `$30` | r = 750 − dy |
| carriers (r < 0) | oriented box: rotate (dx, dz) by −yaw, then \|across\| ≤ r + 400 and \|along\| ≤ r + 1000 *(check the sign handling: r = −1)* |

### 4.3 Response and damage

`collision_response` (`$1BD30`) runs for both objects of the pair:
- `[$754]` = the hitter's damage class `+$34`, and `[$756]` = `damage_by_class[class]` (`$1BD58`): **0, 128, 256, 512, 768, 1024, 1536, 2048, 2560, 3072, 4096, 4096**;
- then it jumps through `hit_handler_table` `$1BD70`[victim type]:

| Victim | Handler | Effect |
|---|---|---|
| player carrier 1–3 | `$1C188` | damage spread over the carrier systems by hit position (§6.2) |
| enemy carrier 4 | `$1C0F4` | **strength `[$11C6]` −= dmg** (except from its own fighters). At ≤ 0: AI off, removed, 4 × 25000 points, "Enemy carrier destroyed", game-end timer 100 |
| Manta 5 | `$1BF84` | bay health `+$14` −= dmg (unless `[$118A]`); ≤ 0 destroyed (`$19188`), else "…has been damaged!" (500-pass hold-off) |
| Walrus 6 | `$1BE5A` | rammed by the carrier: pushed back and 1000 damage (2000 for bays 1–3). Otherwise bay health −= dmg, destroyed at ≤ 0 (`$191F2`) |
| volcano rock 9 | `$1C2EE` | removed, 10 points |
| defence drones 10–13 | `$1C010` | destroyed by anything except another drone |
| laser bolt 16 | `$1BFFA` | removed |
| missiles 17/18 | `$1C06E`/`$1C080` | removed, debris |
| 19/20 | `$1C5EE` | `explode_blast` |
| virus bomb 21 | `$1C512` | see §8.3 |
| island drone 22 | `$1C092` | health; destroyed → 750 points on enemy islands, island drones-out −1 |
| trees | `$1C272` | removed, 1 point |
| enemy fighter 33 | `$1C4E0` | health `+$38`; destroyed → airborne count −1 |
| anti-ship missile 34 | `$1C2D2` | destroyed, 250 points on enemy islands |
| supply drone 35 | `$1C0CA` | health; destroyed → `$1925C` |
| buildings 37–46 | `$1C3D6…` | see §8.3 |
| constructions 48–57 | `$1C4B6` | health −= the raw class (1–11), 15 points per hit on enemy islands |

### 4.4 Debris (`$1CC86`)

This isn't a weapon. There are 4 bursts at `$11D8` (`$12` bytes: active, age, origin x/y/z, shard-list head) and a shard pool at `$66010` (64 × `$1A`).
- `spawn_debris` (`$1CA74`/`$1CA8E`) takes a per-type recipe from `$1C726` (count, speed, angle ranges, then (count, model) pairs from models 58–73) and random velocities and spins.
- Shards fall at −10 per pass (terminal −40) and are freed below the ground.

`$1CAB4` compares the type with the memory word at `$2E` instead of `#$2E`, probably a bug.

## 5. The player carrier (object 10)

### 5.1 Movement (`carrier_step` `$1E744`)

| Global | Meaning |
|---|---|
| `$640.b` | flags: bit 0 scripted manoeuvre or autopilot; bit 1 steer to the waypoint; bit 2 announce arrival; bit 7 rudder self-centres |
| `$641` | sub-state |
| `$642` | rudder ±255 |
| `$644` | speed |
| `$646` | target speed |
| `$64A` | depth under the keel |
| `$64C` | recoil timer |
| `$64E/$652.l` | waypoint x/z |
| `$656` | waypoint cruise speed |
| `$65A` | a vehicle is within 200000 |
| `$65B` | fast cruise |
| `$CFC.l` | **ship fuel** |

Per pass:
1. Superstructure `[$B3C]` = 0 → game lost (`$466` = 1).
2. Lift and dock door (§6.4).
3. **Manual** (bridge screen): rudder += mouse x (±255); target speed ± 1 per pass while a speed key is held.
4. **Fuel**, every 16 passes: `$CFC` −= \|speed\| × 4 (× 16 in fast cruise).
5. **Speed governor** (`$1EA3E`):
   - forward max = min(engine status / 2, 128), or 60 near an island or while a Manta is landing;
   - reverse max = −engine status / 8;
   - fuel 0 → target 0;
   - speed += (target − speed) >> 5, at least ±1.
6. **Heading** += rudder / 6, i.e. up to about 0.23° per pass.
7. **Position** += (0, speed) rotated by heading, ×4 in **fast cruise**. Fast cruise means open sea, no vehicle within 200000, and no landing in progress.
8. **Grounding:** within 10000 of an island's box, depth = (distance − 10000)/256. Below 0 the carrier runs aground: superstructure −1000, "Carrier has grounded", and a 50-pass reverse recoil.
9. **Autopilot:** steer to the waypoint at `$656` (≤ `$20` within 2000), slow to `$10` when more than `$100` off the bearing, and stop and announce within 1000.

### 5.2 Carrier systems (`$AF4`, 10 × 8: cur, max, priority class, status)

`carrier_systems_status` (`$17578`, every pass) derives each status word with one of three formulas:
- **A:** efficiency = cur·256/(max/2) − 256, at least 0;
- **B:** `$FF` while cur > max/2, else 0;
- **C:** cur·256/max.

| # | System | Formula | Status | Effect |
|---|---|---|---|---|
| 0 | Engines | A | `$AFA` | speed caps (§5.1) |
| 1 | Radar | B | `$B02` | *(not traced)* |
| 2 | Communications | B | `$B0A` | comms-pod relay beyond 200000 |
| 3 | Observation Turret | B | `$B12` | turret aiming |
| 4 | Laser Cannon | A | `$B1A` | 0 → can't fire; damaged → double heat |
| 5 | Flare Launcher | B | `$B22` | flares |
| 6 | Surface to Surface Missiles | B | `$B2A` | SSM launcher |
| 7 | Lift | B | `$B32` | Manta hangar → deck |
| 8 | Automatic Repair Systems | C | `$B3A` | the repair rate (`carrier_auto_repair`) |
| 9 | Superstructure | C | `$B42` | cur `$B3C` = 0 → game lost |

**Damage distribution** (`$1C188`):
- "Carrier has been damaged!" (500-pass hold-off), and `[$952]` = 2;
- the hit position along the hull is `[$AB2]` (rotated z offset).

| Systems | Always | Bow (> 300) | Stern (< −300) | Midships |
|---|---|---|---|---|
| 8 | dmg/16 | | | |
| 9 | 3·dmg/16 | | | |
| 1–4 | | dmg/32 | dmg/32 | dmg/4 |
| 5, 7 | | dmg/2 | | dmg/16 |
| 0, 6 | | | dmg/2 | dmg/16 |
| vehicle on the lift | | dmg/2 | | dmg/16 |

### 5.3 Carrier weapons

| Weapon | Details |
|---|---|
| **Laser turret** (`$1EDFC`, screen 8) | Aim heading `$6CC` and elevation `$6CA` (mouse or cursor keys; needs Observation Turret). Fires a type-16 bolt with life 75 and cooldown 5. Heat `$6C8`: +`$200` per shot (+`$400` damaged), −16 per pass; above `$3000` it damages system 4 by 100 per shot |
| **SSM** (`$13598`) | Hammerhead (item 2). Needs the recon drone airborne: the target point = drone position + mouse offset scaled by drone altitude, ≥ 2000 away. Fires a type-19 cruise missile astern, cooldown 50 |
| **Decoy flares** | item 10, objects 17/18, reload `$1150`, needs the Flare Launcher |
| **Recon drone** | item 11, object 15, launched astern; the "viewing drone" screen 10 |
| **Defence drones** | item 6, objects 11–14 (§6.6) |

## 6. Mantas and Walruses

### 6.1 Bays

Objects 2+k (Manta k) and 6+k (Walrus k) pair with bay records of `$34` bytes. The object's `+$1A` = k, which is also the digit in "Manta 1 …" messages.

**Status byte `+1`:**
- 0: ready in the hangar;
- 1: under repair;
- 2: flight preparations;
- 3: on the lift / launching;
- 8: out (deck, flying or driving);
- `$FF`: destroyed.

**Manta record** (`$496 + $34k`):

| Offset | Meaning |
|---|---|
| `+0` | flags: bit 0 automatic, 1 steer to waypoint, 2 announce, 4 crashing, 5 take-off/land request *(guess)*, 6 pitch self-centres, 7 rudder self-centres |
| `+2` | rudder ±255 |
| `+4` | pitch ±255 |
| `+6` | speed |
| `+8` | target speed (throttle) |
| `+$A` | **fuel** (max `$7FFF`) |
| `+$C` | Assassins |
| `+$E` | payload code (4 Quaker, `$A` comms pod) |
| `+$10` | Quasar laser fitted (`$FF`) |
| `+$12` | loadout weight (≤ 750; kept by the arming drops) |
| `+$14` | **health** (max 4000) |
| `+$16` | prep timer |
| `+$18` | instrument page 0–2 (display) |
| `+$1A` | selected weapon: 0 laser, 1 missile, 2 payload |
| `+$1C` | flight mode; in the hangar `$FF` = unbalanced wing loads (no "to deck") |
| `+$1D` | sub-state |
| `+$1E/+$26.l` | waypoint |
| `+$22` | landing-pattern height |
| `+$2A` | cruise speed |
| `+$2E` | telemetry: 0 OK, 1 weak, 2 lost |
| `+$30` | countdown |

In the hangar, `+$C/+$E/+$10` hold the three **loadout codes** instead.

**Walrus record** (`$570 + $34k`):

| Offset | Meaning |
|---|---|
| `+0` | flags: bit 0 automatic, 1 waypoint, 2 announce, 3 beached message given, 4 **steering a guided missile**, 5 rudder turns the turret, 6/7 self-centring |
| `+2` | rudder |
| `+4` | speed |
| `+6` | target speed |
| `+8` | **fuel** |
| `+$A` | Avatar shots (40) |
| `+$C` | Harbingers |
| `+$E` | payload code (8 virus bomb, 9 ACCB, `$B` fuel pod); in the hangar, the prep timer |
| `+$10` | ACCB type: `[$DC6]` (0–2, cycled on the arming screen) when the ACCB is fitted (`$11BBA`); the island becomes resource, factory or defence (kind = type + 1) |
| `+$12` | loadout weight (≤ 2000) |
| `+$14` | **health** |
| `+$16` | instrument page 0–2 (display) |
| `+$18` | selected weapon |
| `+$1A` | drive mode |
| `+$1B` | sub-state |
| `+$1C/+$20.l` | waypoint |
| `+$24` | cruise speed |
| `+$26/+$28` | hangar loadout codes |
| `+$2C` | telemetry |
| `+$2E` | missile guidance timer |
| `+$30` | countdown |

**Hangar machine** (every 10 s; `$1761C` Mantas, `$176AC` Walruses):
- **Repair (1):** health += 400/n, where n = the number of bays under repair; at 4000 → preparing.
- **Preparing (2):** counts the prep timer down → ready.
- **Prep time:** (4000 − health)·16/4000, plus a per-loadout-slot amount, plus 1 (`$11856`, `$11F6A`).

**Hangar commands:**

| Command | Routine | Effect |
|---|---|---|
| prepare | `$1182A` | status 2, prep timer set |
| replace a destroyed Manta | `$117D4` | takes one from Manta stock (item 13 on board); full health, **no fuel** |
| refuel | `$1158A` (Walrus `$11D5A`) | from the carrier's aircraft tank `$D00` (Walrus: `$D04`), up to `$71C` per call |
| send to deck | `$11894` | needs the Lift system, a free lift, and no landing in progress |

### 6.2 Loadouts (`$CAC4`, 12 × 26 bytes)

Each entry: `weight.w, stock pointer.l (the item's on-board count), quantity, icon and strings, fit handler.l`. The fit handler (`$1ED46`) converts slot codes into counts and flags at launch. Everything unused goes back to the stores when the vehicle returns.

| Code | Weight | Item | Fit effect | Fired as |
|---|---|---|---|---|
| 1–3 | 60/115/170 | Assassin ×1/2/3 | Manta `+$C` += n | type 17 homing missile |
| 4 | 600 | Quaker | Manta `+$E` = 4 | type 20 bouncing bomb |
| 5 | 560 | Quasar | Manta `+$10` = `$FF` | type 16 laser bolt |
| 6 | 1500 | Avatar | Walrus `+$A` = 40 shots | type 16 laser bolt |
| 7 | 150 | Harbinger | Walrus `+$C` | type 18 guided missile |
| 8 | 200 | virus bomb | Walrus `+$E` = 8 | type 21 |
| 9 | 400 | ACCB | Walrus `+$E` = 9 | places a builder |
| 10 | 300 | comms pod | Manta `+$E` = `$A`, `$DE0` = this unit | telemetry relay |
| 11 | 450 | fuel pod | Walrus `+$E` = `$B` | refuels another Walrus |

### 6.3 Manta flight (`manta_step` `$1CDA2`)

Per Manta with status > 2:
1. **Health 0** → destroyed (`$1DC4C`).
2. **Range:**
   - within 200000 of the carrier, OK;
   - otherwise it needs Communications and a deployed comms pod within 200000;
   - else "telemetry signal weak", and beyond 260000 "telemetry lost": the Manta crashes (falls at −5 per pass, rolling).
3. **Player control:** rudder += mouse x, pitch += mouse y; throttle ±16; fire → `$1F9E8`.
4. **Modes `+$1C`:**

| Mode | Behaviour |
|---|---|
| 0 | auto-landing on the carrier (approach, offset, match heading, drift to the deck, descend → lift) |
| 1 | on deck (tracks the carrier; fuel −12 per 16 passes) |
| 2 | deck take-off (climb off the lift, accelerate +8 to 60 along the deck, pitch up; at the deck end speed += carrier speed) |
| 3 | **free flight** |
| 4 | runway landing on an island (needs a type `$24` runway; align, descend to 100) |
| 5 | landed: **refuels +`$40` per pass** |
| 6 | island take-off → flight, then automatic return to the carrier |

5. **Flight physics:**
   - max speed = min(health/16 + 50, 250); stall at 50; speed approaches the target by +16/−4 per pass;
   - roll = −25·rudder, pitch = −14·pitch input, heading += rudder·16/6;
   - velocity from pitch and heading; altitude capped at 3500;
   - impact at low altitude, unless lined up with a runway: within 200 of the axis, roll ≤ `$78`, pitch ≤ `$50`, heading within 4000, speed ≤ 100 → mode 4.
6. **Fuel**, every 16 passes: −(speed·16)/50, −256 more if health < 501 (leak). At 0: "has run out of fuel" → crash.
7. **Weapons:**
   - laser: type 16, life 20, class 2, cooldown `$940` = 4;
   - Assassin: a two-press lock-on through the sight scan (`$6290`, `$200F6`). It picks the flag-bit-12 object nearest the cross-hair, at ≥ 500, with lock timer `$116C` = 25. Fires from alternating wings ±300 with cooldown 4 or 10;
   - **the lock-on** (first press, lock timer 0; `manta_lock_scan`): `$62B6` (the walk's `jsr` target) = `$200F6`, `$1168` byte = `$FF`, `$1166` = `$7FFF`, then `$6290` walks the depth tree of the **last render** front to back (near side, node, far side: ascending camera Z; of equal Z the later inserted first). Per node (camera-space Z, X, Y; object = `$5DCDA` + the node's word offset): skip unless flags bits 0 and 12 and Z ≥ 500 (signed long); qx = (X << shx) / z, qy = (−Y << shx) / z with `divs.w`, shx = `[[$6FE]+8]`, z = the **low word** of Z (so Z ≥ `$8000` divides by a negative word and mirrors the point; an overflowing quotient skips; a zero low word would trap); skip if \|qx\| or \|qy\| > 256 (word `neg`: −32768 passes); d = max(\|`[$1162]` − qx\|, \|`[$1164]` − qy\|) on signed words; if d ≤ `[$1166]`: `$1166` = d, `$1168` = the object (so of equal d the last visited wins, the farthest). Then lock (`$116C` = 25, a sound unless `$1249`) if `[$1166]` ≤ 24 and `[$1168]` ≥ 0. The sight `$1162/$1164` is written by the screen 27 render (`$1531A`): weapon 1 → (rudder `+2` >> 3, −pitch `+4` >> 3), weapon 0 → (0, 0), others leave it. `$116C` counts down in screen 27's input handler.
     The model rebuilds the draw list from the state (`view_draw_list`, the port of `$7BF6` for camera `[$9FA]`, island partition included; debris shards are left out: their flag word is 0). The original scans the tree of the frame rendered before the pass. Between that render and the scan only the island step, debris, the carrier and lower-numbered Mantas move, none of them lockable, so the two agree whenever the last render was screen 27's 3D view. They can differ on the pass right after entering screen 27 (the tree is the previous screen's), after a frame of static from lost telemetry (`+$2E` = 2: no tree is built), and if the island step changed a lockable entry that pass *(not seen)*.
   - Quaker: "Bomb dropped", a type 20 that inherits the Manta's velocity (≤ 150).

### 6.4 Walrus (`walrus_step` `$1DCFC`)

1. **Range** as for the Manta; beyond 260000 the Walrus is lost.
2. **Control:**
   - rudder += mouse x (or the turret `+$26` when flag bit 5 is set); throttle ±8; fire → `$1FB6E`;
   - when steering a Harbinger (flag bit 4), the mouse steers the missile instead (`+$1E`/`+$20` of the missile object `+$20`). The guidance timer `+$2E` drops by 8 while it's ≥ 100.
3. **Shore:** within 650 of an island's box, a ramp table sets altitude and pitch from the distance, and the Walrus drives up the beach ("beached" message once).
4. **Fuel**, every 16 passes: −(max(speed, 0)·16)/80, −128 more if health < 501. At 0 the Walrus stops (no crash).
5. **Modes:**
   - 0 docking: drive into the cone behind the carrier, match heading, enter, wait for the door, stow (weapons back to stores);
   - 2 launch: reverse out of the dock, turn to the waypoint, add the carrier's speed;
   - 3 driving.
6. **Weapons:**
   - Avatar laser: type 16, class 4, cooldown `$942` = 8;
   - Harbinger: type 18, cooldown 15, steered;
   - virus bomb: type 21, thrown along the turret heading;
   - ACCB (`$1FC10`): on a neutral island, more than 2000 inside its box, place a `$34` builder 1000 behind the Walrus (code = Walrus `+$10` + 1), `difficulty_add($400)`, 2500 points. Trees in the way are cut down, with debris on the loaded island (`$18B50`). The builder then grows in the island step (`docs/simulation.md`): +4 per 100 raw, and at 61 it becomes a command centre, owner (code >> 4) + 1 = the player, kind = code & 3, goods 5000;
   - fuel pod: another Walrus within 500 and facing within 6000 gets a full tank.

### 6.5 Lift and dock

**Manta lift** (`$1EAA0`):
- **State `$DCE`:** 1 lowering, 2 at the bottom, 3 raising. Height `$DD6`: 0 = deck, `$C000` = down, step `$668` per pass.
- **At the bottom:** a returning Manta stows (weapons back to stores, "Manta N returned to hangar"); a launching Manta is created (type 5, on the lift, pitch `$FD4A`, mode 1) and its loadout is fitted.
- **At the top:** status = 8, "Manta N is now on the flight deck".

**Walrus dock door** (`$1ED94`): state `$DDA` (1 opening, 2 open, 3 closing), position `$DD8` (0…`$D000`, step `$5DC`), docked Walrus `$DE6`.

### 6.6 Defence drones (`$1A296`, objects 11–14)

There are 4 station records at `$65E` (14 bytes each) with fixed offsets around the carrier: (0, +3000), (+3000, 0), (0, −3000), (−3000, 0).
- **Launch:** `[$122F]` and index `$69A` run the launch sequence one drone at a time, rising 30 per pass from −600 through the dock door.
- **Loss:** a drone hit by anything except another drone is destroyed ("has been destroyed!").
- **Store:** item 6 "Defence drones".

### 6.7 Stores and fuel: stockpile versus carrier

There are two inventories. This corrects the "carrier stores" wording in `docs/simulation.md`:

| Inventory | Stores | Fuel |
|---|---|---|
| **Stockpile** (at the stockpile island `[$D4A]`; logistics deliver here) | `$D78[15]` | `$D60/$D64/$D68` (×`$1000` units) |
| **On board the carrier** (what launches and loadouts use) | item records `+8` (`$B76 + $1A·k + 8`; capacity `+$18`) | ship `$CFC`, aircraft `$D00`, AAV `$D04` |

The Resources screen (`$10CAE–$10D26`) queues a transfer into `$D96` (fuel `$D6C`), limited by the carrier's free capacity. The transfer state machine (`$1F316`: `$D3E` active, `$D42` state from a 10-entry table at `$1F346`, `$D44` timer) then flies the **supply drone** (object 16) to carry it.

*(The arrival step that adds to the on-board counts hasn't been traced.)*

## 7. AI objects (`ai_objects_step` `$1A56A`)

Objects 20…63 with flag bits 0 and 8 call `$1A5B6[type]` (a4 = object, `[$6F0]` = index):

| Type | Handler | Behaviour | Launched by |
|---|---|---|---|
| 16 laser bolt | `$1A6A0` | straight line at 350 per pass for `+$1B` passes; removed below sea level nose-down | Manta, Walrus, carrier turret |
| 17 homing missile | `$1A6D0` | 5 passes straight, then homes on `+$1E` (turn ≤ `$800` per pass, speed 250, life 100). Every 5 passes a **decoy flare within 6000 steals the target**. A dead target means 100 passes straight | Manta (Assassin), enemy fighters |
| 18 guided missile | `$1A886` | yaw/pitch steered by the launching Walrus each pass, speed 150, life 120; explodes low | Walrus (Harbinger) |
| 19 cruise missile | `$1A8F6` | flies to the point `+$1E/+$22` at altitude 1000, dropping to 140 within 3000, speed 200, life 255; within 200 → `explode_blast` | carrier SSM, enemy carrier state 10 |
| 20 bomb | `$1A9B0` | gravity −10 per pass (terminal −140), tumbles, **bounces** on the island top (vy = −¾vy) until the rebound < 30, then explodes; over the sea it splashes | Manta (Quaker) |
| 21 virus bomb | `$1AA52` | tumbles, 100 per pass on its heading, life 50; removed when low over an island | Walrus |
| 23 blast | `$1A9A6` | a 5-pass, 2000 × 500 collision volume of class 3 (area damage) | `explode_blast` `$1C5EE` |
| 33 enemy fighter | `$1AC00` | below | enemy carrier |
| 34 anti-ship missile | `$1AABA` | holds altitude 800, then 75 (sea-skimming) after leaving the island. First exit: **"Missile heading for Carrier!"**. Retargets every 20 passes: the nearest of objects 10–14, or the enemy carrier when fired by a player island. Speed 180, life 400 | anti-ship sites (§8) |

**Enemy fighter** (type 33). Fields: `+$1A` state, `+$16` fuel 2000, `+$18` missiles 10, `+$38` health 5000, waypoint `+$22/$26/$2A`.

| State | Behaviour |
|---|---|
| 1 fly to waypoint | fuel −1 per pass, turn rates `$5DC`/`$800`, speed 220. It goes home on recall `[$11D0]`, fuel < 200, health ≤ 1000 or no missiles |
| 2 search | a player unit within 20000 (Walrus +5000 bias, carrier −5000) → attack. Otherwise a bit-14 structure on a player island; otherwise a random waypoint (±7000, altitude 500–2547) |
| 3 attack | gives up beyond 40000. Within 5000 and on heading (±`$100`) it fires a type 17, then breaks off for 100 passes |
| 4/5 return and land | lands on the enemy carrier: removed, airborne count −1, `[$11CA]` aircraft +1 |

## 8. Islands in combat

### 8.1 Island behaviours (`island_behaviours_step` `$1962A` → `$19648`)

For each loaded buffer it sets the context:
- `$A28` buffer, `$A34` island;
- `$A44` player-owned, `$A45` neutral, `$A43` the viewed unit is here;
- `$A42` **a hostile is present**: player units 2–10 attached here for an enemy island, objects 19–23 for a player island.

It then runs `$19778[type]` for entries with flag bit 8.
- **Viewer rule:** entries with flag bit 9 run only while the *viewed* unit is at the island. That covers drone pads, AA, SAM launchers, command centres and the volcano; anti-ship sites and missiles in flight are exempt. So most island defences act only while you're watching that island *(to confirm in Hatari)*.
- **Neutral islands are inert.** Defences on player islands target the enemy's fighters and carrier.

| Type | Handler | Behaviour |
|---|---|---|
| 7 volcano | `$1989C` | eruption cycles; type-9 rocks (`$1996C`, ballistic) |
| 14, 15 | `$19862`/`$19872` | rotating dishes (cosmetic) |
| `$25` drone pad | `$19F00` | every 100 passes while hostiles are present and drones out < `[$446]`: launch a **type 22** drone (ammo `[$448]`, health `[$44E]`, fuel 1000) |
| 22 drone | `$19C46` | the fighter state machine on island-local coordinates. Speed `[$44A]`, turn `[$44C]`, fires type 28 within 5000, returns to its pad |
| `$26` AA turret | `$199B4` | every 8–15 passes picks a hostile within 8000 at altitude 60–2300, tracks it, and when aimed fires a **type 28** every 32–63 passes |
| `$27` SAM launcher | `$19AC4` | every 64–95 passes: a hostile within 7000 at altitude 250–2300 → vertical-launch type 28 |
| `$28` anti-ship site | `$19F8A` | every 128–255 passes: the opposing carrier in this buffer and within `[$454]` → type 34 |
| `$29` command centre | `$19B44` | only at difficulty ≥ `$4000`: a hostile within 4000 below altitude 1500 → type 28 |
| 28 SAM | `$19BC6` | re-aims every 4 passes; turn `[$452]`, speed +5 per pass up to `[$450]` |

### 8.2 Difficulty parameters (`difficulty_apply` `$190FE`, d = `[$444]`)

| Address | Formula | d = 0 | Used for |
|---|---|---|---|
| `$446` | 2, or 3 if d ≥ `$4000` | 2 | max drones out per island |
| `$448` | 3 + (d ≥ `$8000` ? (d − `$8000`)/10000 : 0) | 3 | drone missiles |
| `$44A` | 150 + 1.5·(d >> 10) | 150 | drone speed |
| `$44C` | ¾·((d >> 6) + `$400`) | `$300` | drone turn rate |
| `$44E` | (d >> 4) + `$200` | `$200` | drone health; command-centre health = `$44E` + `$800` |
| `$450` | (d >> 10) + 200 | 200 | SAM max speed |
| `$452` | (d >> 6) + `$400` | `$400` | SAM turn rate |
| `$454` | max(d, `$4000`) | `$4000` | anti-ship range; enemy-carrier engage offset |

**Raising d:**
- destroying enemy buildings (table below);
- destroying an enemy command centre (+`$800`);
- dropping an ACCB (+`$400`);
- saving an action game (+`$2000`).

### 8.3 Buildings, command centres and the virus bomb

**Buildings** (`$1C3D6…`): health `+$38` −= dmg and is **written back to the island slot** (`$1C69C`), so the strategic layer sees it. At ≤ 0 the slot is cleared. On enemy islands it also gives points and difficulty:

| Type | `$25` | `$26` | `$27` | `$28` | `$2A` | `$2B` | `$2C` | `$2D` | `$2E` |
|---|---|---|---|---|---|---|---|---|---|
| Points | 2500 | 1000 | 1000 | 2000 | 1000 | 1000 | 2000 | 2000 | 1500 |
| Difficulty | `$400` | 0 | 0 | `$200` | `$100` | `$100` | `$100` | `$100` | `$100` |

**Command centre** (`$1C324`):
- Virus bombs do nothing, and lasers do dmg/8.
- At ≤ 0 the island turns **neutral**. If it was enemy-held: 5000 points, `difficulty_add($800)`, and `[$D52]` +1.
- If it was the stockpile island, the stockpile is lost (`stores_clear`).
- Then `network_invalidate`.

**Virus bomb** (`$1C512`): touching a command centre within ±10000 of its facing (from the front) makes the island **player-owned**:
- 10000 points, "X is now a friendly island", `network_invalidate`;
- if the island was already the player's: "Friendly control centre bombed!".

## 9. Enemy close-range combat (states 10 and 11)

`enemy_engage` (`$1B250`, `docs/simulation.md`) puts the carrier beside its island and sets state 10.

**State 10** (`$1B2D2`):

| Condition | Action |
|---|---|
| no player unit within 270000 | back to the strategic resolution of the island (`$1B18E`) |
| strength < 3000 or reserve 0 | → state 11, recall fighters (`[$11D0]`) |
| player carrier within 30000 | turn ≤ 200 per pass to put the **stern towards the player**. Aligned within `$800`, with reserve and the cooldown `$11D4` at 0: fire a **type 19** cruise missile astern at the carrier's current position (reserve −1, cooldown 200) |
| otherwise | turn back to the engage heading `+$1E` |
| fighters | aircraft > 0, cooldown `$11CC` = 0, airborne `$11CE` < 4 and a free object 20–23 → launch a **type 33** (aircraft −1, cooldown 30) |

**Bug:** `$11CE` (airborne fighters) is decremented when one lands or is lost but never incremented. The limit of 4 never applies, and the count goes negative until it's reset.

**State 11** (`$1B49E`): retreat. Turn to `+$1E`, accelerate (`$11D6` +2 per pass, up to 250) and sail away. Once no player unit is near: state 1, hidden.

**Damage** to the enemy comes from any hit (class damage straight off strength, §4.3) and from the strategic bombardment's return fire.

**Destruction.** Either way, at strength ≤ 0 (hit, `$1C0F4`) or < 0 (return fire, `$1B728`): the AI is switched off (`[$464]` = 0; only the new game sets it again), the end timer `[$732]` = 100 and "Enemy Carrier destroyed!!!!" shows. A hit also clears object 19 (debris, 4 × 25000 points); after return fire it stays hidden where it was. The enemy state is left as it was (3, 10 or 11) and never runs again; fighters still airborne fly home to object 19's last position (a hit clears only its flags) and land there as usual, adding to an aircraft count nothing reads any more. 100 passes later `game_frame` requests screen 35. **YES** goes back to the previous tab and the game runs on without the enemy carrier: enemy islands keep their defences, builders and the NEMESIS resupply, and the game ends by the island counts (64 → result 3) or the player carrier's loss. **NO** sets result 4 and goes to the game-over screen. Verified end to end by `check_enemy_destroyed` (`stunits_enemy.py`).

## 10. Screens and controls

**Screens:** `[$718]` = the current screen (0–35), with 8 tabs (`$714`, last screen per tab in `$720`). Per-screen tables:
- render `$144A0`;
- input `$170D4` (§10.3);
- panels `$43856` (§10.4);
- enter `$139D4`;
- leave `$14318` (§10.5).

| Tab | Screens |
|---|---|
| Carrier | 0 bridge 3D, 1 damage status / set priorities, 2 map (waypoint), 3 resources (stores, priorities, transfers), 4 messages |
| Weapons | 8 laser turret, 9 decoy flares, 10 viewing drone, 11 defence drones radar, 12 weapons status |
| Walrus | 16 arming, 17 map, 18 control, 19 dock 3D, 20 status |
| Manta | 24 arming, 25 map, 26 deck 3D, 27 control, 28 status |
| — (tabs 5–7) | 32 game status (save, format, options, surrender; skips the simulation), 33 title, 34 game over, 35 enemy carrier destroyed ("continue?") |

**What the simulation reads:**

| Unit | Variables |
|---|---|
| Carrier | throttle `$646` (slider (187 − y)·4); chevrons `$88A/$88B`; autopilot `$640` bit 0; course `$640` bit 1, waypoint `$64E/$652`, cruise `$656`; rudder from mouse-steer |
| Manta (selected `$48C`, record `$48E`) | throttle `+8` (slider (195 − y)·7, 0–250); weapon `+$1A`; flags `+0`; mode `+$1C` |
| Walrus (selected `$566`, record `$568`) | throttle `+6` (slider (195 − y)·`$2AA` >> 8, 0–96); weapon `+$18`; flags `+0`; mode `+$1A` |
| Laser turret | cursor keys or icons; fire `$88C` or a click; zoom Insert / Clr-Home |
| Carrier systems | repair class `$AF4 + 8k + 4` (0–2), set on the damage screen |

### 10.1 Input path

**Interrupts** (IKBD `$17D80`):
- mouse deltas add to `$1268/$126A`;
- buttons: `$880` left, `$882` right, each a word (held, new) set to `$0101` on a press;
- keys: `$798 + 2·scancode` (held, new); the character (table `$1280`) goes to `$1270`.

**`ui_frame` (`$F636`, every pass, before the simulation):**
1. A forced tab request `[$1227]`/`[$1228]` is taken first.
2. `$17EEA` turns the left button into `$45A`: 2 for a new press, 1 for auto-repeat once held 6 passes, else 0.
3. **Pointer mode** (`$457` set) only: the hit test `$B46E` walks the screen's panels (`$71C`). The first element under the pointer `$474/$476` that is shown and enabled gets the click. It needs:
   - the panel's state word to match (e.g. `[$702]` = 0, the carrier map's `[$710]`, the damage screen's `[$B62]`/`[$B64]`, the map's `[$766]`);
   - its type's flags (`$40EB2` + 2·type) without bit 15 (hidden) or 14 (disabled);
   - while paused (`[$462]` = 0), bit 12. Only PAUSE (type 99) has it.
4. The handler at element `+$12` is jumped to with:
   - d0/d1 = pointer x/y;
   - d2 = `[$45A]`;
   - a3 = the element;
   - a5 = past the panel's state address.

   Handlers that begin `cmpi.w #2,d2; bne $F6E8` act on a fresh press only. The others repeat while the button is held.
5. Pending tab `$716` / screen `$71A` (set by the left/right icon columns `$F844`/`$F880`; `$71A` = the current screen re-enters it): leave `$14302`, `[$718]`, `$139AE` (zeroes the view pointers `$9FA/$9FE/$A02`, `$122D`), enter, then `$71C` = the panel list.
6. **Drag** (`$F73C`): modal mode `$460` with a drop callback `$468`, run on release with the pointer in d0/d1.

**Mouse-steer** (`$457` clear): no hit test. A left click is the vehicle's fire button (`$881`), and the deltas steer the viewed unit and are cleared after use. The right button toggles `$457` on screens 0, 8, 18 and 27 (on 0 and 27 not under autopilot, on 18 not while steering a guided missile).

**Element flags** in `$40EB2`: bit 15 hidden, 14 disabled, 13 highlight, 12 usable while paused, 5 selected. Several buttons share a type. The auto button (type 90, `$40F66`) is the carrier, Manta and Walrus autopilot alike, and each control screen's input handler keeps its selected bit equal to its unit's auto bit.

### 10.2 The loop around the simulation (`$408A`) and game flow

| Step | Address | What it writes |
|---|---|---|
| keys | `$4194` | Control-Q → resume `$FD98`; Control-S → pause `$FD84`; Alt-Help → "Yes, I have kicked out the OS" |
| paused | `$44EE` | while `[$462]` = 0. Typing **GROW OLD ALONG WITH ME** (stored inverted at `$1172`, progress `$116E`) sets debug mode `$1189` and resumes. In debug mode: keypad 1 refuels every Manta/Walrus, keypad 2 heals them (`$FA0`), keypad 3 moves units and the carrier with a course to their waypoints, Control + F-key dumps the screen to disk |
| debug | `$46E8` | debug mode: keypad + / − switch `$118A`; keypad 9 shows the difficulty |
| simulation | `$40B0–$413E` | skipped while paused or while `[$461]` is set (screens 32–35). `$8FE` is cleared, then set on passes that run the logistics tick (nothing reads it) |
| end timer | `$4158` | `$732` counts down. It is set to 100 when the enemy carrier is destroyed (`$1B72C`, `$1C154`). At 0: `$730` = `[$714]`, tab 7 is requested (screen 35: YES `$13990` goes back to tab `[$730]`; NO `$1399C` sets `$466` = 4 and goes to 34) |
| end request | `$417A` | `[$463]` is cleared and tab 6 is requested (screen 34: game over, result `$466`) |

**Pause** (`$FD66`, the PAUSE button, fresh press, not while `$461` is set) toggles between:
- **pause** `$FD84`: PAUSE selected (`$40F78` bit 5), `$462` = 0, `$1231` = 0 (palette cycling), sound off, `$1270` = `$FF`;
- **resume** `$FD98`: `$462` = `$1231` = `$FF`, the key table `$798–$883`, `$888`, `$1270`, the mouse deltas and `$880–$883` are cleared.

The game clock (VBL `$17BC8`) runs only while `$462` is set and `$461` is clear.

**End of game:** `$463` is set by:
- `carrier_step` (superstructure 0, `$466` = 1);
- the island counts at `$40B0` (enemy 64 → 2, player 64 → 3);
- surrender on the game-status screen `$101D8` (`$466` = 0).

### 10.3 Per-pass input handlers (`$170D4`, run by `screen_input` `$17094`)

After the handler, `$17094` clears these new flags: right `$883`, 1–4 `$79D–$7A3`, keypad 1–4 `$873/$875/$877/$86D`, Insert `$83D` and Clr-Home `$827`.

| Handler | Screens | Each pass | Port |
|---|---|---|---|
| `$170D2` | 4–7, 9, 11–15, 20–23, 28–35 | `rts` | ✔ |
| `$17164` | 0 bridge | gauge timers `$954/$956/$958/$962` count down and redraw. The auto button follows `$640` bit 0 (`$12C16`). Autopilot off: a right click toggles `$457`. Insert / Clr-Home zoom the view (`$8B4`, `$8B2`; display only). Control-V → "Atari Bundle Version 16/02/90" | ✔ |
| `$1747A` | 1 damage | spins the diagram (`$64D16`, display). With the lift shown (`[$B58]` = 8) it cycles `$B5A` 0–7 (part highlight). `$952` counts down and redraws | ✔ |
| `$17244` | 2, 17, 25 maps | held cursor keys scroll (`$FAB0/$FAC8/$FA84/$FA6C`), held Insert / Clr-Home zoom (`$F984/$F99A`); with `[$766]` = 0 a right click recentres (`$F8B4`). On 17/25 the number keys select a unit (`$132D6` → `$FFB8`/`$FF1E`) | ✔ |
| `$177DE` | 3 resources | guards the sub-mode `[$D14]`: modes ≥ 2 need comms (`$B0A`). Modes ≥ 4 also need no transfer running (`$D3E`) and the carrier at the stockpile island. Otherwise it shows the message, sets `$D14` = 0 and reselects the tab (`$1227`/`$1228`) | ✔ |
| `$1741A` | 8 laser turret | `$968` gauge; held cursor keys aim (`$FD3C` up, `$FD44` down, `$FD2C` left, `$FD34` right); Insert / Clr-Home zoom (`$FC9C/$FCAC`); a right click toggles `$457` | ✔ |
| `$1778E` | 10 viewing drone | drone (object 15) not out: disarms. Otherwise a right click toggles **SSM armed `$1160`**: arming sets the pointer sprite and clips the pointer to the picture (`$46E–$47E`, UI); disarming runs `$F712` (also ends modal mode `$460`). The drone itself can't be steered: it falls on its own (`flares_step`) | ✔ |
| `$17748` | 16 Walrus arming | number keys → `$11DEE`; hangar animation `$DC8` (± `$DCA`, bouncing between `$6000` and `$A000`) | ✔ |
| `$17374` | 18 Walrus control | number keys → `$10016`. Re-enters (`$71A` = 18) when `[$70E]` ≠ (status ≥ 8), and then stops. Gauges `$954/$958/$962`. In auto, the auto button follows bit 0 (`$14004` when it isn't lit); in manual, a right click toggles `$457`, but while steering a guided missile (bit 4) it is only consumed and the handler returns (no icon sync); then a lit auto button is cleared (`$14004`) | ✔ |
| `$17782` | 19 Walrus dock | number keys → `$1249E` | ✔ |
| `$17610` | 24 Manta arming | number keys → `$116C6` | ✔ |
| `$1773C` | 26 Manta deck | number keys → `$11F92` | ✔ |
| `$172BC` | 27 Manta control | **`$116C` (missile lock timer) −1**: simulation state, counted only here. Number keys → `$FE54`. Re-enters when `[$70C]` ≠ (status ≥ 8). Gauges `$954/$956/$958/$962`; auto button / right click as on 18 (no missile exception) | ✔ |

`$132D6` (number keys) works only in pointer mode outside a drag. It takes the first of 1, keypad 1, 2, keypad 2, … whose new flag is set (clearing only that one) and calls the select handler with d2 = 2 and a fake element at `$13352` (type = base + i, `+2` = i) that it writes into the code. It bypasses the hit test, so it works whatever the icons' flags say.

### 10.4 Panel click handlers

F = fresh press only, R = repeats while held. Port: ✔ ported, ✘ not yet. The left/right icon columns (`$F844`, `$F880`), PAUSE (`$FD66`, ✔) and the no-op `$B068` are on every screen.

**Carrier** (all in `stunits_controls.py`, §10.6):

| Scr | (x, y) | Handler | F/R | Does |
|---|---|---|---|---|
| 0 | 288,159 slider | `$FE3C` | R | autopilot off: `$646` = (187 − y)·4, 112 to −32 |
| 0 | 298,155 / 298,178 | `$FDCE` / `$FDD4` | R | `$88A` / `$88B` (target = speed ± 1 in `carrier_step`) |
| 0 | 78,155 | `$12A26` | R | autopilot off: `$646` = 0 |
| 0 | 100,155 | `$12A16` | R | autopilot off: `$640` bit 7 (rudder self-centres) |
| 0 | 100,178 (type 90) | `$12BAC` | F | needs `[$641]` = 1: auto selected → off; else with a course (`$640` bit 1) on, "Autopilot on"; else "No course set" |
| 0 | 56,155 / 56,178 | `$FCE8` / `$FCF8` | F | view zoom `$8B4` 0–4, `$8B2` = `$2E8` >> zoom (the renderer's; also Insert / Clr-Home in `$17164`) |
| 1 | 13…265,155 (types 48–57) | `$FB3A` | F | show system k: `$B58` (`$B5A` = 0 for the lift), part highlights |
| 1 | 293,155 | `$FBBE` | F | priorities panel: classes → work table `$B44`, `$B62` = `$B64` = 1 |
| 1 | grid 200,16 (84×100) | `$FC64` | F | `$B44`[(y − 16)/10] = (x − 200)/28 |
| 1 | CANCEL / EXIT | `$FC08` / `$FC24` | F | reset the work table / write it to the classes, `$B62` = `$B64` = 0 |
| 2/17/25 | map 32,6 (256×128) | `$F8D8` | R | cursor = map point under the pointer (`$F91E`): Manta `$69E/$6A6`, Walrus `$6B0/$6B4`, carrier `$6BE/$6C2` (if `[$710]` = 0) |
| 2/17/25 | 100…122,155/178 | `$FAB0 $FA6C $FAC8 $FA84` | R | scroll `$75C/$760` by zoom·`$400` (±`$7FFFFF`) |
| 2/17/25 | 144,155 / 144,178 | `$F984` / `$F99A` | R | zoom level `$758` −1 / +1 (0–10), `$75A` = 1 << level |
| 2/17/25 | 166,155 | `$F9BE` | R | centre on the selected unit if out, else the carrier; carrier map mode 1: the stockpile island `[$D4A]` |
| 2/17/25 | 166,178 / EXIT | `$FAF4` / `$FB1A` | F | island info box `$766` (centre on `$742/$746`) / close |
| 17/25 | 188,155 | `$FA50` | | centre on the carrier |
| 2 | 13,178 `[$710]`=0 | `$131A8` | F+R | fresh: course = cursor, cruise `$656` = `$6BC`, `$640` bit 1, bit 2 = `$6C6`: "Carrier course set" (autopilot on) or "… - engage autopilot". Held: autopilot on, "Carrier autopilot engaged" |
| 2 | 57,178 | `$1326C` | F | `$640` bit 1 off: "Carrier course cleared" / "Carrier has no course to clear" |
| 2 | 13,155 | `$1336E` | F | `$710` ^= 1 (course panel / islands panel), re-enter |
| 2 | 298,155 / 298,178 / 288,159 | `$12E7A $12E8C $12E9E` | R | cruise `$6BC` ± 8 / slider (187 − y)·4, 0–`$70` |
| 2 | 210,155 (type 92) | `$12E44` | R | `$6C6` = not `$6C6` (report arrival) |
| 2 | 13,178 `[$710]`=1 | `$137C2` | F | **set the stockpile island** to `[$764]` (`$D4E` = old, `$D4A`, `$21D46` re-ships goods). Refused while re-routing (`$1235`), without comms, with stocks in transit (`$D50`, unless `$D4A` < 0), off an island, on the stockpile already, on a non-player island (owner ≠ 1) or one with no route from Vulcan (`$67B40 + 2i` < 0); else "NAME is now stockpile island" (logged). `$21D46` adds the queues back to the stockpile counts and ships all of it from `[$D4E]` as carrier-pool entries from `$66A10` on (unchecked; ≤ 255 each; fuel as `>> 12`, low word, kinds 15–17), clears the counts and sets `$D50` (✔) |
| 4 | 144,155 | `$12C2C` | F | clear the message log (`$A1E8`: `$65D10`, 16 × `$30`, list head `$1148`): "Message log cleared" (`$966` = 2) or "Message log is already clear". The log holds the messages shown through `$A128`; the model leaves it to the app |

**Vehicle control** (all ported, `stunits_controls.py` §10.6; the hangar commands marked ✔ in `stunits_vehicles.py`). Manta record `[$48E]`, object `[$492]`; Walrus record `[$568]`, object `[$56C]`. "Manual" = record `+0` bit 0 clear. Messages "Manta N …" / "Walrus N …" are prefixed by `$A044` / `$A02A`.

| Scr | (x, y) | Handler | F/R | Does |
|---|---|---|---|---|
| 27 / 18 | icons 12…78,155 (types 58–61 / 70–73); number keys | `$FE54` / `$10016` | F | select in place (icon already lit: nothing): old icon off, the old object `[$492]`/`[$56C]` visible again; `$48C`/`$566` = i, records (`$141FE`/`$14224`); `$9FE` = `$9FA` = the new object (made invisible; on 18 too, unlike enter 18), `$A14` = 0 / 1; `$13EBA`/`$13F5A` (`$8F1` = 0, `$70C`/`$70E`, `$A02`, icons, gauges) |
| 27 / 18 | 12/34/56,178 (62–64) | `$FEE2` / `$FF7C` | F | unless that icon is lit: weapon `+$1A` / `+$18` = 0–2, icons (`$13FBA` / `$14004`); no controllability check |
| 27 | 78,178 (type 90) | `$12A44` | F | needs `$70C`, not crashing (bit 4). Auto icon off → on: bit 0, mode `+$1C` = 3, "Autopilot on" (waypoint bit 1) or "Autopilot on - set course". On: mode 2 or 6 → only `+$33` = `$FF`; mode 3 → off (icon, bit 0); other modes nothing. Then `+0` &= `$3F`, icons |
| 18 | 78,178 (type 90) | `$12B00` | F | needs `$70E`. Off → on needs bit 1 ("No course set"): bit 0, mode `+$1A` = 3, "Autopilot on". On: mode 2 → `+$33` = `$FF`; mode 3 → off. Then `+0` &= `$5F` (turret mode off), icons, turret icon |
| 27 | 100,155 / 100,178 | `$129E6` / `$129FE` | F | manual: `+0` bit 7 / bit 6 (rudder / pitch self-centre) |
| 18 | 100,155 | `$12982` | F+R | manual: fresh → bit 7; repeat → bit 6 and rudder `+2` = 0 |
| 18 | 100,178 (type 80) | `$129A2` | F | `$70E` and manual: toggle bit 5 (the mouse turns the turret); turning it off clears bit 6; rudder = 0, bit 7 cleared, icon 80 |
| 27 / 18 | 123,155 | `$100A8` / `$100D4` | F | instrument page `+$18` / `+$16` = (+1) mod 3 (display) |
| 27 / 18 | 288,158…197 slider | `$FDDA` / `$FE0A` | R | manual: throttle `+8` = (195 − y)·7, 0–250 / `+6` = (195 − y)·`$2AA` asr 8, 0–96 |
| 27 / 18 | 298,155 (type 104) | `$10100` / `$1012E` | F | controllable: `not +$32` (sight overlay *(guess)*), icons |
| 27 / 18 | 298,178 (type 105) | `$1015C` / `$10190` | F | controllable: `not $8F1` (rear view; no firing), icon 105 |
| 27 | 135,117 | `$13356` | R | auto in mode 5 (landed on a runway): take-off request bit 5 |
| 26 / 19 | icons (86–89 / 93–96); number keys | `$11F92` / `$1249E` | F | deck / dock selection `$DCC` / `$DE4` (records and objects repointed, `$48C`/`$566` kept), icons, `$95C` = 2 |
| 26 | 56,178 / 12,178 / 100,155 / 100,178 | `$11894 $120F4 $12002 $1242A` | F | to deck, land, take off, to hangar (✔ `stunits_vehicles`) |
| 26 | 144,155 | `$12360` | F | abort the landing of `[$DDC]`: "No aircraft is currently landing" (`$1232` = 0), "Too late to abort landing" (`+$1D` > 3); else `$1232` = 0, mode 3, step 0, without a waypoint bits 1/2 and `$1207A` (cruise 60, height (bay + 1)·300 + 500, 6000 ahead), "Manta N - Landing aborted" |
| 19 | 100,155 / 12,178 | `$1250E` / `$127C4` | F | launch, dock (✔ `stunits_vehicles`) |
| 19 | 144,155 | `$128AE` | F | abort the docking of `[$6E0]`: "No AAV is currently docking" (`$1233` = 0 or mode ≠ 0), "Too late to abort docking" (step ≠ 0); else `$1233` = 0, mode 3, without a waypoint `$12786` (cruise 60, astern by the bay's lane), "Walrus N - Docking aborted" |
| 25 / 17 | icons (58–61 / 70–73); number keys | `$FF1E` / `$FFB8` | F | nothing while `[$766]`; select (`$48C`/`$566`, records); icon already lit → recall the course (`$12ECC` / `$12F30`): waypoint, cruise and announce into the edit values, the map centred on it at zoom level 0; "course recalled" / "has no course to recall" / "no longer exists" |
| 25 / 17 | 13,178 | `$12FC4` / `$13122` | F+R | fresh: course from the edit values (Manta `$69C` cruise, `$69E/$6A2/$6A6`, `$6AC`; Walrus `$6AE`, `$6B0/$6B4`, `$6BA`), "course set" (auto) / "course set - engage autopilot" / "no longer exists"; held: a unit with a course and not automatic gets bit 0 only (its mode isn't set), "autopilot engaged" |
| 25 / 17 | 57,178 | `$12F8E` / `$130EE` | F | clear course: "course cleared" / "course already clear" / "no longer exists" |
| 25 | 298,155/178, 288,159 slider | `$12C90 $12CA2 $12CB4` | R | edit cruise `$69C` ±16 / (195 − y)·7, 50–250 |
| 25 | 254,155/178, 244,159 slider | `$12CE2 $12CF6 $12D0A` | R | edit altitude `$6A2` (long) ±230 / (195 − y)·97, 250–3500 |
| 17 | 298,155/178, 288,159 slider | `$12D98 $12DAA $12DBC` | R | edit cruise `$6AE` ±8 / (195 − y)·`$2AA` **lsr** 8, 0–96 |
| 25 / 17 | 188,178 (91) / 210,155 (92) | `$12D40 $12D50` / `$12DEC $12DFC` | R | toggle the map draw mode `$6AA`/`$6B8` (bit 0 of the low byte; display) / `not` the announce flag `$6AC`/`$6BA`; icons 91/92 |

All the map handlers do nothing while the island info box is up (`[$766]` ≠ 0).

**Arming** (screens 24 / 16; selection `$DB4` / `$DB6`, sub-panel `$DC0` / `$DC2`; the loadout elements need sub-panel 0):

| (x, y) | Handler | F/R | Does |
|---|---|---|---|
| icons (74–77 / 82–85); number keys | `$116C6` / `$11DEE` | F | select the bay (another than the current): icons, record `$48E`/`$568`, sub-panel (`$11738`/`$11E60`), its panel redrawn, `$95C` = 2 |
| 232,155 / 298,155 / 56,178 | `$1158A $11676 $1176A` / `$11D4C $11DA4 $11E8A` | F | refuel, defuel, repair / replace (✔ `stunits_vehicles`) |
| 12,178 (type 78) | `$11886` | F | Manta to deck: the body of `$11894` with d1 = `[$DB4]` |
| 181,80 (type 79) | `$1182A` / `$11F3E` | F | prepare ("CANCEL REPAIRS", ✔) |
| 142,84 / 142,108 | `$1132E $11352` / `$11A30 $11A76` | R | list cursor `$DB8` (0–5: codes `$C770` = 1, 2, 3, 4, 5, `$A`) / `$DBA` (0–4: `$C242` = 6, 7, 8, 9, `$B`) ± 1, unsigned wrap. Walrus on the Harbinger: `$CB80` = 1 if `[$B98]` else 0 |
| 123,85 / 35,86 | `$1135E` / `$11A82` | F | pick up the item at the cursor ("Stores unable to supply payload" if its stock `[entry +2]` is below its quantity `+6`, Walrus also when 0) and start a drag (drop `$113A4` / `$11ACE`) |
| slots 218,70 · 195,90 · 240,90 / 173,96 · 201,96 | `$114C8` / `$11C16` | F | pick up a fitted item (slot not empty): `$DBC` = the slot element, drag (drop `$11500` / `$11C4E`) |
| 43,86 / 114,86 (type 81) | `[$42832]` / `[$4281C]` | R | the −/+ buttons call whatever the Walrus panel draw installed: `$11D0C`/`$11CE0` Harbingers to load `$CB80` −1 (≥ 1) / +1 (≤ stock `$B98`, ≤ 9), or with the list on the ACCB `$11D40`/`$11D1C` its building type `$DC6` −1 / +1 (0–2; `+1` resets only at exactly 3) |

**Drops** (`$F6EA`: when the button is released during a drag, `ui_frame` ends modal mode `$F712`, re-runs the hit test with `$46C` = 0 so that it only marks the element under the pointer, then `jmp [$468]` with d0/d1 = the pointer):
- `$113A4` Manta: the first slot element whose box (`$B52C`, unsigned) holds the pointer: slot 0 fuselage `+$10`, 1/2 wings `+$C`/`+$E` (`$11448`). Wings take codes ≤ 2 only ("Payload cannot be wing mounted"). Weight `+$12` − old item + new ≤ 750 (signed), else "Payload capacity exceeded". The old item's quantity goes back to its stock, the code is written, the new quantity is taken from stock, panel redrawn.
- `$11ACE` Walrus: slot 0 (`+$26`) takes entries with `+8` = 1 (virus bomb, ACCB, fuel pod), slot 1 (`+$28`) the others (Avatar, Harbinger), else "Payload cannot be mounted here". An old Harbinger load is valued through a pseudo-entry in the code at `$11BD2` (weight 100 + 150·`+$C`, stock pointer `$B98`, quantity `+$C`), and `+$C` is cleared. Weight ≤ 2000. New Harbingers: `+$C` = `[$CB80]`, then `$CB80` = min(itself, the stock left). ACCB: `+$10` = `[$DC6]`.
- `$11500` / `$11C4E`: in the stores box (x `$21`–`$A2`, y `$4B`–`$84`): the slot cleared, its quantity back to stock, its weight off `+$12`, "Payload returned to stores". A Harbinger load returns `+$C` to `$B98` but `+$C` itself stays.

**Panel callbacks that write game state** (run by the redraws `$B418` above, by the hangar refuel/defuel (`$115CC`, Walrus `$11D92`), and by the screen switch's panel drawing `$782A`, only while their sub-panel is 0; modelled as `_manta_panel` / `_walrus_panel`):
- Manta `$42966` → `$C580` → `$C640`: `+$1C` of `[$48E]` = `$FF` (unbalanced, blocks "to deck") when the wing loads' weights differ by more than 80.
- Walrus `$427B6` → `$BFE8`: with the list on the Harbinger, the Harbinger entry's weight `$CB7A` = 100 + 150·`[$CB80]` and the ± handlers `$4281C`/`$42832` = `$11CE0`/`$11D0C`; on the ACCB `$11D1C`/`$11D40`; type 81's flag = 0 for those two codes, else `$8000` (hidden).

**Resources** (screen 3, sub-mode `$D14`; all ported, type 38, each panel shown only for its sub-mode). Store items `$B76 + $1A·i` (15): `+$10`/`+$12` class and target, `+$14`/`+$16` their edit copies, `+8` on board, `+$18` capacity:

| `$D14` | (x, y) | Handler | F/R | Does |
|---|---|---|---|---|
| 0 | 12,155 | `$1102E` | F | abort the resupply: `$D42` = `$D46` = 2, `$D40` = 0, "Resupply aborted - drone recalled" (logged); refused: no transfer (`$D3E`), `$D46` = 1 "already completed", 2 "has been aborted", `$D40` with `$DE6` = 4 "Too late to abort resupply" |
| 0 | 34,155 | `$10E4A` | F | start the supply transfer (✔, `stunits_enemy`) |
| 0 | 56 / 122 / 188 / 254,155 | `$109E8 $10B72 $10C06 $10D52` | R | open the stores priorities (2: scroll `$D38` = 0, no entry, `+$10/+$12` → `+$14/+$16`), fuel priorities (3: `$D08–$D0C` → `$D0E–$D12`), stores queue (4), fuel queue (5). All need comms; 4 and 5 also no transfer and the carrier at the stockpile |
| 0 | 15 pictures 44…244, 8…92 | `$1098A` | R | item picture: `$D16` = i, `$D14` = 1; back `$109BC` (142,115) |
| 2 | 228,22 / 228,114 | `$10A66` / `$10A3C` | R | reset the edit copies / EXIT: commit them (`+$14/+$16` → `+$10/+$12`), `$D14` = 0 |
| 2 | 176,44 / 176,76 | `$10AA6` / `$10A8A` | R | scroll `$D38` −1 (≥ 0) / +1 (≤ 12): the three rows show items `$D38`… |
| 2 | 80, 24/60/96 | `$10ABC` | F | class of row r: `+$14` = 2, 1, 0 by the pointer's offset in the field (≤ 10, ≤ 21, else; `$10AE6`) |
| 2 | 120, 31/67/103 | `$10B02` | F | type the target of row r: `$D18` = r, keypad entry `$11142` (3 digits, old value `+$16`, callback `$10B5A`) |
| 2 | keypad 216,44 (64 × 64) | `$11196` | F | while `$D1A`: digits (boxes `$111D0`; no leading 0, at most `[$D2C]`) into `$D1B` (count `$D2A`), ENTER (decimal → `+$16` of item `$D18 + $D38`), CANCEL (the old value `$D32` back), DELETE |
| 3 | 228,22 / 228,114 | `$10BC6` / `$10BAC` | R | reset `$D0E–$D12` / EXIT: commit them to `$D08–$D0C`, `$D14` = 0 |
| 3 | 136, 24/60/96 | `$10BDA` | F | fuel k's class `$D0E + 2k` = 2, 1, 0 by the pointer offset |
| 4 | 244,116 / 251,47 / 251,79 | `$10C62 $10C7C $10C6A` | R | back; scroll `$D3A` −1 / +1 (0–12) |
| 4 | 134 / 163, 35/69/103 | `$10C88` / `$10CCA` | R | row r's item (`$D3A + r`): queue `$D96` → stockpile `$D78` / stockpile → queue, at most the room `+$18 − +8 −` queued ("Limit of ACC stores reached" at 0). Step 1; held (d2 = 1) 1 for `$D3C` (= 5 on the press) more passes, then 10 |
| 5 | 140,116 | `$10D9C` | R | back |
| 5 | 134 / 163, 36/61/86 | `$10DA4 $10E18 $10E30` / `$10DC0 $10E22 $10E3C` | R | fuel k: queue `$D6C + 4k` → stockpile `$D60 + 4k` / stockpile → queue, `$4000` a pass (longs), up to `$FFFE0 −` queued `−` tank (`$CFC`, `$D00`, `$D04`): "Limit of ACC tanks reached" |

The keypad entry (`$11142`, whose only caller is `$10B02`) and the sub-panel state `$D0E–$D3B` are game state (the model's region "resources panel $D0E"). The other sub-mode-2 handlers do nothing while `$D1A` is set. There is no keyboard entry: `$1118A` only draws the digits.

**Weapons** (all ported, type 38 unless given):

| Scr | (x, y) | Handler | F/R | Does |
|---|---|---|---|---|
| 8 | 100,155 / 122,178 / 100,178 / 122,155 | `$FD3C $FD44 $FD2C $FD34` | R | elevation `$6CA` − / + or heading `$6CC` − / + 300 >> `[$6D2]`; both rates `$6CE/$6D0` cleared |
| 8 | 117,170 | `$FD60` | R | fire key `$88C` (the turret fires on its next step) |
| 8 | 144,155 / 144,178 | `$FC9C` / `$FCAC` | F | zoom `$6D2` +1 / −1 (0–3), `$A94/$A98` (`$FCD4`) |
| 8 | 166,155 / 166,178 | `$12A34` / `$12A3C` | R | `$6D5` bit 7 / 6: the heading / elevation rate self-centres |
| 9 | 166,155 | `$1338C` | F | decoy flare (✔ `stunits_carrier`) |
| 10 | 210,155 | `$134C2` | F | launch the viewing drone (✔) |
| 10 | picture 32,6 (256 × 128) | `$13598` | F | with the SSM armed, a missile at the point under the pointer (✔) |
| 11 | markers (types 66–69) | `$104C8` | R | drag station k (`$696`); the drop `$104E6` sets `+$A` = (y − `[$4752]` + 1)·60, `+$C` = (x − `[$4750]` − 4)·60 (word compares), refused inside ±900 × ±1500 ("Drone must be outside red boundary") or within √`$24000` of another active station ("Cannot put drones on top of each other"); state `&= 1` |
| 11 | 12,155 | `$10602` | F | reset the stations to the snapshot `$141DE` (not during a recall: "Cannot cancel docking sequence") |
| 11 | 298,155 / 298,178 | `$106EC` / `$10706` | R | formation `$698` +1 (9 → 0) / −1 (−1 → 8) |
| 11 | 188,155 | `$10714` | F | apply formation `[$698]` (table `$1065C`, 9 × 4 stations × `+$A/+$C`); states `&= 1` |
| 11 | 166,155 / 166,178 | `$10738` / `$1088C` | R / F | launch / recall (✔). Launch repeats while held |

The drag and the formation buttons are refused during a recall (`$122F`: "Cannot override docking sequence"). Screen 12 has no buttons.

**Game status, title, game over:**

| Scr | Handler | Does |
|---|---|---|
| 32 | `$101F2 $10200 $101E4 $1020E`, `$1021C` | sub-mode `$708`: surrender, save, format, options; back |
| 32 | `$10228 $10230` | format single / double sided |
| 32 | `$102BE` | **save** slot k (§11); an action game then gets `$2000` difficulty |
| 32 | `$101D8` | surrender: `$463` = `$FF`, `$466` = 0 |
| 32 | `$13778` | options (217,29, `$708` = 7): row k = (y − 29) >> 4 if (y − 29) & 15 < 14; x < 243 off, 252–277 on: `$1246 + k` (sea dots, message ping, background noise, sound effects). The element is 78 high, so a fifth row k = 4 writes `$124A`, the strategy flag (quirk, §12) |
| 33 | `$103B2 / $103A0` | new strategy / action game: `$124A` = `$FF` / 0, `$42A8` (§16) |
| 33 | `$103C4 $103D2 $103DE` | load menu, cancel, **load** slot k |
| 35 | `$13990 / $1399C` | continue / stop (`$466` = 4, screen 34) |

### 10.5 Screen switching, enter and leave

**Tabs** (`$714`; the left icon column, element types 32 + tab, panel `$417F0` shown while `[$704]` = 0). Each tab remembers its last screen in `$720 + 2·tab`:

| Tab | Icon (x, y) | Screens (right icon column, type = screen, panel shown while `[$706]` = 0) | Initial |
|---|---|---|---|
| 1 carrier | 0, 0 | 0, 2, 1, 3, 4 (icons top to bottom) | 0 |
| 2 weapons | 0, 28 | 8–12 | 8 |
| 3 Walrus | 0, 56 | 16–20 | 18 |
| 4 Manta | 0, 84 | 24–28 | 27 |
| 0 game status | 0, 112 | 32 (its right column holds the menu buttons 100–103) | 32 |
| 5, 6, 7 | — | 33 title, 34 game over, 35 enemy carrier destroyed: only by a forced request | 33, 34, 35 |

**ui_frame `$F636`** (every pass, before the simulation):
1. A forced request (`[$1227]` set, from `$42A8`, the end timer and end request, enter 34 and the continue panel) is taken at once, before the hit test (that pass's clicks are lost): `$1227` = 0, tab `$714` = `[$1228]`, screen `[$720 + 2·tab]`.
2. Otherwise `$17EEA` (the click state `$45A`, §10.1) and, unless a drag runs (`$460`), the hit test. A forced request a handler made is taken next.
3. Then a pending screen `$71A` ≥ 0 (also stored as the current tab's screen), else a pending tab `$716` ≥ 0 (its remembered screen).
4. The switch `$F6B6`: leave handler of `[$718]` (none if negative), `[$718]` = new, `$139AE` (`$122D` = 0; `$9FA` camera, `$9FE` viewed, `$A02` controlled = 0), enter handler, `$71C` = panel list `$43856[new]`, `st.b` `$716` and `$71A` (only their high bytes: the words become `$FFxx`), panels drawn (`$782A`, UI).

There is no loop: a request made by an enter handler (34 requests the title) is taken by the next `ui_frame`.

**Icon handlers** (fresh press): the tab icon `$F844`, unless already lit (`$40EB2` + 2·type, bit 13), lights itself, clears the old tab's icon (type 32 + `[$714]`) and sets `$716` = tab. The screen icon `$F880` likewise clears type `[$718]` and sets `$71A` = screen. Re-entering a screen (`$71A` = `[$718]`) runs its leave and enter again: the carrier map's mode button (`$1336E`), the game-status buttons (`$71A` = 32) and the vehicle input handlers when controllability changes.

**Enter (`$139D4`) and leave (`$14318`) handlers.** "Carrier view" = `$A46/$A47` = 0 (world objects drawn), `$9FA` = object 0 (`$5DCDA`), `$9FE` = `$A02` = the carrier, `$A14` = 2. "Obj0" = object 0, "+$E" its y (a long), "+$3C" its island buffer pointer. `$6FE` = the 3D viewport rectangle (`$4744` full, `$4770`, `$4786` turret, `$475A` hangar, `$47C8` title, `$47DC` game status). Palettes (`$1326`), gauge and redraw timers (`$954–$968`, `$984`) and panel drawing are UI.

| Scr | Enter | State written | Leave |
|---|---|---|---|
| 0 | `$13C24` | `$1230` = `$FF`; carrier view; obj0 flags 0, y 240, `+$3C` = the carrier's; `$480` = `[$64A]`; auto button = `$640` bit 0 | `$143D4`: carrier visible (`+1` bit 1), `$1230` = 0 |
| 1 | `$1404E` | `$A46/$A47` = `$FF`; the damage diagram as the model list at `$64CFE` (the carrier `$4A` and parts `$4B–$52`, entry `[$B58]` lit by `+1` bit 1; 8 → part 0, `$B5A` = 0); `$9FA` = obj0 at the origin, `+$3C` = `$64CFE` | `$14414`: obj0 flags 0 |
| 2 | `$13AB2` | map view `$758–$767` ← `$768`; icon 92 = `$6C6` | `$143A8`: `$768` ← `$758` |
| 3, 12, 20, 28 | `$142E6`, `$142DC` | palette only | — |
| 4 | `$14288` | `$6FE` | — |
| 8 | `$1415A` | carrier view; obj0 flags 0, `+$3C` = the carrier's (y kept); object 1 flags 0; turret view offsets `$A94` = `[$6D2]`·10000, `$A98` = `[$6D2]`·14142 (`$FCD4`) | `$1441C`: `$A94` = `$A98` = 0 |
| 9 | `$13A64` | carrier view; obj0 flags 0, `+8` = 0, y 300, `+$3C` = the carrier's | `$14426` (`rts`) |
| 10 | `$142A0` | `$9FA` = `$9FE` = `$A02` = object 15 (the viewing drone), its visible bit cleared; `$A14` = 3; SSM disarmed `$1160` = 0 | `$14428`: object 15 visible |
| 11 | `$141B2` | snapshot of the 4 stations' `+$A/+$C` into `$141DE` (for RESET `$10602`) | — |
| 16 | `$13C94` | carrier view; obj0 = the hangar model (flags 3, type `$53`, y 140, `+$3C` = the carrier's); `$568` = record `[$DB6]`; sub-panel `$DC2` (`$11E60`: status 0 → 0, 1–2 → 1, 3+ → 2) | `$143E2`: obj0 `+1` bit 0 cleared, carrier visible |
| 17 | `$13AC2` | `$14224` (`$568`/`$56C` from `[$566]`); icons 91/92 = `$6B8`/`$6BA`; `$758` ← `$778` | `$143AE`: `$778` ← `$758` |
| 18 | `$13F06` | `$1230` = `$FF`; objects 0 and 1 flags 0; `$116C` = 0; `$14224`; `$9FE` = the Walrus object (`$9FA` stays 0), its visible bit cleared; `$A14` = 1; `$13F5A`: `$8F1` = 0, `$70E` = status (`+1`, signed) ≥ 8, `$A02` = `$9FE` if so else 0, icons (62–64 weapon, 80 turret, 90 auto, 104, 105). Not controllable: `$457` = `$FF` | `$14404`: `[$9FE]` visible, `$1230` = 0 |
| 19 | `$13D08` | `$56C`/`$568` from `[$DE4]`; object 1 flags 0; carrier view; obj0 flags, `+4`, `+8` = 0, y 50 (`+$3C` kept) | — |
| 24 | `$13D82` | carrier view; obj0 flags 0, y 240, `+$3C` = the carrier's; `$48E` = record `[$DB4]`; sub-panel `$DC0` | `$14464`: carrier visible, `$1230` = 0 |
| 25 | `$13AD2` | `$141FE` (`$48E`/`$492` from `[$48C]`); icons 91/92 = `$6AA`/`$6AC`; `$758` ← `$788` | `$143B4`: `$788` ← `$758` |
| 26 | `$13DEE` | `$492`/`$48E` from `[$DCC]`; object 1 flags 0; carrier view; obj0 flags, `+4`, `+8` = 0, y 230 (`+$3C` kept) | — |
| 27 | `$13E68` | `$1230` = `$FF`; object 1 flags 0; `$141FE`; `$9FA` = `$9FE` = the Manta object, its visible bit cleared; `$A14` = 0; `$13EBA`: `$8F1` = 0, `$70C`, `$A02` as for 18, icons; not controllable: `$457` = `$FF`; `$116C` = 0 | `$143F4`: `[$9FE]` visible, `$1230` = 0 |
| 32 | `$13AFA` | sound off; `$708` = 0 if entered from the tab icon (`[$716]` ≥ 0, so not on a re-enter); button `100 + k` lit for `[$708]` (table `$13B6C`); **`$461` = `$FF`** | `$143CA`: `$461` = 0, `$706` = 0 |
| 33 | `$13B7C` | `$8F0` = 0 (no 3-VBL frame lock); `$A46/$A47` = `$FF`; the title model at `$64CFE`; `$6CA` = 2000; `$9FA` = obj0 at the origin; `$461` = `$FF`; `$93C` = `[$93A]` | `$14432`: `$8F0` = `$FF` |
| 34 | `$1424A` | `$F712`; `$457` = `$461` = `$FF`; `$4272`: request tab 5 (title), `$708` = 0 | `$14438`: a tune if `[$466]` = 1; waits for a key (`$889`); clears `$881` bit 0 |
| 35 | `$14266` | sound off; `$F712`; `$457` = `$461` = `$FF` | `$1445E`: `$461` = 0 |

Screens 5–7, 13–15, 21–23 and 29–31 have `rts` handlers.

**What the renderer follows** (after the enter handler; `$9FA` camera, `$9FE` viewed, `$A02` controlled, `$A14` camera mode):

| Scr | `$9FA` | `$9FE` | `$A02` | `$A14` | Object 0 |
|---|---|---|---|---|---|
| 0 bridge | object 0 | carrier | carrier | 2 | y 240 |
| 8 turret | object 0 | carrier | carrier | 2 | y kept; the view is offset by `$A94/$A98` |
| 9 flares | object 0 | carrier | carrier | 2 | y 300 |
| 10 drone | object 15 | object 15 | object 15 | 3 | — |
| 16 Walrus hangar | object 0 | carrier | carrier | 2 | hangar model `$53`, y 140 |
| 18 Walrus | 0 | the Walrus | it, if status ≥ 8, else 0 | 1 | flags 0 |
| 19 Walrus dock | object 0 | carrier | carrier | 2 | y 50 |
| 24 Manta hangar | object 0 | carrier | carrier | 2 | y 240 |
| 26 Manta deck | object 0 | carrier | carrier | 2 | y 230 |
| 27 Manta | the Manta | the Manta | it, if status ≥ 8, else 0 | 0 | — |
| 1 damage, 33 title | object 0 at the origin, its `+$3C` the model list `$64CFE` | 0 | 0 | kept | — |
| other screens | 0 | 0 | 0 | kept | — |

Each screen's render sets object 0's position, angles and island buffer (`+$3C`) every frame (screen 18 also `$9FA` = object 0): §10.8 lists the rules (`$A14` isn't read by them). `$9FE` is also the island defences' viewer rule (§8.1). With `$9FE` = 0 (maps, status screens) the simulation still runs.

**Notes:**
- **Visible bits.** Entering a vehicle view clears the viewed object's visible bit (`+1` bit 1); leaving sets it again, as do leaving 0/24 for the carrier and 10 for the drone. Selecting another vehicle in place (`$FE54`/`$10016`) swaps the bits and view pointers without leave/enter.
- **Controllability.** `$70C`/`$70E` and `$A02` are recomputed on enter (and on select). The input handlers re-enter the screen when a vehicle's status crosses 8.
- **`$461`** (simulation skipped, game clock stopped, PAUSE ignored) is set by entering 32–35 and cleared by leaving 32 and 35. Leaving 33 and 34 doesn't clear it: `$42A8` restores it from the snapshot, and loading a game too.
- **Saved selections.** The arming and dock screens keep their own selection (`$DB4`, `$DB6`, `$DCC`, `$DE4`) and repoint `$48E`/`$492` or `$568`/`$56C` without touching `$48C`/`$566`. The control and map screens reselect from `$48C`/`$566` on enter.
- Panel draw callbacks also write element flags (e.g. type 79 hidden unless the Walrus is in the hangar, `$C3D4`; type 81, `$C076`) and patch element handlers (`$4281C`). Only the two that write game state are modelled (`panels_drawn`, §10.4: the Manta balance flag, the Harbinger weight and the ± handlers); the other flags are left out with the panel drawing, and the UI must apply them when it offers those buttons.

### 10.6 The port (`stunits_controls.py`, `units_controls.ts`)

The browser UI calls these methods in place of the mouse, keyboard and click handlers, then `game_frame()` once per pass:
- `cmd_*` methods write exactly what their handler writes. Redraws, palettes, sounds and the 3D highlight bits are left out, and messages become `("message", text)` events.
- They return False where the hit test (§10.1) would not have reached the handler: panel state, element flags, paused, or a drag in progress (`$460`: no hit test runs).
- They don't check the screen or pointer mode. The UI decides which controls it offers.
- Two exceptions keep the arming screens' invariants, which other screens' commands could otherwise break (the original only reaches these handlers on screen 24 / 16):
  - The loadout panel's elements (list ±, pick, slots, Walrus ±; so `cmd_*_fit/unfit`) also need `[$48E]` / `[$568]` to be the arming bay's record (`$DB4` / `$DB6`) and that vehicle in the hangar (status 0); `cmd_manta_arming_to_deck` needs the record. On the arming screen both always hold while sub-panel 0 is shown. Elsewhere `cmd_manta_select`, the deck/map selections and the hangar commands' bay arguments repoint the record, possibly to a vehicle that is out, whose `+$C/+$E/+$10` hold counts (`+$10` = `$FFxx` with a Quasar), not codes.
  - The hangar commands (`stunits_vehicles`) return False during a drag. With a bay argument (the UI's stand-in for selecting it on the arming screen), prepare needs status 1/2, or 0 with that bay selected and sub-panel 1 still shown; replace needs a destroyed vehicle (only repair's `bmi` reaches `$117D4`/`$11EF0`). Unchecked, prepare sent an out Manta to status 2 and then 0, a "hangar" record holding counts.

| Method | Original |
|---|---|
| `input_mouse_move(dx, dy)`, `input_mouse_button(right, down)`, `input_key(scancode, down)` | the IKBD interrupt's writes |
| `cmd_carrier_throttle(y)`, `cmd_carrier_speed_up/down()`, `cmd_carrier_stop()`, `cmd_carrier_centre_rudder()`, `cmd_carrier_autopilot(held)` | `$FE3C`, `$FDCE/$FDD4`, `$12A26`, `$12A16`, `$12BAC` |
| `cmd_toggle_mouse_steer()` | a right click (the next screen 0 input toggles `$457`) |
| `cmd_steer_to(rudder)` | the mouse-steer delta that makes the next `carrier_step` set `$642` = rudder (screen 0, mouse-steer, no autopilot) |
| `map_to_world(px, py)`, `world_to_map(x, z)` | `$F91E` and its inverse |
| `cmd_map_click(px, py)`, `cmd_map_recentre(px, py)`, `cmd_map_zoom_in/out()`, `cmd_map_scroll_up/down/left/right()`, `cmd_map_centre_unit()`, `cmd_map_centre_carrier()`, `cmd_map_island_info()`, `cmd_map_island_info_exit()` | `$F8D8`, `$F8B4`, `$F984/$F99A`, `$FAB0 $FAC8 $FA84 $FA6C`, `$F9BE`, `$FA50`, `$FAF4`, `$FB1A` |
| `cmd_carrier_set_course(held)`, `cmd_carrier_clear_course()`, `cmd_carrier_map_mode()`, `cmd_carrier_cruise_up/down()`, `cmd_carrier_cruise(y)`, `cmd_carrier_toggle_announce()` | `$131A8`, `$1326C`, `$1336E`, `$12E7A/$12E8C`, `$12E9E`, `$12E44` |
| `cmd_set_carrier_waypoint(x, z)` | the carrier cursor at (x, z) as a map click writes it, then set course. Any x/z within ±`$7FFFFF` is accepted, not just the map grid |
| `cmd_select_system(k)`, `cmd_priorities_open()`, `cmd_priority_click(px, py)`, `cmd_priorities_cancel()`, `cmd_priorities_exit()`, `cmd_set_repair_priority(k, cls)` | `$FB3A`, `$FBBE`, `$FC64`, `$FC08`, `$FC24`; the last is open + click + EXIT |
| `cmd_pause()` | `$FD66` |
| `ui_frame()` | `$F636` without the hit test: the forced request, else `$17EEA`, then during a drag the drop once the button is up (`drag_drop`, `$F6EA`; the four arming drops are ported, others raise `NotImplementedError`), else the pending screen/tab (§10.5). Call it once per pass after the commands, before `game_frame` |
| `cmd_select_tab(tab)`, `cmd_select_screen(screen)` | `$F844`, `$F880`, then the rest of `ui_frame` (`$F67A`): the switch happens in the call |
| `cmd_switch_screen(screen)` | the clicks that reach screen 0–4, 8–12, 16–20, 24–28 or 32: the tab icon if another tab is shown (entering its remembered screen), then the screen icon |
| `cmd_game_status_menu(mode)`, `cmd_game_status_back()`, `cmd_surrender()` | `$101F2 $10200 $101E4 $1020E` (mode 6, 3, 1, 7), `$1021C`, `$101D8`. The save, format and options panels themselves are not ported |
| `cmd_continue(yes)` | `$13990` / `$1399C` (screen 35) |
| `new_game_screen()` | replaces `stunits_setup`'s: `ui_frame`'s forced request after `$42A8` |
| `enter_*`, `leave_*`, `select_manta()`, `select_walrus()`, `manta_view_update(rec)`, `walrus_view_update(rec)`, `turret_zoom_view(z)`, `panels_drawn(screen)` | the enter/leave handlers and their helpers `$141FE $14224 $13EBA $13F5A $FCD4`; the state the switch's panel drawing `$782A` writes (§10.4) |
| `cmd_manta_select(bay)`, `cmd_manta_weapon(w)`, `cmd_manta_autopilot()`, `cmd_manta_centre_rudder()`, `cmd_manta_centre_pitch()`, `cmd_manta_instruments()`, `cmd_manta_throttle(y)`, `cmd_manta_sight()`, `cmd_manta_rear_view()`, `cmd_manta_runway_take_off()` | screen 27: `$FE54`, `$FEE2`, `$12A44`, `$129E6`, `$129FE`, `$100A8`, `$FDDA`, `$10100`, `$1015C`, `$13356` |
| `cmd_manta_steer_to(rudder, pitch)` | the mouse-steer deltas that make the next `manta_step` set the selected Manta's rudder `+2` and pitch `+4` (it must be the viewed one, manual, `$457` clear) |
| `cmd_vehicle_fire()` | a left click in mouse-steer mode: `$881`, which the viewed Manta or Walrus fires with on its next step |
| `cmd_manta_deck_select(bay)`, `cmd_manta_abort_landing()` | screen 26: `$11F92`, `$12360` (to deck, land, take off, to hangar: `cmd_manta_to_deck/land/take_off/to_hangar` in `stunits_vehicles`) |
| `cmd_manta_map_select(bay)`, `cmd_manta_set_course(held)`, `cmd_manta_clear_course()`, `cmd_manta_cruise_up/down()`, `cmd_manta_cruise(y)`, `cmd_manta_altitude_up/down()`, `cmd_manta_altitude(y)`, `cmd_manta_map_draw_mode()`, `cmd_manta_toggle_announce()` | screen 25: `$FF1E` (+ recall `$12ECC`), `$12FC4`, `$12F8E`, `$12C90/$12CA2`, `$12CB4`, `$12CE2/$12CF6`, `$12D0A`, `$12D40`, `$12D50` |
| `cmd_set_manta_waypoint(x, z)` | the Manta map cursor `$69E/$6A6` at (x, z), then set course (altitude and cruise as edited) |
| `cmd_manta_arming_select(bay)`, `cmd_manta_arming_to_deck()`, `cmd_manta_loadout_next/prev()`, `cmd_manta_pick_loadout()`, `cmd_manta_pick_slot(k)` | screen 24: `$116C6`, `$11886`, `$1132E/$11352`, `$1135E`, `$114C8` (refuel, defuel, repair, replace, prepare: `stunits_vehicles`) |
| `cmd_manta_fit(slot, code)`, `cmd_manta_unfit(slot)` | the clicks a player makes: cursor steps to `code`, pick up, release over the slot / the stores box, `ui_frame` drops |
| `cmd_walrus_select(bay)`, `cmd_walrus_weapon(w)`, `cmd_walrus_autopilot()`, `cmd_walrus_centre_rudder(held)`, `cmd_walrus_turret_mode()`, `cmd_walrus_instruments()`, `cmd_walrus_throttle(y)`, `cmd_walrus_sight()`, `cmd_walrus_rear_view()` | screen 18: `$10016`, `$FF7C`, `$12B00`, `$12982`, `$129A2`, `$100D4`, `$FE0A`, `$1012E`, `$10190` |
| `cmd_walrus_steer_to(rudder)`, `cmd_walrus_missile_steer_to(yaw, pitch)` | mouse-steer deltas for the Walrus rudder `+2`, or, while it steers a Harbinger (bit 4; False otherwise), the missile's turn rates `+$1E/+$20` |
| `cmd_walrus_dock_select(bay)`, `cmd_walrus_abort_docking()` | screen 19: `$1249E`, `$128AE` (launch, dock: `cmd_walrus_launch/dock`) |
| `cmd_walrus_map_select(bay)`, `cmd_walrus_set_course(held)`, `cmd_walrus_clear_course()`, `cmd_walrus_cruise_up/down()`, `cmd_walrus_cruise(y)`, `cmd_walrus_map_draw_mode()`, `cmd_walrus_toggle_announce()`, `cmd_set_walrus_waypoint(x, z)` | screen 17: `$FFB8` (+ recall `$12F30`), `$13122`, `$130EE`, `$12D98/$12DAA`, `$12DBC`, `$12DEC`, `$12DFC`; cursor `$6B0/$6B4` + set course |
| `cmd_walrus_arming_select(bay)`, `cmd_walrus_loadout_next/prev()`, `cmd_walrus_loadout_plus/minus()`, `cmd_walrus_pick_loadout()`, `cmd_walrus_pick_slot(k)`, `cmd_walrus_fit(slot, code)`, `cmd_walrus_unfit(slot)` | screen 16: `$11DEE`, `$11A30/$11A76`, the type-81 buttons (`[$4281C]`/`[$42832]`: Harbingers `$11CE0/$11D0C` or ACCB type `$11D1C/$11D40`), `$11A82`, `$11C16`; fit/unfit as for the Manta |
| `cmd_drag_release(px, py)` | the end of a drag: pointer at (px, py), left button up; the next `ui_frame` drops |
| `key_select(base, handler)`, `manta_control_input()`, `manta_deck_input()`, `manta_arming_input()`, `walrus_control_input()`, `walrus_dock_input()`, `walrus_arming_input()` | `$132D6` and the input handlers `$172BC $1773C $17610 $17374 $17782 $17748` |
| `vehicle_ui()` | read helper: the selections (`$48C`, `$566`, `$DB4`, `$DB6`, `$DCC`, `$DE4`), controllable `$70C/$70E`, the loadout list cursors' codes, `$CB80`, the drag in progress, the lock timer |
| `game_frame()` | `$408A–$4190` without `ui_frame`, rendering and sound: `$4194`, `paused_frame`, `debug_keys`, `screen_input`, `sim_pass`, end timer, end request. It records `("game over", 35, [$466])` (enemy carrier destroyed; the original asks whether to continue) or `("game over", 34, [$466])` |
| `screen_input()` | `$17094`: every screen's input handler |
| `cmd_bridge_zoom_in/out()`, `bridge_zoom(d)` | `$FCE8/$FCF8` (`$8B4`, `$8B2`) |
| `cmd_turret_up/down/left/right()`, `cmd_turret_fire()`, `cmd_turret_zoom_in/out()`, `cmd_turret_centre_heading/elevation()`, `turret_input()` | screen 8: `$FD3C $FD44 $FD2C $FD34`, `$FD60`, `$FC9C/$FCAC`, `$12A34/$12A3C`, `$1741A` |
| `cmd_turret_steer_to(heading_rate, elevation_rate)` | mouse-steer deltas for the turret's aim rates `$6CE/$6D0` (screen 8, `$457` clear); `cmd_vehicle_fire()` fires |
| `cmd_flare_launch()`, `cmd_recon_launch()`, `cmd_ssm_fire_at(px, py)`, `drone_view_input()` | `$1338C`, `$134C2`, `$13598` with the pointer at (px, py), `$1778E` (a right click, `cmd_toggle_mouse_steer()`, arms the SSM): the gated buttons of `stunits_carrier`'s commands |
| `cmd_drones_launch()`, `cmd_drones_recall()`, `cmd_drone_stations_reset()`, `cmd_drone_formation_next/prev/apply()`, `cmd_drone_station_pick(k)`, `cmd_drone_station_move(k, px, py)`, `drone_station_pixel(k)` | screen 11: `$10738`, `$1088C`, `$10602`, `$106EC/$10706/$10714`, `$104C8` + drop `$104E6`; the move is pick, release, `ui_frame`; the last is the drop's inverse for the UI |
| `resources_input()`, `cmd_resupply_abort()`, `cmd_resources_transfer()`, `cmd_item_picture(k)`, `cmd_item_picture_back()` | screen 3: `$177DE`, `$1102E`, `$10E4A` (`cmd_supply_transfer`), `$1098A`, `$109BC` |
| `cmd_stores_priorities_open/reset/exit()`, `cmd_stores_scroll_up/down()`, `cmd_stores_class(row, cls)`, `cmd_stores_target(row)`, `cmd_keypad(key)` | `$109E8 $10A66 $10A3C`, `$10AA6/$10A8A`, `$10ABC`, `$10B02`, `$11196` (key 0–9, 10 ENTER, 11 CANCEL, 12 DELETE) |
| `cmd_fuel_priorities_open/reset/exit()`, `cmd_fuel_class(k, cls)` | `$10B72 $10BC6 $10BAC`, `$10BDA` |
| `cmd_stores_queue_open/back()`, `cmd_stores_queue_scroll_up/down()`, `cmd_stores_unqueue/queue(row, held)`, `cmd_fuel_queue_open/back()`, `cmd_fuel_unqueue/queue(k)` | `$10C06 $10C62`, `$10C7C/$10C6A`, `$10C88/$10CCA` (held = auto-repeat, d2 = 1), `$10D52 $10D9C`, `$10DA4 $10E18 $10E30` / `$10DC0 $10E22 $10E3C` |
| `cmd_set_stockpile()`, `stockpile_reship()`, `island_name(i)` | `$137C2` (carrier map, islands panel), `$21D46`, the name table `$4AD20` |
| `cmd_clear_message_log(logged)` | `$12C2C`; the log is the app's, so it says whether there is one |
| `cmd_game_option(k, on)`, `cmd_title_new_game(strategy)` | `$13778` (k 0–3; 4 is the `$124A` quirk), `$103B2/$103A0` (then `new_game`) |

`screen_input` reads only input and UI state, so `game_frame` runs it before `sim_pass` instead of after the win check and route relaxation; the result is the same. The panel flag table is in the state, as `redraw: panel flags $40EB2`: the UI code changes it too, so `check_units.diff` skips it, and this module's checks compare it themselves.

**Checks** (`check_units.py`):
- each of the 40 commands against its original handler(s), called with the registers the hit test passes, on 168 captured and perturbed states (action and play captures; screens 0/1/2/4/17/25, pause, menu, panel states and flags, map view, priorities);
- `game_frame` against the original loop from `$4090` back to `$408A`, with rendering `$14472` and `frame_flip` `$17F48` stubbed:
  - 496 perturbed single frames: pause, menu, Control-Q/S, cheat code, debug keys, end timer and request, every ported screen's input;
  - 40 runs of 200 frames with random commands and raw input between frames, the original running its own `screen_input`.
- screen switching against `ui_frame` and the enter/leave handlers (the icon handlers called with a3 = an element of their type, then `$F67A`; `ui_frame` itself; the game-over key wait skipped), on 44 captured states (action, play and Hatari new-game captures) with perturbed screen state (current screen and tab, remembered screens, icon and panel flags, pending tab/screen and forced requests, selections, bay records, `$708`, `$6D2`, `$B58`, the map icons' inputs):
  - every screen 0–4, 8–12, 16–20, 24–28 and 32 by `cmd_switch_screen` from a random screen, and 33–35 by forced request, from each state (1056 switches), half of them followed by 8 passes of `ui_frame` + `game_frame`;
  - 704 random commands (`ui_frame`, tab and screen icons, `cmd_switch_screen`, game-status buttons, surrender, continue), 30 % followed by 12 passes with further random switches (about 4800 passes after switches in all).

  The passes run every screen's input handler on both sides; the comparison leaves out the element flags that only panel drawing writes (types 79 and 81).
- the 66 Manta and Walrus commands (§10.4) against their handlers, called with the registers the hit test passes (d0/d1, d2 = 2 or 1 for held, a3 = the real panel element; the ± buttons through the handler the panel installed; drags released over a slot, the stores box or elsewhere and dropped by the original's `ui_frame`), each on 135 perturbed states (360 for the arming panels' commands; action and play captures with Mantas and Walruses out, landing and docking; vehicle screens, icon and button flags, controllability, record flags, modes, loadouts, weights and Harbingers, stocks, landing/docking flags, map edit values, list cursors, drags, keys; the arming bay as the selected record in any status, and out Mantas holding counts, `+$10` = `$FFxx`);
- the 16 hangar commands (`stunits_vehicles.check_commands`) on 240 perturbed states each, half with a bay argument (the refusals compared as "nothing happens"), drags in progress, out Mantas holding counts;
- `screen_input` on screens 16–19 and 24–27 against `$17094` on 720 perturbed states (number keys in and out of pointer mode and drags, re-entry, gauges, the auto button, right clicks, missile steering, the lock timer, the hangar animation);
- 46 runs of 120 passes (`ui_frame` + `game_frame`) on every vehicle screen, entered by clicks from play and action captures, with random vehicle commands, drags, mouse steering, firing, throttle and number keys and right clicks between passes, the original running its own `ui_frame` and `screen_input`.
- the 52 "War" commands (bridge zoom, turret, flares, viewing drone and SSM, defence drones and the station drag, every resources handler with the keypad entry, the stockpile island, message log, options) against their handlers with the registers the hit test passes (d0/d1, d2 = 2 or 1, a3 = the real element; the drag released on the radar and dropped by the original's `ui_frame`), each on 264 perturbed states (screens 3, 4, 8–12, 32; sub-panels, keypad entries, scrolls, items, stocks, queues and fuel, comms, transfer state, stockpile, turret angles/rates/zoom, drones and stations, recalls, stocks and launcher damage);
- `screen_input` on screens 3, 8 and 10 (and 0's zoom keys) against `$17094` on about 800 perturbed states;
- 40 runs of 120 passes on screens 0, 3, 4 and 8–12, entered by clicks, with random War commands, cursor/Insert/Clr-Home keys, right clicks, turret mouse aiming and firing;
- `cmd_title_new_game` against `$103B2/$103A0` and `ui_frame` from the title image (8 clicks, 4 new games).

### 10.7 What each screen shows

The 2D screens draw everything from state each pass: the render routine (`$144A0`) and the panel draw callbacks (`$43856`, run by `$B400`/`$B418` while their state word matches). `web/src/app/readout_screens.ts` has a read helper for each, named after what it shows, which returns the displayed data (and the formatted text where the original's formatting matters) from a RAM image. `tools/check_readouts.py` checks each helper's Python mirror (`tools/stscreens_*.py`) against the original: `tools/stscreens.py` runs the screen's render and panels under Unicorn, logs every character drawn (5×5 `$9AD2`, 8×8 `$9C94`, vector `$9CEE`) and, per screen, the bar, rectangle, line and icon primitives, and compares on captures plus perturbed states. All 6768 cases match at the default fuzz.

| Screen | Helpers |
|---|---|
| shared | `fmtDigits`, `blankZeros`, `numberField`, `percent`, `percentText` |
| 3 resources | `resourcesSubPage`, `resourcesStoresOnBoard`, `resourcesCarrierFuel`, `resourcesItemPage`, `resourcesStoresPriorities`, `resourcesClassLevel`, `resourcesFuelPriorities`, `resourcesStoresTransfer`, `resourcesFuelTransfer`, `resourcesGate`, `resourcesBar`, `resourcesQty3`, `storeItemName` |
| 4 messages | `messageLog`, `messageAgeText` |
| 11 defence radar | `defenceRadar`, `droneState` |
| 12 weapons status | `weaponsStatus`, `droneState` |
| 16/24 arming | `armingView`, `armingPanel`, `loadoutCatalogue`, `loadoutEntry`, `baySummary` |
| 20/28 status | `vehicleStatusPage`, `mapCoordinate` |
| 2/17/25 maps | `mapViewState`, `mapToScreen`, `mapIslands`, `mapUnits`, `mapMarkerSegments`, `mapLinksDrawn`, `islandsPanel`, `islandInfoBox`, `mapSliders`, `mapBorder`, `mapCopyright`, `mapCoordText`, `MAP_SHOWS_ENEMY_CARRIER` |
| 32 game status | `gameStatus`, `score`, `gameClock`, `gameOptions` |
| 34 game over | `gameOver` (35 has fixed text only) |

**Not shown anywhere:** the difficulty, the enemy carrier and enemy units on the map (§ maps below), the stockpile's in-transit flag `$D50`, island production, stocks, buildings and defences, and vehicle fuel/mode/waypoint/telemetry on the status screens (fuel is a bar on the arming screens).

#### Text and number routines (shared by every 2D screen)

- `$9A56` 5×5 string at (d1, d2): characters ≥ `$20` (6 px each, `$9AD2`); `5 n` colour; `6` skips spaces; any other control byte ends it.
- `$9BC0` 8×8 stream from the cursor `$122B/$122C` (8-px columns, pixel rows; margin `$122A`): 0 end, `1 x`, `2 y`, `3 x y`, 4 margin = x, `5 n` colour, 6 skip spaces, 13 newline (x = margin, y + 8), ≥ `$80` a macro from `$9C8A`; other bytes are cells (`$9C94`).
- `$9CEE` vector-font string (scale d3).
- `$9FAA/$9FB6/$9FC2/$9FCE`: 5/4/3/2 digits of an unsigned word by repeated subtraction. The first digit is `'0'` + quotient, so it isn't limited to 9 (2 digits of 105 → `:5`). Callers blank leading zeros: inline `cmpi.b #'0'` (one), or `$9FFE` (n).
- `$EC3C/$EC4A` percentage: p = (cur & `$FFFF`)·100 / max (`divu`, low word). The text is `100%` only at exactly 100; otherwise it's 3 places, right-aligned, with the tens blank when 0 (`  5%`, ` 44%`).

#### Screen 4: Messages

Render `$162B0`, redrawn only while `$966` ≠ 0 (`$A206` sets it to 2).

| What | Source | Format |
|---|---|---|
| Log | linked list from `[$1148]` through 16 records of `$30` bytes at `$65D10`: `next.l`, `time.l` (`[$97A]` when posted), text from `+8` | oldest first, one line per 6 px from y 28 |
| Age | `[$97A]` − time | `M:SS`: one character `'0'` + age / 60 at x 33, `:` at 38, seconds as 2 digits at 42 (no blanking) |
| Text | `+8` | at x 59, as `$9A56` draws it |
| Empty | `[$1148]` = 0 | `NO MESSAGES` at (127, 56) |

- **Adding** (`$A128` → `$A206`): the first free record (top bit of `next` set) gets time `[$97A]` and the text uppercased (a–z only), and is appended at the tail. With all 16 in use, the head (oldest) is reused. So at most 16 messages are shown.
- **Expiry:** the render drops the leading records older than **300 s** (unsigned compare), marks them free (`st.b` on `next`) and moves `$1148`; everything after the first young one is listed. The PNG bar message (`$9B3`/`$98C`, `$964`/`$988`) is separate.
- **CLEAR** `$12C2C` empties the log.

#### Screen 11: Defence drones radar

Render `$15EB0`; the panel callback `$D166` draws the frame art and sets `$95A` = 2.

This is a plan view of the carrier at **1/60**, bow to the right, centred on the `$4744` viewport centre (`+$C`, `+$E`) = (160, 70). A carrier-relative point (across, along) is drawn at x = cx + along/60, y = cy + across/60 (`divs`, truncating), and icons go at (−4, −8) from it.

| Element | Source | Shown when |
|---|---|---|
| Carrier outline | line list `$16254` over the 39 points `$161BA` (`/60`) | always |
| Drone k (element type 66 + k, `$42A4E` + `$16`k; draggable) | station record `$65E` + 14k: position `+2` (across), `+4` (along) | `+1` bit 0 (out) and the Radar system works (status byte `$B02` ≠ 0). Otherwise the element is hidden (`$40EB2` flag bit 15) |
| Its station icon (16-px sprite `$161A2` + 6k) | `+$A` (across), `+$C` (along) | as above, and `+1` bit 1 (on station) clear |
| Formation preview (box (213, 157)–(294, 196), while `$95A`, counted down) | `$1065C` + 16·`[$698]`: 4 × (across, along) | four 3×3 colour-2 squares centred at (254 + along/192, 177 + across/192) |
| `DRONES ACTIVE` / `DRONES IN STORAGE` | `$65C` / item 6 `$C1A` | 2 digits, a leading 0 blank, (40, 160) / (40, 168) |
| Status line (y 184) | as on screen 12 | |

The station flag bits (`$1A296`):
- bit 0: out;
- bit 1: on station (on arrival, `+2/+4` = `+$A/+$C`);
- bit 2: heading to the waypoint `+6/+8`;
- bit 3: launching.

Formation 0 is the default cross (±3000 on each axis), the stations the game starts with; formations 1–8 are the other preset patterns (table `$1065C`).

#### Screen 12: Weapons status

Render `$16BA6`; the static labels come from the list `$16E46` via `$C6F6`.

| Line | Source | Rule |
|---|---|---|
| Laser cannon STATUS / REPAIR STATE | system 4 | `OPERATIVE` if the status **word** `$B1A` ≠ 0, else `INOPERATIVE`; percent `$B14`/`$B16` |
| Observation turret | system 3 | status **byte** `$B12` ≠ 0; percent `$B0C`/`$B0E` |
| `LASER OVERHEATING` (45, 61) | heat `$6C8` | > `$3000` (unsigned) |
| FLARES ACTIVE / IN STORES | `$1152` / item 10 `$C82` | 2 digits, a leading 0 blank |
| Flare launcher | system 5, byte `$B22`; `$B1C`/`$B1E` | |
| Flare line (y 61) | | `$C82` = 0 → `NO FLARES AVAILABLE`; else `[$1152]` = 2 or reload `$1150` ≠ 0 → `RELOADING LAUNCHER`; else `FLARE LAUNCHER READY` |
| VIEWERS / MISSILES IN STORES | item 11 `$C9C` / item 2 `$BB2` | 2 digits |
| Missile launcher | system 6, byte `$B2A`; `$B24`/`$B26` | |
| `VIEWING DRONE ACTIVE` (36, 126) | object 15 `+1` bit 0 (`$5EF9B`) | when live |
| DRONES ACTIVE / IN STORES | `$65C` / item 6 `$C1A` | 2 digits |
| Drone line (y 126) | `$122F`, stations `$65E` | `$122F` set → `DRONES DOCKING`; none out (bit 0) → `NO DRONES ACTIVE`; all out drones' bit 1 set (no record with bit 1 clear) → `DRONES ON STATION`; else `DRONES IN TRANSIT` |

Systems 3, 5 and 6 use formula B (§5.2), which writes only the high byte (`st.b`/`sf.b`), so their status is a byte. The laser (formula A) is tested as a word.

#### Screen 32: Game status

Panels by sub-mode `[$708]`:

| Mode | Callback | Shows |
|---|---|---|
| 0 | `$CD5C` | `GAME STATUS`; `SCORE:` (`$A418`: 12 digit bytes `$918–$923`, most significant first, + `'0'` into `$90A`, up to 11 leading zeros blanked, then control 6 skips them, so a plain decimal); `TIME:` `HH:MM:SS` from `$8F4/$8F6/$8F8` (`$CDC8` into `$900`, 2 digits each, no blanking); `FRIENDLY ISLANDS:` `$D54`, `ENEMY ISLANDS:` `$D56` (2 digits, a leading 0 dropped) |
| 1 | `$E4E4` | the surrender prompt (fixed) |
| 3 | `$E510` | the save prompt (fixed) |
| 6 | `$E4F4` | the format prompt (fixed) |
| 5 | `$E4FC` | a disk error, `[$70A]` 0–5 (table `$E520`): couldn't format, error writing, not a game disk, error reading, not a valid game, program disk |
| 7 | `$EAEE` | GAME OPTIONS: Sea Dots `$1246`, Message Ping `$1247`, Background Noise `$1248`, Sound Effects `$1249`. YES (x `$D9`) is lit (bit 13) while the byte is 0, NO (x `$FC`) otherwise |

- Points are added by `$A3EE` (d0 added to the last digit, with carry); `$A3E0` clears them.
- The clock is run by the VBL (`$17BC8`): 50 frames (`$8FA`) make a second, and it counts only while `$462` is set and `$461` is clear. Hours wrap at 24 into days `$8F2`, which isn't shown.
- **Difficulty is not shown anywhere.**

#### Screens 34 / 35: Game over

Screen 34: panel callback `$CF42`, viewport `$47B2`.

- `GAME OVER` in the vector font at (10, 20), scale `$600`.
- The result text `$CFCA[[$466]]`:
  - 0 `Game was aborted`;
  - 1 `Aircraft Carrier Destroyed! / All is lost`;
  - 2 `All islands captured by enemy / All is lost`;
  - 3 `All islands captured! / Congratulations on completing / Carrier Command`;
  - 4 `Enemy Carrier Destroyed! / Congratulations …`.
- `Time:` (as on screen 32), `Final Score:` (the score), and `Islands Taken:` `$D52`, as 4 digits (`$9FB6`) with 3 leading zeros blanked and skipped. `$D52` is +1 for each island the player captures (`$1C362`, `$1C55C`); it's never decreased.

**Screen 35** (`$EBBA`): fixed text `Enemy Carrier Destroyed! / Remaining islands can be captured / for maximum points. / Do you want to continue?`, with NO / YES buttons. There are no values on it.

**Check** (`tools/check_readouts.py misc`, mirror `stscreens_misc.py`): the text and plot positions the original draws were captured by hooking `$9AD2`/`$9C94`/`$9CEE` (characters), `$B292` (radar icons), `$5308` (preview squares) and `$B034` (option buttons), and the element flags and positions after the render. They were compared with the mirror on 15 captures (units-frame, play, action) × 5 screens × (1 + 40 fuzzed states): messages, systems and stocks, drone records and formations, score digits, clock, counts, result and options. That's 3075 cases, all exact.

#### Screen 3: Resources

The render `$14C6C` jumps on the sub-page `[$D14]` (table `$14C7E`: 0 `$14C96`, 2 `$1118A`, 5 `$14D28`, others `rts`). Each sub-page has one panel (state `[$D14]` = n) whose callback draws the data; sub-page 0 also shows the bottom button panel `$42CC8` (`$D188`, icons only). Store items are the 15 records at `$B76` (`$1A` bytes):

| Offset | Meaning |
|---|---|
| `+0/+2` | icon position on sub-page 0 |
| `+4.l` | 32×32 icon |
| `+8` | **count on board** (what launches and loadouts take) |
| `+$A.l` | name: 8×8 control-coded text (code 3 x y; x in 8-px columns), then after its NUL the description (5×5 lines separated by control bytes, NUL-terminated) |
| `+$E` | picture index (64×64 at `$3B426 + $600·n`) |
| `+$10/+$12` | committed priority class (0–2) and target stock: what the logistics use |
| `+$14/+$16` | working copies edited on sub-page 2 |
| `+$18` | carrier capacity (not drawn) |

Numbers: 3 digits by `$9FC2` (repeated subtraction on the unsigned word), then `$9FFE` with 2 blanks up to two leading zeros (`"  0"`, `" 32"`, `"999"`). Values above 999 would print a byte `$30 + n/100` (a control byte ends the 5×5 string); the game caps stores at 999. Bars: `$5488` draws two rows (y and y − 1); first the full 78-px track in colour 0, then the value in colour 2 from x0 to x0 + n, n = `divs.w` quotient (on overflow the value's low word), min 78 (signed compare).

| Sub | Drawn by | Shows | Derivation |
|---|---|---|---|
| 0 stores | `$D264`, render `$14C96` | each item's icon at (`+0`, `+2`) and its count `+8` at (x + 7, y + 34); the carrier's tanks as bars at y 192: ship `$CFC` x 60, aircraft `$D00` x 148, AAV `$D04` x 236 | n = fuel / `$3482` (78 px ≈ `$1001BC`) |
| 1 item | `$D2C6` | item `[$D16]` (the icon clicked): picture at (48, 14), "UNITS : nnn" = `+8` (8×8 at column 14, y 96), the name segments (8×8, colour 3), the description (5×5 at x 115, y 14 + 8·line) | palette `$138A` |
| 2 stores priorities | `$DEAA`, render `$1118A` | three items from scroll `[$D38]` (0–12): icon, class box for `+$14`, target `+$16` (3 digits at (134, 41 + 36·i)) | class box `$DF84` (x 80–111, rows from y = 24 + 36·i): 0 → bottom third (+22…+30), 1 → middle (+11…+20), other → top (+1…+9). Clicking a third sets 2 (top) / 1 / 0 (`$10AE6`); 2 is the most urgent |
| 2, keypad | `$112BA` (while `[$D1A]`) | the digits typed at `$D1B` over the target of row `[$D18]`, in a box `$D22–$D28` (colour `[$D2E]`, text `[$D30]`), right-aligned: x = `[$D22]` + 6·(`[$D2C]` − max(`[$D2A]`, 1)); the row box from `$E026` lit in colour 10 | `$10B02` opens it (3 digits); the value goes to `+$16` |
| 3 fuel priorities | `$E0BA` | three class boxes (x 136; ship, aircraft, AAV) for the working copy `$D0E/$D10/$D12` | as sub-page 2; EXIT (`$10BAC`) commits to `$D08–$D0C`, entering (`$10B72`) copies them back |
| 4 stores transfer | `$E1B6` | three items from scroll `[$D3A]` (0–12): stockpile stock `$D78[k]` at (97, 47 + 34·i) and queued for the drone `$D96[k]` at (206, …) | 3 digits; the ± buttons are in §10.4 |
| 5 fuel transfer | render `$14D28` | rows y 49/74/99 (ship, aircraft, AAV): stockpile tanks `$D60/$D64/$D68` as bars at x 44 (n = fuel / `$6905`, 78 px ≈ `$2001E6` ≈ the `$1FFFC0` cap) and queued fuel `$D6C/$D70/$D74` at x 196 (n = fuel / `$3482`) | no numbers |

Sub-page 2 and 3 show the **working** values: EXIT (`$10A3C`) commits `+$14/+$16` → `+$10/+$12`, CANCEL (`$10A66`) and entering (`$109E8`) reload them. The stockpile island's name and the in-transit flag `$D50` aren't drawn on this screen.

**Gating** (`$177DE` every pass, and the buttons `$109E8 $10B72 $10C06 $10D52`): sub-pages ≥ 2 need comms (`[$B0A]` ≠ 0), else "Communications are out". Sub-pages 4 and 5 also need no transfer running (`[$D3E]` = 0, else "Resupply operation in progress") and the carrier at the stockpile island: its island buffer `[$5E996]` (object 10 `+$3C`) ≠ 0 and the buffer's island byte `+1` = `[$D4A]`, else "Stockpile island out of range". On failure the pass handler shows the message, sets `[$D14]` = 0 and re-selects the tab.

Port: `readout_screens.ts` `resourcesSubPage`, `resourcesStoresOnBoard`, `resourcesCarrierFuel`, `resourcesItemPage`, `resourcesStoresPriorities`, `resourcesClassLevel`, `resourcesFuelPriorities`, `resourcesStoresTransfer`, `resourcesFuelTransfer`, `resourcesGate`, `resourcesBar`, `resourcesQty3`, `storeItemName`. Checked (`tools/check_readouts.py resources`, mirror `stscreens_resources.py`): every sub-page's render and panel callback under Unicorn on 14 captures × 13 states (the capture plus 12 perturbed: counts, targets, classes, stockpile and queue stocks, all fuel values incl. negative and overflowing, scrolls, the picture item, the keypad field) against the mirror's strings (position and text), bars (row, x0, x1, colour) and class boxes: 1092 sub-page draws, all equal.

#### Screens 24 / 16: Manta / Walrus arming

The selected bay is `[$DB4]` (Manta) / `[$DB6]` (Walrus); its record `[$48E]` / `[$568]` is set by the enter handler and the bay select. The render (`$15002` / `$14E56`) draws the 3D hangar into the small viewport, then:
- **Panel redraw** while `$95E` / `$960` is set: the sub-panel `$DC0` / `$DC2` from the record's status (`$11738` / `$11E60`: 0 → 0 loadout, 1–2 → 1 repair, 3 and above, `$FF` included → 2), the bottom icons and the three sub-panels, then `$95C` = 2.
- **Carrier tank bar** every frame (`$15200`): the aircraft tank `$D00` (Manta) or AAV tank `$D04` (Walrus), a horizontal colour-2 line at y 192 from x 236, length min(tank `divs` `$3482`, 78) px.
- **Bay list** while `$95C` counts down (2 passes after a redraw), at (106, 159): "MANTA FIGHTERS:" / "WALRUS AAV'S:", then the bay numbers 1–4 and, at (118, 168 + 6k), a status word picked by index = (status byte, sign-extended) + 1, 6 or more → 5:

| Index | Manta | Walrus |
|---|---|---|
| 0 (`$FF`) | DESTROYED | DESTROYED |
| 1 (0) | IN HANGER | IN DOCK |
| 2 (1) | UNDER REPAIR | UNDER REPAIR |
| 3 (2) | FLIGHT PREP | LAUNCH PREP |
| 4 (3) | IN LIFT | IN LIFT (a launching Walrus) |
| 5 (4 and above) | ACTIVE | ACTIVE |

The strings are dot-led (`"........ DESTROYED"`).

The bottom panel (`$C4D4` / `$BFAA`) is icons only: bays 1–4, refuel, defuel, repair, plus to deck for the Manta. A destroyed bay swaps the icons at `$C52C/$C536`: repair becomes replace.

**Sub-panel 0, loadout** (`$C580` / `$BFE8`):

| What | Where (x, y) | Derivation |
|---|---|---|
| "MANTA n" / "WALRUS n" | 205 / 202, 8 | n = bay + 1 (`$C84C` / `$C325`) |
| List item (cursor `$DB8` / `$DBA` over `$C770` = 1, 2, 3, 4, 5, `$A` / `$C242` = 6, 7, 8, 9, `$B`) | icon at (123, 85) / (35, 86) | loadout entry `$CAC4 + 26·code` (below) |
| Mounting text (Manta only) | 35, 99 (lines +6) | entry `+$E`: "SINGLE/DUAL/TRIPLE MISSILE MOUNTING", "CENTRE MOUNTING ONLY" |
| Description | 35, 114 (lines +6) | entry `+$12`, lines split at `$0D`. ACCB: the third line is "POD - " + 8 bytes from `$CAAC + 8·[$DC6]` (RESOURCE / FACTORY / DEFENCE), patched into the code at `$CA6F` |
| WEIGHT | 35, 91 / 35, 105 | entry `+0`: 3 digits with 2 leading zeros blanked, "WEIGHT   ___KG" (Walrus: 4 digits, 3 blanked). Walrus Harbinger: `+0` is patched to 100 + 150·`[$CB80]` first (`$C038`) |
| QUANTITY | 35, 85 / 35, 99 | the item's **on-board stock** `[entry +2]` (item record `+8`), 3 digits, 2 blanked |
| Harbingers to load | 81, 89 | Walrus with the cursor on code 7: the element's descriptor kind (`$416B8`) = entry `+8` = 11 (48-px bitmap + text), text = '0' + `[$CB80]` (`$416CD`). Code 6 has kind 10 (no digit); 8, 9 and 11 kind 1 |
| Fitted items | slots (218, 70) fuselage `+$10`, (195, 90) / (240, 90) wings `+$C` / `+$E`; Walrus (173, 96) payload `+$26`, (201, 96) weapon `+$28` | icons of each slot's entry (`+$A`; ACCB icon + 160·`+$10`). The Walrus weapon slot's descriptor kind = its entry `+8`. A Harbinger (kind 11) shows '0' + `+$C` (byte) at (219, 99) |
| REPAIR STATE | 254, 63 / 254, 66 | health `+$14` as `$EC4A` percent of 4000: (h·100) `divu` 4000; "100" or " " + tens (space if 0) + units, then "%" |
| PAYLOAD WEIGHT: MAXIMUM | 166, 28 / 34 | static 750 / 2000 KG |
| PAYLOAD WEIGHT: CURRENT | 166, 34 / 40 | record `+$12`, 5 digits with 4 blanked, written over the "000KG" of the string, so no "KG" is left |
| Vehicle fuel bar | x 180, from y 88 up | `$7A46` / `$7A56`: min(fuel `divs` `$38E`, 36) px (36 = `$7FFF`), colour 2 over a colour-0 line of 36 (`$546C`, `[$484]`). Manta fuel `+$A`, Walrus `+8` |
| UNBALANCED PAYLOAD | 172, 42 | Manta: \|weight(`+$C` code) − weight(`+$E` code)\| > 80 (unsigned, after `neg`). The same test writes record `+$1C` = `$FF` (blocks "to deck") or 0 |

Loadout entry (`$CAC4`, 26 bytes): `+0` weight, `+2` stock pointer, `+6` quantity taken per fit, `+8` descriptor kind for the Walrus elements (0, 1 = slot 0 payloads, 10 Avatar, 11 Harbinger), `+$A` icon, `+$E` mounting text (Manta), `+$12` description, `+$16` fit handler. Code 0 is the empty slot.

**Sub-panel 1, repair** (`$CBFE` / `$C3A4`): status 1 shows "UNDER REPAIR" (190, 30 / 33), and element type 79 "CANCEL REPAIRS" is enabled (flag `$40F50` = 0). Status 2 shows "UNDERGOING" + "FLIGHT PREPERATIONS" (Walrus: "LAUNCH PREPERATIONS"; misspelt in the original), and type 79 is hidden (`$8000`). Both show "REPAIR STATE :" with the health percent at (256, 48). The prep timer is not shown.

**Sub-panel 2** (`$CCBA` / `$C440`): `$FF` "AIRCRAFT DESTROYED" / "AAV DESTROYED"; status 3 "AIRCRAFT PREPARING" + "TO LAUNCH" (Walrus "AAV PREPARING"); otherwise "AIRCRAFT ACTIVE" / "AAV ACTIVE".

#### Screens 28 / 20: Manta / Walrus status (`$16750` / `$169FE`)

These screens have no panels. Four blocks at (33, 7), (162, 7), (33, 72), (162, 72) (table `$169EE`), one per bay k, with record `$496/$570 + $34k` and object 2+k / 6+k. Lines are 6 px apart; a line is only drawn (and only takes a row) when it applies:
1. "MANTA k+1" / "WALRUS k+1".
2. "STATUS:" + DESTROYED (status bit 7), IN HANGER / IN DOCK (status < 8), else ACTIVE. Only ACTIVE units get the lines below.
3. "POSITION:" x "," z: object `+$A` / `+$12` `divs` `$68E` (1678 world units per map unit, truncated), shown as a sign and the last 4 of 5 digits of the magnitude (`$BCFC`, buffer `$AD3`).
4. "ISLAND:" the name of the island whose 3D buffer the unit is attached to (object `+$3C`, byte `+1` = island index), or NONE.
5. "REPAIR STATE:" health `+$14` percent of 4000, leading spaces skipped ("5%", "100%").
6. "PAYLOAD:" record `+$12` as 4 digits, zeros blanked and skipped, "KG".
7. Weapons:
   - Manta: "LASER" if `+$10` (byte) ≠ 0, then "MISSILES (n)" if `+$C` ≠ 0, with n = '0' + `+$C` (one character);
   - Walrus: "LASER (n)" while `+$A` ≥ 0 (signed; n = Avatar shots, 2 digits, a leading 0 dropped), then "MISSILES (n)" as for the Manta.
8. Payload: if `+$E` ≠ 0, its low byte names it: 4 CLUSTER BOMB (the Quaker), 8 VIRUS BOMB, 9 ACCB POD, `$A` COMMS POD, `$B` FUEL POD. Other values draw nothing.

Fuel, mode, waypoint and telemetry are not shown here.

**Check** (`tools/check_readouts.py vehicles`, mirror `stscreens_vehicles.py`): the screen's render runs under Unicorn (3D calls stubbed: `$7BF6 $6402 $91EC $62D4`), with `$95E/$960` = 1 and `$95C` = 2, so the panels and bay list are drawn. Each drawn string at a data position, the tank and fuel bar lengths (`$5488`/`$546C` hooks) and the Manta `+$1C` balance flag are compared with the mirror. The inputs are 12 captures (units-frame 16/20/24/28; play flight/combat/launch/late/takeoff; views walrus/manta; action) plus 30 fuzzed states each: bay status, health 0…65535, weight, fuel, loadouts, Harbingers, ACCB type, positions, island buffers, stocks and tanks. Result: 1488 states, all match. The TS readers agree with the mirror on 120 fuzzed states.

#### Maps (screens 2 carrier, 17 Walrus, 25 Manta)

**Render** `$14E3C` (same for all three): the slider bars `$7712`, then the map `$65E2`, or, while `[$766]` ≠ 0, the island information box (panel `$4203A` → `$BB10`) instead of the map. The bottom panels' draw callbacks (`$E9B4`, `$E9E6`, `$BF86`, `$BF5E`, `$BF3E`, `$EA12`, `$EA62`) only draw icons and bevel boxes: every number on these screens comes from the render. Readouts: `mapViewState`, `mapToScreen`, `mapIslands`, `mapUnits`, `mapMarkerSegments`, `mapLinksDrawn`, `islandsPanel`, `islandInfoBox`, `mapSliders`, `mapBorder`, `mapCopyright`, `mapCoordText` (`readout_screens.ts`).

**View and transform.** Viewport `$47F2`: (32, 6)–(287, 133), centre (160, 70). Zoom level `[$758]` (0–10), `[$75A]` = 1 << level, centre world point `[$75C]/[$760]`. `$7526`: sx = 160 + f(x − `[$75C]`), sy = 70 + f(−(z − `[$760]`)), f(d) = `$65C4` = (d asr 8) / zoom with `divs`, **floored** (the quotient is decremented when the remainder is negative); a quotient beyond a word gives ±`$7FFF` by the dividend's sign, and an overflowing add of the centre gives `$7FFF`. So one pixel = 256·zoom world units, north (+z) up. (`map_to_world`, `$F91E`, is the inverse used by clicks.)

**Draw order** (`$65E2`): `$764` = `$FFxx` (`st.b`), `[$73E]` = 2000000; clear the viewport; `$750` = min(zoom, 8); the world-edge frame `$68A6` (32 world points `$7564` through `$7526`, line list `$7694`, colour 4); on the carrier map in islands mode (`[$710]` ≠ 0) the links `$68D0` and the islands panel `$699E`; the islands `$6658`; `$6A9E` (copyright text and the fixed centre reticle, line list `$76CB` over screen points `$7664`); then the units of the screen (`$6AFA` 25, `$6C36` 17, `$6D74` 2).

**Islands** (`$6658`, each of the 64 in order):
- The centre offset (mx, my) = f(dx), f(dz) must be within ±255 px (unsigned compare after `neg`), else the island is skipped entirely.
- Nearest: the island with the smallest mx² + my² (< the running `[$73E]`) becomes `[$764]` (index), `[$74C]` (owner), `[$742]/[$746]` (its world x/z). This is the island the INFO button (`$FAF4`) opens and centres on; with none in range `[$764]` stays negative and `$FAF4` does nothing.
- Label (zoom level ≤ 5, unsigned): the name in the vector font, colour 8, at (left − 35/zoom, top − 32/zoom), scale `$400`/zoom (all `divu`). Drawn before the rectangle's clip test, so it can show for an island whose rectangle is off the map.
- Rectangle: corners f(dx ∓ halfX) + 160, f(dz ∓ halfZ) + 70 (word adds), widened to 1 px if empty; skipped when wholly outside the viewport, else clipped and filled (`$5308`) with `$6E90`[owner]: neutral `$B` (`$467`), friendly 7 (`$445`), hostile 1 (`$720`).
- Markers, only at zoom level ≤ 2 (signed) and for a drawn rectangle:
  - carrier map in islands mode: friendly islands only. Kind marker `$B` + (economy `+0` & 3) (base, resource, factory, defence) at the island centre, `$1000` west on the stockpile island `[$D4A]`, which also gets the stockpile marker `$F` `$1000` east. Other islands: no markers.
  - otherwise: structure slot 1 of type `$24` (runway) → marker 9 turned by the slot's `+2` angle; slots 2 and 3 of type 7 (volcano) → marker `$A`; at the island + the slot's `+8/+$A` words.

**Markers** (`$6DFE`): shape `$6E96` + 8·k = (points: count.w, (x.w, y.w)…; line list: count.b, (colour.b, i.b, j.b)…). Drawn when the world point's screen position has \|sx\|, \|sy\| ≤ `$200`; points turned with the `$A6C` (sin, cos) pair (word rotation, high word of 4·(x·cos + y·sin), y·cos − x·sin), divided by 12·min(zoom, 8) (`divs`), y negated, plus (sx, sy); lines clipped to the map (`$5C60`). `[$A88]` = drawn, `[$A80]/[$A84]` = (sx, sy). Shapes: 0 Manta and 1 Walrus (the same diamond, ±80), 2 carrier (100 × 200 box), 3 cursor (corner brackets, colour 15 = the cycling colour), 4–7 bay 1–4 waypoints, 8 carrier waypoint, 9 runway, `$A` volcano, `$B–$E` island kinds, `$F` stockpile; all colour 8 except the cursor. Units are turned to (heading + `$800`) & `$F000` (16 directions); waypoints and the cursor are not turned.

**Units:**

| Screen | Drawn (in order) |
|---|---|
| 25 Manta (`$6AFA`) | per bay k (record `$496 + $34k`, object 2 + k): status `+1` < 0 (destroyed) → nothing; status ≥ 3 → marker 0 at the object, and a label if the draw mode `[$6AA]` = 2, or `[$6AA]` = 1 and k = `[$48C]`, at zoom level ≤ 1 and the marker was drawn: "MANTA k+1" at (sx + (22 >> level), sy − (22 >> level)) and "x,z" (`$BCFC` each) at (sx + (12 >> level), sy − (10 >> level)), vector font, colour 8, scale `$200` >> level. Then, with record flag bit 1 (course set), marker 4 + k at the waypoint `+$1E/+$26`. Then the carrier (marker 2, object 10) and the cursor (marker 3) at `$69E/$6A6` |
| 17 Walrus (`$6C36`) | the same with the records `$570 + $34k`, objects 6 + k, marker 1, mode `[$6B8]`, selection `[$566]`, "WALRUS k+1", waypoint `+$1C/+$20`, cursor `$6B0/$6B4` |
| 2 carrier (`$6D74`) | course mode (`[$710]` = 0): the cursor at `$6BE/$6C2`, the waypoint (marker 8) at `$64E/$652` if `$640` bit 1; then the carrier |

Units on the lift or in the hangar (status 0–2) aren't plotted, but their waypoint is if the flag is set.

**The enemy is never on the map.** No map routine draws objects other than 2–10; the enemy carrier (object 19), enemy aircraft and missiles are not plotted at any zoom, whatever the radar (`$B02` is read only by the vehicle-view radar scope `$146A4` and the defence-drone radar `$15F28`). Enemy activity shows only through island colours (hostile islands) and messages.

**Coordinates** (`$BCFC`): v / 1678 (`divs`, truncated), written as 5 digits with the sign over the first (always 0 in the world's ±`$7FFFFF`): "+0123", "-4415".

**Links** (`$68D0`, carrier map in islands mode only): every network entry (`$2239E`, count.w then (weight.w, island.w)) with a non-zero weight, from both of its islands (each link is drawn twice), between the island centres (`$7526`), clipped. Colour by the owners: both friendly 7; friendly and neutral or hostile `$C` (`$333`); both hostile 1; neutral–neutral and neutral–hostile links aren't drawn.

**Islands panel** (`$699E`, carrier map, `[$710]` = 1; 5×5 at (197, 163)): `$CDF2` first **recounts** `[$D54]` (friendly), `[$D56]` (hostile) and `[$D58 + 2·kind]` for each friendly island's economy `+0` byte (it doesn't clear `$D58`), then prints "FACTORY ISLANDS  nn" (`[$D5C]`), "RESOURCE ISLANDS nn" (`[$D5A]`), "DEFENCE ISLANDS  nn" (`[$D5E]`) (2 digits, a leading 0 as a space; colour 8), "STOCKPILE" (colour 1) and the stockpile island's name at (197, 187), or "NONE!" in colour 15 when `[$D4A]` < 0. The render thus writes simulation state (the island counts) while this panel is up.

**Island information box** (`$BB10`, `[$766]` = 1): opened by INFO (`$FAF4`: `[$764]` ≥ 0 → map centre = `[$742]/[$746]`, `$766` = 1), closed by EXIT (`$FB1A`). Title "ISLAND INFORMATION" (8×8), then 5×5 lines in colour 8 at x 52, y 32 + 6n, label and value on one line:

| Line | Value |
|---|---|
| NAME: | the name of `[$764]` |
| POSITION: | `$BCFC`(`[$742]`) "," `$BCFC`(`[$746]`) |
| AREA: | a = (halfX·halfZ `mulu` / 10000 `divu`) / 250 `divu` (the first quotient sign-extended), shown in tenths: the 5 digits with up to 3 leading zeros dropped and a "." before the last, then " SQUARE KM" (e.g. 18000 × 18000 → "12.9") |
| CONTROL: | `[$74C]`: FREE / FRIENDLY / HOSTILE |
| ISLAND TYPE     : | friendly only: economy `+0` & 3: BASE / RESOURCE / FACTORY / DEFENCE |
| DISTANCE TO BASE: | friendly, not BASE: "RE-ROUTING" while `[$1235]` (and the box ends there); else route `$67B40`[0][i] (VULCAN row): negative → "ISOLATED", else (w << 16) / 1573 (`divu`; w ≥ 1573 overflows and shows 0), leading zeros dropped |
| DISTANCE TO STOCKPILE: | friendly, i ≠ `[$D4A]` ≥ 0: route `[i][$D4A]`, same format |

The box reads `[$764]`, `[$74C]` and `[$742/$746]` as the last map render left them; it doesn't re-pick while open. Production, stocks, buildings and defences are not shown anywhere on the map screens.

**Slider bars** (`$7712`, under the bottom-right panel, 2 px wide, from y 195 up, colour 2 over a colour-0 track to y 159): carrier map (course mode) cruise at x 292: (`[$6BC]` asr 2) + 8, ≤ 36; Walrus cruise at x 292: ((`[$6AE]` << 8) & `$FFFF`) / `$2AA`, ≤ 36; Manta altitude at x 248: `[$6A2]`.l / 97, ≤ 36, and cruise at x 292: `[$69C]` / 7. The Manta cruise clamp tests the stale d3 instead of d0, which is harmless: 250/7 = 35. No figures are printed.

**Easter egg** (`$6A9E`): at zoom levels ≤ 5, "COPYRIGHT REALTIME GAMES SOFTWARE LTD 1987/88" (`$76E4`, vector font, colour 15, scale `$400`/zoom) is written at world (`$79BFFF`, −`$7FD800`), the far south-east corner, when its screen point is within \|sx\| < `$400`, \|sy\| < `$100`.

**Check** (`tools/check_readouts.py map`, mirror `stscreens_map.py`): the render `$14E3C` under Unicorn on 9 captures (units-frame 2/17/25, play late/combat/flight/supply, sim enemy AI, action) as screens 2, 17 and 25, plus 30 perturbed states each (`--fuzz`) (zoom level and centre, owners, kinds, stockpile, islands mode, route words, `$1235`, unit statuses, flags, positions, headings, waypoints, cursors, draw modes, selections, sliders), and the info box on every third state (opened as `$FAF4` does): the vector labels (text, position, scale), the 5×5 text, the filled rectangles, every line segment drawn through `$5C60` (border, links, all markers), the slider bars and `[$764]` all match the mirror (1113/1113 states at the default fuzz, 2859/2859 with 80).

### 10.8 State the renders write (`render_effects`)

`screen_dispatch` (`$14472`, after every pass) runs the screen's render from `$144A0`, which also runs some panel callbacks (`$B418`). The renders write simulation state that the next passes read, so the app must reproduce it: it doesn't run the original render. For example, the 3D views put their camera object's island buffer in `+$3C` (object 0 takes the carrier's on the bridge), and `island_behaviours_step` sets `$A43` ("camera at this island", `$19650`) from `[$9FA]` `+$3C`, which changes the island's behaviour and its RNG draws. A new game has object 0 `+$3C` = 0 until the first bridge render. `RenderMixin.render_effects(screen)` (`tools/stunits_render.py`, `units_render.ts`) ports every state write. The app calls it once per loop for the screen shown, after `game_frame` and before `frame_flip_flags` (under the debug freeze, `[$1189]` with key `$866` held, `game_main` skips both).

| Screen | Writes |
|---|---|
| all, debug mode | `$210F0` consumes key `$871` bit 0; `$17874` (running game, not the menu) consumes key `$869` bit 0 and flips the frame-rate display `$122D` |
| 2, 17, 25 maps | `map_render_effects()`: `$764` (nearest island, `st.b` first), `$73E` (its distance², or 2000000), `$74C` (owner, byte), `$742/$746`, `$750` = min(zoom, 8); with the islands panel (`[$710]` ≠ 0, screen 2) the recount `$CDF2` of `$D54`, `$D56`, `$D5A–$D5E` (and `$D58` + 2·kind, which isn't cleared); the marker turn pair `$A6C/$A6E` (screen 2: the carrier's; 17/25: (0, `$4000`)). Nothing while the info box is up (`[$766]` ≠ 0). See "Maps" above for the nearest rule |
| 4 messages | `$966` − 1 while ≠ 0 (the log's expiry is the app's) |
| 11 defence radar | drone elements 66–69: flag bit 15 (hidden) unless the drone is out and `[$B02]` ≠ 0 |
| 0, 24 bridge, Manta arming | object 0: the carrier's pitch, yaw, roll and `+$3C`, x/z 100 behind the carrier origin (the `$A6C` pair = carrier yaw on the way); the 3D view. 0: the radar record's `$4810` = `[$8B2]`, then the radar overlay (while `[$B02]`). 24: the arming redraw (below) |
| 16 Walrus arming | object 0: `+$3C`, pitch, roll from the carrier, yaw = carrier yaw + `[$DC8]`, x/z 1000 astern; the 3D view; the arming redraw |
| 26 / 19 deck / dock | object 0: x/z = carrier + the turned (±420, ±1050), yaw = carrier yaw + `[$DDE]` / `[$DEA]`, `+$3C`; the 3D view; the bay countdown `$95C`. 19: while the countdown is still running after its decrement, `$A6C` = (sin, cos) of −carrier yaw |
| 9 flares | object 0: `+$3C`, x/z 1000 astern, yaw `[$115A]`, pitch `[$1158]`; the 3D view |
| 10 viewing drone | the 3D view from `[$9FA]` (the drone) while object 15 is live |
| 1 damage | the 3D view of the model list (`$64CFE`) while `[$B62]` = 0 |
| 8 turret | working (`[$B12]` ≠ 0): `$478E/$4790` = `[$6D2]` + 8; object 0 (`+$3C`, pitch `[$6CA]`, yaw carrier + `[$6CC]`, carrier roll, the eye: pairs `$A70` = −`[$6CC]`, `$A68` = `[$6CA]`, `$A6C` = carrier yaw); the 3D view. Always: model 84 (the turret wireframe) leaves the pairs (`[$6CA]`, `[$6CC]`, 0). Broken: **one `rand`** (static) |
| 18 Walrus | while `[$70E]` and the Walrus is live, unless telemetry `+$2C` = 2: its visible bit cleared; object 0 = the Walrus + 45 up (yaw + turret `+$26`), `$9FA` = object 0; the 3D view (rear view: yaw + `$8000`, pitch negated, restored). Guiding a missile (record bit 4): the Walrus visible again, `$9FA` = the missile, hidden for the inset's collection and shown again; inset viewport `$4834` (`+2`, `+6`, `+$E`, `+$12` from the hold `+$2E`); `$6FE` = `$4744`. `+$32`: the radar overlay. Telemetry ≠ 0: one `rand` |
| 27 Manta | while `[$70C]`, the record not destroyed, the Manta live, unless telemetry `+$2E` = 2: the 3D view from `[$9FA]` (rear view: the Manta's yaw + `$8000`, pitch and roll negated, restored); not in rear view, the sight `$1162/$1164` (weapon 1: rudder and −pitch asr 3; weapon 0: 0). `+$32`: the radar overlay. Telemetry ≠ 0: one `rand` |
| 33 title | one `rand`; the 3D view of the model list; the model's turret `$6CA/$6CC/$6D4`; the shown model's yaw (`$64D16`) − 500; Control+A/B set `$124C`; the attract wait `$93C`/`$93A` and its expiry (back to the title, §10.2); debug keypad +/- (and they step the model's type `$64D12` through 1–`$49`) |

**The 3D view** (`view_3d`: `scene_collect` `$7BF6` + `scene_draw` `$62D4`, from the camera `[$9FA]`; docs/memory-map.md "Bridge view camera and projection" is the pipeline):
- The camera pairs `$A74–$A7F`. With an island buffer at `+$3C` (an island buffer, or the model list) holding entries: `$A4A/$A4E/$A52` = its origin − the camera. With its plateau (entry 0) inserted, the partition rectangle `$A56–$A65` and `[$8A6]` = camera y ≥ `$50`. While that is set, the runway entries 1…n (n = header `+$10`) get bit 2 of `+1` cleared, then set, with their camera X, Y, Z in `+$1E/+$22/+$26`, when they pass the cull. `$8A6` keeps its value otherwise: the draw reads it.
- The draw walks the depth tree back to front. Each object's model (LOD by Z − `[$A94]`, `$7B72`) runs its basis routine, which leaves the object pairs `$A68` pitch, `$A6C` yaw − camera yaw, `$A70` roll (`$A78C` boxes and their users). The carrier models with turret (`$AA78/$AABE/$AB42`) leave (`[$6CA]`, yaw + `[$6CC]` − camera yaw, 0); `$AC16` (the Walrus) yaw + `+$26`; `$AF4E` roll 0; trees, the plateau, overlays and empty models nothing. So the pairs after a frame are those of the **last box-based object drawn**. A plateau drawn with `[$8A6]` set is followed by its runway entries with bit 2. The carrier models 2 and 3 draw the lift `[$6D6]` and dock `[$6DA]` vehicles inside their BSP. With both present, their order follows the BSP root's plane (model 2 plane 0, the deck; model 3 plane 3, midships), from the model's camera-space vertices (`_carrier_plane`: the hull box, its Q15 list, lift vertices, the turret boxes, the vertex ops, the `$9902` test, all ported exactly).
- The radar list (`$5892A`, built while `[$1230]`) is scratch, except that the overlay `$146A4` sets `$A6C` = (sin, cos) of the camera's yaw when it holds the island plateau (type 8) and X and Z divided by the scope's scale fit words.

**Bay status and arming redraws** (`$95C`, `$95E`, `$960`, region "bay redraw countdowns"): the deck, dock and arming renders count `$95C` down. 16/24 redraw their panels once `$960`/`$95E` is set: they clear it, set `$DC2`/`$DC0` from the bay record (`$11E60`/`$11738`), run the loadout panel's callback (`_walrus_panel`/`_manta_panel`, §10.4), and set `$95C` = 2. Their writers are ported where they occur: the hangar timers `$1761C/$176AC` (`$95C`, `$95E`/`$960` = 2), the vehicle-destroyed routines (`$95E`/`$960` = 1), the hangar commands (repair, replace, prepare, to deck/hangar, take off, land, launch, dock), the Manta landing and take-off from the deck, the Walrus launch and docking abort, the lift's arrival and return, and the deck-drone hit. The damage screen's input (`$1747A`) turns the diagram (`$64D16` + `$100`) and moves the highlighted lift part. `cmd_select_system` (`$FB3A`) moves the system highlight (visible bits of the model list's entries from `$64D50`).

So the renders advance the game RNG on the title and on screens showing static (later random numbers change, e.g. the layout of the next new game). They also give the island behaviours their camera context.

**Checks** (`check_units.py`):
- `check_render_effects`: `$14472` under Unicorn on the units-frame captures, 600 captures switched to random screens, and the views captures (`tools/capture_views.py`) perturbed 40 times each by `stview.perturb_view` (carrier and vehicle attitudes and places, turret, cranes, rear view, the missile inset, lift and dock vehicles). Every state region after the render must equal the model's; only the element flags 79/81 written by the arming panels' draw callbacks are left out. Each BSP root-plane flag the original computes for a carrier model 2/3 is also compared with `_carrier_plane`. 5003 renders and 1131 plane flags, all equal (the original faults on 372 perturbed states, which are skipped).
- `check_render_loop`, **the whole loop**: the model's `ui_frame`, `game_frame`, `render_effects` and `frame_flip_flags` against the original's `game_main` loop with the render running. Only `frame_flip`'s VBL waits and the game-over key wait are skipped, and Unicorn's `trapv` is emulated. Every state region is compared after every pass (the display timers `$952`, `$962`, `$966` excepted), across 117 runs and 23,000 passes:
  - Hatari new games; model new games from the title image (strategy and action, checked against `$42A8` + `ui_frame` first); the title screen;
  - play, action and views captures, and views captures perturbed by `stview.perturb_view`;
  - the modules' perturbed scenarios, switched by clicks to a 3D view.

  Every run switches screens by clicks now and then (every 3D view, weighted towards them). Between passes it issues random vehicle and war commands, rear view, turret and number keys. Exercised: every screen, the inside/outside island partitions, runway entries, debris shards, both lift and dock vehicles (and both BSP orders), the missile inset, rear views, radar overlays, the dock countdown pair and the arming redraws. All equal.

## 11. Save format

**Disk:** raw sectors (`$211A6` reads, `$212A0` writes). Sector 0 is a header with `GMDSK!`. There are **4 slots** of 190 sectors (slot k from sector 199k + 1).

**Save:**
1. Copy the globals `$400–$124B` and the flag table `$40EB2–$40F85` to `$70000`.
2. Write RAM **`$5DCDA–$758D9`**: objects, island buffers, debris, the slot order, pools, economy, routes, owners and islands, plus the globals copy.
3. In an action game, add `$2000` difficulty.

**Load:**
1. Read the slot back.
2. Check `$70000` against the payload header `4EF8 405A "Atari Bundle Version 16/02/90"` (version stamp).
3. Restore the globals and flags.
4. Refilter the network (`$4288`).

**Not saved:**
- the RNG `$1236`;
- the route-pending flags `$1234/$1235`;
- the network link bits (re-derived);
- the tree quota row `$18EE9`;
- the command-centre max health `$18D74`: not re-applied on load, so after a cold boot it may stay 0 *(to test)*;
- the enemy's coin `$22C1E`.

**This is the authoritative game state as far as the game is concerned.** The port's save format should cover the same data, plus the items above that the original leaves out.

## 12. Bugs and quirks (unit layer)

- **Airborne fighter count:** `$11CE` is never incremented, so the 4-fighter limit doesn't work.
- **Debris recipe bound:** `$1CAB4` compares with memory `$2E` instead of `#$2E`.
- **Carrier box against island objects:** in `collide_island_objects`, the rotation call leaves cos(angle) in `d3`. The along-hull limit therefore becomes cos + 1000 (up to 17384) instead of radius + 1000, so carriers hit island objects (drones, SAMs) far beyond their hull. The object-pair test reloads the radius and is correct.
- **Destroyed buildings on non-enemy islands:** the "not an enemy island" branch lands on the "store health" code. The destroyed entry gets `d1` as its health and is written back to its slot again. By then `d1` holds the last debris shard's z offset.
- **Debris branch patch:** `$1CB54` rewrites the `bpl` at `$1CBF4` into `bra` when the burst is within 50 of the ground, so low explosions always throw their shards upwards. The patched code byte is simulation state.
- **Debris recipes:** the bound check reads the exception vector word at `$2E`, so destroyed constructions (types 48–57) use whatever bytes follow the table.
- **SSM life:** firing a surface-to-surface missile (`$13598`) writes `$FF` to the *carrier's* `+$1B` (`a4` still points at it). The missile's life starts at 0 and the plain `subq` wraps it to 255.
- **Enemy losing contact (`$1B18E`)** reads the island owner at `$69B40` + the low word of the enemy carrier's x (a leftover register), not an island index. The outcome depends on that arbitrary byte.
- **Enemy retreat speed** has its operands swapped: it sticks at 248 instead of reaching 250.
- **Fighter launch** resets pair schedules with a stale `$6EE`.
- **Supply drone without an island:** in state 5, `movea` doesn't set flags, so only the carrier's buffer is tested. The drone's box test then reads address 0 (the exception vectors) as its island.
- **Fighter target search:** a player carrier within 5000 of a fighter gives a negative distance, which the caller treats as "no target".
- **Runway landing:** a `clr.w d0` meant to zero fields also clears the low word of dx, so that pass's bearing is wrong.
- **Carrier landing** step 1 ends when the *sum* of two velocity components is 0, so opposite residual velocities end it early.
- **Lost telemetry** makes a Manta fall but destroys a Walrus outright, with no debris.
- **The Manta panel redraw** (`$C640`, triggered by refuel/defuel) rewrites record `+$1C`, which is the auto mode in flight: a UI routine changes simulation state.
- **Walrus guided-missile fire** does `andi.w #$3F` on the record, clearing the whole flag byte.
- **Options panel:** its element is 78 pixels high, so below the four option rows a click in a fifth row writes `$1246 + 4` = `$124A`, the strategy flag (nothing is drawn there *(guess)*).
- **Stockpile re-shipping** (`$21D46`) writes carrier-pool entries from the first one on, without looking at what is there or at the pool's end.
- **Manta bombs** take their velocity from the Manta's pitch and heading. **Walrus laser bolts** start at the turret but copy the hull's angles.
- **Loadout codes are trusted:** the arming panels index the item table `$CAC4` with the record's slot words unchecked (`mulu.w #$1A` + `adda.w`). Only out vehicles hold other values there, and the arming screen never shows them the loadout panel. Forced (a soak run reached it before the model's gates, §10.6: Manta 0 back at status 0 with `+$10` = `$FF05`), the panel draw `$C580` takes the fuselage icon pointer from `$B146 + $A` in code (`$D459D659`, odd: an address error when the sprite is drawn, `$B2AC`); a drop onto that slot (`$113A4`) stores a weight `+$12` reduced by the code word `$6100` and then adds to the "stock" at `$A2F6225F` (odd: an address error at `$11426`). Checked under Unicorn from the soak's state. The model raises `NotImplementedError` there.
- **Two Walrus paths corrupt the stack and crash the ST.** The model deviates on purpose: it records a `("deviation", …)` event, and `check_units.py` ends a run there instead of comparing.
  - *Guided missile with no free object* (all of objects 24–63 live). `$1FF0E` `beq`s to the `rts` at `$1FB6C`, but a1 was pushed at `$1FEFA`, so the CPU "returns" into the Walrus record (`$570 + $34·k`) and executes it. Its first word, flags·256 + 8, is an illegal instruction unless the turret bit 5 is set; then the rudder and speed words run, soon reaching a line-F or illegal word. In Hatari (EmuTOS) the exception handler then runs into the game's memory (illegal instruction at `$FD2`) and the pass never finishes: the machine hangs. TOS 1.0 would show bombs at best: the game has overwritten its system variables. **Model:** nothing is fired, as with the other weapons, and the cooldown `$942` = 15 stays.
  - *Auto mode ≥ 4* (`$1E078`): a `bsr` to the loop end, which pops the return address's high word (1) as the bay counter. The loop runs one more bay (bay 4 is the carrier with the globals at `$640` as its record), then `rts` returns to `$E07C000n` (n = 3 − bay). On the 68000 that is `$7C000n`: an address error for odd n, a bus error otherwise. In Hatari: "Address Error reading at address $e07c0003", then a bus error inside the handler, and the machine hangs. No code sets a Walrus mode above 3 (dock 0, launch 2, autopilot 3), so only a corrupt save could reach it. **Model:** the bay idles, as in mode 1.
- **Collision distance:** `collide_objects` (`$1BB40`) negates the x and z offsets based on the sign of their *low word* (the flags come from the `move.w` to `$AB0`/`$AB2`). An offset like −40000 (`$FFFF63C0`) is left negative and counts as near, so far-apart pairs are re-tested every pass. The model reproduces it.
- **Command-centre health after a cold-boot load:** probably 0, since `difficulty_apply` isn't re-run *(to test)*.
- **Frame-rate-dependent game speed:** see §1.
- **Defences need the viewer:** most island defences only act while the viewed unit is at the island (§8.1).
- **`w` in the Hatari debugger:** values are decimal unless prefixed with `$`, so `tools/capture_sim.py`'s `w $889 ff` is a harmless no-op.

## 13. Open questions

- The effects of Radar (system 1) and the exact uses of `$B22`/`$B2A`.
- Flag bits 3 and 15; Manta/Walrus flag bits 5–7; `+$32` (a toggle); `$8F1`; `$640` bit 7; `$118A`.
- Object 1. `+$1E`/`+$26` on non-vehicle types.
- Supply-drone arrival (how transfers reach the on-board counts); `$21D46`'s role when the stockpile moves.
- Exact Manta crash and terrain damage values (`$1DC40` callers); whether weight `+$12` affects performance.
- `$2092A` (second sound pass); the `$460`/`$468` modal handlers.
- The viewer rule for defences and the cold-boot command-centre health: both need Hatari tests.

## 14. Status of the model

`tools/stunits.py` (`UnitState`, extending the strategic `WorldState`) models a **whole simulation pass**: `UnitState.sim_pass()` corresponds to game_main `$40B0–$413E`. It leaves out the screen-input, sound and message-line routines. Sound and the message line write only UI memory (checked by recording their writes); `screen_input` is ported separately and runs from `game_frame()` (§10.6), the whole loop around `sim_pass`. Ported:

| Area | Routines |
|---|---|
| Vehicles | `vehicles_step`; `carrier_step` (speed governor, autopilot, grounding, recoil); the Manta/Walrus loops for bays in the hangar; the laser turret off its screen; crane and observation-turret angles `$1F070`; flares and object 15 `$1F214`; idle supply transfer; idle lift; dock door |
| Debris and drones | `debris_step`, `free_burst`; `defence_drones_step`: stations, routing round the carrier's box, positions |
| Island defences | `island_behaviours_step` and every island behaviour: AA turret, SAM launcher, SAM, command centre, drone pad, island drone (fly, search, attack, return, land), anti-ship site, volcano and its rocks, the two dish types; the hostile search and aiming |
| Weapons in flight | every AI object: laser bolt, homing missile (flare decoys, lost targets), Walrus-guided missile, cruise missile, bouncing bomb, virus bomb, anti-ship missile, explosion blast, and the enemy fighter (cruise, search for units or buildings, attack with homing missiles, return and land on the enemy carrier) |
| Hits | `collision_response` and all 30 hit handlers: carrier damage by hull position, enemy carrier strength, Manta/Walrus damage, island drones, SAMs, rocks, trees, buildings (score, difficulty, write-back to the slot), command centres (island turns neutral), constructions, virus bombs (capture), blasts, flares, deck drones; `difficulty_add`/`difficulty_apply`; the score |
| Debris | spawning from the per-type recipes, including the self-modifying branch |
| Objects | `init_world_object`/`init_island_object`, schedule resets, `find_free_object`/`find_free_island_object`; `object_passes`: island attach/load/release, both collision passes with time-to-contact scheduling and the contact tests |
| Per second / 10 s | `island_range_check`; the hangar repair and preparation timers |
| Other | `carrier_systems_status`; the island step's 3D buffer mirroring and entry creation; trees cut down by a new building or builder on the loaded island (`$18B50`: debris, entry removed) |
| Maths | yaw/pitch rotation, rotation matrix, atan2, `aim_angles`, `turn_towards`, `self_centre` |

| Vehicles out of the hangar (`stunits_vehicles.py`) | the Manta loop (telemetry and comms relay, manual flight, ground contact, crashes, fuel, all 7 auto modes: carrier landing, deck, take-off, waypoints, runway landing, landed, runway take-off) and the Walrus loop (telemetry, driving, guided-missile steering, beach ramps, docking, launch, waypoints); Manta and Walrus firing (laser, missile lock-on and locked missile, bomb, guided missile, cargo, ACCB, fuel pod); the lift and loadouts; destruction; 16 hangar commands (refuel, defuel, repair, replace, prepare, to deck/hangar, take off, land, launch, dock) |
| Enemy close combat and supply (`stunits_enemy.py`) | enemy states 10 and 11 (stern to the player, cruise missiles, fighter launches, retreat, losing contact); the carrier's destruction and the game after it (§9); the supply drone's 16-state transfer, the transfer command and the drone's destruction |
| Carrier weapons (`stunits_carrier.py`) | the laser turret on its screen (aiming, firing, heat); commands: flares, recon drone, surface-to-surface missile, defence drone launch and recall |
| Controls and game flow (`stunits_controls.py`) | the carrier's bridge, map and damage-screen commands, pause, raw input injection, screen switching (`ui_frame`, tab and screen icons, every enter/leave handler, game-status and continue buttons), the player's control of Mantas and Walruses (every click handler of screens 16–19 and 24–27 and of the Walrus/Manta maps: selection, weapons, autopilot, throttle, steering and firing, courses, abort landing/docking, arming with loadout drags and the panel callbacks that write state), the weapons (laser turret, flares, viewing drone and SSM, defence-drone stations and formations), the resources screen (priorities with the keypad entry, stores and fuel queues, resupply abort), the stockpile island, the message log, options and the title's new-game buttons, `screen_input` for every screen (with the number keys and the missile lock timer), and `game_frame` (the whole `game_main` loop without the UI: keys, paused frame and cheat code, debug keys, end timer and end request) (§10.6) |
| New game (`stunits_setup.py`) | the new-game routine `$42A8` for both game types and the first screen change after it (§16) |
| Renders and sounds (`stunits_render.py`) | `render_effects(screen)`: the state every screen's render routine writes, the 3D views' camera, pipeline and overlay writes included (§10.8); the sound events (§17) are emitted by the modules above and checked here |

The model is split into `UnitCore` (`tools/stunits.py`) and three mixins, one per row above. Each module lists its routines, scenarios and command checks for `check_units.py`.

**Not ported** (the model raises `NotImplementedError` instead of guessing):
- Paths that no game state reaches. They keep their `NotImplementedError`, and each message says why it's unreachable:
  - Manta runway modes 4–6 without an island buffer: they start from the attached island's runway, and the Manta stays attached there;
  - a Walrus guided-missile pointer other than 0 or an object;
  - Walrus payload or loadout codes above 11, and an unknown loadout handler: the arming screens store codes 0–11, and the item table is constant;
  - lift state above 3, and a Manta on the lift (`$DD0` ≥ 0) with no lift object `$6D6`: every write to `$DD0` is followed by `$1DC6C`, which sets `$6D6`.
- The save, load and format panels (`$10228 $10230 $102BE`, the title's load menu `$103C4 $103D2 $103DE`): the app has its own saves. The message log itself (`$1148`, `$65D10`, written by `$A128`) is left to the app (messages are events).
- The debug screen dump (Control + F-key while paused in debug mode).
- UI-only effects: the pointer sprite and clip (`$46E–$47E`), redraw timers, on-screen messages and sounds (both recorded as events, §17).

**Test data** is an **action game**, which needs no input. The carrier sails a few thousand units on autopilot to a waypoint off enemy island THERMOPYLAE (27) and stops: `capture_sim.py --action --steps 20 300 1500 --ticks 0 --passes 40 --out emu/out/action` captures RAM around 120 whole passes. Under Unicorn, a whole pass reproduces the Hatari capture exactly, apart from interrupt-driven counters and the stack.

**Island 27 never engages** in that game. The carrier stops about 35,300 units from the island's centre, outside the 16-bit island-local range, so its "in range" flag (bit 9) is never set. The engagement scenario below moves it closer.

**Real play** (`tools/capture_play.py`): a scripted session in Hatari clicks the actual panel buttons, as a player would.
1. Send Manta 1 to the deck and launch it, with its waypoint poked onto island 27; it's shot down by the island's defences.
2. Launch Walrus 1 towards the island.
3. With island 27 made the stockpile and 4 Assassins queued, start a supply transfer; the drone completes it.

The session captures 300 passes across these phases. All 300 match the model byte for byte, both against Hatari and against the original under Unicorn. Two 4000-pass model-against-original runs from the take-off and the transfer also match, the first through the Manta's destruction.

**Checks** (`tools/check_units.py`, about 25 s):
- the maths against the original on 3000 random inputs each;
- each routine on the 120 captured states, with 2000 fuzzed states for `carrier_step`;
- whole passes against the original and against the **Hatari** `pass_*_out` captures: all 120 match;
- 4000 consecutive passes from the start of the voyage against the original (screen input stubbed, the one-second flag every 8 passes): identical, including an enemy capture along the way;
- every command (Manta, Walrus, supply, carrier weapons) against its original handler on 240 perturbed states, and 3000 single calls each of the supply step and enemy states 10/11;
- the Manta missile lock-on on screen 27 captures (`emu/out/views`, made by `tools/capture_views.py`) with 0–5 lockable objects placed around the sight (near, far, Z ≥ `$8000`, exact ties): the original renders the screen (`$15244`, which builds the tree and the sight), then 200 calls of `$1FFEA` and 200 whole passes with the fire key down, against the model from the rendered state;
- 300 perturbed multi-pass scenarios, including Manta flight, weapons, carrier and runway landings and the lift, Walrus driving, weapons, docking and launch, ACCB drops on a neutral island (often onto trees), enemy close combat and retreat, supply transfers, and the turret screen;
- **the enemy carrier destroyed**, end to end (`check_enemy_destroyed`): six games, by the bombardment's return fire at a player-held NEMESIS and by a cruise-missile blast with its fighters airborne, through screen 35 and YES (3000 passes on without the enemy) or NO;
- **capturing an island**, end to end (`check_accb_capture`), three runs (ACCB types 0–2) from the play session's `pass_flight_0_in`. Island 27 loses its command centre and turns neutral; Walrus 1 drives on autopilot up the beach to a spot inland; the player takes over and drops the ACCB onto a planted tree; the builder grows until the island is the player's (pass 1606), then 100 more passes. All about 1700 passes match the original, with the drop, the tree cut, the capture and the island kind checked;
- the original 160 scenarios:
  - **weapons:** every AI-object type in flight around the carrier and the island with live targets, and enemy fighters raiding a player island's buildings;
  - **island defences:** an air raid (Manta objects hovering in range, so AA turrets, SAM sites and island drones engage), a bombardment of the island's buildings, volcano eruptions, island drones returning or losing their pad, and a player-held island firing anti-ship missiles at the enemy carrier;
  - **other:** engagement of the carrier, the carrier's defence drones (stations and launch), flares, turret, voyage, debris, collisions, hangars, construction.

  All match. The only stop left is the voyage scenario's artificial teleport (depth divide overflow).

## 15. Plan: modelling and verifying the unit layer

The approach is the same as for the strategic layer (`tools/stsim.py`, `tools/check_sim.py`), with three additions:
1. **Captures from real play.** Driving Hatari needs control input: `tools/capture_sim.py` pokes the mouse and buttons. The vehicle controls (§10) are plain RAM variables, so a capture script can launch a Manta or Walrus, set throttle and steering, and fire, by writing those variables at chosen passes. Capture RAM around single calls of each per-pass routine, as for the island step.
2. **Port in dependency order:**
   1. helpers (`velocity_from_angles`, `turn_towards`, the sine table, atan2);
   2. the object store, `init_world_object` and `find_free_object`;
   3. `object_passes` (attach, collision scheduling, contact tests, `collision_response` and the hit handlers);
   4. the AI objects;
   5. island behaviours;
   6. vehicles (carrier, then Walrus, then Manta);
   7. enemy states 10/11;
   8. debris (cosmetic, but it uses the RNG, so it matters for byte-exactness).
3. **Extend the state regions** with the object list, island buffers, debris, the bay records and the carrier/vehicle globals. The save format (§11) is a ready-made list.

With the unit layer ported, `WorldState.frame()` becomes a full pass of the game. The model then needs input injection (the control variables) instead of the stubs used so far.

## 16. Starting a new game (`$42A8`)

The title menu's buttons set `[$124A]` (**set = strategy**, `$103B2`; clear = action, `$103A0`) and call `$42A8`. It runs inside `ui_frame`, so the rest of that `game_main` pass follows straight away: `ui_frame` takes the screen request, and the simulation block runs (`[$462]` = `$FF` and `[$461]` = 0 come from the snapshot).

| Step | Routine | State written |
|---|---|---|
| 1 | `globals_restore` `$1A518` | `$400–$1235` and the panel flags `$40EB2` from the boot snapshot at `$4BCCA` (`$1A4C6`). The RNG `$1236` is outside it |
| 2 | `session_reset` `$4206` | mouse, buttons and key table (`$17EC6`, `$17EAE`), tick count `$97A` = 0, all pool entries free (`$218D8`), debris idle and shard free list (`$1C6E2`), pointer reset (`$F712`), `$457` set. UI only: sound, message log, MFP |
| 3 | `$4260` | `$8F0` in game, `$1230` clear, `$1231` set, `$463` clear |
| 4 | | screen request `[$1227]` = `$FF`, tab `[$1228]` = 1 |
| 5 | action only | waypoint `$64E/$652` = (`$FFE66600`, `$FFE77748`), cruise `$656` = `$70`, `$640` = 7 (autopilot to the waypoint, announce), `$6C6` = `$FF` |
| 6 | | difficulty `$444` = 0 (strategy) or `$2000` (action); `difficulty_apply` |
| 7 | | AI on (`$464`); `$457` cleared, then set again by `frame_flip` if `[$458]` (the snapshot has 0); `$458` set |
| 8 | `init_islands` `$18370` | below |
| 9 | `count_islands` `$CDF2` | `$D54–$D5E` |
| 10 | `$55BC` | 3328 random words at `$5C2DA`, the static-noise texture drawn by `$5540`. Not simulation state, but it advances the RNG |
| 11 | | `$8FD` set: the first pass runs a logistics tick |
| 12 | `object_store_init` `$43D2` | all 64 objects cleared, `$A48` = 64, island buffers not in use, bay indices `+$1A` of objects 2–9; the carrier (object 10, type 1) and the enemy carrier (object 19, type 4) at y = 160 from the start table |
| 13 | `network_invalidate` `$216C6` | link filter, route matrix reset, relaxation pending |
| 14 | | map views `$75C/$760` = carrier x/z, the three map cursors `$69E/$6A6`, `$6B0/$6B4`, `$6BE/$6C2` = carrier + 1000, and `$758–$767` copied to `$768`, `$778`, `$788` |

**`init_islands`** (`$18370`):
1. The slot order `$66690` gets 0–31 (`$18924`); owners and economy records are cleared.
2. Each island is filled from its 22-byte record at `$4AFB6`: x, z (word × 512), half-extents, area = hx·hz >> 16, the economy capacities (2 × area) and the three `$FF` bytes. All 32 slots are cleared, then slot 0 is the plateau (type 8), slot 1 the runway (type `$2F`, with the record's heading) when the record has one, then up to two volcanoes (type 7, random heading).
3. `$1897E`: 21 trees per island, from island 63 down, at `find_free_island_spot` positions (the call repeats until it succeeds); type `$18` + (rand & 3).
4. `$189CA`: the start list's command centres under construction: type `$34`, progress `$3B`, start code in `+6`. If the island were full (result −2) the game would `trap #7`.
5. `world_warmup` (601 fast-build steps).

**Start tables** (20 bytes: carrier x.l, z.l, heading.w; enemy x.l, z.l, heading.w; the start list follows):

| Game | Table | Carrier | Enemy carrier | Start list |
|---|---|---|---|---|
| strategy | `$18FCE` | (−7,409,664, −7,195,440), heading `$3E8` | (6,990,336, 4,164,304) | `$18FE2`, 9 islands |
| action | `$19008` | (−1,677,824, −1,612,864) | (−276,722, −3,414,010) | `$1901C`, 52 islands |

**The first screen.** `ui_frame` then takes the request: tab 1, whose last screen `[$722]` is 0 (the bridge). The title screen (`[$718]` = −1) has no leave handler. The bridge's enter handler (`$13C24`) writes simulation state: the camera `$9FA` = object 0, the viewed and controlled units `$9FE/$A02` = the carrier, object 0 flags cleared, y = 240 and island buffer = the carrier's, `$A46/$A47` cleared, the redraw flags, and the autopilot button's panel flag `$40F66` (`$12C16`).

**RNG.** The Timer A sound interrupt (`$20668`) also advances the RNG, but no sound plays during `$42A8`: in Hatari, the original's result from the RAM at the click matches the Unicorn run exactly. The world a game gets depends only on the RNG at the click, which the title screen keeps advancing (its static-noise drawing `$5540` calls `rand`). *(The same drawing may run during play, where it would advance the simulation's RNG from the renderer; not traced.)*

**Port** (`tools/stunits_setup.py`, `web/src/sim/units_setup.ts`). The app:
1. loads a title-screen RAM image (`emu/out/title/ram.bin`: 1500 VBLs after `game_main`) with `UnitState.from_ram`;
2. seeds the RNG longs `$1236` and `$123A`;
3. calls `new_game(strategy)`, which writes `[$124A]` itself, then `new_game_screen()`;
4. then runs `sim_pass()`. The original runs the first pass in the same `game_main` iteration.

Everything else after `$42A8` is UI: the screen clear, "Creating...", panel drawing, the clock `$8F2–$8FA` and the in-game flags `$8F0`/`$461`/`$462` (restored from the snapshot: running, not paused). These aren't simulation regions; the app sets its own.

New regions: the strategy flag `$124A`, the map view copies `$768–$797`, and read-only inputs (the snapshot `$4BCCA`, the island records, and the tail of the start lists past the quota table, `$19078–$190ED`).

**Checks** (`check_units.py`, the `stunits_setup` CHECKS):
- `new_game` then `new_game_screen` against `$42A8` and then `ui_frame` under Unicorn (only `frame_flip`'s VBL wait is skipped; the UI routines run) from the title image: both game types, 8 seeds each, all regions;
- 3000 passes of the model from its new game against the original's passes, two seeds per game type;
- against Hatari (`uv run python tools/stunits_setup.py` captures `emu/out/newgame/`): from the RAM at the click, the state when `$42A8` returns, at the first pass and after it, for both game types.

All match.

## 17. Sound triggers

This section lists every place the original starts a sound. The model emits an event at each one (below), for `web/src/audio/sound.ts` (`play(id)`, `explosion(level, x, y, z)`, `gameOver()`). The engine itself is in `docs/memory-map.md` "Sound".

**How the calls work**
- `jsr $20B90` + id plays an effect-table entry. Every call site checks the sound-effects option `$1249` itself; the routine doesn't.
- `$20C30` (object A1: its longs `+$A/+$E/+$12`) and `$20C08` (island-buffer entry A1: its words `+$A/+$C/+$E` + `[$A4A/$A4E/$A52]`) play a graded explosion. Each checks `$1249` and needs `[$A02]` ≠ 0. The variant is level + 6 − max(0, (d − 5000) >> 13), where d is the Chebyshev distance to `[$A02]`, so it's 3–8.
- Every level is a constant `move.w #n,d0` just before the call. "Every hit" means the hit handler plays it unconditionally.
- No sound path in the ported routines raises `NotImplementedError` / `NotPorted`.

**Events** (Python tuples, TS arrays; the same in both):
- `("sound", id)`: an effect call (`$20B90`), emitted only where the original makes it, so after its call site's `$1249` test (`UnitCore._sfx`). Ids 7 and 8 are silent table entries, but the calls happen.
- `("explosion", level, a)` for `$20C30` with A1 = `a` (the object: its longs `+$A/+$E/+$12` give the position), and `("explosion", level, x, y, z)` for `$20C08` (the island entry's words plus `[$A4A/$A4E/$A52]`, computed at the call). Every call is emitted; the routine's own gates (`$1249`, `[$A02]` ≠ 0) and the distance grade are the player's. Quirk: `hit_enemy_fighter` calls it with the A1 that the debris routine `$1CA8E` leaves, the debris burst it used (or the fighter if none), so that address is the event's (`debris_a1`); the sound plays at that record's `+$A/+$E/+$12`, not at the fighter.
- `("message", text, ping)`: ping is 1 when the message routine calls `$20B90` itself (id 0 from `$A0DE`, 1 from `$A128`): `[$1247]` = 0 and the header byte's bit 7 clear; a bit-7 message through `$A0DE` still pings while sound effects are off (`[$1249]` ≠ 0). `WorldState._ping(bit7, first)`. The bit-7 messages: "Decoy flare launched", "Recon drone launched", "Surface to surface missile launched", "Missile heading for Carrier!", "motion blocked by object" (all `$A0DE`, header `$99`/`$8F`) and "Carrier has grounded" (`$A128`, `$99`): the callers play their own effect after them. All other 154 message calls have a bit-7-clear header (`$19`, `$1E`, `$32`, `$50`).
- `("attacked", i)`, `("fallen", i)`, `("repulsed", i)`, `("captured", i, owner)` and `("neutral", i)` are `$A128` messages too (headers `$19`/`$1E`): they ping when `[$1247]` = 0. Their format is unchanged.
- `("sound", "game over")`: the tune of `leave_game_over` (`$2019A` with `$4B72E`, `[$466]` = 1; ungated).

**Check** (`tools/stunits_render.py`, run by `check_units.py`): the original runs under Unicorn with hooks on `$20B90` (id from the inline word; calls from `$A11E`/`$A168` are the message pings), `$20C30`/`$20C08` (level d0, A1, the computed position), `$A0DE`/`$A128` and `$14446`; its log must equal the model's events pass by pass (or call by call):
- `check_sound_events`: a 3000-pass run from the first action capture, 60 passes from 25 play captures, 120 perturbed scenario runs of every module (with `$1247`/`$1249` switched at random) and 60 Manta/Walrus weapon runs with sound effects on;
- `check_virus_capture`: 300 direct calls of `$1C512` (capture sound 11, both explosions);
- `check_sound_commands`: the carrier, hangar, controls, vehicle and war command checks, screen switching, the vehicle and war runs, the lock-on and the ACCB capture, re-run with every state comparison also comparing the sound log (about 57000 comparisons, 7300 calls).

Every effect id 0–12, "game over", every explosion level of both forms and both ping values occur and match.

**Effect calls** (`$20B90`). "sfx" means skipped when `$1249` ≠ 0.

| Site | id | Routine | Model method (Python; TS same name) | Condition |
|---|---|---|---|---|
| `$A11E` | 0 | `$A0DE` message | `_unit_message` (stunits_controls / units_controls) and every `("message", …)` from `$A0DE` | `$1247` = 0 (ping on) and (`$1249` ≠ 0 or header byte bit 7 clear). Bit 7 set means the caller plays its own sound. |
| `$A168` | 1 | `$A128` message (second routine) | as above | `$1247` = 0 and header bit 7 clear. `$1249` isn't tested. |
| `$13424` | 10 | `$1338C` | `cmd_launch_flare` (stunits_carrier) | sfx; flare launched |
| `$13546` | 10 | `$134C2` | `cmd_launch_recon_drone` (stunits_carrier) | sfx; drone launched |
| `$136D0` | 5 | `$13598` | `cmd_fire_ssm` (stunits_carrier) | sfx; cruise missile (type 19) created |
| `$1AB32` | 12 | `$1AABA` anti-ship missile (type 34) | `antiship_missile` (stunits) | sfx; first leaves its island box (`+$1A` 0 → 1) with `+$1C` = 0 (aimed at the player) |
| `$1BED4` | 0 | `$1BE5A` | `hit_walrus` (stunits) | sfx; Walrus rammed (class 11), "motion blocked" |
| `$1C59E` | 11 | `$1C512` | `hit_virus_bomb` (stunits) | sfx; capture: hitter type `$29`, angle test passes, island not already the player's |
| `$1E8F2` | 1 | `$1E8CE` | `carrier_ground` (stunits) | sfx; carrier runs aground |
| `$1EF56` | 4 | `$1EDFC` | `carrier_laser_turret_step` (stunits_carrier) | sfx; a bolt is fired (screen 8, cooldown 0, fire, turret health ≠ 0, a free object) |
| `$1FA82` | 7 | `$1F9E8` | `manta_fire` (stunits_vehicles) | sfx; bomb dropped. The entry is 0, so this is silent. |
| `$1FB44` | 2 | `$1FA9C` | `_manta_laser` | sfx; bolt fired (`$940` = 0, a free object) |
| `$1FC04` | 8 | `$1FBA4` | `_walrus_drop` | sfx; virus bomb dropped. The entry is 0, so this is silent. |
| `$1FED6` | 3 | `$1FE4A` | `_walrus_laser` | sfx; ammo > 0, `$942` = 0, a free object |
| `$1FF88` | 6 | `$1FEEA` | `_walrus_missile` | sfx; guided missile (type 18) fired |
| `$2002A` | 9 | `$1FFEA` | `manta_lock_scan` | sfx; lock-on (`$116C` = 25) |
| `$200CA` | 5 | `$1FFEA` | `_manta_missile` | sfx; homing missile (type 17) fired at `$1168` (second press) |
| `$14446` | (record `$4B72E`) | `$14438` | `leave_game_over` (stunits_controls) | `[$466]` = 1 (carrier destroyed). Ungated. Use `gameOver()`. |

**Graded explosions.** All of these methods are in `tools/stunits.py` / `units_core.ts`, except `supply_drone_destroyed` (also in stunits_enemy).

| Site | Level | Form | Routine → model method | Condition |
|---|---|---|---|---|
| `$1A8EE` | 1 | object | `$1A886` `guided_missile` | Hits the ground or an island: y < 150 and (y ≤ 30 or inside the island box). Expiry is silent. |
| `$1AA08` | 1 | object | `$1A9B0` `bomb` | Bounce: over its island box at y < 100 with rebound ≥ 30 |
| `$1BF52` | 1 | object | `$1BF42` `_vehicle_damage` (from `hit_walrus`) | Walrus health ≤ 0 |
| `$1BFB0` | 1 | object | `$1BF84` `hit_manta` | class ≠ 0, `$118A` = 0, Manta health ≤ 0 |
| `$1C008` | 0 | object | `$1BFFA` `hit_laser` | Bolt hit by anything but a bolt |
| `$1C060` | 0 | object | `$1C010` `hit_deck_drone` | Hit by anything but a drone of types 10–13 |
| `$1C072` | 0 | object | `$1C06E` `hit_world_simple` (type 17 homing missile) | Every hit |
| `$1C084` | **1** | object | `$1C080` `hit_world_simple` (type 18 guided missile) | Every hit. The model method serves three handlers with different levels, so it needs the handler address. |
| `$1C318` | 0 | object | `$1C312` `hit_world_simple` (type 32 recon drone) | Every hit |
| `$1C0B8` | 1 | island | `$1C092` `hit_island_drone` | class ≠ 0 and health ≤ 0 |
| `$1C0E2` | 1 | object | `$1C0CA` `hit_supply_drone` → `supply_drone_destroyed` | class ≠ 0 and health ≤ 0 |
| `$1C118` | 2 | object | `$1C0F4` `hit_enemy_carrier` | class ≠ 0, hitter type ≠ `$21`, strength `[$11C6]` ≤ 0 (destroyed) |
| `$1C278` | 1 | island | `$1C272` `hit_tree` | Every hit |
| `$1C298` | 0 | island | `$1C292` `hit_sam` | Every hit |
| `$1C2AA` | 0 | object | `$1C2A4` `hit_flare` | Every hit |
| `$1C2C4` | 1 | object (itself) | `$1C2BA` `explode_world` | Anti-ship missile's life runs out (`$1AAC4`) |
| `$1C2D8` | 1 | object | `$1C2D2` `hit_antiship` | Every hit |
| `$1C2FC` | 0 | island | `$1C2EE` `hit_rock` | Hitter isn't the volcano (type 7) |
| `$1C39E` | 2 | island | `$1C324` `hit_command_centre` | Not a virus bomb; health ≤ 0 (island freed) |
| `$1C474` / `$1C480` | 2 / 1 | island | `$1C45E` `hit_building` | Health ≤ 0. Level 2 if `[$AA8]` ≠ 0 (types 37, 40, 42–46), else level 1 (types 38, 39). |
| `$1C4BA` | 0 | island | `$1C4B6` `hit_construction` | Every hit, before the damage |
| `$1C500` | 2 | object | `$1C4E0` `hit_enemy_fighter` | Hitter type ≠ 4 and health ≤ 0 |
| `$1C5B6` | 0 | object | `$1C512` `hit_virus_bomb` | Any hit other than the capture, including "Friendly control centre bombed!" |
| `$1C5F4` | 2 | object | `$1C5EC`/`$1C5EE` `explode_blast` / `hit_blast` | Every cruise-missile or bomb blast: life ends or within 200 of the target (`$1A8FA`/`$1A944`); bomb rebound < 30 or y < 20 over the sea (`$1A9F0`/`$1AA4E`) |

**Other notes**
- `jsr $20CD8` (a bare `rts`) at `$1A9D2`, `$1C5E6` and `$1C60A` are removed sound calls.
- The vehicle sounds aren't events. Call `sound.pass()` after each simulation pass, as `game_main` calls `$20730`. Call `sound.reset()` where the original calls `$20716` (new game `$4212`/`$4280`, `$13AFA`, `$14266`). The pause button calls `$20434`/`$2045E`: `sound.setEnabled`.
- With sound effects on, `$20B90` also writes the caller's A0/D0 to `$A80`/`$A84`. The model ignores this scratch.
