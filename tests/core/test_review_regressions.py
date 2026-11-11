"""Regression tests for code-review findings in the core (shows, zones, processing)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from shapely.geometry import LineString, Point

from airbrief_core import _native
from airbrief_core.analyses import zones
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.shows import MAX_CLOSE_PAIRS
from airbrief_core.analyses.shows import run as run_show
from airbrief_core.data.aerodromes import AerodromeIndex
from airbrief_core.errors import AnalysisError
from airbrief_core.models.analyses import ShowParams
from airbrief_core.models.common import UserZone
from airbrief_core.models.presets import DEFAULT_ZONE_RULES
from airbrief_core.processing import NON_FINITE_REASON, _build_record
from airbrief_core.io.reader import RawFeature, RawLayer
from airbrief_core.geo.crs import WGS84


def test_pairs_within_limit_raises_before_building_pairs() -> None:
    frames, drones = 50, 60
    pos = np.zeros((frames, drones, 3))  # every drone on one point: 1,770 pairs per frame
    active = np.ones((frames, drones), dtype=bool)
    with pytest.raises(_native.TooManyPairs):
        _native.pairs_within(pos, active, 1.5, max_pairs=10_000)
    small = _native.pairs_within(pos[:2], active[:2], 1.5, max_pairs=10_000)
    assert small == _native.pairs_within(pos[:2], active[:2], 1.5)
    assert len(small) == 2 * drones * (drones - 1) // 2


def test_stacked_show_is_refused_not_exhausting_memory() -> None:
    drones = 400  # 79,800 close pairs per frame: the cap is passed within a few frames
    tracks = [
        {"id": f"D{i:03d}", "times": [0.0, 60.0], "coordinates": [[77.59, 13.0, 10.0], [77.59, 13.0, 10.0]]}
        for i in range(drones)
    ]
    params = ShowParams.model_validate({"tracks": tracks, "min_separation_m": 5.0})
    with pytest.raises(AnalysisError) as caught:
        run_show(params, AnalysisContext())
    assert caught.value.code == "too_many_conflicts"
    assert caught.value.details["max_close_pairs"] == MAX_CLOSE_PAIRS


class _Pack:
    def __init__(self, index: AerodromeIndex) -> None:
        self._index = index

    def aerodromes(self) -> AerodromeIndex:
        return self._index


def test_nearest_aerodrome_to_a_long_track_is_not_missed() -> None:
    rows = ["ident,type,name,latitude_deg,longitude_deg,elevation_ft"]
    # Eight airfields 11 km north of the middle of the track, and one heliport by its far end.
    for i in range(8):
        rows.append(f"AF{i},small_airport,Field {i},13.10,{77.22 + i * 0.01:.2f},3000")
    rows.append("HELI,heliport,End heliport,13.005,77.49,3000")
    pack = _Pack(AerodromeIndex.from_csv_text("\n".join(rows)))
    track = LineString([(77.0, 13.0), (77.5, 13.0)])  # about 54 km long

    nearest = zones.nearest_aerodromes(track, pack, k=1)  # type: ignore[arg-type]

    assert nearest[0].ident == "HELI"
    assert nearest[0].distance_km < 1.0


def test_zone_polygons_are_parsed_once_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = zones._build_polygon_zones

    def counting(*args: Any) -> Any:
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(zones, "_build_polygon_zones", counting)
    zone = UserZone.model_validate(
        {"name": "Stadium", "zone": "red", "geometry": {"type": "Polygon", "coordinates": [[[77.0, 13.0], [77.1, 13.0], [77.1, 13.1], [77.0, 13.0]]]}}
    )
    user_zones = [zone]
    for _ in range(5):
        zones._polygon_zones(None, DEFAULT_ZONE_RULES, user_zones, True)
    assert len(calls) == 1
    # A different zone list (another run) is parsed again, never mixed up with the cached one.
    zones._polygon_zones(None, DEFAULT_ZONE_RULES, [zone.model_copy()], True)
    assert len(calls) == 2


def _layer(feature: RawFeature) -> RawLayer:
    return RawLayer(name="layer", crs=WGS84, crs_source="kml", features=[feature])


@pytest.mark.filterwarnings("ignore:invalid value encountered")
def test_nan_coordinates_make_the_feature_unsupported() -> None:
    bad = RawFeature(fid="1", name="bad", geometry=LineString([(77.5, 12.9), (float("nan"), 12.95), (77.6, 13.0)]), properties={})
    issues: list[Any] = []
    record = _build_record(0, _layer(bad), bad, issues)
    assert record.geometry is None and record.geometry_wgs84 is None
    assert record.geometry_type == "LineString"
    assert record.measurement.reason == NON_FINITE_REASON
    assert [i.code for i in issues] == ["invalid_coordinates"]


def test_nan_altitudes_are_dropped_but_the_feature_is_measured() -> None:
    feature = RawFeature(fid="2", name="line", geometry=LineString([(77.5, 12.9, 10.0), (77.51, 12.9, float("inf"))]), properties={})
    issues: list[Any] = []
    record = _build_record(0, _layer(feature), feature, issues)
    assert record.measurement.status.value == "measured"
    assert record.geometry_wgs84 is not None and len(record.geometry_wgs84["coordinates"][0]) == 2
    assert [i.code for i in issues] == ["invalid_altitudes"]


def test_point_geometry_is_kept() -> None:
    point = RawFeature(fid="3", name="p", geometry=Point(77.5, 12.9), properties={})
    record = _build_record(0, _layer(point), point, [])
    assert record.geometry_wgs84 == {"type": "Point", "coordinates": [77.5, 12.9]}
