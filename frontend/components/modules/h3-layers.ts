/**
 * H3 results (flyability grid, density cells, hotspots) as polygon FeatureCollections. Modules
 * return these from `derivedLayers` so the Layers panel and the drawer's "Layers as table" list
 * them like any other result layer. The API's GeoJSON export builds the same polygons
 * (`api/services/analyses.py`, `h3_layers`), with the same layer names.
 */

import { cellToBoundary, isValidCell } from "h3-js";
import type { Feature, FeatureCollection, PolygonGeometry } from "@/lib/types";

/** One polygon feature per valid cell; `properties` = `{ h3, ...props(cell, index) }`. */
export function h3Collection<T extends { h3: string }>(
  cells: readonly T[],
  props: (cell: T, index: number) => Record<string, unknown>,
): FeatureCollection<PolygonGeometry, Record<string, unknown>> {
  const features: Feature<PolygonGeometry, Record<string, unknown>>[] = [];
  cells.forEach((cell, index) => {
    if (!isValidCell(cell.h3)) return;
    // GeoJSON order ([lng, lat]) and a closed ring.
    const ring = cellToBoundary(cell.h3, true);
    features.push({
      type: "Feature",
      id: cell.h3,
      geometry: { type: "Polygon", coordinates: [ring] },
      properties: { h3: cell.h3, ...props(cell, index) },
    });
  });
  return { type: "FeatureCollection", features };
}
