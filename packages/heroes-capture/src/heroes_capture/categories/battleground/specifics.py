"""What a battleground adds to the stages (map/specifics.py): its script part (structures and camps
one at a time, the cores' holes), its elements read from the map when it is prepared, and with
--structures elements their capture before the tiles, their cut-outs after the stitch and their
atlases in the pack."""

import math
import re
from pathlib import Path

from heroes_capture.capture.pack import Packing
from heroes_capture.capture.prepare import Preparation
from heroes_capture.capture.script import doodad_lines
from heroes_capture.capture.sky import append_to_catalog
from heroes_capture.capture.stitch import Stitched
from heroes_capture.categories.battleground import element_pack, element_stitch, elements, script
from heroes_capture.common import actors, game_data
from heroes_capture.common.jsonio import read_json
from heroes_capture.common.runlog import log, stage
from heroes_capture.map.specifics import Specifics

# The stacking probe's sink: the site operation's id, and how far below the ground it draws a copy.
SINK_OP, SINK_DEPTH = "HrsSink", 30.0
# Candidate spots kept for each copy, for the calibration to try: the NEAR_CANDIDATES nearest, then
# up to FAR_CANDIDATES more, each at least CANDIDATE_SPACING cells from the others kept (the 40
# nearest alone lay within a few cells of one another: one accepted copy's rubble ruled them all
# out, and 48 of Dragon Shire's 82 structures had none left to try; with these, 59 of the 63 its
# camera bounds have room for at once).
NEAR_CANDIDATES, FAR_CANDIDATES, CANDIDATE_SPACING = 20, 100, 6.0


def spread_candidates(candidates: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """A structure's candidates to try (nearest first): its nearest, then others spread out."""
    far: list[tuple[float, float]] = []
    for p in candidates[NEAR_CANDIDATES:]:
        if len(far) >= FAR_CANDIDATES:
            break
        if all(math.dist(p, q) >= CANDIDATE_SPACING for q in far):
            far.append(p)
    return list(candidates[:NEAR_CANDIDATES]) + far


def prepare(prep: Preparation) -> None:
    """The holes the cores stand on filled in the file, the elements and (for the elements render)
    each structure's copy's spot: the map's calibration's, or a spare one; for the calibration,
    the candidates."""
    archive = prep.archive
    # The holes in the terrain the cores stand on (their centres show the sky through), filled in
    # the file so the ground is there when a core is gone; the script opens them again over exactly
    # those cells as it starts (and shows them on "el holes show").
    holes = []
    if archive.has("t3CellFlags"):
        cell_flags, holes = elements.fill_structure_holes(archive.read("t3CellFlags"), prep.objects)
        if holes:
            archive.write("t3CellFlags", cell_flags)
            log("terrain holes under structures filled in the file, opened by the script: "
                + ", ".join(f"{h['type']} at ({h['x']:g}, {h['y']:g}), {len(h['cells'])} cells" for h in holes))
    found = elements.element_list(prep.map_script, prep.objects, prep.regions)
    log(f"elements: {len(found['structures'])} structures in {len(found['towns'])} towns, {len(found['camps'])} camps")
    if prep.opts["structures"] == "elements":
        light = elements.lighting_map(archive.read_optional("LightingMap.tga"))
        if prep.opts["calibrate"]:
            # The stacking probe's sink: a copy's model drawn this far below where it stands (a site
            # operation, as Blizzard's actors offset theirs: CActorSiteOpLocalOffset).
            actors = archive.read_text("Base.StormData\\GameData\\ActorData.xml")
            archive.write("Base.StormData\\GameData\\ActorData.xml",
                          append_to_catalog(actors, [f'    <CActorSiteOpLocalOffset id="{SINK_OP}"><LocalOffset Z="-{SINK_DEPTH:g}"/></CActorSiteOpLocalOffset>']).encode("utf-8"))
            # The calibration's candidates for each copy's spot (calibrate.py tries them in turn),
            # the placed doodads (what moves while paused is named by them), and the data report:
            # what each structure and doodad is made of (common/actors.py).
            candidates = elements.copy_candidates(found["structures"], found["camps"], prep.info["bounds"], prep.info["width"], light)
            found["copyCandidates"] = {str(k): [list(p) for p in spread_candidates(v)] for k, v in candidates.items()}
            placed = elements.parse_objects(prep.objects)[1]
            found["doodads"] = [{"type": d["type"], "x": d["x"], "y": d["y"]} for d in placed]
            found["report"] = data_report(prep, sorted({u["type"] for u in found["structures"]}), sorted({d["type"] for d in placed}))
            moving = [t for t, m in found["report"]["doodads"].items() if m["particles"] or m["ribbons"] or m["lights"]]
            log(f"calibration: candidate spots for {len(candidates)} structures' copies; {len(found['report']['structures'])} structure "
                f"types' actors and {len(placed)} doodads of {len(found['report']['doodads'])} types read "
                f"({len(moving)} types with particles, ribbons or lights)")
        elif prep.opts["calibration"]:
            # What the map's structures are made of (common/actors.py): each one's finished and death
            # models, whether they draw team colour (none: not shot again from Chaos's side) and how far
            # their meshes reach (two standing apart further than theirs can't overlap on screen).
            found["report"] = data_report(prep, sorted({u["type"] for u in found["structures"]}), [])
            for structure in found["structures"]:  # its name as the game shows it, for the run's log
                structure["name"] = found["report"]["names"].get(structure["type"], structure["type"])
            # Each structure's copy's spot, whether it changes with the side, and each kind's fall
            # wait, as the map's reviewed calibration has them (calibrate.py; VERIFICATION.md).
            calibration = read_json(Path(prep.opts["calibration"]))
            spots = elements.calibrated_copy_spots(found["structures"], calibration)
            measured = calibration.get("structures") or {}
            for structure in found["structures"]:
                if structure["id"] in spots:
                    structure["copy"] = spots[structure["id"]]
                if str(structure["id"]) in measured:
                    structure["changesSide"] = measured[str(structure["id"])]["changes"]
                # Its kind's rubble circle, measured from its debris in the calibration.
                radius = (calibration.get("rubbleRadii") or {}).get(f"{structure['type']}|{structure.get('owner')}")
                if radius:
                    structure["rubbleRadius"] = radius
            prep.manifest["fallWaits"] = calibration.get("fallWaits") or {}
            prep.manifest["standWaits"] = calibration.get("standWaits") or {}
            prep.manifest["standModels"] = calibration.get("standModels") or {}
            log(f"copies: {len(spots)} structures' rubble prepared on the spots the calibration checked; "
                f"{len(found['structures']) - len(spots)} fall themselves; "
                f"{len(prep.manifest['fallWaits'])} kinds' fall waits and {len(prep.manifest['standWaits'])} standing times picked in its review")
        else:
            # Each structure's copy's spare spot (element_capture.py), under its own lighting; unchecked.
            spots = elements.copy_spots(found["structures"], found["camps"], prep.info["bounds"], prep.info["width"], light)
            for structure in found["structures"]:
                if structure["id"] in spots:
                    structure["copy"] = spots[structure["id"]]
            log(f"copies: {len(spots)} structures' rubble prepared on spare spots"
                f"{' under the same lighting (the lighting map)' if light is not None else ''}, not calibrated; "
                f"{len(found['structures']) - len(spots)} fall themselves")
    # What the render cuts out of the map (ELEMENTS-PLAN.md): structures, towns, camps.
    prep.manifest["elements"] = found
    # Structures kept in the tiles are in their team's colours there: a tile that shows one is shot
    # from Chaos's side too (tiles.team_boxes).
    if prep.opts["structures"] == "keep":
        prep.team_coloured += [[u["x"], u["y"]] for u in found["structures"]]
    prep.values.update(script.values([c for h in holes for c in h["cells"]], prep.values["map_region"], doodad_lines(prep.hide_doodads)))


def data_report(prep: Preparation, structure_types: list[str], doodad_types: list[str]) -> dict:
    """What the map's structures and doodads are made of (common/actors.py), from the game's data
    and the map's own."""
    mods = re.findall(r"([A-Za-z0-9_]+\.stormmod)", prep.archive.read_text("DocumentInfo") or "")
    own = [prep.archive.read_text(f"Base.StormData\\GameData\\{name}.xml") or "" for name in ("ActorData", "ModelData")]
    with game_data.game_storage() as storage:
        return actors.report(storage, mods, own, structure_types, doodad_types, prep.archive.read_text(game_data.GAMESTRINGS) or "")


def before_tiles(session, manifest: dict, base) -> None:
    """Each structure and camp on its own, then every structure brought down: the tiles are the
    bare terrain (a resumed run only brings them down again)."""
    if manifest.get("structures") == "elements":
        from heroes_capture.categories.battleground import element_capture  # drives the game: Windows only

        with stage("Elements"):
            element_capture.capture_elements(session, manifest, base)


# The calibration's stages (calibrate.py), each runnable on its own (`map calibrate --only`), with
# its version: bump one when what it measures, or how, changes, and only that stage is out of date.
CALIBRATION_STAGES = {"motion": 1, "structures": 1, "rubble-at-home": 1, "standing": 1}
# Its probes: run only when named, for what a render doesn't need (pausing: which ways of pausing
# still what the map's own pause misses; stacking: copies sunk out of view under one camera and
# brought up one at a time).
CALIBRATION_PROBES = {"pausing": 11, "stacking": 1}


def calibrate(session, manifest: dict, base, stages: list[str], previous: dict) -> dict:
    """The named calibration stages measured in the game (calibrate.py)."""
    from heroes_capture.categories.battleground import calibrate as calibration  # drives the game: Windows only

    return calibration.calibrate(session, manifest, base, stages, previous)


def after_stitch(stitched: Stitched) -> None:
    """The elements placed on the map image; on a map of several arenas, each arena's on its own
    image (the images in the areas' order, as stitch.write_outputs writes them)."""
    if (stitched.base / "elements" / "elements.json").exists():
        with stage("Placing the elements"):
            several = len(stitched.written) > 1
            for area_no, image_id in enumerate(stitched.written):
                element_stitch.write_elements(stitched.manifest, stitched.base, stitched.out, stitched.layout, stitched.scale,
                                              stitched.ax, stitched.ay, image_id, area_no if several else None)


def pack_data(packing: Packing) -> dict:
    found = element_pack.collect_elements(packing.out, packing.raw, packing.pack, packing.manifest, packing.layer_id, packing.small_factor,
                                          packing.layers)
    return {"elements": found} if found else {}


def extend(specifics: Specifics) -> None:
    specifics.script_parts.append(script.PART)
    specifics.prepare.append(prepare)
    specifics.before_tiles.append(before_tiles)
    specifics.calibrate.append(calibrate)
    specifics.calibration_stages.update(CALIBRATION_STAGES)
    specifics.calibration_probes.update(CALIBRATION_PROBES)
    specifics.after_stitch.append(after_stitch)
    specifics.pack_data.append(pack_data)
