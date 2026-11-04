"""JSON files, always UTF-8 with "\n" line ends: read (with a default when the file isn't there) and
written; the manifest in the form JavaScript's JSON.stringify writes (js_json), whoever writes it."""

import json
from pathlib import Path

from heroes_capture.common import js_json

_MISSING = object()


def read_json(path: Path, default=_MISSING):
    """The file's JSON; `default` when the file doesn't exist (given), else FileNotFoundError."""
    if default is not _MISSING and not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data, indent: int | None = 2) -> None:
    """`indent` None: compact, no spaces between items."""
    separators = (",", ":") if indent is None else None
    path.write_text(json.dumps(data, indent=indent, separators=separators), encoding="utf-8", newline="\n")


def write_manifest(path: Path, manifest: dict) -> None:
    """A prepared map's manifest (capture/prepare.py writes it; the capture adds what it measures)."""
    path.write_text(js_json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
