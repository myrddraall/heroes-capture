"""The elements in the map image: each structure and camp the elements capture shot alone over the
sky (element_capture.py) cut out and placed in the map image's pixels, with masks of where a
standing neighbour is in front (the battlegrounds' after_stitch step, specifics.py)."""

from pathlib import Path

import numpy as np
from PIL import Image

from heroes_capture.capture.matting import cut_out, light_level
from heroes_capture.capture.sky import LIGHT_LEVEL
from heroes_capture.capture.stitch import Layout
from heroes_capture.capture.team_colour import team_mask
from heroes_capture.common import ui
from heroes_capture.common.frames import PNG_COMPRESSION, frame_exists, load_frame
from heroes_capture.common.geometry import circle_box, screen_point
from heroes_capture.common.imaging import nonzero_box
from heroes_capture.common.jsonio import read_json, write_json
from heroes_capture.common.runlog import log, warn
from heroes_capture.common.workers import ordered_map

# A standing structure's masks of where a neighbour is in front of it (write_elements): a pixel
# changed by more than MASK_CHANGE when the neighbour is shown beside it, where the neighbour's own
# cut-out is at least MASK_SOLID opaque; a mask of fewer than MASK_LEAST pixels left out.
MASK_CHANGE, MASK_SOLID, MASK_LEAST = 24, 160, 20
# Structures whose remains outlive the clearing between the elements' rubble waves (a town hall's
# own actor, a moonwell's ring), so may lie in a neighbour's rubble shot (write_elements).
LINGERING_REMAINS = ("TownTownHall", "TownMoonwell")


# A team-colour mask's pixels this strong (either channel) count; this many of them in the other
# team's channel than its owner's: the structure was shot from the wrong side.
MASK_STRONG, MASK_WRONG_LEAST = 20, 200


def mask_team(mask: np.ndarray) -> str | None:
    """Whose colour a team-colour mask (R: Order's team's amount, G: Chaos's; team_colour.py) says
    changed: "order" or "chaos" when one team's strong pixels outnumber the other's and there are
    MASK_WRONG_LEAST of them at least; None when too few changed to tell."""
    order = int((mask[..., 0] > MASK_STRONG).sum())
    chaos = int((mask[..., 1] > MASK_STRONG).sum())
    if max(order, chaos) < MASK_WRONG_LEAST:
        return None
    return "order" if order > chaos else "chaos"


def write_elements(manifest: dict, base: Path, out: Path, layout: Layout, scale: float, ax: float, ay: float, image_id: str,
                   area_no: int | None = None) -> int:
    """The elements the capture shot alone over the sky (element_capture.py: elements/), each cut out
    (matting.cut_out: its colour and exact transparency, cropped to its circle and then to what
    shows) and placed in the map image's pixels: where its source tile sits on the canvas, moved by
    the difference between the camera's position for its shot and for the tile's, less the
    image's crop. Writes <id>-elements/<key>.png and <id>-elements.json (each one's element, state,
    image and rectangle in the map image; no file where nothing is left of it in that state), and
    for a state shot from both sides its team-colour mask, <key>-team.png (team_colour.py). On a
    map of several arenas, one call per arena's image (`area_no`: the elements shot from that
    area's tiles; the index gathers them all). Returns how many states were placed."""

    folder = base / "elements"
    record_path = folder / "elements.json"
    if not record_path.exists():
        return 0
    records = read_json(record_path)
    if area_no is not None:
        records = {key: r for key, r in records.items()
                   if layout.by_index.get(r.get("tile"), {}).get("area", 0) == area_no}
    positions_path = base / "positions.json"
    positions = read_json(positions_path, {})
    geo = read_json(out / f"{image_id}.geo.json")
    crop_x0, crop_y0 = ax + geo["originCell"]["x"] * scale, ay - geo["originCell"]["y"] * scale
    screen = {"w": layout.screen_w, "h": layout.screen_h}
    target = out / f"{manifest['id']}-elements"
    target.mkdir(exist_ok=True)
    # What the scene couldn't hide, as each source tile's shot with nothing shown has it (the
    # cliff doodads, unless switching the terrain off took them).
    residue = []
    for key, record in records.items():
        if record["kind"] == "empty" and frame_exists(folder / key):
            empty_shot = load_frame(folder / key)
            residue.append(float((empty_shot[:, layout.page_x0:].min(axis=2) < 200).mean()))
    if residue:
        log(f"elements: what couldn't be hidden: {max(residue) * 100:.2f}% of the pixels at most of {len(residue)} tiles' empty shots"
            f" ({sum(r > 0.0005 for r in residue)} with anything there)")

    by_id = {u["id"]: u for u in (manifest.get("elements") or {}).get("structures", [])}

    def place(key: str, record: dict) -> tuple[dict | None, dict | None, list[tuple[str, str]]]:
        """One element's state cut out and saved (a worker's part): its entry (no file: nothing
        there) or None, what masks() needs of it, and what to log (in order, by the caller). Only
        the part of the shots round its circle is matted (circle_box), the white level measured on
        the whole shot."""
        notes: list[tuple[str, str]] = []
        tile, shot = record["tile"], record.get("shot", key)
        if tile not in layout.placed or not frame_exists(folder / shot) or not frame_exists(folder / f"{shot}-black"):
            notes.append(("warn", f"  element {key}: its tile ({tile + 1}) isn't in the map, or its shots are missing; left out"))
            return None, None, notes
        planned = layout.by_index[tile]
        tile_camera = positions.get(str(tile)) or {"x": planned["x"], "y": planned["y"]}
        camera = record.get("camera") or tile_camera
        centre = screen_point(record, camera, screen, scale)
        radius = record["radius"] * scale
        white, black = load_frame(folder / shot), load_frame(folder / f"{shot}-black")
        level = light_level(white, black) or LIGHT_LEVEL
        cv0, cv1, cu0, cu1 = circle_box(centre, radius, white.shape)
        part = (slice(cv0, cv1), slice(cu0, cu1))

        def local(p: tuple[float, float]) -> tuple[float, float]:
            return p[0] - cu0, p[1] - cv0

        # What can't be hidden, as its tile's shot with everything hidden shows it (taken from the
        # same camera position, or it wouldn't line up).
        empty_record = records.get(f"empty-{tile}")
        empty = None
        if empty_record and frame_exists(folder / f"empty-{tile}"):
            empty_camera = empty_record.get("camera") or tile_camera
            if np.hypot(empty_camera["x"] - camera["x"], empty_camera["y"] - camera["y"]) * scale < 1:
                empty = load_frame(folder / f"empty-{tile}")[part]
        # A town structure's rubble: its town's moonwells' and hall's remains kept out (a hall's
        # fall brings its town's moonwells down too, and both their remains outlive the clearing
        # between waves: a moonwell's ring lay in its keep's shot), only what is nearer this one
        # than them its own.
        others = []
        this = by_id.get(record.get("element")) if record["kind"] == "structure" and record["state"] == "rubble" else None
        if this is not None and this.get("town") is not None:
            others = [local(screen_point(m, camera, screen, scale)) for m in by_id.values()
                      if m is not this and m.get("town") == this["town"] and m["type"].startswith(LINGERING_REMAINS)]
        rgba = cut_out(white[part], black[part], local(centre), radius, empty, others, level=level)
        alpha = rgba[..., 3]
        found = nonzero_box(alpha)
        entry = {"key": key, "kind": record["kind"], "element": record["element"], "state": record["state"], "image": image_id}
        if found is None:
            # Nothing left of it (a moonwell leaves no rubble): the state is "nothing".
            notes.append(("log", f"  element {key}: nothing there"))
            return {**entry, "file": None, "rect": None}, None, notes
        # In the whole shot's pixels.
        v0, v1, u0, u1 = cv0 + found[0], cv0 + found[1], cu0 + found[2], cu0 + found[3]
        # Reaching the circle's edge: the circle may have cut it short (a radius to widen).
        yy, xx = np.nonzero(alpha)
        if np.hypot(xx + cu0 - centre[0], yy + cv0 - centre[1]).max() > radius - 2:
            notes.append(("warn", f"  element {key}: its cut-out reaches the edge of its circle ({record['radius']:g} cells); it may be cut short"))
        x = layout.placed[tile][0] + u0 + (camera["x"] - tile_camera["x"]) * scale - crop_x0
        y = layout.placed[tile][1] + v0 - (camera["y"] - tile_camera["y"]) * scale - crop_y0
        mine = rgba[v0 - cv0:v1 - cv0, u0 - cu0:u1 - cu0]
        Image.fromarray(np.ascontiguousarray(mine), "RGBA").save(target / f"{key}.png", compress_level=PNG_COMPRESSION)
        kept = {"box": (v0, v1, u0, u1), "alpha": mine[..., 3].copy(), "shot": shot}
        placed_entry = {**entry, "file": f"{key}.png", "rect": [int(round(x)), int(round(y)), u1 - u0, v1 - v0]}
        # Its team colour (team_colour.py): the same cut-out from Chaos's side, over the same rectangle.
        chaos = record.get("chaos")
        if chaos and frame_exists(folder / chaos) and frame_exists(folder / f"{chaos}-black"):
            chaos_white, chaos_black = load_frame(folder / chaos), load_frame(folder / f"{chaos}-black")
            chaos_rgba = cut_out(chaos_white[part], chaos_black[part], local(centre), radius, empty, others,
                                 level=light_level(chaos_white, chaos_black) or level)
            mask = team_mask(mine, chaos_rgba[v0 - cv0:v1 - cv0, u0 - cu0:u1 - cu0])
            # Its own team's colour should be what changed; the other's: the shots were taken from
            # the wrong side (a switch not yet drawn: element_capture.pair_both waits for it).
            owner = (by_id.get(record.get("element")) or {}).get("owner")
            if mask is not None and owner and (changed := mask_team(mask)) and changed != owner:
                notes.append(("warn", f"  element {key}: {owner.capitalize()}'s, but its team colour changed as {changed.capitalize()}'s (shot from the wrong side)"))
            if mask is not None:
                rgb = np.dstack([mask, np.zeros(mask.shape[:2], np.uint8)])
                Image.fromarray(rgb, "RGB").save(target / f"{key}-team.png", compress_level=PNG_COMPRESSION)
                placed_entry["teamMask"] = f"{key}-team.png"
        return placed_entry, kept, notes

    def masks(entry: dict, record: dict) -> list[dict]:
        """A standing structure's masks, one for each neighbour that stands in front of it
        somewhere (element_capture.stand: its shot over black with that neighbour faded in beside
        it): its pixels the neighbour covers there, where the neighbour's own cut-out is solid
        (MASK_SOLID: a glow passing over it isn't in front of it). <key>-under-<id>.png, opaque
        where hidden, see-through elsewhere (the viewer erases with it)."""
        key, mine = entry["key"], cropped[entry["key"]]
        v0, v1, u0, u1 = mine["box"]
        alone = load_frame(folder / f"{mine['shot']}-black")[v0:v1, u0:u1].astype(np.int16)
        x, y, w, h = entry["rect"]
        found = []
        for n in record.get("neighbours") or []:
            other = by_key.get(f"structure-{n}-standing")
            if other is None or other.get("file") is None or not frame_exists(folder / f"{mine['shot']}-with-{n}"):
                continue
            beside = load_frame(folder / f"{mine['shot']}-with-{n}")[v0:v1, u0:u1].astype(np.int16)
            covered = (np.abs(beside - alone).max(axis=2) > MASK_CHANGE) & (mine["alpha"] > 8)
            # Where the neighbour's own cut-out lies over this one, in the map image's pixels.
            solid = np.zeros_like(covered)
            nx, ny, nw, nh = other["rect"]
            ox0, oy0, ox1, oy1 = max(x, nx), max(y, ny), min(x + w, nx + nw), min(y + h, ny + nh)
            if ox0 < ox1 and oy0 < oy1:
                theirs = cropped[other["key"]]["alpha"]
                solid[oy0 - y:oy1 - y, ox0 - x:ox1 - x] = theirs[oy0 - ny:oy1 - ny, ox0 - nx:ox1 - nx] >= MASK_SOLID
            hidden = covered & solid
            if hidden.sum() < MASK_LEAST:
                continue
            name = f"{key}-under-{n}.png"
            rgba = np.zeros((*hidden.shape, 4), np.uint8)
            rgba[..., 3] = hidden * 255
            Image.fromarray(rgba, "RGBA").save(target / name, compress_level=PNG_COMPRESSION)
            found.append({"id": n, "file": name})
        return found

    shots = [(key, record) for key, record in sorted(records.items()) if record["kind"] != "empty"]
    placed = []
    cropped: dict[str, dict] = {}
    # On every core (workers.ordered_map: in order, so the log reads as it did one at a time).
    with ui.bar(len(shots), "elements cut out") as advance:
        for (key, _), (entry, kept, notes) in zip(shots, ordered_map(lambda item: place(*item), shots)):
            for level, text in notes:
                (warn if level == "warn" else log)(text)
            if entry is not None:
                placed.append(entry)
            if kept is not None:
                cropped[key] = kept
            advance()
    # Where standing neighbours overlap: which is in front of which, pixel by pixel.
    by_key = {entry["key"]: entry for entry in placed}
    masked = 0
    to_mask = [entry for entry in placed if entry.get("file") and records[entry["key"]].get("neighbours")]
    for entry, found in zip(to_mask, ordered_map(lambda entry: masks(entry, records[entry["key"]]), to_mask)):
        entry["hiddenBy"] = found
        masked += len(found)
    if masked:
        log(f"elements: {masked} masks of where a standing neighbour is in front")
    index_path = out / f"{manifest['id']}-elements.json"
    earlier = read_json(index_path, {}).get("cutouts", []) if area_no else []  # the other arenas' (this run's first call writes afresh)
    write_json(index_path, {"image": image_id, "cutouts": earlier + placed}, indent=1)
    nothing = sum(1 for p in placed if p["file"] is None)
    log(f"elements: {len(placed) - nothing} cut-outs placed in {image_id}, {nothing} states with nothing there, {len(shots) - len(placed)} left out")
    return len(placed)
