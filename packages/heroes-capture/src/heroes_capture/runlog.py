"""The run's log: each line printed and, once the run's folder is known, appended to its log.txt
(copied back with the results, so a run can be diagnosed from them)."""

import time
from contextlib import contextmanager
from pathlib import Path

_path: Path | None = None
_timings: list[tuple[str, float]] = []


def set_log_file(path: Path) -> None:
    global _path
    _path = path


def log(*parts, **kw) -> None:
    text = " ".join(str(p) for p in parts)
    print(text, **kw)
    if _path is not None:
        with open(_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


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
