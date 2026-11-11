/**
 * Workspace module types and static metadata. The components are wired up in
 * `components/modules/registry.tsx`, which builds each `ModuleDefinition` from `MODULE_META` plus
 * the module's Panel / Results / buildLayers.
 *
 * `ModuleId` is the `?m=` URL value (kebab-case); `kinds` are API analysis kinds (snake_case).
 */

import type { ComponentType } from "react";
import type { Layer, PickingInfo } from "@deck.gl/core";
import type { HatchAtlas } from "./colors";
import { formatDate, formatHeight, formatLength, formatNumber, formatSpeed, formatUtcOffset } from "./format";
import { DAYLIGHT_PROFILE_SHORT } from "./labels";
import type { Units } from "./store";
import type { AnalysisInfo, AnalysisKind, DatasetFeatureCollection, FeatureCollection, FileInfo, ZoneKind } from "./types";

export type ModuleId = "measure" | "airspace" | "ground-risk" | "evtol" | "shows" | "survey" | "flight-audit" | "environment" | "line-of-sight";

export const MODULE_IDS: readonly ModuleId[] = [
  "measure",
  "airspace",
  "ground-risk",
  "evtol",
  "shows",
  "survey",
  "flight-audit",
  "environment",
  "line-of-sight",
];

/** Lucide icon component names used by the rail (import these by name from lucide-react). */
export type ModuleIconName = "Ruler" | "Plane" | "Users" | "Building2" | "Sparkles" | "Grid3x3" | "Route" | "Sun" | "RadioTower";

export interface ModuleMetaEntry {
  id: ModuleId;
  /** short rail label */
  label: string;
  /** one sentence for tooltips, the command palette and the landing page */
  description: string;
  /** lucide-react icon component name */
  icon: ModuleIconName;
  /** analysis kinds this module runs/displays; empty for the measure module */
  kinds: readonly AnalysisKind[];
}

export const MODULE_META: Readonly<Record<ModuleId, ModuleMetaEntry>> = {
  measure: {
    id: "measure",
    label: "Measure",
    description: "Area, perimeter and length of every feature, with the CRS and geodesic check used.",
    icon: "Ruler",
    kinds: [],
  },
  airspace: {
    id: "airspace",
    label: "Airspace",
    description: "Indicative airspace zones, ceilings, terrain and obstacles for a planned flight.",
    icon: "Plane",
    kinds: ["airspace"],
  },
  "ground-risk": {
    id: "ground-risk",
    label: "Ground risk",
    description: "SORA 2.5 evidence: the operational volume, people under and around it, and the intrinsic ground risk class.",
    icon: "Users",
    kinds: ["ground_risk"],
  },
  evtol: {
    id: "evtol",
    label: "eVTOL",
    description: "Score vertiport sites and route an air-taxi corridor between two of them.",
    icon: "Building2",
    kinds: ["evtol_siting", "evtol_corridor"],
  },
  shows: {
    id: "shows",
    label: "Shows",
    description: "Validate a drone-show choreography: separation, dynamics, geofence and audience safety.",
    icon: "Sparkles",
    kinds: ["show"],
  },
  survey: {
    id: "survey",
    label: "Survey",
    description: "Estimate mapping or spraying effort over an area or a corridor: GSD over relief, photos, batteries, time, GCPs and spray mix.",
    icon: "Grid3x3",
    kinds: ["survey"],
  },
  "flight-audit": {
    id: "flight-audit",
    label: "Flight audit",
    description: "Audit a flown track (FlightMD, GPX, KML or CSV) against height limits, zones, a training area and distance from take-off.",
    icon: "Route",
    kinds: ["flight_audit"],
  },
  environment: {
    id: "environment",
    label: "Environment",
    description: "Sun and civil twilight, magnetic declination, wind at height and nearby power lines and masts, as evidence.",
    icon: "Sun",
    kinds: ["environment"],
  },
  "line-of-sight": {
    id: "line-of-sight",
    label: "Line of sight",
    description: "Terrain viewshed from the pilot and observers, and radio path Fresnel clearance, as geometric evidence.",
    icon: "RadioTower",
    kinds: ["line_of_sight"],
  },
};

/** Modules in rail order. */
export const MODULE_LIST: readonly ModuleMetaEntry[] = MODULE_IDS.map((id) => MODULE_META[id]);

export function isModuleId(value: unknown): value is ModuleId {
  return typeof value === "string" && (MODULE_IDS as readonly string[]).includes(value);
}

/** The module that owns an analysis kind (e.g. "evtol_corridor" → "evtol"). */
export function moduleForKind(kind: AnalysisKind): ModuleId {
  for (const id of MODULE_IDS) {
    if (MODULE_META[id].kinds.includes(kind)) return id;
  }
  return "measure";
}

/**
 * Result layers (`analysis.result.layers` keys) that show where an analysis' answer is. The
 * workspace brings these into view when a result arrives. Other layers can reach far beyond
 * them (e.g. airspace zone rings around distant aerodromes), so they are left out.
 */
export const RESULT_FOCUS_LAYERS: Readonly<Record<AnalysisKind, readonly string[]>> = {
  airspace: ["input"],
  ground_risk: ["footprint", "input"],
  evtol_siting: ["candidates", "input"],
  evtol_corridor: ["route", "corridor", "endpoints"],
  show: ["paths", "show_box", "audience_buffer"],
  survey: ["input", "blocks", "lines"],
  flight_audit: ["path", "home", "training_box"],
  environment: ["input", "emi_search"],
  line_of_sight: ["input", "observers", "radio_paths"],
};

function numberParam(params: Record<string, unknown>, key: string): number | null {
  const v = params[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function stringParam(params: Record<string, unknown>, key: string): string | null {
  const v = params[key];
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

function zoneRulesPart(p: Readonly<Record<string, unknown>>): string | null {
  if (p.zone_profile === "custom" || p.zone_rules) return "custom rules";
  // no zone_profile: stored before profiles, with the conservative rules
  return p.zone_profile === "dgca_2021" ? null : "conservative rules";
}

function resultField(analysis: AnalysisInfo, key: string): unknown {
  const r = analysis.result as Record<string, unknown> | null;
  return r ? r[key] : undefined;
}

/**
 * A one-line summary of the parameters that tell runs of the same kind apart, for the
 * History table, e.g. "60 m AGL · grid" or "P4 RTK · 100 m AGL · 80/70% overlap". Values the request
 * left out fall back to the result (which echoes the value used); unknown parts are skipped.
 * `cameras` maps survey camera preset ids to their specs (`Meta.cameras`).
 */
export function analysisParamsSummary(
  analysis: AnalysisInfo,
  units: Units,
  cameras?: Readonly<Record<string, { name: string }>>,
): string {
  const p = analysis.params ?? {};
  const resultNumber = (key: string): number | null => {
    const v = resultField(analysis, key);
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  };
  const agl = (m: number | null) => (m === null ? null : `${formatHeight(m, units)} AGL`);
  // A planned height in its reference (airbrief_core heights): "120 m above take-off", "1,050 m AMSL".
  const planned = (m: number | null, reference: unknown) =>
    m === null ? null : reference === "takeoff" ? `${formatHeight(m, units)} above take-off` : reference === "amsl" ? `${formatHeight(m, units)} AMSL` : agl(m);
  const parts: (string | null)[] = [];
  switch (analysis.kind) {
    case "airspace":
      parts.push(planned(numberParam(p, "altitude_agl_m") ?? resultNumber("altitude_agl_m"), p.height_reference));
      if (p.grid !== false) parts.push("grid");
      parts.push(zoneRulesPart(p));
      if (Array.isArray(p.zone_dataset_ids) && p.zone_dataset_ids.length > 0) {
        parts.push(`zones from ${formatNumber(p.zone_dataset_ids.length)} layer${p.zone_dataset_ids.length === 1 ? "" : "s"}`);
      }
      break;
    case "ground_risk": {
      const dim = numberParam(p, "max_dimension_m");
      const speed = numberParam(p, "max_speed_ms");
      parts.push(dim === null ? null : `${formatLength(dim, units)} span`);
      parts.push(speed === null ? null : formatSpeed(speed, units));
      parts.push(agl(numberParam(p, "altitude_agl_m")));
      if (p.buffer_rule === "ballistic") parts.push("ballistic buffer");
      if (p.volume && typeof p.volume === "object") parts.push("SORA 2.5 volume");
      if (Array.isArray(p.assemblies) && p.assemblies.length > 0) parts.push(`${formatNumber(p.assemblies.length)} event${p.assemblies.length === 1 ? "" : "s"}`);
      break;
    }
    case "evtol_siting": {
      const top = numberParam(p, "top_n");
      const sep = numberParam(p, "min_separation_km");
      parts.push(top === null ? null : `top ${formatNumber(top)}`);
      parts.push(sep === null ? null : `${formatLength(sep * 1000, units)} apart`);
      parts.push(zoneRulesPart(p));
      if (p.unverified_band === "exclude") parts.push("airport bands kept out");
      break;
    }
    case "evtol_corridor": {
      const from = stringParam(p, "origin_name");
      const to = stringParam(p, "destination_name");
      const width = numberParam(p, "corridor_width_m");
      parts.push(from && to ? `${from} → ${to}` : null);
      parts.push(width === null ? null : `${formatLength(width, units)} wide`);
      parts.push(
        p.cruise_reference === "amsl"
          ? planned(numberParam(p, "cruise_altitude_amsl_m"), "amsl")
          : agl(numberParam(p, "cruise_altitude_agl_m") ?? resultNumber("cruise_altitude_agl_m")),
      );
      parts.push(zoneRulesPart(p));
      if (p.unverified_band === "exclude") parts.push("airport bands kept out");
      break;
    }
    case "show": {
      const drones = Array.isArray(p.tracks) ? p.tracks.length : resultNumber("drone_count");
      const sep = numberParam(p, "min_separation_m");
      parts.push(drones === null ? null : `${formatNumber(drones)} drone${drones === 1 ? "" : "s"}`);
      parts.push(sep === null ? null : `${formatLength(sep, units)} separation`);
      if (p.audience_buffer_rule === "ballistic" || p.audience_buffer_rule === "one_to_one") {
        parts.push(p.audience_buffer_rule === "ballistic" ? "ballistic audience distance" : "1:1 audience distance");
      }
      break;
    }
    case "survey": {
      const coverage = p.coverage === "corridor" ? numberParam(p, "corridor_width_m") : null;
      if (p.mode === "spraying") {
        // e.g. "Spraying · 20 L/ha · 5 m swath · corridor 30 m wide"
        const spray = p.spray && typeof p.spray === "object" ? (p.spray as Record<string, unknown>) : {};
        const rate = numberParam(spray, "rate_l_per_ha") ?? 20;
        const swath = numberParam(spray, "swath_m") ?? 5;
        parts.push("Spraying", `${formatNumber(rate)} L/ha`, `${formatLength(swath, units)} swath`);
        parts.push(coverage === null ? null : `corridor ${formatLength(coverage, units)} wide`);
        break;
      }
      const camera = p.camera;
      const resultCamera = resultField(analysis, "camera") as { name?: unknown } | undefined;
      parts.push(
        typeof camera === "string"
          ? (cameras?.[camera]?.name ?? camera)
          : camera && typeof camera === "object" && typeof (camera as { name?: unknown }).name === "string"
            ? (camera as { name: string }).name
            : typeof resultCamera?.name === "string"
              ? resultCamera.name
              : null,
      );
      const gsd = numberParam(p, "target_gsd_cm");
      parts.push(
        gsd !== null ? `${formatNumber(gsd)} cm/px` : planned(numberParam(p, "altitude_agl_m") ?? resultNumber("altitude_agl_m"), p.height_reference),
      );
      const front = numberParam(p, "front_overlap_pct");
      const side = numberParam(p, "side_overlap_pct");
      parts.push(front !== null && side !== null ? `${formatNumber(front)}/${formatNumber(side)}% overlap` : null);
      parts.push(coverage === null ? null : `corridor ${formatLength(coverage, units)} wide`);
      if (p.transit_legs === true) parts.push("transit legs");
      break;
    }
    case "flight_audit": {
      const ceiling = numberParam(p, "ceiling_agl_m") ?? resultNumber("ceiling_agl_m");
      parts.push(ceiling === null ? null : `${formatHeight(ceiling, units)} ceiling`);
      const flight = resultField(analysis, "flight") as { file_name?: unknown; track_name?: unknown } | undefined;
      const track = p.track && typeof p.track === "object" ? (p.track as { name?: unknown }) : null;
      const flightName = [flight?.file_name, flight?.track_name, track?.name].find((v): v is string => typeof v === "string" && v.length > 0);
      parts.push(flightName ?? null);
      const farLimit = numberParam(p, "max_distance_from_home_m");
      parts.push(farLimit === null ? null : `${formatLength(farLimit, units)} from take-off`);
      parts.push(zoneRulesPart(p));
      if (p.band_ceiling === "permission") parts.push("band ceilings as permission");
      break;
    }
    case "environment": {
      // e.g. "21 Jun 2026 · 17:30–19:30 UTC+05:30 · FAA Part 107 · 120 m AGL"
      const day = stringParam(p, "date");
      parts.push(day ? formatDate(day, { withTime: false, utc: true }) : null);
      const start = stringParam(p, "window_start");
      const end = stringParam(p, "window_end");
      const offset = numberParam(p, "utc_offset_min");
      parts.push(start && end ? `${start}–${end}${offset === null ? "" : ` ${formatUtcOffset(offset)}`}` : null);
      const profile = typeof p.daylight_profile === "string" && p.daylight_profile in DAYLIGHT_PROFILE_SHORT
        ? (p.daylight_profile as keyof typeof DAYLIGHT_PROFILE_SHORT)
        : "india_daylight";
      const minSun = numberParam(p, "min_sun_elevation_deg") ?? 0;
      parts.push(profile === "custom" ? `sun ≥ ${formatNumber(minSun)}°` : DAYLIGHT_PROFILE_SHORT[profile]);
      parts.push(agl(numberParam(p, "height_agl_m") ?? resultNumber("height_agl_m")));
      break;
    }
    case "line_of_sight": {
      // e.g. "120 m AGL · 2 observers · 2.4 GHz"
      parts.push(agl(numberParam(p, "target_height_agl_m") ?? resultNumber("target_height_agl_m") ?? 120));
      const observers = Array.isArray(p.observers) ? p.observers.length : null;
      parts.push(observers === null || observers === 0 ? null : `${formatNumber(observers)} observer${observers === 1 ? "" : "s"}`);
      const mhz = p.radio_check === false ? null : (numberParam(p, "frequency_mhz") ?? 2400);
      parts.push(mhz === null ? null : mhz >= 1000 ? `${formatNumber(mhz / 1000, { maximumFractionDigits: 3 })} GHz` : `${formatNumber(mhz)} MHz`);
      break;
    }
    default:
      break;
  }
  const batch = p.batch && typeof p.batch === "object" ? (p.batch as { feature_count?: unknown; features?: { features?: unknown } }) : null;
  if (batch) {
    // the API lists feature_count; older static demo dumps still have the features
    const n = typeof batch.feature_count === "number" ? batch.feature_count : Array.isArray(batch.features?.features) ? batch.features.features.length : null;
    parts.push(n === null ? "one result per feature" : `one result per feature (${formatNumber(n)})`);
  }
  return parts.filter((s): s is string => Boolean(s)).join(" · ");
}

// What a module implementation provides. The prose verison is in frontend/README.md, "Module contract".

/** Props for a module's inspector panel (form + run). */
export interface ModulePanelProps {
  datasetId: string | null;
  file: FileInfo | null;
  /** WGS84 features of the dataset (null while loading or without a dataset) */
  features: DatasetFeatureCollection | null;
  /** selected feature indices (from the URL `f`), empty when nothing is selected */
  selectedFeatures: number[];
  /** the analysis shown in the URL (`a`), only when its kind is one of this module's `kinds` */
  analysis: AnalysisInfo | null;
  /** call after `createAnalysis` succeeds so the workspace writes `?a=` and opens the drawer */
  onAnalysisStarted: (analysis: AnalysisInfo) => void;
}

/**
 * Props for a module's results drawer content.
 *
 * For modules with analysis `kinds`, the workspace renders `Results` only when `analysis` is
 * non-null AND `analysis.kind` is one of the module's kinds (any status: show progress /
 * error states yourself or rely on the drawer's job header). The measure module (no kinds)
 * receives `analysis: null` and works from `datasetId` / `file`.
 */
export interface ModuleResultsProps {
  analysis: AnalysisInfo | null;
  datasetId: string | null;
  file: FileInfo | null;
}

/** Everything a module needs to build its deck.gl layers. */
export interface ModuleLayerContext {
  /** same object as the first argument of `buildLayers` (kept for convenience) */
  analysis: AnalysisInfo | null;
  features: DatasetFeatureCollection | null;
  /** layer ids hidden by the user (`useUi().hiddenLayers`) */
  hiddenLayers: ReadonlySet<string>;
  hoveredFeature: number | null;
  selectedFeature: number | null;
  /** playback time in seconds (shows, flight audit); live while playing */
  time: number;
  /** highlighted result row of the active module (`useUi().resultHighlight`), e.g. "block-2" */
  resultHighlight: string | null;
  reducedMotion: boolean;
  units: Units;
  /** hatch atlas for zone fills (FillStyleExtension), null until created in the browser */
  hatch: HatchAtlas | null;
  theme: "dark" | "light";
  /** the map is in 3D (pitched) mode. Layer factories already apply the height rule (`withHeights`): flat in 2D, above the ground in 3D */
  view3d: boolean;
}

/** A toggleable map layer of a module, listed in the Layers panel with its legend. */
export interface ModuleLayerInfo {
  /**
   * Unique id, convention `${moduleId}:${layerKey}`, e.g. "airspace:zones". Every deck.gl
   * layer returned by `buildLayers` must have `id === info.id` or start with `${info.id}/`
   * (e.g. "airspace:zones/labels"): the workspace hides those when the user turns the layer
   * off. Deck layers whose id matches no ModuleLayerInfo are always shown.
   */
  id: string;
  label: string;
  /** analysis kinds for which this layer exists (omit = all of the module's kinds) */
  kinds?: readonly AnalysisKind[];
  /**
   * Key in `analysis.result.layers` (or in the module's `derivedLayers`) holding this layer's
   * GeoJSON, used by the drawer's "Layers as table" tab. Omit when the layer has no GeoJSON.
   */
  resultLayer?: string;
  /** listed only for one-result-per-feature analyses (`components/modules/batch.tsx`), which list nothing else */
  batch?: boolean;
}

/** One entry of a categorical legend. Never rely on colour alone: the label is required. */
export interface LegendItem {
  label: string;
  /** CSS colour: hex, rgb() or `var(--token)` */
  color: string;
  /** swatch shape (default "fill"); "hatch" = diagonal lines in `color` (e.g. a buffer ring) */
  shape?: "fill" | "line" | "dashed-line" | "point" | "ring" | "hex" | "hatch";
  /** draw the zone hatch swatch (colour + pattern) instead of `color` */
  zone?: ZoneKind;
  /** muted text after the label, e.g. a count or a range */
  detail?: string;
}

/** A legend block shown on the map (bottom-left). */
export interface LegendSpec {
  /** unique per map, e.g. "airspace:zones" */
  id: string;
  title: string;
  kind: "categorical" | "ramp";
  /** categorical entries */
  items?: readonly LegendItem[];
  /** ramp: CSS colours from low to high, evenly spaced along the bar */
  stops?: readonly string[];
  /** ramp: numeric break values (at least 2), evenly spaced along the bar, low to high */
  breaks?: readonly number[];
  /** unit printed after the breaks, e.g. "people/km²", "m", "score" */
  unit?: string;
  /** custom break formatter (default: formatNumber) */
  formatBreak?: (value: number) => string;
  /** id of the ModuleLayerInfo this legend describes: hidden with it, and "View as table" opens it */
  layerId?: string;
  /** one short sentence under the legend (e.g. "Indicative, not an official map") */
  note?: string;
}

export interface ModuleLayerOutput {
  layers: Layer[];
  legends: LegendSpec[];
}

export interface ModuleDefinition extends ModuleMetaEntry {
  /** inspector panel (form, presets, Advanced, Run) */
  Panel: ComponentType<ModulePanelProps>;
  /** results drawer content (KPIs, charts, tables) */
  Results?: ComponentType<ModuleResultsProps>;
  /** toggleable map layers (Layers panel + hiding) */
  layers?: readonly ModuleLayerInfo[];
  /**
   * Map layers + legends for an analysis of this module. Called by the workspace only when
   * `analysis.status === "COMPLETED"` and `analysis.result !== null` and the kind is one of
   * `kinds`. Must be pure and cheap (it runs on every hover/selection/time change; memoise
   * heavy data transforms by `analysis.id`). Return deck.gl layers with ids following
   * `ModuleLayerInfo.id`, `pickable: true` where hover/click should work.
   */
  buildLayers?: (analysis: AnalysisInfo, ctx: ModuleLayerContext) => ModuleLayerOutput;
  /** hover tooltip text for a picked object of one of this module's layers (null = none) */
  describePick?: (info: PickingInfo) => string | null;
  /** click on one of this module's layers; return true when handled (skips feature selection) */
  onPick?: (info: PickingInfo) => boolean;
  /**
   * Extra GeoJSON layers built from result fields that are not in `result.layers` (e.g. H3 cells
   * as polygons), merged with them for the Layers panel and "Layers as table". Called for a
   * completed analysis of this module; memoise by the source arrays (`derived`).
   */
  derivedLayers?: (analysis: AnalysisInfo) => Record<string, FeatureCollection>;
}
