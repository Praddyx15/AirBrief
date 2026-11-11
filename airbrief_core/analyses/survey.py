"""Survey effort estimator: GSD, flight lines, photos, flight time and battery blocks.

Procedure (docs/SPEC.md §3, "Survey"; steps 10–15 in docs/features/heights.md):

1. GSD = sw·H/(f·iw); the altitude for a target GSD is gsd·f·iw/sw (sensor height and image
   height are used instead in portrait orientation, so the reported across-track GSD matches).
2. Footprint across = sw·H/f and along = sh·H/f (swapped in portrait).
3. Line spacing = across·(1 − side overlap); trigger distance = along·(1 − front overlap).
4. Direction = the bearing of the long axis of the minimum rotated rectangle of the area in a
   :class:`~airbrief_core.geo.frame.LocalFrame` (or the user's ``direction_deg``).
5. The area is rotated so the lines run along x; lines are spaced from minY + spacing/2 and
   clipped to the polygon. Segments are flown in boustrophedon (serpentine) order with straight
   connectors between them.
6. Photos = Σ(floor(segment/trigger) + 1) over the segments.
7. Flight minutes = (line length/speed + turns·turn_s + connector length/speed)/60, with one
   turn per connector.
8. Blocks: consecutive whole lines are grouped until a battery's time is used up; each block's
   polygon = area ∩ the strip covering its lines. Blocks are numbered from 1.
9. Batteries = Σ ceil(block time/battery) over the blocks: one per block, more only for a block
   whose single line outlasts a battery. Total minutes = flight + (batteries − 1)·swap. Packing
   whole lines can need one battery more than ceil(flight/battery); the blocks are what is flown.
10. Heights (:mod:`.heights`). H is read in ``height_reference``: ``agl`` assumes terrain
    following (the GSD stays the planned one); ``takeoff`` and ``amsl`` fly one level
    ``L = T + H`` or ``L = H`` (T: take-off ground elevation). The layout of steps 1–3 uses the
    height above the take-off ground ``H₀`` (``H`` above take-off; ``L − T`` for AMSL). With a
    DEM, each block's ground ``z_min..z_max`` gives ``H_eff = L − z`` from ``L − z_max`` to
    ``L − z_min`` and the GSD range ``gsd(H_eff)`` (GSD is proportional to the height). Over
    the highest ground the footprint shrinks by ``H_low / H₀`` while the line spacing ``S`` and
    trigger distance ``B`` stay, so the worst overlap is ``1 − (1 − p)·H₀ / H_low`` (end lap
    ``(G − B)/G`` with ground coverage ``G = d·H′/f``, Wolf, Dewitt & Wilkinson, *Elements of
    Photogrammetry*; the same for side lap). Ground above the level is a critical issue.
11. Corridor (``coverage = "corridor"``): the centreline is buffered by half of
    ``corridor_width_m`` (flat ends) in a LocalFrame; ``n = ceil(width / S)`` lines are flown
    along it at offsets ``(i − (n − 1)/2)·S`` (shapely ``offset_curve``), alternating
    direction. The corridor is cut into ``k`` equal lengths, the smallest ``k`` whose every
    piece fits one usable battery with the connector flown into it from the previous piece
    (and one tank when spraying, its mix shared by line length); each piece is a block.
12. Battery reserve: usable time = battery × (1 − reserve %). Transit legs (``transit_legs``):
    from the take-off point (given, or the area centroid / corridor start) straight to each
    block's first line and back from its last, at ``transit_speed_ms`` (default the survey
    speed), counted in the block's time; climbs and descents are not counted.
13. Ground control points: ``gcp_min + floor(area_ha / gcp_ha_per_extra)`` for an area,
    ``max(gcp_min, floor(centreline / gcp_corridor_spacing_m) + 1)`` along a corridor (a rule
    of thumb, configurable; :data:`~airbrief_core.models.survey.GCP_RULE_REFERENCE`). Storage =
    photos × images per capture × megabytes per image (``photo_mb``, else the camera's).
14. Spraying (``mode = "spraying"``, effort only): lines at the swath width; the pump output
    needed is ``Q = rate·w·v·60/10 000`` L/min (the sprayer calibration formula L/min =
    L/ha × km/h × m / 600); above the pump maximum the speed is lowered to
    ``v = Q_max·10 000/(rate·w·60)``. Litres = area × rate; tank loads = ceil(litres / tank).
    Sorties follow the serpentine path with breakpoint resume, as agricultural spraying drones
    fly: a sortie takes whole lines while the usable battery (with the return leg) and the tank
    allow (litres shared by line length), then sprays the next line until the tank or the
    battery runs out, and the next sortie resumes there (time per metre = the line's time ÷ its
    spray length). Refills = sorties − 1, each stop lasting max(refill, battery swap). Work
    rates: theoretical field capacity ``C = w·v·3 600 / 10 000`` ha/h (ASABE EP496: speed ×
    width / 10 with km/h and m) and effective = area / total time.
15. Sensors: RGB, thermal and multispectral presets (``CameraSpec.kind``); a thermal sensor's
    size is its pixel pitch × resolution. Multispectral cameras write one image per band.

The ``lines`` layer is a visual aid only (``visual_only: true``). AirBrief deliberately never
exports survey or spray lines as waypoints or mission files.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, substring

from ..data.raster import RasterSampler
from ..errors import AnalysisError
from ..geo.frame import LocalFrame
from ..geo.geodesic import geodesic_area_perimeter
from ..geo.transform import geojson_to_geometry, geometry_to_geojson
from ..geo.validity import drop_z, iter_parts, lineal_part, polygonal_part, repair
from ..models.analyses import SurveyBlock, SurveyParams, SurveyResult
from ..models.common import Issue, Severity
from ..models.heights import TakeoffPoint
from ..models.presets import CAMERA_PRESETS, CameraSpec
from ..models.survey import GCP_RULE_REFERENCE, SPRAY_HEIGHT_BAND_M, GcpEstimate, SprayResult, StorageEstimate
from .base import AnalysisContext
from .heights import dem_info, flight_level, height_issues, takeoff_point

__all__ = [
    "MAX_CORRIDOR_LINE_PIECES",
    "MAX_FLIGHT_LINES",
    "MAX_SURVEY_BLOCKS",
    "altitude_for_gsd",
    "camera_math",
    "gcp_estimate",
    "resolve_camera",
    "run",
    "spray_flow_l_per_min",
    "worst_overlap_pct",
]

MAX_FLIGHT_LINES = 20_000
"""Upper bound on the number of flight lines (guards against huge areas or tiny spacings)."""

NON_SQUARE_TOLERANCE = 0.01
"""Relative difference between across- and along-track pixel GSD above which pixels are non-square."""

MAX_CORRIDOR_BLOCKS = 2_000
"""Upper bound on the number of pieces a corridor is cut into for its battery blocks."""

MAX_CORRIDOR_LINE_PIECES = 20_000
"""Upper bound on corridor lines × pieces (each an offset curve and a map feature, as many as
``MAX_FLIGHT_LINES`` allows an area): the guard against a wide corridor at a fine GSD, which would
otherwise take many minutes and gigabytes."""

TERRAIN_STEP_M = 30.0
"""Grid step for the DEM statistics of each block."""

TERRAIN_MAX_POINTS = 20_000
"""DEM samples per block (the step grows beyond it)."""

LITRE_TOLERANCE = 1e-3
"""Relative slack when filling a tank (0.1 %: the per-line shares of the mix carry geodesic noise)."""

MAX_SORTIES = 100_000
"""Upper bound on spraying sorties (guards against a tiny tank over a huge area)."""

MAX_SURVEY_BLOCKS = 5_000
"""Upper bound on the battery blocks (spraying: sorties) of an area: each gets a polygon and DEM
statistics, about 15 ms apiece, so this bounds a job to a couple of minutes."""

Orientation = Literal["landscape", "portrait"]


def resolve_camera(camera: str | CameraSpec) -> CameraSpec:
    """A :class:`CameraSpec` from a preset key or an explicit spec."""
    if isinstance(camera, CameraSpec):
        return camera
    spec = CAMERA_PRESETS.get(camera)
    if spec is None:
        raise AnalysisError(
            "invalid_params",
            f"Unknown camera preset '{camera}'",
            details={"camera": camera, "known": sorted(CAMERA_PRESETS)},
        )
    return spec


def _axes(camera: CameraSpec, orientation: Orientation) -> tuple[float, int, float, int]:
    """(across sensor mm, across pixels, along sensor mm, along pixels) for an orientation."""
    if orientation == "portrait":
        return camera.sensor_height_mm, camera.image_height_px, camera.sensor_width_mm, camera.image_width_px
    return camera.sensor_width_mm, camera.image_width_px, camera.sensor_height_mm, camera.image_height_px


def altitude_for_gsd(camera: CameraSpec, gsd_cm: float, *, orientation: Orientation = "landscape") -> float:
    """Altitude AGL (m) that gives the across-track GSD ``gsd_cm``: H = gsd·f·iw/sw."""
    if not (math.isfinite(gsd_cm) and gsd_cm > 0.0):
        raise AnalysisError("invalid_params", "Target GSD must be a positive number of centimetres",
                            details={"target_gsd_cm": gsd_cm})
    across_mm, across_px, _, _ = _axes(camera, orientation)
    return (gsd_cm / 100.0) * camera.focal_length_mm * across_px / across_mm


def camera_math(
    camera: CameraSpec,
    altitude_agl_m: float,
    *,
    orientation: Orientation = "landscape",
    front_overlap_pct: float = 80.0,
    side_overlap_pct: float = 70.0,
) -> dict[str, Any]:
    """Photogrammetric quantities for a camera flown at ``altitude_agl_m``.

    Returns a dict with ``altitude_agl_m``, ``gsd_cm`` (across-track pixel size),
    ``gsd_along_cm``, ``footprint_across_m``, ``footprint_along_m``, ``line_spacing_m``,
    ``trigger_distance_m`` and ``square_pixels``. In landscape the sensor's long side lies across
    the flight direction; portrait swaps the axes.

    Example: P4 RTK at 100 m → GSD 2.74 cm, footprint 150 × 100 m, spacing 45 m (side 70 %),
    trigger 20 m (front 80 %).
    """
    if not (math.isfinite(altitude_agl_m) and altitude_agl_m > 0.0):
        raise AnalysisError("invalid_params", "Altitude must be a positive number of metres",
                            details={"altitude_agl_m": altitude_agl_m})
    if not (0.0 <= front_overlap_pct < 100.0 and 0.0 <= side_overlap_pct < 100.0):
        raise AnalysisError("invalid_params", "Overlaps must be in [0, 100) percent",
                            details={"front_overlap_pct": front_overlap_pct, "side_overlap_pct": side_overlap_pct})
    across_mm, across_px, along_mm, along_px = _axes(camera, orientation)
    f = camera.focal_length_mm
    footprint_across = across_mm * altitude_agl_m / f
    footprint_along = along_mm * altitude_agl_m / f
    gsd_across_cm = footprint_across / across_px * 100.0
    gsd_along_cm = footprint_along / along_px * 100.0
    square = abs(gsd_across_cm - gsd_along_cm) <= NON_SQUARE_TOLERANCE * max(gsd_across_cm, gsd_along_cm)
    return {
        "altitude_agl_m": float(altitude_agl_m),
        "gsd_cm": gsd_across_cm,
        "gsd_along_cm": gsd_along_cm,
        "footprint_across_m": footprint_across,
        "footprint_along_m": footprint_along,
        "line_spacing_m": footprint_across * (1.0 - side_overlap_pct / 100.0),
        "trigger_distance_m": footprint_along * (1.0 - front_overlap_pct / 100.0),
        "square_pixels": bool(square),
    }


def worst_overlap_pct(overlap_pct: float, planned_height_m: float, lowest_height_m: float) -> float | None:
    """Overlap left where the aircraft is only ``lowest_height_m`` above the ground (step 10).

    The spacing ``S = G₀·(1 − p)`` was laid out for the footprint ``G₀`` at ``planned_height_m``;
    at ``H_low`` the footprint is ``G₀·H_low/H₀``, so the overlap is ``1 − (1 − p)·H₀/H_low``
    (negative: a gap). None when the ground reaches the flight level.
    """
    if not (math.isfinite(lowest_height_m) and lowest_height_m > 0.0 and planned_height_m > 0.0):
        return None
    return 100.0 * (1.0 - (1.0 - overlap_pct / 100.0) * planned_height_m / lowest_height_m)


def spray_flow_l_per_min(rate_l_per_ha: float, swath_m: float, speed_ms: float) -> float:
    """Pump output (L/min) that applies ``rate_l_per_ha`` over a swath at a ground speed (step 14).

    ``Q = rate·w·v·60 / 10 000``, the sprayer calibration formula L/min = L/ha × km/h × m / 600.
    """
    return rate_l_per_ha * swath_m * speed_ms * 60.0 / 10_000.0


def gcp_estimate(
    *, area_ha: float, centreline_m: float | None, gcp_min: int, ha_per_extra: float, corridor_spacing_m: float
) -> GcpEstimate:
    """Ground control points for a block (step 13): a configurable rule of thumb."""
    if centreline_m is not None:
        count = max(gcp_min, int(math.floor(centreline_m / corridor_spacing_m + 1e-9)) + 1)
        rule = f"one every {corridor_spacing_m:g} m along the centreline (at least {gcp_min})"
        return GcpEstimate(count=count, basis="corridor", rule=rule, reference=GCP_RULE_REFERENCE)
    count = gcp_min + int(math.floor(area_ha / ha_per_extra + 1e-9))
    rule = f"{gcp_min} + 1 per {ha_per_extra:g} ha"
    return GcpEstimate(count=count, basis="area", rule=rule, reference=GCP_RULE_REFERENCE)


@dataclass
class _Segment:
    """One clipped flight-line segment in the rotated frame, oriented in flight direction."""

    line: int  # index among non-empty lines
    y: float
    x_start: float
    x_end: float
    length: float
    photos: int
    transition_m: float = 0.0  # connector from the previous segment (0 for the first)


def _auto_direction(local: BaseGeometry) -> float:
    """Bearing (0..180, clockwise from north) of the long axis of the minimum rotated rectangle."""
    mrr = local.minimum_rotated_rectangle
    if isinstance(mrr, Polygon):
        coords = list(mrr.exterior.coords)
    else:
        coords = list(mrr.coords) if hasattr(mrr, "coords") else []
    best_len = -1.0
    best = (1.0, 0.0)
    for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:], strict=True):
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length > best_len + 1e-9:
            best_len = length
            best = (dx, dy)
    if best_len <= 0.0:
        return 90.0
    bearing = math.degrees(math.atan2(best[0], best[1])) % 180.0
    bearing = round(bearing, 6)
    return 0.0 if bearing >= 180.0 else bearing


def _clip_line(rot: BaseGeometry, y: float, minx: float, maxx: float) -> list[tuple[float, float]]:
    """x-intervals of the horizontal line at ``y`` inside ``rot``, sorted by start."""
    probe = LineString([(minx - 10.0, y), (maxx + 10.0, y)])
    inter = rot.intersection(probe)
    intervals: list[tuple[float, float]] = []
    for part in shapely.get_parts(inter):
        if part.is_empty:
            continue
        if part.geom_type == "LineString":
            xs = [c[0] for c in part.coords]
            lo, hi = min(xs), max(xs)
            if hi - lo > 1e-6:
                intervals.append((lo, hi))
        elif part.geom_type == "MultiLineString":
            for sub in part.geoms:
                xs = [c[0] for c in sub.coords]
                lo, hi = min(xs), max(xs)
                if hi - lo > 1e-6:
                    intervals.append((lo, hi))
    intervals.sort()
    # Merge touching intervals (vertices exactly on the line can split a segment).
    merged: list[tuple[float, float]] = []
    for lo, hi in intervals:
        if merged and lo <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _feature(geom: BaseGeometry, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry_to_geojson(geom, 8), "properties": props}


def _parse_area(obj: dict[str, Any]) -> Polygon | MultiPolygon:
    geom = geojson_to_geometry(obj)
    if geom.is_empty:
        raise AnalysisError("invalid_geometry", "The survey area is empty")
    geom = drop_z(geom)
    poly = polygonal_part(geom)
    if poly is None:
        raise AnalysisError(
            "geometry_type_not_supported",
            "Survey planning needs an area (Polygon or MultiPolygon)",
            details={"geometry_type": geom.geom_type},
        )
    fixed, _ = repair(poly)
    fixed_poly = polygonal_part(fixed)
    if fixed_poly is None or fixed_poly.is_empty:
        raise AnalysisError("invalid_geometry", "The survey area has no valid polygonal part")
    return fixed_poly


def _parse_centreline(obj: dict[str, Any]) -> LineString:
    geom = geojson_to_geometry(obj)
    if geom.is_empty:
        raise AnalysisError("invalid_geometry", "The corridor centreline is empty")
    lines = lineal_part(drop_z(geom))
    if lines is None:
        raise AnalysisError(
            "geometry_type_not_supported",
            "Corridor mapping needs a centreline (LineString): select or draw a line",
            details={"geometry_type": geom.geom_type},
        )
    merged = lines if isinstance(lines, LineString) else linemerge(lines)
    if not isinstance(merged, LineString):
        raise AnalysisError(
            "geometry_type_not_supported",
            "Corridor mapping needs one connected centreline: select a single line",
            details={"parts": len(list(iter_parts(merged)))},
        )
    return merged


@dataclass
class _Coverage:
    """The surveyed area (WGS84 and local metres) and, for a corridor, its centreline."""

    area: BaseGeometry
    area_m2: float
    frame: LocalFrame
    local: BaseGeometry
    input_geom: BaseGeometry
    centreline_local: LineString | None = None
    width_m: float | None = None


def _coverage(params: SurveyParams) -> _Coverage:
    if params.coverage == "corridor":
        width = float(params.corridor_width_m or 0.0)
        centre = _parse_centreline(params.geometry)
        frame = LocalFrame.for_geometry(centre)
        centre_local = frame.to_local(centre)
        if centre_local.length <= 0.0:
            raise AnalysisError("invalid_geometry", "The corridor centreline has zero length")
        local = centre_local.buffer(width / 2.0, cap_style="flat", join_style="round", quad_segs=8)
        if not local.is_valid:
            local = shapely.make_valid(local)
        local_poly = polygonal_part(local)
        if local_poly is None or local_poly.area <= 0.0:
            raise AnalysisError("invalid_geometry", "The corridor has zero area")
        area = frame.to_wgs84(local_poly)
        area_m2, _ = geodesic_area_perimeter(area)
        return _Coverage(area=area, area_m2=area_m2, frame=frame, local=local_poly, input_geom=centre,
                         centreline_local=centre_local, width_m=width)
    area = _parse_area(params.geometry)
    area_m2, _ = geodesic_area_perimeter(area)
    frame = LocalFrame.for_geometry(area)
    local = frame.to_local(area)
    if not local.is_valid:
        local = shapely.make_valid(local)
    local_poly = polygonal_part(local)
    if local_poly is None or local_poly.area <= 0.0:
        raise AnalysisError("invalid_geometry", "The survey area has zero area")
    return _Coverage(area=area, area_m2=area_m2, frame=frame, local=local_poly, input_geom=area)


@dataclass
class _Job:
    spacing: float
    trigger: float | None  # None: no photos (spraying)
    speed: float
    turn_s: float
    battery_s: float
    takeoff_xy: tuple[float, float] | None  # local metres; set when transit legs are counted
    transit_speed: float
    litres: float | None = None  # spraying: the whole mix
    tank_l: float | None = None


@dataclass
class _Block:
    lines: list[int]
    seconds: float
    line_m: float
    photos: int
    polygon: BaseGeometry  # WGS84
    batteries: int
    transit_in_m: float | None = None
    transit_out_m: float | None = None
    litres: float | None = None


@dataclass
class _Layout:
    direction: float
    line_count: int
    segment_count: int
    line_m: float
    connector_m: float
    flight_s: float
    photos: int
    blocks: list[_Block]
    line_features: list[dict[str, Any]]
    long_lines: int
    transit_m: float | None = None
    transit_s: float | None = None
    transit_too_long: bool = False
    centreline_m: float | None = None
    batteries: int = 0
    split_lines: int = 0  # spraying: lines no single sortie can fly whole (resumed mid-line)


def _batteries(seconds: float, litres: float | None, job: _Job) -> int:
    """Batteries (sorties) a block needs: its time per usable battery and its mix per tank."""
    count = max(1, int(math.ceil(seconds / job.battery_s - 1e-9)))
    if litres is not None and job.tank_l:
        count = max(count, int(math.ceil(litres / job.tank_l - LITRE_TOLERANCE)))
    return count


def _area_layout(cov: _Coverage, job: _Job, direction_deg: float | None, ctx: AnalysisContext) -> _Layout:
    """Steps 4–9 (and 12/14 constraints) over an area."""
    local_poly = cov.local
    spacing = job.spacing
    speed = job.speed
    direction = float(direction_deg) if direction_deg is not None else _auto_direction(local_poly)
    theta = 90.0 - direction  # maths angle (CCW from +x) of the flight-line direction
    rot = affinity.rotate(local_poly, -theta, origin=(0.0, 0.0))
    minx, miny, maxx, maxy = rot.bounds
    height = maxy - miny
    if height / spacing > MAX_FLIGHT_LINES:
        raise AnalysisError(
            "area_too_large",
            f"The area needs more than {MAX_FLIGHT_LINES} flight lines at {spacing:.1f} m spacing.",
            details={"line_spacing_m": spacing, "extent_m": height, "max_lines": MAX_FLIGHT_LINES},
        )

    ys: list[float] = []
    y = miny + spacing / 2.0
    while y < maxy:
        ys.append(y)
        y += spacing
    if not ys:
        ys.append((miny + maxy) / 2.0)

    ctx.step("Laying out flight lines", 0.3)
    shapely.prepare(rot)
    segments: list[_Segment] = []
    line_ys: list[float] = []
    for i, ly in enumerate(ys):
        if i % 200 == 0:
            ctx.check_cancelled()
        intervals = _clip_line(rot, ly, minx, maxx)
        if not intervals:
            continue
        line_no = len(line_ys)
        line_ys.append(ly)
        forward = line_no % 2 == 0
        ordered = intervals if forward else list(reversed(intervals))
        for lo, hi in ordered:
            x_start, x_end = (lo, hi) if forward else (hi, lo)
            length = hi - lo
            photos = int(math.floor(length / job.trigger + 1e-9)) + 1 if job.trigger is not None else 0
            segments.append(_Segment(line=line_no, y=ly, x_start=x_start, x_end=x_end, length=length, photos=photos))
    if not segments:
        raise AnalysisError("invalid_geometry", "No flight line intersects the survey area")

    connectors: list[LineString] = []
    for prev, seg in zip(segments[:-1], segments[1:], strict=True):
        conn = LineString([(prev.x_end, prev.y), (seg.x_start, seg.y)])
        seg.transition_m = float(conn.length)
        connectors.append(conn)

    line_m = sum(s.length for s in segments)
    connector_m = sum(s.transition_m for s in segments)
    turns = len(segments) - 1
    flight_s = line_m / speed + turns * job.turn_s + connector_m / speed
    photos = sum(s.photos for s in segments)

    ctx.step("Grouping battery blocks", 0.6)
    # Per-line time; the transition into a line's first segment belongs to that line.
    line_seconds = [0.0] * len(line_ys)
    line_lead = [0.0] * len(line_ys)
    line_lengths = [0.0] * len(line_ys)
    line_photos = [0] * len(line_ys)
    line_first: dict[int, _Segment] = {}
    line_last: dict[int, _Segment] = {}
    for idx, seg in enumerate(segments):
        cost = seg.length / speed
        if idx > 0:
            cost += seg.transition_m / speed + job.turn_s
            if seg.line not in line_first:
                line_lead[seg.line] = seg.transition_m / speed + job.turn_s
        line_seconds[seg.line] += cost
        line_lengths[seg.line] += seg.length
        line_photos[seg.line] += seg.photos
        line_first.setdefault(seg.line, seg)
        line_last[seg.line] = seg

    battery_s = job.battery_s
    groups: list[list[int]] = []
    long_lines = 0
    split_lines = 0
    transit_m: float | None = None
    transit_s: float | None = None
    transit_too_long = False
    block_seconds: list[float] = []
    block_transit: list[tuple[float, float] | None] = []
    block_litres: list[float | None] = []
    takeoff_rot: tuple[float, float] | None = None
    if job.takeoff_xy is not None:
        rotated = affinity.rotate(Point(job.takeoff_xy), -theta, origin=(0.0, 0.0))
        takeoff_rot = (float(rotated.x), float(rotated.y))
    line_segs: list[list[_Segment]] = [[] for _ in line_ys]
    for seg in segments:
        line_segs[seg.line].append(seg)
    sorties: list[_Sortie] | None = None
    if job.litres is not None and line_m > 0:
        # Spraying: a sortie ends when the tank or the usable battery runs out (step 14).
        own = [line_seconds[ln] - line_lead[ln] for ln in range(len(line_ys))]
        sorties, split_lines, transit_too_long = _spray_sorties(line_segs, line_lengths, own, line_lead, job, takeoff_rot)
        groups = [s.lines for s in sorties]
        block_seconds = [s.seconds for s in sorties]
        block_litres = [s.litres for s in sorties]
        block_transit = [(s.transit_in_m, s.transit_out_m) if takeoff_rot is not None else None for s in sorties]
        flight_s = sum(block_seconds)
        if takeoff_rot is not None:
            transit_m = sum(s.transit_in_m + s.transit_out_m for s in sorties)
            transit_s = transit_m / job.transit_speed
    elif takeoff_rot is None:
        current: list[int] = []
        current_s = 0.0
        for ln, secs in enumerate(line_seconds):
            if secs > battery_s:
                long_lines += 1
            if current and current_s + secs > battery_s:
                groups.append(current)
                current, current_s = [], 0.0
            current.append(ln)
            current_s += secs
        if current:
            groups.append(current)
        block_seconds = [sum(line_seconds[ln] for ln in lines) for lines in groups]
        block_transit = [None] * len(groups)
        block_litres = [None] * len(groups)
    else:
        # Transit legs: whole lines are grouped so each block's legs fit its battery too (step 12).
        own = [line_seconds[ln] - line_lead[ln] for ln in range(len(line_ys))]
        tx, ty = takeoff_rot
        t_in = [math.hypot(line_first[ln].x_start - tx, line_first[ln].y - ty) for ln in range(len(line_ys))]
        t_out = [math.hypot(line_last[ln].x_end - tx, line_last[ln].y - ty) for ln in range(len(line_ys))]
        groups, block_seconds, block_transit, long_lines, transit_too_long = _group_lines(own, line_lead, t_in, t_out, job)
        block_litres = [None] * len(groups)
        transit_m = sum(a + b for a, b in (t for t in block_transit if t is not None))
        transit_s = transit_m / job.transit_speed
        flight_s = sum(block_seconds)
    if len(groups) > MAX_SURVEY_BLOCKS:
        # Each block gets its own polygon and DEM statistics (step 10): refuse before building them.
        what = "sorties" if sorties is not None else "battery blocks"
        raise AnalysisError(
            "area_too_large",
            f"The job needs {len(groups):,} {what}, more than {MAX_SURVEY_BLOCKS:,}: split the area, or enlarge the "
            "battery" + (" or the tank." if sorties is not None else " or fly faster."),
            details={"blocks": len(groups), "max_blocks": MAX_SURVEY_BLOCKS},
        )

    def _to_wgs(geom_rot: BaseGeometry) -> BaseGeometry:
        return cov.frame.to_wgs84(affinity.rotate(geom_rot, theta, origin=(0.0, 0.0)))

    blocks: list[_Block] = []
    block_of_line: dict[int, int] = {}
    pad = max(10.0, spacing)
    bounds = (minx - pad, miny - pad, maxx + pad, maxy + pad)
    for i, (lines, secs, transit, litres) in enumerate(zip(groups, block_seconds, block_transit, block_litres, strict=True)):
        ctx.check_cancelled()
        if sorties is not None:
            strip = _sortie_area(sorties[i], line_segs, line_ys, bounds)
            line_length = sorties[i].spray_m
        else:
            first, last = lines[0], lines[-1]
            low = miny - pad if first == 0 else (line_ys[first - 1] + line_ys[first]) / 2.0
            high = maxy + pad if last == len(line_ys) - 1 else (line_ys[last] + line_ys[last + 1]) / 2.0
            strip = box(minx - pad, low, maxx + pad, high)
            line_length = sum(line_lengths[ln] for ln in lines)
        piece = polygonal_part(rot.intersection(strip))
        if piece is None or piece.is_empty:
            continue
        piece_wgs = polygonal_part(_to_wgs(piece))
        if piece_wgs is None:
            continue
        index = len(blocks) + 1
        for ln in lines:
            block_of_line.setdefault(ln, index)
        blocks.append(_Block(
            lines=lines,
            seconds=secs,
            line_m=line_length,
            photos=sum(line_photos[ln] for ln in lines),
            polygon=piece_wgs,
            batteries=_batteries(secs, litres, job),
            transit_in_m=transit[0] if transit is not None else None,
            transit_out_m=transit[1] if transit is not None else None,
            litres=litres,
        ))

    ctx.step("Building layers", 0.85)
    line_features: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        if idx % 1000 == 0:
            ctx.check_cancelled()
        if idx > 0:
            line_features.append(_feature(_to_wgs(connectors[idx - 1]), {
                "kind": "connector",
                "order": idx,
                "length_m": round(seg.transition_m, 2),
                "visual_only": True,
            }))
        line_features.append(_feature(
            _to_wgs(LineString([(seg.x_start, seg.y), (seg.x_end, seg.y)])),
            {
                "kind": "line",
                "order": idx + 1,
                "line": seg.line + 1,
                "block": block_of_line.get(seg.line),
                "length_m": round(seg.length, 2),
                "photos": seg.photos,
                "visual_only": True,
            },
        ))

    # Every group counts, as in step 9 (a group whose strip is empty still needs its battery).
    batteries = sum(
        _batteries(secs, litres, job) for secs, litres in zip(block_seconds, block_litres, strict=True)
    )
    return _Layout(
        direction=direction, line_count=len(line_ys), segment_count=len(segments), line_m=line_m,
        connector_m=connector_m, flight_s=flight_s, photos=photos, blocks=blocks, line_features=line_features,
        long_lines=long_lines, transit_m=transit_m, transit_s=transit_s, transit_too_long=transit_too_long,
        batteries=batteries, split_lines=split_lines,
    )


def _group_lines(
    own: list[float],
    lead: list[float],
    t_in: list[float],
    t_out: list[float],
    job: _Job,
) -> tuple[list[list[int]], list[float], list[tuple[float, float] | None], int, bool]:
    """Group whole lines under a usable battery with transit legs (step 12).

    Returns the groups, their seconds, (transit in, out) metres, the lines that alone exceed a
    battery and whether a transit leg alone uses up a battery.
    """
    v_t = job.transit_speed

    def start_cost(ln: int) -> float:
        return t_in[ln] / v_t + own[ln]

    def end_cost(ln: int) -> float:
        return t_out[ln] / v_t

    groups: list[list[int]] = []
    seconds: list[float] = []
    current: list[int] = []
    body = 0.0
    long_lines = 0
    too_long = False
    for ln in range(len(own)):
        if start_cost(ln) + end_cost(ln) > job.battery_s:
            long_lines += 1
        if (t_in[ln] + t_out[ln]) / v_t >= job.battery_s:
            too_long = True
        if current:
            cand = body + lead[ln] + own[ln]
            if cand + end_cost(ln) <= job.battery_s:
                current.append(ln)
                body = cand
                continue
            groups.append(current)
            seconds.append(body + end_cost(current[-1]))
        current = [ln]
        body = start_cost(ln)
    if current:
        groups.append(current)
        seconds.append(body + end_cost(current[-1]))
    transits: list[tuple[float, float] | None] = [(t_in[g[0]], t_out[g[-1]]) for g in groups]
    return groups, seconds, transits, long_lines, too_long


@dataclass
class _Sortie:
    """One spraying sortie: pieces ``(line, from_m, to_m)`` along the lines' spray length."""

    pieces: list[tuple[int, float, float]]
    seconds: float
    litres: float
    end: tuple[int, float]
    transit_in_m: float = 0.0
    transit_out_m: float = 0.0

    @property
    def lines(self) -> list[int]:
        out: list[int] = []
        for ln, _, _ in self.pieces:
            if not out or out[-1] != ln:
                out.append(ln)
        return out

    @property
    def spray_m(self) -> float:
        return sum(b - a for _, a, b in self.pieces)


def _line_point(segs: list[_Segment], s: float) -> tuple[float, float]:
    """The point ``s`` metres of spray length along a line's segments (rotated frame, flight order)."""
    c = 0.0
    for k, seg in enumerate(segs):
        if s <= c + seg.length + 1e-9 or k == len(segs) - 1:
            f = min(max((s - c) / seg.length, 0.0), 1.0) if seg.length > 0.0 else 0.0
            return seg.x_start + f * (seg.x_end - seg.x_start), seg.y
        c += seg.length
    return segs[-1].x_end, segs[-1].y


def _spray_sorties(
    line_segs: list[list[_Segment]],
    lengths: list[float],
    own: list[float],
    lead: list[float],
    job: _Job,
    takeoff: tuple[float, float] | None,
) -> tuple[list[_Sortie], int, bool]:
    """Spraying sorties along the serpentine path, resumed where they stop (step 14).

    Agricultural spraying drones return when the tank is empty or the battery reaches its reserve
    and resume at that point ("breakpoint resume"). A sortie therefore takes whole lines while the
    usable battery (with the return leg to the take-off point) and the tank allow, then sprays the
    next line until the tank or the battery runs out; the next sortie resumes there. A line's time
    per metre is its time divided by its spray length (inner connectors and turns included).
    Returns the sorties, the number of lines cut by a refill or swap, and whether a transit leg
    alone uses up a battery.
    """
    tank = float(job.tank_l or 0.0)
    total = sum(lengths)
    if tank <= 0.0 or total <= 0.0 or job.litres is None:
        raise AnalysisError("invalid_params", "Spraying needs a tank and a positive mix and line length")
    lpm = job.litres / total  # litres per metre of spray line
    limit_l = tank * (1.0 + LITRE_TOLERANCE)
    battery = job.battery_s
    v_t = job.transit_speed

    def leg(ln: int, s: float) -> float:
        if takeoff is None:
            return 0.0
        x, y = _line_point(line_segs[ln], s)
        return math.hypot(x - takeoff[0], y - takeoff[1])

    sorties: list[_Sortie] = []
    cut = 0
    too_long = False
    cur: _Sortie | None = None

    def close(sortie: _Sortie) -> None:
        sortie.transit_out_m = leg(*sortie.end)
        sortie.seconds += sortie.transit_out_m / v_t
        sorties.append(sortie)
        if len(sorties) > MAX_SORTIES:
            raise AnalysisError(
                "area_too_large",
                f"The job needs more than {MAX_SORTIES} sorties: enlarge the tank or the battery, or split the area.",
                details={"max_sorties": MAX_SORTIES},
            )

    def open_at(ln: int, s: float, connector_s: float) -> _Sortie:
        sortie = _Sortie(pieces=[], seconds=0.0, litres=0.0, end=(ln, s))
        if takeoff is not None:
            sortie.transit_in_m = leg(ln, s)
            sortie.seconds = sortie.transit_in_m / v_t
        else:
            # Without transit legs a sortie starts with the connector into its first line (step 8).
            sortie.seconds = connector_s
        return sortie

    for ln, length in enumerate(lengths):
        if length <= 0.0:
            continue
        need_l = lpm * length
        need_t = own[ln]
        back_t = leg(ln, length) / v_t
        if cur is None:
            fresh_t = (leg(ln, 0.0) / v_t if takeoff is not None else lead[ln]) + need_t + back_t
            if need_l <= limit_l and fresh_t <= battery + 1e-9:
                cur = open_at(ln, 0.0, lead[ln])
                cur.pieces.append((ln, 0.0, length))
                cur.seconds += need_t
                cur.litres += need_l
                cur.end = (ln, length)
                continue
        elif cur.litres + need_l <= limit_l and cur.seconds + lead[ln] + need_t + back_t <= battery + 1e-9:
            cur.pieces.append((ln, 0.0, length))
            cur.seconds += lead[ln] + need_t
            cur.litres += need_l
            cur.end = (ln, length)
            continue
        # The line does not fit what is left: spray it until the tank or battery runs out, resume.
        per_m = need_t / length
        if cur is not None:
            if cur.litres >= tank or cur.seconds + lead[ln] + leg(ln, 0.0) / v_t >= battery:
                close(cur)
                cur = None
            else:
                cur.seconds += lead[ln]
        pieces = 0
        s = 0.0
        while s < length - 1e-6:
            fresh = cur is None
            if cur is None:
                cur = open_at(ln, s, lead[ln] if s == 0.0 else 0.0)
            by_tank = s + max(tank - cur.litres, 0.0) / lpm
            b = min(length, by_tank)
            for _ in range(4):  # the return leg depends on where the sortie ends
                spare = battery - cur.seconds - leg(ln, b) / v_t
                b = min(length, by_tank, s + max(spare, 0.0) / per_m)
            if b <= s + 1e-6:
                if not fresh:
                    close(cur)
                    cur = None
                    continue
                # Even a fresh battery cannot fly out here and back: the tank sets the piece.
                too_long = True
                b = min(length, by_tank)
            cur.pieces.append((ln, s, b))
            cur.seconds += (b - s) * per_m
            cur.litres += (b - s) * lpm
            cur.end = (ln, b)
            pieces += 1
            s = b
            if s < length - 1e-6:
                close(cur)
                cur = None
        if pieces > 1:
            cut += 1
    if cur is not None:
        close(cur)
    return sorties, cut, too_long


def _segment_territories(segs: list[_Segment], x_lo: float, x_hi: float) -> list[tuple[float, float]]:
    """The x-range each segment of a line owns: split halfway between neighbours, the ends to the edges."""
    order = sorted(range(len(segs)), key=lambda k: min(segs[k].x_start, segs[k].x_end))
    out = [(x_lo, x_hi)] * len(segs)
    for pos, k in enumerate(order):
        seg_lo = min(segs[k].x_start, segs[k].x_end)
        seg_hi = max(segs[k].x_start, segs[k].x_end)
        lo = x_lo if pos == 0 else (max(segs[order[pos - 1]].x_start, segs[order[pos - 1]].x_end) + seg_lo) / 2.0
        hi = x_hi if pos == len(order) - 1 else (seg_hi + min(segs[order[pos + 1]].x_start, segs[order[pos + 1]].x_end)) / 2.0
        out[k] = (lo, hi)
    return out


def _sortie_area(
    sortie: _Sortie,
    line_segs: list[list[_Segment]],
    line_ys: list[float],
    bounds: tuple[float, float, float, float],
) -> BaseGeometry:
    """The strips a sortie sprays (rotated frame): whole lines across, partial lines to the break points."""
    x_lo, y_lo, x_hi, y_hi = bounds
    n = len(line_ys)
    boxes: list[BaseGeometry] = []
    for ln, a, b in sortie.pieces:
        low = y_lo if ln == 0 else (line_ys[ln - 1] + line_ys[ln]) / 2.0
        high = y_hi if ln == n - 1 else (line_ys[ln] + line_ys[ln + 1]) / 2.0
        segs = line_segs[ln]
        length = sum(seg.length for seg in segs)
        if a <= 1e-6 and b >= length - 1e-6:
            boxes.append(box(x_lo, low, x_hi, high))
            continue
        territories = _segment_territories(segs, x_lo, x_hi)
        c = 0.0
        for seg, (t_lo, t_hi) in zip(segs, territories, strict=True):
            lo_s, hi_s = max(a, c), min(b, c + seg.length)
            if hi_s > lo_s + 1e-9:
                forward = seg.x_end >= seg.x_start
                xa, _ = _line_point([seg], lo_s - c)
                xb, _ = _line_point([seg], hi_s - c)
                if lo_s <= c + 1e-6:  # from the segment's start: its territory on that side
                    xa = t_lo if forward else t_hi
                if hi_s >= c + seg.length - 1e-6:
                    xb = t_hi if forward else t_lo
                boxes.append(box(min(xa, xb), low, max(xa, xb), high))
            c += seg.length
    return shapely.union_all(boxes)


@dataclass
class _Piece:
    """One corridor piece (a block): its lines in flight order and its time."""

    start_m: float
    end_m: float
    parts: list[tuple[LineString, int]]  # (line in flight direction, line number across 1..n)
    connectors: list[LineString]
    line_m: float
    photos: int
    seconds: float
    transit_in_m: float | None
    transit_out_m: float | None


def _corridor_piece(
    centre: LineString, a: float, b: float, offsets: list[float], job: _Job
) -> _Piece:
    sub = substring(centre, a, b)
    if not isinstance(sub, LineString) or sub.length <= 0.0:
        sub = LineString([centre.interpolate(a), centre.interpolate(b)])
    parts: list[tuple[LineString, int]] = []
    for i, offset in enumerate(offsets):
        curve = sub if abs(offset) < 1e-9 else shapely.offset_curve(sub, offset, quad_segs=8, join_style="round")
        lines = [p for p in iter_parts(curve) if isinstance(p, LineString) and p.length > 1e-6]
        if i % 2 == 1:
            lines = [LineString(list(p.coords)[::-1]) for p in reversed(lines)]
        parts.extend((p, i + 1) for p in lines)
    connectors = [
        LineString([prev.coords[-1], nxt.coords[0]]) for (prev, _), (nxt, _) in zip(parts[:-1], parts[1:], strict=True)
    ]
    line_m = sum(p.length for p, _ in parts)
    connector_m = sum(c.length for c in connectors)
    photos = (
        sum(int(math.floor(p.length / job.trigger + 1e-9)) + 1 for p, _ in parts) if job.trigger is not None else 0
    )
    seconds = line_m / job.speed + connector_m / job.speed + len(connectors) * job.turn_s
    t_in = t_out = None
    if job.takeoff_xy is not None and parts:
        tx, ty = job.takeoff_xy
        sx, sy = parts[0][0].coords[0][:2]
        ex, ey = parts[-1][0].coords[-1][:2]
        t_in = math.hypot(sx - tx, sy - ty)
        t_out = math.hypot(ex - tx, ey - ty)
        seconds += (t_in + t_out) / job.transit_speed
    return _Piece(start_m=a, end_m=b, parts=parts, connectors=connectors, line_m=line_m, photos=photos,
                  seconds=seconds, transit_in_m=t_in, transit_out_m=t_out)


def _lead_seconds(pieces: list[_Piece], job: _Job) -> list[float]:
    """Time of the connector and turn flown into each corridor piece from the previous one.

    Without transit legs the aircraft flies on from the end of one piece to the start of the
    next, as between area blocks (step 8); with transit legs each piece starts from the take-off
    point instead (its legs are in the piece's own time).
    """
    leads = [0.0] * len(pieces)
    if job.takeoff_xy is not None:
        return leads
    previous_end: tuple[float, float] | None = None
    for j, piece in enumerate(pieces):
        if not piece.parts:
            continue
        sx, sy = piece.parts[0][0].coords[0][:2]
        if previous_end is not None:
            leads[j] = math.hypot(sx - previous_end[0], sy - previous_end[1]) / job.speed + job.turn_s
        ex, ey = piece.parts[-1][0].coords[-1][:2]
        previous_end = (float(ex), float(ey))
    return leads


def _corridor_layout(cov: _Coverage, job: _Job, ctx: AnalysisContext) -> _Layout:
    """Step 11: lines along the centreline, cut into battery-sized pieces."""
    centre = cov.centreline_local
    width = cov.width_m
    if centre is None or width is None:
        raise AnalysisError("invalid_params", "Corridor mapping needs a centreline and a width")
    length = float(centre.length)
    n = max(1, int(math.ceil(width / job.spacing - 1e-9)))
    if n > MAX_FLIGHT_LINES:
        raise AnalysisError(
            "area_too_large",
            f"The corridor needs more than {MAX_FLIGHT_LINES} lines at {job.spacing:.1f} m spacing.",
            details={"line_spacing_m": job.spacing, "corridor_width_m": width},
        )
    offsets = [(i - (n - 1) / 2.0) * job.spacing for i in range(n)]
    ctx.step("Laying out corridor lines", 0.3)
    k = 1
    max_k = max(1, min(MAX_CORRIDOR_BLOCKS, int(math.ceil(length / max(job.spacing, 1.0)))))
    while True:
        ctx.check_cancelled()
        if n * k > MAX_CORRIDOR_LINE_PIECES:
            raise AnalysisError(
                "area_too_large",
                f"The corridor needs {n:,} lines cut into at least {k:,} battery pieces, more than "
                f"{MAX_CORRIDOR_LINE_PIECES:,} line pieces: narrow the corridor, fly higher (a coarser GSD) or "
                "split the centreline.",
                details={"lines": n, "pieces": k, "max_line_pieces": MAX_CORRIDOR_LINE_PIECES},
            )
        pieces = [_corridor_piece(centre, length * j / k, length * (j + 1) / k, offsets, job) for j in range(k)]
        # Each piece as flown: with the connector into it from the previous one, and its share of
        # the mix by line length (pieces of equal centreline length differ on bends).
        worst_s = max(p.seconds + lead for p, lead in zip(pieces, _lead_seconds(pieces, job), strict=True))
        line_total = sum(p.line_m for p in pieces)
        worst_l = job.litres * max(p.line_m for p in pieces) / line_total if job.litres and line_total > 0 else 0.0
        over = max(worst_s / job.battery_s, worst_l / job.tank_l if job.tank_l and job.litres else 0.0)
        if over <= 1.0 + 1e-9 or k >= max_k:
            break
        k = min(max_k, max(k + 1, int(math.ceil(k * over))))

    ctx.step("Grouping battery blocks", 0.6)
    total_line_m = sum(p.line_m for p in pieces)
    flight_s = 0.0
    connector_m = 0.0
    blocks: list[_Block] = []
    line_features: list[dict[str, Any]] = []
    order = 0
    previous_end: tuple[float, float] | None = None
    for j, piece in enumerate(pieces):
        seconds = piece.seconds
        if job.takeoff_xy is None and previous_end is not None and piece.parts:
            # Without transit legs the connector into the piece is flown, as between area blocks.
            lead = LineString([previous_end, piece.parts[0][0].coords[0][:2]])
            seconds += lead.length / job.speed + job.turn_s
            connector_m += lead.length
            line_features.append(_feature(cov.frame.to_wgs84(lead), {
                "kind": "connector", "order": order, "length_m": round(lead.length, 2), "visual_only": True,
            }))
        flight_s += seconds
        connector_m += sum(c.length for c in piece.connectors)
        litres = job.litres * piece.line_m / total_line_m if job.litres is not None and total_line_m > 0 else None
        strip = substring(centre, piece.start_m, piece.end_m)
        block_local = strip.buffer(width / 2.0, cap_style="flat", join_style="round", quad_segs=8).intersection(cov.local)
        block_poly = polygonal_part(cov.frame.to_wgs84(block_local))
        blocks.append(_Block(
            lines=list(range(n)),
            seconds=seconds,
            line_m=piece.line_m,
            photos=piece.photos,
            polygon=block_poly if block_poly is not None else cov.frame.to_wgs84(block_local),
            batteries=_batteries(seconds, litres, job),
            transit_in_m=piece.transit_in_m,
            transit_out_m=piece.transit_out_m,
            litres=litres,
        ))
        for p_idx, (part, line_no) in enumerate(piece.parts):
            if p_idx > 0:
                conn = piece.connectors[p_idx - 1]
                line_features.append(_feature(cov.frame.to_wgs84(conn), {
                    "kind": "connector", "order": order, "length_m": round(conn.length, 2), "visual_only": True,
                }))
            order += 1
            line_features.append(_feature(cov.frame.to_wgs84(part), {
                "kind": "line",
                "order": order,
                "line": line_no,
                "block": j + 1,
                "length_m": round(part.length, 2),
                "photos": int(math.floor(part.length / job.trigger + 1e-9)) + 1 if job.trigger is not None else 0,
                "visual_only": True,
            }))
        if piece.parts:
            previous_end = piece.parts[-1][0].coords[-1][:2]

    transit_m = None
    transit_s = None
    too_long = False
    if job.takeoff_xy is not None:
        transit_m = sum((b.transit_in_m or 0.0) + (b.transit_out_m or 0.0) for b in blocks)
        transit_s = transit_m / job.transit_speed
        too_long = any(
            ((b.transit_in_m or 0.0) + (b.transit_out_m or 0.0)) / job.transit_speed >= job.battery_s for b in blocks
        )
    long_pieces = sum(1 for b in blocks if b.seconds > job.battery_s + 1e-9)
    (x0, y0), (x1, y1) = centre.coords[0][:2], centre.coords[-1][:2]
    bearing = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 180.0
    return _Layout(
        direction=0.0 if round(bearing, 6) >= 180.0 else round(bearing, 6),
        line_count=n,
        segment_count=sum(len(p.parts) for p in pieces),
        line_m=total_line_m,
        connector_m=connector_m,
        flight_s=flight_s,
        photos=sum(p.photos for p in pieces),
        blocks=blocks,
        line_features=line_features,
        long_lines=long_pieces,
        transit_m=transit_m,
        transit_s=transit_s,
        transit_too_long=too_long,
        centreline_m=length,
        batteries=sum(b.batteries for b in blocks),
    )


@dataclass
class _BlockGround:
    terrain_min: float | None
    terrain_max: float | None


def _block_ground(polygons: list[BaseGeometry], dem: RasterSampler | None, ctx: AnalysisContext) -> list[_BlockGround]:
    out: list[_BlockGround] = []
    for i, poly in enumerate(polygons):
        if i % 50 == 0:  # DEM statistics take about 15 ms per block
            ctx.check_cancelled()
        if dem is None:
            out.append(_BlockGround(None, None))
            continue
        stats = dem.stats(poly, TERRAIN_STEP_M, max_points=TERRAIN_MAX_POINTS)
        if not stats.get("valid") or stats.get("min") is None or stats.get("max") is None:
            out.append(_BlockGround(None, None))
        else:
            out.append(_BlockGround(float(stats["min"]), float(stats["max"])))
    return out


def _r(value: float | None, digits: int) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _takeoff(params: SurveyParams, cov: _Coverage, dem: RasterSampler | None, needed: bool) -> TakeoffPoint | None:
    if not needed:
        return None
    return takeoff_point(cov.input_geom, params.takeoff, dem, params.takeoff_elevation_m)


def _usable_battery_s(params: SurveyParams) -> float:
    if params.battery_reserve_pct > 0.0:
        return params.battery_minutes * 60.0 * (1.0 - params.battery_reserve_pct / 100.0)
    return params.battery_minutes * 60.0


def run(params: SurveyParams, ctx: AnalysisContext) -> SurveyResult:
    """Estimate survey (or spraying) effort over an area or a corridor (see the module docstring).

    Needs no data pack; with a DEM it adds the ground under each block (step 10).
    """
    if params.mode == "spraying":
        return _run_spraying(params, ctx)
    issues: list[Issue] = []
    ctx.step("Camera geometry", 0.05)
    camera = resolve_camera(params.camera)
    orientation: Orientation = params.orientation
    ref = params.height_reference
    dem = ctx.datapack.dem() if ctx.datapack is not None else None
    cov = _coverage(params)
    takeoff = _takeoff(params, cov, dem, ref != "agl" or params.transit_legs)
    takeoff_elev = takeoff.elevation_m if takeoff is not None and ref != "agl" else None
    if params.target_gsd_cm is not None:
        altitude = altitude_for_gsd(camera, params.target_gsd_cm, orientation=orientation)
        where = "AGL" if ref == "agl" else "above the take-off ground"
        issues.append(Issue(
            code="altitude_from_gsd",
            message=f"Altitude {altitude:.1f} m {where} derived from the target GSD of {params.target_gsd_cm:g} cm.",
            severity=Severity.INFO,
        ))
        level = flight_level("takeoff", altitude, takeoff_elev) if ref != "agl" else None
    else:
        value = float(params.altitude_agl_m if params.altitude_agl_m is not None else 100.0)
        level = flight_level(ref, value, takeoff_elev)
        if ref == "amsl":
            if takeoff_elev is None:
                raise AnalysisError(
                    "heights_not_assessed",
                    "An AMSL survey height needs the take-off ground elevation: no DEM covers the take-off "
                    "point. Enter the take-off elevation, or plan the height above take-off.",
                )
            altitude = value - takeoff_elev
            if altitude <= 0.0:
                raise AnalysisError(
                    "invalid_params",
                    f"The AMSL level {value:,.0f} m is not above the take-off ground ({takeoff_elev:,.0f} m AMSL).",
                    details={"altitude_agl_m": value, "takeoff_elevation_m": takeoff_elev},
                )
        else:
            altitude = value
    cam = camera_math(
        camera,
        altitude,
        orientation=orientation,
        front_overlap_pct=params.front_overlap_pct,
        side_overlap_pct=params.side_overlap_pct,
    )
    spacing = float(cam["line_spacing_m"])
    trigger = float(cam["trigger_distance_m"])
    speed = float(params.speed_ms)
    if spacing <= 0.0 or trigger <= 0.0:
        raise AnalysisError("invalid_params", "Line spacing and trigger distance must be positive",
                            details={"line_spacing_m": spacing, "trigger_distance_m": trigger})

    if altitude > params.max_altitude_agl_m + 1e-6:  # same float tolerance as below
        issues.append(Issue(
            code="altitude_above_limit",
            message=(
                f"Planned altitude {altitude:.1f} m AGL is above the limit of "
                f"{params.max_altitude_agl_m:g} m AGL. Lower the altitude or accept a finer GSD."
            ),
            severity=Severity.WARNING,
        ))
    trigger_interval = trigger / speed
    # A small tolerance: 30 m / 15 m/s must count as exactly 2 s, not 1.9999… (float rounding).
    if params.min_trigger_interval_s > 0.0 and trigger_interval < params.min_trigger_interval_s - 1e-6:
        suggested = trigger / params.min_trigger_interval_s
        issues.append(Issue(
            code="trigger_interval_too_short",
            message=(
                f"Photos every {trigger_interval:.2f} s is faster than the camera minimum of "
                f"{params.min_trigger_interval_s:g} s. Fly at {suggested:.1f} m/s or slower."
            ),
            severity=Severity.WARNING,
        ))
    if not cam["square_pixels"]:
        issues.append(Issue(
            code="non_square_pixels",
            message=(
                f"Pixels are not square: across-track GSD {cam['gsd_cm']:.2f} cm, along-track "
                f"{cam['gsd_along_cm']:.2f} cm."
            ),
            severity=Severity.INFO,
        ))

    ctx.step("Preparing area", 0.15)
    job = _Job(
        spacing=spacing, trigger=trigger, speed=speed, turn_s=params.turn_seconds,
        battery_s=_usable_battery_s(params),
        takeoff_xy=_takeoff_xy(cov, takeoff) if params.transit_legs else None,
        transit_speed=float(params.transit_speed_ms or speed),
    )
    layout = _corridor_layout(cov, job, ctx) if params.coverage == "corridor" else _area_layout(
        cov, job, params.direction_deg, ctx
    )
    flight_minutes = layout.flight_s / 60.0
    batteries = layout.batteries
    total_minutes = flight_minutes + (batteries - 1) * params.battery_swap_minutes
    _effort_issues(issues, layout, params, job)

    ctx.step("Heights over the ground", 0.75)
    grounds = _block_ground([b.polygon for b in layout.blocks], dem, ctx)
    heights = _mapping_heights(params, camera, orientation, altitude, level, takeoff_elev, grounds, issues)

    blocks = _survey_blocks(layout, grounds, heights.block_agl, heights.block_gsd)
    area_ha = cov.area_m2 / 1e4
    layers = _layers(cov, layout, takeoff, area_ha)
    issues.extend(ctx.issues)

    images_per_capture = camera.images_per_capture
    mb = params.photo_mb if params.photo_mb is not None else camera.photo_mb
    storage = (
        StorageEstimate(
            images=layout.photos * images_per_capture,
            images_per_capture=images_per_capture,
            mb_per_image=float(mb),
            total_gb=round(layout.photos * images_per_capture * float(mb) / 1000.0, 3),
            source="params" if params.photo_mb is not None else "camera",
        )
        if mb is not None
        else None
    )
    hours = total_minutes / 60.0
    return SurveyResult(
        camera=camera,
        gsd_cm=round(cam["gsd_cm"], 4),
        gsd_along_cm=round(cam["gsd_along_cm"], 4),
        altitude_agl_m=round(altitude, 3),
        max_altitude_agl_m=float(params.max_altitude_agl_m),
        footprint_across_m=round(cam["footprint_across_m"], 3),
        footprint_along_m=round(cam["footprint_along_m"], 3),
        line_spacing_m=round(spacing, 3),
        trigger_distance_m=round(trigger, 3),
        trigger_interval_s=round(trigger_interval, 3),
        direction_deg=round(layout.direction, 2),
        line_count=layout.line_count,
        segment_count=layout.segment_count,
        line_km=round(layout.line_m / 1000.0, 4),
        photos=layout.photos,
        flight_minutes=round(flight_minutes, 3),
        batteries=batteries,
        total_minutes=round(total_minutes, 3),
        area_ha=round(area_ha, 4),
        ha_per_hour=round(area_ha / hours, 3) if hours > 0.0 else 0.0,
        blocks=blocks,
        issues=issues,
        layers=layers,
        mode="mapping",
        coverage=params.coverage,
        corridor_width_m=cov.width_m,
        centreline_km=_r(layout.centreline_m / 1000.0, 4) if layout.centreline_m is not None else None,
        height_reference=ref,
        takeoff=takeoff,
        flight_level_amsl_m=_r(level, 2),
        heights_assessed=heights.assessed,
        dem=dem_info(ctx.datapack),
        terrain_min_m=_r(heights.terrain_min, 2),
        terrain_max_m=_r(heights.terrain_max, 2),
        relief_m=_r(heights.relief, 2),
        agl_min_m=_r(heights.agl_min, 2),
        agl_max_m=_r(heights.agl_max, 2),
        gsd_min_cm=_r(heights.gsd_min, 4),
        gsd_max_cm=_r(heights.gsd_max, 4),
        worst_side_overlap_pct=_r(heights.worst_side, 1),
        worst_front_overlap_pct=_r(heights.worst_front, 1),
        heights_note=heights.note,
        battery_reserve_pct=float(params.battery_reserve_pct),
        usable_battery_minutes=round(job.battery_s / 60.0, 3),
        transit_km=_r(layout.transit_m / 1000.0, 4) if layout.transit_m is not None else None,
        transit_minutes=_r(layout.transit_s / 60.0, 3) if layout.transit_s is not None else None,
        gcp=gcp_estimate(
            area_ha=area_ha, centreline_m=layout.centreline_m, gcp_min=params.gcp_min,
            ha_per_extra=params.gcp_ha_per_extra, corridor_spacing_m=params.gcp_corridor_spacing_m,
        ),
        storage=storage,
        method=_method("mapping", params),
    )


def _takeoff_xy(cov: _Coverage, takeoff: TakeoffPoint | None) -> tuple[float, float] | None:
    if takeoff is None:
        return None
    xs, ys = cov.frame.xy([takeoff.lon], [takeoff.lat])
    return float(xs[0]), float(ys[0])


def _effort_issues(issues: list[Issue], layout: _Layout, params: SurveyParams, job: _Job) -> None:
    if layout.long_lines:
        if params.coverage == "corridor":
            message = (
                f"{layout.long_lines} corridor block(s) take longer than one usable battery even when cut "
                "short; widen the line spacing, fly faster or narrow the corridor."
            )
        else:
            message = f"{layout.long_lines} flight line(s) take longer than one battery; split the area or fly faster."
        issues.append(Issue(code="line_exceeds_battery", message=message, severity=Severity.WARNING))
    if layout.split_lines:
        issues.append(Issue(
            code="spray_line_resumed",
            message=(
                f"{layout.split_lines} spray line(s) are cut by a refill or battery swap: each sortie sprays until "
                "its tank or usable battery runs out and the next resumes the line where it stopped."
            ),
            severity=Severity.INFO,
        ))
    if layout.transit_too_long:
        issues.append(Issue(
            code="transit_exceeds_battery",
            message=(
                f"Flying from the take-off point to a block and back uses up a usable battery "
                f"({job.battery_s / 60.0:.1f} min): take off closer to the area."
            ),
            severity=Severity.WARNING,
        ))


@dataclass
class _Heights:
    assessed: bool
    terrain_min: float | None
    terrain_max: float | None
    relief: float | None
    agl_min: float | None
    agl_max: float | None
    gsd_min: float | None
    gsd_max: float | None
    worst_side: float | None
    worst_front: float | None
    note: str | None
    block_agl: list[tuple[float | None, float | None]]
    block_gsd: list[tuple[float | None, float | None]]


def _gsd_at(camera: CameraSpec, orientation: Orientation, height: float | None) -> float | None:
    if height is None or not (math.isfinite(height) and height > 0.0):
        return None
    return float(camera_math(camera, height, orientation=orientation)["gsd_cm"])


def _mapping_heights(
    params: SurveyParams,
    camera: CameraSpec,
    orientation: Orientation,
    altitude: float,
    level: float | None,
    takeoff_elev: float | None,
    grounds: list[_BlockGround],
    issues: list[Issue],
) -> _Heights:
    """Step 10: GSD and overlap over the ground of each block (with a DEM)."""
    ref = params.height_reference
    lows = [g.terrain_min for g in grounds if g.terrain_min is not None]
    highs = [g.terrain_max for g in grounds if g.terrain_max is not None]
    z_min = min(lows) if lows else None
    z_max = max(highs) if highs else None
    relief = z_max - z_min if z_min is not None and z_max is not None else None
    nominal_gsd = _gsd_at(camera, orientation, altitude)
    if ref == "agl" or level is None:
        # Terrain following (or no level known): the planned height holds over every block.
        if ref != "agl":
            ground = [v for g in grounds for v in (g.terrain_min, g.terrain_max)] or [math.nan]
            issues.extend(height_issues(ref, [math.nan if v is None else v for v in ground], takeoff_elev))
        note = None
        if ref == "agl" and relief is not None and z_min is not None and z_max is not None:
            note = (
                f"Terrain following at {altitude:,.0f} m above ground assumed: ground relief {relief:,.0f} m "
                f"(from {z_min:,.0f} m to {z_max:,.0f} m AMSL). Flown at one level instead, the GSD and "
                "overlap would change with the ground: plan the height above take-off to see by how much."
            )
        same: list[tuple[float | None, float | None]] = [(altitude, altitude)] * len(grounds)
        return _Heights(
            assessed=ref == "agl", terrain_min=z_min, terrain_max=z_max, relief=relief,
            agl_min=altitude, agl_max=altitude, gsd_min=nominal_gsd, gsd_max=nominal_gsd,
            worst_side=params.side_overlap_pct, worst_front=params.front_overlap_pct, note=note,
            block_agl=same, block_gsd=[(nominal_gsd, nominal_gsd)] * len(grounds),
        )
    ground_values = [math.nan if v is None else v for g in grounds for v in (g.terrain_min, g.terrain_max)]
    issues.extend(height_issues(ref, ground_values or [math.nan], takeoff_elev))
    if z_min is None or z_max is None:
        # No DEM under the area: flat ground at the take-off level.
        return _Heights(
            assessed=False, terrain_min=None, terrain_max=None, relief=None, agl_min=altitude, agl_max=altitude,
            gsd_min=nominal_gsd, gsd_max=nominal_gsd, worst_side=params.side_overlap_pct,
            worst_front=params.front_overlap_pct, note=None,
            block_agl=[(altitude, altitude)] * len(grounds), block_gsd=[(nominal_gsd, nominal_gsd)] * len(grounds),
        )
    block_agl: list[tuple[float | None, float | None]] = []
    block_gsd: list[tuple[float | None, float | None]] = []
    for g in grounds:
        if g.terrain_min is None or g.terrain_max is None:
            block_agl.append((altitude, altitude))
            block_gsd.append((nominal_gsd, nominal_gsd))
            continue
        low, high = level - g.terrain_max, level - g.terrain_min
        block_agl.append((low, high))
        block_gsd.append((_gsd_at(camera, orientation, low), _gsd_at(camera, orientation, high)))
    agl_min = level - z_max
    agl_max = level - z_min
    worst_side = worst_overlap_pct(params.side_overlap_pct, altitude, agl_min)
    worst_front = worst_overlap_pct(params.front_overlap_pct, altitude, agl_min)
    if agl_min <= 0.0:
        issues.append(Issue(
            code="terrain_above_flight_level",
            message=(
                f"The ground rises to {z_max:,.0f} m AMSL, above the flight level of {level:,.0f} m AMSL: "
                "the survey would meet the terrain (or roofs and canopy, in a surface model)."
            ),
            severity=Severity.CRITICAL,
        ))
    if agl_max > params.max_altitude_agl_m + 1e-6:
        issues.append(Issue(
            code="altitude_above_limit_over_low_ground",
            message=(
                f"Over the lowest ground ({z_min:,.0f} m AMSL) the survey is {agl_max:,.0f} m above ground, above "
                f"the {params.max_altitude_agl_m:g} m AGL limit."
            ),
            severity=Severity.WARNING,
        ))
    gsd_low = _gsd_at(camera, orientation, agl_min)
    gsd_high = _gsd_at(camera, orientation, agl_max)
    note = (
        f"Flight level {level:,.0f} m AMSL over ground from {z_min:,.0f} m to {z_max:,.0f} m AMSL "
        f"({z_max - z_min:,.0f} m relief): from {max(agl_min, 0.0):,.0f} m to {agl_max:,.0f} m above ground."
    )
    if worst_side is not None:
        note += f" Worst side overlap {worst_side:.0f} % over the highest ground."
    return _Heights(
        assessed=True, terrain_min=z_min, terrain_max=z_max, relief=z_max - z_min, agl_min=agl_min,
        agl_max=agl_max, gsd_min=gsd_low, gsd_max=gsd_high, worst_side=worst_side, worst_front=worst_front,
        note=note, block_agl=block_agl, block_gsd=block_gsd,
    )


def _survey_blocks(
    layout: _Layout,
    grounds: list[_BlockGround],
    block_agl: list[tuple[float | None, float | None]],
    block_gsd: list[tuple[float | None, float | None]],
) -> list[SurveyBlock]:
    blocks: list[SurveyBlock] = []
    for i, (b, g, agl, gsd) in enumerate(zip(layout.blocks, grounds, block_agl, block_gsd, strict=True)):
        block_area_m2, _ = geodesic_area_perimeter(b.polygon)
        blocks.append(SurveyBlock(
            index=i + 1,
            area_ha=round(block_area_m2 / 1e4, 4),
            line_km=round(b.line_m / 1000.0, 4),
            minutes=round(b.seconds / 60.0, 3),
            photos=b.photos,
            polygon=geometry_to_geojson(b.polygon, 8),
            transit_in_m=_r(b.transit_in_m, 1),
            transit_out_m=_r(b.transit_out_m, 1),
            terrain_min_m=_r(g.terrain_min, 2),
            terrain_max_m=_r(g.terrain_max, 2),
            agl_min_m=_r(agl[0], 2),
            agl_max_m=_r(agl[1], 2),
            gsd_min_cm=_r(gsd[0], 4),
            gsd_max_cm=_r(gsd[1], 4),
            litres=_r(b.litres, 2),
        ))
    return blocks


def _layers(cov: _Coverage, layout: _Layout, takeoff: TakeoffPoint | None, area_ha: float) -> dict[str, Any]:
    input_features = [_feature(cov.area, {"role": "input", "area_ha": round(area_ha, 4)})]
    if cov.centreline_local is not None:
        input_features.append(_feature(cov.frame.to_wgs84(cov.centreline_local), {
            "role": "centreline", "length_m": round(cov.centreline_local.length, 2),
            "corridor_width_m": cov.width_m,
        }))
    block_features = []
    for i, b in enumerate(layout.blocks):
        block_area_m2, _ = geodesic_area_perimeter(b.polygon)
        props: dict[str, Any] = {
            "block": i + 1,
            "area_ha": round(block_area_m2 / 1e4, 4),
            "line_km": round(b.line_m / 1000.0, 4),
            "minutes": round(b.seconds / 60.0, 3),
            "photos": b.photos,
            "lines": len(b.lines),
        }
        if b.litres is not None:
            props["litres"] = round(b.litres, 2)
        block_features.append(_feature(b.polygon, props))
    layers: dict[str, Any] = {
        "input": {"type": "FeatureCollection", "features": input_features},
        "blocks": {"type": "FeatureCollection", "features": block_features},
        "lines": {"type": "FeatureCollection", "features": layout.line_features},
    }
    if takeoff is not None:
        layers["takeoff"] = {"type": "FeatureCollection", "features": [
            _feature(Point(takeoff.lon, takeoff.lat), {"role": "takeoff", **takeoff.model_dump(mode="json", exclude={"lon", "lat"})}),
        ]}
    return layers


def _method(mode: str, params: SurveyParams) -> str:
    """The formulas behind the numbers, for the result (docs/features/heights.md)."""
    common = (
        f" Usable battery = battery × (1 − {params.battery_reserve_pct:g} % reserve)"
        + ("; transit legs from the take-off point are counted in each block." if params.transit_legs else ".")
    )
    if mode == "spraying":
        return (
            "Spray lines at the swath width over the area; pump output Q = rate × swath × speed × 60 / 10 000 "
            "L/min (L/ha × km/h × m / 600), the speed lowered when Q would exceed the pump maximum; litres = "
            "area × rate; tank loads = ceil(litres / tank); each sortie sprays along the lines until its tank "
            "or usable battery runs out and the next resumes there (breakpoint resume); field capacity = "
            "swath × speed × 3 600 / 10 000 ha/h (ASABE EP496)." + common
        )
    return (
        "GSD = sensor width × height / (focal length × image width); line spacing and trigger distance from "
        "the footprint and overlaps; over relief the height above ground is the flight level minus the "
        "ground, and the worst overlap 1 − (1 − p) × H₀ / H_low (Wolf, Dewitt & Wilkinson, Elements of "
        "Photogrammetry)." + common
    )


def _run_spraying(params: SurveyParams, ctx: AnalysisContext) -> SurveyResult:
    spray = params.spray
    if spray is None:  # set by SurveyParams for mode "spraying"
        raise AnalysisError("invalid_params", "Spraying needs spray parameters")
    issues: list[Issue] = []
    ctx.step("Spray rates", 0.05)
    dem = ctx.datapack.dem() if ctx.datapack is not None else None
    cov = _coverage(params)
    takeoff = _takeoff(params, cov, dem, params.transit_legs)
    requested = float(spray.speed_ms)
    needed = spray_flow_l_per_min(spray.rate_l_per_ha, spray.swath_m, requested)
    speed = requested
    flow_limited = needed > spray.flow_l_per_min + 1e-9
    if flow_limited:
        speed = spray.flow_l_per_min * 10_000.0 / (spray.rate_l_per_ha * spray.swath_m * 60.0)
        issues.append(Issue(
            code="spray_flow_limited",
            message=(
                f"At {requested:g} m/s the pump would need {needed:.2f} L/min for {spray.rate_l_per_ha:g} L/ha "
                f"over a {spray.swath_m:g} m swath, above its {spray.flow_l_per_min:g} L/min maximum: the "
                f"estimate flies at {speed:.2f} m/s."
            ),
            severity=Severity.WARNING,
        ))
    flow = spray_flow_l_per_min(spray.rate_l_per_ha, spray.swath_m, speed)
    low, high = SPRAY_HEIGHT_BAND_M
    if not (low - 1e-9 <= spray.height_m <= high + 1e-9):
        issues.append(Issue(
            code="spray_height_outside_band",
            message=(
                f"A spray height of {spray.height_m:g} m above the crop is outside the {low:g}–{high:g} m band "
                "this estimate assumes; check the product label and the operator's procedure for drift."
            ),
            severity=Severity.INFO,
        ))
    area_ha = cov.area_m2 / 1e4
    # Rounded to the centilitre, as reported: a geodesic area a hair above 2 ha must not add a tank load.
    litres = round(area_ha * spray.rate_l_per_ha, 2)
    ctx.step("Preparing area", 0.15)
    job = _Job(
        spacing=float(spray.swath_m), trigger=None, speed=speed, turn_s=spray.turn_seconds,
        battery_s=_usable_battery_s(params),
        takeoff_xy=_takeoff_xy(cov, takeoff) if params.transit_legs else None,
        transit_speed=float(params.transit_speed_ms or speed), litres=litres, tank_l=spray.tank_l,
    )
    layout = _corridor_layout(cov, job, ctx) if params.coverage == "corridor" else _area_layout(
        cov, job, params.direction_deg, ctx
    )
    _effort_issues(issues, layout, params, job)
    sorties = layout.batteries
    refills = max(0, sorties - 1)
    stop_minutes = max(spray.refill_minutes, params.battery_swap_minutes)
    flight_minutes = layout.flight_s / 60.0
    total_minutes = flight_minutes + refills * stop_minutes
    hours = total_minutes / 60.0
    theoretical = spray.swath_m * speed * 3600.0 / 10_000.0
    effective = area_ha / hours if hours > 0.0 else 0.0

    ctx.step("Heights over the ground", 0.75)
    grounds = _block_ground([b.polygon for b in layout.blocks], dem, ctx)
    lows = [g.terrain_min for g in grounds if g.terrain_min is not None]
    highs = [g.terrain_max for g in grounds if g.terrain_max is not None]
    z_min = min(lows) if lows else None
    z_max = max(highs) if highs else None
    note = (
        f"Spraying assumes terrain following at {spray.height_m:g} m above the crop: ground relief "
        f"{z_max - z_min:,.0f} m (from {z_min:,.0f} m to {z_max:,.0f} m AMSL)."
        if z_min is not None and z_max is not None
        else None
    )
    height = float(spray.height_m)
    blocks = _survey_blocks(layout, grounds, [(height, height)] * len(grounds), [(None, None)] * len(grounds))
    layers = _layers(cov, layout, takeoff, area_ha)
    issues.extend(ctx.issues)
    result = SprayResult(
        swath_m=spray.swath_m,
        rate_l_per_ha=spray.rate_l_per_ha,
        tank_l=spray.tank_l,
        height_m=spray.height_m,
        requested_speed_ms=requested,
        speed_ms=round(speed, 3),
        flow_l_per_min=round(flow, 3),
        max_flow_l_per_min=spray.flow_l_per_min,
        flow_limited=flow_limited,
        litres=round(litres, 2),
        tank_loads=max(1, int(math.ceil(litres / spray.tank_l - LITRE_TOLERANCE))),
        sorties=sorties,
        refills=refills,
        spray_minutes=round(layout.line_m / speed / 60.0, 3),
        theoretical_ha_per_hour=round(theoretical, 3),
        effective_ha_per_hour=round(effective, 3),
        field_efficiency_pct=round(100.0 * effective / theoretical, 1) if theoretical > 0.0 else 0.0,
        method=_method("spraying", params),
    )
    return SurveyResult(
        altitude_agl_m=height,
        max_altitude_agl_m=float(params.max_altitude_agl_m),
        line_spacing_m=round(float(spray.swath_m), 3),
        direction_deg=round(layout.direction, 2),
        line_count=layout.line_count,
        segment_count=layout.segment_count,
        line_km=round(layout.line_m / 1000.0, 4),
        photos=0,
        flight_minutes=round(flight_minutes, 3),
        batteries=sorties,
        total_minutes=round(total_minutes, 3),
        area_ha=round(area_ha, 4),
        ha_per_hour=round(effective, 3),
        blocks=blocks,
        issues=issues,
        layers=layers,
        mode="spraying",
        coverage=params.coverage,
        corridor_width_m=cov.width_m,
        centreline_km=_r(layout.centreline_m / 1000.0, 4) if layout.centreline_m is not None else None,
        height_reference="agl",
        takeoff=takeoff,
        heights_assessed=True,
        dem=dem_info(ctx.datapack),
        terrain_min_m=_r(z_min, 2),
        terrain_max_m=_r(z_max, 2),
        relief_m=_r(z_max - z_min, 2) if z_min is not None and z_max is not None else None,
        agl_min_m=height,
        agl_max_m=height,
        heights_note=note,
        battery_reserve_pct=float(params.battery_reserve_pct),
        usable_battery_minutes=round(job.battery_s / 60.0, 3),
        transit_km=_r(layout.transit_m / 1000.0, 4) if layout.transit_m is not None else None,
        transit_minutes=_r(layout.transit_s / 60.0, 3) if layout.transit_s is not None else None,
        spray=result,
        method=result.method,
    )

