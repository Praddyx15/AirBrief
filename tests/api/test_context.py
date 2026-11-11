"""Plan without a file: ``/api/context/``, ``/api/places/`` and ``/api/geometry/parse/``.

Runs against the synthetic ``demo`` pack (bbox 77.30–77.95 E, 12.70–13.30 N; VOBL at
13.1986 N 77.7066 E with IATA code BLR, VOYK at 13.1355 N 77.6061 E, 150 obstacles).
"""

from __future__ import annotations

import json
import math
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import shapely
from fastapi.testclient import TestClient
from shapely.geometry import box, shape

VOBL_VIEW = "77.6,13.1,77.8,13.3"


def _context(client: TestClient, **params: Any) -> Any:
    response = client.get("/api/context/", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_context_layers_are_selected_by_the_view(api_client: TestClient) -> None:
    body = _context(api_client, bbox=VOBL_VIEW)
    assert body["datapack"] == "demo"
    assert body["synthetic"] is True
    assert body["bbox"] == [77.6, 13.1, 77.8, 13.3]
    assert set(body["layers"]) == {"aerodromes", "rings", "zones", "obstacles", "water", "coverage"}
    view = box(77.6, 13.1, 77.8, 13.3)

    idents = [f["properties"]["ident"] for f in body["layers"]["aerodromes"]["features"]]
    assert idents == ["VOBL", "VOYK"]  # large before medium; VOBG and VOJK are outside the view
    vobl = body["layers"]["aerodromes"]["features"][0]["properties"]
    assert vobl["iata"] == "BLR" and vobl["type_label"] == "Large airport"

    for name in ("aerodromes", "obstacles"):
        for f in body["layers"][name]["features"]:
            lon, lat = f["geometry"]["coordinates"][:2]
            assert 77.6 <= lon <= 77.8 and 13.1 <= lat <= 13.3
    for name in ("rings", "water"):
        assert body["layers"][name]["features"], name
        for f in body["layers"][name]["features"]:
            assert shape(f["geometry"]).intersects(view)
    # Rings come whole (not clipped at the view), so their outlines stay true circles.
    ring_bounds = [shape(f["geometry"]).bounds for f in body["layers"]["rings"]["features"]]
    assert any(b[3] > 13.3 for b in ring_bounds)

    cover = body["layers"]["coverage"]["features"]
    assert len(cover) == 1
    assert shape(cover[0]["geometry"]).bounds == pytest.approx((77.3, 12.7, 77.95, 13.3))
    assert body["truncated"] == dict.fromkeys(body["layers"], False)
    assert body["counts"]["aerodromes"] == 2
    assert body["limit"] == 5000


def test_context_layer_whitelist(api_client: TestClient) -> None:
    body = _context(api_client, bbox=VOBL_VIEW, layers="aerodromes, coverage")
    assert set(body["layers"]) == {"aerodromes", "coverage"}
    response = api_client.get("/api/context/", params={"bbox": VOBL_VIEW, "layers": "aerodromes,roads"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_params"
    assert error["details"]["unknown"] == ["roads"]
    assert "obstacles" in error["details"]["allowed"]


def test_context_feature_cap_and_truncation(api_client: TestClient) -> None:
    full = _context(api_client, bbox="77.3,12.7,77.95,13.3", layers="obstacles")
    total = full["counts"]["obstacles"]
    assert total == len(full["layers"]["obstacles"]["features"]) > 3
    tallest = max(f["properties"]["height_m"] for f in full["layers"]["obstacles"]["features"])

    cut = _context(api_client, bbox="77.3,12.7,77.95,13.3", layers="obstacles,aerodromes", limit=3)
    assert len(cut["layers"]["obstacles"]["features"]) == 3
    assert cut["truncated"]["obstacles"] is True
    assert cut["counts"]["obstacles"] == total
    heights = [f["properties"]["height_m"] for f in cut["layers"]["obstacles"]["features"]]
    assert heights[0] == tallest and heights == sorted(heights, reverse=True)  # tallest kept
    assert cut["truncated"]["aerodromes"] is True  # 4 aerodromes, limit 3

    for limit in (0, 5001):
        assert api_client.get("/api/context/", params={"bbox": VOBL_VIEW, "limit": limit}).status_code == 422


def test_context_vertex_cap_keeps_the_largest_water(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from airbrief_core.data import context as core_context

    full = _context(api_client, bbox="77.3,12.7,77.95,13.3", layers="water")
    features = full["layers"]["water"]["features"]
    assert len(features) >= 2 and full["truncated"]["water"] is False
    areas = [shape(f["geometry"]).area for f in features]
    assert areas == sorted(areas, reverse=True)  # largest first
    first = int(shapely.get_num_coordinates(shape(features[0]["geometry"])))

    # A budget of the largest polygon's vertices keeps that polygon only, and says so.
    monkeypatch.setattr(core_context, "CONTEXT_VERTEX_LIMIT", first)
    cut = _context(api_client, bbox="77.3,12.7,77.95,13.3", layers="water")
    assert [f["id"] for f in cut["layers"]["water"]["features"]] == [features[0]["id"]]
    assert cut["truncated"]["water"] is True
    assert cut["counts"]["water"] == len(features)
    # At least one feature is always kept, however large.
    monkeypatch.setattr(core_context, "CONTEXT_VERTEX_LIMIT", 1)
    one = _context(api_client, bbox="77.3,12.7,77.95,13.3", layers="water")
    assert len(one["layers"]["water"]["features"]) == 1


def test_context_view_larger_than_two_degrees_is_refused(api_client: TestClient) -> None:
    response = api_client.get("/api/context/", params={"bbox": "76.9,12.5,79.0,13.5"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "area_too_large"
    assert error["details"]["limit_deg"] == 2.0
    # Exactly 2° × 2° is allowed, also when the decimal text is not exact in binary floats
    # (65.030927 − 63.030927 = 2.000000000000007), as the web app's recentred views can be.
    assert api_client.get("/api/context/", params={"bbox": "76.5,12.0,78.5,14.0"}).status_code == 200
    assert 65.030927 - 63.030927 > 2.0
    assert api_client.get("/api/context/", params={"bbox": "63.030927,12.0,65.030927,13.0"}).status_code == 200
    assert api_client.get("/api/context/", params={"bbox": "63.030927,12.0,65.030928,13.0"}).status_code == 422


@pytest.mark.parametrize("bbox", ["77,13,78", "a,b,c,d", "77.8,13.1,77.6,13.3", "77,13,78,nan", "-181,0,-180,1"])
def test_context_malformed_bbox(api_client: TestClient, bbox: str) -> None:
    response = api_client.get("/api/context/", params={"bbox": bbox})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_params"


def test_context_outside_every_pack(api_client: TestClient) -> None:
    body = _context(api_client, bbox="2.2,48.8,2.4,48.9", layers="aerodromes,rings")
    assert body["datapack"] is None
    assert body["layers"] == {
        "aerodromes": {"type": "FeatureCollection", "features": []},
        "rings": {"type": "FeatureCollection", "features": []},
    }
    assert body["truncated"] == {"aerodromes": False, "rings": False}


def test_context_view_at_the_edge_of_a_pack_uses_that_pack(api_client: TestClient) -> None:
    # The view's centre (77.95 E) is on the pack's east edge: the covered half still shows.
    body = _context(api_client, bbox="77.9,13.0,78.0,13.1", layers="coverage")
    assert body["datapack"] == "demo"
    assert len(body["layers"]["coverage"]["features"]) == 1


def test_context_etag_revalidation(api_client: TestClient) -> None:
    first = api_client.get("/api/context/", params={"bbox": VOBL_VIEW, "layers": "aerodromes"})
    tag = first.headers["etag"]
    assert tag.startswith('W/"')
    assert "max-age" in first.headers["cache-control"]
    again = api_client.get("/api/context/", params={"bbox": VOBL_VIEW, "layers": "aerodromes"}, headers={"If-None-Match": tag})
    assert again.status_code == 304
    other = api_client.get("/api/context/", params={"bbox": VOBL_VIEW, "layers": "coverage"}, headers={"If-None-Match": tag})
    assert other.status_code == 200 and other.headers["etag"] != tag


def _places(client: TestClient, q: str, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/api/places/", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("q", ["VOBL", "vobl", "BLR", "blr", "kempe", "Kempegowda International"])
def test_places_find_vobl(api_client: TestClient, q: str) -> None:
    results = _places(api_client, q)
    assert results, q
    first = results[0]
    assert first["kind"] == "aerodrome" and first["ident"] == "VOBL"
    assert (first["lon"], first["lat"]) == pytest.approx((77.7066, 13.1986))
    assert first["label"] == "VOBL · Kempegowda International Airport"
    assert first["detail"] == "Large airport · Bengaluru · IATA BLR"
    assert first["datapack"] == "demo"


def test_places_ranking(api_client: TestClient) -> None:
    assert [r["ident"] for r in _places(api_client, "hal")] == ["VOBG"]
    assert [r["ident"] for r in _places(api_client, "VO")] == ["VOBL", "VOBG", "VOYK", "VOJK"]  # by size
    # Municipality matches every aerodrome; limit applies.
    assert len(_places(api_client, "bengaluru")) == 4
    assert len(_places(api_client, "bengaluru", limit=2)) == 2
    assert _places(api_client, "zzzz") == []


def test_places_coordinates_first(api_client: TestClient) -> None:
    results = _places(api_client, "13.1979, 77.7063")
    assert results[0]["kind"] == "coordinates"
    assert (results[0]["lon"], results[0]["lat"]) == (77.7063, 13.1979)
    assert results[0]["label"] == "13.19790° N, 77.70630° E"
    assert "latitude, longitude" in results[0]["detail"]
    dms = _places(api_client, "13°11'52.4\"N 77°42'22.7\"E")[0]
    assert dms["kind"] == "coordinates"
    assert dms["lat"] == pytest.approx(13 + 11 / 60 + 52.4 / 3600)


def test_places_from_several_packs_are_merged_by_match_quality(
    make_client: Any, synthetic_pack_dir: Path, tmp_path: Path
) -> None:
    packs = tmp_path / "packs"
    shutil.copytree(synthetic_pack_dir / "demo", packs / "demo")
    other = packs / "aaa"  # listed before "demo" (packs are listed by name)
    other.mkdir()
    (other / "aerodromes.csv").write_text(
        "ident,type,name,latitude_deg,longitude_deg,elevation_ft,iso_country,municipality,iata_code\n"
        "XVOB,small_airport,Vobble Field,51.0,0.5,100,GB,Kent,\n",
        encoding="utf-8",
    )
    manifest = {
        "name": "aaa",
        "version": "1",
        "synthetic": False,
        "bbox": [0.0, 50.5, 1.0, 51.5],
        "layers": {"aerodromes": {"file": "aerodromes.csv", "source": "test", "licence": "MIT"}},
    }
    (other / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    client = make_client(datapack_dir=packs)
    # "vob" is an ident prefix of VOBL and VOBG (rank 1) but only a name prefix of XVOB (rank 2).
    results = _places(client, "vob")
    assert [r["ident"] for r in results] == ["VOBL", "VOBG", "XVOB"]
    assert [r["datapack"] for r in results] == ["demo", "demo", "aaa"]


def test_places_needs_a_query(api_client: TestClient) -> None:
    assert api_client.get("/api/places/").status_code == 422
    assert api_client.get("/api/places/", params={"q": ""}).status_code == 422
    assert api_client.get("/api/places/", params={"q": "VOBL", "limit": 26}).status_code == 422


def test_parse_wkt_polygon(api_client: TestClient) -> None:
    wkt = "POLYGON ((77.48 12.90, 77.50 12.90, 77.50 12.91, 77.48 12.91, 77.48 12.90))"
    response = api_client.post("/api/geometry/parse/", json={"text": wkt})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "Polygon" and body["source"] == "wkt" and body["vertices"] == 5
    assert shape(body["geometry"]).bounds == pytest.approx((77.48, 12.90, 77.50, 12.91))
    assert body["issues"] == []
    assert api_client.post("/api/geometry/parse", json={"text": wkt}).status_code == 200


def test_parse_coordinates_with_shape(api_client: TestClient) -> None:
    text = "13.19 77.70\n13.20 77.71\n13.21 77.70"
    body = api_client.post("/api/geometry/parse/", json={"text": text, "shape": "polygon"}).json()
    assert body["kind"] == "Polygon" and body["source"] == "coordinates"
    assert "ambiguous_order" in {i["code"] for i in body["issues"]}
    auto = api_client.post("/api/geometry/parse/", json={"text": text}).json()
    assert auto["kind"] == "LineString"


def test_parse_geojson_feature_collection(api_client: TestClient) -> None:
    fc = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"name": "a"}, "geometry": {"type": "Point", "coordinates": [77.5, 12.9]}}],
    }
    body = api_client.post("/api/geometry/parse/", json={"text": json.dumps(fc)}).json()
    assert body["source"] == "geojson" and body["kind"] == "GeometryCollection"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("POLYGON ((0 0, 1 1", "invalid_geometry"),
        ("95, 200", "invalid_coordinates"),
        ("POINT (500000 1400000)", "invalid_coordinates"),
        ("POINT (77 12)" + " " * 100_001, "too_complex"),
    ],
    ids=["unreadable-wkt", "out-of-range", "projected", "over-100-kB"],
)
def test_parse_errors(api_client: TestClient, text: str, code: str) -> None:
    response = api_client.post("/api/geometry/parse/", json={"text": text})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_parse_refuses_hostile_text_quickly_and_cleanly(api_client: TestClient) -> None:
    # Security review: deeply nested collections answered 500 (RecursionError while writing the
    # result back; deeper WKT crashed the process) and whitespace runs made the coordinate
    # parser backtrack for minutes.
    nested = '{"type":"GeometryCollection","geometries":[' * 950 + '{"type":"Point","coordinates":[1,2]}' + "]}" * 950
    response = api_client.post("/api/geometry/parse/", json={"text": nested})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "too_complex"
    started = time.perf_counter()
    response = api_client.post("/api/geometry/parse/", json={"text": "1" + " " * 5000 + "!"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_coordinates"
    response = api_client.get("/api/places/", params={"q": "N" + " " * 190 + "!"})
    assert response.status_code == 200 and response.json() == []
    assert time.perf_counter() - started < 2.0


def test_parse_rejects_unknown_fields(api_client: TestClient) -> None:
    response = api_client.post("/api/geometry/parse/", json={"text": "1, 1", "crs": "EPSG:3857"})
    assert response.status_code == 422


def _circle(lon: float, lat: float, radius_m: float, n: int = 64) -> list[list[float]]:
    """The 64-gon the web app builds (WGS84 radii of curvature at the centre latitude)."""
    a, f = 6_378_137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    phi = math.radians(lat)
    w = math.sqrt(1 - e2 * math.sin(phi) ** 2)
    meridian = a * (1 - e2) / w**3
    normal = a / w
    ring = []
    for i in range(n):
        t = 2 * math.pi * i / n
        ring.append([lon + math.degrees(radius_m * math.sin(t) / (normal * math.cos(phi))), lat + math.degrees(radius_m * math.cos(t) / meridian)])
    ring.append(ring[0])
    return ring


def test_pin_dataset_runs_airspace_without_a_file(api_client: TestClient, helpers: SimpleNamespace) -> None:
    lon, lat, radius = 77.7063, 13.1979, 500.0
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": "Pin", "radius_m": radius}, "geometry": {"type": "Point", "coordinates": [lon, lat]}},
            {
                "type": "Feature",
                "properties": {"name": "Pin radius", "radius_m": radius},
                "geometry": {"type": "Polygon", "coordinates": [_circle(lon, lat, radius)]},
            },
        ],
    }
    response = helpers.upload(api_client, "Pin 13.1979 N 77.7063 E.geojson", json.dumps(fc).encode())
    assert response.status_code == 201, response.text
    info = response.json()
    assert info["status"] == "COMPLETED" and info["feature_count"] == 2
    assert info["filename"] == "Pin 13.1979 N 77.7063 E.geojson"

    # The 64-gon's geodesic area is the polygon's: (n/2)·r²·sin(2π/n) = 99.839 % of πr².
    measured = api_client.get(f"/api/files/{info['id']}/measurements/").json()["items"]
    disk = next(m for m in measured if m["name"] == "Pin radius")["measurement"]
    assert disk["geodesic_area_m2"] == pytest.approx(32 * radius**2 * math.sin(2 * math.pi / 64), rel=2e-3)

    done = helpers.run_analysis(api_client, {"kind": "airspace", "dataset_id": info["id"], "params": {}})
    assert done["status"] == "COMPLETED", done
    result = done["result"]
    # Next to VOBL: inside its boundary (the DGCA 0–5 km band), so the evidence says permission is required.
    assert result["permission_required"] is True
    assert result["nearest_aerodromes"][0]["ident"] == "VOBL"


def test_pasted_wkt_polygon_becomes_a_survey_area(api_client: TestClient, helpers: SimpleNamespace) -> None:
    # The web app reads the paste, wraps the geometry as a FeatureCollection and uploads it.
    wkt = "POLYGON ((77.48 12.90, 77.50 12.90, 77.50 12.91, 77.48 12.91, 77.48 12.90))"
    parsed = api_client.post("/api/geometry/parse/", json={"text": wkt}).json()
    fc = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"name": "Pasted geometry"}, "geometry": parsed["geometry"]}],
    }
    response = helpers.upload(api_client, "Pasted geometry.geojson", json.dumps(fc).encode())
    assert response.status_code == 201, response.text
    info = response.json()
    assert info["status"] == "COMPLETED" and info["feature_count"] == 1

    done = helpers.run_analysis(api_client, {"kind": "survey", "dataset_id": info["id"], "params": {}})
    assert done["status"] == "COMPLETED", done
    # 0.02° × 0.01° at 12.905° N on WGS84: 0.02·π/180·N·cos φ = 2170.6 m by 0.01·π/180·M = 1106.3 m,
    # 240.1 ha (N, M: prime-vertical and meridian radii of curvature).
    assert done["result"]["area_ha"] == pytest.approx(240.1, rel=0.005)
    assert done["result"]["line_count"] > 0
