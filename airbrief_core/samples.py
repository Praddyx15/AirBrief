"""Built-in sample files with known answers (Tier 0 ingest/measure cases and module demos).

Every sample is generated deterministically from code: ``build_sample(name)`` returns the same
bytes on every call and every platform (fixed zip timestamps and attributes, fixed float
formatting, no wall-clock values). ``write_all(dest)`` writes every sample below ``dest`` using
the relative ``SampleSpec.filename`` (``tier0/…``, ``modules/…``, ``flightmd/…``).

Known answers (asserted by ``tests/core/test_samples.py``; see RUNBOOK §6)
---------------------------------------------------------------------------
* ``square_1km_equator.kml`` / ``square_1km_60N.kml`` (K1/K2): corners at ±500 m in a
  Lambert Azimuthal Equal-Area frame centred on (0°, 0°) / (10°E, 60°N), inverse-projected to
  lon/lat. Area 1,000,000 m², perimeter 4,000 m (±0.01 %).
* ``climb_line_z.kml`` (K5): two vertices ±500 m east/west of (77.5946°E, 12.9716°N) on the
  local AEQD frame, z 0 → 100 m, ``relativeToGround``. 2D 1,000.00 m; 3D √(1000² + 100²) =
  1,004.99 m.
* ``plots_shapefile.zip``: EPSG:32643 (WGS 84 / UTM 43N, ESRI WKT ``.prj``, ``.cpg`` UTF-8),
  three axis-aligned rectangles with integer UTM coordinates near Bengaluru. Grid areas are
  exact: Plot A 200 × 100 m = 20,000 m²; Plot B 150 × 150 m = 22,500 m²; Plot C 300 × 50 m =
  15,000 m². Attributes: ``name``, ``crop``, ``owner``. ``no_prj.zip`` is the same data
  without the ``.prj`` (→ "CRS unknown — not measured").
* ``show_50_drones.kml`` / ``.csv`` (K8): exactly one conflict, pair **D07–D32** at
  **t = 48.0 s**, minimum 3D separation **0.80 m** (both drones at the ring centre, D07 at
  z = 50.0 m and D32 at z = 50.8 m). ``show_50_drones_clean.kml`` has no conflict (minimum
  separation 2·25·sin(π/50) = 3.14 m at the 25 m ring) and no dynamic violation at the default
  limits (1.5 m, 8 m/s horizontal, 4 m/s climb, 3 m/s descent, 4 m/s², 1 s window).
* ``flightmd_report_v15.json`` / ``flightmd_report_v12.json`` (K7): home 900.0 m AMSL, 301
  GPS points every 10 m (3,000 m). Height above take-off: max **135 m**; above 120 m between
  **1,157.14 m** and **1,642.86 m** along the track (takeoff-relative; a DEM shifts this by its
  calibration, ±1 m).
* ``bengaluru_survey.kml``: a pentagon inside 77.485–77.497°E × 12.900–12.910°N, ≈1.19 km².
* ``training_flight.gpx`` (GPX 1.1): waypoint "Take-off" at 77.4880°E 12.9020°N, 905 m; a closed
  route "Training box" (x −100…500 m, y −200…200 m in the AEQD frame of the take-off point);
  track "Training flight", 401 fixes at 1 Hz from 2026-03-21T04:30:00Z with ``ele`` = 905 m +
  height (see :func:`training_flight_position`). Height above take-off: max **130 m**; above
  120 m from **t = 133.33 s to 166.67 s** (33.33 s) between 400 m and 500 m along the track.
  Outside the training box from **t = 200 s to 260 s** (60 s, 600 m to 900 m along the track),
  at most **100 m** outside. Farthest from take-off **608.28 m** (√(600² + 100²)); beyond 500 m
  from t = 197.98 s to 260 s.

Drone-show choreography (local ENU metres, origin 77.5920°E 13.0005°N, ground 0 m)
---------------------------------------------------------------------------------
50 drones D00…D49 on a ring of radius 40 m at angles 2πi/50; every phase uses the ease
s(τ) = (1 − cos πτ)/2. 0–20 s climb z 0 → 50 m (D32 to 50.8 m in the conflict variant);
20–40 s rotate the ring by 36° (five slots); 40–56 s the conflict variant swaps D07 and D32
(diametrically opposite) along straight eased paths through the centre, both reaching the
centre at t = 48.0 s, while the clean variant holds; 56–76 s radius 40 → 25 m; 76–96 s
radius 25 → 40 m; 96–126 s descend to 0. Keyframes every integer second (127 per drone);
``gx:Track`` ``<when>`` values start at 2026-01-01T19:00:00Z, altitude = z,
``relativeToGround``.

Dynamics, verified analytically (worst case over both variants, before 1 s windowing):
climb peak 50.8·π/40 = 3.99 m/s (clean 3.93), descent peak 50.8·π/60 = 2.66 m/s, rotation
speed 40·(π/5)·π/40 = 1.97 m/s, radius change 15·π/40 = 1.18 m/s, swap peak 40·π/16 =
7.85 m/s (1 s window over the 1 Hz keyframes: 7.80 m/s), largest acceleration 40·(π/16)² =
1.54 m/s² (swap) and 50.8·π²/800 = 0.63 m/s² (climb). Separation between ring neighbours is
5.02 m at 40 m and 3.14 m at 25 m; the swapping drones stay ≥ 40·sin 7.2° = 5.0 m from every
other drone. In the conflict variant the D07–D32 3D distance at the 1 Hz keyframes 47/48/49 s
is 15.63/0.80/15.63 m, so at 10 Hz only the 48.0 s frame is below 1.5 m (47.9 s: 1.75 m).

``zipbomb_small.zip`` holds one entry of 201 MiB (210,763,776 bytes) of zeros, deflated to
about 0.2 MB. It is only built on demand (``build_sample``/``write_all``/the CLI); unpacking
it needs that much space, which is the point of the ingest guard test (K9).
"""

from __future__ import annotations

import io
import json
import math
import struct
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from numpy.typing import NDArray

from airbrief_core.errors import AirBriefError
from airbrief_core.geo.crs import WGS84, local_area_crs
from airbrief_core.geo.frame import LocalFrame
from airbrief_core.geo.transform import get_transformer

__all__ = [
    "FLIGHT_EXCEEDANCE_END_M",
    "FLIGHT_EXCEEDANCE_START_M",
    "FLIGHT_HOME",
    "FLIGHT_HOME_ALT_AMSL_M",
    "FLIGHT_MAX_HEIGHT_M",
    "FLIGHT_POINT_SPACING_M",
    "FLIGHT_TOTAL_DISTANCE_M",
    "PLOT_GRID_AREAS_M2",
    "SAMPLES",
    "SHOW_CONFLICT_MIN_DISTANCE_M",
    "SHOW_CONFLICT_PAIR",
    "SHOW_CONFLICT_TIME_S",
    "SHOW_DRONE_COUNT",
    "SHOW_DURATION_S",
    "SHOW_ORIGIN",
    "SHOW_START_UTC",
    "TRAINING_ABOVE_120_END_S",
    "TRAINING_ABOVE_120_START_S",
    "TRAINING_BOX_XY",
    "TRAINING_DURATION_S",
    "TRAINING_EXIT_END_M",
    "TRAINING_EXIT_END_S",
    "TRAINING_EXIT_MAX_OUTSIDE_M",
    "TRAINING_EXIT_START_M",
    "TRAINING_EXIT_START_S",
    "TRAINING_FARTHEST_M",
    "TRAINING_HOME",
    "TRAINING_HOME_ELE_M",
    "TRAINING_MAX_HEIGHT_M",
    "TRAINING_START_UTC",
    "ZIPBOMB_UNCOMPRESSED_BYTES",
    "SampleSpec",
    "build_sample",
    "flight_height_above_takeoff",
    "show_keyframes",
    "show_position",
    "training_flight_position",
    "write_all",
]


SQUARE_HALF_SIDE_M = 500.0

CLIMB_LINE_CENTRE = (77.5946, 12.9716)
CLIMB_LINE_HALF_LENGTH_M = 500.0
CLIMB_LINE_TOP_M = 100.0

PLOTS_EPSG = 32643
PLOTS_ESRI_WKT = (
    'PROJCS["WGS_1984_UTM_Zone_43N",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],'
    'PARAMETER["Central_Meridian",75.0],PARAMETER["Scale_Factor",0.9996],'
    'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]'
)
# (name, crop, owner, xmin, ymin, xmax, ymax) in EPSG:32643 metres.
_PLOTS: tuple[tuple[str, str, str, int, int, int, int], ...] = (
    ("Plot A", "Ragi", "K. Ramaiah", 777_000, 1_434_000, 777_200, 1_434_100),
    ("Plot B", "Tomato", "S. Lakshmi", 777_300, 1_434_000, 777_450, 1_434_150),
    ("Plot C", "Mulberry", "Anand Farms Pvt Ltd", 777_000, 1_434_300, 777_300, 1_434_350),
)
PLOT_GRID_AREAS_M2: dict[str, float] = {
    name: float((xmax - xmin) * (ymax - ymin)) for name, _c, _o, xmin, ymin, xmax, ymax in _PLOTS
}
_PLOT_FIELDS: tuple[tuple[str, int], ...] = (("name", 32), ("crop", 24), ("owner", 40))

SHOW_ORIGIN: tuple[float, float, float] = (77.5920, 13.0005, 0.0)
SHOW_START_UTC = datetime(2026, 1, 1, 19, 0, 0, tzinfo=UTC)
SHOW_DRONE_COUNT = 50
SHOW_DURATION_S = 126
SHOW_RING_RADIUS_M = 40.0
SHOW_INNER_RADIUS_M = 25.0
SHOW_ALTITUDE_M = 50.0
SHOW_CONFLICT_ALTITUDE_M = 50.8
SHOW_ROTATION_RAD = math.radians(36.0)
SHOW_SWAP = (7, 32)
SHOW_CONFLICT_PAIR = ("D07", "D32")
SHOW_CONFLICT_TIME_S = 48.0
SHOW_CONFLICT_MIN_DISTANCE_M = 0.80
SHOW_BOX_HALF_M = 60.0
SHOW_AUDIENCE_XY = (-60.0, -180.0, 60.0, -130.0)  # xmin, ymin, xmax, ymax

FLIGHT_HOME = (77.49, 12.905)
FLIGHT_HOME_ALT_AMSL_M = 900.0
FLIGHT_POINT_SPACING_M = 10.0
FLIGHT_OUT_LEG_M = 1480.0
FLIGHT_CROSS_LEG_M = 40.0
FLIGHT_TOTAL_DISTANCE_M = 3000.0
FLIGHT_MAX_HEIGHT_M = 135.0
FLIGHT_CEILING_M = 120.0
FLIGHT_EXCEEDANCE_START_M = 1100.0 + 100.0 * (120.0 - 100.0) / 35.0  # 1157.142857…
FLIGHT_EXCEEDANCE_END_M = 1600.0 + 100.0 * (135.0 - 120.0) / 35.0  # 1642.857142…
FLIGHT_CRUISE_SPEED_MS = 8.0

# Training flight (GPX): local AEQD metres from the take-off point, 1 Hz fixes for 400 s.
TRAINING_HOME = (77.4880, 12.9020)
TRAINING_HOME_ELE_M = 905.0
TRAINING_START_UTC = datetime(2026, 3, 21, 4, 30, 0, tzinfo=UTC)
TRAINING_DURATION_S = 400
TRAINING_BOX_XY = (-100.0, -200.0, 500.0, 200.0)  # xmin, ymin, xmax, ymax
TRAINING_MAX_HEIGHT_M = 130.0
TRAINING_ABOVE_120_START_S = 120.0 + 20.0 / 1.5  # 133.33… s, climbing 1.5 m/s from 100 m
TRAINING_ABOVE_120_END_S = 160.0 + 10.0 / 1.5  # 166.67… s, descending 1.5 m/s from 130 m
TRAINING_EXIT_START_S = 200.0
TRAINING_EXIT_END_S = 260.0
TRAINING_EXIT_START_M = 600.0  # distance along the track: 400 east + 100 north + 100 east
TRAINING_EXIT_END_M = 900.0  # + 100 east to x = 600 m, 100 south, 100 west back to x = 500 m
TRAINING_EXIT_MAX_OUTSIDE_M = 100.0
TRAINING_FARTHEST_M = math.hypot(600.0, 100.0)  # 608.28 m from take-off at t = 220 s

ZIPBOMB_UNCOMPRESSED_BYTES = 201 * 1024 * 1024

_ZIP_DATE = (2026, 1, 1, 0, 0, 0)
_KML_NS = "http://www.opengis.net/kml/2.2"
_GX_NS = "http://www.google.com/kml/ext/2.2"


@dataclass(frozen=True, slots=True)
class SampleSpec:
    """Catalogue entry for a sample file.

    ``filename`` is the path relative to the samples directory (``tier0/x.kml``); ``module`` is
    the workspace module the sample demonstrates: ``"measure"`` for Tier 0, otherwise an
    analysis kind (``airspace``, ``survey``, ``evtol_siting``, ``show``, ``flight_audit``).
    """

    name: str
    title: str
    description: str
    filename: str
    module: str

    @property
    def is_report(self) -> bool:
        """A FlightMD report JSON (opened in the flight audit, not loaded as a dataset)."""
        return self.module == "flight_audit" and self.filename.lower().endswith(".json")


def _num(value: float, decimals: int) -> str:
    text = f"{float(value):.{decimals}f}"
    if text.startswith("-") and float(text) == 0.0:
        return text[1:]
    return text


def _kml_coords(lons: Sequence[float], lats: Sequence[float], alts: Sequence[float] | None = None) -> str:
    if alts is None:
        return " ".join(f"{_num(lo, 9)},{_num(la, 9)}" for lo, la in zip(lons, lats, strict=True))
    return " ".join(
        f"{_num(lo, 9)},{_num(la, 9)},{_num(z, 3)}" for lo, la, z in zip(lons, lats, alts, strict=True)
    )


def _kml_document(name: str, body: str, description: str | None = None) -> bytes:
    desc = f"<description>{escape(description)}</description>\n" if description else ""
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<kml xmlns="{_KML_NS}" xmlns:gx="{_GX_NS}">\n'
        "<Document>\n"
        f"<name>{escape(name)}</name>\n"
        f"{desc}"
        f"{body}"
        "</Document>\n"
        "</kml>\n"
    )
    return text.encode("utf-8")


def _kml_polygon(outer: str, holes: Sequence[str] = (), altitude_mode: str | None = None) -> str:
    mode = f"<altitudeMode>{altitude_mode}</altitudeMode>" if altitude_mode else ""
    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{h}</coordinates></LinearRing></innerBoundaryIs>"
        for h in holes
    )
    return (
        f"<Polygon>{mode}<outerBoundaryIs><LinearRing><coordinates>{outer}</coordinates>"
        f"</LinearRing></outerBoundaryIs>{inner}</Polygon>"
    )


def _extended_data(values: dict[str, str]) -> str:
    items = "".join(
        f'<Data name="{escape(k)}"><value>{escape(v)}</value></Data>' for k, v in values.items()
    )
    return f"<ExtendedData>{items}</ExtendedData>"


def _placemark(name: str, geometry: str, extended: dict[str, str] | None = None, description: str | None = None) -> str:
    desc = f"<description>{escape(description)}</description>" if description else ""
    ext = _extended_data(extended) if extended else ""
    return f"<Placemark><name>{escape(name)}</name>{desc}{ext}{geometry}</Placemark>\n"


def _zip_bytes(entries: Sequence[tuple[str, bytes]]) -> bytes:
    """Deterministic zip archive (fixed timestamps, Unix attributes, deflate level 6)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for arcname, data in entries:
            info = zipfile.ZipInfo(arcname, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=6)
    return buffer.getvalue()


def _local_ring_to_lonlat(frame: LocalFrame, xy: Sequence[tuple[float, float]]) -> tuple[list[float], list[float]]:
    xs = np.array([p[0] for p in xy], dtype=float)
    ys = np.array([p[1] for p in xy], dtype=float)
    lons, lats = frame.lonlat(xs, ys)
    return [float(v) for v in lons], [float(v) for v in lats]


def _rect_ring(xmin: float, ymin: float, xmax: float, ymax: float) -> list[tuple[float, float]]:
    """Closed counter-clockwise rectangle ring."""
    return [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]


def _square_kml(lon0: float, lat0: float, label: str) -> bytes:
    transformer = get_transformer(local_area_crs(lon0, lat0), WGS84)
    h = SQUARE_HALF_SIDE_M
    ring = _rect_ring(-h, -h, h, h)
    lons, lats = transformer.transform([p[0] for p in ring], [p[1] for p in ring])
    lons_l = [float(v) for v in lons]
    lats_l = [float(v) for v in lats]
    lons_l[-1], lats_l[-1] = lons_l[0], lats_l[0]
    body = _placemark(
        f"1 km square {label}",
        _kml_polygon(_kml_coords(lons_l, lats_l)),
        extended={"side_m": "1000", "expected_area_m2": "1000000", "expected_perimeter_m": "4000"},
        description=(
            "Corners laid out at ±500 m on a Lambert Azimuthal Equal-Area projection centred on "
            f"({lon0:g}, {lat0:g}) and stored as lon/lat. Expected area 1,000,000 m²."
        ),
    )
    return _kml_document(f"1 km square {label}", body)


def _build_square_equator() -> bytes:
    return _square_kml(0.0, 0.0, "at the equator")


def _build_square_60n() -> bytes:
    return _square_kml(10.0, 60.0, "at 60°N")


def _build_mixed_features() -> bytes:
    frame = LocalFrame(77.6010, 12.9750)
    p_lon, p_lat = _local_ring_to_lonlat(frame, [(-150.0, 120.0)])
    l_lon, l_lat = _local_ring_to_lonlat(frame, [(-300.0, -200.0), (-50.0, -120.0), (250.0, -180.0)])
    outer = _rect_ring(0.0, 0.0, 300.0, 200.0)
    hole = [(100.0, 60.0), (100.0, 140.0), (200.0, 140.0), (200.0, 60.0), (100.0, 60.0)]
    o_lon, o_lat = _local_ring_to_lonlat(frame, outer)
    h_lon, h_lat = _local_ring_to_lonlat(frame, hole)
    o_lon[-1], o_lat[-1] = o_lon[0], o_lat[0]
    h_lon[-1], h_lat[-1] = h_lon[0], h_lat[0]
    points = _placemark(
        "GNSS base station",
        f"<Point><coordinates>{_kml_coords(p_lon, p_lat)}</coordinates></Point>",
        extended={"kind": "base_station", "antenna_height_m": "1.80", "surveyor": "Field team 2"},
    )
    lines = _placemark(
        "Access track",
        f"<LineString><tessellate>1</tessellate><coordinates>{_kml_coords(l_lon, l_lat)}</coordinates></LineString>",
        extended={"kind": "track", "surface": "gravel"},
    )
    areas = _placemark(
        "Survey block with pond",
        _kml_polygon(_kml_coords(o_lon, o_lat), [_kml_coords(h_lon, h_lat)]),
        extended={"kind": "survey_block", "priority": "high", "note": "Pond excluded (inner ring)"},
    )
    body = (
        "<Folder><name>Survey</name>\n"
        f"<Folder><name>Points</name>\n{points}</Folder>\n"
        f"<Folder><name>Lines</name>\n{lines}</Folder>\n"
        f"<Folder><name>Areas</name>\n{areas}</Folder>\n"
        "</Folder>\n"
    )
    return _kml_document("Mixed features", body, "A point, a line and a polygon with a hole in nested folders.")


def _build_climb_line() -> bytes:
    frame = LocalFrame(*CLIMB_LINE_CENTRE)
    h = CLIMB_LINE_HALF_LENGTH_M
    lons, lats = _local_ring_to_lonlat(frame, [(-h, 0.0), (h, 0.0)])
    coords = _kml_coords(lons, lats, [0.0, CLIMB_LINE_TOP_M])
    body = _placemark(
        "Climb 0 to 100 m",
        "<LineString><altitudeMode>relativeToGround</altitudeMode>"
        f"<coordinates>{coords}</coordinates></LineString>",
        extended={"expected_length_2d_m": "1000.00", "expected_length_3d_m": "1004.99"},
        description="1,000 m west-to-east line climbing from 0 to 100 m above ground.",
    )
    return _kml_document("Climbing line", body)


def _build_bowtie() -> bytes:
    lons = [77.6000, 77.6100, 77.6100, 77.6000, 77.6000]
    lats = [12.9700, 12.9800, 12.9700, 12.9800, 12.9700]
    body = _placemark(
        "Bow-tie (self-intersecting)",
        _kml_polygon(_kml_coords(lons, lats)),
        description="The ring crosses itself; ingest repairs it with make_valid and flags it.",
    )
    return _kml_document("Invalid bow-tie polygon", body)


def _build_corrupt() -> bytes:
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<kml xmlns="{_KML_NS}">\n'
        "<Document>\n<name>Corrupt file</name>\n"
        "<Placemark><name>Truncated polygon</name><Polygon><outerBoundaryIs><LinearRing>"
        "<coordinates>77.5900,12.9700 77.6000,12.9700 77.60"
    )
    return text.encode("utf-8")


def _shapefile_parts(
    rings: Sequence[Sequence[tuple[float, float]]],
    records: Sequence[Sequence[str]],
    fields: Sequence[tuple[str, int]],
) -> tuple[bytes, bytes, bytes]:
    """Single-part polygon shapefile (.shp, .shx, .dbf) from closed clockwise rings."""
    contents: list[bytes] = []
    for ring in rings:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        content = struct.pack("<i4d2i", 5, min(xs), min(ys), max(xs), max(ys), 1, len(ring))
        content += struct.pack("<i", 0)
        content += b"".join(struct.pack("<2d", x, y) for x, y in ring)
        contents.append(content)

    all_x = [p[0] for ring in rings for p in ring]
    all_y = [p[1] for ring in rings for p in ring]
    bbox = (min(all_x), min(all_y), max(all_x), max(all_y), 0.0, 0.0, 0.0, 0.0)

    def header(length_bytes: int) -> bytes:
        return struct.pack(">7i", 9994, 0, 0, 0, 0, 0, length_bytes // 2) + struct.pack("<2i8d", 1000, 5, *bbox)

    shp_records = b""
    shx_records = b""
    offset = 100
    for number, content in enumerate(contents, start=1):
        shx_records += struct.pack(">2i", offset // 2, len(content) // 2)
        record = struct.pack(">2i", number, len(content) // 2) + content
        shp_records += record
        offset += len(record)
    shp = header(100 + len(shp_records)) + shp_records
    shx = header(100 + len(shx_records)) + shx_records

    record_length = 1 + sum(length for _n, length in fields)
    header_length = 32 + 32 * len(fields) + 1
    dbf = struct.pack("<4BIHH20x", 3, 126, 1, 1, len(records), header_length, record_length)
    for field_name, length in fields:
        dbf += field_name.encode("ascii").ljust(11, b"\0")[:11] + b"C" + b"\0" * 4 + bytes([length, 0]) + b"\0" * 14
    dbf += b"\r"
    for values in records:
        row = b" "
        for value, (_field_name, length) in zip(values, fields, strict=True):
            row += value.encode("utf-8")[:length].ljust(length, b" ")
        dbf += row
    dbf += b"\x1a"
    return shp, shx, dbf


def _plots_components() -> tuple[bytes, bytes, bytes]:
    rings: list[list[tuple[float, float]]] = []
    records: list[list[str]] = []
    for name, crop, owner, xmin, ymin, xmax, ymax in _PLOTS:
        # Shapefile outer rings are clockwise.
        rings.append(
            [(xmin, ymin), (xmin, ymax), (xmax, ymax), (xmax, ymin), (xmin, ymin)]
        )
        records.append([name, crop, owner])
    return _shapefile_parts(rings, records, _PLOT_FIELDS)


def _build_plots_shapefile() -> bytes:
    shp, shx, dbf = _plots_components()
    return _zip_bytes(
        [
            ("plots.shp", shp),
            ("plots.shx", shx),
            ("plots.dbf", dbf),
            ("plots.prj", PLOTS_ESRI_WKT.encode("ascii")),
            ("plots.cpg", b"UTF-8"),
        ]
    )


def _build_no_prj() -> bytes:
    shp, shx, dbf = _plots_components()
    return _zip_bytes(
        [
            ("plots_no_prj.shp", shp),
            ("plots_no_prj.shx", shx),
            ("plots_no_prj.dbf", dbf),
            ("plots_no_prj.cpg", b"UTF-8"),
        ]
    )


def _build_zipslip() -> bytes:
    harmless = _kml_document(
        "Harmless decoy",
        _placemark("Decoy point", "<Point><coordinates>77.5946,12.9716</coordinates></Point>"),
    )
    return _zip_bytes(
        [
            ("../evil.txt", b"This file must never be written outside the upload folder.\n"),
            ("doc.kml", harmless),
        ]
    )


def _build_zipbomb() -> bytes:
    buffer = io.BytesIO()
    chunk = bytes(1024 * 1024)
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("zeros.bin", date_time=_ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        with archive.open(info, "w") as handle:
            for _ in range(ZIPBOMB_UNCOMPRESSED_BYTES // len(chunk)):
                handle.write(chunk)
    return buffer.getvalue()


def _build_bengaluru_survey() -> bytes:
    lons = [77.485, 77.497, 77.497, 77.490, 77.485, 77.485]
    lats = [12.900, 12.900, 12.904, 12.910, 12.910, 12.900]
    body = _placemark(
        "Survey area",
        _kml_polygon(_kml_coords(lons, lats)),
        extended={"client": "Demo agri cooperative", "purpose": "Orthomosaic, 3 cm GSD"},
        description="Pentagon of about 1.19 km² west of Bengaluru for the survey estimator and airspace check.",
    )
    return _kml_document("Bengaluru survey", body)


def _build_bengaluru_evtol() -> bytes:
    min_lon, min_lat, max_lon, max_lat = 77.630, 12.805, 77.790, 13.010
    ring = [[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]
    collection = {
        "type": "FeatureCollection",
        "name": "bengaluru_evtol",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Study area", "role": "study_area"},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            },
            {
                "type": "Feature",
                "properties": {"name": "Whitefield", "role": "origin"},
                "geometry": {"type": "Point", "coordinates": [77.750, 12.970]},
            },
            {
                "type": "Feature",
                "properties": {"name": "Electronic City", "role": "destination"},
                "geometry": {"type": "Point", "coordinates": [77.670, 12.845]},
            },
        ],
    }
    return (json.dumps(collection, indent=2) + "\n").encode("utf-8")


def _ease(tau: float) -> float:
    """s(τ) = (1 − cos πτ)/2 with τ clamped to [0, 1]."""
    tau = min(1.0, max(0.0, tau))
    return (1.0 - math.cos(math.pi * tau)) / 2.0


def show_position(index: int, t: float, *, conflict: bool) -> tuple[float, float, float]:
    """Local ENU position (m) of drone ``index`` at ``t`` seconds (see the module docstring)."""
    base = 2.0 * math.pi * index / SHOW_DRONE_COUNT
    swapper = conflict and index in SHOW_SWAP
    top = SHOW_CONFLICT_ALTITUDE_M if (conflict and index == SHOW_SWAP[1]) else SHOW_ALTITUDE_M

    if t <= 20.0:
        z = top * _ease(t / 20.0)
    elif t <= 96.0:
        z = top
    else:
        z = top * (1.0 - _ease((t - 96.0) / 30.0))

    rotation = SHOW_ROTATION_RAD * _ease((t - 20.0) / 20.0)
    if t <= 56.0:
        radius = SHOW_RING_RADIUS_M
    elif t <= 76.0:
        radius = SHOW_RING_RADIUS_M - (SHOW_RING_RADIUS_M - SHOW_INNER_RADIUS_M) * _ease((t - 56.0) / 20.0)
    elif t <= 96.0:
        radius = SHOW_INNER_RADIUS_M + (SHOW_RING_RADIUS_M - SHOW_INNER_RADIUS_M) * _ease((t - 76.0) / 20.0)
    else:
        radius = SHOW_RING_RADIUS_M

    if swapper and 40.0 < t < 56.0:
        # Straight eased path through the centre: p = P0 · (1 − 2 s(τ)) = P0 · cos(πτ).
        factor = math.cos(math.pi * (t - 40.0) / 16.0)
        angle = base + rotation
        x = SHOW_RING_RADIUS_M * math.cos(angle) * factor
        y = SHOW_RING_RADIUS_M * math.sin(angle) * factor
    else:
        angle = base + rotation + (math.pi if (swapper and t >= 56.0) else 0.0)
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
    return round(x, 6) + 0.0, round(y, 6) + 0.0, round(z, 6) + 0.0


def show_keyframes(*, conflict: bool) -> tuple[list[str], NDArray[np.float64], NDArray[np.float64]]:
    """1 Hz keyframes: ``(drone_ids, times (T,), positions (T, N, 3) local ENU metres)``."""
    ids = [f"D{i:02d}" for i in range(SHOW_DRONE_COUNT)]
    times = np.arange(SHOW_DURATION_S + 1, dtype=np.float64)
    positions = np.empty((times.size, SHOW_DRONE_COUNT, 3), dtype=np.float64)
    for k, t in enumerate(times):
        for i in range(SHOW_DRONE_COUNT):
            positions[k, i] = show_position(i, float(t), conflict=conflict)
    return ids, times, positions


def _show_kml(conflict: bool) -> bytes:
    ids, times, positions = show_keyframes(conflict=conflict)
    frame = LocalFrame(SHOW_ORIGIN[0], SHOW_ORIGIN[1])
    whens = [(SHOW_START_UTC + timedelta(seconds=int(t))).strftime("%Y-%m-%dT%H:%M:%SZ") for t in times]
    parts: list[str] = ["<Folder><name>Drones</name>\n"]
    for i, drone in enumerate(ids):
        lons, lats = frame.lonlat(positions[:, i, 0], positions[:, i, 1])
        lines = ["<Placemark><name>", drone, "</name><gx:Track><altitudeMode>relativeToGround</altitudeMode>\n"]
        lines.extend(f"<when>{w}</when>\n" for w in whens)
        lines.extend(
            f"<gx:coord>{_num(lo, 9)} {_num(la, 9)} {_num(z, 3)}</gx:coord>\n"
            for lo, la, z in zip(lons, lats, positions[:, i, 2], strict=True)
        )
        lines.append("</gx:Track></Placemark>\n")
        parts.append("".join(lines))
    parts.append("</Folder>\n")

    h = SHOW_BOX_HALF_M
    box_lon, box_lat = _local_ring_to_lonlat(frame, _rect_ring(-h, -h, h, h))
    aud_lon, aud_lat = _local_ring_to_lonlat(frame, _rect_ring(*SHOW_AUDIENCE_XY))
    box_lon[-1], box_lat[-1] = box_lon[0], box_lat[0]
    aud_lon[-1], aud_lat[-1] = aud_lon[0], aud_lat[0]
    parts.append("<Folder><name>Site</name>\n")
    parts.append(_placemark("Show box", _kml_polygon(_kml_coords(box_lon, box_lat)), extended={"size_m": "120 x 120"}))
    parts.append(_placemark("Audience", _kml_polygon(_kml_coords(aud_lon, aud_lat)), extended={"capacity": "2000"}))
    parts.append("</Folder>\n")

    if conflict:
        name = "50-drone show (with injected conflict)"
        desc = "D07 and D32 swap through the ring centre and pass 0.80 m apart at t = 48.0 s."
    else:
        name = "50-drone show (clean)"
        desc = "Same choreography without the swap: no conflict, no dynamic or geofence violation."
    return _kml_document(name, "".join(parts), desc)


def _build_show_conflict_kml() -> bytes:
    return _show_kml(conflict=True)


def _build_show_clean_kml() -> bytes:
    return _show_kml(conflict=False)


def _build_show_csv() -> bytes:
    ids, times, positions = show_keyframes(conflict=True)
    lon0, lat0, alt0 = SHOW_ORIGIN
    lines = [f"# origin={lon0:.4f},{lat0:.4f},{alt0:g}", "drone_id,t,x,y,z"]
    for i, drone in enumerate(ids):
        for k, t in enumerate(times):
            x, y, z = positions[k, i]
            lines.append(f"{drone},{_num(t, 1)},{_num(x, 4)},{_num(y, 4)},{_num(z, 4)}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def flight_height_above_takeoff(distance_m: float) -> float:
    """Height above take-off (m) of the sample flight at ``distance_m`` along the track."""
    d = distance_m
    if d <= 300.0:
        return 100.0 * d / 300.0
    if d <= 1100.0:
        return 100.0
    if d <= 1200.0:
        return 100.0 + 35.0 * (d - 1100.0) / 100.0
    if d <= 1600.0:
        return 135.0
    if d <= 1700.0:
        return 135.0 - 35.0 * (d - 1600.0) / 100.0
    if d <= FLIGHT_TOTAL_DISTANCE_M - 300.0:
        return 100.0
    return max(0.0, 100.0 * (FLIGHT_TOTAL_DISTANCE_M - d) / 300.0)


def _flight_local_xy(distance_m: float) -> tuple[float, float]:
    """East leg, short north leg, west leg back (local metres from home)."""
    d = distance_m
    if d <= FLIGHT_OUT_LEG_M:
        return d, 0.0
    if d <= FLIGHT_OUT_LEG_M + FLIGHT_CROSS_LEG_M:
        return FLIGHT_OUT_LEG_M, d - FLIGHT_OUT_LEG_M
    return FLIGHT_OUT_LEG_M - (d - FLIGHT_OUT_LEG_M - FLIGHT_CROSS_LEG_M), FLIGHT_CROSS_LEG_M


def _flight_report(schema_version: str) -> dict[str, object]:
    count = int(round(FLIGHT_TOTAL_DISTANCE_M / FLIGHT_POINT_SPACING_M)) + 1
    distances = [k * FLIGHT_POINT_SPACING_M for k in range(count)]
    xy = [_flight_local_xy(d) for d in distances]
    frame = LocalFrame(*FLIGHT_HOME)
    lons, lats = _local_ring_to_lonlat(frame, xy)
    alts = [FLIGHT_HOME_ALT_AMSL_M + flight_height_above_takeoff(d) for d in distances]
    gps_path = [[round(la, 7), round(lo, 7), round(a, 2)] for lo, la, a in zip(lons, lats, alts, strict=True)]
    hdop = [round(0.62 + 0.18 * (0.5 + 0.5 * math.sin(k / 17.0)), 2) for k in range(count)]
    duration_s = FLIGHT_TOTAL_DISTANCE_M / FLIGHT_CRUISE_SPEED_MS + 24.0
    is_v15 = schema_version == "1.5"

    metadata: dict[str, object] = {
        "gps_path": gps_path,
        "location_name": "Bengaluru, India",
        "log_start_utc": "2026-03-14T06:12:08Z",
        "flight_duration_s": duration_s,
        "max_altitude_m": FLIGHT_MAX_HEIGHT_M,
        "max_speed_ms": 8.4,
        "total_distance_m": FLIGHT_TOTAL_DISTANCE_M,
        "vehicle_type": "Quadcopter",
        "firmware": "ArduCopter V4.5.7",
        "weather": {
            "temperature_c": 27.5,
            "wind_speed_ms": 3.2,
            "wind_direction_deg": 250,
            "humidity_pct": 58,
            "conditions": "Partly cloudy",
            "source": "open-meteo archive",
        },
    }
    if is_v15:
        metadata["gps_path_hdop"] = hdop

    report: dict[str, object] = {
        "report_id": "fmd-20260314-bng-0042" if is_v15 else "fmd-20260314-bng-0042-legacy",
        "schema_version": schema_version,
        "overall_score": 78.4,
        "score_label": "Good",
    }
    if is_v15:
        report["letter_grade"] = "B+"
    report.update(
        {
            "executive_summary": (
                "Healthy survey flight west of Bengaluru. Vibration and power were nominal and GPS "
                "quality stayed good (HDOP under 0.8). The aircraft climbed to 135 m above take-off "
                "for about 486 m of the track, above the common 120 m ceiling."
            ),
            "metadata": metadata,
            "findings": [
                {
                    "id": "F-001",
                    "severity": "warning",
                    "category": "altitude",
                    "title": "Altitude above 120 m",
                    "detail": "Height above take-off reached 135 m between about 1,157 m and 1,643 m along the track.",
                },
                {
                    "id": "F-002",
                    "severity": "info",
                    "category": "gps",
                    "title": "GPS quality good",
                    "detail": "HDOP stayed below 0.8 with 18-21 satellites in view.",
                },
            ],
            "param_change_sheet": (
                [
                    {
                        "parameter": "FENCE_ALT_MAX",
                        "current": 150.0,
                        "recommended": 120.0,
                        "reason": "Keep the altitude fence at the 120 m operating ceiling.",
                    }
                ]
                if is_v15
                else []
            ),
            "analyser_results": [
                {"analyser": "vibration", "status": "pass", "score": 91.0, "summary": "Vibration within limits."},
                {"analyser": "power", "status": "pass", "score": 86.5, "summary": "Battery sag nominal."},
                {"analyser": "gps", "status": "pass", "score": 94.0, "summary": "No glitches or fix loss."},
                {"analyser": "altitude", "status": "warn", "score": 55.0, "summary": "Climbed above 120 m."},
            ],
            "processing_time_ms": 1843 if is_v15 else 2210,
            "file_name": "2026-03-14_06-12-08_survey.bin",
            "file_size_bytes": 18_734_592,
        }
    )
    return report


def _build_flightmd_v15() -> bytes:
    return (json.dumps(_flight_report("1.5"), indent=2) + "\n").encode("utf-8")


def _build_flightmd_v12() -> bytes:
    return (json.dumps(_flight_report("1.2"), indent=2) + "\n").encode("utf-8")


def training_flight_position(t: float) -> tuple[float, float, float]:
    """``(east, north, height above take-off)`` in metres of the GPX training flight at ``t`` s.

    Climb 2.5 m/s to 100 m; east 400 m at 5 m/s; climb 1.5 m/s to 130 m; north 100 m; descend
    to 100 m; east to x = 600 m (100 m outside the training box); south 100 m; west home; descend.
    """
    if t <= 40.0:
        return 0.0, 0.0, 2.5 * t
    if t <= 120.0:
        return 5.0 * (t - 40.0), 0.0, 100.0
    if t <= 140.0:
        return 400.0, 0.0, 100.0 + 1.5 * (t - 120.0)
    if t <= 160.0:
        return 400.0, 5.0 * (t - 140.0), 130.0
    if t <= 180.0:
        return 400.0, 100.0, 130.0 - 1.5 * (t - 160.0)
    if t <= 220.0:
        return 400.0 + 5.0 * (t - 180.0), 100.0, 100.0
    if t <= 240.0:
        return 600.0, 100.0 - 5.0 * (t - 220.0), 100.0
    if t <= 360.0:
        return 600.0 - 5.0 * (t - 240.0), 0.0, 100.0
    return 0.0, 0.0, max(0.0, 100.0 - 2.5 * (t - 360.0))


def _gpx_time(seconds: float) -> str:
    return (TRAINING_START_UTC + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_training_flight_gpx() -> bytes:
    frame = LocalFrame(*TRAINING_HOME)
    times = list(range(TRAINING_DURATION_S + 1))
    xyh = [training_flight_position(float(t)) for t in times]
    lons, lats = _local_ring_to_lonlat(frame, [(x, y) for x, y, _ in xyh])
    trkpts = "".join(
        f'<trkpt lat="{_num(la, 9)}" lon="{_num(lo, 9)}"><ele>{_num(TRAINING_HOME_ELE_M + h, 2)}</ele>'
        f"<time>{_gpx_time(t)}</time></trkpt>\n"
        for lo, la, (_, _, h), t in zip(lons, lats, xyh, times, strict=True)
    )
    box_lon, box_lat = _local_ring_to_lonlat(frame, _rect_ring(*TRAINING_BOX_XY))
    box_lon[-1], box_lat[-1] = box_lon[0], box_lat[0]
    rtepts = "".join(
        f'<rtept lat="{_num(la, 9)}" lon="{_num(lo, 9)}"></rtept>\n' for lo, la in zip(box_lon, box_lat, strict=True)
    )
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="AirBrief sample generator" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"<metadata><name>Training flight (demo)</name><time>{_gpx_time(0)}</time></metadata>\n"
        f'<wpt lat="{_num(TRAINING_HOME[1], 9)}" lon="{_num(TRAINING_HOME[0], 9)}"><ele>{_num(TRAINING_HOME_ELE_M, 2)}</ele>'
        f"<time>{_gpx_time(0)}</time><name>Take-off</name><sym>Flag, Green</sym></wpt>\n"
        "<rte><name>Training box</name><desc>Permitted training area, 600 m x 400 m.</desc>\n"
        f"{rtepts}</rte>\n"
        "<trk><name>Training flight</name><desc>1 Hz position fixes from the flight controller.</desc><trkseg>\n"
        f"{trkpts}</trkseg></trk>\n"
        "</gpx>\n"
    )
    return text.encode("utf-8")


_CATALOGUE: tuple[tuple[SampleSpec, Callable[[], bytes]], ...] = (
    (
        SampleSpec(
            "square_1km_equator",
            "1 km square at the equator",
            "Known answer: 1,000,000 m² and 4,000 m perimeter (K1).",
            "tier0/square_1km_equator.kml",
            "measure",
        ),
        _build_square_equator,
    ),
    (
        SampleSpec(
            "square_1km_60N",
            "1 km square at 60°N",
            "Same square at high latitude: still 1,000,000 m² (K2).",
            "tier0/square_1km_60N.kml",
            "measure",
        ),
        _build_square_60n,
    ),
    (
        SampleSpec(
            "plots_shapefile",
            "Farm plots (Shapefile, UTM 43N)",
            "Three plots near Bengaluru in EPSG:32643 with attributes; grid areas 20,000, 22,500 and 15,000 m².",
            "tier0/plots_shapefile.zip",
            "measure",
        ),
        _build_plots_shapefile,
    ),
    (
        SampleSpec(
            "mixed_features",
            "Mixed KML features",
            "A point, a line and a polygon with a hole in nested folders with ExtendedData.",
            "tier0/mixed_features.kml",
            "measure",
        ),
        _build_mixed_features,
    ),
    (
        SampleSpec(
            "climb_line_z",
            "Climbing line (3D)",
            "1,000 m line climbing 0 to 100 m: 3D length 1,004.99 m (K5).",
            "tier0/climb_line_z.kml",
            "measure",
        ),
        _build_climb_line,
    ),
    (
        SampleSpec(
            "bowtie_invalid",
            "Self-intersecting polygon",
            "A bow-tie polygon that is repaired and flagged.",
            "tier0/bowtie_invalid.kml",
            "measure",
        ),
        _build_bowtie,
    ),
    (
        SampleSpec(
            "no_prj",
            "Shapefile without .prj",
            "The CRS is unknown, so features are listed but not measured.",
            "tier0/no_prj.zip",
            "measure",
        ),
        _build_no_prj,
    ),
    (
        SampleSpec(
            "attack_zipslip",
            "Zip-slip attack",
            "An archive entry named ../evil.txt; must be rejected as unsafe_archive.",
            "tier0/attack_zipslip.zip",
            "measure",
        ),
        _build_zipslip,
    ),
    (
        SampleSpec(
            "corrupt",
            "Corrupt KML",
            "Truncated XML; processing fails with corrupt_file.",
            "tier0/corrupt.kml",
            "measure",
        ),
        _build_corrupt,
    ),
    (
        SampleSpec(
            "zipbomb_small",
            "Zip bomb (201 MiB of zeros)",
            "About 0.2 MB compressed, 201 MiB uncompressed; must be rejected as archive_too_large.",
            "tier0/zipbomb_small.zip",
            "measure",
        ),
        _build_zipbomb,
    ),
    (
        SampleSpec(
            "bengaluru_survey",
            "Bengaluru survey",
            "A 1.19 km² survey area for the airspace check and the survey estimator.",
            "modules/bengaluru_survey.kml",
            "survey",
        ),
        _build_bengaluru_survey,
    ),
    (
        SampleSpec(
            "bengaluru_evtol",
            "Bengaluru eVTOL",
            "Study area with Whitefield and Electronic City for vertiport siting and corridors.",
            "modules/bengaluru_evtol.geojson",
            "evtol_siting",
        ),
        _build_bengaluru_evtol,
    ),
    (
        SampleSpec(
            "show_50_drones",
            "50-drone show",
            "A 126 s show with one injected conflict: D07 and D32 at 0.80 m at t = 48.0 s (K8).",
            "modules/show_50_drones.kml",
            "show",
        ),
        _build_show_conflict_kml,
    ),
    (
        SampleSpec(
            "show_50_drones_clean",
            "50-drone show (clean)",
            "The same show without the swap: no conflicts or violations.",
            "modules/show_50_drones_clean.kml",
            "show",
        ),
        _build_show_clean_kml,
    ),
    (
        SampleSpec(
            "show_50_drones_csv",
            "50-drone show (CSV)",
            "The conflict show as drone_id,t,x,y,z in local metres with an origin header.",
            "modules/show_50_drones.csv",
            "show",
        ),
        _build_show_csv,
    ),
    (
        SampleSpec(
            "flightmd_report_v15",
            "FlightMD report (schema 1.5)",
            "Survey flight reaching 135 m above take-off; above 120 m from 1,157 m to 1,643 m (K7).",
            "flightmd/flightmd_report_v15.json",
            "flight_audit",
        ),
        _build_flightmd_v15,
    ),
    (
        SampleSpec(
            "flightmd_report_v12",
            "FlightMD report (schema 1.2)",
            "The same flight in the older 1.2 layout (no letter grade, no HDOP series).",
            "flightmd/flightmd_report_v12.json",
            "flight_audit",
        ),
        _build_flightmd_v12,
    ),
    (
        SampleSpec(
            "training_flight_gpx",
            "Training flight (GPX)",
            "A timed 400 s GPX track: 130 m above take-off for 33.3 s and 60 s outside its training box.",
            "modules/training_flight.gpx",
            "flight_audit",
        ),
        _build_training_flight_gpx,
    ),
)

SAMPLES: dict[str, SampleSpec] = {spec.name: spec for spec, _builder in _CATALOGUE}
_BUILDERS: dict[str, Callable[[], bytes]] = {spec.name: builder for spec, builder in _CATALOGUE}


@lru_cache(maxsize=None)
def _cached_build(name: str) -> bytes:
    return _BUILDERS[name]()


def build_sample(name: str) -> bytes:
    """The bytes of sample ``name``, the same on every call; an unknown name is ``not_found``."""
    if name not in _BUILDERS:
        raise AirBriefError(
            "not_found",
            f"Unknown sample '{name}'",
            details={"name": name, "known": sorted(SAMPLES)},
        )
    return _cached_build(name)


def write_all(dest_dir: str | Path) -> list[Path]:
    """Write every sample below ``dest_dir`` (creating sub-folders) and return the paths."""
    root = Path(dest_dir)
    written: list[Path] = []
    for name, spec in SAMPLES.items():
        path = root / spec.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(build_sample(name))
        written.append(path)
    return written
