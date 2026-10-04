"""Prepares a battleground for capture: copies the .stormmap, injects the capture script
(capture_script.galaxy) and writes the capture grid the capture and stitch steps follow.

    heroes-capture prepare "Towers of Doom" [options]   (heroes-capture map render runs it first)
    heroes-capture prepare "C:\\path\\to\\Some Map.stormmap" [options]

A bare name is a map as the game names it, read from the installed game (or, with no install,
from Blizzard's CDN); the tileset and light-set definitions and the sky models come from there too.
Options:

  --structures keep|hide   keep or hide forts, towers, cores and gates      (default keep)
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
                           (or past each arena area, see below)             (default 12)
  --out <dir>              working folder                                   (default tmp)
"""

import json
import math
import re
import struct
import time
from pathlib import Path

from . import game_data
from . import js_json
from .capture_script import STATUS_CELL_H, STATUS_CELL_W, STATUS_CELLS, STATUS_ROWS, capture_script
from .light_data import has_sky, main_light, sky_models, tileset_of
from .sky import PARALLAX_KEYS, SKIES, painted_texture_files, parallax_keys, sky_files, solid_dds
from .runlog import log, warn
from .stormlib import Archive

HERE = Path(__file__).resolve().parent




# How many values each option takes (the options without one are switches).
OPTION_VALUES = {"--structures": 1, "--px-per-cell": 1, "--screen": 1, "--fov": 1, "--pitch": 1, "--refit-yaw": 1,
                 "--distance": 1, "--keep": 1, "--no-lens": 0, "--show-ui": 0, "--paint-texture": 2, "--keep-intro": 0,
                 "--margin": 1, "--crop-margin": 1, "--out": 1}


def render_id(map_name: str, structures: str) -> str:
    """The id a preparation's files are named by: <map slug>-structures, or -terrain with the
    structures hidden."""
    return f"{slug(map_name)}-{'terrain' if structures == 'hide' else 'structures'}"


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
    if opts["structures"] not in ("keep", "hide"):
        raise SystemExit("--structures is keep or hide")
    if not 0 < opts["keep"] <= 1:
        raise SystemExit("--keep is between 0 and 1")
    return opts


def slug(s: str) -> str:
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", s.lower()))


def resolve_map(map_: str, storage) -> tuple[str, bytes]:
    """The map's name and its .stormmap: a path to one, or a map the game has, by name."""
    if map_.lower().endswith(".stormmap"):
        path = Path(map_)
        if not path.exists():
            raise SystemExit(f"no such file: {map_}")
        return path.name[: -len(".stormmap")], path.read_bytes()
    return game_data.map_file(storage, map_)


def read_table(name: str) -> dict:
    """A JSON table shipped next to this script."""
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def js_round(value: float) -> int:
    """Math.round: halves go up."""
    return math.floor(value + 0.5)


def resolve_refit_yaw(map_data: dict, light_sets: dict) -> int:
    """The yaw of the lighting-refit look: facing the map's main light (see light_data.py), or
    180 with a warning when the light can't be found."""
    light = main_light(map_data, light_sets)
    if light["yaw"] is None:
        warn(f"the map's main light wasn't found (tileset {light['tileset']}, light set {light['lighting']}); "
            "the refit look faces yaw 180. Pass --refit-yaw.")
        return 180
    log(f"main light: tileset {light['tileset']}, light set {light['lighting']}, from {light['yaw']:.0f} degrees (the refit look faces it)")
    return js_round(light["yaw"])


def read_map_info(buf: bytes) -> dict:
    """MapInfo: map size at bytes 16 and 20, and the camera bounds (left, bottom, right, top) as
    four uint32 right after a string. The bounds' offset moves with the strings before them, so
    take the first aligned-to-a-string quadruple that fits the map; this matches all 35 current
    battlegrounds and brawls."""
    w, h = struct.unpack_from("<II", buf, 16)
    for o in range(32, len(buf) - 15):
        if buf[o - 1] != 0:
            continue
        l, b, r, t = struct.unpack_from("<IIII", buf, o)
        if l < r <= w and b < t <= h and r - l >= w / 3 and t - b >= h / 3:
            return {"width": w, "height": h, "bounds": {"left": l, "bottom": b, "right": r, "top": t}, "boundsOffset": o}
    return {"width": w, "height": h, "bounds": {"left": 0, "bottom": 0, "right": w, "top": h}, "boundsOffset": None}


def parse_areas(regions_xml: str) -> list[dict]:
    """A map that is several arenas in one (Punisher Arena: one arena per round, stacked on the
    map, the camera bounds moved to the round's arena at run time) marks each arena with a region
    named "..._MapBounds" in its Regions file. Two or more of them: capture areas, each rendered to
    its own image. `<quad value="left,bottom,right,top"/>`."""
    areas = []
    for match in re.finditer(r"<region\b[\s\S]*?</region>", regions_xml):
        name = re.search(r'<name value="([^"]*)"', match.group(0))
        quad = re.search(r'<shape type="rect">[\s\S]*?<quad value="([^"]*)"', match.group(0))
        if not name or not quad or not re.search(r"MapBounds$", name.group(1), re.I):
            continue
        try:
            left, bottom, right, top = (float(v) for v in quad.group(1).split(","))
        except ValueError:
            continue
        if left >= right or bottom >= top:
            continue
        areas.append({"name": re.sub(r"_?MapBounds$", "", name.group(1), flags=re.I),
                      "bounds": {"left": left, "bottom": bottom, "right": right, "top": top}})
    areas.sort(key=lambda a: (a["name"].lower(), a["name"]))
    return areas if len(areas) >= 2 else []


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

    def spread(span: float, max_step: float) -> tuple[int, float]:
        count = max(1, math.ceil(span / max_step) + 1)
        return count, (span / (count - 1) if count > 1 else max_step)

    cols, step_x = spread(area["right"] - area["left"], view_w * opts["keep"])
    rows, step_y = spread(area["top"] - area["bottom"], view_h * opts["keep"])
    tiles = [{"index": row * cols + col, "row": row, "col": col,
              "x": area["left"] + col * step_x, "y": area["top"] - row * step_y}  # row 0 is the top (north) edge
             for row in range(rows) for col in range(cols)]
    return {"distance": distance, "step": {"x": step_x, "y": step_y}, "cols": cols, "rows": rows, "tiles": tiles, "area": area}


def preparation_id(id_: str) -> int:
    """The map's identity in the status strip: a 16-bit hash of this preparation (map, structures
    and time), so the capture can tell its own map from one left running by an earlier run."""
    h = 2166136261
    for ch in f"{id_} {int(time.time() * 1000)}":
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return (h ^ (h >> 16)) & 0xFFFF


def check_definition_order(script: str) -> None:
    """Galaxy is single-pass: a function must be defined before any call to it, and the game gives
    no error when it isn't; the whole map script silently fails and the map runs with no triggers
    at all (which shows as every interface panel visible at once). Refuse to emit a script where
    any hrsCap_ function is called before its definition."""
    defined = {m.group(1): m.start() for m in re.finditer(r"^(?:void|bool|int|fixed|string|text) (hrsCap_\w+) \(", script, re.M)}
    for m in re.finditer(r"\b(hrsCap_[A-Za-z]\w*)\s*\(", script):
        at = defined.get(m.group(1))
        if at is not None and m.start() < at:  # (None: a variable, or a Blizzard function)
            line = script[: m.start()].count("\n") + 1
            raise SystemExit(f"capture script: {m.group(1)} is used at line {line} before it is defined")


def merge_area_grids(opts: dict, info: dict, areas: list[dict]) -> dict:
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


def main(argv: list[str]) -> Path:
    """Prepare the map; returns the manifest's path."""
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
    source = {"name": map_name}
    id_ = render_id(source["name"], opts["structures"])
    target = (out / f"{id_}.stormmap").resolve()
    target.write_bytes(map_bytes)

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
        sky_start = "white" if sky_mode == "matte" else "black"
        # The map's own sky models, for probes that show them (command "sky mapsky" / "sky mapparallax").
        map_sky = sky_models(map_data, light_sets)
        log(f"map's own sky: fixed {map_sky['fixed'] or 'none'}, parallax {map_sky['parallax'] or 'none'}")
        log("void: sky (each tile shot over white and black)" if sky_mode == "matte" else "void: black terrain (one shot over black)")
        # A map of several arenas: its Regions file (none: one area, the camera bounds). One grid
        # per area; the camera bounds are lifted (unbound) so the camera can reach every area.
        regions = read("Regions")
        areas = parse_areas(regions) if regions else []
        grid = merge_area_grids(opts, info, areas) if areas else plan_grid(opts, info["bounds"])
        unbound = opts["margin"] > 0 or bool(areas)
        # Far clip well past the camera.
        lens = {"fov": opts["fov"], "farClip": max(800, math.ceil(grid["distance"] * 3))} if opts["lens"] else None
        # Cloud layers are doodads placed in the map (Battlefield of Eternity: 20
        # Storm_Doodad_Heaven_Clouds); the script hides every doodad type with "cloud" in its name.
        objects = read("Objects") or ""
        hide_doodads = list(dict.fromkeys(re.findall(r'<ObjectDoodad [^>]*Type="([^"]*[Cc]loud[^"]*)"', objects)))
        if hide_doodads:
            log(f"cloud doodads hidden: {', '.join(hide_doodads)}")

        map_id = preparation_id(id_)

        original = read("MapScript.galaxy")
        # The map's opening timers (opening-timers.json), by the libraries its script includes.
        timer_table = read_table("opening-timers.json")
        includes = [m.split("/")[-1] for m in re.findall(r'^include "([^"]+)"', original, re.M)]
        opening_timers = [t for lib in includes for t in timer_table.get(lib, [])]
        log(f"opening timers cut short: {len(opening_timers)} ({', '.join(l for l in includes if l in timer_table)})"
            if opening_timers else "opening timers: none known for this map")
        # A map of rounds (arena mode): "quit" makes the current round the last.
        arena = "LibAREN" in includes
        if arena:
            log('rounds (LibAREN): "quit" ends the match after this round')
        init = original.rfind("void InitMap () {")
        if init < 0:
            raise SystemExit("MapScript.galaxy has no InitMap; not a battleground script?")
        eol = "\r\n" if "\r\n" in original else "\n"
        close = original.find(f"{eol}}}", init)
        script = capture_script(
            tiles=grid["tiles"], hide_structures=opts["structures"] == "hide", distance=grid["distance"],
            pitch=opts["pitch"], refit_yaw=refit_yaw, lens=lens, unbound=unbound, show_ui=opts["showUi"],
            keep_intro=opts["keepIntro"], sky_colour=sky_start, map_sky=map_sky, map_width=info["width"],
            map_height=info["height"], opening_timers=opening_timers, map_id=map_id, hide_doodads=hide_doodads,
            arena=arena,
        ).replace("\n", eol)
        check_definition_order(script)
        # Galaxy is single-pass: the capture functions go before InitMap, the call at its end.
        patched = original[:init] + script + original[init:close] + f"{eol}    hrsCap_Init();" + original[close:]
        archive.write("MapScript.galaxy", patched.encode("utf-8"))

        # The status strip's cells are a white texture tinted per cell (capture_script.galaxy).
        archive.write("Assets\\Textures\\HrsWhite.dds", solid_dds((255, 255, 255), 64, 64))

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
            log(f'keyed copies of {map_sky["parallax"]}: command "sky parallaxwhite", "parallaxblack", "parallaxbare", "parallaxwhitebare"')
        for name, data in sky_files(tileset, sky_start, read, keys):
            archive.write(name, data)
        for name, data in painted_texture_files(opts["paintTextures"]):
            archive.write(name, data)
        for name, paint in opts["paintTextures"].items():
            log(f"texture {name} painted {paint}")
        log(f"skyboxes: {', '.join(SKIES)} (tileset {tileset})")

    manifest = {
        "map": source["name"],
        "id": id_,
        "stormmap": str(target),
        "structures": opts["structures"],
        "screen": opts["screen"],
        "pxPerCell": opts["pxPerCell"],
        "fov": opts["fov"] if opts["lens"] else None,
        "keep": opts["keep"],
        "distance": grid["distance"],
        "pitch": opts["pitch"],
        "refitYaw": refit_yaw,
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
        "sky": {"mode": sky_mode, "start": sky_start, "colours": list(SKIES), "mapSky": map_sky, "keys": bool(keys["models"])},
        "hideDoodads": hide_doodads,
        # The status strip (two columns) sits in the top-left corner; this many pixels of each
        # screenshot's left edge are blanked by the capture and left out by the stitch.
        "status": {"cells": STATUS_CELLS, "rows": STATUS_ROWS, "cellUnits": [STATUS_CELL_W, STATUS_CELL_H], "pageLeft": 64, "mapId": map_id},
    }
    manifest_path = (out / f"{id_}.json").resolve()
    manifest_path.write_text(js_json.dumps(manifest, indent=2), encoding="utf-8")

    bounds = info["bounds"]
    log(f"{source['name']}: {info['width']}x{info['height']} cells, camera bounds "
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
