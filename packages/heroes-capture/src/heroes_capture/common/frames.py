"""Images on disk. Screenshots: raw arrays (.npy), which the stitch reads without decoding; paths
are given without the extension. And the zlib level the big output PNGs are written at."""

from pathlib import Path

import numpy as np


PNG_COMPRESSION = 1  # zlib level for the big output PNGs: much faster to write than 6, somewhat larger files


def save_frame(stem: Path, frame: np.ndarray) -> None:
    """An RGB frame as <stem>.npy (uncompressed: a few times larger than a PNG, read at disk speed)."""
    np.save(stem.with_name(stem.name + ".npy"), np.ascontiguousarray(frame), allow_pickle=False)


def frame_exists(stem: Path) -> bool:
    return stem.with_name(stem.name + ".npy").exists()


def load_frame(stem: Path) -> np.ndarray:
    """The frame as an RGB uint8 array, from <stem>.npy."""
    return np.load(stem.with_name(stem.name + ".npy"), allow_pickle=False)


def tile_stem(folder: Path, index: int, black: bool = False, chaos: bool = False) -> Path:
    """A tile's shot (its shot over black with `black`; from Chaos's side with `chaos`), as the
    capture saves it in its folder."""
    return folder / f"tile_{index:04d}{'-chaos' if chaos else ''}{'-black' if black else ''}"
