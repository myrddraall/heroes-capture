"""Job files (heroes_capture/job.py): reading them, a job's progress, and what a run of some parts
removes from a kept run."""

import json

import pytest

from heroes_capture import job


def write(tmp_path, text):
    path = tmp_path / "job.yml"
    path.write_text(text)
    return path


def test_a_job_file_s_runs_are_read_and_checked(tmp_path):
    runs = job.load(write(tmp_path, """
runs:
  - map: Dragon Shire
    render: elements
  - map: Dragon Shire
    render: elements
    only: [tiles, rubble]
  - map: Battlefield of Eternity
    render: keep
    only: skies
  - map: Dragon Shire
    calibrate: [standing]
  - map: Dragon Shire
    calibrate: all
"""))
    assert [(r.map, r.render, r.only, r.calibrate) for r in runs] == [
        ("Dragon Shire", "elements", [], None), ("Dragon Shire", "elements", ["rubble", "tiles"], None),  # (in the parts' order)
        ("Battlefield of Eternity", "keep", ["skies"], None), ("Dragon Shire", None, [], ["standing"]), ("Dragon Shire", None, [], [])]
    assert runs[1].describe() == "Dragon Shire: elements render, only rubble, tiles"
    assert runs[3].describe() == "Dragon Shire: calibrate standing" and runs[4].describe() == "Dragon Shire: calibrate all"


@pytest.mark.parametrize("text, says", [
    ("runs: []", "a list of runs"),
    ("runs:\n  - render: keep", "names its map"),
    ("runs:\n  - map: X", "a render"),
    ("runs:\n  - map: X\n    render: keep\n    calibrate: [a]", "a render"),
    ("runs:\n  - map: X\n    render: some", "render is one of"),
    ("runs:\n  - map: X\n    render: keep\n    only: [roofs]", "only takes some of"),
    ("runs:\n  - map: X\n    render: keep\n    only: [rubble]", "an elements render's"),
    ("runs:\n  - map: X\n    render: keep\n    colour: red", "no setting colour"),
    ("runs: [", "isn't valid YAML"),
])
def test_a_job_file_that_can_t_run_says_why(tmp_path, text, says):
    with pytest.raises(job.JobError, match=says):
        job.load(write(tmp_path, text))


def test_a_job_s_progress_is_resumed_only_for_the_same_file(tmp_path):
    path = write(tmp_path, "runs:\n  - map: X\n    render: keep\n")
    work = tmp_path / "tmp"
    with pytest.raises(job.JobError, match="no stopped job"):
        job.resumed(work, path)
    state = job.started(work, path)
    state["done"].append(0)
    job.save(work, state)
    assert job.resumed(work, path)["done"] == [0]
    path.write_text("runs:\n  - map: Y\n    render: keep\n")
    with pytest.raises(job.JobError, match="has changed since"):
        job.resumed(work, path)


def test_the_capture_parts_a_render_s_parts_need():
    assert job.capture_parts(["rubble"]) == ["elements"]
    assert job.capture_parts(["tiles", "standing", "skies"]) == ["skies", "elements", "tiles"]


def test_a_run_of_some_parts_removes_only_what_it_reshoots(tmp_path):
    run = tmp_path / "run"
    (run / "tiles").mkdir(parents=True)
    (run / "sky").mkdir()
    (run / "elements").mkdir()
    for name in ("tiles/tile_0000.npy", "tiles/tile_0001.npy", "sky/s.npy", "sky-layers.json", "positions.json"):
        (run / name).write_text("x")
    records = {"structure-5-standing": {"kind": "structure", "state": "standing", "shot": "structure-5-standing"},
               "structure-5-rubble": {"kind": "structure", "state": "rubble", "shot": "structure-5-rubble"},
               "structure-55-rubble": {"kind": "structure", "state": "rubble", "shot": "structure-55-rubble"},
               "camp-1-spawned": {"kind": "camp", "state": "spawned", "shot": "camp-1-spawned"}}
    (run / "elements" / "elements.json").write_text(json.dumps(records))
    for key in records:
        for suffix in ("", "-black", "-chaos", "-chaos-black"):
            (run / "elements" / f"{key}{suffix}.npy").write_text("x")
    removed = job.clear(run, ["rubble"])
    assert removed == {"rubble": 2}
    left = json.loads((run / "elements" / "elements.json").read_text())
    assert set(left) == {"structure-5-standing", "camp-1-spawned"}
    assert not list((run / "elements").glob("structure-5-rubble*")) and len(list((run / "elements").glob("structure-5-standing*"))) == 4
    assert (run / "tiles" / "tile_0000.npy").exists() and (run / "sky-layers.json").exists()
    assert job.clear(run, ["tiles", "skies", "camps"]) == {"skies": 1, "tiles": 2, "camps": 1}
    assert not list((run / "tiles").iterdir()) and not (run / "positions.json").exists() and not (run / "sky").exists()
