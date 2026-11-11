"""Data-pack integrity (MISS-02): layer hashes written by the seed, checked when a pack is opened,
cached per file, refused on mismatch, flagged as unverified on older packs, and recorded in every
analysis envelope; the Overpass endpoint that served each OSM layer is recorded too."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from airbrief_core.data import datapack as datapack_module
from airbrief_core.data import fetch
from airbrief_core.data.datapack import DataPack, DataPackManifest, DataPackRegistry, content_sha256, write_manifest
from airbrief_core.data.sources import OVERPASS_ENDPOINTS, REGIONS
from airbrief_core.errors import DataPackError
from airbrief_core.orchestrator import run_analysis_sync
from seed import datapack as seed

AREA = {"type": "Polygon", "coordinates": [[[77.48, 12.90], [77.50, 12.90], [77.50, 12.91], [77.48, 12.91], [77.48, 12.90]]]}


@pytest.fixture
def pack_copy(tmp_path: Path, synthetic_pack: DataPack) -> Path:
    """A private copy of the synthetic pack, safe to tamper with."""
    root = tmp_path / "packs" / "demo"
    shutil.copytree(synthetic_pack.root, root)
    return root


def _manifest(root: Path) -> dict[str, Any]:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _tamper(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["features"] = []
    path.write_text(json.dumps(data), encoding="utf-8")


def test_seed_writes_a_hash_per_layer_and_the_content_hash(synthetic_pack: DataPack) -> None:
    raw = _manifest(synthetic_pack.root)
    lines = []
    for key, entry in raw["layers"].items():
        digest = hashlib.sha256((synthetic_pack.root / entry["file"]).read_bytes()).hexdigest()
        assert entry["sha256"] == digest, key
        lines.append((entry["file"], digest))
    # sha256sum format, sorted by file: reproducible with standard tools.
    listing = "".join(f"{sha}  {name}\n" for name, sha in sorted(lines))
    assert raw["content_sha256"] == hashlib.sha256(listing.encode("utf-8")).hexdigest()
    assert content_sha256(lines) == raw["content_sha256"]
    assert synthetic_pack.integrity == "verified" and synthetic_pack.integrity_problems == []
    assert synthetic_pack.content_sha256 == raw["content_sha256"]
    info = synthetic_pack.info()
    assert info["integrity"] == "verified" and info["content_sha256"] == raw["content_sha256"]


@pytest.mark.parametrize("change", ["edit", "delete", "drop_from_manifest", "edit_hash_only"])
def test_a_changed_pack_is_unusable(pack_copy: Path, change: str) -> None:
    zones = pack_copy / "zones.geojson"
    if change == "edit":
        _tamper(zones)
    elif change == "delete":
        zones.unlink()
    else:
        raw = _manifest(pack_copy)
        if change == "drop_from_manifest":
            del raw["layers"]["zones"]  # hides the restricted areas without touching a file
        else:
            raw["layers"]["zones"]["sha256"] = "0" * 64
        (pack_copy / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")

    pack = DataPack(pack_copy)
    assert pack.integrity == "mismatch" and pack.integrity_problems
    assert pack.content_sha256 is None
    assert pack.info()["integrity"] == "mismatch"
    with pytest.raises(DataPackError) as exc:
        pack.obstacles()
    assert exc.value.code == "datapack_integrity" and exc.value.http_status == 503
    assert "demo" in exc.value.message and "rebuild" in exc.value.message
    with pytest.raises(DataPackError):
        run_analysis_sync("airspace", {"geometry": AREA}, datapack=pack)


def test_a_pack_without_hashes_is_unverified_and_every_analysis_says_so(tmp_path: Path, synthetic_pack: DataPack) -> None:
    root = tmp_path / "old"
    shutil.copytree(synthetic_pack.root, root)
    layers = {k: v.model_copy(update={"sha256": None}) for k, v in synthetic_pack.manifest.layers.items()}
    write_manifest(root, synthetic_pack.manifest.model_copy(update={"layers": layers, "content_sha256": None, "name": "old"}))
    pack = DataPack(root)
    assert pack.integrity == "unverified" and pack.content_sha256 is None
    assert len(pack.obstacles()) == 150  # still usable

    envelope = run_analysis_sync("airspace", {"geometry": AREA}, datapack=pack)
    assert envelope.datapack_sha256 is None
    notes = [i for i in envelope.result["issues"] if i["code"] == "datapack_unverified"]
    assert len(notes) == 1 and notes[0]["severity"] == "warning" and "'old'" in notes[0]["message"]


def test_a_verified_pack_is_recorded_in_the_envelope(synthetic_pack: DataPack) -> None:
    envelope = run_analysis_sync("airspace", {"geometry": AREA}, datapack=synthetic_pack)
    assert envelope.datapack_sha256 == synthetic_pack.manifest.content_sha256
    assert not any(i["code"] == "datapack_unverified" for i in envelope.result["issues"])


def test_hashes_are_cached_per_file_until_size_or_mtime_changes(pack_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(datapack_module, "_hashes", {})
    assert DataPack(pack_copy).integrity == "verified"
    zones = pack_copy / "zones.geojson"
    st = zones.stat()
    key = (str(zones.resolve()), st.st_size, st.st_mtime_ns)
    assert key in datapack_module._hashes
    # A second open reads the cache, not the file: a planted cache entry is believed...
    datapack_module._hashes[key] = "f" * 64
    assert DataPack(pack_copy).integrity == "mismatch"
    # ...until the file's mtime changes, when it is hashed again.
    os.utime(zones, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert DataPack(pack_copy).integrity == "verified"


def test_the_registry_reverifies_a_pack_whose_layer_changed(pack_copy: Path) -> None:
    registry = DataPackRegistry(pack_copy.parent)
    (first,) = registry.list()
    assert first.integrity == "verified"
    _tamper(pack_copy / "zones.geojson")
    (again,) = registry.list()
    assert again is not first and again.integrity == "mismatch"
    assert registry.select([77.48, 12.90, 77.50, 12.91]) is again  # still chosen, so analyses fail loudly


def test_seal_records_new_hashes_after_an_intended_edit(pack_copy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _tamper(pack_copy / "zones.geojson")
    assert DataPack(pack_copy).integrity == "mismatch"
    assert seed.main(["--seal", str(pack_copy)]) == 0
    pack = DataPack(pack_copy)
    assert pack.integrity == "verified" and pack.zones() == []
    assert pack.manifest.content_sha256 in capsys.readouterr().out
    with pytest.raises(SystemExit, match="does not exist"):
        seed.main(["--seal", str(pack_copy.parent / "missing")])


def test_overpass_returns_the_endpoint_that_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    def post_form(url: str, data: dict[str, str], **_: Any) -> bytes:
        if url == OVERPASS_ENDPOINTS[0]:
            raise DataPackError("fetch_failed", "HTTP 504")
        return b'{"elements": []}'

    monkeypatch.setattr(fetch, "post_form", post_form)
    payload, endpoint = fetch.overpass_query("[out:json];", rounds=1)
    assert payload == {"elements": []} and endpoint == OVERPASS_ENDPOINTS[1]


def test_region_pack_records_the_overpass_mirror_and_is_sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mirror = OVERPASS_ENDPOINTS[2]

    def offline(*_: Any, **__: Any) -> Any:
        raise DataPackError("fetch_failed", "offline")

    monkeypatch.setattr(fetch, "download", offline)
    monkeypatch.setattr(fetch, "read_remote_mosaic", offline)
    monkeypatch.setattr(fetch, "overpass_query", lambda query: ({"elements": [], "osm3s": {"timestamp_osm_base": "2026-10-01T00:00:00Z"}}, mirror))
    said: list[str] = []
    root, status = seed.build_region_pack(REGIONS["bengaluru"], tmp_path, lite=True, say=said.append)
    assert status["obstacles"] and not status["dem"]
    manifest = DataPackManifest.model_validate(_manifest(root))
    for key in ("obstacles", "pois", "water", "assemblies", "power"):
        assert manifest.layers[key].source_url == mirror, key
    assert any("served by the mirror" in line for line in said)
    assert manifest.content_sha256 and all(entry.sha256 for entry in manifest.layers.values())
    assert DataPack(root).integrity == "verified"


def test_refetch_fills_the_layers_overpass_missed_and_seals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(*_: Any, **__: Any) -> Any:
        raise DataPackError("fetch_failed", "offline")

    def busy_for_pois(query: str) -> tuple[dict[str, Any], str]:
        if '"amenity"="bus_station"' in query:
            raise DataPackError("fetch_failed", "All Overpass mirrors failed")
        return {"elements": []}, OVERPASS_ENDPOINTS[0]

    monkeypatch.setattr(fetch, "download", offline)
    monkeypatch.setattr(fetch, "read_remote_mosaic", offline)
    monkeypatch.setattr(fetch, "overpass_query", busy_for_pois)
    said: list[str] = []
    root, status = seed.build_region_pack(REGIONS["bengaluru"], tmp_path, lite=True, say=said.append)
    assert status["pois"] is False and "pois" not in _manifest(root)["layers"]

    station = {"type": "node", "id": 1, "lat": 12.98, "lon": 77.57, "tags": {"railway": "station", "name": "Majestic"}}
    monkeypatch.setattr(fetch, "overpass_query", lambda query: ({"elements": [station]}, OVERPASS_ENDPOINTS[1]))
    sealed, fetched = seed.refetch_osm_layers(root, say=said.append)
    assert fetched == {"pois": True}
    assert sealed.layers["pois"].model_dump()["feature_count"] == 1
    assert DataPack(root).integrity == "verified"
    assert seed.main(["--refetch", str(root)]) == 0  # nothing missing now
    with pytest.raises(SystemExit, match="not OpenStreetMap layers: dem"):
        seed.main(["--refetch", str(root), "--layers", "dem"])


def test_refetch_seals_the_pack_even_when_a_later_layer_breaks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(*_: Any, **__: Any) -> Any:
        raise DataPackError("fetch_failed", "offline")

    monkeypatch.setattr(fetch, "download", offline)
    monkeypatch.setattr(fetch, "read_remote_mosaic", offline)
    monkeypatch.setattr(fetch, "overpass_query", lambda query: ({"elements": []}, OVERPASS_ENDPOINTS[0]))
    root, _ = seed.build_region_pack(REGIONS["bengaluru"], tmp_path, lite=True, say=lambda _m: None)

    station = {"type": "node", "id": 1, "lat": 12.98, "lon": 77.57, "tags": {"railway": "station", "name": "Majestic"}}

    def stations_then_a_bug(query: str) -> tuple[dict[str, Any], str]:
        if '"amenity"="bus_station"' in query:
            return {"elements": [station]}, OVERPASS_ENDPOINTS[0]
        raise TypeError("a converter bug")

    monkeypatch.setattr(fetch, "overpass_query", stations_then_a_bug)
    with pytest.raises(TypeError):
        seed.refetch_osm_layers(root, ["pois", "water"], say=lambda _m: None)
    # pois.geojson was rewritten before water failed: the pack is still sealed and usable
    assert DataPack(root).integrity == "verified"
    assert DataPackManifest.model_validate(_manifest(root)).layers["pois"].model_dump()["feature_count"] == 1


def _ed269_square(identifier: str, lon: float, lat: float, restriction: str) -> dict[str, Any]:
    ring = [[lon, lat], [lon + 0.01, lat], [lon + 0.01, lat + 0.01], [lon, lat + 0.01], [lon, lat]]
    return {
        "identifier": identifier, "country": "IND", "name": f"Zone {identifier}", "type": "COMMON",
        "restriction": restriction, "applicability": [{"permanent": "YES"}],
        "geometry": [{"uomDimensions": "M", "lowerLimit": 0, "lowerVerticalReference": "AGL", "upperLimit": 150,
                      "upperVerticalReference": "AGL", "horizontalProjection": {"type": "Polygon", "coordinates": [ring]}}],
    }


def test_zones_from_a_national_file_go_into_the_pack(pack_copy: Path, tmp_path: Path) -> None:
    doc = {"title": "Test", "features": [
        _ed269_square("IN01", 77.49, 12.90, "PROHIBITED"),
        _ed269_square("FAR01", 10.0, 50.0, "REQ_AUTHORISATION"),  # outside the pack: dropped
    ]}
    src = tmp_path / "zones.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    assert seed.main(["--seal", str(pack_copy), "--zones", str(src), "--zones-credit", "Test authority, open data"]) == 0

    pack = DataPack(pack_copy)
    assert pack.integrity == "verified"
    (zone,) = pack.zones()
    assert zone.name == "Zone IN01" and zone.zone == "red" and zone.restriction == "PROHIBITED"
    assert zone.upper_limit_m == 150 and zone.source_format == "ed269"
    raw = _manifest(pack_copy)
    assert raw["layers"]["zones"]["feature_count"] == 1 and raw["layers"]["zones"]["zones_in_file"] == 2
    assert raw["layers"]["zones"]["file_name"] == "zones.json"
    assert "Zones: Test authority, open data" in raw["attribution"]

    # importing again replaces the layer and the credit instead of adding to them
    assert seed.main(["--seal", str(pack_copy), "--zones", str(src)]) == 0
    assert len(DataPack(pack_copy).zones()) == 1
    assert not [a for a in _manifest(pack_copy)["attribution"] if a.startswith("Zones: ")]


def test_zones_refuses_a_file_that_is_not_a_zone_file(pack_copy: Path, tmp_path: Path) -> None:
    src = tmp_path / "plain.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    with pytest.raises(SystemExit, match="not an ED-269 JSON or ED-318 GeoJSON"):
        seed.main(["--seal", str(pack_copy), "--zones", str(src)])
    with pytest.raises(SystemExit):
        seed.main(["--synthetic", "--zones", str(src)])


def test_a_bad_zone_file_fails_before_the_region_is_downloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def build(*_: Any, **__: Any) -> Any:
        raise AssertionError("downloaded the region before reading the zone file")

    monkeypatch.setattr(seed, "build_region_pack", build)
    with pytest.raises(SystemExit, match="Cannot read zones from"):
        seed.main(["--region", "bengaluru", "--zones", str(tmp_path / "zones.jsn")])


def test_zones_come_out_of_a_zip_packed_on_a_mac(pack_copy: Path, tmp_path: Path) -> None:
    import zipfile

    doc = {"title": "Test", "features": [_ed269_square("IN01", 77.49, 12.90, "PROHIBITED")]}
    src = tmp_path / "zones.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("zones.json", json.dumps(doc))
        z.writestr("__MACOSX/._zones.json", b"\x00\x05\x16\x07")
    assert seed.main(["--seal", str(pack_copy), "--zones", str(src)]) == 0
    assert [z.name for z in DataPack(pack_copy).zones()] == ["Zone IN01"]
