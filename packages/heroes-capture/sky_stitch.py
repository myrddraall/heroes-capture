"""The map's own sky layers as images, and composites with the map (from capture.py's sky/ shots).

Layers, written next to the map image:
  <id>-layer-fixed.png       the fixed skybox: it moves with the camera, so it is one screen-sized
                             backdrop for a viewer, not part of the map
  <id>-layer-background.png  the parallax model's background art, with transparency where it lets
                             the fixed skybox through
  <id>-layer-haze.png        the parallax model's haze, matted (colour and transparency)
  <id>-layer-map.png         the map (the same image as <id>.png)
  <id>-composite.png         background, haze and map together, transparent where none of them is
  <id>-composite-on-black.png  the haze and the map over black (no background art, no fixed skybox)
  <id>-composite-with-fixed.png  the same over the fixed skybox, stretched behind everything like a
                             backdrop (opaque, as the skybox is)
  <id>-layers.json           how a viewer places each layer: its rate (how fast it moves against
                             the map) and which of its pixels is behind which map cell

Each sky shot is matted on its own (the haze from its shots over white and black, with the white
level the game's lighting gives the key taken from the white-without-haze shot; the background
art from its shots over black and over the fixed skybox), then the shots are blended into one
image per layer, each placed by its camera position times the layer's measured rate.

The composites are at map scale, with each map point showing the sky the game draws behind it
when the camera is centred on it: the sky layers stretched by 1/rate about the middle camera
position.
"""

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pyvips
from PIL import Image

from frames import frame_exists, load_frame
from runlog import log, stage
from workers import ordered_map

PNG_COMPRESSION = 1  # zlib level for the big PNGs: much faster to write than 6, somewhat larger files


def _load(stem: Path) -> np.ndarray:
    return load_frame(stem).astype(np.float32)


def _mean3(a: np.ndarray) -> np.ndarray:
    return (a[:, :, 0] + a[:, :, 1] + a[:, :, 2]) / 3.0


def _haze(white: np.ndarray, black: np.ndarray, level: np.ndarray) -> np.ndarray:
    """RGBA (float, 0..1 alpha) of the haze: alpha from how much the background shows through,
    the difference between the shots over white and over black against the white level."""
    alpha = np.clip(1.0 - _mean3(white - black) / np.maximum(_mean3(level), 1.0), 0.0, 1.0)
    alpha[alpha < 1 / 255] = 0.0
    colour = np.where(alpha[..., None] > 0, np.clip(black / np.maximum(alpha, 1 / 255)[..., None], 0, 255), 0)
    return np.dstack([colour, alpha])


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


def _blend(frames, size: tuple[int, int], width: int, height: int, left: int) -> np.ndarray:
    """RGBA frames (float) placed at (x, y) on one canvas, blended with weights falling off
    towards each frame's edges (premultiplied, so transparent parts don't darken the rest).
    `frames` yields (rgba, x, y), one at a time; `size` is each frame's (height, width). The
    first `left` columns of every frame (the status strip) are left out."""
    colour = np.zeros((height, width, 3), np.float32)
    alpha = np.zeros((height, width), np.float32)
    weight = np.zeros((height, width), np.float32)
    h, w = size
    wx = np.zeros(w, np.float32)
    wx[left:] = np.sin(np.pi * (np.arange(left, w) - left + 0.5) / (w - left))
    wy = np.sin(np.pi * (np.arange(h) + 0.5) / h).astype(np.float32)
    window = wy[:, None] * wx[None, :]
    for rgba, x, y in frames:
        a = rgba[..., 3] * window
        colour[y : y + h, x : x + w] += rgba[..., :3] * a[..., None]
        alpha[y : y + h, x : x + w] += a
        weight[y : y + h, x : x + w] += window
    out_alpha = np.where(weight > 0, alpha / np.maximum(weight, 1e-6), 0)
    out_colour = np.where(alpha[..., None] > 1e-6, colour / np.maximum(alpha, 1e-6)[..., None], 0)
    return np.dstack([out_colour, out_alpha * 255]).round().clip(0, 255).astype(np.uint8)


def build(manifest: dict, base: Path) -> None:
    """The layer images and composites, when capture.py left sky shots in <id>/sky/."""
    folder = base / "sky"
    record_path, measured_path = folder / "positions.json", base / "sky-layers.json"
    if not record_path.exists() or not measured_path.exists():
        return
    record = json.loads(record_path.read_text())
    measured = json.loads(measured_path.read_text())
    names = sorted(record["positions"])
    if not names or not frame_exists(folder / "fixed"):
        log("  sky layers: no shots; skipped")
        return
    out_id = manifest["id"]
    out = base.parent
    scale = record["mapPxPerCell"]  # the map's screen pixels per cell during the capture
    left = int((manifest.get("status") or {}).get("pageLeft", 0))
    fixed = _load(folder / "fixed")
    height, width = fixed.shape[:2]
    cameras = np.array([record["positions"][n]["camera"] for n in names])
    centre = cameras.mean(axis=0)
    log(f"sky layers: {len(names)} shots")

    def rate_of(layer: str) -> tuple[float, float]:
        known = (measured["layers"].get(layer) or measured["layers"]["parallax"])["rate"]
        fallback = [r for r in known if r] or [record["rateUsedForSteps"]]
        return tuple(float(r) if r else float(np.mean(fallback)) for r in known)

    # The status strip's column (the first `left` columns of every shot) is cut off each shot, so
    # the layer images have no empty strip along their edge.
    middle_x = width / 2 - left  # the screen's middle, in a shot with the strip's column cut off
    width -= left
    placements = {}
    with stage("sky layer images"):
        for layer, make in (("background", lambda n: _background(_load(folder / f"{n}-bare"), _load(folder / f"{n}-bareoverfixed"), fixed)),
                            ("haze", lambda n: _haze(_load(folder / f"{n}-white"), _load(folder / f"{n}-black"), _load(folder / f"{n}-whitebare")))):
            kx, ky = rate_of("parallax" if layer == "background" else "haze")
            # Camera east moves the sky left on screen, camera north moves it down; so in the layer's
            # image the shot from a camera further east sits further right, further north higher up.
            ox = kx * scale * (cameras[:, 0] - centre[0])
            oy = -ky * scale * (cameras[:, 1] - centre[1])
            x0, y0 = ox.min(), oy.min()
            px = np.round(ox - x0).astype(int)
            py = np.round(oy - y0).astype(int)
            canvas_w, canvas_h = int(px.max()) + width, int(py.max()) + height  # from the rounded places: no empty edge
            frames = ((rgba[:, left:], int(px[k]), int(py[k])) for k, rgba in enumerate(ordered_map(make, names)))
            image = _blend(frames, (height, width), canvas_w, canvas_h, 0)
            path = out / f"{out_id}-layer-{layer}.png"
            Image.fromarray(image).save(path, compress_level=PNG_COMPRESSION)
            # The layer's pixel behind the screen's middle with the camera at `centre`.
            placements[layer] = {"image": path.name, "width": canvas_w, "height": canvas_h, "rate": [kx, ky],
                                 "centreCell": [float(centre[0]), float(centre[1])],
                                 "centrePixel": [middle_x - x0, height / 2 - y0],
                                 "pxPerMapCell": [kx * scale, ky * scale]}
            log(f"  {layer}: {canvas_w}x{canvas_h} px, rate {kx:.4f}, {ky:.4f} -> {path.name}")
    Image.fromarray(fixed[:, left:].astype(np.uint8)).save(out / f"{out_id}-layer-fixed.png", compress_level=PNG_COMPRESSION)

    map_png = out / f"{out_id}.png"
    geo_path = out / f"{out_id}.geo.json"
    if not map_png.exists() or not geo_path.exists():
        log("  sky layers: no single map image (a map of several arenas?); no composites")
        return
    shutil.copyfile(map_png, out / f"{out_id}-layer-map.png")
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
            lambda: composite.flatten(background=[40, 40, 40]).thumbnail_image(2048).write_to_file(str(out / f"{out_id}-composite-preview.jpg"), Q=88),
            lambda: on_black.write_to_file(str(out / f"{out_id}-composite-on-black.png"), compression=PNG_COMPRESSION),
            lambda: on_black.thumbnail_image(2048).write_to_file(str(out / f"{out_id}-composite-on-black-preview.jpg"), Q=88),
            lambda: with_fixed.write_to_file(str(out / f"{out_id}-composite-with-fixed.png"), compression=PNG_COMPRESSION),
            lambda: with_fixed.thumbnail_image(2048).write_to_file(str(out / f"{out_id}-composite-with-fixed-preview.jpg"), Q=88),
        ]
        with ThreadPoolExecutor(max_workers=len(writes)) as pool:
            for done in [pool.submit(w) for w in writes]:
                done.result()
    (out / f"{out_id}-layers.json").write_text(json.dumps({
        "map": {"image": f"{out_id}-layer-map.png", "rate": [1.0, 1.0], "geo": geo_path.name},
        "fixed": {"image": f"{out_id}-layer-fixed.png", "rate": [0.0, 0.0], "note": "moves with the camera: a screen backdrop"},
        **placements,
        "howTo": "A layer pixel for map cell (x, y) seen with the camera there: centrePixel + pxPerMapCell * ((x, y) - centreCell), y flipped (image y grows south). A viewer panning by d map cells moves a layer by rate * d.",
    }, indent=2))
    log(f"  composites: {out_id}-composite.png, -composite-on-black.png, -composite-with-fixed.png ({mw}x{mh} px)")
