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
  <id>.png          the full image (with transparency where the map lets the sky through,
                    when each tile was shot over a white and a black skybox)
  <id>-preview.jpg  a 2048 px wide preview
  <id>.geo.json     scale and origin, to convert map cells to image pixels (for replay overlays)
                    (a map of several arenas, e.g. Punisher Arena: <id>-<area>.png etc., one per arena)
  <id>-tiles/       with --tiles: a Google Maps style pyramid ({z}/{y}/{x}.jpg, 256 px)
"""

import argparse
import functools
import json
import os
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
    # A screenshot that is entirely black is a failed grab, whatever its marker fit says; it
    # would be pasted as a black box.
    black = [t["index"] for t in present if (g := small(t["index"])) is not None and g.max() < 12]
    if black:
        log(f"  skipped {len(black)} all-black screenshots: {black}")
        present = [t for t in present if t["index"] not in black]
        tiles = [t for t in tiles if t["index"] not in black]
        by_pos = {(t["row"], t["col"]): t for t in tiles}
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
    # Dropping matches can cut screenshots loose (no anchor, no match left); the solve leaves
    # those at (0, 0), so keep only what is still connected to the anchors (or the pinned tile).
    reach = {ANCHOR} if anchors else {solved_ids[0]}
    reach |= set(anchors)
    grew = True
    while grew:
        grew = False
        for a, b, *_ in kept:
            if (a in reach) != (b in reach):
                reach |= {a, b}
                grew = True
    placed = {i: pos[col_of[i]] for i in solved_ids if i in reach}  # top-left of each screenshot
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

    # 4. Compose (see compose below). The canvas covers every placed screenshot.
    min_xy = np.min([p for p in placed.values()], axis=0)
    shift = -np.floor(min_xy)
    for i in placed:
        placed[i] = placed[i] + shift
    width = int(np.ceil(max(p[0] for p in placed.values()) + screen_w))
    height = int(np.ceil(max(p[1] for p in placed.values()) + screen_h))
    centres = {i: placed[i] + (screen_w / 2, screen_h / 2) for i in placed}
    mid = np.mean(list(centres.values()), axis=0)

    # Transparency by difference matting: each tile shot over a white and a black skybox
    # (capture.py, sky.mjs). A pixel that is sky in both differs by the whole white level; one
    # that is all map is the same in both; in between (soft edges, glass, glow) the difference
    # is exactly the see-through share. The white level is measured, not assumed (the game
    # renders the white skybox at about 230), from pixels that are black in the black shot.
    matting = bool(manifest.get("sky")) and any((tiles_dir / f"tile_{t['index']:04d}-black.png").exists() for t in present)
    white_level = [None]

    def matte(white: np.ndarray, black: np.ndarray) -> np.ndarray:
        """RGBA from the two shots: colour from the black shot (the map's own light, nothing of
        the sky in it), un-premultiplied; alpha from the difference."""
        w = white.astype(np.float32)
        b = black.astype(np.float32)
        d = w - b
        if white_level[0] is None:
            sky = (b.max(axis=2) <= 4) & (w.min(axis=2) >= 120) & (w.max(axis=2) - w.min(axis=2) <= 8)
            if sky.sum() >= 2000:
                white_level[0] = float(np.median(w[sky].mean(axis=1)))
                log(f"  white skybox level {white_level[0]:.1f}")
        level = white_level[0] or 230.0
        see_through = np.clip(d.mean(axis=2) / level, 0, 1)
        # Not grey: something changed between the shots (an animated glow), no matte there.
        animated = (d.max(axis=2) - d.min(axis=2)) > 24
        alpha = np.where(animated, 1.0, 1.0 - see_through)
        alpha = np.where(d.mean(axis=2) < -8, 1.0, alpha)
        rgb = np.where(alpha[:, :, None] > 1 / 255, np.clip(b / np.maximum(alpha, 1 / 255)[:, :, None], 0, 255), 0)
        return np.dstack([rgb, alpha * 255]).round().astype(np.uint8)

    def load(i: int) -> np.ndarray:
        white = np.asarray(Image.open(tiles_dir / f"tile_{i:04d}.png").convert("RGB"))
        if not matting:
            return white
        black_path = tiles_dir / f"tile_{i:04d}-black.png"
        if not black_path.exists():
            return np.dstack([white, np.full(white.shape[:2], 255, dtype=np.uint8)])
        return matte(white, np.asarray(Image.open(black_path).convert("RGB")))

    channels = 4 if matting else 3

    # The page: with markers, only the middle of each screenshot may be used, like a print page
    # with bleed; the markers sit in the bleed, so nothing outside the page ever reaches the
    # output. Without markers the whole screenshot is the page.
    share = manifest.get("pageShare")
    page_x0 = int(round(screen_w / 2 - share * screen_w)) if share else 0
    page_x1 = int(round(screen_w / 2 + share * screen_w)) if share else screen_w
    page_y0 = int(round(screen_h / 2 - share * screen_h)) if share else 0
    page_y1 = int(round(screen_h / 2 + share * screen_h)) if share else screen_h

    # Seams between neighbouring screenshots. Not the halfway line between their centres: the
    # path through their overlap where the two agree best (least difference, dynamic
    # programming at match scale). Anything off the ground plane (a floating island below the
    # arena, a tall tower) sits at a different place in each screenshot, parallax the marker
    # fit can't remove; a halfway cut through it shows a step, a routed seam goes around it.
    SEAM_STEP = 2  # the seam may move this many (reduced) pixels sideways per pixel along

    def route_seam(cost: np.ndarray) -> np.ndarray:
        """Least-cost top-to-bottom path through `cost` (rows along the seam, columns across):
        the column for each row."""
        rows, cols = cost.shape
        total = cost.copy()
        back = np.zeros((rows, cols), dtype=np.int16)
        offsets = range(-SEAM_STEP, SEAM_STEP + 1)
        for r in range(1, rows):
            best = np.full(cols, np.inf, dtype=np.float32)
            for d in offsets:
                shifted = np.full(cols, np.inf, dtype=np.float32)
                if d >= 0:
                    shifted[d:] = total[r - 1, : cols - d] if d else total[r - 1]
                else:
                    shifted[:d] = total[r - 1, -d:]
                better = shifted < best
                best[better] = shifted[better]
                back[r][better] = d
            total[r] = cost[r] + best
        path = np.zeros(rows, dtype=np.int32)
        path[-1] = int(np.argmin(total[-1]))
        for r in range(rows - 1, 0, -1):
            path[r - 1] = path[r] - back[r, path[r]]
        return path

    def overlap_cost(i: int, j: int, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
        """|difference| of screenshots i and j over the canvas rectangle, at match scale."""
        a, b = small(i), small(j)
        pi, pj = placed[i], placed[j]
        ys = (np.arange(y0, y1, MATCH_SCALE) + MATCH_SCALE / 2)
        xs = (np.arange(x0, x1, MATCH_SCALE) + MATCH_SCALE / 2)

        def sample(img: np.ndarray, p: np.ndarray) -> np.ndarray:
            r = np.clip(((ys - p[1]) / MATCH_SCALE).astype(int), 0, img.shape[0] - 1)
            c = np.clip(((xs - p[0]) / MATCH_SCALE).astype(int), 0, img.shape[1] - 1)
            return img[np.ix_(r, c)]

        diff = np.abs(sample(a, pi) - sample(b, pj))
        # Smoothed a little, so the seam prefers quiet areas over lucky single pixels.
        k = 3
        pad = np.pad(diff, k // 2, mode="edge")
        out = np.zeros_like(diff)
        for dy in range(k):
            for dx in range(k):
                out += pad[dy : dy + diff.shape[0], dx : dx + diff.shape[1]]
        return out / (k * k)

    # seams[(i, j)]: between i and its right neighbour j, the seam x for each canvas row
    # (a 'v' seam); between i and its lower neighbour j, the seam y for each canvas column.
    seams: dict[tuple[int, int], tuple[str, int, np.ndarray]] = {}
    for i in matched:
        t = by_index[i]
        for (dr, dc), kind in (((0, 1), "v"), ((1, 0), "h")):
            n = by_pos.get((t["row"] + dr, t["col"] + dc))
            if n is None or n["index"] not in matched:
                continue
            j = n["index"]
            xi, yi = placed[i]
            xj, yj = placed[j]
            # Their overlap on the canvas, within both pages, a strip from each edge left out
            # so the seam never runs along the very edge of a screenshot.
            edge = 2 * FEATHER
            ox0 = int(max(xi + page_x0, xj + page_x0) + edge)
            ox1 = int(min(xi + page_x1, xj + page_x1) - edge)
            oy0 = int(max(yi + page_y0, yj + page_y0) + edge)
            oy1 = int(min(yi + page_y1, yj + page_y1) - edge)
            if ox1 - ox0 < 4 * MATCH_SCALE or oy1 - oy0 < 4 * MATCH_SCALE:
                continue
            cost = overlap_cost(i, j, ox0, oy0, ox1, oy1)
            if kind == "v":
                path = route_seam(cost)  # rows = canvas rows, columns across the overlap
                seams[(i, j)] = ("v", oy0, ox0 + path * MATCH_SCALE + MATCH_SCALE / 2)
            else:
                path = route_seam(cost.T)  # rows = canvas columns
                seams[(i, j)] = ("h", ox0, oy0 + path * MATCH_SCALE + MATCH_SCALE / 2)

    def seam_at(seam: tuple[str, int, np.ndarray], along: np.ndarray) -> np.ndarray:
        """The seam's crossing coordinate at each canvas row (a 'v' seam) or column ('h'),
        the ends extended straight on."""
        _, start, path = seam
        idx = np.clip(((along - start) / MATCH_SCALE).astype(int), 0, len(path) - 1)
        return path[idx]

    log(f"  seams routed between {len(seams)} pairs of neighbours")
    if os.environ.get("HRS_STITCH_DEBUG"):
        (base / "seams.json").write_text(json.dumps(
            {f"{i}-{j}": {"kind": k, "start": st, "path": p.tolist(), "placed": [placed[i].tolist(), placed[j].tolist()]}
             for (i, j), (k, st, p) in seams.items()}))

    def compose(subset: set[int]) -> np.ndarray:
        """The canvas painted from these screenshots only. Full frames go down first (estimated
        ones, then matched ones outermost first), so there are no gaps; then each matched
        screenshot's own region, bounded by its seams, goes on top, blended over FEATHER
        pixels either side of each seam so small lighting differences don't show as steps."""
        canvas = np.zeros((height, width, channels), dtype=np.uint8)

        def paste(img: np.ndarray, x: int, y: int) -> None:
            h, w = img.shape[:2]
            canvas[y : y + h, x : x + w] = img

        estimated = [i for i in placed if i not in matched and i in subset]
        chosen = [i for i in matched if i in subset]
        for i in estimated + sorted(chosen, key=lambda i: -np.hypot(*(centres[i] - mid))):
            x, y = placed[i]
            paste(load(i)[page_y0:page_y1, page_x0:page_x1], int(round(x)) + page_x0, int(round(y)) + page_y0)

        for i in chosen:
            t = by_index[i]
            px, py = int(round(placed[i][0])), int(round(placed[i][1]))
            # The region this screenshot supplies: its page, less what lies beyond a seam with a
            # neighbour (feathered over FEATHER px either side of the seam).
            x0, x1, y0, y1 = page_x0, page_x1, page_y0, page_y1
            gx = np.arange(x0, x1, dtype=np.float32)[None, :] + px  # canvas coordinates
            gy = np.arange(y0, y1, dtype=np.float32)[:, None] + py
            alpha = np.ones((y1 - y0, x1 - x0), dtype=np.float32)
            for (dr, dc), mine in (((0, 1), True), ((0, -1), False), ((1, 0), True), ((-1, 0), False)):
                n = by_pos.get((t["row"] + dr, t["col"] + dc))
                if n is None:
                    continue
                key = (i, n["index"]) if mine else (n["index"], i)
                seam = seams.get(key)
                if seam is None:
                    if n["index"] in matched:
                        # Matched neighbour, no seam routed (overlap too small): the halfway line.
                        m = (centres[i] + centres[n["index"]]) / 2
                        if dc:
                            edge_x = m[0]
                            alpha *= np.clip((edge_x - gx) / (2 * FEATHER) + 0.5, 0, 1) if dc > 0 else np.clip((gx - edge_x) / (2 * FEATHER) + 0.5, 0, 1)
                        else:
                            edge_y = m[1]
                            alpha *= np.clip((edge_y - gy) / (2 * FEATHER) + 0.5, 0, 1) if dr > 0 else np.clip((gy - edge_y) / (2 * FEATHER) + 0.5, 0, 1)
                    continue
                if seam[0] == "v":
                    sx = seam_at(seam, gy[:, 0])[:, None]  # seam x per row
                    alpha *= np.clip((sx - gx) / (2 * FEATHER) + 0.5, 0, 1) if dc > 0 else np.clip((gx - sx) / (2 * FEATHER) + 0.5, 0, 1)
                else:
                    sy = seam_at(seam, gx[0, :])[None, :]  # seam y per column
                    alpha *= np.clip((sy - gy) / (2 * FEATHER) + 0.5, 0, 1) if dr > 0 else np.clip((gy - sy) / (2 * FEATHER) + 0.5, 0, 1)
            # Only the part of the page where this screenshot has any say.
            rows_on = np.nonzero(alpha.max(axis=1) > 0)[0]
            cols_on = np.nonzero(alpha.max(axis=0) > 0)[0]
            if not len(rows_on) or not len(cols_on):
                continue
            ry0, ry1 = rows_on[0], rows_on[-1] + 1
            cx0, cx1 = cols_on[0], cols_on[-1] + 1
            region = canvas[py + y0 + ry0 : py + y0 + ry1, px + x0 + cx0 : px + x0 + cx1]
            tile = load(i)[y0 + ry0 : y0 + ry1, x0 + cx0 : x0 + cx1].astype(np.float32)
            a = alpha[ry0:ry1, cx0:cx1, None]
            region[:] = (tile * a + region.astype(np.float32) * (1 - a) + 0.5).astype(np.uint8)
        return canvas

    ax, ay = ax + shift[0], ay + shift[1]

    # 5. Output: the image cropped to the camera bounds plus a margin (the screenshots reach
    #    half a screen further out: sky, or on a map of several arenas the next arena's
    #    stands). Several arenas: one image each, <id>-<area>.png. They sit close together and
    #    their stands interleave, so the full margin is kept towards a neighbour too (its
    #    stands' tips at the edge) rather than cutting into this arena's own.
    margin = manifest.get("cropMargin", 12)
    outputs = [(a["name"], a["bounds"]) for a in manifest.get("areas") or []] or [(None, manifest["cameraBounds"])]
    for name, bounds in outputs:
        x0 = max(0, int(np.floor(scale * (bounds["left"] - margin) + ax)))
        x1 = min(width, int(np.ceil(scale * (bounds["right"] + margin) + ax)))
        y0 = max(0, int(np.floor(-scale * (bounds["top"] + margin) + ay)))
        y1 = min(height, int(np.ceil(-scale * (bounds["bottom"] - margin) + ay)))
        if x1 - x0 < 16 or y1 - y0 < 16:
            log(f"  {name or manifest['id']}: nothing placed inside its bounds; skipped")
            continue
        out_id = f"{manifest['id']}-{name.lower()}" if name else manifest["id"]
        # Each arena's image from its own screenshots only: the next arena's rows overlap
        # this one's edge, from other camera positions (parallax), and the two areas have no
        # seams between them.
        area_no = outputs.index((name, bounds)) if name else None
        subset = {i for i in placed if area_no is None or by_index[i].get("area", 0) == area_no}
        canvas = pyvips.Image.new_from_array(compose(subset), interpretation="srgb")
        image = canvas.crop(x0, y0, x1 - x0, y1 - y0)
        out_png = base.parent / f"{out_id}.png"
        image.write_to_file(str(out_png), compression=6)
        preview = image.flatten(background=[48, 48, 48]) if matting else image
        preview.thumbnail_image(2048).write_to_file(str(base.parent / f"{out_id}-preview.jpg"), Q=88)
        geo = {
            "map": manifest["map"],
            "area": name,
            "image": out_png.name,
            "width": x1 - x0,
            "height": y1 - y0,
            "pxPerCell": scale,
            "originCell": {"x": -(ax - x0) / scale, "y": (ay - y0) / scale},
            "toPixel": "px = (x - originCell.x) * pxPerCell; py = (originCell.y - y) * pxPerCell",
        }
        (base.parent / f"{out_id}.geo.json").write_text(json.dumps(geo, indent=2))
        log(f"{x1 - x0}x{y1 - y0} px -> {out_png}")

        if args.tiles:
            pyramid = base.parent / f"{out_id}-tiles"
            image.dzsave(str(pyramid), layout="google", suffix=".png" if matting else ".jpg[Q=90]", tile_size=256)
            log(f"tile pyramid -> {pyramid}")


if __name__ == "__main__":
    main()
