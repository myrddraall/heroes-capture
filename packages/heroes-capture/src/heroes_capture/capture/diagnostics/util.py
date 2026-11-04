"""What a diagnostic needs that doesn't drive the game (probes.py has the rest): the screen round a
spot, and what changed between two shots of the same view."""

import numpy as np

from heroes_capture.common.geometry import circle_box, screen_point
from heroes_capture.common.imaging import nonzero_box


def crop_round(frame: np.ndarray, spot: dict, camera: dict, manifest: dict, radius: float) -> np.ndarray:
    """The square of the frame `radius` cells round a cell, the strip's column left out."""
    ppc = manifest["pxPerCell"]
    v0, v1, u0, u1 = circle_box(screen_point(spot, camera, manifest["screen"], ppc), radius * ppc, frame.shape)
    left = int((manifest.get("status") or {}).get("pageLeft", 0))
    return np.ascontiguousarray(frame[v0:v1, max(u0, left):u1, :3])


def change_map(before: np.ndarray, after: np.ndarray, threshold: int = 40) -> tuple[float, np.ndarray]:
    """What changed between two shots of the same view: the share of pixels that differ by more
    than `threshold` in some channel, and a picture of it (the second shot where it differs, the
    rest dimmed)."""
    a, b = before.astype(np.int16), after.astype(np.int16)
    changed = np.abs(a - b).max(axis=2) > threshold
    picture = (a * 0.35).astype(np.uint8)
    picture[changed] = b[changed].astype(np.uint8)
    return float(changed.mean()), picture


def describe(mask: np.ndarray) -> str:
    """A difference in numbers: the share of pixels changed, and the box round them."""
    box = nonzero_box(mask)
    if box is None:
        return "no pixels changed"
    v0, v1, u0, u1 = box
    fill = float(mask[v0:v1, u0:u1].mean())
    return f"{mask.mean() * 100:6.3f}% of pixels changed, within x {u0}..{u1}, y {v0}..{v1} ({fill * 100:.0f}% of that box)"
