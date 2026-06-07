"""Tests for the `schema` command."""

import json

import pyarrow as pa
from click.testing import CliRunner

from botmap.cli import cli


def test_schema_command_json(monkeypatch):
    """`schema` returns flattened schema + sample feature."""

    schema = pa.schema([
        ("id", pa.string()),
        ("height", pa.float64()),
        ("categories", pa.struct([("primary", pa.string())])),
    ])

    class _Reader:
        def __init__(self):
            self.schema = schema
            self._done = False

        def read_next_batch(self):
            if self._done:
                raise StopIteration
            self._done = True
            return pa.RecordBatch.from_pylist(
                [{"id": "abc", "height": 50.0, "categories": {"primary": "hotel"}}],
                schema=schema,
            )

    monkeypatch.setattr("botmap.cli.record_batch_reader",
                        lambda *a, **k: _Reader())
    monkeypatch.setattr("botmap.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "schema", "-t", "place"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "place"
    field_names = {f["name"] for f in payload["fields"]}
    assert "id" in field_names
    assert "categories.primary" in field_names
    assert "example" in payload


def test_schema_command_serializes_wkb_geometry(monkeypatch):
    """A sample feature with WKB-bytes geometry must serialize as GeoJSON."""
    import shapely.wkb
    from shapely.geometry import Point

    schema = pa.schema([
        ("id", pa.string()),
        ("geometry", pa.binary()),
    ])

    wkb = shapely.wkb.dumps(Point(-73.985, 40.748))

    class _Reader:
        def __init__(self):
            self.schema = schema
            self._done = False

        def read_next_batch(self):
            if self._done:
                raise StopIteration
            self._done = True
            return pa.RecordBatch.from_pylist(
                [{"id": "abc", "geometry": wkb}], schema=schema,
            )

    monkeypatch.setattr("botmap.cli.record_batch_reader",
                        lambda *a, **k: _Reader())
    monkeypatch.setattr("botmap.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "schema", "-t", "place"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    geom = payload["example"]["geometry"]
    assert geom["type"] == "Point"
    assert geom["coordinates"] == [-73.985, 40.748]
