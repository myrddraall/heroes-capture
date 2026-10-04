"""Builds dist/heroes-capture.exe (one file, PyInstaller) at the release version.

    python tools/build_exe.py

The version comes from PROJECT_VERSION, which git-flow sets for `github.actions.build`
(0.0.0-dev without it). It goes into the program (heroes_capture/_version.py, for --version) and
into the executable's version resource as the ProductVersion string, which git-flow's
`executable` artifact reads to refuse a stale build. The executable bundles Python, the package
with its data files (the Galaxy template, the menu templates, the viewer page), its dependencies,
and StormLib and CascLib from native/ (built first by tools/build_native.py when missing).

On Windows it builds heroes-capture.exe; elsewhere the same program as a native binary, which is
how the build is checked without Windows (PyInstaller can't cross-compile).
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent
BUILD = PACKAGE / "build"
DIST = PACKAGE / "dist"
VERSION_MODULE = PACKAGE / "src" / "heroes_capture" / "_version.py"
NAME = "heroes-capture"
PYINSTALLER = "pyinstaller>=6.10,<7"
LIBRARIES = ("StormLib.dll", "CascLib.dll") if sys.platform == "win32" else ("libstorm.so", "libcasc.so")


def numeric(version: str) -> tuple[int, int, int, int]:
    """The four numbers Windows' fixed version fields hold: major.minor.patch.0 (the
    prerelease part, which they can't hold, is in the ProductVersion string)."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise SystemExit(f"not a version: {version!r}")
    return (*(int(part) for part in match.groups()), 0)


def version_resource(version: str) -> str:
    """PyInstaller's --version-file contents."""
    four = numeric(version)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={four}, prodvers={four}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Myrddraall'),
      StringStruct('FileDescription', 'heroes-capture'),
      StringStruct('FileVersion', {version!r}),
      StringStruct('InternalName', {NAME!r}),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', 'heroes-capture'),
      StringStruct('ProductVersion', {version!r})])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def run(*command: str) -> None:
    subprocess.run(command, check=True, cwd=PACKAGE)


def main() -> None:
    version = os.environ.get("PROJECT_VERSION") or "0.0.0-dev"
    print(f"building {NAME} {version}")
    for folder in (BUILD, DIST):
        shutil.rmtree(folder, ignore_errors=True)
    BUILD.mkdir()
    if not all((PACKAGE / "native" / name).exists() for name in LIBRARIES):
        run(sys.executable, "tools/build_native.py", *(["--windows"] if sys.platform == "win32" else []))
    VERSION_MODULE.write_text(f'VERSION = "{version}"\n', encoding="utf-8")
    (BUILD / "version_info.txt").write_text(version_resource(version), encoding="utf-8")
    (BUILD / "entry.py").write_text("from heroes_capture.cli import main\n\nmain()\n", encoding="utf-8")
    run(sys.executable, "-m", "pip", "install", "--quiet", PYINSTALLER, ".")
    binaries = [arg for name in LIBRARIES for arg in ("--add-binary", f"{PACKAGE / 'native' / name}{os.pathsep}.")]
    run(sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm", "--console", "--name", NAME,
        "--version-file", str(BUILD / "version_info.txt"),
        "--collect-data", "heroes_capture",  # the Galaxy template, menu templates, viewer page, opening timers
        "--collect-submodules", "heroes_capture",  # imported lazily by the command
        "--hidden-import", "_libvips",  # pyvips' compiled binding, imported inside a try
        *binaries,
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
        str(BUILD / "entry.py"))
    built = next(DIST.iterdir())
    print(f"-> {built} ({built.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
