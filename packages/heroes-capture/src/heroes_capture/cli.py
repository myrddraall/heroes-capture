"""The heroes-capture command (Typer, with Rich for its output).

    heroes-capture map render "Battlefield of Eternity"         prepare, capture and stitch a battleground
    heroes-capture map render "Cursed Hollow" --structures hide   bare terrain
    heroes-capture map render "Dragon Shire" -o D:\\renders        into D:\\renders\\dragon-shire
    heroes-capture map render --category all                    every map, skipping those rendered
    heroes-capture map list                                     the game's maps, and which are validated
    heroes-capture clean-up                                     remove the working files a failed render left in tmp\\
    heroes-capture --version

`prepare`, `capture` and `stitch` are the render's steps on their own; they keep their own option
parsers for now (`heroes-capture prepare --help`).
"""

import json
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

from . import ui
from .game_menus import ScriptBroken

PROBES = ("probe_light", "probe_sky", "probe_sky_reach", "probe_depth", "probe_waits", "probe_elements")
DISTANCE = "214"  # camera distance: far, so tall objects lean little at the seams
KEEP = "0.4"  # share of each screenshot used, centred
VALIDATED = Path(__file__).with_name("validated-maps.json")
TMP = Path("tmp")  # the working files and the diagnostic log, in the current folder
LOGS = Path("logs")  # the plain log: the output as --log prints it, kept
MAPS = Path("maps")  # the default output folder
PASS_THROUGH = {"allow_extra_args": True, "ignore_unknown_options": True}

app = typer.Typer(
    help="Captures Heroes of the Storm assets from the game client. Start Heroes from the Battle.net app "
         "first, in Windowed (Fullscreen).",
    no_args_is_help=True, add_completion=False, context_settings={"help_option_names": ["-h", "--help"]},
)
map_app = typer.Typer(help="Battlegrounds: render one, or list them.", no_args_is_help=True)
app.add_typer(map_app, name="map")


def version() -> str:
    try:
        from ._version import VERSION
        return VERSION
    except ImportError:
        return "0.0.0-dev"


def screen_size(monitor: str | None = None) -> str | None:
    """A monitor's resolution in real pixels ("3440x1440"), on Windows: the one `monitor` names
    (monitors.choose), or the primary. ValueError when none is named so."""
    if sys.platform != "win32":
        return None
    from .monitors import choose

    return choose(monitor or "primary").size


def run_capture(argv: list[str]) -> None:
    """The capture, started again after a lost match (capture.recover); a crash's traceback goes
    into the run's log too, so a failed run can be diagnosed from the copied results."""
    from . import capture
    from .runlog import log

    try:
        capture.main(argv)
    except capture.Recoverable as e:
        with ui.suspend():  # the restarted capture draws its own view
            code = capture.recover(e, argv)
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


def clean_up(paths: list[Path], keep: bool) -> None:
    """A finished command's working files removed, and their folder too once empty; with keep
    (--keep-tmp), left for diagnosis. A failed command doesn't get here: its files stay (see
    kept_on_failure), for clean-up to remove later."""
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
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id") == manifest.stem and "tiles" in data:
            found += [manifest, *(p for p in (manifest.with_suffix(".stormmap"), manifest.with_suffix("")) if p.exists())]
    found += [p for p in sorted(folder.glob("*.stormmap")) if p not in found]
    found += sorted(folder.glob("heroes-capture*.log"))
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
    from .inject import OPTION_VALUES

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
# hero selection, its rounds), so in these categories only the validated maps are supported.
SUPPORTED_ONCE_VALIDATED = {"Arena", "Brawl"}


def supported(name: str, category: str, validated: dict) -> bool:
    """Whether the capture handles this map of the game's: a validated one, or one outside the
    categories whose maps need handling of their own."""
    return name in validated or category not in SUPPORTED_ONCE_VALIDATED


def maps_to_render(map_spec: str | None, category: Category | None, validated: dict) -> tuple[list[tuple[str, str]], int]:
    """(what to prepare, the map's name) for each map to render, and how many unsupported maps the
    category left out: the one named (the game's spelling of its name; a .stormmap path's file
    name; an unsupported arena or brawl renders when named, with a warning), or the category's
    supported maps (all of them for "all"), in map list's order."""
    from . import game_data

    if map_spec and map_spec.lower().endswith(".stormmap"):
        return [(map_spec, Path(map_spec).name[: -len(".stormmap")])], 0
    with game_data.open_storage(game_data.find_install()) as storage:
        index = game_data.map_index(storage)
        if map_spec:
            name = game_data.find_map(storage, map_spec)
            if not supported(name, index[name]["category"], validated):
                ui.warn(f"{name} is unsupported: {index[name]['category'].lower()} maps each need handling of their own, "
                        "and this one hasn't been validated; rendering it anyway")
            return [(name, name)], 0
    order = {c: n for n, c in enumerate(game_data.CATEGORIES)}
    chosen = [n for n, e in index.items() if category == Category.all or e["category"].lower() == category.value]
    names = sorted((n for n in chosen if supported(n, index[n]["category"], validated)),
                   key=lambda n: (order[index[n]["category"]], n.casefold()))
    return [(n, n) for n in names], len(chosen) - len(names)


def kept_run(work: Path, map_id: str, options: list[str]) -> Path | None:
    """The manifest of a run left unfinished in `work` (failed, stopped, or --keep-tmp) that a
    render with these preparing options can carry on: the same map, prepared the same way."""
    manifest = work / f"{map_id}.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("renderOptions") != options or not manifest.with_suffix(".stormmap").exists():
        return None
    return manifest.resolve()  # as a fresh preparation's


def first_missing_tile(manifest_path: Path, manifest: dict) -> int:
    """Where a kept run's capture carries on: at its first tile without a screenshot, less the
    ones that may have been half-written when it stopped. len(tiles) when all are there."""
    from .frames import frame_exists

    tiles = manifest_path.with_suffix("") / "tiles"
    for position, tile in enumerate(manifest["tiles"]):
        if not frame_exists(tiles / f"tile_{tile['index']:04d}"):
            return max(0, position - SAVERS)
    return len(manifest["tiles"])


def render_one(map_spec: str, name: str, options: list[str], output_dir: Path, keep_tmp: bool, force: bool,
               capture_options: list[str], validated: dict) -> bool:
    """One map: skipped when its pack is in the output folder already (unless force), carried on
    from a kept run of it, or prepared, captured and stitched (the stitch writes its pack); its
    working files removed after (unless keep_tmp). False when skipped."""
    from . import inject, pack, stitch

    structures = options[options.index("--structures") + 1]
    work = Path(options[options.index("--out") + 1])
    map_id = inject.render_id(name, structures)
    out = output_dir / inject.slug(name)
    if not force and pack.is_written(out, structures):
        ui.done(f"{name}: already rendered in {out} (--force renders it again)")
        return False
    note = "" if name in validated else "; not validated: it may not come out right"
    manifest_path = None if force else kept_run(work, map_id, options)
    if manifest_path:
        planned = json.loads(manifest_path.read_text())
        start = first_missing_tile(manifest_path, planned)
        ui.done(f"{name}: carrying on from the run left in {work} ({start} of {len(planned['tiles'])} tiles kept){note}")
    else:
        with ui.step(f"Preparing {name}"):
            manifest_path = inject.main([map_spec, *options])
        planned = json.loads(manifest_path.read_text())
        planned["renderOptions"] = options  # what a later render must match to carry this one on
        manifest_path.write_text(json.dumps(planned, indent=2))
        start = 0
        ui.done(f"{planned['map']} ({structures} structures): {len(planned['tiles'])} tiles planned, "
                + ("void shot over white and black" if planned["sky"]["mode"] == "matte" else "black void") + note)
    manifest = str(manifest_path)
    if start < len(planned["tiles"]):
        if not start:
            for folder in ("tiles", "elements"):  # old screenshots would mix in (the elements' record skips what it lists)
                shutil.rmtree(manifest_path.with_suffix("") / folder, ignore_errors=True)
        with ui.step(f"Capturing {name} in the game"):
            run_capture([manifest, *(["--start", str(start)] if start else []), *capture_options])
    with ui.step(f"Stitching {name}"):
        stitch.main([manifest, "--output-dir", str(output_dir)])
    if not keep_tmp:
        remove([manifest_path, manifest_path.with_suffix(".stormmap"), manifest_path.with_suffix("")])
    ui.done(f"{name}: the map is in {stitch.output_folder(output_dir, planned)} (heroes-capture map view \"{name}\" shows it)")
    return True


@map_app.command("render", context_settings=PASS_THROUGH, options_metavar="[OPTIONS] [MAP]")
def render(
    ctx: typer.Context,
    category: Annotated[Optional[Category], typer.Option("--category", "-c", help="Render every map of a category instead of one map; all: every map. Unsupported maps are always left out.", show_default=False)] = None,
    structures: Annotated[Structures, typer.Option(help="Keep or hide forts, towers, cores and gates; or elements: each structure and camp on its own, over the bare terrain (in development, ELEMENTS-PLAN.md).")] = Structures.keep,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Where the maps' folders go: <output-dir>/<map id>, e.g. maps/dragon-shire.")] = MAPS,
    force: Annotated[bool, typer.Option("--force", help="Render maps already rendered in the output folder again, from the start.")] = False,
    keep_tmp: Annotated[bool, typer.Option("--keep-tmp", help="Leave the working files (screenshots, the prepared map, diagnostic logs) in tmp\\ for diagnosis.")] = False,
    game: Annotated[Optional[str], typer.Option(hidden=True)] = None,  # the install, when it isn't found by itself
    monitor: Annotated[Optional[str], typer.Option("--monitor", help="Put the game on this monitor before launching the map, and render at its resolution: its number as Windows lists them (the run's log lists the monitors), its device name, or primary.", show_default=False)] = None,
    probe_light: Annotated[bool, typer.Option(hidden=True)] = False,  # diagnostics instead of the tiles
    probe_sky: Annotated[bool, typer.Option(hidden=True)] = False,
    probe_sky_reach: Annotated[bool, typer.Option(hidden=True)] = False,
    probe_depth: Annotated[bool, typer.Option(hidden=True)] = False,
    probe_waits: Annotated[bool, typer.Option(hidden=True)] = False,
    probe_elements: Annotated[bool, typer.Option(hidden=True)] = False,
    show_ui: Annotated[bool, typer.Option(hidden=True)] = False,  # diagnostic: launch with the HUD up and stop
) -> None:
    """Prepare, capture and stitch a map, or every map of a category.

    MAP: the map as the game names it (case and punctuation don't matter), or a path to a .stormmap; or --category instead.

    The images go to <output-dir>/<map id> (maps\\<map id> in the current folder). A map already rendered there is skipped (--force renders it again), and a map whose render was left unfinished (failed or stopped) carries on from its working files. A map that fails doesn't stop a category's run. Maps that haven't been validated render too; they may not come out right.

    The working files and the diagnostic log go to tmp\\ and are removed once done, unless --keep-tmp; a failed render leaves them. The output, as --log prints it, goes to logs\\heroes-capture.log and stays. Options that aren't listed here go to the preparing step (heroes-capture prepare --help).
    """
    import traceback

    from . import inject

    map_spec, extra = split_map(list(ctx.args))
    if (map_spec is None) == (category is None):
        raise typer.BadParameter("name one map, or pick maps with --category")
    chosen = {"probe_light": probe_light, "probe_sky": probe_sky, "probe_sky_reach": probe_sky_reach, "probe_depth": probe_depth, "probe_waits": probe_waits, "probe_elements": probe_elements}
    probe = next((f"--{name.replace('_', '-')}" for name in PROBES if chosen[name]), None)
    if category and (probe or show_ui):
        raise typer.BadParameter("the diagnostics take one map")
    validated = validated_maps()
    maps, left_out = maps_to_render(map_spec, category, validated)  # a name that isn't a renderable map stops here, before any file is written
    work = Path(extra[extra.index("--out") + 1]) if "--out" in extra else TMP
    options = ["--structures", structures.value, *extra]
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
    capture_options = [*(["--game", game] if game else []), *(["--monitor", monitor] if monitor else [])]
    log_to(work)

    if probe or show_ui:  # diagnostics: prepared afresh; their files stay in tmp\
        with kept_on_failure(work):
            if show_ui:
                options.append("--show-ui")
            with ui.step(f"Preparing {maps[0][1]}"):
                manifest = str(inject.main([maps[0][0], *options]))
            with ui.step(f"Probe {probe}" if probe else "Launching the map with the HUD up (diagnostic; stops there)"):
                run_capture([manifest, probe or "--launch-only", *capture_options])
        return

    if category:
        ui.done(f"{len(maps)} maps to render ({category.value}), {sum(n in validated for _, n in maps)} of them validated"
                + (f"; {left_out} unsupported left out" if left_out else ""))
    rendered, skipped, failed = 0, 0, []
    for map_spec, name in maps:
        try:
            if render_one(map_spec, name, options, output_dir, keep_tmp, force, capture_options, validated):
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
    clean_up([ui.log_file()], keep_tmp)


def validated_maps() -> dict[str, dict]:
    """validated-maps.json's maps: name -> {version, note}."""
    return json.loads(VALIDATED.read_text(encoding="utf-8"))["maps"]


GONE = "No longer in the game"


def map_rows(game_maps: dict[str, str], folder_maps: list[str], validated: dict[str, dict]) -> list[tuple[str, str, str]]:
    """(category, map, status) for the game's maps (name -> category), by category and then
    alphabetically; the folder maps, which can't be rendered, under Other; then the validated maps
    the game no longer has. Status: "validated", "not yet", "unsupported" (a folder map, or an
    arena or brawl not validated) or "not in the game"."""
    from .game_data import CATEGORIES

    rows = []
    for category in CATEGORIES:
        names = [n for n, c in game_maps.items() if c == category] + (folder_maps if category == "Other" else [])
        for name in sorted(names, key=str.casefold):
            if name in folder_maps or not supported(name, category, validated):
                status = "unsupported"
            else:
                status = "validated" if name in validated else "not yet"
            rows.append((category, name, status))
    for name in sorted(set(validated) - set(game_maps) - set(folder_maps), key=str.casefold):
        rows.append((GONE, name, "not in the game"))
    return rows


STATUS_STYLE = {"validated": "[green]✓ validated[/]", "not yet": "[yellow]not yet[/]", "unsupported": "[dim]unsupported[/]",
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
    from . import pack
    from .inject import slug
    from .serve import serve

    folder = output_dir / slug(map)
    if not folder.is_dir():
        from . import game_data

        with game_data.open_storage(game_data.find_install()) as storage:
            folder = output_dir / slug(game_data.find_map(storage, map))
    packed = pack.variant_folder(folder, structures.value) / "pack"
    if not (packed / "pack.json").exists():
        raise SystemExit(f"no pack in {packed}: render the map first (heroes-capture map render \"{map}\")")
    server = serve(packed)
    ui.done(f"{packed} at http://127.0.0.1:{server.server_port}/index.html (Ctrl+C stops it)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()


@map_app.command("list")
def list_maps(
    keep_tmp: Annotated[bool, typer.Option("--keep-tmp", help="Leave the run's diagnostic log in tmp\\.")] = False,
) -> None:
    """The game's maps by category, and which have been validated.

    Battleground: the game's 5v5 maps, the Versus AI / Quick Match / Storm League pool (with the custom-game-only ones: the game's data doesn't tell them apart). Arena and Brawl: the brawl modes' maps; each needs handling of its own, so only the validated ones are supported. Other: sandboxes, and Try Me Mode and the tutorials, which are unsupported (the game keeps them as folders, not map archives).

    Validated: the map's render has been reviewed and, where needed, tuned for (validated-maps.json). The rest render with the defaults, unchecked.
    """
    from .game_data import find_install, folder_maps, map_index, open_storage

    log_to(TMP)
    with kept_on_failure(TMP), ui.step("Reading the game's maps"):
        with open_storage(find_install()) as storage:
            game_maps = {name: entry["category"] for name, entry in map_index(storage).items()}
            folders = folder_maps(storage)
    rows = map_rows(game_maps, folders, validated_maps())
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
    done = sum(status == "validated" for _, _, status in rows)
    unsupported = sum(status == "unsupported" for _, _, status in rows)
    ui.show(f"{done} of {len(game_maps)} maps validated; {unsupported} unsupported")
    clean_up([ui.log_file()], keep_tmp)


# ------------------------------------------------------------------------------------------------
# The steps on their own, and the build's check
# ------------------------------------------------------------------------------------------------


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def prepare(ctx: typer.Context) -> None:
    """Prepare a map: read it, inject the capture script, plan the grid (prints the manifest)."""
    from . import inject

    args = list(ctx.args)
    opts = inject.parse_args(args)  # --help, and a mistyped option, before any file is written
    log_to(Path(opts["out"]))
    with ui.step(f"Preparing {opts['map']}"):
        manifest = inject.main(args)
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
    title = f"Capturing {json.loads(Path(args[0]).read_text())['map']} in the game" + (f" (restart {restart} of 3)" if restart else "")
    with ui.step(title):  # the live view, also in a restarted capture's own process
        run_capture(args)


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def stitch(ctx: typer.Context) -> None:
    """Stitch a capture's screenshots into the map image, sky layers and viewer."""
    from . import stitch as stitching

    args = list(ctx.args)
    if not args or Path(args[0]).suffix != ".json" or {"--help", "-h"} & set(args):
        stitching.main(args)  # its usage, or --help
        return
    log_to(Path(args[0]).parent)
    with ui.step(f"Stitching {json.loads(Path(args[0]).read_text())['map']}"):
        stitching.main(args)


@app.command("clean-up")
def clean_up_command() -> None:
    """Remove the working files a failed or --keep-tmp run left in tmp\\ (only the ones heroes-capture wrote)."""
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
    with ui.step("Capturing Demo Map in the game"):
        ui.info("waiting for the map's status strip ...")
        time.sleep(1.5)
        with ui.bar(40, "tiles") as advance:
            for n in range(40):
                ui.info(f"tile {n + 1}/40  (row {n // 8}, col {n % 8})")
                ui.detail(f"tile {n + 1}: camera at ({20 + 26 * (n % 8)}, {178 - 11.5 * (n // 8)})")
                if n == 10:
                    ui.warn("focus lost during tile 11/40 (in front: explorer.exe); redoing it from the start")
                if n == 18:
                    warnings.warn("a Python warning from some library", RuntimeWarning)
                if n == 24:
                    logging.getLogger("dxcam").warning("a library's logging warning")
                if n == 30:
                    print("a library printing straight to stdout")
                    print("and to stderr", file=sys.stderr)
                advance()
                time.sleep(0.15)
    ui.done("40 screenshots in tmp\\demo")
    with ui.step("Stitching"):
        for stage in ("matching neighbours", "routing seams", "composing", "writing the map image"):
            ui.info(stage)
            time.sleep(0.8)
    ui.done("The map is in maps\\demo-map")


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

    # The modules that drive the game need Windows to import at all.
    windows_only = {"capture", "game_control", "game_state", "game_window", "monitors", "probes", "screen", "sky_layers"}
    skip = {"__main__"} | (set() if sys.platform == "win32" else windows_only)
    modules = [f"heroes_capture.{m.name}" for m in pkgutil.iter_modules(package_path) if m.name not in skip]
    modules += ["numpy.fft", "scipy.ndimage", "scipy.optimize", "PIL.Image", "pyvips"]
    if sys.platform == "win32":
        modules += ["dxcam", "dxcam.processor._numpy_kernels", "mss", "pydirectinput"]
    for name in modules:
        importlib.import_module(name)
    import pyvips

    if not pyvips.API_mode:
        raise SystemExit("pyvips runs without its compiled binding (_libvips)")
    table = Table("Map", "Status")
    table.add_row("Battlefield of Eternity", STATUS_STYLE["validated"])
    Console(file=io.StringIO(), width=80, color_system="truecolor").print(table)  # loads Rich's Unicode width tables
    validated_maps()
    from . import casclib, stormlib

    stormlib._load()
    casclib._load()
    print(f"self-check: {len(modules)} modules, Rich, the data files and both native libraries load")


def main(argv: list[str] | None = None) -> None:
    app(args=argv, prog_name="heroes-capture")
