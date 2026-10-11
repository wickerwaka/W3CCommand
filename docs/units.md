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

**Island buffer header:** `+0` in use, `+1` island, `+2/+6/+$A.l` origin x, y, z, `+$E.w` entry count (`$40`), `+$10.w` drones out.

`load_island_buffer` (`$17FB4`) builds one:
- **Entry 0** is the island itself (type 8, local y `$28`, `+$1E/+$20` = half-extents).
- **Each slot** goes through `slot_entry_table` `$18066` (8 bytes per slot type: `handler.l, param.l`):
  - a negative handler creates the entry type −handler at the slot's x/z and heading, with **local y = param + `$50`**, and health `+$38` = slot value;
  - the y params are: volcano 600; types `$0E`/`$0F` 60/45; trees 200/250/100/175; `$25` 300, `$26` 30, `$27` 40, `$28`/`$29` 100, `$2A` 500, `$2B`/`$2C` 200, `$2D`/`$2E` 100 (construction types are the same);
  - this **answers the web viewer's open question on vertical placement**.
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
| `+$12` | weight *(guess)* |
| `+$14` | **health** (max 4000) |
| `+$16` | prep timer |
| `+$1A` | selected weapon: 0 laser, 1 missile, 2 payload |
| `+$1C` | flight mode |
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
| `+$10` | ACCB code *(guess)* |
| `+$12` | weight *(guess)* |
| `+$14` | **health** |
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
   - ACCB (`$1FC10`): on a neutral island under the Walrus, place a `$34` builder (code = Walrus `+$10` + 1), `difficulty_add($400)`, 2500 points;
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

## 10. Screens and controls

**Screens:** `[$718]` = the current screen (0–35), with 8 tabs (`$714`, last screen per tab in `$720`). Per-screen tables:
- render `$144A0`;
- input `$170D4`;
- panels `$43856`;
- enter `$139D4`;
- leave `$14318`.

Screen changes go through `ui_frame` (`$F636`): the `[$1227]`/`[$1228]` request, the left icon column → tab, the right column → screen (element type = screen id).

| Tab | Screens |
|---|---|
| Carrier | 0 bridge 3D, 1 damage status / set priorities, 2 map (waypoint), 3 resources (stores, priorities, transfers), 4 messages |
| Weapons | 8 laser turret, 9 decoy flares, 10 viewing drone, 11 defence drones radar, 12 weapons status |
| Walrus | 16 arming, 17 map, 18 control, 19 dock 3D, 20 status |
| Manta | 24 arming, 25 map, 26 deck 3D, 27 control, 28 status |
| — | 32 game status (save, format, quit; pauses), 33 title, 34/35 game over |

**Mouse:**
- **Pointer mode** (`$457` set): the deltas `$1268/$126A` move the pointer `$474/$476`.
- **Mouse-steer** (`$457` clear, toggled with the right button on screens 0, 8, 18 and 27): the raw deltas go to the viewed vehicle and are cleared after use.

**Clicks:**
- Buttons: `$880` left, `$882` right (held / new).
- `$45A` = 2 for a new press, 1 for auto-repeat after 6 passes.
- Panel element `+$12` is the click handler; the element type indexes the flag table `$40EB2` (bit 15 hidden, 14 disabled, 13 highlight, 5 selected).

**Keyboard:** `$798 + 2·scancode` (held, new). The last character goes to `$1270`.

**What the simulation reads:**

| Unit | Variables |
|---|---|
| Carrier | throttle `$646` (slider (187 − y)·4); chevrons `$88A/$88B`; autopilot `$640` bit 0; rudder from mouse-steer |
| Manta (selected `$48C`, record `$48E`) | throttle `+8` (slider (195 − y)·7); weapon `+$1A`; flags `+0`; mode `+$1C` |
| Walrus (selected `$566`, record `$568`) | throttle `+6`; weapon `+$18`; flags `+0`; mode `+$1A` |
| Laser turret | cursor keys or icons; fire `$88C` or a click; zoom Insert / Clr-Home |

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

`tools/stunits.py` (`UnitState`, extending the strategic `WorldState`) has these, verified by `tools/check_units.py`:
- `carrier_step` with the speed governor, autopilot, grounding and recoil;
- `walrus_dock_door_step`;
- the idle Manta lift;
- the shared maths: yaw/pitch rotation, atan2, `turn_towards`, `self_centre`.

**Test data** is an **action game**, which needs no input. The carrier sails a few thousand units on autopilot to a waypoint off enemy island THERMOPYLAE (27) and stops: `capture_sim.py --action --steps 20 300 1500 --ticks 0 --passes 40 --out emu/out/action`.
- It captures RAM around 120 whole simulation passes (`$40B0–$413E`).
- Under Unicorn, a whole pass reproduces the Hatari capture exactly, except for interrupt-driven counters and the stack. So the oracle is valid for the unit layer too.

**Island 27 never engages** in this game. The carrier stops about 35,300 units from the island's centre, outside the 16-bit island-local range. Its "in range" flag (bit 9) is never set, so the drone pad and AA turrets see no hostile, and the carrier is beyond the anti-ship range `[$454]`.

**Checks:**
- the maths against the original on 3000 random inputs each;
- the routines on all 120 captured states plus 2000 fuzzed ones (steering, grounding, recoil, manual keys and mouse, no fuel, damaged engines, open sea);
- 3000 consecutive `carrier_step` calls match the original through arrival and stop.

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
