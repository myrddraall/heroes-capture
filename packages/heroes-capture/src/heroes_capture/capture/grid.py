"""The capture grid: the camera targets the tiles are shot from, in map cells."""

import math


def spread(span: float, max_step: float) -> tuple[int, float]:
    """How many targets cover a span, and how far apart: the first and last on its ends, the rest
    evenly between, at most `max_step` apart (one target for a span shorter than that)."""
    count = max(1, math.ceil(span / max_step) + 1)
    return count, (span / (count - 1) if count > 1 else max_step)


def plan_tiles(area: dict, view_w: float, view_h: float, keep: float) -> dict:
    """The grid over an area (left, bottom, right, top, in cells) for a view of view_w x view_h
    cells, each shot keeping its centre `keep` share at most: cols, rows, the step between targets
    and the tiles (index, row, col, x, y), row 0 the top (north) edge."""
    cols, step_x = spread(area["right"] - area["left"], view_w * keep)
    rows, step_y = spread(area["top"] - area["bottom"], view_h * keep)
    tiles = [{"index": row * cols + col, "row": row, "col": col, "x": area["left"] + col * step_x, "y": area["top"] - row * step_y}
             for row in range(rows) for col in range(cols)]
    return {"cols": cols, "rows": rows, "step": {"x": step_x, "y": step_y}, "tiles": tiles}
