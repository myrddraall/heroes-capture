"""Job files: one or more runs described in YAML, run in order by `heroes-capture job <file>`.

    runs:
      - map: Dragon Shire
        render: elements            # keep | hide | elements: a whole render
      - map: Dragon Shire
        render: elements
        only: [rubble]              # just these parts of it, over the map's last run (its working files)
      - map: Dragon Shire
        calibrate: [standing]       # calibration stages; [] or all: the whole calibration

A render's parts: skies (the map's sky layers), standing, camps and rubble (the battlegrounds'
elements), tiles. A run of some parts builds on the map's kept run of the same render: what it
reshoots is removed from it first, the rest is kept, and the map is stitched from all of it. The
setup each part needs (the map prepared and launched; the structures brought down for rubble and
before the tiles) is done whatever is chosen.

The job's progress goes in <tmp>/job.json as each run finishes, so `--resume` carries on a stopped
job: the runs done are skipped, and the one under way carries on from the shots it had already
taken again."""

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from heroes_capture.common.jsonio import read_json, write_json

RENDERS = ("keep", "hide", "elements")
# A render's parts, and the capture's part each belongs to (capture/tiles.py PARTS).
PARTS = {"skies": "skies", "standing": "elements", "camps": "elements", "rubble": "elements", "tiles": "tiles"}
STATE = "job.json"


class JobError(ValueError):
    """A job file that can't be run as written (the message says where)."""


@dataclass
class Run:
    """One run of a job: a render (whole, or only some of its parts) or a calibration."""
    map: str
    render: str | None = None
    only: list[str] = field(default_factory=list)
    calibrate: list[str] | None = None

    def describe(self) -> str:
        if self.calibrate is not None:
            return f"{self.map}: calibrate " + (", ".join(self.calibrate) or "all")
        return f"{self.map}: {self.render} render" + (f", only {', '.join(self.only)}" if self.only else "")


def load(path: Path) -> list[Run]:
    """A job file's runs, checked (JobError says what is wrong, and which run)."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise JobError(f"{path} isn't valid YAML: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list) or not data["runs"]:
        raise JobError(f"{path}: a job is a list of runs under \"runs:\"")
    runs = []
    for n, entry in enumerate(data["runs"], start=1):
        where = f"{path}, run {n}"
        if not isinstance(entry, dict) or not isinstance(entry.get("map"), str) or not entry["map"].strip():
            raise JobError(f"{where}: each run names its map (map: ...)")
        unknown = set(entry) - {"map", "render", "only", "calibrate"}
        if unknown:
            raise JobError(f"{where}: no setting {', '.join(sorted(unknown))} (map, render, only, calibrate)")
        if ("render" in entry) == ("calibrate" in entry):
            raise JobError(f"{where}: a run is a render (render: keep, hide or elements) or a calibration (calibrate: [stages])")
        if "calibrate" in entry:
            stages = entry["calibrate"]
            stages = [] if stages in (None, "all") else stages
            if isinstance(stages, str):
                stages = [stages]
            if not isinstance(stages, list) or not all(isinstance(s, str) for s in stages) or "only" in entry:
                raise JobError(f"{where}: calibrate takes a list of stages, or all")
            runs.append(Run(entry["map"].strip(), calibrate=stages))
            continue
        if entry["render"] not in RENDERS:
            raise JobError(f"{where}: render is one of {', '.join(RENDERS)}")
        only = entry.get("only") or []
        if isinstance(only, str):
            only = [only]
        if not isinstance(only, list) or set(only) - set(PARTS):
            raise JobError(f"{where}: only takes some of {', '.join(PARTS)}")
        if entry["render"] != "elements" and set(only) & {"standing", "camps", "rubble"}:
            raise JobError(f"{where}: standing, camps and rubble are an elements render's")
        runs.append(Run(entry["map"].strip(), render=entry["render"], only=[p for p in PARTS if p in only]))
    return runs


def digest(path: Path) -> str:
    """A job file's content, for --resume to carry on only the same job."""
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16]


def started(work: Path, path: Path) -> dict:
    """A job's progress, new: nothing done yet (written in `work`)."""
    state = {"file": str(path), "digest": digest(path), "done": [], "cleared": []}
    save(work, state)
    return state


def resumed(work: Path, path: Path) -> dict:
    """The progress of the stopped job to carry on (JobError when there is none, or the file has
    changed since: it would be another job)."""
    try:
        state = read_json(work / STATE)
    except (OSError, ValueError):
        raise JobError(f"no stopped job to resume in {work}") from None
    if state.get("digest") != digest(path):
        raise JobError(f"{path} isn't the job that stopped ({state.get('file')}), or it has changed since: run it without --resume")
    return state


def save(work: Path, state: dict) -> None:
    work.mkdir(parents=True, exist_ok=True)
    write_json(work / STATE, state, indent=1)


def capture_parts(only: list[str]) -> list[str]:
    """The capture's parts a render's chosen parts need (capture/tiles.py PARTS, in its order)."""
    wanted = {PARTS[p] for p in only}
    return [p for p in ("skies", "elements", "tiles") if p in wanted]


def clear(run_folder: Path, only: list[str]) -> dict[str, int]:
    """What a run of these parts reshoots, removed from a kept run's folder (the rest is kept):
    the sky layers' shots and measurement, the tiles and their camera positions, the elements'
    records of the chosen kinds (standing structures, camps, rubble) and their shots. How many of
    each were removed."""
    removed = {}
    if "skies" in only:
        sky = run_folder / "sky"
        removed["skies"] = len(list(sky.glob("*"))) if sky.is_dir() else 0
        shutil.rmtree(sky, ignore_errors=True)
        (run_folder / "sky-layers.json").unlink(missing_ok=True)
    if "tiles" in only:
        tiles = run_folder / "tiles"
        removed["tiles"] = len(list(tiles.glob("tile_*")))
        for shot in tiles.glob("tile_*"):
            shot.unlink()
        (run_folder / "positions.json").unlink(missing_ok=True)
    kinds = {"standing": lambda r: r["kind"] == "structure" and r["state"] == "standing",
             "camps": lambda r: r["kind"] == "camp",
             "rubble": lambda r: r["kind"] == "structure" and r["state"] == "rubble"}
    chosen = [kinds[p] for p in only if p in kinds]
    record_path = run_folder / "elements" / "elements.json"
    if chosen and record_path.exists():
        records = read_json(record_path)
        gone = {key: r for key, r in records.items() if any(test(r) for test in chosen)}
        for key, r in gone.items():
            for shot in (run_folder / "elements").glob(f"{r['shot']}*"):
                if shot.stem in (r["shot"], f"{r['shot']}-black", f"{r['shot']}-chaos", f"{r['shot']}-chaos-black"):
                    shot.unlink()
            del records[key]
        write_json(record_path, records, indent=1)
        for p in ("standing", "camps", "rubble"):
            if p in only:
                removed[p] = sum(kinds[p](r) for r in gone.values())
    return removed
