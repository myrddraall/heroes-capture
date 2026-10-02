# Transparent background: plan for an investigation

**Goal:** the see-through parts of a battleground (the void around and below the map) come out
transparent in the render, as a PNG with a real alpha channel, including soft edges.

Everything below comes from the game's own data: the trigger libraries and game data files,
downloaded from Blizzard's CDN (CASC, online mode) and the `.stormmap` files. Nothing here has
been tried in the game yet.

## What we found

**1. Each battleground's backdrop is set by its tileset, and a map can override it.**
Tilesets (`CTerrain` in `TerrainData.xml`) have these fields, among others:

| Field                                                                     | What it does                                                           |
| ------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `FixedSkyboxModel`, `NonFixedSkyboxModel`                                 | The skybox, and a parallax layer that moves with the camera            |
| `HideLowestLevel`                                                         | Don't draw the lowest terrain level, so the skybox shows through there |
| `FogEnabled`, `FogColor`, `FogDensity`, `FogFalloff`, `FogStartingHeight` | Distance haze                                                          |
| `FOWColor`                                                                | The colour of fog-of-war / unexplored areas                            |
| `Lighting`                                                                | The lighting set                                                       |

Tilesets with a sky use `HideLowestLevel=1` together with a skybox (for example
`StormHeavenAndHell*`: `HeavenSkybox` + `HeavenSkyboxParallax`; `Braxis`: `BraxisSkybox`).
Towers of Doom's tileset, `StormKingsCrestWinterRavenCourt`, sets neither, but has fog on.

**2. The map already overrides its tileset in its own data.** `Towers of Doom.stormmap`
contains `Base.StormData\GameData\TerrainData.xml`:

```xml
<CTerrain id="StormKingsCrestWinterRavenCourt">
    <FOWColor value="255,10,0,26"/>
    <FogColor value="255,40,88,136"/>
</CTerrain>
```

So `inject.mjs` can add fields to that file the same way it patches `MapScript.galaxy`; no
new mechanism is needed. `FOWColor` (10, 0, 26) is very close to the void colour in our renders
(about 16, 10, 32), so the void may simply be fog-of-war colour.

**3. Skyboxes are ordinary models, and simple ones use one texture.**
`HeavenSkybox` is defined as `<CModel id="HeavenSkybox" parent="Skybox">` pointing at
`Assets\Skyboxes\Storm_Doodad_Heaven_Skybox\Storm_Doodad_Heaven_Skybox.m3`, and that model
uses exactly one texture: `Assets/Textures/Storm_Heaven_Skybox_Base_Diff.dds`.
(`storm_skybox_scbraxis.m3` uses two.) 13 skybox models exist in the game data.

**4. Scripts can swap the backdrop at runtime.** Blizzard's arena maps call
`GameSetBackground(c_backgroundTerrain, "Storm_Skybox_ArenaHeaven_Parallax", 100.0)`.

## Routes, simplest first

**Route A: fog-of-war colour as a green screen (data only).** Set `FOWColor` to pure green in
the map's `TerrainData.xml`. If the void is fog-of-war colour, it turns green, and a colour key
makes it transparent. One field, no assets. Weakness: a single-colour key gives hard edges, and
green fringes where the edge is soft.

**Route B: our own skybox.** In the map's data:

- `HideLowestLevel=1` on its tileset, so the lowest level shows the sky;
- `FixedSkyboxModel` = a model of our own, defined in an injected `ModelData.xml`
  (`<CModel id="HrsSky" parent="Skybox">` pointing at the heaven skybox mesh), with
  `NonFixedSkyboxModel` cleared so no parallax layer sits on top;
- `FogEnabled=0`, so haze doesn't tint the edges (this may also help lighting consistency);
- the skybox's texture replaced by our own **solid-colour DDS**, added to the map archive at the
  same path (`Assets\Textures\Storm_Heaven_Skybox_Base_Diff.dds`). Files in a map normally take
  priority over the game's copies of the same path; that is the thing to confirm.

This gives any colour we like, not just green, and no reliance on what the void happens to be.

**Route C: difference matting, for true soft transparency (the goal).** Shoot each tile over two
backgrounds, black and white, with the camera unmoved:

- identical pixels → fully opaque map;
- pixels differing by the full black-to-white amount → fully transparent;
- anything in between → the exact partial transparency, and the true colour can be recovered
  too (`alpha = 1 − (white − black) / 255`, colour = the black shot / alpha).

That needs two skyboxes that can be swapped at runtime with `GameSetBackground`. Since the
texture override works by path, the two need _different_ meshes: for example the heaven mesh
with a black texture, and the Braxis mesh with both its textures white. The capture then takes
calibration, clean over black, clean over white; the stitch computes alpha per screenshot
before placing it, and writes a PNG with an alpha channel. It costs a third shot per tile.

## Experiments, in order

Each is a small change in `inject.mjs` plus one short run, parked on an edge tile.

1. **Is the void fog-of-war colour?** Route A: `FOWColor` = pure green. Pass: the void turns
   green. Either way, it tells us what the void is.
2. **Does the map's data override take effect?** Route B without the texture: `HideLowestLevel=1`,
   `FixedSkyboxModel` = our `HrsSky` model (the heaven mesh as it is). Pass: the heaven sky
   appears under the map. This proves that the map-data layer and a custom model id both work.
3. **Does a texture in the map replace the game's?** Add the solid-colour DDS at the heaven
   texture's path. Pass: the sky turns that colour. If not, try defining the model with a
   different mesh, or look for a model field that selects a texture.
4. **Is the colour uniform?** Check the sky isn't shaded by lighting or fog (turn fog off here)
   and that the edges blend into it rather than into haze.
5. **Runtime swap.** Two skybox models (black, white), and a `sky <model>` chat command calling
   `GameSetBackground`. Pass: the swap is immediate and complete, with the camera unmoved.
6. **Full difference-matting capture** on one map, if 5 passes.

Experiments 1–3 answer whether this is possible at all, within one or two runs.

## What stays the same

Marker calibration, the stitch's placement, and the tile pyramid are unchanged. The stitch
gains an alpha channel (it currently writes RGB) and a per-screenshot alpha step. The tile
pyramid would switch to PNG tiles, since JPEG has no transparency.
