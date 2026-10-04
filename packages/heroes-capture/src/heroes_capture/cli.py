"""The heroes-capture command (Typer, with Rich for its output).

    heroes-capture map render "Battlefield of Eternity"         prepare, capture and stitch a battleground
    heroes-capture map render "Cursed Hollow" --structures hide   bare terrain
    heroes-capture map list                                     the game's maps, and which are validated
    heroes-capture --version

`prepare`, `capture` and `stitch` are the render's steps on their own; they keep their own option
parsers for now (`heroes-capture prepare --help`).
"""

import json
import shutil
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import ui

PROBES = ("probe_light", "probe_sky", "probe_depth", "probe_waits")
DISTANCE = "214"  # camera distance: far, so tall objects lean little at the seams
KEEP = "0.4"  # share of each screenshot used, centred
VALIDATED = Path(__file__).with_name("validated-maps.json")
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


def screen_size() -> str | None:
    """The primary monitor's resolution in real pixels ("3440x1440"), on Windows."""
    if sys.platform != "win32":
        return None
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()
    return f"{ctypes.windll.user32.GetSystemMetrics(0)}x{ctypes.windll.user32.GetSystemMetrics(1)}"


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
    """Every message also into <folder>/heroes-capture.log."""
    ui.set_log_file(folder / "heroes-capture.log")


# ------------------------------------------------------------------------------------------------
# map render, map list
# ------------------------------------------------------------------------------------------------


class Structures(str, Enum):
    keep = "keep"
    hide = "hide"


@map_app.command("render", context_settings=PASS_THROUGH)
def render(
    ctx: typer.Context,
    map: Annotated[str, typer.Argument(help="The map as the game names it (case and punctuation don't matter), or a path to a .stormmap.", show_default=False)],  # noqa: A002
    structures: Annotated[Structures, typer.Option(help="Keep or hide forts, towers, cores and gates.")] = Structures.keep,
    game: Annotated[Optional[str], typer.Option(hidden=True)] = None,  # the install, when it isn't found by itself
    probe_light: Annotated[bool, typer.Option(hidden=True)] = False,  # diagnostics instead of the tiles
    probe_sky: Annotated[bool, typer.Option(hidden=True)] = False,
    probe_depth: Annotated[bool, typer.Option(hidden=True)] = False,
    probe_waits: Annotated[bool, typer.Option(hidden=True)] = False,
    show_ui: Annotated[bool, typer.Option(hidden=True)] = False,  # diagnostic: launch with the HUD up and stop
) -> None:
    """Prepare, capture and stitch a battleground.

    Writes into work\\ in the current folder. Options after the map that aren't listed here go to the preparing step (heroes-capture prepare --help).
    """
    map_name = map
    from . import inject, stitch

    prepare_options = list(ctx.args)
    log_to(Path(prepare_options[prepare_options.index("--out") + 1] if "--out" in prepare_options else "work"))
    prepare = [map_name, "--structures", structures.value, *prepare_options]
    if "--screen" not in prepare_options and screen_size():
        prepare += ["--screen", screen_size()]
    if not {"--distance", "--fov"} & set(prepare_options):
        prepare += ["--distance", DISTANCE]
    if "--keep" not in prepare_options:
        prepare += ["--keep", KEEP]
    if show_ui:
        prepare.append("--show-ui")
    with ui.step(f"Preparing {map_name}"):
        manifest = str(inject.main(prepare))
    planned = json.loads(Path(manifest).read_text())
    ui.done(f"{planned['map']} ({structures.value} structures): {len(planned['tiles'])} tiles planned, "
            + ("void shot over white and black" if planned["sky"]["mode"] == "matte" else "black void"))
    capture_options = ["--game", game] if game else []
    chosen = {"probe_light": probe_light, "probe_sky": probe_sky, "probe_depth": probe_depth, "probe_waits": probe_waits}
    probe = next((f"--{name.replace('_', '-')}" for name in PROBES if chosen[name]), None)
    if probe:
        with ui.step(f"Probe {probe}"):
            run_capture([manifest, probe, *capture_options])
        return
    if show_ui:
        with ui.step("Launching the map with the HUD up (diagnostic; stops there)"):
            run_capture([manifest, "--launch-only", *capture_options])
        return
    shutil.rmtree(Path(manifest).with_suffix("") / "tiles", ignore_errors=True)  # old screenshots would mix in
    with ui.step(f"Capturing {planned['map']} in the game"):
        run_capture([manifest, *capture_options])
    with ui.step("Stitching"):
        stitch.main([manifest, "--tiles"])
    ui.done(f"The images are next to {manifest}")


def validated_maps() -> dict[str, dict]:
    """validated-maps.json's maps: name -> {version, note}."""
    return json.loads(VALIDATED.read_text(encoding="utf-8"))["maps"]


GONE = "No longer in the game"


def map_rows(game_maps: dict[str, str], unsupported: list[str], validated: dict[str, dict]) -> list[tuple[str, str, str, str, str]]:
    """(category, map, status, validated with, note) for the game's maps (name -> category), by
    category and then alphabetically; the maps that can't be rendered, under Other; then the
    validated maps the game no longer has. Status: "validated", "not yet", "unsupported" or
    "not in the game"."""
    from .game_data import CATEGORIES

    def row(category, name, status):
        entry = validated.get(name, {})
        return (category, name, status, entry.get("version", ""), entry.get("note", ""))

    rows = []
    for category in CATEGORIES:
        names = [n for n, c in game_maps.items() if c == category] + (unsupported if category == "Other" else [])
        for name in sorted(names, key=str.casefold):
            status = "unsupported" if name in unsupported else "validated" if name in validated else "not yet"
            rows.append(row(category, name, status))
    for name in sorted(set(validated) - set(game_maps) - set(unsupported), key=str.casefold):
        rows.append(row(GONE, name, "not in the game"))
    return rows


STATUS_STYLE = {"validated": "[green]✓ validated[/]", "not yet": "[yellow]not yet[/]", "unsupported": "[dim]unsupported[/]",
                "not in the game": "[red]not in the game[/]"}


@map_app.command("list")
def list_maps() -> None:
    """The game's maps by category, and which have been validated.

    Battleground: the game's 5v5 maps, the Versus AI / Quick Match / Storm League pool (with the custom-game-only ones: the game's data doesn't tell them apart). Arena and Brawl: the brawl modes' maps. Other: sandboxes, and Try Me Mode and the tutorials, which are unsupported (the game keeps them as folders, not map archives).

    Validated: the map's render has been reviewed and, where needed, tuned for (validated-maps.json). The rest render with the defaults, unchecked.
    """
    from .game_data import find_install, folder_maps, map_index, open_storage

    log_to(Path("work"))
    with ui.step("Reading the game's maps"):
        with open_storage(find_install()) as storage:
            game_maps = {name: entry["category"] for name, entry in map_index(storage).items()}
            unsupported = folder_maps(storage)
    rows = map_rows(game_maps, unsupported, validated_maps())
    table = Table(title="Maps", title_justify="left")
    table.add_column("Map", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Validated with", no_wrap=True)
    table.add_column("Checked", overflow="fold")
    shown = None
    for category, name, status, validated_with, note in rows:
        if category != shown:
            if shown:
                table.add_section()
            table.add_row(f"[bold]{category}[/]")
            shown = category
        table.add_row("  " + name, STATUS_STYLE[status], validated_with, note)
    console = Console()
    console.print(table)
    done = sum(status == "validated" for _, _, status, _, _ in rows)
    console.print(f"{done} of {len(game_maps)} maps validated; {len(unsupported)} unsupported")


# ------------------------------------------------------------------------------------------------
# The steps on their own, and the build's check
# ------------------------------------------------------------------------------------------------


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def prepare(ctx: typer.Context) -> None:
    """Prepare a map: read it, inject the capture script, plan the grid (prints the manifest)."""
    from . import inject

    args = list(ctx.args)
    log_to(Path(args[args.index("--out") + 1] if "--out" in args else "work"))
    print(inject.main(args))


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def capture(ctx: typer.Context) -> None:
    """Capture a prepared map in the running game (heroes-capture capture --help)."""
    args = list(ctx.args)
    if args and Path(args[0]).suffix == ".json":
        log_to(Path(args[0]).parent)
    run_capture(args)


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def stitch(ctx: typer.Context) -> None:
    """Stitch a capture's screenshots into the map image, sky layers and viewer."""
    from . import stitch as stitching

    args = list(ctx.args)
    if args and Path(args[0]).suffix == ".json":
        log_to(Path(args[0]).parent)
    stitching.main(args)


@app.command("ui-demo", hidden=True)
def ui_demo() -> None:
    """The output's look, warnings included, without the game: two steps with progress bars and,
    partway through, a warning from each source (ours, Python's warnings, a library's logging, a
    library writing straight to stdout and stderr)."""
    import logging
    import time
    import warnings

    log_to(Path("work"))
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
    ui.done("40 screenshots in work\\demo")
    with ui.step("Stitching"):
        for stage in ("matching neighbours", "routing seams", "composing", "writing the map image"):
            ui.info(stage)
            time.sleep(0.8)
    ui.done("The images are next to work\\demo.json")


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
    windows_only = {"capture", "game_control", "game_state", "game_window", "probes", "screen", "sky_layers"}
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
