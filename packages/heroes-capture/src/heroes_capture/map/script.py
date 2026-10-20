"""heroes-capture's map script, assembled from parts: the map's own (script.galaxy here: the status
strip, the command box, the note, leaving the match), then each layer's that takes part (the
capture's, a category's, a single map's), in that order. prepare.py appends the script before the
map's InitMap and calls hrsCap_Init at InitMap's end.

A part is plain Galaxy with two kinds of header line: "// command: <word> <trigger function>" (a
command typed into the command box: its first word runs that trigger) and "// init: <function>"
(run once as the map starts). Its ${name} placeholders are filled from the values the layers give.
A function a later part defines again replaces the earlier one (the earlier part's default: the
quit's hrsCap_Restore and hrsCap_EndMatch, for one). The assembled script declares the commands'
triggers and every function first (a prototype each, as Blizzard's _h libraries do), so a part may
call what a later one defines; then the parts; then the command table (hrsCap_CommandTrigger) and
hrsCap_Init.

Galaxy is single-pass, and one unknown native or syntax error stops the whole map script, which
shows in the game as every interface panel visible at once: check refuses a script that calls a
function before it is declared, or one no part defines. The calls in the parts are ones Blizzard's
own Heroes scripts make, except the lens values (field of view, far and near clip) and
CameraSetBounds, StarCraft II natives the game also accepts. Actor messages are plain strings the
game ignores when it doesn't know them, so they cannot break compilation.
"""

import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from string import Template

from heroes_capture.common import js_json
from heroes_capture.map.galaxy import functions
from heroes_capture.map.status import CELL_UNITS, CELLS, ROWS

PART = Path(__file__).with_name("script.galaxy")
_DIRECTIVE = re.compile(r"^// (command: \w+|init:) (hrsCap_\w+)$", re.M)


def fixed(n: float) -> str:
    """A Galaxy fixed literal: whole numbers as "48.0", others to four places (as JavaScript's
    toFixed rounds: halves away from zero)."""
    if float(n).is_integer():
        return js_json.number(n) + ".0"
    return str(Decimal(n).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def map_values(map_id: int, map_width: int, map_height: int) -> dict:
    """The map's own part's values, and the map's size and its whole region for every part: the
    whole map, not RegionEntireMap() (only the playable area), for the reveal and for pausing every
    animation the way Blizzard's own maps do at game over (Trial Grounds: one actor message to the
    whole-map region, no filter; filter terms broke the map's script)."""
    return {
        "status_rows": str(ROWS), "status_cells": str(CELLS + 1), "status_cell_w": str(CELL_UNITS[0]), "status_cell_h": str(CELL_UNITS[1]),
        "map_id": str(map_id), "map_width": fixed(map_width), "map_height": fixed(map_height),
        "map_region": f"RegionRect(-16.0, -16.0, {fixed(map_width + 16)}, {fixed(map_height + 16)})",
    }


def assemble(parts: list[str], values: dict) -> str:
    """The script from its parts (the map's own first, then each layer's), their placeholders filled
    from `values` (see the module's note)."""
    texts = [Template(part).substitute(values) for part in parts]
    commands, inits = [], []
    for text in texts:
        for kind, function in _DIRECTIVE.findall(text):
            if kind == "init:":
                inits.append(function)
            else:
                commands.append((kind.split()[1], function))
    # A function defined again later replaces the earlier definition.
    last: dict[str, int] = {}
    for k, text in enumerate(texts):
        for name in functions(text):
            last[name] = k
    for k, text in enumerate(texts):
        for name, body in functions(text).items():
            if last[name] != k:
                texts[k] = text = text.replace(body, f"// {name}: redefined by a later part")
    defined = {name: body for text in texts for name, body in functions(text).items()}
    triggers = [function.removesuffix("_Func") for _, function in commands]
    header = ["//" + "-" * 98, "// heroes-capture's map script (map/script.py): the commands' triggers and every function, declared.",
              "//" + "-" * 98, *(f"trigger {t};" for t in triggers), *(body[: body.index("{")].rstrip() + ";" for body in defined.values()),
              "trigger hrsCap_CommandTrigger (string lp_word);"]
    table = "\n".join(f'    if ((lp_word == "{word}")) {{ return {trigger}; }}' for (word, _), trigger in zip(commands, triggers))
    created = "\n".join(f'    {trigger} = TriggerCreate("{function}");' for (_, function), trigger in zip(commands, triggers))
    called = "\n".join(f"    {init}();" for init in inits)
    tail = f"""// The command a first word runs (the parts' "// command:" lines).
trigger hrsCap_CommandTrigger (string lp_word) {{
{table}
    return null;
}}

void hrsCap_Init () {{
{created}
{called}
}}
"""
    script = "\n".join(header) + "\n\n" + "\n".join(texts) + "\n" + tail
    check(script)
    return script


_DECLARED = re.compile(r"^(?:void|bool|int|fixed|string|text|unit|trigger) (hrsCap_\w+) \(", re.M)


def check(script: str) -> None:
    """Galaxy is single-pass: a function must be declared before any call to it, and the game gives
    no error when it isn't, nor for a call to a function nowhere defined; the whole map script
    silently fails and the map runs with no triggers at all (every interface panel visible at
    once). Refuse a script where any hrsCap_ function is called before its first declaration, or
    called and never defined."""
    declared = {}
    for m in _DECLARED.finditer(script):
        declared.setdefault(m.group(1), m.start())
    bodies = functions(script)
    for m in re.finditer(r"\b(hrsCap_[A-Za-z]\w*)\s*\(", script):
        name = m.group(1)
        line = script[: m.start()].count("\n") + 1
        if name not in bodies:
            raise SystemExit(f"capture script: {name} is called at line {line} but no part defines it")
        if m.start() < declared[name]:
            raise SystemExit(f"capture script: {name} is used at line {line} before it is declared")
