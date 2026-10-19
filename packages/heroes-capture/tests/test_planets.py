"""The planets export (ui/planets.py): the frames a planet is put in, and the export from the
game's own data (marker game_data)."""

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

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


def test_a_light_as_the_app_lights_with_it_about_the_camera():
    table = {"default": {"hdrExposure": 1.5, "hdrBloomThreshold": 0.6},
             "Flyby": {"parent": "default", "ambient": [0.1, 0.2, 0.3],
                       "directional": [{"index": "Key", "color": [1, 0.5, 0.25], "multiplier": 2.0, "direction": [1, 0, 0]},
                                       {"index": "Back", "color": [1, 1, 1], "multiplier": 1.0, "direction": [0, 1, -1]},
                                       {"index": "Fill", "color": [1, 1, 1], "multiplier": 1.0, "direction": [0, 0, 0]}]}}
    light = planets.light_in(table, "Flyby", np.eye(3))
    assert light["exposure"] == 1.5 and light["ambient"] == [0.1, 0.2, 0.3] and light["bloomThreshold"] == 0.6
    assert light["directional"] == [{"direction": [1, 0, 0], "color": [2, 1, 0.5]},
                                    {"direction": [0, 1, -1], "color": [1, 1, 1]}]  # one without a direction is no light
    # Seen by a camera looking along -x (up +z), a light travelling +x in the world travels
    # towards the camera: -y in the planet's frame, which faces it.
    turn = planets.facing(np.array([-1.0, 0, 0]), np.array([0, 0, 1.0]))
    assert planets.light_in(table, "Flyby", turn)["directional"][0]["direction"] == pytest.approx([0, -1, 0])


def test_a_sphere_s_centre_from_its_points_a_hemisphere_s_too():
    points = _sphere_points(2.0) + [1.0, 0.0, 0.0]
    assert planets.sphere_centre(points) == pytest.approx([1, 0, 0], abs=1e-6)
    half = points[points[:, 1] < 0.0]  # the hemisphere towards -y: its mean is well inside it
    assert planets.sphere_centre(half) == pytest.approx([1, 0, 0], abs=1e-6)


def test_a_mesh_s_shape_a_sphere_seen_from_either_side_or_a_ring():
    t = np.linspace(0, 2 * math.pi, 50, endpoint=False)
    ring = np.c_[np.cos(t) * 0.9, np.full(50, -0.6), np.sin(t) * 0.9]
    ring = np.r_[ring, ring * [1.1, 1.0, 1.1]]
    shape = planets.shape_of(ring, np.tile([0.0, -1.0, 0.0], (100, 1)))
    assert shape == {"kind": "ring", "inner": 0.9, "outer": 0.99, "depth": -0.6}
    sphere = _sphere_points(1.2)
    assert planets.shape_of(sphere, sphere / 1.2) == {"kind": "sphere", "radius": 1.2, "side": "near"}
    assert planets.shape_of(sphere, -sphere / 1.2)["side"] == "far"  # normals inward: inside out


def _sphere_points(radius: float, pole=(0.0, 0.0, 1.0), meridian=(1.0, 0.0, 0.0)) -> np.ndarray:
    """A uv sphere's vertices: rings about the pole, as the game's meshes are built."""
    polar, azimuth = np.meshgrid(np.linspace(0.05, 0.95, 19) * math.pi, np.linspace(0, 2 * math.pi, 36, endpoint=False))
    polar, azimuth = polar.ravel(), azimuth.ravel()
    pole, meridian = np.array(pole), np.array(meridian)
    side = np.cross(pole, meridian)
    return radius * (np.outer(np.sin(polar) * np.cos(azimuth), meridian) + np.outer(np.sin(polar) * np.sin(azimuth), side) + np.outer(np.cos(polar), pole))


def test_a_uv_sphere_s_mapping_is_fitted_back_from_its_coordinates():
    # A sphere whose pole leans to -x: u round it from +y, v down from the pole.
    pole = np.array([-0.5, 0.0, math.sqrt(0.75)])
    meridian = np.array([0.0, 1.0, 0.0])
    points = _sphere_points(1.0, pole, meridian)
    polar = np.arccos(points @ pole) / math.pi
    azimuth = np.arctan2(points @ np.cross(pole, meridian), points @ meridian) / (2 * math.pi)
    uvs = np.c_[(0.3 - azimuth) % 1.0, 0.1 + 0.8 * polar]
    mapping = planets.mapping_of({"kind": "sphere"}, points, uvs, "a test sphere")
    assert mapping["kind"] == "sphere"
    assert mapping["pole"] == pytest.approx(pole, abs=0.01)
    assert mapping["flip"] is True and mapping["u0"] == pytest.approx(0.3, abs=0.01)
    # v by the angle from the pole: 0.1 + 0.8 of it, as far as the rows go (they stop short of the poles).
    assert len(mapping["v"]) == planets.V_SAMPLES
    assert mapping["v"][16] == pytest.approx(0.1 + 0.8 * 16 / 31, abs=0.01)
    assert mapping["v"][0] == pytest.approx(0.14, abs=0.01) and mapping["v"][-1] == pytest.approx(0.86, abs=0.01)
    # Its meridian is its own choice: u comes out the same through it.
    m = np.array(mapping["meridian"])
    az = np.arctan2(points @ np.cross(np.array(mapping["pole"]), m), points @ m) / (2 * math.pi)
    u = (mapping["u0"] - az) % 1.0
    assert np.abs(((u - uvs[:, 0] + 0.5) % 1.0) - 0.5).max() < 0.02


def test_a_linear_mapping_is_fitted_where_it_fits_better():
    points = _sphere_points(1.16)
    uvs = np.c_[0.5 + 0.38 * points[:, 0] - 0.18 * points[:, 1], 0.5 - 0.41 * points[:, 2]]
    mapping = planets.mapping_of({"kind": "sphere"}, points, uvs, "a test shell")
    assert mapping["kind"] == "linear"
    assert mapping["u"] == pytest.approx([0.38, -0.18, 0, 0.5], abs=1e-3)
    assert mapping["v"] == pytest.approx([0, 0, -0.41, 0.5], abs=1e-3)


def test_a_ring_s_mapping_u_greatest_at_one_azimuth():
    t = np.linspace(-math.pi, math.pi, 72, endpoint=False)
    points = np.c_[np.cos(t), np.full(72, -0.6), np.sin(t)]
    top = 1.0  # u greatest where the azimuth is 1 radian
    uvs = np.c_[0.5 * (1 - np.abs(((t - top + math.pi) % (2 * math.pi)) - math.pi) / math.pi), np.zeros(72)]
    mapping = planets.mapping_of({"kind": "ring"}, points, uvs, "a test ring")
    assert mapping == {"kind": "ring", "from": pytest.approx(top, abs=0.05), "uMax": pytest.approx(0.5, abs=0.01)}


def test_a_region_of_several_spheres_is_several_parts_about_their_own_centres():
    # Two spheres joined by nothing: a planet and a moon half its size, off to its right.
    a = _sphere_points(1.0)
    b = _sphere_points(0.5) + [2.0, 0.0, 0.5]
    chain = lambda start, n: [i for k in range(n - 2) for i in (start + k, start + k + 1, start + k + 2)]
    pieces = planets.components(len(a) + len(b), chain(0, len(a)) + chain(len(a), len(b)))
    # (every run of three indices is a triangle: here each sphere's vertices chained in turn)
    assert [len(p) for p in pieces] == [len(a), len(b)]


def test_a_layer_on_the_second_coordinates_is_baked_over_the_first():
    # A square: its first coordinates the unit square, its second the same turned a quarter.
    uvs = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
    uvs2 = np.array([[0, 1], [0, 0], [1, 0], [1, 1]], dtype=float)
    triangles = np.array([[0, 1, 2], [0, 2, 3]])
    source = np.zeros((16, 16, 4), dtype=np.uint8)
    source[:, :8] = [255, 0, 0, 255]  # the left half red, the right half blue
    source[:, 8:] = [0, 0, 255, 255]
    baked = planets.bake_layer(Image.fromarray(source, "RGBA"), {"wrap": [False, False]}, uvs, uvs2, triangles, size=32)
    pixels = np.asarray(baked)
    assert tuple(pixels[4, 16, :3]) == (255, 0, 0)  # the top of the square reads the source's left
    assert tuple(pixels[28, 16, :3]) == (0, 0, 255)  # the bottom its right
    # A layer's tiling applies, about the texture's centre: doubled across u (which runs down the
    # baked square), wrapping, the top reads the source's right (blue), a quarter down its left.
    tiled = np.asarray(planets.bake_layer(Image.fromarray(source, "RGBA"), {"tiling": [2, 1], "wrap": [True, True]}, uvs, uvs2, triangles, size=32))
    assert tuple(tiled[4, 16, :3]) == (0, 0, 255) and tuple(tiled[12, 16, :3]) == (255, 0, 0)


@pytest.mark.game_data
def test_the_planets_from_the_game_s_data(storage, tmp_path: Path):
    index = planets.export(storage, tmp_path)
    ids = {p["id"]: p["kind"] for p in index["planets"]}
    templates = {"play-large", "play-medium", "play-small"}
    assert {k for k, v in ids.items() if v == "play"} == templates
    assert {p["id"] for p in index["planets"] if p.get("style") == "nexus"} == templates  # each wears it unless told otherwise
    assert {k for k, v in ids.items() if v == "world"} == {"azeroth", "korhal", "sanctuary"}
    assert {k for k, v in ids.items() if v == "painted"} == {f"planet-{n}" for n in range(1, 10)}
    # The Play screen's own set, where the game puts it (its camera at y -41 looking along +y).
    play = {p["id"]: p for p in index["play"]}
    assert play["play-large"]["position"] == pytest.approx([-48.2, 64.6, 25.4], abs=0.1)
    assert play["play-large"]["radius"] == pytest.approx(27.3, abs=0.1)
    assert play["play-small"]["radius"] < play["play-medium"]["radius"] < play["play-large"]["radius"]

    large = json.loads((tmp_path / "planets" / "play-large.json").read_text())
    # A base: its painting and glow; its hex grid and ring are the nexus style's.
    [body] = large["parts"]
    assert body["role"] == "surface" and body["shape"] == {"kind": "sphere", "radius": pytest.approx(1, abs=1e-3), "side": "near"}  # the body's radius is the unit
    assert body["mapping"]["kind"] == "sphere" and body["mapping"]["pole"][2] > 0.95  # a uv sphere about z
    assert len(body["materials"]) >= 2  # its painting and its glow
    assert all(large["materials"][k]["blend"] == "opaque" or (large["materials"][k]["diffuse"] or {}).get("texture")
               for k in body["materials"])  # no hex grid
    # Its masks read the mesh's second texture coordinates: baked over the first, per planet.
    masks = [m["alphaMasks"][0] for m in (large["materials"][k] for k in body["materials"]) if m["alphaMasks"]]
    assert masks and all("uvSet" not in m and m["texture"].startswith("play-large-") for m in masks)
    assert all(large["textures"][m["texture"]]["width"] == planets.BAKE_SIZE for m in masks)
    assert "mapping2" not in body and large.get("light") is None
    # It wears the nexus style as its own shield is: unshaded, the grid as the data has it.
    nexus = json.loads((tmp_path / "planets" / "style-nexus.json").read_text())
    [grid] = nexus["parts"][0]["materials"]
    worn = {k: json.loads((tmp_path / "planets" / f"{k}.json").read_text())["tuning"] for k in templates}
    assert all(t["style"] == "nexus" and t["variants"]["nexus"]["shade"] == {"strength": 0} for t in worn.values())
    shields = {k: t["variants"]["nexus"]["materials"][grid] for k, t in worn.items()}
    assert shields["play-large"]["scale"] == 1 and shields["play-large"]["strength"] == 1  # the style's own grid
    assert shields["play-medium"]["scale"] == pytest.approx(1.5) and shields["play-small"]["scale"] == pytest.approx(6)  # tiled 6, 4, 1 round
    assert shields["play-medium"]["strength"] == pytest.approx(0.8) and shields["play-small"]["spread"] == pytest.approx(0.2)
    for shield in shields.values():
        assert math.hypot(*shield["spreadFrom"]) == pytest.approx(1, abs=1e-3) and shield["spreadFrom"][1] < 0  # on its face
    assert shields["play-medium"]["spreadFrom"][0] < 0 < shields["play-large"]["spreadFrom"][0]  # brightest on the left, and right

    korhal = json.loads((tmp_path / "planets" / "korhal.json").read_text())
    shells = [p for p in korhal["parts"] if p["shape"]["kind"] == "sphere" and p["shape"]["side"] == "far"]
    assert shells and shells[0]["shape"]["radius"] > 1 and shells[0]["mapping"]["kind"] == "linear"  # its own atmosphere shell
    body = korhal["parts"][0]
    assert body["shape"]["radius"] == pytest.approx(1, abs=0.01) and body["mapping"]["kind"] == "sphere"
    # Its clouds read its second coordinates too, which a rule fits: mapped, not baked.
    assert body["mapping2"]["kind"] in ("sphere", "linear")
    assert not any(k.endswith("-korhal") for k in body["materials"])
    assert body["color"] != [255, 255, 255, 255]  # the vertex colour its rim glow multiplies by
    # The body's rim glow: an additive colour over it, there only where the surface turns away.
    adds = [korhal["materials"][m] for m in body["materials"] if korhal["materials"][m]["blend"] == "add"]
    assert any(m["diffuse"] and "color" in m["diffuse"] and m["diffuse"]["fresnel"]["type"] == 1 for m in adds)
    assert grid not in body["materials"]  # no hex grid
    assert len(korhal["light"]["directional"]) == 3 and korhal["light"]["exposure"] == 1.5
    assert korhal["tuning"] == planets.TUNING["korhal"]
    for light in korhal["light"]["directional"]:
        assert math.hypot(*light["direction"]) == pytest.approx(1, abs=1e-3)

    # The nexus style: the Play planet's hex grid as a shield, its ring, and a light that shades.
    assert index["styles"] == [{"id": "nexus"}]
    assert [p["role"] for p in nexus["parts"]] == ["shield", "ring"] and nexus["kind"] == "style"
    assert nexus["parts"][0]["pattern"] == {"kind": "hex", "across": planets.HEX_ACROSS}  # its hexes drawn by the app, as many as the texture's
    assert all(nexus["materials"][k]["blend"] != "opaque" for k in nexus["parts"][0]["materials"])
    assert nexus["tuning"] == {"materials": {k: planets.NEXUS_SHIELD for k in nexus["parts"][0]["materials"]}}
    assert all(k in korhal["materials"] for k in korhal["tuning"]["materials"])  # its tuning names its own materials
    assert "light" not in nexus and 0 < nexus["shade"]["strength"] <= 1 and [p["role"] for p in korhal["parts"]][:1] == ["surface"]

    painted = json.loads((tmp_path / "planets" / "planet-1.json").read_text())
    assert len(painted["parts"]) == 1 and painted["parts"][0]["shape"]["kind"] == "sphere"
    assert painted["materials"][painted["parts"][0]["materials"][0]]["unshaded"] is False  # lit, as the flyby lights it
    for file in (large, korhal, painted):
        for texture in file["textures"].values():
            assert (tmp_path / texture["file"]).stat().st_size > 0
