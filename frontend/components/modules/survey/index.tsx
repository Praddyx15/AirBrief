"use client";

/**
 * Survey module: effort estimate for a photogrammetry survey (GSD and its range over relief,
 * footprint, line spacing, photos, flight time, battery-sized work blocks, transit legs, GCPs and
 * storage) or a spraying job (spray mix, tank loads, sorties, work rates), over an area or along a
 * corridor centreline. It is an estimate only: no flight plan or waypoints are generated (mission
 * planning belongs to UAOP), and the lines are a visual aid.
 */

import type { PickingInfo } from "@deck.gl/core";
import { categorical } from "@/lib/colors";
import { formatDuration } from "@/lib/format";
import {
  MODULE_META,
  type LegendSpec,
  type ModuleDefinition,
  type ModuleLayerContext,
  type ModuleLayerOutput,
} from "@/lib/modules";
import type { AnalysisInfo, FeatureCollection, Geometry, SurveyResult, TakeoffPoint } from "@/lib/types";
import { boundsCenter, derived, geoJsonLayer, geometryBounds, mapPalette, textLabelLayer } from "@/components/map/layers/common";
import { withBatch } from "../batch";
import { resultLayer } from "../shared";
import { ESTIMATE_NOTE, SPRAY_NOTE, blockColor } from "./model";
import { SurveyPanel } from "./panel";
import { SurveyResults } from "./results";

const LAYERS = [
  { id: "survey:input", label: "Survey area", resultLayer: "input" },
  { id: "survey:blocks", label: "Battery blocks", resultLayer: "blocks" },
  { id: "survey:lines", label: "Coverage pattern (visual)", resultLayer: "lines" },
  { id: "survey:takeoff", label: "Take-off", resultLayer: "takeoff" },
] as const;

type AnyFc = FeatureCollection<Geometry | null, Record<string, unknown>>;

function buildSurveyLayers(analysis: AnalysisInfo, ctx: ModuleLayerContext): ModuleLayerOutput {
  const result = analysis.result as SurveyResult | null;
  if (!result) return { layers: [], legends: [] };
  const palette = mapPalette();
  const highlight = ctx.resultHighlight;
  const spraying = result.mode === "spraying";
  const noun = spraying ? "Sortie" : "Battery";
  // `label` names the block as the table does ("Sortie 3" when spraying), for the map tooltip.
  type BlockFeature = { type: "Feature"; geometry: Geometry; properties: { index: number; label: string } };
  const blockFeatures = derived(result.blocks, "survey:block-features", (): BlockFeature[] =>
    result.blocks.map((b) => ({
      type: "Feature",
      geometry: b.polygon as unknown as Geometry,
      properties: { index: b.index, label: `${spraying ? "Sortie" : "Battery block"} ${b.index}` },
    })),
  );
  const labelData = derived(result.blocks, "survey:block-labels", () =>
    result.blocks
      .map((b) => {
        const bounds = geometryBounds(b.polygon as unknown as Geometry);
        return bounds ? { index: b.index, position: boundsCenter(bounds) } : null;
      })
      .filter((d): d is { index: number; position: [number, number] } => d !== null),
  );

  const layers = [
    geoJsonLayer({
      id: "survey:input",
      data: result.layers.input as AnyFc | undefined,
      fillColor: [palette.accent[0], palette.accent[1], palette.accent[2], 20],
      lineColor: palette.accent,
      lineWidth: 2,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer<{ index: number }>({
      id: "survey:blocks",
      data: blockFeatures,
      fillColor: (p) => categorical(p.index - 1, highlight === `block-${p.index}` ? 120 : 60),
      lineColor: (p) => categorical(p.index - 1, 255),
      lineWidth: (p) => (highlight === `block-${p.index}` ? 3 : 1.5),
      updateTriggers: [highlight],
    }),
    textLabelLayer<{ index: number; position: [number, number] }>({
      id: "survey:blocks/labels",
      data: labelData,
      getPosition: (d) => d.position,
      getText: (d) => `${noun} ${d.index}`,
      size: 11,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "survey:lines",
      data: result.layers.lines as AnyFc | undefined,
      filled: false,
      lineColor: [palette.text[0], palette.text[1], palette.text[2], 140],
      lineWidth: 1,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
  ];
  const legends: LegendSpec[] = [
    {
      id: "survey:blocks",
      title: spraying ? "Sorties" : "Battery blocks",
      kind: "categorical",
      layerId: "survey:blocks",
      items: result.blocks.slice(0, 8).map((b) => ({
        label: `${noun} ${b.index}`,
        color: blockColor(b.index),
        detail: formatDuration(b.minutes * 60),
      })),
      note:
        result.blocks.length > 8
          ? `${result.blocks.length - 8} more in the table. ${spraying ? SPRAY_NOTE : ESTIMATE_NOTE}`
          : spraying
            ? SPRAY_NOTE
            : ESTIMATE_NOTE,
    },
  ];

  const takeoff = resultLayer(result.layers, "takeoff");
  if (takeoff.features.length > 0 && result.takeoff) {
    const t = result.takeoff;
    layers.push(
      geoJsonLayer({
        id: "survey:takeoff",
        data: takeoff as AnyFc,
        fillColor: palette.accent,
        lineColor: palette.text,
        lineWidth: 2,
        pointRadius: 7,
        updateTriggers: [ctx.theme],
      }),
      textLabelLayer<TakeoffPoint>({
        id: "survey:takeoff/label",
        data: derived(t, "survey:takeoff-label", () => [t]),
        getPosition: (d) => [d.lon, d.lat],
        getText: () => "Take-off",
        pixelOffset: [0, -18],
        size: 11,
        updateTriggers: [ctx.theme],
      }),
    );
    legends.push({
      id: "survey:takeoff",
      title: "Take-off",
      kind: "categorical",
      layerId: "survey:takeoff",
      items: [{ label: result.transit_km != null ? "Take-off (transit legs start here)" : "Take-off", color: "var(--accent)", shape: "point" }],
    });
  }
  return { layers, legends };
}

function describeSurveyPick(info: PickingInfo): string | null {
  const id = info.layer?.id ?? "";
  if (id.startsWith("survey:takeoff")) return "Take-off";
  if (!id.startsWith("survey:blocks") || !info.object) return null;
  const props = (info.object as { properties?: { index?: number; label?: string } }).properties;
  if (typeof props?.label === "string") return props.label;
  return typeof props?.index === "number" ? `Battery block ${props.index}` : null;
}

export const definition: ModuleDefinition = withBatch({
  ...MODULE_META.survey,
  Panel: SurveyPanel,
  Results: SurveyResults,
  layers: LAYERS,
  buildLayers: buildSurveyLayers,
  describePick: describeSurveyPick,
});

export default definition;
