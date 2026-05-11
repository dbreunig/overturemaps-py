"""Tests for download CLI command."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from overturemaps.cli import (
    BboxParamType,
    _bbox_area_sq_deg,
    cli,
)


class _DummyWriter:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, value, traceback):
        return False


class _DummyReader:
    schema = object()


def test_download_saves_absolute_output_path(monkeypatch):
    """`download` stores absolute output path in saved state."""

    captured = {}

    monkeypatch.setattr("overturemaps.cli.get_latest_release", lambda: "2024-11-13.0")

    monkeypatch.setattr(
        "overturemaps.cli.record_batch_reader", lambda *args, **kwargs: _DummyReader()
    )
    monkeypatch.setattr(
        "overturemaps.cli.get_writer", lambda *args, **kwargs: _DummyWriter()
    )
    monkeypatch.setattr("overturemaps.cli.copy", lambda *args, **kwargs: None)

    def _fake_save_state(state, state_path):
        captured["state"] = state
        captured["state_path"] = state_path

    monkeypatch.setattr("overturemaps.cli.save_state", _fake_save_state)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            [
                "download",
                "-f",
                "geojson",
                "-t",
                "building",
                "-o",
                "relative-output.geojson",
            ],
        )

        assert result.exit_code == 0
        assert "state" in captured
        assert captured["state"].bbox is None
        assert captured["state"].output == str(
            Path("relative-output.geojson").resolve()
        )


# --- BboxParamType validation tests ---


class TestBboxParamType:
    """Tests for BboxParamType.convert() error messages."""

    def _convert(self, value):
        """Helper: invoke the param type's convert method."""
        param_type = BboxParamType()
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["download", "-f", "geojson", "-t", "building", "--bbox", value],
        )
        return result

    def test_valid_bbox(self):
        param_type = BboxParamType()
        result = param_type.convert("-71.10,42.34,-71.05,42.36", None, None)
        assert result == [-71.10, 42.34, -71.05, 42.36]

    def test_wrong_number_of_values(self):
        result = self._convert("1,2,3")
        assert result.exit_code != 0
        assert "exactly 4 values" in result.output

    def test_non_numeric_values(self):
        result = self._convert("a,b,c,d")
        assert result.exit_code != 0
        assert "must be numbers" in result.output

    def test_longitude_out_of_range(self):
        result = self._convert("-200,42,-71,43")
        assert result.exit_code != 0
        assert "Longitude" in result.output
        assert "-180" in result.output

    def test_latitude_out_of_range(self):
        result = self._convert("-71,-100,-70,42")
        assert result.exit_code != 0
        assert "Latitude" in result.output
        assert "-90" in result.output

    def test_swapped_xmin_xmax(self):
        result = self._convert("10,42,-10,43")
        assert result.exit_code != 0
        assert "xmin" in result.output
        assert "xmax" in result.output

    def test_swapped_ymin_ymax(self):
        result = self._convert("-71,43,-70,42")
        assert result.exit_code != 0
        assert "ymin" in result.output
        assert "ymax" in result.output

    def test_example_shown_in_error(self):
        """Error messages should include a usage example."""
        result = self._convert("1,2,3")
        assert "Example" in result.output or "--bbox" in result.output


# --- Area helper ---


def test_bbox_area_sq_deg():
    assert _bbox_area_sq_deg(0, 0, 10, 10) == 100.0
    assert _bbox_area_sq_deg(-180, -90, 180, 90) == pytest.approx(64800.0)


# --- Large bbox warning in download command ---


def test_download_warns_on_large_bbox(monkeypatch):
    """download should warn when bbox is very large."""
    monkeypatch.setattr("overturemaps.cli.get_latest_release", lambda: "2024-11-13.0")

    monkeypatch.setattr(
        "overturemaps.cli.record_batch_reader", lambda *args, **kwargs: _DummyReader()
    )
    monkeypatch.setattr(
        "overturemaps.cli.get_writer", lambda *args, **kwargs: _DummyWriter()
    )
    monkeypatch.setattr("overturemaps.cli.copy", lambda *args, **kwargs: None)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "download",
            "-f",
            "geojson",
            "-t",
            "building",
            "--bbox",
            "-180,-90,180,90",
        ],
    )
    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "1.2 TB" in result.output
    assert "400 GB" in result.output


def test_download_warns_on_no_bbox(monkeypatch):
    """download should warn when no bbox is provided."""
    monkeypatch.setattr("overturemaps.cli.get_latest_release", lambda: "2024-11-13.0")

    monkeypatch.setattr(
        "overturemaps.cli.record_batch_reader", lambda *args, **kwargs: _DummyReader()
    )
    monkeypatch.setattr(
        "overturemaps.cli.get_writer", lambda *args, **kwargs: _DummyWriter()
    )
    monkeypatch.setattr("overturemaps.cli.copy", lambda *args, **kwargs: None)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "download",
            "-f",
            "geojson",
            "-t",
            "building",
        ],
    )
    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "No bounding box" in result.output
    assert "1.2 TB" in result.output


def test_download_no_warning_on_small_bbox(monkeypatch):
    """download should not warn when bbox is small."""
    monkeypatch.setattr("overturemaps.cli.get_latest_release", lambda: "2024-11-13.0")

    monkeypatch.setattr(
        "overturemaps.cli.record_batch_reader", lambda *args, **kwargs: _DummyReader()
    )
    monkeypatch.setattr(
        "overturemaps.cli.get_writer", lambda *args, **kwargs: _DummyWriter()
    )
    monkeypatch.setattr("overturemaps.cli.copy", lambda *args, **kwargs: None)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "download",
            "-f",
            "geojson",
            "-t",
            "building",
            "--bbox",
            "-71.10,42.34,-71.05,42.36",
        ],
    )
    assert result.exit_code == 0
    assert "Warning" not in result.output


def test_download_invalid_release_explains_retention_policy(monkeypatch):
    """download --release with an old release should explain the retention policy."""
    monkeypatch.setattr(
        "overturemaps.cli.get_available_releases",
        lambda: (["2026-03-18.0", "2026-02-18.0"], "2026-03-18.0"),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "download",
            "-f",
            "geojson",
            "-t",
            "building",
            "--release",
            "2024-07-22.0",
        ],
    )
    assert result.exit_code != 0
    assert "no longer available" in result.output
    assert "GDPR" in result.output
    assert "60 days" in result.output
    assert "2026-03-18.0" in result.output
    assert "docs.overturemaps.org/release-calendar" in result.output


def test_download_with_in_flag_resolves_to_bbox(monkeypatch):
    """`--in` resolves a place to a bbox and feeds it through the pipeline."""
    from overturemaps.geocoding import Division

    captured = {}

    def fake_best_match(query):
        captured["query"] = query
        return Division(
            id="boston-ma", name="Boston", subtype="locality",
            country="US", region="US-MA",
            admin_level=8, population=654776, parent_division_id="ma",
            bbox=(-71.19, 42.23, -70.99, 42.40),
        )

    monkeypatch.setattr("overturemaps.cli.best_match", fake_best_match)
    monkeypatch.setattr("overturemaps.cli.get_latest_release", lambda: "2025-12-17.0")

    def fake_reader(type_, bbox, *args, **kwargs):
        captured["bbox"] = bbox
        captured["type"] = type_
        return _DummyReader()

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)
    monkeypatch.setattr("overturemaps.cli.get_writer", lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)
    monkeypatch.setattr("overturemaps.cli.save_state", lambda *a, **k: None)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            ["download", "-t", "building", "-f", "geojson",
             "-o", "out.geojson", "--in", "Boston, MA"],
        )
        assert result.exit_code == 0, result.output
        assert captured["query"] == "Boston, MA"
        assert captured["bbox"] == [-71.19, 42.23, -70.99, 42.40]


def test_download_rejects_in_and_bbox_together():
    """`--in` and `--bbox` are mutually exclusive."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["download", "-t", "building", "-f", "geojson",
         "-o", "out.geojson", "--in", "Boston", "--bbox", "-71,42,-70,43"],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower() or "cannot be used together" in result.output.lower()


def test_download_with_where_passes_filter_to_reader(monkeypatch):
    """`--where` is parsed and passed alongside the bbox filter."""
    captured = {}

    monkeypatch.setattr("overturemaps.cli.get_latest_release", lambda: "2025-12-17.0")

    def fake_reader(type_, bbox, release, ct, rt, stac, where_filters=None):
        captured["where_filters"] = where_filters
        return _DummyReader()

    # We're going to overwrite record_batch_reader to accept the new kwarg.
    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)
    monkeypatch.setattr("overturemaps.cli.get_writer", lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)
    monkeypatch.setattr("overturemaps.cli.save_state", lambda *a, **k: None)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            ["download", "-t", "building", "-f", "geojson",
             "-o", "out.geojson", "--bbox", "-71.1,42.3,-71.0,42.4",
             "--where", "height>50"],
        )
        assert result.exit_code == 0, result.output
        assert captured["where_filters"] is not None
        assert len(captured["where_filters"]) == 1
        assert captured["where_filters"][0].key == "height"
        assert captured["where_filters"][0].op == ">"
        assert captured["where_filters"][0].value == 50


def test_download_malformed_where_clean_error():
    """A malformed --where expression yields a clean UsageError, not a traceback."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["download", "-t", "building", "-f", "geojson",
         "-o", "out.geojson", "--bbox", "-71,42,-70,43",
         "--where", "just_a_key"],
    )
    assert result.exit_code != 0
    # The error message comes from parse_where_expr's ValueError
    assert "Could not parse" in result.output
