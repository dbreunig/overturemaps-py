"""Tests for the `where` command."""

import json

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


_BOSTON = Division(
    id="boston-ma", name="Boston", subtype="locality",
    country="US", region="US-MA",
    admin_level=8, population=654776, parent_division_id="ma",
    bbox=(-71.19, 42.23, -70.99, 42.40),
)


@pytest.fixture
def fake_match(monkeypatch):
    captured = {}

    def fake_resolve(query):
        captured["query"] = query
        return [_BOSTON]

    monkeypatch.setattr("overturemaps.cli.resolve", fake_resolve)
    return captured


def test_where_human_output(fake_match):
    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Boston, MA"])
    assert result.exit_code == 0
    assert "Boston" in result.output
    assert "US-MA" in result.output
    assert "654776" in result.output or "654,776" in result.output


def test_where_json_output(fake_match):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "where", "Boston, MA"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["name"] == "Boston"
    assert data["region"] == "US-MA"
    assert data["bbox"] == [-71.19, 42.23, -70.99, 42.40]
    assert "candidates" in data


def test_where_no_match(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.resolve", lambda q: [])
    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Nonexistentville"])
    assert result.exit_code != 0


def test_where_no_match_json(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.resolve", lambda q: [])
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "where", "Nonexistentville"])
    assert result.exit_code != 0
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "no_match" in combined or "No match" in combined


_WILLIAMSBURG_VA = Division(
    id="williamsburg-va", name="Williamsburg", subtype="locality",
    country="US", region="US-VA",
    admin_level=8, population=15425, parent_division_id=None,
    bbox=(-76.75, 37.25, -76.66, 37.32),
)


def test_where_no_match_suggests_bare_name(monkeypatch):
    """A neighborhood-qualified query that fails should name the bare-name
    candidate that does resolve, so the agent has a recovery path."""
    def fake_resolve(q):
        return [_WILLIAMSBURG_VA] if q == "Williamsburg" else []

    monkeypatch.setattr("overturemaps.cli.resolve", fake_resolve)
    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Williamsburg, Brooklyn"])
    assert result.exit_code != 0
    # Names the resolvable parent candidate and offers recovery paths.
    assert "Williamsburg" in result.output
    assert "US-VA" in result.output
    assert "--bbox" in result.output


def test_where_no_match_json_includes_suggestion(monkeypatch):
    def fake_resolve(q):
        return [_WILLIAMSBURG_VA] if q == "Williamsburg" else []

    monkeypatch.setattr("overturemaps.cli.resolve", fake_resolve)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "where", "Williamsburg, Brooklyn"])
    assert result.exit_code != 0
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "no_match" in combined
    assert "Williamsburg" in combined


def test_where_geometry_emits_geojson_feature(monkeypatch):
    """`where --geometry` emits the division_area polygon as a GeoJSON Feature."""
    from shapely.geometry import box

    monkeypatch.setattr("overturemaps.cli.resolve", lambda q: [_BOSTON])
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    def fake_prefetch(ids, lon, lat, release):
        import overturemaps.cli as c
        c._polygon_cache[ids[0]] = box(-71.19, 42.23, -70.99, 42.40)

    monkeypatch.setattr("overturemaps.cli._prefetch_polygons", fake_prefetch)

    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Boston, MA", "--geometry"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["type"] == "Feature"
    assert data["geometry"]["type"] == "Polygon"
    assert data["properties"]["name"] == "Boston"
    assert data["properties"]["subtype"] == "locality"


def test_where_geojson_alias_flag(monkeypatch):
    """`--geojson` is an accepted alias for `--geometry`."""
    from shapely.geometry import box

    monkeypatch.setattr("overturemaps.cli.resolve", lambda q: [_BOSTON])
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    def fake_prefetch(ids, lon, lat, release):
        import overturemaps.cli as c
        c._polygon_cache[ids[0]] = box(-71.19, 42.23, -70.99, 42.40)

    monkeypatch.setattr("overturemaps.cli._prefetch_polygons", fake_prefetch)

    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Boston, MA", "--geojson"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["type"] == "Feature"


def test_where_geometry_no_polygon_errors(monkeypatch):
    """When no division_area polygon exists, --geometry errors cleanly."""
    monkeypatch.setattr("overturemaps.cli.resolve", lambda q: [_BOSTON])
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    def fake_prefetch(ids, lon, lat, release):
        import overturemaps.cli as c
        c._polygon_cache[ids[0]] = None

    monkeypatch.setattr("overturemaps.cli._prefetch_polygons", fake_prefetch)

    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Boston, MA", "--geometry"])
    assert result.exit_code != 0


def test_where_ambiguous_shows_hint(monkeypatch):
    """When multiple matches exist, the human view hints at --all."""
    alameda_ca = Division(
        id="alameda-ca", name="Alameda", subtype="locality",
        country="US", region="US-CA",
        admin_level=8, population=78280, parent_division_id=None,
        bbox=(-122.34, 37.71, -122.21, 37.79),
    )
    alameda_sk = Division(
        id="alameda-sk", name="Alameda", subtype="region",
        country="CA", region="CA-SK",
        admin_level=4, population=None, parent_division_id=None,
        bbox=(-102.3, 49.2, -102.2, 49.3),
    )
    monkeypatch.setattr(
        "overturemaps.cli.resolve",
        lambda q: [alameda_ca, alameda_sk],
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Alameda, CA"])
    assert result.exit_code == 0
    assert "Alameda" in result.output
    assert "US-CA" in result.output
    assert "--all" in result.output  # hint to disambiguate
    # By default only the first match is shown in detail
    assert "CA-SK" not in result.output


def test_where_all_lists_every_match(monkeypatch):
    """`where --all` shows every candidate."""
    alameda_ca = Division(
        id="alameda-ca", name="Alameda", subtype="locality",
        country="US", region="US-CA",
        admin_level=8, population=78280, parent_division_id=None,
        bbox=(-122.34, 37.71, -122.21, 37.79),
    )
    alameda_sk = Division(
        id="alameda-sk", name="Alameda", subtype="region",
        country="CA", region="CA-SK",
        admin_level=4, population=None, parent_division_id=None,
        bbox=(-102.3, 49.2, -102.2, 49.3),
    )
    monkeypatch.setattr(
        "overturemaps.cli.resolve",
        lambda q: [alameda_ca, alameda_sk],
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Alameda, CA", "--all"])
    assert result.exit_code == 0
    assert "[1]" in result.output
    assert "[2]" in result.output
    assert "US-CA" in result.output
    assert "CA-SK" in result.output
