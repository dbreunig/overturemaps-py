"""Tests for the `where` command."""

import json

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


@pytest.fixture
def fake_match(monkeypatch):
    captured = {}

    def fake(query):
        captured["query"] = query
        return Division(
            id="boston-ma", name="Boston", subtype="locality",
            country="US", region="US-MA",
            admin_level=8, population=654776, parent_division_id="ma",
            bbox=(-71.19, 42.23, -70.99, 42.40),
        )

    def fake_resolve(query):
        return [fake(query)]

    monkeypatch.setattr("overturemaps.cli.best_match", fake)
    monkeypatch.setattr("overturemaps.cli.resolve", fake_resolve)
    return captured


def test_where_human_output(fake_match):
    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Boston, MA"])
    assert result.exit_code == 0
    assert "Boston" in result.output
    assert "US-MA" in result.output
    assert "654776" in result.output or "654,776" in result.output


def test_where_json_output(fake_match):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "where", "Boston, MA"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["name"] == "Boston"
    assert data["region"] == "US-MA"
    assert data["bbox"] == [-71.19, 42.23, -70.99, 42.40]
    assert "candidates" in data


def test_where_no_match(monkeypatch):
    monkeypatch.setattr(
        "overturemaps.cli.best_match",
        lambda q: (_ for _ in ()).throw(LookupError(f"No match for {q!r}")),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Nonexistentville"])
    assert result.exit_code != 0


def test_where_no_match_json(monkeypatch):
    monkeypatch.setattr(
        "overturemaps.cli.best_match",
        lambda q: (_ for _ in ()).throw(LookupError(f"No match for {q!r}")),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "where", "Nonexistentville"])
    assert result.exit_code != 0
    # JSON error is printed to stderr by Click's invoke (mixed in result.output by default)
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "no_match" in combined or "No match" in combined
