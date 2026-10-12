"""What a battleground adds to the stages (map/specifics.py): its script part (structures and camps
one at a time, the cores' holes), its elements read from the map when it is prepared, and with
--structures elements their capture before the tiles, their cut-outs after the stitch and their
atlases in the pack."""

from heroes_capture.capture.pack import Packing
from heroes_capture.capture.prepare import Preparation
from heroes_capture.capture.script import doodad_lines
from heroes_capture.capture.stitch import Stitched
from heroes_capture.categories.battleground import element_pack, element_stitch, elements, script
from heroes_capture.common.runlog import log, stage, warn
from heroes_capture.map.specifics import Specifics


def prepare(prep: Preparation) -> None:
    """The holes the cores stand on filled in the file, the elements and (for the elements render)
    each structure's copy's spare spot."""
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
        # Each structure's copy's spare spot (element_capture.py), under its own lighting.
        light = elements.lighting_map(archive.read_optional("LightingMap.tga"))
        spots = elements.copy_spots(found["structures"], found["camps"], prep.info["bounds"], prep.info["width"], light)
        for structure in found["structures"]:
            if structure["id"] in spots:
                structure["copy"] = spots[structure["id"]]
        log(f"copies: {len(spots)} structures' rubble prepared on spare spots"
            f"{' under the same lighting (the lighting map)' if light is not None else ''}; "
            f"{len(found['structures']) - len(spots)} fall themselves")
    # What the render cuts out of the map (ELEMENTS-PLAN.md): structures, towns, camps.
    prep.manifest["elements"] = found
    # Structures kept in the tiles are in their team's colours there: a tile that shows one is shot
    # from Chaos's side too (tiles.team_boxes).
    if prep.opts["structures"] == "keep":
        prep.team_coloured += [[u["x"], u["y"]] for u in found["structures"]]
    prep.values.update(script.values([c for h in holes for c in h["cells"]], prep.values["map_region"], doodad_lines(prep.hide_doodads)))


def before_tiles(session, manifest: dict, base) -> None:
    """Each structure and camp on its own, then every structure brought down: the tiles are the
    bare terrain (a resumed run only brings them down again)."""
    if manifest.get("structures") == "elements":
        from heroes_capture.categories.battleground import element_capture  # drives the game: Windows only

        element_capture.capture_elements(session, manifest, base)


def after_stitch(stitched: Stitched) -> None:
    if (stitched.base / "elements" / "elements.json").exists():
        with stage("elements"):
            if len(stitched.written) == 1:
                element_stitch.write_elements(stitched.manifest, stitched.base, stitched.out, stitched.layout, stitched.scale,
                                              stitched.ax, stitched.ay, stitched.written[0])
            else:
                warn("elements: a map of several areas isn't supported yet; its elements are left out")


def pack_data(packing: Packing) -> dict:
    found = element_pack.collect_elements(packing.out, packing.raw, packing.pack, packing.manifest, packing.layer_id, packing.small_factor)
    return {"elements": found} if found else {}


def extend(specifics: Specifics) -> None:
    specifics.script_parts.append(script.PART)
    specifics.prepare.append(prepare)
    specifics.before_tiles.append(before_tiles)
    specifics.after_stitch.append(after_stitch)
    specifics.pack_data.append(pack_data)
