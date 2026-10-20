"""Difference matting: RGBA from two shots of the same view, over the light sky and over black (sky.py),
for a tile (matte), an element alone over the sky (cut_out), and the sky layers (difference_alpha).
A pixel that is sky in both differs by the whole light level; one that is all map is the same in
both; in between (soft edges, glass, glow) the difference is exactly the see-through share."""

import numpy as np

from heroes_capture.capture.sky import LIGHT_LEVEL
from heroes_capture.common.imaging import channel_diff, max3, mean3, min3

MATTE_BAND = 32  # rows matted at a time (see matte)


def difference_alpha(over_key: np.ndarray, over_black: np.ndarray, key_level) -> np.ndarray:
    """Alpha (0..1, float) from shots over a key and over black (float RGB), against the key's level
    (a number, or per pixel)."""
    return 1.0 - np.clip(mean3(over_key - over_black) / np.maximum(key_level, 1.0), 0.0, 1.0)


def light_level(white: np.ndarray, black: np.ndarray) -> float | None:
    """The level the game renders the matte's light sky at (sky.LIGHT_SKY, about LIGHT_LEVEL), from
    a pair of shots over it and over black (`white`: the light one): the median of the pixels that
    are sky (black in the black shot, flat and bright in the light one); None when fewer than 2000
    are."""
    w, b = white.astype(np.float32), black.astype(np.float32)
    w_min = min3(w)
    sky = (max3(b) <= 4) & (w_min >= 120) & (max3(w) - w_min <= 8)
    return float(np.median(w[sky].mean(axis=1))) if sky.sum() >= 2000 else None


# Pixels whose difference between the two shots isn't grey by more than this (the largest channel
# less the smallest) differ by something other than the sky showing through.
MOVED_SPREAD = 24


def moved_pixels(white: np.ndarray, black: np.ndarray) -> np.ndarray:
    """The pixels taken for something that moved between a tile's two shots: whose difference isn't
    grey (MOVED_SPREAD), in patches at least 3 pixels across (an opening). Anything that really
    moves (a unit walking in, a pulsing effect) is a patch; once the map's effects were frozen and
    the light sky a grey, what was left was one- and two-pixel specks along the floating pieces'
    edges, which differ by a hair between the shots: left to the matte, they keep their soft edge
    instead of turning opaque in the black shot's colour (dark dots along the rim)."""
    spread = np.empty(white.shape[:2], bool)
    for r0 in range(0, white.shape[0], MATTE_BAND):
        d = white[r0:r0 + MATTE_BAND].astype(np.int16) - black[r0:r0 + MATTE_BAND].astype(np.int16)
        spread[r0:r0 + MATTE_BAND] = (max3(d) - min3(d)) > MOVED_SPREAD
    if not spread.any():
        return spread
    from scipy import ndimage

    return ndimage.binary_opening(spread, structure=np.ones((3, 3), bool))


def matte(white: np.ndarray, black: np.ndarray, level: float, animated: bool = True, moved: list | None = None) -> np.ndarray:
    """Difference matting: RGBA from the shots over the light sky and over black (`white`: the light
    one). A pixel that is sky in both differs by the whole light level; one that is all map is the
    same in both; in between (soft edges, glass, glow) the difference is exactly the see-through
    share. Colour from the black shot (the map's own light, nothing of the sky in it),
    un-premultiplied. The light level is measured, not assumed (the game renders the light grey of
    sky.LIGHT_SKY at about 198): see light_level. `animated`: a pixel taken for something that moved
    between the shots (moved_pixels) left opaque (off for the elements' cut-outs: nothing moves in
    them, and a tinted see-through glow, a core's shield, has a difference that isn't grey).
    `moved`: given, the number of pixels taken for moving is appended to it."""
    # In bands of rows small enough to stay in the processor's cache: every pixel is worked
    # out on its own, and with whole tiles the threads spent their time waiting on memory.
    out = np.empty((*white.shape[:2], 4), dtype=np.uint8)
    changed_all = moved_pixels(white, black) if animated else None
    if moved is not None:
        moved.append(int(changed_all.sum()) if changed_all is not None else 0)
    for r0 in range(0, white.shape[0], MATTE_BAND):
        r1 = r0 + MATTE_BAND
        w = white[r0:r1].astype(np.float32)
        b = black[r0:r1].astype(np.float32)
        d = w - b
        d_mean = mean3(d)
        see_through = np.clip(d_mean / level, 0, 1)
        # Something that moved between the shots (moved_pixels): no matte there.
        changed = changed_all[r0:r1] if changed_all is not None else np.zeros(d.shape[:2], bool)
        alpha = np.where(changed, 1.0, 1.0 - see_through)
        alpha = np.where(d_mean < -8, 1.0, alpha)
        # Un-premultiplied colour: the black shot over alpha. Only where it is see-through:
        # elsewhere (most pixels) alpha is exactly 1 and the colour is the black shot as it is.
        rgb = b
        part = alpha < 1.0
        a_part = alpha[part]
        rgb[part] = np.where(a_part[:, None] > 1 / 255, np.clip(b[part] / np.maximum(a_part, 1 / 255)[:, None], 0, 255), 0)
        out[r0:r1, :, :3] = rgb.round()
        out[r0:r1, :, 3] = (alpha * 255).round()
    return out


def matte_with_alpha(white: np.ndarray, alpha: np.ndarray, level: float) -> np.ndarray:
    """RGBA from one shot over the light sky and an alpha known from another pair of the same view
    (a tile from Chaos's side: only its team colour differs from its shots from Order, which give
    the see-through share): the shot is alpha·colour + (1 − alpha)·level, so the colour is the
    shot less the sky's share, over alpha; where it is opaque (most pixels), the shot as it is."""
    out = np.empty((*white.shape[:2], 4), dtype=np.uint8)
    for r0 in range(0, white.shape[0], MATTE_BAND):
        r1 = r0 + MATTE_BAND
        w = white[r0:r1].astype(np.float32)
        a = alpha[r0:r1].astype(np.float32) / 255.0
        rgb = w
        part = a < 1.0
        a_part = a[part]
        rgb[part] = np.where(a_part[:, None] > 1 / 255,
                             np.clip((w[part] - (1.0 - a_part)[:, None] * level) / np.maximum(a_part, 1 / 255)[:, None], 0, 255), 0)
        out[r0:r1, :, :3] = rgb.round()
        out[r0:r1, :, 3] = alpha[r0:r1]
    return out


# An element's cut-out from its shots alone over the sky (cut_out), opacity out of 255. At or below
# VEIL: transparent. At or above SOLID: opaque; soft edges, glass and glows lie between. Measured
# over the white sky, whose bloom left a veil on the sky (99.9% of its pixels at 7 or less) and took
# solid parts down to about 236 (Battlefield of Eternity's tower, camp and Immortal: the elements
# probe); over the light grey the sky comes out at exactly 0 and solid parts at exactly 255 (the
# matte probe), so they hold with room to spare.
VEIL_ALPHA, SOLID_ALPHA = 8, 235
# A pixel of an element's shot within this much (the largest of the three channels) of the same
# pixel in its tile's shot with everything hidden is something that can't be hidden (a cliff
# doodad), not the element: two shots over the sky differ by almost nothing (the probe: at most
# 0.006% of pixels between two in a row).
EMPTY_MATCH = 10


def cut_out(white: np.ndarray, black: np.ndarray, centre: tuple[float, float], radius: float,
            empty: np.ndarray | None = None, others: list[tuple[float, float]] = (), level: float | None = None) -> np.ndarray:
    """An element's cut-out (RGBA) from its two shots alone over the sky, over white and over black:
    matted as the stitch mattes a tile, then only the circle round it kept (`centre` and `radius` in
    screen pixels), the sky's faint veil made transparent and the solid parts opaque, their colour
    the black shot's (VEIL_ALPHA, SOLID_ALPHA). `empty`: its tile's shot over white with everything
    hidden; what is the same in both is what can't be hidden (the map's cliff doodads), left out.
    `others`: where other elements are on the shot (screen pixels), whose own pixels these may be
    (rubble shot with all the rest of the rubble lying there): only what is nearer `centre` than
    any of them is kept. `level`: the light sky's level, measured on the whole shot (light_level),
    when these are only part of it (circle_box: the matte works pixel by pixel, so a part of the
    shots gives that part of the cut-out exactly); measured on these when not given."""
    rgba = matte(white, black, level or light_level(white, black) or LIGHT_LEVEL, animated=False)
    alpha = rgba[..., 3]
    yy, xx = np.ogrid[: alpha.shape[0], : alpha.shape[1]]
    own = (xx - centre[0]) ** 2 + (yy - centre[1]) ** 2
    alpha[own > radius**2] = 0
    for ox, oy in others:
        alpha[(xx - ox) ** 2 + (yy - oy) ** 2 < own] = 0
    if empty is not None:
        alpha[channel_diff(white, empty) <= EMPTY_MATCH] = 0
    alpha[alpha <= VEIL_ALPHA] = 0
    solid = alpha >= SOLID_ALPHA
    rgba[solid, :3] = black[solid]
    alpha[solid] = 255
    return rgba
