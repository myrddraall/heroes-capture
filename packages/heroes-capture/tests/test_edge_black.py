"""The game's black at the map's edge, removed by the stitch (capture/edge_black.py)."""

import numpy as np

from heroes_capture.capture.edge_black import edge_black_removed

PPC = 10.0  # px per cell


def canvas(width_cells=40, height_cells=30, margin_cells=4):
    """A canvas round a map of width x height cells with a margin of sky: all transparent, and the
    canvas pixel of cell (0, 0) at (ax, ay)."""
    w, h = int((width_cells + 2 * margin_cells) * PPC), int((height_cells + 2 * margin_cells) * PPC)
    ax, ay = margin_cells * PPC, (margin_cells + height_cells) * PPC
    return np.zeros((h, w, 4), np.uint8), ax, ay


def paint_disc(rgba, ax, ay, cx, cy, radius_cells, colour):
    """An opaque disc of `colour` at cell (cx, cy)."""
    ys, xs = np.mgrid[0:rgba.shape[0], 0:rgba.shape[1]]
    inside = ((xs - (ax + cx * PPC)) ** 2 + (ys - (ay - cy * PPC)) ** 2) <= (radius_cells * PPC) ** 2
    rgba[inside, :3] = colour
    rgba[inside, 3] = 255
    return inside


def game_darkened(rgba, ax, ay, line_cell_y):
    """What the game does north of a line: black from the line on, fading over the cell before."""
    ys = np.arange(rgba.shape[0])[:, None]
    cy = (ay - ys) / PPC
    k = np.clip(line_cell_y - cy, 0.0, 1.0)  # 0 at the line, 1 a cell south of it
    rgb = rgba[..., :3].astype(np.float32) * k[..., None]
    rgba[..., :3] = np.where(rgba[..., 3:4] > 0, rgb, rgba[..., :3]).astype(np.uint8)


def test_a_model_reaching_the_last_cell_loses_the_black_and_fades_to_the_line():
    rgba, ax, ay = canvas()
    disc = paint_disc(rgba, ax, ay, 20, 28.5, 2.5, (80, 160, 200))  # an orb over cells 26-31: into the last cell and past the map
    game_darkened(rgba, ax, ay, 29.0)  # the last cell row 29-30 and beyond black, fade over 28-29
    assert ((rgba[..., :3].max(axis=2) <= 8) & (rgba[..., 3] > 0)).sum() > 500  # the cap is there
    removed, restored = edge_black_removed(rgba, PPC, ax, ay, (40, 30))
    assert removed > 500 and restored > 300
    ys = np.arange(rgba.shape[0]); cy = (ay - ys) / PPC
    col = rgba[:, int(ax + 20 * PPC)]  # the column through the orb's middle
    past = col[cy >= 29.0]; assert (past[:, 3] == 0).all()  # nothing left at or past the line
    band = col[(cy > 28.1) & (cy < 28.9)]
    assert (band[:, 3] > 0).all() and (band[:, 3] < 255).all()  # fading out towards the line ...
    assert np.all(np.diff(band[:, 3].astype(int)) >= 0)  # ... alpha rising southwards, away from the line
    assert np.abs(band[:, :3].astype(int) - (80, 160, 200)).max() <= 12  # ... in the orb's own colour
    south = col[(cy > 26.5) & (cy < 27.9)]
    assert (south[:, 3] == 255).all() and np.abs(south[:, :3].astype(int) - (80, 160, 200)).max() <= 2  # untouched past the fade


def test_dark_models_inside_the_map_are_left_alone():
    rgba, ax, ay = canvas()
    paint_disc(rgba, ax, ay, 20, 15, 2.0, (0, 0, 0))  # a black disc in the middle of the map, on sky
    paint_disc(rgba, ax, ay, 8, 28.5, 1.5, (0, 0, 0))  # and one at the edge not meeting the sky: inside a lit floor
    paint_disc(rgba, ax, ay, 8, 27.0, 4.0, (120, 120, 120))
    paint_disc(rgba, ax, ay, 8, 28.5, 1.5, (0, 0, 0))
    before = rgba.copy()
    assert edge_black_removed(rgba, PPC, ax, ay, (40, 30)) == (0, 0)
    assert np.array_equal(rgba, before)


def test_a_canvas_without_black_is_untouched_quickly():
    rgba, ax, ay = canvas()
    paint_disc(rgba, ax, ay, 20, 15, 10.0, (90, 90, 90))
    before = rgba.copy()
    assert edge_black_removed(rgba, PPC, ax, ay, (40, 30)) == (0, 0)
    assert np.array_equal(rgba, before)
