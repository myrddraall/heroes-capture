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
    """Commands write work\\heroes-capture.log in the current folder."""
    monkeypatch.chdir(tmp_path)


def test_map_render_runs_the_three_steps_with_the_defaults(monkeypatch, tmp_path):
    from heroes_capture import inject, stitch

    calls = []
    (tmp_path / "m.json").write_text(json.dumps({"map": "Dragon Shire", "tiles": [{}] * 3, "sky": {"mode": "black"}}))
    monkeypatch.setattr(inject, "main", lambda argv: calls.append(("prepare", argv)) or tmp_path / "m.json")
    monkeypatch.setattr(cli, "run_capture", lambda argv: calls.append(("capture", argv)))
    monkeypatch.setattr(stitch, "main", lambda argv: calls.append(("stitch", argv)))
    result = runner.invoke(cli.app, ["map", "render", "dragon shire", "--structures", "hide", "--fov", "12"])
    assert result.exit_code == 0, result.output
    assert calls[0] == ("prepare", ["dragon shire", "--structures", "hide", "--fov", "12", "--keep", "0.4"])
    assert calls[1] == ("capture", [str(tmp_path / "m.json")]) and calls[2] == ("stitch", [str(tmp_path / "m.json"), "--tiles"])
    assert "Dragon Shire (hide structures): 3 tiles planned, black void" in result.output
    assert (tmp_path / "work" / "heroes-capture.log").exists()


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
