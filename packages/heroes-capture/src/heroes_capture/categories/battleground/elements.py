"""Map elements (ELEMENTS-PLAN.md): what a render cuts out of a map, read from its files when it is
prepared (its structures, towns and camps: element_list), an element's cut-out from its shots alone
over the sky (matting.cut_out), and the holes the cores stand on (filled when a map is prepared, opened
again by the capture script as it starts)."""

import math
import re
import struct

import numpy as np
from scipy import ndimage

from heroes_capture.map.galaxy import functions

# A town hall's ring: the structures of its town's region within this many cells of it (hall,
# fountain, gate, towers, walls at up to 14.4, and the standalone tower a fort takes with it when
# it falls: GameLib kills one within 15). A town region's structures beyond the ring are its
# outposts (the towers, gate and fountain the map adds further down the lane): structures within
# OUTPOST_LINK cells of one another form one.
RING_RADIUS = 15
OUTPOST_LINK = 14
# A group of walls alone isn't an outpost: walls ring their town (Dragon Shire's stand 15 to 15.7
# from the hall, just past RING_RADIUS), so they stay in it.
WALL_PREFIX = "TownWall"
# The standalone tower that goes with a fort (GameLib's Kill Standalone Towers: when a fort dies, the
# first of this type within this many cells of it is killed). It stands outside every town region,
# so it is put in that fort's town.
STANDALONE_TOWER, STANDALONE_FORT, STANDALONE_REACH = "TownCannonTowerL3Standalone", "TownTownHallL2", 15.0
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


def hookup_function(script: str, marker: str) -> str | None:
    """The body of the map script's function that hooks something up for the game's libraries,
    found by what it does (`marker`, a line of it), not by its name: the maps name it variously
    (gt_HookupTownData_Func, gf_HookupTownData, gt_InitializeTownData_Func on Dragon Shire,
    gt_HookupTownDataForField_Func on Towers of Doom). The one with the most hookups, when several."""
    bodies = [b for b in functions(script).values() if marker in b]
    return max(bodies, key=lambda b: b.count(marker)) if bodies else None


def parse_towns(script: str, regions: dict[int, dict]) -> list[dict]:
    """The map's towns, as its script hooks them up for GameLib (libGame_gv_townTownData, numbered
    in that order: each "lv_town += 1"): lane (the lv_lane last set), owner and region."""
    hookup = hookup_function(script, "lv_town += 1;")
    towns, lane = [], None
    if not hookup:
        return towns
    events = r"\blv_lane = (\d+);|\b(lv_town \+= 1);|\.lv_owner = libCore_gv_cOMPUTER_Team(\w+);|\.lv_townRegion = RegionFromId\((\d+)\);"
    for m in re.finditer(events, hookup):
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
    hookup = hookup_function(script, "lv_junglecamp += 1;")
    camps = []
    if not hookup:
        return camps
    for n, block in enumerate(re.split(r"\blv_junglecamp \+= 1;", hookup)[1:], start=1):
        kind = re.search(r"lv_mapDataCampDefenderType = libMapM_ge_JungleCampDefenderTypes_(\w+);", block)
        ids = re.findall(r"lv_mapData(?:CampCaptainSpawnPoint|DefenderSpawnPoints\[\d+\]) = PointFromId\((\d+)\);", block)
        spots = [points[int(i)] for i in ids if int(i) in points]
        if not spots:
            continue
        x, y = sum(p[0] for p in spots) / len(spots), sum(p[1] for p in spots) / len(spots)
        camps.append({"camp": n, "type": kind.group(1) if kind else None, "x": round(x, 3), "y": round(y, 3),
                      "spawns": [list(p) for p in spots], "spread": round(max(math.dist((x, y), p) for p in spots), 3)})
    return camps


def group_outposts(structures: list[dict], towns: list[dict]) -> list[dict]:
    """The towns' outposts, numbered in town order and, within a town, from the hall outwards: a
    town's structures beyond its hall's ring (RING_RADIUS), joined when within OUTPOST_LINK of one
    another. Sets each structure's "outpost" (None: the ring, the cores, anything outside the
    towns, and a town without a hall) and returns the outposts: number, town, lane, owner and the
    middle of the structures. A group of walls alone stays in the town (WALL_PREFIX)."""
    outposts = []
    for u in structures:
        u["outpost"] = None
    for t in towns:
        own = [u for u in structures if u["town"] == t["town"]]
        hall = next((u for u in own if u["type"].startswith("TownTownHall")), None)
        if hall is None:
            continue
        beyond = [u for u in own if math.dist((u["x"], u["y"]), (hall["x"], hall["y"])) > RING_RADIUS]
        clusters: list[list[dict]] = []
        for u in beyond:
            near = [c for c in clusters if any(math.dist((u["x"], u["y"]), (v["x"], v["y"])) <= OUTPOST_LINK for v in c)]
            merged = [u] + [v for c in near for v in c]
            clusters = [c for c in clusters if c not in near] + [merged]
        middles = [(sum(u["x"] for u in c) / len(c), sum(u["y"] for u in c) / len(c), c) for c in clusters]
        middles = [m for m in middles if not all(u["type"].startswith(WALL_PREFIX) for u in m[2])]  # walls alone: the town's
        for x, y, c in sorted(middles, key=lambda m: math.dist(m[:2], (hall["x"], hall["y"]))):
            outposts.append({"outpost": len(outposts) + 1, "town": t["town"], "lane": t["lane"], "owner": t["owner"],
                             "x": round(x, 3), "y": round(y, 3)})
            for u in c:
                u["outpost"] = outposts[-1]["outpost"]
    return outposts


# The death model the game's town structures share (a tower's remains are its dead state's and
# explosions', not in its death list): no say on whether a structure's remains are team-coloured.
GENERIC_DEATH = "TownStructureDeathAnimation"


def standing_team_colour(u: dict, report: dict) -> bool | None:
    """Whether a structure's finished model draws team colour standing (the data report's,
    common/m3.py); None when the report can't tell."""
    model = (report.get("models") or {}).get((report.get("standModels") or {}).get(u["type"]))
    return (model or {}).get("teamColour", {}).get("Stand")


def rubble_team_colour(u: dict, report: dict) -> bool | None:
    """Whether a structure's remains can draw team colour: one of its death models does while its
    death plays or after (its remains are shot while it plays). None when the report can't tell
    (no death model of its own, as a tower: it dies into a dead state and explosions)."""
    models = report.get("models") or {}
    own = [m for m in (report.get("deathModels") or {}).get(u["type"], []) if m != GENERIC_DEATH and m in models]
    if not own:
        return None
    found = [v for m in own for v in (models[m].get("teamColour") or {}).values()]
    return any(found) if found else None


def stand_reach(u: dict, report: dict, distance: float) -> float | None:
    """How far a structure's finished model can reach on screen from its cell, in cells: its mesh's
    reach across the ground at its top's height, as the capture camera (`distance` up, straight
    down) sees it: r·d/(d−h). None when the report can't tell."""
    model = (report.get("models") or {}).get((report.get("standModels") or {}).get(u["type"])) or {}
    extent = model.get("extent")
    if not extent:
        return None
    scale = model.get("scale") or 1.0
    reach, height = extent["reach"] * scale, max(0.0, extent["height"] * scale)
    return reach * distance / max(distance - height, 1e-3)


def may_overlap(u: dict, n: dict, report: dict, distance: float, fallback: float) -> bool:
    """Whether two standing structures can overlap on screen: nearer than both their models reach
    (stand_reach); nearer than `fallback` when the report can't tell for either."""
    a, b = stand_reach(u, report, distance), stand_reach(n, report, distance)
    apart = math.dist((u["x"], u["y"]), (n["x"], n["y"]))
    return apart < (a + b if a is not None and b is not None else fallback)


def goes_with_a_fort(u: dict, structures: list[dict]) -> bool:
    """Whether a structure is a standalone tower GameLib takes down with a fort (STANDALONE_REACH):
    it falls before its fort (rubble_waves) and has no copy (left standing while its copy fell, it
    fell with the fort, into the fort's shot)."""
    return u.get("type") == STANDALONE_TOWER and any(
        f.get("type") == STANDALONE_FORT and math.dist((u["x"], u["y"]), (f["x"], f["y"])) <= STANDALONE_REACH for f in structures)


def standalone_towns(structures: list[dict]) -> None:
    """Each standalone tower outside every town put in the town of the fort GameLib takes it down
    with (STANDALONE_REACH): the nearest within reach."""
    forts = [u for u in structures if u["type"] == STANDALONE_FORT and u["town"] is not None]
    for u in structures:
        if u["type"] == STANDALONE_TOWER and u["town"] is None:
            near = [f for f in forts if math.dist((u["x"], u["y"]), (f["x"], f["y"])) <= STANDALONE_REACH]
            if near:
                u["town"] = min(near, key=lambda f: math.dist((u["x"], u["y"]), (f["x"], f["y"])))["town"]


def element_list(script: str, objects: str, regions_xml: str) -> dict:
    """What a render cuts out of a map (ELEMENTS-PLAN.md), read from its files when it is prepared:
    its structures (Objects' placed units) with their towns (the region each stands in) and
    outposts (group_outposts), its camps, and for each element the radius of the circle that
    isolates it. Which tile each is shot from is the capture's to pick (source_tile): the grid can
    be planned again once the map is running."""
    regions = parse_regions(regions_xml)
    towns = parse_towns(script, regions)
    structures = []
    for u in parse_objects(objects)[0]:
        if not is_structure(u["type"]):
            continue
        town = next((t["town"] for t in towns if region_contains(regions.get(t["region"], {"shapes": []}), u["x"], u["y"])), None)
        structures.append({"id": u["id"], "type": u["type"], "x": u["x"], "y": u["y"], "owner": OWNERS.get(u["player"]),
                           "town": town, "core": u["type"].endswith("Core"), "radius": STRUCTURE_RADIUS})
    standalone_towns(structures)
    outposts = group_outposts(structures, towns)
    camps = parse_camps(script, parse_points(objects))
    for c in camps:
        c["radius"] = round(c["spread"] + CAMP_MARGIN, 3)
    return {"structures": structures, "towns": towns, "outposts": outposts, "camps": camps}


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


def copy_candidates(structures: list[dict], camps: list[dict], bounds: dict, map_width: int, light: np.ndarray | None,
                    least: float | None = None) -> dict[int, list[tuple[float, float]]]:
    """Each copied structure's candidate spots (the cores, COPY_EXCLUDED and a fort's standalone
    tower, goes_with_a_fort, have none), by id,
    nearest first: inside the bounds, clear of every structure's and camp's circle (by `least`
    more, or the smallest rubble's), under the same lighting as the structure's cell when the map
    has a lighting map, on the same part of a cell (a wall on a half cell: a copy is placed as its
    footprint fits). Not yet clear of one another: copy_spots picks among them, the calibration
    (calibrate.py) tries them in turn."""
    margin = int(STRUCTURE_RADIUS)
    taken = [(u["x"], u["y"], u["radius"]) for u in structures] + [(c["x"], c["y"], c["radius"]) for c in camps]
    grid = [(x, y) for x in range(int(bounds["left"]) + margin, int(bounds["right"]) - margin + 1, 2)
            for y in range(int(bounds["bottom"]) + margin, int(bounds["top"]) - margin + 1, 2)]
    smallest = least if least is not None else min((r for _, r in RUBBLE_RADII), default=STRUCTURE_RADIUS)
    free = [p for p in grid if all(math.dist(p, (x, y)) >= r + smallest for x, y, r in taken)]
    light_of = (lambda x, y: lighting_at(light, map_width, x, y)) if light is not None else None
    found = {}
    for u in structures:
        if u["core"] or u["type"] in COPY_EXCLUDED or goes_with_a_fort(u, structures):
            continue
        here = light_of(u["x"], u["y"]) if light_of else None
        fx, fy, rubble = u["x"] % 1, u["y"] % 1, rubble_radius(u)
        options = [(p[0] + fx, p[1] + fy) for p in free if here is None or float(np.abs(light_of(*p) - here).max()) <= LIGHT_MATCH]
        options = [p for p in options if all(math.dist(p, (x, y)) >= r + rubble for x, y, r in taken)]
        found[u["id"]] = sorted(options, key=lambda p: math.dist(p, (u["x"], u["y"])))
    return found


def copy_spots(structures: list[dict], camps: list[dict], bounds: dict, map_width: int, light: np.ndarray | None) -> dict:
    """A spare spot for each structure's copy (the elements capture's rubble, prepared while the
    structures stand: element_capture.py), by id, when the map isn't calibrated: the nearest of
    its candidates (copy_candidates) clear of every other copy's, the structures with the fewest
    to choose from first. The cores and COPY_EXCLUDED get none, nor any the map has no room for:
    they fall themselves. Unchecked: on a map with a void inside its bounds (Dragon Shire's black
    corners) a spot can be in it, and the copy's rubble comes out black (calibrate.py checks)."""
    candidates = copy_candidates(structures, camps, bounds, map_width, light)
    by_id = {u["id"]: u for u in structures}
    spots: dict = {}
    used: list[tuple[float, float, float]] = []
    for sid in sorted(candidates, key=lambda i: len(candidates[i])):
        rubble = rubble_radius(by_id[sid])
        options = [p for p in candidates[sid] if all(math.dist(p, (x, y)) >= r + rubble for x, y, r in used)]
        if options:
            p = options[0]
            used.append((p[0], p[1], rubble))
            spots[sid] = {"x": p[0], "y": p[1]}
    return spots


def calibrated_copy_spots(structures: list[dict], calibration: dict) -> dict:
    """The copy spots a calibration checked (calibrate.py: a copy made there came out as its
    structure looks), by id, for the structures the map still has."""
    ids = {u["id"] for u in structures}
    return {int(k): {"x": v["x"], "y": v["y"]} for k, v in (calibration.get("copySpots") or {}).items() if int(k) in ids}


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
    """The circle a structure's rubble is cut out by: its kind's, measured in the map's reviewed
    calibration ("rubbleRadius", calibration_review.rubble_radii), else RUBBLE_RADII."""
    if structure.get("rubbleRadius"):
        return structure["rubbleRadius"]
    if structure.get("core"):
        return CORE_RUBBLE_RADIUS
    return next((r for prefix, r in RUBBLE_RADII if structure["type"].startswith(prefix)), structure["radius"])


# Real seconds from a structure's fall to its rubble's shot, by kind and team: the map's own, picked
# in its calibration's review from a film of each kind falling (calibrate.py: the moment its rubble
# looks best), else these, chosen the same way from the film probe on Battlefield of Eternity
# (each kind on each team filmed falling, a frame every quarter second). The first entry that
# matches: a type prefix, a suffix ("" any), a team. Hell gates leave nothing once their
# explosion is over. A structure none matches: FALL_WAIT_OTHER; a core: CORE_WAIT (chosen from its
# own film: by 6 its debris has settled smaller).
FALL_WAITS = (
    ("TownCannonTowerL3Standalone", "", "order", 2.10), ("TownCannonTowerL3", "", "order", 2.02), ("TownCannonTowerL2", "", "order", 2.12),
    ("TownCannonTowerL3Standalone", "", "chaos", 3.00), ("TownCannonTowerL3", "", "chaos", 2.25), ("TownCannonTowerL2", "", "chaos", 2.21),
    ("TownGateL3", "", "order", 1.33), ("TownGate", "", "order", 1.23), ("TownGate", "", "chaos", 1.0),
    ("TownWallRadial", "L3", "order", 2.48), ("TownWallRadial", "L2", "order", 2.46),
    ("TownWallRadial", "L3", "chaos", 2.24), ("TownWallRadial", "L2", "chaos", 1.33),
    ("TownMoonwellL3", "", "order", 3.30), ("TownMoonwellL2", "", "order", 2.64),
    ("TownMoonwellL3", "", "chaos", 1.92), ("TownMoonwellL2", "", "chaos", 1.84),
    ("TownTownHallL3", "", "order", 3.02), ("TownTownHallL2", "", "order", 2.73),
    ("TownTownHallL3", "", "chaos", 2.74), ("TownTownHallL2", "", "chaos", 2.62),
)
FALL_WAIT_OTHER = 2.0
CORE_WAIT = 4.8


def fall_wait(structure: dict, picked: dict | None = None) -> float:
    """How long a fallen structure is left before its rubble is shot (FALL_WAITS; `picked`: the
    map's, from its calibration's review, by "<type>|<owner>")."""
    if structure["core"]:
        return CORE_WAIT
    kind = structure["type"]
    if (picked or {}).get(f"{kind}|{structure.get('owner')}") is not None:
        return picked[f"{kind}|{structure.get('owner')}"]
    return next((wait for prefix, suffix, owner, wait in FALL_WAITS
                 if kind.startswith(prefix) and kind.endswith(suffix) and structure.get("owner") == owner), FALL_WAIT_OTHER)


# Each structure's animations started over before its shots ("el restart": swapped to its finished
# model, from the start, no build) and paused ("el hold") at its kind's standing
# time, picked in the calibration's review from a film (calibrate.py's standing stage): the same
# moment every run, and its effects lit (a model's particles only run, and are only kept, near
# the camera: a structure the camera wasn't on when the map paused had none, Dragon Shire's forts'
# braziers unlit). A kind without a picked time: LIGHT_WAIT.
LIGHT_WAIT = 2.0


def stand_wait(structure: dict, picked: dict | None = None) -> float:
    """How long a structure runs from its restart before its shots (LIGHT_WAIT, or `picked`: the
    map's standing times, by type: the animation is the same for both teams)."""
    return (picked or {}).get(structure["type"], LIGHT_WAIT)


def rubble_waves(structures: list[dict], radius=lambda u: u["radius"]) -> list[list[dict]]:
    """The structures in waves to bring down together, so that each one's rubble can be shot alone:
    no two in a wave nearer each other than both their circles reach (their rubble would be in
    each other's shots), and each core in a wave of its own, last (brought down together, the
    second core's fall came out unlike one alone: no smoke, its pool still red, few chunks). Each structure goes into the first wave it fits, the biggest circles first (`radius`:
    each one's circle; a structure's rubble's, rubble_radius). Camps (no "core") the same: those in
    a wave spawned together. A fort's standalone tower first of all: brought down after its fort,
    it is already gone (GameLib takes it down with the fort, STANDALONE_REACH)."""
    waves: list[list[dict]] = []
    cores = [u for u in structures if u.get("core")]
    for u in sorted((u for u in structures if not u.get("core")), key=lambda u: (not goes_with_a_fort(u, structures), -radius(u))):
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


def called(element: dict) -> str:
    """An element (or its record) as the run's log names it: its name in the game (a structure's,
    from the data report) and where it is."""
    kind = "camp" if element.get("kind") == "camp" or "camp" in element else "structure"
    return f"{element.get('name') or element.get('type') or kind} ({element['x']:g}, {element['y']:g})"
