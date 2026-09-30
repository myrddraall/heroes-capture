# map-capture

Renders top-down images of Heroes of the Storm battlegrounds by driving the game itself: a
script injected into the map hides the HUD, the fog of war and the units, points the camera
straight down and steps it across the map while screenshots are taken, and the screenshots are
stitched by their known positions. Structures (forts, towers, cores, gates) can be kept or
hidden, so each map can be rendered both ways.

Runs on Windows with the game installed. Start Heroes from the Battle.net app first, so it's
logged in; capture.py then hands the prepared map to the running game.

## Setup (once)

- [Node.js](https://nodejs.org) 20 or later, and [Python](https://www.python.org) 3.11 or later
- In this folder:

  ```powershell
  npm install
  pip install -r requirements.txt
  ```

- In the game's options: **Display Mode: Windowed (Fullscreen)** (screenshots of exclusive
  fullscreen come out black), your monitor's native resolution, graphics on Ultra.

## Render a map

`run.cmd` holds the current step (a render or a diagnostic) and its settings, so a personal
`update.cmd` that refreshes the files and calls it needs no arguments. `update.cmd` is not
tracked: it contains the machine's source path.

The quick way is `render.cmd`, which runs all three steps at 3440x1440 against
`D:\Games\Heroes of the Storm` (edit `SCREEN` and `GAME` at the top to change them), and
installs what's needed on first run:

```powershell
render.cmd                          # Towers of Doom, structures kept
render.cmd "Cursed Hollow" hide     # another map, bare terrain
```

The steps it runs:

```powershell
# 1. Prepare: downloads the map, injects the capture script, plans the grid.
node inject.mjs "Towers of Doom" --structures keep --screen 3840x2160

# 2. Capture: launches the map, waits for it to load and for the intro to finish; leave the
#    mouse and keyboard alone.
python capture.py work/towers-of-doom-structures.json

# 3. Stitch: writes work/towers-of-doom-structures.png, a preview, and the geo file.
python stitch.py work/towers-of-doom-structures.json --tiles
```

Use `--structures hide` for bare terrain; it writes `…-terrain` files alongside.

Map names are the file names in
[jamiephan/HeroesOfTheStorm_S2MA/maps](https://github.com/jamiephan/HeroesOfTheStorm_S2MA/tree/main/maps)
(kept current with the live game), or pass a path to any `.stormmap`.

### Options (inject.mjs)

| Option                         | Default          | Effect                                                                                                                                                                                                           |
| ------------------------------ | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--structures keep\|hide`      | `keep`           | keep or hide forts, towers, cores and gates                                                                                                                                                                      |
| `--px-per-cell <n>`            | `48`             | output resolution; a map is ~220 cells wide, so 48 gives ~10,600 px                                                                                                                                              |
| `--screen <w>x<h>`             | `3840x2160`      | the game's resolution while capturing; match your monitor                                                                                                                                                        |
| `--fov <deg>`                  | `20`             | field of view; narrower is flatter (less lean on tall objects) but puts the camera further away                                                                                                                  |
| `--distance <units>`           |                  | camera distance instead of `--fov` (the field of view is then chosen to keep the scale); `render.cmd` uses 214                                                                                                     |
| `--keep <0..1>`                | `0.6`            | share of each screenshot used, centred; the rest is overlap, used to measure the scale                                                                                                                           |
| `--no-lens`                    |                  | don't set the field of view, far clip or yaw (see troubleshooting)                                                                                                                                               |
| `--keep-mechanics`             |                  | keep map-mechanic units such as altars, which may sit over holes in the terrain (experimental)                                                                                                                   |
| `--freeze`                     |                  | pause model animations (experimental)                                                                                                                                                                            |
| `--markers`                    | on in render.cmd | numbered registration markers and two shots per tile: one with the markers (calibration: exact camera geometry, and proof the view is the right tile) and one without, which is the image kept. See `markers.py` |
| `--show-ui`                    |                  | diagnostic: leave the HUD up, launch the map and stop                                                                                                                                                            |
| `--probe-zoom` / `--probe-fov` |                  | diagnostic: with `--markers`, measure at which camera distance / field of view the markers still draw; writes `work\<what>-probe.txt`                                                                            |
| `--margin <cells>`             | `0`              | also capture beyond the map's camera bounds (lifts them)                                                                                                                                                         |
| `--crop-margin <cells>`        | `12`             | the stitched image reaches this far past the camera bounds (or past each arena's area)                                                                                                                         |
| `--sky`                        | off              | add solid-colour skyboxes (black, white, magenta, lime, cyan; `sky.mjs`), for chroma keying and difference matting; chat `sky <colour>` swaps them at run time (experimental)                                  |
| `--sky-start <colour>`         | `black`          | the skybox the map starts with                                                                                                                                                                                 |
| `--probe-sky`                  |                  | diagnostic: with `--sky`, one edge tile over each skybox; the sky part of each shot is measured (mean colour, spread)                                                                                          |

Higher `--px-per-cell` means more screenshots and a closer camera. Past about 64–128 px per
cell there is no more detail: that's the game's own texture resolution.

### Outputs

- `<id>.png`: the full image, cropped to the camera bounds plus `--crop-margin` cells (a map of
  several arenas: `<id>-m1.png`, `<id>-m2.png`, ..., one per arena, each cropped to its area).
  With `--sky` (the default in render.cmd) it has an alpha channel: the void is transparent
- `<id>-preview.jpg`: 2048 px wide (transparency shown over dark grey)
- `<id>.geo.json`: pixels per map cell and the image origin in map cells, to place replay
  positions: `px = (x - originCell.x) * pxPerCell`, `py = (originCell.y - y) * pxPerCell`
- `<id>-tiles/` (with `--tiles`): a Google Maps style pyramid, `{z}/{y}/{x}.jpg`, 256 px tiles

## How it works

- **inject.mjs** copies the `.stormmap` (an MPQ archive), reads the map size and camera bounds
  from its `MapInfo`, plans a grid of camera positions, and appends the capture script
  (`capture-script.mjs`) to `MapScript.galaxy`, called at the end of `InitMap`. Nothing else in
  the map changes. A map that is several arenas in one (Punisher Arena: one arena per round,
  stacked on the map, the camera bounds moved to the round's arena at run time) marks each
  with a region named `..._MapBounds` in its `Regions` file; with two or more, each gets its
  own grid, the script lifts the camera bounds so the camera can reach them all, and the
  stitch writes one image per arena (`<id>-m1.png`, ...).
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
  overriding; tilesets and light sets are in `light-sets.json` (`light-data.mjs` resolves
  them, `--refit-yaw` overrides). After a game update that adds tilesets, rebuild the table:
  extract every `mods/**/GameData/TerrainData.xml` and `LightData.xml` from the game's CASC
  storage into a folder (file names = CASC paths with `__` for the separators) and run
  `node generate-light-sets.mjs <folder>`.
- **Transparent void (`--sky`, `sky.mjs`).** The void around and below a map is the skybox. The
  map gets five solid-colour skyboxes: a skybox is a model on a stock mesh whose textures are
  referenced by path, and a file in the map at that path replaces the game's, so each colour is
  a stock mesh (the Braxis bowl and the "parallax" bowls; the big heaven/Luxoria bowl won't swap
  at run time) plus solid-colour DDS textures, with the mesh's cloud layers made transparent.
  The script sets the camera-fixed skybox (`GameSetBackground` layer 0) white at every tile and
  black on `black`; the tileset's parallax layer and fog are turned off; cloud layers placed in
  the map as doodads (Battlefield of Eternity, Punisher Arena) are hidden by type. Each tile is
  shot over white and over black, and the stitch turns the pair into colour and transparency
  (difference matting: the difference between the shots is exactly the see-through share; the
  white level is measured from the shots, the game renders it at about 230; pixels that changed
  between the shots other than by the sky, an animated glow, stay opaque). `--probe-sky` shows
  each colour on one edge tile (`HRS_SKY_SEQUENCE` scripts the swaps).
- **capture.py** starts Heroes through the Battle.net app if it isn't running (started
  directly it can't authenticate; `--battlenet` or `HRS_BATTLENET` if the app isn't in the
  usual place), then launches the map through `Support64\HeroesSwitcher_x64.exe` (Heroes must be
  at the main menu: a running match keeps its map), learns the game font's digits from a
  reference label, asks the map script for the camera bounds the game really applies (an
  arena's are far tighter than its map file says; the script sends the camera to the four
  corners, reads where it stopped and shows the answer as a 12-digit label), re-plans the
  grid from them, sends each tile with its position (`tile <n> <x> <y>`), and for each tile takes two shots: with the numbered markers (each label is
  just the marker's number, plus one label at the centre with the full tile number, so a stale
  frame is caught), and after `clean` hides them, the image kept. It only types while the game is in front;
  if the game loses focus part way through a tile, that tile is dropped and redone from its
  start once the game is back in front; black frames are retaken; it stops early if the map stops responding, and
  leaves the match at the end (`quit`), ready for the next run.
- **stitch.py** places each screenshot by what it shows, not by where the camera was sent
  (the game can hold the camera back near the edges, and its zoom can differ from the plan):
  like panorama software, it matches every screenshot against its neighbours to measure their
  real offset and solves all positions together, dropping matches that disagree and skipping
  black or featureless screenshots. With markers (the default), each screenshot's marker fit
  anchors it absolutely and image matching only fills in for screenshots whose markers were not
  found. Each pixel comes from the nearest screenshot centre, blended
  across seams so small lighting changes don't show as steps. The map-to-pixel conversion in
  `geo.json` is fitted from the screenshots in the middle of the map outward. Tested on
  simulated screenshots with the zoom 25% off plan, most edge screenshots held back and some
  lighting variation: every screenshot placed, scale and origin exact, output pixel-identical
  to the source apart from the blending.

## Status: untested in the game

Everything up to the game is tested: the injected map re-reads cleanly with every other file
byte-identical, and the stitching is exact on simulated input. What hasn't run yet is the game
itself. Every call in the capture script is one Blizzard's own Heroes map scripts make,
**except** the lens values (`c_cameraValueFieldOfView`, `c_cameraValueFarClip`,
`c_cameraValueYaw`) and `CameraSetBounds` (used only with `--margin`), which are StarCraft II
natives not seen in Heroes scripts.

### Troubleshooting

- **The game asks you to log in, or can't validate the licence:** start the Battle.net app and
  log in first. If it still asks, load the map through Try Mode instead: copy it over Try
  Mode's map, start Try Mode in the game as usual, then capture with `--no-launch`:

  ```powershell
  mkdir "D:\Games\Heroes of the Storm\maps\heroes\singleplayermaps"
  copy work\towers-of-doom-structures.stormmap "D:\Games\Heroes of the Storm\maps\heroes\singleplayermaps\(10)trymemode.stormmap"
  py capture.py work\towers-of-doom-structures.json --no-launch
  ```

  Delete that `(10)trymemode.stormmap` afterwards to get normal Try Mode back.

- **A "script failed to compile" error naming a `c_cameraValue…` constant:** re-run
  inject.mjs with `--no-lens`. Without a narrow field of view, tall objects lean more at the
  screenshot edges; lower `--keep` (e.g. `0.4`) to use only the centre.
- **HUD pieces still visible:** note which ones. There are more hide calls to try.
- **The camera doesn't move when `tile` is typed:** check the chat opens with Enter; the
  script matches any message containing `tile`.
- **Trees or props missing or low-detail:** the camera is too far away for the game's detail
  distance. Raise `--px-per-cell` or `--fov` (both bring the camera closer).
- **Minions flicker into some screenshots, or some screenshots have blurry textures:** raise
  `--settle` in capture.py (the least time from a camera move to the kept screenshot, 0.5 s by
  default); units are swept four times a second.
