"""Small array helpers for frames and layers: per-pixel reductions over the three colour channels
(without numpy's slow reduction over a short last axis), a colour un-premultiplied by its alpha,
and how much two shots differ."""

import numpy as np


def max3(a: np.ndarray) -> np.ndarray:
    """a.max(axis=2) for three channels, without numpy's slow reduction over a short last axis."""
    return np.maximum(np.maximum(a[:, :, 0], a[:, :, 1]), a[:, :, 2])


def min3(a: np.ndarray) -> np.ndarray:
    return np.minimum(np.minimum(a[:, :, 0], a[:, :, 1]), a[:, :, 2])


def mean3(a: np.ndarray) -> np.ndarray:
    """a.mean(axis=2) for three float channels, to the bit: the same sum in the same order and
    the same division (by a count, as numpy's mean does)."""
    total = a[:, :, 0] + a[:, :, 1]
    total += a[:, :, 2]
    return np.true_divide(total, np.intp(3), out=total, casting="unsafe")


def unpremultiply(colour: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Colour (float, premultiplied: what shows over black) divided by its alpha (0..1), clipped to
    0..255; nothing where the alpha is under one level in 255."""
    return np.where(alpha[..., None] > 1 / 255, np.clip(colour / np.maximum(alpha, 1 / 255)[..., None], 0, 255), 0)


def channel_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """How much two shots differ at each pixel: the largest of the three channels' differences."""
    return np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)


def nonzero_box(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """The rows and columns (v0, v1, u0, u1) of the smallest box round a mask's set (or nonzero)
    pixels; None when there are none."""
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if not len(rows):
        return None
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
