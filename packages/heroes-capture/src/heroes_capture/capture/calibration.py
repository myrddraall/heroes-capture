"""A map's calibration (VERIFICATION.md): the probe that measures in the game what a map's elements
render would otherwise assume, and its review.

1. `map calibrate "<map>"` plays one short match of the map: the capture runs it instead of the
   tiles (`capture <manifest> --calibrate`): the start-up, then each calibrate hook
   (map/specifics.py; the battleground's: categories/battleground/calibrate.py), then
   calibration.json and its pictures in the run's folder, copied to
   <output-dir>/<map id>/calibration.json.
2. The review (`calibration-review`): what the numbers settle is settled, the rest is asked on a
   page; the answers come back as answers.json.
3. `calibration-apply` writes the reviewed calibration into the package
   (maps/calibration/<map id>.json, beside validated-maps.json), and an elements render of the map
   is prepared with it; a map without one at the current VERSION isn't rendered with elements.

Its stages can run on their own (`map calibrate --only <stage>`), each merged into the map's
calibration so far. Each stage has a version (map/specifics.py's calibration_stages): bumped when
what it measures, or how, changes, which puts only that stage out of date. VERSION is the file's
shape."""

from pathlib import Path

from heroes_capture.common.jsonio import read_json, write_json

VERSION = 3  # the file's shape; each stage has its own version (map/specifics.py's calibration_stages)
FILE = "calibration.json"
PICTURES = "calibration"  # the pictures the calibration takes for the review, beside FILE
PREVIOUS = "previous.json"  # the map's calibration so far, in a run of some stages only


REVIEWED = Path(__file__).resolve().parents[1] / "maps" / "calibration"


def reviewed_path(map_slug: str) -> Path:
    """Where a map's reviewed calibration is kept: in the package, beside validated-maps.json."""
    return REVIEWED / f"{map_slug}.json"


def path_for(map_folder: Path) -> Path:
    """Where a map's calibration is kept: beside its packs, in <output-dir>/<map id>."""
    return map_folder / FILE


def current(path: Path, stages: dict[str, int]) -> dict | None:
    """A calibration with every one of `stages` at its current version; None when there is none,
    or one is missing or older."""
    try:
        found = read_json(path)
    except (OSError, ValueError):
        return None
    done = found.get("stages") or {}
    return found if found.get("version") == VERSION and all(done.get(k) == v for k, v in stages.items()) else None


def reviewed(map_slug: str, stages: dict[str, int]) -> Path | None:
    """A map's reviewed calibration with every stage at its current version, or None."""
    path = reviewed_path(map_slug)
    found = current(path, stages)
    return path if found is not None and found.get("reviewed") else None


def chosen_stages(only: list[str], stages: dict[str, int], probes: dict[str, int] | None = None) -> list[str]:
    """The stages a calibration runs: those named (every stage when none are; a probe only when
    named), in their order. ValueError for a name that isn't one."""
    probes = probes or {}
    unknown = [s for s in only if s not in stages and s not in probes]
    if unknown:
        raise ValueError(f"no calibration stage {', '.join(unknown)} (the stages: {', '.join(stages)}; the probes: {', '.join(probes) or 'none'})")
    return [s for s in stages if not only or s in only] + [s for s in probes if s in only]


def merged(previous: dict, found: dict, run: list[str], stages: dict[str, int]) -> dict:
    """A calibration so far with a run's findings: what the run measured replaces what was there,
    the rest is kept, and each stage run is recorded at its version. (A calibration from before the
    stages were versioned, VERSION 2, had the motion and structures stages.)"""
    done = dict(previous.get("stages") or ({"motion": 1, "structures": 1} if previous.get("version") == 2 else {}))
    done.update({s: stages[s] for s in run})
    return {**previous, **found, "version": VERSION, "stages": done}


def tool_version() -> str:
    try:
        from heroes_capture._version import VERSION as tool
        return tool
    except ImportError:
        return "0.0.0-dev"


def run(session, manifest: dict, hooks: list, stages: dict[str, int], only: list[str], base: Path,
        probes: dict[str, int] | None = None) -> Path:
    """The calibration's chosen stages (chosen_stages: each hook's measurements of them) with what
    the start-up found (the camera bounds the game applies), merged into the map's calibration so
    far (PREVIOUS in the run's folder, when the run is of some stages only) and written to the
    run's folder. Returns the file."""
    run_stages = chosen_stages(only, stages, probes)
    previous = read_json(base / PREVIOUS) if (base / PREVIOUS).exists() else {}
    found = {"tool": tool_version(), "map": manifest["map"], "screen": manifest["screen"], "pxPerCell": manifest["pxPerCell"], "mapSize": manifest["mapSize"],
             "cameraBounds": manifest["cameraBounds"]}
    for hook in hooks:
        found.update(hook(session, manifest, base, run_stages, previous))
    path = base / FILE
    write_json(path, merged(previous, found, run_stages, {**stages, **(probes or {})}), indent=1)
    return path
