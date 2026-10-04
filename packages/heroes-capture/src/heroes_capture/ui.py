"""The tool's output, in one of two modes, with everything also written to a log file.

pretty (the default on a terminal): a live view with Rich: the current step with a spinner, its
latest status line, progress bars for the long loops, and a ✓ line per finished step. Routine
messages (info) only update the status line; warnings print above the live view, which redraws
below them, so they never break a progress bar. Python warnings and library logging show as
warnings; anything a library prints straight to stdout or stderr shows dimmed above the view.

log (--log; the default when CI is set or the output isn't a terminal): every message on its own
line, as plain text.

--verbose adds the detail messages in both modes. The log file (set_log_file) always gets every
message, detail included, and every line libraries printed, with a time stamp.
"""

import io
import logging
import os
import sys
import time
import warnings
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.spinner import Spinner
from rich.text import Text


class _State:
    mode = "log"
    verbose = False
    console: Console | None = None
    file = None
    pending: list[str] = []
    live: Live | None = None
    title = ""
    status = ""
    bars: Progress | None = None
    started = 0.0


_s = _State()


class _Tee(io.TextIOBase):
    """sys.stdout or sys.stderr while the tool runs: whatever a library prints straight to them
    goes into the log file too, and in the live view prints (dimmed) above it."""

    def __init__(self, kind: str, original):
        self.kind, self.original, self.pending = kind, original, ""

    def write(self, text: str) -> int:
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self._line(line)
        return len(text)

    def flush(self) -> None:
        if self.pending:
            self._line(self.pending)
            self.pending = ""
        self.original.flush()

    def _line(self, line: str) -> None:
        _record(self.kind, line)
        if _s.live:
            _console().print(Text(line, style="dim"))
        else:
            self.original.write(line + "\n")

    def isatty(self) -> bool:
        return self.original.isatty()

    def fileno(self) -> int:
        return self.original.fileno()

    @property
    def encoding(self):
        return getattr(self.original, "encoding", "utf-8")


class _Stdout(io.TextIOBase):
    """Where the console writes: the real stdout behind _Tee, looked up at each write (a stream
    held on to can be swapped or closed in the meantime, as tests do)."""

    @staticmethod
    def _target():
        return sys.stdout.original if isinstance(sys.stdout, _Tee) else sys.stdout

    def write(self, text: str) -> int:
        return self._target().write(text)

    def flush(self) -> None:
        self._target().flush()

    def isatty(self) -> bool:
        return self._target().isatty()

    def fileno(self) -> int:
        return self._target().fileno()

    @property
    def encoding(self):
        return getattr(self._target(), "encoding", "utf-8")


def configure(log: bool = False, verbose: bool = False) -> None:
    """Pick the mode and route Python warnings, library logging and whatever libraries print
    through this module."""
    _s.console = Console(file=_Stdout(), highlight=False)
    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee("stdout", sys.stdout)
    if not isinstance(sys.stderr, _Tee):
        sys.stderr = _Tee("stderr", sys.stderr)
    plain = log or bool(os.environ.get("CI")) or not _s.console.is_terminal
    _s.mode = "log" if plain else "pretty"
    _s.verbose = verbose
    warnings.showwarning = lambda message, category, filename, lineno, file=None, line=None: warn(
        f"{category.__name__}: {message} ({Path(filename).name}:{lineno})")
    handler = logging.Handler(logging.WARNING)
    handler.emit = lambda record: warn(f"{record.name}: {record.getMessage()}")
    logging.getLogger().addHandler(handler)


def _console() -> Console:
    if _s.console is None:
        configure()
    return _s.console


def set_log_file(path: Path) -> None:
    """Where every message goes too (appended: a restarted capture keeps writing to it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _s.file = open(path, "a", encoding="utf-8")
    for line in _s.pending:
        _s.file.write(line)
    _s.pending.clear()
    _s.file.flush()


def close_log_file() -> None:
    """The log file closed (before its folder is removed); messages are kept in memory again."""
    if _s.file:
        _s.file.close()
        _s.file = None


def _record(kind: str, text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {kind:<7} {text}\n"
    if _s.file:
        _s.file.write(line)
        _s.file.flush()
    else:
        _s.pending.append(line)


def _plain(text: str) -> None:
    _console().print(text, markup=False, soft_wrap=True)


def info(text: str) -> None:
    """A routine message: a line in log mode, the status line in the live view."""
    _record("info", text)
    if _s.mode == "log":
        _plain(text)
    elif _s.live:
        _s.status = text.strip()
        _refresh()


def detail(text: str) -> None:
    """Extra information, shown with --verbose."""
    _record("detail", text)
    if not _s.verbose:
        return
    if _s.mode == "pretty":
        _console().print(Text("  " + text, style="dim"))
    else:
        _plain("  " + text)


def warn(text: str) -> None:
    """Something to notice: shown in both modes (above the live view, which carries on)."""
    _record("warning", text)
    if _s.mode == "log":
        _plain(f"warning: {text}")
    else:
        _console().print(Text("⚠ ", style="yellow bold") + Text(text, style="yellow"))


def done(text: str) -> None:
    """An outcome worth keeping on screen."""
    _record("done", text)
    if _s.mode == "log":
        _plain(text)
    else:
        _console().print(Text("✓ ", style="green bold") + Text(text))


class _View:
    """The live view: the step with a spinner, its status line, and its progress bars."""

    def __init__(self):
        self.spinner = Spinner("dots", style="cyan")

    def __rich__(self):
        elapsed = time.time() - _s.started
        self.spinner.update(text=Text.assemble((_s.title, "bold"), (f"  {elapsed:4.0f} s", "dim")))
        parts = [self.spinner]
        if _s.status:
            parts.append(Text("  " + _s.status, style="dim", overflow="ellipsis", no_wrap=True))
        if _s.bars and _s.bars.tasks:
            parts.append(_s.bars)
        return Group(*parts)


def _refresh() -> None:
    if _s.live:
        _s.live.refresh()


@contextmanager
def step(title: str):
    """A step of the run: the live view while it runs, then a ✓ (or ✗) line with its time."""
    _record("step", title)
    _s.started = time.time()
    if _s.mode == "log":
        _plain(f"\n{title}")
        try:
            yield
        finally:
            _record("step", f"{title}: {time.time() - _s.started:.0f} s")
        return
    _s.title, _s.status = title, ""
    _s.bars = Progress(TextColumn("  {task.description}"), BarColumn(bar_width=40), MofNCompleteColumn(),
                       TimeRemainingColumn(), console=_console())
    _s.live = Live(_View(), console=_console(), refresh_per_second=8, transient=True,
                   redirect_stdout=False, redirect_stderr=False)  # _Tee prints those above the view
    _s.live.start()
    failed = True
    try:
        yield
        failed = False
    finally:
        _s.live.stop()
        _s.live, _s.bars, _s.status = None, None, ""
        seconds = time.time() - _s.started
        _record("step", f"{title}: {seconds:.0f} s{' (failed)' if failed else ''}")
        mark = Text("✗ ", style="red bold") if failed else Text("✓ ", style="green bold")
        _console().print(mark + Text(title) + Text(f"  {seconds:.0f} s", style="dim"))


@contextmanager
def suspend():
    """The live view paused (another process takes over the terminal: a restarted capture)."""
    if not _s.live:
        yield
        return
    _s.live.stop()
    try:
        yield
    finally:
        _s.live.start()


@contextmanager
def bar(total: int, label: str):
    """A progress bar in the live view (nothing in log mode, whose messages already say it):
    yields advance(n=1)."""
    if _s.mode != "pretty" or not _s.bars:
        yield lambda n=1: None
        return
    task = _s.bars.add_task(label, total=total)
    try:
        yield lambda n=1: (_s.bars.advance(task, n), _refresh())
    finally:
        if _s.bars:
            _s.bars.remove_task(task)
