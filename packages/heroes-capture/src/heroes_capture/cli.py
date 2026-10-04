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
) -> None:
    pass


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
    prepare = [map_name, "--structures", structures.value, *prepare_options]
    if "--screen" not in prepare_options and screen_size():
        prepare += ["--screen", screen_size()]
    if not {"--distance", "--fov"} & set(prepare_options):
        prepare += ["--distance", DISTANCE]
    if "--keep" not in prepare_options:
        prepare += ["--keep", KEEP]
    if show_ui:
        prepare.append("--show-ui")
    print(f"\n[1/3] Preparing {map_name} (structures: {structures.value})")
    manifest = str(inject.main(prepare))
    capture_options = ["--game", game] if game else []
    chosen = {"probe_light": probe_light, "probe_sky": probe_sky, "probe_depth": probe_depth, "probe_waits": probe_waits}
    probe = next((f"--{name.replace('_', '-')}" for name in PROBES if chosen[name]), None)
    if probe:
        print(f"\nProbe {probe}")
        run_capture([manifest, probe, *capture_options])
        return
    if show_ui:
        print("\nDiagnostic run: launching the map and stopping here.")
        run_capture([manifest, "--launch-only", *capture_options])
        return
    shutil.rmtree(Path(manifest).with_suffix("") / "tiles", ignore_errors=True)  # old screenshots would mix in
    print("\n[2/3] Capturing")
    run_capture([manifest, *capture_options])
    print("\n[3/3] Stitching")
    stitch.main([manifest, "--tiles"])
    print(f"\nDone. The image is next to {manifest}")


def validated_maps() -> dict[str, dict]:
    """validated-maps.json's maps: name -> {version, note}."""
    return json.loads(VALIDATED.read_text(encoding="utf-8"))["maps"]


def map_rows(game_maps: list[str], validated: dict[str, dict]) -> list[tuple[str, str, str, str]]:
    """(map, status, validated with, note) for the game's maps, alphabetically, then the validated
    maps the game no longer has. Status: "validated", "not yet" or "not in the game"."""
    rows = []
    for name in sorted(game_maps, key=str.casefold):
        entry = validated.get(name)
        rows.append((name, "validated" if entry else "not yet", (entry or {}).get("version", ""), (entry or {}).get("note", "")))
    for name in sorted(set(validated) - set(game_maps), key=str.casefold):
        rows.append((name, "not in the game", validated[name].get("version", ""), validated[name].get("note", "")))
    return rows


STATUS_STYLE = {"validated": "[green]✓ validated[/]", "not yet": "[yellow]not yet[/]", "not in the game": "[red]not in the game[/]"}


@map_app.command("list")
def list_maps() -> None:
    """The game's battlegrounds, and which have been validated.

    Validated: the map's render has been reviewed and, where needed, tuned for (validated-maps.json). The rest render with the defaults, unchecked.
    """
    from .game_data import find_install, map_index, open_storage

    with open_storage(find_install()) as storage:
        game_maps = list(map_index(storage))
    rows = map_rows(game_maps, validated_maps())
    table = Table(title="Battlegrounds", title_justify="left")
    table.add_column("Map", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Validated with", no_wrap=True)
    table.add_column("Checked", overflow="fold")
    for name, status, validated_with, note in rows:
        table.add_row(name, STATUS_STYLE[status], validated_with, note)
    console = Console()
    console.print(table)
    done = sum(status == "validated" for _, status, _, _ in rows)
    console.print(f"{done} of {len(game_maps)} maps validated")


# ------------------------------------------------------------------------------------------------
# The steps on their own, and the build's check
# ------------------------------------------------------------------------------------------------


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def prepare(ctx: typer.Context) -> None:
    """Prepare a map: read it, inject the capture script, plan the grid (prints the manifest)."""
    from . import inject

    print(inject.main(list(ctx.args)))


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def capture(ctx: typer.Context) -> None:
    """Capture a prepared map in the running game (heroes-capture capture --help)."""
    run_capture(list(ctx.args))


@app.command(context_settings=PASS_THROUGH, add_help_option=False)
def stitch(ctx: typer.Context) -> None:
    """Stitch a capture's screenshots into the map image, sky layers and viewer."""
    from . import stitch as stitching

    stitching.main(list(ctx.args))


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
