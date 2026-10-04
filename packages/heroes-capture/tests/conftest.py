"""Shared test helpers: the simulated game (tests/sim) and the game's storage.

The game data tests read Blizzard's CDN through CascLib (no install on the test machine), with
files cached in the same cache the tool uses (game_data.cache_dir()); CI keeps that cache
between runs. They need native/ filled by tools/build_native.py.
"""

import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SIM = PACKAGE / "tests" / "sim"
sys.path.insert(0, str(PACKAGE / "src"))


SIM_MEMORY = 3 * 2**30  # what one simulated capture (or a stitch of one) may take at its peak: ~1.5 GB measured, doubled


def sim_slots() -> int:
    """How many simulations may run at once: as many as the free memory holds (HRS_SIM_SLOTS sets
    it). All of them at once on a many-core machine ran it out of memory: the kernel killed one
    (exit code -9) partway through, a different one each time."""
    if os.environ.get("HRS_SIM_SLOTS"):
        return max(1, int(os.environ["HRS_SIM_SLOTS"]))
    try:
        meminfo = Path("/proc/meminfo").read_text()
    except OSError:
        return os.cpu_count() or 1
    available = int(re.search(r"MemAvailable:\s+(\d+) kB", meminfo).group(1)) * 1024
    return max(1, available // SIM_MEMORY)


@contextmanager
def sim_slot():
    """One of sim_slots() places, shared by the test processes (xdist's workers) through locked
    files; waits for one to come free."""
    try:
        import fcntl
    except ImportError:  # not on Linux or macOS: no limit
        yield
        return
    folder = Path(tempfile.gettempdir()) / "heroes-capture-sim-slots"
    folder.mkdir(exist_ok=True)
    slots = sim_slots()
    while True:
        for n in range(slots):
            handle = open(folder / f"slot-{n}", "w")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
                handle.close()
            return
        time.sleep(0.2)


def simulate(work: Path, mode: str = "matte", *args: str, **env: str) -> dict:
    """One simulated capture (tests/sim/fakegame.py) in `work`: its outcome line, its run log
    and its folder."""
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(SIM / "makemanifest.py"), str(work), mode], check=True)
    with sim_slot():
        run = subprocess.run([sys.executable, str(SIM / "fakegame.py"), str(PACKAGE), str(work), *args],
                             cwd=work, capture_output=True, text=True, timeout=900, env={**os.environ, **env})
    outcome = re.findall(r"\[harness\] (.*); virtual time", run.stdout)
    log = work / "test-map" / "log.txt"
    return {"outcome": outcome[-1] if outcome else f"no outcome (exit code {run.returncode}):\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}",
            "log": log.read_text() if log.exists() else "", "work": work}


@pytest.fixture(scope="session")
def storage():
    """The game's storage on Blizzard's CDN."""
    from heroes_capture.game_data import open_storage

    with open_storage(None) as opened:
        yield opened
