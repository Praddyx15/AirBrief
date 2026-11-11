"""Check a brief bundle: ``python -m airbrief_core.verify <bundle.zip> [--public-key KEY]``.

Every entry listed in ``provenance.json`` is re-hashed, entries it does not list are refused, and
with ``--public-key`` (the instance's published key: base64, or a file holding it or a PEM public
key) ``signature.json`` is checked against the exact bytes of ``provenance.json``. A bundle
without ``signature.json`` fails when a key is given. The zip is read in memory with the archive
guards of the upload code (:mod:`airbrief_core.io.ingest`); nothing is extracted. Exit status 0
when every check passed, 1 otherwise.

``python -m airbrief_core.verify --new-key <path>`` writes a new signing key for an instance
(``BRIEF_SIGNING_KEY_FILE``) and prints its key id and public key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .errors import IngestError
from .io.ingest import _entry_parts, _is_symlink
from .signing import ALGORITHM, SIGNATURE_FILE, SIGNED_FILE, check_signature, key_id, parse_public_key, write_new_key

__all__ = ["MAX_BUNDLE_FILES", "MAX_BUNDLE_BYTES", "VerifyReport", "main", "verify_bundle"]

MAX_BUNDLE_FILES = 1000
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
"""Uncompressed total accepted (the API writes at most 200 MB)."""
_MAX_JSON_BYTES = 16 * 1024 * 1024


@dataclass
class VerifyReport:
    files: int = 0
    """Entries whose size and SHA-256 match ``provenance.json``."""
    problems: list[str] = field(default_factory=list)
    signature: Literal["absent", "not_checked", "valid", "invalid"] | None = None
    """None when the check stopped before reaching the signature (an unreadable or unsafe zip)."""
    key_id: str | None = None
    """The key id ``signature.json`` names (not trusted: the signature check decides)."""

    @property
    def ok(self) -> bool:
        return not self.problems


class _Budget:
    def __init__(self) -> None:
        self.left = MAX_BUNDLE_BYTES

    def take(self, n: int) -> None:
        self.left -= n
        if self.left < 0:
            raise IngestError("archive_too_large", f"The bundle expands to more than {MAX_BUNDLE_BYTES // (1024 * 1024)} MB.")


def _members(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """File entries by name, checked like uploads before anything is read; a name given twice is
    refused (a reader would see only one of the two)."""
    out: dict[str, zipfile.ZipInfo] = {}
    declared = 0
    for info in zf.infolist():
        parts = _entry_parts(info.filename)
        if _is_symlink(info):
            raise IngestError("unsafe_archive", f"Archive entry '{info.filename}' is a symbolic link.")
        if info.is_dir() or not parts:
            continue
        if info.flag_bits & 0x1:
            raise IngestError("unsupported_format", f"Archive entry '{info.filename}' is encrypted.")
        name = "/".join(parts)
        if name in out:
            raise IngestError("unsafe_archive", f"Archive entry '{name}' appears more than once.")
        out[name] = info
        if len(out) > MAX_BUNDLE_FILES:
            raise IngestError("too_many_files", f"The bundle holds more than {MAX_BUNDLE_FILES} files.")
        declared += max(0, info.file_size)
        if declared > MAX_BUNDLE_BYTES:
            raise IngestError("archive_too_large", f"The bundle expands to more than {MAX_BUNDLE_BYTES // (1024 * 1024)} MB.")
    return out


def _digest(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: _Budget) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with zf.open(info) as f:
        while chunk := f.read(1 << 16):
            budget.take(len(chunk))
            size += len(chunk)
            h.update(chunk)
    return h.hexdigest(), size


def _read_json(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: _Budget) -> tuple[bytes, Any]:
    if info.file_size > _MAX_JSON_BYTES:
        raise IngestError("archive_too_large", f"{info.filename} is larger than {_MAX_JSON_BYTES // (1024 * 1024)} MB.")
    with zf.open(info) as f:
        data = f.read(_MAX_JSON_BYTES + 1)
    if len(data) > _MAX_JSON_BYTES:
        raise IngestError("archive_too_large", f"{info.filename} is larger than {_MAX_JSON_BYTES // (1024 * 1024)} MB.")
    budget.take(len(data))
    try:
        return data, json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IngestError("corrupt_file", f"{info.filename} is not valid JSON.") from exc


def _listed(provenance: Any) -> list[dict[str, Any]]:
    entries = provenance.get("bundle", {}).get("entries") if isinstance(provenance, dict) else None
    if not isinstance(entries, list):
        raise IngestError("corrupt_file", f"{SIGNED_FILE} has no bundle.entries list.")
    for e in entries:
        if not (isinstance(e, dict) and isinstance(e.get("name"), str) and isinstance(e.get("bytes"), int)
                and isinstance(e.get("sha256"), str)):
            raise IngestError("corrupt_file", f"{SIGNED_FILE} has a malformed entry: {str(e)[:120]}")
    return entries


def verify_bundle(path: Path | str, public_key: bytes | None = None) -> VerifyReport:
    """Check the bundle at ``path`` (see the module docstring); ``public_key`` is the raw 32-byte
    Ed25519 key the signature must verify with, or None to check the hashes only."""
    report = VerifyReport()
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
        report.problems.append(f"not a readable zip file: {exc}")
        return report
    with zf:
        try:
            _check(zf, public_key, report)
        except IngestError as exc:
            report.problems.append(exc.message)
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, OSError) as exc:
            report.problems.append(f"the zip could not be read: {exc}")
    return report


def _check(zf: zipfile.ZipFile, public_key: bytes | None, report: VerifyReport) -> None:
    members = _members(zf)
    budget = _Budget()
    if SIGNED_FILE not in members:
        report.problems.append(f"no {SIGNED_FILE} in the bundle")
        return
    provenance_bytes, provenance = _read_json(zf, members[SIGNED_FILE], budget)
    listed: set[str] = set()
    for entry in _listed(provenance):
        name = entry["name"]
        if name in (SIGNED_FILE, SIGNATURE_FILE) or name in listed:
            report.problems.append(f"{name}: listed in {SIGNED_FILE} where it cannot be")
            continue
        listed.add(name)
        info = members.get(name)
        if info is None:
            report.problems.append(f"{name}: listed in {SIGNED_FILE} but missing from the bundle")
            continue
        digest, size = _digest(zf, info, budget)
        if size != entry["bytes"] or digest != entry["sha256"].lower():
            report.problems.append(f"{name}: content differs from {SIGNED_FILE} (SHA-256 or size)")
        else:
            report.files += 1
    for name in members:
        if name not in listed and name not in (SIGNED_FILE, SIGNATURE_FILE):
            report.problems.append(f"{name}: in the bundle but not listed in {SIGNED_FILE}")

    if SIGNATURE_FILE not in members:
        report.signature = "absent"
        if public_key is not None:
            report.problems.append(f"not signed: no {SIGNATURE_FILE}, but a public key was given")
        return
    _, sig = _read_json(zf, members[SIGNATURE_FILE], budget)
    if not isinstance(sig, dict) or not all(isinstance(sig.get(k), str) for k in ("algorithm", "key_id", "signature")):
        report.signature = "invalid"
        report.problems.append(f"{SIGNATURE_FILE} needs the strings algorithm, key_id and signature")
        return
    report.key_id = sig["key_id"]
    if public_key is None:
        report.signature = "not_checked"
        return
    report.signature = "invalid"
    if sig["algorithm"] != ALGORITHM or sig.get("signed_file", SIGNED_FILE) != SIGNED_FILE:
        report.problems.append(f"{SIGNATURE_FILE}: expected an {ALGORITHM} signature of {SIGNED_FILE}")
    elif sig["key_id"] != key_id(public_key):
        report.problems.append(f"signed with key {sig['key_id']}, not with the given key {key_id(public_key)}")
    elif not check_signature(public_key, provenance_bytes, sig["signature"]):
        report.problems.append(f"the signature does not match {SIGNED_FILE}: it was changed after signing")
    else:
        report.signature = "valid"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m airbrief_core.verify",
        description="Check the hashes and signature of an AirBrief brief bundle, or create a signing key.",
    )
    parser.add_argument("bundle", nargs="?", type=Path, help="the bundle zip to check")
    parser.add_argument("--public-key", help="the instance's public key: base64, or a file with it or a PEM public key")
    parser.add_argument("--new-key", type=Path, metavar="PATH", help="write a new Ed25519 private key to PATH and print its public key")
    args = parser.parse_args(argv)
    if (args.bundle is None) == (args.new_key is None):
        parser.error("give a bundle to check, or --new-key PATH")

    if args.new_key is not None:
        try:
            signer = write_new_key(args.new_key)
        except FileExistsError:
            parser.error(f"{args.new_key} exists; a signing key is never overwritten")
        except RuntimeError as exc:
            parser.error(str(exc))
        print(f"wrote an Ed25519 private key to {args.new_key}: keep it secret, set BRIEF_SIGNING_KEY_FILE to it")
        print(f"key id      {signer.key_id}")
        print(f"public key  {signer.public_key_b64}")
        return 0

    public_key = None
    if args.public_key:
        try:
            public_key = parse_public_key(args.public_key)
        except (ValueError, RuntimeError) as exc:
            parser.error(f"--public-key: {exc}")
    try:
        report = verify_bundle(args.bundle, public_key)
    except RuntimeError as exc:
        parser.error(str(exc))
    print(f"bundle  {args.bundle}")
    for problem in report.problems:
        print(f"FAIL    {problem}")
    if report.signature is not None:
        print(f"files   {report.files} match {SIGNED_FILE}")
    if report.signature == "absent":
        print("signed  no: the SHA-256 values detect accidental corruption, not deliberate edits")
    elif report.signature == "not_checked":
        print(f"signed  by key {report.key_id}, NOT checked: give --public-key with the instance's published key")
    elif report.signature == "valid":
        print(f"signed  yes, {ALGORITHM} signature valid for key {report.key_id}")
    print("result  OK" if report.ok else f"result  FAILED ({len(report.problems)} problem{'' if len(report.problems) == 1 else 's'})")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
