/**
 * One result per feature (airbrief_core/batch.py). Verdict labels and tones come from the result
 * (`counts`), so the API, the CSV and the brief name them the same way.
 */

import { CATEGORICAL, toneVar } from "./colors";
import { EMI_KIND_LABEL } from "./labels";
import { areaParts, formatDuration, formatHeight, formatLength, formatNumber, formatPercent, formatSpeed } from "./format";
import type { Units } from "./store";
import type { AnalysisInfo, AnalysisKind, BatchItem, BatchResult, DatasetFeatureCollection, EmiKind } from "./types";

export const BATCH_KINDS: readonly AnalysisKind[] = ["airspace", "ground_risk", "survey", "environment", "line_of_sight"];

/** As airbrief_core/models/batch.py BATCH_FEATURE_LIMITS. */
export function batchLimit(kind: AnalysisKind): number {
  // LOS items are slow with a far observer (a 400 × 400 viewshed each)
  return kind === "line_of_sight" ? 500 : 1000;
}

export function isBatchResult(result: unknown): result is BatchResult {
  return typeof result === "object" && result !== null && (result as { batch?: unknown }).batch === "per_feature";
}

/** A per-feature analysis, finished or not (its parameters say so before the result exists). */
export function isBatchAnalysis(analysis: AnalysisInfo | null | undefined): boolean {
  if (!analysis) return false;
  const batch = analysis.params?.batch as { mode?: unknown } | null | undefined;
  return isBatchResult(analysis.result) || (typeof batch === "object" && batch !== null && batch.mode === "per_feature");
}

export function batchResult(analysis: AnalysisInfo | null | undefined): BatchResult | null {
  return analysis?.status === "COMPLETED" && isBatchResult(analysis.result) ? analysis.result : null;
}

/** CSS colour of each verdict: its tone's, or one categorical colour each when none has a tone (survey). */
export function batchVerdictColors(result: BatchResult): Map<string, string> {
  const ordinal = result.counts.every((c) => c.tone === "neutral");
  return new Map(result.counts.map((c, i) => [c.verdict, ordinal ? (CATEGORICAL[i % CATEGORICAL.length] as string) : toneVar(c.tone)]));
}

/** The `params.batch` a panel sends, or nothing when the toggle is off. */
export function batchParams(on: boolean, keyProperty: string | null): { batch?: { mode: "per_feature"; key_property: string | null } } {
  return on ? { batch: { mode: "per_feature", key_property: keyProperty } } : {};
}

/** The toggle state from stored parameters (`useFormFromAnalysis`). */
export function batchFromParams(p: Readonly<Record<string, unknown>>): { on: boolean; keyProperty: string | null } {
  const raw = p.batch;
  if (typeof raw !== "object" || raw === null) return { on: false, keyProperty: null };
  const key = (raw as { key_property?: unknown }).key_property;
  return { on: true, keyProperty: typeof key === "string" && key ? key : null };
}

/** Attribute names of the dataset (the `_` system properties left out), for the key choice. */
export function attributeNames(features: DatasetFeatureCollection | null): string[] {
  const names = new Set<string>();
  for (const f of features?.features ?? []) {
    for (const key of Object.keys(f.properties ?? {})) if (!key.startsWith("_")) names.add(key);
    if (names.size >= 200) break;
  }
  return [...names].sort((a, b) => a.localeCompare(b));
}

/** Features a per-feature run would take: the selection, else the whole dataset (minus `skip`). */
export function batchFeatureCount(
  features: DatasetFeatureCollection | null,
  selected: readonly number[],
  skip?: (index: number) => boolean,
): number {
  const wanted = selected.length > 0 ? new Set(selected) : null;
  let n = 0;
  for (const f of features?.features ?? []) {
    const i = f.properties._index;
    if ((!wanted || wanted.has(i)) && !skip?.(i)) n += 1;
  }
  return n;
}

export interface BatchColumnContext {
  units: Units;
  /** an area in the user's land-area unit (survey) */
  area: (m2: number | null) => string;
}

export interface BatchColumn {
  id: string;
  header: string;
  numeric?: boolean;
  text: (item: BatchItem, c: BatchColumnContext) => string;
  sort: (item: BatchItem) => number | string | null;
}

function num(item: BatchItem, key: string): number | null {
  const v = item.values[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function str(item: BatchItem, key: string): string | null {
  const v = item.values[key];
  return typeof v === "string" && v ? v : null;
}

const AIRSPACE: BatchColumn[] = [
  { id: "band", header: "Band", text: (i) => str(i, "band") ?? "Outside mapped zones", sort: (i) => str(i, "band") },
  {
    id: "ceiling",
    header: "Free-flight ceiling",
    numeric: true,
    text: (i, c) => (i.values.permission_required ? "Permission" : formatHeight(num(i, "effective_ceiling_agl_m"), c.units)),
    sort: (i) => (i.values.permission_required ? -1 : num(i, "effective_ceiling_agl_m")),
  },
  {
    id: "conflicts",
    header: "Obstacle conflicts",
    numeric: true,
    text: (i) => formatNumber(num(i, "obstacle_conflicts")),
    sort: (i) => num(i, "obstacle_conflicts"),
  },
  {
    id: "aerodrome",
    header: "Nearest aerodrome",
    text: (i, c) => {
      const km = num(i, "nearest_aerodrome_km");
      const ident = str(i, "nearest_aerodrome");
      return ident ? `${ident} · ${formatLength(km === null ? null : km * 1000, c.units)}` : "—";
    },
    sort: (i) => num(i, "nearest_aerodrome_km"),
  },
];

const GROUND_RISK: BatchColumn[] = [
  { id: "igrc", header: "iGRC", numeric: true, text: (i) => (num(i, "igrc") === null ? "Beyond table" : formatNumber(num(i, "igrc"))), sort: (i) => num(i, "igrc") ?? 99 },
  { id: "band", header: "Density band", text: (i) => str(i, "band") ?? "—", sort: (i) => num(i, "max_density_per_km2") },
  {
    id: "density",
    header: "Highest density",
    numeric: true,
    text: (i) => `${formatNumber(num(i, "max_density_per_km2"), { maximumFractionDigits: 0 })} /km²`,
    sort: (i) => num(i, "max_density_per_km2"),
  },
  { id: "people", header: "People in footprint", numeric: true, text: (i) => formatNumber(num(i, "population"), { maximumFractionDigits: 0 }), sort: (i) => num(i, "population") },
  {
    id: "adjacent",
    header: "Adjacent density",
    numeric: true,
    text: (i) => (num(i, "adjacent_density_per_km2") === null ? "—" : `${formatNumber(num(i, "adjacent_density_per_km2"), { maximumFractionDigits: 0 })} /km²`),
    sort: (i) => num(i, "adjacent_density_per_km2"),
  },
];

const SURVEY: BatchColumn[] = [
  { id: "area", header: "Area", numeric: true, text: (i, c) => c.area(num(i, "area_ha") === null ? null : (num(i, "area_ha") as number) * 10_000), sort: (i) => num(i, "area_ha") },
  { id: "photos", header: "Photos", numeric: true, text: (i) => formatNumber(num(i, "photos")), sort: (i) => num(i, "photos") },
  { id: "minutes", header: "Total time", numeric: true, text: (i) => formatDuration(num(i, "minutes") === null ? null : (num(i, "minutes") as number) * 60), sort: (i) => num(i, "minutes") },
  { id: "batteries", header: "Batteries", numeric: true, text: (i) => formatNumber(num(i, "batteries")), sort: (i) => num(i, "batteries") },
  { id: "gsd", header: "GSD", numeric: true, text: (i) => (num(i, "gsd_cm") === null ? "—" : `${formatNumber(num(i, "gsd_cm"), { maximumFractionDigits: 2 })} cm/px`), sort: (i) => num(i, "gsd_cm") },
];

const ENVIRONMENT: BatchColumn[] = [
  {
    id: "emi",
    header: "Nearest power line or mast",
    text: (i, c) => {
      const kind = str(i, "emi_nearest_kind");
      if (kind === null) return num(i, "emi_count") === null ? "Not checked" : "None";
      const kv = num(i, "emi_nearest_kv");
      const label = (EMI_KIND_LABEL[kind as EmiKind] ?? kind) + (kv === null ? "" : `, ${formatNumber(kv)} kV`);
      return `${formatLength(num(i, "emi_nearest_m"), c.units)} (${label})`;
    },
    sort: (i) => num(i, "emi_nearest_m"),
  },
  { id: "count", header: "Nearby", numeric: true, text: (i) => formatNumber(num(i, "emi_count")), sort: (i) => num(i, "emi_count") },
  {
    id: "daylight",
    header: "Outside daylight",
    numeric: true,
    text: (i) => formatDuration(num(i, "daylight_minutes_outside") === null ? null : (num(i, "daylight_minutes_outside") as number) * 60),
    sort: (i) => num(i, "daylight_minutes_outside"),
  },
  { id: "declination", header: "Declination", numeric: true, text: (i) => `${formatNumber(num(i, "declination_deg"), { maximumFractionDigits: 1 })}°`, sort: (i) => num(i, "declination_deg") },
  { id: "wind", header: "Wind margin", numeric: true, text: (i, c) => formatSpeed(num(i, "wind_margin_ms"), c.units), sort: (i) => num(i, "wind_margin_ms") },
];

const LINE_OF_SIGHT: BatchColumn[] = [
  { id: "visible", header: "Visible", numeric: true, text: (i) => formatPercent(num(i, "visible_pct")), sort: (i) => num(i, "visible_pct") },
  { id: "hidden", header: "Hidden area", numeric: true, text: (i, c) => areaParts(num(i, "hidden_area_m2"), c.units).text, sort: (i) => num(i, "hidden_area_m2") },
  { id: "fresnel", header: "Worst Fresnel clearance", numeric: true, text: (i) => formatPercent(num(i, "radio_worst_clearance_pct")), sort: (i) => num(i, "radio_worst_clearance_pct") },
  { id: "beyond", header: "Beyond VLOS guidance", numeric: true, text: (i) => formatPercent(num(i, "beyond_guidance_pct")), sort: (i) => num(i, "beyond_guidance_pct") },
];

export const BATCH_COLUMNS: Readonly<Partial<Record<AnalysisKind, readonly BatchColumn[]>>> = {
  airspace: AIRSPACE,
  ground_risk: GROUND_RISK,
  survey: SURVEY,
  environment: ENVIRONMENT,
  line_of_sight: LINE_OF_SIGHT,
};
