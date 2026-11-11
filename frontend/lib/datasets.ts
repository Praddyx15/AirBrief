/**
 * Small, pure helpers about datasets (`FileInfo`) shared by the upload page, the workspace and
 * the report.
 */

import type { FileInfo } from "./types";

/**
 * A processed dataset with no coordinate system and nothing measured (e.g. a shapefile without
 * its .prj): it cannot be drawn or measured until it is uploaded again with a CRS override, so
 * it must not look "Ready".
 */
export function needsCrs(file: Pick<FileInfo, "status" | "crs" | "feature_count" | "totals">): boolean {
  return file.status === "COMPLETED" && file.crs === null && file.feature_count > 0 && (file.totals?.measured ?? 0) === 0;
}

/** Any polygon in its layers: what "zones from a layer" and the ED-318 export read. */
export function hasPolygons(file: Pick<FileInfo, "layers">): boolean {
  return file.layers.some((l) => (l.geometry_types.Polygon ?? 0) + (l.geometry_types.MultiPolygon ?? 0) > 0);
}
