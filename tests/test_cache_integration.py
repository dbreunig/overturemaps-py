"""Integration tests for cache: hit real S3, build a real index."""

import pytest

from overturemaps.cache import build_index, index_path
from overturemaps.core import get_latest_release

pytestmark = pytest.mark.integration


def test_build_real_index(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    release = get_latest_release()
    p = build_index(release)
    assert p == index_path(release)
    assert p.stat().st_size > 0

    import pyarrow.parquet as pq
    t = pq.read_table(p)
    # Should contain at least one country-level entry
    assert t.num_rows > 100
    assert "bbox_xmin" in t.column_names
