"""Known-answer tests for the built-in samples (``airbrief_core.samples``; RUNBOOK §6)."""

from __future__ import annotations

import io
import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pytest
import shapefile
from lxml import etree
from pyproj import CRS, Geod
from shapely.geometry import Point, Polygon, shape

from airbrief_core import _native
from airbrief_core import samples as smp
from airbrief_core.errors import AirBriefError
from airbrief_core.geo.crs import WGS84, local_area_crs
from airbrief_core.geo.frame import LocalFrame
from airbrief_core.geo.transform import get_transformer

NS = {"k": "http://www.opengis.net/kml/2.2", "gx": "http://www.google.com/kml/ext/2.2"}
GEOD = Geod(ellps="WGS84")
LIGHT_SAMPLES = [name for name in smp.SAMPLES if name != "zipbomb_small"]
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def _kml(name: str) -> etree._Element:
    return etree.fromstring(smp.build_sample(name), _PARSER)


def _coords(text: str) -> list[tuple[float, ...]]:
    return [tuple(float(v) for v in item.split(",")) for item in text.split()]


def _zip(name: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(smp.build_sample(name)))


def _resample(times: np.ndarray, positions: np.ndarray, rate_hz: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """Linear resampling of keyframes (T, N, 3) at ``rate_hz``."""
    steps = int(round((times[-1] - times[0]) * rate_hz))
    t_new = times[0] + np.arange(steps + 1) / rate_hz
    out = np.empty((t_new.size, positions.shape[1], 3))
    for i in range(positions.shape[1]):
        for axis in range(3):
            out[:, i, axis] = np.interp(t_new, times, positions[:, i, axis])
    return t_new, out


def test_catalogue_is_consistent() -> None:
    expected_files = {
        "tier0/square_1km_equator.kml",
        "tier0/square_1km_60N.kml",
        "tier0/plots_shapefile.zip",
        "tier0/mixed_features.kml",
        "tier0/climb_line_z.kml",
        "tier0/bowtie_invalid.kml",
        "tier0/no_prj.zip",
        "tier0/attack_zipslip.zip",
        "tier0/corrupt.kml",
        "tier0/zipbomb_small.zip",
        "modules/bengaluru_survey.kml",
        "modules/bengaluru_evtol.geojson",
        "modules/show_50_drones.kml",
        "modules/show_50_drones_clean.kml",
        "modules/show_50_drones.csv",
        "flightmd/flightmd_report_v15.json",
        "flightmd/flightmd_report_v12.json",
        "modules/training_flight.gpx",
    }
    assert {spec.filename for spec in smp.SAMPLES.values()} == expected_files
    for name, spec in smp.SAMPLES.items():
        assert spec.name == name
        assert spec.title and spec.description
        assert spec.module in {"measure", "survey", "evtol_siting", "show", "flight_audit"}


@pytest.mark.parametrize("name", LIGHT_SAMPLES)
def test_samples_are_deterministic(name: str) -> None:
    first = smp.build_sample(name)
    fresh = smp._BUILDERS[name]()
    assert isinstance(first, bytes) and len(first) > 0
    assert first == fresh


def test_unknown_sample_raises_not_found() -> None:
    with pytest.raises(AirBriefError) as info:
        smp.build_sample("does_not_exist")
    assert info.value.code == "not_found"


def test_known_answer_constants() -> None:
    assert smp.PLOT_GRID_AREAS_M2 == {"Plot A": 20000.0, "Plot B": 22500.0, "Plot C": 15000.0}
    assert smp.SHOW_CONFLICT_PAIR == ("D07", "D32")
    assert smp.SHOW_CONFLICT_TIME_S == 48.0
    assert smp.SHOW_CONFLICT_MIN_DISTANCE_M == pytest.approx(0.80)
    assert smp.FLIGHT_MAX_HEIGHT_M == 135.0
    assert smp.FLIGHT_EXCEEDANCE_START_M == pytest.approx(1157.14, abs=0.005)
    assert smp.FLIGHT_EXCEEDANCE_END_M == pytest.approx(1642.86, abs=0.005)


@pytest.mark.parametrize(("name", "lon0", "lat0"), [("square_1km_equator", 0.0, 0.0), ("square_1km_60N", 10.0, 60.0)])
def test_square_known_area(name: str, lon0: float, lat0: float) -> None:
    root = _kml(name)
    ring = _coords(root.findtext(".//k:Polygon//k:coordinates", namespaces=NS))
    assert len(ring) == 5 and ring[0] == ring[-1]
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    area, perimeter = GEOD.polygon_area_perimeter(lons, lats)
    assert abs(area) == pytest.approx(1_000_000.0, rel=1e-4)
    assert perimeter == pytest.approx(4_000.0, rel=1e-4)
    transformer = get_transformer(WGS84, local_area_crs(lon0, lat0))
    xs, ys = transformer.transform(lons, lats)
    assert Polygon(list(zip(xs, ys, strict=True))).area == pytest.approx(1_000_000.0, rel=1e-6)


def test_climb_line_known_lengths() -> None:
    root = _kml("climb_line_z")
    assert root.findtext(".//k:LineString/k:altitudeMode", namespaces=NS) == "relativeToGround"
    (lon1, lat1, z1), (lon2, lat2, z2) = _coords(root.findtext(".//k:LineString/k:coordinates", namespaces=NS))
    _, _, length = GEOD.inv(lon1, lat1, lon2, lat2)
    assert length == pytest.approx(1000.0, abs=0.01)
    assert (z1, z2) == (0.0, 100.0)
    assert math.hypot(length, z2 - z1) == pytest.approx(1004.99, abs=0.01)
    assert lon2 > lon1 and abs(lat2 - lat1) < 1e-4


def _read_plots(name: str, stem: str) -> shapefile.Reader:
    archive = _zip(name)
    return shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{stem}.shp")),
        shx=io.BytesIO(archive.read(f"{stem}.shx")),
        dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
        encoding="utf-8",
    )


def _ring_area(points: list[list[float]]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points[:-1], points[1:], strict=True):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def test_plots_shapefile_grid_areas_and_attributes() -> None:
    archive = _zip("plots_shapefile")
    assert sorted(archive.namelist()) == ["plots.cpg", "plots.dbf", "plots.prj", "plots.shp", "plots.shx"]
    assert archive.read("plots.cpg") == b"UTF-8"
    prj = archive.read("plots.prj").decode("ascii")
    assert CRS.from_wkt(prj).to_epsg() == 32643

    reader = _read_plots("plots_shapefile", "plots")
    assert reader.shapeType == shapefile.POLYGON
    assert [f[0] for f in reader.fields[1:]] == ["name", "crop", "owner"]
    areas = {}
    for shape_rec in reader.shapeRecords():
        record = shape_rec.record.as_dict()
        assert record["crop"] and record["owner"]
        areas[record["name"]] = _ring_area(shape_rec.shape.points)
    assert areas == {"Plot A": 20000.0, "Plot B": 22500.0, "Plot C": 15000.0}

    # Near Bengaluru in UTM 43N.
    to_wgs = get_transformer(CRS.from_epsg(32643), WGS84)
    lon, lat = to_wgs.transform(*reader.bbox[:2])
    assert 77.4 < lon < 77.7 and 12.8 < lat < 13.1


def test_no_prj_has_no_prj() -> None:
    archive = _zip("no_prj")
    names = archive.namelist()
    assert not any(n.endswith(".prj") for n in names)
    assert {"plots_no_prj.shp", "plots_no_prj.shx", "plots_no_prj.dbf"} <= set(names)
    assert len(_read_plots("no_prj", "plots_no_prj").shapes()) == 3


def test_mixed_features_structure() -> None:
    root = _kml("mixed_features")
    survey = root.find("k:Document/k:Folder", namespaces=NS)
    assert survey.findtext("k:name", namespaces=NS) == "Survey"
    children = [f.findtext("k:name", namespaces=NS) for f in survey.findall("k:Folder", namespaces=NS)]
    assert children == ["Points", "Lines", "Areas"]
    assert len(root.findall(".//k:Point", namespaces=NS)) == 1
    assert len(root.findall(".//k:LineString", namespaces=NS)) == 1
    assert len(root.findall(".//k:Polygon/k:innerBoundaryIs", namespaces=NS)) == 1
    assert len(root.findall(".//k:ExtendedData", namespaces=NS)) == 3
    polygon = root.find(".//k:Polygon", namespaces=NS)
    outer = _coords(polygon.findtext("k:outerBoundaryIs//k:coordinates", namespaces=NS))
    inner = _coords(polygon.findtext("k:innerBoundaryIs//k:coordinates", namespaces=NS))
    shell = Polygon([c[:2] for c in outer], [[c[:2] for c in inner]])
    assert shell.is_valid


def test_bowtie_is_invalid() -> None:
    ring = _coords(_kml("bowtie_invalid").findtext(".//k:coordinates", namespaces=NS))
    assert not Polygon(ring).is_valid


def test_corrupt_kml_does_not_parse() -> None:
    with pytest.raises(etree.XMLSyntaxError):
        etree.fromstring(smp.build_sample("corrupt"), _PARSER)


def test_zipslip_entry() -> None:
    names = _zip("attack_zipslip").namelist()
    assert "../evil.txt" in names


def test_zipbomb_is_large_uncompressed() -> None:
    data = smp.build_sample("zipbomb_small")
    assert len(data) < 1_000_000
    infos = zipfile.ZipFile(io.BytesIO(data)).infolist()
    assert len(infos) == 1
    assert infos[0].file_size == smp.ZIPBOMB_UNCOMPRESSED_BYTES > 200 * 1024 * 1024


def test_bengaluru_survey_area() -> None:
    ring = _coords(_kml("bengaluru_survey").findtext(".//k:coordinates", namespaces=NS))
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    assert min(lons) == pytest.approx(77.485) and max(lons) == pytest.approx(77.497)
    assert min(lats) == pytest.approx(12.900) and max(lats) == pytest.approx(12.910)
    area, _ = GEOD.polygon_area_perimeter(lons, lats)
    assert 1.15e6 < abs(area) < 1.23e6


def test_bengaluru_evtol_covers_both_sites() -> None:
    collection = json.loads(smp.build_sample("bengaluru_evtol"))
    features = collection["features"]
    area = shape(features[0]["geometry"])
    points = {f["properties"]["name"]: shape(f["geometry"]) for f in features[1:]}
    assert set(points) == {"Whitefield", "Electronic City"}
    assert points["Whitefield"].equals(Point(77.750, 12.970))
    assert points["Electronic City"].equals(Point(77.670, 12.845))
    for point in points.values():
        assert area.contains(point)
        assert area.exterior.distance(point) >= 0.03


def test_show_conflict_analytic() -> None:
    ids, times, pos = smp.show_keyframes(conflict=True)
    assert ids[7] == "D07" and ids[32] == "D32"
    assert times.tolist() == [float(t) for t in range(127)]
    k = int(smp.SHOW_CONFLICT_TIME_S)
    d07, d32 = pos[k, 7], pos[k, 32]
    assert d07[:2].tolist() == [0.0, 0.0] and d32[:2].tolist() == [0.0, 0.0]
    assert float(np.linalg.norm(d32 - d07)) == pytest.approx(0.80, abs=1e-9)
    # The pair swaps slots: D07 ends where D32 started (after the 36 degree rotation). Only the
    # horizontal slot is swapped; D32 keeps its 50.8 m altitude.
    np.testing.assert_allclose(pos[56, 7, :2], pos[40, 32, :2], atol=1e-6)
    np.testing.assert_allclose(pos[56, 32, :2], pos[40, 7, :2], atol=1e-6)


@pytest.mark.parametrize("conflict", [True, False])
def test_show_separation_at_10hz(conflict: bool) -> None:
    _, times, pos = smp.show_keyframes(conflict=conflict)
    t10, pos10 = _resample(times, pos)
    airborne = pos10[:, :, 2] > 1.0
    pairs = _native.pairs_within(pos10, airborne, 1.5)
    dist, ii, jj = _native.min_separation_frames(pos10, airborne)
    if conflict:
        assert [(t10[f], i, j) for f, i, j, _ in pairs] == [(48.0, 7, 32)]
        assert pairs[0][3] == pytest.approx(0.80, abs=1e-6)
        k = int(np.argmin(dist))
        assert (t10[k], ii[k], jj[k]) == (48.0, 7, 32)
        assert dist[k] == pytest.approx(0.80, abs=1e-6)
    else:
        assert pairs == []
        assert float(dist[np.isfinite(dist)].min()) == pytest.approx(50.0 * math.sin(math.pi / 50.0), abs=1e-3)


@pytest.mark.parametrize("conflict", [True, False])
def test_show_dynamics_within_default_limits(conflict: bool) -> None:
    _, times, pos = smp.show_keyframes(conflict=conflict)
    _, pos10 = _resample(times, pos)
    half = 5  # 1 s window at 10 Hz
    vel = (pos10[2 * half :] - pos10[: -2 * half]) / 1.0
    acc = (vel[2 * half :] - vel[: -2 * half]) / 1.0
    assert float(np.linalg.norm(vel[:, :, :2], axis=2).max()) < 8.0
    assert float(vel[:, :, 2].max()) < 4.0
    assert float(vel[:, :, 2].min()) > -3.0
    assert float(np.linalg.norm(acc, axis=2).max()) < 4.0


@pytest.mark.parametrize("conflict", [True, False])
def test_show_geofence(conflict: bool) -> None:
    _, _, pos = smp.show_keyframes(conflict=conflict)
    assert float(np.abs(pos[:, :, :2]).max()) <= 60.0
    assert float(pos[:, :, 1].min()) > -130.0 + 50.0  # clear of the audience buffer
    assert float(pos[:, :, 2].max()) <= 120.0
    assert float(pos[0, :, 2].max()) == 0.0 and float(pos[-1, :, 2].max()) == 0.0


@pytest.mark.parametrize(("name", "conflict"), [("show_50_drones", True), ("show_50_drones_clean", False)])
def test_show_kml_tracks(name: str, conflict: bool) -> None:
    root = _kml(name)
    folders = {f.findtext("k:name", namespaces=NS): f for f in root.iter(f"{{{NS['k']}}}Folder")}
    assert set(folders) == {"Drones", "Site"}
    placemarks = folders["Drones"].findall("k:Placemark", namespaces=NS)
    assert [p.findtext("k:name", namespaces=NS) for p in placemarks] == [f"D{i:02d}" for i in range(50)]
    site = [p.findtext("k:name", namespaces=NS) for p in folders["Site"].findall("k:Placemark", namespaces=NS)]
    assert site == ["Show box", "Audience"]

    _, _, pos = smp.show_keyframes(conflict=conflict)
    frame = LocalFrame(smp.SHOW_ORIGIN[0], smp.SHOW_ORIGIN[1])
    for index in (0, 7, 32, 49):
        track = placemarks[index].find("gx:Track", namespaces=NS)
        assert track.findtext("k:altitudeMode", namespaces=NS) == "relativeToGround"
        whens = [w.text for w in track.findall("k:when", namespaces=NS)]
        assert len(whens) == 127
        assert whens[0] == "2026-01-01T19:00:00Z" and whens[-1] == "2026-01-01T19:02:06Z"
        coords = np.array([[float(v) for v in c.text.split()] for c in track.findall("gx:coord", namespaces=NS)])
        assert coords.shape == (127, 3)
        xs, ys = frame.xy(coords[:, 0], coords[:, 1])
        np.testing.assert_allclose(xs, pos[:, index, 0], atol=2e-3)
        np.testing.assert_allclose(ys, pos[:, index, 1], atol=2e-3)
        np.testing.assert_allclose(coords[:, 2], pos[:, index, 2], atol=1e-3)


def test_show_csv_matches_keyframes() -> None:
    lines = smp.build_sample("show_50_drones_csv").decode("utf-8").splitlines()
    assert lines[0] == "# origin=77.5920,13.0005,0"
    assert lines[1] == "drone_id,t,x,y,z"
    rows = [line.split(",") for line in lines[2:]]
    assert len(rows) == 50 * 127
    ids, _, pos = smp.show_keyframes(conflict=True)
    for row in rows:
        i = ids.index(row[0])
        k = int(float(row[1]))
        np.testing.assert_allclose([float(v) for v in row[2:]], pos[k, i], atol=6e-5)
    conflict_rows = {r[0]: r for r in rows if r[1] == "48.0" and r[0] in smp.SHOW_CONFLICT_PAIR}
    assert conflict_rows["D07"][2:] == ["0.0000", "0.0000", "50.0000"]
    assert conflict_rows["D32"][2:] == ["0.0000", "0.0000", "50.8000"]


def _crossings(distances: np.ndarray, heights: np.ndarray, limit: float) -> list[tuple[float, float]]:
    segments: list[tuple[float, float]] = []
    start: float | None = None
    for k in range(1, len(heights)):
        h0, h1 = heights[k - 1], heights[k]
        d0, d1 = distances[k - 1], distances[k]
        if h0 <= limit < h1:
            start = d0 + (limit - h0) / (h1 - h0) * (d1 - d0)
        elif h0 > limit >= h1 and start is not None:
            segments.append((start, d0 + (h0 - limit) / (h0 - h1) * (d1 - d0)))
            start = None
    return segments


@pytest.mark.parametrize(("name", "version"), [("flightmd_report_v15", "1.5"), ("flightmd_report_v12", "1.2")])
def test_flightmd_report_known_answers(name: str, version: str) -> None:
    report = json.loads(smp.build_sample(name))
    for key in (
        "report_id",
        "schema_version",
        "overall_score",
        "score_label",
        "executive_summary",
        "metadata",
        "findings",
        "param_change_sheet",
        "analyser_results",
        "processing_time_ms",
        "file_name",
        "file_size_bytes",
    ):
        assert key in report
    assert report["schema_version"] == version
    assert ("letter_grade" in report) is (version == "1.5")
    meta = report["metadata"]
    assert meta["location_name"] == "Bengaluru, India"
    path = np.array(meta["gps_path"], dtype=float)  # [lat, lon, alt]
    assert path.shape == (301, 3)
    if version == "1.5":
        assert len(meta["gps_path_hdop"]) == 301
    else:
        assert "gps_path_hdop" not in meta
    assert path[0, 0] == pytest.approx(12.905) and path[0, 1] == pytest.approx(77.49)
    assert path[0, 2] == smp.FLIGHT_HOME_ALT_AMSL_M

    _, _, steps = GEOD.inv(path[:-1, 1], path[:-1, 0], path[1:, 1], path[1:, 0])
    np.testing.assert_allclose(steps, 10.0, atol=0.05)
    distances = np.concatenate([[0.0], np.cumsum(steps)])
    assert distances[-1] == pytest.approx(3000.0, abs=0.5)

    heights = path[:, 2] - path[0, 2]
    assert float(heights.max()) == smp.FLIGHT_MAX_HEIGHT_M == 135.0
    assert heights[-1] == 0.0
    segments = _crossings(distances, heights, 120.0)
    assert len(segments) == 1
    assert segments[0][0] == pytest.approx(1157.14, abs=0.5)
    assert segments[0][1] == pytest.approx(1642.86, abs=0.5)
    assert meta["max_altitude_m"] == 135.0
    assert meta["total_distance_m"] == 3000.0


def test_flight_profile_function() -> None:
    assert smp.flight_height_above_takeoff(0.0) == 0.0
    assert smp.flight_height_above_takeoff(300.0) == 100.0
    assert smp.flight_height_above_takeoff(1400.0) == 135.0
    assert smp.flight_height_above_takeoff(smp.FLIGHT_EXCEEDANCE_START_M) == pytest.approx(120.0)
    assert smp.flight_height_above_takeoff(smp.FLIGHT_EXCEEDANCE_END_M) == pytest.approx(120.0)
    assert smp.flight_height_above_takeoff(3000.0) == 0.0


def test_training_flight_function_known_answers() -> None:
    pos = smp.training_flight_position
    assert pos(0.0) == (0.0, 0.0, 0.0)
    assert pos(40.0) == (0.0, 0.0, 100.0)
    assert pos(140.0) == (400.0, 0.0, smp.TRAINING_MAX_HEIGHT_M)
    assert pos(smp.TRAINING_ABOVE_120_START_S)[2] == pytest.approx(120.0)
    assert pos(smp.TRAINING_ABOVE_120_END_S)[2] == pytest.approx(120.0)
    xmax = smp.TRAINING_BOX_XY[2]
    assert pos(smp.TRAINING_EXIT_START_S)[0] == xmax and pos(smp.TRAINING_EXIT_END_S)[0] == xmax
    assert math.hypot(*pos(220.0)[:2]) == pytest.approx(smp.TRAINING_FARTHEST_M)
    assert max(pos(float(t))[0] for t in range(401)) - xmax == smp.TRAINING_EXIT_MAX_OUTSIDE_M
    assert pos(float(smp.TRAINING_DURATION_S)) == (0.0, 0.0, 0.0)


def test_training_flight_gpx_structure() -> None:
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    root = etree.fromstring(smp.build_sample("training_flight_gpx"), _PARSER)
    assert root.get("version") == "1.1"
    trkpts = root.findall("g:trk/g:trkseg/g:trkpt", ns)
    assert len(trkpts) == smp.TRAINING_DURATION_S + 1
    assert trkpts[0].findtext("g:time", namespaces=ns) == "2026-03-21T04:30:00Z"
    assert trkpts[-1].findtext("g:time", namespaces=ns) == "2026-03-21T04:36:40Z"
    eles = [float(p.findtext("g:ele", namespaces=ns) or "nan") for p in trkpts]
    assert max(eles) == smp.TRAINING_HOME_ELE_M + smp.TRAINING_MAX_HEIGHT_M
    rtepts = root.findall("g:rte/g:rtept", ns)
    assert root.findtext("g:rte/g:name", namespaces=ns) == "Training box"
    assert len(rtepts) == 5 and rtepts[0].attrib == rtepts[-1].attrib
    # The box corners are the AEQD rectangle of the take-off point.
    frame = LocalFrame(*smp.TRAINING_HOME)
    x, y = frame.xy(np.array([float(rtepts[2].get("lon"))]), np.array([float(rtepts[2].get("lat"))]))
    assert (float(x[0]), float(y[0])) == pytest.approx((smp.TRAINING_BOX_XY[2], smp.TRAINING_BOX_XY[3]), abs=1e-3)


def test_write_all(tmp_path: Path) -> None:
    paths = smp.write_all(tmp_path / "samples")
    assert len(paths) == len(smp.SAMPLES)
    for path, (name, spec) in zip(paths, smp.SAMPLES.items(), strict=True):
        assert path == tmp_path / "samples" / spec.filename
        assert path.read_bytes() == smp.build_sample(name)
