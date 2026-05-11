"""On-disk cache for the divisions geocoding index."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


_INDEX_FILE_RE = re.compile(r"^divisions-index-(.+)\.parquet$")


def cache_dir() -> Path:
    """Return the overturemaps cache directory, respecting XDG_CACHE_HOME."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "overturemaps"
    return Path(os.environ.get("HOME", "~")).expanduser() / ".cache" / "overturemaps"


def index_path(release: str) -> Path:
    """Path to the divisions-index file for a given release."""
    return cache_dir() / f"divisions-index-{release}.parquet"


def _scan_existing_indexes() -> list[tuple[str, Path]]:
    """Return (release, path) for every divisions-index file present on disk."""
    d = cache_dir()
    if not d.exists():
        return []
    out: list[tuple[str, Path]] = []
    for entry in d.iterdir():
        if not entry.is_file():
            continue
        m = _INDEX_FILE_RE.match(entry.name)
        if m:
            out.append((m.group(1), entry))
    return out


def cache_info(latest_release: Optional[str] = None) -> dict:
    """Return a JSON-serializable summary of the current cache state."""
    indexes = _scan_existing_indexes()
    if indexes:
        # Pick the newest (alphabetical sort is fine: release IDs are date-prefixed)
        indexes.sort(key=lambda pair: pair[0], reverse=True)
        current_release, current_path = indexes[0]
        size = current_path.stat().st_size
    else:
        current_release = None
        size = 0

    up_to_date = (
        current_release is not None
        and latest_release is not None
        and current_release == latest_release
    )

    # `index_path` reports where the *current* (latest) index *would* live.
    target_release = latest_release or current_release or ""
    return {
        "index_path": str(index_path(target_release)) if target_release else str(cache_dir()),
        "index_release": current_release,
        "latest_release": latest_release,
        "up_to_date": up_to_date,
        "size_bytes": size,
    }


def clear_cache() -> int:
    """Remove every divisions-index file. Returns the number of files removed."""
    indexes = _scan_existing_indexes()
    for _release, path in indexes:
        path.unlink()
    return len(indexes)
