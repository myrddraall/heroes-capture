"""The planets export (ui/planets.py): the frames a planet is put in, and the export from the
game's own data (marker game_data)."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from heroes_capture.ui import planets


def test_a_frame_faces_the_eye_along_minus_y_with_up_as_near_z_as_it_can():
    view = np.array([1.0, 0.0, 0.0])  # the eye looks along +x at the planet
    turn = planets.facing(view, np.array([0.0, 0.0, 1.0]))
    assert turn @ view == pytest.approx([0, 1, 0])  # the view is +y: the eye is along -y
    assert turn @ np.array([0.0, 0.0, 1.0]) == pytest.approx([0, 0, 1])
    assert np.linalg.det(turn) == pytest.approx(1)  # a turn, not a mirror
    # Up not square to the view: its part across the view is what stays up.
    turn = planets.facing(view, np.array([1.0, 0.0, 1.0]))
    assert turn @ np.array([0.0, 0.0, 1.0]) == pytest.approx([0, 0, 1])


def test_a_frame_takes_the_world_into_a_bone_s_own_space_in_its_units():
    world = np.eye(4)
    world[:3, :3] *= 2.0  # a bone scaled by 2, at (10, 0, 0)
    world[:3, 3] = [10, 0, 0]
    into = planets.frame_of(world, np.eye(3), 5.0)
    assert into @ np.array([10, 0, 0, 1]) == pytest.approx([0, 0, 0, 1])
    assert into @ np.array([15, 0, 0, 1]) == pytest.approx([1, 0, 0, 1])  # 5 units from the bone: 1


def test_the_light_on_at_a_time_of_the_cutscene():
    schedule = [(0, "First"), (7974, "Second"), (14461, "Third")]
    assert planets.light_at(schedule, 0) == "First"
    assert planets.light_at(schedule, 8000) == "Second"
    assert planets.light_at(schedule, 99999) == "Third"


def test_a_light_as_the_app_lights_with_it_in_a_planet_s_frame():
    table = {"default": {"hdrExposure": 1.5},
             "Flyby": {"parent": "default", "ambient": [0.1, 0.2, 0.3],
                       "directional": [{"index": "Key", "color": [1, 0.5, 0.25], "multiplier": 2.0, "direction": [1, 0, 0]},
                                       {"index": "Fill", "color": [1, 1, 1], "multiplier": 1.0, "direction": [0, 0, 0]}]}}
    turn = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], dtype=float)  # x becomes -y
    light = planets.light_in(table, "Flyby", turn)
    assert light["exposure"] == 1.5 and light["ambient"] == [0.1, 0.2, 0.3]
    assert light["directional"] == [{"direction": [0, -1, 0], "color": [2, 1, 0.5]}]  # one without a direction is no light


@pytest.mark.game_data
def test_the_planets_from_the_game_s_data(storage, tmp_path: Path):
    index = planets.export(storage, tmp_path)
    ids = {p["id"]: p["kind"] for p in index["planets"]}
    assert {k for k, v in ids.items() if v == "play"} == {"play-large", "play-medium", "play-small"}
    assert {k for k, v in ids.items() if v == "world"} == {"azeroth", "korhal", "sanctuary"}
    assert {k for k, v in ids.items() if v == "painted"} == {f"planet-{n}" for n in range(1, 10)}
    # The Play screen's own set, where the game puts it (its camera at y -41 looking along +y).
    play = {p["id"]: p for p in index["play"]}
    assert play["play-large"]["position"] == pytest.approx([-48.2, 64.6, 25.4], abs=0.1)
    assert play["play-large"]["radius"] == pytest.approx(27.3, abs=0.1)
    assert play["play-small"]["radius"] < play["play-medium"]["radius"] < play["play-large"]["radius"]

    large = json.loads((tmp_path / "planets" / "play-large.json").read_text())
    body = np.array(large["meshes"]["body"]["positions"]).reshape(-1, 3)
    assert np.linalg.norm(body, axis=1).max() == pytest.approx(1, abs=1e-3)  # the body's radius is the unit
    assert body[:, 1].max() <= 1e-3  # a hemisphere towards -y, the eye's side
    assert "normals" in large["meshes"]["body"] and large.get("light") is None

    korhal = json.loads((tmp_path / "planets" / "korhal.json").read_text())
    assert {"body", "atmosphere"} <= set(korhal["meshes"])
    assert all(mesh["positions"].__len__() // 3 <= planets.MAX_MESH_VERTICES for mesh in korhal["meshes"].values())
    assert korhal["meshes"]["body"]["materials"][-1] == large["meshes"]["body"]["materials"][1]  # the hex grid
    assert len(korhal["light"]["directional"]) == 3 and korhal["light"]["exposure"] == 1.5
    for light in korhal["light"]["directional"]:
        assert math.hypot(*light["direction"]) == pytest.approx(1, abs=1e-3)

    painted = json.loads((tmp_path / "planets" / "planet-1.json").read_text())
    assert painted["template"] == "play-large" and "meshes" not in painted
    assert painted["body"][1] == large["meshes"]["body"]["materials"][1]
    assert painted["materials"][painted["body"][0]]["unshaded"] is False  # lit, as the flyby lights it
    for file in (large, korhal, painted):
        for texture in file["textures"].values():
            assert (tmp_path / texture["file"]).stat().st_size > 0
