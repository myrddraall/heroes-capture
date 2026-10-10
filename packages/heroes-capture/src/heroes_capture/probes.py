"""Diagnostic runs (capture.py --probe-light, --probe-sky, --probe-waits, --probe-elements): instead of the tiles, chosen views and
command sequences, a shot after each, kept in a probe-<what>-<time> folder next to the tiles.
"""

import json
import math
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import sky_layers
from .elements import changed, screen_point
from .game_control import quit_match, send_command, settle, step
from .runlog import done, log, warn
from .screen import changed_share


def _save(frame: np.ndarray, path: Path) -> None:
    Image.fromarray(np.ascontiguousarray(frame)).save(path, compress_level=1)


def probe_light(session, manifest: dict, out: Path) -> None:
    """The render's own commands at chosen spots, to see what each does to the picture.

    HRS_PROBE_POINTS="x,y;x,y" (map cells; default the middle tile) and HRS_PROBE_TILE_PATH,
    entries "cmd,cmd,...:settle" separated by ";": the commands in order, each acknowledged
    through the strip ("tile" and "move" get the point appended; "tile@x:y" / "move@x:y" that
    spot instead, to arrive the way the render does from a neighbouring tile; "black",
    "sky white" etc. as they are), then `clean`, the wait, and two shots 0.5 s apart (how much
    animates between them is logged; HRS_PROBE_GAP seconds apart instead). An entry without an
    "@" spot first jumps 100 cells away, so the point is arrived at afresh. An entry may start
    with "name=", which names its shots. Default "tile:0.5"."""
    tiles = manifest["tiles"]
    probe_dir = out.parent / f"probe-light-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(exist_ok=True)

    gap = float(os.environ.get("HRS_PROBE_GAP", "0.5"))

    def shot(name: str) -> None:
        frame = session.grab(dark_ok=True)
        if frame is not None:
            _save(frame, probe_dir / f"{name}.png")
            settle(gap)
            again = session.grab(dark_ok=True)
            if again is not None:
                _save(again, probe_dir / f"{name}-b.png")
                moving = float((np.abs(frame.astype(np.int16) - again.astype(np.int16)).max(axis=2) > 24).mean())
                st = session.status() if session.strip.located else None
                clock = f", game clock {st.game_seconds} s" if st else ""
                log(f"  probe: {name}  moving between two shots {gap:g} s apart: {moving * 100:.2f}% of pixels{clock}")
                return
        log(f"  probe: {name} (no frame)")

    def run() -> None:
        points = [tuple(float(v) for v in p.split(",")) for p in os.environ.get("HRS_PROBE_POINTS", "").split(";") if p.strip()]
        if not points:
            middle = min(tiles, key=lambda t: abs(t["row"] - manifest["rows"] // 2) + abs(t["col"] - manifest["cols"] // 2))
            points = [(middle["x"], middle["y"])]
        send_command("clean")  # no labels in these shots
        settle(0.5)
        for n, (x, y) in enumerate(points, start=1):
            send_command(f"look {x:.1f} {y:.1f}")
            settle(1.0)
            for k, entry in enumerate(os.environ.get("HRS_PROBE_TILE_PATH", "tile:0.5").split(";")):
                cmds, wait = entry.rsplit(":", 1)
                given, _, cmds = cmds.rpartition("=")
                if "@" not in cmds:
                    send_command(f"look {x + 100:.1f} {y:.1f}")  # arrive afresh
                    settle(1.0)
                acked = True
                for cmd in cmds.split(","):
                    if cmd.startswith(("tile@", "move@")):
                        name, spot = cmd.split("@")
                        full = f"{name} 0 {spot.replace(':', ' ')}"
                    elif cmd in ("tile", "move"):
                        full = f"{cmd} 0 {x:.2f} {y:.2f}"
                    else:
                        full = cmd
                    acked = session.send(full, timeout=3.0) is not None and acked
                if float(wait) > 5:
                    log(f"  waiting {float(wait):g} s of match time before the next shot ...")
                settle(float(wait))
                session.send("clean")
                label = given or (cmds.replace(",", "+").replace(" ", "-").replace(":", "_") if len(cmds) < 40 else f"{cmds.count(',') + 1}cmds-{cmds.split(',')[0].replace(' ', '')}")
                shot(f"point{n}-{k:02d}-{label}-s{wait}" + ("" if acked else "-noack"))

    step(run, "the lighting probe")
    quit_match()
    done(f"probe screenshots in {probe_dir}")


def probe_sky(session, manifest: dict, out: Path) -> None:
    """Do the solid-colour skyboxes show, and are they flat? An edge tile (left edge, middle
    row: half map, half sky) over each colour in turn; the sky part of each shot is measured
    (mean colour and spread) and the shots are kept. HRS_SKY_SEQUENCE scripts the swaps:
    "command:wait|..." with commands "<colour> <layer>" (layer 0 is the camera-fixed skybox,
    1 the terrain-relative parallax layer); the same command twice in a row only waits longer,
    which tells a slow transition from a failed swap."""
    tiles = manifest["tiles"]
    probe_dir = out.parent / f"probe-sky-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(exist_ok=True)
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
        sequence = os.environ.get("HRS_SKY_SEQUENCE") or "start:0|black 0:2.5|white 0:2.5|grey 0:2.5|lightgrey 0:2.5|none 0:2.5"
        previous = None
        for k, item in enumerate(sequence.split("|")):
            command, wait = item.rsplit(":", 1)
            if command not in ("start", previous):
                send_command(f"sky {command}")
            settle(float(wait))
            shot(f"{k:02d}-{command.replace(' ', '-layer')}-{wait}s")
            previous = command

    step(run, "the skybox probe")
    quit_match()
    done(f"probe screenshots in {probe_dir}")


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
    probe_dir = out.parent / f"probe-sky-reach-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(exist_ok=True)
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
                answer = session.send(f"tile 0 {x:.2f} {y:.2f}", timeout=3.0)
                if answer is None or session.send(f"hidemap {clip}") is None:
                    log(f"  ({x:.0f}, {y:.0f}): the map didn't answer")
                    continue
                cam = (answer[0].camera_x, answer[0].camera_y)
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

    step(run, "the sky reach probe")
    quit_match()
    done(f"probe screenshots in {probe_dir}")


def _difference(a: np.ndarray, b: np.ndarray, left: int) -> str:
    """How two shots of the same view differ: the share of 32-pixel blocks that changed (as the
    capture's half-drawn check measures), the mean difference, and the 99.9th percentile."""
    a, b = a[:, left:], b[:, left:]
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return f"blocks changed {changed_share(a, b) * 100:5.2f}%, mean {d.mean():5.2f}, p99.9 {np.percentile(d, 99.9):5.1f}"


def probe_waits(session, manifest: dict, out: Path) -> None:
    """Can the fixed waits be shorter? Each is tried shorter on the same views and the shot
    compared with one at the current value (and the current value is shot twice, to show how
    much two identical shots differ anyway):

    - the lighting-refit look held before each tile (0.1 s in the script), and the settle from
      the tile's acknowledgement to the kept shot (0.1 s): on sample tiles across the grid
      (corners, edges, the middle), each arrived at from its neighbour, as in a render
    - the time a sky swap gets to be drawn before its shot (0.1 s): the haze over white, swapped
      to from the background art, at one sky position
    - and the sky pass with sky positions further apart (0.8 of a screen instead of 0.6) into
      sky-keep08/, to compare its layers with the current ones."""
    tiles = manifest["tiles"]
    left = int((manifest.get("status") or {}).get("pageLeft", 0))
    probe_dir = out.parent / f"probe-waits-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(exist_ok=True)
    by_pos = {(t["row"], t["col"]): t for t in tiles}
    rows, cols = max(t["row"] for t in tiles), max(t["col"] for t in tiles)
    picks = [(0, 0), (0, cols // 2), (rows // 2, 0), (rows // 2, cols // 2), (rows, cols), (rows // 3, 2 * cols // 3)]
    samples = []
    for rc in picks:
        t = by_pos.get(rc)
        if t is not None and t not in samples:
            samples.append(t)

    def save(frame: np.ndarray, name: str) -> None:
        Image.fromarray(np.ascontiguousarray(frame)).save(probe_dir / f"{name}.png", compress_level=1)

    def tile_shot(t: dict, refit: float, settle_time: float) -> np.ndarray | None:
        """The tile as a render shoots it, arrived at from its neighbour (left, else right)."""
        neighbour = by_pos.get((t["row"], t["col"] - 1)) or by_pos.get((t["row"], t["col"] + 1)) or t
        if session.send(f"refitwait {refit:g}") is None:
            return None
        if session.send(f"tile {neighbour['index']} {neighbour['x']:.2f} {neighbour['y']:.2f}", timeout=3.0) is None:
            return None
        settle(0.5)
        answer = session.send(f"tile {t['index']} {t['x']:.2f} {t['y']:.2f}", timeout=3.0)
        if answer is None:
            return None
        moved = time.time()
        wait = moved + settle_time - time.time()
        if wait > 0:
            settle(wait)
        raw = session.fresh()
        return None if raw is None else session.blank(raw)

    conditions = [("current", 0.1, 0.1), ("current-again", 0.1, 0.1), ("refit-0.05", 0.05, 0.1),
                  ("settle-0.05", 0.1, 0.05), ("both-short", 0.05, 0.05)]
    log(f"waits probe: {len(samples)} sample tiles, {len(conditions)} shots each")
    for t in samples:
        def run() -> None:
            base = None
            for name, refit, settle_time in conditions:
                frame = tile_shot(t, refit, settle_time)
                if frame is None:
                    log(f"  tile {t['index'] + 1} {name}: no shot")
                    continue
                save(frame, f"tile{t['index']:03d}-{name}")
                if base is None:
                    base = frame
                    continue
                log(f"  tile {t['index'] + 1} (row {t['row']}, col {t['col']}) {name:14s} vs current: {_difference(frame, base, left)}")

        step(run, f"waits probe, tile {t['index'] + 1}")
    session.send("refitwait 0.1")

    sky = manifest.get("sky") or {}
    measured = None
    if sky.get("keys") and (sky.get("mapSky") or {}).get("parallax"):
        measured = step(lambda: sky_layers.measure(session, manifest, out.parent), "measuring the sky layers")
    if measured:
        area = manifest["area"]
        x, y = (area["left"] + area["right"]) / 2, (area["bottom"] + area["top"]) / 2
        clip = measured["nearClip"]

        def sky_run() -> None:
            base = None
            for name, wait in (("current", 0.1), ("current-again", 0.1), ("settle-0.05", 0.05), ("settle-0", 0.0)):
                for command in (f"tile 0 {x:.2f} {y:.2f}", f"hidemap {clip}", "sky parallaxbare 1"):
                    if session.send(command, timeout=3.0) is None:
                        warn("sky: no answer")
                        return
                settle(1.0)
                if session.send("sky parallaxlight 1") is None:
                    return
                settle(wait)
                frame = session.grab(dark_ok=True)
                save(frame, f"sky-{name}")
                if base is None:
                    base = frame
                    continue
                log(f"  sky swap {name:14s} vs current: {_difference(frame, base, left)}")

        step(sky_run, "waits probe, sky swap")
        session.send("sky mapparallax 1")
        sky_layers.capture(session, manifest, out.parent, measured, keep=0.8, folder_name="sky-keep08")
    quit_match()
    done(f"probe screenshots in {probe_dir}")



# The matte probe: the same paused view over white, black and two greys at a few spots with light
# that adds to what is behind it (a core's shield, a tower's orb, a core's lava and fire, the embers
# in the void by the Hell side), to compare the matte as it is (white and black: the white sky,
# drawn at about 230, clips a bright see-through spark at 255) with one over grey and black plus a
# glow layer for the light left over. The shots only: the comparison is worked out from them
# afterwards. Each spot: a name, its cell (or how to find it), and the radius in cells kept round it.
MATTE_SKIES = ["white", "black", "grey", "lightgrey"]
MATTE_RADIUS = 16.0


def matte_spots(manifest: dict) -> list[dict]:
    """The matte probe's spots: Order's core (its shield), an Order level 3 tower (its orb),
    Chaos's core (its lava and fire), and the void past the Hell side's top and right edges."""
    found, bounds = manifest["elements"]["structures"], manifest["cameraBounds"]
    spots = []
    for name, pick in (("order-core", lambda u: u["core"] and u["owner"] == "order"),
                       ("order-tower", lambda u: u["type"].startswith("TownCannonTowerL3") and u["owner"] == "order"),
                       ("chaos-core", lambda u: u["core"] and u["owner"] == "chaos")):
        u = next((u for u in found if pick(u)), None)
        if u is not None:
            spots.append({"name": name, "x": u["x"], "y": u["y"], "radius": 24.0 if u["core"] else MATTE_RADIUS})
    mid_y = (bounds["bottom"] + bounds["top"]) / 2
    spots.append({"name": "hell-top-edge", "x": bounds["left"] + (bounds["right"] - bounds["left"]) * 0.65, "y": bounds["top"], "radius": MATTE_RADIUS})
    spots.append({"name": "hell-right-edge", "x": bounds["right"], "y": mid_y, "radius": MATTE_RADIUS})
    return spots


def probe_elements(session, manifest: dict, out: Path) -> None:
    """The matte probe (matte_spots, MATTE_SKIES): the map as the tiles shoot it (paused, the
    structures standing), the camera over each spot ("el at": the lighting refitted there, the
    capture camera), shot over each sky in turn ("sky <colour>"), the part MATTE_RADIUS cells (a
    core's: 24) round the spot kept, lossless: probe-matte-<time>/<spot>-<sky>.png, and spots.json
    (each spot's cell, camera, and where its crop lies in the screen)."""
    left = int((manifest.get("status") or {}).get("pageLeft", 0))
    probe_dir = out.parent / f"probe-matte-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(exist_ok=True)
    ppc, screen = manifest["pxPerCell"], manifest["screen"]
    spots = matte_spots(manifest)
    log(f"matte probe: {len(spots)} spots over {', '.join(MATTE_SKIES)}: {', '.join(s['name'] for s in spots)}")

    def command(text: str, timeout: float = 3.0) -> bool:
        if session.send(text, timeout=timeout) is None:
            warn(f"  no answer to \"{text}\"")
            return False
        return True

    def run() -> None:
        for spot in spots:
            command(f"el at {spot['x']:.2f} {spot['y']:.2f}")
            settle(0.5)
            status = session.status()
            camera = {"x": status.camera_x, "y": status.camera_y} if status else spot
            cx, cy = screen_point(spot, camera, screen, ppc)
            r = int(spot["radius"] * ppc)
            box = (max(0, int(cy) - r), int(cy) + r, max(left, int(cx) - r), int(cx) + r)
            spot.update(camera=camera, box=box)
            for sky in MATTE_SKIES:
                command(f"sky {sky} 0")
                settle(1.0)  # the sky swap drawn (a frame or two), the scene paused
                frame = session.grab(dark_ok=True)
                if frame is None:
                    warn(f"  {spot['name']} over {sky}: no frame")
                    continue
                crop = frame[box[0]:box[1], box[2]:box[3], :3]
                Image.fromarray(np.ascontiguousarray(crop)).save(probe_dir / f"{spot['name']}-{sky}.png")
            log(f"  {spot['name']} at ({spot['x']:g}, {spot['y']:g}): {len(MATTE_SKIES)} skies")
        command("sky white 0")
        (probe_dir / "spots.json").write_text(json.dumps(spots, indent=1))

    step(run, "matte probe, each spot over each sky")
    quit_match()
    done(f"probe screenshots in {probe_dir}")


def elements_town(manifest: dict) -> dict:
    """The Order team's forward town's hall (the busiest view: its gate, towers, orbs and walls)."""
    found = manifest["elements"]["structures"]
    halls = [u for u in found if u["type"].startswith("TownTownHall") and u["owner"] == "order"]
    core = next(u for u in found if u["core"] and u["owner"] == "order")
    return max(halls, key=lambda u: math.dist((u["x"], u["y"]), (core["x"], core["y"])))
