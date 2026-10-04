"""The run's log: each message shown (ui.py: a line in --log mode, the status line in the live
view) and, once the run's folder is known, appended to its log.txt (copied back with the results,
so a run can be diagnosed from them)."""

import time
from contextlib import contextmanager
from pathlib import Path

from . import ui

_path: Path | None = None
_timings: list[tuple[str, float]] = []


def set_log_file(path: Path) -> None:
    global _path
    _path = path


def _append(text: str) -> None:
    if _path is not None:
        with open(_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def log(*parts) -> None:
    """A routine message."""
    text = " ".join(str(p) for p in parts)
    ui.info(text)
    _append(text)


def warn(text: str) -> None:
    """Something to notice, shown in every mode."""
    ui.warn(text)
    _append(f"warning: {text}")


def done(text: str) -> None:
    """An outcome worth keeping on screen."""
    ui.done(text)
    _append(text)


def detail(text: str) -> None:
    """Extra information (shown with --verbose; always in the log files)."""
    ui.detail(text)
    _append(f"  {text}")


@contextmanager
def stage(name: str):
    """Time a stage of the run; log_timings() lists them all at the end."""
    started = time.time()
    try:
        yield
    finally:
        _timings.append((name, time.time() - started))


def log_timings(title: str) -> None:
    """The stages timed so far, one line each, and the total."""
    if not _timings:
        return
    width = max(len(name) for name, _ in _timings)
    log(f"{title} timings:")
    for name, seconds in _timings:
        log(f"  {name:<{width}}  {seconds:7.1f} s")
    log(f"  {'total':<{width}}  {sum(s for _, s in _timings):7.1f} s")
    _timings.clear()
