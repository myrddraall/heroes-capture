"""The tool's output: a running log as a tree, in one of two modes, with everything also written to
two log files.

The tree: what the run does is in folders (a job, its runs, their steps, a step's stages and
passes), each with what happened in it, line by line, indented under it. A folder opens with its
title (once something is shown in it) and closes with a line of its own: ✓ (✗ if it failed), its
title again, its bar and count when it counts something, and how long it took; a folder with
nothing in it is that one line. Lines stay where they are printed: a long run, or the run before,
is scrolled back to.

pretty (the default on a terminal): the tree, with a live footer below it: each folder still open
with its bar (a folder's count: its own, or what the folders in it count, those planned included),
how long it has run, the deepest one's time left, what is planned next, and a pause while it
lasts. Python warnings and library logging show as warnings; anything a library prints straight to
stdout or stderr shows dimmed in the tree.

log (--log; the default when CI is set or the output isn't a terminal): the same tree, as plain
text, without the footer.

--verbose adds the detail messages in both modes.

The plain log (set_plain_log) gets the tree as log mode prints it, whatever the mode on screen: the
run to read back. The diagnostic log (set_log_file) gets every message, detail included, and every
line libraries printed, each with a time stamp and its kind. Listeners (add_listener) get each line
of the tree as plain text, detail included: a run's own log (runlog.py).

A process started to carry on the run (a restarted capture) draws its tree at the depth the parent
had (HRS_UI_DEPTH), so its lines sit under the parent's open folders.
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
from rich.text import Text

GUIDE = "│ "
BAR_WIDTH = 20
BAR_AT = 64  # the column the closing lines' bars start at (further when the title is longer)
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
DEPTH_ENV = "HRS_UI_DEPTH"


class Folder:
    """A folder of the tree: what it counts (total, None: nothing), how far it is, and the folders
    in it (those planned and not opened yet included)."""

    def __init__(self, title: str, total: int | None = None, completed: int = 0, planned: bool = False):
        self.title, self.total, self.completed = title, total, completed
        self.state = "planned" if planned else "running"
        self.children: list[Folder] = []
        self.started = time.time()
        self.seconds = 0.0
        self.opened = False  # its title line printed (once something is shown in it)

    def advance(self, n: int = 1) -> None:
        self.completed += n
        _refresh()

    def set_total(self, total: int | None) -> None:
        self.total = total
        _refresh()

    def units(self) -> tuple[int, int]:
        """(done, total) of what it counts: its own, or the sum of the folders in it."""
        if self.total is not None:
            if self.state == "planned":
                return 0, self.total
            return (self.total if self.state == "done" else min(self.completed, self.total)), self.total
        done = total = 0
        for child in self.children:
            d, t = child.units()
            done, total = done + d, total + t
        return done, total


class _State:
    def __init__(self):
        self.open: list[Folder] = []
        self.listeners: list = []

    mode = "log"
    verbose = False
    console: Console | None = None
    file = None
    file_path: Path | None = None
    pending: list[str] = []
    plain = None
    plain_pending: list[str] = []
    live: Live | None = None
    base = 0  # the depth the tree starts at (a restarted capture's: its parent's)
    notice = ""  # a passing state shown in the footer while it lasts (see notice)
    notice_started = 0.0
    handler: logging.Handler | None = None


_s = _State()


class _Tee(io.TextIOBase):
    """sys.stdout or sys.stderr while the tool runs: whatever a library prints straight to them
    goes into the log file too, and shows dimmed in the tree."""

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
        if _s.open:
            _emit(self.kind, line, Text(line, style="dim"))
        else:
            _record(self.kind, line)
            _plain_log(line)
            _tell(line)
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
    try:
        _s.base = max(0, int(os.environ.get(DEPTH_ENV, "0")))
    except ValueError:
        _s.base = 0
    warnings.showwarning = lambda message, category, filename, lineno, file=None, line=None: warn(
        f"{category.__name__}: {message} ({Path(filename).name}:{lineno})")
    # Once: configured again (as tests do, putting this module's state back between them), a
    # second handler would show each warning twice; the root logger is asked, not the state.
    if not any(getattr(h, "hrs_ui", False) for h in logging.getLogger().handlers):
        _s.handler = logging.Handler(logging.WARNING)
        _s.handler.emit = lambda record: warn(f"{record.name}: {record.getMessage()}")
        _s.handler.hrs_ui = True
        logging.getLogger().addHandler(_s.handler)


def _console() -> Console:
    if _s.console is None:
        configure()
    return _s.console


def set_log_file(path: Path) -> None:
    """Where every message goes too (appended: a restarted capture keeps writing to it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _s.file, _s.file_path = open(path, "a", encoding="utf-8"), path
    for line in _s.pending:
        _s.file.write(line)
    _s.pending.clear()
    _s.file.flush()


def log_file() -> Path | None:
    """The diagnostic log's path, once set."""
    return _s.file_path


def close_log_file() -> None:
    """The log file closed (before its folder is removed); messages are kept in memory again."""
    if _s.file:
        _s.file.close()
        _s.file = None


def set_plain_log(path: Path, header: bool = True) -> None:
    """Where the output goes as log mode prints it, appended after a dated line with the command
    (header=False: a run carrying on, as a restarted capture, without a line of its own)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _s.plain = open(path, "a", encoding="utf-8")
    if header:
        _s.plain.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} heroes-capture {' '.join(sys.argv[1:])}\n")
    for line in _s.plain_pending:
        _s.plain.write(line)
    _s.plain_pending.clear()
    _s.plain.flush()


def add_listener(listener) -> None:
    """listener(line) for each line of the tree from now on, as plain text (detail included)."""
    _s.listeners.append(listener)


def remove_listener(listener) -> None:
    if listener in _s.listeners:
        _s.listeners.remove(listener)


def _plain_log(text: str) -> None:
    line = text + "\n"
    if _s.plain:
        _s.plain.write(line)
        _s.plain.flush()
    else:
        _s.plain_pending.append(line)


def _record(kind: str, text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {kind:<7} {text}\n"
    if _s.file:
        _s.file.write(line)
        _s.file.flush()
    else:
        _s.pending.append(line)


def _tell(text: str) -> None:
    for listener in list(_s.listeners):
        listener(text)


def _plain(text: str) -> None:
    _console().print(text, markup=False, soft_wrap=True)


# ------------------------------------------------------------------------------------------------
# The tree
# ------------------------------------------------------------------------------------------------

def depth() -> int:
    """How deep a line printed now sits (a restarted capture starts its tree there); the open
    folders' title lines printed first, so what it prints sits under them."""
    _open_titles()
    return _s.base + len(_s.open)


def _guides(level: int) -> str:
    return GUIDE * level


def _line_out(level: int, kind: str, plain: str, styled: Text, shown: bool = True) -> None:
    """One line of the tree at `level`: in the logs, to the listeners, and on screen (`shown`):
    `plain` as log mode prints it (the plain log reads the same in either mode), `styled` in the
    live view."""
    text = _guides(level) + plain
    _record(kind, "  " * level + plain)
    _tell(text)
    if not shown:
        return
    _plain_log(text)
    if _s.mode == "log":
        _plain(text)
    else:
        _console().print(Text(_guides(level), style="dim") + styled, soft_wrap=True)


def _open_titles() -> None:
    """The title lines of the open folders not printed yet (nothing was shown in them so far)."""
    for i, folder in enumerate(_s.open):
        if not folder.opened:
            folder.opened = True
            _line_out(_s.base + i, "folder", f"▾ {folder.title}", Text("▾ ", style="cyan") + Text(folder.title, style="bold"))


def _emit(kind: str, plain: str, styled: Text, shown: bool = True) -> None:
    """A line in the deepest open folder."""
    if shown:
        _open_titles()
    _line_out(_s.base + len(_s.open), kind, plain, styled, shown)


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def _bar(done: int, total: int, width: int = BAR_WIDTH) -> tuple[str, str]:
    filled = 0 if total <= 0 else round(width * min(done, total) / total)
    return "━" * filled, "─" * (width - filled)


def _counted(folder: Folder) -> tuple[str, Text]:
    """A folder's bar and count (nothing when it counts nothing), plain and styled."""
    done, total = folder.units()
    if not total:
        return "", Text()
    full, rest = _bar(done, total)
    count = f"  {done}/{total}"
    return full + rest + count, Text(full, style="green" if folder.state == "done" else "cyan") + Text(rest, style="dim") + Text(count)


def _close_line(folder: Folder, level: int) -> None:
    _open_titles()  # the folders it is in, if nothing was shown in them yet
    marks = {"done": ("✓", "green bold"), "failed": ("✗", "red bold"), "stopped": ("✗", "red bold")}
    mark, style = marks.get(folder.state, ("✓", "green bold"))
    lead = "└ " if folder.opened else ""
    title = f"{mark} {folder.title}"
    pad = " " * max(2, BAR_AT - len(_guides(level)) - len(lead) - len(title))
    bar_plain, bar_styled = _counted(folder)
    when = _duration(folder.seconds) + (" (stopped)" if folder.state == "stopped" else " (failed)" if folder.state == "failed" else "")
    plain = f"{lead}{title}{pad}{bar_plain}{'  ' if bar_plain else ''}{when}"
    styled = (Text(lead, style="dim") + Text(mark + " ", style=style) + Text(folder.title) + Text(pad) + bar_styled
              + Text(("  " if bar_plain else "") + when, style="dim"))
    _line_out(level, "folder", plain, styled)


@contextmanager
def folder(title: str, total: int | None = None, done: int = 0):
    """A folder of the tree, open while the block runs (see the module's note): yields it, to count
    what it does (advance; set_total). A folder planned under the same name (plan) is the one
    opened. On an exception it closes failed (stopped: interrupted) and the exception goes on."""
    parent = _s.open[-1] if _s.open else None
    node = next((c for c in parent.children if c.state == "planned" and c.title == title), None) if parent else None
    if node is None:
        node = Folder(title, total, done)
        if parent:
            parent.children.append(node)
    else:
        node.state, node.started, node.completed = "running", time.time(), done
        if total is not None:
            node.total = total
    level = _s.base + len(_s.open)
    _record("folder", "  " * level + f"{title} (opened)")
    _s.open.append(node)
    _start_live()
    try:
        yield node
        node.state = "done"
    except KeyboardInterrupt:
        node.state = "stopped"
        raise
    except BaseException:
        node.state = "failed"
        raise
    finally:
        node.seconds = time.time() - node.started
        if node in _s.open:
            _s.open.remove(node)
        node.children = [c for c in node.children if c.state != "planned"]  # planned, never opened: left out
        _close_line(node, level)
        if not _s.open:
            _stop_live()
        else:
            _refresh()


def plan(title: str, total: int | None = None) -> None:
    """A folder the open one will open later (folder, by the same title): in the footer as what is
    next, and counted in the open folders' bars from now."""
    if _s.open:
        _s.open[-1].children.append(Folder(title, total, planned=True))
        _refresh()


@contextmanager
def step(title: str):
    """A step of the run (preparing, capturing, stitching): a folder."""
    with folder(title) as node:
        yield node


@contextmanager
def bar(total: int, label: str, done: int = 0):
    """A folder counting `total` things, starting at `done` (a resumed run's): yields advance(n=1)."""
    with folder(label, total, done) as node:
        yield node.advance


# ------------------------------------------------------------------------------------------------
# Lines
# ------------------------------------------------------------------------------------------------

def show(renderable) -> None:
    """Something to show as it is (a table): on screen, and as plain text in the plain log."""
    if isinstance(renderable, str):
        _emit("show", renderable, Text(renderable))
        return
    _console().print(renderable)
    text = io.StringIO()
    Console(file=text, width=_console().width, color_system=None, highlight=False).print(renderable)
    for line in text.getvalue().splitlines():
        _record("show", line.rstrip())
        _plain_log(line.rstrip())
        _tell(line.rstrip())


def info(text: str) -> None:
    """A routine message: a line in the open folder."""
    text = text.strip()
    _emit("info", text, Text(text))


def detail(text: str) -> None:
    """Extra information, shown with --verbose (always in the logs and to the listeners)."""
    text = text.strip()
    _emit("detail", text, Text(text, style="dim"), shown=_s.verbose)


def warn(text: str) -> None:
    """Something to notice: a line in the open folder, in yellow (warning: in log mode)."""
    text = text.strip()
    _emit("warning", f"warning: {text}", Text("⚠ ", style="yellow bold") + Text(text, style="yellow"))


def done(text: str) -> None:
    """An outcome worth keeping: a ✓ line in the open folder."""
    text = text.strip()
    _emit("done", text, Text("✓ ", style="green bold") + Text(text))


@contextmanager
def notice(text: str, after: str | None = None):
    """A passing state (the game not in front, a menu open): in the footer while it lasts (a line
    in log mode), then one line in the tree saying how long it lasted (`after`, or the text)."""
    _record("paused", text)
    started = time.time()
    if _s.mode == "log" or not _s.live:
        _emit("paused", f"paused: {text}", Text(f"paused: {text}"))
    _s.notice, _s.notice_started = text, started
    _refresh()
    try:
        yield
    finally:
        _s.notice = ""
        line = f"⏸ paused {_duration(time.time() - started)}: {after or text}"
        _emit("paused", line, Text(line, style="yellow"))


# ------------------------------------------------------------------------------------------------
# The live footer
# ------------------------------------------------------------------------------------------------

class _Footer:
    """The folders still open, each with its bar and time; the deepest's time left; what is
    planned next; a pause while it lasts."""

    def __rich__(self):
        now = time.time()
        rows = []
        for i, node in enumerate(_s.open):
            deepest = i == len(_s.open) - 1
            mark = SPINNER[int(now * 10) % len(SPINNER)] if deepest else "▸"
            title = "  " * i + f"{mark} {node.title}"
            row = Text(title, style="bold" if deepest else "")
            row.append(" " * max(2, BAR_AT - len(title)))
            done, total = node.units()
            if total:
                full, rest = _bar(done, total)
                row.append(full, style="cyan")
                row.append(rest, style="dim")
                row.append(f"  {done}/{total}")
            elapsed = now - node.started
            row.append(f"  {_duration(elapsed)}", style="dim")
            if deepest and total and done:
                row.append(f"  ~{_duration(elapsed / done * max(0, total - done))} left", style="dim")
            row.no_wrap, row.overflow = True, "ellipsis"
            rows.append(row)
            planned = [c for c in node.children if c.state == "planned"]
            if planned:
                ahead = " · ".join(c.title + (f" {c.total}" if c.total else "") for c in planned)
                rows.append(Text("  " * (i + 1) + f"next: {ahead}", style="dim", no_wrap=True, overflow="ellipsis"))
        if _s.notice:
            rows.append(Text(f"⏸ {_s.notice}  {_duration(now - _s.notice_started)}", style="yellow bold", no_wrap=True, overflow="ellipsis"))
        return Group(Text("─" * min(_console().width, 100), style="dim"), *rows)


def _start_live() -> None:
    if _s.mode == "pretty" and _s.live is None:
        _s.live = Live(_Footer(), console=_console(), refresh_per_second=8, transient=True,
                       redirect_stdout=False, redirect_stderr=False)  # _Tee puts those in the tree
        _s.live.start()


def _stop_live() -> None:
    if _s.live:
        _s.live.stop()
        _s.live = None


def _refresh() -> None:
    if _s.live:
        _s.live.refresh()


@contextmanager
def suspend():
    """The footer taken down (another process takes over the terminal: a restarted capture, which
    draws its own tree under the open folders: depth)."""
    if not _s.live:
        yield
        return
    _s.live.stop()
    try:
        yield
    finally:
        _s.live.start()
