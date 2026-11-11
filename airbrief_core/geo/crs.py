"""Coordinate reference system helpers.

* :func:`resolve_crs` turns user text (``EPSG:32643``, ``32643``, WKT, ESRI ``.prj`` WKT,
  PROJ strings) into a :class:`pyproj.CRS`. Text that would make PROJ open a file (grids, init
  files) is refused, and PROJ networking is switched off when this module is imported.
* :func:`local_area_crs` / :func:`local_length_crs` build the per-feature measurement
  projections described in docs/SPEC.md ("Measurement rules"): Lambert Azimuthal
  Equal-Area for area and Transverse Mercator with ``k=1`` for lengths, both on the WGS84
  ellipsoid and centred on the feature.
* UTM helpers are used only for human-readable context in measurements.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

from pyproj import CRS, network
from pyproj.exceptions import CRSError

from airbrief_core.errors import AnalysisError, IngestError

# Even with PROJ_NETWORK=ON in the environment: user CRS text must never make PROJ download.
network.set_network_enabled(False)

WGS84: CRS = CRS.from_epsg(4326)
"""Geographic WGS84 (lon/lat when used with ``always_xy=True`` transformers)."""

__all__ = [
    "WGS84",
    "check_crs_text",
    "crs_label",
    "horizontal_crs",
    "is_geographic",
    "local_area_crs",
    "local_area_proj4",
    "local_length_crs",
    "local_length_proj4",
    "resolve_crs",
    "try_resolve_crs",
    "unit_factor",
    "utm_epsg_for",
    "utm_zone_label",
]


def _fmt(value: float) -> str:
    """Format a coordinate for a PROJ string: fixed precision, no trailing zeros, no ``-0``."""
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        return "0"
    return text


# PROJ string options and operations that read grid, init or model files, "@" grid names, and
# the WKT keywords that carry them. "+nadgrids=@null" (no datum shift) is in GDAL's EPSG:3857 WKT.
_NULL_GRID = re.compile(r"\+?nadgrids\s*=\s*@null\b", re.IGNORECASE)
_PROJ_FILES = re.compile(
    r"(?:^|[\s+])(?:nadgrids|geoidgrids|grids|xy_grids|z_grids|init|file|model)\s*="
    r"|\bproj\s*=\s*(?:\w*gridshift|deformation|tinshift|defmodel)\b"
    r"|@",
    re.IGNORECASE,
)
_WKT_FILES = re.compile(r"(?i:PROJ4_GRIDS|PARAMETERFILE)|\bGRIDS\b")


def _shown(raw: str) -> str:
    return raw if len(raw) <= 200 else raw[:200] + "..."


def _file_reference(raw: str) -> IngestError:
    return IngestError(
        "invalid_crs",
        "The coordinate reference system refers to grid or init files (+nadgrids, +geoidgrids, "
        "+init, @ grid names, PARAMETERFILE…), which are not read. Use an EPSG code or plain WKT.",
        details={"crs": _shown(raw), "rule": "file_reference"},
    )


def check_crs_text(text: str) -> None:
    """Refuse CRS text that would make PROJ open files: the grid, init and model options of
    PROJ strings (``+nadgrids``, ``+geoidgrids``, ``+grids``, ``+xy_grids``, ``+z_grids``,
    ``+init=``, ``+file=``…), grid-shift operations (``+proj=hgridshift``…), ``@`` grid names and
    the WKT ``GRIDS``/``PARAMETERFILE`` keywords. ``+nadgrids=@null`` is allowed.

    Raises:
        IngestError: ``invalid_crs`` (``details.rule == "file_reference"``).
    """
    rest = _NULL_GRID.sub("", text)
    if _PROJ_FILES.search(rest) or _WKT_FILES.search(rest):
        raise _file_reference(text)


def resolve_crs(text: str | int | CRS | None) -> CRS | None:
    """Parse a CRS from user input.

    Accepts ``None``/blank (returns ``None``), an existing :class:`CRS`, an EPSG code as
    ``int`` or digit string, ``"EPSG:xxxx"``/authority strings, OGC or ESRI WKT, PROJ
    strings and PROJJSON. Text that refers to grid or init files is refused before PROJ sees
    it (:func:`check_crs_text`), and so is a parsed CRS that still carries a grid file (a
    PROJJSON bound CRS, say). PROJ's own error text is not passed on.

    Raises:
        IngestError: ``invalid_crs`` when non-empty input cannot be parsed or is refused.
    """
    if text is None:
        return None
    if isinstance(text, CRS):
        return text
    if isinstance(text, bool):
        raise IngestError("invalid_crs", "CRS must be text or an EPSG code", details={"crs": str(text)})
    if isinstance(text, int):
        try:
            return CRS.from_epsg(text)
        except CRSError as exc:
            raise IngestError("invalid_crs", f"Unknown EPSG code {text}", details={"crs": str(text)}) from exc
    raw = str(text).strip().lstrip("﻿").strip()
    if not raw:
        return None
    check_crs_text(raw)
    try:
        crs = CRS.from_epsg(int(raw)) if raw.isdigit() else CRS.from_user_input(raw)
        wkt = crs.to_wkt()
    except (CRSError, ValueError, TypeError) as exc:
        # PROJ's message can name files and paths on the server
        raise IngestError(
            "invalid_crs",
            "The coordinate reference system could not be understood",
            details={"crs": _shown(raw)},
        ) from exc
    if _WKT_FILES.search(wkt):
        raise _file_reference(raw)
    return crs


def try_resolve_crs(text: str | int | CRS | None) -> CRS | None:
    """Like :func:`resolve_crs` but returns ``None`` instead of raising on bad input."""
    try:
        return resolve_crs(text)
    except IngestError:
        return None


@lru_cache(maxsize=512)
def _label_for_wkt(wkt: str) -> str:
    crs = CRS.from_wkt(wkt)
    epsg = crs.to_epsg(min_confidence=70)
    if epsg is not None:
        return f"EPSG:{epsg}"
    authority = crs.to_authority(min_confidence=70)
    if authority is not None:
        return f"{authority[0]}:{authority[1]}"
    name = crs.name
    return name if name else "custom"


def crs_label(crs: CRS | None) -> str | None:
    """Short human label: ``EPSG:xxxx`` when identifiable, else ``AUTH:code`` or the CRS name."""
    if crs is None:
        return None
    return _label_for_wkt(crs.to_wkt())


def horizontal_crs(crs: CRS) -> CRS:
    """Return the horizontal component of a compound CRS (the CRS itself otherwise)."""
    if crs.is_compound:
        for sub in crs.sub_crs_list:
            if sub.is_geographic or sub.is_projected:
                return sub
        return crs.sub_crs_list[0]
    return crs


def is_geographic(crs: CRS) -> bool:
    """True when the (horizontal part of the) CRS uses angular lon/lat coordinates."""
    return bool(horizontal_crs(crs).is_geographic)


def unit_factor(crs: CRS) -> float:
    """Metres per unit of the first horizontal axis of a projected CRS (1.0 when unknown)."""
    axes = horizontal_crs(crs).axis_info
    if not axes:
        return 1.0
    factor = axes[0].unit_conversion_factor
    if factor is None or not math.isfinite(factor) or factor <= 0:
        return 1.0
    return float(factor)


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the WGS84 UTM zone containing (lon, lat), with the Norway/Svalbard exceptions."""
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise AnalysisError("invalid_geometry", "Coordinates must be finite", details={"lon": lon, "lat": lat})
    lon_n = ((float(lon) + 180.0) % 360.0) - 180.0
    lat_c = max(-90.0, min(90.0, float(lat)))
    zone = int(math.floor((lon_n + 180.0) / 6.0)) + 1
    zone = max(1, min(60, zone))
    # South-west Norway
    if 56.0 <= lat_c < 64.0 and 3.0 <= lon_n < 12.0:
        zone = 32
    # Svalbard
    if 72.0 <= lat_c < 84.0:
        if 0.0 <= lon_n < 9.0:
            zone = 31
        elif 9.0 <= lon_n < 21.0:
            zone = 33
        elif 21.0 <= lon_n < 33.0:
            zone = 35
        elif 33.0 <= lon_n < 42.0:
            zone = 37
    return (32600 if lat_c >= 0 else 32700) + zone


def utm_zone_label(epsg: int) -> str:
    """``32643`` -> ``"43N"``, ``32719`` -> ``"19S"``.

    Raises:
        AnalysisError: ``invalid_params`` if the code is not a WGS84 UTM zone.
    """
    code = int(epsg)
    if 32601 <= code <= 32660:
        return f"{code - 32600}N"
    if 32701 <= code <= 32760:
        return f"{code - 32700}S"
    raise AnalysisError("invalid_params", f"EPSG:{code} is not a WGS84 UTM zone", details={"epsg": code})


def _check_centre(lon0: float, lat0: float) -> tuple[float, float]:
    if not (math.isfinite(lon0) and math.isfinite(lat0)):
        raise AnalysisError("invalid_geometry", "Projection centre must be finite", details={"lon0": lon0, "lat0": lat0})
    lon = ((float(lon0) + 180.0) % 360.0) - 180.0
    lat = max(-90.0, min(90.0, float(lat0)))
    return lon, lat


def local_area_proj4(lon0: float, lat0: float) -> str:
    """PROJ string of the Lambert Azimuthal Equal-Area projection centred on (lon0, lat0)."""
    lon, lat = _check_centre(lon0, lat0)
    return f"+proj=laea +lat_0={_fmt(lat)} +lon_0={_fmt(lon)} +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"


def local_length_proj4(lon0: float, lat0: float) -> str:
    """PROJ string of the Transverse Mercator (k=1) projection centred on (lon0, lat0)."""
    lon, lat = _check_centre(lon0, lat0)
    return f"+proj=tmerc +lat_0={_fmt(lat)} +lon_0={_fmt(lon)} +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"


@lru_cache(maxsize=1024)
def _crs_from_proj4(proj4: str) -> CRS:
    return CRS.from_proj4(proj4)


def local_area_crs(lon0: float, lat0: float) -> CRS:
    """Equal-area CRS (LAEA, WGS84 ellipsoid, metres) centred on (lon0, lat0)."""
    return _crs_from_proj4(local_area_proj4(lon0, lat0))


def local_length_crs(lon0: float, lat0: float) -> CRS:
    """Length CRS (Transverse Mercator, k=1, WGS84 ellipsoid, metres) centred on (lon0, lat0)."""
    return _crs_from_proj4(local_length_proj4(lon0, lat0))
