"""Phase correlation: how far one image's content moved in another (the stitches' matching)."""

import numpy as np


def phase_correlate(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """(dy, dx, strength) with b(y, x) ≈ a(y + dy, x + dx), to a fraction of a pixel;
    strength near 1 is a clean match."""
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1])).astype(np.float32)
    fa = np.fft.rfft2((a - a.mean()) * win)
    fb = np.fft.rfft2((b - b.mean()) * win)
    cross = fb * np.conj(fa)
    cross /= np.abs(cross) + 1e-9
    corr = np.fft.irfft2(cross, s=a.shape)
    y, x = np.unravel_index(int(np.argmax(corr)), corr.shape)

    def refine(before: float, peak: float, after: float) -> float:
        # Vertex of the parabola through the peak and its neighbours.
        denom = before - 2 * peak + after
        return 0.5 * (before - after) / denom if denom else 0.0

    h, w = corr.shape
    fy = y + refine(corr[(y - 1) % h, x], corr[y, x], corr[(y + 1) % h, x])
    fx = x + refine(corr[y, (x - 1) % w], corr[y, x], corr[y, (x + 1) % w])
    dy = fy - h if fy > h / 2 else fy
    dx = fx - w if fx > w / 2 else fx
    return -float(dy), -float(dx), float(corr.max())
