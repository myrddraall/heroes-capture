"""The pieces with exact rules, without the game."""

import importlib
import json
import os
import struct
import sys
import types

import numpy as np
import pytest

from heroes_capture.common import geometry
from heroes_capture.common import js_json
from heroes_capture.capture import matting
from heroes_capture.common.frames import frame_exists
from heroes_capture.map import script as map_script
from heroes_capture.map.galaxy import functions
from heroes_capture.map.script import check, fixed
from heroes_capture.capture.prepare import plan_grid
from heroes_capture.map.prepare import read_map_info
from heroes_capture.common.lighting import main_light, parse_lights, parse_terrains, tileset_of
from heroes_capture.capture.sky import parallax_keys
from heroes_capture.common.dds import clear_dds, solid_dds


# ------------------------------------------------------------------------------------------------
# JSON as JavaScript writes it (expected strings from Node's JSON.stringify)
# ------------------------------------------------------------------------------------------------

NUMBERS = {
    0: "0", -0.0: "0", 26.0: "26", 0.1: "0.1", 123.45: "123.45", 1e-7: "1e-7", 1.5e-7: "1.5e-7",
    0.000001: "0.000001", 0.0000012345: "0.0000012345", 1e21: "1e+21", 1.2345e21: "1.2345e+21",
    1e20: "100000000000000000000", 214.00000000000003: "214.00000000000003", 5e-324: "5e-324",
    1.7976931348623157e308: "1.7976931348623157e+308", 0.30000000000000004: "0.30000000000000004",
    2.5e-6: "0.0000025", -3.25: "-3.25",
}


@pytest.mark.parametrize("value", NUMBERS)
def test_numbers_as_javascript_prints_them(value):
    assert js_json.number(value) == NUMBERS[value]


def test_json_layout_as_json_stringify():
    value = {"a": [], "b": {}, "c": 'é"\n\u0001', "d": [{"x": 1.0, "y": [2, 3]}], "e": None, "f": True}
    assert js_json.dumps(value, indent=2) == (
        '{\n  "a": [],\n  "b": {},\n  "c": "é\\"\\n\\u0001",\n  "d": [\n    {\n      "x": 1,\n      "y": [\n'
        '        2,\n        3\n      ]\n    }\n  ],\n  "e": null,\n  "f": true\n}'
    )


# ------------------------------------------------------------------------------------------------
# The map script, assembled from its parts
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value, text", [(48, "48.0"), (214.00000000000003, "214.0000"), (41.03125, "41.0313"),
                                         (-3.09375, "-3.0938"), (8.019003082878507, "8.0190")])
def test_fixed_rounds_halves_away_from_zero(value, text):
    assert fixed(value) == text


def script(category: str | None = "arena", hole_cells=(), **overrides):
    """The map script as a preparation assembles it: the map's part, the capture's, then the
    category's (an arena's is a battleground's and its own; None: none), on a 256-cell map."""
    from heroes_capture.capture import script as capture
    from heroes_capture.categories.arena import specifics as arena
    from heroes_capture.categories.battleground import script as battleground

    options = dict(tiles=[{"x": 20, "y": 178}, {"x": 46.5, "y": 166.25}], hide_structures=False, distance=214.0, pitch=90.0,
                   refit_yaw=180.0, lens={"fov": 8.019, "farClip": 800}, unbound=False, show_ui=False,
                   opening_timers=["libX_gv_timer"], hide_doodads=["Storm_Doodad_Heaven_Clouds"])
    options.update(overrides)
    values = map_script.map_values(1234, 256, 256) | capture.values(**options)
    values |= battleground.values(list(hole_cells), values["map_region"], capture.doodad_lines(options["hide_doodads"]))
    parts = [map_script.PART, capture.PART, *{None: [], "battleground": [battleground.PART], "arena": [battleground.PART, arena.PART]}[category]]
    return map_script.assemble([p.read_text(encoding="utf-8") for p in parts], values)


def test_the_map_script_is_filled_and_declared_before_use():
    text = script()
    assert "${" not in text
    assert "hrsCap_tileX[1] = 46.5000; hrsCap_tileY[1] = 166.2500;" in text
    assert "const int hrsCap_mapId = 1234;" in text
    assert "hrsCap_CutShort(libX_gv_timer);" in text
    assert "libAREN_gv_aRM_RoundScore" in text
    check(text)
    assert "libAREN_gv_aRM_RoundScore" not in script("battleground")


@pytest.mark.parametrize("category, commands, inits", [
    (None, ["note", "quit", "tile", "clean", "black", "sky", "fov", "hidemap", "pause", "unbound", "side"], ["Core", "Capture"]),
    ("battleground", ["note", "quit", "tile", "clean", "black", "sky", "fov", "hidemap", "pause", "unbound", "side", "el"], ["Core", "Capture", "Battleground"]),
    ("arena", ["note", "quit", "tile", "clean", "black", "sky", "fov", "hidemap", "pause", "unbound", "side", "el"], ["Core", "Capture", "Battleground"]),
])
def test_every_combination_of_parts_assembles(category, commands, inits):
    """Each part's commands are in the command table, its init is called once from hrsCap_Init, in
    the parts' order, and a function a later part defines again replaces the earlier one: the
    capture's hrsCap_Restore replaces the map's empty one, an arena's hrsCap_EndMatch (the round
    made the last) the map's (the core killed)."""
    import re

    text = script(category)
    table = text[text.index("trigger hrsCap_CommandTrigger (string lp_word) {"):text.index("void hrsCap_Init () {")]
    assert re.findall(r'lp_word == "(\w+)"', table) == commands
    init = text[text.index("void hrsCap_Init () {"):]
    assert re.findall(r"hrsCap_(\w+)Init\(\);", init) == inits
    for word in commands:  # each command's trigger declared, then created
        trigger = re.search(rf'lp_word == "{word}"\)\) {{ return (\w+); }}', table).group(1)
        assert f"trigger {trigger};" in text and f"{trigger} = TriggerCreate(" in init
    assert len(re.findall(r"^void hrsCap_Restore \(\) \{", text, re.M)) == 1
    assert len(re.findall(r"^void hrsCap_EndMatch \(\) \{", text, re.M)) == 1
    assert ("libAREN_gv_aRM_RoundScore" in functions(text)["hrsCap_EndMatch"]) == (category == "arena")
    check(text)


def test_the_map_s_event_is_switched_off_unless_its_objective_is_brought_in():
    """A map whose objective isn't brought in for the tiles has its event switched off before the
    gates open (Battlefield of Eternity's Immortals fought in the middle of the tiles); a map whose
    opening timers are cut short (Hanamura: its objective in place for the tiles) keeps it. Minion
    waves are stopped on every map once it is ready."""
    assert "libMapM_gf_EnableDisableMapEvent(false);" in script(opening_timers=[])
    assert "libMapM_gf_EnableDisableMapEvent" not in script()
    assert "libGame_gf_MinionStartStopMinionSpawnCycle(false);" in script() and "libGame_gf_MinionStartStopMinionSpawnCycle(false);" in script(opening_timers=[])
    check(script(opening_timers=[]))


def test_the_elements_capture_s_commands_are_in_the_script():
    """The "el" command (the elements capture, ELEMENTS-PLAN.md): its verbs and no others, a tower
    brought down the game's way (the dead-state morph, not a kill), the remains cleared as
    Blizzard's maps clear a town."""
    import re

    text = script()
    assert 'if ((lp_word == "el")) { return hrsCap_gt_Element; }' in text
    verbs = ("hideall", "kill", "core", "at", "fades", "killall", "clear", "keep", "camp", "copy", "wide", "fadeall",
             "copykill", "play", "freeze", "holes", "env", "pausemsg", "clock", "typemsg", "worldfx", "light", "restart", "hold", "farclip", "water",
             "paint", "msg")
    assert sorted(set(re.findall(r'\(lv_verb == "(\w+)"\)', text))) == sorted(verbs)
    assert 'AbilityCommand("TowerDeadMorph", 0)' in text and 'UnitBehaviorAdd(lp_unit, "TownCannonTowerInvulnerable"' in text
    # A tower by its morph, not the Tower attribute (town halls have it too, and never fell).
    assert 'if (UnitAbilityExists(lp_unit, "TowerDeadMorph")) {' in text and "c_unitAttributeTower" not in text
    assert 'UnitGroup("TownCannonTowerDead"' in text and '"ScopeContains _DeathModel"' in text and "ActorWorldParticleFXDestroy();" in text
    assert "libGame_gv_teams[lv_team].lv_core = UnitLastCreated();" in text  # the replacement core first
    assert "lv_unit != libGame_gv_teams[libGame_gv_teamOrderIndex_C].lv_core" in text  # killall spares the cores
    # "el at": the lighting refitted (without it structures came out duller), then the capture
    # camera applied (a sky shot leaves the near clip past the ground); none of the rest of the scene.
    at = text[text.index('(lv_verb == "at")'):text.index('(lv_verb == "fades")')]
    assert at.index("hrsCap_NormalCamera(hrsCap_cmdPlayer);") < at.index("hrsCap_ApplyCamera(hrsCap_cmdPlayer);")
    assert "hrsCap_Scene();" not in at
    # Hiding a structure hides every actor of its own (a core's shield crystals), and showing it shows them.
    assert 'ActorScopeSend(ActorScopeFromUnit(lp_unit), "SetVisibility 0");' in text and "hrsCap_ShowStructure(lv_unit, lp_show);" in text
    # A fall sets off nothing for the players: no loot banner in a town hall's rubble, no XP.
    kill = functions(text)["hrsCap_Kill"]
    assert "hrsCap_QuietFalls();" in kill and 'UnitBehaviorAdd(lp_unit, "UnitGivesNoXP", lp_unit, 1);' in kill
    assert "libGame_gv_loot_DropBannerInTownHallRubble = false;" in text
    assert "libCore_gv_sYSXPOn = false;" in text
    assert "libMLBD_gf_MMBOESpawnBoss" not in text
    assert "if (!false) {" in text  # no holes: none opened
    full = script(hole_cells=[(46, 97), (47, 97)])
    assert "RegionAddCircle(hrsCap_holes, true, Point(46.5000, 97.5000), 0.45);" in full and "if (!true) {" in full
    assert "hrsCap_HolesInit();" in functions(full)["hrsCap_BattlegroundInit"]
    # Isolating: the terrain, every doodad and the units outside the circle hidden, and the loot
    # banners there (only those: rubble's wide bounds reach past the circle); then all shown again,
    # the holes opened again and the cloud layers hidden again.
    isolate = functions(full)["hrsCap_Isolate"]
    assert 'hrsCap_DoodadsMessage("SetVisibility 0");' in isolate and "hrsCap_IsolateUnits(lp_x, lp_y, lp_radius);" in isolate
    assert isolate.index("hrsCap_OpenHoles();") > isolate.index("TerrainShowRegion(RegionRect(-16.0, -16.0, 272.0, 272.0), true);")
    assert isolate.index('"Storm_Doodad_Heaven_Clouds");') > isolate.index('hrsCap_DoodadsMessage("SetVisibility 1");')
    assert '"ScopeContains LootBanner"' in full
    # "el env off": everything hidden, and the terrain, doodads and water switched off.
    assert "hrsCap_Isolate(true, 0.0, 0.0, 0.0);" in full and "EnvironmentShow(c_environmentTerrain, false);" in full
    assert "EnvironmentShow(c_environmentTerrain, true);" in full and "hrsCap_Isolate(false, 0.0, 0.0, 0.0);" in full
    check(full)


def test_declaration_order_is_enforced():
    with pytest.raises(SystemExit, match="hrsCap_B is used at line 1 before it is declared"):
        check("void hrsCap_A () { hrsCap_B(); }\nvoid hrsCap_B () {}\n")
    with pytest.raises(SystemExit, match="hrsCap_C is called at line 1 but no part defines it"):
        check("void hrsCap_A () { hrsCap_C(); }\n")


# ------------------------------------------------------------------------------------------------
# The grid and the map file
# ------------------------------------------------------------------------------------------------


def test_grid_spreads_tiles_between_the_bounds():
    opts = {"screen": {"w": 3440.0, "h": 1440.0}, "pxPerCell": 48.0, "distance": 214.0, "fov": 20.0, "margin": 0.0, "keep": 0.4}
    grid = plan_grid(opts, {"left": 0, "bottom": 0, "right": 100, "top": 50})
    assert (grid["cols"], grid["rows"]) == (5, 6)
    assert grid["step"] == {"x": 25.0, "y": 10.0}
    assert grid["tiles"][0] == {"index": 0, "row": 0, "col": 0, "x": 0, "y": 50}
    assert grid["tiles"][-1]["x"] == 100 and grid["tiles"][-1]["y"] == 0
    assert opts["fov"] == pytest.approx(8.019003082878507)  # chosen to show 30 cells from 214 (as every render)


def test_map_info_bounds_after_a_string():
    head = bytearray(32)
    struct.pack_into("<II", head, 16, 248, 208)
    data = bytes(head) + b"Name\x00" + struct.pack("<IIII", 20, 28, 228, 178) + bytes(8)
    info = read_map_info(data)
    assert (info["width"], info["height"]) == (248, 208)
    assert info["bounds"] == {"left": 20, "bottom": 28, "right": 228, "top": 178}


# ------------------------------------------------------------------------------------------------
# Lighting
# ------------------------------------------------------------------------------------------------


def test_main_light_through_parents_and_overrides():
    table = {
        "terrains": parse_terrains('<CTerrain id="Base"><Lighting value="Day"/></CTerrain>'
                                   '<CTerrain id="Child" parent="Base"><HideLowestLevel value="1"/></CTerrain>'),
        "lights": parse_lights('<CLight id="Day"><ToDInfoArray index="0"><DirectionalLight index="Key" Direction="0,1,-1"/>'
                               '</ToDInfoArray></CLight>'),
    }
    files = {"t3Terrain": '<heightMap tileSet="Child"/>', "terrainData": None, "lightData": None}
    assert tileset_of(files["t3Terrain"]) == "Child"
    light = main_light(files, table)
    assert light["lighting"] == "Day" and light["yaw"] == pytest.approx(270.0)
    files["lightData"] = '<CLight id="Day"><DirectionalLight index="Key"><Direction value="1,0,-1"/></DirectionalLight></CLight>'
    assert main_light(files, table)["yaw"] == pytest.approx(180.0)


# ------------------------------------------------------------------------------------------------
# Skies
# ------------------------------------------------------------------------------------------------


def test_solid_and_clear_textures():
    dds = solid_dds((255, 255, 255), 64, 32)
    assert dds[:4] == b"DDS " and struct.unpack_from("<III", dds, 12) == (32, 64, 1024) and dds[84:88] == b"DXT1"
    assert dds[128:132] == b"\xff\xff\xff\xff"
    assert len(dds) == 128 + 8 * (128 + 32 + 8 + 2 + 1 + 1 + 1)  # every mip level down to 1x1
    assert clear_dds(4, 4)[84:88] == b"DXT5"


def test_keyed_copies_keep_the_model_file_length():
    m3 = (b"head/Storm_Heaven_SkyParallax_Base_Diffuse.dds mid /Storm_Heaven_SkyParallax_Clouds_Diffuse.dds"
          b" /Storm_Heaven_SkyParallax_Clouds_Hell_Diffuse.dds tail")
    keys = parallax_keys("HeavenSkyboxParallax", m3)
    models = dict(keys["files"])
    assert len(keys["models"]) == 4
    for variant in ("light", "black", "bare", "lightbare"):
        copy = models[f"Assets\\Skyboxes\\HrsParallaxKeys\\HeavenSkyboxParallax_{variant}.m3"]
        assert len(copy) == len(m3)
    assert b"HrsKeyLight" in models["Assets\\Skyboxes\\HrsParallaxKeys\\HeavenSkyboxParallax_light.m3"]
    assert b"Base_Diffuse" in models["Assets\\Skyboxes\\HrsParallaxKeys\\HeavenSkyboxParallax_bare.m3"]


# ------------------------------------------------------------------------------------------------
# The sky measurement's consistency rule
# ------------------------------------------------------------------------------------------------


@pytest.fixture
def sky_layers(monkeypatch):
    monkeypatch.setitem(sys.modules, "heroes_capture.common.game_control", types.SimpleNamespace(settle=None, step=None))
    from heroes_capture.capture import sky_layers

    monkeypatch.setattr(sky_layers, "log", lambda message: None)
    return sky_layers


@pytest.mark.parametrize("rate, strength, expected", [
    ([1.2836, 0.4759], [0.01, 0.032], [0.4759, 0.4759]),  # an impossible rate: the other axis stands
    ([0.4745, 0.4758], [0.012, 0.042], [0.4745, 0.4758]),  # agreeing axes are kept
    ([None, 0.47], [None, 0.05], [0.47, 0.47]),  # one axis unmeasured
    ([0.2, 0.47], [0.3, 0.05], [0.2, 0.2]),  # disagreeing: the stronger match stands
    ([None, None], [None, None], [None, None]),
])
def test_sky_rates_agree_across_and_down(sky_layers, rate, strength, expected):
    sky_layers.consistent("haze", rate, strength)
    assert rate == expected


def test_the_haze_is_cut_off_where_the_white_key_isnt_behind_it():
    """Past the end of the sky shells (Punisher Arena's haze reaches further than its art) the haze
    is transparent, not estimated; where the key is behind it, its measured alpha and colour."""
    import numpy as np

    from heroes_capture.capture.sky_stitch import _haze

    h, w = 40, 80
    level = np.zeros((h, w, 3), np.float32)
    level[:, :40] = 230  # the white key behind the left half only
    haze_colour, haze_alpha = np.array([120.0, 130.0, 150.0]), 0.5
    black = np.full((h, w, 3), haze_colour * haze_alpha, np.float32)  # the haze over black, everywhere
    white = black + level * (1 - haze_alpha)  # over the key, where there is one
    out = _haze(white, black, level)
    assert np.allclose(out[:, :40, 3], haze_alpha, atol=0.01) and np.allclose(out[:, :40, :3], haze_colour, atol=1)
    assert (out[:, 40:, 3] == 0).all()


def test_a_shot_through_any_lens_maps_onto_the_centre_view_exactly():
    """The camera straight down at one height: a point of the tilted sky plane seen at screen
    position p from a camera, through a lens that sees `lens` times as much across, is where the
    affine transform puts it in the view from the centre camera (through the render's lens),
    exactly: checked against the ray from each camera to the plane."""
    import numpy as np

    from heroes_capture.capture.sky import lens_scale
    from heroes_capture.capture.sky_stitch import Plane

    focal, centre = 48.0 * 214, np.array([124.0, 103.0])
    plane = Plane(468.0, (0.01, -0.46), centre, focal)
    for camera, fov in (((20.0, 28.0), 8.0), ((228.0, 178.0), 16.0), ((124.0, 103.0), 32.0), ((20.0, 178.0), 32.0)):
        lens = lens_scale(fov, 8.0)
        matrix, t = plane.to_centre_view(camera, lens)
        for p in ((0.0, 0.0), (1700.0, -700.0), (-1720.0, 720.0), (900.0, 300.0)):
            # The ray through p from the camera (its focal: the render's over the lens) to the plane.
            f = focal / lens
            under = plane.under(camera)
            z = under / (1 - (plane.slope[0] * p[0] - plane.slope[1] * p[1]) / f)
            x, y = camera[0] + p[0] * z / f, camera[1] - p[1] * z / f
            seen = np.array([focal * (x - centre[0]) / z, focal * (centre[1] - y) / z])
            assert np.allclose(matrix @ np.array(p) + t, seen, atol=1e-6), (camera, fov, p)


def test_a_layer_is_squared_off_to_the_largest_rectangle_inside_its_picture():
    """The sky pictures are trapezoids, with holes (the background art's gaps onto the fixed skybox)
    and stray specks around them: the picture is its largest part, holes filled, and the layer's
    rectangle the largest inside it."""
    import numpy as np

    from heroes_capture.capture.sky_stitch import largest_rectangle, panel

    defined = np.zeros((40, 100), bool)
    for y in range(4, 36):
        defined[y, y // 2 : 100 - y // 2] = True  # narrower towards the bottom
    defined[15:20, 40:45] = False  # a hole
    defined[0, 0] = defined[39, 99] = True  # specks
    picture = panel(defined)
    assert picture[15:20, 40:45].all() and not picture[0, 0] and not picture[39, 99]
    x0, y0, x1, y1 = largest_rectangle(picture)
    assert picture[y0:y1, x0:x1].all()
    area = (x1 - x0) * (y1 - y0)
    # No rectangle inside it is bigger: every band of rows, as wide as its narrowest row allows.
    best = max((min(100 - b // 2 for b in range(top, bottom)) - max(b // 2 for b in range(top, bottom))) * (bottom - top)
               for top in range(4, 36) for bottom in range(top + 1, 37))
    assert area == best
    assert largest_rectangle(np.zeros((3, 3), bool)) is None


# ------------------------------------------------------------------------------------------------
# Map elements: two shots' difference
# ------------------------------------------------------------------------------------------------


def test_a_diagnostic_s_change_map_and_crop():
    """What changed between two shots (diagnostics.change_map: the share over the threshold, the
    second shot where it differs and the rest dimmed), described in numbers, and the screen round a
    spot with the strip's column left out."""
    from heroes_capture.capture.diagnostics import util as diagnostics

    a = np.zeros((40, 60, 3), np.uint8)
    b = a.copy()
    b[10:20, 30:50] = 200  # what changed
    b[5, 40] = 40  # not over the threshold
    share, picture = diagnostics.change_map(a, b)
    assert share == 200 / (40 * 60) and (picture[10:20, 30:50] == 200).all() and (picture[0, 0] == 0).all()
    mask = (b.max(axis=2) > 40)
    assert diagnostics.describe(mask) == " 8.333% of pixels changed, within x 30..50, y 10..20 (100% of that box)"
    assert diagnostics.describe(np.zeros((4, 4), bool)) == "no pixels changed"
    manifest = {"pxPerCell": 2, "screen": {"w": 60, "h": 40}, "status": {"pageLeft": 4}}
    # 10 cells round the screen's middle (30, 20): 20 px either way and a pixel to spare, clipped to
    # the frame; the strip's 4 columns are outside it anyway.
    assert diagnostics.crop_round(b, {"x": 0, "y": 0}, {"x": 0, "y": 0}, manifest, 10).shape == (40, 43, 3)
    assert diagnostics.crop_round(b, {"x": -10, "y": 0}, {"x": 0, "y": 0}, manifest, 10).shape[1] == 32 - 4  # the strip left out


def test_the_holes_structures_stand_on_are_filled_and_no_others():
    """A core stands on a small hole in the terrain (its pedestal covers it); that hole loses its
    hole flag, so the ground is drawn once the core's remains are cleared. A hole with no structure
    on it, or with only a marker in it, stays."""
    from heroes_capture.categories.battleground import elements

    width, height = 12, 10
    cells = np.zeros((height, width), np.uint8)
    cells[4:7, 2:5] = elements.CELL_HOLE | 1  # under the core (other bits kept)
    cells[0:2, 8:12] = elements.CELL_HOLE  # the void, with a marker in it
    header = b"LFCT" + bytes(20) + struct.pack("<II", width, height)
    objects = ('<ObjectUnit Id="1" Position="3.5,5,0" UnitType="KingsCore" Player="11"/>'
               '<ObjectUnit Id="2" Position="9,0.5,0" UnitType="StormGameStartPathingBlocker"/>'
               '<ObjectUnit Id="3" Position="7,7,0" UnitType="TownCannonTowerL2" Player="11"/>')
    filled, holes = elements.fill_structure_holes(header + cells.tobytes() + b"tail", objects)
    assert holes == [{"type": "KingsCore", "x": 3.5, "y": 5.0, "cells": [(x, y) for y in range(4, 7) for x in range(2, 5)]}]
    assert filled[:32] == header and filled.endswith(b"tail")
    after = np.frombuffer(filled, np.uint8, width * height, 32).reshape(height, width)
    assert (after[4:7, 2:5] == 1).all() and (after[0:2, 8:12] == elements.CELL_HOLE).all()


def test_an_element_s_cut_out_from_its_shots_over_the_sky():
    """Matted from the shots over white and black, cropped to the circle round it, the sky's veil
    transparent, solid parts opaque in the black shot's colour, a soft edge kept as it is."""
    from heroes_capture.categories.battleground import elements

    white = np.full((60, 80, 3), 230, np.uint8)
    black = np.zeros((60, 80, 3), np.uint8)
    white[:, 70:] = 228  # the white sky a shade uneven: the veil
    black[20:30, 20:30] = 100  # a solid part ...
    white[20:30, 20:30] = 110  # ... the white sky brightening it by 10 levels
    black[20:30, 30:32] = 50  # a soft edge, half see-through
    white[20:30, 30:32] = 50 + 115
    black[5, 75] = white[5, 75] = 90  # outside the circle (a cliff doodad)
    rgba = matting.cut_out(white, black, centre=(30, 25), radius=20)
    assert (rgba[20:30, 20:30, 3] == 255).all() and (rgba[20:30, 20:30, :3] == 100).all()
    assert (abs(rgba[20:30, 30:32, 3].astype(int) - 128) <= 2).all()
    assert rgba[5, 75, 3] == 0 and (rgba[:, 70:, 3] == 0).all() and rgba[50, 10, 3] == 0
    assert geometry.screen_point({"x": 108, "y": 60}, {"x": 98, "y": 62.6154}, {"w": 3440, "h": 1440}, 48) == pytest.approx((2200, 845.5), abs=0.1)


def test_an_element_s_cut_out_leaves_out_what_can_t_be_hidden_and_its_neighbours_rubble():
    """What is the same as in its tile's shot with everything hidden (a cliff doodad) is left out,
    but not the element drawn over it; rubble keeps only what is nearer its own structure."""
    from heroes_capture.categories.battleground import elements

    white = np.full((60, 80, 3), 230, np.uint8)
    black = np.zeros((60, 80, 3), np.uint8)
    white[10:50, 10:20] = black[10:50, 10:20] = 70  # a cliff doodad: can't be hidden
    empty = white.copy()
    white[20:30, 12:18] = black[20:30, 12:18] = 150  # the element, over the doodad
    white[20:30, 40:46] = black[20:30, 40:46] = 150  # a neighbour's rubble, nearer it than us
    rgba = matting.cut_out(white, black, centre=(15, 25), radius=40, empty=empty, others=[(43, 25)])
    assert (rgba[20:30, 12:18, 3] == 255).all()  # the element, kept over the doodad
    assert (rgba[10:20, 10:20, 3] == 0).all() and (rgba[30:50, 10:20, 3] == 0).all()  # the doodad, left out
    assert (rgba[20:30, 40:46, 3] == 0).all()  # the neighbour's rubble, left out


def test_an_element_s_cut_out_from_only_the_part_of_its_shots_round_its_circle_is_the_same():
    """The stitch mattes only the part of an element's shots round its circle (circle_box), the
    white level measured on the whole shot: the same cut-out, pixel for pixel, as from the whole
    shots, and the same with what can't be hidden and a neighbour's rubble left out."""
    from heroes_capture.categories.battleground import elements
    from heroes_capture.capture.matting import light_level

    rng = np.random.default_rng(7)
    white = np.full((300, 400, 3), 231, np.uint8)
    black = np.zeros((300, 400, 3), np.uint8)
    body = rng.integers(0, 200, (120, 150, 3), dtype=np.uint8)
    see = rng.random((120, 150, 1))  # every share of see-through, soft edges and glows
    black[90:210, 100:250] = (body * (1 - see)).astype(np.uint8)
    white[90:210, 100:250] = np.clip(body * (1 - see) + 231 * see, 0, 255).astype(np.uint8)
    empty = white.copy()
    empty[150:170, 120:140] = white[150:170, 120:140]  # the same as the empty shot there: left out
    empty[:150] = 0
    centre, radius, others = (170.4, 148.7), 63.2, [(230.0, 150.0)]
    whole = matting.cut_out(white, black, centre, radius, empty, others)
    v0, v1, u0, u1 = geometry.circle_box(centre, radius, white.shape)
    part = (slice(v0, v1), slice(u0, u1))
    local = lambda p: (p[0] - u0, p[1] - v0)  # noqa: E731
    cut = matting.cut_out(white[part], black[part], local(centre), radius, empty[part], [local(o) for o in others],
                           level=light_level(white, black))
    assert (cut == whole[part]).all()
    outside = whole.copy()
    outside[part] = 0
    assert not outside[..., 3].any()  # nothing of the cut-out outside the part
    assert geometry.circle_box((5.0, 5.0), 20.0, (300, 400, 3)) == (0, 27, 0, 27)  # clipped to the frame


def test_a_map_s_element_list_from_its_script_objects_and_regions():
    """Towns as the script hooks them up (the lane set before a town counts for it), each structure
    in the town whose region holds it (a shape marked negative cuts out of it), camps numbered in
    the script's order with their middle and spread from their spawn points."""
    from heroes_capture.categories.battleground import elements

    script = """bool gt_HookupTownData_Func (bool testConds, bool runActions) {
    lv_lane = 1;
    lv_town += 1;
    libGame_gv_townTownData[lv_town].lv_lane = lv_lane;
    libGame_gv_townTownData[lv_town].lv_owner = libCore_gv_cOMPUTER_TeamOrder;
    libGame_gv_townTownData[lv_town].lv_townRegion = RegionFromId(2);
    lv_lane = 2;
    lv_town += 1;
    libGame_gv_townTownData[lv_town].lv_owner = libCore_gv_cOMPUTER_TeamChaos;
    libGame_gv_townTownData[lv_town].lv_townRegion = RegionFromId(3);
    return true;
}
bool gt_HookupJungleCreepData_Func (bool testConds, bool runActions) {
    lv_junglecamp += 1;
    libMapM_gv_jungleCreepCamps[lv_junglecamp].lv_mapDataCampDefenderType = libMapM_ge_JungleCampDefenderTypes_SiegeCamp1;
    libMapM_gv_jungleCreepCamps[lv_junglecamp].lv_mapDataCampCaptainSpawnPoint = PointFromId(7);
    libMapM_gv_jungleCreepCamps[lv_junglecamp].lv_mapDataDefenderSpawnPoints[1] = PointFromId(8);
    libMapM_gv_jungleCreepCamps[lv_junglecamp].lv_mapDataDefenderSpawnPoints[2] = PointFromId(9);
    return true;
}
"""
    regions = ('<region id="2"><name value="Order town"/><shape type="rect"><quad value="0,0,20,20"/></shape>'
               '<shape type="rect"><negative/><quad value="15,15,20,20"/></shape></region>'
               '<region id="3"><name value="Chaos town"/><shape type="diamond"><center value="50,50"/>'
               '<width value="10"/><height value="10"/></shape></region>')
    objects = ('<ObjectPoint Id="7" Position="30,30,0" Type="Normal"/><ObjectPoint Id="8" Position="32,30,0" Type="Normal"/>'
               '<ObjectPoint Id="9" Position="31,33,0" Type="Normal"/>'
               '<ObjectUnit Id="20" Position="5,5,0" UnitType="TownCannonTowerL2" Player="11"/>'
               '<ObjectUnit Id="21" Position="17,17,0" UnitType="TownMoonwellL2" Player="11"/>'
               '<ObjectUnit Id="22" Position="52,51,0" UnitType="TownTownHallL2" Player="12"/>'
               '<ObjectUnit Id="23" Position="80,80,0" UnitType="KingsCore" Player="12"/>'
               '<ObjectUnit Id="24" Position="6,6,0" UnitType="LootBannerSconce"/>')
    found = elements.element_list(script, objects, regions)
    assert found["towns"] == [{"town": 1, "lane": 1, "owner": "order", "region": 2, "name": "Order town"},
                              {"town": 2, "lane": 2, "owner": "chaos", "region": 3, "name": "Chaos town"}]
    assert [(u["id"], u["owner"], u["town"], u["outpost"], u["core"]) for u in found["structures"]] == [
        (20, "order", 1, None, False), (21, "order", None, None, False), (22, "chaos", 2, None, False), (23, "chaos", None, None, True)]
    assert found["outposts"] == []  # town 1 has no hall; town 2 is its hall alone
    (camp,) = found["camps"]
    assert (camp["camp"], camp["type"], camp["x"], camp["y"]) == (1, "SiegeCamp1", 31.0, 31.0)
    assert camp["spread"] == 2.0 and camp["radius"] == 2.0 + elements.CAMP_MARGIN
    tiles = [{"index": 0, "x": 0, "y": 0}, {"index": 1, "x": 30, "y": 30}]
    assert elements.source_tile(camp, tiles)["index"] == 1


@pytest.fixture
def shots(monkeypatch):
    """The shot helpers with the game's controls stood in for (they need Windows)."""
    monkeypatch.setitem(sys.modules, "heroes_capture.common.game_control", types.SimpleNamespace(settle=lambda seconds: None))
    monkeypatch.delitem(sys.modules, "heroes_capture.capture.shots", raising=False)
    from heroes_capture.capture import shots

    return shots


def test_a_side_switch_counts_as_drawn_once_enough_of_a_box_changes(shots):
    """A side switch is watched for on screen: more than RECOLOUR_PIXELS pixels of one watched box
    (a structure's circle, a team-coloured doodad's) changed, nothing outside them counted."""
    import numpy as np

    reference = np.zeros((40, 40, 3), np.uint8)
    frame = reference.copy()
    frame[0:10, 0:10] = 255  # 100 pixels changed, outside the boxes
    assert not shots.recoloured(reference, frame, [(20, 40, 20, 40), (0, 8, 30, 40)])
    frame[20:25, 20:25] = (200, 40, 30)  # 25 inside: not more than RECOLOUR_PIXELS
    assert not shots.recoloured(reference, frame, [(20, 40, 20, 40)])
    frame[25, 20] = (200, 40, 30)
    assert shots.recoloured(reference, frame, [(0, 8, 30, 40), (20, 40, 20, 40)])


def test_a_town_s_outposts_are_its_structures_beyond_the_hall_s_ring_joined_by_nearness():
    """The hall's ring (within RING_RADIUS) is the town's own; what lies beyond it forms outposts,
    structures within OUTPOST_LINK of one another together, numbered from the hall outwards and
    across the towns in town order. The cores and the untowned belong to none."""
    from heroes_capture.categories.battleground import elements

    def unit(id, type, x, y, town):
        return {"id": id, "type": type, "x": x, "y": y, "town": town}

    structures = [unit(1, "TownTownHallL3", 60, 120, 1), unit(2, "TownWallRadial2L3", 64, 134, 1),  # 14.6: in the ring
                  unit(3, "TownCannonTowerL3Standalone", 70, 100, 1), unit(4, "TownMoonwellL3", 72, 97, 1),
                  unit(5, "TownCannonTowerL3Standalone", 80, 105, 1),  # 12.8 from 3: joined
                  unit(6, "TownCannonTowerL3Standalone", 60, 160, 1),  # alone, further out
                  unit(7, "KingsCore", 40, 100, None), unit(8, "TownCannonTowerL3Standalone", 90, 140, None),
                  unit(9, "TownTownHallL2", 150, 60, 2), unit(10, "TownCannonTowerL3Standalone", 150, 40, 2)]
    towns = [{"town": 1, "lane": 1, "owner": "order", "region": 2, "name": ""}, {"town": 2, "lane": 2, "owner": "chaos", "region": 3, "name": ""}]
    outposts = elements.group_outposts(structures, towns)
    assert outposts == [{"outpost": 1, "town": 1, "lane": 1, "owner": "order", "x": 74.0, "y": 100.667},
                        {"outpost": 2, "town": 1, "lane": 1, "owner": "order", "x": 60.0, "y": 160.0},
                        {"outpost": 3, "town": 2, "lane": 2, "owner": "chaos", "x": 150.0, "y": 40.0}]
    assert [u["outpost"] for u in structures] == [None, None, 1, 1, 1, 2, None, None, None, 3]


def test_walls_alone_beyond_the_ring_stay_in_their_town():
    """Dragon Shire's walls stand 15 to 15.7 from the hall, just past the ring: a group of walls
    alone isn't an outpost."""
    from heroes_capture.categories.battleground import elements

    structures = [{"id": 1, "type": "TownTownHallL2", "x": 100, "y": 100, "town": 1},
                  {"id": 2, "type": "TownWallRadial5L2", "x": 115.4, "y": 100, "town": 1},
                  {"id": 3, "type": "TownWallRadial3L3", "x": 100, "y": 115.5, "town": 1},
                  {"id": 4, "type": "TownWallRadial2L3", "x": 100, "y": 70, "town": 1},  # with a tower: an outpost
                  {"id": 5, "type": "TownCannonTowerL3Standalone", "x": 104, "y": 70, "town": 1}]
    outposts = elements.group_outposts(structures, [{"town": 1, "lane": 1, "owner": "order", "region": 2, "name": ""}])
    assert [u["outpost"] for u in structures] == [None, None, None, 1, 1] and len(outposts) == 1


def test_a_standalone_tower_outside_the_towns_goes_with_its_fort():
    """GameLib takes a fort's standalone tower down with it (the nearest within STANDALONE_REACH),
    so one outside every town region is put in that fort's town; one near no fort stays out."""
    from heroes_capture.categories.battleground import elements

    structures = [{"id": 1, "type": "TownTownHallL2", "x": 100, "y": 100, "town": 4},
                  {"id": 2, "type": "TownTownHallL3", "x": 130, "y": 100, "town": 5},  # a keep takes none
                  {"id": 3, "type": "TownCannonTowerL3Standalone", "x": 113.2, "y": 100, "town": None},
                  {"id": 4, "type": "TownCannonTowerL3Standalone", "x": 125, "y": 100, "town": None},
                  {"id": 5, "type": "TownCannonTowerL3Standalone", "x": 100, "y": 140, "town": None}]
    elements.standalone_towns(structures)
    assert [u["town"] for u in structures] == [4, 5, 4, None, None]


class _Recoverable(Exception):
    def __init__(self, why, resume_at=None):
        super().__init__(why)
        self.why, self.resume_at = why, resume_at


@pytest.fixture
def element_capture(monkeypatch):
    """The elements capture with the game's controls stood in for (they need Windows)."""
    monkeypatch.setitem(sys.modules, "heroes_capture.common.game_control",
                        types.SimpleNamespace(settle=lambda seconds: None, step=lambda action, what: action(), Recoverable=_Recoverable))
    monkeypatch.delitem(sys.modules, "heroes_capture.categories.battleground.element_capture", raising=False)
    monkeypatch.delitem(sys.modules, "heroes_capture.capture.shots", raising=False)
    # Through sys.modules: the package's attribute may still be another test's import.
    element_capture = importlib.import_module("heroes_capture.categories.battleground.element_capture")

    monkeypatch.setattr(element_capture, "log", lambda message: None)
    monkeypatch.setattr(element_capture, "warn", lambda message: None)
    return element_capture


class _FakeSession:
    """Answers every command, and shows a frame; records what was typed. The frame is black from
    Order's side and white from Chaos's (after "side 2"), and white while repainted ("el paint ...
    SetTeamColor", until the camera moves on): a side switch and a repaint show."""

    def __init__(self, unanswered=()):
        self.sent, self.unanswered, self.team, self.painted = [], set(unanswered), 1, False
        self.everything = []  # what was typed, the lighting of the structures' effects too

    def send(self, command, timeout=2.0):
        self.everything.append(command)
        # Each structure's restart and pause at its standing time ("el restart", "el hold", once
        # the camera reaches it) left out of `sent`: the tests below are about the shots
        # (test_each_structure_is_restarted_and_paused_at_its_standing_time).
        if not command.startswith(("el restart ", "el hold ")):
            self.sent.append(command)
        if command.startswith("side ") and command not in self.unanswered:
            self.team = int(command.split()[-1])
        if command.startswith("el paint "):
            self.painted = "SetTeamColor" in command
        if command.startswith("el at "):
            self.painted = False  # (a repaint is of the remains where it was sent: not those the camera moves to)
        return None if command in self.unanswered else (types.SimpleNamespace(camera_x=1.0, camera_y=2.0), self.grab())

    def blank(self, frame):
        return frame

    def command(self, text, timeout=3.0):
        answer = self.send(text, timeout)
        return answer[0] if answer else None

    def camera(self, default=None):
        return self.status().camera

    def grab(self, dark_ok=False):
        return np.full((40, 60, 3), 0 if self.team == 1 and not self.painted else 255, np.uint8)

    def status(self):
        return types.SimpleNamespace(camera_x=10.0, camera_y=20.0, camera={"x": 10.0, "y": 20.0})


ELEMENTS_MANIFEST = {
    "tiles": [{"index": 0, "x": 0, "y": 0}, {"index": 1, "x": 50, "y": 50}],
    "screen": {"w": 3440, "h": 1440}, "pxPerCell": 48, "distance": 85.0,
    "teamColours": {"ally": {"diffuse": [36, 92, 255], "emissive": [36, 92, 255]}, "hostile": {"diffuse": [255, 0, 0], "emissive": [178, 0, 0]}},
    "elements": {
        "structures": [{"id": 5, "type": "TownCannonTowerL2", "x": 1, "y": 2, "owner": "order", "town": 1, "core": False, "radius": 6.0},
                       {"id": 6, "type": "KingsCore", "x": 3, "y": 1, "owner": "chaos", "town": None, "core": True, "radius": 6.0}],
        "towns": [],
        "camps": [{"camp": 1, "type": "SiegeCamp1", "x": 49, "y": 51, "spread": 2.0, "radius": 5.0}],
    },
}


def test_the_elements_capture_shoots_each_element_then_brings_the_structures_down(element_capture, tmp_path):
    session = _FakeSession()
    element_capture.capture_elements(session, ELEMENTS_MANIFEST, tmp_path)
    sent = session.sent
    # Every structure faded out (not hidden: it keeps its look and effects as the map paused it),
    # the rest of the scene hidden, the camp (clear of every structure's circle) spawned straight
    # away, born, and frozen; then each structure faded in in the command that moves the camera
    # straight above it (the move sets the white sky), what the one before left faded in faded out
    # in it too, shot over white and black; no sky put back after (the next move does it). (No
    # camera bounds in this manifest: no room for copies, so every structure falls in waves.)
    # In passes: each structure shot over white and black from Order's side; then (every structure
    # standing as it is shot) each faded in over black with its neighbour (within NEIGHBOUR_REACH:
    # no models in this report) faded in beside it (which of them is in front where they overlap);
    # then our player put on Chaos once, each shot again over white and black, and back once. The
    # last one left faded in, faded out before the camp's shot. While the camp is born, the note at
    # the top of the screen says so.
    assert sent[:24] == ["tile 0 0.00 0.00", "el fadeall 0", "el env off", "el keep 1", "el camp 1",
                         "note Waiting for the camps to spawn", "el freeze",
                         "el at 1.00 2.00 1 2 1", "black", "el at 3.00 1.00 1 2 0 3 1 1", "black",  # from Order
                         "el at 1.00 2.00 3 1 0 1 2 1", "black", "el fades 3 1 1",  # with its neighbour
                         "el at 3.00 1.00 1 2 0 3 1 0 3 1 1", "black", "el fades 1 2 1",
                         "side 2", "el at 1.00 2.00 3 1 0 1 2 0 1 2 1", "black", "el at 3.00 1.00 1 2 0 3 1 1", "black", "side 1",
                         "el fades 3 1 0"]
    # The camp shot after the structures (from Order's only: camps don't change with the side), its
    # defenders removed; no isolating anywhere.
    assert sent[24:28] == ["el at 49.00 51.00", "black", "el keep 0", "clean"] and sent.count("el camp 1") == 1
    records = json.loads((tmp_path / "elements" / "elements.json").read_text())
    assert records["structure-5-standing"]["neighbours"] == [6] and records["structure-6-standing"]["neighbours"] == [5]
    records = json.loads((tmp_path / "elements" / "elements.json").read_text())
    assert records["structure-5-standing"]["chaos"] == "structure-5-standing-chaos" and "chaos" not in records["camp-1-spawned"]
    assert {p.stem for p in (tmp_path / "elements").glob("structure-5-standing*")} >= {
        "structure-5-standing", "structure-5-standing-black", "structure-5-standing-chaos", "structure-5-standing-chaos-black"}
    # (The only sky: the light one back for a remains' repainted shots, rubble_shots.)
    assert not any(c.startswith("el isolate") or (c.startswith("sky ") and c != "sky lightgrey 0") for c in sent)
    # Rubble in waves: the tower, then the core in a wave of its own, last (after its hidden
    # replacement takes its place); each brought down, left for its time, shot centred, repainted
    # in the other side's team colour and shot again (a fallen structure keeps the colour it died
    # with, whatever the side), put back, cleared away.
    core, kill = sent.index("el core 2 -1"), sent.index("el kill 1 2 -1")
    # Each brought down with the camera far back over it (a fall gives off its smoke and dust only
    # near the camera).
    assert kill < core and sent[core - 2:core] == ["el wide 3.00 1.00 212.5", "el fades 3 1 1"]
    assert sent[kill - 3:kill] == ["note Rubble wave 1 of 2: waiting for the structures to fall", "el wide 1.00 2.00 212.5", "el fades 1 2 1"]
    assert sent[core - 3] == "note Rubble wave 2 of 2: waiting for the structures to fall"
    # As drawn over white and black, repainted with the sky still black, and (changed) over the
    # light sky; nothing put back (ClearTeamColor doesn't).
    assert sent[core + 1:core + 6] == ["el freeze", "el at 3.00 1.00", "black",
                                       "el paint 3 1 22 SetTeamColor~36,92,255~36,92,255", "sky lightgrey 0"]  # Chaos's remains: as its ally
    assert sent[kill + 1:kill + 7] == ["el freeze", "el at 1.00 2.00", "black",
                                       "el paint 1 2 9 SetTeamColor~255,0,0~178,0,0", "sky lightgrey 0", "el clear"]  # Order's: as its enemy
    # Clearing: Order's core (not yet replaced) and anything left brought down, the scene shown
    # again, the remains and the fallen structures' own actors cleared, the holes shown, everything
    # let play a moment and paused again (the doodads shown again give off their particles).
    assert sent[-10:] == ["note Clearing the remains before the tiles", "el core 1 0", "el killall 5", "el env on", "el clear all", "el holes show", "el hideall", "el play",
                         "el freeze", "clean"]
    records = json.loads((tmp_path / "elements" / "elements.json").read_text())
    assert sorted(records) == ["camp-1-spawned", "structure-5-rubble", "structure-5-standing", "structure-6-rubble", "structure-6-standing"]
    assert records["structure-5-rubble"]["shot"] == "structure-5-rubble" and records["camp-1-spawned"]["tile"] == 1
    assert records["structure-6-standing"]["camera"] == {"x": 10.0, "y": 20.0} and records["structure-6-standing"]["neighbours"] == [5]
    assert frame_exists(tmp_path / "elements" / "structure-6-standing-with-5")
    assert frame_exists(tmp_path / "elements" / "structure-5-rubble") and frame_exists(tmp_path / "elements" / "structure-5-rubble-black")


def _lose_focus_after(element_capture, tmp_path, command: str) -> list[str]:
    """The elements capture with the focus lost at the first shot after `command` is typed, and the
    step done again; what was typed."""

    class Lost(Exception):
        pass

    class Session(_FakeSession):
        armed, done = False, False

        def send(self, text, timeout=2.0):
            if text == command and not Session.done:
                Session.armed = True
            return super().send(text, timeout)

        def grab(self, dark_ok=False):
            if dark_ok and Session.armed:
                Session.armed, Session.done = False, True
                raise Lost
            return super().grab(dark_ok)

    def step(action, what):
        while True:
            try:
                return action()
            except Lost:
                pass

    element_capture.step = step
    session = Session()
    element_capture.capture_elements(session, ELEMENTS_MANIFEST, tmp_path)
    return session.sent


def test_a_neighbour_s_shot_done_again_starts_from_what_is_really_shown(element_capture, tmp_path):
    """The focus lost with a structure's neighbour faded in beside it: the step done again fades that
    neighbour out in the command that moves the camera back, before it is faded in again."""
    sent = _lose_focus_after(element_capture, tmp_path, "el fades 3 1 1")
    lost = sent.index("el fades 3 1 1")
    assert sent[lost + 1:lost + 4] == ["el at 1.00 2.00 1 2 0 3 1 0 1 2 1", "black", "el fades 3 1 1"]


class _UnchangingSession(_FakeSession):
    """A side switch never shows."""

    def grab(self, dark_ok=False):
        return np.zeros((40, 60, 3), np.uint8)


@pytest.mark.parametrize("changes, sides", [(None, 1), (True, 1), (False, 0)])
def test_a_structure_is_shot_from_chaos_s_side_unless_the_calibration_saw_it_unchanged(element_capture, tmp_path, monkeypatch, changes, sides):
    """One switch to Chaos's side for the whole pass (none when nothing in it can change): a
    structure shot as from Order, with no wait for its colours to change (the switch long drawn);
    one the calibration saw unchanged not shot from Chaos's side at all. Its rubble is repainted,
    not switched, and shot the same way."""
    manifest = json.loads(json.dumps(ELEMENTS_MANIFEST))
    manifest["elements"]["structures"] = manifest["elements"]["structures"][:1]
    manifest["elements"]["camps"] = []
    if changes is not None:
        manifest["elements"]["structures"][0]["changesSide"] = changes
    session = _UnchangingSession()
    element_capture.capture_elements(session, manifest, tmp_path)
    assert session.sent.count("side 2") == sides
    record = json.loads((tmp_path / "elements" / "elements.json").read_text())["structure-5-standing"]
    assert record["chaos"] is None  # (nothing changed on this screen)


def test_a_shot_from_chaos_s_side_done_again_stays_on_chaos_s_side(element_capture, tmp_path):
    """The focus lost in the pass from Chaos's side: the step done again shoots it from there again,
    without switching back and forth; our player back on Order once after the pass."""
    sent = _lose_focus_after(element_capture, tmp_path, "side 2")
    pass_from_chaos = sent[sent.index("side 2"):sent.index("side 1") + 1]
    assert pass_from_chaos.count("side 2") == 1 and pass_from_chaos.count("el at 1.00 2.00 3 1 0 1 2 0 1 2 1") == 1
    assert pass_from_chaos.count("black") == 2 and sent.count("side 1") == 1


def _copies_manifest():
    manifest = json.loads(json.dumps(ELEMENTS_MANIFEST))
    manifest["elements"]["structures"][0]["copy"] = {"x": 100.0, "y": 100.0}
    manifest["elements"]["structures"].append({"id": 7, "type": "TownWallRadial2L3", "x": 30.5, "y": 30.5, "owner": "order", "town": 1,
                                               "core": False, "radius": 8.0, "copy": {"x": 140.5, "y": 60.5}})
    return manifest


def test_the_elements_capture_prepares_the_rubble_with_copies(element_capture, tmp_path, monkeypatch):
    """Each structure with a spare spot has its copy made there, then brought down a group at a time
    with the camera over the group, and everything paused once each has settled; then each copy's
    rubble shot on its spot with no waiting, recorded as if from its structure's cell (the camera
    moved by the difference), and only the rest (the core) falls in a wave."""
    monkeypatch.delenv(element_capture.COPY_TRYING, raising=False)
    monkeypatch.delenv(element_capture.COPY_CRASHED, raising=False)
    session = _FakeSession()
    element_capture.capture_elements(session, _copies_manifest(), tmp_path)
    sent = session.sent
    # Made first; their spots too far apart for one camera, each brought down in a group of its own
    # with the camera far back over it (numbered by group: the tower's spot leftmost), paused
    # before the next.
    assert [c for c in sent if c.startswith("el copy ")] == ["el copy 0 make 1 2 100 100", "el copy 1 make 30.5 30.5 140.5 60.5"]
    assert sent[sent.index("el copy 0 make 1 2 100 100") - 1] == "note Preparing rubble: making copies of 2 structures"
    first = sent.index("el copy 1 make 30.5 30.5 140.5 60.5") + 1
    assert sent[first:first + 8] == ["note Preparing rubble: bringing down copies of the structures, group 1 of 2",
                                     "el wide 100.00 100.00 212.5", "el copykill 0 0", "el freeze",
                                     "note Preparing rubble: bringing down copies of the structures, group 2 of 2",
                                     "el wide 140.50 60.50 212.5", "el copykill 1 1", "el freeze"]
    assert sent.index("el fadeall 0") < first < next(i for i, c in enumerate(sent) if c.startswith("el at 1.00 2.00"))
    tower = sent.index("el at 100.00 100.00")
    assert sent[tower + 1:tower + 4] == ["black", "el paint 100 100 9 SetTeamColor~255,0,0~178,0,0", "sky lightgrey 0"]
    assert sent[sent.index("el at 140.50 60.50") + 2].startswith("el paint 140.5 60.5 ")
    assert not any(c.startswith("el kill ") for c in sent) and "el core 2 -1" in sent
    records = json.loads((tmp_path / "elements" / "elements.json").read_text())
    # The fake camera is at (10, 20) for every shot: as if from the tower's cell, (10 + 1 - 100, 20 + 2 - 100).
    assert records["structure-5-rubble"]["camera"] == {"x": -89.0, "y": -78.0}
    assert records["structure-5-rubble"]["chaos"] == "structure-5-rubble-chaos"  # its team colour, as standing's
    assert records["structure-5-rubble"]["x"] == 1 and records["structure-7-rubble"]["tile"] == 1  # its structure's tile
    assert "structure-6-rubble" in records


def test_no_shot_is_taken_while_a_note_is_up(element_capture, tmp_path, monkeypatch):
    """A note (the line at the top of the screen while the run waits) is up only until the next
    "el at" or "tile", which the script takes it down for: every shot comes after one of them."""
    monkeypatch.delenv(element_capture.COPY_TRYING, raising=False)
    monkeypatch.delenv(element_capture.COPY_CRASHED, raising=False)

    class Session(_FakeSession):
        def grab(self, dark_ok=False):
            if dark_ok:  # the capture's shots (an answer's frame isn't one)
                self.sent.append("<shot>")
            return super().grab(dark_ok)

    for n, manifest in enumerate((ELEMENTS_MANIFEST, _copies_manifest())):
        session, base = Session(), tmp_path / str(n)
        base.mkdir()
        element_capture.capture_elements(session, manifest, base)
        assert any(c.startswith("note ") for c in session.sent) and "<shot>" in session.sent
        up = False
        for command in session.sent:
            if command.startswith("note "):
                up = True
            elif command.startswith(("el at ", "tile ")):
                up = False
            elif command == "<shot>":
                assert not up, session.sent


def test_a_type_whose_copy_crashed_the_game_falls_in_waves_after_the_relaunch(element_capture, tmp_path, monkeypatch):
    """The type being copied when the game went (left in the environment the recovery's process
    inherits) isn't copied again: it falls in waves."""
    monkeypatch.setenv(element_capture.COPY_TRYING, "TownWallRadial2L3")
    monkeypatch.delenv(element_capture.COPY_CRASHED, raising=False)
    session = _FakeSession()
    element_capture.capture_elements(session, _copies_manifest(), tmp_path)
    assert [c for c in session.sent if c.startswith("el copy ")] == ["el copy 0 make 1 2 100 100"]
    assert "el kill 30.5 30.5 -1" in session.sent
    assert element_capture.COPY_TRYING not in os.environ and os.environ[element_capture.COPY_CRASHED] == "TownWallRadial2L3"


def test_copy_spots_keep_clear_on_the_structure_s_lighting():
    """Each copy's spot: its circle clear of every structure's, camp's and other spot's, under the
    lighting its structure has (the lighting map: a left half red, a right half green), on the
    same part of a cell; none for cores and keeps."""
    import math

    import numpy as np

    from heroes_capture.categories.battleground import elements

    light = np.zeros((200, 400, 4), np.float32)  # 2 pixels a cell, 200 x 100 cells
    light[:, :200, 0] = 255
    light[:, 200:, 1] = 255
    structures = [{"id": 1, "type": "TownCannonTowerL2", "x": 30, "y": 50, "core": False, "radius": 8.0},
                  {"id": 2, "type": "TownWallRadial2L3", "x": 170.5, "y": 50.5, "core": False, "radius": 8.0},
                  {"id": 3, "type": "KingsCore", "x": 100, "y": 50, "core": True, "radius": 8.0},
                  {"id": 4, "type": "TownTownHallL3", "x": 60, "y": 20, "core": False, "radius": 8.0}]
    spots = elements.copy_spots(structures, [], {"left": 0, "bottom": 0, "right": 200, "top": 100}, 200, light)
    assert set(spots) == {1, 2}
    assert spots[1]["x"] < 96 and spots[2]["x"] > 104  # each on its own half
    assert spots[2]["x"] % 1 == 0.5 and spots[2]["y"] % 1 == 0.5
    # Each spot's rubble circle (a tower's 4 cells, a wall's 6) clear of every structure's (8) and
    # of the other's.
    for i, s in spots.items():
        assert all(math.dist((s["x"], s["y"]), (u["x"], u["y"])) >= 8 + elements.rubble_radius(structures[i - 1]) for u in structures)
    assert math.dist(*[(s["x"], s["y"]) for s in spots.values()]) >= 4 + 6


def test_a_camp_near_a_structure_is_spawned_after_the_structures_are_shot(element_capture, tmp_path):
    """A camp whose circle reaches a structure's would be in that structure's cut-out: it isn't
    spawned with the scene hidden but after every structure's standing shot."""
    manifest = json.loads(json.dumps(ELEMENTS_MANIFEST))
    manifest["elements"]["camps"][0].update(x=8, y=2)
    session = _FakeSession()
    element_capture.capture_elements(session, manifest, tmp_path)
    sent = session.sent
    assert sent.index("el camp 1") > sent.index("el fades 3 1 0") and sent[1:4] == ["el fadeall 0", "el env off", "el at 1.00 2.00 1 2 1"]


def test_a_resumed_elements_capture_only_brings_the_structures_down(element_capture, tmp_path):
    element_capture.capture_elements(_FakeSession(), ELEMENTS_MANIFEST, tmp_path)
    again = _FakeSession()
    element_capture.capture_elements(again, ELEMENTS_MANIFEST, tmp_path)
    assert again.sent == ["tile 0 0.00 0.00", "el fadeall 0", "el env off", "note Clearing the remains before the tiles", "el core 1 0", "el core 2 0", "el killall 5",
                          "el env on", "el clear all", "el holes show", "el hideall", "el play", "el freeze", "clean"]


def test_rubble_waves_keep_each_one_s_rubble_out_of_the_others_shots():
    """No two structures in a wave nearer each other than their circles reach together; each core
    in a wave of its own, last."""
    from heroes_capture.categories.battleground import elements

    def u(i, x, y, core=False):
        return {"id": i, "x": x, "y": y, "radius": 8.0, "core": core}

    town = [u(1, 0, 0), u(2, 5, 0), u(3, 10, 0), u(4, 40, 0), u(5, 45, 0)]
    waves = elements.rubble_waves(town + [u(9, 100, 100, core=True), u(10, 200, 100, core=True)])
    assert [[s["id"] for s in w] for w in waves] == [[1, 4], [2, 5], [3], [9], [10]]
    for w in waves[:-2]:
        assert all(abs(a["x"] - b["x"]) >= 16 for a in w for b in w if a is not b)


def test_each_structure_is_restarted_and_paused_at_its_standing_time(element_capture, tmp_path):
    """Each structure, faded in under the camera, started over ("el restart": its animations from
    the start, its effects lit by the camera) and paused at its kind's standing time before its
    shots, in the same command (its seconds: one round trip): the same moment every run."""
    session = _FakeSession()
    element_capture.capture_elements(session, ELEMENTS_MANIFEST, tmp_path)
    seq = session.everything
    first = seq.index("el at 1.00 2.00 1 2 1")
    assert seq[first + 1:first + 3] == ["el restart 1 2 - 2", "black"]  # restarted and paused in one command
    assert sum(c.startswith("el restart 3 1 ") for c in seq) == 1 and not any(c.startswith("el hold ") for c in seq)


def test_a_structure_runs_its_kind_s_picked_standing_time():
    from heroes_capture.categories.battleground import elements

    tower = {"type": "TownCannonTowerL3", "owner": "chaos"}
    assert elements.stand_wait(tower, {"TownCannonTowerL3": 3.25}) == 3.25  # by type: both teams alike
    assert elements.stand_wait(tower) == elements.LIGHT_WAIT


def test_the_elements_capture_gives_up_on_a_map_that_stops_answering(element_capture, tmp_path):
    session = _FakeSession(unanswered={"tile 0 0.00 0.00", "el fadeall 0", "el env off"})
    with pytest.raises(_Recoverable) as lost:
        element_capture.capture_elements(session, ELEMENTS_MANIFEST, tmp_path)
    assert lost.value.resume_at == 0


def test_a_team_colour_mask_rebuilds_the_other_side_and_any_colours():
    """team_colour: a pixel of Order's team half blue from Order's side is half red from Chaos's;
    one of Chaos's team the other way round; a pixel unchanged keeps no team colour. The mask
    (Order's team's amount, Chaos's) gives back the Chaos picture with the colours swapped, and the
    picture itself with them as shot."""
    import numpy as np

    from heroes_capture.capture.team_colour import MASK_SCALE, TEAM_COLOURS, recoloured, team_mask

    blue, red = np.array(TEAM_COLOURS["order"], float), np.array(TEAM_COLOURS["chaos"], float)
    base = np.array([60.0, 50.0, 40.0])
    order = np.zeros((1, 3, 4), np.uint8)
    chaos = np.zeros((1, 3, 4), np.uint8)
    order[0, 0, :3], chaos[0, 0, :3] = base + 0.5 * blue * 0.6, base + 0.5 * red * 0.6  # Order's team, 0.3 of the colour
    order[0, 1, :3], chaos[0, 1, :3] = base + 0.4 * red * 0.6, base + 0.4 * blue * 0.6  # Chaos's team, 0.24
    order[0, 2, :3] = chaos[0, 2, :3] = base  # no team colour
    order[..., 3] = chaos[..., 3] = 255
    mask = team_mask(order, chaos)
    assert abs(mask[0, 0, 0] / 255 * MASK_SCALE - 0.3) < 0.02 and mask[0, 0, 1] == 0
    assert abs(mask[0, 1, 1] / 255 * MASK_SCALE - 0.24) < 0.02 and mask[0, 1, 0] == 0
    assert not mask[0, 2].any()
    swapped = recoloured(order, mask, {"order": TEAM_COLOURS["chaos"], "chaos": TEAM_COLOURS["order"]})
    assert np.abs(swapped.astype(int) - chaos).max() <= 3
    assert (recoloured(order, mask, TEAM_COLOURS) == order).all()
    assert team_mask(order, order) is None


def test_team_coloured_doodads_are_those_of_a_team_s_computer():
    """The doodads drawn in a team's colours (Objects: TeamColor 11 or 12, the teams' computer
    players), by cell; the neutral ones (0) and a hero player's (1) don't change with our side."""
    from heroes_capture.map.prepare import team_coloured

    objects = """<PlacedObjects>
    <ObjectDoodad Id="1" Position="103.8298,127.17,7.9375" Scale="1,1,1" Type="Storm_Doodad_Heaven1_Jungle_A" TeamColor="11">
    <ObjectDoodad Id="2" Position="20,30,1" Scale="1,1,1" Type="Storm_Doodad_Hell_CoreRange" TeamColor="12"/>
    <ObjectDoodad Id="3" Position="5,6,0" Scale="1,1,1" Type="Storm_Doodad_Heaven_MercCampProps" TeamColor="0">
    <ObjectDoodad Id="4" Position="7,8,0" Scale="1,1,1" Type="Storm_Doodad_Heaven_CurbThin" TeamColor="1">
    <ObjectDoodad Id="5" Position="9,9,0" Scale="1,1,1" Type="Storm_Doodad_Heaven_Tree">
    <ObjectUnit Id="6" Position="10,10,0" Scale="1,1,1" UnitType="TownCannonTowerL2" Player="11"/>
    </PlacedObjects>"""
    assert team_coloured(objects) == [[103.83, 127.17], [20.0, 30.0]]


def test_the_elements_render_has_its_own_files_and_pack_folder(tmp_path):
    """--structures elements is a render of its own (ELEMENTS-PLAN.md): its preparation's files and
    its pack (maps/<map>/elements/) never overwrite the render with the structures kept."""
    from heroes_capture.capture import pack, prepare

    assert prepare.render_id("Battlefield of Eternity", "elements") == "battlefield-of-eternity-elements"
    assert prepare.render_id("Battlefield of Eternity", "keep") == "battlefield-of-eternity-structures"
    assert pack.variant_folder(tmp_path, "elements") == tmp_path / "elements" and pack.variant_folder(tmp_path, "keep") == tmp_path
    assert prepare.parse_args(["Dragon Shire", "--structures", "elements"])["structures"] == "elements"
    with pytest.raises(SystemExit, match="keep, hide or elements"):
        prepare.parse_args(["Dragon Shire", "--structures", "some"])


def test_the_script_reads_the_number_of_its_longest_commands():
    """A command's sequence number is its last word: the script must reach it in the longest command
    sent ("el copy <n> make <x> <y> <to x> <to y> <number>": 9 words), or it answers with another
    word and the capture, hearing no answer, sends it again (four copies made)."""
    import re

    from heroes_capture.map import script as cs

    template = cs.PART.read_text(encoding="utf-8")
    seq = template[template.index("int hrsCap_CommandSeq ()"):template.index("void hrsCap_Ack ()")]
    words = template[template.index("string hrsCap_CommandWords (int lp_from)"):]
    reach = int(re.search(r"lv_n <= (\d+)", seq).group(1))
    assert reach >= 9 and reach >= int(re.search(r"lv_n <= (\d+)", words).group(1)) + 1



def test_a_cut_out_keeps_a_tinted_glow_see_through():
    """A core's shield: over white a little bluer than the sky, over black dim blue (a difference
    that isn't grey). In a tile that is taken for something that moved between the shots and left
    opaque; in an element's cut-out (nothing moves) it stays the faint glow it is, not an opaque
    dark blotch."""
    import numpy as np

    from heroes_capture.categories.battleground import elements
    from heroes_capture.capture import stitch

    white = np.full((40, 40, 3), 198, np.uint8)  # the light sky (too little of it to measure: LIGHT_LEVEL)
    black = np.zeros((40, 40, 3), np.uint8)
    white[18:23, 18:23], black[18:23, 18:23] = (193, 212, 213), (13, 35, 58)  # a patch of glow (a speck isn't taken for moving)
    assert matting.matte(white, black, 198.0)[20, 20, 3] == 255
    cut = matting.cut_out(white, black, (20, 20), 10)
    assert 0 < cut[20, 20, 3] < 60 and cut[20, 20, :3].max() > 200


def test_a_thin_rim_that_differs_between_the_shots_isn_t_taken_for_moving():
    """Something that moved between the shots is a patch, taken for moving and left opaque; a
    one-pixel rim along an edge that differs by a hair between them (what was left once the map was
    frozen and the light sky a grey) keeps its matte, its soft edge."""
    from heroes_capture.capture import stitch

    white = np.full((60, 60, 3), 198, np.uint8)  # the light sky
    black = np.zeros((60, 60, 3), np.uint8)
    white[30, 5:55], black[30, 5:55] = (150, 130, 100), (60, 50, 40)  # a rim: half see-through, its difference tinted
    white[5:15, 5:15], black[5:15, 5:15] = (220, 120, 60), (20, 20, 20)  # a patch that moved
    moved = matting.moved_pixels(white, black)
    assert moved[5:15, 5:15].all() and not moved[30].any()
    rgba = matting.matte(white, black, 198.0)
    assert (rgba[5:15, 5:15, 3] == 255).all()  # the patch: opaque
    assert (rgba[30, 5:55, 3] < 200).all()  # the rim: still see-through


def test_a_seam_takes_a_floating_piece_from_one_screenshot_not_a_ghost_of_both():
    """At a seam's feather: where the two screenshots agree, blended by opacity (a see-through
    pixel's black doesn't darken the other); where one has a piece above the ground and the other
    the sky there (the piece seen shifted), the one weighing more taken whole, not a dark
    see-through copy (the "shadows" at the map's edges)."""
    import numpy as np

    from heroes_capture.capture import stitch

    under = np.array([[[200, 160, 40, 255], [200, 160, 40, 255], [100, 100, 100, 128]]], np.uint8)
    over = np.array([[[0, 0, 0, 0], [0, 0, 0, 0], [100, 100, 100, 160]]], np.uint8)
    weight = np.array([[0.3, 0.7, 0.5]], np.float32)
    out = stitch.seam_blend(under, over, weight)
    assert tuple(out[0, 0]) == (200, 160, 40, 255)  # the piece, whole (it weighs more)
    assert out[0, 1, 3] == 0  # the sky, whole
    assert tuple(out[0, 2, :3]) == (100, 100, 100) and out[0, 2, 3] == 144  # agreeing: blended, no darkening

def test_view_groups_fit_one_camera_each():
    """Items in groups that each fit the box round its middle, every item in one group."""
    from heroes_capture.categories.battleground import elements

    items = [{"x": x, "y": y} for x, y in ((0, 0), (10, 5), (25, 0), (100, 0), (5, 40), (110, 8))]
    groups = elements.view_groups(items, 15, 10)
    assert sorted(len(g) for g in groups) == [1, 2, 3] and sum(len(g) for g in groups) == len(items)
    for g in groups:
        cx, cy = elements.view_middle(g)
        assert all(abs(i["x"] - cx) <= 15 and abs(i["y"] - cy) <= 10 for i in g)


def test_each_kind_on_each_team_is_shot_at_its_own_time(element_capture):
    """fall_wait by kind and team (the film probe's choices): a Heaven and a Hell moonwell of the
    same level differ; a level 3 town tower isn't taken for a standalone one; walls by level."""
    w = element_capture.fall_wait
    assert w({"type": "TownMoonwellL3", "owner": "order", "core": False}) == 3.30
    assert w({"type": "TownMoonwellL3", "owner": "chaos", "core": False}) == 1.92
    assert w({"type": "TownCannonTowerL3", "owner": "chaos", "core": False}) == 2.25
    assert w({"type": "TownCannonTowerL3Standalone", "owner": "chaos", "core": False}) == 3.00
    assert w({"type": "TownWallRadial17L2", "owner": "chaos", "core": False}) == 1.33
    assert w({"type": "TownWallRadial14L3", "owner": "chaos", "core": False}) == 2.24
    assert w({"type": "KingsCore", "owner": "chaos", "core": True}) == 4.8  # elements.CORE_WAIT


def test_the_game_draws_at_most_5_to_4_narrow():
    from heroes_capture.common.monitors import Monitor, game_size

    assert game_size(3440, 1440) == (3440, 1440)
    assert game_size(1680, 1440) == (1680, 1344)  # the game letterboxed a 7:6 monitor to 5:4
    assert game_size(1080, 1920) == (1080, 864)  # a portrait monitor gets a 5:4 picture its width wide
    narrow = Monitor(3, "\\\\.\\DISPLAY3", 3440, 0, 1680, 1440, False)
    assert narrow.game_size == "1680x1344"
    assert narrow.game_rect() == (3440, 48, 1680, 1344)  # centred, as the game put it
    wide = Monitor(4, "\\\\.\\DISPLAY4", 0, 0, 3440, 1440, True)
    assert wide.game_rect() == (0, 0, 3440, 1440)


def test_the_interface_scales_to_the_width_on_a_narrow_screen():
    from heroes_capture.common.game_state import interface_height

    assert interface_height(3440, 1440) == 1440
    assert interface_height(1680, 1344) == 1260  # 1600 units across 1680 px: 1.05 px a unit, 1200 units tall
    assert interface_height(1920, 1440) == 1440  # 4:3 exactly: either way


def test_the_narrow_menu_layout_is_recognised():
    """The main menu as the game draws it at 1680x1344 (a 5:4 picture on a 7:6 monitor): a
    different layout from the 16:9 one, kept at half size from a run where the usual
    reference found 1 part of 3. The frame is put back to full size for the check."""
    from pathlib import Path

    from PIL import Image

    from heroes_capture.common.game_state import menu_matches

    half = Image.open(Path(__file__).with_name("fixtures") / "menu-narrow-1680x1344-half.png").convert("RGB")
    frame = np.asarray(half.resize((1680, 1344), Image.BILINEAR), dtype=np.uint8)
    details: list[str] = []
    assert menu_matches(frame, details) == 3, details


def test_a_galaxy_script_s_functions_are_read_with_their_braces_balanced():

    script = """
void libGame_gf_TownDestroyed (int lp_town) {
    // Variable Declarations
    if (true) {
        UnitGroupLoopBegin(RegionUnits(lv_townRegion));
    }
}

int libGame_gf_TownCount () {
    return 3;
}

bool libGame_gf_StandaloneTowerDeath (unit lp_unit) {
    return UnitGetType(lp_unit) == "TownCannonTowerL3Standalone";
}
"""
    assert set(functions(script)) == {"libGame_gf_TownDestroyed", "libGame_gf_TownCount", "libGame_gf_StandaloneTowerDeath"}
    assert functions(script)["libGame_gf_TownCount"].endswith("return 3;\n}")


def test_the_game_s_script_names_are_found_in_its_program():
    from heroes_capture.common import game_data

    program = b"\x00\x12GamePause\x00EnvironmentShow\x00c_actorIntersectAgainstCenter\x00junk\x01AnimSetTimeScaleGlobal\x00ab\x00"
    assert game_data.script_names(program, "pause|timescale") == ["AnimSetTimeScaleGlobal", "GamePause"]
    assert game_data.script_names(program, "^c_actor") == ["c_actorIntersectAgainstCenter"]


def test_models_the_map_defines_itself_are_frozen_in_their_own_entries():
    """A second entry with the same id didn't take (Dragon Shire's forts burned on while paused):
    the freeze settings go into the map's own entry (the first, when several); only the models it
    doesn't define get entries appended."""
    from heroes_capture.capture import sky

    catalog = ('<Catalog>\r\n    <CModel id="Fort"><Model value="fort.m3"/></CModel>\r\n    <CModel id="Gate" parent="Building"/>\r\n'
               '    <CModel id="Fort"><Radius value="2"/></CModel>\r\n    <CModel id="FortDamage"/>\r\n</Catalog>\r\n')
    frozen, rest = sky.freeze_in_place(catalog, ["Fort", "Gate", "Tower"])
    assert rest == ["Tower"]
    assert f'<CModel id="Fort">{sky.FREEZE}<Model value="fort.m3"/></CModel>' in frozen
    assert f'<CModel id="Gate" parent="Building">{sky.FREEZE}</CModel>' in frozen
    assert frozen.count(sky.FREEZE) == 2 and '<CModel id="FortDamage"/>' in frozen  # not asked for: untouched
    files = dict(sky.sky_files("T", "black", lambda name: catalog if name.endswith("ModelData.xml") else None, {"models": [], "files": [], "freeze": ["Fort", "Tower"]}))
    data = files["Base.StormData\\GameData\\ModelData.xml"].decode()
    assert f'<CModel id="Fort">{sky.FREEZE}<Model' in data and f'<CModel id="Tower">{sky.FREEZE}</CModel>' in data
    assert sky.freeze_in_place(None, ["Fort"]) == (None, ["Fort"])



def _water_files():
    """A 16 x 8 map: one water (height 7) painted over x 0..8, y 0..8, its terrain 4 (under the
    water) on x < 4 and 8 elsewhere; t3HeightMap's raw heights as raw * 0.001 - 1."""
    import struct

    entry = b"PondWater".ljust(32, b"\0") + bytes(8) + struct.pack("<4f", 0, 0, 8, 8)
    t3water = b"WATR" + struct.pack("<II", 110, 1) + bytes(20) + entry
    vw, vh = 17, 9
    heights = np.full((vh, vw), 8.0)
    heights[:, :5] = 4.0  # corners x 0..4: cells x 0..3 all under, x 4 has two corners under
    raw = np.rint((heights + 1) / 0.001).astype("<u2")
    records = np.zeros((vh, vw, 3), "<u2")
    records[..., 0] = raw
    heightmap = b"HMAP" + struct.pack("<III", 101, vw, vh) + bytes(16) + records.tobytes()
    terrain = '<terrain><vertData quantizeBias="1.0" quantizeScale="0.001" standardHeight="8"/></terrain>'
    water_data = '<Catalog><CWater id="PondWater"><State index="0"><Height value="7.000000"/></State></CWater></Catalog>'
    return t3water, heightmap, terrain, water_data


def test_a_map_s_water_cells_come_from_its_water_rectangles_and_terrain():
    """A cell is water inside its water's rectangle where its lowest corner is under the water's
    height (map/water.py); without the height, the whole rectangle."""
    from heroes_capture.map import water

    t3water, heightmap, terrain, water_data = _water_files()
    assert water.water_rects(t3water) == [("PondWater", 0, 0, 8, 8)]
    assert water.water_heights(["<Catalog/>", water_data]) == {"PondWater": 7.0}
    wet = water.water_cells(t3water, heightmap, terrain, [water_data], 16, 8)
    assert wet.shape == (8, 16) and wet[:, :5].all() and not wet[:, 5:].any()
    assert water.water_cells(t3water, heightmap, terrain, [], 16, 8)[:, :8].all()  # no height: the rectangle
    assert not water.water_cells(None, heightmap, terrain, [water_data], 16, 8).any()
    assert water.water_runs(wet, grow=0)[:2] == [[0, 0, 5], [1, 0, 5]]
    assert water.water_runs(wet, grow=1)[0] == [0, 0, 6]


def test_in_the_water_only_changes_along_the_team_colours_count():
    """The water's pixels on the canvas (cell (x, y) at (ax + x * scale, ay - y * scale)); there a
    change between the sides counts as team colour only along the teams' colours: moving foam
    (brighter) doesn't, a roof over the water swapping blue for red does."""
    from heroes_capture.capture import stitch
    from heroes_capture.capture.team_colour import TEAM_COLOURS
    from heroes_capture.map import water

    wet = water.water_pixels((40, 60), [[0, 2, 4]], 5.0, 10.0, 40.0)  # cells x 2..3, y 0: pixels x 20..30, y 35..40
    assert wet[35:40, 20:30].all() and wet.sum() == 50
    order = np.full((40, 60, 4), 255, np.uint8)
    order[..., :3] = (80, 90, 100)
    chaos = order.copy()
    chaos[36, 22, :3] = (160, 170, 180)  # foam, in the water
    chaos[36, 40, :3] = (160, 170, 180)  # the same change on land (no water there)
    order[38, 25, :3], chaos[38, 25, :3] = TEAM_COLOURS["order"], TEAM_COLOURS["chaos"]  # a roof over the water
    mask = stitch.canvas_team_mask(order, chaos, wet=wet)
    assert not mask[36, 22].any() and mask[36, 40].any() and mask[38, 25, 0] > 0


def test_a_fort_s_standalone_tower_falls_in_a_wave_before_its_fort():
    """GameLib takes the standalone tower down with its fort: brought down after it, nothing is left."""
    from heroes_capture.categories.battleground import elements

    fort = {"id": 1, "type": "TownTownHallL2", "x": 100, "y": 100}
    tower = {"id": 2, "type": "TownCannonTowerL3Standalone", "x": 113.5, "y": 100}
    far = {"id": 3, "type": "TownCannonTowerL3Standalone", "x": 160, "y": 100}  # near no fort: by its circle
    radii = {1: 9.2, 2: 7.1, 3: 7.1}
    waves = elements.rubble_waves([fort, tower, far], radius=lambda u: radii[u["id"]])
    assert [[u["id"] for u in w] for w in waves] == [[2, 3], [1]]


def test_a_copy_that_falls_to_nothing_leaves_its_structure_to_the_waves(element_capture, tmp_path, monkeypatch):
    """Its remains' circle shows nothing but the light sky: not recorded, and the structure itself is
    brought down in a wave (Dragon Shire's copies near the map's edges, now and then)."""
    monkeypatch.delenv(element_capture.COPY_TRYING, raising=False)
    monkeypatch.delenv(element_capture.COPY_CRASHED, raising=False)

    class _EmptySession(_FakeSession):
        """The light sky everywhere, on the small screen below, the camera where it was last sent."""
        at = {"x": 0.0, "y": 0.0}

        def send(self, command, timeout=2.0):
            if command.startswith("el at "):
                self.at = {"x": float(command.split()[2]), "y": float(command.split()[3])}
            return super().send(command, timeout)

        def camera(self, default=None):
            return dict(self.at)

        def grab(self, dark_ok=False):
            return np.full((40, 60, 3), int(element_capture.LIGHT_LEVEL), np.uint8)

    manifest = _copies_manifest()
    manifest["screen"], manifest["pxPerCell"] = {"w": 60, "h": 40}, 2
    session = _EmptySession()
    element_capture.capture_elements(session, manifest, tmp_path)
    assert "el kill 1 2 -1" in session.sent  # the tower (its copy fell to nothing) brought down itself
    assert sum(c.startswith("note Rubble wave") for c in session.sent) >= 2


def test_a_fort_s_standalone_tower_gets_no_copy():
    """Left standing while its copy falls, it falls with its fort (GameLib), into the fort's shot."""
    from heroes_capture.categories.battleground import elements

    structures = [{"id": 1, "type": "TownTownHallL2", "x": 60, "y": 60, "radius": 8.0, "core": False},
                  {"id": 2, "type": "TownCannonTowerL3Standalone", "x": 73.5, "y": 60, "radius": 8.0, "core": False},
                  {"id": 3, "type": "TownCannonTowerL3Standalone", "x": 60, "y": 120, "radius": 8.0, "core": False}]
    assert [elements.goes_with_a_fort(u, structures) for u in structures] == [False, True, False]
    found = elements.copy_candidates(structures, [], {"left": 0, "bottom": 0, "right": 200, "top": 200}, 200, None)
    assert 2 not in found and 3 in found


def test_the_data_report_says_what_has_team_colour_and_what_can_overlap():
    """From the models (actors.report): a structure's finished model's team colour standing; its
    remains' from its own death models (a tower's only the shared one: no say); two structures can
    overlap on screen when nearer than their models reach, as the capture camera sees them."""
    from heroes_capture.categories.battleground import elements

    report = {"standModels": {"Fort": "FortModel", "Wall": "WallModel", "Tower": "TowerModel"},
              "deathModels": {"Fort": ["FortDeath"], "Wall": [elements.GENERIC_DEATH, "WallDeath00"], "Tower": [elements.GENERIC_DEATH]},
              "models": {"FortModel": {"teamColour": {"Stand": True}, "extent": {"reach": 3.0, "height": 6.0}, "scale": 1.0},
                         "WallModel": {"teamColour": {"Stand": False}, "extent": {"reach": 3.0, "height": 2.0}, "scale": 1.0},
                         "TowerModel": {"teamColour": {}, "extent": None},
                         "FortDeath": {"teamColour": {"Death": True, "Dead": True}},
                         "WallDeath00": {"teamColour": {"Death": False, "Dead": False}},
                         elements.GENERIC_DEATH: {"teamColour": {"Death": False}}}}
    fort, wall, tower = ({"type": t, "x": x, "y": 0} for t, x in (("Fort", 0), ("Wall", 6), ("Tower", 30)))
    assert [elements.standing_team_colour(u, report) for u in (fort, wall, tower)] == [True, False, None]
    assert [elements.rubble_team_colour(u, report) for u in (fort, wall, tower)] == [True, False, None]
    assert elements.stand_reach(fort, report, 85.0) == pytest.approx(3.0 * 85 / 79)
    assert elements.may_overlap(fort, wall, report, 85.0, 10.0)  # 6 apart, reaching 3.2 + 3.1
    far = {"type": "Wall", "x": 7, "y": 0}
    assert not elements.may_overlap(fort, far, report, 85.0, 10.0)  # 7 apart: more than both reach
    assert elements.may_overlap(far, {"type": "Tower", "x": 15, "y": 0}, report, 85.0, 10.0)  # unknown: within 10


def test_the_survey_reports_what_the_data_says_for_each_team():
    """Step 1 (categories/battleground/survey.py): each kind's team colour standing and in its
    remains per team where the data picks each team's model (Battlefield of Eternity's Heaven walls
    draw team colour, its Hell walls don't), so no side switch to time, and a look filmed per team."""
    from heroes_capture.categories.battleground import survey

    report = {"names": {"TownWallRadial2L3": "Wall"}, "standModels": {"TownWallRadial2L3": "WallRadialHell2"},
              "deathModels": {"TownWallRadial2L3": ["WallDeath"]},
              "teams": {"TownWallRadial2L3": {"order": {"stand": "WallRadialHeaven2", "deaths": ["WallDeath"]},
                                              "chaos": {"stand": "WallRadialHell2", "deaths": ["WallDeath"]}}},
              "models": {"WallRadialHeaven2": {"teamColour": {"Stand": True}, "sequences": [{"name": "Stand", "seconds": 1}]},
                         "WallRadialHell2": {"teamColour": {"Stand": False}, "sequences": [{"name": "GLstand", "seconds": 6.667}]},
                         "WallDeath": {"teamColour": {"Death": False, "Dead": False}, "sequences": [{"name": "Death", "seconds": 6.667}]}}}
    walls = [{"id": n, "type": "TownWallRadial2L3", "name": "Wall", "x": 10 + 40 * n, "y": 10, "owner": owner, "town": 1, "core": False, "radius": 3}
             for n, owner in enumerate(("order", "chaos"))]
    manifest = {"map": "Test", "mapSize": {"width": 100, "height": 80}, "sky": {"mode": "black"}, "tiles": [{}] * 4, "distance": 85.0,
                "elements": {"structures": walls, "towns": [{}], "camps": [], "outposts": [], "report": report, "copyCandidates": {"0": [[1, 1]]}}}
    found = survey.survey(manifest)
    [wall] = found["kinds"]
    assert wall["standing"] == {"order": True, "chaos": False} and wall["remains"] == {"order": False, "chaos": False}
    assert wall["fall"] == [6.7] and wall["byTeam"]
    text = "\n".join(survey.lines(found))
    assert "blue yes, red no" in text and "side switch: none to time" in text and "standing filmed: 2 looks" in text


def test_a_look_that_differs_by_team_is_filmed_and_timed_per_team():
    """elements.stand_kind: by type where both teams show the same finished model, by type and team
    where they differ (Battlefield of Eternity's Heaven and Hell); a standing time or restart model
    kept that way is found for each structure (by_stand_kind), its type's otherwise."""
    from heroes_capture.categories.battleground import elements

    report = {"standModels": {"Fort": "HellFort", "Tower": "Tower"},
              "teams": {"Fort": {"order": {"stand": "HeavenFort", "deaths": []}, "chaos": {"stand": "HellFort", "deaths": []}}}}
    blue, red, tower = {"type": "Fort", "owner": "order"}, {"type": "Fort", "owner": "chaos"}, {"type": "Tower", "owner": "chaos"}
    assert [elements.stand_kind(u, report) for u in (blue, red, tower)] == ["Fort|order", "Fort|chaos", "Tower"]
    assert elements.stand_model(blue, report) == "HeavenFort" and elements.stand_model(tower, report) == "Tower"
    waits = {"Fort|order": 2.5, "Fort|chaos": 0.0, "Tower": 1.0}
    assert [elements.stand_wait(u, waits) for u in (blue, red, tower)] == [2.5, 0.0, 1.0]  # (0 s is a time, not none)


def test_a_copy_of_a_kind_whose_remains_the_variation_picks_gets_variation_0_first():
    """A gate's (and a wall's) normal death is empty: its remains come from a death variation picked
    by player AND model variation ("UnitDeathCustomize; PlayerId 11 11; ModelVariation 0"), so a copy
    the game makes, with none of those variations, falls to nothing (Battlefield of Eternity's gate
    copies). The data report names such types; their copies get their finished model with
    variation 0 (ModelSwap's second word, as Blizzard's own data swaps it) before they fall."""
    from heroes_capture.categories.battleground import elements
    from heroes_capture.common.actors import Catalog

    catalog = Catalog(['''<Catalog>
      <CActorUnit id="Gate" unitName="Gate">
        <On Terms="UnitDeathCustomize; PlayerId 11 11; ModelVariation 0" Send="DeathCustomize Variation0"/>
        <DeathArray index="Normal" ModelLink=""/>
      </CActorUnit>
      <CActorUnit id="Tower" unitName="Tower"><On Terms="UnitDeathCustomize; PlayerId 11 11" Send="DeathCustomize Variation0"/></CActorUnit>
    </Catalog>'''])
    assert catalog.death_by_variation("Gate") and not catalog.death_by_variation("Tower")
    report = {"standModels": {"Gate": "GateHell", "Tower": "Tower"}, "variationDeaths": ["Gate"],
              "teams": {"Gate": {"order": {"stand": "GateHeaven", "deaths": []}}}}
    assert elements.death_setup({"type": "Gate", "owner": "order"}, report) == "ModelSwap~GateHeaven~0"
    assert elements.death_setup({"type": "Gate", "owner": "chaos"}, report) == "ModelSwap~GateHell~0"
    assert elements.death_setup({"type": "Tower", "owner": "order"}, report) is None


def test_a_structure_whose_remains_the_variation_picks_gets_variation_0_before_it_falls(element_capture, tmp_path):
    """Brought down where it stands too: its variation as the map placed it may be none its deaths
    name (Battlefield of Eternity's gates in variations 2 and 3: they fell to nothing), so it is set
    to 0 ("el msg") before its kill, in a command of its own."""
    manifest = json.loads(json.dumps(ELEMENTS_MANIFEST))
    manifest["elements"]["report"] = {"standModels": {"TownCannonTowerL2": "TowerModel"}, "variationDeaths": ["TownCannonTowerL2"]}
    session = _FakeSession()
    element_capture.capture_elements(session, manifest, tmp_path)
    sent = session.sent
    setup, kill = sent.index("el msg 1 2 ModelSwap~TowerModel~0"), sent.index("el kill 1 2 -1")
    assert setup < kill and not any(c.startswith("el msg 3 1 ") for c in sent)  # (the core's remains aren't picked so)


def test_a_team_s_death_variation_is_the_map_s_where_it_redefines_one():
    """A variation name defined by the base game and again by the map's mod (Battlefield of
    Eternity's gates: base Variation2 = TownGateDamagedDeath02, the map's = its Hell gate's): the
    map's, read later, is the one a team's death picks."""
    from heroes_capture.common.actors import Catalog

    base = '''<Catalog><CActorUnit id="Gate" unitName="Gate">
      <DeathCustoms ModelLink="BaseDeath02" Name="Variation2"/></CActorUnit>
      <CModel id="BaseDeath02"/><CModel id="HellDeath0"/></Catalog>'''
    mod = '''<Catalog><CActorUnit id="Gate" unitName="Gate">
      <On Terms="UnitDeathCustomize; PlayerId 12 12; ModelVariation 0" Send="DeathCustomize Variation2"/>
      <DeathCustoms ModelLink="HellDeath0" Name="Variation2"/></CActorUnit></Catalog>'''
    assert Catalog([base, mod]).death_models("Gate", 12) == ["HellDeath0"]


def test_a_tile_from_chaos_s_side_needs_no_shot_over_black():
    """A team-coloured tile from Chaos's side is shot over the light sky only: its colour comes from
    that shot with the see-through share of its pair from Order (matting.matte_with_alpha), the same
    colour the pair of its own (over light and over black) gave, see-through pixels included."""
    import numpy as np

    from heroes_capture.capture.matting import matte, matte_with_alpha

    level = 198.0
    colour = np.array([[[200, 40, 30], [10, 20, 220], [120, 120, 120]]], np.float32)  # red, blue, grey
    alpha = np.array([[1.0, 0.5, 0.25]], np.float32)
    white = (colour * alpha[..., None] + (1 - alpha[..., None]) * level).round().astype(np.uint8)
    black = (colour * alpha[..., None]).round().astype(np.uint8)
    pair = matte(white, black, level, animated=False)
    alone = matte_with_alpha(white, pair[..., 3], level)
    assert np.array_equal(alone[..., 3], pair[..., 3])
    assert np.abs(alone[..., :3].astype(int) - pair[..., :3].astype(int)).max() <= 2

