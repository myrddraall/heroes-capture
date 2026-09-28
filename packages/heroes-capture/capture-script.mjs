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

/**
 * @param {object} o
 * @param {{ x: number, y: number }[]} o.tiles Camera targets, in map cells.
 * @param {boolean} o.hideStructures Hide structures (and kept map-mechanic units) too.
 * @param {number} o.distance Camera distance from its target.
 * @param {{ fov: number, farClip: number } | null} o.lens Narrow field of view; null to leave the map's.
 * @param {boolean} o.unbound Lift the map's camera bounds so edge tiles are not clamped.
 * @param {boolean} o.keepMechanics Remove only heroes, minions, mercenaries, map creatures and
 *   summons, keeping map-mechanic units; otherwise every unit but structures is removed.
 * @param {boolean} o.freeze Pause model animations, so neighbouring screenshots match.
 * @param {boolean} o.showUi Leave the HUD up (to check what the HUD hiding affects).
 * @param {{ dx: number, dy: number }[] | null} o.markers Registration markers, as offsets in map
 *   cells from each tile's camera target: solid magenta text tags pinned to those ground points,
 *   shown only while that tile is on screen. Null for none.
 */
export function captureScript({
  tiles,
  hideStructures,
  distance,
  lens,
  unbound,
  keepMechanics,
  freeze,
  showUi,
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
    CameraSetValue(lp_player, c_cameraValueYaw, 90.0, 0.0, -1, 10.0);`
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
  const markerDecl = markers
    ? `
const int hrsCap_markerCount = ${markerCount};
fixed[${markerCount}] hrsCap_markerDX;
fixed[${markerCount}] hrsCap_markerDY;
int[${markerCount}] hrsCap_markers;
int hrsCap_glyphs = c_textTagNone;
trigger hrsCap_gt_Clean;
trigger hrsCap_gt_Glyphs;`
    : '';
  const markerFuncs = markers
    ? `
void hrsCap_InitMarkers () {
${markers.map((m, k) => `    hrsCap_markerDX[${k}] = ${fixed(m.dx)}; hrsCap_markerDY[${k}] = ${fixed(m.dy)}; hrsCap_markers[${k}] = c_textTagNone;`).join('\n')}
}

// A marker's label: its number, then the tile number's last digit, so every calibration shot
// also proves it shows the tile that was asked for.
text hrsCap_MarkerText (int lp_k, int lp_index) {
    return StringToText((IntToString(lp_k) + IntToString(ModI(lp_index, 10))));
}

// Numbered registration markers: magenta labels with black digits, pinned to known ground
// points. Each tile is shot twice: once with them (calibration: where they land gives the exact
// camera geometry) and once without ("clean": the image that is kept). Created once, while the
// camera is briefly close (the game does not draw labels created under a far camera, but keeps
// drawing them once created), together with the "0123456789" reference label the capture
// learns the digits from; then moved, renumbered and shown per tile.
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
            TextTagCreate(hrsCap_MarkerText(lv_k, lp_index), 24, Point((hrsCap_tileX[lp_index] + hrsCap_markerDX[lv_k]), (hrsCap_tileY[lp_index] + hrsCap_markerDY[lv_k])), 0.0, true, false, PlayerGroupAll());
            TextTagSetColor(TextTagLastCreated(), c_textTagColorText, Color(0.00, 0.00, 0.00));
            TextTagSetColor(TextTagLastCreated(), c_textTagColorBackground, ColorWithAlpha(100.00, 0.00, 100.00, 100.00));
            hrsCap_markers[lv_k] = TextTagLastCreated();
        }
        TextTagCreate(StringToText("0123456789"), 24, Point(hrsCap_tileX[lp_index], hrsCap_tileY[lp_index]), 0.0, false, false, PlayerGroupAll());
        TextTagSetColor(TextTagLastCreated(), c_textTagColorText, Color(0.00, 0.00, 0.00));
        TextTagSetColor(TextTagLastCreated(), c_textTagColorBackground, ColorWithAlpha(100.00, 0.00, 100.00, 100.00));
        hrsCap_glyphs = TextTagLastCreated();
        lv_p = 1;
        for ( ; lv_p <= 10 ; lv_p += 1 ) {
            if ((PlayerStatus(lv_p) == c_playerStatusActive)) {
                hrsCap_ApplyCamera(lv_p);
            }
        }
        return;
    }
    lv_k = 0;
    for ( ; lv_k < hrsCap_markerCount ; lv_k += 1 ) {
        TextTagSetPosition(hrsCap_markers[lv_k], Point((hrsCap_tileX[lp_index] + hrsCap_markerDX[lv_k]), (hrsCap_tileY[lp_index] + hrsCap_markerDY[lv_k])), 0.0);
        TextTagSetText(hrsCap_markers[lv_k], hrsCap_MarkerText(lv_k, lp_index));
        TextTagShow(hrsCap_markers[lv_k], PlayerGroupAll(), true);
    }
}

// Hide the markers (and the reference label) for the clean shot.
void hrsCap_HideMarkers () {
    int lv_k;

    lv_k = 0;
    for ( ; lv_k < hrsCap_markerCount ; lv_k += 1 ) {
        if ((hrsCap_markers[lv_k] != c_textTagNone)) {
            TextTagShow(hrsCap_markers[lv_k], PlayerGroupAll(), false);
        }
    }
    if ((hrsCap_glyphs != c_textTagNone)) {
        TextTagShow(hrsCap_glyphs, PlayerGroupAll(), false);
    }
}

// Chat "clean": hide the markers, camera unmoved.
bool hrsCap_gt_Clean_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_HideMarkers();
    return true;
}

// Chat "glyphs": show the reference label at the current tile's centre (markers hidden), for
// the capture to learn the game font's digits.
bool hrsCap_gt_Glyphs_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_HideMarkers();
    if ((hrsCap_glyphs != c_textTagNone)) {
        TextTagSetPosition(hrsCap_glyphs, Point(hrsCap_tileX[hrsCap_currentTile], hrsCap_tileY[hrsCap_currentTile]), 0.0);
        TextTagShow(hrsCap_glyphs, PlayerGroupAll(), true);
    }
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
    hrsCap_gt_Clean = TriggerCreate("hrsCap_gt_Clean_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Clean, c_playerAny, "clean", true);
    hrsCap_gt_Glyphs = TriggerCreate("hrsCap_gt_Glyphs_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Glyphs, c_playerAny, "glyphs", true);`
    : '';

  return `//--------------------------------------------------------------------------------------------------
// Map capture (injected by heroes-replay-stats tools/map-capture)
// Chat "tile <n>" moves the camera to tile n of the capture grid.
//--------------------------------------------------------------------------------------------------
const bool hrsCap_hideStructures = ${hideStructures};
const fixed hrsCap_distance = ${fixed(distance)};
fixed hrsCap_zoom = 0.0; // set by chat "zoom <distance>"; 0 means the planned distance
fixed hrsCap_fov = 0.0; // set by chat "fov <degrees>"; 0 means the planned field of view
const int hrsCap_tileCount = ${n};
int hrsCap_currentTile = 0;
fixed[${n}] hrsCap_tileX;
fixed[${n}] hrsCap_tileY;
trigger hrsCap_gt_Tile;
trigger hrsCap_gt_Sweep;
trigger hrsCap_gt_Zoom;
trigger hrsCap_gt_Quit;
trigger hrsCap_gt_Fov;${markerDecl}

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
    CameraSetValue(lp_player, c_cameraValuePitch, 90.0, 0.0, -1, 10.0);
    if ((hrsCap_zoom > 0.0)) {
        CameraSetValue(lp_player, c_cameraValueDistance, hrsCap_zoom, 0.0, -1, 10.0);
    }
    else {
        CameraSetValue(lp_player, c_cameraValueDistance, hrsCap_distance, 0.0, -1, 10.0);
    }${lensLines}
}

${markerFuncs}
// No fog of war, no unexplored black, no HUD, no messages, a top-down camera.
void hrsCap_Scene () {
    int lv_p;
    int lv_f;

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
${uiLines}${boundsLine}${freezeLines}
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
}

bool hrsCap_gt_Tile_Func (bool testConds, bool runActions) {
    int lv_index;

    lv_index = StringToInt(StringWord(EventChatMessage(false), 2));
    if (((lv_index < 0) || (lv_index >= hrsCap_tileCount))) {
        return true;
    }
    if (!runActions) {
        return true;
    }
    hrsCap_currentTile = lv_index;
    hrsCap_ClearUnits();
    hrsCap_Scene();
    CameraPan(EventPlayer(), Point(hrsCap_tileX[lv_index], hrsCap_tileY[lv_index]), 0.0, -1, 10.0, false);${markerCall}
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
    return true;
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

// Chat "quit" ends the match for that player (no dialog, no score screen), back to the menu:
// the next run's map only loads from there.
bool hrsCap_gt_Quit_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    GameOver(EventPlayer(), c_gameOverTie, false, false);
    return true;
}

// Chat "fov <degrees>" changes the field of view on the spot (diagnostic); "fov 0" restores it.
bool hrsCap_gt_Fov_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_fov = StringToFixed(StringWord(EventChatMessage(false), 2));
    hrsCap_ApplyCamera(EventPlayer());
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
    }
}

// Units keep spawning (minions, mercenaries, objectives) and the chat line lingers; sweep both,
// cut the intro short as soon as it starts, and keep the game's labels hidden.
bool hrsCap_gt_Sweep_Func (bool testConds, bool runActions) {
    if (!runActions) {
        return true;
    }
    hrsCap_SkipIntro();
    hrsCap_HideLabels();
    hrsCap_ClearUnits();
    UIClearMessages(PlayerGroupAll(), c_messageAreaAll);
    return true;
}

void hrsCap_Init () {
    hrsCap_InitTiles();${markerInit}
    hrsCap_gt_Tile = TriggerCreate("hrsCap_gt_Tile_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Tile, c_playerAny, "tile", false);
    hrsCap_gt_Zoom = TriggerCreate("hrsCap_gt_Zoom_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Zoom, c_playerAny, "zoom", false);
    hrsCap_gt_Quit = TriggerCreate("hrsCap_gt_Quit_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Quit, c_playerAny, "quit", true);
    hrsCap_gt_Fov = TriggerCreate("hrsCap_gt_Fov_Func");
    TriggerAddEventChatMessage(hrsCap_gt_Fov, c_playerAny, "fov", false);
    hrsCap_gt_Sweep = TriggerCreate("hrsCap_gt_Sweep_Func");
    TriggerAddEventTimePeriodic(hrsCap_gt_Sweep, 0.25, c_timeGame);
}

`;
}
