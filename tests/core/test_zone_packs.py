"""Aerodrome perimeters and runways in data packs, and the any-region pack builder (offline: the
downloads are replaced by fakes)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import Point, shape

from airbrief_core.data import DataPack, fetch
from airbrief_core.data.sources import (
    MAX_REGION_SPAN_DEG,
    REGIONS,
    check_region_bbox,
    worldpop_source,
    worldpop_url,
)
from seed import datapack as seed

ROWS = [
    {"ident": "VOBL", "type": "large_airport", "name": "Kempegowda", "latitude_deg": "13.1986", "longitude_deg": "77.7066",
     "iata_code": "BLR", "gps_code": "VOBL", "icao_code": "VOBL", "scheduled_service": "yes"},
    {"ident": "IN-0042", "type": "small_airport", "name": "Farm strip", "latitude_deg": "13.05", "longitude_deg": "77.40",
     "iata_code": "", "gps_code": "", "scheduled_service": "no"},
    {"ident": "VOBG", "type": "medium_airport", "name": "HAL", "latitude_deg": "12.9499", "longitude_deg": "77.6682",
     "iata_code": "", "gps_code": "VOBG", "scheduled_service": "no"},
]


def _way(osm_id: int, ring: list[tuple[float, float]], **tags: str) -> dict[str, Any]:
    return {"type": "way", "id": osm_id, "tags": {"aeroway": "aerodrome", **tags},
            "geometry": [{"lon": x, "lat": y} for x, y in ring]}


def _square(lon: float, lat: float, half: float) -> list[tuple[float, float]]:
    return [(lon - half, lat - half), (lon + half, lat - half), (lon + half, lat + half), (lon - half, lat + half),
            (lon - half, lat - half)]


def test_perimeters_join_by_icao_iata_or_containment() -> None:
    payload = {"elements": [
        _way(1, _square(77.70, 13.20, 0.02), icao="VOBL", name="Kempegowda International Airport"),
        _way(2, _square(77.70, 13.20, 0.005), iata="BLR"),  # smaller: the larger boundary stays
        _way(3, _square(77.40, 13.05, 0.003)),  # no tags: contains the farm strip's reference point
        _way(4, _square(76.00, 12.00, 0.01), icao="ZZZZ"),  # matches nothing
        {"type": "node", "id": 5, "lat": 12.95, "lon": 77.67, "tags": {"aeroway": "aerodrome", "icao": "VOBG"}},
    ]}
    feats = seed.perimeters_from_overpass(payload, ROWS)
    by_ident = {f["properties"]["ident"]: f for f in feats}
    assert set(by_ident) == {"VOBL", "IN-0042"}  # VOBG is only a node: no boundary
    assert by_ident["VOBL"]["properties"]["match"] == "icao" and by_ident["VOBL"]["id"] == "osm-w1"
    assert by_ident["IN-0042"]["properties"]["match"] == "contains"
    assert shape(by_ident["VOBL"]["geometry"]).contains(Point(77.7066, 13.1986))
    iata_only = seed.perimeters_from_overpass({"elements": [_way(2, _square(77.70, 13.20, 0.005), iata="blr")]}, ROWS)
    assert iata_only[0]["properties"]["ident"] == "VOBL" and iata_only[0]["properties"]["match"] == "iata"


def test_runways_from_ourairports() -> None:
    text = (
        "id,airport_ref,airport_ident,length_ft,width_ft,surface,lighted,closed,le_ident,le_latitude_deg,"
        "le_longitude_deg,le_elevation_ft,le_heading_degT,le_displaced_threshold_ft,he_ident,he_latitude_deg,"
        "he_longitude_deg\n"
        "1,1,VOBL,13123,148,ASP,1,0,09L,13.20,77.69,3000,90,,27R,13.20,77.73\n"
        "2,1,VOBL,10000,150,ASP,1,1,09R,13.19,77.69,3000,90,,27L,13.19,77.72\n"  # closed
        "3,2,VOMM,12000,150,ASP,1,0,07,12.99,80.16,50,70,,25,13.00,80.19\n"  # not in the pack
        "4,1,VOBL,3000,,GRS,0,0,H1,,,,,,,,\n"  # no ends
    )
    (runway,) = seed.runways_from_ourairports(text, ["vobl"])
    props = runway["properties"]
    assert props["airport_ident"] == "VOBL" and props["le_ident"] == "09L"
    assert props["width_m"] == pytest.approx(148 * 0.3048, abs=0.01)
    assert props["length_m"] == pytest.approx(13123 * 0.3048, abs=0.01)
    assert [list(c) for c in runway["geometry"]["coordinates"]] == [[77.69, 13.2], [77.73, 13.2]]


def test_synthetic_pack_has_outlines(synthetic_pack: DataPack) -> None:
    assert {"aerodrome_perimeters", "runways"} <= set(synthetic_pack.layers)
    assert synthetic_pack.integrity == "verified"
    assert set(synthetic_pack.perimeters()) == {"VOBL", "VOBG", "VOYK"}
    assert {k: len(v) for k, v in synthetic_pack.runways().items()} == {"VOBL": 2, "VOBG": 1, "VOYK": 1, "VOJK": 1}
    # the VOBL boundary is elongated north-south around its runways
    minx, miny, maxx, maxy = synthetic_pack.perimeters()["VOBL"].bounds
    assert (maxy - miny) / (maxx - minx) > 1.5
    assert {a.ident: a.scheduled_service for a in synthetic_pack.aerodromes()} == {
        "VOBL": True, "VOBG": False, "VOYK": False, "VOJK": False}


def test_worldpop_url_and_region_bbox() -> None:
    assert worldpop_url("npl") == (
        "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/2020/NPL/npl_ppp_2020_1km_Aggregated_UNadj.tif")
    assert worldpop_source("IND") is worldpop_source("ind")
    assert worldpop_source("LKA").key == "worldpop_lka_2020_1km"
    with pytest.raises(ValueError):
        worldpop_url("IN")
    assert check_region_bbox([85.1, 27.5, 85.6, 27.9]) == (85.1, 27.5, 85.6, 27.9)
    for bad in ([85.1, 27.5, 85.6], [85.6, 27.5, 85.1, 27.9], [0, 0, MAX_REGION_SPAN_DEG + 0.1, 1], [0, 95, 1, 96],
                [0, 0, float("nan"), 1]):
        with pytest.raises(ValueError):
            check_region_bbox(bad)
    assert REGIONS["bengaluru"].iso3 == "IND"


def test_cli_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    args = seed._parse_args(["--bbox", "85.1,27.5,85.6,27.9", "--name", "kathmandu", "--iso3", "npl"])
    assert args.bbox == (85.1, 27.5, 85.6, 27.9)
    region = seed.region_for_bbox(args.name, args.bbox, args.iso3)
    assert region.name == "kathmandu" and region.iso3 == "NPL" and region.bbox == (85.1, 27.5, 85.6, 27.9)
    for argv, message in (
        (["--bbox", "85.1,27.5,88.6,27.9", "--name", "x"], "at most"),
        (["--bbox", "85.1,27.5,85.6", "--name", "x"], "four numbers"),
        (["--bbox", "85.1,27.5,85.6,27.9"], "needs --name"),
        (["--bbox", "85.1,27.5,85.6,27.9", "--name", "x", "--iso3", "NP"], "alpha-3"),
        (["--region", "bengaluru", "--iso3", "IND"], "--iso3 applies to --bbox"),
        (["--synthetic", "--bbox", "1,1,2,2"], "not allowed"),
    ):
        with pytest.raises(SystemExit):
            seed._parse_args(argv)
        assert message in capsys.readouterr().err


def test_region_pack_from_a_bbox_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    airports = (
        "ident,type,name,latitude_deg,longitude_deg,elevation_ft,iso_country,municipality,iata_code,scheduled_service,gps_code\n"
        "VNKT,large_airport,Tribhuvan,27.6966,85.3591,4390,NP,Kathmandu,KTM,yes,VNKT\n"
    )
    runways = (
        "id,airport_ref,airport_ident,length_ft,width_ft,surface,lighted,closed,le_ident,le_latitude_deg,"
        "le_longitude_deg,he_ident,he_latitude_deg,he_longitude_deg\n"
        "1,1,VNKT,10007,148,ASP,1,0,02,27.684,85.352,20,27.708,85.366\n"
    )

    def download(url: str, dest: Path, **_: Any) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(runways if url.endswith("runways.csv") else airports, encoding="utf-8")
        return dest

    def overpass_query(query: str, **_: Any) -> tuple[dict[str, Any], str]:
        elements = []
        if '"aeroway"="aerodrome"' in query:
            elements = [_way(9, _square(85.359, 27.697, 0.015), icao="VNKT")]
        return {"elements": elements, "osm3s": {"timestamp_osm_base": "2026-10-19T00:00:00Z"}}, "https://overpass.test/api"

    def read_remote_mosaic(urls: list[str], bbox: Any, nodata: float, **_: Any) -> fetch.RemoteRaster:
        assert not any("worldpop" in u for u in urls)  # no --iso3: no population download
        array = np.full((40, 50), 1300.0, dtype=np.float32)
        return fetch.RemoteRaster(array=array, transform=from_origin(85.1, 27.9, 0.01, 0.01), crs="EPSG:4326",
                                  nodata=nodata, sources=urls, missing=[])

    monkeypatch.setattr(fetch, "download", download)
    monkeypatch.setattr(fetch, "overpass_query", overpass_query)
    monkeypatch.setattr(fetch, "read_remote_mosaic", read_remote_mosaic)
    root, status = seed.build_region_pack(seed.region_for_bbox("kathmandu", (85.1, 27.5, 85.6, 27.9), None),
                                          tmp_path, name="kathmandu", say=lambda _m: None)
    assert "population" not in status  # skipped: no --iso3
    assert status["aerodrome_perimeters"] and status["runways"] and status["aerodromes"]
    pack = DataPack(root)
    assert pack.integrity == "verified"
    assert pack.manifest.region == "kathmandu" and "population" not in pack.layers
    assert set(pack.perimeters()) == {"VNKT"} and len(pack.runways()["VNKT"]) == 1
    assert pack.manifest.layers["aerodrome_perimeters"].source_url == "https://overpass.test/api"
    assert pack.manifest.layers["runways"].source == "ourairports_runways"
    assert pack.perimeters()["VNKT"].contains(Point(85.3591, 27.6966))
