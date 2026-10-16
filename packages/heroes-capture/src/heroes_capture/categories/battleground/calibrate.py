"""The battleground's part of a map's calibration (capture/calibration.py), in its own match; its
stages (specifics.CALIBRATION_STAGES: motion, structures, rubble-at-home) can each run on their
own (`map calibrate --only`):

1. what still moves while the map is paused: each tile of the grid looked at as the tiles see it
   (the map paused by the sweep once ready), twice MOTION_GAP apart; each patch that moved
   (moving_patches) placed on the map and named by what stands there (what_is_at: a structure, a
   doodad, or nothing known: water, most often), with a two-frame picture. The data report
   (common/actors.py, made when the map is prepared) says what each of those is made of;
2. then, with the scene hidden as the elements capture hides it ("el fadeall 0", "el env off"),
   each structure in turn:
   - its look: faded in alone, shot over the white sky once it holds still (or after
     shots.STILL_CAP: it moves while paused, and how much is kept);
   - its side switch: our player put on Chaos and back ("side 2", "side 1"), how long each takes
     to show, or that nothing on it changes; what moves on it by itself left out of the
     comparison (a flickering fire isn't a switch);
   - its copy's spot (the structures the render copies: not the cores nor
     elements.COPY_EXCLUDED): the candidates prepare found (elements.copy_candidates: in the
     bounds, clear of everything, under the same lighting map), nearest first, each tried until
     one passes: a copy made there ("el copy 0 make"), shot as its structure was and compared with
     it (matches: drawn as much, as bright, no colour cast, no more black: Dragon Shire's void
     corners draw a copy black), then brought down, its fall watched until it settles
     (settle_time) and its rubble checked for black too. A spot that fails rules out the
     candidates round it (FAILED_REACH: a void is an area). The structures with the fewest
     candidates go first; each accepted spot is then taken. The first copy of each kind on each
     team to pass is filmed falling, a frame every FILM_STEP, for the review to pick the moment its
     rubble looks best (with one look from the render's camera, element_capture.VIEW_BACK times
     the capture's distance back, where a fall gives off its smoke and dust: filmed from there,
     the first calibration's films came out empty);
3. last, one structure of each kind on each team (not the cores) brought down on its own cell and
   its rubble shot with the scene hidden and then shown (rubble_at_home): the rubble as it really
   lies, to tell whether hiding the scene or copying the structure darkens it (Dragon Shire's
   forts' rubble came out black in the first renders).

What it found: motion (every patch that moved while paused), structures (by structure id: whether
it changes with the side, toChaos and toOrder in seconds, and the share of it that moves while
paused), copySpots (by structure id), falls (by "<type>|<owner>": the median seconds its falls took
to settle, and its film), tries (every spot tried, with the measurements and why it failed),
rubbleAtHome (by kind: one structure brought down on its own cell, its rubble with the scene
hidden and shown, how much hiding darkens it, and how its copy's compared), and the data report. Pictures of all of it go to calibration/ in the run's folder, for the review.
"""

import math
import statistics
import time
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from heroes_capture.capture.shots import still_frame, tile_command
from heroes_capture.categories.battleground.elements import rubble_radius, standing_team_colour
from heroes_capture.common import ui
from heroes_capture.common.game_control import Recoverable, settle, step
from heroes_capture.common.geometry import circle_box, screen_point
from heroes_capture.common.imaging import channel_diff
from heroes_capture.common.runlog import log, stage, warn

# Candidate spots tried per structure at most; the cells round a failed spot whose candidates are
# skipped.
TRIES, FAILED_REACH = 8, 6.0
# A pixel further than SKY_DIFF from the sky's colour (its largest channel) is drawn; one darker
# than DARK in every channel is black.
SKY_DIFF, DARK = 30, 24
# A copy passes when it is drawn over at least DRAWN_LEAST of the pixels its structure is, its mean
# brightness is within BRIGHTNESS_MOST of the structure's, its colour (the brightness evened out)
# within CAST_MOST in each channel (Battlefield of Eternity's Hell side casts orange), and no more
# than DARK_MORE_MOST more of it is black.
DRAWN_LEAST, BRIGHTNESS_MOST, CAST_MOST, DARK_MORE_MOST = 0.5, 0.25, 25.0, 0.15
# Seconds a new copy is left before its look (then until it holds still): a structure made anew
# plays its birth (a level 3 tower's rises).
COPY_SETTLE = 1.5
# Rubble more than this share black fails (a copy's rubble in Dragon Shire's void came out black).
RUBBLE_DARK_MOST = 0.6
# A copy whose rubble covers less than this share of its circle left none (Dragon Shire's wall and
# gate copies near the map's edges fell to nothing; the least real rubble there covered 0.0053):
# the spot fails.
RUBBLE_DRAWN_LEAST = 0.001
# Seconds a side switch is watched for at most; a switch shows once more than SWITCH_PIXELS pixels
# of the structure's circle differ by more than CHANGE (what moves by itself left out).
SIDE_CAP, SWITCH_PIXELS = 2.5, 25
CHANGE = 24
# A fall: a frame every FALL_GAP seconds; settled once fewer than FALL_STILL of its circle's pixels
# change between frames for FALL_HOLD seconds, at least FALL_LEAST after the kill; watched for
# FALL_CAP at most.
FALL_GAP, FALL_STILL, FALL_HOLD, FALL_LEAST, FALL_CAP = 0.1, 0.01, 0.5, 0.5, 6.0
# A filmed fall: a frame every FILM_STEP seconds for FILM_SECONDS. A film's frames are kept as shot,
# FILM_MOST pixels square at most (the review shows a frame that big), FILM_COLUMNS to a row (a
# WebP is 16383 pixels wide at most).
FILM_STEP, FILM_SECONDS, FILM_MOST, FILM_COLUMNS = 0.25, 6.0, 720, 8
# The camera the render brings structures down under (element_capture.VIEW_BACK).
VIEW_BACK = 2.5
# What moves while paused: two looks MOTION_GAP seconds apart; pixels that differ by more than
# CHANGE, in blocks of MOTION_BLOCK pixels; a patch of at least MOTION_LEAST blocks counts. Patches
# from overlapping tiles within MOTION_SAME cells of one another are one. A structure moving over
# more than MOVING_SHARE of its circle while hidden is noted as moving.
MOTION_GAP, MOTION_BLOCK, MOTION_LEAST, MOTION_SAME, MOVING_SHARE = 0.6, 8, 4, 3.0, 0.002
# A doodad within this many cells of a patch's middle is taken to be what moved.
DOODAD_REACH = 3.0
# Rubble at home: pixels left out at its edges when comparing it with the scene hidden and shown;
# a ratio below HIDDEN_DARKER is reported as darkened. SHOW_SETTLE: seconds for the scene to be
# drawn again once shown.
EDGE_PIXELS, HIDDEN_DARKER, SHOW_SETTLE = 4, 0.9, 0.3
# Commands in a row the map doesn't answer before the match counts as lost.
FAILURES = 3


def sky_colour(frame: np.ndarray) -> np.ndarray:
    """The sky's colour behind the hidden scene: the median of the frame's top right corner (the
    strip is in the top left, a note in the middle)."""
    return np.median(frame[:32, -32:, :3].reshape(-1, 3), axis=0)


def appearance(frame: np.ndarray, centre: tuple[float, float], radius: float) -> dict:
    """What a circle of the frame (`centre` and `radius` in pixels) shows over the sky: the share of
    it drawn, the drawn pixels' mean colour, and the share of them black."""
    v0, v1, u0, u1 = circle_box(centre, radius, frame.shape)
    ys, xs = np.mgrid[v0:v1, u0:u1]
    inside = (xs + 0.5 - centre[0]) ** 2 + (ys + 0.5 - centre[1]) ** 2 <= radius ** 2
    crop = frame[v0:v1, u0:u1, :3]
    drawn = (np.abs(crop.astype(np.int16) - sky_colour(frame).astype(np.int16)).max(axis=2) > SKY_DIFF) & inside
    pixels = crop[drawn].astype(np.float32)
    if not len(pixels):
        return {"drawn": 0.0, "mean": [0.0, 0.0, 0.0], "dark": 0.0}
    return {"drawn": round(len(pixels) / max(int(inside.sum()), 1), 4), "mean": [round(float(v), 1) for v in pixels.mean(axis=0)],
            "dark": round(float((pixels.max(axis=1) < DARK).mean()), 4)}


def matches(structure: dict, copy: dict) -> str | None:
    """Whether a copy looks as its structure does (their appearance): None when it does, else why
    not. A structure with nothing drawn has nothing to compare."""
    if not structure["drawn"]:
        return None
    if copy["drawn"] < DRAWN_LEAST * structure["drawn"]:
        return "hardly drawn"
    if copy["dark"] - structure["dark"] > DARK_MORE_MOST:
        return "black"
    ratio = statistics.fmean(copy["mean"]) / max(statistics.fmean(structure["mean"]), 1.0)
    if abs(ratio - 1) > BRIGHTNESS_MOST:
        return "darker" if ratio < 1 else "brighter"
    if max(abs(c / ratio - s) for c, s in zip(copy["mean"], structure["mean"])) > CAST_MOST:
        return "colour cast"
    return None


def settle_time(samples: list[tuple[float, float]]) -> float | None:
    """When a fall settled, from (seconds after the kill, share of its circle changed since the frame
    before): the first sample at least FALL_LEAST in from which every one for FALL_HOLD seconds
    changed fewer than FALL_STILL; None before that has been seen."""
    for i, (t, _) in enumerate(samples):
        if t < FALL_LEAST or samples[-1][0] < t + FALL_HOLD:
            continue
        if all(share < FALL_STILL for s, share in samples[i:] if s <= t + FALL_HOLD):
            return round(t, 2)
    return None


def usable(spot: tuple[float, float], bounds: dict, used: list[tuple[float, float, float]], failed: list[tuple[float, float]],
           rubble: float) -> bool:
    """Whether a candidate is worth trying: the camera can centre on it (the game's bounds), clear
    of the copies already placed (their rubble and this one's) and of every failed spot's
    neighbourhood (FAILED_REACH)."""
    return (bounds["left"] <= spot[0] <= bounds["right"] and bounds["bottom"] <= spot[1] <= bounds["top"]
            and all(math.dist(spot, (x, y)) >= r + rubble for x, y, r in used)
            and all(math.dist(spot, f) >= FAILED_REACH for f in failed))


def calibration_order(structures: list[dict], candidates: dict) -> list[dict]:
    """The copied structures (those with candidates), the fewest candidates first, then the rest."""
    copied = sorted((u for u in structures if str(u["id"]) in candidates), key=lambda u: len(candidates[str(u["id"])]))
    return copied + [u for u in structures if str(u["id"]) not in candidates]


def moving_patches(moved: np.ndarray) -> list[tuple[tuple[int, int, int, int], tuple[float, float], int]]:
    """The patches of a frame that moved (`moved`: each pixel's), in blocks of MOTION_BLOCK pixels,
    at least MOTION_LEAST blocks each: their box (v0, v1, u0, u1), middle (u, v) and size in
    pixels."""
    b = MOTION_BLOCK
    h, w = moved.shape[0] // b * b, moved.shape[1] // b * b
    blocks = moved[:h, :w].reshape(h // b, b, w // b, b).any(axis=(1, 3))
    labels, count = ndimage.label(blocks)
    found = []
    for n, (rows, cols) in enumerate(ndimage.find_objects(labels), start=1):
        if int((labels[rows, cols] == n).sum()) < MOTION_LEAST:
            continue
        box = (rows.start * b, rows.stop * b, cols.start * b, cols.stop * b)
        pixels = int(moved[box[0]:box[1], box[2]:box[3]].sum())
        ys, xs = np.nonzero(moved[box[0]:box[1], box[2]:box[3]])
        found.append((box, (float(xs.mean()) + box[2], float(ys.mean()) + box[0]), pixels))
    return found


def cell_at(point: tuple[float, float], camera: dict, screen: dict, px_per_cell: float) -> tuple[float, float]:
    """The cell at a point of a shot (geometry.screen_point's inverse)."""
    return camera["x"] + (point[0] - screen["w"] / 2) / px_per_cell, camera["y"] - (point[1] - screen["h"] / 2) / px_per_cell


def what_is_at(cell: tuple[float, float], structures: list[dict], doodads: list[dict]) -> dict:
    """What stands at a cell: a structure whose circle holds it, else the nearest doodad within
    DOODAD_REACH, else nothing known (water, most often)."""
    near = [u for u in structures if math.dist(cell, (u["x"], u["y"])) <= u["radius"]]
    if near:
        u = min(near, key=lambda u: math.dist(cell, (u["x"], u["y"])))
        return {"kind": "structure", "type": u["type"], "id": u["id"]}
    near = [d for d in doodads if math.dist(cell, (d["x"], d["y"])) <= DOODAD_REACH]
    if near:
        return {"kind": "doodad", "type": min(near, key=lambda d: math.dist(cell, (d["x"], d["y"])))["type"]}
    return {"kind": "unknown"}


def same_patches(found: list[dict]) -> list[dict]:
    """Patches seen from overlapping tiles merged: within MOTION_SAME cells of a bigger one, the
    smaller is the same thing."""
    kept: list[dict] = []
    for patch in sorted(found, key=lambda p: -p["pixels"]):
        if all(math.dist((patch["x"], patch["y"]), (k["x"], k["y"])) > MOTION_SAME for k in kept):
            kept.append(patch)
    return kept


def compare_shown(hidden: np.ndarray, shown: np.ndarray, centre: tuple[float, float], radius: float) -> dict:
    """Rubble shot with the scene hidden and then shown, the camera still: its look over the sky
    (appearance), and the ratio of its brightness with the scene hidden to shown, over the pixels
    it covers less a few at its edges (blended with the sky or the ground); None without any."""
    look = appearance(hidden, centre, radius)
    v0, v1, u0, u1 = circle_box(centre, radius, hidden.shape)
    ys, xs = np.mgrid[v0:v1, u0:u1]
    inside = (xs + 0.5 - centre[0]) ** 2 + (ys + 0.5 - centre[1]) ** 2 <= radius ** 2
    crop = hidden[v0:v1, u0:u1, :3].astype(np.int16)
    drawn = (np.abs(crop - sky_colour(hidden).astype(np.int16)).max(axis=2) > SKY_DIFF) & inside
    inner = ndimage.binary_erosion(drawn, iterations=EDGE_PIXELS)
    if not inner.any():
        return {"look": look, "ratio": None}
    a = float(hidden[v0:v1, u0:u1, :3][inner].mean())
    b = float(shown[v0:v1, u0:u1, :3][inner].mean())
    return {"look": look, "ratio": round(a / max(b, 1.0), 3)}


def home_wait(u: dict, falls: dict) -> float:
    """How long a structure brought down at home is left before it's paused: Battlefield of
    Eternity's wait for its kind, else how long its kind's falls took to settle, else 2 s."""
    from heroes_capture.categories.battleground.elements import FALL_WAITS

    kind = u["type"]
    table = next((w for prefix, suffix, owner, w in FALL_WAITS if kind.startswith(prefix) and kind.endswith(suffix) and u.get("owner") == owner), None)
    settled = (falls.get(f"{kind}|{u.get('owner')}") or {}).get("settle")
    return table if table is not None else settled if settled is not None else 2.0


def home_order(structures: list[dict]) -> list[dict]:
    """One structure of each kind on each team to bring down at home (not the cores: the match
    would end), the forts and keeps last (a fort's fall takes a standalone tower near it down too)."""
    chosen: dict[str, dict] = {}
    for u in structures:
        if not u["core"]:
            chosen.setdefault(f"{u['type']}|{u.get('owner')}", u)
    return sorted(chosen.values(), key=lambda u: u["type"].startswith("TownTownHall"))


# The pausing probe's ways of pausing, in the order they are added, each a step's commands (step 0:
# the map's own pause, as the tiles are shot). The messages in the forms Blizzard's actor data
# sends them. (Its earlier runs, with no effect on the forts' fires and swirl: "AnimSetPausedAll"
# by actor centre, game and visual radius and to every unit's scope, "AnimSetTimeScaleGlobal 0",
# "AnimGlobalLoopsStop" with and without "Instant", "AnimGlobalLoopsClear", the clock at 0.) The
# last may leave the map unable to answer: the probe then ends there.
GL_BRACKETS = ("GL_BSD", "GL_BSD_A", "GL_BSD_B", "GL_BSD_C", "GL_BSD_D", "GL_BSD_E")
# (Its fourth run sent, to every actor and unit scope: "AnimSetPaused" on BSD, Stand and the
# global-loop animations, "AnimSetTimeScale" and "AnimSetTimeScaleGlobal ... TimeScale" near 0,
# "AnimSetTimeScaleAbsolute Stand" near 0, "AnimBracketStop" on the global-loop animations, the
# clock at 0.01, and "DestroyParticles": the forts' fires burned on through it all, their look
# unchanged. So this run first asks whether anything sent reaches what is drawn: the forts tinted
# red, by their type.) A step's commands: "<message>" to every actor and unit scope, "type
# <unit type> <message>" to that type's units' scopes, or a script verb ("worldfx", "clock <n>").
FORT = "TownTownHallL2"
# (Its fifth run, on a map with the freeze settings in the models' own entries: the forts held
# still under the map's own pause, the tint reached them, but their braziers came out unlit. Its
# sixth let the forts play 2 s and 6 s before pausing them again, and everything 3 s: the swirl
# moved and froze again, the braziers stayed dark. So the fire hangs on something that isn't
# playing: this run starts what it may hang on, then pauses.)
GL_STARTS = [f"type {FORT} AnimBracketStart {b} GLbirth{s} GLstand{s} GLdeath{s} FullMatch"
             for b, s in zip(GL_BRACKETS, ("", ",A", ",B", ",C", ",D", ",E"))]
# (Its seventh run tried those from wherever the camera was: the braziers stayed dark in every shot;
# the user saw fire frozen in some, gone once the camera moved. A model's particles may run, and
# be kept, only near the camera, as a fall's are: so this run lights the fort with the camera on it,
# then sends the camera away and back.) "at target": the camera on the target; "at away": on the
# camera bounds' corner furthest from it.
PAUSE_FOCUS = (FORT,)
# (Its ninth run: lit with everything let run and paused with the units, the fire was gone once the
# camera went away and back, though the eighth run had kept it; and the render's forts, lit once for
# all before the shots, came out unlit. So the elements capture now lights each structure as its
# shots begin ("el light"). Its tenth run was on a map whose models' bounding boxes were padded 500
# cells out (BoundingBoxPadding, so as never to be culled): the forts stayed unlit without the
# camera on them, so that went; lit in place with the scene hidden, they came out lit, as the
# render's forts did.) "light": the target's structure lit in place.
PAUSE_STEPS = (
    ("the map's own pause", []),
    ("everything let run 3 s with the camera far away, paused, then looked at", ["at away", "top pause 0", "wait 3", "top pause"]),
    ("the scene hidden as for the standing shots", ["top el env off"]),
    ("lit in place (el light), the scene hidden", ["light"]),
    ("the scene shown again", ["top el env on"]),
)
# (The first run's targets were mostly beside the river, its boxes four cells round full of water no
# pause stills: the water is hidden now, the boxes smaller, and every moving structure kind is in.)
STILLED, PAUSE_TARGETS, PAUSE_REACH = 0.1, 16, 2.5


def pause_command(command: str) -> str:
    """A pausing step's command as the script takes it (PAUSE_STEPS): messages with "~" for their
    spaces."""
    if command.startswith("top "):  # a command of its own, not an "el" verb (the capture's "pause [0]")
        return command[4:]
    if command.startswith(("clock ", "worldfx", "play", "freeze")):
        return f"el {command}"
    if command.startswith("type "):
        _, unit, message = command.split(" ", 2)
        return f"el typemsg {unit} {message.replace(' ', '~')}"
    return f"el pausemsg {command.replace(' ', '~')}"


def away_from(target: dict, bounds: dict) -> tuple[float, float]:
    """The camera bounds' corner furthest from a target."""
    corners = [(bounds[x], bounds[y]) for x in ("left", "right") for y in ("bottom", "top")]
    return max(corners, key=lambda c: math.dist(c, (target["x"], target["y"])))


def pause_targets(motion: list[dict]) -> list[dict]:
    """What the pausing probe looks at: the biggest moving patch of each kind of structure it is
    after (PAUSE_FOCUS), the biggest kinds first, PAUSE_TARGETS at most (the doodads held still
    under the map's own pause once the water beside them was hidden; the water itself is hidden for
    the probe)."""
    biggest: dict[tuple, dict] = {}
    for patch in motion:
        key = (patch["what"]["kind"], patch["what"].get("type", ""))
        if key[0] == "structure" and key[1] in PAUSE_FOCUS and (key not in biggest or patch["pixels"] > biggest[key]["pixels"]):
            biggest[key] = patch
    return sorted(biggest.values(), key=lambda p: (p["what"]["kind"] != "structure", -p["pixels"]))[:PAUSE_TARGETS]


def stilled_at(shares: list[float]) -> int | None:
    """The first step whose motion is below STILLED of step 0's; None if none is (or nothing moved)."""
    if not shares or shares[0] <= 0:
        return None
    return next((k for k, share in enumerate(shares) if k and share < STILLED * shares[0]), None)


def pausing_probe(session, manifest: dict, folder: Path, motion: list[dict], send) -> dict:
    """Each way of pausing (PAUSE_STEPS) added in turn, the motion at each target (pause_targets:
    from the calibration's motion stage) measured after each, the water hidden: which way stills
    what, and whether it changed the look (the last step's view against the first's, where
    neither moved)."""
    targets = pause_targets(motion)
    if not targets:
        warn("  the pausing probe has nothing to look at: run the motion stage first")
        return {"steps": [name for name, _ in PAUSE_STEPS], "targets": []}
    screen, ppc = manifest["screen"], manifest["pxPerCell"]
    send(tile_command(manifest["tiles"][0]))  # the scene as the tiles have it
    send("el water off")  # the water moves whatever is paused: it would swamp what beside it moves
    cells = {u["id"]: (u["x"], u["y"]) for u in (manifest.get("elements") or {}).get("structures", [])}
    found = [{"what": t["what"], "x": t["x"], "y": t["y"], "shares": [], "structure": cells.get(t["what"].get("id"), (t["x"], t["y"]))}
             for t in targets]
    looks: list[dict] = [{} for _ in targets]
    stopped = None
    with ui.bar(len(PAUSE_STEPS) * len(targets), "pausing tried") as advance:
        for k, (name, commands) in enumerate(PAUSE_STEPS):
            for command in commands:
                if command.startswith("wait "):
                    settle(float(command.split()[1]))
                elif command.startswith("light"):
                    seconds = command.split()[1] if " " in command else "2"
                    send(f"el light {found[0]['structure'][0]:g} {found[0]['structure'][1]:g} {seconds}")
                elif command.startswith("fade "):
                    send(f"el fades {found[0]['structure'][0]:g} {found[0]['structure'][1]:g} {command.split()[1]}")
                elif command.startswith("at "):
                    x, y = (found[0]["x"], found[0]["y"]) if command == "at target" else away_from(found[0], manifest["cameraBounds"])
                    send(f"el at {x:.2f} {y:.2f}")
                else:
                    send(pause_command(command))
            if commands:
                settle(MOTION_GAP)
            for n, target in enumerate(found):
                def measure() -> None:
                    send(f"el at {target['x']:.2f} {target['y']:.2f}")
                    camera = session.camera({"x": target["x"], "y": target["y"]})
                    a = session.grab(dark_ok=True)
                    settle(MOTION_GAP)
                    b = session.grab(dark_ok=True)
                    if a is None or b is None:
                        target["shares"].append(None)
                        return
                    box = circle_box(screen_point(target, camera, screen, ppc), PAUSE_REACH * ppc, a.shape)
                    v0, v1, u0, u1 = box
                    moved = channel_diff(a[v0:v1, u0:u1], b[v0:v1, u0:u1]) > CHANGE
                    target["shares"].append(round(float(moved.mean()), 4))
                    # How much the look changed since step 0, where neither moved: whether the
                    # step did anything visible at all.
                    crop = a[v0:v1, u0:u1].astype(np.int16)
                    if k == 0:
                        looks[n][0] = (crop, moved)
                        target["changed"] = [0.0]
                    elif 0 in looks[n] and looks[n][0][0].shape == crop.shape:
                        still = ~(looks[n][0][1] | moved)
                        target["changed"].append(round(float(np.abs(crop - looks[n][0][0]).max(axis=2)[still].mean()), 1) if still.any() else None)
                    target[f"picture{k}"] = _moving_picture(folder, f"pausing-{n}-{k}", a, b, box)
                    if k == len(PAUSE_STEPS) - 1:
                        looks[n][k] = (crop, moved)
                try:
                    step(measure, f"pausing step {k}, target {n + 1}")
                except Recoverable:
                    if k < len(PAUSE_STEPS) - 1:
                        raise
                    warn(f"  the map stopped answering after {name}: the probe ends there")
                    target["shares"].append(None)
                    stopped = name
                advance()
                if stopped:
                    break
            if stopped:
                break
    last = len(PAUSE_STEPS) - 1
    for target, look in zip(found, looks):
        target["stilledAt"] = stilled_at([s or 0.0 for s in target["shares"]])
        target["stilledBy"] = PAUSE_STEPS[target["stilledAt"]][0] if target["stilledAt"] is not None else None
        if 0 in look and last in look:
            (first, m0), (final, m1) = look[0], look[last]
            still = ~(m0 | m1)
            target["lookChange"] = round(float(np.abs(first - final).max(axis=2)[still].mean()), 1) if still.any() else None
        target["pictures"] = [target.pop(f"picture{k}", None) for k in range(len(PAUSE_STEPS))]
        target["first"], target["last"] = target["pictures"][0], target["pictures"][-1]
    for target in found:
        what = target["what"].get("type") or "nothing placed (water?)"
        log(f"  {what}: moving {target['shares']}, look changed {target.get('changed')}; "
            + (f"stilled by {target['stilledBy']}" if target["stilledBy"] else "never stilled")
            + (f"; look changed by {target['lookChange']}" if target.get("lookChange") is not None else ""))
    return {"steps": [name for name, _ in PAUSE_STEPS], "targets": found, "stoppedAnswering": stopped}


# The standing films: one structure of each type (the animation is the same for both teams), swapped
# to its finished model ("el restart": the model it shows once built, from the data report;
# swapping starts its animations from the start, and its global loops) with the camera straight
# down over it as close as keeps all of that model on screen (stand_distance, from the model's
# mesh in its file), and the rest of the scene hidden, filmed a frame every FILM_STEP for as long
# as that model's standing animation takes (stand_seconds) and STAND_MARGIN more for its effects to
# build up (STAND_LEAST at least, STAND_MOST at most), then paused ("el hold"): the review picks the
# moment each type looks best, and the render restarts each structure the same way and pauses it
# there (elements.stand_wait). A film keeps STAND_FRAMES frames at most, evenly spaced (its step
# recorded), cut to what the model can cover. (Restarted by hiding and showing it, a level 3 tower or gate
# replayed its build on a birth model the game then swaps out.)
STAND_LEAST, STAND_MARGIN, STAND_MOST, STAND_FRAMES = 4.0, 3.0, 30.0, 48


def standing_sequences(structure_type: str, report: dict) -> list[dict]:
    """A type's finished model's standing sequences (Stand, GLstand; not its Stand Work ones)."""
    model = (report.get("standModels") or {}).get(structure_type)
    return [q for q in ((report.get("models") or {}).get(model) or {}).get("sequences", [])
            if q["name"].lower().startswith(("stand", "glstand")) and "work" not in q["name"].lower()]


def stand_seconds(structure_type: str, report: dict) -> float:
    """How long a type's standing film runs: its finished model's longest standing sequence and
    STAND_MARGIN more (see the note above)."""
    standing = [q["seconds"] for q in standing_sequences(structure_type, report)]
    return min(STAND_MOST, max(STAND_LEAST, max(standing, default=0.0) + STAND_MARGIN))


def stand_extent(structure_type: str, report: dict) -> tuple[float, float] | None:
    """How far a type's finished model reaches across the ground from the structure's spot
    (whichever way it faces) and how high, in cells: its mesh's (actors.m3_extent), at its largest
    size."""
    model = ((report.get("models") or {}).get((report.get("standModels") or {}).get(structure_type)) or {})
    if not model.get("extent"):
        return None
    scale = model.get("scale", 1.0)
    return scale * model["extent"]["reach"], max(0.0, scale * model["extent"]["height"])


# The model takes STAND_FIT of the screen's height at most (room for its effects, which reach past
# its mesh); the camera comes STAND_CLOSER times closer than the capture's at most.
STAND_FIT, STAND_CLOSER = 0.7, 4.0


def stand_distance(extent: tuple[float, float] | None, manifest: dict) -> float:
    """The camera distance (straight down) that keeps a model within STAND_FIT of the screen round
    its spot: through the capture's lens (pxPerCell at its distance) a point r cells out and z up
    shows r·f/(d−z) pixels from the middle, f = pxPerCell·distance."""
    far = manifest["distance"]
    if extent is None:
        return far
    reach, height = extent
    lens = manifest["pxPerCell"] * far
    half = STAND_FIT * min(manifest["screen"]["w"], manifest["screen"]["h"]) / 2
    return max(far / STAND_CLOSER, height + reach * lens / half)


def stand_crop(extent: tuple[float, float] | None, manifest: dict, distance: float, centre: tuple[float, float], shape) -> tuple[int, int, int, int]:
    """The square of the screen a model can show in at `distance` round its spot (`centre`), with
    room past it for its effects (1 / STAND_FIT times as wide); the whole screen's height without
    an extent."""
    if extent is None:
        return circle_box(centre, min(shape[:2]) / 2, shape)
    reach, height = extent
    lens = manifest["pxPerCell"] * manifest["distance"]
    return circle_box(centre, reach * lens / max(distance - height, 1e-3) / STAND_FIT, shape)


def thinned(frames: list, most: int) -> tuple[list, int]:
    """At most `most` of a film's frames, evenly spaced, and how many apart they are."""
    every = max(1, math.ceil(len(frames) / most))
    return frames[::every], every


def standing_films(session, manifest: dict, folder: Path, send) -> dict:
    """Each type's standing film (see the note above), by type: its strip, its step and how many
    frames, how long it ran, and the structure filmed."""
    structures = (manifest.get("elements") or {}).get("structures", [])
    chosen: dict[str, dict] = {}
    for u in structures:
        chosen.setdefault(u["type"], u)
    screen, ppc = manifest["screen"], manifest["pxPerCell"]
    report = (manifest.get("elements") or {}).get("report") or {}
    films: dict[str, dict] = {}
    shown: dict | None = None
    send(tile_command(manifest["tiles"][0]))
    send("el fadeall 0")
    send("el env off", timeout=10.0)
    with ui.bar(len(chosen), "standing filmed") as advance:
        for kind, u in sorted(chosen.items()):
            def film() -> None:
                nonlocal shown
                fading = ([(shown, 0)] if shown is not None and shown is not u else []) + [(u, 1)]
                send(f"el at {u['x']:.2f} {u['y']:.2f} " + " ".join(f"{s['x']:g} {s['y']:g} {v}" for s, v in fading))
                shown = u
                # In closer (the capture's view straight down, nearer), as far as its model still fits.
                extent = stand_extent(u["type"], report)
                distance = stand_distance(extent, manifest)
                closer = manifest["distance"] / distance
                send(f"el wide {u['x']:.2f} {u['y']:.2f} {distance:g}", timeout=6.0)
                camera = session.camera({"x": u["x"], "y": u["y"]})
                crop = None
                seconds = stand_seconds(u["type"], report)
                send(f"el restart {u['x']:g} {u['y']:g} {(report.get('standModels') or {}).get(u['type'], '')}".rstrip())
                started, frames = time.time(), []
                while len(frames) * FILM_STEP < seconds:
                    settle(max(0.0, started + len(frames) * FILM_STEP - time.time()))
                    frame = session.grab(dark_ok=True)
                    if frame is None:
                        continue
                    crop = crop or stand_crop(extent, manifest, distance, screen_point(u, camera, screen, ppc * closer), frame.shape)
                    v0, v1, u0, u1 = crop
                    frames.append(frame[v0:v1, u0:u1].copy())
                send(f"el hold {u['x']:g} {u['y']:g}")
                kept, every = thinned(frames, STAND_FRAMES)
                films[kind] = {"id": u["id"], "model": (report.get("standModels") or {}).get(u["type"]), "closer": round(closer, 3),
                               "extent": extent, "film": _film_strip(folder, f"stand-{u['type']}", kept), "columns": film_columns(len(kept)),
                               "step": FILM_STEP * every, "frames": len(kept), "seconds": seconds}
            step(film, f"filming {kind} standing")
            advance()
    log(f"calibration: {len(films)} types filmed standing from their restart: "
        + ", ".join(f"{k} {v['seconds']:g} s" for k, v in sorted(films.items())))
    return films


# The stacking probe: copies of a fort and of the tower nearest it made on the fort's cell, sunk below
# the ground (the fort's by the HrsSink site operation, the tower's by "SetHeight": which works?), the
# far plane put just below the ground so what is sunk is cut out, then each brought up in turn and
# shot. Does the far plane cut the sunk ones out, does each come up alone, and does a fire lit up
# there survive being sunk and brought up again (or being let run while sunk)?
SINK = 30.0  # specifics.SINK_DEPTH
STACK_REACH = 12.0  # cells round the cell each shot is cut to
SET_HEIGHT_DOWN, SET_HEIGHT_UP = f"SetHeight -{SINK:g}", "SetHeight 0"
SINK_ON, SINK_OFF = "HostSiteOpsSet ::Host HrsSink", "HostSiteOpsSet ::Host"


def stacking_probe(session, manifest: dict, folder: Path, send) -> dict:
    """The copies sunk and brought up one at a time under one camera (see the note above): each
    step's shot and how much of it is drawn."""
    structures = (manifest.get("elements") or {}).get("structures", [])
    fort = next((u for u in structures if u["type"] == FORT), None)
    towers = [u for u in structures if u["type"].startswith("TownCannonTower")]
    if fort is None or not towers:
        warn("  the stacking probe needs a fort and a tower")
        return {"steps": []}
    tower = min(towers, key=lambda u: math.dist((u["x"], u["y"]), (fort["x"], fort["y"])))
    screen, ppc = manifest["screen"], manifest["pxPerCell"]
    x, y = fort["x"], fort["y"]
    ground = manifest["distance"]
    steps: list[dict] = []

    def msg(n: int, message: str) -> None:
        send(f"el copy {n} msg {message.replace(' ', '~')}")

    def shot(name: str) -> None:
        settle(0.5)
        frame = session.grab(dark_ok=True)
        if frame is None:
            steps.append({"step": name, "drawn": None, "picture": None})
            return
        centre = screen_point({"x": x, "y": y}, session.camera({"x": x, "y": y}), screen, ppc)
        box = circle_box(centre, STACK_REACH * ppc, frame.shape)
        steps.append({"step": name, "drawn": appearance(frame, centre, STACK_REACH * ppc)["drawn"],
                      "picture": _picture(folder, f"stacking-{len(steps)}", frame, box)})

    send(tile_command(manifest["tiles"][0]))
    send("el fadeall 0")
    send("el env off", timeout=10.0)
    send(f"el at {x:.2f} {y:.2f}")
    send(f"el copy 0 make {x:g} {y:g} {x:g} {y:g}")
    send(f"el copy 1 make {tower['x']:g} {tower['y']:g} {x:g} {y:g}")
    settle(2.0)
    msg(0, "AnimSetPausedAll")
    msg(1, "AnimSetPausedAll")
    shot("the fort and the tower copied on one cell and paused")
    msg(0, SINK_ON)
    msg(1, SET_HEIGHT_DOWN)
    shot(f"the fort sunk {SINK:g} by the site operation, the tower by SetHeight")
    send(f"el farclip {ground + 3:g}")
    shot("the far plane just below the ground")
    msg(0, SINK_OFF)
    shot("the fort brought up")
    msg(0, "AnimSetPausedAll 0")
    settle(2.0)
    msg(0, "AnimSetPausedAll")
    shot("the fort lit up there (let run 2 s)")
    msg(0, SINK_ON)
    msg(1, SET_HEIGHT_UP)
    shot("the fort sunk, the tower brought up")
    msg(1, SET_HEIGHT_DOWN)
    msg(0, SINK_OFF)
    shot("the tower sunk, the fort brought up again (its fire kept?)")
    msg(0, SINK_ON)
    send("pause 0")
    settle(3.0)
    send("pause")
    msg(0, SINK_OFF)
    shot("the fort sunk, everything let run 3 s, the fort brought up (lit while cut out?)")
    for s in steps:
        log(f"  stacking: {s['step']}: drawn {s['drawn']}")
    return {"fort": fort["id"], "tower": tower["id"], "steps": steps}


def _picture(folder: Path, name: str, frame: np.ndarray, box: tuple[int, int, int, int]) -> str | None:
    """A crop of a frame saved for the review (PNG); its file name."""
    v0, v1, u0, u1 = box
    if not (v0 < v1 and u0 < u1):
        return None
    Image.fromarray(np.ascontiguousarray(frame[v0:v1, u0:u1, :3])).save(folder / f"{name}.png", compress_level=1)
    return f"{name}.png"


def _moving_picture(folder: Path, name: str, a: np.ndarray, b: np.ndarray, box: tuple[int, int, int, int]) -> str | None:
    """Two looks at the same box as one picture that flips between them (animated WebP); its file
    name."""
    v0, v1, u0, u1 = box
    if not (v0 < v1 and u0 < u1):
        return None
    frames = [Image.fromarray(np.ascontiguousarray(f[v0:v1, u0:u1, :3])) for f in (a, b)]
    frames[0].save(folder / f"{name}.webp", save_all=True, append_images=frames[1:], duration=500, loop=0, quality=85)
    return f"{name}.webp"


def film_columns(frames: int) -> int:
    """How many of a film's frames its picture holds to a row."""
    return max(1, min(FILM_COLUMNS, frames))


def film_box(centre: tuple[float, float], shape: tuple) -> tuple[int, int, int, int]:
    """The square round a structure's place on the screen that its debris is filmed in: as big as
    the screen's height allows (clipped at the screen's edges)."""
    return circle_box(centre, min(shape[:2]) / 2, shape)


def film_scale(centre: tuple[float, float], shape: tuple, crop: tuple, ppc: float) -> dict:
    """Where a film's structure is in its saved frames ("centre", pixels) and a saved pixel's size
    ("cellsPerPixel"), for a film cut by film_box round `centre` on a screen of `shape`, its frames
    `crop` (h, w) as shot (_film_strip saves them FILM_MOST square at most), `ppc` pixels a cell
    on the screen."""
    f0, _, g0, _ = film_box(centre, shape)
    size = min(FILM_MOST, max(crop[:2]))
    shrink = size / max(crop[:2])
    return {"centre": [round((centre[0] - g0) * shrink, 1), round((centre[1] - f0) * shrink, 1)], "cellsPerPixel": round(1 / (ppc * shrink), 6)}


def _film_strip(folder: Path, name: str, frames: list[np.ndarray]) -> str | None:
    """A film's frames in rows of film_columns, each square at the size shot (FILM_MOST at most;
    PNG); its file name."""
    if not frames:
        return None
    size = min(FILM_MOST, max(frames[0].shape[:2]))
    columns = film_columns(len(frames))
    strip = Image.new("RGB", (size * columns, size * math.ceil(len(frames) / columns)))
    for n, frame in enumerate(frames):
        picture = Image.fromarray(np.ascontiguousarray(frame[..., :3]))
        strip.paste(picture.resize((size, size), Image.LANCZOS) if picture.size != (size, size) else picture,
                    (n % columns * size, n // columns * size))
    strip.save(folder / f"{name}.png", compress_level=6)
    return f"{name}.png"


def calibrate(session, manifest: dict, base: Path, stages: list[str] = ("motion", "structures", "rubble-at-home"),
              previous: dict | None = None) -> dict:
    """The named stages (see the module's note; specifics.CALIBRATION_STAGES), and what they
    measured. `previous`: the map's calibration so far, whose copies' rubble and falls the rubble at
    home is compared with when the structures stage isn't run with it."""
    previous = previous or {}
    found = manifest.get("elements") or {}
    structures = found.get("structures", [])
    doodads = found.get("doodads", [])
    candidates: dict = found.get("copyCandidates") or {}
    bounds, screen, ppc = manifest["cameraBounds"], manifest["screen"], manifest["pxPerCell"]
    folder = base / "calibration"
    folder.mkdir(exist_ok=True)
    motion: list[dict] = []
    measured: dict = {}
    spots: dict = {}
    falls: dict[str, dict] = {}
    tries: list[dict] = []
    used: list[tuple[float, float, float]] = []
    failed: list[tuple[float, float]] = []
    # Kept as each command is answered, so a structure done again after a lost focus starts from
    # what is really there: the structure left faded in, our player's side, a copy standing or
    # fallen on its spot.
    state = {"shown": None, "team": 1, "copy": False, "failures": 0}

    def send(command: str, timeout: float = 3.0) -> bool:
        if session.command(command, timeout=timeout) is not None:
            state["failures"] = 0
            return True
        state["failures"] += 1
        if state["failures"] >= FAILURES:
            raise Recoverable(f"the map stopped answering during the calibration (\"{command}\")")
        return False

    def look_at_tile(tile: dict) -> None:
        """A tile as the tiles see it, twice; each patch that moved in between noted."""
        send(tile_command(tile))
        camera = session.camera({"x": tile["x"], "y": tile["y"]})
        a = session.grab(dark_ok=True)
        settle(MOTION_GAP)
        b = session.grab(dark_ok=True)
        if a is None or b is None:
            return
        for k, (box, middle, pixels) in enumerate(moving_patches(channel_diff(a, b) > CHANGE)):
            x, y = cell_at(middle, camera, screen, ppc)
            margin = 2 * MOTION_BLOCK
            wide = (max(0, box[0] - margin), box[1] + margin, max(0, box[2] - margin), box[3] + margin)
            motion.append({"x": round(x, 1), "y": round(y, 1), "pixels": pixels, "tile": tile["index"],
                           "what": what_is_at((x, y), structures, doodads),
                           "picture": _moving_picture(folder, f"moving-{tile['index']}-{k}", a, b, wide)})

    def look(spot: dict, radius: float, name: str) -> tuple[np.ndarray | None, tuple[float, float], tuple[int, int, int, int]]:
        """The view once the circle round the spot holds still, the spot's place on the screen and
        the circle's box; its picture kept."""
        camera = session.camera({"x": spot["x"], "y": spot["y"]})
        first = session.grab(dark_ok=True)
        centre = screen_point(spot, camera, screen, ppc)
        box = circle_box(centre, radius * ppc, first.shape if first is not None else (0, 0))
        frame = still_frame(session, [box], first) if first is not None else None
        if frame is not None:
            _picture(folder, name, frame, box)
        return frame, centre, box

    def changed(reference: np.ndarray, frame: np.ndarray, box, moving: np.ndarray) -> bool:
        v0, v1, u0, u1 = box
        diff = channel_diff(reference[v0:v1, u0:u1], frame[v0:v1, u0:u1]) > CHANGE
        return int((diff & ~moving).sum()) > SWITCH_PIXELS

    def side_time(team: int, reference: np.ndarray, box, moving: np.ndarray) -> tuple[float | None, np.ndarray]:
        """Our player put on the team; the seconds until the circle changes (what moves by itself
        left out; None: it didn't), and the frame once it holds still again."""
        if not send(f"side {team}"):
            return None, reference
        state["team"] = team
        started = time.time()
        while time.time() < started + SIDE_CAP:
            frame = session.grab(dark_ok=True)
            if frame is not None and changed(reference, frame, box, moving):
                seconds = round(time.time() - started, 2)
                held = still_frame(session, [box], frame)
                return seconds, held if held is not None else frame
            settle(0.02)
        return None, reference

    def fall(centre: tuple[float, float], shape: tuple, radius: float, film: bool) -> tuple[float | None, np.ndarray | None, list]:
        """The copy brought down; the seconds its fall took to settle (settle_time; None: not within
        FALL_CAP, watched in its rubble's circle), the last frame, and (filmed: the whole
        FILM_SECONDS) a frame every FILM_STEP, of as much round it as the screen holds (film_box:
        its debris measured in them, rubble_radii)."""
        v0, v1, u0, u1 = circle_box(centre, radius, shape)
        f0, f1, g0, g1 = film_box(centre, shape)
        send("el copy 0 kill")
        started, previous, samples, frames, settled = time.time(), session.grab(dark_ok=True), [], [], None
        while time.time() < started + (FILM_SECONDS if film else FALL_CAP):
            settle(FALL_GAP)
            frame = session.grab(dark_ok=True)
            if frame is None:
                continue
            t = time.time() - started
            if film and len(frames) * FILM_STEP <= t < FILM_SECONDS:
                frames.append(frame[f0:f1, g0:g1].copy())
            if previous is not None:
                moved = channel_diff(previous[v0:v1, u0:u1], frame[v0:v1, u0:u1]) > CHANGE
                samples.append((t, float(moved.mean()) if moved.size else 0.0))
            previous = frame
            settled = settled if settled is not None else settle_time(samples)
            if settled is not None and not film:
                break
        return settled, previous, frames

    def try_spot(u: dict, spot: tuple[float, float], own: dict) -> dict:
        """A copy of the structure on the spot, compared with it standing and as rubble: what was
        measured and why it failed (None: it passed; its fall's time kept, and the first of its
        kind and team filmed). The copy is gone after (its rubble cleared)."""
        x, y = spot
        send(f"el copy 0 make {u['x']:g} {u['y']:g} {x:g} {y:g}")
        state["copy"] = True
        send(f"el at {x:.2f} {y:.2f}")
        settle(COPY_SETTLE)
        name = f"structure-{u['id']}-at-{x:g}-{y:g}"
        frame, centre, _ = look({"x": x, "y": y}, u["radius"], name)
        result = {"id": u["id"], "type": u["type"], "owner": u.get("owner"), "x": x, "y": y, "standing": f"{name}.png", "own": own}
        result["copy"] = appearance(frame, centre, u["radius"] * ppc) if frame is not None else None
        why = "no frame" if frame is None else matches(own, result["copy"])
        if why is not None:
            send("el copy 0 remove")
            state["copy"] = False
            return {**result, "failed": why}
        kind = f"{u['type']}|{u.get('owner')}"
        film = kind not in falls or not falls[kind].get("film")
        wide = None
        if film:
            # What the render's camera, far back, shows of the copy (the first calibration's films
            # from there came out empty): a look kept, the capture camera put back for the film.
            send(f"el wide {x:.2f} {y:.2f} {manifest['distance'] * VIEW_BACK:g}", timeout=6.0)
            view = session.grab(dark_ok=True)
            if view is not None:
                wide = f"wide-{u['type']}-{u.get('owner')}.png"
                Image.fromarray(np.ascontiguousarray(view[::2, ::2, :3])).save(folder / wide, compress_level=6)
            send(f"el at {x:.2f} {y:.2f}")
        seconds, rubble, frames = fall(centre, frame.shape, rubble_radius(u) * ppc, film)
        if rubble is not None:
            _picture(folder, f"{name}-rubble", rubble, circle_box(centre, rubble_radius(u) * ppc, rubble.shape))
            result["rubble"] = f"{name}-rubble.png"
        send("el clear")
        state["copy"] = False
        if rubble is not None:
            result["rubbleLook"] = appearance(rubble, centre, rubble_radius(u) * ppc)
        if rubble is not None and result["rubbleLook"]["dark"] > RUBBLE_DARK_MOST:
            return {**result, "failed": "black rubble"}
        if rubble is not None and result["rubbleLook"]["drawn"] < RUBBLE_DRAWN_LEAST:
            return {**result, "failed": "no rubble"}
        entry = falls.setdefault(kind, {"settles": []})
        if seconds is not None:
            entry["settles"].append(seconds)
        if film and frames:
            entry.update(film_scale(centre, frame.shape, frames[0].shape, ppc))
            entry["film"] = _film_strip(folder, f"fall-{u['type']}-{u.get('owner')}", frames)
            entry["step"] = FILM_STEP
            entry["frames"] = len(frames)
            entry["columns"] = film_columns(len(frames))
            entry["wide"] = wide
        return {**result, "failed": None}

    def one(u: dict) -> None:
        """A structure's look, motion and side switch measured, then its copy's spot found."""
        key = str(u["id"])
        if state["copy"]:  # a step done again: the last try's copy, standing or fallen, gone first
            send("el copy 0 remove")
            send("el clear")
            state["copy"] = False
        if state["team"] != 1 and send("side 1"):
            state["team"] = 1
        fading = [(state["shown"], 0)] if state["shown"] is not None and state["shown"] is not u else []
        triples = " ".join(f"{s['x']:g} {s['y']:g} {value}" for s, value in fading + [(u, 1)])
        send(f"el at {u['x']:.2f} {u['y']:.2f} {triples}")
        state["shown"] = u
        frame, centre, box = look(u, u["radius"], f"structure-{key}")
        if frame is None:
            warn(f"  {u['type']} {key}: no frame")
            return
        own = appearance(frame, centre, u["radius"] * ppc)
        # What moves on it by itself while paused (left out of the side switch's comparison).
        settle(MOTION_GAP)
        again = session.grab(dark_ok=True)
        v0, v1, u0, u1 = box
        moving = (channel_diff(frame[v0:v1, u0:u1], again[v0:v1, u0:u1]) > CHANGE if again is not None
                  else np.zeros((v1 - v0, u1 - u0), bool))
        moves = round(float(moving.mean()), 4) if moving.size else 0.0
        entry = {"type": u["type"], "owner": u.get("owner"), "standing": f"structure-{key}.png", "moves": moves}
        if moves > MOVING_SHARE:
            entry["moving"] = _moving_picture(folder, f"structure-{key}-moving", frame, again, box)
        # Whether it changes with the side: its model's data says (elements.standing_team_colour), or
        # it is measured (switched to Chaos and back, watched).
        known = standing_team_colour(u, found.get("report") or {})
        if known is not None:
            measured[key] = {**entry, "changes": known, "toChaos": None, "toOrder": None, "fromData": True}
        else:
            to_chaos, chaos_frame = side_time(2, frame, box, ndimage.binary_dilation(moving, iterations=2))
            to_order, _ = side_time(1, chaos_frame, box, ndimage.binary_dilation(moving, iterations=2))
            measured[key] = {**entry, "changes": to_chaos is not None and to_order is not None, "toChaos": to_chaos, "toOrder": to_order}
        if key not in candidates:
            return
        send(f"el fades {u['x']:g} {u['y']:g} 0")
        state["shown"] = None
        rubble, tried = rubble_radius(u), 0
        for x, y in candidates[key]:
            if tried >= TRIES:
                break
            if not usable((x, y), bounds, used, failed, rubble):
                continue
            tried += 1
            result = try_spot(u, (x, y), own)
            tries.append(result)
            if result["failed"] is None:
                spots[key] = {"x": x, "y": y}
                used.append((x, y, rubble))
                return
            failed.append((x, y))
        warn(f"  {u['type']} {key}: no spot passed ({tried} tried); it falls itself in the render")

    def rubble_at_home(u: dict) -> None:
        """A structure brought down on its own cell, paused at its kind's fall wait, its rubble shot
        with the scene hidden and then shown: how much hiding the scene changes rubble (the same
        pixels compared), and how its copy's rubble compared with it."""
        kind = f"{u['type']}|{u.get('owner')}"
        wait = home_wait(u, falls)
        send(f"el at {u['x']:.2f} {u['y']:.2f} {u['x']:g} {u['y']:g} 1")
        camera = session.camera({"x": u["x"], "y": u["y"]})
        send(f"el kill {u['x']:g} {u['y']:g} {wait:g}", timeout=wait + 6.0)
        hidden = session.grab(dark_ok=True)
        send("el env on", timeout=10.0)
        settle(SHOW_SETTLE)
        shown = session.grab(dark_ok=True)
        send("el env off", timeout=10.0)
        send("el clear")
        if hidden is None or shown is None:
            return
        centre = screen_point(u, camera, screen, ppc)
        radius = rubble_radius(u) * ppc
        box = circle_box(centre, radius, hidden.shape)
        name = f"home-{u['type']}-{u.get('owner')}"
        # Hidden: as much round it as the screen holds, at the size shot (its debris measured in it,
        # rubble_radii: the structure's place in it, "centre", and a pixel's cells, "cellsPerPixel").
        wide = film_box(centre, hidden.shape)
        home = {"id": u["id"], "wait": wait, "hidden": _picture(folder, f"{name}-hidden", hidden, wide),
                "shown": _picture(folder, f"{name}-shown", shown, box), **compare_shown(hidden, shown, centre, radius),
                "centre": [round(centre[0] - wide[2], 1), round(centre[1] - wide[0], 1)], "cellsPerPixel": round(1 / ppc, 6)}
        # (An earlier calibration's tries didn't record the team: the structure's.)
        owners = {s["id"]: s.get("owner") for s in structures}
        copy = next((t for t in tries if f"{t['type']}|{t.get('owner', owners.get(t['id']))}" == kind and t["failed"] is None and t.get("rubble")), None)
        if copy:
            home["copy"] = copy["rubble"]
            if copy.get("rubbleLook") and home["look"]["drawn"]:
                home["copyRatio"] = round(statistics.fmean(copy["rubbleLook"]["mean"]) / max(statistics.fmean(home["look"]["mean"]), 1.0), 3)
        homes[kind] = home

    def hide_scene() -> None:
        send(tile_command(manifest["tiles"][0]))  # the scene and the capture camera as the tiles have them
        send("el fadeall 0")
        send("el env off", timeout=10.0)

    result: dict = {"report": found.get("report") or {}}
    if "motion" in stages:
        with stage("calibration (what moves while paused)"):
            with ui.bar(len(manifest["tiles"]), "tiles looked at") as advance:
                for tile in manifest["tiles"]:
                    step(lambda: look_at_tile(tile), f"looking at tile {tile['index'] + 1}")
                    advance()
        motion = same_patches(motion)
        kinds = Counter(p["what"]["kind"] for p in motion)
        log(f"calibration: {len(motion)} patches move while paused ({', '.join(f'{n} {k}' for k, n in kinds.most_common()) or 'none'})")
        result["motion"] = motion
    if "structures" in stages:
        order = calibration_order(structures, candidates)
        with stage("calibration (structures)"):
            hide_scene()
            with ui.bar(len(order), "structures calibrated") as advance:
                for u in order:
                    step(lambda: one(u), f"calibrating {u['type']} {u['id']}")
                    advance()
        for entry in falls.values():
            entry["settle"] = round(statistics.median(entry["settles"]), 2) if entry["settles"] else None
        changing = [s for s in measured.values() if s["changes"]]
        slowest = max((max(s["toChaos"], s["toOrder"]) for s in changing if s["toChaos"] is not None and s["toOrder"] is not None), default=0.0)
        reasons = Counter(t["failed"] for t in tries if t["failed"])
        log(f"calibration: {len(changing)} of {len(measured)} structures change with the side (the slowest switch {slowest:.2f} s), "
            f"{sum(s['moves'] > MOVING_SHARE for s in measured.values())} move while paused; "
            f"{len(spots)} of {len(candidates)} copied structures have a checked spot, {sum(reasons.values())} spots failed"
            + (f" ({', '.join(f'{n} {why}' for why, n in reasons.most_common())})" if reasons else ""))
        if falls:
            log("  falls: " + ", ".join(f"{k} settled {v['settle']:g} s" + (" (filmed)" if v.get("film") else "")
                                        for k, v in sorted(falls.items()) if v["settle"] is not None))
        result.update(structures=measured, copySpots=spots, falls=falls, tries=tries)
    if "standing" in stages:
        result["standing"] = standing_films(session, manifest, folder, send)
    if "stacking" in stages:
        if len(stages) > 1:
            raise ValueError("the stacking probe runs on its own: it leaves copies and the camera's clip planes changed")
        result["stacking"] = stacking_probe(session, manifest, folder, send)
    if "pausing" in stages:
        if len(stages) > 1:
            raise ValueError("the pausing probe runs on its own: its ways of pausing can't be undone")
        result["pausing"] = pausing_probe(session, manifest, folder, previous.get("motion") or [], send)
    if "rubble-at-home" in stages:
        # Last (it brings the map's structures down): one structure of each kind on each team, at
        # home; compared with the copies' rubble of this run, or of the calibration so far.
        if "structures" not in stages:
            tries[:] = previous.get("tries") or []
            falls.update(previous.get("falls") or {})
        homes: dict[str, dict] = {}
        at_home = home_order(structures)
        with stage("calibration (rubble at home)"):
            if "structures" not in stages:
                hide_scene()
            with ui.bar(len(at_home), "rubble shot at home") as advance:
                for u in at_home:
                    step(lambda: rubble_at_home(u), f"bringing down {u['type']} {u['id']} at home")
                    advance()
        darker = [k for k, h in homes.items() if h["ratio"] is not None and h["ratio"] < HIDDEN_DARKER]
        copies_darker = [k for k, h in homes.items() if h.get("copyRatio") is not None and h["copyRatio"] < HIDDEN_DARKER]
        log(f"calibration: rubble at home for {len(homes)} kinds; hiding the scene darkens {len(darker)}"
            + (f" ({', '.join(f'{k} {homes[k]["ratio"]:.0%}' for k in darker)})" if darker else "")
            + f"; copies' rubble darker than at home for {len(copies_darker)}"
            + (f" ({', '.join(f'{k} {homes[k]["copyRatio"]:.0%}' for k in copies_darker)})" if copies_darker else ""))
        result["rubbleAtHome"] = homes
    return result
