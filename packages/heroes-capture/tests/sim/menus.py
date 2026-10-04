"""The game's menus over a match, drawn the way they look, at any screen size (sizes relative to
the height, as the game's interface scales): the frame dimmed, and buttons with a light
blue-violet outline over a purple fill. For game_menus.py's tests and the simulated game."""

import numpy as np

OUTLINE = (120, 142, 232)
FILL = (52, 26, 104)


def button(frame: np.ndarray, cx: float, cy: float, width: float, height: float) -> None:
    """A button centred at (cx, cy), its sizes in screen heights."""
    h, w = frame.shape[:2]
    x0, x1 = int(cx * w - width * h / 2), int(cx * w + width * h / 2)
    y0, y1 = int(cy * h - height * h / 2), int(cy * h + height * h / 2)
    edge = max(1, round(0.002 * h))
    frame[y0:y1, x0:x1] = FILL
    frame[y0:y0 + edge, x0:x1] = frame[y1 - edge:y1, x0:x1] = OUTLINE
    frame[y0:y1, x0:x0 + edge] = frame[y0:y1, x1 - edge:x1] = OUTLINE


def game_menu(frame: np.ndarray, kind: str = "esc") -> np.ndarray:
    """`frame` with one of the game's menus over it: "esc" (four buttons in a column), "exit"
    (the Alt+F4 dialog: two side by side), "options" (a column of seven, Accept and Cancel)."""
    out = (frame // 6).astype(np.uint8)
    if kind == "esc":
        for cy in (0.35, 0.43, 0.52, 0.66):
            button(out, 0.5, cy, 0.33, 0.042)
    elif kind == "exit":
        for cx in (0.5 - 0.11 * out.shape[0] / out.shape[1], 0.5 + 0.11 * out.shape[0] / out.shape[1]):
            button(out, cx, 0.62, 0.215, 0.047)
    else:
        for n in range(7):
            button(out, 0.13 * out.shape[0] / out.shape[1] + 0.05, 0.14 + 0.047 * n, 0.25, 0.04)
        button(out, 0.38, 0.92, 0.21, 0.053)
        button(out, 0.54, 0.92, 0.21, 0.053)
    return out


def broken_interface(frame: np.ndarray) -> np.ndarray:
    """`frame` as the game shows it when the map's script failed to compile: every interface panel
    at once under a red tint (a debug menu's column of buttons, a close button)."""
    out = (frame.astype(np.float32) * 0.35 + np.array([200, 25, 40], np.float32) * 0.65).astype(np.uint8)
    for n in range(6):
        button(out, 0.07 * out.shape[0] / out.shape[1] + 0.03, 0.12 + 0.17 * n, 0.2, 0.035)
    button(out, 0.57, 0.53, 0.2, 0.05)
    return out

