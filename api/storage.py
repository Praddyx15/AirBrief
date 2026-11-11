"""File storage for uploads: one directory per dataset under ``UPLOAD_DIR``.

Layout of ``UPLOAD_DIR/<dataset_id>/``:

* ``source<ext>`` — the uploaded bytes (streamed to disk, size-limited, SHA-256 hashed);
* ``options.json`` — processing options for queued jobs (CRS override, CSV origin);
* ``work/`` — temporary staging directory used while processing (removed afterwards).

The :class:`TTLSweeper` thread deletes datasets (rows and files) and stand-alone analyses older
than ``UPLOAD_TTL_HOURS``, then re-measures the space used.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from fastapi import UploadFile

from airbrief_core.errors import IngestError

from .errors import ApiError
from .models import utcnow

if TYPE_CHECKING:
    from .config import Settings
    from .db import Database

log = logging.getLogger("airbrief.api.storage")

CHUNK_BYTES = 1024 * 1024
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")
_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


@dataclass(frozen=True)
class StoredFile:
    """A file written to a dataset directory."""

    path: Path
    filename: str
    size_bytes: int
    sha256: str
    head: bytes


def safe_filename(filename: str | None) -> str:
    """The base name of a client filename with unsafe characters replaced (never empty)."""
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].replace("\x00", "")
    cleaned = _UNSAFE_CHARS.sub("_", base).strip(" .")
    return cleaned[:200] or "upload"


def file_too_large(limit_bytes: int, size: int | None = None) -> IngestError:
    """The ``file_too_large`` error (HTTP 413)."""
    limit_mb = limit_bytes / (1024 * 1024)
    details: dict[str, Any] = {"max_upload_bytes": limit_bytes, "max_upload_mb": round(limit_mb, 3)}
    if size is not None:
        details["size_bytes"] = size
    return IngestError(
        "file_too_large",
        f"The file is larger than the {limit_mb:g} MB upload limit.",
        details=details,
    )


_STORAGE_FULL_MESSAGES = {
    "quota": "This server has no storage left for uploads right now. Try again later.",
    "disk": "This server's disk is almost full, so it cannot take uploads right now. Try again later.",
}


def _tree_size(path: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        if "work" in dirnames:
            dirnames.remove("work")  # extraction scratch, not stored data
        for name in filenames:
            try:
                total += os.stat(os.path.join(dirpath, name)).st_size
            except FileNotFoundError:
                continue  # deleted while we walked
    return total


class Storage:
    """Per-dataset directories below the upload root.

    ``quota_bytes`` caps the total size of the stored files and ``min_free_bytes`` keeps that
    much of the disk free (0 turns either off). Writes that would break them fail with 507
    ``storage_full``. The total is measured once, kept up to date as files are written and
    deleted, and re-measured by the TTL sweeper, so no request walks the whole tree.
    """

    def __init__(self, upload_dir: Path, *, quota_bytes: int = 0, min_free_bytes: int = 0) -> None:
        self.root = Path(upload_dir)
        self.quota_bytes = quota_bytes
        self.min_free_bytes = min_free_bytes
        self._used: int | None = None
        self._lock = threading.Lock()

    def dataset_dir(self, dataset_id: str) -> Path:
        if not _ID_RE.match(dataset_id):
            raise ValueError(f"invalid dataset id {dataset_id!r}")
        return self.root / dataset_id

    def work_dir(self, dataset_id: str) -> Path:
        return self.dataset_dir(dataset_id) / "work"

    @staticmethod
    def _source_name(filename: str) -> str:
        suffix = PurePosixPath(safe_filename(filename)).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ""
        return f"source{suffix}"

    def check_space(self, incoming: int, *, reserve: bool = False) -> None:
        """Raise 507 ``storage_full`` if ``incoming`` more bytes break the quota or the free-space floor.

        With ``reserve`` the bytes are counted as used in the same step, so parallel uploads
        cannot both pass the check for the last free megabytes.
        """
        with self._lock:
            if self.quota_bytes:
                if self._used is None:
                    self._used = _tree_size(self.root)
                if self._used + incoming > self.quota_bytes:
                    raise self._full("quota")
            if self.min_free_bytes and shutil.disk_usage(self.root).free - incoming < self.min_free_bytes:
                raise self._full("disk")
            if reserve and self._used is not None:
                self._used += incoming

    def used_bytes(self) -> int | None:
        """Bytes stored under the root as last counted (None until a quota check measured them)."""
        return self._used

    def refresh_usage(self) -> None:
        """Re-measure the stored bytes (only tracked when there is a quota)."""
        if not self.quota_bytes:
            return
        size = _tree_size(self.root)
        with self._lock:
            self._used = size

    def _count(self, delta: int) -> None:
        with self._lock:
            if self._used is not None:
                self._used = max(0, self._used + delta)

    @staticmethod
    def _full(reason: str) -> ApiError:
        return ApiError("storage_full", _STORAGE_FULL_MESSAGES[reason], status=507, details={"reason": reason})

    async def save_upload(self, dataset_id: str, upload: UploadFile, max_bytes: int) -> StoredFile:
        """Stream ``upload`` to disk, enforcing ``max_bytes`` (413 ``file_too_large``)."""
        filename = safe_filename(upload.filename)
        directory = self.dataset_dir(dataset_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self._source_name(filename)
        digest = hashlib.sha256()
        size = 0
        head = b""
        try:
            with path.open("wb") as out:
                while True:
                    chunk = await upload.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise file_too_large(max_bytes)
                    self.check_space(len(chunk), reserve=True)
                    if len(head) < 16:
                        head = (head + chunk)[:16]
                    digest.update(chunk)
                    out.write(chunk)
        except BaseException:
            self.delete(dataset_id)
            raise
        finally:
            await upload.close()
        return StoredFile(path=path, filename=filename, size_bytes=size, sha256=digest.hexdigest(), head=head)

    def save_bytes(self, dataset_id: str, filename: str, data: bytes) -> StoredFile:
        """Write ``data`` (e.g. a built-in sample) as the dataset's source file."""
        self.check_space(len(data), reserve=True)
        name = safe_filename(filename)
        directory = self.dataset_dir(dataset_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self._source_name(name)
        path.write_bytes(data)
        return StoredFile(
            path=path,
            filename=name,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            head=data[:16],
        )

    def write_options(self, dataset_id: str, options: dict[str, Any]) -> None:
        """Persist processing options next to the source file (read back by queued jobs)."""
        path = self.dataset_dir(dataset_id) / "options.json"
        data = json.dumps(options).encode("utf-8")
        path.write_bytes(data)
        self._count(len(data))

    def read_options(self, dataset_id: str) -> dict[str, Any]:
        path = self.dataset_dir(dataset_id) / "options.json"
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def clear_work(self, dataset_id: str) -> None:
        shutil.rmtree(self.work_dir(dataset_id), ignore_errors=True)

    def delete(self, dataset_id: str) -> None:
        """Remove the dataset directory and everything in it."""
        try:
            directory = self.dataset_dir(dataset_id)
        except ValueError:
            return
        before = _tree_size(directory) if self._used is not None else 0
        shutil.rmtree(directory, ignore_errors=True)
        if before:
            self._count(_tree_size(directory) - before)


class TTLSweeper(threading.Thread):
    """Background thread deleting expired datasets and analyses (``UPLOAD_TTL_HOURS > 0``)."""

    def __init__(self, db: Database, storage: Storage, settings: Settings) -> None:
        super().__init__(name="airbrief-ttl-sweeper", daemon=True)
        self.db = db
        self.storage = storage
        self.ttl = timedelta(hours=settings.upload_ttl_hours)
        self.interval_s = max(30.0, min(900.0, self.ttl.total_seconds() / 4.0))
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sweep()
            except Exception:  # keep sweeping on the next tick whatever happened
                log.exception("TTL sweep failed")
            self._stop_event.wait(self.interval_s)

    def sweep(self) -> int:
        """Delete everything older than the TTL. Returns the number of deleted datasets."""
        from sqlalchemy import delete, select

        from .models import Analysis, Dataset
        from .services.datasets import delete_dataset

        cutoff = utcnow() - self.ttl
        with self.db.session() as session:
            expired = list(session.scalars(select(Dataset.id).where(Dataset.created_at < cutoff)))
        for dataset_id in expired:
            with self.db.session() as session:
                delete_dataset(session, self.storage, dataset_id, missing_ok=True)
        with self.db.session() as session:
            session.execute(
                delete(Analysis).where(Analysis.dataset_id.is_(None), Analysis.created_at < cutoff)
            )
            session.commit()
        if expired:
            log.info("TTL sweeper deleted %d dataset(s)", len(expired))
        self.storage.refresh_usage()
        return len(expired)
