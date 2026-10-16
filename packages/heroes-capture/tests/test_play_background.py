"""The Play screen's background export (ui/play_background.py): the cutscene read for the Play
screen's hold, the camera from its node, the light's HDR, animation tracks; and the export from the
game's own data (marker game_data)."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from heroes_capture.common.lighting import light_hdr, parse_lights
from heroes_capture.common.m3 import Track, compose
from heroes_capture.ui import play_background
from test_ui import Storage

CUTSCENE = """<?xml version="1.0" encoding="utf-8"?>
<CutsceneState>
  <CCutsceneNodeDirector name="Director">
    <CCutsceneNodeBookmark name="Bookmarks">
      <CCutsceneElementBookmark start="9000" bookmarkName="Stand Loop"/>
      <CCutsceneElementBookmark start="25899" bookmarkName="ScreenPlay.HeroSelect Start"/>
    </CCutsceneNodeBookmark>
    <CCutsceneNodeCamera name="Background" position="0.000000,49.472809,-2.002756" fov="43.000000" nearClip="0.376200" farClip="185.000000" distance="90.699997" pitch="-1.432791" yaw="-179.998886" heightOffset="5.340000"/>
    <CCutsceneNodeActor name="Floor" modelLink="Storm_UI_HeroSelection_Floor">
      <CCutsceneElementAnim start="17144" anim="Birth"/>
      <CCutsceneElementAnim start="25340" anim="Stand"/>
      <CCutsceneElementAnim start="28899" anim="Death"/>
    </CCutsceneNodeActor>
    <CCutsceneNodeActiveLight filter="SpotlightOn" name="SpotlightOn">
      <CCutsceneElementActiveLight lightID="Spot"/>
    </CCutsceneNodeActiveLight>
    <CCutsceneNodeActiveLight name="Active Light">
      <CCutsceneElementActiveLight lightID="Base"/>
      <CCutsceneElementActiveLight start="21402" lightID="Spotlight"/>
      <CCutsceneElementActiveLight start="25340" lightID="Base"/>
    </CCutsceneNodeActiveLight>
  </CCutsceneNodeDirector>
</CutsceneState>"""


def test_the_cutscene_as_it_holds_the_play_screen():
    storage = Storage({f"mods\\heroesdata.stormmod\\base.stormdata\\cutscenes\\{play_background.CUTSCENE}": CUTSCENE})
    found = play_background.cutscene(storage)
    assert (found["model"], found["animation"], found["light"]) == ("Storm_UI_HeroSelection_Floor", "Stand", "Base")
    assert found["camera"]["fov"] == "43.000000"


def test_the_camera_looks_along_the_model_s_front_from_behind_its_floor():
    camera = play_background.camera(play_background.cutscene(Storage(
        {f"mods\\heroesdata.stormmod\\base.stormdata\\cutscenes\\{play_background.CUTSCENE}": CUTSCENE}))["camera"])
    assert camera["eye"] == pytest.approx([0, -41.2, 5.605], abs=0.01)
    assert camera["forward"] == pytest.approx([0, math.cos(math.radians(1.432791)), -math.sin(math.radians(1.432791))], abs=1e-4)
    assert (camera["fovY"], camera["near"], camera["far"]) == (43, 0.3762, 185)


def test_a_light_s_hdr_from_it_or_its_parents():
    lights = parse_lights("""
      <CLight id="default"><ToDInfoArray index="0"><Param index="HDRExposure" value="1.5"/></ToDInfoArray></CLight>
      <CLight id="Base" parent="default"><ToDInfoArray index="0"><Param index="HDRWhitePoint" value="1.85"/></ToDInfoArray></CLight>
      <CLight id="Plain"/>""")
    assert light_hdr(lights, "Base") == {"exposure": 1.5, "whitePoint": 1.85}
    assert light_hdr(lights, "Plain") is None


def test_tracks_between_their_keys_and_bone_matrices():
    track = Track((0, 200000), ((0.0, 0.0), (0.3333, 4.0)))
    assert track.at(100000) == pytest.approx((0.16665, 2.0))
    assert track.at(-5) == (0.0, 0.0) and track.at(300000) == (0.3333, 4.0)
    turned = compose((1, 2, 3), (0, 0, 1, 0), (2, 2, 2))  # half a turn about z, doubled, moved
    assert turned @ np.array([1, 0, 0, 1]) == pytest.approx([-1, 2, 3, 1])


@pytest.mark.game_data
@pytest.mark.xdist_group("cdn")
def test_the_play_screen_s_sky_from_the_game_s_data(storage, tmp_path: Path):
    scene = play_background.export(storage, tmp_path)
    assert scene["source"]["animation"] == "Stand"
    assert scene["camera"]["eye"] == pytest.approx([0, -41.2, 5.6], abs=0.05)
    assert scene["tone"] == {"exposure": 1.0, "whitePoint": 1.85}
    assert set(scene["meshes"]) == {
        "Sky_Cylinder_Stars", "Cylinder001", "TwinkleStarsEmitter",
        # the planets, each with its atmosphere (a billboard of the bone's own)
        "OrangePlanet", "Dummy001", "Dummy002", "Dummy002-17", "Dummy003", "Dummy003-19",
    }
    assert scene["meshes"]["Sky_Cylinder_Stars"]["materials"] == ["staticStars", "animatedStars", "peterPainting"]
    # The planets' hex grid: a colour layer times the grid, fading in towards the rim the fresnel turns to.
    hex_grid = scene["materials"][scene["meshes"]["Dummy002"]["materials"][1]]
    assert hex_grid["emissiveBlend"] == "mod" and hex_grid["diffuse"]["color"] == [64, 13, 255, 255]
    assert hex_grid["emissive"]["fresnel"] == {"type": 1, "exponent": 3.0, "min": 0.2, "max": 1.0,
                                               "yaw": pytest.approx(0.5236), "pitch": pytest.approx(-0.43633)}
    assert len(scene["meshes"]["Dummy002"]["normals"]) == len(scene["meshes"]["Dummy002"]["positions"])
    # An atmosphere's gradient repeats across u only, its mask in neither: no edge sampling the far end.
    atmosphere = scene["materials"]["atmospherePlanetMedium"]
    assert atmosphere["diffuse"]["wrap"] == [True, False] and atmosphere["alphaMasks"][0]["wrap"] == [False, False]
    # Each planet's meshes, named for its size as its atmosphere's material is.
    planets = {key: mesh.get("planet") for key, mesh in scene["meshes"].items()}
    assert planets == {"Sky_Cylinder_Stars": None, "Cylinder001": None, "TwinkleStarsEmitter": None,
                       "OrangePlanet": "large", "Dummy001": "large", "Dummy002": "medium",
                       "Dummy002-17": "medium", "Dummy003": "small", "Dummy003-19": "small"}
    energy = scene["materials"]["horizonEnergy"]
    assert energy["diffuse"]["scroll"] == {"seconds": 200, "by": pytest.approx([0.33333, 4.0])}
    assert scene["particles"][0]["rate"] == 120 and scene["particles"][0]["vertexAlpha"]
    for texture in scene["textures"].values():
        assert (tmp_path / texture["file"]).stat().st_size > 0
    assert json.loads((tmp_path / "scene.json").read_text()) == json.loads(json.dumps(scene))
