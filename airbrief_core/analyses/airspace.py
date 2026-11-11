"""Airspace & terrain pre-flight check for a polygon, route or point (docs/SPEC.md §3).

Pipeline:

1. Parse the WGS84 GeoJSON geometry (Z dropped, invalid polygons repaired).
2. Nearest aerodromes (data pack), zone hits (aerodrome rings, user zones, data-pack zones).
3. Terrain: a DEM summary over the geometry and, for lines, a profile every
   ``profile_spacing_m`` with the per-point free-flight limit (``limit_agl_m``: the zone
   ceiling, ``0`` in a red zone, ``None`` where permission is required).
4. Obstacles within ``obstacle_search_m``; a conflict is ``height + vertical_clearance >
   altitude``.
5. H3 flyability grid over the area (lines and points buffered by 50 m): per cell the zone at
   the centroid, the terrain height and the tallest obstacle inside the cell, with a status:
   ``restricted`` (red), ``permission`` (permission required), ``limited`` (ceiling below the
   planned altitude, or an obstacle that would leave less than the vertical clearance) or
   ``free``.
6. Effective ceiling = the lowest free-flight ceiling anywhere on the geometry (``None`` when
   permission is required anywhere or a red zone is touched).
7. Verdict: ``fail`` when a red zone is touched; ``caution`` when permission is required, the
   effective ceiling is below the planned altitude, or an obstacle conflicts; else ``pass``.
8. Assessment completeness: without a data pack, without its ``aerodromes`` layer, or with the
   geometry outside the pack, aerodrome zones were not checked. The result then has
   ``assessment_complete = False``, an issue ``not_assessed``, a summary that starts with
   "Airspace not assessed", and a ``pass`` becomes ``caution`` (never ``pass``). A geometry that
   extends beyond the pack is "Airspace only partly assessed" with the same consequences (the
   flight audit's rule for track points outside the pack).
9. Height reference (:mod:`.heights`). ``altitude_agl_m`` is the planned value read in
   ``height_reference``. ``agl`` (the default) keeps every result above unchanged. ``takeoff``
   and ``amsl`` fly a constant level (take-off ground + value, or the value) and use the height
   above the ground *at each sample*: the profile's ``agl_m``, each grid cell's ``agl_m`` and
   status, obstacle clearance (level − obstacle base) and the ceiling check (the highest AGL).
   The result adds the AGL range, the stretches of a route below ``min_agl_m``
   (``below_min_agl``) and above the free-flight ceiling (``above_ceiling``), grid-cell counts
   and the take-off point. ``agl`` plans get only the stretches above the ceiling (the planned
   value restated against the zone ceilings); their ``min_agl_m`` is None (not checked). Below the minimum gives ``caution``; ground above the flight level a
   critical issue and ``fail``; a plan the DEM could not relate to the ground
   (``heights_assessed = False``) is never ``pass``. The DEM is described from the manifest
   (``dem``: Copernicus is a surface model, roofs and canopy included).

10. Zone rules (:mod:`.zones`): ``zone_profile`` picks the rule set (DGCA 2021 bands from the
   aerodrome perimeter by default; ``zone_rules`` makes it ``custom``). Zones come from the pack,
   ``user_zones`` and ``dataset_zones`` (filled by the API from ``zone_dataset_ids``). With a
   planned window, zones not active in it and zones whose vertical band the flight cannot reach
   are left out and listed in ``inactive_zones``. ``aerodrome_operational`` switches an
   aerodrome's bands on or off. ``band_evidence`` gives, per aerodrome band reached (and
   "elsewhere"), the samples in it (profile samples of a route, grid cells of an area, else the
   vertices), the highest planned height above ground and its margin to the band's ceiling; a
   band the published rule does not define is never ``pass``. Bands that could not be measured
   from a mapped perimeter are noted (``perimeter_approximated``, ``perimeter_from_runways``).
   Every result carries ``assessment_banner``: red zones are not open data, verify on Digital Sky.

Nothing is required from the data pack: without one (or without a layer) the module still runs
and records issues explaining what was not checked.
"""

from __future__ import annotations

import math
from typing import Any, Literal

import h3
import numpy as np
import shapely
from shapely.geometry import MultiPoint, Point
from shapely.geometry.base import BaseGeometry

from ..data.datapack import DataPack
from ..data.raster import RasterSampler
from ..errors import AnalysisError
from ..geo.frame import LocalFrame
from ..geo.geodesic import geodesic_area_perimeter, geodesic_length
from ..geo.h3grid import cell_centroids, cells_for_polygon
from ..geo.sampling import grid_points
from ..geo.transform import geojson_to_geometry, geometry_to_geojson
from ..geo.validity import geometry_type_name, iter_parts, lineal_part, polygonal_part, repair
from ..models.analyses import AirspaceParams, AirspaceResult, FlyabilityCell
from ..models.common import (
    BBox,
    ObstacleHit,
    ProfilePoint,
    Severity,
    TerrainSummary,
    Verdict,
    ZoneHit,
    ZoneRules,
)
from ..models.heights import HeightSegment, TakeoffPoint
from ..models.presets import DIGITAL_SKY_URL, DISCLAIMER_AIRSPACE, RED_ZONES_NOTICE, ZONE_PROFILE_LABELS, profile_rules
from ..models.zones import BandEvidence
from .base import AnalysisContext
from .heights import (
    LIMIT_TOLERANCE_M,
    dem_info,
    flight_level,
    height_issues,
    height_segments,
    heights_from_ground,
    reference_phrase,
    takeoff_point,
)
from .terrain import obstacle_height, obstacle_hits, terrain_profile, terrain_summary
from .zones import (
    TimeWindow,
    ZoneState,
    band_evidence,
    classify_points,
    nearest_aerodromes,
    ring_polygons,
    touched_polygon_zones,
    zone_hits_by_activity,
)

__all__ = ["GRID_BUFFER_M", "MAX_PROFILE_POINTS", "NOT_ASSESSED_SUMMARY", "PARTLY_ASSESSED", "assessment_gap", "run"]

NOT_ASSESSED_SUMMARY = (
    "Airspace not assessed: no data pack covers this area — aerodrome zones were not checked."
)
"""Summary opening when no data pack is available (the verdict is then never ``pass``)."""

PARTLY_ASSESSED = "Airspace only partly assessed"
"""Opening of the gap of a geometry that extends beyond its data pack (as the flight audit's)."""

GRID_BUFFER_M = 50.0
"""Lines and points are buffered by this distance to build the flyability grid area."""

MAX_PROFILE_POINTS = 5_000
"""The profile spacing is enlarged so a route never yields more samples than this."""

MAX_VERTEX_SAMPLES = 20_000
"""Vertices used to classify the geometry for the effective ceiling (strided beyond this)."""

INTERIOR_SAMPLES = 2_000
"""Interior grid points used to classify polygons for the effective ceiling."""

TERRAIN_STEP_M = 30.0
"""Grid step for the polygon terrain summary."""

OBSTACLE_LIMIT = 200

CellStatus = Literal["free", "limited", "permission", "restricted"]


class _Plan:
    """How the planned height reads: the reference, the value, the take-off and the level."""

    def __init__(self, params: AirspaceParams, takeoff: TakeoffPoint | None) -> None:
        self.ref = params.height_reference
        self.value = float(params.altitude_agl_m)
        self.takeoff = takeoff
        self.takeoff_elev = takeoff.elevation_m if takeoff is not None else None
        self.level = flight_level(self.ref, self.value, self.takeoff_elev)

    @property
    def terrain_true(self) -> bool:
        return self.ref != "agl"

    def agl(self, ground: np.ndarray) -> np.ndarray:
        """Height above ground per ground sample (NaN where it cannot be known)."""
        return heights_from_ground(ground, self.ref, self.value, self.takeoff_elev)[0]

    def flat_agl(self) -> float:
        """The height above ground where the ground is unknown (flat ground at the take-off).

        An AMSL plan with neither DEM nor take-off elevation has none: the planned value is then
        used for the zone and obstacle checks, and the result says heights were not assessed.
        """
        value = self.ground_agl()
        return value if math.isfinite(value) else self.value

    def ground_agl(self) -> float:
        """Height above flat ground at the take-off; NaN when the plan says nothing of the ground."""
        return float(self.agl(np.array([np.nan]))[0])


def _round_or_none(value: float, digits: int = 2) -> float | None:
    v = float(value)
    return round(v, digits) if math.isfinite(v) else None


def _note_once(ctx: AnalysisContext, code: str, message: str, severity: Severity) -> None:
    if any(issue.code == code for issue in ctx.issues):
        return
    ctx.note(code, message, severity)


def _prepare_geometry(obj: dict[str, Any], ctx: AnalysisContext) -> BaseGeometry:
    geom = geojson_to_geometry(obj)
    if geom.is_empty:
        raise AnalysisError("invalid_geometry", "The geometry is empty")
    geom = shapely.force_2d(geom)
    coords = shapely.get_coordinates(geom)
    if coords.size and (np.abs(coords[:, 1]).max() > 90.0 or np.abs(coords[:, 0]).max() > 540.0):
        raise AnalysisError(
            "invalid_geometry",
            "Coordinates must be WGS84 longitude/latitude degrees",
            details={"max_abs_lon": float(np.abs(coords[:, 0]).max()), "max_abs_lat": float(np.abs(coords[:, 1]).max())},
        )
    fixed, repaired = repair(geom)
    if fixed is None or fixed.is_empty:
        raise AnalysisError("invalid_geometry", "The geometry has no valid part after repair")
    if repaired:
        ctx.note("geometry_repaired", "The input geometry was invalid and has been repaired before analysis.", Severity.INFO)
    return fixed


def _coverage_notes(ctx: AnalysisContext, pack: DataPack | None, geom: BaseGeometry) -> None:
    if pack is None:
        _note_once(
            ctx,
            "datapack_missing",
            "No data pack covers this area: aerodrome zones, terrain, obstacles and data-pack zones "
            "were not checked. Only user zones and the default zone rules were applied.",
            Severity.WARNING,
        )
        return
    if not pack.covers(BBox.from_bounds(geom.bounds)):
        _note_once(
            ctx,
            "out_of_coverage",
            f"The geometry extends beyond the coverage of data pack '{pack.name}'; results outside it are incomplete.",
            Severity.WARNING,
        )
    available = set(pack.layers)
    missing = {
        "aerodromes": "aerodrome zones and nearest aerodromes were not checked",
        "dem": "terrain heights are unavailable",
        "obstacles": "obstacles were not checked",
    }
    for layer, consequence in missing.items():
        if layer not in available:
            _note_once(
                ctx,
                f"layer_missing_{layer}",
                f"Data pack '{pack.name}' has no {layer} layer: {consequence}.",
                Severity.WARNING,
            )
    if pack.synthetic:
        _note_once(
            ctx,
            "synthetic_datapack",
            f"Data pack '{pack.name}' is synthetic demo data, not real-world data.",
            Severity.INFO,
        )


def assessment_gap(pack: DataPack | None, geom: BaseGeometry) -> str | None:
    """Why aerodrome zones could not be checked for ``geom``, or None when they were.

    Aerodrome zones are only known from a data pack's ``aerodromes`` layer. Without a pack,
    without that layer, or for a geometry outside the pack's coverage, the default rules see no
    aerodrome there, and a "free" result would be a false all-clear.
    """
    if pack is None:
        return NOT_ASSESSED_SUMMARY
    if "aerodromes" not in pack.layers:
        return (
            f"Airspace not assessed: data pack '{pack.name}' has no aerodrome layer — aerodrome "
            "zones were not checked."
        )
    bounds = BBox.from_bounds(geom.bounds)
    if not pack.bbox.intersects(bounds):
        return (
            f"Airspace not assessed: the geometry lies outside data pack '{pack.name}' — aerodrome "
            "zones were not checked."
        )
    if not pack.covers(bounds):
        return (
            f"{PARTLY_ASSESSED}: the geometry extends beyond data pack '{pack.name}' — aerodrome zones "
            "were not checked there."
        )
    return None


def _profile_limit(state: ZoneState) -> float | None:
    if state.zone == "red":
        return 0.0
    if state.permission_required:
        # a band flown above its ceiling still shows that ceiling (green to 60 m, yellow above)
        return state.rule_ceiling_agl_m
    return state.ceiling_agl_m


def _build_profile(
    lines: BaseGeometry,
    length_m: float,
    params: AirspaceParams,
    ctx: AnalysisContext,
    pack: DataPack | None,
    rules: ZoneRules,
    dem: RasterSampler | None,
    plan: _Plan,
    zkw: dict[str, Any],
) -> tuple[list[ProfilePoint], list[ZoneState]]:
    spacing = float(params.profile_spacing_m)
    if length_m / spacing > MAX_PROFILE_POINTS:
        spacing = length_m / MAX_PROFILE_POINTS
        ctx.note(
            "profile_spacing_increased",
            f"The route is long; the terrain profile spacing was increased to {spacing:.0f} m.",
            Severity.INFO,
        )
    raw = terrain_profile(lines, dem, spacing)
    if not raw:
        return [], []
    lons = np.array([p.lon for p in raw], dtype=np.float64)
    lats = np.array([p.lat for p in raw], dtype=np.float64)
    alt = float(params.altitude_agl_m)
    out: list[ProfilePoint] = []
    if plan.terrain_true:
        ground = np.array([np.nan if p.terrain_m is None else p.terrain_m for p in raw], dtype=np.float64)
        agl, amsl = heights_from_ground(ground, plan.ref, plan.value, plan.takeoff_elev)
        states = classify_points(lons, lats, pack=pack, rules=rules, agl=_known(agl, plan), **zkw)
        for i, (point, state) in enumerate(zip(raw, states, strict=True)):
            if i % 1000 == 0:
                ctx.check_cancelled()
            out.append(
                point.model_copy(
                    update={
                        "agl_m": _round_or_none(agl[i]),
                        "altitude_amsl_m": _round_or_none(amsl[i]),
                        "limit_agl_m": _profile_limit(state),
                    }
                )
            )
        return out, states
    states = classify_points(lons, lats, pack=pack, rules=rules, agl=alt, **zkw)
    for i, (point, state) in enumerate(zip(raw, states, strict=True)):
        if i % 1000 == 0:
            ctx.check_cancelled()
        out.append(
            point.model_copy(
                update={
                    "agl_m": alt,
                    "altitude_amsl_m": round(point.terrain_m + alt, 2) if point.terrain_m is not None else None,
                    "limit_agl_m": _profile_limit(state),
                }
            )
        )
    return out, states


def _grid_area(geom: BaseGeometry) -> BaseGeometry | None:
    parts: list[BaseGeometry] = []
    poly = polygonal_part(geom)
    if poly is not None:
        parts.append(poly)
    others: list[BaseGeometry] = []
    lines = lineal_part(geom)
    if lines is not None:
        others.append(lines)
    points = [p for p in iter_parts(geom) if isinstance(p, Point)]
    if points:
        others.append(MultiPoint(points))
    if others:
        frame = LocalFrame.for_geometry(geom)
        for other in others:
            buffered = frame.to_local(other).buffer(GRID_BUFFER_M, quad_segs=8)
            parts.append(frame.to_wgs84(buffered))
    if not parts:
        return None
    area = shapely.union_all(parts)
    if not area.is_valid:
        area = shapely.make_valid(area)
    return polygonal_part(area)


def _obstacle_max_by_cell(pack: DataPack | None, area: BaseGeometry, res: int, cells: set[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    if pack is None or "obstacles" not in pack.layers:
        return out
    index = pack.obstacles()
    if not index:
        return out
    min_lon, min_lat, max_lon, max_lat = area.bounds
    pad_m = float(h3.average_hexagon_edge_length(res, unit="m")) * 2.0
    pad_lat = pad_m / 110_000.0
    pad_lon = pad_m / (111_000.0 * max(math.cos(math.radians(min(89.0, max(abs(min_lat), abs(max_lat))))), 1e-3))
    for feat in index.in_bbox((min_lon - pad_lon, min_lat - pad_lat, max_lon + pad_lon, max_lat + pad_lat)):
        height = obstacle_height(feat.props)
        if height is None:
            continue
        cell = h3.latlng_to_cell(float(feat.lat), float(feat.lon), res)
        if cell in cells and height > out.get(cell, -math.inf):
            out[cell] = height
    return out


def _cell_status(state: ZoneState, obstacle_m: float | None, alt: float, clearance: float) -> CellStatus:
    if state.zone == "red":
        return "restricted"
    if state.permission_required:
        return "permission"
    if state.ceiling_agl_m is not None and state.ceiling_agl_m < alt:
        return "limited"
    if obstacle_m is not None and obstacle_m + clearance > alt:
        return "limited"
    return "free"


def _build_grid(
    geom: BaseGeometry,
    params: AirspaceParams,
    ctx: AnalysisContext,
    pack: DataPack | None,
    rules: ZoneRules,
    dem: RasterSampler | None,
    plan: _Plan,
    zkw: dict[str, Any],
) -> tuple[list[FlyabilityCell], int | None, bool, list[ZoneState]]:
    area = _grid_area(geom)
    if area is None or area.is_empty:
        return [], None, False, []
    cells, truncated = cells_for_polygon(area, params.h3_resolution, params.max_grid_cells)
    if not cells:
        return [], None, False, []
    res = int(h3.get_resolution(cells[0]))
    if truncated:
        ctx.note(
            "grid_truncated",
            f"The flyability grid was limited to {params.max_grid_cells} cells (H3 resolution {res}).",
            Severity.INFO,
        )
    lons, lats = cell_centroids(cells)
    terrain = np.asarray(dem.sample(lons, lats), dtype=np.float64) if dem is not None else np.full(len(cells), np.nan)
    obstacles = _obstacle_max_by_cell(pack, area, res, set(cells))
    alt = float(params.altitude_agl_m)
    clearance = float(params.vertical_clearance_m)
    out: list[FlyabilityCell] = []
    if plan.terrain_true:
        cell_agl = plan.agl(terrain)
        states = classify_points(lons, lats, pack=pack, rules=rules, agl=_known(cell_agl, plan), **zkw)
        flat = plan.flat_agl()
        for i, cell in enumerate(cells):
            if i % 2000 == 0:
                ctx.check_cancelled()
            state = states[i]
            t = float(terrain[i])
            obstacle = obstacles.get(cell)
            h = float(cell_agl[i])
            out.append(
                FlyabilityCell(
                    h3=cell,
                    zone=state.zone,
                    ceiling_agl_m=state.ceiling_agl_m,
                    terrain_m=round(t, 2) if math.isfinite(t) else None,
                    obstacle_max_height_m=round(obstacle, 2) if obstacle is not None else None,
                    status=_cell_status(state, obstacle, h if math.isfinite(h) else flat, clearance),
                    agl_m=round(h, 2) if math.isfinite(h) else None,
                )
            )
        return out, res, truncated, states
    states = classify_points(lons, lats, pack=pack, rules=rules, agl=alt, **zkw)
    for i, cell in enumerate(cells):
        if i % 2000 == 0:
            ctx.check_cancelled()
        state = states[i]
        t = float(terrain[i])
        obstacle = obstacles.get(cell)
        out.append(
            FlyabilityCell(
                h3=cell,
                zone=state.zone,
                ceiling_agl_m=state.ceiling_agl_m,
                terrain_m=round(t, 2) if math.isfinite(t) else None,
                obstacle_max_height_m=round(obstacle, 2) if obstacle is not None else None,
                status=_cell_status(state, obstacle, alt, clearance),
            )
        )
    return out, res, truncated, states


def _known(agl: np.ndarray, plan: _Plan) -> np.ndarray:
    """Heights above ground with the unknown ones read as over flat ground at the take-off (NaN
    stays when not even that is known: zones then apply and no ceiling is judged)."""
    return np.where(np.isfinite(agl), agl, plan.ground_agl())


def _sample_points(geom: BaseGeometry) -> tuple[np.ndarray, np.ndarray]:
    """The geometry's vertices and (for polygons) a grid of interior points."""
    coords = shapely.get_coordinates(geom)
    if coords.shape[0] > MAX_VERTEX_SAMPLES:
        stride = int(math.ceil(coords.shape[0] / MAX_VERTEX_SAMPLES))
        coords = np.vstack([coords[::stride], coords[-1:]])
    lon_parts = [coords[:, 0]]
    lat_parts = [coords[:, 1]]
    poly = polygonal_part(geom)
    if poly is not None:
        area_m2 = geodesic_area_perimeter(poly)[0]
        step = max(TERRAIN_STEP_M, math.sqrt(max(area_m2, 1.0) / INTERIOR_SAMPLES))
        g_lons, g_lats, _ = grid_points(poly, step, INTERIOR_SAMPLES)
        lon_parts.append(np.asarray(g_lons, dtype=np.float64))
        lat_parts.append(np.asarray(g_lats, dtype=np.float64))
    return np.concatenate(lon_parts), np.concatenate(lat_parts)


def _sample_agl(lons: np.ndarray, lats: np.ndarray, plan: _Plan, dem: RasterSampler | None) -> np.ndarray | float:
    if not plan.terrain_true:
        return plan.value
    ground = np.asarray(dem.sample(lons, lats), dtype=np.float64) if dem is not None else np.full(lons.shape, np.nan)
    return _known(plan.agl(ground), plan)


def _unique(names: list[str]) -> str:
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    if len(seen) > 3:
        return ", ".join(seen[:3]) + f" and {len(seen) - 3} more"
    return ", ".join(seen)


def _subject(geom_type: str, area_m2: float | None, length_m: float | None) -> str:
    if area_m2 is not None and area_m2 > 0:
        if area_m2 >= 1e6:
            return f"The {area_m2 / 1e6:.2f} km² area"
        return f"The {area_m2 / 1e4:.2f} ha area"
    if length_m is not None and length_m > 0:
        if length_m >= 1000:
            return f"The {length_m / 1000:.2f} km route"
        return f"The {length_m:.0f} m route"
    if geom_type in ("Point", "MultiPoint"):
        return "The location" if geom_type == "Point" else "The locations"
    return "The geometry"


class _HeightsEvidence:
    """Terrain-true height evidence of an above-take-off or AMSL plan, for the summary."""

    def __init__(
        self,
        *,
        plan: _Plan,
        assessed: bool,
        agl_min: float | None,
        agl_max: float | None,
        min_agl: float,
        below: list[HeightSegment],
        above: list[HeightSegment],
        cells_below: int | None,
        cells_above: int | None,
        terrain_above: bool,
    ) -> None:
        self.plan = plan
        self.assessed = assessed
        self.agl_min = agl_min
        self.agl_max = agl_max
        self.min_agl = min_agl
        self.below = below
        self.above = above
        self.cells_below = cells_below
        self.cells_above = cells_above
        self.terrain_above = terrain_above

    @property
    def planned(self) -> str:
        return reference_phrase(self.plan.ref, self.plan.value)

    @property
    def below_minimum(self) -> bool:
        if self.below or self.cells_below:
            return True
        return self.agl_min is not None and self.agl_min < self.min_agl - LIMIT_TOLERANCE_M

    def sentence(self) -> str:
        if not self.assessed:
            return "Heights above ground were not assessed: no DEM relates the planned height to the ground."
        parts: list[str] = []
        level = self.plan.level
        where = f"Flight level {level:,.0f} m AMSL" if level is not None else f"At {self.planned}"
        if self.plan.ref == "takeoff" and self.plan.takeoff_elev is not None:
            where += f" (take-off ground {self.plan.takeoff_elev:,.0f} m AMSL)"
        if self.agl_min is not None and self.agl_max is not None:
            parts.append(f"{where}: from {self.agl_min:,.0f} m to {self.agl_max:,.0f} m above ground.")
        if self.terrain_above:
            parts.append("The ground rises above the flight level.")
        elif self.below:
            total = sum(s.length_m for s in self.below)
            noun = "stretch" if len(self.below) == 1 else "stretches"
            parts.append(
                f"Below the {self.min_agl:g} m minimum above ground for {total:,.0f} m of the route "
                f"({len(self.below)} {noun})."
            )
        elif self.cells_below:
            parts.append(f"Below the {self.min_agl:g} m minimum above ground in {self.cells_below} grid cells.")
        elif self.below_minimum:
            parts.append(f"Below the {self.min_agl:g} m minimum above ground over the highest ground.")
        if self.above:
            total = sum(s.length_m for s in self.above)
            parts.append(f"Above the free-flight ceiling for {total:,.0f} m of the route.")
        elif self.cells_above:
            parts.append(f"Above the free-flight ceiling in {self.cells_above} grid cells.")
        return " ".join(parts)


def _summary(
    *,
    subject: str,
    alt: float,
    red_names: list[str],
    permission_names: list[str],
    permission: bool,
    effective: float | None,
    obstacles: list[ObstacleHit],
    clearance: float,
    search_m: float,
    pack: DataPack | None,
    gap: str | None = None,
    heights: _HeightsEvidence | None = None,
) -> str:
    parts: list[str] = [gap] if gap else []
    # check is the highest AGL reached; None when no DEM ties the plan to the ground, so
    # theres nothing to compare the ceiling with.
    planned = heights.planned if heights is not None else f"{alt:g} m AGL"
    check = heights.agl_max if heights is not None else alt
    if red_names:
        parts.append(f"{subject} touches a red zone ({_unique(red_names)}); flight there is not allowed.")
    elif permission:
        where = f" ({_unique(permission_names)})" if permission_names else ""
        parts.append(f"{subject} lies in a zone that requires permission{where}.")
    elif effective is not None and check is not None and effective < check:
        if heights is None:
            parts.append(f"{subject} is limited to {effective:g} m AGL without permission, below the planned {alt:g} m.")
        else:
            parts.append(
                f"{subject} reaches {check:,.0f} m above ground at {planned}, above the {effective:g} m AGL "
                "free-flight ceiling."
            )
    elif gap and not gap.startswith(PARTLY_ASSESSED):
        # Never word an unchecked area as allowed: only user zones and default rules were applied.
        parts.append(f"{subject} was checked at {planned} against user zones and the default rules only.")
    elif gap or check is None:
        # Partly outside the pack, or a height above ground nobody knows: no "allowed" wording.
        parts.append(f"{subject} was checked at {planned}.")
    elif effective is not None:
        if heights is None:
            parts.append(f"{subject} allows {alt:g} m AGL without permission (free-flight ceiling {effective:g} m).")
        else:
            parts.append(f"{subject} stays within the {effective:g} m AGL free-flight ceiling at {planned}.")
    else:
        parts.append(f"{subject} was checked at {planned}.")
    if heights is not None:
        text = heights.sentence()
        if text:
            parts.append(text)
    conflicts = [o for o in obstacles if o.conflict]
    if conflicts:
        noun = "obstacle" if len(conflicts) == 1 else "obstacles"
        parts.append(f"{len(conflicts)} {noun} would leave less than {clearance:g} m vertical clearance.")
    elif obstacles:
        noun = "obstacle" if len(obstacles) == 1 else "obstacles"
        parts.append(f"{len(obstacles)} {noun} within {search_m:g} m, none in conflict.")
    if pack is None:
        parts.append("No data pack was available, so aerodromes, terrain and obstacles were not checked.")
    return " ".join(parts)


def _obstacle_layer(hits: list[ObstacleHit]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": hit.id,
                "geometry": {"type": "Point", "coordinates": [hit.lon, hit.lat]},
                "properties": hit.model_dump(mode="json", exclude={"lon", "lat"}),
            }
            for hit in hits
        ],
    }


def run(params: AirspaceParams, ctx: AnalysisContext) -> AirspaceResult:
    """Run the airspace & terrain check (see the module docstring)."""
    ctx.step("Reading geometry", 0.02)
    geom = _prepare_geometry(params.geometry, ctx)
    rules = profile_rules(params.zone_profile, params.zone_rules)
    pack = ctx.datapack
    window: TimeWindow | None = None
    if params.planned_start_utc is not None and params.planned_end_utc is not None:
        window = (params.planned_start_utc, params.planned_end_utc)
    zkw: dict[str, Any] = {
        "user_zones": [*params.user_zones, *params.dataset_zones],
        "include_datapack_zones": params.include_datapack_zones,
        "operational": params.aerodrome_operational,
        "window": window,
    }
    alt = float(params.altitude_agl_m)
    clearance = float(params.vertical_clearance_m)
    geom_type = geometry_type_name(geom)
    poly = polygonal_part(geom)
    lines = lineal_part(geom)
    area_m2 = round(float(geodesic_area_perimeter(poly)[0]), 2) if poly is not None else None
    length_m = round(float(geodesic_length(lines)), 2) if lines is not None else None
    _coverage_notes(ctx, pack, geom)
    dem = pack.dem() if pack is not None else None
    plan = _Plan(
        params,
        takeoff_point(geom, params.takeoff, dem, params.takeoff_elevation_m)
        if params.height_reference != "agl"
        else None,
    )

    ctx.step("Nearest aerodromes", 0.08)
    nearest = nearest_aerodromes(geom, pack, 5, rules=rules, operational=params.aerodrome_operational)

    ctx.step("Terrain", 0.3)
    step = TERRAIN_STEP_M if poly is not None else float(params.profile_spacing_m)
    terrain = terrain_summary(geom, dem, step)
    profile: list[ProfilePoint] = []
    profile_states: list[ZoneState] = []
    if lines is not None and length_m is not None and length_m > 0:
        profile, profile_states = _build_profile(lines, length_m, params, ctx, pack, rules, dem, plan, zkw)
    max_terrain: float | None = terrain.max_m if terrain is not None else None
    if max_terrain is None:
        heights = [p.terrain_m for p in profile if p.terrain_m is not None]
        max_terrain = max(heights) if heights else None
    max_amsl = round(max_terrain + alt, 2) if max_terrain is not None else None

    ctx.step("Obstacles", 0.45)
    obstacles = obstacle_hits(
        geom, pack, search_m=float(params.obstacle_search_m),
        altitude_agl_m=plan.flat_agl() if plan.terrain_true else alt,
        vertical_clearance_m=clearance, dem=dem, limit=OBSTACLE_LIMIT, aircraft_amsl_m=plan.level,
    )

    grid: list[FlyabilityCell] = []
    grid_res: int | None = None
    grid_truncated = False
    grid_states: list[ZoneState] = []
    if params.grid:
        ctx.step("Flyability grid", 0.55)
        grid, grid_res, grid_truncated, grid_states = _build_grid(geom, params, ctx, pack, rules, dem, plan, zkw)

    ctx.step("Heights above ground", 0.7)
    hev = _heights_evidence(params, plan, ctx, geom, terrain, profile, grid)

    ctx.step("Airspace zones", 0.75)
    # Zones are judged at the highest planned height above ground (unknown: as over flat ground).
    flat = plan.ground_agl()
    reach = hev.agl_max if hev.agl_max is not None else (flat if math.isfinite(flat) else None)
    hits, inactive = zone_hits_by_activity(geom, pack=pack, rules=rules, altitude_agl_m=reach, **zkw)
    sample_lons, sample_lats = _sample_points(geom)
    sample_agl = _sample_agl(sample_lons, sample_lats, plan, dem)
    bands = _band_evidence(profile, grid, poly is not None, sample_lons, sample_lats, sample_agl, plan, pack, rules, params)
    _zone_notes(ctx, rules, hits, inactive, params, window)

    ctx.step("Effective ceiling", 0.8)
    states = classify_points(sample_lons, sample_lats, pack=pack, rules=rules, agl=sample_agl, **zkw) + profile_states
    # Grid cells extend 50 m beyond lines/points; only polygon grids describe the geometry itself.
    if poly is not None and lines is None:
        states += grid_states
    red_names = [h.name for h in hits if h.zone == "red"] + [s.name for s in states if s.zone == "red"]
    permission_names = [h.name for h in hits if h.permission_required and h.zone != "red"]
    permission_names += [s.name for s in states if s.permission_required and s.zone != "red"]
    permission = bool(permission_names)
    ceilings = [h.ceiling_agl_m for h in hits if h.ceiling_agl_m is not None and not h.permission_required]
    ceilings += [s.ceiling_agl_m for s in states if s.ceiling_agl_m is not None and not s.permission_required]
    no_free_flight = permission or bool(red_names)
    effective = None if no_free_flight else (round(float(min(ceilings)), 2) if ceilings else None)

    conflict = any(o.conflict for o in obstacles)
    gap = assessment_gap(pack, geom)
    evidence = hev.evidence
    # Above-take-off / AMSL plans compare the ceiling with the highest AGL. None when no DEM
    # relates the plan to the ground: heights_assessed is then False, the verdict at best caution.
    check = evidence.agl_max if evidence is not None else alt
    height_caution = evidence is not None and (evidence.below_minimum or not evidence.assessed)
    above_effective = effective is not None and check is not None and effective < check
    if red_names or (evidence is not None and evidence.terrain_above):
        verdict = Verdict.FAIL
    elif permission or above_effective or conflict or gap is not None or height_caution:
        # Unchecked aerodrome zones (or unassessed heights) are never an all-clear.
        verdict = Verdict.CAUTION
    else:
        verdict = Verdict.PASS
    if gap is not None:
        _note_once(
            ctx,
            "not_assessed",
            f"{gap} Treat the area as unverified and check it on DGCA Digital Sky ({DIGITAL_SKY_URL}).",
            Severity.WARNING,
        )

    ctx.step("Summary", 0.92)
    summary = _summary(
        subject=_subject(geom_type, area_m2, length_m),
        alt=alt,
        red_names=red_names,
        permission_names=permission_names,
        permission=permission,
        effective=effective,
        obstacles=obstacles,
        clearance=clearance,
        search_m=float(params.obstacle_search_m),
        pack=pack,
        gap=gap,
        heights=evidence,
    )
    band_text = _band_text(bands)
    if band_text:
        summary = f"{summary} {band_text}"

    zones_layer = ring_polygons(pack, rules, BBox.from_bounds(geom.bounds), params.aerodrome_operational)
    touched = touched_polygon_zones(
        geom, pack=pack, rules=rules, user_zones=zkw["user_zones"],
        include_datapack_zones=params.include_datapack_zones, window=window,
    )
    zones_layer["features"].extend(touched["features"])
    input_props: dict[str, Any] = {"role": "input", "altitude_agl_m": alt, "verdict": verdict.value}
    if plan.terrain_true:
        input_props.update(height_reference=plan.ref, flight_level_amsl_m=_round_or_none(
            plan.level if plan.level is not None else math.nan))
    layers: dict[str, Any] = {
        "input": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "input",
                    "geometry": geometry_to_geojson(geom, 8),
                    "properties": input_props,
                }
            ],
        },
        "zones": zones_layer,
        "obstacles": _obstacle_layer(obstacles),
    }
    if plan.terrain_true:
        layers["heights"] = _stretch_layer(profile, hev.below, hev.above)
        if plan.takeoff is not None:
            layers["takeoff"] = _takeoff_layer(plan.takeoff)
    if evidence is not None:
        # A constant level is the top of the flight; without one, the highest ground + highest AGL.
        if plan.level is not None:
            max_amsl = round(plan.level, 2)
        else:
            max_amsl = round(max_terrain + check, 2) if max_terrain is not None and check is not None else None

    ctx.step("Done", 1.0)
    return AirspaceResult(
        verdict=verdict,
        summary=summary,
        geometry_type=geom_type,
        area_m2=area_m2,
        length_m=length_m,
        altitude_agl_m=alt,
        effective_ceiling_agl_m=effective,
        permission_required=permission,
        max_terrain_m=max_terrain,
        max_altitude_amsl_m=max_amsl,
        nearest_aerodromes=nearest,
        zones=hits,
        terrain=terrain,
        profile=profile,
        obstacles=obstacles,
        grid=grid,
        grid_resolution=grid_res,
        grid_truncated=grid_truncated,
        assessment_complete=gap is None,
        issues=list(ctx.issues),
        disclaimer=DISCLAIMER_AIRSPACE,
        layers=layers,
        height_reference=plan.ref,
        # The minimum applies where the AGL varies (above take-off / AMSL); None: not checked.
        min_agl_m=float(params.min_agl_m) if plan.terrain_true else None,
        takeoff=plan.takeoff,
        flight_level_amsl_m=_round_or_none(plan.level) if plan.level is not None else None,
        agl_min_m=hev.agl_min,
        agl_max_m=hev.agl_max,
        below_min_agl=hev.below,
        above_ceiling=hev.above,
        cells_below_min_agl=hev.cells_below,
        cells_above_ceiling=hev.cells_above,
        heights_assessed=evidence.assessed if evidence is not None else True,
        dem=dem_info(pack),
        zone_profile=params.zone_profile,
        band_evidence=bands,
        inactive_zones=inactive,
        assessment_banner=f"{RED_ZONES_NOTICE} Rules: {ZONE_PROFILE_LABELS[params.zone_profile]}.",
    )


def _band_evidence(
    profile: list[ProfilePoint],
    grid: list[FlyabilityCell],
    is_area: bool,
    lons: np.ndarray,
    lats: np.ndarray,
    agl: np.ndarray | float,
    plan: _Plan,
    pack: DataPack | None,
    rules: ZoneRules,
    params: AirspaceParams,
) -> list[BandEvidence]:
    """Bands reached by the route's profile samples, else an area's grid cells, else the vertices."""
    kind: Literal["profile", "grid", "vertices"] = "vertices"
    if len(profile) > 1:
        kind = "profile"
        lons = np.array([p.lon for p in profile], dtype=np.float64)
        lats = np.array([p.lat for p in profile], dtype=np.float64)
        agl = _known(np.array([np.nan if p.agl_m is None else p.agl_m for p in profile], dtype=np.float64), plan)
    elif grid and is_area:
        kind = "grid"
        lons, lats = cell_centroids([c.h3 for c in grid])
        agl = _known(np.array([np.nan if c.agl_m is None else c.agl_m for c in grid], dtype=np.float64), plan) \
            if plan.terrain_true else plan.value
    return band_evidence(lons, lats, agl, pack=pack, rules=rules, operational=params.aerodrome_operational,
                         sample_kind=kind)


def _zone_notes(
    ctx: AnalysisContext,
    rules: ZoneRules,
    hits: list[ZoneHit],
    inactive: list[ZoneHit],
    params: AirspaceParams,
    window: TimeWindow | None,
) -> None:
    if rules.distance_from == "perimeter":
        approx = sorted({h.ident or h.name for h in hits if h.source == "aerodrome" and h.distance_from == "reference_point"})
        hull = sorted({h.ident or h.name for h in hits if h.source == "aerodrome" and h.distance_from == "runway_hull"})
        if approx:
            _note_once(ctx, "perimeter_approximated",
                       f"The bands of {_unique(approx)} were measured from the reference point minus an assumed "
                       "radius: the data pack has no boundary or runways for them, so the bands may be out by "
                       "up to half the airport's length.", Severity.WARNING)
        if hull:
            _note_once(ctx, "perimeter_from_runways",
                       f"The bands of {_unique(hull)} were measured from the hull of the runways (no mapped "
                       "boundary): aprons and buildings beyond the runways are not included.", Severity.INFO)
    off = sorted(k for k, v in params.aerodrome_operational.items() if not v)
    if off:
        _note_once(ctx, "aerodrome_not_operational",
                   f"Marked not operational for this check, so their bands were not applied: {_unique(off)}. "
                   "Confirm the status with the airport operator or on Digital Sky.", Severity.WARNING)
    if inactive:
        noun = "zone does" if len(inactive) == 1 else "zones do"
        _note_once(ctx, "zones_inactive",
                   f"{len(inactive)} {noun} not apply to this flight (outside the planned window or clear of its "
                   "heights) and are listed separately.", Severity.INFO)
    timed = [z for z in [*params.user_zones, *params.dataset_zones] if z.valid_from or z.valid_to or z.schedule]
    if window is None and timed:
        noun = "zone has" if len(timed) == 1 else "zones have"
        _note_once(ctx, "zone_window_not_checked",
                   f"{len(timed)} {noun} a validity period but no planned window was given, so they were "
                   "treated as active.", Severity.INFO)
    if any(z.vertical_reference == "WGS84" and (z.lower_limit_m is not None or z.upper_limit_m is not None)
           for z in [*params.user_zones, *params.dataset_zones]):
        _note_once(ctx, "vertical_reference_approximated",
                   "Some zone limits are WGS84 (ellipsoid) heights: without a geoid model they were widened by "
                   "the geoid's range (−106 to +85 m) before comparing with the ground.", Severity.INFO)


def _band_text(bands: list[BandEvidence]) -> str:
    """Sentences on the aerodrome bands that limit the flight: unverified 0–5 km areas and bands
    flown above their ceiling."""
    parts: list[str] = []
    verify = [b for b in bands if b.basis == "verify_official_map"]
    if verify:
        names = _unique([b.ident or b.name for b in verify])
        parts.append(f"Part of the flight is within {verify[0].outer_km:g} km of the perimeter of {names}, which the "
                     "published rule does not define: verify on Digital Sky.")
    for b in bands:
        # only bands that turn into another zone above their ceiling; a plain ceiling is in the summary already
        if b.agl_margin_m is None or b.agl_margin_m >= 0 or b.worst_agl_m is None or b.ceiling_agl_m is None:
            continue
        if not b.permission_required or b.basis == "verify_official_map":
            continue
        where = f"the {b.band.split(':')[0]} band of {b.ident or b.name}" if b.distance_from is not None else "open airspace"
        parts.append(f"In {where} the flight reaches {b.worst_agl_m:,.0f} m above ground, {-b.agl_margin_m:,.0f} m "
                     f"above its {b.ceiling_agl_m:g} m green ceiling: {b.zone} zone above it, permission required.")
    return " ".join(parts)


class _Heights:
    """Every plan's height evidence; ``evidence`` is set for above-take-off / AMSL plans only."""

    def __init__(
        self,
        *,
        agl_min: float | None,
        agl_max: float | None,
        below: list[HeightSegment],
        above: list[HeightSegment],
        cells_below: int | None,
        cells_above: int | None,
        evidence: _HeightsEvidence | None,
    ) -> None:
        self.agl_min = agl_min
        self.agl_max = agl_max
        self.below = below
        self.above = above
        self.cells_below = cells_below
        self.cells_above = cells_above
        self.evidence = evidence


def _heights_evidence(
    params: AirspaceParams,
    plan: _Plan,
    ctx: AnalysisContext,
    geom: BaseGeometry,
    terrain: TerrainSummary | None,
    profile: list[ProfilePoint],
    grid: list[FlyabilityCell],
) -> _Heights:
    """AGL range, stretches below the minimum / above the ceiling, and grid-cell counts.

    Every plan gets the stretches and grid cells above the ceiling (for an ``agl`` plan that
    only restates the planned value against the zone ceilings). The minimum, the AGL range over
    the ground, the height notes and the summary evidence are for above-take-off and AMSL plans,
    whose AGL varies with the ground: an ``agl`` plan never checks a minimum.
    """
    min_agl = float(params.min_agl_m)
    alt = float(params.altitude_agl_m)
    flat = plan.flat_agl()
    below: list[HeightSegment] = []
    above: list[HeightSegment] = []
    if len(profile) > 1:
        distance = np.array([p.distance_m for p in profile], dtype=np.float64)
        agl = np.array([np.nan if p.agl_m is None else p.agl_m for p in profile], dtype=np.float64)
        limits = np.array([np.nan if p.limit_agl_m is None else p.limit_agl_m for p in profile], dtype=np.float64)
        if plan.terrain_true:
            below = height_segments(distance, agl, min_agl, "below")
        above = height_segments(distance, agl, limits, "above")
    cells_below: int | None = None
    cells_above: int | None = None
    if grid:
        cell_heights = [
            (c.agl_m if c.agl_m is not None else (flat if plan.terrain_true else alt), c) for c in grid
        ]
        if plan.terrain_true:
            cells_below = sum(1 for h, _ in cell_heights if h < min_agl - LIMIT_TOLERANCE_M)
        cells_above = sum(
            1 for h, c in cell_heights if c.ceiling_agl_m is not None and h > c.ceiling_agl_m + LIMIT_TOLERANCE_M
        )
    if not plan.terrain_true:
        return _Heights(agl_min=alt, agl_max=alt, below=below, above=above, cells_below=cells_below,
                        cells_above=cells_above, evidence=None)

    # Ground under the flight: the terrain summary over the geometry, the route profile and (for
    # areas only: the grid of a line reaches 50 m beyond it) the grid cells.
    ground_parts: list[float] = []
    if terrain is not None:
        ground_parts += [terrain.min_m, terrain.max_m]
    ground_parts += [p.terrain_m for p in profile if p.terrain_m is not None]
    if polygonal_part(geom) is not None and lineal_part(geom) is None:
        ground_parts += [c.terrain_m for c in grid if c.terrain_m is not None]
    ground = np.array(ground_parts if ground_parts else [np.nan], dtype=np.float64)
    for issue in height_issues(plan.ref, ground, plan.takeoff_elev):
        _note_once(ctx, issue.code, issue.message, issue.severity)
    known = bool(np.isfinite(ground).any())
    assessed = known and (plan.ref == "amsl" or plan.takeoff_elev is not None)
    agl = plan.agl(ground)
    finite = agl[np.isfinite(agl)]
    if finite.size:
        agl_min: float | None = round(float(np.min(finite)), 2)
        agl_max: float | None = round(float(np.max(finite)), 2)
    elif math.isfinite(flat) and (plan.ref == "takeoff" or plan.takeoff_elev is not None):
        agl_min = agl_max = round(flat, 2)
    else:
        agl_min = agl_max = None
    terrain_above = assessed and agl_min is not None and agl_min <= 0.0
    if terrain_above and plan.level is not None:
        _note_once(
            ctx,
            "terrain_above_flight_level",
            f"The ground rises to {float(np.nanmax(ground)):,.0f} m AMSL, above the flight level of "
            f"{plan.level:,.0f} m AMSL: the plan would meet the terrain (or roofs and canopy, in a "
            "surface model).",
            Severity.CRITICAL,
        )
    evidence = _HeightsEvidence(
        plan=plan, assessed=assessed, agl_min=agl_min, agl_max=agl_max, min_agl=min_agl,
        below=below, above=above, cells_below=cells_below, cells_above=cells_above,
        terrain_above=terrain_above,
    )
    return _Heights(agl_min=agl_min, agl_max=agl_max, below=below, above=above, cells_below=cells_below,
                    cells_above=cells_above, evidence=evidence)


def _profile_position(profile: list[ProfilePoint], distance_m: float) -> list[float]:
    d = np.array([p.distance_m for p in profile], dtype=np.float64)
    lon = float(np.interp(distance_m, d, [p.lon for p in profile]))
    lat = float(np.interp(distance_m, d, [p.lat for p in profile]))
    return [round(lon, 8), round(lat, 8)]


def _stretch_layer(
    profile: list[ProfilePoint], below: list[HeightSegment], above: list[HeightSegment]
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for kind, stretches in (("below_min_agl", below), ("above_ceiling", above)):
        for i, s in enumerate(stretches):
            inner = [[p.lon, p.lat] for p in profile if s.start_m < p.distance_m < s.end_m]
            coords = [_profile_position(profile, s.start_m), *inner, _profile_position(profile, s.end_m)]
            features.append({
                "type": "Feature",
                "id": f"{kind}-{i + 1}",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"kind": kind, **s.model_dump(mode="json")},
            })
    return {"type": "FeatureCollection", "features": features}


def _takeoff_layer(takeoff: TakeoffPoint) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "id": "takeoff",
            "geometry": {"type": "Point", "coordinates": [takeoff.lon, takeoff.lat]},
            "properties": {"role": "takeoff", **takeoff.model_dump(mode="json", exclude={"lon", "lat"})},
        }],
    }
