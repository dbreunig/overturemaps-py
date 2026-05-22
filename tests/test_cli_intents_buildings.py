"""Tests for the `buildings` intent verb."""

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


class _DummyWriter:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _DummyReader:
    schema = object()


def test_buildings_passes_where_through(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "overturemaps.cli.resolve",
        lambda q: [Division(
            id="nyc", name="New York", subtype="locality",
            country="US", region="US-NY",
            admin_level=8, population=8000000, parent_division_id=None,
            bbox=(-74.05, 40.6, -73.9, 40.9),
        )],
    )
    monkeypatch.setattr("overturemaps.cli.get_writer", lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)

    captured = {}

    def fake_reader(type_, bbox, *a, where_filters=None, **k):
        captured["type"] = type_
        captured["bbox"] = bbox
        captured["where_filters"] = where_filters
        return _DummyReader()

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "buildings", "--in", "New York, US-NY",
            "--where", "height>100",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "building"
        assert captured["where_filters"][0].key == "height"
        assert captured["where_filters"][0].op == ">"
        assert captured["where_filters"][0].value == 100


def test_buildings_with_bbox(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("overturemaps.cli.get_writer", lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)

    captured = {}

    def fake_reader(type_, bbox, *a, where_filters=None, **k):
        captured["type"] = type_
        captured["bbox"] = bbox
        captured["where_filters"] = where_filters
        return _DummyReader()

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "buildings", "--bbox", "-74.05,40.6,-73.9,40.9",
            "--where", "height>100",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "building"
        assert captured["bbox"] == [-74.05, 40.6, -73.9, 40.9]


def test_buildings_in_and_bbox_mutually_exclusive(monkeypatch):
    runner = CliRunner()
    result = runner.invoke(cli, [
        "buildings", "--in", "New York",
        "--bbox", "-74.05,40.6,-73.9,40.9",
        "-f", "geojsonseq",
    ])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_buildings_requires_in_or_bbox():
    runner = CliRunner()
    result = runner.invoke(cli, ["buildings", "-f", "geojsonseq"])
    assert result.exit_code != 0
    assert "Provide --in or --bbox" in result.output
