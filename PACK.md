# The map pack (format 6)

What heroes-capture writes for each map so that a map viewer, built in another project, can show
it: quickly at first, then sharper as the user zooms in, down to full detail. This document is the
contract between the two. The viewer builds against it, and heroes-capture writes to it. A change
a viewer would notice bumps the format number. Format 2: the sky layers' pictures are squared off,
each filling its whole rectangle (see [Sky layers fill their rectangles](#sky-layers-fill-their-rectangles)).
Format 3: each tiled layer has an [overview](#overviews), and the elements' cut-outs are in
[atlases](#elements), at two scales, instead of a file each: a map's opening view takes a handful
of requests however many structures it has. Format 4: each structure has an `outpost` beside its
`town`, and the pack lists the [outposts](#elements). Format 5: a layer's PMTiles archive holds
only the levels finer than its [overview](#overviews), each layer's overview is at the level the
opening view needs (the sky layers' a level finer than the map's), and the pack has only what a
viewer reads (the game's other pictures of the map and the reference viewer are in `raw/`).
Format 6: [team colour](#team-colour), a mask of where the map and its structures are drawn in a
team's colour, so a viewer can show the teams in any colours.

A pack is plain static files, made to be hosted as they are (GitHub Pages for now) and read by a
browser with no server code.

**Status:** heroes-capture writes packs at the end of every render (`capture/pack.py`), with a
reference viewer beside each (`raw/index.html`; `heroes-capture map view <map>` opens it).

## Contents

- [Folder layout](#folder-layout)
- [pack.json](#packjson)
- [Coordinates](#coordinates)
- [Layers and drawing](#layers-and-drawing)
- [Tile pyramids](#tile-pyramids)
- [Images](#images)
- [Team colour](#team-colour)
- [Raw layers](#raw-layers)
- [Hosting](#hosting)
- [Later](#later)

## Folder layout

`map render` writes one folder per map, `<output-dir>/<map id>/`. The map id is the map's name in
lower case, words joined by hyphens: `battlefield-of-eternity`.

```text
maps/battlefield-of-eternity/
  pack/                       the pack: what a viewer loads; host this folder as it is
    pack.json                 what's in the pack and how to draw it (below)
    map.pmtiles               the map: a tile pyramid (one per arena on a map of several)
    map-overview.webp         its overview: the coarsest level drawn, whole
    background.pmtiles        the sky's background art, a tile pyramid, and its overview
    haze.pmtiles              the sky's haze, a tile pyramid, and its overview
    fixed.webp                the fixed skybox: one screen-sized backdrop, not tiled
    thumbnail.webp            the map, small, for a map picker
    images/                   the custom minimap as SVG, the loading screen and its icons (see Images)
    elements/                 the elements render's atlases (see Elements)
  raw/                        not part of the pack: full-resolution PNGs of the layers, the game's
                              own pictures of the map, and the reference viewer (index.html)
```

The pack is for the map with its structures (forts, towers, cores, gates) kept. A render with
them hidden writes the same layout into `terrain/` inside the map's folder, and the elements render
(`--structures elements`, in development: the bare terrain with each structure and camp cut out
on its own, see [Elements](#elements)) into `elements/`. A map without the
sky layers (no parallax sky in the game) has no `background`, `haze` or `fixed`.

## pack.json

The pack's only entry point. A viewer reads it first and finds everything else from it. An
example, abridged (Battlefield of Eternity; the layer values are from a real render, others
illustrative):

```json
{
  "format": 6,
  "tool": "heroes-capture 0.2.0",
  "gameBuild": 98025,
  "map": {
    "id": "battlefield-of-eternity",
    "name": "Battlefield of Eternity",
    "category": "Battleground",
    "validated": true,
    "structures": "keep",
    "sizeCells": [248, 216],
    "cameraBounds": { "left": 9, "bottom": 7, "right": 239, "top": 200 }
  },
  "capture": { "pxPerCell": 48, "screen": [3440, 1440], "fov": 8.02 },
  "arenas": null,
  "layers": [
    {
      "id": "fixed", "kind": "fixed", "file": "fixed.webp", "size": [3376, 1440]
    },
    {
      "id": "background", "kind": "parallax", "file": "background.pmtiles",
      "size": [8508, 4788], "levels": 6, "tileSize": 512, "rate": 0.4573,
      "overview": { "file": "background-overview.webp", "level": 3, "size": [2127, 1197] },
      "centreCell": [124.0, 103.0], "centrePixel": [4222.0, 2571.0], "pxPerCell": 22.1409
    },
    {
      "id": "haze", "kind": "parallax", "file": "haze.pmtiles",
      "size": [8757, 4940], "levels": 6, "tileSize": 512, "rate": 0.4777,
      "overview": { "file": "haze-overview.webp", "level": 3, "size": [2190, 1235] },
      "centreCell": [124.0, 103.0], "centrePixel": [4346.0, 2660.0], "pxPerCell": 23.1298
    },
    {
      "id": "map", "kind": "map", "file": "map.pmtiles",
      "size": [12866, 9260], "levels": 6, "tileSize": 512, "rate": 1.0,
      "overview": { "file": "map-overview.webp", "level": 2, "size": [1609, 1158] },
      "teamMask": { "file": "map-team.pmtiles",
                    "overview": { "file": "map-team-overview.webp", "level": 2, "size": [1609, 1158] } },
      "originCell": [-2.2519, 204.4259], "pxPerCell": 48.4038
    }
  ],
  "images": {
    "thumbnail": { "file": "thumbnail.webp", "size": [512, 369] },
    "customMinimapSvg": { "file": "images/custom-minimap.svg", "size": [496, 416], "source": "CustomMiniMap.dds",
                          "boundsCells": { "left": 0.0, "bottom": -1.25, "right": 248.0, "top": 206.75 } },
    "loadingScreen": { "file": "images/loading-screen.webp", "size": [1920, 1080],
                       "source": "ui_ingame_mapmechanic_loadscreen_battlefieldofeternity.dds",
                       "smaller": [ { "file": "images/loading-screen-960w.webp", "size": [960, 540] },
                                    { "file": "images/loading-screen-480w.webp", "size": [480, 270] } ] },
    "loadingScreenIcons": [
      { "file": "images/loading-screen-icon-1.webp", "size": [292, 184], "source": "ui_ingame_mapmechanic_loadscreen_eternity_icon1.dds",
        "smaller": [ { "file": "images/loading-screen-icon-1-146w.webp", "size": [146, 92] },
                     { "file": "images/loading-screen-icon-1-73w.webp", "size": [73, 46] } ] }
    ]
  },
  "data": {},
  "teamColours": { "order": [101, 159, 255], "chaos": [255, 57, 48], "maskScale": 2.0 },
  "files": {
    "map.pmtiles": { "bytes": 61234567, "sha256": "…" }
  }
}
```

| Field | Meaning |
|---|---|
| `format` | This document's format number. A viewer refuses a format it doesn't know. |
| `tool` | The heroes-capture version that wrote the pack. |
| `gameBuild` | The game's build number the map and images came from (`null` if unknown). |
| `map.id`, `map.name` | The folder's id and the map's name as the game shows it (English). |
| `map.category` | `Battleground`, `Arena`, `Brawl` or `Other` (as `map list` shows them); `null` if unknown. |
| `map.validated` | The tool's authors have checked this map's render. Information only. |
| `map.structures` | `keep`: structures are in the map layer; `hide`: they aren't; `elements`: the map layer is the bare terrain, and each structure and camp is in `data.elements`. |
| `map.sizeCells` | The whole map in map cells (width, height). |
| `map.cameraBounds` | Where the game lets the camera go, in map cells. A viewer can keep its camera inside. |
| `capture` | How the render was taken: map pixels per cell planned, the game's screen size, field of view in degrees. |
| `arenas` | `null`, or the arenas of a map of several (see [Several arenas](#several-arenas)). |
| `layers` | The layers, back to front: draw them in this order (see [Layers and drawing](#layers-and-drawing)). |
| `images` | The map's other pictures, each with its file and size in pixels (see [Images](#images)). Any may be missing. |
| `teamColours` | The colours the pictures' team colour was shot in, and a mask byte's worth (see [Team colour](#team-colour)). |
| `data` | Information about the map, to grow. `elements` in the elements render (see [Elements](#elements)); otherwise empty. |
| `files` | Every file in the pack (`pack.json` aside), its size and SHA-256. A viewer can cache by hash (GitHub Pages caches for only ten minutes). |

`pack.json` is written compact (no spaces or line breaks; the example is spread out to be read),
and the pack's WebP files carry no metadata.

## Coordinates

The world unit is the game's **map cell**: x grows east, y grows north, (0, 0) is the map's
south-west corner. Positions from replays and from the game's data are in map cells, so a viewer
needs no other world coordinates.

Images are in pixels with y growing south (down), (0, 0) at the top-left.

## Layers and drawing

Each layer is an image with its own scale and its own movement against the map. The viewer has
a camera: the map cell `(cx, cy)` at the middle of its window, and a zoom `z`, the window's
pixels per map-image pixel. At `z = 1` the map is drawn at the resolution it was captured at,
which is close to how the game shows it.

For every layer, each frame:

1. **Scale.** Draw the layer at `s = 1 / (1 + rate · (1/z − 1))` window pixels per layer pixel.
   For the map (`rate` 1) that is `z`. Layers further back (smaller `rate`) shrink and grow less
   as the camera rises and falls, as in the game.
2. **Position.** Find `(u, v)`, the layer's pixel that belongs at the window's middle, and draw
   the layer offset so that pixel lands there: its top-left at
   `(windowWidth/2 − u·s, windowHeight/2 − v·s)`.

The pixel at the window's middle, `(u, v)`, depends on the layer's kind:

- **`map`**: `u = (cx − originCell.x) · pxPerCell`, `v = (originCell.y − cy) · pxPerCell`.
- **`parallax`** (the sky's background art and haze):
  `u = centrePixel.x + pxPerCell · (cx − centreCell.x)`,
  `v = centrePixel.y − pxPerCell · (cy − centreCell.y)`.
  Here `pxPerCell` is the layer's own pixels per map cell of camera movement (its `rate`
  times the scale it was captured at). A parallax layer is a view of the sky from the camera at
  `centreCell`, as if the screen were large enough to show all of it.
- **`fixed`**: none. It moves with the camera: draw it filling the window, behind everything
  (cover, not stretched), at every zoom.

The order in `layers` is the drawing order, back to front: fixed skybox, background art, haze,
map. Every layer except `fixed` has transparency (the map where the game lets the void through,
the haze everywhere), so the layers behind show through.

### Sky layers fill their rectangles

The sky shells are a finite panel seen in perspective, so the parts of them the render sees are
trapezoids: past their edges the window would show the layer behind, or nothing, a cut-off sky the
game never shows. heroes-capture shoots as much of the shells as it can (from across the camera
bounds, and through wider lenses for the rest) and squares each parallax layer off: its picture is
the largest rectangle inside what it saw, so **a `parallax` layer's picture fills its whole
rectangle** (`size`). The background art keeps the gaps where it lets the fixed skybox through, and
the haze its clear patches: those are the sky, not its edge.

So a viewer keeps the window inside every parallax layer's rectangle and never shows a sky edge. The
window shows the layer's pixels from `u − windowWidth/(2s)` to `u + windowWidth/(2s)` across and
`v − windowHeight/(2s)` to `v + windowHeight/(2s)` down (`s`, `u` and `v` as above); the layer fills
the window when those are inside `0`..`size`.

### Several arenas

On a map of several arenas (Punisher Arena) each arena is its own map layer, and `arenas` lists
them: `[{"id": "m1", "label": "M1", "layer": "map-m1", "boundsCells": {...}, "middleCell": [x, y]}]`.

A viewer shows one arena at a time, with its map layer and the sky layers. While an arena is
shown, the sky layers take that arena's `middleCell` in place of their `centreCell`: the sky sits
behind the arena the camera is over, as the game draws it. The camera stays inside the shown
arena's `boundsCells`.

## Tile pyramids

Each layer except `fixed` is a tile pyramid in one [PMTiles](https://github.com/protomaps/PMTiles)
(version 3) file. PMTiles is a single-file tile archive with an index at its start. A viewer
fetches the index once and then each tile by an HTTP range request, using the
[`pmtiles`](https://www.npmjs.com/package/pmtiles) JavaScript reader, from static hosting with
no tile server.

- **Levels.** Level `levels − 1` is the full image. Each level below halves it, down to level 0,
  which fits in one tile. A level's pixel `(i, j)` covers full-resolution pixels
  `[i·2^k, (i+1)·2^k) × [j·2^k, (j+1)·2^k)`, where `k = levels − 1 − level`. Pixels and tiles
  line up exactly at every level, so a viewer can blend two levels for smooth zooming.
- **Which levels are stored.** The archive holds the levels finer than the layer's
  [overview](#overviews)'s (`overview.level + 1` to `levels − 1`); the overview is its own level,
  whole, and nothing coarser is kept. A layer of one level (it fits in one tile) is the exception:
  its archive holds that level too.
- **Tiles.** 512 × 512 pixels. Tile `(x, y)` at a level holds that level's pixels
  `[512x, 512x + 512) × [512y, 512y + 512)`. A tile at the right or bottom edge is padded with
  transparency to the full 512.
- **Addresses.** PMTiles' `z/x/y` is `level/x/y`, with y counting rows down from the top.
- **Encoding.** WebP. The map's and the background art's tiles: colour lossy at quality 85,
  transparency exact (WebP stores it losslessly). The haze's, which are mostly transparency:
  colour at quality 90, transparency lossy at quality 50. The overviews as their layer's tiles. Each level is the full image box-filtered down (on premultiplied colour, so
  transparent edges don't darken): every pixel the average of the full-resolution pixels it covers.
- **Empty tiles.** A tile with nothing in it (fully transparent) isn't stored. A tile missing
  from the archive is transparent.
- **Which level to draw.** At scale `s` (window pixels per layer pixel), the level whose pixels
  are about one window pixel: `levels − 1 − floor(log2(1/s))`, clamped to `overview.level` ..
  `levels − 1`. Zoomed out past the overview's level, draw the overview smaller. For smooth
  zooming, draw the next coarser level under it (down to the overview) and fade between the two.
- **PMTiles' own header.** Its minimum and maximum zoom are the first level stored and
  `levels − 1`; its bounds and centre (made for maps of the Earth) mean nothing here. `pack.json`
  has everything a viewer needs.

### Overviews

Each tiled layer's `overview` is one of its pyramid's levels whole, as a single WebP (`level`,
counted as the pyramid's levels are, 0 the coarsest; `size` in that level's pixels, the image's
size at that level rounded up). It is the coarsest level a viewer draws, and the archive doesn't
hold it (see [Tile pyramids](#tile-pyramids)). A viewer fetches it first and cuts it into that
level's tiles (`tileSize` apart, the ones past its right or bottom edge padded with transparency),
so the whole layer is on screen after one request, and reads the archive only for the finer
levels, as the camera zooms in.

Its level is the one the map's opening view needs:

- **`map`**: the finest level no longer than 2048 px on a side (but never the full image, which
  is the archive's).
- **`parallax`**: the level drawn at the zoom where the map's overview is a pixel to a window
  pixel (`z = 2^(overview.level − (levels − 1))` of the first map layer): there a layer further
  back is drawn larger than the map (see [Layers and drawing](#layers-and-drawing)), so its
  overview is usually a level finer, up to 4096 px on a side.

## Images

Each is in `images` only if the map has what it is made from.

| Key | File | What |
|---|---|---|
| `thumbnail` | `thumbnail.webp` | the map layer, 512 px wide, over the dark backdrop |
| `customMinimapSvg` | `images/custom-minimap.svg` | the custom minimap `MapInfo` names (`CustomMiniMap.dds`, `.tga` on some maps: the drawn minimap the game shows) redrawn as vector shapes (below) |
| `loadingScreen` | `images/loading-screen.webp` | the map's loading screen picture (the background only), lossy, opaque |
| `loadingScreenIcons` | `images/loading-screen-icon-<n>.webp` | a list: the pictures the loading screen's layout places on it (the objectives' icons), in the layout's order, exact, with their transparency |

Each entry is `{"file", "size"}`, all but the thumbnail's with `source`, the game file it came
from (a viewer can match an icon to an objective by its name, e.g.
`ui_ingame_mapmechanic_loadscreen_eternity_icon1.dds`). The loading screen's and its icons' also
have `smaller`: the same picture at a half and a quarter of its size (`<name>-<width>w.webp`, each
`{"file", "size"}`, largest first), so a viewer can take the smallest that is sharp where it draws
it (an `<img>`'s `srcset`). The loading screen is the texture `MapInfo`
names, or else the `DraftIntroImage` of the game's map entry (`CMap` in the shared data's
`MapData.xml`) `MapInfo` names; the icons are the layout's `Texture` values (a texture, or an asset
name looked up in the game's `Assets.txt` lists), less the loading screen itself; where the layout
`MapInfo` names isn't in the game's data (Warhead Junction), the textures named after the loading
screen (`ui_ingame_mapmechanic_loadscreen_<its last part>_icon<n>.dds`). The game's pictures of the
map are also kept in `raw/images/` as PNG, not in the pack: `minimap.png` (`Minimap.tga`),
`custom-minimap.png` and `custom-minimap-hover.png`, `replay-preview.png`
(`ReplaysPreviewImage.tga`), `map-select.png` (the picture `DocumentInfo` names) and
`loading-screen.png` with its icons.

### The custom minimap as SVG

`customMinimapSvg` is the custom minimap redrawn from the shapes it's made of. Its entry also has
`boundsCells`, `{left, bottom, right, top}`: the map cells it covers. Draw the SVG stretched to that
rectangle and it lies on the map layer (its viewBox is the picture's size in pixels, and it has
`preserveAspectRatio="none"`).

The rectangle is the picture at the map's scale: a whole number of pixels per cell (the picture's
width over the map's, 2 on most maps, 4 on Trial Grounds). It isn't centred on the map: it's centred
on the middle of the map's camera bounds, 2.25 cells lower (measured over the maps that have one,
against their renders). A few maps' minimaps don't follow that exactly; their corrections, set by
looking at the minimap over the render, are in each map's own module (`maps/`: Volskaya Foundry's
is drawn 2 cells shorter).

Its parts, by id, back to front, with their classes:

| Id | Class | What |
|---|---|---|
| `#outline` (in `defs`) | | one path down the middle of the light outline, round the whole map; at each nexus the circle's arc running on into the cut-ins, and one curve from each cut-in's tip out to the border |
| `#out-stroke` | `out-stroke` | the black: the outline stroked wide behind the main shape, so only the half outside it shows, and `#cut-ins`, the black in each cut-in between the circle's arc and the curve out to the border, narrowing to the tip |
| `#body` | `bg inner-stroke` | the main shape: the outline filled with the body colour (`bg`) and stroked with the light line (`inner-stroke`) |
| `#fog` | `fog` | the soft lighter areas: one shape, blurred, clipped to the outline |
| `g#camps` | `camps` (each path) | the darker spots (camps) |
| `g#parts` | `terrain` (each path) | the light parts, each in its own colour |
| `g#nexus-<n>` | `nexus` (each path) | each nexus's swirl |

The colours are written as presentation attributes, so a stylesheet overrides them when the SVG is
in the page: `.bg { fill: … }`, `.inner-stroke { stroke: … }`, `.out-stroke { stroke: …; fill: … }`
(the fill colours the cut-ins), and `fill` for `.fog`, `.terrain`, `.camps` and `.nexus`.

The outline's strokes don't scale (`vector-effect: non-scaling-stroke`, on `#outline` itself): at any
size the black edge and the light line keep the widths they have at the picture's own size, in screen
pixels. That holds where the SVG is part of the page (inline); drawn as an `<img>`, a browser scales
the whole picture, strokes and all.

Maps whose custom minimap isn't in their archive have neither image (Alterac Pass, Hanamura
Temple and Pull Party name one in `MapInfo` that the game's data doesn't have).

## Team colour

The game draws everything that has a team in the colour of our player's ally (blue) or enemy (red):
the structures, and on some maps doodads in the terrain (Battlefield of Eternity's Heaven lamps and
crystal beds, Hell's core range). The pack is drawn from Order's side, so Order's team is blue and
Chaos's red. heroes-capture shoots those pictures from both sides, and the pack carries a
**team-colour mask** for them: how much of each pixel is its team's colour. A viewer adds that
amount of a different colour to show the teams in any colours: swapped, or its own.

- **The mask** has two amounts per pixel: red, Order's team's; green, Chaos's (a pixel belongs to
  one team; blue is unused). A byte `v` is `v / 255 · maskScale` of the team's colour. Lossless WebP.
- **Drawing a pixel in other colours:** `rgb' = rgb + R/255 · maskScale · (order' − order) + G/255
  · maskScale · (chaos' − chaos)`, each channel clamped to 0..255, where `order` and `chaos` are
  `teamColours`' and `order'`, `chaos'` the colours wanted; alpha unchanged. With the colours as
  shot this is the picture itself; with them swapped it is the picture from Chaos's side, within a
  few levels (about 8 on average where team colour is, more in the brightest glows).
- **The map layer's mask** (`teamMask` on a `map` layer, only where the map has team colour) is
  tiled and levelled exactly as the layer (same `levels`, `tileSize`, overview level, stored
  levels), its tile `(x, y)` at a level over the same pixels; a tile missing from its archive has no
  team colour. Its own overview, as the layer's.
- **The elements' masks:** a standing state with team colour has `teamMask`, `{"at", "small"}`:
  its mask's top-left corner in the `team` atlas (the cut-out's size, over the same `rect`) and in
  `teamSmall` (at the `small` atlas's scale, over its `small` picture).
- The sky layers and rubble have no team colour; neither do the camps.

## Elements

The elements render's map layer is the bare terrain: every structure was destroyed and its remains
cleared before the tiles were shot. Each structure and mercenary camp was shot on its own over the
sky instead, and `data.elements` lists them with their cut-outs, so a viewer can show or hide each
one, or show the map as it stood at a moment of a replay.

```json
"elements": {
  "layer": "map",
  "atlases": {
    "standing": {"file": "elements/standing.webp", "size": [4096, 1420], "scale": 1},
    "rubble": {"file": "elements/rubble.webp", "size": [4096, 1690], "scale": 1},
    "masks": {"file": "elements/masks.webp", "size": [4096, 1380], "scale": 1},
    "small": {"file": "elements/small.webp", "size": [1024, 410], "scale": 0.125}
  },
  "structures": [
    {"id": 1013, "type": "TownCannonTowerL2", "cell": [108, 60], "owner": "order", "town": 3, "outpost": null, "core": false,
     "states": {"standing": {"rect": [5120, 3410, 140, 132], "atlas": "standing", "at": [860, 0], "small": [108, 0],
                             "hiddenBy": [{"id": 1014, "at": [860, 0]}]},
                "rubble": {"rect": [5126, 3418, 128, 118], "atlas": "rubble", "at": [0, 140], "small": [0, 18]}}}
  ],
  "towns": [{"town": 3, "lane": 2, "owner": "order", "region": 12, "name": "Lane 2 - Order - Town 1"}],
  "outposts": [{"outpost": 1, "town": 1, "lane": 1, "owner": "order", "cell": [75.9, 100.3]}],
  "camps": [
    {"camp": 2, "type": "BruiserCamp1", "cell": [95.86, 94.95],
     "states": {"spawned": {"rect": [4200, 4980, 180, 330], "atlas": "standing", "at": [1000, 0], "small": [125, 0]}}}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `layer` | The map layer the cut-outs belong to (their rectangles are in its pixels). |
| `atlases` | The cut-outs' pictures, packed: `standing` (the standing structures and the camps), `rubble`, `masks` (see `hiddenBy`), each at the map layer's scale (`scale` 1), `small`, every state's cut-out at the map layer's overview scale (`scale`: 1/8 for a six-level pyramid), and `team` and `teamSmall`, the standing states' [team-colour masks](#team-colour) at those two scales. WebP: the cut-outs' colour lossy at quality 90 with their transparency exact, 4 transparent pixels apart (lossy colour is coarser than the pixels); the masks lossless. `size` in pixels. |
| `structures` | Every structure the map places: its placed unit's `id`, its unit `type`, its `cell`, its `owner` (`order`, `chaos`), the `town` it belongs to (`null`: the cores and anything outside the towns), the `outpost` it belongs to (`null`: the town hall's ring and whatever belongs to no outpost) and whether it's a `core`. |
| `towns` | The map's towns as its script numbers them: lane, owner, and the map's region for it. A town is the game's: the region whose structures its town hall's fall affects (the hall's fountains fall with it). A region can reach far down the lane, so the towers, gate and fountain the map adds there are in the town too. |
| `outposts` | A town's structures beyond its hall's ring (15 cells: hall, fountain, gate, towers, walls, and the standalone tower a fort takes with it), joined when within 14 cells of one another: the lane's forward towers, gate and fountain as a group of their own. Numbered in town order, nearest the hall first; each with its `town`, `lane`, `owner` and the `cell` in the middle of its structures. |
| `camps` | The mercenary camps as the script numbers them (the game's own camp numbers), their defender `type` and the `cell` they gather round. |
| `states` | Each state the element was shot in: `standing` and `rubble` for a structure (a tower's rubble as the game shows a fallen one), `spawned` for a camp's defenders. `null`: nothing is left of it in that state (a fallen moonwell). A state that couldn't be shot is missing. |
| `rect` | Where the state's cut-out goes on the map layer: left, top, width, height in its pixels. |
| `atlas`, `at` | The cut-out's picture: its top-left corner in that atlas (`standing` or `rubble`), `rect`'s width and height in size. |
| `small` | The same cut-out's top-left corner in the `small` atlas, where it is ⌈width · scale⌉ by ⌈height · scale⌉ pixels (the atlas's `scale`). Zoomed out, draw this one; zoomed in past about twice its scale, the full one. |
| `hiddenBy` | A standing structure's only: where a neighbouring structure stands in front of it (a wall over a tower's base, but under its orb), a mask per neighbour (`id`, and `at`: its top-left corner in the `masks` atlas, the size of the cut-out, opaque where hidden). While that neighbour is standing too, erase the masked pixels from this cut-out before drawing it. At the small scale the overlaps are a pixel or two: no masks are needed. |

Draw the cut-outs over the map layer: every one shown as rubble first, then the rest (standing
structures, camps), each further north first (by `cell` y, larger first), so nearer ones overlap
further ones as in the game and rubble, which lies on the ground, never covers a standing building; where two standing structures overlap, their `hiddenBy`
masks settle which is in front, whichever is drawn first. They carry no shadows: each was shot with nothing round
it. The colours are Order's side's (Order blue); a standing state's `teamMask` recolours it (see
[Team colour](#team-colour)).

## Raw layers

Next to the pack, `raw/` holds each layer at full resolution as PNG (lossless): `map.png` (or
`map-<arena>.png`), `background.png`, `haze.png` and `fixed.png`, and with `stitch --composites`
the composites (`composite.png`, `composite-on-black.png`, `composite-with-fixed.png`). Its
`layers.json` places the layers the same way as `pack.json` does. They're for keeping and for
further work, not for a viewer: the pack's tiles come from them. The elements render's cut-outs are
in `raw/elements/` as PNG, one file each, as they were before being packed into the atlases; the
game's pictures of the map in `raw/images/` (see [Images](#images)); and the reference viewer,
`index.html`, which reads `../pack/`.

## Hosting

- **Range requests.** A viewer reads PMTiles by range requests, which GitHub Pages serves. The
  packs are published from [heroes-maps](https://github.com/myrddraall/heroes-maps) (its `maps/`
  folder, a catalog at `maps/index.json`, each pack at `maps/<map id>/`), served by Pages at
  `https://myrddraall.github.io/heroes-maps/`.
- **Size limits.** git refuses files over 100 MB, and GitHub Pages expects a site under about
  1 GB. One map's pack is expected to come to about 60–100 MB, so Pages holds a handful of maps,
  not all of them. Past that, packs can move to an S3-style bucket (Cloudflare R2, for example)
  without any change to the format.
- **Caching.** Pages sends a ten-minute cache time. A viewer that caches tiles keys them by the
  hashes in `pack.json`'s `files`.

## Later

- More map data in `data`: objectives and lanes.
