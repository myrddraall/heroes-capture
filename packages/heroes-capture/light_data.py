"""The map's main light, for the lighting-refit look (see capture_script.galaxy): the map's
tileset (t3Terrain.xml) names a light set (CTerrain Lighting), and the light set's "Key"
directional light has a direction. Tileset and light-set definitions live in the game's data
(light-sets.json, built by generate_light_sets.py); a map can override either in its own
TerrainData.xml and LightData.xml.
"""

import copy
import math
import re


def tileset_of(t3_terrain: str) -> str | None:
    """The tileset a map's t3Terrain.xml names (<heightMap tileSet="...">), or None."""
    match = re.search(r'\btileSet="([^"]+)"', t3_terrain, re.I)
    return match.group(1) if match else None


def _entries(xml: str, tag: str) -> list[tuple[dict, str]]:
    """Each <tag ...>...</tag> or <tag .../>: its attributes and its body."""
    out = []
    for match in re.finditer(rf"<{tag}\s+([^>]*?)(/>|>([\s\S]*?)</{tag}>)", xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', match.group(1)))
        out.append((attrs, match.group(3) or ""))
    return out


def parse_terrains(xml: str, into: dict | None = None) -> dict:
    """CTerrain entries: id -> {parent?, lighting?, skybox?, parallax?, hideLowest?} (the
    FixedSkyboxModel, NonFixedSkyboxModel and HideLowestLevel fields; "" for a skybox cleared)."""
    into = {} if into is None else into
    for attrs, body in _entries(xml, "CTerrain"):
        if not attrs.get("id"):
            continue
        terrain = into.setdefault(attrs["id"], {})
        if attrs.get("parent"):
            terrain["parent"] = attrs["parent"]
        for field, pattern in (("lighting", r'<Lighting value="([^"]*)"'),
                               ("skybox", r'<FixedSkyboxModel value="([^"]*)"'),
                               ("parallax", r'<NonFixedSkyboxModel value="([^"]*)"')):
            match = re.search(pattern, body)
            if match:
                terrain[field] = match.group(1)
        match = re.search(r'<HideLowestLevel value="([^"]*)"', body)
        if match:
            terrain["hideLowest"] = match.group(1) == "1"
    return into


def _key_direction(xml: str) -> list[float] | None:
    # <DirectionalLight index="Key" ... Direction="x,y,z"/>, or with a child
    # <Direction value="x,y,z"/> or <Direction X=".." Y=".." Z=".."/>.
    match = re.search(r'<DirectionalLight index="Key"(\s[^>]*?)?(/>|>([\s\S]*?)</DirectionalLight>)', xml)
    if not match:
        return None
    attr = re.search(r'Direction="([^"]+)"', match.group(1) or "")
    if attr:
        return [float(v) for v in attr.group(1).split(",")]
    body = match.group(3) or ""
    value = re.search(r'<Direction value="([^"]+)"', body)
    if value:
        return [float(v) for v in value.group(1).split(",")]
    xyz = re.search(r'<Direction X="([^"]+)" Y="([^"]+)" Z="([^"]+)"', body)
    return [float(v) for v in xyz.groups()] if xyz else None


def parse_lights(xml: str, into: dict | None = None) -> dict:
    """CLight entries: id -> {parent?, key?: [x, y, z]} (the first time of day's Key light)."""
    into = {} if into is None else into
    for attrs, body in _entries(xml, "CLight"):
        if not attrs.get("id"):
            continue
        light = into.setdefault(attrs["id"], {})
        if attrs.get("parent"):
            light["parent"] = attrs["parent"]
        tod = re.search(r'<ToDInfoArray index="0"[^>]*>([\s\S]*?)</ToDInfoArray>', body)
        key = _key_direction(tod.group(1) if tod else body)
        if key:
            light["key"] = key
    return into


def _inherited(table: dict, id_: str, field: str, seen: set | None = None):
    """A field of a definition, or of its parents."""
    seen = set() if seen is None else seen
    definition = table.get(id_)
    if definition is None or id_ in seen:
        return None
    seen.add(id_)
    if field in definition:
        return definition[field]
    return _inherited(table, definition["parent"], field, seen) if definition.get("parent") else None


def _terrains(map_files: dict, table: dict) -> dict:
    """The game's tilesets with the map's own TerrainData.xml overrides (a copy)."""
    terrains = copy.deepcopy(table["terrains"])
    if map_files.get("terrainData"):
        parse_terrains(map_files["terrainData"], terrains)
    return terrains


def has_sky(map_files: dict, table: dict) -> bool:
    """Whether the map's void shows the sky: its tileset (with the map's own TerrainData.xml
    overrides) leaves the lowest terrain level undrawn, or names a skybox (fixed or parallax:
    Battlefield of Eternity clears the fixed one and shows its parallax layer). Otherwise the void
    is terrain drawn black (Dragon Shire, Towers of Doom, Tomb of the Spider Queen), which no
    skybox can show through."""
    tileset = tileset_of(map_files["t3Terrain"])
    if not tileset:
        return False
    terrains = _terrains(map_files, table)
    return bool(_inherited(terrains, tileset, "hideLowest") or _inherited(terrains, tileset, "skybox")
                or _inherited(terrains, tileset, "parallax"))


def sky_models(map_files: dict, table: dict) -> dict:
    """The map's own sky models, {fixed, parallax} (model ids, or None): its tileset's, with the
    map's TerrainData.xml overrides, falling back to the game's own where the map clears one
    (Battlefield of Eternity clears the fixed skybox, though its model still exists)."""
    tileset = tileset_of(map_files["t3Terrain"])
    if not tileset:
        return {"fixed": None, "parallax": None}
    terrains = _terrains(map_files, table)

    def pick(field: str) -> str | None:
        return _inherited(terrains, tileset, field) or _inherited(table["terrains"], tileset, field) or None

    return {"fixed": pick("skybox"), "parallax": pick("parallax")}


def main_light(map_files: dict, table: dict) -> dict:
    """The map's main light and the yaw that faces it. `map_files` holds the map's own
    t3Terrain.xml, TerrainData.xml and LightData.xml (the last two may be None); `table` is
    light-sets.json. Returns {tileset, lighting, key, yaw} with what could be resolved; yaw is
    None if not."""
    tileset = tileset_of(map_files["t3Terrain"])
    terrains = _terrains(map_files, table)
    lights = copy.deepcopy(table["lights"])
    if map_files.get("lightData"):
        parse_lights(map_files["lightData"], lights)
    # A tileset that names no light set uses the one with its own name (Sky Temple's
    # StormEgyptWorld).
    lighting = None
    if tileset:
        lighting = _inherited(terrains, tileset, "lighting") or (tileset if tileset in lights else None)
    key = _inherited(lights, lighting, "key") if lighting else None
    yaw = None
    if key:
        # The light travels along `key`; the look faces where it comes from. Yaw 90 looks along +y.
        # (Computed as the Node injector did, to the last bit: fmod keeps the sign like JS's %.)
        yaw = math.atan2(-key[1], -key[0]) * 180 / math.pi
        yaw = math.fmod(math.fmod(yaw, 360) + 360, 360)
    return {"tileset": tileset, "lighting": lighting or None, "key": key or None, "yaw": yaw}
