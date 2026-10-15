"""The elements in the pack: the cut-outs (element_stitch.py) packed into atlases, and the pack's
data.elements (PACK.md, Elements; the battlegrounds' pack_data step, specifics.py)."""

import math
import shutil
from pathlib import Path

import pyvips

from heroes_capture.capture.pack import QUALITY, pack_atlas, rgba
from heroes_capture.common.jsonio import read_json

# Transparent pixels between the cut-outs in the lossy atlases: lossy WebP's colour is coarser than
# its pixels (halved, and coded in blocks of 4), and would carry a neighbour's into a cut-out's edge.
ATLAS_GAP = 4

def collect_elements(out: Path, raw: Path, pack: Path, manifest: dict, layer_id: str, small_factor: int,
                     layers: dict[str, str] | None = None) -> dict | None:
    """The elements render's cut-outs (element_stitch.write_elements: <id>-elements/ and its index) moved
    into raw/elements/, packed into the pack's atlases (PACK.md, Elements): `standing` (the standing
    structures and the camps), `rubble`, `masks` (where a standing neighbour is in front), `small`,
    every state at 1/`small_factor` (the map layer's overview scale) for the zoomed-out view, and
    `team` and `teamSmall`, the team-colour masks of the states shot from both sides. The pack's data.elements: every structure and
    camp the map has, each with its states' places in the atlases and rectangles in the map layer's
    pixels; on a map of several arenas (`layers`: each map image's layer id) an element's `layer`
    names its arena's, where that isn't `layer_id`. None when the render has no elements."""
    index_path = out / f"{manifest['id']}-elements.json"
    if not index_path.exists():
        return None
    index = read_json(index_path)
    source = out / f"{manifest['id']}-elements"
    (raw / "elements").mkdir(parents=True, exist_ok=True)
    (pack / "elements").mkdir(parents=True, exist_ok=True)
    pictures: dict[str, list[tuple[str, pyvips.Image]]] = {"standing": [], "rubble": [], "masks": [], "small": [], "team": [], "teamSmall": []}
    states: dict[tuple[str, int], dict] = {}

    def small(picture: pyvips.Image) -> pyvips.Image:
        """A picture at 1/small_factor: box-filtered on premultiplied colour (as the pyramid's levels
        are), ceil(width / factor) by ceil(height / factor)."""
        if small_factor == 1:
            return picture
        w, h = math.ceil(picture.width / small_factor) * small_factor, math.ceil(picture.height / small_factor) * small_factor
        padded = picture.embed(0, 0, w, h, extend="background", background=[0, 0, 0, 0])
        return padded.premultiply().shrink(small_factor, small_factor).unpremultiply().cast("uchar")

    for cut in index["cutouts"]:
        if cut["file"] is None:  # the element leaves nothing in this state (a fallen moonwell)
            states.setdefault((cut["kind"], cut["element"]), {})[cut["state"]] = None
            continue
        shutil.move(source / cut["file"], raw / "elements" / cut["file"])
        picture = rgba(pyvips.Image.new_from_file(str(raw / "elements" / cut["file"])))
        atlas = "rubble" if cut["state"] == "rubble" else "standing"
        pictures[atlas].append((cut["key"], picture))
        pictures["small"].append((cut["key"], small(picture)))
        state = {"rect": cut["rect"], "atlas": atlas, "key": cut["key"], "layer": (layers or {}).get(cut.get("image"), layer_id)}
        # Where a standing neighbour is in front of it: a mask (white: hidden) over the same rect.
        hidden = []
        for mask in cut.get("hiddenBy") or []:
            shutil.move(source / mask["file"], raw / "elements" / mask["file"])
            key = mask["file"].removesuffix(".png")
            pictures["masks"].append((key, rgba(pyvips.Image.new_from_file(str(raw / "elements" / mask["file"])))))
            hidden.append({"id": mask["id"], "key": key})
        if hidden:
            state["hiddenBy"] = hidden
        if cut.get("teamMask"):
            # Its team-colour mask (team_colour.py) over the same rect, and at the small scale.
            shutil.move(source / cut["teamMask"], raw / "elements" / cut["teamMask"])
            team = rgba(pyvips.Image.new_from_file(str(raw / "elements" / cut["teamMask"])))
            pictures["team"].append((cut["key"], team))
            pictures["teamSmall"].append((cut["key"], small(team)))
            state["teamMask"] = True
        states.setdefault((cut["kind"], cut["element"]), {})[cut["state"]] = state
    shutil.rmtree(source, ignore_errors=True)
    index_path.unlink()
    # The atlases written, and each state's places in them put in for its keys.
    atlases, places = {}, {}
    for name, found in pictures.items():
        if not found:
            continue
        lossless = name in ("masks", "team", "teamSmall")
        atlas, at = pack_atlas(found, gap=0 if lossless else ATLAS_GAP)
        # The colour lossy, as the tiles' (the transparency exact); the masks lossless: sharp-edged,
        # and team colour's amounts exact.
        if lossless:
            atlas.webpsave(str(pack / "elements" / f"{name}.webp"), lossless=True, keep="none")
        else:
            atlas.webpsave(str(pack / "elements" / f"{name}.webp"), Q=QUALITY, alpha_q=100, keep="none")
        atlases[name] = {"file": f"elements/{name}.webp", "size": [atlas.width, atlas.height], "scale": 1 / small_factor if name in ("small", "teamSmall") else 1}
        places[name] = at
    element_layer: dict[tuple[str, int], str] = {}
    for which, by_state in states.items():
        for state in (s for s in by_state.values() if s):
            element_layer[which] = state.pop("layer")
    for state in (s for by_state in states.values() for s in by_state.values() if s):
        key = state.pop("key")
        state["at"] = list(places[state["atlas"]][key])
        state["small"] = list(places["small"][key])
        for mask in state.get("hiddenBy", []):
            mask["at"] = list(places["masks"][mask.pop("key")])
        if state.pop("teamMask", False):
            state["teamMask"] = {"at": list(places["team"][key]), "small": list(places["teamSmall"][key])}
    found = manifest.get("elements") or {}
    def on_layer(which: tuple[str, int]) -> dict:
        """The element's `layer`, when its arena's isn't the elements' own."""
        layer = element_layer.get(which, layer_id)
        return {"layer": layer} if layer != layer_id else {}

    structures = [{"id": u["id"], "type": u["type"], "cell": [u["x"], u["y"]], "owner": u["owner"], "town": u["town"],
                   "outpost": u.get("outpost"), "core": u["core"], **on_layer(("structure", u["id"])),
                   "states": states.get(("structure", u["id"]), {})}
                  for u in found.get("structures", [])]
    camps = [{"camp": c["camp"], "type": c["type"], "cell": [c["x"], c["y"]], **on_layer(("camp", c["camp"])),
              "states": states.get(("camp", c["camp"]), {})}
             for c in found.get("camps", [])]
    outposts = [{"outpost": o["outpost"], "town": o["town"], "lane": o["lane"], "owner": o["owner"], "cell": [o["x"], o["y"]]}
                for o in found.get("outposts", [])]
    return {"layer": layer_id, "atlases": atlases, "structures": structures, "towns": found.get("towns", []),
            "outposts": outposts, "camps": camps}
