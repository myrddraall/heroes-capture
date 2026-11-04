"""Filling the missing part of an image with patches of a source image, so the fill looks like the
source: multi-scale patch-based completion (Wexler, Shechtman and Irani, "Space-Time Completion of
Video", 2007), each patch's nearest source patch found by PatchMatch (Barnes et al., 2009) and the
fill's colours kept to the source's spread at each step (Kaspar et al., "Self Tuning Texture
Optimization", 2015: without it the fill drifts to the source's flattest patches).

The image is filled coarse to fine. At each scale, a few rounds of: every pixel's patch matched to
its nearest source patch, then each pixel voted for by the patches over it (weighted by how well
each matched), then the pixels kept from the image put back. The kept part is a weight (0 to 1), so
a fill blends into what is kept over a soft edge.
"""

import numpy as np
from scipy.ndimage import binary_erosion, zoom

# The patches' side, in pixels.
PATCH = 7
_R = PATCH // 2
# The fill starts at the scale where its image's short side is about this (or where the source has
# too few whole patches left: MIN_SOURCE_PATCHES).
MIN_SIZE = 24
MIN_SOURCE_PATCHES = 100


def _down(image: np.ndarray) -> np.ndarray:
    """Half the size, each pixel the mean of the four it covers."""
    h, w = image.shape[0] // 2, image.shape[1] // 2
    image = image[:h * 2, :w * 2]
    return image.reshape(h, 2, w, 2, *image.shape[2:]).mean((1, 3))


def _padded(image: np.ndarray, wrap: bool) -> np.ndarray:
    """The image with room for a patch about every pixel: mirrored at the top and bottom, round the
    sides where it wraps (a texture round a sphere), else mirrored there too."""
    image = np.pad(image, ((_R, _R), (0, 0), (0, 0)), mode="reflect")
    return np.pad(image, ((0, 0), (_R, _R), (0, 0)), mode="wrap" if wrap else "reflect")


class _Source:
    """A source image's pixels, and where a whole patch of it may be taken from (its centres)."""

    def __init__(self, image: np.ndarray, usable: np.ndarray):
        self.pixels = np.pad(image, ((_R, _R), (_R, _R), (0, 0)), mode="edge").astype(np.float32)
        self.ok = binary_erosion(usable, np.ones((PATCH, PATCH)), border_value=0)
        self.centres = np.argwhere(self.ok)
        self.h, self.w = usable.shape

    def valid(self, qy: np.ndarray, qx: np.ndarray) -> np.ndarray:
        inside = (qy >= 0) & (qy < self.h) & (qx >= 0) & (qx < self.w)
        out = np.zeros(qy.shape, bool)
        out[inside] = self.ok[qy[inside], qx[inside]]
        return out

    def random(self, rng: np.random.Generator, shape: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
        pick = self.centres[rng.integers(len(self.centres), size=int(np.prod(shape)))]
        return pick[:, 0].reshape(shape), pick[:, 1].reshape(shape)


def _distance(padded: np.ndarray, source: _Source, qy: np.ndarray, qx: np.ndarray) -> np.ndarray:
    """Each pixel's patch's squared difference from the source patch at (qy, qx)."""
    h, w = qy.shape
    d = np.zeros((h, w), np.float32)
    for dy in range(PATCH):
        for dx in range(PATCH):
            d += ((padded[dy:dy + h, dx:dx + w] - source.pixels[qy + dy, qx + dx]) ** 2).sum(-1)
    return d


def _match(padded: np.ndarray, source: _Source, qy: np.ndarray, qx: np.ndarray, rng: np.random.Generator,
           rounds: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each pixel's nearest source patch, by PatchMatch from the matches given: neighbours' matches
    passed on (at 8, 4, 2 and 1 pixels, every way), a search about each match at halving radii,
    and one random patch more."""
    d = _distance(padded, source, qy, qx)

    def try_(cy: np.ndarray, cx: np.ndarray) -> None:
        nonlocal qy, qx, d
        ok = source.valid(cy, cx)
        cy, cx = np.where(ok, cy, qy), np.where(ok, cx, qx)
        dc = _distance(padded, source, cy, cx)
        better = dc < d
        qy, qx, d = np.where(better, cy, qy), np.where(better, cx, qx), np.where(better, dc, d)

    for _ in range(rounds):
        for step in (8, 4, 2, 1):
            for sy, sx in ((0, step), (step, 0), (0, -step), (-step, 0)):
                try_(np.roll(qy, (sy, sx), (0, 1)) + sy, np.roll(qx, (sy, sx), (0, 1)) + sx)
        radius = max(source.h, source.w)
        while radius >= 1:
            try_(qy + rng.integers(-radius, radius + 1, qy.shape), qx + rng.integers(-radius, radius + 1, qx.shape))
            radius //= 2
        try_(*source.random(rng, qy.shape))
    return qy, qx, d


def _vote(source: _Source, qy: np.ndarray, qx: np.ndarray, d: np.ndarray, wrap: bool, sharpness: float) -> np.ndarray:
    """Each pixel as the patches over it have it, each patch weighted by how well it matched: by
    exp(-d / 2s), s the `sharpness` percentile of the matches' distances (lower: nearer the best
    patch, sharper)."""
    h, w = qy.shape
    s = np.percentile(d, sharpness) + 1e-6
    weight = np.exp(-np.minimum(d / (2 * s), 30)).astype(np.float64)
    total = np.zeros((h + 2 * _R, w + 2 * _R, 3))
    weights = np.zeros((h + 2 * _R, w + 2 * _R))
    for dy in range(PATCH):
        for dx in range(PATCH):
            total[dy:dy + h, dx:dx + w] += weight[..., None] * source.pixels[qy + dy, qx + dx]
            weights[dy:dy + h, dx:dx + w] += weight
    if wrap:  # what fell past one side belongs at the other
        total[:, _R:2 * _R] += total[:, w + _R:]
        total[:, w:w + _R] += total[:, :_R]
        weights[:, _R:2 * _R] += weights[:, w + _R:]
        weights[:, w:w + _R] += weights[:, :_R]
    total, weights = total[_R:_R + h, _R:_R + w], weights[_R:_R + h, _R:_R + w]
    return (total / np.maximum(weights, 1e-30)[..., None]).astype(np.float32)


def _spread_like(image: np.ndarray, keep: np.ndarray, source: np.ndarray, usable: np.ndarray) -> np.ndarray:
    """The filled part's colours, each channel given the source's spread (by rank)."""
    filled = keep < 0.5
    if filled.sum() < 16:
        return image
    out = image.copy()
    for ch in range(3):
        values = np.sort(source[..., ch][usable])
        x = image[..., ch][filled]
        rank = np.argsort(np.argsort(x)) / max(len(x) - 1, 1)
        out[..., ch][filled] = np.interp(rank, np.linspace(0, 1, len(values)), values)
    return out


def _doubled(matches: np.ndarray, h: int, w: int) -> np.ndarray:
    """A coarser scale's matches (source coordinates) at the next: each doubled, over 2 by 2 pixels,
    to `h` by `w`."""
    out = np.repeat(np.repeat(matches * 2, 2, 0), 2, 1)
    return np.pad(out, ((0, max(0, h - out.shape[0])), (0, max(0, w - out.shape[1]))), mode="edge")[:h, :w]


def fill(image: np.ndarray, keep: np.ndarray, source: np.ndarray, usable: np.ndarray, wrap: bool,
         rng: np.random.Generator) -> np.ndarray:
    """`image` (h, w, 3) filled where `keep` (h, w, 0 to 1: how much of the image to keep) is below 1,
    from patches of `source` (its pixels where `usable`); round its sides where it `wrap`s."""
    levels = [(image.astype(np.float32), keep.astype(np.float32), source.astype(np.float32), usable)]
    while min(levels[-1][0].shape[:2]) // 2 >= MIN_SIZE and min(levels[-1][2].shape[:2]) // 2 >= 3 * PATCH:
        img, k, src, use = levels[-1]
        half = _down(use.astype(np.float32)) > 0.999
        if binary_erosion(half, np.ones((PATCH, PATCH)), border_value=0).sum() < MIN_SOURCE_PATCHES:
            break
        levels.append((_down(img), _down(k), _down(src), half))
    levels.reverse()
    if not binary_erosion(levels[0][3], np.ones((PATCH, PATCH)), border_value=0).any():
        raise ValueError("the source has no whole patch to fill from")
    current = qy = qx = None
    for n, (img, k, src, use) in enumerate(levels):
        h, w = img.shape[:2]
        s = _Source(src, use)
        if current is None:
            # Started from random patches of the source: from a flat start the fill settles grey.
            qy, qx = s.random(rng, (h, w))
            current = k[..., None] * img + (1 - k[..., None]) * _vote(s, qy, qx, np.zeros((h, w), np.float32), wrap, 75)
        else:
            current = zoom(current, (h / current.shape[0], w / current.shape[1], 1), order=1)
            qy = np.clip(_doubled(qy, h, w) + (np.arange(h)[:, None] % 2), 0, s.h - 1)
            qx = np.clip(_doubled(qx, h, w) + (np.arange(w)[None, :] % 2), 0, s.w - 1)
            bad = ~s.valid(qy, qx)
            ry, rx = s.random(rng, (int(bad.sum()),))
            qy[bad], qx[bad] = ry, rx
            current = k[..., None] * img + (1 - k[..., None]) * current
        last = n == len(levels) - 1
        for _ in range(8 if n == 0 else max(3, 6 - n)):
            qy, qx, d = _match(_padded(current, wrap), s, qy, qx, rng, 2 if n == 0 else 1)
            voted = _spread_like(_vote(s, qy, qx, d, wrap, 40 if last else 75), k, src, use)
            current = k[..., None] * img + (1 - k[..., None]) * voted
    return current
