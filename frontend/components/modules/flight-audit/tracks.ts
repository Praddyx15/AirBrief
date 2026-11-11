/**
 * Position tracks and training areas of the open dataset, for the flight audit's "Dataset track"
 * source and its additional checks. The API resolves the same track from the selected feature
 * (`api/services/tracks.py`); the training area is sent as a polygon (or `null` for "Off").
 */

import type { DatasetFeatureCollection, Geometry, TrackFormat, TrackHeights } from "@/lib/types";
import { featureName } from "../shared";

/** Feature names taken as a training area by default (same rule as `TRAINING_BOX_RE` in the API). */
export const TRAINING_AREA_RE = /training|\b(area|boundary|geofence)\b/i;

/** Areas to stay out of are never the default training area (`NOT_TRAINING_BOX_RE` in the API). */
export const NOT_TRAINING_AREA_RE = /\bno[\s_-]*fly|\bnfz\b|restrict|prohibit|danger|exclu|keep[\s_-]*out|forbidden|audience|spectator|\bred[\s_-]*zone/i;

/** True for a feature name that reads as the area a flight should stay inside (`is_training_area_name`). */
export function isTrainingAreaName(name: string): boolean {
  return TRAINING_AREA_RE.test(name) && !NOT_TRAINING_AREA_RE.test(name);
}

export const TRACK_FORMAT_LABEL: Record<TrackFormat, string> = {
  gpx: "GPX track",
  kml: "KML gx:Track",
  csv: "CSV track",
  other: "Position track",
};

export const TRACK_HEIGHTS_LABEL: Record<TrackHeights, string> = {
  auto: "From the file",
  amsl: "Above sea level",
  above_ground: "Above ground",
  above_takeoff: "Above take-off",
};

export const TRACK_HEIGHTS_HINT =
  "“From the file” follows its altitude mode: absolute (GPX elevations) is above sea level, relativeToGround is above ground, anything else is read as height above the take-off point.";

/** A timed track of the open dataset (a feature the API reports with `_time_points`). */
export interface DatasetTrack {
  index: number;
  name: string;
  layer: string;
  points: number;
  durationS: number | null;
  format: TrackFormat;
  /** first fix (GPX tracks carry it as a property) */
  start: string | null;
}

/** A polygon (or closed line) of the open dataset that can serve as the training area. */
export interface AreaChoice {
  key: string;
  index: number;
  name: string;
  geometry: Geometry;
}

function formatOf(props: Record<string, unknown>): TrackFormat {
  if (props.gpx_element !== undefined && props.gpx_element !== null) return "gpx";
  if (props.source_format === "lon_lat" || props.source_format === "local_xyz") return "csv";
  return "kml";
}

export function datasetTracks(features: DatasetFeatureCollection | null): DatasetTrack[] {
  const out: DatasetTrack[] = [];
  for (const f of features?.features ?? []) {
    const p = f.properties;
    const type = f.geometry?.type;
    if (typeof p._time_points !== "number" || p._time_points < 2) continue;
    if (type !== "LineString" && type !== "MultiLineString") continue;
    out.push({
      index: p._index,
      name: featureName(p),
      layer: p._layer,
      points: p._time_points,
      durationS: typeof p._duration_s === "number" ? p._duration_s : null,
      format: formatOf(p),
      start: typeof p.start_time === "string" ? p.start_time : null,
    });
  }
  return out;
}

function isClosedRing(coords: unknown): coords is number[][] {
  if (!Array.isArray(coords) || coords.length < 4) return false;
  const first = coords[0] as unknown;
  const last = coords[coords.length - 1] as unknown;
  return (
    Array.isArray(first) &&
    Array.isArray(last) &&
    first.length >= 2 &&
    last.length >= 2 &&
    first[0] === last[0] &&
    first[1] === last[1]
  );
}

/** Polygons and closed lines (e.g. a GPX route around the area) as training-area choices. */
export function areaCandidates(features: DatasetFeatureCollection | null): AreaChoice[] {
  const out: AreaChoice[] = [];
  for (const f of features?.features ?? []) {
    const g = f.geometry;
    if (!g) continue;
    // A timed track is a flight, never a training area (a closed track could look like one).
    if (f.properties._time_points != null) continue;
    let geometry: Geometry | null = null;
    if (g.type === "Polygon" || g.type === "MultiPolygon") geometry = g;
    else if (g.type === "LineString" && isClosedRing(g.coordinates)) {
      geometry = { type: "Polygon", coordinates: [g.coordinates.map((c) => [c[0] as number, c[1] as number])] };
    }
    if (!geometry) continue;
    out.push({ key: `f:${f.properties._index}`, index: f.properties._index, name: featureName(f.properties), geometry });
  }
  return out;
}

/** The area chosen by default for a dataset track: the first one named like a training area. */
export function defaultAreaKey(choices: readonly AreaChoice[]): string {
  return choices.find((c) => isTrainingAreaName(c.name))?.key ?? "off";
}
