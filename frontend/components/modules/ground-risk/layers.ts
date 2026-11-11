/**
 * Ground-risk map layers. SORA 2.5 operational-volume results are drawn as nested polygons:
 * the adjacent area as a light wash, the square density grid, the ground risk buffer hatched,
 * the contingency volume dashed and the flight geography solid, with hotspots and outdoor
 * assemblies on top. Results of the earlier footprint method keep their H3 density cells and
 * dashed footprint. Every layer has a legend entry (never colour alone).
 */

import type { Layer, PickingInfo } from "@deck.gl/core";
import { FillStyleExtension, type FillStyleExtensionProps } from "@deck.gl/extensions";
import { GeoJsonLayer, type GeoJsonLayerProps } from "@deck.gl/layers";
import { VIRIDIS_STOPS, cssVarRef, viridis, type HatchAtlas, type RGBA } from "@/lib/colors";
import { formatLength, formatNumber } from "@/lib/format";
import { ASSEMBLY_BAND_LABEL } from "@/lib/labels";
import type { LegendSpec, ModuleLayerContext, ModuleLayerOutput } from "@/lib/modules";
import type { AnalysisInfo, AssemblyHit, DensityCell, FeatureCollection, Geometry, GridDensityCell, GroundRiskResult } from "@/lib/types";
import { derived, drawableFeatures, geoJsonLayer, h3Layer, mapPalette, pointLayer, textLabelLayer } from "@/components/map/layers/common";
import { currentUnits, resultLayer } from "../shared";
import { h3Collection } from "../h3-layers";
import { gridSquares, type GridSquareProps } from "./volume";

export const LAYERS = [
  { id: "ground-risk:adjacent", label: "Adjacent area", resultLayer: "adjacent_area" },
  { id: "ground-risk:density", label: "Population density", resultLayer: "density" },
  { id: "ground-risk:buffer", label: "Ground risk buffer", resultLayer: "ground_risk_buffer" },
  { id: "ground-risk:contingency", label: "Contingency volume", resultLayer: "contingency_volume" },
  { id: "ground-risk:footprint", label: "Footprint", resultLayer: "footprint" },
  { id: "ground-risk:input", label: "Flight geography", resultLayer: "input" },
  { id: "ground-risk:hotspots", label: "Hotspots", resultLayer: "hotspots" },
  { id: "ground-risk:assemblies", label: "Outdoor assemblies", resultLayer: "assemblies" },
] as const;

type AnyCollection = FeatureCollection<Geometry | null, Record<string, unknown>>;

/** A hotspot or density cell of either method, for tables and labels. */
export interface DensityRow {
  id: string;
  lon: number;
  lat: number;
  density_per_km2: number;
  band: string;
}

const fromH3 = (c: DensityCell): DensityRow => ({ id: c.h3, lon: c.lon, lat: c.lat, density_per_km2: c.density_per_km2, band: c.band });
const fromGrid = (c: GridDensityCell): DensityRow => ({ id: c.id, lon: c.lon, lat: c.lat, density_per_km2: c.density_per_km2, band: c.band });

/** The hotspots of a result (grid squares for the operational volume, H3 cells otherwise). */
export function hotspotRows(result: GroundRiskResult): DensityRow[] {
  const grid = result.density_grid;
  if (grid) return derived(grid.hotspots, "ground-risk:hotspot-rows", () => grid.hotspots.map(fromGrid));
  return derived(result.hotspots, "ground-risk:hotspot-rows", () => result.hotspots.map(fromH3));
}

/** Density cells and hotspots as polygons ("density", "hotspots", as in the GeoJSON export). */
export function groundRiskDerivedLayers(analysis: AnalysisInfo): Record<string, FeatureCollection> {
  const result = analysis.result as GroundRiskResult | null;
  if (!result) return {};
  const grid = result.density_grid;
  if (grid) {
    return {
      density: derived(grid.cells, "ground-risk:density-squares", () => gridSquares(grid.cells, grid.grid_m)),
      hotspots: derived(grid.hotspots, "ground-risk:hotspot-squares", () => gridSquares(grid.hotspots, grid.grid_m, true)),
    };
  }
  return {
    density: derived(result.cells, "ground-risk:density-table", () =>
      h3Collection(result.cells, (c) => ({ density_per_km2: c.density_per_km2, band: c.band })),
    ),
    hotspots: derived(result.hotspots, "ground-risk:hotspots-table", () =>
      h3Collection(result.hotspots, (c, i) => ({ rank: i + 1, density_per_km2: c.density_per_km2, band: c.band })),
    ),
  };
}

/** log10(1 + d) normalised by the maximum, so city cores do not wash out suburbs. */
function logScale(maxDensity: number) {
  const top = Math.log10(1 + Math.max(1, maxDensity));
  return (d: number) => Math.min(1, Math.max(0, Math.log10(1 + Math.max(0, d)) / top));
}

function logBreaks(maxDensity: number, count = 5): number[] {
  const top = Math.log10(1 + Math.max(1, maxDensity));
  return Array.from({ length: count }, (_, i) => Math.round(10 ** ((top * i) / (count - 1)) - 1));
}

const withA = (c: RGBA, a: number): RGBA => [c[0], c[1], c[2], a];

/** A polygon ring drawn with a translucent fill, an outline and a diagonal hatch on top. */
function hatchedLayers(id: string, data: AnyCollection, color: RGBA, hatch: HatchAtlas | null, triggers: unknown[]): Layer[] {
  const features = drawableFeatures(data) as unknown as GeoJsonLayerProps<Record<string, unknown>>["data"];
  const base = new GeoJsonLayer<Record<string, unknown>>({
    id,
    data: features,
    filled: true,
    stroked: true,
    pickable: true,
    lineWidthUnits: "pixels",
    getFillColor: withA(color, 28),
    getLineColor: withA(color, 200),
    getLineWidth: 1,
    updateTriggers: { getFillColor: triggers, getLineColor: triggers },
  });
  if (!hatch) return [base];
  const pattern = new GeoJsonLayer<Record<string, unknown>, FillStyleExtensionProps>({
    id: `${id}/hatch`,
    data: features,
    filled: true,
    stroked: false,
    pickable: false,
    getFillColor: withA(color, 150),
    extensions: [new FillStyleExtension({ pattern: true })],
    fillPatternAtlas: hatch.atlas,
    fillPatternMapping: hatch.mapping,
    fillPatternMask: true,
    getFillPattern: () => "diagonal",
    getFillPatternScale: 1,
    getFillPatternOffset: [0, 0],
    updateTriggers: { getFillColor: triggers },
  });
  return [base, pattern];
}

function volumeLayers(result: GroundRiskResult, ctx: ModuleLayerContext): Layer[] {
  const palette = mapPalette();
  const triggers = [ctx.theme];
  const grid = result.density_grid;
  const scale = logScale(result.max_density_per_km2);
  const squares = grid ? derived(grid.cells, "ground-risk:density-squares", () => gridSquares(grid.cells, grid.grid_m)) : null;
  const layer = (key: string) => resultLayer(result.layers, key) as AnyCollection;
  return [
    geoJsonLayer({
      id: "ground-risk:adjacent",
      data: layer("adjacent_area"),
      fillColor: withA(palette.muted, 26),
      lineColor: withA(palette.muted, 170),
      lineWidth: 1,
      updateTriggers: triggers,
    }),
    ...(squares
      ? [
          geoJsonLayer<GridSquareProps>({
            id: "ground-risk:density",
            data: squares,
            fillColor: (p) => viridis(scale(p.density_per_km2), 175),
            stroked: false,
            opacity: 0.8,
            updateTriggers: triggers,
          }),
        ]
      : []),
    ...hatchedLayers("ground-risk:buffer", layer("ground_risk_buffer"), palette.warn, ctx.hatch, triggers),
    geoJsonLayer({
      id: "ground-risk:contingency",
      data: layer("contingency_volume"),
      filled: false,
      lineColor: palette.accentText,
      lineWidth: 2,
      dashed: true,
      pickable: false,
      updateTriggers: triggers,
    }),
    geoJsonLayer({
      id: "ground-risk:footprint",
      data: layer("footprint"),
      filled: false,
      lineColor: withA(palette.text, 200),
      lineWidth: 1.25,
      pickable: false,
      updateTriggers: triggers,
    }),
    geoJsonLayer({
      id: "ground-risk:input",
      data: layer("flight_geography"),
      fillColor: withA(palette.accent, 70),
      lineColor: palette.accent,
      lineWidth: 2,
      pointRadius: 5,
      // Not pickable: dataset features under it stay hoverable and selectable.
      pickable: false,
      updateTriggers: triggers,
    }),
  ];
}

function footprintLayers(result: GroundRiskResult, ctx: ModuleLayerContext): Layer[] {
  const palette = mapPalette();
  const scale = logScale(result.max_density_per_km2);
  return [
    h3Layer<DensityCell>({
      id: "ground-risk:density",
      data: result.cells,
      getHexagon: (c) => c.h3,
      getFillColor: (c) => viridis(scale(c.density_per_km2), 190),
      opacity: 0.75,
    }),
    geoJsonLayer({
      id: "ground-risk:footprint",
      data: resultLayer(result.layers, "footprint") as AnyCollection,
      filled: false,
      lineColor: palette.text,
      lineWidth: 2,
      dashed: true,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "ground-risk:input",
      data: resultLayer(result.layers, "input") as AnyCollection,
      fillColor: withA(palette.accent, 30),
      lineColor: palette.accent,
      lineWidth: 1.5,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
  ];
}

function assemblyLayers(result: GroundRiskResult, ctx: ModuleLayerContext): Layer[] {
  const hits = result.assemblies ?? [];
  if (hits.length === 0) return [];
  const palette = mapPalette();
  const triggers = [ctx.theme];
  const areas = derived(resultLayer(result.layers, "assemblies"), "ground-risk:assembly-areas", () => ({
    type: "FeatureCollection" as const,
    features: resultLayer(result.layers, "assemblies").features.filter(
      (f) => f.geometry?.type === "Polygon" || f.geometry?.type === "MultiPolygon",
    ),
  }));
  return [
    geoJsonLayer({
      id: "ground-risk:assemblies/areas",
      data: areas as AnyCollection,
      fillColor: withA(palette.danger, 40),
      lineColor: palette.danger,
      lineWidth: 1.5,
      pickable: false,
      updateTriggers: triggers,
    }),
    pointLayer<AssemblyHit>({
      id: "ground-risk:assemblies",
      data: hits,
      getPosition: (a) => [a.lon, a.lat],
      radius: 7,
      fillColor: palette.danger,
      lineColor: palette.bg,
      lineWidth: 2,
      updateTriggers: triggers,
    }),
    textLabelLayer<AssemblyHit>({
      id: "ground-risk:assemblies/labels",
      data: hits.slice(0, 20),
      getPosition: (a) => [a.lon, a.lat],
      getText: (a) => a.name,
      pixelOffset: [0, -18],
      size: 11,
      updateTriggers: triggers,
    }),
  ];
}

export function buildGroundRiskLayers(analysis: AnalysisInfo, ctx: ModuleLayerContext): ModuleLayerOutput {
  const result = analysis.result as GroundRiskResult | null;
  if (!result) return { layers: [], legends: [] };
  const palette = mapPalette();
  const hotspots = hotspotRows(result);
  const layers: Layer[] = [
    ...(result.volume ? volumeLayers(result, ctx) : footprintLayers(result, ctx)),
    pointLayer<DensityRow>({
      id: "ground-risk:hotspots",
      data: hotspots,
      getPosition: (c) => [c.lon, c.lat],
      radius: 6,
      fillColor: palette.warn,
      lineColor: palette.text,
      updateTriggers: [ctx.theme],
    }),
    textLabelLayer<DensityRow>({
      id: "ground-risk:hotspots/labels",
      data: derived(hotspots, "ground-risk:top5", () => hotspots.slice(0, 5)),
      getPosition: (c) => [c.lon, c.lat],
      getText: (c) => `${formatNumber(c.density_per_km2, { maximumFractionDigits: 0 })}/km²`,
      pixelOffset: [0, -16],
      size: 11,
      updateTriggers: [ctx.theme],
    }),
    ...assemblyLayers(result, ctx),
  ];

  const legends: LegendSpec[] = [
    {
      id: "ground-risk:density",
      title: "Population density",
      kind: "ramp",
      stops: VIRIDIS_STOPS,
      breaks: logBreaks(result.max_density_per_km2),
      unit: "people/km²",
      layerId: "ground-risk:density",
      note: result.volume
        ? `Log scale. Squares of ${formatLength(result.volume.grid_m, ctx.units)} averaged over the footprint.`
        : "Log scale. Dashed outline: footprint including the ground-risk buffer.",
    },
  ];
  if (result.volume) {
    legends.push({
      id: "ground-risk:volume",
      title: "Operational volume (SORA 2.5)",
      kind: "categorical",
      items: [
        { label: "Flight geography", color: cssVarRef("accent"), shape: "fill" },
        { label: "Contingency volume", color: cssVarRef("accent-text"), shape: "dashed-line" },
        { label: "Ground risk buffer", color: cssVarRef("warn"), shape: "hatch" },
        { label: "iGRC footprint edge (FG + CV + GRB)", color: cssVarRef("text"), shape: "line" },
        { label: "Adjacent area", color: "rgb(var(--text-muted-rgb) / 0.45)", shape: "fill" },
        { label: "Density hotspot", color: cssVarRef("warn"), shape: "point" },
        ...((result.assemblies ?? []).length > 0
          ? [{ label: "Outdoor assembly (within 1 km)", color: cssVarRef("danger"), shape: "point" as const }]
          : []),
      ],
      note: "SORA Step 2 / Step 8 evidence — not a GRC, ARC or SAIL.",
    });
  }
  return { layers, legends };
}

export function describeGroundRiskPick(info: PickingInfo): string | null {
  const id = info.layer?.id ?? "";
  const object = info.object as Record<string, unknown> | undefined;
  if (!object) return null;
  if (id.startsWith("ground-risk:assemblies")) {
    const a = object as unknown as AssemblyHit;
    const where = a.distance_m > 0 ? `${formatLength(a.distance_m, currentUnits())} from the volume` : "inside the volume";
    return `${a.name} · ${ASSEMBLY_BAND_LABEL[a.band] ?? a.band_label} · ${where}`;
  }
  if (id.startsWith("ground-risk:hotspots")) {
    const c = object as unknown as DensityRow;
    return `${formatNumber(c.density_per_km2, { maximumFractionDigits: 0 })} people/km² · ${c.band}`;
  }
  const props = (object.properties ?? object) as Record<string, unknown>;
  if (id.startsWith("ground-risk:density")) {
    const d = Number(props.density_per_km2);
    return Number.isFinite(d) ? `${formatNumber(d, { maximumFractionDigits: 0 })} people/km² · ${String(props.band ?? "")}` : null;
  }
  if (id.startsWith("ground-risk:buffer")) {
    const m = Number(props.grb_m);
    return Number.isFinite(m) ? `Ground risk buffer · ${formatLength(m, currentUnits())} wide` : "Ground risk buffer";
  }
  if (id.startsWith("ground-risk:adjacent")) {
    const d = Number(props.mean_density_per_km2);
    return `Adjacent area${Number.isFinite(d) && props.mean_density_per_km2 !== null ? ` · average ${formatNumber(d, { maximumFractionDigits: 0 })} people/km²` : ""}`;
  }
  return null;
}
