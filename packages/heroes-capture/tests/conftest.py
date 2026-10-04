"""Shared test helpers: the simulated game (tests/sim) and the game's storage.

The game data tests read Blizzard's CDN through CascLib (no install on the test machine), with
files cached in the same cache the tool uses (game_data.cache_dir()); CI keeps that cache
between runs. They need native/ filled by tools/build_native.py.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SIM = PACKAGE / "tests" / "sim"
sys.path.insert(0, str(PACKAGE / "src"))


def simulate(work: Path, mode: str = "matte", *args: str, **env: str) -> dict:
    """One simulated capture (tests/sim/fakegame.py) in `work`: its outcome line, its run log
    and its folder."""
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(SIM / "makemanifest.py"), str(work), mode], check=True)
    run = subprocess.run([sys.executable, str(SIM / "fakegame.py"), str(PACKAGE), str(work), *args],
                         cwd=work, capture_output=True, text=True, timeout=900, env={**os.environ, **env})
    outcome = re.findall(r"\[harness\] (.*); virtual time", run.stdout)
    log = work / "test-map" / "log.txt"
    return {"outcome": outcome[-1] if outcome else f"no outcome:\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}",
            "log": log.read_text() if log.exists() else "", "work": work}


@pytest.fixture(scope="session")
def storage():
    """The game's storage on Blizzard's CDN."""
    from heroes_capture.game_data import open_storage

    with open_storage(None) as opened:
        yield opened
