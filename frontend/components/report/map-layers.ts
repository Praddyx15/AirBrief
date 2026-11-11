/**
 * Layers of the print-friendly SVG maps (`geometry-svg.tsx`): the report's overview map of one
 * dataset and analysis (`buildMapLayers`), and the operation evidence brief's map of several
 * analyses (`briefMapLayers`). Colours and names are the workspace map's for each module.
 */

import { batchVerdictColors, isBatchResult } from "@/lib/batch";
import { CATEGORICAL } from "@/lib/colors";
import type { AnalysisInfo, AnalysisKind, BatchResult, DatasetFeatureCollection } from "@/lib/types";
import type { FeatureStyle, GeometryLayer } from "./geometry-svg";

/** Layers with more features than this are listed instead of drawn (the exports hold them). */
export const MAX_LAYER_FEATURES = 5000;

/**
 * Drawing order: the dataset (muted context under an analysis), fills, highlights, lines, then
 * the analysed area's boundary and points on top, so no layer in the legend is hidden.
 */
const ORDER = { dataset: -1, fill: 0, halo: 1, line: 2, boundary: 4, point: 5 } as const;

type LayerStyle = Omit<GeometryLayer, "id" | "features"> & { order: number };

export function humanizeLayer(key: string): string {
  const s = key.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function obstacleStyle(conflictLabel: string) {
  return (p: Record<string, unknown>): FeatureStyle =>
    p.conflict === true ? { label: conflictLabel, color: "var(--danger)" } : { label: "Obstacle, clear", color: "var(--text-muted)" };
}

/** Survey battery block number (1-based, as in the blocks table and the workspace map). */
function blockNumber(p: Record<string, unknown>): number | null {
  return typeof p.block === "number" && Number.isFinite(p.block) ? p.block : null;
}

/** Same colour per block as the workspace map and table (block 1 takes the first colour). */
function blockColor(block: number): string {
  const n = CATEGORICAL.length;
  return CATEGORICAL[(((block - 1) % n) + n) % n] as string;
}

function textProperty(key: string) {
  return (p: Record<string, unknown>): string | null => (typeof p[key] === "string" && p[key] ? (p[key] as string) : null);
}

const INPUT_STYLE = { color: "var(--accent)", outline: true, strokeWidth: 2, order: ORDER.boundary } as const;

export const RESULT_LAYER_STYLE: Readonly<Partial<Record<AnalysisKind, Readonly<Record<string, LayerStyle>>>>> = {
  airspace: {
    input: { label: "Checked area", ...INPUT_STYLE },
    zones: { label: "Zones", color: "var(--text-muted)", order: ORDER.fill },
    obstacles: {
      label: "Obstacles",
      color: "var(--text-muted)",
      order: ORDER.point,
      styleFeature: obstacleStyle("Obstacle in conflict with the planned height"),
    },
  },
  ground_risk: {
    footprint: { label: "Footprint (with buffer)", color: "var(--text)", outline: true, dashed: true, order: ORDER.fill },
    contingency_volume: { label: "Contingency volume", color: "var(--warn)", outline: true, dashed: true, order: ORDER.fill },
    flight_geography: { label: "Flight geography", color: "var(--accent)", order: ORDER.fill },
    assemblies: { label: "Outdoor assemblies within 1 km", color: "var(--danger)", order: ORDER.fill, labelFeature: textProperty("name") },
    input: { label: "Flight area", ...INPUT_STYLE },
  },
  evtol_siting: {
    input: { label: "Study area", ...INPUT_STYLE, dashed: true },
    candidates: {
      label: "Candidate sites",
      color: "var(--accent)",
      order: ORDER.point,
      labelFeature: (p) => (typeof p.rank === "number" ? `#${p.rank}` : null),
    },
  },
  evtol_corridor: {
    corridor: { label: "Corridor edge", color: "var(--text)", outline: true, dashed: true, order: ORDER.fill },
    route: { label: "Route", color: "var(--accent)", strokeWidth: 3, order: ORDER.line },
    endpoints: { label: "Origin and destination", color: "var(--accent)", order: ORDER.point, labelFeature: textProperty("name") },
    obstacles: { label: "Obstacles", color: "var(--text-muted)", order: ORDER.point, styleFeature: obstacleStyle("Obstacle in corridor") },
  },
  show: {
    show_box: { label: "Show box", color: "var(--accent)", strokeWidth: 2, order: ORDER.fill },
    audience_buffer: {
      label: "Audience buffer",
      color: "var(--danger)",
      order: ORDER.fill,
      styleFeature: (p) => ({ label: p.role === "audience" ? "Audience area" : "Audience buffer", zone: "red" }),
    },
    paths: { label: "Flight paths", color: "var(--text-muted)", strokeWidth: 1, order: ORDER.line },
  },
  survey: {
    input: { label: "Survey area", ...INPUT_STYLE },
    blocks: {
      label: "Battery blocks",
      color: CATEGORICAL[0] as string,
      order: ORDER.fill,
      styleFeature: (p) => {
        const n = blockNumber(p);
        return n === null ? null : { label: `Battery ${n}`, color: blockColor(n) };
      },
      labelFeature: (p) => {
        const n = blockNumber(p);
        return n === null ? null : `Battery ${n}`;
      },
    },
    lines: { label: "Coverage pattern (visual)", color: "var(--text-muted)", strokeWidth: 0.75, order: ORDER.line },
  },
  flight_audit: {
    training_box: { label: "Training area", color: "var(--accent)", outline: true, dashed: true, order: ORDER.fill },
    path: { label: "Flight path", color: "var(--ok)", strokeWidth: 2.5, order: ORDER.line },
    exceedances: { label: "Exceedances", color: "var(--danger)", halo: true, order: ORDER.halo },
    area_exits: { label: "Outside the training area", color: "var(--warn)", halo: true, order: ORDER.halo },
    distance_exceedances: { label: "Beyond the distance limit", color: "var(--danger)", halo: true, order: ORDER.halo },
    home: { label: "Take-off point", color: "var(--ok)", order: ORDER.point, labelFeature: () => "Take-off" },
  },
  environment: {
    emi_search: { label: "Interference search distance", color: "var(--text-muted)", outline: true, dashed: true, order: ORDER.fill },
    input: { label: "Analysed area", ...INPUT_STYLE },
    emi: {
      label: "Power lines and masts (proximity evidence)",
      color: "var(--warn)",
      strokeWidth: 2.5,
      order: ORDER.line,
      labelFeature: textProperty("label"),
    },
    point: { label: "Evaluation point (sun, magnetic field, wind)", color: "var(--accent)", order: ORDER.point },
  },
  line_of_sight: {
    input: { label: "Line-of-sight area", ...INPUT_STYLE, dashed: true },
    radio_paths: { label: "Radio paths", color: "var(--text-muted)", dashed: true, order: ORDER.line },
    observers: { label: "Pilot, observers and ground station", color: "var(--accent)", order: ORDER.point, labelFeature: textProperty("name") },
  },
};

/** A one-result-per-feature run: each feature in its result's colour, as on the workspace map. */
function batchLayerStyle(result: BatchResult): Readonly<Record<string, LayerStyle>> {
  const colors = batchVerdictColors(result);
  return {
    features: {
      label: "Result per feature",
      color: "var(--text-muted)",
      order: ORDER.fill,
      styleFeature: (p) =>
        p.status === "ok" && typeof p.verdict === "string"
          ? { label: typeof p.label === "string" ? p.label : p.verdict, color: colors.get(p.verdict) ?? "var(--text-muted)" }
          : { label: "Not assessed", color: "var(--text-muted)" },
    },
  };
}

/** Overview-map layers of the report, in drawing order, plus the names of layers too large to draw. */
export function buildMapLayers(
  features: DatasetFeatureCollection | null,
  analysis: AnalysisInfo | null,
): { layers: GeometryLayer[]; skipped: string[] } {
  const ordered: { order: number; layer: GeometryLayer }[] = [];
  const skipped: string[] = [];
  const result = analysis?.status === "COMPLETED" ? analysis.result : null;
  if (features) {
    ordered.push({
      order: result ? ORDER.dataset : ORDER.fill,
      layer: {
        id: "dataset",
        label: "Dataset features",
        features: features.features,
        // Under an analysis the dataset is context: muted, with the analysis input drawn above it.
        color: result ? "var(--text-muted)" : "var(--accent)",
      },
    });
  }
  if (analysis && result) {
    const styles = isBatchResult(result) ? batchLayerStyle(result) : (RESULT_LAYER_STYLE[analysis.kind] ?? {});
    Object.entries(result.layers).forEach(([key, fc], i) => {
      if (!fc || !Array.isArray(fc.features) || fc.features.length === 0) return;
      const style = styles[key];
      const label = style?.label ?? humanizeLayer(key);
      if (fc.features.length > MAX_LAYER_FEATURES) {
        skipped.push(label);
        return;
      }
      const { order, ...rest } = style ?? {
        label,
        color: CATEGORICAL[(i + 1) % CATEGORICAL.length] as string,
        order: ORDER.line,
      };
      ordered.push({ order, layer: { ...rest, id: `analysis-${key}`, features: fc.features } });
    });
  }
  // Array.prototype.sort is stable: equal orders keep the API's layer order.
  ordered.sort((a, b) => a.order - b.order);
  return { layers: ordered.map((o) => o.layer), skipped };
}

/**
 * The layers each kind draws on the brief map: the operation and its key evidence. Grids, density
 * cells, aerodrome rings, survey lines and flight paths of shows stay out, so the map shows the
 * operation rather than the region around it (the bundle's GeoJSON and KML hold every layer).
 * The same choice as `airbrief_core/brief_html.py` (the bundle's `brief.html`).
 */
const BRIEF_LAYERS: Readonly<Record<AnalysisKind, readonly string[]>> = {
  airspace: ["input", "obstacles"],
  ground_risk: ["footprint", "contingency_volume", "flight_geography", "assemblies", "input"],
  survey: ["blocks", "input"],
  evtol_siting: ["input", "candidates"],
  evtol_corridor: ["corridor", "route", "endpoints"],
  show: ["show_box", "audience_buffer"],
  flight_audit: ["training_box", "path", "home"],
  environment: ["input", "emi"],
  line_of_sight: ["input", "radio_paths", "observers"],
};

/** Features drawn per brief layer (the bundle's GeoJSON and KML hold every feature). */
export const MAX_BRIEF_LAYER_FEATURES = 2000;

/**
 * The brief map: each analysis' key layers (each with its legend entry) and the pilot / take-off
 * position. Areas first, then lines, then points.
 */
export function briefMapLayers(analyses: readonly AnalysisInfo[], pilot: readonly number[] | null): GeometryLayer[] {
  const ordered: { order: number; layer: GeometryLayer }[] = [];
  for (const analysis of analyses) {
    if (analysis.status !== "COMPLETED" || !analysis.result) continue;
    const layers = analysis.result.layers;
    const batch = isBatchResult(analysis.result) ? analysis.result : null;
    const styles = batch ? batchLayerStyle(batch) : (RESULT_LAYER_STYLE[analysis.kind] ?? {});
    for (const key of batch ? ["features"] : (BRIEF_LAYERS[analysis.kind] ?? [])) {
      const fc = layers[key];
      if (!fc || !Array.isArray(fc.features) || fc.features.length === 0) continue;
      const style = styles[key];
      const { order, ...rest } = style ?? { label: humanizeLayer(key), color: "var(--text-muted)", order: ORDER.line };
      ordered.push({
        order,
        layer: { ...rest, id: `${analysis.id}-${key}`, features: fc.features.slice(0, MAX_BRIEF_LAYER_FEATURES) },
      });
    }
  }
  if (pilot && pilot.length >= 2 && Number.isFinite(pilot[0]) && Number.isFinite(pilot[1])) {
    ordered.push({
      order: ORDER.point + 1,
      layer: {
        id: "operation-pilot",
        label: "Pilot / take-off position",
        color: "var(--text)",
        features: [{ type: "Feature", geometry: { type: "Point", coordinates: [pilot[0] as number, pilot[1] as number] }, properties: {} }],
        labelFeature: () => "Pilot",
      },
    });
  }
  ordered.sort((a, b) => a.order - b.order);
  return ordered.map((o) => o.layer);
}
