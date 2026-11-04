"""The battlegrounds' part of the map script (script.galaxy here; map/script.py assembles the parts)
and its values: the structures and camps one at a time ("el ..."), and the holes the cores stand
on."""

from pathlib import Path

from heroes_capture.map.script import fixed

PART = Path(__file__).with_name("script.galaxy")


def values(hole_cells: list[tuple[int, int]], map_region: str, doodad_lines: str) -> dict:
    """hole_cells: the cells of the holes the cores stand on, filled in the map file (prepare.py):
    the script opens them again with TerrainShowRegion as it starts, so the map looks as the game
    draws it, and shows them on "el holes show" (the ground where a core stood). map_region: the
    whole map (map/script.map_values). doodad_lines: the capture's lines hiding the cloud layers,
    hidden again when "el env on" shows the doodads."""
    # "el env off|on" (the elements capture): all the terrain, every doodad and every unit in sight
    # hidden, so what is shown (a structure, a camp) stands alone over the sky, or all shown again
    # (the cores' holes opened again, the cloud layers hidden again).
    isolate_function = f"""void hrsCap_Isolate (bool lp_on, fixed lp_x, fixed lp_y, fixed lp_radius) {{
    if (lp_on) {{
        TerrainShowRegion({map_region}, false);
        hrsCap_DoodadsMessage("SetVisibility 0");
        hrsCap_IsolateUnits(lp_x, lp_y, lp_radius);
    }}
    else {{
        TerrainShowRegion({map_region}, true);
        hrsCap_OpenHoles();
        hrsCap_DoodadsMessage("SetVisibility 1");{doodad_lines.replace(chr(10) + "    ", chr(10) + "        ")}
        hrsCap_RestoreUnits();
    }}
}}
"""
    # The cores' holes as a region: a small circle on each cell's centre (the terrain switch goes by
    # cells; Blizzard's code builds regions with RegionAddCircle, never RegionAddRect).
    hole_lines = "".join(f"\n    RegionAddCircle(hrsCap_holes, true, Point({fixed(x + 0.5)}, {fixed(y + 0.5)}), 0.45);" for x, y in hole_cells)
    return {"isolate_function": isolate_function, "hole_lines": hole_lines, "has_holes": "true" if hole_cells else "false"}
