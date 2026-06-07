"""Integration smoke tests for the new agent-facing commands."""

import json

import pytest
from click.testing import CliRunner

from botmap.cli import cli

pytestmark = pytest.mark.integration


def test_where_boston_ma():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "where", "Boston, US-MA"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["region"] == "US-MA"
    assert data["subtype"] == "locality"
    assert data["population"] > 100_000
    xmin, ymin, xmax, ymax = data["bbox"]
    assert -72 < xmin < -70 and 41 < ymin < 43


def test_count_places_in_boston():
    runner = CliRunner()
    result = runner.invoke(cli, [
        "--json", "count", "-t", "place", "--in", "Boston, US-MA",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["count"] > 100


def test_categories_in_boston_top_5():
    runner = CliRunner()
    result = runner.invoke(cli, [
        "--json", "categories", "-t", "place",
        "--in", "Boston, US-MA", "--top", "5",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 5
    for row in data:
        assert row["count"] > 0
        assert isinstance(row["value"], str)


def test_at_nearby_place():
    # Empire State Building
    runner = CliRunner()
    result = runner.invoke(cli, [
        "at", "40.7484,-73.9857", "-t", "place", "-n", "3",
        "-f", "geojsonseq",
    ])
    assert result.exit_code == 0, result.output
    lines = [l for l in result.output.strip().split("\n") if l.startswith("{")]
    assert 1 <= len(lines) <= 3


def test_containing_known_point():
    # A point in central Boston
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "containing", "42.3601,-71.0589"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    subtypes = [d["subtype"] for d in data]
    assert "country" in subtypes
    assert "region" in subtypes or "locality" in subtypes
    # Innermost-first ordering; admin_level may be None for neighborhoods
    levels = [d["admin_level"] or 0 for d in data]
    assert levels == sorted(levels, reverse=True)
