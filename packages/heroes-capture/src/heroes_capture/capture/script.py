"""The capture's part of the map script (script.galaxy here; map/script.py assembles the parts) and
its values. It skips the map's intro cutscene, opens the gates early and cuts the map's opening
timers short, reveals the whole map, hides the HUD, removes units (and keeps removing them as they
spawn), keeps or hides structures, pauses the map's animations, sets the solid-colour skybox and
points the camera straight down; "tile <n> <x> <y>;", "clean;", "black;", "sky ...;" and the rest
move the camera and swap the sky (tiles.py sends them, and reads the status strip to know when
each is done)."""

from pathlib import Path

from heroes_capture.capture.sky import LIGHT_SKY, model_id
from heroes_capture.map.script import fixed

PART = Path(__file__).with_name("script.galaxy")

# The map's event off before the gates open, on a map whose objective isn't brought in for the
# tiles (no opening timers cut short): Battlefield of Eternity's Immortals fought in the middle of
# the tiles, depending on the timing. MapMechanicsLib's own switch, which a map's event checks when
# the gates open (and the map's own disable trigger run, where it has one).
EVENT_OFF_LINES = """    if (!libGame_gv_gameStarted && !hrsCap_eventOff) {
        hrsCap_eventOff = true;
        libMapM_gf_EnableDisableMapEvent(false);
    }
"""


def doodad_lines(hide_doodads: list[str]) -> str:
    """The lines hiding each doodad type: cloud layers placed in the map as doodads."""
    return "".join(f'\n    libNtve_gf_ShowHideDoodadsInRegion(false, RegionEntireMap(), "{kind}");  // a cloud layer, placed as doodads'
                   for kind in hide_doodads)


def values(
    *,
    tiles: list[dict],
    hide_structures: bool,
    distance: float,
    pitch: float,
    refit_yaw: float,
    lens: dict | None,
    unbound: bool,
    show_ui: bool,
    sky_colour: str = LIGHT_SKY,
    map_sky: dict | None = None,
    opening_timers: list[str] = (),
    hide_doodads: list[str] = (),
    keep_intro: bool = False,
) -> dict:
    """The capture part's values.

    tiles: camera targets, in map cells. hide_structures: hide structures and map-mechanic units
    too. distance: the camera's distance from its target. pitch: degrees (90 is straight down).
    refit_yaw: yaw of the lighting-refit look before each tile (towards the map's main light).
    lens: {fov, farClip} for a narrow field of view; None leaves the map's. unbound: lift the
    map's camera bounds so edge tiles are not clamped (and, on a map that is several arenas in one,
    so the camera can reach every arena); lifted before each tile's first pan, since the map may
    put its own bounds back at any time. show_ui: leave the HUD up (diagnostic). sky_colour: the
    sky each tile starts under, the light sky (then "black" for the matte's second shot) or black.
    map_sky: the map's own sky models {fixed, parallax}, for the commands "sky mapsky" and
    "sky mapparallax" (the sky layers' shots). opening_timers: the map's timers between the gates
    and its first objective (its own module, maps/), cut short so the objective is in place before
    the tiles; a map without any has its event switched off instead (EVENT_OFF_LINES).
    hide_doodads: doodad types to hide (cloud layers placed in the map as doodads). keep_intro:
    let the intro cutscene play out (diagnostic).
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
    return {
        "hide_structures": "true" if hide_structures else "false",
        "distance": fixed(distance),
        "pitch": fixed(pitch),
        "tile_count": str(len(tiles)),
        "map_sky": map_sky.get("fixed") or "",
        "map_parallax": map_sky.get("parallax") or "",
        "forward_seconds": "8.0" if opening_timers else "0.0",
        "tile_lines": "\n".join(f"    hrsCap_tileX[{i}] = {fixed(t['x'])}; hrsCap_tileY[{i}] = {fixed(t['y'])};" for i, t in enumerate(tiles)),
        "lens_lines": lens_lines,
        "refit_yaw": fixed(refit_yaw),
        "hero_ui_lines": hero_ui_lines,
        "ui_lines": ui_lines,
        "bounds_line": """
    CameraSetBounds(PlayerGroupAll(), RegionEntireMap(), false);""" if unbound else "",
        "sky_model": model_id(sky_colour),
        "sky_state": "2" if sky_colour == "black" else "1",
        "light_sky": LIGHT_SKY,
        "light_sky_model": model_id(LIGHT_SKY),
        "doodad_lines": doodad_lines(hide_doodads),
        "cut_short_lines": "\n".join(f"        hrsCap_CutShort({t});" for t in opening_timers),
        "event_off_lines": "" if opening_timers else EVENT_OFF_LINES,
        "skip_intro_line": "" if keep_intro else "    hrsCap_SkipIntro();\n",
    }
