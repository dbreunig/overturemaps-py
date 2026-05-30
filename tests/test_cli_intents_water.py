"""Tests for the `water` intent verb."""

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
            id="boston", name="Boston", subtype="locality",
            country="US", region="US-MA",
            admin_level=8, population=654776, parent_division_id=None,
            bbox=(-71.19, 42.23, -70.99, 42.40),
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


def test_water_class_shortcut(monkeypatch):
    captured = {}
    _patch_common(monkeypatch, captured)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "water", "--in", "Boston, MA",
            "--class", "river",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "water"
        klass = [f for f in captured["where_filters"] if f.key == "class"]
        assert klass and klass[0].value == "river"


def test_water_with_bbox(monkeypatch):
    captured = {}
    _patch_common(monkeypatch, captured)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "water", "--bbox", "-71.19,42.23,-70.99,42.40",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "water"
        assert captured["bbox"] == [-71.19, 42.23, -70.99, 42.40]


def test_water_in_and_bbox_mutually_exclusive():
    runner = CliRunner()
    result = runner.invoke(cli, [
        "water", "--in", "Boston",
        "--bbox", "-71.19,42.23,-70.99,42.40",
        "-f", "geojsonseq",
    ])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_water_requires_in_or_bbox():
    runner = CliRunner()
    result = runner.invoke(cli, ["water", "-f", "geojsonseq"])
    assert result.exit_code != 0
    assert "Provide --in or --bbox" in result.output
