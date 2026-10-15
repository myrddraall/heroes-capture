"""A shot's commands and waits that the tiles and the elements share: the tile command, the shot
over black once the swap to the black sky is drawn, and the frame once a side switch is drawn."""

import time

import numpy as np

from heroes_capture.common.game_control import settle
from heroes_capture.common.imaging import channel_diff


def tile_command(tile: dict) -> str:
    """`tile <n> <x> <y>`: the map script takes the position from the command, so the grid can
    be planned here, from the camera bounds measured in the game (see plan_grid)."""
    return f"tile {tile['index']} {tile['x']:.2f} {tile['y']:.2f}"


def black_settled(session, first: np.ndarray, white: np.ndarray) -> np.ndarray:
    """The shot over black, once the skybox swap has finished rendering: the share of black
    pixels jumps and then holds still between two grabs (no sky in view: the first frame as it
    is)."""

    def black_share(frame: np.ndarray) -> float:
        return float((frame[::4, ::4].max(axis=2) <= 6).mean())

    before, frame, share = black_share(white), first, black_share(first)
    for _ in range(8):
        if share - before <= 0.005:
            return frame  # no sky in view, or the swap not rendered yet
        settle(0.05)
        again = session.grab(dark_ok=True)
        if again is None:
            return frame
        next_share = black_share(again)
        if abs(next_share - share) < 0.002:
            return again
        frame, share = again, next_share
    return frame


def shot_over_black(session, white: np.ndarray | None) -> np.ndarray | None:
    """The view over black ("black"), once the swap is drawn (black_settled), after its shot over
    the light sky (`white`); None when the map doesn't answer or there was no light shot."""
    answer = session.send("black", timeout=3.0)
    if answer is None or white is None:
        return None
    return black_settled(session, session.blank(answer[1]), white)


# A side switch ("side <team>") is on screen 47-212 ms after the strip acknowledges it on
# Battlefield of Eternity's towers (the switch probe), but Dragon Shire's forts take longer, and a
# structure faded in just after a switch may still be redrawing in the old side's colours: the
# frames are watched until what has team colours changes, for at most SWITCH_CAP seconds (a view
# with nothing in team colours never changes), and then until it holds still (still_frame).
SWITCH_CAP, SWITCH_SETTLE = 2.0, 0.1
# Held still: two grabs STILL_GAP apart differing in no watched box (recoloured); at most STILL_CAP.
STILL_GAP, STILL_CAP = 0.12, 2.0
# A pixel that differs by more than RECOLOUR_CHANGE (the largest of the three channels) has
# changed; more than RECOLOUR_PIXELS of them in a watched box: the switch is drawn (a structure's
# team colours, or a lamp's, are hundreds of pixels at the capture's scale).
RECOLOUR_CHANGE, RECOLOUR_PIXELS = 24, 25


def recoloured(reference: np.ndarray, frame: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> bool:
    """Whether any of a frame's boxes (circle_box's rows and columns) differs from the reference's
    by more than RECOLOUR_PIXELS pixels: a side switch drawn."""
    return any(int((channel_diff(reference[v0:v1, u0:u1], frame[v0:v1, u0:u1]) > RECOLOUR_CHANGE).sum()) > RECOLOUR_PIXELS
               for v0, v1, u0, u1 in boxes)


def still_frame(session, boxes: list[tuple[int, int, int, int]], first: np.ndarray | None = None) -> np.ndarray | None:
    """A frame once `boxes` of the view hold still: two grabs STILL_GAP apart that differ in none of
    them (recoloured); after STILL_CAP, the frame as it is. `first`: a grab to start from."""
    previous = first if first is not None else session.grab(dark_ok=True)
    deadline = time.time() + STILL_CAP
    while previous is not None and time.time() < deadline:
        settle(STILL_GAP)
        frame = session.grab(dark_ok=True)
        if frame is None:
            return previous
        shown = [b for b in boxes if b[0] < b[1] and b[2] < b[3]] or [(0, frame.shape[0], 0, frame.shape[1])]
        if not recoloured(previous, frame, shown):
            return frame
        previous = frame
    return previous


def first_change(session, reference: np.ndarray, boxes: list[tuple[int, int, int, int]], cap: float = SWITCH_CAP) -> tuple[np.ndarray | None, float | None]:
    """The first frame whose boxes differ from `reference` (recoloured), and the seconds it took;
    after `cap` seconds without one, the frame as it is and None."""
    boxes = [b for b in boxes if b[0] < b[1] and b[2] < b[3]] or [(0, reference.shape[0], 0, reference.shape[1])]
    started = time.time()
    while time.time() < started + cap:
        frame = session.grab(dark_ok=True)
        if frame is not None and recoloured(reference, frame, boxes):
            return frame, time.time() - started
        settle(0.02)
    return session.grab(dark_ok=True), None


def recoloured_frame(session, reference: np.ndarray | None, boxes: list[tuple[int, int, int, int]],
                     cap: float = SWITCH_CAP) -> tuple[np.ndarray | None, bool]:
    """After a side switch: the frame once `boxes` of it have changed from `reference` (the whole
    frame without any: first_change) and then held still (still_frame); after `cap` seconds
    without a change, the frame as it is. And whether it changed."""
    if reference is None:
        settle(cap)  # nothing to watch against: the longest the switch takes
        return session.grab(dark_ok=True), False
    frame, seconds = first_change(session, reference, boxes, cap)
    if seconds is None:
        return frame, False
    return still_frame(session, boxes, frame), True
