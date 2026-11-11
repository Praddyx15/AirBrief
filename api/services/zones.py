"""Zones from datasets for the airspace check, the stored zone profile, and ED-318 exports.

``zone_dataset_ids`` name datasets whose polygons become zones (``dataset_zones`` in the stored
parameters). A dataset is read by its id like any other (possession of the id is read access):
at most 10 datasets, only features whose bounding box meets the checked geometry (widened by
~1 km), at most 5,000 zones in all, read as ``zone_mappings`` says. A request's own
``dataset_zones`` are always replaced, so the 5,000 cap is the server's, not a client's.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from airbrief_core.errors import AirBriefError
from airbrief_core.geo.transform import geojson_to_geometry
from airbrief_core.io.geozones import Ed318Limits, to_ed318, zone_from_properties
from airbrief_core.models.common import UserZone, ZoneKind
from airbrief_core.models.zones import MAX_DATASET_ZONES, MAX_ZONE_DATASETS, ZoneLayerMapping

from ..errors import ApiError, invalid_params_error
from ..models import Feature
from .datasets import get_dataset

REACH_MARGIN_DEG = 0.01
"""Zones are read where their bounding box comes within this of the checked geometry's (≈ 1 km):
the flyability grid reaches 50 m beyond lines and points."""

_POLYGONS = ("Polygon", "MultiPolygon")


def _reach(geometry: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(geometry, dict):
        return None
    try:
        geom = geojson_to_geometry(geometry)
    except AirBriefError:
        return None
    if geom.is_empty:
        return None
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    m = REACH_MARGIN_DEG
    return min_lon - m, min_lat - m, max_lon + m, max_lat + m


def _mapping(raw: Any, dataset_id: str) -> ZoneLayerMapping:
    try:
        return ZoneLayerMapping.model_validate(raw or {})
    except ValidationError as exc:
        raise invalid_params_error(exc, prefix=f"params.zone_mappings.{dataset_id}") from exc


ZONE_PROFILE_KINDS = frozenset({"airspace", "flight_audit", "evtol_siting", "evtol_corridor"})

ASSESSMENT_CHOICES: dict[str, dict[str, str]] = {
    "flight_audit": {"band_ceiling": "exceedance"},
    "evtol_siting": {"unverified_band": "permission"},
    "evtol_corridor": {"unverified_band": "permission"},
}
"""The user's assessment choices per kind, with their defaults (docs/features/choices.md)."""


def record_zone_profile(params: dict[str, Any], kind: str = "airspace") -> None:
    """Store the zone profile a run uses (airspace, flight audit, eVTOL) and its assessment choices."""
    # explicitly, so a later change of the default never changes what a stored run meant
    params.setdefault("zone_profile", "custom" if params.get("zone_rules") is not None else "dgca_2021")
    for key, default in ASSESSMENT_CHOICES.get(kind, {}).items():
        params.setdefault(key, default)


def resolve_zone_inputs(session: Session, params: dict[str, Any]) -> None:
    """Record the zone profile an airspace check runs with, and fill ``params["dataset_zones"]``
    from ``params["zone_dataset_ids"]`` (see the module docstring).

    Raises ``not_found`` / ``dataset_not_ready`` for a dataset, ``invalid_params`` for a dataset
    without polygons or a value out of range, ``too_many_zones`` past 5,000 zones.
    """
    record_zone_profile(params)
    params.pop("dataset_zones", None)
    ids = params.get("zone_dataset_ids")
    if not ids:
        return
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > MAX_ZONE_DATASETS:
        raise ApiError("invalid_params", f"zone_dataset_ids must be a list of at most {MAX_ZONE_DATASETS} dataset ids.",
                       details={"field": "zone_dataset_ids"})
    mappings = params.get("zone_mappings") or {}
    if not isinstance(mappings, dict):
        raise ApiError("invalid_params", "zone_mappings must map dataset ids to column mappings.",
                       details={"field": "zone_mappings"})
    reach = _reach(params.get("geometry"))
    zones: list[dict[str, Any]] = []
    for dataset_id in dict.fromkeys(ids):
        row = get_dataset(session, dataset_id)
        if row.status != "COMPLETED":
            raise ApiError("dataset_not_ready", f"Zone dataset '{dataset_id}' is {row.status}; it must be COMPLETED.",
                           details={"dataset_id": dataset_id, "status": row.status})
        mapping = _mapping(mappings.get(dataset_id), dataset_id)
        scope = (Feature.dataset_id == dataset_id) & Feature.geometry_type.in_(_POLYGONS)
        if mapping.layer is not None:
            scope = scope & (Feature.layer == mapping.layer)
        if not session.scalar(select(func.count()).select_from(Feature).where(scope)):
            where = f" in layer '{mapping.layer}'" if mapping.layer is not None else ""
            raise ApiError("invalid_params", f"Dataset '{row.filename}' has no polygons{where} to use as zones.",
                           details={"dataset_id": dataset_id, "layer": mapping.layer})
        query = select(Feature).where(scope)
        if reach is not None:
            min_lon, min_lat, max_lon, max_lat = reach
            query = query.where(Feature.max_lon >= min_lon, Feature.min_lon <= max_lon,
                                Feature.max_lat >= min_lat, Feature.min_lat <= max_lat)
        for feature in session.scalars(query.order_by(Feature.feature_index)):
            if len(zones) >= MAX_DATASET_ZONES:
                raise ApiError(
                    "too_many_zones",
                    f"More than {MAX_DATASET_ZONES:,} zones of the chosen datasets reach this area. Pick fewer "
                    "datasets or a layer, or check a smaller area.",
                    details={"limit": MAX_DATASET_ZONES},
                )
            name = feature.name or f"{row.filename} #{feature.feature_index}"
            try:
                zone = zone_from_properties(feature.properties or {}, feature.geometry_wgs84 or {}, name=name,
                                            layer_mapping=mapping)
            except AirBriefError as exc:
                raise ApiError("invalid_params", f"Dataset '{row.filename}', feature {feature.feature_index}: {exc.message}",
                               details={"dataset_id": dataset_id, "feature_index": feature.feature_index}) from exc
            zones.append(zone.model_dump(mode="json"))
    params["dataset_zones"] = zones


def dataset_ed318(session: Session, dataset_id: str, limits: Ed318Limits = "geometry") -> dict[str, Any]:
    """The polygons of a dataset as an ED-318 FeatureCollection, zone kinds read as for zone datasets
    (a polygon without a zone column is red, ``PROHIBITED``). Raises ``no_zone_polygons`` (422)
    when the dataset has no polygons."""
    row = get_dataset(session, dataset_id)
    query = (select(Feature).where(Feature.dataset_id == dataset_id, Feature.geometry_type.in_(_POLYGONS))
             .order_by(Feature.feature_index))
    zones: list[UserZone] = []
    for feature in session.scalars(query):
        name = feature.name or f"{row.filename} #{feature.feature_index}"
        try:
            zones.append(zone_from_properties(feature.properties or {}, feature.geometry_wgs84 or {}, name=name))
        except AirBriefError as exc:
            raise ApiError("invalid_params", f"Feature {feature.feature_index} is not a valid zone: {exc.message}",
                           details={"feature_index": feature.feature_index}) from exc
    if not zones:
        raise ApiError("no_zone_polygons", "This dataset has no polygons to export as zones.", status=422,
                       details={"format": "ed318"})
    return to_ed318(zones, name=row.filename, limits=limits)


def _ring_zones(props: dict[str, Any], geometry: dict[str, Any]) -> list[UserZone]:
    name = " ".join(p for p in (props.get("ident"), str(props.get("label") or "").split(":")[0]) if p) or "Aerodrome band"
    label = str(props.get("label") or "") or None
    ceiling = props.get("ceiling_agl_m")
    above = props.get("ceiling_exceeded_zone")
    zone: ZoneKind = props["zone"] if props.get("zone") in ("green", "yellow", "red") else "yellow"
    if above in ("yellow", "red"):
        limit = float(ceiling) if isinstance(ceiling, int | float) else None
        if limit is not None:
            return [
                UserZone(name=f"{name} up to {limit:g} m"[:120], zone=zone, geometry=geometry, upper_limit_m=limit,
                         note=label),
                UserZone(name=f"{name} above {limit:g} m"[:120], zone=above, geometry=geometry, lower_limit_m=limit,
                         note=label),
            ]
    if props.get("basis") == "verify_official_map" or props.get("permission_required"):
        return [UserZone(name=name[:120], zone="red" if zone == "red" else "yellow", geometry=geometry, note=label)]
    return [UserZone(name=name[:120], zone=zone, geometry=geometry,
                     ceiling_agl_m=float(ceiling) if isinstance(ceiling, int | float) else None, note=label)]


ED318_KINDS = frozenset({"airspace", "flight_audit"})
"""Kinds whose results carry a ``zones`` layer (a flight audit only when bands or zones were near)."""


def analysis_zones(kind: str, result: dict[str, Any]) -> list[UserZone]:
    """The ``zones`` layer of an airspace or flight-audit result as zones: aerodrome bands (a band
    that changes zone at its ceiling becomes two volumes) and the user, dataset and pack zones it
    touched. A flight audit stored before the layer, or with nothing near, has none."""
    if kind not in ED318_KINDS:
        raise ApiError("invalid_params", "Only airspace and flight-audit analyses have zones to export as ED-318.",
                       details={"format": "ed318", "kind": kind})
    layer = (result.get("layers") or {}).get("zones")
    if not isinstance(layer, dict):
        if kind == "flight_audit":
            return []
        raise ApiError("invalid_params", "This airspace analysis has no zones layer to export as ED-318.",
                       details={"format": "ed318", "kind": kind})
    zones: list[UserZone] = []
    for i, feature in enumerate(layer.get("features") or []):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        if props.get("source") == "aerodrome":
            zones += _ring_zones(props, geometry)
        else:
            zones.append(zone_from_properties(props, geometry, name=str(props.get("name") or f"Zone {i + 1}")))
    return zones


def analysis_ed318(kind: str, result: dict[str, Any], *, name: str, limits: Ed318Limits = "geometry") -> dict[str, Any]:
    """:func:`analysis_zones` as an ED-318 FeatureCollection (the brief bundle's keeps the default)."""
    return to_ed318(analysis_zones(kind, result), name=name, limits=limits)
