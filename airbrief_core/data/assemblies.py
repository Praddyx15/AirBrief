"""Outdoor assembly venues of a data pack (``assemblies.geojson``).

Stadiums, sports centres with a known capacity, theme parks and marketplaces: places where large
outdoor assemblies of people are expected (JARUS SORA 2.5 Main Body §4.8.3 (b)(iii), Step #8).
Each feature is a Point or a (Multi)Polygon with the properties ``name``, ``kind``
(``stadium`` · ``sports_centre`` · ``theme_park`` · ``marketplace``) and ``capacity`` (people,
``null`` when unknown). Real packs take them from OpenStreetMap (``seed/datapack.py``).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry.base import BaseGeometry

from .vectors import _iter_features, _load_geojson

log = logging.getLogger(__name__)

ASSEMBLY_KINDS: tuple[str, ...] = ("stadium", "sports_centre", "theme_park", "marketplace")

_NUMBER = re.compile(r"\d[\d,.\s]*")


def parse_capacity(value: Any) -> int | None:
    """People from an OSM ``capacity`` tag (``"40000"``, ``"40,000"``, ``"40 000"``, ``"approx. 5000"``).

    The first number in the text counts; ``;``-separated lists take the largest. Values that are
    not a plausible head count (≤ 0 or above 10 million) give None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
        return int(round(number)) if math.isfinite(number) and 0 < number <= 10_000_000 else None
    best: int | None = None
    for part in str(value).split(";"):
        match = _NUMBER.search(part)
        if not match or part[: match.start()].rstrip().endswith("-"):
            continue  # no number, or a negative one
        digits = re.sub(r"[,\s]", "", match.group(0))
        if digits.count(".") == 1 and len(digits.split(".")[1]) == 3:
            digits = digits.replace(".", "")  # "40.000": dot as a thousands separator
        try:
            number = float(digits)
        except ValueError:
            continue
        if math.isfinite(number) and 0 < number <= 10_000_000:
            people = int(round(number))
            best = people if best is None else max(best, people)
    return best


@dataclass
class AssemblyFeature:
    """An assembly venue: point or polygon geometry (WGS84) with its properties."""

    id: str
    geometry: BaseGeometry
    name: str
    kind: str | None = None
    capacity: int | None = None
    props: dict[str, Any] = field(default_factory=dict)


def load_assemblies(source: Path | str | dict[str, Any]) -> list[AssemblyFeature]:
    """Read assembly venues from a GeoJSON file or dict; non-point, non-polygon features are skipped."""
    out: list[AssemblyFeature] = []
    for i, (fid, geom, props) in enumerate(_iter_features(_load_geojson(source))):
        kind = geom.geom_type
        if kind not in ("Point", "MultiPoint", "Polygon", "MultiPolygon"):
            log.warning("assemblies feature %d is %s, not a point or polygon; skipped", i, kind)
            continue
        if not geom.is_valid:
            geom = shapely.make_valid(geom)
        name = str(props.get("name") or f"Assembly {i + 1}")[:120]
        raw_kind = props.get("kind")
        out.append(
            AssemblyFeature(
                id=fid,
                geometry=geom,
                name=name,
                kind=str(raw_kind) if raw_kind else None,
                capacity=parse_capacity(props.get("capacity")),
                props=props,
            )
        )
    return out


__all__ = ["ASSEMBLY_KINDS", "AssemblyFeature", "load_assemblies", "parse_capacity"]
