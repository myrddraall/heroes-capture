"""Driving the game: input only while it is in front, steps redone when focus is lost or a menu
opens, starting Heroes (through Battle.net), and its window on a chosen monitor. (The match itself,
commands to the map's script and leaving: map/match.py.)"""

import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from heroes_capture.common.game_menus import game_menu_open
from heroes_capture.common.game_state import MAP_FAILED, game_state, menu_matches
from heroes_capture.common.game_window import (
    bring_game_to_front,
    foreground_is_game,
    foreground_program,
    game_region,
    game_running,
    game_window_handle,
    hold_key,
    park_cursor,
)
from heroes_capture.common.runlog import detail, log, paused, warn
from heroes_capture.common.screen import ScreenGrabber


class Recoverable(Exception):
    """The match was lost (the game closed, the map failed to load, the wrong map is running):
    the run starts again from `resume_at` in a fresh launch (see tiles.py's recover)."""

    def __init__(self, why: str, resume_at: int | None = None):
        super().__init__(why)
        self.why, self.resume_at = why, resume_at


class GamePaused(Exception):
    """One of the game's menus is open over the match (Esc, Options, the exit dialog): the step is
    redone from its start once it's closed (see step)."""


class FocusLost(Exception):
    """The game lost focus part way through a step; the step is redone from its start."""


# ------------------------------------------------------------------------------------------------
# Focus: nothing is typed or grabbed unless the game is in front
# ------------------------------------------------------------------------------------------------

_brought_to_front = False  # Heroes is put in front once, as the run starts; after that the run waits for it


def bring_to_front_at_start() -> None:
    """Put Heroes in front, once per run (as it starts, or as the run starts it): after that, a
    window put in front of it (alt-tabbing away to pause) is waited out, never pushed aside."""
    global _brought_to_front
    if not _brought_to_front and not foreground_is_game():
        bring_game_to_front()
    _brought_to_front = True


def require_focus() -> None:
    """Part way through a step: stop the step if the game is no longer in front."""
    if not foreground_is_game():
        raise FocusLost


def wait_for_game(redoing: str | None = None) -> None:
    """Block until the game window is in front. Nothing is typed anywhere else. `redoing`: the step
    done again once it is (for the line saying what the pause was)."""
    if foreground_is_game():
        return
    program = foreground_program()
    with paused(f"the game isn't in front ({program} is): click into the game to carry on",
                f"{program} was in front" + (f"; {redoing} done again" if redoing else "")):
        while not foreground_is_game():
            if not game_running():
                raise Recoverable("the game isn't running any more (it crashed?)")
            time.sleep(0.5)
    park_cursor()
    time.sleep(1.0)


def wait_for_menu_closed() -> None:
    """Block while one of the game's menus is open over the match, looking twice a second while the
    game is in front; two looks without it in a row and the run carries on. The game closing
    meanwhile (Leave in the exit dialog) is a lost match."""
    with paused("a game menu is open (Esc, Options or the exit dialog): close it to carry on", "a game menu was open"), \
            ScreenGrabber(game_region(), duplication=False) as screen:
        clear = 0
        while clear < 2:
            if not game_running():
                raise Recoverable("the game isn't running any more (it was closed from its menu?)")
            if foreground_is_game():
                clear = 0 if game_menu_open(screen.grab()) else clear + 1
            time.sleep(0.5)
    park_cursor()


def step(action, what: str):
    """Run one step (a tile, a probe shot, a start-up attempt) with the game in front the whole
    time. If it loses focus part way, or one of the game's menus opens over the map, the step is
    abandoned, and redone from its start once the game is back in front and the menu closed:
    half-done steps are never continued."""
    while True:
        wait_for_game()
        try:
            return action()
        except (FocusLost, GamePaused) as e:
            if isinstance(e, GamePaused):
                detail(f"a game menu opened during {what}; redoing it once it's closed")
                wait_for_menu_closed()
            else:
                detail(f"focus lost during {what} (in front: {foreground_program()}); redoing it once the game is back")
            wait_for_game(redoing=what)


def settle(seconds: float) -> None:
    """Wait with the game in front the whole time; raises FocusLost if it isn't (see step). A
    long wait (over 5 s: a probe's idle time) only needs the game in front at its end: nothing
    is typed or grabbed meanwhile, and a window flashing up would otherwise restart the step."""
    if seconds > 5:
        time.sleep(seconds)
        wait_for_game()
        return
    end = time.time() + seconds
    while True:
        require_focus()
        if time.time() >= end:
            return
        time.sleep(min(0.05, max(0.0, end - time.time())))


# ------------------------------------------------------------------------------------------------
# Starting Heroes and launching the map
# ------------------------------------------------------------------------------------------------


def find_battlenet(given: str | None, game: str) -> Path | None:
    """The Battle.net app: as given, or found in the usual folders, the Windows uninstall
    registry entry, or next to the game."""
    candidates = [Path(given)] if given else []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LocalAppData")):
        if base:
            candidates += [Path(base) / "Battle.net" / "Battle.net.exe", Path(base) / "Programs" / "Battle.net" / "Battle.net.exe"]
    try:
        import winreg

        for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Battle.net"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Battle.net"),
                          (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Battle.net")):
            try:
                with winreg.OpenKey(root, key) as k:
                    for name in ("InstallLocation", "DisplayIcon"):
                        try:
                            value = str(winreg.QueryValueEx(k, name)[0]).strip('"')
                        except OSError:
                            continue
                        p = Path(value)
                        candidates.append(p if p.suffix.lower() == ".exe" else p / "Battle.net.exe")
            except OSError:
                pass
    except ImportError:
        pass
    drive = Path(game).anchor
    candidates += [Path(drive) / "Battle.net" / "Battle.net.exe", Path(drive) / "Games" / "Battle.net" / "Battle.net.exe",
                   Path(drive) / "Program Files (x86)" / "Battle.net" / "Battle.net.exe"]
    for c in candidates:
        if c.exists():
            return c
    return None


def ensure_game_running(battlenet: str | None, game: str) -> None:
    """Start Heroes through the Battle.net app if it isn't running (after a crash): started
    directly, the game can't authenticate. Then wait for its window and its main menu."""
    if game_running():
        return
    launcher = find_battlenet(battlenet, game)
    if launcher is None:
        sys.exit(
            "Heroes isn't running, and the Battle.net app wasn't found. Start Heroes from Battle.net\n"
            "by hand, or tell heroes-capture where the app is: set HRS_BATTLENET=<path to Battle.net.exe>\n"
            "in update.cmd (or pass --battlenet)."
        )
    log(f"Heroes isn't running; starting it through Battle.net ({launcher}) ...")
    # Battle.net can let a launch command go by: told to launch right after the game was killed,
    # it still showed the game's page with Play ready, and started nothing. So the command is
    # sent again after 45 s, then as the battlenet:// link the app also launches from, and
    # after that the run waits (as a pause, up to 10 minutes) for Play to be pressed by hand.
    attempts = [
        ("the launch command", lambda: subprocess.Popen([str(launcher), "--exec=launch Hero"])),
        ("the launch command again", lambda: subprocess.Popen([str(launcher), "--exec=launch Hero"])),
        ("the battlenet://Hero link", lambda: subprocess.Popen(["cmd", "/c", "start", "", "battlenet://Hero"], shell=False)),
    ]
    started = time.time()
    for n, (how, start) in enumerate(attempts):
        if n:
            log(f"  Heroes hasn't started after {time.time() - started:.0f} s; trying {how}")
        start()
        wait_until = time.time() + 45
        while not game_running() and time.time() < wait_until:
            time.sleep(1.0)
        if game_running():
            break
    if not game_running():
        with paused("Battle.net didn't start Heroes: press Play on its Heroes of the Storm page to carry on", "waited for Play in Battle.net"):
            deadline = time.time() + 600
            while not game_running():
                if time.time() > deadline:
                    sys.exit("Heroes didn't start within 10 minutes (is the Battle.net app logged in, and does it show the game as installed?)")
                time.sleep(1.0)
    deadline = time.time() + 180
    while not bring_game_to_front():  # Heroes starting up: put in front as its window appears
        if time.time() > deadline:
            sys.exit("Heroes started, but its window didn't appear within 3 minutes")
        time.sleep(1.0)
    global _brought_to_front
    _brought_to_front = True
    # Logging in comes between the window and the menu; a map launched during it leaves the
    # game at the login screen afterwards. Arrived: the menu's fixed interface is on screen,
    # two looks in a row.
    last_note = 0.0
    seen = 0
    with ScreenGrabber(game_region(), duplication=False) as screen:
        while time.time() < deadline + 180:
            time.sleep(2.0)
            if not foreground_is_game():  # waited for, not taken back: the user may have stepped away from it on purpose
                if time.time() - last_note > 10:
                    log(f"  Heroes isn't in front ({foreground_program()} is); click into the game to carry on")
                    last_note = time.time()
                continue
            parts = menu_matches(screen.grab())
            seen = seen + 1 if parts >= 2 else 0
            if seen >= 2:
                log("Heroes is at the main menu")
                return
            if time.time() - last_note > 10:
                log(f"  waiting for the menu ({parts} of 3 fixed parts of it on screen)")
                last_note = time.time()
    warn("couldn't tell whether Heroes reached the menu; carrying on")


def dump_unrecognised(frame, name: str) -> None:
    """A frame the game's state couldn't be read from, saved at half size beside the run's
    diagnostic log (or in tmp\\) as <name>-<time>.png, with the menu parts' scores in the log."""
    if frame is None:
        warn(f"  nothing recognised on screen for a while (the game isn't in front, so no frame to keep)")
        return
    from PIL import Image

    from heroes_capture.common import ui

    folder = (ui.log_file() or Path("tmp") / "x").parent
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}-{time.strftime('%H%M%S')}.png"
    try:
        Image.fromarray(np.ascontiguousarray(frame)).reduce(2).save(path)
    except (OSError, ValueError) as e:
        warn(f"  couldn't keep the screen ({e})")
        return
    details: list[str] = []
    menu_matches(frame, details)
    warn(f"  nothing recognised on screen for a while: it is in {path}; the menu's parts: " + "; ".join(details))


def game_display_settings() -> list[str]:
    """The display lines of the game's own settings (Documents\\Heroes of the Storm\\Variables.txt):
    what it thinks its window's mode, size, place and monitor are. Empty when the file isn't
    found (the Documents folder is asked of Windows: it may be redirected, to OneDrive say)."""
    import ctypes

    candidates = [Path.home() / "Documents", Path.home() / "OneDrive" / "Documents"]
    try:
        buffer = ctypes.c_wchar_p()
        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}, as the GUID's bytes in memory
        folder_id = ctypes.c_char_p(bytes.fromhex("D09AD3FD8F23AF46ADB46C85480369C7"))
        if ctypes.windll.shell32.SHGetKnownFolderPath(folder_id, 0, None, ctypes.byref(buffer)) == 0 and buffer.value:
            candidates.insert(0, Path(buffer.value))
    except (AttributeError, OSError, TypeError):
        pass
    for documents in candidates:
        settings = documents / "Heroes of the Storm" / "Variables.txt"
        if settings.exists():
            keys = ("display", "window", "width", "height", "monitor", "fullscreen", "resolution")
            lines = [line.strip() for line in settings.read_text(errors="replace").splitlines() if any(k in line.lower() for k in keys)]
            return [f"({settings})", *lines]
    log("the game's settings file wasn't found (looked for Heroes of the Storm\\Variables.txt in " + ", ".join(str(c) for c in candidates) + ")")
    return []


def window_rect() -> tuple[int, int, int, int]:
    r = game_region()
    return (r["left"], r["top"], r["width"], r["height"])


def place_game_on_monitor(spec: str) -> None:
    """The game's window put on the monitor `spec` names (monitors.choose) and sized to it, at the
    main menu before the map is launched, so the map loads at that monitor's resolution. The game
    may move its window back by itself (it has a monitor of its own in its settings, and puts the
    window there again when it redraws at a new size), so the window is watched for a few seconds
    and placed again, up to three times; where it is after that is logged. The capture follows
    the window wherever and whatever size it is, so a window that didn't take the monitor's size
    is only warned about. The game's own display settings are logged first, for the diagnosis."""
    from heroes_capture.common.monitors import choose, monitors, place_window

    found = monitors()
    log("monitors: " + "; ".join(m.describe() for m in found))
    settings = game_display_settings()
    if settings:
        log("the game's display settings: " + "; ".join(settings))
    monitor = choose(spec, found)
    # Where the window should be: the monitor, or on one narrower than the game draws (5:4), the
    # letterboxed picture the game makes its window.
    wanted = monitor.game_rect()
    if wanted[3] != monitor.height:
        log(f"monitor {monitor.number} is narrower than the game draws (5:4): it will draw {monitor.game_size}, centred, with black above and below")
    deadline = time.time() + 60
    while (hwnd := game_window_handle()) is None:
        if time.time() > deadline:
            sys.exit(f"the game's window didn't appear within a minute; it can't be put on monitor {spec}")
        time.sleep(1.0)
    before = window_rect()
    log(f"the game's window is {before[2]}x{before[3]} at ({before[0]}, {before[1]}); putting it on monitor {monitor.describe()}")
    _place_and_watch(hwnd, monitor, wanted)


def keep_game_on_monitor(spec: str, when: str) -> None:
    """The window placed again if the game moved it since (it puts the window back where its
    settings remember, windowx/windowy in Variables.txt, as the map loads: a window left at 48
    px from the top of a 1440 monitor had the taskbar over its bottom). `when` names the moment,
    for the log."""
    from heroes_capture.common.monitors import choose, monitors

    monitor = choose(spec, monitors())
    wanted = monitor.game_rect()
    rect = window_rect()
    if rect == wanted:
        return
    hwnd = game_window_handle()
    if hwnd is None:
        warn(f"the game's window is {rect[2]}x{rect[3]} at ({rect[0]}, {rect[1]}) {when}, and can't be found to place it again")
        return
    log(f"the game moved its window to {rect[2]}x{rect[3]} at ({rect[0]}, {rect[1]}) {when}; putting it back on monitor {monitor.number}")
    _place_and_watch(hwnd, monitor, wanted)


def _place_and_watch(hwnd, monitor, wanted: tuple[int, int, int, int]) -> None:
    """The window put on the monitor, watched for 6 s and placed again when the game moves it
    back, up to three times; where it is after that is logged."""
    from heroes_capture.common.monitors import place_window

    for attempt in range(1, 4):
        place_window(hwnd, monitor)
        time.sleep(1.5)  # the game redraws at the new size
        bring_game_to_front()
        # Watched for 6 s: the game may put the window back where its settings say.
        moved_back = None
        started = time.time()
        while time.time() - started < 6.0:
            rect = window_rect()
            if rect != wanted:
                moved_back = (round(time.time() - started, 1), rect)
                break
            time.sleep(0.5)
        if moved_back is None:
            log(f"the game is on monitor {monitor.describe()}" + (f" (placed {attempt} times)" if attempt > 1 else ""))
            return
        seconds, (x, y, w, h) = moved_back
        warn(f"placing {attempt}: the game moved its window to {w}x{h} at ({x}, {y}) after {seconds} s"
             + ("; placing it again" if attempt < 3 else ""))
    x, y, w, h = window_rect()
    warn(f"the game's window is {w}x{h} at ({x}, {y}) after being put on monitor {monitor.describe()} three times; the capture follows the window as it is")


def dismiss_failed_dialog() -> None:
    """Press the error dialog's OK if it is up: it would swallow the next launch."""
    with ScreenGrabber(game_region(), duplication=False) as screen:
        if foreground_is_game() and game_state(screen.grab()) == MAP_FAILED:
            hold_key("enter")
            time.sleep(1.0)


# ------------------------------------------------------------------------------------------------
# Leaving the match
# ------------------------------------------------------------------------------------------------


