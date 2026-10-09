"""Map elements (ELEMENTS-PLAN.md): what a render cuts out of a map, read from its files when it is
prepared (its structures, towns and camps: element_list), an element's cut-out from its shots alone
over the sky (cut_out), and the holes the cores stand on (filled when a map is prepared, opened
again by the capture script as it starts). The elements probe (probes.py, --probe-elements) uses
the rest: its targets, and the pixels two shots of the same view differ in."""

import math
import re
import struct

import numpy as np
from scipy import ndimage

from .stormlib import Archive

# The Order team's computer player (libCore_gv_cOMPUTER_TeamOrder), who owns its structures, and
# its number in libGame_gv_teams.
ORDER_PLAYER, ORDER_TEAM = 11, 1
# A town's structures: those within this many cells of its town hall (its walls reach 13).
TOWN_RADIUS = 14
# Pixels that differ by more than this (the largest of the three channels) count as changed.
ELEMENT_THRESHOLD = 24
# An element's cut-out from its shots alone over the sky (cut_out), opacity out of 255. At or below
# VEIL: transparent (the white sky isn't quite even: 99.9% of the pixels that are sky in both shots
# came out at 7 or less, the elements had almost none that low). At or above SOLID: opaque (the
# white sky brightens solid parts by up to about 14-19 levels of its 230, putting them at 236 and
# up; soft edges, glass and glows lie between). Measured on Battlefield of Eternity's tower, camp
# and Immortal (the elements probe).
VEIL_ALPHA, SOLID_ALPHA = 8, 235
# A pixel of an element's shot within this much (the largest of the three channels) of the same
# pixel in its tile's shot with everything hidden is something that can't be hidden (a cliff
# doodad), not the element: two shots over the sky differ by almost nothing (the probe: at most
# 0.006% of pixels between two in a row).
EMPTY_MATCH = 10


# t3CellFlags (the map's terrain cells, a byte each): the cell is a hole, not drawn (the sky shows
# through). Battlefield of Eternity's cores each stand on a small hole their pedestal covers.
CELL_HOLE = 4


def parse_objects(objects: str) -> tuple[list[dict], list[dict]]:
    """A map's placed units and doodads (its Objects file): id, type, cell, owner."""

    def parse(tag: str, type_key: str) -> list[dict]:
        found = []
        for m in re.finditer(rf"<{tag} [^>]*>", objects):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(0)))
            x, y, *_ = (float(v) for v in attrs["Position"].split(","))
            found.append({"id": int(attrs.get("Id", "0") or 0), "type": attrs.get(type_key, ""), "x": x, "y": y,
                          "player": int(attrs.get("Player", attrs.get("TeamColor", "0")) or 0)})
        return found

    return parse("ObjectUnit", "UnitType"), parse("ObjectDoodad", "Type")


def placed(manifest: dict) -> tuple[list[dict], list[dict]]:
    """The prepared map's placed units and doodads."""
    with Archive(manifest["stormmap"]) as archive:
        return parse_objects(archive.read_text("Objects") or "")


def is_structure(unit_type: str) -> bool:
    """A town's structures (Town...) and the cores (...Core)."""
    return unit_type.startswith("Town") or unit_type.endswith("Core")


def fill_structure_holes(cell_flags: bytes, objects: str) -> tuple[bytes, list[dict]]:
    """t3CellFlags with the holes structures stand on filled: each hole (cells joined edge to edge)
    that a placed structure's cell lies in loses its hole flag, so the terrain is drawn there once
    the structure is gone (a core's remains cleared leave its hole open to the sky). The file: a
    32-byte header (width and height at bytes 24 and 28), then a byte per cell, row by row from the
    map's bottom. Returns the file and the holes filled ({type, x, y, cells: [(x, y), ...]})."""
    width, height = struct.unpack_from("<II", cell_flags, 24)
    cells = np.frombuffer(cell_flags, np.uint8, width * height, 32).reshape(height, width).copy()
    holes, count = ndimage.label(cells & CELL_HOLE)
    filled = []
    for u in parse_objects(objects)[0]:
        x, y = int(u["x"]), int(u["y"])
        if not is_structure(u["type"]) or not (0 <= x < width and 0 <= y < height) or not holes[y, x]:
            continue
        hole = holes == holes[y, x]
        ys, xs = np.nonzero(hole)
        filled.append({"type": u["type"], "x": u["x"], "y": u["y"], "cells": [(int(cx), int(cy)) for cx, cy in zip(xs, ys)]})
        cells[hole] &= ~CELL_HOLE & 0xFF
        holes[hole] = 0
    return cell_flags[:32] + cells.tobytes() + cell_flags[32 + width * height :], filled


# The map's computer players, who own its structures (libCore_gv_cOMPUTER_TeamOrder / TeamChaos).
OWNERS = {11: "order", 12: "chaos"}
# How far round an element its cut-out may reach, in cells: it is cropped to that circle (and
# rubble, shot all at once, to the part nearer its own structure than any other). A structure: its
# own size and its glow, generous (the walls are long). A camp: its spawn points' spread and this
# much more.
STRUCTURE_RADIUS = 8.0
CAMP_MARGIN = 3.0


def parse_points(objects: str) -> dict[int, tuple[float, float]]:
    """A map's placed points (Objects' ObjectPoint), by id: the cells scripts name with
    PointFromId."""
    points = {}
    for m in re.finditer(r"<ObjectPoint [^>]*>", objects):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(0)))
        x, y, *_ = (float(v) for v in attrs["Position"].split(","))
        points[int(attrs["Id"])] = (x, y)
    return points


def parse_regions(regions: str) -> dict[int, dict]:
    """A map's regions (its Regions file), by id: name and shapes, each a rect (left, bottom, right,
    top), circle (centre, radius) or diamond (centre, width, height), and whether it cuts out."""
    found = {}
    for m in re.finditer(r'<region id="(\d+)">([\s\S]*?)</region>', regions):
        body = m.group(2)
        name = re.search(r'<name value="([^"]*)"', body)
        shapes = []
        for sm in re.finditer(r'<shape type="(\w+)">([\s\S]*?)</shape>', body):
            kind, sb = sm.group(1), sm.group(2)

            def value(key: str) -> list[float]:
                v = re.search(rf'<{key} value="([^"]*)"', sb)
                return [float(n) for n in v.group(1).split(",")] if v else []

            shape = {"kind": kind, "negative": "<negative/>" in sb}
            if kind == "rect":
                shape["quad"] = value("quad")
            elif kind in ("circle", "diamond"):
                shape["center"] = value("center")
                shape.update({"radius": (value("radius") or [0])[0]} if kind == "circle"
                             else {"width": (value("width") or [0])[0], "height": (value("height") or [0])[0]})
            else:
                continue
            shapes.append(shape)
        found[int(m.group(1))] = {"name": name.group(1) if name else "", "shapes": shapes}
    return found


def region_contains(region: dict, x: float, y: float) -> bool:
    """Whether a cell lies in a region: its shapes in order, each adding to it or cutting out of it."""
    inside = False
    for s in region["shapes"]:
        if s["kind"] == "rect":
            left, bottom, right, top = s["quad"]
            hit = left <= x <= right and bottom <= y <= top
        elif s["kind"] == "circle":
            hit = math.dist((x, y), s["center"]) <= s["radius"]
        else:
            cx, cy = s["center"]
            hit = abs(x - cx) / max(s["width"] / 2, 1e-9) + abs(y - cy) / max(s["height"] / 2, 1e-9) <= 1
        if hit:
            inside = not s["negative"]
    return inside


def parse_towns(script: str, regions: dict[int, dict]) -> list[dict]:
    """The map's towns, as its script hooks them up for GameLib (libGame_gv_townTownData, numbered
    in that order: each "lv_town += 1"): lane (the lv_lane last set), owner and region."""
    hookup = re.search(r"bool gt_HookupTownData_Func[\s\S]*?\n}", script)
    towns, lane = [], None
    if not hookup:
        return towns
    events = r"\blv_lane = (\d+);|\b(lv_town \+= 1);|\.lv_owner = libCore_gv_cOMPUTER_Team(\w+);|\.lv_townRegion = RegionFromId\((\d+)\);"
    for m in re.finditer(events, hookup.group(0)):
        if m.group(1):
            lane = int(m.group(1))
        elif m.group(2):
            towns.append({"town": len(towns) + 1, "lane": lane, "owner": None, "region": None, "name": ""})
        elif towns and m.group(3):
            towns[-1]["owner"] = m.group(3).lower()
        elif towns and m.group(4):
            towns[-1]["region"] = int(m.group(4))
            towns[-1]["name"] = regions.get(int(m.group(4)), {}).get("name", "")
    return [t for t in towns if t["region"] is not None]


def parse_camps(script: str, points: dict[int, tuple[float, float]]) -> list[dict]:
    """The map's mercenary camps, as its script hooks them up for the jungle library
    (libMapM_gv_jungleCreepCamps, numbered in that order, each "lv_junglecamp += 1":
    libMapM_gf_JungleRespawnCreepsForCamp's numbers): defender type, its captain's and defenders'
    spawn points (Objects' points), their middle, and how far they spread from it."""
    hookup = re.search(r"bool gt_HookupJungleCreepData_Func[\s\S]*?\n}", script)
    camps = []
    if not hookup:
        return camps
    for n, block in enumerate(re.split(r"\blv_junglecamp \+= 1;", hookup.group(0))[1:], start=1):
        kind = re.search(r"lv_mapDataCampDefenderType = libMapM_ge_JungleCampDefenderTypes_(\w+);", block)
        ids = re.findall(r"lv_mapData(?:CampCaptainSpawnPoint|DefenderSpawnPoints\[\d+\]) = PointFromId\((\d+)\);", block)
        spots = [points[int(i)] for i in ids if int(i) in points]
        if not spots:
            continue
        x, y = sum(p[0] for p in spots) / len(spots), sum(p[1] for p in spots) / len(spots)
        camps.append({"camp": n, "type": kind.group(1) if kind else None, "x": round(x, 3), "y": round(y, 3),
                      "spawns": [list(p) for p in spots], "spread": round(max(math.dist((x, y), p) for p in spots), 3)})
    return camps


def element_list(script: str, objects: str, regions_xml: str) -> dict:
    """What a render cuts out of a map (ELEMENTS-PLAN.md), read from its files when it is prepared:
    its structures (Objects' placed units) with their towns (the region each stands in), its camps,
    and for each element the radius of the circle that isolates it. Which tile each is shot from is
    the capture's to pick (source_tile): the grid can be planned again once the map is running."""
    regions = parse_regions(regions_xml)
    towns = parse_towns(script, regions)
    structures = []
    for u in parse_objects(objects)[0]:
        if not is_structure(u["type"]):
            continue
        town = next((t["town"] for t in towns if region_contains(regions.get(t["region"], {"shapes": []}), u["x"], u["y"])), None)
        structures.append({"id": u["id"], "type": u["type"], "x": u["x"], "y": u["y"], "owner": OWNERS.get(u["player"]),
                           "town": town, "core": u["type"].endswith("Core"), "radius": STRUCTURE_RADIUS})
    camps = parse_camps(script, parse_points(objects))
    for c in camps:
        c["radius"] = round(c["spread"] + CAMP_MARGIN, 3)
    return {"structures": structures, "towns": towns, "camps": camps}


# Structures never copied (they fall themselves, in waves): the cores (a copy lacks the statue and
# shield crystals the map's script gives the real one) and keeps (a keep's copy crashed the game in
# every launch of a Battlefield of Eternity render; the forts' were fine).
COPY_EXCLUDED = ("TownTownHallL3",)
# How far the lighting map may differ between a structure's cell and its copy's spot (0..255 per
# channel, averaged over LIGHT_RADIUS cells round each): on Battlefield of Eternity a copy on the
# Hell side came out orange.
LIGHT_MATCH = 10.0
LIGHT_RADIUS = 4.0


def lighting_map(tga: bytes | None) -> np.ndarray | None:
    """A map's LightingMap.tga (each channel one light set's share, a few pixels per cell) as RGBA,
    row 0 at the map's bottom edge (its rows run up the map, as its Heaven/Hell boundary against
    the rendered Battlefield of Eternity shows); None without one (one lighting everywhere)."""
    if not tga:
        return None
    from io import BytesIO

    from PIL import Image

    return np.asarray(Image.open(BytesIO(tga)).convert("RGBA"), dtype=np.float32)


def lighting_at(light: np.ndarray, map_width: int, x: float, y: float) -> np.ndarray:
    """The lighting map's mean within LIGHT_RADIUS cells of a cell."""
    scale = light.shape[1] / map_width
    r = max(1, int(LIGHT_RADIUS * scale))
    cx, cy = int(x * scale), int(y * scale)
    patch = light[max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1]
    return patch.reshape(-1, 4).mean(axis=0) if patch.size else np.zeros(4, np.float32)


def copy_spots(structures: list[dict], camps: list[dict], bounds: dict, map_width: int, light: np.ndarray | None) -> dict:
    """A spare spot for each structure's copy (the elements capture's rubble, prepared while the
    structures stand: element_capture.py), by id: inside the bounds, its circle clear of every
    structure's and camp's and every other spot's, under the same lighting as the structure's
    cell, on the same part of a cell (a wall on a half cell: a copy is placed as its footprint
    fits), nearest the structure first. The cores and COPY_EXCLUDED get none, nor any the map has
    no room for: they fall themselves."""
    margin = int(STRUCTURE_RADIUS)
    taken = [(u["x"], u["y"], u["radius"]) for u in structures] + [(c["x"], c["y"], c["radius"]) for c in camps]
    grid = [(x, y) for x in range(int(bounds["left"]) + margin, int(bounds["right"]) - margin + 1, 2)
            for y in range(int(bounds["bottom"]) + margin, int(bounds["top"]) - margin + 1, 2)]
    smallest = min((r for _, r in RUBBLE_RADII), default=STRUCTURE_RADIUS)
    free = [p for p in grid if all(math.dist(p, (x, y)) >= r + smallest for x, y, r in taken)]
    light_of = (lambda x, y: lighting_at(light, map_width, x, y)) if light is not None else None
    wanted = [u for u in structures if not u["core"] and u["type"] not in COPY_EXCLUDED]
    matching = {}
    for u in wanted:
        here = light_of(u["x"], u["y"]) if light_of else None
        matching[u["id"]] = [p for p in free if here is None or float(np.abs(light_of(*p) - here).max()) <= LIGHT_MATCH]
    spots: dict = {}
    used: list[tuple[float, float, float]] = []
    # The structures with the fewest spots to choose from first.
    for u in sorted(wanted, key=lambda u: len(matching[u["id"]])):
        fx, fy, rubble = u["x"] % 1, u["y"] % 1, rubble_radius(u)
        options = [(p[0] + fx, p[1] + fy) for p in matching[u["id"]]]
        options = [p for p in options if all(math.dist(p, (x, y)) >= r + rubble for x, y, r in taken)
                   and all(math.dist(p, (x, y)) >= r + rubble for x, y, r in used)]
        if options:
            p = min(options, key=lambda p: math.dist(p, (u["x"], u["y"])))
            used.append((p[0], p[1], rubble))
            spots[u["id"]] = {"x": p[0], "y": p[1]}
    return spots


# Cells a structure's rubble reaches when it is shot (element_capture.FALL_WAITS after its fall),
# for its own circle (a standing structure's is STRUCTURE_RADIUS): the spread probe on Battlefield
# of Eternity, each brought down alone, the furthest of its debris: a gate's 8.2, a tower's 3.0, a
# wall's 5.4, a moonwell's 5.0, a keep's 8.5, a fort's 3.9 one time and about 8 another (its
# chunks thrown far), a core's 17.6 (its smoke and the pieces flung furthest); with a margin. In
# renders more reached their circles' edges: a level 3 tower's chunks 6 cells, a fort's 12, a
# core's 18; and, brought down in view (its explosion playing in full), a level 2 tower's 6.
# The first prefix that matches; none: STRUCTURE_RADIUS.
RUBBLE_RADII = (("TownGate", 9.0), ("TownCannonTower", 9.0), ("TownWall", 6.0), ("TownMoonwell", 6.0), ("TownTownHall", 14.0))
CORE_RUBBLE_RADIUS = 22.0


def rubble_radius(structure: dict) -> float:
    """The circle a structure's rubble is cut out by (RUBBLE_RADII)."""
    if structure.get("core"):
        return CORE_RUBBLE_RADIUS
    return next((r for prefix, r in RUBBLE_RADII if structure["type"].startswith(prefix)), structure["radius"])


def rubble_waves(structures: list[dict], radius=lambda u: u["radius"]) -> list[list[dict]]:
    """The structures in waves to bring down together, so that each one's rubble can be shot alone:
    no two in a wave nearer each other than both their circles reach (their rubble would be in
    each other's shots), and each core in a wave of its own, last (brought down together, the
    second core's fall came out unlike one alone: no smoke, its pool still red, few chunks). Each structure goes into the first wave it fits, the biggest circles first (`radius`:
    each one's circle; a structure's rubble's, rubble_radius). Camps (no "core") the same: those in
    a wave spawned together."""
    waves: list[list[dict]] = []
    cores = [u for u in structures if u.get("core")]
    for u in sorted((u for u in structures if not u.get("core")), key=lambda u: -radius(u)):
        for wave in waves:
            if all(math.dist((u["x"], u["y"]), (o["x"], o["y"])) >= radius(u) + radius(o) for o in wave):
                wave.append(u)
                break
        else:
            waves.append([u])
    return waves + [[c] for c in cores]


def view_groups(items: list, half_w: float, half_h: float, at=lambda item: (item["x"], item["y"])) -> list[list]:
    """Items in groups that each fit a box of half_w by half_h cells either side of its middle
    (view_middle): the elements capture brings each group down with the camera over it, as a fall
    gives off its particles only near the camera. From the left: each group the first item left
    and every other within the box's width to its right and its height round it."""
    left = sorted(items, key=lambda item: at(item))
    groups = []
    while left:
        x0, y0 = at(left[0])
        group = [item for item in left if x0 <= at(item)[0] <= x0 + 2 * half_w and abs(at(item)[1] - y0) <= half_h]
        groups.append(group)
        left = [item for item in left if not any(item is g for g in group)]
    return groups


def view_middle(group: list, at=lambda item: (item["x"], item["y"])) -> tuple[float, float]:
    """The middle of a group's box (view_groups)."""
    xs, ys = [at(item)[0] for item in group], [at(item)[1] for item in group]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def source_tile(element: dict, tiles: list[dict]) -> dict:
    """The grid tile an element is shot from: the one whose centre is nearest it (the least
    perspective lean), so its cut-out drops into the map where that tile's pixels are used."""
    return min(tiles, key=lambda t: math.dist((t["x"], t["y"]), (element["x"], element["y"])))


def element_targets(manifest: dict) -> dict:
    """What the elements probe shoots, read from the map: the Order team's forward town (its town
    hall furthest from its core, and the structures within TOWN_RADIUS of it), the mercenary camp
    nearest that town, the Order core, and the middle of the map (where the objective spawns)."""
    units, doodads = placed(manifest)
    order = [u for u in units if u["player"] == ORDER_PLAYER]
    core = next(u for u in order if u["type"].endswith("Core"))
    hall = max((u for u in order if u["type"].startswith("TownTownHall")), key=lambda u: math.dist((u["x"], u["y"]), (core["x"], core["y"])))
    town = sorted((u for u in order if u["type"].startswith("Town") and math.dist((u["x"], u["y"]), (hall["x"], hall["y"])) <= TOWN_RADIUS),
                  key=lambda u: math.dist((u["x"], u["y"]), (hall["x"], hall["y"])))
    camps = [d for d in doodads if "MercCamp" in d["type"]]
    camp = min(camps, key=lambda d: math.dist((d["x"], d["y"]), (hall["x"], hall["y"]))) if camps else None
    area = manifest["area"]
    middle = {"x": round((area["left"] + area["right"]) / 2, 1), "y": round((area["bottom"] + area["top"]) / 2, 1)}
    return {"town": town, "camp": camp, "core": core, "middle": middle}


def changed(a: np.ndarray, b: np.ndarray, left: int) -> np.ndarray:
    """Where two shots of the same view differ (the strip's column left out: never changed)."""
    mask = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > ELEMENT_THRESHOLD
    mask[:, :left] = False
    return mask


def describe(mask: np.ndarray) -> str:
    """A difference in numbers: the share of pixels changed, and the box round them."""
    if not mask.any():
        return "no pixels changed"
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    box = (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)
    fill = float(mask[box[1] : box[3], box[0] : box[2]].mean())
    return f"{mask.mean() * 100:6.3f}% of pixels changed, within x {box[0]}..{box[2]}, y {box[1]}..{box[3]} ({fill * 100:.0f}% of that box)"


def screen_point(spot: dict, tile: dict, screen: dict, px_per_cell: float) -> tuple[float, float]:
    """Where a cell is on screen in a tile's shot (the camera straight down at the tile's centre)."""
    return screen["w"] / 2 + (spot["x"] - tile["x"]) * px_per_cell, screen["h"] / 2 - (spot["y"] - tile["y"]) * px_per_cell


def cut_out(white: np.ndarray, black: np.ndarray, centre: tuple[float, float], radius: float,
            empty: np.ndarray | None = None, others: list[tuple[float, float]] = ()) -> np.ndarray:
    """An element's cut-out (RGBA) from its two shots alone over the sky, over white and over black:
    matted as the stitch mattes a tile, then only the circle round it kept (`centre` and `radius` in
    screen pixels), the sky's faint veil made transparent and the solid parts opaque, their colour
    the black shot's (VEIL_ALPHA, SOLID_ALPHA). `empty`: its tile's shot over white with everything
    hidden; what is the same in both is what can't be hidden (the map's cliff doodads), left out.
    `others`: where other elements are on the shot (screen pixels), whose own pixels these may be
    (rubble shot with all the rest of the rubble lying there): only what is nearer `centre` than
    any of them is kept."""
    from .stitch import matte, white_level  # here: the stitch imports inject, which imports this module

    rgba = matte(white, black, white_level(white, black) or 230.0, animated=False)
    alpha = rgba[..., 3]
    yy, xx = np.ogrid[: alpha.shape[0], : alpha.shape[1]]
    own = (xx - centre[0]) ** 2 + (yy - centre[1]) ** 2
    alpha[own > radius**2] = 0
    for ox, oy in others:
        alpha[(xx - ox) ** 2 + (yy - oy) ** 2 < own] = 0
    if empty is not None:
        alpha[np.abs(white.astype(np.int16) - empty.astype(np.int16)).max(axis=2) <= EMPTY_MATCH] = 0
    alpha[alpha <= VEIL_ALPHA] = 0
    solid = alpha >= SOLID_ALPHA
    rgba[solid, :3] = black[solid]
    alpha[solid] = 255
    return rgba
