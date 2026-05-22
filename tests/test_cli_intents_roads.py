"""Tests for the `roads` intent verb."""

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


class _DummyWriter:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _DummyReader:
    schema = object()


def test_roads_class_shortcut(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "overturemaps.cli.resolve",
        lambda q: [Division(
            id="tx", name="Texas", subtype="region",
            country="US", region="US-TX",
            admin_level=4, population=30000000, parent_division_id=None,
            bbox=(-106.6, 25.8, -93.5, 36.5),
        )],
    )
    monkeypatch.setattr("overturemaps.cli.get_writer", lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)

    captured = {}

    def fake_reader(type_, bbox, *a, where_filters=None, **k):
        captured["type"] = type_
        captured["where_filters"] = where_filters
        return _DummyReader()

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "roads", "--in", "Texas, US",
            "--class", "motorway",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "segment"
        klass = [f for f in captured["where_filters"] if f.key == "class"]
        assert klass and klass[0].value == "motorway"


def test_roads_with_bbox(monkeypatch):
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
            "roads", "--bbox", "-106.6,25.8,-93.5,36.5",
            "--class", "motorway",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "segment"
        assert captured["bbox"] == [-106.6, 25.8, -93.5, 36.5]
        klass = [f for f in captured["where_filters"] if f.key == "class"]
        assert klass and klass[0].value == "motorway"


def test_roads_in_and_bbox_mutually_exclusive():
    runner = CliRunner()
    result = runner.invoke(cli, [
        "roads", "--in", "Texas",
        "--bbox", "-106.6,25.8,-93.5,36.5",
        "-f", "geojsonseq",
    ])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_roads_requires_in_or_bbox():
    runner = CliRunner()
    result = runner.invoke(cli, ["roads", "-f", "geojsonseq"])
    assert result.exit_code != 0
    assert "Provide --in or --bbox" in result.output
