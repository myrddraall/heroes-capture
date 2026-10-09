# The map pack (format 1)

What heroes-capture writes for each map so that a map viewer, built in another project, can show
it: quickly at first, then sharper as the user zooms in, down to full detail. This document is the
contract between the two. The viewer builds against it, and heroes-capture writes to it. A change
a viewer would notice bumps the format number.

A pack is plain static files, made to be hosted as they are (GitHub Pages for now) and read by a
browser with no server code.

**Status:** heroes-capture writes packs at the end of every render (`pack.py`), with a reference
viewer in each (`index.html`; `heroes-capture map view <map>` opens it).

## Contents

- [Folder layout](#folder-layout)
- [pack.json](#packjson)
- [Coordinates](#coordinates)
- [Layers and drawing](#layers-and-drawing)
- [Tile pyramids](#tile-pyramids)
- [Images](#images)
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
    background.pmtiles        the sky's background art, a tile pyramid
    haze.pmtiles              the sky's haze, a tile pyramid
    fixed.webp                the fixed skybox: one screen-sized backdrop, not tiled
    thumbnail.webp            the map, small, for a map picker
    images/                   the game's own pictures of the map (see Images)
    index.html                the reference viewer (heroes-capture's check that the pack works)
  raw/                        full-resolution PNGs of the layers and composites; not part of the pack
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
  "format": 1,
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
      "centreCell": [124.0, 103.0], "centrePixel": [4222.0, 2571.0], "pxPerCell": 22.1409
    },
    {
      "id": "haze", "kind": "parallax", "file": "haze.pmtiles",
      "size": [8757, 4940], "levels": 6, "tileSize": 512, "rate": 0.4777,
      "centreCell": [124.0, 103.0], "centrePixel": [4346.0, 2660.0], "pxPerCell": 23.1298
    },
    {
      "id": "map", "kind": "map", "file": "map.pmtiles",
      "size": [12866, 9260], "levels": 6, "tileSize": 512, "rate": 1.0,
      "originCell": [-2.2519, 204.4259], "pxPerCell": 48.4038
    }
  ],
  "images": {
    "thumbnail": { "file": "thumbnail.webp", "size": [512, 369] },
    "minimap": { "file": "images/minimap.png", "size": [256, 256], "source": "Minimap.tga" }
  },
  "data": {},
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
| `data` | Information about the map, to grow. `elements` in the elements render (see [Elements](#elements)); otherwise empty. |
| `files` | Every file in the pack (`pack.json` aside), its size and SHA-256. A viewer can cache by hash (GitHub Pages caches for only ten minutes). |

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
- **Tiles.** 512 × 512 pixels. Tile `(x, y)` at a level holds that level's pixels
  `[512x, 512x + 512) × [512y, 512y + 512)`. A tile at the right or bottom edge is padded with
  transparency to the full 512.
- **Addresses.** PMTiles' `z/x/y` is `level/x/y`, with y counting rows down from the top.
- **Encoding.** WebP, lossy at high quality (90), with transparency kept exact (WebP stores
  alpha losslessly). Each level is a filtered downscale of the full image, not of the level above.
- **Empty tiles.** A tile with nothing in it (fully transparent) isn't stored. A tile missing
  from the archive is transparent.
- **Which level to draw.** At scale `s` (window pixels per layer pixel), the level whose pixels
  are about one window pixel: `levels − 1 − floor(log2(1/s))`, clamped to the levels there are.
  For smooth zooming, draw the next coarser level under it and fade between the two.
- **PMTiles' own header.** Its minimum and maximum zoom are `0` and `levels − 1`; its bounds and
  centre (made for maps of the Earth) mean nothing here. `pack.json` has everything a viewer
  needs.

## Images

The map's own pictures from the game, converted to PNG (the thumbnail is WebP). Each is in
`images` only if the game has it for this map.

| Key | File | From the game |
|---|---|---|
| `thumbnail` | `thumbnail.webp` | none: the map layer, 512 px wide, over the dark backdrop |
| `minimap` | `images/minimap.png` | `Minimap.tga`, in the map |
| `customMinimap` | `images/custom-minimap.png` | the custom minimap `MapInfo` names (`CustomMiniMap.dds`, `.tga` on some maps), in the map: the drawn minimap the game shows |
| `customMinimapSvg` | `images/custom-minimap.svg` | none: `customMinimap` redrawn as vector shapes (below) |
| `customMinimapHover` | `images/custom-minimap-hover.png` | `CustomMiniMap_Hover.dds` (or the `.tga` `MapInfo` names), in the map |
| `replayPreview` | `images/replay-preview.png` | `ReplaysPreviewImage.tga`, in the map |
| `mapSelect` | `images/map-select.png` | the map-select picture `DocumentInfo` names (`Storm_UI_Gamemode_MapSelect_<Map>.png`), in the map |
| `loadingScreen` | `images/loading-screen.png` | the loading screen `MapInfo` names (`ui_ingame_mapmechanic_loadscreen_<map>.dds`), in the game's textures |
| `loadingScreenIcons` | `images/loading-screen-icon-<n>.png` | the icons the loading screen's layout places, in the game's textures (a list) |

Each entry is `{"file", "size", "source"}`, `source` being the game file it came from;
`loadingScreenIcons` is a list of them. The loading screen is the background picture only. The
layout's text and the icons' positions on it aren't composed into it.

### The custom minimap as SVG

`customMinimapSvg` is the custom minimap redrawn from the shapes it's made of. Its entry also has
`boundsCells`, `{left, bottom, right, top}`: the map cells it covers. Draw the SVG stretched to that
rectangle and it lies on the map layer (its viewBox is the picture's size in pixels, and it has
`preserveAspectRatio="none"`).

The rectangle is the picture at the map's scale: a whole number of pixels per cell (the picture's
width over the map's, 2 on most maps, 4 on Trial Grounds). It isn't centred on the map: it's centred
on the middle of the map's camera bounds, 2.25 cells lower (measured over the maps that have one,
against their renders). A few maps' minimaps don't follow that exactly; their corrections, set by
looking at the minimap over the render, are in `minimap-placement.json` (Volskaya Foundry's is
drawn 2 cells shorter).

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

## Elements

The elements render's map layer is the bare terrain: every structure was destroyed and its remains
cleared before the tiles were shot. Each structure and mercenary camp was shot on its own over the
sky instead, and `data.elements` lists them with their cut-outs, so a viewer can show or hide each
one, or show the map as it stood at a moment of a replay.

```json
"elements": {
  "layer": "map",
  "structures": [
    {"id": 1013, "type": "TownCannonTowerL2", "cell": [108, 60], "owner": "order", "town": 3, "core": false,
     "states": {"standing": {"file": "elements/structure-1013-standing.webp", "rect": [5120, 3410, 140, 132]},
                "rubble": {"file": "elements/structure-1013-rubble.webp", "rect": [5126, 3418, 128, 118]}}}
  ],
  "towns": [{"town": 3, "lane": 2, "owner": "order", "region": 12, "name": "Lane 2 - Order - Town 1"}],
  "camps": [
    {"camp": 2, "type": "BruiserCamp1", "cell": [95.86, 94.95],
     "states": {"spawned": {"file": "elements/camp-2-spawned.webp", "rect": [4200, 4980, 180, 330]}}}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `layer` | The map layer the cut-outs belong to (their rectangles are in its pixels). |
| `structures` | Every structure the map places: its placed unit's `id`, its unit `type`, its `cell`, its `owner` (`order`, `chaos`), the `town` it belongs to (`null`: the cores and anything outside the towns) and whether it's a `core`. |
| `towns` | The map's towns as its script numbers them: lane, owner, and the map's region for it. |
| `camps` | The mercenary camps as the script numbers them (the game's own camp numbers), their defender `type` and the `cell` they gather round. |
| `states` | Each state the element was shot in: `standing` and `rubble` for a structure (a tower's rubble as the game shows a fallen one), `spawned` for a camp's defenders. `null`: nothing is left of it in that state (a fallen moonwell). A state that couldn't be shot is missing. |
| `file`, `rect` | The state's cut-out (WebP with transparency) and where it goes on the map layer: left, top, width, height in its pixels. |
| `hiddenBy` | A standing structure's only: where a neighbouring structure stands in front of it (a wall over a tower's base, but under its orb), a mask per neighbour (`id`, and `file`: a WebP the size of the cut-out, opaque where hidden). While that neighbour is standing too, erase the masked pixels from this cut-out before drawing it. |

Draw the cut-outs over the map layer: every one shown as rubble first, then the rest (standing
structures, camps), each further north first (by `cell` y, larger first), so nearer ones overlap
further ones as in the game and rubble, which lies on the ground, never covers a standing building; where two standing structures overlap, their `hiddenBy`
masks settle which is in front, whichever is drawn first. They carry no shadows: each was shot with nothing round
it. The colours are an observer's (the left team blue).

## Raw layers

Next to the pack, `raw/` holds each layer at full resolution as PNG (lossless): `map.png` (or
`map-<arena>.png`), `background.png`, `haze.png` and `fixed.png`, and the composites
(`composite.png`, `composite-on-black.png`, `composite-with-fixed.png`). Its `layers.json`
places the layers the same way as `pack.json` does. They're for keeping and for further work, not for a viewer: the
pack's tiles come from them. The elements render's cut-outs are in `raw/elements/` as PNG.

## Hosting

- **Range requests.** A viewer reads PMTiles by range requests, which GitHub Pages serves. This
  repository publishes its packs to Pages from `packages/site` (a home page listing the maps,
  each pack at `maps/<map id>/`).
- **Size limits.** git refuses files over 100 MB, and GitHub Pages expects a site under about
  1 GB. One map's pack is expected to come to about 60–100 MB, so Pages holds a handful of maps,
  not all of them. Past that, packs can move to an S3-style bucket (Cloudflare R2, for example)
  without any change to the format.
- **Caching.** Pages sends a ten-minute cache time. A viewer that caches tiles keys them by the
  hashes in `pack.json`'s `files`.

## Later

- More map data in `data`: objectives and lanes.
