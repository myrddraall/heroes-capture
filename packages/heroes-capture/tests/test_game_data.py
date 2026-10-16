"""The game's own data, read from Blizzard's CDN as on a machine without the game: the maps, the
tilesets and light sets, the sky models; and maps prepared from it, whose injected script may use
only names Blizzard's own Galaxy code has (one unknown name stops the whole map script)."""

import json
import re
import tempfile
from pathlib import Path

import pytest

from heroes_capture import cli
from heroes_capture.capture import prepare
from heroes_capture.common import game_data
from heroes_capture.map.galaxy import functions
from heroes_capture.capture.sky import PARALLAX_KEYS
from heroes_capture.common.stormlib import Archive

# One worker for all of them: CascLib's CDN cache isn't safe for several processes filling it at
# once (a cold cache came out corrupt when the tests ran on parallel workers).
pytestmark = [pytest.mark.game_data, pytest.mark.xdist_group("cdn")]

# Names the script uses that Blizzard's code doesn't spell out but the game has accepted.
# EnvironmentShow and its c_environment* constants: in the game executable's table of script
# functions and constants, next to TerrainShowRegion (which the game has accepted); so is
# c_actorIntersectAgainstCenter, beside the c_actorIntersectAgainstRadiusContact Blizzard uses;
# and GameSetGlobalTimeScale (`heroes-capture game-names`, on the user's install, 2026-10-13).
ACCEPTED = {"GameSetGlobalTimeScale", "BoolToInt", "RegionRect", "GameSetBackground", "CutsceneStop", "c_syncFrameTypeTextTag", "TerrainShowRegion",
            "EnvironmentShow", "c_environmentTerrain", "c_environmentDoodads", "c_environmentWater", "c_actorIntersectAgainstCenter",
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


def test_every_model_a_map_can_use_gets_its_particles_frozen(storage):
    """The models whose particles the capture freezes are every model of the shared data, wherever
    its catalog defines it: those in ModelData.xml, and those beside what uses them (the loot
    banner sconce's, in LootBoxData.xml, kept its purple smoke moving through the paused tiles)."""
    ids = set(game_data.map_model_ids(storage, []))
    model_data = storage.read("mods\\heroesdata.stormmod\\base.stormdata\\gamedata\\modeldata.xml").decode("utf-8", errors="replace")
    assert set(game_data.model_ids_in(model_data)) <= ids
    assert "LootBannerSconce" in ids
    lootbox = storage.read("mods\\heroesdata.stormmod\\base.stormdata\\gamedata\\lootbox\\lootboxdata.xml").decode("utf-8", errors="replace")
    assert set(game_data.model_ids_in(lootbox)) <= ids


def test_gamelib_takes_a_fort_s_standalone_tower_down_with_it(storage):
    """elements.STANDALONE_*: GameLib's Kill Standalone Towers, as the game has it."""
    import re

    from heroes_capture.categories.battleground import elements

    gamelib = (storage.read(storage.find("*gamelib.galaxy")[0]) or b"").decode("utf-8", "replace")
    body = gamelib[gamelib.index("bool libGame_gt_KillStandaloneTowers_Func"):]
    body = body[:re.search(r"\r?\n}\r?\n", body).start()]
    assert f'UnitGetType(EventUnit()) == "{elements.STANDALONE_FORT}"' in body
    assert f'== "{elements.STANDALONE_TOWER}"' in body
    assert float(re.search(r"UnitGroupSearch\(null, c_playerAny, UnitGetPosition\(EventUnit\(\)\), ([\d.]+)", body).group(1)) == elements.STANDALONE_REACH


def test_the_game_s_team_colours_from_our_player_s_view(storage):
    """GameUIData's OverrideColors, the shared Heroes data's over the core's: an ally's blue, an
    enemy's red (its glow darker; its diffuse the core's), as the game's own SetTeamColor uses them."""
    assert game_data.team_colours(storage) == {"ally": {"diffuse": [36, 92, 255], "emissive": [36, 92, 255]},
                                               "hostile": {"diffuse": [255, 0, 0], "emissive": [178, 0, 0]}}


def test_a_model_s_team_colour_is_read_from_its_materials(storage):
    """common/m3.py has_team_colour, on Dragon Shire's models as the game draws them: its forts,
    gates and moonwells team-coloured standing, its walls not; a fort's remains team-coloured, a
    moonwell's only while its death plays (its pools dissolve away)."""
    from heroes_capture.common.m3 import M3

    def model(pattern):
        return M3(storage.read(storage.find(pattern)[0]))

    assert model("*storm_building_kingscrest_fort.m3").has_team_colour("Stand")
    assert model("*storm_building_ds19kingscrest_gate_00.m3").has_team_colour("Stand")
    assert model("*storm_building_ds19kingscrest_manawell.m3").has_team_colour("Stand")
    assert not model("*storm_building_kingscrest_townwall_00.m3").has_team_colour("Stand")  # (its variations numbered)
    assert model("*storm_building_kingscrest_fort_death.m3").has_team_colour("Dead")
    moonwell_death = model("*storm_building_ds19kingscrest_manawell_death.m3")
    assert moonwell_death.has_team_colour("Death") and not moonwell_death.has_team_colour("Dead")


def test_structures_are_named_as_the_game_shows_them(storage):
    """GameStrings' Unit/Name entries (common/actors.py unit_names), for the run's log: the moonwell
    is the game's Healing Fountain, a level 2 town hall its Fort, a level 3 one its Keep; the map's
    own strings win over the mods'."""
    from heroes_capture.common.actors import unit_names

    types = ["KingsCore", "TownCannonTowerL3", "TownMoonwellL3", "TownTownHallL2", "TownTownHallL3", "TownWallRadial14L3"]
    assert unit_names(storage, ["dragonshire.stormmod"], "", types) == {
        "KingsCore": "Core", "TownCannonTowerL3": "Cannon Tower", "TownMoonwellL3": "Healing Fountain",
        "TownTownHallL2": "Fort", "TownTownHallL3": "Keep", "TownWallRadial14L3": "Wall"}
    assert unit_names(storage, [], "Unit/Name/KingsCore=Dragon Core\r\n", ["KingsCore"]) == {"KingsCore": "Dragon Core"}


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
    manifest_path = prepare.main([map_name, "--screen", "3440x1440", "--distance", "214", "--keep", "0.4", "--out", str(tmp_path), *extra],
                                 cli.specifics_of_map)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["map"] == map_name and manifest["sky"]["mode"] == sky_mode
    assert manifest["category"] == ("arena" if map_name == "Punisher Arena" else "battleground")
    assert len(manifest["areas"] or []) == arenas and len(manifest["tiles"]) > 50
    if sky_mode == "matte":
        assert manifest["sky"]["keys"] == (map_name == "Battlefield of Eternity" or map_name == "Punisher Arena")
    with Archive(manifest["stormmap"]) as archive:
        script = archive.read_text("MapScript.galaxy")
        assert archive.has("Assets\\Textures\\HrsWhite.dds")
        models = archive.read_text("Base.StormData\\GameData\\ModelData.xml")
    # Every model the map can use freezes its particles and ribbons while its animations are
    # paused: those of the shared data (a gate's) and of the map's own mods.
    # (In the map's own entry for a model it defines, which may have other attributes; appended otherwise.)
    frozen = set(re.findall(r'<CModel\b[^>]*\bid="([^"]+)"[^>]*><Flags index="FreezeParticlesAndRibbonsOnAnimPause" value="1"/>'
                            r'<PausedParticleSystemBehavior value="FreezeAll"/>', models))
    assert "TownGateDamaged" in frozen and len(frozen) > 1000
    if map_name == "Dragon Shire":  # its forts' model, defined in its own data: frozen in that entry, once
        assert "TownTownHallL2" in frozen and len(re.findall(r'<CModel id="TownTownHallL2"', models)) == 1
    if map_name == "Battlefield of Eternity":
        assert "TownMoonwellHeaven" in frozen
    capture = script[script.index("// heroes-capture's map script"): script.index("void InitMap () {")]
    capture = re.sub(r"//[^\n]*", "", re.sub(r'"[^"\n]*"', '""', capture))
    names = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", capture)) | set(re.findall(r"\b(c_\w+|lib\w+_g[vf]_\w+)", capture))
    if map_name == "Battlefield of Eternity":
        # The cores' holes, filled in the file, are a region of one circle per cell the script
        # opens as it starts.
        assert capture.count("RegionAddCircle(hrsCap_holes, true, Point(") == 24 + 30
    unknown = sorted(n for n in names if not n.startswith("hrsCap_") and n not in KEYWORDS | ACCEPTED
                     and not re.search(r"\b" + re.escape(n) + r"\b", blizzard_galaxy))
    assert not unknown, f"names Blizzard's code doesn't have: {unknown}"
    # The note at the top middle of the screen: its command, and taken down by every command a
    # shot starts with before anything is drawn for it.
    bodies = functions(script)
    assert 'if ((lp_word == "note")) { return hrsCap_gt_Note; }' in script
    assert "hrsCap_NoteHide();" in bodies["hrsCap_gt_Tile_Func"]
    element = bodies["hrsCap_gt_Element_Func"]
    assert "hrsCap_NoteHide();" in element[element.index('(lv_verb == "at")'):element.index('(lv_verb == "fades")')]
    # A map of rounds: "quit" makes the current round the last (the arenas' part).
    assert ("libAREN_gv_aRM_RoundScore" in capture) == (map_name == "Punisher Arena")
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


def test_battlefield_of_eternity_s_elements(tmp_path):
    """What a render cuts out of Battlefield of Eternity, read from its files when it is prepared:
    72 structures in 8 towns, the two lanes' forward pieces as two outposts of five, 4 camps (the
    numbers its renders have)."""
    manifest = json.loads(prepare.main(["Battlefield of Eternity", "--screen", "3440x1440", "--distance", "214", "--out", str(tmp_path)],
                                       cli.specifics_of_map).read_text())
    found = manifest["elements"]
    assert (len(found["structures"]), len(found["towns"]), len(found["camps"])) == (72, 8, 4)
    assert [(o["town"], o["owner"]) for o in found["outposts"]] == [(1, "order"), (7, "chaos")]
    assert sum(1 for u in found["structures"] if u["outpost"] is not None) == 10
    # The doodads in a team's colours (Heaven's lamps, crystal beds, grates, outer walls; Hell's core
    # range), and with the structures kept in the tiles the 72 structures: the tiles that see one are
    # shot from Chaos's side too.
    assert len(manifest["teamColoured"]) == 132 + 72
def test_a_map_s_own_pictures(storage, tmp_path):
    """The pictures a pack carries from the game (PACK.md, Images), from Dragon Shire's archive and
    the textures its MapInfo names."""
    from heroes_capture.capture import pack

    name, data = game_data.map_file(storage, "Dragon Shire")
    (tmp_path / "map.stormmap").write_bytes(data)
    found = pack.map_images(tmp_path / "map.stormmap", storage, tmp_path / "images")
    assert {"minimap", "customMinimap", "customMinimapHover", "replayPreview", "mapSelect", "loadingScreen", "loadingScreenIcons"} <= set(found)
    assert found["minimap"] == {"file": "images/minimap.png", "size": [512, 512], "source": "Minimap.tga"}
    assert found["loadingScreen"]["source"] == "ui_ingame_mapmechanic_loadscreen_dragonshire.dds" and found["loadingScreen"]["size"] == [1920, 1080]
    assert len(found["loadingScreenIcons"]) == 3
    for entry in [found["minimap"], found["loadingScreen"], *found["loadingScreenIcons"]]:
        assert (tmp_path / entry["file"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def loadscreen(*names: str) -> list[str]:
    return [f"ui_ingame_mapmechanic_loadscreen_{n}.dds" for n in names]


@pytest.mark.parametrize("map_name, screen, icons", [
    # MapInfo names the game's map entry, not the picture: the entry's DraftIntroImage.
    ("Battlefield of Eternity", "ui_ingame_mapmechanic_loadscreen_battlefieldofeternity.dds", loadscreen("eternity_icon1", "eternity_icon2", "eternity_icon3")),
    # The layout outside LoadingScreens/, its icons by asset name (@UI/...).
    ("Towers of Doom", "ui_ingame_mapmechanic_loadscreen_towersofdoom.dds", loadscreen(*(f"towersofdoom_icon{n}" for n in (1, 2, 3)))),
    # The layout's first picture is the loading screen itself: not an icon.
    ("Hanamura Temple", "ui_ingame_mapmechanic_loadscreen_hanamura_rework.dds", loadscreen(*(f"hanamura_icon{n}" for n in (1, 2, 3)))),
    # The layout MapInfo names isn't in the game's data: the icons by the loading screen's name.
    ("Warhead Junction", "storm_ui_homescreenbackground_warhead.dds", loadscreen(*(f"warhead_icon{n}" for n in (1, 2, 3)))),
    # MapInfo names a generic picture and the map's entry: the entry's is the arena's own.
    ("Punisher Arena", "ui_ingame_simple_loadscreen_battlefieldofeternity.dds", loadscreen("shrines_icon2", "punisherarena_icon2")),
])
def test_a_loading_screen_found_through_the_game_s_data(storage, tmp_path, map_name, screen, icons):
    """The loading screens and their icons (PACK.md, Images), as the game's data names them."""
    from heroes_capture.capture import pack

    (tmp_path / "map.stormmap").write_bytes(game_data.map_file(storage, map_name)[1])
    found = pack.map_images(tmp_path / "map.stormmap", storage, tmp_path / "images")
    assert found["loadingScreen"]["source"].lower() == screen and found["loadingScreen"]["size"][0] == 1920  # the simple ones 1020 tall
    assert [e["source"].lower() for e in found["loadingScreenIcons"]] == icons


def test_a_custom_minimap_mapinfo_names_as_a_tga(storage, tmp_path):
    """The custom minimap is the file MapInfo names: on Escape From Braxis, a .tga."""
    from heroes_capture.capture import pack

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

    from heroes_capture.capture import minimap_svg

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
