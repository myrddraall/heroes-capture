"""Where the native libraries are (StormLib, CascLib): bundled into the executable, or in a
development copy's native/ folder, which tools/build_native.py fills (not in git)."""

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[2] / "native"


def library(windows_name: str, other_name: str) -> Path:
    """The library's file: on Windows `windows_name` (a DLL), elsewhere `other_name`."""
    name = windows_name if sys.platform == "win32" else other_name
    bundled = getattr(sys, "_MEIPASS", None)
    path = Path(bundled) / name if bundled else NATIVE / name
    if not path.exists():
        raise RuntimeError(f"{name} is missing (expected {path}): build it with python tools/build_native.py"
                           + (" --windows" if windows_name == name and sys.platform == "win32" else ""))
    return path
