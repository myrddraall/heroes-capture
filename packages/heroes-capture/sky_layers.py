"""How fast the map's own sky layers move against the map, for a parallax viewer.

A sky model on the parallax layer is a set of shells far below the map: when the camera pans,
it moves across the screen slower than the map does. The rate (sky shift / map shift: 1 moves
with the map, 0 stays put) is what a viewer needs to move each layer with the view. Measured,
not configured: the camera is put at the middle of the map and at two offsets, and at each the
map is shot, then (near clip past the map, so only the sky is drawn, at speed 1, which freezes
it in the same position every time) each sky layer; phase correlation gives the shifts. The
fixed skybox moves with the camera (rate 0 by definition).

Layers: "parallax", the map's parallax model as it is (background art and haze together); and
"haze", the keyed copy over black, when the map has one (sky.py PARALLAX_KEYS), which shows the
haze shells alone. Written to <id>/sky-layers.json.
"""

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from frames import save_frame
from game_control import settle, step
from runlog import log

SKY_SETTLE = 0.1  # real seconds for a sky swap to be drawn before its shot (was 1; the waits probe found shots within one level down to 0.05)
MAP_SETTLE = 0.1  # real seconds after a camera move before a shot of the map (as the tiles' settle)
NEAR_CLIP_SHARE = 1.2  # the near clip, as a share of the camera distance: past the map, before the sky shells
OFFSETS = ((15.0, 0.0), (0.0, 6.0))  # camera offsets in map cells: across, then up (shifts well under half a screen)


def phase_offset(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """(dx, dy, strength): how far the content of `b` moved from where it is in `a`, in screen
    pixels, to a fraction of a pixel (phase correlation at half size). Strength near 1 is a
    clean match; under about 0.05 there was nothing to match."""
    def prepare(f: np.ndarray) -> np.ndarray:
        g = f[::2, ::2].astype(np.float32).mean(axis=2)
        g -= g.mean()
        return g * np.outer(np.hanning(g.shape[0]), np.hanning(g.shape[1])).astype(np.float32)

    pa, pb = prepare(a), prepare(b)
    cross = np.fft.rfft2(pb) * np.conj(np.fft.rfft2(pa))
    cross /= np.abs(cross) + 1e-9
    corr = np.fft.irfft2(cross, s=pa.shape)
    y, x = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = corr.shape

    def refine(before: float, peak: float, after: float) -> float:
        denom = before - 2 * peak + after
        return 0.5 * (before - after) / denom if denom else 0.0

    fy = y + refine(corr[(y - 1) % h, x], corr[y, x], corr[(y + 1) % h, x])
    fx = x + refine(corr[y, (x - 1) % w], corr[y, x], corr[y, (x + 1) % w])
    dy = fy - h if fy > h / 2 else fy
    dx = fx - w if fx > w / 2 else fx
    return float(dx) * 2, float(dy) * 2, float(corr.max())


AXES_AGREE = 0.1  # a shell's rates across and down agree to within this share (the background art: 0.4574, 0.4596)


def consistent(name: str, rate: list, strength: list) -> None:
    """A sky shell moves at one rate across and down, and slower than the map (a rate of 1 or more
    is impossible for a layer behind it). Soft haze over black can match falsely (strength 0.01: a
    rate of 1.28 once, where the other axis gave 0.476 at 0.032): when an axis's rate is impossible,
    or the two disagree by more than AXES_AGREE, the axis with the stronger match stands for both.
    In place."""
    usable = [0 < r < 1 if r is not None else False for r in rate]
    if usable == [True, True] and abs(rate[0] - rate[1]) <= AXES_AGREE * max(rate):
        return
    if not any(usable):
        return
    best = max((axis for axis in (0, 1) if usable[axis]), key=lambda axis: strength[axis] or 0.0)
    log(f"  sky layers: {name}'s rates {rate} disagree (matches {strength}); taking axis {'xy'[best]}'s {rate[best]} for both")
    rate[0] = rate[1] = rate[best]


def measure(session, manifest: dict, out_dir: Path, area: dict | None = None) -> dict | None:
    """Measure the sky layers' rates (see the module notes) and write sky-layers.json; None when
    the map has no parallax sky. Leaves the scene as the next `tile` expects it (that command
    puts back the capture camera and our sky)."""
    sky = manifest.get("sky") or {}
    models = sky.get("mapSky") or {}
    if not models.get("parallax"):
        return None
    clip = round(manifest["distance"] * NEAR_CLIP_SHARE)
    layers = {"parallax": "sky mapparallax 1"}
    if sky.get("keys"):
        layers["haze"] = "sky parallaxblack 1"
    area = area or manifest["area"]  # the camera bounds the game applies, when the caller measured them
    centre = ((area["left"] + area["right"]) / 2, (area["bottom"] + area["top"]) / 2)
    log(f"measuring the sky layers' parallax ({', '.join(layers)}; near clip {clip}) ...")

    def shoot(x: float, y: float) -> dict | None:
        """The map and each sky layer, with the camera at (x, y). Each layer is put up anew at
        every position: at speed 1 it then shows the same frozen moment each time (left up while
        the camera moved, the haze drifted enough to skew its rate)."""
        answer = session.send(f"tile 0 {x:.2f} {y:.2f}", timeout=3.0)
        if answer is None:
            return None
        settle(MAP_SETTLE)
        frames = {"map": session.grab(dark_ok=True), "camera": (answer[0].camera_x, answer[0].camera_y)}
        if session.send(f"hidemap {clip}") is None:  # map clipped away, sky frozen, our sky down
            return None
        for name, command in layers.items():
            if session.send(command) is None:
                return None
            settle(SKY_SETTLE)  # the swap drawn
            frames[name] = session.grab(dark_ok=True)
        # The map's own parallax back in its slot (never left empty).
        session.send("sky mapparallax 1")
        return frames

    base = shoot(*centre)
    if base is None:
        log("  sky layers: the map didn't answer; not measured")
        return None
    result = {"cameraDistance": manifest["distance"], "nearClip": clip, "measured": time.strftime("%Y-%m-%d %H:%M"),
              "layers": {"fixed": {"model": models.get("fixed"), "rate": [0.0, 0.0], "note": "moves with the camera"}}}
    rates: dict[str, list] = {name: [None, None] for name in layers}
    strengths: dict[str, list] = {name: [None, None] for name in layers}
    scale: list = [None, None]  # the map's pixels per cell on screen, from the same shots
    for axis, (ox, oy) in enumerate(OFFSETS):
        moved = shoot(centre[0] + ox, centre[1] + oy)
        if moved is None:
            log(f"  sky layers: no answer at offset {ox, oy}; that axis not measured")
            continue
        map_dx, map_dy, map_strength = phase_offset(base["map"], moved["map"])
        map_shift = map_dx if axis == 0 else map_dy
        if abs(map_shift) < 20 or map_strength < 0.05:
            log(f"  sky layers: the map didn't move measurably ({map_shift:.1f} px, strength {map_strength:.2f}); axis {axis} skipped")
            continue
        moved_cells = moved["camera"][axis] - base["camera"][axis]
        if abs(moved_cells) > 1:
            scale[axis] = round(abs(map_shift / moved_cells), 3)
        for name in layers:
            dx, dy, strength = phase_offset(base[name], moved[name])
            rates[name][axis] = round((dx if axis == 0 else dy) / map_shift, 4)
            strengths[name][axis] = round(strength, 3)
        log(f"  axis {'xy'[axis]}: camera moved {moved['camera'][axis] - base['camera'][axis]:.2f} cells, the map {map_shift:.1f} px; "
            + ", ".join(f"{n} {rates[n][axis]} (match {strengths[n][axis]})" for n in layers))
    for name in layers:
        consistent(name, rates[name], strengths[name])
        known = [r for r in rates[name] if r is not None]
        layer = {"model": models["parallax"] if name == "parallax" else f"{models['parallax']} (haze, keyed copy)",
                 "rate": rates[name], "matchStrength": strengths[name]}
        if known and np.mean(known) > 0:
            layer["depth"] = round(manifest["distance"] / float(np.mean(known)), 1)  # from the camera, like cameraDistance
        result["layers"][name] = layer
    result["mapPxPerCell"] = scale
    path = out_dir / "sky-layers.json"
    path.write_text(json.dumps(result, indent=2))
    log("  sky layers: " + "; ".join(f"{n} rate {result['layers'][n]['rate']}" for n in layers) + f" -> {path.name}")
    return result


SKY_KEEP = 0.8  # the share of a screen the sky moves between neighbouring camera positions (0.6 gave 20 positions on Battlefield of Eternity, 0.8 gives 12; the shots must overlap for sky_stitch to fit the shells' tilt)
FIXED_CLIP = 600  # a near clip past the parallax shells and short of the fixed skybox (which survives 1000)


def capture(session, manifest: dict, out_dir: Path, measured: dict | None, keep: float = SKY_KEEP, folder_name: str = "sky", area: dict | None = None) -> None:
    """The sky layers as images, for a parallax viewer: the camera steps across the map (steps
    sized so the sky, moving at its measured rate, moves `keep` of a screen between
    positions), the map clipped away, and at each position the keyed copies of the parallax
    model are shot (sky.py KEY_VARIANTS: the background art alone; white without haze; the
    haze over white; over black), then the background art over the map's own fixed skybox
    (where the art lets it through). The fixed skybox once, alone. Into <id>/sky/, with
    positions.json; stitch.py makes the layers and composites from them."""
    sky = manifest.get("sky") or {}
    if not sky.get("keys") or not measured:
        return
    # Per axis, the fastest layer's rate: the sky that moves most between positions sets the step.
    rates = [layer.get("rate") or [None, None] for name, layer in measured["layers"].items() if name != "fixed"]
    rate_xy = [max([r[axis] for r in rates if r[axis]], default=None) for axis in (0, 1)]
    scale = [s for s in measured.get("mapPxPerCell", []) if s]
    if not any(rate_xy) or not scale:
        log("  sky layer images: no measured rate; skipped")
        return
    rate_xy = [r or max(r2 for r2 in rate_xy if r2) for r in rate_xy]  # an axis not measured: the other's
    scale = float(np.mean(scale))
    folder = out_dir / folder_name
    folder.mkdir(exist_ok=True)
    for old in [*folder.glob("*.png"), *folder.glob("*.npy")]:
        old.unlink()
    area = area or manifest["area"]
    screen = manifest["screen"]

    def spread(lo: float, hi: float, view: float, rate: float) -> list[float]:
        step_cells = keep * view / (rate * scale)
        count = max(1, math.ceil((hi - lo) / step_cells) + 1)
        return [lo + (hi - lo) * k / (count - 1) for k in range(count)] if count > 1 else [(lo + hi) / 2]

    xs = spread(area["left"], area["right"], screen["w"], rate_xy[0])
    ys = spread(area["bottom"], area["top"], screen["h"], rate_xy[1])
    clip = measured["nearClip"]
    log(f"sky layer images: {len(xs)}x{len(ys)} camera positions, near clip {clip} ...")
    saver = ThreadPoolExecutor(max_workers=2)
    record = {"keep": keep, "rateUsedForSteps": rate_xy, "mapPxPerCell": scale, "nearClip": clip, "screen": screen, "positions": {}}

    def save(frame: np.ndarray, name: str) -> None:
        saver.submit(save_frame, folder / name, frame)

    def at(x: float, y: float, near: float) -> tuple[float, float] | None:
        """The camera to (x, y), the map clipped away at `near`, our sky down."""
        answer = session.send(f"tile 0 {x:.2f} {y:.2f}", timeout=3.0)
        if answer is None:
            return None
        if session.send(f"hidemap {near}") is None:  # map clipped away, sky frozen, our sky down
            return None
        return answer[0].camera_x, answer[0].camera_y

    def fixed_alone() -> None:
        if at(xs[0], ys[0], FIXED_CLIP) is None or session.send("sky mapsky 0") is None:
            raise RuntimeError("the map didn't answer")
        settle(SKY_SETTLE)
        save(session.grab(dark_ok=True), "fixed")

    try:
        step(fixed_alone, "the fixed skybox")
    except RuntimeError as e:
        log(f"  the fixed skybox: {e}; no sky layer images this run")
        step(lambda: session.send("sky mapparallax 1"), "putting the map's own parallax back")
        saver.shutdown(wait=True)
        return
    n = 0
    for y in reversed(ys):  # north first, like the tiles
        for x in xs:
            name = f"p{n:03d}"

            def one() -> None:
                camera = at(x, y, clip)
                if camera is None:
                    raise RuntimeError("the map didn't answer")
                for variant in ("bare", "whitebare", "white", "black"):
                    if session.send(f"sky parallax{variant} 1") is None:
                        raise RuntimeError("the map didn't answer")
                    settle(SKY_SETTLE)  # drawn; set anew at speed 1, the haze is at the same frozen moment each time
                    save(session.grab(dark_ok=True), f"{name}-{variant}")
                # The background art over the map's own fixed skybox: where it lets the skybox through.
                for command in ("sky mapsky 0", "sky parallaxbare 1"):
                    if session.send(command) is None:
                        raise RuntimeError("the map didn't answer")
                settle(SKY_SETTLE)
                save(session.grab(dark_ok=True), f"{name}-bareoverfixed")
                record["positions"][name] = {"camera": list(camera)}

            try:
                step(one, f"sky layer images {n + 1}/{len(xs) * len(ys)}")
            except RuntimeError as e:
                log(f"  sky position {n + 1}: {e}; skipped")
            n += 1
    step(lambda: session.send("sky mapparallax 1"), "putting the map's own parallax back")
    saver.shutdown(wait=True)
    (folder / "positions.json").write_text(json.dumps(record, indent=2))
    log(f"  sky layer images: {len(record['positions'])} positions -> {folder.name}/")
