"use client";

/**
 * Print-friendly map of a dataset and analysis layers as pure SVG (no WebGL, no tiles).
 *
 * Coordinates (WGS84) are projected equirectangularly around the data's mid-latitude
 * (x = Δlon · cos φ₀, y = Δlat) and fitted to the view box, which is accurate enough for a
 * site-scale overview. Vertices closer than ~0.6 px are dropped so large layers stay light.
 *
 * Layers are drawn in array order (callers put fills first and boundaries and points last).
 * Colours carry the same meaning as the workspace map:
 * - zone features (`properties.zone` = green | yellow | red) get their colour plus the hatch,
 * - flight path segments (`properties.agl_band` = ok | near | over) get the ok / warn / danger
 *   colour, with a dashed overlay above the limit,
 * - `styleFeature` gives per-feature colours (e.g. one per survey battery block).
 * The legend is built from what is actually drawn, and every entry has a text label (never
 * colour alone).
 */

import { useId, useMemo } from "react";
import { ZONE_HEX, ZONE_LABEL, zoneSwatchStyle } from "@/lib/colors";
import { formatLength } from "@/lib/format";
import type { Units } from "@/lib/store";
import type { AglBand, Feature, Geometry, Position, ZoneKind } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Per-feature colour and legend entry (features with the same label share one entry). */
export interface FeatureStyle {
  label: string;
  /** CSS colour (a `var(--token)` or a palette value from lib/colors) */
  color?: string;
  /** draw with the zone colour and hatch instead of `color` */
  zone?: ZoneKind;
}

export interface GeometryLayer {
  id: string;
  label: string;
  features: readonly Feature<Geometry | null, Record<string, unknown>>[];
  /** CSS colour (a `var(--token)` or a palette value from lib/colors) */
  color: string;
  /** dashed lines and outlines */
  dashed?: boolean;
  /** areas: outline only, no fill (boundaries drawn above other layers) */
  outline?: boolean;
  /** lines: a wide translucent band (a highlight drawn under the lines it marks) */
  halo?: boolean;
  /** stroke width in view-box px (default 1.25 for areas, 2 for lines) */
  strokeWidth?: number;
  /** per-feature style; null keeps the layer style */
  styleFeature?: (properties: Record<string, unknown>) => FeatureStyle | null;
  /** short text drawn at the feature's centre, e.g. "Battery 1" */
  labelFeature?: (properties: Record<string, unknown>) => string | null;
}

export interface GeometrySvgProps {
  layers: readonly GeometryLayer[];
  /** accessible title, e.g. "Map of plots.zip with airspace layers" */
  title: string;
  units: Units;
  className?: string;
  /** view box width in px (default 800) */
  width?: number;
  /** view box height range in px (default 260–640): the map's aspect follows the data within it */
  minHeight?: number;
  maxHeight?: number;
}

type XY = [number, number];

const ZONE_KINDS: readonly ZoneKind[] = ["green", "yellow", "red"];

function isZone(value: unknown): value is ZoneKind {
  return typeof value === "string" && (ZONE_KINDS as readonly string[]).includes(value);
}

const AGL_BANDS: readonly AglBand[] = ["ok", "near", "over"];

/** Same colours and words as the workspace flight-audit legend. */
const BAND_STYLE: Readonly<Record<AglBand, { color: string; label: string }>> = {
  ok: { color: "var(--ok)", label: "Below the limit" },
  near: { color: "var(--warn)", label: "Within 10 m of the limit" },
  over: { color: "var(--danger)", label: "Above the limit" },
};

function isBand(value: unknown): value is AglBand {
  return typeof value === "string" && (AGL_BANDS as readonly string[]).includes(value);
}

const DASH = "6 4";

function eachPosition(geometry: Geometry | null, visit: (p: Position) => void): void {
  if (!geometry) return;
  switch (geometry.type) {
    case "Point":
      visit(geometry.coordinates);
      return;
    case "MultiPoint":
    case "LineString":
      geometry.coordinates.forEach(visit);
      return;
    case "MultiLineString":
    case "Polygon":
      geometry.coordinates.forEach((ring) => ring.forEach(visit));
      return;
    case "MultiPolygon":
      geometry.coordinates.forEach((poly) => poly.forEach((ring) => ring.forEach(visit)));
      return;
    case "GeometryCollection":
      geometry.geometries.forEach((g) => eachPosition(g, visit));
      return;
  }
}

interface Projection {
  project: (p: Position) => XY;
  width: number;
  height: number;
  /** metres per view-box pixel */
  metresPerPx: number;
}

function buildProjection(layers: readonly GeometryLayer[], width: number, minHeight: number, maxHeight: number): Projection | null {
  let minLon = Infinity;
  let minLat = Infinity;
  let maxLon = -Infinity;
  let maxLat = -Infinity;
  for (const layer of layers) {
    for (const f of layer.features) {
      eachPosition(f.geometry, (p) => {
        const lon = p[0];
        const lat = p[1];
        if (typeof lon !== "number" || typeof lat !== "number" || !Number.isFinite(lon) || !Number.isFinite(lat)) return;
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
      });
    }
  }
  if (!Number.isFinite(minLon)) return null;

  // Degenerate extents (one point, a north-south line): give them ~500 m of room.
  const minSpan = 0.0045;
  if (maxLon - minLon < minSpan) {
    const c = (minLon + maxLon) / 2;
    minLon = c - minSpan / 2;
    maxLon = c + minSpan / 2;
  }
  if (maxLat - minLat < minSpan) {
    const c = (minLat + maxLat) / 2;
    minLat = c - minSpan / 2;
    maxLat = c + minSpan / 2;
  }

  const lat0 = ((minLat + maxLat) / 2) * (Math.PI / 180);
  const kx = Math.max(0.05, Math.cos(lat0));
  const dataW = (maxLon - minLon) * kx;
  const dataH = maxLat - minLat;
  const margin = 24;
  const height = Math.round(Math.min(maxHeight, Math.max(minHeight, (width * dataH) / dataW)));
  const scale = Math.min((width - 2 * margin) / dataW, (height - 2 * margin) / dataH);
  const offX = (width - dataW * scale) / 2;
  const offY = (height - dataH * scale) / 2;

  return {
    width,
    height,
    metresPerPx: 111_320 / scale,
    project: (p) => [offX + ((p[0] ?? 0) - minLon) * kx * scale, height - (offY + ((p[1] ?? 0) - minLat) * scale)],
  };
}

const MIN_STEP_PX = 0.6;

function lineD(coords: readonly Position[], project: Projection["project"], close: boolean): string {
  if (coords.length === 0) return "";
  const out: string[] = [];
  let last: XY | null = null;
  coords.forEach((p, i) => {
    const xy = project(p);
    const isLast = i === coords.length - 1;
    if (last && !isLast && Math.abs(xy[0] - last[0]) < MIN_STEP_PX && Math.abs(xy[1] - last[1]) < MIN_STEP_PX) return;
    out.push(`${last ? "L" : "M"}${xy[0].toFixed(1)} ${xy[1].toFixed(1)}`);
    last = xy;
  });
  return out.join("") + (close ? "Z" : "");
}

interface Shapes {
  areas: string[];
  lines: string[];
  points: XY[];
}

function collectShapes(geometry: Geometry | null, project: Projection["project"], acc: Shapes): void {
  if (!geometry) return;
  switch (geometry.type) {
    case "Point":
      acc.points.push(project(geometry.coordinates));
      return;
    case "MultiPoint":
      geometry.coordinates.forEach((p) => acc.points.push(project(p)));
      return;
    case "LineString":
      acc.lines.push(lineD(geometry.coordinates, project, false));
      return;
    case "MultiLineString":
      geometry.coordinates.forEach((l) => acc.lines.push(lineD(l, project, false)));
      return;
    case "Polygon":
      acc.areas.push(geometry.coordinates.map((r) => lineD(r, project, true)).join(""));
      return;
    case "MultiPolygon":
      acc.areas.push(geometry.coordinates.map((poly) => poly.map((r) => lineD(r, project, true)).join("")).join(""));
      return;
    case "GeometryCollection":
      geometry.geometries.forEach((g) => collectShapes(g, project, acc));
      return;
  }
}

/** Centre of the projected bounding box (label anchor). */
function projectedCentre(geometry: Geometry | null, project: Projection["project"]): XY | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  eachPosition(geometry, (p) => {
    const [x, y] = project(p);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  });
  return Number.isFinite(minX) ? [(minX + maxX) / 2, (minY + maxY) / 2] : null;
}

function niceLength(target: number): number {
  const pow = Math.pow(10, Math.floor(Math.log10(target)));
  const n = target / pow;
  const nice = n >= 5 ? 5 : n >= 2 ? 2 : 1;
  return nice * pow;
}

type SwatchShape = "area" | "outline" | "line" | "halo" | "point";

interface LegendEntry {
  key: string;
  label: string;
  color: string;
  shape: SwatchShape;
  dashed: boolean;
  /** dashed overlay colour (the "above the limit" band) */
  overlay: string | null;
  zone: ZoneKind | null;
  count: number;
}

interface RenderedFeature {
  key: string;
  shapes: Shapes;
  color: string;
  zone: ZoneKind | null;
  /** dashed overlay drawn over the line (above-the-limit band) */
  overlay: string | null;
  label: string | null;
  centre: XY | null;
  legendKey: string;
}

function shapeOf(shapes: Shapes, layer: GeometryLayer): SwatchShape {
  if (shapes.areas.length > 0) return layer.outline ? "outline" : "area";
  if (shapes.lines.length > 0) return layer.halo ? "halo" : "line";
  return "point";
}

function Swatch({ entry }: { entry: LegendEntry }) {
  if (entry.zone) {
    return <span aria-hidden className="inline-block size-3 shrink-0 rounded-sm" style={zoneSwatchStyle(entry.zone)} />;
  }
  const stroke = { stroke: entry.color };
  return (
    <svg aria-hidden viewBox="0 0 16 12" className="h-3 w-4 shrink-0 overflow-visible">
      {entry.shape === "area" || entry.shape === "outline" ? (
        <rect
          x="1"
          y="1"
          width="14"
          height="10"
          rx="1.5"
          strokeWidth="1.5"
          strokeDasharray={entry.dashed ? "3 2" : undefined}
          style={{ ...stroke, fill: entry.shape === "area" ? entry.color : "none", fillOpacity: 0.3 }}
        />
      ) : entry.shape === "point" ? (
        <circle cx="8" cy="6" r="4" strokeWidth="1.25" style={{ fill: entry.color, stroke: "var(--bg)" }} />
      ) : (
        <>
          <line
            x1="1"
            y1="6"
            x2="15"
            y2="6"
            strokeLinecap="round"
            strokeWidth={entry.shape === "halo" ? 7 : 2.5}
            strokeDasharray={entry.dashed ? "3 2" : undefined}
            style={{ ...stroke, strokeOpacity: entry.shape === "halo" ? 0.35 : 1 }}
          />
          {entry.overlay ? <line x1="1" y1="6" x2="15" y2="6" strokeWidth="1" strokeDasharray="3 2" style={{ stroke: entry.overlay }} /> : null}
        </>
      )}
    </svg>
  );
}

export function GeometrySvg({ layers, title, units, className, width = 800, minHeight = 260, maxHeight = 640 }: GeometrySvgProps) {
  const uid = useId().replace(/:/g, "");
  const projection = useMemo(() => buildProjection(layers, width, minHeight, maxHeight), [layers, width, minHeight, maxHeight]);

  const { rendered, legend } = useMemo(() => {
    const out: { layer: GeometryLayer; features: RenderedFeature[] }[] = [];
    const entries = new Map<string, LegendEntry>();
    const zoneCounts = new Map<ZoneKind, number>();
    if (!projection) return { rendered: out, legend: [] as LegendEntry[] };

    for (const layer of layers) {
      const features = layer.features.map((f, i): RenderedFeature => {
        const shapes: Shapes = { areas: [], lines: [], points: [] };
        collectShapes(f.geometry, projection.project, shapes);
        const props = f.properties ?? {};
        const custom = layer.styleFeature?.(props) ?? null;
        const zone = custom?.zone ?? (isZone(props.zone) ? props.zone : null);
        const band = isBand(props.agl_band) ? props.agl_band : null;
        const label = layer.labelFeature?.(props) ?? null;
        const shape = shapeOf(shapes, layer);

        let color = layer.color;
        let overlay: string | null = null;
        let legendKey: string;
        if (zone) {
          color = ZONE_HEX[zone];
          legendKey = custom ? `${layer.id}:custom:${custom.label}` : `zone:${zone}`;
          if (!custom) zoneCounts.set(zone, (zoneCounts.get(zone) ?? 0) + 1);
        } else if (custom) {
          color = custom.color ?? layer.color;
          legendKey = `${layer.id}:custom:${custom.label}`;
        } else if (band) {
          color = BAND_STYLE[band].color;
          overlay = band === "over" ? "var(--text)" : null;
          legendKey = `${layer.id}:band:${band}`;
        } else {
          legendKey = `${layer.id}:layer`;
        }

        if (!legendKey.startsWith("zone:")) {
          const existing = entries.get(legendKey);
          if (existing) existing.count += 1;
          else
            entries.set(legendKey, {
              key: legendKey,
              label: custom ? custom.label : band ? BAND_STYLE[band].label : layer.label,
              color,
              shape,
              dashed: Boolean(layer.dashed),
              overlay,
              zone: custom?.zone ?? null,
              count: 1,
            });
        }

        return {
          key: `${layer.id}-${i}`,
          shapes,
          color,
          zone,
          overlay,
          label,
          centre: label ? projectedCentre(f.geometry, projection.project) : null,
          legendKey,
        };
      });
      out.push({ layer, features });
    }

    const zoneEntries: LegendEntry[] = ZONE_KINDS.filter((z) => zoneCounts.has(z)).map((z) => ({
      key: `zone:${z}`,
      label: ZONE_LABEL[z],
      color: ZONE_HEX[z],
      shape: "area",
      dashed: false,
      overlay: null,
      zone: z,
      count: zoneCounts.get(z) ?? 0,
    }));
    return { rendered: out, legend: [...entries.values(), ...zoneEntries] };
  }, [layers, projection]);

  const featureCount = layers.reduce((n, l) => n + l.features.length, 0);

  if (!projection) {
    return (
      <div className={cn("flex h-40 items-center justify-center rounded border border-border text-sm text-fg-muted", className)}>
        No geometry with known coordinates to draw.
      </div>
    );
  }

  const { width: W, height: H, metresPerPx } = projection;
  const barMetres = niceLength(metresPerPx * W * 0.2);
  const barPx = barMetres / metresPerPx;

  return (
    <figure className={cn("print-avoid-break space-y-2", className)}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-labelledby={`${uid}-title ${uid}-desc`}
        className="block h-auto w-full rounded border border-border bg-bg"
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={`${uid}-title`}>{title}</title>
        <desc id={`${uid}-desc`}>
          {`${featureCount} features in ${layers.length} layer${layers.length === 1 ? "" : "s"}: ${layers
            .map((l) => `${l.label} (${l.features.length})`)
            .join(", ")}. North is up.`}
        </desc>
        <defs>
          <pattern id={`${uid}-zone-green`} width="8" height="8" patternUnits="userSpaceOnUse">
            <circle cx="4" cy="4" r="1" style={{ fill: ZONE_HEX.green }} />
          </pattern>
          <pattern id={`${uid}-zone-yellow`} width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <path d="M0,0 V7" strokeWidth="1.2" style={{ stroke: ZONE_HEX.yellow }} />
          </pattern>
          <pattern id={`${uid}-zone-red`} width="7" height="7" patternUnits="userSpaceOnUse">
            <path d="M0,0 L7,7 M7,0 L0,7" strokeWidth="1" style={{ stroke: ZONE_HEX.red }} />
          </pattern>
        </defs>

        {rendered.map(({ layer, features }) => {
          const dash = layer.dashed ? DASH : undefined;
          const areaStroke = layer.strokeWidth ?? (layer.outline ? 2 : 1.25);
          const lineStroke = layer.halo ? Math.max(8, layer.strokeWidth ?? 0) : layer.strokeWidth ?? 2;
          return (
            <g key={layer.id} aria-hidden>
              {features.map((f) => (
                <g key={f.key}>
                  {f.shapes.areas.map((d, i) => (
                    <g key={`a${i}`}>
                      {layer.outline ? null : (
                        <path d={d} fillRule="evenodd" style={{ fill: f.color, fillOpacity: f.zone ? 0.18 : 0.14 }} />
                      )}
                      {f.zone && !layer.outline ? (
                        <path d={d} fillRule="evenodd" fill={`url(#${uid}-zone-${f.zone})`} opacity={0.7} />
                      ) : null}
                      <path
                        d={d}
                        fill="none"
                        strokeWidth={areaStroke}
                        strokeLinejoin="round"
                        strokeDasharray={dash}
                        style={{ stroke: f.color }}
                      />
                    </g>
                  ))}
                  {f.shapes.lines.map((d, i) => (
                    <g key={`l${i}`}>
                      <path
                        d={d}
                        fill="none"
                        strokeWidth={lineStroke}
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeDasharray={dash}
                        style={{ stroke: f.color, strokeOpacity: layer.halo ? 0.35 : 1 }}
                      />
                      {f.overlay ? (
                        <path
                          d={d}
                          fill="none"
                          strokeWidth={1}
                          strokeDasharray={DASH}
                          strokeLinecap="butt"
                          style={{ stroke: f.overlay }}
                        />
                      ) : null}
                    </g>
                  ))}
                  {f.shapes.points.map(([x, y], i) => (
                    <circle
                      key={`p${i}`}
                      cx={x}
                      cy={y}
                      r={4}
                      strokeWidth={1.25}
                      style={{ fill: f.color, stroke: "var(--bg)" }}
                    />
                  ))}
                  {f.label && f.centre ? (
                    <text
                      x={f.centre[0]}
                      y={f.shapes.points.length > 0 && f.shapes.areas.length === 0 ? f.centre[1] - 9 : f.centre[1] + 4}
                      textAnchor="middle"
                      fontSize="11"
                      fontWeight={600}
                      strokeWidth={3}
                      strokeLinejoin="round"
                      paintOrder="stroke"
                      style={{ fill: "var(--text)", stroke: "var(--bg)" }}
                    >
                      {f.label}
                    </text>
                  ) : null}
                </g>
              ))}
            </g>
          );
        })}

        {/* north arrow */}
        <g transform={`translate(${W - 24} 30)`} aria-hidden>
          <path d="M0,-12 L5,4 L0,0 L-5,4 Z" style={{ fill: "var(--text)" }} />
          <text x="0" y="16" textAnchor="middle" fontSize="10" style={{ fill: "var(--text-muted)" }}>
            N
          </text>
        </g>
        {/* scale bar */}
        <g transform={`translate(16 ${H - 16})`} aria-hidden>
          <path
            d={`M0,0 H${barPx.toFixed(1)} M0,-4 V4 M${barPx.toFixed(1)},-4 V4`}
            fill="none"
            strokeWidth="1.25"
            style={{ stroke: "var(--text)" }}
          />
          <text x={barPx + 6} y="4" fontSize="10" style={{ fill: "var(--text-muted)" }}>
            {formatLength(barMetres, units)}
          </text>
        </g>
      </svg>
      <figcaption>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted" aria-label="Legend">
          {legend.map((entry) => (
            <li key={entry.key} className="flex items-center gap-1.5">
              <Swatch entry={entry} />
              <span className="text-fg">{entry.label}</span>
              <span className="num">({entry.count})</span>
            </li>
          ))}
        </ul>
        <p className="mt-1 text-2xs text-fg-muted">
          Equirectangular overview, north up. Not for navigation.
        </p>
      </figcaption>
    </figure>
  );
}
