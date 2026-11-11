"use client";

/**
 * eVTOL module: multi-criteria vertiport siting on an H3 grid, and least-cost corridor routing
 * between two sites (or two point features) with terrain, airspace, obstacle and noise checks.
 */

import * as React from "react";
import type { PickingInfo } from "@deck.gl/core";
import { Building2 } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";
import { VIRIDIS_STOPS, ZONE_LABEL, viridis } from "@/lib/colors";
import { areaParts, formatCoord, formatHeight, formatLength, formatNumber, formatPercent, heightParts, lengthParts, parseNumberText } from "@/lib/format";
import {
  MODULE_META,
  type LegendSpec,
  type ModuleDefinition,
  type ModuleLayerContext,
  type ModuleLayerOutput,
  type ModulePanelProps,
  type ModuleResultsProps,
} from "@/lib/modules";
import { useAnalyses, useMyDatasetAnalysisIds } from "@/lib/queries";
import { OBSTACLE_HEIGHT_SOURCE_LABEL, UNVERIFIED_BANDS, UNVERIFIED_BAND_HINT, UNVERIFIED_BAND_LABEL, ZONE_PROFILE_LABEL } from "@/lib/labels";
import { useSettings, useUi, useUnits } from "@/lib/store";
import {
  analysisResult,
  type AnalysisInfo,
  type AnalysisKind,
  type AnalysisRequest,
  type CorridorWeights,
  type EvtolCorridorResult,
  type EvtolSitingResult,
  type FeatureCollection,
  type Geometry,
  type HeightSegment,
  type NoiseCell,
  type ObstacleHit,
  type SiteCandidate,
  type SitingCell,
  type SitingWeights,
  type UnverifiedBand,
  type ZoneHit,
} from "@/lib/types";
import { derived, geoJsonLayer, h3Layer, mapPalette, pointLayer, textLabelLayer } from "@/components/map/layers/common";
import { Badge } from "@/components/ui/badge";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { KpiGrid, KpiTile } from "@/components/ui/kpi-tile";
import { Label } from "@/components/ui/label";
import { NumberInput } from "@/components/ui/number-input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAnalysisRunner } from "@/components/workspace/analysis-runner";
import { PlanStartHint } from "@/components/workspace/plan-start";
import {
  Advanced,
  DEFAULT_HEIGHT_REFERENCE,
  DemCaveat,
  Disclaimer,
  FormErrorSummary,
  HeightReferenceField,
  InputHint,
  IssueList,
  ModuleSection,
  NumField,
  RunButton,
  SwitchField,
  SyntheticNote,
  ToneBadge,
  ZONE_ORDER,
  currentUnits,
  featureName,
  pointBounds,
  resultLayer,
  useFocusMap,
  useFormErrors,
  countPolygons,
  type HeightReferenceState,
} from "../shared";
import { HeightProfileChart, HeightStretchTable, HeightsNotAssessedBanner, heightRangeParts, heightStretches } from "../height-profile";
import { ZoneRulesField, useRingsProfile, zoneRulesFromParams, zoneRulesParams, type ZoneRulesValue } from "../airspace/zone-inputs";
import { ChoiceField, ChoicesHint } from "../assessment-choices";

/** Corridor height defaults (airbrief_core EvtolCorridorParams). */
const CORRIDOR_HEIGHTS = { cruiseAmsl: 1200, minAgl: 30 } as const;

/** The rest of the corridor's defaults (EvtolCorridorParams and CorridorWeights). */
const CORRIDOR_DEFAULTS = { resolution: 8, maxCells: 60_000, studyMargin: 3, noiseDistance: 100, profileSpacing: 100 } as const;
const DEFAULT_COST_WEIGHTS: CorridorWeights = { ground_risk: 0.5, airspace: 0.3, obstacles: 0.2 };

const COST_FACTORS: { key: keyof CorridorWeights; label: string; hint: string }[] = [
  { key: "ground_risk", label: "Ground risk cost", hint: "Weight of the population density under the route (relative to the densest cell)." },
  { key: "airspace", label: "Airspace cost", hint: "Weight of yellow zones, and of red ones when they are not avoided." },
  { key: "obstacles", label: "Obstacle cost", hint: "Weight of tall structures, relative to the cruise height less the clearance." },
];

/** Siting defaults (EvtolSitingParams). */
const SITING_DEFAULTS = { obstacleRadius: 500, maxCells: 20_000 } as const;

/** The corridor's two cruise references, as the shared height-reference control explains them here. */
const CRUISE_HINTS = {
  agl: "One level, set by the highest ground (and the tallest obstacle) along the route plus the cruise height.",
  amsl: "A fixed level above mean sea level: the height above the ground changes along the route.",
} as const;

type Criterion = keyof SitingWeights;

const CRITERIA: { key: Criterion; label: string; hint: string }[] = [
  { key: "demand", label: "Demand", hint: "People living nearby (more is better)." },
  { key: "access", label: "Access", hint: "Distance to stations and transit (closer is better)." },
  { key: "airspace", label: "Airspace", hint: "Zone class (green is best, red is excluded)." },
  { key: "obstacles", label: "Obstacles", hint: "Tall structures nearby (fewer is better)." },
  { key: "slope", label: "Terrain slope", hint: "Flatter ground is better." },
];

const DEFAULT_WEIGHTS: SitingWeights = { demand: 0.35, access: 0.2, airspace: 0.2, obstacles: 0.15, slope: 0.1 };

/** Site scores (0–100) everywhere: select labels, table, KPI, chart and map tooltip. */
const fmtScore = (score: number | null | undefined) => formatNumber(score, { decimals: 1 });

/** Highlight key of a site, scoped to its siting run so a selection never carries over to another run. */
const siteKey = (analysisId: string, rank: number) => `site-${analysisId}-${rank}`;

/** A candidate as drawn on the map: it carries its run's id, so a click selects that run's site. */
type CandidatePoint = SiteCandidate & { analysisId: string };

/** `label` is shown in the pickers; `name` (default: the label) is sent as the endpoint name and drawn on the map. */
type PointChoice = { key: string; label: string; name?: string; lon: number; lat: number };

/** Endpoint choices are keyed by position, so an opened corridor finds its endpoints again. */
const pointKey = (lon: number, lat: number) => `pt:${lon.toFixed(7)},${lat.toFixed(7)}`;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function paramNumber(params: Record<string, unknown>, key: string): number | null {
  const v = params[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function paramLonLat(v: unknown): [number, number] | null {
  if (!Array.isArray(v) || v.length < 2) return null;
  const [lon, lat] = v as unknown[];
  return typeof lon === "number" && typeof lat === "number" && Number.isFinite(lon) && Number.isFinite(lat) ? [lon, lat] : null;
}

function WeightControl({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  onChange: (value: number) => void;
}) {
  const id = React.useId();
  // A weight typed outside 0–1 is clamped on leaving the field (or on Enter), and the note says so
  // until the weight changes some other way (typing, slider, reopened analysis). Fiddly: leaving
  // the field after Enter re-reads the clamped "1.00", and that must keep the note.
  const [clamped, setClamped] = React.useState<{ typed: number; used: number } | null>(null);
  const noteFor = (text: string) => {
    const parsed = parseNumberText(text); // the NumberInput's own parser, so both read the same number
    if (parsed.kind === "number" && (parsed.value < 0 || parsed.value > 1)) {
      setClamped({ typed: parsed.value, used: Math.min(1, Math.max(0, parsed.value)) });
    }
  };
  const clampNote =
    clamped && Math.abs(clamped.used - value) < 1e-9
      ? `Weights range from 0 to 1: ${formatNumber(clamped.typed)} was set to ${formatNumber(clamped.used, { decimals: 2 })}.`
      : null;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={`${id}-num`}>{label}</Label>
        <NumberInput
          id={`${id}-num`}
          className="w-20"
          value={value}
          onValueChange={(v) => onChange(v === null ? 0 : Math.min(1, Math.max(0, v)))}
          min={0}
          max={1}
          step={0.05}
          decimals={2}
          clampOnBlur
          onInput={() => setClamped(null)}
          onBlur={(e) => noteFor(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") noteFor(e.currentTarget.value);
          }}
          aria-describedby={clampNote ? `${id}-hint ${id}-clamp` : `${id}-hint`}
        />
      </div>
      <Slider
        value={[value]}
        min={0}
        max={1}
        step={0.05}
        onValueChange={(v) => {
          setClamped(null);
          onChange(v[0] ?? 0);
        }}
        thumbLabels={[`${label} weight`]}
        getValueText={(v) => `${formatNumber(v, { maximumFractionDigits: 2 })}`}
      />
      <p id={`${id}-hint`} className="text-xs text-fg-muted">
        {hint}
      </p>
      {/* Always present (empty when nothing was clamped), so the status is announced when it fills. */}
      <p id={`${id}-clamp`} role="status" className={clampNote ? "text-xs text-warn" : "sr-only"}>
        {clampNote}
      </p>
    </div>
  );
}

function EvtolPanel({ datasetId, file, features, selectedFeatures, analysis }: ModulePanelProps) {
  const runner = useAnalysisRunner();
  const sitingForm = useFormErrors();
  const corridorForm = useFormErrors();
  const [tab, setTab] = React.useState<"siting" | "corridor">(analysis?.kind === "evtol_corridor" ? "corridor" : "siting");

  const [weights, setWeights] = React.useState<SitingWeights>(DEFAULT_WEIGHTS);
  const [topN, setTopN] = React.useState<number | null>(10);
  const [separation, setSeparation] = React.useState<number | null>(2);
  const [demandRadius, setDemandRadius] = React.useState<number | null>(2);
  const [accessRadius, setAccessRadius] = React.useState<number | null>(3);
  const [maxSlope, setMaxSlope] = React.useState<number | null>(5);
  const [resolution, setResolution] = React.useState<number | null>(8);
  const [excludeWater, setExcludeWater] = React.useState(true);
  const [excludeRed, setExcludeRed] = React.useState(true);
  const [obstacleRadius, setObstacleRadius] = React.useState<number | null>(SITING_DEFAULTS.obstacleRadius);
  const [sitingCells, setSitingCells] = React.useState<number | null>(SITING_DEFAULTS.maxCells);

  const [originKey, setOriginKey] = React.useState<string>("");
  const [destinationKey, setDestinationKey] = React.useState<string>("");
  const [width, setWidth] = React.useState<number | null>(300);
  const [cruise, setCruise] = React.useState<number | null>(300);
  const [speed, setSpeed] = React.useState<number | null>(150);
  const [energy, setEnergy] = React.useState<number | null>(1000);
  const [clearance, setClearance] = React.useState<number | null>(50);
  const [noiseRef, setNoiseRef] = React.useState<number | null>(75);
  const [noiseThreshold, setNoiseThreshold] = React.useState<number | null>(55);
  const [avoidRed, setAvoidRed] = React.useState(true);
  const [cruiseRef, setCruiseRef] = React.useState<HeightReferenceState>(DEFAULT_HEIGHT_REFERENCE);
  const [cruiseAmsl, setCruiseAmsl] = React.useState<number | null>(CORRIDOR_HEIGHTS.cruiseAmsl);
  const [minAgl, setMinAgl] = React.useState<number | null>(CORRIDOR_HEIGHTS.minAgl);
  const [ceiling, setCeiling] = React.useState<number | null>(null);
  const [costWeights, setCostWeights] = React.useState<CorridorWeights>(DEFAULT_COST_WEIGHTS);
  const [corridorResolution, setCorridorResolution] = React.useState<number | null>(CORRIDOR_DEFAULTS.resolution);
  const [corridorCells, setCorridorCells] = React.useState<number | null>(CORRIDOR_DEFAULTS.maxCells);
  const [studyMargin, setStudyMargin] = React.useState<number | null>(CORRIDOR_DEFAULTS.studyMargin);
  const [noiseDistance, setNoiseDistance] = React.useState<number | null>(CORRIDOR_DEFAULTS.noiseDistance);
  const [profileSpacing, setProfileSpacing] = React.useState<number | null>(CORRIDOR_DEFAULTS.profileSpacing);
  // Endpoints of an opened corridor, offered even when they are no longer sites or features.
  const [openedEndpoints, setOpenedEndpoints] = React.useState<PointChoice[]>([]);
  // One rule set for both tabs, as the map's context rings show one.
  const [rules, setRules] = React.useState<ZoneRulesValue>(() => zoneRulesFromParams({}));
  useRingsProfile(rules.profile);
  // Like the rules, one choice for both tabs; null follows the setting.
  const unverifiedSetting = useSettings((s) => s.unverifiedBand);
  const [unverifiedRun, setUnverifiedBand] = React.useState<UnverifiedBand | null>(null);
  const unverifiedBand = unverifiedRun ?? unverifiedSetting;

  // Latest completed siting analysis of this dataset: its candidates are corridor endpoints.
  const myRuns = useMyDatasetAnalysisIds(datasetId);
  const analyses = useAnalyses(datasetId, { ids: myRuns });
  const siting = React.useMemo(() => {
    if (analysis?.kind === "evtol_siting" && analysis.status === "COMPLETED") return analysis;
    return (analyses.data?.items ?? []).find((a) => a.kind === "evtol_siting" && a.status === "COMPLETED") ?? null;
  }, [analysis, analyses.data]);
  const sitingResult = analysisResult(siting, "evtol_siting");

  const choices = React.useMemo<PointChoice[]>(() => {
    const out: PointChoice[] = [];
    const add = (choice: PointChoice) => {
      if (!out.some((c) => c.key === choice.key)) out.push(choice);
    };
    for (const c of sitingResult?.candidates ?? []) {
      add({ key: pointKey(c.lon, c.lat), label: `Site #${c.rank} (score ${fmtScore(c.score)})`, name: `Site #${c.rank}`, lon: c.lon, lat: c.lat });
    }
    for (const f of features?.features ?? []) {
      if (f.geometry?.type !== "Point") continue;
      const [lon, lat] = f.geometry.coordinates as [number, number];
      add({ key: pointKey(lon, lat), label: featureName(f.properties), lon, lat });
    }
    for (const c of openedEndpoints) add(c);
    return out;
  }, [sitingResult, features, openedEndpoints]);

  React.useEffect(() => {
    if (choices.length >= 2) {
      if (!choices.some((c) => c.key === originKey)) setOriginKey((choices[0] as PointChoice).key);
      if (!choices.some((c) => c.key === destinationKey)) setDestinationKey((choices[1] as PointChoice).key);
    }
  }, [choices, originKey, destinationKey]);

  // A run started from this form already matches it when it first opens; any other siting or
  // corridor opened here (deep link, history, or an earlier run reopened later) puts its own
  // inputs back, so running again repeats that analysis.
  const runPending = React.useRef(false);
  const startedHere = React.useRef(new Set<string>());
  const restoredId = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!analysis || restoredId.current === analysis.id) return;
    restoredId.current = analysis.id;
    if (runPending.current || startedHere.current.delete(analysis.id)) return;
    const p = analysis.params;
    const restore = (key: string, set: (value: number) => void) => {
      const v = paramNumber(p, key);
      if (v !== null) set(v);
    };
    const restoreFlag = (key: string, set: (value: boolean) => void) => {
      const v = p[key];
      if (typeof v === "boolean") set(v);
    };
    if (analysis.kind === "evtol_siting" || analysis.kind === "evtol_corridor") {
      setRules(zoneRulesFromParams(p));
      setUnverifiedBand(p.unverified_band === "exclude" ? "exclude" : "permission");
    }
    if (analysis.kind === "evtol_siting") {
      setTab("siting");
      const w = isRecord(p.weights) ? p.weights : null;
      if (w) {
        setWeights((prev) => {
          const next = { ...prev };
          for (const c of CRITERIA) {
            const v = paramNumber(w, c.key);
            if (v !== null) next[c.key] = v;
          }
          return next;
        });
      }
      restore("top_n", setTopN);
      restore("min_separation_km", setSeparation);
      restore("demand_radius_km", setDemandRadius);
      restore("access_radius_km", setAccessRadius);
      restore("max_slope_deg", setMaxSlope);
      restore("h3_resolution", setResolution);
      restoreFlag("exclude_water", setExcludeWater);
      restoreFlag("exclude_red_zones", setExcludeRed);
      restore("obstacle_radius_m", setObstacleRadius);
      restore("max_cells", setSitingCells);
    } else if (analysis.kind === "evtol_corridor") {
      setTab("corridor");
      const origin = paramLonLat(p.origin);
      const destination = paramLonLat(p.destination);
      if (origin && destination) {
        const endpoint = ([lon, lat]: [number, number], name: unknown, fallback: string): PointChoice => {
          const base = typeof name === "string" && name.trim() ? name.trim() : `${fallback} ${formatCoord(lon, lat)}`;
          return { key: pointKey(lon, lat), label: `${base} (opened corridor)`, name: base, lon, lat };
        };
        setOpenedEndpoints([endpoint(origin, p.origin_name, "Origin"), endpoint(destination, p.destination_name, "Destination")]);
        setOriginKey(pointKey(origin[0], origin[1]));
        setDestinationKey(pointKey(destination[0], destination[1]));
      }
      restore("corridor_width_m", setWidth);
      restore("cruise_altitude_agl_m", setCruise);
      restore("cruise_speed_kmh", setSpeed);
      restore("energy_wh_per_km", setEnergy);
      restore("obstacle_clearance_m", setClearance);
      restore("noise_level_db_at_ref", setNoiseRef);
      restore("noise_threshold_db", setNoiseThreshold);
      restoreFlag("avoid_red_zones", setAvoidRed);
      const w = isRecord(p.weights) ? p.weights : {};
      setCostWeights({
        ground_risk: paramNumber(w, "ground_risk") ?? DEFAULT_COST_WEIGHTS.ground_risk,
        airspace: paramNumber(w, "airspace") ?? DEFAULT_COST_WEIGHTS.airspace,
        obstacles: paramNumber(w, "obstacles") ?? DEFAULT_COST_WEIGHTS.obstacles,
      });
      restore("h3_resolution", setCorridorResolution);
      restore("max_cells", setCorridorCells);
      restore("study_margin_km", setStudyMargin);
      restore("noise_ref_distance_m", setNoiseDistance);
      restore("profile_spacing_m", setProfileSpacing);
      setCruiseRef({
        ...DEFAULT_HEIGHT_REFERENCE,
        reference: p.cruise_reference === "amsl" ? "amsl" : "agl",
        takeoffElevation: paramNumber(p, "origin_elevation_m"),
      });
      setCruiseAmsl(paramNumber(p, "cruise_altitude_amsl_m") ?? CORRIDOR_HEIGHTS.cruiseAmsl);
      setMinAgl(paramNumber(p, "min_agl_m") ?? CORRIDOR_HEIGHTS.minAgl);
      setCeiling(paramNumber(p, "ceiling_agl_m"));
    }
  }, [analysis]);

  // A run that could not start is reported inline, above the Run button of the form that started it.
  const [lastRunTab, setLastRunTab] = React.useState<typeof tab>("siting");
  const start = <K extends AnalysisKind>(kind: K, params: AnalysisRequest<K>["params"], options?: { featureIndices: number[] }) => {
    setLastRunTab(kind === "evtol_corridor" ? "corridor" : "siting");
    runPending.current = true;
    void runner
      .run(kind, params, options)
      .then((info) => {
        // Skipped once when it opens (unless it already has, while the run was pending).
        if (info && restoredId.current !== info.id) startedHere.current.add(info.id);
      })
      .finally(() => {
        runPending.current = false;
      });
  };

  const polygonCount = React.useMemo(() => countPolygons(features, selectedFeatures), [features, selectedFeatures]);

  const ready = file?.status === "COMPLETED";
  const weightSum = CRITERIA.reduce((s, c) => s + weights[c.key], 0);
  const sitingBlocked = !file
    ? "Draw or paste the study area, or open a dataset with its polygon."
    : !ready
      ? "Wait for the dataset to finish processing."
      : polygonCount === 0
        ? "Siting needs a study-area polygon."
        : weightSum <= 0
          ? "Give at least one criterion a weight above zero."
          : null;

  const origin = choices.find((c) => c.key === originKey) ?? null;
  const destination = choices.find((c) => c.key === destinationKey) ?? null;
  const corridorBlocked =
    choices.length < 2
      ? "Run siting first or open a dataset with two point features (origin and destination)."
      : !origin || !destination
        ? "Choose an origin and a destination."
        : origin.key === destination.key
          ? "Origin and destination must differ."
          : null;

  const runSiting = sitingForm.guard(() => {
    start("evtol_siting", {
      weights,
      top_n: topN ?? 10,
      min_separation_km: separation ?? 2,
      demand_radius_km: demandRadius ?? 2,
      access_radius_km: accessRadius ?? 3,
      max_slope_deg: maxSlope ?? 5,
      h3_resolution: resolution ?? 8,
      exclude_water: excludeWater,
      exclude_red_zones: excludeRed,
      obstacle_radius_m: obstacleRadius ?? SITING_DEFAULTS.obstacleRadius,
      max_cells: sitingCells ?? SITING_DEFAULTS.maxCells,
      unverified_band: unverifiedBand,
      ...zoneRulesParams(rules),
    });
  });

  const cruiseAmslMode = cruiseRef.reference === "amsl";
  const runCorridor = corridorForm.guard(() => {
    if (!origin || !destination) return;
    start(
      "evtol_corridor",
      {
        origin: [origin.lon, origin.lat],
        destination: [destination.lon, destination.lat],
        // Short names ("Site #1"): they label the route on the map; scores stay in the drawer.
        origin_name: origin.name ?? origin.label,
        destination_name: destination.name ?? destination.label,
        corridor_width_m: width ?? 300,
        cruise_altitude_agl_m: cruise ?? 300,
        cruise_speed_kmh: speed ?? 150,
        energy_wh_per_km: energy ?? 1000,
        obstacle_clearance_m: clearance ?? 50,
        noise_level_db_at_ref: noiseRef ?? 75,
        noise_threshold_db: noiseThreshold ?? 55,
        avoid_red_zones: avoidRed,
        cruise_reference: cruiseAmslMode ? "amsl" : "agl",
        ...(cruiseAmslMode
          ? { cruise_altitude_amsl_m: cruiseAmsl ?? CORRIDOR_HEIGHTS.cruiseAmsl, origin_elevation_m: cruiseRef.takeoffElevation }
          : {}),
        min_agl_m: minAgl ?? CORRIDOR_HEIGHTS.minAgl,
        ceiling_agl_m: ceiling,
        weights: costWeights,
        h3_resolution: corridorResolution ?? CORRIDOR_DEFAULTS.resolution,
        max_cells: corridorCells ?? CORRIDOR_DEFAULTS.maxCells,
        study_margin_km: studyMargin ?? CORRIDOR_DEFAULTS.studyMargin,
        noise_ref_distance_m: noiseDistance ?? CORRIDOR_DEFAULTS.noiseDistance,
        profile_spacing_m: profileSpacing ?? CORRIDOR_DEFAULTS.profileSpacing,
        unverified_band: unverifiedBand,
        ...zoneRulesParams(rules),
      },
      { featureIndices: [] },
    );
  });

  const unverifiedField = (
    <ChoiceField
      legend="Within 5 km of an airport (verify on Digital Sky)"
      choices={UNVERIFIED_BANDS}
      labels={UNVERIFIED_BAND_LABEL}
      hints={UNVERIFIED_BAND_HINT}
      value={unverifiedBand}
      setting={unverifiedSetting}
      onChange={setUnverifiedBand}
    />
  );

  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)} className="space-y-3 text-sm">
      <ChoicesHint choice="unverifiedBand" />
      <TabsList className="grid w-full grid-cols-2">
        <TabsTrigger value="siting">Vertiport siting</TabsTrigger>
        <TabsTrigger value="corridor">Corridor</TabsTrigger>
      </TabsList>

      <TabsContent value="siting">
        <form className="space-y-4" onSubmit={(e) => e.preventDefault()} aria-label="Vertiport siting">
          {!file ? (
            <PlanStartHint purpose="Siting scores every hexagon of a study area: draw it, paste it or type its corners." tools={["area"]} paste />
          ) : (
            <InputHint>Scores every hexagon in the study area and picks the best separated sites.</InputHint>
          )}
          <FormErrorSummary form={sitingForm} />
          <ModuleSection title="Criteria weights" aside={<span className="num text-xs text-fg-muted">sum {formatNumber(weightSum, { maximumFractionDigits: 2 })}</span>}>
            {CRITERIA.map((c) => (
              <WeightControl key={c.key} label={c.label} hint={c.hint} value={weights[c.key]} onChange={(v) => setWeights((w) => ({ ...w, [c.key]: v }))} />
            ))}
            <p className="text-xs text-fg-muted">Weights are normalised by their sum.</p>
          </ModuleSection>
          <ModuleSection title="Sites">
            <NumField label="Number of sites" value={topN} onChange={setTopN} min={1} max={50} decimals={0} integer form={sitingForm} />
            <NumField label="Minimum separation" unit="km" value={separation} onChange={setSeparation} min={0} max={50} step={0.5} form={sitingForm} />
          </ModuleSection>
          <ModuleSection title="Zone rules">
            <ZoneRulesField value={rules} onChange={setRules} form={sitingForm} />
            <p className="text-xs text-fg-muted">
              {unverifiedBand === "exclude"
                ? "A site within 5 km of an airport perimeter (DGCA 2021) is excluded, like one in a red zone: the published rule does not say whether it is red."
                : "A site within 5 km of an airport perimeter (DGCA 2021) scores as permission required and is marked “Verify on Digital Sky”: the published rule does not say whether it is red."}
            </p>
            {unverifiedField}
          </ModuleSection>
          <Advanced form={sitingForm}>
            <NumField label="Demand radius" unit="km" value={demandRadius} onChange={setDemandRadius} min={0.1} max={20} form={sitingForm} />
            <NumField label="Access radius" unit="km" value={accessRadius} onChange={setAccessRadius} min={0.1} max={20} form={sitingForm} />
            <NumField label="Maximum slope" unit="°" value={maxSlope} onChange={setMaxSlope} min={0.5} max={45} form={sitingForm} />
            <NumField label="Grid resolution (H3)" value={resolution} onChange={setResolution} min={6} max={9} decimals={0} integer form={sitingForm} />
            <NumField
              label="Obstacle radius"
              unit="m"
              value={obstacleRadius}
              onChange={setObstacleRadius}
              min={1}
              max={5000}
              hint="Tall structures this close to a cell lower its obstacle score."
              form={sitingForm}
            />
            <NumField
              label="Cell limit"
              value={sitingCells}
              onChange={setSitingCells}
              min={100}
              max={200_000}
              step={1000}
              decimals={0}
              integer
              hint="Most hexagons scored; a larger study area at a fine resolution is cut, and the result says so."
              form={sitingForm}
            />
            <SwitchField label="Exclude water" checked={excludeWater} onChange={setExcludeWater} />
            <SwitchField label="Exclude red zones" checked={excludeRed} onChange={setExcludeRed} />
          </Advanced>
          {runner.error && lastRunTab === "siting" ? (
            <ErrorState compact error={runner.error} title="Vertiport siting could not start" onRetry={sitingBlocked ? undefined : runSiting} retrying={runner.isPending} />
          ) : null}
          <RunButton onRun={runSiting} pending={runner.isPending} disabledReason={sitingBlocked} label="Score sites" />
        </form>
      </TabsContent>

      <TabsContent value="corridor">
        <form className="space-y-4" onSubmit={(e) => e.preventDefault()} aria-label="Corridor routing">
          <InputHint>Routes the lowest-cost corridor between two sites, avoiding red zones, tall obstacles and dense areas.</InputHint>
          <FormErrorSummary form={corridorForm} />
          <ModuleSection title="Endpoints">
            {(["origin", "destination"] as const).map((which) => (
              <div key={which} className="space-y-1.5">
                <Label htmlFor={`evtol-${which}`}>{which === "origin" ? "Origin" : "Destination"}</Label>
                <Select value={which === "origin" ? originKey : destinationKey} onValueChange={which === "origin" ? setOriginKey : setDestinationKey} disabled={choices.length === 0}>
                  <SelectTrigger id={`evtol-${which}`}>
                    <SelectValue placeholder="No points available" />
                  </SelectTrigger>
                  <SelectContent>
                    {choices.map((c) => (
                      <SelectItem key={c.key} value={c.key}>
                        {c.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ))}
          </ModuleSection>
          <ModuleSection title="Flight">
            <HeightReferenceField
              value={cruiseRef}
              onChange={setCruiseRef}
              form={corridorForm}
              references={["agl", "amsl"]}
              hints={CRUISE_HINTS}
              position={false}
              elevationLabel="Origin ground elevation"
              pickId="evtol-origin"
            />
            {cruiseAmslMode ? (
              <NumField
                label="Cruise level (AMSL)"
                unit="m"
                value={cruiseAmsl}
                onChange={setCruiseAmsl}
                min={1}
                max={12000}
                hint="The level flown, above mean sea level."
                form={corridorForm}
              />
            ) : null}
            <NumField
              label="Cruise height above ground"
              unit="m"
              value={cruise}
              onChange={setCruise}
              min={10}
              max={3000}
              hint={cruiseAmslMode ? "Used to report the level that would clear the highest ground by this much." : undefined}
              form={corridorForm}
            />
            <NumField
              label="Minimum height above ground"
              unit="m"
              value={minAgl}
              onChange={setMinAgl}
              min={0}
              max={3000}
              hint="Stretches of the route lower than this above the ground are reported."
              form={corridorForm}
            />
            <NumField
              label="Ceiling above ground"
              unit="m"
              value={ceiling}
              onChange={setCeiling}
              min={1}
              max={12000}
              optional
              hint="Empty: none. Stretches of the route higher than this above the ground are reported."
              form={corridorForm}
            />
            <NumField label="Cruise speed" unit="km/h" value={speed} onChange={setSpeed} min={10} max={1000} form={corridorForm} />
            <NumField label="Corridor width" unit="m" value={width} onChange={setWidth} min={10} max={5000} form={corridorForm} />
          </ModuleSection>
          <ModuleSection title="Zone rules">
            <ZoneRulesField value={rules} onChange={setRules} form={corridorForm} />
            <p className="text-xs text-fg-muted">
              Bands are judged at the cruise height. The 0–5 km band (DGCA 2021){" "}
              {unverifiedBand === "exclude" ? "is impassable, like a red zone," : "costs as much as a permission zone"} and is named in the
              results: it may hold red zones.
            </p>
            {unverifiedField}
          </ModuleSection>
          <Advanced form={corridorForm}>
            <NumField label="Energy use" unit="Wh/km" value={energy} onChange={setEnergy} min={1} max={100000} form={corridorForm} />
            <NumField label="Obstacle clearance" unit="m" value={clearance} onChange={setClearance} min={0} max={1000} form={corridorForm} />
            <NumField
              label="Noise level at the reference distance"
              unit="dB"
              value={noiseRef}
              onChange={setNoiseRef}
              min={0}
              max={200}
              form={corridorForm}
            />
            <NumField
              label="Noise reference distance"
              unit="m"
              value={noiseDistance}
              onChange={setNoiseDistance}
              min={1}
              max={10_000}
              hint="Where the noise level above was measured (spherical spreading from there)."
              form={corridorForm}
            />
            <NumField label="Noise threshold" unit="dB" value={noiseThreshold} onChange={setNoiseThreshold} min={0} max={200} form={corridorForm} />
            <SwitchField label="Avoid red zones" checked={avoidRed} onChange={setAvoidRed} />
            <div role="group" aria-labelledby="evtol-cost-weights" className="space-y-3 rounded border border-border p-3">
              <p id="evtol-cost-weights" className="text-sm font-medium">
                Route cost weights
              </p>
              {COST_FACTORS.map((f) => (
                <NumField
                  key={f.key}
                  label={f.label}
                  value={costWeights[f.key]}
                  onChange={(v) => setCostWeights((w) => ({ ...w, [f.key]: v ?? 0 }))}
                  min={0}
                  max={5}
                  step={0.1}
                  hint={f.hint}
                  form={corridorForm}
                />
              ))}
              <p className="text-xs text-fg-muted">0 to 5 each; 0 leaves a factor out. Distance always counts, so all at 0 gives the shortest path.</p>
            </div>
            <NumField label="Grid resolution (H3)" value={corridorResolution} onChange={setCorridorResolution} min={6} max={9} decimals={0} integer form={corridorForm} />
            <NumField
              label="Cell limit"
              value={corridorCells}
              onChange={setCorridorCells}
              min={100}
              max={300_000}
              step={1000}
              decimals={0}
              integer
              hint="Most hexagons searched; a larger study area is searched at a coarser resolution, and the result says so."
              form={corridorForm}
            />
            <NumField
              label="Study area margin"
              unit="km"
              value={studyMargin}
              onChange={setStudyMargin}
              min={0}
              max={50}
              hint="Margin around the two endpoints (or the study area) that the route may use."
              form={corridorForm}
            />
            <NumField label="Profile spacing" unit="m" value={profileSpacing} onChange={setProfileSpacing} min={10} max={2000} form={corridorForm} />
          </Advanced>
          {runner.error && lastRunTab === "corridor" ? (
            <ErrorState compact error={runner.error} title="Corridor routing could not start" onRetry={corridorBlocked ? undefined : runCorridor} retrying={runner.isPending} />
          ) : null}
          <RunButton onRun={runCorridor} pending={runner.isPending} disabledReason={corridorBlocked} label="Route corridor" />
        </form>
      </TabsContent>
    </Tabs>
  );
}

/** How a site's score adds up: sub-score (0–1) × the criterion's share of the weights × 100. */
function SiteBreakdown({ site, weights }: { site: SiteCandidate; weights: SitingWeights }) {
  const total = CRITERIA.reduce((s, c) => s + (weights[c.key] ?? 0), 0);
  return (
    <div className="max-w-lg space-y-1.5">
      <table className="w-full text-sm">
        <caption className="sr-only">
          How the score of site {site.rank} adds up: each criterion&apos;s sub-score from 0 to 1, times its share of the weights, gives its points.
        </caption>
        <thead>
          <tr className="text-xs text-fg-muted">
            <th scope="col" className="pb-1 text-left font-medium">
              Criterion
            </th>
            <th scope="col" className="pb-1 text-right font-medium">
              Sub-score
            </th>
            <th scope="col" className="pb-1 text-right font-medium">
              Weight
            </th>
            <th scope="col" className="pb-1 text-right font-medium">
              Points
            </th>
          </tr>
        </thead>
        <tbody>
          {CRITERIA.map((c) => {
            const sub = site.breakdown[c.key] ?? 0;
            const share = total > 0 ? (weights[c.key] ?? 0) / total : 0;
            const points = sub * share * 100;
            return (
              <tr key={c.key}>
                <th scope="row" className="py-0.5 pr-2 text-left font-normal text-fg-muted">
                  {c.label}
                </th>
                <td className="num py-0.5 text-right">{formatNumber(sub, { decimals: 2 })}</td>
                <td className="num py-0.5 text-right">× {formatPercent(share, { fraction: true, maximumFractionDigits: 0 })}</td>
                <td className="py-0.5 pl-3">
                  <div className="flex items-center justify-end gap-2">
                    {/* Track = the criterion's share of 100 points; fill = the points it earned. */}
                    <span aria-hidden className="h-2 w-24 shrink-0">
                      <span className="block h-2 rounded bg-elevated" style={{ width: `${share * 100}%` }}>
                        <span className="block h-2 rounded bg-accent" style={{ width: `${Math.min(1, Math.max(0, sub)) * 100}%` }} />
                      </span>
                    </span>
                    <span className="num w-10 text-right">{formatNumber(points, { decimals: 1 })}</span>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t border-border">
            <th scope="row" colSpan={3} className="pt-1 text-left font-medium">
              Score
            </th>
            <td className="num pt-1 text-right font-semibold">{fmtScore(site.score)} / 100</td>
          </tr>
        </tfoot>
      </table>
      <p className="text-xs text-fg-muted">Points = sub-score × weight share × 100; together they make the score.</p>
    </div>
  );
}

function SitingResults({ analysis, result }: { analysis: AnalysisInfo; result: EvtolSitingResult }) {
  const units = useUnits();
  const focus = useFocusMap();
  const highlight = useUi((s) => s.resultHighlight);
  const setHighlight = useUi((s) => s.setResultHighlight);

  // The clicked site (table row or map point) drives the selected row, the breakdown and the
  // chart. Hovering a row only previews it on the map and is undone when the pointer leaves.
  const keyOf = React.useCallback((c: SiteCandidate) => siteKey(analysis.id, c.rank), [analysis.id]);
  const isSite = React.useCallback((key: string | null) => key !== null && result.candidates.some((c) => keyOf(c) === key), [result.candidates, keyOf]);
  const hovering = React.useRef(false);
  const [selectedKey, setSelectedKey] = React.useState<string | null>(() => (isSite(highlight) ? highlight : null));
  React.useEffect(() => {
    // A highlight set elsewhere (a click on a candidate on the map) selects that site.
    if (!hovering.current && isSite(highlight)) setSelectedKey(highlight);
  }, [highlight, isSite]);
  const selected = result.candidates.find((c) => keyOf(c) === selectedKey) ?? result.candidates[0] ?? null;

  const columns = React.useMemo<DataTableColumn<SiteCandidate>[]>(
    () => [
      { id: "rank", header: "Rank", numeric: true, width: 56, cell: (c) => c.rank, sortValue: (c) => c.rank },
      { id: "score", header: "Score", numeric: true, rowHeader: true, cell: (c) => fmtScore(c.score), sortValue: (c) => c.score },
      {
        id: "zone",
        header: "Zone",
        cell: (c) =>
          c.zone_basis === "verify_official_map" ? (
            <ToneBadge tone="warn" label="Verify on Digital Sky" />
          ) : (
            <Badge variant={`zone-${c.zone}`}>{ZONE_LABEL[c.zone]}</Badge>
          ),
        sortValue: (c) => ZONE_ORDER[c.zone],
      },
      { id: "people", header: "People nearby", numeric: true, cell: (c) => formatNumber(c.population_nearby, { maximumFractionDigits: 0 }), sortValue: (c) => c.population_nearby },
      {
        id: "poi",
        header: "Nearest station",
        cell: (c) => (c.nearest_poi ? `${c.nearest_poi} (${formatLength((c.nearest_poi_km ?? 0) * 1000, units)})` : "—"),
        sortValue: (c) => c.nearest_poi_km,
      },
      { id: "slope", header: "Slope", numeric: true, cell: (c) => `${formatNumber(c.slope_deg, { maximumFractionDigits: 1 })}°`, sortValue: (c) => c.slope_deg },
    ],
    [units],
  );

  const chartData = result.candidates.slice(0, 15).map((c) => ({ key: keyOf(c), name: `#${c.rank}`, score: c.score }));
  const selectedId = selected ? keyOf(selected) : null;

  return (
    <div className="space-y-4">
      <SyntheticNote synthetic={analysis.datapack_synthetic} />
      <KpiGrid label="Siting summary">
        <KpiTile label="Candidate sites" value={formatNumber(result.candidates.length)} />
        <KpiTile label="Best score" value={fmtScore(result.candidates[0]?.score ?? null)} unit="/ 100" />
        <KpiTile label="Cells evaluated" value={formatNumber(result.cells_evaluated)} explanation={`${formatNumber(result.cells_excluded)} excluded`} />
      </KpiGrid>

      {chartData.length > 0 ? (
        <figure aria-label="Site scores" className="space-y-1">
          <figcaption className="text-xs font-medium uppercase tracking-wide text-fg-muted">
            Scores (sorted){selected ? ` · site #${selected.rank} selected` : ""}
          </figcaption>
          <div style={{ height: Math.max(120, chartData.length * 24 + 40) }} className="max-w-3xl">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 8 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" domain={[0, 100]} stroke="var(--text-muted)" fontSize={11} />
                <YAxis type="category" dataKey="name" stroke="var(--text-muted)" fontSize={11} width={36} interval={0} />
                <ChartTooltip
                  cursor={{ fill: "var(--elevated)" }}
                  contentStyle={{ background: "var(--elevated)", border: "1px solid var(--border)", fontSize: 12 }}
                  formatter={(v: number) => [fmtScore(v), "Score"]}
                />
                <Bar dataKey="score" name="Score" isAnimationActive={false} label={{ position: "right", fontSize: 10, fill: "var(--text-muted)", formatter: (v: number) => fmtScore(v) }}>
                  {chartData.map((d) => (
                    <Cell
                      key={d.key}
                      fill={d.key === selectedId ? "var(--accent)" : "var(--text-muted)"}
                      fillOpacity={d.key === selectedId ? 1 : 0.45}
                      stroke={d.key === selectedId ? "var(--text)" : "none"}
                      strokeWidth={d.key === selectedId ? 1.5 : 0}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </figure>
      ) : null}

      <ModuleSection title="Ranking">
        <DataTable
          data={result.candidates}
          columns={columns}
          getRowId={keyOf}
          caption="Candidate vertiport sites"
          onRowClick={(c) => {
            setSelectedKey(keyOf(c));
            setHighlight(keyOf(c));
            focus(pointBounds(c.lon, c.lat, 800));
          }}
          onRowHover={(c) => {
            hovering.current = c !== null;
            setHighlight(c ? keyOf(c) : selectedKey);
          }}
          selectedRowIds={selectedId ? [selectedId] : []}
          getRowLabel={(c) => `Show site ${c.rank} on the map`}
          initialSort={{ columnId: "rank", direction: "asc" }}
          dense
          emptyState={<EmptyState compact title="No sites" description="Every cell was excluded. Relax the slope limit or exclusions." />}
        />
      </ModuleSection>

      {selected ? (
        <ModuleSection title={`Site #${selected.rank} breakdown`}>
          <SiteBreakdown site={selected} weights={result.weights} />
          <p className="text-xs text-fg-muted">
            {formatCoord(selected.lon, selected.lat)} · elevation {formatHeight(selected.elevation_m, units)}
            {selected.notes.length > 0 ? ` · ${selected.notes.join(" ")}` : ""}
          </p>
        </ModuleSection>
      ) : null}

      <ModuleSection title="Criteria">
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-fg-muted">
          {Object.entries(result.criteria).map(([k, v]) => (
            <li key={k}>
              <span className="text-fg">{k}</span>: {v}
            </li>
          ))}
        </ul>
        <p className="text-xs text-fg-muted">
          Zone rules: {ZONE_PROFILE_LABEL[result.zone_profile ?? "conservative_illustrative"]}.
          {result.unverified_band === "exclude" ? " Within 5 km of an airport: kept out, like a red zone." : ""}
        </p>
      </ModuleSection>

      <IssueList issues={result.issues} />
      <Disclaimer text={result.disclaimer} />
    </div>
  );
}

function CorridorResults({ analysis, result }: { analysis: AnalysisInfo; result: EvtolCorridorResult }) {
  const units = useUnits();
  const focus = useFocusMap();
  const zoneColumns = React.useMemo<DataTableColumn<ZoneHit>[]>(
    () => [
      {
        id: "name",
        header: "Zone",
        rowHeader: true,
        // The ring tells apart two rows of the same aerodrome (e.g. its 0–5 km and 5–8 km bands).
        cell: (z) => (
          <>
            {z.name}
            {z.ring ? <span className="block text-fg-muted">{z.ring}</span> : null}
          </>
        ),
        sortValue: (z) => z.name,
      },
      {
        id: "zone",
        header: "Class",
        cell: (z) =>
          z.basis === "verify_official_map" ? (
            <ToneBadge tone="warn" label="Verify on Digital Sky" />
          ) : (
            <Badge variant={`zone-${z.zone}`}>{ZONE_LABEL[z.zone]}</Badge>
          ),
        sortValue: (z) => ZONE_ORDER[z.zone],
      },
      {
        id: "ceiling",
        header: "Ceiling",
        numeric: true,
        cell: (z) =>
          z.permission_required
            ? z.agl_margin_m != null && z.agl_margin_m < 0
              ? `Permission (${formatHeight(-z.agl_margin_m, units)} over)`
              : "Permission"
            : formatHeight(z.ceiling_agl_m, units),
        sortValue: (z) => (z.permission_required ? -1 : z.ceiling_agl_m),
      },
      { id: "overlap", header: "Overlap", numeric: true, cell: (z) => areaParts(z.overlap_area_m2 ?? null, units).text, sortValue: (z) => z.overlap_area_m2 ?? null },
    ],
    [units],
  );
  const obstacleColumns = React.useMemo<DataTableColumn<ObstacleHit>[]>(
    () => [
      { id: "name", header: "Obstacle", rowHeader: true, cell: (o) => o.name ?? o.kind, sortValue: (o) => o.name ?? o.kind },
      { id: "status", header: "Status", cell: (o) => <ToneBadge tone={o.conflict ? "danger" : "ok"} label={o.conflict ? "Conflict" : "Clear"} />, sortValue: (o) => (o.conflict ? 0 : 1) },
      {
        id: "height",
        header: "Height",
        numeric: true,
        cell: (o) =>
          o.height_assumed ? (
            <>
              {formatHeight(o.height_m, units)}
              <span className="block text-fg-muted">{OBSTACLE_HEIGHT_SOURCE_LABEL.default}</span>
            </>
          ) : (
            formatHeight(o.height_m, units)
          ),
        sortValue: (o) => o.height_m,
      },
      { id: "distance", header: "From route", numeric: true, cell: (o) => formatLength(o.distance_m, units), sortValue: (o) => o.distance_m },
    ],
    [units],
  );

  const amslLevel = result.cruise_reference === "amsl" ? (result.cruise_altitude_amsl_m ?? null) : null;
  const flown = amslLevel ?? result.required_cruise_amsl_m;
  const stretches = heightStretches(result.below_min_agl, result.above_ceiling);
  const stretchFeatures = resultLayer(result.layers, "heights");
  const belowM = (result.below_min_agl ?? []).reduce((sum: number, s: HeightSegment) => sum + s.length_m, 0);
  const aboveM = (result.above_ceiling ?? []).reduce((sum: number, s: HeightSegment) => sum + s.length_m, 0);
  return (
    <div className="space-y-4">
      <HeightsNotAssessedBanner assessed={result.heights_assessed} message={result.issues.find((i) => i.code === "heights_not_assessed")?.message} />
      <SyntheticNote synthetic={analysis.datapack_synthetic} />
      <KpiGrid label="Corridor summary">
        <KpiTile label="Route length" value={lengthParts(result.length_km * 1000, units)} explanation={`Straight line ${formatLength(result.straight_km * 1000, units)}`} />
        <KpiTile
          label="Detour"
          value={formatNumber(result.detour_pct, { maximumFractionDigits: 1 })}
          unit="%"
          tone={result.detour_pct > 30 ? "warn" : "neutral"}
          toneLabel={result.detour_pct > 30 ? "Long detour" : undefined}
        />
        <KpiTile label="Flight time" value={formatNumber(result.flight_time_min, { maximumFractionDigits: 1 })} unit="min" />
        <KpiTile label="Energy" value={formatNumber(result.energy_kwh, { maximumFractionDigits: 1 })} unit="kWh" />
        {amslLevel !== null ? (
          <KpiTile
            label="Cruise level (AMSL)"
            value={heightParts(amslLevel, units)}
            explanation={`Fixed level; ${formatHeight(result.required_cruise_amsl_m, units)} would clear the highest ground by ${formatHeight(result.cruise_altitude_agl_m, units)}`}
          />
        ) : (
          <KpiTile label="Required cruise (AMSL)" value={heightParts(result.required_cruise_amsl_m, units)} explanation={`${formatHeight(result.cruise_altitude_agl_m, units)} above the highest ground`} />
        )}
        {result.agl_min_m != null ? (
          <KpiTile
            label="Height above ground"
            value={heightRangeParts(result.agl_min_m, result.agl_max_m, units)}
            tone={result.agl_min_m <= 0 ? "danger" : belowM > 0 ? "warn" : aboveM > 0 ? "danger" : "ok"}
            toneLabel={
              result.agl_min_m <= 0
                ? "Ground above the cruise level"
                : belowM > 0
                  ? `Below ${formatHeight(result.min_agl_m ?? null, units)} for ${formatLength(belowM, units)}`
                  : aboveM > 0
                    ? `Above the ceiling for ${formatLength(aboveM, units)}`
                    : "Within limits"
            }
            explanation="Lowest to highest along the route"
          />
        ) : null}
        <KpiTile
          label="People exposed to noise"
          value={formatNumber(result.noise.exposed_population, { maximumFractionDigits: 0 })}
          explanation={`≥ ${formatNumber(result.noise.threshold_db)} dB within ${formatLength(result.noise.footprint_half_width_m, units)} of the route`}
        />
      </KpiGrid>

      {result.profile.length > 1 ? (
        <HeightProfileChart
          title="Height profile along the route"
          points={result.profile}
          units={units}
          flightLabel={flown != null ? `Cruise level (${formatHeight(flown, units)} AMSL)` : "Cruise level"}
          minAgl={result.min_agl_m ?? null}
          ceilingAgl={result.ceiling_agl_m ?? null}
          below={result.below_min_agl ?? []}
          above={result.above_ceiling ?? []}
        />
      ) : null}
      {stretches.length > 0 ? (
        <HeightStretchTable
          stretches={stretches}
          units={units}
          caption="Stretches of the route below the minimum height above ground or above the ceiling"
          onSelect={
            stretchFeatures.features.length > 0
              ? (s) => {
                  const feature = stretchFeatures.features.find((f) => f.id === s.id);
                  if (feature) focus(feature);
                }
              : undefined
          }
        />
      ) : null}
      <DemCaveat dem={result.dem} />

      <ModuleSection title={`Zones crossed (${result.zones.length})`}>
        <DataTable data={result.zones} columns={zoneColumns} getRowId={(z, i) => `${z.name}-${i}`} caption="Zones crossed by the corridor" dense emptyState={<EmptyState compact title="No zones crossed" />} />
        <p className="text-xs text-fg-muted">
          Judged at the highest height above ground along the route. Zone rules: {ZONE_PROFILE_LABEL[result.zone_profile ?? "conservative_illustrative"]}.
          {result.unverified_band === "exclude" ? " Within 5 km of an airport: kept out, like a red zone." : ""}
        </p>
      </ModuleSection>
      <ModuleSection title={`Obstacles (${result.obstacles.length})`}>
        <DataTable
          data={result.obstacles}
          columns={obstacleColumns}
          getRowId={(o) => o.id}
          caption="Obstacles inside the corridor"
          onRowClick={(o) => focus(pointBounds(o.lon, o.lat, 400))}
          getRowLabel={(o) => `Zoom to ${o.name ?? o.kind}`}
          dense
          emptyState={<EmptyState compact title="No obstacles in the corridor" />}
        />
      </ModuleSection>
      <p className="text-xs text-fg-muted">
        Noise: {formatNumber(result.noise.cells.length)} cells at or above {formatNumber(result.noise.threshold_db)} dB covering{" "}
        {areaParts(result.noise.exposed_area_km2 * 1_000_000, units).text}. Density along the corridor: max{" "}
        {formatNumber(result.max_density_per_km2, { maximumFractionDigits: 0 })}, mean {formatNumber(result.mean_density_per_km2, { maximumFractionDigits: 0 })} people/km².
      </p>
      <IssueList issues={result.issues} />
      <Disclaimer text={result.disclaimer} />
    </div>
  );
}

function EvtolResults({ analysis }: ModuleResultsProps) {
  if (!analysis) return null;
  const siting = analysisResult(analysis, "evtol_siting");
  if (siting) return <SitingResults key={analysis.id} analysis={analysis} result={siting} />;
  const corridor = analysisResult(analysis, "evtol_corridor");
  if (corridor) return <CorridorResults analysis={analysis} result={corridor} />;
  return analysis.status === "COMPLETED" ? <EmptyState compact icon={<Building2 />} title="No result" /> : null;
}

const LAYERS = [
  { id: "evtol:cells", label: "Site scores", kinds: ["evtol_siting"] },
  { id: "evtol:candidates", label: "Candidate sites", kinds: ["evtol_siting"], resultLayer: "candidates" },
  { id: "evtol:noise", label: "Noise footprint", kinds: ["evtol_corridor"] },
  { id: "evtol:corridor", label: "Corridor", kinds: ["evtol_corridor"], resultLayer: "corridor" },
  { id: "evtol:route", label: "Route", kinds: ["evtol_corridor"], resultLayer: "route" },
  { id: "evtol:obstacles", label: "Obstacles", kinds: ["evtol_corridor"], resultLayer: "obstacles" },
  { id: "evtol:heights", label: "Height stretches", kinds: ["evtol_corridor"], resultLayer: "heights" },
] as const;

type AnyFc = FeatureCollection<Geometry | null, Record<string, unknown>>;

function buildSitingLayers(analysisId: string, result: EvtolSitingResult, ctx: ModuleLayerContext): ModuleLayerOutput {
  const palette = mapPalette();
  const scored = derived(result.cells, "evtol:scored", () => result.cells.filter((c) => !c.excluded && c.score !== null));
  const excluded = derived(result.cells, "evtol:excluded", () => result.cells.filter((c) => c.excluded));
  const candidates = derived(result.candidates, "evtol:candidate-points", (): CandidatePoint[] => result.candidates.map((c) => ({ ...c, analysisId })));
  const legends: LegendSpec[] = [
    { id: "evtol:cells", title: "Site score", kind: "ramp", stops: VIRIDIS_STOPS, breaks: [0, 25, 50, 75, 100], unit: "score", layerId: "evtol:cells", note: "Grey hexagons are excluded (water, red zone or steep)." },
    { id: "evtol:candidates", title: "Candidates", kind: "categorical", layerId: "evtol:candidates", items: [{ label: "Candidate site (rank)", color: "var(--accent)", shape: "point" }] },
  ];
  return {
    layers: [
      h3Layer<SitingCell>({ id: "evtol:cells", data: scored, getHexagon: (c) => c.h3, getFillColor: (c) => viridis((c.score ?? 0) / 100, 170), opacity: 0.7 }),
      h3Layer<SitingCell>({
        id: "evtol:cells/excluded",
        data: excluded,
        getHexagon: (c) => c.h3,
        getFillColor: () => [palette.subtle[0], palette.subtle[1], palette.subtle[2], 90],
        getLineColor: () => [palette.border[0], palette.border[1], palette.border[2], 200],
        opacity: 0.6,
        updateTriggers: [ctx.theme],
      }),
      pointLayer<CandidatePoint>({
        id: "evtol:candidates",
        data: candidates,
        getPosition: (c) => [c.lon, c.lat],
        radius: (c) => (ctx.resultHighlight === siteKey(c.analysisId, c.rank) ? 11 : 8),
        fillColor: palette.accent,
        lineColor: palette.text,
        lineWidth: 2,
        updateTriggers: [ctx.resultHighlight, ctx.theme],
      }),
      textLabelLayer<SiteCandidate>({
        id: "evtol:candidates/labels",
        data: result.candidates,
        getPosition: (c) => [c.lon, c.lat],
        getText: (c) => `#${c.rank}`,
        pixelOffset: [0, -18],
        size: 11,
        updateTriggers: [ctx.theme],
      }),
      geoJsonLayer({
        id: "evtol:candidates/input",
        data: resultLayer(result.layers, "input") as AnyFc,
        filled: false,
        lineColor: palette.accent,
        lineWidth: 1.5,
        dashed: true,
        pickable: false,
        updateTriggers: [ctx.theme],
      }),
    ],
    legends,
  };
}

function buildCorridorLayers(result: EvtolCorridorResult, ctx: ModuleLayerContext): ModuleLayerOutput {
  const palette = mapPalette();
  const levels = result.noise.cells.map((c) => c.level_db);
  const lo = result.noise.threshold_db;
  const hi = Math.max(lo + 1, ...levels);
  const legends: LegendSpec[] = [
    { id: "evtol:noise", title: "Noise level", kind: "ramp", stops: VIRIDIS_STOPS, breaks: [lo, lo + (hi - lo) / 2, hi].map((v) => Math.round(v)), unit: "dB", layerId: "evtol:noise" },
    {
      id: "evtol:route",
      title: "Corridor",
      kind: "categorical",
      layerId: "evtol:route",
      items: [
        { label: "Route", color: "var(--accent)", shape: "line" },
        { label: "Corridor edge", color: "var(--text)", shape: "dashed-line" },
        { label: "Obstacle in corridor", color: "var(--danger)", shape: "point" },
      ],
    },
  ];
  return {
    layers: [
      h3Layer<NoiseCell>({ id: "evtol:noise", data: result.noise.cells, getHexagon: (c) => c.h3, getFillColor: (c) => viridis((c.level_db - lo) / (hi - lo), 150), opacity: 0.6 }),
      geoJsonLayer({ id: "evtol:corridor", data: resultLayer(result.layers, "corridor") as AnyFc, fillColor: [palette.accent[0], palette.accent[1], palette.accent[2], 25], lineColor: palette.text, lineWidth: 1.5, dashed: true, pickable: false, updateTriggers: [ctx.theme] }),
      geoJsonLayer({ id: "evtol:route", data: resultLayer(result.layers, "route") as AnyFc, filled: false, lineColor: palette.accent, lineWidth: 4, pickable: false, updateTriggers: [ctx.theme] }),
      geoJsonLayer({ id: "evtol:route/endpoints", data: resultLayer(result.layers, "endpoints") as AnyFc, fillColor: palette.accent, lineColor: palette.text, pointRadius: 8, lineWidth: 2, pickable: true, updateTriggers: [ctx.theme] }),
      // Origin label above its point, destination label below, so the two never overprint on a
      // short route seen from far away.
      ...(["origin", "destination"] as const).map((role) =>
        textLabelLayer<{ name: string; position: [number, number] }>({
          id: role === "origin" ? "evtol:route/labels" : "evtol:route/labels-destination",
          data: derived(resultLayer(result.layers, "endpoints"), `evtol:endpoint-labels:${role}`, () =>
            resultLayer(result.layers, "endpoints").features.flatMap((f, i) => {
              const props = (f.properties as Record<string, unknown> | null) ?? {};
              const isOrigin = props.role === "origin" || (props.role === undefined && i === 0);
              if (f.geometry?.type !== "Point" || isOrigin !== (role === "origin")) return [];
              return [{ name: String(props.name ?? ""), position: f.geometry.coordinates as [number, number] }];
            }),
          ),
          getPosition: (d) => d.position,
          getText: (d) => d.name,
          pixelOffset: role === "origin" ? [0, -20] : [0, 20],
          updateTriggers: [ctx.theme],
        }),
      ),
      pointLayer<ObstacleHit>({ id: "evtol:obstacles", data: result.obstacles, getPosition: (o) => [o.lon, o.lat], radius: 6, fillColor: (o) => (o.conflict ? palette.danger : palette.muted), lineColor: palette.text, updateTriggers: [ctx.theme] }),
      ...corridorStretchLayers(result, ctx, legends),
    ],
    legends,
  };
}

/** Stretches of a fixed-level corridor below the minimum (amber, dashed) or above the ceiling (red). */
function corridorStretchLayers(result: EvtolCorridorResult, ctx: ModuleLayerContext, legends: LegendSpec[]) {
  const stretches = resultLayer(result.layers, "heights") as unknown as FeatureCollection<Geometry | null, HeightSegment & { kind: string }>;
  if (stretches.features.length === 0) return [];
  const palette = mapPalette();
  const below = derived(stretches, "evtol:stretches-below", () => stretches.features.filter((f) => f.properties.kind === "below_min_agl"));
  const above = derived(stretches, "evtol:stretches-above", () => stretches.features.filter((f) => f.properties.kind === "above_ceiling"));
  legends.push({
    id: "evtol:heights",
    title: "Heights above ground",
    kind: "categorical",
    layerId: "evtol:heights",
    items: [
      ...(above.length > 0 ? [{ label: "Above the ceiling", color: "var(--danger)", shape: "line" as const }] : []),
      ...(below.length > 0 ? [{ label: "Below the minimum height", color: "var(--warn)", shape: "dashed-line" as const }] : []),
    ],
  });
  return [
    geoJsonLayer({ id: "evtol:heights", data: above, filled: false, lineColor: palette.danger, lineWidth: 6, updateTriggers: [ctx.theme] }),
    geoJsonLayer({ id: "evtol:heights/below", data: below, filled: false, lineColor: palette.warn, lineWidth: 6, dashed: true, dashArray: [2, 1.5], updateTriggers: [ctx.theme] }),
  ];
}

function buildEvtolLayers(analysis: AnalysisInfo, ctx: ModuleLayerContext): ModuleLayerOutput {
  if (analysis.kind === "evtol_siting" && analysis.result) return buildSitingLayers(analysis.id, analysis.result as EvtolSitingResult, ctx);
  if (analysis.kind === "evtol_corridor" && analysis.result) return buildCorridorLayers(analysis.result as EvtolCorridorResult, ctx);
  return { layers: [], legends: [] };
}

function describeEvtolPick(info: PickingInfo): string | null {
  const id = info.layer?.id ?? "";
  const obj = info.object as Record<string, unknown> | null;
  if (!obj) return null;
  if (id === "evtol:candidates") {
    const c = obj as unknown as SiteCandidate;
    return `Site #${c.rank}: score ${fmtScore(c.score)}`;
  }
  if (id === "evtol:cells") {
    const c = obj as unknown as SitingCell;
    return `Score ${fmtScore(c.score)}`;
  }
  if (id === "evtol:cells/excluded") return `Excluded: ${String((obj as unknown as SitingCell).reason ?? "constraint")}`;
  if (id === "evtol:noise") return `${formatNumber((obj as unknown as NoiseCell).level_db, { maximumFractionDigits: 0 })} dB`;
  if (id === "evtol:obstacles") {
    const o = obj as unknown as ObstacleHit;
    return `${o.name ?? o.kind}: ${formatHeight(o.height_m, currentUnits())}${o.height_assumed ? " (assumed height)" : ""}`;
  }
  if (id.startsWith("evtol:heights")) {
    const p = (obj.properties ?? {}) as Partial<HeightSegment & { kind: string }>;
    if (p.length_m === undefined || p.peak_agl_m === undefined) return null;
    const units = currentUnits();
    return p.kind === "above_ceiling"
      ? `Above the ceiling for ${formatLength(p.length_m, units)}: up to ${formatHeight(p.peak_agl_m, units)} above ground`
      : `Below the minimum for ${formatLength(p.length_m, units)}: down to ${formatHeight(p.peak_agl_m, units)} above ground`;
  }
  return null;
}

/** A click on a candidate selects it (the results table, breakdown and chart follow). */
function onEvtolPick(info: PickingInfo): boolean {
  if (info.layer?.id !== "evtol:candidates" || !info.object) return false;
  const c = info.object as CandidatePoint;
  if (typeof c.analysisId !== "string") return false;
  useUi.getState().setResultHighlight(siteKey(c.analysisId, c.rank));
  return true;
}

export const definition: ModuleDefinition = {
  ...MODULE_META.evtol,
  Panel: EvtolPanel,
  Results: EvtolResults,
  layers: LAYERS,
  buildLayers: buildEvtolLayers,
  describePick: describeEvtolPick,
  onPick: onEvtolPick,
};

export default definition;
