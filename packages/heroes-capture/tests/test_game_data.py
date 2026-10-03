"""The game's own data, read from Blizzard's CDN as on a machine without the game: the maps, the
tilesets and light sets, the sky models; and maps prepared from it, whose injected script may use
only names Blizzard's own Galaxy code has (one unknown name stops the whole map script)."""

import json
import re
import tempfile
from pathlib import Path

import pytest

from heroes_capture import game_data, inject
from heroes_capture.sky import PARALLAX_KEYS
from heroes_capture.stormlib import Archive

pytestmark = pytest.mark.game_data

# Names the script uses that Blizzard's code doesn't spell out but the game has accepted.
ACCEPTED = {"BoolToInt", "RegionRect", "GameSetBackground", "CutsceneStop", "c_syncFrameTypeTextTag",
            "libMapM_gv_mMIntroCutscene", "libMapM_gv_mMIntroCutsceneFinished", "libMapM_gv_uIJungleCampPanel"}
KEYWORDS = {"if", "for", "while", "return", "else"}


@pytest.fixture(scope="module")
def blizzard_galaxy(storage) -> str:
    """All of Blizzard's Galaxy code: the storage's own files and those inside its mod archives."""
    code = [(storage.read(name) or b"").decode("utf-8", errors="replace") for name in storage.find("*.galaxy")]
    with tempfile.TemporaryDirectory() as folder:
        for name in storage.find("*.s2ma"):
            data = storage.read(name)
            if not data or data[:4] != b"MPQ\x1a":
                continue
            path = Path(folder) / "depot.s2ma"
            path.write_bytes(data)
            with Archive(path) as archive:
                listing = archive.read("(listfile)").decode("utf-8", errors="replace").split() if archive.has("(listfile)") else []
                code += [archive.read(n).decode("utf-8", errors="replace") for n in listing if n.lower().endswith(".galaxy")]
    return "\n".join(code)


def test_the_battleground_maps_are_found(storage):
    name, data = game_data.map_file(storage, "battlefield of eternity")
    assert name == "Battlefield of Eternity" and data[:4] == b"MPQ\x1a"
    for wanted in ("Punisher Arena", "Dragon Shire", "Hanamura Temple", "Cursed Hollow", "Towers of Doom"):
        assert game_data.map_file(storage, wanted)[0] == wanted


def test_tilesets_light_sets_and_sky_models(storage):
    table = game_data.light_sets(storage)
    assert len(table["terrains"]) > 30 and len(table["lights"]) > 300
    assert "lighting" in table["terrains"]["StormHeavenAndHellHeaven"] or "parent" in table["terrains"]["StormHeavenAndHellHeaven"]
    for spec in PARALLAX_KEYS.values():
        model = game_data.sky_model_file(storage, spec["file"])
        assert model and spec["base"].encode().lower() in model.lower()


@pytest.mark.parametrize("map_name, sky_mode, arenas", [
    ("Battlefield of Eternity", "matte", 0), ("Punisher Arena", "matte", 3), ("Dragon Shire", "black", 0),
])
def test_prepared_maps(map_name, sky_mode, arenas, tmp_path, blizzard_galaxy):
    manifest_path = inject.main([map_name, "--screen", "3440x1440", "--distance", "214", "--keep", "0.4", "--out", str(tmp_path)])
    manifest = json.loads(manifest_path.read_text())
    assert manifest["map"] == map_name and manifest["sky"]["mode"] == sky_mode
    assert len(manifest["areas"] or []) == arenas and len(manifest["tiles"]) > 50
    if sky_mode == "matte":
        assert manifest["sky"]["keys"] == (map_name == "Battlefield of Eternity" or map_name == "Punisher Arena")
    with Archive(manifest["stormmap"]) as archive:
        script = archive.read_text("MapScript.galaxy")
        assert archive.has("Assets\\Textures\\HrsWhite.dds")
    capture = script[script.index("// Map capture (injected"): script.index("void InitMap () {")]
    capture = re.sub(r"//[^\n]*", "", re.sub(r'"[^"\n]*"', '""', capture))
    names = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", capture)) | set(re.findall(r"\b(c_\w+|lib\w+_g[vf]_\w+)", capture))
    unknown = sorted(n for n in names if not n.startswith("hrsCap_") and n not in KEYWORDS | ACCEPTED
                     and not re.search(r"\b" + re.escape(n) + r"\b", blizzard_galaxy))
    assert not unknown, f"names Blizzard's code doesn't have: {unknown}"
