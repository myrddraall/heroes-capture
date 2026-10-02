"""The game on the Windows desktop: its process, its window, and keyboard and mouse input to it.

Only plain Win32 calls through ctypes. Nothing here knows about maps or the capture.
"""

import ctypes
import ctypes.wintypes as wt
import time
from pathlib import Path

import pydirectinput

pydirectinput.PAUSE = 0.02  # between synthetic key events

# Real pixel coordinates on every monitor, whatever the Windows display scaling.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    ctypes.windll.user32.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# ------------------------------------------------------------------------------------------------
# The game's process
# ------------------------------------------------------------------------------------------------

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def process_name(pid: int) -> str | None:
    """The file name of a process's program, or None if it can't be looked at."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return Path(buf.value).name
    finally:
        kernel32.CloseHandle(handle)


def is_game_program(name: str | None) -> bool:
    """The game's own program, HeroesOfTheStorm*.exe."""
    return bool(name) and name.lower().startswith("heroesofthestorm")


def window_program(hwnd) -> str | None:
    """The program a window belongs to."""
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return process_name(pid.value)


def foreground_is_game() -> bool:
    """Whether the window in front belongs to the game's own program.

    Checked by process rather than window title: a title check also passed for other windows
    that happened to mention the game, and the keys went there.
    """
    return is_game_program(window_program(ctypes.windll.user32.GetForegroundWindow()))


def foreground_program() -> str:
    """The program of the window in front, for messages."""
    return window_program(ctypes.windll.user32.GetForegroundWindow()) or "?"


def game_process_seen() -> bool:
    """One look through the running processes for the game's."""
    kernel32 = ctypes.windll.kernel32
    pids = (ctypes.c_ulong * 4096)()
    used = ctypes.c_ulong()
    if not kernel32.K32EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(used)):
        return True  # can't tell: assume it is
    return any(is_game_program(process_name(pid)) for pid in pids[: used.value // ctypes.sizeof(ctypes.c_ulong)])


def game_running() -> bool:
    """Whether any HeroesOfTheStorm*.exe is running (after a crash, none is). One look can miss
    it (it did once, with the game plainly running), so three misses a second apart are
    needed before it counts as gone."""
    for attempt in range(3):
        if game_process_seen():
            return True
        if attempt < 2:
            time.sleep(1.0)
    return False


# ------------------------------------------------------------------------------------------------
# The game's window
# ------------------------------------------------------------------------------------------------


def game_region() -> dict:
    """The game window's drawable area in screen pixels (the foreground window: the game)."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    rect, origin = RECT(), POINT(0, 0)
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return {"left": origin.x, "top": origin.y, "width": rect.right, "height": rect.bottom}


def park_cursor() -> None:
    """The mouse to the bottom-left of the game window, inside the column blanked for the status
    strip and below its cells: the game draws its own cursor, so wherever it is parked it is in
    every screenshot (at the middle it left a mark at each tile's centre), and at a screen edge
    it would scroll the camera (the map script locks camera input too, but not before it runs)."""
    try:
        region = game_region()
        ctypes.windll.user32.SetCursorPos(region["left"] + 28, region["top"] + region["height"] - 40)
    except Exception:
        pass  # no window yet; nothing to park over


def bring_game_to_front() -> bool:
    """Find the game's window and put it in front (so a menu shot can be taken and keys go to
    it). Windows only lets a process that has just sent input do that, hence the Alt tap."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and is_game_program(window_program(hwnd)):
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


# ------------------------------------------------------------------------------------------------
# Keyboard input (SendInput with scan codes, which the game accepts)
# ------------------------------------------------------------------------------------------------


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


KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
EXTRA_KEYS = {"numpad5": 0x4C}  # scan codes pydirectinput leaves out (its number pad entries are commented out)


def _key_event(key: str, up: bool) -> INPUT:
    code = EXTRA_KEYS.get(key) or pydirectinput.KEYBOARD_MAPPING[key]
    return INPUT(type=1, ki=KEYBDINPUT(0, code, KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0), 0, 0))


def type_burst(keys: list[str]) -> None:
    """Press and release each key, all in one SendInput call: the game gets them in order with
    nothing else in between, as fast as it reads them. Only for characters typed into the open
    chat box: the game can miss a hotkey (such as the Enter that opens and sends chat) pressed
    and released in the same instant; see hold_key."""
    events = [_key_event(key, up) for key in keys for up in (False, True)]
    batch = (INPUT * len(events))(*events)
    ctypes.windll.user32.SendInput(len(events), batch, ctypes.sizeof(INPUT))


def hold_key(key: str, seconds: float = 0.04) -> None:
    """Press a key and hold it for a few frames, so the game can't miss it."""
    for up in (False, True):
        event = _key_event(key, up)
        ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        if not up:
            time.sleep(seconds)
