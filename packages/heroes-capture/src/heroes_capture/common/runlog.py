"""The run's log: each message shown (ui.py: a line in the tree) and, once the run's folder is known,
the tree from then on appended to its log.txt as plain text, detail included (copied back with the
results, so a run can be diagnosed from them)."""

import time
from contextlib import contextmanager
from pathlib import Path

from heroes_capture.common import ui

_path: Path | None = None
_timings: list[tuple[str, float]] = []
# The map's commands answered in each open stage, by verb: [count, seconds, unanswered] (command_time).
_commands: list[dict[str, list]] = []
# The stage running and the last message logged (watch.py names its frames by them).
current = {"stage": "", "message": ""}


def _write(line: str) -> None:
    if _path is not None:
        with open(_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def set_log_file(path: Path) -> None:
    global _path
    _path = path
    ui.remove_listener(_write)
    ui.add_listener(_write)


def log(*parts) -> None:
    """A routine message."""
    text = " ".join(str(p) for p in parts)
    current["message"] = text.strip()
    ui.info(text)


def warn(text: str) -> None:
    """Something to notice, shown in every mode."""
    current["message"] = text
    ui.warn(text)


def done(text: str) -> None:
    """An outcome worth keeping on screen."""
    current["message"] = text
    ui.done(text)


def detail(text: str) -> None:
    """Extra information (shown with --verbose; always in the log files)."""
    ui.detail(text)


@contextmanager
def paused(text: str, after: str | None = None):
    """A pause the run waits out (see ui.notice): shown while it lasts, then a line saying how long
    (`after`: what it was, said once it's over)."""
    with ui.notice(text, after):
        yield


def command_time(verb: str, seconds: float, answered: bool) -> None:
    """One of the map's commands sent and answered (or not) in `seconds`, for its stage's line in
    the diagnostic log (stage)."""
    if _commands:
        found = _commands[-1].setdefault(verb, [0, 0.0, 0])
        found[0] += 1
        found[1] += seconds
        found[2] += 0 if answered else 1


@contextmanager
def stage(name: str, total: int | None = None, done: int = 0):
    """A stage of the run: a folder of the tree (ui.folder: yields it, to count what it does), timed
    for log_timings; the map's commands sent in it, by verb, how many and their average round
    trip, a detail line as it closes (counted in the stages it is in too)."""
    started = time.time()
    outer = current["stage"]
    current["stage"] = name
    _commands.append({})
    try:
        with ui.folder(name, total, done) as node:
            try:
                yield node
            finally:
                commands = _commands[-1]
                if commands:
                    count, seconds = sum(c[0] for c in commands.values()), sum(c[1] for c in commands.values())
                    detail(f"commands: {count} in {seconds:.1f} s; " + ", ".join(
                        f"{verb} {n}× {s / n:.2f} s" + (f" ({missed} unanswered)" if missed else "")
                        for verb, (n, s, missed) in sorted(commands.items(), key=lambda kv: -kv[1][1])))
    finally:
        commands = _commands.pop()
        if _commands:
            for verb, (n, s, missed) in commands.items():
                found = _commands[-1].setdefault(verb, [0, 0.0, 0])
                found[0], found[1], found[2] = found[0] + n, found[1] + s, found[2] + missed
        current["stage"] = outer
        _timings.append((name, time.time() - started))


def log_timings(title: str) -> None:
    """The stages timed so far, one detail line each (each stage's folder shows its time), and the
    total."""
    if not _timings:
        return
    detail(f"{title} timings: " + ", ".join(f"{name} {seconds:.1f} s" for name, seconds in _timings)
           + f"; total {sum(s for _, s in _timings):.1f} s")
    _timings.clear()
