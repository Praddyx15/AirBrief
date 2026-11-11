"""Datasets: create from uploads or samples, process, query and export.

Uploads up to ``INLINE_PROCESS_MAX_MB`` (and every sample) are processed inside the request and
answered with 201; larger uploads are queued for the job runner and answered with 202 PENDING.

When processing in the request, security and format rejections (``unsupported_format``,
``file_too_large``, ``unsafe_archive``, ``archive_too_large``, ``too_many_files``) are returned
as HTTP errors and nothing is kept. Every other ingest problem (``corrupt_file``,
``missing_component``, ``empty_file``…) yields a ``FAILED`` dataset whose ``error`` explains why.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import UploadFile
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session

from airbrief_core.errors import AirBriefError, IngestError
from airbrief_core.geo.crs import resolve_crs
from airbrief_core.io.geozones import Ed318Limits
from airbrief_core.io.ingest import detect_kind
from airbrief_core.io.kml_write import ALTITUDE_MODES, KmlDocument, has_z
from airbrief_core.models.common import BBox
from airbrief_core.models.features import DatasetResult, FeatureRecord, MeasurementTotals
from airbrief_core.processing import process_file
from airbrief_core.samples import SAMPLES, build_sample

from ..config import Settings
from ..db import Database
from ..deps import AppContext
from ..errors import ApiError, not_found
from ..models import Analysis, Dataset, Feature, as_utc, utcnow
from ..ownership import new_owner_token
from ..schemas import ErrorInfo, FileInfo, FilesPage, MeasurementItem, MeasurementsPage, SampleInfo
from ..storage import Storage, StoredFile

log = logging.getLogger("airbrief.api.datasets")

REJECTED_CODES = frozenset(
    {"unsupported_format", "file_too_large", "unsafe_archive", "archive_too_large", "too_many_files"}
)
"""Ingest errors answered as HTTP errors (nothing stored) when processing in the request."""

FEATURE_BATCH = 500

MEASUREMENT_COLUMNS: tuple[str, ...] = (
    "status",
    "reason",
    "area_m2",
    "perimeter_m",
    "length_m",
    "length_3d_m",
    "geodesic_area_m2",
    "geodesic_perimeter_m",
    "geodesic_length_m",
    "deviation_pct",
    "grid_area_m2",
    "grid_length_m",
    "method",
    "utm_epsg",
    "utm_zone",
    "repaired",
    "has_z",
)

_EXPORT_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "geojson": "application/geo+json",
    "kml": "application/vnd.google-earth.kml+xml",
}


def parse_origin(text: str | None) -> tuple[float, float, float] | None:
    """Parse the ``origin`` form field ``"lon,lat[,alt]"`` (422 ``invalid_params`` when bad)."""
    if text is None or not text.strip():
        return None
    parts = [p.strip() for p in text.split(",")]
    try:
        values = [float(p) for p in parts]
    except ValueError as exc:
        raise ApiError(
            "invalid_params", "origin must be 'lon,lat' or 'lon,lat,alt' in degrees and metres.",
            details={"origin": text[:100]},
        ) from exc
    if len(values) not in (2, 3) or not all(math.isfinite(v) for v in values):
        raise ApiError(
            "invalid_params", "origin must be 'lon,lat' or 'lon,lat,alt' in degrees and metres.",
            details={"origin": text[:100]},
        )
    lon, lat = values[0], values[1]
    alt = values[2] if len(values) == 3 else 0.0
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise ApiError("invalid_params", "origin lon/lat are out of range.", details={"origin": text[:100]})
    return lon, lat, alt


def validate_crs_override(text: str | None) -> str | None:
    """Check a CRS override early so a typo is a 422 ``invalid_crs`` instead of a failed dataset."""
    if text is None or not text.strip():
        return None
    value = text.strip()
    if len(value) > 4000:
        raise IngestError("invalid_crs", "The CRS override is too long.", details={"length": len(value)})
    resolve_crs(value)
    return value


async def create_from_upload(
    ctx: AppContext,
    upload: UploadFile,
    crs_override: str | None,
    origin: str | None,
) -> tuple[FileInfo, int]:
    """Store an upload, then process it inline or queue it. Returns ``(FileInfo, http_status)``."""
    from starlette.concurrency import run_in_threadpool

    crs = validate_crs_override(crs_override)
    csv_origin = parse_origin(origin)
    dataset_id = str(uuid.uuid4())
    stored = await ctx.storage.save_upload(dataset_id, upload, ctx.settings.max_upload_bytes)
    return await run_in_threadpool(
        register_dataset, ctx, dataset_id, stored, crs, csv_origin, None, False
    )


def create_from_sample(ctx: AppContext, name: str) -> FileInfo:
    """Create and process (inline) a dataset from the built-in sample ``name``."""
    spec = SAMPLES.get(name)
    if spec is None:
        raise not_found("Sample", name)
    if spec.is_report:
        raise ApiError(
            "sample_not_dataset",
            f"'{name}' is a FlightMD report, not a dataset. Download it and open it in the Flight audit module.",
            details={"name": name, "download": f"/api/samples/{name}/download/"},
        )
    data = build_sample(name)
    dataset_id = str(uuid.uuid4())
    stored = ctx.storage.save_bytes(dataset_id, PurePosixPath(spec.filename).name, data)
    info, _status = register_dataset(ctx, dataset_id, stored, None, None, name, True)
    return info


def register_dataset(
    ctx: AppContext,
    dataset_id: str,
    stored: StoredFile,
    crs_override: str | None,
    csv_origin: tuple[float, float, float] | None,
    sample: str | None,
    force_inline: bool,
) -> tuple[FileInfo, int]:
    """Create the dataset row for a stored file and process it (inline) or queue it."""
    file_type: str | None
    try:
        file_type = detect_kind(stored.filename, stored.head)
    except AirBriefError as exc:
        if exc.code in REJECTED_CODES:
            ctx.storage.delete(dataset_id)
            raise
        # e.g. a ".zip" that is not a zip: kept as a FAILED dataset by the processing step.
        file_type = None
    ctx.storage.write_options(
        dataset_id,
        {"crs_override": crs_override, "csv_origin": list(csv_origin) if csv_origin else None},
    )
    inline = force_inline or stored.size_bytes <= ctx.settings.inline_process_max_bytes
    owner_token, owner_token_hash = new_owner_token()
    with ctx.db.session() as session:
        session.add(
            Dataset(
                id=dataset_id,
                filename=stored.filename,
                file_type=file_type,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                status="PROCESSING" if inline else "PENDING",
                claimed_by="inline" if inline else None,
                # or another process's recovery takes it for a job whose process died
                heartbeat_at=utcnow() if inline else None,
                stored_path=str(stored.path),
                sample=sample,
                owner_token_hash=owner_token_hash,
                created_at=utcnow(),
                feature_count=0,
                layer_count=0,
            )
        )
        session.commit()

    if inline:
        process_dataset(ctx.db, ctx.storage, ctx.settings, dataset_id, reject_unsafe=True)
        status_code = 201
    else:
        ctx.notify_jobs()
        status_code = 202

    with ctx.db.session() as session:
        row = session.get(Dataset, dataset_id)
        if row is None:
            raise not_found("File", dataset_id)
        # The owner token is shown exactly once, in this creation response.
        return to_file_info(row).model_copy(update={"owner_token": owner_token}), status_code


def _mark_failed(db: Database, dataset_id: str, code: str, message: str, elapsed_ms: int) -> None:
    with db.session() as session:
        session.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id)
            .values(
                status="FAILED",
                error_code=code,
                error_message=message[:4000],
                processed_at=utcnow(),
                processing_ms=elapsed_ms,
            )
        )
        session.commit()


def _feature_row(dataset_id: str, record: FeatureRecord) -> dict[str, Any]:
    bbox = record.bbox_wgs84
    return {
        "dataset_id": dataset_id,
        "feature_index": record.index,
        "layer": record.layer,
        "fid": record.fid,
        "name": record.name,
        "geometry_type": record.geometry_type,
        "crs": record.crs,
        "geometry": record.geometry,
        "geometry_wgs84": record.geometry_wgs84,
        "properties": record.properties or {},
        "measurement": record.measurement.model_dump(mode="json"),
        "min_lon": bbox.min_lon if bbox else None,
        "min_lat": bbox.min_lat if bbox else None,
        "max_lon": bbox.max_lon if bbox else None,
        "max_lat": bbox.max_lat if bbox else None,
        "altitude_mode": record.altitude_mode,
        "times": record.times,
        "time_origin_utc": record.time_origin_utc,
    }


def _persist_result(db: Database, dataset_id: str, result: DatasetResult, elapsed_ms: int) -> None:
    with db.session() as session:
        session.execute(delete(Feature).where(Feature.dataset_id == dataset_id))
        batch: list[dict[str, Any]] = []
        for record in result.features:
            batch.append(_feature_row(dataset_id, record))
            if len(batch) >= FEATURE_BATCH:
                session.execute(insert(Feature), batch)
                batch = []
        if batch:
            session.execute(insert(Feature), batch)
        session.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id)
            .values(
                status="COMPLETED",
                error_code=None,
                error_message=None,
                file_type=result.file_type,
                crs=result.crs,
                crs_source=result.crs_source,
                feature_count=result.feature_count,
                layer_count=len(result.layers),
                bbox=result.bbox_wgs84.as_list() if result.bbox_wgs84 is not None else None,
                layers=[layer.model_dump(mode="json") for layer in result.layers],
                totals=result.totals.model_dump(mode="json"),
                issues=[issue.model_dump(mode="json") for issue in result.issues],
                processed_at=utcnow(),
                processing_ms=elapsed_ms,
            )
        )
        session.commit()


def process_dataset(
    db: Database,
    storage: Storage,
    settings: Settings,
    dataset_id: str,
    *,
    reject_unsafe: bool = False,
) -> None:
    """Process a claimed dataset (status ``PROCESSING``) and persist features in batches.

    With ``reject_unsafe`` (request-time processing), errors in :data:`REJECTED_CODES` delete the
    dataset and are re-raised; otherwise every failure is recorded on the dataset row.
    """
    with db.session() as session:
        row = session.get(Dataset, dataset_id)
        if row is None or not row.stored_path:
            return
        path = Path(row.stored_path)
        filename = row.filename

    options = storage.read_options(dataset_id)
    crs_override = options.get("crs_override") or None
    raw_origin = options.get("csv_origin")
    csv_origin: tuple[float, float, float] | None = None
    if isinstance(raw_origin, list) and len(raw_origin) == 3:
        csv_origin = (float(raw_origin[0]), float(raw_origin[1]), float(raw_origin[2]))

    started = time.perf_counter()
    workdir = storage.work_dir(dataset_id)
    storage.clear_work(dataset_id)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        result = process_file(
            path,
            filename,
            limits=settings.ingest_limits,
            crs_override=crs_override,
            csv_origin=csv_origin,
            workdir=workdir,
        )
    except AirBriefError as exc:
        storage.clear_work(dataset_id)
        if reject_unsafe and exc.code in REJECTED_CODES:
            with db.session() as session:
                delete_dataset(session, storage, dataset_id, missing_ok=True)
            raise
        _mark_failed(db, dataset_id, exc.code, exc.message, _elapsed_ms(started))
        return
    except Exception:  # parser crashes must never take the API down
        storage.clear_work(dataset_id)
        log.exception("Processing dataset %s failed", dataset_id)
        _mark_failed(
            db, dataset_id, "internal_error", f"Processing failed unexpectedly. The server log has the details (dataset {dataset_id}).",
            _elapsed_ms(started),
        )
        return
    storage.clear_work(dataset_id)

    try:
        _persist_result(db, dataset_id, result, _elapsed_ms(started))
    except Exception:  # database errors are recorded on the row when possible
        log.exception("Storing features of dataset %s failed", dataset_id)
        try:
            _mark_failed(
                db, dataset_id, "internal_error", f"Storing the results failed. The server log has the details (dataset {dataset_id}).",
                _elapsed_ms(started),
            )
        except Exception:  # the row may be gone (deleted while processing)
            log.exception("Could not mark dataset %s as failed", dataset_id)


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def to_file_info(row: Dataset) -> FileInfo:
    error = None
    if row.error_code:
        error = ErrorInfo(code=row.error_code, message=row.error_message or row.error_code)
    created = as_utc(row.created_at)
    assert created is not None
    return FileInfo.model_validate(
        {
            "id": row.id,
            "filename": row.filename,
            "file_type": row.file_type,
            "size_bytes": row.size_bytes,
            "status": row.status,
            "feature_count": row.feature_count or 0,
            "layer_count": row.layer_count or 0,
            "crs": row.crs,
            "crs_source": row.crs_source,
            "bbox": row.bbox,
            "layers": row.layers or [],
            "totals": row.totals,
            "issues": row.issues or [],
            "error": error,
            "created_at": created,
            "processed_at": as_utc(row.processed_at),
            "processing_ms": row.processing_ms,
            "sample": row.sample,
        }
    )


def get_dataset(session: Session, dataset_id: str) -> Dataset:
    """The dataset row, or 404 ``not_found``."""
    row = session.get(Dataset, dataset_id)
    if row is None:
        raise not_found("File", dataset_id)
    return row


def list_files(session: Session, limit: int, offset: int, *, ids: list[str] | None = None) -> FilesPage:
    """Datasets newest first: all of them, or only ``ids`` when given."""
    query = select(Dataset)
    count = select(func.count()).select_from(Dataset)
    if ids is not None:
        query = query.where(Dataset.id.in_(ids))
        count = count.where(Dataset.id.in_(ids))
    total = session.scalar(count) or 0
    rows = session.scalars(query.order_by(Dataset.created_at.desc(), Dataset.id).limit(limit).offset(offset))
    return FilesPage(items=[to_file_info(r) for r in rows], total=int(total))


def measurements_page(
    session: Session,
    dataset_id: str,
    *,
    limit: int,
    offset: int,
    geometry_type: str | None,
    status: str | None,
) -> MeasurementsPage:
    row = get_dataset(session, dataset_id)
    query = select(
        Feature.feature_index.label("feature_index"),
        Feature.layer,
        Feature.name,
        Feature.geometry_type,
        Feature.crs,
        Feature.measurement,
    ).where(Feature.dataset_id == dataset_id)
    count_query = select(func.count()).select_from(Feature).where(Feature.dataset_id == dataset_id)
    if geometry_type:
        query = query.where(Feature.geometry_type == geometry_type)
        count_query = count_query.where(Feature.geometry_type == geometry_type)
    if status:
        # Filtered in SQL (json_extract on SQLite, ->> on PostgreSQL), not by loading every row.
        status_is = Feature.measurement["status"].as_string() == status
        query = query.where(status_is)
        count_query = count_query.where(status_is)
    total = int(session.scalar(count_query) or 0)
    page = list(session.execute(query.order_by(Feature.feature_index).limit(limit).offset(offset)))

    items = [
        MeasurementItem.model_validate(
            {
                "feature_index": r.feature_index,
                "layer": r.layer,
                "name": r.name,
                "geometry_type": r.geometry_type,
                "crs": r.crs,
                "measurement": r.measurement or {"status": "error", "reason": "Measurement missing"},
            }
        )
        for r in page
    ]
    totals = MeasurementTotals.model_validate(row.totals) if row.totals else MeasurementTotals()
    return MeasurementsPage(
        file_id=dataset_id, items=items, total=total, limit=limit, offset=offset, totals=totals
    )


def _iter_features(session: Session, dataset_id: str) -> Iterator[Feature]:
    query = (
        select(Feature)
        .where(Feature.dataset_id == dataset_id)
        .order_by(Feature.feature_index)
        .execution_options(yield_per=1000)
    )
    yield from session.scalars(query)


def feature_to_geojson(feature: Feature) -> dict[str, Any]:
    """A stored feature as a GeoJSON Feature in WGS84 with the ``_``-prefixed system properties."""
    measurement = feature.measurement or {}
    properties: dict[str, Any] = dict(feature.properties or {})
    properties.update(
        {
            "_index": feature.feature_index,
            "_layer": feature.layer,
            "_name": feature.name,
            "_type": feature.geometry_type,
            "_status": measurement.get("status"),
            "_area_m2": measurement.get("area_m2"),
            "_perimeter_m": measurement.get("perimeter_m"),
            "_length_m": measurement.get("length_m"),
        }
    )
    times = feature.times
    if times and len(times) >= 2:
        # Timed tracks (KML gx:Track, GPX trk, CSV): what the flight audit and shows panels list.
        properties["_time_points"] = len(times)
        properties["_duration_s"] = round(float(times[-1]) - float(times[0]), 3)
    return {
        "type": "Feature",
        "id": feature.feature_index,
        "geometry": feature.geometry_wgs84,
        "properties": properties,
    }


def features_geojson(session: Session, dataset_id: str) -> dict[str, Any]:
    """The whole dataset as a GeoJSON FeatureCollection (WGS84)."""
    row = get_dataset(session, dataset_id)
    collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "name": row.filename,
        "features": [feature_to_geojson(f) for f in _iter_features(session, dataset_id)],
    }
    if row.bbox and len(row.bbox) == 4:
        collection["bbox"] = list(row.bbox)
    return collection


def feature_record(session: Session, dataset_id: str, index: int) -> FeatureRecord:
    """One feature with its full measurement, or 404."""
    get_dataset(session, dataset_id)
    feature = session.scalar(
        select(Feature).where(Feature.dataset_id == dataset_id, Feature.feature_index == index)
    )
    if feature is None:
        raise not_found("Feature", f"{dataset_id}/{index}")
    bbox = None
    if None not in (feature.min_lon, feature.min_lat, feature.max_lon, feature.max_lat):
        bbox = BBox(
            min_lon=float(feature.min_lon),  # type: ignore[arg-type]
            min_lat=float(feature.min_lat),  # type: ignore[arg-type]
            max_lon=float(feature.max_lon),  # type: ignore[arg-type]
            max_lat=float(feature.max_lat),  # type: ignore[arg-type]
        )
    return FeatureRecord.model_validate(
        {
            "index": feature.feature_index,
            "layer": feature.layer,
            "fid": feature.fid,
            "name": feature.name,
            "geometry_type": feature.geometry_type,
            "crs": feature.crs,
            "geometry": feature.geometry,
            "geometry_wgs84": feature.geometry_wgs84,
            "bbox_wgs84": bbox,
            "properties": feature.properties or {},
            "altitude_mode": feature.altitude_mode,
            "times": feature.times,
            "time_origin_utc": feature.time_origin_utc,
            "measurement": feature.measurement or {"status": "error", "reason": "Measurement missing"},
        }
    )


def load_features(
    session: Session, dataset_id: str, indices: Iterable[int] | None = None
) -> list[Feature]:
    """Features of a dataset (all, or the given indices) ordered by index."""
    query = select(Feature).where(Feature.dataset_id == dataset_id)
    if indices is not None:
        wanted = sorted(set(indices))
        if not wanted:
            return []
        query = query.where(Feature.feature_index.in_(wanted))
    return list(session.scalars(query.order_by(Feature.feature_index)))


def delete_dataset(session: Session, storage: Storage, dataset_id: str, *, missing_ok: bool = False) -> None:
    """Delete a dataset, its features, its analyses and its files."""
    row = session.get(Dataset, dataset_id)
    if row is None:
        if missing_ok:
            storage.delete(dataset_id)
            return
        raise not_found("File", dataset_id)
    session.execute(delete(Feature).where(Feature.dataset_id == dataset_id))
    session.execute(delete(Analysis).where(Analysis.dataset_id == dataset_id))
    session.delete(row)
    session.commit()
    storage.delete(dataset_id)


def list_samples() -> list[SampleInfo]:
    return [
        SampleInfo(
            name=spec.name,
            title=spec.title,
            description=spec.description,
            filename=spec.filename,
            module=spec.module,
        )
        for spec in SAMPLES.values()
    ]


def sample_download(name: str) -> tuple[bytes, str, str]:
    """``(bytes, media_type, filename)`` of a sample file."""
    spec = SAMPLES.get(name)
    if spec is None:
        raise not_found("Sample", name)
    filename = PurePosixPath(spec.filename).name
    return build_sample(name), media_type_for(filename), filename


def media_type_for(filename: str) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    return {
        ".kml": "application/vnd.google-earth.kml+xml",
        ".kmz": "application/vnd.google-earth.kmz",
        ".zip": "application/zip",
        ".geojson": "application/geo+json",
        ".json": "application/json",
        ".csv": "text/csv; charset=utf-8",
        ".gpx": "application/gpx+xml",
    }.get(suffix, "application/octet-stream")


def export_basename(filename: str) -> str:
    """A safe ASCII base name (no extension) for export downloads."""
    stem = PurePosixPath(filename).stem or "dataset"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "dataset"
    return cleaned[:100]


def _csv_safe(value: Any) -> Any:
    """Neutralise spreadsheet formulas in text cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_dataset(
    session: Session, dataset_id: str, fmt: str, ed318_limits: Ed318Limits | None = None
) -> tuple[bytes, str, str]:
    """Export as ``csv``, ``geojson``, ``kml`` or ``ed318`` (limits where ``ed318_limits`` says,
    ``geometry`` by default). Returns ``(content, media_type, filename)``."""
    row = get_dataset(session, dataset_id)
    base = export_basename(row.filename)
    if ed318_limits is not None and fmt != "ed318":
        raise ApiError("invalid_params", "ed318_limits applies to the ED-318 export only.",
                       details={"format": fmt, "ed318_limits": ed318_limits})
    if fmt == "geojson":
        content = json.dumps(features_geojson(session, dataset_id), ensure_ascii=False).encode("utf-8")
    elif fmt == "csv":
        content = _export_csv(session, dataset_id)
    elif fmt == "kml":
        content = _export_kml(session, row)
    elif fmt == "ed318":
        from .zones import dataset_ed318  # zones imports this module

        doc = dataset_ed318(session, dataset_id, ed318_limits or "geometry")
        return json.dumps(doc, ensure_ascii=False).encode("utf-8"), "application/geo+json", f"{base}.ed318.geojson"
    else:
        raise ApiError("invalid_params", "format must be csv, geojson, kml or ed318.", details={"format": fmt})
    return content, _EXPORT_MEDIA_TYPES[fmt], f"{base}.{fmt}"


def _export_csv(session: Session, dataset_id: str) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    header = [
        "index", "layer", "fid", "name", "geometry_type", "crs",
        *MEASUREMENT_COLUMNS,
        "min_lon", "min_lat", "max_lon", "max_lat", "properties",
    ]
    writer.writerow(header)
    for f in _iter_features(session, dataset_id):
        m = f.measurement or {}
        writer.writerow(
            [
                f.feature_index,
                _csv_safe(f.layer),
                _csv_safe(f.fid),
                _csv_safe(f.name),
                f.geometry_type,
                _csv_safe(f.crs),  # a custom .prj's own name: uploader-controlled text
                *[_csv_safe(m.get(col)) for col in MEASUREMENT_COLUMNS],
                f.min_lon,
                f.min_lat,
                f.max_lon,
                f.max_lat,
                _csv_safe(json.dumps(f.properties or {}, ensure_ascii=False)) if f.properties else "",
            ]
        )
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _export_kml(session: Session, row: Dataset) -> bytes:
    doc = KmlDocument(
        row.filename,
        "Exported by AirBrief. Geometry in WGS84; measurements in ExtendedData (SI units).",
    )
    folders: dict[str, Any] = {}
    for f in _iter_features(session, row.id):
        folder = folders.get(f.layer)
        if folder is None:
            folder = doc.folder(f.layer or "Layer")
            folders[f.layer] = folder
        measurement = f.measurement or {}
        values: dict[str, Any] = {
            "feature_index": f.feature_index,
            "layer": f.layer,
            "geometry_type": f.geometry_type,
            "crs": f.crs,
        }
        for col in MEASUREMENT_COLUMNS:
            if measurement.get(col) is not None:
                values[col] = measurement[col]
        for key, value in (f.properties or {}).items():
            if key not in values and value is not None:
                values[str(key)] = value
        mode: str | None = None
        if f.geometry_wgs84 and has_z(f.geometry_wgs84):
            mode = f.altitude_mode if f.altitude_mode in ALTITUDE_MODES else "absolute"
        doc.placemark(
            folder,
            f.name or f.fid or f"Feature {f.feature_index}",
            values.items(),
            f.geometry_wgs84,
            altitude_mode=mode,
        )
    return doc.tobytes()
