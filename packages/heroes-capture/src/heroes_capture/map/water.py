"""Where a map's water is, from its own files: the water the terrain draws moves even with the game
paused, so a comparison of two shots (the team colour's, from each side) leaves it out.

- t3Water ("WATR", version 110): a 32-byte header (the magic, the version, the count of entries,
  five zeros), then an entry of 56 bytes each: the water's id (32 bytes, null-padded: a CWater),
  8 bytes (an id of the type; zero), and the rectangle it is painted in, x0, y0, x1, y1 as floats in
  map cells (y north, the far edges not included; multiples of 8).
- The water's height: its CWater's first state (<State index="0"><Height value=...>), the map's own
  WaterData first, else the mods' (the last loaded wins).
- t3HeightMap ("HMAP", version 101): the terrain's height at each cell corner, (width + 1) x
  (height + 1) records of 6 bytes from byte 32, row 0 the south edge; the first uint16 of each is
  the height as raw * quantizeScale - quantizeBias (t3Terrain.xml's vertData).

A cell is water where it is inside one of its water's rectangles and its lowest corner is below
that water's height (Dragon Shire: its six ponds, 2308 cells). None of this format is documented
anywhere found (sc2mapster's Data/Water page lists the editor's fields only); it was decoded from
Dragon Shire, Raven Court, Docks and others, whose files all fit it."""

import re
import struct
import xml.etree.ElementTree as ET

import numpy as np
from scipy import ndimage

WATER_MAGIC, ENTRY, HEADER = b"WATR", 56, 32
HEIGHT_MAGIC, VERTEX = b"HMAP", 6
# Cells round the water left out too: its shoreline's waves (the water's BeachShoreline) move at
# its edge.
WATER_GROW = 2


def water_rects(t3water: bytes | None) -> list[tuple[str, int, int, int, int]]:
    """Each water entry: its CWater id and its rectangle (x0, y0, x1, y1, cells; far edges out)."""
    if not t3water or t3water[:4] != WATER_MAGIC or len(t3water) < HEADER:
        return []
    count = struct.unpack_from("<I", t3water, 8)[0]
    found = []
    for i in range(count):
        at = HEADER + ENTRY * i
        if at + ENTRY > len(t3water):
            break
        name = t3water[at:at + 32].split(b"\0")[0].decode("latin1")
        x0, y0, x1, y1 = struct.unpack_from("<4f", t3water, at + 40)
        found.append((name, int(x0), int(y0), int(x1), int(y1)))
    return found


def water_heights(texts: list[str]) -> dict[str, float]:
    """Each CWater's height (its first state's), the later texts winning."""
    heights: dict[str, float] = {}
    for text in texts:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        for water in root.iter("CWater"):
            for state in water.findall("State"):
                height = state.find("Height")
                if state.get("index", "0") == "0" and height is not None and height.get("value"):
                    heights[water.get("id")] = float(height.get("value"))
    return heights


def corner_heights(heightmap: bytes | None, terrain_xml: str) -> np.ndarray | None:
    """The terrain's height at each cell corner, [y, x] (y north), from t3HeightMap and t3Terrain.xml."""
    if not heightmap or heightmap[:4] != HEIGHT_MAGIC:
        return None
    vw, vh = struct.unpack_from("<II", heightmap, 8)
    bias = re.search(r'quantizeBias="([-\d.e+]+)"', terrain_xml)
    scale = re.search(r'quantizeScale="([-\d.e+]+)"', terrain_xml)
    if not bias or not scale or len(heightmap) < HEADER + vw * vh * VERTEX:
        return None
    raw = np.frombuffer(heightmap, "<u2", count=vw * vh * VERTEX // 2, offset=HEADER).reshape(vh, vw, VERTEX // 2)[..., 0]
    return raw.astype(np.float32) * float(scale.group(1)) - float(bias.group(1))


def water_cells(t3water: bytes | None, heightmap: bytes | None, terrain_xml: str, water_texts: list[str],
                width: int, height: int) -> np.ndarray:
    """Which map cells are water, [y, x] (see the module's note). A water whose height isn't found
    counts all of its rectangle; without the terrain's heights, every rectangle counts."""
    wet = np.zeros((height, width), bool)
    corners = corner_heights(heightmap, terrain_xml)
    lowest = None
    if corners is not None and corners.shape == (height + 1, width + 1):
        lowest = np.minimum.reduce([corners[:-1, :-1], corners[:-1, 1:], corners[1:, :-1], corners[1:, 1:]])
    heights = water_heights(water_texts)
    for name, x0, y0, x1, y1 in water_rects(t3water):
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        if lowest is None or name not in heights:
            wet[y0:y1, x0:x1] = True
        else:
            wet[y0:y1, x0:x1] |= lowest[y0:y1, x0:x1] < heights[name]
    return wet


def water_runs(wet: np.ndarray, grow: int = WATER_GROW) -> list[list[int]]:
    """The water cells (grown by `grow` cells) as runs, [y, x0, x1] with x1 not included: the
    manifest's "water"."""
    if grow and wet.any():
        wet = ndimage.binary_dilation(wet, iterations=grow)
    runs = []
    for y in range(wet.shape[0]):
        row = np.flatnonzero(np.diff(np.concatenate(([0], wet[y].astype(np.int8), [0]))))
        runs += [[y, int(a), int(b)] for a, b in zip(row[::2], row[1::2])]
    return runs


def water_pixels(shape: tuple, runs: list[list[int]], px_per_cell: float, ax: float, ay: float) -> np.ndarray:
    """The water's pixels on a canvas of `shape` (bool, h x w): the canvas's pixel of map cell
    (x, y) is (ax + x * px_per_cell, ay - y * px_per_cell)."""
    h, w = shape[:2]
    mask = np.zeros((h, w), bool)
    for y, x0, x1 in runs:
        v0, v1 = int(np.floor(ay - (y + 1) * px_per_cell)), int(np.ceil(ay - y * px_per_cell))
        u0, u1 = int(np.floor(ax + x0 * px_per_cell)), int(np.ceil(ax + x1 * px_per_cell))
        v0, v1, u0, u1 = max(0, v0), min(h, v1), max(0, u0), min(w, u1)
        if v1 > v0 and u1 > u0:
            mask[v0:v1, u0:u1] = True
    return mask
