"""Galaxy scripts (a map's MapScript.galaxy, the game's trigger libraries) read as functions."""

import re

# A Galaxy function: its signature line and its body, braces balanced.
_FUNCTION = re.compile(r"^(?P<sig>(?:[A-Za-z_][\w<>]*\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^)]*\))\s*\{", re.MULTILINE)


def functions(script: str) -> dict[str, str]:
    """Every function of a Galaxy script, by name: its text from the signature to the closing brace."""
    found: dict[str, str] = {}
    for m in _FUNCTION.finditer(script):
        depth, i = 0, m.end() - 1
        while i < len(script):
            c = script[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        found[m.group("name")] = script[m.start() : i + 1]
    return found
