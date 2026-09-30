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
    """Solid label magenta. Relative, not absolute: a dark map dims the labels too (Tomb of the
    Spider Queen draws them at about 175, Towers of Doom at 246). Red and blue close together
    keeps out the maps' purples and pinks; what gets through is checked by reading digits."""
    f = frame.astype(np.int16)
    r, g, b = f[:, :, 0], f[:, :, 1], f[:, :, 2]
    return (np.minimum(r, b) - g > 100) & (np.abs(r - b) < 60)


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
        return "tile id names another tile (stale frame)"
    ox, oy = fit["centreOffsetCells"]
    held = f"camera {abs(ox):.1f} cells {'east' if ox > 0 else 'west'}" if abs(ox) > 0.5 else ""
    if abs(oy) > 0.5:
        held += (", " if held else "camera ") + f"{abs(oy):.1f} cells {'north' if oy > 0 else 'south'}"
    ids = f" {fit['ids']}" if fit.get("ids") else ""
    return f"{fit['found']}/8 markers{ids}, {fit['scale'][0]:.2f} px/cell, fit ±{fit['residual']:.1f} px" + (
        f", {held} of plan" if held else ""
    )


# --- Numbered markers -------------------------------------------------------------------------
# Each marker's label is a single digit, its own number, the same on every tile; one more label
# at the view's centre carries the full tile number, zero-padded to at least three digits.
# Labels are magenta digits drawn straight on the map (text tags have no background by
# default). The digit shapes are learned once per capture from a "0123456789" reference label in the
# game's own font (text tags are drawn flat on the screen at a fixed size, so every "3" looks
# the same), then each label is read by comparing its digits with those shapes.

GLYPH_SIZE = (12, 18)  # width, height every digit is normalised to for comparison


def _ink(frame: np.ndarray) -> np.ndarray:
    """Magenta text pixels, including the anti-aliased edges of the strokes (relative, as in
    magenta_mask)."""
    f = frame.astype(np.int16)
    r, g, b = f[:, :, 0], f[:, :, 1], f[:, :, 2]
    return (np.minimum(r, b) - g > 50) & (np.abs(r - b) < 70)


def _label_boxes(frame: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (x0, y0, x1, y1) of magenta labels (the characters of one label merged)."""
    mask = magenta_mask(frame)
    if not mask.any():
        return []
    labels, n = ndimage.label(ndimage.binary_dilation(mask, iterations=4))
    boxes = []
    for sl in ndimage.find_objects(labels):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        if (x1 - x0) >= 12 and (y1 - y0) >= 10 and mask[sl].sum() > 30:
            boxes.append((x0, y0, x1, y1))
    # The game's font leaves wide gaps around a "1", wider than the dilation bridges, so
    # characters on the same line less than half a label's height apart are one label.
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                height = max(a[3] - a[1], b[3] - b[1])
                overlap = min(a[3], b[3]) - max(a[1], b[1])
                gap = max(a[0], b[0]) - min(a[2], b[2])
                if overlap > 0.6 * height and gap < 0.5 * height:
                    boxes[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    return boxes


def _glyphs(frame: np.ndarray, box: tuple[int, int, int, int]) -> list[np.ndarray]:
    """The characters of one label, left to right, each normalised to GLYPH_SIZE.
    Characters are split at the empty columns between them."""
    from PIL import Image

    x0, y0, x1, y1 = box
    ink = _ink(frame[y0:y1, x0:x1])
    cols = ink.any(axis=0)
    out = []
    x = 0
    while x < len(cols):
        if not cols[x]:
            x += 1
            continue
        start = x
        while x < len(cols) and cols[x]:
            x += 1
        g = ink[:, start:x]
        ys = np.nonzero(g.any(axis=1))[0]
        g = g[ys.min() : ys.max() + 1]
        if g.sum() < 6 or g.shape[0] < 5:
            continue
        # Centre on a canvas of the normalised shape, so a narrow "1" keeps its proportions.
        h, w = g.shape
        cw = max(w, round(h * GLYPH_SIZE[0] / GLYPH_SIZE[1]))
        canvas = np.zeros((h, cw), dtype=bool)
        canvas[:, (cw - w) // 2 : (cw - w) // 2 + w] = g
        g = canvas
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
    frame: np.ndarray, markers: list[dict], digits: dict[int, np.ndarray], tile_index: int, id_digits: int = 3
) -> dict | None:
    """Read every label and fit the mapping from the single-digit marker labels, once the tile id
    label (the full tile number, zero-padded to `id_digits`) confirms this is the right tile.

    Returns the same shape as fit_markers, plus "tileIdOk" (False when the id label names another
    tile: a stale frame). None when the id label can't be read or fewer than three markers were
    identified.
    """
    identified: dict[int, tuple[float, float]] = {}
    tile_id = None
    expected = f"{tile_index:0{id_digits}d}"
    for box in _label_boxes(frame):
        glyphs = _glyphs(frame, box)
        reads = [_read(g, digits) for g in glyphs]
        if not reads:
            continue
        text, weakest = "".join(str(d) for d, _ in reads), min(score for _, score in reads)
        # The id only confirms a known number, so it may read weaker (the map shows through
        # behind the label) as long as every digit's best match is the expected one.
        if len(reads) == id_digits and (weakest >= 0.6 or (text == expected and weakest >= 0.45)):
            tile_id = int(text)
        elif weakest < 0.6:
            continue
        elif len(reads) == 1 and reads[0][0] < len(markers):
            x0, y0, x1, y1 = box
            identified[reads[0][0]] = ((x0 + x1) / 2, (y0 + y1) / 2)
    if tile_id is None:
        return None
    if tile_id != tile_index:
        return {"found": 0, "tileIdOk": False}
    if len(identified) < 3:
        return None
    ks = sorted(identified)
    # A label can be thrown off by nearby magenta-ish map lights; while the fit is off by more
    # than 2 px, drop the worst marker and refit, keeping at least four.
    while True:
        rows = np.array([[markers[k]["dx"], markers[k]["dy"], 1.0] for k in ks])
        xs = np.array([identified[k][0] for k in ks])
        ys = np.array([identified[k][1] for k in ks])
        cx, *_ = np.linalg.lstsq(rows, xs, rcond=None)
        cy, *_ = np.linalg.lstsq(rows, ys, rcond=None)
        errors = np.hypot(rows @ cx - xs, rows @ cy - ys)
        residual = float(max(np.abs(rows @ cx - xs).max(), np.abs(rows @ cy - ys).max()))
        if residual <= 2.0 or len(ks) <= 4:
            break
        del ks[int(np.argmax(errors))]
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
        "tileIdOk": True,
    }


def label_height(frame: np.ndarray) -> int | None:
    """The height of the "0123456789" reference label (the widest label on screen)."""
    boxes = _label_boxes(frame)
    if not boxes:
        return None
    x0, y0, x1, y1 = max(boxes, key=lambda b: b[2] - b[0])
    return y1 - y0


def count_labels(frame: np.ndarray, digits: dict[int, np.ndarray] | None = None, height: int | None = None) -> int:
    """How many labels are on screen (a clean shot should have none). Given the learned digits
    and the reference label's height, only boxes about a label's height whose characters all
    read as digits count, so the map's own magenta lights don't."""
    count = 0
    for box in _label_boxes(frame):
        if height and not 0.7 * height <= box[3] - box[1] <= 1.4 * height:
            continue
        if digits:
            reads = [_read(g, digits) for g in _glyphs(frame, box)]
            # Real labels read at 0.8 to 1; thin pinkish map features can pass for a weak "1".
            if not reads or min(score for _, score in reads) < 0.85:
                continue
        count += 1
    return count
