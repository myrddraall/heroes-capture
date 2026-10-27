"""Preparing any map for heroes-capture's script: what of its file every preparation reads (its name
and .stormmap, its size and camera bounds, the libraries and models it uses), the identity the
status strip reports, and the assembled script put into its MapScript. capture/prepare.py prepares
a map for capture with these."""

import re
import struct
import time
from pathlib import Path

from heroes_capture.common import game_data
from heroes_capture.common.dds import solid_dds
from heroes_capture.common.stormlib import Archive

# The status strip's cells are a white texture tinted per cell (script.galaxy).
STRIP_TEXTURE = "Assets\\Textures\\HrsWhite.dds"


def resolve_map(map_: str, storage) -> tuple[str, bytes]:
    """The map's name and its .stormmap: a path to one, or a map the game has, by name."""
    if map_.lower().endswith(".stormmap"):
        path = Path(map_)
        if not path.exists():
            raise SystemExit(f"no such file: {map_}")
        return path.name[: -len(".stormmap")], path.read_bytes()
    return game_data.map_file(storage, map_)


def model_ids(storage, target: Path) -> tuple[list[str], list[str]]:
    """Every model the map can use, from the mods it names (its DocumentInfo) and theirs and from
    every catalog file of its own game data; and those mods."""
    with Archive(target) as archive:
        mods = re.findall(r"([A-Za-z0-9_]+\.stormmod)", archive.read_text("DocumentInfo") or "")
        listed = (archive.read_text("(listfile)") or "").splitlines()
        own = [i for name in listed if name.strip().lower().startswith("base.stormdata\\gamedata\\") and name.strip().lower().endswith(".xml")
               for i in game_data.model_ids_in(archive.read_text(name.strip()) or "")]
    return sorted(set(game_data.map_model_ids(storage, mods)) | set(own)), mods


def read_map_info(buf: bytes) -> dict:
    """MapInfo: map size at bytes 16 and 20, and the camera bounds (left, bottom, right, top) as
    four uint32 right after a string. The bounds' offset moves with the strings before them, so
    take the first aligned-to-a-string quadruple that fits the map; this matches all 35 current
    battlegrounds and brawls."""
    w, h = struct.unpack_from("<II", buf, 16)
    for o in range(32, len(buf) - 15):
        if buf[o - 1] != 0:
            continue
        l, b, r, t = struct.unpack_from("<IIII", buf, o)
        if l < r <= w and b < t <= h and r - l >= w / 3 and t - b >= h / 3:
            return {"width": w, "height": h, "bounds": {"left": l, "bottom": b, "right": r, "top": t}, "boundsOffset": o}
    return {"width": w, "height": h, "bounds": {"left": 0, "bottom": 0, "right": w, "top": h}, "boundsOffset": None}


def includes(map_script: str) -> list[str]:
    """The libraries the map's script includes, by file name (LibMHmu, LibAREN, ...)."""
    return [m.split("/")[-1] for m in re.findall(r'^include "([^"]+)"', map_script, re.M)]


# The two teams' computer players (Order's, Chaos's), whose colours a doodad can be drawn in.
TEAM_PLAYERS = ("11", "12")


def team_coloured(objects: str) -> list[list[float]]:
    """The cells of the doodads the map draws in a team's colours (Objects: TeamColor of a team's
    computer player; Battlefield of Eternity's Heaven lamps, crystal beds and outer walls): what
    changes, besides the units, when our player changes side."""
    cells = []
    for attrs in re.findall(r"<ObjectDoodad ([^>]*)>", objects):
        found = dict(re.findall(r'(\w+)="([^"]*)"', attrs))
        if found.get("TeamColor") in TEAM_PLAYERS:
            x, y = (float(v) for v in found["Position"].split(",")[:2])
            cells.append([round(x, 2), round(y, 2)])
    return cells


def preparation_id(id_: str) -> int:
    """The map's identity in the status strip: a 16-bit hash of this preparation (map, structures
    and time), so the capture can tell its own map from one left running by an earlier run."""
    h = 2166136261
    for ch in f"{id_} {int(time.time() * 1000)}":
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return (h ^ (h >> 16)) & 0xFFFF


def inject(archive: Archive, map_script: str, script: str) -> None:
    """The assembled script (script.assemble) put into the map's MapScript: Galaxy is single-pass,
    so its functions go before InitMap and the call to hrsCap_Init at InitMap's end; and the status
    strip's texture."""
    init = map_script.rfind("void InitMap () {")
    if init < 0:
        raise SystemExit("MapScript.galaxy has no InitMap; not a battleground script?")
    eol = "\r\n" if "\r\n" in map_script else "\n"
    close = map_script.find(f"{eol}}}", init)
    patched = map_script[:init] + script.replace("\n", eol) + map_script[init:close] + f"{eol}    hrsCap_Init();" + map_script[close:]
    archive.write("MapScript.galaxy", patched.encode("utf-8"))
    archive.write(STRIP_TEXTURE, solid_dds((255, 255, 255), 64, 64))
