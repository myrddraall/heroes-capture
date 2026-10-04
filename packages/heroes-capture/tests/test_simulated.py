"""The capture end to end against the simulated game and desktop (tests/sim/fakegame.py): a full
render in both void modes, a resumed run, the faults the capture recovers from, the probes; and
the stitch of a simulated render."""

import json
import subprocess
import sys

import pytest

from conftest import PACKAGE, simulate

RELAUNCH = "relaunch attempted: -m heroes_capture capture test-map.json --game game"

SCENARIOS = {
    # name: (mode, capture args, environment, the outcome, text the run log must show)
    "matte": ("matte", [], {}, "finished", "sky layer images: 4 positions"),
    "black": ("black", [], {}, "finished", "tile 12/12"),
    "resume": ("matte", ["--no-launch", "--start", "9"], {"FAKE_START": "map"}, "finished", "tile 12/12"),
    "focus": ("matte", [], {"FAKE_FAULT": "focus", "FAKE_FAULT_AT": "30.5"}, "finished", "focus lost during tile"),
    "edges": ("matte", [], {"FAKE_BOUNDS": "22,26,42,38"}, "finished", "more beyond them (ring 1)"),
    "hidden world": ("matte", [], {"FAKE_HIDDEN": "1"}, "finished", "the sky work waits until the map is ready"),
    "wrong map": ("matte", [], {"FAKE_FAULT": "wrongmap"}, f"error RuntimeError: {RELAUNCH} (HRS_RECOVERIES=1)", "another map is running"),
    "silent strip": ("matte", [], {"FAKE_FAULT": "silent", "FAKE_FAULT_AT": "35"}, f"error RuntimeError: {RELAUNCH} --start 9", "lost the match"),
    "crash": ("matte", [], {"FAKE_FAULT": "crash", "FAKE_FAULT_AT": "36"}, f"error RuntimeError: {RELAUNCH}", "lost the match"),
    "probe sky": ("matte", ["--probe-sky"], {}, "finished", "05-none-layer0"),
    "probe depth": ("matte", ["--probe-depth"], {}, "finished", "sky layers: parallax rate"),
    "launch only": ("matte", ["--launch-only"], {}, "finished", ""),
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name, tmp_path):
    mode, args, env, outcome, shown = SCENARIOS[name]
    run = simulate(tmp_path, mode, *args, **env)
    assert run["outcome"].startswith(outcome), run["outcome"]
    assert shown in run["log"], run["log"][-3000:]


def test_stitch_of_a_simulated_render(tmp_path):
    run = simulate(tmp_path, "matte")
    assert run["outcome"] == "finished"
    result = subprocess.run([sys.executable, "-m", "heroes_capture", "stitch", "test-map.json", "--tiles"], cwd=tmp_path,
                            capture_output=True, text=True, env={"PYTHONPATH": str(PACKAGE / "src")})
    assert result.returncode == 0, result.stderr[-3000:]
    for name in ("test-map.png", "test-map.geo.json", "test-map-layers.json", "test-map-composite.png",
                 "test-map-viewer/index.html", "test-map-tiles"):
        assert (tmp_path / name).exists(), name
    layers = json.loads((tmp_path / "test-map-layers.json").read_text())
    assert {"map", "fixed", "background", "haze"} <= set(layers)
