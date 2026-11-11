"""Tests for terrain profiles, terrain summaries and obstacle hits (airbrief_core.analyses.terrain)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from shapely.geometry import LineString, Point, box

from airbrief_core.analyses.terrain import obstacle_hits, terrain_profile, terrain_summary
from airbrief_core.data import DataPack
from airbrief_core.errors import AnalysisError
from airbrief_core.geo.geodesic import geodesic_length
from seed.datapack import synthetic_elevation

SURVEY = box(77.48, 12.90, 77.50, 12.91)
FIXED_OBSTACLES = {"obs-0001": 65.0, "obs-0002": 42.0, "obs-0003": 48.0}
ROUTE = LineString([(77.47, 12.90), (77.51, 12.92)])


def test_terrain_profile_length_and_endpoints(synthetic_pack: DataPack) -> None:
    dem = synthetic_pack.dem()
    assert dem is not None
    spacing = 30.0
    profile = terrain_profile(ROUTE, dem, spacing)
    length = geodesic_length(ROUTE)
    expected = int(math.ceil(length / spacing * (1.0 - 1e-6))) + 1
    assert len(profile) == expected
    assert profile[0].distance_m == pytest.approx(0.0)
    assert profile[-1].distance_m == pytest.approx(length, abs=0.05)
    assert (profile[0].lon, profile[0].lat) == pytest.approx((77.47, 12.90))
    assert (profile[-1].lon, profile[-1].lat) == pytest.approx((77.51, 12.92))
    dists = np.array([p.distance_m for p in profile])
    assert np.all(np.diff(dists) > 0)
    assert np.max(np.diff(dists)) <= spacing + 0.01

    terrain = np.array([p.terrain_m for p in profile], dtype=float)
    truth = synthetic_elevation(np.array([p.lon for p in profile]), np.array([p.lat for p in profile]))
    assert np.all(np.isfinite(terrain))
    assert np.max(np.abs(terrain - truth)) < 1.0


def test_terrain_profile_without_dem() -> None:
    profile = terrain_profile(ROUTE, None, 500.0)
    assert len(profile) >= 2
    assert all(p.terrain_m is None for p in profile)


def test_terrain_profile_rejects_polygon_and_bad_spacing() -> None:
    with pytest.raises(AnalysisError) as exc:
        terrain_profile(SURVEY, None, 30.0)
    assert exc.value.code == "geometry_type_not_supported"
    with pytest.raises(AnalysisError) as exc:
        terrain_profile(ROUTE, None, 0.0)
    assert exc.value.code == "invalid_params"


def test_terrain_summary_survey_area(synthetic_pack: DataPack) -> None:
    summary = terrain_summary(SURVEY, synthetic_pack.dem())
    assert summary is not None
    assert summary.samples > 10
    assert summary.min_m <= summary.mean_m <= summary.max_m
    assert summary.relief_m == pytest.approx(summary.max_m - summary.min_m, abs=0.02)
    assert 850.0 < summary.min_m < 1000.0
    assert summary.source == "synthetic"


def test_terrain_summary_none_without_dem_or_outside(synthetic_pack: DataPack) -> None:
    assert terrain_summary(SURVEY, None) is None
    assert terrain_summary(box(10.0, 10.0, 10.01, 10.01), synthetic_pack.dem()) is None


def test_terrain_summary_point(synthetic_pack: DataPack) -> None:
    summary = terrain_summary(Point(77.49, 12.905), synthetic_pack.dem())
    assert summary is not None
    assert summary.samples == 1
    assert summary.relief_m == pytest.approx(0.0)
    assert summary.max_m == pytest.approx(float(synthetic_elevation(77.49, 12.905)), abs=1.0)


def _fixed(hits: list) -> dict:
    return {h.id: h for h in hits if h.id in FIXED_OBSTACLES}


def test_obstacle_hits_conflict_rule(synthetic_pack: DataPack) -> None:
    dem = synthetic_pack.dem()
    # Clearance 30 m: conflict when height + 30 > altitude.
    at_120 = _fixed(obstacle_hits(SURVEY, synthetic_pack, search_m=0.0, altitude_agl_m=120.0,
                                  vertical_clearance_m=30.0, dem=dem))
    assert set(at_120) == set(FIXED_OBSTACLES)
    assert not any(h.conflict for h in at_120.values())  # 65 + 30 = 95 <= 120

    at_90 = _fixed(obstacle_hits(SURVEY, synthetic_pack, search_m=0.0, altitude_agl_m=90.0,
                                 vertical_clearance_m=30.0, dem=dem))
    assert {k for k, h in at_90.items() if h.conflict} == {"obs-0001"}  # only the 65 m mast

    at_60 = _fixed(obstacle_hits(SURVEY, synthetic_pack, search_m=0.0, altitude_agl_m=60.0,
                                 vertical_clearance_m=30.0, dem=dem))
    assert all(h.conflict for h in at_60.values())

    for oid, hit in at_60.items():
        assert hit.height_m == pytest.approx(FIXED_OBSTACLES[oid])
        assert hit.clearance_m == pytest.approx(60.0 - FIXED_OBSTACLES[oid])
        assert hit.distance_m == pytest.approx(0.0)
        assert hit.base_m is not None
        assert hit.base_m == pytest.approx(float(synthetic_elevation(hit.lon, hit.lat)), abs=1.0)
        assert hit.top_amsl_m == pytest.approx(hit.base_m + hit.height_m)


def test_obstacle_hits_ordering_limit_and_no_dem(synthetic_pack: DataPack) -> None:
    hits = obstacle_hits(SURVEY, synthetic_pack, search_m=300.0, altitude_agl_m=90.0, vertical_clearance_m=30.0)
    flags = [h.conflict for h in hits]
    assert flags == sorted(flags, reverse=True)  # conflicts first
    assert all(h.base_m is None and h.top_amsl_m is None for h in hits)
    assert all(h.distance_m <= 300.0 + 1e-6 for h in hits)
    assert len(obstacle_hits(SURVEY, synthetic_pack, search_m=300.0, altitude_agl_m=90.0,
                             vertical_clearance_m=30.0, limit=1)) == 1
    assert obstacle_hits(SURVEY, None, search_m=300.0, altitude_agl_m=90.0, vertical_clearance_m=30.0) == []
