"""Shared helpers for the intent verbs (places, buildings, roads, at, containing)."""

from __future__ import annotations

import math

# Meters per degree at the equator.
_M_PER_DEG = 111_320.0


def bbox_around_point(
    lat: float, lon: float, radius_meters: float
) -> tuple[float, float, float, float]:
    """Return (xmin, ymin, xmax, ymax) for a bbox around a point.

    Note: caller passes lat then lon (geographic convention) but bbox is
    returned in lon/lat order to match the rest of the codebase.
    """
    dlat = radius_meters / _M_PER_DEG
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    dlon = radius_meters / (_M_PER_DEG * cos_lat)
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points in meters."""
    R = 6_371_000  # mean Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


DEFAULT_RADIUS_BY_TYPE = {
    "place": 100,
    "building": 50,
    "address": 25,
}
