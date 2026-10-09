"""The elements capture (ELEMENTS-PLAN.md, The run), before a render's tiles when it is prepared with
--structures elements. The whole scene is hidden once (every structure and unit, and the terrain,
doodads and water switched off: "el env off") over the white sky, and the camera goes to each
element in turn, straight above it in the middle of the screen:

1. standing: every structure faded out by its opacity ("el fadeall 0"), not hidden: it keeps its
   look as the map paused it once ready, its effects built up while the map ran and held still
   (the prepared map's models freeze their particles and ribbons with their animations); hidden
   and shown again, a structure restarts its animations (a level 3 tower's into its birth) and
   gives off no particles until it plays. Then each faded in, shot alone over white and over
   black, and faded out again. The camps spawned together (those near enough to share a shot in
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
it out (elements.cut_out) and place it. A fresh launch after a lost match skips what is recorded,
then brings the structures down again (they all stand again in a new match).
"""

import json
import math
import os
import time
from pathlib import Path

from . import ui
from .elements import rubble_radius, rubble_waves, source_tile, view_groups, view_middle
from .frames import save_frame

from .game_control import Recoverable, settle, step
from .runlog import log, stage, warn

# Real seconds from a structure's fall to its rubble's shot, by kind and team: chosen from the film
# probe on Battlefield of Eternity (each kind on each team filmed falling, a frame every quarter
# second: the moment its rubble looked best). The first entry that matches: a type prefix, a
# suffix ("" any), a team. Hell gates leave nothing once their explosion is over. A structure
# none matches: FALL_WAIT_OTHER; a core: CORE_WAIT (chosen from its own film: by 6 its debris has
# settled smaller).
FALL_WAITS = (
    ("TownCannonTowerL3Standalone", "", "order", 2.10), ("TownCannonTowerL3", "", "order", 2.02), ("TownCannonTowerL2", "", "order", 2.12),
    ("TownCannonTowerL3Standalone", "", "chaos", 3.00), ("TownCannonTowerL3", "", "chaos", 2.25), ("TownCannonTowerL2", "", "chaos", 2.21),
    ("TownGateL3", "", "order", 1.33), ("TownGate", "", "order", 1.23), ("TownGate", "", "chaos", 1.0),
    ("TownWallRadial", "L3", "order", 2.48), ("TownWallRadial", "L2", "order", 2.46),
    ("TownWallRadial", "L3", "chaos", 2.24), ("TownWallRadial", "L2", "chaos", 1.33),
    ("TownMoonwellL3", "", "order", 3.30), ("TownMoonwellL2", "", "order", 2.64),
    ("TownMoonwellL3", "", "chaos", 1.92), ("TownMoonwellL2", "", "chaos", 1.84),
    ("TownTownHallL3", "", "order", 3.02), ("TownTownHallL2", "", "order", 2.73),
    ("TownTownHallL3", "", "chaos", 2.74), ("TownTownHallL2", "", "chaos", 2.62),
)
FALL_WAIT_OTHER = 2.0
CORE_WAIT = 4.8
KILL_WAIT = 5.0  # the clearing's: everything left standing brought down, its rubble not shot


def fall_wait(structure: dict) -> float:
    """How long a fallen structure is left before its rubble is shot (FALL_WAITS)."""
    if structure["core"]:
        return CORE_WAIT
    kind = structure["type"]
    return next((wait for prefix, suffix, owner, wait in FALL_WAITS
                 if kind.startswith(prefix) and kind.endswith(suffix) and structure.get("owner") == owner), FALL_WAIT_OTHER)


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
# Seconds from a structure shown (or a camp frozen) to its shot: a frame or two to be drawn.
SHOW_SETTLE = 0.1
# A fall gives off its particles (smoke, dust, sparks, a core's lava) only near the camera (the
# in-view probe on Battlefield of Eternity: with the camera far away, or far back over the whole
# map, a copy's rubble had only its chunks; at the edge of the capture camera's view a wall's had
# none; with the camera VIEW_BACK times the capture's distance back, centred, every kind had them
# all). So everything brought down at once is brought down with the camera that far back over
# it, all of it within VIEW_SHARE of that view either way of its middle (elements.view_groups).
VIEW_BACK, VIEW_SHARE = 2.5, 0.5
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


def capture_elements(session, manifest: dict, base: Path, tile_command, black_settled) -> None:
    """The passes (see the module's note). `base`: the run's folder (the tiles' folder's parent);
    `tile_command`: the capture's command for a tile (one, first, puts the whole scene in place:
    what ran before, the sky layers' shots, leaves the camera as they need it); `black_settled`:
    the capture's wait for the black sky to be drawn (session, first frame, white frame) -> frame."""
    found = manifest.get("elements") or {}
    structures, camps = found.get("structures", []), found.get("camps", [])
    folder = base / "elements"
    folder.mkdir(exist_ok=True)
    record_path = folder / "elements.json"
    records: dict = json.loads(record_path.read_text()) if record_path.exists() else {}
    tiles = manifest["tiles"]
    failures = {"count": 0}

    def send(command: str, timeout: float = 3.0) -> bool:
        if session.send(command, timeout=timeout) is not None:
            failures["count"] = 0
            return True
        failures["count"] += 1
        warn(f"  no answer to \"{command}\"")
        if failures["count"] >= FAILURES:
            raise Recoverable(f"the map stopped answering during the elements capture (\"{command}\")", resume_at=0)
        return False

    def go(element: dict) -> dict:
        """The camera straight above the element ("el at": the lighting refitted there, as the tiles
        do, but none of the rest of their scene), the white sky back; answered once the camera has
        stopped. Returns the grid tile it belongs to, the one the stitch places it by."""
        send(f"el at {element['x']:.2f} {element['y']:.2f}")
        return source_tile(element, tiles)

    def pair(stem: str) -> dict | None:
        """The view over white and over black, saved as <stem> and <stem>-black; the camera's
        position. None without a frame. The sky stays black: the next move puts the white back."""
        settle(SHOW_SETTLE)
        white = session.grab(dark_ok=True)
        status = session.status()
        answer = session.send("black", timeout=3.0)
        black = black_settled(session, session.blank(answer[1]), white) if answer is not None and white is not None else None
        if white is None or black is None:
            warn(f"  {stem}: no frame")
            return None
        save_frame(folder / stem, white)
        save_frame(folder / f"{stem}-black", black)
        return {"x": status.camera_x, "y": status.camera_y} if status else {}

    def record(kind: str, element: dict, state: str, tile: dict, camera: dict, **extra) -> None:
        key = element_key(kind, element, state)
        records[key] = {"kind": kind, "state": state, "element": element["id"] if kind == "structure" else element["camp"],
                        "x": element["x"], "y": element["y"], "radius": element["radius"], "tile": tile["index"],
                        "shot": key, "camera": camera or None, **extra}
        record_path.write_text(json.dumps(records, indent=1))

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
        for u in copied:
            os.environ[COPY_TRYING] = u["type"]
            send(f"el copy {plan[u['id']]} make {u['x']:g} {u['y']:g} {u['copy']['x']:g} {u['copy']['y']:g}")
        os.environ.pop(COPY_TRYING, None)
        camps_born = early["at"] + CAMP_WAIT if early["camps"] else 0.0
        for group in copy_groups or [[]]:
            if group:
                look_over(*view_middle(group, at=lambda u: (u["copy"]["x"], u["copy"]["y"])))
            started_at = time.time()
            ends = [started_at, camps_born]
            longest = max((fall_wait(u) for u in group), default=0.0)
            for wait in sorted({fall_wait(u) for u in group}, reverse=True):
                falling = [u for u in group if fall_wait(u) == wait]
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

    def fade(u: dict, value: int) -> None:
        send(f"el scopemsg {u['x']:g} {u['y']:g} SetOpacity {value} 0")

    def stand(u: dict) -> None:
        """A structure faded in, shot alone over white and black, then (the sky still black) over
        black again with each neighbour (NEIGHBOUR_REACH) faded in beside it, one at a time:
        where the neighbour covers it, the neighbour stands in front (the stitch makes its masks:
        stitch.write_elements); faded out again."""
        fade(u, 1)
        tile = go(u)
        key = element_key("structure", u, "standing")
        if (camera := pair(key)) is not None:
            beside = []
            for n in structures:
                if n is u or math.dist((n["x"], n["y"]), (u["x"], u["y"])) >= NEIGHBOUR_REACH:
                    continue
                fade(n, 1)
                settle(SHOW_SETTLE)
                frame = session.grab(dark_ok=True)
                fade(n, 0)
                if frame is not None:
                    save_frame(folder / f"{key}-with-{n['id']}", frame)
                    beside.append(n["id"])
            record("structure", u, "standing", tile, camera, neighbours=beside)
        fade(u, 0)

    def spawn(camps_now: list[dict]) -> None:
        send("el keep 1")  # the sweep leaves them
        for c in camps_now:
            send(f"el camp {c['camp']}")

    def camp_wave(camps_now: list[dict], spawned: bool = False) -> None:
        """Camps far enough apart spawned together, left to be born, frozen, each one shot
        (spawned: already, with the structures' first wave, and frozen with it)."""
        if not spawned:
            spawn(camps_now)
            settle(CAMP_WAIT)
            send("el freeze")
        for c in camps_now:
            tile = go(c)
            if (camera := pair(element_key("camp", c, "spawned"))) is not None:
                record("camp", c, "spawned", tile, camera)
        send("el keep 0")
        send("clean")  # the camps' defenders removed

    def copy_rubble(u: dict) -> None:
        """A copy's remains shot on its spot, recorded as if from its structure's cell."""
        spot = u["copy"]
        go({**u, "x": spot["x"], "y": spot["y"]})
        tile = source_tile(u, tiles)
        if (camera := pair(element_key("structure", u, "rubble"))) is not None:
            if camera:
                camera = {"x": camera["x"] + u["x"] - spot["x"], "y": camera["y"] + u["y"] - spot["y"]}
            record("structure", {**u, "radius": rubble_radius(u)}, "rubble", tile, camera)

    def wave(structures_now: list[dict]) -> None:
        """A wave brought down (faded in first: faded out after its standing shot), a group at a
        time with the camera over it (look_over), each group left to settle and paused; then each
        one's rubble shot alone, and the remains cleared away."""
        for group in grouped(structures_now):
            look_over(*view_middle(group))
            for u in group:
                fade(u, 1)
            # The slowest brought down first, so each has fallen for just its own fall_wait at the pause.
            started_at, longest, ends = time.time(), max(fall_wait(u) for u in group), []
            for u in sorted(group, key=lambda u: -fall_wait(u)):
                settle(max(0.0, started_at + longest - fall_wait(u) - time.time()))
                if u["core"]:
                    send(f"el core {1 if u['owner'] == 'order' else 2} -1", timeout=5.0)
                    replaced.add(1 if u["owner"] == "order" else 2)
                else:
                    send(f"el kill {u['x']:g} {u['y']:g} -1")
                ends.append(time.time() + fall_wait(u))
            settle(max(0.0, max(ends) - time.time()))
            send("el freeze")
        for u in structures_now:
            tile = go(u)
            if (camera := pair(element_key("structure", u, "rubble"))) is not None:
                record("structure", {**u, "radius": rubble_radius(u)}, "rubble", tile, camera)
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
        copied = [u for u in structures if u.get("copy") and u["type"] not in crashed
                  and element_key("structure", u, "rubble") not in records][:COPY_LIMIT]
        # In groups to bring down under one camera each; numbered by group, then by fall_wait, the
        # slowest first: each set's numbers in a row, for "el copykill".
        copy_groups[:] = [sorted(g, key=lambda u: -fall_wait(u)) for g in grouped(copied, at=lambda u: (u["copy"]["x"], u["copy"]["y"]))]
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
            if early["camps"]:
                step(lambda: camp_wave(early["camps"], spawned=True), "camps spawned early")
                advance(len(early["camps"]))
            late = [c for c in pending_camps if c not in early["camps"]]
            for n, camps_now in enumerate(rubble_waves(late), start=1):
                step(lambda: camp_wave(camps_now), f"camp wave {n}")
                advance(len(camps_now))
        log(f"  {len(pending_structures) + len(pending_camps)} element(s) shot standing")
    # Only structures have rubble (camps aren't destroyed).
    with stage("elements (rubble)"):
        pending_rubble = [u for u in structures if element_key("structure", u, "rubble") not in records]
        if plan and not prepared["done"]:
            step(prepare, "preparing the rubble")
        copied = [u for u in pending_rubble if u["id"] in plan]
        rest = [u for u in pending_rubble if u["id"] not in plan]
        waves = rubble_waves(rest, radius=rubble_radius)
        with ui.bar(len(pending_rubble), "rubble") as advance:
            for u in copied:
                step(lambda: copy_rubble(u), element_key("structure", u, "rubble"))
                advance()
            if copied:
                send("el clear")  # the copies' remains
            for n, structures_now in enumerate(waves, start=1):
                log(f"  wave {n} of {len(waves)}: {len(structures_now)} structure(s)")
                step(lambda: wave(structures_now), f"rubble wave {n}")
                advance(len(structures_now))
    with stage("elements (clearing)"):
        def clear() -> None:
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
    log(f"elements: {len(records)} shot ({len(structures)} structures standing and as rubble, {len(camps)} camps) "
        f"in {time.time() - started:.0f} s; the structures are down, the tiles are the bare terrain")
