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
    if not fit.get("fit"):
        return "labels name another tile (stale frame)"
    ox, oy = fit["centreOffsetCells"]
    held = f"camera {abs(ox):.1f} cells {'east' if ox > 0 else 'west'}" if abs(ox) > 0.5 else ""
    if abs(oy) > 0.5:
        held += (", " if held else "camera ") + f"{abs(oy):.1f} cells {'north' if oy > 0 else 'south'}"
    ids = f" {fit['ids']}" if fit.get("ids") else ""
    return f"{fit['found']}/8 markers{ids}, {fit['scale'][0]:.2f} px/cell, fit ±{fit['residual']:.1f} px" + (
        f", {held} of plan" if held else ""
    )


# --- Numbered markers -------------------------------------------------------------------------
# Each marker's label is two digits: the marker's number, then the tile number's last digit.
# The digit shapes are learned once per capture from a "0123456789" reference label in the
# game's own font (text tags are drawn flat on the screen at a fixed size, so every "3" looks
# the same), then each label is read by comparing its digits with those shapes.

GLYPH_SIZE = (12, 18)  # width, height every digit is normalised to for comparison


def _label_boxes(frame: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (x0, y0, x1, y1) of magenta label backgrounds."""
    mask = magenta_mask(frame)
    if not mask.any():
        return []
    labels, n = ndimage.label(ndimage.binary_dilation(mask, iterations=2))
    boxes = []
    for sl in ndimage.find_objects(labels):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        if (x1 - x0) >= 12 and (y1 - y0) >= 10 and mask[sl].sum() > 60:
            boxes.append((x0, y0, x1, y1))
    return boxes


def _glyphs(frame: np.ndarray, box: tuple[int, int, int, int]) -> list[np.ndarray]:
    """The dark characters inside one label box, left to right, each normalised to GLYPH_SIZE."""
    from PIL import Image

    x0, y0, x1, y1 = box
    crop = frame[y0:y1, x0:x1].astype(int)
    # Tighten to the magenta background itself (the box came from a dilated mask).
    inside = magenta_mask(frame[y0:y1, x0:x1])
    ys, xs = np.nonzero(inside)
    crop = crop[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    dark = crop.max(axis=2) < 90
    labels, n = ndimage.label(dark)
    out = []
    h, w = dark.shape
    for sl in sorted(ndimage.find_objects(labels), key=lambda s: s[1].start):
        g = dark[sl]
        touches_edge = sl[0].start == 0 or sl[1].start == 0 or sl[0].stop == h or sl[1].stop == w
        if touches_edge or g.sum() < 6 or g.shape[0] < 5:
            continue
        img = Image.fromarray((g * 255).astype(np.uint8)).resize(GLYPH_SIZE, Image.BILINEAR)
        out.append(np.asarray(img, dtype=np.float32) / 255.0)
    return out


def learn_digits(frame: np.ndarray) -> dict[int, np.ndarray] | None:
    """Digit shapes from a screenshot showing the "0123456789" reference label (the widest
    label on screen). None unless exactly ten characters are found."""
    boxes = _label_boxes(frame)
    if not boxes:
        return None
    box = max(boxes, key=lambda b: b[2] - b[0])
    glyphs = _glyphs(frame, box)
    if len(glyphs) != 10:
        return None
    return {d: g for d, g in enumerate(glyphs)}


def _read(glyph: np.ndarray, digits: dict[int, np.ndarray]) -> tuple[int, float]:
    """The best-matching digit and how well it matches (1 = identical)."""
    def score(t: np.ndarray) -> float:
        a, b = glyph - glyph.mean(), t - t.mean()
        denom = np.sqrt((a * a).sum() * (b * b).sum())
        return float((a * b).sum() / denom) if denom else 0.0

    best = max(digits, key=lambda d: score(digits[d]))
    return best, score(digits[best])


def fit_numbered(
    frame: np.ndarray, markers: list[dict], digits: dict[int, np.ndarray], tile_index: int
) -> dict | None:
    """Read every label and fit the mapping from the ones that name a marker and this tile.

    Returns the same shape as fit_markers, plus "tileDigitOk" (False when labels were read but
    named another tile: a stale frame). None when fewer than three markers were identified.
    """
    identified: dict[int, tuple[float, float]] = {}
    wrong_tile = 0
    for box in _label_boxes(frame):
        glyphs = _glyphs(frame, box)
        if len(glyphs) != 2:
            continue
        (k, sk), (t, st) = _read(glyphs[0], digits), _read(glyphs[1], digits)
        if min(sk, st) < 0.6 or k >= len(markers):
            continue
        if t != tile_index % 10:
            wrong_tile += 1
            continue
        x0, y0, x1, y1 = box
        identified[k] = ((x0 + x1) / 2, (y0 + y1) / 2)
    if len(identified) < 3:
        return {"found": 0, "tileDigitOk": False} if wrong_tile >= 3 else None
    ks = sorted(identified)
    rows = np.array([[markers[k]["dx"], markers[k]["dy"], 1.0] for k in ks])
    xs = np.array([identified[k][0] for k in ks])
    ys = np.array([identified[k][1] for k in ks])
    cx, *_ = np.linalg.lstsq(rows, xs, rcond=None)
    cy, *_ = np.linalg.lstsq(rows, ys, rcond=None)
    residual = float(max(np.abs(rows @ cx - xs).max(), np.abs(rows @ cy - ys).max()))
    a, b, c = (float(v) for v in cx)
    d, e, f = (float(v) for v in cy)
    h, w = frame.shape[:2]
    det = a * e - b * d
    centre = (((w / 2 - c) * e - b * (h / 2 - f)) / det, (a * (h / 2 - f) - d * (w / 2 - c)) / det)
    return {
        "found": len(ks),
        "ids": ks,
        "fit": [a, b, c, d, e, f],
        "scale": [a, -e],
        "residual": residual,
        "centreOffsetCells": [centre[0], centre[1]],
        "tileDigitOk": True,
    }


def count_labels(frame: np.ndarray) -> int:
    """How many label boxes are on screen (a clean shot should have none)."""
    return len(_label_boxes(frame))
