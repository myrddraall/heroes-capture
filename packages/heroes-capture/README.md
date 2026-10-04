# heroes-capture

Renders top-down images of Heroes of the Storm battlegrounds by driving the game itself: a
script injected into the map hides the HUD, the fog of war and the units, points the camera
straight down and steps it across the map while screenshots are taken, and the screenshots are
stitched by their known positions. Structures (forts, towers, cores, gates) can be kept or
hidden, so each map can be rendered both ways.

Runs on Windows with the game installed. Start Heroes from the Battle.net app first, so it's
logged in; the capture then hands the prepared map to the running game.

## The release

Each release on GitHub has `heroes-capture-win-x64.exe`, the whole tool in one file (Python
included; nothing to install), and its `.sha256`. The newest is always at
`https://github.com/myrddraall/heroes-capture/releases/latest/download/heroes-capture-win-x64.exe`.

```powershell
heroes-capture-win-x64.exe map render "Battlefield of Eternity"
heroes-capture-win-x64.exe map render --category all   # every map the tool can render
heroes-capture-win-x64.exe map list     # the game's maps by category, and which have been validated
```

The output folder is one a render can be run again on: a map already rendered there is skipped
(`--force` renders it again), and a map whose render failed or was stopped carries on from where
it got to. A category's run goes on past a map that fails and lists the failures at the end; the
same command again picks them up.

A render's images go to `maps\<map id>\` in the folder it runs from (`maps\battlefield-of-eternity\`;
`-o` / `--output-dir` picks another folder for them). Its working files (the screenshots, the
prepared map, the diagnostic logs) go to `tmp\` and are removed when the render finishes;
`--keep-tmp` leaves them for diagnosis, and a failed render leaves them too. `heroes-capture
clean-up` removes them afterwards (only what heroes-capture wrote in `tmp\`).

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
  fullscreen come out black), your monitor's native resolution, graphics on Ultra.

## Render a map

`run.cmd` holds the current step (a render or a diagnostic) and its settings, so a personal
`update.cmd` that refreshes the files and calls it needs no arguments. `update.cmd` is not
tracked: it contains the machine's source path.

`heroes-capture map render` runs all three steps against the installed game, at the primary
monitor's resolution, into `maps\<map id>\`, with its working files in `tmp\`:

```powershell
heroes-capture map render "Towers of Doom"                  # structures kept, into maps\towers-of-doom
heroes-capture map render "Cursed Hollow" --structures hide # bare terrain
heroes-capture map render "Dragon Shire" -o D:\renders      # into D:\renders\dragon-shire
heroes-capture map render "Dragon Shire" --keep-tmp         # tmp\ left for diagnosis
heroes-capture map render --category arena                  # battleground, arena, brawl, other or all
heroes-capture map render "Dragon Shire" --force            # again, though maps\dragon-shire has it
heroes-capture clean-up                                     # removes what a failed or --keep-tmp render left in tmp\
```

A map already rendered in the output folder (its viewer is there) is skipped. A render left
unfinished carries on from its working files when run again with the same options: the capture
resumes at the first missing screenshot (less the two the saving threads may have left
half-written), or the stitch runs straight away when they're all there. With other options it
starts again. Maps not yet validated render too; their line says they may not come out right.
Options that aren't `map render`'s own go to the preparing step: `heroes-capture prepare --help`
lists them.

(`py -m heroes_capture map render ...` is the same.) `heroes-capture map list` lists the game's
maps by category and which have been validated: their render reviewed and, where needed, tuned
for. They're in `validated-maps.json`; add a map there once its render has been looked over. The
categories come from the mods each map builds on: Battleground (the 5v5 maps of the Versus AI /
Quick Match / Storm League pool, with the custom-game-only ones: the game's data doesn't tell them
apart), Arena and Brawl (each map needs handling of its own, as Punisher Arena's three arenas and
rounds did, so only the validated ones are supported; naming another renders it with a warning),
and Other (the sandboxes, and Try Me Mode and the tutorials, listed as unsupported: the game keeps
them as folders rather than map archives, and the capture builds on an archive). A category's run
leaves the unsupported maps out. The steps on their own, which leave their files in place:

```powershell
# 1. Prepare: reads the map from the game, injects the capture script, plans the grid (into tmp\).
heroes-capture prepare "Towers of Doom" --screen 3440x1440 --distance 214 --keep 0.4

# 2. Capture: launches the map, waits for it to load and for the intro to finish; leave the
#    mouse and keyboard alone.
heroes-capture capture tmp/towers-of-doom-structures.json

# 3. Stitch: writes maps/towers-of-doom/towers-of-doom-structures.png, a preview, the geo file...
heroes-capture stitch tmp/towers-of-doom-structures.json --tiles
```

The screenshots are kept as raw `.npy` arrays (fast for the stitch to read; older runs' PNG tiles
still stitch).

Use `--structures hide` for bare terrain; it writes `…-terrain` files alongside.

Map names are as the game shows them (case and punctuation don't matter), or pass a path to any
`.stormmap`. The battleground maps are `.s2ma` archives under content-hash names in the game's
storage; the first run after a game update opens each to index them by name (about 20 s).

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
| `--probe-light` | command sequences at chosen points, two shots after each (`HRS_PROBE_POINTS="x,y;x,y"`, `HRS_PROBE_TILE_PATH="tile:0.5;tile,black:0.5"`; see `probes.py`), to see what each step does to the picture |
| `--probe-sky`   | one edge tile over each skybox; the sky part of each shot is measured (mean colour, spread); `HRS_SKY_SEQUENCE` scripts the swaps                                                           |
| `--probe-waits` | the fixed waits (lighting-refit look, settle before the kept shot, sky swaps) tried shorter on sample tiles and compared with the current ones; and the sky pass with positions further apart (`sky-keep08/`) |

Higher `--px-per-cell` means more screenshots and a closer camera. Past about 64–128 px per
cell there is no more detail: that's the game's own texture resolution.

### Outputs

- `<id>.png`: the full image, cropped to the camera bounds plus `--crop-margin` cells, widened
  to take in all of the map the screenshots show (a map of several arenas: `<id>-m1.png`,
  `<id>-m2.png`, ..., one per arena, each cropped to its own content).
  It has an alpha channel: the void is transparent
- `<id>-preview.jpg`: 2048 px wide (transparency shown over dark grey)
- `<id>-on-white.jpg`: the full image flattened over white, for looking at
- `<id>.geo.json`: pixels per map cell and the image origin in map cells, to place replay
  positions: `px = (x - originCell.x) * pxPerCell`, `py = (originCell.y - y) * pxPerCell`
- `<id>-tiles/` (with `--tiles`): a Google Maps style pyramid, `{z}/{y}/{x}.jpg`, 256 px tiles
- On a map with keyed copies of its own sky (Battlefield of Eternity so far): the sky layers
  for a parallax viewer. `<id>-layer-map.png` (the map), `<id>-layer-background.png` (the sky's
  background art), `<id>-layer-haze.png` (the haze over it, with transparency),
  `<id>-layer-fixed.png` (the fixed skybox: it moves with the camera, so it is a screen
  backdrop), `<id>-composite.png` (background, haze and map together, with transparency),
  `<id>-composite-on-black.png`, `<id>-composite-with-fixed.png` (over the fixed skybox, stretched
  behind everything as a backdrop), and `<id>-layers.json` with each layer's speed against the map
  and where it sits. The sky layers are the sky seen from the middle camera position: the
  shells are a tilted plane (fitted from the sky shots' overlaps), so the map's straight edges
  come out slanted and the layer is a trapezoid, transparent in the corners no shot reaches.
  Every render of a map with its own parallax sky also writes `<id>/sky-layers.json`, the
  measured speeds.
- `<id>-viewer/`: a prototype viewer, `index.html` and the layer images it shows (the map, on a
  map of several arenas each arena's, shown one at a time with buttons to switch, the sky
  layers centred on it; and where the map has them the fixed skybox, background art and haze). Drag to pan, mouse wheel to
  zoom; the sky layers move at their rates behind the map, and zooming moves the camera up and
  down, so they shrink less than the map. Opens straight from the folder (no server needed).

## How it works

- **inject.py** copies the `.stormmap` (an MPQ archive, written through StormLib), reads the map
  size and camera bounds from its `MapInfo`, plans a grid of camera positions, and appends the
  capture script (`capture_script.galaxy`, filled in by `capture_script.py`) to `MapScript.galaxy`,
  called at the end of `InitMap`. It also adds
  the status strip's texture and the solid-colour skyboxes (see below). A map that is several arenas in one (Punisher Arena: one arena per round,
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
  HUD, and sets a straight-down camera. Typing `tile <n>` in chat moves the camera to tile n.
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
  and `LightData.xml` in the game's storage at each run (`game_data.py`, `light_data.py`;
  `--refit-yaw` overrides), so a game update that adds tilesets needs nothing done.
- **Opening events first.** The gates open 3 s in (GameLib's `libCore_gv_bALOpenTheGatesDelay`
  and its countdown timer); then, for 8 real seconds, the map's own timers between the gates and
  its first objective are cut short as each starts (`opening-timers.json`, by the libraries the
  map script includes; Hanamura's payload otherwise comes 3 minutes in), so the objective and
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
  The script sets the camera-fixed skybox (`GameSetBackground` layer 0) white at every tile and
  black on `black`; the tileset's parallax layer and fog are turned off; cloud layers placed in
  the map as doodads (Battlefield of Eternity, Punisher Arena) are hidden by type. Each tile is
  shot over white and over black, and the stitch turns the pair into colour and transparency
  (difference matting: the difference between the shots is exactly the see-through share; the
  white level is measured from the shots, the game renders it at about 230; pixels that changed
  between the shots other than by the sky, an animated glow, stay opaque). Whether a map's
  void shows the sky is read from its tileset (from the game's data, with the map's own overrides:
  the lowest terrain level undrawn, or a skybox). Without that (Dragon Shire, Towers of Doom,
  Tomb of the Spider Queen) the void is terrain drawn black, which no skybox shows through:
  one shot per tile, over black, and the stitch makes the near-black that is connected to the
  outside transparent. `--probe-sky` shows each colour on one edge tile.
- **The status strip (`status.py`).** How the capture knows a command has been carried out:
  the map script draws a dialog in the top-left corner, two columns of black-or-white cells on
  a black backdrop down the whole left edge, redrawn at the end of every chat command and every
  sweep. It carries a locator, the sequence number of the last command carried out (the
  capture appends one to each command it sends), the camera's actual target (so clamping at
  the map's edge is known, and the camera bounds are measured by sending the camera to two
  corners), which sky is up, a tick, the game clock, the map's identity and phase, and a parity
  bit (`status.py` has the layout). Reading it is a few pixel averages, so the capture polls it
  fifty times a second and shoots the moment the acknowledgement appears, instead of waiting
  fixed times. The echoed camera position (to 1/64 cell) is what places each screenshot; the
  scale (pixels per cell) comes from the image matches between neighbours. The strip's 64 px
  column is blanked in the kept shots and left out of the stitch (`status.pageLeft`).
- **The game's state, and recovery.** capture.py tells the game's states apart from a screen
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
- **capture.py** starts Heroes through the Battle.net app if it isn't running (started
  directly it can't authenticate; `--battlenet` or `HRS_BATTLENET` if the app isn't in the
  usual place), then launches the map through `Support64\HeroesSwitcher_x64.exe` (Heroes must be
  at the main menu: a running match keeps its map), waits for the status strip, on a map with its own sky, measures and shoots the sky layers
  while the map gets ready (or once it is ready, when an in-game hero selection hides the world
  meanwhile: Punisher Arena), measures the camera bounds the game really applies (an arena's are far tighter than its map file says)
  from where the camera stops when sent to two corners, re-plans the grid from them, sends
  each tile with its position (`tile <n> <x> <y>`), records where the camera really went
  (`positions.json`), and for each tile takes the kept image once `tile` has been carried out, then (number pad 5, or the `black` command if the key went
  missing)
  the same view over the black skybox. It only types while the game is in front, and puts the
  game in front only as the run starts (or as it starts the game): alt-tabbing away pauses the
  run until you click back into the game. Opening the game's own menu (Esc, Options, the Alt+F4
  dialog) pauses it the same way, until the menu is closed. Either way the tile in progress is
  dropped and redone from its start; black frames are retaken; where map content reaches an
  outer tile's outer edge (Battlefield of Eternity's arches run past the camera bounds), it lifts
  the camera bounds and adds tiles beyond, outwards until the edge is clear (three at most); it stops early if the map stops responding, and
  leaves the match at the end (`quit`; the stitch runs while the game leaves, and the next launch
  waits for the menu).
- **stitch.py** places each screenshot where the map script reported its camera
  (`positions.json`), and matches every screenshot against its neighbours, like panorama
  software, to measure their real offsets: the scale comes from those matches, and all
  positions are solved together, dropping matches that disagree and skipping black or
  featureless screenshots. Neighbours meet along a seam routed through their overlap where the
  two agree best (it goes around things off the ground plane, which shift between views),
  never far from the halfway line, blended over a few pixels so small lighting changes don't
  show as steps. Then the void is made transparent (see above) and the image is cropped to the
  map. Without `positions.json`, screenshots are placed by the matches alone and the
  map-to-pixel conversion in `geo.json` is fitted from the middle of the map outward.

## Files

| File                                       | What it does                                                                                  |
| ------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `src/heroes_capture/`                      | the package; the modules below are in it                                                      |
| `cli.py`                                   | the `heroes-capture` command (Typer, Rich): `map render`, `map list`, `prepare`, `capture`, `stitch` |
| `validated-maps.json`                      | the maps whose renders have been reviewed (`map list`)                                        |
| `ui.py`                                    | the output: the live view or log lines, warnings, progress bars, the log file                 |
| `inject.py`                                | prepares the map: reads it, plans the grid, injects the script, adds the textures, writes the manifest |
| `capture_script.galaxy`, `capture_script.py` | the Galaxy script injected into the map (scene, opening, status strip, chat commands), and its values |
| `sky.py`, `light_data.py`                  | the solid-colour skyboxes; the map's tileset, lighting and sky from the game's definitions     |
| `stormlib.py`                              | MPQ archives through StormLib (ctypes)                                                        |
| `casclib.py`, `game_data.py`               | the game's CASC storage through CascLib; the install, the maps, tilesets, light sets, models   |
| `native.py`, `tools/build_native.py`       | where StormLib and CascLib are; fills `native/` with them (not in git)                         |
| `tools/build_exe.py`                       | builds the one-file `heroes-capture.exe` (PyInstaller) at the release version                 |
| `js_json.py`                               | JSON written as JavaScript writes it, so manifests match those the Node injector wrote        |
| `opening-timers.json`                      | per map library, the timers between the gates and the first objective                          |
| `capture.py`                               | the capture run: start-up, the tiles, recovery                                                |
| `status.py`                                | reads the status strip                                                                        |
| `game_control.py`                          | drives the game: focus, chat, launching, waiting for the map, leaving the match                |
| `game_state.py`                            | tells the game's states apart from a screen grab (`menu-reference/` holds the menu's templates) |
| `game_menus.py`                            | recognises the game's menus over a match (Esc, Options, the exit dialog) at any screen size    |
| `game_window.py`                           | Windows calls: the game's process and window, keyboard and cursor                              |
| `screen.py`                                | screen grabbing and frame comparisons                                                         |
| `probes.py`                                | the `--probe-light` and `--probe-sky` diagnostics                                             |
| `sky_layers.py` | measures the map's sky layers' speeds and shoots them across the map |
| `sky_stitch.py` | the sky layer images, the composites and `-layers.json` |
| `stitch.py`                                | stitches the screenshots and writes the outputs                                               |
| `runlog.py`                                | the run's log (printed and written to `log.txt`)                                              |
| `frames.py` | screenshots on disk (`.npy`, or PNG from older runs) |
| `workers.py` | the ordered thread pool the stitches use |
| `matching.py` | phase correlation, for the stitches' matching |
| `viewer.py`, `viewer.html` | the prototype viewer folder the stitch writes per map image |

## Tests

```sh
python tools/build_native.py   # once: StormLib and CascLib for this machine, into native/
uv run pytest -n auto --dist loadgroup   # or pnpm test from the workspace root
```

- `tests/test_units.py`: the pieces with exact rules (JSON as JavaScript writes it, the script's
  numbers, the grid, lighting, sky textures, the sky measurement's consistency rule).
- `tests/test_simulated.py`: the capture end to end against a simulated game and desktop
  (`tests/sim/fakegame.py`: a virtual clock, the chat commands carried out, frames with the
  status strip): renders in both void modes, a resumed run, lost focus, the game's menu opened
  part way, a silent strip, a crash, the wrong map, the probes; and the stitch of a simulated
  render.
- `tests/test_game_menus.py`: the game's menus told from the map at screen sizes from 600 to
  2160 rows and 16:9 to 32:9 (`tests/sim/menus.py` draws them).
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

- **HUD pieces still visible:** note which ones. There are more hide calls to try.
- **The camera doesn't move when `tile` is typed:** check the chat opens with Enter; the
  script matches any message containing `tile`.

- **Trees or props missing or low-detail:** the camera is too far away for the game's detail
  distance. Raise `--px-per-cell` or `--fov` (both bring the camera closer).

- **Minions flicker into some screenshots, or some screenshots have blurry textures:** raise
  `--settle` of `heroes-capture capture` (the least time from a camera move to the kept screenshot, 0.1 s by
  default); units are swept four times a second.
