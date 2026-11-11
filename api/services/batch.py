"""One result per feature: the features of ``params.batch`` (called from :mod:`api.services.analyses`).

``batch = {"mode": "per_feature", "key_property"?: str, "features"?: FeatureCollection}`` works
for airspace, ground risk, survey, environment and line of sight. Without ``features`` the
selected features (all of them when none are selected) are used, each with its properties, its
name as ``_name`` and its id as ``_fid``; line of sight leaves out the pilot, observer and
ground-station points, which are its observers. Only then is ``from_dataset`` set, so the items
keep the dataset's feature numbers; features sent in the request are numbered by position.
``geometry`` becomes the extent of the features: it only chooses the data pack, since
:mod:`airbrief_core.batch` runs each feature on its own.
"""

from __future__ import annotations

from typing import Any

import shapely

from airbrief_core.batch import check_batch_size
from airbrief_core.errors import AirBriefError, AnalysisError
from airbrief_core.geo.transform import geojson_to_geometry, geometry_to_geojson
from airbrief_core.models.batch import BATCH_KINDS

from ..errors import ApiError
from ..models import Feature
from .line_of_sight import observer_role

__all__ = ["resolve_batch"]


def _is_observer(f: Feature) -> bool:
    geom = f.geometry_wgs84
    return geom is not None and geom.get("type") == "Point" and observer_role(f.name, f.properties) is not None


def _as_feature(f: Feature) -> dict[str, Any]:
    props: dict[str, Any] = {**(f.properties or {}), "_name": f.name}
    if f.fid:
        props["_fid"] = f.fid
    return {"type": "Feature", "id": f.feature_index, "geometry": f.geometry_wgs84, "properties": props}


def _extent(features: list[Any]) -> dict[str, Any] | None:
    boxes = []
    for f in features:
        geometry = f.get("geometry") if isinstance(f, dict) else None
        if not isinstance(geometry, dict):
            continue
        try:
            geom = geojson_to_geometry(geometry)
        except AirBriefError:
            continue  # the item reports it
        if not geom.is_empty:
            boxes.append(geom.bounds)
    if not boxes:
        return None
    box = shapely.box(min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    return geometry_to_geojson(box, decimals=8)


def resolve_batch(kind: str, resolved: dict[str, Any], selected: list[Feature], has_dataset: bool) -> None:
    """Fill ``batch.features`` from the selection and set ``geometry`` to their extent (see the module docstring)."""
    if kind not in BATCH_KINDS:
        raise ApiError(
            "invalid_params",
            "One result per feature is available for airspace, ground risk, survey, environment and line of sight.",
            details={"kind": kind, "field": "batch"},
        )
    batch = resolved["batch"]
    if not isinstance(batch, dict):
        return  # validation reports the wrong type
    # set here, never taken from the request: a client's own features are numbered by position
    batch = resolved["batch"] = {**batch, "from_dataset": "features" not in batch}
    if batch["from_dataset"]:
        if not has_dataset:
            raise ApiError("invalid_params", "One result per feature needs a dataset, or the features in batch.features.",
                           details={"field": "batch.features"})
        features = [_as_feature(f) for f in selected if not (kind == "line_of_sight" and _is_observer(f))]
        if not features:
            raise AnalysisError("no_features", "The selection has no features to run one by one.", details={"kind": kind})
        batch = resolved["batch"] = {**batch, "features": {"type": "FeatureCollection", "features": features}}
    collection = batch["features"]
    items = collection.get("features") if isinstance(collection, dict) else None
    if not isinstance(items, list):
        return
    check_batch_size(kind, len(items))
    extent = _extent(items)
    if extent is None:
        raise AnalysisError(
            "no_features",
            "None of the features has a known position (unknown CRS or empty geometry).",
            details={"kind": kind},
        )
    resolved["geometry"] = extent
