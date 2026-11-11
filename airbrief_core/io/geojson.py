"""GeoJSON reader (RFC 7946, plus the legacy 2008 ``crs`` member).

EPSG:4326 unless a legacy ``crs`` member names another CRS. One that can't be resolved leaves
the CRS unknown, with a warning, rather than guessing. A feature whose geometry can't be built
keeps its properties and gets an ``unsupported_reason``; the rest of the file still loads.

UAS geographical zone files (ED-269 JSON, ED-318 GeoJSON) are recognised and read as polygons
with their limits and validity as flat properties (:mod:`.geozones`), ready to use as zones.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
from pyproj import CRS
from shapely.geometry import shape as shapely_shape

from ..errors import AnalysisError, IngestError
from ..geo.crs import WGS84, resolve_crs
from ..models.common import Issue, Severity
from .geozones import detect_geozone_format, geozone_features
from .reader import RawFeature, RawLayer, charge_vertices

_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}
_NAME_KEYS = ("name", "title", "label")


def _legacy_crs(member: Any) -> tuple[CRS | None, str | None]:
    if not isinstance(member, dict):
        return None, "the 'crs' member is not an object"
    kind = str(member.get("type", "")).lower()
    raw_props = member.get("properties")
    props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
    text: str | None = None
    if kind == "name":
        name = props.get("name")
        text = str(name) if name else None
    elif kind == "epsg":
        code = props.get("code")
        text = f"EPSG:{code}" if code is not None else None
    elif kind == "link":
        return None, "linked CRS definitions are not fetched"
    if not text:
        return None, "the 'crs' member has no usable name or code"
    try:
        return resolve_crs(text), None
    except IngestError:
        return None, f"the CRS '{text[:200]}' is not recognised"


def _flightmd_report(doc: dict[str, Any]) -> dict[str, Any] | None:
    """The report when ``doc`` is a FlightMD report JSON (optionally nested under ``"report"``):
    a flight log for the flight audit module, not map data. Otherwise None."""
    nested = doc.get("report")
    report: dict[str, Any] = nested if isinstance(nested, dict) else doc
    if "schema_version" not in report:
        return None
    metadata = report.get("metadata")
    has_path = "gps_path" in report or (isinstance(metadata, dict) and "gps_path" in metadata)
    return report if "report_id" in report or has_path else None


def _not_geojson_message(gtype: object) -> str:
    """Plain-language reason a JSON document is not GeoJSON (no Python reprs)."""
    if gtype is None:
        return 'This JSON file has no GeoJSON "type" field, so it holds no map data.'
    if isinstance(gtype, str) and gtype.strip():
        return (
            f'This JSON file is of type "{gtype.strip()[:60]}", not GeoJSON. '
            "Expected a FeatureCollection, a Feature or a geometry."
        )
    return 'This JSON file has an invalid "type" field, so it is not GeoJSON.'


def _feature_name(props: dict[str, Any]) -> str | None:
    lowered = {str(k).lower(): v for k, v in props.items()}
    for key in _NAME_KEYS:
        value = lowered.get(key)
        if isinstance(value, str | int | float) and str(value).strip():
            return str(value).strip()
    return None


def _feature(obj: dict[str, Any], position: int) -> RawFeature:
    raw_props = obj.get("properties")
    if isinstance(raw_props, dict):
        props: dict[str, Any] = dict(raw_props)
    elif raw_props is None:
        props = {}
    else:
        props = {"value": raw_props}
    fid_value = obj.get("id")
    fid = str(fid_value) if fid_value is not None else str(position)
    feature = RawFeature(fid=fid, name=_feature_name(props), geometry=None, properties=props)
    geom = obj.get("geometry")
    if geom is None:
        return feature
    if not isinstance(geom, dict) or geom.get("type") not in _GEOMETRY_TYPES:
        gtype = geom.get("type") if isinstance(geom, dict) else type(geom).__name__
        feature.unsupported_reason = f"Unsupported GeoJSON geometry type '{gtype}'"
        return feature
    try:
        # NaN/Infinity parse as JSON numbers; the record builder rejects them (no warning here).
        with np.errstate(invalid="ignore"):
            built = shapely_shape(geom)
    except Exception as exc:  # shapely raises several types for malformed coordinates
        feature.unsupported_reason = f"Invalid {geom.get('type')} geometry: {exc}"
        return feature
    feature.geometry = None if built.is_empty else built
    charge_vertices(feature.geometry)
    return feature


def read_geojson(data: bytes, layer_name: str) -> RawLayer:
    """One layer from GeoJSON bytes.

    Raises ``corrupt_file`` for invalid JSON or a document that is not GeoJSON, and
    ``flightmd_report`` for a FlightMD report (that belongs in the flight audit module).
    """
    try:
        doc = json.loads(data)
    except (ValueError, RecursionError) as exc:
        raise IngestError("corrupt_file", "The file is not valid JSON.", details={"reason": str(exc)}) from exc
    issues: list[Issue] = []
    # before the type checks: some zone files are a bare list or sit in an envelope
    zone_format = detect_geozone_format(doc)
    if zone_format is None and not isinstance(doc, dict):
        raise IngestError("corrupt_file", "This JSON file is a list or a value, not a GeoJSON object.")
    if zone_format is not None:
        try:
            zone_features = geozone_features(doc, zone_format)
        except AnalysisError as exc:
            raise IngestError("invalid_zone_file", f"This {zone_format.upper()} zone file could not be read: {exc.message}",
                              details=exc.details) from exc
        issues.append(Issue(
            code="geozone_file",
            message=f"Layer '{layer_name}' was read as an {zone_format.upper()} zone file: {len(zone_features)} zones, "
                    "circles as 64-sided polygons, limits in metres. Use it as zones in the airspace check.",
            severity=Severity.INFO,
        ))
        doc = {"type": "FeatureCollection", "features": zone_features}

    gtype = doc.get("type")
    report = _flightmd_report(doc) if gtype is None else None
    if report is not None:
        report_id = report.get("report_id")
        raise IngestError(
            "flightmd_report",
            "This is a FlightMD flight report, not map data. Open it in the Flight audit module.",
            details={"report_id": str(report_id) if report_id is not None else None},
        )
    if gtype == "FeatureCollection":
        raw_features = doc.get("features")
        if not isinstance(raw_features, list):
            raise IngestError("corrupt_file", "The FeatureCollection has no 'features' array.")
    elif gtype == "Feature":
        raw_features = [doc]
    elif gtype in _GEOMETRY_TYPES:
        raw_features = [{"type": "Feature", "properties": {}, "geometry": doc}]
    else:
        raise IngestError(
            "corrupt_file",
            _not_geojson_message(gtype),
            details={"type": gtype if isinstance(gtype, str) else None},
        )

    crs: CRS | None = WGS84
    crs_source: str | None = "geojson"
    if "crs" in doc and doc["crs"] is not None:
        legacy, problem = _legacy_crs(doc["crs"])
        if legacy is None:
            crs, crs_source = None, None
            issues.append(
                Issue(
                    code="invalid_crs",
                    message=f"Layer '{layer_name}': {problem}. The CRS is unknown, so features are not measured.",
                    severity=Severity.WARNING,
                )
            )
        else:
            crs = legacy
            issues.append(
                Issue(
                    code="legacy_crs",
                    message=f"Layer '{layer_name}' uses the legacy GeoJSON 'crs' member; honoured as {legacy.to_string()}.",
                    severity=Severity.INFO,
                )
            )

    features: list[RawFeature] = []
    for position, item in enumerate(raw_features):
        if not isinstance(item, dict) or item.get("type") != "Feature":
            issues.append(
                Issue(
                    code="invalid_feature",
                    message=f"Layer '{layer_name}' item {position} is not a GeoJSON Feature and was skipped.",
                    severity=Severity.WARNING,
                )
            )
            continue
        features.append(_feature(item, position))
    return RawLayer(name=layer_name, crs=crs, crs_source=crs_source, features=features, issues=issues)
