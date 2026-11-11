"""Evidence integrity through the API (MISS-01, MISS-02).

* With ``BRIEF_SIGNING_KEY_FILE`` every bundle carries ``signature.json``; ``GET /api/meta/``
  publishes the public key and its id; the bundle verifies with that key and fails once edited.
* Without a key the meta says so (``signing: null``), bundles are unsigned and the brief says its
  hashes detect corruption, not deliberate edits.
* Data packs: ``GET /api/meta/`` shows each pack's integrity; analyses record the pack's content
  hash (envelope, brief provenance); a tampered pack is shown as ``mismatch`` and analyses on it
  fail with ``datapack_integrity``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from airbrief_core.data import DataPack
from airbrief_core.models.brief import UNSIGNED_NOTE
from airbrief_core.signing import BriefSigner, write_new_key
from airbrief_core.verify import verify_bundle
from api.config import Settings

AREA = {"type": "Polygon", "coordinates": [[[77.48, 12.90], [77.50, 12.90], [77.50, 12.91], [77.48, 12.91], [77.48, 12.90]]]}


def _airspace(client: TestClient, helpers: SimpleNamespace) -> dict[str, Any]:
    info = helpers.run_analysis(client, {"kind": "airspace", "params": {"geometry": AREA}})
    assert info["status"] == "COMPLETED", info["error"]
    return info  # type: ignore[no-any-return]


def _bundle(client: TestClient, ids: list[str], path: Path) -> Path:
    response = client.post("/api/briefs/bundle/", json={"analysis_ids": ids})
    assert response.status_code == 200, response.text
    path.write_bytes(response.content)
    return path


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "keys" / "brief-signing.pem"
    write_new_key(path)
    return path


def test_signed_bundle_verifies_with_the_published_key(
    make_client: Any, helpers: SimpleNamespace, key_file: Path, tmp_path: Path
) -> None:
    client = make_client(brief_signing_key_file=key_file)
    signer = BriefSigner.from_file(key_file)
    signing = client.get("/api/meta/").json()["signing"]
    assert signing == {"algorithm": "Ed25519", "key_id": signer.key_id, "public_key": signer.public_key_b64}
    public_key = base64.b64decode(signing["public_key"])

    analysis = _airspace(client, helpers)
    brief = client.post("/api/briefs/", json={"analysis_ids": [analysis["id"]]}).json()
    assert brief["provenance"]["signing_key_id"] == signer.key_id
    assert brief["provenance"]["signature_note"].startswith(f"Bundles from this instance are signed (key {signer.key_id})")

    path = _bundle(client, [analysis["id"]], tmp_path / "bundle.zip")
    with zipfile.ZipFile(path) as zf:
        signature = json.loads(zf.read("signature.json"))
        assert signature["algorithm"] == "Ed25519" and signature["key_id"] == signer.key_id
        assert "signature.json" not in {e["name"] for e in json.loads(zf.read("provenance.json"))["bundle"]["entries"]}
        assert f"Bundles from this instance are signed (key {signer.key_id})" in zf.read("brief.html").decode("utf-8")
    report = verify_bundle(path, public_key)
    assert report.ok and report.signature == "valid" and report.files > 5

    # Edit brief.json and recompute its hash in provenance.json, as someone hiding a zone would.
    edited = tmp_path / "edited.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(edited, "w") as dst:
        provenance = json.loads(src.read("provenance.json"))
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "brief.json":
                assert b"Zones touched" in data
                data = data.replace(b"Zones touched", b"Zones avoided")
                entry = next(e for e in provenance["bundle"]["entries"] if e["name"] == "brief.json")
                entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            elif info.filename == "provenance.json":
                continue
            dst.writestr(info, data)
        dst.writestr("provenance.json", json.dumps(provenance, indent=1).encode("utf-8"))
    tampered = verify_bundle(edited, public_key)
    assert not tampered.ok and tampered.signature == "invalid"


def test_without_a_key_bundles_are_unsigned_and_say_so(api_client: TestClient, helpers: SimpleNamespace, tmp_path: Path) -> None:
    assert api_client.get("/api/meta/").json()["signing"] is None
    analysis = _airspace(api_client, helpers)
    brief = api_client.post("/api/briefs/", json={"analysis_ids": [analysis["id"]]}).json()
    assert brief["provenance"]["signing_key_id"] is None and brief["provenance"]["signature_note"] == UNSIGNED_NOTE
    path = _bundle(api_client, [analysis["id"]], tmp_path / "bundle.zip")
    assert "signature.json" not in zipfile.ZipFile(path).namelist()
    report = verify_bundle(path)
    assert report.ok and report.signature == "absent"


def test_an_unusable_signing_key_stops_the_settings(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pem"
    bad.write_text("not a key", encoding="utf-8")
    for path in (bad, tmp_path / "missing.pem"):
        with pytest.raises(ValueError, match="BRIEF_SIGNING_KEY_FILE"):
            Settings(_env_file=None, brief_signing_key_file=path)  # type: ignore[call-arg]
    assert Settings(_env_file=None, brief_signing_key_file="").brief_signer is None  # type: ignore[call-arg]


def test_analyses_and_briefs_record_the_pack_content(
    api_client: TestClient, helpers: SimpleNamespace, synthetic_pack: DataPack, tmp_path: Path
) -> None:
    content = synthetic_pack.manifest.content_sha256
    (pack,) = api_client.get("/api/meta/").json()["datapacks"]
    assert pack["integrity"] == "verified" and pack["content_sha256"] == content
    analysis = _airspace(api_client, helpers)
    brief = api_client.post("/api/briefs/", json={"analysis_ids": [analysis["id"]]}).json()
    assert brief["provenance"]["analyses"][0]["datapack_sha256"] == content
    (provenance_pack,) = brief["provenance"]["datapacks"]
    assert provenance_pack["content_sha256"] == content
    assert all(layer["sha256"] for layer in provenance_pack["layers"])
    assert "datapack_changed" not in {w["code"] for w in brief["warnings"]}
    response = api_client.post("/api/briefs/bundle/", json={"analysis_ids": [analysis["id"]]})
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert json.loads(zf.read("analyses/01_airspace.json"))["datapack_sha256"] == content


def test_a_tampered_pack_is_shown_and_refused(make_client: Any, helpers: SimpleNamespace, synthetic_pack: DataPack, tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    shutil.copytree(synthetic_pack.root, packs / "demo")
    zones = packs / "demo" / "zones.geojson"
    zones.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    client = make_client(datapack_dir=packs)
    (pack,) = client.get("/api/meta/").json()["datapacks"]
    assert pack["integrity"] == "mismatch"
    info = helpers.run_analysis(client, {"kind": "airspace", "params": {"geometry": AREA}})
    assert info["status"] == "FAILED" and info["error"]["code"] == "datapack_integrity"
    assert "failed its integrity check" in info["error"]["message"]
    context = client.get("/api/context/", params={"bbox": "77.48,12.90,77.50,12.91"})
    assert context.status_code == 503 and context.json()["error"]["code"] == "datapack_integrity"
    places = client.get("/api/places/", params={"q": "VOBL"})
    assert places.status_code == 200
    assert all(p["kind"] == "coordinates" for p in places.json())
