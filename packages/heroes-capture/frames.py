"""Screenshots on disk: raw arrays (.npy), which the stitch reads without decoding, or PNG from
older runs. Paths are given without the extension."""

from pathlib import Path

import numpy as np
from PIL import Image


def save_frame(stem: Path, frame: np.ndarray) -> None:
    """An RGB frame as <stem>.npy (uncompressed: a few times larger than a PNG, read at disk speed)."""
    np.save(stem.with_name(stem.name + ".npy"), np.ascontiguousarray(frame), allow_pickle=False)


def frame_exists(stem: Path) -> bool:
    return stem.with_name(stem.name + ".npy").exists() or stem.with_name(stem.name + ".png").exists()


def load_frame(stem: Path) -> np.ndarray:
    """The frame as an RGB uint8 array, from <stem>.npy, else <stem>.png."""
    npy = stem.with_name(stem.name + ".npy")
    if npy.exists():
        return np.load(npy, allow_pickle=False)
    return np.asarray(Image.open(stem.with_name(stem.name + ".png")).convert("RGB"))
