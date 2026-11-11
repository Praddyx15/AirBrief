"""The three assessment choices (docs/features/choices.md): the flight audit's ``band_ceiling``,
eVTOL's ``unverified_band`` and the ED-318 export layout. Every value stays conservative: a band
to verify or a zone flown through never becomes a pass or free."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from shapely.geometry import LineString, box, mapping

from airbrief_core.analyses import flight_audit
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.evtol import _simplify_route, run_corridor, run_siting
from airbrief_core.analyses.zones import has_unverified_band, unverified_bands
from airbrief_core.brief import build_brief
from airbrief_core.data import DataPack
from airbrief_core.errors import AnalysisError
from airbrief_core.geo.frame import LocalFrame
from airbrief_core.geo.geodesic import GEOD
from airbrief_core.io.geozones import detect_geozone_format, parse_ed318, to_ed318
from airbrief_core.models.analyses import (
    EvtolCorridorParams,
    EvtolCorridorResult,
    EvtolSitingParams,
    EvtolSitingResult,
    FlightAuditParams,
    FlightAuditResult,
)
from airbrief_core.models.brief import Brief, BriefPart, BriefSource
from airbrief_core.models.common import UserZone, Verdict, ZoneRules
from airbrief_core.models.presets import CONSERVATIVE_ILLUSTRATIVE, DGCA_2021_ZONE_RULES
from airbrief_core.models.tracks import AuditTrackInput
from airbrief_core.orchestrator import run_analysis_sync

VOBG = (77.6682, 12.9499)  # HAL: synthetic outline 3.6 km east-west and 1 km wide
OPEN_COUNTRY = (77.40, 12.75)  # more than 30 km from every aerodrome of the synthetic pack


def _towards(origin: tuple[float, float], azimuth: float, distance_m: float) -> tuple[float, float]:
    lon, lat, _ = GEOD.fwd(origin[0], origin[1], azimuth, distance_m)
    return float(lon), float(lat)


def _track(height_m: float = 75.0, start: tuple[float, float] = VOBG, first_m: int = 12_000) -> AuditTrackInput:
    # from HAL: 12.0 to 13.6 km east, all of it in the DGCA 8-12 km band (17 fixes, 100 m apart)
    points = [_towards(start, 90.0, d) for d in range(first_m, first_m + 1_601, 100)]
    return AuditTrackInput(
        coordinates=[[lon, lat, height_m] for lon, lat in points],
        times=[float(i) for i in range(len(points))],
        altitude_mode="relativeToGround",
    )


def _audit(pack: DataPack, track: AuditTrackInput | None = None, **params: Any) -> FlightAuditResult:
    return flight_audit.run(FlightAuditParams(track=track or _track(), **params), AnalysisContext(datapack=pack))


def _area(centre: tuple[float, float], half_m: float) -> dict[str, Any]:
    dlat = half_m / 110_600.0
    dlon = half_m / (111_320.0 * np.cos(np.radians(centre[1])))
    return mapping(box(centre[0] - dlon, centre[1] - dlat, centre[0] + dlon, centre[1] + dlat))


def _red_above(rules: ZoneRules) -> ZoneRules:
    """The rules with every yellow-above band turned red above its ceiling."""
    def ring(r: Any) -> Any:
        return r.model_copy(update={"ceiling_exceeded_zone": "red"}) if r.ceiling_exceeded_zone else r

    return rules.model_copy(update={"rules": [a.model_copy(update={"rings": [ring(r) for r in a.rings]}) for a in rules.rules]})


def test_band_ceiling_defaults_to_an_exceedance(synthetic_pack: DataPack) -> None:
    result = _audit(synthetic_pack)
    assert FlightAuditParams(track=_track()).band_ceiling == "exceedance"
    assert result.band_ceiling == "exceedance" and result.above_band_ceiling is None
    assert result.verdict == Verdict.FAIL
    (exceedance,) = result.exceedances
    assert exceedance.limit_m == 60.0 and exceedance.max_agl_m == 75.0 and "8–12 km" in exceedance.reason
    assert "above_band_ceiling" not in result.layers


def test_band_ceiling_permission_reports_the_yellow_zone_above(synthetic_pack: DataPack) -> None:
    strict = _audit(synthetic_pack)
    result = _audit(synthetic_pack, band_ceiling="permission")
    assert result.band_ceiling == "permission"
    assert result.verdict == Verdict.CAUTION
    assert result.exceedances == []
    # the same fixes as the exceedance, with the band's ceiling and the margin
    (run,) = result.above_band_ceiling or []
    (exceedance,) = strict.exceedances
    assert (run.start_index, run.end_index, run.start_distance_m, run.end_distance_m) == (
        exceedance.start_index, exceedance.end_index, exceedance.start_distance_m, exceedance.end_distance_m)
    assert run.limit_m == 60.0 and run.max_agl_m == 75.0 and run.limit_m - run.max_agl_m == -15.0
    assert run.reason.startswith("Flown in yellow airspace above the 60 m ceiling of ")
    assert run.reason.endswith(": permission required") and "8–12 km" in run.reason
    assert len(result.layers["above_band_ceiling"]["features"]) == 1
    # the band's ceiling is no limit any more: the flight's own 120 m is
    assert {p.limit_agl_m for p in result.profile} == {120.0}
    codes = {i.code for i in result.issues}
    assert "above_band_ceiling" in codes and "limit_capped_zone" not in codes
    # zones and bands are the evidence of the same flight either way
    assert [b.model_dump() for b in result.band_evidence] == [b.model_dump() for b in strict.band_evidence]
    (hit,) = [z for z in result.zones if z.ident == "VOBG"]
    assert hit.zone == "yellow" and hit.permission_required and hit.agl_margin_m == -15.0


def test_band_ceiling_permission_keeps_the_audit_limit(synthetic_pack: DataPack) -> None:
    result = _audit(synthetic_pack, band_ceiling="permission", ceiling_agl_m=70.0)
    assert result.verdict == Verdict.FAIL
    (exceedance,) = result.exceedances
    assert exceedance.limit_m == 70.0 and exceedance.reason == "Above the 70 m height limit"
    assert [r.limit_m for r in result.above_band_ceiling or []] == [60.0]


def test_band_ceiling_permission_keeps_a_zone_ceiling_under_the_band(synthetic_pack: DataPack) -> None:
    track = _track()
    zone = UserZone(name="Event area", zone="yellow", ceiling_agl_m=70.0, geometry=_area(_towards(VOBG, 90.0, 12_800), 1_500.0))
    result = _audit(synthetic_pack, track, band_ceiling="permission", user_zones=[zone])
    assert result.verdict == Verdict.FAIL
    assert {e.limit_m for e in result.exceedances} == {70.0}
    assert all("Event area" in e.reason for e in result.exceedances)


def test_band_ceiling_permission_never_lets_a_red_zone_pass(synthetic_pack: DataPack) -> None:
    zone = UserZone(name="Stadium", zone="red", geometry=_area(_towards(VOBG, 90.0, 12_800), 300.0))
    assert _audit(synthetic_pack, band_ceiling="permission", user_zones=[zone]).verdict == Verdict.FAIL

    # a band that turns red above its ceiling keeps that ceiling as a limit
    result = _audit(synthetic_pack, band_ceiling="permission", zone_rules=_red_above(DGCA_2021_ZONE_RULES))
    assert result.verdict == Verdict.FAIL
    assert [e.limit_m for e in result.exceedances] == [60.0]
    assert result.above_band_ceiling == []


def test_band_ceiling_permission_above_open_airspace(synthetic_pack: DataPack) -> None:
    track = _track(150.0, OPEN_COUNTRY, 0)
    strict = _audit(synthetic_pack, track, ceiling_agl_m=200.0)
    assert strict.verdict == Verdict.FAIL
    assert [e.limit_m for e in strict.exceedances] == [120.0]

    result = _audit(synthetic_pack, track, ceiling_agl_m=200.0, band_ceiling="permission")
    assert result.verdict == Verdict.CAUTION and result.exceedances == []
    (run,) = result.above_band_ceiling or []
    assert run.limit_m == 120.0 and run.max_agl_m == 150.0
    assert run.reason == "Flown in yellow airspace above the 120 m ceiling outside mapped zones: permission required"
    assert "limit_capped_default" not in {i.code for i in result.issues}


def test_band_ceiling_changes_nothing_under_the_conservative_rules(synthetic_pack: DataPack) -> None:
    strict = _audit(synthetic_pack, zone_profile="conservative_illustrative")
    result = _audit(synthetic_pack, zone_profile="conservative_illustrative", band_ceiling="permission")
    assert result.verdict == strict.verdict == Verdict.FAIL
    assert [e.model_dump() for e in result.exceedances] == [e.model_dump() for e in strict.exceedances]
    assert result.above_band_ceiling == []


def test_an_audit_stored_before_the_choice_reads_as_an_exceedance(synthetic_pack: DataPack) -> None:
    stored = run_analysis_sync("flight_audit", {"track": _track().model_dump(mode="json")}, datapack=synthetic_pack)
    data = stored.model_dump(mode="json")
    del data["params"]["band_ceiling"]
    del data["result"]["band_ceiling"], data["result"]["above_band_ceiling"]
    assert FlightAuditParams.model_validate(data["params"]).band_ceiling == "exceedance"
    old = FlightAuditResult.model_validate(data["result"])
    assert old.band_ceiling is None and old.above_band_ceiling is None


def test_unverified_bands_reads_the_rings() -> None:
    assert has_unverified_band(DGCA_2021_ZONE_RULES)
    assert not has_unverified_band(CONSERVATIVE_ILLUSTRATIVE)


def test_unverified_bands_per_point(synthetic_pack: DataPack) -> None:
    near, far = _towards(VOBG, 180.0, 2_000.0), _towards(VOBG, 180.0, 10_000.0)
    lons, lats = np.array([near[0], far[0], np.nan]), np.array([near[1], far[1], np.nan])
    assert unverified_bands(lons, lats, pack=synthetic_pack, rules=DGCA_2021_ZONE_RULES) == ["VOBG 0–5 km", None, None]
    assert unverified_bands(lons, lats, pack=synthetic_pack, rules=CONSERVATIVE_ILLUSTRATIVE) == [None, None, None]


def _siting(pack: DataPack, **params: Any) -> EvtolSitingResult:
    # 2 km south of HAL's reference point: 1.5 km past its boundary, in the DGCA 0-5 km band
    p = EvtolSitingParams(geometry=_area(_towards(VOBG, 180.0, 2_000.0), 400.0), h3_resolution=9, top_n=2,
                          min_separation_km=0.0, max_slope_deg=45.0, exclude_water=False, **params)
    return run_siting(p, AnalysisContext(datapack=pack))


def test_siting_scores_the_band_by_default(synthetic_pack: DataPack) -> None:
    result = _siting(synthetic_pack)
    assert result.unverified_band == "permission"
    assert result.candidates and all(c.zone_basis == "verify_official_map" for c in result.candidates)
    assert result.cells_excluded == 0


def test_siting_can_exclude_the_band_like_a_red_zone(synthetic_pack: DataPack) -> None:
    result = _siting(synthetic_pack, unverified_band="exclude")
    assert result.unverified_band == "exclude"
    assert result.candidates == []
    assert result.cells and all(c.excluded and c.reason == "unverified_band" for c in result.cells)
    issues = {i.code: i.message for i in result.issues}
    assert "VOBG 0–5 km" in issues["verify_official_map"] and "excluded like a red zone" in issues["verify_official_map"]
    assert "band to verify" in issues["all_cells_excluded"]
    assert "is excluded like a red zone" in result.criteria["airspace"]

    # also when red zones are only scored: it was chosen
    assert _siting(synthetic_pack, unverified_band="exclude", exclude_red_zones=False).candidates == []


def test_siting_exclusion_changes_nothing_under_the_conservative_rules(synthetic_pack: DataPack) -> None:
    scored = _siting(synthetic_pack, zone_profile="conservative_illustrative")
    result = _siting(synthetic_pack, zone_profile="conservative_illustrative", unverified_band="exclude")
    assert [c.model_dump() for c in result.candidates] == [c.model_dump() for c in scored.candidates]
    assert result.cells_excluded == 0
    assert "changes nothing" in result.criteria["airspace"]


@pytest.fixture(scope="module")
def corridors(synthetic_pack: DataPack) -> dict[tuple[str, str], EvtolCorridorResult]:
    # 7 km north to 7 km south of HAL, 300 m above ground, with room to go round its 0-5 km band
    out = {}
    for profile in ("dgca_2021", "conservative_illustrative"):
        for choice in ("permission", "exclude"):
            params = EvtolCorridorParams(origin=_towards(VOBG, 0.0, 7_000.0), destination=_towards(VOBG, 180.0, 7_000.0),
                                         study_margin_km=10.0, h3_resolution=8, zone_profile=profile, unverified_band=choice)
            out[profile, choice] = run_corridor(params, AnalysisContext(datapack=synthetic_pack))
    return out


def _route_in_band(result: EvtolCorridorResult, pack: DataPack) -> int:
    coords = result.route["coordinates"][1:-1]
    bands = unverified_bands(np.array([c[0] for c in coords]), np.array([c[1] for c in coords]), pack=pack,
                             rules=DGCA_2021_ZONE_RULES)
    return sum(b is not None for b in bands)


def test_corridor_costs_the_band_by_default(corridors: dict[tuple[str, str], EvtolCorridorResult], synthetic_pack: DataPack) -> None:
    result = corridors["dgca_2021", "permission"]
    assert result.unverified_band == "permission"
    assert _route_in_band(result, synthetic_pack) > 0
    assert "never as free" in {i.code: i.message for i in result.issues}["verify_official_map"]


def test_corridor_can_make_the_band_impassable(corridors: dict[tuple[str, str], EvtolCorridorResult], synthetic_pack: DataPack) -> None:
    costed = corridors["dgca_2021", "permission"]
    result = corridors["dgca_2021", "exclude"]
    assert result.unverified_band == "exclude"
    assert _route_in_band(result, synthetic_pack) == 0
    assert result.length_km > costed.length_km
    message = {i.code: i.message for i in result.issues}["verify_official_map"]
    assert "VOBG 0–5 km" in message and "treated like a red zone: impassable" in message


def test_smoothing_never_cuts_through_a_blocked_area() -> None:
    # up 15 m, then 1 km east: the 20 m simplification would take the diagonal, which crosses the box
    frame = LocalFrame(77.5, 12.9)
    raw = frame.to_wgs84(LineString([(0.0, 0.0), (0.0, 15.0), (1_000.0, 15.0)]))
    start, end = raw.coords[0], raw.coords[-1]
    blocked = frame.to_wgs84(box(400.0, 0.0, 420.0, 7.0))
    assert _simplify_route(raw, frame, start, end, cells=[blocked]).equals(raw)
    assert len(_simplify_route(raw, frame, start, end).coords) == 2


def test_corridor_exclusion_changes_nothing_under_the_conservative_rules(corridors: dict[tuple[str, str], EvtolCorridorResult]) -> None:
    costed = corridors["conservative_illustrative", "permission"]
    result = corridors["conservative_illustrative", "exclude"]
    assert result.length_km == costed.length_km and result.route == costed.route
    codes = {i.code for i in result.issues}
    assert "unverified_band_not_applicable" in codes and "verify_official_map" not in codes


def test_a_corridor_from_inside_an_excluded_band_has_no_route(synthetic_pack: DataPack) -> None:
    params = EvtolCorridorParams(origin=_towards(VOBG, 180.0, 2_000.0), destination=_towards(VOBG, 180.0, 9_000.0),
                                 study_margin_km=1.0, unverified_band="exclude")
    with pytest.raises(AnalysisError) as exc:
        run_corridor(params, AnalysisContext(datapack=synthetic_pack))
    assert exc.value.code == "no_route" and "excluded bands to verify" in exc.value.message


def test_evtol_results_stored_before_the_choice_validate(corridors: dict[tuple[str, str], EvtolCorridorResult]) -> None:
    stored = corridors["dgca_2021", "permission"].model_dump(mode="json")
    del stored["unverified_band"]
    assert EvtolCorridorResult.model_validate(stored).unverified_band is None
    siting = {"candidates": [], "cells_evaluated": 0, "cells_excluded": 0, "weights": {}, "criteria": {}, "disclaimer": "x"}
    assert EvtolSitingResult.model_validate(siting).unverified_band is None
    assert EvtolSitingParams(geometry=_area(VOBG, 500.0)).unverified_band == "permission"
    assert EvtolCorridorParams(origin=(77.6, 12.95), destination=(77.7, 12.95)).unverified_band == "permission"


def _zones() -> list[UserZone]:
    square = _area(OPEN_COUNTRY, 500.0)
    return [
        UserZone(name="From the ground", zone="red", geometry=square, source_format="ed318"),
        UserZone(name="AMSL band", zone="yellow", geometry=square, lower_limit_m=900.0, upper_limit_m=1_200.0,
                 vertical_reference="AMSL", restriction="REQ_AUTHORISATION", source_format="ed318"),
        UserZone(name="Up to 60 m", zone="green", geometry=square, upper_limit_m=60.0, ceiling_agl_m=60.0, source_format="ed318"),
    ]


@pytest.mark.parametrize("limits", ["geometry", "properties"])
def test_ed318_layouts_round_trip(limits: str) -> None:
    doc = to_ed318(_zones(), limits=limits)  # type: ignore[arg-type]
    assert detect_geozone_format(doc) == "ed318"
    assert parse_ed318(doc) == _zones()


def test_ed318_layouts_put_the_limits_in_different_places() -> None:
    by_geometry = to_ed318(_zones())
    by_properties = to_ed318(_zones(), limits="properties")
    keys = ("lower", "lowerReference", "upper", "upperReference", "uom")
    for g, p in zip(by_geometry["features"], by_properties["features"], strict=True):
        assert "layer" not in p["geometry"] and not any(k in g["properties"] for k in keys)
        assert {k: p["properties"][k] for k in keys} == g["geometry"]["layer"]
    amsl = by_properties["features"][1]["properties"]
    assert (amsl["lower"], amsl["lowerReference"], amsl["upper"], amsl["upperReference"], amsl["uom"]) == (900.0, "AMSL", 1200.0, "AMSL", "M")


def _brief(kind: str, params: dict[str, Any], pack: DataPack) -> tuple[Brief, BriefPart]:
    envelope = run_analysis_sync(kind, params, datapack=pack).model_dump(mode="json")
    brief = build_brief([BriefSource(analysis_id=f"a-{kind}", envelope=envelope)])
    section = "flight_audit" if kind == "flight_audit" else "evtol"
    return brief, next(s for s in brief.sections if s.key == section).parts[0]


def _flagged(brief: Brief) -> str:
    return " ".join(w.message for w in brief.warnings if w.code == "non_default_parameters")


def test_the_brief_flags_band_ceiling_permission(synthetic_pack: DataPack) -> None:
    brief, part = _brief("flight_audit", {"track": _track().model_dump(mode="json"), "band_ceiling": "permission"}, synthetic_pack)
    assert "(band_ceiling: permission)" in _flagged(brief)
    rules = next(f for f in part.facts if f.label == "Zone rules")
    assert rules.note is not None and "permission required, not an exceedance" in rules.note
    (table,) = [t for t in part.tables if t.caption.startswith("Flown in yellow airspace")]
    assert table.rows[0][3:5] == ["75 m", "60 m"]


def test_the_brief_names_unverified_band_exclude_without_flagging_it(synthetic_pack: DataPack) -> None:
    params = {"geometry": _area(_towards(VOBG, 180.0, 10_000.0), 400.0), "h3_resolution": 9, "top_n": 2,
              "min_separation_km": 0.0, "max_slope_deg": 45.0, "unverified_band": "exclude"}
    brief, part = _brief("evtol_siting", params, synthetic_pack)
    assert "unverified_band" not in _flagged(brief)
    rules = next(f for f in part.facts if f.label == "Zone rules")
    assert rules.note is not None and "(unverified_band: exclude)" in rules.note


def test_the_brief_says_nothing_about_default_choices(synthetic_pack: DataPack) -> None:
    brief, part = _brief("flight_audit", {"track": _track().model_dump(mode="json")}, synthetic_pack)
    assert "band_ceiling" not in _flagged(brief)
    assert next(f for f in part.facts if f.label == "Zone rules").note is None
    assert not any(t.caption.startswith("Flown in yellow airspace") for t in part.tables)
