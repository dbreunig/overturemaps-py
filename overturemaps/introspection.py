"""Static catalog of Overture themes and types, plus runtime schema helpers."""

from __future__ import annotations

from typing import List, Optional

import pyarrow as pa

from .core import type_theme_map, get_all_overture_types


THEME_DESCRIPTIONS: dict[str, str] = {
    "addresses": "Address features with street, number, postcode, and country.",
    "base": "Base layers: land, water, land use/cover, infrastructure, bathymetry.",
    "buildings": "Building footprints with height, floors, class, and roof attributes.",
    "divisions": "Administrative divisions (countries, regions, counties, localities) and their polygons.",
    "places": "Categorized point features for businesses, services, and amenities (POIs).",
    "transportation": "Road network as segments with class, surface, speed limits, and connector junctions.",
}


TYPE_DESCRIPTIONS: dict[str, str] = {
    "address": "Address point with street, number, postcode, country code.",
    "bathymetry": "Underwater terrain features.",
    "building": "Building footprint with height, floor count, class, subtype.",
    "building_part": "Sub-component of a building when it has variable height/material.",
    "division": "Point representation of an administrative division (country, region, locality...).",
    "division_area": "Polygon area for a division.",
    "division_boundary": "Linear boundary between divisions.",
    "place": "Categorized POI with names, brand, categories, contact info.",
    "segment": "Road or rail segment with class, subclass, surface, speed limits.",
    "connector": "Junction or endpoint where segments meet.",
    "infrastructure": "Linear or point infrastructure features (bridges, tunnels, towers, etc.).",
    "land": "Natural land features (forest, beach, glacier, etc.).",
    "land_cover": "Land cover surface (forest, grassland, water, etc.).",
    "land_use": "Predominant human use of an area (commercial, residential, recreation, etc.).",
    "water": "Water bodies (river, lake, ocean).",
}


def list_themes() -> List[dict]:
    """List the six themes with descriptions and member types."""
    out = []
    for theme in sorted(THEME_DESCRIPTIONS.keys()):
        members = sorted(t for t, th in type_theme_map.items() if th == theme)
        out.append({
            "name": theme,
            "description": THEME_DESCRIPTIONS[theme],
            "types": members,
        })
    return out


def list_types(theme: Optional[str] = None) -> List[dict]:
    """List all types, optionally filtered to a single theme."""
    if theme is not None and theme not in THEME_DESCRIPTIONS:
        raise ValueError(
            f"Unknown theme {theme!r}. Available: "
            f"{', '.join(sorted(THEME_DESCRIPTIONS.keys()))}"
        )
    out = []
    for type_name in sorted(get_all_overture_types()):
        type_theme = type_theme_map[type_name]
        if theme is not None and type_theme != theme:
            continue
        out.append({
            "name": type_name,
            "theme": type_theme,
            "description": TYPE_DESCRIPTIONS.get(type_name, ""),
        })
    return out


def flatten_schema(schema: pa.Schema) -> List[dict]:
    """Flatten a (possibly nested) Arrow schema into a list of dotted field rows.

    Each row is {"name": "categories.primary", "type": "string"}.
    """
    out: List[dict] = []

    def _walk(prefix: str, type_):
        if pa.types.is_struct(type_):
            for i in range(type_.num_fields):
                child = type_.field(i)
                _walk(f"{prefix}.{child.name}" if prefix else child.name, child.type)
        elif pa.types.is_list(type_) or pa.types.is_large_list(type_):
            out.append({"name": prefix, "type": str(type_)})
        else:
            out.append({"name": prefix, "type": str(type_)})

    for field in schema:
        _walk(field.name, field.type)
    return out
