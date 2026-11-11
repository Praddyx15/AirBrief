"""ED-269 / ED-318 zone files (airbrief_core.io.geozones) and their ingest.

The documents are small synthetic files shaped like the published national ones (field names of
the FOCA ED-269 model and ENAIRE's ED-318 specification); no real zone data is bundled.
"""

from __future__ import annotations

import codecs
import copy
import html
import io
import json
import math
import re
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pytest
from shapely.geometry import Point, box, mapping, shape

from airbrief_core.errors import AnalysisError, IngestError
from airbrief_core.geo.geodesic import geodesic_area_perimeter
from airbrief_core.io import geozones
from airbrief_core.io.geojson import read_geojson
from airbrief_core.io.geozones import (
    detect_geozone_format,
    parse_ed269,
    parse_ed318,
    to_ed318,
    zone_from_properties,
)
from airbrief_core.models.common import UserZone, ZoneSchedule
from airbrief_core.models.zones import ZoneLayerMapping

SQUARE = [[[6.10, 49.60], [6.12, 49.60], [6.12, 49.62], [6.10, 49.62], [6.10, 49.60]]]


def _ed269_zone(**over: Any) -> dict[str, Any]:
    zone: dict[str, Any] = {
        "identifier": "LUR01",
        "country": "LUX",
        "name": "Test prohibited area",
        "type": "COMMON",
        "restriction": "PROHIBITED",
        "reason": ["SENSITIVE"],
        "zoneAuthority": [{"name": "Test authority", "siteURL": "https://example.org", "purpose": "AUTHORIZATION"}],
        "applicability": [{"permanent": "YES"}],
        "geometry": [{
            "uomDimensions": "M",
            "lowerLimit": 0,
            "lowerVerticalReference": "AGL",
            "upperLimit": 120,
            "upperVerticalReference": "AGL",
            "horizontalProjection": {"type": "Polygon", "coordinates": SQUARE},
        }],
    }
    zone.update(over)
    return zone


def _ed269(*zones: dict[str, Any]) -> dict[str, Any]:
    return {"title": "Test zones", "description": "synthetic", "features": list(zones)}


def test_circle_area_is_within_half_a_percent() -> None:
    radius = 2_000.0
    doc = _ed269(_ed269_zone(geometry=[{
        "uomDimensions": "M", "lowerLimit": 0, "lowerVerticalReference": "AGL", "upperLimit": 99999,
        "upperVerticalReference": "AMSL", "horizontalProjection": {"type": "Circle", "center": [6.13, 49.61], "radius": radius},
    }]))
    (zone,) = parse_ed269(doc)
    area, _ = geodesic_area_perimeter(shape(zone.geometry))
    assert area == pytest.approx(math.pi * radius**2, rel=0.005)
    assert len(zone.geometry["coordinates"][0]) == geozones.CIRCLE_SEGMENTS + 1
    assert zone.upper_limit_m is None and zone.lower_limit_m is None  # unlimited, from the ground


def test_feet_become_metres_and_unlimited_is_none() -> None:
    doc = _ed269(_ed269_zone(geometry=[{
        "uomDimensions": "FT", "lowerLimit": 400, "lowerVerticalReference": "AMSL", "upperLimit": "UNL",
        "upperVerticalReference": "AMSL", "horizontalProjection": {"type": "Polygon", "coordinates": SQUARE},
    }]))
    (zone,) = parse_ed269(doc)
    assert zone.lower_limit_m == pytest.approx(121.92)
    assert zone.upper_limit_m is None and zone.vertical_reference == "AMSL"
    (zone,) = parse_ed269(_ed269(_ed269_zone(geometry=[{
        "uomDimensions": "FT", "lowerLimit": 0, "lowerVerticalReference": "AGL", "upperLimit": 400,
        "upperVerticalReference": "AGL", "horizontalProjection": {"type": "Polygon", "coordinates": SQUARE},
    }])))
    assert zone.upper_limit_m == pytest.approx(121.92) and zone.lower_limit_m is None


@pytest.mark.parametrize(("code", "kind", "note"), [
    ("PROHIBITED", "red", None),
    ("REQ_AUTHORISATION", "yellow", None),
    ("REQ_AUTHORIZATION", "yellow", None),
    ("CONDITIONAL", "yellow", "Conditional"),
    ("USPACE", "yellow", "U-space"),
    ("NO_RESTRICTION", "green", None),
])
def test_restriction_mapping(code: str, kind: str, note: str | None) -> None:
    (zone,) = parse_ed269(_ed269(_ed269_zone(restriction=code)))
    assert zone.zone == kind and zone.restriction == code and zone.ceiling_agl_m is None
    assert zone.source_format == "ed269"
    if note is None:
        assert zone.note is None
    else:
        assert zone.note is not None and note in zone.note


def test_schedules_and_validity() -> None:
    doc = _ed269(_ed269_zone(applicability=[{
        "permanent": "NO",
        "startDateTime": "2026-10-20T00:00:00.00Z",
        "endDateTime": "2026-10-27T00:00:00.00+02:00",
        "schedule": [
            {"day": ["MON", "TUE", "WED", "THU", "FRI"], "startTime": "08:00:00Z", "endTime": "17:30:00Z"},
            {"day": "SAT", "startEvent": "SR", "endEvent": "SS"},
            {"day": ["SUN"], "startTime": "10:00:00+01:00", "endTime": "12:00:00+01:00"},
        ],
    }]))
    (zone,) = parse_ed269(doc)
    assert zone.valid_from == datetime(2026, 10, 20, tzinfo=UTC)
    assert zone.valid_to == datetime(2026, 10, 26, 22, tzinfo=UTC)
    assert zone.schedule == [
        ZoneSchedule(days=["MON", "TUE", "WED", "THU", "FRI"], start="08:00", end="17:30"),
        ZoneSchedule(days=["SAT"], start="00:00", end="24:00"),
        ZoneSchedule(days=["SUN"], start="09:00", end="11:00"),
    ]
    assert zone.note is not None and "sunrise/sunset" in zone.note


def test_volumes_and_periods_become_separate_zones() -> None:
    volume = _ed269_zone()["geometry"][0]
    upper = dict(volume, lowerLimit=120, upperLimit=300)
    periods = [{"startDateTime": "2026-10-20T08:00:00Z", "endDateTime": "2026-10-20T10:00:00Z"},
               {"startDateTime": "2026-10-21T08:00:00Z", "endDateTime": "2026-10-21T10:00:00Z"}]
    zones = parse_ed269(_ed269(_ed269_zone(geometry=[volume, upper], applicability=periods)))
    assert len(zones) == 4
    assert zones[0].name == "LUR01 Test prohibited area (1/4)"
    assert {(z.lower_limit_m, z.upper_limit_m) for z in zones} == {(None, 120.0), (120.0, 300.0)}


def _ed318_feature(**props: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "identifier": "ITR0001",
        "country": "ITA",
        "name": [{"text": "Zona di prova", "lang": "it-IT"}],
        "type": "REQ_AUTHORIZATION",
        "variant": "COMMON",
        "reason": ["AIR_TRAFFIC"],
        "limitedApplicability": [{"startDateTime": "2026-10-20T08:00:00Z", "endDateTime": "2026-10-20T12:00:00Z"}],
    }
    base.update(props)
    return {
        "type": "Feature",
        "properties": base,
        "geometry": {"type": "Polygon", "coordinates": SQUARE,
                     "layer": {"upper": 60, "upperReference": "AGL", "lower": 0, "lowerReference": "AGL", "uom": "M"}},
    }


def test_ed318_features() -> None:
    circle = {"type": "Feature", "properties": {"identifier": "ITC1", "name": "Circle", "type": "PROHIBITED"},
              "geometry": {"type": "Point", "coordinates": [12.5, 41.9], "extent": {"subType": "Circle", "radius": 500},
                           "layer": {"upper": 400, "upperReference": "AMSL", "lower": 0, "lowerReference": "AGL", "uom": "FT"}}}
    doc = {"type": "FeatureCollection", "features": [_ed318_feature(), circle]}
    assert detect_geozone_format(doc) == "ed318"
    first, second = parse_ed318(doc)
    assert first.name == "ITR0001 Zona di prova" and first.zone == "yellow" and first.upper_limit_m == 60.0
    assert first.valid_from == datetime(2026, 10, 20, 8, tzinfo=UTC) and first.reason == "AIR_TRAFFIC"
    assert second.zone == "red" and second.vertical_reference == "AMSL"
    assert second.upper_limit_m == pytest.approx(121.92)
    assert shape(second.geometry).contains(Point(12.5, 41.9))


def test_detect_format() -> None:
    assert detect_geozone_format(_ed269(_ed269_zone())) == "ed269"
    assert detect_geozone_format({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "plain"}, "geometry": mapping(Point(1, 2))}]}) is None
    assert detect_geozone_format([1, 2]) is None
    assert detect_geozone_format({"features": []}) is None


def _zones_for_round_trip() -> list[UserZone]:
    area = mapping(box(77.48, 12.90, 77.50, 12.91))
    return [
        UserZone(name="Stadium event", zone="red", geometry=area, note="Temporary red zone  (Rule 22)",
                 valid_from=datetime(2026, 10, 20, 8, tzinfo=UTC), valid_to=datetime(2026, 10, 20, 18, 30, 15, 250, tzinfo=UTC),
                 schedule=[ZoneSchedule(days=["SAT", "SUN"], start="06:00", end="24:00"),
                           ZoneSchedule(days=["ANY"], start="22:00", end="02:00")],
                 reason="Public event", authority="District police"),
        UserZone(name="Low ceiling", zone="yellow", geometry=area, ceiling_agl_m=45.0, lower_limit_m=10.0,
                 upper_limit_m=500.0, restriction="CONDITIONAL", reason="NATURE, NOISE"),
        UserZone(name="AMSL block", zone="yellow", geometry=area, lower_limit_m=1_000.0, vertical_reference="AMSL",
                 source_format="ed269", restriction="REQ_AUTHORISATION"),
        UserZone(name="Ellipsoid top", zone="green", geometry=area, upper_limit_m=1_200.5, vertical_reference="WGS84"),
        UserZone(name="Open", zone="green", geometry=area),
    ]


def test_round_trip() -> None:
    zones = _zones_for_round_trip()
    doc = to_ed318(zones)
    assert detect_geozone_format(doc) == "ed318"
    # what ED-318 readers see
    first = doc["features"][0]
    assert first["properties"]["type"] == "PROHIBITED" and first["geometry"]["layer"]["upper"] == 99999
    assert first["properties"]["reason"] == ["OTHER"] and first["properties"]["otherReasonInfo"] == "Public event"
    assert doc["features"][1]["properties"]["reason"] == ["NATURE", "NOISE"]
    # through JSON, as a file would go
    assert parse_ed318(json.loads(json.dumps(doc))) == zones


def test_round_trip_without_the_airbrief_extension() -> None:
    zones = [UserZone(name="Block", zone="red", geometry=mapping(box(1, 1, 2, 2)), restriction="PROHIBITED",
                      lower_limit_m=50.0, upper_limit_m=200.0, valid_from=datetime(2026, 1, 1, tzinfo=UTC))]
    doc = to_ed318(zones)
    del doc["features"][0]["properties"]["extendedProperties"]
    (back,) = parse_ed318(doc)
    assert back.model_dump(exclude={"source_format", "name"}) == zones[0].model_dump(exclude={"source_format", "name"})
    assert back.name == "AA00001 Block" and back.source_format == "ed318"


def _bad(doc: Any, parser: Any = parse_ed269) -> AnalysisError:
    with pytest.raises(AnalysisError) as exc:
        parser(doc)
    return exc.value


@pytest.mark.parametrize("change", [
    {"restriction": "MAYBE"},
    {"geometry": []},
    {"geometry": [{"horizontalProjection": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}]},
    {"geometry": [{"horizontalProjection": {"type": "Polygon", "coordinates": [[[200, 0], [201, 0], [201, 1], [200, 0]]]}}]},
    {"geometry": [{"horizontalProjection": {"type": "Polygon", "coordinates": "nope"}}]},
    {"geometry": [{"horizontalProjection": {"type": "Circle", "center": [6.1, 49.6], "radius": -5}}]},
    {"geometry": [{"horizontalProjection": {"type": "Circle", "center": "here", "radius": 5}}]},
    {"geometry": [{"uomDimensions": "KM", "horizontalProjection": {"type": "Polygon", "coordinates": SQUARE}}]},
    {"geometry": [{"lowerLimit": 300, "upperLimit": 100, "horizontalProjection": {"type": "Polygon", "coordinates": SQUARE}}]},
    {"geometry": [{"upperVerticalReference": "QNH", "horizontalProjection": {"type": "Polygon", "coordinates": SQUARE}}]},
    {"geometry": [{"upperLimit": float("nan"), "horizontalProjection": {"type": "Polygon", "coordinates": SQUARE}}]},
    {"applicability": [{"startDateTime": "yesterday"}]},
    {"applicability": [{"startDateTime": "2026-10-21T00:00:00Z", "endDateTime": "2026-10-20T00:00:00Z"}]},
    {"applicability": [{"schedule": [{"day": ["FUNDAY"], "startTime": "08:00", "endTime": "09:00"}]}]},
    {"applicability": [{"schedule": [{"day": ["MON"], "startTime": "25:00", "endTime": "09:00"}]}]},
    {"applicability": ["permanent"]},
])
def test_malformed_zones_are_refused_with_their_index(change: dict[str, Any]) -> None:
    doc = _ed269(_ed269_zone(), _ed269_zone(**change))
    err = _bad(doc)
    assert err.code == "invalid_params" and err.details.get("zone") == 1


def test_malformed_documents() -> None:
    assert _bad([1, 2]).code == "invalid_params"
    assert _bad({"features": "none"}).code == "invalid_params"
    assert _bad({"features": [1]}).code == "invalid_params"
    assert _bad({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None}]}, parse_ed318).code == "invalid_params"
    too_many = {"features": [{}] * (geozones.MAX_DOCUMENT_ZONES + 1)}
    assert _bad(too_many).details.get("limit") == geozones.MAX_DOCUMENT_ZONES
    nested: Any = {"leaf": 1}
    for _ in range(200):
        nested = {"x": nested}
    assert _bad(_ed269(_ed269_zone(extendedProperties=nested))).code == "too_complex"
    many_volumes = [_ed269_zone()["geometry"][0]] * (geozones.MAX_VOLUMES + 1)
    assert _bad(_ed269(_ed269_zone(geometry=many_volumes))).details.get("limit") == geozones.MAX_VOLUMES


def test_volumes_times_periods_are_capped_before_they_are_built() -> None:
    # 30 zones x 100 volumes x 10 periods = 30,000 parts from a file of 30 features
    volumes = [_ed269_zone()["geometry"][0]] * geozones.MAX_VOLUMES
    periods = [{"startDateTime": f"2026-10-{d:02d}T08:00:00Z", "endDateTime": f"2026-10-{d:02d}T10:00:00Z"} for d in range(1, 11)]
    doc = _ed269(*[_ed269_zone(geometry=volumes, applicability=periods) for _ in range(30)])
    started = time.perf_counter()
    err = _bad(doc)
    assert err.details.get("limit") == geozones.MAX_DOCUMENT_ZONES
    assert err.details.get("zone") == 5  # the sixth zone's 1,000 parts pass 5,000
    assert time.perf_counter() - started < 5.0
    feats = [_ed318_feature(type="PROHIBITED") for _ in range(30)]
    for f in feats:
        f["geometry"] = {"type": "GeometryCollection", "geometries": [f["geometry"]] * geozones.MAX_VOLUMES}
        f["properties"]["limitedApplicability"] = periods
    err = _bad({"type": "FeatureCollection", "features": feats}, parse_ed318)
    assert err.details.get("limit") == geozones.MAX_DOCUMENT_ZONES


def test_vertex_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geozones, "MAX_ZONE_VERTICES", 9)
    err = _bad(_ed269(_ed269_zone(), _ed269_zone()))
    assert err.details.get("limit") == 9 and err.details.get("zone") == 1


def test_zone_from_properties() -> None:
    area = mapping(box(1, 1, 2, 2))
    assert zone_from_properties({"colour": "Yellow", "ceiling_m": "40"}, area, name="a").ceiling_agl_m == 40.0
    assert zone_from_properties({"restriction": "NO_RESTRICTION"}, area, name="b").zone == "green"
    assert zone_from_properties({}, area, name="c").zone == "red"
    mapped = ZoneLayerMapping(zone_field="kind", default_zone="yellow", ceiling_field="top", valid_from_field="from",
                              valid_to_field="to")
    zone = zone_from_properties({"kind": "unknown", "top": 30, "from": "2026-10-20T08:00:00+05:30", "to": "2026-10-21"},
                                area, name="d", layer_mapping=mapped)
    assert zone.zone == "yellow" and zone.ceiling_agl_m == 30.0
    assert zone.valid_from == datetime(2026, 10, 20, 2, 30, tzinfo=UTC)
    assert zone.valid_to == datetime(2026, 10, 21, tzinfo=UTC)
    with pytest.raises(AnalysisError) as exc:
        zone_from_properties({"valid_from": "2026-10-21T00:00:00Z", "valid_to": "2026-10-20T00:00:00Z"}, area, name="e")
    assert exc.value.code == "invalid_params"
    with pytest.raises(AnalysisError):
        zone_from_properties({"ceiling_agl_m": 50_000}, area, name="f")
    # an unreadable schedule never makes a zone less active
    assert zone_from_properties({"schedule": [{"days": "x"}]}, area, name="g").schedule == []


def test_ingest_reads_zone_files() -> None:
    doc = _ed269(_ed269_zone(), _ed269_zone(identifier="LUR02", restriction="REQ_AUTHORISATION", geometry=[{
        "uomDimensions": "M", "lowerLimit": 0, "lowerVerticalReference": "AGL", "upperLimit": 99999,
        "upperVerticalReference": "AGL", "horizontalProjection": {"type": "Circle", "center": [6.13, 49.61], "radius": 300},
    }]))
    layer = read_geojson(json.dumps(doc).encode(), "lu.json")
    assert [f.geometry.geom_type for f in layer.features] == ["Polygon", "Polygon"]
    first, second = (f.properties for f in layer.features)
    assert first["zone"] == "red" and first["identifier"] == "LUR01" and first["upper_limit_m"] == 120
    assert second["zone"] == "yellow" and "upper_limit_m" not in second
    assert layer.features[0].name == "LUR01 Test prohibited area"
    assert any(i.code == "geozone_file" for i in layer.issues)
    # flat properties read back as the same zones
    back = [zone_from_properties(f.properties, mapping(f.geometry), name=f.name or "") for f in layer.features]
    assert back == parse_ed269(doc)

    ed318 = to_ed318(_zones_for_round_trip())
    layer = read_geojson(json.dumps(ed318).encode(), "zones.ed318.geojson")
    assert len(layer.features) == 5 and layer.features[0].properties["identifier"] == "AA00001"
    assert layer.features[0].properties["schedule"][0]["days"] == ["SAT", "SUN"]


def test_ingest_refuses_a_broken_zone_file() -> None:
    doc = _ed269(_ed269_zone(), _ed269_zone(restriction="SOMETIMES"))
    with pytest.raises(IngestError) as exc:
        read_geojson(json.dumps(doc).encode(), "bad.json")
    assert exc.value.code == "invalid_zone_file" and exc.value.details.get("zone") == 1


def test_parsers_do_not_change_their_input() -> None:
    doc = _ed269(_ed269_zone())
    before = copy.deepcopy(doc)
    parse_ed269(doc)
    assert doc == before



def _scheduled(day: str, start: str, end: str) -> UserZone:
    return parse_ed269(_ed269(_ed269_zone(applicability=[{"schedule": [{"day": [day], "startTime": start, "endTime": end}]}])))[0]


def test_a_schedule_time_with_an_offset_moves_its_weekday_too() -> None:
    # Monday 00:00-06:00 at UTC+2 is Sunday 22:00 to Monday 04:00 in UTC
    east = _scheduled("MON", "00:00+02:00", "06:00+02:00")
    assert east.schedule == [ZoneSchedule(days=["SUN"], start="22:00", end="04:00")]
    monday = datetime(2026, 10, 19, 1, tzinfo=UTC)
    assert geozones.schedule_intervals(east, monday, monday + timedelta(minutes=5))
    assert _scheduled("MON", "22:00-05:00", "05:00-05:00").schedule == [ZoneSchedule(days=["TUE"], start="03:00", end="10:00")]
    assert _scheduled("SAT", "08:00+02:00", "24:00+02:00").schedule == [ZoneSchedule(days=["SAT"], start="06:00", end="22:00")]
    assert _scheduled("ANY", "00:00+02:00", "06:00+02:00").schedule[0].days == ["ANY"]


def test_vertex_budget_counts_every_period_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geozones, "MAX_ZONE_VERTICES", 12)
    assert parse_ed269(_ed269(_ed269_zone()))  # one 5-vertex square
    periods = [{"startDateTime": f"2026-10-0{d}T08:00:00Z", "endDateTime": f"2026-10-0{d}T10:00:00Z"} for d in (1, 2, 3)]
    assert _bad(_ed269(_ed269_zone(applicability=periods))).details.get("limit") == 12


def test_large_circles_are_read() -> None:
    for radius, unit, metres in ((100_000, "M", 100_000.0), (121_520, "FT", 37_039.3)):
        volume = {"uomDimensions": unit, "lowerLimit": 0, "lowerVerticalReference": "AGL", "upperLimit": 120,
                  "upperVerticalReference": "AGL", "horizontalProjection": {"type": "Circle", "center": [6.13, 49.61], "radius": radius}}
        zone = parse_ed269(_ed269(_ed269_zone(geometry=[volume])))[0]
        _, miny, _, maxy = shape(zone.geometry).bounds
        assert (maxy - miny) * 111_200 / 2 == pytest.approx(metres, rel=0.01)
    volume["uomDimensions"] = "M"
    for radius in (600_000, 0, True, "far"):
        volume["horizontalProjection"]["radius"] = radius
        _bad(_ed269(_ed269_zone(geometry=[volume])))


def test_only_real_zone_files_are_read_as_zone_files() -> None:
    marker = {"type": "Feature", "properties": {"name": "Range", "type": "Prohibited"}, "geometry": mapping(Point(1, 2))}
    assert detect_geozone_format({"type": "FeatureCollection", "features": [marker]}) is None
    # AirBrief's own export of imported zones comes back as plain GeoJSON, flat fields and all
    layer = read_geojson(json.dumps(_ed269(_ed269_zone())).encode(), "lu.json")
    exported = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": f.properties, "geometry": mapping(f.geometry)} for f in layer.features]}
    assert detect_geozone_format(exported) is None


def test_ed318_export_uses_the_ed318_spellings() -> None:
    area = mapping(box(77.48, 12.90, 77.50, 12.91))
    zones = [UserZone(name="Permission", zone="yellow", geometry=area),
             UserZone(name="From ED-269", zone="yellow", geometry=area, restriction="REQ_AUTHORISATION", source_format="ed269"),
             UserZone(name="U-space", zone="yellow", geometry=area, restriction="U-SPACE")]
    doc = to_ed318(zones)
    assert [f["properties"]["type"] for f in doc["features"]] == ["REQ_AUTHORIZATION", "REQ_AUTHORIZATION", "USPACE"]
    assert parse_ed318(json.loads(json.dumps(doc))) == zones


# The national files below cannot be committed (ENAIRE reserves all rights, the IAA states no
# licence, the DFS samples are for testing only), so these fixtures copy their layouts, field for field.

def _enaire_feature(day: str = "ANY", **zone: Any) -> dict[str, Any]:
    uas: dict[str, Any] = {
        "identifier": "LEBZ45", "country": "ESP", "name": "BADAJOZ/Talavera La Real", "type": "REQ_AUTHORIZATION",
        "variant": "COMMON", "restrictionConditions": "", "region": "0", "reasons": "AIR_TRAFFIC",
        "otherReasonInfo": "", "regulationExemption": "No", "extendedProperties": "Aeródromo",
        "message": "<p>Por debajo de 45m <b>no</b> es necesario coordinar.</p>"
                   "<font color=\"#dc143c\">Por encima, NO permitido &amp; coordinar.</font>",
    }
    uas.update(zone)
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": SQUARE}, "properties": {
        "SHAPE_Area": "321103828.407361",
        "limitedApplicability": [{"startDateTime": "", "endDateTime": "", "schedule": [
            {"day": day, "startTime": "", "startEvent": "", "endTime": "", "endEvent": ""}]}],
        "UASZone": uas,
        "zoneAuthority": [{"purpose": "AUTHORIZATION", "intervalBefore": "", "name": "", "email": "cecoabjz@aena.es"}],
        "verticalLayer": {"upper": 900, "upperReference": "AGL", "lower": 45, "lowerReference": "AGL", "UOM": "M"},
        "datasetMetadata": {"provider": "", "issued": "", "validFrom": "", "validTo": ""},
    }}


def test_enaire_nests_the_zone_and_its_limits_in_the_properties() -> None:
    doc = {"type": "FeatureCollection", "features": [_enaire_feature(), _enaire_feature(day="", identifier="INF4324")]}
    assert detect_geozone_format(doc) == "ed318"
    first, placeholder = parse_ed318(doc)
    assert first.name == "LEBZ45 BADAJOZ/Talavera La Real" and first.zone == "yellow"
    assert (first.lower_limit_m, first.upper_limit_m, first.vertical_reference) == (45.0, 900.0, "AGL")
    assert first.reason == "AIR_TRAFFIC" and first.schedule == []  # every day, all day
    assert first.note == "Por debajo de 45m no es necesario coordinar. Por encima, NO permitido & coordinar."
    assert placeholder.schedule == []  # an empty schedule entry, not a malformed one
    layer = read_geojson(json.dumps(doc).encode(), "ZGUAS_Aero.json")
    assert len(layer.features) == 2 and layer.features[0].properties["identifier"] == "LEBZ45"


def test_zone_files_in_a_service_envelope_or_a_bare_list() -> None:
    # DFS: the ED-318 sample is a service reply, the ED-269 one a bare list per category
    reply = {"uasZones": {"type": "FeatureCollection", "features": [_ed318_feature()]},
             "genericReply": {"requestProcessedTimeStamp": "2025-05-27T09:12:06.257014564Z"}}
    assert detect_geozone_format(reply) == "ed318" and len(parse_ed318(reply)) == 1
    assert detect_geozone_format([_ed269_zone()]) == "ed269"
    assert parse_ed269([_ed269_zone()]) == parse_ed269(_ed269(_ed269_zone()))
    assert len(read_geojson(json.dumps(reply).encode(), "BeispieldatenED318.json").features) == 1
    assert len(read_geojson(json.dumps([_ed269_zone()]).encode(), "dipul_labore.geojson").features) == 1
    with pytest.raises(IngestError) as exc:
        read_geojson(b"[1, 2]", "numbers.json")
    assert exc.value.code == "corrupt_file"


def test_irish_and_luxembourg_details() -> None:
    # IAA: limits in feet ("ft") in geometry.layer, reason as one string, metadata at the top
    feat = _ed318_feature(reason="AIR_TRAFFIC", limitedApplicability=None)
    feat["geometry"]["layer"] = {"upper": 400, "upperReference": "AGL", "lower": 0, "lowerReference": "AGL", "uom": "ft"}
    doc = {"type": "FeatureCollection", "name": "20261021_UAS_Zones_Ireland_V1",
           "datasetMetadata": {"provider": "Irish Aviation Authority"}, "features": [feat]}
    zone = parse_ed318(doc)[0]
    assert zone.upper_limit_m == 121.92 and zone.reason == "AIR_TRAFFIC"
    # Luxembourg's file starts with a UTF-8 byte order mark
    bom = codecs.BOM_UTF8 + json.dumps(_ed269(_ed269_zone())).encode()
    assert len(read_geojson(bom, "uav_202610310645.json").features) == 1

# FOCA publishes every Swiss UAS zone in one ED-269 file (open use, source to be given);
# about 17 MB, 1,256 zones and a zone of 63 volumes on 2026-10-30
FOCA_ED269_URL = (
    "https://data.geo.admin.ch/ch.bazl.einschraenkungen-drohnen/einschraenkungen-drohnen/"
    "einschraenkungen-drohnen_4326.json"
)


@pytest.mark.network
@pytest.mark.slow
def test_the_swiss_national_zone_file_ingests(tmp_path: Path) -> None:
    from airbrief_core.data.fetch import download
    from airbrief_core.processing import process_file

    path = download(FOCA_ED269_URL, tmp_path / "ch.json", timeout=180)
    fmt, zones = geozones.parse_geozones(json.loads(path.read_bytes()))
    assert fmt == "ed269" and len(zones) > 1000
    dataset = process_file(path, "ch.json", workdir=tmp_path / "work")
    assert len(dataset.features) == len(zones)
    # a few of FOCA's rings cross themselves; ingest repairs and flags them, as for any upload
    codes = [i.code for i in dataset.issues]
    assert "geozone_file" in codes and set(codes) <= {"geozone_file", "geometry_repaired"}
    assert all(f.measurement.status == "measured" for f in dataset.features)
    assert {f.properties["zone"] for f in dataset.features} <= {"red", "yellow", "green"}


NATIONAL_ZONE_FILES = {
    "enaire_aero": "https://aip.enaire.es/recursos/descargas/ZGUAS/ZGUAS_Aero.zip",
    "luxembourg": "https://drones.geoportail.lu/zones",
    "dfs_ed318_sample": "https://dipul.bund.de/drohnenhersteller/de/beispieldatened318.zip",
    # the file's name changes with every issue, so it is found on the page
    "iaa": "https://www.iaa.ie/general-aviation/drones/uas-geographic-zones",
}


@pytest.mark.network
@pytest.mark.slow
@pytest.mark.parametrize("source", sorted(NATIONAL_ZONE_FILES))
def test_national_zone_files_ingest(source: str, tmp_path: Path) -> None:
    from airbrief_core.data.fetch import download, fetch_bytes
    from airbrief_core.processing import process_file

    url = NATIONAL_ZONE_FILES[source]
    if source == "iaa":
        page = fetch_bytes(url).decode("utf-8", "replace")
        link = re.search(r'href="([^"]+\.geojson[^"]*)"', page)
        assert link, "the IAA page no longer links a GeoJSON file"
        url = urljoin(url, html.unescape(link.group(1)))
    raw = download(url, tmp_path / "download", timeout=180).read_bytes()
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            raw = archive.read(next(n for n in archive.namelist() if n.lower().endswith(".json")))
    path = tmp_path / f"{source}.json"
    path.write_bytes(raw)
    dataset = process_file(path, path.name, workdir=tmp_path / "work")
    assert "geozone_file" in [i.code for i in dataset.issues] and len(dataset.features) > 10
    assert all(f.measurement.status == "measured" for f in dataset.features)
