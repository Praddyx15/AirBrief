"""Ed25519 signatures of brief bundles (optional: ``pip install "airbrief-core[signing]"``).

One key per instance. The bundle's ``signature.json`` holds a signature of the exact bytes of its
``provenance.json``, which lists the SHA-256 of every other entry; recipients check it against the
public key the operator publishes (``GET /api/meta/`` → ``signing``) with
``python -m airbrief_core.verify``. The key id is the first 16 hex characters of the SHA-256 of
the raw 32-byte public key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

__all__ = [
    "ALGORITHM",
    "SIGNATURE_FILE",
    "SIGNED_FILE",
    "BriefSigner",
    "check_signature",
    "key_id",
    "parse_public_key",
    "write_new_key",
]

ALGORITHM = "Ed25519"
SIGNATURE_FILE = "signature.json"
SIGNED_FILE = "provenance.json"


def _ed25519() -> Any:
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise RuntimeError(
            "brief signing needs the 'cryptography' package: pip install \"airbrief-core[signing]\""
        ) from exc
    return ed25519


def key_id(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()[:16]


class BriefSigner:
    """The instance's private key: signs ``provenance.json`` of every bundle."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        from cryptography.hazmat.primitives import serialization

        self._key = private_key
        self.public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        self.key_id = key_id(self.public_key)

    @classmethod
    def from_file(cls, path: Path) -> BriefSigner:
        """Load a PEM (PKCS#8, unencrypted) Ed25519 private key."""
        ed25519 = _ed25519()
        from cryptography.hazmat.primitives import serialization

        data = Path(path).read_bytes()
        try:
            key = serialization.load_pem_private_key(data, password=None)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{path} is not an unencrypted PEM private key") from exc
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise ValueError(f"{path} holds a {type(key).__name__}, not an Ed25519 key")
        return cls(key)

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key).decode("ascii")

    def sign(self, data: bytes) -> dict[str, str]:
        """The content of ``signature.json`` for ``data`` (the bytes of ``provenance.json``)."""
        return {
            "algorithm": ALGORITHM,
            "key_id": self.key_id,
            "signed_file": SIGNED_FILE,
            "signature": base64.b64encode(self._key.sign(data)).decode("ascii"),
        }


def write_new_key(path: Path) -> BriefSigner:
    """Create a new Ed25519 key at ``path`` (PEM, owner-only permissions where the OS has them).
    Refuses to overwrite an existing file."""
    ed25519 = _ed25519()
    from cryptography.hazmat.primitives import serialization

    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL: never replace a key bundles were signed with; O_BINARY: no CRLF on Windows
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return BriefSigner(key)


def parse_public_key(value: str) -> bytes:
    """A raw 32-byte Ed25519 public key from base64 text, or from a file holding that text or a
    PEM public key."""
    text = value.strip()
    if Path(text).is_file():
        text = Path(text).read_text(encoding="utf-8").strip()
    if text.startswith("-----BEGIN"):
        ed25519 = _ed25519()
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_public_key(text.encode("ascii"))
        if not isinstance(key, ed25519.Ed25519PublicKey):
            raise ValueError("the PEM public key is not an Ed25519 key")
        return key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("the public key is neither base64 nor a PEM file") from exc
    if len(raw) != 32:
        raise ValueError(f"an Ed25519 public key has 32 bytes, this one {len(raw)}")
    return raw


def check_signature(public_key: bytes, data: bytes, signature_b64: str) -> bool:
    """True when ``signature_b64`` is a valid Ed25519 signature of ``data`` by ``public_key``."""
    ed25519 = _ed25519()
    from cryptography.exceptions import InvalidSignature

    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (binascii.Error, ValueError):
        return False
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(public_key).verify(signature, data)
    except InvalidSignature:
        return False
    return True
