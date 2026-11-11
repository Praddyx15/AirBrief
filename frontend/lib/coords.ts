/**
 * Typed coordinates → points, in the browser. The same rules as the API's parser
 * (`airbrief_core/geo/parse.py`, tested in `tests/core/test_geo_parse.py`), so the coordinates
 * dialog and the "Go to" search answer as you type and work in the static demo too. The server
 * remains the authority: a saved operation is re-read when it is uploaded.
 *
 * One point per line (or per `;`-separated part). Accepted:
 * - decimal degrees: `12.9716, 77.5946` · `12.9716 77.5946` · `-33.8688 151.2093`
 * - hemisphere letters before or after each value: `12.9716 N, 77.5946 E` · `N12.9716 E77.5946`
 * - degrees, minutes, seconds: `12°58′17.8″N 77°35′40.6″E` · `12 58 17.8 N 77 35 40.6 E`
 * - a decimal comma where it cannot be the separator: `12,9716 77,5946` (`12,5,77` is refused:
 *   12.5, 77 or 12, 5.77?)
 * - `[lon, lat]` in square brackets, read in GeoJSON order
 *
 * Order: hemisphere letters decide; without them a value beyond ±90° is the longitude; when
 * both fit, the text is read as latitude, longitude and an `ambiguous_order` warning says so.
 */

import type { Severity } from "./types";

export type LonLat = [number, number];

export interface CoordinateNote {
  code: "ambiguous_order" | "lonlat_order" | "decimal_comma";
  message: string;
  severity: Severity;
}

export type CoordinateParse =
  | { ok: true; points: LonLat[]; notes: CoordinateNote[] }
  | { ok: false; error: string; line: number | null };

/** Most points the dialog accepts (the API's `MAX_VERTICES`). */
export const MAX_TYPED_POINTS = 10_000;

const FORMAT_HINT =
  "Use decimal degrees such as '12.9716, 77.5946', hemispheres such as '12.9716 N 77.5946 E', or degrees, minutes and seconds such as 12°58′17.8″N 77°35′40.6″E.";

const NORMALISE: Record<string, string> = {
  "\u2212": "-",
  "\u2032": "'",
  "\u2019": "'",
  "\u2018": "'",
  "\u00b4": "'",
  "`": "'",
  "\u2033": '"',
  "\u201c": '"',
  "\u201d": '"',
  "\u00ba": "\u00b0",
  "\u02da": "\u00b0",
  "\u00a0": " ",
  "\u202f": " ",
  "\u2009": " ",
  "\t": " ",
};
const NORMALISE_RE = new RegExp(`[${Object.keys(NORMALISE).join("")}]`, "g");

const NUM = "[-+]?\\d+(?:\\.\\d+)?";
const DOT_PAIR = new RegExp(`^\\s*(${NUM})\\s*(?:,|\\s)\\s*(${NUM})\\s*$`);
const NUM_COMMA = "[-+]?\\d+(?:,\\d+)?";
const COMMA_PAIR = new RegExp(`^\\s*(${NUM_COMMA})\\s*(?:,\\s+|\\s+|/)\\s*(${NUM_COMMA})\\s*$`);
/** Three whole numbers joined by bare commas ("12,5,77"): 12.5, 77 or 12, 5.77 — refused. */
const AMBIGUOUS_COMMAS = /^\s*[-+]?\d+,\d+,[-+]?\d+\s*$/;
const SIGN = "(?<sign>[-+])?\\s*";
// Degrees, minutes[, seconds] (whole degrees) or decimal degrees; each part ends with its symbol
// or whitespace, so "12.97" is never read as 1° 2.97′.
const BODY =
  "(?:" +
  "(?<d>\\d{1,3})(?:\\s*°\\s*|\\s+)" +
  "(?<m>\\d{1,2}(?:[.,]\\d+)?)(?:\\s*'\\s*|\\s+|(?=\\s*[NSEWnsew,/]|\\s*$))" +
  "(?:(?<s>\\d{1,2}(?:[.,]\\d+)?)\\s*(?:\"|'')?)?" +
  "|" +
  "(?<dd>\\d{1,3}(?:[.,]\\d+)?)\\s*°?" +
  ")";
const COMPONENT_SUFFIX = new RegExp(`${SIGN}${BODY}\\s*(?<h>[NSEWnsew])?(?![A-Za-z])`, "g");
const COMPONENT_PREFIX = new RegExp(`(?<h>[NSEWnsew])?\\s*${SIGN}${BODY}`, "g");
const SEPARATOR = /^[\s,/]*$/;

type Axis = "lat" | "lon";
interface Component {
  value: number;
  axis: Axis | null;
}
interface RawPoint {
  line: number;
  text: string;
  first: Component;
  second: Component;
  geojsonOrder: boolean;
}

class ParseError extends Error {
  constructor(
    message: string,
    readonly line: number | null,
  ) {
    super(message);
  }
}

const toNumber = (text: string) => Number.parseFloat(text.replace(",", "."));

function decimalPair(text: string): [number, number, boolean] | null {
  const dot = DOT_PAIR.exec(text);
  if (dot) return [Number.parseFloat(dot[1] as string), Number.parseFloat(dot[2] as string), false];
  const comma = COMMA_PAIR.exec(text);
  if (comma && ((comma[1] as string).includes(",") || (comma[2] as string).includes(","))) {
    return [toNumber(comma[1] as string), toNumber(comma[2] as string), true];
  }
  return null;
}

function component(match: RegExpMatchArray, line: number, text: string): Component {
  const g = match.groups ?? {};
  let sign = g.sign === "-" ? -1 : 1;
  let value: number;
  if (g.dd !== undefined) {
    value = toNumber(g.dd);
  } else {
    const minutes = toNumber(g.m ?? "0");
    const seconds = g.s !== undefined ? toNumber(g.s) : 0;
    if (minutes >= 60 || seconds >= 60) throw new ParseError(`Line ${line}: minutes and seconds must be below 60 in '${text}'. ${FORMAT_HINT}`, line);
    if (g.s !== undefined && !Number.isInteger(minutes)) {
      throw new ParseError(`Line ${line}: minutes with decimals cannot be followed by seconds in '${text}'.`, line);
    }
    value = Number.parseFloat(g.d ?? "0") + minutes / 60 + seconds / 3600;
  }
  const hemisphere = (g.h ?? "").toUpperCase();
  if (hemisphere && g.sign) throw new ParseError(`Line ${line}: use either a minus sign or a hemisphere letter, not both, in '${text}'.`, line);
  let axis: Axis | null = null;
  if (hemisphere) {
    axis = hemisphere === "N" || hemisphere === "S" ? "lat" : "lon";
    if (hemisphere === "S" || hemisphere === "W") sign = -1;
  }
  return { value: sign * value, axis };
}

function dmsComponents(text: string, line: number): Component[] {
  const first = text.trimStart().charAt(0).toUpperCase();
  const pattern = first === "N" || first === "S" || first === "E" || first === "W" ? COMPONENT_PREFIX : COMPONENT_SUFFIX;
  const out: Component[] = [];
  let position = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (match[0].length === 0) continue;
    if (!SEPARATOR.test(text.slice(position, start))) break;
    out.push(component(match, line, text));
    position = start + match[0].length;
  }
  if (out.length === 0 || !SEPARATOR.test(text.slice(position))) throw new ParseError(`Line ${line}: could not read '${text}'. ${FORMAT_HINT}`, line);
  return out;
}

function rawPoint(line: number, text: string): [RawPoint, boolean] {
  // Whitespace runs read like one space; collapsing them first keeps the overlapping \s* of the
  // patterns from backtracking polynomially on long runs (as airbrief_core.geo.parse does).
  let body = text
    .replace(NORMALISE_RE, (ch) => NORMALISE[ch] ?? ch)
    .replace(/''/g, '"')
    .replace(/\s+/g, " ")
    .trim();
  let geojsonOrder = false;
  if (body.startsWith("[") && body.endsWith("]")) {
    body = body.slice(1, -1).trim();
    geojsonOrder = true;
  } else if (body.startsWith("(") && body.endsWith(")")) {
    body = body.slice(1, -1).trim();
  }
  const pair = /[°'"A-Za-z]/.test(body) ? null : decimalPair(body);
  if (pair) {
    const [a, b, comma] = pair;
    return [{ line, text, first: { value: a, axis: null }, second: { value: b, axis: null }, geojsonOrder }, comma];
  }
  const letters = new Set((body.match(/[A-Za-z]/g) ?? []).map((c) => c.toUpperCase()));
  for (const l of letters) if (!"NSEW".includes(l)) throw new ParseError(`Line ${line}: unexpected text in '${text}'. ${FORMAT_HINT}`, line);
  if (AMBIGUOUS_COMMAS.test(body)) {
    throw new ParseError(
      `Line ${line}: '${text}' can be read two ways: which comma is the decimal comma? Put a space after the comma between the two values (e.g. '12,5, 77') or use decimal points.`,
      line,
    );
  }
  const parts = dmsComponents(body, line);
  if (parts.length !== 2) {
    throw new ParseError(
      `Line ${line}: a point needs exactly two values (latitude and longitude), found ${parts.length} in '${text}'. Separate points with new lines or ';'.`,
      line,
    );
  }
  return [{ line, text, first: parts[0] as Component, second: parts[1] as Component, geojsonOrder }, /\d,\d/.test(body)];
}

function orderVote(p: RawPoint): "latlon" | "lonlat" | null {
  const a = Math.abs(p.first.value);
  const b = Math.abs(p.second.value);
  if (a > 90 && b > 90) throw new ParseError(`Line ${p.line}: neither value of '${p.text}' can be a latitude (both are beyond ±90°).`, p.line);
  if (a > 90) return "lonlat";
  if (b > 90) return "latlon";
  return null;
}

/** Points typed as text, as `[lon, lat]`, with notes on how they were read. */
export function parseCoordinateText(text: string): CoordinateParse {
  try {
    const parts: [number, string][] = [];
    // The same line breaks as the API (`_LINE_BREAK` in parse.py), so line numbers match.
    text.split(/\r\n|[\n\r\v\f\u0085\u2028\u2029]/).forEach((lineText, i) => {
      for (const part of lineText.split(";")) if (part.trim()) parts.push([i + 1, part.trim()]);
    });
    if (parts.length === 0) return { ok: false, error: `Type at least one point. ${FORMAT_HINT}`, line: null };
    if (parts.length > MAX_TYPED_POINTS) {
      return { ok: false, error: `At most ${MAX_TYPED_POINTS.toLocaleString("en-US")} points can be typed or pasted; upload a file instead.`, line: null };
    }
    const raw: RawPoint[] = [];
    let decimalComma = false;
    for (const [line, part] of parts) {
      const [point, comma] = rawPoint(line, part);
      raw.push(point);
      decimalComma ||= comma;
    }
    const votes = new Map<"latlon" | "lonlat", RawPoint>();
    const undecided = raw.filter((p) => p.first.axis === null && p.second.axis === null && !p.geojsonOrder);
    for (const p of undecided) {
      const vote = orderVote(p);
      if (vote && !votes.has(vote)) votes.set(vote, p);
    }
    if (votes.size > 1) {
      const ll = votes.get("latlon") as RawPoint;
      const lo = votes.get("lonlat") as RawPoint;
      return {
        ok: false,
        error: `Points mix the two orders: '${ll.text}' (line ${ll.line}) is latitude, longitude but '${lo.text}' (line ${lo.line}) is longitude, latitude. Use one order for every point.`,
        line: lo.line,
      };
    }
    const order = votes.has("lonlat") ? "lonlat" : "latlon";
    const points: LonLat[] = [];
    for (const p of raw) {
      const { first, second } = p;
      let lat: number;
      let lon: number;
      if (first.axis && second.axis) {
        if (first.axis === second.axis) {
          const word = first.axis === "lat" ? "latitudes (N/S)" : "longitudes (E/W)";
          throw new ParseError(`Line ${p.line}: '${p.text}' has two ${word}; one value must be N/S and the other E/W.`, p.line);
        }
        [lat, lon] = first.axis === "lat" ? [first.value, second.value] : [second.value, first.value];
      } else if (first.axis || second.axis) {
        const [known, other] = first.axis ? [first, second] : [second, first];
        [lat, lon] = known.axis === "lat" ? [known.value, other.value] : [other.value, known.value];
      } else if (p.geojsonOrder || order === "lonlat") {
        [lon, lat] = [first.value, second.value];
      } else {
        [lat, lon] = [first.value, second.value];
      }
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) throw new ParseError(`Line ${p.line}: '${p.text}' is not a number.`, p.line);
      if (Math.abs(lat) > 90) throw new ParseError(`Line ${p.line}: latitude ${lat}° is outside −90° to 90°.`, p.line);
      if (Math.abs(lon) > 180) throw new ParseError(`Line ${p.line}: longitude ${lon}° is outside −180° to 180°.`, p.line);
      points.push([lon, lat]);
    }
    const notes: CoordinateNote[] = [];
    if (undecided.length > 0 && votes.size === 0) {
      notes.push({
        code: "ambiguous_order",
        severity: "warning",
        message: "Both values fit either order, so they were read as latitude, longitude. If you meant longitude, latitude, swap them or add N/S and E/W letters.",
      });
    } else if (votes.has("lonlat")) {
      notes.push({ code: "lonlat_order", severity: "info", message: "Read as longitude, latitude: a first value beyond ±90° can only be a longitude." });
    }
    if (raw.some((p) => p.geojsonOrder)) {
      notes.push({ code: "lonlat_order", severity: "info", message: "Values in [square brackets] were read in GeoJSON order: longitude, latitude." });
    }
    if (decimalComma) notes.push({ code: "decimal_comma", severity: "info", message: "A comma between digits was read as a decimal point." });
    return { ok: true, points, notes };
  } catch (error) {
    if (error instanceof ParseError) return { ok: false, error: error.message, line: error.line };
    throw error;
  }
}

/** Swap the two values of every typed point (for "Swap latitude and longitude"). */
export function swapCoordinateText(text: string): string {
  return text
    .split(/\r\n|\r|\n/)
    .map((line) =>
      line
        .split(";")
        .map((part) => {
          const m = /^(\s*)([^,\s]+)(\s*,\s*|\s+)([^,\s]+)(\s*)$/.exec(part);
          return m ? `${m[1]}${m[4]}${m[3]}${m[2]}${m[5]}` : part;
        })
        .join(";"),
    )
    .join("\n");
}

/** `"12.97160° N, 77.59460° E"`: latitude first, hemisphere letters (as the API labels places). */
export function formatLatLon(lon: number, lat: number, decimals = 5): string {
  return `${Math.abs(lat).toFixed(decimals)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(decimals)}° ${lon >= 0 ? "E" : "W"}`;
}
