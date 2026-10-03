"""Build light-sets.json: for every tileset in the game, its light set and skybox, for every light
set the direction of its main ("Key") light. inject.py uses it to point the lighting-refit look at
a map's main light (--refit-yaw) without a per-map setting.

    python generate_light_sets.py <dir with the game's TerrainData.xml and LightData.xml files>

The files come from the game's data (mods/.../GameData/TerrainData.xml and LightData.xml in every
mod), extracted from its CASC storage; file names are the CASC paths with the separators replaced
by "__", so that base mods sort before the battleground mods that override them. A field set
later replaces one set earlier; missing fields come from a definition's parent.
"""

import re
import sys
from datetime import date
from pathlib import Path

import js_json
from light_data import parse_lights, parse_terrains


def order(name: str) -> int:
    for rank, pattern in enumerate((r"mods__core", r"mods__heroesdata", r"mods__heroes\.stormmod")):
        if re.search(pattern, name, re.I):
            return rank
    return 3


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python generate_light_sets.py <dir>")
    folder = Path(sys.argv[1])
    terrains: dict = {}
    lights: dict = {}
    files = sorted((p.name for p in folder.iterdir()), key=lambda n: (order(n), n.lower(), n))
    for name in files:
        xml = (folder / name).read_text(encoding="utf-8")
        if re.search(r"terraindata\.xml$", name, re.I):
            parse_terrains(xml, terrains)
        elif re.search(r"lightdata\.xml$", name, re.I):
            parse_lights(xml, lights)
    out = {"generated": date.today().isoformat(), "terrains": terrains, "lights": lights}
    Path(__file__).with_name("light-sets.json").write_text(js_json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"{len(terrains)} tilesets, {len(lights)} light sets, from {len(files)} files")


if __name__ == "__main__":
    main()
