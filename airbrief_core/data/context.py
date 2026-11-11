"""Context layers and place search over a data pack, before any analysis runs.

Features are selected by their bounding box or geometry intersecting the view, and returned
whole (never clipped), so outlines never gain false edges at the view border. The ``rings`` are
the same polygons the airspace module draws, but context layers are reference data, not an
assessment: zones are only evaluated against an operation in the airspace module.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import shapely
from shapely.geometry import box, mapping, shape

from ..errors import AnalysisError
from ..models.common import BBox

if TYPE_CHECKING:
    from .aerodromes import Aerodrome
    from .datapack import DataPack

__all__ = [
    "CONTEXT_FEATURE_LIMIT",
    "CONTEXT_LAYERS",
    "CONTEXT_VERTEX_LIMIT",
    "MAX_CONTEXT_SPAN_DEG",
    "SEARCH_LIMIT_MAX",
    "PackContext",
    "PlaceMatch",
    "build_context",
    "check_context_bbox",
    "search_places",
]

ContextLayer = Literal["aerodromes", "rings", "zones", "obstacles", "water", "coverage"]
CONTEXT_LAYERS: tuple[ContextLayer, ...] = ("aerodromes", "rings", "zones", "obstacles", "water", "coverage")
"""Context layer names, in drawing order from the top of the legend."""
MAX_CONTEXT_SPAN_DEG = 2.0
"""Largest view (degrees of longitude and of latitude) one context request may cover."""
CONTEXT_FEATURE_LIMIT = 5000
"""Most features returned per layer."""
CONTEXT_VERTEX_LIMIT = 100_000
"""Most vertices returned per layer (about 2.5 MB of JSON): whole lakes, rivers and rings can be
large, so a layer is also cut once its features reach this many vertices (at least one feature is
always kept)."""
SEARCH_LIMIT_MAX = 25
"""Most place matches one search returns."""

AERODROME_TYPE_LABEL: dict[str, str] = {
    "large_airport": "Large airport",
    "medium_airport": "Medium airport",
    "small_airport": "Small airport",
    "heliport": "Heliport",
    "seaplane_base": "Seaplane base",
    "balloonport": "Balloon port",
}
_TYPE_ORDER: dict[str, int] = {kind: i for i, kind in enumerate(AERODROME_TYPE_LABEL)}
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class PlaceMatch:
    """One search result: where it is and how to label it."""

    label: str
    kind: Literal["aerodrome", "coordinates"]
    lon: float
    lat: float
    ident: str | None = None
    detail: str = ""
    #: match quality (0 best, see :func:`_rank`), so results of several packs can be merged
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _rank(aero: Aerodrome, query: str, tokens: Sequence[str]) -> int | None:
    """How well ``aero`` matches (0 best), or None.

    0 exact ident / IATA · 1 ident / IATA prefix · 2 name prefix · 3 a name word starts with the
    query · 4 municipality prefix or word prefix · 5 query inside the name · 6 inside the
    municipality · 7 every query word starts a word of the ident, IATA code, name or municipality.
    """
    ident = aero.ident.lower()
    iata = (aero.iata or "").lower()
    name = aero.name.lower()
    town = (aero.municipality or "").lower()
    if query == ident or (iata and query == iata):
        return 0
    if ident.startswith(query) or (iata and iata.startswith(query)):
        return 1
    if name.startswith(query):
        return 2
    if any(w.startswith(query) for w in _words(name)):
        return 3
    if town and (town.startswith(query) or any(w.startswith(query) for w in _words(town))):
        return 4
    if query in name:
        return 5
    if town and query in town:
        return 6
    if len(tokens) > 1:
        words = _words(f"{ident} {iata} {name} {town}")
        if all(any(w.startswith(t) for w in words) for t in tokens):
            return 7
    return None


def aerodrome_detail(aero: Aerodrome) -> str:
    """``"Large airport · Bengaluru · IATA BLR"``."""
    parts = [AERODROME_TYPE_LABEL.get(aero.type, aero.type.replace("_", " ").capitalize())]
    if aero.municipality:
        parts.append(aero.municipality)
    if aero.iata:
        parts.append(f"IATA {aero.iata}")
    return " · ".join(parts)


def search_places(pack: DataPack, query: str, limit: int = 10) -> list[PlaceMatch]:
    """Aerodromes of ``pack`` matching ``query`` (case-insensitive), best first.

    Ties are broken by aerodrome size (large airports first), then by name. At most ``limit``
    (1 to :data:`SEARCH_LIMIT_MAX`) results.
    """
    q = " ".join(query.lower().split())
    if not q:
        return []
    limit = max(1, min(int(limit), SEARCH_LIMIT_MAX))
    tokens = _words(q)
    ranked: list[tuple[int, int, str, Aerodrome]] = []
    for aero in pack.aerodromes():
        rank = _rank(aero, q, tokens)
        if rank is not None:
            ranked.append((rank, _TYPE_ORDER.get(aero.type, len(_TYPE_ORDER)), aero.name.lower(), aero))
    ranked.sort(key=lambda t: (t[0], t[1], t[2]))
    return [
        PlaceMatch(
            label=f"{a.ident} · {a.name}" if a.ident else a.name,
            kind="aerodrome",
            lon=a.lon,
            lat=a.lat,
            ident=a.ident or None,
            detail=aerodrome_detail(a),
            rank=rank,
        )
        for rank, _, _, a in ranked[:limit]
    ]


@dataclass
class PackContext:
    """Context layers for one view: FeatureCollections, whether each was cut at the limit, and
    how many features matched before the cut."""

    layers: dict[str, dict[str, Any]] = field(default_factory=dict)
    truncated: dict[str, bool] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


def check_context_bbox(bbox: BBox) -> None:
    """Refuse a view larger than :data:`MAX_CONTEXT_SPAN_DEG` (``area_too_large``) or one that
    is not a valid WGS84 box (``invalid_params``)."""
    values = bbox.as_list()
    if not all(math.isfinite(v) for v in values):
        raise AnalysisError("invalid_params", "The bbox values must be numbers.")
    if not (-180 <= bbox.min_lon < bbox.max_lon <= 180 and -90 <= bbox.min_lat < bbox.max_lat <= 90):
        raise AnalysisError(
            "invalid_params",
            "bbox must be min_lon,min_lat,max_lon,max_lat in degrees, with min < max.",
            details={"bbox": values},
        )
    width = bbox.max_lon - bbox.min_lon
    height = bbox.max_lat - bbox.min_lat
    # A tolerance for decimal text: 65.030927 − 63.030927 is 2.000000000000007 in binary floats.
    limit = MAX_CONTEXT_SPAN_DEG + 1e-9
    if width > limit or height > limit:
        raise AnalysisError(
            "area_too_large",
            f"The view is {width:.2f}° × {height:.2f}°; context layers cover at most "
            f"{MAX_CONTEXT_SPAN_DEG:g}° × {MAX_CONTEXT_SPAN_DEG:g}°. Zoom in.",
            details={"width_deg": round(width, 6), "height_deg": round(height, 6), "limit_deg": MAX_CONTEXT_SPAN_DEG},
        )


def _collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _vertex_count(geometry: Any) -> int:
    """Positions in a GeoJSON geometry dict (0 for null)."""
    if not isinstance(geometry, dict):
        return 0
    if geometry.get("type") == "GeometryCollection":
        return sum(_vertex_count(g) for g in geometry.get("geometries") or [])
    count = 0
    stack: list[Any] = [geometry.get("coordinates")]
    while stack:
        item = stack.pop()
        if isinstance(item, list | tuple):
            if item and isinstance(item[0], int | float):
                count += 1
            else:
                stack.extend(item)
    return count


def _cap(name: str, features: list[dict[str, Any]], limit: int, out: PackContext) -> None:
    """Keep the first ``limit`` features, and stop earlier at :data:`CONTEXT_VERTEX_LIMIT` vertices."""
    kept: list[dict[str, Any]] = []
    vertices = 0
    for feature in features[:limit]:
        n = _vertex_count(feature.get("geometry"))
        if kept and vertices + n > CONTEXT_VERTEX_LIMIT:
            break
        kept.append(feature)
        vertices += n
    out.counts[name] = len(features)
    out.truncated[name] = len(kept) < len(features)
    out.layers[name] = _collection(kept)


def _aerodrome_features(pack: DataPack, view: BBox) -> list[dict[str, Any]]:
    inside = [
        a
        for a in pack.aerodromes()
        if view.min_lon <= a.lon <= view.max_lon and view.min_lat <= a.lat <= view.max_lat
    ]
    inside.sort(key=lambda a: (_TYPE_ORDER.get(a.type, len(_TYPE_ORDER)), a.name.lower()))
    return [
        {
            "type": "Feature",
            "id": a.ident,
            "geometry": {"type": "Point", "coordinates": [round(a.lon, 8), round(a.lat, 8)]},
            "properties": {
                "ident": a.ident,
                "name": a.name,
                "type": a.type,
                "type_label": AERODROME_TYPE_LABEL.get(a.type, a.type),
                "iata": a.iata,
                "municipality": a.municipality,
                "elevation_m": a.elevation_m,
            },
        }
        for a in inside
    ]


def _ring_features(pack: DataPack, view: BBox, profile: str = "dgca_2021") -> list[dict[str, Any]]:
    from ..analyses.zones import ring_polygons
    from ..models.presets import ZONE_RULE_PROFILES

    area = box(*view.as_list())
    rings = ring_polygons(pack, ZONE_RULE_PROFILES[profile], view)["features"]
    return [f for f in rings if shape(f["geometry"]).intersects(area)]


def _zone_features(pack: DataPack, view: BBox) -> list[dict[str, Any]]:
    area = box(*view.as_list())
    out: list[dict[str, Any]] = []
    for i, zone in enumerate(pack.zones()):
        geom = shape(zone.geometry)
        if not geom.intersects(area):
            continue
        out.append(
            {
                "type": "Feature",
                "id": f"zone-{i + 1}",
                "geometry": zone.geometry,
                "properties": {
                    "source": "datapack",
                    "name": zone.name,
                    "zone": zone.zone,
                    "ceiling_agl_m": zone.ceiling_agl_m,
                    "note": zone.note,
                },
            }
        )
    return out


def _height(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return -math.inf
    return number if math.isfinite(number) else -math.inf


def _obstacle_features(pack: DataPack, view: BBox) -> list[dict[str, Any]]:
    # Tallest first, so a view cut at the limit keeps the obstacles that matter most.
    found = sorted(pack.obstacles().in_bbox(view.as_list()), key=lambda f: -_height(f.get("height_m")))
    return [f.to_feature() for f in found]


def _water_features(pack: DataPack, view: BBox) -> list[dict[str, Any]]:
    # Largest first, so a view cut at the limit keeps the big lakes and rivers.
    found = sorted(pack.water().intersecting(box(*view.as_list())), key=lambda f: -f.geometry.area)
    return [f.to_feature() for f in found]


def _coverage_features(pack: DataPack, view: BBox) -> list[dict[str, Any]]:
    cover = pack.bbox
    if not cover.intersects(view):
        return []
    return [
        {
            "type": "Feature",
            "id": f"coverage-{pack.name}",
            "geometry": mapping(shapely.box(*cover.as_list())),
            "properties": {
                "name": pack.name,
                "synthetic": pack.synthetic,
                "layers": ", ".join(pack.layers),
                "version": pack.manifest.version,
            },
        }
    ]


_BUILDERS = {
    "aerodromes": _aerodrome_features,
    "rings": _ring_features,
    "zones": _zone_features,
    "obstacles": _obstacle_features,
    "water": _water_features,
    "coverage": _coverage_features,
}


def build_context(
    pack: DataPack,
    bbox: BBox,
    layers: Sequence[str] | None = None,
    limit: int | None = None,
    profile: str = "dgca_2021",
) -> PackContext:
    """Context FeatureCollections of ``pack`` for the view ``bbox``.

    ``rings`` are drawn with the airspace rule set ``profile`` (a key of
    :data:`~airbrief_core.models.presets.ZONE_RULE_PROFILES`, as the airspace check uses).

    ``layers`` defaults to every name in :data:`CONTEXT_LAYERS`; ``limit`` (1 to
    :data:`CONTEXT_FEATURE_LIMIT`, the default) caps each layer, which is then also cut at
    :data:`CONTEXT_VERTEX_LIMIT` vertices. An unknown layer or a bad limit is ``invalid_params``;
    the bbox is checked by :func:`check_context_bbox`.
    """
    check_context_bbox(bbox)
    wanted = list(dict.fromkeys(layers)) if layers is not None else list(CONTEXT_LAYERS)
    unknown = [name for name in wanted if name not in _BUILDERS]
    if unknown:
        raise AnalysisError(
            "invalid_params",
            f"Unknown context layer(s): {', '.join(unknown)}. Choose from {', '.join(CONTEXT_LAYERS)}.",
            details={"unknown": unknown, "allowed": list(CONTEXT_LAYERS)},
        )
    cap = CONTEXT_FEATURE_LIMIT if limit is None else int(limit)
    if not 1 <= cap <= CONTEXT_FEATURE_LIMIT:
        raise AnalysisError(
            "invalid_params",
            f"limit must be between 1 and {CONTEXT_FEATURE_LIMIT}.",
            details={"limit": cap},
        )
    from ..models.presets import ZONE_RULE_PROFILES

    if profile not in ZONE_RULE_PROFILES:
        raise AnalysisError(
            "invalid_params",
            f"Unknown zone profile '{profile}'. Choose from {', '.join(ZONE_RULE_PROFILES)}.",
            details={"profile": profile, "allowed": list(ZONE_RULE_PROFILES)},
        )
    out = PackContext()
    for name in wanted:
        features = _ring_features(pack, bbox, profile) if name == "rings" else _BUILDERS[name](pack, bbox)
        _cap(name, features, cap, out)
    return out
