"""Tests for the introspection CLI commands."""

import json

from click.testing import CliRunner

from overturemaps.cli import cli


def test_themes_human():
    runner = CliRunner()
    result = runner.invoke(cli, ["themes"])
    assert result.exit_code == 0
    assert "buildings" in result.output
    assert "transportation" in result.output


def test_themes_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "themes"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 6
    assert all("name" in t and "description" in t and "types" in t for t in data)


def test_types_all_human():
    runner = CliRunner()
    result = runner.invoke(cli, ["types"])
    assert result.exit_code == 0
    assert "building" in result.output
    assert "segment" in result.output


def test_types_filtered_human():
    runner = CliRunner()
    result = runner.invoke(cli, ["types", "--theme", "buildings"])
    assert result.exit_code == 0
    assert "building" in result.output
    assert "building_part" in result.output
    assert "segment" not in result.output


def test_types_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "types"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 15
