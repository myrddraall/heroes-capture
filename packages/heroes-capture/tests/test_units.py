"""The pieces with exact rules, without the game."""

import json
import os
import struct
import sys
import types

import numpy as np
import pytest

from heroes_capture import js_json
from heroes_capture.frames import frame_exists
from heroes_capture.capture_script import capture_script, fixed
from heroes_capture.inject import check_definition_order, plan_grid, read_map_info
from heroes_capture.light_data import main_light, parse_lights, parse_terrains, tileset_of
from heroes_capture.sky import clear_dds, parallax_keys, solid_dds


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
# The capture script
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value, text", [(48, "48.0"), (214.00000000000003, "214.0000"), (41.03125, "41.0313"),
                                         (-3.09375, "-3.0938"), (8.019003082878507, "8.0190")])
def test_fixed_rounds_halves_away_from_zero(value, text):
    assert fixed(value) == text


def script(**overrides):
    options = dict(tiles=[{"x": 20, "y": 178}, {"x": 46.5, "y": 166.25}], hide_structures=False, distance=214.0,
                   lens={"fov": 8.019, "farClip": 800}, unbound=False, show_ui=False, map_id=1234,
                   opening_timers=["libX_gv_timer"], hide_doodads=["Storm_Doodad_Heaven_Clouds"], arena=True)
    options.update(overrides)
    return capture_script(**options)


def test_capture_script_is_filled_and_defined_before_use():
    text = script()
    assert "${" not in text
    assert "hrsCap_tileX[1] = 46.5000; hrsCap_tileY[1] = 166.2500;" in text
    assert "const int hrsCap_mapId = 1234;" in text
    assert "hrsCap_CutShort(libX_gv_timer);" in text
    assert "libAREN_gv_aRM_RoundScore" in text
    check_definition_order(text)
    assert "libAREN_gv_aRM_RoundScore" not in script(arena=False)


def test_the_elements_probe_s_commands_are_in_the_script():
    """The "el" command (--probe-elements, ELEMENTS-PLAN.md): its verbs, a tower brought down the
    game's way (the dead-state morph, not a kill), the remains cleared as Blizzard's maps clear a
    town, and the objective spawned only on a map whose spawn function we know (LibMLBD)."""
    text = script()
    assert 'else if ((lv_word == "el")) { lv_t = hrsCap_gt_Element; }' in text
    for verb in ("hideall", "showall", "show", "hide", "kill", "core", "clear", "keep", "camp", "freeze", "boss",
                 "smsg", "umsg", "msg", "terrain", "isolate", "holes", "killall", "env", "killhow", "quiet", "at", "scene", "scopemsg", "pauseable", "behave", "play", "copy", "copykill", "fadeall", "wide", "dmsg", "deadunit"):
        assert f'(lv_verb == "{verb}")' in text
    assert 'AbilityCommand("TowerDeadMorph", 0)' in text and 'UnitBehaviorAdd(lp_unit, "TownCannonTowerInvulnerable"' in text
    # A tower by its morph, not the Tower attribute (town halls have it too, and never fell).
    assert 'if (UnitAbilityExists(lp_unit, "TowerDeadMorph")) {' in text and "c_unitAttributeTower" not in text
    assert 'UnitGroup("TownCannonTowerDead"' in text and '"ScopeContains _DeathModel"' in text and "ActorWorldParticleFXDestroy();" in text
    assert "libGame_gv_teams[lv_team].lv_core = UnitLastCreated();" in text  # the replacement core first
    assert "lv_unit != libGame_gv_teams[libGame_gv_teamOrderIndex_C].lv_core" in text  # killall spares the cores
    # "el at": the lighting refitted (without it structures came out duller), then the capture
    # camera applied (a sky shot leaves the near clip past the ground); none of the rest of the scene.
    at = text[text.index('(lv_verb == "at")'):text.index('(lv_verb == "scene")')]
    assert at.index("hrsCap_NormalCamera(hrsCap_cmdPlayer);") < at.index("hrsCap_ApplyCamera(hrsCap_cmdPlayer);")
    assert "hrsCap_Scene();" not in at
    # Hiding a structure hides every actor of its own (a core's shield crystals), and showing it shows them.
    assert 'ActorScopeSend(ActorScopeFromUnit(lp_unit), "SetVisibility 0");' in text and "hrsCap_ShowStructure(lv_unit, lp_show);" in text
    # A fall sets off nothing for the players: no loot banner in a town hall's rubble, no XP.
    kill = text[text.index("void hrsCap_Kill ("):text.index("void hrsCap_KillShown (")]
    assert "hrsCap_QuietFalls();" in kill and 'UnitBehaviorAdd(lp_unit, "UnitGivesNoXP", lp_unit, 1);' in kill
    assert "libGame_gv_loot_DropBannerInTownHallRubble = false;" in text
    assert "libCore_gv_sYSXPOn = false;" in text
    assert "libMLBD_gf_MMBOESpawnBoss" not in text
    assert "if (!false) {" in text  # no holes: none opened
    full = script(hole_cells=[(46, 97), (47, 97)])
    assert "RegionAddCircle(hrsCap_holes, true, Point(46.5000, 97.5000), 0.45);" in full and "if (!true) {" in full
    assert full.index("hrsCap_HolesInit();") > full.index("void hrsCap_Init ()")
    # Isolating: the terrain, every doodad and the units outside the circle hidden, and the loot
    # banners there (only those: rubble's wide bounds reach past the circle); then all shown again,
    # the holes opened again and the cloud layers hidden again.
    isolate = full[full.index("void hrsCap_Isolate ("):full.index("// \"el <verb> ...\"")]
    assert 'hrsCap_DoodadsMessage("SetVisibility 0");' in isolate and "hrsCap_IsolateUnits(lp_x, lp_y, lp_radius);" in isolate
    assert isolate.index("hrsCap_OpenHoles();") > isolate.index("TerrainShowRegion(RegionRect(-16.0, -16.0, 272.0, 272.0), true);")
    assert isolate.index('"Storm_Doodad_Heaven_Clouds");') > isolate.index('hrsCap_DoodadsMessage("SetVisibility 1");')
    assert '"ScopeContains LootBanner"' in full
    # "el env off": everything hidden, and the terrain, doodads and water switched off.
    assert "hrsCap_Isolate(true, 0.0, 0.0, 0.0);" in full and "EnvironmentShow(c_environmentTerrain, false);" in full
    assert "EnvironmentShow(c_environmentTerrain, true);" in full and "hrsCap_Isolate(false, 0.0, 0.0, 0.0);" in full
    check_definition_order(full)
    boss = script(boss=True)
    assert "libMLBD_gf_MMBOESpawnBoss(StringToInt(StringWord(hrsCap_cmd, 3)), Point(" in boss
    check_definition_order(boss)


def test_definition_order_is_enforced():
    with pytest.raises(SystemExit, match="hrsCap_B is used at line 1 before it is defined"):
        check_definition_order("void hrsCap_A () { hrsCap_B(); }\nvoid hrsCap_B () {}\n")


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
    for variant in ("white", "black", "bare", "whitebare"):
        copy = models[f"Assets\\Skyboxes\\HrsParallaxKeys\\HeavenSkyboxParallax_{variant}.m3"]
        assert len(copy) == len(m3)
    assert b"HrsKeyWhite" in models["Assets\\Skyboxes\\HrsParallaxKeys\\HeavenSkyboxParallax_white.m3"]
    assert b"Base_Diffuse" in models["Assets\\Skyboxes\\HrsParallaxKeys\\HeavenSkyboxParallax_bare.m3"]


# ------------------------------------------------------------------------------------------------
# The sky measurement's consistency rule
# ------------------------------------------------------------------------------------------------


@pytest.fixture
def sky_layers(monkeypatch):
    monkeypatch.setitem(sys.modules, "heroes_capture.game_control", types.SimpleNamespace(settle=None, step=None))
    from heroes_capture import sky_layers

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

    from heroes_capture.sky_stitch import _haze

    h, w = 40, 80
    level = np.zeros((h, w, 3), np.float32)
    level[:, :40] = 230  # the white key behind the left half only
    haze_colour, haze_alpha = np.array([120.0, 130.0, 150.0]), 0.5
    black = np.full((h, w, 3), haze_colour * haze_alpha, np.float32)  # the haze over black, everywhere
    white = black + level * (1 - haze_alpha)  # over the key, where there is one
    out = _haze(white, black, level, 0)
    assert np.allclose(out[:, :40, 3], haze_alpha, atol=0.01) and np.allclose(out[:, :40, :3], haze_colour, atol=1)
    assert (out[:, 40:, 3] == 0).all()


def test_a_layer_cropped_to_its_content_stays_in_place():
    """The cropped haze keeps each pixel where it was behind the map: its canvas origin moves with
    the crop."""
    import numpy as np

    from heroes_capture.sky_stitch import _crop_to_content

    image = np.zeros((10, 20, 4), np.uint8)
    image[2:5, 3:9] = (10, 20, 30, 255)
    image[4, 8] = (1, 2, 3, 200)  # a marked pixel, at canvas (8, 4)
    low = np.array([-100.0, -50.0])
    cropped, moved = _crop_to_content(image, low)
    assert cropped.shape == (3, 6, 4)
    assert tuple(cropped[4 - 2, 8 - 3]) == (1, 2, 3, 200)
    assert tuple(moved + [8 - 3, 4 - 2]) == tuple(low + [8, 4])  # the same place in the centre view
    empty, same = _crop_to_content(np.zeros((4, 4, 4), np.uint8), low)
    assert empty.shape == (4, 4, 4) and tuple(same) == tuple(low)


# ------------------------------------------------------------------------------------------------
# Map elements: two shots' difference
# ------------------------------------------------------------------------------------------------


def test_an_element_s_difference_leaves_out_the_strip_and_small_changes():
    from heroes_capture import elements

    a = np.zeros((40, 60, 3), np.uint8)
    b = a.copy()
    b[10:20, 30:50] = 200  # the element
    b[5, 40] = elements.ELEMENT_THRESHOLD  # not over the threshold
    b[:, :4] = 255  # the status strip's column
    mask = elements.changed(b, a, left=4)
    assert mask.sum() == 200 and mask[10:20, 30:50].all()
    assert elements.describe(mask) == " 8.333% of pixels changed, within x 30..50, y 10..20 (100% of that box)"
    assert elements.describe(np.zeros((4, 4), bool)) == "no pixels changed"


def test_the_holes_structures_stand_on_are_filled_and_no_others():
    """A core stands on a small hole in the terrain (its pedestal covers it); that hole loses its
    hole flag, so the ground is drawn once the core's remains are cleared. A hole with no structure
    on it, or with only a marker in it, stays."""
    from heroes_capture import elements

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
    from heroes_capture import elements

    white = np.full((60, 80, 3), 230, np.uint8)
    black = np.zeros((60, 80, 3), np.uint8)
    white[:, 70:] = 228  # the white sky a shade uneven: the veil
    black[20:30, 20:30] = 100  # a solid part ...
    white[20:30, 20:30] = 110  # ... the white sky brightening it by 10 levels
    black[20:30, 30:32] = 50  # a soft edge, half see-through
    white[20:30, 30:32] = 50 + 115
    black[5, 75] = white[5, 75] = 90  # outside the circle (a cliff doodad)
    rgba = elements.cut_out(white, black, centre=(30, 25), radius=20)
    assert (rgba[20:30, 20:30, 3] == 255).all() and (rgba[20:30, 20:30, :3] == 100).all()
    assert (abs(rgba[20:30, 30:32, 3].astype(int) - 128) <= 2).all()
    assert rgba[5, 75, 3] == 0 and (rgba[:, 70:, 3] == 0).all() and rgba[50, 10, 3] == 0
    assert elements.screen_point({"x": 108, "y": 60}, {"x": 98, "y": 62.6154}, {"w": 3440, "h": 1440}, 48) == pytest.approx((2200, 845.5), abs=0.1)


def test_an_element_s_cut_out_leaves_out_what_can_t_be_hidden_and_its_neighbours_rubble():
    """What is the same as in its tile's shot with everything hidden (a cliff doodad) is left out,
    but not the element drawn over it; rubble keeps only what is nearer its own structure."""
    from heroes_capture import elements

    white = np.full((60, 80, 3), 230, np.uint8)
    black = np.zeros((60, 80, 3), np.uint8)
    white[10:50, 10:20] = black[10:50, 10:20] = 70  # a cliff doodad: can't be hidden
    empty = white.copy()
    white[20:30, 12:18] = black[20:30, 12:18] = 150  # the element, over the doodad
    white[20:30, 40:46] = black[20:30, 40:46] = 150  # a neighbour's rubble, nearer it than us
    rgba = elements.cut_out(white, black, centre=(15, 25), radius=40, empty=empty, others=[(43, 25)])
    assert (rgba[20:30, 12:18, 3] == 255).all()  # the element, kept over the doodad
    assert (rgba[10:20, 10:20, 3] == 0).all() and (rgba[30:50, 10:20, 3] == 0).all()  # the doodad, left out
    assert (rgba[20:30, 40:46, 3] == 0).all()  # the neighbour's rubble, left out


def test_a_map_s_element_list_from_its_script_objects_and_regions():
    """Towns as the script hooks them up (the lane set before a town counts for it), each structure
    in the town whose region holds it (a shape marked negative cuts out of it), camps numbered in
    the script's order with their middle and spread from their spawn points."""
    from heroes_capture import elements

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
    assert [(u["id"], u["owner"], u["town"], u["core"]) for u in found["structures"]] == [
        (20, "order", 1, False), (21, "order", None, False), (22, "chaos", 2, False), (23, "chaos", None, True)]
    (camp,) = found["camps"]
    assert (camp["camp"], camp["type"], camp["x"], camp["y"]) == (1, "SiegeCamp1", 31.0, 31.0)
    assert camp["spread"] == 2.0 and camp["radius"] == 2.0 + elements.CAMP_MARGIN
    tiles = [{"index": 0, "x": 0, "y": 0}, {"index": 1, "x": 30, "y": 30}]
    assert elements.source_tile(camp, tiles)["index"] == 1


class _Recoverable(Exception):
    def __init__(self, why, resume_at=None):
        super().__init__(why)
        self.why, self.resume_at = why, resume_at


@pytest.fixture
def element_capture(monkeypatch):
    """The elements capture with the game's controls stood in for (they need Windows)."""
    monkeypatch.setitem(sys.modules, "heroes_capture.game_control",
                        types.SimpleNamespace(settle=lambda seconds: None, step=lambda action, what: action(), Recoverable=_Recoverable))
    monkeypatch.delitem(sys.modules, "heroes_capture.element_capture", raising=False)
    from heroes_capture import element_capture

    monkeypatch.setattr(element_capture, "log", lambda message: None)
    monkeypatch.setattr(element_capture, "warn", lambda message: None)
    return element_capture


class _FakeSession:
    """Answers every command, and shows a frame; records what was typed."""

    def __init__(self, unanswered=()):
        self.sent, self.unanswered = [], set(unanswered)

    def send(self, command, timeout=2.0):
        self.sent.append(command)
        return None if command in self.unanswered else (types.SimpleNamespace(camera_x=1.0, camera_y=2.0), self.grab())

    def blank(self, frame):
        return frame

    def grab(self, dark_ok=False):
        return np.zeros((4, 6, 3), np.uint8)

    def status(self):
        return types.SimpleNamespace(camera_x=10.0, camera_y=20.0)


def _tile_command(tile):
    return f"tile {tile['index']} {tile['x']:.2f} {tile['y']:.2f}"


def _black_settled(session, first, white):
    return first


ELEMENTS_MANIFEST = {
    "tiles": [{"index": 0, "x": 0, "y": 0}, {"index": 1, "x": 50, "y": 50}],
    "screen": {"w": 3440, "h": 1440}, "pxPerCell": 48, "distance": 85.0,
    "elements": {
        "structures": [{"id": 5, "type": "TownCannonTowerL2", "x": 1, "y": 2, "owner": "order", "town": 1, "core": False, "radius": 6.0},
                       {"id": 6, "type": "KingsCore", "x": 3, "y": 1, "owner": "chaos", "town": None, "core": True, "radius": 6.0}],
        "towns": [],
        "camps": [{"camp": 1, "type": "SiegeCamp1", "x": 49, "y": 51, "spread": 2.0, "radius": 5.0}],
    },
}


def test_the_elements_capture_shoots_each_element_then_brings_the_structures_down(element_capture, tmp_path):
    session = _FakeSession()
    element_capture.capture_elements(session, ELEMENTS_MANIFEST, tmp_path, _tile_command, _black_settled)
    sent = session.sent
    # Every structure faded out (not hidden: it keeps its look and effects as the map paused it),
    # the rest of the scene hidden, the camp (clear of every structure's circle) spawned straight
    # away, born, and frozen; then each structure faded in, the camera straight above it (the move
    # sets the white sky), shot over white and black, faded out; no sky put back after (the next
    # move does it). (No camera bounds in this manifest: no room for copies, so every structure
    # falls in waves.)
    # The two are neighbours (within NEIGHBOUR_REACH): after each one's pair of shots, the other is
    # faded in beside it for a shot over black (which of them is in front where they overlap).
    assert sent[:19] == ["tile 0 0.00 0.00", "el fadeall 0", "el env off", "el keep 1", "el camp 1", "el freeze",
                         "el scopemsg 1 2 SetOpacity 1 0", "el at 1.00 2.00", "black",
                         "el scopemsg 3 1 SetOpacity 1 0", "el scopemsg 3 1 SetOpacity 0 0", "el scopemsg 1 2 SetOpacity 0 0",
                         "el scopemsg 3 1 SetOpacity 1 0", "el at 3.00 1.00", "black",
                         "el scopemsg 1 2 SetOpacity 1 0", "el scopemsg 1 2 SetOpacity 0 0", "el scopemsg 3 1 SetOpacity 0 0",
                         "el at 49.00 51.00"]
    # The camp shot after the structures, its defenders removed; no isolating anywhere.
    assert sent[18:22] == ["el at 49.00 51.00", "black", "el keep 0", "clean"] and sent.count("el camp 1") == 1
    assert not any(c.startswith("el isolate") or c.startswith("sky ") for c in sent)
    # Rubble in waves: the tower, then the core in a wave of its own, last (after its hidden
    # replacement takes its place); each brought down, left for its time, shot centred, cleared
    # away.
    core, kill = sent.index("el core 2 -1"), sent.index("el kill 1 2 -1")
    # Each brought down with the camera far back over it (a fall gives off its smoke and dust only
    # near the camera).
    assert kill < core and sent[core - 2:core] == ["el wide 3.00 1.00 212.5", "el scopemsg 3 1 SetOpacity 1 0"]
    assert sent[kill - 2:kill] == ["el wide 1.00 2.00 212.5", "el scopemsg 1 2 SetOpacity 1 0"]
    assert sent[core + 1:core + 4] == ["el freeze", "el at 3.00 1.00", "black"]
    assert sent[kill + 1:kill + 5] == ["el freeze", "el at 1.00 2.00", "black", "el clear"]
    # Clearing: Order's core (not yet replaced) and anything left brought down, the scene shown
    # again, the remains and the fallen structures' own actors cleared, the holes shown, everything
    # let play a moment and paused again (the doodads shown again give off their particles).
    assert sent[-9:] == ["el core 1 0", "el killall 5", "el env on", "el clear all", "el holes show", "el hideall", "el play",
                         "el freeze", "clean"]
    records = json.loads((tmp_path / "elements" / "elements.json").read_text())
    assert sorted(records) == ["camp-1-spawned", "structure-5-rubble", "structure-5-standing", "structure-6-rubble", "structure-6-standing"]
    assert records["structure-5-rubble"]["shot"] == "structure-5-rubble" and records["camp-1-spawned"]["tile"] == 1
    assert records["structure-6-standing"]["camera"] == {"x": 10.0, "y": 20.0} and records["structure-6-standing"]["neighbours"] == [5]
    assert frame_exists(tmp_path / "elements" / "structure-6-standing-with-5")
    assert frame_exists(tmp_path / "elements" / "structure-5-rubble") and frame_exists(tmp_path / "elements" / "structure-5-rubble-black")


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
    element_capture.capture_elements(session, _copies_manifest(), tmp_path, _tile_command, _black_settled)
    sent = session.sent
    # Made first; their spots too far apart for one camera, each brought down in a group of its own
    # with the camera far back over it (numbered by group: the tower's spot leftmost), paused
    # before the next.
    assert [c for c in sent if c.startswith("el copy ")] == ["el copy 0 make 1 2 100 100", "el copy 1 make 30.5 30.5 140.5 60.5"]
    first = sent.index("el copy 1 make 30.5 30.5 140.5 60.5") + 1
    assert sent[first:first + 6] == ["el wide 100.00 100.00 212.5", "el copykill 0 0", "el freeze",
                                     "el wide 140.50 60.50 212.5", "el copykill 1 1", "el freeze"]
    assert sent.index("el fadeall 0") < first < sent.index("el at 1.00 2.00")
    tower = sent.index("el at 100.00 100.00")
    assert sent[tower + 1] == "black" and sent[sent.index("el at 140.50 60.50") + 1] == "black"
    assert not any(c.startswith("el kill ") for c in sent) and "el core 2 -1" in sent
    records = json.loads((tmp_path / "elements" / "elements.json").read_text())
    # The fake camera is at (10, 20) for every shot: as if from the tower's cell, (10 + 1 - 100, 20 + 2 - 100).
    assert records["structure-5-rubble"]["camera"] == {"x": -89.0, "y": -78.0}
    assert records["structure-5-rubble"]["x"] == 1 and records["structure-7-rubble"]["tile"] == 1  # its structure's tile
    assert "structure-6-rubble" in records


def test_a_type_whose_copy_crashed_the_game_falls_in_waves_after_the_relaunch(element_capture, tmp_path, monkeypatch):
    """The type being copied when the game went (left in the environment the recovery's process
    inherits) isn't copied again: it falls in waves."""
    monkeypatch.setenv(element_capture.COPY_TRYING, "TownWallRadial2L3")
    monkeypatch.delenv(element_capture.COPY_CRASHED, raising=False)
    session = _FakeSession()
    element_capture.capture_elements(session, _copies_manifest(), tmp_path, _tile_command, _black_settled)
    assert [c for c in session.sent if c.startswith("el copy ")] == ["el copy 0 make 1 2 100 100"]
    assert "el kill 30.5 30.5 -1" in session.sent
    assert element_capture.COPY_TRYING not in os.environ and os.environ[element_capture.COPY_CRASHED] == "TownWallRadial2L3"


def test_copy_spots_keep_clear_on_the_structure_s_lighting():
    """Each copy's spot: its circle clear of every structure's, camp's and other spot's, under the
    lighting its structure has (the lighting map: a left half red, a right half green), on the
    same part of a cell; none for cores and keeps."""
    import math

    import numpy as np

    from heroes_capture import elements

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
    element_capture.capture_elements(session, manifest, tmp_path, _tile_command, _black_settled)
    sent = session.sent
    assert sent.index("el camp 1") > sent.index("el scopemsg 3 1 SetOpacity 0 0") and sent[1:4] == ["el fadeall 0", "el env off", "el scopemsg 1 2 SetOpacity 1 0"]


def test_a_resumed_elements_capture_only_brings_the_structures_down(element_capture, tmp_path):
    element_capture.capture_elements(_FakeSession(), ELEMENTS_MANIFEST, tmp_path, _tile_command, _black_settled)
    again = _FakeSession()
    element_capture.capture_elements(again, ELEMENTS_MANIFEST, tmp_path, _tile_command, _black_settled)
    assert again.sent == ["tile 0 0.00 0.00", "el fadeall 0", "el env off", "el core 1 0", "el core 2 0", "el killall 5",
                          "el env on", "el clear all", "el holes show", "el hideall", "el play", "el freeze", "clean"]


def test_rubble_waves_keep_each_one_s_rubble_out_of_the_others_shots():
    """No two structures in a wave nearer each other than their circles reach together; each core
    in a wave of its own, last."""
    from heroes_capture import elements

    def u(i, x, y, core=False):
        return {"id": i, "x": x, "y": y, "radius": 8.0, "core": core}

    town = [u(1, 0, 0), u(2, 5, 0), u(3, 10, 0), u(4, 40, 0), u(5, 45, 0)]
    waves = elements.rubble_waves(town + [u(9, 100, 100, core=True), u(10, 200, 100, core=True)])
    assert [[s["id"] for s in w] for w in waves] == [[1, 4], [2, 5], [3], [9], [10]]
    for w in waves[:-2]:
        assert all(abs(a["x"] - b["x"]) >= 16 for a in w for b in w if a is not b)


def test_the_elements_capture_gives_up_on_a_map_that_stops_answering(element_capture, tmp_path):
    session = _FakeSession(unanswered={"tile 0 0.00 0.00", "el fadeall 0", "el env off"})
    with pytest.raises(_Recoverable) as lost:
        element_capture.capture_elements(session, ELEMENTS_MANIFEST, tmp_path, _tile_command, _black_settled)
    assert lost.value.resume_at == 0


def test_the_elements_render_has_its_own_files_and_pack_folder(tmp_path):
    """--structures elements is a render of its own (ELEMENTS-PLAN.md): its preparation's files and
    its pack (maps/<map>/elements/) never overwrite the render with the structures kept."""
    from heroes_capture import inject, pack

    assert inject.render_id("Battlefield of Eternity", "elements") == "battlefield-of-eternity-elements"
    assert inject.render_id("Battlefield of Eternity", "keep") == "battlefield-of-eternity-structures"
    assert pack.variant_folder(tmp_path, "elements") == tmp_path / "elements" and pack.variant_folder(tmp_path, "keep") == tmp_path
    assert inject.parse_args(["Dragon Shire", "--structures", "elements"])["structures"] == "elements"
    with pytest.raises(SystemExit, match="keep, hide or elements"):
        inject.parse_args(["Dragon Shire", "--structures", "some"])


def test_the_script_reads_the_number_of_its_longest_commands():
    """A command's sequence number is its last word: the script must reach it in the longest command
    sent ("el copy <n> make <x> <y> <to x> <to y> <number>": 9 words), or it answers with another
    word and the capture, hearing no answer, sends it again (four copies made)."""
    import re

    from heroes_capture import capture_script as cs

    template = cs.TEMPLATE.read_text(encoding="utf-8")
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

    from heroes_capture import elements, stitch

    white = np.full((40, 40, 3), 230, np.uint8)
    black = np.zeros((40, 40, 3), np.uint8)
    white[20, 20], black[20, 20] = (223, 242, 243), (13, 35, 58)
    assert stitch.matte(white, black, 230.0)[20, 20, 3] == 255
    cut = elements.cut_out(white, black, (20, 20), 10)
    assert 0 < cut[20, 20, 3] < 60 and cut[20, 20, :3].max() > 200


def test_a_seam_takes_a_floating_piece_from_one_screenshot_not_a_ghost_of_both():
    """At a seam's feather: where the two screenshots agree, blended by opacity (a see-through
    pixel's black doesn't darken the other); where one has a piece above the ground and the other
    the sky there (the piece seen shifted), the one weighing more taken whole, not a dark
    see-through copy (the "shadows" at the map's edges)."""
    import numpy as np

    from heroes_capture import stitch

    under = np.array([[[200, 160, 40, 255], [200, 160, 40, 255], [100, 100, 100, 128]]], np.uint8)
    over = np.array([[[0, 0, 0, 0], [0, 0, 0, 0], [100, 100, 100, 160]]], np.uint8)
    weight = np.array([[0.3, 0.7, 0.5]], np.float32)
    out = stitch.seam_blend(under, over, weight)
    assert tuple(out[0, 0]) == (200, 160, 40, 255)  # the piece, whole (it weighs more)
    assert out[0, 1, 3] == 0  # the sky, whole
    assert tuple(out[0, 2, :3]) == (100, 100, 100) and out[0, 2, 3] == 144  # agreeing: blended, no darkening

def test_view_groups_fit_one_camera_each():
    """Items in groups that each fit the box round its middle, every item in one group."""
    from heroes_capture import elements

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
    assert w({"type": "KingsCore", "owner": "chaos", "core": True}) == element_capture.CORE_WAIT
