"""Tier 0 pipeline: stage an upload, read every layer, repair, reproject and measure features.

``process_file`` never aborts a file because of one bad feature: an unexpected failure is
recorded on that feature as ``measurement.status = "error"`` and processing continues.
User-input problems for the file as a whole raise :class:`~airbrief_core.errors.IngestError`.
"""

from __future__ import annotations

import base64
import logging
import math
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from .errors import AirBriefError, IngestError
from .geo.crs import WGS84, crs_label, is_geographic
from .geo.measure import measure_geometry
from .geo.transform import geometry_to_geojson, to_wgs84
from .geo.validity import geometry_type_name, repair
from .io.ingest import IngestLimits, stage_upload
from .io.reader import RawFeature, RawLayer, read_staged
from .models.common import BBox, Issue, Severity
from .models.features import (
    DatasetResult,
    FeatureRecord,
    LayerInfo,
    Measurement,
    MeasurementStatus,
    MeasurementTotals,
)

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, float], None]


def json_safe(value: Any) -> Any:
    """Convert attribute values to JSON-safe types.

    Dates/times → ISO strings, bytes → UTF-8 text (base64 when not text), Decimal → int/float,
    non-finite floats → None, numpy scalars → Python scalars, containers recursively.
    """
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
        try:
            return raw.decode("utf-8").rstrip("\x00")
        except UnicodeDecodeError:
            return base64.b64encode(raw).decode("ascii")
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(v) for v in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return json_safe(item())
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _is_wgs84(crs: CRS) -> bool:
    try:
        return bool(crs.equals(WGS84, ignore_axis_order=True))
    except Exception:  # pyproj comparison failures mean "not equal"
        return False


def _with_source_z(wgs: BaseGeometry, source: BaseGeometry) -> BaseGeometry:
    """Re-attach source Z values to a 2D-transformed geometry when the vertex count matches."""
    if not source.has_z or wgs.has_z:
        return wgs
    src = shapely.get_coordinates(source, include_z=True)
    dst = shapely.get_coordinates(wgs)
    if len(src) != len(dst):
        return wgs
    coords = np.column_stack([dst[:, 0], dst[:, 1], np.nan_to_num(src[:, 2], nan=0.0)])
    return shapely.set_coordinates(shapely.force_3d(wgs), coords)


def _wgs84_geometry(geom: BaseGeometry, crs: CRS) -> BaseGeometry:
    if _is_wgs84(crs):
        return geom
    return _with_source_z(to_wgs84(geom, crs), geom)


def _error_measurement(repaired: bool) -> Measurement:
    # Generic on purpose: library text can hold internals. The cause is logged by the caller.
    return Measurement(
        status=MeasurementStatus.ERROR, reason="This feature could not be measured (internal error).", repaired=repaired
    )


NON_FINITE_REASON = "Some coordinates are not numbers (NaN or infinity), so this feature cannot be drawn or measured."


def _finite_geometry(
    geom: BaseGeometry, index: int, raw: RawFeature, issues: list[Issue]
) -> tuple[BaseGeometry | None, str | None]:
    """Guard against NaN/infinite coordinates (JSON parsers and float() accept them).

    A non-finite x/y makes the feature unsupported (its geometry is dropped); a non-finite
    altitude drops the altitudes. Returns the geometry to keep and an unsupported reason.
    """
    label = raw.name or raw.fid or "unnamed"
    xy = shapely.get_coordinates(geom)
    if xy.size and not bool(np.isfinite(xy).all()):
        issues.append(
            Issue(
                code="invalid_coordinates",
                message=f"Feature {index} ({label}) has coordinates that are not numbers and was skipped.",
                severity=Severity.WARNING,
                feature_index=index,
            )
        )
        return None, NON_FINITE_REASON
    if geom.has_z:
        xyz = shapely.get_coordinates(geom, include_z=True)
        if xyz.size and not bool(np.isfinite(xyz[:, 2]).all()):
            issues.append(
                Issue(
                    code="invalid_altitudes",
                    message=f"Feature {index} ({label}) has altitudes that are not numbers; its altitudes were ignored.",
                    severity=Severity.WARNING,
                    feature_index=index,
                )
            )
            return shapely.force_2d(geom), None
    return geom, None


def _build_record(index: int, layer: RawLayer, raw: RawFeature, issues: list[Issue]) -> FeatureRecord:
    """Never raises for a bad feature."""
    crs = layer.crs
    label = crs_label(crs)
    geom = raw.geometry
    if geom is not None and geom.is_empty:
        geom = None
    unsupported_reason = raw.unsupported_reason
    dropped_type: str | None = None
    if geom is not None:
        original = geom
        geom, non_finite = _finite_geometry(geom, index, raw, issues)
        if non_finite is not None:
            unsupported_reason = unsupported_reason or non_finite
            dropped_type = original.geom_type
    repaired = False
    geometry_json: dict[str, Any] | None = None
    wgs_json: dict[str, Any] | None = None
    bbox: BBox | None = None
    try:
        type_name = dropped_type or geometry_type_name(geom)
    except Exception:  # defensive: the type name must never break the record
        type_name = geom.geom_type if geom is not None else "None"

    try:
        if geom is not None and unsupported_reason is None:
            geom, repaired = repair(geom)
            type_name = geometry_type_name(geom)
            if repaired:
                issues.append(
                    Issue(
                        code="geometry_repaired",
                        message=f"Feature {index} ({raw.name or raw.fid or 'unnamed'}) had invalid geometry and was repaired.",
                        severity=Severity.INFO,
                        feature_index=index,
                    )
                )
        if geom is not None:
            decimals = 8 if crs is not None and is_geographic(crs) else None
            geometry_json = geometry_to_geojson(geom, decimals=decimals)
            if crs is not None:
                wgs = _wgs84_geometry(geom, crs)
                wgs_json = geometry_to_geojson(wgs, decimals=8)
                if not wgs.is_empty:
                    bounds = wgs.bounds
                    if all(math.isfinite(v) for v in bounds):
                        bbox = BBox.from_bounds(bounds)
        if unsupported_reason is not None:
            measurement = Measurement(status=MeasurementStatus.UNSUPPORTED, reason=unsupported_reason)
        else:
            measurement = measure_geometry(geom, crs, repaired=repaired)
    except Exception:  # one bad feature must not abort the file
        log.exception("Feature %s of layer %s could not be processed", index, layer.name)
        measurement = _error_measurement(repaired)
        issues.append(
            Issue(
                code="feature_error",
                message=f"Feature {index} ({raw.name or raw.fid or 'unnamed'}) could not be processed (internal error).",
                severity=Severity.WARNING,
                feature_index=index,
            )
        )

    return FeatureRecord(
        index=index,
        layer=layer.name,
        fid=raw.fid,
        name=raw.name,
        geometry_type=type_name,
        crs=label,
        geometry=geometry_json,
        geometry_wgs84=wgs_json,
        bbox_wgs84=bbox,
        properties=json_safe(raw.properties) if isinstance(raw.properties, dict) else {},
        altitude_mode=raw.altitude_mode,
        times=[float(t) for t in raw.times] if raw.times is not None else None,
        time_origin_utc=raw.time_origin_utc,
        measurement=measurement,
    )


def _dataset_crs(layers: list[RawLayer]) -> tuple[str | None, str | None]:
    labels: list[str] = []
    sources: list[str] = []
    for layer in layers:
        label = crs_label(layer.crs)
        if label is not None:
            labels.append(label)
            if layer.crs_source:
                sources.append(layer.crs_source)
    unique = set(labels)
    if not unique:
        crs = None
    elif len(unique) == 1:
        crs = labels[0]
    else:
        crs = "mixed"
    source = sources[0] if sources and len(set(sources)) == 1 else None
    return crs, source


def _totals(features: list[FeatureRecord]) -> MeasurementTotals:
    totals = MeasurementTotals(feature_count=len(features))
    for f in features:
        m = f.measurement
        status = MeasurementStatus(m.status)
        if status is MeasurementStatus.MEASURED:
            totals.measured += 1
        elif status is MeasurementStatus.NOT_APPLICABLE:
            totals.not_applicable += 1
        elif status is MeasurementStatus.UNSUPPORTED:
            totals.unsupported += 1
        elif status is MeasurementStatus.SKIPPED:
            totals.skipped += 1
        else:
            totals.error += 1
        totals.area_m2 += m.area_m2 or 0.0
        totals.perimeter_m += m.perimeter_m or 0.0
        totals.length_m += m.length_m or 0.0
        totals.length_3d_m += m.length_3d_m or 0.0
    return totals


def process_file(
    path: Path,
    filename: str,
    *,
    limits: IngestLimits = IngestLimits(),
    crs_override: str | None = None,
    csv_origin: tuple[float, float, float] | None = None,
    workdir: Path | None = None,
    progress: ProgressCallback | None = None,
) -> DatasetResult:
    """Process one uploaded file end to end.

    ``filename`` is the client's name for it and drives type detection. ``crs_override`` (e.g.
    ``"EPSG:32643"``) applies to shapefile/GeoJSON layers; ``csv_origin`` is the ``(lon, lat, alt)``
    origin of local x/y/z CSV tracks. Without ``workdir`` the upload is staged in a temp dir that
    is removed afterwards. Raises :class:`IngestError` for an unreadable or unsafe upload, or one
    over ``limits.max_features``.
    """

    def report(stage: str, fraction: float) -> None:
        if progress is not None:
            progress(stage, max(0.0, min(1.0, fraction)))

    with ExitStack() as stack:
        if workdir is None:
            workdir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="airbrief-")))
        report("staging", 0.02)
        staged = stage_upload(Path(path), filename, Path(workdir), limits)

        report("reading", 0.1)
        try:
            raw_layers = read_staged(staged, crs_override, csv_origin, limits.max_coordinates)
        except AirBriefError:
            raise
        except Exception as exc:  # any parser crash on user input is a corrupt file
            log.warning("Reading %s failed", filename, exc_info=True)
            raise IngestError(
                "corrupt_file",
                f"'{filename}' could not be read.",
                details={"reason": type(exc).__name__},
            ) from exc

        layers = [layer for layer in raw_layers if not layer.skipped]
        total = sum(len(layer.features) for layer in layers)
        if total > limits.max_features:
            raise IngestError(
                "too_many_features",
                f"The file has {total} features; the limit is {limits.max_features}.",
                details={"feature_count": total, "max_features": limits.max_features},
            )

        # The readers already stop at the limit while building geometries (charge_vertices);
        # this recount is the backstop for any reader that does not.
        coordinates = sum(
            int(shapely.get_num_coordinates(raw.geometry))
            for layer in layers
            for raw in layer.features
            if raw.geometry is not None
        )
        if coordinates > limits.max_coordinates:
            raise IngestError(
                "too_complex",
                f"The file has {coordinates:,} vertices; the limit is {limits.max_coordinates:,}. "
                "Simplify the geometry or split the file.",
                details={"coordinates": coordinates, "max_coordinates": limits.max_coordinates},
            )

        issues: list[Issue] = []
        for layer in raw_layers:
            issues.extend(layer.issues)
        if total == 0:
            issues.append(Issue(code="no_features", message="The file contains no features.", severity=Severity.WARNING))

        report("measuring", 0.2)
        features: list[FeatureRecord] = []
        layer_infos: list[LayerInfo] = []
        step = max(1, total // 50)
        index = 0
        for layer in layers:
            geometry_types: dict[str, int] = {}
            for raw in layer.features:
                record = _build_record(index, layer, raw, issues)
                features.append(record)
                geometry_types[record.geometry_type] = geometry_types.get(record.geometry_type, 0) + 1
                index += 1
                if index % step == 0:
                    report("measuring", 0.2 + 0.75 * index / max(total, 1))
            layer_infos.append(
                LayerInfo(
                    name=layer.name,
                    feature_count=len(layer.features),
                    crs=crs_label(layer.crs),
                    geometry_types=geometry_types,
                )
            )

        bbox: BBox | None = None
        for record in features:
            if record.bbox_wgs84 is not None:
                bbox = record.bbox_wgs84 if bbox is None else bbox.union(record.bbox_wgs84)

        dataset_crs, crs_source = _dataset_crs(layers)
        result = DatasetResult(
            filename=filename,
            file_type=staged.kind,
            crs=dataset_crs,
            crs_source=crs_source,  # type: ignore[arg-type]
            feature_count=len(features),
            layers=layer_infos,
            features=features,
            bbox_wgs84=bbox,
            totals=_totals(features),
            issues=issues,
        )
        report("done", 1.0)
        return result
