/**
 * The Galaxy script injected into a battleground. It skips the map's intro cutscene, reveals
 * the whole map, hides the HUD, removes units (and keeps removing them as they spawn), keeps or
 * hides structures, and points the camera straight down. Typing `tile <n>` in chat moves the camera to tile n of the capture
 * grid. Two opt-in extras, both still unproven in the game: keeping map-mechanic units such as
 * altars, and freezing model animations.
 *
 * Every native and library call below is one Blizzard's own Heroes scripts make, except the
 * "lens" values (field of view, far clip, yaw) and CameraSetBounds, which are StarCraft II
 * natives not seen in Heroes scripts. Those are only emitted when asked for, so a missing
 * constant cannot stop the default script from compiling. Actor messages are plain strings the
 * game ignores when it doesn't know them, so they cannot break compilation either.
 */

const fixed = (n) => (Number.isInteger(n) ? `${n}.0` : n.toFixed(4));
/** The status strip (see capture-script.mjs's hrsCap_Status* and status.py): cells and size. */
export const STATUS_CELLS = 56;
export const STATUS_CELL_UNITS = 20;

/**
 * @param {object} o
 * @param {{ x: number, y: number }[]} o.tiles Camera targets, in map cells.
 * @param {boolean} o.hideStructures Hide structures (and kept map-mechanic units) too.
 * @param {number} o.distance Camera distance from its target.
 * @param {number} [o.pitch] Camera pitch in degrees (90 is straight down).
 * @param {number} [o.refitYaw] Yaw of the lighting-refit look before each tile (towards the
 *   map's main light).
 * @param {{ fov: number, farClip: number } | null} o.lens Narrow field of view; null to leave the map's.
 * @param {boolean} o.unbound Lift the map's camera bounds so edge tiles are not clamped (and, on a
 *   map that is several arenas in one, so the camera can reach every arena). Lifted before each
 *   tile's first pan, since the map may put its own bounds back at any time.
 * @param {boolean} o.keepMechanics Remove only heroes, minions, mercenaries, map creatures and
 *   summons, keeping map-mechanic units; otherwise every unit but structures is removed.
 * @param {boolean} o.freeze Pause model animations, so neighbouring screenshots match.
 * @param {boolean} o.showUi Leave the HUD up (to check what the HUD hiding affects).
 * @param {boolean} o.sky Solid-colour skyboxes are in the map (sky.mjs): chat "sky <colour>" swaps.
 * @param {string} o.skyColour The sky each tile starts under: white (then "black" for the matte's second shot) or black.
 * @param {string[]} o.hideDoodads Doodad types to hide (cloud layers placed in the map as doodads).
 * @param {boolean} [o.keepIntro] Let the intro cutscene play out instead of skipping it
 *   (diagnostic: does skipping it leave the map's lighting half changed?).
 * @param {{ dx: number, dy: number }[] | null} o.markers Registration markers, as offsets in map
 *   cells from each tile's camera target: magenta single-digit text tags pinned to those ground
 *   points, shown (with a tile id label at the view's centre) only while that tile is on
 *   screen. Null for none.
 */
export function captureScript({
  tiles,
  hideStructures,
  distance,
  pitch = 90,
  refitYaw = 180,
  lens,
  unbound,
  keepMechanics,
  freeze,
  showUi,
  sky = false,
  skyColour = 'white',
  hideDoodads = [],
  keepIntro = false,
  markers,
}) {
  const n = tiles.length;
  const tileLines = tiles
    .map((t, i) => `    hrsCap_tileX[${i}] = ${fixed(t.x)}; hrsCap_tileY[${i}] = ${fixed(t.y)};`)
    .join('\n');
  const lensLines = lens
    ? `
    if ((hrsCap_fov > 0.0)) {
        CameraSetValue(lp_player, c_cameraValueFieldOfView, hrsCap_fov, 0.0, -1, 10.0);
    }
    else {
        CameraSetValue(lp_player, c_cameraValueFieldOfView, ${fixed(lens.fov)}, 0.0, -1, 10.0);
    }
    CameraSetValue(lp_player, c_cameraValueFarClip, ${fixed(lens.farClip)}, 0.0, -1, 10.0);
    // The near clip well out from the game's tiny default: depth precision goes with the
    // far/near ratio, and at this distance flat decals on the ground (road trim, cracks, the
    // decorations lying on surfaces) were z-fighting the ground and losing in patches.
    CameraSetValue(lp_player, c_cameraValueNearClip, 5.0, 0.0, -1, 10.0);`
    : '';
  const boundsLine = unbound
    ? `
    CameraSetBounds(PlayerGroupAll(), RegionEntireMap(), false);`
    : '';
  const removalLines = keepMechanics
    ? `    hrsCap_RemoveUnits(UnitFilter((1 << c_targetFilterHeroic), 0, (1 << c_targetFilterStructure), 0));
    hrsCap_RemoveUnits(UnitFilter((1 << c_targetFilterMinion), 0, (1 << c_targetFilterStructure), 0));
    hrsCap_RemoveUnits(UnitFilter((1 << c_targetFilterCreep), 0, (1 << c_targetFilterStructure), 0));
    hrsCap_RemoveUnits(UnitFilter(0, (1 << (c_targetFilterMapCreature - 32)), (1 << c_targetFilterStructure), 0));
    hrsCap_RemoveUnits(UnitFilter(0, (1 << (c_targetFilterSummoned - 32)), (1 << c_targetFilterStructure), 0));`
    : `    hrsCap_RemoveUnits(UnitFilter(0, 0, (1 << c_targetFilterStructure), 0));`;
  // What stays: structures only, or structures and map-mechanic units.
  const remainingFilter = keepMechanics
    ? 'UnitFilter(0, 0, (1 << c_targetFilterMissile), 0)'
    : 'UnitFilter((1 << c_targetFilterStructure), 0, 0, 0)';
  const freezeLines = freeze
    ? `
    ActorRegionSend(RegionEntireMap(), c_actorIntersectAgainstRadiusContact, "AnimSetPausedAll 1", "Doodad", "");
    ActorRegionSend(RegionEntireMap(), c_actorIntersectAgainstRadiusContact, "AnimSetPausedAll 1", "Unit", "");
    ActorRegionSend(RegionEntireMap(), c_actorIntersectAgainstRadiusContact, "AnimSetPausedAll 1", "Model", "");`
    : '';

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
    // visible when it started, debug panels included); text tags stay, for the markers.
    lv_f = c_syncFrameTypeFirst;
    for ( ; lv_f <= c_syncFrameTypeLast ; lv_f += 1 ) {
        if ((lv_f != c_syncFrameTypeTextTag)) {
            UISetFrameVisible(PlayerGroupAll(), lv_f, false);
        }
    }`;
  const markerCount = markers ? markers.length : 0;
  // Tile ids are zero-padded to at least three digits: never one digit like a marker, never ten
  // like the reference label.
  const idDigits = Math.max(3, String(n - 1).length);
  const idPad = Array.from({ length: idDigits - 1 }, (_, k) => {
    const below = 10 ** (idDigits - 1 - k);
    return `    if ((lp_index < ${below})) {
        lv_s = ("0" + lv_s);
    }`;
  }).join('\n');
  const markerDecl = markers
    ? `
const int hrsCap_markerCount = ${markerCount};
fixed[${markerCount}] hrsCap_markerDX;
fixed[${markerCount}] hrsCap_markerDY;
int[${markerCount}] hrsCap_markers;
int hrsCap_glyphs = c_textTagNone;
int hrsCap_idLabel = c_textTagNone;
trigger hrsCap_gt_Glyphs;`
    : '';
  const markerFuncs = markers
    ? `
void hrsCap_InitMarkers () {
${markers.map((m, k) => `    hrsCap_markerDX[${k}] = ${fixed(m.dx)}; hrsCap_markerDY[${k}] = ${fixed(m.dy)}; hrsCap_markers[${k}] = c_textTagNone;`).join('\n')}
}

// The tile id label: the full tile number, zero-padded to ${idDigits} digits, so it can't be mistaken for
// a (single-digit) marker. It only proves the calibration shot shows the tile that was asked
// for; it is never used for positioning.
text hrsCap_IdText (int lp_index) {
    string lv_s;

    lv_s = IntToString(lp_index);
${idPad}
    return StringToText(lv_s);
}

// The map's size in cells, from the game.

// Where the tile id label goes: the tile's centre, kept a few cells inside the map (text tags
// outside the map aren't drawn, and a tile at the map's corner has its centre on the edge).
point hrsCap_IdPoint (int lp_index) {
    return Point(MaxF(3.0, MinF(hrsCap_curX, (hrsCap_MapWidth() - 3.0))), MaxF(3.0, MinF(hrsCap_curY, (hrsCap_MapHeight() - 3.0))));
}

// Numbered registration markers: magenta digits pinned to known ground points. Each marker's
// label is only its own number, the same on every tile, so it always has the same shape and
// its measured position means the same thing on every tile. Each tile is shot twice: once with them (calibration: where they land gives the exact
// camera geometry) and once without ("clean": the image that is kept). Created once, while the
// camera is briefly close (the game does not draw labels created under a far camera, but keeps
// drawing them once created), together with the "0123456789" reference label the capture
// learns the digits from and the tile id label; then moved and shown per tile.
void hrsCap_ShowMarkers (int lp_index) {
    int lv_k;
    int lv_p;

    if ((hrsCap_markers[0] == c_textTagNone)) {
        lv_p = 1;
        for ( ; lv_p <= 10 ; lv_p += 1 ) {
            if ((PlayerStatus(lv_p) == c_playerStatusActive)) {
                CameraSetValue(lv_p, c_cameraValueFieldOfView, 40.0, 0.0, -1, 10.0);
                CameraSetValue(lv_p, c_cameraValueDistance, 41.0, 0.0, -1, 10.0);
            }
        }
        lv_k = 0;
        for ( ; lv_k < hrsCap_markerCount ; lv_k += 1 ) {
            TextTagCreate(StringToText(IntToString(lv_k)), 24, Point((hrsCap_curX + hrsCap_markerDX[lv_k]), (hrsCap_curY + hrsCap_markerDY[lv_k])), 0.0, true, false, PlayerGroupAll());
            TextTagSetColor(TextTagLastCreated(), c_textTagColorText, Color(100.00, 0.00, 100.00));
            TextTagSetFogVisibility(TextTagLastCreated(), c_visTypeFog);
            hrsCap_markers[lv_k] = TextTagLastCreated();
        }
        TextTagCreate(StringToText("0123456789"), 24, Point(hrsCap_curX, hrsCap_curY), 0.0, false, false, PlayerGroupAll());
        TextTagSetColor(TextTagLastCreated(), c_textTagColorText, Color(100.00, 0.00, 100.00));
        TextTagSetFogVisibility(TextTagLastCreated(), c_visTypeFog);
        hrsCap_glyphs = TextTagLastCreated();
        TextTagCreate(hrsCap_IdText(lp_index), 24, hrsCap_IdPoint(lp_index), 0.0, true, false, PlayerGroupAll());
        TextTagSetColor(TextTagLastCreated(), c_textTagColorText, Color(100.00, 0.00, 100.00));
        TextTagSetFogVisibility(TextTagLastCreated(), c_visTypeFog);
        hrsCap_idLabel = TextTagLastCreated();
        hrsCap_markersShown = 1;
        lv_p = 1;
        for ( ; lv_p <= 10 ; lv_p += 1 ) {
            if ((PlayerStatus(lv_p) == c_playerStatusActive)) {
                hrsCap_ApplyCamera(lv_p);
            }
        }
        return;
    }
    TextTagShow(hrsCap_glyphs, PlayerGroupAll(), false);
    lv_k = 0;
    for ( ; lv_k < hrsCap_markerCount ; lv_k += 1 ) {
        TextTagSetPosition(hrsCap_markers[lv_k], Point((hrsCap_curX + hrsCap_markerDX[lv_k]), (hrsCap_curY + hrsCap_markerDY[lv_k])), 0.0);
        TextTagShow(hrsCap_markers[lv_k], PlayerGroupAll(), true);
    }
    hrsCap_markersShown = 1;
    TextTagSetPosition(hrsCap_idLabel, hrsCap_IdPoint(lp_index), 0.0);
    TextTagSetText(hrsCap_idLabel, hrsCap_IdText(lp_index));
    TextTagShow(hrsCap_idLabel, PlayerGroupAll(), true);
}

// Hide the markers, the tile id label and the reference label, for the clean shot.
void hrsCap_HideMarkers () {
    int lv_k;

    hrsCap_markersShown = 0;
    lv_k = 0;
    for ( ; lv_k < hrsCap_markerCount ; lv_k += 1 ) {
        if ((hrsCap_markers[lv_k] != c_textTagNone)) {
            TextTagShow(hrsCap_markers[lv_k], PlayerGroupAll(), false);
        }
    }
    if ((hrsCap_glyphs != c_textTagNone)) {
        TextTagShow(hrsCap_glyphs, PlayerGroupAll(), false);
    }
    if ((hrsCap_idLabel != c_textTagNone)) {
        TextTagShow(hrsCap_idLabel, PlayerGroupAll(), false);
    }
}

// Chat "glyphs": show the reference label where the camera is actually looking (markers
// hidden), for the capture to learn the game font's digits. The camera's target, not the tile's
// centre: an arena's camera bounds can keep the camera far from the tile it was sent to.
bool hrsCap_gt_Glyphs_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_HideMarkers();
    if ((hrsCap_glyphs != c_textTagNone)) {
        TextTagSetPosition(hrsCap_glyphs, CameraGetTarget(EventPlayer()), 0.0);
        TextTagShow(hrsCap_glyphs, PlayerGroupAll(), true);
    }
    hrsCap_Ack();
    return true;
}
`
    : '';
  const markerCall = markers
    ? `
    hrsCap_ShowMarkers(lv_index);`
    : '';
  const markerInit = markers
    ? `
    hrsCap_InitMarkers();
    hrsCap_gt_Glyphs = TriggerCreate("hrsCap_gt_Glyphs_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Glyphs, c_playerAny, "glyphs", false);`
    : '';

  return `//--------------------------------------------------------------------------------------------------
// Map capture (injected by heroes-replay-stats tools/map-capture)
// Chat "tile <n>" moves the camera to tile n of the capture grid.
//--------------------------------------------------------------------------------------------------
const bool hrsCap_hideStructures = ${hideStructures};
const fixed hrsCap_distance = ${fixed(distance)};
fixed hrsCap_zoom = 0.0; // set by chat "zoom <distance>"; 0 means the planned distance
fixed hrsCap_pitch = ${fixed(pitch)};
fixed hrsCap_fov = 0.0; // set by chat "fov <degrees>"; 0 means the planned field of view
const int hrsCap_tileCount = ${n};
int hrsCap_currentTile = 0;
bool hrsCap_boundsBusy = false;  // a "bounds" measurement is under way (a second one would fight it for the camera)
fixed hrsCap_curX = 0.0;  // the current tile's camera target (from the command, else the table)
fixed hrsCap_curY = 0.0;
fixed[${n}] hrsCap_tileX;
fixed[${n}] hrsCap_tileY;
trigger hrsCap_gt_Tile;
trigger hrsCap_gt_Sweep;
trigger hrsCap_gt_Zoom;
trigger hrsCap_gt_Quit;
trigger hrsCap_gt_Freeze;
trigger hrsCap_gt_Look;
trigger hrsCap_gt_Normal;
trigger hrsCap_gt_Clip;
trigger hrsCap_gt_Pitch;
trigger hrsCap_gt_Sky;
trigger hrsCap_gt_Black;
trigger hrsCap_gt_Bounds;
trigger hrsCap_gt_Fov;
trigger hrsCap_gt_Clean;
trigger hrsCap_gt_Move;${markerDecl}

// The status strip: a dialog in the top-left corner, one column of ${STATUS_CELLS} black-or-white cells, redrawn
// at the end of every chat command (and every sweep). The capture reads it from the screen
// far faster than it can judge the picture, so it knows when a command has been carried out
// and rendered. Cells: 0 white, 1 black (locator), then bits: command sequence number (8),
// tile (10), camera target x and y in 1/64 cells (15 each), markers shown, sky white, sky
// black, tick (flips every sweep), parity of all bits, and a black end cell.
const int hrsCap_statusCells = ${STATUS_CELLS};
const int hrsCap_statusCell = ${STATUS_CELL_UNITS};  // cell size in interface units
int hrsCap_status = c_invalidDialogId;
int[${STATUS_CELLS}] hrsCap_statusCtl;
int hrsCap_seq = 0;
int hrsCap_tick = 0;
int hrsCap_markersShown = 0;
int hrsCap_skyState = 0;  // 1 white, 2 black, 0 other
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

void hrsCap_StatusCell (int lp_cell, int lp_bit) {
    if ((lp_bit != 0)) {
        DialogControlSetPropertyAsColor(hrsCap_statusCtl[lp_cell], c_triggerControlPropertyColor, PlayerGroupAll(), Color(100.0, 100.0, 100.0));
    }
    else {
        DialogControlSetPropertyAsColor(hrsCap_statusCtl[lp_cell], c_triggerControlPropertyColor, PlayerGroupAll(), Color(0.0, 0.0, 0.0));
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
    hrsCap_StatusBits(10, 10, hrsCap_currentTile);
    hrsCap_StatusBits(20, 15, lv_x);
    hrsCap_StatusBits(35, 15, lv_y);
    hrsCap_StatusBits(50, 1, hrsCap_markersShown);
    hrsCap_StatusBits(51, 1, BoolToInt((hrsCap_skyState == 1)));
    hrsCap_StatusBits(52, 1, BoolToInt((hrsCap_skyState == 2)));
    hrsCap_StatusBits(53, 1, hrsCap_tick);
    hrsCap_StatusCell(54, hrsCap_statusParity);
    hrsCap_StatusCell(55, 0);
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

    hrsCap_status = DialogCreate(hrsCap_statusCell, (hrsCap_statusCell * hrsCap_statusCells), c_anchorTopLeft, 0, 0, false);
    DialogSetImageVisible(hrsCap_status, false);
    lv_k = 0;
    for ( ; lv_k < hrsCap_statusCells ; lv_k += 1 ) {
        hrsCap_statusCtl[lv_k] = DialogControlCreate(hrsCap_status, c_triggerControlTypeImage);
        DialogControlSetPropertyAsString(hrsCap_statusCtl[lv_k], c_triggerControlPropertyImage, PlayerGroupAll(), "Assets/Textures/HrsWhite.dds");
        DialogControlSetPropertyAsInt(hrsCap_statusCtl[lv_k], c_triggerControlPropertyImageType, PlayerGroupAll(), c_triggerImageTypeNormal);
        DialogControlSetSize(hrsCap_statusCtl[lv_k], PlayerGroupAll(), hrsCap_statusCell, hrsCap_statusCell);
        DialogControlSetPosition(hrsCap_statusCtl[lv_k], PlayerGroupAll(), c_anchorTopLeft, 0, (lv_k * hrsCap_statusCell));
    }
    hrsCap_statusPlayer = hrsCap_LocalPlayer();
    hrsCap_StatusDraw();
    DialogSetVisible(hrsCap_status, PlayerGroupAll(), true);
}

fixed hrsCap_MapWidth () {
    return PointGetX(RegionGetBoundsMax(RegionEntireMap()));
}

fixed hrsCap_MapHeight () {
    return PointGetY(RegionGetBoundsMax(RegionEntireMap()));
}

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
        UnitRemove(lv_unit);
    }
}

// Remove units; what stays (structures, plus map-mechanic units such as altars with
// --keep-mechanics) loses its health bars, and is hidden in terrain-only mode. Hidden, not
// removed: removing a core could end the game.
void hrsCap_ClearUnits () {
    unitgroup lv_g;
    int lv_u;
    unit lv_unit;

${removalLines}

    lv_g = UnitGroup(null, c_playerAny, RegionEntireMap(), ${remainingFilter}, 0);
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

void hrsCap_ApplyCamera (int lp_player) {
    CameraLockInput(lp_player, true);
    CameraUseHeightDisplacement(lp_player, false);
    CameraSetValue(lp_player, c_cameraValuePitch, hrsCap_pitch, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueYaw, 90.0, 0.0, -1, 10.0);
    if ((hrsCap_zoom > 0.0)) {
        CameraSetValue(lp_player, c_cameraValueDistance, hrsCap_zoom, 0.0, -1, 10.0);
    }
    else {
        CameraSetValue(lp_player, c_cameraValueDistance, hrsCap_distance, 0.0, -1, 10.0);
    }${lensLines}
}

// A camera like real play (distance 34, fov 45), shallow (pitch 35) and facing the map's main
// light (yaw ${fixed(refitYaw)}). The game fits its lighting (the region the main light and
// shadows are computed for) to the last camera it takes as normal and never refits it for the
// capture camera: the rest of the map was drawn without the main light (a dark, straight-edged
// area, a different one each match; dark boxes around holes). So every tile shows this camera
// at its centre for a few frames first, and the game refits around the tile. Only the latest
// look counts, and only a shallow look towards the light cleared every box (found with
// --probe-light sweeps: pitch 52 or other yaws left boxes around some holes).
void hrsCap_NormalCamera (int lp_player) {
    CameraSetValue(lp_player, c_cameraValuePitch, 35.0, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueYaw, ${fixed(refitYaw)}, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueDistance, 34.0, 0.0, -1, 10.0);
    CameraSetValue(lp_player, c_cameraValueFieldOfView, 45.0, 0.0, -1, 10.0);
}


${markerFuncs}
// No fog of war, no unexplored black, no HUD, no messages, a top-down camera.
void hrsCap_Scene () {
    int lv_p;
    int lv_f;

    // (Not VisEnable(c_visTypeFog, false): with fog of war off the reveal below stops working,
    // and only the vision around one team's buildings is drawn lit; found with --probe-light.)
    lv_p = 0;
    for ( ; lv_p <= 15 ; lv_p += 1 ) {
        VisExploreArea(lv_p, RegionEntireMap(), true, false);
        VisRevealArea(lv_p, RegionEntireMap(), 0.0, false);
    }

    lv_p = 1;
    for ( ; lv_p <= 10 ; lv_p += 1 ) {
        if ((PlayerStatus(lv_p) == c_playerStatusActive)) {
${heroUiLines}            hrsCap_ApplyCamera(lv_p);
        }
    }
${uiLines}${boundsLine}${sky ? `
    GameSetBackground(0, "HrsSky${skyColour === 'black' ? 'Black' : 'White'}", 100.0);  // the camera-fixed skybox (sky.mjs)
    hrsCap_skyState = ${skyColour === 'black' ? 2 : 1};` : ''}${hideDoodads.map((type) => `
    libNtve_gf_ShowHideDoodadsInRegion(false, RegionEntireMap(), "${type}");  // a cloud layer, placed as doodads`).join('')}
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    // (With --freeze) paused animations, in a thread of its own: if the game rejects it, only
    // that thread stops. --freeze itself broke the map script on Battlefield of Eternity
    // (every interface panel showing), so it stays off.${freeze ? `
    TriggerExecute(hrsCap_gt_Freeze, false, false);` : ''}
}

// --freeze: pause the animations of everything placed on the map.
bool hrsCap_gt_Freeze_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }${freezeLines}
    return true;
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

// Chat "tile <n> [<x> <y>]": tile n, at the given map position or the planned one. The capture
// gives the position: its grid is planned from the camera bounds the game applies at run time
// (an arena's are far tighter than its map file says), which it measures with "bounds".
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
    // The normal camera at the tile first, a few frames, so the game refits its lighting there
    // (see hrsCap_NormalCamera); then the scene and the capture camera.
    CameraPan(EventPlayer(), Point(hrsCap_curX, hrsCap_curY), 0.0, -1, 10.0, false);
    hrsCap_NormalCamera(EventPlayer());
    Wait(0.25, c_timeReal);  // real time: a map that holds game time still (an arena's selection phase) would never return
    if ((hrsCap_currentTile != lv_index)) {
        return true;  // another tile was asked for meanwhile
    }
    hrsCap_Scene();
    CameraPan(EventPlayer(), Point(hrsCap_curX, hrsCap_curY), 0.0, -1, 10.0, false);${markerCall}
    // A moment before acknowledging: the camera is clamped to its bounds at the next update,
    // and the strip reports where the camera really is (read straight after the pan, it
    // reported the target as asked, and the bounds check saw no clamping at all).
    Wait(0.125, c_timeReal);
    if ((hrsCap_currentTile != lv_index)) {
        return true;
    }
    hrsCap_Ack();
    return true;
}

// Chat "move <n> <x> <y>" (probe): like "tile" but without the scene set-up (the vision reveal,
// camera re-application, interface hiding, sky, doodad hiding), to tell what that costs.
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
    Wait(0.125, c_timeReal);
    hrsCap_Ack();
    return true;
}

// Chat "zoom <distance>" changes the camera distance on the spot (diagnostic: e.g. to find the
// distance at which the markers stop being drawn); "zoom 0" goes back to the planned one.
bool hrsCap_gt_Zoom_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_zoom = StringToFixed(StringWord(EventChatMessage(false), 2));
    hrsCap_ApplyCamera(EventPlayer());
    hrsCap_Ack();
    return true;
}

// Put back what the capture changed: the game's end-of-match sequence (the camera flying to a
// core, the explosion) crashed the game the moment it started, with the interface hidden and
// the camera far out of its normal range. Blizzard's own cinematic exit restores the same
// things.
void hrsCap_Restore () {
    int lv_p;
    int lv_f;
    camerainfo lv_cam;

    TriggerEnable(hrsCap_gt_Sweep, false);${markers ? `
    hrsCap_HideMarkers();` : ''}
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

// Chat "quit" ends the match for that player (no dialog, no score screen), back to the menu:
// the next run's map only loads from there. A defeat, the ending real matches have.
bool hrsCap_gt_Quit_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_Restore();
    Wait(0.5, c_timeReal);
    GameOver(EventPlayer(), c_gameOverDefeat, false, false);
    return true;
}

// Chat "fov <degrees>" changes the field of view on the spot (diagnostic); "fov 0" restores it.
bool hrsCap_gt_Fov_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_fov = StringToFixed(StringWord(EventChatMessage(false), 2));
    hrsCap_ApplyCamera(EventPlayer());
    hrsCap_Ack();
    return true;
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

// Units keep spawning (minions, mercenaries, objectives) and the chat line lingers; sweep both,
// cut the intro short as soon as it starts, and keep the game's labels hidden.
// Chat "clean": the kept shot follows: markers hidden (if any), camera unmoved, messages cleared.
bool hrsCap_gt_Clean_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
${markers ? `    hrsCap_HideMarkers();
` : ''}    // A transmission (an Immortal's voice line) puts a subtitle and its backdrop on screen,
    // outside the frames hidden by the scene; cleared right before the shot.
    TransmissionClearGroup(PlayerGroupAll());
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    hrsCap_Ack();
    return true;
}

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
    hrsCap_tick = (1 - hrsCap_tick);
    hrsCap_StatusDraw();
    return true;
}

// Chat "look <x> <y>" (lighting probe): point the camera at that map position, nothing else.
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

// Chat "normal [distance [pitch [yaw]]]" (lighting probe): the normal camera, with any of
// its values overridden; "fov 0" / "zoom 0" go back to the capture camera.
bool hrsCap_gt_Normal_Func (bool testConds, bool runActions) {
    fixed lv_v;

    if (!runActions) {
        return true;
    }
    hrsCap_NormalCamera(EventPlayer());
    lv_v = StringToFixed(StringWord(EventChatMessage(false), 2));
    if ((lv_v > 0.0)) {
        CameraSetValue(EventPlayer(), c_cameraValueDistance, lv_v, 0.0, -1, 10.0);
    }
    lv_v = StringToFixed(StringWord(EventChatMessage(false), 3));
    if ((lv_v > 0.0)) {
        CameraSetValue(EventPlayer(), c_cameraValuePitch, lv_v, 0.0, -1, 10.0);
    }
    lv_v = StringToFixed(StringWord(EventChatMessage(false), 4));
    if ((lv_v > 0.0)) {
        CameraSetValue(EventPlayer(), c_cameraValueYaw, lv_v, 0.0, -1, 10.0);
    }
    hrsCap_Ack();
    return true;
}

// Three digits, zero-padded, for the bounds label.
string hrsCap_Pad3 (fixed lp_v) {
    string lv_s;

    lv_s = IntToString(FixedToInt(MaxF(0.0, MinF(lp_v, 999.0)) + 0.5));
    if ((StringLength(lv_s) < 2)) {
        lv_s = ("0" + lv_s);
    }
    if ((StringLength(lv_s) < 3)) {
        lv_s = ("0" + lv_s);
    }
    return lv_s;
}

// Chat "bounds": the camera bounds the game applies (an arena's are far tighter than its map
// file says). The camera is sent to the map's four corners and asked where it stopped; the
// result goes on the tile id label as twelve digits, left/bottom/right/top, three each, and
// the camera returns to the current tile and the label goes where the camera actually looks.
bool hrsCap_gt_Bounds_Func (bool testConds, bool runActions) {
    point lv_p;
    fixed lv_l;
    fixed lv_b;
    fixed lv_r;
    fixed lv_t;
    int lv_c;

    if (!runActions) {
        return true;
    }
    if (hrsCap_boundsBusy) {
        return true;
    }
    hrsCap_boundsBusy = true;${markers ? `
    hrsCap_HideMarkers();` : ''}
    lv_l = hrsCap_MapWidth();
    lv_b = hrsCap_MapHeight();
    lv_r = 0.0;
    lv_t = 0.0;
    lv_c = 0;
    for ( ; lv_c < 4 ; lv_c += 1 ) {
        if ((lv_c == 0)) {
            CameraPan(EventPlayer(), Point(0.0, 0.0), 0.0, -1, 10.0, false);
        }
        else if ((lv_c == 1)) {
            CameraPan(EventPlayer(), Point(hrsCap_MapWidth(), 0.0), 0.0, -1, 10.0, false);
        }
        else if ((lv_c == 2)) {
            CameraPan(EventPlayer(), Point(hrsCap_MapWidth(), hrsCap_MapHeight()), 0.0, -1, 10.0, false);
        }
        else {
            CameraPan(EventPlayer(), Point(0.0, hrsCap_MapHeight()), 0.0, -1, 10.0, false);
        }
        Wait(0.2, c_timeReal);
        lv_p = CameraGetTarget(EventPlayer());
        lv_l = MinF(lv_l, PointGetX(lv_p));
        lv_r = MaxF(lv_r, PointGetX(lv_p));
        lv_b = MinF(lv_b, PointGetY(lv_p));
        lv_t = MaxF(lv_t, PointGetY(lv_p));
    }
    CameraPan(EventPlayer(), Point(hrsCap_curX, hrsCap_curY), 0.0, -1, 10.0, false);${markers ? `
    Wait(0.2, c_timeReal);
    if ((hrsCap_idLabel != c_textTagNone)) {
        TextTagSetText(hrsCap_idLabel, StringToText((hrsCap_Pad3(lv_l) + hrsCap_Pad3(lv_b) + hrsCap_Pad3(lv_r) + hrsCap_Pad3(lv_t))));
        TextTagSetPosition(hrsCap_idLabel, CameraGetTarget(EventPlayer()), 0.0);
        TextTagShow(hrsCap_idLabel, PlayerGroupAll(), true);
    }` : ''}
    hrsCap_boundsBusy = false;
    hrsCap_Ack();
    return true;
}

// Chat "sky <colour> [<layer>]": the solid-colour skybox of that name (sky.mjs) on layer 0 (the
// camera-fixed skybox, the default) or 1 (c_backgroundTerrain, the terrain-relative parallax
// layer, which Blizzard's arenas swap). "none" clears the layer, "heaven" is the map's own
// HeavenSkybox (probe reference).
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
    else {
        lv_model = ("HrsSky" + StringCase(StringSub(lv_colour, 1, 1), true) + StringSub(lv_colour, 2, StringLength(lv_colour)));
    }
    GameSetBackground(lv_layer, lv_model, 100.0);
    if ((lv_layer == 0) && (lv_colour == "white")) { hrsCap_skyState = 1; }
    else if ((lv_layer == 0) && (lv_colour == "black")) { hrsCap_skyState = 2; }
    else { hrsCap_skyState = 0; }
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    hrsCap_Ack();
    return true;
}

// Chat "black": the black skybox, for the second clean shot (difference matting: the same view
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

// Chat "pitch <deg>" (lighting probe): the capture camera's pitch (90 is straight down).
bool hrsCap_gt_Pitch_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_pitch = StringToFixed(StringWord(EventChatMessage(false), 2));
    hrsCap_ApplyCamera(EventPlayer());
    hrsCap_Ack();
    return true;
}

// Chat "clip <near> <far>" (lighting probe): the camera's clip planes.
bool hrsCap_gt_Clip_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    CameraSetValue(EventPlayer(), c_cameraValueNearClip, StringToFixed(StringWord(EventChatMessage(false), 2)), 0.0, -1, 10.0);
    CameraSetValue(EventPlayer(), c_cameraValueFarClip, StringToFixed(StringWord(EventChatMessage(false), 3)), 0.0, -1, 10.0);
    hrsCap_Ack();
    return true;
}

void hrsCap_Init () {
    hrsCap_InitTiles();
    hrsCap_StatusInit();${markerInit}
    hrsCap_gt_Clean = TriggerCreate("hrsCap_gt_Clean_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Clean, c_playerAny, "clean", false);
    hrsCap_gt_Move = TriggerCreate("hrsCap_gt_Move_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Move, c_playerAny, "move", false);
    hrsCap_gt_Tile = TriggerCreate("hrsCap_gt_Tile_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Tile, c_playerAny, "tile", false);
    hrsCap_gt_Zoom = TriggerCreate("hrsCap_gt_Zoom_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Zoom, c_playerAny, "zoom", false);
    hrsCap_gt_Freeze = TriggerCreate("hrsCap_gt_Freeze_Func");
    hrsCap_gt_Bounds = TriggerCreate("hrsCap_gt_Bounds_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Bounds, c_playerAny, "bounds", false);
    hrsCap_gt_Pitch = TriggerCreate("hrsCap_gt_Pitch_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Pitch, c_playerAny, "pitch", false);${sky ? `
    hrsCap_gt_Sky = TriggerCreate("hrsCap_gt_Sky_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Sky, c_playerAny, "sky", false);
    hrsCap_gt_Black = TriggerCreate("hrsCap_gt_Black_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Black, c_playerAny, "black", false);` : ''}
    hrsCap_gt_Clip = TriggerCreate("hrsCap_gt_Clip_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Clip, c_playerAny, "clip", false);
    hrsCap_gt_Normal = TriggerCreate("hrsCap_gt_Normal_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Normal, c_playerAny, "normal", false);
    hrsCap_gt_Look = TriggerCreate("hrsCap_gt_Look_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Look, c_playerAny, "look", false);
    hrsCap_gt_Quit = TriggerCreate("hrsCap_gt_Quit_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Quit, c_playerAny, "quit", true);
    hrsCap_gt_Fov = TriggerCreate("hrsCap_gt_Fov_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Fov, c_playerAny, "fov", false);
    hrsCap_gt_Sweep = TriggerCreate("hrsCap_gt_Sweep_Func");
    TriggerAddEventTimePeriodic(hrsCap_gt_Sweep, 0.25, c_timeReal);
}

`;
}
