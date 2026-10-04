"""The heroes-capture command: its commands and help, and map list's statuses."""

import json

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
    result = runner.invoke(cli.app, ["prepare", "Dragon Shire", "--screen", "3440x1440", "--help"])
    assert result.exit_code == 0 and seen == [["Dragon Shire", "--screen", "3440x1440", "--help"]]


@pytest.fixture(autouse=True)
def in_a_scratch_folder(monkeypatch, tmp_path):
    """Commands write tmp\\ and maps\\ in the current folder."""
    monkeypatch.chdir(tmp_path)


def render_steps(monkeypatch, tmp_path, fail_stitch=False):
    """map render with its three steps stood in for: prepare writes the working files the real
    one would (manifest, prepared map, folder), stitch writes the map's folder."""
    from heroes_capture import inject, stitch

    calls = []

    def prepare(argv):
        calls.append(("prepare", argv))
        work = tmp_path / argv[argv.index("--out") + 1]
        work.mkdir(exist_ok=True)
        (work / "dragon-shire-terrain").mkdir(exist_ok=True)
        (work / "dragon-shire-terrain.stormmap").write_text("map")
        manifest = work / "dragon-shire-terrain.json"
        manifest.write_text(json.dumps({"map": "Dragon Shire", "tiles": [{}] * 3, "sky": {"mode": "black"}}))
        return manifest.resolve()

    def stitching(argv):
        calls.append(("stitch", argv))
        if fail_stitch:
            raise RuntimeError("stitch failed")
        out = tmp_path / argv[argv.index("--output-dir") + 1] / "dragon-shire"
        out.mkdir(parents=True)
        (out / "dragon-shire-terrain.png").write_text("png")

    monkeypatch.setattr(inject, "main", prepare)
    monkeypatch.setattr(cli, "run_capture", lambda argv: calls.append(("capture", argv)))
    monkeypatch.setattr(stitch, "main", stitching)
    return calls


def test_map_render_runs_the_three_steps_with_the_defaults(monkeypatch, tmp_path):
    calls = render_steps(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["map", "render", "dragon shire", "--structures", "hide", "--fov", "12"])
    assert result.exit_code == 0, result.output
    manifest = str((tmp_path / "tmp" / "dragon-shire-terrain.json").resolve())
    assert calls[0] == ("prepare", ["dragon shire", "--structures", "hide", "--fov", "12", "--out", "tmp", "--keep", "0.4"])
    assert calls[1] == ("capture", [manifest])
    assert calls[2] == ("stitch", [manifest, "--tiles", "--output-dir", "maps"])
    assert "Dragon Shire (hide structures): 3 tiles planned, black void" in result.output
    assert (tmp_path / "maps" / "dragon-shire" / "dragon-shire-terrain.png").exists()
    assert "The map is in maps" in result.output
    assert not (tmp_path / "tmp").exists()  # the working files removed, and their folder


def test_map_render_keeps_the_working_files_when_asked(monkeypatch, tmp_path):
    render_steps(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["map", "render", "dragon shire", "--keep-tmp", "-o", "renders"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "renders" / "dragon-shire" / "dragon-shire-terrain.png").exists()
    for name in ("dragon-shire-terrain.json", "dragon-shire-terrain.stormmap", "dragon-shire-terrain", "heroes-capture.log"):
        assert (tmp_path / "tmp" / name).exists(), name


def test_a_failed_map_render_leaves_the_working_files(monkeypatch, tmp_path):
    render_steps(monkeypatch, tmp_path, fail_stitch=True)
    result = runner.invoke(cli.app, ["map", "render", "dragon shire"])
    assert result.exit_code != 0
    assert (tmp_path / "tmp" / "dragon-shire-terrain.json").exists() and (tmp_path / "tmp" / "heroes-capture.log").exists()


def test_the_clean_up_command_removes_only_what_the_tool_wrote(tmp_path):
    tmp = tmp_path / "tmp"
    (tmp / "dragon-shire-terrain" / "tiles").mkdir(parents=True)
    (tmp / "dragon-shire-terrain" / "tiles" / "tile_0001.npy").write_bytes(b"x" * 1000)
    (tmp / "dragon-shire-terrain.json").write_text(json.dumps({"id": "dragon-shire-terrain", "tiles": []}))
    (tmp / "dragon-shire-terrain.stormmap").write_text("map")
    (tmp / "cursed-hollow-structures.stormmap").write_text("map")  # a preparation that failed before its manifest
    (tmp / "heroes-capture.log").write_text("run")
    (tmp / "notes.json").write_text(json.dumps({"mine": True}))  # not the tool's
    (tmp / "setup.log").write_text("pip")
    result = runner.invoke(cli.app, ["clean-up"])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in tmp.iterdir()) == ["notes.json", "setup.log"]
    assert "1 render" in result.output and "left alone" in result.output
    (tmp / "notes.json").unlink()
    (tmp / "setup.log").unlink()
    (tmp / "heroes-capture.log").write_text("run")
    assert runner.invoke(cli.app, ["clean-up"]).exit_code == 0
    assert not tmp.exists()  # emptied, so removed
    assert "Nothing to clean up" in runner.invoke(cli.app, ["clean-up"]).output


def test_a_failed_map_render_says_how_to_clean_up(monkeypatch, tmp_path):
    render_steps(monkeypatch, tmp_path, fail_stitch=True)
    result = runner.invoke(cli.app, ["--log", "map", "render", "dragon shire"])
    assert "heroes-capture clean-up removes them" in result.output


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
        ("Battleground", "dragon cave", "not yet", "", ""),
        ("Battleground", "Dragon Shire", "validated", "0.1.0", "void"),
        ("Battleground", "Towers of Doom", "not yet", "", ""),
        ("Brawl", "Pull Party", "not yet", "", ""),
        ("Other", "Sandbox (Cursed Hollow)", "not yet", "", ""),
        ("Other", "Try Me Mode", "unsupported", "", ""),
        (cli.GONE, "Old Map", "not in the game", "0.1.0", ""),
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
    assert "maps validated; 4 unsupported" in result.output
