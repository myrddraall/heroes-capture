"""The Galaxy script injected into a battleground: capture_script.galaxy with the map's values
filled in (inject.py appends it before InitMap and calls hrsCap_Init at InitMap's end). It skips
the map's intro cutscene, opens the gates early and cuts the map's opening timers short, reveals
the whole map, hides the HUD, removes units (and keeps removing them as they spawn), keeps or hides
structures, pauses the map's animations, sets the solid-colour skybox and points the camera
straight down. capture.py drives it through chat commands ("tile <n> <x> <y>", "clean", "black",
"quit", ...) and reads its status strip, drawn in the screen's top-left corner, to know when each
is done.

Galaxy is single-pass: every function must be defined before its first use (inject.py checks),
and one unknown native or syntax error stops the whole map script, which shows in the game as
every interface panel visible at once. The calls in the script are ones Blizzard's own Heroes
scripts make, except the lens values (field of view, far and near clip) and CameraSetBounds, which
are StarCraft II natives the game also accepts. Actor messages are plain strings the game ignores
when it doesn't know them, so they cannot break compilation.
"""

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from string import Template

import js_json

TEMPLATE = Path(__file__).with_name("capture_script.galaxy")

# The status strip (hrsCap_Status* in the script, read by status.py): cells and size.
STATUS_CELLS = 89  # 88 bits plus a fixed black cell at the top of the second column
STATUS_ROWS = 64  # cells per column; the strip continues in a second column to the right
STATUS_CELL_W = 25  # cell width in interface units (30 px at 1440 lines: 1.2 px per unit)
STATUS_CELL_H = 15  # cell height (18 px)


def fixed(n: float) -> str:
    """A Galaxy fixed literal: whole numbers as "48.0", others to four places (as JavaScript's
    toFixed rounds: halves away from zero)."""
    if float(n).is_integer():
        return js_json.number(n) + ".0"
    return str(Decimal(n).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def capture_script(
    *,
    tiles: list[dict],
    hide_structures: bool,
    distance: float,
    pitch: float = 90,
    refit_yaw: float = 180,
    lens: dict | None,
    unbound: bool,
    show_ui: bool,
    sky_colour: str = "white",
    map_sky: dict | None = None,
    map_width: int = 256,
    map_height: int = 256,
    opening_timers: list[str] = (),
    map_id: int = 0,
    hide_doodads: list[str] = (),
    keep_intro: bool = False,
    arena: bool = False,
) -> str:
    """The script for one prepared map.

    tiles: camera targets, in map cells. hide_structures: hide structures and map-mechanic units
    too. distance: the camera's distance from its target. pitch: degrees (90 is straight down).
    refit_yaw: yaw of the lighting-refit look before each tile (towards the map's main light).
    lens: {fov, farClip} for a narrow field of view; None leaves the map's. unbound: lift the
    map's camera bounds so edge tiles are not clamped (and, on a map that is several arenas in one,
    so the camera can reach every arena); lifted before each tile's first pan, since the map may
    put its own bounds back at any time. show_ui: leave the HUD up (diagnostic). sky_colour: the
    sky each tile starts under, white (then "black" for the matte's second shot) or black.
    map_width, map_height: the map's size in cells (MapInfo), for the reveal: RegionEntireMap() is
    only the playable area. opening_timers: the map's timers between the gates and its first
    objective (opening-timers.json), cut short so the objective is in place before the tiles.
    map_sky: the map's own sky models {fixed, parallax}, for the probe commands "sky mapsky" and
    "sky mapparallax". map_id: the prepared map's identity, 0..65535, shown in the status strip.
    hide_doodads: doodad types to hide (cloud layers placed in the map as doodads). keep_intro:
    let the intro cutscene play out (diagnostic). arena: the map plays rounds (its script includes
    LibAREN: Punisher Arena), so a core killed ends only the round, and "quit" first gives the
    other team all but its last round win.
    """
    map_sky = map_sky or {"fixed": None, "parallax": None}
    if lens:
        lens_lines = f"""
    CameraSetValue(lp_player, c_cameraValueFieldOfView, {fixed(lens["fov"])}, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueFarClip, {fixed(lens["farClip"])}, 0.0, -1, 10.0);
    // The near clip well out from the game's tiny default: depth precision goes with the
    // far/near ratio, and at this distance flat decals on the ground (road trim, cracks, the
    // decorations lying on surfaces) were z-fighting the ground and losing in patches.
    CameraSetValue(lp_player, c_cameraValueNearClip, 5.0, 0.0, -1, 10.0);"""
    else:
        lens_lines = """
    // No lens: the game's own clip planes (a sky shot's "hidemap" moved them).
    CameraSetValue(lp_player, c_cameraValueNearClip, CameraInfoGetValue(CameraInfoDefault(), c_cameraValueNearClip), 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueFarClip, CameraInfoGetValue(CameraInfoDefault(), c_cameraValueFarClip), 0.0, -1, 10.0);"""
    bounds_line = """
    CameraSetBounds(PlayerGroupAll(), RegionEntireMap(), false);""" if unbound else ""
    # The whole map, not RegionEntireMap() (only the playable area), for the reveal and for pausing
    # every animation the way Blizzard's own maps do at game over (Trial Grounds: one actor message
    # to the whole-map region, no filter; filter terms broke the map's script).
    map_region = f"RegionRect(-16.0, -16.0, {fixed(map_width + 16)}, {fixed(map_height + 16)})"
    hero_ui_lines = "" if show_ui else """            libUIUI_gf_UIHeroConsoleShowHideForPlayer(false, lv_p);
            libUIUI_gf_UIGameUIShowHideConsolePanelForPlayer(false, lv_p);
            libUIUI_gf_UIHeroTrackerArrowShowHideForPlayer(false, lv_p);
"""
    ui_lines = "" if show_ui else """    UISetMode(PlayerGroupAll(), c_uiModeFullscreen, c_transitionDurationImmediate);
    // Every UI frame type, as cinematic mode does (the intro's exit re-shows whatever was
    // visible when it started, debug panels included); text tags are left alone.
    lv_f = c_syncFrameTypeFirst;
    for ( ; lv_f <= c_syncFrameTypeLast ; lv_f += 1 ) {
        if ((lv_f != c_syncFrameTypeTextTag)) {
            UISetFrameVisible(PlayerGroupAll(), lv_f, false);
        }
    }"""
    doodad_lines = "".join(
        f'\n    libNtve_gf_ShowHideDoodadsInRegion(false, RegionEntireMap(), "{kind}");  // a cloud layer, placed as doodads'
        for kind in hide_doodads
    )
    arena_quit_line = ("    libAREN_gv_aRM_RoundScore[libGame_gf_EnemyTeam(libGame_gf_TeamNumberOfPlayer(EventPlayer()))]"
                       " = libAREN_gv_victoriesCount - 1;\n") if arena else ""
    values = {
        "hide_structures": "true" if hide_structures else "false",
        "distance": fixed(distance),
        "pitch": fixed(pitch),
        "tile_count": str(len(tiles)),
        "map_sky": map_sky.get("fixed") or "",
        "map_parallax": map_sky.get("parallax") or "",
        "forward_seconds": "8.0" if opening_timers else "0.0",
        "status_rows": str(STATUS_ROWS),
        "status_cells": str(STATUS_CELLS),
        "status_cell_w": str(STATUS_CELL_W),
        "status_cell_h": str(STATUS_CELL_H),
        "map_id": str(map_id),
        "tile_lines": "\n".join(
            f"    hrsCap_tileX[{i}] = {fixed(t['x'])}; hrsCap_tileY[{i}] = {fixed(t['y'])};" for i, t in enumerate(tiles)
        ),
        "map_width": fixed(map_width),
        "map_height": fixed(map_height),
        "lens_lines": lens_lines,
        "refit_yaw": fixed(refit_yaw),
        "map_region": map_region,
        "hero_ui_lines": hero_ui_lines,
        "ui_lines": ui_lines,
        "bounds_line": bounds_line,
        "sky_model": "Black" if sky_colour == "black" else "White",
        "sky_state": "2" if sky_colour == "black" else "1",
        "doodad_lines": doodad_lines,
        "cut_short_lines": "\n".join(f"        hrsCap_CutShort({t});" for t in opening_timers),
        "skip_intro_line": "" if keep_intro else "    hrsCap_SkipIntro();\n",
        "arena_quit_line": arena_quit_line,
    }
    return Template(TEMPLATE.read_text(encoding="utf-8")).substitute(values)
