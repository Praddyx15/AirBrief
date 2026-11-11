"""Owner tokens: who may delete or cancel a dataset or analysis.

AirBrief has no accounts. Ids are random UUIDs, so whoever knows an id can *open* the item,
for example from a shared link. Destructive actions need more than the id: every dataset and
analysis gets a random **owner token** when it is created.
- The token is returned once, in the creation response.
- Only its SHA-256 hash is stored.
- The browser keeps the token and sends it as ``X-Owner-Token`` on DELETE and cancel.
- It also sends a dataset's token when it runs an analysis on that dataset, which marks the
  analysis as the owner's (``analyses.by_dataset_owner``).

Enforcement is controlled by ``REQUIRE_OWNER_TOKEN`` (default: on when ``APP_ENV=production``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .errors import ApiError

OWNER_TOKEN_HEADER = "X-Owner-Token"
_TOKEN_BYTES = 32


def new_owner_token() -> tuple[str, str]:
    """A fresh ``(token, sha256_hex)`` pair."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    return token, hash_owner_token(token)


def hash_owner_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def owner_token_matches(stored_hash: str | None, provided: str | None) -> bool:
    """Constant-time check of ``provided`` against ``stored_hash``; False when either is missing."""
    if not provided or stored_hash is None:
        return False
    return hmac.compare_digest(stored_hash, hash_owner_token(provided))


def check_owner(stored_hash: str | None, provided: str | None, *, required: bool, what: str) -> None:
    """Raise 403 unless ``provided`` matches ``stored_hash`` (only when ``required``)."""
    if not required:
        return
    if not provided:
        raise ApiError(
            "owner_token_required",
            f"Deleting or cancelling this {what} needs its owner token ({OWNER_TOKEN_HEADER} header). "
            "Only the browser that created it has the token.",
            status=403,
        )
    if not owner_token_matches(stored_hash, provided):
        raise ApiError("owner_token_invalid", f"The owner token does not match this {what}.", status=403)
