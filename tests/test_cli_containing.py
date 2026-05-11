"""Tests for the `containing` command."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.compute as pc
import pytest
from click.testing import CliRunner

from overturemaps.cli import cli


def _write_index(tmp_path):
    """Write a fake divisions index with three nested divisions."""
    table = pa.table({
        "id": ["us", "ma", "boston"],
        "name_primary": ["United States", "Massachusetts", "Boston"],
        "subtype": ["country", "region", "locality"],
        "class": [None, None, None],
        "country": ["US", "US", "US"],
        "region": [None, "US-MA", "US-MA"],
        "admin_level": [2, 4, 8],
        "population": [330000000, 7000000, 654776],
        "parent_division_id": [None, "us", "ma"],
        # All three bboxes contain Boston's downtown
        "bbox_xmin": [-180.0, -73.5, -71.19],
        "bbox_ymin": [18.0, 41.2, 42.23],
        "bbox_xmax": [-66.0, -69.9, -70.99],
        "bbox_ymax": [71.0, 42.9, 42.40],
    })
    p = tmp_path / "overturemaps" / "divisions-index-2025-12-17.0.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, p)


def test_containing_returns_innermost_first(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    # We stub _polygon_contains to always return True so the test doesn't hit S3.
    monkeypatch.setattr(
        "overturemaps.cli._polygon_contains",
        lambda division_id, lon, lat: True,
    )

    _write_index(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "--json", "containing", "42.360,-71.060",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    # Innermost (highest admin_level) first
    assert data[0]["subtype"] == "locality"
    assert data[1]["subtype"] == "region"
    assert data[2]["subtype"] == "country"
