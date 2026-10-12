# Carrier Command in the browser

A port of Carrier Command (Atari ST, 1988). The game rules are the original's: the simulation in
`src/sim` is a line-by-line port of the original code, checked against it. The screens are a
new HTML/canvas UI. Every button calls the same command the original's panel handler runs.

## Setup

You need your own copy of the game (`roms/CARRIER.TOS`), Hatari (`brew install hatari`), uv
and Node 24.

```
uv sync
uv run python tools/split_tos.py
cd web
npm install
npm run data     # boots the game in Hatari once, saves the title screen to public/data/ (git-ignored)
npm run dev      # then open the printed URL
```

`npm run build` writes a static site to `dist/`, which includes your `public/data/`. It's for
personal use only; don't publish it.

The public site is built by `.github/workflows/pages.yml` on every push to `main` and deployed
to GitHub Pages (enable Pages with source "GitHub Actions" in the repository settings). It's
built from a clean checkout, so it has no game data, and the workflow fails if any turns up in
`dist/`. Without the title image there's no new game: players load their own state exported
from Hatari (Esc → load).

## Playing

Esc opens the menu: new strategy or action game, four save slots, the autosave (every 30 s),
loading a state exported from Hatari (a 512 KB RAM image or a `.sav` memory snapshot), export,
the free camera and sound.

The tabs follow the original's four screen groups: **Carrier**, **Weapons**, **Walrus**,
**Manta**.

| Where | Keys and mouse |
|---|---|
| Anywhere | Esc menu · P or Space pause · B bridge · M map · C free camera (drag to look, WASD and Q/E or R/F to fly, wheel for speed) |
| Bridge | W/S or ↑/↓ speed (hold) · A/D or ←/→ rudder (hold) · X stop · E centre the rudder · Q autopilot |
| Carrier map | click sets the course (then press Autopilot or Q) · right-click (or long-press) a friendly island to make it the stockpile · drag and wheel or pinch to pan and zoom |
| Manta control | W/S throttle · A/D turn · ↑/↓ climb and dive · F or Enter fire (hold) · Q autopilot · E centre |
| Walrus control | W/S throttle · A/D turn · F or Enter fire · Q autopilot · E centre · ↑/↓/←/→ steer a Harbinger in flight |
| Laser turret | arrows aim · F fire (hold) · zoom buttons |
| Viewing drone | launch the drone, Arm SSM, then click the target in the picture |
| Touch / mouse | on the bridge, Manta and Walrus control and turret screens, drag on the view to steer or aim (the drag is a joystick); the panels have hold buttons for throttle and fire |

### Taking your first island (strategy game)

1. The carrier starts just south of VULCAN, facing it. Hold S to back off, then press X.
2. On the **Map**, click near a neutral (grey) island, then press **Q**.
   - The autopilot steers in a straight line and runs aground on any island in the way. Each
     grounding costs superstructure. Set the course in legs.
3. When the message line says the carrier has reached its waypoint, press X to stop.
4. **Walrus → Arming**: put the ACCB (automatic command centre builder) in the payload slot,
   and Harbingers or the Avatar laser in the weapon slot.
5. **Walrus → Dock**: Refuel, then Launch. Wait until it's clear of the dock.
6. **Walrus → Map**: click the middle of the island. Then on **Control**, press Q to drive
   there.
7. Once it's well inland, take over (Q), select **Payload** and fire. The ACCB drops.
8. A builder grows on the island and the island becomes yours. Destroy the enemy carrier or
   hold all 64 islands to win.

`npm run playthrough` plays exactly this with the model's own commands, and checks every step.

### Continue a browser game in Hatari

Export the state from the menu. This saves a 512 KB RAM image, `carrier_<clock>.bin`. Then run
this from the repository root:

```
uv run python tools/hatari_import.py play ~/Downloads/carrier_<clock>.bin
```

Hatari boots the original and starts a game in fast-forward. At the top of its main loop it
loads your image, then carries on at normal speed with sound. The original continues the game
exactly where the browser left it. `tools/hatari_import.py verify` checks this against the
model, pass by pass.

## How this port differs from the original

- **Screens:** the screens are new. They show what the original shows (`src/app/readout_screens.ts`,
  checked against the original's drawing), but they aren't the ST's panels.
- **Map:**
  - The modern map draws all islands at every zoom, plus the network and your units.
  - The enemy carrier is never shown, as in the original. The menu has a debug option to show it.
- **Dithering:** two-colour surfaces use the ST's checker dither at about one ST pixel per cell
  (the menu can switch to blended colours).
- **Crashes:** where the original crashes the ST, the port does something safe and says so in
  the message log (`docs/units.md` §12).

## Development

| Command | Checks |
|---|---|
| `npm run check-sim` | the simulation against vectors recorded from the Python reference (`uv run python tools/export_sim_vectors.py`), which is itself checked against the original (`tools/check_sim.py`, `tools/check_units.py`) |
| `npm run check-models` | the 3D model decoder against the original's vertex code |
| `npm run check-sound` | the sound engine against the original's |
| `npm run soak` | random games for many passes: no exceptions, no unported code |
| `npm run playthrough` | the island-capture walkthrough above |

`src/sim/README.md` has the porting conventions. The repository's `CLAUDE.md` and `docs/`
describe everything else.
