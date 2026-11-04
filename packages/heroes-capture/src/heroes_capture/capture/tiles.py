"""Launch a prepared battleground and screenshot every tile of its capture grid.

    heroes-capture capture tmp/towers-of-doom-structures.json [options]   (heroes-capture map render runs it)

The injected map script (map/script.py, assembled from its parts) takes commands typed into its
command box and reports through its status strip (map/status.py) when each is done and where the
camera really is.
The capture:

  1. starts Heroes if needed and launches the map (common/game_control.py, map/match.py);
  2. waits for the strip and checks it is the map prepared for this run; while the map gets ready
     (gates open, opening timers cut short, animations paused) it measures and shoots the map's
     own sky layers, where there are any (sky_layers.py);
  3. measures the camera bounds the game applies and re-plans the grid from them;
  4. runs what the map's category adds before the tiles (map/specifics.py: a battleground's
     elements capture, with --structures elements);
  5. for each tile: `tile <n> <x> <y>` and the kept shot, and with matting `black` and the same
     view over the black skybox; the camera positions go to positions.json;
  6. leaves the match without waiting for the menu (the stitch runs meanwhile; the next launch
     waits for it).

When the match is lost (the game closes, the map fails to load, another map is running) the run
is launched again and resumes from the first missing tile, three times at most.

Captures the game window itself, on whichever monitor it is. Run the game in
"Windowed (Fullscreen)" at the resolution given to prepare, and leave the mouse and keyboard
alone while it captures.
"""

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from heroes_capture.capture import calibration, sky_layers
from heroes_capture.capture.diagnostics import probes
from heroes_capture.capture.grid import plan_tiles
from heroes_capture.capture.shots import SWITCH_CAP, shot_over_black, tile_command
from heroes_capture.capture.sky import LIGHT_LEVEL
from heroes_capture.common import ui
from heroes_capture.common.frames import frame_exists, load_frame, save_frame, tile_stem
from heroes_capture.common.game_control import (
    FocusLost,
    GamePaused,
    Recoverable,
    dismiss_failed_dialog,
    keep_game_on_monitor,
    require_focus,
    settle,
    step,
)
from heroes_capture.common.game_data import find_install
from heroes_capture.common.geometry import circle_box, screen_point
from heroes_capture.common.game_menus import ScriptBroken, game_menu_open, interface_all_shown
from heroes_capture.common.game_state import IN_MAP, LOADING, MAP_FAILED, MENU, NOT_RUNNING, PHASES, game_state
from heroes_capture.common.game_window import game_region, hold_key
from heroes_capture.common.jsonio import read_json, write_json, write_manifest
from heroes_capture.common.runlog import command_time, detail, done, log, log_timings, set_log_file, stage, warn
from heroes_capture.common.screen import ScreenGrabber, disagree, looks_black, same_view, view_shift
from heroes_capture.common.watch import start_watch
from heroes_capture.map.match import launch_map, quit_match, send_chat, send_command, wait_for_map_load
from heroes_capture.map.specifics import Specifics
from heroes_capture.map.status import Status, StatusStrip



def plan_grid(manifest: dict, bounds: dict) -> None:
    """Re-plan the capture grid for other camera bounds (capture/prepare.py's plan_grid), in
    place: tiles, area, cameraBounds, cols, rows, step. Camera targets stay inside the bounds
    (the game clamps any beyond them), first and last on the bounds, the rest spread evenly,
    at most `keep` of a screen apart."""
    grid = plan_tiles(bounds, manifest["screen"]["w"] / manifest["pxPerCell"], manifest["screen"]["h"] / manifest["pxPerCell"], manifest["keep"])
    manifest.update(grid, area=dict(bounds), cameraBounds=dict(bounds))


class Session:
    """The game window being captured: frames from it, and the map script's status strip in
    them (read from every raw frame; kept shots have its column blanked, and the stitch leaves
    that column out)."""

    def __init__(self, screen: ScreenGrabber, strip_width: int):
        self.screen = screen
        self.strip = StatusStrip()
        self.strip_width = strip_width
        self._seq = 0
        self._menu_checked = 0.0
        self.team = 1  # our player's side (1 Order, 2 Chaos): "side" switches it, for the team-colour pairs

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

    def read(self, raw: np.ndarray, waited: bool = False) -> Status | None:
        """The strip's status in a raw frame; None if it can't be read. When it can't (at most
        twice a second), or a command went unanswered (`waited`), and one of the game's menus is
        open (Esc, Options, the exit dialog), the step stops: GamePaused, which waits for the menu
        to close and redoes the step, as a lost focus does."""
        status = self.strip.read(raw)
        if (status is None and time.time() >= self._menu_checked) or waited:
            self._menu_checked = time.time() + 0.5
            if game_menu_open(raw):
                raise GamePaused
        return status

    def status(self) -> Status | None:
        return self.read(self.raw_grab())

    def camera(self, default: dict | None = None) -> dict:
        """Where the camera is ({x, y} in cells), from the strip; `default` (or {}) when it can't be read."""
        status = self.status()
        return status.camera if status else dict(default or {})

    def strip_box(self) -> tuple[int, int, int, int] | None:
        """The screen's top-left corner the strip is in (x0, y0, x1, y1 from the region's corner),
        once located: what polling grabs (a sliver of the screen, not a whole frame)."""
        extent = self.strip.extent()
        if extent is None:
            return None
        width, height = extent
        return 0, 0, min(width, self.screen.region["width"]), min(height, self.screen.region["height"])

    def poll(self, check, timeout: float = 1.0) -> tuple[Status, np.ndarray] | None:
        """A frame whose strip passes `check` (a Status -> bool), grabbed within `timeout`: its
        status and the raw frame; None when none did (and then, if a menu is open over the map, the
        step stops: read). Once the strip is located, it is watched in its own corner of the screen
        (strip_box), and a whole frame grabbed once that passes (the frame the caller gets), or to
        look for a menu while the strip can't be read."""
        deadline = time.time() + timeout
        box = self.strip_box()
        while time.time() < deadline:
            if box is not None:
                require_focus()
                corner = self.screen.grab(box)
                require_focus()
                seen = self.strip.read(corner)
                if seen is None or not check(seen):
                    if seen is None and time.time() >= self._menu_checked:
                        self.read(self.raw_grab())  # (a menu over the map: GamePaused)
                    time.sleep(0.02)
                    continue
            raw = self.raw_grab()
            status = self.read(raw)
            if status is not None and check(status):
                return status, raw
            time.sleep(0.02)
        self.read(self.raw_grab(), waited=True)
        return None

    def fresh(self, timeout: float = 1.0) -> np.ndarray | None:
        """A frame grabbed now in which the strip still shows the last command's number: the
        game drew it after that command was carried out, and nothing else was sent since."""
        found = self.poll(lambda st: st.seq == self._seq, timeout)
        return found[1] if found else None

    def send(self, command: str, timeout: float = 2.0, sends: int = 4) -> tuple[Status, np.ndarray] | None:
        """A command typed into the map's command box with a sequence number appended, then the
        strip polled until it shows that number: the command has been carried out and the frame
        rendered. Returns the status and that raw frame; None when the map never answered (sent
        `sends` times, `timeout` seconds each). Before each retry, the chat's "focus" gives the
        box the keyboard back, in case it lost it (see map/script.galaxy's command box). Its round
        trip is counted for its stage's line in the diagnostic log (runlog.command_time)."""
        words = command.split()
        verb = " ".join(words[:2]) if words[:1] == ["el"] else (words[0] if words else "")
        started = time.time()
        answer = self._send(command, timeout, sends)
        command_time(verb, time.time() - started, answer is not None)
        return answer

    def _send(self, command: str, timeout: float, sends: int) -> tuple[Status, np.ndarray] | None:
        for attempt in range(sends):
            self.read(self.raw_grab())  # a menu opened since the last command: wait, rather than type into it
            if attempt:
                detail(f"no answer to \"{command}\"; giving the command box the keyboard back (chat \"focus\")")
                self._seq = self._seq % 255 + 1
                send_chat(f"focus {self._seq} ;")
                self.poll(lambda st: st.seq == self._seq, timeout=1.0)
            self._seq = self._seq % 255 + 1  # 1..255; 0 is what the strip shows before any command
            send_command(f"{command} {self._seq}")
            answer = self.poll(lambda st: st.seq == self._seq, timeout)  # unanswered: a menu open over the map takes the keys
            if answer is not None:
                return answer
        return None

    def command(self, text: str, timeout: float = 3.0) -> Status | None:
        """A command whose answer only matters as far as a warning: the status it was answered with,
        or None (warned)."""
        answer = self.send(text, timeout=timeout)
        if answer is None:
            warn("the map didn't answer a command; carrying on")
            detail(f"no answer to \"{text}\"")
            return None
        return answer[0]

    def side(self, team: int) -> bool:
        """Our player put on a team ("side <team>": 1 Order, 2 Chaos); whether the map answered."""
        if self.send(f"side {team}", timeout=3.0) is None:
            return False
        self.team = team
        return True

    def move_camera(self, x: float, y: float, timeout: float = 3.0) -> Status | None:
        """The camera to a cell ("tile 0 <x> <y>": the capture camera, no tile's scene), once it has
        stopped; the status it was answered with (the camera where the bounds let it go), or None."""
        answer = self.send(f"tile 0 {x:.2f} {y:.2f}", timeout=timeout)
        return answer[0] if answer else None


# ------------------------------------------------------------------------------------------------
# Start-up: the strip, the map's identity, the map ready, the first tile, the camera bounds
# ------------------------------------------------------------------------------------------------


def wait_for_strip(session: Session, out: Path) -> int:
    """The status strip: drawn by the map script from its start, but the interface is hidden
    while the intro cutscene plays. Nothing is typed until it shows. Raises Recoverable when the
    game closes, the map fails to load, the menu comes back, or 3 minutes pass; ScriptBroken when
    every interface panel shows instead (the script failed to compile: no relaunch helps).
    Returns the width of the column blanked for the strip from then on: its two columns of cells
    as they came out on this screen (whatever the resolution and interface scale) and a margin."""
    deadline = time.time() + 180
    log("waiting for the map's status strip")
    failed_since, menu_since, dumped, broken_looks, broken_checked = None, None, False, 0, 0.0
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
            warn("no status strip after 30 s (the screen's left edge is saved as strip-missing.png)")
        if state == LOADING and time.time() - broken_checked >= 1.0:
            broken_checked = time.time()
            broken_looks = broken_looks + 1 if interface_all_shown(frame) else 0
            if broken_looks >= 2:
                small = Image.fromarray(np.ascontiguousarray(frame)).reduce(2)
                small.save(out.parent / "script-broken.png")
                raise ScriptBroken("the map's script failed to compile: every interface panel is showing (Blizzard's debug menu "
                                   f"among them; a shot is in {out.parent / 'script-broken.png'}). That's a heroes-capture bug, "
                                   "not something a relaunch fixes; leave the match by hand (Esc, Quit).")
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
    detail(f"status strip: {strip.cell_w}x{strip.cell} px cells at {strip.origin}")
    session.strip_width = 2 * strip.cell_w + strip.origin[0] + STRIP_MARGIN
    detail(f"{session.strip_width} px blanked at the left edge for it")
    return session.strip_width


def wait_until_ready(session: Session) -> None:
    """The map ready, before anything else is measured or shot: the gates open early (3 s), the
    map's opening timers are cut short so its first objective is in place (the camera bounds can
    change then too), and the script pauses every animation; nothing is sent to the map until
    it is over. Each phase the strip reports is logged."""
    log("waiting for the map to be ready")
    started = time.time()
    last_phase = None

    def ready() -> bool:
        nonlocal last_phase
        status = session.status()
        if status and status.phase != last_phase:
            last_phase = status.phase
            log(f"map phase: {PHASES[status.phase]} (game clock {status.game_seconds} s)")
        return bool(status and status.ready)

    while not step(ready, "waiting for the game to start"):
        if time.time() - started > 240:
            warn("the map didn't report ready within 4 minutes; pausing the animations now")
            step(lambda: session.send("pause"), "pausing the animations")
            return
        time.sleep(1.0)
    status = step(session.status, "reading the strip")
    cuts = f", {status.opening_cuts} opening timers cut short" if status and status.opening_cuts else ""
    log(f"the map is ready after {time.time() - started:.0f} s (game clock {status.game_seconds} s{cuts})" if status
        else f"the map is ready after {time.time() - started:.0f} s")


def game_bounds(session: Session, manifest: dict) -> dict | None:
    """The camera bounds the game applies right now: where the camera stops when sent to the
    map's corners (None if the map didn't answer, or the result makes no sense)."""
    size = manifest["mapSize"]
    low = step(lambda: session.move_camera(0, 0), "the bounds check")
    high = step(lambda: session.move_camera(size["width"], size["height"]), "the bounds check")
    if not (low and high):
        return None
    bounds = {"left": low.camera_x, "bottom": low.camera_y, "right": high.camera_x, "top": high.camera_y}
    return bounds if bounds["left"] < bounds["right"] and bounds["bottom"] < bounds["top"] else None


def measure_bounds(session: Session, manifest: dict, manifest_path: Path) -> None:
    """The camera bounds the game really applies (an arena's are far tighter than its map file
    says): the camera is sent to the map's corners and the strip reports where it stopped. A
    grid planned for the wrong bounds spends most tiles on sky. The grid is re-planned (and the
    manifest rewritten) when they differ from the map file's."""
    bounds = game_bounds(session, manifest)
    if bounds is None:
        warn("couldn't measure where the camera can go in the game; the grid stays as the map file has it")
        return
    planned = manifest["cameraBounds"]
    if any(abs(bounds[k] - planned[k]) > 2 for k in bounds):
        plan_grid(manifest, bounds)
        write_manifest(manifest_path, manifest)
        def span(b: dict) -> str:
            return f"x {b['left']:g}–{b['right']:g}, y {b['bottom']:g}–{b['top']:g}"

        log(f"camera range in the game {span(bounds)}: grid now {manifest['cols']}×{manifest['rows']} = {len(manifest['tiles'])} tiles")
        detail(f"the map file says {span(planned)}")
    else:
        detail(f"camera bounds in the game match the map file: {bounds}")


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
            detail("the map hasn't taken a command yet (an intro or a selection phase)")
            time.sleep(1.0)

    with stage("Status strip"):
        if manifest.get("keepIntro"):
            # Diagnostic: the intro plays out. Nothing is typed meanwhile, in case keys count as
            # skipping it.
            log("letting the intro cutscene play out (nothing typed for 60 s)")
            step(lambda: settle(60.0), "the wait for the intro")
        # The blanked column's width goes into the manifest (status.pageLeft): the stitch leaves
        # it out of every tile, and a resumed run blanks it from its first frame.
        manifest["status"]["pageLeft"] = wait_for_strip(session, out)
        write_manifest(manifest_path, manifest)
        # The map running is the one prepared for this run (not one an earlier run left).
        expected_id = manifest["status"].get("mapId")
        seen = step(session.status, "reading the strip")
        if expected_id is not None and seen is not None and seen.map_id != expected_id:
            quit_match()
            raise Recoverable(f"another map is running (identity {seen.map_id}, expected {expected_id})")
    if before_ready is not None:
        with stage("First command"):
            until_commands_taken()
        before_ready()
    with stage("Map ready"):
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
        detail("half-drawn frame; retaking")
        settle(0.1)
        reference, frame = frame, session.grab(dark_ok=True)
    return frame, False


# Cells round a team-coloured doodad watched for a side switch (team_boxes), and how far past a
# tile's view one may stand and still reach into it (a starting platform, an outer wall).
TEAM_RADIUS, TEAM_REACH = 3.0, 6.0


def team_boxes(manifest: dict, camera: dict, shape: tuple) -> list[tuple[int, int, int, int]] | None:
    """The screen boxes round the team-coloured doodads in a tile's view (manifest teamColoured,
    from the map's Objects file), watched for a side switch; None when there are none: nothing in
    the view changes with the side."""
    ppc, screen = manifest["pxPerCell"], manifest["screen"]
    half_w, half_h = screen["w"] / ppc / 2 + TEAM_REACH, screen["h"] / ppc / 2 + TEAM_REACH
    near = [{"x": x, "y": y} for x, y in manifest.get("teamColoured") or []
            if abs(x - camera["x"]) <= half_w and abs(y - camera["y"]) <= half_h]
    if not near:
        return None
    return [circle_box(screen_point(cell, camera, screen, ppc), TEAM_RADIUS * ppc, shape) for cell in near]


def shoot_once(session: Session, tile: dict, settle_time: float, manifest: dict):
    """(the strip's status at the tile: where the camera really is; the kept shot; the same view
    over the black skybox when matting; whether the kept shot doesn't match the view the tile
    was acknowledged in). From Order's side: the team-coloured tiles are shot again from Chaos's
    after every tile (capture_team_side)."""
    matting = (manifest.get("sky") or {}).get("mode") == "matte"
    if session.team != 1 and session.side(1):  # a step done again after a lost focus, left on Chaos
        settle(SWITCH_CAP)
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
        black = shot_over_black(session, frame)
    return status, frame, black, mismatch


def shoot_chaos(session: Session, tile: dict, settle_time: float, manifest: dict) -> np.ndarray | None:
    """A team-coloured tile again from Chaos's side, shot as the tiles from Order are: our player
    was put on Chaos once before the pass, so the view is drawn in its colours as the camera gets
    there (no wait for them to change: where little team colour shows, nothing visibly changes,
    and that wait sat out its whole cap). Over the light sky only, matting or not: the stitch takes
    the see-through share from the tile's pair from Order. None without a view or anything
    team-coloured in it."""
    answer = session.send(tile_command(tile), timeout=3.0)
    if answer is None:
        return None
    moved = time.time()
    status, reference = answer[0], session.blank(answer[1])
    wait = moved + settle_time - time.time()
    if wait > 0:
        settle(wait)
    raw = session.fresh()
    frame = whole_frame(session, session.blank(raw), reference)[0] if raw is not None else None
    if frame is None or team_boxes(manifest, status.camera, frame.shape) is None:
        return None
    return frame  # (no shot over black: the stitch takes the see-through share from the tile's pair from Order, stitch.Shots.load)


def shoot(session: Session, tile: dict, settle_time: float, manifest: dict):
    """One tile from Order's side, start to finish, as one step. A tile whose kept shot isn't the
    view it was acknowledged in (something moved the camera in between) is done again, once."""
    result = shoot_once(session, tile, settle_time, manifest)
    if result[3] and result[1] is not None:
        detail(f"tile {tile['index'] + 1}: the view changed under it; doing it again")
        result = shoot_once(session, tile, settle_time, manifest)
    return result


def offset_note(status: Status, tile: dict) -> str:
    """Near the edges the game holds the camera back, which is worth a note."""
    off_x, off_y = status.camera_x - tile["x"], status.camera_y - tile["y"]
    parts = []
    if abs(off_x) > 0.5:
        parts.append(f"{abs(off_x):.1f} cells {'east' if off_x > 0 else 'west'}")
    if abs(off_y) > 0.5:
        parts.append(f"{abs(off_y):.1f} cells {'north' if off_y > 0 else 'south'}")
    return "; camera held " + " and ".join(parts) + " of plan" if parts else ""


STRIP_MARGIN = 4  # pixels blanked past the status strip's two columns (wait_for_strip)
EDGE_BAND = 40  # pixels along a frame's outer edge checked for map content running on past it
EDGE_CONTENT = 200  # this many content pixels in the band: the map goes on past the grid there
EDGE_RINGS = 3  # at most this many tiles past the grid on any side


def edge_content(frame: np.ndarray, black: np.ndarray | None, matting: bool, left: int, side: str) -> bool:
    """Whether map content reaches the frame's edge on that side (top, bottom, left, right): mostly
    opaque pixels in the outermost band (matte maps: what differs little between the shots over
    the light sky and black; maps whose void is black terrain: anything not near-black). The status
    strip's column is left out."""
    band = {
        "top": np.s_[:EDGE_BAND, left:],
        "bottom": np.s_[-EDGE_BAND:, left:],
        "left": np.s_[:, left : left + EDGE_BAND],
        "right": np.s_[:, -EDGE_BAND:],
    }[side]
    light = frame[band].astype(np.int16)
    if matting and black is not None:
        content = (light - black[band].astype(np.int16)).mean(axis=2) < LIGHT_LEVEL / 2  # more than half opaque
    elif matting:
        content = (np.abs(light - LIGHT_LEVEL).max(axis=2) > 16) | (light.max(axis=2) - light.min(axis=2) > 20)  # not the light sky
    else:
        content = light.max(axis=2) > 20
    return int(content.sum()) >= EDGE_CONTENT


def outer_edges(tiles: list, areas: list | None) -> dict[int, dict[str, int]]:
    """Per area (0 on a map of one), its grid's outer rows and columns: {side: row or column}.
    On a map of several arenas, not the sides facing another arena: past those is the other
    arena, not more of this one."""
    edges = {}
    for k in sorted({t.get("area", 0) for t in tiles}):
        mine = [t for t in tiles if t.get("area", 0) == k]
        rows, cols = [t["row"] for t in mine], [t["col"] for t in mine]
        edges[k] = {"top": min(rows), "bottom": max(rows), "left": min(cols), "right": max(cols)}
        if areas:
            a = areas[k]["bounds"]
            for other in (o["bounds"] for i, o in enumerate(areas) if i != k):
                across = other["left"] < a["right"] and other["right"] > a["left"]
                along = other["bottom"] < a["top"] and other["top"] > a["bottom"]
                for side, faces in (("top", across and other["bottom"] >= a["top"]), ("bottom", across and other["top"] <= a["bottom"]),
                                    ("left", along and other["right"] <= a["left"]), ("right", along and other["left"] >= a["right"])):
                    if faces:
                        edges[k].pop(side, None)
    return edges


def capture_tiles(session: Session, manifest: dict, start: int, settle_time: float, out: Path, positions: dict, positions_path: Path, manifest_path: Path) -> None:
    """Every tile from `start`, saved as PNGs (written in the background while the next tile is
    shot), with the camera positions in positions.json. Three tiles in a row that fail raise
    Recoverable, resuming at the first of them.

    Then past the grid, where the map runs on: an outer tile with map content at its outer edge
    (Battlefield of Eternity's arches past the camera bounds; on a map of several arenas, an
    arena's edges that face no other arena) gets a tile beyond it, the camera bounds lifted to
    the whole map, and so on outwards (EDGE_RINGS at most). Those tiles are added
    to the manifest, so the stitch places them like the rest."""
    tiles = manifest["tiles"]
    matting = (manifest.get("sky") or {}).get("mode") == "matte"
    left = int((manifest.get("status") or {}).get("pageLeft", 0))
    outer = outer_edges(tiles, manifest.get("areas"))

    def sides_of(tile: dict) -> list[str]:
        """The grid's outer edges this tile is on."""
        at = outer.get(tile.get("area", 0), {})
        return [side for side, edge in at.items() if (tile["row"] if side in ("top", "bottom") else tile["col"]) == edge]

    flagged: list[tuple[dict, str]] = []  # (tile, side): content at that edge
    saver = ThreadPoolExecutor(max_workers=SAVERS)
    state = {"previous": None, "cleared": 0, "failures": 0, "done": 0, "advance": lambda n=1: None}

    def take(tile: dict, total: int, sides: list[str]) -> None:
        note = ""
        try:
            status, frame, black, mismatch = step(lambda: shoot(session, tile, settle_time, manifest), f"tile {tile['index'] + 1}/{total}")
        except Recoverable as e:
            if e.resume_at is None:  # lost during this tile (the game closed): the fresh launch carries on at it
                e.resume_at = tile["index"]
            raise
        if status is not None:
            if status.cleared_since_ready > state["cleared"]:
                note += f"; {status.cleared_since_ready - state['cleared']} units cleared"
                state["cleared"] = status.cleared_since_ready
            if status.intro:
                note += "; the intro is playing"
            positions[str(tile["index"])] = status.camera
            write_json(positions_path, positions, indent=None)
            note += offset_note(status, tile)
            if mismatch:
                note += "; the view moved under it"
        # Near the edges the game holds the camera back, so neighbouring tiles can really show
        # the same view; keep it either way (the stitch places duplicates on top of each other).
        if frame is not None and state["previous"] is not None and same_view(frame, state["previous"]):
            note += "; same view as the tile before"

        if frame is None:
            state["failures"] += 1
            warn(f"tile {tile['index'] + 1}: no picture (the map didn't answer, or the frames were black); not saved")
            if state["failures"] >= 3:
                first_bad = tile["index"] - state["failures"] + 1
                try:
                    frame_now = session.raw_grab()
                except FocusLost:
                    frame_now = None
                game = game_state(frame_now, session.strip)
                if game != IN_MAP:
                    raise Recoverable(f"the match was lost at tile {first_bad + 1} ({game})", resume_at=first_bad)
                quit_match()
                raise Recoverable(f"the map stopped answering at tile {first_bad + 1}", resume_at=first_bad)
            return
        state["failures"] = 0
        state["previous"] = frame
        saver.submit(save_frame, tile_stem(out, tile["index"]), frame)
        if matting:
            if black is not None:
                saver.submit(save_frame, tile_stem(out, tile["index"], black=True), black)
            else:
                note += "; no shot over black (kept opaque)"
        for side in sides:
            if edge_content(frame, black, matting, left, side):
                flagged.append((tile, side))
                note += f"; the map runs on past its {side} edge"
        state["done"] += 1
        state["advance"]()
        log(f"tile {tile['index'] + 1} of {total}{note}")

    if start:
        # A resumed run: the outer tiles shot before it still count (from their saved shots).
        for tile in tiles[:start]:
            sides = sides_of(tile)
            if sides and frame_exists(tile_stem(out, tile["index"])):
                frame = load_frame(tile_stem(out, tile["index"]))
                black = load_frame(tile_stem(out, tile["index"], black=True)) if frame_exists(tile_stem(out, tile["index"], black=True)) else None
                flagged.extend((tile, side) for side in sides if edge_content(frame, black, matting, left, side))
    if any(t.get("edge") for t in tiles[start:]):
        # Tiles past the grid left by the run this one resumes: the camera bounds lifted again.
        step(lambda: session.send("unbound"), "lifting the camera bounds")
    with stage("Tiles (blue)", len(tiles), start) as counter:  # a resumed run's bar starts at the tiles kept
        state["advance"] = counter.advance
        for tile in tiles[start:]:
            take(tile, len(tiles), sides_of(tile))
    state["advance"] = lambda n=1: None
    with stage("Tiles past the edge"):
        extend_past_grid(manifest, tiles, flagged, take, session, manifest_path)
    saver.shutdown(wait=True)
    capture_team_side(session, manifest, settle_time, out, positions)


def capture_team_side(session: Session, manifest: dict, settle_time: float, out: Path, positions: dict) -> None:
    """The tiles with team-coloured doodads in view again from Chaos's side (the stitch's
    team-colour mask), in one pass after them all: our player put on Chaos once and back once, not
    twice a tile (the scene is paused: the two passes are the same pixels but for the team colour).
    The tiles whose shots from Chaos's side are missing only (a resumed run carries on)."""
    shape = (int(manifest["screen"]["h"]), int(manifest["screen"]["w"]))
    todo = [t for t in manifest["tiles"] if str(t["index"]) in positions and frame_exists(tile_stem(out, t["index"]))
            and not frame_exists(tile_stem(out, t["index"], chaos=True)) and team_boxes(manifest, positions[str(t["index"])], shape)]
    if not todo:
        return
    if not session.side(2):
        warn("couldn't switch to the red team: the tiles have no team colour")
        return
    settle(SWITCH_CAP)
    saver = ThreadPoolExecutor(max_workers=SAVERS)
    with stage("Tiles (red)", len(todo)) as counter:
        for tile in todo:
            log(f"tile {tile['index'] + 1} of {len(manifest['tiles'])}")
            frame = step(lambda: shoot_chaos(session, tile, settle_time, manifest), f"tile {tile['index'] + 1} as red")
            if frame is not None:
                saver.submit(save_frame, tile_stem(out, tile["index"], chaos=True), frame)
            else:
                warn(f"tile {tile['index'] + 1}: no picture as red")
            counter.advance()
    saver.shutdown(wait=True)
    if session.side(1):
        settle(SWITCH_CAP)
    detail(f"{len(todo)} tiles shot again as red, in one pass")


def extend_past_grid(manifest: dict, tiles: list, flagged: list, take, session, manifest_path: Path) -> None:
    """Tiles beyond outer tiles whose outer edge had map content, outwards (see capture_tiles);
    each in its arena on a map of several."""
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
            new.append(({"index": len(tiles) + len(new), "row": key[0], "col": key[1], "x": tile["x"] + dx, "y": tile["y"] + dy, "edge": True,
                         **({"area": tile["area"]} if "area" in tile else {})}, side))
        if not new:
            break
        if not unbound_sent:
            if step(lambda: session.send("unbound"), "lifting the camera bounds") is None:
                warn("the map didn't take \"unbound\"; no tiles past the grid")
                break
            unbound_sent = True
        log(f"the map runs on past the grid's edge: {len(new)} more tiles" + (f" (ring {ring + 1})" if ring else ""))
        tiles.extend(tile for tile, _ in new)
        write_manifest(manifest_path, manifest)  # before shooting: a resumed run knows them, and the stitch places them
        with ui.bar(len(new), f"Ring {ring + 1}") as advance:
            for tile, side in new:
                take(tile, len(tiles), [side])
                advance()


# ------------------------------------------------------------------------------------------------
# The run
# ------------------------------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="heroes-capture capture", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path, help="the .json written by prepare")
    ap.add_argument("--game", help="Heroes of the Storm folder (found from the install by default)")
    ap.add_argument("--battlenet", default=os.environ.get("HRS_BATTLENET"), help="the Battle.net app (Battle.net.exe), used to start Heroes when it isn't running; found automatically if not given")
    ap.add_argument("--no-launch", action="store_true", help="the map is already running")
    ap.add_argument("--launch-only", action="store_true", help="launch the map and stop (to try the commands by hand)")
    ap.add_argument("--calibrate", nargs="?", const="", default=None, metavar="STAGES",
                    help="the map's calibration instead of the tiles (capture/calibration.py; prepared with --calibrate): "
                         "every stage, or those named (comma-separated)")
    for name, (_, what) in probes.PROBES.items():
        ap.add_argument(f"--probe-{name}", action="store_true", help=f"diagnostic: {what}")
    ap.add_argument("--settle", type=float, default=0.1, help="least seconds from a move to the kept screenshot (default 0.1: shorter showed differences only where the scene animates anyway)")
    ap.add_argument("--start", type=int, default=0, help="first tile, to resume a run (default 0)")
    ap.add_argument("--parts", default=",".join(PARTS), help=f"which parts to capture, comma-separated (default all: {', '.join(PARTS)}); "
                    "elements also brings the structures down before tiles")
    ap.add_argument("--reuse", action="store_true", help="keep the shots already in the run's folder (a job's run of some parts: "
                    "it removed what it reshoots; the elements capture shoots only what its record lacks)")
    ap.add_argument("--monitor", help="put the game on this monitor before launching the map (its number as Windows lists them, its device name, or primary); the map then runs at its resolution")
    ap.add_argument("--monitor-region", type=int, help="diagnostic: capture this mss monitor number instead of the game window")
    return ap.parse_args(argv)


# A capture's parts, in the order they run: the map's sky layers (while the map gets ready), what its
# category does before the tiles (the battlegrounds' elements, which bring the structures down: the
# tiles are the bare terrain), the tiles.
PARTS = ("skies", "elements", "tiles")
# The threads saving the tiles' screenshots: a run stopped mid-save may leave this many half-written
# (cli.SAVERS is the same).
SAVERS = 2


def main(argv: list[str], specifics_of: Callable[[dict], Specifics]) -> None:
    """Capture a prepared map; specifics_of(manifest) gives what its category and the map itself
    add (cli.specifics_of). --parts: only some of it (the tiles need the structures down: elements
    runs with them, shooting only what its record lacks)."""
    args = parse_args(argv)
    parts = {p for p in args.parts.split(",") if p}
    if parts - set(PARTS):
        sys.exit(f"no capture part {', '.join(sorted(parts - set(PARTS)))} (the parts: {', '.join(PARTS)})")
    manifest = read_json(args.manifest)
    specifics = specifics_of(manifest)
    if not manifest.get("status"):
        sys.exit(f"{args.manifest} has no status strip: prepare the map again")
    tiles = manifest["tiles"]
    out = args.manifest.parent / manifest["id"] / "tiles"
    out.mkdir(parents=True, exist_ok=True)
    if not args.start and not args.reuse:
        # An earlier run's shots would mix into this one (its sky shots and measurement too: the
        # stitch builds sky layers from whatever it finds).
        sky_dir = out.parent / "sky"
        for old in [*out.glob("tile_*.png"), *out.glob("tile_*.npy"), *sky_dir.glob("*.png"), *sky_dir.glob("*.npy"), sky_dir / "positions.json", out.parent / "sky-layers.json"]:
            if old.exists():
                old.unlink()
    set_log_file(args.manifest.parent / manifest["id"] / "log.txt")
    log(f"screen {manifest['screen']['w']}×{manifest['screen']['h']}, {manifest['pxPerCell']:g} px per cell")
    detail(f"capture {manifest['id']}: {len(tiles)} tiles, fov {manifest.get('fov')}")
    # Where the camera really was for each tile (the status strip's echo), for stitch.py; kept
    # across a resumed run.
    positions_path = args.manifest.parent / manifest["id"] / "positions.json"
    positions: dict = read_json(positions_path) if (args.start or args.reuse) and positions_path.exists() else {}

    probe = next((name for name in probes.PROBES if getattr(args, f"probe_{name.replace('-', '_')}")), None)
    if probe:
        log(f"probe {probe}: {probes.PROBES[probe][1]}")
    elif args.calibrate is not None:
        only = [n for n in args.calibrate.split(",") if n]
        log(f"calibrating: {', '.join(calibration.chosen_stages(only, specifics.calibration_stages, specifics.calibration_probes))}")
    elif not args.launch_only:
        shots = "two shots each, over light grey and over black" if (manifest.get("sky") or {}).get("mode") == "matte" else "one shot each, over black"
        log(f"{len(tiles)} tiles, {shots}" + (f"; carrying on at tile {args.start + 1}" if args.start else ""))
        # What the capture does, in the footer from the start (the map's own passes plan theirs).
        ui.plan("Launching the map")
        ui.plan("Starting up")
        if "tiles" in parts:
            ui.plan("Tiles (blue)", len(tiles))
            ui.plan("Tiles past the edge")
            ui.plan("Tiles (red)")
        ui.plan("Leaving the match")
    if not args.launch_only:
        warn("leave the keyboard and mouse alone until the capture is done")
    with stage("Launching the map"):
        if not args.no_launch:
            game = args.game or find_install()
            if not game:
                sys.exit("Heroes of the Storm's install wasn't found (uninstall entries, the Battle.net app's list, the usual folders); pass --game")
            launch_map(manifest, str(game), args.battlenet, args.monitor)
        if args.launch_only:
            done("Launched. In the game, type commands ending in ';' (the map's command box has the keyboard): 'tile <n>;' moves to a tile, 'clean;', 'black;', 'sky <colour>;', 'pause;'.")
            return
        wait_for_map_load(not args.no_launch)
        if args.monitor:
            keep_game_on_monitor(args.monitor, "as the map loaded")

    expected = (manifest["screen"]["w"], manifest["screen"]["h"])
    region = ScreenGrabber.monitor(args.monitor_region) if args.monitor_region else game_region()
    with ScreenGrabber(region, duplication=os.environ.get("HRS_CAPTURE", "duplication") != "mss") as screen:
        detail(f"capturing {region['width']}x{region['height']} at ({region['left']}, {region['top']}) by {screen.method}")
        if os.environ.get("HRS_WATCH"):
            # Beside the run's folder, which a finished render removes.
            watch_folder = out.parent.parent / f"watch-{time.strftime('%H%M%S')}"
            start_watch(region, watch_folder)
            log(f"watching the screen into {watch_folder} (HRS_WATCH)")
        if (region["width"], region["height"]) != expected:
            warn(f"that is not the {expected[0]}x{expected[1]} the grid was planned for; the stitch will still work, at a different scale")
        session = Session(screen, int(manifest["status"].get("pageLeft", 0)))
        measured = None
        sky_waits = False  # the sky work put off until the map is ready (its world was hidden)

        def sky_work(wait_if_hidden: bool = False) -> None:
            """The map's own sky layers, while the map gets ready: how fast each moves against
            the map (a parallax viewer's layer speeds), and the layers themselves as images
            where the map has keyed copies of its sky. Over the camera bounds the game applies
            (the grid is re-planned from them only once the map is ready, as its opening can
            change them; the sky only needs the camera to reach where it is sent)."""
            nonlocal measured, sky_waits
            if not (manifest.get("sky") or {}).get("mapSky", {}).get("parallax"):
                return
            if wait_if_hidden:
                # An in-game hero selection (Punisher Arena) fades to black and hides the world
                # until the picks are done, though the map takes commands: the shots would be black.
                frame = step(lambda: session.grab(dark_ok=True), "a look at the map")
                if frame is not None and float((frame.max(axis=2) > 16).mean()) < 0.01:
                    log("the map isn't drawn yet (an in-game hero selection?); the sky waits until the map is ready")
                    sky_waits = True
                    return
            area = None if manifest.get("unbound") else game_bounds(session, manifest)
            with stage("Measuring the sky layers"):
                measured = step(lambda: sky_layers.measure(session, manifest, out.parent, area), "measuring the sky layers")
            with stage("Shooting the sky layers"):
                sky_layers.capture(session, manifest, out.parent, measured, area=area)

        # A resumed run (after a recovery) keeps the sky work this run already did.
        sky_done = "skies" not in parts or (args.start and (out.parent / "sky-layers.json").exists() and (out.parent / "sky" / "positions.json").exists())
        with stage("Starting up"):
            start_up(session, manifest, args.manifest, out, before_ready=None if probe or args.calibrate is not None or sky_done else lambda: sky_work(wait_if_hidden=True))
        if sky_waits:
            sky_work()
        if probe:
            probes.PROBES[probe][0](session, manifest, out)
            return
        if args.calibrate is not None:
            found = calibration.run(session, manifest, specifics.calibrate, specifics.calibration_stages,
                                    [n for n in args.calibrate.split(",") if n], out.parent, specifics.calibration_probes)
            quit_match(wait=False)
            log_timings("calibration")
            done(f"the calibration is in {found}")
            return
        if parts & {"elements", "tiles"}:
            for hook in specifics.before_tiles:
                hook(session, manifest, out.parent)
        if "tiles" in parts:
            start = args.start
            if args.reuse and not start:  # a job's run carried on: from its first tile still to shoot (the grid as planned now)
                missing = next((n for n, t in enumerate(manifest["tiles"]) if not frame_exists(tile_stem(out, t["index"]))), len(manifest["tiles"]))
                start = max(0, missing - SAVERS) if missing < len(manifest["tiles"]) else missing
            capture_tiles(session, manifest, start, args.settle, out, positions, positions_path, args.manifest)

    with stage("Leaving the match"):
        quit_match(wait=False)  # the stitch runs while the game leaves; the next launch waits for the menu
    detail(f"camera positions recorded for {len(positions)} screenshots")
    log_timings("capture")
    if "tiles" in parts:
        done(f"{len(manifest['tiles'])} screenshots" + (f" ({len(manifest['tiles']) - args.start} of them in this launch)" if args.start else ""))
        detail(f"screenshots in {out}")
    else:
        done(f"captured: {', '.join(p for p in PARTS if p in parts)}")


def recover(e: Recoverable, argv: list[str]) -> int:
    """Start the run again in a fresh launch, from where it was lost: the match is left (or the
    game started again through Battle.net if it closed), the map launched anew and the tiles
    resumed from `resume_at` (screenshots and camera positions taken so far are kept). Three
    times at most per run (HRS_RECOVERIES counts them across the relaunches). Returns the fresh
    run's exit code."""
    done = int(os.environ.get("HRS_RECOVERIES", "0"))
    warn(f"lost the match: {e.why}")
    if done >= 3:
        sys.exit(f"\nStopped: {e.why}; already recovered 3 times in this run.")
    argv = [a for a in argv if a != "--no-launch"]
    if e.resume_at is not None:
        if "--start" in argv:
            i = argv.index("--start")
            del argv[i : i + 2]
        argv += ["--start", str(e.resume_at)]
    try:  # an error dialog still up would swallow the launch
        dismiss_failed_dialog()
    except Exception:
        pass
    warn(f"launching the map again ({done + 1} of 3 tries)" + (f", carrying on at tile {e.resume_at + 1}" if e.resume_at is not None else ""))
    env = dict(os.environ, HRS_RECOVERIES=str(done + 1))
    if ui.log_file():
        env["HRS_DIAG_LOG"] = str(ui.log_file())  # the fresh run carries on in this run's diagnostic log
    env[ui.DEPTH_ENV] = str(ui.depth())  # and draws its tree under this one's open folders
    # The same program again: the exe itself, or Python running the package.
    program = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "heroes_capture"]
    return subprocess.call([*program, "capture", *argv], env=env)
