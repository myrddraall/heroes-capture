/**
 * The Galaxy script injected into a battleground (inject.mjs appends it before InitMap and
 * calls hrsCap_Init at InitMap's end). It skips the map's intro cutscene, opens the gates early
 * and cuts the map's opening timers short, reveals the whole map, hides the HUD, removes units
 * (and keeps removing them as they spawn), keeps or hides structures, pauses the map's
 * animations, sets the solid-colour skybox and points the camera straight down. capture.py
 * drives it through chat commands ("tile <n> <x> <y>", "clean", "black", "quit", ...) and reads
 * its status strip, drawn in the screen's top-left corner, to know when each is done.
 *
 * Galaxy is single-pass: every function must be defined before its first use (inject.mjs
 * checks), and one unknown native or syntax error stops the whole map script, which shows in
 * the game as every interface panel visible at once. The calls below are ones Blizzard's own
 * Heroes scripts make, except the lens values (field of view, far and near clip) and
 * CameraSetBounds, which are StarCraft II natives the game also accepts. Actor messages are
 * plain strings the game ignores when it doesn't know them, so they cannot break compilation.
 */

const fixed = (n) => (Number.isInteger(n) ? `${n}.0` : n.toFixed(4));

/** The status strip (hrsCap_Status* below, read by status.py): cells and size. */
export const STATUS_CELLS = 89; // 88 bits plus a fixed black cell at the top of the second column
export const STATUS_ROWS = 64; // cells per column; the strip continues in a second column to the right
export const STATUS_CELL_W = 25; // cell width in interface units (30 px at 1440 lines: 1.2 px per unit)
export const STATUS_CELL_H = 15; // cell height (18 px)

/**
 * @param {object} o
 * @param {{ x: number, y: number }[]} o.tiles Camera targets, in map cells.
 * @param {boolean} o.hideStructures Hide structures and map-mechanic units too.
 * @param {number} o.distance Camera distance from its target.
 * @param {number} [o.pitch] Camera pitch in degrees (90 is straight down).
 * @param {number} [o.refitYaw] Yaw of the lighting-refit look before each tile (towards the
 *   map's main light).
 * @param {{ fov: number, farClip: number } | null} o.lens Narrow field of view; null to leave the map's.
 * @param {boolean} o.unbound Lift the map's camera bounds so edge tiles are not clamped (and, on a
 *   map that is several arenas in one, so the camera can reach every arena). Lifted before each
 *   tile's first pan, since the map may put its own bounds back at any time.
 * @param {boolean} o.showUi Leave the HUD up (diagnostic: to check what the HUD hiding affects).
 * @param {string} o.skyColour The sky each tile starts under: white (then "black" for the
 *   matte's second shot) or black.
 * @param {number} o.mapWidth Map size in cells (MapInfo), for the reveal: RegionEntireMap() is only the playable area.
 * @param {number} o.mapHeight
 * @param {number} o.mapId The prepared map's identity, 0..65535, shown in the status strip.
 * @param {string[]} o.openingTimers The map's timers between the gates and its first objective
 *   (opening-timers.json), cut short so the objective is in place before the tiles.
 * @param {{ fixed: string | null, parallax: string | null }} [o.mapSky] The map's own sky models,
 *   for the probe commands "sky mapsky" and "sky mapparallax".
 * @param {string[]} o.hideDoodads Doodad types to hide (cloud layers placed in the map as doodads).
 * @param {boolean} [o.keepIntro] Let the intro cutscene play out instead of skipping it
 *   (diagnostic: does skipping it leave the map's lighting half changed?).
 * @param {boolean} [o.arena] The map plays rounds (its script includes LibAREN: Punisher Arena):
 *   a core killed ends only the round, so "quit" first gives the other team all but its last
 *   round win.
 */
export function captureScript({
  tiles,
  hideStructures,
  distance,
  pitch = 90,
  refitYaw = 180,
  lens,
  unbound,
  showUi,
  skyColour = 'white',
  mapSky = { fixed: null, parallax: null },
  mapWidth = 256,
  mapHeight = 256,
  openingTimers = [],
  mapId = 0,
  hideDoodads = [],
  keepIntro = false,
  arena = false,
}) {
  const n = tiles.length;
  const tileLines = tiles
    .map((t, i) => `    hrsCap_tileX[${i}] = ${fixed(t.x)}; hrsCap_tileY[${i}] = ${fixed(t.y)};`)
    .join('\n');
  const lensLines = lens
    ? `
    CameraSetValue(lp_player, c_cameraValueFieldOfView, ${fixed(lens.fov)}, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueFarClip, ${fixed(lens.farClip)}, 0.0, -1, 10.0);
    // The near clip well out from the game's tiny default: depth precision goes with the
    // far/near ratio, and at this distance flat decals on the ground (road trim, cracks, the
    // decorations lying on surfaces) were z-fighting the ground and losing in patches.
    CameraSetValue(lp_player, c_cameraValueNearClip, 5.0, 0.0, -1, 10.0);`
    : `
    // No lens: the game's own clip planes (a sky shot's "hidemap" moved them).
    CameraSetValue(lp_player, c_cameraValueNearClip, CameraInfoGetValue(CameraInfoDefault(), c_cameraValueNearClip), 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueFarClip, CameraInfoGetValue(CameraInfoDefault(), c_cameraValueFarClip), 0.0, -1, 10.0);`;
  const boundsLine = unbound
    ? `
    CameraSetBounds(PlayerGroupAll(), RegionEntireMap(), false);`
    : '';
  // The whole map, not RegionEntireMap() (only the playable area), for the reveal and for
  // pausing every animation the way Blizzard's own maps do at game over (Trial Grounds: one
  // actor message to the whole-map region, no filter; filter terms broke the map's script).
  const mapRegion = `RegionRect(-16.0, -16.0, ${fixed(mapWidth + 16)}, ${fixed(mapHeight + 16)})`;
  const heroUiLines = showUi
    ? ''
    : `            libUIUI_gf_UIHeroConsoleShowHideForPlayer(false, lv_p);
            libUIUI_gf_UIGameUIShowHideConsolePanelForPlayer(false, lv_p);
            libUIUI_gf_UIHeroTrackerArrowShowHideForPlayer(false, lv_p);
`;
  const uiLines = showUi
    ? ''
    : `    UISetMode(PlayerGroupAll(), c_uiModeFullscreen, c_transitionDurationImmediate);
    // Every UI frame type, as cinematic mode does (the intro's exit re-shows whatever was
    // visible when it started, debug panels included); text tags are left alone.
    lv_f = c_syncFrameTypeFirst;
    for ( ; lv_f <= c_syncFrameTypeLast ; lv_f += 1 ) {
        if ((lv_f != c_syncFrameTypeTextTag)) {
            UISetFrameVisible(PlayerGroupAll(), lv_f, false);
        }
    }`;
  const skyModel = skyColour === 'black' ? 'Black' : 'White';
  const skyState = skyColour === 'black' ? 2 : 1;
  const doodadLines = hideDoodads
    .map(
      (type) => `
    libNtve_gf_ShowHideDoodadsInRegion(false, RegionEntireMap(), "${type}");  // a cloud layer, placed as doodads`,
    )
    .join('');

  return `//--------------------------------------------------------------------------------------------------
// Map capture (injected by heroes-replay-stats tools/map-capture)
// Chat "tile <n> <x> <y>" moves the camera to tile n; the status strip reports when it is done.
//--------------------------------------------------------------------------------------------------
const bool hrsCap_hideStructures = ${hideStructures};
const fixed hrsCap_distance = ${fixed(distance)};
const fixed hrsCap_pitch = ${fixed(pitch)};
const int hrsCap_tileCount = ${n};
int hrsCap_currentTile = 0;
fixed hrsCap_curX = 0.0;  // the current tile's camera target (from the command, else the table)
fixed hrsCap_curY = 0.0;
fixed[${n}] hrsCap_tileX;
fixed[${n}] hrsCap_tileY;
trigger hrsCap_gt_Tile;
trigger hrsCap_gt_Clean;
trigger hrsCap_gt_Black;
trigger hrsCap_gt_Sky;
trigger hrsCap_gt_Pause;
trigger hrsCap_gt_Move;
trigger hrsCap_gt_Look;
trigger hrsCap_gt_Clip;
trigger hrsCap_gt_BgSpeed;
trigger hrsCap_gt_Unbound;
trigger hrsCap_gt_RefitWait;
trigger hrsCap_gt_HideMap;
trigger hrsCap_gt_KeyBlack;
trigger hrsCap_gt_Quiet;
fixed hrsCap_refitWait = 0.1;  // real seconds the lighting-refit look is held before each tile (chat "refitwait"; 0.25 until the waits probe showed shorter changes nothing but the effects that animate anyway)
bool hrsCap_unbound = false;  // chat "unbound": the camera may go anywhere on the map (tiles past the camera bounds)
trigger hrsCap_gt_Quit;
trigger hrsCap_gt_Sweep;
trigger hrsCap_gt_FastForward;

// The map's state.
int hrsCap_skyState = 0;  // 1 white, 2 black, 0 other
fixed hrsCap_bgSpeed = 100.0;  // animation speed (percent) "sky" gives a background; chat "bgspeed"
const string hrsCap_mapSky = "${mapSky.fixed || ''}";  // the map's own sky models (probes)
const string hrsCap_mapParallax = "${mapSky.parallax || ''}";
bool hrsCap_paused = false;  // the map's animations have been paused (once the game started)
bool hrsCap_forwardStarted = false;  // the fast-forward after the gates opened has begun
bool hrsCap_ready = false;  // fast-forward done: the map's opening events have happened
bool hrsCap_leaving = false;  // "quit" received: the match is ending
int hrsCap_clearedSinceReady = 0;  // units the clearing removed after the map was ready
bool hrsCap_introOn = false;  // the map's intro cutscene is playing (set by the sweep)
int hrsCap_openingCuts = 0;  // opening timers cut short so far
const fixed hrsCap_gatesDelay = 3.0;  // seconds to the gates opening (the game's default is 35)
const fixed hrsCap_forwardSeconds = ${openingTimers.length ? '8.0' : '0.0'};  // real seconds the map's opening timers are kept short (none known for this map: no wait)
const fixed hrsCap_readySeconds = 18.0;  // real seconds from the gates opening to the map ready and paused, fast-forward included: the cores' entrance animation hadn't finished at 10

//--------------------------------------------------------------------------------------------------
// The status strip: a dialog in the top-left corner, two columns of ${STATUS_ROWS} black-or-white cells
// (wide and short), redrawn at the end of every chat command and every sweep. The capture reads
// it from the screen far faster than it can judge the picture, so it knows when a command has
// been carried out and rendered. Cells: 0 white, 1 black (locator), then bits: command sequence
// number (8), game clock since the gates opened in seconds (10), camera target x and y in 1/64
// cells (15 each), unused (1), sky white, sky black, tick (flips every sweep), ready (the gates
// have opened and the map's opening events are in place), the map's identity (16 bits, a hash of
// its prepared id: the capture checks it is driving the map it prepared), the map's phase (2
// bits: 0 before the gates open, 1 the opening timers being cut short, 2 ready, 3 leaving),
// units cleared since ready (8: something spawning mid-render), intro cutscene playing (1),
// opening timers cut short so far (4), parity of all bits, and a black end cell.
//--------------------------------------------------------------------------------------------------
const int hrsCap_statusCells = ${STATUS_CELLS};
const int hrsCap_statusCellW = ${STATUS_CELL_W};  // cell size in interface units
const int hrsCap_statusCellH = ${STATUS_CELL_H};
const int hrsCap_statusRows = ${STATUS_ROWS};
const int hrsCap_mapId = ${mapId};  // the prepared map's identity (inject.mjs)
int hrsCap_status = c_invalidDialogId;
int[${STATUS_CELLS}] hrsCap_statusCtl;
int hrsCap_statusBackdrop = c_invalidDialogControlId;
int hrsCap_statusBackdropDialog = c_invalidDialogId;
int hrsCap_statusFiller = c_invalidDialogControlId;
int hrsCap_seq = 0;
int hrsCap_tick = 0;
int hrsCap_statusParity = 0;
int hrsCap_statusPlayer = 1;

int hrsCap_LocalPlayer () {
    int lv_p;

    lv_p = 1;
    for ( ; lv_p <= 10 ; lv_p += 1 ) {
        if ((PlayerStatus(lv_p) == c_playerStatusActive)) {
            return lv_p;
        }
    }
    return 1;
}

// Cell lp_cell of the strip's 88 (logical): the second column starts with a fixed black cell
// (so the locator's white cell can't run into a white cell beside it), so cells from the
// column's height on sit one further down.
void hrsCap_StatusCell (int lp_cell, int lp_bit) {
    int lv_c;

    lv_c = lp_cell;
    if ((lv_c >= hrsCap_statusRows)) {
        lv_c += 1;
    }
    if ((lp_bit != 0)) {
        DialogControlSetPropertyAsColor(hrsCap_statusCtl[lv_c], c_triggerControlPropertyColor, PlayerGroupAll(), Color(100.0, 100.0, 100.0));
    }
    else {
        DialogControlSetPropertyAsColor(hrsCap_statusCtl[lv_c], c_triggerControlPropertyColor, PlayerGroupAll(), Color(0.0, 0.0, 0.0));
    }
}

// Bits of a value, least significant first, into cells from lp_start; the parity keeps up.
void hrsCap_StatusBits (int lp_start, int lp_count, int lp_value) {
    int lv_i;
    int lv_bit;

    lv_i = 0;
    for ( ; lv_i < lp_count ; lv_i += 1 ) {
        lv_bit = ((lp_value >> lv_i) & 1);
        hrsCap_statusParity = (hrsCap_statusParity ^ lv_bit);
        hrsCap_StatusCell((lp_start + lv_i), lv_bit);
    }
}

void hrsCap_StatusDraw () {
    point lv_target;
    int lv_x;
    int lv_y;

    if ((hrsCap_status == c_invalidDialogId)) {
        return;
    }
    lv_target = CameraGetTarget(hrsCap_statusPlayer);
    lv_x = FixedToInt((PointGetX(lv_target) * 64.0) + 0.5);
    lv_y = FixedToInt((PointGetY(lv_target) * 64.0) + 0.5);
    if ((lv_x < 0)) { lv_x = 0; }
    if ((lv_y < 0)) { lv_y = 0; }
    if ((lv_x > 32767)) { lv_x = 32767; }
    if ((lv_y > 32767)) { lv_y = 32767; }
    hrsCap_statusParity = 0;
    hrsCap_StatusCell(0, 1);
    hrsCap_StatusCell(1, 0);
    hrsCap_StatusBits(2, 8, hrsCap_seq);
    hrsCap_StatusBits(10, 10, MinI(1023, FixedToInt(TimerGetElapsed(libGame_gv_gameTimer))));  // game clock since the gates, seconds
    hrsCap_StatusBits(20, 15, lv_x);
    hrsCap_StatusBits(35, 15, lv_y);
    hrsCap_StatusBits(50, 1, 0);  // unused
    hrsCap_StatusBits(51, 1, BoolToInt((hrsCap_skyState == 1)));
    hrsCap_StatusBits(52, 1, BoolToInt((hrsCap_skyState == 2)));
    hrsCap_StatusBits(53, 1, hrsCap_tick);
    hrsCap_StatusBits(54, 1, BoolToInt(hrsCap_ready));
    hrsCap_StatusBits(55, 16, hrsCap_mapId);
    hrsCap_StatusBits(73, 8, MinI(255, hrsCap_clearedSinceReady));
    hrsCap_StatusBits(81, 1, BoolToInt(hrsCap_introOn));
    hrsCap_StatusBits(82, 4, MinI(15, hrsCap_openingCuts));
    if (hrsCap_leaving) {
        hrsCap_StatusBits(71, 2, 3);
    }
    else if (hrsCap_ready) {
        hrsCap_StatusBits(71, 2, 2);
    }
    else if (hrsCap_forwardStarted) {
        hrsCap_StatusBits(71, 2, 1);
    }
    else {
        hrsCap_StatusBits(71, 2, 0);
    }
    hrsCap_StatusCell(86, hrsCap_statusParity);
    hrsCap_StatusCell(87, 0);
    DialogControlSetPropertyAsColor(hrsCap_statusCtl[hrsCap_statusRows], c_triggerControlPropertyColor, PlayerGroupAll(), Color(0.0, 0.0, 0.0));
}

// The sequence number a command carries as its last word (the capture appends it).
int hrsCap_CommandSeq () {
    string lv_s;
    string lv_word;
    string lv_last;
    int lv_n;

    lv_s = EventChatMessage(false);
    lv_last = "";
    lv_n = 1;
    for ( ; lv_n <= 8 ; lv_n += 1 ) {
        lv_word = StringWord(lv_s, lv_n);
        if ((lv_word == "")) {
            break;
        }
        lv_last = lv_word;
    }
    return (StringToInt(lv_last) & 255);
}

// Called at the end of a command, once its work is done: the strip shows its sequence number.
void hrsCap_Ack () {
    hrsCap_statusPlayer = EventPlayer();
    hrsCap_seq = hrsCap_CommandSeq();
    hrsCap_StatusDraw();
}

void hrsCap_StatusInit () {
    int lv_k;

    // A black backdrop down the left edge below the cells: its own dialog, anchored to the
    // bottom-left and reaching up behind the cells; created first, the strip draws over it.
    hrsCap_statusBackdropDialog = DialogCreate((hrsCap_statusCellW * 2), 700, c_anchorBottomLeft, 0, 0, false);
    DialogSetImageVisible(hrsCap_statusBackdropDialog, false);
    hrsCap_statusBackdrop = DialogControlCreate(hrsCap_statusBackdropDialog, c_triggerControlTypeImage);
    DialogControlSetPropertyAsString(hrsCap_statusBackdrop, c_triggerControlPropertyImage, PlayerGroupAll(), "Assets/Textures/HrsWhite.dds");
    DialogControlSetPropertyAsInt(hrsCap_statusBackdrop, c_triggerControlPropertyImageType, PlayerGroupAll(), c_triggerImageTypeNormal);
    DialogControlSetPropertyAsColor(hrsCap_statusBackdrop, c_triggerControlPropertyColor, PlayerGroupAll(), Color(0.0, 0.0, 0.0));
    DialogControlSetSize(hrsCap_statusBackdrop, PlayerGroupAll(), (hrsCap_statusCellW * 2), 700);
    DialogControlSetPosition(hrsCap_statusBackdrop, PlayerGroupAll(), c_anchorTopLeft, 0, 0);
    DialogSetVisible(hrsCap_statusBackdropDialog, PlayerGroupAll(), true);
    hrsCap_status = DialogCreate((hrsCap_statusCellW * 2), (hrsCap_statusCellH * hrsCap_statusRows), c_anchorTopLeft, 0, 0, false);
    DialogSetImageVisible(hrsCap_status, false);
    // Black under the second column's cells, down to the strip's foot (the backdrop dialog
    // takes over from there).
    hrsCap_statusFiller = DialogControlCreate(hrsCap_status, c_triggerControlTypeImage);
    DialogControlSetPropertyAsString(hrsCap_statusFiller, c_triggerControlPropertyImage, PlayerGroupAll(), "Assets/Textures/HrsWhite.dds");
    DialogControlSetPropertyAsInt(hrsCap_statusFiller, c_triggerControlPropertyImageType, PlayerGroupAll(), c_triggerImageTypeNormal);
    DialogControlSetPropertyAsColor(hrsCap_statusFiller, c_triggerControlPropertyColor, PlayerGroupAll(), Color(0.0, 0.0, 0.0));
    DialogControlSetSize(hrsCap_statusFiller, PlayerGroupAll(), hrsCap_statusCellW, (hrsCap_statusCellH * ((2 * hrsCap_statusRows) - hrsCap_statusCells)));
    DialogControlSetPosition(hrsCap_statusFiller, PlayerGroupAll(), c_anchorTopLeft, hrsCap_statusCellW, (hrsCap_statusCellH * (hrsCap_statusCells - hrsCap_statusRows)));
    lv_k = 0;
    for ( ; lv_k < hrsCap_statusCells ; lv_k += 1 ) {
        hrsCap_statusCtl[lv_k] = DialogControlCreate(hrsCap_status, c_triggerControlTypeImage);
        DialogControlSetPropertyAsString(hrsCap_statusCtl[lv_k], c_triggerControlPropertyImage, PlayerGroupAll(), "Assets/Textures/HrsWhite.dds");
        DialogControlSetPropertyAsInt(hrsCap_statusCtl[lv_k], c_triggerControlPropertyImageType, PlayerGroupAll(), c_triggerImageTypeNormal);
        DialogControlSetSize(hrsCap_statusCtl[lv_k], PlayerGroupAll(), hrsCap_statusCellW, hrsCap_statusCellH);
        DialogControlSetPosition(hrsCap_statusCtl[lv_k], PlayerGroupAll(), c_anchorTopLeft, ((lv_k / hrsCap_statusRows) * hrsCap_statusCellW), (ModI(lv_k, hrsCap_statusRows) * hrsCap_statusCellH));
    }
    hrsCap_statusPlayer = hrsCap_LocalPlayer();
    hrsCap_StatusDraw();
    DialogSetVisible(hrsCap_status, PlayerGroupAll(), true);
}

//--------------------------------------------------------------------------------------------------
// The scene: units, camera, interface, sky.
//--------------------------------------------------------------------------------------------------
void hrsCap_InitTiles () {
${tileLines}
}

void hrsCap_RemoveUnits (unitfilter lp_filter) {
    unitgroup lv_g;
    int lv_u;
    unit lv_unit;

    lv_g = UnitGroup(null, c_playerAny, RegionEntireMap(), lp_filter, 0);
    lv_u = UnitGroupCount(lv_g, c_unitCountAll);
    for (;; lv_u -= 1) {
        lv_unit = UnitGroupUnitFromEnd(lv_g, lv_u);
        if (lv_unit == null) { break; }
        // Objective units stay: Hanamura's payload carries the path lines drawn to it.
        if ((UnitGetType(lv_unit) == "Payload_Neutral") || (UnitGetType(lv_unit) == "Payload_Neutral_Warning")) { continue; }
        UnitRemove(lv_unit);
        if (hrsCap_ready) {
            hrsCap_clearedSinceReady += 1;
        }
    }
}

// Remove heroes, minions, mercenaries, map creatures and summons; what stays (structures, and
// map-mechanic units such as altars: removing those leaves holes in the terrain) loses its
// health bars, and is hidden in terrain-only mode. Hidden, not removed: removing a core could
// end the game.
void hrsCap_ClearUnits () {
    unitgroup lv_g;
    int lv_u;
    unit lv_unit;

    hrsCap_RemoveUnits(UnitFilter((1 << c_targetFilterHeroic), 0, (1 << c_targetFilterStructure), 0));
    hrsCap_RemoveUnits(UnitFilter((1 << c_targetFilterMinion), 0, (1 << c_targetFilterStructure), 0));
    hrsCap_RemoveUnits(UnitFilter((1 << c_targetFilterCreep), 0, (1 << c_targetFilterStructure), 0));
    hrsCap_RemoveUnits(UnitFilter(0, (1 << (c_targetFilterMapCreature - 32)), (1 << c_targetFilterStructure), 0));
    hrsCap_RemoveUnits(UnitFilter(0, (1 << (c_targetFilterSummoned - 32)), (1 << c_targetFilterStructure), 0));

    lv_g = UnitGroup(null, c_playerAny, RegionEntireMap(), UnitFilter(0, 0, (1 << c_targetFilterMissile), 0), 0);
    lv_u = UnitGroupCount(lv_g, c_unitCountAll);
    for (;; lv_u -= 1) {
        lv_unit = UnitGroupUnitFromEnd(lv_g, lv_u);
        if (lv_unit == null) { break; }
        UnitSetState(lv_unit, c_unitStateStatusBar, false);
        if (hrsCap_hideStructures) {
            ActorSend(libNtve_gf_MainActorofUnit(lv_unit), "SetVisibility");
        }
    }
}

// The camera bounds lifted to the whole map, after chat "unbound" (the capture's tiles past the
// camera bounds, where map content runs on past the edge of the grid). Before every pan: the map
// may put its own bounds back at any time.
void hrsCap_LiftBounds () {
    if (hrsCap_unbound) {
        CameraSetBounds(PlayerGroupAll(), RegionRect(0.0, 0.0, ${fixed(mapWidth)}, ${fixed(mapHeight)}), false);
    }
}

void hrsCap_ApplyCamera (int lp_player) {
    CameraLockInput(lp_player, true);
    CameraUseHeightDisplacement(lp_player, false);
    CameraSetValue(lp_player, c_cameraValuePitch, hrsCap_pitch, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueYaw, 90.0, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueDistance, hrsCap_distance, 0.0, -1, 10.0);${lensLines}
}

// A camera like real play (distance 34, fov 45), shallow (pitch 35) and facing the map's main
// light (yaw ${fixed(refitYaw)}). The game fits its lighting (the region the main light and
// shadows are computed for) to the last camera it takes as normal and never refits it for the
// capture camera: the rest of the map was drawn without the main light (a dark, straight-edged
// area, a different one each match; dark boxes around holes). So every tile shows this camera
// at its centre for a few frames first, and the game refits around the tile. Only the latest
// look counts, and only a shallow look towards the light cleared every box (pitch 52 or other
// yaws left boxes around some holes).
void hrsCap_NormalCamera (int lp_player) {
    CameraSetValue(lp_player, c_cameraValuePitch, 35.0, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueYaw, ${fixed(refitYaw)}, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueDistance, 34.0, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueFieldOfView, 45.0, 0.0, -1, 10.0);
}

// No fog of war, no unexplored black, no HUD, no messages, a top-down camera, the sky.
void hrsCap_Scene () {
    int lv_p;
    int lv_f;

    // (Not VisEnable(c_visTypeFog, false): with fog of war off the reveal below stops working,
    // and only the vision around one team's buildings is drawn lit.)
    // The whole map, not RegionEntireMap() (the playable area): terrain outside the playable
    // bounds is otherwise left unexplored, under the black fog-of-war mask and its gradient
    // at the boundary, which darkened the outer walls and trees of Dragon Shire to half.
    lv_p = 0;
    for ( ; lv_p <= 15 ; lv_p += 1 ) {
        VisExploreArea(lv_p, ${mapRegion}, true, false);
        VisRevealArea(lv_p, ${mapRegion}, 0.0, false);
    }

    lv_p = 1;
    for ( ; lv_p <= 10 ; lv_p += 1 ) {
        if ((PlayerStatus(lv_p) == c_playerStatusActive)) {
${heroUiLines}            hrsCap_ApplyCamera(lv_p);
        }
    }
${uiLines}${boundsLine}
    hrsCap_LiftBounds();
    GameSetBackground(0, "HrsSky${skyModel}", 100.0);  // the camera-fixed skybox (sky.mjs)
    hrsCap_skyState = ${skyState};${doodadLines}
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    // Every animation on the map paused (glows, fires, smoke, swaying trees, flags), the way
    // Blizzard's maps do at game over: the two shots of a tile then differ only by the sky.
    // Only once the map is ready (see hrsCap_gt_FastForward): everything the start and the
    // first objective bring is there; the sweep sends it the moment that is so.
    // (Water is the terrain's own layer, not an actor; its waves are time-driven in the shader
    // and neither its data nor catalog edits still them, so it keeps moving.)
    if (hrsCap_ready) {
        libNtve_gf_SendActorMessageToGameRegion(${mapRegion}, "AnimSetPausedAll");
    }
}

// The map's intro cutscene is still running for someone (the sweep stops it, and the game's
// intro code then takes a moment to restore the camera and interface).
bool hrsCap_IntroPlaying () {
    int lv_p;

    lv_p = 1;
    for ( ; lv_p <= 10 ; lv_p += 1 ) {
        if ((libMapM_gv_mMIntroCutscene[lv_p] != c_cutsceneNone) && (libMapM_gv_mMIntroCutsceneFinished[lv_p] == false)) {
            return true;
        }
    }
    return false;
}

// Skip the map's intro cutscene the way the game's own skip does: stop the cutscene. The
// intro (libMapM_gf_PlayMapMechanicIntroForPlayer) waits for its cutscene to end and then
// restores the camera, interface, sound and vision itself, so nothing is left half-done.
void hrsCap_SkipIntro () {
    int lv_p;

    lv_p = 1;
    for ( ; lv_p <= 10 ; lv_p += 1 ) {
        if ((libMapM_gv_mMIntroCutscene[lv_p] != c_cutsceneNone) && (libMapM_gv_mMIntroCutsceneFinished[lv_p] == false)) {
            CutsceneStop(libMapM_gv_mMIntroCutscene[lv_p]);
        }
    }
}

// The game's own in-world labels (mercenary camp names and timers) are text tags stored per
// camp in the map-mechanics library; hide them, or they end up in the screenshots.
void hrsCap_HideLabels () {
    int lv_c;

    lv_c = 1;
    for ( ; lv_c <= libMapM_gv_jungleMaxCamps ; lv_c += 1 ) {
        if ((libMapM_gv_jungleCreepCamps[lv_c].lv_campHelperTextTagOrder != c_textTagNone)) {
            TextTagShow(libMapM_gv_jungleCreepCamps[lv_c].lv_campHelperTextTagOrder, PlayerGroupAll(), false);
        }
        if ((libMapM_gv_jungleCreepCamps[lv_c].lv_campHelperTextTagChaos != c_textTagNone)) {
            TextTagShow(libMapM_gv_jungleCreepCamps[lv_c].lv_campHelperTextTagChaos, PlayerGroupAll(), false);
        }
        // The camp's name, description and respawn timer ("Bruiser Camp", "Defeat or bribe this
        // camp", "0:29") are an interface panel per camp, created inside the game's
        // MercCampPanel and positioned over the camp (the camp's own dialog is only the anchor).
        if ((libMapM_gv_uIJungleCampPanel.lv_jungleCreepCampsInfoPanel[lv_c] != c_invalidDialogControlId)) {
            DialogControlSetVisible(libMapM_gv_uIJungleCampPanel.lv_jungleCreepCampsInfoPanel[lv_c], PlayerGroupAll(), false);
        }
    }
    if ((libMapM_gv_uIJungleCampPanel.lv_jungleCreepCampsParentPanel != c_invalidDialogControlId)) {
        DialogControlSetVisible(libMapM_gv_uIJungleCampPanel.lv_jungleCreepCampsParentPanel, PlayerGroupAll(), false);
    }
}

// Put back what the capture changed: the game's end-of-match sequence (the camera flying to a
// core, the explosion) crashed the game the moment it started, with the interface hidden and
// the camera far out of its normal range. Blizzard's own cinematic exit restores the same
// things. The sky slots too, as a render leaves them (our sky in the fixed slot, the map's own
// parallax in the other, normal speed): leaving with them as the sky probes set them (a slot
// empty, keyed copies, speed 1) crashed the game the moment the match ended.
void hrsCap_Restore () {
    int lv_p;
    int lv_f;
    camerainfo lv_cam;

    TriggerEnable(hrsCap_gt_Sweep, false);
    hrsCap_bgSpeed = 100.0;
    GameSetBackground(0, "HrsSky${skyModel}", 100.0);
    hrsCap_skyState = ${skyState};
    if ((hrsCap_mapParallax != "")) {
        GameSetBackground(1, hrsCap_mapParallax, 100.0);
    }
    lv_cam = CameraInfoDefault();
    lv_p = 1;
    for ( ; lv_p <= 10 ; lv_p += 1 ) {
        if ((PlayerStatus(lv_p) == c_playerStatusActive)) {
            CameraSetValue(lv_p, c_cameraValuePitch, CameraInfoGetValue(lv_cam, c_cameraValuePitch), 0.0, -1, 10.0);
            CameraSetValue(lv_p, c_cameraValueDistance, CameraInfoGetValue(lv_cam, c_cameraValueDistance), 0.0, -1, 10.0);
            CameraSetValue(lv_p, c_cameraValueFieldOfView, CameraInfoGetValue(lv_cam, c_cameraValueFieldOfView), 0.0, -1, 10.0);
            CameraSetValue(lv_p, c_cameraValueFarClip, CameraInfoGetValue(lv_cam, c_cameraValueFarClip), 0.0, -1, 10.0);
            CameraSetValue(lv_p, c_cameraValueNearClip, CameraInfoGetValue(lv_cam, c_cameraValueNearClip), 0.0, -1, 10.0);
            CameraSetValue(lv_p, c_cameraValueYaw, CameraInfoGetValue(lv_cam, c_cameraValueYaw), 0.0, -1, 10.0);
            CameraUseHeightDisplacement(lv_p, true);
            CameraLockInput(lv_p, false);
        }
    }
    UISetMode(PlayerGroupAll(), c_uiModeConsole, c_transitionDurationImmediate);
    lv_f = c_syncFrameTypeFirst;
    for ( ; lv_f <= c_syncFrameTypeLast ; lv_f += 1 ) {
        UISetFrameVisible(PlayerGroupAll(), lv_f, true);
    }
}

// Until the camera has stopped moving (a pan eases over several frames, the longer the further;
// a clamp at the bounds is applied at the next update): the status strip's echo of the
// camera's target is only right after this. At most 1.5 s.
void hrsCap_WaitCameraStill () {
    point lv_prev;
    point lv_now;
    int lv_n;

    lv_prev = CameraGetTarget(hrsCap_statusPlayer);
    lv_n = 0;
    while ((lv_n < 24)) {
        Wait(0.0625, c_timeReal);
        lv_now = CameraGetTarget(hrsCap_statusPlayer);
        if ((DistanceBetweenPoints(lv_now, lv_prev) < 0.02)) {
            return;
        }
        lv_prev = lv_now;
        lv_n += 1;
    }
}

//--------------------------------------------------------------------------------------------------
// The opening: gates early, the map's opening timers cut short, then the map is ready.
//--------------------------------------------------------------------------------------------------

// One of the map's opening timers to half a second from expiring, if it is running and has
// longer than that to go.
void hrsCap_CutShort (timer lp_t) {
    if ((TimerGetRemaining(lp_t) > 0.5)) {
        TimerSetElapsed(lp_t, (TimerGetDuration(lp_t) - 0.5));
        hrsCap_openingCuts += 1;
    }
}

// Once the gates have opened: for a few real seconds the map's opening timers (the countdown
// to its first objective and the steps on the way: Hanamura's payload comes 3 minutes after the
// gates) are cut short as each one starts, so the objective and what it brings are in place
// before any tile is shot; the map is then ready. (Speeding game time up instead,
// GameSetGlobalTimeScale, moved no timers: the game clock read 11 s after 10 s at 20x.)
bool hrsCap_gt_FastForward_Func (bool testConds, bool runActions) {
    int lv_n;

    if (!runActions) {
        return true;
    }
    lv_n = 0;
    while ((lv_n < FixedToInt(hrsCap_forwardSeconds * 4.0))) {
${openingTimers.map((t) => `        hrsCap_CutShort(${t});`).join('\n')}
        Wait(0.25, c_timeReal);
        lv_n += 1;
    }
    Wait(MaxF(0.0, hrsCap_readySeconds - hrsCap_forwardSeconds), c_timeReal);
    hrsCap_ready = true;
    return true;
}

// Four times a second: units keep spawning (minions, mercenaries, objectives) and the chat line
// lingers; sweep both, cut the intro short as soon as it starts, keep the game's labels hidden,
// move the opening along, pause the animations once the map is ready, redraw the strip.
bool hrsCap_gt_Sweep_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
${keepIntro ? '' : '    hrsCap_SkipIntro();\n'}    hrsCap_HideLabels();
    hrsCap_ClearUnits();
    TransmissionClearGroup(PlayerGroupAll());
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    // No camera scrolling from the mouse at a screen edge or the keys, from the start and even
    // after something (the intro's exit) has unlocked it.
    CameraLockInput(hrsCap_LocalPlayer(), true);
    // The gates sooner (GameLib's countdown, before it gets far), then the fast-forward.
    if (!libGame_gv_gameStarted && (TimerGetRemaining(libGame_gv_openTheGatesTimer) > hrsCap_gatesDelay)) {
        TimerSetElapsed(libGame_gv_openTheGatesTimer, (TimerGetDuration(libGame_gv_openTheGatesTimer) - hrsCap_gatesDelay));
    }
    if (libGame_gv_gameStarted && !hrsCap_forwardStarted) {
        hrsCap_forwardStarted = true;
        TriggerExecute(hrsCap_gt_FastForward, false, false);
    }
    if (hrsCap_ready && !hrsCap_paused) {
        hrsCap_paused = true;
        libNtve_gf_SendActorMessageToGameRegion(${mapRegion}, "AnimSetPausedAll");
    }
    hrsCap_introOn = hrsCap_IntroPlaying();
    hrsCap_tick = (1 - hrsCap_tick);
    hrsCap_StatusDraw();
    return true;
}

//--------------------------------------------------------------------------------------------------
// Chat commands. Each ends with hrsCap_Ack once its work is done, so the strip shows the
// sequence number the capture appended to it.
//--------------------------------------------------------------------------------------------------

// "tile <n> [<x> <y>]": tile n, at the given map position or the planned one. The capture
// gives the position: it re-plans the grid from the camera bounds the game applies at run time
// (an arena's are far tighter than its map file says), which it measures by sending the camera
// to two corners. Acknowledged once the camera has stopped (eased pan, clamp at the bounds):
// the strip then reports where the camera really is.
bool hrsCap_gt_Tile_Func (bool testConds, bool runActions) {
    int lv_index;
    string lv_xs;

    lv_index = StringToInt(StringWord(EventChatMessage(false), 2));
    lv_xs = StringWord(EventChatMessage(false), 3);
    if ((lv_index < 0) || (lv_index > 999) || ((lv_xs == "") && (lv_index >= hrsCap_tileCount))) {
        return true;
    }
    if (!runActions) {
        return true;
    }
    // Nothing until the intro is over, so its exit can't undo the scene (the capture keeps
    // asking until it answers).
    if (hrsCap_IntroPlaying()) {
        return true;
    }
    hrsCap_currentTile = lv_index;
    if ((lv_xs != "")) {
        hrsCap_curX = StringToFixed(lv_xs);
        hrsCap_curY = StringToFixed(StringWord(EventChatMessage(false), 4));
    }
    else {
        hrsCap_curX = hrsCap_tileX[lv_index];
        hrsCap_curY = hrsCap_tileY[lv_index];
    }
    hrsCap_ClearUnits();${boundsLine}
    hrsCap_LiftBounds();
    // The normal camera at the tile first, a few frames, so the game refits its lighting there
    // (see hrsCap_NormalCamera); then the scene and the capture camera.
    CameraPan(EventPlayer(), Point(hrsCap_curX, hrsCap_curY), 0.0, -1, 10.0, false);
    hrsCap_NormalCamera(EventPlayer());
    Wait(hrsCap_refitWait, c_timeReal);  // real time: a map that holds game time still (an arena's selection phase) would never return
    if ((hrsCap_currentTile != lv_index)) {
        return true;  // another tile was asked for meanwhile
    }
    hrsCap_Scene();
    CameraPan(EventPlayer(), Point(hrsCap_curX, hrsCap_curY), 0.0, -1, 10.0, false);
    hrsCap_statusPlayer = EventPlayer();
    hrsCap_WaitCameraStill();
    if ((hrsCap_currentTile != lv_index)) {
        return true;
    }
    // Ready for the kept shot: a transmission (an Immortal's voice line) puts a subtitle and its
    // backdrop on screen, outside the frames the scene hides; cleared with the messages.
    TransmissionClearGroup(PlayerGroupAll());
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    hrsCap_Ack();
    return true;
}

// "clean": the kept shot follows: camera unmoved, messages and transmissions cleared.
bool hrsCap_gt_Clean_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    // A transmission (an Immortal's voice line) puts a subtitle and its backdrop on screen,
    // outside the frames hidden by the scene; cleared right before the shot.
    TransmissionClearGroup(PlayerGroupAll());
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    hrsCap_Ack();
    return true;
}

// "black": the black skybox, for the second clean shot (difference matting: the same view
// over white and over black gives each pixel's transparency). "tile" puts white back (Scene).
bool hrsCap_gt_Black_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    GameSetBackground(0, "HrsSkyBlack", 100.0);
    hrsCap_skyState = 2;
    TransmissionClearGroup(PlayerGroupAll());
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    hrsCap_Ack();
    return true;
}

// Sixteen times a second: transmissions (an Immortal's voice line puts a subtitle and its backdrop
// on screen, outside the frames the scene hides) and messages cleared, so none can be in a kept
// shot (the shot follows the tile's acknowledgement by a tenth of a second).
bool hrsCap_gt_Quiet_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    TransmissionClearGroup(PlayerGroupAll());
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    return true;
}

// Number pad 5: the black skybox, like "black" but from one key press (no chat box to open, type
// into and send), for the second shot of every tile. A key carries no sequence number: the strip
// keeps the tile's and shows the sky as black, which is what the capture waits for (and it falls
// back to "black" if the key went missing).
bool hrsCap_gt_KeyBlack_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    GameSetBackground(0, "HrsSkyBlack", 100.0);
    hrsCap_skyState = 2;
    TransmissionClearGroup(PlayerGroupAll());
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    hrsCap_statusPlayer = EventPlayer();
    hrsCap_StatusDraw();
    return true;
}

// "sky <colour> [<layer>]" (probes): the solid-colour skybox of that name (sky.mjs) on layer 0
// (the camera-fixed skybox, the default) or 1 (c_backgroundTerrain, the terrain-relative
// parallax layer, which Blizzard's arenas swap). "none" clears the layer, "heaven" is
// HeavenSkybox, "mapsky" and "mapparallax" the map's own fixed and parallax sky models. At the
// animation speed "bgspeed" set (100 unless changed). Never "none" on layer 1: every run that
// emptied the parallax slot crashed the game when the match ended, even with it set again.
bool hrsCap_gt_Sky_Func (bool testConds, bool runActions) {
    string lv_colour;
    string lv_model;
    int lv_layer;

    if (!runActions) {
        return true;
    }
    lv_colour = StringWord(EventChatMessage(false), 2);
    lv_layer = StringToInt(StringWord(EventChatMessage(false), 3));
    if ((lv_colour == "none")) {
        lv_model = "";
    }
    else if ((lv_colour == "heaven")) {
        lv_model = "HeavenSkybox";
    }
    else if ((lv_colour == "mapsky")) {
        lv_model = hrsCap_mapSky;
    }
    else if ((lv_colour == "mapparallax")) {
        lv_model = hrsCap_mapParallax;
    }
    else {
        lv_model = ("HrsSky" + StringCase(StringSub(lv_colour, 1, 1), true) + StringSub(lv_colour, 2, StringLength(lv_colour)));
    }
    GameSetBackground(lv_layer, lv_model, hrsCap_bgSpeed);
    if ((lv_layer == 0) && (lv_colour == "white")) { hrsCap_skyState = 1; }
    else if ((lv_layer == 0) && (lv_colour == "black")) { hrsCap_skyState = 2; }
    else { hrsCap_skyState = 0; }
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    hrsCap_Ack();
    return true;
}

// "pause [0]": pause every animation on the map the way Blizzard's maps do at game over (the
// whole-map actor message plus UnitPauseAll), "0" to resume. The sweep pauses them by itself
// once the map is ready; the capture sends this only if the map never reports ready.
bool hrsCap_gt_Pause_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    if ((StringWord(EventChatMessage(false), 2) == "0")) {
        libNtve_gf_SendActorMessageToGameRegion(${mapRegion}, "AnimSetPausedAll 0");
        UnitPauseAll(false);
    }
    else {
        libNtve_gf_SendActorMessageToGameRegion(${mapRegion}, "AnimSetPausedAll");
        UnitPauseAll(true);
    }
    hrsCap_Ack();
    return true;
}

// "move <n> <x> <y>" (--probe-light): like "tile" but without the scene set-up (the vision
// reveal, camera re-application, interface hiding, sky, doodad hiding), to tell what that costs.
bool hrsCap_gt_Move_Func (bool testConds, bool runActions) {
    int lv_index;

    if (!runActions) {
        return true;
    }
    lv_index = StringToInt(StringWord(EventChatMessage(false), 2));
    hrsCap_currentTile = lv_index;
    hrsCap_curX = StringToFixed(StringWord(EventChatMessage(false), 3));
    hrsCap_curY = StringToFixed(StringWord(EventChatMessage(false), 4));
    CameraPan(EventPlayer(), Point(hrsCap_curX, hrsCap_curY), 0.0, -1, 10.0, false);
    hrsCap_NormalCamera(EventPlayer());
    Wait(0.25, c_timeReal);
    hrsCap_ApplyCamera(EventPlayer());
    CameraPan(EventPlayer(), Point(hrsCap_curX, hrsCap_curY), 0.0, -1, 10.0, false);
    hrsCap_statusPlayer = EventPlayer();
    hrsCap_WaitCameraStill();
    hrsCap_Ack();
    return true;
}

// "look <x> <y>" (--probe-light): point the camera at that map position, nothing else.
bool hrsCap_gt_Look_Func (bool testConds, bool runActions) {
    fixed lv_x;
    fixed lv_y;

    if (!runActions) {
        return true;
    }
    lv_x = StringToFixed(StringWord(EventChatMessage(false), 2));
    lv_y = StringToFixed(StringWord(EventChatMessage(false), 3));
    CameraPan(EventPlayer(), Point(lv_x, lv_y), 0.0, -1, 10.0, false);
    hrsCap_Ack();
    return true;
}

// "clip <near> <far>" (--probe-light): the camera's clip planes, until the next "tile" puts the
// capture camera's back. Probe: does a near clip past the ground (the camera is about 214 units
// out) leave only the skybox?
bool hrsCap_gt_Clip_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    CameraSetValue(EventPlayer(), c_cameraValueNearClip, StringToFixed(StringWord(EventChatMessage(false), 2)), 0.0, -1, 10.0);
    CameraSetValue(EventPlayer(), c_cameraValueFarClip, StringToFixed(StringWord(EventChatMessage(false), 3)), 0.0, -1, 10.0);
    hrsCap_Ack();
    return true;
}

// "hidemap <near clip>" (the sky passes): the map clipped away at that distance (far clip 5000),
// sky swaps at speed 1 (frozen), our sky taken down: what three commands did, in one.
bool hrsCap_gt_HideMap_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    CameraSetValue(EventPlayer(), c_cameraValueNearClip, StringToFixed(StringWord(EventChatMessage(false), 2)), 0.0, -1, 10.0);
    CameraSetValue(EventPlayer(), c_cameraValueFarClip, 5000.0, 0.0, -1, 10.0);
    hrsCap_bgSpeed = 1.0;
    GameSetBackground(0, "", hrsCap_bgSpeed);
    hrsCap_skyState = 0;
    hrsCap_Ack();
    return true;
}

// "refitwait <seconds>" (probes): how long the lighting-refit look is held before each tile.
bool hrsCap_gt_RefitWait_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_refitWait = StringToFixed(StringWord(EventChatMessage(false), 2));
    hrsCap_Ack();
    return true;
}

// "unbound": lift the camera bounds to the whole map from now on (see hrsCap_LiftBounds).
bool hrsCap_gt_Unbound_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_unbound = true;
    hrsCap_LiftBounds();
    hrsCap_Ack();
    return true;
}

// "bgspeed <percent>" (probes): the animation speed the following "sky" commands give their
// background (GameSetBackground's last argument; 0 to try freezing a sky's own animation).
bool hrsCap_gt_BgSpeed_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_bgSpeed = StringToFixed(StringWord(EventChatMessage(false), 2));
    hrsCap_Ack();
    return true;
}

// "quit" ends the match by killing that player's own core: the game then ends it its normal way
// (end sequence, defeat), back towards the menu, where the next run's map loads. (Ending it
// directly with GameOver crashed Battlefield of Eternity once the match had run about 30 s.) On
// a map of rounds the other team is first one round win short of the match, so this round is
// the last.
bool hrsCap_gt_Quit_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_leaving = true;
    hrsCap_StatusDraw();  // the strip says "leaving" at once (Restore shows the interface again)
    Wait(0.25, c_timeReal);
    hrsCap_Restore();
    Wait(0.5, c_timeReal);
${arena ? '    libAREN_gv_aRM_RoundScore[libGame_gf_EnemyTeam(libGame_gf_TeamNumberOfPlayer(EventPlayer()))] = libAREN_gv_victoriesCount - 1;\n' : ''}    UnitKill(libGame_gv_teams[libGame_gf_TeamNumberOfPlayer(EventPlayer())].lv_core);
    return true;
}

void hrsCap_Init () {
    hrsCap_InitTiles();
    hrsCap_StatusInit();
    // (Chat events match a word anywhere in the message, so "sky black" also runs "black";
    // the order the triggers are created in is kept as it was tested.)
    hrsCap_gt_Clean = TriggerCreate("hrsCap_gt_Clean_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Clean, c_playerAny, "clean", false);
    hrsCap_gt_Move = TriggerCreate("hrsCap_gt_Move_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Move, c_playerAny, "move", false);
    hrsCap_gt_Tile = TriggerCreate("hrsCap_gt_Tile_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Tile, c_playerAny, "tile", false);
    hrsCap_gt_Pause = TriggerCreate("hrsCap_gt_Pause_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Pause, c_playerAny, "pause", false);
    hrsCap_gt_Sky = TriggerCreate("hrsCap_gt_Sky_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Sky, c_playerAny, "sky", false);
    hrsCap_gt_Black = TriggerCreate("hrsCap_gt_Black_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Black, c_playerAny, "black", false);
    hrsCap_gt_Clip = TriggerCreate("hrsCap_gt_Clip_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Clip, c_playerAny, "clip", false);
    hrsCap_gt_KeyBlack = TriggerCreate("hrsCap_gt_KeyBlack_Func");
    TriggerAddEventKeyPressed(hrsCap_gt_KeyBlack, c_playerAny, c_keyNumPad5, true, c_keyModifierStateIgnore, c_keyModifierStateIgnore, c_keyModifierStateIgnore);
    hrsCap_gt_HideMap = TriggerCreate("hrsCap_gt_HideMap_Func");
    TriggerAddEventChatMessage(hrsCap_gt_HideMap, c_playerAny, "hidemap", false);
    hrsCap_gt_RefitWait = TriggerCreate("hrsCap_gt_RefitWait_Func");
    TriggerAddEventChatMessage(hrsCap_gt_RefitWait, c_playerAny, "refitwait", false);
    hrsCap_gt_Unbound = TriggerCreate("hrsCap_gt_Unbound_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Unbound, c_playerAny, "unbound", false);
    hrsCap_gt_BgSpeed = TriggerCreate("hrsCap_gt_BgSpeed_Func");
    TriggerAddEventChatMessage(hrsCap_gt_BgSpeed, c_playerAny, "bgspeed", false);
    hrsCap_gt_Look = TriggerCreate("hrsCap_gt_Look_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Look, c_playerAny, "look", false);
    hrsCap_gt_Quit = TriggerCreate("hrsCap_gt_Quit_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Quit, c_playerAny, "quit", true);
    hrsCap_gt_FastForward = TriggerCreate("hrsCap_gt_FastForward_Func");
    libCore_gv_bALOpenTheGatesDelay = hrsCap_gatesDelay;  // read when GameLib starts its countdown
    hrsCap_gt_Sweep = TriggerCreate("hrsCap_gt_Sweep_Func");
    TriggerAddEventTimePeriodic(hrsCap_gt_Sweep, 0.25, c_timeReal);
    hrsCap_gt_Quiet = TriggerCreate("hrsCap_gt_Quiet_Func");
    TriggerAddEventTimePeriodic(hrsCap_gt_Quiet, 0.0625, c_timeReal);
}

`;
}
