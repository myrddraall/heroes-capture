"""The pieces with exact rules, without the game."""

import struct
import sys
import types

import pytest

from heroes_capture import js_json
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
