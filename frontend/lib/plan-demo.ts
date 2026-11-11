/**
 * The static demo (`NEXT_PUBLIC_DEMO=1`) has no API: these answer the plan endpoints from
 * `demo/context.json` (the demo pack's context layers, written by `python -m seed.demo_export`)
 * and the browser's coordinate parser. They follow the API's rules (`api/routers/context.py`,
 * `airbrief_core/data/context.py`) closely enough for a read-only preview.
 */

import { MAX_TYPED_POINTS, formatLatLon, parseCoordinateText, type LonLat } from "./coords";
import type {
  BBoxList,
  ContextLayerKey,
  ContextResponse,
  Feature,
  Geometry,
  GeometryParseResponse,
  GeometryShape,
  Issue,
  PlaceResult,
  Position,
} from "./types";

function extend(b: number[], g: Geometry | null): void {
  if (!g) return;
  if (g.type === "GeometryCollection") {
    g.geometries.forEach((x) => extend(b, x));
    return;
  }
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") {
      const [x, y] = c as number[];
      b[0] = Math.min(b[0] as number, x as number);
      b[1] = Math.min(b[1] as number, y as number);
      b[2] = Math.max(b[2] as number, x as number);
      b[3] = Math.max(b[3] as number, y as number);
    } else if (Array.isArray(c)) c.forEach(walk);
  };
  walk((g as { coordinates: unknown }).coordinates);
}

function touches(f: Feature, view: BBoxList): boolean {
  const b = [Infinity, Infinity, -Infinity, -Infinity];
  extend(b, f.geometry);
  return (b[0] as number) <= view[2] && (b[2] as number) >= view[0] && (b[1] as number) <= view[3] && (b[3] as number) >= view[1];
}

/** The demo pack's context for a view: the exported layers filtered to features touching it. */
export function demoContext(all: ContextResponse, view: BBoxList, layers: readonly ContextLayerKey[]): ContextResponse {
  const overlaps = all.bbox[0] <= view[2] && all.bbox[2] >= view[0] && all.bbox[1] <= view[3] && all.bbox[3] >= view[1];
  const out: ContextResponse = { ...all, bbox: view, layers: {}, truncated: {}, counts: {}, datapack: overlaps ? all.datapack : null };
  for (const key of layers) {
    const features = overlaps ? (all.layers[key]?.features ?? []).filter((f) => touches(f, view)) : [];
    out.layers[key] = { type: "FeatureCollection", features };
    out.truncated[key] = false;
    out.counts[key] = features.length;
  }
  return out;
}

const TYPE_ORDER = ["large_airport", "medium_airport", "small_airport", "heliport", "seaplane_base", "balloonport"];
const words = (s: string) => s.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);

/** Rank of an aerodrome for a query (0 best), as `airbrief_core.data.context._rank`. */
function rank(p: Record<string, unknown>, q: string, tokens: string[]): number | null {
  const ident = String(p.ident ?? "").toLowerCase();
  const iata = String(p.iata ?? "").toLowerCase();
  const name = String(p.name ?? "").toLowerCase();
  const town = String(p.municipality ?? "").toLowerCase();
  if (q === ident || (iata && q === iata)) return 0;
  if (ident.startsWith(q) || (iata && iata.startsWith(q))) return 1;
  if (name.startsWith(q)) return 2;
  if (words(name).some((w) => w.startsWith(q))) return 3;
  if (town && (town.startsWith(q) || words(town).some((w) => w.startsWith(q)))) return 4;
  if (name.includes(q)) return 5;
  if (town && town.includes(q)) return 6;
  if (tokens.length > 1) {
    const all = words(`${ident} ${iata} ${name} ${town}`);
    if (tokens.every((t) => all.some((w) => w.startsWith(t)))) return 7;
  }
  return null;
}

/** Typed coordinates, then the demo pack's aerodromes matching `query`. */
export function demoPlaces(all: ContextResponse | null, query: string, limit: number): PlaceResult[] {
  const out: PlaceResult[] = [];
  const parsed = parseCoordinateText(query);
  if (parsed.ok && parsed.points.length === 1) {
    const [lon, lat] = parsed.points[0] as LonLat;
    const codes = new Set(parsed.notes.map((n) => n.code));
    out.push({
      label: formatLatLon(lon, lat),
      kind: "coordinates",
      ident: null,
      lon,
      lat,
      detail: codes.has("ambiguous_order")
        ? "Coordinates, read as latitude, longitude"
        : codes.has("lonlat_order")
          ? "Coordinates, read as longitude, latitude"
          : "Coordinates",
      datapack: null,
    });
  }
  const q = query.toLowerCase().split(/\s+/).filter(Boolean).join(" ");
  if (!q || !all) return out.slice(0, limit);
  const tokens = words(q);
  const ranked = (all.layers.aerodromes?.features ?? [])
    .map((f) => ({ f, p: (f.properties ?? {}) as Record<string, unknown>, r: rank((f.properties ?? {}) as Record<string, unknown>, q, tokens) }))
    .filter((x): x is { f: Feature; p: Record<string, unknown>; r: number } => x.r !== null && x.f.geometry?.type === "Point")
    .sort(
      (a, b) =>
        a.r - b.r ||
        TYPE_ORDER.indexOf(String(a.p.type)) - TYPE_ORDER.indexOf(String(b.p.type)) ||
        String(a.p.name).localeCompare(String(b.p.name)),
    );
  for (const { f, p } of ranked) {
    const [lon, lat] = (f.geometry as { coordinates: Position }).coordinates as [number, number];
    const detail = [p.type_label, p.municipality, p.iata ? `IATA ${String(p.iata)}` : null].filter(Boolean).join(" · ");
    out.push({ label: `${String(p.ident)} · ${String(p.name)}`, kind: "aerodrome", ident: String(p.ident), lon, lat, detail, datapack: all.datapack });
  }
  return out.slice(0, limit);
}

const GEOMETRY_TYPES = new Set(["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection"]);

function inRange(g: Geometry): boolean {
  const b = [Infinity, Infinity, -Infinity, -Infinity];
  extend(b, g);
  return Number.isFinite(b[0]) && Math.abs(b[0] as number) <= 180 && Math.abs(b[2] as number) <= 180 && Math.abs(b[1] as number) <= 90 && Math.abs(b[3] as number) <= 90;
}

function countVertices(g: Geometry): number {
  if (g.type === "GeometryCollection") return g.geometries.reduce((n, x) => n + countVertices(x), 0);
  let n = 0;
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") n += 1;
    else if (Array.isArray(c)) c.forEach(walk);
  };
  walk((g as { coordinates: unknown }).coordinates);
  return n;
}

/**
 * Pasted GeoJSON or coordinates read in the browser. Throws an Error with a readable message;
 * WKT needs the API (the caller says so).
 */
export function demoParseGeometry(text: string, shape: GeometryShape): GeometryParseResponse {
  const trimmed = text.trim();
  if (trimmed.startsWith("{")) {
    let doc: unknown;
    try {
      doc = JSON.parse(trimmed);
    } catch (error) {
      throw new Error(`The text is not valid JSON: ${error instanceof Error ? error.message : String(error)}.`);
    }
    const obj = doc as { type?: string; geometry?: Geometry | null; features?: Feature[] };
    let geometry: Geometry | null = null;
    if (obj.type === "Feature") geometry = obj.geometry ?? null;
    else if (obj.type === "FeatureCollection") {
      const parts = (obj.features ?? []).map((f) => f.geometry).filter((g): g is Geometry => Boolean(g));
      geometry = parts.length ? { type: "GeometryCollection", geometries: parts } : null;
    } else if (obj.type && GEOMETRY_TYPES.has(obj.type)) geometry = doc as Geometry;
    if (!geometry) throw new Error("Paste a GeoJSON geometry, Feature or FeatureCollection with a geometry.");
    if (!inRange(geometry)) throw new Error("Coordinates are outside longitude ±180° / latitude ±90°: paste WGS84 longitude/latitude.");
    const vertices = countVertices(geometry);
    if (vertices > MAX_TYPED_POINTS) {
      throw new Error(`The geometry has ${vertices.toLocaleString("en-US")} vertices; at most ${MAX_TYPED_POINTS.toLocaleString("en-US")} can be pasted.`);
    }
    return { geometry, kind: geometry.type, source: "geojson", vertices, issues: [] };
  }
  const parsed = parseCoordinateText(trimmed);
  if (!parsed.ok) throw new Error(parsed.error);
  const pts = parsed.points;
  const closed = pts.length >= 4 && pts[0]?.[0] === pts[pts.length - 1]?.[0] && pts[0]?.[1] === pts[pts.length - 1]?.[1];
  const as = shape !== "auto" ? shape : pts.length === 1 ? "point" : closed ? "polygon" : "line";
  let geometry: Geometry;
  if (as === "point") geometry = pts.length === 1 ? { type: "Point", coordinates: pts[0] as LonLat } : { type: "MultiPoint", coordinates: pts };
  else if (as === "line") {
    if (pts.length < 2) throw new Error("A line needs at least two different points.");
    geometry = { type: "LineString", coordinates: pts };
  } else {
    const ring = closed ? pts.slice(0, -1) : pts;
    if (ring.length < 3) throw new Error("An area needs at least three different points.");
    geometry = { type: "Polygon", coordinates: [[...ring, ring[0] as LonLat]] };
  }
  const issues: Issue[] = parsed.notes.map((n) => ({ code: n.code, message: n.message, severity: n.severity, feature_index: null }));
  return { geometry, kind: geometry.type, source: "coordinates", vertices: countVertices(geometry), issues };
}
