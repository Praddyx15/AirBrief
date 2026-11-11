/**
 * Ground risk, SORA 2.5 operational volume: pure helpers shared by the panel, the results and the
 * map layers. The sizes repeat the formulas of `airbrief_core/analyses/groundrisk.py` so the form
 * can preview them while typing; the analysis itself always computes and reports them.
 *
 *   S_CV = S_GNSS + S_pos + S_map + v·t_R + S_CM,  S_CM = v²/(2·g·tan θ) (multirotor) or v²/(g·tan φ)
 *   H_CV = H_FG + H_alt + v·t_R·sin γ + ½·v²/g
 *   S_GRB = H_CV + c/2 (1:1) or v·√(2·H_CV/g) + c/2 (ballistic)
 *   adjacent distance = clamp(v·180 s, 5 km, 35 km)
 *   grid = SORA 2.5 Main Body Table 4 by the flight-geography height
 */

import type {
  AircraftType,
  AssemblyInput,
  FeatureCollection,
  Geometry,
  GridDensityCell,
  OperationalVolumeParams,
  UasPreset,
  VolumeDefaults,
} from "@/lib/types";

export const GRAVITY_MS2 = 9.80665;

/**
 * `VolumeDefaults` of airbrief_core/models/ground_risk_volume.py, used when the API's aircraft
 * classes carry no volume terms (an older API, or a static demo exported before them).
 */
export const VOLUME_FALLBACK: VolumeDefaults = {
  aircraft: "multirotor",
  gnss_error_m: 3,
  position_error_m: 1,
  map_error_m: 1,
  reaction_time_s: 1,
  bank_angle_deg: 30,
  pitch_angle_deg: 45,
  altimetry_error_m: 4,
  climb_angle_deg: 45,
};

/** The operational-volume fields of the form (numbers may be null while being edited). */
export interface VolumeForm {
  aircraft: AircraftType;
  /** null = the height above ground of the operation */
  fgHeight: number | null;
  gnss: number | null;
  position: number | null;
  map: number | null;
  reaction: number | null;
  pitch: number | null;
  bank: number | null;
  altimetry: number | null;
  climb: number | null;
}

/** The volume terms of an aircraft class, or the API defaults. */
export function presetVolume(preset: UasPreset | undefined): VolumeDefaults {
  return { ...VOLUME_FALLBACK, ...(preset?.volume ?? {}) };
}

export function volumeFormFrom(d: VolumeDefaults, fgHeight: number | null = null): VolumeForm {
  return {
    aircraft: d.aircraft,
    fgHeight,
    gnss: d.gnss_error_m,
    position: d.position_error_m,
    map: d.map_error_m,
    reaction: d.reaction_time_s,
    pitch: d.pitch_angle_deg,
    bank: d.bank_angle_deg,
    altimetry: d.altimetry_error_m,
    climb: d.climb_angle_deg,
  };
}

/** Request parameters; a field left empty takes the API default (`fallback`). */
export function volumeParams(f: VolumeForm, fallback: VolumeDefaults = VOLUME_FALLBACK): OperationalVolumeParams {
  return {
    aircraft: f.aircraft,
    fg_height_m: f.fgHeight,
    gnss_error_m: f.gnss ?? fallback.gnss_error_m,
    position_error_m: f.position ?? fallback.position_error_m,
    map_error_m: f.map ?? fallback.map_error_m,
    reaction_time_s: f.reaction ?? fallback.reaction_time_s,
    pitch_angle_deg: f.pitch ?? fallback.pitch_angle_deg,
    bank_angle_deg: f.bank ?? fallback.bank_angle_deg,
    altimetry_error_m: f.altimetry ?? fallback.altimetry_error_m,
    climb_angle_deg: f.climb ?? fallback.climb_angle_deg,
  };
}

function num(obj: Record<string, unknown>, key: string, fallback: number | null): number | null {
  const v = obj[key];
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

/** The form fields from stored `params.volume` (missing terms take `fallback`), or null without one. */
export function volumeFormFromParams(value: unknown, fallback: VolumeDefaults): VolumeForm | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  return {
    aircraft: v.aircraft === "fixed_wing" || v.aircraft === "multirotor" ? v.aircraft : fallback.aircraft,
    fgHeight: num(v, "fg_height_m", null),
    gnss: num(v, "gnss_error_m", fallback.gnss_error_m),
    position: num(v, "position_error_m", fallback.position_error_m),
    map: num(v, "map_error_m", fallback.map_error_m),
    reaction: num(v, "reaction_time_s", fallback.reaction_time_s),
    pitch: num(v, "pitch_angle_deg", fallback.pitch_angle_deg),
    bank: num(v, "bank_angle_deg", fallback.bank_angle_deg),
    altimetry: num(v, "altimetry_error_m", fallback.altimetry_error_m),
    climb: num(v, "climb_angle_deg", fallback.climb_angle_deg),
  };
}

export interface VolumePreview {
  sCm: number;
  sCv: number;
  hCv: number;
  grb: number;
  /** v·180 s and its 5–35 km clamp, metres */
  adjacentRaw: number;
  adjacent: number;
}

const rad = (deg: number) => (deg * Math.PI) / 180;

/** Sizes for the form's values, or null while a value it needs is missing. */
export function previewVolume(
  f: VolumeForm,
  o: { speed: number | null; heightAgl: number | null; dimension: number | null; rule: "one_to_one" | "ballistic" },
): VolumePreview | null {
  const angle = f.aircraft === "fixed_wing" ? f.bank : f.pitch;
  const values = [o.speed, o.dimension, f.gnss, f.position, f.map, f.reaction, angle, f.altimetry, f.climb];
  const hFg = f.fgHeight ?? o.heightAgl;
  if (hFg === null || values.some((v) => v === null || !Number.isFinite(v))) return null;
  const v = o.speed as number;
  const tR = f.reaction as number;
  const tan = Math.tan(rad(angle as number));
  if (!(tan > 0)) return null;
  const sCm = f.aircraft === "fixed_wing" ? (v * v) / (GRAVITY_MS2 * tan) : (v * v) / (2 * GRAVITY_MS2 * tan);
  const sCv = (f.gnss as number) + (f.position as number) + (f.map as number) + v * tR + sCm;
  const hCv = hFg + (f.altimetry as number) + v * tR * Math.sin(rad(f.climb as number)) + (0.5 * v * v) / GRAVITY_MS2;
  const half = 0.5 * (o.dimension as number);
  const grb = o.rule === "ballistic" ? v * Math.sqrt((2 * hCv) / GRAVITY_MS2) + half : hCv + half;
  const [adjacentRaw, adjacent] = adjacentDistance(v);
  return { sCm, sCv, hCv, grb, adjacentRaw, adjacent };
}

/** `[v·180 s, clamp(v·180 s, 5 km, 35 km)]` in metres (SORA 2.5 §4.8.3 (b)(i)). */
export function adjacentDistance(speedMs: number): [number, number] {
  const raw = speedMs * 180;
  return [raw, Math.min(35_000, Math.max(5_000, raw))];
}

const M_PER_FT = 0.3048;
/** SORA 2.5 Main Body Table 4: (max height AGL in feet, suggested grid cell in metres). */
const GRID_SIZES: readonly [number, number][] = [
  [500, 200],
  [1_000, 400],
  [2_500, 1_000],
  [5_000, 2_000],
  [10_000, 4_000],
  [20_000, 5_000],
  [60_000, 10_000],
];

/** Suggested density-grid cell (m) for a maximum height AGL (m), as the API chooses it. */
export function autoGridM(heightM: number): number {
  for (const [ft, size] of GRID_SIZES) if (heightM <= ft * M_PER_FT + 1e-9) return size;
  return GRID_SIZES[GRID_SIZES.length - 1]?.[1] ?? 10_000;
}

/** Metres per degree of latitude and longitude at `latDeg` (WGS84 series). */
function metresPerDegree(latDeg: number): [number, number] {
  const p = rad(latDeg);
  const lat = 111_132.954 - 559.822 * Math.cos(2 * p) + 1.175 * Math.cos(4 * p);
  const lon = 111_412.84 * Math.cos(p) - 93.5 * Math.cos(3 * p) + 0.118 * Math.cos(5 * p);
  return [lat, Math.max(lon, 1e-6)];
}

/** A type alias (not an interface) so it fits `GeoJsonProperties`. */
export type GridSquareProps = {
  id: string;
  rank?: number;
  density_per_km2: number;
  band: string;
};

/**
 * The averaged density cells as squares of `gridM` around their centres (east/north aligned,
 * as the API's local frame is near the operation). The GeoJSON export inverse-projects the exact
 * corners; on the map the two differ by well under a metre.
 */
export function gridSquares(
  cells: readonly GridDensityCell[],
  gridM: number,
  ranked = false,
): FeatureCollection<Geometry, GridSquareProps> {
  const half = gridM / 2;
  return {
    type: "FeatureCollection",
    features: cells.map((c, i) => {
      const [mLat, mLon] = metresPerDegree(c.lat);
      const dLat = half / mLat;
      const dLon = half / mLon;
      const ring = [
        [c.lon - dLon, c.lat - dLat],
        [c.lon + dLon, c.lat - dLat],
        [c.lon + dLon, c.lat + dLat],
        [c.lon - dLon, c.lat + dLat],
        [c.lon - dLon, c.lat - dLat],
      ];
      const props: GridSquareProps = { id: c.id, density_per_km2: c.density_per_km2, band: c.band };
      return {
        type: "Feature",
        id: c.id,
        geometry: { type: "Polygon", coordinates: [ring] },
        properties: ranked ? { rank: i + 1, ...props } : props,
      };
    }),
  };
}

/** "12.9010, 77.5055" (signed latitude, longitude in decimal degrees) → position, or null. */
export function parseLatLon(text: string): { lat: number; lon: number } | null {
  const parts = text
    .trim()
    .replace(/°/g, " ")
    .split(/[\s,;]+/)
    .filter(Boolean);
  if (parts.length !== 2) return null;
  const [lat, lon] = parts.map(Number) as [number, number];
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon };
}

/** A 48-vertex circle of `radiusM` around a point (local scale), as a GeoJSON polygon. */
export function circlePolygon(lon: number, lat: number, radiusM: number): Geometry {
  const [mLat, mLon] = metresPerDegree(lat);
  const ring: number[][] = [];
  for (let i = 0; i <= 48; i += 1) {
    const a = (2 * Math.PI * (i % 48)) / 48;
    ring.push([lon + (radiusM * Math.sin(a)) / mLon, lat + (radiusM * Math.cos(a)) / mLat]);
  }
  return { type: "Polygon", coordinates: [ring] };
}

/** True for the geometries the API accepts for an assembly. */
export function isAssemblyGeometry(g: Geometry | null | undefined): g is Geometry {
  return !!g && (g.type === "Point" || g.type === "MultiPoint" || g.type === "Polygon" || g.type === "MultiPolygon");
}

/** Assemblies stored with an analysis (invalid entries are dropped). */
export function assembliesFromParams(value: unknown): AssemblyInput[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (a): a is AssemblyInput =>
      !!a && typeof a === "object" && typeof (a as AssemblyInput).name === "string" && isAssemblyGeometry((a as AssemblyInput).geometry),
  );
}

/** The point itself, or a rough centre (mean of the first ring's vertices) for the "Area around …" text. */
export function assemblyPlace(g: Geometry): { lon: number; lat: number } | null {
  if (g.type === "Point") return { lon: Number(g.coordinates[0]), lat: Number(g.coordinates[1]) };
  const ring =
    g.type === "Polygon" ? g.coordinates[0] : g.type === "MultiPolygon" ? g.coordinates[0]?.[0] : g.type === "MultiPoint" ? g.coordinates : null;
  if (!ring || ring.length === 0) return null;
  let lon = 0;
  let lat = 0;
  for (const p of ring) {
    lon += Number(p[0]);
    lat += Number(p[1]);
  }
  return { lon: lon / ring.length, lat: lat / ring.length };
}
