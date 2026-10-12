"""Prepares a map for capture: copies the .stormmap, injects heroes-capture's script (assembled from
the map's, the capture's and the map's category's and its own parts: map/script.py) and writes the
capture grid the capture and stitch steps follow.

    heroes-capture prepare "Towers of Doom" [options]   (heroes-capture map render runs it first)
    heroes-capture prepare "C:\\path\\to\\Some Map.stormmap" [options]

A bare name is a map as the game names it, read from the installed game (or, with no install,
from Blizzard's CDN); the tileset and light-set definitions and the sky models come from there too.
Options:

  --structures keep|hide|elements   keep or hide forts, towers, cores and gates, or
                           elements: each structure and camp shot on its own first, then the
                           tiles of the bare terrain (ELEMENTS-PLAN.md)     (default keep)
  --px-per-cell <n>        output resolution, pixels per map cell         (default 48)
  --screen <w>x<h>         the game's resolution while capturing           (default 3840x2160)
  --fov <deg>              vertical field of view; narrower is flatter      (default 20)
  --pitch <deg>            camera pitch (default 90, straight down)
  --refit-yaw <deg>        yaw of the lighting-refit look before each tile; by default it
                           faces the map's main light, found from the map's tileset and the
                           game's light sets (read from the game); only a shallow look towards
                           the light clears the dark boxes around holes
  --distance <units>       camera distance instead: the field of view is chosen to keep
                           --px-per-cell (beyond about 120 the game renders the terrain
                           in low detail: dark squares around holes, dark wedges)
  --keep <0..1>            share of each screenshot used, centred           (default 0.6)
  --no-lens                leave field of view and clip planes to the map
  --show-ui                leave the HUD up (diagnostic)
  --paint-texture <texture> <colour|clear>   paint one of the map's own sky textures a
                           solid colour, or make it transparent (sky probes); repeatable
  --keep-intro             let the intro cutscene play out instead of skipping it (diagnostic)
  --margin <cells>         capture past the camera bounds (lifts them)      (default 0)
  --crop-margin <cells>    the stitched image reaches this far past the camera bounds
                           (or past each capture area, see below)           (default 12)
  --out <dir>              working folder                                   (default tmp)
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from heroes_capture.capture import script as capture_script
from heroes_capture.capture.grid import plan_tiles
from heroes_capture.capture.sky import LIGHT_SKY, PARALLAX_KEYS, SKIES, frozen_particle_models, painted_texture_files, parallax_keys, sky_files
from heroes_capture.common import game_data, js_json
from heroes_capture.common.js_json import js_round
from heroes_capture.common.jsonio import write_manifest
from heroes_capture.common.lighting import has_sky, main_light, sky_models, tileset_of
from heroes_capture.common.runlog import log, warn
from heroes_capture.common.stormlib import Archive
from heroes_capture.map import script
from heroes_capture.map.prepare import inject, model_ids, preparation_id, read_map_info, resolve_map, team_coloured
from heroes_capture.map.specifics import Specifics

# How many values each option takes (the options without one are switches).
OPTION_VALUES = {"--structures": 1, "--px-per-cell": 1, "--screen": 1, "--fov": 1, "--pitch": 1, "--refit-yaw": 1,
                 "--distance": 1, "--keep": 1, "--no-lens": 0, "--show-ui": 0, "--paint-texture": 2, "--keep-intro": 0,
                 "--margin": 1, "--crop-margin": 1, "--out": 1}


@dataclass
class Preparation:
    """What a category's or a map's prepare hook (map/specifics.py) works with: the options, the
    map file open, and what the hooks add."""

    opts: dict
    archive: Archive
    info: dict  # read_map_info: size and camera bounds
    map_script: str  # the map's own MapScript.galaxy
    objects: str  # its Objects file
    regions: str  # its Regions file ("" without one)
    hide_doodads: list[str]  # the doodad types the capture hides (cloud layers)
    values: dict  # the script parts' values (map/script.map_values to start)
    team_coloured: list[list[float]]  # cells of what the tiles show in a team's colour (tiles.team_boxes)
    manifest: dict = field(default_factory=dict)  # fields added to the manifest
    areas: list[dict] = field(default_factory=list)  # capture areas, each rendered to its own image (none: one, the camera bounds)


FOW_OVERRIDE = """<?xml version="1.0" encoding="us-ascii"?>
<Catalog>
    <CFoW default="1">
        <Color value="0,0,0,0"/>
    </CFoW>
    <CFoW id="Dark">
        <Color value="0,0,0,0"/>
    </CFoW>
    <CFoW id="Black">
        <Hidden value="0"/>
        <Color value="0,0,0,0"/>
    </CFoW>
    <CFoW id="BlackNoUnhide">
        <Hidden value="0"/>
        <Color value="0,0,0,0"/>
        <Expand value="0"/>
    </CFoW>
</Catalog>
"""


def render_id(map_name: str, structures: str) -> str:
    """The id a preparation's files are named by: <map slug>-structures, -terrain with the
    structures hidden, or -elements."""
    return f"{game_data.slug(map_name)}-{ {'hide': 'terrain', 'elements': 'elements'}.get(structures, 'structures') }"


def parse_args(argv: list[str]) -> dict:
    opts = {
        "map": None, "structures": "keep", "pxPerCell": 48.0, "screen": {"w": 3840.0, "h": 2160.0},
        "fov": 20.0, "distance": None, "pitch": 90.0, "refitYaw": None, "keep": 0.6, "lens": True,
        "showUi": False, "paintTextures": {}, "keepIntro": False, "margin": 0.0, "cropMargin": 12.0,
        "out": "tmp",
    }
    numbers = {"--px-per-cell": "pxPerCell", "--fov": "fov", "--distance": "distance", "--pitch": "pitch",
               "--refit-yaw": "refitYaw", "--keep": "keep", "--margin": "margin", "--crop-margin": "cropMargin"}
    if {"--help", "-h"} & set(argv):
        print(__doc__.strip())
        raise SystemExit(0)
    args = iter(argv)
    for a in args:
        if a == "--structures":
            opts["structures"] = next(args)
        elif a in numbers:
            opts[numbers[a]] = float(next(args))
        elif a == "--screen":
            w, h = (float(v) for v in next(args).split("x"))
            opts["screen"] = {"w": w, "h": h}
        elif a == "--no-lens":
            opts["lens"] = False
        elif a == "--keep-intro":
            opts["keepIntro"] = True
        elif a == "--show-ui":
            opts["showUi"] = True
        elif a == "--paint-texture":
            name = next(args)
            opts["paintTextures"][name] = next(args)
        elif a == "--out":
            opts["out"] = next(args)
        elif not a.startswith("--") and not opts["map"]:
            opts["map"] = a
        else:
            raise SystemExit(f"unknown option {a}")
    if not opts["map"]:
        raise SystemExit('usage: heroes-capture prepare "<map name or .stormmap path>" [options]')
    if opts["structures"] not in ("keep", "hide", "elements"):
        raise SystemExit("--structures is keep, hide or elements")
    if not 0 < opts["keep"] <= 1:
        raise SystemExit("--keep is between 0 and 1")
    return opts


def resolve_refit_yaw(map_data: dict, light_sets: dict) -> int:
    """The yaw of the lighting-refit look: facing the map's main light (see lighting.py), or 180
    with a warning when the light can't be found."""
    light = main_light(map_data, light_sets)
    if light["yaw"] is None:
        warn(f"the map's main light wasn't found (tileset {light['tileset']}, light set {light['lighting']}); "
            "the refit look faces yaw 180. Pass --refit-yaw.")
        return 180
    log(f"main light: tileset {light['tileset']}, light set {light['lighting']}, from {light['yaw']:.0f} degrees (the refit look faces it)")
    return js_round(light["yaw"])


def plan_grid(opts: dict, bounds: dict) -> dict:
    """Camera and grid. Straight down, the camera sees a world rectangle of height
    2·distance·tan(fov/2) at its target; picking the resolution fixes that height (screen height /
    px-per-cell), and so the distance. Each screenshot contributes its centre `keep` share at most.

    Camera targets stay inside the map's camera bounds, since the game clamps any beyond them
    (which duplicated the edge columns). So the first and last columns sit on the bounds and the
    rest are spread evenly between, at most `keep` of a screen apart; the kept strips then reach
    half a spacing past the bounds, which the screenshots still cover."""
    view_h = opts["screen"]["h"] / opts["pxPerCell"]
    view_w = opts["screen"]["w"] / opts["pxPerCell"]
    if opts["distance"]:
        # The field of view that shows view_h cells from that distance.
        opts["fov"] = 2 * math.atan(view_h / 2 / opts["distance"]) * 180 / math.pi
    distance = view_h / 2 / math.tan(opts["fov"] * math.pi / 180 / 2)
    m = opts["margin"]
    area = {"left": bounds["left"] - m, "bottom": bounds["bottom"] - m, "right": bounds["right"] + m, "top": bounds["top"] + m}

    return {"distance": distance, "area": area, **plan_tiles(area, view_w, view_h, opts["keep"])}


def merge_area_grids(opts: dict, areas: list[dict]) -> dict:
    """One grid per area, rows numbered on from the last area's with a gap row between, so the
    stitch never takes the last row of one area for a neighbour of the first of the next."""
    grid = None
    row_base = 0
    for k, area in enumerate(areas):
        g = plan_grid(opts, area["bounds"])
        first = len(grid["tiles"]) if grid else 0
        for t in g["tiles"]:
            t["index"] += first
            t["row"] += row_base
            t["area"] = k
        area.update({"cols": g["cols"], "rows": g["rows"], "firstTile": first, "tileCount": len(g["tiles"])})
        row_base += g["rows"] + 1
        if grid is None:
            grid = g
        else:
            grid = {**grid, "tiles": grid["tiles"] + g["tiles"], "cols": max(grid["cols"], g["cols"]), "rows": row_base - 1,
                    "area": {"left": min(grid["area"]["left"], g["area"]["left"]),
                             "bottom": min(grid["area"]["bottom"], g["area"]["bottom"]),
                             "right": max(grid["area"]["right"], g["area"]["right"]),
                             "top": max(grid["area"]["top"], g["area"]["top"])}}
    return grid


def main(argv: list[str], specifics_for: Callable[[str, str], Specifics]) -> Path:
    """Prepare the map; returns the manifest's path. specifics_for(map name, its MapScript) gives
    what its category and the map itself add (cli.specifics_for)."""
    opts = parse_args(argv)
    out = Path(opts["out"])
    out.mkdir(parents=True, exist_ok=True)
    install = game_data.find_install()
    if install:
        log(f"game data from the install in {install}")
    with game_data.open_storage(install) as storage:
        map_name, map_bytes = resolve_map(opts["map"], storage)
        light_sets = game_data.light_sets(storage)
        models = {spec["file"]: game_data.sky_model_file(storage, spec["file"]) for spec in PARALLAX_KEYS.values()}
        id_ = render_id(map_name, opts["structures"])
        target = (out / f"{id_}.stormmap").resolve()
        target.write_bytes(map_bytes)
        # Every model the map can use: each made to freeze its particles and ribbons while its
        # animations are paused (sky.py).
        map_models, map_mods = model_ids(storage, target)

    with Archive(target) as archive:
        # (Widening the playable bounds in MapInfo, to move the game's boundary fade off the outer
        # walls, made the map unopenable: "Unable to open map".)
        read = archive.read_text
        info = read_map_info(archive.read("MapInfo"))
        map_data = {
            "t3Terrain": read("t3Terrain.xml") or "",
            "terrainData": read("Base.StormData\\GameData\\TerrainData.xml"),
            "lightData": read("Base.StormData\\GameData\\LightData.xml"),
        }
        refit_yaw = opts["refitYaw"] if opts["refitYaw"] is not None else resolve_refit_yaw(map_data, light_sets)
        # Whether the void shows the sky (the tileset has a skybox): then each tile is shot over
        # white and over black and the difference is the transparency. Otherwise the void is
        # terrain drawn black; one shot over black, and the stitch makes that black transparent.
        sky_mode = "matte" if has_sky(map_data, light_sets) else "black"
        sky_start = LIGHT_SKY if sky_mode == "matte" else "black"
        # The map's own sky models, for the sky layers' shots (command "sky mapsky" / "sky mapparallax").
        map_sky = sky_models(map_data, light_sets)
        log(f"map's own sky: fixed {map_sky['fixed'] or 'none'}, parallax {map_sky['parallax'] or 'none'}")
        log("void: sky (each tile shot over the light grey and black)" if sky_mode == "matte" else "void: black terrain (one shot over black)")
        # Cloud layers are doodads placed in the map (Battlefield of Eternity: 20
        # Storm_Doodad_Heaven_Clouds); the script hides every doodad type with "cloud" in its name.
        objects = read("Objects") or ""
        hide_doodads = list(dict.fromkeys(re.findall(r'<ObjectDoodad [^>]*Type="([^"]*[Cc]loud[^"]*)"', objects)))
        if hide_doodads:
            log(f"cloud doodads hidden: {', '.join(hide_doodads)}")
        # The doodads drawn in a team's colours: a tile with one in view is also shot from Chaos's
        # side (tiles.team_boxes), for the team-colour mask.
        coloured = team_coloured(objects)
        map_script = read("MapScript.galaxy")
        map_id = preparation_id(id_)

        # What the map's category and the map itself add (map/specifics.py).
        specifics = specifics_for(map_name, map_script)
        log(f"category: {specifics.category}")
        prep = Preparation(opts=opts, archive=archive, info=info, map_script=map_script, objects=objects, regions=read("Regions") or "",
                           hide_doodads=hide_doodads, values=script.map_values(map_id, info["width"], info["height"]),
                           team_coloured=list(coloured))
        for hook in specifics.prepare:
            hook(prep)
        log(f"team-coloured: {len(coloured)} doodads" + (f", {len(prep.team_coloured) - len(coloured)} structures" if len(prep.team_coloured) > len(coloured) else ""))
        # One grid per capture area; the camera bounds are lifted (unbound) so the camera can
        # reach every area.
        areas = prep.areas
        grid = merge_area_grids(opts, areas) if areas else plan_grid(opts, info["bounds"])
        unbound = opts["margin"] > 0 or bool(areas)
        # The map's opening timers (its own module, maps/).
        opening_timers = specifics.opening_timers
        log(f"opening timers cut short: {len(opening_timers)}" if opening_timers else "opening timers: none known for this map")
        # Far clip well past the camera.
        lens = {"fov": opts["fov"], "farClip": max(800, math.ceil(grid["distance"] * 3))} if opts["lens"] else None
        values = prep.values | capture_script.values(
            tiles=grid["tiles"], hide_structures=opts["structures"] == "hide", distance=grid["distance"],
            pitch=opts["pitch"], refit_yaw=refit_yaw, lens=lens, unbound=unbound, show_ui=opts["showUi"],
            keep_intro=opts["keepIntro"], sky_colour=sky_start, map_sky=map_sky, opening_timers=opening_timers,
            hide_doodads=hide_doodads,
        )
        parts = [script.PART, capture_script.PART, *specifics.script_parts]
        inject(archive, map_script, script.assemble([p.read_text(encoding="utf-8") for p in parts], values))
        # EXPERIMENT (run.cmd): the map's last cell row and column, and everything past them, are
        # drawn black with a one-cell fade (Punisher Arena's north orb, Battlefield of Eternity's
        # east chain), untouched by VisEnable(c_visTypeMask, false). The map's fog-of-war type
        # ("BlackNoUnhide" in MapInfo; CFoW in the game's FoWData.xml) given no colour and no hiding,
        # as the map's own data: if the black is the vision texture's border drawn in that colour,
        # it goes.
        archive.write("Base.StormData\\GameData\\FoWData.xml", FOW_OVERRIDE.encode("us-ascii"))

        # The solid-colour skyboxes (sky.py): the capture shoots each tile over white and over
        # black, and the stitch turns the difference into transparency.
        tileset = tileset_of(map_data["t3Terrain"])
        if not tileset:
            raise SystemExit("t3Terrain.xml names no tileset; cannot set the skybox")
        # Keyed copies of the map's parallax sky, when sky.py knows that model (its file from the game).
        keys = {"models": [], "files": []}
        key_spec = PARALLAX_KEYS.get(map_sky["parallax"])
        if key_spec and models.get(key_spec["file"]):
            keys = parallax_keys(map_sky["parallax"], models[key_spec["file"]])
            log(f'keyed copies of {map_sky["parallax"]}: command "sky parallaxlight", "parallaxblack", "parallaxbare", "parallaxlightbare"')
        # The models' particles and ribbons frozen while paused, in the same catalog as the skies.
        frozen = frozen_particle_models(map_models)
        log(f"particles and ribbons frozen with their animations: {len(frozen)} models (mods {', '.join(map_mods) or 'none'} and the shared data)")
        for name, data in sky_files(tileset, sky_start, read, {"models": keys["models"] + frozen, "files": keys["files"]}):
            archive.write(name, data)
        for name, data in painted_texture_files(opts["paintTextures"]):
            archive.write(name, data)
        for name, paint in opts["paintTextures"].items():
            log(f"texture {name} painted {paint}")
        log(f"skyboxes: {', '.join(SKIES)} (tileset {tileset})")

    manifest = {
        "map": map_name,
        "id": id_,
        "category": specifics.category,
        "stormmap": str(target),
        "structures": opts["structures"],
        "screen": opts["screen"],
        "pxPerCell": opts["pxPerCell"],
        "fov": opts["fov"] if opts["lens"] else None,
        "keep": opts["keep"],
        "distance": grid["distance"],
        "mapSize": {"width": info["width"], "height": info["height"]},
        "cameraBounds": info["bounds"],
        "areas": areas or None,
        "unbound": unbound,
        "cropMargin": opts["cropMargin"],
        "area": grid["area"],
        "step": grid["step"],
        "cols": grid["cols"],
        "rows": grid["rows"],
        "tiles": grid["tiles"],
        "keepIntro": opts["keepIntro"],
        "teamColoured": prep.team_coloured,
        "sky": {"mode": sky_mode, "start": sky_start, "colours": list(SKIES), "mapSky": map_sky, "keys": bool(keys["models"])},
        **prep.manifest,
        # The status strip (two columns) sits in the top-left corner. The capture measures it as
        # it comes out on the screen and adds pageLeft here: how many pixels of each screenshot's
        # left edge it blanks for it, which the stitch leaves out.
        "status": {"mapId": map_id},
    }
    manifest_path = (out / f"{id_}.json").resolve()
    write_manifest(manifest_path, manifest)

    bounds = info["bounds"]
    log(f"{map_name}: {info['width']}x{info['height']} cells, camera bounds "
        f"{{ left: {js_json.number(bounds['left'])}, bottom: {js_json.number(bounds['bottom'])}, "
        f"right: {js_json.number(bounds['right'])}, top: {js_json.number(bounds['top'])} }}")
    for a in areas:
        b = a["bounds"]
        log(f"  area {a['name']}: {a['cols']}x{a['rows']} tiles, cells {js_json.number(b['left'])}-{js_json.number(b['right'])} "
            f"x {js_json.number(b['bottom'])}-{js_json.number(b['top'])}")
    log(f"camera distance {grid['distance']:.1f}, {grid['cols']}x{grid['rows']} = {len(grid['tiles'])} tiles, "
        f"output about {js_round(grid['cols'] * grid['step']['x'] * opts['pxPerCell'])}x"
        f"{js_round(grid['rows'] * grid['step']['y'] * opts['pxPerCell'])} px")
    log(f"map      {target}")
    return manifest_path
