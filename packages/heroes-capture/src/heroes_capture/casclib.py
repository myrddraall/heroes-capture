"""The game's CASC storage through CascLib (3.0), by ctypes: the local install's, or Blizzard's CDN
(downloaded on demand into a cache) where no game is installed. CascLib publishes no binaries:
tools/build_native.py builds it (on Windows a Unicode DLL); native.py says where it is.
"""

import ctypes
import os
import sys
from pathlib import Path

from .native import library

CASC_OPEN_BY_NAME = 0
CASC_LOCALE_ALL = 0xFFFFFFFF
FIND_DATA_SIZE = 8192  # bigger than CASC_FIND_DATA on any platform (MAX_PATH is 260 on Windows, 1024 elsewhere)
INVALID_HANDLE = (ctypes.c_void_p(-1).value, None, 0)
PRODUCT = "hero"
STORAGE_PRODUCT = 4  # CascStorageProduct: the product's code name and build number


class _StorageProduct(ctypes.Structure):
    _fields_ = [("code_name", ctypes.c_char * 0x1C), ("build", ctypes.c_uint32)]

_lib = None


def _load():
    global _lib
    if _lib is not None:
        return _lib
    path = library("CascLib.dll", "libcasc.so")
    if sys.platform == "win32":
        lib = ctypes.WinDLL(str(path))
        path_type = ctypes.c_wchar_p  # built Unicode: storage paths are wide
    else:
        lib = ctypes.CDLL(str(path))
        path_type = ctypes.c_char_p
    handle, dword, name = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p
    signatures = {
        "CascOpenStorage": ([path_type, dword, ctypes.POINTER(handle)], ctypes.c_bool),
        "CascOpenOnlineStorage": ([path_type, dword, ctypes.POINTER(handle)], ctypes.c_bool),
        "CascCloseStorage": ([handle], ctypes.c_bool),
        "CascOpenFile": ([handle, ctypes.c_void_p, dword, dword, ctypes.POINTER(handle)], ctypes.c_bool),
        "CascGetFileSize64": ([handle, ctypes.POINTER(ctypes.c_uint64)], ctypes.c_bool),
        "CascReadFile": ([handle, ctypes.c_void_p, dword, ctypes.POINTER(dword)], ctypes.c_bool),
        "CascCloseFile": ([handle], ctypes.c_bool),
        "CascFindFirstFile": ([handle, name, ctypes.c_void_p, path_type], handle),
        "CascFindNextFile": ([handle, ctypes.c_void_p], ctypes.c_bool),
        "CascFindClose": ([handle], ctypes.c_bool),
        "CascGetStorageInfo": ([handle, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], ctypes.c_bool),
        "GetCascError": ([], dword),
    }
    for function, (args, result) in signatures.items():
        getattr(lib, function).argtypes = args
        getattr(lib, function).restype = result
    lib.path_type = path_type
    _lib = lib
    return lib


class Storage:
    """An opened CASC storage; a context manager. File names are the game's paths, separators
    as backslashes or slashes ("mods\\heroesdata.stormmod\\base.stormdata\\GameData\\LightData.xml")."""

    def __init__(self, handle, description: str):
        self.lib = _load()
        self.handle = handle
        self.description = description

    @classmethod
    def local(cls, install: Path) -> "Storage":
        """The installed game's storage (the folder with .build.info)."""
        lib = _load()
        handle = ctypes.c_void_p()
        # The folder alone (its .build.info names one product), else with the product's code.
        for params in (str(install), f"{install}*{PRODUCT}"):
            if lib.CascOpenStorage(cls._path(lib, params), CASC_LOCALE_ALL, ctypes.byref(handle)):
                return cls(handle, f"the install in {install}")
        raise OSError(f"could not open the game's storage in {install} (CascLib error {lib.GetCascError()})")

    @classmethod
    def online(cls, cache: Path, region: str = "us") -> "Storage":
        """Blizzard's CDN, files downloaded on demand into `cache`."""
        lib = _load()
        cache.mkdir(parents=True, exist_ok=True)
        handle = ctypes.c_void_p()
        if not lib.CascOpenOnlineStorage(cls._path(lib, f"{cache}*{PRODUCT}*{region}"), CASC_LOCALE_ALL, ctypes.byref(handle)):
            raise OSError(f"could not open the game's storage on Blizzard's CDN (CascLib error {lib.GetCascError()})")
        return cls(handle, "Blizzard's CDN")

    @staticmethod
    def _path(lib, text: str):
        return text if lib.path_type is ctypes.c_wchar_p else os.fsencode(text)

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self.handle:
            self.lib.CascCloseStorage(self.handle)
            self.handle = ctypes.c_void_p()

    def build(self) -> int | None:
        """The game build the storage holds (its build number), or None if CascLib doesn't say."""
        info, needed = _StorageProduct(), ctypes.c_size_t()
        if not self.lib.CascGetStorageInfo(self.handle, STORAGE_PRODUCT, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(needed)):
            return None
        return int(info.build) or None

    def read(self, name: str) -> bytes | None:
        """A file's bytes, or None when the storage has no such file."""
        file = ctypes.c_void_p()
        if not self.lib.CascOpenFile(self.handle, name.encode(), 0, CASC_OPEN_BY_NAME, ctypes.byref(file)):
            return None
        try:
            size = ctypes.c_uint64()
            if not self.lib.CascGetFileSize64(file, ctypes.byref(size)):
                raise OSError(f"could not size {name} (CascLib error {self.lib.GetCascError()})")
            buffer = ctypes.create_string_buffer(size.value)
            got = ctypes.c_uint32()
            if size.value and not self.lib.CascReadFile(file, buffer, size.value, ctypes.byref(got)):
                raise OSError(f"could not read {name} (CascLib error {self.lib.GetCascError()})")
            return buffer.raw[: got.value]
        finally:
            self.lib.CascCloseFile(file)

    def find(self, mask: str) -> list[str]:
        """The names of the files matching a wildcard mask ("*TerrainData.xml")."""
        data = ctypes.create_string_buffer(FIND_DATA_SIZE)
        search = self.lib.CascFindFirstFile(self.handle, mask.encode(), data, None)
        if search in INVALID_HANDLE:
            return []
        names = []
        try:
            while True:
                names.append(data.value.decode("utf-8", errors="replace"))
                if not self.lib.CascFindNextFile(search, data):
                    break
        finally:
            self.lib.CascFindClose(search)
        return names
