"""Resolve place names to Overture division features via the local index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pyarrow.compute as pc
import pyarrow.parquet as pq

from .cache import ensure_index


@dataclass(frozen=True)
class Division:
    id: str
    name: str
    subtype: str
    country: Optional[str]
    region: Optional[str]
    admin_level: int
    population: Optional[int]
    parent_division_id: Optional[str]
    bbox: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "subtype": self.subtype,
            "country": self.country,
            "region": self.region,
            "admin_level": self.admin_level,
            "population": self.population,
            "parent_division_id": self.parent_division_id,
            "bbox": list(self.bbox),
        }


def _latest_release() -> str:
    # Imported lazily so tests can monkeypatch without S3 calls.
    from .core import get_latest_release
    return get_latest_release()


def _parse_query(query: str) -> tuple[str, list[str]]:
    """Split 'Boston, US-MA' into ('Boston', ['US-MA'])."""
    parts = [p.strip() for p in query.split(",")]
    name = parts[0]
    qualifiers = [p for p in parts[1:] if p]
    return name, qualifiers


def _load_index_table():
    release = _latest_release()
    path = ensure_index(release)
    return pq.read_table(path)


def resolve(query: str) -> List[Division]:
    """Return all divisions matching the query (name + optional qualifiers).

    Returns a list ordered by best-match-first: higher admin_level
    (innermost area) preferred, then higher population.
    """
    name, qualifiers = _parse_query(query)
    table = _load_index_table()

    # Case-insensitive equality on name_primary only.
    # name_common was dropped from the index due to PyArrow join constraints
    # on map<string,string> non-key fields (see commit 2041f45).
    name_lower = name.lower()
    primary = pc.utf8_lower(table.column("name_primary"))
    name_match = pc.equal(primary, name_lower)

    filtered = table.filter(name_match)

    # Apply qualifiers: each must match country code (2 chars) or region code
    for q in qualifiers:
        country_match = pc.equal(filtered.column("country"), q)
        region_match = pc.equal(filtered.column("region"), q)
        filtered = filtered.filter(pc.or_(country_match, region_match))

    if filtered.num_rows == 0:
        return []

    # Sort: admin_level desc (innermost first) then population desc
    sort_indices = pc.sort_indices(
        filtered,
        sort_keys=[("admin_level", "descending"), ("population", "descending")],
    )
    filtered = filtered.take(sort_indices)

    rows = filtered.to_pylist()
    return [
        Division(
            id=r["id"],
            name=r["name_primary"],
            subtype=r["subtype"],
            country=r["country"],
            region=r["region"],
            admin_level=r["admin_level"],
            population=r["population"],
            parent_division_id=r["parent_division_id"],
            bbox=(r["bbox_xmin"], r["bbox_ymin"], r["bbox_xmax"], r["bbox_ymax"]),
        )
        for r in rows
    ]


def best_match(query: str) -> Division:
    """Return the single best match for the query. Raises LookupError on no match."""
    results = resolve(query)
    if not results:
        raise LookupError(f"No division found for {query!r}")
    return results[0]
