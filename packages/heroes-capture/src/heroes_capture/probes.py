"""Diagnostic runs (capture.py --probe-light, --probe-sky, --probe-waits, --probe-elements): instead of the tiles, chosen views and
command sequences, a shot after each, kept in a probe-<what>-<time> folder next to the tiles.
"""

import math
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import sky_layers
from .elements import ORDER_TEAM, changed, cut_out, describe, element_targets, screen_point
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
        sequence = os.environ.get("HRS_SKY_SEQUENCE") or "start:0|black 0:2.5|white 0:2.5|magenta 0:2.5|lime 0:2.5|none 0:2.5"
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
                if session.send("sky parallaxwhite 1") is None:
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



def probe_elements(session, manifest: dict, out: Path) -> None:
    """The cores' holes (ELEMENTS-PLAN.md, The bare terrain): filled in the map file when it was
    prepared, opened again by the script as it started. On the Order core, through the script's
    "el" commands, from the tile nearest it:

    - the core as the script opened its hole (to compare with a map whose hole is the file's own)
    - the hole shown (the floor through the core's centre) and opened again: back as it was?
    - the core alone over the sky (its cut-out), then everything shown again: the hole opened
      again after all the terrain was shown?
    - the core killed after a hidden replacement took its place as the team's core (the match
      carries on), its remains cleared (the hole open to the sky), and the hole shown: the ground
      where the core stood

    Every shot is kept in probe-elements-<time>/, the differences logged."""
    left = int((manifest.get("status") or {}).get("pageLeft", 0))
    probe_dir = out.parent / f"probe-elements-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(exist_ok=True)
    core = element_targets(manifest)["core"]
    shots: dict[str, np.ndarray] = {}
    at = {}  # the tile the camera is on
    log(f"elements probe: the core at ({core['x']:g}, {core['y']:g})")

    def command(text: str, timeout: float = 3.0) -> bool:
        if session.send(text, timeout=timeout) is None:
            warn(f"  no answer to \"{text}\"")
            return False
        return True

    def shot(name: str) -> np.ndarray | None:
        frame = session.grab(dark_ok=True)
        if frame is None:
            log(f"  {name}: no frame")
            return None
        shots[name] = frame
        _save(frame, probe_dir / f"{name}.png")
        return frame

    def compare(name: str, other: str) -> None:
        if name in shots and other in shots:
            log(f"  {name} vs {other}: {describe(changed(shots[name], shots[other], left))}")

    def isolated(name: str, spot: dict, radius: float) -> None:
        """What is on show alone over the sky ("el isolate on": all the terrain, every doodad, the
        cliff doodads, and the units and model actors more than `radius` cells from `spot`
        hidden), over white and over black, made into its cut-out (elements.cut_out); then
        everything shown again, and the view compared with the one before."""
        shot(f"{name}-iso-before")
        if not command(f"el isolate on {spot['x']:g} {spot['y']:g} {radius:g}"):
            return
        settle(1.0)
        white = shot(f"{name}-iso-white")
        command("black")
        settle(0.5)
        black = shot(f"{name}-iso-black")
        command("sky white 0")  # the layer given: the sequence number is appended after it
        if white is not None and black is not None:
            centre = screen_point(spot, at["tile"], manifest["screen"], manifest["pxPerCell"])
            rgba = cut_out(white, black, centre, radius * manifest["pxPerCell"])
            Image.fromarray(rgba, "RGBA").save(probe_dir / f"{name}-iso-cutout.png", compress_level=1)
            alpha = rgba[..., 3]
            log(f"  {name} alone over the sky: {(alpha > 0).sum()} pixels ({describe(alpha > 0)}), "
                f"{(alpha == 255).sum()} opaque, {((alpha > 0) & (alpha < 255)).sum()} soft")
        command("el isolate off")
        settle(1.0)
        shot(f"{name}-iso-restored")
        compare(f"{name}-iso-restored", f"{name}-iso-before")

    def run() -> None:
        t = min(manifest["tiles"], key=lambda t: math.dist((t["x"], t["y"]), (core["x"], core["y"])))
        at["tile"] = t
        command(f"tile {t['index']} {t['x']:.2f} {t['y']:.2f}")
        settle(1.0)
        command("clean")
        settle(0.5)
        log(f"  tile {t['index'] + 1} at ({t['x']:g}, {t['y']:g})")
        shot("core-before")
        for way in ("show", "hide"):
            command(f"el holes {way}")
            settle(1.0)
            shot(f"core-holes-{way}")
            compare(f"core-holes-{way}", "core-before")
        command("el hideall")
        command(f"el show {core['x']:g} {core['y']:g}")
        settle(0.5)
        isolated("core", core, 5.0)
        command("el showall")
        settle(0.5)
        status = session.status()
        if not command(f"el core {ORDER_TEAM} 15", timeout=20.0):
            return
        settle(1.0)
        shot("core-rubble")
        after = session.status()
        if after is None:
            warn("  after the core's death the strip can't be read: the match may have ended")
        else:
            going = after.phase != 3 and status is not None and after.game_seconds > status.game_seconds
            log(f"  after the core's death: phase {after.phase}: " + ("the match carries on" if going else "the match looks over"))
        command("el clear")
        settle(1.0)
        shot("core-cleared")
        command("el holes show")
        settle(1.0)
        shot("core-cleared-shown")
        compare("core-cleared-shown", "core-cleared")

    step(run, "elements probe, the core's holes")
    quit_match()
    done(f"probe screenshots in {probe_dir}")
