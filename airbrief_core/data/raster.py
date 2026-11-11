"""In-memory raster sampling for data-pack layers (DEM and population).

:class:`RasterSampler` reads band 1 of a GeoTIFF into memory the first time it is used
(thread-safe, guarded by a lock) and samples it with index math:

* ``sample`` — bilinear (default) or nearest. Pixel values are taken at pixel centres.
  A point whose own pixel is nodata gives NaN; neighbouring nodata pixels are dropped from the
  bilinear weights. Points outside the raster extent give NaN.
* ``density_per_km2`` — population semantics: the raster holds *people per pixel*; density is
  people divided by the geodesic area of that pixel in km² (exact ellipsoidal quadrangle area for
  north-up geographic rasters), then interpolated bilinearly.
* ``integrate_density`` — density sampled on ``geo.sampling.grid_points`` × cell area → people.
* ``slope_deg`` — central finite differences in metres, ``delta_m`` east/west/north/south of each
  point (one-sided at the edges).

Rasters are expected in EPSG:4326 (data packs are written that way); other CRSs are supported by
transforming the query points with pyproj.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from ..errors import AnalysisError, DataPackError

log = logging.getLogger(__name__)

SampleMethod = Literal["bilinear", "nearest"]
ArrayLike = float | Sequence[float] | np.ndarray

# WGS84 ellipsoid
_WGS84_A = 6_378_137.0
_WGS84_F = 1.0 / 298.257223563
_WGS84_B = _WGS84_A * (1.0 - _WGS84_F)
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)
_WGS84_E = math.sqrt(_WGS84_E2)


def _authalic_q(lat_deg: np.ndarray) -> np.ndarray:
    s = np.sin(np.radians(lat_deg))
    es = _WGS84_E * s
    return s / (1.0 - _WGS84_E2 * s * s) + (1.0 / (2.0 * _WGS84_E)) * np.log((1.0 + es) / (1.0 - es))


def geographic_cell_area_km2(lat1_deg: ArrayLike, lat2_deg: ArrayLike, dlon_deg: ArrayLike) -> np.ndarray:
    """Exact area (km²) of WGS84 quadrangles bounded by two parallels and ``dlon_deg`` of longitude.

    Uses ``A = b²·Δλ/2 · |q(φ2) − q(φ1)|`` with ``q`` the authalic function; vectorised.
    """
    lat1 = np.clip(np.asarray(lat1_deg, dtype=np.float64), -90.0, 90.0)
    lat2 = np.clip(np.asarray(lat2_deg, dtype=np.float64), -90.0, 90.0)
    dlon = np.radians(np.abs(np.asarray(dlon_deg, dtype=np.float64)))
    area_m2 = (_WGS84_B**2) * dlon / 2.0 * np.abs(_authalic_q(lat2) - _authalic_q(lat1))
    return area_m2 / 1e6


def _metres_per_degree(lat_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local metres per degree of longitude and latitude on WGS84."""
    phi = np.radians(lat_deg)
    s2 = np.sin(phi) ** 2
    w = np.sqrt(1.0 - _WGS84_E2 * s2)
    m_lon = math.pi / 180.0 * _WGS84_A * np.cos(phi) / w
    m_lat = math.pi / 180.0 * _WGS84_A * (1.0 - _WGS84_E2) / w**3
    return m_lon, m_lat


def _polygonal(geom: BaseGeometry) -> BaseGeometry | None:
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [p for p in shapely.get_parts(geom) if p.geom_type in ("Polygon", "MultiPolygon") and not p.is_empty]
    if not parts:
        return None
    return shapely.union_all(parts)


class RasterSampler:
    """Lazy, thread-safe, in-memory sampler for a single-band GeoTIFF.

    ``name`` (default: the file stem) is reported as ``source`` in results. ``kind`` and
    ``units`` (e.g. ``"m"``, ``"people/pixel"``) are labels only.
    """

    def __init__(self, path: Path | str, name: str | None = None, *, kind: str | None = None, units: str | None = None) -> None:
        self.path = Path(path)
        self.name = name or self.path.stem
        self.kind = kind
        self.units = units
        self._lock = threading.Lock()
        self._loaded = False
        self._array: np.ndarray | None = None
        self._density: np.ndarray | None = None
        self._inv: tuple[float, float, float, float, float, float] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        self._transform: Any = None
        self._crs: Any = None
        self._transformer: Any = None
        self._bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self._geographic = True

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load()
            self._loaded = True

    def _load(self) -> None:
        import rasterio
        from pyproj import CRS, Transformer
        from rasterio.errors import RasterioIOError
        from rasterio.warp import transform_bounds

        if not self.path.exists():
            raise DataPackError("layer_missing", f"Raster file not found: {self.path.name}", details={"file": self.path.name})
        try:
            with rasterio.open(self.path) as ds:
                if ds.count < 1:
                    raise DataPackError("datapack_invalid", f"Raster {self.path.name} has no bands")
                raw = ds.read(1)
                nodata = ds.nodata
                transform = ds.transform
                crs = ds.crs
                width, height = ds.width, ds.height
        except RasterioIOError as exc:
            # The rasterio message holds the absolute server path: log it, keep it out of the error.
            log.warning("Cannot read raster %s: %s", self.path, exc)
            raise DataPackError("datapack_invalid", f"The data-pack raster '{self.path.name}' could not be read.") from exc

        arr = raw.astype(np.float32)
        invalid = ~np.isfinite(arr)
        if nodata is not None and not (isinstance(nodata, float) and math.isnan(nodata)):
            invalid |= raw == nodata
        arr[invalid] = np.nan
        arr.setflags(write=False)

        if crs is None:
            log.warning("Raster %s has no CRS; assuming EPSG:4326", self.path.name)
            src_crs = CRS.from_epsg(4326)
        else:
            src_crs = CRS.from_user_input(crs.to_wkt())
        wgs84 = CRS.from_epsg(4326)
        self._geographic = bool(src_crs.is_geographic)
        if src_crs.equals(wgs84, ignore_axis_order=True):
            self._transformer = None
        else:
            self._transformer = Transformer.from_crs(wgs84, src_crs, always_xy=True)

        inv = ~transform
        self._inv = (inv.a, inv.b, inv.c, inv.d, inv.e, inv.f)
        self._transform = transform
        self._crs = src_crs
        left, top = transform.c, transform.f
        right = left + transform.a * width + transform.b * height
        bottom = top + transform.d * width + transform.e * height
        xs = [left, right]
        ys = [top, bottom]
        b = (min(xs), min(ys), max(xs), max(ys))
        if self._transformer is not None:
            b = tuple(transform_bounds(crs or "EPSG:4326", "EPSG:4326", *b, densify_pts=21))  # type: ignore[assignment]
        self._bounds = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
        # log.debug("raster %s: %dx%d px, bounds %s", self.path.name, width, height, self._bounds)
        self._array = arr

    def _pixel_area_km2(self) -> np.ndarray:
        """Area of every pixel in km², broadcastable to the array shape."""
        assert self._array is not None
        t = self._transform
        h, w = self._array.shape
        if not self._geographic:
            unit = 1.0
            try:
                unit = float(self._crs.axis_info[0].unit_conversion_factor)
            except (AttributeError, IndexError, TypeError):
                unit = 1.0
            area = abs(t.a * t.e - t.b * t.d) * unit * unit / 1e6
            return np.full((1, 1), area, dtype=np.float64)
        if t.b == 0 and t.d == 0:
            rows = np.arange(h, dtype=np.float64)
            lat_top = t.f + t.e * rows
            lat_bot = t.f + t.e * (rows + 1.0)
            return geographic_cell_area_km2(lat_top, lat_bot, t.a)[:, None]
        # Rotated geographic grid: local scale at each pixel centre.
        rows, cols = np.mgrid[0:h, 0:w].astype(np.float64)
        lat_c = t.d * (cols + 0.5) + t.e * (rows + 0.5) + t.f
        m_lon, m_lat = _metres_per_degree(lat_c)
        return np.abs(t.a * t.e - t.b * t.d) * m_lon * m_lat / 1e6

    def _density_array(self) -> np.ndarray:
        self._ensure_loaded()
        if self._density is None:
            with self._lock:
                if self._density is None:
                    assert self._array is not None
                    dens = (self._array.astype(np.float64) / self._pixel_area_km2()).astype(np.float32)
                    dens.setflags(write=False)
                    self._density = dens
        return self._density

    @property
    def array(self) -> np.ndarray:
        """The (read-only) pixel array, float32 with NaN for nodata."""
        self._ensure_loaded()
        assert self._array is not None
        return self._array

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """WGS84 extent ``(min_lon, min_lat, max_lon, max_lat)``."""
        self._ensure_loaded()
        return self._bounds

    @property
    def shape(self) -> tuple[int, int]:
        """``(rows, cols)``."""
        return tuple(self.array.shape)  # type: ignore[return-value]

    @property
    def transform(self) -> Any:
        """The affine geotransform (``affine.Affine``)."""
        self._ensure_loaded()
        return self._transform

    @property
    def resolution(self) -> tuple[float, float]:
        """Pixel size ``(x, y)`` in raster CRS units (degrees for data packs)."""
        t = self.transform
        return (math.hypot(t.a, t.d), math.hypot(t.b, t.e))

    def nominal_resolution_m(self) -> float:
        """Approximate pixel size in metres at the raster centre (the larger of x and y)."""
        rx, ry = self.resolution
        if not self._geographic:
            return max(rx, ry)
        lat_c = (self.bounds[1] + self.bounds[3]) / 2.0
        m_lon, m_lat = _metres_per_degree(np.asarray(lat_c))
        return float(max(rx * m_lon, ry * m_lat))

    def _sample_grid(self, grid: np.ndarray, lons: ArrayLike, lats: ArrayLike, method: str) -> np.ndarray:
        if method not in ("bilinear", "nearest"):
            raise AnalysisError("invalid_params", f"Unknown sampling method {method!r}; use 'bilinear' or 'nearest'")
        x_in, y_in = np.broadcast_arrays(np.asarray(lons, dtype=np.float64), np.asarray(lats, dtype=np.float64))
        shape = x_in.shape
        x = x_in.ravel()
        y = y_in.ravel()
        if self._transformer is not None and x.size:
            x, y = self._transformer.transform(x, y)
            x = np.asarray(x, dtype=np.float64)
            y = np.asarray(y, dtype=np.float64)
        ia, ib, ic, id_, ie, if_ = self._inv
        col = ia * x + ib * y + ic
        row = id_ * x + ie * y + if_
        h, w = grid.shape
        out = np.full(x.shape, np.nan, dtype=np.float64)
        with np.errstate(invalid="ignore"):
            inside = np.isfinite(col) & np.isfinite(row) & (col >= 0) & (col <= w) & (row >= 0) & (row <= h)
        if not inside.any() or h == 0 or w == 0:
            return out.reshape(shape)
        c = col[inside]
        r = row[inside]
        ci = np.clip(np.floor(c).astype(np.int64), 0, w - 1)
        ri = np.clip(np.floor(r).astype(np.int64), 0, h - 1)
        nearest = grid[ri, ci].astype(np.float64)
        if method == "nearest":
            out[inside] = nearest
            return out.reshape(shape)

        fc = np.clip(c - 0.5, 0.0, w - 1.0)
        fr = np.clip(r - 0.5, 0.0, h - 1.0)
        c0 = np.floor(fc).astype(np.int64)
        r0 = np.floor(fr).astype(np.int64)
        c1 = np.minimum(c0 + 1, w - 1)
        r1 = np.minimum(r0 + 1, h - 1)
        tx = fc - c0
        ty = fr - r0
        values = np.stack(
            [grid[r0, c0], grid[r0, c1], grid[r1, c0], grid[r1, c1]], axis=0
        ).astype(np.float64)
        weights = np.stack([(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty], axis=0)
        valid = np.isfinite(values)
        weights = np.where(valid, weights, 0.0)
        wsum = weights.sum(axis=0)
        num = np.where(valid, values * weights, 0.0).sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            result = np.where(wsum > 1e-12, num / np.where(wsum > 0, wsum, 1.0), np.nan)
        result = np.where(np.isfinite(nearest), result, np.nan)
        out[inside] = result
        return out.reshape(shape)

    def sample(self, lons: ArrayLike, lats: ArrayLike, method: SampleMethod = "bilinear") -> np.ndarray:
        """Pixel values at WGS84 points (NaN for nodata or outside). Shape follows the inputs."""
        return self._sample_grid(self.array, lons, lats, method)

    def density_per_km2(self, lons: ArrayLike, lats: ArrayLike, method: SampleMethod = "bilinear") -> np.ndarray:
        """Population density (people/km²) at WGS84 points, for a people-per-pixel raster."""
        return self._sample_grid(self._density_array(), lons, lats, method)

    def integrate_density(self, polygon_wgs84: BaseGeometry, step_m: float = 100.0, *, max_points: int = 250_000) -> float:
        """Estimated number of people inside a WGS84 polygon.

        Density is sampled on a regular grid (``geo.sampling.grid_points``) and multiplied by the
        grid cell area. Polygons smaller than one grid cell fall back to the density at a
        representative point times the polygon's geodesic area. NaN samples count as zero.
        Non-polygonal geometries contain nobody and return 0.
        """
        from ..geo.geodesic import geodesic_area_perimeter
        from ..geo.sampling import grid_points

        poly = _polygonal(polygon_wgs84)
        if poly is None:
            return 0.0
        if step_m <= 0:
            raise AnalysisError("invalid_params", "step_m must be positive")
        lons, lats, cell_area = grid_points(poly, step_m, max_points)
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        if lons.size == 0:
            rp = poly.representative_point()
            point_density = float(self.density_per_km2(rp.x, rp.y))
            if not math.isfinite(point_density):
                return 0.0
            area_m2, _ = geodesic_area_perimeter(poly)
            return max(0.0, point_density * float(area_m2) / 1e6)
        dens = self.density_per_km2(lons, lats)
        areas = np.broadcast_to(np.asarray(cell_area, dtype=np.float64), dens.shape)
        total = float(np.where(np.isfinite(dens), dens * areas, 0.0).sum()) / 1e6
        return max(0.0, total)

    def _sample_points_for(self, geom: BaseGeometry, step_m: float, max_points: int) -> tuple[np.ndarray, np.ndarray]:
        from ..geo.sampling import grid_points, sample_line

        poly = _polygonal(geom)
        xs: list[np.ndarray] = []
        ys: list[np.ndarray] = []
        if poly is not None:
            lons, lats, _ = grid_points(poly, step_m, max_points)
            xs.append(np.asarray(lons, dtype=np.float64))
            ys.append(np.asarray(lats, dtype=np.float64))
            if np.asarray(lons).size == 0:
                rp = poly.representative_point()
                xs.append(np.array([rp.x]))
                ys.append(np.array([rp.y]))
        for part in shapely.get_parts(geom):
            gt = part.geom_type
            if gt in ("LineString", "LinearRing", "MultiLineString"):
                for line in shapely.get_parts(part):
                    if line.is_empty:
                        continue
                    _, lons, lats = sample_line(line, step_m)
                    xs.append(np.asarray(lons, dtype=np.float64))
                    ys.append(np.asarray(lats, dtype=np.float64))
            elif gt in ("Point", "MultiPoint"):
                coords = shapely.get_coordinates(part)
                if coords.size:
                    xs.append(coords[:, 0])
                    ys.append(coords[:, 1])
        if not xs:
            return np.empty(0), np.empty(0)
        return np.concatenate(xs), np.concatenate(ys)

    def stats(self, polygon: BaseGeometry, step_m: float = 100.0, *, max_points: int = 250_000) -> dict[str, Any]:
        """Summary statistics of pixel values over a geometry.

        Polygons are sampled on a grid, lines every ``step_m`` and points directly.
        Returns ``{count, valid, min, max, mean, median, p95, std}``; statistics are ``None``
        when there is no valid sample.
        """
        if polygon is None or polygon.is_empty:
            lons = lats = np.empty(0)
        else:
            lons, lats = self._sample_points_for(polygon, step_m, max_points)
        values = self.sample(lons, lats) if lons.size else np.empty(0)
        finite = values[np.isfinite(values)]
        result: dict[str, Any] = {"count": int(values.size), "valid": int(finite.size)}
        if finite.size:
            result.update(
                min=float(np.min(finite)),
                max=float(np.max(finite)),
                mean=float(finite.mean()),
                median=float(np.median(finite)),
                p95=float(np.percentile(finite, 95)),
                std=float(finite.std()),
            )
        else:
            result.update(min=None, max=None, mean=None, median=None, p95=None, std=None)
        return result

    def slope_deg(self, lons: ArrayLike, lats: ArrayLike, delta_m: float = 30.0) -> np.ndarray:
        """Terrain slope in degrees from finite differences ``delta_m`` metres around each point."""
        from ..geo.geodesic import GEOD

        if delta_m <= 0:
            raise AnalysisError("invalid_params", "delta_m must be positive")
        x_in, y_in = np.broadcast_arrays(np.asarray(lons, dtype=np.float64), np.asarray(lats, dtype=np.float64))
        shape = x_in.shape
        x = x_in.ravel()
        y = y_in.ravel()
        if x.size == 0:
            return np.empty(shape, dtype=np.float64)
        dist = np.full(x.shape, float(delta_m))

        def offset(az: float) -> tuple[np.ndarray, np.ndarray]:
            lo, la, _ = GEOD.fwd(x, y, np.full(x.shape, az), dist)
            return np.asarray(lo, dtype=np.float64), np.asarray(la, dtype=np.float64)

        z_c = self.sample(x, y)
        z_e = self.sample(*offset(90.0))
        z_w = self.sample(*offset(270.0))
        z_n = self.sample(*offset(0.0))
        z_s = self.sample(*offset(180.0))

        def derivative(z_plus: np.ndarray, z_minus: np.ndarray) -> np.ndarray:
            both = np.isfinite(z_plus) & np.isfinite(z_minus)
            plus_only = np.isfinite(z_plus) & ~np.isfinite(z_minus) & np.isfinite(z_c)
            minus_only = np.isfinite(z_minus) & ~np.isfinite(z_plus) & np.isfinite(z_c)
            d = np.full(z_c.shape, np.nan)
            d[both] = (z_plus[both] - z_minus[both]) / (2.0 * delta_m)
            d[plus_only] = (z_plus[plus_only] - z_c[plus_only]) / delta_m
            d[minus_only] = (z_c[minus_only] - z_minus[minus_only]) / delta_m
            return d

        dzdx = derivative(z_e, z_w)
        dzdy = derivative(z_n, z_s)
        with np.errstate(invalid="ignore"):
            slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
        slope = np.where(np.isfinite(z_c), slope, np.nan)
        return slope.reshape(shape)

    def info(self) -> dict[str, Any]:
        """JSON-friendly description (name, kind, units, bounds, shape, resolution)."""
        rows, cols = self.shape
        return {
            "name": self.name,
            "kind": self.kind,
            "units": self.units,
            "bounds": list(self.bounds),
            "rows": rows,
            "cols": cols,
            "resolution": list(self.resolution),
            "resolution_m": round(self.nominal_resolution_m(), 2),
        }

    def __repr__(self) -> str:
        return f"RasterSampler(name={self.name!r}, path={str(self.path)!r}, loaded={self._loaded})"


__all__ = ["RasterSampler", "geographic_cell_area_km2"]
