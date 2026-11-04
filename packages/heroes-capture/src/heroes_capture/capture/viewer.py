"""The pack's reference viewer: index.html in raw/, beside the pack (viewer.html as it is). It reads
../pack/pack.json and draws the layers as PACK.md describes, tiles fetched from the layers' PMTiles
archives by range requests; it needs the render's folder served over HTTP (heroes-capture map view
does that locally), or the pack folder chosen when it is opened from disk. A check that the pack
works, not the map viewer itself, which lives in its own project.
"""

import shutil
from pathlib import Path

TEMPLATE = Path(__file__).with_name("viewer.html")


def write(raw: Path) -> None:
    """The reference viewer into a render's raw/ folder."""
    shutil.copyfile(TEMPLATE, raw / "index.html")
