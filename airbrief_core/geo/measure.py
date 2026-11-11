"""Per-feature measurement (docs/SPEC.md "Measurement rules").

Procedure for a measurable geometry in a known CRS:

1. Transform to WGS84 and unwrap the antimeridian.
2. Take the centroid (lon0, lat0), rounded to 0.01 degrees so neighbouring features share
   cached transformers (the effect on accuracy is below 1e-6 relative).
3. Area in a Lambert Azimuthal Equal-Area projection centred there; perimeter and length in
   a Transverse Mercator projection (k=1) centred there.
4. 3D length = sum of sqrt(dx² + dy² + dz²) in the TM projection, using the source Z values.
5. Geodesic ground truth from ``pyproj.Geod`` and ``deviation_pct`` = the largest relative
   difference across the measured quantities, in percent.

Projected sources also report ``grid_area_m2``/``grid_length_m``, measured in the source grid
and converted to metres with the CRS unit factor. ``grid_length_m`` is the lineal length when
the feature has lineal parts, otherwise the polygon perimeter.
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from pyproj import CRS
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from airbrief_core.models.features import Measurement, MeasurementStatus

from .crs import (
    WGS84,
    is_geographic,
    local_area_crs,
    local_area_proj4,
    local_length_crs,
    local_length_proj4,
    unit_factor,
    utm_epsg_for,
    utm_zone_label,
)
from .geodesic import geodesic_area_perimeter, geodesic_length
from .transform import transform_geom, unwrap_antimeridian
from .validity import iter_parts

__all__ = ["CENTRE_ROUNDING_DEG", "measure_geometry"]

CENTRE_ROUNDING_DEG = 0.01
"""Grid to which the local projection centre is snapped (for transformer cache reuse)."""

REASON_EMPTY = "Empty geometry — not measured"
REASON_CRS_UNKNOWN = "CRS unknown — not measured"
REASON_POINTS = "Points have no length or area"


def _snap(value: float) -> float:
    return round(round(value / CENTRE_ROUNDING_DEG) * CENTRE_ROUNDING_DEG, 6)


def _part_counts(geom: BaseGeometry) -> dict[str, int]:
    counts: dict[str, int] = {}
    for part in iter_parts(geom):
        name = "LineString" if part.geom_type == "LinearRing" else part.geom_type
        counts[name] = counts.get(name, 0) + 1
    return counts


def _centre(geom_wgs84: BaseGeometry) -> tuple[float, float]:
    centre = geom_wgs84.centroid
    if not centre.is_empty and math.isfinite(centre.x) and math.isfinite(centre.y):
        lon, lat = float(centre.x), float(centre.y)
    else:
        minx, miny, maxx, maxy = geom_wgs84.bounds
        lon, lat = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    lon = ((lon + 180.0) % 360.0) - 180.0
    lat = max(-90.0, min(90.0, lat))
    return lon, lat


def _length_3d(line_tm: LineString) -> float:
    coords = np.asarray(line_tm.coords, dtype=float)
    if coords.shape[0] < 2:
        return 0.0
    if coords.shape[1] < 3:
        diffs = np.diff(coords[:, :2], axis=0)
        return float(np.sum(np.hypot(diffs[:, 0], diffs[:, 1])))
    z = np.where(np.isfinite(coords[:, 2]), coords[:, 2], 0.0)
    dx = np.diff(coords[:, 0])
    dy = np.diff(coords[:, 1])
    dz = np.diff(z)
    return float(np.sum(np.sqrt(dx * dx + dy * dy + dz * dz)))


def _rel_dev(value: float, truth: float) -> float | None:
    if not (math.isfinite(value) and math.isfinite(truth)) or truth <= 0.0:
        return None
    return abs(value - truth) / truth * 100.0


def measure_geometry(geom: BaseGeometry | None, crs: CRS | None, *, repaired: bool = False) -> Measurement:
    """Measure one feature geometry given in ``crs``. Never raises for a bad geometry:
    unexpected failures give ``status=error`` with the reason."""
    if geom is None or geom.is_empty:
        return Measurement(status=MeasurementStatus.SKIPPED, reason=REASON_EMPTY, repaired=repaired)
    has_z = bool(geom.has_z)
    parts = _part_counts(geom)
    if crs is None:
        return Measurement(
            status=MeasurementStatus.SKIPPED,
            reason=REASON_CRS_UNKNOWN,
            repaired=repaired,
            has_z=has_z,
            parts=parts,
        )
    atoms = list(iter_parts(geom))
    polygons = [p for p in atoms if isinstance(p, Polygon)]
    lines = [p for p in atoms if isinstance(p, LineString)]
    if not polygons and not lines:
        return Measurement(
            status=MeasurementStatus.NOT_APPLICABLE,
            reason=REASON_POINTS,
            repaired=repaired,
            has_z=has_z,
            parts=parts,
        )
    try:
        return _measure(polygons, lines, crs, repaired=repaired, has_z=has_z, parts=parts)
    except Exception as exc:  # one bad feature must not stop the others
        return Measurement(
            status=MeasurementStatus.ERROR,
            reason=f"Measurement failed: {getattr(exc, 'message', None) or exc}",
            repaired=repaired,
            has_z=has_z,
            parts=parts,
        )


def _measure(
    polygons: list[Polygon],
    lines: list[LineString],
    crs: CRS,
    *,
    repaired: bool,
    has_z: bool,
    parts: dict[str, int],
) -> Measurement:
    plain_lines = [LineString(ln.coords) if ln.geom_type == "LinearRing" else ln for ln in lines]
    measurable = shapely.GeometryCollection([*polygons, *plain_lines])

    grid_area: float | None = None
    grid_length: float | None = None
    if not is_geographic(crs):
        factor = unit_factor(crs)
        if polygons:
            grid_area = float(sum(p.area for p in polygons)) * factor * factor
        if plain_lines:
            grid_length = float(sum(ln.length for ln in plain_lines)) * factor
        elif polygons:
            grid_length = float(sum(p.length for p in polygons)) * factor

    wgs = unwrap_antimeridian(transform_geom(measurable, crs, WGS84))
    coords = shapely.get_coordinates(wgs)
    if coords.size == 0 or not np.all(np.isfinite(coords)):
        raise ValueError("coordinates are not finite after transformation to WGS84")
    lon0, lat0 = _centre(wgs)
    lon0, lat0 = _snap(lon0), _snap(lat0)
    lon0 = ((lon0 + 180.0) % 360.0) - 180.0
    lat0 = max(-90.0, min(90.0, lat0))

    wgs_parts = list(wgs.geoms)  # type: ignore[attr-defined]
    wgs_polys = [g for g in wgs_parts if isinstance(g, Polygon)]
    wgs_lines = [g for g in wgs_parts if isinstance(g, LineString)]

    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    length_3d_m: float | None = None
    geo_area: float | None = None
    geo_perimeter: float | None = None
    geo_length: float | None = None
    deviations: list[float] = []

    length_crs = local_length_crs(lon0, lat0)
    if wgs_polys:
        poly_collection = shapely.MultiPolygon(wgs_polys)
        poly_laea = transform_geom(poly_collection, WGS84, local_area_crs(lon0, lat0))
        poly_tm = transform_geom(poly_collection, WGS84, length_crs)
        area_m2 = float(poly_laea.area)
        perimeter_m = float(poly_tm.length)
        geo_area, geo_perimeter = geodesic_area_perimeter(poly_collection)
        for value, truth in ((area_m2, geo_area), (perimeter_m, geo_perimeter)):
            dev = _rel_dev(value, truth)
            if dev is not None:
                deviations.append(dev)
    if wgs_lines:
        line_collection = shapely.MultiLineString(wgs_lines)
        lines_tm = transform_geom(line_collection, WGS84, length_crs)
        length_m = float(lines_tm.length)
        if has_z:
            length_3d_m = float(sum(_length_3d(ln) for ln in lines_tm.geoms))  # type: ignore[attr-defined]
        geo_length = geodesic_length(line_collection)
        dev = _rel_dev(length_m, geo_length)
        if dev is not None:
            deviations.append(dev)

    utm_epsg = utm_epsg_for(lon0, lat0)
    return Measurement(
        status=MeasurementStatus.MEASURED,
        area_m2=area_m2,
        perimeter_m=perimeter_m,
        length_m=length_m,
        length_3d_m=length_3d_m,
        geodesic_area_m2=geo_area,
        geodesic_perimeter_m=geo_perimeter,
        geodesic_length_m=geo_length,
        deviation_pct=max(deviations) if deviations else None,
        grid_area_m2=grid_area,
        grid_length_m=grid_length,
        method="local-projection",
        area_crs=local_area_proj4(lon0, lat0) if wgs_polys else None,
        length_crs=local_length_proj4(lon0, lat0),
        utm_epsg=utm_epsg,
        utm_zone=utm_zone_label(utm_epsg),
        repaired=repaired,
        has_z=has_z,
        parts=parts,
    )
