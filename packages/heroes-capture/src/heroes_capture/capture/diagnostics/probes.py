"""Diagnostic runs instead of the tiles (`map render <map> --probe-<name>`, PROBES): chosen views
and command sequences, a shot after each, kept in a probe-<name>-<time> folder next to the tiles.
The helpers are what the probes so far had in common, kept for the next one: a run's scaffold
(probe_folder, finish), the camera over a spot (look_at; diagnostics.crop_round the screen round it, diagnostics.change_map what
changed between two shots), and the frames that follow a command (frames_after).
"""

import math
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image

from heroes_capture.capture import sky_layers
from heroes_capture.common.game_control import settle, step
from heroes_capture.common.game_state import interface_height
from heroes_capture.common.game_window import game_region
from heroes_capture.common.jsonio import write_json
from heroes_capture.common.runlog import done, log, stage, warn
from heroes_capture.map.match import quit_match, send_command
from heroes_capture.map.status import CELL_UNITS, ROWS


def _save(frame: np.ndarray, path: Path) -> None:
    Image.fromarray(np.ascontiguousarray(frame)).save(path, compress_level=1)


def probe_folder(out: Path, name: str) -> Path:
    """The probe's folder next to the tiles: probe-<name>-<time>."""
    folder = out.parent / f"probe-{name}-{time.strftime('%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def finish(run, what: str, folder: Path) -> None:
    """The probe's work (redone after a lost focus, as every step), the match left, the folder named."""
    step(run, what)
    quit_match()
    done(f"probe results in {folder}")


def look_at(session, spot: dict, wait: float = 0.5) -> dict:
    """The capture camera straight above a cell ("el at": the lighting refitted there), then where
    the camera really is (the strip; the spot itself if it can't be read)."""
    session.command(f"el at {spot['x']:.2f} {spot['y']:.2f}")
    settle(wait)
    return session.camera({"x": spot["x"], "y": spot["y"]})


def frames_after(session, text: str, seconds: float, crop=lambda frame: frame) -> list[tuple[float, np.ndarray]] | None:
    """A command, then every frame for `seconds` after the strip acknowledges it, each with its
    time after the acknowledgement (the first: the frame that showed it). How soon what the command
    does is on screen (a side switch: 47-212 ms, on one of the game's next simulation ticks).
    None when the map doesn't answer."""
    answer = session.send(text, timeout=5.0)
    if answer is None:
        warn(f"  no answer to \"{text}\"")
        return None
    acked = time.time()
    frames = [(0.0, crop(answer[1]))]
    while time.time() < acked + seconds:
        frames.append((time.time() - acked, crop(session.raw_grab())))
    return frames


def probe_monitor(session, manifest: dict, out: Path) -> None:
    """Does the tool work on this monitor, at its resolution? Run with `--monitor <n>`, once per
    monitor. The start-up has already happened at that resolution (the strip found, the map
    ready, the first tile, the camera bounds); this records what it found, in the log and in
    probe-monitor-<WxH>-<time>/: report.json, the strip's column at full size (edge.png) and the
    whole screen at half size (frame.png). The checks: the window took the monitor's size and
    place; the manifest planned for that size; the strip's cells are the size the height predicts
    (25 x 15 interface units, 1200 units to the screen's height; the second column is directly
    right of the first); the first tile's camera is where it was sent."""
    from heroes_capture.common.monitors import monitors

    region = game_region()
    w, h = region["width"], region["height"]
    probe_dir = out.parent.parent / f"probe-monitor-{w}x{h}-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(parents=True, exist_ok=True)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, ok, detail))
        (log if ok else warn)(f"  monitor probe: {name}: {'ok' if ok else 'NOT OK'} ({detail})")

    rect = (region["left"], region["top"], w, h)
    on = next((m for m in monitors() if m.game_rect() == rect), None)
    letterboxed = on is not None and on.game_rect() != (on.left, on.top, on.width, on.height)
    log(f"  monitor probe: the game's window is {w}x{h} at ({region['left']}, {region['top']})"
        + (f", monitor {on.describe()}" + (" (letterboxed: the monitor is narrower than the game's 5:4)" if letterboxed else "") if on
           else ", not a monitor's rectangle nor the game's 5:4 picture on one"))
    check("window fills a monitor (or its 5:4 picture of one)", on is not None, f"{w}x{h} at ({region['left']}, {region['top']})")
    planned = (manifest["screen"]["w"], manifest["screen"]["h"])
    check("grid planned for this size", planned == (w, h), f"planned {planned[0]}x{planned[1]}, window {w}x{h}")
    strip = session.strip
    # The cells: 25 x 15 interface units; the interface is 1200 units tall (or 1600 wide on a
    # screen narrower than 4:3: interface_height).
    unit = interface_height(w, h) / 1200
    expect_w, expect_h = CELL_UNITS[0] * unit, CELL_UNITS[1] * unit
    cells_ok = abs(strip.pitch_y - expect_h) <= 1.0 and abs(strip.cell_w - expect_w) <= 1.0
    check("strip cells the size the height predicts", cells_ok,
          f"cells {strip.cell_w}x{strip.cell} px, row pitch {strip.pitch_y:.2f}; expected {expect_w:.2f}x{expect_h:.2f} ({unit:.3f} px per interface unit"
          + (", scaled to the width: the screen is narrower than 4:3)" if interface_height(w, h) < h else ")"))
    check("strip's second column directly right of the first", abs(strip.pitch_x - strip.cell_w) <= 1.0,
          f"column pitch {strip.pitch_x:.2f}, cell width {strip.cell_w}")
    check("strip fits the screen's height", strip.origin[1] + ROWS * strip.pitch_y <= h,
          f"{ROWS} rows from y={strip.origin[1]} end at {strip.origin[1] + ROWS * strip.pitch_y:.0f}, screen {h} tall")
    log(f"  monitor probe: {session.strip_width} px blanked at the left edge; {manifest['pxPerCell']} px per cell planned")
    # The camera sent to the middle tile (where no bound can clamp it) and read back through the
    # strip; then to a corner tile, logged only: how far the bounds clamp it there says how the
    # planned grid fits this screen's shape.
    tiles = manifest["tiles"]
    mid_x = (min(t["x"] for t in tiles) + max(t["x"] for t in tiles)) / 2
    mid_y = (min(t["y"] for t in tiles) + max(t["y"] for t in tiles)) / 2
    middle = min(tiles, key=lambda t: abs(t["x"] - mid_x) + abs(t["y"] - mid_y))
    corner = min(tiles, key=lambda t: (t["x"], t["y"]))

    def camera_at(tile: dict) -> tuple[float, float] | None:
        """The camera once the map acknowledges the tile (it has moved and been clamped by then)."""
        answer = session.send(f"tile {tile['index']} {tile['x']:.2f} {tile['y']:.2f}", timeout=3.0)
        return (answer[0].camera_x, answer[0].camera_y) if answer else None

    camera = camera_at(middle)
    if camera:
        off = math.hypot(camera[0] - middle["x"], camera[1] - middle["y"])
        check("the camera goes where a tile sends it", off <= 0.5,
              f"middle tile sent to ({middle['x']:.2f}, {middle['y']:.2f}), camera at ({camera[0]:.2f}, {camera[1]:.2f}), {off:.2f} cells off")
    else:
        check("the camera goes where a tile sends it", False, "the strip couldn't be read")
    at_corner = camera_at(corner)
    if at_corner:
        log(f"  monitor probe: corner tile sent to ({corner['x']:.2f}, {corner['y']:.2f}), camera at ({at_corner[0]:.2f}, {at_corner[1]:.2f}), "
            f"{math.hypot(at_corner[0] - corner['x'], at_corner[1] - corner['y']):.2f} cells off (the bounds clamp it there)")
    frame = session.raw_grab()
    _save(np.ascontiguousarray(frame[:, : max(session.strip_width, 1) + 32]), probe_dir / "edge.png")
    Image.fromarray(np.ascontiguousarray(frame)).reduce(2).save(probe_dir / "frame.png")
    report = {
        "window": region, "monitor": on.describe() if on else None, "planned": {"w": planned[0], "h": planned[1]},
        "strip": {"origin": list(strip.origin), "cell": [strip.cell_w, strip.cell], "pitch": [strip.pitch_x, strip.pitch_y], "blanked": session.strip_width},
        "pxPerCell": manifest["pxPerCell"], "fov": manifest.get("fov"), "tiles": len(tiles),
        "camera": {"middle": {"sent": [middle["x"], middle["y"]], "at": camera}, "corner": {"sent": [corner["x"], corner["y"]], "at": at_corner}},
        "checks": [{"check": n, "ok": ok, "detail": d} for n, ok, d in checks],
    }
    write_json(probe_dir / "report.json", report, indent=2)
    failed = [n for n, ok, _ in checks if not ok]
    (done if not failed else warn)(f"monitor probe at {w}x{h}: {len(checks) - len(failed)} of {len(checks)} checks ok"
                                   + (f"; not ok: {', '.join(failed)}" if failed else "") + f"; report in {probe_dir}")
    with stage("leaving"):
        quit_match()


def probe_sky(session, manifest: dict, out: Path) -> None:
    """Do the solid-colour skyboxes show, and are they flat? An edge tile (left edge, middle
    row: half map, half sky) over each colour in turn; the sky part of each shot is measured
    (mean colour and spread) and the shots are kept. HRS_SKY_SEQUENCE scripts the swaps:
    "command:wait|..." with commands "<colour> <layer>" (layer 0 is the camera-fixed skybox,
    1 the terrain-relative parallax layer); the same command twice in a row only waits longer,
    which tells a slow transition from a failed swap."""
    tiles = manifest["tiles"]
    probe_dir = probe_folder(out, "sky")
    edge = min(tiles, key=lambda t: t["col"] * 1000 + abs(t["y"] - (manifest["area"]["top"] + manifest["area"]["bottom"]) / 2))

    def shot(name: str) -> None:
        frame = session.grab(dark_ok=True)
        if frame is None:
            log(f"  {name}: no frame")
            return
        _save(frame, probe_dir / f"{name}.png")
        # The left fifth (sky, if the tile sits on the edge) and a patch of open sky further in.
        h, w = frame.shape[:2]
        parts = []
        for what, part in (("left fifth", frame[:, : w // 5]), ("sky patch", frame[: h // 3, w // 4 : w // 4 + w // 8])):
            px = part.reshape(-1, 3).astype(np.float32)
            parts.append(f"{what} {tuple(int(v) for v in px.mean(axis=0))} ±{tuple(round(float(v), 1) for v in px.std(axis=0))}")
        log(f"  {name}: " + "; ".join(parts))

    def run() -> None:
        send_command(f"tile {edge['index']} {edge['x']:.2f} {edge['y']:.2f}")
        settle(2.0)
        send_command("clean")
        settle(0.5)
        sequence = os.environ.get("HRS_SKY_SEQUENCE") or "start:0|black 0:2.5|lightgrey 0:2.5|none 0:2.5"
        previous = None
        for k, item in enumerate(sequence.split("|")):
            command, wait = item.rsplit(":", 1)
            if command not in ("start", previous):
                send_command(f"sky {command}")
            settle(float(wait))
            shot(f"{k:02d}-{command.replace(' ', '-layer')}-{wait}s")
            previous = command

    finish(run, "the skybox probe", probe_dir)


# The sky reach probe's lenses, in degrees, after the render's own (the camera can't go far past the
# camera bounds: the game stops it short of the map's edges, so a wider lens is what sees further).
SKY_REACH_FOVS = (16.0, 32.0)


def probe_sky_reach(session, manifest: dict, out: Path) -> None:
    """How much of the map's parallax sky there is to shoot (the sky layers' pictures are
    trapezoids, from the shots inside the camera bounds). The bounds lifted ("unbound"), the camera
    sent past the map's corners and edges (the game stops it where it may go) and to the middle; at
    each, the map clipped away as the sky shots have it, and with the render's lens and each of
    SKY_REACH_FOVS ("fov"), three shots: the background art alone ("bare", over nothing: black
    where the art ends), the light grey alone, and the haze over it. Logged per shot: where the
    camera went, the share of the screen the art fills and the share the haze reaches (where it
    differs from the light grey alone), and the share of art along each side of the screen (where
    the art ends). The shots kept at a quarter size."""
    probe_dir = probe_folder(out, "sky-reach")
    left = int(manifest["status"].get("pageLeft", 0))
    area = manifest["area"]
    size = manifest["mapSize"]

    def shot(name: str) -> np.ndarray | None:
        frame = session.grab(dark_ok=True)
        if frame is None:
            log(f"  {name}: no frame")
            return None
        h, w = frame.shape[:2]
        Image.fromarray(np.ascontiguousarray(frame)).resize((w // 4, h // 4)).save(probe_dir / f"{name}.png", compress_level=1)
        return frame[:, left:].astype(np.int16)

    def sides(art: np.ndarray) -> str:
        """The share of art in a band a twentieth of the screen deep along each side."""
        h, w = art.shape
        bands = {"N": art[: h // 20], "S": art[-h // 20 :], "W": art[:, : w // 20], "E": art[:, -w // 20 :]}
        return " ".join(f"{k} {v.mean() * 100:3.0f}%" for k, v in bands.items())

    def run() -> None:
        measured = sky_layers.measure(session, manifest, out.parent, area)
        clip = measured["nearClip"] if measured else round(manifest["distance"] * sky_layers.NEAR_CLIP_SHARE)
        if session.send("unbound") is None:
            raise RuntimeError("the map didn't answer")
        mid_x, mid_y = (area["left"] + area["right"]) / 2, (area["bottom"] + area["top"]) / 2
        xs, ys = [-50.0, mid_x, size["width"] + 50.0], [-50.0, mid_y, size["height"] + 50.0]
        fovs = [float(manifest.get("fov") or 8.0), *SKY_REACH_FOVS]
        log(f"sky reach probe: bounds {area}, map {size}, near clip {clip}; camera x {xs}, y {ys}; lenses {fovs}")
        for y in reversed(ys):  # north first
            for x in xs:
                status = session.move_camera(x, y)
                if status is None or session.send(f"hidemap {clip}") is None:
                    log(f"  ({x:.0f}, {y:.0f}): the map didn't answer")
                    continue
                cam = (status.camera_x, status.camera_y)
                for fov in fovs:
                    session.send(f"fov {fov:g}")
                    name = f"x{x:.0f}-y{y:.0f}-fov{fov:g}"
                    frames = {}
                    for variant in ("bare", "lightbare", "light"):
                        session.send(f"sky parallax{variant} 1")
                        settle(sky_layers.SKY_SETTLE)
                        frames[variant] = shot(f"{name}-{variant}")
                    if any(f is None for f in frames.values()):
                        continue
                    art = frames["bare"].max(axis=2) > 8
                    haze = float((np.abs(frames["light"] - frames["lightbare"]).max(axis=2) > 3).mean())
                    log(f"  camera at ({cam[0]:.1f}, {cam[1]:.1f}), lens {fov:g}: art fills {art.mean() * 100:5.1f}% "
                        f"(along the sides: {sides(art)}), haze reaches {haze * 100:5.1f}%")
        session.send("sky mapparallax 1")

    finish(run, "the sky reach probe", probe_dir)


# Every probe, declared once: its name (`--probe-<name>`) -> what it runs, and what it does (the
# capture's help and the line it logs before it starts).
PROBES = {
    "monitor": (probe_monitor, "the start-up at this monitor's resolution (with --monitor), then a report on the window, the strip's cells and the first tile; run once per monitor"),
    "sky": (probe_sky, "one edge tile over each solid-colour skybox, to check the colour shows and is flat"),
    "sky-reach": (probe_sky_reach, "the camera as far past the camera bounds as it goes, the sky shot there with the render's lens and wider ones: how much parallax sky there is"),
}
