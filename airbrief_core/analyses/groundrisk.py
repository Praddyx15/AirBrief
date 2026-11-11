"""Ground risk: SORA intrinsic ground risk class (iGRC) evidence for an operation.

Two methods. Both end with the same Table 2 lookup (:func:`igrc_lookup`); neither computes a
final GRC, ARC, SAIL or containment level: the output is *evidence* for SORA Step #2 and #8.

**Operational volume (``params.volume`` given; JARUS SORA 2.5 Main Body §2.2, §4.2, §4.8).**

1. **Flight geography (FG)** = the input geometry (area, route or point) at the flight-geography
   height ``H_FG`` (``volume.fg_height_m``, default ``altitude_agl_m``).
2. **Contingency volume (CV)**, §2.2.3 and §4.2.4 "iGRC footprint" (b) (navigation accuracy,
   flight technical error, path-definition/map error and latencies), with the term structure and
   contingency-manoeuvre formulas of the German Luftfahrt-Bundesamt (LBA) method for the
   operational volume (the one implemented by public FG/CV/GRB calculators). Conventions differ
   between national methods, so every term is an input:

   * ``S_CV = S_GNSS + S_pos + S_map + S_R + S_CM`` with ``S_R = v·t_R`` and
     ``S_CM = v²/(2·g·tan θ)`` for a multirotor stopping at pitch angle θ, or
     ``S_CM = v²/(g·tan φ)`` for a fixed-wing turning at bank angle φ (the turn radius);
   * ``H_CV = H_FG + H_alt + H_R + H_CM`` with ``H_R = v·t_R·sin γ`` (climb at angle γ during the
     reaction time; γ = 45° by default, i.e. a vertical share of 0.707) and ``H_CM = ½·v²/g`` (the
     whole kinetic energy converted into height, the conservative convention for both types).

   ``v`` is the maximum speed, ``g`` = 9.80665 m/s².
3. **Ground risk buffer (GRB)**, §4.2.4 "Ground risk buffer" (a): (i) the 1-to-1 principle
   ``S_GRB = H_CV + c/2`` (c = maximum characteristic dimension), or (ii) a value the applicant
   substantiates (Annex E §E.4, criterion 3), here the LBA method's ballistic
   ``S_GRB = v·√(2·H_CV/g) + c/2`` (drag-free fall from ``H_CV`` at ``v``).
4. **Footprint** (§4.2.4 "iGRC footprint" (a)) = FG ∪ CV ∪ GRB, built as nested buffers of the
   FG in a :class:`~airbrief_core.geo.frame.LocalFrame` (:func:`build_operational_volume`).
5. **Density** (§4.2.3 (c)): the population raster is averaged on a square grid of ``grid_m``
   cells (default from Main Body Table 4 by ``H_FG``: 200 m up to 500 ft, 400 m up to 1,000 ft,
   1 km up to 2,500 ft, 2 km up to 5,000 ft) laid out in the local frame; every cell that
   overlaps the footprint counts, and the highest cell mean is the iGRC density (``p95`` is kept
   as a supplementary statistic). Cells are averaged from k×k nearest-pixel samples.
6. **Adjacent area** (§2.2.5, §4.8.3 (b)): its outer limit lies ``clamp(v·180 s, 5 km, 35 km)``
   from the operational volume; its inner limit is the GRB edge. The average density over that
   ring is the people integrated on a 500 m grid divided by the covered ring area (parts outside
   the data pack are excluded and reported as ``partial_coverage``). When the GRB is wider than
   the adjacent distance the assessment is not required (§4.8.4, Figure 7).
7. **Assemblies** (§4.8.3 (b)(iii)): outdoor assembly venues from the pack (``assemblies`` layer)
   and user entries within 1 km of the operational volume, banded < 40k / 40k–400k / > 400k
   people (Tables 8–13) or "capacity unknown".

**Footprint method (``params.volume`` is None; earlier results).** Buffer = altitude (1:1) or
``v·√(2h/g)`` (ballistic) around the geometry; density sampled at the centroids of the H3 cells
at ``h3_resolution`` inside the footprint (``max_cells`` truncates).

**Lookup.** :func:`igrc_lookup` maps (dimension, speed, density) to the iGRC through
``SORA_IGRC_COLUMNS``/``SORA_IGRC_ROWS``: column = the first one where dimension ≤ column
dimension and speed ≤ column speed; row = the first density band whose upper bound is greater
than the density; a controlled ground area uses row 0; take-off mass ≤ 0.25 kg and speed
≤ 25 m/s give iGRC 1; a ``None`` cell is outside SORA scope. Population in the footprint =
``RasterSampler.integrate_density(footprint)``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import h3
import numpy as np
import shapely
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from ..data.datapack import DataPack
from ..data.raster import RasterSampler
from ..errors import AnalysisError
from ..geo.frame import LocalFrame
from ..geo.geodesic import geodesic_area_perimeter
from ..geo.h3grid import cell_centroids, cells_for_polygon
from ..geo.sampling import grid_points
from ..geo.transform import geojson_to_geometry, geometry_to_geojson
from ..geo.validity import drop_z, geometry_type_name, polygonal_part, repair
from ..models.analyses import DensityCell, GroundRiskParams, GroundRiskResult
from ..models.common import Issue, Severity
from ..models.ground_risk_volume import (
    ASSEMBLY_BAND_LABELS,
    AssemblyBand,
    AssemblyHit,
    AssemblyInput,
    DensityGrid,
    GridDensityCell,
    IgrcColumn,
    IgrcRow,
    IgrcTable,
    OperationalVolumeParams,
    OperationalVolumeResult,
    VolumeTerm,
)
from ..models.presets import (
    DISCLAIMER_GROUND_RISK,
    SORA_ADJACENT_MAX_M,
    SORA_ADJACENT_MIN_M,
    SORA_ADJACENT_TIME_S,
    SORA_ASSEMBLY_BANDS,
    SORA_ASSEMBLY_RADIUS_M,
    SORA_EVIDENCE_LABEL,
    SORA_GRID_REFERENCE,
    SORA_GRID_SIZES,
    SORA_IGRC_COLUMNS,
    SORA_IGRC_ROWS,
    SORA_SMALL_UAS_MASS_KG,
    SORA_SMALL_UAS_SPEED_MS,
    SORA_TABLE_REFERENCE,
)
from .base import AnalysisContext

__all__ = [
    "ADJACENT_STEP_M",
    "GRAVITY_MS2",
    "OperationalVolume",
    "adjacent_distance_m",
    "assembly_band",
    "auto_grid_m",
    "ballistic_distance_m",
    "buffer_distance_m",
    "build_operational_volume",
    "contingency_volume",
    "density_band",
    "density_grid_collection",
    "igrc_lookup",
    "igrc_table",
    "run",
]

GRAVITY_MS2 = 9.80665
"""Standard gravity (m/s²)."""

HOTSPOT_COUNT = 10
POPULATION_STEP_M = 100.0
ADJACENT_STEP_M = 500.0  # coarse b/c the rings reach 35 km
ADJACENT_MAX_POINTS = 250_000  # the step grows past this

# Cells in the density grid's bounding box before the grid is coarsened.
MAX_GRID_CANDIDATES = 2_000_000
MAX_SAMPLES_PER_AXIS = 8
MAX_GRID_SAMPLES = 4_000_000  # whole grid; huge grids get fewer samples per cell

QUAD_SEGS = 16  # per quarter circle of the volume buffers: a vertex every 5.625°

VOLUME_REFERENCE = (
    "JARUS SORA 2.5 Main Body §2.2 (semantic model), §4.2 Step #2 (iGRC footprint, Table 2, "
    "Table 4) and §4.8 Step #8 (adjacent area, assemblies); contingency-volume term structure of "
    "the German LBA (Luftfahrt-Bundesamt) operational-volume method; every term is editable."
)

_BEYOND_TABLE_LABEL = (
    f"Beyond table (> {SORA_IGRC_COLUMNS[-1][0]:g} m or > {SORA_IGRC_COLUMNS[-1][1]:g} m/s)"
)


def _column_label(dim_m: float, speed_ms: float) -> str:
    return f"≤ {dim_m:g} m, ≤ {speed_ms:g} m/s"


def _column_index(max_dimension_m: float, max_speed_ms: float) -> int | None:
    for idx, (col_dim, col_speed) in enumerate(SORA_IGRC_COLUMNS):
        if max_dimension_m <= col_dim and max_speed_ms <= col_speed:
            return idx
    return None


def _band_index(density_per_km2: float) -> int:
    for idx, (_label, upper, _values) in enumerate(SORA_IGRC_ROWS):
        if idx == 0:
            continue  # row 0 is the controlled ground area, selected explicitly
        if upper is None or upper > density_per_km2:
            return idx
    return len(SORA_IGRC_ROWS) - 1


def density_band(density_per_km2: float) -> str:
    """Label of the SORA density band (people/km²) that contains ``density_per_km2``."""
    value = float(density_per_km2)
    if not math.isfinite(value):
        value = 0.0
    return SORA_IGRC_ROWS[_band_index(max(0.0, value))][0]


def igrc_lookup(
    max_dimension_m: float,
    max_speed_ms: float,
    density_per_km2: float,
    *,
    controlled: bool = False,
    takeoff_mass_kg: float | None = None,
) -> tuple[int | None, str, str, bool]:
    """Look up the SORA-style intrinsic ground risk class.

    Returns ``(igrc, column_label, band_label, small_uas_rule)``. ``igrc`` is None when the
    combination falls outside the table (aircraft beyond the last column, or a ``None`` cell):
    the operation is outside SORA scope. ``controlled`` picks row 0. ``small_uas_rule`` is True
    when the mass ≤ 0.25 kg and speed ≤ 25 m/s rule set the class to 1 (only checked when
    ``takeoff_mass_kg`` is given).

    Raises ``AnalysisError("invalid_params")`` for a non-finite or non-positive dimension or
    speed, or a non-finite or negative density.
    """
    dim = float(max_dimension_m)
    speed = float(max_speed_ms)
    density = float(density_per_km2)
    if not (math.isfinite(dim) and dim > 0.0 and math.isfinite(speed) and speed > 0.0):
        raise AnalysisError(
            "invalid_params",
            "Aircraft dimension and speed must be positive finite numbers",
            details={"max_dimension_m": max_dimension_m, "max_speed_ms": max_speed_ms},
        )
    if not math.isfinite(density) or density < 0.0:
        raise AnalysisError(
            "invalid_params",
            "Population density must be a finite, non-negative number",
            details={"density_per_km2": density_per_km2},
        )

    row = 0 if controlled else _band_index(density)
    band_label = SORA_IGRC_ROWS[row][0]
    col = _column_index(dim, speed)
    column_label = _column_label(*SORA_IGRC_COLUMNS[col]) if col is not None else _BEYOND_TABLE_LABEL

    small_uas = (
        takeoff_mass_kg is not None
        and float(takeoff_mass_kg) <= SORA_SMALL_UAS_MASS_KG
        and speed <= SORA_SMALL_UAS_SPEED_MS
    )
    if small_uas:
        return 1, column_label, band_label, True
    if col is None:
        return None, column_label, band_label, False
    return SORA_IGRC_ROWS[row][2][col], column_label, band_label, False


def igrc_table(
    max_dimension_m: float, max_speed_ms: float, density_per_km2: float, *, controlled: bool = False
) -> IgrcTable:
    """Table 2 as data, with the row and column that apply (``column_index`` None beyond it)."""
    return IgrcTable(
        reference=SORA_TABLE_REFERENCE,
        columns=[
            IgrcColumn(dimension_m=dim, speed_ms=speed, label=_column_label(dim, speed))
            for dim, speed in SORA_IGRC_COLUMNS
        ],
        rows=[IgrcRow(label=label, values=list(values)) for label, _upper, values in SORA_IGRC_ROWS],
        row_index=0 if controlled else _band_index(max(0.0, float(density_per_km2))),
        column_index=_column_index(float(max_dimension_m), float(max_speed_ms)),
    )


def ballistic_distance_m(speed_ms: float, height_m: float) -> float:
    """Horizontal distance of a drag-free fall from ``height_m`` at ``speed_ms``: v·√(2h/g)."""
    return float(speed_ms) * math.sqrt(2.0 * max(0.0, float(height_m)) / GRAVITY_MS2)


def buffer_distance_m(params: GroundRiskParams) -> float:
    """Footprint-method buffer (m): the altitude (1:1) or the ballistic distance from it."""
    if params.buffer_rule == "ballistic":
        return ballistic_distance_m(params.max_speed_ms, params.altitude_agl_m)
    return float(params.altitude_agl_m)


def auto_grid_m(height_m: float) -> float:
    """Suggested density-grid cell size (m) for a maximum height AGL (Main Body Table 4)."""
    for max_height_m, size_m in SORA_GRID_SIZES:
        if float(height_m) <= max_height_m + 1e-9:
            return size_m
    return SORA_GRID_SIZES[-1][1]


def adjacent_distance_m(speed_ms: float) -> tuple[float, float]:
    """``(v·180 s, clamp(v·180 s, 5 km, 35 km))``: the Step #8 adjacent-area distance (m)."""
    raw = float(speed_ms) * SORA_ADJACENT_TIME_S
    return raw, min(SORA_ADJACENT_MAX_M, max(SORA_ADJACENT_MIN_M, raw))


def assembly_band(people: int | None) -> AssemblyBand:
    """Size band of an assembly: < 40k, 40k–400k (both ends inclusive), > 400k, or unknown."""
    if people is None:
        return "unknown"
    low, high = SORA_ASSEMBLY_BANDS
    if people < low:
        return "lt_40k"
    if people <= high:
        return "40k_400k"
    return "gt_400k"


def _num(value: float, decimals: int = 1) -> str:
    text = f"{float(value):,.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


_G_TEXT = _num(GRAVITY_MS2, 3)


def contingency_volume(
    volume: OperationalVolumeParams,
    *,
    speed_ms: float,
    fg_height_m: float,
    dimension_m: float,
    buffer_rule: str,
) -> tuple[float, float, float, list[VolumeTerm]]:
    """Sizes of the contingency volume and ground risk buffer with every term.

    Returns ``(s_cv_m, h_cv_m, grb_m, terms)``: the horizontal and vertical contingency volume
    sizes (m from the flight geography), the ground risk buffer width (m beyond the contingency
    volume) and the terms with their formulas (see the module docstring).
    """
    v = float(speed_ms)
    t_r = float(volume.reaction_time_s)
    s_r = v * t_r
    terms: list[VolumeTerm] = [
        VolumeTerm(name="S_GNSS", label="GNSS position error", axis="horizontal", value_m=volume.gnss_error_m,
                   formula=f"S_GNSS = {_num(volume.gnss_error_m, 2)} m (input)",
                   source="SORA 2.5 §4.2.4 iGRC footprint (b): accuracy of the navigation solution"),
        VolumeTerm(name="S_pos", label="Position-keeping (flight technical) error", axis="horizontal",
                   value_m=volume.position_error_m, formula=f"S_pos = {_num(volume.position_error_m, 2)} m (input)",
                   source="SORA 2.5 §4.2.4 iGRC footprint (b): flight technical error"),
        VolumeTerm(name="S_map", label="Map and path-definition error", axis="horizontal", value_m=volume.map_error_m,
                   formula=f"S_map = {_num(volume.map_error_m, 2)} m (input)",
                   source="SORA 2.5 §4.2.4 iGRC footprint (b): path definition error (e.g. map error)"),
        VolumeTerm(name="S_R", label="Reaction distance", axis="horizontal", value_m=s_r,
                   formula=f"S_R = v·t_R = {_num(v)} m/s × {_num(t_r, 2)} s = {_num(s_r)} m",
                   source="SORA 2.5 §4.2.4 iGRC footprint (b): latencies"),
    ]
    if volume.aircraft == "fixed_wing":
        phi = float(volume.bank_angle_deg)
        s_cm = v * v / (GRAVITY_MS2 * math.tan(math.radians(phi)))
        terms.append(VolumeTerm(
            name="S_CM", label="Contingency manoeuvre (turn radius)", axis="horizontal", value_m=s_cm,
            formula=f"S_CM = v²/(g·tan φ) = {_num(v)}²/({_G_TEXT} × tan {_num(phi)}°) = {_num(s_cm)} m",
            source="Fixed-wing turn at bank angle φ (LBA operational-volume method)",
        ))
    else:
        theta = float(volume.pitch_angle_deg)
        s_cm = v * v / (2.0 * GRAVITY_MS2 * math.tan(math.radians(theta)))
        terms.append(VolumeTerm(
            name="S_CM", label="Contingency manoeuvre (stopping distance)", axis="horizontal", value_m=s_cm,
            formula=f"S_CM = v²/(2·g·tan θ) = {_num(v)}²/(2 × {_G_TEXT} × tan {_num(theta)}°) = {_num(s_cm)} m",
            source="Multirotor stop at pitch angle θ (LBA operational-volume method)",
        ))
    s_cv = float(volume.gnss_error_m + volume.position_error_m + volume.map_error_m) + s_r + s_cm

    gamma = float(volume.climb_angle_deg)
    share = math.sin(math.radians(gamma))
    h_r = s_r * share
    h_cm = 0.5 * v * v / GRAVITY_MS2
    h_cv = float(fg_height_m) + float(volume.altimetry_error_m) + h_r + h_cm
    terms += [
        VolumeTerm(name="H_FG", label="Flight-geography height", axis="vertical", value_m=float(fg_height_m),
                   formula=f"H_FG = {_num(fg_height_m)} m (planned maximum height AGL)",
                   source="SORA 2.5 §2.2.2 flight geography"),
        VolumeTerm(name="H_alt", label="Altimetry error", axis="vertical", value_m=volume.altimetry_error_m,
                   formula=f"H_alt = {_num(volume.altimetry_error_m, 2)} m (input)",
                   source="SORA 2.5 §4.2.4 iGRC footprint (b): accuracy of the navigation solution (height)"),
        VolumeTerm(name="H_R", label="Height gained during the reaction time", axis="vertical", value_m=h_r,
                   formula=(f"H_R = v·t_R·sin γ = {_num(v)} m/s × {_num(t_r, 2)} s × sin {_num(gamma)}° "
                            f"= {_num(h_r)} m"),
                   source="Climb at angle γ during the reaction time (vertical share sin γ; 45° ≈ 0.707)"),
        VolumeTerm(name="H_CM", label="Contingency manoeuvre height", axis="vertical", value_m=h_cm,
                   formula=f"H_CM = ½·v²/g = 0.5 × {_num(v)}²/{_G_TEXT} = {_num(h_cm)} m",
                   source="Kinetic energy converted into height (conservative convention for both types)"),
    ]

    half_c = 0.5 * float(dimension_m)
    if buffer_rule == "ballistic":
        fall = ballistic_distance_m(v, h_cv)
        grb = fall + half_c
        terms.append(VolumeTerm(
            name="S_GRB", label="Ground risk buffer (ballistic)", axis="buffer", value_m=grb,
            formula=(f"S_GRB = v·√(2·H_CV/g) + c/2 = {_num(v)} × √(2 × {_num(h_cv)}/{_G_TEXT}) + "
                     f"{_num(half_c, 2)} = {_num(grb)} m"),
            source=("SORA 2.5 §4.2.4 Ground risk buffer (a)(ii): a value other than 1-to-1, here the "
                    "ballistic drag-free fall from H_CV of the LBA operational-volume method"),
        ))
    else:
        grb = h_cv + half_c
        terms.append(VolumeTerm(
            name="S_GRB", label="Ground risk buffer (1:1 rule)", axis="buffer", value_m=grb,
            formula=f"S_GRB = H_CV + c/2 = {_num(h_cv)} + {_num(half_c, 2)} = {_num(grb)} m",
            source="SORA 2.5 §4.2.4 Ground risk buffer (a)(i): 1-to-1 principle",
        ))
    return s_cv, h_cv, grb, terms


@dataclass(frozen=True)
class OperationalVolume:
    """Flight geography, contingency volume, iGRC footprint and adjacent outer limit.

    All geometries are in metres in ``frame``; each one contains the previous one exactly (they
    are unions with it), so FG ⊂ CV ⊂ footprint ⊂ adjacent outer limit.
    """

    frame: LocalFrame
    flight_geography: BaseGeometry
    contingency: BaseGeometry
    footprint: BaseGeometry
    adjacent_outer: BaseGeometry | None

    def wgs84(self, local: BaseGeometry) -> BaseGeometry:
        out = self.frame.to_wgs84(local)
        return out if out.is_valid else shapely.make_valid(out)


def _valid(geom: BaseGeometry) -> BaseGeometry:
    return geom if geom.is_valid else shapely.make_valid(geom)


def build_operational_volume(
    flight_geography_wgs84: BaseGeometry,
    s_cv_m: float,
    grb_m: float,
    adjacent_m: float | None = None,
) -> OperationalVolume:
    """Nested buffers of the flight geography in a local azimuthal-equidistant frame.

    CV = FG buffered by ``s_cv_m``; footprint = CV ∪ FG buffered by ``s_cv_m + grb_m``; adjacent
    outer limit = footprint ∪ FG buffered by ``s_cv_m + adjacent_m`` (the adjacent distance is
    measured from the operational volume, SORA 2.5 §2.2.5 (b)).
    """
    frame = LocalFrame.for_geometry(flight_geography_wgs84)
    fg = frame.to_local(flight_geography_wgs84)
    cv = _valid(shapely.union(fg, fg.buffer(float(s_cv_m), quad_segs=QUAD_SEGS)))
    footprint = _valid(shapely.union(cv, fg.buffer(float(s_cv_m) + float(grb_m), quad_segs=QUAD_SEGS)))
    adjacent_outer = None
    if adjacent_m is not None:
        adjacent_outer = _valid(
            shapely.union(footprint, fg.buffer(float(s_cv_m) + float(adjacent_m), quad_segs=QUAD_SEGS))
        )
    return OperationalVolume(frame, fg, cv, footprint, adjacent_outer)


@dataclass
class _Grid:
    grid: DensityGrid
    values: np.ndarray  # valid cell means (people/km²)


def _grid_cells(footprint_local: BaseGeometry, grid_m: float) -> tuple[np.ndarray, np.ndarray] | None:
    """``(ix, iy)`` of the cells overlapping the footprint; None past MAX_GRID_CANDIDATES."""
    g = float(grid_m)
    minx, miny, maxx, maxy = footprint_local.bounds
    ix0, ix1 = math.floor(minx / g), max(math.floor(minx / g) + 1, math.ceil(maxx / g))
    iy0, iy1 = math.floor(miny / g), max(math.floor(miny / g) + 1, math.ceil(maxy / g))
    if (ix1 - ix0) * (iy1 - iy0) > MAX_GRID_CANDIDATES:
        return None
    gx, gy = np.meshgrid(np.arange(ix0, ix1), np.arange(iy0, iy1))
    ix = gx.ravel()
    iy = gy.ravel()
    cx = (ix + 0.5) * g
    cy = (iy + 0.5) * g
    shapely.prepare(footprint_local)
    inside = shapely.contains_xy(footprint_local, cx, cy)
    # Cells whose centre is outside but within half a diagonal may still overlap: test exactly.
    # (+5 % covers the chords of the 4-segment buffer, which lie up to 1.9 % inside the arcs.)
    near_zone = footprint_local.buffer(g * math.sqrt(0.5) * 1.05, quad_segs=4)
    shapely.prepare(near_zone)
    border = np.nonzero(~inside & shapely.contains_xy(near_zone, cx, cy))[0]
    if border.size:
        boxes = shapely.box(ix[border] * g, iy[border] * g, (ix[border] + 1) * g, (iy[border] + 1) * g)
        overlap = shapely.intersects(footprint_local, boxes) & ~shapely.touches(footprint_local, boxes)
        inside[border[overlap]] = True
    return ix[inside], iy[inside]


def _density_grid(
    vol: OperationalVolume,
    population: RasterSampler,
    grid_m: float,
    max_cells: int,
    ctx: AnalysisContext,
) -> _Grid:
    """Mean density per square cell over the footprint, coarsening the grid to ``max_cells``."""
    g = float(grid_m)
    coarsened = False
    found = _grid_cells(vol.footprint, g)
    while found is None or found[0].size > max_cells:
        g *= 2.0
        coarsened = True
        found = _grid_cells(vol.footprint, g)
    ix, iy = found
    ctx.check_cancelled()
    pixel_m = max(1.0, population.nominal_resolution_m())
    k = int(min(MAX_SAMPLES_PER_AXIS, max(2, math.ceil(2.0 * g / pixel_m))))
    # Memory guard: max_cells goes up to 200,000.
    k = max(1, min(k, math.isqrt(max(1, MAX_GRID_SAMPLES // max(1, int(ix.size))))))
    offsets = ((np.arange(k) + 0.5) / k - 0.5) * g
    ox, oy = np.meshgrid(offsets, offsets)
    cx = (ix + 0.5) * g
    cy = (iy + 0.5) * g
    sx = (cx[:, None] + ox.ravel()[None, :]).ravel()
    sy = (cy[:, None] + oy.ravel()[None, :]).ravel()
    lons, lats = vol.frame.lonlat(sx, sy)
    ctx.check_cancelled()
    dens = np.asarray(population.density_per_km2(lons, lats, method="nearest"), dtype=float).reshape(ix.size, k * k)
    valid = np.isfinite(dens)
    counts = valid.sum(axis=1)
    sums = np.where(valid, np.maximum(dens, 0.0), 0.0).sum(axis=1)
    means = np.full(ix.size, np.nan)
    np.divide(sums, counts, out=means, where=counts > 0)

    clon, clat = vol.frame.lonlat(cx, cy)
    cells: list[GridDensityCell] = []
    for i in range(ix.size):
        if i % 2000 == 0:
            ctx.check_cancelled()
        value = float(means[i])
        if not math.isfinite(value):
            continue
        cells.append(GridDensityCell(
            id=f"{int(ix[i])}_{int(iy[i])}",
            ix=int(ix[i]),
            iy=int(iy[i]),
            lon=round(float(clon[i]), 7),
            lat=round(float(clat[i]), 7),
            density_per_km2=round(value, 3),
            band=density_band(value),
        ))
    hotspots = sorted(cells, key=lambda c: (-c.density_per_km2, c.id))[:HOTSPOT_COUNT]
    grid = DensityGrid(
        grid_m=g,
        origin_lon=round(vol.frame.lon0, 10),
        origin_lat=round(vol.frame.lat0, 10),
        samples_per_cell=k * k,
        cells=cells,
        hotspots=hotspots,
        nodata_cells=int(ix.size - len(cells)),
        coarsened=coarsened,
    )
    return _Grid(grid=grid, values=means[np.isfinite(means)])


def density_grid_collection(
    result: Mapping[str, Any], key: Literal["cells", "hotspots"] = "cells", *, ranked: bool = False
) -> dict[str, Any] | None:
    """The density grid of a stored result as square polygons (for exports), or None when the
    result has no density grid (footprint method).

    ``key`` picks the grid's ``cells`` or its ``hotspots``; ``ranked`` adds a 1-based ``rank``.
    Corners are inverse-projected from the result's local frame, so the polygons are exactly
    the cells that were averaged.
    """
    grid = result.get("density_grid")
    if not isinstance(grid, Mapping):
        return None
    try:
        g = float(grid["grid_m"])
        frame = LocalFrame(float(grid["origin_lon"]), float(grid["origin_lat"]))
    except (KeyError, TypeError, ValueError, AnalysisError):
        return None
    rows = [c for c in grid.get(key) or [] if isinstance(c, Mapping)
            and isinstance(c.get("ix"), int) and isinstance(c.get("iy"), int)]
    if not rows or not (math.isfinite(g) and g > 0):
        return {"type": "FeatureCollection", "features": []}
    ix = np.array([c["ix"] for c in rows], dtype=float)
    iy = np.array([c["iy"] for c in rows], dtype=float)
    xs = np.stack([ix, ix + 1, ix + 1, ix, ix], axis=1) * g
    ys = np.stack([iy, iy, iy + 1, iy + 1, iy], axis=1) * g
    lons, lats = frame.lonlat(xs.ravel(), ys.ravel())
    lons = np.round(lons.reshape(xs.shape), 7)
    lats = np.round(lats.reshape(ys.shape), 7)
    features = []
    for n, row in enumerate(rows):
        ring = [[float(lons[n, j]), float(lats[n, j])] for j in range(5)]
        props = {k: v for k, v in row.items() if k not in ("lon", "lat")}
        features.append({
            "type": "Feature",
            "id": str(row.get("id")),
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"rank": n + 1, **props} if ranked else props,
        })
    return {"type": "FeatureCollection", "features": features}


@dataclass
class _Adjacent:
    ring: BaseGeometry  # WGS84
    area_km2: float
    people: float | None
    mean_density: float | None
    coverage: float


def _adjacent(ring_wgs84: BaseGeometry, population: RasterSampler, pack: DataPack) -> _Adjacent:
    """People and mean density over the adjacent ring on a coarse grid (samples outside the pack
    are excluded; nodata inside it counts as nobody, as in ``integrate_density``)."""
    area_m2, _ = geodesic_area_perimeter(ring_wgs84)
    lons, lats, cell_area = grid_points(ring_wgs84, ADJACENT_STEP_M, ADJACENT_MAX_POINTS)
    lons = np.asarray(lons, dtype=float)
    lats = np.asarray(lats, dtype=float)
    if lons.size == 0:
        return _Adjacent(ring_wgs84, area_m2 / 1e6, None, None, 0.0)
    b = pack.bbox
    covered = (lons >= b.min_lon) & (lons <= b.max_lon) & (lats >= b.min_lat) & (lats <= b.max_lat)
    coverage = float(np.count_nonzero(covered)) / float(lons.size)
    if not covered.any():
        return _Adjacent(ring_wgs84, area_m2 / 1e6, None, None, 0.0)
    dens = np.asarray(population.density_per_km2(lons[covered], lats[covered]), dtype=float)
    dens = np.where(np.isfinite(dens), np.maximum(dens, 0.0), 0.0)
    cell_km2 = float(cell_area) / 1e6
    people = float(dens.sum()) * cell_km2
    covered_km2 = cell_km2 * float(np.count_nonzero(covered))
    return _Adjacent(ring_wgs84, area_m2 / 1e6, people, people / covered_km2, coverage)


@dataclass
class _Candidate:
    name: str
    source: str
    geometry: BaseGeometry  # WGS84
    kind: str | None
    capacity: int | None
    start_utc: str | None = None
    end_utc: str | None = None
    note: str | None = None


def _user_candidates(entries: Sequence[AssemblyInput]) -> list[_Candidate]:
    out: list[_Candidate] = []
    for entry in entries:
        try:
            geom = _parse_geometry(entry.geometry)
        except AnalysisError as exc:
            raise AnalysisError(
                "invalid_geometry",
                f"The geometry of assembly '{entry.name}' is not usable: {exc.message}",
                details={"assembly": entry.name},
            ) from exc
        out.append(_Candidate(
            name=entry.name,
            source="user",
            geometry=geom,
            kind="event",
            capacity=entry.expected_people,
            start_utc=entry.start_utc.isoformat() if entry.start_utc else None,
            end_utc=entry.end_utc.isoformat() if entry.end_utc else None,
            note=entry.note,
        ))
    return out


def _pack_candidates(pack: DataPack, around_wgs84: BaseGeometry, radius_m: float) -> list[_Candidate]:
    """Pack venues whose bounds come within ``radius_m`` of ``around_wgs84`` (a coarse prefilter)."""
    min_lon, min_lat, max_lon, max_lat = around_wgs84.bounds
    pad_lat = radius_m / 110_000.0 + 1e-4
    pad_lon = radius_m / (111_000.0 * max(math.cos(math.radians(max(abs(min_lat), abs(max_lat)))), 1e-3)) + 1e-4
    window = box(min_lon - pad_lon, min_lat - pad_lat, max_lon + pad_lon, max_lat + pad_lat)
    out: list[_Candidate] = []
    for feat in pack.assemblies():
        if not feat.geometry.intersects(window):
            continue
        out.append(_Candidate(name=feat.name, source="datapack", geometry=feat.geometry,
                              kind=feat.kind, capacity=feat.capacity))
    return out


def _assembly_hits(
    vol: OperationalVolume, candidates: Sequence[_Candidate], radius_m: float
) -> tuple[list[tuple[AssemblyHit, _Candidate]], list[str]]:
    """Assemblies within ``radius_m`` of the operational volume (distance measured in the local
    frame, 0 inside), nearest first, with their source candidate, and the names of those inside
    the iGRC footprint."""
    hits: list[tuple[AssemblyHit, _Candidate]] = []
    in_footprint: list[str] = []
    for cand in candidates:
        local = vol.frame.to_local(cand.geometry)
        distance = float(shapely.distance(vol.contingency, local))
        if not math.isfinite(distance) or distance > radius_m:
            continue
        if shapely.intersects(vol.footprint, local):
            in_footprint.append(cand.name)
        rep = cand.geometry if cand.geometry.geom_type == "Point" else cand.geometry.representative_point()
        band = assembly_band(cand.capacity)
        hits.append((AssemblyHit(
            name=cand.name,
            source="user" if cand.source == "user" else "datapack",
            kind=cand.kind,
            capacity=cand.capacity,
            band=band,
            band_label=ASSEMBLY_BAND_LABELS[band],
            distance_m=round(distance, 1),
            lon=round(float(rep.x), 7),
            lat=round(float(rep.y), 7),
            start_utc=cand.start_utc,
            end_utc=cand.end_utc,
            note=cand.note,
        ), cand))
    hits.sort(key=lambda pair: (pair[0].distance_m, pair[0].name))
    return hits, in_footprint


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _feature(geom: BaseGeometry, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry_to_geojson(geom, 8), "properties": props}


def _parse_geometry(obj: dict[str, Any]) -> BaseGeometry:
    geom = geojson_to_geometry(obj)
    if geom.is_empty:
        raise AnalysisError("invalid_geometry", "The operation geometry is empty")
    geom = drop_z(geom)
    geom, _ = repair(geom)
    if geom is None or geom.is_empty:
        raise AnalysisError("invalid_geometry", "The operation geometry is empty after repair")
    return geom


def _polygon_wgs84(geom: BaseGeometry, what: str) -> BaseGeometry:
    """The valid polygonal part of a WGS84 geometry (``invalid_geometry`` when there is none)."""
    poly = polygonal_part(geom)
    if poly is not None and not poly.is_valid:
        poly = polygonal_part(shapely.make_valid(poly))
    if poly is None or poly.is_empty:
        raise AnalysisError("invalid_geometry", f"Could not build a valid {what}")
    return poly


def _require_population(ctx: AnalysisContext) -> tuple[DataPack, RasterSampler]:
    pack = ctx.require("population")
    population = pack.population()
    if population is None:  # listed but unreadable — treat as missing
        raise AnalysisError(
            "layer_missing",
            f"Data pack '{pack.name}' has no readable population layer.",
            details={"datapack": pack.name, "missing": ["population"]},
        )
    return pack, population


def _coverage_issues(pack: DataPack, footprint: BaseGeometry, issues: list[Issue]) -> None:
    coverage = box(*pack.bbox.as_list())
    if not footprint.intersects(coverage):
        raise AnalysisError(
            "out_of_coverage",
            f"The footprint lies outside data pack '{pack.name}'.",
            details={"datapack": pack.name, "datapack_bbox": pack.bbox.as_list(),
                     "footprint_bbox": list(footprint.bounds)},
        )
    if not coverage.contains(footprint):
        issues.append(Issue(
            code="partial_coverage",
            message=f"Part of the footprint lies outside data pack '{pack.name}'; densities there are unknown.",
            severity=Severity.WARNING,
        ))


def _lookup_issues(
    params: GroundRiskParams, pack: DataPack, small_uas: bool, in_scope: bool, column_label: str,
    band_label: str, issues: list[Issue],
) -> None:
    if small_uas:
        issues.append(Issue(
            code="small_uas_rule",
            message="Take-off mass ≤ 0.25 kg and speed ≤ 25 m/s: the small-UAS rule sets the iGRC to 1.",
            severity=Severity.INFO,
        ))
    elif not in_scope:
        issues.append(Issue(
            code="out_of_scope",
            message=(
                f"No iGRC for column '{column_label}' and band '{band_label}': the operation is "
                "outside the scope of the SORA-style table (certified category)."
            ),
            severity=Severity.CRITICAL,
        ))
    if params.controlled_ground_area:
        issues.append(Issue(
            code="controlled_ground_area",
            message="Controlled ground area declared: the density band is not used for the class.",
            severity=Severity.INFO,
        ))
    if pack.synthetic:
        issues.append(Issue(
            code="synthetic_data",
            message=f"Data pack '{pack.name}' is synthetic: densities are illustrative, not real.",
            severity=Severity.WARNING,
        ))


def _method_text(params: GroundRiskParams, buffer_m: float, resolution: int, cell_count: int) -> str:
    if params.buffer_rule == "ballistic":
        buffer_txt = (
            f"ballistic buffer v·√(2h/g) = {params.max_speed_ms:g} m/s × "
            f"√(2 × {params.altitude_agl_m:g} m / {GRAVITY_MS2:g} m/s²) = {buffer_m:.0f} m"
        )
    else:
        buffer_txt = f"1:1 buffer equal to the altitude ({buffer_m:.0f} m)"
    return (
        f"The operation geometry was buffered by a {buffer_txt} in a local azimuthal-equidistant "
        f"frame. Population density was sampled at the centroids of {cell_count} H3 cells "
        f"(resolution {resolution}) inside the footprint; the {params.density_statistic} of the "
        "samples is the operational density. The class comes from the SORA-style table: column = "
        "first with dimension and speed within its limits, row = first density band above the "
        "density (controlled ground area → first row; take-off mass ≤ 0.25 kg and speed ≤ 25 m/s "
        "→ class 1). Population in the footprint is the density integrated on a "
        f"{POPULATION_STEP_M:.0f} m grid."
    )


def _run_footprint(params: GroundRiskParams, ctx: AnalysisContext) -> GroundRiskResult:
    """The footprint method: geometry + 1:1/ballistic buffer, density on H3 cell centroids."""
    pack, population = _require_population(ctx)
    issues: list[Issue] = []

    ctx.step("Preparing footprint", 0.05)
    geom = _parse_geometry(params.geometry)
    buffer_m = buffer_distance_m(params)
    frame = LocalFrame.for_geometry(geom)
    local = frame.to_local(geom)
    footprint_local = _valid(local.buffer(buffer_m, quad_segs=QUAD_SEGS))
    footprint = _polygon_wgs84(frame.to_wgs84(footprint_local), "ground-risk footprint")
    footprint_area_m2, _ = geodesic_area_perimeter(footprint)
    _coverage_issues(pack, footprint, issues)

    ctx.step("Building H3 grid", 0.2)
    cells, truncated = cells_for_polygon(footprint, params.h3_resolution, params.max_cells)
    resolution = params.h3_resolution
    if cells:
        resolution = int(h3.get_resolution(cells[0]))
    if truncated:
        issues.append(Issue(
            code="cells_truncated",
            message=(
                f"The footprint needs more than {params.max_cells} cells at resolution "
                f"{params.h3_resolution}; the grid was coarsened or limited to {len(cells)} cells "
                f"(resolution {resolution})."
            ),
            severity=Severity.WARNING,
        ))

    ctx.step("Sampling population density", 0.4)
    lons, lats = cell_centroids(cells)
    dens = np.asarray(population.density_per_km2(lons, lats), dtype=float) if cells else np.empty(0)
    valid = np.isfinite(dens)
    if not np.any(valid):
        raise AnalysisError(
            "out_of_coverage",
            "No population data is available inside the footprint.",
            details={"datapack": pack.name, "cells": len(cells)},
        )
    missing = int(np.count_nonzero(~valid))
    if missing:
        issues.append(Issue(
            code="density_nodata",
            message=f"{missing} of {len(cells)} cells have no population data and were ignored.",
            severity=Severity.WARNING,
        ))
    values = np.maximum(dens[valid], 0.0)
    max_density = float(values.max())
    p95_density = float(np.percentile(values, 95))
    mean_density = float(values.mean())
    density = max_density if params.density_statistic == "max" else p95_density

    ctx.step("Looking up iGRC", 0.6)
    igrc, column_label, band_label, small_uas = igrc_lookup(
        params.max_dimension_m,
        params.max_speed_ms,
        density,
        controlled=params.controlled_ground_area,
        takeoff_mass_kg=params.takeoff_mass_kg,
    )
    in_scope = igrc is not None
    _lookup_issues(params, pack, small_uas, in_scope, column_label, band_label, issues)

    ctx.step("Summarising cells", 0.7)
    density_cells: list[DensityCell] = []
    for i, cell in enumerate(cells):
        if i % 1000 == 0:
            ctx.check_cancelled()
        if not valid[i]:
            continue
        value = max(0.0, float(dens[i]))
        density_cells.append(DensityCell(
            h3=cell,
            lon=round(float(lons[i]), 8),
            lat=round(float(lats[i]), 8),
            density_per_km2=round(value, 3),
            band=density_band(value),
        ))
    hotspots = sorted(density_cells, key=lambda c: (-c.density_per_km2, c.h3))[:HOTSPOT_COUNT]

    ctx.step("Integrating population", 0.85)
    people = float(population.integrate_density(footprint, step_m=POPULATION_STEP_M))

    ctx.step("Building layers", 0.95)
    layers = {
        "input": _feature_collection([
            _feature(geom, {"role": "input", "geometry_type": geometry_type_name(geom)})
        ]),
        "footprint": _feature_collection([
            _feature(footprint, {
                "role": "footprint",
                "buffer_m": round(buffer_m, 3),
                "buffer_rule": params.buffer_rule,
                "area_km2": round(footprint_area_m2 / 1e6, 6),
                "population": round(people, 1),
                "igrc": igrc,
                "density_band": band_label,
            })
        ]),
    }
    issues.extend(ctx.issues)

    return GroundRiskResult(
        igrc=igrc,
        in_scope=in_scope,
        small_uas_rule=small_uas,
        column=column_label,
        density_band=band_label,
        density_statistic=params.density_statistic,
        max_density_per_km2=round(max_density, 3),
        p95_density_per_km2=round(p95_density, 3),
        mean_density_per_km2=round(mean_density, 3),
        population_in_footprint=round(people, 1),
        footprint_area_km2=round(footprint_area_m2 / 1e6, 6),
        buffer_m=round(buffer_m, 3),
        hotspots=hotspots,
        cells=density_cells,
        cells_truncated=truncated,
        table_reference=SORA_TABLE_REFERENCE,
        method=_method_text(params, buffer_m, resolution, len(cells)),
        disclaimer=DISCLAIMER_GROUND_RISK,
        issues=issues,
        layers=layers,
    )


def _volume_method_text(
    params: GroundRiskParams,
    volume: OperationalVolumeResult,
    grid: DensityGrid,
    density: float,
    adjacent: _Adjacent | None,
    adjacent_note: str,
    assemblies: Sequence[AssemblyHit],
    igrc: int | None,
    column_label: str,
    band_label: str,
) -> str:
    terms = {t.name: t for t in volume.terms}
    h_terms = " + ".join(_num(terms[n].value_m) for n in ("S_GNSS", "S_pos", "S_map", "S_R", "S_CM"))
    v_terms = " + ".join(_num(terms[n].value_m) for n in ("H_FG", "H_alt", "H_R", "H_CM"))
    grid_note = {
        "auto": f"{_num(volume.grid_m, 0)} m, from {SORA_GRID_REFERENCE} for H_FG = {_num(volume.fg_height_m)} m",
        "user": f"{_num(volume.grid_m, 0)} m, set by the user",
        "coarsened": f"{_num(volume.grid_m, 0)} m, coarsened to stay within {params.max_cells:,} cells",
    }[volume.grid_source]
    statistic = "highest" if params.density_statistic == "max" else "95th-percentile"
    parts = [
        f"{SORA_EVIDENCE_LABEL}.",
        f"Flight geography (FG) = the input geometry at H_FG = {_num(volume.fg_height_m)} m AGL; "
        f"v = {_num(volume.speed_ms)} m/s, c = {_num(params.max_dimension_m, 2)} m, "
        f"g = {_G_TEXT} m/s² ({volume.aircraft.replace('_', '-')}).",
        f"Contingency volume: S_CV = S_GNSS + S_pos + S_map + S_R + S_CM = {h_terms} = "
        f"{_num(volume.s_cv_m)} m, with {terms['S_R'].formula} and {terms['S_CM'].formula}.",
        f"H_CV = H_FG + H_alt + H_R + H_CM = {v_terms} = {_num(volume.h_cv_m)} m, with "
        f"{terms['H_R'].formula} and {terms['H_CM'].formula}.",
        f"Ground risk buffer: {terms['S_GRB'].formula}.",
        f"iGRC footprint = FG + CV + GRB ({_num(volume.s_cv_m + volume.grb_m)} m around the FG). "
        f"Density = the {statistic} cell mean among the {len(grid.cells):,} square cells ({grid_note}) "
        f"that overlap the footprint, each averaged from {grid.samples_per_cell} nearest-pixel samples: "
        f"{_num(density, 0)} people/km² (SORA 2.5 §4.2.3 (c)).",
    ]
    if igrc is None:
        parts.append(f"Table 2: column {column_label}, row {band_label}: no iGRC (outside the table).")
    else:
        parts.append(f"Table 2: column {column_label}, row {band_label} → iGRC {igrc}.")
    adj_raw = volume.adjacent_distance_km
    parts.append(
        f"Adjacent area (Step #8): outer limit = clamp(v × 180 s, 5 km, 35 km) = clamp("
        f"{_num(adj_raw, 2)} km, 5 km, 35 km) = {_num(volume.adjacent_outer_km, 2)} km from the operational volume."
    )
    if adjacent is not None and adjacent.mean_density is not None and adjacent.people is not None:
        parts.append(
            f"Average density between the GRB edge and that limit = {_num(adjacent.people, 0)} people / "
            f"{_num(adjacent.area_km2 * adjacent.coverage, 1)} km² covered = "
            f"{_num(adjacent.mean_density, 1)} people/km² ({ADJACENT_STEP_M:.0f} m integration grid)."
        )
    elif adjacent_note:
        parts.append(adjacent_note)
    parts.append(
        f"Outdoor assemblies within {SORA_ASSEMBLY_RADIUS_M / 1000:g} km of the operational volume: "
        f"{len(assemblies)} (bands < 40k, 40k–400k, > 400k people)."
    )
    return " ".join(parts)


def _run_volume(params: GroundRiskParams, volume_params: OperationalVolumeParams, ctx: AnalysisContext) -> GroundRiskResult:
    pack, population = _require_population(ctx)
    issues: list[Issue] = []

    ctx.step("Sizing the operational volume", 0.04)
    geom = _parse_geometry(params.geometry)
    fg_height = float(volume_params.fg_height_m if volume_params.fg_height_m is not None else params.altitude_agl_m)
    speed = float(params.max_speed_ms)
    s_cv, h_cv, grb, terms = contingency_volume(
        volume_params, speed_ms=speed, fg_height_m=fg_height, dimension_m=params.max_dimension_m,
        buffer_rule=params.buffer_rule,
    )
    adj_raw, adj_m = adjacent_distance_m(speed)
    terms.append(VolumeTerm(
        name="D_adj", label="Adjacent-area distance", axis="adjacent", value_m=adj_m,
        formula=(f"D_adj = clamp(v × 180 s, 5 km, 35 km) = clamp({_num(speed)} m/s × 180 s = "
                 f"{_num(adj_raw / 1000, 2)} km, 5 km, 35 km) = {_num(adj_m / 1000, 2)} km"),
        source="SORA 2.5 §4.8.3 (b)(i): distance flown in 3 minutes at maximum speed, 5–35 km",
    ))
    adjacent_required = adj_m > grb
    want_adjacent = params.adjacent_area and adjacent_required

    ctx.step("Building the volumes", 0.1)
    vol = build_operational_volume(geom, s_cv, grb, adj_m if want_adjacent else None)
    footprint = _polygon_wgs84(vol.wgs84(vol.footprint), "iGRC footprint")
    contingency = vol.wgs84(vol.contingency)
    footprint_area_m2, _ = geodesic_area_perimeter(footprint)
    _coverage_issues(pack, footprint, issues)

    ctx.step("Averaging density on the grid", 0.25)
    grid_source: Literal["auto", "user", "coarsened"] = "user" if params.grid_m is not None else "auto"
    grid_m = float(params.grid_m) if params.grid_m is not None else auto_grid_m(fg_height)
    gridded = _density_grid(vol, population, grid_m, params.max_cells, ctx)
    grid = gridded.grid
    if grid.coarsened:
        grid_source = "coarsened"
        issues.append(Issue(
            code="grid_coarsened",
            message=(
                f"The footprint needs more than {params.max_cells:,} cells of {_num(grid_m, 0)} m; "
                f"the density grid was coarsened to {_num(grid.grid_m, 0)} m cells."
            ),
            severity=Severity.WARNING,
        ))
    if gridded.values.size == 0:
        raise AnalysisError(
            "out_of_coverage",
            "No population data is available inside the footprint.",
            details={"datapack": pack.name, "cells": grid.nodata_cells},
        )
    if grid.nodata_cells:
        issues.append(Issue(
            code="density_nodata",
            message=(f"{grid.nodata_cells} of {grid.nodata_cells + len(grid.cells)} grid cells have no "
                     "population data and were ignored."),
            severity=Severity.WARNING,
        ))
    values = gridded.values
    max_density = float(values.max())
    p95_density = float(np.percentile(values, 95))
    mean_density = float(values.mean())
    density = max_density if params.density_statistic == "max" else p95_density
    if params.density_statistic == "p95":
        issues.append(Issue(
            code="density_statistic_p95",
            message=(
                "The class uses the 95th-percentile density. SORA 2.5 Step #2 uses the highest "
                f"density in the footprint ({_num(max_density, 0)} people/km²)."
            ),
            severity=Severity.WARNING,
        ))

    ctx.step("Looking up iGRC", 0.45)
    igrc, column_label, band_label, small_uas = igrc_lookup(
        params.max_dimension_m, speed, density,
        controlled=params.controlled_ground_area, takeoff_mass_kg=params.takeoff_mass_kg,
    )
    in_scope = igrc is not None
    _lookup_issues(params, pack, small_uas, in_scope, column_label, band_label, issues)
    table = igrc_table(params.max_dimension_m, speed, density, controlled=params.controlled_ground_area)

    ctx.step("Integrating population", 0.55)
    people = float(population.integrate_density(footprint, step_m=POPULATION_STEP_M))

    adjacent: _Adjacent | None = None
    adjacent_note = ""
    if not params.adjacent_area:
        adjacent_note = "The adjacent area was not computed (switched off)."
    elif not adjacent_required:
        adjacent_note = (
            f"The ground risk buffer ({_num(grb)} m) is wider than the adjacent-area distance "
            f"({_num(adj_m / 1000, 2)} km): the adjacent-area assessment is not required (§4.8.4)."
        )
        issues.append(Issue(code="adjacent_not_required", message=adjacent_note, severity=Severity.INFO))
    elif vol.adjacent_outer is not None:
        ctx.step("Integrating the adjacent area", 0.65)
        ring_local = _valid(vol.adjacent_outer.difference(vol.footprint))
        ring = _polygon_wgs84(vol.wgs84(ring_local), "adjacent area")
        adjacent = _adjacent(ring, population, pack)
        if adjacent.coverage < 0.999:
            issues.append(Issue(
                code="partial_coverage",
                message=(
                    f"Only {adjacent.coverage * 100:.0f} % of the adjacent area lies inside data pack "
                    f"'{pack.name}'; its average density covers that part only."
                    if adjacent.coverage > 0 else
                    f"The adjacent area lies outside data pack '{pack.name}'; its density is unknown."
                ),
                severity=Severity.WARNING,
            ))
        if params.takeoff_mass_kg is not None and params.takeoff_mass_kg <= SORA_SMALL_UAS_MASS_KG:
            issues.append(Issue(
                code="adjacent_small_uas",
                message=("UA of 250 g or less: SORA 2.5 Step #8 applies low containment with no "
                         "operational limits for the adjacent area (§4.8.3 (a)); the figures are context."),
                severity=Severity.INFO,
            ))

    ctx.step("Checking assemblies", 0.8)
    candidates = _user_candidates(params.assemblies)
    if "assemblies" in pack.layers:
        candidates = _pack_candidates(pack, contingency, SORA_ASSEMBLY_RADIUS_M) + candidates
    else:
        issues.append(Issue(
            code="assemblies_layer_missing",
            message=(f"Data pack '{pack.name}' has no assemblies layer: only the assemblies you "
                     "entered were checked."),
            severity=Severity.INFO,
        ))
    found_assemblies, in_footprint = _assembly_hits(vol, candidates, SORA_ASSEMBLY_RADIUS_M)
    assemblies = [hit for hit, _cand in found_assemblies]
    if in_footprint:
        issues.append(Issue(
            code="assembly_in_footprint",
            message=(
                f"{', '.join(in_footprint[:3])}{' and more' if len(in_footprint) > 3 else ''} lies inside "
                "the iGRC footprint. Population maps do not include event crowds: SORA 2.5 Table 3 "
                "describes assemblies of people as more than 50,000 people/km²."
            ),
            severity=Severity.WARNING,
        ))
    if grb > SORA_ASSEMBLY_RADIUS_M:
        issues.append(Issue(
            code="assemblies_not_applicable",
            message=(f"The ground risk buffer ({_num(grb)} m) exceeds 1 km: the Step #8 assembly "
                     "consideration is not applicable (§4.8.4); the list is context."),
            severity=Severity.INFO,
        ))

    ctx.step("Building layers", 0.92)
    volume = OperationalVolumeResult(
        aircraft=volume_params.aircraft,
        speed_ms=round(speed, 3),
        fg_height_m=round(fg_height, 3),
        s_cv_m=round(s_cv, 3),
        h_cv_m=round(h_cv, 3),
        grb_m=round(grb, 3),
        grb_rule=params.buffer_rule,
        adjacent_outer_km=round(adj_m / 1000.0, 4),
        adjacent_distance_km=round(adj_raw / 1000.0, 4),
        grid_m=grid.grid_m,
        grid_source=grid_source,
        terms=[t.model_copy(update={"value_m": round(t.value_m, 3)}) for t in terms],
        reference=VOLUME_REFERENCE,
    )
    fg_wgs84 = vol.wgs84(vol.flight_geography)
    grb_ring = vol.wgs84(_valid(vol.footprint.difference(vol.contingency)))
    layers: dict[str, Any] = {
        "input": _feature_collection([
            _feature(geom, {"role": "input", "geometry_type": geometry_type_name(geom)})
        ]),
        "footprint": _feature_collection([
            _feature(footprint, {
                "role": "footprint",
                "buffer_m": round(s_cv + grb, 3),
                "buffer_rule": params.buffer_rule,
                "area_km2": round(footprint_area_m2 / 1e6, 6),
                "population": round(people, 1),
                "igrc": igrc,
                "density_band": band_label,
                "evidence": SORA_EVIDENCE_LABEL,
            })
        ]),
        "flight_geography": _feature_collection([
            _feature(fg_wgs84, {"role": "flight_geography", "height_m": round(fg_height, 2)})
        ]),
        "contingency_volume": _feature_collection([
            _feature(contingency, {"role": "contingency_volume", "s_cv_m": round(s_cv, 2),
                                   "h_cv_m": round(h_cv, 2)})
        ]),
        "ground_risk_buffer": _feature_collection([
            _feature(grb_ring, {"role": "ground_risk_buffer", "grb_m": round(grb, 2),
                                "rule": params.buffer_rule})
        ] if not grb_ring.is_empty else []),
        "adjacent_area": _feature_collection([
            _feature(adjacent.ring, {
                "role": "adjacent_area",
                "outer_km": round(adj_m / 1000.0, 3),
                "area_km2": round(adjacent.area_km2, 3),
                "mean_density_per_km2": None if adjacent.mean_density is None else round(adjacent.mean_density, 2),
                "people": None if adjacent.people is None else round(adjacent.people, 0),
                "coverage": round(adjacent.coverage, 4),
            })
        ] if adjacent is not None else []),
        "assemblies": _feature_collection([
            _feature(cand.geometry, {
                "name": hit.name, "source": hit.source, "kind": hit.kind, "capacity": hit.capacity,
                "band": hit.band, "band_label": hit.band_label, "distance_m": hit.distance_m,
            })
            for hit, cand in found_assemblies
        ]),
    }
    issues.extend(ctx.issues)

    method = _volume_method_text(
        params, volume, grid, density, adjacent, adjacent_note, assemblies, igrc, column_label, band_label,
    )
    ctx.step("Done", 1.0)
    return GroundRiskResult(
        igrc=igrc,
        in_scope=in_scope,
        small_uas_rule=small_uas,
        column=column_label,
        density_band=band_label,
        density_statistic=params.density_statistic,
        max_density_per_km2=round(max_density, 3),
        p95_density_per_km2=round(p95_density, 3),
        mean_density_per_km2=round(mean_density, 3),
        population_in_footprint=round(people, 1),
        footprint_area_km2=round(footprint_area_m2 / 1e6, 6),
        buffer_m=round(s_cv + grb, 3),
        cells_truncated=grid.coarsened,
        table_reference=SORA_TABLE_REFERENCE,
        method=method,
        disclaimer=f"{SORA_EVIDENCE_LABEL}. {DISCLAIMER_GROUND_RISK}",
        issues=issues,
        layers=layers,
        evidence_label=SORA_EVIDENCE_LABEL,
        volume=volume,
        density_grid=grid,
        igrc_table=table,
        adjacent_mean_density_per_km2=(
            None if adjacent is None or adjacent.mean_density is None else round(adjacent.mean_density, 3)
        ),
        adjacent_people=None if adjacent is None or adjacent.people is None else round(adjacent.people, 1),
        adjacent_area_km2=None if adjacent is None else round(adjacent.area_km2, 4),
        adjacent_coverage=None if adjacent is None else round(adjacent.coverage, 4),
        assemblies=assemblies,
    )


def run(params: GroundRiskParams, ctx: AnalysisContext) -> GroundRiskResult:
    """Run the ground-risk analysis. Requires the ``population`` layer of a data pack.

    With ``params.volume`` the SORA 2.5 operational-volume method runs; without it the earlier
    footprint method (see the module docstring).
    """
    if params.volume is not None:
        return _run_volume(params, params.volume, ctx)
    return _run_footprint(params, ctx)
