"""Launch a prepared battleground and screenshot every tile of its capture grid.

    python capture.py work/towers-of-doom-structures.json [options]

Moves the camera by typing `tile <n>` into the game's chat (the injected script listens for
it), waits for the frame to settle, and saves the screen as a lossless PNG. Chat is used
rather than a hotkey because it names the tile: a missed keystroke cannot shift every later
screenshot by one.

Captures the game window itself, on whichever monitor it is. Run the game in
"Windowed (Fullscreen)" at the resolution given to inject.mjs, and leave the mouse and keyboard
alone while it captures.
"""

import argparse
import ctypes
import json
import math
import shutil
import subprocess
import tempfile
import sys
import time
from pathlib import Path

import mss
import numpy as np
import pydirectinput
from PIL import Image

from markers import count_labels, describe, fit_markers, fit_numbered, learn_digits

DEFAULT_GAME = r"D:\Games\Heroes of the Storm"

pydirectinput.PAUSE = 0.02  # between synthetic key events

# Real pixel coordinates on every monitor, whatever the Windows display scaling.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    ctypes.windll.user32.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def foreground_is_game() -> bool:
    """Whether the window in front belongs to the game's own program, HeroesOfTheStorm*.exe.

    Checked by process rather than window title: a title check also passed for other windows
    that happened to mention the game, and the keys went there.
    """
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return False
        return Path(buf.value).name.lower().startswith("heroesofthestorm")
    finally:
        kernel32.CloseHandle(handle)


def game_region() -> dict:
    """The game window's drawable area in screen pixels (the foreground window: the game)."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    rect, origin = RECT(), POINT(0, 0)
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return {"left": origin.x, "top": origin.y, "width": rect.right, "height": rect.bottom}


def looks_black(frame: np.ndarray) -> bool:
    """A failed grab, or a frame caught mid-transition: black or a single flat colour."""
    sample = frame[::8, ::8]
    return int(sample.max()) < 12 or float(sample.std()) < 3.0


def same_view(a: np.ndarray, b: np.ndarray) -> bool:
    """The camera didn't move: the chat command was probably dropped."""
    return float(np.abs(a[::8, ::8].astype(np.int16) - b[::8, ::8].astype(np.int16)).mean()) < 0.5


def wait_for_game() -> None:
    """Block until the game window is in front. Nothing is typed anywhere else."""
    if foreground_is_game():
        return
    print("  game window not in front; waiting (click into the game to continue)...", flush=True)
    while not foreground_is_game():
        time.sleep(0.5)
    time.sleep(1.0)


def send_chat(text: str) -> None:
    """Open chat, type, send, checking the game is in front before every key. If it loses focus
    part way, wait for it to come back, clear the half-typed line with Esc, and start again.
    pydirectinput uses scan codes, which the game accepts."""
    keys = ["enter", *("space" if ch == " " else ch for ch in text), "enter"]
    while True:
        wait_for_game()
        for i, key in enumerate(keys):
            if not foreground_is_game():
                break
            pydirectinput.press(key)
            time.sleep(0.15 if i == 0 else 0.02)  # the chat box needs a moment to open
        else:
            return
        print("  focus lost while typing; retrying", flush=True)
        wait_for_game()
        pydirectinput.press("esc")
        time.sleep(0.3)


def quit_match() -> None:
    """Leave the match (the capture script's "quit" command), so the game is back at the menu:
    a running match keeps its map, and the next run's map only loads from the menu."""
    if foreground_is_game():
        send_chat("quit")
        log("left the match (back to the menu for the next run)")


def settle(seconds: float) -> None:
    """Wait for the frame to settle with the game in front the whole time; if it loses focus,
    wait for it to come back and start the wait again."""
    while True:
        wait_for_game()
        end = time.time() + seconds
        while time.time() < end and foreground_is_game():
            time.sleep(0.05)
        if foreground_is_game():
            return


def wait_until_still(grab, checks: int = 3, interval: float = 1.5, limit: float = 180) -> None:
    """Wait for the intro cutscene to end: several frames in a row without the view moving."""
    print("waiting for the view to settle (intro cutscene) ...", flush=True)
    deadline = time.time() + limit
    still, previous = 0, None
    while still < checks and time.time() < deadline:
        wait_for_game()
        frame = grab()
        if frame is not None and previous is not None:
            moved = float(np.abs(frame[::8, ::8].astype(np.int16) - previous[::8, ::8].astype(np.int16)).mean())
            still = still + 1 if moved < 3 else 0
        previous = frame
        time.sleep(interval)
    if still < checks:
        print(f"  still moving after {limit:.0f} seconds; starting anyway")


def magenta_pixels(frame: np.ndarray) -> int:
    """Pixels of the markers' solid magenta (text tags with a (255, 0, 255) background)."""
    r, g, b = frame[::2, ::2, 0], frame[::2, ::2, 1], frame[::2, ::2, 2]
    return int(((r > 170) & (b > 170) & (g < 100)).sum()) * 4


def probe_camera(args, manifest: dict, tiles: list, what: str, values: list) -> None:
    """Step one camera setting over the middle of the map and count marker pixels at each value.
    "zoom": the camera distance. "fov": the field of view, with the distance adjusted so the
    view keeps the planned size (that finds the flattest view that still draws the markers)."""
    if not manifest.get("markers"):
        sys.exit("the map was prepared without --markers; nothing to probe")
    view_h = manifest["screen"]["h"] / manifest["pxPerCell"]
    middle = min(tiles, key=lambda t: abs(t["row"] - manifest["rows"] / 2) + abs(t["col"] - manifest["cols"] / 2))
    print(
        f"\n{what} probe. When the map has loaded (pick any hero if asked), come back here and press\n"
        "Enter, then click into the game window and leave it alone."
    )
    input()
    wait_for_game()
    time.sleep(3)
    results = []
    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
        region = game_region()

        def grab() -> np.ndarray | None:
            for _ in range(10):
                wait_for_game()
                shot = sct.grab(region)
                frame = np.asarray(shot, dtype=np.uint8)[:, :, 2::-1]
                if not looks_black(frame):
                    return frame
                time.sleep(0.4)
            return None

        wait_until_still(grab)
        send_chat(f"tile {middle['index']}")
        settle(2.0)
        for v in values:
            if what == "fov":
                distance = view_h / 2 / math.tan(math.radians(v) / 2)
                send_chat(f"fov {v}")
                settle(0.3)
                send_chat(f"zoom {distance:.1f}")
            else:
                send_chat(f"zoom {v}")
            settle(2.0)
            frame = grab()
            n = magenta_pixels(frame) if frame is not None else 0
            results.append((v, n))
            print(f"  {what} {v:4d}: {n:6d} magenta pixels" + ("" if frame is not None else "  (no usable frame)"), flush=True)
            if frame is not None:
                Image.fromarray(np.ascontiguousarray(frame)).save(
                    args.manifest.parent / f"{what}-probe-{v:03d}.png", compress_level=1
                )
        quit_match()
    visible = [v for v, n in results if n >= 400]
    if not visible:
        summary = "markers never visible"
    elif what == "fov":
        summary = f"markers visible from fov {min(visible)} up" + (f"; not at {max(v for v, n in results if n < 400 and v < min(visible))}" if any(v < min(visible) for v, _ in results) else "")
    else:
        summary = f"markers visible up to distance {max(visible)}"
    report = args.manifest.parent / f"{what}-probe.txt"
    report.write_text("\n".join(f"{v}\t{n}" for v, n in results) + "\n" + summary + "\n")
    print(f"\n{summary}\n(details and screenshots in {args.manifest.parent})")


_LOG_PATH = None


def log(*parts, **kw) -> None:
    """print, and append to the run's log file once it is known."""
    text = " ".join(str(p) for p in parts)
    print(text, **kw)
    if _LOG_PATH is not None:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path, help="the .json written by inject.mjs")
    ap.add_argument("--game", default=DEFAULT_GAME, help=f"Heroes of the Storm folder (default {DEFAULT_GAME})")
    ap.add_argument("--no-launch", action="store_true", help="the map is already running")
    ap.add_argument("--launch-only", action="store_true", help="launch the map and stop (to try chat commands by hand)")
    ap.add_argument("--probe-zoom", action="store_true", help="find the camera distance beyond which the markers stop drawing")
    ap.add_argument("--probe-fov", action="store_true", help="find the narrowest field of view at which the markers still draw")
    ap.add_argument("--settle", type=float, default=1.0, help="seconds to wait after each move (default 1.0)")
    ap.add_argument("--start", type=int, default=0, help="first tile, to resume a run (default 0)")
    ap.add_argument("--monitor", type=int, help="capture this mss monitor number instead of the game window")
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text())
    tiles = manifest["tiles"]
    out = args.manifest.parent / manifest["id"] / "tiles"
    out.mkdir(parents=True, exist_ok=True)
    global _LOG_PATH
    _LOG_PATH = args.manifest.parent / manifest["id"] / "log.txt"
    log(f"capture {manifest['id']}: {len(tiles)} tiles, screen {manifest['screen']['w']}x{manifest['screen']['h']}, fov {manifest.get('fov')}, {manifest['pxPerCell']} px/cell")
    # Marker fits per tile (see markers.py), for stitch.py; kept across a resumed run.
    markers_path = args.manifest.parent / manifest["id"] / "markers.json"
    marker_fits: dict = json.loads(markers_path.read_text()) if args.start and markers_path.exists() else {}

    if not args.no_launch:
        switcher = Path(args.game) / "Support64" / "HeroesSwitcher_x64.exe"
        if not switcher.exists():
            sys.exit(f"not found: {switcher} (pass --game)")
        # The game wants an absolute path, and may not read one on a network share (such as
        # \\wsl.localhost\...), so launch a local copy. Each launch gets a new name: with a
        # reused one the game ran an earlier preparation's map, whose grid didn't match.
        local = Path(tempfile.gettempdir()) / "hrs-map-capture"
        local.mkdir(exist_ok=True)
        for old in local.glob("*.stormmap"):
            try:
                old.unlink()
            except OSError:
                pass  # still open in a running game
        stormmap = str(shutil.copy(manifest["stormmap"], local / f"{manifest['id']}-{int(time.time())}.stormmap"))
        print(f"launching {manifest['map']} ({len(tiles)} tiles) ...")
        subprocess.Popen([str(switcher), stormmap])

    if args.probe_zoom:
        probe_camera(args, manifest, tiles, "zoom", [40, 60, 80, 100, 120, 140, 160, 180, 200, 230, 260, 300])
        return
    if args.probe_fov:
        # Narrow to wide; the distance is adjusted with each so the view stays the same size.
        probe_camera(args, manifest, tiles, "fov", [6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 40])
        return

    if args.launch_only:
        print(
            "\nLaunched. In the game, chat commands: 'tile <n>' moves to a tile, 'zoom <distance>'\n"
            "changes the camera distance, 'zoom 0' goes back to the planned one."
        )
        return

    print(
        "\n(Heroes needs to be running from Battle.net and at the main menu, not in a match: a running\n"
        "match keeps its map. Each run leaves the match at the end by itself.)\n"
        "When the map has loaded (pick any hero if asked), come back here and press Enter,\n"
        "then click into the game window. Capture waits for the intro cutscene to finish, only\n"
        "types while the game is in front, and pauses whenever it isn't. Don't touch anything\n"
        "while it runs."
    )
    input()
    wait_for_game()
    time.sleep(3)

    expected = (manifest["screen"]["w"], manifest["screen"]["h"])
    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:  # mss 10 renamed it
        region = sct.monitors[args.monitor] if args.monitor else game_region()
        log(f"capturing {region['width']}x{region['height']} at ({region['left']}, {region['top']})")
        if (region["width"], region["height"]) != expected:
            print(
                f"warning: that is not the {expected[0]}x{expected[1]} the grid was planned for; "
                "the stitch will still work, at a different scale"
            )
        def grab() -> np.ndarray | None:
            """One frame as RGB, retaken while it comes back black; None if it stays black."""
            tries = 0
            while tries < 10:
                # The grab copies whatever is on screen there, so the game must be in front
                # before and after it; otherwise it may have caught another window.
                wait_for_game()
                shot = sct.grab(region)
                if not foreground_is_game():
                    settle(0.5)
                    continue
                frame = np.asarray(shot, dtype=np.uint8)[:, :, 2::-1]  # BGRA -> RGB
                if not looks_black(frame):
                    return frame
                tries += 1
                time.sleep(0.3)
            return None

        # The first command right away: it hides the interface the intro's exit leaves behind
        # (whose timers would never look still). Then wait for the view to settle, and send it
        # once more so the camera is exactly on the first tile.
        send_chat(f"tile {tiles[args.start]['index']}")
        settle(3.0)
        wait_until_still(grab, limit=45)
        send_chat(f"tile {tiles[args.start]['index']}")
        settle(args.settle)

        # Numbered markers: learn the game font's digits from the "0123456789" reference label.
        digits = None
        if manifest.get("markers"):
            send_chat("glyphs")
            settle(args.settle)
            ref = grab()
            if ref is not None:
                Image.fromarray(np.ascontiguousarray(ref)).save(out.parent / "glyphs.png", compress_level=1)
                digits = learn_digits(ref)
            log("digits learned from the reference label" if digits else
                "could not learn the digits (see glyphs.png); falling back to the marker pattern")

        def calibrate(tile: dict) -> tuple[dict | None, int]:
            """Calibration shot: (fit, labels on screen)."""
            frame = grab()
            if frame is None:
                return None, 0
            if digits:
                fit = fit_numbered(frame, manifest["markers"], digits, tile["index"])
            else:
                fit = fit_markers(frame, manifest["markers"], manifest["screen"], manifest["pxPerCell"])
            return fit, count_labels(frame)

        previous = None
        failures = 0  # consecutive tiles that stayed black
        silent = 0  # consecutive tiles whose calibration shot showed no labels at all
        started = time.time()
        for tile in tiles[args.start :]:
            send_chat(f"tile {tile['index']}")
            settle(args.settle)
            note = ""
            if manifest.get("markers"):
                # Shot 1 of 2, calibration: the numbered markers give the exact camera geometry,
                # and their tile digit proves the view is this tile. One retake if not.
                fit, labels = calibrate(tile)
                if not (fit and fit.get("fit")):
                    send_chat(f"tile {tile['index']}")
                    settle(args.settle)
                    fit, labels = calibrate(tile)
                silent = silent + 1 if labels == 0 else 0
                if silent >= 3:
                    quit_match()
                    sys.exit(
                        "\nStopped: no markers on screen for three tiles in a row. The map's script is\n"
                        "not responding to the chat commands; if every interface panel is visible in the\n"
                        "game, the script failed to compile."
                    )
                if fit and not fit.get("fit"):
                    fit = None  # labels named another tile: a stale frame, no usable fit
                marker_fits[str(tile["index"])] = fit
                markers_path.write_text(json.dumps(marker_fits))
                note = "  " + describe(fit)
                # Shot 2 of 2, clean: markers hidden, camera unmoved. This is the image kept.
                send_chat("clean")
                settle(0.5)
            frame = grab()
            if frame is not None and manifest.get("markers") and count_labels(frame) > 0:
                send_chat("clean")
                settle(0.5)
                frame = grab()
                if frame is not None and count_labels(frame) > 0:
                    note += "  (labels still visible in the clean shot)"
            # Near the edges the game holds the camera back, so neighbouring tiles can really
            # show the same view; keep it either way (the stitch places duplicates on top of each
            # other). Only black frames are failures.
            if frame is not None and previous is not None and same_view(frame, previous):
                note += "  (same view as the previous tile)"

            if frame is None:
                failures += 1
                log(f"    tile {tile['index']} failed (black); not saved")
                if failures >= 3:
                    quit_match()
                    first_bad = tile["index"] - failures + 1
                    sys.exit(
                        f"\nStopped: the game stopped responding at tile {first_bad} (black frames).\n"
                        f"Check what the game shows, then resume from there in a fresh game:\n"
                        f"  py capture.py {args.manifest} --game \"{args.game}\" --start {first_bad}"
                    )
                continue
            failures = 0
            previous = frame
            path = out / f"tile_{tile['index']:04d}.png"
            Image.fromarray(np.ascontiguousarray(frame)).save(path, compress_level=1)
            done = tile["index"] - args.start + 1
            eta = (time.time() - started) / done * (len(tiles) - args.start - done)
            log(f"  tile {tile['index'] + 1}/{len(tiles)}  (row {tile['row']}, col {tile['col']})  ~{eta:.0f}s left{note}")

    quit_match()
    if manifest.get("markers"):
        good = sum(1 for f in marker_fits.values() if f)
        log(f"markers found in {good} of {len(marker_fits)} screenshots")
    print(f"\n{len(tiles) - args.start} screenshots in {out}\nnext: python stitch.py {args.manifest}")


if __name__ == "__main__":
    main()
