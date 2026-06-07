"""Tests for the divisions-index cache."""

import os
from pathlib import Path

import pytest

from botmap.cache import (
    cache_dir,
    index_path,
    cache_info,
    clear_cache,
)


def test_cache_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache_dir() == tmp_path / "botmap"


def test_cache_dir_fallback_when_xdg_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".cache" / "botmap"
    assert cache_dir() == expected


def test_index_path_includes_release(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    p = index_path("2025-12-17.0")
    assert p.name == "divisions-index-2025-12-17.0.parquet"
    assert p.parent == tmp_path / "botmap"


def test_cache_info_when_no_index(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    info = cache_info(latest_release="2025-12-17.0")
    assert info["index_path"] == str(index_path("2025-12-17.0"))
    assert info["index_release"] is None
    assert info["latest_release"] == "2025-12-17.0"
    assert info["up_to_date"] is False
    assert info["size_bytes"] == 0


def test_cache_info_with_stale_index(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    # Create a fake stale index file
    stale = index_path("2024-01-01.0")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"hello world")

    info = cache_info(latest_release="2025-12-17.0")
    assert info["index_release"] == "2024-01-01.0"
    assert info["up_to_date"] is False
    assert info["size_bytes"] == len(b"hello world")


def test_cache_info_with_current_index(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    current = index_path("2025-12-17.0")
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(b"x" * 100)

    info = cache_info(latest_release="2025-12-17.0")
    assert info["up_to_date"] is True
    assert info["index_release"] == "2025-12-17.0"
    assert info["size_bytes"] == 100


def test_clear_removes_all_index_files(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    a = index_path("2024-01-01.0")
    b = index_path("2025-12-17.0")
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_bytes(b"a")
    b.write_bytes(b"b")

    removed = clear_cache()
    assert removed == 2
    assert not a.exists()
    assert not b.exists()


def test_clear_when_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert clear_cache() == 0


import pyarrow as pa


class _FakeArrowFn:
    """Hold the return value the fake will produce."""

    def __init__(self, return_value):
        self.return_value = return_value
        self.calls: list = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.return_value


def _fake_division_table():
    # names.common matches the real Overture schema: map<string, string>.
    # All entries are None here to keep the fixture simple; the integration
    # test exercises real map values against S3 data.
    names_type = pa.struct([
        ("primary", pa.string()),
        ("common", pa.map_(pa.string(), pa.string())),
    ])
    bbox_type = pa.struct([
        ("xmin", pa.float64()), ("ymin", pa.float64()),
        ("xmax", pa.float64()), ("ymax", pa.float64()),
    ])
    return pa.table({
        "id": ["d1", "d2", "d3", "d4"],
        "names": pa.array(
            [
                {"primary": "Boston", "common": None},
                {"primary": "Cambridge", "common": None},
                {"primary": "Boston", "common": None},
                # d4: a microhood with only a point geometry — no division_area row
                {"primary": "Williamsburg", "common": None},
            ],
            type=names_type,
        ),
        "subtype": ["locality", "locality", "locality", "microhood"],
        "class": [None, None, None, None],
        "country": ["US", "US", "GB", "US"],
        "region": ["US-MA", "US-MA", "GB-LIN", "US-NY"],
        "admin_level": [8, 8, 8, None],
        "population": [654776, 118000, 41000, None],
        "parent_division_id": ["mass", "mass", "lincolnshire", "brooklyn"],
        # The division's own bbox (point-geometry for d4)
        "bbox": pa.array(
            [
                {"xmin": -71.19, "ymin": 42.23, "xmax": -70.99, "ymax": 42.40},
                {"xmin": -71.16, "ymin": 42.36, "xmax": -71.07, "ymax": 42.42},
                {"xmin":   0.00, "ymin": 53.00, "xmax":   0.20, "ymax": 53.10},
                {"xmin": -73.9535, "ymin": 40.7146, "xmax": -73.9535, "ymax": 40.7146},
            ],
            type=bbox_type,
        ),
    })


def _fake_division_area_table():
    bbox_type = pa.struct(
        [("xmin", pa.float64()), ("ymin", pa.float64()),
         ("xmax", pa.float64()), ("ymax", pa.float64())]
    )
    return pa.table({
        # Only d1–d3 have area polygons; d4 (microhood) has none.
        "division_id": ["d1", "d2", "d3"],
        "bbox": pa.array(
            [
                {"xmin": -71.19, "ymin": 42.23, "xmax": -70.99, "ymax": 42.40},
                {"xmin": -71.16, "ymin": 42.36, "xmax": -71.07, "ymax": 42.42},
                {"xmin":   0.00, "ymin": 53.00, "xmax":   0.20, "ymax": 53.10},
            ],
            type=bbox_type,
        ),
    })


def test_build_index_writes_joined_parquet(monkeypatch, tmp_path):
    from botmap import cache as cache_mod

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    # Stub out the S3 reads
    div_table = _fake_division_table()
    area_table = _fake_division_area_table()

    def fake_read_partition(theme, type_, release, columns):
        if type_ == "division":
            return div_table.select(columns)
        if type_ == "division_area":
            return area_table.select(columns)
        raise AssertionError(f"unexpected type {type_}")

    monkeypatch.setattr(cache_mod, "_read_partition_columns", fake_read_partition)

    out_path = cache_mod.build_index("2025-12-17.0")
    assert out_path.exists()
    assert out_path == cache_mod.index_path("2025-12-17.0")

    import pyarrow.parquet as pq
    table = pq.read_table(out_path)
    # All 4 rows present — including the microhood with no division_area
    assert table.num_rows == 4
    assert set(table.column_names) >= {
        "id", "name_primary", "subtype", "country",
        "region", "admin_level", "population", "parent_division_id",
        "bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax",
    }
    assert "name_common" not in table.column_names
    # own_* helpers must be dropped from the final output
    assert "own_xmin" not in table.column_names


def test_build_index_point_division_uses_own_bbox_with_buffer(monkeypatch, tmp_path):
    """A microhood with no division_area gets its point bbox buffered by ~1 km."""
    from botmap import cache as cache_mod
    import pyarrow.parquet as pq
    import pyarrow.compute as pc

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    div_table = _fake_division_table()
    area_table = _fake_division_area_table()

    def fake_read_partition(theme, type_, release, columns):
        return div_table.select(columns) if type_ == "division" else area_table.select(columns)

    monkeypatch.setattr(cache_mod, "_read_partition_columns", fake_read_partition)
    out_path = cache_mod.build_index("2025-12-17.0")

    table = pq.read_table(out_path)
    rows = table.filter(pc.equal(table.column("id"), "d4")).to_pydict()

    # Point was at -73.9535, 40.7146; buffer of 0.009 deg should be applied.
    assert rows["bbox_xmin"] == pytest.approx([-73.9535 - 0.009])
    assert rows["bbox_ymin"] == pytest.approx([40.7146 - 0.009])
    assert rows["bbox_xmax"] == pytest.approx([-73.9535 + 0.009])
    assert rows["bbox_ymax"] == pytest.approx([40.7146 + 0.009])


def test_build_index_polygon_division_not_buffered(monkeypatch, tmp_path):
    """Divisions with a real area polygon must not be expanded by the point buffer."""
    from botmap import cache as cache_mod
    import pyarrow.parquet as pq
    import pyarrow.compute as pc

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    div_table = _fake_division_table()
    area_table = _fake_division_area_table()

    def fake_read_partition(theme, type_, release, columns):
        return div_table.select(columns) if type_ == "division" else area_table.select(columns)

    monkeypatch.setattr(cache_mod, "_read_partition_columns", fake_read_partition)
    out_path = cache_mod.build_index("2025-12-17.0")

    table = pq.read_table(out_path)
    rows = table.filter(pc.equal(table.column("id"), "d1")).to_pydict()
    assert rows["bbox_xmin"] == pytest.approx([-71.19])
    assert rows["bbox_xmax"] == pytest.approx([-70.99])


def test_ensure_index_skips_when_current(monkeypatch, tmp_path):
    from botmap import cache as cache_mod

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    target = cache_mod.index_path("2025-12-17.0")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fake index")

    called = []
    monkeypatch.setattr(
        cache_mod, "build_index",
        lambda release: called.append(release) or target,
    )

    result = cache_mod.ensure_index("2025-12-17.0")
    assert result == target
    assert called == []  # no rebuild


def test_ensure_index_rebuilds_when_stale(monkeypatch, tmp_path):
    from botmap import cache as cache_mod

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    old = cache_mod.index_path("2024-01-01.0")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"old index")

    called = []
    def fake_build(release):
        called.append(release)
        p = cache_mod.index_path(release)
        p.write_bytes(b"new index")
        return p

    monkeypatch.setattr(cache_mod, "build_index", fake_build)

    result = cache_mod.ensure_index("2025-12-17.0")
    assert result == cache_mod.index_path("2025-12-17.0")
    assert called == ["2025-12-17.0"]
