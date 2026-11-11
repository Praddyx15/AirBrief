"""Local metric frame: Azimuthal Equidistant (AEQD) projection on the WGS84 ellipsoid.

Distances and azimuths from the origin are exact; elsewhere distortion is negligible for the
tens-of-kilometres extents analyses work with. x points east, y points north (ENU).
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from numpy.typing import ArrayLike
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from airbrief_core.errors import AnalysisError

from .crs import WGS84
from .transform import get_transformer, transform_geom, unwrap_antimeridian

__all__ = ["LocalFrame"]


def _fmt(value: float) -> str:
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


class LocalFrame:
    """A local east/north metre frame centred on (lon0, lat0)."""

    def __init__(self, lon0: float, lat0: float) -> None:
        if not (math.isfinite(lon0) and math.isfinite(lat0)) or not -90.0 <= lat0 <= 90.0:
            raise AnalysisError(
                "invalid_geometry",
                "Local frame origin must be a finite lon/lat with latitude in [-90, 90]",
                details={"lon0": lon0, "lat0": lat0},
            )
        self.lon0 = ((float(lon0) + 180.0) % 360.0) - 180.0
        self.lat0 = float(lat0)
        self.proj4 = (
            f"+proj=aeqd +lat_0={_fmt(self.lat0)} +lon_0={_fmt(self.lon0)} "
            "+x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
        )
        self.crs: CRS = CRS.from_proj4(self.proj4)
        self._fwd = get_transformer(WGS84, self.crs)
        self._inv = get_transformer(self.crs, WGS84)

    def __repr__(self) -> str:
        return f"LocalFrame(lon0={self.lon0!r}, lat0={self.lat0!r})"

    @classmethod
    def for_geometry(cls, geom: BaseGeometry) -> LocalFrame:
        """Frame centred on the centroid of a WGS84 geometry (antimeridian-safe)."""
        if geom is None or geom.is_empty:
            raise AnalysisError("invalid_geometry", "Cannot build a local frame for an empty geometry")
        unwrapped = unwrap_antimeridian(geom)
        centre = unwrapped.centroid
        if centre.is_empty or not (math.isfinite(centre.x) and math.isfinite(centre.y)):
            minx, miny, maxx, maxy = unwrapped.bounds
            lon0, lat0 = (minx + maxx) / 2.0, (miny + maxy) / 2.0
        else:
            lon0, lat0 = centre.x, centre.y
        return cls(lon0, max(-90.0, min(90.0, lat0)))

    def to_local(self, geom: BaseGeometry) -> BaseGeometry:
        """WGS84 lon/lat geometry -> local metres (Z passes through)."""
        return transform_geom(geom, WGS84, self.crs)

    def to_wgs84(self, geom: BaseGeometry) -> BaseGeometry:
        """Local metre geometry -> WGS84 lon/lat (Z passes through)."""
        return transform_geom(geom, self.crs, WGS84)

    def xy(self, lons: ArrayLike, lats: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
        """Project lon/lat arrays to local x (east) and y (north) metres."""
        lo = np.asarray(lons, dtype=float)
        la = np.asarray(lats, dtype=float)
        xs, ys = self._fwd.transform(lo, la)
        return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)

    def lonlat(self, xs: ArrayLike, ys: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
        """Inverse of :meth:`xy`: local metres to lon/lat arrays."""
        x = np.asarray(xs, dtype=float)
        y = np.asarray(ys, dtype=float)
        lons, lats = self._inv.transform(x, y)
        return np.asarray(lons, dtype=float), np.asarray(lats, dtype=float)

    def distance_from_origin(self, geom: BaseGeometry) -> float:
        """Shortest planar distance (m) from the frame origin to a WGS84 geometry."""
        return float(shapely.distance(shapely.Point(0.0, 0.0), self.to_local(geom)))
