"""Tests for data packs: synthetic pack contents, registry selection and seed helpers."""

from __future__ import annotations

import http.client
import io
import json
import os
import time
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from shapely.geometry import Point, box, shape

from airbrief_core.data import DataPack, DataPackRegistry
from airbrief_core.data.aerodromes import AerodromeIndex
from airbrief_core.data.datapack import LAYER_KEYS, DataPackManifest, write_manifest
from airbrief_core.data.sources import REGIONS, copernicus_tile_url, copernicus_tiles_for_bbox
from airbrief_core.errors import DataPackError
from seed.datapack import (
    SYNTHETIC_BBOX,
    SYNTHETIC_CREATED_UTC,
    build_synthetic_pack,
    filter_ourairports,
    obstacles_from_overpass,
    parse_height,
    parse_levels,
    pois_from_overpass,
    water_from_overpass,
)

SURVEY = box(77.48, 12.90, 77.50, 12.91)


def test_manifest(synthetic_pack: DataPack) -> None:
    assert synthetic_pack.name == "demo"
    assert synthetic_pack.synthetic is True
    assert synthetic_pack.bbox.as_list() == pytest.approx(list(SYNTHETIC_BBOX))
    assert synthetic_pack.manifest.created_utc == SYNTHETIC_CREATED_UTC
    assert synthetic_pack.layers == list(LAYER_KEYS)
    for key in LAYER_KEYS:
        entry = synthetic_pack.manifest.layers[key]
        assert entry.file and entry.source == "synthetic" and entry.licence
    info = synthetic_pack.info()
    assert info["name"] == "demo" and info["synthetic"] is True and len(info["bbox"]) == 4
    assert info["attribution"]
    raw = json.loads((synthetic_pack.root / "manifest.json").read_text(encoding="utf-8"))
    assert set(raw) >= {"name", "version", "created_utc", "synthetic", "bbox", "layers", "attribution"}


def test_covers(synthetic_pack: DataPack) -> None:
    assert synthetic_pack.covers(SURVEY.bounds)
    assert synthetic_pack.covers([77.4, 12.8, 77.9, 13.2])
    assert not synthetic_pack.covers([77.0, 12.8, 77.9, 13.2])


def test_aerodromes(synthetic_pack: DataPack) -> None:
    aeros = synthetic_pack.aerodromes()
    assert isinstance(aeros, AerodromeIndex)
    assert sorted(a.ident for a in aeros) == ["VOBG", "VOBL", "VOJK", "VOYK"]
    nearest, dist = aeros.nearest(77.7066, 13.1986, k=1)[0]
    assert nearest.ident == "VOBL" and nearest.type == "large_airport" and dist < 0.01
    assert nearest.elevation_m == pytest.approx(3002 * 0.3048, abs=0.1)
    jakkur = aeros.by_ident("vojk")
    assert jakkur is not None and jakkur.name == "Jakkur Aerodrome" and jakkur.type == "small_airport"
    assert aeros.by_ident("VOBG").type == "medium_airport"  # type: ignore[union-attr]
    three = aeros.nearest(77.5946, 12.9716, k=3)
    assert len(three) == 3 and [d for _, d in three] == sorted(d for _, d in three)
    within = aeros.within(77.5946, 12.9716, 10.0)
    assert [a.ident for a, _ in within] == ["VOBG"]
    assert aeros.nearest(77.5946, 12.9716, k=1, types=["large_airport"])[0][0].ident == "VOBL"
    fc = aeros.to_feature_collection()
    assert len(fc["features"]) == 4


def test_obstacles(synthetic_pack: DataPack) -> None:
    obstacles = synthetic_pack.obstacles()
    assert len(obstacles) == 150
    heights = np.array([f.get("height_m") for f in obstacles])
    assert heights.min() >= 30 and heights.max() <= 180
    kinds = {f.get("kind") for f in obstacles}
    assert {"building", "mast", "communication_tower"} <= kinds
    lon0, lat0, lon1, lat1 = SYNTHETIC_BBOX
    assert all(lon0 <= f.lon <= lon1 and lat0 <= f.lat <= lat1 for f in obstacles)
    near_survey = obstacles.near_geometry(SURVEY, 200.0)
    assert len(near_survey) >= 2
    assert [d for _, d in near_survey] == sorted(d for _, d in near_survey)
    inside = obstacles.in_polygon(SURVEY)
    assert {f.id for f in inside} <= {f.id for f, _ in near_survey}
    assert all(d == 0.0 for f, d in near_survey if f in inside)
    around = obstacles.near(77.4885, 12.9046, 50.0)
    assert around and around[0][0].id == "obs-0001" and around[0][1] < 1.0


def test_pois_and_water(synthetic_pack: DataPack) -> None:
    pois = synthetic_pack.pois()
    assert len(pois) == 25
    assert {f.get("kind") for f in pois} == {"metro_station", "rail_station"}
    water = synthetic_pack.water()
    assert len(water) == 12
    inside = water.contains_xy([77.6650, 77.5946], [12.9350, 12.9716])
    assert inside.tolist() == [True, False]
    assert water.contains_xy(77.6650, 12.9350).shape == ()
    hits = water.intersecting(Point(77.6650, 12.9350).buffer(0.001))
    assert [h.get("name") for h in hits] == ["Synthetic Bellandur lake"]
    assert not water.intersecting(SURVEY)
    # Lakes are circles of the stated radius (area ≈ π r²).
    from airbrief_core.geo.geodesic import geodesic_area_perimeter

    bellandur = hits[0]
    area, _ = geodesic_area_perimeter(bellandur.geometry)
    assert area == pytest.approx(np.pi * 1100.0**2, rel=0.01)


def test_zones(synthetic_pack: DataPack) -> None:
    zones = synthetic_pack.zones()
    assert len(zones) == 1
    zone = zones[0]
    assert zone.name == "Demo restricted zone (synthetic)" and zone.zone == "red"
    assert zone.ceiling_agl_m is None
    assert shape(zone.geometry).contains(Point(77.5906, 12.9794))


def test_layers_are_cached(synthetic_pack: DataPack) -> None:
    assert synthetic_pack.dem() is synthetic_pack.dem()
    assert synthetic_pack.obstacles() is synthetic_pack.obstacles()


def test_synthetic_pack_is_deterministic(synthetic_pack: DataPack, tmp_path: Path) -> None:
    other = build_synthetic_pack(tmp_path, "demo")
    assert other == tmp_path / "demo"
    for name in ("manifest.json", "aerodromes.csv", "obstacles.geojson", "pois.geojson", "water.geojson", "zones.geojson"):
        assert (other / name).read_bytes() == (synthetic_pack.root / name).read_bytes(), name
    for name in ("dem.tif", "population.tif"):
        with rasterio.open(other / name) as a, rasterio.open(synthetic_pack.root / name) as b:
            np.testing.assert_array_equal(a.read(1), b.read(1))
            assert a.transform == b.transform
    # Rebuilding over an existing pack replaces it and leaves no temporary folder.
    build_synthetic_pack(tmp_path, "demo")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo"]


def test_invalid_pack_name(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        build_synthetic_pack(tmp_path, "../escape")


def _manifest_only(root: Path, name: str, bbox: list[float]) -> None:
    write_manifest(root / name, DataPackManifest(name=name, bbox=bbox, created_utc="2026-01-01T00:00:00Z"))


def test_registry_with_synthetic_pack(synthetic_pack_dir: Path) -> None:
    reg = DataPackRegistry(synthetic_pack_dir)
    assert reg.names() == ["demo"]
    assert reg.get("demo") is not None and reg.get("missing") is None
    assert reg.select(SURVEY.bounds).name == "demo"  # type: ignore[union-attr]
    assert reg.select([10.0, 10.0, 10.1, 10.1]) is None
    assert reg.select(None, preferred="demo").name == "demo"  # type: ignore[union-attr]
    assert reg.list()[0] is reg.list()[0]  # cached between calls


def test_registry_selection_rules(tmp_path: Path) -> None:
    _manifest_only(tmp_path, "big", [70.0, 10.0, 80.0, 20.0])
    _manifest_only(tmp_path, "small", [77.0, 12.0, 78.0, 13.0])
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "manifest.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    reg = DataPackRegistry(tmp_path)
    assert reg.names() == ["big", "small"]

    inside_small = [77.4, 12.4, 77.6, 12.6]
    assert reg.select(inside_small).name == "small"  # type: ignore[union-attr]
    assert reg.select(inside_small, preferred="big").name == "big"  # type: ignore[union-attr]
    assert reg.select(inside_small, preferred="nope").name == "small"  # type: ignore[union-attr]
    # Not contained anywhere: largest overlap, only if it holds the centroid.
    assert reg.select([79.0, 15.0, 80.4, 16.0]).name == "big"  # type: ignore[union-attr]
    assert reg.select([79.9, 15.0, 81.0, 16.0]) is None
    assert reg.select([90.0, 0.0, 91.0, 1.0]) is None
    # A point-like bbox works too.
    assert reg.select([77.5, 12.5, 77.5, 12.5]).name == "small"  # type: ignore[union-attr]
    assert reg.select(None) is None  # two packs, nothing preferred
    assert DataPackRegistry(tmp_path / "does-not-exist").list() == []


def test_registry_prefers_real_over_synthetic(tmp_path: Path) -> None:
    # The synthetic demo pack is smaller, but a real pack covering the area must win.
    write_manifest(
        tmp_path / "demo",
        DataPackManifest(name="demo", bbox=[77.3, 12.7, 77.95, 13.3], created_utc="2026-01-01T00:00:00Z", synthetic=True),
    )
    _manifest_only(tmp_path, "bengaluru", [77.25, 12.65, 78.0, 13.35])
    reg = DataPackRegistry(tmp_path)
    area = [77.48, 12.90, 77.50, 12.91]
    assert reg.select(area).name == "bengaluru"  # type: ignore[union-attr]
    assert reg.select(area, preferred="demo").name == "demo"  # type: ignore[union-attr]


def test_pack_without_layers(tmp_path: Path) -> None:
    _manifest_only(tmp_path, "bare", [0.0, 0.0, 1.0, 1.0])
    pack = DataPack(tmp_path / "bare")
    assert pack.layers == []
    assert pack.dem() is None and pack.population() is None
    assert len(pack.aerodromes()) == 0 and len(pack.obstacles()) == 0 and len(pack.water()) == 0
    assert pack.zones() == []
    assert not pack.water().contains_xy([0.5], [0.5]).any()


def test_pack_errors(tmp_path: Path) -> None:
    with pytest.raises(DataPackError) as missing:
        DataPack(tmp_path)
    assert missing.value.code == "datapack_missing"
    (tmp_path / "manifest.json").write_text(json.dumps({"name": "x", "bbox": [1, 1, 0, 0]}), encoding="utf-8")
    with pytest.raises(DataPackError) as invalid:
        DataPack(tmp_path)
    assert invalid.value.code == "datapack_invalid"
    (tmp_path / "manifest.json").write_text(
        json.dumps({"name": "x", "bbox": [0, 0, 1, 1], "layers": {"dem": {"file": "../dem.tif"}}}), encoding="utf-8"
    )
    with pytest.raises(DataPackError):
        DataPack(tmp_path)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("45", 45.0),
        ("45 m", 45.0),
        ("45.5m", 45.5),
        ("~30", 30.0),
        ("150 ft", 45.72),
        ("150'", 45.72),
        ("12'6\"", 3.81),
        ("20;35", 35.0),
        ("12,5", 12.5),
        (60, 60.0),
        ("abc", None),
        ("0", None),
        ("", None),
        (None, None),
        ("5000", None),
    ],
)
def test_parse_height(value: object, expected: float | None) -> None:
    result = parse_height(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, abs=0.01)


def test_parse_levels() -> None:
    assert parse_levels("12") == 12.0
    assert parse_levels("12;14") == 12.0
    assert parse_levels("x") is None
    assert parse_levels(None) is None


def test_obstacles_from_overpass() -> None:
    payload = {
        "elements": [
            {"type": "way", "id": 1, "center": {"lat": 12.9, "lon": 77.5}, "tags": {"building": "yes", "building:levels": "12"}},
            {"type": "way", "id": 2, "center": {"lat": 12.9, "lon": 77.5}, "tags": {"building": "yes", "height": "5"}},
            {"type": "node", "id": 3, "lat": 12.91, "lon": 77.51, "tags": {"man_made": "mast"}},
            {"type": "node", "id": 4, "lat": 12.92, "lon": 77.52, "tags": {"power": "tower", "height": "120 ft"}},
            {"type": "node", "id": 5, "lat": 12.93, "lon": 77.53, "tags": {"man_made": "tower", "tower:type": "communication"}},
            {"type": "way", "id": 6, "tags": {"man_made": "chimney"}},
        ]
    }
    feats = {f["id"]: f["properties"] for f in obstacles_from_overpass(payload)}
    assert set(feats) == {"osm-w1", "osm-n3", "osm-n4", "osm-n5"}
    assert feats["osm-w1"]["height_m"] == pytest.approx(38.4) and feats["osm-w1"]["height_source"] == "levels"
    assert feats["osm-n3"]["kind"] == "mast" and feats["osm-n3"]["height_source"] == "default"
    assert feats["osm-n4"]["kind"] == "power_tower" and feats["osm-n4"]["height_m"] == pytest.approx(36.58)
    assert feats["osm-n5"]["kind"] == "communication_tower"


def test_pois_from_overpass_dedupes() -> None:
    payload = {
        "elements": [
            {"type": "node", "id": 1, "lat": 12.97, "lon": 77.6, "tags": {"railway": "station", "station": "subway", "name": "MG Road"}},
            {"type": "way", "id": 2, "center": {"lat": 12.9701, "lon": 77.6001},
             "tags": {"public_transport": "station", "subway": "yes", "name": "MG Road"}},
            {"type": "node", "id": 3, "lat": 12.98, "lon": 77.57, "tags": {"railway": "station", "name": "City"}},
            {"type": "node", "id": 4, "lat": 12.97, "lon": 77.58, "tags": {"amenity": "bus_station", "name": "Majestic"}},
        ]
    }
    feats = pois_from_overpass(payload)
    assert [f["properties"]["kind"] for f in feats] == ["metro_station", "rail_station", "bus_station"]


def test_water_from_overpass_assembles_multipolygon() -> None:
    def pts(coords: list[tuple[float, float]]) -> list[dict[str, float]]:
        return [{"lon": x, "lat": y} for x, y in coords]

    payload = {
        "elements": [
            {
                "type": "relation",
                "id": 10,
                "tags": {"type": "multipolygon", "natural": "water", "name": "Two-part lake"},
                "members": [
                    {"type": "way", "ref": 1, "role": "outer", "geometry": pts([(77.50, 12.90), (77.52, 12.90), (77.52, 12.92)])},
                    {"type": "way", "ref": 2, "role": "outer", "geometry": pts([(77.52, 12.92), (77.50, 12.92), (77.50, 12.90)])},
                    {"type": "way", "ref": 3, "role": "inner",
                     "geometry": pts([(77.505, 12.905), (77.515, 12.905), (77.515, 12.915), (77.505, 12.915), (77.505, 12.905)])},
                ],
            },
            {"type": "way", "id": 11, "tags": {"natural": "water", "water": "pond"},
             "geometry": pts([(77.6, 12.9), (77.601, 12.9), (77.601, 12.901), (77.6, 12.901), (77.6, 12.9)])},
            {"type": "way", "id": 12, "tags": {"natural": "water"},
             "geometry": pts([(77.6, 12.9), (77.60001, 12.9), (77.60001, 12.90001), (77.6, 12.9)])},
        ]
    }
    feats = {f["id"]: f for f in water_from_overpass(payload)}
    assert set(feats) == {"osm-r10", "osm-w11"}
    lake = shape(feats["osm-r10"]["geometry"])
    assert lake.geom_type == "Polygon" and len(lake.interiors) == 1
    assert lake.area == pytest.approx(0.02 * 0.02 - 0.01 * 0.01, rel=1e-6)
    assert feats["osm-w11"]["properties"]["kind"] == "pond"


def test_filter_ourairports() -> None:
    text = (
        "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,iso_country,iso_region,municipality\n"
        '1,VOBL,large_airport,"Kempegowda International Airport",13.1986,77.7066,3002,AS,IN,IN-KA,Bengaluru\n'
        "2,XXXX,closed,Old strip,13.0,77.5,3000,AS,IN,IN-KA,\n"
        "3,VOMM,large_airport,Chennai,12.99,80.17,52,AS,IN,IN-TN,Chennai\n"
        "4,IN-0001,heliport,Pad,13.5,77.9,,AS,IN,IN-KA,\n"
    )
    rows = filter_ourairports(text, REGIONS["bengaluru"].bbox, 0.3)
    assert [r["ident"] for r in rows] == ["IN-0001", "VOBL"]
    index = AerodromeIndex.from_csv_text(text)
    assert {a.ident for a in index} == {"VOBL", "VOMM", "IN-0001"}
    assert index.by_ident("IN-0001").elevation_m is None  # type: ignore[union-attr]


def test_copernicus_urls() -> None:
    assert copernicus_tile_url(12, 77) == (
        "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N12_00_E077_00_DEM/"
        "Copernicus_DSM_COG_10_N12_00_E077_00_DEM.tif"
    )
    assert copernicus_tile_url(-1, -78, lite=True).endswith(
        "copernicus-dem-90m.s3.amazonaws.com/Copernicus_DSM_COG_30_S01_00_W078_00_DEM/Copernicus_DSM_COG_30_S01_00_W078_00_DEM.tif"
    )
    urls = copernicus_tiles_for_bbox(REGIONS["bengaluru"].bbox)
    assert len(urls) == 2 and all("E077" in u for u in urls)
    assert len(copernicus_tiles_for_bbox([77.0, 12.0, 78.0, 13.0])) == 1


@pytest.mark.network
@pytest.mark.slow
def test_ourairports_download(tmp_path: Path) -> None:
    from airbrief_core.data.fetch import download
    from airbrief_core.data.sources import OURAIRPORTS_URL

    path = download(OURAIRPORTS_URL, tmp_path / "airports.csv", timeout=120)
    index = AerodromeIndex.from_csv(path)
    assert index.by_ident("VOBL") is not None


def _fake_pack(root: Path, marker: str) -> Path:
    root.mkdir(parents=True)
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "marker.txt").write_text(marker, encoding="utf-8")
    return root


def test_swap_in_cleans_a_leftover_from_an_interrupted_run(tmp_path: Path) -> None:
    from seed.datapack import _swap_in

    final = _fake_pack(tmp_path / "demo", "old")
    _fake_pack(tmp_path / ".demo.previous", "stale")
    work = _fake_pack(tmp_path / ".demo.partial", "new")
    assert _swap_in(work, final) == final
    assert (final / "marker.txt").read_text(encoding="utf-8") == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo"]


def test_swap_in_keeps_the_old_pack_when_the_new_one_cannot_move_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from seed.datapack import _swap_in

    final = _fake_pack(tmp_path / "demo", "old")
    work = _fake_pack(tmp_path / ".demo.partial", "new")
    real_replace = Path.replace

    def failing_replace(self: Path, target: str | Path) -> Path:
        if self == work:
            raise PermissionError("locked")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    with pytest.raises(PermissionError):
        _swap_in(work, final)
    assert (final / "marker.txt").read_text(encoding="utf-8") == "old"


class _Reply:
    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status, self.headers, self._body = status, headers or {}, io.BytesIO(body)

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_server(monkeypatch: pytest.MonkeyPatch, replies: list[Any]) -> list[str | None]:
    """urlopen answers with ``replies`` in turn (an exception is raised); returns the Range headers sent."""
    from airbrief_core.data import fetch

    ranges: list[str | None] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> _Reply:
        ranges.append(request.get_header("Range"))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(fetch.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(fetch, "_sleep_backoff", lambda attempt: None)
    return ranges


def test_download_resumes_only_a_partial_file_that_still_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from airbrief_core.data.fetch import download

    url = "https://example.org/airports.csv"
    dest, part = tmp_path / "airports.csv", tmp_path / "airports.csv.part"
    # from two hours ago: the file on the server may have changed since, so start again
    part.write_bytes(b"old")
    os.utime(part, (time.time() - 7200, time.time() - 7200))
    ranges = _fake_server(monkeypatch, [_Reply(b"fresh")])
    assert download(url, dest).read_bytes() == b"fresh" and ranges == [None]
    # recent, but the server answers with another range: start again
    dest.unlink()
    part.write_bytes(b"abc")
    ranges = _fake_server(monkeypatch, [_Reply(b"zzz", 206, {"Content-Range": "bytes 0-2/3"}), _Reply(b"abcdef")])
    assert download(url, dest).read_bytes() == b"abcdef" and ranges == ["bytes=3-", None]
    # a 416 means the file shrank or changed: never keep the partial file as if it were complete
    dest.unlink()
    part.write_bytes(b"abcdefgh")
    gone = urllib.error.HTTPError(url, 416, "Range Not Satisfiable", Message(), None)
    ranges = _fake_server(monkeypatch, [gone, _Reply(b"new")])
    assert download(url, dest).read_bytes() == b"new" and ranges == ["bytes=8-", None]
    # a matching range is appended
    dest.unlink()
    part.write_bytes(b"abc")
    ranges = _fake_server(monkeypatch, [_Reply(b"def", 206, {"Content-Range": "bytes 3-5/6"})])
    assert download(url, dest).read_bytes() == b"abcdef" and ranges == ["bytes=3-"]


def test_a_dropped_response_is_retried_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from airbrief_core.data.fetch import download, fetch_bytes

    _fake_server(monkeypatch, [http.client.IncompleteRead(b"par"), _Reply(b"whole")])
    assert download("https://example.org/a.csv", tmp_path / "a.csv").read_bytes() == b"whole"
    _fake_server(monkeypatch, [http.client.IncompleteRead(b"par"), _Reply(b"{}")])
    assert fetch_bytes("https://example.org/b.json", retries=2) == b"{}"
    _fake_server(monkeypatch, [http.client.IncompleteRead(b"par")])
    with pytest.raises(DataPackError) as exc:
        fetch_bytes("https://example.org/b.json", retries=1)
    assert exc.value.code == "fetch_failed"
