"""Which state the game is in, from a screen grab: not running, at the main menu, loading, in our
map, leaving the match, or showing the "Unable to open map" dialog.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .game_window import game_process_seen

NOT_RUNNING = "not running"
MENU = "menu"
LOADING = "loading"
IN_MAP = "in a map"
LEAVING = "leaving the match"
MAP_FAILED = "map failed to load"

# The map's phases as the status strip reports them (status.py: Status.phase).
PHASES = ("before the gates", "opening timers cut short", "ready", "leaving")

_MENU_REFERENCE = None  # the main menu's fixed interface: templates shipped in menu-reference/


def _load_menu_reference() -> list:
    """Templates of the main menu's fixed parts (the top bar, the chat box, the buttons at the
    bottom right), from a screenshot of the menu, with their positions relative to the screen
    height and anchored to the corners, as the interface scales. Login, loading, in-game and
    post-match screens have none of them."""
    global _MENU_REFERENCE
    if _MENU_REFERENCE is None:
        folder = Path(__file__).with_name("menu-reference")
        _MENU_REFERENCE = []
        try:
            for p in json.loads((folder / "patches.json").read_text())["patches"]:
                p["template"] = np.asarray(Image.open(folder / f"{p['name']}.png").convert("L"), dtype=np.float32)
                _MENU_REFERENCE.append(p)
        except (OSError, KeyError, ValueError) as e:
            print(f"(no menu reference: {e})", flush=True)
    return _MENU_REFERENCE


def menu_matches(frame: np.ndarray) -> int:
    """How many of the menu's fixed parts are on screen where they belong (0 to 3)."""
    grey = frame.astype(np.float32).mean(axis=2)
    h, w = grey.shape
    found = 0
    for p in _load_menu_reference():
        pw, ph = int(round(p["w"] * h)), int(round(p["h"] * h))
        x = int(round(p["dx"] * h)) if p["anchor"].endswith("left") else w - int(round(p["dx"] * h)) - pw
        y = int(round(p["dy"] * h)) if p["anchor"].startswith("top") else h - int(round(p["dy"] * h)) - ph
        if x < 0 or y < 0 or x + pw > w or y + ph > h:
            continue
        region = np.asarray(Image.fromarray(grey[y : y + ph, x : x + pw].astype(np.uint8)).resize(p["template"].shape[::-1], Image.BILINEAR), dtype=np.float32)
        t = p["template"]
        a, b = region - region.mean(), t - t.mean()
        denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
        if denom and float((a * b).sum() / denom) > 0.6:
            found += 1
    return found


def failed_dialog(frame: np.ndarray) -> bool:
    """The game's error dialog ("Unable to open map."): a black band across the middle of the
    screen with the menu's coloured backdrop above and below it (a fade to black is black all
    over)."""
    small = frame[::8, ::8].max(axis=2)
    h = small.shape[0]
    middle = float((small[int(h * 0.30) : int(h * 0.60)] <= 12).mean())
    top = float(small[: int(h * 0.08)].mean())
    bottom = float(small[int(h * 0.92) :].mean())
    return middle > 0.8 and top > 15 and bottom > 15


def game_state(frame: np.ndarray | None, strip=None) -> str:
    """Which of the game's states the screen shows, cheapest test first: the game's process,
    our map's status strip (only our map draws it), the main menu's fixed interface, the error
    dialog; anything else (the loading screen, a transition, an intro) counts as loading."""
    if not game_process_seen():
        return NOT_RUNNING
    if frame is None:
        return LOADING
    if strip is not None and ((strip.located and strip.read(frame) is not None) or strip.locate(frame)):
        status = strip.read(frame)
        return LEAVING if status is not None and status.phase == 3 else IN_MAP
    if menu_matches(frame) >= 2:
        return MENU
    if failed_dialog(frame):
        return MAP_FAILED
    return LOADING
