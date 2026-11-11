/**
 * Map preview of plan mode (`lib/plan-store.ts`): the shape being drawn (placed vertices, a
 * dashed rubber band to the pointer, the area so far), the unsaved draft (a pin with its radius
 * circle, a line, an area or a pasted geometry) and the "Go to" target. Drawn above every other
 * layer and never pickable, so clicks always reach the drawing handlers.
 */

import type { Layer } from "@deck.gl/core";
import type { LonLat } from "@/lib/coords";
import { circleRing, draftGeometry, type PlanDraft, type PlanTool } from "@/lib/plan";
import type { GoToTarget } from "@/lib/plan-store";
import type { Feature, Geometry } from "@/lib/types";
import { geoJsonLayer, mapPalette, pathLayer, pointLayer, textLabelLayer } from "./common";

export interface PlanLayerState {
  drawing: PlanTool | null;
  vertices: readonly LonLat[];
  cursor: LonLat | null;
  draft: PlanDraft | null;
  pinRadiusM: number;
  goTo: GoToTarget | null;
}

function feature(geometry: Geometry): Feature {
  return { type: "Feature", geometry, properties: {} };
}

/** Layers for the current plan state (empty when nothing is being planned). */
export function planLayers(s: PlanLayerState, theme: "dark" | "light"): Layer[] {
  const palette = mapPalette();
  const accent = palette.accent;
  const fill = [accent[0], accent[1], accent[2], 40] as [number, number, number, number];
  const triggers = [theme];
  const layers: Layer[] = [];

  if (s.goTo) {
    const goTo = s.goTo;
    layers.push(
      pointLayer({
        id: "plan:goto",
        data: [goTo],
        getPosition: (g) => [g.lon, g.lat],
        radius: 10,
        filled: false,
        lineColor: palette.text,
        lineWidth: 2.5,
        pickable: false,
        updateTriggers: triggers,
      }),
      textLabelLayer({
        id: "plan:goto/label",
        data: [goTo],
        getPosition: (g) => [g.lon, g.lat],
        getText: (g) => g.label,
        pixelOffset: [0, 22],
        size: 12,
        updateTriggers: triggers,
      }),
    );
  }

  const draft = s.draft;
  if (draft) {
    if (draft.kind === "pin" && draft.vertices[0]) {
      const [lon, lat] = draft.vertices[0];
      layers.push(
        geoJsonLayer({
          id: "plan:draft/radius",
          data: [feature({ type: "Polygon", coordinates: [circleRing(lon, lat, s.pinRadiusM)] })],
          fillColor: fill,
          lineColor: accent,
          lineWidth: 2,
          dashed: true,
          pickable: false,
          updateTriggers: [...triggers, s.pinRadiusM],
        }),
      );
    } else {
      const geometry = draftGeometry(draft);
      if (geometry) {
        layers.push(
          geoJsonLayer({
            id: "plan:draft",
            data: [feature(geometry)],
            fillColor: fill,
            lineColor: accent,
            lineWidth: 3,
            pointRadius: 7,
            pickable: false,
            updateTriggers: triggers,
          }),
        );
      }
    }
    if (draft.kind !== "geometry") {
      layers.push(
        pointLayer({
          id: "plan:draft/vertices",
          data: draft.vertices,
          getPosition: (p) => p,
          radius: draft.kind === "pin" ? 7 : 4,
          fillColor: accent,
          lineColor: palette.bg,
          lineWidth: 2,
          pickable: false,
          updateTriggers: triggers,
        }),
      );
    }
  }

  if (s.drawing) {
    const vertices = s.vertices;
    const cursor = s.cursor;
    if (s.drawing === "pin" && cursor) {
      layers.push(
        geoJsonLayer({
          id: "plan:drawing/radius",
          data: [feature({ type: "Polygon", coordinates: [circleRing(cursor[0], cursor[1], s.pinRadiusM)] })],
          filled: false,
          lineColor: accent,
          lineWidth: 1.5,
          dashed: true,
          pickable: false,
          updateTriggers: [...triggers, s.pinRadiusM],
        }),
      );
    }
    if (s.drawing === "area" && vertices.length + (cursor ? 1 : 0) >= 3) {
      const ring = [...vertices, ...(cursor ? [cursor] : []), vertices[0] as LonLat];
      layers.push(
        geoJsonLayer({
          id: "plan:drawing/area",
          data: [feature({ type: "Polygon", coordinates: [ring] })],
          fillColor: fill,
          stroked: false,
          pickable: false,
          updateTriggers: triggers,
        }),
      );
    }
    if (vertices.length >= 2) {
      layers.push(
        pathLayer({
          id: "plan:drawing/path",
          data: [vertices],
          getPath: (p) => [...p],
          color: accent,
          width: 3,
          pickable: false,
          updateTriggers: triggers,
        }),
      );
    }
    const last = vertices[vertices.length - 1];
    if (last && cursor && s.drawing !== "pin") {
      const band: LonLat[][] = [[last, cursor]];
      if (s.drawing === "area" && vertices.length >= 2) band.push([cursor, vertices[0] as LonLat]);
      layers.push(
        pathLayer({
          id: "plan:drawing/rubber-band",
          data: band,
          getPath: (p) => p,
          color: accent,
          width: 2,
          dashed: true,
          pickable: false,
          updateTriggers: triggers,
        }),
      );
    }
    if (vertices.length > 0) {
      const closable = s.drawing === "area" && vertices.length >= 3;
      layers.push(
        pointLayer({
          id: "plan:drawing/vertices",
          data: vertices.map((p, i) => ({ p, first: i === 0 })),
          getPosition: (d) => d.p,
          // The first vertex grows once clicking it would close the area.
          radius: (d) => (d.first && closable ? 8 : 5),
          fillColor: (d) => (d.first && closable ? palette.bg : accent),
          lineColor: (d) => (d.first && closable ? accent : palette.bg),
          lineWidth: (d) => (d.first && closable ? 3 : 2),
          pickable: false,
          updateTriggers: [...triggers, closable],
        }),
      );
    }
  }
  return layers;
}
