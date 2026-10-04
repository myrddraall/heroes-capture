"""Diagnostic runs (capture.py --probe-light, --probe-sky, --probe-waits, --probe-input): instead of the tiles, chosen views and
command sequences, a shot after each, kept in a probe-<what>-<time> folder next to the tiles.
"""

import json
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import game_control
from . import sky_layers
from .game_control import quit_match, send_chat, settle, step
from .game_window import alt_tab, bring_game_to_front, foreground_is_game, type_burst, type_unicode
from .runlog import done, log, warn
from .screen import changed_share


def _save(frame: np.ndarray, path: Path) -> None:
    Image.fromarray(np.ascontiguousarray(frame)).save(path, compress_level=1)


def probe_light(session, manifest: dict, out: Path) -> None:
    """The render's own commands at chosen spots, to see what each does to the picture.

    HRS_PROBE_POINTS="x,y;x,y" (map cells; default the middle tile) and HRS_PROBE_TILE_PATH,
    entries "cmd,cmd,...:settle" separated by ";": the commands in order, each acknowledged
    through the strip ("tile" and "move" get the point appended; "tile@x:y" / "move@x:y" that
    spot instead, to arrive the way the render does from a neighbouring tile; "black",
    "sky white" etc. as they are), then `clean`, the wait, and two shots 0.5 s apart (how much
    animates between them is logged; HRS_PROBE_GAP seconds apart instead). An entry without an
    "@" spot first jumps 100 cells away, so the point is arrived at afresh. An entry may start
    with "name=", which names its shots. Default "tile:0.5"."""
    tiles = manifest["tiles"]
    probe_dir = out.parent / f"probe-light-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(exist_ok=True)

    gap = float(os.environ.get("HRS_PROBE_GAP", "0.5"))

    def shot(name: str) -> None:
        frame = session.grab(dark_ok=True)
        if frame is not None:
            _save(frame, probe_dir / f"{name}.png")
            settle(gap)
            again = session.grab(dark_ok=True)
            if again is not None:
                _save(again, probe_dir / f"{name}-b.png")
                moving = float((np.abs(frame.astype(np.int16) - again.astype(np.int16)).max(axis=2) > 24).mean())
                st = session.status() if session.strip.located else None
                clock = f", game clock {st.game_seconds} s" if st else ""
                log(f"  probe: {name}  moving between two shots {gap:g} s apart: {moving * 100:.2f}% of pixels{clock}")
                return
        log(f"  probe: {name} (no frame)")

    def run() -> None:
        points = [tuple(float(v) for v in p.split(",")) for p in os.environ.get("HRS_PROBE_POINTS", "").split(";") if p.strip()]
        if not points:
            middle = min(tiles, key=lambda t: abs(t["row"] - manifest["rows"] // 2) + abs(t["col"] - manifest["cols"] // 2))
            points = [(middle["x"], middle["y"])]
        send_chat("clean")  # no labels in these shots
        settle(0.5)
        for n, (x, y) in enumerate(points, start=1):
            send_chat(f"look {x:.1f} {y:.1f}")
            settle(1.0)
            for k, entry in enumerate(os.environ.get("HRS_PROBE_TILE_PATH", "tile:0.5").split(";")):
                cmds, wait = entry.rsplit(":", 1)
                given, _, cmds = cmds.rpartition("=")
                if "@" not in cmds:
                    send_chat(f"look {x + 100:.1f} {y:.1f}")  # arrive afresh
                    settle(1.0)
                acked = True
                for cmd in cmds.split(","):
                    if cmd.startswith(("tile@", "move@")):
                        name, spot = cmd.split("@")
                        full = f"{name} 0 {spot.replace(':', ' ')}"
                    elif cmd in ("tile", "move"):
                        full = f"{cmd} 0 {x:.2f} {y:.2f}"
                    else:
                        full = cmd
                    acked = session.send(full, timeout=3.0) is not None and acked
                if float(wait) > 5:
                    log(f"  waiting {float(wait):g} s of match time before the next shot ...")
                settle(float(wait))
                session.send("clean")
                label = given or (cmds.replace(",", "+").replace(" ", "-").replace(":", "_") if len(cmds) < 40 else f"{cmds.count(',') + 1}cmds-{cmds.split(',')[0].replace(' ', '')}")
                shot(f"point{n}-{k:02d}-{label}-s{wait}" + ("" if acked else "-noack"))

    step(run, "the lighting probe")
    quit_match()
    done(f"probe screenshots in {probe_dir}")


def probe_sky(session, manifest: dict, out: Path) -> None:
    """Do the solid-colour skyboxes show, and are they flat? An edge tile (left edge, middle
    row: half map, half sky) over each colour in turn; the sky part of each shot is measured
    (mean colour and spread) and the shots are kept. HRS_SKY_SEQUENCE scripts the swaps:
    "command:wait|..." with commands "<colour> <layer>" (layer 0 is the camera-fixed skybox,
    1 the terrain-relative parallax layer); the same command twice in a row only waits longer,
    which tells a slow transition from a failed swap."""
    tiles = manifest["tiles"]
    probe_dir = out.parent / f"probe-sky-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(exist_ok=True)
    edge = min(tiles, key=lambda t: t["col"] * 1000 + abs(t["y"] - (manifest["area"]["top"] + manifest["area"]["bottom"]) / 2))

    def shot(name: str) -> None:
        frame = session.grab(dark_ok=True)
        if frame is None:
            log(f"  {name}: no frame")
            return
        _save(frame, probe_dir / f"{name}.png")
        # The left fifth (sky, if the tile sits on the edge) and a patch of open sky further in.
        h, w = frame.shape[:2]
        parts = []
        for what, part in (("left fifth", frame[:, : w // 5]), ("sky patch", frame[: h // 3, w // 4 : w // 4 + w // 8])):
            px = part.reshape(-1, 3).astype(np.float32)
            parts.append(f"{what} {tuple(int(v) for v in px.mean(axis=0))} ±{tuple(round(float(v), 1) for v in px.std(axis=0))}")
        log(f"  {name}: " + "; ".join(parts))

    def run() -> None:
        send_chat(f"tile {edge['index']} {edge['x']:.2f} {edge['y']:.2f}")
        settle(2.0)
        send_chat("clean")
        settle(0.5)
        sequence = os.environ.get("HRS_SKY_SEQUENCE") or "start:0|black 0:2.5|white 0:2.5|magenta 0:2.5|lime 0:2.5|none 0:2.5"
        previous = None
        for k, item in enumerate(sequence.split("|")):
            command, wait = item.rsplit(":", 1)
            if command not in ("start", previous):
                send_chat(f"sky {command}")
            settle(float(wait))
            shot(f"{k:02d}-{command.replace(' ', '-layer')}-{wait}s")
            previous = command

    step(run, "the skybox probe")
    quit_match()
    done(f"probe screenshots in {probe_dir}")


def _difference(a: np.ndarray, b: np.ndarray, left: int) -> str:
    """How two shots of the same view differ: the share of 32-pixel blocks that changed (as the
    capture's half-drawn check measures), the mean difference, and the 99.9th percentile."""
    a, b = a[:, left:], b[:, left:]
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return f"blocks changed {changed_share(a, b) * 100:5.2f}%, mean {d.mean():5.2f}, p99.9 {np.percentile(d, 99.9):5.1f}"


def probe_waits(session, manifest: dict, out: Path) -> None:
    """Can the fixed waits be shorter? Each is tried shorter on the same views and the shot
    compared with one at the current value (and the current value is shot twice, to show how
    much two identical shots differ anyway):

    - the lighting-refit look held before each tile (0.1 s in the script), and the settle from
      the tile's acknowledgement to the kept shot (0.1 s): on sample tiles across the grid
      (corners, edges, the middle), each arrived at from its neighbour, as in a render
    - the time a sky swap gets to be drawn before its shot (0.1 s): the haze over white, swapped
      to from the background art, at one sky position
    - and the sky pass with sky positions further apart (0.8 of a screen instead of 0.6) into
      sky-keep08/, to compare its layers with the current ones."""
    tiles = manifest["tiles"]
    left = int((manifest.get("status") or {}).get("pageLeft", 0))
    probe_dir = out.parent / f"probe-waits-{time.strftime('%H%M%S')}"
    probe_dir.mkdir(exist_ok=True)
    by_pos = {(t["row"], t["col"]): t for t in tiles}
    rows, cols = max(t["row"] for t in tiles), max(t["col"] for t in tiles)
    picks = [(0, 0), (0, cols // 2), (rows // 2, 0), (rows // 2, cols // 2), (rows, cols), (rows // 3, 2 * cols // 3)]
    samples = []
    for rc in picks:
        t = by_pos.get(rc)
        if t is not None and t not in samples:
            samples.append(t)

    def save(frame: np.ndarray, name: str) -> None:
        Image.fromarray(np.ascontiguousarray(frame)).save(probe_dir / f"{name}.png", compress_level=1)

    def tile_shot(t: dict, refit: float, settle_time: float) -> np.ndarray | None:
        """The tile as a render shoots it, arrived at from its neighbour (left, else right)."""
        neighbour = by_pos.get((t["row"], t["col"] - 1)) or by_pos.get((t["row"], t["col"] + 1)) or t
        if session.send(f"refitwait {refit:g}") is None:
            return None
        if session.send(f"tile {neighbour['index']} {neighbour['x']:.2f} {neighbour['y']:.2f}", timeout=3.0) is None:
            return None
        settle(0.5)
        answer = session.send(f"tile {t['index']} {t['x']:.2f} {t['y']:.2f}", timeout=3.0)
        if answer is None:
            return None
        moved = time.time()
        wait = moved + settle_time - time.time()
        if wait > 0:
            settle(wait)
        raw = session.fresh()
        return None if raw is None else session.blank(raw)

    conditions = [("current", 0.1, 0.1), ("current-again", 0.1, 0.1), ("refit-0.05", 0.05, 0.1),
                  ("settle-0.05", 0.1, 0.05), ("both-short", 0.05, 0.05)]
    log(f"waits probe: {len(samples)} sample tiles, {len(conditions)} shots each")
    for t in samples:
        def run() -> None:
            base = None
            for name, refit, settle_time in conditions:
                frame = tile_shot(t, refit, settle_time)
                if frame is None:
                    log(f"  tile {t['index'] + 1} {name}: no shot")
                    continue
                save(frame, f"tile{t['index']:03d}-{name}")
                if base is None:
                    base = frame
                    continue
                log(f"  tile {t['index'] + 1} (row {t['row']}, col {t['col']}) {name:14s} vs current: {_difference(frame, base, left)}")

        step(run, f"waits probe, tile {t['index'] + 1}")
    session.send("refitwait 0.1")

    # The chat box: how long it needs to open before the text is typed. Each wait, 30 commands
    # sent once each: how many the map took, and how long a command took on average.
    def chat_run() -> None:
        current = game_control.CHAT_OPEN_WAIT
        try:
            for wait in (0.06, 0.04, 0.03, 0.02):
                game_control.CHAT_OPEN_WAIT = wait
                taken, started = 0, time.time()
                for _ in range(30):
                    taken += session.send("clean", timeout=1.0, sends=1) is not None
                log(f"  chat box wait {wait:.2f} s: {taken}/30 commands taken first time, {(time.time() - started) / 30:.2f} s per command")
                if taken < 30:
                    break  # shorter still would lose more (and letters typed before the box opens reach the game as hotkeys)
        finally:
            game_control.CHAT_OPEN_WAIT = current

    step(chat_run, "waits probe, chat box")

    sky = manifest.get("sky") or {}
    measured = None
    if sky.get("keys") and (sky.get("mapSky") or {}).get("parallax"):
        measured = step(lambda: sky_layers.measure(session, manifest, out.parent), "measuring the sky layers")
    if measured:
        area = manifest["area"]
        x, y = (area["left"] + area["right"]) / 2, (area["bottom"] + area["top"]) / 2
        clip = measured["nearClip"]

        def sky_run() -> None:
            base = None
            for name, wait in (("current", 0.1), ("current-again", 0.1), ("settle-0.05", 0.05), ("settle-0", 0.0)):
                for command in (f"tile 0 {x:.2f} {y:.2f}", f"hidemap {clip}", "sky parallaxbare 1"):
                    if session.send(command, timeout=3.0) is None:
                        warn("sky: no answer")
                        return
                settle(1.0)
                if session.send("sky parallaxwhite 1") is None:
                    return
                settle(wait)
                frame = session.grab(dark_ok=True)
                save(frame, f"sky-{name}")
                if base is None:
                    base = frame
                    continue
                log(f"  sky swap {name:14s} vs current: {_difference(frame, base, left)}")

        step(sky_run, "waits probe, sky swap")
        session.send("sky mapparallax 1")
        sky_layers.capture(session, manifest, out.parent, measured, keep=0.8, folder_name="sky-keep08")
    quit_match()
    done(f"probe screenshots in {probe_dir}")


def probe_input(session, manifest: dict, out: Path) -> None:
    """Ways to send the map its commands that a chat box knocked open or shut can't upset: the
    map's own edit box (capture_script.galaxy's input probe), typed into as key presses or as
    Unicode text, read every 1/16 s or on its dialog events; whether Unicode text reaches the game
    as key presses (where a stray one would be a hotkey); whether the box keeps the keyboard after
    an alt-tab. Each way is timed (ms from typing to the strip's answer) against the chat. The
    findings are logged and written to probe-input/input-probe.json."""
    probe_dir = out.parent / "probe-input"
    probe_dir.mkdir(exist_ok=True)
    rounds = 8
    results: dict = {}

    def next_seq() -> int:
        session._seq = session._seq % 255 + 1
        return session._seq

    def answered(seq: int, timeout: float = 2.0) -> bool:
        return step(lambda: session.wait_for(lambda st: st.seq == seq, timeout), "waiting for the answer") is not None

    def keys_of(text: str) -> list[str]:
        return ["space" if ch == " " else ch for ch in text]

    def strip() -> dict:
        status = step(session.status, "reading the strip")
        return {"keys": status.opening_cuts, "events": status.cleared_since_ready} if status else {"keys": None, "events": None}

    def timed(name: str, send) -> int:
        """`rounds` pings sent with send(text); how many were answered (the times in results)."""
        times = []
        for _ in range(rounds):
            seq = next_seq()
            started = time.time()
            send(f"ping {seq}.")
            ok = answered(seq)
            times.append(round((time.time() - started) * 1000) if ok else None)
            settle(0.1)
        got = sorted(t for t in times if t is not None)
        results[name] = {"answered": len(got), "of": rounds, "ms": times,
                         "median": got[len(got) // 2] if got else None, "max": got[-1] if got else None}
        log(f"  {name}: {len(got)} of {rounds} answered" + (f", median {got[len(got) // 2]} ms, max {got[-1]} ms" if got else ""))
        return len(got)

    def in_box(text: str, unicode: bool = False) -> bool:
        """One command typed into the box; whether the strip answered."""
        seq = next_seq()
        (type_unicode if unicode else lambda t: type_burst(keys_of(t)))(f"{text} {seq}.")
        return answered(seq)

    log("input probe: the chat, for comparison")
    times = []
    for _ in range(rounds):
        started = time.time()
        ok = step(lambda: session.send("refitwait 0.1", timeout=2.0, sends=1), "a chat command") is not None
        times.append(round((time.time() - started) * 1000) if ok else None)
        settle(0.1)
    got = sorted(t for t in times if t is not None)
    results["chat"] = {"answered": len(got), "of": rounds, "ms": times, "median": got[len(got) // 2] if got else None, "max": got[-1] if got else None}
    log(f"  chat: {len(got)} of {rounds} answered" + (f", median {got[len(got) // 2]} ms, max {got[-1]} ms" if got else ""))

    log("input probe: does input reach the game as a key press? (the backslash key, counted by the map)")
    step(lambda: session.send("inputkeys"), "counting the backslash key")
    before = strip()["keys"]
    type_burst(["\\"])
    settle(0.5)
    pressed = strip()["keys"]
    type_unicode("\\")
    settle(0.5)
    typed = strip()["keys"]
    counted = lambda a, b: None if a is None or b is None else (b - a) % 16  # noqa: E731
    results["keys"] = {"key press counted": counted(before, pressed), "unicode counted": counted(pressed, typed)}
    log(f"  a key press: counted {counted(before, pressed)} time(s); Unicode text: counted {counted(pressed, typed)} time(s)"
        " (0: Unicode text never acts as a hotkey)")

    log("input probe: the map's edit box, read every 1/16 s")
    made = step(lambda: session.send("inputbox 1 0"), "making the edit box") is not None
    frame = step(session.raw_grab, "a shot of the box")
    Image.fromarray(np.ascontiguousarray(frame[:, : frame.shape[1] // 8])).save(probe_dir / "box.png")
    results["box made"] = made
    typed_ok = timed("box, key presses, read every 1/16 s", lambda t: type_burst(keys_of(t))) if made else 0
    unicode_ok = timed("box, Unicode text, read every 1/16 s", type_unicode) if made else 0
    if typed_ok or unicode_ok:
        before = strip()["keys"]
        type_burst(["\\"])
        settle(0.5)
        results["keys"]["key press counted with the box focused"] = counted(before, strip()["keys"])
        log(f"  a key press while the box has the keyboard: counted {results['keys']['key press counted with the box focused']} time(s) by the game")
        # Commands outside the timings go as Unicode text when that reaches the box and the game
        # doesn't take it as key presses: one that misses the box then sets nothing off.
        unicode = bool(unicode_ok) and (results["keys"]["unicode counted"] == 0 or not typed_ok)

        def set_mode(mode: int, refocus: int) -> bool:
            """Through the box; if it has lost the keyboard, through the chat, which gives it back."""
            return in_box(f"mode {mode} {refocus}", unicode) or step(lambda: session.send(f"inputbox {mode} {refocus}"), "the edit box's mode") is not None

        log("input probe: after an alt-tab, without and with the map asking for the keyboard again")
        for refocus in (0, 1):
            set_mode(1, refocus)
            alt_tab()
            time.sleep(1.5)
            away = not foreground_is_game()
            bring_game_to_front()
            time.sleep(1.0)
            game_control.wait_for_game()
            ok = in_box("ping", unicode)
            results[f"after alt-tab, refocus {refocus}"] = {"switched away": away, "answered": ok}
            log(f"  refocus {refocus}: {'switched away' if away else 'the alt-tab did not switch away'}; the box {'took' if ok else 'did not take'} the next command")

        log("input probe: the box read on its dialog events instead")
        results["event types seen while polling"] = [k for k in range(8) if (strip()["events"] or 0) >> k & 1]
        if set_mode(2, 0):
            send = type_unicode if unicode else (lambda t: type_burst(keys_of(t)))
            timed("box, " + ("Unicode text" if unicode else "key presses") + ", on its events", send)
        else:
            log("  couldn't switch the box to its events")
        results["event types seen"] = [k for k in range(8) if (strip()["events"] or 0) >> k & 1]
        log(f"  dialog event types that fired on the box: {results['event types seen'] or 'none'}")
    else:
        log("  the box took no input; the alt-tab and event tests are skipped")
    # The box goes before the quit: while it has the keyboard, Enter doesn't open the chat box, and
    # a "quit" sent as chat lands in the box instead.
    if made and not in_box("close", unicode):
        warn("the edit box didn't take \"close\"; leaving may need doing by hand (Esc, Quit)")
    quit_match()
    (probe_dir / "input-probe.json").write_text(json.dumps(results, indent=2))
    done(f"input probe findings in {probe_dir / 'input-probe.json'}")

