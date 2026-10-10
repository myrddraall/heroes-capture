"""The monitors on the Windows desktop, and the game's window put on one of them (`map render
--monitor`): a monitor by its number (in the order Windows lists them), its device name
(`\\\\.\\DISPLAY2`) or `primary`. Plain Win32 through ctypes; real pixels, whatever the display
scaling. Nothing here knows about the game.
"""

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    ctypes.windll.user32.SetProcessDPIAware()

MONITORINFOF_PRIMARY = 0x1
SWP_NOZORDER, SWP_FRAMECHANGED = 0x0004, 0x0020


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
