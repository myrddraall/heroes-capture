"""Stitch captured screenshots into one top-down image of the battleground.

    python stitch.py work/towers-of-doom-structures.json [options]

Screenshots are placed by what they show, not by where the camera was asked to go: the game
may hold the camera back near the map edges, and its zoom may differ from the plan. Like
panorama software, every screenshot is matched against its right and lower neighbours to
measure their real offset, and all positions are then solved together (least squares, with
matches that disagree dropped). Black and featureless screenshots are skipped. Each pixel of
the output comes from the screenshot whose centre is nearest, which is the least distorted.

The map-to-pixel conversion (for replay overlays) is fitted afterwards from the screenshots
whose camera went where it was sent.

Writes, next to the tiles folder:
  <id>.png          the full image
  <id>-preview.jpg  a 2048 px wide preview
  <id>.geo.json     scale and origin, to convert map cells to image pixels (for replay overlays)
  <id>-tiles/       with --tiles: a Google Maps style pyramid ({z}/{y}/{x}.jpg, 256 px)
"""

import argparse
import functools
import json
from pathlib import Path

import numpy as np
import pyvips
from PIL import Image

MATCH_SCALE = 2  # screenshots are matched at half size: fast, and still ~0.2 px precise
FEATHER = 16  # pixels blended either side of each seam: enough to hide lighting steps, narrow enough not to double leaning objects


def phase_correlate(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """(dy, dx, strength) with b(y, x) ≈ a(y + dy, x + dx), to a fraction of a pixel;
    strength near 1 is a clean match."""
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1])).astype(np.float32)
    fa = np.fft.rfft2((a - a.mean()) * win)
    fb = np.fft.rfft2((b - b.mean()) * win)
    cross = fb * np.conj(fa)
    cross /= np.abs(cross) + 1e-9
    corr = np.fft.irfft2(cross, s=a.shape)
    y, x = np.unravel_index(int(np.argmax(corr)), corr.shape)

    def refine(before: float, peak: float, after: float) -> float:
        # Vertex of the parabola through the peak and its neighbours.
        denom = before - 2 * peak + after
        return 0.5 * (before - after) / denom if denom else 0.0

    h, w = corr.shape
    fy = y + refine(corr[(y - 1) % h, x], corr[y, x], corr[(y + 1) % h, x])
    fx = x + refine(corr[y, (x - 1) % w], corr[y, x], corr[y, (x + 1) % w])
    dy = fy - h if fy > h / 2 else fy
    dx = fx - w if fx > w / 2 else fx
    return -float(dy), -float(dx), float(corr.max())


def unwrap(shift: float, size: float, expected: float) -> float:
    """Correlation can't tell a shift from shift ± size; take the one nearest the plan."""
    return min((shift - size, shift, shift + size), key=lambda s: abs(s - expected))


_LOG_PATH = None


def log(*parts, **kw) -> None:
    """print, and append to the run's log file once it is known."""
    text = " ".join(str(p) for p in parts)
    print(text, **kw)
    if _LOG_PATH is not None:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--tiles", action="store_true", help="also write a Google Maps style tile pyramid")
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text())
    base = args.manifest.parent / manifest["id"]
    global _LOG_PATH
    _LOG_PATH = base / "log.txt"
    log(f"stitch {manifest['id']}")
    tiles_dir = base / "tiles"
    tiles = manifest["tiles"]
    rows, cols = manifest["rows"], manifest["cols"]
    by_pos = {(t["row"], t["col"]): t for t in tiles}

    def tile_path(t: dict) -> Path:
        return tiles_dir / f"tile_{t['index']:04d}.png"

    @functools.lru_cache(maxsize=2 * cols + 4)
    def small(index: int) -> np.ndarray | None:
        path = tiles_dir / f"tile_{index:04d}.png"
        if not path.exists():
            return None
        img = Image.open(path).convert("L")
        return np.asarray(img.reduce(MATCH_SCALE), dtype=np.float32)

    present = [t for t in tiles if tile_path(t).exists()]
    if not present:
        raise SystemExit(f"no screenshots in {tiles_dir}; run capture.py first")
    screen_w, screen_h = Image.open(tile_path(present[0])).size

    # Usable screenshots: not a failed black grab, not featureless void.
    usable = set()
    for t in present:
        g = small(t["index"])
        if g is not None and g.std() >= 3:
            usable.add(t["index"])

    # Marker fits from capture.py (markers.py): for each tile, where its screen centre really
    # was in map cells from the planned target, and its scale. A good fit anchors the tile
    # absolutely; tiles without one are placed relative to their neighbours by image matching.
    planned = manifest["pxPerCell"]
    by_index = {t["index"]: t for t in tiles}
    markers_path = base / "markers.json"
    fits = json.loads(markers_path.read_text()) if markers_path.exists() else {}
    anchored: dict[int, tuple[float, float]] = {}  # tile -> its screen centre in map cells
    scales = []
    for key, fit in fits.items():
        i = int(key)
        # Numbered markers are identified by their digits, so three suffice; the pattern
        # fallback needs five to be sure which marker is which.
        enough = 3 if fit and fit.get("ids") else 5
        if fit and fit.get("fit") and fit["found"] >= enough and fit["residual"] <= 3.0 and i in by_index and tile_path(by_index[i]).exists():
            t = by_index[i]
            anchored[i] = (t["x"] + fit["centreOffsetCells"][0], t["y"] + fit["centreOffsetCells"][1])
            scales.append((fit["scale"][0] + fit["scale"][1]) / 2)
    anchor_scale = float(np.median(scales)) if scales else None
    if fits:
        log(f"markers: {len(anchored)} of {len(fits)} screenshots anchored" + (f", {anchor_scale:.3f} px/cell" if scales else ""))

    edges = []  # (a, b, dx, dy, weight)
    for t in tiles:
        for dr, dc in ((0, 1), (1, 0)):
            n = by_pos.get((t["row"] + dr, t["col"] + dc))
            if n is None or t["index"] not in usable or n["index"] not in usable:
                continue
            a, b = small(t["index"]), small(n["index"])
            h, w = a.shape
            exp_dx = (n["x"] - t["x"]) * planned / MATCH_SCALE
            exp_dy = (t["y"] - n["y"]) * planned / MATCH_SCALE
            dy, dx, strength = phase_correlate(a, b)
            dx, dy = unwrap(dx, w, exp_dx), unwrap(dy, h, exp_dy)
            # A real neighbour moves mostly along its own axis.
            along, across = (dx, dy) if dc else (dy, dx)
            size_along = w if dc else h
            # A zero shift is allowed: near the edges the game can hold the camera back so far
            # that a screenshot shows exactly what its neighbour does.
            if strength < 0.03 or along < -0.05 * size_along or along >= size_along or abs(across) > 0.25 * size_along:
                continue
            edges.append((t["index"], n["index"], dx * MATCH_SCALE, dy * MATCH_SCALE, strength))

    # 2. Solve positions over the largest connected group, dropping disagreeing matches.
    groups: dict[int, int] = {}

    def root(i: int) -> int:
        while groups.setdefault(i, i) != i:
            i = groups[i]
        return i

    ANCHOR = -1  # a virtual node: everything anchored by markers is connected to it
    for a, b, *_ in edges:
        groups[root(a)] = root(b)
    for i in anchored:
        groups[root(i)] = root(ANCHOR)
    nodes = {e[0] for e in edges} | {e[1] for e in edges} | set(anchored)
    counts: dict[int, int] = {}
    for i in nodes:
        counts[root(i)] = counts.get(root(i), 0) + 1
    if not counts:
        raise SystemExit("no neighbouring screenshots could be matched; are they all black?")
    main_root = root(ANCHOR) if anchored else max(counts, key=counts.get)
    solved_ids = sorted(i for i in nodes if root(i) == main_root)
    col_of = {i: k for k, i in enumerate(solved_ids)}
    kept = [e for e in edges if e[0] in col_of and e[1] in col_of]
    # Anchor positions: the tile's top-left in a frame of anchor_scale px per cell, x east and
    # y south, with the map origin at (0, 0). The centre is the measured point; the half-screen
    # offset uses the shared scale, so any difference from the tile's own scale is spread
    # evenly either side of the centre, where the kept part of the screenshot is.
    anchors = {
        i: (anchor_scale * cx - screen_w / 2, -anchor_scale * cy - screen_h / 2)
        for i, (cx, cy) in anchored.items()
    }
    ANCHOR_WEIGHT = 5.0

    pos = np.zeros((len(solved_ids), 2))
    for _ in range(5):
        # One row per match, one per anchor; without anchors, one pinning the first tile at (0, 0).
        extra = len(anchors) if anchors else 1
        m = np.zeros((len(kept) + extra, len(solved_ids)))
        rhs = np.zeros((len(kept) + extra, 2))
        for r, (a, b, dx, dy, wgt) in enumerate(kept):
            m[r, col_of[a]], m[r, col_of[b]] = -wgt, wgt
            rhs[r] = (dx * wgt, dy * wgt)
        if anchors:
            for r, (i, (ax_, ay_)) in enumerate(anchors.items(), start=len(kept)):
                m[r, col_of[i]] = ANCHOR_WEIGHT
                rhs[r] = (ax_ * ANCHOR_WEIGHT, ay_ * ANCHOR_WEIGHT)
        else:
            m[-1, 0] = 1.0
        pos = np.linalg.lstsq(m, rhs, rcond=None)[0]
        residual = [np.hypot(*(pos[col_of[b]] - pos[col_of[a]] - (dx, dy))) for a, b, dx, dy, _ in kept]
        worst = max(residual) if residual else 0
        if worst <= 3:
            break
        kept = [e for e, r in zip(kept, residual) if r <= max(3, np.median(residual) * 3)]
    placed = {i: pos[col_of[i]] for i in solved_ids}  # top-left of each screenshot
    log(f"placed {len(placed)} of {len(tiles)} screenshots from {len(kept)} matches ({len(edges)} measured)")
    unplaced = [t["index"] for t in present if t["index"] not in placed]
    if unplaced:
        log(f"  not placed (no markers, no match): {len(unplaced)} screenshots: {unplaced[:20]}{' ...' if len(unplaced) > 20 else ''}")

    # 3. Map cells -> pixels: fit on screenshots whose camera went where it was sent
    #    (outliers are the ones the game held back at the edges).
    #    Many edge screenshots can be off, too many to outvote, so the fit starts from the middle
    #    of the map, where the camera can't have been held back, and then takes in every
    #    screenshot that agrees with it to within a few pixels.
    ids = list(placed)
    area = manifest["area"]
    mx, my = (area["left"] + area["right"]) / 2, (area["bottom"] + area["top"]) / 2
    hx, hy = (area["right"] - area["left"]) / 2, (area["top"] - area["bottom"]) / 2

    def fit(sel: list[int]) -> tuple[float, float, float]:
        wx = np.array([by_index[i]["x"] for i in sel])
        wy = np.array([by_index[i]["y"] for i in sel])
        cx = np.array([placed[i][0] + screen_w / 2 for i in sel])
        cy = np.array([placed[i][1] + screen_h / 2 for i in sel])
        # cx = s·x + ax, cy = −s·y + ay, one shared scale.
        design = np.zeros((2 * len(sel), 3))
        design[: len(sel), 0], design[: len(sel), 1] = wx, 1
        design[len(sel) :, 0], design[len(sel) :, 2] = -wy, 1
        return tuple(np.linalg.lstsq(design, np.concatenate([cx, cy]), rcond=None)[0])

    def error(i: int, s: float, ax: float, ay: float) -> float:
        t = by_index[i]
        return float(np.hypot(s * t["x"] + ax - placed[i][0] - screen_w / 2, -s * t["y"] + ay - placed[i][1] - screen_h / 2))

    if anchors:
        # The anchors put the frame at anchor_scale px per cell with the map origin at (0, 0).
        scale, ax, ay = anchor_scale, 0.0, 0.0
        held_back = sum(
            1 for i, (cx, cy) in anchored.items() if np.hypot(cx - by_index[i]["x"], cy - by_index[i]["y"]) > 0.5
        )
        log(f"scale {scale:.3f} px/cell from the markers (planned {planned}); {held_back} screenshots had the camera held back")
    else:
        fit_ids = [i for i in ids if abs(by_index[i]["x"] - mx) <= hx / 2 and abs(by_index[i]["y"] - my) <= hy / 2]
        if len(fit_ids) < 3:
            fit_ids = ids
        for _ in range(3):
            scale, ax, ay = fit(fit_ids)
            agreeing = [i for i in ids if error(i, scale, ax, ay) <= 4.0]
            if len(agreeing) < 3 or agreeing == fit_ids:
                break
            fit_ids = agreeing
        scale, ax, ay = fit(fit_ids)
        held_back = len(ids) - len(fit_ids)
        log(f"scale {scale:.3f} px/cell (planned {planned}); {held_back} screenshots were off their planned spot")

    # Screenshots that couldn't be matched (mostly void) go where the fit says, if not black.
    # That is only an estimate (the camera may have been held back), so they are painted
    # underneath, showing only where no matched screenshot reaches.
    # With markers, a screenshot that has neither a marker fit nor a match is left out: it is
    # almost always a corner where the game held the camera far back, and the planned position
    # would put its content outside the map.
    matched = set(placed)
    if not anchors:
        for t in present:
            if t["index"] not in placed and small(t["index"]) is not None and small(t["index"]).max() >= 12:
                placed[t["index"]] = np.array([scale * t["x"] + ax - screen_w / 2, -scale * t["y"] + ay - screen_h / 2])

    # 4. Compose. Canvas covers every placed screenshot. Full frames go down first (estimated
    #    ones, then matched ones outermost first), so there are no gaps; then each matched
    #    screenshot's nearest-centre region goes on top, blended over FEATHER pixels either side
    #    of each seam so small lighting differences between screenshots don't show as steps.
    min_xy = np.min([p for p in placed.values()], axis=0)
    shift = -np.floor(min_xy)
    for i in placed:
        placed[i] = placed[i] + shift
    width = int(np.ceil(max(p[0] for p in placed.values()) + screen_w))
    height = int(np.ceil(max(p[1] for p in placed.values()) + screen_h))
    centres = {i: placed[i] + (screen_w / 2, screen_h / 2) for i in placed}
    mid = np.mean(list(centres.values()), axis=0)

    def load(i: int) -> np.ndarray:
        return np.asarray(Image.open(tiles_dir / f"tile_{i:04d}.png").convert("RGB"))

    # The page: with markers, only the middle of each screenshot may be used, like a print page
    # with bleed; the markers sit in the bleed, so nothing outside the page ever reaches the
    # output. Without markers the whole screenshot is the page.
    share = manifest.get("pageShare")
    page_x0 = int(round(screen_w / 2 - share * screen_w)) if share else 0
    page_x1 = int(round(screen_w / 2 + share * screen_w)) if share else screen_w
    page_y0 = int(round(screen_h / 2 - share * screen_h)) if share else 0
    page_y1 = int(round(screen_h / 2 + share * screen_h)) if share else screen_h

    canvas = np.zeros((height, width, 3), dtype=np.uint8)

    def paste(img: np.ndarray, x: int, y: int) -> None:
        h, w = img.shape[:2]
        canvas[y : y + h, x : x + w] = img

    estimated = [i for i in placed if i not in matched]
    for i in estimated + sorted(matched, key=lambda i: -np.hypot(*(centres[i] - mid))):
        x, y = placed[i]
        paste(load(i)[page_y0:page_y1, page_x0:page_x1], int(round(x)) + page_x0, int(round(y)) + page_y0)

    def ramp(coord: np.ndarray, start: float, end: float) -> np.ndarray:
        """0 at `start`, 1 at `end`, clamped (start > end ramps the other way)."""
        return np.clip((coord - start) / (end - start), 0, 1)

    for i in matched:
        t = by_index[i]
        c = centres[i]
        px, py = int(round(placed[i][0])), int(round(placed[i][1]))
        # Seams (nearest-centre boundaries) with each matched neighbour, in this screenshot's pixels.
        seams = {"left": None, "right": None, "top": None, "bottom": None}
        for (dr, dc), side in (((0, -1), "left"), ((0, 1), "right"), ((-1, 0), "top"), ((1, 0), "bottom")):
            n = by_pos.get((t["row"] + dr, t["col"] + dc))
            if n is not None and n["index"] in matched:
                m = (c + centres[n["index"]]) / 2
                seams[side] = m[0] - px if side in ("left", "right") else m[1] - py
        # The region this screenshot supplies: reaching FEATHER past each seam, never past the page.
        x0 = page_x0 if seams["left"] is None else max(page_x0, int(seams["left"] - FEATHER))
        x1 = page_x1 if seams["right"] is None else min(page_x1, int(np.ceil(seams["right"] + FEATHER)))
        y0 = page_y0 if seams["top"] is None else max(page_y0, int(seams["top"] - FEATHER))
        y1 = page_y1 if seams["bottom"] is None else min(page_y1, int(np.ceil(seams["bottom"] + FEATHER)))
        if x1 <= x0 or y1 <= y0:
            continue
        gx = np.arange(x0, x1, dtype=np.float32)[None, :]
        gy = np.arange(y0, y1, dtype=np.float32)[:, None]
        alpha = np.ones((y1 - y0, x1 - x0), dtype=np.float32)
        if seams["left"] is not None:
            alpha = np.minimum(alpha, ramp(gx, seams["left"] - FEATHER, seams["left"] + FEATHER))
        if seams["right"] is not None:
            alpha = np.minimum(alpha, ramp(gx, seams["right"] + FEATHER, seams["right"] - FEATHER))
        if seams["top"] is not None:
            alpha = np.minimum(alpha, ramp(gy, seams["top"] - FEATHER, seams["top"] + FEATHER))
        if seams["bottom"] is not None:
            alpha = np.minimum(alpha, ramp(gy, seams["bottom"] + FEATHER, seams["bottom"] - FEATHER))
        region = canvas[py + y0 : py + y1, px + x0 : px + x1]
        tile = load(i)[y0:y1, x0:x1].astype(np.float32)
        a = alpha[:, :, None]
        region[:] = (tile * a + region.astype(np.float32) * (1 - a) + 0.5).astype(np.uint8)

    canvas = pyvips.Image.new_from_array(canvas, interpretation="srgb")

    out_png = base.with_suffix(".png")
    canvas.write_to_file(str(out_png), compression=6)
    canvas.thumbnail_image(2048).write_to_file(str(base.parent / f"{manifest['id']}-preview.jpg"), Q=88)

    ax, ay = ax + shift[0], ay + shift[1]
    geo = {
        "map": manifest["map"],
        "image": out_png.name,
        "width": width,
        "height": height,
        "pxPerCell": scale,
        "originCell": {"x": -ax / scale, "y": ay / scale},
        "toPixel": "px = (x - originCell.x) * pxPerCell; py = (originCell.y - y) * pxPerCell",
    }
    (base.parent / f"{manifest['id']}.geo.json").write_text(json.dumps(geo, indent=2))
    log(f"{width}x{height} px -> {out_png}")

    if args.tiles:
        pyramid = base.parent / f"{manifest['id']}-tiles"
        canvas.dzsave(str(pyramid), layout="google", suffix=".jpg[Q=90]", tile_size=256)
        log(f"tile pyramid -> {pyramid}")


if __name__ == "__main__":
    main()
