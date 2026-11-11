"use client";

/**
 * Generic, print-friendly summary of any analysis: verdict, key numbers per kind (KPI tiles),
 * the main finding tables, notes and the disclaimer. Used by `/w/report`.
 *
 * Names, KPI labels and wording follow the workspace results drawer for the same analysis
 * (components/modules/<module>/index.tsx), so a printed report reads like the screen it came
 * from. The analysis name comes from `analysisKindLabel`, the one used by toasts and History.
 */

import { AlertTriangle, Info, OctagonAlert } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { KpiGrid, KpiTile, type KpiTone } from "@/components/ui/kpi-tile";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { analysisKindLabel } from "@/components/workspace/analysis-runner";
import { auditLimits, describeLimit, describeWithinLimit } from "@/components/modules/flight-audit/limits";
import { TRACK_FORMAT_LABEL } from "@/components/modules/flight-audit/tracks";
import { VERDICT_LABEL, ZONE_LABEL, toneForSeverity } from "@/lib/colors";
import { GRB_RULE_LABEL } from "@/lib/labels";
import { DAYLIGHT_PROFILE_LABEL, DAYLIGHT_SEGMENT_LABEL, DAYLIGHT_STATUS_LABEL, EMI_KIND_LABEL } from "@/lib/labels";
import { localWithDay } from "@/components/modules/environment/time";
import {
  EMPTY,
  areaParts,
  formatArea,
  formatBearing,
  formatClock,
  formatCoord,
  formatDate,
  formatDuration,
  formatHeight,
  formatLength,
  formatNumber,
  formatSpeed,
  heightParts,
  lengthParts,
  localizeUnits,
  speedParts,
  type FormattedValue,
} from "@/lib/format";
import type { Units } from "@/lib/store";
import { BATCH_COLUMNS, isBatchResult } from "@/lib/batch";
import type { BatchItem, BatchResult } from "@/lib/types";
import {
  isAnalysisOfKind,
  type AuditSegment,
  type DemInfo,
  type EmiItem,
  type HeightReference,
  type HeightSegment,
  type Issue,
  type ObstacleHit,
  type ShowResult,
  type SunEvent,
  type TakeoffPoint,
  type Verdict,
  type ZoneHit,
  type AnalysisInfo,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export interface ReportKpi {
  label: string;
  value: string | FormattedValue;
  explanation?: string;
  tone?: KpiTone;
  toneLabel?: string;
}

export interface ReportTable {
  caption: string;
  columns: { header: string; numeric?: boolean }[];
  rows: string[][];
  /** total rows before truncation */
  total: number;
}

export interface AnalysisSummaryData {
  title: string;
  verdict: Verdict | null;
  summary: string | null;
  /** prominent caveats ("Airspace not assessed: …", "Heights not assessed: …"), shown above the numbers */
  banners?: string[];
  kpis: ReportKpi[];
  tables: ReportTable[];
  issues: Issue[];
  disclaimer: string | null;
  /** a line under the key numbers (height reference, origin, method) */
  note?: string | null;
}

const MAX_ROWS = 25;

/** Same note as the survey results in the workspace. */
const SURVEY_ESTIMATE_NOTE = "Estimate only — no flight plan or waypoints are generated.";

function table(caption: string, columns: ReportTable["columns"], rows: string[][]): ReportTable {
  return { caption, columns, rows: rows.slice(0, MAX_ROWS), total: rows.length };
}

function plain(value: string, unit = ""): FormattedValue {
  return { value, unit, text: unit ? `${value} ${unit}` : value };
}

function num(value: number | null | undefined, unit = "", decimals?: number): FormattedValue {
  return plain(formatNumber(value, decimals === undefined ? {} : { decimals }), value == null ? "" : unit);
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${formatNumber(count)} ${count === 1 ? one : many}`;
}

function distanceKm(km: number | null | undefined, units: Units): string {
  return formatLength(km === null || km === undefined || !Number.isFinite(km) ? null : km * 1000, units);
}

/** Survey height, flagged when above the limit the run checked (120 m when the result predates it). */
function surveyHeightKpi(altitudeM: number, limit: unknown, units: Units): ReportKpi {
  const limitM = typeof limit === "number" && Number.isFinite(limit) ? limit : 120;
  const above = altitudeM > limitM + 1e-6;
  return {
    label: "Height",
    value: heightParts(altitudeM, units),
    explanation: "Above ground",
    tone: above ? "warn" : "neutral",
    toneLabel: above ? `Above the ${formatHeight(limitM, units)} limit` : undefined,
  };
}

function zoneRows(zones: readonly ZoneHit[], units: Units): string[][] {
  return zones.map((z) => [
    z.name + (z.ident ? ` (${z.ident})` : ""),
    ZONE_LABEL[z.zone] + (z.ring ? ` · ${z.ring}` : ""),
    z.ceiling_agl_m === null ? EMPTY : formatHeight(z.ceiling_agl_m, units),
    z.permission_required ? "Required" : "No",
    z.distance_km === null ? EMPTY : formatLength(z.distance_km * 1000, units),
  ]);
}

const ZONE_COLUMNS: ReportTable["columns"] = [
  { header: "Name" },
  { header: "Zone" },
  { header: "Ceiling AGL", numeric: true },
  { header: "Permission" },
  { header: "Distance", numeric: true },
];

function issueMessage(issues: readonly Issue[], code: string): string | null {
  return issues.find((i) => i.code === code)?.message ?? null;
}

/** An obstacle's name, marked when its height was assumed (no mapped height). */
function obstacleName(o: ObstacleHit): string {
  return `${o.name ?? o.id}${o.height_assumed ? " (assumed height)" : ""}`;
}

const OBSTACLE_COLUMNS: ReportTable["columns"] = [
  { header: "Obstacle" },
  { header: "Kind" },
  { header: "Height", numeric: true },
  { header: "Clearance", numeric: true },
  { header: "Distance", numeric: true },
  { header: "Check" },
];

/** Every obstacle, conflicts first then nearest first, as in the workspace tables. */
function obstacleRows(obstacles: readonly ObstacleHit[], units: Units): string[][] {
  return [...obstacles]
    .sort((a, b) => Number(b.conflict) - Number(a.conflict) || a.distance_m - b.distance_m)
    .map((o) => [
      obstacleName(o),
      // "communication_tower" → "Communication tower" (never the raw enum on paper)
      o.kind ? o.kind.charAt(0).toUpperCase() + o.kind.slice(1).replace(/_/g, " ") : EMPTY,
      formatHeight(o.height_m, units),
      o.clearance_m === null ? EMPTY : formatHeight(o.clearance_m, units),
      formatLength(o.distance_m, units),
      o.conflict ? "Conflict" : "Clear",
    ]);
}

/** How a planned height is read (airbrief_core/analyses/heights.py). */
const HEIGHT_REFERENCE_TEXT: Readonly<Record<HeightReference, string>> = {
  agl: "Above ground (AGL)",
  takeoff: "Above take-off",
  amsl: "Above mean sea level (AMSL)",
};

/** How a flown track's heights were read (same text as the flight-audit results). */
const ALTITUDE_REFERENCE_TEXT: Readonly<Record<string, string>> = {
  terrain_calibrated: "Terrain-corrected and calibrated at take-off (the first fix is assumed on the ground).",
  terrain_raw: "Terrain-corrected without calibration (GPS altitude taken as AMSL).",
  takeoff_relative: "Height above take-off (no terrain data for this area).",
  recorded_agl: "Heights as recorded above ground (the track's altitude mode).",
};

const TAKEOFF_SOURCE_TEXT: Readonly<Record<string, string>> = {
  given: "given",
  first_vertex: "the first vertex",
  centroid: "the area centroid",
  point: "the point",
  origin: "the origin",
};

/** Terrain-true height fields shared by airspace, survey and the eVTOL corridor (all optional). */
interface HeightFields {
  takeoff?: TakeoffPoint | null;
  flight_level_amsl_m?: number | null;
  agl_min_m?: number | null;
  agl_max_m?: number | null;
  min_agl_m?: number | null;
  ceiling_agl_m?: number | null;
  below_min_agl?: HeightSegment[];
  above_ceiling?: HeightSegment[];
  heights_assessed?: boolean;
  dem?: DemInfo | null;
}

const SEGMENT_COLUMNS: ReportTable["columns"] = [
  { header: "From", numeric: true },
  { header: "To", numeric: true },
  { header: "Length", numeric: true },
  { header: "Height above ground", numeric: true },
];

/**
 * Key numbers and tables of the height reference: the reference itself, the take-off ground,
 * the flight level, the height above ground along the route and the stretches below the minimum
 * or above the ceiling (with a "Heights not assessed" banner when no DEM covered a take-off or
 * AMSL plan). Results stored before heights were reported give nothing.
 */
function heightDetails(
  r: HeightFields,
  reference: HeightReference,
  issues: readonly Issue[],
  units: Units,
): { kpis: ReportKpi[]; tables: ReportTable[]; banners: string[] } {
  const kpis: ReportKpi[] = [];
  const tables: ReportTable[] = [];
  const banners: string[] = [];
  if (r.heights_assessed === false) {
    banners.push(
      `Heights not assessed: ${issueMessage(issues, "heights_not_assessed") ?? "no terrain model (DEM) covers this area, so heights above ground were not checked."}`,
    );
  }
  if (reference !== "agl") kpis.push({ label: "Height reference", value: plain(HEIGHT_REFERENCE_TEXT[reference]) });
  if (r.takeoff && r.takeoff.elevation_m !== null) {
    kpis.push({
      label: "Take-off ground",
      value: heightParts(r.takeoff.elevation_m, units),
      explanation: `AMSL at ${formatCoord(r.takeoff.lon, r.takeoff.lat)} (${TAKEOFF_SOURCE_TEXT[r.takeoff.source] ?? r.takeoff.source})`,
    });
  }
  if (typeof r.flight_level_amsl_m === "number") {
    kpis.push({ label: "Flight level (AMSL)", value: heightParts(r.flight_level_amsl_m, units) });
  }
  const aglMin = r.agl_min_m;
  const aglMax = r.agl_max_m;
  if (typeof aglMin === "number" && typeof aglMax === "number" && (reference !== "agl" || Math.abs(aglMax - aglMin) > 0.5)) {
    kpis.push({
      label: "Height above ground",
      value: plain(`${formatNumber(aglMin, { maximumFractionDigits: 0 })}–${formatHeight(aglMax, units)}`),
      explanation: "Lowest to highest along the flight",
    });
  }
  const runs: [keyof HeightFields & ("below_min_agl" | "above_ceiling"), string, string, number | null | undefined][] = [
    ["below_min_agl", "Below the minimum height", "Below the minimum height above ground", r.min_agl_m],
    ["above_ceiling", "Above the ceiling", "Above the ceiling", r.ceiling_agl_m],
  ];
  for (const [key, label, caption, fallbackLimit] of runs) {
    const segments = r[key];
    if (!segments || (reference === "agl" && segments.length === 0)) continue;
    const total = segments.reduce((sum, s) => sum + s.length_m, 0);
    const limit = segments[0]?.limit_m ?? fallbackLimit ?? null;
    kpis.push({
      label,
      value: plain(plural(segments.length, "stretch", "stretches")),
      tone: segments.length > 0 ? "warn" : "ok",
      toneLabel: segments.length > 0 ? "Found" : "None",
      explanation:
        segments.length > 0
          ? `${formatLength(total, units)} in total${limit !== null ? `; limit ${formatHeight(limit, units)}` : ""}`
          : limit !== null
            ? `Limit ${formatHeight(limit, units)}`
            : undefined,
    });
    if (segments.length > 0) {
      tables.push(
        table(
          caption,
          SEGMENT_COLUMNS,
          segments.map((s) => [
            formatLength(s.start_m, units, { maximumFractionDigits: 1 }),
            formatLength(s.end_m, units, { maximumFractionDigits: 1 }),
            formatLength(s.length_m, units),
            formatHeight(s.peak_agl_m, units),
          ]),
        ),
      );
    }
  }
  if (r.dem) kpis.push({ label: "Terrain model", value: plain(r.dem.title), explanation: r.dem.caveat });
  return { kpis, tables, banners };
}

/** Training-area exits or runs beyond the distance from take-off (flight audit). */
function auditSegmentRows(segments: readonly AuditSegment[], units: Units): string[][] {
  return segments.map((s) => [
    formatLength(s.start_distance_m, units, { maximumFractionDigits: 1 }),
    formatLength(s.end_distance_m, units, { maximumFractionDigits: 1 }),
    formatLength(s.length_m, units),
    s.duration_s === null ? EMPTY : formatDuration(s.duration_s),
    formatLength(s.peak_m, units),
  ]);
}

function auditSegmentColumns(peak: string): ReportTable["columns"] {
  return [
    { header: "From", numeric: true },
    { header: "To", numeric: true },
    { header: "Length flown", numeric: true },
    { header: "Duration", numeric: true },
    { header: peak, numeric: true },
  ];
}

/** Show checks (same names as the drone-show results). */
const SHOW_VIOLATION_LABEL: Readonly<Record<string, string>> = {
  speed_h: "Horizontal speed",
  climb: "Climb rate",
  descent: "Descent rate",
  acceleration: "Acceleration",
  geofence: "Outside show box",
  audience: "Inside audience buffer",
  ceiling: "Above ceiling",
  endurance: "Airborne longer than endurance",
  low_altitude_motion: "Moving below navigation height",
};

const AUDIENCE_RULE_LABEL: Readonly<Record<string, string>> = { fixed: "Fixed", one_to_one: "1:1 height", ballistic: "Ballistic" };

/** The origin and height reference a show check used (as under the show results). */
function describeShowOrigin(r: ShowResult, units: Units): string {
  const [lon, lat, ground] = r.frames.origin;
  const source = r.origin_source === "inferred" ? " (the mean first position)" : r.origin_source === "given" ? " (given)" : "";
  const heights =
    r.altitude_reference === "absolute"
      ? `heights above sea level, measured from ${formatHeight(ground ?? 0, units)} at the origin`
      : "heights relative to the origin";
  return `Origin ${formatCoord(lon, lat)}${source} · ${heights}.`;
}

/** Site checks of a show: the box (given or derived), the audience rule, pads and endurance. */
function showSiteKpis(r: ShowResult, endurance: unknown, units: Units): ReportKpi[] {
  const kpis: ReportKpi[] = [];
  if (typeof r.max_altitude_m === "number") {
    kpis.push({ label: "Max height", value: heightParts(r.max_altitude_m, units, { maximumFractionDigits: 1 }), explanation: "Above the show origin" });
  }
  const site = r.site;
  kpis.push({
    label: "Show box",
    value: site.show_box_area_m2 === null || site.show_box_area_m2 === undefined ? plain("Not defined") : areaParts(site.show_box_area_m2, units),
    explanation:
      site.show_box_source === "derived"
        ? `Derived from the tracks (+${formatLength(site.show_box_margin_m ?? 0, units)} margin)`
        : site.show_box_source === "given"
          ? "“Show box” polygon"
          : undefined,
  });
  if (site.audience_buffer) {
    const b = site.audience_buffer;
    kpis.push({
      label: "Audience distance",
      value: lengthParts(b.distance_m, units, { maximumFractionDigits: 1 }),
      explanation: `${AUDIENCE_RULE_LABEL[b.rule] ?? b.rule}: ${localizeUnits(b.formula, units)}`,
    });
  }
  if (site.audience_area_m2 !== null && site.audience_area_m2 !== undefined) {
    kpis.push({
      label: "Box to audience",
      value: lengthParts(site.box_audience_distance_m ?? null, units),
      tone: site.box_overlaps_audience_buffer ? "danger" : "ok",
      toneLabel: site.box_overlaps_audience_buffer ? "Overlaps the box" : "Clear of the box",
    });
  }
  if (r.pads) {
    const p = r.pads;
    const spacings = [p.takeoff_min_spacing_m, p.landing_min_spacing_m].filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    kpis.push({
      label: "Pad spacing",
      value: lengthParts(spacings.length > 0 ? Math.min(...spacings) : null, units, { maximumFractionDigits: 2 }),
      tone: p.conflicts.length > 0 ? "danger" : "ok",
      toneLabel: p.conflicts.length > 0 ? `Below ${formatLength(p.limit_m, units)}` : "Clear",
      explanation: `${formatNumber(p.takeoff_pads)} take-off, ${formatNumber(p.landing_pads)} landing pads`,
    });
  }
  if (typeof endurance === "number" && Number.isFinite(endurance)) {
    const longest = r.drones.reduce((m, d) => Math.max(m, d.airborne_s), 0);
    const over = r.violations.filter((v) => v.kind === "endurance").length;
    kpis.push({
      label: "Endurance",
      value: plain(formatDuration(longest)),
      tone: over > 0 ? "danger" : "ok",
      toneLabel: over > 0 ? `${plural(over, "drone")} over` : "Within",
      explanation: `Longest airborne time against the ${formatDuration(endurance)} endurance`,
    });
  }
  return kpis;
}

function batchSummary(info: AnalysisInfo, r: BatchResult, units: Units): AnalysisSummaryData {
  const columns = BATCH_COLUMNS[info.kind] ?? [];
  const context = { units, area: (m2: number | null) => areaParts(m2, units).text };
  const labels = new Map(r.counts.map((c) => [c.verdict, c.label]));
  const rank = (i: BatchItem) => (i.status === "ok" ? (r.counts.findIndex((c) => c.verdict === i.verdict) + 1 || 50) : 100);
  const rows = [...r.items]
    .sort((a, b) => rank(a) - rank(b))
    .map((i) => [
      i.name && i.name !== i.key ? `${i.key} (${i.name})` : i.key,
      i.status === "ok" ? (labels.get(i.verdict ?? "") ?? EMPTY) : `Not assessed: ${i.error ?? "no reason given"}`,
      ...columns.map((col) => (i.status === "ok" ? col.text(i, context) : EMPTY)),
    ]);
  const missing = r.errors + r.skipped;
  return {
    title: `${analysisKindLabel(info.kind)}: one result per feature`,
    verdict: null,
    summary: r.summary,
    banners: missing > 0 ? [`Not assessed: ${plural(missing, "feature")} of ${formatNumber(r.feature_count)} have no result; their rows say why.`] : [],
    kpis: [
      { label: "Features", value: plain(formatNumber(r.feature_count)), explanation: "One result each, with the same parameters" },
      ...r.counts.map<ReportKpi>((c) => ({ label: c.label, value: plain(formatNumber(c.count)), explanation: "Features with this result" })),
      ...(missing > 0 ? [{ label: "Not assessed", value: plain(formatNumber(missing)), tone: "warn" as const, toneLabel: "See the rows" }] : []),
    ],
    tables: [
      table(
        "One result per feature (most severe first)",
        [{ header: "Feature" }, { header: "Result" }, ...columns.map((c) => ({ header: c.header, numeric: c.numeric }))],
        rows,
      ),
    ],
    issues: r.issues,
    disclaimer: r.disclaimer,
    note: r.method,
  };
}

/** Key numbers and tables for an analysis; null when it has no result yet. */
export function summarizeAnalysis(info: AnalysisInfo, units: Units): AnalysisSummaryData | null {
  const base = { title: analysisKindLabel(info.kind), summary: null as string | null };
  if (info.status === "COMPLETED" && isBatchResult(info.result)) return batchSummary(info, info.result, units);

  if (isAnalysisOfKind(info, "survey") && info.result) {
    const r = info.result;
    const spraying = r.mode === "spraying";
    const reference = r.height_reference ?? "agl";
    // A range only when the terrain makes one (as the survey panel), never "2.74–2.74".
    const hasRange =
      typeof r.gsd_min_cm === "number" && typeof r.gsd_max_cm === "number" && Math.abs(r.gsd_max_cm - r.gsd_min_cm) >= 0.005;
    const gsd = hasRange
      ? plain(`${formatNumber(r.gsd_min_cm, { maximumFractionDigits: 2 })}–${formatNumber(r.gsd_max_cm, { maximumFractionDigits: 2 })}`, "cm/px")
      : num(r.gsd_cm, "cm/px", 2);
    const heights = heightDetails(r, reference, r.issues, units);
    const kpis: ReportKpi[] = [];
    if (!spraying) {
      kpis.push({
        label: "Ground sample distance",
        value: gsd,
        explanation: hasRange
          ? r.heights_note ?? "Over the terrain relief"
          : r.gsd_along_cm !== r.gsd_cm
            ? `Along track ${formatNumber(r.gsd_along_cm, { maximumFractionDigits: 2 })} cm/px`
            : "Square pixels",
      });
    }
    const heightKpi = surveyHeightKpi(r.altitude_agl_m, r.max_altitude_agl_m ?? info.params.max_altitude_agl_m, units);
    kpis.push({ ...heightKpi, explanation: HEIGHT_REFERENCE_TEXT[reference] });
    if (r.coverage === "corridor") {
      kpis.push({
        label: "Corridor",
        value: lengthParts(r.corridor_width_m ?? null, units),
        explanation: `Wide, along a ${formatLength((r.centreline_km ?? 0) * 1000, units)} centreline`,
      });
    }
    if (!spraying) {
      kpis.push(
        {
          label: "Image footprint",
          value: plain(`${formatLength(r.footprint_across_m, units)} × ${formatLength(r.footprint_along_m, units)}`),
          explanation: "Across × along track",
        },
        { label: "Trigger distance", value: lengthParts(r.trigger_distance_m, units), explanation: `Every ${formatNumber(r.trigger_interval_s, { maximumFractionDigits: 1 })} s` },
        { label: "Photos", value: num(r.photos) },
      );
    }
    kpis.push(
      {
        label: "Line spacing",
        value: lengthParts(r.line_spacing_m, units),
        explanation: `${plural(r.line_count, "line")} at ${formatBearing(r.direction_deg)} (a visual aid, not a route)`,
      },
      { label: "Line length", value: lengthParts(r.line_km * 1000, units), explanation: plural(r.segment_count, "segment") },
      {
        label: "Flight time",
        value: plain(formatDuration(r.flight_minutes * 60)),
        explanation: `${formatDuration(r.total_minutes * 60)} including battery swaps${spraying ? " and refills" : ""}`,
      },
      {
        label: "Batteries",
        value: num(r.batteries),
        explanation: typeof r.battery_reserve_pct === "number" && r.battery_reserve_pct > 0 ? `${formatNumber(r.battery_reserve_pct)}% reserve kept` : undefined,
      },
      { label: "Area", value: areaParts(r.area_ha * 10_000, units), explanation: `${formatArea(r.ha_per_hour * 10_000, units)} per hour` },
    );
    if (typeof r.transit_km === "number" && r.transit_km > 0) {
      kpis.push({
        label: "Transit",
        value: lengthParts(r.transit_km * 1000, units),
        explanation: `${formatDuration((r.transit_minutes ?? 0) * 60)} to and from the take-off point`,
      });
    }
    if (typeof r.worst_side_overlap_pct === "number") {
      kpis.push({ label: "Worst side overlap", value: num(r.worst_side_overlap_pct, "%", 0), explanation: "At the highest ground" });
    }
    if (typeof r.worst_front_overlap_pct === "number") {
      kpis.push({ label: "Worst front overlap", value: num(r.worst_front_overlap_pct, "%", 0), explanation: "At the highest ground" });
    }
    if (r.gcp) kpis.push({ label: "Ground control points", value: num(r.gcp.count), explanation: r.gcp.rule });
    if (r.storage) {
      kpis.push({
        label: "Storage",
        value: plain(formatNumber(r.storage.total_gb, { maximumFractionDigits: 1 }), "GB"),
        explanation: `${formatNumber(r.storage.images)} images × ${formatNumber(r.storage.mb_per_image, { maximumFractionDigits: 1 })} MB`,
      });
    }
    if (r.spray) {
      const s = r.spray;
      kpis.push(
        {
          label: "Spray mix",
          value: plain(formatNumber(s.litres, { maximumFractionDigits: 1 }), "L"),
          explanation: `${formatNumber(s.rate_l_per_ha)} L/ha over a ${formatLength(s.swath_m, units)} swath`,
        },
        {
          label: "Tank loads",
          value: num(s.tank_loads),
          explanation: `${formatNumber(s.tank_l)} L tank; ${plural(s.refills, "refill")}, ${plural(s.sorties, "sortie")}`,
        },
        {
          label: "Spraying rate",
          value: plain(formatNumber(s.effective_ha_per_hour, { maximumFractionDigits: 2 }), "ha/h"),
          explanation: `${formatNumber(s.field_efficiency_pct, { maximumFractionDigits: 0 })}% field efficiency at ${formatSpeed(s.speed_ms, units)}${s.flow_limited ? " (pump-limited)" : ""}`,
        },
      );
    }
    if (typeof r.terrain_min_m === "number" && typeof r.terrain_max_m === "number") {
      kpis.push({
        label: "Terrain under the survey",
        value: plain(`${formatNumber(r.terrain_min_m, { maximumFractionDigits: 0 })}–${formatHeight(r.terrain_max_m, units)}`),
        explanation: `Above mean sea level${typeof r.relief_m === "number" ? `; relief ${formatHeight(r.relief_m, units)}` : ""}`,
      });
    }
    kpis.push(...heights.kpis);
    const withGsd =
      !spraying &&
      r.blocks.some((b) => typeof b.gsd_min_cm === "number" && typeof b.gsd_max_cm === "number" && Math.abs(b.gsd_max_cm - b.gsd_min_cm) >= 0.005);
    const blockColumns: ReportTable["columns"] = [
      { header: "Battery" },
      { header: "Area", numeric: true },
      { header: "Line length", numeric: true },
      { header: "Flight time", numeric: true },
      { header: spraying ? "Spray mix" : "Photos", numeric: true },
      ...(withGsd ? [{ header: "GSD", numeric: true }] : []),
    ];
    return {
      ...base,
      title: spraying ? "Spraying effort estimator" : base.title,
      verdict: null,
      summary: spraying
        ? "Spraying effort estimate only — no route or waypoints are generated."
        : `${r.camera?.name ?? "Camera"}. ${SURVEY_ESTIMATE_NOTE}`,
      banners: heights.banners,
      kpis,
      tables: [
        ...(r.blocks.length > 1
          ? [
              table(
                "Battery blocks",
                blockColumns,
                r.blocks.map((b) => [
                  `Battery ${b.index}`,
                  formatArea(b.area_ha * 10_000, units),
                  formatLength(b.line_km * 1000, units),
                  formatDuration(b.minutes * 60),
                  spraying ? `${formatNumber(b.litres ?? null, { maximumFractionDigits: 1 })} L` : formatNumber(b.photos),
                  ...(withGsd ? [`${formatNumber(b.gsd_min_cm ?? null, { maximumFractionDigits: 2 })}–${formatNumber(b.gsd_max_cm ?? null, { maximumFractionDigits: 2 })} cm/px`] : []),
                ]),
              ),
            ]
          : []),
        ...heights.tables,
      ],
      issues: r.issues,
      disclaimer: null,
    };
  }

  if (isAnalysisOfKind(info, "airspace") && info.result) {
    const r = info.result;
    const nearest = r.nearest_aerodromes[0] ?? null;
    const conflicts = r.obstacles.filter((o) => o.conflict);
    const reference = r.height_reference ?? "agl";
    const heights = heightDetails(r, reference, r.issues, units);
    const banners =
      r.assessment_complete === false
        ? [
            `Airspace not assessed: ${issueMessage(r.issues, "not_assessed") ?? "no data pack covers this area, so aerodrome zones were not checked."}`,
            ...heights.banners,
          ]
        : heights.banners;
    const gridCounts = r.grid.reduce<Record<string, number>>((acc, c) => ({ ...acc, [c.status]: (acc[c.status] ?? 0) + 1 }), {});
    return {
      ...base,
      summary: r.summary,
      verdict: r.verdict,
      banners,
      note: r.terrain ? `Terrain from ${formatNumber(r.terrain.samples)} DEM samples; relief ${formatHeight(r.terrain.relief_m, units)}.` : null,
      kpis: [
        {
          label: "Planned height",
          value: heightParts(r.altitude_agl_m, units),
          explanation: HEIGHT_REFERENCE_TEXT[reference],
        },
        {
          label: "Free-flight ceiling",
          value: r.permission_required
            ? plain("Permission required")
            : r.effective_ceiling_agl_m === null
              ? plain("Restricted")
              : heightParts(r.effective_ceiling_agl_m, units),
          tone: r.permission_required ? "warn" : "neutral",
          toneLabel: r.permission_required ? "Needs permission" : undefined,
          explanation: `Planned ${formatHeight(r.altitude_agl_m, units)} above ground`,
        },
        { label: "Highest terrain", value: heightParts(r.max_terrain_m, units), explanation: "Above mean sea level" },
        { label: "Planned top (AMSL)", value: heightParts(r.max_altitude_amsl_m, units), explanation: "Highest terrain + planned height" },
        {
          label: "Nearest aerodrome",
          value: plain(nearest ? distanceKm(nearest.distance_km, units) : "None in data"),
          explanation: nearest
            ? `${nearest.name} (${nearest.ident}) at ${formatBearing(nearest.bearing_deg)}`
            : "No aerodromes in the data pack",
        },
        { label: "Zones touched", value: num(r.zones.length) },
        {
          label: "Obstacle conflicts",
          value: num(conflicts.length),
          tone: conflicts.length > 0 ? "danger" : "ok",
          toneLabel: conflicts.length > 0 ? "Conflicts" : "None",
          explanation: `${plural(r.obstacles.length, "obstacle")} checked`,
        },
        ...(r.grid.length > 0
          ? [
              {
                label: "Flyability grid",
                value: num(r.grid.length, "cells"),
                explanation: `${formatNumber(gridCounts.free ?? 0)} free, ${formatNumber(gridCounts.limited ?? 0)} limited, ${formatNumber(gridCounts.permission ?? 0)} permission, ${formatNumber(gridCounts.restricted ?? 0)} restricted${r.grid_truncated ? " (cut at the cell limit)" : ""}`,
              },
            ]
          : []),
        ...heights.kpis,
      ],
      tables: [
        table("Zones touched by the planned flight", ZONE_COLUMNS, zoneRows(r.zones, units)),
        table("Obstacles (conflicts first)", OBSTACLE_COLUMNS, obstacleRows(r.obstacles, units)),
        table(
          "Nearest aerodromes",
          [{ header: "Aerodrome" }, { header: "Type" }, { header: "Distance", numeric: true }, { header: "Bearing", numeric: true }],
          r.nearest_aerodromes.map((a) => [`${a.name} (${a.ident})`, a.type.replace(/_/g, " "), distanceKm(a.distance_km, units), formatBearing(a.bearing_deg)]),
        ),
        ...heights.tables,
      ].filter((t) => t.total > 0),
      issues: r.issues,
      disclaimer: r.disclaimer,
    };
  }

  if (isAnalysisOfKind(info, "ground_risk") && info.result) {
    const r = info.result;
    const p95 = r.density_statistic === "p95";
    return {
      ...base,
      verdict: null,
      // The persistent scope label of operational-volume results (never a GRC, ARC or SAIL).
      summary: r.volume ? `${r.evidence_label ?? "SORA Step 2 / Step 8 evidence — not a GRC, ARC or SAIL"}.` : base.summary,
      kpis: [
        {
          label: "Intrinsic ground risk class",
          value: plain(r.igrc === null ? "Out of scope" : String(r.igrc)),
          tone: r.igrc === null ? "danger" : r.igrc >= 7 ? "danger" : r.igrc >= 5 ? "warn" : "ok",
          toneLabel: r.igrc === null ? "Beyond the table" : r.small_uas_rule ? "Small-UAS rule" : `Column ${r.column}`,
          explanation: r.in_scope ? `Band: ${r.density_band}` : "Outside the scope of the lookup table",
        },
        {
          label: `Density (${p95 ? "95th pct." : "max"})`,
          value: num(p95 ? r.p95_density_per_km2 : r.max_density_per_km2, "people/km²", 0),
          explanation: `Mean ${formatNumber(r.mean_density_per_km2, { decimals: 0 })} /km²`,
        },
        { label: "People in footprint", value: num(r.population_in_footprint, "", 0), explanation: "Modelled population" },
        {
          label: "Footprint",
          value: areaParts(r.footprint_area_km2 * 1_000_000, units),
          explanation: r.volume
            ? `FG + CV + GRB: ${formatLength(r.volume.s_cv_m + r.volume.grb_m, units)} around the flight geography`
            : `Buffer ${formatLength(r.buffer_m, units)}`,
        },
        { label: p95 ? "Max density" : "95th percentile density", value: num(p95 ? r.max_density_per_km2 : r.p95_density_per_km2, "people/km²", 0) },
        // SORA 2.5 operational volume (Step 2 / Step 8 evidence), when the analysis built one.
        ...(r.volume
          ? [
              { label: "Contingency volume", value: lengthParts(r.volume.s_cv_m, units), explanation: `Ceiling ${formatHeight(r.volume.h_cv_m, units)} AGL` },
              { label: "Ground risk buffer", value: lengthParts(r.volume.grb_m, units), explanation: GRB_RULE_LABEL[r.volume.grb_rule] },
              {
                label: "Adjacent area density",
                value: r.adjacent_mean_density_per_km2 == null ? plain("Not computed") : num(r.adjacent_mean_density_per_km2, "people/km²", 0),
                explanation: `Average out to ${formatLength(r.volume.adjacent_outer_km * 1000, units)}`,
              },
              { label: "Assemblies within 1 km", value: num((r.assemblies ?? []).length) },
            ]
          : []),
      ],
      tables: [
        ...(r.volume
          ? [
              table(
                "Operational volume terms (SORA 2.5)",
                [{ header: "Term" }, { header: "Value", numeric: true }, { header: "Formula (SI)" }],
                r.volume.terms.map((term) => [`${term.name} · ${term.label}`, formatLength(term.value_m, units), term.formula]),
              ),
              table(
                "Outdoor assemblies within 1 km",
                [{ header: "Assembly" }, { header: "Size" }, { header: "Distance", numeric: true }],
                (r.assemblies ?? []).map((a) => [a.name, a.band_label, a.distance_m <= 0 ? "Inside" : formatLength(a.distance_m, units)]),
              ),
            ]
          : []),
        table(
          "Hotspots",
          [{ header: "Cell" }, { header: "Location" }, { header: "Density (people/km²)", numeric: true }, { header: "Band" }],
          r.density_grid
            ? r.density_grid.hotspots.map((h) => [h.id, formatCoord(h.lon, h.lat), formatNumber(h.density_per_km2, { decimals: 0 }), h.band])
            : r.hotspots.map((h) => [h.h3, formatCoord(h.lon, h.lat), formatNumber(h.density_per_km2, { decimals: 0 }), h.band]),
        ),
      ].filter((t) => t.total > 0),
      issues: r.issues,
      disclaimer: r.disclaimer,
    };
  }

  if (isAnalysisOfKind(info, "evtol_siting") && info.result) {
    const r = info.result;
    const top = r.candidates[0];
    return {
      ...base,
      verdict: null,
      kpis: [
        { label: "Candidate sites", value: num(r.candidates.length) },
        { label: "Best score", value: top ? plain(formatNumber(top.score, { maximumFractionDigits: 1 }), "/ 100") : plain(EMPTY) },
        { label: "Cells evaluated", value: num(r.cells_evaluated), explanation: `${formatNumber(r.cells_excluded)} excluded (water, red zones, steep slopes)` },
      ],
      tables: [
        table(
          "Ranked vertiport candidates",
          [{ header: "Rank", numeric: true }, { header: "Score", numeric: true }, { header: "Location" }, { header: "Zone" }, { header: "Slope", numeric: true }, { header: "Nearest POI" }],
          r.candidates.map((c) => [
            String(c.rank),
            formatNumber(c.score, { maximumFractionDigits: 1 }),
            formatCoord(c.lon, c.lat),
            ZONE_LABEL[c.zone],
            c.slope_deg === null ? EMPTY : `${formatNumber(c.slope_deg, { maximumFractionDigits: 1 })}°`,
            c.nearest_poi ? `${c.nearest_poi}${c.nearest_poi_km !== null ? ` (${formatLength(c.nearest_poi_km * 1000, units)})` : ""}` : EMPTY,
          ]),
        ),
      ].filter((t) => t.total > 0),
      issues: r.issues,
      disclaimer: r.disclaimer,
    };
  }

  if (isAnalysisOfKind(info, "evtol_corridor") && info.result) {
    const r = info.result;
    const amsl = r.cruise_reference === "amsl";
    // The corridor's cruise is above the highest ground (agl) or a fixed level (amsl): its height
    // fields are read like an airspace "above ground" plan, never like an above-take-off one.
    const heights = heightDetails(r, amsl ? "amsl" : "agl", r.issues, units);
    const origin = typeof info.params.origin_name === "string" && info.params.origin_name ? info.params.origin_name : "Origin";
    const destination =
      typeof info.params.destination_name === "string" && info.params.destination_name ? info.params.destination_name : "destination";
    return {
      ...base,
      verdict: null,
      summary: `${origin} → ${destination}: planning-level corridor evidence, not a route approval.`,
      banners: heights.banners,
      kpis: [
        { label: "Route length", value: lengthParts(r.length_km * 1000, units), explanation: `Straight line ${formatLength(r.straight_km * 1000, units)}` },
        {
          label: "Detour",
          value: plain(formatNumber(r.detour_pct, { maximumFractionDigits: 1 }), "%"),
          tone: r.detour_pct > 30 ? "warn" : "neutral",
          toneLabel: r.detour_pct > 30 ? "Long detour" : undefined,
        },
        { label: "Flight time", value: plain(formatDuration(r.flight_time_min * 60)) },
        { label: "Energy", value: num(r.energy_kwh, "kWh", 1) },
        {
          label: "Required cruise (AMSL)",
          value: heightParts(r.required_cruise_amsl_m, units),
          explanation: amsl
            ? `The ${formatHeight(r.cruise_altitude_amsl_m ?? null, units)} level flown`
            : `${formatHeight(r.cruise_altitude_agl_m, units)} above the highest ground`,
        },
        { label: "Highest terrain along the corridor", value: heightParts(r.max_terrain_m, units), explanation: "Above mean sea level" },
        {
          label: "People exposed to noise",
          value: num(r.noise.exposed_population, "", 0),
          explanation: `≥ ${formatNumber(r.noise.threshold_db)} dB within ${formatLength(r.noise.footprint_half_width_m, units)} of the route`,
        },
        { label: "Zones crossed", value: num(r.zones.length) },
        {
          label: "Obstacle conflicts",
          value: num(r.obstacles.filter((o) => o.conflict).length),
          tone: r.obstacles.some((o) => o.conflict) ? "danger" : "ok",
          toneLabel: r.obstacles.some((o) => o.conflict) ? "Conflicts" : "None",
          explanation: `${plural(r.obstacles.length, "obstacle")} near the corridor`,
        },
        ...heights.kpis,
      ],
      tables: [
        table("Zones crossed", ZONE_COLUMNS, zoneRows(r.zones, units)),
        table("Obstacles (conflicts first)", OBSTACLE_COLUMNS, obstacleRows(r.obstacles, units)),
        ...heights.tables,
      ].filter((t) => t.total > 0),
      issues: r.issues,
      disclaimer: r.disclaimer,
    };
  }

  if (isAnalysisOfKind(info, "show") && info.result) {
    const r = info.result;
    const conflicts = r.conflicts.length;
    const violations = r.violations.length;
    return {
      ...base,
      verdict: r.verdict,
      summary:
        (conflicts === 0 && violations === 0
          ? `No conflicts or limit violations across ${formatNumber(r.frames_checked)} frames.`
          : `${plural(conflicts, "conflict")} and ${plural(violations, "violation")}.`) +
        (r.events_truncated ? " More events exist than are listed (event limit reached)." : ""),
      kpis: [
        { label: "Drones", value: num(r.drone_count), explanation: `${formatClock(r.duration_s)} long` },
        {
          label: "Minimum separation",
          value: lengthParts(r.min_separation_m, units, { maximumFractionDigits: 2 }),
          explanation: r.min_separation_pair ? `${r.min_separation_pair.join(" – ")} at ${formatClock(r.min_separation_at_s)}` : undefined,
        },
        { label: "Conflicts", value: num(conflicts), tone: conflicts > 0 ? "danger" : "ok", toneLabel: conflicts > 0 ? "Too close" : "None" },
        { label: "Limit violations", value: num(violations), tone: violations > 0 ? "danger" : "ok", toneLabel: violations > 0 ? "Check table" : "None" },
        { label: "Checked at", value: plain(formatNumber(r.sample_rate_hz), "Hz"), explanation: `${r.backend === "cpp" ? "C++" : "Python"} kernel` },
        ...showSiteKpis(r, info.params.endurance_s, units),
      ],
      note: describeShowOrigin(r, units),
      tables: [
        table(
          "Separation conflicts",
          [{ header: "Time", numeric: true }, { header: "Drones" }, { header: "Closest", numeric: true }, { header: "Window", numeric: true }],
          r.conflicts.map((c) => [
            formatClock(c.t_min_s),
            `${c.drone_a} – ${c.drone_b}`,
            formatLength(c.min_distance_m, units, { maximumFractionDigits: 2 }),
            `${formatClock(c.start_s)} – ${formatClock(c.end_s)}`,
          ]),
        ),
        table(
          "Dynamic and site violations",
          [{ header: "From", numeric: true }, { header: "Drone" }, { header: "Check" }, { header: "Peak", numeric: true }, { header: "Limit", numeric: true }],
          r.violations.map((v) => {
            const isSpeed = v.kind === "speed_h" || v.kind === "climb" || v.kind === "descent" || v.kind === "low_altitude_motion";
            const fmt = (x: number) =>
              isSpeed
                ? formatSpeed(x, units)
                : v.kind === "acceleration"
                  ? `${formatNumber(x, { maximumFractionDigits: 2 })} m/s²`
                  : v.kind === "endurance"
                    ? formatDuration(x) // airborne time against the endurance, in seconds
                    : formatLength(x, units);
            return [formatClock(v.start_s), v.drone, SHOW_VIOLATION_LABEL[v.kind] ?? v.kind.replace(/_/g, " "), fmt(v.peak_value), fmt(v.limit)];
          }),
        ),
        table(
          `Pads closer than ${formatLength(r.pads?.limit_m ?? null, units, { maximumFractionDigits: 2 })}${r.pads?.conflicts_truncated ? " (more exist)" : ""}`,
          [{ header: "When" }, { header: "Drones" }, { header: "Apart", numeric: true }],
          (r.pads?.conflicts ?? []).map((c) => [
            c.phase === "takeoff" ? "Take-off" : "Landing",
            `${c.drone_a} – ${c.drone_b}`,
            formatLength(c.distance_m, units, { maximumFractionDigits: 2 }),
          ]),
        ),
      ].filter((t) => t.total > 0),
      issues: r.issues,
      disclaimer: null,
    };
  }

  if (isAnalysisOfKind(info, "flight_audit") && info.result) {
    const r = info.result;
    const over = r.exceedances.length;
    // The limit actually used (zone and default rules can lower the requested one), as in the workspace.
    const limits = auditLimits(r);
    // A 2D position track has no heights: never "within the limit" (flight-audit/index.tsx does the same).
    const heightsAssessed = r.heights_assessed !== false;
    const banners: string[] = [];
    if (r.assessment_complete === false) {
      banners.push(
        `Airspace not assessed: ${issueMessage(r.issues, "not_assessed") ?? "no data pack covers this flight, so aerodrome zones were not checked. Treat the flight as unverified there."}`,
      );
    }
    if (!heightsAssessed) banners.push("Heights not assessed: the track has no altitudes, so the height limit was not checked.");
    const flight = r.flight;
    const isTrack = flight.source === "track";
    const flightName = flight.file_name ?? flight.report_id ?? flight.track_name ?? (isTrack ? "Position track" : "FlightMD flight");
    const areaExits = r.area_exits ?? null;
    const farSegments = r.distance_exceedances ?? null;
    const farLimit = r.max_distance_from_home_m ?? null;
    const extraKpis: ReportKpi[] = [];
    if (typeof r.duration_s === "number") {
      extraKpis.push({
        label: "Flight time",
        value: plain(formatDuration(r.duration_s)),
        explanation: !heightsAssessed
          ? undefined
          : typeof r.time_above_limit_s === "number" && r.time_above_limit_s > 0
            ? `${formatDuration(r.time_above_limit_s)} above the height limit`
            : "Never above the height limit",
      });
    }
    if (typeof r.max_home_distance_m === "number") {
      extraKpis.push({
        label: "Farthest from take-off",
        value: lengthParts(r.max_home_distance_m, units),
        tone: farLimit === null ? "neutral" : farSegments && farSegments.length > 0 ? "danger" : "ok",
        toneLabel: farLimit === null ? undefined : farSegments && farSegments.length > 0 ? "Beyond the distance" : "Within the distance",
        explanation: farLimit === null ? undefined : `Limit ${formatLength(farLimit, units)}`,
      });
    }
    if (areaExits !== null) {
      extraKpis.push({
        label: "Outside the training area",
        value: plain(typeof r.time_outside_area_s === "number" ? formatDuration(r.time_outside_area_s) : formatNumber(areaExits.length)),
        tone: areaExits.length > 0 ? "danger" : "ok",
        toneLabel: areaExits.length > 0 ? `Left it ${plural(areaExits.length, "time")}` : "Stayed inside",
        explanation: r.training_box_name ?? undefined,
      });
    }
    return {
      ...base,
      verdict: r.verdict,
      summary: !heightsAssessed
        ? "Heights not assessed: the track has no altitudes."
        : over > 0
          ? `${plural(over, "segment")} above the limit; highest ${formatHeight(r.max_agl_m, units)} above ground.`
          : describeWithinLimit(limits, r.max_agl_m, units),
      banners,
      note: [
        [flightName, isTrack && flight.track_format ? TRACK_FORMAT_LABEL[flight.track_format] : null, flight.log_start_utc ? formatDate(flight.log_start_utc, { withTime: true }) : null]
          .filter(Boolean)
          .join(" · "),
        heightsAssessed
          ? ALTITUDE_REFERENCE_TEXT[r.altitude_reference] ?? null
          : "The track is 2D (no altitudes): only positions, distances and times were audited.",
      ]
        .filter(Boolean)
        .join(". "),
      kpis: [
        heightsAssessed
          ? {
              label: "Max height above ground",
              value: heightParts(r.max_agl_m, units),
              tone: over > 0 ? "danger" : "ok",
              toneLabel: over > 0 ? "Above the limit" : "Within the limit",
              explanation: describeLimit(limits, units),
            }
          : {
              label: "Max height above ground",
              value: plain("Not recorded"),
              tone: "warn",
              toneLabel: "Not assessed",
              explanation: "The track has no altitudes, so the height limit was not checked.",
            },
        ...(heightsAssessed ? [{ label: "Max height above take-off", value: heightParts(r.max_height_above_takeoff_m, units) }] : []),
        { label: "Distance flown", value: lengthParts(r.distance_m, units), explanation: `${formatNumber(r.flight.points)} GPS points` },
        ...extraKpis,
        {
          label: "Nearest aerodrome",
          value: plain(r.nearest_aerodrome ? distanceKm(r.min_aerodrome_distance_km, units) : "None in data"),
          explanation: r.nearest_aerodrome ? `${r.nearest_aerodrome.name} (${r.nearest_aerodrome.ident})` : undefined,
        },
        {
          label: "People overflown",
          value: r.overflown_population === null ? plain("No data") : num(r.overflown_population, "", 0),
          explanation: r.max_density_per_km2 ? `Max ${formatNumber(r.max_density_per_km2, { maximumFractionDigits: 0 })} people/km²` : undefined,
        },
        { label: "Zones crossed", value: num(r.zones.length) },
      ],
      tables: [
        table(
          "Ceiling exceedances",
          [{ header: "From", numeric: true }, { header: "To", numeric: true }, { header: "Length", numeric: true }, { header: "Max AGL", numeric: true }, { header: "Limit", numeric: true }, { header: "Reason" }],
          r.exceedances.map((e) => [
            formatLength(e.start_distance_m, units, { maximumFractionDigits: 1 }),
            formatLength(e.end_distance_m, units, { maximumFractionDigits: 1 }),
            formatLength(e.length_m, units),
            formatHeight(e.max_agl_m, units),
            formatHeight(e.limit_m, units),
            e.reason,
          ]),
        ),
        table(
          `Segments outside ${r.training_box_name ?? "the training area"}`,
          auditSegmentColumns("Farthest outside"),
          auditSegmentRows(areaExits ?? [], units),
        ),
        table(
          `Beyond ${formatLength(farLimit, units)} from take-off`,
          auditSegmentColumns("Farthest"),
          auditSegmentRows(farSegments ?? [], units),
        ),
        table("Zones crossed", ZONE_COLUMNS, zoneRows(r.zones, units)),
      ].filter((t) => t.total > 0),
      issues: r.issues,
      disclaimer: r.disclaimer,
    };
  }

  if (isAnalysisOfKind(info, "line_of_sight") && info.result) {
    // Same names as the line-of-sight results in the workspace (components/modules/line-of-sight).
    const r = info.result;
    const radio = r.radio;
    const pct = (v: number | null | undefined) => num(v, "%", 1);
    const mhz = radio ? (radio.frequency_mhz >= 1000 ? `${formatNumber(radio.frequency_mhz / 1000, { maximumFractionDigits: 3 })} GHz` : `${formatNumber(radio.frequency_mhz)} MHz`) : "";
    const kpis: ReportKpi[] = [
      { label: "Area in visual line of sight", value: pct(r.combined_visible_pct), explanation: `Aircraft ${formatHeight(r.target_height_agl_m, units)} above ground` },
    ];
    if (r.radio_los_pct !== null) kpis.push({ label: "Area in radio line of sight", value: pct(r.radio_los_pct), explanation: radio ? `From ${radio.station_name}` : undefined });
    if (radio) {
      const tone = radio.worst_clearance_pct === null ? "neutral" : radio.paths_blocked > 0 ? "danger" : radio.paths_below_threshold > 0 ? "warn" : "ok";
      kpis.push({
        label: "Worst Fresnel clearance",
        value: pct(radio.worst_clearance_pct),
        tone,
        toneLabel: tone === "danger" ? "Line of sight cut" : tone === "warn" ? `Below ${formatNumber(radio.clearance_threshold_pct)}%` : tone === "ok" ? `At least ${formatNumber(radio.clearance_threshold_pct)}%` : undefined,
        explanation: `First Fresnel zone at ${mhz}`,
      });
      if (radio.max_fspl_db !== null) kpis.push({ label: "Free-space path loss", value: num(radio.max_fspl_db, "dB", 1), explanation: "Longest path; geometry only, not a link budget" });
    }
    kpis.push(
      {
        label: "Largest hidden patch",
        value: r.largest_hidden_patch ? areaParts(r.largest_hidden_patch.area_m2, units) : plain("None"),
        explanation: "Hidden from every pilot and observer",
      },
      { label: "VLOS guidance distance", value: lengthParts(r.vlos_guidance.guidance_m, units), explanation: "Non-regulatory guidance" },
    );
    return {
      ...base,
      verdict: null,
      summary: "Geometric evidence from the data-pack surface model — not a VLOS approval, link budget or coverage prediction.",
      kpis,
      tables: [
        table(
          "Pilot, observers and ground station",
          [{ header: "Name" }, { header: "Role" }, { header: "Sees of the area", numeric: true }, { header: "Height above ground", numeric: true }, { header: "Position" }],
          r.observers.map((o) => [
            o.name + (o.assumed ? " (assumed)" : ""),
            o.role === "gcs" ? "Ground station" : o.role === "observer" ? "Visual observer" : "Remote pilot",
            pct(o.visible_pct).text,
            formatHeight(o.height_agl_m, units),
            formatCoord(o.lon, o.lat),
          ]),
        ),
        table(
          "Radio paths",
          [{ header: "To" }, { header: "Distance", numeric: true }, { header: "Worst clearance", numeric: true }, { header: "Free-space loss", numeric: true }],
          (radio?.paths ?? []).map((p) => [p.to_name, formatLength(p.distance_m, units), pct(p.worst_clearance_pct).text, `${formatNumber(p.fspl_db, { decimals: 1 })} dB`]),
        ),
        table(
          "Hidden stretches of the route",
          [{ header: "From", numeric: true }, { header: "To", numeric: true }, { header: "Length", numeric: true }],
          (r.route?.hidden_segments ?? []).map((s) => [formatLength(s.start_m, units), formatLength(s.end_m, units), formatLength(s.length_m, units)]),
        ),
      ].filter((t) => t.total > 0),
      issues: r.issues,
      disclaimer: r.disclaimer,
    };
  }

  if (isAnalysisOfKind(info, "environment") && info.result) {
    // Same names as the environment results in the workspace (components/modules/environment).
    const r = info.result;
    const zone = r.utc_offset_label;
    const at = (e: SunEvent | null) => plain(e ? localWithDay(e.local, e.day_offset) : "None", e ? zone : "");
    const polar = r.sun.polar_day ? "Polar day: the sun stays up all day" : r.sun.polar_night ? "Polar night: the sun stays down all day" : undefined;
    const m = r.magnetic;
    const kpis: ReportKpi[] = [
      { label: "Sunrise", value: at(r.sun.sunrise), explanation: polar ?? `Civil dawn ${at(r.sun.civil_dawn).value}` },
      { label: "Sunset", value: at(r.sun.sunset), explanation: polar },
      { label: "Civil dusk", value: at(r.sun.civil_dusk), explanation: "Sun's centre 6° below the horizon" },
    ];
    const w = r.daylight.window;
    if (w) {
      const outside = r.daylight.minutes_outside ?? 0;
      const lighting = r.daylight.minutes_lighting ?? 0;
      kpis.push({
        label: "Planned window",
        value: num(outside, "min outside", 0),
        explanation: `${w.start_local}–${w.end_local}, ${DAYLIGHT_PROFILE_LABEL[r.daylight.profile].toLowerCase()}`,
        tone: outside >= 0.5 ? "warn" : lighting >= 0.5 ? "info" : "neutral",
        toneLabel: outside >= 0.5 ? "Outside the profile" : lighting >= 0.5 ? `Lighting required for ${formatNumber(lighting, { maximumFractionDigits: 0 })} min` : undefined,
      });
    }
    kpis.push({
      label: "Magnetic declination",
      value: plain(`${formatNumber(m.declination_deg, { decimals: 1 })}°`),
      explanation: `${Math.abs(m.declination_deg) < 0.05 ? "Along" : m.declination_deg > 0 ? "East of" : "West of"} true north; inclination ${formatNumber(m.inclination_deg, { decimals: 1 })}° (${m.model})`,
      tone: m.blackout ? "danger" : m.caution || !m.within_validity ? "warn" : "neutral",
      toneLabel: m.blackout ? "WMM blackout zone (H below 2000 nT)" : m.caution ? "WMM caution zone (H below 6000 nT)" : !m.within_validity ? "Outside the model's validity" : undefined,
    });
    if (r.wind) {
      const wind = r.wind;
      const over = (wind.margin_ms ?? 0) < 0 || (wind.gust_margin_ms ?? 0) < 0;
      kpis.push({
        label: `Wind at ${formatHeight(r.height_agl_m, units)}`,
        value: speedParts(wind.wind_at_height_ms, units),
        explanation:
          wind.margin_ms !== null && wind.wind_limit_ms !== null
            ? `${formatSpeed(Math.abs(wind.margin_ms), units)} ${wind.margin_ms >= 0 ? "below" : "above"} the ${formatSpeed(wind.wind_limit_ms, units)} rated limit (power-law estimate)`
            : `Power-law estimate from ${formatSpeed(wind.surface_wind_ms, units)} at ${formatHeight(wind.reference_height_m, units)}`,
        tone: over ? "warn" : "neutral",
        toneLabel: over ? "Estimate above the rated limit" : undefined,
      });
    }
    const line = r.emi.find((i) => i.source === "power" && (i.kind === "power_line" || i.kind === "minor_line" || i.kind === "cable")) ?? null;
    const emiName = (i: EmiItem) => {
      const head = i.voltage_kv !== null ? `${formatNumber(i.voltage_kv)} kV ${EMI_KIND_LABEL[i.kind].toLowerCase()}` : EMI_KIND_LABEL[i.kind];
      return i.name ? `${head} · ${i.name}` : head;
    };
    const powerChecked = r.emi_assessed && !r.issues.some((i) => i.code === "power_not_assessed");
    kpis.push({
      label: "Nearest power line",
      value: !powerChecked ? plain("Not assessed") : line ? (line.inside ? plain("Crosses") : lengthParts(line.distance_m, units)) : plain("None"),
      explanation: !powerChecked ? undefined : line ? emiName(line) : `No power line within ${formatLength(r.emi_radius_m, units)}`,
      tone: line?.inside ? "warn" : "neutral",
      toneLabel: line?.inside ? "Crosses or touches the analysed geometry" : undefined,
    });
    return {
      ...base,
      verdict: null,
      summary: `${r.daylight.statement} Local times ${zone}. Evidence for planning, not a go/no-go.`,
      kpis,
      tables: [
        table(
          "Planned window by sun state",
          [{ header: "Sun" }, { header: "Profile" }, { header: "Start", numeric: true }, { header: "End", numeric: true }, { header: "Duration", numeric: true }],
          r.daylight.window_segments.map((s) => [
            DAYLIGHT_SEGMENT_LABEL[s.kind],
            DAYLIGHT_STATUS_LABEL[s.status],
            s.start_local,
            s.end_local,
            formatDuration(s.minutes * 60),
          ]),
        ),
        table(
          `Power lines and masts within ${formatLength(r.emi_radius_m, units)} (proximity evidence)`,
          [{ header: "Feature" }, { header: "Voltage", numeric: true }, { header: "Distance", numeric: true }, { header: "Source" }],
          r.emi.map((i) => [
            emiName(i),
            i.voltage_kv !== null ? `${formatNumber(i.voltage_kv)} kV` : EMPTY,
            i.inside ? (i.kind === "power_line" || i.kind === "minor_line" || i.kind === "cable" ? "Crosses" : "Inside") : formatLength(i.distance_m, units),
            i.source === "power" ? "Power layer" : "Obstacle layer",
          ]),
        ),
      ].filter((t) => t.total > 0),
      issues: r.issues,
      disclaimer: r.disclaimer,
    };
  }

  return null;
}

const VERDICT_VARIANT: Record<Verdict, "ok" | "warn" | "danger"> = { pass: "ok", caution: "warn", fail: "danger" };

/** Issues with severity icon + text. `label` names the list ("Notes" for analyses, as in the workspace). */
export function IssueList({ issues, label = "Notes", className }: { issues: readonly Issue[]; label?: string; className?: string }) {
  if (issues.length === 0) return null;
  return (
    <ul className={cn("space-y-1 text-sm", className)} aria-label={label}>
      {issues.map((issue, i) => {
        const tone = toneForSeverity(issue.severity);
        const Icon = tone === "danger" ? OctagonAlert : tone === "warn" ? AlertTriangle : Info;
        return (
          <li key={`${issue.code}-${i}`} className="flex items-start gap-2">
            <Icon
              className={cn("mt-0.5 size-4 shrink-0", tone === "danger" ? "text-danger" : tone === "warn" ? "text-warn" : "text-accent")}
              aria-hidden
            />
            <span>
              <span className="font-medium capitalize">{issue.severity}</span>
              {issue.feature_index !== null ? <span className="text-fg-muted"> · feature {issue.feature_index}</span> : null}
              <span className="text-fg-muted">: </span>
              {issue.message}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function ReportDataTable({ data }: { data: ReportTable }) {
  return (
    <div className="print-avoid-break space-y-1">
      <Table>
        <caption className="mb-1 text-left text-sm font-medium text-fg">{data.caption}</caption>
        <TableHeader>
          <TableRow>
            {data.columns.map((c) => (
              <TableHead key={c.header} className={c.numeric ? "text-right" : undefined}>
                {c.header}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.rows.map((row, i) => (
            <TableRow key={i}>
              {row.map((cell, j) => (
                <TableCell key={j} className={cn(data.columns[j]?.numeric && "num whitespace-nowrap text-right", "align-top")}>
                  {cell}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {data.total > data.rows.length ? (
        <p className="text-xs text-fg-muted">
          Showing {data.rows.length} of {data.total} rows. The full list is in the CSV and JSON exports.
        </p>
      ) : null}
    </div>
  );
}

/** Verdict, KPI tiles, tables, notes and disclaimer for one analysis. */
export function AnalysisSummary({ analysis, units }: { analysis: AnalysisInfo; units: Units }) {
  const data = summarizeAnalysis(analysis, units);
  if (!data) {
    return (
      <Alert variant="info">
        <AlertTitle>No result to show</AlertTitle>
        <AlertDescription>This analysis has not produced a result.</AlertDescription>
      </Alert>
    );
  }
  return (
    <div className="space-y-4">
      {data.verdict ? (
        <Alert variant={VERDICT_VARIANT[data.verdict]} role="status" className="print-avoid-break">
          <AlertTitle>
            {data.title}: {VERDICT_LABEL[data.verdict]}
          </AlertTitle>
          {data.summary ? <AlertDescription>{localizeUnits(data.summary, units)}</AlertDescription> : null}
        </Alert>
      ) : data.summary ? (
        <p className="text-sm text-fg-muted">{localizeUnits(data.summary, units)}</p>
      ) : null}
      {(data.banners ?? []).map((banner) => {
        const [head, ...rest] = banner.split(": ");
        return (
          <Alert key={banner} variant="warn" className="print-avoid-break">
            <AlertTitle>{rest.length > 0 ? head : "Not assessed"}</AlertTitle>
            <AlertDescription>{localizeUnits(rest.length > 0 ? rest.join(": ") : banner, units)}</AlertDescription>
          </Alert>
        );
      })}
      <KpiGrid label={`${data.title} key numbers`}>
        {data.kpis.map((k) => (
          <KpiTile
            key={k.label}
            label={k.label}
            value={k.value}
            explanation={k.explanation}
            tone={k.tone && k.tone !== "neutral" ? k.tone : "neutral"}
            toneLabel={k.tone && k.tone !== "neutral" ? k.toneLabel : undefined}
            className="print-avoid-break"
          />
        ))}
      </KpiGrid>
      {data.note ? <p className="text-xs text-fg-muted">{data.note}</p> : null}
      {data.tables.map((t) => (
        <ReportDataTable key={t.caption} data={t} />
      ))}
      {data.issues.length > 0 ? (
        <div className="space-y-1">
          <h3 className="text-sm font-medium">Notes ({data.issues.length})</h3>
          <IssueList issues={data.issues} />
        </div>
      ) : null}
      {data.disclaimer ? (
        <p className="border-t border-border pt-2 text-xs text-fg-muted" role="note">
          <span className="font-medium text-fg">Disclaimer: </span>
          {data.disclaimer}
        </p>
      ) : null}
    </div>
  );
}
