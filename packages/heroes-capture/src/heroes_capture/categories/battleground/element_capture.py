"""The elements capture (ELEMENTS-PLAN.md, The run), before a render's tiles when it is prepared with
--structures elements. The whole scene is hidden once (every structure and unit, and the terrain,
doodads and water switched off: "el env off") over the white sky, and the camera goes to each
element in turn, straight above it in the middle of the screen:

1. standing: every structure faded out by its opacity ("el fadeall 0"), not hidden: it keeps its
   look as the map paused it once ready, its effects built up while the map ran and held still
   (the prepared map's models freeze their particles and ribbons with their animations; and each
   structure's animations are started over once the camera is over it and paused at its kind's
   standing time, "el restart" and "el hold": the same moment every run, its particles lit, as
   they only run near the camera); hidden
   and shown again, a structure restarts its animations (a level 3 tower's into its birth) and
   gives off no particles until it plays. Then each faded in, shot alone over white and over
   black, from our player's side (Order: the observer's colours) and from Chaos's (switched in
   between, "side <team>": the Chaos player's colours, for the team-colour mask), and faded
   out again. The camps spawned together (those near enough to share a shot in
   waves of their own), frozen, each shot, then removed: the first camp wave spawned as the scene
   is hidden, born while the rubble is prepared, and shot after the structures. The rubble
   prepared first: a copy of every structure that
   has a spare spot (elements.copy_spots, planned when the map is prepared: out of every other
   circle, under the structure's own lighting) made there first ("el copy <n> make"), then
   brought down a group at a time with the camera far back over it (a fall gives off its smoke and
   dust only near the camera: VIEW_BACK), each set of the same fall_wait at once ("el copykill"),
   the slowest to settle first, so that each has fallen for just its own fall_wait (its debris
   landed, before it fades) when everything is paused;
2. rubble: each copy's remains shot on its spot, recorded as if from its structure's cell (the
   camera moved by the difference: the view straight down is the same anywhere), so the stitch
   places it there; no waiting. The cores, the keeps (elements.COPY_EXCLUDED), and any structure
   the map had no room to copy, then brought down in waves (elements.rubble_waves: none in a wave near
   enough another for their rubble to share a shot; the cores last, after hidden replacements
   take their places), each wave left to settle, each one's rubble shot alone, then cleared away
   before the next wave;
3. the scene shown again, the remains cleared, the cores' holes shown, and everything let play
   again for a moment (a doodad shown again while paused gives off no particles): the tiles that
   follow are the bare terrain.

The shots (raw, like the tiles') go to elements/, and elements.json records each element's state:
its shot, the tile it is placed by, the camera's position and its circle, for the stitch to cut
it out (matting.cut_out) and place it. A fresh launch after a lost match skips what is recorded,
then brings the structures down again (they all stand again in a new match).
"""

import math
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import numpy as np

from heroes_capture.capture.sky import LIGHT_LEVEL, LIGHT_SKY
from heroes_capture.capture.shots import SWITCH_CAP, recoloured_frame, shot_over_black, still_frame, tile_command
from heroes_capture.categories.battleground.elements import (
    goes_with_a_fort,
    fall_wait,
    rubble_radius,
    rubble_waves,
    source_tile,
    stand_wait,
    view_groups,
    view_middle,
)
from heroes_capture.common import ui
from heroes_capture.common.frames import save_frame
from heroes_capture.common.geometry import circle_box, screen_point

from heroes_capture.common.game_control import Recoverable, settle, step
from heroes_capture.common.jsonio import read_json, write_json
from heroes_capture.common.runlog import log, stage, warn

KILL_WAIT = 5.0  # the clearing's: everything left standing brought down, its rubble not shot


# Real seconds for a camp's defenders to spawn (a quarter of a second apart) and finish being born.
CAMP_WAIT = 5.0
# Real seconds the scene shown again after the elements is let play before it is paused again, for
# the doodads' particles to build up: shown while paused a doodad gives off none (the play-then-
# freeze probe on Battlefield of Eternity: a gate's effects there by half a second, a Hell gate's
# smoke still thickening at 2).
PLAY_WAIT = 3.0
# Cells within which another structure is a neighbour whose overlap with a structure is measured
# (stand): a wall's end on a tower, a gate between its towers.
NEIGHBOUR_REACH = 10.0
# Structures faded in one command at most ("<x> <y> <opacity>" each: the script reads 17 words).
FADES_A_COMMAND = 4
# Seconds from a structure shown (or a camp frozen) to its shot: a frame or two to be drawn.
SHOW_SETTLE = 0.1
# Seconds a side switch is watched for on a structure the calibration found nothing changes on.
UNCHANGING_CAP = 0.5
# Remains repainted (SetTeamColor, no blend time) change at once: watched this long at most.
PAINT_CAP = 0.5
# Remains whose circle has less than this share of pixels more than EMPTY_DIFF off the light sky
# are nothing (a copy that fell to nothing; the least real rubble covers about 0.005).
EMPTY_SHARE, EMPTY_DIFF = 0.001, 30
# A fall gives off its particles (smoke, dust, sparks, a core's lava) only near the camera (the
# in-view probe on Battlefield of Eternity: with the camera far away, or far back over the whole
# map, a copy's rubble had only its chunks; at the edge of the capture camera's view a wall's had
# none; with the camera VIEW_BACK times the capture's distance back, centred, every kind had them
# all). So everything brought down at once is brought down with the camera that far back over
# it, all of it within VIEW_SHARE of that view either way of its middle (elements.view_groups).
VIEW_BACK, VIEW_SHARE = 2.5, 0.5
# Words a note may have ("note <text>" and the command's number: the script reads 17 words).
NOTE_WORDS = 15
# Commands in a row the map doesn't answer before the match counts as lost.
FAILURES = 3
# Copies the map script keeps (hrsCap_copies); any more are brought down in waves.
COPY_LIMIT = 128
# The types of structure being copied (made, or a group brought down) while the command is
# unanswered, in the environment a recovery's fresh process inherits: a copy that crashed the game
# leaves its types here, and they are brought down in waves from then on (COPY_CRASHED, so far).
COPY_TRYING, COPY_CRASHED = "HRS_COPY_TRYING", "HRS_COPY_CRASHED"


def element_key(kind: str, element: dict, state: str) -> str:
    """An element's shots' name: structure-<placed unit id>-<state>, camp-<n>-<state>."""
    return f"{kind}-{element['id'] if kind == 'structure' else element['camp']}-{state}"


def capture_elements(session, manifest: dict, base: Path) -> None:
    """The passes (see the module's note). `base`: the run's folder (the tiles' folder's parent);
    The first tile's command puts the whole scene in place (what ran before, the sky layers'
    shots, leaves the camera as they need it)."""
    found = manifest.get("elements") or {}
    structures, camps = found.get("structures", []), found.get("camps", [])
    folder = base / "elements"
    folder.mkdir(exist_ok=True)
    record_path = folder / "elements.json"
    records: dict = read_json(record_path, {})
    tiles = manifest["tiles"]
    failures = {"count": 0}

    def wait_of(u: dict) -> float:
        return fall_wait(u, manifest.get("fallWaits"))
    # The frames saved while the next are shot, as the tiles' are; each on disk before a record
    # lists it (a resumed run skips what is recorded).
    saver = ThreadPoolExecutor(max_workers=2)
    saving: list[Future] = []

    def save(stem: str, frame: np.ndarray) -> None:
        saving.append(saver.submit(save_frame, folder / stem, frame))

    def saved() -> None:
        for future in saving:
            future.result()
        saving.clear()

    def send(command: str, timeout: float = 3.0) -> bool:
        if session.command(command, timeout=timeout) is not None:
            failures["count"] = 0
            return True
        failures["count"] += 1
        if failures["count"] >= FAILURES:
            raise Recoverable(f"the map stopped answering during the elements capture (\"{command}\")", resume_at=0)
        return False

    def note(text: str) -> None:
        """A line at the top middle of the screen saying what the run waits on while nothing is
        shot (the script's "note"); the next "el at" or "tile", which every shot starts with,
        takes it down."""
        if len(text.split()) > NOTE_WORDS or ";" in text:
            raise ValueError(f"a note has at most {NOTE_WORDS} words and no ';': {text}")
        send(f"note {text}")

    def triples(fading: list[tuple[dict, int]]) -> str:
        """Structures to fade, as the script's "<x> <y> <opacity>" words."""
        return " ".join(f"{u['x']:g} {u['y']:g} {value}" for u, value in fading)

    def go(element: dict, fading: list[tuple[dict, int]] = ()) -> dict:
        """The camera straight above the element ("el at": the lighting refitted there, as the tiles
        do, but none of the rest of their scene), the white sky back; answered once the camera has
        stopped. `fading`: structures faded first, in the same command (at most FADES_A_COMMAND).
        Returns the grid tile it belongs to, the one the stitch places it by."""
        send(f"el at {element['x']:.2f} {element['y']:.2f}" + (f" {triples(fading)}" if fading else ""))
        return source_tile(element, tiles)

    def fade_all(fading: list[tuple[dict, int]]) -> None:
        """Structures faded, as few commands as the script takes ("el fades")."""
        for i in range(0, len(fading), FADES_A_COMMAND):
            send(f"el fades {triples(fading[i:i + FADES_A_COMMAND])}")

    def pair(stem: str) -> dict | None:
        """The view over white and over black, saved as <stem> and <stem>-black; the camera's
        position. None without a frame. The sky stays black: the next move puts the white back."""
        settle(SHOW_SETTLE)
        white = session.grab(dark_ok=True)
        camera = session.camera()
        black = shot_over_black(session, white)
        if white is None or black is None:
            warn(f"  {stem}: no frame")
            return None
        save(stem, white)
        save(f"{stem}-black", black)
        return camera

    # Our player's side: Order (1) as the match starts and between structures; Chaos (2) only
    # while a structure's Chaos shots are taken. Kept as each switch is answered, so a step done
    # again after a lost focus starts by switching back.
    side = {"team": 1, "unchanged": 0}

    def switch(team: int, element: dict, camera: dict, reference: np.ndarray | None) -> tuple[np.ndarray | None, bool]:
        """Our player put on the team ("side"), then the frame once the element's circle has changed
        from `reference` (shots.recoloured_frame), and whether it did. Not waited on for long when
        the calibration found nothing on it that changes (UNCHANGING_CAP)."""
        if not send(f"side {team}"):
            return None, False
        side["team"] = team
        shape = reference.shape if reference is not None else (0, 0)
        box = circle_box(screen_point(element, camera, manifest["screen"], manifest["pxPerCell"]),
                         element["radius"] * manifest["pxPerCell"], shape)
        return recoloured_frame(session, reference, [box], UNCHANGING_CAP if element.get("changesSide") is False else SWITCH_CAP)

    def back_to_order() -> None:
        """Our player back on Order, if a step was left on Chaos (a lost focus mid-structure)."""
        if side["team"] != 1 and send("side 1"):
            side["team"] = 1
            settle(SWITCH_CAP)

    def pair_both(stem: str, element: dict, attempt: int = 1) -> dict | None:
        """The view over white and over black from both sides: Order's saved as <stem> and
        <stem>-black (as pair), Chaos's as <stem>-chaos and <stem>-chaos-black. In the order that
        needs no extra sky swap: white from Order, switch, white from Chaos, black from Chaos,
        switch back, black from Order. The camera's position; None without Order's frames (Chaos's
        missing: left out of the record, and noted).

        Which side the game really draws is read from the screen, not assumed: a structure with
        team colour changes on one switch and back on the other. Changing on neither: it has none
        (a gate on some maps), unless the calibration saw it change ("changesSide"): then, as when
        it changes only on the switch back, the first shots were Chaos's (the game hadn't taken
        the last switch back when they were shot: Dragon Shire's forts came out in the other
        side's colours), so they are all taken again."""
        camera = session.camera()
        watched = camera or {"x": element["x"], "y": element["y"]}  # where the circle is watched for the switch
        # Faded in just now (and just after the last structure's switch back to Order): shot once
        # it has finished redrawing, its colours Order's side's.
        first = session.grab(dark_ok=True)
        shape = first.shape if first is not None else (0, 0)
        circle = circle_box(screen_point(element, watched, manifest["screen"], manifest["pxPerCell"]),
                            element["radius"] * manifest["pxPerCell"], shape)
        white = still_frame(session, [circle], first)
        chaos_white, to_chaos = switch(2, element, watched, white)
        chaos_black = shot_over_black(session, chaos_white)
        black, to_order = switch(1, element, watched, chaos_black)
        if white is None or black is None:
            warn(f"  {stem}: no frame")
            return None
        if not to_chaos and (to_order or element.get("changesSide")):
            if attempt < 3:
                log(f"  {stem}: " + ("shot from Chaos's side (the switch back hadn't been drawn)" if to_order
                                     else "the switch to Chaos wasn't seen, though the calibration saw it change") + "; shot again")
                return pair_both(stem, element, attempt + 1)
            warn(f"  {stem}: the side switch isn't drawn as sent; its shots may be from the wrong side")
        if not to_chaos:
            side["unchanged"] += 1
        save(stem, white)
        save(f"{stem}-black", black)
        if chaos_white is None or chaos_black is None:
            warn(f"  {stem}: no frame from Chaos's side")
        else:
            save(f"{stem}-chaos", chaos_white)
            save(f"{stem}-chaos-black", chaos_black)
        return {"camera": camera, "chaos": f"{stem}-chaos" if chaos_white is not None and chaos_black is not None else None}

    def record(kind: str, element: dict, state: str, tile: dict, camera: dict, **extra) -> None:
        key = element_key(kind, element, state)
        records[key] = {"kind": kind, "state": state, "element": element["id"] if kind == "structure" else element["camp"],
                        "x": element["x"], "y": element["y"], "radius": element["radius"], "tile": tile["index"],
                        "shot": key, "camera": camera or None, **extra}
        saved()
        write_json(record_path, records, indent=1)

    def look_over(x: float, y: float) -> None:
        """The camera VIEW_BACK times further back straight above a cell ("el wide"): what falls in
        the middle of its view gives off its particles."""
        send(f"el wide {x:.2f} {y:.2f} {manifest['distance'] * VIEW_BACK:g}", timeout=6.0)

    def grouped(items: list, at=lambda item: (item["x"], item["y"])) -> list[list]:
        """Items in groups to bring down under one camera (view_groups)."""
        screen, ppc = manifest["screen"], manifest["pxPerCell"]
        half_w = screen["w"] / ppc / 2 * VIEW_BACK * VIEW_SHARE
        half_h = screen["h"] / ppc / 2 * VIEW_BACK * VIEW_SHARE
        return view_groups(items, half_w, half_h, at)

    def prepare() -> None:
        """Every copy made on its spot; then each group of them (planned: copy_groups) brought down
        with the camera over it (look_over), each set of the same fall_wait at once ("el
        copykill"), the slowest first, so each has fallen for just its own fall_wait when
        everything is paused (and the early camps born, by the first group's pause)."""
        copied = sorted((u for u in structures if u["id"] in plan), key=lambda u: plan[u["id"]])
        note(f"Preparing rubble: making copies of {len(copied)} structures" if copied else "Waiting for the camps to spawn")
        for u in copied:
            os.environ[COPY_TRYING] = u["type"]
            send(f"el copy {plan[u['id']]} make {u['x']:g} {u['y']:g} {u['copy']['x']:g} {u['copy']['y']:g}")
        os.environ.pop(COPY_TRYING, None)
        camps_born = early["at"] + CAMP_WAIT if early["camps"] else 0.0
        for n, group in enumerate(copy_groups or [[]], start=1):
            if group:
                note(f"Preparing rubble: bringing down copies of the structures, group {n} of {len(copy_groups)}")
                look_over(*view_middle(group, at=lambda u: (u["copy"]["x"], u["copy"]["y"])))
            started_at = time.time()
            ends = [started_at, camps_born]
            longest = max((wait_of(u) for u in group), default=0.0)
            for wait in sorted({wait_of(u) for u in group}, reverse=True):
                falling = [u for u in group if wait_of(u) == wait]
                settle(max(0.0, started_at + longest - wait - time.time()))
                os.environ[COPY_TRYING] = ",".join(sorted({u["type"] for u in falling}))
                send(f"el copykill {plan[falling[0]['id']]} {plan[falling[-1]['id']]}")
                os.environ.pop(COPY_TRYING, None)
                ends.append(time.time() + wait)
            settle(max(0.0, max(ends) - time.time()))
            send("el freeze")
        if copied:
            log(f"  rubble prepared: {len(copied)} copies brought down on their spots, in {len(copy_groups)} groups")
        prepared["done"] = True

    # What the standing shots left faded in: the structure last shot and its last neighbour. Each
    # structure fades them out in the command that fades it in and moves the camera, each neighbour
    # the one before it; kept as soon as each command is answered, so a step done again after a
    # lost focus starts from what is really shown.
    shown: dict[str, dict | None] = {"structure": None, "neighbour": None}

    def leftovers() -> list[tuple[dict, int]]:
        return [(s, 0) for s in (shown["structure"], shown["neighbour"]) if s is not None]

    def put_away() -> None:
        """Whatever the standing shots left faded in, faded out; our player back on Order."""
        back_to_order()
        if fading := leftovers():
            fade_all(fading)
        shown.update(structure=None, neighbour=None)

    def stand(u: dict) -> None:
        """A structure faded in (what the one before left faded out, in the same command as the
        camera's move), shot alone over white and black from both sides (pair_both), then (the sky
        still black, our player on Order) over black again with each neighbour (NEIGHBOUR_REACH)
        faded in beside it, one at a time, each in place of the one before: where the neighbour
        covers it, the neighbour stands in front (the stitch makes its masks:
        element_stitch.write_elements). Left faded in for the next one to put away."""
        back_to_order()
        tile = go(u, leftovers() + [(u, 1)])
        shown.update(structure=u, neighbour=None)
        # Its animations started over and paused at its kind's standing time (picked in the
        # calibration's review): every run catches it at the same moment, its effects lit.
        send(f"el restart {u['x']:g} {u['y']:g} {(manifest.get('standModels') or {}).get(u['type'], '')}".rstrip())
        settle(stand_wait(u, manifest.get("standWaits")))
        send(f"el hold {u['x']:g} {u['y']:g}")
        key = element_key("structure", u, "standing")
        if (shots := pair_both(key, u)) is not None:
            camera = shots["camera"]
            beside = []
            for n in structures:
                if n is u or math.dist((n["x"], n["y"]), (u["x"], u["y"])) >= NEIGHBOUR_REACH:
                    continue
                fade_all(([(shown["neighbour"], 0)] if shown["neighbour"] else []) + [(n, 1)])
                shown["neighbour"] = n
                settle(SHOW_SETTLE)
                frame = session.grab(dark_ok=True)
                if frame is not None:
                    save(f"{key}-with-{n['id']}", frame)
                    beside.append(n["id"])
            record("structure", u, "standing", tile, camera, neighbours=beside, chaos=shots["chaos"])

    def spawn(camps_now: list[dict]) -> None:
        send("el keep 1")  # the sweep leaves them
        for c in camps_now:
            send(f"el camp {c['camp']}")

    def camp_wave(camps_now: list[dict], spawned: bool = False) -> None:
        """Camps far enough apart spawned together, left to be born, frozen, each one shot
        (spawned: already, with the structures' first wave, and frozen with it)."""
        if not spawned:
            note("Waiting for the camps to spawn")
            spawn(camps_now)
            settle(CAMP_WAIT)
            send("el freeze")
        for c in camps_now:
            tile = go(c)
            if (camera := pair(element_key("camp", c, "spawned"))) is not None:
                record("camp", c, "spawned", tile, camera)
        send("el keep 0")
        send("clean")  # the camps' defenders removed

    def rubble_shots(u: dict, at: dict) -> dict | None:
        """A structure's remains (on the cell `at`) over white and black as drawn (Order's side), and
        as Chaos's side would draw them: a fallen structure keeps the team colour it died with
        whatever side our player is put on, so its remains are repainted in the other side's colour
        ("el paint": the game's SetTeamColor, with its hostile or ally colours, manifest
        "teamColours") and shot again. Both shots as drawn come first (the game's ClearTeamColor
        doesn't put the colour back: Dragon Shire's keeps stayed repainted), then the light sky back
        (held still), the repaint, and both repainted. The same remains each time, nothing switched.
        Remains that don't change when repainted have no team colour: no Chaos shots (Dragon
        Shire's moonwells': a side switch seemed to change theirs, but what changed was their debris
        moving). Remains with nothing in their circle are noted ("empty": a copy that fell to nothing)."""
        stem = element_key("structure", u, "rubble")
        radius = rubble_radius(u)
        other = (manifest.get("teamColours") or {}).get("hostile" if u.get("owner") == "order" else "ally")
        camera = session.camera()
        first = session.grab(dark_ok=True)
        shape = first.shape if first is not None else (0, 0)
        circle = circle_box(screen_point(at, camera or at, manifest["screen"], manifest["pxPerCell"]), radius * manifest["pxPerCell"], shape)
        white = still_frame(session, [circle], first)
        black = shot_over_black(session, white)
        if white is None or black is None:
            warn(f"  {stem}: no frame")
            return None
        save(stem, white)
        save(f"{stem}-black", black)
        v0, v1, u0, u1 = circle
        inside = white[v0:v1, u0:u1, :3]
        if inside.size and float((np.abs(inside.astype(np.int16) - LIGHT_LEVEL).max(axis=2) > EMPTY_DIFF).mean()) < EMPTY_SHARE:
            return {"camera": camera, "chaos": None, "empty": True}
        if not other or not send(f"sky {LIGHT_SKY} 0"):
            return {"camera": camera, "chaos": None}
        light = still_frame(session, [circle])
        paint = f"el paint {at['x']:g} {at['y']:g} {radius:g}"
        if light is None or not send(f"{paint} SetTeamColor~{','.join(map(str, other['diffuse']))}~{','.join(map(str, other.get('emissive', other['diffuse'])))}"):
            return {"camera": camera, "chaos": None}
        chaos_white, changed = recoloured_frame(session, light, [circle], PAINT_CAP)
        chaos_black = shot_over_black(session, chaos_white) if changed else None
        send(f"{paint} ClearTeamColor")
        if chaos_black is None:
            return {"camera": camera, "chaos": None}
        save(f"{stem}-chaos", chaos_white)
        save(f"{stem}-chaos-black", chaos_black)
        return {"camera": camera, "chaos": f"{stem}-chaos"}

    fell_to_nothing: list[dict] = []

    def copy_rubble(u: dict) -> None:
        """A copy's remains shot on its spot, recorded as if from its structure's cell; a copy that
        fell to nothing (Dragon Shire's near the map's edges, now and then) leaves its structure to
        the waves, brought down itself."""
        spot = u["copy"]
        go({**u, "x": spot["x"], "y": spot["y"]})
        tile = source_tile(u, tiles)
        if (shots := rubble_shots(u, spot)) is not None and shots.get("empty"):
            warn(f"  {u['type']} {u['id']}: its copy at ({spot['x']:g}, {spot['y']:g}) fell to nothing; brought down itself")
            fell_to_nothing.append(u)
        elif shots is not None:
            camera = shots["camera"]
            if camera:
                camera = {"x": camera["x"] + u["x"] - spot["x"], "y": camera["y"] + u["y"] - spot["y"]}
            record("structure", {**u, "radius": rubble_radius(u)}, "rubble", tile, camera, chaos=shots["chaos"])

    def wave(structures_now: list[dict], n: int, waves: int) -> None:
        """A wave brought down (faded in first: faded out after its standing shot), a group at a
        time with the camera over it (look_over), each group left to settle and paused; then each
        one's rubble shot alone, and the remains cleared away. `n` of `waves`: for the note."""
        note(f"Rubble wave {n} of {waves}: waiting for the structures to fall")
        for group in grouped(structures_now):
            look_over(*view_middle(group))
            fade_all([(u, 1) for u in group])
            # The slowest brought down first, so each has fallen for just its own fall_wait at the pause.
            started_at, longest, ends = time.time(), max(wait_of(u) for u in group), []
            for u in sorted(group, key=lambda u: -wait_of(u)):
                settle(max(0.0, started_at + longest - wait_of(u) - time.time()))
                if u["core"]:
                    send(f"el core {1 if u['owner'] == 'order' else 2} -1", timeout=5.0)
                    replaced.add(1 if u["owner"] == "order" else 2)
                else:
                    send(f"el kill {u['x']:g} {u['y']:g} -1")
                ends.append(time.time() + wait_of(u))
            settle(max(0.0, max(ends) - time.time()))
            send("el freeze")
        for u in structures_now:
            tile = go(u)
            if (shots := rubble_shots(u, u)) is not None:
                record("structure", {**u, "radius": rubble_radius(u)}, "rubble", tile, shots["camera"], chaos=shots["chaos"])
        send("el clear")

    def plan_copies() -> dict:
        """Each structure with rubble still to shoot and a spare spot (planned when the map was
        prepared), but any type whose copy crashed the game, by id: its copy's number."""
        crashed = [t for t in os.environ.get(COPY_CRASHED, "").split(",") if t]
        if os.environ.get(COPY_TRYING):
            lost = os.environ.pop(COPY_TRYING).split(",")
            crashed += lost
            os.environ[COPY_CRASHED] = ",".join(crashed)
            warn(f"  copies of {', '.join(lost)} crashed the game: from now on they fall in waves")
        copied = [u for u in structures if u.get("copy") and u["type"] not in crashed and not goes_with_a_fort(u, structures)
                  and element_key("structure", u, "rubble") not in records][:COPY_LIMIT]
        # In groups to bring down under one camera each; numbered by group, then by fall_wait, the
        # slowest first: each set's numbers in a row, for "el copykill".
        copy_groups[:] = [sorted(g, key=lambda u: -wait_of(u)) for g in grouped(copied, at=lambda u: (u["copy"]["x"], u["copy"]["y"]))]
        return {u["id"]: n for n, u in enumerate(u for g in copy_groups for u in g)}

    replaced: set[int] = set()
    started = time.time()
    copy_groups: list[list[dict]] = []
    plan = plan_copies()
    prepared = {"done": False}
    with stage("elements (standing)"):
        pending_structures = [u for u in structures if element_key("structure", u, "standing") not in records]
        pending_camps = [c for c in camps if element_key("camp", c, "spawned") not in records]
        camp_waves = rubble_waves(pending_camps)
        # The first camp wave spawned with the scene hidden, born while the structures' first wave
        # plays, and shot after the structures: only camps clear of every structure's circle (the
        # structures' shots must not have them in their cut-outs).
        early = {"camps": [c for c in (camp_waves[0] if camp_waves and pending_structures else [])
                           if all(math.dist((c["x"], c["y"]), (u["x"], u["y"])) >= c["radius"] + u["radius"] for u in structures)],
                 "at": 0.0}
        send(tile_command(tiles[0]))  # the scene and the capture camera as the tiles have them
        # The structures faded out, not hidden: each keeps its look as the map paused it, effects
        # and all (hidden and shown again, a structure restarts its animations, a level 3 tower's
        # into its birth, and gives off no particles until it plays).
        send("el fadeall 0")
        send("el env off", timeout=10.0)
        if early["camps"]:
            spawn(early["camps"])
            early["at"] = time.time()
        if plan or early["camps"]:
            step(prepare, "preparing the rubble")
        with ui.bar(len(pending_structures) + len(pending_camps), "elements standing") as advance:
            for u in pending_structures:
                step(lambda: stand(u), element_key("structure", u, "standing"))
                advance()
            step(put_away, "the last structure shot faded out")
            if early["camps"]:
                step(lambda: camp_wave(early["camps"], spawned=True), "camps spawned early")
                advance(len(early["camps"]))
            late = [c for c in pending_camps if c not in early["camps"]]
            for n, camps_now in enumerate(rubble_waves(late), start=1):
                step(lambda: camp_wave(camps_now), f"camp wave {n}")
                advance(len(camps_now))
        log(f"  {len(pending_structures) + len(pending_camps)} element(s) shot standing; the structures from both sides"
            + (f" ({side['unchanged']} with nothing that changed with the side)" if side["unchanged"] else ""))
    # Only structures have rubble (camps aren't destroyed).
    with stage("elements (rubble)"):
        pending_rubble = [u for u in structures if element_key("structure", u, "rubble") not in records]
        if plan and not prepared["done"]:
            step(prepare, "preparing the rubble")
        copied = [u for u in pending_rubble if u["id"] in plan]
        rest = [u for u in pending_rubble if u["id"] not in plan]
        with ui.bar(len(pending_rubble), "rubble") as advance:
            for u in copied:
                step(lambda: copy_rubble(u), element_key("structure", u, "rubble"))
                if u not in fell_to_nothing:
                    advance()
            if copied:
                send("el clear")  # the copies' remains
            waves = rubble_waves(rest + fell_to_nothing, radius=rubble_radius)
            for n, structures_now in enumerate(waves, start=1):
                log(f"  wave {n} of {len(waves)}: {len(structures_now)} structure(s)")
                step(lambda: wave(structures_now, n, len(waves)), f"rubble wave {n}")
                advance(len(structures_now))
    with stage("elements (clearing)"):
        def clear() -> None:
            note("Clearing the remains before the tiles")
            for team in (1, 2):  # a resumed run: everything still standing comes down now
                if team not in replaced:
                    send(f"el core {team} 0", timeout=5.0)
            send(f"el killall {KILL_WAIT:g}", timeout=KILL_WAIT + 10)
            send("el env on", timeout=10.0)
            send("el clear all")
            send("el holes show")
            send("el hideall")
            send("el play")  # the doodads shown again give off their particles, then hold them
            settle(PLAY_WAIT)
            send("el freeze")
            send("clean")

        step(clear, "clearing the remains")
    saver.shutdown(wait=True)
    log(f"elements: {len(records)} shot ({len(structures)} structures standing and as rubble, {len(camps)} camps) "
        f"in {time.time() - started:.0f} s; the structures are down, the tiles are the bare terrain")
