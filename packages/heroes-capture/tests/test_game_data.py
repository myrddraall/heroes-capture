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

# One worker for all of them: CascLib's CDN cache isn't safe for several processes filling it at
# once (a cold cache came out corrupt when the tests ran on parallel workers).
pytestmark = [pytest.mark.game_data, pytest.mark.xdist_group("cdn")]

# Names the script uses that Blizzard's code doesn't spell out but the game has accepted.
ACCEPTED = {"BoolToInt", "RegionRect", "GameSetBackground", "CutsceneStop", "c_syncFrameTypeTextTag", "TerrainShowRegion",
            "libMapM_gv_mMIntroCutscene", "libMapM_gv_mMIntroCutsceneFinished", "libMapM_gv_uIJungleCampPanel"}
KEYWORDS = {"if", "for", "while", "return", "else"}


def galaxy_calls(code: str, names: set[str] | None = None) -> list[tuple[str, list[str]]]:
    """Each call in Galaxy code (of `names` only, if given): the function's name and its arguments'
    source text, split at the top level's commas."""
    found = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", code):
        name = m.group(1)
        if name in KEYWORDS or (names is not None and name not in names):
            continue
        i, depth, args, current, quoted = m.end(), 1, [], "", False
        while i < len(code) and depth:
            ch = code[i]
            if quoted:
                quoted = not (ch == '"' and code[i - 1] != "\\")
            elif ch == '"':
                quoted = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if not depth:
                    break
            elif ch == "," and depth == 1:
                args.append(current.strip())
                current = ""
                i += 1
                continue
            current += ch
            i += 1
        if current.strip() or args:
            args.append(current.strip())
        found.append((name, args))
    return found


def literal_kind(arg: str) -> str | None:
    """What a call's argument plainly is, from its text: "string" (a literal, or text joined to one),
    "number" (a literal, or arithmetic on names), "bool"; None when it can't be told (a name, a call)."""
    if arg.startswith('"'):
        return "string"
    if re.fullmatch(r"-?\d+(\.\d+)?", arg):
        return "number"
    if arg in ("true", "false"):
        return "bool"
    if arg.startswith("(") and '"' not in arg and re.search(r"\w\s*[-+*/]\s*[\w(]", arg) and not re.search(r"[<>=!&|]", arg):
        return "number"
    return None


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


@pytest.mark.parametrize("map_name, sky_mode, arenas, extra", [
    ("Battlefield of Eternity", "matte", 0, []), ("Punisher Arena", "matte", 3, []), ("Dragon Shire", "black", 0, []),
])
def test_prepared_maps(map_name, sky_mode, arenas, extra, tmp_path, blizzard_galaxy):
    manifest_path = inject.main([map_name, "--screen", "3440x1440", "--distance", "214", "--keep", "0.4", "--out", str(tmp_path), *extra])
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
    if map_name == "Battlefield of Eternity":
        # "el isolate" hides the cliff doodads the map's terrain places, by type; the cores' holes,
        # filled in the file, are a region of one circle per cell the script opens as it starts.
        assert all(f'"{name}"' in script for name in ("StormDoodadHeaven1JungleFTO", "StormDoodadHell1JungleCTO"))
        assert capture.count("RegionAddCircle(hrsCap_holes, true, Point(") == 24 + 30
    unknown = sorted(n for n in names if not n.startswith("hrsCap_") and n not in KEYWORDS | ACCEPTED
                     and not re.search(r"\b" + re.escape(n) + r"\b", blizzard_galaxy))
    assert not unknown, f"names Blizzard's code doesn't have: {unknown}"
    # The elements probe's objective: spawned through Battlefield of Eternity's library only.
    assert ("libMLBD_gf_MMBOESpawnBoss" in capture) == (map_name == "Battlefield of Eternity")
    # One call with the wrong number or kind of arguments stops the script as surely as an unknown
    # name does (StringReplace(text, find, "", ...): it takes a range, not a replacement). Each
    # call of a function Blizzard defines in Galaxy is held to its parameters; each native's to
    # how Blizzard's code calls it: a count it uses, and no literal where it puts another kind.
    ours = [(n, a) for n, a in galaxy_calls(capture) if not n.startswith("hrsCap_")]
    used = {n for n, _ in ours}
    defined = {m.group(1): [p.split()[0] for p in m.group(2).split(",") if p.strip()]
               for m in re.finditer(r"^\w+\s+(\w+)\s*\(([^)]*)\)\s*\{", blizzard_galaxy, re.M) if m.group(1) in used}
    theirs: dict = {}
    for name, args in galaxy_calls(blizzard_galaxy, used - set(defined)):
        theirs.setdefault(name, {}).setdefault(len(args), [set() for _ in args])
        for kinds, arg in zip(theirs[name][len(args)], args):
            kinds.add(literal_kind(arg))
    wrong = []
    for name, args in ours:
        if name in defined:
            params = defined[name]
            plain = {"string": ("string", "text"), "number": ("int", "fixed"), "bool": ("bool",)}
            if len(args) != len(params) or any(literal_kind(a) and t not in plain[literal_kind(a)] for a, t in zip(args, params)):
                wrong.append(f"{name}({', '.join(args)}): Blizzard defines it ({', '.join(params)})")
        elif name in theirs:
            if len(args) not in theirs[name]:
                wrong.append(f"{name}({', '.join(args)}): Blizzard calls it with {sorted(theirs[name])} arguments")
                continue
            for k, (arg, kinds) in enumerate(zip(args, theirs[name][len(args)])):
                seen = kinds - {None}
                if literal_kind(arg) and seen and literal_kind(arg) not in seen:
                    wrong.append(f"{name}(...): argument {k + 1} is a {literal_kind(arg)}, Blizzard passes {sorted(seen)}")
    assert not wrong, "calls unlike Blizzard's: " + "; ".join(wrong)


def test_the_elements_probe_s_targets_on_battlefield_of_eternity(tmp_path):
    """The elements probe (--probe-elements) finds what it shoots in the map's placed objects: the
    Order team's forward town round its town hall, the camp nearest it, the Order core."""
    from heroes_capture import elements

    manifest = json.loads(inject.main(["Battlefield of Eternity", "--screen", "3440x1440", "--distance", "214", "--out", str(tmp_path)]).read_text())
    targets = elements.element_targets(manifest)
    hall, *rest = targets["town"]
    assert (hall["type"], hall["x"], hall["y"]) == ("TownTownHallL2", 98, 65)
    assert {u["type"] for u in rest} >= {"TownCannonTowerL2", "TownGateL215BLUR", "TownMoonwellL2", "TownWallRadial5L2"}
    assert all(u["player"] == elements.ORDER_PLAYER for u in targets["town"]) and len(targets["town"]) <= 8
    assert "MercCamp" in targets["camp"]["type"] and abs(targets["camp"]["x"] - 98) < 2 and abs(targets["camp"]["y"] - 95.5) < 1
    assert (targets["core"]["type"], targets["core"]["x"], targets["core"]["y"]) == ("KingsCore", 49, 100)
    # The holes the cores stand on (24 and 30 cells) filled in the prepared map, the rest of the
    # map's holes (the void round the islands) as they were (the script opens the two again).
    import numpy as np

    with Archive(manifest["stormmap"]) as archive:
        flags = archive.read("t3CellFlags")
    holes = (np.frombuffer(flags, np.uint8, 248 * 208, 32).reshape(208, 248) & elements.CELL_HOLE) > 0
    assert not holes[97:103, 46:52].any() and not holes[105:111, 196:202].any()
    assert holes.sum() == 6704 - 24 - 30


def test_a_map_s_own_pictures(storage, tmp_path):
    """The pictures a pack carries from the game (PACK.md, Images), from Dragon Shire's archive and
    the textures its MapInfo names."""
    from heroes_capture import pack

    name, data = game_data.map_file(storage, "Dragon Shire")
    (tmp_path / "map.stormmap").write_bytes(data)
    found = pack.map_images(tmp_path / "map.stormmap", storage, tmp_path / "images")
    assert {"minimap", "customMinimap", "customMinimapHover", "replayPreview", "mapSelect", "loadingScreen", "loadingScreenIcons"} <= set(found)
    assert found["minimap"] == {"file": "images/minimap.png", "size": [512, 512], "source": "Minimap.tga"}
    assert found["loadingScreen"]["source"] == "ui_ingame_mapmechanic_loadscreen_dragonshire.dds" and found["loadingScreen"]["size"] == [1920, 1080]
    assert len(found["loadingScreenIcons"]) == 3
    for entry in [found["minimap"], found["loadingScreen"], *found["loadingScreenIcons"]]:
        assert (tmp_path / entry["file"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_custom_minimap_mapinfo_names_as_a_tga(storage, tmp_path):
    """The custom minimap is the file MapInfo names: on Escape From Braxis, a .tga."""
    from heroes_capture import pack

    name, data = game_data.map_file(storage, "Escape From Braxis")
    (tmp_path / "map.stormmap").write_bytes(data)
    found = pack.map_images(tmp_path / "map.stormmap", None, tmp_path / "images")
    assert found["customMinimap"] == {"file": "images/custom-minimap.png", "size": [384, 464], "source": "CustomMiniMap.tga"}
    assert found["customMinimapHover"]["source"] == "CustomMiniMap_Hover.tga"


def test_cursed_hollow_s_custom_minimap_as_svg(storage, tmp_path):
    """Cursed Hollow's custom minimap redrawn (PACK.md, The custom minimap as SVG): each nexus an arc
    of the outline and its swirl, the camps, and drawn it looks like the picture."""
    import io

    import numpy as np
    import pyvips
    from PIL import Image

    from heroes_capture import minimap_svg

    name, data = game_data.map_file(storage, "Cursed Hollow")
    (tmp_path / "map.stormmap").write_bytes(data)
    with Archive(tmp_path / "map.stormmap") as archive:
        picture = Image.open(io.BytesIO(archive.read("CustomMiniMap.dds"))).convert("RGBA")
    svg = minimap_svg.to_svg(picture)
    outline = re.search(r'<path id="outline"[^>]* d="([^"]+)"', svg).group(1)
    assert outline.count("A") == 2  # each nexus: the circle's arc between its cut-ins' tips
    assert re.search(r'<path id="cut-ins" d="([^"]+)"', svg).group(1).count("Z") == 4  # their black, one per cut-in
    for nexus in ("nexus-1", "nexus-2"):
        assert re.search(rf'<g id="{nexus}">(.*?)</g>', svg, re.S).group(1).count('<path class="nexus"') == 1  # its swirl
    assert re.search(r'<g id="camps">(.*?)</g>', svg, re.S).group(1).count('<path class="camps"') == 6
    drawn = pyvips.Image.svgload_buffer(svg.encode())
    drawn = np.ndarray(buffer=drawn.write_to_memory(), dtype=np.uint8, shape=[drawn.height, drawn.width, drawn.bands]).astype(float)
    original = np.asarray(picture).astype(float)
    inside = original[..., 3] > 0
    diff = np.abs(drawn[..., :3] * drawn[..., 3:] / 255 - original[..., :3] * original[..., 3:] / 255).max(axis=2)[inside]
    assert diff.mean() < 3 and (diff > 25).mean() < 0.01
