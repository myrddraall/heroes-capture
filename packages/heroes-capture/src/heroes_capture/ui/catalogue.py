"""Which of the game's textures belong to its interface, and what its data says about each.

A texture is the interface's when any of these holds:
  - an interface layout (.stormlayout and the like) draws it: a frame's Texture, ImageMaskTexture,
    AlphaMaskTexture, HitTestTexture, or icon, by file or by its asset name ("@UI/<name>");
  - the asset table names it for the interface (GameData/Assets.txt: "UI/<name>=Assets\\Textures\\...");
  - a catalog entry shows it as a picture (GameData: a button's Icon, a hero's Portrait, a map's
    LoadingScreenImage, ...);
  - its name carries one of the interface's own prefixes (storm_ui_, ui_, hud_, ...).

What is kept for each, to tell one from another: its asset names, the layout frames that draw it
(with the frame's texture type: a border or nine-slice piece) and the catalog entries that show it,
with the entry's English name where the game's strings give one.
"""

import re
import xml.etree.ElementTree as ET
from collections import defaultdict

from heroes_capture.common.casclib import Storage
from heroes_capture.common.game_data import asset_aliases
from heroes_capture.common.runlog import log

# The interface's own name prefixes. Not storm_ui-editoricon (the map editor's) and not the sprays
# and banners (collectibles drawn in the world, not the interface).
PREFIXES = ("storm_ui_", "ui_", "hud_", "storm_heroselect_", "storm_lootui_", "storm_btn")
# The layout fields that draw a texture (the others with an "@UI/" value are text and tooltips).
LAYOUT_FIELD = re.compile(r"\w*(?:Texture|Icon)$")
# The catalog fields that show a texture as a picture in the interface (not a model's or terrain's).
DATA_FIELDS = {
    "AlertIcon", "CollectionIcon", "DraftIntroImage", "DraftScreenLargeImage", "DraftScreenLargeImageBackground",
    "DraftScreenPortrait", "DraftScreenPortraitBackground", "HeroIcon", "Icon", "Image", "InfoIcon", "LargeImage",
    "LeaderboardImage", "LoadingScreenImage", "MinimapIcon", "MinimapIconAlly", "MinimapIconBackground",
    "MinimapIconEnemy", "PartyFrameImage", "PartyPanelButtonImage", "Portrait", "PortraitPaired", "ScoreScreenImage",
    "SelectScreenButtonImage", "UnitIcon", "UnitIconMultiple", "UniverseIcon", "VariationIcon",
}
ASSETS = re.compile(r"\\(base|enus)\.stormassets\\", re.I)  # the shared textures and the English ones
MAX_USES = 12  # layout uses and catalog entries kept per texture (a white pixel is drawn by hundreds)


def file_name(path: str) -> str:
    """A texture's file name, lower case, from any path to it ("Assets\\Textures\\Foo.dds" -> foo.dds)."""
    return path.replace("/", "\\").rsplit("\\", 1)[-1].lower()


def textures(storage: Storage) -> dict[str, str]:
    """Every texture by file name -> its path in the storage: the English one where the game has one
    (it overrides the shared one in an English client), and the Heroes mod's before the core's."""
    found = [n for n in storage.find("*.dds") if ASSETS.search(n)]
    found.sort(key=lambda n: ("\\enus.stormassets\\" not in n.lower(), "heroes.stormmod" not in n.lower(), n.lower()))
    paths: dict[str, str] = {}
    for name in found:
        paths.setdefault(file_name(name), name)
    return paths


def _frames(element: ET.Element, path: list[str], uses: list[tuple[str, ET.Element, ET.Element]]) -> None:
    """The texture fields under a frame, with the frame's path (its name and its parents')."""
    for child in element:
        if child.tag == "Frame":
            _frames(child, [*path, child.get("name", "?")], uses)
        elif LAYOUT_FIELD.match(child.tag) and child.get("val"):
            uses.append(("/".join(path), element, child))
        else:
            _frames(child, path, uses)


def layout_uses(storage: Storage, aliases: dict[str, str]) -> dict[str, list[dict]]:
    """Texture file name -> where the interface's layouts draw it: {layout, frame, field, type?, tiled?}."""
    found: dict[str, list[dict]] = defaultdict(list)
    names = [n for n in storage.find("*.storm*") if n.lower().endswith((".stormlayout", ".stormstyle", ".stormcomponents"))
             and "\\base.stormdata\\" in n.lower()]
    for name in names:
        try:
            root = ET.fromstring((storage.read(name) or b"").decode("utf-8-sig", errors="replace"))
        except ET.ParseError:
            log(f"interface layout {name} isn't readable XML: left out")
            continue
        uses: list = []
        _frames(root, [], uses)
        layout = file_name(name).rsplit(".", 1)[0]
        for frame, owner, field in uses:
            value = field.get("val", "")
            target = aliases.get(value[1:]) if value.startswith("@") else value
            if not target or not target.lower().endswith((".dds", ".tga")):
                continue
            use = {"layout": layout, "frame": frame, "field": field.tag}
            kind = owner.find("TextureType")
            if kind is not None and kind.get("val"):
                use["type"] = kind.get("val")
            tiled = owner.find("Tiled")
            if tiled is not None and tiled.get("val") == "true":
                use["tiled"] = True
            found[file_name(target)].append(use)
    return found


def english_names(storage: Storage) -> dict[str, list[tuple[str, str]]]:
    """Entry id -> [(kind, name)] from every mod's English strings ("Button/Name/<id>=<name>"),
    their markup taken out."""
    names: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for path in storage.find("*\\enus.stormdata\\localizeddata\\gamestrings.txt"):
        for line in (storage.read(path) or b"").decode("utf-8-sig", errors="replace").splitlines():
            key, sep, text = line.partition("=")
            parts = key.split("/")
            if sep and len(parts) == 3 and parts[1] == "Name":
                names[parts[2]].append((parts[0], re.sub(r"<[^>]*>", "", text).strip()))
    return names


def data_uses(storage: Storage) -> dict[str, list[dict]]:
    """Texture file name -> the catalog entries that show it as a picture: {entry, field, name?}.
    The name is the entry's English one: the string whose kind the entry's class begins with
    (CButton ButcherEnraged -> "Button/Name/ButcherEnraged")."""
    names = english_names(storage)
    found: dict[str, list[dict]] = defaultdict(list)
    for path in storage.find("*\\gamedata\\*.xml"):
        if "\\base.stormdata\\" not in path.lower():
            continue
        try:
            root = ET.fromstring((storage.read(path) or b"").decode("utf-8-sig", errors="replace"))
        except ET.ParseError:
            log(f"game data {path} isn't readable XML: left out")
            continue
        for entry in root:
            entry_id = entry.get("id")
            if not entry_id:
                continue
            for field in entry.iter():
                if field.tag not in DATA_FIELDS:
                    continue
                value = field.get("value", "")
                if not value.lower().endswith((".dds", ".tga")):
                    continue
                use = {"entry": f"{entry.tag} {entry_id}", "field": field.tag}
                english = [text for kind, text in names.get(entry_id, []) if entry.tag[1:].startswith(kind)]
                if english:
                    use["name"] = english[0]
                uses = found[file_name(value)]
                if use not in uses:
                    uses.append(use)
    return found


def catalogue(storage: Storage) -> dict[str, dict]:
    """The interface's textures, by file name: {path, aliases?, layouts?, layoutsTotal?, data?, dataTotal?}
    (the totals when there are more uses than are kept)."""
    paths = textures(storage)
    table = asset_aliases(storage)
    aliases: dict[str, list[str]] = defaultdict(list)
    for name, target in table.items():
        if name.startswith("UI/") and target.lower().endswith(".dds"):
            aliases[file_name(target)].append(name)
    layouts = layout_uses(storage, table)
    data = data_uses(storage)
    entries = {}
    for name, path in sorted(paths.items()):
        if not (name.startswith(PREFIXES) or name in aliases or name in layouts or name in data):
            continue
        entry: dict = {"path": path}
        if name in aliases:
            entry["aliases"] = sorted(aliases[name])
        for key, uses in (("layouts", layouts.get(name)), ("data", data.get(name))):
            if uses:
                entry[key] = uses[:MAX_USES]
                if len(uses) > MAX_USES:
                    entry[f"{key}Total"] = len(uses)
        entries[name] = entry
    return entries
