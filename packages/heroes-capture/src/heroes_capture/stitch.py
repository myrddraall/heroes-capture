"""Stitch captured screenshots into one top-down image of the battleground.

    heroes-capture stitch tmp/towers-of-doom-structures.json [--tiles] [--output-dir maps]   (heroes-capture map render runs it last)

Each screenshot is anchored where the map script reported its camera (positions.json, written
by capture.py), and every screenshot is also matched against its right and lower neighbours,
like panorama software, to measure their real offsets; the scale (pixels per map cell) comes
from those matches, and all positions are solved together (least squares, with matches that
disagree dropped). Black and featureless screenshots are skipped. Neighbours meet along a seam
routed through their overlap where they agree best, near the halfway line, blended over a few
pixels. Without positions.json, the screenshots are placed by the matches alone and the
map-to-pixel conversion is fitted from the middle of the map outward.

Writes into <output-dir>/<map id> (maps/towers-of-doom; the map id is the map's name in
lower case, words joined by hyphens):
  <id>.png          the full image, with transparency where the map lets the void through
                    (matted from each tile's shots over a white and a black skybox, or the
                    black-painted void made transparent)
  <id>-preview.jpg  a 2048 px wide preview (over dark grey)
  <id>-on-white.jpg the full image flattened over white (easier to look at than the alpha)
  <id>.geo.json     scale and origin, to convert map cells to image pixels (for replay overlays)
                    (a map of several arenas, e.g. Punisher Arena: <id>-<area>.png etc., one per arena)
  <id>-tiles/       with --tiles: a Google Maps style pyramid ({z}/{y}/{x}.png, 256 px)
  <id>-viewer/      a prototype viewer: index.html and the layers it shows (viewer.py)
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyvips
from PIL import Image

from . import sky_stitch
from . import viewer
from .inject import slug
from .frames import PNG_COMPRESSION, frame_exists, load_frame
from .matching import phase_correlate
from .workers import ordered_map
from .runlog import log, warn, log_timings, set_log_file, stage

MATCH_SCALE = 2  # screenshots are matched at half size: fast, and still ~0.2 px precise
FEATHER = 16  # pixels blended either side of each seam: enough to hide lighting steps, narrow enough not to double leaning objects
SEAM_STEP = 2  # a seam may move this many (reduced) pixels sideways per pixel along
ANCHOR_WEIGHT = 5.0  # an anchor's weight in the solve, against a match's (its strength, up to 1)
ANCHOR = -1  # a virtual node in the solve: every anchored screenshot is connected to it
MATTE_BAND = 32  # rows matted at a time (see Shots.matte)


# ------------------------------------------------------------------------------------------------
# Matching
# ------------------------------------------------------------------------------------------------


def unwrap(shift: float, size: float, expected: float) -> float:
    """Correlation can't tell a shift from shift ± size; take the one nearest the plan."""
    return min((shift - size, shift, shift + size), key=lambda s: abs(s - expected))


def _max3(a: np.ndarray) -> np.ndarray:
    """a.max(axis=2) for three channels, without numpy's slow reduction over a short last axis."""
    return np.maximum(np.maximum(a[:, :, 0], a[:, :, 1]), a[:, :, 2])


def _min3(a: np.ndarray) -> np.ndarray:
    return np.minimum(np.minimum(a[:, :, 0], a[:, :, 1]), a[:, :, 2])


def _mean3(a: np.ndarray) -> np.ndarray:
    """a.mean(axis=2) for three float channels, to the bit: the same sum in the same order and
    the same division (by a count, as numpy's mean does)."""
    total = a[:, :, 0] + a[:, :, 1]
    total += a[:, :, 2]
    return np.true_divide(total, np.intp(3), out=total, casting="unsafe")


class Shots:
    """The run's screenshots: half-size greyscale copies for matching (cached), and each tile as
    RGBA for composing (matted from its pair of shots when the map was shot over white and
    black)."""

    def __init__(self, manifest: dict, base: Path):
        self.dir = base / "tiles"
        # Only when this map is shot over white and black (manifest sky mode): a results folder
        # can hold -black shots left from an earlier run of a map since shot once per tile.
        self.matting = (manifest.get("sky") or {}).get("mode") == "matte"
        self.white_level: float | None = None
        self._small: dict[int, np.ndarray | None] = {}

    def stem(self, index: int) -> Path:
        return self.dir / f"tile_{index:04d}"

    def black_stem(self, index: int) -> Path:
        return self.dir / f"tile_{index:04d}-black"

    def exists(self, index: int) -> bool:
        return frame_exists(self.stem(index))

    def has_black(self, index: int) -> bool:
        return frame_exists(self.black_stem(index))

    def small(self, index: int) -> np.ndarray | None:
        """The screenshot in grey at half size, for matching. Every one is kept (as bytes, a
        sixth of the float copy): the seams come back to them in no useful order."""
        if index not in self._small:
            self._small[index] = np.asarray(Image.fromarray(load_frame(self.stem(index))).convert("L").reduce(MATCH_SCALE)) if self.exists(index) else None
        grey = self._small[index]
        return None if grey is None else grey.astype(np.float32)

    def levels_for(self, order: list[int]) -> dict[int, float]:
        """The white level each matted tile is matted with, in this order: measured on the first
        tile that shows enough sky (pixels black in the black shot), 230 for any before it. Done
        in order before the tiles are loaded in parallel, so the result doesn't depend on which
        thread finishes first."""
        levels = {}
        for i in order:
            if not self.matting or not self.has_black(i):
                continue
            if self.white_level is None:
                w = load_frame(self.stem(i)).astype(np.float32)
                b = load_frame(self.black_stem(i)).astype(np.float32)
                w_min = _min3(w)
                sky = (_max3(b) <= 4) & (w_min >= 120) & (_max3(w) - w_min <= 8)
                if sky.sum() >= 2000:
                    self.white_level = float(np.median(w[sky].mean(axis=1)))
                    log(f"  white skybox level {self.white_level:.1f}")
            levels[i] = self.white_level or 230.0
        return levels

    def matte(self, white: np.ndarray, black: np.ndarray, level: float) -> np.ndarray:
        """Difference matting: RGBA from the shots over white and over black. A pixel that is sky
        in both differs by the whole white level; one that is all map is the same in both; in
        between (soft edges, glass, glow) the difference is exactly the see-through share.
        Colour from the black shot (the map's own light, nothing of the sky in it),
        un-premultiplied. The white level is measured, not assumed (the game renders the white
        skybox at about 230): see levels_for."""
        # In bands of rows small enough to stay in the processor's cache: every pixel is worked
        # out on its own, and with whole tiles the threads spent their time waiting on memory.
        out = np.empty((*white.shape[:2], 4), dtype=np.uint8)
        for r0 in range(0, white.shape[0], MATTE_BAND):
            r1 = r0 + MATTE_BAND
            w = white[r0:r1].astype(np.float32)
            b = black[r0:r1].astype(np.float32)
            d = w - b
            d_mean = _mean3(d)
            see_through = np.clip(d_mean / level, 0, 1)
            # Not grey: something changed between the shots (an animated glow), no matte there.
            animated = (_max3(d) - _min3(d)) > 24
            alpha = np.where(animated, 1.0, 1.0 - see_through)
            alpha = np.where(d_mean < -8, 1.0, alpha)
            # Un-premultiplied colour: the black shot over alpha. Only where it is see-through:
            # elsewhere (most pixels) alpha is exactly 1 and the colour is the black shot as it is.
            rgb = b
            part = alpha < 1.0
            a_part = alpha[part]
            rgb[part] = np.where(a_part[:, None] > 1 / 255, np.clip(b[part] / np.maximum(a_part, 1 / 255)[:, None], 0, 255), 0)
            out[r0:r1, :, :3] = rgb.round()
            out[r0:r1, :, 3] = (alpha * 255).round()
        return out

    def load(self, i: int, level: float | None) -> np.ndarray:
        """The tile as RGBA: matted from its pair of shots with that white level, or opaque (a
        map whose void is black terrain is shot once, over black; void_terrain_transparent
        handles the void)."""
        white = load_frame(self.stem(i))
        if not self.matting or not self.has_black(i):
            return np.dstack([white, np.full(white.shape[:2], 255, dtype=np.uint8)])
        return self.matte(white, load_frame(self.black_stem(i)), level if level is not None else 230.0)


def read_anchors(base: Path, by_index: dict, shots: Shots, present: list) -> tuple[dict[int, tuple[float, float]], bool]:
    """Where each screenshot's centre really was, in map cells: the camera target the map script
    reported through its status strip for every tile (positions.json). Tiles without one are
    placed relative to their neighbours by image matching. Also whether positions.json exists."""
    anchored: dict[int, tuple[float, float]] = {}
    positions_path = base / "positions.json"
    if not positions_path.exists():
        return anchored, False
    for key, pos in json.loads(positions_path.read_text()).items():
        i = int(key)
        if i in by_index and shots.exists(i):
            anchored[i] = (pos["x"], pos["y"])
    log(f"camera positions: {len(anchored)} of {len(present)} screenshots anchored")
    return anchored, True


def measure_matches(tiles: list, by_pos: dict, usable: set, shots: Shots, centre, planned: float) -> list:
    """Every screenshot against its right and lower neighbours: (a, b, dx, dy, strength) for each
    pair that matched, the offset in full-size pixels."""
    pairs = []
    for t in tiles:
        for dr, dc in ((0, 1), (1, 0)):
            n = by_pos.get((t["row"] + dr, t["col"] + dc))
            if n is not None and t["index"] in usable and n["index"] in usable:
                pairs.append((t, n, dc))

    def match(pair):
        t, n, dc = pair
        a, b = shots.small(t["index"]), shots.small(n["index"])
        h, w = a.shape
        (tx, ty), (nx, ny) = centre(t), centre(n)
        exp_dx = (nx - tx) * planned / MATCH_SCALE
        exp_dy = (ty - ny) * planned / MATCH_SCALE
        dy, dx, strength = phase_correlate(a, b)
        dx, dy = unwrap(dx, w, exp_dx), unwrap(dy, h, exp_dy)
        # A real neighbour moves mostly along its own axis.
        along, across = (dx, dy) if dc else (dy, dx)
        size_along = w if dc else h
        # A zero shift is allowed: near the edges the game can hold the camera back so far
        # that a screenshot shows exactly what its neighbour does.
        if strength < 0.03 or along < -0.05 * size_along or along >= size_along or abs(across) > 0.25 * size_along:
            return None
        return (t["index"], n["index"], dx * MATCH_SCALE, dy * MATCH_SCALE, strength)

    return [edge for edge in ordered_map(match, pairs) if edge is not None]


def scale_from_matches(edges: list, anchored: dict, planned: float) -> float:
    """Pixels per map cell from the matches themselves: each matched pair's pixel offset over its
    camera offset, hundreds of them; the planned value if too few pairs moved enough to tell."""
    estimates = []
    for a, b, dx, dy, _ in edges:
        if a in anchored and b in anchored:
            ex, ey = anchored[b][0] - anchored[a][0], anchored[a][1] - anchored[b][1]
            if abs(ex) >= 4:
                estimates.append(dx / ex)
            if abs(ey) >= 4:
                estimates.append(dy / ey)
    scale = float(np.median(estimates)) if len(estimates) >= 6 else float(planned)
    log(f"  scale {scale:.3f} px/cell from {len(estimates)} matched offsets (planned {planned})")
    return scale


def solve_positions(edges: list, anchors: dict, anchored: dict) -> tuple[dict[int, np.ndarray], list]:
    """Every screenshot's top-left, solved over the largest connected group (or the one tied to
    the anchors) by least squares, dropping matches that disagree. Returns the positions of
    those still connected to the anchors (or the pinned first tile), and the matches kept."""
    groups: dict[int, int] = {}

    def root(i: int) -> int:
        while groups.setdefault(i, i) != i:
            i = groups[i]
        return i

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
    return {i: pos[col_of[i]] for i in solved_ids if i in reach}, kept


def fit_frame(manifest: dict, by_index: dict, placed: dict, screen_w: int, screen_h: int) -> tuple[float, float, float, int]:
    """Without anchors: map cells -> pixels (scale, ax, ay), fitted on screenshots whose camera
    went where it was sent (outliers are the ones the game held back at the edges). Many edge
    screenshots can be off, too many to outvote, so the fit starts from the middle of the map,
    where the camera can't have been held back, and then takes in every screenshot that agrees
    with it to within a few pixels. Also how many screenshots were off."""
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
    return scale, ax, ay, len(ids) - len(fit_ids)


# ------------------------------------------------------------------------------------------------
# Composing
# ------------------------------------------------------------------------------------------------


@dataclass
class Layout:
    """Where every screenshot goes on the canvas (top-left corners), and the part of each that
    may be used (the page: the status strip's column along the left edge is left out)."""

    placed: dict[int, np.ndarray]
    matched: set[int]  # placed by matches or anchors; the rest only estimated
    by_pos: dict
    by_index: dict
    screen_w: int
    screen_h: int
    page_x0: int
    width: int = 0
    height: int = 0
    centres: dict = field(default_factory=dict)
    mid: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.page_x1, self.page_y0, self.page_y1 = self.screen_w, 0, self.screen_h
        self.width = int(np.ceil(max(p[0] for p in self.placed.values()) + self.screen_w))
        self.height = int(np.ceil(max(p[1] for p in self.placed.values()) + self.screen_h))
        self.centres = {i: self.placed[i] + (self.screen_w / 2, self.screen_h / 2) for i in self.placed}
        self.mid = np.mean(list(self.centres.values()), axis=0)


def route_seam(cost: np.ndarray) -> np.ndarray:
    """Least-cost top-to-bottom path through `cost` (rows along the seam, columns across): the
    column for each row."""
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


def overlap_cost(shots: Shots, layout: Layout, i: int, j: int, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    """|difference| of screenshots i and j over the canvas rectangle, at match scale."""
    a, b = shots.small(i), shots.small(j)
    pi, pj = layout.placed[i], layout.placed[j]
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


def route_seams(shots: Shots, layout: Layout, px_per_cell: float) -> dict:
    """Seams between neighbouring screenshots. Not the halfway line between their centres: the
    path through their overlap where the two agree best (least difference, dynamic programming
    at match scale). Anything off the ground plane (a floating island below the arena, a tall
    tower) sits at a different place in each screenshot, parallax the positions can't remove; a
    halfway cut through it shows a step, a routed seam goes around it. Never far from the
    halfway line, though: the game lights and details the terrain around each screenshot's
    centre (the lighting refit reaches about 15 cells; further out a hole keeps its dark box and
    trim goes missing), so a seam that wandered to the far side of the overlap picked up the far
    screenshot's unlit edge.

    seams[(i, j)]: between i and its right neighbour j, the seam x for each canvas row (a 'v'
    seam); between i and its lower neighbour j, the seam y for each canvas column ('h')."""
    band = int(4 * px_per_cell)  # how far from the halfway line a seam may go, in pixels
    jobs = []  # (i, j, kind, ox0, oy0, ox1, oy1), in the order the seams were always routed
    for i in layout.matched:
        t = layout.by_index[i]
        for (dr, dc), kind in (((0, 1), "v"), ((1, 0), "h")):
            n = layout.by_pos.get((t["row"] + dr, t["col"] + dc))
            if n is None or n["index"] not in layout.matched:
                continue
            j = n["index"]
            xi, yi = layout.placed[i]
            xj, yj = layout.placed[j]
            # Their overlap on the canvas, within both pages, a strip from each edge left out so
            # the seam never runs along the very edge of a screenshot.
            edge = 2 * FEATHER
            ox0 = int(max(xi + layout.page_x0, xj + layout.page_x0) + edge)
            ox1 = int(min(xi + layout.page_x1, xj + layout.page_x1) - edge)
            oy0 = int(max(yi + layout.page_y0, yj + layout.page_y0) + edge)
            oy1 = int(min(yi + layout.page_y1, yj + layout.page_y1) - edge)
            mid = (layout.centres[i] + layout.centres[j]) / 2
            if kind == "v":
                ox0, ox1 = max(ox0, int(mid[0] - band)), min(ox1, int(mid[0] + band))
            else:
                oy0, oy1 = max(oy0, int(mid[1] - band)), min(oy1, int(mid[1] + band))
            if ox1 - ox0 < 4 * MATCH_SCALE or oy1 - oy0 < 4 * MATCH_SCALE:
                continue
            jobs.append((i, j, kind, ox0, oy0, ox1, oy1))

    def route(job):
        i, j, kind, ox0, oy0, ox1, oy1 = job
        cost = overlap_cost(shots, layout, i, j, ox0, oy0, ox1, oy1)
        if kind == "v":
            path = route_seam(cost)  # rows = canvas rows, columns across the overlap
            return ("v", oy0, ox0 + path * MATCH_SCALE + MATCH_SCALE / 2)
        path = route_seam(cost.T)  # rows = canvas columns
        return ("h", ox0, oy0 + path * MATCH_SCALE + MATCH_SCALE / 2)

    seams: dict[tuple[int, int], tuple[str, int, np.ndarray]] = {}
    for job, seam in zip(jobs, ordered_map(route, jobs)):
        seams[(job[0], job[1])] = seam
    return seams


def seam_at(seam: tuple[str, int, np.ndarray], along: np.ndarray) -> np.ndarray:
    """The seam's crossing coordinate at each canvas row (a 'v' seam) or column ('h'), the ends
    extended straight on."""
    _, start, path = seam
    idx = np.clip(((along - start) / MATCH_SCALE).astype(int), 0, len(path) - 1)
    return path[idx]


def supply_region(layout: Layout, seams: dict, i: int) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
    """The part of screenshot i's page it supplies, and how much (0..1 per pixel): its page,
    less what lies beyond a seam with a neighbour, feathered over FEATHER px either side of the
    seam. Returns the weights over their bounding box and the box (y0, y1, x0, x1) in page
    coordinates; None if it supplies nothing."""
    L = layout
    t = L.by_index[i]
    px, py = int(round(L.placed[i][0])), int(round(L.placed[i][1]))
    x0, x1, y0, y1 = L.page_x0, L.page_x1, L.page_y0, L.page_y1
    gx = np.arange(x0, x1, dtype=np.float32)[None, :] + px  # canvas coordinates
    gy = np.arange(y0, y1, dtype=np.float32)[:, None] + py

    def ramp(edge, coord, before: bool) -> np.ndarray:
        """1 on this screenshot's side of an edge, 0 beyond, over 2·FEATHER pixels."""
        return np.clip((edge - coord) / (2 * FEATHER) + 0.5, 0, 1) if before else np.clip((coord - edge) / (2 * FEATHER) + 0.5, 0, 1)

    alpha = np.ones((y1 - y0, x1 - x0), dtype=np.float32)
    for (dr, dc), mine in (((0, 1), True), ((0, -1), False), ((1, 0), True), ((-1, 0), False)):
        n = L.by_pos.get((t["row"] + dr, t["col"] + dc))
        if n is None:
            continue
        seam = seams.get((i, n["index"]) if mine else (n["index"], i))
        if seam is None:
            if n["index"] in L.matched:
                # Matched neighbour, no seam routed (overlap too small): the halfway line.
                m = (L.centres[i] + L.centres[n["index"]]) / 2
                alpha *= ramp(m[0], gx, dc > 0) if dc else ramp(m[1], gy, dr > 0)
            continue
        if seam[0] == "v":
            alpha *= ramp(seam_at(seam, gy[:, 0])[:, None], gx, dc > 0)  # seam x per row
        else:
            alpha *= ramp(seam_at(seam, gx[0, :])[None, :], gy, dr > 0)  # seam y per column
    rows_on = np.nonzero(alpha.max(axis=1) > 0)[0]
    cols_on = np.nonzero(alpha.max(axis=0) > 0)[0]
    if not len(rows_on) or not len(cols_on):
        return None
    ry0, ry1, cx0, cx1 = rows_on[0], rows_on[-1] + 1, cols_on[0], cols_on[-1] + 1
    return alpha[ry0:ry1, cx0:cx1], (y0 + ry0, y0 + ry1, x0 + cx0, x0 + cx1)


def compose(shots: Shots, layout: Layout, seams: dict, subset: set[int]) -> np.ndarray:
    """The canvas painted from these screenshots only. Full frames go down first (estimated ones,
    then matched ones outermost first), so there are no gaps; then each matched screenshot's own
    region, bounded by its seams, goes on top, blended over FEATHER pixels either side of each
    seam so small lighting differences don't show as steps. Each screenshot is loaded (and
    matted) once: its region is kept from the first pass for the second."""
    L = layout
    canvas = np.zeros((L.height, L.width, 4), dtype=np.uint8)

    estimated = [i for i in L.placed if i not in L.matched and i in subset]
    chosen = [i for i in L.matched if i in subset]
    boxes = {}
    for i, region in zip(chosen, ordered_map(lambda i: supply_region(L, seams, i), chosen)):
        if region is not None:
            boxes[i] = region[1]
    kept: dict[int, np.ndarray] = {}
    order = estimated + sorted(chosen, key=lambda i: -np.hypot(*(L.centres[i] - L.mid)))
    levels = shots.levels_for(order)
    for i, full in zip(order, ordered_map(lambda i: shots.load(i, levels.get(i)), order)):
        x, y = int(round(L.placed[i][0])) + L.page_x0, int(round(L.placed[i][1])) + L.page_y0
        img = full[L.page_y0 : L.page_y1, L.page_x0 : L.page_x1]
        canvas[y : y + img.shape[0], x : x + img.shape[1]] = img
        if i in boxes:
            y0, y1, x0, x1 = boxes[i]
            kept[i] = full[y0:y1, x0:x1].copy()

    # The regions' weights worked out on the thread pool; the blending stays in order (they overlap).
    blended = [i for i in chosen if i in boxes]
    for i, (alpha, (y0, y1, x0, x1)) in zip(blended, ordered_map(lambda i: supply_region(L, seams, i), blended)):
        px, py = int(round(L.placed[i][0])), int(round(L.placed[i][1]))
        region = canvas[py + y0 : py + y1, px + x0 : px + x1]
        tile = kept.pop(i).astype(np.float32)
        a = alpha[:, :, None]
        region[:] = (tile * a + region.astype(np.float32) * (1 - a) + 0.5).astype(np.uint8)
    return canvas


def _quarter(mask: np.ndarray) -> np.ndarray:
    """A boolean mask at quarter resolution: a cell is set if any of its 4x4 pixels is."""
    h, w = mask.shape
    padded = np.zeros((-(-h // 4) * 4, -(-w // 4) * 4), dtype=bool)
    padded[:h, :w] = mask
    return padded.reshape(padded.shape[0] // 4, 4, padded.shape[1] // 4, 4).any(axis=(1, 3))


def void_terrain_transparent(rgba: np.ndarray, px_per_cell: float) -> np.ndarray:
    """Some maps (Dragon Shire, Towers of Doom, the lower half of Hanamura) draw their void as
    terrain painted black rather than leaving it to the sky, so the matte keeps it. Near-black
    that reaches the image border is that void (a dark spot inside the map doesn't), and so is a
    large near-black area that touches what the matte already made transparent. Only next to
    this painted void are the edge corrections below applied: where the matte found the sky, its
    own transparency is already right."""
    from scipy import ndimage

    rgb, alpha = rgba[:, :, :3], rgba[:, :, 3]
    peak = _max3(rgb)
    sky = alpha < 8  # what the matte made transparent
    dark = (peak <= 20) & ~sky
    labels, count = ndimage.label(dark)
    if not count:
        return rgba
    border = np.zeros(count + 1, dtype=bool)
    for edge in (labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]):
        border[np.unique(edge)] = True
    touches_sky = np.zeros(count + 1, dtype=bool)
    touches_sky[np.unique(labels[ndimage.binary_dilation(sky, iterations=1) & dark])] = True
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    big = sizes >= (4 * px_per_cell) ** 2  # at least 4 x 4 cells
    is_void = border | (touches_sky & big)
    is_void[0] = False
    void = is_void[labels]
    if not void.any():
        return rgba
    # Void seen through gaps in foliage at the edge is black too but cut off from the outside
    # by the leaves: any dark pixel within ~40 px of the void counts as void.
    h, w = void.shape
    around = np.repeat(np.repeat(ndimage.binary_dilation(_quarter(void), iterations=10), 4, axis=0), 4, axis=1)[:h, :w]
    void |= dark & around
    coarse = _quarter(void)  # the void at quarter resolution, for the distances below
    # Distances to the sky, likewise: a pixel nearer the sky than the painted void is left to
    # the matte.
    coarse_sky = _quarter(sky)
    dist_sky = ndimage.zoom(ndimage.distance_transform_edt(~coarse_sky) * 4.0, 4, order=1)[:h, :w] if coarse_sky.any() else np.full((h, w), np.inf, dtype=np.float32)
    out = rgba.copy()
    out[void, 3] = 0
    # The game blends the void's black into the ground over about a cell from the edge (a
    # texture blend, the same along the whole boundary), so the brightness there is a function
    # of the distance to the void. Measured, not assumed: the median brightness at each distance
    # against the median further in gives the blend factor k(d); the pixels' transparency is
    # k(d) and their colour C / k(d). Objects standing in the band are lifted with it, which
    # over black composites back to what was rendered.
    band = int(round(1.25 * px_per_cell))
    dist = ndimage.zoom(ndimage.distance_transform_edt(~coarse) * 4.0, 4, order=1)[:h, :w]
    in_band = (dist > 0) & (dist <= band) & ~void & (alpha >= 8) & (dist < dist_sky)
    beyond = (dist > band) & (dist <= 2 * band) & (alpha >= 8)
    if in_band.any() and beyond.sum() > 1000:
        luma = rgb.mean(axis=2)
        reference = float(np.median(luma[beyond]))
        d_band = dist[in_band]
        l_band = luma[in_band]
        bins = np.clip((d_band / 2).astype(int), 0, band // 2)  # 2 px steps
        k = np.ones(band // 2 + 1, dtype=np.float32)
        for i in range(band // 2 + 1):
            sel = bins == i
            if sel.sum() > 200:
                k[i] = min(1.0, float(np.median(l_band[sel])) / max(reference, 1.0))
        k = np.maximum.accumulate(k)  # the blend only gets lighter further in
        if k[0] < 0.6:  # a real ramp; otherwise there is nothing to correct
            k_px = np.clip(k[bins], 0.02, 1.0)
            ys, xs = np.nonzero(in_band)
            out[ys, xs, :3] = np.clip(rgb[ys, xs].astype(np.float32) / k_px[:, None], 0, 255).astype(np.uint8)
            out[ys, xs, 3] = (k_px * 255).astype(np.uint8)
            log(f"  edge blend corrected over {band} px: k from {k[0]:.2f} at the void to 1.0")
    # The game draws the terrain's edge against the void without anti-aliasing (a hard, stepped
    # line). In a narrow band along the edge, colour (premultiplied) and alpha are blurred
    # together and the colour un-premultiplied again: the steps smooth out along the edge and
    # the crossing softens by about a pixel.
    rim = ndimage.binary_dilation(void, iterations=3) & ~ndimage.binary_erosion(void, iterations=3) & (dist < dist_sky)
    a_f = out[:, :, 3].astype(np.float32) / 255.0
    a_soft = ndimage.gaussian_filter(a_f, sigma=1.5)
    for ch in range(3):
        pm = ndimage.gaussian_filter(out[:, :, ch].astype(np.float32) * a_f, sigma=1.5)
        out[rim, ch] = np.clip(pm[rim] / np.maximum(a_soft[rim], 1 / 255), 0, 255).astype(np.uint8)
    out[rim, 3] = (a_soft[rim] * 255).round().astype(np.uint8)
    log(f"  void terrain made transparent: {void.mean() * 100:.1f}% of the image")
    return out


# ------------------------------------------------------------------------------------------------
# Output
# ------------------------------------------------------------------------------------------------


def other_areas_removed(rgba: np.ndarray, areas: list, own: int, scale: float, ax: float, ay: float) -> tuple[np.ndarray, int]:
    """On a map of several arenas: an arena's image without the edges of the arenas next to it,
    which its screenshots see across the void between them. Each separate solid piece of the
    image (void all round it) belongs to the arena whose bounds it is nearest; the pieces nearer
    another arena are made transparent (with a few pixels round them, their soft edges). The
    arena's own floating pieces stay, as they are nearest it, and so does anything touching the
    arena itself. Image pixel (x, y) is map cell ((x - ax) / scale, (ay - y) / scale). Returns
    the image and how many pieces went."""
    from scipy import ndimage

    labels, count = ndimage.label(rgba[:, :, 3] >= 16, structure=np.ones((3, 3), dtype=bool))
    if count < 2:
        return rgba, 0

    def gap(piece: tuple[slice, slice], bounds: dict) -> float:
        """Map cells between a piece's bounding box and an area's bounds (0 where they overlap)."""
        rows, cols = piece
        left, right = (cols.start - ax) / scale, (cols.stop - ax) / scale
        top, bottom = (ay - rows.start) / scale, (ay - rows.stop) / scale
        dx = max(bounds["left"] - right, left - bounds["right"], 0.0)
        dy = max(bounds["bottom"] - top, bottom - bounds["top"], 0.0)
        return float(np.hypot(dx, dy))

    pieces = ndimage.find_objects(labels)
    others = np.zeros(count + 1, dtype=bool)
    for k, piece in enumerate(pieces, start=1):
        mine = gap(piece, areas[own]["bounds"])
        others[k] = mine > 0 and any(gap(piece, a["bounds"]) < mine for i, a in enumerate(areas) if i != own)
    if not others.any():
        return rgba, 0
    kept = (labels > 0) & ~others[labels]
    gone = ndimage.binary_dilation(others[labels], iterations=4) & ~kept
    rgba[gone] = 0
    return rgba, int(others.sum())


def write_outputs(manifest: dict, base: Path, out: Path, shots: Shots, layout: Layout, seams: dict, scale: float, ax: float, ay: float, pyramid: bool) -> list[str]:
    """The image cropped to the camera bounds plus a margin, or further where the map runs on
    past them, and its preview, on-white copy, geo file and pyramid. Several arenas: one image
    each, <id>-<area>.png, without the edges of the arenas next to it (other_areas_removed), so
    its crop can follow its own content the same way.
    Returns the ids of the images written."""
    written = []
    width, height = layout.width, layout.height
    margin = manifest.get("cropMargin", 12)
    outputs = [(a["name"], a["bounds"]) for a in manifest.get("areas") or []] or [(None, manifest["cameraBounds"])]
    for area_no, (name, bounds) in enumerate(outputs):
        x0 = max(0, int(np.floor(scale * (bounds["left"] - margin) + ax)))
        x1 = min(width, int(np.ceil(scale * (bounds["right"] + margin) + ax)))
        y0 = max(0, int(np.floor(-scale * (bounds["top"] + margin) + ay)))
        y1 = min(height, int(np.ceil(-scale * (bounds["bottom"] - margin) + ay)))
        if x1 - x0 < 16 or y1 - y0 < 16:
            warn(f"{name or manifest['id']}: nothing placed inside its bounds; skipped")
            continue
        out_id = f"{manifest['id']}-{name.lower()}" if name else manifest["id"]
        # Each arena's image from its own screenshots only: the next arena's rows overlap this
        # one's edge, from other camera positions (parallax), and the two areas have no seams
        # between them.
        subset = {i for i in layout.placed if name is None or layout.by_index[i].get("area", 0) == area_no}
        with stage("composing"):
            composed = compose(shots, layout, seams, subset)
        with stage("void transparency"):
            composed = void_terrain_transparent(composed, manifest["pxPerCell"])
        if name is not None:
            with stage("other arenas removed"):
                composed, removed = other_areas_removed(composed, manifest["areas"], area_no, scale, ax, ay)
            log(f"  {out_id}: {removed} pieces of the arenas next to it removed")
        # The crop follows the map, not the camera bounds. A map's terrain can run well past
        # its camera bounds (Cursed Hollow: 25 cells), and the edge screenshots see it; the
        # output takes in everything that isn't void. (Faint pixels don't count, so a blurred
        # rim or a stray speck can't set the edge.) The same margin of transparency on every
        # side, as far as the canvas allows.
        opaque = composed[::4, ::4, 3] >= 32
        rows_on, cols_on = np.nonzero(opaque.any(axis=1))[0], np.nonzero(opaque.any(axis=0))[0]
        if len(rows_on) and len(cols_on):
            pad = int(round(margin * scale))
            x0 = min(x0, max(0, int(cols_on[0] * 4) - pad))
            x1 = max(x1, min(width, int((cols_on[-1] + 1) * 4) + pad))
            y0 = min(y0, max(0, int(rows_on[0] * 4) - pad))
            y1 = max(y1, min(height, int((rows_on[-1] + 1) * 4) + pad))
        image = pyvips.Image.new_from_array(composed, interpretation="srgb").crop(x0, y0, x1 - x0, y1 - y0)
        out_png = out / f"{out_id}.png"
        writes = [
            lambda: image.write_to_file(str(out_png), compression=PNG_COMPRESSION),
            lambda: image.flatten(background=[48, 48, 48]).thumbnail_image(2048).write_to_file(str(out / f"{out_id}-preview.jpg"), Q=88),
            # The full image over white, for looking at: transparency is hard to judge by eye.
            lambda: image.flatten(background=[255, 255, 255]).write_to_file(str(out / f"{out_id}-on-white.jpg"), Q=90),
        ]
        if pyramid:
            writes.append(lambda: image.dzsave(str(out / f"{out_id}-tiles"), layout="google", suffix=f".png[compression={PNG_COMPRESSION}]", tile_size=256))
        with stage("writing the map image"), ThreadPoolExecutor(max_workers=len(writes)) as pool:
            for done in [pool.submit(w) for w in writes]:
                done.result()
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
        (out / f"{out_id}.geo.json").write_text(json.dumps(geo, indent=2))
        log(f"{x1 - x0}x{y1 - y0} px -> {out_png}")
        if pyramid:
            log(f"tile pyramid -> {out / f'{out_id}-tiles'}")
        written.append(out_id)
    return written


def output_folder(output_dir: Path, manifest: dict) -> Path:
    """<output-dir>/<map id>: where a map's images go."""
    return output_dir / slug(manifest["map"])


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="heroes-capture stitch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--tiles", action="store_true", help="also write a Google Maps style tile pyramid")
    ap.add_argument("--output-dir", type=Path, default=Path("maps"), help="where the map's folder of images goes (default: maps)")
    args = ap.parse_args(argv)

    manifest = json.loads(args.manifest.read_text())
    base = args.manifest.parent / manifest["id"]
    out = output_folder(args.output_dir, manifest)
    out.mkdir(parents=True, exist_ok=True)
    set_log_file(base / "log.txt")
    log(f"stitch {manifest['id']}")
    shots = Shots(manifest, base)
    tiles = manifest["tiles"]

    # 1. The screenshots: present, not a failed (all-black) grab, not featureless void.
    present = [t for t in tiles if shots.exists(t["index"])]
    if not present:
        raise SystemExit(f"no screenshots in {shots.dir}; run capture.py first")
    for _ in ordered_map(shots.small, [t["index"] for t in present]):
        pass  # the half-size copies decoded in parallel (they are cached)
    # A screenshot that is entirely black is a failed grab; it would be pasted as a black box.
    black = [t["index"] for t in present if (g := shots.small(t["index"])) is not None and g.max() < 12]
    if black:
        log(f"  skipped {len(black)} all-black screenshots: {black}")
        present = [t for t in present if t["index"] not in black]
        tiles = [t for t in tiles if t["index"] not in black]
    by_pos = {(t["row"], t["col"]): t for t in tiles}
    by_index = {t["index"]: t for t in tiles}
    screen_h, screen_w = load_frame(shots.stem(present[0]["index"])).shape[:2]
    usable = {t["index"] for t in present if (g := shots.small(t["index"])) is not None and g.std() >= 3}

    # 2. Anchors, matches, the scale, the solve.
    planned = manifest["pxPerCell"]
    anchored, echoed = read_anchors(base, by_index, shots, present)
    with stage("matching neighbours"):
        edges = measure_matches(tiles, by_pos, usable, shots, lambda t: anchored.get(t["index"], (t["x"], t["y"])), planned)
    anchor_scale = scale_from_matches(edges, anchored, planned) if echoed else None
    # Anchor positions: the tile's top-left in a frame of anchor_scale px per cell, x east and y
    # south, with the map origin at (0, 0). The centre is the measured point; the half-screen
    # offset uses the shared scale, so any difference from the tile's own scale is spread evenly
    # either side of the centre, where the kept part of the screenshot is.
    anchors = {i: (anchor_scale * cx - screen_w / 2, -anchor_scale * cy - screen_h / 2) for i, (cx, cy) in anchored.items()}
    with stage("solving positions"):
        placed, kept = solve_positions(edges, anchors, anchored)
    log(f"placed {len(placed)} of {len(tiles)} screenshots from {len(kept)} matches ({len(edges)} measured)")
    unplaced = [t["index"] for t in present if t["index"] not in placed]
    if unplaced:
        warn(f"not placed (no position, no match): {len(unplaced)} screenshots: {unplaced[:20]}{' ...' if len(unplaced) > 20 else ''}")

    # 3. Map cells -> pixels.
    if anchors:
        # The anchors put the frame at anchor_scale px per cell with the map origin at (0, 0).
        scale, ax, ay = anchor_scale, 0.0, 0.0
        held_back = sum(1 for i, (cx, cy) in anchored.items() if np.hypot(cx - by_index[i]["x"], cy - by_index[i]["y"]) > 0.5)
        log(f"scale {scale:.3f} px/cell from the camera positions (planned {planned}); {held_back} screenshots had the camera held back")
    else:
        scale, ax, ay, off = fit_frame(manifest, by_index, placed, screen_w, screen_h)
        log(f"scale {scale:.3f} px/cell (planned {planned}); {off} screenshots were off their planned spot")
    matched = set(placed)
    if not anchors:
        # Screenshots that couldn't be matched (mostly void) go where the fit says, if not
        # black. That is only an estimate (the camera may have been held back), so they are
        # painted underneath, showing only where no matched screenshot reaches.
        for t in present:
            if t["index"] not in placed and shots.small(t["index"]) is not None and shots.small(t["index"]).max() >= 12:
                placed[t["index"]] = np.array([scale * t["x"] + ax - screen_w / 2, -scale * t["y"] + ay - screen_h / 2])

    # 4. The canvas covers every placed screenshot.
    shift = -np.floor(np.min([p for p in placed.values()], axis=0))
    for i in placed:
        placed[i] = placed[i] + shift
    ax, ay = ax + shift[0], ay + shift[1]
    # The status strip (blanked by the capture) sits along the left edge: left out of the page.
    page_x0 = int((manifest.get("status") or {}).get("pageLeft", 0))
    layout = Layout(placed, matched, by_pos, by_index, screen_w, screen_h, page_x0)

    # 5. Seams, then each output.
    with stage("routing seams"):
        seams = route_seams(shots, layout, manifest["pxPerCell"])
    log(f"  seams routed between {len(seams)} pairs of neighbours")
    if os.environ.get("HRS_STITCH_DEBUG"):
        (base / "seams.json").write_text(json.dumps(
            {f"{i}-{j}": {"kind": k, "start": st, "path": p.tolist(), "placed": [placed[i].tolist(), placed[j].tolist()]}
             for (i, j), (k, st, p) in seams.items()}))
    written = write_outputs(manifest, base, out, shots, layout, seams, scale, ax, ay, args.tiles)
    # The map's own sky layers and the composites, when the capture shot them.
    sky_stitch.build(manifest, base, out, written)
    viewer.write(out, manifest["id"], manifest["map"], written)
    log_timings("stitch")
