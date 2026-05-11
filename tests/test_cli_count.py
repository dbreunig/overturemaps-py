"""Tests for the `count` command."""

import json

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


def test_count_with_bbox(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("overturemaps.cli.count_rows",
                        lambda *a, **k: 12345)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "count", "-t", "building", "--bbox", "-71.1,42.3,-71.0,42.4",
    ])
    assert result.exit_code == 0
    assert "12345" in result.output or "12,345" in result.output


def test_count_json(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("overturemaps.cli.count_rows",
                        lambda *a, **k: 12345)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "--json", "count", "-t", "building",
        "--bbox", "-71.1,42.3,-71.0,42.4",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 12345
    assert payload["type"] == "building"
    assert payload["bbox"] == [-71.1, 42.3, -71.0, 42.4]


def test_count_with_in_and_where(monkeypatch):
    captured = {}
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "overturemaps.cli.best_match",
        lambda q: Division(
            id="x", name="Boston", subtype="locality",
            country="US", region="US-MA",
            admin_level=8, population=654776, parent_division_id=None,
            bbox=(-71.2, 42.2, -71.0, 42.4),
        ),
    )

    def fake_count(type_, bbox=None, release=None, **kwargs):
        captured["bbox"] = bbox
        captured["where_filters"] = kwargs.get("where_filters")
        return 42

    monkeypatch.setattr("overturemaps.cli.count_rows", fake_count)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "count", "-t", "building", "--in", "Boston, MA",
        "--where", "height>100",
    ])
    assert result.exit_code == 0, result.output
    assert captured["bbox"] == [-71.2, 42.2, -71.0, 42.4]
    assert len(captured["where_filters"]) == 1
    assert captured["where_filters"][0].key == "height"
