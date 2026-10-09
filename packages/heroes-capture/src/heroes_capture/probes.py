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
from .elements import changed, cut_out, describe, screen_point
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



# The film probe: every kind of structure on both teams filmed falling, to choose the moment each
# kind's rubble is shot (one time for all of a kind didn't suit both teams: a Hell moonwell's lay
# under thick smoke, a Heaven tower's under its blue glow, some walls' were only a puff). Each a
# copy on an empty spot of its own (a keep falls itself: its copy crashed the game), the camera on
# it (a fall gives off its particles only near the camera), a frame every FILM_GAP for
# FILM_SECONDS in a circle of FILM_RADIUS cells. A kind: a type prefix and a team, the first such
# structure on the map ("=" before the type: that type exactly).
FILM_KINDS = [(kind, owner) for owner in ("order", "chaos") for kind in (
    "TownCannonTowerL2", "=TownCannonTowerL3", "TownCannonTowerL3Standalone", "TownGateL215", "TownGateL3",
    "TownWallRadial", "TownMoonwellL2", "TownMoonwellL3", "TownTownHallL2", "TownTownHallL3")] + [
    ("TownWallRadial5L2", "order"), ("TownWallRadial17L2", "chaos"), ("TownWallRadial18L2", "chaos")]
FILM_SECONDS, FILM_GAP, FILM_RADIUS = 8.0, 0.25, 14.0
# Cells an empty spot keeps from every structure and camp, and from the other spots.
DUP_CLEAR = 20.0


def empty_spots(manifest: dict, count: int) -> list[dict]:
    """Spots on the map with nothing near them (DUP_CLEAR), inside the camera's bounds, on a grid
    of 4 cells: the first `count`, nearest the map's middle first."""
    bounds, found = manifest["cameraBounds"], manifest["elements"]
    taken = [(u["x"], u["y"]) for u in found["structures"]] + [(c["x"], c["y"]) for c in found["camps"]]
    mid = ((bounds["left"] + bounds["right"]) / 2, (bounds["bottom"] + bounds["top"]) / 2)
    grid = sorted(((x, y) for x in range(int(bounds["left"]) + 8, int(bounds["right"]) - 7, 4)
                   for y in range(int(bounds["bottom"]) + 8, int(bounds["top"]) - 7, 4)), key=lambda p: math.dist(p, mid))
    spots: list[dict] = []
    for p in grid:
        if all(math.dist(p, t) >= DUP_CLEAR for t in taken):
            spots.append({"x": float(p[0]), "y": float(p[1])})
            taken.append(p)
            if len(spots) == count:
                break
    return spots


def write_film_page(folder: Path, films: list[dict]) -> None:
    """index.html in the film probe's folder: each kind's frames, stepped through with a slider (or
    the arrow keys), the time since its fall under it."""
    page = """<!doctype html><meta charset="utf-8"><title>Falls filmed</title>
<style>body{font:14px system-ui;margin:16px;background:#ddd}section{margin:0 0 28px}h2{font-size:15px;margin:4px 0}
img{max-width:100%;background:#e6e6e6;display:block}input{width:100%}</style>
<p>Each kind's fall, a frame every quarter second: drag the slider (or click it and use the arrow keys); the time since the fall is under it.</p>
<div id="films"></div>
<script>
const films = FILMS;
const root = document.getElementById("films");
for (const film of films) {
  const s = document.createElement("section");
  s.innerHTML = `<h2>${film.name}</h2><img><input type="range" min="0" max="${film.frames.length - 1}" value="0"><div></div>`;
  const [img, range, label] = [s.querySelector("img"), s.querySelector("input"), s.querySelector("div")];
  const show = () => { const f = film.frames[range.value]; img.src = f; label.textContent = f.split("/")[1].replace(".jpg", ""); };
  range.addEventListener("input", show);
  show();
  root.appendChild(s);
}
</script>"""
    (folder / "index.html").write_text(page.replace("FILMS", json.dumps(films)), encoding="utf-8")


def probe_elements(session, manifest: dict, out: Path) -> None:
    """Each kind of structure on each team filmed falling (FILM_KINDS), to choose when its rubble is
    shot. On Battlefield of Eternity, every structure faded out ("el fadeall 0") and the scene hidden
    ("el env off"), each kind in turn: its copy made on an empty spot ("el copy make"; a keep: the
    structure itself, where it stands), the camera straight above it ("el at"), brought down
    ("el copykill"; "el kill") and filmed over white, a frame every FILM_GAP seconds for
    FILM_SECONDS in a circle of FILM_RADIUS cells; then paused, its remains cleared. Every frame
    full size, probe-elements-<time>/<n>-<owner>-<type>/<seconds>s.jpg, and index.html there to
    step through each kind's (write_film_page)."""

    left = int((manifest.get("status") or {}).get("pageLeft", 0))
    probe_dir = out.parent / f"probe-elements-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(exist_ok=True)
    found = manifest["elements"]["structures"]
    ppc = manifest["pxPerCell"]
    screen = manifest["screen"]
    tests = []
    for kind, owner in FILM_KINDS:
        exact = kind.startswith("=")
        u = next((u for u in found if u["owner"] == owner and (u["type"] == kind[1:] if exact else u["type"].startswith(kind))), None)
        if u is not None:
            tests.append(u)
    spots = iter(empty_spots(manifest, len(tests)))
    log(f"elements probe: {len(tests)} kinds of structure filmed falling: {', '.join(u['owner'] + ' ' + u['type'] for u in tests)}")

    def command(text: str, timeout: float = 3.0) -> bool:
        if session.send(text, timeout=timeout) is None:
            warn(f"  no answer to \"{text}\"")
            return False
        return True

    films: list[dict] = []

    def run() -> None:
        command("el fadeall 0")
        command("el env off", timeout=10.0)
        for n, u in enumerate(tests):
            keep = u["type"].startswith("TownTownHallL3")
            at = u if keep else {**u, **{k: v + u[k] % 1 for k, v in next(spots).items()}}
            if not keep:
                command(f"el copy {n} make {u['x']:g} {u['y']:g} {at['x']:g} {at['y']:g}")
            command(f"el at {at['x']:.2f} {at['y']:.2f}")
            status = session.status()
            camera = {"x": status.camera_x, "y": status.camera_y} if status else at
            cx, cy = screen_point(at, camera, screen, ppc)
            r = int(FILM_RADIUS * ppc)
            box = (slice(max(0, int(cy) - r), int(cy) + r), slice(max(left, int(cx) - r), int(cx) + r))
            if keep:
                command(f"el scopemsg {u['x']:g} {u['y']:g} SetOpacity 1 0")
                command(f"el kill {u['x']:g} {u['y']:g} -1")
            else:
                command(f"el copykill {n} {n}")
            started = time.time()
            frames: list[tuple[float, np.ndarray]] = []
            while time.time() - started < FILM_SECONDS:
                frame = session.grab(dark_ok=True)
                if frame is not None:
                    frames.append((time.time() - started, frame[box].copy()))
                settle(FILM_GAP)
            command("el freeze")
            command("el clear")
            name = f"{n:02d}-{u['owner']}-{u['type']}"
            (probe_dir / name).mkdir(exist_ok=True)
            names = []
            for t, crop in frames:
                names.append(f"{name}/{t:05.2f}s.jpg")
                Image.fromarray(np.ascontiguousarray(crop[..., :3])).save(probe_dir / names[-1], quality=92)
            films.append({"name": name, "frames": names})
            log(f"  {u['owner']} {u['type']} ({'itself' if keep else 'a copy'}): {len(frames)} frames")
        command("el env on", timeout=10.0)
        write_film_page(probe_dir, films)

    step(run, "elements probe, every kind filmed falling")
    quit_match()
    done(f"probe screenshots in {probe_dir}")


def elements_town(manifest: dict) -> dict:
    """The Order team's forward town's hall (the busiest view: its gate, towers, orbs and walls)."""
    found = manifest["elements"]["structures"]
    halls = [u for u in found if u["type"].startswith("TownTownHall") and u["owner"] == "order"]
    core = next(u for u in found if u["core"] and u["owner"] == "order")
    return max(halls, key=lambda u: math.dist((u["x"], u["y"]), (core["x"], core["y"])))
