"""The status strip: how the capture knows a command has been carried out and rendered.

The map script (capture-script.mjs, hrsCap_Status*) draws a dialog in the top-left corner of
the screen: two columns of black-or-white cells, wider than they are tall, redrawn at the end of
every chat command and on every sweep. Reading it from a screenshot is a handful of pixel
averages, so the capture can poll it many times a second instead of judging the picture.

Cells, top to bottom: 0 white and 1 black (the locator), then bits, least significant first:
  2..9    sequence number of the last command carried out (8 bits; the capture appends it to
          each command it sends)
  10..19  game clock since the gates opened, seconds (10 bits)
  20..34  camera target x, in 1/64 cells (15 bits)
  35..49  camera target y, in 1/64 cells (15 bits)
  50      unused (always 0)
  51      sky white     52  sky black
  53      tick (flips every sweep, a quarter second)
  54      ready (the gates have opened and the map's opening events are in place)
  55..70  the map's identity (16 bits; the manifest's status.mapId)
  71..72  the map's phase: 0 before the gates open, 1 opening timers being cut short, 2 ready,
          3 leaving (quit received)
  73..80  units the clearing removed since the map was ready (8 bits, capped)
  81      the map's intro cutscene is playing
  82..85  opening timers cut short so far (4 bits, capped)
  86      parity of every bit above     87  black (end locator)

The cells run down a first column of ROWS cells and continue in a second column directly to its
right, below a fixed black cell at its top (which keeps the locator's white cell distinct).
"""

from dataclasses import dataclass

import numpy as np

CELLS = 88
ROWS = 64
SEARCH_WIDTH = 160  # the strip is looked for this far from the left edge
SEARCH_HEIGHT_SHARE = 0.9


@dataclass
class Status:
    seq: int
    game_seconds: int
    camera_x: float
    camera_y: float
    sky_white: bool
    sky_black: bool
    tick: int
    ready: bool
    map_id: int
    phase: int
    cleared_since_ready: int
    intro: bool
    opening_cuts: int


class StatusStrip:
    """Finds the strip in a frame once (its size on screen depends on the interface scale),
    then reads it from any frame."""

    def __init__(self) -> None:
        self.origin: tuple[int, int] | None = None  # top-left of cell 0
        self.cell: int = 0  # cell height in whole pixels, as seen (for logs)
        self.cell_w: int = 0  # cell width in whole pixels, as seen
        self.pitch_y: float = 0.0  # exact distance between rows (cells needn't be whole pixels)
        self.pitch_x: float = 0.0  # exact distance between the columns

    @property
    def located(self) -> bool:
        return self.origin is not None

    def locate(self, frame: np.ndarray) -> bool:
        """Look for the locator: a white cell at the top-left with a black cell of the same size
        directly below it, and the whole column of cells fitting on screen; the parity-checked
        read must then succeed."""
        h = int(frame.shape[0] * SEARCH_HEIGHT_SHARE)
        region = frame[:h, :SEARCH_WIDTH].astype(np.int16)
        white = (region.min(axis=2) >= 200) & (region.max(axis=2) - region.min(axis=2) <= 30)
        black = region.max(axis=2) <= 40
        if not white.any():
            return False
        # Candidate top-left corners: white pixels whose left and upper neighbours aren't white.
        top_left = white.copy()
        top_left[1:, :] &= ~white[:-1, :]
        top_left[:, 1:] &= ~white[:, :-1]
        for y, x in zip(*np.nonzero(top_left)):
            # The strip is anchored to the screen's top-left corner: a white cell elsewhere
            # (inside the strip, or anything else on screen) is not the locator.
            if x > 12 or y > 12:
                continue
            # The white run to the right and down gives the cell's width and height.
            run_x = int(np.argmin(white[y, x:])) if not white[y, x:].all() else white.shape[1] - x
            run_y = int(np.argmin(white[y:, x])) if not white[y:, x].all() else white.shape[0] - y
            if run_y < 8 or run_y > 60 or run_x < run_y or run_x > 160:
                continue
            if y + ROWS * run_y > frame.shape[0]:
                continue
            qy, qx = run_y // 4, run_x // 4
            if not white[y + qy : y + run_y - qy, x + qx : x + run_x - qx].all():
                continue
            if not black[y + run_y + qy : y + 2 * run_y - qy, x + qx : x + run_x - qx].all():
                continue
            # The cells' size in interface units rarely comes out as whole pixels (13 units at
            # 1.2 px per unit is 15.6 px), and one cell's run is off by up to a pixel; over 64
            # rows that is a whole cell. So the exact pitch is found: of the pitches at which
            # the read checks out, the one at which every cell reads most clearly (a pitch a
            # little off still passes the parity now and then, with cells near the bottom
            # half in their neighbours; it then misreads other frames).
            self.origin, self.cell, self.cell_w = (int(x), int(y)), int(run_y), int(run_x)
            best = None
            for dy in np.arange(-1.0, 1.01, 0.05):
                self.pitch_y = run_y + float(dy)
                for dx in np.arange(-1.5, 1.51, 0.25):
                    self.pitch_x = run_x + float(dx)
                    if self.read(frame) is not None:
                        # Equally clear: the pitch nearest the cell's measured size.
                        score = (round(self._clarity(frame), 1), -abs(float(dy)), -abs(float(dx)))
                        if best is None or score > best[0]:
                            best = (score, self.pitch_x, self.pitch_y)
            if best is not None:
                _, self.pitch_x, self.pitch_y = best
                return True
            self.origin, self.cell, self.cell_w, self.pitch_x, self.pitch_y = None, 0, 0, 0.0, 0.0
        return False

    def _bit(self, frame: np.ndarray, k: int) -> int | None:
        return self._cell(frame, k + 1 if k >= ROWS else k)  # the second column starts with a fixed black cell

    def _level(self, frame: np.ndarray, k: int) -> float:
        """Mean brightness of the middle of physical cell k: down the first column, then the
        second."""
        c, cw = self.cell, self.cell_w
        x = int(round(self.origin[0] + (k // ROWS) * self.pitch_x))
        y = int(round(self.origin[1] + (k % ROWS) * self.pitch_y))
        q, qw = max(1, c // 4), max(1, cw // 4)
        return float(frame[y + q : y + c - q, x + qw : x + cw - qw].mean())

    def _clarity(self, frame: np.ndarray) -> float:
        """How far the least clear cell is from mid-grey: highest at the exact pitch, where no
        cell's middle reaches into a neighbour."""
        return min(abs(self._level(frame, k) - 127.5) for k in range(CELLS + 1))

    def _cell(self, frame: np.ndarray, k: int) -> int | None:
        """Physical cell k: 1 white, 0 black, None neither."""
        level = self._level(frame, k)
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
        if self._cell(frame, ROWS) != 0:  # the fixed black cell at the top of the second column
            return None
        payload = bits[2 : CELLS - 2]
        if sum(payload) % 2 != bits[CELLS - 2]:
            return None

        def value(start: int, count: int) -> int:
            return sum(bits[start + i] << i for i in range(count))

        return Status(
            seq=value(2, 8),
            game_seconds=value(10, 10),
            camera_x=value(20, 15) / 64.0,
            camera_y=value(35, 15) / 64.0,
            sky_white=bool(bits[51]),
            sky_black=bool(bits[52]),
            tick=bits[53],
            ready=bool(bits[54]),
            map_id=value(55, 16),
            phase=value(71, 2),
            cleared_since_ready=value(73, 8),
            intro=bool(bits[81]),
            opening_cuts=value(82, 4),
        )

    def blank(self, frame: np.ndarray, width: int) -> np.ndarray:
        """The frame with the strip's column blacked out, so shots compare and stitch as if it
        weren't there (the stitch also leaves that column out of the page)."""
        out = frame.copy()
        out[:, :width] = 0
        return out
