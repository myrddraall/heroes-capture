"""Which state the game is in, from a screen grab: not running, at the main menu, loading, in our
map, leaving the match, or showing the "Unable to open map" dialog.
"""

from pathlib import Path

import numpy as np
from PIL import Image

from heroes_capture.common.jsonio import read_json
from heroes_capture.common.runlog import warn

NOT_RUNNING = "not running"
MENU = "menu"
LOADING = "loading"
IN_MAP = "in a map"
LEAVING = "leaving the match"
MAP_FAILED = "map failed to load"

# The map's phases as the status strip reports them (status.py: Status.phase).
PHASES = ("before the gates", "opening timers cut short", "ready", "leaving")

_MENU_REFERENCE = None  # the main menu's fixed interface: templates shipped in menu-reference/


# The main menu has two layouts: the usual one, and a narrower one the game switches to on a
# screen narrower than this (seen at 5:4; the usual one at 16:9 and 21:9, so the change is
# somewhere between 5:4 and 16:9).
NARROW_MENU_BELOW = 1.5



def interface_height(width: int, height: int) -> float:
    """The height the game lays its interface out for, in pixels: the screen's, unless the screen
    is narrower than 4:3, when the interface (1600 x 1200 units at 4:3) is scaled to the width
    instead (1680 x 1344 gave cells 1.05 times their unit size, not 1.12). Everything measured
    "relative to the screen's height" means this height."""
    return min(height, width * 1200 / 1600)

def _load_menu_reference(narrow: bool = False) -> list:
    """Templates of the main menu's fixed parts (the top bar, the chat box, the buttons at the
    bottom right), from a screenshot of the menu, with their positions relative to the
    interface's height and anchored to the corners, as the interface scales. Login, loading,
    in-game and post-match screens have none of them. `narrow`: the narrow layout's set
    (patches-narrow.json), cut from a 1680x1344 screen."""
    global _MENU_REFERENCE
    if _MENU_REFERENCE is None:
        folder = Path(__file__).with_name("menu-reference")
        _MENU_REFERENCE = {False: [], True: []}
        for key, file, prefix in ((False, "patches.json", ""), (True, "patches-narrow.json", "narrow-")):
            try:
                for p in read_json(folder / file)["patches"]:
                    p["template"] = np.asarray(Image.open(folder / f"{prefix}{p['name']}.png").convert("L"), dtype=np.float32)
                    _MENU_REFERENCE[key].append(p)
            except (OSError, KeyError, ValueError) as e:
                warn(f"no menu reference ({file}): {e}")
    return _MENU_REFERENCE[narrow]


def menu_matches(frame: np.ndarray, details: list[str] | None = None) -> int:
    """How many of the menu's fixed parts are on screen where they belong (0 to 3). On a screen
    narrower than 4:3 the interface is shorter than the screen (interface_height), and where the
    game puts it vertically isn't known: each part is looked for with the interface at the top,
    centred and at the bottom. `details`, if given, gets each part's best correlation and where."""
    grey = frame.astype(np.float32).mean(axis=2)
    h, w = grey.shape
    u = interface_height(w, h)  # the parts' sizes and offsets are fractions of the interface's height
    offsets = sorted({0, int((h - u) // 2), int(h - u)})  # the interface's top, when it is shorter than the screen
    found = 0
    for p in _load_menu_reference(narrow=w / h < NARROW_MENU_BELOW):
        pw, ph = int(round(p["w"] * u)), int(round(p["h"] * u))
        x = int(round(p["dx"] * u)) if p["anchor"].endswith("left") else w - int(round(p["dx"] * u)) - pw
        best, best_at = 0.0, None
        for top in offsets:
            y = top + (int(round(p["dy"] * u)) if p["anchor"].startswith("top") else int(round(u)) - int(round(p["dy"] * u)) - ph)
            if x < 0 or y < 0 or x + pw > w or y + ph > h:
                continue
            region = np.asarray(Image.fromarray(grey[y : y + ph, x : x + pw].astype(np.uint8)).resize(p["template"].shape[::-1], Image.BILINEAR), dtype=np.float32)
            t = p["template"]
            a, b = region - region.mean(), t - t.mean()
            denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
            score = float((a * b).sum() / denom) if denom else 0.0
            if score > best:
                best, best_at = score, (x, y)
        if best > 0.6:
            found += 1
        if details is not None:
            details.append(f"{p['name']} {best:.2f}" + (f" at {best_at}" if best_at else " (off screen)"))
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
    from heroes_capture.common.game_window import game_process_seen  # Windows only; the rest of this module runs anywhere (tests)

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
