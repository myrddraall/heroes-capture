"""Launch a prepared battleground and screenshot every tile of its capture grid.

    python capture.py work/towers-of-doom-structures.json [options]

Moves the camera by typing `tile <n>` into the game's chat (the injected script listens for
it), waits until the screen shows that tile, and saves the screen as a lossless PNG. Chat is used
rather than a hotkey because it names the tile: a missed keystroke cannot shift every later
screenshot by one.

Captures the game window itself, on whichever monitor it is. Run the game in
"Windowed (Fullscreen)" at the resolution given to inject.mjs, and leave the mouse and keyboard
alone while it captures.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import shutil
import subprocess
import tempfile
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mss
import numpy as np
import pydirectinput
from PIL import Image

from status import Status, StatusStrip

DEFAULT_GAME = r"D:\Games\Heroes of the Storm"

pydirectinput.PAUSE = 0.02  # between synthetic key events


# Keyboard input for typing a whole command in one SendInput call (see type_burst).
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


def type_burst(keys: list[str]) -> None:
    """Press and release each key, all in one SendInput call: the game gets them in order with
    nothing else in between, as fast as it reads them. Scan codes, which the game accepts.
    Only for characters typed into the open chat box: the game can miss a hotkey (such as the
    Enter that opens and sends chat) pressed and released in the same instant; see hold_key."""
    events = []
    for key in keys:
        code = pydirectinput.KEYBOARD_MAPPING[key]
        for up in (0, 0x0002):  # KEYEVENTF_KEYUP
            events.append(INPUT(type=1, ki=KEYBDINPUT(0, code, 0x0008 | up, 0, 0)))  # KEYEVENTF_SCANCODE
    batch = (INPUT * len(events))(*events)
    ctypes.windll.user32.SendInput(len(events), batch, ctypes.sizeof(INPUT))

# Real pixel coordinates on every monitor, whatever the Windows display scaling.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    ctypes.windll.user32.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def foreground_is_game() -> bool:
    """Whether the window in front belongs to the game's own program, HeroesOfTheStorm*.exe.

    Checked by process rather than window title: a title check also passed for other windows
    that happened to mention the game, and the keys went there.
    """
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return False
        return Path(buf.value).name.lower().startswith("heroesofthestorm")
    finally:
        kernel32.CloseHandle(handle)


def game_running() -> bool:
    """Whether any HeroesOfTheStorm*.exe is running (after a crash, none is). One look can miss
    it (it did once, with the game plainly running), so three misses a second apart are
    needed before it counts as gone."""
    for attempt in range(3):
        if _game_process_seen():
            return True
        if attempt < 2:
            time.sleep(1.0)
    return False


def _game_process_seen() -> bool:
    kernel32 = ctypes.windll.kernel32
    pids = (ctypes.c_ulong * 4096)()
    used = ctypes.c_ulong()
    if not kernel32.K32EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(used)):
        return True  # can't tell: assume it is
    buf = ctypes.create_unicode_buffer(1024)
    for pid in pids[: used.value // ctypes.sizeof(ctypes.c_ulong)]:
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            size = ctypes.c_ulong(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                if Path(buf.value).name.lower().startswith("heroesofthestorm"):
                    return True
        finally:
            kernel32.CloseHandle(handle)
    return False


def bring_game_to_front() -> bool:
    """Find the game's window and put it in front (so a menu shot can be taken and keys go to
    it). Windows only lets a process that has just sent input do that, hence the Alt tap."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _window_process_is_game(hwnd):
            rect = RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            if rect.right > 400 and rect.bottom > 300:  # the game window, not a helper window
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    if not found:
        return False
    hold_key("alt", 0.02)
    user32.ShowWindow(found[0], 9)  # SW_RESTORE
    user32.SwitchToThisWindow(found[0], True)  # what Alt+Tab does; SetForegroundWindow alone is often refused
    user32.SetForegroundWindow(found[0])
    time.sleep(0.5)
    park_cursor()
    return foreground_is_game()


def foreground_program() -> str:
    """The program of the window in front, for messages."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        return Path(buf.value).name if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else "?"
    finally:
        kernel32.CloseHandle(handle)


def _window_process_is_game(hwnd) -> bool:
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return False
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return False
        return Path(buf.value).name.lower().startswith("heroesofthestorm")
    finally:
        kernel32.CloseHandle(handle)


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
    # The window, then the menu: the menu's fixed interface (a saved menu shot) if there is
    # one, otherwise a screen that has stopped changing much for 10 s.
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
            frame = screen.grab()
            parts = menu_matches(frame)
            seen = seen + 1 if parts >= 2 else 0
            if seen >= 2:
                log("Heroes is at the main menu")
                return
            if time.time() - last_note > 10:
                log(f"  waiting for the menu ({parts} of 3 fixed parts of it on screen)")
                last_note = time.time()
    log("  couldn't tell whether Heroes reached the menu; carrying on")


def game_region() -> dict:
    """The game window's drawable area in screen pixels (the foreground window: the game)."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    rect, origin = RECT(), POINT(0, 0)
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return {"left": origin.x, "top": origin.y, "width": rect.right, "height": rect.bottom}


def looks_black(frame: np.ndarray) -> bool:
    """A failed grab, or a frame caught mid-transition: black or a single flat colour."""
    sample = frame[::8, ::8]
    return int(sample.max()) < 12 or float(sample.std()) < 3.0


def same_view(a: np.ndarray, b: np.ndarray) -> bool:
    """The camera didn't move: the chat command was probably dropped."""
    return float(np.abs(a[::8, ::8].astype(np.int16) - b[::8, ::8].astype(np.int16)).mean()) < 0.5


class ScreenGrabber:
    """Frames of a screen region as RGB arrays.

    Through DXGI desktop duplication (the dxcam package) where it works: it hands over only
    frames the compositor has finished. mss copies the screen with GDI, which can catch the
    game part way through presenting a frame (a band of garbled strips, then black, or all
    black) though the screen never shows one; those are retaken (see whole_frame), but a few
    still got through as all-black tiles. Desktop duplication is the default for tiles
    (HRS_CAPTURE=mss switches); the crash when leaving a match happens with either. After
    losing access (a display transition) dxcam quietly returns its last frame, so the checks
    that watch the screen change use mss (`duplication=False`)."""

    open_grabbers: list["ScreenGrabber"] = []

    def __init__(self, region: dict, duplication: bool = False):
        ScreenGrabber.open_grabbers.append(self)
        self.region = region
        self.camera = None
        self.box = None
        self.sct = None
        if not duplication:
            self.sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
            return
        try:
            import dxcam

            for index in range(8):
                try:
                    # numpy conversion: the default one needs OpenCV.
                    camera = dxcam.create(output_idx=index, output_color="RGB", processor_backend="numpy")
                except Exception:
                    break
                if camera is None:
                    break
                r = camera._output.desc.DesktopCoordinates  # where this monitor is on the desktop
                inside = (
                    r.left <= region["left"] and r.top <= region["top"]
                    and region["left"] + region["width"] <= r.right and region["top"] + region["height"] <= r.bottom
                )
                if inside:
                    x, y = region["left"] - r.left, region["top"] - r.top
                    self.camera, self.box = camera, (x, y, x + region["width"], y + region["height"])
                    break
        except Exception as e:  # not installed, or no DXGI output: fall back
            print(f"(desktop duplication unavailable: {e}; using mss)", flush=True)
        if self.camera is None:
            self.sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()  # mss 10 renamed it

    @property
    def method(self) -> str:
        return "desktop duplication" if self.camera is not None else "mss"

    def grab(self) -> np.ndarray:
        if self.camera is not None:
            # A copy of its own (it may be saved in the background while the next is taken), and
            # the last frame again when nothing has changed since; None only before the first.
            for _ in range(25):
                frame = self.camera.grab(region=self.box, copy=True, new_frame_only=False)
                if frame is not None:
                    return frame
                time.sleep(0.02)
            return np.zeros((self.region["height"], self.region["width"], 3), np.uint8)
        return np.asarray(self.sct.grab(self.region), dtype=np.uint8)[:, :, 2::-1]  # BGRA -> RGB

    def __enter__(self) -> "ScreenGrabber":
        return self

    def __exit__(self, *exc) -> None:
        if self in ScreenGrabber.open_grabbers:
            ScreenGrabber.open_grabbers.remove(self)
        if self.sct is not None:
            self.sct.close()
        self.release_duplication()

    def release_duplication(self) -> None:
        if self.camera is not None:
            # Let go of desktop duplication: nothing holds on to the game's screen while it
            # leaves the match.
            try:
                self.camera.release()
            except Exception:
                pass
            self.camera = None


def changed_share(a: np.ndarray, b: np.ndarray) -> float:
    """The share of the screen that differs between two shots: 32-pixel blocks whose average
    brightness differs by more than 25 levels."""

    def blocks(f: np.ndarray) -> np.ndarray:
        h, w = f.shape[0] // 32 * 32, f.shape[1] // 32 * 32
        g = f[:h, :w].astype(np.float32).mean(axis=2)
        return g.reshape(h // 32, 32, w // 32, 32).mean(axis=(1, 3))

    return float((np.abs(blocks(a) - blocks(b)) > 25).mean())


def view_shift(a: np.ndarray, b: np.ndarray) -> float:
    """How far the view moved between two shots, in screen pixels (phase correlation at a
    quarter scale). Animation (an animated sky, flickering lights, labels) changes pixels but
    doesn't move the view; a camera move does."""

    def prepare(f: np.ndarray) -> np.ndarray:
        g = f[::4, ::4].astype(np.float32).mean(axis=2)
        g -= g.mean()
        return g * np.hanning(g.shape[0])[:, None] * np.hanning(g.shape[1])[None, :]

    pa, pb = prepare(a), prepare(b)
    cross = np.fft.rfft2(pa) * np.conj(np.fft.rfft2(pb))
    cross /= np.abs(cross) + 1e-9
    corr = np.fft.irfft2(cross, s=pa.shape)
    y, x = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = corr.shape
    dy, dx = (y - h if y > h // 2 else y), (x - w if x > w // 2 else x)
    return float(np.hypot(dx, dy)) * 4


def disagree(a: np.ndarray, b: np.ndarray) -> bool:
    """Two shots of the same view disagree over more than a sliver of the screen: one was caught
    while the game was part way through drawing it (a band of garbled strips, then black), or
    shows a chat box. Labels and animation change well under 1% of the screen (measured); a
    half-drawn frame about a quarter of it."""
    return changed_share(a, b) > 0.02


_MENU_REFERENCE = None  # the main menu's fixed interface: templates shipped in menu-reference/


def _load_menu_reference() -> list:
    """Templates of the main menu's fixed parts (the top bar, the chat box, the buttons at the
    bottom right), from a screenshot of the menu, with their positions relative to the screen
    height and anchored to the corners, as the interface scales. Login, loading, in-game and
    post-match screens have none of them."""
    global _MENU_REFERENCE
    if _MENU_REFERENCE is None:
        folder = Path(__file__).with_name("menu-reference")
        _MENU_REFERENCE = []
        try:
            for p in json.loads((folder / "patches.json").read_text())["patches"]:
                p["template"] = np.asarray(Image.open(folder / f"{p['name']}.png").convert("L"), dtype=np.float32)
                _MENU_REFERENCE.append(p)
        except (OSError, KeyError, ValueError) as e:
            print(f"(no menu reference: {e})", flush=True)
    return _MENU_REFERENCE


def menu_matches(frame: np.ndarray) -> int:
    """How many of the menu's fixed parts are on screen where they belong (0 to 3)."""
    grey = frame.astype(np.float32).mean(axis=2)
    h, w = grey.shape
    found = 0
    for p in _load_menu_reference():
        pw, ph = int(round(p["w"] * h)), int(round(p["h"] * h))
        x = int(round(p["dx"] * h)) if p["anchor"].endswith("left") else w - int(round(p["dx"] * h)) - pw
        y = int(round(p["dy"] * h)) if p["anchor"].startswith("top") else h - int(round(p["dy"] * h)) - ph
        if x < 0 or y < 0 or x + pw > w or y + ph > h:
            continue
        region = np.asarray(Image.fromarray(grey[y : y + ph, x : x + pw].astype(np.uint8)).resize(p["template"].shape[::-1], Image.BILINEAR), dtype=np.float32)
        t = p["template"]
        a, b = region - region.mean(), t - t.mean()
        denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
        if denom and float((a * b).sum() / denom) > 0.6:
            found += 1
    return found


def is_menu(frame: np.ndarray) -> bool:
    return menu_matches(frame) >= 2


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


def wait_for_map_load(launched: bool) -> None:
    """After a launch, wait until the game has left the main menu (typing at the menu could send
    chat to a channel): its fixed interface is gone. Slow loading and the intro cutscene are
    waited out after this, by asking the map script until it answers."""
    wait_for_game()
    if not launched:
        return
    print("waiting for the map to load ...", flush=True)
    started = time.time()
    try:
        with ScreenGrabber(game_region(), duplication=False) as screen:
            while time.time() - started < 60:
                if foreground_is_game() and not is_menu(screen.grab()):
                    return
                time.sleep(0.5)
    except Exception as e:  # the check must never cost the run
        log(f"  couldn't watch for the map to load ({type(e).__name__}: {e}); waiting 5 s instead")
        time.sleep(5)
class FocusLost(Exception):
    """The game lost focus part way through a step; the step is redone from its start."""


_chat_open = False  # the chat box was opened and not yet sent (a step was interrupted mid-command)


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
            print(f"  focus lost during {what} (in front: {foreground_program()}); redoing it from the start", flush=True)
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


def park_cursor() -> None:
    """The mouse to the middle of the game window: at a screen edge it scrolls the camera (the
    map script also locks camera input, but not before it runs)."""
    try:
        region = game_region()
        ctypes.windll.user32.SetCursorPos(region["left"] + region["width"] // 2, region["top"] + region["height"] // 2)
    except Exception:
        pass  # no window yet; nothing to park over


def require_focus() -> None:
    """Part way through a step: stop the step if the game is no longer in front."""
    if not foreground_is_game():
        raise FocusLost


def wait_for_game() -> None:
    """Block until the game window is in front. Nothing is typed anywhere else."""
    if foreground_is_game():
        return
    print("  game window not in front; waiting (click into the game to continue)...", flush=True)
    while not foreground_is_game():
        if not game_running():
            sys.exit("\nStopped: the game isn't running any more (did it crash?). Start it again and rerun.")
        time.sleep(0.5)
    park_cursor()
    time.sleep(1.0)


def hold_key(key: str, seconds: float = 0.04) -> None:
    """Press a key and hold it for a few frames, so the game can't miss it."""
    code = pydirectinput.KEYBOARD_MAPPING[key]
    for up in (0, 0x0002):
        event = INPUT(type=1, ki=KEYBDINPUT(0, code, 0x0008 | up, 0, 0))
        ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        if not up:
            time.sleep(seconds)


def send_chat(text: str) -> None:
    """Open chat, type, send, only while the game is in front: a held Enter, a moment for the
    chat box to open, the text in one burst, and a held Enter to send it. Raises FocusLost if
    the game isn't in front (the step it belongs to is then redone; see step)."""
    global _chat_open
    require_focus()
    hold_key("enter")
    _chat_open = True
    time.sleep(0.15)  # the chat box needs a moment (a few frames) to open
    require_focus()
    type_burst(["space" if ch == " " else ch for ch in text])
    time.sleep(0.03)
    hold_key("enter")
    _chat_open = False


def quit_match() -> None:
    """Leave the match (the capture script's "quit" command) and wait until the game is back at
    the main menu: a running match keeps its map, and a map launched before the menu is back
    is ignored (the next run in run.cmd starts straight after this one). Leaving takes a
    while (credits scroll over the map, the camera flies to a core, it explodes, a loading
    screen), so instead of a fixed wait: back at the menu is when the screen looks like the
    menu shot taken before the launch again (two looks in a row); without such a shot, when it
    has changed little for 20 s."""
    if not foreground_is_game():
        return
    for grabber in list(ScreenGrabber.open_grabbers):
        grabber.release_duplication()  # nothing holds the game's screen while it leaves
    try:
        _leave_and_wait_for_menu()
    except Exception as e:  # a failed check must not cost the run its stitch
        log(f"  couldn't watch for the menu ({type(e).__name__}: {e}); waiting 45 s instead")
        time.sleep(45)


def _leave_and_wait_for_menu() -> None:
    # A live reading of the screen (mss): the end of the match is a display transition, after
    # which desktop duplication can keep handing back the last frame of the match.
    with ScreenGrabber(game_region(), duplication=False) as screen:

        def shot() -> np.ndarray:
            return screen.grab()

        def leave() -> np.ndarray:
            before = shot()
            send_chat("quit")
            return before

        in_match = step(leave, "sending quit")
        print("leaving the match ...", flush=True)
        started = time.time()
        previous, calm_since, trace = None, None, []
        while time.time() - started < 120:
            time.sleep(2)
            if not game_running():
                log("  the game closed while leaving the match (did it crash?); start it again before the next run")
                break
            if not foreground_is_game():
                previous, calm_since = None, None  # another window in front: can't tell
                continue
            frame = shot()
            since_match = changed_share(frame, in_match)
            since_last = changed_share(frame, previous) if previous is not None else 1.0
            parts = menu_matches(frame)
            trace.append(f"{time.time() - started:.0f}s:{since_match:.2f}/{since_last:.2f}/{parts}")
            # The menu's fixed interface (top bar, chat box, buttons) is back on screen.
            arrived = parts >= 2
            needed = 4
            if arrived:
                calm_since = calm_since or time.time()
                if time.time() - calm_since >= needed:
                    break
            else:
                calm_since = None
            previous = frame
        else:
            log("  the game hadn't settled back at the menu after 2 minutes; carrying on")
        log("  leaving, change since the match / since the last look / menu parts seen: " + " ".join(trace))
    log(f"left the match after {time.time() - started:.0f} s (back at the menu for the next run)")


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


def wait_until_still(grab, checks: int = 3, interval: float = 1.5, limit: float = 180) -> None:
    """Wait for the intro cutscene to end: several frames in a row without the view moving."""
    print("waiting for the view to settle (intro cutscene) ...", flush=True)
    deadline = time.time() + limit
    still, previous = 0, None
    while still < checks and time.time() < deadline:
        wait_for_game()
        frame = grab()
        if frame is not None and previous is not None:
            moved = float(np.abs(frame[::8, ::8].astype(np.int16) - previous[::8, ::8].astype(np.int16)).mean())
            still = still + 1 if moved < 3 else 0
        previous = frame
        time.sleep(interval)
    if still < checks:
        print(f"  still moving after {limit:.0f} seconds; starting anyway")


def magenta_pixels(frame: np.ndarray) -> int:
    """Pixels of the markers' solid magenta (text tags with a (255, 0, 255) background)."""
    r, g, b = frame[::2, ::2, 0], frame[::2, ::2, 1], frame[::2, ::2, 2]
    return int(((r > 170) & (b > 170) & (g < 100)).sum()) * 4


def probe_camera(args, manifest: dict, tiles: list, what: str, values: list) -> None:
    """Step one camera setting over the middle of the map and count marker pixels at each value.
    "zoom": the camera distance. "fov": the field of view, with the distance adjusted so the
    view keeps the planned size (that finds the flattest view that still draws the markers)."""
    if not manifest.get("markers"):
        sys.exit("the map was prepared without --markers; nothing to probe")
    view_h = manifest["screen"]["h"] / manifest["pxPerCell"]
    middle = min(tiles, key=lambda t: abs(t["row"] - manifest["rows"] / 2) + abs(t["col"] - manifest["cols"] / 2))
    print(f"{what.capitalize()} probe on {manifest['map']}: {len(values)} camera settings, counting marker pixels at each. Leave the keyboard and mouse alone.")
    wait_for_map_load(not args.no_launch)
    time.sleep(3)
    results = []
    with ScreenGrabber(game_region()) as screen:

        def grab() -> np.ndarray | None:
            for _ in range(10):
                wait_for_game()
                frame = screen.grab()
                if not looks_black(frame):
                    return frame
                time.sleep(0.4)
            return None

        wait_until_still(grab, limit=45)

        def at_value(v: int) -> np.ndarray | None:
            send_chat(tile_command(middle))
            settle(2.0)
            if what == "fov":
                distance = view_h / 2 / math.tan(math.radians(v) / 2)
                send_chat(f"fov {v}")
                settle(0.3)
                send_chat(f"zoom {distance:.1f}")
            else:
                send_chat(f"zoom {v}")
            settle(2.0)
            return grab()

        for v in values:
            frame = step(lambda: at_value(v), f"{what} {v}")
            n = magenta_pixels(frame) if frame is not None else 0
            results.append((v, n))
            print(f"  {what} {v:4d}: {n:6d} magenta pixels" + ("" if frame is not None else "  (no usable frame)"), flush=True)
            if frame is not None:
                Image.fromarray(np.ascontiguousarray(frame)).save(
                    args.manifest.parent / f"{what}-probe-{v:03d}.png", compress_level=1
                )
        quit_match()
    visible = [v for v, n in results if n >= 400]
    if not visible:
        summary = "markers never visible"
    elif what == "fov":
        summary = f"markers visible from fov {min(visible)} up" + (f"; not at {max(v for v, n in results if n < 400 and v < min(visible))}" if any(v < min(visible) for v, _ in results) else "")
    else:
        summary = f"markers visible up to distance {max(visible)}"
    report = args.manifest.parent / f"{what}-probe.txt"
    report.write_text("\n".join(f"{v}\t{n}" for v, n in results) + "\n" + summary + "\n")
    print(f"\n{summary}\n(details and screenshots in {args.manifest.parent})")


_LOG_PATH = None


def log(*parts, **kw) -> None:
    """print, and append to the run's log file once it is known."""
    text = " ".join(str(p) for p in parts)
    print(text, **kw)
    if _LOG_PATH is not None:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path, help="the .json written by inject.mjs")
    ap.add_argument("--game", default=DEFAULT_GAME, help=f"Heroes of the Storm folder (default {DEFAULT_GAME})")
    ap.add_argument("--battlenet", default=os.environ.get("HRS_BATTLENET"), help="the Battle.net app (Battle.net.exe), used to start Heroes when it isn't running; found automatically if not given")
    ap.add_argument("--no-launch", action="store_true", help="the map is already running")
    ap.add_argument("--launch-only", action="store_true", help="launch the map and stop (to try chat commands by hand)")
    ap.add_argument("--probe-zoom", action="store_true", help="find the camera distance beyond which the markers stop drawing")
    ap.add_argument("--probe-fov", action="store_true", help="find the narrowest field of view at which the markers still draw")
    ap.add_argument("--probe-light", action="store_true", help="screenshots of chosen points (HRS_PROBE_POINTS) from several camera distances, to diagnose uneven lighting")
    ap.add_argument("--probe-sky", action="store_true", help="one edge tile over each solid-colour skybox (inject.mjs --sky), to check the colour shows and is uniform")
    ap.add_argument("--settle", type=float, default=0.5, help="least seconds from a move to the kept screenshot (default 0.5)")
    ap.add_argument("--start", type=int, default=0, help="first tile, to resume a run (default 0)")
    ap.add_argument("--monitor", type=int, help="capture this mss monitor number instead of the game window")
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text())
    tiles = manifest["tiles"]
    out = args.manifest.parent / manifest["id"] / "tiles"
    out.mkdir(parents=True, exist_ok=True)
    global _LOG_PATH
    _LOG_PATH = args.manifest.parent / manifest["id"] / "log.txt"
    log(f"capture {manifest['id']}: {len(tiles)} tiles, screen {manifest['screen']['w']}x{manifest['screen']['h']}, fov {manifest.get('fov')}, {manifest['pxPerCell']} px/cell")
    # Where the camera really was for each tile (the status strip's echo), for stitch.py; kept
    # across a resumed run.
    positions_path = args.manifest.parent / manifest["id"] / "positions.json"
    positions: dict = json.loads(positions_path.read_text()) if args.start and positions_path.exists() else {}
    strip_mode = bool(manifest.get("status"))

    if not args.no_launch:
        switcher = Path(args.game) / "Support64" / "HeroesSwitcher_x64.exe"
        if not switcher.exists():
            sys.exit(f"not found: {switcher} (pass --game)")
        ensure_game_running(args.battlenet, args.game)
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
        print(f"launching {manifest['map']} ({len(tiles)} tiles) ...")
        subprocess.Popen([str(switcher), stormmap])

    if args.probe_zoom:
        probe_camera(args, manifest, tiles, "zoom", [40, 60, 80, 100, 120, 140, 160, 180, 200, 230, 260, 300])
        return
    if args.probe_fov:
        # Narrow to wide; the distance is adjusted with each so the view stays the same size.
        probe_camera(args, manifest, tiles, "fov", [6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 40])
        return

    if args.launch_only:
        print(
            "\nLaunched. In the game, chat commands: 'tile <n>' moves to a tile, 'zoom <distance>'\n"
            "changes the camera distance, 'zoom 0' goes back to the planned one."
        )
        return

    if args.probe_light:
        print(f"Lighting probe on {manifest['map']}: screenshots at chosen spots and cameras.")
    elif args.probe_sky:
        print(f"Skybox probe on {manifest['map']}: one edge tile, a scripted sequence of skybox swaps, a shot after each.")
    else:
        shots = "two shots each (over white, over black)" if (manifest.get("sky") or {}).get("mode") == "matte" else "one shot each (over black; the void is black terrain)"
        print(f"Capturing {manifest['map']}: {len(tiles) - args.start} tiles, {shots}. Leave the keyboard and mouse alone.")
    wait_for_map_load(not args.no_launch)
    time.sleep(3)

    expected = (manifest["screen"]["w"], manifest["screen"]["h"])
    if args.monitor:
        with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
            region = dict(sct.monitors[args.monitor])
    else:
        region = game_region()
    with ScreenGrabber(region, duplication=os.environ.get("HRS_CAPTURE", "duplication") != "mss") as screen:
        log(f"capturing {region['width']}x{region['height']} at ({region['left']}, {region['top']}) by {screen.method}")
        if (region["width"], region["height"]) != expected:
            print(
                f"warning: that is not the {expected[0]}x{expected[1]} the grid was planned for; "
                "the stitch will still work, at a different scale"
            )
        # The status strip (status.py): the map script's readout in the top-left corner, read
        # from every raw frame; kept shots have its column blanked (the stitch leaves it out).
        strip = StatusStrip()
        strip_width = int((manifest.get("status") or {}).get("pageLeft", 0))

        def raw_grab() -> np.ndarray:
            # The grab copies whatever is on screen there, so the game must be in front
            # before and after it; otherwise it may have caught another window.
            require_focus()
            frame = screen.grab()
            require_focus()
            return frame

        def blank(frame: np.ndarray) -> np.ndarray:
            return strip.blank(frame, strip_width) if strip.located and strip_width else frame

        def grab(dark_ok: bool = False) -> np.ndarray | None:
            """One frame as RGB (strip blanked), retaken while it comes back black; None if it
            stays black. `dark_ok` takes a dark, flat frame as it is: a shot at a map's edge
            can be all void (pure black on Tomb of the Spider Queen)."""
            tries = 0
            while tries < 10:
                frame = blank(raw_grab())
                if dark_ok or not looks_black(frame):
                    return frame
                tries += 1
                time.sleep(0.3)
            return None

        seq_counter = [0]

        def send(command: str, timeout: float = 2.0, sends: int = 4) -> tuple[Status, np.ndarray] | None:
            """A chat command with a sequence number appended, then the strip polled until it
            shows that number: the command has been carried out and the frame rendered. Returns
            the status and that raw frame; None when the map never answered (sent `sends`
            times, `timeout` seconds each)."""
            for _ in range(sends):
                seq_counter[0] = seq_counter[0] % 255 + 1  # 1..255; 0 is what the strip shows before any command
                seq = seq_counter[0]
                send_chat(f"{command} {seq}")
                deadline = time.time() + timeout
                while time.time() < deadline:
                    raw = raw_grab()
                    status = strip.read(raw)
                    if status is not None and status.seq == seq:
                        return status, raw
                    time.sleep(0.02)
            return None

        # Start-up. The first command goes to the middle tile (of the first arena, on a map of
        # several): a corner tile can be clamped far from where it was sent.
        first_area = [t for t in tiles if t.get("area", 0) == 0]
        centre_x = (min(t["x"] for t in first_area) + max(t["x"] for t in first_area)) / 2
        centre_y = (min(t["y"] for t in first_area) + max(t["y"] for t in first_area)) / 2
        first_tile = min(first_area, key=lambda t: abs(t["x"] - centre_x) + abs(t["y"] - centre_y))
        if manifest.get("keepIntro"):
            # Diagnostic: the intro plays out. Nothing is typed meanwhile, in case keys count
            # as skipping it.
            log("letting the intro cutscene play out (nothing typed for 60 s) ...")
            step(lambda: settle(60.0), "the wait for the intro")

        if strip_mode:
            # 1. The status strip: drawn by the map script from its start, but the interface is
            #    hidden while the intro cutscene plays. Nothing is typed until it shows.
            deadline = time.time() + 180
            log("waiting for the map's status strip ...")
            while not step(lambda: strip.locate(raw_grab()), "finding the status strip"):
                if time.time() > deadline:
                    quit_match()
                    sys.exit(
                        "\nStopped: the map's status strip never appeared. If every interface panel is visible\n"
                        "in the game, the map's script failed to compile."
                    )
                time.sleep(0.5)
            log(f"  status strip: {strip.cell} px cells at {strip.origin}")
            if strip_width and strip.cell + strip.origin[0] > strip_width:
                log(f"  warning: the strip ({strip.cell + strip.origin[0]} px) is wider than the {strip_width} px blanked; lower the interface scale or raise pageLeft")
            # 2. The first tile, until the map takes it: `tile` is ignored while the intro's
            #    exit is still restoring the camera and interface.
            while True:
                answer = step(lambda: send(tile_command(first_tile), timeout=2.0, sends=1), "the start-up")
                if answer:
                    break
                if time.time() > deadline:
                    quit_match()
                    sys.exit("\nStopped: the map never carried out a command (the strip shows, so the script runs; is a selection phase holding it?)")
                log("  start-up: the map hasn't taken a command yet (intro or selection phase)")
                time.sleep(1.0)
            # 3. The camera bounds the game really applies (an arena's are far tighter than its
            #    map file says): the camera is sent to the map's corners and the strip reports
            #    where it stopped. A grid planned for the wrong bounds spends most tiles on
            #    sky. (Not when the script lifts the bounds: the grid then comes from the map
            #    file, or from its arena areas.)
            if not manifest.get("unbound"):
                size = manifest["mapSize"]
                low = step(lambda: send("tile 0 0 0", timeout=3.0), "the bounds check")
                high = step(lambda: send(f"tile 0 {size['width']} {size['height']}", timeout=3.0), "the bounds check")
                bounds = None
                if low and high:
                    bounds = {"left": low[0].camera_x, "bottom": low[0].camera_y, "right": high[0].camera_x, "top": high[0].camera_y}
                    if not (bounds["left"] < bounds["right"] and bounds["bottom"] < bounds["top"]):
                        bounds = None
                if bounds is None:
                    log("  couldn't measure the camera bounds in the game; using the map file's")
                else:
                    planned = manifest["cameraBounds"]
                    if any(abs(bounds[k] - planned[k]) > 2 for k in bounds):
                        plan_grid(manifest, bounds)
                        tiles = manifest["tiles"]
                        args.manifest.write_text(json.dumps(manifest, indent=2))
                        log(f"camera bounds in the game: {bounds} (map file: {planned}); grid re-planned: {manifest['cols']}x{manifest['rows']} = {len(tiles)} tiles")
                    else:
                        log(f"camera bounds in the game match the map file: {bounds}")
            # The game's intro exit pans the camera to the player's start position a moment after
            # the map starts taking commands; the first tile must not be shot into that. The
            # view has to hold still for a second before the tiles start.
            def hold_still() -> None:
                before = grab(dark_ok=True)
                for _ in range(20):
                    settle(1.0)
                    now = grab(dark_ok=True)
                    if before is not None and now is not None and same_view(now, before):
                        return
                    before = now

            step(hold_still, "waiting for the camera to hold still")
        else:

            def start() -> None:
                send_chat(tile_command(first_tile))
                settle(3.0)
                wait_until_still(grab, limit=180)
                send_chat(tile_command(first_tile))
                settle(1.0)

            step(start, "the start-up")

        if args.probe_light:
            # Does the dark, straight-edged area depend on the camera? (Time, fog of war, the
            # reveal and the shadow setting made no difference.) Whole-map views with a wider
            # lens at three distances and from three camera positions, then the capture camera
            # itself over neighbouring tiles, and one tile again after a trip to the far corner.
            # Every shot is named after its camera.
            probe_dir = out.parent / f"probe-light-{time.strftime('%H%M%S')}"
            probe_dir.mkdir(exist_ok=True)

            def shot(name: str) -> None:
                frame = grab(dark_ok=True)
                if frame is not None:
                    Image.fromarray(np.ascontiguousarray(frame)).save(probe_dir / f"{name}.png", compress_level=1)
                log(f"  probe: {name}" + ("" if frame is not None else " (no frame)"))

            def at(row: int, col: int) -> dict:
                return min(tiles, key=lambda t: abs(t["row"] - row) + abs(t["col"] - col))

            def run_probe() -> None:
                # Does the darkening (the straight-edged area, the squares around holes) depend
                # on the camera's distance? Each point (HRS_PROBE_POINTS="x,y;x,y", map cells)
                # from close up to further than the capture camera, and with the capture camera.
                points = [tuple(float(v) for v in p.split(",")) for p in os.environ.get("HRS_PROBE_POINTS", "").split(";") if p.strip()]
                if not points:
                    points = [(t["x"], t["y"]) for t in (at(manifest["rows"] // 2, manifest["cols"] // 2),)]
                send_chat("clean")  # no labels in these shots
                settle(0.5)
                for n, (x, y) in enumerate(points, start=1):
                    send_chat(f"look {x:.1f} {y:.1f}")
                    settle(1.0)
                    if os.environ.get("HRS_PROBE_TILE_PATH"):
                        # The render's own path at this spot: "tile" (scene set-up and all) or
                        # "move" (the same camera moves, no scene set-up), acknowledged through
                        # the strip, then `clean`, a wait, a shot. Entries "cmd:settle;...", each
                        # after a jump 100 cells away so the spot is arrived at afresh.
                        # Entries "cmd,cmd,...:settle": the commands in order ("tile" and "move"
                        # get the spot appended; "black", "sky white" etc. as they are), then
                        # `clean`, the wait, a shot.
                        for k, entry in enumerate(os.environ["HRS_PROBE_TILE_PATH"].split(";")):
                            cmds, wait = entry.rsplit(":", 1)
                            if "@" not in cmds:
                                send_chat(f"look {x + 100:.1f} {y:.1f}")  # arrive afresh
                                settle(1.0)
                            acked = True
                            for cmd in cmds.split(","):
                                # "tile@x,y" / "move@x,y": that spot instead of the point (a
                                # neighbouring tile, to arrive the way the render does).
                                if cmd.startswith(("tile@", "move@")):
                                    name, at = cmd.split("@")
                                    full = f"{name} 0 {at.replace(':', ' ')}"
                                elif cmd in ("tile", "move"):
                                    full = f"{cmd} 0 {x:.2f} {y:.2f}"
                                else:
                                    full = cmd
                                acked = send(full, timeout=3.0) is not None and acked
                            if float(wait) > 5:
                                log(f"  waiting {float(wait):g} s of match time before the next shot ...")
                            settle(float(wait))
                            send("clean")
                            label = cmds.replace(",", "+").replace(" ", "").replace(":", "_") if len(cmds) < 40 else f"{cmds.count(',') + 1}cmds-{cmds.split(',')[0].replace(' ', '')}"
                            shot(f"point{n}-{k:02d}-{label}-s{wait}" + ("" if acked else "-noack"))
                        continue
                    if os.environ.get("HRS_PROBE_REFIT_SCAN"):
                        # Where must the normal camera look for its refit to reach this spot?
                        # Each entry "dx,dy,distance": the normal camera at that offset from the
                        # spot and that distance, then the capture camera at the spot, one shot.
                        # Entries separated by ";": a sequence of looks separated by "|", each
                        # "dx,dy,distance[,pitch,yaw,dwell[,settle]]", then the capture camera,
                        # `settle` seconds (the last look's; default 2), and a shot.
                        for k, entry in enumerate(os.environ["HRS_PROBE_REFIT_SCAN"].split(";")):
                            names = []
                            final_settle = 2.0
                            for look in entry.split("|"):
                                vals = [float(v) for v in look.split(",")]
                                vals += [0, 0, 1.5, 2.0][len(vals) - 3 :]  # defaults for pitch, yaw, dwell, settle
                                dx, dy, dist, npitch, nyaw, dwell, final_settle = vals
                                send_chat(f"look {x + dx:.1f} {y + dy:.1f}")
                                settle(0.3)
                                send_chat(f"normal {dist:g} {npitch:g} {nyaw:g}")
                                settle(dwell)
                                names.append(f"d{dist:g}p{npitch:g}y{nyaw:g}w{dwell:g}")
                            send_chat(f"look {x:.1f} {y:.1f}")
                            settle(0.3)
                            send_chat("fov 0")
                            settle(0.3)
                            send_chat("zoom 0")
                            settle(final_settle)
                            shot(f"point{n}-{k:02d}-refit-" + "_".join(names) + f"-s{final_settle:g}")
                        continue
                    if os.environ.get("HRS_PROBE_CLIP"):
                        # The capture camera at the current clip planes, then shorter far clips
                        # and a raised near clip (the one untested lever for the boxes around
                        # holes), a normal-camera refit, and the capture camera again.
                        send_chat("fov 0")
                        settle(0.5)
                        send_chat("zoom 0")
                        settle(2.0)
                        shot(f"point{n}-1-clip-default")
                        for k, (near, far) in enumerate(((0.1, 300), (0.1, 150), (5, 300), (0.1, 800)), start=2):
                            send_chat(f"clip {near:g} {far:g}")
                            settle(2.0)
                            shot(f"point{n}-{k}-clip-{near:g}-{far:g}")
                        send_chat("normal")
                        settle(1.0)
                        send_chat("fov 0")
                        settle(0.5)
                        send_chat("zoom 0")
                        settle(2.0)
                        shot(f"point{n}-6-after-refit")
                        continue
                    if os.environ.get("HRS_PROBE_REFRESH"):
                        # Does a camera like real play make the game refit its lighting? Wide and
                        # capture-camera shots before, the normal camera itself, the same after.
                        def cam(label: str, f: float, distance: float) -> None:
                            send_chat(f"fov {f:g}")
                            settle(0.5)
                            send_chat(f"zoom {distance:g}")
                            settle(2.0)
                            shot(f"point{n}-{label}")

                        cam("1-before-wide", 40, 200)
                        cam("2-before-capture", 0, 0)
                        send_chat("normal")
                        settle(2.0)
                        shot(f"point{n}-3-normal")
                        cam("4-after-capture", 0, 0)
                        cam("5-after-wide", 40, 200)
                        continue
                    # Cameras: HRS_PROBE_CAMERAS="fov:distance,..." or, at fov 40,
                    # HRS_PROBE_DISTANCES="d,d,...". Settings may repeat (a zoom out then back
                    # in, to check the result doesn't depend on the way there): shots are
                    # numbered in order.
                    # "fov:distance" or "fov:distance:pitch" per camera.
                    if os.environ.get("HRS_PROBE_CAMERAS"):
                        cameras = [tuple(float(v) for v in c.split(":")) for c in os.environ["HRS_PROBE_CAMERAS"].split(",")]
                    else:
                        cameras = [(40.0, float(d)) for d in os.environ.get("HRS_PROBE_DISTANCES", "25,50,100,200,300").split(",")]
                    fov = pitch = None
                    for k, camera in enumerate(cameras):
                        if camera[0] == 0:  # "0:0": the normal play camera
                            send_chat("normal")
                            settle(2.0)
                            shot(f"point{n}-{k:02d}-normal-camera")
                            fov = pitch = None  # the next camera re-applies everything
                            continue
                        f, distance = camera[0], camera[1]
                        want_pitch = camera[2] if len(camera) > 2 else 90.0
                        if want_pitch != pitch:
                            send_chat(f"pitch {want_pitch:g}")
                            settle(0.5)
                            pitch = want_pitch
                        if f != fov:
                            send_chat(f"fov {f:g}")
                            settle(0.5)
                            fov = f
                        send_chat(f"zoom {distance:g}")
                        settle(2.0)
                        shot(f"point{n}-{k:02d}-fov{f:g}-distance{distance:03.0f}-pitch{want_pitch:g}")
                    send_chat("fov 0")
                    settle(0.5)
                    send_chat("zoom 0")
                    settle(2.0)
                    shot(f"point{n}-capture-camera")

            step(run_probe, "the lighting probe")
            quit_match()
            print(f"\nprobe screenshots in {probe_dir}")
            return

        if args.probe_sky:
            # Do our solid-colour skyboxes show, and are they flat? An edge tile (left edge,
            # middle row: half map, half sky) over each colour in turn; the sky part of each
            # shot is measured (mean colour and spread) and the shots are kept.
            probe_dir = out.parent / f"probe-sky-{time.strftime('%H%M%S')}"
            probe_dir.mkdir(exist_ok=True)
            edge = min(tiles, key=lambda t: t["col"] * 1000 + abs(t["y"] - (manifest["area"]["top"] + manifest["area"]["bottom"]) / 2))

            def run_sky_probe() -> None:
                send_chat(tile_command(edge))
                settle(2.0)
                send_chat("clean")
                settle(0.5)

                def shot(name: str) -> None:
                    frame = grab(dark_ok=True)
                    if frame is None:
                        log(f"  {name}: no frame")
                        return
                    Image.fromarray(np.ascontiguousarray(frame)).save(probe_dir / f"{name}.png", compress_level=1)
                    # The left fifth (sky, if the tile sits on the edge) and a patch of open
                    # sky further in.
                    h, w = frame.shape[:2]
                    parts = []
                    for what, part in (("left fifth", frame[:, : w // 5]), ("sky patch", frame[: h // 3, w // 4 : w // 4 + w // 8])):
                        px = part.reshape(-1, 3).astype(np.float32)
                        parts.append(f"{what} {tuple(int(v) for v in px.mean(axis=0))} ±{tuple(round(float(v), 1) for v in px.std(axis=0))}")
                    log(f"  {name}: " + "; ".join(parts))

                # A scripted sequence: what each command shows, in order. Layer 0 is the
                # camera-fixed skybox; layer 1 the terrain-relative parallax layer. The same
                # command twice a while apart tells a slow transition from a failed swap.
                sequence = os.environ.get("HRS_SKY_SEQUENCE") or "start:0|black 0:2.5|white 0:2.5|magenta 0:2.5|lime 0:2.5|cyan 0:2.5|none 0:2.5"
                previous = None
                for k, item in enumerate(sequence.split("|")):
                    command, wait = item.rsplit(":", 1)
                    if command not in ("start", previous):  # the same command again: only wait longer, don't resend
                        send_chat(f"sky {command}")
                    settle(float(wait))
                    shot(f"{k:02d}-{command.replace(' ', '-layer')}-{wait}s")
                    previous = command

            step(run_sky_probe, "the skybox probe")
            quit_match()
            print(f"\nprobe screenshots in {probe_dir}")
            return

        last_calibration: list[np.ndarray] = []  # the frame the strip acknowledged `tile` in: the reference view
        calibration_mismatch = [False]  # set by whole_frame: the kept shot isn't the reference view

        def black_settled(first: np.ndarray, white: np.ndarray) -> np.ndarray:
            """The shot over black, once the skybox swap has finished rendering: the share of
            black pixels jumps and then holds still between two grabs (no sky in view: the
            first frame as it is)."""

            def black_share(frame: np.ndarray) -> float:
                return float((frame[::4, ::4].max(axis=2) <= 6).mean())

            before, frame, share = black_share(white), first, black_share(first)
            for _ in range(8):
                if share - before <= 0.005:
                    return frame  # no sky in view, or the swap not rendered yet: look once more below
                settle(0.05)
                again = grab(dark_ok=True)
                if again is None:
                    return frame
                next_share = black_share(again)
                if abs(next_share - share) < 0.002:
                    return again
                frame, share = again, next_share
            return frame

        def whole_frame(frame: np.ndarray | None) -> np.ndarray | None:
            """The kept shot, retaken while it disagrees with this tile's reference frame (same
            view) and with the retake before it: a half-drawn frame. Two retakes that agree
            with each other are accepted, in case the reference was the bad one; then
            calibration_mismatch is set."""
            calibration_mismatch[0] = False
            reference = last_calibration[0] if last_calibration else None
            # Same view as the calibration shot (no shift), however much animates (an animated
            # sky, lava): the markers' measurement holds.
            # A half-drawn frame (drawn at the top, black below) matches the reference where it
            # is drawn, so the shift test alone would pass it: not if it has black where the
            # reference has none.
            featureless = frame is not None and float(frame[::8, ::8].std()) < 3.0
            partly_black = (
                frame is not None and reference is not None
                and float(((frame[::8, ::8].max(axis=2) < 12) & (reference[::8, ::8].max(axis=2) >= 12)).mean()) > 0.02
            )
            if frame is not None and reference is not None and not featureless and not partly_black and view_shift(frame, reference) <= 1.5:
                return frame
            for attempt in range(5):
                # (A black grab of a view that is all void passes, and looks just like it: some
                # maps' void is pure black.)
                if frame is None or reference is None or not disagree(frame, reference):
                    calibration_mismatch[0] = attempt > 0
                    return frame
                log("    half-drawn frame; retaking")
                settle(0.1)
                reference, frame = frame, grab(dark_ok=True)
            return frame

        # PNGs are written in the background while the next tile is shot.
        saver = ThreadPoolExecutor(max_workers=2)

        previous = None
        failures = 0  # consecutive tiles that stayed black
        started = time.time()
        matting = (manifest.get("sky") or {}).get("mode") == "matte"

        def shoot(tile: dict) -> tuple[Status | None, np.ndarray | None, np.ndarray | None]:
            """One tile, start to finish, as one step: (the strip's status at the tile: where
            the camera really is; the kept shot; the same view over the black skybox when
            matting). A tile whose kept shot isn't the view it was acknowledged in (something
            moved the camera in between) is done again, once."""
            if not strip_mode:
                send_chat(tile_command(tile))
                settle(max(args.settle, 1.0))
                return None, grab(), None
            result = shoot_once(tile)
            if calibration_mismatch[0] and result[1] is not None:
                log(f"    tile {tile['index']}: the view changed under it; doing it again")
                result = shoot_once(tile)
            return result

        def shoot_once(tile: dict) -> tuple[Status | None, np.ndarray | None, np.ndarray | None]:
            calibration_mismatch[0] = False
            # `tile`, acknowledged once the camera has moved and been clamped: the status says
            # where the camera really is, and the acknowledged frame is the reference view.
            answer = send(tile_command(tile), timeout=3.0)
            if answer is None:
                return None, None, None
            moved = time.time()
            status = answer[0]
            last_calibration[:] = [blank(answer[1])]
            # Shot 1, clean: camera unmoved, over the white skybox, not before --settle seconds
            # from the move (textures still loading). This is the image kept.
            wait = moved + args.settle - time.time()
            if wait > 0:
                settle(wait)
            answer = send("clean")
            if answer is None:
                return status, None, None
            frame = whole_frame(blank(answer[1]))
            # Shot 2 (matting): the same over black; the stitch turns the pair into colour and
            # transparency.
            black = None
            if matting and frame is not None:
                answer = send("black")
                if answer is not None:
                    black = black_settled(blank(answer[1]), frame)
            return status, frame, black

        for tile in tiles[args.start :]:
            note = ""
            status, frame, black = step(lambda: shoot(tile), f"tile {tile['index']}")
            if strip_mode and status is not None:
                # Where the camera really is, for the stitch. Near the edges the game holds it
                # back, which is worth a note.
                positions[str(tile["index"])] = {"x": status.camera_x, "y": status.camera_y}
                positions_path.write_text(json.dumps(positions))
                off_x, off_y = status.camera_x - tile["x"], status.camera_y - tile["y"]
                if abs(off_x) > 0.5 or abs(off_y) > 0.5:
                    parts = []
                    if abs(off_x) > 0.5:
                        parts.append(f"{abs(off_x):.1f} cells {'east' if off_x > 0 else 'west'}")
                    if abs(off_y) > 0.5:
                        parts.append(f"{abs(off_y):.1f} cells {'north' if off_y > 0 else 'south'}")
                    note += "  camera " + " and ".join(parts) + " of plan"
                if calibration_mismatch[0]:
                    note += "  (kept shot doesn't match the view the tile was acknowledged in)"
            # Near the edges the game holds the camera back, so neighbouring tiles can really
            # show the same view; keep it either way (the stitch places duplicates on top of each
            # other). Only black frames are failures.
            if frame is not None and previous is not None and same_view(frame, previous):
                note += "  (same view as the previous tile)"

            if frame is None:
                failures += 1
                log(f"    tile {tile['index']} failed (no answer from the map, or black frames); not saved")
                if failures >= 3:
                    quit_match()
                    first_bad = tile["index"] - failures + 1
                    sys.exit(
                        f"\nStopped: the game stopped responding at tile {first_bad} (no answers, or black frames).\n"
                        f"Check what the game shows, then resume from there in a fresh game:\n"
                        f"  py capture.py {args.manifest} --game \"{args.game}\" --start {first_bad}"
                    )
                continue
            failures = 0
            previous = frame
            path = out / f"tile_{tile['index']:04d}.png"
            saver.submit(Image.fromarray(np.ascontiguousarray(frame)).save, path, compress_level=1)
            if matting:
                if black is not None:
                    saver.submit(Image.fromarray(np.ascontiguousarray(black)).save, out / f"tile_{tile['index']:04d}-black.png", compress_level=1)
                else:
                    note += "  (no shot over black; kept opaque)"
            done = tile["index"] - args.start + 1
            eta = (time.time() - started) / done * (len(tiles) - args.start - done)
            log(f"  tile {tile['index'] + 1}/{len(tiles)}  (row {tile['row']}, col {tile['col']})  ~{eta:.0f}s left{note}")

        saver.shutdown(wait=True)

    quit_match()
    if strip_mode:
        log(f"camera positions recorded for {len(positions)} screenshots")
    print(f"\n{len(tiles) - args.start} screenshots in {out}\nnext: python stitch.py {args.manifest}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback

        # Into the log too, so a failed run can be diagnosed from the copied results.
        log(traceback.format_exc())
        raise
