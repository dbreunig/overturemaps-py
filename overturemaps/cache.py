"""On-disk cache for the divisions geocoding index."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as _fs
import pyarrow.parquet as pq


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


_S3_BUCKET = "overturemaps-us-west-2"


def _read_partition_columns(theme: str, type_: str, release: str, columns: list[str]) -> pa.Table:
    """Read selected columns from a divisions partition on S3."""
    path = f"{_S3_BUCKET}/release/{release}/theme={theme}/type={type_}/"
    fs = _fs.S3FileSystem(anonymous=True, region="us-west-2")
    dataset = ds.dataset(path, filesystem=fs)
    return dataset.to_table(columns=columns)


def build_index(release: str) -> Path:
    """Read divisions data from S3 for `release` and write the local index parquet."""
    div_cols = [
        "id", "names", "subtype", "class", "country", "region",
        "admin_level", "population", "parent_division_id",
    ]
    area_cols = ["division_id", "bbox"]

    div = _read_partition_columns("divisions", "division", release, div_cols)
    area = _read_partition_columns("divisions", "division_area", release, area_cols)

    # Flatten names struct -> name_primary only.
    # names.common is map<string, string> (language -> localized name) in real
    # Overture data; PyArrow's join backend rejects map and list types as
    # non-key fields.  v1 of the index therefore matches only against
    # name_primary.  Common-name match coverage can be revisited if user
    # feedback warrants the complexity.
    names_col = div.column("names").combine_chunks()
    name_primary = pc.struct_field(names_col, "primary")

    # Each division can have multiple division_area rows; combine to a single
    # bbox by taking min(xmin), min(ymin), max(xmax), max(ymax) per division_id.
    bbox_struct = area.column("bbox").combine_chunks()
    area_flat = pa.table({
        "division_id": area.column("division_id"),
        "xmin": pc.struct_field(bbox_struct, "xmin"),
        "ymin": pc.struct_field(bbox_struct, "ymin"),
        "xmax": pc.struct_field(bbox_struct, "xmax"),
        "ymax": pc.struct_field(bbox_struct, "ymax"),
    })
    bbox_agg = area_flat.group_by("division_id").aggregate([
        ("xmin", "min"),
        ("ymin", "min"),
        ("xmax", "max"),
        ("ymax", "max"),
    ]).rename_columns([
        "division_id", "bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax",
    ])

    # Build the flat division table (without the names struct).
    # Cast any null-typed columns to string so the join backend accepts them.
    def _as_string(col: pa.ChunkedArray) -> pa.ChunkedArray:
        if pa.types.is_null(col.type):
            return col.cast(pa.string())
        return col

    div_flat = pa.table({
        "id": div.column("id"),
        "name_primary": name_primary,
        "subtype": _as_string(div.column("subtype")),
        "class": _as_string(div.column("class")),
        "country": _as_string(div.column("country")),
        "region": _as_string(div.column("region")),
        "admin_level": div.column("admin_level"),
        "population": div.column("population"),
        "parent_division_id": _as_string(div.column("parent_division_id")),
    })

    # Inner join on id <-> division_id
    joined = div_flat.join(bbox_agg, keys="id", right_keys="division_id", join_type="inner")

    out = index_path(release)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(joined, out)
    return out


def ensure_index(latest_release: str) -> Path:
    """Return the path to a current index, building it if missing or stale."""
    target = index_path(latest_release)
    if target.exists():
        return target
    return build_index(latest_release)
