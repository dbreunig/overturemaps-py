"""Tests for the `at` command."""

import pyarrow as pa
import pytest
from click.testing import CliRunner

from overturemaps.cli import cli


def _features_reader(rows):
    """Build a one-shot RecordBatchReader from a list of dicts."""
    schema = pa.schema([
        ("id", pa.string()),
        ("name", pa.string()),
        ("geometry", pa.binary()),
    ])
    return _Reader(schema, rows)


class _Reader:
    def __init__(self, schema, rows):
        self.schema = schema
        self._rows = rows
        self._done = False

    def read_next_batch(self):
        if self._done:
            raise StopIteration
        self._done = True
        return pa.RecordBatch.from_pylist(self._rows, schema=self.schema)


def test_at_sorts_by_distance(monkeypatch, tmp_path):
    """`at` should keep the N closest features, sorted by distance ascending."""
    import shapely.wkb
    from shapely.geometry import Point

    # Three points: the third is closest, the first is farthest.
    pts = [
        ("p_far", "Far Cafe", shapely.wkb.dumps(Point(-71.060, 42.360))),
        ("p_mid", "Mid Cafe", shapely.wkb.dumps(Point(-71.061, 42.360))),
        ("p_near", "Near Cafe", shapely.wkb.dumps(Point(-71.0615, 42.360))),
    ]
    rows = [{"id": pid, "name": name, "geometry": wkb}
            for pid, name, wkb in pts]

    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("overturemaps.cli.record_batch_reader",
                        lambda *a, **k: _features_reader(rows))

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "at", "42.360,-71.0617", "-t", "place", "-n", "2",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        lines = open("out.jsonl").read().strip().split("\n")
        assert len(lines) == 2
        # First line should be the nearest feature
        assert '"id":"p_near"' in lines[0]
        assert '"id":"p_mid"' in lines[1]


def test_at_passes_where_filters(monkeypatch, tmp_path):
    """`at --where` should parse expressions and forward them to the reader."""
    captured = {}

    def fake_reader(type_, bbox, *a, where_filters=None, **k):
        captured["type"] = type_
        captured["bbox"] = bbox
        captured["where_filters"] = where_filters
        return _features_reader([])

    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "at", "42.360,-71.0617", "-t", "place", "-n", "5",
            "--where", "categories.primary=coffee_shop",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "place"
        assert captured["where_filters"] is not None
        assert len(captured["where_filters"]) == 1
        f = captured["where_filters"][0]
        assert f.key == "categories.primary"
        assert f.op == "="
        assert f.value == "coffee_shop"


def test_at_invalid_where_errors(monkeypatch):
    """`at --where` with an invalid expression should fail cleanly."""
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("overturemaps.cli.record_batch_reader",
                        lambda *a, **k: _features_reader([]))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "at", "42.360,-71.0617",
        "--where", "this is not a filter",
        "-f", "geojsonseq",
    ])
    assert result.exit_code != 0
