"""Tests for the divisions-index cache."""

import os
from pathlib import Path

import pytest

from overturemaps.cache import (
    cache_dir,
    index_path,
    cache_info,
    clear_cache,
)


def test_cache_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache_dir() == tmp_path / "overturemaps"


def test_cache_dir_fallback_when_xdg_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".cache" / "overturemaps"
    assert cache_dir() == expected


def test_index_path_includes_release(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    p = index_path("2025-12-17.0")
    assert p.name == "divisions-index-2025-12-17.0.parquet"
    assert p.parent == tmp_path / "overturemaps"


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
    return pa.table({
        "id": ["d1", "d2", "d3"],
        "names": pa.array(
            [
                {"primary": "Boston", "common": None},
                {"primary": "Cambridge", "common": None},
                {"primary": "Boston", "common": None},
            ],
            type=pa.struct([("primary", pa.string()), ("common", pa.string())]),
        ),
        "subtype": ["locality", "locality", "locality"],
        "class": [None, None, None],
        "country": ["US", "US", "GB"],
        "region": ["US-MA", "US-MA", "GB-LIN"],
        "admin_level": [8, 8, 8],
        "population": [654776, 118000, 41000],
        "parent_division_id": ["mass", "mass", "lincolnshire"],
    })


def _fake_division_area_table():
    bbox_type = pa.struct(
        [("xmin", pa.float64()), ("ymin", pa.float64()),
         ("xmax", pa.float64()), ("ymax", pa.float64())]
    )
    return pa.table({
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
    from overturemaps import cache as cache_mod

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
    # 3 input rows, all joined successfully
    assert table.num_rows == 3
    assert set(table.column_names) >= {
        "id", "name_primary", "name_common", "subtype", "country",
        "region", "admin_level", "population", "parent_division_id",
        "bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax",
    }


def test_ensure_index_skips_when_current(monkeypatch, tmp_path):
    from overturemaps import cache as cache_mod

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
    from overturemaps import cache as cache_mod

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
