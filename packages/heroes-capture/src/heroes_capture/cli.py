"""The heroes-capture command (Typer, with Rich for its output).

    heroes-capture map render "Battlefield of Eternity"         prepare, capture and stitch a battleground
    heroes-capture map render "Cursed Hollow" --structures hide   bare terrain
    heroes-capture map render "Dragon Shire" -o D:\\renders        into D:\\renders\\dragon-shire
    heroes-capture map render --category all                    every map, skipping those rendered
    heroes-capture map list                                     the game's maps, and which are verified
    heroes-capture ui extract                                   the game's interface textures, as pictures with a catalogue
    heroes-capture ui search "gold hexagonal portrait frame"    the interface textures that fit a description
    heroes-capture ui background -o <app>\\public\\backgrounds\\play  the Play screen's background, for a browser to draw
    heroes-capture clean-up                                     remove the working files a failed render left in tmp\\
    heroes-capture --version

`prepare`, `capture` and `stitch` are the render's steps on their own; they keep their own option
parsers for now (`heroes-capture prepare --help`).
"""

import hashlib
import os
import shutil
import sys
import time
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from heroes_capture.common import ui
from heroes_capture.common.game_menus import ScriptBroken
from heroes_capture.common.jsonio import read_json, write_manifest

PROBES = ("probe_monitor", "probe_sky", "probe_sky_reach")  # probes.PROBES, as `map render` takes them
DISTANCE = "214"  # camera distance: far, so tall objects lean little at the seams
KEEP = "0.4"  # share of each screenshot used, centred
TMP = Path("tmp")  # the working files and the diagnostic log, in the current folder
LOGS = Path("logs")  # the plain log: the output as --log prints it, kept
MAPS = Path("maps")  # the default output folder
UI_ASSETS = Path("assets") / "ui"  # the interface textures' default folder
CALIBRATION_WORK = "calibration"  # a calibration's working folder, in the render's
PASS_THROUGH = {"allow_extra_args": True, "ignore_unknown_options": True}
WINDOWS_ONLY = ("mss", "pydirectinput", "dxcam")  # what driving the game needs, and only Windows has

app = typer.Typer(
    help="Captures Heroes of the Storm assets from the game client. Start Heroes from the Battle.net app "
         "first, in Windowed (Fullscreen).",
    no_args_is_help=True, add_completion=False, context_settings={"help_option_names": ["-h", "--help"]},
)
map_app = typer.Typer(help="Battlegrounds: render one, or list them.", no_args_is_help=True)
app.add_typer(map_app, name="map")
ui_app = typer.Typer(help="The game's interface textures: extract them, search them by description.", no_args_is_help=True)
app.add_typer(ui_app, name="ui")


def version() -> str:
    try:
        from ._version import VERSION
        return VERSION
    except ImportError:
        return "0.0.0-dev"


def screen_size(monitor: str | None = None) -> str | None:
    """The size the game draws at on a monitor, in real pixels ("3440x1440"), on Windows: the one
    `monitor` names (monitors.choose), or the primary; on a monitor narrower than 5:4 the 5:4
    picture the game letterboxes there (monitors.game_size). ValueError when none is named so."""
    if sys.platform != "win32":
        return None
    from heroes_capture.common.monitors import choose

    return choose(monitor or "primary").game_size


def specifics_for(map_name: str, category: str):
    """What a map's category and the map itself add to the stages (map/specifics.py): the
    category's part (categories/<category>), then the map's own (maps/)."""
    from heroes_capture import maps
    from heroes_capture.categories.arena import specifics as arena
    from heroes_capture.categories.battleground import specifics as battleground
    from heroes_capture.map.specifics import Specifics

    specifics = Specifics(category)
    {"arena": arena, "battleground": battleground}[category].extend(specifics)
    maps.extend(map_name, specifics)
    return specifics


def specifics_of_map(map_name: str, map_script: str):
    """specifics_for a map being prepared, its category read from its script: an arena (a map of
    rounds) or a battleground (any other)."""
    from heroes_capture.categories.arena import specifics as arena

    return specifics_for(map_name, "arena" if arena.matches(map_script) else "battleground")


def specifics_of(manifest: dict):
    """specifics_for a prepared map, as its manifest records it."""
    return specifics_for(manifest["map"], manifest["category"])


def run_capture(argv: list[str]) -> None:
    """The capture, started again after a lost match (tiles.recover); a crash's traceback goes
    into the run's log too, so a failed run can be diagnosed from the copied results."""
    from heroes_capture.capture import tiles
    from heroes_capture.common.runlog import log

    try:
        tiles.main(argv, specifics_of)
    except tiles.Recoverable as e:
        with ui.suspend():  # the restarted capture draws its own view
            code = tiles.recover(e, argv)
        if code:
            raise SystemExit(code)
    except SystemExit:
        raise
    except Exception:
        import traceback

        log(traceback.format_exc())
        raise


def show_version(value: bool) -> None:
    if value:
        print(version())
        raise typer.Exit()


@app.callback()
def _options(
    _version: Annotated[bool, typer.Option("--version", "-V", help="Print the version and stop.", callback=show_version, is_eager=True)] = False,
    log: Annotated[bool, typer.Option("--log", help="Plain log lines instead of the live view (the default in CI or when the output isn't a terminal).")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Also show the detail messages.")] = False,
) -> None:
    ui.configure(log=log, verbose=verbose)


def log_to(folder: Path) -> None:
    """The output also into logs/heroes-capture.log, as --log prints it (kept, every command's run
    after a dated line), and every message with its details into the run's own diagnostic log,
    <folder>/heroes-capture-<date>-<time>.log (a working file: the run removes its own when it
    finishes, never an earlier one's). A capture restarted after a lost match carries on in its
    run's diagnostic log (capture.recover passes it as HRS_DIAG_LOG), and in the plain log without
    a dated line of its own."""
    carried_on = os.environ.get("HRS_DIAG_LOG")
    ui.set_plain_log(LOGS / "heroes-capture.log", header=not carried_on)
    ui.set_log_file(Path(carried_on) if carried_on else diagnostic_log(folder))


def diagnostic_log(folder: Path) -> Path:
    """A new run's diagnostic log in `folder`: heroes-capture-<date>-<time>.log (-2, -3 ... after
    it when a run that began in the same second has one)."""
    stem = f"heroes-capture-{time.strftime('%Y%m%d-%H%M%S')}"
    path, n = folder / f"{stem}.log", 1
    while path.exists():
        n += 1
        path = folder / f"{stem}-{n}.log"
    return path


def remove(paths: list[Path]) -> None:
    """Files and folders removed (what can't be, such as a file still open, stays)."""
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def cleaned_up(folder: Path, clean: bool) -> None:
    """With --clean-up: every working file heroes-capture left in `folder` removed once done (this
    run's and earlier ones', as heroes-capture clean-up does), and the folder once empty; otherwise
    kept (a later run builds on them), saying so."""
    clean_up(working_files(folder) + ([ui.log_file()] if ui.log_file() else []), keep=not clean)


def clean_up(paths: list[Path], keep: bool) -> None:
    """A finished command's working files removed, and their folder too once empty; with keep (a
    render's and a calibration's, unless --clean-up: a later run builds on them), left. A failed
    command doesn't get here: its files stay (see kept_on_failure), for clean-up to remove later."""
    if keep:
        ui.done(f"Working files kept in {paths[0].parent} (heroes-capture clean-up removes them)")
        return
    if ui.log_file() in paths:
        ui.close_log_file()
    remove(paths)
    try:
        paths[0].parent.rmdir()
    except OSError:
        pass  # other files in it


@contextmanager
def kept_on_failure(folder: Path):
    """A command whose working files stay in `folder` if it fails, saying so."""
    try:
        yield
    except BaseException:
        ui.warn(f"the working files are left in {folder} for diagnosis; heroes-capture clean-up removes them")
        raise


def working_files(folder: Path) -> list[Path]:
    """What heroes-capture wrote in a working folder: each prepared map's manifest (<id>.json, a
    manifest by its id and tiles), its prepared map (<id>.stormmap) and its folder of screenshots
    and logs (<id>), any prepared map left without a manifest (a preparation that failed), and the
    runs' diagnostic logs. Anything else in the folder isn't the tool's."""
    if not folder.is_dir():
        return []
    found = []
    for manifest in sorted(folder.glob("*.json")):
        try:
            data = read_json(manifest)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id") == manifest.stem and "tiles" in data:
            found += [manifest, *(p for p in (manifest.with_suffix(".stormmap"), manifest.with_suffix("")) if p.exists())]
    found += [p for p in sorted(folder.glob("*.stormmap")) if p not in found]
    found += sorted(folder.glob("heroes-capture*.log"))
    found += [folder / "job.json"] if (folder / "job.json").exists() else []  # a job's progress (job.py)
    # A calibration's (calibrate_one): the whole folder when it holds only the tool's files.
    calibrations = working_files(folder / CALIBRATION_WORK) if folder.name != CALIBRATION_WORK else []
    if calibrations and set(calibrations) == set((folder / CALIBRATION_WORK).iterdir()):
        found.append(folder / CALIBRATION_WORK)
    else:
        found += calibrations
    return found


# ------------------------------------------------------------------------------------------------
# map render, map list
# ------------------------------------------------------------------------------------------------


class Structures(str, Enum):
    keep = "keep"
    hide = "hide"
    elements = "elements"


class Category(str, Enum):
    battleground = "battleground"
    arena = "arena"
    brawl = "brawl"
    other = "other"
    all = "all"


SAVERS = 2  # the capture's screenshot-saving threads: a run stopped mid-save may leave this many half-written


def split_map(words: list[str]) -> tuple[str | None, list[str]]:
    """The map and the preparing step's options, from the words after `map render` that aren't its
    own options: the map is the first word no option takes as a value. (The map isn't a declared
    argument: click would take the first bare word for it, which in `--fov 12 "Dragon Shire"` is
    the 12.)"""
    from heroes_capture.capture.prepare import OPTION_VALUES

    found, options, i = None, [], 0
    while i < len(words):
        word = words[i]
        if word.startswith("-"):
            if word not in OPTION_VALUES:
                raise typer.BadParameter(f"no such option: {word} (heroes-capture prepare --help lists the preparing step's)")
            options += words[i:i + 1 + OPTION_VALUES[word]]
            i += 1 + OPTION_VALUES[word]
        elif found is None:
            found, i = word, i + 1
        else:
            raise typer.BadParameter(f'"{word}": one map at a time (or several with --category)')
    return found, options


# Arenas and brawls each need handling of their own (Punisher Arena's three arenas, its in-game
# hero selection, its rounds), so in these categories only the known maps are supported: those in
# validated-maps.json, verified at the checklist's current version or an earlier one.
SUPPORTED_ONCE_KNOWN = {"Arena", "Brawl"}


def supported(name: str, category: str, known: dict) -> bool:
    """Whether the capture handles this map of the game's: a known one, or one outside the
    categories whose maps need handling of their own."""
    return name in known or category not in SUPPORTED_ONCE_KNOWN


def maps_to_render(map_spec: str | None, category: Category | None, known: dict) -> tuple[list[tuple[str, str]], int]:
    """(what to prepare, the map's name) for each map to render, and how many unsupported maps the
    category left out: the one named (the game's spelling of its name; a .stormmap path's file
    name; an unsupported arena or brawl renders when named, with a warning), or the category's
    supported maps (all of them for "all"), in map list's order."""
    from heroes_capture.common import game_data

    if map_spec and map_spec.lower().endswith(".stormmap"):
        return [(map_spec, Path(map_spec).name[: -len(".stormmap")])], 0
    with game_data.game_storage() as storage:
        index = game_data.map_index(storage)
        if map_spec:
            name = game_data.find_map(storage, map_spec)
            if not supported(name, index[name]["category"], known):
                ui.warn(f"{name} is unsupported: {index[name]['category'].lower()} maps each need handling of their own, "
                        "and this one hasn't been handled; rendering it anyway")
            return [(name, name)], 0
    order = {c: n for n, c in enumerate(game_data.CATEGORIES)}
    chosen = [n for n, e in index.items() if category == Category.all or e["category"].lower() == category.value]
    names = sorted((n for n in chosen if supported(n, index[n]["category"], known)),
                   key=lambda n: (order[index[n]["category"]], n.casefold()))
    return [(n, n) for n in names], len(chosen) - len(names)


def preparing_options(structures: str, extra: list[str], monitor: str | None) -> list[str]:
    """The preparing step's options for a render: the structures, the options given (`extra`),
    and the defaults for the rest (the working folder, the game's resolution on the monitor, the
    camera's distance, the share of each screenshot kept)."""
    options = ["--structures", structures, *extra]
    if "--out" not in extra:
        options += ["--out", str(TMP)]
    if "--screen" not in extra:
        try:
            screen = screen_size(monitor)
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e
        if screen:
            options += ["--screen", screen]
    if not {"--distance", "--fov"} & set(extra):
        options += ["--distance", DISTANCE]
    if "--keep" not in extra:
        options += ["--keep", KEEP]
    return options


def without(options: list[str], *names: str) -> list[str]:
    """The preparing options less the named ones (each with its value)."""
    kept, i = [], 0
    while i < len(options):
        if options[i] in names:
            i += 2
        else:
            kept.append(options[i])
            i += 1
    return kept


def tool_fingerprint() -> str:
    """What the tool is, for a kept run to be carried on only by the same tool (its shots taken
    the same way): a hash of its own files (the code, the map scripts, the data, the maps' reviewed
    calibrations); the built exe's version."""
    if getattr(sys, "frozen", False):
        from heroes_capture.capture.calibration import tool_version

        return f"exe {tool_version()}"
    root = Path(__file__).resolve().parent
    digest = hashlib.sha1()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def same_preparation(kept: list[str] | None, options: list[str]) -> bool:
    """Whether a kept run was prepared with these options, wherever the tool's folder is now: the
    reviewed calibration's path left out (the folder may have moved; its contents count through
    tool_fingerprint, and a run of some parts takes the calibration as it is now)."""
    return kept is not None and without(kept, "--calibration") == without(options, "--calibration")


def kept_run(work: Path, map_id: str, options: list[str]) -> Path | None:
    """The manifest of a run left unfinished in `work` (failed or stopped) that a render with these
    preparing options can carry on: the same map, prepared the same way, by the same tool
    (tool_fingerprint: a changed capture would mix two ways of shooting). A finished run isn't
    carried on: a render shoots afresh."""
    manifest = work / f"{map_id}.json"
    try:
        data = read_json(manifest)
    except (OSError, ValueError):
        return None
    if not same_preparation(data.get("renderOptions"), options) or not manifest.with_suffix(".stormmap").exists():
        return None
    if data.get("finished"):
        return None
    if data.get("stormmap") and not Path(data["stormmap"]).exists():
        ui.warn(f"the run left in {work} was prepared in another folder (the tool's folder moved?): starting over")
        return None
    if data.get("tool") != tool_fingerprint():
        ui.warn(f"the run left in {work} was taken by another version of the tool: starting over")
        return None
    return manifest.resolve()  # as a fresh preparation's


def first_missing_tile(manifest_path: Path, manifest: dict) -> int:
    """Where a kept run's capture carries on: at its first tile without a screenshot, less the
    ones that may have been half-written when it stopped. len(tiles) when all are there."""
    from heroes_capture.common.frames import frame_exists, tile_stem

    tiles = manifest_path.with_suffix("") / "tiles"
    for position, tile in enumerate(manifest["tiles"]):
        if not frame_exists(tile_stem(tiles, tile["index"])):
            return max(0, position - SAVERS)
    return len(manifest["tiles"])


def calibrate_one(map_spec: str, name: str, options: list[str], output_dir: Path, clean: bool, capture_options: list[str],
                  only: list[str] = ()) -> Path:
    """A map's calibration (capture/calibration.py): prepared for it (in its own working folder, so
    a kept render isn't touched), run in the game, and kept beside the map's packs with its
    pictures, for the review. `only`: just those stages, merged into the map's calibration so far
    (which they need). Returns the file."""
    from heroes_capture.capture import calibration, prepare
    from heroes_capture.common import game_data

    target = calibration.path_for(output_dir / game_data.slug(name))
    if only and not target.exists():
        raise typer.BadParameter(f"{name} has no calibration yet to add stages to: calibrate it whole first")
    work = Path(options[options.index("--out") + 1]) / CALIBRATION_WORK
    options = ["--structures", "elements", *without(options, "--structures", "--out", "--calibration"), "--out", str(work), "--calibrate"]
    ui.plan("Preparing the map for its calibration")
    ui.plan("Calibrating in the game" + (f" ({', '.join(only)})" if only else ""))
    with ui.step("Preparing the map for its calibration"):
        manifest_path = prepare.main([map_spec, *options], specifics_of_map)
    run_folder = manifest_path.with_suffix("")
    shutil.rmtree(run_folder, ignore_errors=True)  # a kept earlier calibration's pictures would mix in
    run_folder.mkdir(parents=True, exist_ok=True)
    if only:  # the calibration so far, for the stages run to be merged into
        shutil.copyfile(target, run_folder / calibration.PREVIOUS)
    with ui.step("Calibrating in the game" + (f" ({', '.join(only)})" if only else "")):
        run_capture([str(manifest_path), "--calibrate", ",".join(only), *capture_options])
    # The measurements and their pictures (for the review) beside the map's packs; a run of some
    # stages adds its pictures to the others'.
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(run_folder / calibration.FILE, target)
    pictures = target.parent / calibration.PICTURES
    if not only:
        shutil.rmtree(pictures, ignore_errors=True)
    if (run_folder / calibration.PICTURES).is_dir():
        shutil.copytree(run_folder / calibration.PICTURES, pictures, dirs_exist_ok=True)
    if clean:
        remove([manifest_path, manifest_path.with_suffix(".stormmap"), manifest_path.with_suffix("")])
        try:
            work.rmdir()
        except OSError:
            pass  # other files in it
    ui.done(f"calibration saved in {target}")
    return target


def with_reviewed_calibration(options: list[str], name: str) -> list[str]:
    """An elements render's preparing options with the map's reviewed calibration (it stops
    without one); any other render's as they are."""
    from heroes_capture.capture import calibration
    from heroes_capture.common import game_data

    if options[options.index("--structures") + 1] != "elements":
        return options
    from heroes_capture.categories.battleground.specifics import CALIBRATION_STAGES  # the arena's are the battleground's

    calibrated = calibration.reviewed(game_data.slug(name), CALIBRATION_STAGES)
    if calibrated is None:
        raise SystemExit(f"{name} has no reviewed calibration from this version of the tool: calibrate it first "
                         f"(heroes-capture map calibrate \"{name}\"), then review it (VERIFICATION.md)")
    return [*options, "--calibration", str(calibrated)]


def render_one(map_spec: str, name: str, options: list[str], output_dir: Path, clean: bool, force: bool,
               capture_options: list[str], validated: dict, carry_on: bool = False) -> bool:
    """One map: skipped when its pack is in the output folder already (unless force), carried on
    from an unfinished run of it (kept_run), or prepared, captured and stitched (the stitch writes
    its pack); its working files kept after, marked finished (a later run builds on them), or with
    clean removed. An elements render is prepared with the map's reviewed calibration, and stops
    without one. carry_on: an unfinished run is carried on even when forced (a job resumed). False
    when skipped."""
    from heroes_capture.common import game_data
    from heroes_capture.capture import calibration
    from heroes_capture.capture import prepare
    from heroes_capture.capture import pack
    from heroes_capture.capture import stitch

    structures = options[options.index("--structures") + 1]
    work = Path(options[options.index("--out") + 1])
    map_id = prepare.render_id(name, structures)
    out = output_dir / game_data.slug(name)
    if not force and pack.is_written(out, structures):
        ui.done(f"{name}: already rendered in {out} (--force renders it again)")
        return False
    options = with_reviewed_calibration(options, name)
    note = "" if name in validated else " (this map isn't verified yet)"
    manifest_path = kept_run(work, map_id, options) if carry_on or not force else None
    if manifest_path:
        planned = read_json(manifest_path)
        plan_render(structures, "Capturing in the game", None, planned, prepared=True)
        start = first_missing_tile(manifest_path, planned)
        ui.done(f"carrying on from the run left in {work} ({start} of {len(planned['tiles'])} tiles kept){note}")
    else:
        plan_render(structures, "Capturing in the game", None)
        with ui.step("Preparing the map"):
            manifest_path = prepare.main([map_spec, *options], specifics_of_map)
        planned = read_json(manifest_path)
        plan_render(structures, "Capturing in the game", None, planned)
        planned["renderOptions"] = options  # what a later render must match to carry this one on
        planned["tool"] = tool_fingerprint()  # and the tool that must be carrying it on
        write_manifest(manifest_path, planned)
        start = 0
        ui.done(f"{len(planned['tiles'])} tiles planned{note}")
    manifest = str(manifest_path)
    if start < len(planned["tiles"]):
        if not start:
            for folder in ("tiles", "elements"):  # old screenshots would mix in (the elements' record skips what it lists)
                shutil.rmtree(manifest_path.with_suffix("") / folder, ignore_errors=True)
        with ui.step("Capturing in the game"):
            run_capture([manifest, *(["--start", str(start)] if start else []), *capture_options])
    with ui.step("Stitching and packing"):
        stitch.main([manifest, "--output-dir", str(output_dir)], specifics_of)
    if clean:
        remove([manifest_path, manifest_path.with_suffix(".stormmap"), manifest_path.with_suffix("")])
    else:
        write_manifest(manifest_path, {**read_json(manifest_path), "finished": True})
    ui.done(f"the map is in {stitch.output_folder(output_dir, planned)}; heroes-capture map view \"{name}\" shows it")
    return True


# The stitch's stages, in the order they run (capture/stitch.py, the battleground's elements,
# capture/pack.py); one a map doesn't need is left out as its step closes.
STITCH_STAGES = ("Matching neighbours", "Placing the screenshots", "Routing the seams", "Composing", "Transparency outside the map",
                 "Team colour", "The black edge", "Writing the map image", "Placing the elements", "Packing")


def plan_render(structures: str, capturing: str, only: list[str] | None, manifest: dict | None = None, prepared: bool = False) -> None:
    """A render's whole tree, pending, as its run starts (the live view shows all of it): its
    steps, the capture's stages and the elements' passes (capture/tiles.py, element_capture.py:
    the titles they open), the stitch's stages; with their counts once the map is prepared
    (`manifest`; `prepared`: a kept run's, so no preparing step). `only`: a run of some parts
    (job.PARTS), None the whole render."""
    parts = set(only) if only else {"skies", "standing", "camps", "rubble", "tiles"}
    if not prepared:
        ui.plan("Preparing the map")
    capture = ui.plan(capturing)
    if capture is None:
        return
    capture.plan("Launching the map")
    starting = capture.plan("Starting up")
    starting.plan("Status strip")
    starting.plan("Map ready")
    found = (manifest or {}).get("elements") or {}
    if structures == "elements" and parts & {"standing", "camps", "rubble"}:
        elements = capture.plan("Elements")
        count = len(found["structures"]) if found.get("structures") else None
        if "rubble" in parts:  # the copies brought down before the standing shots
            elements.plan("Rubble preparation")
        if "standing" in parts:
            for title in ("Standing (blue)", "Neighbours"):
                elements.plan(title, count)
            elements.plan("Standing (red)")
        if "camps" in parts and (manifest is None or found.get("camps")):
            elements.plan("Camps", len(found["camps"]) if found.get("camps") else None)
        if "rubble" in parts:
            elements.plan("Rubble", count)
        elements.plan("Clearing")
    if "tiles" in parts:
        capture.plan("Tiles (blue)", len(manifest["tiles"]) if manifest else None)
        capture.plan("Tiles past the edge")
        capture.plan("Tiles (red)")
    capture.plan("Leaving the match")
    stitching = ui.keep_count(ui.plan("Stitching and packing"))
    for title in STITCH_STAGES:
        stitching.plan(title)


def render_some(map_spec: str, name: str, options: list[str], output_dir: Path, only: list[str], capture_options: list[str],
                cleared: bool, mark_cleared) -> None:
    """Only some parts of a render (job.PARTS), over the map's kept run of the same render: what
    they reshoot removed from it (job.clear; not again when `cleared`: a resumed job's run keeps
    what it had shot), the map prepared again (the tool as it is now) and launched, only the
    capture's parts they need run (the elements capture shooting only what its record lacks, and
    bringing the structures down for the tiles), the tiles kept from before checked against the
    grid as planned now, then the map stitched from all of it."""
    from heroes_capture import job
    from heroes_capture.capture import prepare, stitch

    structures = options[options.index("--structures") + 1]
    options = with_reviewed_calibration(options, name)
    work = Path(options[options.index("--out") + 1])
    manifest_path = work / f"{prepare.render_id(name, structures)}.json"
    run_folder = manifest_path.with_suffix("")
    try:
        kept = read_json(manifest_path)
    except (OSError, ValueError):
        kept = None
    if kept is None or not run_folder.is_dir():
        raise SystemExit(f"{name} has no kept {structures} render in {work} to build on: render it whole first")
    if not same_preparation(kept.get("renderOptions"), options):
        raise SystemExit(f"{name}'s kept render in {work} was prepared with other options: render it whole")
    if not cleared:
        removed = job.clear(run_folder, only)
        mark_cleared()
        ui.done(f"reshooting {', '.join(f'{p} ({n})' if p in removed else p for p, n in ((p, removed.get(p)) for p in only))}; the rest kept")
    capturing = f"Capturing in the game ({', '.join(only)})"
    plan_render(structures, capturing, only)
    with ui.step("Preparing the map"):
        manifest_path = prepare.main([map_spec, *options], specifics_of_map)
    planned = read_json(manifest_path)
    plan_render(structures, capturing, only, planned)
    write_manifest(manifest_path, {**planned, "renderOptions": options, "tool": tool_fingerprint()})
    with ui.step(capturing):
        run_capture([str(manifest_path), "--parts", ",".join(job.capture_parts(only)), "--reuse", *capture_options])
    if "tiles" not in only:
        now = [(t["index"], t.get("x"), t.get("y")) for t in read_json(manifest_path)["tiles"]]
        if now != [(t["index"], t.get("x"), t.get("y")) for t in kept["tiles"]]:
            raise SystemExit(f"{name}'s grid came out differently this time: the kept tiles don't fit it; run tiles too")
    with ui.step("Stitching and packing"):
        stitch.main([str(manifest_path), "--output-dir", str(output_dir)], specifics_of)
    write_manifest(manifest_path, {**read_json(manifest_path), "finished": True})
    ui.done(f"the map is in {stitch.output_folder(output_dir, planned)}")


@app.command("job")
def run_job(
    path: Annotated[Path, typer.Argument(help="The job file (YAML): its runs, in order (heroes_capture/job.py describes it).")],
    resume: Annotated[bool, typer.Option("--resume", help="Carry on the job that stopped: the runs it finished skipped, the one under way carried on from what it had shot.")] = False,
    clean: Annotated[bool, typer.Option("--clean-up", help="Remove the working files from tmp\\ once the job is done; for a job known to work.")] = False,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Where the maps' folders go.")] = MAPS,
    game: Annotated[Optional[str], typer.Option(hidden=True)] = None,
    monitor: Annotated[Optional[str], typer.Option("--monitor", help="Put the game on this monitor first, and render at its resolution.", show_default=False)] = None,
) -> None:
    """Run a job file: renders (whole, or only some of their parts over the map's last run) and calibrations, in order.

    A run of some parts (only: skies, standing, camps, rubble, tiles) builds on the map's kept run of the same render: what it reshoots is removed, the rest kept, and the map stitched from all of it. The job's progress is kept in tmp\\job.json, for --resume.
    """
    from heroes_capture import job

    try:
        runs = job.load(path)
    except (OSError, job.JobError) as e:
        raise typer.BadParameter(str(e)) from None
    log_to(TMP)
    try:
        state = job.resumed(TMP, path) if resume else job.started(TMP, path)
    except job.JobError as e:
        raise typer.BadParameter(str(e)) from None
    validated, known = validated_maps(), known_maps()
    for run in runs:  # every map named is known before anything runs
        maps_to_render(run.map, None, known)
    capture_options = [*(["--game", game] if game else []), *(["--monitor", monitor] if monitor else [])]
    with ui.folder(f"Job {path.name}", total=len(runs), done=len(state["done"])) as whole:
        for n, run in enumerate(runs):
            heading = f"Run {n + 1} of {len(runs)}: {run.describe()}"
            if n in state["done"]:
                ui.done(f"{heading}: done before")
                continue
            carrying_on = resume and state.get("current") == n
            state["current"] = n
            job.save(TMP, state)
            with ui.folder(heading + (" (carried on)" if carrying_on else ""), section=True):
                run_one_of_job(run, n, state, carrying_on, output_dir, monitor, capture_options, validated, known)
            state["done"].append(n)
            job.save(TMP, state)
            whole.advance()
    ui.done(f"job done: {len(runs)} run{'s' if len(runs) != 1 else ''}")
    cleaned_up(TMP, clean)


def run_one_of_job(run, n: int, state: dict, carrying_on: bool, output_dir: Path, monitor: str | None, capture_options: list[str],
                   validated: dict, known: dict) -> None:
    """One run of a job (job.Run): a calibration, a render of some parts, or a whole render."""
    from heroes_capture import job

    (map_spec, name), = maps_to_render(run.map, None, known)[0]
    with kept_on_failure(TMP):
        if run.calibrate is not None:
            calibrate_one(map_spec, name, preparing_options("elements", [], monitor), output_dir, False, capture_options, run.calibrate)
        elif run.only:
            def mark_cleared(n=n) -> None:
                state["cleared"].append(n)
                job.save(TMP, state)

            render_some(map_spec, name, preparing_options(run.render, [], monitor), output_dir, run.only, capture_options,
                        carrying_on and n in state["cleared"], mark_cleared)
        else:
            render_one(map_spec, name, preparing_options(run.render, [], monitor), output_dir, False, True, capture_options,
                       validated, carry_on=carrying_on)


@map_app.command("render", context_settings=PASS_THROUGH, options_metavar="[OPTIONS] [MAP]")
def render(
    ctx: typer.Context,
    category: Annotated[Optional[Category], typer.Option("--category", "-c", help="Render every map of a category instead of one map; all: every map. Unsupported maps are always left out.", show_default=False)] = None,
    structures: Annotated[Structures, typer.Option(help="Keep or hide forts, towers, cores and gates; or elements: each structure and camp on its own, over the bare terrain (in development, ELEMENTS-PLAN.md).")] = Structures.keep,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Where the maps' folders go: <output-dir>/<map id>, e.g. maps/dragon-shire.")] = MAPS,
    force: Annotated[bool, typer.Option("--force", help="Render maps already rendered in the output folder again, from the start.")] = False,
    clean: Annotated[bool, typer.Option("--clean-up", help="Remove the working files (screenshots, the prepared map, diagnostic logs) from tmp\\ once done; for a render known to work. Kept otherwise: a later run builds on them, and heroes-capture clean-up removes them.")] = False,
    game: Annotated[Optional[str], typer.Option(hidden=True)] = None,  # the install, when it isn't found by itself
    monitor: Annotated[Optional[str], typer.Option("--monitor", help="Put the game on this monitor before launching the map, and render at its resolution: its number as Windows lists them (the run's log lists the monitors), its device name, or primary.", show_default=False)] = None,
    probe_monitor: Annotated[bool, typer.Option(hidden=True)] = False,  # diagnostics instead of the tiles: the start-up on a monitor, with --monitor, and a report
    probe_sky: Annotated[bool, typer.Option(hidden=True)] = False,
    probe_sky_reach: Annotated[bool, typer.Option(hidden=True)] = False,
    show_ui: Annotated[bool, typer.Option(hidden=True)] = False,  # diagnostic: launch with the HUD up and stop
) -> None:
    """Prepare, capture and stitch a map, or every map of a category.

    MAP: the map as the game names it (case and punctuation don't matter), or a path to a .stormmap; or --category instead.

    The images go to <output-dir>/<map id> (maps\\<map id> in the current folder). A map already rendered there is skipped (--force renders it again), and a map whose render was left unfinished (failed or stopped) carries on from its working files. A map that fails doesn't stop a category's run. Maps that haven't been verified render too; they may not come out right.

    The working files and the diagnostic log go to tmp\\ and stay (a render that stops carries on from them; a finished one is shot afresh next time), unless --clean-up removes them once done. The output, as --log prints it, goes to logs\\heroes-capture.log and stays. Options that aren't listed here go to the preparing step (heroes-capture prepare --help).
    """
    import traceback

    from heroes_capture.capture import prepare

    map_spec, extra = split_map(list(ctx.args))
    if (map_spec is None) == (category is None):
        raise typer.BadParameter("name one map, or pick maps with --category")
    chosen = {"probe_monitor": probe_monitor, "probe_sky": probe_sky, "probe_sky_reach": probe_sky_reach}
    probe = next((f"--{name.replace('_', '-')}" for name in PROBES if chosen[name]), None)
    if category and (probe or show_ui):
        raise typer.BadParameter("the diagnostics take one map")
    validated, known = validated_maps(), known_maps()
    maps, left_out = maps_to_render(map_spec, category, known)  # a name that isn't a renderable map stops here, before any file is written
    work = Path(extra[extra.index("--out") + 1]) if "--out" in extra else TMP
    options = preparing_options(structures.value, extra, monitor)
    capture_options = [*(["--game", game] if game else []), *(["--monitor", monitor] if monitor else [])]
    log_to(work)

    if probe or show_ui:  # diagnostics: prepared afresh; their files stay in tmp\
        with kept_on_failure(work):
            if show_ui:
                options.append("--show-ui")
            with ui.step(f"Preparing {maps[0][1]}"):
                manifest = str(prepare.main([maps[0][0], *options], specifics_of_map))
            with ui.step(f"Probe {probe}" if probe else "Launching the map with the HUD up (diagnostic; stops there)"):
                run_capture([manifest, probe or "--launch-only", *capture_options])
        return

    if category:
        ui.done(f"{len(maps)} maps to render ({category.value}), {sum(n in validated for _, n in maps)} of them verified"
                + (f"; {left_out} unsupported left out" if left_out else ""))
    rendered, skipped, failed = 0, 0, []
    for map_spec, name in maps:
        try:
            with ui.folder(f"{name}: {structures.value} render", section=True):
                rendered_now = render_one(map_spec, name, options, output_dir, clean, force, capture_options, validated)
            if rendered_now:
                rendered += 1
            else:
                skipped += 1
        except KeyboardInterrupt:
            ui.warn(f"stopped during {name}; its working files are left in {work}: the same command carries on")
            raise
        except ScriptBroken:
            raise  # every map gets the same script: the run stops
        except (Exception, SystemExit) as e:
            if not category:
                ui.warn(f"the working files are left in {work} for diagnosis (the same command carries on; heroes-capture clean-up removes them)")
                raise
            ui.detail(traceback.format_exc())
            ui.warn(f"{name} failed ({e}); its working files are left in {work}")
            failed.append(name)
    if category:
        ui.done(f"{rendered} rendered, {skipped} already rendered, {len(failed)} failed")
    if failed:
        ui.warn(f"failed: {', '.join(failed)}. The same command carries them on; their working files and the diagnostic log are in {work} (heroes-capture clean-up removes them)")
        raise SystemExit(1)
    cleaned_up(work, clean)


@map_app.command("survey")
def survey(
    map_name: Annotated[str, typer.Argument(metavar="MAP", help="The map as the game names it (case and punctuation don't matter).")],
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Where the maps' folders go: the survey goes in <output-dir>/<map id>/survey.json.")] = MAPS,
) -> None:
    """Step 1 of a new map: what its files and the game's data say about it, and so what its calibration will measure and what the data answers instead (no game needed: the data comes from the install, or Blizzard's servers).

    Its odd cases come out as questions for the calibration's review.
    """
    import tempfile

    from heroes_capture.capture import prepare
    from heroes_capture.categories.battleground import survey as surveying
    from heroes_capture.common import game_data
    from heroes_capture.common.jsonio import write_json

    (map_spec, name), = maps_to_render(map_name, None, known_maps())[0]
    log_to(TMP)
    with tempfile.TemporaryDirectory() as work:
        with ui.folder(f"{name}: survey", section=True), ui.step("Reading the map and the game's data"):
            manifest_path = prepare.main([map_spec, "--structures", "elements", "--calibrate", "--out", work], specifics_of_map)
        found = surveying.survey(read_json(manifest_path))
    target = output_dir / game_data.slug(name) / "survey.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, found, indent=1)
    for line in surveying.lines(found):
        ui.show(line)
    ui.done(f"survey saved in {target}")


@map_app.command("calibrate", context_settings=PASS_THROUGH, options_metavar="[OPTIONS] MAP")
def calibrate(
    ctx: typer.Context,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Where the maps' folders go: the calibration is kept in <output-dir>/<map id>.")] = MAPS,
    clean: Annotated[bool, typer.Option("--clean-up", help="Remove the working files (the prepared map, diagnostic logs) from tmp\\ once done. Kept otherwise; heroes-capture clean-up removes them.")] = False,
    only: Annotated[Optional[str], typer.Option("--only", help="Only these stages (comma-separated: motion, structures, rubble-at-home; or the probe pausing), added to the map's calibration so far.", show_default=False)] = None,
    game: Annotated[Optional[str], typer.Option(hidden=True)] = None,
    monitor: Annotated[Optional[str], typer.Option("--monitor", help="Put the game on this monitor before launching the map, and calibrate at its resolution.", show_default=False)] = None,
) -> None:
    """Calibrate a map: measure in the game what its elements render needs, into <output-dir>/<map id>/calibration.json (and its pictures in calibration/ beside it).

    What still moves while paused, each structure's side switch, its copy's spot and its fall (filmed), and what the map's structures and doodads are made of. The results are reviewed before an elements render can use them; VERIFICATION.md has the steps.
    """
    from heroes_capture.capture import calibration
    from heroes_capture.categories.battleground.specifics import CALIBRATION_PROBES, CALIBRATION_STAGES

    map_spec, extra = split_map(list(ctx.args))
    if map_spec is None:
        raise typer.BadParameter("name the map")
    stages = [n.strip() for n in (only or "").split(",") if n.strip()]
    try:
        calibration.chosen_stages(stages, CALIBRATION_STAGES, CALIBRATION_PROBES)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    maps, _ = maps_to_render(map_spec, None, known_maps())
    options = preparing_options("elements", extra, monitor)
    capture_options = [*(["--game", game] if game else []), *(["--monitor", monitor] if monitor else [])]
    work = Path(options[options.index("--out") + 1])
    log_to(work)
    with kept_on_failure(work):
        calibrate_one(maps[0][0], maps[0][1], options, output_dir, clean, capture_options, stages)
    cleaned_up(work, clean)


@app.command("calibration-review", hidden=True)
def calibration_review(
    run: Annotated[Path, typer.Argument(help="A calibration run's folder (its calibration.json and calibration/ pictures).")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where the review page goes.")] = Path("review"),
) -> None:
    """Build a calibration's review page (index.html, img/, answers.json) to publish."""
    from heroes_capture.categories.battleground import calibration_review as review

    page = review.write_review(run, out)
    data = review.review_data(read_json(run / "calibration.json"))
    ui.done(f"review in {page}: {len(data['falls'])} falls to time, {len(data['spots'])} copy spots and "
            f"{len(data['colour'])} team colours to check, {len(data['motion'])} groups moving while paused")


@app.command("calibration-apply", hidden=True)
def calibration_apply(
    run: Annotated[Path, typer.Argument(help="The calibration run's folder (its calibration.json).")],
    answers: Annotated[Path, typer.Argument(help="The review's answers.json.")],
) -> None:
    """Write a map's reviewed calibration into the package (maps/calibration/<map id>.json)."""
    from heroes_capture.capture import calibration
    from heroes_capture.categories.battleground import calibration_review as review
    from heroes_capture.common import game_data
    from heroes_capture.common.jsonio import write_json

    found = read_json(run / "calibration.json")
    given = read_json(answers)
    if not given.get("submitted"):
        raise typer.BadParameter(f"{answers} hasn't been submitted")
    reviewed = review.apply_review(found, given, review.empty_kinds(run, review.review_data(found)))
    reviewed["rubbleRadii"], edged = review.rubble_radii(run, found, given)
    if edged:
        ui.warn(f"rubble reached the edge of what was shot for {', '.join(edged)}: their circles may be short "
                "(calibrate the structures and rubble at home again to shoot wider)")
    target = calibration.reviewed_path(game_data.slug(found["map"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, reviewed, indent=1)
    ui.done(f"{found['map']}: reviewed calibration in {target} ({len(reviewed['copySpots'])} copy spots, "
            f"{len(reviewed['fallWaits'])} fall waits picked, {len(reviewed['rubbleRadii'])} kinds' rubble circles measured)")


@app.command("game-names", hidden=True)
def game_names(
    pattern: Annotated[str, typer.Argument(help="A regular expression (case ignored), e.g. \"Pause|TimeScale\".")],
) -> None:
    """Search the installed game's program for script names (functions, constants) matching a
    pattern; the list is printed and kept in logs\\game-names.txt."""
    from heroes_capture.common import game_data

    install = game_data.find_install()
    program = game_data.game_program(install) if install else None
    if program is None:
        raise typer.BadParameter("no installed game found (its Versions\\Base*\\HeroesOfTheStorm_x64.exe)")
    names = game_data.script_names(program.read_bytes(), pattern)
    LOGS.mkdir(exist_ok=True)
    (LOGS / "game-names.txt").write_text(f"{program}\n{pattern}\n" + "\n".join(names) + "\n", encoding="utf-8")
    for name in names:
        ui.show(name)
    ui.done(f"{len(names)} names matching {pattern} in {program.name}, kept in {LOGS / 'game-names.txt'}")


def validated_maps() -> dict[str, dict]:
    """The maps verified at the checklist's current version (maps/validated-maps.json, VERIFICATION.md)."""
    from heroes_capture import maps

    return maps.validated_maps()


def known_maps() -> dict[str, dict]:
    """The maps in validated-maps.json at any checklist version: handled, verified or not."""
    from heroes_capture import maps

    return maps.known_maps()


GONE = "No longer in the game"


def map_rows(game_maps: dict[str, str], folder_maps: list[str], validated: dict[str, dict], known: dict[str, dict] | None = None) -> list[tuple[str, str, str]]:
    """(category, map, status) for the game's maps (name -> category), by category and then
    alphabetically; the folder maps, which can't be rendered, under Other; then the known maps the
    game no longer has. Status: "verified" (at the checklist's current version), "not yet",
    "unsupported" (a folder map, or an arena or brawl not known) or "not in the game"."""
    known = validated if known is None else known
    from heroes_capture.common.game_data import CATEGORIES

    rows = []
    for category in CATEGORIES:
        names = [n for n, c in game_maps.items() if c == category] + (folder_maps if category == "Other" else [])
        for name in sorted(names, key=str.casefold):
            if name in folder_maps or not supported(name, category, known):
                status = "unsupported"
            else:
                status = "verified" if name in validated else "not yet"
            rows.append((category, name, status))
    for name in sorted(set(known) - set(game_maps) - set(folder_maps), key=str.casefold):
        rows.append((GONE, name, "not in the game"))
    return rows


STATUS_STYLE = {"verified": "[green]✓ verified[/]", "not yet": "[yellow]not yet[/]", "unsupported": "[dim]unsupported[/]",
                "not in the game": "[red]not in the game[/]"}


@map_app.command("view")
def view(
    map: Annotated[str, typer.Argument(help="The map as the game names it (case and punctuation don't matter), or its folder's name.", show_default=False)],  # noqa: A002
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Where the maps' folders are.")] = MAPS,
    structures: Annotated[Structures, typer.Option(help="Which render: with the structures kept or hidden, or the elements render.")] = Structures.keep,
) -> None:
    """Open a rendered map's pack in its reference viewer, in the browser.

    The pack is served on this computer until you stop it (Ctrl+C): the viewer reads its tiles by range requests, which a browser won't make to files opened from disk.
    """
    from heroes_capture.capture import pack
    from heroes_capture.common.game_data import slug
    from heroes_capture.capture.serve import serve

    folder = output_dir / slug(map)
    if not folder.is_dir():
        from heroes_capture.common import game_data

        with game_data.game_storage() as storage:
            folder = output_dir / slug(game_data.find_map(storage, map))
    variant = pack.variant_folder(folder, structures.value)
    if not (variant / "pack" / "pack.json").exists() or not (variant / "raw" / "index.html").exists():
        raise SystemExit(f"no pack in {variant / 'pack'}: render the map first (heroes-capture map render \"{map}\")")
    server = serve(variant, "raw/index.html")
    ui.done(f"{variant / 'pack'} at http://127.0.0.1:{server.server_port}/raw/index.html (Ctrl+C stops it)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()


@map_app.command("list")
def list_maps(
    keep_tmp: Annotated[bool, typer.Option("--keep-tmp", help="Leave the run's diagnostic log in tmp\\.")] = False,
) -> None:
    """The game's maps by category, and which are verified.

    Battleground: the game's 5v5 maps, the Versus AI / Quick Match / Storm League pool (with the custom-game-only ones: the game's data doesn't tell them apart). Arena and Brawl: the brawl modes' maps; each needs handling of its own, so only the known ones (validated-maps.json) are supported. Other: sandboxes, and Try Me Mode and the tutorials, which are unsupported (the game keeps them as folders, not map archives).

    Verified: the map's render passed the checklist (VERIFICATION.md) at its current version; a change to the render drops every map to "not yet" until it passes again.
    """
    from heroes_capture.common.game_data import folder_maps, game_storage, map_index

    log_to(TMP)
    with kept_on_failure(TMP), ui.step("Reading the game's maps"):
        with game_storage() as storage:
            game_maps = {name: entry["category"] for name, entry in map_index(storage).items()}
            folders = folder_maps(storage)
    rows = map_rows(game_maps, folders, validated_maps(), known_maps())
    table = Table(title="Maps", title_justify="left")
    table.add_column("Map", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    shown = None
    for category, name, status in rows:
        if category != shown:
            if shown:
                table.add_section()
            table.add_row(f"[bold]{escape(category)}[/]")
            shown = category
        table.add_row("  " + escape(name), STATUS_STYLE[status])  # as written, brackets too
    ui.show(table)
    done = sum(status == "verified" for _, _, status in rows)
    unsupported = sum(status == "unsupported" for _, _, status in rows)
    ui.show(f"{done} of {len(game_maps)} maps verified; {unsupported} unsupported")
    clean_up([ui.log_file()], keep_tmp)


# ------------------------------------------------------------------------------------------------
# The interface's textures
# ------------------------------------------------------------------------------------------------


@ui_app.command("extract")
def ui_extract(
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Where the pictures and their catalogue go.")] = UI_ASSETS,
) -> None:
    """Extract the game's interface textures as PNG pictures, with a catalogue of what the game's data says about each.

    The interface's textures: the ones its layouts draw, its asset table names, its catalog entries show as pictures (icons, portraits), and the ones named with its prefixes (storm_ui_, hud_, ...). The catalogue (catalogue.json) gives each one's size, its asset names, the layout frames that draw it and the entries that show it. A texture unchanged since the last run isn't decoded again.
    """
    from heroes_capture.common.game_data import game_storage
    from heroes_capture.ui.extract import extract

    log_to(TMP)
    with kept_on_failure(TMP), ui.step("Extracting the interface's textures"):
        with game_storage() as storage:
            found = extract(storage, output_dir)
    textures = found["textures"]
    failed = sum("error" in e for e in textures.values())
    ui.done(f"{len(textures) - failed} interface textures in {output_dir} (game build {found['build']})"
            + (f"; {failed} the decoder couldn't read" if failed else ""))
    clean_up([ui.log_file()], False)


@ui_app.command("background")
def ui_background(
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Where the scene and its textures go.")] = Path("assets") / "backgrounds" / "play",
) -> None:
    """Export the Play screen's background for a browser to draw: its sky, the energy drifting towards the horizon, the twinkling stars and the planets, and more planets for the app to put up in sets of its own.

    From the game's data: the camera, light and animation of the cutscene the Play screen runs, and its model's meshes, materials and particles (scene.json), with the textures they use as WebP; and the planets (planets/), the Play screen's own and the tutorial flyby's, each in a file of its own.
    """
    from heroes_capture.common.game_data import game_storage
    from heroes_capture.ui.planets import export as export_planets
    from heroes_capture.ui.play_background import export

    log_to(TMP)
    with kept_on_failure(TMP), ui.step("Exporting the Play screen's background"):
        with game_storage() as storage:
            scene = export(storage, output_dir)
            export_planets(storage, output_dir)
    size = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file())
    ui.done(f"The Play screen's background in {output_dir} ({size / 1e3:.0f} kB, game build {scene['source']['build']})")
    clean_up([ui.log_file()], False)


@ui_app.command("search")
def ui_search(
    description: Annotated[str, typer.Argument(help="What to look for, in words: \"gold hexagonal portrait frame\".", show_default=False)],
    count: Annotated[int, typer.Option("--count", "-n", help="How many to list and show.")] = 40,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Where ui extract put the pictures.")] = UI_ASSETS,
) -> None:
    """The interface textures that best fit a description: listed, and shown on a contact sheet.

    Ranked by how near each texture's picture is to the description (an image-text model on the CPU: the search extra), and by its words in what the game's data calls the texture. The first search looks at every picture once (10 to 30 minutes); the rest take seconds.
    """
    from heroes_capture.ui import search as searching

    found, textures, sheet = searching.search(output_dir, description, count)
    table = Table(title=f"\"{description}\" (pictures in {output_dir / 'png'})", title_justify="left")
    table.add_column("#", justify="right")
    table.add_column("Texture", overflow="fold")  # whole: it's the picture's file name
    table.add_column("Size", no_wrap=True)
    table.add_column("Where the game uses it", overflow="fold")
    for i, (name, _) in enumerate(found, 1):
        entry = textures[name]
        table.add_row(str(i), escape(Path(entry["png"]).name), f"{entry['width']}x{entry['height']}", escape(searching.where(entry)))
    ui.show(table)
    ui.done(f"contact sheet: {sheet}")


# ------------------------------------------------------------------------------------------------
# The steps on their own, and the build's check
# ------------------------------------------------------------------------------------------------


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def prepare(ctx: typer.Context) -> None:
    """Prepare a map: read it, inject the capture script, plan the grid (prints the manifest)."""
    from heroes_capture.capture import prepare

    args = list(ctx.args)
    opts = prepare.parse_args(args)  # --help, and a mistyped option, before any file is written
    log_to(Path(opts["out"]))
    with ui.step(f"Preparing {opts['map']}"):
        manifest = prepare.main(args, specifics_of_map)
    print(manifest)


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def capture(ctx: typer.Context) -> None:
    """Capture a prepared map in the running game (heroes-capture capture --help)."""
    args = list(ctx.args)
    if not args or Path(args[0]).suffix != ".json" or {"--help", "-h"} & set(args):
        run_capture(args)  # its usage, or --help
        return
    log_to(Path(args[0]).parent)
    restart = os.environ.get("HRS_RECOVERIES")  # a capture started again after a lost match (capture.recover)
    title = f"Restart {restart} of 3: the map launched again" if restart else f"Capturing {read_json(Path(args[0]))['map']} in the game"
    with ui.step(title):  # the live view, also in a restarted capture's own process
        run_capture(args)


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def stitch(ctx: typer.Context) -> None:
    """Stitch a capture's screenshots into the map image, sky layers and viewer."""
    from heroes_capture.capture import stitch as stitching

    args = list(ctx.args)
    if not args or Path(args[0]).suffix != ".json" or {"--help", "-h"} & set(args):
        stitching.main(args, specifics_of)  # its usage, or --help
        return
    log_to(Path(args[0]).parent)
    with ui.step(f"Stitching {read_json(Path(args[0]))['map']}"):
        stitching.main(args, specifics_of)


@app.command("clean-up")
def clean_up_command() -> None:
    """Remove the working files runs left in tmp\\ (only the ones heroes-capture wrote)."""
    found = working_files(TMP)
    if not found:
        ui.done(f"Nothing to clean up in {TMP}")
        return
    size = sum(f.stat().st_size for p in found for f in ([p] if p.is_file() else p.rglob("*")) if f.is_file())
    runs = sum(p.suffix == ".json" for p in found)
    remove(found)
    try:
        TMP.rmdir()
    except OSError:
        pass
    left = [p for p in found if p.exists()]
    for path in left:
        ui.warn(f"couldn't remove {path} (still open?)")
    renders = f"{runs} render{'s' if runs != 1 else ''}, " if runs else ""
    others = f"; the other files in {TMP} aren't heroes-capture's and are left alone" if TMP.exists() and not left else ""
    amount = f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"
    ui.done(f"Removed the working files in {TMP} ({renders}{amount}){others}")


@app.command("ui-demo", hidden=True)
def ui_demo() -> None:
    """The output's look, warnings included, without the game: two steps with progress bars and,
    partway through, a warning from each source (ours, Python's warnings, a library's logging, a
    library writing straight to stdout and stderr)."""
    import logging
    import time
    import warnings

    log_to(TMP)
    manifest = {"tiles": [{}] * 40, "elements": {"structures": [{}] * 12, "camps": [{}] * 2}}
    with ui.folder("Run 1 of 1: Demo Map: elements render", section=True):
        plan_render("elements", "Capturing in the game", None)
        with ui.step("Preparing the map"):
            ui.info("game data: D:\\Games\\Heroes of the Storm")
            time.sleep(1.0)
            ui.info("grid: 8×5 = 40 tiles from 214 cells up; the map image about 12373×8126 px")
            time.sleep(0.6)
        plan_render("elements", "Capturing in the game", None, manifest)
        with ui.step("Capturing in the game"):
            with ui.folder("Launching the map"):
                ui.info("launching Demo Map")
                time.sleep(1.0)
            with ui.folder("Starting up"):
                for title in ("Status strip", "Map ready"):
                    with ui.folder(title):
                        time.sleep(0.5)
                ui.info("the map is ready after 1 s (game clock 18 s)")
            with ui.folder("Elements"):
                with ui.folder("Rubble preparation", 3) as counter:
                    for n in range(3):
                        ui.info(f"group {n + 1} of 3: 4 falling")
                        counter.advance()
                        time.sleep(0.4)
                with ui.folder("Standing (blue)", 12) as counter:
                    for n in range(12):
                        ui.info(f"Cannon Tower ({40 + n * 9}, 96)")
                        counter.advance()
                        time.sleep(0.15)
                with ui.folder("Neighbours", 12) as counter:
                    for n in range(12):
                        counter.advance()
                        time.sleep(0.05)
                with ui.folder("Standing (red)", 8) as counter:
                    for n in range(8):
                        ui.info(f"Healing Fountain ({52.5 + n * 12:g}, 68.5)")
                        if n == 3:
                            with ui.notice("the game isn't in front (explorer.exe is): click into the game to carry on",
                                           "explorer.exe was in front; Healing Fountain (88.5, 68.5) as red done again"):
                                time.sleep(2.0)
                        counter.advance()
                        time.sleep(0.2)
                for title, total in (("Camps", 2), ("Rubble", 12)):
                    with ui.folder(title, total) as counter:
                        for n in range(total):
                            ui.info(f"wave {n // 4 + 1} of 3" if title == "Rubble" else f"camp wave {n + 1}")
                            counter.advance()
                            time.sleep(0.15)
                with ui.folder("Clearing"):
                    time.sleep(0.5)
            with ui.folder("Tiles (blue)", 40) as counter:
                for n in range(40):
                    ui.info(f"tile {n + 1} of 40")
                    if n == 10:
                        ui.warn("tile 11: no picture (the map didn't answer, or the frames were black); not saved")
                    if n == 18:
                        warnings.warn("a Python warning from some library", RuntimeWarning)
                    if n == 24:
                        logging.getLogger("dxcam").warning("a library's logging warning")
                    if n == 30:
                        print("a library printing straight to stdout")
                        print("and to stderr", file=sys.stderr)
                    counter.advance()
                    time.sleep(0.1)
            with ui.folder("Leaving the match"):
                time.sleep(0.3)
            ui.done("40 screenshots")
        with ui.step("Stitching and packing"):
            for stage in ("Matching neighbours", "Placing the screenshots", "Routing the seams", "Composing", "Writing the map image", "Packing"):
                with ui.folder(stage):
                    ui.info(stage.lower())
                    time.sleep(0.5)
        ui.done("the map is in maps\\demo-map")


@app.command("self-check", hidden=True)
def self_check() -> None:
    """Load everything the tool loads on demand, as a built executable must carry it all: the
    modules imported lazily or by name (dxcam's compiled kernel, pyvips' compiled binding, Rich's
    Unicode tables), and StormLib and CascLib. The build runs it on the fresh executable
    (tools/build_exe.py)."""
    import importlib
    import io
    import pkgutil

    from . import __path__ as package_path

    ours = [m.name for m in pkgutil.walk_packages(package_path, "heroes_capture.") if m.name != "heroes_capture.__main__"]
    others = ["numpy.fft", "scipy.ndimage", "scipy.optimize", "PIL.Image", "pyvips"]
    if sys.platform == "win32":
        others += ["dxcam", "dxcam.processor._numpy_kernels", "mss", "pydirectinput"]
    loaded = 0
    for name in ours + others:
        try:
            importlib.import_module(name)
            loaded += name in ours
        except ModuleNotFoundError as e:
            # The modules that drive the game need Windows' screen grabbing and input to import.
            if sys.platform == "win32" or e.name not in WINDOWS_ONLY:
                raise
    import pyvips

    if not pyvips.API_mode:
        raise SystemExit("pyvips runs without its compiled binding (_libvips)")
    table = Table("Map", "Status")
    table.add_row("Battlefield of Eternity", STATUS_STYLE["verified"])
    Console(file=io.StringIO(), width=80, color_system="truecolor").print(table)  # loads Rich's Unicode width tables
    validated_maps()
    from heroes_capture.common import casclib
    from heroes_capture.common import stormlib

    stormlib._load()
    casclib._load()
    print(f"self-check: {loaded} modules, Rich, the data files and both native libraries load")


def main(argv: list[str] | None = None) -> None:
    app(args=argv, prog_name="heroes-capture")
