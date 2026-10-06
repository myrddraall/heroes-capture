"""Map elements (ELEMENTS-PLAN.md): a map's structures, camps and objective, read from its placed
objects, and the pixels two shots of the same view differ in (an element shown and not). Used by
the elements probe (probes.py, --probe-elements) for now, and the holes structures stand on: filled
when a map is prepared (inject.py), opened again by the capture script as it starts."""

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


# t3CellFlags (the map's terrain cells, a byte each): the cell is a hole, not drawn (the sky shows
# through). Battlefield of Eternity's cores each stand on a small hole their pedestal covers.
CELL_HOLE = 4


def parse_objects(objects: str) -> tuple[list[dict], list[dict]]:
    """A map's placed units and doodads (its Objects file): type, cell, owner."""

    def parse(tag: str, type_key: str) -> list[dict]:
        found = []
        for m in re.finditer(rf"<{tag} [^>]*>", objects):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(0)))
            x, y, *_ = (float(v) for v in attrs["Position"].split(","))
            found.append({"type": attrs.get(type_key, ""), "x": x, "y": y, "player": int(attrs.get("Player", attrs.get("TeamColor", "0")) or 0)})
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


def cut_out(white: np.ndarray, black: np.ndarray, centre: tuple[float, float], radius: float) -> np.ndarray:
    """An element's cut-out (RGBA) from its two shots alone over the sky, over white and over black:
    matted as the stitch mattes a tile, then only the circle round it kept (`centre` and `radius` in
    screen pixels: what can't be hidden, the map's cliff doodads, lies outside it), the sky's faint
    veil made transparent and the solid parts opaque, their colour the black shot's (VEIL_ALPHA,
    SOLID_ALPHA)."""
    from .stitch import matte, white_level  # here: the stitch imports inject, which imports this module

    rgba = matte(white, black, white_level(white, black) or 230.0)
    alpha = rgba[..., 3]
    yy, xx = np.ogrid[: alpha.shape[0], : alpha.shape[1]]
    alpha[(xx - centre[0]) ** 2 + (yy - centre[1]) ** 2 > radius**2] = 0
    alpha[alpha <= VEIL_ALPHA] = 0
    solid = alpha >= SOLID_ALPHA
    rgba[solid, :3] = black[solid]
    alpha[solid] = 255
    return rgba
