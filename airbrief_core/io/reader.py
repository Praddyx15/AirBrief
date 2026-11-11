"""Format-neutral raw layers and :func:`read_staged`, the dispatcher for a staged upload.

Geometries stay in the layer's *source* CRS. A ``.shp`` that can't be read, or a GPX or CSV file
that can't be read next to other files, becomes a ``skipped`` placeholder layer with the issue;
the upload fails only if nothing readable is left (docs/SPEC.md §2 "Ingest").
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import shapely
from pyproj import CRS

from ..errors import AirBriefError, IngestError
from ..models.common import Issue, Severity

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

    from .ingest import StagedSource


@dataclass
class RawFeature:
    """One feature as read from a source file, before measurement.

    ``geometry`` is a Shapely geometry in the layer's source CRS (Z kept when present), or
    ``None`` for null/empty shapes and unsupported geometry kinds. ``unsupported_reason`` is
    set when the feature exists but its geometry kind cannot be represented (KML Model,
    shapefile MultiPatch, invalid coordinates). ``times`` are seconds aligned one-to-one with
    the geometry's vertices (tracks only); ``time_origin_utc`` is the ISO 8601 instant of
    ``t = 0`` when the source carried absolute timestamps.
    """

    fid: str | None
    name: str | None
    geometry: BaseGeometry | None
    properties: dict[str, Any] = field(default_factory=dict)
    altitude_mode: str | None = None
    times: list[float] | None = None
    time_origin_utc: str | None = None
    unsupported_reason: str | None = None


@dataclass
class RawLayer:
    """A named group of features sharing one CRS.

    ``skipped`` marks a placeholder layer that could not be read at all (e.g. a ``.shp``
    without its ``.shx``); it carries only the issues explaining why and is not listed as a
    dataset layer.
    """

    name: str
    crs: CRS | None
    crs_source: str | None
    features: list[RawFeature] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    skipped: bool = False


_SHAPEFILE_SUFFIX = ".shp"
_KML_SUFFIXES = (".kml",)
_GEOJSON_SUFFIXES = (".geojson", ".json")
_CSV_SUFFIXES = (".csv",)
_GPX_SUFFIXES = (".gpx",)


def _resolve_override(crs_override: str | None) -> CRS | None:
    if crs_override is None or not crs_override.strip():
        return None
    from ..geo.crs import resolve_crs

    try:
        crs = resolve_crs(crs_override.strip())
    except AirBriefError:
        raise
    except Exception as exc:  # pyproj raises CRSError (and occasionally others) on bad text
        raise IngestError(
            "invalid_crs",
            f"The CRS override {crs_override!r} is not a recognised coordinate reference system.",
            details={"crs_override": crs_override},
        ) from exc
    if crs is None:
        raise IngestError(
            "invalid_crs",
            f"The CRS override {crs_override!r} is not a recognised coordinate reference system.",
            details={"crs_override": crs_override},
        )
    return crs


def _suffix(path: Path) -> str:
    return path.suffix.lower()


def _apply_override(layer: RawLayer, override: CRS | None) -> RawLayer:
    """Apply a CRS override to a GeoJSON layer (shapefiles handle it themselves)."""
    if override is None:
        return layer
    from ..geo.crs import crs_label

    layer.crs = override
    layer.crs_source = "override"
    layer.issues.append(
        Issue(
            code="crs_override",
            message=f"Layer '{layer.name}': CRS override {crs_label(override)} applied.",
            severity=Severity.INFO,
        )
    )
    return layer


def _ignored_override_issue(layer: RawLayer, why: str) -> None:
    layer.issues.append(
        Issue(
            code="crs_override_ignored",
            message=f"Layer '{layer.name}': CRS override ignored ({why}).",
            severity=Severity.INFO,
        )
    )


def _read_shapefiles(shp_paths: list[Path], override: CRS | None) -> tuple[list[RawLayer], list[IngestError]]:
    from .shapefile import read_shapefile

    layers: list[RawLayer] = []
    failures: list[IngestError] = []
    for shp in shp_paths:
        try:
            layers.append(read_shapefile(shp, override))
        except IngestError as exc:
            if exc.code == "too_complex":  # a limit on the whole upload, not a broken layer
                raise
            failures.append(exc)
            layers.append(
                RawLayer(
                    name=shp.stem,
                    crs=None,
                    crs_source=None,
                    issues=[
                        Issue(
                            code=exc.code,
                            message=f"Layer '{shp.stem}' skipped: {exc.message}",
                            severity=Severity.WARNING,
                        )
                    ],
                    skipped=True,
                )
            )
    return layers, failures


def _pick_kmz_document(paths: list[Path], root: Path) -> Path | None:
    """KMZ rule: ``doc.kml`` (shallowest) is preferred, otherwise the first ``.kml`` in archive order."""
    kmls = [p for p in paths if _suffix(p) in _KML_SUFFIXES]
    if not kmls:
        return None
    docs = [p for p in kmls if p.name.lower() == "doc.kml"]
    if docs:

        def depth(p: Path) -> int:
            try:
                return len(p.relative_to(root).parts)
            except ValueError:
                return len(p.parts)

        return min(docs, key=depth)
    return kmls[0]


@dataclass
class _VertexBudget:
    limit: int
    used: int = 0


_VERTEX_BUDGET: ContextVar[_VertexBudget | None] = ContextVar("airbrief_vertex_budget", default=None)


def charge_vertices(geometry: BaseGeometry | None) -> None:
    """Count a just-built geometry against the vertex limit of the current :func:`read_staged`.

    Format readers call this for every feature. Outside ``read_staged`` it does nothing.

    Raises:
        IngestError: ``too_complex`` as soon as the running total exceeds the limit.
    """
    budget = _VERTEX_BUDGET.get()
    if budget is None or geometry is None:
        return
    budget.used += int(shapely.get_num_coordinates(geometry))
    if budget.used > budget.limit:
        raise IngestError(
            "too_complex",
            f"The file has more than {budget.limit:,} vertices, the limit. Simplify the geometry or split the file.",
            details={"max_coordinates": budget.limit},
        )


def read_staged(
    staged: StagedSource,
    crs_override: str | None,
    csv_origin: tuple[float, float, float] | None,
    max_coordinates: int | None = None,
) -> list[RawLayer]:
    """Read every supported layer of a staged upload.

    Returns the layers in a stable order (archive order for zips). Placeholder layers with
    ``skipped=True`` carry issues for components that could not be read. ``max_coordinates``
    caps the total vertex count while reading (``None`` = no cap).

    Raises:
        IngestError: ``invalid_crs`` for an unusable override, ``missing_component`` when a
            shapefile archive has no readable layer, ``no_supported_content`` when nothing
            readable was found, ``too_complex`` past ``max_coordinates``, or the format
            reader's own ``corrupt_file`` errors.
    """
    token = _VERTEX_BUDGET.set(_VertexBudget(max_coordinates) if max_coordinates is not None else None)
    try:
        return _read_staged(staged, crs_override, csv_origin)
    finally:
        _VERTEX_BUDGET.reset(token)


def _read_staged(
    staged: StagedSource,
    crs_override: str | None,
    csv_origin: tuple[float, float, float] | None,
) -> list[RawLayer]:
    # Format readers import RawFeature/RawLayer from this module, so they are imported lazily.
    from .geojson import read_geojson
    from .kml import parse_kml

    override = _resolve_override(crs_override)
    kind = staged.kind
    paths = list(staged.paths)
    layers: list[RawLayer] = []

    if kind == "kmz":
        doc = _pick_kmz_document(paths, staged.root)
        if doc is None:
            raise IngestError("no_supported_content", "The KMZ archive does not contain a .kml document.")
        layers = parse_kml(doc.read_bytes(), None)
        if not layers:
            raise IngestError("no_supported_content", "The KMZ document contains no placemarks.")
        if override is not None:
            for layer in layers:
                _ignored_override_issue(layer, "KML is always EPSG:4326")
        return layers

    # A plain file or an archive: every supported file is read (archive order within a format).
    shp_paths = [p for p in paths if _suffix(p) == _SHAPEFILE_SUFFIX]
    kml_paths = [p for p in paths if _suffix(p) in _KML_SUFFIXES]
    geojson_paths = [p for p in paths if _suffix(p) in _GEOJSON_SUFFIXES]
    gpx_paths = [p for p in paths if _suffix(p) in _GPX_SUFFIXES]
    csv_paths = [p for p in paths if _suffix(p) in _CSV_SUFFIXES]

    if kind == "geojson" and not geojson_paths:
        geojson_paths = paths
    if kind == "kml" and not kml_paths:
        kml_paths = paths
    if kind == "gpx" and not gpx_paths:
        gpx_paths = paths
    if kind == "csv" and not csv_paths:
        csv_paths = paths

    failures: list[IngestError] = []
    if shp_paths:
        shp_layers, failures = _read_shapefiles(shp_paths, override)
        layers.extend(shp_layers)
        shp_stems = {p.with_suffix("").as_posix().lower() for p in shp_paths}
        orphan_issues: list[Issue] = []
        for p in paths:
            if _suffix(p) in (".dbf", ".shx") and p.with_suffix("").as_posix().lower() not in shp_stems:
                orphan_issues.append(
                    Issue(
                        code="missing_component",
                        message=f"'{p.name}' has no matching .shp and was ignored.",
                        severity=Severity.WARNING,
                    )
                )
        if orphan_issues:
            layers.append(RawLayer(name="(ignored files)", crs=None, crs_source=None, issues=orphan_issues, skipped=True))

    multi_kml = len(kml_paths) > 1 or bool(shp_paths)
    for p in kml_paths:
        kml_layers = parse_kml(p.read_bytes(), p.stem if multi_kml else None)
        if override is not None:
            for layer in kml_layers:
                _ignored_override_issue(layer, "KML is always EPSG:4326")
        layers.extend(kml_layers)

    for p in geojson_paths:
        layers.append(_apply_override(read_geojson(p.read_bytes(), p.stem), override))

    # GPX and CSV files next to other files (or several of them) are read one by one: a file that
    # cannot be read is skipped with an issue instead of failing the whole archive.
    tolerant = len(shp_paths) + len(kml_paths) + len(geojson_paths) + len(gpx_paths) + len(csv_paths) > 1
    track_failures: list[IngestError] = []

    def read_one(path: Path, read: Callable[[], list[RawLayer]], why_no_override: str) -> None:
        try:
            read_layers = read()
        except IngestError as exc:
            if not tolerant or exc.code == "too_complex":
                raise
            track_failures.append(exc)
            layers.append(_skipped_file_layer(path, exc))
            return
        if override is not None:
            for layer in read_layers:
                _ignored_override_issue(layer, why_no_override)
        layers.extend(read_layers)

    for p in gpx_paths:
        read_one(p, partial(_gpx_layers, p, tolerant), "GPX is always WGS84")
    for p in csv_paths:
        read_one(p, partial(_csv_layers, p, csv_origin), "CSV tracks are always WGS84")
    _merge_column_notes(layers)

    readable = [layer for layer in layers if not layer.skipped]
    if not readable:
        if shp_paths:
            if any(f.code == "missing_component" for f in failures) or not failures:
                raise IngestError(
                    "missing_component",
                    "No shapefile layer could be read: every .shp needs its .shx and .dbf next to it.",
                    details={"layers": [{"name": layer.name, "issues": [i.message for i in layer.issues]} for layer in layers]},
                )
            first = failures[0]
            raise IngestError(first.code, first.message, details=first.details)
        if track_failures:
            first_track = track_failures[0]
            raise IngestError(first_track.code, first_track.message, details=first_track.details)
        if kml_paths:
            raise IngestError("no_supported_content", "The KML document contains no placemarks.")
        if gpx_paths:
            raise IngestError("no_supported_content", "The GPX document contains no waypoints, routes or tracks.")
        raise IngestError(
            "no_supported_content",
            "No supported content found (expected .shp with .shx/.dbf, .kml, .geojson, .gpx or .csv).",
        )
    return layers


def _gpx_layers(path: Path, prefixed: bool) -> list[RawLayer]:
    from .gpx import parse_gpx

    return parse_gpx(path.read_bytes(), path.stem if prefixed else None)


def _csv_layers(path: Path, origin: tuple[float, float, float] | None) -> list[RawLayer]:
    from .tracks import read_track_csv

    return [read_track_csv(path.read_bytes(), path.stem, origin)]


def _skipped_file_layer(path: Path, exc: IngestError) -> RawLayer:
    return RawLayer(
        name=path.stem,
        crs=None,
        crs_source=None,
        issues=[Issue(code=exc.code, message=f"'{path.name}' skipped: {exc.message}", severity=Severity.WARNING)],
        skipped=True,
    )


def _merge_column_notes(layers: list[RawLayer]) -> None:
    """One "columns not kept" note for a set of CSV files (a per-drone export has one per drone)."""
    carriers = [layer for layer in layers if any(i.code == "csv_columns_not_kept" for i in layer.issues)]
    if len(carriers) < 2:
        return
    for layer in carriers[1:]:
        layer.issues = [i for i in layer.issues if i.code != "csv_columns_not_kept"]
    others = len(carriers) - 1
    suffix = f" The same applies to {others} other CSV file{'s' if others != 1 else ''}."
    first = carriers[0]
    first.issues = [
        i.model_copy(update={"message": i.message + suffix}) if i.code == "csv_columns_not_kept" else i
        for i in first.issues
    ]
