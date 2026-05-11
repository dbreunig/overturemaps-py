"""Tests for the `places` intent verb."""

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


class _DummyWriter:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _DummyReader:
    schema = object()


def _setup(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "overturemaps.cli.best_match",
        lambda q: Division(
            id="boston", name="Boston", subtype="locality",
            country="US", region="US-MA",
            admin_level=8, population=654776, parent_division_id=None,
            bbox=(-71.19, 42.23, -70.99, 42.40),
        ),
    )
    monkeypatch.setattr("overturemaps.cli.get_writer",
                        lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)
    monkeypatch.setattr("overturemaps.cli.save_state", lambda *a, **k: None)


def test_places_with_category(monkeypatch):
    _setup(monkeypatch)
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
            "places", "--in", "Boston, MA",
            "--category", "restaurant",
            "-f", "geojson", "-o", "out.geojson",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "place"
        assert captured["bbox"] == [-71.19, 42.23, -70.99, 42.40]
        cats = [f for f in captured["where_filters"]
                if f.key == "categories.primary"]
        assert len(cats) == 1
        assert cats[0].value == "restaurant"
