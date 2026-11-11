/**
 * Plan without a file: an operation defined on the map (pin, line, area), typed, pasted or
 * taken from the device location becomes an ordinary dataset. `operationFile()` wraps it as a
 * GeoJSON FeatureCollection that the workspace uploads through `POST /api/files/`, so Measure,
 * every module, the exports and the report work on it unchanged.
 *
 * A pin with radius R is saved as two features: the Point (properties `radius_m`) and
 * "Pin radius", a 64-sided polygon inscribed in the circle of radius R, so modules that need an
 * area (airspace overlap, ground risk, survey) can use it. Its vertices are R from the centre
 * using the WGS84 radii of curvature at the centre's latitude (meridian M and prime vertical N):
 *   Δφ = R·cos θ / M,   Δλ = R·sin θ / (N·cos φ),
 *   M = a(1 − e²)/(1 − e² sin²φ)^{3/2},   N = a/(1 − e² sin²φ)^{1/2}
 * which is exact to a few millimetres per kilometre of radius (the ellipsoid's curvature changes
 * little across the circle). The inscribed 64-gon's area is (64/2)·R²·sin(2π/64) = 99.84 % of
 * πR²; the server measures the saved polygon geodesically like any other.
 */

import type { Feature, FeatureCollection, Geometry, Issue, Position } from "./types";
import { formatLatLon, type LonLat } from "./coords";

/** WGS84 semi-major axis (m) and flattening. */
const WGS84_A = 6_378_137;
const WGS84_F = 1 / 298.257223563;
const WGS84_E2 = WGS84_F * (2 - WGS84_F);
/** Mean Earth radius (IUGG R1, m), for the "≈" previews of lengths and areas. */
const MEAN_EARTH_RADIUS_M = 6_371_008.8;

/** Sides of the polygon that stands for a pin's radius. */
export const PIN_CIRCLE_SIDES = 64;
/** Area of the inscribed polygon as a share of the circle: (n/2)·sin(2π/n)/π. */
export const PIN_CIRCLE_AREA_RATIO = (PIN_CIRCLE_SIDES / 2) * Math.sin((2 * Math.PI) / PIN_CIRCLE_SIDES) / Math.PI;
export const DEFAULT_PIN_RADIUS_M = 100;
export const PIN_RADIUS_PRESETS_M: readonly number[] = [50, 100, 250, 500, 1000];
export const MIN_PIN_RADIUS_M = 1;
export const MAX_PIN_RADIUS_M = 50_000;

export type PlanTool = "pin" | "line" | "area";

/** Where a draft came from. */
export type DraftSource = "map" | "coordinates" | "paste" | "location" | "goto";

/** An operation that has been defined but not saved yet. */
export interface PlanDraft {
  kind: PlanTool | "geometry";
  /** `[lon, lat]`: the pin (one), the line (≥ 2) or the area's ring without the closing vertex (≥ 3) */
  vertices: LonLat[];
  /** a pasted geometry (kind "geometry") */
  geometry: Geometry | null;
  /** pasted GeoJSON text, uploaded as is so feature names and properties survive */
  sourceText: string | null;
  source: DraftSource;
  /** device location accuracy: radius (m) at 95 % confidence, as the Geolocation API reports it */
  accuracyM: number | null;
  /** name of the operation, e.g. "Pin" or "Drawn area" */
  name: string;
  /** how typed or pasted text was read (coordinate order, decimal commas, repairs) */
  notes?: readonly Issue[];
}

/** Ring (closed, `n + 1` positions) of the `n`-gon inscribed in the circle of `radiusM` around a point. */
export function circleRing(lon: number, lat: number, radiusM: number, n = PIN_CIRCLE_SIDES): Position[] {
  const phi = (lat * Math.PI) / 180;
  const w = Math.sqrt(1 - WGS84_E2 * Math.sin(phi) ** 2);
  const meridian = (WGS84_A * (1 - WGS84_E2)) / w ** 3;
  const normal = WGS84_A / w;
  const cosPhi = Math.max(1e-9, Math.cos(phi));
  const ring: Position[] = [];
  for (let i = 0; i < n; i += 1) {
    const t = (2 * Math.PI * i) / n;
    ring.push([
      lon + ((radiusM * Math.sin(t)) / (normal * cosPhi)) * (180 / Math.PI),
      lat + ((radiusM * Math.cos(t)) / meridian) * (180 / Math.PI),
    ]);
  }
  ring.push(ring[0] as Position);
  return ring;
}

/**
 * Why a pin's circle cannot be saved, or null: the polygon is built in plain longitude/latitude,
 * so it must stay clear of the poles and of the ±180° meridian.
 */
export function pinCircleProblem(lon: number, lat: number, radiusM: number): string | null {
  if (!(radiusM >= MIN_PIN_RADIUS_M && radiusM <= MAX_PIN_RADIUS_M)) {
    return `The radius must be between ${MIN_PIN_RADIUS_M} m and ${MAX_PIN_RADIUS_M.toLocaleString("en-US")} m.`;
  }
  const ring = circleRing(lon, lat, radiusM);
  const lats = ring.map((p) => p[1] as number);
  const lons = ring.map((p) => p[0] as number);
  if (Math.max(...lats.map(Math.abs)) > 89.5) return "The circle reaches a pole: move the pin or reduce the radius.";
  if (Math.max(...lons.map(Math.abs)) > 180) return "The circle crosses the ±180° meridian: move the pin or reduce the radius.";
  return null;
}

/** Great-circle distance on the mean sphere (haversine), metres. */
export function sphereDistanceM(a: LonLat, b: LonLat): number {
  const toRad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * toRad;
  const dLon = (b[0] - a[0]) * toRad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * toRad) * Math.cos(b[1] * toRad) * Math.sin(dLon / 2) ** 2;
  return 2 * MEAN_EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Length of a path on the mean sphere, metres (a preview: the server measures geodesically). */
export function pathLengthM(points: readonly LonLat[], closed = false): number {
  let total = 0;
  for (let i = 1; i < points.length; i += 1) total += sphereDistanceM(points[i - 1] as LonLat, points[i] as LonLat);
  if (closed && points.length > 2) total += sphereDistanceM(points[points.length - 1] as LonLat, points[0] as LonLat);
  return total;
}

/**
 * Area of a ring on the mean sphere (spherical excess, Chamberlain & Duquette 2007:
 * A = R²/2 · |Σ (λᵢ₊₁ − λᵢ)(2 + sin φᵢ + sin φᵢ₊₁)|), m². A preview; the server measures it.
 */
export function ringAreaM2(ring: readonly LonLat[]): number {
  if (ring.length < 3) return 0;
  const toRad = Math.PI / 180;
  let sum = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const p1 = ring[i] as LonLat;
    const p2 = ring[(i + 1) % ring.length] as LonLat;
    sum += (p2[0] - p1[0]) * toRad * (2 + Math.sin(p1[1] * toRad) + Math.sin(p2[1] * toRad));
  }
  return Math.abs((sum * MEAN_EARTH_RADIUS_M * MEAN_EARTH_RADIUS_M) / 2);
}

/** The draft as one GeoJSON geometry (a pin is its Point). */
export function draftGeometry(draft: PlanDraft): Geometry | null {
  if (draft.kind === "geometry") return draft.geometry;
  if (draft.kind === "pin") return draft.vertices[0] ? { type: "Point", coordinates: draft.vertices[0] } : null;
  if (draft.kind === "line") return draft.vertices.length >= 2 ? { type: "LineString", coordinates: draft.vertices } : null;
  if (draft.vertices.length < 3) return null;
  return { type: "Polygon", coordinates: [[...draft.vertices, draft.vertices[0] as LonLat]] };
}

/** `[min_lon, min_lat, max_lon, max_lat]` of a list of positions. */
export function pointsBounds(points: readonly Position[]): [number, number, number, number] | null {
  if (points.length === 0) return null;
  let b: [number, number, number, number] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const p of points) {
    const [x, y] = [p[0] as number, p[1] as number];
    b = [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)];
  }
  return b;
}

/** "13.1979 N 77.7063 E" (4 decimals ≈ 11 m; only letters, digits, dots and spaces for a file name). */
export function positionSlug(lon: number, lat: number): string {
  return `${Math.abs(lat).toFixed(4)} ${lat >= 0 ? "N" : "S"} ${Math.abs(lon).toFixed(4)} ${lon >= 0 ? "E" : "W"}`;
}

/** File name of the dataset a draft is saved as. */
export function operationFileName(draft: PlanDraft): string {
  const at = draft.vertices[0];
  if (draft.kind === "pin" && at) {
    return `${draft.source === "location" ? "My location" : "Pin"} ${positionSlug(at[0], at[1])}.geojson`;
  }
  if (draft.kind === "line") return draft.source === "coordinates" ? "Typed line.geojson" : "Drawn line.geojson";
  if (draft.kind === "area") return draft.source === "coordinates" ? "Typed area.geojson" : "Drawn area.geojson";
  return "Pasted geometry.geojson";
}

/** The FeatureCollection a draft is saved as (see the module comment for pins). */
export function operationCollection(draft: PlanDraft, pinRadiusM: number): FeatureCollection {
  const created = { source: "AirBrief plan", input: draft.source };
  if (draft.kind === "pin") {
    const at = draft.vertices[0] as LonLat;
    const radius = Math.round(pinRadiusM * 100) / 100;
    const extra = draft.accuracyM !== null ? { accuracy_m: Math.round(draft.accuracyM * 10) / 10 } : {};
    return {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: { type: "Point", coordinates: [at[0], at[1]] },
          properties: { name: draft.name, radius_m: radius, ...extra, ...created },
        },
        {
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [circleRing(at[0], at[1], pinRadiusM)] },
          properties: {
            name: "Pin radius",
            radius_m: radius,
            note: `${PIN_CIRCLE_SIDES}-sided polygon inscribed in the ${radius} m circle around ${formatLatLon(at[0], at[1])}`,
            ...created,
          },
        },
      ],
    };
  }
  const geometry = draftGeometry(draft);
  const parts: Geometry[] = !geometry ? [] : geometry.type === "GeometryCollection" ? geometry.geometries : [geometry];
  const features: Feature[] = parts.map((g, i) => ({
    type: "Feature",
    geometry: g,
    properties: { name: parts.length > 1 ? `${draft.name} ${i + 1}` : draft.name, ...created },
  }));
  return { type: "FeatureCollection", features };
}

/** The draft as a `.geojson` File ready for `useUpload`. Pasted GeoJSON is uploaded as pasted. */
export function operationFile(draft: PlanDraft, pinRadiusM: number): File {
  const text = draft.kind === "geometry" && draft.sourceText ? draft.sourceText : JSON.stringify(operationCollection(draft, pinRadiusM));
  return new File([text], operationFileName(draft), { type: "application/geo+json" });
}
