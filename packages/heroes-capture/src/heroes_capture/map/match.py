"""The match: commands typed into the map's command box (and the chat's "focus"), the prepared map
launched and loaded, and the match left (back at the main menu)."""

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from heroes_capture.common.game_control import (
    Recoverable,
    bring_to_front_at_start,
    dump_unrecognised,
    ensure_game_running,
    place_game_on_monitor,
    require_focus,
    step,
    wait_for_game,
)
from heroes_capture.common.game_state import IN_MAP, LOADING, MAP_FAILED, MENU, NOT_RUNNING, game_state
from heroes_capture.common.game_window import foreground_is_game, game_region, game_running, hold_key, type_unicode
from heroes_capture.common.runlog import log, warn
from heroes_capture.common.screen import ScreenGrabber
from heroes_capture.map.status import StatusStrip

CHAT_OPEN_WAIT = 0.06  # seconds for the chat box to open before the text is typed (0.15 until the waits probe: 30 of 30 commands taken even at 0.03)


def send_command(text: str) -> None:
    """A command typed into the map's command box (capture_script.galaxy), only while the game is
    in front: Unicode text ending in ";", in one burst. Text input never acts as a hotkey, wherever
    it lands. Raises FocusLost if the game isn't in front (the step is then redone; see step)."""
    require_focus()
    type_unicode(f"{text};")


def send_chat(text: str) -> None:
    """A chat message (only "focus ... ;": the keyboard back to the command box): a held Enter, a
    moment for the chat box to open, the text as Unicode, a held Enter. With the command box
    holding the keyboard after all, the Enters do nothing and the text lands in the box, a command
    there too."""
    require_focus()
    hold_key("enter")
    time.sleep(CHAT_OPEN_WAIT)  # the chat box needs a moment (a few frames) to open
    require_focus()
    type_unicode(text)
    time.sleep(0.03)
    hold_key("enter")


def launch_map(manifest: dict, game: str, battlenet: str | None, monitor: str | None = None) -> None:
    """Hand the prepared map to the running game (Heroes must be at the main menu: a running
    match keeps its map), starting Heroes first if it isn't running; with `monitor`, the game's
    window put on that monitor first (place_game_on_monitor)."""
    switcher = Path(game) / "Support64" / "HeroesSwitcher_x64.exe"
    if not switcher.exists():
        sys.exit(f"not found: {switcher} (pass --game)")
    if game_running():
        # Back at the menu first: an earlier run may have left its match without waiting.
        wait_for_menu()
    ensure_game_running(battlenet, game)
    if monitor:
        place_game_on_monitor(monitor)
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
        step(lambda: send_command("quit 0"), "sending quit")
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
    bring_to_front_at_start()
    strip = StatusStrip()
    with ScreenGrabber(game_region(), duplication=False) as screen:
        started = time.time()
        state, phases, menu_looks, resent, dumped = None, [], 0, False, False
        while time.time() - started < 120:
            # The window can move or change size as the match ends (the game redraws, and may put
            # the window where its settings say): the reading follows it, or it would look at the
            # wrong part of the desktop and never see the menu.
            if foreground_is_game() and (region := game_region()) != screen.region:
                log(f"  the game's window is now {region['width']}x{region['height']} at ({region['left']}, {region['top']}); following it")
                screen.region = region
            seen = game_state(screen.grab() if foreground_is_game() else None, strip)
            if seen == IN_MAP and not resent and time.time() - started > 3:
                resent = True
                step(lambda: (send_chat("focus 0 ;"), time.sleep(0.3), send_command("quit 0")), "sending quit again" if quit_sent else "leaving a match still running")
            if seen != state:
                state = seen
                phases.append(f"{state} at {time.time() - started:.1f} s")
            if state == LOADING and time.time() - started > 45 and not dumped:
                # The match ended long ago and nothing is recognised: the screen as it is, and how
                # far each part of the menu was from being found, for the diagnosis.
                dumped = True
                dump_unrecognised(screen.grab() if foreground_is_game() else None, "menu-unseen")
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
            if menu_looks >= 2:
                log(f"left the match after {time.time() - started:.1f} s (back at the menu)")
            elif state != NOT_RUNNING:
                warn(f"gave up waiting for the menu after {time.time() - started:.0f} s: the match may still be running")
