/**
 * Number and unit formatting (metric | imperial). Inputs are always SI (m, m², s, m/s).
 *
 * Every `formatX` returns a display string such as "1.25 km"; null/undefined/NaN become "—".
 * Every `xParts` returns `{ value, unit, text }` so KPI tiles can style the unit separately.
 * A fixed locale keeps the server and client renders identical.
 */

import type { Units } from "./store";

export const LOCALE = "en-US";
export const EMPTY = "—";

export interface FormattedValue {
  /** the number, formatted (or "—") */
  value: string;
  /** unit symbol, "" when unitless or empty */
  unit: string;
  /** value and unit joined with a thin space */
  text: string;
}

export interface NumberOptions {
  /** fixed number of decimals (overrides the automatic choice) */
  decimals?: number;
  /** at most this many decimals (default: automatic by magnitude) */
  maximumFractionDigits?: number;
  /** compact notation: 12.3K, 4.5M */
  compact?: boolean;
  /** always show the sign (+/−) */
  signed?: boolean;
}

const M_PER_FT = 0.3048;
const M_PER_MI = 1609.344;
const M2_PER_FT2 = M_PER_FT * M_PER_FT;
const M2_PER_ACRE = 4046.8564224;
const M2_PER_MI2 = M_PER_MI * M_PER_MI;
const MS_PER_KMH = 1 / 3.6;
const MS_PER_MPH = 0.44704;
const MS_PER_KN = 1852 / 3600;

const nfCache = new Map<string, Intl.NumberFormat>();

function nf(options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = JSON.stringify(options);
  let f = nfCache.get(key);
  if (!f) {
    f = new Intl.NumberFormat(LOCALE, options);
    nfCache.set(key, f);
  }
  return f;
}

function isNum(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function autoDecimals(abs: number): number {
  if (abs === 0) return 0;
  if (abs < 1) return 3;
  if (abs < 10) return 2;
  if (abs < 100) return 1;
  return 0;
}

export function formatNumber(value: number | null | undefined, options: NumberOptions = {}): string {
  if (!isNum(value)) return EMPTY;
  const opts: Intl.NumberFormatOptions = {};
  if (options.compact) {
    opts.notation = "compact";
    opts.maximumFractionDigits = options.maximumFractionDigits ?? 1;
  } else if (options.decimals !== undefined) {
    opts.minimumFractionDigits = options.decimals;
    opts.maximumFractionDigits = options.decimals;
  } else {
    opts.maximumFractionDigits = options.maximumFractionDigits ?? autoDecimals(Math.abs(value));
  }
  if (options.signed) opts.signDisplay = "exceptZero";
  // Use a real minus sign for legibility.
  return nf(opts).format(value).replace("-", "−");
}

/**
 * A formatted count with its noun, singular for exactly one: `plural(1, "feature")` → "1 feature",
 * `plural(50_000, "feature")` → "50,000 features", `plural(2, "battery", "batteries")`.
 */
export function plural(count: number, one: string, many = `${one}s`): string {
  return `${formatNumber(count, { maximumFractionDigits: 0 })} ${count === 1 ? one : many}`;
}

function parts(value: number | null | undefined, unit: string, options: NumberOptions = {}): FormattedValue {
  const v = formatNumber(value, options);
  if (v === EMPTY) return { value: EMPTY, unit: "", text: EMPTY };
  return { value: v, unit, text: unit ? `${v} ${unit}` : v };
}

/** Area in m² → m² / ha / km² (metric) or ft² / ac / mi² (imperial). */
export function areaParts(m2: number | null | undefined, units: Units = "metric", options: NumberOptions = {}): FormattedValue {
  if (!isNum(m2)) return parts(null, "");
  const abs = Math.abs(m2);
  if (units === "imperial") {
    if (abs < M2_PER_ACRE) return parts(m2 / M2_PER_FT2, "ft²", options);
    if (abs < 640 * M2_PER_ACRE) return parts(m2 / M2_PER_ACRE, "ac", options);
    return parts(m2 / M2_PER_MI2, "mi²", options);
  }
  if (abs < 10_000) return parts(m2, "m²", options);
  if (abs < 1_000_000) return parts(m2 / 10_000, "ha", options);
  return parts(m2 / 1_000_000, "km²", options);
}

export function formatArea(m2: number | null | undefined, units: Units = "metric", options?: NumberOptions): string {
  return areaParts(m2, units, options).text;
}

/** Length in m → m / km (metric) or ft / mi (imperial). */
export function lengthParts(m: number | null | undefined, units: Units = "metric", options: NumberOptions = {}): FormattedValue {
  if (!isNum(m)) return parts(null, "");
  const abs = Math.abs(m);
  if (units === "imperial") {
    if (abs < M_PER_MI) return parts(m / M_PER_FT, "ft", options);
    return parts(m / M_PER_MI, "mi", options);
  }
  if (abs < 1000) return parts(m, "m", options);
  return parts(m / 1000, "km", options);
}

export function formatLength(m: number | null | undefined, units: Units = "metric", options?: NumberOptions): string {
  return lengthParts(m, units, options).text;
}

/** Height or altitude in m → m (metric) or ft (imperial), never km / mi. Defaults to whole units. */
export function heightParts(m: number | null | undefined, units: Units = "metric", options: NumberOptions = {}): FormattedValue {
  const opts: NumberOptions = { maximumFractionDigits: 0, ...options };
  if (units === "imperial") return parts(isNum(m) ? m / M_PER_FT : null, "ft", opts);
  return parts(m, "m", opts);
}

export function formatHeight(m: number | null | undefined, units: Units = "metric", options?: NumberOptions): string {
  return heightParts(m, units, options).text;
}

export type SpeedUnit = "ms" | "kmh" | "mph" | "kn";

/**
 * Speed in m/s → m/s (metric) or mph (imperial). Pass `as` to force km/h, mph or kn
 * (for example eVTOL cruise speeds in km/h).
 */
export function speedParts(
  ms: number | null | undefined,
  units: Units = "metric",
  options: NumberOptions & { as?: SpeedUnit } = {},
): FormattedValue {
  const target: SpeedUnit = options.as ?? (units === "imperial" ? "mph" : "ms");
  const v = isNum(ms) ? ms : null;
  const opts: NumberOptions = { maximumFractionDigits: 1, ...options };
  switch (target) {
    case "kmh":
      return parts(v === null ? null : v / MS_PER_KMH, "km/h", opts);
    case "mph":
      return parts(v === null ? null : v / MS_PER_MPH, "mph", opts);
    case "kn":
      return parts(v === null ? null : v / MS_PER_KN, "kn", opts);
    default:
      return parts(v, "m/s", opts);
  }
}

export function formatSpeed(
  ms: number | null | undefined,
  units: Units = "metric",
  options?: NumberOptions & { as?: SpeedUnit },
): string {
  return speedParts(ms, units, options).text;
}

/**
 * Percent. By default `value` is already in percent (e.g. `deviation_pct`, overlaps).
 * Pass `{ fraction: true }` for 0..1 values (progress, weights).
 */
export function percentParts(
  value: number | null | undefined,
  options: NumberOptions & { fraction?: boolean } = {},
): FormattedValue {
  if (!isNum(value)) return parts(null, "");
  const pct = options.fraction ? value * 100 : value;
  const abs = Math.abs(pct);
  const auto = abs === 0 ? 0 : abs < 0.1 ? 3 : abs < 10 ? 2 : abs < 100 ? 1 : 0;
  const v = formatNumber(pct, { maximumFractionDigits: auto, ...options });
  return { value: v, unit: "%", text: `${v}%` };
}

export function formatPercent(value: number | null | undefined, options?: NumberOptions & { fraction?: boolean }): string {
  return percentParts(value, options).text;
}

/** Seconds → "12.3 s", "4 min 05 s", "1 h 07 min". Above an hour, `precise` keeps the seconds. */
export function formatDuration(seconds: number | null | undefined, options: { precise?: boolean } = {}): string {
  if (!isNum(seconds)) return EMPTY;
  const sign = seconds < 0 ? "−" : "";
  const s = Math.abs(seconds);
  if (s < 60) {
    return `${sign}${formatNumber(s, { maximumFractionDigits: s < 10 ? 1 : 0 })} s`;
  }
  const totalSec = Math.round(s);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const sec = totalSec % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  if (h === 0) return `${sign}${m} min ${pad(sec)} s`;
  if (options.precise) return `${sign}${h} h ${pad(m)} min ${pad(sec)} s`;
  return `${sign}${h} h ${pad(m)} min`;
}

/** Playback clock: seconds → "m:ss.s" (or "h:mm:ss" above an hour). */
export function formatClock(seconds: number | null | undefined): string {
  if (!isNum(seconds)) return EMPTY;
  const s = Math.max(0, seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(Math.floor(sec)).padStart(2, "0")}`;
  return `${m}:${sec.toFixed(1).padStart(4, "0")}`;
}

/** Bytes → "512 B", "1.4 MB". */
export function formatBytes(bytes: number | null | undefined): string {
  if (!isNum(bytes)) return EMPTY;
  const unitsList = ["B", "KB", "MB", "GB"];
  let v = bytes;
  let i = 0;
  while (Math.abs(v) >= 1024 && i < unitsList.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${formatNumber(v, { maximumFractionDigits: i === 0 ? 0 : 1 })} ${unitsList[i]}`;
}

export interface CoordOptions {
  /** "dd" decimal degrees (default) or "dms" degrees-minutes-seconds */
  format?: "dd" | "dms";
  /** decimals for dd (default 5 ≈ 1 m) or for the seconds in dms (default 1) */
  precision?: number;
  /** "latlon" (default, human order) or "lonlat" (GeoJSON order) */
  order?: "latlon" | "lonlat";
}

function dms(value: number, positive: string, negative: string, precision: number): string {
  const hemi = value >= 0 ? positive : negative;
  const abs = Math.abs(value);
  let d = Math.floor(abs);
  let m = Math.floor((abs - d) * 60);
  let s = (abs - d - m / 60) * 3600;
  if (Number(s.toFixed(precision)) >= 60) {
    s = 0;
    m += 1;
  }
  if (m >= 60) {
    m = 0;
    d += 1;
  }
  return `${d}°${String(m).padStart(2, "0")}′${s.toFixed(precision).padStart(precision > 0 ? precision + 3 : 2, "0")}″${hemi}`;
}

/** `(lon, lat)` → "12.97160° N, 77.59460° E" (or DMS). */
export function formatCoord(lon: number | null | undefined, lat: number | null | undefined, options: CoordOptions = {}): string {
  if (!isNum(lon) || !isNum(lat)) return EMPTY;
  const format = options.format ?? "dd";
  let latText: string;
  let lonText: string;
  if (format === "dms") {
    const p = options.precision ?? 1;
    latText = dms(lat, "N", "S", p);
    lonText = dms(lon, "E", "W", p);
  } else {
    const p = options.precision ?? 5;
    latText = `${Math.abs(lat).toFixed(p)}° ${lat >= 0 ? "N" : "S"}`;
    lonText = `${Math.abs(lon).toFixed(p)}° ${lon >= 0 ? "E" : "W"}`;
  }
  return options.order === "lonlat" ? `${lonText}, ${latText}` : `${latText}, ${lonText}`;
}

/** Bearing in degrees → "045°". */
export function formatBearing(deg: number | null | undefined): string {
  if (!isNum(deg)) return EMPTY;
  const d = ((Math.round(deg) % 360) + 360) % 360;
  return `${String(d).padStart(3, "0")}°`;
}

export interface DateOptions {
  /** include the time of day (default true) */
  withTime?: boolean;
  /** show in UTC and suffix "UTC" (default false: local time) */
  utc?: boolean;
}

/** ISO string / epoch ms / Date → "8 Oct 2026, 14:05" (local) or "... UTC". */
export function formatDate(value: string | number | Date | null | undefined, options: DateOptions = {}): string {
  if (value === null || value === undefined || value === "") return EMPTY;
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return EMPTY;
  const withTime = options.withTime ?? true;
  const fmt = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
    ...(options.utc ? { timeZone: "UTC" } : {}),
  });
  const text = fmt.format(date);
  return options.utc && withTime ? `${text} UTC` : text;
}

/** "3 min ago", "2 h ago", "yesterday"; falls back to formatDate after a week. */
export function formatRelative(value: string | number | Date | null | undefined, now: number = Date.now()): string {
  if (value === null || value === undefined || value === "") return EMPTY;
  const date = value instanceof Date ? value : new Date(value);
  const t = date.getTime();
  if (Number.isNaN(t)) return EMPTY;
  const diff = Math.round((now - t) / 1000);
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86_400) return `${Math.round(diff / 3600)} h ago`;
  if (diff < 172_800) return "yesterday";
  if (diff < 604_800) return `${Math.round(diff / 86_400)} days ago`;
  return formatDate(date, { withTime: false });
}

/**
 * How a form input declared in a metric unit ("m", "km/h") is shown and typed in the user's unit
 * system. `NumField` (components/modules/shared.tsx) gets it from `inputUnit`.
 */
export interface InputUnit {
  /** unit symbol shown in the label and inside the input */
  unit: string;
  /** declared-unit value → display value */
  toDisplay: (value: number) => number;
  /** display value → declared-unit value (what the form state and the API keep) */
  fromDisplay: (value: number) => number;
}

/**
 * Imperial equivalents of the metric units used by form inputs, with the size of one imperial
 * unit in the metric one. Accelerations (m/s²), camera units (mm, cm/px) and densities stay
 * metric, as they do in the results.
 */
const IMPERIAL_INPUT_UNITS: Readonly<Record<string, { unit: string; size: number }>> = {
  m: { unit: "ft", size: M_PER_FT },
  km: { unit: "mi", size: M_PER_MI / 1000 },
  "m/s": { unit: "mph", size: MS_PER_MPH },
  "km/h": { unit: "mph", size: MS_PER_MPH / MS_PER_KMH },
  "m²": { unit: "ft²", size: M2_PER_FT2 },
  ha: { unit: "ac", size: M2_PER_ACRE / 10_000 },
  "km²": { unit: "mi²", size: M2_PER_MI2 / 1_000_000 },
};

/** The display unit of an input declared in `unit`, or null when it is shown as declared. */
export function inputUnit(unit: string | undefined, units: Units): InputUnit | null {
  if (!unit || units !== "imperial") return null;
  const entry = IMPERIAL_INPUT_UNITS[unit];
  if (!entry) return null;
  return { unit: entry.unit, toDisplay: (value) => value / entry.size, fromDisplay: (value) => value * entry.size };
}

/** Decimals of a step such as 0.25 (2) or 5 (0). */
export function stepDecimals(step: number | undefined): number {
  if (step === undefined || !Number.isFinite(step)) return 0;
  const text = String(step);
  const exp = /e-(\d+)$/i.exec(text);
  if (exp) return Number(exp[1]);
  const dot = text.indexOf(".");
  return dot === -1 ? 0 : text.length - dot - 1;
}

/** Decimals for a converted input value: about three significant digits, at least the step's. */
export function inputDecimals(value: number, step?: number): number {
  const abs = Math.abs(value);
  const auto = abs === 0 || abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  return Math.max(auto, stepDecimals(step));
}

/** Round to a number of decimals without binary noise (0.1 + 0.2 → 0.3). */
export function roundTo(value: number, decimals: number): number {
  return Number(value.toFixed(Math.min(Math.max(decimals, 0), 12)));
}

/**
 * A number typed in an input: a value, an empty field, an entry that is still being typed
 * ("1,200," on the way to "1,200,000") or an error. `message` says what is wrong: for a
 * partial entry, it applies once the user leaves the field without finishing it.
 */
export type ParsedNumberText =
  | { kind: "number"; value: number }
  | { kind: "empty" }
  | { kind: "partial"; message: string }
  | { kind: "invalid"; message: string };

const PLAIN_NUMBER = /^-?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i;
/** 1,200 or 12,345.6: commas between groups of three digits (no leading zero); "1,200." too, like "12.". */
const COMMA_GROUPS = /^-?[1-9]\d{0,2}(,\d{3})+(\.\d*)?$/;
/** 1,00,000 or 12,34,567.5: Indian grouping (lakh, crore), two digits per group above the thousands. */
const LAKH_GROUPS = /^-?[1-9]\d?(,\d{2})+,\d{3}(\.\d*)?$/;
/** Separators between digit groups other than a comma: space, no-break space, narrow no-break space. */
const SPACE_SEPARATOR = /[   ]/g;
/** 1 200 with a space, no-break or narrow no-break space between groups of three digits. */
const SPACE_GROUPS = /^-?[1-9]\d{0,2}([   ]\d{3})+(\.\d*)?$/;
/**
 * 2,5 or 0,25: one comma as the decimal point, followed by one or two digits. With three digits
 * it could be a thousands separator ("0,500"), and longer runs are more likely a mistyped
 * thousands group ("10,0000") than a decimal, so both are errors.
 */
const DECIMAL_COMMA = /^-?\d*,\d{1,2}$/;
/** Entries that can still become a number: "-", ".", "12,", "1,200,0", "1,00,00", "1 2" (on the way to "1 200"), "1e". */
const PARTIAL_NUMBER = /^-?(\.|\d*(,\d+)*,\d{0,2}|[1-9]\d{0,2}([ \u00a0\u202f]\d{3})*[ \u00a0\u202f]\d{0,2}|\d+\.?\d*e[+-]?)?$/i;

export const NUMBER_TEXT_HINT = "Enter a number, for example 1200 or 2.5.";
export const NUMBER_COMMA_HINT = "Use a dot for decimals (2.5) and commas only between thousands (1,200).";

/**
 * Read a typed number. Thousands separators are accepted ("1,200", "1 200" and "1,00,000") and
 * a lone comma is a decimal point when it cannot be a thousands separator ("2,5" → 2.5).
 * Anything ambiguous ("1234,567", "0,500", "1,2,3") is an error, never a guess.
 */
export function parseNumberText(text: string): ParsedNumberText {
  const t = text.trim().replace(/[−–]/g, "-");
  if (t === "") return { kind: "empty" };
  const message = t.includes(",") ? NUMBER_COMMA_HINT : NUMBER_TEXT_HINT;
  let normalized: string | null = null;
  if (PLAIN_NUMBER.test(t)) normalized = t;
  else if (COMMA_GROUPS.test(t) || LAKH_GROUPS.test(t)) normalized = t.replace(/,/g, "");
  else if (SPACE_GROUPS.test(t)) normalized = t.replace(SPACE_SEPARATOR, "");
  else if (DECIMAL_COMMA.test(t)) normalized = t.replace(",", ".");
  if (normalized === null) return PARTIAL_NUMBER.test(t) ? { kind: "partial", message } : { kind: "invalid", message };
  const value = Number(normalized);
  return Number.isFinite(value) ? { kind: "number", value } : { kind: "invalid", message: NUMBER_TEXT_HINT };
}

/** A number followed by a metric unit, not inside a word or a longer number ("5 min", "8.8 mm" do not match). */
const QUANTITY_IN_TEXT = /(^|[^\w.,])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)\s?(m\/s²|m\/s|km\/h|km²|m²|km|ha|m)(?![\w²³/])/g;

/**
 * Metric quantities inside a sentence written by the API ("allows 120 m AGL within 300 m"), in
 * the given units. Lengths become ft / mi, areas ft² / ac / mi² and speeds mph; accelerations,
 * camera units and other text stay as written. Metric text is returned unchanged.
 */
export function localizeUnits(text: string, units: Units): string {
  if (units !== "imperial") return text;
  return text.replace(QUANTITY_IN_TEXT, (match: string, lead: string, num: string, unit: string) => {
    const v = Number(num.replace(/,/g, ""));
    if (!Number.isFinite(v)) return match;
    switch (unit) {
      case "m":
        return `${lead}${formatLength(v, units)}`;
      case "km":
        return `${lead}${formatLength(v * 1000, units)}`;
      case "m²":
        return `${lead}${formatArea(v, units)}`;
      case "ha":
        return `${lead}${formatArea(v * 10_000, units)}`;
      case "km²":
        return `${lead}${formatArea(v * 1_000_000, units)}`;
      case "m/s":
        return `${lead}${formatSpeed(v, units)}`;
      case "km/h":
        return `${lead}${formatSpeed(v * MS_PER_KMH, units)}`;
      default:
        return match;
    }
  });
}

const METHOD_LABELS: Record<string, string> = {
  "local-projection": "Local projection",
};

/** Readable name of a measurement method (e.g. "local-projection" → "Local projection"). */
export function formatMethod(method: string | null | undefined): string {
  if (!method) return EMPTY;
  const known = METHOD_LABELS[method];
  if (known) return known;
  const words = method.replace(/[-_]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const PROJ_NAMES: Record<string, string> = {
  laea: "Local equal-area (LAEA)",
  tmerc: "Local Transverse Mercator",
  aeqd: "Local azimuthal equidistant",
  utm: "UTM",
};

/**
 * A readable description of a CRS as the API reports it: an authority code ("EPSG:32643") is
 * kept, a PROJ string ("+proj=laea +lat_0=12.96 +lon_0=77.56 …") becomes "Local equal-area
 * (LAEA) at 12.96° N, 77.56° E". Unknown strings are returned unchanged.
 */
export function describeProjection(crs: string | null | undefined): string {
  if (!crs) return EMPTY;
  const text = crs.trim();
  if (!text.startsWith("+proj=")) return text;
  const params = new Map<string, string>();
  for (const part of text.split(/\s+/)) {
    const [key, value] = part.replace(/^\+/, "").split("=");
    if (key) params.set(key, value ?? "");
  }
  const proj = params.get("proj") ?? "";
  const name = PROJ_NAMES[proj] ?? `PROJ ${proj}`;
  if (proj === "utm") {
    const zone = params.get("zone");
    return zone ? `UTM zone ${zone}${params.has("south") ? "S" : "N"}` : name;
  }
  const lat = Number.parseFloat(params.get("lat_0") ?? "");
  const lon = Number.parseFloat(params.get("lon_0") ?? "");
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return name;
  const latText = `${formatNumber(Math.abs(lat), { maximumFractionDigits: 2 })}° ${lat >= 0 ? "N" : "S"}`;
  const lonText = `${formatNumber(Math.abs(lon), { maximumFractionDigits: 2 })}° ${lon >= 0 ? "E" : "W"}`;
  return `${name} at ${latText}, ${lonText}`;
}

/** Minutes east of UTC → "UTC+05:30" / "UTC−03:30" (the zone label of local times). */
export function formatUtcOffset(minutes: number | null | undefined): string {
  if (!isNum(minutes)) return EMPTY;
  const whole = Math.round(minutes);
  const abs = Math.abs(whole);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `UTC${whole < 0 ? "−" : "+"}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** A land-area unit for field work; the user's choice lives in `lib/area-units.ts`. */
export type LocalAreaUnit = "ha" | "acre" | "guntha" | "bigha";

export const LOCAL_AREA_UNITS: readonly LocalAreaUnit[] = ["ha", "acre", "guntha", "bigha"];

/** 1 guntha = 1/40 acre = 121 square yards = 1,089 ft² ≈ 101.17 m² (western and southern India). */
export const M2_PER_GUNTHA = M2_PER_ACRE / 40;

/** One square foot in m² (bigha sizes are usually quoted in square feet). */
export const M2_PER_SQ_FT = M2_PER_FT2;

export const LOCAL_AREA_UNIT_LABEL: Readonly<Record<LocalAreaUnit, string>> = {
  ha: "Hectare",
  acre: "Acre",
  guntha: "Guntha",
  bigha: "Bigha",
};

const LOCAL_AREA_SYMBOL: Readonly<Record<LocalAreaUnit, string>> = { ha: "ha", acre: "ac", guntha: "guntha", bigha: "bigha" };

/**
 * Bigha sizes as commonly quoted, each from the customary units it is defined in (they vary by
 * state and even district: check the local land record). The user can type any size in Settings.
 * - West Bengal / Assam: 1 bigha = 20 katha (cottah) × 720 ft² = 14,400 ft² ≈ 1,337.8 m²;
 * - Gujarat: 1 bigha = 16 guntha × 1,089 ft² = 17,424 ft² ≈ 1,618.7 m² (0.4 acre);
 * - Rajasthan, pucca bigha: 3,025 square yards = 27,225 ft² ≈ 2,529.3 m².
 */
export const BIGHA_PRESETS: readonly { id: string; label: string; m2: number }[] = [
  { id: "wb_assam", label: "West Bengal / Assam (14,400 ft²)", m2: 14_400 * M2_PER_FT2 },
  { id: "gujarat", label: "Gujarat (16 guntha, 17,424 ft²)", m2: 16 * (M2_PER_ACRE / 40) },
  { id: "rajasthan_pucca", label: "Rajasthan, pucca (27,225 ft²)", m2: 27_225 * M2_PER_FT2 },
];

/** Square metres in one `unit` (the bigha size is the user's). */
export function localAreaSize(unit: LocalAreaUnit, bighaM2: number): number {
  switch (unit) {
    case "ha":
      return 10_000;
    case "acre":
      return M2_PER_ACRE;
    case "guntha":
      return M2_PER_GUNTHA;
    case "bigha":
      return bighaM2;
  }
}

/** An area in m² in a local unit: 20,234 m² → "5 ac", "200 guntha", "15.12 bigha". */
export function localAreaParts(
  m2: number | null | undefined,
  unit: LocalAreaUnit,
  bighaM2: number,
  options: NumberOptions = {},
): FormattedValue {
  const size = localAreaSize(unit, bighaM2);
  if (!isNum(m2) || !(size > 0)) return parts(null, "");
  return parts(m2 / size, LOCAL_AREA_SYMBOL[unit], { maximumFractionDigits: 2, ...options });
}

export function formatLocalArea(m2: number | null | undefined, unit: LocalAreaUnit, bighaM2: number, options?: NumberOptions): string {
  return localAreaParts(m2, unit, bighaM2, options).text;
}
