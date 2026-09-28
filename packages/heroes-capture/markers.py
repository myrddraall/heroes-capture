"""Find the registration markers in a screenshot and fit the camera mapping they imply.

The capture script pins solid magenta labels to known ground points around each screenshot
(offsets in map cells from the tile's camera target). Where they land on screen gives a linear
mapping from map cells to pixels for that screenshot: its true position (the game may hold the
camera back at the edges), its scale, and any shear. Measured on real screenshots at a narrow
field of view, the mapping is linear to about a pixel.
"""

import numpy as np
from scipy import ndimage

# Matching tolerances, in px. The first pass allows for the scale being off plan (the game's
# actual scale differs from the planned one by a few percent, more at wide fields of view), so
# it grows with the marker's distance from the screen centre; the second pass, against the
# fitted mapping, is tight.
LOOSE_MISMATCH = 14.0
LOOSE_RELATIVE = 0.15
TIGHT_MISMATCH = 6.0


def magenta_mask(frame: np.ndarray) -> np.ndarray:
    r, g, b = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
    return (r > 170) & (b > 170) & (g < 100)


def blobs(frame: np.ndarray) -> list[tuple[float, float, int]]:
    """(x, y, area) of each magenta blob; the letters of one label are merged."""
    mask = magenta_mask(frame)
    if not mask.any():
        return []
    merged = ndimage.binary_dilation(mask, iterations=6)
    labels, n = ndimage.label(merged)
    idx = range(1, n + 1)
    out = []
    for (cy, cx), area in zip(ndimage.center_of_mass(mask, labels, idx), ndimage.sum(mask, labels, idx)):
        if area > 100:
            out.append((float(cx), float(cy), int(area)))
    return out


def fit_markers(frame: np.ndarray, markers: list[dict], screen: dict, px_per_cell: float) -> dict | None:
    """Match blobs to the expected marker layout and fit `px = a*dx + b*dy + c`,
    `py = d*dx + e*dy + f` (dx, dy in map cells from the tile's planned camera target).

    The layout may be shifted as a whole (camera held back), so the shift is found first: each
    (blob, marker) pairing proposes one, and the shift that lines up the most markers wins.
    Returns None when fewer than five markers are found.
    """
    found = blobs(frame)
    if len(found) < 5:
        return None
    w, h = screen["w"], screen["h"]
    expected = [(w / 2 + m["dx"] * px_per_cell, h / 2 - m["dy"] * px_per_cell) for m in markers]
    tol = [LOOSE_MISMATCH + LOOSE_RELATIVE * np.hypot(ex - w / 2, ey - h / 2) for ex, ey in expected]

    def match(predicted: list[tuple[float, float]], tolerance: list[float]) -> list[tuple[int, int]]:
        pairs = []
        for j, (fx, fy) in enumerate(predicted):
            i, dist = min(((i, (fx - x) ** 2 + (fy - y) ** 2) for i, (x, y, _) in enumerate(found)), key=lambda c: c[1])
            if dist <= tolerance[j] ** 2:
                pairs.append((j, i))
        return pairs

    def fit(pairs):
        rows = np.array([[markers[j]["dx"], markers[j]["dy"], 1.0] for j, _ in pairs])
        xs = np.array([found[i][0] for _, i in pairs])
        ys = np.array([found[i][1] for _, i in pairs])
        cx, *_ = np.linalg.lstsq(rows, xs, rcond=None)
        cy, *_ = np.linalg.lstsq(rows, ys, rcond=None)
        return rows, xs, ys, cx, cy

    def tight_pairs(cx, cy):
        predicted = [
            (float(np.dot([m["dx"], m["dy"], 1.0], cx)), float(np.dot([m["dx"], m["dy"], 1.0], cy)))
            for m in markers
        ]
        return match(predicted, [TIGHT_MISMATCH] * len(markers))

    # The whole layout may be shifted (camera held back), so every (blob, marker) pairing
    # proposes a shift. Each proposal that lines up at least five markers loosely is refitted
    # and re-matched tightly; the proposal with the most tight matches wins. With the layout's
    # irregular spacing a wrong pairing cannot survive the tight pass, so a runner-up with a
    # different pairing and as many tight matches means the pattern is ambiguous: no fit.
    best: list[tuple[int, int]] = []
    runner_up = 0
    seen: set[frozenset] = set()
    for bx, by, _ in found:
        for ex, ey in expected:
            sx, sy = bx - ex, by - ey
            loose = match([(fx + sx, fy + sy) for fx, fy in expected], tol)
            if len(loose) < 5 or frozenset(loose) in seen:
                continue
            seen.add(frozenset(loose))
            _, _, _, cx, cy = fit(loose)
            tight = tight_pairs(cx, cy)
            if len(tight) < 5:
                continue
            if len(tight) > len(best):
                runner_up = len(best) if best and set(best) != set(tight) else runner_up
                best = tight
            elif len(tight) == len(best) and set(tight) != set(best):
                runner_up = len(tight)
    if len(best) < 5 or runner_up >= len(best):
        return None
    # Refit on the winner and re-match until the set of matched markers stops changing.
    rows, xs, ys, cx, cy = fit(best)
    for _ in range(3):
        again = tight_pairs(cx, cy)
        if len(again) < 5 or set(again) == set(best):
            break
        best = again
        rows, xs, ys, cx, cy = fit(best)
    a_rows = rows
    residual = float(max(np.abs(a_rows @ cx - xs).max(), np.abs(a_rows @ cy - ys).max()))
    a, b, c = (float(v) for v in cx)
    d, e, f = (float(v) for v in cy)
    # Where the screen centre really is, in cells from the planned target: solve for px = w/2, py = h/2.
    det = a * e - b * d
    centre = (
        ((w / 2 - c) * e - b * (h / 2 - f)) / det,
        (a * (h / 2 - f) - d * (w / 2 - c)) / det,
    )
    return {
        "found": len(best),
        "fit": [a, b, c, d, e, f],
        "scale": [a, -e],
        "residual": residual,
        "centreOffsetCells": [centre[0], centre[1]],
    }


def describe(fit: dict | None) -> str:
    if fit is None:
        return "markers not found"
    ox, oy = fit["centreOffsetCells"]
    held = f"camera {abs(ox):.1f} cells {'east' if ox > 0 else 'west'}" if abs(ox) > 0.5 else ""
    if abs(oy) > 0.5:
        held += (", " if held else "camera ") + f"{abs(oy):.1f} cells {'north' if oy > 0 else 'south'}"
    return f"{fit['found']}/8 markers, {fit['scale'][0]:.2f} px/cell, fit ±{fit['residual']:.1f} px" + (
        f", {held} of plan" if held else ""
    )
