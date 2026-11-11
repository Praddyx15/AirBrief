"""eVTOL planning: vertiport siting and corridor routing (docs/SPEC.md §3, "eVTOL").

**Siting** (:func:`run_siting`; requires population and DEM)

1. H3 cells whose centres fall inside the area polygon (``max_cells`` may coarsen or truncate).
2. Five criteria per cell, each normalised to 0..1:

   * *demand* — people within ``demand_radius_km``: Σ density × cell area over the H3 grid disk
     of radius ``k = ring_radius_k(res, radius)``, divided by the maximum over the area. When the
     disk would be very large (``k`` > :data:`MAX_DEMAND_K`) the disk is evaluated at a coarser
     resolution on the cell's parent, which keeps the cost bounded;
   * *access* — ``1 − min(d / access_radius_km, 1)`` with ``d`` the geodesic distance (km) to
     the nearest point of interest (stations); 0 when the pack has no POIs;
   * *airspace* — the zone at the site, on the ground: green 1, yellow with a free-flight ceiling
     0.5, yellow requiring permission 0, red 0 (or excluded when ``exclude_red_zones``). A band
     the published rule does not define (the DGCA 2021 0–5 km band, "verify on Digital Sky") may
     be red: it scores 0 like permission, is not excluded (it is not known to be red, and an
     airport can host a vertiport), and its sites are marked (``zone_basis``, a note, an issue
     ``verify_official_map``). With ``unverified_band = "exclude"`` its cells are excluded like
     red ones instead (reason ``unverified_band``, whatever ``exclude_red_zones`` says), and the
     issue names the band;
   * *obstacles* — ``1 − min(L / 5, 1)`` where ``L`` is the height-weighted obstacle count within
     ``obstacle_radius_m``: each obstacle counts ``clip(height / 50 m, 0.5, 3)``;
   * *slope* — ``1 − min(slope / max_slope_deg, 1)``.

3. Hard exclusions: water (``PolygonIndex.contains_xy``), red zones, the band to verify (with
   ``unverified_band = "exclude"``), slope above the maximum and cells without terrain data.
4. Score = weighted mean of the criteria × 100. The top ``top_n`` candidates are picked greedily
   in score order, skipping any closer than ``min_separation_km`` to one already chosen.

**Corridor** (:func:`run_corridor`; requires population, DEM optional)

1. Study area = ``study_area`` or the origin/destination bounding box (local metres) plus
   ``study_margin_km``. H3 cells over it; the resolution is coarsened (with an issue) so the cell
   count stays within ``max_cells``. The origin and destination cells are always included.
2. Graph: cells are nodes, edges join neighbouring cells. Edge cost = geodesic distance ×
   (1 + w_gr·density_norm + w_air·airspace_penalty + w_obs·obstacle_penalty), with penalties the
   mean of the two cells. airspace_penalty: yellow needing permission 1.0, yellow with a ceiling
   0.3, red 1.0 (when not avoided), green 0. Zones are judged at the cruise height above ground
   (``cruise_altitude_agl_m``; at a fixed AMSL level, the level minus the cell's ground), so an
   aerodrome band flown above its ceiling counts as its zone above it (DGCA 2021 8–12 km above
   60 m: yellow, 1.0). The DGCA 0–5 km band to verify on Digital Sky may hold red zones: it costs
   1.0 like permission and the result names it (issue ``verify_official_map``); it is never free.
   Open airspace above its ceiling (yellow above 120 m under DGCA 2021) is the same in every cell,
   so it adds no cost and is an issue (``open_airspace_above_ceiling``) instead.
   obstacle_penalty = min(1, tallest obstacle in the cell / (cruise − clearance)).
   density_norm = centroid density / maximum in the study area.
3. Red zones are impassable when ``avoid_red_zones`` (cells whose centre is classified red, plus
   cells whose hexagon touches a red data-pack zone). Cells holding an obstacle taller than
   ``cruise_altitude_agl_m − obstacle_clearance_m`` are impassable. With ``unverified_band =
   "exclude"`` so are cells whose centre is in the band to verify, like red ones, and the issue
   ``verify_official_map`` names it. Rules without such a band (the conservative ones) are not
   changed by it, and an issue says so.
4. Path: ``networkx.astar_path`` with a (slightly scaled-down, hence admissible) great-circle
   heuristic. No path → ``AnalysisError("no_route")``.
5. Route = origin, path cell centroids, destination; simplified to 20 m in a LocalFrame (kept
   unsimplified if simplification would cut into a red zone). Corridor = route buffered by
   ``corridor_width_m / 2``.
6. Length (geodesic), straight-line distance, detour %, time = length / speed, energy =
   length × Wh/km. Terrain profile every ``profile_spacing_m``; required cruise level AMSL =
   max(highest terrain under the corridor + cruise AGL, highest obstacle top AMSL + clearance).
7. Noise: L(r) = L_ref − 20·log10(r / r_ref) with slant range r = √(h² + d²). The ground
   footprint half-width is √(r_t² − h²) where r_t = r_ref·10^((L_ref − T)/20) is the distance at
   which the level falls to the threshold T. Cells whose centre lies within the half-width of the
   route are exposed; each reports its level and population (density × cell area).
8. Cruise reference (:mod:`.heights`). ``agl`` (default, unchanged): the cruise level is the
   required level of step 6 and the profile's ``agl_m`` is that level minus the terrain.
   ``amsl``: the corridor is flown at ``cruise_altitude_amsl_m``; each cell's obstacle limit is
   that level minus its ground minus the clearance (step 3), the profile's ``agl_m`` is the level
   minus the terrain, obstacle clearance is measured from it, and the noise height ``h`` is the
   lowest height above ground along the route. Both modes report the AGL range along the route,
   the stretches below ``min_agl_m`` and above ``ceiling_agl_m`` (when given) and the origin as
   the take-off point; ``amsl`` adds ``below_min_agl`` / ``terrain_above_flight_level`` notes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import h3
import networkx as nx
import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from ..errors import AnalysisError
from ..geo.frame import LocalFrame
from ..geo.geodesic import distance_m, geodesic_area_perimeter, geodesic_length
from ..geo.h3grid import (
    cell_area_m2,
    cell_centroids,
    cell_polygon,
    cells_for_polygon,
    neighbours,
    ring_radius_k,
)
from ..geo.sampling import grid_points
from ..geo.transform import geojson_to_geometry, geometry_to_geojson
from ..geo.validity import drop_z, polygonal_part, repair
from ..models.analyses import (
    EvtolCorridorParams,
    EvtolCorridorResult,
    EvtolSitingParams,
    EvtolSitingResult,
    NoiseCell,
    NoiseSummary,
    SiteCandidate,
    SitingCell,
)
from ..models.common import Issue, ObstacleHit, ProfilePoint, Severity, ZoneHit
from ..models.heights import HeightSegment, TakeoffPoint
from ..models.presets import DIGITAL_SKY_URL, DISCLAIMER_EVTOL, profile_rules
from .base import AnalysisContext
from .heights import dem_info, height_issues, height_segments, takeoff_point
from .terrain import obstacle_hits, terrain_profile
from .zones import ZoneState, classify_points, has_unverified_band, unverified_bands, zones_for_geometry

if TYPE_CHECKING:
    from ..data.datapack import DataPack
    from ..data.raster import RasterSampler
    from ..data.vectors import PointFeature, PointIndex

__all__ = [
    "MAX_DEMAND_K",
    "ROUTE_SIMPLIFY_M",
    "noise_half_width_m",
    "noise_level_db",
    "run_corridor",
    "run_siting",
]

MAX_DEMAND_K = 8
"""Largest grid-disk radius evaluated for the demand criterion before coarsening the disk."""

OBSTACLE_REF_HEIGHT_M = 50.0
"""An obstacle of this height counts once in the siting obstacle load."""

OBSTACLE_LOAD_CAP = 5.0
"""Height-weighted obstacle count at which the siting obstacle score reaches 0."""

ROUTE_SIMPLIFY_M = 20.0
"""Douglas-Peucker tolerance (m) applied to the routed polyline."""

MIN_STUDY_MARGIN_M = 1000.0
"""Smallest margin (m) around the origin/destination box, so a detour is always possible."""

AIRSPACE_PENALTY_PERMISSION = 1.0
AIRSPACE_PENALTY_CEILING = 0.3
AIRSPACE_PENALTY_RED = 1.0

_HEURISTIC_SCALE = 0.99  # sphere vs ellipsoid margin keeps the A* heuristic admissible
_EARTH_RADIUS_M = 6_371_008.8
_DENSITY_STEP_M = 100.0
_DENSITY_MAX_POINTS = 50_000

_CRITERIA = ("demand", "access", "airspace", "obstacles", "slope")


def _feature(geometry: BaseGeometry, properties: dict[str, Any], fid: str | None = None) -> dict[str, Any]:
    feat: dict[str, Any] = {
        "type": "Feature",
        "geometry": geometry_to_geojson(geometry, 8),
        "properties": properties,
    }
    if fid is not None:
        feat["id"] = fid
    return feat


def _fc(features: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


def _area_from_geojson(obj: dict[str, Any]) -> BaseGeometry:
    geom = geojson_to_geometry(obj)
    if geom.is_empty:
        raise AnalysisError("invalid_geometry", "The area geometry is empty")
    geom = drop_z(geom)
    fixed, _ = repair(geom)
    poly = polygonal_part(fixed)
    if poly is None or poly.is_empty:
        raise AnalysisError(
            "geometry_type_not_supported",
            "An area (polygon) is required",
            details={"geometry_type": geom.geom_type},
        )
    return poly


def _height_m(feature: PointFeature) -> float | None:
    """Obstacle height above ground (m) from ``height_m``; None when missing or invalid."""
    value = feature.get("height_m")
    if value is None or isinstance(value, bool):
        return None
    try:
        height = float(value)
    except (TypeError, ValueError):
        return None
    return height if math.isfinite(height) and height >= 0.0 else None


def _clean(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    return np.where(np.isfinite(arr) & (arr > 0.0), arr, 0.0)


def _round_opt(value: float | None, digits: int) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _nearest_points(
    frame: LocalFrame, lons: np.ndarray, lats: np.ndarray, index: PointIndex
) -> tuple[list[PointFeature | None], np.ndarray]:
    """Nearest feature of ``index`` for each point and its geodesic distance in km (NaN if none)."""
    n = int(lons.size)
    if n == 0 or len(index) == 0:
        return [None] * n, np.full(n, np.nan)
    px, py = frame.xy(index.lons, index.lats)
    ok = np.isfinite(px) & np.isfinite(py)
    if not ok.any():
        return [None] * n, np.full(n, np.nan)
    tree = cKDTree(np.column_stack([px[ok], py[ok]]))
    cx, cy = frame.xy(lons, lats)
    _, nearest = tree.query(np.column_stack([cx, cy]), k=1)
    feat_idx = np.nonzero(ok)[0][np.asarray(nearest, dtype=np.int64)]
    dist = np.asarray(
        distance_m(lons, lats, index.lons[feat_idx], index.lats[feat_idx]), dtype=np.float64
    ).reshape(n)
    return [index[int(j)] for j in feat_idx], dist / 1000.0


def _obstacle_load(
    frame: LocalFrame, lons: np.ndarray, lats: np.ndarray, index: PointIndex, radius_m: float
) -> tuple[np.ndarray, np.ndarray]:
    """Height-weighted obstacle load and plain count within ``radius_m`` of each point."""
    n = int(lons.size)
    load = np.zeros(n)
    count = np.zeros(n, dtype=np.int64)
    if n == 0 or len(index) == 0:
        return load, count
    px, py = frame.xy(index.lons, index.lats)
    raw_heights = [_height_m(f) for f in index.features]
    heights = np.array(
        [OBSTACLE_REF_HEIGHT_M if h is None else h for h in raw_heights], dtype=np.float64
    )
    ok = np.isfinite(px) & np.isfinite(py)
    if not ok.any():
        return load, count
    weights = np.clip(heights[ok] / OBSTACLE_REF_HEIGHT_M, 0.5, 3.0)
    tree = cKDTree(np.column_stack([px[ok], py[ok]]))
    cx, cy = frame.xy(lons, lats)
    hits = tree.query_ball_point(np.column_stack([cx, cy]), r=float(radius_m))
    for i, members in enumerate(hits):
        if members:
            count[i] = len(members)
            load[i] = float(weights[np.asarray(members, dtype=np.int64)].sum())
    return load, count


def _siting_airspace_score(state: ZoneState) -> float:
    if state.zone == "green":
        return 1.0
    if state.zone == "yellow":
        return 0.0 if state.permission_required else 0.5
    return 0.0


def _demand_people(
    cells: list[str], res: int, radius_m: float, pop: RasterSampler, ctx: AnalysisContext
) -> np.ndarray:
    """People within ``radius_m`` of each cell: Σ density × cell area over a grid disk."""
    if not cells:
        return np.zeros(0)
    dres = int(res)
    k = ring_radius_k(dres, radius_m)
    while k > MAX_DEMAND_K and dres > 0:
        dres -= 1
        k = ring_radius_k(dres, radius_m)
    keys = [h3.cell_to_parent(c, dres) if dres < res else c for c in cells]
    unique = sorted(set(keys))
    disks: dict[str, list[str]] = {}
    members: set[str] = set()
    for i, key in enumerate(unique):
        if i % 2000 == 0:
            ctx.check_cancelled()
        disk = list(h3.grid_disk(key, k))
        disks[key] = disk
        members.update(disk)
    ordered = sorted(members)
    m_lons, m_lats = cell_centroids(ordered)
    density = _clean(pop.density_per_km2(m_lons, m_lats))
    areas_km2 = np.array([cell_area_m2(c) for c in ordered], dtype=np.float64) / 1e6
    people = dict(zip(ordered, (density * areas_km2).tolist(), strict=True))
    per_key = {key: float(sum(people[c] for c in disk)) for key, disk in disks.items()}
    return np.array([per_key[key] for key in keys], dtype=np.float64)


def _siting_notes(
    state: ZoneState,
    slope: float,
    max_slope: float,
    obstacle_count: int,
    poi: PointFeature | None,
    poi_km: float,
    access_radius_km: float,
    obstacle_radius_m: float,
) -> list[str]:
    notes: list[str] = []
    if state.basis == "verify_official_map":
        notes.append(f"{state.name}, {state.label.split(':')[0]} band: not defined by the published rule and may be a "
                     "red zone. Verify on Digital Sky; permission required.")
    elif state.zone == "yellow":
        if state.permission_required:
            notes.append(f"Yellow zone ({state.label}): permission required.")
        elif state.ceiling_agl_m is not None:
            notes.append(f"Yellow zone ({state.label}): ceiling {state.ceiling_agl_m:g} m AGL.")
        else:
            notes.append(f"Yellow zone ({state.label}).")
    elif state.zone == "red":
        notes.append(f"Red zone ({state.label}): flight not permitted.")
    if math.isfinite(slope) and slope > 0.75 * max_slope:
        notes.append(f"Slope {slope:.1f}° is close to the {max_slope:g}° limit.")
    if obstacle_count:
        notes.append(f"{obstacle_count} obstacle(s) within {obstacle_radius_m:g} m.")
    if poi is not None and math.isfinite(poi_km):
        name = str(poi.get("name") or poi.id)
        if poi_km > access_radius_km:
            notes.append(f"Nearest station {name} is {poi_km:.1f} km away (beyond {access_radius_km:g} km).")
        else:
            notes.append(f"Nearest station {name} at {poi_km:.1f} km.")
    return notes


def run_siting(params: EvtolSitingParams, ctx: AnalysisContext) -> EvtolSitingResult:
    """Score H3 cells over an area for vertiport suitability and pick separated top candidates."""
    pack = ctx.require("population", "dem")
    pop = pack.population()
    dem = pack.dem()
    if pop is None or dem is None:
        raise AnalysisError("layer_missing", "The data pack lacks a population or DEM raster")
    issues: list[Issue] = []
    weights = params.weights
    rules = profile_rules(params.zone_profile, params.zone_rules)

    ctx.step("Preparing area", 0.02)
    area = _area_from_geojson(params.geometry)

    ctx.step("Building H3 grid", 0.08)
    cells, truncated = cells_for_polygon(area, params.h3_resolution, params.max_cells)
    if not cells:
        raise AnalysisError("invalid_geometry", "The area does not cover any H3 cell")
    res = int(h3.get_resolution(cells[0]))
    if truncated:
        issues.append(Issue(
            code="cells_truncated",
            message=(
                f"The area needs more than {params.max_cells} cells at resolution "
                f"{params.h3_resolution}; evaluated {len(cells)} cells at resolution {res}."
            ),
            severity=Severity.WARNING,
        ))
    n = len(cells)
    lons, lats = cell_centroids(cells)

    ctx.step("Estimating demand", 0.15)
    demand_people = _demand_people(cells, res, params.demand_radius_km * 1000.0, pop, ctx)
    max_demand = float(demand_people.max()) if n else 0.0
    if max_demand > 0.0:
        demand = demand_people / max_demand
    else:
        demand = np.zeros(n)
        issues.append(Issue(
            code="no_population",
            message="No population found around the area; the demand criterion is 0 everywhere.",
            severity=Severity.WARNING,
        ))

    ctx.step("Measuring access", 0.35)
    frame = LocalFrame.for_geometry(area)
    pois = pack.pois()
    nearest_poi, poi_km = _nearest_points(frame, lons, lats, pois)
    if len(pois) == 0:
        issues.append(Issue(
            code="no_pois",
            message="The data pack has no points of interest; the access criterion is 0 everywhere.",
            severity=Severity.INFO,
        ))
    access = np.where(
        np.isfinite(poi_km), 1.0 - np.minimum(np.nan_to_num(poi_km, nan=0.0) / params.access_radius_km, 1.0), 0.0
    )

    ctx.step("Classifying airspace", 0.5)
    states = classify_points(lons, lats, pack=pack, rules=rules)
    airspace = np.array([_siting_airspace_score(s) for s in states], dtype=np.float64)
    red = np.array([s.zone == "red" for s in states], dtype=bool)
    exclude_band = params.unverified_band == "exclude"
    bands: list[str | None] = unverified_bands(lons, lats, pack=pack, rules=rules) if exclude_band else [None] * n

    ctx.step("Counting obstacles", 0.6)
    load, obstacle_count = _obstacle_load(frame, lons, lats, pack.obstacles(), params.obstacle_radius_m)
    obstacles = 1.0 - np.minimum(load / OBSTACLE_LOAD_CAP, 1.0)

    ctx.step("Sampling terrain", 0.7)
    elevation = np.asarray(dem.sample(lons, lats), dtype=np.float64)
    slope = np.asarray(dem.slope_deg(lons, lats), dtype=np.float64)
    slope_ok = np.isfinite(slope)
    slope_score = np.where(slope_ok, 1.0 - np.minimum(np.nan_to_num(slope) / params.max_slope_deg, 1.0), 0.0)

    water = np.zeros(n, dtype=bool)
    if params.exclude_water:
        water = np.asarray(pack.water().contains_xy(lons, lats), dtype=bool)

    ctx.step("Scoring cells", 0.8)
    total_w = weights.demand + weights.access + weights.airspace + weights.obstacles + weights.slope
    score = (
        weights.demand * demand
        + weights.access * access
        + weights.airspace * airspace
        + weights.obstacles * obstacles
        + weights.slope * slope_score
    ) / total_w * 100.0

    reasons: list[str | None] = []
    for i in range(n):
        if water[i]:
            reasons.append("water")
        elif red[i] and params.exclude_red_zones:
            reasons.append("red_zone")
        elif bands[i] is not None:
            reasons.append("unverified_band")
        elif not slope_ok[i]:
            reasons.append("no_terrain_data")
        elif slope[i] > params.max_slope_deg:
            reasons.append("slope")
        else:
            reasons.append(None)
    excluded = np.array([r is not None for r in reasons], dtype=bool)
    eligible = [i for i in range(n) if not excluded[i]]

    ctx.step("Selecting candidates", 0.9)
    order = sorted(eligible, key=lambda i: (-float(score[i]), cells[i]))
    min_sep_m = params.min_separation_km * 1000.0
    chosen: list[int] = []
    for pos, i in enumerate(order):
        if len(chosen) >= params.top_n:
            break
        if pos % 2000 == 0:
            ctx.check_cancelled()
        if chosen and min_sep_m > 0.0:
            sel = np.asarray(chosen, dtype=np.int64)
            dist = np.asarray(
                distance_m(np.full(sel.size, lons[i]), np.full(sel.size, lats[i]), lons[sel], lats[sel]),
                dtype=np.float64,
            )
            if np.any(dist < min_sep_m):
                continue
        chosen.append(i)

    if not eligible:
        issues.append(Issue(
            code="all_cells_excluded",
            message=(
                f"Every cell was excluded (water, red zone{', band to verify' if exclude_band else ''}, slope or missing "
                "terrain); no candidates."
            ),
            severity=Severity.WARNING,
        ))
    elif len(chosen) < params.top_n:
        issues.append(Issue(
            code="fewer_candidates",
            message=(
                f"Only {len(chosen)} candidate(s) satisfy the {params.min_separation_km:g} km "
                f"minimum separation (requested {params.top_n})."
            ),
            severity=Severity.INFO,
        ))

    candidates: list[SiteCandidate] = []
    for rank, i in enumerate(chosen, start=1):
        poi = nearest_poi[i]
        km = float(poi_km[i])
        candidates.append(SiteCandidate(
            rank=rank,
            h3=cells[i],
            lon=round(float(lons[i]), 8),
            lat=round(float(lats[i]), 8),
            score=round(float(score[i]), 2),
            breakdown={
                "demand": round(float(demand[i]), 4),
                "access": round(float(access[i]), 4),
                "airspace": round(float(airspace[i]), 4),
                "obstacles": round(float(obstacles[i]), 4),
                "slope": round(float(slope_score[i]), 4),
            },
            elevation_m=_round_opt(float(elevation[i]), 1),
            slope_deg=_round_opt(float(slope[i]), 2),
            population_nearby=round(float(demand_people[i]), 0),
            nearest_poi=None if poi is None else str(poi.get("name") or poi.id),
            nearest_poi_km=_round_opt(km, 3),
            zone=states[i].zone,
            notes=_siting_notes(
                states[i], float(slope[i]), params.max_slope_deg, int(obstacle_count[i]), poi, km,
                params.access_radius_km, params.obstacle_radius_m,
            ),
            zone_basis=states[i].basis,
        ))
    unverified = [f"#{c.rank}" for c in candidates if c.zone_basis == "verify_official_map"]
    if unverified:
        issues.append(Issue(
            code="verify_official_map",
            message=(
                f"Site{'s' if len(unverified) > 1 else ''} {', '.join(unverified)} lie in an aerodrome band the "
                "published rule does not define, which may hold red zones: scored as permission required, not "
                f"excluded. Verify on Digital Sky ({DIGITAL_SKY_URL})."
            ),
            severity=Severity.WARNING,
        ))
    banded = [b for b in bands if b is not None]
    if banded:
        issues.append(Issue(
            code="verify_official_map",
            message=(
                f"{len(banded)} cell{'s' if len(banded) != 1 else ''} in {', '.join(sorted(set(banded)))} "
                f"{'were' if len(banded) != 1 else 'was'} excluded like a red zone (unverified_band: exclude): a band "
                f"the published rule does not define, which may hold red zones. Verify on Digital Sky ({DIGITAL_SKY_URL})."
            ),
            severity=Severity.WARNING,
        ))

    cell_rows = [
        SitingCell(
            h3=cells[i],
            score=None if excluded[i] else round(float(score[i]), 2),
            excluded=bool(excluded[i]),
            reason=reasons[i],
        )
        for i in range(n)
    ]

    if not exclude_band:
        band_text = ", as is a band the published rule does not define (it may be red: such sites are marked 'verify on Digital Sky')"
    elif has_unverified_band(rules):
        band_text = "; a band the published rule does not define (it may be red) is excluded like a red zone"
    else:
        band_text = " (these rules have no band to verify on Digital Sky, so excluding one changes nothing)"
    criteria = {
        "demand": (
            f"People within {params.demand_radius_km:g} km (H3 grid disk), normalised by the "
            "highest value in the area."
        ),
        "access": (
            f"1 − distance to the nearest station / {params.access_radius_km:g} km (0 beyond)."
        ),
        "airspace": (
            "The zone at the site, on the ground. Green 1.0; yellow with a free-flight ceiling 0.5; yellow "
            "requiring permission 0.0"
            + band_text
            + ("; red excluded." if params.exclude_red_zones else "; red 0.0.")
        ),
        "obstacles": (
            f"1 − height-weighted obstacle count within {params.obstacle_radius_m:g} m / "
            f"{OBSTACLE_LOAD_CAP:g} (each obstacle counts height / {OBSTACLE_REF_HEIGHT_M:g} m, "
            "between 0.5 and 3)."
        ),
        "slope": (
            f"1 − terrain slope / {params.max_slope_deg:g}°; steeper cells are excluded."
        ),
    }

    ctx.step("Building layers", 0.96)
    candidate_features = [
        _feature(
            Point(c.lon, c.lat),
            {
                "rank": c.rank,
                "score": c.score,
                "h3": c.h3,
                "zone": c.zone,
                "nearest_poi": c.nearest_poi,
                "nearest_poi_km": c.nearest_poi_km,
                "population_nearby": c.population_nearby,
            },
            fid=f"candidate-{c.rank}",
        )
        for c in candidates
    ]
    layers = {
        "input": _fc([_feature(area, {"role": "input"}, fid="input")]),
        "candidates": _fc(candidate_features),
    }

    return EvtolSitingResult(
        candidates=candidates,
        cells=cell_rows,
        cells_evaluated=n,
        cells_excluded=int(excluded.sum()),
        cells_truncated=truncated,
        weights=weights,
        criteria=criteria,
        disclaimer=DISCLAIMER_EVTOL,
        issues=[*ctx.issues, *issues],
        layers=layers,
        zone_profile=params.zone_profile,
        unverified_band=params.unverified_band,
    )


def noise_half_width_m(level_db_at_ref: float, ref_distance_m: float, threshold_db: float, height_m: float) -> float:
    """Ground half-width (m) within which the level reaches ``threshold_db``.

    The threshold slant range is ``r_t = r_ref · 10^((L_ref − T)/20)``; on the ground directly
    beside the track the half-width is ``√(r_t² − h²)``, or 0 when ``r_t ≤ h``.
    """
    r_t = float(ref_distance_m) * 10.0 ** ((float(level_db_at_ref) - float(threshold_db)) / 20.0)
    h = max(float(height_m), 0.0)
    if r_t <= h:
        return 0.0
    return math.sqrt(r_t * r_t - h * h)


def noise_level_db(level_db_at_ref: float, ref_distance_m: float, slant_m: np.ndarray | float) -> np.ndarray:
    """Spherical-spreading level ``L_ref − 20·log10(r / r_ref)`` at slant range(s) ``r``."""
    r = np.maximum(np.asarray(slant_m, dtype=np.float64), 1e-3)
    return float(level_db_at_ref) - 20.0 * np.log10(r / float(ref_distance_m))


def _corridor_airspace_penalty(state: ZoneState) -> float:
    if state.zone == "red":
        return AIRSPACE_PENALTY_RED
    if state.zone == "yellow":
        return AIRSPACE_PENALTY_PERMISSION if state.permission_required else AIRSPACE_PENALTY_CEILING
    return 0.0


def _study_area(params: EvtolCorridorParams, issues: list[Issue]) -> BaseGeometry:
    o_lon, o_lat = params.origin
    d_lon, d_lat = params.destination
    if params.study_area is not None:
        study = _area_from_geojson(params.study_area)
        outside = [
            label
            for label, (lon, lat) in (("origin", params.origin), ("destination", params.destination))
            if not study.covers(Point(lon, lat))
        ]
        if outside:
            issues.append(Issue(
                code="endpoint_outside_study_area",
                message=(
                    f"The {' and '.join(outside)} lie(s) outside the study area; the route can only "
                    "use cells inside it."
                ),
                severity=Severity.WARNING,
            ))
        return study
    frame = LocalFrame.for_geometry(LineString([(o_lon, o_lat), (d_lon, d_lat)]))
    xs, ys = frame.xy([o_lon, d_lon], [o_lat, d_lat])
    margin = max(params.study_margin_km * 1000.0, MIN_STUDY_MARGIN_M)
    rect = shapely.box(
        float(xs.min()) - margin, float(ys.min()) - margin, float(xs.max()) + margin, float(ys.max()) + margin
    )
    rect = shapely.segmentize(rect, max(margin, 1000.0))
    return frame.to_wgs84(rect)


def _corridor_cells(
    study: BaseGeometry, params: EvtolCorridorParams, issues: list[Issue]
) -> tuple[list[str], int, str, str]:
    """Cells over the study area at a resolution that fits ``max_cells`` (+ O/D cells)."""
    area_m2, _ = geodesic_area_perimeter(study)
    res = int(params.h3_resolution)
    while res > 0 and area_m2 / float(h3.average_hexagon_area(res, unit="m^2")) > params.max_cells:
        res -= 1
    cells, truncated = cells_for_polygon(study, res, params.max_cells)
    actual = int(h3.get_resolution(cells[0])) if cells else res
    if actual != params.h3_resolution:
        issues.append(Issue(
            code="grid_coarsened",
            message=(
                f"The study area needs more than {params.max_cells} cells at resolution "
                f"{params.h3_resolution}; routed on resolution {actual} instead."
            ),
            severity=Severity.WARNING,
        ))
    elif truncated:
        issues.append(Issue(
            code="cells_truncated",
            message=f"The study area was truncated to the {params.max_cells} cells nearest its centre.",
            severity=Severity.WARNING,
        ))
    o_cell = str(h3.latlng_to_cell(params.origin[1], params.origin[0], actual))
    d_cell = str(h3.latlng_to_cell(params.destination[1], params.destination[0], actual))
    cell_set = set(cells)
    cell_set.update((o_cell, d_cell))
    return sorted(cell_set), actual, o_cell, d_cell


def _red_zone_union(pack: DataPack) -> BaseGeometry | None:
    geoms: list[BaseGeometry] = []
    for zone in pack.zones():
        if zone.zone != "red":
            continue
        try:
            geom = polygonal_part(geojson_to_geometry(zone.geometry))
        except AnalysisError:
            continue
        if geom is not None and not geom.is_empty:
            geoms.append(geom if geom.is_valid else shapely.make_valid(geom))
    if not geoms:
        return None
    return shapely.union_all(geoms)


def _cells_touching(cells: list[str], lons: np.ndarray, lats: np.ndarray, geom: BaseGeometry, res: int) -> np.ndarray:
    """Boolean mask of cells whose hexagon intersects ``geom``."""
    mask = np.zeros(len(cells), dtype=bool)
    minx, miny, maxx, maxy = geom.bounds
    # A hexagon reaches at most one (max) edge length from its centre; use two for safety.
    margin_lat = 2.0 * float(h3.average_hexagon_edge_length(res, unit="m")) * 1.5 / 110_000.0
    cos_lat = max(math.cos(math.radians(min(89.0, max(abs(miny), abs(maxy)) + margin_lat))), 0.05)
    margin_lon = margin_lat / cos_lat
    near = np.nonzero(
        (lons >= minx - margin_lon) & (lons <= maxx + margin_lon)
        & (lats >= miny - margin_lat) & (lats <= maxy + margin_lat)
    )[0]
    if near.size == 0:
        return mask
    polys = np.empty(near.size, dtype=object)
    for k, i in enumerate(near):
        polys[k] = cell_polygon(cells[int(i)])
    shapely.prepare(geom)
    mask[near] = np.asarray(shapely.intersects(geom, polys), dtype=bool)
    return mask


def _simplify_route(
    raw: LineString, frame: LocalFrame, origin: tuple[float, float], destination: tuple[float, float],
    cells: Sequence[BaseGeometry] = (), raw_local: BaseGeometry | None = None,
) -> LineString:
    """The cell route smoothed to ``ROUTE_SIMPLIFY_M``, or the cell route itself when the smoothed
    line would touch one of ``cells`` (impassable, never the endpoints' own) further than 1 m from it.
    ``raw_local`` is ``raw`` in ``frame``, when the caller has projected it already."""
    local = frame.to_local(raw) if raw_local is None else raw_local
    simplified = frame.to_wgs84(local.simplify(ROUTE_SIMPLIFY_M, preserve_topology=False))
    coords = [(float(x), float(y)) for x, y, *_ in simplified.coords] if not simplified.is_empty else []
    if len(coords) < 2:
        coords = [origin, destination]
    coords[0] = origin
    coords[-1] = destination
    route = LineString(coords)
    polys = np.asarray(cells, dtype=object)
    touched = polys[np.asarray(shapely.intersects(route, polys), dtype=bool)]
    if touched.size:
        away = frame.to_wgs84(frame.to_local(route).difference(local.buffer(1.0)))
        if np.any(shapely.intersects(away, touched)):
            return raw
    return route


def run_corridor(params: EvtolCorridorParams, ctx: AnalysisContext) -> EvtolCorridorResult:
    """Route a least-cost eVTOL corridor between two points over an H3 cost graph."""
    pack = ctx.require("population")
    pop = pack.population()
    if pop is None:
        raise AnalysisError("layer_missing", "The data pack lacks a population raster")
    dem = pack.dem()
    issues: list[Issue] = []
    rules = profile_rules(params.zone_profile, params.zone_rules)
    weights = params.weights
    origin = (float(params.origin[0]), float(params.origin[1]))
    destination = (float(params.destination[0]), float(params.destination[1]))
    straight_m = float(distance_m(origin[0], origin[1], destination[0], destination[1]))
    if straight_m < 1.0:
        raise AnalysisError("invalid_params", "Origin and destination coincide")
    if dem is None:
        issues.append(Issue(
            code="dem_missing",
            message="No DEM in the data pack: the terrain profile and required cruise level are omitted.",
            severity=Severity.WARNING,
        ))
    amsl_level = (
        float(params.cruise_altitude_amsl_m)
        if params.cruise_reference == "amsl" and params.cruise_altitude_amsl_m is not None
        else None
    )
    takeoff = takeoff_point(None, origin, dem, params.origin_elevation_m, source="origin")

    ctx.step("Preparing study area", 0.03)
    study = _study_area(params, issues)

    ctx.step("Building H3 grid", 0.08)
    cells, res, o_cell, d_cell = _corridor_cells(study, params, issues)
    n = len(cells)
    index = {c: i for i, c in enumerate(cells)}
    lons, lats = cell_centroids(cells)

    ctx.step("Sampling population", 0.15)
    density = _clean(pop.density_per_km2(lons, lats))
    dmax = float(density.max()) if n else 0.0
    density_norm = density / dmax if dmax > 0.0 else np.zeros(n)

    ctx.step("Classifying airspace", 0.22)
    cell_ground = np.asarray(dem.sample(lons, lats), dtype=np.float64) if amsl_level is not None and dem is not None else None
    cruise_agl: float | np.ndarray = float(params.cruise_altitude_agl_m)
    if amsl_level is not None and cell_ground is not None:
        cruise_agl = np.where(np.isfinite(cell_ground), amsl_level - cell_ground, cruise_agl)
    # Open airspace above its ceiling is the same in every cell: an issue below, not a cost.
    cost_rules = rules.model_copy(update={"default_ceiling_exceeded_zone": None})
    states = classify_points(lons, lats, pack=pack, rules=cost_rules, agl=cruise_agl)
    air_pen = np.array([_corridor_airspace_penalty(s) for s in states], dtype=np.float64)
    red = np.array([s.zone == "red" for s in states], dtype=bool)
    red_union = _red_zone_union(pack)
    if red_union is not None:
        touching = _cells_touching(cells, lons, lats, red_union, res)
        red |= touching
        air_pen = np.where(touching, np.maximum(air_pen, AIRSPACE_PENALTY_RED), air_pen)

    ctx.step("Mapping obstacles", 0.3)
    limit_m = params.cruise_altitude_agl_m - params.obstacle_clearance_m
    max_h = np.zeros(n)
    has_obs = np.zeros(n, dtype=bool)
    minx, miny, maxx, maxy = study.bounds
    for f in pack.obstacles().in_bbox((minx - 0.05, miny - 0.05, maxx + 0.05, maxy + 0.05)):
        h = _height_m(f)
        if h is None:
            continue
        j = index.get(str(h3.latlng_to_cell(f.lat, f.lon, res)))
        if j is None:
            continue
        has_obs[j] = True
        max_h[j] = max(max_h[j], h)
    if amsl_level is not None and cell_ground is not None:
        # A fixed level: the room under it changes with the ground of each cell (step 8).
        cell_limit = np.where(
            np.isfinite(cell_ground), amsl_level - cell_ground - params.obstacle_clearance_m, limit_m
        )
        obs_blocked = has_obs & (max_h > cell_limit)
        obs_pen = np.where(has_obs, np.minimum(max_h / np.maximum(cell_limit, 1.0), 1.0), 0.0)
    else:
        obs_blocked = has_obs & (max_h > limit_m)
        obs_pen = np.where(has_obs, np.minimum(max_h / max(limit_m, 1.0), 1.0), 0.0)

    bands: list[str | None] = (
        unverified_bands(lons, lats, pack=pack, rules=rules) if params.unverified_band == "exclude" else [None] * n
    )
    passable = ~obs_blocked & ~np.array([b is not None for b in bands], dtype=bool)
    if params.avoid_red_zones:
        passable &= ~red
    o_i, d_i = index[o_cell], index[d_cell]
    for label, i in (("origin", o_i), ("destination", d_i)):
        if not passable[i]:
            if red[i] and params.avoid_red_zones:
                why = "a red zone"
            elif bands[i] is not None:
                why = f"{bands[i]}, a band to verify on Digital Sky (excluded)"
            else:
                why = "a tall obstacle"
            issues.append(Issue(
                code=f"{label}_blocked",
                message=f"The {label} lies in a cell blocked by {why}; it is allowed only as an endpoint.",
                severity=Severity.WARNING,
            ))
            passable[i] = True

    ctx.step("Building cost graph", 0.38)
    graph: nx.Graph = nx.Graph()
    graph.add_nodes_from(np.nonzero(passable)[0].tolist())
    src: list[int] = []
    dst: list[int] = []
    for i, cell in enumerate(cells):
        if i % 2000 == 0:
            ctx.check_cancelled()
        if not passable[i]:
            continue
        for nb in neighbours(cell):
            j = index.get(nb)
            if j is not None and j > i and passable[j]:
                src.append(i)
                dst.append(j)
    if src:
        s = np.asarray(src, dtype=np.int64)
        t = np.asarray(dst, dtype=np.int64)
        dist = np.asarray(distance_m(lons[s], lats[s], lons[t], lats[t]), dtype=np.float64)
        factor = (
            1.0
            + weights.ground_risk * (density_norm[s] + density_norm[t]) / 2.0
            + weights.airspace * (air_pen[s] + air_pen[t]) / 2.0
            + weights.obstacles * (obs_pen[s] + obs_pen[t]) / 2.0
        )
        graph.add_weighted_edges_from(zip(s.tolist(), t.tolist(), (dist * factor).tolist(), strict=True))

    rad_lon = np.radians(lons)
    rad_lat = np.radians(lats)

    def heuristic(u: int, v: int) -> float:
        dlat = rad_lat[v] - rad_lat[u]
        dlon = rad_lon[v] - rad_lon[u]
        a = math.sin(dlat / 2.0) ** 2 + math.cos(rad_lat[u]) * math.cos(rad_lat[v]) * math.sin(dlon / 2.0) ** 2
        return _HEURISTIC_SCALE * 2.0 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))

    ctx.step("Searching route", 0.48)
    try:
        path: list[int] = nx.astar_path(graph, o_i, d_i, heuristic=heuristic, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
        raise AnalysisError(
            "no_route",
            "No route connects the origin and destination without crossing blocked cells (red zones"
            + (", excluded bands to verify on Digital Sky" if any(b is not None for b in bands) else "")
            + " or obstacles above cruise − clearance). Enlarge the study area or relax the constraints.",
            details={"cells": n, "blocked": int((~passable).sum()), "resolution": res},
        ) from exc
    ctx.check_cancelled()

    ctx.step("Shaping corridor", 0.58)
    raw_coords = [origin, *[(float(lons[i]), float(lats[i])) for i in path], destination]
    raw = LineString(raw_coords)
    frame = LocalFrame.for_geometry(raw)
    # Every impassable cell (red, an excluded band, a tall obstacle) must survive the smoothing;
    # the endpoints' own were made passable above. Only those the 20 m smoothing can reach: a
    # cell spans at most two edge lengths from its centroid.
    shut = np.nonzero(~passable)[0]
    sx, sy = frame.xy(lons[shut], lats[shut])
    reach = 2.0 * float(h3.average_hexagon_edge_length(res, unit="m")) + ROUTE_SIMPLIFY_M
    raw_local = frame.to_local(raw)
    near = shut[np.asarray(shapely.distance(raw_local, shapely.points(sx, sy)), dtype=np.float64) <= reach]
    route = _simplify_route(raw, frame, origin, destination, cells=[cell_polygon(cells[i]) for i in near],
                            raw_local=raw_local)
    corridor = frame.to_wgs84(frame.to_local(route).buffer(params.corridor_width_m / 2.0, quad_segs=8))
    if not corridor.is_valid:
        corridor = shapely.make_valid(corridor)

    length_m = geodesic_length(route)
    length_km = length_m / 1000.0
    straight_km = straight_m / 1000.0
    detour_pct = max(0.0, (length_m / straight_m - 1.0) * 100.0)
    flight_time_min = length_km / params.cruise_speed_kmh * 60.0
    energy_kwh = length_km * params.energy_wh_per_km / 1000.0

    ctx.step("Terrain profile", 0.66)
    profile: list[ProfilePoint] = []
    max_terrain: float | None = None
    if dem is not None:
        profile = terrain_profile(route, dem, params.profile_spacing_m)
        terrain_vals = [p.terrain_m for p in profile if p.terrain_m is not None and math.isfinite(p.terrain_m)]
        corridor_stats = dem.stats(corridor, step_m=_DENSITY_STEP_M, max_points=_DENSITY_MAX_POINTS)
        if corridor_stats.get("max") is not None:
            terrain_vals.append(float(corridor_stats["max"]))
        max_terrain = max(terrain_vals) if terrain_vals else None

    ctx.step("Checking obstacles", 0.74)
    hits: list[ObstacleHit] = obstacle_hits(
        route,
        pack,
        search_m=params.corridor_width_m / 2.0,
        altitude_agl_m=params.cruise_altitude_agl_m,
        vertical_clearance_m=params.obstacle_clearance_m,
        dem=dem,
        aircraft_amsl_m=amsl_level,
    )
    conflicts = [h for h in hits if h.conflict]
    if conflicts:
        issues.append(Issue(
            code="obstacle_conflict",
            message=(
                f"{len(conflicts)} obstacle(s) within the corridor infringe the "
                f"{params.obstacle_clearance_m:g} m clearance below the cruise altitude."
            ),
            severity=Severity.WARNING,
        ))

    required_amsl: float | None = None
    if max_terrain is not None:
        required_amsl = max_terrain + params.cruise_altitude_agl_m
        for hit in hits:
            if hit.top_amsl_m is not None and math.isfinite(hit.top_amsl_m):
                required_amsl = max(required_amsl, hit.top_amsl_m + params.obstacle_clearance_m)
        flown = required_amsl if amsl_level is None else amsl_level
        profile = [
            p.model_copy(update={
                "altitude_amsl_m": round(flown, 1),
                "agl_m": round(flown - p.terrain_m, 1),
            })
            if p.terrain_m is not None and math.isfinite(p.terrain_m)
            else p
            for p in profile
        ]
    heights = _corridor_heights(params, profile, amsl_level, takeoff, dem is not None, issues)
    # At a fixed AMSL level the worst case is the lowest height above ground.
    noise_h = params.cruise_altitude_agl_m
    if amsl_level is not None and heights.agl_min is not None:
        noise_h = max(heights.agl_min, 0.0)

    ctx.step("Checking airspace", 0.8)
    # at the highest height above ground along the route, as the airspace check judges a plan
    top = heights.agl_max if heights.agl_max is not None else float(params.cruise_altitude_agl_m)
    zone_hits: list[ZoneHit] = zones_for_geometry(corridor, pack=pack, rules=rules, altitude_agl_m=top)
    unverified = sorted({f"{z.ident or z.name} {(z.ring or '').split(':')[0]}".strip()
                         for z in zone_hits if z.basis == "verify_official_map"})
    excluded = sorted({b for b in bands if b is not None})
    if params.unverified_band == "exclude" and not has_unverified_band(rules):
        issues.append(Issue(
            code="unverified_band_not_applicable",
            message="These rules have no band to verify on Digital Sky, so excluding such bands changed nothing.",
            severity=Severity.INFO,
        ))
    elif params.unverified_band == "exclude" and (excluded or unverified):
        said = f"Cells in {', '.join(excluded)} were treated like a red zone: impassable (unverified_band: exclude). " if excluded else ""
        if unverified:
            said += f"The corridor still touches {', '.join(unverified)} (an endpoint, or the edge of a cell beside it). "
        issues.append(Issue(
            code="verify_official_map",
            message=(
                f"{said}A band the published rule does not define may hold red zones; verify it on Digital Sky "
                f"({DIGITAL_SKY_URL})."
            ),
            severity=Severity.WARNING,
        ))
    elif unverified:
        issues.append(Issue(
            code="verify_official_map",
            message=(
                f"The corridor crosses {', '.join(unverified)}: a band the published rule does not define, which "
                "may hold red zones. It was costed as permission required, never as free; verify it on Digital "
                f"Sky ({DIGITAL_SKY_URL})."
            ),
            severity=Severity.WARNING,
        ))
    above = rules.default_ceiling_exceeded_zone
    if above is not None and rules.default_zone != "red" and top > rules.default_ceiling_agl_m:
        issues.append(Issue(
            code="open_airspace_above_ceiling",
            message=(
                f"The route flies up to {top:,.0f} m above ground, over the {rules.default_ceiling_agl_m:g} m ceiling "
                f"of open airspace: {above} zone there under these rules, so permission is needed along the "
                "whole route, not only near aerodromes."
            ),
            severity=Severity.WARNING,
        ))
    if any(z.zone == "red" for z in zone_hits):
        issues.append(Issue(
            code="red_zone_crossed",
            message="The corridor touches a red zone.",
            severity=Severity.CRITICAL if params.avoid_red_zones else Severity.WARNING,
        ))
    if any(z.zone == "yellow" and z.permission_required for z in zone_hits):
        issues.append(Issue(
            code="permission_required",
            message="The corridor crosses yellow zone(s) where flight needs permission.",
            severity=Severity.INFO,
        ))

    ctx.step("Estimating noise", 0.86)
    half_width = noise_half_width_m(
        params.noise_level_db_at_ref, params.noise_ref_distance_m, params.noise_threshold_db,
        noise_h,
    )
    noise_cells: list[NoiseCell] = []
    exposed_population = 0.0
    exposed_area_km2 = 0.0
    if half_width > 0.0 and n:
        route_local = frame.to_local(route)
        cx, cy = frame.xy(lons, lats)
        rminx, rminy, rmaxx, rmaxy = route_local.bounds
        near = np.nonzero(
            (cx >= rminx - half_width) & (cx <= rmaxx + half_width)
            & (cy >= rminy - half_width) & (cy <= rmaxy + half_width)
        )[0]
        if near.size:
            d = np.asarray(shapely.distance(route_local, shapely.points(cx[near], cy[near])), dtype=np.float64)
            slant = np.sqrt(d * d + noise_h ** 2)
            levels = noise_level_db(params.noise_level_db_at_ref, params.noise_ref_distance_m, slant)
            for k in np.nonzero((d <= half_width) & (levels >= params.noise_threshold_db - 1e-9))[0]:
                i = int(near[int(k)])
                area_km2 = cell_area_m2(cells[i]) / 1e6
                people = float(density[i]) * area_km2
                exposed_population += people
                exposed_area_km2 += area_km2
                noise_cells.append(NoiseCell(h3=cells[i], level_db=round(float(levels[int(k)]), 1), population=round(people, 1)))
        noise_cells.sort(key=lambda c: (-c.level_db, c.h3))
    else:
        issues.append(Issue(
            code="noise_below_threshold",
            message=(
                f"At {noise_h:g} m AGL the modelled level on the ground stays below "
                f"{params.noise_threshold_db:g} dB."
            ),
            severity=Severity.INFO,
        ))
    noise = NoiseSummary(
        threshold_db=params.noise_threshold_db,
        footprint_half_width_m=round(half_width, 1),
        exposed_population=round(exposed_population, 0),
        exposed_area_km2=round(exposed_area_km2, 3),
        cells=noise_cells,
    )

    ctx.step("Density along corridor", 0.92)
    g_lons, g_lats, _ = grid_points(
        corridor, max(25.0, min(_DENSITY_STEP_M, params.corridor_width_m / 3.0)), _DENSITY_MAX_POINTS
    )
    g_dens = np.asarray(pop.density_per_km2(g_lons, g_lats), dtype=np.float64) if np.size(g_lons) else np.empty(0)
    g_dens = g_dens[np.isfinite(g_dens)]
    max_density = float(np.max(g_dens)) if g_dens.size else None
    mean_density = float(g_dens.mean()) if g_dens.size else None

    ctx.step("Building layers", 0.97)
    o_name = params.origin_name or "Origin"
    d_name = params.destination_name or "Destination"
    route_props: dict[str, Any] = {
        "name": f"{o_name} → {d_name}",
        "length_km": round(length_km, 3),
        "straight_km": round(straight_km, 3),
        "detour_pct": round(detour_pct, 2),
        "cruise_altitude_agl_m": params.cruise_altitude_agl_m,
        "required_cruise_amsl_m": _round_opt(required_amsl, 1),
    }
    if amsl_level is not None:
        route_props.update(cruise_reference="amsl", cruise_altitude_amsl_m=amsl_level)
    layers = {
        "route": _fc([_feature(route, route_props, fid="route")]),
        "corridor": _fc([_feature(corridor, {
            "name": f"{o_name} → {d_name} corridor",
            "width_m": params.corridor_width_m,
        }, fid="corridor")]),
        "endpoints": _fc([
            _feature(Point(*origin), {"role": "origin", "name": o_name}, fid="origin"),
            _feature(Point(*destination), {"role": "destination", "name": d_name}, fid="destination"),
        ]),
        "obstacles": _fc([
            _feature(Point(h.lon, h.lat), {
                "id": h.id,
                "kind": h.kind,
                "name": h.name,
                "height_m": h.height_m,
                "top_amsl_m": h.top_amsl_m,
                "distance_m": h.distance_m,
                "clearance_m": h.clearance_m,
                "conflict": h.conflict,
            }, fid=f"obstacle-{h.id}")
            for h in hits
        ]),
    }
    if amsl_level is not None:
        layers["heights"] = _fc([
            _feature(_route_stretch(profile, s), {"kind": kind, **s.model_dump(mode="json")}, fid=f"{kind}-{i + 1}")
            for kind, stretches in (("below_min_agl", heights.below), ("above_ceiling", heights.above))
            for i, s in enumerate(stretches)
        ])

    return EvtolCorridorResult(
        route=geometry_to_geojson(route, 8),
        corridor=geometry_to_geojson(corridor, 8),
        length_km=round(length_km, 3),
        straight_km=round(straight_km, 3),
        detour_pct=round(detour_pct, 2),
        flight_time_min=round(flight_time_min, 2),
        energy_kwh=round(energy_kwh, 2),
        cruise_altitude_agl_m=params.cruise_altitude_agl_m,
        required_cruise_amsl_m=_round_opt(required_amsl, 1),
        max_terrain_m=_round_opt(max_terrain, 1),
        profile=profile,
        obstacles=hits,
        zones=zone_hits,
        noise=noise,
        max_density_per_km2=_round_opt(max_density, 1),
        mean_density_per_km2=_round_opt(mean_density, 1),
        cells_considered=n,
        disclaimer=DISCLAIMER_EVTOL,
        issues=[*ctx.issues, *issues],
        layers=layers,
        cruise_reference=params.cruise_reference,
        cruise_altitude_amsl_m=amsl_level,
        min_agl_m=params.min_agl_m,
        ceiling_agl_m=params.ceiling_agl_m,
        takeoff=takeoff,
        agl_min_m=heights.agl_min,
        agl_max_m=heights.agl_max,
        below_min_agl=heights.below,
        above_ceiling=heights.above,
        heights_assessed=heights.assessed,
        dem=dem_info(pack),
        zone_profile=params.zone_profile,
        unverified_band=params.unverified_band,
    )


class _CorridorHeights:
    def __init__(
        self, agl_min: float | None, agl_max: float | None, below: list[HeightSegment],
        above: list[HeightSegment], assessed: bool,
    ) -> None:
        self.agl_min = agl_min
        self.agl_max = agl_max
        self.below = below
        self.above = above
        self.assessed = assessed


def _corridor_heights(
    params: EvtolCorridorParams,
    profile: list[ProfilePoint],
    amsl_level: float | None,
    takeoff: TakeoffPoint,
    has_dem: bool,
    issues: list[Issue],
) -> _CorridorHeights:
    """AGL range and stretches along the route (step 8); notes for a fixed AMSL level only."""
    distance = np.array([p.distance_m for p in profile], dtype=np.float64)
    agl = np.array([np.nan if p.agl_m is None else p.agl_m for p in profile], dtype=np.float64)
    finite = agl[np.isfinite(agl)]
    agl_min = round(float(np.min(finite)), 1) if finite.size else None
    agl_max = round(float(np.max(finite)), 1) if finite.size else None
    below = height_segments(distance, agl, params.min_agl_m, "below") if finite.size else []
    above = (
        height_segments(distance, agl, params.ceiling_agl_m, "above")
        if finite.size and params.ceiling_agl_m is not None
        else []
    )
    if amsl_level is None:
        return _CorridorHeights(agl_min, agl_max, below, above, True)
    ground = np.array([np.nan if p.terrain_m is None else p.terrain_m for p in profile] or [np.nan], dtype=np.float64)
    notes = height_issues("amsl", ground, takeoff.elevation_m)
    if not has_dem:
        # Without a DEM the corridor has no profile at all: no flat-ground heights are derived
        # (the general note would say the ground was taken as flat at a typed origin elevation).
        notes = [
            Issue(
                code="heights_not_assessed",
                message=(
                    f"No DEM in the data pack: the heights above ground along the route at {amsl_level:,.0f} m "
                    "AMSL were not assessed."
                ),
                severity=Severity.WARNING,
            ),
            *(i for i in notes if i.code != "heights_not_assessed"),
        ]
    issues.extend(notes)
    assessed = has_dem and bool(np.isfinite(ground).any())
    if agl_min is not None and agl_min <= 0.0:
        issues.append(Issue(
            code="terrain_above_flight_level",
            message=(
                f"The ground along the route rises above the cruise level of {amsl_level:,.0f} m AMSL "
                f"(lowest height above ground {agl_min:,.0f} m)."
            ),
            severity=Severity.CRITICAL,
        ))
    elif below:
        total = sum(s.length_m for s in below)
        issues.append(Issue(
            code="below_min_agl",
            message=(
                f"At {amsl_level:,.0f} m AMSL the route is below the {params.min_agl_m:g} m minimum above "
                f"ground for {total:,.0f} m ({len(below)} stretch{'es' if len(below) != 1 else ''})."
            ),
            severity=Severity.WARNING,
        ))
    return _CorridorHeights(agl_min, agl_max, below, above, assessed)


def _route_stretch(profile: list[ProfilePoint], s: HeightSegment) -> LineString:
    """The part of the route between two distances, from the profile samples (interpolated ends)."""
    d = np.array([p.distance_m for p in profile], dtype=np.float64)
    lons = np.array([p.lon for p in profile], dtype=np.float64)
    lats = np.array([p.lat for p in profile], dtype=np.float64)

    def at(x: float) -> tuple[float, float]:
        return float(np.interp(x, d, lons)), float(np.interp(x, d, lats))

    inner = [(float(lo), float(la)) for lo, la, di in zip(lons, lats, d, strict=True) if s.start_m < di < s.end_m]
    return LineString([at(s.start_m), *inner, at(s.end_m)])
