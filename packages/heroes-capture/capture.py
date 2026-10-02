"""Launch a prepared battleground and screenshot every tile of its capture grid.

    python capture.py work/towers-of-doom-structures.json [options]

The injected map script (capture-script.mjs) takes chat commands and reports through its status
strip (status.py) when each is done and where the camera really is. The capture:

  1. starts Heroes if needed and launches the map (game_control.py);
  2. waits for the strip, checks it is the map prepared for this run, and waits until the map
     reports ready (gates open, opening timers cut short, animations paused);
  3. measures the camera bounds the game applies and re-plans the grid from them;
  4. for each tile: `tile <n> <x> <y>`, `clean` and the kept shot, and with matting `black` and
     the same view over the black skybox; the camera positions go to positions.json;
  5. leaves the match, back to the menu for the next run.

When the match is lost (the game closes, the map fails to load, another map is running) the run
is launched again and resumes from the first missing tile, three times at most.

Captures the game window itself, on whichever monitor it is. Run the game in
"Windowed (Fullscreen)" at the resolution given to inject.mjs, and leave the mouse and keyboard
alone while it captures.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

import probes
import sky_layers
from frames import save_frame
from game_control import (
    Recoverable,
    dismiss_failed_dialog,
    launch_map,
    quit_match,
    require_focus,
    send_chat,
    settle,
    step,
    wait_for_map_load,
)
from game_state import IN_MAP, LOADING, MAP_FAILED, MENU, NOT_RUNNING, PHASES, game_state
from game_window import foreground_is_game, game_region, hold_key
from runlog import log, log_timings, set_log_file, stage
from screen import ScreenGrabber, disagree, looks_black, same_view, view_shift
from status import Status, StatusStrip

DEFAULT_GAME = r"D:\Games\Heroes of the Storm"


def tile_command(tile: dict) -> str:
    """`tile <n> <x> <y>`: the map script takes the position from the command, so the grid can
    be planned here, from the camera bounds measured in the game (see plan_grid)."""
    return f"tile {tile['index']} {tile['x']:.2f} {tile['y']:.2f}"


def plan_grid(manifest: dict, bounds: dict) -> None:
    """Re-plan the capture grid for other camera bounds (inject.mjs's planGrid, in Python), in
    place: tiles, area, cameraBounds, cols, rows, step. Camera targets stay inside the bounds
    (the game clamps any beyond them), first and last on the bounds, the rest spread evenly,
    at most `keep` of a screen apart."""
    view_w = manifest["screen"]["w"] / manifest["pxPerCell"]
    view_h = manifest["screen"]["h"] / manifest["pxPerCell"]

    def spread(span: float, max_step: float) -> tuple[int, float]:
        count = max(1, math.ceil(span / max_step) + 1)
        return count, (span / (count - 1) if count > 1 else max_step)

    cols, step_x = spread(bounds["right"] - bounds["left"], view_w * manifest["keep"])
    rows, step_y = spread(bounds["top"] - bounds["bottom"], view_h * manifest["keep"])
    tiles = []
    for row in range(rows):
        for col in range(cols):
            tiles.append({"index": len(tiles), "row": row, "col": col, "x": bounds["left"] + col * step_x, "y": bounds["top"] - row * step_y})
    manifest.update(tiles=tiles, area=dict(bounds), cameraBounds=dict(bounds), cols=cols, rows=rows, step={"x": step_x, "y": step_y})


class Session:
    """The game window being captured: frames from it, and the map script's status strip in
    them (read from every raw frame; kept shots have its column blanked, and the stitch leaves
    that column out)."""

    def __init__(self, screen: ScreenGrabber, strip_width: int):
        self.screen = screen
        self.strip = StatusStrip()
        self.strip_width = strip_width
        self._seq = 0

    def raw_grab(self) -> np.ndarray:
        """The screen as it is. The grab copies whatever is on screen there, so the game must be
        in front before and after it; otherwise it may have caught another window."""
        require_focus()
        frame = self.screen.grab()
        require_focus()
        return frame

    def blank(self, frame: np.ndarray) -> np.ndarray:
        return self.strip.blank(frame, self.strip_width) if self.strip.located and self.strip_width else frame

    def grab(self, dark_ok: bool = False) -> np.ndarray | None:
        """One frame as RGB (strip blanked), retaken while it comes back black; None if it
        stays black. `dark_ok` takes a dark, flat frame as it is: a shot at a map's edge can be
        all void (pure black on Tomb of the Spider Queen)."""
        for _ in range(10):
            frame = self.blank(self.raw_grab())
            if dark_ok or not looks_black(frame):
                return frame
            time.sleep(0.3)
        return None

    def status(self) -> Status | None:
        return self.strip.read(self.raw_grab())

    @property
    def last_seq(self) -> int:
        return self._seq

    def fresh(self, timeout: float = 1.0) -> np.ndarray | None:
        """A frame grabbed now in which the strip still shows the last command's number: the
        game drew it after that command was carried out, and nothing else was sent since."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.raw_grab()
            status = self.strip.read(raw)
            if status is not None and status.seq == self._seq:
                return raw
            time.sleep(0.02)
        return None

    def wait_for(self, check, timeout: float = 1.0) -> np.ndarray | None:
        """A frame whose strip passes `check` (a Status -> bool), grabbed within `timeout`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.raw_grab()
            status = self.strip.read(raw)
            if status is not None and check(status):
                return raw
            time.sleep(0.02)
        return None

    def send(self, command: str, timeout: float = 2.0, sends: int = 4) -> tuple[Status, np.ndarray] | None:
        """A chat command with a sequence number appended, then the strip polled until it shows
        that number: the command has been carried out and the frame rendered. Returns the
        status and that raw frame; None when the map never answered (sent `sends` times,
        `timeout` seconds each)."""
        for _ in range(sends):
            self._seq = self._seq % 255 + 1  # 1..255; 0 is what the strip shows before any command
            send_chat(f"{command} {self._seq}")
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = self.raw_grab()
                status = self.strip.read(raw)
                if status is not None and status.seq == self._seq:
                    return status, raw
                time.sleep(0.02)
        return None


# ------------------------------------------------------------------------------------------------
# Start-up: the strip, the map's identity, the map ready, the first tile, the camera bounds
# ------------------------------------------------------------------------------------------------


def wait_for_strip(session: Session, out: Path) -> None:
    """The status strip: drawn by the map script from its start, but the interface is hidden
    while the intro cutscene plays. Nothing is typed until it shows. Raises Recoverable when the
    game closes, the map fails to load, the menu comes back, or 3 minutes pass."""
    deadline = time.time() + 180
    log("waiting for the map's status strip ...")
    failed_since, menu_since, dumped = None, None, False
    started = time.time()
    while True:
        frame = step(session.raw_grab, "finding the status strip")
        state = game_state(frame, session.strip)
        if state == IN_MAP:
            break
        if not dumped and time.time() - started > 30 and state == LOADING:
            # Kept for diagnosis: the screen's left edge, where the strip should be.
            dumped = True
            Image.fromarray(np.ascontiguousarray(frame[:, :240])).save(out.parent / "strip-missing.png")
            log("  no status strip after 30 s; the screen's left edge is in strip-missing.png")
        if state == NOT_RUNNING:
            raise Recoverable("the game closed before the map started")
        if state == MAP_FAILED:
            failed_since = failed_since or time.time()
            if time.time() - failed_since > 2:
                hold_key("enter")  # the dialog's OK
                raise Recoverable("the game couldn't open the map")
        else:
            failed_since = None
        if state == MENU:
            menu_since = menu_since or time.time()
            if time.time() - menu_since > 15:
                raise Recoverable("the game went back to the menu instead of starting the map")
        else:
            menu_since = None
        if time.time() > deadline:
            quit_match()
            raise Recoverable("the map's status strip never appeared (if every interface panel was visible, its script failed to compile)")
        time.sleep(0.25)
    strip = session.strip
    log(f"  status strip: {strip.cell_w}x{strip.cell} px cells at {strip.origin}")
    if session.strip_width and 2 * strip.cell_w + strip.origin[0] > session.strip_width:
        log(f"  warning: the strip ({2 * strip.cell_w + strip.origin[0]} px) is wider than the {session.strip_width} px blanked; lower the interface scale or raise pageLeft")


def wait_until_ready(session: Session) -> None:
    """The map ready, before anything else is measured or shot: the gates open early (3 s), the
    map's opening timers are cut short so its first objective is in place (the camera bounds can
    change then too), and the script pauses every animation; nothing is sent to the map until
    it is over. Each phase the strip reports is logged."""
    log("waiting for the map to be ready (gates open, opening timers cut short) ...")
    started = time.time()
    last_phase = None

    def ready() -> bool:
        nonlocal last_phase
        status = session.status()
        if status and status.phase != last_phase:
            last_phase = status.phase
            log(f"  map phase: {PHASES[status.phase]} (game clock {status.game_seconds} s)")
        return bool(status and status.ready)

    while not step(ready, "waiting for the game to start"):
        if time.time() - started > 240:
            log("  the map didn't report ready within 4 minutes; pausing the animations now")
            step(lambda: session.send("pause"), "pausing the animations")
            return
        time.sleep(1.0)
    status = session.status()
    clock = f"; game clock {status.game_seconds} s since the gates, {status.opening_cuts} opening timers cut short" if status else ""
    log(f"  map ready after {time.time() - started:.0f} s{clock}")


def measure_bounds(session: Session, manifest: dict, manifest_path: Path) -> None:
    """The camera bounds the game really applies (an arena's are far tighter than its map file
    says): the camera is sent to the map's corners and the strip reports where it stopped. A
    grid planned for the wrong bounds spends most tiles on sky. The grid is re-planned (and the
    manifest rewritten) when they differ from the map file's."""
    size = manifest["mapSize"]
    low = step(lambda: session.send("tile 0 0 0", timeout=3.0), "the bounds check")
    high = step(lambda: session.send(f"tile 0 {size['width']} {size['height']}", timeout=3.0), "the bounds check")
    bounds = None
    if low and high:
        bounds = {"left": low[0].camera_x, "bottom": low[0].camera_y, "right": high[0].camera_x, "top": high[0].camera_y}
        if not (bounds["left"] < bounds["right"] and bounds["bottom"] < bounds["top"]):
            bounds = None
    if bounds is None:
        log("  couldn't measure the camera bounds in the game; using the map file's")
        return
    planned = manifest["cameraBounds"]
    if any(abs(bounds[k] - planned[k]) > 2 for k in bounds):
        plan_grid(manifest, bounds)
        manifest_path.write_text(json.dumps(manifest, indent=2))
        log(f"camera bounds in the game: {bounds} (map file: {planned}); grid re-planned: {manifest['cols']}x{manifest['rows']} = {len(manifest['tiles'])} tiles")
    else:
        log(f"camera bounds in the game match the map file: {bounds}")


def start_up(session: Session, manifest: dict, manifest_path: Path, out: Path, before_ready=None) -> None:
    """From the launch to the first tile; the grid may be re-planned (manifest["tiles"]).
    `before_ready` runs once the map takes commands, while it is still getting ready (the gates,
    the opening, the entrance animations): work that doesn't need the map paused (the sky
    layers) is done in that wait instead of after the tiles."""
    tiles = manifest["tiles"]
    # The first tile: the middle tile (of the first arena, on a map of several), as a corner tile
    # can be clamped far from where it was sent.
    first_area = [t for t in tiles if t.get("area", 0) == 0]
    centre_x = (min(t["x"] for t in first_area) + max(t["x"] for t in first_area)) / 2
    centre_y = (min(t["y"] for t in first_area) + max(t["y"] for t in first_area)) / 2
    first_tile = min(first_area, key=lambda t: abs(t["x"] - centre_x) + abs(t["y"] - centre_y))

    def until_commands_taken() -> None:
        """The first tile, until the map takes it: `tile` is ignored while the intro's exit is
        still restoring the camera and interface."""
        deadline = time.time() + 180
        while not step(lambda: session.send(tile_command(first_tile), timeout=2.0, sends=1), "the start-up"):
            if time.time() > deadline:
                quit_match()
                sys.exit("\nStopped: the map never carried out a command (the strip shows, so the script runs; is a selection phase holding it?)")
            log("  start-up: the map hasn't taken a command yet (intro or selection phase)")
            time.sleep(1.0)

    with stage("start-up (strip)"):
        if manifest.get("keepIntro"):
            # Diagnostic: the intro plays out. Nothing is typed meanwhile, in case keys count as
            # skipping it.
            log("letting the intro cutscene play out (nothing typed for 60 s) ...")
            step(lambda: settle(60.0), "the wait for the intro")
        wait_for_strip(session, out)
        # The map running is the one prepared for this run (not one an earlier run left).
        expected_id = manifest["status"].get("mapId")
        seen = session.status()
        if expected_id is not None and seen is not None and seen.map_id != expected_id:
            quit_match()
            raise Recoverable(f"another map is running (identity {seen.map_id}, expected {expected_id})")
    if before_ready is not None:
        with stage("start-up (first command)"):
            until_commands_taken()
        before_ready()
    with stage("start-up (ready, bounds)"):
        finish_start_up(session, manifest, manifest_path, until_commands_taken)


def finish_start_up(session: Session, manifest: dict, manifest_path: Path, until_commands_taken) -> None:
    """The map ready, the first tile, the camera bounds, the view holding still."""
    wait_until_ready(session)
    settle(1.0)
    until_commands_taken()
    # (Not when the script lifts the bounds: the grid then comes from the map file, or from its
    # arena areas.)
    if not manifest.get("unbound"):
        measure_bounds(session, manifest, manifest_path)

    # The game's intro exit pans the camera to the player's start position a moment after the
    # map starts taking commands; the first tile must not be shot into that. The view has to
    # hold still for a second before the tiles start.
    def hold_still() -> None:
        before = session.grab(dark_ok=True)
        for _ in range(20):
            settle(1.0)
            now = session.grab(dark_ok=True)
            if before is not None and now is not None and same_view(now, before):
                return
            before = now

    step(hold_still, "waiting for the camera to hold still")


# ------------------------------------------------------------------------------------------------
# The tiles
# ------------------------------------------------------------------------------------------------


def black_settled(session: Session, first: np.ndarray, white: np.ndarray) -> np.ndarray:
    """The shot over black, once the skybox swap has finished rendering: the share of black
    pixels jumps and then holds still between two grabs (no sky in view: the first frame as it
    is)."""

    def black_share(frame: np.ndarray) -> float:
        return float((frame[::4, ::4].max(axis=2) <= 6).mean())

    before, frame, share = black_share(white), first, black_share(first)
    for _ in range(8):
        if share - before <= 0.005:
            return frame  # no sky in view, or the swap not rendered yet
        settle(0.05)
        again = session.grab(dark_ok=True)
        if again is None:
            return frame
        next_share = black_share(again)
        if abs(next_share - share) < 0.002:
            return again
        frame, share = again, next_share
    return frame


def whole_frame(session: Session, frame: np.ndarray | None, reference: np.ndarray | None) -> tuple[np.ndarray | None, bool]:
    """The kept shot, retaken while it disagrees with the tile's reference frame (the frame the
    strip acknowledged `tile` in: same view) and with the retake before it: a half-drawn frame.
    Two retakes that agree with each other are accepted, in case the reference was the bad one;
    the second value then says the kept shot doesn't match the reference."""
    # Same view as the reference (no shift), however much animates (an animated sky, lava).
    # A half-drawn frame (drawn at the top, black below) matches the reference where it is
    # drawn, so the shift test alone would pass it: not if it has black where the reference
    # has none.
    featureless = frame is not None and float(frame[::8, ::8].std()) < 3.0
    partly_black = (
        frame is not None and reference is not None
        and float(((frame[::8, ::8].max(axis=2) < 12) & (reference[::8, ::8].max(axis=2) >= 12)).mean()) > 0.02
    )
    if frame is not None and reference is not None and not featureless and not partly_black and view_shift(frame, reference) <= 1.5:
        return frame, False
    for attempt in range(5):
        # (A black grab of a view that is all void passes, and looks just like it: some maps'
        # void is pure black.)
        if frame is None or reference is None or not disagree(frame, reference):
            return frame, attempt > 0
        log("    half-drawn frame; retaking")
        settle(0.1)
        reference, frame = frame, session.grab(dark_ok=True)
    return frame, False


def shoot_once(session: Session, tile: dict, settle_time: float, matting: bool):
    """(the strip's status at the tile: where the camera really is; the kept shot; the same view
    over the black skybox when matting; whether the kept shot doesn't match the view the tile
    was acknowledged in)."""
    # `tile`, acknowledged once the camera has moved and been clamped: the status says where
    # the camera really is, and the acknowledged frame is the reference view.
    answer = session.send(tile_command(tile), timeout=3.0)
    if answer is None:
        return None, None, None, False
    moved = time.time()
    status = answer[0]
    reference = session.blank(answer[1])
    # Shot 1: camera unmoved, over the white skybox, not before --settle seconds from the move
    # (textures still loading). This is the image kept. (`tile` cleared transmissions and
    # messages just before it acknowledged; the sweep keeps them cleared.)
    wait = moved + settle_time - time.time()
    if wait > 0:
        settle(wait)
    raw = session.fresh()
    if raw is None:
        return status, None, None, False
    frame, mismatch = whole_frame(session, session.blank(raw), reference)
    # Shot 2 (matting): the same over black; the stitch turns the pair into colour and
    # transparency.
    black = None
    if matting and frame is not None:
        # The black sky from one key press (number pad 5): the strip keeps the tile's number and
        # shows the sky as black once it is. The chat command if the key went missing.
        require_focus()
        hold_key("numpad5")
        raw = session.wait_for(lambda st: st.seq == session.last_seq and st.sky_black, timeout=0.8)
        if raw is None:
            answer = session.send("black")
            raw = answer[1] if answer is not None else None
        if raw is not None:
            black = black_settled(session, session.blank(raw), frame)
    return status, frame, black, mismatch


def shoot(session: Session, tile: dict, settle_time: float, matting: bool):
    """One tile, start to finish, as one step. A tile whose kept shot isn't the view it was
    acknowledged in (something moved the camera in between) is done again, once."""
    result = shoot_once(session, tile, settle_time, matting)
    if result[3] and result[1] is not None:
        log(f"    tile {tile['index'] + 1}: the view changed under it; doing it again")
        result = shoot_once(session, tile, settle_time, matting)
    return result


def offset_note(status: Status, tile: dict) -> str:
    """Near the edges the game holds the camera back, which is worth a note."""
    off_x, off_y = status.camera_x - tile["x"], status.camera_y - tile["y"]
    parts = []
    if abs(off_x) > 0.5:
        parts.append(f"{abs(off_x):.1f} cells {'east' if off_x > 0 else 'west'}")
    if abs(off_y) > 0.5:
        parts.append(f"{abs(off_y):.1f} cells {'north' if off_y > 0 else 'south'}")
    return "  camera " + " and ".join(parts) + " of plan" if parts else ""


EDGE_BAND = 40  # pixels along a frame's outer edge checked for map content running on past it
EDGE_CONTENT = 200  # this many content pixels in the band: the map goes on past the grid there
EDGE_RINGS = 3  # at most this many tiles past the grid on any side


def edge_content(frame: np.ndarray, black: np.ndarray | None, matting: bool, left: int, side: str) -> bool:
    """Whether map content reaches the frame's edge on that side (top, bottom, left, right): mostly
    opaque pixels in the outermost band (matte maps: what differs little between the shots over
    white and black; maps whose void is black terrain: anything not near-black). The status
    strip's column is left out."""
    band = {
        "top": np.s_[:EDGE_BAND, left:],
        "bottom": np.s_[-EDGE_BAND:, left:],
        "left": np.s_[:, left : left + EDGE_BAND],
        "right": np.s_[:, -EDGE_BAND:],
    }[side]
    white = frame[band].astype(np.int16)
    if matting and black is not None:
        content = (white - black[band].astype(np.int16)).mean(axis=2) < 115  # more than half opaque
    elif matting:
        content = (white.min(axis=2) < 200) | (white.max(axis=2) - white.min(axis=2) > 20)  # not the white sky
    else:
        content = white.max(axis=2) > 20
    return int(content.sum()) >= EDGE_CONTENT


def capture_tiles(session: Session, manifest: dict, start: int, settle_time: float, out: Path, positions: dict, positions_path: Path, manifest_path: Path) -> None:
    """Every tile from `start`, saved as PNGs (written in the background while the next tile is
    shot), with the camera positions in positions.json. Three tiles in a row that fail raise
    Recoverable, resuming at the first of them.

    Then past the grid, where the map runs on: an outer tile with map content at its outer edge
    (Battlefield of Eternity's arches past the camera bounds) gets a tile beyond it, the camera
    bounds lifted to the whole map, and so on outwards (EDGE_RINGS at most). Those tiles are added
    to the manifest, so the stitch places them like the rest."""
    tiles = manifest["tiles"]
    matting = (manifest.get("sky") or {}).get("mode") == "matte"
    left = int((manifest.get("status") or {}).get("pageLeft", 0))
    rows = [t["row"] for t in tiles]
    cols = [t["col"] for t in tiles]
    outer = {"top": min(rows), "bottom": max(rows), "left": min(cols), "right": max(cols)}
    # Not on a map of several arenas: its areas' grids sit side by side, and their edges are each
    # other's.
    extend = not manifest.get("areas")
    flagged: list[tuple[dict, str]] = []  # (tile, side): content at that edge
    saver = ThreadPoolExecutor(max_workers=2)
    state = {"previous": None, "cleared": 0, "failures": 0, "done": 0}
    started = time.time()

    def take(tile: dict, total: int, sides: list[str]) -> None:
        note = ""
        status, frame, black, mismatch = step(lambda: shoot(session, tile, settle_time, matting), f"tile {tile['index'] + 1}/{total}")
        if status is not None:
            if status.cleared_since_ready > state["cleared"]:
                note += f"  ({status.cleared_since_ready - state['cleared']} units spawned and cleared since the last tile)"
                state["cleared"] = status.cleared_since_ready
            if status.intro:
                note += "  (the intro cutscene is playing)"
            positions[str(tile["index"])] = {"x": status.camera_x, "y": status.camera_y}
            positions_path.write_text(json.dumps(positions))
            note += offset_note(status, tile)
            if mismatch:
                note += "  (kept shot doesn't match the view the tile was acknowledged in)"
        # Near the edges the game holds the camera back, so neighbouring tiles can really show
        # the same view; keep it either way (the stitch places duplicates on top of each other).
        if frame is not None and state["previous"] is not None and same_view(frame, state["previous"]):
            note += "  (same view as the previous tile)"

        if frame is None:
            state["failures"] += 1
            log(f"    tile {tile['index'] + 1} failed (no answer from the map, or black frames); not saved")
            if state["failures"] >= 3:
                first_bad = tile["index"] - state["failures"] + 1
                game = game_state(session.raw_grab() if foreground_is_game() else None, session.strip)
                if game != IN_MAP:
                    raise Recoverable(f"the match was lost at tile {first_bad + 1} ({game})", resume_at=first_bad)
                quit_match()
                raise Recoverable(f"the map stopped answering at tile {first_bad + 1}", resume_at=first_bad)
            return
        state["failures"] = 0
        state["previous"] = frame
        saver.submit(save_frame, out / f"tile_{tile['index']:04d}", frame)
        if matting:
            if black is not None:
                saver.submit(save_frame, out / f"tile_{tile['index']:04d}-black", black)
            else:
                note += "  (no shot over black; kept opaque)"
        for side in sides:
            if extend and edge_content(frame, black, matting, left, side):
                flagged.append((tile, side))
                note += f"  (the map runs on past its {side} edge)"
        state["done"] += 1
        eta = (time.time() - started) / state["done"] * max(0, total - start - state["done"])
        log(f"  tile {tile['index'] + 1}/{total}  (row {tile['row']}, col {tile['col']})  ~{eta:.0f}s left{note}")

    with stage("tiles"):
        for tile in tiles[start:]:
            sides = [side for side, at in outer.items() if (tile["row"] if side in ("top", "bottom") else tile["col"]) == at]
            take(tile, len(tiles), sides)
    with stage("tiles past the grid"):
        extend_past_grid(manifest, tiles, flagged, take, extend, session, manifest_path)
    saver.shutdown(wait=True)


def extend_past_grid(manifest: dict, tiles: list, flagged: list, take, extend: bool, session, manifest_path: Path) -> None:
    """Tiles beyond outer tiles whose outer edge had map content, outwards (see capture_tiles)."""
    if not extend:
        return
    step_x, step_y = manifest["step"]["x"], manifest["step"]["y"]
    move = {"top": (0, step_y, -1, 0), "bottom": (0, -step_y, 1, 0), "left": (-step_x, 0, 0, -1), "right": (step_x, 0, 0, 1)}
    taken = {(t["row"], t["col"]) for t in tiles}
    unbound_sent = False
    for ring in range(EDGE_RINGS):
        frontier, flagged[:] = list(flagged), []
        new = []
        for tile, side in frontier:
            dx, dy, dr, dc = move[side]
            key = (tile["row"] + dr, tile["col"] + dc)
            if key in taken:
                continue
            taken.add(key)
            new.append(({"index": len(tiles) + len(new), "row": key[0], "col": key[1], "x": tile["x"] + dx, "y": tile["y"] + dy, "edge": True}, side))
        if not new:
            break
        if not unbound_sent:
            if step(lambda: session.send("unbound"), "lifting the camera bounds") is None:
                log("  the map didn't take \"unbound\"; no tiles past the grid")
                break
            unbound_sent = True
        log(f"  the map runs on past the grid's edge at {len(new)} tiles: {len(new)} more beyond them (ring {ring + 1})")
        tiles.extend(tile for tile, _ in new)
        manifest_path.write_text(json.dumps(manifest, indent=2))  # before shooting: a resumed run knows them, and the stitch places them
        for tile, side in new:
            take(tile, len(tiles), [side])


# ------------------------------------------------------------------------------------------------
# The run
# ------------------------------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path, help="the .json written by inject.mjs")
    ap.add_argument("--game", default=DEFAULT_GAME, help=f"Heroes of the Storm folder (default {DEFAULT_GAME})")
    ap.add_argument("--battlenet", default=os.environ.get("HRS_BATTLENET"), help="the Battle.net app (Battle.net.exe), used to start Heroes when it isn't running; found automatically if not given")
    ap.add_argument("--no-launch", action="store_true", help="the map is already running")
    ap.add_argument("--launch-only", action="store_true", help="launch the map and stop (to try chat commands by hand)")
    ap.add_argument("--probe-light", action="store_true", help="diagnostic: command sequences at chosen points (HRS_PROBE_POINTS, HRS_PROBE_TILE_PATH), a shot after each")
    ap.add_argument("--probe-sky", action="store_true", help="diagnostic: one edge tile over each solid-colour skybox, to check the colour shows and is uniform")
    ap.add_argument("--probe-waits", action="store_true", help="diagnostic: the fixed waits tried shorter on sample tiles and a sky swap, compared with the current ones; and the sky pass with positions further apart")
    ap.add_argument("--probe-depth", action="store_true", help="diagnostic: only measure the sky layers' parallax (done after the tiles in every render)")
    ap.add_argument("--settle", type=float, default=0.1, help="least seconds from a move to the kept screenshot (default 0.1; the waits probe found differences only where the scene animates anyway, as at 0.5)")
    ap.add_argument("--start", type=int, default=0, help="first tile, to resume a run (default 0)")
    ap.add_argument("--monitor", type=int, help="capture this mss monitor number instead of the game window")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text())
    if not manifest.get("status"):
        sys.exit(f"{args.manifest} has no status strip: prepare the map again with inject.mjs")
    tiles = manifest["tiles"]
    out = args.manifest.parent / manifest["id"] / "tiles"
    out.mkdir(parents=True, exist_ok=True)
    if not args.start:
        for old in [*out.glob("tile_*.png"), *out.glob("tile_*.npy")]:  # an earlier run's shots would mix into this one
            old.unlink()
    set_log_file(args.manifest.parent / manifest["id"] / "log.txt")
    log(f"capture {manifest['id']}: {len(tiles)} tiles, screen {manifest['screen']['w']}x{manifest['screen']['h']}, fov {manifest.get('fov')}, {manifest['pxPerCell']} px/cell")
    # Where the camera really was for each tile (the status strip's echo), for stitch.py; kept
    # across a resumed run.
    positions_path = args.manifest.parent / manifest["id"] / "positions.json"
    positions: dict = json.loads(positions_path.read_text()) if args.start and positions_path.exists() else {}

    if not args.no_launch:
        launch_map(manifest, args.game, args.battlenet)
    if args.launch_only:
        print("\nLaunched. In the game, chat commands: 'tile <n>' moves to a tile, 'clean', 'black', 'sky <colour>', 'pause'.")
        return

    if args.probe_light:
        print(f"Lighting probe on {manifest['map']}: screenshots at chosen spots and cameras.")
    elif args.probe_waits:
        print(f"Waits probe on {manifest['map']}: sample tiles and a sky swap with shorter waits, compared with the current ones.")
    elif args.probe_depth:
        print(f"Sky depth probe on {manifest['map']}: the sky layers' parallax, measured at three camera positions.")
    elif args.probe_sky:
        print(f"Skybox probe on {manifest['map']}: one edge tile, a scripted sequence of skybox swaps, a shot after each.")
    else:
        shots = "two shots each (over white, over black)" if (manifest.get("sky") or {}).get("mode") == "matte" else "one shot each (over black; the void is black terrain)"
        print(f"Capturing {manifest['map']}: {len(tiles) - args.start} tiles, {shots}. Leave the keyboard and mouse alone.")
    with stage("launch and load"):
        wait_for_map_load(not args.no_launch)

    expected = (manifest["screen"]["w"], manifest["screen"]["h"])
    region = ScreenGrabber.monitor(args.monitor) if args.monitor else game_region()
    with ScreenGrabber(region, duplication=os.environ.get("HRS_CAPTURE", "duplication") != "mss") as screen:
        log(f"capturing {region['width']}x{region['height']} at ({region['left']}, {region['top']}) by {screen.method}")
        if (region["width"], region["height"]) != expected:
            print(f"warning: that is not the {expected[0]}x{expected[1]} the grid was planned for; the stitch will still work, at a different scale")
        session = Session(screen, int(manifest["status"].get("pageLeft", 0)))
        probe = args.probe_light or args.probe_sky or args.probe_waits or args.probe_depth
        measured = None

        def sky_work() -> None:
            """The map's own sky layers, while the map gets ready: how fast each moves against
            the map (a parallax viewer's layer speeds), and the layers themselves as images
            where the map has keyed copies of its sky."""
            nonlocal measured
            with stage("sky depth measurement"):
                measured = step(lambda: sky_layers.measure(session, manifest, out.parent), "measuring the sky layers")
            with stage("sky layer shots"):
                sky_layers.capture(session, manifest, out.parent, measured)

        start_up(session, manifest, args.manifest, out, before_ready=None if probe else sky_work)
        if args.probe_light:
            probes.probe_light(session, manifest, out)
            return
        if args.probe_sky:
            probes.probe_sky(session, manifest, out)
            return
        if args.probe_waits:
            probes.probe_waits(session, manifest, out)
            return
        if args.probe_depth:
            with stage("sky depth measurement"):
                step(lambda: sky_layers.measure(session, manifest, out.parent), "measuring the sky layers")
            with stage("leaving"):
                quit_match()
            log_timings("capture")
            return
        capture_tiles(session, manifest, args.start, args.settle, out, positions, positions_path, args.manifest)

    with stage("leaving"):
        quit_match(wait=False)  # the stitch runs while the game leaves; the next launch waits for the menu
    log(f"camera positions recorded for {len(positions)} screenshots")
    log_timings("capture")
    print(f"\n{len(manifest['tiles']) - args.start} screenshots in {out}\nnext: python stitch.py {args.manifest}")


def recover(e: Recoverable) -> None:
    """Start the run again in a fresh launch, from where it was lost: the match is left (or the
    game started again through Battle.net if it closed), the map launched anew and the tiles
    resumed from `resume_at` (screenshots and camera positions taken so far are kept). Three
    times at most per run (HRS_RECOVERIES counts them across the relaunches)."""
    done = int(os.environ.get("HRS_RECOVERIES", "0"))
    log(f"\nlost the match: {e.why}")
    if done >= 3:
        sys.exit(f"\nStopped: {e.why}; already recovered 3 times in this run.")
    argv = [a for a in sys.argv[1:] if a != "--no-launch"]
    if e.resume_at is not None:
        if "--start" in argv:
            i = argv.index("--start")
            del argv[i : i + 2]
        argv += ["--start", str(e.resume_at)]
    try:  # an error dialog still up would swallow the launch
        dismiss_failed_dialog()
    except Exception:
        pass
    log(f"recovering ({done + 1} of 3): launching the map again" + (f" and resuming at tile {e.resume_at + 1}" if e.resume_at is not None else ""))
    env = dict(os.environ, HRS_RECOVERIES=str(done + 1))
    sys.exit(subprocess.call([sys.executable, sys.argv[0], *argv], env=env))


if __name__ == "__main__":
    try:
        main()
    except Recoverable as e:
        recover(e)
    except SystemExit:
        raise
    except Exception:
        import traceback

        # Into the log too, so a failed run can be diagnosed from the copied results.
        log(traceback.format_exc())
        raise
