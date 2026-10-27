"""Volskaya Foundry: its custom minimap is drawn about 2 cells shorter than the map's scale (with the
general rule, capture/pack.custom_minimap_bounds, the bottom lies on the render and the top runs 2
cells past the top building's wall). Set by looking at the minimap over the map's render."""

from heroes_capture.map.specifics import Specifics


def extend(specifics: Specifics) -> None:
    specifics.minimap_correction = {"top": -2}
