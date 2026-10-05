"""The heroes-capture command: its commands and help, and map list's statuses."""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heroes_capture import cli

runner = CliRunner()


def test_version_and_help():
    assert runner.invoke(cli.app, ["--version"]).output.strip() == cli.version()
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("map", "prepare", "capture", "stitch"):
        assert command in result.output
    assert "self-check" not in result.output  # hidden
    result = runner.invoke(cli.app, ["map", "--help"])
    assert "render" in result.output and "list" in result.output


def test_prepare_passes_its_options_through(monkeypatch, tmp_path):
    from heroes_capture import inject

    seen = []
    monkeypatch.setattr(inject, "main", lambda argv: seen.append(argv) or tmp_path / "x.json")
    result = runner.invoke(cli.app, ["prepare", "Dragon Shire", "--screen", "3440x1440", "--keep-intro"])
    assert result.exit_code == 0 and seen == [["Dragon Shire", "--screen", "3440x1440", "--keep-intro"]]


def test_the_steps_on_their_own_show_as_steps(monkeypatch, tmp_path):
    """capture and stitch run in a step (the live view): a capture restarted after a lost match runs
    as `capture` in its own process, and showed no progress without one."""
    from heroes_capture import stitch

    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "m.json").write_text(json.dumps({"map": "Punisher Arena"}))
    monkeypatch.setattr(cli, "run_capture", lambda argv: None)
    monkeypatch.setattr(stitch, "main", lambda argv: None)
    monkeypatch.setenv("HRS_RECOVERIES", "1")
    result = runner.invoke(cli.app, ["--log", "capture", "tmp/m.json", "--start", "86"])
    assert result.exit_code == 0 and "\nCapturing Punisher Arena in the game (restart 1 of 3)\n" in result.output
    result = runner.invoke(cli.app, ["--log", "stitch", "tmp/m.json"])
    assert result.exit_code == 0 and "\nStitching Punisher Arena\n" in result.output


def test_prepare_help_lists_its_options(tmp_path):
    result = runner.invoke(cli.app, ["prepare", "--help"])
    assert result.exit_code == 0 and "--px-per-cell" in result.output and "--crop-margin" in result.output
    assert not (tmp_path / "tmp").exists() and not (tmp_path / "logs").exists()


@pytest.fixture
def ui_state():
    """The output module's state put back after a test that changes it by hand (a log file left
    closed, or the live view's mode, would break the next test that prints)."""
    from heroes_capture import ui

    saved = dict(vars(ui._s))
    yield
    if ui._s.plain and ui._s.plain is not saved.get("plain"):
        ui._s.plain.close()
    for name in [n for n in vars(ui._s) if n not in saved]:
        delattr(ui._s, name)
    for name, value in saved.items():
        setattr(ui._s, name, value)


@pytest.fixture(autouse=True)
def in_a_scratch_folder(monkeypatch, tmp_path):
    """Commands write tmp\\ and maps\\ in the current folder."""
    monkeypatch.chdir(tmp_path)


GAME_MAPS = {"Dragon Shire": "Battleground", "Cursed Hollow": "Battleground", "Punisher Arena": "Arena", "Pull Party": "Brawl"}
TILES = 10


def render_steps(monkeypatch, tmp_path, fail_stitch=(), fail_capture=None):
    """map render against a stand-in game (GAME_MAPS, and Try Me Mode unsupported) with its three
    steps stood in for: prepare writes the working files the real one would (manifest, prepared
    map, folder), capture the screenshots (stopping before tile n of a map in fail_capture), and
    stitch the map's folder with its viewer (failing for the maps in fail_stitch)."""
    from contextlib import nullcontext

    from heroes_capture import game_data, inject, stitch

    calls = []
    monkeypatch.setattr(game_data, "find_install", lambda: None)
    monkeypatch.setattr(game_data, "open_storage", lambda install: nullcontext())
    monkeypatch.setattr(game_data, "map_index", lambda storage: {n: {"file": n, "category": c} for n, c in GAME_MAPS.items()})
    monkeypatch.setattr(game_data, "folder_maps", lambda storage: ["Try Me Mode"])

    def prepare(argv):
        calls.append(("prepare", argv))
        work = tmp_path / argv[argv.index("--out") + 1]
        map_id = inject.render_id(argv[0], argv[argv.index("--structures") + 1])
        (work / map_id).mkdir(parents=True, exist_ok=True)
        (work / f"{map_id}.stormmap").write_text("map")
        manifest = work / f"{map_id}.json"
        manifest.write_text(json.dumps({"map": argv[0], "id": map_id, "tiles": [{"index": i} for i in range(TILES)], "sky": {"mode": "black"}}))
        return manifest.resolve()

    def capture(argv):
        calls.append(("capture", argv))
        manifest = json.loads(Path(argv[0]).read_text())
        tiles = Path(argv[0]).with_suffix("") / "tiles"
        tiles.mkdir(parents=True, exist_ok=True)
        start = int(argv[argv.index("--start") + 1]) if "--start" in argv else 0
        for i in range(start, TILES):
            if (fail_capture or {}).get(manifest["map"]) == i:
                raise RuntimeError(f"the match was lost at tile {i + 1}")
            (tiles / f"tile_{i:04d}.npy").write_bytes(b"shot")

    def stitching(argv):
        calls.append(("stitch", argv))
        manifest = json.loads(Path(argv[0]).read_text())
        if manifest["map"] in fail_stitch:
            raise RuntimeError("stitch failed")
        out = tmp_path / argv[argv.index("--output-dir") + 1] / inject.slug(manifest["map"])
        (out / "pack").mkdir(parents=True, exist_ok=True)
        (out / "pack" / "pack.json").write_text("{}")

    monkeypatch.setattr(inject, "main", prepare)
    monkeypatch.setattr(cli, "run_capture", capture)
    monkeypatch.setattr(stitch, "main", stitching)
    return calls


def test_map_render_runs_the_three_steps_with_the_defaults(monkeypatch, tmp_path):
    calls = render_steps(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["map", "render", "dragon shire", "--structures", "hide", "--fov", "12"])
    assert result.exit_code == 0, result.output
    manifest = str((tmp_path / "tmp" / "dragon-shire-terrain.json").resolve())
    assert calls[0] == ("prepare", ["Dragon Shire", "--structures", "hide", "--fov", "12", "--out", "tmp", "--keep", "0.4"])
    assert calls[1] == ("capture", [manifest])
    assert calls[2] == ("stitch", [manifest, "--output-dir", "maps"])
    assert "Dragon Shire (hide structures): 10 tiles planned, black void" in result.output
    assert "not validated" not in result.output  # Dragon Shire is
    assert (tmp_path / "maps" / "dragon-shire" / "pack" / "pack.json").exists()
    assert "Dragon Shire: the map is in maps" in result.output
    assert not (tmp_path / "tmp").exists()  # the working files removed, and their folder
    plain = (tmp_path / "logs" / "heroes-capture.log").read_text()  # the plain log stays
    assert plain.startswith("===== ") and "\nPreparing Dragon Shire\n" in plain and "10 tiles planned" in plain
    assert "\x1b[" not in plain


def test_options_before_the_map_keep_their_values(monkeypatch, tmp_path):
    calls = render_steps(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["map", "render", "--fov", "12", "--paint-texture", "sky", "clear", "dragon shire"])
    assert result.exit_code == 0, result.output
    assert calls[0] == ("prepare", ["Dragon Shire", "--structures", "keep", "--fov", "12", "--paint-texture", "sky", "clear",
                                    "--out", "tmp", "--keep", "0.4"])


def test_a_misspelled_or_unsupported_map_stops_before_writing_anything(monkeypatch, tmp_path):
    render_steps(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["map", "render", "Dragn Shire"])
    assert result.exit_code != 0 and "did you mean Dragon Shire?" in result.output
    assert "working files" not in result.output
    result = runner.invoke(cli.app, ["map", "render", "try me mode"])
    assert result.exit_code != 0 and "Try Me Mode is unsupported" in result.output
    assert not (tmp_path / "tmp").exists() and not (tmp_path / "logs").exists()


def test_map_render_takes_a_map_or_a_category(monkeypatch, tmp_path):
    render_steps(monkeypatch, tmp_path)
    assert runner.invoke(cli.app, ["map", "render"]).exit_code == 2
    assert runner.invoke(cli.app, ["map", "render", "dragon shire", "--category", "arena"]).exit_code == 2


def test_map_render_keeps_the_working_files_when_asked(monkeypatch, tmp_path):
    render_steps(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["map", "render", "dragon shire", "--keep-tmp", "-o", "renders"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "renders" / "dragon-shire" / "pack" / "pack.json").exists()
    for name in ("dragon-shire-structures.json", "dragon-shire-structures.stormmap", "dragon-shire-structures"):
        assert (tmp_path / "tmp" / name).exists(), name
    assert len(list((tmp_path / "tmp").glob("heroes-capture-*.log"))) == 1


def test_a_failed_map_render_leaves_the_working_files(monkeypatch, tmp_path):
    render_steps(monkeypatch, tmp_path, fail_stitch={"Dragon Shire"})
    result = runner.invoke(cli.app, ["--log", "map", "render", "dragon shire"])
    assert result.exit_code != 0
    assert (tmp_path / "tmp" / "dragon-shire-structures.json").exists() and list((tmp_path / "tmp").glob("heroes-capture-*.log"))
    assert "heroes-capture clean-up removes them" in result.output


def test_a_later_run_leaves_a_failed_runs_diagnostic_log(monkeypatch, tmp_path):
    render_steps(monkeypatch, tmp_path, fail_stitch={"Dragon Shire"})
    assert runner.invoke(cli.app, ["map", "render", "dragon shire"]).exit_code != 0
    [failed] = (tmp_path / "tmp").glob("heroes-capture-*.log")
    render_steps(monkeypatch, tmp_path)
    assert runner.invoke(cli.app, ["map", "render", "dragon shire"]).exit_code == 0
    assert failed.exists() and "left in tmp for diagnosis" in failed.read_text()  # its own was removed, not this one
    assert list((tmp_path / "tmp").glob("heroes-capture-*.log")) == [failed]


def test_a_rendered_map_is_skipped_unless_forced(monkeypatch, tmp_path):
    calls = render_steps(monkeypatch, tmp_path)
    assert runner.invoke(cli.app, ["map", "render", "dragon shire"]).exit_code == 0
    calls.clear()
    result = runner.invoke(cli.app, ["map", "render", "dragon shire"])
    assert result.exit_code == 0 and "Dragon Shire: already rendered in maps" in result.output and calls == []
    result = runner.invoke(cli.app, ["map", "render", "dragon shire", "--force"])
    assert result.exit_code == 0 and [c[0] for c in calls] == ["prepare", "capture", "stitch"]


def test_a_stopped_capture_carries_on_from_its_screenshots(monkeypatch, tmp_path):
    render_steps(monkeypatch, tmp_path, fail_capture={"Dragon Shire": 6})
    assert runner.invoke(cli.app, ["map", "render", "dragon shire"]).exit_code != 0
    calls = render_steps(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["map", "render", "dragon shire"])
    assert result.exit_code == 0, result.output
    manifest = str((tmp_path / "tmp" / "dragon-shire-structures.json").resolve())
    assert calls[0] == ("capture", [manifest, "--start", "4"])  # tile 7 lost; the two before it may be half-written
    assert "carrying on from the run left in tmp (4 of 10 tiles kept)" in result.output
    assert [c[0] for c in calls] == ["capture", "stitch"]
    left = sorted(p.name for p in (tmp_path / "tmp").iterdir())  # the stopped run's diagnostic log stays, for clean-up
    assert len(left) == 1 and left[0].startswith("heroes-capture-")


def test_a_kept_run_with_other_options_starts_again(monkeypatch, tmp_path):
    render_steps(monkeypatch, tmp_path, fail_stitch={"Dragon Shire"})
    assert runner.invoke(cli.app, ["map", "render", "dragon shire"]).exit_code != 0
    calls = render_steps(monkeypatch, tmp_path)
    assert runner.invoke(cli.app, ["map", "render", "dragon shire", "--fov", "12"]).exit_code == 0
    assert [c[0] for c in calls] == ["prepare", "capture", "stitch"]


def test_a_category_run_goes_on_past_a_failure_and_carries_on_where_it_left_off(monkeypatch, tmp_path):
    calls = render_steps(monkeypatch, tmp_path, fail_stitch={"Cursed Hollow"})
    result = runner.invoke(cli.app, ["--log", "map", "render", "--category", "all"])
    assert result.exit_code == 1, result.output
    assert [c[1][0] for c in calls if c[0] == "prepare"] == ["Cursed Hollow", "Dragon Shire", "Punisher Arena"]
    assert "3 maps to render (all), 2 of them validated; 1 unsupported left out" in result.output  # Pull Party: a brawl not validated
    assert "Cursed Hollow (keep structures): 10 tiles planned, black void; not validated: it may not come out right" in result.output
    assert "2 rendered, 0 already rendered, 1 failed" in result.output and "failed: Cursed Hollow." in result.output
    calls = render_steps(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["--log", "map", "render", "--category", "all"])
    assert result.exit_code == 0, result.output
    assert [c[0] for c in calls] == ["stitch"]  # Cursed Hollow's screenshots were all there; the rest done
    assert "1 rendered, 2 already rendered, 0 failed" in result.output
    assert list((tmp_path / "tmp").glob("*.json")) == []


def test_a_category_run_renders_only_that_categorys_supported_maps(monkeypatch, tmp_path):
    calls = render_steps(monkeypatch, tmp_path)
    assert runner.invoke(cli.app, ["map", "render", "-c", "arena"]).exit_code == 0
    assert [c[1][0] for c in calls if c[0] == "prepare"] == ["Punisher Arena"]
    result = runner.invoke(cli.app, ["map", "render", "-c", "brawl"])
    assert result.exit_code == 0 and "0 maps to render (brawl), 0 of them validated; 1 unsupported left out" in result.output


def test_an_unsupported_brawl_renders_when_named_with_a_warning(monkeypatch, tmp_path):
    calls = render_steps(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["--log", "map", "render", "pull party"])
    assert result.exit_code == 0, result.output
    assert "warning: Pull Party is unsupported: brawl maps each need handling of their own" in result.output
    assert [c[0] for c in calls] == ["prepare", "capture", "stitch"]


def test_a_restarted_capture_carries_on_in_its_runs_logs(monkeypatch, tmp_path):
    from heroes_capture import ui

    monkeypatch.setenv("HRS_DIAG_LOG", str(tmp_path / "tmp" / "heroes-capture-run.log"))
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "heroes-capture.log").write_text("===== the run's dated line\n")
    cli.log_to(tmp_path / "tmp")
    ui.done("tile 9 again")
    assert ui.log_file() == tmp_path / "tmp" / "heroes-capture-run.log"
    assert (tmp_path / "logs" / "heroes-capture.log").read_text() == "===== the run's dated line\ntile 9 again\n"


def test_the_clean_up_command_removes_only_what_the_tool_wrote(tmp_path):
    tmp = tmp_path / "tmp"
    (tmp / "dragon-shire-terrain" / "tiles").mkdir(parents=True)
    (tmp / "dragon-shire-terrain" / "tiles" / "tile_0001.npy").write_bytes(b"x" * 1000)
    (tmp / "dragon-shire-terrain.json").write_text(json.dumps({"id": "dragon-shire-terrain", "tiles": []}))
    (tmp / "dragon-shire-terrain.stormmap").write_text("map")
    (tmp / "cursed-hollow-structures.stormmap").write_text("map")  # a preparation that failed before its manifest
    (tmp / "heroes-capture-20261004-120000.log").write_text("run")
    (tmp / "heroes-capture.log").write_text("an older version's run")
    (tmp / "notes.json").write_text(json.dumps({"mine": True}))  # not the tool's
    (tmp / "setup.log").write_text("pip")
    result = runner.invoke(cli.app, ["clean-up"])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in tmp.iterdir()) == ["notes.json", "setup.log"]
    assert "1 render" in result.output and "left alone" in result.output
    (tmp / "notes.json").unlink()
    (tmp / "setup.log").unlink()
    (tmp / "heroes-capture-20261004-130000-2.log").write_text("run")
    assert runner.invoke(cli.app, ["clean-up"]).exit_code == 0
    assert not tmp.exists()  # emptied, so removed
    assert "Nothing to clean up" in runner.invoke(cli.app, ["clean-up"]).output


def test_clean_up_leaves_other_files_in_tmp(tmp_path):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "setup.log").write_text("pip")
    (tmp_path / "tmp" / "heroes-capture.log").write_text("run")
    cli.clean_up([tmp_path / "tmp" / "heroes-capture.log"], keep=False)
    assert [p.name for p in (tmp_path / "tmp").iterdir()] == ["setup.log"]


def test_map_rows():
    validated = {"Dragon Shire": {"version": "0.1.0", "note": "void"}, "Old Map": {"version": "0.1.0", "note": ""}}
    game_maps = {"Towers of Doom": "Battleground", "dragon cave": "Battleground", "Dragon Shire": "Battleground",
                 "Pull Party": "Brawl", "Sandbox (Cursed Hollow)": "Other"}
    assert cli.map_rows(game_maps, ["Try Me Mode"], validated) == [
        ("Battleground", "dragon cave", "not yet"),
        ("Battleground", "Dragon Shire", "validated"),
        ("Battleground", "Towers of Doom", "not yet"),
        ("Brawl", "Pull Party", "unsupported"),  # a brawl, not validated
        ("Other", "Sandbox (Cursed Hollow)", "not yet"),
        ("Other", "Try Me Mode", "unsupported"),
        (cli.GONE, "Old Map", "not in the game"),
    ]


@pytest.mark.parametrize("dependencies, category", [
    (["Mods\\heroesmapmods/battlegroundmapmods/alteracpass.stormmod"], "Battleground"),
    (["Mods\\HeroesMapMods\\BattlegroundMapMods\\Hanamura.StormMod"], "Battleground"),
    (["Mods\\heroesbrawlmods/arenamodemods/punisherarena.stormmod"], "Arena"),
    (["Mods\\heroesbrawlmods\\brawlmapmods\\onelane\\braxisoutpost.stormmod", "Mods\\heroesbrawlmods/heroselectionmods/ingameheroselection.stormmod"], "Brawl"),
    (["Mods/HeroesData.StormMod", "Mods\\heroesbrawlmods/mutatormods/snowbrawl-ext.stormmod"], "Brawl"),
    (["Mods\\heroesmapmods/battlegroundmapmods/sandbox-ext.stormmod", "Mods\\heroesmapmods/battlegroundmapmods/cursedhollow.stormmod"], "Other"),
    (["Mods/HeroesData.StormMod"], "Other"),
])
def test_map_category(dependencies, category):
    from heroes_capture.game_data import map_category

    assert map_category(dependencies) == category


def test_validated_maps_file():
    maps = json.loads(cli.VALIDATED.read_text(encoding="utf-8"))["maps"]
    assert {"Battlefield of Eternity", "Dragon Shire", "Punisher Arena"} <= set(maps)
    assert all(set(entry) == {"version", "note"} for entry in maps.values())


@pytest.mark.game_data
@pytest.mark.xdist_group("cdn")
def test_map_list_against_the_game():
    result = runner.invoke(cli.app, ["map", "list"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "✓ validated" in result.output and "Battlefield of Eternity" in result.output and "Alterac Pass" in result.output
    for category in ("Battleground", "Arena", "Brawl", "Other"):
        assert category in result.output
    assert "Try Me Mode" in result.output and "unsupported" in result.output
    assert "Garden Arena" in result.output  # an arena not validated: unsupported, still listed
    assert re.search(r"\d+ of \d+ maps validated; \d+ unsupported", result.output)


def test_the_plain_log_is_what_log_mode_prints_in_either_mode(tmp_path, ui_state):
    """logs/heroes-capture.log reads the same whether the screen showed the live view or log lines."""
    from heroes_capture import ui

    def run(mode: str) -> list[str]:
        ui.configure(log=True)
        ui._s.mode = mode  # pretty can't be picked on a test's (non-terminal) output
        ui.set_plain_log(tmp_path / f"{mode}.log")
        with ui.step("Capturing"):
            ui.info("tile 1/2")
            ui.warn("focus lost")
            ui.detail("hidden without --verbose")
            print("a library's line")
        ui.done("2 screenshots")
        ui.show("1 of 2 maps validated")
        return (tmp_path / f"{mode}.log").read_text().splitlines()[1:]  # after the dated line

    assert run("pretty") == run("log") == ["", "Capturing", "tile 1/2", "warning: focus lost", "a library's line",
                                            "2 screenshots", "1 of 2 maps validated"]


def test_library_warnings_show_once_however_often_the_output_is_configured(ui_state, capsys):
    import logging

    from heroes_capture import ui

    ui.configure(log=True)
    ui.configure(log=True)
    capsys.readouterr()
    logging.getLogger("dxcam").warning("a library's warning")
    assert capsys.readouterr().out.count("a library's warning") == 1


def test_map_list_shows_names_as_written_and_no_validation_details(monkeypatch):
    from contextlib import nullcontext

    from heroes_capture import game_data

    monkeypatch.setattr(game_data, "find_install", lambda: None)
    monkeypatch.setattr(game_data, "open_storage", lambda install: nullcontext())
    monkeypatch.setattr(game_data, "map_index", lambda storage: {"Dragon Shire [bold]": {"file": "x", "category": "Battleground"}})
    monkeypatch.setattr(game_data, "folder_maps", lambda storage: [])
    monkeypatch.setattr(cli, "validated_maps", lambda: {"Dragon Shire [bold]": {"version": "0.1.0", "note": "void edges"}})
    result = runner.invoke(cli.app, ["map", "list"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "Dragon Shire [bold]" in result.output and "✓ validated" in result.output
    assert "0.1.0" not in result.output and "void edges" not in result.output  # the authors' record, not shown


def test_a_resumed_runs_progress_bar_counts_the_whole_run(ui_state):
    from heroes_capture import ui

    ui.configure()
    ui._s.mode = "pretty"
    with ui.step("Capturing"):
        with ui.bar(154, "tiles", done=30) as advance:
            advance()
            [task] = ui._s.bars.tasks
            assert (task.total, task.completed) == (154, 31)


def test_a_pause_shows_while_it_lasts_and_is_gone_after(tmp_path, ui_state):
    """Focus lost or a menu open: a passing line in the live view, not a warning piling up above it;
    the plain log keeps both ends."""
    from heroes_capture import ui

    ui.configure(log=True)
    ui._s.mode = "pretty"
    ui.set_plain_log(tmp_path / "plain.log")
    with ui.step("Capturing"):
        with ui.notice("the game isn't in front"):
            assert ui._s.notice == "the game isn't in front"
        assert ui._s.notice == ""
    lines = (tmp_path / "plain.log").read_text().splitlines()
    assert "paused: the game isn't in front" in lines and any(line.startswith("carrying on after") for line in lines)


def test_map_view_serves_a_rendered_pack_until_stopped(monkeypatch, tmp_path):
    from heroes_capture import serve as serving

    (tmp_path / "maps" / "dragon-shire" / "pack").mkdir(parents=True)
    (tmp_path / "maps" / "dragon-shire" / "pack" / "pack.json").write_text("{}")
    served = []

    class Server:
        server_port = 8123

        def shutdown(self):
            served.append("stopped")

    monkeypatch.setattr(serving, "serve", lambda folder: served.append(folder) or Server())
    monkeypatch.setattr(cli.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    result = runner.invoke(cli.app, ["--log", "map", "view", "Dragon Shire"])
    assert result.exit_code == 0, result.output
    assert served == [Path("maps/dragon-shire/pack"), "stopped"] and "http://127.0.0.1:8123/index.html" in result.output
    result = runner.invoke(cli.app, ["map", "view", "dragon shire", "--structures", "hide"])
    assert result.exit_code != 0 and "no pack in" in result.output
