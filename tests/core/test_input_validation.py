"""Validation guards added after code review: data-pack layer paths and FlightMD links."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from airbrief_core.data.datapack import DataPack, DataPackManifest, LayerEntry, write_manifest
from airbrief_core.errors import IngestError
from airbrief_core.io.ingest import IngestLimits
from airbrief_core.models.analyses import FlightAuditParams
from airbrief_core.processing import process_file
from airbrief_core.samples import build_sample

REPORT = {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [12.91, 77.51, 950.0]]}}


@pytest.mark.parametrize("name", ["dem.tif", "rasters/dem.tif", r"rasters\dem.tif"])
def test_layer_file_accepts_relative_paths(name: str) -> None:
    assert LayerEntry(file=name).file == name


def test_too_many_vertices_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "square.kml"
    path.write_bytes(build_sample("square_1km_equator"))  # one polygon, 5 vertices
    with pytest.raises(IngestError) as info:
        process_file(path, "square.kml", limits=IngestLimits(max_coordinates=4))
    assert info.value.code == "too_complex"
    assert process_file(path, "square.kml", limits=IngestLimits(max_coordinates=5)).feature_count == 1


def test_vertex_limit_stops_a_shapefile_instead_of_skipping_the_layer(tmp_path: Path) -> None:
    path = tmp_path / "plots.zip"
    path.write_bytes(build_sample("plots_shapefile"))  # three rectangles, 15 vertices
    with pytest.raises(IngestError) as info:
        process_file(path, "plots.zip", limits=IngestLimits(max_coordinates=10))
    assert info.value.code == "too_complex"  # not a skipped layer / missing_component
    assert process_file(path, "plots.zip", limits=IngestLimits(max_coordinates=15)).feature_count == 3


def test_backslash_layer_path_resolves_on_any_os(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "zones.geojson").write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
    write_manifest(
        root,
        DataPackManifest(
            name="pack",
            bbox=[77.0, 12.0, 78.0, 13.0],
            created_utc="2026-01-01T00:00:00Z",
            layers={"zones": LayerEntry(file=r"sub\zones.geojson")},
        ),
    )
    found = DataPack(root).path("zones")
    assert found is not None
    assert found.resolve() == (root / "sub" / "zones.geojson").resolve()


@pytest.mark.parametrize(
    "name",
    ["", "  ", "/etc/passwd", "../dem.tif", "a/../../b.tif", "C:dem.tif", r"C:\data\dem.tif", r"\data\dem.tif", r"..\dem.tif"],
)
def test_layer_file_rejects_paths_outside_the_pack(name: str) -> None:
    with pytest.raises(ValidationError):
        LayerEntry(file=name)


@pytest.mark.parametrize("url", ["https://flightmd.example/report/abc", "http://localhost:3000/report/1", None, ""])
def test_flightmd_url_accepts_http_links(url: str | None) -> None:
    params = FlightAuditParams(report=REPORT, flightmd_url=url)
    assert params.flightmd_url in (None, url)


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,hi", "ftp://x/y", "//evil.example/x", "http://", "https:// x", "https://user:pw@host/x"],
)
def test_flightmd_url_rejects_other_schemes(url: str) -> None:
    with pytest.raises(ValidationError):
        FlightAuditParams(report=REPORT, flightmd_url=url)


def test_raster_errors_keep_the_server_path_private(tmp_path: Path) -> None:
    from airbrief_core.data.raster import RasterSampler
    from airbrief_core.errors import DataPackError

    folder = tmp_path / "private-server-folder"
    folder.mkdir()
    (folder / "dem.tif").write_bytes(b"this is not a GeoTIFF")
    for path, code in ((folder / "dem.tif", "datapack_invalid"), (folder / "missing.tif", "layer_missing")):
        with pytest.raises(DataPackError) as info:
            RasterSampler(path).sample([77.5], [12.9])
        assert info.value.code == code
        assert "private-server-folder" not in info.value.message
        assert "private-server-folder" not in str(info.value.details)


def test_module_result_that_fails_validation_is_an_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    from airbrief_core.analyses import REGISTRY
    from airbrief_core.errors import AirBriefError
    from airbrief_core.orchestrator import run_analysis_sync

    def bad_result(params: object, ctx: object) -> dict[str, object]:
        return {"secret": "internal value"}

    monkeypatch.setitem(REGISTRY, "survey", dataclasses.replace(REGISTRY["survey"], run=bad_result))
    geometry = {"type": "Polygon", "coordinates": [[[77.5, 12.9], [77.51, 12.9], [77.51, 12.91], [77.5, 12.9]]]}
    with pytest.raises(AirBriefError) as info:
        run_analysis_sync("survey", {"geometry": geometry})
    assert info.value.code == "internal_error"
    assert info.value.message == "The survey analysis failed unexpectedly."


_SHOW_TRACKS = [
    {"id": "a", "times": [0, 1], "coordinates": [[77.5, 12.9, 0], [77.5001, 12.9, 10]]},
    {"id": "b", "times": [0, 1], "coordinates": [[77.501, 12.9, 0], [77.5011, 12.9, 10]]},
]


@pytest.mark.parametrize(
    "origin",
    [[float("nan"), 12.9], [500.0, 12.9], [77.5, 95.0], [77.5, 12.9, float("inf")], [77.5, 12.9, 1e308]],
)
def test_show_origin_must_be_a_finite_wgs84_position(origin: list[float]) -> None:
    # Security review: these were accepted (JSON bodies may carry NaN/Infinity) and gave a
    # COMPLETED analysis of NaN heights, or a FAILED job with a misleading geometry error.
    from airbrief_core.models.analyses import ShowParams

    with pytest.raises(ValidationError):
        ShowParams.model_validate({"tracks": _SHOW_TRACKS, "origin": origin})
    assert ShowParams.model_validate({"tracks": _SHOW_TRACKS, "origin": [77.5, 12.9, 905.0]}).origin == (77.5, 12.9, 905.0)
    assert ShowParams.model_validate({"tracks": _SHOW_TRACKS, "origin": [77.5, 12.9]}).origin == (77.5, 12.9)


# VERIFY-02: FlightMD parser errors never echo report values.


@pytest.mark.parametrize("value", ["internal secret token", None, True, {"nested": "secret"}])
def test_flightmd_parser_errors_do_not_echo_values(value: object) -> None:
    from airbrief_core.errors import AnalysisError
    from airbrief_core.integrations.flightmd import parse_flightmd_report

    report = {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [value, 77.51, 950.0]]}}
    with pytest.raises(AnalysisError) as info:
        parse_flightmd_report(report)
    assert info.value.code == "report_invalid"
    assert info.value.details == {"index": 1}
    assert "secret" not in info.value.message


def test_flightmd_parser_does_not_list_the_keys_of_other_json() -> None:
    from airbrief_core.errors import AnalysisError
    from airbrief_core.integrations.flightmd import parse_flightmd_report

    with pytest.raises(AnalysisError) as info:
        parse_flightmd_report({"schema_version": "1.5", "internal_admin_key": "x"})
    assert info.value.code == "report_invalid"
    assert "internal_admin_key" not in str(info.value.details)


# VERIFY-03: list bounds in analysis parameters.


_RING = {"zone": "red", "inner_km": 0, "outer_km": 5, "label": "r"}


def _rules(rules: int = 1, types: int = 1, rings: int = 1) -> dict:
    rule = {"aerodrome_types": ["large_airport"] * types, "rings": [_RING] * rings}
    return {"rules": [rule] * rules}


@pytest.mark.parametrize(
    ("ok", "too_many"),
    [
        (_rules(rules=50), _rules(rules=51)),
        (_rules(types=20), _rules(types=21)),
        (_rules(rings=20), _rules(rings=21)),
    ],
)
def test_zone_rule_lists_are_bounded(ok: dict, too_many: dict) -> None:
    from airbrief_core.models.common import ZoneRules

    ZoneRules.model_validate(ok)
    with pytest.raises(ValidationError) as info:
        ZoneRules.model_validate(too_many)
    assert info.value.errors()[0]["type"] == "too_long"


def test_track_lists_are_bounded() -> None:
    from airbrief_core.models.analyses import TrackInput
    from airbrief_core.models.tracks import AuditTrackInput

    times = [float(i) for i in range(500_001)]
    with pytest.raises(ValidationError):
        TrackInput.model_validate({"id": "a", "times": times, "coordinates": [[77.5, 12.9, 1.0], [77.5, 12.9, 1.0]]})
    with pytest.raises(ValidationError):
        AuditTrackInput.model_validate({"coordinates": [[77.5, 12.9], [77.6, 12.9]], "times": times})
