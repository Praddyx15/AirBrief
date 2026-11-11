"""ESRI Shapefile reader built on pyshp (module ``shapefile``).

* Geometries are built from ``shape.points`` / ``shape.parts`` / ``shape.z`` so Z values
  survive (M values are ignored).
* Polygon rings are grouped by orientation: clockwise rings are exteriors (per the ESRI
  specification), counter-clockwise rings are holes assigned to the smallest exterior that
  contains them. Files written with the wrong orientation (no clockwise ring) are read with
  every ring as an exterior; geometry repair later resolves overlaps.
* Null shapes become features with ``geometry=None``; MultiPatch is reported unsupported.
* ``.prj`` is parsed with :func:`~airbrief_core.geo.crs.resolve_crs` (no grid or init files);
  an unreadable ``.prj`` adds an issue and leaves the CRS unknown (features are then not measured).
* ``.cpg`` selects the attribute encoding (UTF-8 when absent); undecodable bytes are replaced.
"""

from __future__ import annotations

import codecs
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import shapefile as pyshp
from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from ..errors import IngestError
from ..geo.crs import resolve_crs
from ..models.common import Issue, Severity
from .reader import RawFeature, RawLayer, charge_vertices

_NULL = 0
_POINT_TYPES = {1, 11, 21}
_LINE_TYPES = {3, 13, 23}
_POLYGON_TYPES = {5, 15, 25}
_MULTIPOINT_TYPES = {8, 18, 28}
_Z_TYPES = {11, 13, 15, 18, 31}
_MULTIPATCH = 31
_NAME_KEYS = ("name", "title", "label")

_CPG_ALIASES = {
    "65001": "utf-8",
    "utf8": "utf-8",
    "88591": "latin-1",
    "8859_1": "latin-1",
    "iso88591": "latin-1",
    "ansi": "cp1252",
    "ansi1252": "cp1252",
}


def _sibling(shp_path: Path, suffix: str) -> Path | None:
    """Find ``<stem><suffix>`` next to the .shp, ignoring the case of the extension."""
    exact = shp_path.with_suffix(suffix)
    if exact.exists():
        return exact
    stem = shp_path.stem.lower()
    try:
        for candidate in shp_path.parent.iterdir():
            if candidate.is_file() and candidate.stem.lower() == stem and candidate.suffix.lower() == suffix:
                return candidate
    except OSError:
        return None
    return None


def _encoding_from_cpg(cpg_path: Path | None) -> tuple[str, str | None]:
    """Return ``(python_codec, note)`` for a .cpg file (``utf-8`` if absent or unknown)."""
    if cpg_path is None:
        return "utf-8", None
    try:
        raw = cpg_path.read_bytes()[:64].decode("ascii", errors="ignore").strip()
    except OSError:
        return "utf-8", None
    if not raw:
        return "utf-8", None
    key = raw.lower().replace("-", "").replace(" ", "")
    candidates = [_CPG_ALIASES.get(key, ""), raw, raw.replace(" ", "")]
    if key.isdigit():
        candidates.append(f"cp{key}")
    for name in candidates:
        if not name:
            continue
        try:
            return codecs.lookup(name).name, None
        except LookupError:
            continue
    return "utf-8", f"Unknown code page '{raw}' in .cpg; attributes read as UTF-8."


def read_prj(prj_path: Path) -> tuple[CRS | None, str | None]:
    """Parse a .prj file. Returns ``(crs, problem)``; ``crs`` is None when unreadable."""
    try:
        text = prj_path.read_bytes().decode("utf-8", errors="replace").strip().lstrip("﻿")
    except OSError:  # the OS message names the staging path on the server
        return None, "the .prj file could not be read"
    if not text:
        return None, "the .prj file is empty"
    try:
        return resolve_crs(text), None
    except IngestError as exc:
        if exc.details.get("rule") == "file_reference":
            return None, "the .prj refers to grid or init files, which are not read"
        return None, "the .prj could not be parsed"


def _signed_area(ring: list[tuple[float, ...]]) -> float:
    """Shoelace signed area: positive for counter-clockwise, negative for clockwise."""
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _coords(shape: Any, has_z: bool) -> list[tuple[float, ...]]:
    points = shape.points
    zs = list(getattr(shape, "z", None) or []) if has_z else []
    out: list[tuple[float, ...]] = []
    for i, p in enumerate(points):
        x, y = float(p[0]), float(p[1])
        if has_z:
            if len(p) > 2 and p[2] is not None:
                z = float(p[2])
            elif i < len(zs) and zs[i] is not None:
                z = float(zs[i])
            else:
                z = 0.0
            out.append((x, y, z))
        else:
            out.append((x, y))
    return out


def _split_parts(shape: Any, coords: list[tuple[float, ...]]) -> list[list[tuple[float, ...]]]:
    parts = list(getattr(shape, "parts", None) or [0])
    bounds = [*parts, len(coords)]
    return [coords[bounds[i] : bounds[i + 1]] for i in range(len(parts)) if bounds[i] < bounds[i + 1]]


def _close(ring: list[tuple[float, ...]]) -> list[tuple[float, ...]]:
    if ring and (ring[0][0], ring[0][1]) != (ring[-1][0], ring[-1][1]):
        return [*ring, ring[0]]
    return ring


def build_polygon(rings: list[list[tuple[float, ...]]]) -> Polygon | MultiPolygon | None:
    """Group shapefile rings into polygons by orientation and containment."""
    usable: list[tuple[list[tuple[float, ...]], float]] = []
    for ring in rings:
        closed = _close(ring)
        if len(closed) < 4:
            continue
        area = _signed_area(closed)
        if area == 0.0:
            continue
        usable.append((closed, area))
    if not usable:
        return None

    exteriors = [r for r, a in usable if a < 0]
    holes = [r for r, a in usable if a > 0]
    if not exteriors:
        exteriors, holes = holes, []

    shells = [Polygon([(c[0], c[1]) for c in r]) for r in exteriors]
    assigned: list[list[list[tuple[float, ...]]]] = [[] for _ in exteriors]
    for hole in holes:
        hole_poly = Polygon([(c[0], c[1]) for c in hole])
        probe = hole_poly.representative_point()
        best: int | None = None
        for i, shell in enumerate(shells):
            if shell.contains(probe) and (best is None or shell.area < shells[best].area):
                best = i
        if best is None:
            for i, shell in enumerate(shells):
                if shell.intersects(hole_poly) and (best is None or shell.area < shells[best].area):
                    best = i
        if best is None:
            # A counter-clockwise ring outside every exterior: treat it as an exterior itself.
            exteriors.append(hole)
            shells.append(hole_poly)
            assigned.append([])
        else:
            assigned[best].append(hole)

    polygons = [Polygon(ext, assigned[i]) for i, ext in enumerate(exteriors)]
    if len(polygons) == 1:
        return polygons[0]
    return MultiPolygon(polygons)


def shape_to_geometry(shape: Any) -> tuple[BaseGeometry | None, str | None]:
    """Convert a pyshp shape to Shapely. Returns ``(geometry, unsupported_reason)``."""
    shape_type = int(getattr(shape, "shapeType", _NULL) or _NULL)
    if shape_type == _NULL or not shape.points:
        return None, None
    if shape_type == _MULTIPATCH:
        return None, "Shapefile MultiPatch (3D surface) geometry is not supported"
    has_z = shape_type in _Z_TYPES
    coords = _coords(shape, has_z)
    if shape_type in _POINT_TYPES:
        return Point(coords[0]), None
    if shape_type in _MULTIPOINT_TYPES:
        return MultiPoint(coords), None
    if shape_type in _LINE_TYPES:
        lines = [LineString(part) for part in _split_parts(shape, coords) if len(part) >= 2]
        if not lines:
            return None, None
        return (lines[0] if len(lines) == 1 else MultiLineString(lines)), None
    if shape_type in _POLYGON_TYPES:
        return build_polygon(_split_parts(shape, coords)), None
    return None, f"Shapefile shape type {shape_type} is not supported"


def _feature_name(props: dict[str, Any]) -> str | None:
    lowered = {k.lower(): v for k, v in props.items()}
    for key in _NAME_KEYS:
        value = lowered.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _missing(shp_path: Path, part: str) -> IngestError:
    return IngestError(
        "missing_component",
        f"'{shp_path.name}' has no matching {part} file.",
        details={"layer": shp_path.stem, "missing": part},
    )


def read_shapefile(shp_path: Path, crs_override: CRS | None) -> RawLayer:
    """Read one shapefile layer (``.shp`` + ``.shx`` + ``.dbf``, optional ``.prj``/``.cpg``).

    Raises:
        IngestError: ``missing_component`` when ``.shx`` or ``.dbf`` is absent,
            ``corrupt_file`` when the header cannot be read.
    """
    shx_path = _sibling(shp_path, ".shx")
    if shx_path is None:
        raise _missing(shp_path, ".shx")
    dbf_path = _sibling(shp_path, ".dbf")
    if dbf_path is None:
        raise _missing(shp_path, ".dbf")

    layer_name = shp_path.stem
    issues: list[Issue] = []
    prj_path = _sibling(shp_path, ".prj")

    crs: CRS | None
    crs_source: str | None
    if crs_override is not None:
        crs, crs_source = crs_override, "override"
        issues.append(
            Issue(
                code="crs_override",
                message=f"Layer '{layer_name}': CRS override applied"
                + (" (the .prj was ignored)." if prj_path is not None else "."),
                severity=Severity.INFO,
            )
        )
    elif prj_path is not None:
        crs, problem = read_prj(prj_path)
        crs_source = "prj" if crs is not None else None
        if problem is not None:
            issues.append(
                Issue(
                    code="invalid_prj",
                    message=f"Layer '{layer_name}': {problem}. The CRS is unknown, so features are not measured.",
                    severity=Severity.WARNING,
                )
            )
    else:
        crs, crs_source = None, None
        issues.append(
            Issue(
                code="missing_prj",
                message=f"Layer '{layer_name}' has no .prj file. The CRS is unknown, so features are not measured "
                "(guessing a CRS is unsafe; supply a CRS override to measure).",
                severity=Severity.WARNING,
            )
        )

    encoding, cpg_note = _encoding_from_cpg(_sibling(shp_path, ".cpg"))
    if cpg_note:
        issues.append(Issue(code="unknown_encoding", message=f"Layer '{layer_name}': {cpg_note}", severity=Severity.INFO))

    features: list[RawFeature] = []
    with ExitStack() as stack:
        try:
            shp_fh = stack.enter_context(open(shp_path, "rb"))
            shx_fh = stack.enter_context(open(shx_path, "rb"))
            dbf_fh = stack.enter_context(open(dbf_path, "rb"))
            reader = pyshp.Reader(shp=shp_fh, shx=shx_fh, dbf=dbf_fh, encoding=encoding, encodingErrors="replace")
            stack.callback(reader.close)
            count = len(reader)
        except Exception as exc:  # pyshp raises ShapefileException, struct.error, ValueError, ... on bad headers
            # pyshp's messages can include the staging path on the server: only the type goes out.
            raise IngestError(
                "corrupt_file",
                f"'{shp_path.name}' is not a readable shapefile.",
                details={"layer": layer_name, "reason": type(exc).__name__},
            ) from exc

        bad_records = 0
        for i in range(count):
            props: dict[str, Any] = {}
            try:
                props = dict(reader.record(i).as_dict())
            except Exception as exc:  # one damaged DBF row must not abort the layer
                bad_records += 1
                issues.append(
                    Issue(
                        code="corrupt_record",
                        message=f"Layer '{layer_name}' record {i}: attributes unreadable ({exc}).",
                        severity=Severity.WARNING,
                    )
                )
            geometry: BaseGeometry | None = None
            reason: str | None = None
            try:
                geometry, reason = shape_to_geometry(reader.shape(i))
            except Exception as exc:  # one damaged shape must not abort the layer
                bad_records += 1
                reason = f"Shape could not be read: {exc}"
                issues.append(
                    Issue(
                        code="corrupt_record",
                        message=f"Layer '{layer_name}' shape {i}: {exc}.",
                        severity=Severity.WARNING,
                    )
                )
            charge_vertices(geometry)
            features.append(
                RawFeature(
                    fid=str(i),
                    name=_feature_name(props),
                    geometry=geometry,
                    properties=props,
                    unsupported_reason=reason,
                )
            )
        if count and bad_records >= count * 2:
            raise IngestError(
                "corrupt_file",
                f"'{shp_path.name}' could not be read: every record is damaged.",
                details={"layer": layer_name},
            )

    return RawLayer(name=layer_name, crs=crs, crs_source=crs_source, features=features, issues=issues)
