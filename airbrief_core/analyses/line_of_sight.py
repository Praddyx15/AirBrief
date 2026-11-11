"""Line of sight: VLOS viewshed from pilots and observers, and C2 radio path with Fresnel clearance.

The module produces geometric *evidence* (fractions of the area that are visible, Fresnel clearance
along radio paths, free-space loss and a non-regulatory VLOS distance), never an approval.

Procedure
---------
1. **Grid.** The operation geometry and the observers are projected to a
   :class:`~airbrief_core.geo.frame.LocalFrame` (azimuthal equidistant). The grid covers their
   bounding box plus a margin of ``max(150 m, 3 cells)`` with square cells of ``grid_m``
   (default 30 m); it is coarsened (whole metres) so that neither side exceeds 400 cells (relief
   narrower than a coarsened cell is then smoothed away, which an issue says). Cell heights are
   the data-pack DEM sampled bilinearly at the cell centres. The DEM is a surface model of about
   30 m (roofs and canopy included). With ``include_obstacles`` every data-pack obstacle puts its
   top (DEM at the obstacle + ``height_m``) on its cell; several in one cell keep the tallest.
2. **Viewshed** (:func:`viewshed`, per observer). The eye stands the eye height (pilot and
   observer 1.7 m, ground station 2.0 m by default) above the gridded terrain at the observer.
   Heights are lowered by the Earth curvature and refraction drop ``d²/(2·k·R)`` with
   ``R = 6,371,000 m``: ``k = 1/(1 − 1/7) = 7/6`` for visible light (GDAL ``gdal_viewshed``
   default curvature coefficient 0.85714) and ``k = 4/3`` for radio (ground stations). A cell is
   visible when the slope from the eye to the aircraft (terrain + target height) is at least the
   steepest slope from the eye to any surface sample nearer than the cell's near edge. The
   horizon comes from a radial sweep: ``n ≥ 2π·r_max`` rays (neighbouring rays at most one cell
   apart at the far edge), sampled every half cell along the ray, keeping the running maximum
   slope (the "R2" approximation of Franklin & Ray, 1994). Along a ray the terrain is
   interpolated bilinearly between cell centres (a plane stays a plane, so a low eye on a slope
   is not buried by the cell steps) and obstacle tops are taken in the cell a sample falls in
   (the observer's own cell excluded: a mast it stands by is not an obstruction). Each cell takes
   the horizon of its nearest ray and is judged at the point of that ray at its own distance.
   Combined visibility = seen by at least one pilot or observer; ground stations give the radio
   line-of-sight coverage instead.
3. **Area statistics.** Area cells are the cells whose centre lies in a polygon, the cells a line
   passes through and the cells of points. Visible % = visible area cells / area cells with DEM
   data. Along a route the line is cut every half cell and each piece takes the visibility of its
   midpoint's cell, giving the visible length, the longest continuous visible stretch and the
   hidden stretches. The largest hidden patch is the biggest 8-connected group of hidden area
   cells.
4. **Radio** (:func:`fresnel_profile`). From the first ground station (else the pilot) to the
   ``radio_target``, else to every vertex of the route (at most 20, else evenly spaced points),
   else to the farthest corner of the area. The path is sampled on the DEM itself (not the grid)
   every ``min(s, max(5 m, D/400))`` with ``s`` the smaller of the grid cell and the DEM pixel;
   obstacles within ``s/2`` of the path add their top at their position along it. The LOS is the chord between the antennas lowered by the Earth bulge
   ``d1·d2/(2·k·R)`` (k = 4/3); the first Fresnel zone radius is
   ``r1 = 17.32·√(d1·d2/(f·D))`` m with distances in km and f in GHz; clearance % =
   ``(LOS − surface)/r1·100``. Stretches below ``fresnel_clearance_pct`` are flagged. Free-space
   path loss over the slant distance: ``FSPL = 20·log10(d_km) + 20·log10(f_MHz) + 32.44`` dB.
   Geometry only — not a link budget or coverage prediction.
5. **VLOS guidance** (:func:`vlos_guidance`), non-regulatory: with a characteristic dimension,
   EASA's ALOS = 327·CD + 20 m (multirotor) or 490·CD + 30 m (fixed-wing), DLOS = 0.3 × ground
   visibility, VLOS = min(ALOS, DLOS) (EASA Guidelines for UAS operations in the open and specific
   category, Issue 03, 17 July 2025, §I); otherwise the commonly cited UK CAA figure of 500 m.
   It is an air-line (slant) distance: the map ring is drawn at ``√(G² − h²)`` for the target
   height ``h`` over level ground, and ``beyond_guidance_pct`` uses the true slant distance.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring

from ..data.datapack import DataPack
from ..data.raster import RasterSampler
from ..errors import AnalysisError
from ..geo.frame import LocalFrame
from ..geo.geodesic import distance_m
from ..geo.sampling import sample_line
from ..geo.transform import geojson_to_geometry, geometry_to_geojson, unwrap_antimeridian
from ..geo.validity import drop_z, geometry_type_name, iter_parts, lineal_part, polygonal_part, repair
from ..models.common import Issue, Severity
from ..models.line_of_sight import (
    DEFAULT_HEIGHT_BY_ROLE,
    DISCLAIMER_LINE_OF_SIGHT,
    EARTH_RADIUS_M,
    RADIO_GEOMETRY_NOTE,
    UK_CAA_VLOS_M,
    LineOfSightParams,
    LineOfSightResult,
    LosBelowThreshold,
    LosGridInfo,
    LosHiddenPatch,
    LosObserver,
    LosProfileSample,
    LosRadioPath,
    LosRadioSummary,
    LosRouteSegment,
    LosRouteSummary,
    VlosGuidance,
)
from .base import AnalysisContext
from .heights import dem_info
from .terrain import obstacle_height

__all__ = [
    "EASA_VLOS_REFERENCE",
    "FRESNEL_CONSTANT",
    "FSPL_CONSTANT_DB",
    "MAX_EXTENT_M",
    "MAX_GRID_SIDE",
    "UK_CAA_REFERENCE",
    "curvature_drop_m",
    "fresnel_profile",
    "fresnel_radius_m",
    "fspl_db",
    "grid_bilinear",
    "run",
    "viewshed",
    "vlos_guidance",
]

MAX_GRID_SIDE = 400
"""Largest number of cells along either side of the grid (coarsened beyond)."""

MAX_EXTENT_M = 60_000.0
"""Largest grid side (m, margin included). Beyond it the inputs are refused as ``area_too_large``."""

GRID_MARGIN_M = 150.0
GRID_MARGIN_CELLS = 3

RAY_BLOCK = 256
"""Rays processed together in :func:`viewshed` (bounds the temporary arrays)."""

RAY_STEP_CELLS = 0.5
"""Sample spacing along a viewshed ray, in cells. Half a cell keeps a ray from stepping over the
corner of a one-cell obstacle (a whole-cell step misses about a third of the cells a diagonal ray
crosses)."""

RADIO_MIN_STEP_M = 5.0
"""Finest radio-profile sample spacing (m)."""

FRESNEL_CONSTANT = 17.32
"""r1 = 17.32·√(d1·d2/(f·D)) m with d in km and f in GHz (√(c/10⁶) ≈ 17.31, rounded as in ITU-R P.530)."""

FSPL_CONSTANT_DB = 32.44
"""FSPL = 20·log10(d_km) + 20·log10(f_MHz) + 32.44 dB (ITU-R P.525 free-space loss)."""

MAX_RADIO_TARGETS = 20
PROFILE_MAX_POINTS_SINGLE = 240
PROFILE_MAX_POINTS_MANY = 120
MAX_HIDDEN_SEGMENTS = 200
LAYER_DECIMALS = 7

EASA_ALOS: dict[str, tuple[float, float]] = {"multirotor": (327.0, 20.0), "fixed_wing": (490.0, 30.0)}
"""ALOS = a·CD + b metres, per aircraft type (EASA guidelines, §I)."""

EASA_DLOS_FACTOR = 0.3
"""DLOS = 0.3 × ground visibility (EASA guidelines, §I)."""

EASA_VLOS_REFERENCE = (
    "EASA, Guidelines for UAS operations in the open and specific category (Ref. to Regulation (EU) "
    "2019/947), Issue 03, 17 July 2025, §I 'Calculation of the VLOS distance': ALOS = 327·CD + 20 m "
    "(rotorcraft/multirotor) or 490·CD + 30 m (fixed-wing), DLOS = 0.3 × ground visibility (at least "
    "5 km), VLOS = the smaller of the two. Non-binding guidance."
)
UK_CAA_REFERENCE = (
    "UK CAA CAP 722 (commonly cited): VLOS is normally accepted out to 500 m horizontally from the "
    "remote pilot, provided the aircraft can still be seen. Guidance, not a limit."
)

_TWO_PI = 2.0 * math.pi


def curvature_drop_m(distance: float, k: float) -> float:
    """Apparent drop (m) of a point ``distance`` metres away: d²/(2·k·R), R = 6,371,000 m.

    ``k = inf`` means no curvature (0). At 10 km with k = 4/3 the drop is 5.89 m.
    """
    if not math.isfinite(k):
        return 0.0
    if k <= 0:
        raise AnalysisError("invalid_params", "The effective Earth-radius factor k must be positive", details={"k": k})
    d = float(distance)
    return d * d / (2.0 * k * EARTH_RADIUS_M)


def fresnel_radius_m(d1_m: float, d2_m: float, frequency_mhz: float) -> float:
    """First Fresnel zone radius (m) at ``d1_m`` from one end of a ``d1_m + d2_m`` path.

    r1 = 17.32·√(d1·d2/(f·D)) with d1, d2, D in km and f in GHz. 2.4 GHz, 1 km, midpoint: 5.59 m.
    """
    if frequency_mhz <= 0:
        raise AnalysisError("invalid_params", "The frequency must be positive", details={"frequency_mhz": frequency_mhz})
    d1 = max(0.0, float(d1_m)) / 1000.0
    d2 = max(0.0, float(d2_m)) / 1000.0
    total = d1 + d2
    if total <= 0.0:
        return 0.0
    return FRESNEL_CONSTANT * math.sqrt(d1 * d2 / ((frequency_mhz / 1000.0) * total))


def fspl_db(distance: float, frequency_mhz: float) -> float:
    """Free-space path loss (dB): 20·log10(d_km) + 20·log10(f_MHz) + 32.44. 2.4 GHz, 1 km: 100.04 dB."""
    if distance <= 0 or frequency_mhz <= 0:
        raise AnalysisError(
            "invalid_params",
            "Free-space loss needs a positive distance and frequency",
            details={"distance_m": distance, "frequency_mhz": frequency_mhz},
        )
    return 20.0 * math.log10(distance / 1000.0) + 20.0 * math.log10(frequency_mhz) + FSPL_CONSTANT_DB


def fresnel_profile(
    distances_m: np.ndarray,
    surface_m: np.ndarray,
    from_amsl_m: float,
    to_amsl_m: float,
    frequency_mhz: float,
    k: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Radio line, first Fresnel radius and clearance along a sampled path.

    ``distances_m`` increase from the first antenna; the last one is the path length D.
    ``surface_m`` is the terrain (or obstacle top) AMSL at each, NaN where unknown. ``k = inf``
    means no curvature.

    Returns ``(los_m, r1_m, clearance_pct)``: the chord between the antennas lowered by the Earth
    bulge d1·d2/(2·k·R); r1 = 17.32·√(d1·d2/(f·D)) m (km, GHz); clearance % =
    (LOS − surface)/r1·100, NaN where r1 = 0 (the ends) or the surface is unknown.
    """
    d = np.asarray(distances_m, dtype=np.float64)
    surface = np.asarray(surface_m, dtype=np.float64)
    if d.ndim != 1 or d.size < 2 or surface.shape != d.shape:
        raise AnalysisError("invalid_params", "A path profile needs at least two samples with a surface height each")
    if frequency_mhz <= 0:
        raise AnalysisError("invalid_params", "The frequency must be positive", details={"frequency_mhz": frequency_mhz})
    total = float(d[-1])
    if total <= 0.0:
        raise AnalysisError("invalid_params", "A path profile needs a positive length")
    d2 = np.maximum(total - d, 0.0)
    d1 = np.maximum(d, 0.0)
    chord = from_amsl_m + (to_amsl_m - from_amsl_m) * d1 / total
    bulge = d1 * d2 / (2.0 * k * EARTH_RADIUS_M) if math.isfinite(k) else np.zeros_like(d1)
    los = chord - bulge
    r1 = FRESNEL_CONSTANT * np.sqrt((d1 / 1000.0) * (d2 / 1000.0) / ((frequency_mhz / 1000.0) * (total / 1000.0)))
    with np.errstate(divide="ignore", invalid="ignore"):
        clearance = np.where((r1 > 1e-9) & np.isfinite(surface), (los - surface) / r1 * 100.0, np.nan)
    return los, r1, clearance


def vlos_guidance(
    aircraft: str,
    characteristic_dimension_m: float | None,
    ground_visibility_m: float,
    target_height_m: float,
) -> VlosGuidance:
    """Non-regulatory VLOS distance guidance (see the module docstring, step 5)."""
    if characteristic_dimension_m is not None:
        a, b = EASA_ALOS.get(aircraft, EASA_ALOS["multirotor"])
        alos = a * float(characteristic_dimension_m) + b
        dlos = EASA_DLOS_FACTOR * float(ground_visibility_m)
        guidance = min(alos, dlos)
        basis = "easa_alos_dlos"
        reference = EASA_VLOS_REFERENCE
        alos_m: float | None = round(alos, 1)
        dlos_m: float | None = round(dlos, 1)
    else:
        guidance = UK_CAA_VLOS_M
        basis = "uk_caa_500m"
        reference = UK_CAA_REFERENCE
        alos_m = dlos_m = None
    ring = math.sqrt(max(guidance * guidance - float(target_height_m) ** 2, 0.0))
    return VlosGuidance(
        guidance_m=round(guidance, 1),
        basis=basis,  # type: ignore[arg-type]
        aircraft=aircraft,  # type: ignore[arg-type]
        characteristic_dimension_m=characteristic_dimension_m,
        alos_m=alos_m,
        dlos_m=dlos_m,
        ground_visibility_m=float(ground_visibility_m),
        ring_radius_m=round(ring, 1),
        reference=reference,
    )


def grid_bilinear(values: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Bilinear interpolation of a cell-centred grid at positions in cell units.

    Cell ``(r, c)`` holds the value at ``(c + 0.5, r + 0.5)``; positions beyond the outer centres
    take the edge values. NaN cells are left out of the weights (NaN only when all four are NaN).
    """
    z = np.asarray(values, dtype=np.float64)
    rows, cols = z.shape
    fc = np.clip(np.asarray(xs, dtype=np.float64) - 0.5, 0.0, cols - 1.0)
    fr = np.clip(np.asarray(ys, dtype=np.float64) - 0.5, 0.0, rows - 1.0)
    c0 = np.floor(fc).astype(np.int64)
    r0 = np.floor(fr).astype(np.int64)
    c1 = np.minimum(c0 + 1, cols - 1)
    r1 = np.minimum(r0 + 1, rows - 1)
    tx = fc - c0
    ty = fr - r0
    if np.isfinite(z).all():
        top = z[r0, c0] * (1 - tx) + z[r0, c1] * tx
        bottom = z[r1, c0] * (1 - tx) + z[r1, c1] * tx
        return np.asarray(top * (1 - ty) + bottom * ty, dtype=np.float64)
    total = np.zeros(fc.shape, dtype=np.float64)
    weight = np.zeros(fc.shape, dtype=np.float64)
    for rr, cc, w in ((r0, c0, (1 - tx) * (1 - ty)), (r0, c1, tx * (1 - ty)), (r1, c0, (1 - tx) * ty), (r1, c1, tx * ty)):
        v = z[rr, cc]
        ok = np.isfinite(v)
        total += np.where(ok, v * w, 0.0)
        weight += np.where(ok, w, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(weight > 1e-12, total / np.where(weight > 0.0, weight, 1.0), np.nan)


def viewshed(
    terrain_m: np.ndarray,
    cell_m: float,
    observer_xy: tuple[float, float],
    eye_amsl_m: float,
    target_agl_m: float,
    k: float,
    *,
    obstacles_m: np.ndarray | None = None,
    max_range_m: float | None = None,
    check: Callable[[], None] | None = None,
) -> np.ndarray:
    """Cells of a terrain grid above which a target at ``target_agl_m`` can be seen from one eye.

    Coordinates are in cell units: x along columns, y along rows; cell ``(r, c)`` has its centre at
    ``(c + 0.5, r + 0.5)`` and ``observer_xy`` uses the same units (any position, not only cell
    centres). Heights are metres AMSL with NaN for no data. The method is step 2 of the module
    docstring (Franklin & Ray's R2 sweep).

    ``obstacles_m`` has the terrain's shape, NaN where a cell has none: obstacles block the view
    but do not lift the target. ``k = inf`` means no curvature drop, ``max_range_m`` is a
    horizontal distance and ``check`` runs between ray blocks so the caller can cancel.

    Returns the boolean mask of visible cells (False where the terrain has no data).
    """
    z = np.asarray(terrain_m, dtype=np.float64)
    if z.ndim != 2 or z.size == 0:
        raise AnalysisError("invalid_params", "The viewshed needs a non-empty 2-D terrain grid")
    if not (cell_m > 0 and math.isfinite(cell_m)):
        raise AnalysisError("invalid_params", "The cell size must be a positive number of metres")
    if math.isfinite(k) and k <= 0:
        raise AnalysisError("invalid_params", "The effective Earth-radius factor k must be positive", details={"k": k})
    tops = None if obstacles_m is None else np.asarray(obstacles_m, dtype=np.float64)
    if tops is not None and tops.shape != z.shape:
        raise AnalysisError("invalid_params", "The obstacle grid must have the shape of the terrain grid")
    rows, cols = z.shape
    ox, oy = float(observer_xy[0]), float(observer_xy[1])
    own_c, own_r = math.floor(ox), math.floor(oy)
    curvature = 1.0 / (2.0 * k * EARTH_RADIUS_M) if math.isfinite(k) else 0.0

    corner_x = np.array([0.5, cols - 0.5]) - ox
    corner_y = np.array([0.5, rows - 0.5]) - oy
    r_max = float(np.max(np.hypot(corner_x[:, None], corner_y[None, :])))
    n_samples = max(1, int(math.ceil(r_max / RAY_STEP_CELLS)) + 1)
    n_rays = max(64, 8 * int(math.ceil(_TWO_PI * max(r_max, 1.0) / 8.0)))
    steps = np.arange(1, n_samples + 1, dtype=np.float64) * RAY_STEP_CELLS
    dist = steps * cell_m
    drop = dist * dist * curvature

    # Each cell: its distance, its nearest ray and the samples before its near edge, i.e. before
    # r − h with h = 0.5 / max(|cos θ|, |sin θ|) (half the cell's width along the ray).
    dx = (np.arange(cols, dtype=np.float64) + 0.5 - ox)[None, :]
    dy = (np.arange(rows, dtype=np.float64) + 0.5 - oy)[:, None]
    r = np.hypot(dx, dy)
    theta_c = np.mod(np.arctan2(np.broadcast_to(dy, r.shape), np.broadcast_to(dx, r.shape)), _TWO_PI)
    half = 0.5 / np.maximum(np.abs(np.cos(theta_c)), np.abs(np.sin(theta_c)))
    ray = (np.rint(theta_c * (n_rays / _TWO_PI)).astype(np.int64) % n_rays).ravel()
    before = (np.ceil((r - half) / RAY_STEP_CELLS - 1e-9).astype(np.int64) - 1).ravel()
    blocked_by = np.full(r.size, np.nan)
    order = np.argsort(ray, kind="stable")
    bounds = np.searchsorted(ray[order], np.arange(0, n_rays + RAY_BLOCK, RAY_BLOCK))

    for block, start in enumerate(range(0, n_rays, RAY_BLOCK)):
        if check is not None:
            check()
        stop = min(n_rays, start + RAY_BLOCK)
        theta = (_TWO_PI / n_rays) * np.arange(start, stop, dtype=np.float64)
        px = ox + np.cos(theta)[:, None] * steps[None, :]
        py = oy + np.sin(theta)[:, None] * steps[None, :]
        ci = np.floor(px).astype(np.int64)
        ri = np.floor(py).astype(np.int64)
        inside = (ci >= 0) & (ci < cols) & (ri >= 0) & (ri < rows)
        zs = np.full(px.shape, np.nan)
        zs[inside] = grid_bilinear(z, px[inside], py[inside])
        if tops is not None:
            on = inside & ~((ci == own_c) & (ri == own_r))
            zs[on] = np.fmax(zs[on], tops[ri[on], ci[on]])
        slope = (zs - drop[None, :] - eye_amsl_m) / dist[None, :]
        # fmax, not maximum: it skips NaN (no data, outside the grid) where maximum would propogate it.
        horizon = np.fmax.accumulate(slope, axis=1)
        cells = order[bounds[block] : bounds[block + 1]]
        cells = cells[before[cells] >= 1]
        blocked_by[cells] = horizon[ray[cells] - start, before[cells] - 1]

    # The target: terrain + target height at the point of the cell's ray at the cell's distance.
    theta_r = ray.reshape(r.shape) * (_TWO_PI / n_rays)
    base = grid_bilinear(z, ox + np.cos(theta_r) * r, oy + np.sin(theta_r) * r)
    dist_c = r * cell_m
    blocked = blocked_by.reshape(r.shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        target = (base + target_agl_m - dist_c * dist_c * curvature - eye_amsl_m) / dist_c
        visible = np.isfinite(z) & np.isfinite(base) & (~np.isfinite(blocked) | (target >= blocked - 1e-12))
    if max_range_m is not None:
        visible &= dist_c <= float(max_range_m)
    return np.asarray(visible, dtype=bool)


@dataclass
class _Grid:
    """Square cells in the local frame; row 0 is the southern row (y grows north)."""

    frame: LocalFrame
    x0: float
    y0: float
    cell_m: float
    cols: int
    rows: int
    ground: np.ndarray  # DEM at the cell centres (m AMSL), NaN = no data
    obstacles: np.ndarray  # tallest obstacle top per cell (m AMSL), NaN = none

    def cell_units(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.x0) / self.cell_m, (y - self.y0) / self.cell_m

    def cell_index(self, xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        ci = np.floor((np.asarray(xs, dtype=np.float64) - self.x0) / self.cell_m).astype(np.int64)
        ri = np.floor((np.asarray(ys, dtype=np.float64) - self.y0) / self.cell_m).astype(np.int64)
        inside = (ci >= 0) & (ci < self.cols) & (ri >= 0) & (ri < self.rows)
        return ri, ci, inside

    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        xs = self.x0 + (np.arange(self.cols, dtype=np.float64) + 0.5) * self.cell_m
        ys = self.y0 + (np.arange(self.rows, dtype=np.float64) + 0.5) * self.cell_m
        return np.meshgrid(xs, ys)


def _grid_layout(
    bounds: tuple[float, float, float, float], requested_m: float
) -> tuple[float, float, float, int, int, bool]:
    """``(x0, y0, cell_m, cols, rows, coarsened)`` for local ``bounds`` (see step 1)."""
    minx, miny, maxx, maxy = bounds
    width = maxx - minx
    height = maxy - miny
    cell = float(requested_m)

    def sides(c: float) -> tuple[float, int, int]:
        margin = max(GRID_MARGIN_M, GRID_MARGIN_CELLS * c)
        return margin, int(math.ceil((width + 2.0 * margin) / c - 1e-9)), int(math.ceil((height + 2.0 * margin) / c - 1e-9))

    margin, cols, rows = sides(cell)
    extent = max(width, height) + 2.0 * margin
    if extent > MAX_EXTENT_M:
        # The extent as compared: the inputs plus the grid margin on both sides (rounded up, so the
        # text never reads as within the limit).
        raise AnalysisError(
            "area_too_large",
            f"The operation and the observers span {math.ceil(extent / 100.0) / 10.0:.1f} km with the "
            f"{margin:.0f} m grid margin; "
            f"line of sight is computed over at most {MAX_EXTENT_M / 1000.0:.0f} km. Check the observer positions "
            "or split the area.",
            details={"extent_m": round(extent, 1), "max_extent_m": MAX_EXTENT_M},
        )
    coarsened = False
    if max(cols, rows) > MAX_GRID_SIDE:
        coarsened = True
        cell = float(math.ceil((max(width, height) + 2.0 * margin) / MAX_GRID_SIDE))
        margin, cols, rows = sides(cell)
        while max(cols, rows) > MAX_GRID_SIDE:
            cell += 1.0
            margin, cols, rows = sides(cell)
    return minx - margin, miny - margin, cell, max(cols, 1), max(rows, 1), coarsened


def _raise_obstacles(grid: _Grid, pack: DataPack, dem: RasterSampler) -> int:
    """Put each data-pack obstacle's top on its cell (``grid.obstacles``); returns the number used."""
    index = pack.obstacles()
    if not index:
        return 0
    xs_c = np.array([grid.x0, grid.x0 + grid.cols * grid.cell_m, grid.x0, grid.x0 + grid.cols * grid.cell_m])
    ys_c = np.array([grid.y0, grid.y0, grid.y0 + grid.rows * grid.cell_m, grid.y0 + grid.rows * grid.cell_m])
    lons_c, lats_c = grid.frame.lonlat(xs_c, ys_c)
    pad = 0.001
    candidates = (
        (index.lons >= lons_c.min() - pad)
        & (index.lons <= lons_c.max() + pad)
        & (index.lats >= lats_c.min() - pad)
        & (index.lats <= lats_c.max() + pad)
    )
    idx = np.nonzero(candidates)[0]
    if idx.size == 0:
        return 0
    heights = np.array([obstacle_height(index.features[int(i)].props) or np.nan for i in idx], dtype=np.float64)
    lons = index.lons[idx]
    lats = index.lats[idx]
    xs, ys = grid.frame.xy(lons, lats)
    ri, ci, inside = grid.cell_index(xs, ys)
    bases = np.asarray(dem.sample(lons, lats), dtype=np.float64)
    tops = bases + heights
    use = inside & np.isfinite(tops)
    if not np.any(use):
        return 0
    flat = grid.obstacles.reshape(-1).copy()
    # fmax over a NaN start: the first obstacle sets the cell, several obstacles keep the tallest.
    np.fmax.at(flat, ri[use] * grid.cols + ci[use], tops[use])
    grid.obstacles = flat.reshape(grid.rows, grid.cols)
    return int(np.count_nonzero(use))


def _area_mask(grid: _Grid, local: BaseGeometry) -> np.ndarray:
    """Area cells: centres inside polygons, cells crossed by lines, cells of points."""
    mask = np.zeros((grid.rows, grid.cols), dtype=bool)
    poly = polygonal_part(local)
    if poly is not None:
        shapely.prepare(poly)
        xs, ys = grid.centres()
        inside = np.asarray(shapely.contains_xy(poly, xs, ys), dtype=bool)
        if not inside.any():
            rp = poly.representative_point()
            ri, ci, ok = grid.cell_index(np.array([rp.x]), np.array([rp.y]))
            if ok[0]:
                inside[ri[0], ci[0]] = True
        mask |= inside
    lines = lineal_part(local)
    if lines is not None:
        for part in iter_parts(lines):
            length = float(part.length)
            n = max(1, int(math.ceil(length / (grid.cell_m / 4.0))))
            pts = shapely.line_interpolate_point(part, np.linspace(0.0, length, n + 1))
            coords = shapely.get_coordinates(pts)
            ri, ci, ok = grid.cell_index(coords[:, 0], coords[:, 1])
            mask[ri[ok], ci[ok]] = True
    for part in iter_parts(local):
        if part.geom_type == "Point":
            ri, ci, ok = grid.cell_index(np.array([part.x]), np.array([part.y]))
            if ok[0]:
                mask[ri[0], ci[0]] = True
    return mask


@dataclass
class _Viewpoint:
    name: str
    role: str
    lon: float
    lat: float
    height_m: float
    assumed: bool
    x: float = 0.0
    y: float = 0.0
    ground_m: float = math.nan  # DEM at the position (reported; radio paths)
    grid_ground_m: float = math.nan  # gridded terrain at the position (viewshed)

    @property
    def eye_m(self) -> float:
        return self.ground_m + self.height_m

    @property
    def view_eye_m(self) -> float:
        """Eye over the gridded terrain the viewshed sees, so a coarse grid never buries it."""
        ground = self.grid_ground_m if math.isfinite(self.grid_ground_m) else self.ground_m
        return ground + self.height_m

    @property
    def visual(self) -> bool:
        return self.role in ("pilot", "observer")


def _assumed_position(geom: BaseGeometry) -> tuple[float, float, str]:
    lines = lineal_part(geom)
    if lines is not None:
        first = next(iter_parts(lines))
        x, y = first.coords[0][:2]
        return float(x), float(y), "the start of the route"
    poly = polygonal_part(geom)
    if poly is not None:
        # Across the antimeridian the plain lon/lat centroid lands near 0°: unwrap first.
        unwrapped = unwrap_antimeridian(poly)
        c = unwrapped.centroid
        if c.is_empty or not unwrapped.contains(c):
            c = unwrapped.representative_point()
        x = float(c.x)
        return (x - 360.0 if x > 180.0 else x), float(c.y), "the centre of the area"
    p = next(iter_parts(geom))
    return float(p.coords[0][0]), float(p.coords[0][1]), "the first point"


def _viewpoints(params: LineOfSightParams, geom: BaseGeometry, issues: list[Issue]) -> list[_Viewpoint]:
    points = [
        _Viewpoint(o.name, o.role, float(o.lon), float(o.lat), o.eye_height_m(), False) for o in params.observers
    ]
    if not points:
        lon, lat, where = _assumed_position(geom)
        points.append(_Viewpoint("Pilot", "pilot", lon, lat, DEFAULT_HEIGHT_BY_ROLE["pilot"], True))
        issues.append(Issue(
            code="observer_assumed",
            message=(
                f"No pilot or observer was given: the pilot is assumed at {where} ({lat:.5f}, {lon:.5f}) "
                f"with a {DEFAULT_HEIGHT_BY_ROLE['pilot']:g} m eye height. Place the real positions for "
                "meaningful evidence."
            ),
            severity=Severity.WARNING,
        ))
    elif not any(p.visual for p in points):
        station = points[0]
        points.insert(0, _Viewpoint(
            f"Pilot at {station.name}"[:80], "pilot", station.lon, station.lat, DEFAULT_HEIGHT_BY_ROLE["pilot"], True
        ))
        issues.append(Issue(
            code="pilot_assumed_at_gcs",
            message=(
                f"Only ground stations were given: the pilot is assumed to stand at '{station.name}' "
                f"with a {DEFAULT_HEIGHT_BY_ROLE['pilot']:g} m eye height."
            ),
            severity=Severity.INFO,
        ))
    return points


def _feature(geom: BaseGeometry, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry_to_geojson(geom, LAYER_DECIMALS), "properties": props}


def _collection(features: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


def _to_wgs84(grid: _Grid, geom: BaseGeometry) -> BaseGeometry:
    out = grid.frame.to_wgs84(geom)
    if out.geom_type in ("Polygon", "MultiPolygon"):
        out = shapely.orient_polygons(out)
    return out


def _polygons_by_value(grid: _Grid, values: np.ndarray, valid: np.ndarray) -> dict[int, BaseGeometry]:
    """Merged cell polygons (local metres) per integer value of ``values`` where ``valid``."""
    from rasterio import features as rio_features
    from rasterio.transform import Affine

    if not np.any(valid):
        return {}
    transform = Affine(grid.cell_m, 0.0, grid.x0, 0.0, grid.cell_m, grid.y0)
    parts: dict[int, list[BaseGeometry]] = {}
    source = np.ascontiguousarray(values.astype(np.uint8))
    for geom, value in rio_features.shapes(source, mask=np.ascontiguousarray(valid), connectivity=4, transform=transform):
        parts.setdefault(int(value), []).append(shape(geom))
    tolerance = grid.cell_m * 1e-6  # drops collinear vertices along cell edges only
    return {v: shapely.simplify(shapely.union_all(p), tolerance) for v, p in parts.items()}


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """``(start, stop)`` index pairs of the runs of True in a 1-D boolean array (stop exclusive)."""
    m = np.asarray(mask, dtype=np.int8)
    if m.size == 0:
        return []
    edges = np.diff(np.concatenate([[0], m, [0]]))
    starts = np.nonzero(edges == 1)[0]
    stops = np.nonzero(edges == -1)[0]
    return list(zip(starts.tolist(), stops.tolist(), strict=True))


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _parse_geometry(obj: dict[str, Any]) -> BaseGeometry:
    geom = geojson_to_geometry(obj)
    if geom.is_empty:
        raise AnalysisError("invalid_geometry", "The operation geometry is empty")
    geom = drop_z(geom)
    geom, _ = repair(geom)
    if geom is None or geom.is_empty:
        raise AnalysisError("invalid_geometry", "The operation geometry is empty after repair")
    return geom


@dataclass
class _RouteResult:
    summary: LosRouteSummary
    segments: list[tuple[BaseGeometry, LosRouteSegment]]


def _route(grid: _Grid, lines_local: BaseGeometry, visible: np.ndarray) -> _RouteResult:
    """Visibility along the route, cut every half cell (see step 3)."""
    offset = 0.0
    pieces: list[tuple[BaseGeometry, LosRouteSegment]] = []
    total = visible_len = longest = 0.0
    for part in iter_parts(lines_local):
        length = float(part.length)
        if length <= 0.0:
            continue
        n = max(1, int(math.ceil(length / (grid.cell_m / 2.0))))
        edges = np.linspace(0.0, length, n + 1)
        mids = (edges[:-1] + edges[1:]) / 2.0
        coords = shapely.get_coordinates(shapely.line_interpolate_point(part, mids))
        ri, ci, ok = grid.cell_index(coords[:, 0], coords[:, 1])
        seen = np.zeros(n, dtype=bool)
        seen[ok] = visible[ri[ok], ci[ok]]
        for flag in (True, False):
            for a, b in _runs(seen if flag else ~seen):
                start, stop = float(edges[a]), float(edges[b])
                seg = LosRouteSegment(
                    start_m=round(offset + start, 1),
                    end_m=round(offset + stop, 1),
                    length_m=round(stop - start, 1),
                    visible=flag,
                )
                pieces.append((substring(part, start, stop), seg))
                if flag:
                    visible_len += stop - start
                    longest = max(longest, stop - start)
        total += length
        offset += length
    pieces.sort(key=lambda item: item[1].start_m)
    hidden = [seg for _, seg in pieces if not seg.visible]
    summary = LosRouteSummary(
        length_m=round(total, 1),
        visible_length_m=round(visible_len, 1),
        visible_pct=round(100.0 * visible_len / total, 1) if total > 0 else 0.0,
        max_continuous_visible_m=round(longest, 1),
        hidden_segments=hidden[:MAX_HIDDEN_SEGMENTS],
        hidden_segments_truncated=len(hidden) > MAX_HIDDEN_SEGMENTS,
    )
    return _RouteResult(summary=summary, segments=pieces)


@dataclass
class _RadioEnd:
    name: str
    lon: float
    lat: float
    height_m: float


def _radio_targets(params: LineOfSightParams, geom: BaseGeometry, frame: LocalFrame, station: _Viewpoint) -> list[_RadioEnd]:
    """The far ends of the radio paths (see step 4)."""
    target_h = float(params.target_height_agl_m)
    if params.radio_target is not None:
        rt = params.radio_target
        height = float(rt.height_agl_m) if rt.height_agl_m is not None else target_h
        return [_RadioEnd(rt.name, float(rt.lon), float(rt.lat), height)]
    ends: list[_RadioEnd] = []
    lines = lineal_part(geom)
    if lines is not None:
        vertices = [c[:2] for part in iter_parts(lines) for c in part.coords]
        unique: list[tuple[float, float]] = []
        for x, y in vertices:
            if not unique or (abs(unique[-1][0] - x) > 1e-12 or abs(unique[-1][1] - y) > 1e-12):
                unique.append((float(x), float(y)))
        if len(unique) > MAX_RADIO_TARGETS:
            total = float(sum(float(distance_m(*a, *b)) for a, b in zip(unique[:-1], unique[1:], strict=True)))
            _, lons, lats = sample_line(lines, max(total / (MAX_RADIO_TARGETS - 1), 1.0))
            unique = list(zip(lons.tolist(), lats.tolist(), strict=True))[:MAX_RADIO_TARGETS]
        for i, (lon, lat) in enumerate(unique, start=1):
            ends.append(_RadioEnd(f"Route point {i}", lon, lat, target_h))
    poly = polygonal_part(geom)
    if poly is not None:
        corners = np.asarray([c[:2] for part in iter_parts(poly) for c in part.exterior.coords], dtype=np.float64)
        xs, ys = frame.xy(corners[:, 0], corners[:, 1])
        far = int(np.argmax(np.hypot(xs - station.x, ys - station.y)))
        ends.append(_RadioEnd("Farthest corner", float(corners[far, 0]), float(corners[far, 1]), target_h))
    if lines is None and poly is None:
        pts = [p for p in iter_parts(geom) if p.geom_type == "Point"]
        for i, p in enumerate(pts[:MAX_RADIO_TARGETS], start=1):
            ends.append(_RadioEnd(f"Point {i}", float(p.x), float(p.y), target_h))
    return ends[:MAX_RADIO_TARGETS]


def _keep_indices(n: int, max_points: int, keep: Iterable[int]) -> np.ndarray:
    base = np.unique(np.rint(np.linspace(0, n - 1, min(n, max_points))).astype(np.int64))
    extra = np.array([i for i in keep if 0 <= i < n], dtype=np.int64)
    return np.unique(np.concatenate([base, extra]))


def _radio_path(
    station: _Viewpoint,
    end: _RadioEnd,
    *,
    dem: RasterSampler,
    pack: DataPack,
    frame: LocalFrame,
    params: LineOfSightParams,
    corridor_m: float,
    spacing_cap_m: float,
    max_points: int,
) -> LosRadioPath | None:
    """One radio path profile, or None when an end is outside the DEM or the path is too short."""
    ground_b = float(np.asarray(dem.sample(end.lon, end.lat)))
    if not math.isfinite(ground_b):
        return None
    length = float(distance_m(station.lon, station.lat, end.lon, end.lat))
    if length < 1.0:
        return None
    spacing = min(spacing_cap_m, max(RADIO_MIN_STEP_M, length / 400.0))
    line = LineString([(station.lon, station.lat), (end.lon, end.lat)])
    dists, lons, lats = sample_line(line, spacing)
    terrain = np.asarray(dem.sample(lons, lats), dtype=np.float64)
    obstacle = np.full(dists.shape, np.nan)
    used = 0
    if params.include_obstacles and pack.has("obstacles"):
        near = pack.obstacles().near_geometry(line, corridor_m)
        if near:
            ax, ay = frame.xy(np.array([station.lon, end.lon]), np.array([station.lat, end.lat]))
            vx, vy = float(ax[1] - ax[0]), float(ay[1] - ay[0])
            seg2 = vx * vx + vy * vy
            n = dists.size
            for feat, _dist in near:
                h = obstacle_height(feat.props)
                if h is None or seg2 <= 0:
                    continue
                base = float(np.asarray(dem.sample(feat.lon, feat.lat)))
                if not math.isfinite(base):
                    continue
                px, py = frame.xy(np.array([feat.lon]), np.array([feat.lat]))
                t = ((float(px[0]) - float(ax[0])) * vx + (float(py[0]) - float(ay[0])) * vy) / seg2
                i = int(round(min(max(t, 0.0), 1.0) * (n - 1)))
                if i <= 0 or i >= n - 1:
                    continue  # an obstacle at an antenna is its mast, not an obstruction
                obstacle[i] = base + h if not math.isfinite(obstacle[i]) else max(obstacle[i], base + h)
                used += 1
    surface = np.fmax(terrain, obstacle)
    za = station.eye_m
    zb = ground_b + end.height_m
    los, r1, clearance = fresnel_profile(dists, surface, za, zb, params.frequency_mhz, params.k_radio)

    finite = np.isfinite(clearance)
    worst_pct: float | None = None
    worst_at: float | None = None
    worst_i = -1
    segments: list[LosBelowThreshold] = []
    if np.any(finite):
        worst_i = int(np.nanargmin(np.where(finite, clearance, np.nan)))
        worst_pct = float(clearance[worst_i])
        worst_at = float(dists[worst_i])
        below = finite & (clearance < params.fresnel_clearance_pct)
        for a, b in _runs(below):
            segments.append(LosBelowThreshold(
                start_m=round(float(dists[a]), 1),
                end_m=round(float(dists[b - 1]), 1),
                min_clearance_pct=round(float(np.min(clearance[a:b])), 1),
            ))
    keep = [worst_i, *np.nonzero(np.isfinite(obstacle))[0].tolist()]
    for seg_start, seg_stop in _runs(np.isfinite(clearance) & (clearance < params.fresnel_clearance_pct)):
        keep += [seg_start, seg_stop - 1]
    profile = [
        LosProfileSample(
            d_m=round(float(dists[i]), 1),
            terrain_m=_round(float(terrain[i])),
            obstacle_m=_round(float(obstacle[i])),
            los_m=round(float(los[i]), 2),
            fresnel_lower_m=round(float(los[i] - r1[i]), 2),
            fresnel_radius_m=round(float(r1[i]), 2),
            clearance_pct=_round(float(clearance[i]), 1),
        )
        for i in _keep_indices(dists.size, max_points, keep).tolist()
    ]
    slant = math.hypot(length, zb - za)
    return LosRadioPath(
        from_name=station.name,
        to_name=end.name,
        from_lon=round(station.lon, 8),
        from_lat=round(station.lat, 8),
        to_lon=round(end.lon, 8),
        to_lat=round(end.lat, 8),
        from_height_agl_m=round(station.height_m, 2),
        to_height_agl_m=round(end.height_m, 2),
        from_amsl_m=round(za, 2),
        to_amsl_m=round(zb, 2),
        distance_m=round(length, 1),
        slant_m=round(slant, 1),
        fspl_db=round(fspl_db(slant, params.frequency_mhz), 2),
        worst_clearance_pct=_round(worst_pct, 1),
        worst_at_m=_round(worst_at, 1),
        max_fresnel_radius_m=round(float(np.max(r1)), 2),
        los_blocked=worst_pct is not None and worst_pct < 0.0,
        below_threshold=worst_pct is not None and worst_pct < params.fresnel_clearance_pct,
        segments_below=segments,
        obstacles_on_path=used,
        nodata_samples=int(np.count_nonzero(~np.isfinite(terrain))),
        profile=profile,
    )


def _radio_band(path: LosRadioPath) -> str:
    if path.worst_clearance_pct is None:
        return "unknown"
    if path.los_blocked:
        return "blocked"
    return "below" if path.below_threshold else "clear"


def _method_text(
    params: LineOfSightParams,
    info: LosGridInfo,
    guidance: VlosGuidance,
    radio: LosRadioSummary | None,
    radio_step_m: float,
) -> str:
    coarse = f" (coarsened from {info.requested_grid_m:g} m to stay within {MAX_GRID_SIDE} × {MAX_GRID_SIDE} cells)" if info.coarsened else ""
    resolution = f"about {info.dem_resolution_m:.0f} m, " if info.dem_resolution_m else ""
    obstacles = (
        f" {info.obstacles_used} data-pack obstacles put their tops on their cells."
        if info.obstacles_used
        else ""
    )
    text = (
        f"The data-pack DEM ({info.dem_source}; {resolution}a surface model with roofs and canopy) was resampled "
        f"bilinearly onto a {info.cols} × {info.rows} grid of {info.grid_m:g} m cells{coarse} in a local "
        f"azimuthal-equidistant frame covering the operation and the observers.{obstacles} A cell counts as "
        f"visible from an observer when the slope from the eye (eye height above the gridded terrain) to the "
        f"aircraft {params.target_height_agl_m:g} m above the terrain is at least the steepest slope to the surface "
        "before the cell. Heights are lowered by the Earth curvature and refraction drop d²/(2·k·R), R = 6,371,000 m, "
        f"with k = {params.k_visible:.4g} for pilots and observers (refraction coefficient 1/7, GDAL's default "
        f"curvature coefficient 0.85714) and k = {params.k_radio:.4g} for ground stations. The horizon in each "
        "direction comes from a radial sweep (rays at most one cell apart, sampled every half cell with the terrain "
        "interpolated bilinearly and obstacle tops per cell, running maximum slope; Franklin & Ray's R2). Visible % "
        "= visible area cells ÷ area cells with DEM data; combined = seen by at least one pilot or observer."
    )
    if params.max_range_m is not None:
        text += f" Cells beyond {params.max_range_m:g} m from an observer count as not visible from it."
    if radio is not None:
        text += (
            f" Radio paths from '{radio.station_name}' at {radio.frequency_mhz:g} MHz were sampled on the DEM every "
            f"≤ {radio_step_m:g} m (obstacles within {radio_step_m / 2.0:g} m of a path add their top). The first "
            "Fresnel zone radius is r1 = 17.32·√(d1·d2/(f·D)) m (d in km, f in GHz) and clearance % = "
            f"(LOS − surface)/r1, with the LOS lowered by the Earth bulge d1·d2/(2·k·R), k = {radio.k_factor:.4g}; "
            f"stretches below {radio.clearance_threshold_pct:g} % are flagged. Free-space path loss over the slant "
            f"distance: FSPL = 20·log10(d_km) + 20·log10(f_MHz) + 32.44 dB. {RADIO_GEOMETRY_NOTE}"
        )
    if guidance.basis == "easa_alos_dlos":
        text += (
            f" VLOS guidance (non-regulatory): EASA ALOS = {EASA_ALOS[guidance.aircraft][0]:g} × "
            f"{guidance.characteristic_dimension_m:g} m + {EASA_ALOS[guidance.aircraft][1]:g} m = {guidance.alos_m:g} m, "
            f"DLOS = 0.3 × {guidance.ground_visibility_m:g} m = {guidance.dlos_m:g} m, VLOS = min = {guidance.guidance_m:g} m."
        )
    else:
        text += f" VLOS guidance (non-regulatory): the commonly cited UK CAA figure of {UK_CAA_VLOS_M:g} m."
    text += (
        f" It is an air-line distance: the map ring is drawn at √(G² − h²) = {guidance.ring_radius_m:g} m for the "
        "target height over level ground; the share of the area beyond it uses the slant distance to the nearest "
        "pilot or observer."
    )
    return text


def run(params: LineOfSightParams, ctx: AnalysisContext) -> LineOfSightResult:
    """Run the line-of-sight analysis. Requires the ``dem`` layer; uses ``obstacles`` when present."""
    pack = ctx.require("dem")
    dem = pack.dem()
    if dem is None:  # listed but unreadable — treat as missing
        raise AnalysisError(
            "layer_missing",
            f"Data pack '{pack.name}' has no readable DEM.",
            details={"datapack": pack.name, "missing": ["dem"]},
        )
    issues: list[Issue] = []

    ctx.step("Preparing inputs", 0.02)
    geom = _parse_geometry(params.geometry)
    viewpoints = _viewpoints(params, geom, issues)
    frame = LocalFrame.for_geometry(geom)
    local = frame.to_local(geom)
    vx, vy = frame.xy([v.lon for v in viewpoints], [v.lat for v in viewpoints])
    minx, miny, maxx, maxy = local.bounds
    bounds = (
        min(minx, float(vx.min())),
        min(miny, float(vy.min())),
        max(maxx, float(vx.max())),
        max(maxy, float(vy.max())),
    )
    x0, y0, cell, cols, rows, coarsened = _grid_layout(bounds, params.grid_m)  # area_too_large first
    for v, x, y in zip(viewpoints, vx.tolist(), vy.tolist(), strict=True):
        v.x, v.y = float(x), float(y)
        v.ground_m = float(np.asarray(dem.sample(v.lon, v.lat)))
        if not math.isfinite(v.ground_m):
            raise AnalysisError(
                "out_of_coverage",
                f"'{v.name}' ({v.lat:.5f}, {v.lon:.5f}) is outside the DEM of data pack '{pack.name}'.",
                details={"datapack": pack.name, "observer": v.name},
            )

    ctx.step("Resampling the DEM onto the grid", 0.08)
    centres_x = x0 + (np.arange(cols, dtype=np.float64) + 0.5) * cell
    centres_y = y0 + (np.arange(rows, dtype=np.float64) + 0.5) * cell
    gx, gy = np.meshgrid(centres_x, centres_y)
    glon, glat = frame.lonlat(gx.ravel(), gy.ravel())
    ground = np.asarray(dem.sample(glon, glat), dtype=np.float64).reshape(rows, cols)
    grid = _Grid(frame, x0, y0, cell, cols, rows, ground, np.full((rows, cols), np.nan))
    for v in viewpoints:
        cx, cy = grid.cell_units(v.x, v.y)
        v.grid_ground_m = float(grid_bilinear(ground, np.array([cx]), np.array([cy]))[0])
    if coarsened:
        issues.append(Issue(
            code="grid_coarsened",
            message=(
                f"The operation and the observers need more than {MAX_GRID_SIDE} cells of {params.grid_m:g} m "
                f"along one side: the grid was coarsened to {cell:g} m cells, so relief narrower than a cell "
                "(ridges, embankments, roof and canopy edges of the surface model) may be smoothed away. "
                "Check a smaller extent for detail."
            ),
            severity=Severity.WARNING,
        ))

    # Radio profiles follow the DEM itself: a coarsened grid must not coarsen them too.
    dem_res = dem.nominal_resolution_m()
    radio_step = max(RADIO_MIN_STEP_M, min(cell, dem_res) if math.isfinite(dem_res) and dem_res > 0 else cell)

    ctx.step("Raising obstacles", 0.14)
    obstacles_used = 0
    if params.include_obstacles:
        if pack.has("obstacles"):
            obstacles_used = _raise_obstacles(grid, pack, dem)
        else:
            issues.append(Issue(
                code="obstacles_unavailable",
                message=f"Data pack '{pack.name}' has no obstacle layer: only the DEM surface blocks the view.",
                severity=Severity.INFO,
            ))

    area = _area_mask(grid, local)
    valid = np.isfinite(grid.ground)
    area_valid = area & valid
    area_cells = int(np.count_nonzero(area))
    area_valid_cells = int(np.count_nonzero(area_valid))
    if area_valid_cells == 0:
        raise AnalysisError(
            "out_of_coverage",
            f"The DEM of data pack '{pack.name}' has no data over the operation geometry.",
            details={"datapack": pack.name},
        )
    nodata = int(np.count_nonzero(~valid))
    if nodata:
        issues.append(Issue(
            code="dem_nodata",
            message=(
                f"{nodata} of {rows * cols} grid cells have no DEM data ({area_cells - area_valid_cells} of them in "
                "the operation area); they are left out of the percentages."
            ),
            severity=Severity.WARNING,
        ))

    cell_area = cell * cell
    target_h = float(params.target_height_agl_m)
    combined = np.zeros((rows, cols), dtype=bool)
    counts = np.zeros((rows, cols), dtype=np.int16)
    radio_mask = np.zeros((rows, cols), dtype=bool)
    has_gcs = any(v.role == "gcs" for v in viewpoints)
    rows_out: list[LosObserver] = []
    for i, v in enumerate(viewpoints):
        ctx.step(f"Viewshed from {v.name}", 0.18 + 0.5 * i / len(viewpoints))
        k = params.k_visible if v.visual else params.k_radio
        vis = viewshed(
            grid.ground,
            cell,
            grid.cell_units(v.x, v.y),
            v.view_eye_m,
            target_h,
            k,
            obstacles_m=grid.obstacles,
            max_range_m=params.max_range_m,
            check=ctx.check_cancelled,
        )
        seen = vis & area_valid
        n_seen = int(np.count_nonzero(seen))
        far = float(np.max(np.hypot(gx[seen] - v.x, gy[seen] - v.y))) if n_seen else None
        if v.visual:
            combined |= vis
            counts += vis.astype(np.int16)
        else:
            radio_mask |= vis
        rows_out.append(LosObserver(
            name=v.name,
            role=v.role,  # type: ignore[arg-type]
            lon=round(v.lon, 8),
            lat=round(v.lat, 8),
            height_agl_m=round(v.height_m, 2),
            ground_m=_round(v.ground_m),
            eye_amsl_m=_round(v.eye_m),
            k_factor=round(k, 4),
            los_kind="visual" if v.visual else "radio",
            visible_pct=round(100.0 * n_seen / area_valid_cells, 1),
            visible_area_m2=round(n_seen * cell_area, 1),
            max_visible_m=_round(far, 1),
            assumed=v.assumed,
        ))

    station = next((v for v in viewpoints if v.role == "gcs"), None) or next(v for v in viewpoints if v.visual)
    radio_los_pct: float | None = None
    if params.radio_check:
        if not has_gcs:
            ctx.step(f"Radio line of sight from {station.name}", 0.7)
            radio_mask = viewshed(
                grid.ground, cell, grid.cell_units(station.x, station.y), station.view_eye_m, target_h, params.k_radio,
                obstacles_m=grid.obstacles, max_range_m=params.max_range_m, check=ctx.check_cancelled,
            )
        radio_los_pct = round(100.0 * np.count_nonzero(radio_mask & area_valid) / area_valid_cells, 1)

    ctx.step("Summarising coverage", 0.74)
    seen_any = combined & area_valid
    n_combined = int(np.count_nonzero(seen_any))
    combined_pct = round(100.0 * n_combined / area_valid_cells, 1)
    hidden_area = area_valid & ~combined

    patch: LosHiddenPatch | None = None
    patch_geom: BaseGeometry | None = None
    if np.any(hidden_area):
        from scipy import ndimage

        labels, count = ndimage.label(hidden_area, structure=np.ones((3, 3), dtype=bool))
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        best = int(np.argmax(sizes))
        member = labels == best
        cx, cy = float(gx[member].mean()), float(gy[member].mean())
        plon, plat = frame.lonlat(np.array([cx]), np.array([cy]))
        patch = LosHiddenPatch(
            area_m2=round(float(sizes[best]) * cell_area, 1),
            cells=int(sizes[best]),
            lon=round(float(plon[0]), 8),
            lat=round(float(plat[0]), 8),
        )
        patch_geom = _polygons_by_value(grid, member.astype(np.uint8), member).get(1)

    guidance = vlos_guidance(params.vlos_aircraft, params.characteristic_dimension_m, params.ground_visibility_m, target_h)
    visual = [v for v in viewpoints if v.visual]
    target_z = grid.ground + target_h
    nearest_slant = np.full((rows, cols), np.inf)
    for v in visual:
        slant = np.hypot(np.hypot(gx - v.x, gy - v.y), target_z - v.eye_m)
        nearest_slant = np.fmin(nearest_slant, slant)
    beyond = area_valid & (nearest_slant > guidance.guidance_m)
    beyond_pct = round(100.0 * np.count_nonzero(beyond) / area_valid_cells, 1)
    if guidance.ring_radius_m <= 0.0:
        issues.append(Issue(
            code="target_above_guidance",
            message=(
                f"The target height ({target_h:g} m) is at least the VLOS guidance distance "
                f"({guidance.guidance_m:g} m): the whole operation is beyond it."
            ),
            severity=Severity.WARNING,
        ))
    elif beyond_pct > 0:
        issues.append(Issue(
            code="beyond_vlos_guidance",
            message=(
                f"{beyond_pct:g} % of the area is farther than the {guidance.guidance_m:g} m VLOS guidance distance "
                "(air-line) from every pilot and observer. Guidance only, not a limit."
            ),
            severity=Severity.INFO,
        ))

    route: _RouteResult | None = None
    lines_local = lineal_part(local)
    if lines_local is not None:
        route = _route(grid, lines_local, combined)

    radio: LosRadioSummary | None = None
    if params.radio_check:
        ctx.step("Radio path profiles", 0.8)
        ends = _radio_targets(params, geom, frame, station)
        paths: list[LosRadioPath] = []
        skipped: list[str] = []
        max_points = PROFILE_MAX_POINTS_SINGLE if len(ends) <= 2 else PROFILE_MAX_POINTS_MANY
        for end in ends:
            ctx.check_cancelled()
            path = _radio_path(
                station,
                end,
                dem=dem,
                pack=pack,
                frame=frame,
                params=params,
                corridor_m=radio_step / 2.0,
                spacing_cap_m=radio_step,
                max_points=max_points,
            )
            if path is None:
                skipped.append(end.name)
            else:
                paths.append(path)
        if skipped:
            issues.append(Issue(
                code="radio_path_skipped",
                message=(
                    f"No radio profile to {', '.join(skipped[:5])}{'…' if len(skipped) > 5 else ''}: the point is "
                    "outside the DEM or at the station itself."
                ),
                severity=Severity.INFO,
            ))
        worsts = [p.worst_clearance_pct for p in paths if p.worst_clearance_pct is not None]
        radio = LosRadioSummary(
            frequency_mhz=params.frequency_mhz,
            k_factor=round(params.k_radio, 4),
            clearance_threshold_pct=params.fresnel_clearance_pct,
            station_name=station.name,
            station_role=station.role,  # type: ignore[arg-type]
            paths=paths,
            paths_below_threshold=sum(1 for p in paths if p.below_threshold),
            paths_blocked=sum(1 for p in paths if p.los_blocked),
            worst_clearance_pct=min(worsts) if worsts else None,
            max_fspl_db=max((p.fspl_db for p in paths), default=None),
        )
        if radio.paths_blocked:
            issues.append(Issue(
                code="radio_los_blocked",
                message=(
                    f"Terrain or obstacles cut the straight line on {radio.paths_blocked} of {len(paths)} radio "
                    f"path(s) from '{station.name}' (clearance below 0 %)."
                ),
                severity=Severity.WARNING,
            ))
        elif radio.paths_below_threshold:
            issues.append(Issue(
                code="fresnel_below_threshold",
                message=(
                    f"First Fresnel zone clearance is below {params.fresnel_clearance_pct:g} % on "
                    f"{radio.paths_below_threshold} of {len(paths)} radio path(s) from '{station.name}' "
                    f"(worst {radio.worst_clearance_pct:g} %)."
                ),
                severity=Severity.WARNING,
            ))

    if obstacles_used:
        issues.append(Issue(
            code="obstacles_raised",
            message=f"{obstacles_used} data-pack obstacles were raised onto the grid as point heights in their cell.",
            severity=Severity.INFO,
        ))
    issues.append(Issue(
        code="surface_model",
        message=(
            f"Terrain from '{dem.name}' at about {dem.nominal_resolution_m():.0f} m: a surface model with roofs and "
            "canopy. Thin obstructions (wires, poles, single trees) are not represented."
        ),
        severity=Severity.INFO,
    ))
    if pack.synthetic:
        issues.append(Issue(
            code="synthetic_data",
            message=f"Data pack '{pack.name}' is synthetic: terrain and obstacles are illustrative, not real.",
            severity=Severity.WARNING,
        ))

    ctx.step("Building layers", 0.92)
    polygons = _polygons_by_value(grid, counts.clip(0, 255), valid)
    visible_features = [
        _feature(_to_wgs84(grid, g), {
            "visible": True,
            "observers": value,
            "area_m2": round(float(g.area), 1),
        })
        for value, g in sorted(polygons.items())
        if value > 0 and not g.is_empty
    ]
    hidden_geom = polygons.get(0)
    hidden_features = (
        [_feature(_to_wgs84(grid, hidden_geom), {"visible": False, "observers": 0, "area_m2": round(float(hidden_geom.area), 1)})]
        if hidden_geom is not None and not hidden_geom.is_empty
        else []
    )
    observer_features = [
        _feature(Point(o.lon, o.lat), {
            "name": o.name,
            "role": o.role,
            "height_agl_m": o.height_agl_m,
            "eye_amsl_m": o.eye_amsl_m,
            "visible_pct": o.visible_pct,
            "los_kind": o.los_kind,
            "assumed": o.assumed,
        })
        for o in rows_out
    ]
    ring_features = []
    if guidance.ring_radius_m > 0:
        for v in visual:
            ring = Point(v.x, v.y).buffer(guidance.ring_radius_m, quad_segs=32)
            ring_features.append(_feature(_to_wgs84(grid, ring), {
                "observer": v.name,
                "radius_m": guidance.ring_radius_m,
                "guidance_m": guidance.guidance_m,
                "basis": guidance.basis,
                "label": "VLOS guidance (non-regulatory)",
            }))
    layers: dict[str, Any] = {
        "input": _collection([_feature(geom, {"role": "input", "geometry_type": geometry_type_name(geom)})]),
        "observers": _collection(observer_features),
        "visible": _collection(visible_features),
        "hidden": _collection(hidden_features),
        "vlos_guidance": _collection(ring_features),
    }
    if patch is not None and patch_geom is not None and not patch_geom.is_empty:
        layers["hidden_patch"] = _collection([_feature(_to_wgs84(grid, patch_geom), {
            "area_m2": patch.area_m2,
            "cells": patch.cells,
            "label": "Largest hidden patch",
        })])
    if route is not None:
        layers["route"] = _collection([
            _feature(_to_wgs84(grid, g), {
                "visible": seg.visible,
                "start_m": seg.start_m,
                "end_m": seg.end_m,
                "length_m": seg.length_m,
            })
            for g, seg in route.segments
            if not g.is_empty
        ])
    if radio is not None:
        layers["radio_paths"] = _collection([
            _feature(LineString([(p.from_lon, p.from_lat), (p.to_lon, p.to_lat)]), {
                "from_name": p.from_name,
                "to_name": p.to_name,
                "distance_m": p.distance_m,
                "fspl_db": p.fspl_db,
                "worst_clearance_pct": p.worst_clearance_pct,
                "band": _radio_band(p),
                "below_threshold": p.below_threshold,
                "los_blocked": p.los_blocked,
            })
            for p in radio.paths
        ])

    info = LosGridInfo(
        grid_m=cell,
        requested_grid_m=float(params.grid_m),
        cols=cols,
        rows=rows,
        coarsened=coarsened,
        area_cells=area_valid_cells,
        nodata_cells=nodata,
        dem_source=str(dem.name),
        dem_resolution_m=_round(dem.nominal_resolution_m(), 1),
        obstacles_used=obstacles_used,
    )
    issues.extend(ctx.issues)
    return LineOfSightResult(
        geometry_type=geometry_type_name(geom),
        target_height_agl_m=target_h,
        observers=rows_out,
        combined_visible_pct=combined_pct,
        visible_area_m2=round(n_combined * cell_area, 1),
        hidden_area_m2=round(float(np.count_nonzero(hidden_area)) * cell_area, 1),
        radio_los_pct=radio_los_pct,
        largest_hidden_patch=patch,
        route=route.summary if route is not None else None,
        beyond_guidance_pct=beyond_pct,
        grid=info,
        radio=radio,
        vlos_guidance_m=guidance.guidance_m,
        vlos_guidance=guidance,
        method=_method_text(params, info, guidance, radio, radio_step),
        disclaimer=DISCLAIMER_LINE_OF_SIGHT,
        issues=issues,
        layers=layers,
        dem=dem_info(pack),
    )
