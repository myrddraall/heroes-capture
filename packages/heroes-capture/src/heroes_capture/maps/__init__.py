"""Layer 5: what a single map needs (one module each, its extend adding to the map's Specifics) and
which maps' renders are verified (validated-maps.json: the checklist, VERIFICATION.md)."""

from pathlib import Path

from heroes_capture.common.jsonio import read_json
from heroes_capture.map.specifics import Specifics
from heroes_capture.maps import hanamura_temple, volskaya_foundry

VALIDATED = Path(__file__).with_name("validated-maps.json")
# Map name, as the game names it -> its module.
MAPS = {"Hanamura Temple": hanamura_temple, "Volskaya Foundry": volskaya_foundry}


def known_maps() -> dict[str, dict]:
    """validated-maps.json's maps, verified at any checklist version: name -> {checklist, version,
    note}. Their handling is in place (an arena's or brawl's own), verified or not."""
    return read_json(VALIDATED)["maps"]


def checklist_version() -> int:
    """The checklist's current version (validated-maps.json): bumped when the render changes."""
    return int(read_json(VALIDATED)["checklist"])


def validated_maps() -> dict[str, dict]:
    """The maps verified at the checklist's current version: name -> {checklist, version, note}."""
    data = read_json(VALIDATED)
    return {name: entry for name, entry in data["maps"].items() if entry.get("checklist") == data["checklist"]}


def extend(map_name: str, specifics: Specifics) -> None:
    specifics.validated = map_name in validated_maps()
    if module := MAPS.get(map_name):
        module.extend(specifics)
