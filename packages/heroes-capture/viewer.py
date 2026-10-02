"""A prototype viewer per map: <id>-viewer/ with index.html (viewer.html with the layers'
placement filled in) and the images it shows: the map image (on a map of several arenas, each
arena's image, shown one at a time), and the map's own sky layers behind it when the stitch
made them (<id>-layers.json), moving at their rates as the view is dragged and zoomed. Opens
straight from the folder (no server: the placement is in the page, not fetched).
"""

import json
import shutil
from pathlib import Path

from runlog import log, stage

TEMPLATE = Path(__file__).with_name("viewer.html")
SKY_ORDER = ("background", "haze")  # back to front, under the map


def write(out: Path, map_id: str, title: str, images: list[str]) -> None:
    """<out>/<map_id>-viewer/ from the map images `images` (ids: <id>.png and <id>.geo.json in
    `out`) and, when there, <map_id>-layers.json."""
    images = [i for i in images if (out / f"{i}.geo.json").exists()]
    if not images:
        return
    with stage("viewer"):
        folder = out / f"{map_id}-viewer"
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir()
        data = {"maps": [], "fixed": None, "sky": []}
        for image in images:
            geo = json.loads((out / f"{image}.geo.json").read_text())
            src = "map.png" if len(images) == 1 else f"map-{(geo.get('area') or image).lower()}.png"
            shutil.copyfile(out / geo["image"], folder / src)
            data["maps"].append({"src": src, "width": geo["width"], "height": geo["height"], "label": geo.get("area"),
                                 "pxPerCell": geo["pxPerCell"], "originCell": geo["originCell"]})
        layers_path = out / f"{map_id}-layers.json"
        if layers_path.exists():
            layers = json.loads(layers_path.read_text())
            if "fixed" in layers:
                shutil.copyfile(out / layers["fixed"]["image"], folder / "fixed.png")
                data["fixed"] = "fixed.png"
            for name in SKY_ORDER:
                layer = layers.get(name)
                if not layer:
                    continue
                shutil.copyfile(out / layer["image"], folder / f"{name}.png")
                data["sky"].append({"src": f"{name}.png", "rate": float(sum(layer["rate"]) / len(layer["rate"])),
                                    **{k: layer[k] for k in ("centreCell", "centrePixel", "pxPerMapCell")}})
        page = TEMPLATE.read_text(encoding="utf-8").replace("/*TITLE*/", title).replace("/*DATA*/", json.dumps(data))
        (folder / "index.html").write_text(page, encoding="utf-8")
    log(f"viewer -> {folder / 'index.html'} ({len(images)} map image{'s' if len(images) > 1 else ''}, {len(data['sky'])} sky layers)")
