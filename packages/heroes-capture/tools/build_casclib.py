"""Builds CascLib (the game's CASC storage; it publishes no binaries) from its 3.0 release into
native/: libcasc.so for this machine, or with --windows a 64-bit Unicode CascLib.dll,
cross-compiled with Zig's C compiler.

    python tools/build_casclib.py [--windows]

Needs git, CMake and (for --windows) the ziglang package: pip install cmake ziglang. On a Windows
runner (stage 4) CMake builds it natively instead.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RELEASE = "3.0"
REPO = "https://github.com/ladislav-zezula/CascLib.git"
NATIVE = Path(__file__).resolve().parent.parent / "native"


def zig_wrapper(folder: Path, tool: str) -> str:
    """A script that runs `zig <tool>` aimed at 64-bit Windows (CMake wants one executable)."""
    path = folder / tool
    target = " -target x86_64-windows-gnu" if tool in ("cc", "c++") else ""
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m ziglang {tool}{target} "$@"\n')
    path.chmod(0o755)
    return str(path)


def main() -> None:
    windows = "--windows" in sys.argv[1:]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", RELEASE, REPO, str(work / "src")], check=True)
        configure = ["cmake", "-S", str(work / "src"), "-B", str(work / "build"), "-DCMAKE_BUILD_TYPE=Release",
                     "-DCASC_BUILD_SHARED_LIB=ON",
                     "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"]  # CascLib asks for CMake 2.8, which CMake 4 refuses
        if windows:
            zig = work / "zig"
            zig.mkdir()
            configure += [
                "-DCMAKE_SYSTEM_NAME=Windows", f"-DCMAKE_C_COMPILER={zig_wrapper(zig, 'cc')}",
                f"-DCMAKE_CXX_COMPILER={zig_wrapper(zig, 'c++')}", f"-DCMAKE_AR={zig_wrapper(zig, 'ar')}",
                f"-DCMAKE_RANLIB={zig_wrapper(zig, 'ranlib')}", f"-DCMAKE_RC_COMPILER={zig_wrapper(zig, 'rc')}",
                "-DCASC_UNICODE=ON",  # wide paths, as casclib.py passes them on Windows
                "-DCMAKE_SHARED_LINKER_FLAGS=-lws2_32",  # Visual Studio links the sockets library by a pragma
            ]
        subprocess.run(configure, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["cmake", "--build", str(work / "build"), "-j8"], check=True, stdout=subprocess.DEVNULL)
        built = next((work / "build").glob("libCascLib.dll" if windows else "libcasc.so.*.*.*"))
        NATIVE.mkdir(exist_ok=True)
        out = NATIVE / ("CascLib.dll" if windows else "libcasc.so")
        shutil.copyfile(built, out)
        print(f"CascLib {RELEASE} -> {out}")


if __name__ == "__main__":
    main()
