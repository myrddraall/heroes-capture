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


# A side switch ("side <team>") is on screen 47-212 ms after the strip acknowledges it, all at once,
# on one of the game's next simulation ticks (the switch probe on Battlefield of Eternity): the
# frames are watched until what has team colours changes, for at most SWITCH_CAP seconds (a view
# with nothing in team colours never changes), then a frame more (SWITCH_SETTLE: a whole frame).
SWITCH_CAP, SWITCH_SETTLE = 0.35, 0.1
# A pixel that differs by more than RECOLOUR_CHANGE (the largest of the three channels) has
# changed; more than RECOLOUR_PIXELS of them in a watched box: the switch is drawn (a structure's
# team colours, or a lamp's, are hundreds of pixels at the capture's scale).
RECOLOUR_CHANGE, RECOLOUR_PIXELS = 24, 25


def recoloured(reference: np.ndarray, frame: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> bool:
    """Whether any of a frame's boxes (circle_box's rows and columns) differs from the reference's
    by more than RECOLOUR_PIXELS pixels: a side switch drawn."""
    return any(int((channel_diff(reference[v0:v1, u0:u1], frame[v0:v1, u0:u1]) > RECOLOUR_CHANGE).sum()) > RECOLOUR_PIXELS
               for v0, v1, u0, u1 in boxes)


def recoloured_frame(session, reference: np.ndarray | None, boxes: list[tuple[int, int, int, int]]) -> tuple[np.ndarray | None, bool]:
    """After a side switch: the frame once `boxes` of it have changed from `reference` (the whole
    frame without any), a frame later; after SWITCH_CAP without a change, the frame as it is. And
    whether it changed."""
    if reference is None:
        settle(SWITCH_CAP)  # nothing to watch against: the longest the switch takes
        return session.grab(dark_ok=True), False
    boxes = [b for b in boxes if b[0] < b[1] and b[2] < b[3]] or [(0, reference.shape[0], 0, reference.shape[1])]
    deadline = time.time() + SWITCH_CAP
    while time.time() < deadline:
        frame = session.grab(dark_ok=True)
        if frame is not None and recoloured(reference, frame, boxes):
            settle(SWITCH_SETTLE)
            return session.grab(dark_ok=True), True
        settle(0.02)
    return session.grab(dark_ok=True), False
