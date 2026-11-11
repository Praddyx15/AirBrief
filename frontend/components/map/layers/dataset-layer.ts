/**
 * deck.gl layers for the uploaded dataset (`GET /api/files/{id}/features/`).
 *
 * - Base: polygons, lines and points in the accent colour; features that could not be
 *   measured get a warning-coloured outline.
 * - Hover: brighter fill and thicker outline.
 * - Selection (URL `f`): thick outline in the text colour plus a name label, so selection
 *   never relies on colour alone.
 * - Keyboard focus (N / Shift+N): dashed outline.
 */

import type { Layer, PickingInfo } from "@deck.gl/core";
import { cssVarRef, withAlpha, type RGBA } from "@/lib/colors";
import type { LegendSpec } from "@/lib/modules";
import type { DatasetFeatureCollection, DatasetFeatureProperties, Feature, Geometry } from "@/lib/types";
import { boundsCenter, drawableFeatures, geoJsonLayer, geometryBounds, mapPalette, textLabelLayer } from "./common";

/** Layer id of the dataset features (also the Layers panel toggle id). */
export const DATASET_LAYER_ID = "dataset:features";

export interface DatasetLayerOptions {
  features: DatasetFeatureCollection | null | undefined;
  selected: readonly number[];
  hovered: number | null;
  focused: number | null;
  theme: "dark" | "light";
  visible?: boolean;
}

type DatasetFeature = Feature<Geometry, DatasetFeatureProperties>;

function isMeasuredOk(p: DatasetFeatureProperties): boolean {
  return p._status === "measured" || p._status === "not_applicable";
}

export function datasetFeatureLabel(p: Pick<DatasetFeatureProperties, "_name" | "_index">): string {
  const name = typeof p._name === "string" ? p._name.trim() : "";
  return name || `Feature ${p._index}`;
}

/** Layers for the dataset, ids `dataset:features`, `dataset:features/focus`, `dataset:features/labels`. */
export function datasetLayers(o: DatasetLayerOptions): Layer[] {
  const features = drawableFeatures(o.features ?? null) as DatasetFeature[];
  if (features.length === 0) return [];
  const palette = mapPalette();
  const selected = new Set(o.selected);
  const visible = o.visible ?? true;
  const triggers: unknown[] = [o.selected.join(","), o.hovered, o.focused, o.theme];

  const fillFor = (p: DatasetFeatureProperties): RGBA => {
    if (selected.has(p._index)) return withAlpha(palette.accent, 115);
    if (p._index === o.hovered) return withAlpha(palette.accent, 95);
    return withAlpha(palette.accent, 45);
  };
  const lineFor = (p: DatasetFeatureProperties): RGBA => {
    if (selected.has(p._index)) return palette.text;
    if (p._index === o.hovered) return palette.accentText;
    return isMeasuredOk(p) ? withAlpha(palette.accent, 235) : palette.warn;
  };
  const widthFor = (p: DatasetFeatureProperties): number => {
    if (selected.has(p._index)) return 3.5;
    if (p._index === o.hovered) return 3;
    return 1.5;
  };
  const radiusFor = (p: DatasetFeatureProperties): number => {
    if (selected.has(p._index)) return 8;
    if (p._index === o.hovered) return 7;
    return 5;
  };

  const layers: Layer[] = [
    geoJsonLayer<DatasetFeatureProperties>({
      id: DATASET_LAYER_ID,
      data: features,
      fillColor: fillFor,
      lineColor: lineFor,
      lineWidth: widthFor,
      pointRadius: radiusFor,
      pickable: true,
      visible,
      updateTriggers: triggers,
    }),
  ];

  if (o.focused !== null) {
    const focusedFeature = features.find((f) => f.properties._index === o.focused);
    if (focusedFeature) {
      layers.push(
        geoJsonLayer<DatasetFeatureProperties>({
          id: `${DATASET_LAYER_ID}/focus`,
          data: [focusedFeature],
          filled: false,
          lineColor: palette.warn,
          lineWidth: 3,
          pointRadius: 11,
          dashed: true,
          dashArray: [3, 2],
          pickable: false,
          visible,
          updateTriggers: triggers,
        }),
      );
    }
  }

  if (selected.size > 0) {
    const labels = features
      .filter((f) => selected.has(f.properties._index))
      .map((f) => {
        const b = geometryBounds(f.geometry);
        return b ? { position: boundsCenter(b), text: datasetFeatureLabel(f.properties) } : null;
      })
      .filter((l): l is { position: [number, number]; text: string } => l !== null)
      .slice(0, 50);
    layers.push(
      textLabelLayer({
        id: `${DATASET_LAYER_ID}/labels`,
        data: labels,
        getPosition: (d) => d.position,
        getText: (d) => d.text,
        pixelOffset: [0, -16],
        visible,
        updateTriggers: triggers,
      }),
    );
  }

  return layers;
}

export function datasetLegend(): LegendSpec {
  return {
    id: DATASET_LAYER_ID,
    title: "Dataset",
    kind: "categorical",
    layerId: DATASET_LAYER_ID,
    items: [
      { label: "Feature", color: cssVarRef("accent"), shape: "fill" },
      { label: "Selected (thick outline + label)", color: cssVarRef("text"), shape: "line" },
      { label: "Not measured", color: cssVarRef("warn"), shape: "line" },
      { label: "Keyboard focus", color: cssVarRef("warn"), shape: "dashed-line" },
    ],
  };
}

/** The dataset feature index of a deck.gl pick, or null when it is not a dataset feature. */
export function pickedFeatureIndex(info: PickingInfo | null | undefined): number | null {
  if (!info || !info.picked || !info.layer) return null;
  const id = info.layer.id;
  if (id !== DATASET_LAYER_ID && !id.startsWith(`${DATASET_LAYER_ID}/`)) return null;
  const obj = info.object as { properties?: { _index?: unknown } } | null | undefined;
  const idx = obj?.properties?._index;
  return typeof idx === "number" && Number.isInteger(idx) ? idx : null;
}
