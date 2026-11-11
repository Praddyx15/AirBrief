"""Terrain-true heights: one planned height read as AGL, above take-off or AMSL.

Definitions (ICAO Annex 2, Chapter 1: *altitude* is measured from mean sea level, *height* from a
specified datum). With ``h`` the planned value, ``z(x)`` the DEM ground elevation (m AMSL) below
the aircraft and ``T`` the ground elevation at the take-off point:

* ``agl`` — the aircraft follows the terrain h above it: ``AGL(x) = h``, ``AMSL(x) = z(x) + h``;
* ``takeoff`` — the aircraft holds h above the take-off point (DJI "ALT", ArduPilot relative
  altitude): flight level ``L = T + h``, ``AGL(x) = L − z(x)``;
* ``amsl`` — the aircraft holds a level h above mean sea level: ``L = h``, ``AGL(x) = h − z(x)``.

Over rising ground a constant flight level comes closer to the ground (120 m above a take-off at
900 m over ground at 990 m is 30 m AGL); over a valley it climbs above the ceiling.

Take-off point (:func:`takeoff_point`): the given ``[lon, lat]``, else the first vertex of the
route, the centroid of the area (a point on its surface when the centroid falls outside) or the
point itself. Its elevation is the typed override, else the DEM (bilinear) at that point.

Without a DEM value the ground is unknown: AGL mode needs none; above take-off the ground is
taken as flat at the take-off level (``AGL = h``); AMSL needs the take-off elevation to say
anything (``AGL = h − T``, flat ground), else the AGL is unknown (NaN). Each case is reported
(``heights_not_assessed``, ``terrain_gaps``, ``takeoff_elevation_unknown``).

Runs (:func:`segments`, :func:`height_segments`): consecutive samples where a mask holds. With
the signed excess ``e`` (positive inside the run) a run starts and ends at the linear crossing
``f = −e₀ / (e₁ − e₀)`` between neighbouring samples, applied to the distance along the route
(the same rule as the flight audit's exceedances); a run touching an end of the route starts or
ends there.

The DEM is described from the data-pack manifest (:func:`dem_info`): Copernicus DEM is a digital
surface model (roofs and canopy included) on the EGM2008 geoid (Copernicus DEM Product Handbook),
so heights above it are heights above the *surface*: lower than above bare earth near buildings
and trees. That is conservative for obstacle clearance and not for ceilings.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from ..data.datapack import DataPack
from ..data.raster import RasterSampler
from ..data.sources import SOURCES
from ..geo.transform import unwrap_antimeridian
from ..geo.validity import iter_parts, lineal_part, polygonal_part
from ..models.common import Issue, Severity
from ..models.heights import DemInfo, HeightReference, HeightSegment, TakeoffPoint, TakeoffSource

__all__ = [
    "LIMIT_TOLERANCE_M",
    "REFERENCE_LABELS",
    "default_takeoff",
    "dem_info",
    "flight_level",
    "height_segments",
    "height_issues",
    "heights_from_ground",
    "index_runs",
    "reference_phrase",
    "resolve_heights",
    "segments",
    "takeoff_point",
]

LIMIT_TOLERANCE_M = 1e-6
"""Heights within this of a limit count as at the limit, not beyond it (float noise)."""

REFERENCE_LABELS: dict[str, str] = {
    "agl": "above ground",
    "takeoff": "above take-off",
    "amsl": "AMSL",
}
"""How a planned value reads in text: "120 m above take-off"."""

_M_PER_DEG_LAT = 111_320.0


def index_runs(mask: NDArray[np.bool_] | Sequence[bool]) -> list[tuple[int, int]]:
    """Inclusive ``(start, end)`` index runs of ``True`` in ``mask``."""
    padded = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]]).astype(np.int8)
    step = np.diff(padded)
    starts = np.flatnonzero(step == 1)
    ends = np.flatnonzero(step == -1) - 1
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _crossing(d0: float, d1: float, e0: float, e1: float) -> float:
    """Distance where the excess crosses zero between two samples (linear; d1 when undefined)."""
    if not (math.isfinite(e0) and math.isfinite(e1)) or e1 == e0:
        return d1
    f = min(1.0, max(0.0, -e0 / (e1 - e0)))
    return d0 + f * (d1 - d0)


def segments(
    mask: NDArray[np.bool_] | Sequence[bool],
    distance: ArrayLike,
    excess: ArrayLike | None = None,
) -> list[tuple[float, float]]:
    """``(start_m, end_m)`` of each run of ``True`` in ``mask`` along ``distance``.

    Without ``excess`` a run spans its first to its last sample. With ``excess`` (positive inside
    the run) its ends move to the interpolated zero crossings with the neighbouring samples.
    """
    d = np.asarray(distance, dtype=np.float64)
    e = None if excess is None else np.asarray(excess, dtype=np.float64)
    last = d.size - 1
    out: list[tuple[float, float]] = []
    for start, end in index_runs(mask):
        if e is None or start == 0:
            d_start = float(d[start])
        else:
            d_start = _crossing(float(d[start - 1]), float(d[start]), float(e[start - 1]), float(e[start]))
        if e is None or end == last:
            d_end = float(d[end])
        else:
            d_end = _crossing(float(d[end]), float(d[end + 1]), float(e[end]), float(e[end + 1]))
        out.append((d_start, d_end))
    return out


def height_segments(
    distance: ArrayLike,
    agl: ArrayLike,
    limit: ArrayLike | float,
    kind: str,
) -> list[HeightSegment]:
    """Stretches below a minimum (``kind="below"``) or above a ceiling (``kind="above"``).

    ``limit`` is one number or one value per sample (NaN where there is no limit, e.g. a zone
    that needs permission). Samples without a known AGL never belong to a stretch.
    """
    d = np.asarray(distance, dtype=np.float64)
    h = np.asarray(agl, dtype=np.float64)
    lim = np.broadcast_to(np.asarray(limit, dtype=np.float64), h.shape)
    if kind == "below":
        excess = lim - h
    elif kind == "above":
        excess = h - lim
    else:
        raise ValueError(f"kind must be 'below' or 'above', not {kind!r}")
    mask = np.isfinite(excess) & (excess > LIMIT_TOLERANCE_M)
    out: list[HeightSegment] = []
    for (start, end), (d0, d1) in zip(index_runs(mask), segments(mask, d, excess), strict=True):
        run_h = h[start : end + 1]
        run_lim = lim[start : end + 1]
        peak = float(np.min(run_h)) if kind == "below" else float(np.max(run_h))
        out.append(
            HeightSegment(
                start_m=round(d0, 2),
                end_m=round(d1, 2),
                length_m=round(d1 - d0, 2),
                peak_agl_m=round(peak, 2),
                limit_m=round(float(np.nanmin(run_lim)), 2),
            )
        )
    return out


def default_takeoff(geom: BaseGeometry) -> tuple[float, float, TakeoffSource]:
    """The take-off point assumed when none is given (see the module docstring)."""
    lines = lineal_part(geom)
    if lines is not None:
        first = next(iter_parts(lines))
        x, y = first.coords[0][:2]
        return float(x), float(y), "first_vertex"
    poly = polygonal_part(geom)
    if poly is not None:
        # Across the antimeridian the plain lon/lat centroid lands near 0°: unwrap first.
        unwrapped = unwrap_antimeridian(poly)
        centre = unwrapped.centroid
        if not unwrapped.covers(centre):
            centre = unwrapped.representative_point()
        x = float(centre.x)
        return (x - 360.0 if x > 180.0 else x), float(centre.y), "centroid"
    for part in iter_parts(geom):
        if isinstance(part, Point):
            return float(part.x), float(part.y), "point"
    centre = geom.centroid
    return float(centre.x), float(centre.y), "centroid"


def _sample_one(dem: RasterSampler | None, lon: float, lat: float) -> float | None:
    if dem is None:
        return None
    value = float(np.asarray(dem.sample(np.array([lon]), np.array([lat])), dtype=np.float64)[0])
    return value if math.isfinite(value) else None


def takeoff_point(
    geom: BaseGeometry | None,
    given: Sequence[float] | None,
    dem: RasterSampler | None,
    elevation_override: float | None,
    *,
    source: TakeoffSource | None = None,
) -> TakeoffPoint:
    """The take-off point with its ground elevation (override first, then the DEM).

    ``given`` wins over the geometry; ``source`` names the origin of ``given`` (default "given").
    """
    if given is not None:
        lon, lat = float(given[0]), float(given[1])
        where: TakeoffSource = source or "given"
    elif geom is not None:
        lon, lat, where = default_takeoff(geom)
    else:
        raise ValueError("a take-off point needs a geometry or a given position")
    if elevation_override is not None:
        return TakeoffPoint(
            lon=round(lon, 8), lat=round(lat, 8), elevation_m=round(float(elevation_override), 2),
            source=where, elevation_source="override",
        )
    sampled = _sample_one(dem, lon, lat)
    return TakeoffPoint(
        lon=round(lon, 8), lat=round(lat, 8),
        elevation_m=round(sampled, 2) if sampled is not None else None,
        source=where, elevation_source="dem" if sampled is not None else None,
    )


def flight_level(ref: HeightReference, value_m: float, takeoff_elev_m: float | None) -> float | None:
    """The constant flight level (m AMSL) of a plan, or None (``agl`` has none; unknown take-off)."""
    if ref == "amsl":
        return float(value_m)
    if ref == "takeoff" and takeoff_elev_m is not None:
        return float(takeoff_elev_m) + float(value_m)
    return None


def heights_from_ground(
    ground: ArrayLike,
    ref: HeightReference,
    value_m: float,
    takeoff_elev_m: float | None,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """``(agl, amsl)`` per sample from the ground elevations (NaN where unknown; module docstring)."""
    z = np.asarray(ground, dtype=np.float64)
    h = float(value_m)
    known = np.isfinite(z)
    if ref == "agl":
        return np.full(z.shape, h), z + h
    level = flight_level(ref, h, takeoff_elev_m)
    amsl = np.full(z.shape, np.nan if level is None else level)
    if level is None:
        # Above take-off with an unknown take-off elevation: flat ground at the take-off level.
        # AMSL never gets here (its level is the value itself).
        return np.full(z.shape, h), amsl
    flat = h if ref == "takeoff" else (level - float(takeoff_elev_m) if takeoff_elev_m is not None else np.nan)
    agl = np.where(known, level - np.where(known, z, 0.0), flat)
    return agl.astype(np.float64), amsl


def _height_issues(
    ref: HeightReference, ground: NDArray[np.float64], takeoff_elev_m: float | None
) -> list[Issue]:
    if ref == "agl":
        return []
    issues: list[Issue] = []
    known = int(np.isfinite(ground).sum())
    total = int(ground.size)
    if total and known == 0:
        if ref == "takeoff":
            consequence = "heights above take-off were treated as heights above flat ground."
        elif takeoff_elev_m is not None:
            consequence = (
                f"the ground was taken as flat at the take-off elevation of {takeoff_elev_m:.0f} m AMSL."
            )
        else:
            consequence = "the height above ground could not be derived (give the take-off elevation)."
        issues.append(Issue(
            code="heights_not_assessed",
            message=f"No DEM covers the flight: terrain-true heights were not assessed and {consequence}",
            severity=Severity.WARNING,
        ))
    elif known < total:
        issues.append(Issue(
            code="terrain_gaps",
            message=(
                f"The DEM has no value at {total - known} of {total} samples; there the ground was "
                "taken as flat at the take-off elevation."
            ),
            severity=Severity.INFO,
        ))
    if ref == "takeoff" and takeoff_elev_m is None and known > 0:
        issues.append(Issue(
            code="takeoff_elevation_unknown",
            message=(
                "The DEM has no value at the take-off point: heights above take-off were treated as "
                "heights above flat ground. Enter the take-off elevation to assess them."
            ),
            severity=Severity.WARNING,
        ))
    if ref == "amsl":
        issues.append(Issue(
            code="vertical_reference_approximated",
            message=(
                "AMSL heights are compared with the DEM's heights above the geoid (≈ mean sea level). "
                "A GNSS altitude above the WGS84 ellipsoid differs by the local geoid undulation, and a "
                "barometric altitude depends on the pressure setting."
            ),
            severity=Severity.INFO,
        ))
    return issues


def resolve_heights(
    lons: ArrayLike,
    lats: ArrayLike,
    dem: RasterSampler | None,
    ref: HeightReference,
    value_m: float,
    takeoff_lonlat: Sequence[float] | None = None,
    takeoff_elev_m: float | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.float64], float | None, list[Issue]]:
    """Heights above ground and above sea level at each point, for a plan read in ``ref``.

    Returns ``(agl, amsl, takeoff_elev, issues)``: NaN marks an unknown value; ``takeoff_elev`` is
    the override, else the DEM at ``takeoff_lonlat`` (None in ``agl`` mode or when unknown).
    """
    x = np.asarray(lons, dtype=np.float64)
    y = np.asarray(lats, dtype=np.float64)
    if dem is not None and x.size:
        ground = np.asarray(dem.sample(x, y), dtype=np.float64)
    else:
        ground = np.full(x.shape, np.nan)
    takeoff = None if ref == "agl" else takeoff_elev_m
    if ref != "agl" and takeoff is None and takeoff_lonlat is not None:
        takeoff = _sample_one(dem, float(takeoff_lonlat[0]), float(takeoff_lonlat[1]))
    agl, amsl = heights_from_ground(ground, ref, value_m, takeoff)
    return agl, amsl, takeoff, _height_issues(ref, ground, takeoff)


def height_issues(ref: HeightReference, ground: ArrayLike, takeoff_elev_m: float | None) -> list[Issue]:
    """The notes :func:`resolve_heights` adds, for callers that sampled the ground themselves."""
    return _height_issues(ref, np.asarray(ground, dtype=np.float64), takeoff_elev_m)


def reference_phrase(ref: HeightReference, value_m: float) -> str:
    """"120 m above take-off", "1,050 m AMSL", "60 m above ground"."""
    return f"{value_m:,.0f} m {REFERENCE_LABELS[ref]}"


def dem_info(pack: DataPack | None) -> DemInfo | None:
    """The data pack's DEM described from its manifest, or None without one.

    Copernicus GLO-30/GLO-90 are digital surface models on the EGM2008 geoid; the synthetic pack's
    DEM is invented; anything else is described by its manifest source with an explicit caveat.
    """
    if pack is None or not pack.has("dem"):
        return None
    entry = pack.manifest.layers["dem"]
    source = entry.source or "dem"
    extra = entry.model_extra or {}
    datum_raw = extra.get("vertical_datum")
    datum = str(datum_raw) if isinstance(datum_raw, str) and datum_raw.strip() else None
    res_deg = extra.get("resolution_deg")
    resolution_m = (
        round(float(res_deg) * _M_PER_DEG_LAT, 1)
        if isinstance(res_deg, int | float) and not isinstance(res_deg, bool) and math.isfinite(res_deg)
        else None
    )
    known = SOURCES.get(source)
    title = known.title if known is not None else source
    if source.startswith("copernicus"):
        short = "Copernicus GLO-90" if "90" in source else "Copernicus GLO-30"
        return DemInfo(
            source=source, title=title, surface_model=True, synthetic=False,
            vertical_datum=datum or "EGM2008 (≈ AMSL)", resolution_m=resolution_m,
            caveat=(
                f"{short} surface model — includes roofs and canopy: heights above it are heights above "
                "the surface, lower than above bare ground near buildings and trees (conservative for "
                "obstacle clearance, not for ceilings). Heights are above the EGM2008 geoid."
            ),
        )
    if source == "synthetic" or (pack.synthetic and source in ("", "dem")):
        return DemInfo(
            source=source, title=title, surface_model=None, synthetic=True,
            vertical_datum=datum, resolution_m=resolution_m,
            caveat="Synthetic demo elevation, invented for testing — not real terrain.",
        )
    return DemInfo(
        source=source, title=title, surface_model=None, synthetic=bool(pack.synthetic),
        vertical_datum=datum, resolution_m=resolution_m,
        caveat=(
            f"Elevation model '{source}' from data pack '{pack.name}': check whether it is a surface "
            "model (roofs, canopy) or bare-earth terrain, and its vertical datum."
        ),
    )
