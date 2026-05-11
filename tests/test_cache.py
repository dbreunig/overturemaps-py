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
