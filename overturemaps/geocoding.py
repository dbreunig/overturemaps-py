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


# ISO-3166-1 alpha-3 → alpha-2 for the most common country names users type.
# Overture stores country as alpha-2 ("US", "GB"...), so a qualifier like
# "USA" or "United States" must be normalized before matching.
_COUNTRY_ALIASES = {
    "USA": "US", "UNITED STATES": "US", "U.S.": "US", "U.S.A.": "US", "AMERICA": "US",
    "UK": "GB", "GBR": "GB", "BRITAIN": "GB", "GREAT BRITAIN": "GB",
    "UNITED KINGDOM": "GB", "ENGLAND": "GB",
    "CAN": "CA", "CANADA": "CA",
    "MEX": "MX", "MEXICO": "MX",
    "DEU": "DE", "GERMANY": "DE",
    "FRA": "FR", "FRANCE": "FR",
    "ITA": "IT", "ITALY": "IT",
    "ESP": "ES", "SPAIN": "ES",
    "PRT": "PT", "PORTUGAL": "PT",
    "NLD": "NL", "NETHERLANDS": "NL", "HOLLAND": "NL",
    "BEL": "BE", "BELGIUM": "BE",
    "CHE": "CH", "SWITZERLAND": "CH",
    "AUT": "AT", "AUSTRIA": "AT",
    "POL": "PL", "POLAND": "PL",
    "SWE": "SE", "SWEDEN": "SE",
    "NOR": "NO", "NORWAY": "NO",
    "DNK": "DK", "DENMARK": "DK",
    "FIN": "FI", "FINLAND": "FI",
    "IRL": "IE", "IRELAND": "IE",
    "ISL": "IS", "ICELAND": "IS",
    "RUS": "RU", "RUSSIA": "RU",
    "CHN": "CN", "CHINA": "CN",
    "JPN": "JP", "JAPAN": "JP",
    "KOR": "KR", "SOUTH KOREA": "KR", "KOREA": "KR",
    "IND": "IN", "INDIA": "IN",
    "AUS": "AU", "AUSTRALIA": "AU",
    "NZL": "NZ", "NEW ZEALAND": "NZ",
    "BRA": "BR", "BRAZIL": "BR",
    "ARG": "AR", "ARGENTINA": "AR",
    "CHL": "CL", "CHILE": "CL",
    "COL": "CO", "COLOMBIA": "CO",
    "ZAF": "ZA", "SOUTH AFRICA": "ZA",
    "EGY": "EG", "EGYPT": "EG",
    "TUR": "TR", "TURKEY": "TR", "TÜRKIYE": "TR",
    "ISR": "IL", "ISRAEL": "IL",
    "ARE": "AE", "UAE": "AE", "EMIRATES": "AE",
    "SAU": "SA", "SAUDI ARABIA": "SA",
    "THA": "TH", "THAILAND": "TH",
    "VNM": "VN", "VIETNAM": "VN",
    "IDN": "ID", "INDONESIA": "ID",
    "PHL": "PH", "PHILIPPINES": "PH",
    "MYS": "MY", "MALAYSIA": "MY",
    "SGP": "SG", "SINGAPORE": "SG",
}


def _normalize_qualifier(q: str) -> str:
    """Apply common aliases so 'USA' or 'United States' map to 'US'."""
    return _COUNTRY_ALIASES.get(q.strip().upper(), q)


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

    # Apply qualifiers: each must match country code (2 chars), full region
    # code (e.g. "US-MA"), or the suffix after the hyphen (e.g. "MA" -> "US-MA").
    # Common country aliases (USA, UK, France, ...) are normalized to alpha-2.
    for q in qualifiers:
        normalized = _normalize_qualifier(q)
        country_match = pc.equal(filtered.column("country"), normalized)
        region_match = pc.equal(filtered.column("region"), normalized)
        region_suffix_match = pc.ends_with(filtered.column("region"), f"-{normalized}")
        filtered = filtered.filter(
            pc.or_(pc.or_(country_match, region_match), region_suffix_match)
        )

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
