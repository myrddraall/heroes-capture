"""MPQ archives (a battleground's .stormmap) through StormLib (v9.40), by ctypes. On Windows
it is StormLib's own release DLL (a Unicode build), elsewhere libstorm.so built from the same
release; native.py says where (tools/build_native.py puts them there).
"""

import ctypes
import functools
import sys
from pathlib import Path

from heroes_capture.common.native import encode_path, load

MPQ_FILE_COMPRESS = 0x00000200
MPQ_FILE_REPLACEEXISTING = 0x80000000
MPQ_COMPRESSION_ZLIB = 0x02
SFILE_OPEN_FROM_MPQ = 0

def _signatures(path_type) -> dict:
    handle, dword, name = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p
    return {
        "SFileOpenArchive": ([path_type, dword, dword, ctypes.POINTER(handle)], ctypes.c_bool),
        "SFileCloseArchive": ([handle], ctypes.c_bool),
        "SFileHasFile": ([handle, name], ctypes.c_bool),
        "SFileOpenFileEx": ([handle, name, dword, ctypes.POINTER(handle)], ctypes.c_bool),
        "SFileGetFileSize": ([handle, ctypes.POINTER(dword)], dword),
        "SFileReadFile": ([handle, ctypes.c_void_p, dword, ctypes.POINTER(dword), ctypes.c_void_p], ctypes.c_bool),
        "SFileCloseFile": ([handle], ctypes.c_bool),
        "SFileCreateFile": ([handle, name, ctypes.c_uint64, dword, dword, dword, ctypes.POINTER(handle)], ctypes.c_bool),
        "SFileWriteFile": ([handle, ctypes.c_void_p, dword, dword], ctypes.c_bool),
        "SFileFinishFile": ([handle], ctypes.c_bool),
    }


@functools.cache
def _load():
    return load("StormLib.dll", "libstorm.so", _signatures, use_last_error=True)


def _error() -> int:
    if sys.platform == "win32":
        return ctypes.get_last_error()
    lib = _load()
    lib.SErrGetLastError.restype = ctypes.c_uint32
    return lib.SErrGetLastError()


class Archive:
    """An MPQ archive opened for reading and writing; a context manager."""

    def __init__(self, path: str | Path):
        self.lib = _load()
        self.handle = ctypes.c_void_p()
        if not self.lib.SFileOpenArchive(encode_path(self.lib, str(path)), 0, 0, ctypes.byref(self.handle)):
            raise OSError(f"could not open {path} as an MPQ archive (error {_error()})")

    def __enter__(self) -> "Archive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self.handle:
            self.lib.SFileCloseArchive(self.handle)
            self.handle = ctypes.c_void_p()

    def has(self, name: str) -> bool:
        return bool(self.lib.SFileHasFile(self.handle, name.encode()))

    def read(self, name: str) -> bytes:
        file = ctypes.c_void_p()
        if not self.lib.SFileOpenFileEx(self.handle, name.encode(), SFILE_OPEN_FROM_MPQ, ctypes.byref(file)):
            raise OSError(f"{name} is not in the archive (error {_error()})")
        try:
            size = self.lib.SFileGetFileSize(file, None)
            buffer = ctypes.create_string_buffer(size)
            got = ctypes.c_uint32()
            if size and not self.lib.SFileReadFile(file, buffer, size, ctypes.byref(got), None):
                raise OSError(f"could not read {name} (error {_error()})")
            return buffer.raw[: got.value]
        finally:
            self.lib.SFileCloseFile(file)

    def read_optional(self, name: str) -> bytes | None:
        """A file's contents, or None when the archive has no such file."""
        return self.read(name) if self.has(name) else None

    def read_text(self, name: str) -> str | None:
        """A text file's contents, or None when the archive has no such file."""
        data = self.read_optional(name)
        return data.decode("utf-8", errors="replace") if data is not None else None

    def write(self, name: str, data: bytes) -> None:
        """Add the file, replacing any of that name, zlib-compressed."""
        file = ctypes.c_void_p()
        flags = MPQ_FILE_COMPRESS | MPQ_FILE_REPLACEEXISTING
        if not self.lib.SFileCreateFile(self.handle, name.encode(), 0, len(data), 0, flags, ctypes.byref(file)):
            raise OSError(f"could not add {name} to the map (error {_error()})")
        if data and not self.lib.SFileWriteFile(file, data, len(data), MPQ_COMPRESSION_ZLIB):
            raise OSError(f"could not write {name} into the map (error {_error()})")
        if not self.lib.SFileFinishFile(file):
            raise OSError(f"could not finish {name} in the map (error {_error()})")
