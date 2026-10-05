# The map pack (format 1)

What heroes-capture writes for each map so that a map viewer, built in another project, can show
it: quickly at first, then sharper as the user zooms in, down to full detail. This document is the
contract between the two. The viewer builds against it, and heroes-capture writes to it. A change
a viewer would notice bumps the format number.

A pack is plain static files, made to be hosted as they are (GitHub Pages for now) and read by a
browser with no server code.

**Status:** the format as agreed. heroes-capture doesn't write packs yet. Today it writes the
images and placement files the pack is made from.

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
  raw/                        full-resolution PNGs of the layers; not part of the pack
```

The pack is written for the map with its structures (forts, towers, cores, gates) kept. A map
without the sky layers (no parallax sky in the game) has no `background`, `haze` or `fixed`.

## pack.json

The pack's only entry point. A viewer reads it first and finds everything else from it. An
example, abridged (Battlefield of Eternity; the layer values are from a real render, others
illustrative):

```json
{
  "format": 1,
  "tool": "heroes-capture 0.2.0",
  "gameBuild": "2.55.17.98025",
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
| `gameBuild` | The game build the map and images came from, when known. |
| `map.id`, `map.name` | The folder's id and the map's name as the game shows it (English). |
| `map.category` | `Battleground`, `Arena`, `Brawl` or `Other` (as `map list` shows them). |
| `map.validated` | The tool's authors have checked this map's render. Information only. |
| `map.structures` | `keep`: structures are in the map layer. |
| `map.sizeCells` | The whole map in map cells (width, height). |
| `map.cameraBounds` | Where the game lets the camera go, in map cells. A viewer can keep its camera inside. |
| `capture` | How the render was taken: map pixels per cell planned, the game's screen size, field of view in degrees. |
| `arenas` | `null`, or the arenas of a map of several (see [Several arenas](#several-arenas)). |
| `layers` | The layers, back to front: draw them in this order (see [Layers and drawing](#layers-and-drawing)). |
| `images` | The map's other pictures, each with its file and size in pixels (see [Images](#images)). Any may be missing. |
| `data` | Information about the map, to grow: objectives, camps, lanes and so on. Empty for now. |
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
| `customMinimap` | `images/custom-minimap.png` | `CustomMiniMap.dds`, in the map: the drawn minimap the game shows |
| `customMinimapHover` | `images/custom-minimap-hover.png` | `CustomMiniMap_Hover.dds`, in the map |
| `replayPreview` | `images/replay-preview.png` | `ReplaysPreviewImage.tga`, in the map |
| `mapSelect` | `images/map-select.png` | the map-select picture `DocumentInfo` names (`Storm_UI_Gamemode_MapSelect_<Map>.png`), in the map |
| `loadingScreen` | `images/loading-screen.png` | the loading screen `MapInfo` names (`ui_ingame_mapmechanic_loadscreen_<map>.dds`), in the game's textures |
| `loadingScreenIcons` | `images/loading-screen-icon-<n>.png` | the icons the loading screen's layout places, in the game's textures (a list) |

Each entry is `{"file", "size", "source"}`, `source` being the game file it came from;
`loadingScreenIcons` is a list of them. The loading screen is the background picture only. The
layout's text and the icons' positions on it aren't composed into it.

## Raw layers

Next to the pack, `raw/` holds each layer at full resolution as PNG (lossless): `map.png` (or
`map-<arena>.png`), `background.png`, `haze.png` and `fixed.png`. Its `layers.json` places them
the same way as `pack.json` does. They're for keeping and for further work, not for a viewer: the
pack's tiles come from them.

## Hosting

- **Range requests.** A viewer reads PMTiles by range requests, which GitHub Pages serves.
- **Size limits.** git refuses files over 100 MB, and GitHub Pages expects a site under about
  1 GB. One map's pack is expected to come to about 60–100 MB, so Pages holds a handful of maps,
  not all of them. Past that, packs can move to an S3-style bucket (Cloudflare R2, for example)
  without any change to the format.
- **Caching.** Pages sends a ten-minute cache time. A viewer that caches tiles keys them by the
  hashes in `pack.json`'s `files`.

## Later

- The minimap as SVG (the custom minimap traced into vector shapes), as `images.customMinimapSvg`.
- Map data in `data`: objectives, camps, structures and lanes, from the map's placed units.
- The map without its structures, as a second map layer a viewer can switch to.
