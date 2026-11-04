"""What an arena adds to a battleground's (map/specifics.py): an arena is a battleground of rounds (its
script includes LibAREN: Punisher Arena), whose "quit" makes the current round the last
(script.galaxy here), and that may be several arenas in one, each a capture area of its own."""

import re
from pathlib import Path

from heroes_capture.capture.prepare import Preparation
from heroes_capture.categories.battleground import specifics as battleground
from heroes_capture.common.runlog import log
from heroes_capture.map.prepare import includes
from heroes_capture.map.specifics import Specifics

PART = Path(__file__).with_name("script.galaxy")


def matches(map_script: str) -> bool:
    """Whether a map is an arena: its script includes LibAREN."""
    return "LibAREN" in includes(map_script)


def parse_areas(regions_xml: str) -> list[dict]:
    """A map that is several arenas in one (Punisher Arena: one arena per round, stacked on the
    map, the camera bounds moved to the round's arena at run time) marks each arena with a region
    named "..._MapBounds" in its Regions file. Two or more of them: capture areas, each rendered to
    its own image. `<quad value="left,bottom,right,top"/>`."""
    areas = []
    for match in re.finditer(r"<region\b[\s\S]*?</region>", regions_xml):
        name = re.search(r'<name value="([^"]*)"', match.group(0))
        quad = re.search(r'<shape type="rect">[\s\S]*?<quad value="([^"]*)"', match.group(0))
        if not name or not quad or not re.search(r"MapBounds$", name.group(1), re.I):
            continue
        try:
            left, bottom, right, top = (float(v) for v in quad.group(1).split(","))
        except ValueError:
            continue
        if left >= right or bottom >= top:
            continue
        areas.append({"name": re.sub(r"_?MapBounds$", "", name.group(1), flags=re.I),
                      "bounds": {"left": left, "bottom": bottom, "right": right, "top": top}})
    areas.sort(key=lambda a: (a["name"].lower(), a["name"]))
    return areas if len(areas) >= 2 else []


def prepare(prep: Preparation) -> None:
    log('rounds (LibAREN): "quit" ends the match after this round')
    prep.areas = parse_areas(prep.regions)


def extend(specifics: Specifics) -> None:
    battleground.extend(specifics)
    specifics.script_parts.append(PART)
    specifics.prepare.append(prepare)
