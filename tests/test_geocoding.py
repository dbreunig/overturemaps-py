"""Tests for the geocoding lookup."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from overturemaps.geocoding import resolve, best_match, Division


def _build_index_file(tmp_path: Path) -> Path:
    """Write a tiny fake divisions index parquet at the expected location."""
    table = pa.table({
        "id": [
            "us", "ma", "boston-ma", "boston-uk", "cambridge-ma", "cambridge-uk",
            "walnut-creek-ca",
        ],
        "name_primary": [
            "United States", "Massachusetts", "Boston", "Boston",
            "Cambridge", "Cambridge",
            "Walnut Creek",
        ],
        "subtype": [
            "country", "region", "locality", "locality", "locality", "locality",
            "locality",
        ],
        "class": [None, None, None, None, None, None, None],
        "country": ["US", "US", "US", "GB", "US", "GB", "US"],
        "region": [None, "US-MA", "US-MA", "GB-LIN", "US-MA", "GB-CAM", "US-CA"],
        "admin_level": [2, 4, 8, 8, 8, 8, 8],
        "population": [330000000, 7000000, 654776, 41000, 118000, 145000, 70000],
        "parent_division_id": [
            None, "us", "ma", "lincolnshire", "ma", "cambridgeshire", "ca",
        ],
        "bbox_xmin": [-180.0, -73.5, -71.19, 0.00, -71.16, 0.10, -122.10],
        "bbox_ymin": [18.0, 41.2, 42.23, 53.00, 42.36, 52.18, 37.86],
        "bbox_xmax": [-66.0, -69.9, -70.99, 0.20, -71.07, 0.20, -121.97],
        "bbox_ymax": [71.0, 42.9, 42.40, 53.10, 42.42, 52.22, 37.94],
    })
    out = tmp_path / "overturemaps" / "divisions-index-2025-12-17.0.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out)
    return out


@pytest.fixture
def fake_index(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _build_index_file(tmp_path)
    # Skip the network lookup that ensure_index would do
    monkeypatch.setattr(
        "overturemaps.geocoding._latest_release", lambda: "2025-12-17.0",
    )
    return tmp_path


def test_resolve_exact_match(fake_index):
    results = resolve("Massachusetts")
    assert len(results) == 1
    assert results[0].name == "Massachusetts"
    assert results[0].subtype == "region"


def test_resolve_case_insensitive(fake_index):
    results = resolve("MASSACHUSETTS")
    assert len(results) == 1


def test_resolve_returns_all_ambiguous_matches(fake_index):
    results = resolve("Boston")
    assert len(results) == 2
    assert all(r.name == "Boston" for r in results)


def test_best_match_prefers_higher_population_on_tie(fake_index):
    # Both Bostons have admin_level=8; pick by population
    pick = best_match("Boston")
    assert pick.region == "US-MA"
    assert pick.population == 654776


def test_best_match_country_disambiguator(fake_index):
    pick = best_match("Boston, GB")
    assert pick.region == "GB-LIN"


def test_best_match_region_disambiguator(fake_index):
    pick = best_match("Boston, US-MA")
    assert pick.region == "US-MA"


def test_best_match_admin_level_tiebreak_innermost_wins(fake_index):
    # "United States" has admin_level=2 (largest area). If we somehow had a
    # nested "United States" locality, admin_level should pick the innermost.
    # Here we just confirm a single match is returned for "United States".
    pick = best_match("United States")
    assert pick.subtype == "country"


def test_best_match_no_match_raises(fake_index):
    with pytest.raises(LookupError):
        best_match("Nonexistentville")


def test_division_bbox_property(fake_index):
    pick = best_match("Massachusetts")
    assert pick.bbox == (-73.5, 41.2, -69.9, 42.9)


def test_resolve_region_suffix_qualifier(fake_index):
    """'Boston, MA' should match the US-MA region (the primary documented form)."""
    results = resolve("Boston, MA")
    assert len(results) == 1
    assert results[0].region == "US-MA"


def test_best_match_short_region_qualifier(fake_index):
    """'Boston, MA' produces the same pick as 'Boston, US-MA'."""
    pick_short = best_match("Boston, MA")
    pick_full = best_match("Boston, US-MA")
    assert pick_short.id == pick_full.id


def test_best_match_country_alpha3_alias(fake_index):
    """'Walnut Creek, CA, USA' should map USA -> US."""
    pick = best_match("Walnut Creek, CA, USA")
    assert pick.region == "US-CA"
    assert pick.country == "US"


def test_best_match_country_full_name(fake_index):
    """Full country names like 'United States' should normalize."""
    pick = best_match("Walnut Creek, CA, United States")
    assert pick.region == "US-CA"


def test_best_match_country_uk_alias(fake_index):
    """'UK' should map to 'GB'."""
    pick = best_match("Boston, UK")
    assert pick.region == "GB-LIN"


def test_best_match_unknown_qualifier_rejects(fake_index):
    """An unknown qualifier should still narrow (and reject) — not be silently dropped."""
    with pytest.raises(LookupError):
        best_match("Boston, ZZ")
