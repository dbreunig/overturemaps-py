"""Tests for the `capabilities` command."""

import json

from click.testing import CliRunner

from botmap.cli import cli


def test_capabilities_json_structure():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "capabilities"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "version" in data
    assert "commands" in data
    cmd_names = {c["name"] for c in data["commands"]}
    # Spot check: a few expected commands are present
    for expected in ("download", "where", "count", "themes", "types",
                     "schema", "categories", "capabilities"):
        assert expected in cmd_names, f"missing command {expected}"


def test_capabilities_command_has_params():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "capabilities"])
    data = json.loads(result.output)
    download = next(c for c in data["commands"] if c["name"] == "download")
    param_names = {p["name"] for p in download["params"]}
    assert "type_" in param_names or "type" in param_names
    assert "bbox" in param_names
    assert "in_place" in param_names or "in" in param_names
    assert "where_exprs" in param_names or "where" in param_names
