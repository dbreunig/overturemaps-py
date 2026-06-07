"""Tests for the `addresses` intent verb."""

from click.testing import CliRunner

from botmap.cli import cli
from botmap.geocoding import Division


class _DummyWriter:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _DummyReader:
    schema = object()


def _setup(monkeypatch):
    monkeypatch.setattr("botmap.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "botmap.cli.resolve",
        lambda q: [Division(
            id="alameda", name="Alameda", subtype="locality",
            country="US", region="US-CA",
            admin_level=8, population=78280, parent_division_id=None,
            bbox=(-122.34, 37.71, -122.21, 37.79),
        )],
    )
    monkeypatch.setattr("botmap.cli.get_writer",
                        lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("botmap.cli.copy", lambda *a, **k: None)
    monkeypatch.setattr("botmap.cli.save_state", lambda *a, **k: None)


def test_addresses_with_street_substring(monkeypatch):
    _setup(monkeypatch)
    captured = {}

    def fake_reader(type_, bbox, *a, where_filters=None, **k):
        captured["type"] = type_
        captured["bbox"] = bbox
        captured["where_filters"] = where_filters
        return _DummyReader()

    monkeypatch.setattr("botmap.cli.record_batch_reader", fake_reader)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "addresses", "--in", "Alameda, US-CA",
            "--street", "Fountain", "--number", "1208",
            "-f", "geojson", "-o", "out.geojson",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "address"
        assert captured["bbox"] == [-122.34, 37.71, -122.21, 37.79]
        streets = [f for f in captured["where_filters"] if f.key == "street"]
        assert len(streets) == 1
        assert streets[0].op == "~"
        assert streets[0].value == "Fountain"
        numbers = [f for f in captured["where_filters"] if f.key == "number"]
        assert len(numbers) == 1
        assert numbers[0].op == "="
        assert numbers[0].value == "1208"


def test_addresses_with_bbox_and_postcode(monkeypatch):
    _setup(monkeypatch)
    captured = {}

    def fake_reader(type_, bbox, *a, where_filters=None, **k):
        captured["bbox"] = bbox
        captured["where_filters"] = where_filters
        return _DummyReader()

    monkeypatch.setattr("botmap.cli.record_batch_reader", fake_reader)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "addresses", "--bbox", "-122.3,37.7,-122.2,37.8",
            "--postcode", "94501",
            "-f", "geojson", "-o", "out.geojson",
        ])
        assert result.exit_code == 0, result.output
        assert captured["bbox"] == [-122.3, 37.7, -122.2, 37.8]
        codes = [f for f in captured["where_filters"] if f.key == "postcode"]
        assert codes and codes[0].value == "94501"


def test_addresses_requires_in_or_bbox(monkeypatch):
    _setup(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "addresses", "--street", "Fountain",
        "-f", "geojsonseq",
    ])
    assert result.exit_code != 0
    assert "Provide --in or --bbox" in result.output


def test_addresses_in_and_bbox_mutually_exclusive(monkeypatch):
    _setup(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "addresses", "--in", "Alameda, US-CA",
        "--bbox", "-122.3,37.7,-122.2,37.8",
        "-f", "geojsonseq",
    ])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_addresses_ambiguous_in_emits_stderr_warning(monkeypatch):
    """Two candidates -> stderr warning naming both and pointing to `where --all`."""
    monkeypatch.setattr("botmap.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    alameda_ca = Division(
        id="alameda-ca", name="Alameda", subtype="locality",
        country="US", region="US-CA",
        admin_level=8, population=78280, parent_division_id=None,
        bbox=(-122.34, 37.71, -122.21, 37.79),
    )
    alameda_sk = Division(
        id="alameda-sk", name="Alameda", subtype="region",
        country="CA", region="CA-SK",
        admin_level=4, population=None, parent_division_id=None,
        bbox=(-102.3, 49.2, -102.2, 49.3),
    )
    monkeypatch.setattr(
        "botmap.cli.resolve",
        lambda q: [alameda_ca, alameda_sk],
    )
    monkeypatch.setattr("botmap.cli.get_writer",
                        lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("botmap.cli.copy", lambda *a, **k: None)
    monkeypatch.setattr("botmap.cli.save_state", lambda *a, **k: None)
    monkeypatch.setattr("botmap.cli.record_batch_reader",
                        lambda *a, **k: _DummyReader())

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "addresses", "--in", "Alameda, CA",
            "--street", "Fountain",
            "-f", "geojson", "-o", "out.geojson",
        ])
        assert result.exit_code == 0, (result.output, result.stderr)
        stderr = result.stderr
        assert "Ambiguous" in stderr
        assert "US-CA" in stderr
        assert "CA-SK" in stderr
        assert "--all" in stderr
