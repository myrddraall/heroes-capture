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


def test_map_render_runs_the_three_steps_with_the_defaults(monkeypatch, tmp_path):
    from heroes_capture import inject, stitch

    calls = []
    monkeypatch.setattr(inject, "main", lambda argv: calls.append(("prepare", argv)) or tmp_path / "m.json")
    monkeypatch.setattr(cli, "run_capture", lambda argv: calls.append(("capture", argv)))
    monkeypatch.setattr(stitch, "main", lambda argv: calls.append(("stitch", argv)))
    result = runner.invoke(cli.app, ["map", "render", "dragon shire", "--structures", "hide", "--fov", "12"])
    assert result.exit_code == 0, result.output
    assert calls[0] == ("prepare", ["dragon shire", "--structures", "hide", "--fov", "12", "--keep", "0.4"])
    assert calls[1] == ("capture", [str(tmp_path / "m.json")]) and calls[2] == ("stitch", [str(tmp_path / "m.json"), "--tiles"])


def test_map_rows():
    validated = {"Dragon Shire": {"version": "0.1.0", "note": "void"}, "Old Map": {"version": "0.1.0", "note": ""}}
    assert cli.map_rows(["Towers of Doom", "dragon cave", "Dragon Shire"], validated) == [
        ("dragon cave", "not yet", "", ""),
        ("Dragon Shire", "validated", "0.1.0", "void"),
        ("Towers of Doom", "not yet", "", ""),
        ("Old Map", "not in the game", "0.1.0", ""),
    ]


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
    assert "maps validated" in result.output
