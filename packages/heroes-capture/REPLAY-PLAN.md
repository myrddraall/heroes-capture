# Replay capture: plan for an experimental branch

**Goal:** remove the remaining _temporal_ seams: lighting, glows and animations that change
between one screenshot and the next, and show as steps where two screenshots meet. The live
capture takes each screenshot at a slightly later game time, so neighbouring screenshots can
disagree. If every screenshot is taken at the **same frozen game time**, they cannot.

**Idea (Erd's):** record a short custom match, then capture from its replay. A paused replay
freezes game time completely, and it can be seeked. So: pause at time X, where registration
markers are visible, to learn the camera geometry; then seek to time Y, where the markers are
gone, and take every clean screenshot at that one frozen moment.

Branch: `experiment/replay-capture`, off whatever state `tools/map-capture` is in when we start.

## What we already know

| Fact                                                                                 | Evidence                                                                                                                 |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| Replays re-run the map script: triggers fire again at the same game times            | A replay is a re-simulation from recorded inputs; our own parser (heroprotocol) decodes them as input streams, not video |
| Replays record each player's camera                                                  | heroprotocol decodes `SCameraUpdateEvent` (target, distance, pitch, yaw) from `replay.game.events`                       |
| Pausing freezes everything driven by game time                                       | Observed by Erd in normal replays                                                                                        |
| Seeking forward works (fast re-simulation); seeking back restarts from the beginning | Standard replay behaviour; so the plan only ever seeks forward                                                           |
| Text-tag markers draw at a far camera only if they were created under a close one    | Found in this tool (the zoom probe); matters because in a replay we don't control the camera at creation time            |
| Numbered markers, marker fitting and marker-anchored stitching                       | Built and tested for the live capture, then removed from it once tiles were placed by the status strip's camera echo; `markers.py`, the script's marker code and stitch's `markers.json` support are in commit `da06a41` |

## The pipeline

1. **Record.** A variant of the capture script, for recording rather than capturing:
   - A lattice of numbered markers evenly over the whole map, each label encoding its own map
     position (for example a two-digit column and row), visible from the start. Each label is
     created under a close camera, then the recording camera pulls back (see the table above).
   - The same scene set-up as now: units removed, fog revealed, HUD hidden, intro skipped.
   - At time X + a few seconds the markers are hidden, and at time Y + a few seconds the match
     ends (`GameOver`, as the `quit` command does now), so the replay is only ~20 seconds long.
   - Nobody plays: the recording is a fixed script, the same every time.
2. **Load the replay** and pause it. A small launcher script, like `capture.py`'s launch.
3. **Calibration pass at time X.** Seek to X, pause. For each camera position: move the camera
   (see "Moving the camera" below), take a screenshot, read the markers → exact geometry.
4. **Clean pass at time Y.** Seek to Y (forward), pause. Repeat exactly the same camera moves,
   in the same order, and take the clean screenshots. Every one is at game time Y.
5. **Stitch** with the existing `stitch.py`: the calibration fits go into `markers.json`, the
   clean screenshots into `tiles/`, exactly as the live two-shot capture writes them.

**Check built in:** each clean shot is compared with its calibration shot of the same camera
position. Same world, same camera, so apart from the markers they should correlate almost
perfectly. A clean shot that doesn't match means the camera move wasn't reproducible, and it
gets flagged.

## Moving the camera in a replay

This is the new part: chat commands don't exist in a replay, and the map script can't take
input. Options, in order of preference:

- **Minimap clicks.** Clicking the minimap centres the camera on that map point. It's
  deterministic, so the same click at X and at Y gives the same camera. Needs the minimap
  visible for the click and hidden for the shot (or cropped out).
- **Keyboard panning** from a known start, with fixed key-hold times. Less precise; the marker
  calibration would still measure where each move really landed.
- **The recorded player's camera** (the replay's player view). The recording could tour the
  camera itself, so the replay just follows it. **But** that ties each camera position to a
  different game time, which brings back the temporal seams this plan is meant to remove. Use
  it only to help calibrate, not for the clean pass.

## Experiments, in order (each can kill the plan cheaply)

1. **Does an offline custom match save a replay?** Next live run: after it ends (the `quit`
   command ends it with a real game-over), look in
   `Documents\Heroes of the Storm\Accounts\<id>\<region>-Hero-<n>\Replays\` for a new
   `.StormReplay`. _If not:_ the plan is dead unless there's a setting that forces saving.
2. **Does that replay load?** A replay refers to its map; a custom map run from a local file
   may not be found when the replay is opened. Try opening it from the game's Replays tab, and
   from the file. _If not:_ try placing the map where Try Mode's map lives
   (`maps\heroes\singleplayermaps`), since that path is known to the game.
3. **Pause and seek.** Can we pause at an exact second and seek forward to another? Find the
   hotkeys (pause, speed, jump) and whether the timeline can be clicked to a time.
4. **Camera limits in a paused replay.** How far can the free camera zoom out, and can it look
   straight down? If the zoom stops well short of our distance-214 flat view, we need more
   camera positions (a closer camera), and perspective lean grows. Measure it with the
   markers, the same way the zoom probe did.
5. **Replay UI.** Can the replay's own panels (timeline, player list) be hidden, or at least
   cropped out consistently?
6. **Minimap-click reproducibility.** Same click at time X and time Y → same camera, to the
   pixel? The built-in correlation check answers this.

Experiments 1 and 2 cost nothing beyond one live run. Only if both pass is it worth building
anything.

## Effort, if the experiments pass

- Recording variant of the capture script: small (the scene set-up exists; add the lattice
  and the timed hide and end).
- Replay driver (load, pause, seek, camera moves, shots): the bulk of the work, mostly
  discovering and automating the replay controls.
- Stitching: none; it reuses the two-shot outputs.
