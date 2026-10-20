"""Where things are: a map cell on a shot's screen, and the part of a frame round a circle."""

import math


def screen_point(spot: dict, tile: dict, screen: dict, px_per_cell: float) -> tuple[float, float]:
    """Where a cell is on screen in a tile's shot (the camera straight down at the tile's centre)."""
    return screen["w"] / 2 + (spot["x"] - tile["x"]) * px_per_cell, screen["h"] / 2 - (spot["y"] - tile["y"]) * px_per_cell


def circle_box(centre: tuple[float, float], radius: float, shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    """The rows and columns (v0, v1, u0, u1) of a frame of `shape` that hold a circle (`centre`
    and `radius` in pixels), a pixel to spare each way: all a cut-out needs of its shots."""
    v0, v1 = max(0, math.floor(centre[1] - radius) - 1), min(shape[0], math.ceil(centre[1] + radius) + 2)
    u0, u1 = max(0, math.floor(centre[0] - radius) - 1), min(shape[1], math.ceil(centre[0] + radius) + 2)
    return v0, max(v0, v1), u0, max(u0, u1)
