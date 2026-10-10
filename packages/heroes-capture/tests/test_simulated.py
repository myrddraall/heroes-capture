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
    # name: (mode, capture args, environment, the outcome, text the run log must show). A fault at
    # FAKE_FAULT_AT (the fake game's seconds) lands during the tiles, after the sky shots (17
    # positions through three lenses): tile 1 at 40.3 s, one every 0.61 s (tile 9 from 45.18).
    "matte": ("matte", [], {}, "finished", ("sky layer images: 2x2 camera positions, near clip 257; 2x2 through 16 degrees; 3x3 through 32 degrees", "sky layer images: 17 positions")),
    "black": ("black", [], {}, "finished", "tile 12/12"),
    "resume": ("matte", ["--no-launch", "--start", "9"], {"FAKE_START": "map"}, "finished", "Test Map: 15 tiles, carrying on at tile 10"),
    "focus": ("matte", [], {"FAKE_FAULT": "focus", "FAKE_FAULT_AT": "46.7"}, "finished", "focus lost during tile"),
    "edges": ("matte", [], {"FAKE_BOUNDS": "22,26,42,38"}, "finished", "more beyond them (ring 1)"),
    "hidden world": ("matte", [], {"FAKE_HIDDEN": "1"}, "finished", "the sky work waits until the map is ready"),
    "wrong map": ("matte", [], {"FAKE_FAULT": "wrongmap"}, f"error RuntimeError: {RELAUNCH} (HRS_RECOVERIES=1)", "another map is running"),
    "silent strip": ("matte", [], {"FAKE_FAULT": "silent", "FAKE_FAULT_AT": "45.12"}, f"error RuntimeError: {RELAUNCH} --start 9", "lost the match"),
    "crash": ("matte", [], {"FAKE_FAULT": "crash", "FAKE_FAULT_AT": "43.5"}, f"error RuntimeError: {RELAUNCH} --start ", "lost the match"),  # carries on at the lost tile
    "game menu": ("matte", [], {"FAKE_FAULT": "menu", "FAKE_FAULT_AT": "41.5"}, "finished", "a game menu is open"),  # waited out, not a lost match
    "box focus": ("matte", [], {"FAKE_FAULT": "boxfocus", "FAKE_FAULT_AT": "41.5"}, "finished", ("giving the command box the keyboard back", "tile 12/12")),
    "broken script": ("matte", [], {"FAKE_FAULT": "broken"}, "error ScriptBroken: the map's script failed to compile", ""),  # stops at once, no relaunch
    # Other resolutions: the strip's cells come out at the game's interface scale (fakegame CELL_W),
    # the capture measures them and blanks a column to fit.
    "1080p": ("matte", [], {"FAKE_SCREEN": "1920x1080"}, "finished", ("status strip: 22x14 px cells at (0, 0)", "48 px blanked at the left edge")),
    "2160p": ("matte", [], {"FAKE_SCREEN": "3840x2160"}, "finished", ("status strip: 45x27 px cells at (0, 0)", "94 px blanked at the left edge")),
    # The game put on a monitor before the launch (the fake desktop has one).
    "monitor": ("matte", ["--monitor", "1"], {}, "finished", ("monitors: 1 \\\\.\\DISPLAY1 3440x1440 at (0, 0) primary", "the game is on monitor 1 \\\\.\\DISPLAY1")),
    "probe sky": ("matte", ["--probe-sky"], {}, "finished", "05-none-layer0"),
    "probe depth": ("matte", ["--probe-depth"], {}, "finished", "sky layers: parallax rate"),
    "launch only": ("matte", ["--launch-only"], {}, "finished", ""),
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name, tmp_path):
    mode, args, env, outcome, shown = SCENARIOS[name]
    run = simulate(tmp_path, mode, *args, **env)
    assert run["outcome"].startswith(outcome), run["outcome"]
    for text in (shown,) if isinstance(shown, str) else shown:
        assert text in run["log"], run["log"][-3000:]


def test_stitch_of_a_simulated_render(tmp_path):
    run = simulate(tmp_path, "matte")
    assert run["outcome"] == "finished"
    with sim_slot():
        result = subprocess.run([sys.executable, "-m", "heroes_capture", "stitch", "test-map.json"], cwd=tmp_path,
                                capture_output=True, text=True, env={"PYTHONPATH": str(PACKAGE / "src")})
    assert result.returncode == 0, result.stderr[-3000:]
    out = tmp_path / "maps" / "test-map"  # <output-dir>/<map id>, from the map's name "Test Map"
    for name in ("pack/pack.json", "pack/map.pmtiles", "pack/map-overview.webp", "pack/background.pmtiles", "pack/haze.pmtiles", "pack/fixed.webp",
                 "pack/thumbnail.webp", "pack/index.html", "raw/map.png", "raw/composite.png", "raw/layers.json"):
        assert (out / name).exists(), name
    assert sorted(p.name for p in out.iterdir()) == ["pack", "raw"]
    description = json.loads((out / "pack" / "pack.json").read_text())
    assert [l["id"] for l in description["layers"]] == ["fixed", "background", "haze", "map"]
