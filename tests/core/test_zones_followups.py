"""Zone rule profiles outside the airspace check: the flight audit and eVTOL siting / corridor take
``zone_profile`` like airspace (DGCA 2021 by default), results stored before it read as the
conservative rules, and the brief handles them as it handles airspace."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, box, mapping

from airbrief_core.analyses import flight_audit
from airbrief_core.analyses.airspace import run as airspace_run
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.evtol import AIRSPACE_PENALTY_PERMISSION, _corridor_airspace_penalty, run_corridor, run_siting
from airbrief_core.analyses.zones import classify_points
from airbrief_core.brief import build_brief
from airbrief_core.data import DataPack
from airbrief_core.geo.geodesic import GEOD
from airbrief_core.models.analyses import (
    AirspaceParams,
    EvtolCorridorParams,
    EvtolCorridorResult,
    EvtolSitingParams,
    EvtolSitingResult,
    FlightAuditParams,
    FlightAuditResult,
)
from airbrief_core.models.brief import Brief, BriefPart, BriefSource
from airbrief_core.models.common import UserZone, Verdict
from airbrief_core.models.presets import CONSERVATIVE_ILLUSTRATIVE, DGCA_2021_ZONE_RULES
from airbrief_core.models.tracks import AuditTrackInput
from airbrief_core.orchestrator import run_analysis_sync

VOBG = (77.6682, 12.9499)  # HAL: synthetic outline 3.6 km east-west and 1 km wide


def _towards(origin: tuple[float, float], azimuth: float, distance_m: float) -> tuple[float, float]:
    lon, lat, _ = GEOD.fwd(origin[0], origin[1], azimuth, distance_m)
    return float(lon), float(lat)


def _track(height_m: float = 75.0) -> AuditTrackInput:
    # 12.0 to 13.6 km east of HAL along its runway axis: 10.2 to 11.8 km past its boundary (DGCA
    # 8-12 km band), but 11.0 to 12.6 km from its reference point minus 1 km (the conservative
    # band ends at 13 km)
    points = [_towards(VOBG, 90.0, d) for d in range(12_000, 13_601, 100)]
    return AuditTrackInput(
        coordinates=[[lon, lat, height_m] for lon, lat in points],
        times=[float(i) for i in range(len(points))],
        altitude_mode="relativeToGround",
    )


def _area(centre: tuple[float, float], half_m: float) -> dict[str, Any]:
    dlat = half_m / 110_600.0
    dlon = half_m / (111_320.0 * np.cos(np.radians(centre[1])))
    return mapping(box(centre[0] - dlon, centre[1] - dlat, centre[0] + dlon, centre[1] + dlat))


def _audit(pack: DataPack, **params: Any) -> FlightAuditResult:
    return flight_audit.run(FlightAuditParams(track=_track(), **params), AnalysisContext(datapack=pack))


def _brief(kind: str, params: dict[str, Any], pack: DataPack) -> Brief:
    envelope = run_analysis_sync(kind, params, datapack=pack).model_dump(mode="json")
    return build_brief([BriefSource(analysis_id=f"a-{kind}", envelope=envelope)])


def _part(brief: Brief, section: str) -> BriefPart:
    return next(s for s in brief.sections if s.key == section).parts[0]


@pytest.mark.parametrize(
    ("profile", "measured_from", "expected"),
    [
        ("dgca_2021", "perimeter", [("8–12 km", "yellow", True, -15.0)]),
        ("conservative_illustrative", "reference_point", [("8–12 km", "yellow", False, -15.0), ("Elsewhere", "green", False, 45.0)]),
    ],
)
def test_the_same_track_under_both_profiles(
    synthetic_pack: DataPack, profile: str, measured_from: str, expected: list[tuple[str, str, bool, float]]
) -> None:
    result = _audit(synthetic_pack, zone_profile=profile)
    assert result.zone_profile == profile
    bands = [(b.band.split(":")[0], b.zone, b.permission_required, b.agl_margin_m) for b in result.band_evidence]
    assert bands == expected
    assert result.band_evidence[0].ident == "VOBG" and result.band_evidence[0].distance_from == measured_from
    assert all(b.sample_kind == "track" and b.worst_agl_m == pytest.approx(75.0) for b in result.band_evidence)
    assert sum(b.samples for b in result.band_evidence) == 17

    # The band's 60 m free-flight ceiling is the limit; 75 m is above it either way.
    assert result.verdict == Verdict.FAIL
    assert result.exceedances[0].limit_m == 60.0 and "8–12 km" in result.exceedances[0].reason
    (hit,) = [z for z in result.zones if z.ident == "VOBG"]
    assert hit.agl_margin_m == pytest.approx(-15.0) and hit.distance_from == measured_from
    if profile == "dgca_2021":
        # green to 60 m, so flown at 75 m it is the yellow zone above, as airspace says
        assert hit.zone == "yellow" and hit.permission_required and hit.ceiling_agl_m is None
    else:
        assert hit.zone == "yellow" and not hit.permission_required and hit.ceiling_agl_m == 60.0
    assert result.nearest_aerodrome is not None and result.nearest_aerodrome.operational is True
    rings = [f for f in result.layers["zones"]["features"] if f["properties"]["ident"] == "VOBG"]
    assert {f["properties"]["distance_from"] for f in rings} == {measured_from}


@pytest.mark.parametrize("profile", ["dgca_2021", "conservative_illustrative"])
def test_audit_zones_match_an_airspace_check_of_the_same_line(synthetic_pack: DataPack, profile: str) -> None:
    track = _track()
    audit = _audit(synthetic_pack, zone_profile=profile)
    line = LineString([c[:2] for c in track.coordinates])
    params = AirspaceParams(geometry=mapping(line), altitude_agl_m=75.0, grid=False, zone_profile=profile)
    check = airspace_run(params, AnalysisContext(datapack=synthetic_pack))

    def hits(zones: list[Any]) -> list[tuple[Any, ...]]:
        return [(z.source, z.ident, z.ring, z.zone, z.permission_required, z.ceiling_agl_m, z.distance_from, z.agl_margin_m,
                 z.distance_km) for z in zones]

    def bands(evidence: list[Any]) -> list[tuple[Any, ...]]:
        return [(b.ident, b.band, b.zone, b.permission_required, b.ceiling_agl_m, b.agl_margin_m) for b in evidence]

    assert hits(audit.zones) == hits(check.zones)
    assert bands(audit.band_evidence) == bands(check.band_evidence)


def test_a_two_dimensional_track_gets_bands_without_heights(synthetic_pack: DataPack) -> None:
    flat = _track().model_copy(update={"coordinates": [c[:2] for c in _track().coordinates]})
    result = flight_audit.run(FlightAuditParams(track=flat), AnalysisContext(datapack=synthetic_pack))
    assert result.heights_assessed is False
    (band,) = result.band_evidence
    assert band.band.startswith("8–12 km") and band.worst_agl_m is None and band.agl_margin_m is None
    # not judged at a height: the band's zone at the ground
    (hit,) = [z for z in result.zones if z.ident == "VOBG"]
    assert hit.zone == "green" and hit.ceiling_agl_m == 60.0 and hit.agl_margin_m is None


def _fixes_at(fixes: list[tuple[int, float]]) -> AuditTrackInput:
    # (metres east of HAL, height above ground) per fix
    return AuditTrackInput(coordinates=[[*_towards(VOBG, 90.0, d), h] for d, h in fixes], altitude_mode="relativeToGround")


def _flown(height: Callable[[int], float], start_m: int, end_m: int) -> AuditTrackInput:
    # due west towards HAL from start_m to end_m east of it, one fix every 100 m
    return _fixes_at([(d, height(d)) for d in range(start_m, end_m - 1, -100)])


def test_a_band_is_judged_at_the_height_flown_inside_it(synthetic_pack: DataPack) -> None:
    # 100 m in open airspace, down to 40 m at 14.4 km out, before the 8-12 km band (inside 13.8 km)
    track = _flown(lambda d: 100.0 if d > 14_400 else 40.0, 16_000, 12_000)
    result = flight_audit.run(FlightAuditParams(track=track), AnalysisContext(datapack=synthetic_pack))

    band, elsewhere = result.band_evidence
    assert band.band.startswith("8–12 km") and band.worst_agl_m == pytest.approx(40.0) and band.zone == "green"
    assert elsewhere.worst_agl_m == pytest.approx(100.0)
    (hit,) = [z for z in result.zones if z.ident == "VOBG"]
    assert hit.zone == "green" and not hit.permission_required and hit.ceiling_agl_m == 60.0
    assert hit.agl_margin_m == pytest.approx(20.0)
    assert result.exceedances == []
    assert result.verdict == Verdict.PASS


@pytest.mark.parametrize(("under", "reached"), [(50.0, False), (155.0, True)])
def test_a_red_zone_counts_only_above_its_lower_limit(synthetic_pack: DataPack, under: float, reached: bool) -> None:
    # open airspace 20 to 16 km east of HAL: `under` across the zone (18.1-18.9 km out), 160 m inside 17 km
    block = UserZone(name="Overhead block", zone="red", geometry=_area(_towards(VOBG, 90.0, 18_500), 400.0),
                     lower_limit_m=150.0, restriction="PROHIBITED", source_format="ed318")
    track = _flown(lambda d: under if d > 17_000 else 160.0, 20_000, 16_000)
    params = FlightAuditParams(track=track, user_zones=[block], band_ceiling="permission", ceiling_agl_m=200.0)
    result = flight_audit.run(params, AnalysisContext(datapack=synthetic_pack))

    assert result.max_agl_m == pytest.approx(160.0)
    red = [z for z in result.zones if z.name == "Overhead block"]
    inactive = [i for i in result.issues if i.code == "zones_inactive"]
    if reached:
        assert red[0].zone == "red" and not inactive
        assert result.verdict == Verdict.FAIL
    else:
        assert not red and "Overhead block" in inactive[0].message
        # the 160 m stretch is open airspace above 120 m: permission, not a red zone
        assert result.above_band_ceiling and result.verdict == Verdict.CAUTION


@pytest.mark.parametrize("fixes", [
    [(20_000, 50.0), (18_500, 50.0), (17_900, 200.0), (16_000, 200.0)],  # one fix inside, at 50 m
    [(20_000, 50.0), (19_000, 50.0), (17_900, 200.0), (16_000, 200.0)],  # none inside
])
def test_a_zone_crossed_in_a_climb_counts_wherever_the_fixes_fall(
    synthetic_pack: DataPack, fixes: list[tuple[int, float]]
) -> None:
    # a red zone from 100 m up at 18.1-18.9 km east of HAL; the track climbs from 50 to 200 m across it
    block = UserZone(name="Overhead block", zone="red", geometry=_area(_towards(VOBG, 90.0, 18_500), 400.0),
                     lower_limit_m=100.0)
    params = FlightAuditParams(track=_fixes_at(fixes), user_zones=[block], band_ceiling="permission", ceiling_agl_m=250.0)
    result = flight_audit.run(params, AnalysisContext(datapack=synthetic_pack))

    (hit,) = [z for z in result.zones if z.name == "Overhead block"]
    assert hit.zone == "red" and hit.agl_margin_m == pytest.approx(-100.0)
    assert result.verdict == Verdict.FAIL


def test_the_limits_count_a_zone_where_the_zone_list_does(synthetic_pack: DataPack) -> None:
    # a yellow zone from 100 m up (18.1-18.9 km east of HAL): the fix inside is at 50 m, the next
    # one, outside, at 150 m, so the segment between them reaches the zone
    upper = UserZone(name="Upper block", zone="yellow", geometry=_area(_towards(VOBG, 90.0, 18_500), 400.0),
                     lower_limit_m=100.0)
    track = _fixes_at([(20_000, 50.0), (18_500, 50.0), (17_900, 150.0), (16_000, 150.0)])
    params = FlightAuditParams(track=track, user_zones=[upper], ceiling_agl_m=200.0)
    result = flight_audit.run(params, AnalysisContext(datapack=synthetic_pack))

    (hit,) = [z for z in result.zones if z.name == "Upper block"]
    assert hit.zone == "yellow" and hit.permission_required
    # the fix inside is in that zone too: no free-flight ceiling, so the flight's own 200 m
    assert [p.limit_agl_m for p in result.profile] == [120.0, 200.0, 120.0, 120.0]
    (exceedance,) = result.exceedances
    assert exceedance.start_index == 2 and exceedance.limit_m == 120.0
    assert result.verdict == Verdict.FAIL


def test_a_fix_without_a_height_stays_unknown_and_its_neighbours_keep_theirs() -> None:
    # a NaN reach counts as reaching every vertical band: right for the fix whose height is
    # unknown, wrong for its neighbours
    reach = flight_audit._segment_reach(np.array([50.0, np.nan, 150.0, 40.0, 30.0]))
    np.testing.assert_array_equal(reach, [50.0, np.nan, 150.0, 150.0, 40.0])
    reach = flight_audit._segment_reach(np.array([30.0, np.nan, 30.0]))
    np.testing.assert_array_equal(reach, [30.0, np.nan, 30.0])

    upper = UserZone(name="Upper block", zone="yellow", geometry=_area(_towards(VOBG, 90.0, 18_500), 400.0),
                     lower_limit_m=200.0)
    lons, lats = zip(*[_towards(VOBG, 90.0, d) for d in (18_400, 18_500, 18_600)], strict=True)
    states = classify_points(np.array(lons), np.array(lats), pack=None, rules=DGCA_2021_ZONE_RULES,
                             user_zones=[upper], reach_agl=reach)
    assert [s.name for s in states] == ["Default rules", "Upper block", "Default rules"]


@pytest.mark.parametrize(("band_ceiling", "exceedances", "above"), [("exceedance", 1, 0), ("permission", 0, 1)])
def test_flying_under_a_zone_is_open_airspace(
    synthetic_pack: DataPack, band_ceiling: str, exceedances: int, above: int
) -> None:
    # a yellow zone from 200 m up (18.1-18.9 km east of HAL), flown under at 150 m: the 120 m
    # open-airspace ceiling applies there, not the zone
    upper = UserZone(name="Upper block", zone="yellow", geometry=_area(_towards(VOBG, 90.0, 18_500), 400.0),
                     lower_limit_m=200.0)
    track = _flown(lambda d: 150.0 if 18_200 <= d <= 18_800 else 100.0, 20_000, 16_000)
    params = FlightAuditParams(track=track, user_zones=[upper], ceiling_agl_m=200.0, band_ceiling=band_ceiling)
    result = flight_audit.run(params, AnalysisContext(datapack=synthetic_pack))

    assert not [z for z in result.zones if z.name == "Upper block"]
    assert any(i.code == "zones_inactive" and "Upper block" in i.message for i in result.issues)
    assert len(result.exceedances) == exceedances and len(result.above_band_ceiling or []) == above
    (segment,) = result.exceedances or result.above_band_ceiling or []
    assert segment.limit_m == 120.0 and segment.max_agl_m == 150.0
    assert "outside mapped zones" in segment.reason


def test_an_audit_stored_before_profiles_validates_and_reads_as_conservative(synthetic_pack: DataPack) -> None:
    envelope = run_analysis_sync("flight_audit", {"track": _track().model_dump(mode="json")}, datapack=synthetic_pack)
    stored = envelope.model_dump(mode="json")
    assert stored["params"]["zone_profile"] == "dgca_2021" and stored["result"]["zone_profile"] == "dgca_2021"
    del stored["params"]["zone_profile"]
    del stored["result"]["zone_profile"], stored["result"]["band_evidence"]
    del stored["result"]["layers"]["zones"]

    old = FlightAuditResult.model_validate(stored["result"])
    assert old.zone_profile is None and old.band_evidence == []
    FlightAuditParams.model_validate(stored["params"])

    brief = build_brief([BriefSource(analysis_id="old-audit", envelope=stored)])
    part = _part(brief, "flight_audit")
    rules = next(f for f in part.facts if f.label == "Zone rules")
    assert rules.text == "Conservative illustrative (before zone profiles)"
    assert not any(t.caption == "Aerodrome bands flown" for t in part.tables)
    assert "non_default_parameters" not in {w.code for w in brief.warnings}


def test_the_brief_shows_the_audit_rules_and_bands(synthetic_pack: DataPack) -> None:
    brief = _brief("flight_audit", {"track": _track().model_dump(mode="json")}, synthetic_pack)
    part = _part(brief, "flight_audit")
    assert next(f for f in part.facts if f.label == "Zone rules").text.startswith("DGCA 2021")
    (table,) = [t for t in part.tables if t.caption == "Aerodrome bands flown"]
    assert table.rows[0][:3] == ["8–12 km", "VOBG", "Yellow zone"]


def _flagged(brief: Brief) -> str:
    return " ".join(w.message for w in brief.warnings if w.code == "non_default_parameters")


@pytest.mark.parametrize(
    ("rules", "flagged"),
    [
        ({"zone_profile": "conservative_illustrative"}, False),
        ({"zone_rules": DGCA_2021_ZONE_RULES.model_dump(mode="json")}, False),  # custom, but a preset's rules
        ({"zone_rules": CONSERVATIVE_ILLUSTRATIVE.model_copy(update={"rules": []}).model_dump(mode="json")}, True),
    ],
)
def test_the_cross_check_treats_audit_rules_as_airspace_rules(
    synthetic_pack: DataPack, rules: dict[str, Any], flagged: bool
) -> None:
    line = mapping(LineString([c[:2] for c in _track().coordinates]))
    airspace = _flagged(_brief("airspace", {"geometry": line, "grid": False, **rules}, synthetic_pack))
    audit = _flagged(_brief("flight_audit", {"track": _track().model_dump(mode="json"), **rules}, synthetic_pack))
    assert ("(zone_rules)" in airspace) is flagged
    assert ("(zone_rules)" in audit) is flagged


@pytest.mark.parametrize(
    ("model", "base"),
    [
        (FlightAuditParams, {"track": {"coordinates": [[77.6, 12.95], [77.61, 12.96]]}}),
        (EvtolSitingParams, {"geometry": _area(VOBG, 500.0)}),
        (EvtolCorridorParams, {"origin": (77.6, 12.95), "destination": (77.7, 12.95)}),
    ],
)
def test_zone_profile_and_zone_rules_go_together(model: Any, base: dict[str, Any]) -> None:
    assert model(**base).zone_profile == "dgca_2021"
    assert model(**base, zone_rules=CONSERVATIVE_ILLUSTRATIVE).zone_profile == "custom"
    with pytest.raises(ValidationError, match="needs zone_rules"):
        model(**base, zone_profile="custom")
    with pytest.raises(ValidationError, match="'custom' only"):
        model(**base, zone_profile="dgca_2021", zone_rules=CONSERVATIVE_ILLUSTRATIVE)


@pytest.mark.parametrize(("profile", "zone", "score"), [("dgca_2021", "green", 1.0), ("conservative_illustrative", "yellow", 0.5)])
def test_siting_in_the_eight_to_twelve_km_band(synthetic_pack: DataPack, profile: str, zone: str, score: float) -> None:
    # 10 km south of HAL: 9.5 km past its boundary, 9 km from its reference point minus 1 km
    params = EvtolSitingParams(geometry=_area(_towards(VOBG, 180.0, 10_000.0), 400.0), h3_resolution=9, top_n=2,
                               min_separation_km=0.0, max_slope_deg=45.0, zone_profile=profile)
    result = run_siting(params, AnalysisContext(datapack=synthetic_pack))
    assert result.zone_profile == profile and result.candidates
    for c in result.candidates:
        assert c.zone == zone and c.breakdown["airspace"] == score and c.zone_basis == "rule"


@pytest.mark.parametrize("profile", ["dgca_2021", "conservative_illustrative"])
def test_siting_next_to_an_airport(synthetic_pack: DataPack, profile: str) -> None:
    # 2 km south of HAL's reference point: 1.5 km past its boundary (DGCA 0-5 km, conservative 0-8 km)
    params = EvtolSitingParams(geometry=_area(_towards(VOBG, 180.0, 2_000.0), 400.0), h3_resolution=9, top_n=2,
                               min_separation_km=0.0, max_slope_deg=45.0, exclude_water=False, zone_profile=profile)
    result = run_siting(params, AnalysisContext(datapack=synthetic_pack))
    unverified = profile == "dgca_2021"
    assert result.candidates
    assert not any(cell.reason == "red_zone" for cell in result.cells)  # not known to be red: never excluded as red
    for c in result.candidates:
        assert c.zone == "yellow" and c.breakdown["airspace"] == 0.0
        assert (c.zone_basis == "verify_official_map") is unverified
        assert any("may be a red zone" in n for n in c.notes) is unverified
    assert any(i.code == "verify_official_map" for i in result.issues) is unverified


@pytest.fixture(scope="module")
def corridors(synthetic_pack: DataPack) -> dict[str, EvtolCorridorResult]:
    # 7 km north to 7 km south of HAL, 300 m above ground: straight through its 0-5 km band
    out = {}
    for profile in ("dgca_2021", "conservative_illustrative"):
        params = EvtolCorridorParams(origin=_towards(VOBG, 0.0, 7_000.0), destination=_towards(VOBG, 180.0, 7_000.0),
                                     study_margin_km=1.0, h3_resolution=8, zone_profile=profile)
        out[profile] = run_corridor(params, AnalysisContext(datapack=synthetic_pack))
    return out


def test_corridor_under_dgca_names_the_unverified_band(corridors: dict[str, EvtolCorridorResult]) -> None:
    result = corridors["dgca_2021"]
    assert result.zone_profile == "dgca_2021"
    rings = {(z.ring or "").split(":")[0]: z for z in result.zones if z.ident == "VOBG"}
    assert rings["0–5 km"].basis == "verify_official_map" and rings["0–5 km"].permission_required
    assert rings["5–8 km"].zone == "yellow" and rings["5–8 km"].permission_required
    codes = {i.code: i.message for i in result.issues}
    assert "VOBG 0–5 km" in codes["verify_official_map"] and "never as free" in codes["verify_official_map"]
    # 300 m above ground is over DGCA's 120 m open-airspace ceiling everywhere
    assert "120 m ceiling of open airspace" in codes["open_airspace_above_ceiling"]


def test_corridor_under_the_conservative_rules(corridors: dict[str, EvtolCorridorResult]) -> None:
    result = corridors["conservative_illustrative"]
    assert result.zone_profile == "conservative_illustrative"
    rings = [z.ring or "" for z in result.zones if z.ident == "VOBG"]
    assert rings and all(r.startswith("0–8 km") for r in rings)
    codes = {i.code for i in result.issues}
    assert "verify_official_map" not in codes and "open_airspace_above_ceiling" not in codes


def test_the_unverified_band_is_never_free(synthetic_pack: DataPack) -> None:
    lon, lat = _towards(VOBG, 180.0, 2_000.0)
    (state,) = classify_points(np.array([lon]), np.array([lat]), pack=synthetic_pack, rules=DGCA_2021_ZONE_RULES, agl=300.0)
    assert state.basis == "verify_official_map"
    assert _corridor_airspace_penalty(state) == AIRSPACE_PENALTY_PERMISSION


def test_evtol_results_stored_before_profiles_validate(corridors: dict[str, EvtolCorridorResult]) -> None:
    stored = corridors["dgca_2021"].model_dump(mode="json")
    del stored["zone_profile"]
    assert EvtolCorridorResult.model_validate(stored).zone_profile is None
    siting = {"candidates": [], "cells_evaluated": 0, "cells_excluded": 0, "weights": {}, "criteria": {}, "disclaimer": "x"}
    assert EvtolSitingResult.model_validate(siting).zone_profile is None
