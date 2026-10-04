"""The game's menus over a match (game_menus.py), at any screen size: the Esc menu, Options and the
exit dialog are told from the map, whose own art can have long blue-violet streaks."""

import numpy as np
import pytest

from heroes_capture.game_menus import game_menu_open
from sim.menus import OUTLINE, button, game_menu

SIZES = [(600, 1067), (720, 1280), (1080, 1920), (1080, 2560), (1440, 3440), (1440, 5120), (2160, 3840)]


def a_map(h: int, w: int) -> np.ndarray:
    """Busy, map-like art: noise in earthy colours with a few blue patches."""
    rng = np.random.default_rng(h + w)
    frame = rng.integers(20, 160, (h, w, 3), dtype=np.uint8)
    frame[h // 3: h // 2, w // 4: w // 3] = (90, 110, 210)
    return frame


@pytest.mark.parametrize("h, w", SIZES)
@pytest.mark.parametrize("kind", ["esc", "exit", "options"])
def test_each_menu_at_each_size(h, w, kind):
    assert game_menu_open(game_menu(a_map(h, w), kind))


@pytest.mark.parametrize("h, w", SIZES)
def test_the_map_alone_is_no_menu(h, w):
    frame = a_map(h, w)
    assert not game_menu_open(frame)
    frame[int(0.30 * h): int(0.30 * h) + 3, : w // 2] = OUTLINE  # a long streak (Battlefield of Eternity's sky)
    frame[int(0.45 * h): int(0.45 * h) + 3, : w // 2] = OUTLINE  # another, too far below to be a button's
    assert not game_menu_open(frame)
    button(frame, 0.5, 0.8, 0.3, 0.045)  # one button-like outline alone
    assert not game_menu_open(frame)
