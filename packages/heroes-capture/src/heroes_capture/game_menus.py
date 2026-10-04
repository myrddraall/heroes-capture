"""The game's own menus over a match (the Esc menu, its Options, the exit dialog), recognised on
a screen grab at any screen size. A module of its own so it imports anywhere (game_state.py needs
Windows); see game_menu_open.
"""

import numpy as np

WORK_HEIGHT = 720  # rows game_menu_open works at, whatever the screen's size


def _lavender(frame: np.ndarray) -> np.ndarray:
    """The game's menu buttons' border colour: a light blue-violet (about 120, 140, 230)."""
    f = frame[..., :3].astype(np.int16)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    return (b > 190) & (r > 70) & (g > 70) & (b - r > 45) & (b - g > 45) & (np.abs(r - g) < 50)


def game_menu_open(frame: np.ndarray) -> bool:
    """The game's own menus over the match: the Esc menu, its Options, the exit dialog (Alt+F4).
    All of them have the game's buttons: a light blue-violet outline, its long straight edges a
    button's height apart (overlapping along a button's width at least: side by side buttons'
    edges run together on a small screen). Two such outlines make a menu; the map's own art has
    long blue-violet streaks (Battlefield of Eternity's sky) but no such pairs. Measured relative to the screen's
    height, as the interface scales, on a copy shrunk to about WORK_HEIGHT rows (rows and columns
    alike; each row the brightest of those it stands for, so the thin edges survive)."""
    h = frame.shape[0]
    k = max(1, h // WORK_HEIGHT)
    rows = frame[: h // k * k, ::k, :3]
    lav = _lavender(rows.reshape(h // k, k, rows.shape[1], 3).max(axis=1))
    height = lav.shape[0]
    longest = 0.10 * height  # the narrowest button (the exit dialog's) is ~0.17 of the height wide
    overlap = lambda a0, b0, a1, b1: min(b0, b1) - max(a0, a1) >= longest  # noqa: E731
    edges: list[list[int]] = []  # [first row, x0, x1, last row] of each long edge
    for y in np.nonzero(lav.sum(axis=1) >= longest)[0]:
        steps = np.diff(np.concatenate([[0], lav[y].astype(np.int8), [0]]))
        runs: list[list[int]] = []
        for x0, x1 in zip(np.nonzero(steps == 1)[0], np.nonzero(steps == -1)[0]):
            if runs and x0 - runs[-1][1] <= 0.03 * height:  # a bottom edge's bright middle splits it
                runs[-1][1] = x1
            else:
                runs.append([x0, x1])
        for x0, x1 in runs:
            if x1 - x0 < longest:
                continue
            for e in edges:  # the same edge a row further down (edges are a few rows thick)
                if y - e[3] <= 0.006 * height and overlap(x0, x1, e[1], e[2]):
                    e[3] = y
                    break
            else:
                edges.append([y, x0, x1, y])
    edges = [e for e in edges if e[3] - e[0] <= 0.012 * height]  # an outline is thin; a solid patch of the colour isn't one
    outlines = 0
    for i, (y0, a0, b0, _) in enumerate(edges):
        for y1, a1, b1, _ in edges[i + 1:]:
            if 0.025 <= (y1 - y0) / height <= 0.08 and overlap(a0, b0, a1, b1):
                # Two buttons side by side (the exit dialog's) run together into one outline.
                outlines += 2 if min(b0, b1) - max(a0, a1) >= 0.4 * height else 1
    return outlines >= 2
