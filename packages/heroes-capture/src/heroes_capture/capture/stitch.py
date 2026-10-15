"""Stitch captured screenshots into one top-down image of the battleground.

    heroes-capture stitch tmp/towers-of-doom-structures.json [--output-dir maps] [--composites]   (heroes-capture map render runs it last)

Each screenshot is anchored where the map script reported its camera (positions.json, written
by tiles.py), and every screenshot is also matched against its right and lower neighbours,
like panorama software, to measure their real offsets; the scale (pixels per map cell) comes
from those matches, and all positions are solved together (least squares, with matches that
disagree dropped). Black and featureless screenshots are skipped. Neighbours meet along a seam
routed through their overlap where they agree best, near the halfway line, blended over a few
pixels.

Writes into <output-dir>/<map id> (maps/towers-of-doom; the map id is the map's name in
lower case, words joined by hyphens) the map's pack and raw layers (pack.py, PACK.md): the map
image (with transparency where the map lets the void through: matted from each tile's shots
over a white and a black skybox, or the black-painted void made transparent; on a map of
several arenas, Punisher Arena, one per arena) with its scale and origin in map cells and the sky
layers (sky_stitch.py), as full-resolution PNGs in raw/ and as tile pyramids in pack/; with
--composites also the map over its sky layers, in raw/.
"""

import argparse
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyvips
from PIL import Image

from heroes_capture.capture import edge_black
from heroes_capture.capture import pack
from heroes_capture.capture import sky_stitch
from heroes_capture.capture.matting import light_level, matte, moved_pixels
from heroes_capture.capture.sky import LIGHT_LEVEL
from heroes_capture.capture.team_colour import aligned, team_mask
from heroes_capture.map import water
from heroes_capture.common.frames import PNG_COMPRESSION, frame_exists, load_frame, tile_stem
from heroes_capture.common.game_data import slug
from heroes_capture.common.imaging import max3, nonzero_box
from heroes_capture.common.jsonio import read_json, write_json
from heroes_capture.common.matching import phase_correlate
from heroes_capture.common.runlog import log, warn, log_timings, set_log_file, stage
from heroes_capture.common.workers import ordered_map
from heroes_capture.map.specifics import Specifics

MATCH_SCALE = 2  # screenshots are matched at half size: fast, and still ~0.2 px precise
FEATHER = 16  # pixels blended either side of each seam: enough to hide lighting steps, narrow enough not to double leaning objects
SEAM_STEP = 2  # a seam may move this many (reduced) pixels sideways per pixel along
ANCHOR_WEIGHT = 5.0  # an anchor's weight in the solve, against a match's (its strength, up to 1)
ANCHOR = -1  # a virtual node in the solve: every anchored screenshot is connected to it


# ------------------------------------------------------------------------------------------------
# Matching
# ------------------------------------------------------------------------------------------------


def unwrap(shift: float, size: float, expected: float) -> float:
    """Correlation can't tell a shift from shift ± size; take the one nearest the plan."""
    return min((shift - size, shift, shift + size), key=lambda s: abs(s - expected))


class Shots:
    """The run's screenshots: half-size greyscale copies for matching (cached), and each tile as
    RGBA for composing (matted from its pair of shots when the map was shot over white and
    black)."""

    def __init__(self, manifest: dict, base: Path, chaos: bool = False):
        self.dir = base / "tiles"
        # Each tile from Chaos's side where it was shot from there too (its team-colour pair), else
        # as shot: the canvas the team-colour mask is taken against (team_colour.py).
        self.chaos = chaos
        # Only when this map is shot over white and black (manifest sky mode): a results folder
        # can hold -black shots left from an earlier run of a map since shot once per tile.
        self.matting = (manifest.get("sky") or {}).get("mode") == "matte"
        self.light_level: float | None = None
        self._small: dict[int, np.ndarray | None] = {}
        # Each matted tile's pixels taken for something moving between its two shots (matte):
        # logged, to tell whether anything still moves now that the map's effects are frozen.
        self.moved: dict[int, tuple[int, int]] = {}

    def has_chaos(self, index: int) -> bool:
        return frame_exists(tile_stem(self.dir, index, chaos=True))

    def stem(self, index: int) -> Path:
        return tile_stem(self.dir, index, chaos=self.chaos and self.has_chaos(index))

    def black_stem(self, index: int) -> Path:
        return tile_stem(self.dir, index, black=True, chaos=self.chaos and self.has_chaos(index))

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
        """The light sky's level each matted tile is matted with, in this order: measured on the
        first tile that shows enough sky (pixels black in the black shot), LIGHT_LEVEL for any
        before it. Done
        in order before the tiles are loaded in parallel, so the result doesn't depend on which
        thread finishes first."""
        levels = {}
        for i in order:
            if not self.matting or not self.has_black(i):
                continue
            if self.light_level is None:
                self.light_level = light_level(load_frame(self.stem(i)), load_frame(self.black_stem(i)))
                if self.light_level is not None:
                    log(f"  light sky level {self.light_level:.1f}")
            levels[i] = self.light_level or LIGHT_LEVEL
        return levels

    def load(self, i: int, level: float | None) -> np.ndarray:
        """The tile as RGBA: matted from its pair of shots with that white level, or opaque (a
        map whose void is black terrain is shot once, over black; void_terrain_transparent
        handles the void)."""
        white = load_frame(self.stem(i))
        if not self.matting or not self.has_black(i):
            return np.dstack([white, np.full(white.shape[:2], 255, dtype=np.uint8)])
        counts: list[int] = []
        rgba = matte(white, load_frame(self.black_stem(i)), level if level is not None else LIGHT_LEVEL, moved=counts)
        self.moved[i] = (sum(counts), white.shape[0] * white.shape[1])
        return rgba


def read_anchors(base: Path, by_index: dict, shots: Shots, present: list) -> dict[int, tuple[float, float]]:
    """Where each screenshot's centre really was, in map cells: the camera target the map script
    reported through its status strip for every tile (positions.json, which every capture
    writes)."""
    anchored: dict[int, tuple[float, float]] = {}
    positions_path = base / "positions.json"
    if not positions_path.exists():
        raise SystemExit(f"{positions_path} is missing: capture the map again (a capture records where the camera was for each tile)")
    for key, pos in read_json(positions_path).items():
        i = int(key)
        if i in by_index and shots.exists(i):
            anchored[i] = (pos["x"], pos["y"])
    log(f"camera positions: {len(anchored)} of {len(present)} screenshots anchored")
    if not anchored:
        raise SystemExit(f"{positions_path} has no position for any screenshot there is")
    return anchored


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


# ------------------------------------------------------------------------------------------------
# Composing
# ------------------------------------------------------------------------------------------------


@dataclass
class Layout:
    """Where every screenshot goes on the canvas (top-left corners), and the part of each that
    may be used (the page: the status strip's column along the left edge is left out)."""

    placed: dict[int, np.ndarray]
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


@dataclass
class Stitched:
    """A stitched map, for the after_stitch hooks (map/specifics.py): the manifest, the run's
    folder, the output folder, where each screenshot was placed, the scale and origin (map cells to
    the canvas's pixels: x = ax + cx * scale, y = ay - cy * scale) and the map images written."""

    manifest: dict
    base: Path
    out: Path
    layout: Layout
    scale: float
    ax: float
    ay: float
    written: list[str]


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
    for i in layout.placed:
        t = layout.by_index[i]
        for (dr, dc), kind in (((0, 1), "v"), ((1, 0), "h")):
            n = layout.by_pos.get((t["row"] + dr, t["col"] + dc))
            if n is None or n["index"] not in layout.placed:
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
            if n["index"] in L.placed:
                # Placed neighbour, no seam routed (overlap too small): the halfway line.
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


# Opacity two screenshots must differ by at a pixel of their seam's feather for it to be taken
# from one of them outright rather than blended (seam_blend).
SEAM_DISAGREE = 0.5


def seam_blend(under: np.ndarray, over: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """A screenshot's region (`over`, RGBA) laid over what is there (`under`) with its feather's
    weights (0..1): blended by opacity (colour weighted by each one's alpha, so a see-through
    pixel's black doesn't darken the other's colour), except where the two disagree about whether
    the pixel is there at all (alpha apart by SEAM_DISAGREE: something above the ground, a floating
    piece at the map's edge or a hole's rim, seen shifted in each screenshot), where the one
    weighing more is taken whole. Blended, those came out as dark see-through copies of the piece
    beside it, like shadows."""
    u, o = under.astype(np.float32), over.astype(np.float32)
    ua, oa = u[..., 3] / 255, o[..., 3] / 255
    w = np.where(np.abs(oa - ua) > SEAM_DISAGREE, (weight >= 0.5).astype(np.float32), weight)
    alpha = w * oa + (1 - w) * ua
    rgb = (o[..., :3] * (w * oa)[..., None] + u[..., :3] * ((1 - w) * ua)[..., None]) / np.maximum(alpha, 1e-6)[..., None]
    out = np.empty_like(under)
    out[..., :3] = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = (alpha * 255 + 0.5).astype(np.uint8)
    return out


def compose(shots: Shots, layout: Layout, seams: dict, subset: set[int]) -> np.ndarray:
    """The canvas painted from these screenshots only. Full frames go down first (outermost
    first), so there are no gaps; then each screenshot's own
    region, bounded by its seams, goes on top, blended over FEATHER pixels either side of each
    seam so small lighting differences don't show as steps. Each screenshot is loaded (and
    matted) once: its region is kept from the first pass for the second."""
    L = layout
    canvas = np.zeros((L.height, L.width, 4), dtype=np.uint8)

    chosen = [i for i in L.placed if i in subset]
    boxes = {}
    for i, region in zip(chosen, ordered_map(lambda i: supply_region(L, seams, i), chosen)):
        if region is not None:
            boxes[i] = region[1]
    kept: dict[int, np.ndarray] = {}
    order = sorted(chosen, key=lambda i: -np.hypot(*(L.centres[i] - L.mid)))
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
        region[:] = seam_blend(region, kept.pop(i), alpha)
    counts = {i: shots.moved[i] for i in order if i in shots.moved}
    if counts:
        worst = max(counts, key=lambda i: counts[i][0])
        log(f"  taken for moving between the shots (left opaque): {sum(c for c, _ in counts.values())} pixels in "
            f"{sum(1 for c, _ in counts.values() if c)} of {len(counts)} tiles; most in tile {worst + 1} "
            f"({counts[worst][0] / counts[worst][1] * 100:.3f}% of it)")
        if os.environ.get("HRS_STITCH_DEBUG"):
            show_moving(shots, sorted((i for i in counts if counts[i][0]), key=lambda i: -counts[i][0])[:MOVING_SHOWN])
    return canvas


# Tiles whose pixels taken for moving are shown (show_moving).
MOVING_SHOWN = 3


def show_moving(shots: Shots, tiles: list[int]) -> None:
    """A diagnosis (HRS_STITCH_DEBUG): for each of these tiles, its shot over white at half size with the pixels taken
    for moving marked in magenta, and the densest 256-pixel square of them full size, over white
    and over black side by side; in moving-<time>/ next to the run's folder (it outlives the
    run's working files). What still changes between the shots now that the map's animations,
    particles and ribbons are frozen."""
    import time

    if not tiles:
        return
    folder = shots.dir.parent.parent / f"moving-{time.strftime('%H%M%S')}"
    folder.mkdir(exist_ok=True)
    for i in tiles:
        white, black = load_frame(shots.stem(i)), load_frame(shots.black_stem(i))
        mask = moved_pixels(white, black)
        marked = white[..., :3].copy()
        grown = np.zeros_like(mask)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                grown |= np.roll(np.roll(mask, dy, 0), dx, 1)
        marked[grown] = (255, 0, 255)
        Image.fromarray(marked).reduce(2).save(folder / f"tile-{i + 1}-marked.png")
        h, w = mask.shape
        size = 256
        blocks = mask[: h // size * size, : w // size * size].reshape(h // size, size, w // size, size).sum(axis=(1, 3))
        by, bx = np.unravel_index(int(blocks.argmax()), blocks.shape)
        y0, x0 = by * size, bx * size
        pair = np.concatenate([white[y0:y0 + size, x0:x0 + size, :3], black[y0:y0 + size, x0:x0 + size, :3]], axis=1)
        Image.fromarray(pair).save(folder / f"tile-{i + 1}-densest.png")
    log(f"  the pixels taken for moving shown for tiles {', '.join(str(i + 1) for i in tiles)} in {folder}")


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
    peak = max3(rgb)
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


def canvas_team_mask(order: np.ndarray, chaos: np.ndarray, rows: int = 512, wet: np.ndarray | None = None) -> np.ndarray:
    """The team-colour mask of the composed canvas (team_mask), a strip of rows at a time (the
    whole canvas in floating point would take several GB); the canvas's own transparency for both
    sides (the void's, made transparent after composing). Where `wet` (the water's pixels, which
    move between the shots), only changes along the teams' colours count (team_colour.aligned)."""
    mask = np.zeros(order.shape[:2] + (2,), np.uint8)
    for y in range(0, order.shape[0], rows):
        o = order[y:y + rows]
        c = np.dstack([chaos[y:y + rows, :, :3], o[..., 3:4]])
        found = team_mask(o, c)
        if found is not None:
            if wet is not None and wet[y:y + rows].any():
                found[wet[y:y + rows] & ~aligned(o, c)] = 0
            mask[y:y + rows] = found
    return mask


def write_outputs(manifest: dict, out: Path, shots: Shots, layout: Layout, seams: dict, scale: float, ax: float, ay: float) -> list[str]:
    """The image cropped to the camera bounds plus a margin, or further where the map runs on
    past them, and its geo file (scale and origin, for the pack). Several arenas: one image
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
        # The team colour: the same canvas from Chaos's side, where tiles were shot from there.
        mask = None
        if any(shots.has_chaos(i) for i in subset):
            with stage("team colour"):
                chaos = compose(Shots(manifest, shots.dir.parent, chaos=True), layout, seams, subset)
                # The water moves while paused: there, only a change along the teams' colours counts.
                wet = water.water_pixels(composed.shape, manifest["water"], scale, ax, ay) if manifest.get("water") else None
                mask = canvas_team_mask(composed, chaos, wet=wet)
                del chaos, wet
        # The game's black at the map's edge (edge_black.py), after the team mask: black from both
        # sides, it has no team colour, and the fade undone would.
        if manifest.get("mapSize"):
            with stage("edge black"):
                removed, restored = edge_black.edge_black_removed(composed, scale, ax, ay, (manifest["mapSize"]["width"], manifest["mapSize"]["height"]))
            if removed:
                log(f"  the game's black at the map's edge removed: {removed} px; the fade before it undone on {restored} px")
        if name is not None:
            with stage("other arenas removed"):
                composed, removed = other_areas_removed(composed, manifest["areas"], area_no, scale, ax, ay)
            log(f"  {out_id}: {removed} pieces of the arenas next to it removed")
        # The crop follows the map, not the camera bounds. A map's terrain can run well past
        # its camera bounds (Cursed Hollow: 25 cells), and the edge screenshots see it; the
        # output takes in everything that isn't void. (Faint pixels don't count, so a blurred
        # rim or a stray speck can't set the edge.) The same margin of transparency on every
        # side, as far as the canvas allows.
        found = nonzero_box(composed[::4, ::4, 3] >= 32)
        if found:
            pad = int(round(margin * scale))
            r0, r1, c0, c1 = found
            x0 = min(x0, max(0, c0 * 4 - pad))
            x1 = max(x1, min(width, c1 * 4 + pad))
            y0 = min(y0, max(0, r0 * 4 - pad))
            y1 = max(y1, min(height, r1 * 4 + pad))
        image = pyvips.Image.new_from_array(composed, interpretation="srgb").crop(x0, y0, x1 - x0, y1 - y0)
        out_png = out / f"{out_id}.png"
        with stage("writing the map image"):
            image.write_to_file(str(out_png), compression=PNG_COMPRESSION)
            if mask is not None:
                rgb = np.dstack([mask[y0:y1, x0:x1], np.zeros((y1 - y0, x1 - x0), np.uint8)])
                pyvips.Image.new_from_array(rgb, interpretation="srgb").write_to_file(str(out / f"{out_id}-team.png"), compression=PNG_COMPRESSION)
                log(f"  team colour on {float((rgb[..., :2].max(axis=2) > 0).mean()) * 100:.2f}% of it -> {out_id}-team.png")
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
        write_json(out / f"{out_id}.geo.json", geo, indent=2)
        log(f"{x1 - x0}x{y1 - y0} px -> {out_png}")
        written.append(out_id)
    return written


def output_folder(output_dir: Path, manifest: dict) -> Path:
    """<output-dir>/<map id>: where a map's images go."""
    return output_dir / slug(manifest["map"])


def main(argv: list[str], specifics_of: Callable[[dict], Specifics]) -> None:
    """Stitch a captured map; specifics_of(manifest) gives what its category and the map itself
    add (cli.specifics_of)."""
    ap = argparse.ArgumentParser(prog="heroes-capture stitch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--output-dir", type=Path, default=Path("maps"), help="where the map's folder of images goes (default: maps)")
    ap.add_argument("--composites", action="store_true",
                    help="also write the map over its sky layers as three full-size PNGs in raw/ (a reference: nothing in the pack reads them)")
    args = ap.parse_args(argv)

    manifest = read_json(args.manifest)
    base = args.manifest.parent / manifest["id"]
    specifics = specifics_of(manifest)
    out = output_folder(args.output_dir, manifest)
    out.mkdir(parents=True, exist_ok=True)
    set_log_file(base / "log.txt")
    log(f"stitch {manifest['id']}")
    shots = Shots(manifest, base)
    tiles = manifest["tiles"]

    # 1. The screenshots: present, not a failed (all-black) grab, not featureless void.
    present = [t for t in tiles if shots.exists(t["index"])]
    if not present:
        raise SystemExit(f"no screenshots in {shots.dir}; run the capture first")
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
    anchored = read_anchors(base, by_index, shots, present)
    with stage("matching neighbours"):
        edges = measure_matches(tiles, by_pos, usable, shots, lambda t: anchored.get(t["index"], (t["x"], t["y"])), planned)
    anchor_scale = scale_from_matches(edges, anchored, planned)
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

    # 3. Map cells -> pixels: the anchors put the frame at anchor_scale px per cell with the map
    # origin at (0, 0).
    scale, ax, ay = anchor_scale, 0.0, 0.0
    held_back = sum(1 for i, (cx, cy) in anchored.items() if np.hypot(cx - by_index[i]["x"], cy - by_index[i]["y"]) > 0.5)
    log(f"scale {scale:.3f} px/cell from the camera positions (planned {planned}); {held_back} screenshots had the camera held back")

    # 4. The canvas covers every placed screenshot.
    shift = -np.floor(np.min([p for p in placed.values()], axis=0))
    for i in placed:
        placed[i] = placed[i] + shift
    ax, ay = ax + shift[0], ay + shift[1]
    # The status strip (blanked by the capture) sits along the left edge: left out of the page.
    page_x0 = int((manifest.get("status") or {}).get("pageLeft", 0))
    layout = Layout(placed, by_pos, by_index, screen_w, screen_h, page_x0)

    # 5. Seams, then each output.
    with stage("routing seams"):
        seams = route_seams(shots, layout, manifest["pxPerCell"])
    log(f"  seams routed between {len(seams)} pairs of neighbours")
    if os.environ.get("HRS_STITCH_DEBUG"):
        write_json(base / "seams.json", {f"{i}-{j}": {"kind": k, "start": st, "path": p.tolist(), "placed": [placed[i].tolist(), placed[j].tolist()]}
                                         for (i, j), (k, st, p) in seams.items()}, indent=None)
    written = write_outputs(manifest, out, shots, layout, seams, scale, ax, ay)
    stitched = Stitched(manifest, base, out, layout, scale, ax, ay, written)
    for hook in specifics.after_stitch:
        hook(stitched)
    # The map's own sky layers (and with --composites the composites), when the capture shot them.
    sky_stitch.build(manifest, base, out, written, args.composites)
    pack.write(out, manifest, written, specifics)
    log_timings("stitch")
