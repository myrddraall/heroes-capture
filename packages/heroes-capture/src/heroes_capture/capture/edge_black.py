"""The game's black at the map's edge (stitch): the map's last cell row and column, and anything
past them, are drawn black, fading in over the cell before (Punisher Arena's north orb, Battlefield
of Eternity's east chains: models that reach into the last cell). It is the game's, not the map's:
unchanged by the vision mask, the fog-of-war data or the camera bounds (three renders).

Removed from the stitched image: a solid black patch within NEAR_CELLS of the map's edge that
meets the sky becomes transparent, and the fade before it is undone. The darkening runs from the
line where the black meets the lit model, straight across the model (the game's: a straight edge
in map cells), over one cell: a pixel at distance s inside the line was darkened by k = s / cell,
so its colour is brightened by 1 / k and its transparency set to k, which gives the model fading
out to the line. Never brighter than the model just past the fade: a pixel that wasn't darkened
(another model at another height in front of the line) keeps its own colour."""

import numpy as np
from scipy import ndimage

NEAR_CELLS = 3.0  # black this close to the map's edge is the game's, not a dark model's
MIN_CUT = 10  # a black patch must meet a lit model along at least this many pixels
BRIGHT_PERCENTILE = 98  # the model's brightness just past the fade, the brightening's limit
K_MIN = 0.02  # the fade's smallest factor: at the line, as good as transparent


def edge_black_removed(rgba: np.ndarray, px_per_cell: float, ax: float, ay: float, map_size: tuple[int, int]) -> tuple[int, int]:
    """The game's black at the map's edge removed from a stitched canvas (RGBA uint8, in place):
    the canvas's pixel of map cell (x, y) is (ax + x * px_per_cell, ay - y * px_per_cell), the map
    `map_size` cells wide and high. Returns the pixels made transparent and the pixels whose fade
    was undone. Only the canvas's strips along the map's four edges are looked at."""
    h, w = rgba.shape[:2]
    width, height = map_size
    reach = int(np.ceil((NEAR_CELLS + 2) * px_per_cell))
    left, right = int(round(ax)), int(round(ax + width * px_per_cell))
    top, bottom = int(round(ay - height * px_per_cell)), int(round(ay))
    removed = restored = 0
    for y0, y1, x0, x1 in ((top - reach, top + reach, 0, w), (bottom - reach, bottom + reach, 0, w),
                           (0, h, left - reach, left + reach), (0, h, right - reach, right + reach)):
        y0, y1, x0, x1 = max(0, y0), min(h, y1), max(0, x0), min(w, x1)
        if y1 - y0 < 4 or x1 - x0 < 4:
            continue
        r, f = _strip(rgba[y0:y1, x0:x1], px_per_cell, ax - x0, ay - y0, width, height)
        removed += r
        restored += f
    return removed, restored


def _strip(view: np.ndarray, ppc: float, ax: float, ay: float, width: int, height: int) -> tuple[int, int]:
    """edge_black_removed on one strip of the canvas (a view, changed in place; ax, ay relative to it)."""
    alpha, rgb = view[..., 3], view[..., :3]
    peak = rgb.max(axis=2)
    dark = (alpha >= 8) & (peak <= 8)
    if not dark.any():
        return 0, 0
    sky = alpha < 8
    labels, count = ndimage.label(dark)
    hs, ws = alpha.shape
    cx = (np.arange(ws, dtype=np.float32) - ax) / ppc
    cy = (ay - np.arange(hs, dtype=np.float32)) / ppc
    # Cells to the map's edge, as a row and a column distance broadcast (no full grid in memory).
    near_edge = (np.minimum(cx, width - cx) <= NEAR_CELLS)[None, :] | (np.minimum(cy, height - cy) <= NEAR_CELLS)[:, None]
    near = np.zeros(count + 1, dtype=bool)
    near[np.unique(labels[dark & near_edge])] = True
    touch = np.zeros(count + 1, dtype=bool)
    touch[np.unique(labels[ndimage.binary_dilation(sky, iterations=2) & dark])] = True
    keep = near & touch
    keep[0] = False
    if not keep.any():
        return 0, 0
    void = keep[labels]
    lit = (alpha >= 8) & ~void
    band = ppc
    restored = 0
    for i in np.nonzero(keep)[0]:
        cut = (labels == i) & ndimage.binary_dilation(lit, iterations=1)  # where the black meets the model
        if cut.sum() < MIN_CUT:
            continue
        pts = np.column_stack(np.nonzero(cut)).astype(np.float64)
        centre = pts.mean(axis=0)
        _, _, vt = np.linalg.svd(pts - centre, full_matrices=False)
        along_dir, normal = vt[0], vt[1]  # the line's direction, and across it
        side = np.column_stack(np.nonzero(ndimage.binary_dilation(cut, iterations=2) & lit)).mean(axis=0) - centre
        if side @ normal < 0:
            normal = -normal  # pointing into the model
        proj = (pts - centre) @ along_dir
        lo, hi = proj.min() - band, proj.max() + band
        yy, xx = np.nonzero(lit)
        rel = np.column_stack([yy, xx]) - centre
        along, s = rel @ along_dir, rel @ normal
        inside = (along >= lo) & (along <= hi)
        sel = inside & (s <= band)
        ref = inside & (s > band) & (s <= 2 * band)
        if not ref.any():
            continue
        limit = max(float(np.percentile(peak[yy[ref], xx[ref]], BRIGHT_PERCENTILE)), 1.0)
        sy, sx = yy[sel], xx[sel]
        k = np.clip(s[sel] / band, K_MIN, 1.0)
        k = np.clip(np.maximum(k, peak[sy, sx] / limit), K_MIN, 1.0)
        view[sy, sx, :3] = np.clip(rgb[sy, sx].astype(np.float32) / k[:, None], 0, 255).astype(np.uint8)
        view[sy, sx, 3] = (alpha[sy, sx] * k).astype(np.uint8)
        restored += int((k < 0.999).sum())
    view[void, 3] = 0
    return int(void.sum()), restored
