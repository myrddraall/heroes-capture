# heroes-capture

Renders top-down images of Heroes of the Storm battlegrounds by driving the game itself: a
script injected into the map hides the HUD, the fog of war and the units, points the camera
straight down and steps it across the map while screenshots are taken, and the screenshots are
stitched by their known positions. Structures (forts, towers, cores, gates) can be kept or
hidden, so each map can be rendered both ways.
It also extracts the game's interface textures and finds them by description
([Interface textures](#interface-textures)).

Runs on Windows with the game installed. Start Heroes from the Battle.net app first, so it's
logged in; the capture then hands the prepared map to the running game.

## The release

Each release on GitHub has `heroes-capture-win-x64.exe`, the whole tool in one file (Python
included; nothing to install), and its `.sha256`. The newest is always at
`https://github.com/myrddraall/heroes-capture/releases/latest/download/heroes-capture-win-x64.exe`.

```powershell
heroes-capture-win-x64.exe map render "Battlefield of Eternity"
heroes-capture-win-x64.exe map render --category all   # every map the tool can render
heroes-capture-win-x64.exe map list     # the game's maps by category, and which are verified
```

The output folder is one a render can be run again on: a map already rendered there is skipped
(`--force` renders it again), and a map whose render failed or was stopped carries on from where
it got to. A category's run goes on past a map that fails and lists the failures at the end; the
same command again picks them up.

A render's images go to `maps\<map id>\` in the folder it runs from (`maps\battlefield-of-eternity\`;
`-o` / `--output-dir` picks another folder for them). Its working files (the screenshots, the
prepared map, the diagnostic logs) go to `tmp\` and stay there: a render that stops carries on
from them when run again (only one left unfinished, by the same version of the tool; a finished
render is shot afresh). `--clean-up` removes them once the render is done (for a render known to
work), and `heroes-capture clean-up` removes them any time (only what heroes-capture wrote in `tmp\`).

Two logs:

- `logs\heroes-capture.log`: the output as `--log` prints it (plain text, no colours), whichever
  view was on screen; kept, each command's run after a dated `=====` line.
- `tmp\heroes-capture-<date>-<time>.log` (one per run) and `tmp\<id>\log.txt`: the diagnostic
  logs, with every message, the detail ones included, and every line a library printed,
  time-stamped; working files, so removed with the rest. A run removes only its own: a failed
  run's stay until `clean-up`. A capture restarted after a lost match carries on in its run's.

On a terminal the output is a live view (the step, its status, progress bars; warnings print
above it); `--log` gives plain log lines instead, the default in CI or when the output isn't a
terminal; `-v` / `--verbose` adds the detail messages in either.

The rest of this page is for working on the tool itself.

## Setup (once)

- [Python](https://www.python.org) 3.11 or later
- In this folder (the package, `src/heroes_capture`, and its dependencies):

  ```powershell
  pip install -e .
  ```

- The native libraries, into `native/` (not in git): the map archives are read and written with
  [StormLib](https://github.com/ladislav-zezula/StormLib) (v9.40, its release DLL, checked
  against the release's SHA-256), the game's own data (its maps, tilesets and light sets, sky
  models) is read from the install's CASC storage with
  [CascLib](https://github.com/ladislav-zezula/CascLib) (3.0, which publishes no binaries, so it
  is built). `python tools/build_native.py --windows` fetches and builds both (needs git, CMake
  and, off Windows, Zig: `pip install cmake ziglang`); without `--windows`, the Linux libraries
  the tests use. The install is found by itself (its uninstall entry, the Battle.net app's list,
  the usual folders).

- In the game's options: **Display Mode: Windowed (Fullscreen)** (screenshots of exclusive
  fullscreen come out black), graphics on Ultra. Any resolution does: the render plans for the
  monitor the game is on, and the status strip is measured as it comes out on the screen.

## Render a map

`run.cmd` holds the current step (a render or a diagnostic) and its settings, so a personal
`update.cmd` that refreshes the files and calls it needs no arguments. `update.cmd` is not
tracked: it contains the machine's source path.

`heroes-capture map render` runs all three steps against the installed game, at the primary
monitor's resolution (or another monitor's: `--monitor`), into `maps\<map id>\`, with its working
files in `tmp\`:

```powershell
heroes-capture map render "Towers of Doom"                  # structures kept, into maps\towers-of-doom
heroes-capture map render "Cursed Hollow" --structures hide # bare terrain
heroes-capture map render "Dragon Shire" -o D:\renders      # into D:\renders\dragon-shire
heroes-capture map render "Dragon Shire" --clean-up         # tmp\ cleared once done
heroes-capture map render --category arena                  # battleground, arena, brawl, other or all
heroes-capture map render "Dragon Shire" --force            # again, though maps\dragon-shire has it
heroes-capture map calibrate "Dragon Shire"                 # measure the map for its elements render (then review it)
heroes-capture clean-up                                     # removes the working files runs left in tmp\
heroes-capture job run.yml                                  # the runs a job file lists, in order
heroes-capture job run.yml --resume                         # carries on a job that stopped
```

A job file lists runs in YAML: whole renders, renders of only some parts over the map's last run,
and calibrations (`heroes_capture/job.py` describes it):

```yaml
runs:
  - map: Dragon Shire
    render: elements          # keep, hide or elements: the whole render
  - map: Dragon Shire
    render: elements
    only: [rubble]            # skies, standing, camps, rubble, tiles: just these, over the kept run
  - map: Dragon Shire
    calibrate: [standing]     # calibration stages, or all
```

A run of some parts builds on the map's kept run of the same render in `tmp\`: what it reshoots is
removed, the rest kept, and the map is stitched from all of it; the setup those parts need (the map
prepared and launched, the structures brought down) is done whatever is chosen. The job's progress
is kept in `tmp\job.json`: `--resume` skips the runs it finished and carries on the one under way.

A map already rendered in the output folder (its viewer is there) is skipped. A render left
unfinished carries on from its working files when run again with the same options: the capture
resumes at the first missing screenshot (less the two the saving threads may have left
half-written), or the stitch runs straight away when they're all there. With other options it
starts again. Maps not yet verified render too; their line says they may not come out right.
Options that aren't `map render`'s own go to the preparing step: `heroes-capture prepare --help`
lists them.

An elements render (`--structures elements`) needs the map's reviewed calibration: a short match
(`map calibrate`) that measures in the game what the render would otherwise assume from
Battlefield of Eternity (where each structure's copy can stand and still look like it, which
structures change with the side, what keeps moving while paused) and films each kind's fall,
followed by a review of what the numbers can't settle. The render stops when the map has none from
the current tool. `VERIFICATION.md` has the steps.

(`py -m heroes_capture map render ...` is the same.) `heroes-capture map list` lists the game's
maps by category and which are verified: their render passed the checklist in `VERIFICATION.md`,
with Battlefield of Eternity as the standard, at the checklist's current version. The record is
`maps/validated-maps.json`; a change to the render bumps the checklist's version there, and every
map is unverified until it passes again. The
categories come from the mods each map builds on: Battleground (the 5v5 maps of the Versus AI /
Quick Match / Storm League pool, with the custom-game-only ones: the game's data doesn't tell them
apart), Arena and Brawl (each map needs handling of its own, as Punisher Arena's three arenas and
rounds did, so only the known ones are supported, those in `validated-maps.json` at any checklist
version; naming another renders it with a warning),
and Other (the sandboxes, and Try Me Mode and the tutorials, listed as unsupported: the game keeps
them as folders rather than map archives, and the capture builds on an archive). A category's run
leaves the unsupported maps out. The steps on their own, which leave their files in place:

```powershell
# 1. Prepare: reads the map from the game, injects the capture script, plans the grid (into tmp\).
heroes-capture prepare "Towers of Doom" --screen 3440x1440 --distance 214 --keep 0.4

# 2. Capture: launches the map, waits for it to load and for the intro to finish; leave the
#    mouse and keyboard alone.
heroes-capture capture tmp/towers-of-doom-structures.json

# 3. Stitch: writes maps/towers-of-doom/: the pack (pack/) and the full-size layers (raw/).
#    --composites also writes the map over its sky layers as three full-size PNGs in raw/.
heroes-capture stitch tmp/towers-of-doom-structures.json
```

The screenshots are kept as raw `.npy` arrays (fast for the stitch to read).

Use `--structures hide` for bare terrain; it goes to the map folder's `terrain\`.

Map names are as the game shows them (case and punctuation don't matter), or pass a path to any
`.stormmap`. The battleground maps are `.s2ma` archives under content-hash names in the game's
storage; the first run after a game update opens each to index them by name (about 20 s).

### Monitors

`--monitor <which>` puts the game's window on that monitor before launching the map, and plans
the render for that monitor's resolution: `--monitor 2`, by the number Windows lists the monitors
in (the run's log starts with the list: `monitors: 1 \\.\DISPLAY1 3440x1440 at (0, 0) primary;
2 ...`), or its device name, or `primary`. Without it the game stays where it is and the primary
monitor's resolution is planned for. The capture follows the game's window either way; a window
that didn't take the monitor's size is warned about. The game puts its window back on its own
monitor when it redraws at a new size, so the window is watched for a few seconds and placed again
when that happens (up to three times). The narrowest picture the game draws is 5:4: on a narrower
monitor (1680x1440, say) it draws a 5:4 picture the monitor's width wide (1680x1344), centred, with
black above and below, and the render is planned for that picture.

To check a monitor before rendering on it, `map render <map> --probe-monitor --monitor <which>`
runs the start-up there and then reports, instead of rendering: whether the window took the
monitor, the status strip's cells against what the screen's height predicts, the width blanked for
the strip, and where the camera lands when sent to a tile. The report (`report.json`, the strip's
column and the screen at half size) goes to `tmp\probe-monitor-<WxH>-<time>\`; the log line
`monitor probe at WxH: n of 6 checks ok` sums it up.

### Options (prepare; `map render` passes them on)

| Option                    | Default     | Effect                                                                                                         |
| ------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------- |
| `--structures keep\|hide` | `keep`      | keep or hide forts, towers, cores and gates                                                                    |
| `--px-per-cell <n>`       | `48`        | output resolution; a map is ~220 cells wide, so 48 gives ~10,600 px                                            |
| `--screen <w>x<h>`        | `3840x2160` | the game's resolution while capturing; match your monitor                                                      |
| `--fov <deg>`             | `20`        | field of view; narrower is flatter (less lean on tall objects) but puts the camera further away                |
| `--distance <units>`      |             | camera distance instead of `--fov` (the field of view is then chosen to keep the scale); `map render` uses 214 |
| `--keep <0..1>`           | `0.6`       | share of each screenshot used, centred; the rest is overlap, used to measure the scale                         |
| `--refit-yaw <deg>`       | map's light | yaw of the lighting-refit look before each tile (by default it faces the map's main light)                     |
| `--no-lens`               |             | don't set the field of view or clip planes (see troubleshooting)                                               |
| `--margin <cells>`        | `0`         | also capture beyond the map's camera bounds (lifts them)                                                       |
| `--crop-margin <cells>`   | `12`        | the stitched image reaches this far past the camera bounds (or past each arena's area)                         |
| `--show-ui`               |             | diagnostic: leave the HUD up; `map render` then launches the map and stops                                    |
| `--keep-intro`            |             | diagnostic: let the intro cutscene play out instead of skipping it                                             |
| `--paint-texture <t> <c>` |             | diagnostic: paint one of the map's own sky textures a solid colour, or `clear` (sky probes; repeatable)       |

Diagnostics `map render` runs instead of rendering:

| Switch          | Effect                                                                                                                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--probe-sky`   | one edge tile over each solid-colour skybox; the sky part of each shot is measured (mean colour, spread), to check the colour shows and is flat; `HRS_SKY_SEQUENCE` scripts the swaps |
| `--probe-sky-reach` | the camera as far past the camera bounds as it goes, the sky shot there with the render's lens and wider ones: how much parallax sky a map has |

Higher `--px-per-cell` means more screenshots and a closer camera. Past about 64–128 px per
cell there is no more detail: that's the game's own texture resolution.

### Outputs

Each render writes its map's folder, `maps\<map id>\` (with `--structures hide`, its
`terrain\` subfolder the same way), in two parts. [PACK.md](../../PACK.md) at the repository
root is the format, written as the contract with the map viewer that reads it.

- `pack\`: what a viewer loads, made to be hosted as it is:
  - `pack.json`: what's in the pack and how to draw each layer: the map, its arenas, the
    layers' scales, positions and parallax rates in map cells, the pictures, and every file
    with its SHA-256.
  - Per layer (`map`, `background`, `haze`; one map layer per arena on a map of several) an
    overview (`<layer>-overview.webp`: the level the opening view needs, whole) and a tile
    pyramid (`<layer>.pmtiles`): a PMTiles archive of 512 px WebP tiles of the finer levels,
    read by range requests.
  - `fixed.webp` (the fixed skybox, a screen backdrop), `thumbnail.webp`, `images\custom-minimap.svg`
    (the custom minimap redrawn as an SVG that lies exactly on the map) and, for the elements
    render, `elements\` (the cut-outs' atlases).
- `raw\`: the layers at full resolution as PNG (`map.png`, `background.png`, `haze.png`,
  `fixed.png`), `layers.json` placing them as `pack.json` does, the elements' cut-outs
  (`elements\`), the map's own pictures from the game (`images\`: minimap, custom minimap,
  replay preview, map-select picture, loading screen and its icons, where the map has them),
  with `stitch --composites` the composites (`composite.png`, background, haze and map together;
  `composite-on-black.png`; `composite-with-fixed.png`, over the fixed skybox), and
  `index.html`, the reference viewer. `heroes-capture map view "<map>"` serves the map's folder
  on this computer and opens it: drag to pan, mouse wheel to zoom; each layer's overview shows
  first and tiles sharpen it as you zoom; the sky layers move at their rates behind the map. The
  custom minimap shows on the map while the first view loads, then fades away; the buttons show
  or hide each layer (the background, sky, haze, terrain and minimap; over the terrain the
  minimap is see-through), and "Swap colours" shows the teams in each other's colours.
- Team colour: everything the game draws in a team's colour (the structures, and doodads such as
  Battlefield of Eternity's Heaven lamps) is shot from both sides (our player switched to Chaos
  and back: `side`), and the pack carries a mask of each team's colour (PACK.md, Team colour), so
  a viewer can show the teams in any colours.

The map image is cropped to the camera bounds plus `--crop-margin` cells, widened to take in all
of the map the screenshots show, with the void transparent. Map cells convert to its pixels by
`px = (x - originCell.x) * pxPerCell`, `py = (originCell.y - y) * pxPerCell`. The sky layers
(on maps with keyed copies of their own sky) are the sky seen from the middle camera position:
the shells are a tilted plane (fitted from the sky shots' overlaps), so they come out as
trapezoids, transparent in the corners no shot reaches. Every render of a map with its own
parallax sky also keeps `<id>\sky-layers.json` in its working files, the measured speeds.

## Interface textures

```powershell
heroes-capture ui extract                                  # the interface's textures into assets\ui\
heroes-capture ui search "gold hexagonal portrait frame"   # the 40 that fit a description best
heroes-capture ui search "talent panel background" -n 80
```

`ui extract` reads the game's data (no need for the game to be running) and writes each of the
interface's textures as a PNG, at full size and as a thumbnail, into `assets\ui\` (Blizzard's art:
not in git). The interface's textures are the ones its layouts draw, its asset table names
(`UI/<name>`), its catalog entries show as pictures (button icons, portraits, loading screens)
and the ones named with its own prefixes (`storm_ui_`, `ui_`, `hud_`, ...): about 12,400. Sprays
and banners are left out: they're collectibles drawn in the world.

`assets\ui\catalogue.json` says, for each texture: its size and format, whether it's transparent,
its asset names, the layout frames that draw it (with the frame's texture type: `Border` and
`NineSlice` pieces are stretched by their edges) and the catalog entries that show it, with
their English names. A texture unchanged since the last run isn't decoded again.

`ui search` ranks the textures by how near each picture is to the description, by an image-text
model ([SigLIP 2](https://arxiv.org/abs/2502.14786) through
[OpenCLIP](https://github.com/mlfoundations/open_clip), on the CPU), with a little added for the
description's words in what the catalogue calls the texture. It lists the best and draws them
on a contact sheet, `assets\ui\searches\<description>.png`. It needs the search extra, which
the executable leaves out (PyTorch alone is hundreds of megabytes):

```powershell
uv sync --extra search   # or: pip install -e ".[search]"
```

The model's weights download once (Hugging Face's cache). The first search looks at every
picture (`assets\ui\index-<model>.npz`), which takes minutes; after that a search takes seconds,
and after a game update only the textures it changed are looked at again.

## How it works

- **Preparing (`capture/prepare.py`, with `map/prepare.py`)** copies the `.stormmap` (an MPQ
  archive, written through StormLib), reads the map size and camera bounds from its `MapInfo`,
  plans a grid of camera positions, and puts heroes-capture's script into `MapScript.galaxy`,
  called at the end of `InitMap`. The script is assembled from parts, one per layer that takes
  part (`map/script.py`): the map's own (`map/script.galaxy`: the status strip, the command box,
  the note, leaving the match), the capture's (`capture/script.galaxy`: tiles, scene, camera,
  skies), the map's category's (`categories/battleground/script.galaxy`: structures and camps one
  at a time; `categories/arena/script.galaxy`: rounds) and, when one needs it, a single map's. A
  part declares its commands and its start-up function in header lines, and a function a later
  part defines again replaces the earlier one. It also adds the status strip's texture and the
  solid-colour skyboxes (see below). A map that is several arenas in one (Punisher Arena: one arena per round,
  stacked on the map, the camera bounds moved to the round's arena at run time) marks each
  with a region named `..._MapBounds` in its `Regions` file; with two or more, each gets its
  own grid, the script lifts the camera bounds so the camera can reach them all, and the
  stitch writes one image per arena (`<id>-m1.png`, ...), without the edges of the arenas next
  to it that its screenshots see across the void (each separate piece goes to the arena it is
  nearest). Past an arena's edges that face no other arena the capture goes on where the map
  does, as on a map of one. Such a map plays rounds (its script includes `LibAREN`): killing a core ends only
  the round, so `quit` first leaves the other team one round win short of the match.
- **The capture script** reveals the whole map, removes every unit except structures (and
  keeps removing them as they spawn), hides health bars, keeps or hides structures, hides the
  HUD, and sets a straight-down camera. Commands go into an edit box of the map's own in the
  strip's column (blanked from every shot), typed as Unicode text ending in `;`: `tile <n>;`
  moves the camera to tile n. Text input never acts as a hotkey wherever it lands, and unlike the
  chat box (which opens and closes on Enter, and lost its state when the game lost focus) the box
  has no state to lose; a command takes about 90 ms against about 230 ms through chat (measured
  with a probe). If a command goes unanswered, the chat command `focus` gives the box the
  keyboard back.
  Structures are hidden rather than removed, since removing a core could end the game. Camera
  positions stay inside the map's camera bounds, where the game would otherwise clamp them.
  The map's intro cutscene is skipped the way the game's own skip works: the script stops the
  cutscene, and the game's intro code then restores the camera, interface, sound and vision
  itself; the script ignores `tile` until that is done. Camp names and respawn timers (an
  interface panel pinned over each camp) are hidden along with the other labels. Each `tile`
  first shows a camera like real play at the tile for a few frames, shallow and facing the
  map's main light: the game fits its lighting to the last such camera and never to the
  capture camera, so without this the rest of the map was drawn without its main light (a
  dark, straight-edged area, different each match) and dark boxes stayed around holes; only
  the latest look counts, and only a shallow look towards the light cleared every box. The
  light's direction comes from the map's tileset (`t3Terrain.xml`), the tileset's light set
  and that light set's "Key" light, with the map's own `TerrainData.xml` / `LightData.xml`
  overriding; the tilesets and light sets are read from every mod's `GameData/TerrainData.xml`
  and `LightData.xml` in the game's storage at each run (`game_data.py`, `lighting.py`;
  `--refit-yaw` overrides), so a game update that adds tilesets needs nothing done.
- **Opening events first.** The gates open 3 s in (GameLib's `libCore_gv_bALOpenTheGatesDelay`
  and its countdown timer); then, for 8 real seconds, the map's own timers between the gates and
  its first objective are cut short as each starts (the map's own module in `maps/`: Hanamura
  Temple's payload otherwise comes 3 minutes in), so the objective and
  what it brings are in place before any tile is shot. Everything is paused 18 real seconds
  after the gates open (the fast-forward included), once entrance animations (the cores') have
  finished. The status strip's "ready" bit tells the
  capture when to start, and carries the game clock. (Speeding game time up with
  `GameSetGlobalTimeScale` moved no timers.)
- **Stillness.** Every animation on the map is paused when the scene is set (the whole-map
  `AnimSetPausedAll` actor message Blizzard's maps use at game over): glows, swaying trees,
  flags, fires and smoke hold still, so the two shots of a tile differ only by the sky. Water
  is the terrain's own layer, not an actor, and its waves are time-driven in the shader:
  neither its data (`WaterData.xml`) nor runtime catalog edits still them, so water keeps
  moving (the matte keeps such pixels opaque, the routed seams absorb it at tile joins).
- **Near clip.** The capture camera sets its near clip plane to 5 (the game's default is tiny).
  Depth precision goes with the far/near ratio; at the capture distance, flat decals lying on
  surfaces (road trim, lava cracks, low decorations) were z-fighting the ground and going
  missing in patches. Found with lighting probes that varied the clip planes.
- **Transparent void (`sky.py`).** The void around and below a map is the skybox. The
  map gets four solid-colour skyboxes (white, black, magenta, lime): a skybox is a model on a stock mesh whose textures are
  referenced by path, and a file in the map at that path replaces the game's, so each colour is
  a stock mesh (the Braxis bowl and the "parallax" bowls; the big heaven/Luxoria bowl won't swap
  at run time) plus solid-colour DDS textures, with the mesh's cloud layers made transparent.
  The script sets the camera-fixed skybox (`GameSetBackground` layer 0) to a light grey at every
  tile and black on `black`; the tileset's parallax layer and fog are turned off; cloud layers
  placed in the map as doodads (Battlefield of Eternity, Punisher Arena) are hidden by type. Each
  tile is shot over the light grey and over black, and the stitch turns the pair into colour and
  transparency (difference matting: the difference between the shots is exactly the see-through
  share; the light grey's level is measured from the shots, the game renders it at about 198;
  pixels that changed between the shots other than by the sky, an animated glow, stay opaque).
  Not white: the game draws a white sky at about 230, bright enough to set off its bloom, a soft
  halo round everything in the void that the matte would keep; the light grey doesn't (the matte
  probe). Whether a map's
  void shows the sky is read from its tileset (from the game's data, with the map's own overrides:
  the lowest terrain level undrawn, or a skybox). Without that (Dragon Shire, Towers of Doom,
  Tomb of the Spider Queen) the void is terrain drawn black, which no skybox shows through:
  one shot per tile, over black, and the stitch makes the near-black that is connected to the
  outside transparent. `--probe-sky` shows each colour on one edge tile.
- **The status strip (`status.py`).** How the capture knows a command has been carried out:
  the map script draws a dialog in the top-left corner, two columns of black-or-white cells on
  a black backdrop down the whole left edge, redrawn at the end of every command and every
  sweep. It carries a locator, the sequence number of the last command carried out (the
  capture appends one to each command it sends), the camera's actual target (so clamping at
  the map's edge is known, and the camera bounds are measured by sending the camera to two
  corners), which sky is up, the game clock, the map's identity and phase, and a parity
  bit (`status.py` has the layout). Reading it is a few pixel averages, so the capture polls it
  fifty times a second and shoots the moment the acknowledgement appears, instead of waiting
  fixed times. The echoed camera position (to 1/64 cell) is what places each screenshot; the
  scale (pixels per cell) comes from the image matches between neighbours. The strip's 64 px
  column is blanked in the kept shots and left out of the stitch (`status.pageLeft`).
- **The game's state, and recovery.** The capture (`capture/tiles.py`) tells the game's states apart from a screen
  grab four times a second, cheapest test first: not running (no game process), in our map
  (its status strip), the main menu (its fixed interface, `menu-reference/`), the "Unable to
  open map" dialog (a black band with the menu's backdrop above and below), and otherwise
  loading. It drives the launch, the wait for the map, and the leaving of the match (each
  change of state is logged with its time). When the match is lost (the game closes, the map
  fails to load, the menu comes back, another map is running: the status strip carries a
  16-bit identity of the prepared map), the run is launched again and resumes from the first
  missing tile, keeping what it already has; three times at most per run. The strip also
  carries the map's phase (before the gates, opening timers being cut short, ready, leaving):
  the start-up logs it as it changes, and when leaving, a quit the strip doesn't confirm
  within 3 s is sent again. And the count of units the clearing removed after the map was
  ready (the log notes a tile where something spawned mid-render), whether the intro cutscene
  is playing, and how many opening timers were cut short.
- **The capture (`capture/tiles.py`)** starts Heroes through the Battle.net app if it isn't running (started
  directly it can't authenticate; `--battlenet` or `HRS_BATTLENET` if the app isn't in the
  usual place; the app can let the first launch command go by, so it is sent again after 45 s,
  then as the `battlenet://Hero` link, and after that the run pauses, up to 10 minutes, for Play
  to be pressed on the app's Heroes page), then launches the map through `Support64\HeroesSwitcher_x64.exe` (Heroes must be
  at the main menu: a running match keeps its map), waits for the status strip, on a map with its own sky, measures and shoots the sky layers
  while the map gets ready (or once it is ready, when an in-game hero selection hides the world
  meanwhile: Punisher Arena), measures the camera bounds the game really applies (an arena's are far tighter than its map file says)
  from where the camera stops when sent to two corners, re-plans the grid from them, sends
  each tile with its position (`tile <n> <x> <y>`), records where the camera really went
  (`positions.json`), and for each tile takes the kept image once `tile` has been carried out, then (`black`)
  the same view over the black skybox. It only types while the game is in front, and puts the
  game in front only as the run starts (or as it starts the game): alt-tabbing away pauses the
  run until you click back into the game. Opening the game's own menu (Esc, Options, the Alt+F4
  dialog) pauses it the same way, until the menu is closed. While paused, the live view says why;
  the line goes once the run carries on (the logs keep each pause and how long it lasted). Either
  way the tile in progress is dropped and redone from its start; black frames are retaken; where map content reaches an
  outer tile's outer edge (Battlefield of Eternity's arches run past the camera bounds), it lifts
  the camera bounds and adds tiles beyond, outwards until the edge is clear (three at most); it stops early if the map stops responding, and
  leaves the match at the end (`quit`; the stitch runs while the game leaves, and the next launch
  waits for the menu).
- **The stitch (`capture/stitch.py`)** places each screenshot where the map script reported its camera
  (`positions.json`), and matches every screenshot against its neighbours, like panorama
  software, to measure their real offsets: the scale comes from those matches, and all
  positions are solved together, dropping matches that disagree and skipping black or
  featureless screenshots. Neighbours meet along a seam routed through their overlap where the
  two agree best (it goes around things off the ground plane, which shift between views),
  never far from the halfway line, blended over a few pixels so small lighting changes don't
  show as steps. Then the void is made transparent (see above), the game's black at the map's
  edge is removed (`capture/edge_black.py`: the map's last cell row and column, and anything past
  them, the game draws black with a one-cell fade, whatever the vision, the fog-of-war data or the
  camera bounds; a model reaching into the last cell, like Punisher Arena's north orb, gets that
  black made transparent and the fade before it undone), and the image is cropped to the map.

## Files

The package, `src/heroes_capture/`, is in layers, each a folder; a layer uses only the layers above
it in this table. The map's category and the map itself take part in the stages through one
object, `map/specifics.py`'s `Specifics`, which `cli.py` builds for each map.

| Layer | Folder, module | What it does |
| --- | --- | --- |
| | `cli.py` | the `heroes-capture` command (Typer, Rich): `map render`, `map calibrate`, `map list`, `map view`, `ui extract`, `ui search`, `prepare`, `capture`, `stitch`; picks a map's category and its own module (`specifics_for`) |
| 1 everything | `common/` | the game's install and data (`game_data.py`, `casclib.py`, `stormlib.py`, `native.py`, `lighting.py`, `actors.py`: actor trees and models' moving parts), its process, window and input (`game_window.py`, `game_control.py`, `monitors.py`), screen grabs and comparisons (`screen.py`, `matching.py`), the game's states and menus (`game_state.py`, `game_menus.py`, `menu-reference/`), images, geometry, DDS textures, JSON and frames on disk, the log and the live view (`runlog.py`, `ui.py`, `watch.py`), the worker pool |
| 2 the interface's textures | `ui/` | which textures the interface uses and what the game's data says about each (`catalogue.py`), their pictures (`extract.py`), the search by description (`search.py`) |
| 2 inside any map | `map/` | the script assembler and the map's own part (`script.py`, `script.galaxy`), the status strip (`status.py`), the Galaxy reader (`galaxy.py`), launching and leaving a match and the command box (`match.py`), what every preparation reads of a map (`prepare.py`), `specifics.py` |
| 3 capturing maps | `capture/` | preparing for capture (`prepare.py`), the grid, the capture run (`tiles.py`, `shots.py`), a map's calibration (`calibration.py`), skies (`sky.py`, `sky_layers.py`, `sky_stitch.py`), matting and cut-outs (`matting.py`), the stitch (`stitch.py`, `edge_black.py`), the pack (`pack.py`, `minimap_svg.py`), the reference viewer and `map view` (`viewer.py`, `viewer.html`, `serve.py`), the capture's script part, `diagnostics/` (the `--probe-*` switches and their helpers) |
| 4 categories of maps | `categories/` | `battleground/`: towns, outposts, camps, structure holes, the elements capture, stitch and pack, the calibration's measurements and its review (`calibrate.py`, `calibration_review.py` and its page), its script part; `arena/`: capture areas and rounds |
| 5 single maps | `maps/` | one module per map that needs something of its own (Hanamura Temple's opening timers, Volskaya Foundry's minimap placement), `validated-maps.json` (the maps verified against `VERIFICATION.md`, and the checklist's version), `calibration/` (the maps' reviewed calibrations) |
| | `tools/build_native.py`, `tools/build_exe.py` | fills `native/` with StormLib and CascLib (not in git); builds the one-file `heroes-capture.exe` (PyInstaller) |

## Tests

```sh
python tools/build_native.py   # once: StormLib and CascLib for this machine, into native/
uv run pytest -n auto --dist loadgroup   # or pnpm test from the workspace root
```

- `tests/test_units.py`: the pieces with exact rules (JSON as JavaScript writes it, the script's
  numbers, the grid, lighting, sky textures, the sky measurement's consistency rule).
- `tests/test_simulated.py`: the capture end to end against a simulated game and desktop
  (`tests/sim/fakegame.py`: a virtual clock, the command box and its commands carried out,
  frames with the status strip): renders in both void modes, a resumed run, lost focus, the
  game's menu opened part way, the command box losing the keyboard, a silent strip, a crash, the wrong map, the probes; and the stitch of a simulated
  render.
- `tests/test_game_menus.py`: the game's menus told from the map at screen sizes from 600 to
  2160 rows and 16:9 to 32:9 (`tests/sim/menus.py` draws them).
- `tests/test_ui.py`: the interface's textures on a made-up storage (the catalogue, the pictures,
  the search's ranking) and, marker `game_data`, the game's own interface catalogue.
- `tests/test_game_data.py` (marker `game_data`): the game's own data from Blizzard's CDN (no
  install needed): the maps by name, the tilesets and light sets, the sky models, and three maps
  prepared from it, whose injected script may use only names Blizzard's own Galaxy code has. The
  first run downloads about 3.7 GB into the cache (CascLib keeps whole CDN archives); skip them
  with `-m "not game_data"`.

The workspace's test workflow runs them all on every push, keeping `native/` and the CDN cache
between runs.

## Troubleshooting

- **The game asks you to log in, or can't validate the licence:** start the Battle.net app and
  log in first. If it still asks, load the map through Try Mode instead: copy it over Try
  Mode's map, start Try Mode in the game as usual, then capture with `--no-launch`:

  ```powershell
  mkdir "D:\Games\Heroes of the Storm\maps\heroes\singleplayermaps"
  copy tmp\towers-of-doom-structures.stormmap "D:\Games\Heroes of the Storm\maps\heroes\singleplayermaps\(10)trymemode.stormmap"
  heroes-capture capture tmp\towers-of-doom-structures.json --no-launch
  ```

  Delete that `(10)trymemode.stormmap` afterwards to get normal Try Mode back.

- **Every interface panel visible at once, no status strip:** the injected script failed to
  compile (Galaxy stops the whole map script on one unknown name). The capture gives up after
  3 minutes; `strip-missing.png` shows the screen's left edge.

- **A "script failed to compile" error naming a `c_cameraValue…` constant:** re-run
  `map render` with `--no-lens`. Without a narrow field of view, tall objects lean more at the
  screenshot edges; lower `--keep` (e.g. `0.4`) to use only the centre.

- **Every interface panel showing (Blizzard's debug menu among them), on a red tint:** the map's
  script failed to compile. The run stops at once ("the map's script failed to compile", with a
  shot in `script-broken.png`) rather than relaunching; it's a heroes-capture bug. Leave the match
  by hand (Esc, Quit).
- **HUD pieces still visible:** note which ones. There are more hide calls to try.
- **The camera doesn't move when `tile <n>;` is typed:** the command box needs the keyboard
  (it has it from the match's start; chat `focus 0 ;` gives it back) and the command its `;`.

- **Trees or props missing or low-detail:** the camera is too far away for the game's detail
  distance. Raise `--px-per-cell` or `--fov` (both bring the camera closer).

- **Minions flicker into some screenshots, or some screenshots have blurry textures:** raise
  `--settle` of `heroes-capture capture` (the least time from a camera move to the kept screenshot, 0.1 s by
  default); units are swept four times a second.
