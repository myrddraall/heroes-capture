"""Builds dist/heroes-capture.exe with PyInstaller (one file), at the release version.

    python tools/build_exe.py

The version comes from PROJECT_VERSION, which git-flow sets for `github.actions.build`
(0.0.0-dev without it). It goes into the executable's version resource as the ProductVersion
string, which git-flow's `executable` artifact reads to refuse a stale build, and into the
program itself for `--version`. Windows only: PyInstaller can't cross-compile.
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
ENTRY = PACKAGE / "release-stub" / "heroes_capture_stub.py"
NAME = "heroes-capture"
PYINSTALLER = "pyinstaller>=6.10,<7"


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


def main() -> None:
    version = os.environ.get("PROJECT_VERSION") or "0.0.0-dev"
    print(f"building {NAME}.exe {version}")
    for folder in (BUILD, DIST):
        shutil.rmtree(folder, ignore_errors=True)
    BUILD.mkdir()
    (BUILD / "version_info.txt").write_text(version_resource(version), encoding="utf-8")
    (BUILD / "_version.py").write_text(f"VERSION = {version!r}\n", encoding="utf-8")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", PYINSTALLER], check=True)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm", "--name", NAME,
         "--version-file", str(BUILD / "version_info.txt"), "--paths", str(BUILD),
         "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
         str(ENTRY)],
        check=True,
    )
    print(f"-> {DIST / (NAME + '.exe')}")


if __name__ == "__main__":
    main()
