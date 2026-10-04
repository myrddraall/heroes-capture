"""Driving the game: keys only while it is in front, chat commands, starting Heroes, launching the
map, waiting for it to load, and leaving the match.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .game_state import IN_MAP, LOADING, MAP_FAILED, MENU, NOT_RUNNING, game_state, menu_matches
from .game_window import (
    bring_game_to_front,
    foreground_is_game,
    foreground_program,
    game_region,
    game_running,
    hold_key,
    park_cursor,
    type_burst,
)
from .runlog import log, warn
from .screen import ScreenGrabber
from .status import StatusStrip


class Recoverable(Exception):
    """The match was lost (the game closed, the map failed to load, the wrong map is running):
    the run starts again from `resume_at` in a fresh launch (see capture.py's recover)."""

    def __init__(self, why: str, resume_at: int | None = None):
        super().__init__(why)
        self.why, self.resume_at = why, resume_at


class FocusLost(Exception):
    """The game lost focus part way through a step; the step is redone from its start."""


# ------------------------------------------------------------------------------------------------
# Focus: nothing is typed or grabbed unless the game is in front
# ------------------------------------------------------------------------------------------------

_chat_open = False  # the chat box was opened and not yet sent (a step was interrupted mid-command)
CHAT_OPEN_WAIT = 0.06  # seconds for the chat box to open before the text is typed (0.15 until the waits probe: 30 of 30 commands taken even at 0.03)


def require_focus() -> None:
    """Part way through a step: stop the step if the game is no longer in front."""
    if not foreground_is_game():
        raise FocusLost


def wait_for_game() -> None:
    """Block until the game window is in front. Nothing is typed anywhere else."""
    if foreground_is_game():
        return
    warn("the game window isn't in front; waiting (click into the game to continue)")
    while not foreground_is_game():
        if not game_running():
            raise Recoverable("the game isn't running any more (it crashed?)")
        time.sleep(0.5)
    park_cursor()
    time.sleep(1.0)


def step(action, what: str):
    """Run one step (a tile, a probe shot, a start-up attempt) with the game in front the whole
    time. If it loses focus part way, the step is abandoned, and redone from its start once the
    game is back in front: half-done steps are never continued."""
    global _chat_open
    while True:
        wait_for_game()
        try:
            return action()
        except FocusLost:
            warn(f"focus lost during {what} (in front: {foreground_program()}); redoing it from the start")
            wait_for_game()
            if _chat_open:
                # A half-typed line may be in the chat box: empty it and close it (Enter on an
                # empty line sends nothing). Never Esc: with the box already closed, that opens
                # the game menu, which then swallows every later command.
                type_burst(["backspace"] * 30)
                time.sleep(0.05)
                hold_key("enter")
                _chat_open = False
                time.sleep(0.3)


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


def send_chat(text: str) -> None:
    """Open chat, type, send, only while the game is in front: a held Enter, a moment for the
    chat box to open, the text in one burst, and a held Enter to send it. Raises FocusLost if
    the game isn't in front (the step it belongs to is then redone; see step)."""
    global _chat_open
    require_focus()
    hold_key("enter")
    _chat_open = True
    time.sleep(CHAT_OPEN_WAIT)  # the chat box needs a moment (a few frames) to open
    require_focus()
    type_burst(["space" if ch == " " else ch for ch in text])
    time.sleep(0.03)
    hold_key("enter")
    _chat_open = False


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
            "by hand, or tell capture.py where the app is: set HRS_BATTLENET=<path to Battle.net.exe>\n"
            "in update.cmd (or pass --battlenet)."
        )
    log(f"Heroes isn't running; starting it through Battle.net ({launcher}) ...")
    subprocess.Popen([str(launcher), "--exec=launch Hero"])
    deadline = time.time() + 180
    while not game_running():
        if time.time() > deadline:
            sys.exit("Heroes didn't start within 3 minutes")
        time.sleep(1.0)
    while not bring_game_to_front():
        if time.time() > deadline:
            sys.exit("Heroes started, but its window didn't appear within 3 minutes")
        time.sleep(1.0)
    # Logging in comes between the window and the menu; a map launched during it leaves the
    # game at the login screen afterwards. Arrived: the menu's fixed interface is on screen,
    # two looks in a row.
    last_note = 0.0
    seen = 0
    with ScreenGrabber(game_region(), duplication=False) as screen:
        while time.time() < deadline + 180:
            time.sleep(2.0)
            if not foreground_is_game():
                if not bring_game_to_front() and time.time() - last_note > 10:
                    log(f"  can't bring Heroes in front ({foreground_program()} is in front); click into the game")
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


def launch_map(manifest: dict, game: str, battlenet: str | None) -> None:
    """Hand the prepared map to the running game (Heroes must be at the main menu: a running
    match keeps its map), starting Heroes first if it isn't running."""
    switcher = Path(game) / "Support64" / "HeroesSwitcher_x64.exe"
    if not switcher.exists():
        sys.exit(f"not found: {switcher} (pass --game)")
    if game_running():
        # Back at the menu first: an earlier run may have left its match without waiting.
        wait_for_menu()
    ensure_game_running(battlenet, game)
    # The game wants an absolute path, and may not read one on a network share (such as
    # \\wsl.localhost\...), so launch a local copy. Each launch gets a new name: with a
    # reused one the game ran an earlier preparation's map, whose grid didn't match.
    local = Path(tempfile.gettempdir()) / "hrs-map-capture"
    local.mkdir(exist_ok=True)
    for old in local.glob("*.stormmap"):
        try:
            old.unlink()
        except OSError:
            pass  # still open in a running game
    stormmap = str(shutil.copy(manifest["stormmap"], local / f"{manifest['id']}-{int(time.time())}.stormmap"))
    log(f"launching {manifest['map']} ({len(manifest['tiles'])} tiles) ...")
    subprocess.Popen([str(switcher), stormmap])


def dismiss_failed_dialog() -> None:
    """Press the error dialog's OK if it is up: it would swallow the next launch."""
    with ScreenGrabber(game_region(), duplication=False) as screen:
        if foreground_is_game() and game_state(screen.grab()) == MAP_FAILED:
            hold_key("enter")
            time.sleep(1.0)


def wait_for_map_load(launched: bool) -> None:
    """After a launch, wait until the game has left the main menu (typing at the menu could send
    chat to a channel). Slow loading and the intro cutscene are waited out after this, by
    watching for the map's status strip. Raises Recoverable when the map fails to load, the
    game closes, or the menu stays for a minute (the launch was ignored)."""
    wait_for_game()
    if not launched:
        return
    log("waiting for the map to load ...")
    started, failed_since = time.time(), None
    with ScreenGrabber(game_region(), duplication=False) as screen:
        while time.time() - started < 60:
            state = game_state(screen.grab() if foreground_is_game() else None)
            if state == NOT_RUNNING:
                raise Recoverable("the game closed while the map was loading")
            if state == MAP_FAILED:
                failed_since = failed_since or time.time()
                if time.time() - failed_since > 2:
                    hold_key("enter")  # the dialog's OK
                    raise Recoverable("the game couldn't open the map")
            else:
                failed_since = None
            if state in (LOADING, IN_MAP):
                return
            time.sleep(0.25)
    raise Recoverable("the game stayed at the menu for a minute after the launch")


# ------------------------------------------------------------------------------------------------
# Leaving the match
# ------------------------------------------------------------------------------------------------


def quit_match(wait: bool = True) -> None:
    """Leave the match (the map script's "quit" command). With `wait`, until the game is back at
    the main menu: a running match keeps its map, and a map launched before the menu is back is
    ignored. Without, once the strip confirms the match is ending: a render's stitch then runs
    while the game leaves, and the next launch waits for the menu (launch_map)."""
    if not foreground_is_game():
        return
    for grabber in list(ScreenGrabber.open_grabbers):
        grabber.release_duplication()  # nothing holds the game's screen while it leaves
    try:
        step(lambda: send_chat("quit"), "sending quit")
        log("leaving the match ...")
        wait_for_menu(quit_sent=True, until_leaving=not wait)
    except Exception as e:  # a failed check must not cost the run its stitch
        warn(f"couldn't watch for the menu ({type(e).__name__}: {e}); waiting 45 s instead")
        time.sleep(45)


def wait_for_menu(quit_sent: bool = False, until_leaving: bool = False) -> None:
    """Watch the game's state four times a second until the main menu is back (two looks in a
    row); each change of state is logged with its time, so it is clear where the leaving takes
    its time. Our map still running after 3 s (a quit it didn't take, or a match an earlier run
    left) is sent quit (again). With `until_leaving`, only until the strip says the match is
    ending."""
    # A live reading of the screen (mss): the end of the match is a display transition, after
    # which desktop duplication can keep handing back the last frame of the match.
    if not foreground_is_game():
        bring_game_to_front()
    strip = StatusStrip()
    with ScreenGrabber(game_region(), duplication=False) as screen:
        started = time.time()
        state, phases, menu_looks, resent = None, [], 0, False
        while time.time() - started < 120:
            seen = game_state(screen.grab() if foreground_is_game() else None, strip)
            if seen == IN_MAP and not resent and time.time() - started > 3:
                resent = True
                step(lambda: send_chat("quit"), "sending quit again" if quit_sent else "leaving a match still running")
            if seen != state:
                state = seen
                phases.append(f"{state} at {time.time() - started:.1f} s")
            if until_leaving and state not in (IN_MAP, None):
                log(f"  the match is ending ({state}); the next launch waits for the menu")
                return
            if state == NOT_RUNNING:
                log("  the game closed while leaving the match (did it crash?)")
                break
            if state == MAP_FAILED:
                hold_key("enter")
            menu_looks = menu_looks + 1 if state == MENU else 0
            if menu_looks >= 2:
                break
            time.sleep(0.25)
        else:
            log("  the game hadn't come back to the menu after 2 minutes; carrying on")
        if phases[1:] or quit_sent:  # more than "menu" from the start: there was a match to leave
            log("  leaving: " + ", ".join(phases))
            log(f"left the match after {time.time() - started:.1f} s (back at the menu)")
