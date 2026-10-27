"""A map's calibration (capture/calibration.py, categories/battleground/calibrate.py): what it compares
and measures, its run against a stand-in game, and what the elements render takes from it."""

import importlib
import json
import math
import statistics
import sys
import types

import numpy as np
from PIL import Image
import pytest

class _Recoverable(Exception):
    def __init__(self, why, resume_at=None):
        super().__init__(why)
        self.why, self.resume_at = why, resume_at


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """The game's controls stood in for (they need Windows), with a clock that settle moves on; the
    modules that import them imported afresh."""
    clock = {"t": 0.0}

    def settle(seconds):
        clock["t"] += seconds

    monkeypatch.setitem(sys.modules, "heroes_capture.common.game_control",
                        types.SimpleNamespace(settle=settle, step=lambda action, what: action(), Recoverable=_Recoverable))
    for name in ("calibrate", "element_capture"):
        monkeypatch.delitem(sys.modules, f"heroes_capture.categories.battleground.{name}", raising=False)
    monkeypatch.delitem(sys.modules, "heroes_capture.capture.shots", raising=False)
    return clock


@pytest.fixture
def calibrate(monkeypatch, clock):
    """The calibration, its time and its shots' the clock's (imported through sys.modules: the
    package's attribute may still be an earlier import)."""
    calibrate = importlib.import_module("heroes_capture.categories.battleground.calibrate")

    fake_time = types.SimpleNamespace(time=lambda: clock["t"])
    monkeypatch.setattr(sys.modules["heroes_capture.capture.shots"], "time", fake_time)
    monkeypatch.setattr(calibrate, "time", fake_time)
    monkeypatch.setattr(calibrate, "log", lambda message: None)
    monkeypatch.setattr(calibrate, "warn", lambda message: None)
    calibrate.clock = clock
    return calibrate


SKY = (230, 230, 230)
GREY = (120, 110, 100)


def _frame(colour=GREY, radius=10, size=(120, 160)) -> np.ndarray:
    """The white sky with a disk in the middle."""
    frame = np.full((*size, 3), SKY, np.uint8)
    ys, xs = np.mgrid[:size[0], :size[1]]
    frame[(xs + 0.5 - size[1] / 2) ** 2 + (ys + 0.5 - size[0] / 2) ** 2 <= radius ** 2] = colour
    return frame


def _look(frame):
    from heroes_capture.categories.battleground import calibrate

    return calibrate.appearance(frame, (80, 60), 24)


def test_a_copy_that_looks_as_its_structure_does_passes():
    from heroes_capture.categories.battleground import calibrate

    own = _look(_frame())
    assert own["drawn"] == pytest.approx(314 / 1810, abs=0.01) and own["mean"] == [120.0, 110.0, 100.0] and own["dark"] == 0
    assert calibrate.matches(own, _look(_frame((126, 116, 104)))) is None  # a little brighter: as good


@pytest.mark.parametrize("copy, why", [
    ((5, 5, 5), "black"),  # Dragon Shire's void corners
    ((60, 55, 50), "darker"),
    ((150, 100, 60), "colour cast"),  # Battlefield of Eternity's Hell side, on a Heaven structure
])
def test_a_copy_that_looks_otherwise_fails_and_says_why(copy, why):
    from heroes_capture.categories.battleground import calibrate

    assert calibrate.matches(_look(_frame()), _look(_frame(copy))) == why


def test_a_copy_hardly_drawn_fails():
    from heroes_capture.categories.battleground import calibrate

    assert calibrate.matches(_look(_frame()), _look(_frame(radius=4))) == "hardly drawn"
    assert calibrate.matches(_look(_frame(radius=0)), _look(_frame(radius=4))) is None  # nothing to compare


def test_a_fall_settles_once_it_holds_still_long_enough():
    from heroes_capture.categories.battleground import calibrate

    moving = [(t / 10, 0.2) for t in range(1, 13)]
    still = [(t / 10, 0.001) for t in range(13, 25)]
    assert calibrate.settle_time(moving + still) == 1.3
    assert calibrate.settle_time(moving + still[:4]) is None  # not held for FALL_HOLD yet
    # Never before FALL_LEAST: a fall's first frames can be still before the debris flies.
    assert calibrate.settle_time([(t / 10, 0.0) for t in range(1, 12)]) == calibrate.FALL_LEAST


def test_a_spot_is_tried_only_inside_the_bounds_clear_of_the_copies_and_failed_spots():
    from heroes_capture.categories.battleground import calibrate

    bounds = {"left": 10, "right": 100, "bottom": 10, "top": 100}
    assert calibrate.usable((50, 50), bounds, [], [], 9.0)
    assert not calibrate.usable((5, 50), bounds, [], [], 9.0)  # the camera can't centre on it
    assert not calibrate.usable((50, 50), bounds, [(60, 50, 6.0)], [], 9.0)  # 10 < 6 + 9
    assert not calibrate.usable((50, 50), bounds, [], [(53, 50)], 9.0)  # near a failed spot


def test_the_structures_with_fewest_candidates_are_calibrated_first():
    from heroes_capture.categories.battleground import calibrate

    structures = [{"id": 1}, {"id": 2}, {"id": 3}]
    assert [u["id"] for u in calibrate.calibration_order(structures, {"1": [[0, 0]] * 3, "3": [[0, 0]]})] == [3, 1, 2]


def test_the_candidates_are_nearest_first_and_the_calibrated_spots_are_kept_for_the_structures_there():
    from heroes_capture.categories.battleground import elements

    structures = [{"id": 5, "type": "TownCannonTowerL2", "x": 50.5, "y": 50, "radius": 8.0, "core": False},
                  {"id": 6, "type": "KingsCore", "x": 20, "y": 20, "radius": 8.0, "core": True}]
    bounds = {"left": 0, "right": 120, "bottom": 0, "top": 120}
    candidates = elements.copy_candidates(structures, [], bounds, 120, None)
    assert list(candidates) == [5]  # no copy of a core
    spots = candidates[5]
    near = [math_dist(p, (50.5, 50)) for p in spots]
    assert near == sorted(near) and all(p[0] % 1 == 0.5 for p in spots)  # on the same part of a cell
    assert elements.calibrated_copy_spots(structures, {"copySpots": {"5": {"x": 30.5, "y": 50}, "9": {"x": 1, "y": 1}}}) == {5: {"x": 30.5, "y": 50}}


def math_dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def test_a_fall_waits_the_time_picked_in_the_map_s_review_or_battlefield_of_eternity_s():
    from heroes_capture.categories.battleground import elements as waits

    tower = {"type": "TownCannonTowerL2", "owner": "order", "core": False}
    shrine = {"type": "DragonShrine", "owner": "order", "core": False}
    picked = {"TownCannonTowerL2|order": 3.25}
    assert waits.fall_wait(tower, picked) == 3.25
    assert waits.fall_wait(tower) == 2.12  # filmed on Battlefield of Eternity
    assert waits.fall_wait(shrine, picked) == waits.FALL_WAIT_OTHER
    assert waits.fall_wait({**tower, "core": True}, picked) == waits.CORE_WAIT


STAGES = {"motion": 1, "structures": 2, "rubble-at-home": 1}


def test_a_calibration_counts_only_with_every_stage_at_its_current_version(tmp_path):
    from heroes_capture.capture import calibration

    path = calibration.path_for(tmp_path)
    assert calibration.current(path, STAGES) is None
    path.write_text(json.dumps({"version": calibration.VERSION, "stages": {"motion": 1, "structures": 1, "rubble-at-home": 1}}))
    assert calibration.current(path, STAGES) is None  # the structures stage is out of date
    path.write_text(json.dumps({"version": calibration.VERSION, "stages": STAGES}))
    assert calibration.current(path, STAGES) == {"version": calibration.VERSION, "stages": STAGES}


def test_the_stages_run_are_those_named_or_all_of_them():
    from heroes_capture.capture import calibration

    assert calibration.chosen_stages([], STAGES) == ["motion", "structures", "rubble-at-home"]
    assert calibration.chosen_stages(["rubble-at-home", "motion"], STAGES) == ["motion", "rubble-at-home"]
    with pytest.raises(ValueError, match="no calibration stage rubble"):
        calibration.chosen_stages(["rubble"], STAGES)
    # A probe runs only when named, after the stages.
    assert calibration.chosen_stages([], STAGES, {"pausing": 1}) == ["motion", "structures", "rubble-at-home"]
    assert calibration.chosen_stages(["pausing"], STAGES, {"pausing": 1}) == ["pausing"]


MANIFEST_FACTS = {"map": "Dragon Shire", "screen": {"w": 3440, "h": 1440}, "pxPerCell": 48.0, "mapSize": {"width": 192, "height": 192},
                  "cameraBounds": {"left": 10, "bottom": 10, "right": 180, "top": 180}}


def test_a_calibration_writes_the_start_up_s_findings_and_each_hook_s(tmp_path):
    from heroes_capture.capture import calibration

    asked = []
    hook = lambda session, m, base, stages, previous: asked.append((stages, previous)) or {"structures": {"5": {"changes": True}}}
    path = calibration.run(None, MANIFEST_FACTS, [hook], STAGES, [], tmp_path)
    found = json.loads(path.read_text())
    assert asked == [(["motion", "structures", "rubble-at-home"], {})]
    assert found["version"] == calibration.VERSION and found["stages"] == STAGES and found["structures"] == {"5": {"changes": True}}
    assert found["map"] == "Dragon Shire" and found["cameraBounds"] == MANIFEST_FACTS["cameraBounds"]


def test_a_run_of_one_stage_is_merged_into_the_calibration_so_far(tmp_path):
    """A focused probe: only its stage measured, what the others found kept, and the stage
    recorded at its version. An older calibration (before the stages had versions) had the motion
    and structures stages."""
    from heroes_capture.capture import calibration

    (tmp_path / calibration.PREVIOUS).write_text(json.dumps({"version": 2, "map": "Dragon Shire", "motion": [1], "tries": [{"id": 5}],
                                                             "rubbleAtHome": {"old": {}}}))
    asked = []
    hook = lambda session, m, base, stages, previous: asked.append((stages, previous["tries"])) or {"rubbleAtHome": {"new": {}}}
    found = json.loads(calibration.run(None, MANIFEST_FACTS, [hook], STAGES, ["rubble-at-home"], tmp_path).read_text())
    assert asked == [(["rubble-at-home"], [{"id": 5}])]
    assert found["motion"] == [1] and found["tries"] == [{"id": 5}] and found["rubbleAtHome"] == {"new": {}}
    assert found["version"] == calibration.VERSION and found["stages"] == {"motion": 1, "structures": 1, "rubble-at-home": 1}


# ------------------------------------------------------------------------------------------------
# The run, against a stand-in game
# ------------------------------------------------------------------------------------------------


class _CalibrationGame:
    """The camera goes where "tile", "el at" and "el wide" send it; the frame shows what stands in the
    middle: the tower at (50, 50) (grey from Order's side, bluer from Chaos's, a tenth of a second
    after the switch, with a patch on it that flickers from one look to the next however paused),
    the core at (80, 80) (the same from either side), a copy on its spot (black left of x = 10, the
    void; elsewhere with the tower's fire), or a copy's fall (flickering for 1.2 s, then still,
    smaller)."""

    def __init__(self, clock):
        self.clock, self.sent = clock, []
        self.at, self.team, self.previous, self.switched, self.copy, self.looks = (0.0, 0.0), 1, 1, -1.0, None, 0
        self.fallen, self.scene = set(), False

    def command(self, text, timeout=3.0):
        self.sent.append(text)
        words = text.split()
        if words[0] == "tile":
            self.at = (float(words[2]), float(words[3]))
        elif words[:2] in (["el", "at"], ["el", "wide"]):
            self.at = (float(words[2]), float(words[3]))
        elif words[0] == "side":
            self.previous, self.team, self.switched = self.team, int(words[1]), self.clock["t"]
        elif words[:3] == ["el", "copy", "0"]:
            if words[3] == "make":
                self.copy = {"at": (float(words[6]), float(words[7])), "fell": None}
            elif words[3] == "remove":
                self.copy = None
            elif words[3] == "kill":
                self.copy["fell"] = self.clock["t"]
        elif words[:2] == ["el", "clear"] and self.copy and self.copy["fell"] is not None:
            self.copy = None
        elif words[:2] == ["el", "kill"]:
            self.fallen.add((float(words[2]), float(words[3])))
        elif words[:2] == ["el", "env"]:
            self.scene = words[2] == "on"
        return types.SimpleNamespace()

    def camera(self, default=None):
        return {"x": self.at[0], "y": self.at[1]}

    def grab(self, dark_ok=False):
        self.clock["t"] += 0.01
        self.looks += 1
        t = self.clock["t"]
        if self.at in self.fallen:  # its rubble at home: darker with the scene hidden than shown
            return _frame((120, 110, 100) if self.scene else (90, 82, 75), radius=6)
        if self.at == (50.0, 50.0):
            team = self.team if t >= self.switched + 0.1 else self.previous
            frame = _frame(GREY if team == 1 else (100, 110, 140))
            frame[40:56, 64:80] = (250, 200, 60) if self.looks % 2 else (200, 120, 30)  # a fire on it
            return frame
        if self.at == (80.0, 80.0):
            return _frame()
        if self.copy and self.copy["at"] == self.at:
            colour = (5, 5, 5) if self.at[0] < 10 else GREY
            if self.copy["fell"] is None:
                frame = _frame(colour)
                if colour == GREY:
                    frame[40:56, 64:80] = (250, 200, 60) if self.looks % 2 else (200, 120, 30)  # the same fire
                return frame
            falling = t - self.copy["fell"]
            return _frame(colour if falling >= 1.2 or int(falling * 100) % 2 else SKY, radius=6)
        return _frame(radius=0)


CALIBRATION_MANIFEST = {
    "tiles": [{"index": 0, "x": 50, "y": 50}], "screen": {"w": 160, "h": 120}, "pxPerCell": 4, "distance": 85.0,
    "cameraBounds": {"left": 0, "right": 100, "bottom": 0, "top": 100},
    "elements": {
        "structures": [{"id": 6, "type": "KingsCore", "x": 80, "y": 80, "owner": "chaos", "core": True, "radius": 6.0},
                       {"id": 5, "type": "TownCannonTowerL2", "x": 50, "y": 50, "owner": "order", "core": False, "radius": 6.0}],
        "copyCandidates": {"5": [[5, 50], [7, 50], [30, 50]]},
        "doodads": [{"type": "Storm_Doodad_DS19_Embers", "x": 20, "y": 20}],
        "report": {"structures": {}, "models": {}, "doodads": {}},
    },
}


def test_the_calibration_measures_each_structure_and_finds_its_copy_a_spot_that_looks_right(calibrate, tmp_path):
    game = _CalibrationGame(calibrate.clock)
    found = calibrate.calibrate(game, CALIBRATION_MANIFEST, tmp_path)
    # Each tile looked at as the tiles see it (what moves while paused); then the scene hidden as
    # the elements capture hides it; the tower first (it has candidates): faded in, switched to
    # Chaos and back, faded out; a copy on the nearest candidate (in the void: it comes out black,
    # removed), the next skipped (next to the failed one), a copy on the third (as the tower
    # looks), looked at from the render's camera, far back, then brought down and filmed from the
    # capture's, its rubble cleared; then the core; last, the tower brought down at home (its kind's
    # Battlefield of Eternity wait) and its rubble shot with the scene hidden and shown (not the
    # core: the match would end).
    assert game.sent == ["tile 0 50.00 50.00",
                         "tile 0 50.00 50.00", "el fadeall 0", "el env off",
                         "el at 50.00 50.00 50 50 1", "side 2", "side 1", "el fades 50 50 0",
                         "el copy 0 make 50 50 5 50", "el at 5.00 50.00", "el copy 0 remove",
                         "el copy 0 make 50 50 30 50", "el at 30.00 50.00", "el wide 30.00 50.00 212.5", "el at 30.00 50.00",
                         "el copy 0 kill", "el clear",
                         "el at 80.00 80.00 80 80 1", "side 2", "side 1",
                         "el at 50.00 50.00 50 50 1", "el kill 50 50 2.12", "el env on", "el env off", "el clear"]
    # The fire found moving while paused, on the tower; and on the tower alone, where it didn't
    # pass for a side switch (the tower's own switch, a tenth of a second, did).
    assert [(p["what"], p["tile"]) for p in found["motion"]] == [({"kind": "structure", "type": "TownCannonTowerL2", "id": 5}, 0)]
    tower = found["structures"]["5"]
    assert tower["changes"] and 0.1 <= tower["toChaos"] < 0.2 and tower["moves"] > calibrate.MOVING_SHARE and tower["moving"]
    assert found["structures"]["6"]["changes"] is False and found["structures"]["6"]["toChaos"] is None
    assert found["copySpots"] == {"5": {"x": 30, "y": 50}}
    fall = found["falls"]["TownCannonTowerL2|order"]
    assert 1.2 <= fall["settle"] < 1.4 and fall["film"] == "fall-TownCannonTowerL2-order.png" and fall["frames"] == 24
    assert fall["wide"] == "wide-TownCannonTowerL2-order.png"
    assert [(t["x"], t["failed"]) for t in found["tries"]] == [(5, "black"), (30, None)]
    home = found["rubbleAtHome"]["TownCannonTowerL2|order"]
    assert home["ratio"] == pytest.approx(82.33 / 110, abs=0.005) and home["wait"] == 2.12 and home["copy"] == "structure-5-at-30-50-rubble.png"
    assert home["copyRatio"] == round(statistics.fmean(GREY) / statistics.fmean((90, 82, 75)), 3)
    pictures = {p.name for p in (tmp_path / "calibration").iterdir()}
    assert {"structure-5.png", "structure-5-moving.webp", "structure-5-at-5-50.png", "structure-5-at-30-50-rubble.png",
            "fall-TownCannonTowerL2-order.png", found["motion"][0]["picture"]} <= pictures


def test_a_patch_that_moved_is_found_and_placed():
    from heroes_capture.categories.battleground import calibrate

    moved = np.zeros((120, 160), bool)
    moved[40:56, 64:80] = True
    moved[100, 10] = True  # a lone pixel: not a patch
    [(box, middle, pixels)] = calibrate.moving_patches(moved)
    assert box == (40, 56, 64, 80) and middle == (71.5, 47.5) and pixels == 256
    assert calibrate.cell_at(middle, {"x": 50, "y": 50}, {"w": 160, "h": 120}, 4) == (47.875, 53.125)
    doodads = [{"type": "Embers", "x": 20, "y": 21}]
    assert calibrate.what_is_at((20, 20), [], doodads) == {"kind": "doodad", "type": "Embers"}
    assert calibrate.what_is_at((40, 40), [], doodads) == {"kind": "unknown"}
    merged = calibrate.same_patches([{"x": 10, "y": 10, "pixels": 50}, {"x": 11, "y": 10, "pixels": 80}, {"x": 30, "y": 10, "pixels": 5}])
    assert [p["pixels"] for p in merged] == [80, 5]


# ------------------------------------------------------------------------------------------------
# The review
# ------------------------------------------------------------------------------------------------

OWN = {"drawn": 0.4, "mean": [120.0, 110.0, 100.0], "dark": 0.0}


def _measured() -> dict:
    """A calibration's measurements: two towers (one copy spot passed easily, one narrowly), a gate
    whose two structures disagree on team colour, a filmed fall, things moving while paused."""
    tower = {"type": "TownCannonTowerL2", "owner": "order", "moves": 0.0, "standing": "structure-1.png"}
    return {
        "version": 2, "map": "Dragon Shire", "tool": "0.0.0-dev", "cameraBounds": {"left": 0, "bottom": 0, "right": 100, "top": 100},
        "structures": {"1": {**tower, "changes": True, "toChaos": 0.1, "toOrder": 0.1},
                       "2": {**tower, "changes": True, "toChaos": 0.1, "toOrder": 0.1, "standing": "structure-2.png"},
                       "3": {"type": "TownGateL2", "owner": "order", "moves": 0.0, "standing": "structure-3.png", "changes": True, "toChaos": 0.2, "toOrder": 0.2},
                       "4": {"type": "TownGateL2", "owner": "order", "moves": 0.0, "standing": "structure-4.png", "changes": False, "toChaos": None, "toOrder": None}},
        "copySpots": {"1": {"x": 30, "y": 50}, "2": {"x": 70, "y": 50}},
        "tries": [{"id": 1, "type": "TownCannonTowerL2", "x": 30, "y": 50, "own": OWN, "copy": {"drawn": 0.4, "mean": [122.0, 111.0, 101.0], "dark": 0.0},
                   "standing": "structure-1-at-30-50.png", "rubble": "structure-1-at-30-50-rubble.png", "failed": None},
                  {"id": 2, "type": "TownCannonTowerL2", "x": 5, "y": 50, "own": OWN, "copy": {"drawn": 0.4, "mean": [5.0, 5.0, 5.0], "dark": 1.0}, "failed": "black"},
                  {"id": 2, "type": "TownCannonTowerL2", "x": 70, "y": 50, "own": OWN, "copy": {"drawn": 0.25, "mean": [100.0, 95.0, 85.0], "dark": 0.05},
                   "standing": "structure-2-at-70-50.png", "rubble": "structure-2-at-70-50-rubble.png", "failed": None}],
        "falls": {"TownCannonTowerL2|order": {"settles": [1.3], "settle": 1.3, "film": "fall-TownCannonTowerL2-order.png", "step": 0.25, "frames": 24},
                  "DragonShrine|order": {"settles": [2.0], "settle": 2.0, "film": "fall-DragonShrine-order.png", "step": 0.25, "frames": 24}},
        "motion": [{"x": 20, "y": 20, "pixels": 400, "tile": 0, "what": {"kind": "doodad", "type": "Storm_Doodad_DS19_Embers"}, "picture": "moving-0-0.webp"},
                   {"x": 60, "y": 20, "pixels": 900, "tile": 1, "what": {"kind": "unknown"}, "picture": "moving-1-0.webp"}],
        "report": {"structures": {}, "models": {}, "doodads": {"Storm_Doodad_DS19_Embers": {"file": "embers.m3", "particles": 2, "ribbons": 0, "lights": 0}}},
        "rubbleAtHome": {"TownCannonTowerL2|order": {"id": 1, "wait": 2.12, "hidden": "home-a-hidden.png", "shown": "home-a-shown.png", "ratio": 0.98,
                                                     "copy": "structure-1-at-30-50-rubble.png", "copyRatio": 1.01},
                         "TownTownHallL2|order": {"id": 9, "wait": 2.73, "hidden": "home-b-hidden.png", "shown": "home-b-shown.png", "ratio": 0.4}},
    }


def test_the_review_settles_what_the_numbers_settle_and_asks_the_rest():
    from heroes_capture.categories.battleground import calibration_review as review

    data = review.review_data(_measured())
    assert data["settled"] == {"spots": 1, "noSpot": [], "failedSpots": 1, "changing": 3, "structures": 4, "colourTypes": 1}
    # The narrow pass is asked about; the easy one isn't.
    assert [s["id"] for s in data["spots"]] == ["2"] and data["spots"][0]["margins"]["drawn"] == 0.625
    # The gates disagree: asked; the towers agree: settled.
    assert [(c["type"], c["changes"], c["unchanged"]) for c in data["colour"]] == [("TownGateL2", ["3"], ["4"])]
    # Each film starts on Battlefield of Eternity's wait for its kind, or where it settled.
    falls = {f["kind"]: f for f in data["falls"]}
    assert falls["TownCannonTowerL2|order"]["table"] == 2.12 and falls["TownCannonTowerL2|order"]["preset"] == 8
    assert falls["DragonShrine|order"]["table"] is None and falls["DragonShrine|order"]["preset"] == 8
    # What moves, the biggest first, with what the data says the doodad is made of.
    assert [(m["kind"], m["count"]) for m in data["motion"]] == [("unknown", 1), ("doodad", 1)]
    assert data["motion"][1]["parts"]["particles"] == 2
    # The rubble at home, the darkest first.
    assert [(h["type"], h["ratio"]) for h in data["homes"]] == [("TownTownHallL2", 0.4), ("TownCannonTowerL2", 0.98)]


def test_the_review_s_answers_make_the_reviewed_calibration():
    from heroes_capture.categories.battleground import calibration_review as review

    answers = {"submitted": True, "at": "2026-10-12T20:00:00Z", "falls": {"TownCannonTowerL2|order": 10}, "spots": {"2": "reject"},
               "colour": {"TownGateL2": True}, "motion": {"unknown:": "water"}, "notes": "fine"}
    reviewed = review.apply_review(_measured(), answers)
    assert reviewed["copySpots"] == {"1": {"x": 30, "y": 50}}  # the rejected spot's structure falls by itself
    assert reviewed["structures"]["4"]["changes"] is True  # the gates have team colour
    assert reviewed["fallWaits"] == {"TownCannonTowerL2|order": 2.5}
    assert reviewed["reviewed"]["unanswered"] == ["DragonShrine|order"] and reviewed["reviewed"]["notes"] == "fine"


def test_the_review_page_is_built_with_its_pictures(tmp_path):
    from heroes_capture.categories.battleground import calibration_review as review

    run = tmp_path / "run"
    (run / "calibration").mkdir(parents=True)
    (run / "calibration.json").write_text(json.dumps(_measured()))
    for name in ("fall-TownCannonTowerL2-order.png", "structure-2.png", "structure-2-at-70-50.png"):
        Image.fromarray(_frame()).save(run / "calibration" / name)
    Image.fromarray(_frame(radius=0)).save(run / "calibration" / "fall-DragonShrine-order.png")  # filmed nothing
    page = review.write_review(run, tmp_path / "out")
    html = page.read_text()
    assert "<title>Dragon Shire Calibration</title>" in html and "/*REVIEW*/null" not in html
    data = json.loads(html.split("const REVIEW = ", 1)[1].split(";\n", 1)[0])
    # (Its address carries its content's hash: a browser never shows one kept from before.)
    address, version = data["images"]["fall-TownCannonTowerL2-order.png"].split("?v=")
    assert address == "img/fall-TownCannonTowerL2-order.webp" and len(version) == 10
    assert data["falls"][0]["size"] > 0  # its frames' size as saved: the page never shows one bigger
    # An empty film isn't asked about.
    assert [f["kind"] for f in data["falls"]] == ["TownCannonTowerL2|order"] and data["emptyFalls"] == ["DragonShrine|order"]
    assert (tmp_path / "out" / "img" / "structure-2-at-70-50.webp").exists()
    assert json.loads((tmp_path / "out" / "answers.json").read_text()) == {"submitted": False}


def test_a_structure_s_candidates_are_its_nearest_then_others_spread_out():
    from heroes_capture.categories.battleground import specifics

    line = [(float(x), 0.0) for x in range(0, 1000, 2)]  # nearest first
    kept = specifics.spread_candidates(line)
    assert kept[:specifics.NEAR_CANDIDATES] == line[:specifics.NEAR_CANDIDATES]
    far = kept[specifics.NEAR_CANDIDATES:]
    assert len(far) == specifics.FAR_CANDIDATES and all(b[0] - a[0] >= specifics.CANDIDATE_SPACING for a, b in zip(far, far[1:]))


def test_rubble_at_home_alone_uses_the_calibration_so_far(calibrate, tmp_path):
    """Run on its own, the rubble at home stage hides the scene itself and compares with the
    copies' rubble the calibration so far recorded."""
    game = _CalibrationGame(calibrate.clock)
    # (An earlier calibration's tries: no team, no rubble look.)
    previous = {"tries": [{"id": 5, "type": "TownCannonTowerL2", "failed": None, "rubble": "structure-5-at-30-50-rubble.png"}], "falls": {}}
    found = calibrate.calibrate(game, CALIBRATION_MANIFEST, tmp_path, ["rubble-at-home"], previous)
    assert game.sent == ["tile 0 50.00 50.00", "el fadeall 0", "el env off",
                         "el at 50.00 50.00 50 50 1", "el kill 50 50 2.12", "el env on", "el env off", "el clear"]
    assert set(found) == {"report", "rubbleAtHome"}
    assert found["rubbleAtHome"]["TownCannonTowerL2|order"]["copy"] == "structure-5-at-30-50-rubble.png"


def test_the_pausing_probe_looks_at_the_biggest_of_each_kind_and_finds_what_stills_it(calibrate, tmp_path):
    """The forts' biggest moving patch looked at after each step, the water hidden: the scene hidden
    as for the standing shots, the fort lit in place."""
    from heroes_capture.categories.battleground import calibrate as c

    motion = [{"x": 50, "y": 50, "pixels": 300, "what": {"kind": "structure", "type": "TownTownHallL2", "id": 5}},
              {"x": 50, "y": 51, "pixels": 100, "what": {"kind": "structure", "type": "TownTownHallL2", "id": 5}},
              {"x": 80, "y": 80, "pixels": 90, "what": {"kind": "structure", "type": "TownMoonwellL3", "id": 6}},
              {"x": 30, "y": 30, "pixels": 900, "what": {"kind": "doodad", "type": "Trees"}},
              {"x": 20, "y": 20, "pixels": 5000, "what": {"kind": "unknown"}}]
    # The forts only (PAUSE_FOCUS).
    assert [t["pixels"] for t in c.pause_targets(motion)] == [300]
    assert c.stilled_at([0.2, 0.19, 0.01, 0.0]) == 2 and c.stilled_at([0.2, 0.2]) is None and c.stilled_at([0.0, 0.0]) is None
    assert c.away_from({"x": 10, "y": 90}, {"left": 0, "right": 100, "bottom": 0, "top": 100}) == (100, 0)

    class Game(_CalibrationGame):
        """The fort's fire moves until it is lit in place ("el light")."""

        def grab(self, dark_ok=False):
            self.clock["t"] += 0.01
            self.looks += 1
            frame = _frame()
            if self.at == (50.0, 50.0) and not any(c.startswith("el light") for c in self.sent):
                frame[40:56, 64:80] = (250, 200, 60) if self.looks % 2 else (200, 120, 30)
            return frame

    game = Game(calibrate.clock)
    found = calibrate.calibrate(game, CALIBRATION_MANIFEST, tmp_path, ["pausing"], {"motion": motion})["pausing"]
    assert game.sent[:2] == ["tile 0 50.00 50.00", "el water off"]
    assert "el light 50 50 2" in game.sent and game.sent.index("el env off") < game.sent.index("el light 50 50 2")
    away = game.sent.index("el at 0.00 0.00")  # (the fort in the middle: every corner as far)
    assert game.sent[away + 1:away + 3] == ["pause 0", "pause"]
    assert [c for c in game.sent if c.startswith("el env")] == ["el env off", "el env on"]
    [fire] = found["targets"]
    assert fire["stilledBy"] == "lit in place (el light), the scene hidden"
    assert len(fire["changed"]) == len(c.PAUSE_STEPS) and len(fire["pictures"]) == len(c.PAUSE_STEPS)
    with pytest.raises(ValueError, match="on its own"):
        calibrate.calibrate(Game(calibrate.clock), CALIBRATION_MANIFEST, tmp_path, ["motion", "pausing"], {"motion": motion})


def test_the_stacking_probe_sinks_the_copies_and_brings_each_up_in_turn(calibrate, tmp_path):
    """Copies of a fort and its nearest tower made on the fort's cell and paused, sunk two ways, the
    far plane put just below the ground, then each brought up and shot; a picture and the share
    drawn for each step."""
    manifest = json.loads(json.dumps(CALIBRATION_MANIFEST))
    manifest["elements"]["structures"].append({"id": 7, "type": "TownTownHallL2", "x": 50, "y": 60, "owner": "order", "core": False, "radius": 6.0})
    game = _CalibrationGame(calibrate.clock)
    found = calibrate.calibrate(game, manifest, tmp_path, ["stacking"], {})["stacking"]
    assert found["fort"] == 7 and found["tower"] == 5
    sent = game.sent
    assert sent[3:6] == ["el at 50.00 60.00", "el copy 0 make 50 60 50 60", "el copy 1 make 50 50 50 60"]
    assert "el copy 0 msg HostSiteOpsSet~::Host~HrsSink" in sent and "el copy 1 msg SetHeight~-30" in sent
    assert "el farclip 88" in sent and sent.index("el farclip 88") < sent.index("el copy 0 msg HostSiteOpsSet~::Host")
    assert len(found["steps"]) == 8 and all(s["picture"] for s in found["steps"])


def test_the_standing_stage_films_each_kind_from_its_restart(calibrate, tmp_path):
    """One structure of each kind on each team, faded in with the scene hidden, started over and
    filmed, then paused again: a strip per kind."""
    game = _CalibrationGame(calibrate.clock)
    found = calibrate.calibrate(game, CALIBRATION_MANIFEST, tmp_path, ["standing"], {})["standing"]
    assert sorted(found) == ["KingsCore", "TownCannonTowerL2"]
    tower = found["TownCannonTowerL2"]
    # No sequences in the stand-in report: each filmed STAND_LEAST, from the camera in closer.
    assert tower["film"] == "stand-TownCannonTowerL2.png" and tower["seconds"] == calibrate.STAND_LEAST
    assert tower["frames"] == round(calibrate.STAND_LEAST / calibrate.FILM_STEP)
    sent = game.sent
    assert sent[:3] == ["tile 0 50.00 50.00", "el fadeall 0", "el env off"]
    tower_at = sent.index("el at 50.00 50.00 80 80 0 50 50 1")
    # (No model bounds in the stand-in report: the camera stays at the capture's distance.)
    assert sent[tower_at + 1:tower_at + 4] == ["el wide 50.00 50.00 85", "el restart 50 50", "el hold 50 50"]
    assert (tmp_path / "calibration" / "stand-KingsCore.png").exists()


def test_the_review_asks_each_kind_s_standing_time_and_applies_it():
    from heroes_capture.categories.battleground import calibration_review as review

    measured = _measured()
    measured["standing"] = {"TownCannonTowerL3": {"id": 2, "film": "stand-TownCannonTowerL3.png", "step": 0.25, "frames": 24, "seconds": 9.5}}
    data = review.review_data(measured)
    assert [(f["kind"], f["preset"]) for f in data["standing"]] == [("TownCannonTowerL3", 8)]  # elements.LIGHT_WAIT
    assert "stand-TownCannonTowerL3.png" in review.pictures_of(data)
    reviewed = review.apply_review(measured, {"submitted": True, "standing": {"TownCannonTowerL3": 14}})
    assert reviewed["standWaits"] == {"TownCannonTowerL3": 3.5}
    # A kind whose film was empty wasn't asked about: an answer left from an earlier review is ignored.
    assert review.apply_review(measured, {"submitted": True, "standing": {"TownCannonTowerL3": 14}}, ([], ["TownCannonTowerL3"]))["standWaits"] == {}


def test_a_standing_film_runs_as_long_as_its_animations_take():
    """The finished model's longest standing sequence (its build model's left out: the restart
    skips the build) and a margin; STAND_LEAST at least, STAND_MOST at most."""
    from heroes_capture.categories.battleground import calibrate as c

    report = {"standModels": {"TownTownHallL3": "Keep", "TownCannonTowerL3": "Tower"},
              "models": {"KeepBirth": {"sequences": [{"name": "Stand Morph Start", "seconds": 6.667, "loops": False}]},
                         "Keep": {"sequences": [{"name": "Stand", "seconds": 6.667, "loops": True}]},
                         "Tower": {"sequences": [{"name": "Stand", "seconds": 1.0, "loops": True},
                                                 {"name": "Attack", "seconds": 0.666, "loops": False}]}}}
    assert c.stand_seconds("TownTownHallL3", report) == 6.667 + c.STAND_MARGIN
    assert c.stand_seconds("TownCannonTowerL3", report) == c.STAND_LEAST  # a 1 s loop and the margin: the least
    assert c.stand_seconds("TownGateL2", report) == c.STAND_LEAST  # no finished model known
    assert c.thinned(list(range(100)), 48) == (list(range(0, 100, 3)), 3)
    assert c.thinned(list(range(30)), 48) == (list(range(30)), 1)


def test_a_model_file_s_animation_sequences_are_read():
    import struct

    from heroes_capture.common import actors

    def entry(name_index, start, end, flags):
        return struct.pack("<ii", -1, -1) + struct.pack("<III", 6, name_index, 0) + struct.pack("<II", start, end) + struct.pack("<fI", 0.0, flags) + bytes(92 - 36)
    seqs = entry(2, 0, 1000, 0) + entry(3, 1000, 7667, 1)
    names = [b"Stand\0", b"Morph\0"]
    body = bytearray(16)
    at_seqs = len(body); body += seqs
    at_names = []
    for n in names:
        while len(body) % 16: body += b"\0"
        at_names.append(len(body)); body += n
    while len(body) % 16: body += b"\0"
    index_at = len(body)
    index = [(b"43DM", 0, 1, 11), (b"SEQS"[::-1], at_seqs, 2, 2), (b"RAHC", at_names[0], len(names[0]), 0), (b"RAHC", at_names[1], len(names[1]), 0)]
    for tag, at, count, version in index:
        body += struct.pack("<4sIII", tag, at, count, version)
    body[0:12] = b"43DM" + struct.pack("<II", index_at, len(index))
    assert actors.m3_sequences(bytes(body)) == [{"name": "Stand", "seconds": 1.0, "loops": True},
                                                 {"name": "Morph", "seconds": 6.667, "loops": False}]


def test_a_model_file_s_mesh_extent_is_read_from_its_vertices():
    """Its regions give how many vertices; the byte array they fill holds them (each starting with
    its position): the farthest across the ground, the highest."""
    import struct

    from heroes_capture.common import actors

    stride = 36
    points = [(-2.4, -2.88, 0.0), (2.4, 2.1, 6.61), (0.0, 0.0, 3.0)]
    vertices = b"".join(struct.pack("<3f", *p) + bytes(stride - 12) for p in points)
    regions = (bytes(8) + struct.pack("<II", 0, 2) + bytes(48 - 16)) + (bytes(8) + struct.pack("<II", 2, 1) + bytes(48 - 16))
    body = bytearray(16)
    at_vertices = len(body); body += vertices
    while len(body) % 16: body += b"\0"
    at_regions = len(body); body += regions
    index_at = len(body)
    index = [(b"43DM", 0, 1, 11), (b"__8U", at_vertices, len(vertices), 0), (b"NGER", at_regions, 2, 5)]
    for tag, at, count, version in index:
        body += struct.pack("<4sIII", tag, at, count, version)
    body[0:12] = b"43DM" + struct.pack("<II", index_at, len(index))
    assert actors.m3_extent(bytes(body)) == {"reach": round(math.hypot(2.4, 2.88), 3), "height": 6.61}
    assert actors.m3_extent(b"") is None


def test_a_model_s_size_is_its_own_or_its_parent_s_largest_scale():
    from heroes_capture.common import actors

    catalog = actors.Catalog(['<Catalog><CModel id="Fort"><Model value="fort.m3"/>'
                              '<ScaleMax X="0.9" Y="0.9" Z="0.8"/></CModel>'
                              '<CModel id="FortRed" parent="Fort"/><CModel id="Wall"/>'
                              '<CModel id="Crypt"><ScaleMax value="0.937000,0.937000,0.937000"/></CModel></Catalog>'])
    assert catalog.model_scale("Fort") == 0.9 and catalog.model_scale("FortRed") == 0.9
    assert catalog.model_scale("Wall") == 1.0
    assert catalog.model_scale("Crypt") == 0.937  # written as one value


def test_the_camera_comes_in_as_far_as_the_model_still_fits():
    """From the data: the finished model's mesh extent, at its size, through the capture's lens (a
    point r cells out and z up shows r·f/(d−z) pixels from the middle, f = pxPerCell·distance)."""
    from heroes_capture.categories.battleground import calibrate as c

    report = {"standModels": {"TownTownHallL2": "Fort", "TownGateL2": "Gate", "TownMoonwellL2": "Well"},
              "models": {"Fort": {"scale": 0.9, "extent": {"reach": 3.0, "height": 6.5}},
                         "Gate": {"scale": 1.0, "extent": {"reach": 0.5, "height": 1.0}},
                         "Well": {"scale": 1.0, "extent": None}}}
    reach, height = c.stand_extent("TownTownHallL2", report)
    assert (reach, height) == pytest.approx((2.7, 5.85))
    manifest = {"pxPerCell": 4, "distance": 85.0, "screen": {"w": 160, "h": 120}}
    distance = c.stand_distance((reach, height), manifest)
    # Its farthest point, at its top, lands STAND_FIT of the way to the screen's top or bottom.
    assert reach * 4 * 85 / (distance - height) == pytest.approx(c.STAND_FIT * 60)
    assert c.stand_distance(c.stand_extent("TownGateL2", report), manifest) == 85 / c.STAND_CLOSER  # small: in all the way
    assert c.stand_extent("TownMoonwellL2", report) is None and c.stand_distance(None, manifest) == 85  # not known: the capture's
    v0, v1, u0, u1 = c.stand_crop((reach, height), manifest, distance, (80, 60), (120, 160, 3))
    assert (v0, v1) == (0, 120) and (u0, u1) == (18, 142)  # the model and room for effects past it: the screen's height


def test_a_film_keeps_its_frames_as_shot_in_rows(tmp_path):
    """Each frame at the size shot (FILM_MOST at most), FILM_COLUMNS to a row."""
    from PIL import Image

    from heroes_capture.categories.battleground import calibrate as c

    small = [np.full((300, 300, 3), n, np.uint8) for n in range(c.FILM_COLUMNS + 3)]
    assert c._film_strip(tmp_path, "small", small) == "small.png"
    picture = Image.open(tmp_path / "small.png")
    assert picture.size == (300 * c.FILM_COLUMNS, 600)
    assert picture.getpixel((300 * 2 + 5, 300 + 5)) == (c.FILM_COLUMNS + 2,) * 3  # the 11th frame: row 2, column 3
    c._film_strip(tmp_path, "big", [np.zeros((1400, 1400, 3), np.uint8)] * 3)
    assert Image.open(tmp_path / "big.png").size == (c.FILM_MOST * 3, c.FILM_MOST)
    assert c.film_columns(3) == 3 and c.film_columns(40) == c.FILM_COLUMNS


def test_a_kind_s_rubble_circle_is_measured_from_its_debris(tmp_path):
    """At the moment picked, in its fall film (a saved pixel's cells and the structure's place in
    it recorded); a kind without a film, in its rubble shot at home; the furthest for the type, with
    room. Debris at the edge of what was shot is reported."""
    from PIL import Image

    from heroes_capture.categories.battleground import calibration_review as review

    (tmp_path / "calibration").mkdir()
    sky = (200, 200, 200)
    strip = np.full((100, 200, 3), sky, np.uint8)  # two 100 px frames in a row
    strip[48:52, 100 + 80:100 + 84] = (60, 40, 30)  # frame 2: a chunk 30-34 px right of the middle
    Image.fromarray(strip).save(tmp_path / "calibration" / "fall-T-order.png")
    home = np.full((60, 60, 3), sky, np.uint8)
    home[30, 59] = (60, 40, 30)  # on the picture's last column
    Image.fromarray(home).save(tmp_path / "calibration" / "home-K-chaos-hidden.png")
    calibration = {"pxPerCell": 10.0, "falls": {"T|order": {"film": "fall-T-order.png", "frames": 2, "columns": 2,
                                                             "centre": [50, 50], "cellsPerPixel": 0.2}},
                   "rubbleAtHome": {"K|chaos": {"hidden": "home-K-chaos-hidden.png", "centre": [30, 30], "cellsPerPixel": 0.1}}}
    radii, edged = review.rubble_radii(tmp_path, calibration, {"falls": {"T|order": 1}})
    assert radii["T|order"] == pytest.approx(math.hypot(33, 1.5) * 0.2 * review.RUBBLE_ROOM, abs=0.05)
    assert radii["K|chaos"] == pytest.approx(29 * 0.1 * review.RUBBLE_ROOM, abs=0.05) and edged == ["K|chaos"]
    assert review.rubble_radii(tmp_path, calibration, {"falls": {"T|order": 0}})[0].get("T|order") is None  # nothing there then
    # A type's kinds share its furthest reach (each fall lands differently).
    calibration["rubbleAtHome"]["T|chaos"] = calibration["rubbleAtHome"].pop("K|chaos")
    radii = review.rubble_radii(tmp_path, calibration, {"falls": {"T|order": 1}})[0]
    assert radii["T|order"] == radii["T|chaos"] == pytest.approx(math.hypot(33, 1.5) * 0.2 * review.RUBBLE_ROOM, abs=0.05)


def test_a_structure_s_rubble_circle_is_its_kind_s_measured_one():
    from heroes_capture.categories.battleground import elements

    assert elements.rubble_radius({"type": "TownWallRadial2L3", "radius": 8, "rubbleRadius": 6.9}) == 6.9
    assert elements.rubble_radius({"type": "TownWallRadial2L3", "radius": 8}) == 6.0  # not measured: RUBBLE_RADII


def test_a_copy_that_fell_to_nothing_is_no_copy_spot():
    """Its rubble covering nothing (an earlier calibration passed it): the structure falls itself."""
    from heroes_capture.categories.battleground import calibration_review as review

    measured = _measured()
    measured["copySpots"] = {"7": {"x": 10, "y": 10}, "8": {"x": 20, "y": 20}}
    measured["tries"] = [{"id": 7, "type": "T", "x": 10, "y": 10, "failed": None, "rubbleLook": {"drawn": 0.0, "mean": [0, 0, 0], "dark": 0.0}},
                         {"id": 8, "type": "T", "x": 20, "y": 20, "failed": None, "rubbleLook": {"drawn": 0.0053, "mean": [9, 9, 9], "dark": 0.0}}]
    assert set(review.apply_review(measured, {"submitted": True})["copySpots"]) == {"8"}


def test_a_structure_s_side_change_is_taken_from_its_model_s_data(calibrate, tmp_path):
    """Its finished model's team colour in the data report (common/m3.py): no switch to Chaos's side
    and back to measure it (the tower's here); recorded as from the data."""
    manifest = json.loads(json.dumps(CALIBRATION_MANIFEST))
    manifest["elements"]["report"] = {"structures": {}, "doodads": {}, "standModels": {"TownCannonTowerL2": "Tower"},
                                      "models": {"Tower": {"teamColour": {"Stand": True}}}}
    game = _CalibrationGame(calibrate.clock)
    found = calibrate.calibrate(game, manifest, tmp_path)
    tower = game.sent.index("el at 50.00 50.00 50 50 1")
    assert game.sent[tower + 1] == "el fades 50 50 0"  # no side switches for it
    measured = found["structures"]["5"]
    assert measured["changes"] is True and measured["fromData"] is True and measured["toChaos"] is None
