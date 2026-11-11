/**
 * deck.gl layer factories shared by the dataset layer and every module's `buildLayers`.
 *
 * Conventions (see frontend/README.md "Module contract"):
 * - Layer ids follow `${moduleId}:${layerKey}`; sub-layers append `/${suffix}` so hiding the
 *   parent id in the Layers panel hides them too (`filterHiddenLayers`).
 * - Widths and radii are in screen pixels unless stated, so layers read the same at any zoom.
 * - Colours are `[r, g, b, a]` (0–255). Read theme colours with `mapPalette()` at build time;
 *   rebuild layers when the theme changes (the workspace passes `ctx.theme`).
 * - Zones are never colour-only: use `hatchZoneLayer` (colour + hatch) plus a legend label.
 */

import { COORDINATE_SYSTEM, type Layer, type Position } from "@deck.gl/core";
import { FillStyleExtension, PathStyleExtension, type FillStyleExtensionProps, type PathStyleExtensionProps } from "@deck.gl/extensions";
import { H3HexagonLayer } from "@deck.gl/geo-layers";
import { GeoJsonLayer, PathLayer, ScatterplotLayer, TextLayer, type GeoJsonLayerProps } from "@deck.gl/layers";
import {
  ZONE_HEX,
  ZONE_LABEL,
  ZONE_PATTERN,
  legendBreaks,
  tokenRgba,
  zoneColor,
  type HatchAtlas,
  type RGBA,
} from "@/lib/colors";
import type { LegendSpec } from "@/lib/modules";
import type { BBoxList, Feature, FeatureCollection, Geometry, Position as GeoPosition, ZoneKind } from "@/lib/types";

export interface MapPalette {
  bg: RGBA;
  surface: RGBA;
  border: RGBA;
  text: RGBA;
  muted: RGBA;
  subtle: RGBA;
  accent: RGBA;
  accentText: RGBA;
  ok: RGBA;
  warn: RGBA;
  danger: RGBA;
}

/** Theme colours for deck.gl, read from the CSS tokens now (call while building layers). */
export function mapPalette(): MapPalette {
  return {
    bg: tokenRgba("bg"),
    surface: tokenRgba("surface"),
    border: tokenRgba("border"),
    text: tokenRgba("text"),
    muted: tokenRgba("text-muted"),
    subtle: tokenRgba("text-subtle"),
    accent: tokenRgba("accent"),
    accentText: tokenRgba("accent-text"),
    ok: tokenRgba("ok"),
    warn: tokenRgba("warn"),
    danger: tokenRgba("danger"),
  };
}

/** UI font stack for deck.gl text (canvas fonts cannot use CSS variables). */
export const MAP_FONT_FAMILY = "Geist, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif";

/** True when `layerId` is `parentId` or one of its sub-layers (`${parentId}/...`). */
export function layerIdMatches(layerId: string, parentId: string): boolean {
  return layerId === parentId || layerId.startsWith(`${parentId}/`);
}

/** Drop layers whose id (or parent id) the user hid in the Layers panel. */
export function filterHiddenLayers(layers: readonly Layer[], hidden: ReadonlySet<string>): Layer[] {
  if (hidden.size === 0) return [...layers];
  const hiddenIds = [...hidden];
  return layers.filter((layer) => !hiddenIds.some((h) => layerIdMatches(layer.id, h)));
}

export function filterHiddenLegends(legends: readonly LegendSpec[], hidden: ReadonlySet<string>): LegendSpec[] {
  if (hidden.size === 0) return [...legends];
  const hiddenIds = [...hidden];
  return legends.filter((legend) => !legend.layerId || !hiddenIds.some((h) => layerIdMatches(legend.layerId as string, h)));
}

/** A value, or a function of the feature's properties. */
export type PropValue<P, T> = T | ((properties: P, feature: Feature<Geometry | null, P>) => T);

interface WithProperties<P> {
  properties: P;
}

function propAccessor<P, T>(value: PropValue<P, T> | undefined): T | ((f: WithProperties<P>) => T) | undefined {
  if (typeof value === "function") {
    const fn = value as (properties: P, feature: Feature<Geometry | null, P>) => T;
    return (f: WithProperties<P>) => fn(f.properties, f as unknown as Feature<Geometry | null, P>);
  }
  return value;
}

let drawHeights = false;

/**
 * Build layers for a 2D or a 3D map. The basemap has no terrain, so heights above sea level
 * would float data hundreds of metres up, where the perspective camera enlarges and shifts it.
 * - 2D: every factory below draws flat (Z dropped).
 * - 3D: heights are drawn, relative to the ground: callers pass `heightBase`, the altitude that
 *   stands for ground level (the take-off point of a flight, the origin of a drone show).
 * The workspace wraps each module's `buildLayers` in this; layers built outside it are flat.
 */
export function withHeights<T>(view3d: boolean, build: () => T): T {
  const previous = drawHeights;
  drawHeights = view3d;
  try {
    return build();
  } finally {
    drawHeights = previous;
  }
}

/** A position as drawn: [x, y] in 2D, [x, y, z - base] in 3D. */
export function drawnPosition(p: Position, base = 0): Position {
  if (!drawHeights) return [p[0] as number, p[1] as number];
  return [p[0] as number, p[1] as number, Number(p[2] ?? 0) - base];
}

const DRAWABLE_CACHE = new WeakMap<object, Map<string, unknown[]>>();
const EMPTY_FEATURES: readonly never[] = Object.freeze([]);

type Coordinates = number[] | Coordinates[];

function hasZ(c: Coordinates): boolean {
  let first: unknown = c;
  while (Array.isArray(first) && Array.isArray(first[0])) first = first[0];
  return Array.isArray(first) && first.length > 2;
}

/** Coordinates as drawn: `flat` drops Z; otherwise Z is shifted down by `base`. */
function drawnCoordinates(c: Coordinates, flat: boolean, base: number): Coordinates {
  if (typeof c[0] === "number") {
    if (c.length <= 2) return c;
    return flat ? [c[0] as number, c[1] as number] : [c[0] as number, c[1] as number, (c[2] as number) - base];
  }
  return (c as Coordinates[]).map((inner) => drawnCoordinates(inner, flat, base));
}

function drawnGeometry(g: Geometry, flat: boolean, base: number): Geometry {
  if (g.type === "GeometryCollection") return { ...g, geometries: g.geometries.map((x) => drawnGeometry(x, flat, base)) };
  const coordinates = (g as { coordinates: Coordinates }).coordinates;
  if (!hasZ(coordinates) || (!flat && base === 0)) return g;
  return { ...g, coordinates: drawnCoordinates(coordinates, flat, base) } as Geometry;
}

/**
 * Features with a non-null geometry (deck.gl rejects null geometries), with heights drawn by the
 * rule of `withHeights` (flat in 2D; in 3D, Z minus `heightBase`). Heights are always shown in
 * the tables and profile charts.
 *
 * The result is cached per input object and mode, so layers rebuilt on every frame (playback,
 * hover) get the *same* data array and deck.gl does not re-tessellate unchanged geometry.
 */
export function drawableFeatures<P>(
  data: FeatureCollection<Geometry | null, P> | readonly Feature<Geometry | null, P>[] | null | undefined,
  heightBase = 0,
): Feature<Geometry, P>[] {
  if (!data) return EMPTY_FEATURES as unknown as Feature<Geometry, P>[];
  const flat = !drawHeights;
  const key = flat ? "flat" : `z-${heightBase}`;
  let byMode = DRAWABLE_CACHE.get(data);
  if (!byMode) {
    byMode = new Map();
    DRAWABLE_CACHE.set(data, byMode);
  }
  const cached = byMode.get(key);
  if (cached) return cached as Feature<Geometry, P>[];
  const list = Array.isArray(data) ? data : (data as FeatureCollection<Geometry | null, P>).features;
  const out = (list as readonly Feature<Geometry | null, P>[])
    .filter((f): f is Feature<Geometry, P> => f != null && f.geometry != null)
    .map((f) => {
      const geometry = drawnGeometry(f.geometry, flat, heightBase);
      return geometry === f.geometry ? f : { ...f, geometry };
    });
  // Frozen: the same array is shared by every layer built from this input.
  Object.freeze(out);
  byMode.set(key, out);
  return out;
}

/**
 * Cache a value derived from `source` (e.g. a filtered list of a result array) per source object.
 * Keys are global per source: namespace them by module (e.g. "evtol:endpoint-labels"), and treat
 * the cached value as read-only (it is shared by every later call).
 */
const DERIVED_CACHE = new WeakMap<object, Map<string, unknown>>();
export function derived<T>(source: object, key: string, compute: () => T): T {
  let entry = DERIVED_CACHE.get(source);
  if (!entry) {
    entry = new Map();
    DERIVED_CACHE.set(source, entry);
  }
  if (entry.has(key)) return entry.get(key) as T;
  const value = compute();
  entry.set(key, value);
  return value;
}

export interface GeoJsonLayerOptions<P> {
  id: string;
  data: FeatureCollection<Geometry | null, P> | readonly Feature<Geometry | null, P>[] | null | undefined;
  /** altitude drawn as ground level in 3D (see `withHeights`); default 0 */
  heightBase?: number;
  fillColor?: PropValue<P, RGBA>;
  lineColor?: PropValue<P, RGBA>;
  /** px */
  lineWidth?: PropValue<P, number>;
  /** px */
  pointRadius?: PropValue<P, number>;
  filled?: boolean;
  stroked?: boolean;
  pickable?: boolean;
  visible?: boolean;
  opacity?: number;
  /** extrude polygons by `elevation` metres (3D) */
  extruded?: boolean;
  elevation?: PropValue<P, number>;
  wireframe?: boolean;
  /** dashed lines and polygon outlines */
  dashed?: boolean;
  /** `[dash, gap]` in line widths (default [4, 3]) */
  dashArray?: [number, number];
  /** values that should trigger accessor re-evaluation (e.g. selection, theme) */
  updateTriggers?: unknown[];
}

/** GeoJSON with line widths and point radii in pixels. */
export function geoJsonLayer<P>(o: GeoJsonLayerOptions<P>): Layer {
  const data = drawableFeatures(o.data, o.heightBase ?? 0) as unknown as GeoJsonLayerProps<P>["data"];
  const triggers = o.updateTriggers ?? [];
  const common = {
    id: o.id,
    data,
    filled: o.filled ?? true,
    stroked: o.stroked ?? true,
    pickable: o.pickable ?? true,
    visible: o.visible ?? true,
    opacity: o.opacity ?? 1,
    extruded: o.extruded ?? false,
    wireframe: o.wireframe ?? false,
    pointType: "circle",
    lineWidthUnits: "pixels" as const,
    lineWidthMinPixels: 0,
    pointRadiusUnits: "pixels" as const,
    lineJointRounded: true,
    lineCapRounded: true,
    getFillColor: propAccessor<P, RGBA>(o.fillColor ?? [59, 130, 246, 60]),
    getLineColor: propAccessor<P, RGBA>(o.lineColor ?? [59, 130, 246, 255]),
    getLineWidth: propAccessor<P, number>(o.lineWidth ?? 1.5),
    getPointRadius: propAccessor<P, number>(o.pointRadius ?? 5),
    getElevation: propAccessor<P, number>(o.elevation ?? 0),
    updateTriggers: {
      getFillColor: triggers,
      getLineColor: triggers,
      getLineWidth: triggers,
      getPointRadius: triggers,
      getElevation: triggers,
      getDashArray: triggers,
    },
  };
  if (o.dashed) {
    return new GeoJsonLayer<P, PathStyleExtensionProps>({
      ...common,
      extensions: [new PathStyleExtension({ dash: true })],
      getDashArray: o.dashArray ?? [4, 3],
      dashJustified: true,
    });
  }
  return new GeoJsonLayer<P>(common);
}

export interface HatchZoneLayerOptions<P> {
  /** base id, e.g. "airspace:zones" (the hatch overlay is `${id}/hatch`) */
  id: string;
  data: FeatureCollection<Geometry | null, P> | readonly Feature<Geometry | null, P>[] | null | undefined;
  /** the zone of a feature (null = draw as neutral outline) */
  getZone: (properties: P) => ZoneKind | null;
  hatch: HatchAtlas | null;
  /** alpha of the translucent base fill (default 45) */
  fillAlpha?: number;
  /** alpha of the hatch pattern (default 200) */
  hatchAlpha?: number;
  /** px (default 1.5) */
  lineWidth?: number;
  /** emphasise a feature (thicker outline) */
  isHighlighted?: (properties: P) => boolean;
  pickable?: boolean;
  visible?: boolean;
  updateTriggers?: unknown[];
}

const NEUTRAL_ZONE: RGBA = [148, 163, 184, 255];

/**
 * Zone polygons: a translucent zone-coloured fill and outline (pickable) plus a hatch overlay
 * (green = dots, yellow = diagonal, red = cross) via `FillStyleExtension`, so zones never
 * rely on colour alone. Without the hatch atlas (SSR / first frame) only the base is drawn.
 */
export function hatchZoneLayer<P>(o: HatchZoneLayerOptions<P>): Layer[] {
  const features = drawableFeatures(o.data);
  const fillAlpha = o.fillAlpha ?? 45;
  const hatchAlpha = o.hatchAlpha ?? 200;
  const triggers = o.updateTriggers ?? [];
  const zoneOf = (f: WithProperties<P>) => o.getZone(f.properties);
  const data = features as unknown as GeoJsonLayerProps<P>["data"];

  const base = new GeoJsonLayer<P>({
    id: o.id,
    data,
    filled: true,
    stroked: true,
    pickable: o.pickable ?? true,
    visible: o.visible ?? true,
    lineWidthUnits: "pixels",
    pointRadiusUnits: "pixels",
    pointType: "circle",
    getPointRadius: 5,
    getFillColor: (f: WithProperties<P>) => {
      const z = zoneOf(f);
      return z ? zoneColor(z, fillAlpha) : ([NEUTRAL_ZONE[0], NEUTRAL_ZONE[1], NEUTRAL_ZONE[2], fillAlpha] as RGBA);
    },
    getLineColor: (f: WithProperties<P>) => {
      const z = zoneOf(f);
      return z ? zoneColor(z, 255) : NEUTRAL_ZONE;
    },
    getLineWidth: (f: WithProperties<P>) => (o.isHighlighted?.(f.properties) ? (o.lineWidth ?? 1.5) * 2.5 : (o.lineWidth ?? 1.5)),
    updateTriggers: { getFillColor: triggers, getLineColor: triggers, getLineWidth: triggers },
  });

  if (!o.hatch) return [base];
  const hatch = o.hatch;

  const pattern = new GeoJsonLayer<P, FillStyleExtensionProps>({
    id: `${o.id}/hatch`,
    data,
    filled: true,
    stroked: false,
    pickable: false,
    visible: o.visible ?? true,
    getFillColor: (f: WithProperties<P>) => {
      const z = zoneOf(f);
      return z ? zoneColor(z, hatchAlpha) : ([0, 0, 0, 0] as RGBA);
    },
    extensions: [new FillStyleExtension({ pattern: true })],
    fillPatternAtlas: hatch.atlas,
    fillPatternMapping: hatch.mapping,
    fillPatternMask: true,
    getFillPattern: (f: WithProperties<P>) => {
      const z = zoneOf(f);
      return z ? ZONE_PATTERN[z] : "solid";
    },
    getFillPatternScale: 1,
    getFillPatternOffset: [0, 0],
    updateTriggers: { getFillColor: triggers, getFillPattern: triggers },
  });

  return [base, pattern];
}

export interface TextLabelLayerOptions<D> {
  id: string;
  data: readonly D[];
  getPosition: (d: D) => Position;
  /** altitude drawn as ground level in 3D (see `withHeights`); default 0 */
  heightBase?: number;
  getText: (d: D) => string;
  /** px (default 12) */
  size?: number;
  color?: RGBA;
  /** background chip colour (default: theme surface at 85 %) */
  background?: RGBA | null;
  /** px offset `[x, y]` (default [0, 0]; positive y is down) */
  pixelOffset?: [number, number];
  anchor?: "start" | "middle" | "end";
  baseline?: "top" | "center" | "bottom";
  pickable?: boolean;
  visible?: boolean;
  updateTriggers?: unknown[];
}

/** Text labels with a background chip for legibility on any basemap. */
export function textLabelLayer<D>(o: TextLabelLayerOptions<D>): Layer {
  const palette = mapPalette();
  const bg = o.background === undefined ? ([palette.surface[0], palette.surface[1], palette.surface[2], 217] as RGBA) : o.background;
  const triggers = o.updateTriggers ?? [];
  const base = o.heightBase ?? 0;
  const heights = drawHeights;
  return new TextLayer<D>({
    id: o.id,
    data: o.data as D[],
    getPosition: (d: D) => drawnPosition(o.getPosition(d), base),
    getText: o.getText,
    getSize: o.size ?? 12,
    sizeUnits: "pixels",
    getColor: o.color ?? palette.text,
    getPixelOffset: o.pixelOffset ?? [0, 0],
    getTextAnchor: o.anchor ?? "middle",
    getAlignmentBaseline: o.baseline ?? "center",
    fontFamily: MAP_FONT_FAMILY,
    fontWeight: 500,
    // "auto" builds the font atlas from the data's characters; with no labels that atlas would be
    // empty (0 px high) and WebGL warns "texSubImage2D: no canvas". Empty layers leave the prop
    // out entirely (an explicit undefined also counts as empty) so deck.gl uses its default set.
    ...(Array.isArray(o.data) && o.data.length === 0 ? {} : { characterSet: "auto" as const }),
    billboard: true,
    background: bg !== null,
    getBackgroundColor: bg ?? [0, 0, 0, 0],
    backgroundPadding: [4, 2, 4, 2],
    pickable: o.pickable ?? false,
    visible: o.visible ?? true,
    updateTriggers: { getText: triggers, getPosition: [...triggers, heights, base], getColor: triggers },
  });
}

export interface H3LayerOptions<D> {
  id: string;
  data: readonly D[];
  getHexagon: (d: D) => string;
  getFillColor: (d: D) => RGBA;
  /** outline colour (default: none) */
  getLineColor?: (d: D) => RGBA;
  /** px outline width (default 0.5 when getLineColor is given) */
  lineWidth?: number;
  extruded?: boolean;
  /** metres (when extruded) */
  getElevation?: (d: D) => number;
  elevationScale?: number;
  /** 0..1 fraction of the cell drawn (default 1) */
  coverage?: number;
  opacity?: number;
  pickable?: boolean;
  visible?: boolean;
  updateTriggers?: unknown[];
}

/** H3 cells (score grids, density, flyability). */
export function h3Layer<D>(o: H3LayerOptions<D>): Layer {
  const triggers = o.updateTriggers ?? [];
  const stroked = Boolean(o.getLineColor);
  return new H3HexagonLayer<D>({
    id: o.id,
    data: o.data as D[],
    getHexagon: o.getHexagon,
    getFillColor: o.getFillColor,
    getLineColor: o.getLineColor ?? [0, 0, 0, 0],
    stroked,
    filled: true,
    lineWidthUnits: "pixels",
    getLineWidth: o.lineWidth ?? 0.5,
    extruded: o.extruded ?? false,
    getElevation: o.getElevation ?? 0,
    elevationScale: o.elevationScale ?? 1,
    coverage: o.coverage ?? 1,
    highPrecision: "auto",
    opacity: o.opacity ?? 0.85,
    pickable: o.pickable ?? true,
    visible: o.visible ?? true,
    updateTriggers: { getFillColor: triggers, getLineColor: triggers, getElevation: triggers },
  });
}

export interface PointLayerOptions<D> {
  id: string;
  data: readonly D[];
  getPosition: (d: D) => Position;
  /** altitude drawn as ground level in 3D (see `withHeights`); default 0 */
  heightBase?: number;
  /** px (default 6) */
  radius?: number | ((d: D) => number);
  fillColor?: RGBA | ((d: D) => RGBA);
  lineColor?: RGBA | ((d: D) => RGBA);
  /** px outline (default 1.5; 0 = none) */
  lineWidth?: number | ((d: D) => number);
  filled?: boolean;
  pickable?: boolean;
  visible?: boolean;
  /** `COORDINATE_SYSTEM.METER_OFFSETS` etc. (default LNGLAT) */
  coordinateSystem?: (typeof COORDINATE_SYSTEM)[keyof typeof COORDINATE_SYSTEM];
  coordinateOrigin?: [number, number, number];
  updateTriggers?: unknown[];
}

/** Circles in screen pixels (sites, obstacles, aerodromes, markers). */
export function pointLayer<D>(o: PointLayerOptions<D>): Layer {
  const palette = mapPalette();
  const triggers = o.updateTriggers ?? [];
  const lineWidth = o.lineWidth ?? 1.5;
  const base = o.heightBase ?? 0;
  const heights = drawHeights;
  return new ScatterplotLayer<D>({
    id: o.id,
    data: o.data as D[],
    getPosition: (d: D) => drawnPosition(o.getPosition(d), base),
    getRadius: o.radius ?? 6,
    radiusUnits: "pixels",
    getFillColor: o.fillColor ?? palette.accent,
    getLineColor: o.lineColor ?? palette.bg,
    getLineWidth: lineWidth,
    lineWidthUnits: "pixels",
    stroked: typeof lineWidth === "function" || lineWidth > 0,
    filled: o.filled ?? true,
    billboard: true,
    pickable: o.pickable ?? true,
    visible: o.visible ?? true,
    ...(o.coordinateSystem !== undefined ? { coordinateSystem: o.coordinateSystem } : {}),
    ...(o.coordinateOrigin ? { coordinateOrigin: o.coordinateOrigin } : {}),
    updateTriggers: { getFillColor: triggers, getLineColor: triggers, getRadius: triggers, getPosition: [...triggers, heights, base], getLineWidth: triggers },
  });
}

export interface PathLayerOptions<D> {
  id: string;
  data: readonly D[];
  getPath: (d: D) => Position[];
  /** altitude drawn as ground level in 3D (see `withHeights`); default 0 */
  heightBase?: number;
  color?: RGBA | ((d: D) => RGBA);
  /** px (default 2) */
  width?: number | ((d: D) => number);
  dashed?: boolean;
  /** `[dash, gap]` in line widths (default [4, 3]) */
  dashArray?: [number, number];
  pickable?: boolean;
  visible?: boolean;
  updateTriggers?: unknown[];
}

/** Polylines (routes, tracks, profiles). Dashed lines use PathStyleExtension. */
export function pathLayer<D>(o: PathLayerOptions<D>): Layer {
  const palette = mapPalette();
  const triggers = o.updateTriggers ?? [];
  const base = o.heightBase ?? 0;
  const heights = drawHeights;
  const common = {
    id: o.id,
    data: o.data as D[],
    getPath: (d: D) => o.getPath(d).map((p) => drawnPosition(p, base)),
    getColor: o.color ?? palette.accent,
    getWidth: o.width ?? 2,
    widthUnits: "pixels" as const,
    widthMinPixels: 1,
    capRounded: true,
    jointRounded: true,
    pickable: o.pickable ?? true,
    visible: o.visible ?? true,
    updateTriggers: { getColor: triggers, getWidth: triggers, getPath: [...triggers, heights, base] },
  };
  if (o.dashed) {
    return new PathLayer<D, PathStyleExtensionProps>({
      ...common,
      extensions: [new PathStyleExtension({ dash: true })],
      getDashArray: o.dashArray ?? [4, 3],
      dashJustified: true,
    });
  }
  return new PathLayer<D>(common);
}

export interface DronePoint {
  id: string;
  /** index in `drone_ids` */
  index: number;
  /** [east, north, up] metres from the origin */
  position: [number, number, number];
}

/** A text label at [east, north, up] metres from the show origin. */
export interface DroneLabel {
  position: [number, number, number];
  text: string;
}

export interface DroneCloudLayerOptions {
  /** e.g. "shows:drones" (rings are `${id}/rings`, labels `${id}/labels`) */
  id: string;
  /** [lon, lat, alt] of the show origin (ShowFrames.origin) */
  origin: readonly number[];
  /** one frame: [drone][e, n, u] (ShowFrames.positions[frame]) */
  positions: readonly (readonly number[])[];
  /** ShowFrames.drone_ids */
  ids: readonly string[];
  /** px (default 4) */
  radius?: number;
  color?: (drone: DronePoint) => RGBA;
  /** drones drawn with a danger ring and a text label (e.g. a conflict pair) */
  highlightIds?: ReadonlySet<string>;
  /** label text for highlighted drones (default: the drone id) */
  highlightLabel?: (drone: DronePoint) => string;
  /**
   * Labels drawn instead of one per highlighted drone, e.g. one per conflict pair: two drones a
   * metre apart sit on the same pixel and their own labels would overprint.
   */
  labels?: readonly DroneLabel[];
  pickable?: boolean;
  visible?: boolean;
  updateTriggers?: unknown[];
}

/** Convert one ShowFrames frame to drone points (skips malformed rows). */
export function dronePoints(ids: readonly string[], positions: readonly (readonly number[])[]): DronePoint[] {
  const out: DronePoint[] = [];
  for (let i = 0; i < ids.length; i += 1) {
    const p = positions[i];
    if (!p || p.length < 2) continue;
    const e = Number(p[0]);
    const n = Number(p[1]);
    const u = Number(p[2] ?? 0);
    if (!Number.isFinite(e) || !Number.isFinite(n)) continue;
    out.push({ id: ids[i] as string, index: i, position: [e, n, Number.isFinite(u) ? u : 0] });
  }
  return out;
}

/**
 * Drones as points in metres east/north/up from the show origin
 * (`COORDINATE_SYSTEM.METER_OFFSETS`), with optional highlight rings + labels.
 */
export function droneCloudLayer(o: DroneCloudLayerOptions): Layer[] {
  const palette = mapPalette();
  // Positions are metres east/north/up of the origin, which stands on the ground: drawn at its
  // longitude/latitude only, so "up" is height above the ground (3D) or dropped (2D).
  const origin: [number, number, number] = [Number(o.origin[0] ?? 0), Number(o.origin[1] ?? 0), 0];
  const heights = drawHeights;
  const position = (d: DronePoint) => drawnPosition(d.position);
  const points = dronePoints(o.ids, o.positions);
  const triggers = o.updateTriggers ?? [];
  const color = o.color ?? (() => palette.accentText);
  const layers: Layer[] = [
    new ScatterplotLayer<DronePoint>({
      id: o.id,
      data: points,
      coordinateSystem: COORDINATE_SYSTEM.METER_OFFSETS,
      coordinateOrigin: origin,
      getPosition: position,
      getRadius: o.radius ?? 4,
      radiusUnits: "pixels",
      getFillColor: color,
      getLineColor: palette.bg,
      getLineWidth: 1,
      lineWidthUnits: "pixels",
      stroked: true,
      billboard: true,
      pickable: o.pickable ?? true,
      visible: o.visible ?? true,
      updateTriggers: { getFillColor: triggers, getPosition: [...triggers, heights] },
    }),
  ];
  const highlightIds = o.highlightIds;
  if (highlightIds && highlightIds.size > 0) {
    const highlighted = points.filter((p) => highlightIds.has(p.id));
    const labels: readonly DroneLabel[] =
      o.labels ?? highlighted.map((d) => ({ position: d.position, text: o.highlightLabel ? o.highlightLabel(d) : d.id }));
    layers.push(
      new ScatterplotLayer<DronePoint>({
        id: `${o.id}/rings`,
        data: highlighted,
        coordinateSystem: COORDINATE_SYSTEM.METER_OFFSETS,
        coordinateOrigin: origin,
        getPosition: position,
        getRadius: (o.radius ?? 4) + 8,
        radiusUnits: "pixels",
        filled: false,
        stroked: true,
        getLineColor: palette.danger,
        getLineWidth: 2.5,
        lineWidthUnits: "pixels",
        billboard: true,
        pickable: false,
        visible: o.visible ?? true,
        updateTriggers: { getPosition: [...triggers, heights] },
      }),
      new TextLayer<DroneLabel>({
        id: `${o.id}/labels`,
        data: labels,
        coordinateSystem: COORDINATE_SYSTEM.METER_OFFSETS,
        coordinateOrigin: origin,
        getPosition: (d) => drawnPosition(d.position),
        getText: (d) => d.text,
        getSize: 12,
        sizeUnits: "pixels",
        getColor: palette.text,
        getPixelOffset: [0, -((o.radius ?? 4) + 18)],
        fontFamily: MAP_FONT_FAMILY,
        fontWeight: 600,
        ...(labels.length === 0 ? {} : { characterSet: "auto" as const }), // see textLabelLayer
        background: true,
        getBackgroundColor: [palette.surface[0], palette.surface[1], palette.surface[2], 230],
        backgroundPadding: [4, 2, 4, 2],
        billboard: true,
        pickable: false,
        visible: o.visible ?? true,
        updateTriggers: { getPosition: [...triggers, heights], getText: triggers },
      }),
    );
  }
  return layers;
}

type MutableBounds = [number, number, number, number];

function extendWithPosition(b: MutableBounds, p: GeoPosition): void {
  const lon = p[0];
  const lat = p[1];
  if (typeof lon !== "number" || typeof lat !== "number" || !Number.isFinite(lon) || !Number.isFinite(lat)) return;
  if (lon < b[0]) b[0] = lon;
  if (lat < b[1]) b[1] = lat;
  if (lon > b[2]) b[2] = lon;
  if (lat > b[3]) b[3] = lat;
}

function extendWithGeometry(b: MutableBounds, g: Geometry | null | undefined): void {
  if (!g) return;
  switch (g.type) {
    case "Point":
      extendWithPosition(b, g.coordinates);
      break;
    case "MultiPoint":
    case "LineString":
      g.coordinates.forEach((p) => extendWithPosition(b, p));
      break;
    case "MultiLineString":
    case "Polygon":
      g.coordinates.forEach((ring) => ring.forEach((p) => extendWithPosition(b, p)));
      break;
    case "MultiPolygon":
      g.coordinates.forEach((poly) => poly.forEach((ring) => ring.forEach((p) => extendWithPosition(b, p))));
      break;
    case "GeometryCollection":
      g.geometries.forEach((child) => extendWithGeometry(b, child));
      break;
    default:
      break;
  }
}

function finish(b: MutableBounds): BBoxList | null {
  return Number.isFinite(b[0]) && Number.isFinite(b[1]) && b[0] <= b[2] && b[1] <= b[3] ? [b[0], b[1], b[2], b[3]] : null;
}

function emptyBounds(): MutableBounds {
  return [Infinity, Infinity, -Infinity, -Infinity];
}

/** Bounds of one geometry, or null when it has no valid coordinates. */
export function geometryBounds(geometry: Geometry | null | undefined): BBoxList | null {
  const b = emptyBounds();
  extendWithGeometry(b, geometry);
  return finish(b);
}

/** Bounds of several FeatureCollections (e.g. all `result.layers`). */
export function collectionBounds(collections: readonly (FeatureCollection | null | undefined)[]): BBoxList | null {
  const b = emptyBounds();
  for (const fc of collections) {
    if (!fc || !Array.isArray(fc.features)) continue;
    for (const f of fc.features) extendWithGeometry(b, f?.geometry ?? null);
  }
  return finish(b);
}

/** The centre `[lon, lat]` of a bbox. */
export function boundsCenter(b: BBoxList): [number, number] {
  return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
}

/** Categorical zone legend (colour + hatch + label). */
export function zoneLegend(id: string, options: { title?: string; layerId?: string; zones?: readonly ZoneKind[]; note?: string } = {}): LegendSpec {
  const zones = options.zones ?? (["green", "yellow", "red"] as const);
  return {
    id,
    title: options.title ?? "Zones",
    kind: "categorical",
    items: zones.map((z) => ({ label: ZONE_LABEL[z], color: ZONE_HEX[z], zone: z })),
    layerId: options.layerId,
    note: options.note,
  };
}

/** Continuous ramp legend with `count` evenly spaced numeric breaks from min to max. */
export function rampLegend(o: {
  id: string;
  title: string;
  stops: readonly string[];
  min: number;
  max: number;
  count?: number;
  unit?: string;
  layerId?: string;
  formatBreak?: (value: number) => string;
  note?: string;
}): LegendSpec {
  return {
    id: o.id,
    title: o.title,
    kind: "ramp",
    stops: o.stops,
    breaks: legendBreaks(o.min, o.max, o.count ?? 5),
    unit: o.unit,
    layerId: o.layerId,
    formatBreak: o.formatBreak,
    note: o.note,
  };
}
