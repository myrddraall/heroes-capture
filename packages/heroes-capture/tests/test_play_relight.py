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
    [light] = play_relight.fit_lights(facings, means, weights)
    shown = play_relight.lit_by(facings, [light])
    expected = play_relight.reach(facings, 0.2, 3.0)[:, None] * [2.0, 1.8, 1.6]
    assert np.average(np.abs(shown - expected), axis=0, weights=weights) == pytest.approx([0, 0, 0], abs=0.05)


def test_more_lights_are_added_only_where_one_leaves_much_unexplained():
    facings = np.linspace(-1, 1, play_relight.BINS)
    weights = np.ones_like(facings)
    one = play_relight.reach(facings, 0.0, 2.0)[:, None] * [1.0, 0.8, 0.5]
    assert len(play_relight.fit_lights(facings, one, weights)) == 1
    two = one + play_relight.reach(facings, 0.0, 16.0)[:, None] * [3.0, 3.0, 3.0]  # a warm wash and a white hot spot
    lights = play_relight.fit_lights(facings, two, weights)
    assert 2 <= len(lights) <= play_relight.MAX_LIGHTS
    assert (np.abs(play_relight.lit_by(facings, lights) - two) / (two + 0.05 * two.max())).max() < 0.1  # by relative error, from a set of shapes


def test_a_painted_texture_comes_apart_into_an_even_surface_and_lights_that_paint_it_again():
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
    # Its lights paint it again as the game painted it, where it held detail.
    lights = play_relight.planet_lights(painting, m)
    assert 1 <= len(lights) <= play_relight.MAX_LIGHTS
    for light in lights:
        assert math.hypot(*light["direction"]) == pytest.approx(1, abs=1e-4)
        assert -np.array(light["direction"]) @ towards > 0.99  # it travels away from where it comes from
    again = made * play_relight.lit_by(n @ -np.array(lights[0]["direction"]),
                                       [{"color": l["color"], "wrap": l.get("wrap", 0.0), "softness": l.get("softness", 1.0)}
                                        for l in lights])
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
    painting = {"at": [0.3, 0.6], "lights": [{"color": [1.0, 1.0, 1.0], "wrap": 0.2, "softness": 3.0}]}
    one, other = play_relight.Mapping(TILTED, {"offset": [0.075, 0.0]}), play_relight.Mapping(TILTED, {"offset": [0.1, 0.0]})
    [a], [b] = play_relight.planet_lights(painting, one), play_relight.planet_lights(painting, other)
    assert a["wrap"] == 0.2 and a["softness"] == 3.0 and a["color"] == [1.0, 1.0, 1.0]
    # Where each planet's mapping puts that point: the light comes straight from there.
    for mapping, light in ((one, a), (other, b)):
        s, t = mapping.coordinates(-np.array([light["direction"]]))
        assert (s[0], t[0]) == pytest.approx((0.3, 0.6), abs=1e-4)
    # The second mapping turns the texture a fortieth further round its pole: so does the light.
    turn = math.degrees(math.acos(np.clip(np.dot(a["direction"], b["direction"]), -1, 1)))
    assert 0 < turn < 9
