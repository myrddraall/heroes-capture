"""The map's own sky layers as images, and composites with the map (from capture.py's sky/ shots).

Layers, written next to the map image:
  <id>-layer-fixed.png       the fixed skybox: it moves with the camera, so it is one screen-sized
                             backdrop for a viewer, not part of the map
  <id>-layer-background.png  the parallax model's background art, with transparency where it lets
                             the fixed skybox through
  <id>-layer-haze.png        the parallax model's haze, matted (colour and transparency)
  <id>-composite.png         background, haze and map together, transparent where none of them is
  <id>-composite-on-black.png  the haze and the map over black (no background art, no fixed skybox)
  <id>-composite-with-fixed.png  the same over the fixed skybox, stretched behind everything like a
                             backdrop (opaque, as the skybox is)
  <id>-layers.json           how a viewer places each layer: its rate (how fast it moves against
                             the map) and which of its pixels is behind which map cell
On a map of several arenas (Punisher Arena) the layers and <id>-layers.json, listing each
arena's image, but no composites.

Each sky shot is matted on its own (the haze from its shots over white and black, with the white
level the game's lighting gives the key taken from the white-without-haze shot; the background
art from its shots over black and over the fixed skybox), then the shots are blended into one
image per layer: the view of the layer from the middle camera position, as if the screen were
large enough to show all of it.

The shells aren't level: on Battlefield of Eternity they are a plane rising towards the north, so
the sky's shift between two camera positions changes across the screen (a whole-shot shift by
the measured rate left the towers doubled, up to 60 px apart). So the depth plane is fitted to
patches matched where neighbouring shots overlap (its tilt shared by the layers, as they are
shells of one model; each layer's depth at the middle its own, or from its measured rate when it
has too few matches), and each shot is warped into place through it. With the camera looking
straight down, a shot of a plane maps onto the middle camera's view by an affine transform.

The composites are at map scale, with each map point showing the sky the game draws behind it
when the camera is centred on it: the sky layers stretched by 1/rate about the middle camera
position.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pyvips
from PIL import Image
from scipy.optimize import least_squares

from .frames import PNG_COMPRESSION, frame_exists, load_frame
from .matching import phase_correlate
from .runlog import log, warn, stage
from .workers import ordered_map

MATCH_PATCH = 128  # the side of a matched patch, in half-size pixels (256 screen pixels)
MATCH_STRENGTH = 0.12  # a patch match weaker than this is left out (soft haze matches falsely below it)
MIN_TILT_MATCHES = 12  # fewer matches in all: the shells taken as level (each shot only shifted)
KEY_FULL = 200  # the white key's level (230 in full) from which the haze matte trusts it; below it the haze is cut off
MIN_DEPTH_MATCHES = 40  # fewer for a layer: its depth at the middle from its measured rate (a few tenths of a percent off)


def _load(stem: Path) -> np.ndarray:
    return load_frame(stem).astype(np.float32)


def _mean3(a: np.ndarray) -> np.ndarray:
    return (a[:, :, 0] + a[:, :, 1] + a[:, :, 2]) / 3.0


def _haze(white: np.ndarray, black: np.ndarray, level: np.ndarray, left: int) -> np.ndarray:
    """RGBA (float, 0..1 alpha) of the haze: alpha from how much the background shows through,
    the difference between the shots over white and over black against the white level.

    Only where the white key is behind the haze: past the end of the sky shells (the far south
    of Punisher Arena, whose haze reaches further than its background art: a misalignment in the
    map) that difference says nothing, and the haze is cut off there, transparent, so the layer
    shows it over the art only."""
    key_level = _mean3(level)
    alpha = np.clip(1.0 - _mean3(white - black) / np.maximum(key_level, 1.0), 0.0, 1.0)
    alpha[key_level < KEY_FULL] = 0.0
    alpha[alpha < 1 / 255] = 0.0
    colour = np.where(alpha[..., None] > 0, np.clip(black / np.maximum(alpha, 1 / 255)[..., None], 0, 255), 0)
    return np.dstack([colour, alpha])


def _crop_to_content(image: np.ndarray, low: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """An RGBA layer image cropped to its pixels that aren't transparent, and its canvas origin
    (`low`, the canvas's top-left in the centre view) moved with the crop, so each pixel left stays
    where it was behind the map."""
    ys, xs = np.nonzero(image[:, :, 3])
    if not len(xs):
        return image, low
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    return np.ascontiguousarray(image[y0:y1, x0:x1]), low + np.array([x0, y0])


def _background(over_black: np.ndarray, over_fixed: np.ndarray, fixed: np.ndarray) -> np.ndarray:
    """RGBA of the background art: where the fixed skybox behind it is bright enough to tell,
    alpha from how much of it shows through; elsewhere opaque unless black over black."""
    shows = _mean3(fixed)
    alpha = np.where(
        shows >= 12,
        1.0 - np.clip(_mean3(over_fixed - over_black) / np.maximum(shows, 1.0), 0.0, 1.0),
        (over_black.max(axis=2) > 3).astype(np.float32),
    )
    colour = np.where(alpha[..., None] > 1 / 255, np.clip(over_black / np.maximum(alpha, 1 / 255)[..., None], 0, 255), 0)
    return np.dstack([colour, alpha])


def _blend(frames, width: int, height: int) -> np.ndarray:
    """Warped frames on one canvas, blended with their weights (falling off towards each shot's
    edges), transparent where no shot reaches (the warped shots' outer edges are slanted).
    `frames` yields (premultiplied colour, alpha, weight, x, y), one at a time, so transparent
    parts don't darken the rest."""
    colour = np.zeros((height, width, 3), np.float32)
    alpha = np.zeros((height, width), np.float32)
    weight = np.zeros((height, width), np.float32)
    for rgb, a, win, x, y in frames:
        h, w = a.shape
        colour[y : y + h, x : x + w] += rgb * win[..., None]
        alpha[y : y + h, x : x + w] += a * win
        weight[y : y + h, x : x + w] += win
    out_alpha = np.where(weight > 0, alpha / np.maximum(weight, 1e-6), 0)
    out_colour = np.where(alpha[..., None] > 1e-6, colour / np.maximum(alpha, 1e-6)[..., None], 0)
    return np.dstack([out_colour, out_alpha * 255]).round().clip(0, 255).astype(np.uint8)


class Plane:
    """The sky shells' depth (from the camera) at a map point: depth + slope . (point - centre).
    Screen coordinates here are from the screen's middle, x east, y south; `focal` is screen
    pixels per map cell at unit depth (the map's pixels per cell times the camera distance)."""

    def __init__(self, depth: float, slope: tuple[float, float], centre: np.ndarray, focal: float):
        self.depth, self.slope, self.centre, self.focal = depth, slope, centre, focal

    def under(self, camera) -> float:
        """The depth straight below a camera position."""
        return self.depth + self.slope[0] * (camera[0] - self.centre[0]) + self.slope[1] * (camera[1] - self.centre[1])

    def to_centre_view(self, camera) -> tuple[np.ndarray, np.ndarray]:
        """(A, t): a point at screen position p in a shot from `camera` is at A p + t in the view
        from the centre camera position (both from the screen's middle)."""
        (sx, sy), z = self.slope, self.under(camera)
        dx, dy = camera[0] - self.centre[0], self.centre[1] - camera[1]
        return (np.array([[1 - dx * sx / z, dx * sy / z], [-dy * sx / z, 1 + dy * sy / z]]),
                self.focal * np.array([dx, dy]) / z)


def _shifted(p: np.ndarray, a, b, depth_a: np.ndarray, slope, focal: float) -> np.ndarray:
    """Where points at screen positions p (n x 2) in shots from cameras a (n x 2) are in shots
    from cameras b, for shells with depth `depth_a` below each a and the given slope."""
    z = depth_a / (1 - (slope[0] * p[:, 0] - slope[1] * p[:, 1]) / focal)
    x = a[:, 0] + p[:, 0] * z / focal
    y = a[:, 1] - p[:, 1] * z / focal
    return np.stack([focal * (x - b[:, 0]) / z, focal * (b[:, 1] - y) / z], axis=1)


def _matches(grey: list, cameras: np.ndarray, focal: float, depth: float, left: int) -> list:
    """Patches matched where shots overlap: (a, b, p in a, p in b), screen positions from the
    screen's middle. `grey` holds each shot at half size; the shift a level layer at `depth`
    would have says where to look."""
    h, w = grey[0].shape
    size, lo = MATCH_PATCH, (left + 1) // 2
    pairs = []
    for a in range(len(cameras)):
        for b in range(a + 1, len(cameras)):
            sx = -focal * (cameras[b][0] - cameras[a][0]) / depth / 2  # half-size pixels
            sy = focal * (cameras[b][1] - cameras[a][1]) / depth / 2
            if abs(sx) <= w - lo - size and abs(sy) <= h - size:
                pairs.append((a, b, sx, sy))

    def match(pair) -> list:
        a, b, sx, sy = pair
        found = []
        for y in range(0, h - size + 1, size // 2):
            for x in range(lo, w - size + 1, size // 2):
                xb, yb = int(round(x + sx)), int(round(y + sy))
                if xb < lo or yb < 0 or xb + size > w or yb + size > h:
                    continue
                pa, pb = grey[a][y : y + size, x : x + size], grey[b][yb : yb + size, xb : xb + size]
                if pa.std() < 2 or pb.std() < 2:
                    continue  # nothing to match
                dy, dx, strength = phase_correlate(pa, pb)
                if strength >= MATCH_STRENGTH:  # b's content is a's moved by (-dx, -dy) from the guess
                    centre = (x + size / 2, y + size / 2)
                    found.append((a, b, (2 * centre[0] - w, 2 * centre[1] - h),
                                  (2 * (xb + size / 2 - dx) - w, 2 * (yb + size / 2 - dy) - h)))
        return found

    return [m for found in ordered_map(match, pairs) for m in found]


def _fit_planes(matches: dict, cameras: np.ndarray, centre: np.ndarray, focal: float, depths: dict) -> dict:
    """One Plane per layer: the slope shared, fitted to every layer's matches; a layer's depth at
    the middle fitted when it has enough matches, else `depths` (from its measured rate)."""
    layers = list(matches)
    fitted = [n for n in layers if len(matches[n]) >= MIN_DEPTH_MATCHES]
    total = sum(len(m) for m in matches.values())
    if total < MIN_TILT_MATCHES:
        log(f"  sky shells: {total} matches between shots; taken as level")
        return {n: Plane(depths[n], (0.0, 0.0), centre, focal) for n in layers}
    rows = [(n, a, b, pa, pb) for n in layers for a, b, pa, pb in matches[n]]
    which = np.array([layers.index(n) for n, *_ in rows])
    ca, cb = cameras[[r[1] for r in rows]], cameras[[r[2] for r in rows]]
    pa, pb = np.array([r[3] for r in rows], float), np.array([r[4] for r in rows], float)

    def unpack(params):
        known = dict(depths, **dict(zip(fitted, params[2:])))
        return (params[0], params[1]), np.array([known[n] for n in layers])

    def errors(params):
        slope, middle = unpack(params)
        below_a = middle[which] + slope[0] * (ca[:, 0] - centre[0]) + slope[1] * (ca[:, 1] - centre[1])
        return (_shifted(pa, ca, cb, below_a, slope, focal) - pb).ravel()

    level = [0.0, 0.0] + [depths[n] for n in fitted]
    params = least_squares(errors, level, loss="soft_l1", f_scale=2.0).x
    slope, middle = unpack(params)
    miss = np.hypot(*errors(params).reshape(-1, 2).T)
    was = np.hypot(*errors(level).reshape(-1, 2).T)
    log(f"  sky shells: depth slope {slope[0]:+.3f}, {slope[1]:+.3f} per cell east, north; "
        + ", ".join(f"{n} depth {d:.1f}" + ("" if n in fitted else " (from its rate)") for n, d in zip(layers, middle))
        + f"; {len(rows)} matches off by {np.median(miss):.1f} px (90%: {np.percentile(miss, 90):.1f}), level {np.median(was):.1f} px")
    return {n: Plane(float(d), slope, centre, focal) for n, d in zip(layers, middle)}


def _warp(rgba: np.ndarray, window: np.ndarray, matrix: np.ndarray, offset: np.ndarray, box: tuple[int, int, int, int]):
    """A shot (RGBA float, 0..1 alpha) through an affine transform onto `box` (x, y, w, h) of the
    canvas: (premultiplied colour, alpha, weight) there."""
    stack = np.dstack([rgba[..., :3] * rgba[..., 3:], rgba[..., 3], window]).astype(np.float32)
    out = pyvips.Image.new_from_array(stack).affine(
        list(matrix.ravel()), odx=float(offset[0]) - box[0], ody=float(offset[1]) - box[1], oarea=[0, 0, box[2], box[3]],
        interpolate=pyvips.Interpolate.new("bilinear"), extend="background", background=[0] * 5).numpy()
    return out[..., :3], out[..., 3], out[..., 4]


def build(manifest: dict, base: Path, out: Path, images: list[str]) -> None:
    """The layer images and composites, into `out`, when capture.py left sky shots in <id>/sky/.
    `images`: the ids of the map images the stitch wrote (several on a map of several arenas: the
    layers, but no composites)."""
    folder = base / "sky"
    record_path, measured_path = folder / "positions.json", base / "sky-layers.json"
    if not record_path.exists() or not measured_path.exists():
        return
    record = json.loads(record_path.read_text())
    measured = json.loads(measured_path.read_text())
    names = sorted(record["positions"])
    if not names or not frame_exists(folder / "fixed"):
        warn("sky layers: no shots; skipped")
        return
    out_id = manifest["id"]
    scale = record["mapPxPerCell"]  # the map's screen pixels per cell during the capture
    left = int((manifest.get("status") or {}).get("pageLeft", 0))
    fixed = _load(folder / "fixed")
    height, width = fixed.shape[:2]
    cameras = np.array([record["positions"][n]["camera"] for n in names])
    centre = cameras.mean(axis=0)
    log(f"sky layers: {len(names)} shots")

    def rate_of(layer: str) -> tuple[float, float]:
        known = (measured["layers"].get(layer) or measured["layers"]["parallax"])["rate"]
        used = record["rateUsedForSteps"]
        fallback = [r for r in known if r] or [r for r in (used if isinstance(used, list) else [used]) if r]
        return tuple(float(r) if r else float(np.mean(fallback)) for r in known)

    distance = float(measured.get("cameraDistance") or manifest["distance"])
    focal = scale * distance
    sources = {"background": "parallax", "haze": "haze"}  # each layer's entry in sky-layers.json
    depths = {layer: distance / float(np.mean(rate_of(source))) for layer, source in sources.items()}

    def grey(n: str) -> dict:
        """Shot n at half size, for matching: the background art's brightness; the haze's matte."""
        bare = _mean3(load_frame(folder / f"{n}-bare")[::2, ::2].astype(np.float32))
        white, black, level = (_mean3(load_frame(folder / f"{n}-{v}")[::2, ::2].astype(np.float32)) for v in ("white", "black", "whitebare"))
        return {"background": bare, "haze": np.clip(1.0 - (white - black) / np.maximum(level, 1.0), 0.0, 1.0) * 255}

    # The shot's weights: falling off towards its edges, none over the status strip's column (the
    # first `left` columns), so the layer images have no empty strip along their edge.
    wx = np.zeros(width, np.float32)
    wx[left:] = np.sin(np.pi * (np.arange(left, width) - left + 0.5) / (width - left))
    wy = np.sin(np.pi * (np.arange(height) + 0.5) / height).astype(np.float32)
    window = wy[:, None] * wx[None, :]
    middle = np.array([width / 2, height / 2])
    corners = np.array([[left, 0], [width, 0], [left, height], [width, height]]) - middle  # the shot's used area

    placements = {}
    with stage("sky layer images"):
        greys = list(ordered_map(grey, names))
        matches = {layer: _matches([g[layer] for g in greys], cameras, focal, depths[layer], left) for layer in sources}
        del greys
        planes = _fit_planes(matches, cameras, centre, focal, depths)
        for layer, make in (("background", lambda n: _background(_load(folder / f"{n}-bare"), _load(folder / f"{n}-bareoverfixed"), fixed)),
                            ("haze", lambda n: _haze(_load(folder / f"{n}-white"), _load(folder / f"{n}-black"), _load(folder / f"{n}-whitebare"), left))):
            plane = planes[layer]
            # Each shot onto the view from the centre camera position: where its corners land
            # sets the canvas and the shot's box on it.
            views = [plane.to_centre_view(c) for c in cameras]
            landed = [corners @ m.T + t for m, t in views]
            low = np.floor(np.min([q.min(axis=0) for q in landed], axis=0))
            high = np.ceil(np.max([q.max(axis=0) for q in landed], axis=0))
            canvas_w, canvas_h = (int(v) for v in high - low)
            boxes = []
            for q in landed:
                x0, y0 = (int(v) for v in np.floor(q.min(axis=0)) - low)
                x1, y1 = (int(v) for v in np.ceil(q.max(axis=0)) - low)
                boxes.append((x0, y0, x1 - x0, y1 - y0))

            def placed(k: int, make=make, views=views, boxes=boxes, low=low):
                matrix, t = views[k]
                rgb, alpha, weight = _warp(make(names[k]), window, matrix, t - low - matrix @ middle, boxes[k])
                return rgb, alpha, weight, boxes[k][0], boxes[k][1]

            image = _blend(ordered_map(placed, range(len(names))), canvas_w, canvas_h)
            if layer == "haze":
                # Cut off past the art (_haze): the image cropped to what is left, so its extent
                # is the art's, not the haze's (a viewer centres on its layers).
                image, low = _crop_to_content(image, low)
                canvas_h, canvas_w = image.shape[:2]
            path = out / f"{out_id}-layer-{layer}.png"
            Image.fromarray(image).save(path, compress_level=PNG_COMPRESSION)
            # The layer's pixel behind the screen's middle with the camera at `centre`, and how fast
            # the layer moves there (deeper or shallower parts of the shells move slower or faster).
            rate = distance / plane.depth
            placements[layer] = {"image": path.name, "width": canvas_w, "height": canvas_h, "rate": [rate, rate],
                                 "centreCell": [float(centre[0]), float(centre[1])],
                                 "centrePixel": [float(-low[0]), float(-low[1])],
                                 "pxPerMapCell": [rate * scale, rate * scale]}
            log(f"  {layer}: {canvas_w}x{canvas_h} px, rate {rate:.4f} -> {path.name}")
    Image.fromarray(fixed[:, left:].astype(np.uint8)).save(out / f"{out_id}-layer-fixed.png", compress_level=PNG_COMPRESSION)

    def write_layers(map_entry: dict) -> None:
        (out / f"{out_id}-layers.json").write_text(json.dumps({
            "map": map_entry,
            "fixed": {"image": f"{out_id}-layer-fixed.png", "rate": [0.0, 0.0], "note": "moves with the camera: a screen backdrop"},
            **placements,
            "howTo": "A layer pixel for map cell (x, y) seen with the camera there: centrePixel + pxPerMapCell * ((x, y) - centreCell), y flipped (image y grows south). A viewer panning by d map cells moves a layer by rate * d.",
        }, indent=2))

    map_png = out / f"{out_id}.png"
    geo_path = out / f"{out_id}.geo.json"
    if images != [out_id] or not map_png.exists() or not geo_path.exists():
        # Several arenas: each map image is placed by its own geo file, all in map cells.
        write_layers({"rate": [1.0, 1.0], "areas": [{"image": f"{i}.png", "geo": f"{i}.geo.json"} for i in images]})
        log(f"  sky layers: {len(images)} map images; no composites")
        return
    geo = json.loads(geo_path.read_text())
    s, origin = geo["pxPerCell"], geo["originCell"]
    mw, mh = geo["width"], geo["height"]

    def at_map_scale(layer: str) -> pyvips.Image:
        """The layer stretched onto the map image: map pixel (X, Y) shows the layer's pixel behind
        the screen's middle with the camera at that map point."""
        p = placements[layer]
        kx, ky = p["pxPerMapCell"]
        u0, v0 = p["centrePixel"]
        cx, cy = p["centreCell"]
        a, b = kx / s, u0 + kx * (origin["x"] - cx)  # U = a X + b
        c, d = ky / s, v0 - ky * (origin["y"] - cy)  # V = c Y + d
        img = pyvips.Image.new_from_file(str(out / p["image"]))
        return img.affine([1 / a, 0, 0, 1 / c], odx=-b / a, ody=-d / c, oarea=[0, 0, mw, mh],
                          interpolate=pyvips.Interpolate.new("bilinear"), extend="background", background=[0, 0, 0, 0])

    with stage("composites"):
        # Each composite rendered into memory once, then its files written in parallel.
        map_image = pyvips.Image.new_from_file(str(map_png))
        haze_and_map = at_map_scale("haze").composite2(map_image, "over").copy_memory()
        composite = at_map_scale("background").composite2(haze_and_map, "over").copy_memory()
        on_black = haze_and_map.flatten(background=[0, 0, 0]).copy_memory()
        # With the fixed skybox: it moves with the camera, so behind a whole-map picture it is a
        # backdrop filling the picture.
        backdrop = pyvips.Image.new_from_file(str(out / f"{out_id}-layer-fixed.png"))
        backdrop = backdrop.resize(mw / backdrop.width, vscale=mh / backdrop.height).bandjoin(255)
        with_fixed = backdrop.composite2(composite, "over").flatten(background=[0, 0, 0]).copy_memory()
        writes = [
            lambda: composite.write_to_file(str(out / f"{out_id}-composite.png"), compression=PNG_COMPRESSION),
            lambda: on_black.write_to_file(str(out / f"{out_id}-composite-on-black.png"), compression=PNG_COMPRESSION),
            lambda: with_fixed.write_to_file(str(out / f"{out_id}-composite-with-fixed.png"), compression=PNG_COMPRESSION),
        ]
        with ThreadPoolExecutor(max_workers=len(writes)) as pool:
            for done in [pool.submit(w) for w in writes]:
                done.result()
    write_layers({"image": map_png.name, "rate": [1.0, 1.0], "geo": geo_path.name})
    log(f"  composites: {out_id}-composite.png, -composite-on-black.png, -composite-with-fixed.png ({mw}x{mh} px)")
