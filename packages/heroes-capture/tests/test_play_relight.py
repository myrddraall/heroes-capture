"""The Play screen planets' painting taken apart (ui/play_relight.py) and the patch fill it uses
(ui/patch_fill.py), on painted textures made here, so what they should find is known."""

import math

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from heroes_capture.ui import patch_fill, play_relight
from heroes_capture.ui.planets import PLAIN_MAPPING

TILTED = {**PLAIN_MAPPING, "pole": [0.2588, 0.0, 0.9659], "meridian": [0.0, 1.0, 0.0], "u0": 0.99996}


def surface(height: int, width: int, seed: int = 1) -> np.ndarray:
    """A made-up planet's surface (as stored): blue seas and gold land, in blobs."""
    rng = np.random.default_rng(seed)
    land = gaussian_filter(rng.random((height, width)), (1, 1), mode="wrap") > 0.5
    speckle = gaussian_filter(rng.random((height, width, 3)), (1, 1, 0), mode="wrap")
    sea, gold = np.array([0.08, 0.1, 0.35]), np.array([0.45, 0.33, 0.12])
    return np.where(land[..., None], gold, sea) * (0.7 + 0.6 * speckle)


def painted(albedo: np.ndarray, mapping: play_relight.Mapping, towards: np.ndarray, wrap: float, softness: float,
            colour=(2.0, 1.8, 1.6)) -> np.ndarray:
    """A surface painted under one light (as stored, 0 to 1: the app lights colours as stored)."""
    n, _, _ = mapping.directions(*albedo.shape[:2])
    lit = play_relight.reach(n @ towards, wrap, softness)[..., None] * np.array(colour)
    return np.clip(albedo * lit, 0, 1)


def test_a_mapping_s_coordinates_and_directions_undo_each_other():
    for mapping, layer in ((TILTED, {"offset": [0.075, 0.0]}), ({**PLAIN_MAPPING, "flip": True}, {}),
                           (TILTED, {"offset": [0.1, 0.0], "tiling": [1, 1], "angle": 0.0})):
        m = play_relight.Mapping(mapping, layer)
        n, _, used = m.directions(32, 64)
        s, t = m.coordinates(n[used])
        rows, cols = np.nonzero(used)
        assert np.abs((s - (cols + 0.5) / 64 + 0.5) % 1 - 0.5).max() < 1e-6  # round the sphere: 0 and 1 are one
        assert t == pytest.approx((rows + 0.5) / 32, abs=1e-6)
        assert np.linalg.norm(n, axis=-1) == pytest.approx(1)


def test_the_light_a_texture_is_painted_under_is_found_with_its_falloff():
    m = play_relight.Mapping(TILTED, {"offset": [0.075, 0.0]})
    albedo = surface(64, 128)
    towards = np.array([-0.64, 0.51, -0.57])
    towards /= np.linalg.norm(towards)
    stored = painted(albedo, m, towards, 0.2, 3.0)
    n, polar, used = m.directions(64, 128)
    found = play_relight.light_direction(stored @ play_relight.LUMA, n, np.sin(polar) * used,
                                         np.random.default_rng(0))
    assert math.degrees(math.acos(np.clip(found @ towards, -1, 1))) < 4
    # Its falloff, against the surface: one light, of the shape and colour it was painted with.
    ratio = stored / albedo
    facings, means, weights = play_relight.falloff(ratio, n @ towards, np.sin(polar) * used)
    light = play_relight.fit_light(facings, means, weights)
    shown = play_relight.lit_by(facings, light)
    expected = play_relight.reach(facings, 0.2, 3.0)[:, None] * [2.0, 1.8, 1.6]
    assert np.average(np.abs(shown - expected), axis=0, weights=weights) == pytest.approx([0, 0, 0], abs=0.05)


def test_the_light_s_shape_is_the_material_s_one_wrap_and_drop_off_found_among_the_editor_s():
    facings = np.linspace(-1, 1, play_relight.BINS)
    weights = np.ones_like(facings)
    painted_so = play_relight.reach(facings, 0.3, 2.0)[:, None] * [1.0, 0.8, 0.5]
    light = play_relight.fit_light(facings, painted_so, weights)
    assert (light["wrap"], light["softness"]) == pytest.approx((0.3, 2.0), abs=0.05, rel=0.1)
    assert (np.abs(play_relight.lit_by(facings, light) - painted_so) / (painted_so + 0.05 * painted_so.max())).max() < 0.1
    # On the material: its Wrap and Drop off, each where it isn't the app's own.
    assert play_relight.painting_surface({"wrap": 0.3, "softness": 2.0}) == {"wrap": 0.3, "dropOff": 2.0}
    assert play_relight.painting_surface({"wrap": 0.0, "softness": 1.0}) == {}


def test_a_painted_texture_comes_apart_into_an_even_surface_and_a_light_that_paints_it_again():
    m = play_relight.Mapping(TILTED, {"offset": [0.075, 0.0]})
    albedo = surface(96, 192, seed=3)
    towards = np.array([0.33, 0.75, -0.58])
    towards /= np.linalg.norm(towards)
    stored = painted(albedo, m, towards, 0.1, 2.5)
    made, painting = play_relight.make_surface(stored, m, np.random.default_rng(0))
    assert made.shape == albedo.shape and np.isfinite(made).all() and 0 <= made.min() and made.max() <= 1
    # Where the painting held detail, the surface is the one painted, but for its scale (each
    # channel's): between the poles, as each pole is rebuilt (so none pinches).
    n, polar, used = m.directions(96, 192)
    trust, _ = play_relight._detail(stored, used)
    band = (polar > math.radians(play_relight.CAP_BLEND[1])) & (polar < math.pi - math.radians(play_relight.CAP_BLEND[1]))
    kept = (trust > 0.99) & band
    ratio = (made / albedo)[kept]
    assert kept.sum() > 300 and (np.std(ratio, axis=0) / np.mean(ratio, axis=0)).max() < 0.1
    # Where it was dark, it is filled with what was kept: as bright overall, not black.
    dark = (trust == 0) & band
    assert made[dark].mean() == pytest.approx(made[kept].mean(), rel=0.35)
    # Its light paints it again as the game painted it, where it held detail.
    [light] = play_relight.planet_lights(painting, m)
    assert math.hypot(*light["direction"]) == pytest.approx(1, abs=1e-4)
    assert -np.array(light["direction"]) @ towards > 0.99  # it travels away from where it comes from
    assert set(light) == {"direction", "color"}  # (its shape is its material's)
    surface_of = play_relight.painting_surface(painting)
    again = made * play_relight.lit_by(n @ -np.array(light["direction"]),
                                       {"color": light["color"], "wrap": surface_of.get("wrap", 0.0),
                                        "softness": surface_of.get("dropOff", 1.0)})
    assert np.abs(again - stored)[kept].mean() < 0.03


def test_the_fill_keeps_what_is_kept_and_fills_the_rest_like_the_source():
    rng = np.random.default_rng(0)
    source = surface(48, 96, seed=5).astype(np.float32)
    keep = np.ones((48, 96), np.float32)
    keep[10:38, 30:70] = 0
    image = source * keep[..., None]
    out = patch_fill.fill(image, keep, source, keep > 0.5, True, rng)
    assert np.abs(out - source)[keep == 1].max() < 1e-5
    hole = keep == 0
    assert out[hole].mean(0) == pytest.approx(source[keep == 1].mean(0), rel=0.2)  # like the source, not black
    assert out[hole].std(0) == pytest.approx(source[keep == 1].std(0), rel=0.35)  # as varied, not a flat colour


def test_a_painting_s_light_comes_from_the_same_point_of_its_texture_for_every_planet_mapping_it():
    painting = {"at": [0.3, 0.6], "color": [1.0, 1.0, 1.0], "wrap": 0.2, "softness": 3.0}
    one, other = play_relight.Mapping(TILTED, {"offset": [0.075, 0.0]}), play_relight.Mapping(TILTED, {"offset": [0.1, 0.0]})
    [a], [b] = play_relight.planet_lights(painting, one), play_relight.planet_lights(painting, other)
    assert a["color"] == [1.0, 1.0, 1.0] and "wrap" not in a  # (its shape is its material's)
    # Where each planet's mapping puts that point: the light comes straight from there.
    for mapping, light in ((one, a), (other, b)):
        s, t = mapping.coordinates(-np.array([light["direction"]]))
        assert (s[0], t[0]) == pytest.approx((0.3, 0.6), abs=1e-4)
    # The second mapping turns the texture a fortieth further round its pole: so does the light.
    turn = math.degrees(math.acos(np.clip(np.dot(a["direction"], b["direction"]), -1, 1)))
    assert 0 < turn < 9


def test_a_rim_s_lean_turns_the_view_as_the_app_turns_it_and_back():
    for yaw, pitch in [(0.3, -0.2), (-0.7, 0.4), (0.0, 0.0)]:
        lean = play_relight.lean_of(yaw, pitch)
        assert np.linalg.norm(lean) == pytest.approx(1, abs=1e-4)
        assert play_relight.rim_turn(np.array(lean)) == pytest.approx((yaw, pitch), abs=1e-4)
    # Leaning to the right favours the right of the rim (the side a rim light's lean names).
    n = np.array([[-1.0, 0, 0], [1.0, 0, 0]])
    to_eye = np.array([[0.0, -1, 0], [0.0, -1, 0]])
    left, right = play_relight.rim_reach(n, to_eye, 0.5, 0.0, 0.0, 1.0)
    assert right > left


def test_a_rim_light_lights_a_planet_from_the_game_s_camera_as_a_light_from_behind_does():
    # The large Play screen planet's light (behind it, to the right and below) and its material.
    light = np.array([0.32774, 0.74514, -0.58082])
    rim = play_relight.play_rim(list(light), 1.0, 5.7218, 4.3304)
    n, to_eye, weight = play_relight._seen_points(4.3304)
    target = play_relight.reach(n @ light, 1.0, 5.7218)
    yaw, pitch = play_relight.rim_turn(np.array(rim["lean"]))
    got = rim["strength"] * play_relight.rim_reach(n, to_eye, yaw, pitch, rim["spread"], rim["softness"])
    # As it looks, close over the picture; its lit edge on the side the light is (right, below).
    g = play_relight.RIM_GAMMA
    assert np.sqrt(np.sum(weight * (got**g - target**g) ** 2) / weight.sum()) < 0.05
    assert rim["lean"][0] > 0 and rim["lean"][2] < 0
