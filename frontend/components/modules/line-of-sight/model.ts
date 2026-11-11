/**
 * Line-of-sight module: form defaults, observer detection in a dataset and small shared helpers
 * (no React). The numbers mirror `airbrief_core/models/line_of_sight.py`.
 */

import { formatNumber } from "@/lib/format";
import type { DatasetFeatureCollection, LosRadioPath, ObserverRole } from "@/lib/types";

/** Form defaults, the same as the API's (LineOfSightParams). */
export const DEFAULTS = {
  target: 120,
  grid: 30,
  frequency: 2400,
  clearance: 60,
  /** visible light: 1/(1 − 1/7), GDAL's curvature coefficient 0.85714 */
  kVisible: 7 / 6,
  /** radio, standard atmosphere */
  kRadio: 4 / 3,
  visibility: 5000,
  includeObstacles: true,
  radioCheck: true,
  aircraft: "multirotor" as "multirotor" | "fixed_wing",
} as const;

export const AIRCRAFT = ["multirotor", "fixed_wing"] as const;
export const ROLES: readonly ObserverRole[] = ["pilot", "observer", "gcs"];

/** The API accepts at most this many observers. */
export const MAX_OBSERVERS = 10;

/** Common licence-exempt C2 bands (MHz). */
export const FREQUENCY_PRESETS: readonly number[] = [433, 868, 915, 2400, 5800];

/** Heights matching common ceilings; in imperial they show as whole feet but keep these exact metres. */
export const TARGET_PRESETS: readonly number[] = [30, 60, 120];

export const ROLE_LABEL: Readonly<Record<ObserverRole, string>> = {
  pilot: "Remote pilot",
  observer: "Visual observer",
  gcs: "Ground station",
};

/** Short names used for new rows and on the map. */
export const ROLE_SHORT: Readonly<Record<ObserverRole, string>> = { pilot: "Pilot", observer: "Observer", gcs: "GCS" };

/** Eye height (pilot, observer) or antenna height (ground station) when none is given, metres. */
export const ROLE_HEIGHT_M: Readonly<Record<ObserverRole, number>> = { pilot: 1.7, observer: 1.7, gcs: 2.0 };

export const RADIO_NOTE = "Geometry only — not a link budget or coverage prediction.";
export const EVIDENCE_NOTE = "Geometric evidence from the data-pack surface model — not a VLOS approval.";

/** An observer as edited in the panel (positions may still be empty). */
export interface ObserverDraft {
  /** stable row key (not sent) */
  key: string;
  name: string;
  role: ObserverRole;
  lon: number | null;
  lat: number | null;
  /** null = the role default */
  height: number | null;
}

let rowSeq = 0;
export function newRowKey(): string {
  rowSeq += 1;
  return `obs-${rowSeq}`;
}

// Same patterns as api/services/line_of_sight.py (whole words, case-insensitive), so the panel
// lists the observers the API would read from the dataset.
const GCS_RE = /\b(gcs|ground\s*(control\s*)?station|base\s*station)\b/i;
const OBSERVER_RE = /\b(observer|spotter|vo)\b/i;
const PILOT_RE = /\b(pilot|rpic)\b/i;

/** `pilot` / `observer` / `gcs` from a feature's `role` property or its name, else null. */
export function observerRole(name: string | null | undefined, properties?: Readonly<Record<string, unknown>> | null): ObserverRole | null {
  const role = properties?.role;
  if (typeof role === "string" && (ROLES as readonly string[]).includes(role.trim().toLowerCase())) {
    return role.trim().toLowerCase() as ObserverRole;
  }
  const text = name ?? "";
  if (GCS_RE.test(text)) return "gcs";
  if (OBSERVER_RE.test(text)) return "observer";
  if (PILOT_RE.test(text)) return "pilot";
  return null;
}

/** Point features named like a pilot, observer or ground station (at most MAX_OBSERVERS). */
export function datasetObservers(features: DatasetFeatureCollection | null): ObserverDraft[] {
  const out: ObserverDraft[] = [];
  for (const f of features?.features ?? []) {
    if (out.length >= MAX_OBSERVERS) break;
    if (f.geometry?.type !== "Point") continue;
    const props = f.properties;
    const role = observerRole(props._name, props);
    if (!role) continue;
    const [lon, lat] = f.geometry.coordinates as [number, number];
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
    const h = props.height_agl_m;
    const height = typeof h === "number" && Number.isFinite(h) && h >= 0 && h <= 500 ? h : null;
    out.push({ key: newRowKey(), name: (props._name ?? ROLE_SHORT[role]).slice(0, 80), role, lon, lat, height });
  }
  return out;
}

/** Number of features the check runs on: everything except observer points. */
export function operationFeatureCount(features: DatasetFeatureCollection | null, selected: readonly number[]): number {
  if (!features) return 0;
  const wanted = selected.length > 0 ? new Set(selected) : null;
  const isObserver = (f: DatasetFeatureCollection["features"][number]) =>
    f.geometry?.type === "Point" && observerRole(f.properties._name, f.properties) !== null;
  const usable = features.features.filter((f) => f.geometry != null && !isObserver(f));
  const inSelection = wanted ? usable.filter((f) => wanted.has(f.properties._index)) : usable;
  // An observer-only selection falls back to the rest of the dataset (as the API does).
  return inSelection.length > 0 ? inSelection.length : usable.length;
}

/** "2.4 GHz" / "868 MHz". */
export function formatFrequency(mhz: number | null | undefined): string {
  if (mhz === null || mhz === undefined || !Number.isFinite(mhz)) return "—";
  return mhz >= 1000 ? `${formatNumber(mhz / 1000, { maximumFractionDigits: 3 })} GHz` : `${formatNumber(mhz, { maximumFractionDigits: 1 })} MHz`;
}

export type RadioBand = "clear" | "below" | "blocked" | "unknown";

export function radioBand(path: Pick<LosRadioPath, "worst_clearance_pct" | "los_blocked" | "below_threshold">): RadioBand {
  if (path.worst_clearance_pct === null) return "unknown";
  if (path.los_blocked) return "blocked";
  return path.below_threshold ? "below" : "clear";
}

export const RADIO_BAND_TONE: Readonly<Record<RadioBand, "ok" | "warn" | "danger" | "neutral">> = {
  clear: "ok",
  below: "warn",
  blocked: "danger",
  unknown: "neutral",
};

export function radioBandLabel(band: RadioBand, thresholdPct: number): string {
  switch (band) {
    case "clear":
      return `Clearance ≥ ${formatNumber(thresholdPct)}%`;
    case "below":
      return `Below ${formatNumber(thresholdPct)}%`;
    case "blocked":
      return "Line of sight cut";
    default:
      return "No terrain data";
  }
}

/** Highlight key of a radio path, scoped to its analysis so a selection never carries over. */
export function pathKey(analysisId: string, index: number): string {
  return `los-path-${analysisId}-${index}`;
}
