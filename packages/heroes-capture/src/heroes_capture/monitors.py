"""The monitors on the Windows desktop, and the game's window put on one of them (`map render
--monitor`): a monitor by its number (in the order Windows lists them), its device name
(`\\\\.\\DISPLAY2`) or `primary`. Plain Win32 through ctypes; real pixels, whatever the display
scaling. Nothing here knows about the game.
"""

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass

if hasattr(ctypes, "windll"):  # on Windows (the pure parts, game_size, are usable anywhere)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()

MONITORINFOF_PRIMARY = 0x1
SWP_NOZORDER, SWP_FRAMECHANGED = 0x0004, 0x0020
# The narrowest picture the game draws: 5:4. On a narrower screen it draws a 5:4 picture the
# screen's width wide, centred, with black above and below (a 1680x1440 monitor got 1680x1344 at
# 48 px from the top), and its window is that picture.
MIN_ASPECT = 5 / 4


def game_size(width: int, height: int) -> tuple[int, int]:
    """The size the game draws at on a screen this size: the screen's, unless it is narrower than
    MIN_ASPECT, then as tall as 5:4 allows."""
    return width, min(height, int(round(width / MIN_ASPECT)))


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]


@dataclass(frozen=True)
class Monitor:
    number: int  # 1-based, as Windows lists them
    device: str  # \\.\DISPLAY1
    left: int
    top: int
    width: int
    height: int
    primary: bool

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def game_size(self) -> str:
        """The size the game draws at on this monitor (game_size), as "WxH"."""
        return "{}x{}".format(*game_size(self.width, self.height))

    def game_rect(self) -> tuple[int, int, int, int]:
        """Where the game's window is when it is on this monitor: the monitor, or on one narrower
        than the game draws, its 5:4 picture centred on it."""
        w, h = game_size(self.width, self.height)
        return (self.left, self.top + (self.height - h) // 2, w, h)

    def describe(self) -> str:
        return f"{self.number} {self.device} {self.size} at ({self.left}, {self.top})" + (" primary" if self.primary else "")


def monitors() -> list[Monitor]:
    """Every monitor of the desktop, in the order Windows lists them."""
    user32 = ctypes.windll.user32
    found: list[Monitor] = []
    visitor = ctypes.WINFUNCTYPE(wt.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wt.RECT), wt.LPARAM)

    @visitor
    def visit(hmonitor, _hdc, _rect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            found.append(Monitor(len(found) + 1, info.szDevice, r.left, r.top, r.right - r.left, r.bottom - r.top,
                                 bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    user32.EnumDisplayMonitors(None, None, visit, 0)
    return found


def choose(spec: str, found: list[Monitor] | None = None) -> Monitor:
    """The monitor `spec` names: its number, its device name, or `primary`. ValueError, listing
    the monitors, when none does."""
    found = monitors() if found is None else found
    for m in found:
        if spec == str(m.number) or spec.lower() == m.device.lower() or (spec.lower() == "primary" and m.primary):
            return m
    raise ValueError(f"no monitor {spec!r}; the monitors are: " + "; ".join(m.describe() for m in found))


def place_window(hwnd, monitor: Monitor) -> None:
    """A window moved onto a monitor and sized to it. The game in Windowed (Fullscreen) is a
    borderless window that draws at the size it is given, so it then renders at that monitor's
    resolution."""
    ctypes.windll.user32.SetWindowPos(hwnd, None, monitor.left, monitor.top, monitor.width, monitor.height, SWP_NOZORDER | SWP_FRAMECHANGED)
