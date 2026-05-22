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


class _FakeReaderWithCategories:
    """Stub reader that surfaces a fixed list of categories.primary values."""
    schema = object()

    def __init__(self, categories):
        import pyarrow as pa
        struct_arr = pa.array(
            [{"primary": c} for c in categories],
            type=pa.struct([pa.field("primary", pa.string())]),
        )
        self._batch = pa.record_batch([struct_arr], names=["categories"])
        self._yielded = False

    def read_next_batch(self):
        if self._yielded:
            raise StopIteration
        self._yielded = True
        return self._batch


def _setup(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "overturemaps.cli.resolve",
        lambda q: [Division(
            id="boston", name="Boston", subtype="locality",
            country="US", region="US-MA",
            admin_level=8, population=654776, parent_division_id=None,
            bbox=(-71.19, 42.23, -70.99, 42.40),
        )],
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


def test_places_with_bbox(monkeypatch):
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
            "places", "--bbox", "-122.295,37.778,-122.265,37.800",
            "--category", "coffee_shop",
            "-f", "geojson", "-o", "out.geojson",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "place"
        assert captured["bbox"] == [-122.295, 37.778, -122.265, 37.800]
        cats = [f for f in captured["where_filters"]
                if f.key == "categories.primary"]
        assert cats and cats[0].value == "coffee_shop"


def test_places_in_and_bbox_mutually_exclusive(monkeypatch):
    _setup(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "places", "--in", "Boston, MA",
        "--bbox", "-71.19,42.23,-70.99,42.40",
        "-f", "geojsonseq",
    ])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_places_requires_in_or_bbox(monkeypatch):
    _setup(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["places", "-f", "geojsonseq"])
    assert result.exit_code != 0
    assert "Provide --in or --bbox" in result.output


def test_places_zero_results_emits_category_suggestion(monkeypatch):
    """When categories.primary=X returns 0 rows, the CLI suggests near matches."""
    _setup(monkeypatch)

    # First reader (the filtered query) returns 0 rows.
    # Second reader (the suggestion enumeration) returns a known category list.
    readers = [
        _DummyReader(),  # filtered query — copy() will see 0 rows
        _FakeReaderWithCategories([
            "ferry_terminal", "ferry_service", "ferry_boat_company",
            "coffee_shop", "restaurant", "hospital",
        ]),
    ]

    def fake_reader(*a, **k):
        return readers.pop(0)

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)
    # copy() returns rows_written; force 0 to trigger the hint.
    monkeypatch.setattr("overturemaps.cli.copy", lambda r, w: 0)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "places", "--bbox", "-122.32,37.77,-122.28,37.80",
            "--category", "ferry",
            "-f", "geojson", "-o", "out.geojson",
        ])
        assert result.exit_code == 0, (result.output, result.stderr)
        # Hint goes to stderr; difflib should rank the three ferry_* values first.
        assert "0 rows" in result.stderr
        assert "ferry" in result.stderr
        assert "ferry_terminal" in result.stderr or "ferry_service" in result.stderr


def test_places_zero_results_no_category_filter_no_hint(monkeypatch):
    """Without a categories.primary filter, no suggestion scan runs."""
    _setup(monkeypatch)
    monkeypatch.setattr("overturemaps.cli.record_batch_reader",
                        lambda *a, **k: _DummyReader())
    monkeypatch.setattr("overturemaps.cli.copy", lambda r, w: 0)
    # If the suggester runs, it'd call record_batch_reader a second time.
    # We didn't queue a second reader, so a second call would return the
    # same _DummyReader, but we'll assert no suggestion text either way.

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "places", "--bbox", "-122.32,37.77,-122.28,37.80",
            "--where", "confidence>0.99",
            "-f", "geojson", "-o", "out.geojson",
        ])
        assert result.exit_code == 0
        assert "Did you mean" not in result.stderr
