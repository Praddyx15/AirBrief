"""Signed brief bundles (MISS-01): ``airbrief_core.signing`` and ``python -m airbrief_core.verify``.

Bundles are built here by hand (the API builds real ones in tests/api/test_evidence_integrity.py):
a signed bundle verifies with the right key and fails with a wrong key, an edited file, an edited
provenance or a stripped signature; an unsigned bundle passes the hash check with a clear "not
signed" line; unsafe archives are refused without extracting anything.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import warnings
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from airbrief_core.signing import BriefSigner, key_id, parse_public_key, write_new_key
from airbrief_core.verify import main, verify_bundle

FILES = {
    "brief.html": b"<!doctype html><p>Zones touched: 1 (red)</p>",
    "brief.json": b'{"title": "Operation evidence brief"}',
    "analyses/01_airspace.json": b'{"kind": "airspace"}',
}


def _provenance(files: dict[str, bytes]) -> bytes:
    entries = [{"name": n, "bytes": len(d), "sha256": hashlib.sha256(d).hexdigest()} for n, d in files.items()]
    return json.dumps({"brief_sha256": "0" * 64, "bundle": {"entries": entries}}, indent=1).encode("utf-8")


def _write(path: Path, files: dict[str, bytes], provenance: bytes, signature: dict[str, str] | None = None,
           extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in {**files, **(extra or {})}.items():
            zf.writestr(name, data)
        zf.writestr("provenance.json", provenance)
        if signature is not None:
            zf.writestr("signature.json", json.dumps(signature))
    return path


@pytest.fixture(scope="module")
def signer(tmp_path_factory: pytest.TempPathFactory) -> BriefSigner:
    return write_new_key(tmp_path_factory.mktemp("keys") / "instance.pem")


def _signed(path: Path, signer: BriefSigner, files: dict[str, bytes] = FILES) -> Path:
    provenance = _provenance(files)
    return _write(path, files, provenance, signer.sign(provenance))


def test_signed_bundle_verifies_with_the_instance_key(tmp_path: Path, signer: BriefSigner) -> None:
    report = verify_bundle(_signed(tmp_path / "b.zip", signer), signer.public_key)
    assert report.ok and report.files == 3 and report.signature == "valid" and report.key_id == signer.key_id
    assert signer.key_id == hashlib.sha256(signer.public_key).hexdigest()[:16] == key_id(signer.public_key)


def test_edited_file_fails(tmp_path: Path, signer: BriefSigner) -> None:
    provenance = _provenance(FILES)
    edited = {**FILES, "brief.html": b"<!doctype html><p>Zones touched: 0</p>"}
    report = verify_bundle(_write(tmp_path / "b.zip", edited, provenance, signer.sign(provenance)), signer.public_key)
    assert not report.ok and report.files == 2
    assert any(p.startswith("brief.html: content differs") for p in report.problems)


def test_edited_file_with_recomputed_hashes_fails_the_signature(tmp_path: Path, signer: BriefSigner) -> None:
    # The attack the signature exists for: change a file and fix its hash in provenance.json.
    original = _provenance(FILES)
    edited = {**FILES, "brief.html": b"<!doctype html><p>Zones touched: 0</p>"}
    path = _write(tmp_path / "b.zip", edited, _provenance(edited), signer.sign(original))
    report = verify_bundle(path, signer.public_key)
    assert report.files == 3 and report.signature == "invalid"
    assert report.problems == ["the signature does not match provenance.json: it was changed after signing"]
    # Without the key only the hashes can be checked, and they agree: hence "NOT checked".
    unchecked = verify_bundle(path)
    assert unchecked.ok and unchecked.signature == "not_checked"


def test_wrong_key_fails(tmp_path: Path, signer: BriefSigner) -> None:
    other = write_new_key(tmp_path / "other.pem")
    report = verify_bundle(_signed(tmp_path / "b.zip", signer), other.public_key)
    assert not report.ok and report.signature == "invalid"
    assert report.problems == [f"signed with key {signer.key_id}, not with the given key {other.key_id}"]
    # A signature.json that names the expected key but was made with another one still fails.
    provenance = _provenance(FILES)
    forged = {**other.sign(provenance), "key_id": signer.key_id}
    report = verify_bundle(_write(tmp_path / "f.zip", FILES, provenance, forged), signer.public_key)
    assert report.signature == "invalid" and "does not match" in report.problems[0]


def test_unsigned_bundle_checks_hashes_only(tmp_path: Path, signer: BriefSigner, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path / "b.zip", FILES, _provenance(FILES))
    report = verify_bundle(path)
    assert report.ok and report.files == 3 and report.signature == "absent"
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "signed  no: the SHA-256 values detect accidental corruption, not deliberate edits" in out
    assert "result  OK" in out
    # Expecting a signature (a key is given) and finding none fails: it may have been stripped.
    stripped = verify_bundle(path, signer.public_key)
    assert not stripped.ok and stripped.problems == ["not signed: no signature.json, but a public key was given"]


def test_unlisted_and_missing_entries_fail(tmp_path: Path, signer: BriefSigner) -> None:
    provenance = _provenance(FILES)
    extra = _write(tmp_path / "extra.zip", FILES, provenance, signer.sign(provenance), extra={"tables/99_x.csv": b"a,b\n"})
    assert verify_bundle(extra, signer.public_key).problems == ["tables/99_x.csv: in the bundle but not listed in provenance.json"]
    fewer = {k: v for k, v in FILES.items() if k != "brief.json"}
    missing = _write(tmp_path / "missing.zip", fewer, provenance, signer.sign(provenance))
    assert verify_bundle(missing, signer.public_key).problems == ["brief.json: listed in provenance.json but missing from the bundle"]


@pytest.mark.parametrize("name", ["../brief.html", "/etc/brief.html", "C:/brief.html"])
def test_unsafe_entry_names_are_refused(tmp_path: Path, name: str) -> None:
    path = _write(tmp_path / "b.zip", FILES, _provenance(FILES), extra={name: b"x"})
    report = verify_bundle(path)
    assert not report.ok and report.files == 0
    assert any("Archive entry" in p for p in report.problems)


def test_duplicate_entries_are_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / "b.zip", FILES, _provenance(FILES))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns about the duplicate name
        with zipfile.ZipFile(path, "a") as zf:
            zf.writestr("brief.html", b"<p>second copy</p>")
    report = verify_bundle(path)
    assert report.problems == ["Archive entry 'brief.html' appears more than once."]


def test_not_a_zip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "b.zip"
    path.write_bytes(b"not a zip")
    report = verify_bundle(path)
    assert not report.ok and report.problems[0].startswith("not a readable zip file") and report.signature is None
    assert main([str(path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL    not a readable zip file" in out and "signed" not in out and "result  FAILED (1 problem)" in out


def test_cli_with_public_key_as_base64_or_pem_file(tmp_path: Path, signer: BriefSigner, capsys: pytest.CaptureFixture[str]) -> None:
    path = _signed(tmp_path / "b.zip", signer)
    assert main([str(path), "--public-key", signer.public_key_b64]) == 0
    assert f"signed  yes, Ed25519 signature valid for key {signer.key_id}" in capsys.readouterr().out

    pem = tmp_path / "public.pem"
    pem.write_bytes(Ed25519PublicKey.from_public_bytes(signer.public_key).public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo))
    assert parse_public_key(str(pem)) == signer.public_key
    assert main([str(path), "--public-key", str(pem)]) == 0
    b64_file = tmp_path / "public.txt"
    b64_file.write_text(signer.public_key_b64 + "\n", encoding="utf-8")
    assert main([str(path), "--public-key", str(b64_file)]) == 0

    other = write_new_key(tmp_path / "other.pem")
    assert main([str(path), "--public-key", other.public_key_b64]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "result  FAILED (1 problem)" in out

    with pytest.raises(SystemExit) as exc:
        main([str(path), "--public-key", base64.b64encode(b"short").decode()])
    assert exc.value.code == 2


def test_new_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "keys" / "instance.pem"
    assert main(["--new-key", str(path)]) == 0
    out = capsys.readouterr().out
    loaded = BriefSigner.from_file(path)
    assert f"key id      {loaded.key_id}" in out and f"public key  {loaded.public_key_b64}" in out
    assert path.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----\n")
    if sys.platform != "win32":
        assert (os.stat(path).st_mode & 0o777) == 0o600
    with pytest.raises(SystemExit):
        main(["--new-key", str(path)])  # never overwrites a key
    assert BriefSigner.from_file(path).key_id == loaded.key_id
    with pytest.raises(SystemExit):
        main([])


def test_from_file_refuses_other_keys(tmp_path: Path) -> None:
    path = tmp_path / "ec.pem"
    path.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with pytest.raises(ValueError, match="not an Ed25519 key"):
        BriefSigner.from_file(path)
    (tmp_path / "junk.pem").write_text("not a key", encoding="utf-8")
    with pytest.raises(ValueError, match="not an unencrypted PEM private key"):
        BriefSigner.from_file(tmp_path / "junk.pem")
