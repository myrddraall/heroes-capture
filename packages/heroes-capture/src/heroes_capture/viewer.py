"""The pack's reference viewer: index.html in the pack (viewer.html as it is). It reads pack.json
and draws the layers as PACK.md describes, tiles fetched from the layers' PMTiles archives by
range requests; it needs the pack served over HTTP (heroes-capture map view does that locally).
A check that the pack works, not the map viewer itself, which lives in its own project.
"""

import shutil
from pathlib import Path

TEMPLATE = Path(__file__).with_name("viewer.html")


def write(pack: Path) -> None:
    """The reference viewer into a pack's folder."""
    shutil.copyfile(TEMPLATE, pack / "index.html")
