"""Geodesy and geometry toolkit: CRS handling, transforms, measurement, H3 and sampling."""

from __future__ import annotations

from .crs import (
    WGS84,
    crs_label,
    is_geographic,
    local_area_crs,
    local_length_crs,
    resolve_crs,
    try_resolve_crs,
    utm_epsg_for,
    utm_zone_label,
)
from .frame import LocalFrame
from .geodesic import GEOD, bearing_deg, distance_m, geodesic_area_perimeter, geodesic_length
from .h3grid import (
    cell_area_m2,
    cell_centroids,
    cell_polygon,
    cells_for_polygon,
    cells_to_feature_collection,
    neighbours,
    ring_radius_k,
)
from .measure import measure_geometry
from .sampling import grid_points, sample_line
from .transform import (
    geojson_to_geometry,
    geometry_to_geojson,
    to_wgs84,
    transform_geom,
    unwrap_antimeridian,
)
from .validity import drop_z, geometry_type_name, lineal_part, polygonal_part, repair

__all__ = [
    "GEOD",
    "WGS84",
    "LocalFrame",
    "bearing_deg",
    "cell_area_m2",
    "cell_centroids",
    "cell_polygon",
    "cells_for_polygon",
    "cells_to_feature_collection",
    "crs_label",
    "distance_m",
    "drop_z",
    "geodesic_area_perimeter",
    "geodesic_length",
    "geojson_to_geometry",
    "geometry_to_geojson",
    "geometry_type_name",
    "grid_points",
    "is_geographic",
    "lineal_part",
    "local_area_crs",
    "local_length_crs",
    "measure_geometry",
    "neighbours",
    "polygonal_part",
    "repair",
    "resolve_crs",
    "ring_radius_k",
    "sample_line",
    "to_wgs84",
    "transform_geom",
    "try_resolve_crs",
    "unwrap_antimeridian",
    "utm_epsg_for",
    "utm_zone_label",
]
