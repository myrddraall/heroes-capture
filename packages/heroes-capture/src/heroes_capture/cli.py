"""heroes-capture: captures Heroes of the Storm assets from the game client.

    heroes-capture map "Battlefield of Eternity"        prepare, capture and stitch a battleground
    heroes-capture map "Cursed Hollow" --structures hide   bare terrain
    heroes-capture --version

`map` takes the map as the game names it (or a path to a .stormmap); other options go to the
preparing step (see `heroes-capture prepare --help`). The steps on their own: `prepare`,
`capture`, `stitch`. Start Heroes from the Battle.net app first, in Windowed (Fullscreen).
"""

import argparse
import shutil
import sys
from pathlib import Path

PROBES = ("--probe-light", "--probe-sky", "--probe-depth", "--probe-waits")
DISTANCE = "214"  # camera distance: far, so tall objects lean little at the seams
KEEP = "0.4"  # share of each screenshot used, centred


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


def map_command(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="heroes-capture map", description="Prepare, capture and stitch a battleground.")
    ap.add_argument("map", help="the map as the game names it, or a path to a .stormmap")
    ap.add_argument("--structures", choices=("keep", "hide"), default="keep", help="keep or hide forts, towers, cores and gates")
    ap.add_argument("--game", help=argparse.SUPPRESS)  # the install, when it isn't found by itself
    for probe in PROBES:
        ap.add_argument(probe, action="store_true", help=argparse.SUPPRESS)  # diagnostics instead of the tiles
    ap.add_argument("--show-ui", action="store_true", help=argparse.SUPPRESS)  # diagnostic: launch with the HUD up and stop
    args, prepare_options = ap.parse_known_args(argv)
    from . import inject, stitch

    prepare = [args.map, "--structures", args.structures, *prepare_options]
    if "--screen" not in prepare_options and screen_size():
        prepare += ["--screen", screen_size()]
    if not {"--distance", "--fov"} & set(prepare_options):
        prepare += ["--distance", DISTANCE]
    if "--keep" not in prepare_options:
        prepare += ["--keep", KEEP]
    if args.show_ui:
        prepare.append("--show-ui")
    print(f"\n[1/3] Preparing {args.map} (structures: {args.structures})")
    manifest = str(inject.main(prepare))
    capture_options = ["--game", args.game] if args.game else []
    probe = next((p for p in PROBES if getattr(args, p[2:].replace("-", "_"))), None)
    if probe:
        print(f"\nProbe {probe}")
        run_capture([manifest, probe, *capture_options])
        return
    if args.show_ui:
        print("\nDiagnostic run: launching the map and stopping here.")
        run_capture([manifest, "--launch-only", *capture_options])
        return
    shutil.rmtree(Path(manifest).with_suffix("") / "tiles", ignore_errors=True)  # old screenshots would mix in
    print("\n[2/3] Capturing")
    run_capture([manifest, *capture_options])
    print("\n[3/3] Stitching")
    stitch.main([manifest, "--tiles"])
    print(f"\nDone. The image is next to {manifest}")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["--version"], ["-V"]):
        print(version())
        return
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    command, rest = argv[0], argv[1:]
    if command == "map":
        map_command(rest)
    elif command == "prepare":
        from . import inject

        print(inject.main(rest))
    elif command == "capture":
        run_capture(rest)
    elif command == "stitch":
        from . import stitch

        stitch.main(rest)
    else:
        raise SystemExit(f"unknown command {command}; see heroes-capture --help")
