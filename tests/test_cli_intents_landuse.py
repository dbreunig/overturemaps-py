"""Tests for the `landuse` intent verb."""

from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


class _DummyWriter:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _DummyReader:
    schema = object()


def _patch_common(monkeypatch, captured):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "overturemaps.cli.resolve",
        lambda q: [Division(
            id="brooklyn", name="Brooklyn", subtype="locality",
            country="US", region="US-NY",
            admin_level=8, population=2600000, parent_division_id=None,
            bbox=(-74.05, 40.57, -73.83, 40.74),
        )],
    )
    monkeypatch.setattr("overturemaps.cli.get_writer", lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)

    def fake_reader(type_, bbox, *a, where_filters=None, **k):
        captured["type"] = type_
        captured["bbox"] = bbox
        captured["where_filters"] = where_filters
        return _DummyReader()

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)


def test_landuse_class_shortcut(monkeypatch):
    captured = {}
    _patch_common(monkeypatch, captured)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "landuse", "--in", "Brooklyn",
            "--class", "residential",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "land_use"
        klass = [f for f in captured["where_filters"] if f.key == "class"]
        assert klass and klass[0].value == "residential"


def test_landuse_with_bbox(monkeypatch):
    captured = {}
    _patch_common(monkeypatch, captured)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "landuse", "--bbox", "-74.05,40.57,-73.83,40.74",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "land_use"
        assert captured["bbox"] == [-74.05, 40.57, -73.83, 40.74]


def test_landuse_in_and_bbox_mutually_exclusive():
    runner = CliRunner()
    result = runner.invoke(cli, [
        "landuse", "--in", "Brooklyn",
        "--bbox", "-74.05,40.57,-73.83,40.74",
        "-f", "geojsonseq",
    ])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_landuse_requires_in_or_bbox():
    runner = CliRunner()
    result = runner.invoke(cli, ["landuse", "-f", "geojsonseq"])
    assert result.exit_code != 0
    assert "Provide --in or --bbox" in result.output
