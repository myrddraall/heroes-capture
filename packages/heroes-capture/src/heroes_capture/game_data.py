"""The game's own data, read at runtime: the install, its CASC storage (or Blizzard's CDN where no
game is installed), the battleground maps, the tilesets and light sets, and model files. Nothing
here is generated ahead or checked in, so a game update needs no new release of this tool.
"""

import difflib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .casclib import Storage
from .runlog import log
from .stormlib import Archive


def cache_dir() -> Path:
    """Where downloads and indexes are kept: %LOCALAPPDATA%\\heroes-capture\\cache (on Windows)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".cache")
    return Path(base) / "heroes-capture" / "cache"

SKY_MODELS = "mods\\heroes.stormmod\\base.stormassets\\assets\\skyboxes"
GAMESTRINGS = "enus.stormdata/localizeddata/gamestrings.txt"




# ------------------------------------------------------------------------------------------------
# The install
# ------------------------------------------------------------------------------------------------


def is_install(folder: Path) -> bool:
    """A Heroes of the Storm install: its storage's .build.info and the launcher the capture uses."""
    return (folder / ".build.info").is_file() and (folder / "Support64" / "HeroesSwitcher_x64.exe").is_file()


def _registry_locations() -> list[Path]:
    """Install folders from the Windows uninstall entries (Battle.net registers each game)."""
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                      (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")):
        try:
            with winreg.OpenKey(root, key) as uninstall:
                for i in range(winreg.QueryInfoKey(uninstall)[0]):
                    try:
                        with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, i)) as entry:
                            if winreg.QueryValueEx(entry, "DisplayName")[0] != "Heroes of the Storm":
                                continue
                            found.append(Path(str(winreg.QueryValueEx(entry, "InstallLocation")[0]).strip('"')))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def _battlenet_locations() -> list[Path]:
    """Install folders named in the Battle.net app's product database (paths stored as plain
    text in it): every folder it lists, checked by is_install."""
    db = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Battle.net" / "Agent" / "product.db"
    try:
        data = db.read_bytes()
    except OSError:
        return []
    return [Path(m.decode("utf-8", errors="replace")) for m in re.findall(rb"[A-Za-z]:[\\/][^\x00-\x1f\"<>|?*]{2,240}", data)]


def find_install() -> Path | None:
    """The installed game's folder: from the uninstall entries, the Battle.net app's product
    database, or the usual places; None when there is none (not on Windows, or not installed)."""
    candidates = _registry_locations() + _battlenet_locations()
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            candidates.append(Path(base) / "Heroes of the Storm")
    for drive in "CDEFG":
        candidates += [Path(f"{drive}:/Heroes of the Storm"), Path(f"{drive}:/Games/Heroes of the Storm")]
    for folder in candidates:
        try:
            if is_install(folder):
                return folder
        except OSError:
            continue
    return None


def open_storage(install: Path | None) -> Storage:
    """The install's storage, or Blizzard's CDN (cached) when there is no install."""
    if install:
        return Storage.local(install)
    log("no Heroes of the Storm install found: reading the game's data from Blizzard's CDN")
    return Storage.online(cache_dir() / "casc")


# ------------------------------------------------------------------------------------------------
# Maps
# ------------------------------------------------------------------------------------------------


CATEGORIES = ("Battleground", "Arena", "Brawl", "Other")


def map_category(dependencies: list[str]) -> str:
    """A map's category, from the mods its DocumentHeader names: Arena (heroesbrawlmods'
    arenamodemods), Brawl (the rest of heroesbrawlmods), Battleground (battlegroundmapmods; the
    game's data doesn't tell the Versus AI / Quick Match / Storm League pool from the
    custom-game-only maps) and Other (the sandboxes, which add sandbox-ext to a battleground, and
    anything else)."""
    paths = [d.replace("\\", "/").lower() for d in dependencies]
    if any("heroesbrawlmods/arenamodemods/" in d for d in paths):
        return "Arena"
    if any("heroesbrawlmods/" in d for d in paths):
        return "Brawl"
    if any("sandbox" in d for d in paths):
        return "Other"
    if any("battlegroundmapmods/" in d for d in paths):
        return "Battleground"
    return "Other"


def _dependencies(header: bytes) -> list[str]:
    """The mods a DocumentHeader names (its file: dependency paths)."""
    return [d.decode("utf-8", errors="replace") for d in re.findall(rb"file:([\x20-\x7e]+?)\x00", header)]


def map_index(storage: Storage) -> dict[str, dict]:
    """Map name (as the game shows it) -> {file: its .s2ma file in the storage, category}.
    Battleground maps are .s2ma archives under content-hash names in the storage's depot cache;
    each is opened to tell maps (a map script) from mods, and to read its name and the mods it
    builds on. The names are hashes of the contents, so their list changes when any map does: the
    index is cached under a hash of that list."""
    names = sorted(storage.find("*.s2ma"))
    key = hashlib.sha256("\n".join(["v2", *names]).encode()).hexdigest()[:16]
    cached = cache_dir() / f"maps-{key}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    log(f"indexing the game's maps ({len(names)} depot files, once per game update) ...")
    index = {}
    with tempfile.TemporaryDirectory() as folder:
        for name in names:
            data = storage.read(name)
            if not data or data[:4] != b"MPQ\x1a":
                continue  # text depot files
            path = Path(folder) / "depot.s2ma"
            path.write_bytes(data)
            with Archive(path) as archive:
                if not archive.has("mapscript.galaxy"):
                    continue  # a mod
                strings = archive.read_text(GAMESTRINGS) or ""
                header = archive.read("DocumentHeader") if archive.has("DocumentHeader") else b""
            title = re.search(r"DocInfo/Name=(.+)", strings)
            if title:
                index[title.group(1).strip()] = {"file": name, "category": map_category(_dependencies(header))}
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")
    return index


def folder_maps(storage: Storage) -> list[str]:
    """The maps kept as unpacked folders inside the game's own mod rather than as .s2ma archives
    (Try Me Mode and the tutorials), by name. The capture builds on a map archive, so these can't be
    rendered."""
    titles = []
    for script in storage.find("*mapscript.galaxy"):
        folder = script.rsplit("\\", 1)[0]
        strings = (storage.read(f"{folder}\\{GAMESTRINGS}") or b"").decode("utf-8", errors="replace")
        title = re.search(r"DocInfo/Name=(.+)", strings)
        titles.append(title.group(1).strip() if title else Path(folder.replace("\\", "/")).stem)
    return titles


def _plain(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def find_map(storage: Storage, map_name: str) -> str:
    """A map the game has and the capture can render, by the name the game shows (case and
    punctuation aside): its name as the game spells it. Otherwise stops, saying which (an
    unsupported map), or naming the closest names."""
    index = map_index(storage)
    match = next((name for name in index if _plain(name) == _plain(map_name)), None)
    if match:
        return match
    unsupported = next((name for name in folder_maps(storage) if _plain(name) == _plain(map_name)), None)
    if unsupported:
        raise SystemExit(f"{unsupported} is unsupported: the game keeps it as a folder, not a map archive, and the capture builds on an archive")
    by_plain = {_plain(name): name for name in index}
    close = [by_plain[p] for p in difflib.get_close_matches(_plain(map_name), list(by_plain), n=3, cutoff=0.6)]
    hint = f"did you mean {' or '.join(close)}?" if close else "heroes-capture map list shows them"
    raise SystemExit(f'no map named "{map_name}" in the game; {hint}')


def map_file(storage: Storage, map_name: str) -> tuple[str, bytes]:
    """A battleground's .stormmap, by the name the game shows (case and punctuation aside): its
    name as the game spells it, and its bytes."""
    match = find_map(storage, map_name)
    return match, storage.read(map_index(storage)[match]["file"])


# ------------------------------------------------------------------------------------------------
# Tilesets, light sets and models
# ------------------------------------------------------------------------------------------------


def _order(flat_name: str) -> int:
    """Base mods first, so the battleground mods that override them come later."""
    for rank, pattern in enumerate((r"mods__core", r"mods__heroesdata", r"mods__heroes\.stormmod")):
        if re.search(pattern, flat_name, re.I):
            return rank
    return 3


def light_sets(storage: Storage) -> dict:
    """For every tileset its light set and skybox, for every light set its main ("Key") light's
    direction: {terrains, lights}, from every mod's GameData/TerrainData.xml and LightData.xml. A
    field set later replaces one set earlier; missing fields come from a definition's parent."""
    from .light_data import parse_lights, parse_terrains

    files = []
    for name in storage.find("*TerrainData.xml") + storage.find("*LightData.xml"):
        flat = name.replace("\\", "__")
        if re.search(r"(terraindata|lightdata)\.xml$", flat, re.I):
            files.append((name, flat))
    files.sort(key=lambda f: (_order(f[1]), f[1].lower(), f[1]))
    terrains: dict = {}
    lights: dict = {}
    for name, flat in files:
        xml = storage.read(name).decode("utf-8", errors="replace")
        if re.search(r"terraindata\.xml$", flat, re.I):
            parse_terrains(xml, terrains)
        else:
            parse_lights(xml, lights)
    return {"terrains": terrains, "lights": lights}


CMODEL = re.compile(r'<CModel\b([^>]*)\bid="([^"]+)"')


def model_ids_in(text: str) -> list[str]:
    """The models (CModel) a catalog file defines; abstract ones (default="1") left out."""
    return [m.group(2) for m in CMODEL.finditer(text) if 'default="1"' not in m.group(1)]


def map_model_ids(storage: Storage, map_mods: list[str]) -> list[str]:
    """Every model (CModel) a map's data can use: those of the core mod and the shared Heroes data
    (every map has them), and of the mods the map names in its DocumentInfo, with the mods each of
    those names in turn. From every catalog file of their game data, not only ModelData.xml: many
    models are defined beside what uses them (the loot banner sconce's in LootBoxData.xml, a hero's
    in its own data). Abstract ones (default="1") left out."""
    wanted, queue = set(), [m.lower() for m in map_mods]
    while queue:
        mod = queue.pop()
        if mod in wanted:
            continue
        wanted.add(mod)
        for info in storage.find(f"*\\{mod}\\documentinfo"):
            queue += [m.decode().lower() for m in re.findall(rb"([A-Za-z0-9_]+\.stormmod)", storage.read(info) or b"")]
    ids = []
    for name in storage.find("*.xml"):
        low = name.lower()
        if "\\base.stormdata\\gamedata\\" not in low:
            continue
        if not (low.startswith(("mods\\core.stormmod\\", "mods\\heroesdata.stormmod\\")) or any(f"\\{mod}\\" in low for mod in wanted)):
            continue
        data = storage.read(name) or b""
        if b"<CModel" not in data:
            continue
        ids += model_ids_in(data.decode("utf-8", errors="replace"))
    return sorted(set(ids))


def sky_model_file(storage: Storage, file_name: str) -> bytes | None:
    """A skybox model's .m3 (as named in sky.py's PARALLAX_KEYS), or None."""
    stem = file_name.rsplit(".", 1)[0].lower()
    return storage.read(f"{SKY_MODELS}\\{stem}\\{file_name.lower()}")
