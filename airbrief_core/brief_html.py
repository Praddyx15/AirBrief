"""Self-contained HTML rendering of an operation evidence brief (``brief.html`` of the bundle).

The page loads nothing: no scripts, no fonts, no images from elsewhere (a Content-Security-Policy
``default-src 'none'`` makes sure), so it opens offline and prints the same everywhere. The map is
an inline SVG of the key evidence layers, projected equirectangularly around the mid-latitude
(``x = Δlon·cos φ₀``, ``y = Δlat``) — accurate enough for a site-scale overview, never for
navigation. Print CSS lays it out on A4 portrait like the web app's sheet: the operation across
the page, then the map and the section cards in two balanced columns, every section without an
analysis in one "Not assessed" list, tables cut to :data:`SHEET_TABLE_ROWS` rows, the provenance
last (a typical brief takes one or two pages; the web app scales its text to fit one).
"Evidence, not authorisation" is in the header and in the page margin of every printed page (CSS
``@page`` margin boxes), and a brief computed with a synthetic data pack carries a watermark on
every page.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from html import escape
from typing import Any
from urllib.parse import urlsplit

from .brief import fmt_coord, fmt_length, fmt_utc
from .models.brief import Brief, BriefPart, BriefSection, BriefSource, BriefTable

__all__ = ["MapLayer", "brief_map_layers", "render_brief_html", "render_map_svg"]

MAX_MAP_FEATURES = 2000
"""Features drawn per map layer (the GeoJSON and KML evidence hold every feature)."""

SHEET_TABLE_ROWS = 5
"""Rows of each table on the page, as on the web app's sheet (``brief.json`` keeps 8, the bundle's
CSV tables every row)."""

SHEET_NOTES = 2
"""Notes shown per part before "+N more"."""

_SYNTHETIC_CODES = frozenset({"synthetic_data", "synthetic_datapack"})
"""Notes and cross-checks that only say the data are synthetic: the banner and watermark say it once."""

_INK = "#0f172a"
_MUTED = "#475569"
_ACCENT = "#1e40af"
_OK = "#166534"
_WARN = "#92400e"
_DANGER = "#b91c1c"
_ZONE = {"green": "#15803d", "yellow": "#a16207", "red": "#b91c1c"}
_BAND = {"ok": _OK, "near": _WARN, "over": _DANGER}
_TONE = {"ok": _OK, "warn": _WARN, "danger": _DANGER, "info": _ACCENT, "neutral": _MUTED}


@dataclass
class MapLayer:
    """One drawn layer: its features (GeoJSON dicts) and how they look."""

    key: str
    label: str
    color: str
    features: list[dict[str, Any]] = field(default_factory=list)
    fill: bool = True
    dashed: bool = False
    width: float = 1.5


# (result layer, legend label, colour, fill, dashed, stroke width) per kind, in drawing order.
_KIND_LAYERS: dict[str, tuple[tuple[str, str, str, bool, bool, float], ...]] = {
    "ground_risk": (
        ("footprint", "Ground-risk footprint (FG + CV + GRB)", _MUTED, False, True, 1.25),
        ("contingency_volume", "Contingency volume", _WARN, False, True, 1.25),
        ("flight_geography", "Flight geography", _ACCENT, True, False, 1.25),
        ("assemblies", "Outdoor assemblies within 1 km", _DANGER, True, False, 1.25),
    ),
    "survey": (("blocks", "Survey battery blocks", "#0e7490", True, False, 1.0), ("input", "Survey area", _ACCENT, False, False, 2.0)),
    "evtol_siting": (("input", "Siting study area", _MUTED, False, True, 1.5), ("candidates", "Vertiport candidates", _ACCENT, True, False, 1.5)),
    "evtol_corridor": (
        ("corridor", "eVTOL corridor", _MUTED, False, True, 1.25),
        ("route", "Corridor centreline", _ACCENT, False, False, 2.5),
        ("endpoints", "Corridor origin and destination", _ACCENT, True, False, 1.5),
    ),
    "show": (("show_box", "Show box", _ACCENT, False, False, 2.0), ("audience_buffer", "Audience buffer", _DANGER, True, False, 1.25)),
    "flight_audit": (
        ("training_box", "Training area", _ACCENT, False, True, 1.5),
        ("path", "Flight path", _OK, False, False, 2.0),
        ("home", "Take-off point", _OK, True, False, 1.5),
    ),
    "environment": (("input", "Environment area", _MUTED, False, True, 1.25), ("emi", "Power lines and masts", _WARN, False, False, 2.0)),
    "line_of_sight": (
        ("input", "Line-of-sight area", _MUTED, False, True, 1.25),
        ("radio_paths", "Radio paths", "#7c3aed", False, True, 1.25),
        ("observers", "Pilot, observers and ground station", "#7c3aed", True, False, 1.5),
    ),
    "airspace": (("input", "Airspace checked area", _ACCENT, False, False, 2.0), ("obstacles", "Obstacles", _MUTED, True, False, 1.25)),
}


def brief_map_layers(sources: Sequence[BriefSource], pilot_position: Sequence[float] | None = None) -> list[MapLayer]:
    """The key evidence layers of the analyses for the brief map, plus the pilot position.

    Large context layers (flyability grids, density cells, aerodrome rings, the adjacent area)
    are left out so the map shows the operation, not the region around it.
    """
    layers: list[MapLayer] = []
    for source in sources:
        kind = str(source.envelope.get("kind"))
        result = source.envelope.get("result") or {}
        result_layers = result.get("layers") if isinstance(result, dict) else None
        if not isinstance(result_layers, dict):
            continue
        chosen = _KIND_LAYERS.get(kind, ())
        if result.get("batch") == "per_feature":
            chosen = (("features", "Features (one result each)", _ACCENT, True, False, 1.25),)
        for key, label, color, fill, dashed, width in chosen:
            collection = result_layers.get(key)
            features = collection.get("features") if isinstance(collection, dict) else None
            if not isinstance(features, list) or not features:
                continue
            kept = [f for f in features[:MAX_MAP_FEATURES] if isinstance(f, dict) and isinstance(f.get("geometry"), dict)]
            if kept:
                layers.append(MapLayer(key=f"{kind}:{key}", label=label, color=color, features=kept, fill=fill, dashed=dashed, width=width))
    if pilot_position is not None and len(pilot_position) >= 2:
        layers.append(MapLayer(
            key="operation:pilot",
            label="Pilot / take-off position",
            color=_INK,
            features=[{"type": "Feature", "geometry": {"type": "Point", "coordinates": list(pilot_position[:2])}, "properties": {}}],
        ))
    # Areas first, then lines, then points, keeping the order above inside each group.
    rank = {"Polygon": 0, "MultiPolygon": 0, "LineString": 1, "MultiLineString": 1, "Point": 2, "MultiPoint": 2}
    return sorted(layers, key=lambda layer: min((rank.get(str(f["geometry"].get("type")), 1) for f in layer.features), default=1))


def _positions(geometry: Mapping[str, Any]) -> Iterable[Sequence[float]]:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Point" and isinstance(coords, list):
        yield coords
    elif gtype in ("MultiPoint", "LineString") and isinstance(coords, list):
        yield from coords
    elif gtype in ("MultiLineString", "Polygon") and isinstance(coords, list):
        for part in coords:
            yield from part
    elif gtype == "MultiPolygon" and isinstance(coords, list):
        for poly in coords:
            for ring in poly:
                yield from ring
    elif gtype == "GeometryCollection":
        for g in geometry.get("geometries") or []:
            if isinstance(g, dict):
                yield from _positions(g)


class _Projection:
    def __init__(self, layers: Sequence[MapLayer], width: int, max_height: int, min_height: int = 180) -> None:
        lons: list[float] = []
        lats: list[float] = []
        for layer in layers:
            for f in layer.features:
                for p in _positions(f["geometry"]):
                    if len(p) >= 2 and all(isinstance(v, int | float) and math.isfinite(v) for v in p[:2]):
                        lons.append(float(p[0]))
                        lats.append(float(p[1]))
        self.ok = bool(lons)
        if not self.ok:
            self.width, self.height, self.metres_per_px = width, 0, 1.0
            return
        min_lon, max_lon, min_lat, max_lat = min(lons), max(lons), min(lats), max(lats)
        min_span = 0.0045  # ~500 m of room around a point or a north-south line
        if max_lon - min_lon < min_span:
            c = (min_lon + max_lon) / 2
            min_lon, max_lon = c - min_span / 2, c + min_span / 2
        if max_lat - min_lat < min_span:
            c = (min_lat + max_lat) / 2
            min_lat, max_lat = c - min_span / 2, c + min_span / 2
        self.kx = max(0.05, math.cos(math.radians((min_lat + max_lat) / 2)))
        data_w = (max_lon - min_lon) * self.kx
        data_h = max_lat - min_lat
        margin = 16
        self.width = width
        self.height = int(round(min(max_height, max(min_height, width * data_h / data_w))))
        self.scale = min((width - 2 * margin) / data_w, (self.height - 2 * margin) / data_h)
        self.off_x = (width - data_w * self.scale) / 2
        self.off_y = (self.height - data_h * self.scale) / 2
        self.min_lon, self.min_lat = min_lon, min_lat
        self.metres_per_px = 111_320.0 / self.scale

    def xy(self, p: Sequence[float]) -> tuple[float, float]:
        x = self.off_x + (float(p[0]) - self.min_lon) * self.kx * self.scale
        y = self.height - (self.off_y + (float(p[1]) - self.min_lat) * self.scale)
        return x, y


def _path(coords: Sequence[Sequence[float]], proj: _Projection, close: bool) -> str:
    out: list[str] = []
    last: tuple[float, float] | None = None
    for i, p in enumerate(coords):
        if len(p) < 2:
            continue
        x, y = proj.xy(p)
        if last is not None and i != len(coords) - 1 and abs(x - last[0]) < 0.6 and abs(y - last[1]) < 0.6:
            continue
        out.append(f"{'L' if last else 'M'}{x:.1f} {y:.1f}")
        last = (x, y)
    return "".join(out) + ("Z" if close and out else "")


def _shapes(geometry: Mapping[str, Any], proj: _Projection, areas: list[str], lines: list[str], points: list[tuple[float, float]]) -> None:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Point" and isinstance(coords, list) and len(coords) >= 2:
        points.append(proj.xy(coords))
    elif gtype == "MultiPoint" and isinstance(coords, list):
        points.extend(proj.xy(c) for c in coords if len(c) >= 2)
    elif gtype == "LineString" and isinstance(coords, list):
        lines.append(_path(coords, proj, False))
    elif gtype == "MultiLineString" and isinstance(coords, list):
        lines.extend(_path(part, proj, False) for part in coords)
    elif gtype == "Polygon" and isinstance(coords, list):
        areas.append("".join(_path(ring, proj, True) for ring in coords))
    elif gtype == "MultiPolygon" and isinstance(coords, list):
        areas.append("".join(_path(ring, proj, True) for poly in coords for ring in poly))
    elif gtype == "GeometryCollection":
        for g in geometry.get("geometries") or []:
            if isinstance(g, dict):
                _shapes(g, proj, areas, lines, points)


def _feature_color(layer: MapLayer, props: Mapping[str, Any]) -> str:
    if props.get("conflict") is True:
        return _DANGER
    zone = props.get("zone")
    if isinstance(zone, str) and zone in _ZONE and layer.key.endswith(":zones"):
        return _ZONE[zone]
    band = props.get("agl_band")
    if isinstance(band, str) and band in _BAND:
        return _BAND[band]
    return layer.color


def _nice_length(target: float) -> float:
    power = 10 ** math.floor(math.log10(target))
    n = target / power
    return (5 if n >= 5 else 2 if n >= 2 else 1) * power


def render_map_svg(layers: Sequence[MapLayer], title: str, width: int = 480, max_height: int = 330, min_height: int = 200) -> str:
    """The layers as an accessible inline SVG (``role="img"`` with a title and description).

    The default view box suits one column of the A4 page (about 93 mm wide): strokes, points and
    the scale-bar text keep a readable size there.
    """
    proj = _Projection(layers, width, max_height, min_height)
    if not proj.ok:
        return '<p class="muted">No geometry with known coordinates to draw.</p>'
    body: list[str] = []
    for layer in layers:
        dash = ' stroke-dasharray="6 4"' if layer.dashed else ""
        group: list[str] = []
        for f in layer.features:
            props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
            color = _feature_color(layer, props or {})
            areas: list[str] = []
            lines: list[str] = []
            points: list[tuple[float, float]] = []
            _shapes(f["geometry"], proj, areas, lines, points)
            for d in areas:
                fill = f'fill="{color}" fill-opacity="0.14"' if layer.fill else 'fill="none"'
                group.append(f'<path d="{d}" fill-rule="evenodd" {fill} stroke="{color}" stroke-width="{layer.width}"{dash}/>')
            for d in lines:
                group.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{layer.width}" stroke-linecap="round"'
                             f' stroke-linejoin="round"{dash}/>')
            for x, y in points:
                group.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}" stroke="#ffffff" stroke-width="1"/>')
        body.append(f'<g aria-hidden="true">{"".join(group)}</g>')
    bar_m = _nice_length(proj.metres_per_px * width * 0.2)
    bar_px = bar_m / proj.metres_per_px
    count = sum(len(layer.features) for layer in layers)
    desc = f"{count} features in {len(layers)} layers: " + ", ".join(f"{layer.label} ({len(layer.features)})" for layer in layers) + ". North is up."
    w, h = proj.width, proj.height
    return (
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-labelledby="map-title map-desc" class="map" preserveAspectRatio="xMidYMid meet">'
        f'<title id="map-title">{escape(title)}</title><desc id="map-desc">{escape(desc)}</desc>'
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff" stroke="#94a3b8"/>'
        + "".join(body)
        + f'<g aria-hidden="true" transform="translate({w - 22} 26)"><path d="M0,-12 L5,4 L0,0 L-5,4 Z" fill="{_INK}"/>'
        f'<text x="0" y="17" text-anchor="middle" font-size="11" fill="{_MUTED}">N</text></g>'
        f'<g aria-hidden="true" transform="translate(14 {h - 14})"><path d="M0,0 H{bar_px:.1f} M0,-4 V4 M{bar_px:.1f},-4 V4" fill="none" stroke="{_INK}" stroke-width="1.25"/>'
        f'<text x="{bar_px + 5:.1f}" y="4" font-size="11" fill="{_MUTED}">{escape(fmt_length(bar_m))}</text></g>'
        "</svg>"
    )


_CSS = """
@page { size: A4 portrait; margin: 13mm 11mm 14mm;
  @top-center { content: "Evidence, not authorisation"; font: 600 8pt system-ui, sans-serif; color: #334155; }
  @bottom-left { content: "AirBrief operation evidence brief"; font: 7pt system-ui, sans-serif; color: #475569; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font: 7pt system-ui, sans-serif; color: #475569; } }
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #ffffff; color: #0f172a; font: 8.5pt/1.35 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  font-variant-numeric: tabular-nums; }
main { max-width: 190mm; margin: 0 auto; padding: 6mm 4mm; }
h1 { font-size: 14pt; margin: 0.5mm 0 0; line-height: 1.2; }
h2 { font-size: 9.5pt; margin: 0; display: flex; gap: 2mm; align-items: baseline; flex-wrap: wrap; }
h3 { font-size: 8.3pt; margin: 1.2mm 0 0.3mm; }
p { margin: 0.6mm 0; }
ul { margin: 0.5mm 0; padding-left: 4mm; }
.notice { border: 1.5px solid #0f172a; border-radius: 3px; padding: 1.2mm 2.5mm; font-weight: 600; margin: 0 0 2mm; }
.meta { color: #475569; font-size: 7.3pt; display: flex; flex-wrap: wrap; gap: 0 4mm; margin: 0.5mm 0 2mm; }
.watermark { position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none;
  z-index: 10; color: rgba(185, 28, 28, 0.14); font-size: 40pt; font-weight: 800; transform: rotate(-28deg); text-align: center; }
.synthetic { border: 2px solid #b91c1c; color: #b91c1c; font-weight: 700; padding: 1.2mm 2.5mm; border-radius: 3px; margin: 0 0 2mm; }
.full { margin: 0 0 2.5mm; }
/* The map and the section cards flow in two balanced columns (one on a narrow screen). */
.cols { column-count: 2; column-gap: 3mm; }
.cols > * { break-inside: avoid; page-break-inside: avoid; margin: 0 0 2.5mm; }
.card { display: block; border: 1px solid #94a3b8; border-radius: 4px; padding: 1.6mm 2.2mm; break-inside: avoid; page-break-inside: avoid; }
.card.na { border-style: dashed; }
.na-list { list-style: none; padding: 0; margin: 0.5mm 0 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(min(85mm, 100%), 1fr));
  gap: 0.4mm 4mm; font-size: 7.3pt; }
.checks { list-style: none; padding: 0; margin: 0.5mm 0 0; font-size: 7.6pt; }
.checks li { margin: 0.3mm 0; }
.badge { display: inline-block; border: 1px solid currentColor; border-radius: 3px; padding: 0 1.2mm; font-size: 6.8pt; font-weight: 600;
  white-space: nowrap; }
.ok { color: #166534; } .warn { color: #92400e; } .danger { color: #b91c1c; } .info { color: #1e40af; } .muted, .neutral { color: #475569; }
.banner { border-left: 3px solid #92400e; background: #fffbeb; padding: 0.8mm 2mm; margin: 0.8mm 0; font-weight: 600; }
dl.facts { display: grid; grid-template-columns: repeat(auto-fill, minmax(27mm, 1fr)); gap: 0.8mm 2.5mm; margin: 0.8mm 0; }
.full dl.facts { grid-template-columns: repeat(auto-fill, minmax(36mm, 1fr)); }
dl.facts div { min-width: 0; }
dt { color: #475569; font-size: 7pt; }
dd { margin: 0; font-weight: 600; overflow-wrap: anywhere; }
dd small { display: block; font-weight: 400; color: #475569; font-size: 6.8pt; }
table { border-collapse: collapse; width: 100%; font-size: 7pt; margin: 0.8mm 0; }
caption { text-align: left; font-weight: 600; padding-bottom: 0.4mm; }
th, td { border-bottom: 1px solid #cbd5e1; padding: 0.3mm 0.8mm; text-align: left; vertical-align: top; overflow-wrap: break-word; }
th { color: #475569; font-weight: 600; }
.n { text-align: right; white-space: nowrap; }
.more { color: #475569; font-size: 6.8pt; }
.disclaimer { color: #475569; font-size: 6.8pt; border-top: 1px solid #cbd5e1; padding-top: 0.6mm; margin-top: 0.8mm; }
figure { margin: 0; break-inside: avoid; }
svg.map { display: block; width: 100%; height: auto; }
.legend { list-style: none; padding: 0; margin: 0.8mm 0 0; display: flex; flex-wrap: wrap; gap: 0.3mm 3mm; font-size: 6.8pt; }
.legend span.sw { display: inline-block; width: 3mm; height: 2mm; margin-right: 1mm; border: 1.5px solid; vertical-align: middle; }
.mono { font-family: ui-monospace, Consolas, "Courier New", monospace; font-size: 6.4pt; overflow-wrap: anywhere; }
footer { margin-top: 1mm; border-top: 1.5px solid #0f172a; padding-top: 1.2mm; font-size: 7pt; }
footer table { font-size: 6.5pt; }
.foot { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0 4mm; margin-top: 1.5mm; padding-top: 0.8mm;
  border-top: 1px solid #cbd5e1; color: #475569; font-size: 6.8pt; }
@media print { main { padding: 0; max-width: none; } }
/* A phone screen: one column, and table cells wrap instead of widening the page. */
@media screen and (max-width: 140mm) { .cols { column-count: 1; } th, td, .n, .badge { overflow-wrap: anywhere; white-space: normal; } }
"""


def _e(value: Any) -> str:
    return escape("" if value is None else str(value))


def _fact_html(part: BriefPart) -> str:
    if not part.facts:
        return ""
    items = []
    for fact in part.facts:
        tone = "" if fact.tone == "neutral" else f' <span class="badge {fact.tone}">{_e(fact.tone_label or fact.tone)}</span>'
        note = f"<small>{_e(fact.note)}</small>" if fact.note else ""
        items.append(f"<div><dt>{_e(fact.label)}</dt><dd>{_e(fact.text)}{tone}{note}</dd></div>")
    return f'<dl class="facts">{"".join(items)}</dl>'


def _table_html(table: BriefTable) -> str:
    def cls(i: int) -> str:
        return " class=n" if i < len(table.numeric) and table.numeric[i] else ""

    head = "".join(f'<th scope="col"{cls(i)}>{_e(c)}</th>' for i, c in enumerate(table.columns))
    shown = table.rows[:SHEET_TABLE_ROWS]
    rows = ["<tr>" + "".join(f"<td{cls(i)}>{_e(c)}</td>" for i, c in enumerate(row)) + "</tr>" for row in shown]
    more = (f'<p class="more">Showing {len(shown)} of {table.total} rows; the CSV tables of the bundle hold them all.</p>'
            if table.total > len(shown) else "")
    return f"<table><caption>{_e(table.caption)}</caption><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>{more}"


_VERDICT = {"pass": ("ok", "Pass"), "caution": ("warn", "Caution"), "fail": ("danger", "Fail")}
_STATUS = {"assessed": ("ok", "Assessed"), "partial": ("warn", "Partly assessed"), "not_assessed": ("muted", "Not assessed"),
           "info": ("info", "As given")}


def _section_html(section: BriefSection, synthetic: bool, extra_class: str = "") -> str:
    tone, label = _STATUS[section.status]
    heading = f'<h2>{_e(section.title)} <span class="badge {tone}">{label}</span></h2>'
    body: list[str] = []
    for part in section.parts:
        title = ""
        if section.key != "operation" and (len(section.parts) > 1 or part.title != section.title):
            title = f"<h3>{_e(part.title)}</h3>"
        verdict = ""
        if part.verdict is not None:
            vt, vl = _VERDICT[part.verdict.value]
            verdict = f'<p><span class="badge {vt}">Check result: {vl}</span> {_e(part.summary)}</p>'
        elif part.summary:
            verdict = f"<p>{_e(part.summary)}</p>"
        banners = "".join(f'<p class="banner">{_e(b)}</p>' for b in part.banners)
        tables = "".join(_table_html(t) for t in part.tables)
        all_notes = [n for n in part.notes if not (synthetic and n.code in _SYNTHETIC_CODES)]
        notes = ""
        if all_notes:
            more = len(all_notes) - SHEET_NOTES
            notes = ('<p class="more">Notes: ' + "; ".join(_e(n.message) for n in all_notes[:SHEET_NOTES])
                     + (f"; +{more} more in the analysis file" if more > 0 else "") + "</p>")
        disclaimer = f'<p class="disclaimer">{_e(part.disclaimer)}</p>' if part.disclaimer else ""
        body.append(f"{title}{banners}{verdict}{_fact_html(part)}{tables}{notes}{disclaimer}")
    classes = f"card {extra_class}".strip()
    return f'<section class="{classes}" aria-label="{_e(section.title)}">{heading}{"".join(body)}</section>'


def _not_assessed_html(sections: Sequence[BriefSection]) -> str:
    """Every section without an analysis, in one compact card: never left out, never blank."""
    if not sections:
        return ""
    items = "".join(
        f'<li><b>{_e(s.title)}</b> <span class="badge muted">Not assessed</span> <span class="muted">{_e(s.reason)}</span></li>'
        for s in sections
    )
    return (f'<section class="card na full" aria-label="Not assessed in this brief"><h2>Not assessed in this brief</h2>'
            f'<ul class="na-list">{items}</ul></section>')


def _provenance_html(brief: Brief) -> str:
    prov = brief.provenance
    rows = []
    for a in prov.analyses:
        rows.append(
            "<tr>"
            f"<td>{_e(a.title)}<br><span class=mono>{_e(a.analysis_id)}</span></td>"
            f"<td>{_e(a.datapack or 'none')}{' (synthetic)' if a.datapack_synthetic else ''}</td>"
            f"<td>{_e(fmt_utc(a.finished_utc))}</td>"
            f"<td class=mono>{_e(a.params_sha256)}</td>"
            f"<td class=mono>{_e(a.input_sha256 or 'in the parameters')}</td>"
            "</tr>"
        )
    packs = []
    for pack in prov.datapacks:
        # The layers these analyses may read, with source and licence (provenance.json lists all).
        used = [layer for layer in pack.layers if layer.used_by]
        other = len(pack.layers) - len(used)
        layers = "; ".join(
            f"{layer.key}: {layer.source or '—'}{_via(layer.source_url)} ({layer.licence or 'licence not stated'})" for layer in used
        )
        if other:
            layers += f"{'; ' if layers else ''}{other} other layer{'' if other == 1 else 's'} not read by these analyses"
        state = "" if pack.installed else " — no longer installed on this server"
        attribution = f" {' '.join(pack.attribution)}" if pack.attribution else ""
        content = (f' Content SHA-256 <span class="mono">{_e(pack.content_sha256)}</span>.' if pack.content_sha256
                   else " No content hashes (built before they were added).")
        packs.append(
            f"<p><b>Data pack {_e(pack.name)}</b> v{_e(pack.version or '—')}, created {_e(pack.created_utc or '—')}"
            f"{' — SYNTHETIC' if pack.synthetic else ''}{_e(state)}.{content} {_e(layers)}.{_e(attribution)}</p>"
        )
    return (
        "<footer aria-label=\"Provenance\"><h2>Provenance</h2>"
        f"<p>AirBrief {_e(prov.airbrief_version)} · result schema {_e(prov.schema_version)} · brief schema {_e(prov.brief_schema_version)} · "
        f"generated {_e(fmt_utc(prov.generated_utc))} · analyses {_e(fmt_utc(prov.started_utc))} to {_e(fmt_utc(prov.finished_utc))}</p>"
        f'<p>Brief SHA-256 <span class="mono">{_e(prov.brief_sha256)}</span> · operation SHA-256 '
        f'<span class="mono">{_e(prov.operation_sha256)}</span></p>'
        "<table><caption>Analyses (SHA-256 of the canonical parameters and of the input file)</caption>"
        '<thead><tr><th scope="col">Analysis</th><th scope="col">Data pack</th><th scope="col">Finished</th>'
        '<th scope="col">Parameters SHA-256</th><th scope="col">Input file SHA-256</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
        + "".join(packs)
        + f'<p class="signature"><b>{_e(prov.signature_note)}</b></p>'
        + f'<p class="more">Hashes are SHA-256 of {_e(prov.canonical_json)}. {_e(brief.notice)}</p></footer>'
    )


def _via(url: str | None) -> str:
    host = urlsplit(url).hostname if url else None
    return f" via {host}" if host else ""


def _legend(layers: Sequence[MapLayer]) -> str:
    items = "".join(f'<li><span class="sw" style="border-color:{layer.color}"></span>{_e(layer.label)} ({len(layer.features)})</li>'
                    for layer in layers)
    return f'<ul class="legend" aria-label="Legend">{items}</ul>'


def render_brief_html(brief: Brief, layers: Sequence[MapLayer]) -> str:
    """The brief as one self-contained, printable HTML page."""
    op = brief.operation
    meta = [f"Generated {fmt_utc(brief.provenance.generated_utc)}"]
    if op.operator:
        meta.append(f"Operator {op.operator}")
    if op.planned_start_utc:
        meta.append(f"Window from {fmt_utc(op.planned_start_utc)}")
    if op.pilot_position:
        meta.append(f"Pilot {fmt_coord(op.pilot_position[0], op.pilot_position[1])}")
    meta.append(f"Brief SHA-256 {brief.provenance.brief_sha256[:16]}…")
    watermark = f'<div class="watermark" aria-hidden="true">{_e(brief.watermark)}</div>' if brief.watermark else ""
    synthetic = (f'<p class="synthetic" role="note">{_e(brief.watermark)}: computed with a synthetic demo data pack; '
                 "terrain, population and obstacles are invented.</p>") if brief.watermark else ""
    # The banner and watermark say "synthetic" once; the other cross-checks are listed.
    checks = [w for w in brief.warnings if not (brief.watermark and w.code in _SYNTHETIC_CODES)]
    warnings = ""
    if checks:
        tone = {"critical": ("danger", "Critical"), "warning": ("warn", "Warning"), "info": ("info", "Info")}
        items = "".join(f'<li><span class="badge {tone[w.severity.value][0]}">{tone[w.severity.value][1]}</span> {_e(w.message)}</li>'
                        for w in checks)
        warnings = f'<section class="card full" aria-label="Cross-checks"><h2>Cross-checks</h2><ul class="checks">{items}</ul></section>'
    svg = render_map_svg(layers, f"Map of the operation evidence: {', '.join(layer.label for layer in layers)}")
    operation = next((s for s in brief.sections if s.key == "operation"), None)
    assessed = [s for s in brief.sections if s.key != "operation" and s.status != "not_assessed"]
    missing = [s for s in brief.sections if s.status == "not_assessed"]
    cards = "".join(_section_html(s, bool(brief.watermark)) for s in assessed)
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_e(brief.title)}</title><style>{_CSS}</style></head><body>{watermark}<main>"
        f'<p class="notice" role="note">{_e(brief.notice)}</p>'
        f"<header><h1>{_e(brief.title)}</h1><p class=\"meta\">{''.join(f'<span>{_e(m)}</span>' for m in meta)}</p></header>"
        f"{synthetic}{warnings}"
        f"{_section_html(operation, bool(brief.watermark), 'full') if operation is not None else ''}"
        f'<div class="cols"><figure aria-label="Map">{svg}{_legend(layers)}<figcaption class="more">Equirectangular overview, '
        f"north up. Not for navigation.</figcaption></figure>{cards}</div>"
        f"{_not_assessed_html(missing)}"
        f"{_provenance_html(brief)}"
        '<p class="foot"><span><b>Evidence, not authorisation.</b> AirBrief operation evidence brief.</span>'
        "<span>No go/no-go, no flight plan, no waypoints.</span></p>"
        "</main></body></html>"
    )
