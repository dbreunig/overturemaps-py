"""Tests for intent-verb helpers."""

import math

import pytest

from overturemaps.intents import bbox_around_point, haversine_meters


class TestBboxAroundPoint:
    def test_small_radius_at_equator(self):
        bbox = bbox_around_point(0.0, 0.0, radius_meters=111320)
        # 1 deg at the equator ~= 111.32 km. Radius of 111320 m -> bbox ~= 1 deg each side.
        assert abs((bbox[2] - bbox[0]) - 2.0) < 0.05
        assert abs((bbox[3] - bbox[1]) - 2.0) < 0.05

    def test_radius_shrinks_with_latitude(self):
        bbox_equator = bbox_around_point(0.0, 0.0, radius_meters=10000)
        bbox_polar = bbox_around_point(60.0, 0.0, radius_meters=10000)
        eq_width = bbox_equator[2] - bbox_equator[0]
        polar_width = bbox_polar[2] - bbox_polar[0]
        # At 60° N, cos(60°) = 0.5, so longitude span doubles for the same radius
        assert polar_width > 1.5 * eq_width


class TestHaversineMeters:
    def test_zero_distance(self):
        assert haversine_meters(42.0, -71.0, 42.0, -71.0) == 0

    def test_known_distance_short(self):
        # Approximately 111 m for 0.001 degrees of latitude at the equator
        d = haversine_meters(0.0, 0.0, 0.001, 0.0)
        assert 100 < d < 130

    def test_symmetry(self):
        d1 = haversine_meters(42.0, -71.0, 42.5, -70.5)
        d2 = haversine_meters(42.5, -70.5, 42.0, -71.0)
        assert math.isclose(d1, d2)
