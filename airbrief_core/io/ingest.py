"""Safe staging of uploaded files: type detection, size limits and guarded zip extraction.

Zip guards (docs/SPEC.md §2 "Ingest"):

* entries with absolute paths, ``..`` segments, drive letters, NUL bytes or symlink modes are
  rejected with ``unsafe_archive`` before anything is written;
* the *declared* total uncompressed size is checked up front, and the *actual* number of
  bytes written is counted while streaming, so lying headers cannot bypass
  ``max_unzipped_bytes`` (``archive_too_large``);
* more than ``max_archive_files`` file entries → ``too_many_files``;
* ``__MACOSX/`` resource-fork entries and directory entries are ignored.

A zip may hold shapefiles, KML, GeoJSON, GPX or CSV files (e.g. a Skybrush per-drone export: one
CSV per drone, the drone id being the file stem); every supported file in it is read.
"""

from __future__ import annotations

import re
import shutil
import stat
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..errors import IngestError
from ..models.features import FileType

ZIP_MAGIC = b"PK\x03\x04"
EMPTY_ZIP_MAGIC = b"PK\x05\x06"
_CHUNK = 64 * 1024
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")

_EXTENSION_KINDS: dict[str, FileType] = {
    ".zip": "shapefile",
    ".kml": "kml",
    ".kmz": "kmz",
    ".geojson": "geojson",
    ".json": "geojson",
    ".csv": "csv",
    ".gpx": "gpx",
}
_LOOSE_SHAPEFILE_PARTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}


@dataclass(frozen=True)
class IngestLimits:
    """Upper bounds applied while staging and reading an upload."""

    max_upload_bytes: int = 50 * 1024 * 1024
    max_unzipped_bytes: int = 200 * 1024 * 1024
    max_archive_files: int = 200
    max_features: int = 50_000
    # Total vertices in the file: repairing and measuring huge geometries is CPU-heavy (DoS guard).
    max_coordinates: int = 5_000_000


@dataclass
class StagedSource:
    """An upload copied (and, for archives, extracted) into a private working directory.

    ``kind`` is the dataset file type. For zips it reflects the content: ``shapefile`` when a
    ``.shp`` is present, otherwise ``kml``, ``geojson``, ``gpx`` or ``csv`` (in that order).
    ``paths`` are the readable files in archive order (a single file for plain uploads).
    """

    kind: FileType
    root: Path
    paths: list[Path] = field(default_factory=list)
    filename: str = ""


def _is_zip(head: bytes) -> bool:
    return head.startswith(ZIP_MAGIC) or head.startswith(EMPTY_ZIP_MAGIC)


def detect_kind(filename: str, head: bytes) -> FileType:
    """Detect the file type from the extension, corrected by magic bytes.

    * A zip signature with a ``.kml`` or ``.kmz`` extension → ``kmz``; with ``.zip`` or no
      extension → ``shapefile`` (the zip's content refines this in :func:`stage_upload`).
    * ``.zip``/``.kmz`` without a zip signature → ``corrupt_file``.
    * Loose ``.shp``/``.dbf``/``.shx`` files and unknown extensions → ``unsupported_format``.
    """
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = PurePosixPath(name).suffix.lower()
    zipped = _is_zip(head)

    if suffix in _LOOSE_SHAPEFILE_PARTS:
        raise IngestError(
            "unsupported_format",
            "Upload a shapefile as a .zip containing the .shp, .shx, .dbf (and .prj) files.",
            details={"filename": name},
        )
    if suffix in (".kml", ".kmz") and zipped:
        return "kmz"
    if suffix in (".zip", ".kmz") and not zipped:
        raise IngestError(
            "corrupt_file",
            f"'{name}' is not a valid zip archive.",
            details={"filename": name},
        )
    if suffix == "" and zipped:
        return "shapefile"
    kind = _EXTENSION_KINDS.get(suffix)
    if kind is None:
        raise IngestError(
            "unsupported_format",
            f"Unsupported file type '{suffix or name}'. Accepted: .zip (shapefile, or KML/GeoJSON/GPX/CSV files), "
            ".kml, .kmz, .geojson, .json, .gpx, .csv.",
            details={"filename": name, "extension": suffix},
        )
    if zipped and kind in ("geojson", "csv", "gpx"):
        raise IngestError(
            "corrupt_file",
            f"'{name}' looks like a zip archive, not {kind}.",
            details={"filename": name},
        )
    return kind


def _safe_filename(filename: str) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1].replace("\x00", "")
    cleaned = _SAFE_NAME_RE.sub("_", base).strip(" .")
    return cleaned[:200] or "upload"


def _entry_parts(name: str) -> list[str]:
    """Validate an archive member name and return its path segments.

    Raises ``unsafe_archive`` for absolute paths, drive letters, ``..`` and NUL bytes.
    """
    if "\x00" in name:
        raise IngestError("unsafe_archive", "Archive entry name contains a NUL byte.", details={"entry": name})
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or _DRIVE_RE.match(normalised):
        raise IngestError(
            "unsafe_archive",
            f"Archive entry '{name}' has an absolute path.",
            details={"entry": name},
        )
    parts = [p for p in normalised.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise IngestError(
            "unsafe_archive",
            f"Archive entry '{name}' escapes the extraction directory.",
            details={"entry": name},
        )
    if any(_DRIVE_RE.match(p) for p in parts):
        raise IngestError("unsafe_archive", f"Archive entry '{name}' contains a drive letter.", details={"entry": name})
    return parts


def _is_ignored(parts: list[str]) -> bool:
    return bool(parts) and (parts[0] == "__MACOSX" or parts[-1] == ".DS_Store" or parts[-1].startswith("._"))


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def safe_extract_zip(zip_path: Path, dest: Path, limits: IngestLimits) -> list[Path]:
    """Extract ``zip_path`` into ``dest`` with zip-slip and zip-bomb guards.

    Every member is validated before anything is written. Returns the extracted file paths in
    archive order (``__MACOSX`` and directory entries excluded).
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
        raise IngestError("corrupt_file", "The archive is not a valid zip file.", details={"reason": str(exc)}) from exc

    with zf:
        members: list[tuple[zipfile.ZipInfo, list[str]]] = []
        declared_total = 0
        for info in zf.infolist():
            parts = _entry_parts(info.filename)
            if _is_symlink(info):
                raise IngestError(
                    "unsafe_archive",
                    f"Archive entry '{info.filename}' is a symbolic link.",
                    details={"entry": info.filename},
                )
            if info.is_dir() or not parts or _is_ignored(parts):
                continue
            if info.flag_bits & 0x1:
                raise IngestError(
                    "unsupported_format",
                    "Encrypted (password-protected) archives are not supported.",
                    details={"entry": info.filename},
                )
            members.append((info, parts))
            if len(members) > limits.max_archive_files:
                raise IngestError(
                    "too_many_files",
                    f"The archive contains more than {limits.max_archive_files} files.",
                    details={"max_archive_files": limits.max_archive_files},
                )
            declared_total += max(0, info.file_size)
            if declared_total > limits.max_unzipped_bytes:
                raise IngestError(
                    "archive_too_large",
                    f"The archive expands to more than {limits.max_unzipped_bytes // (1024 * 1024)} MB.",
                    details={"max_unzipped_bytes": limits.max_unzipped_bytes, "declared_bytes": declared_total},
                )

        dest.mkdir(parents=True, exist_ok=True)
        dest_resolved = dest.resolve()
        written_total = 0
        extracted: list[Path] = []
        seen: set[str] = set()
        for info, parts in members:
            target = dest.joinpath(*parts)
            resolved = target.resolve()
            if resolved != dest_resolved and dest_resolved not in resolved.parents:
                raise IngestError(
                    "unsafe_archive",
                    f"Archive entry '{info.filename}' escapes the extraction directory.",
                    details={"entry": info.filename},
                )
            key = resolved.as_posix().lower()
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(_CHUNK)
                        if not chunk:
                            break
                        written_total += len(chunk)
                        if written_total > limits.max_unzipped_bytes:
                            raise IngestError(
                                "archive_too_large",
                                f"The archive expands to more than {limits.max_unzipped_bytes // (1024 * 1024)} MB.",
                                details={"max_unzipped_bytes": limits.max_unzipped_bytes},
                            )
                        out.write(chunk)
            except IngestError:
                raise
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError, ValueError) as exc:
                raise IngestError(
                    "corrupt_file",
                    f"Archive entry '{info.filename}' could not be extracted.",
                    details={"entry": info.filename, "reason": str(exc)},
                ) from exc
            except NotImplementedError as exc:
                raise IngestError(
                    "unsupported_format",
                    f"Archive entry '{info.filename}' uses an unsupported compression method.",
                    details={"entry": info.filename},
                ) from exc
            if key not in seen:
                seen.add(key)
                extracted.append(target)
        return extracted


def _classify_zip(paths: list[Path]) -> FileType:
    suffixes = [p.suffix.lower() for p in paths]
    if ".shp" in suffixes:
        return "shapefile"
    if ".kml" in suffixes:
        return "kml"
    if ".geojson" in suffixes or ".json" in suffixes:
        return "geojson"
    if ".gpx" in suffixes:
        return "gpx"
    if ".csv" in suffixes:
        return "csv"
    if any(s in (".shx", ".dbf") for s in suffixes):
        raise IngestError(
            "missing_component",
            "The archive contains shapefile parts but no .shp file.",
        )
    raise IngestError(
        "no_supported_content",
        "The archive contains no supported file (.shp with .shx/.dbf, .kml, .geojson, .gpx or .csv).",
        details={"files": [p.name for p in paths][:50]},
    )


def stage_upload(src: Path, filename: str, workdir: Path, limits: IngestLimits) -> StagedSource:
    """Validate an uploaded file and stage it inside ``workdir``.

    Plain files are copied to ``workdir/source/``; zips and KMZs are extracted with
    :func:`safe_extract_zip` into ``workdir/extracted/``.

    Raises:
        IngestError: ``empty_file``, ``file_too_large``, ``unsupported_format``,
            ``corrupt_file`` or one of the archive guard codes.
    """
    try:
        size = src.stat().st_size
    except OSError as exc:  # the OS message names the staging path on the server
        raise IngestError("corrupt_file", "The uploaded file could not be read.") from exc
    if size == 0:
        raise IngestError("empty_file", "The uploaded file is empty.")
    if size > limits.max_upload_bytes:
        raise IngestError(
            "file_too_large",
            f"The file is larger than {limits.max_upload_bytes // (1024 * 1024)} MB.",
            details={"size_bytes": size, "max_upload_bytes": limits.max_upload_bytes},
        )
    with open(src, "rb") as fh:
        head = fh.read(512)
    kind = detect_kind(filename, head)
    safe_name = _safe_filename(filename)
    workdir.mkdir(parents=True, exist_ok=True)

    if _is_zip(head):
        root = workdir / "extracted"
        paths = safe_extract_zip(src, root, limits)
        if not paths:
            raise IngestError("no_supported_content", "The archive is empty.")
        if kind != "kmz":
            kind = _classify_zip(paths)
        return StagedSource(kind=kind, root=root, paths=paths, filename=safe_name)

    root = workdir / "source"
    root.mkdir(parents=True, exist_ok=True)
    target = root / safe_name
    shutil.copyfile(src, target)
    return StagedSource(kind=kind, root=root, paths=[target], filename=safe_name)
