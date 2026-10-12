"""The capture end to end against the simulated game and desktop (tests/sim/fakegame.py): a full
render in both void modes, a resumed run, the faults the capture recovers from, the probes; and
the stitch of a simulated render."""

import json
import subprocess
import sys

import pytest

from conftest import PACKAGE, sim_slot, simulate

RELAUNCH = "relaunch attempted: -m heroes_capture capture test-map.json --game game"

SCENARIOS = {
    # name: (mode, capture args, environment, the outcome, text the run log must show). The screen
    # is 1920x1080 unless FAKE_SCREEN says otherwise (the map planned for the same screen, as a real
    # one is). A fault at FAKE_FAULT_AT (the fake game's seconds) lands during the tiles, after the
    # sky shots (17 positions through three lenses): tile 1 at 40.3 s, one every 0.56-0.61 s (tile
    # 9 from 44.98).
    "matte": ("matte", [], {}, "finished", ("sky layer images: 2x2 camera positions, near clip 257; 2x2 through 16 degrees; 3x3 through 32 degrees", "sky layer images: 17 positions")),
    "black": ("black", [], {}, "finished", "tile 74/74"),  # the black void's terrain runs on: three rings past the grid
    "resume": ("matte", ["--no-launch", "--start", "9"], {"FAKE_START": "map"}, "finished", "Test Map: 24 tiles, carrying on at tile 10"),
    "focus": ("matte", [], {"FAKE_FAULT": "focus", "FAKE_FAULT_AT": "46.7"}, "finished", "focus lost during tile"),
    "edges": ("matte", [], {"FAKE_BOUNDS": "22,26,42,38"}, "finished", "more beyond them (ring 1)"),
    "hidden world": ("matte", [], {"FAKE_HIDDEN": "1"}, "finished", "the sky work waits until the map is ready"),
    "wrong map": ("matte", [], {"FAKE_FAULT": "wrongmap"}, f"error RuntimeError: {RELAUNCH} (HRS_RECOVERIES=1)", "another map is running"),
    "silent strip": ("matte", [], {"FAKE_FAULT": "silent", "FAKE_FAULT_AT": "45.12"}, f"error RuntimeError: {RELAUNCH} --start 10", "lost the match"),
    "crash": ("matte", [], {"FAKE_FAULT": "crash", "FAKE_FAULT_AT": "43.5"}, f"error RuntimeError: {RELAUNCH} --start ", "lost the match"),  # carries on at the lost tile
    "game menu": ("matte", [], {"FAKE_FAULT": "menu", "FAKE_FAULT_AT": "41.5"}, "finished", "a game menu is open"),  # waited out, not a lost match
    "box focus": ("matte", [], {"FAKE_FAULT": "boxfocus", "FAKE_FAULT_AT": "41.5"}, "finished", ("giving the command box the keyboard back", "tile 28/28")),
    "broken script": ("matte", [], {"FAKE_FAULT": "broken"}, "error ScriptBroken: the map's script failed to compile", ""),  # stops at once, no relaunch
    # Other screens, each for what it takes a different way: the strip's cells come out at the
    # game's interface scale (fakegame CELL_W), the capture measures them and blanks a column to
    # fit. Ultrawide: more of the map across, fewer tiles. Narrow (5:4): the interface scaled to the
    # width, not the height, and the main menu's narrow layout. 4K: everything bigger.
    "ultrawide": ("matte", [], {"FAKE_SCREEN": "3440x1440"}, "finished", ("15 tiles, screen 3440x1440", "status strip: 30x18 px cells at (0, 0)", "64 px blanked at the left edge")),
    "narrow": ("matte", [], {"FAKE_SCREEN": "1280x1024"}, "finished", ("42 tiles, screen 1280x1024", "status strip: 20x12 px cells at (0, 0)", "44 px blanked at the left edge")),
    "2160p": ("matte", [], {"FAKE_SCREEN": "3840x2160"}, "finished", ("status strip: 45x27 px cells at (0, 0)", "94 px blanked at the left edge")),
    # The game put on a monitor before the launch (the fake desktop has one).
    "monitor": ("matte", ["--monitor", "1"], {}, "finished", ("monitors: 1 \\\\.\\DISPLAY1 1920x1080 at (0, 0) primary", "the game is on monitor 1 \\\\.\\DISPLAY1")),
    "probe sky": ("matte", ["--probe-sky"], {}, "finished", "03-none-layer0"),
    "probe monitor": ("matte", ["--probe-monitor", "--monitor", "1"], {}, "finished", ("the game's window is 1920x1080 at (0, 0), monitor 1", "monitor probe at 1920x1080: 6 of 6 checks ok")),
    "launch only": ("matte", ["--launch-only"], {}, "finished", ""),
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name, tmp_path):
    mode, args, env, outcome, shown = SCENARIOS[name]
    run = simulate(tmp_path, mode, *args, **env)
    assert run["outcome"].startswith(outcome), run["outcome"]
    for text in (shown,) if isinstance(shown, str) else shown:
        assert text in run["log"], run["log"][-3000:]


def test_tiles_with_team_coloured_doodads_are_also_shot_from_chaos_s_side(tmp_path):
    """A tile with a team-coloured doodad in view (manifest teamColoured) is shot from both sides:
    its shots from Chaos's side next to Order's (tile_<n>-chaos, -chaos-black), each taken once the
    switch is drawn; a tile with none is shot once, as before. Order's shots don't change."""
    import numpy as np

    run = simulate(tmp_path, "matte", FAKE_TEAM="1")
    assert run["outcome"] == "finished", run["outcome"]
    assert "(and from Chaos's side)" in run["log"], run["log"][-3000:]
    tiles = tmp_path / "test-map" / "tiles"
    chaos = sorted(p.name for p in tiles.glob("tile_*-chaos.npy"))
    assert chaos and len(chaos) < len(list(tiles.glob("tile_????.npy")))
    assert len(list(tiles.glob("tile_*-chaos-black.npy"))) == len(chaos)
    # The discs: blue from Order's side (both shots), red from Chaos's (both shots).
    blue, red = np.array([40, 90, 255]), np.array([255, 60, 40])
    for name in chaos:
        index = name.removesuffix("-chaos.npy")
        shots = {k: np.load(tiles / f"{index}{k}.npy") for k in ("", "-black", "-chaos", "-chaos-black")}
        disc = (np.abs(shots[""].astype(int) - blue).max(axis=2) < 4)
        if disc.sum() < 100:
            continue  # the disc in the margin past the view: nothing of it on screen
        for k, colour in (("", blue), ("-black", blue), ("-chaos", red), ("-chaos-black", red)):
            assert (np.abs(shots[k][disc].astype(int) - colour).max(axis=1) < 4).mean() > 0.99, (name, k)
        break
    else:
        raise AssertionError("no tile shows a disc")


def test_stitch_of_a_simulated_render(tmp_path):
    run = simulate(tmp_path, "matte")
    assert run["outcome"] == "finished"
    with sim_slot():
        result = subprocess.run([sys.executable, "-m", "heroes_capture", "stitch", "test-map.json", "--composites"], cwd=tmp_path,
                                capture_output=True, text=True, env={"PYTHONPATH": str(PACKAGE / "src")})
    assert result.returncode == 0, result.stderr[-3000:]
    out = tmp_path / "maps" / "test-map"  # <output-dir>/<map id>, from the map's name "Test Map"
    for name in ("pack/pack.json", "pack/map.pmtiles", "pack/map-overview.webp", "pack/background.pmtiles", "pack/haze.pmtiles", "pack/fixed.webp",
                 "pack/thumbnail.webp", "raw/index.html", "raw/map.png", "raw/composite.png", "raw/layers.json"):
        assert (out / name).exists(), name
    assert sorted(p.name for p in out.iterdir()) == ["pack", "raw"]
    description = json.loads((out / "pack" / "pack.json").read_text())
    assert [l["id"] for l in description["layers"]] == ["fixed", "background", "haze", "map"]
