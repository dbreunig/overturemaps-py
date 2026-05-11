"""Tests for the introspection module."""

import pyarrow as pa
import pytest

from overturemaps.introspection import (
    list_themes,
    list_types,
    flatten_schema,
    THEME_DESCRIPTIONS,
    TYPE_DESCRIPTIONS,
)


def test_list_themes_returns_six():
    themes = list_themes()
    assert len(themes) == 6
    names = {t["name"] for t in themes}
    assert names == {
        "addresses", "base", "buildings",
        "divisions", "places", "transportation",
    }


def test_list_themes_has_descriptions():
    themes = list_themes()
    for t in themes:
        assert "description" in t
        assert "types" in t
        assert isinstance(t["types"], list)


def test_list_types_full():
    types = list_types()
    assert len(types) == 15
    names = {t["name"] for t in types}
    assert names == {
        "address", "bathymetry", "building", "building_part",
        "division", "division_area", "division_boundary",
        "place", "segment", "connector",
        "infrastructure", "land", "land_cover", "land_use", "water",
    }


def test_list_types_filtered_by_theme():
    types = list_types(theme="buildings")
    assert {t["name"] for t in types} == {"building", "building_part"}


def test_list_types_unknown_theme_raises():
    with pytest.raises(ValueError):
        list_types(theme="not-a-theme")


def test_every_type_has_a_description():
    types = list_types()
    for t in types:
        assert t["description"], f"Missing description for {t['name']}"


def test_flatten_schema_top_level():
    schema = pa.schema([
        ("id", pa.string()),
        ("height", pa.float64()),
    ])
    fields = flatten_schema(schema)
    assert {"name": "id", "type": "string"} in fields
    assert {"name": "height", "type": "double"} in fields


def test_flatten_schema_nested():
    schema = pa.schema([
        ("categories", pa.struct([
            ("primary", pa.string()),
            ("alternate", pa.list_(pa.string())),
        ])),
    ])
    fields = flatten_schema(schema)
    names = {f["name"] for f in fields}
    assert "categories.primary" in names
    assert "categories.alternate" in names
