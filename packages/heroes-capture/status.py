"""The status strip: how the capture knows a command has been carried out and rendered.

The map script (capture-script.mjs, hrsCap_Status*) draws a dialog in the top-left corner of
the screen: one column of black-or-white cells, redrawn at the end of every chat command and
on every sweep. Reading it from a screenshot is a handful of pixel averages, so the capture
can poll it many times a second instead of judging the picture.

Cells, top to bottom: 0 white and 1 black (the locator), then bits, least significant first:
  2..9    sequence number of the last command carried out (8 bits; the capture appends it to
          each command it sends)
  10..19  tile index (10 bits)
  20..34  camera target x, in 1/64 cells (15 bits)
  35..49  camera target y, in 1/64 cells (15 bits)
  50      markers shown
  51      sky white     52  sky black
  53      tick (flips every sweep, a quarter second)
  54      parity of every bit above     55  black (end locator)
"""

from dataclasses import dataclass

import numpy as np

CELLS = 56
SEARCH_WIDTH = 160  # the strip is looked for this far from the left edge
SEARCH_HEIGHT_SHARE = 0.9


@dataclass
class Status:
    seq: int
    tile: int
    camera_x: float
    camera_y: float
    markers: bool
    sky_white: bool
    sky_black: bool
    tick: int


class StatusStrip:
    """Finds the strip in a frame once (its size on screen depends on the interface scale),
    then reads it from any frame."""

    def __init__(self) -> None:
        self.origin: tuple[int, int] | None = None  # top-left of cell 0
        self.cell: int = 0  # cell size in pixels

    @property
    def located(self) -> bool:
        return self.origin is not None

    def locate(self, frame: np.ndarray) -> bool:
        """Look for the locator: a white square at the top-left with a black square of the same
        size directly below it, and the whole column of cells fitting on screen. Cells 0 and 47
        must read white and black."""
        h = int(frame.shape[0] * SEARCH_HEIGHT_SHARE)
        region = frame[:h, :SEARCH_WIDTH].astype(np.int16)
        white = (region.min(axis=2) >= 200) & (region.max(axis=2) - region.min(axis=2) <= 30)
        black = region.max(axis=2) <= 40
        ys, xs = np.nonzero(white)
        if not len(ys):
            return False
        # Candidate top-left corners: white pixels whose left and upper neighbours aren't white.
        top_left = white.copy()
        top_left[1:, :] &= ~white[:-1, :]
        top_left[:, 1:] &= ~white[:, :-1]
        for y, x in zip(*np.nonzero(top_left)):
            # The white run to the right and down gives the cell size.
            run_x = int(np.argmin(white[y, x:])) if not white[y, x:].all() else white.shape[1] - x
            run_y = int(np.argmin(white[y:, x])) if not white[y:, x].all() else white.shape[0] - y
            cell = min(run_x, run_y)
            if cell < 8 or cell > 80 or abs(run_x - run_y) > 3:
                continue
            if y + CELLS * cell > frame.shape[0]:
                continue
            inner = slice(cell // 4, cell - cell // 4)
            if not white[y : y + cell, x : x + cell][inner, inner].all():
                continue
            if not black[y + cell : y + 2 * cell, x : x + cell][inner, inner].all():
                continue
            self.origin, self.cell = (int(x), int(y)), int(cell)
            if self.read(frame) is not None:
                return True
            self.origin, self.cell = None, 0
        return False

    def _bit(self, frame: np.ndarray, k: int) -> int | None:
        x, y = self.origin
        c = self.cell
        q = max(1, c // 4)
        patch = frame[y + k * c + q : y + (k + 1) * c - q, x + q : x + c - q].astype(np.float32)
        level = float(patch.mean())
        if level > 170:
            return 1
        if level < 85:
            return 0
        return None  # neither: the strip is not where it was, or mid-redraw

    def read(self, frame: np.ndarray) -> Status | None:
        if not self.located:
            return None
        bits = [self._bit(frame, k) for k in range(CELLS)]
        if any(b is None for b in bits) or bits[0] != 1 or bits[1] != 0 or bits[CELLS - 1] != 0:
            return None
        payload = bits[2 : CELLS - 2]
        if sum(payload) % 2 != bits[CELLS - 2]:
            return None

        def value(start: int, count: int) -> int:
            return sum(bits[start + i] << i for i in range(count))

        return Status(
            seq=value(2, 8),
            tile=value(10, 10),
            camera_x=value(20, 15) / 64.0,
            camera_y=value(35, 15) / 64.0,
            markers=bool(bits[50]),
            sky_white=bool(bits[51]),
            sky_black=bool(bits[52]),
            tick=bits[53],
        )

    def blank(self, frame: np.ndarray, width: int) -> np.ndarray:
        """The frame with the strip's column blacked out, so shots compare and stitch as if it
        weren't there (the stitch also leaves that column out of the page)."""
        out = frame.copy()
        out[:, :width] = 0
        return out
