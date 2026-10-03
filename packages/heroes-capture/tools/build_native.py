"""Fills native/ with the libraries heroes-capture loads (not in git):

    python tools/build_native.py            libstorm.so and libcasc.so, built for this machine
    python tools/build_native.py --windows  StormLib.dll and CascLib.dll, for Windows

StormLib (MPQ archives) is pinned to v9.40: on Windows its own release DLL (x64), checked against
the release's published SHA-256; elsewhere built from that release. CascLib (the game's CASC
storage) is pinned to 3.0 and publishes no binaries, so it is always built: for Windows a Unicode
DLL, cross-compiled with Zig's C compiler, or natively when run on Windows.

Needs git and CMake, and for --windows off Windows the ziglang package: pip install cmake ziglang.
"""

import hashlib
import io
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

NATIVE = Path(__file__).resolve().parent.parent / "native"
STORMLIB = ("https://github.com/ladislav-zezula/StormLib.git", "v9.40")
STORMLIB_DLL = "https://github.com/ladislav-zezula/StormLib/releases/download/v9.40/stormlib_dll.zip"
STORMLIB_DLL_SHA256 = "b2c9635e7b63edee1bd7c82e7dc180d739f3accb2b8994804c7774e464ce89ae"  # v9.40's release.sha256
CASCLIB = ("https://github.com/ladislav-zezula/CascLib.git", "3.0")


def clone(repo: tuple[str, str], into: Path) -> Path:
    subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", repo[1], repo[0], str(into)], check=True)
    return into


def cmake(source: Path, build: Path, options: list[str]) -> None:
    subprocess.run(["cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release", *options],
                   check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["cmake", "--build", str(build), "--config", "Release", "-j8"], check=True, stdout=subprocess.DEVNULL)


def zig_toolchain(folder: Path) -> list[str]:
    """CMake options that cross-compile for 64-bit Windows with Zig (CMake wants one executable
    per tool, so each is a small script)."""
    folder.mkdir()
    options = ["-DCMAKE_SYSTEM_NAME=Windows"]
    for tool, variable in (("cc", "C_COMPILER"), ("c++", "CXX_COMPILER"), ("ar", "AR"), ("ranlib", "RANLIB"), ("rc", "RC_COMPILER")):
        script = folder / tool
        target = " -target x86_64-windows-gnu" if tool in ("cc", "c++") else ""
        script.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m ziglang {tool}{target} "$@"\n')
        script.chmod(0o755)
        options.append(f"-DCMAKE_{variable}={script}")
    return options


def download(url: str) -> bytes:
    """A file over HTTPS, a few tries (GitHub's release downloads sometimes answer 503)."""
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "heroes-capture build_native"})
            with urllib.request.urlopen(request) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError


def stormlib_dll() -> None:
    data = download(STORMLIB_DLL)
    if hashlib.sha256(data).hexdigest() != STORMLIB_DLL_SHA256:
        raise SystemExit(f"{STORMLIB_DLL} doesn't match StormLib v9.40's published checksum")
    (NATIVE / "StormLib.dll").write_bytes(zipfile.ZipFile(io.BytesIO(data)).read("x64/StormLib.dll"))


def main() -> None:
    windows = "--windows" in sys.argv[1:]
    cross = windows and sys.platform != "win32"
    NATIVE.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if windows:
            stormlib_dll()
        else:
            cmake(clone(STORMLIB, work / "stormlib"), work / "stormlib-build", ["-DBUILD_SHARED_LIBS=ON"])
            shutil.copyfile(next((work / "stormlib-build").glob("libstorm.so.*.*.*")), NATIVE / "libstorm.so")
        options = ["-DCASC_BUILD_SHARED_LIB=ON",
                   "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"]  # CascLib asks for CMake 2.8, which CMake 4 refuses
        if windows:
            options += ["-DCASC_UNICODE=ON"]  # wide paths, as casclib.py passes them on Windows
        if cross:
            options += zig_toolchain(work / "zig")
            options += ["-DCMAKE_SHARED_LINKER_FLAGS=-lws2_32"]  # Visual Studio links the sockets library by a pragma
        cmake(clone(CASCLIB, work / "casclib"), work / "casclib-build", options)
        pattern = "**/*CascLib*.dll" if windows else "libcasc.so.*.*.*"
        shutil.copyfile(next((work / "casclib-build").glob(pattern)), NATIVE / ("CascLib.dll" if windows else "libcasc.so"))
    print(f"native/: {', '.join(sorted(p.name for p in NATIVE.iterdir()))}")


if __name__ == "__main__":
    main()
