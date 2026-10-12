"""Where the native libraries are (StormLib, CascLib): bundled into the executable, or in a
development copy's native/ folder, which tools/build_native.py fills (not in git)."""

import ctypes
import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3] / "native"


def library(windows_name: str, other_name: str) -> Path:
    """The library's file: on Windows `windows_name` (a DLL), elsewhere `other_name`."""
    name = windows_name if sys.platform == "win32" else other_name
    bundled = getattr(sys, "_MEIPASS", None)
    path = Path(bundled) / name if bundled else NATIVE / name
    if not path.exists():
        raise RuntimeError(f"{name} is missing (expected {path}): build it with python tools/build_native.py"
                           + (" --windows" if windows_name == name and sys.platform == "win32" else ""))
    return path


def load(windows_name: str, other_name: str, signatures, use_last_error: bool = False):
    """The library loaded, each function given its argument and result types: `signatures(path_type)`
    -> {name: ([arg types], result type)}, where path_type is how the library takes file paths
    (on Windows both are Unicode builds: wide strings; elsewhere bytes). The library's `path_type`
    is kept on it, for encode_path."""
    path = library(windows_name, other_name)
    if sys.platform == "win32":
        lib, path_type = ctypes.WinDLL(str(path), use_last_error=use_last_error), ctypes.c_wchar_p
    else:
        lib, path_type = ctypes.CDLL(str(path)), ctypes.c_char_p
    for function, (args, result) in signatures(path_type).items():
        getattr(lib, function).argtypes = args
        getattr(lib, function).restype = result
    lib.path_type = path_type
    return lib


def encode_path(lib, text: str):
    """A path as the library takes it (load)."""
    return text if lib.path_type is ctypes.c_wchar_p else os.fsencode(text)
