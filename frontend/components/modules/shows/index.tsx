"use client";

/**
 * Shows module: validate a drone-show choreography (separation, dynamics, geofence, audience
 * buffer, ceiling, pad spacing, endurance) and review it with a timeline. Playback never starts
 * by itself; the transport bar is keyboard-operable (Space, ←/→, Shift+←/→) and conflicts seek
 * the timeline. Without a "Show box" polygon the box is derived from the tracks; "Check airspace
 * for this show box" runs the airspace module on it at the show ceiling.
 */

import * as React from "react";
import type { PickingInfo } from "@deck.gl/core";
import { Crosshair, Pause, Play, Plane, SkipBack, SkipForward, Sparkles } from "lucide-react";
import { DEMO_MODE } from "@/lib/api";
import { formatArea, formatClock, formatCoord, formatDuration, formatHeight, formatLength, formatNumber, formatSpeed, heightParts, lengthParts, localizeUnits } from "@/lib/format";
import {
  MODULE_META,
  type LegendItem,
  type LegendSpec,
  type ModuleDefinition,
  type ModuleLayerContext,
  type ModuleLayerOutput,
  type ModulePanelProps,
  type ModuleResultsProps,
} from "@/lib/modules";
import { usePrefersReducedMotion } from "@/lib/motion";
import { usePlan } from "@/lib/plan-store";
import { PLAYBACK_SPEEDS, useUi, useUnits, type Units } from "@/lib/store";
import {
  analysisResult,
  type AnalysisInfo,
  type AnalysisRequest,
  type DroneStats,
  type FeatureCollection,
  type Geometry,
  type ShowAudienceRule,
  type ShowConflict,
  type ShowFrames,
  type ShowPadConflict,
  type ShowResult,
  type ShowViolation,
} from "@/lib/types";
import { useWorkspaceParams } from "@/lib/url-state";
import { droneCloudLayer, dronePoints, geoJsonLayer, hatchZoneLayer, mapPalette, type DroneLabel } from "@/components/map/layers/common";
import { Button } from "@/components/ui/button";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { KpiGrid, KpiTile } from "@/components/ui/kpi-tile";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAnalysisRunner } from "@/components/workspace/analysis-runner";
import { announce } from "@/components/workspace/live-region";
import {
  Advanced,
  FormErrorSummary,
  InputHint,
  IssueList,
  ModuleSection,
  NumField,
  RunButton,
  SwitchField,
  ToneBadge,
  VerdictBanner,
  currentUnits,
  pointBounds,
  resultLayer,
  useFocusMap,
  useFormErrors,
} from "../shared";
import { ResetFormButton, paramChoice, paramNumber, useFormFromAnalysis } from "../analysis-form";

const VIOLATION_LABEL: Record<ShowViolation["kind"], string> = {
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

const AUDIENCE_RULE_LABEL: Record<ShowAudienceRule, string> = {
  fixed: "Fixed",
  one_to_one: "1:1 height",
  ballistic: "Ballistic",
};

const AUDIENCE_RULES: readonly ShowAudienceRule[] = ["fixed", "one_to_one", "ballistic"];

type AltitudeChoice = "auto" | "relative" | "absolute";

const ALTITUDE_LABEL: Record<AltitudeChoice, string> = {
  auto: "From the file",
  relative: "Relative to the origin",
  absolute: "Above sea level",
};

/** "Peak / limit" of a violation in its own unit. */
function violationValue(v: ShowViolation, units: Units): string {
  if (v.kind === "endurance") return `${formatDuration(v.peak_value)} / ${formatDuration(v.limit)}`;
  if (v.kind === "geofence" || v.kind === "audience") return `${formatLength(v.peak_value, units, { maximumFractionDigits: 2 })} / ${formatLength(v.limit, units, { maximumFractionDigits: 2 })}`;
  if (v.kind === "ceiling") return `${formatHeight(v.peak_value, units, { maximumFractionDigits: 2 })} / ${formatHeight(v.limit, units, { maximumFractionDigits: 2 })}`;
  if (v.kind === "acceleration") return `${formatNumber(v.peak_value, { maximumFractionDigits: 2 })} / ${formatNumber(v.limit, { maximumFractionDigits: 2 })} m/s²`;
  return `${formatSpeed(v.peak_value, units)} / ${formatSpeed(v.limit, units)}`;
}

const conflictKey = (c: ShowConflict) => `conflict-${c.drone_a}-${c.drone_b}-${c.t_min_s}`;

/** Highest height the airspace module checks (`altitude_agl_m` in AirspaceParams). */
const AIRSPACE_MAX_AGL_M = 1500;

/** Positions of every drone at time `t`, linearly interpolated between playback frames. */
export function positionsAt(frames: ShowFrames, t: number): number[][] {
  const times = frames.times;
  if (times.length === 0) return [];
  if (t <= (times[0] as number)) return frames.positions[0] as number[][];
  const last = times.length - 1;
  if (t >= (times[last] as number)) return frames.positions[last] as number[][];
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if ((times[mid] as number) <= t) lo = mid;
    else hi = mid;
  }
  const t0 = times[lo] as number;
  const t1 = times[hi] as number;
  const k = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
  const a = frames.positions[lo] as number[][];
  const b = frames.positions[hi] as number[][];
  return a.map((pa, i) => {
    const pb = b[i] ?? pa;
    return [
      (pa[0] ?? 0) + ((pb[0] ?? 0) - (pa[0] ?? 0)) * k,
      (pa[1] ?? 0) + ((pb[1] ?? 0) - (pa[1] ?? 0)) * k,
      (pa[2] ?? 0) + ((pb[2] ?? 0) - (pa[2] ?? 0)) * k,
    ];
  });
}

function showBounds(frames: ShowFrames) {
  const start = frames.times[0] ?? 0;
  const end = frames.times[frames.times.length - 1] ?? start;
  const step = frames.times.length > 1 ? (end - start) / (frames.times.length - 1) : 1;
  return { start, end, step };
}

/** [lon, lat] of a point `e` metres east and `n` metres north of the show origin. */
function enuToLonLat(origin: readonly number[], e: number, n: number): [number, number] {
  const lon0 = Number(origin[0] ?? 0);
  const lat0 = Number(origin[1] ?? 0);
  return [lon0 + e / (111_320 * Math.max(0.1, Math.cos((lat0 * Math.PI) / 180))), lat0 + n / 110_540];
}

/** Highlighted drones closer than this share one label: their own labels would overprint. */
const LABEL_MERGE_M = 10;

/**
 * One label per group of nearby highlighted drones, at the group's centre. A pair (a conflict)
 * reads "D07 – D32 · 0.8 m" with their current distance; larger groups list the drones.
 */
function droneLabels(ids: readonly string[], positions: readonly (readonly number[])[], highlighted: ReadonlySet<string>, units: Units): DroneLabel[] {
  const groups: { ids: string[]; points: [number, number, number][] }[] = [];
  for (const d of dronePoints(ids, positions)) {
    if (!highlighted.has(d.id)) continue;
    const near = groups.find((g) => g.points.some((q) => Math.hypot(q[0] - d.position[0], q[1] - d.position[1], q[2] - d.position[2]) < LABEL_MERGE_M));
    if (near) {
      near.ids.push(d.id);
      near.points.push(d.position);
    } else {
      groups.push({ ids: [d.id], points: [d.position] });
    }
  }
  return groups.map((g) => {
    const mean = (k: 0 | 1 | 2) => g.points.reduce((s, q) => s + q[k], 0) / g.points.length;
    const position: [number, number, number] = [mean(0), mean(1), mean(2)];
    const [a, b] = g.points;
    if (g.ids.length === 2 && a && b) {
      const apart = Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
      return { position, text: `${g.ids[0]} – ${g.ids[1]} · ${formatLength(apart, units, { maximumFractionDigits: 1 })}` };
    }
    return { position, text: g.ids.join(" · ") };
  });
}

/** Form defaults (ShowParams in airbrief_core/models/analyses.py). */
const SHOW_DEFAULTS = {
  minSep: 1.5,
  hSpeed: 8,
  climb: 4,
  descent: 3,
  accel: 4,
  ceiling: 120,
  audienceBuffer: 50,
  audienceRule: "fixed" as ShowAudienceRule,
  breachPct: 0,
  boxMargin: 5,
  padSpacing: 1.5,
  lowSpeedTolerance: 0.5,
  sampleRate: 10,
  playbackRate: 4,
  dynamicsWindow: 1,
  airborneThreshold: 1,
  maxEvents: 200,
};

function ShowsPanel({ file, features, analysis }: ModulePanelProps) {
  const runner = useAnalysisRunner();
  const form = useFormErrors();
  const [minSep, setMinSep] = React.useState<number | null>(SHOW_DEFAULTS.minSep);
  const [hSpeed, setHSpeed] = React.useState<number | null>(SHOW_DEFAULTS.hSpeed);
  const [climb, setClimb] = React.useState<number | null>(SHOW_DEFAULTS.climb);
  const [descent, setDescent] = React.useState<number | null>(SHOW_DEFAULTS.descent);
  const [accel, setAccel] = React.useState<number | null>(SHOW_DEFAULTS.accel);
  const [ceiling, setCeiling] = React.useState<number | null>(SHOW_DEFAULTS.ceiling);
  const [audienceBuffer, setAudienceBuffer] = React.useState<number | null>(SHOW_DEFAULTS.audienceBuffer);
  const [audienceRule, setAudienceRule] = React.useState<ShowAudienceRule>(SHOW_DEFAULTS.audienceRule);
  const [breachPct, setBreachPct] = React.useState<number | null>(SHOW_DEFAULTS.breachPct);
  const [boxMargin, setBoxMargin] = React.useState<number | null>(SHOW_DEFAULTS.boxMargin);
  const [padSpacing, setPadSpacing] = React.useState<number | null>(SHOW_DEFAULTS.padSpacing);
  const [endurance, setEndurance] = React.useState<number | null>(null);
  const [minNavAltitude, setMinNavAltitude] = React.useState<number | null>(null);
  const [lowSpeedTolerance, setLowSpeedTolerance] = React.useState<number | null>(SHOW_DEFAULTS.lowSpeedTolerance);
  const [sampleRate, setSampleRate] = React.useState<number | null>(SHOW_DEFAULTS.sampleRate);
  const [playbackRate, setPlaybackRate] = React.useState<number | null>(SHOW_DEFAULTS.playbackRate);
  const [dynamicsWindow, setDynamicsWindow] = React.useState<number | null>(SHOW_DEFAULTS.dynamicsWindow);
  const [airborneThreshold, setAirborneThreshold] = React.useState<number | null>(SHOW_DEFAULTS.airborneThreshold);
  const [maxEvents, setMaxEvents] = React.useState<number | null>(SHOW_DEFAULTS.maxEvents);
  // Origin and heights: "From the file" leaves both to the API (absolute altitudes in the file
  // set them; otherwise the origin is the mean first position at 0 m).
  const [altitudeChoice, setAltitudeChoice] = React.useState<AltitudeChoice>("auto");
  const [originSet, setOriginSet] = React.useState(false);
  const [originLon, setOriginLon] = React.useState<number | null>(null);
  const [originLat, setOriginLat] = React.useState<number | null>(null);
  // Empty: absolute altitudes are measured from the lowest take-off position (the API's rule).
  const [originGround, setOriginGround] = React.useState<number | null>(null);
  const [picking, setPicking] = React.useState(false);

  const trackCount = React.useMemo(
    () => (features?.features ?? []).filter((f) => f.geometry?.type === "LineString" || f.geometry?.type === "MultiLineString").length,
    [features],
  );
  const hasShowBox = React.useMemo(
    () => (features?.features ?? []).some((f) => /show\s*box/i.test(f.properties._name ?? "") && (f.geometry?.type === "Polygon" || f.geometry?.type === "MultiPolygon")),
    [features],
  );

  const ready = file?.status === "COMPLETED";
  const disabledReason = !file
    ? "Open a show file (KML with gx:Track per drone, or CSV drone_id,t,x,y,z)."
    : !ready
      ? "Wait for the dataset to finish processing."
      : trackCount === 0
        ? "This dataset has no drone tracks."
        : null;

  // The fields follow the analysis on screen (reopened, from History or a shared link); an
  // analysis started here already matches them. Stored params hold what the API resolved, so a
  // file's absolute altitudes come back as the origin and reference that were used.
  const applyParams = React.useCallback((p: Readonly<Record<string, unknown>>) => {
    const d = SHOW_DEFAULTS;
    setMinSep(paramNumber(p, "min_separation_m", d.minSep));
    setHSpeed(paramNumber(p, "max_horizontal_speed_ms", d.hSpeed));
    setClimb(paramNumber(p, "max_climb_rate_ms", d.climb));
    setDescent(paramNumber(p, "max_descent_rate_ms", d.descent));
    setAccel(paramNumber(p, "max_acceleration_ms2", d.accel));
    setCeiling(paramNumber(p, "ceiling_m", d.ceiling));
    setAudienceBuffer(paramNumber(p, "audience_buffer_m", d.audienceBuffer));
    setAudienceRule(paramChoice(p, "audience_buffer_rule", AUDIENCE_RULES, d.audienceRule));
    setBreachPct(paramNumber(p, "audience_breach_margin_pct", d.breachPct));
    setBoxMargin(paramNumber(p, "show_box_margin_m", d.boxMargin));
    setPadSpacing(paramNumber(p, "min_pad_spacing_m", d.padSpacing));
    setEndurance(paramNumber(p, "endurance_s", null));
    setMinNavAltitude(paramNumber(p, "min_navigation_altitude_m", null));
    setLowSpeedTolerance(paramNumber(p, "low_altitude_speed_tolerance_ms", d.lowSpeedTolerance));
    setSampleRate(paramNumber(p, "sample_rate_hz", d.sampleRate));
    setPlaybackRate(paramNumber(p, "playback_rate_hz", d.playbackRate));
    setDynamicsWindow(paramNumber(p, "dynamics_window_s", d.dynamicsWindow));
    setAirborneThreshold(paramNumber(p, "airborne_threshold_m", d.airborneThreshold));
    setMaxEvents(paramNumber(p, "max_events", d.maxEvents));
    setAltitudeChoice(paramChoice<AltitudeChoice>(p, "altitude_reference", ["relative", "absolute"], "auto"));
    const origin = Array.isArray(p.origin) ? p.origin : null;
    const valid = origin !== null && origin.length >= 2 && origin.every((v) => typeof v === "number" && Number.isFinite(v));
    setOriginSet(valid);
    setOriginLon(valid ? (origin[0] as number) : null);
    setOriginLat(valid ? (origin[1] as number) : null);
    setOriginGround(valid && typeof origin[2] === "number" && Number.isFinite(origin[2]) ? origin[2] : null);
  }, []);
  const { markStarted, setFields, fieldsKey } = useFormFromAnalysis(analysis, applyParams);

  // "Pick on map" borrows the plan pin tool (crosshair, Space places at the map centre, Escape
  // cancels): the pin becomes the origin and never an operation draft. The listener runs inside
  // the store update, so an unsaved plan draft the pin replaced is put back before anything renders.
  React.useEffect(() => {
    if (!picking) return;
    const stop = usePlan.subscribe((state, prev) => {
      if (prev.drawing !== "pin" || state.drawing === "pin") return;
      const draft = state.draft;
      setPicking(false);
      if (draft && draft !== prev.draft && draft.kind === "pin") {
        const point = draft.vertices[0];
        usePlan.getState().setDraft(prev.draft);
        if (point) {
          setOriginSet(true);
          setOriginLon(Math.round(point[0] * 1e7) / 1e7);
          setOriginLat(Math.round(point[1] * 1e7) / 1e7);
          announce(`Show origin set to ${formatCoord(point[0], point[1])}.`);
        }
      } else {
        // Escape, another plan tool, the coordinates dialog or a module's own map pick.
        announce("Picking the origin was cancelled.");
      }
    });
    return () => {
      stop();
      if (usePlan.getState().drawing === "pin") usePlan.getState().cancelDrawing();
    };
  }, [picking]);
  const pickOnMap = () => {
    setPicking(true);
    usePlan.getState().startDrawing("pin");
    announce("Click the map where the show origin is, or press Space to use the map centre. Escape cancels.");
  };

  const onRun = form.guard(() => {
    const d = SHOW_DEFAULTS;
    const params: AnalysisRequest<"show">["params"] = {
      min_separation_m: minSep ?? d.minSep,
      max_horizontal_speed_ms: hSpeed ?? d.hSpeed,
      max_climb_rate_ms: climb ?? d.climb,
      max_descent_rate_ms: descent ?? d.descent,
      max_acceleration_ms2: accel ?? d.accel,
      ceiling_m: ceiling ?? d.ceiling,
      audience_buffer_m: audienceBuffer ?? d.audienceBuffer,
      audience_buffer_rule: audienceRule,
      audience_breach_margin_pct: breachPct ?? 0,
      show_box_margin_m: boxMargin ?? d.boxMargin,
      min_pad_spacing_m: padSpacing ?? d.padSpacing,
      sample_rate_hz: sampleRate ?? d.sampleRate,
      playback_rate_hz: playbackRate ?? d.playbackRate,
      dynamics_window_s: dynamicsWindow ?? d.dynamicsWindow,
      airborne_threshold_m: airborneThreshold ?? d.airborneThreshold,
      max_events: maxEvents ?? d.maxEvents,
      ...(endurance !== null ? { endurance_s: endurance } : {}),
      ...(minNavAltitude !== null
        ? { min_navigation_altitude_m: minNavAltitude, low_altitude_speed_tolerance_ms: lowSpeedTolerance ?? d.lowSpeedTolerance }
        : {}),
      ...(altitudeChoice !== "auto" ? { altitude_reference: altitudeChoice } : {}),
      ...(originSet && originLon !== null && originLat !== null
        ? { origin: originGround === null ? ([originLon, originLat] as [number, number]) : ([originLon, originLat, originGround] as [number, number, number]) }
        : {}),
    };
    void runner.run("show", params, { featureIndices: [] }).then((info) => {
      if (info) markStarted(info.id);
    });
  });

  return (
    <form className="space-y-4 text-sm" onSubmit={(e) => e.preventDefault()} aria-label="Drone-show validation">
      <InputHint>
        Tracks come from the dataset ({formatNumber(trackCount)} found). Polygons named &quot;Show box&quot; and &quot;Audience&quot; are
        used for the site checks; without a show box, one is derived from the tracks.
      </InputHint>
      <FormErrorSummary form={form} />
      <React.Fragment key={fieldsKey}>
        <ModuleSection title="Safety limits">
          <NumField label="Minimum separation" unit="m" value={minSep} onChange={setMinSep} min={0.1} max={100} step={0.1} form={form} />
          <NumField label="Max horizontal speed" unit="m/s" value={hSpeed} onChange={setHSpeed} min={0.1} max={100} form={form} />
          <NumField label="Max climb rate" unit="m/s" value={climb} onChange={setClimb} min={0.1} max={50} form={form} />
          <NumField label="Max descent rate" unit="m/s" value={descent} onChange={setDescent} min={0.1} max={50} form={form} />
          <NumField label="Max acceleration" unit="m/s²" value={accel} onChange={setAccel} min={0.1} max={100} form={form} />
          <NumField label="Ceiling" unit="m" value={ceiling} onChange={setCeiling} min={1} max={2000} hint="Above the show origin." form={form} />
        </ModuleSection>

        <ModuleSection title="Audience">
          <div className="space-y-1.5">
            <p className="text-sm font-medium" id="show-audience-rule">
              Audience distance
            </p>
            <ToggleGroup
              type="single"
              value={audienceRule}
              onValueChange={(v) => v && setAudienceRule(v as ShowAudienceRule)}
              aria-labelledby="show-audience-rule"
              aria-describedby="show-audience-rule-hint"
            >
              {AUDIENCE_RULES.map((r) => (
                <ToggleGroupItem key={r} value={r}>
                  {AUDIENCE_RULE_LABEL[r]}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            <p id="show-audience-rule-hint" className="text-xs text-fg-muted">
              {audienceRule === "fixed"
                ? "A distance you set."
                : audienceRule === "one_to_one"
                  ? "The show's greatest height above the origin (the 1:1 rule)."
                  : "Fall-out distance v·√(2h/g) from the show's greatest height and horizontal speed (no drag)."}
            </p>
          </div>
          {audienceRule === "fixed" ? (
            <NumField label="Audience buffer" unit="m" value={audienceBuffer} onChange={setAudienceBuffer} min={0} max={2000} form={form} />
          ) : null}
          <NumField
            label="Safety factor"
            unit="%"
            value={breachPct}
            onChange={setBreachPct}
            min={0}
            max={500}
            presets={{ metric: [0, 25, 50], label: "Safety factor presets" }}
            hint="Added to the audience distance (e.g. +50 %): a margin you choose, not a regulatory value."
            form={form}
          />
          <NumField
            label="Derived show box margin"
            unit="m"
            value={boxMargin}
            onChange={setBoxMargin}
            min={0.1}
            max={500}
            hint={hasShowBox ? "Not used: the dataset has a “Show box” polygon." : "Added around the tracks' extent when there is no “Show box” polygon."}
            form={form}
          />
        </ModuleSection>

        <ModuleSection title="Pads and endurance">
          <NumField label="Minimum pad spacing" unit="m" value={padSpacing} onChange={setPadSpacing} min={0.1} max={100} step={0.1} hint="Between drones on the ground at take-off and at landing." form={form} />
          <NumField
            label="Endurance per drone"
            unit="s"
            value={endurance}
            onChange={setEndurance}
            min={1}
            max={36_000}
            optional
            integer
            hint="Airborne time a battery allows; leave empty to skip."
            form={form}
          />
        </ModuleSection>

        <ModuleSection title="Origin and heights">
          <div className="space-y-1.5">
            <Label htmlFor="show-altitude">Track heights</Label>
            <Select value={altitudeChoice} onValueChange={(v) => setAltitudeChoice(v === "relative" || v === "absolute" ? v : "auto")}>
              <SelectTrigger id="show-altitude" aria-describedby="show-altitude-hint">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(["auto", "relative", "absolute"] as const).map((c) => (
                  <SelectItem key={c} value={c}>
                    {ALTITUDE_LABEL[c]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p id="show-altitude-hint" className="text-xs text-fg-muted">
              “From the file” uses its altitude mode: absolute altitudes are measured from the lowest take-off pad. Above sea level
              subtracts the origin&apos;s ground altitude.
            </p>
          </div>
          <SwitchField
            label="Set the origin"
            checked={originSet}
            onChange={(on) => {
              setOriginSet(on);
              // The fields (and the map pick) go away with the switch: end a pending pick too.
              if (!on && picking && usePlan.getState().drawing === "pin") usePlan.getState().cancelDrawing();
            }}
            hint="Otherwise the origin is the mean first position (or, for absolute altitudes, the take-off pads)."
          />
          {originSet ? (
            <div className="space-y-3">
              <NumField label="Origin longitude" unit="°" value={originLon} onChange={setOriginLon} min={-180} max={180} step={0.0001} decimals={7} form={form} />
              <NumField label="Origin latitude" unit="°" value={originLat} onChange={setOriginLat} min={-90} max={90} step={0.0001} decimals={7} form={form} />
              <NumField
                label="Origin ground altitude"
                unit="m"
                value={originGround}
                onChange={setOriginGround}
                min={-500}
                max={9000}
                optional
                hint="Above sea level; used with absolute altitudes. Leave empty to measure from the lowest take-off position."
                form={form}
              />
              <Button type="button" variant="outline" size="sm" className="w-full" onClick={pickOnMap} disabled={picking}>
                <Crosshair aria-hidden /> {picking ? "Click the map…" : "Pick the origin on the map"}
              </Button>
            </div>
          ) : null}
        </ModuleSection>

        <Advanced form={form}>
          <NumField
            label="Minimum navigation height"
            unit="m"
            value={minNavAltitude}
            onChange={setMinNavAltitude}
            min={0.1}
            max={500}
            optional
            hint="Flags horizontal motion below this height (drones should climb vertically first); leave empty to skip."
            form={form}
          />
          {minNavAltitude !== null ? (
            <NumField label="Horizontal speed tolerance" unit="m/s" value={lowSpeedTolerance} onChange={setLowSpeedTolerance} min={0.01} max={20} step={0.1} form={form} />
          ) : null}
          <NumField label="Check sample rate" unit="Hz" value={sampleRate} onChange={setSampleRate} min={1} max={50} step={0.5} decimals={1} form={form} />
          <NumField
            label="Dynamics window"
            unit="s"
            value={dynamicsWindow}
            onChange={setDynamicsWindow}
            min={0.1}
            max={10}
            step={0.1}
            hint="Speeds, climb rates and accelerations are averaged over this window; a longer one smooths peaks away (the brief flags more than 1 s)."
            form={form}
          />
          <NumField
            label="Airborne threshold"
            unit="m"
            value={airborneThreshold}
            onChange={setAirborneThreshold}
            min={0}
            max={50}
            step={0.5}
            hint="Drones at or below this height count as on the ground: no separation check, and pads are measured there (the brief flags more than 1 m)."
            form={form}
          />
          <NumField
            label="Events listed"
            value={maxEvents}
            onChange={setMaxEvents}
            min={1}
            max={10_000}
            decimals={0}
            integer
            hint="Most conflicts and violations listed (conflicts first); the result says when more were found."
            form={form}
          />
          <NumField label="Playback frame rate" unit="Hz" value={playbackRate} onChange={setPlaybackRate} min={0.5} max={20} form={form} />
        </Advanced>
      </React.Fragment>
      {runner.error ? <ErrorState compact error={runner.error} title="Show validation could not start" onRetry={disabledReason ? undefined : onRun} retrying={runner.isPending} /> : null}
      <RunButton onRun={onRun} pending={runner.isPending} disabledReason={disabledReason} label="Validate show" />
      <ResetFormButton onReset={() => setFields({})} />
    </form>
  );
}

function TransportBar({ result }: { result: ShowResult }) {
  const { start, end, step } = showBounds(result.frames);
  const playback = useUi((s) => s.playback);
  const setPlaying = useUi((s) => s.setPlaying);
  const setPlaybackTime = useUi((s) => s.setPlaybackTime);
  const setPlaybackSpeed = useUi((s) => s.setPlaybackSpeed);
  const { time: urlTime, setTime } = useWorkspaceParams();
  const reducedMotion = usePrefersReducedMotion();

  // Initialise from the URL (deep link) once, paused.
  const initialised = React.useRef(false);
  React.useEffect(() => {
    if (initialised.current) return;
    initialised.current = true;
    setPlaying(false);
    setPlaybackTime(urlTime !== null ? Math.min(end, Math.max(start, urlTime)) : start);
  }, [urlTime, start, end, setPlaying, setPlaybackTime]);

  React.useEffect(() => {
    if (!playback.playing) return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      const { time, speed, playing } = useUi.getState().playback;
      if (!playing) return;
      const next = time + dt * speed;
      if (next >= end) {
        setPlaybackTime(end);
        setPlaying(false);
        setTime(end);
        announce("Playback finished.");
        return;
      }
      setPlaybackTime(next);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playback.playing, end, setPlaybackTime, setPlaying, setTime]);

  const seek = React.useCallback(
    (t: number) => {
      const clamped = Math.min(end, Math.max(start, t));
      setPlaybackTime(clamped);
      setTime(Math.round(clamped * 10) / 10);
    },
    [start, end, setPlaybackTime, setTime],
  );

  const toggle = () => {
    if (playback.playing) {
      setPlaying(false);
      setTime(Math.round(playback.time * 10) / 10);
    } else {
      if (playback.time >= end) setPlaybackTime(start);
      setPlaying(true);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    // Keys from the speed listbox (rendered in a portal, but React events still bubble here),
    // inputs and buttons belong to those controls, not to the transport shortcuts.
    const target = e.target as HTMLElement;
    if (
      e.defaultPrevented ||
      !e.currentTarget.contains(target) ||
      target.closest('[role="listbox"], [role="option"], [role="combobox"], input, select, textarea')
    ) {
      return;
    }
    if (e.key === " " && !(target instanceof HTMLButtonElement)) {
      e.preventDefault();
      toggle();
    } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      if (target.getAttribute("role") === "slider") return; // the slider handles its own keys
      e.preventDefault();
      const dir = e.key === "ArrowLeft" ? -1 : 1;
      setPlaying(false);
      seek(playback.time + dir * (e.shiftKey ? 5 : step));
    }
  };

  const ticks = result.conflicts.map((c) => ({ key: conflictKey(c), left: end > start ? ((c.t_min_s - start) / (end - start)) * 100 : 0 }));

  return (
    <div
      role="group"
      aria-label="Show playback"
      className="space-y-2 rounded border border-border bg-surface p-2"
      onKeyDown={onKeyDown}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="icon-sm" variant="outline" aria-label="Previous frame" onClick={() => { setPlaying(false); seek(playback.time - step); }}>
          <SkipBack aria-hidden />
        </Button>
        {/* A plain button whose label says what it does (not a toggle: its name changes with the state). */}
        <Button type="button" size="sm" onClick={toggle}>
          {playback.playing ? <Pause aria-hidden /> : <Play aria-hidden />}
          {playback.playing ? "Pause" : "Play"}
        </Button>
        <Button type="button" size="icon-sm" variant="outline" aria-label="Next frame" onClick={() => { setPlaying(false); seek(playback.time + step); }}>
          <SkipForward aria-hidden />
        </Button>
        <span className="num text-sm" aria-live="off">
          {formatClock(playback.time)} / {formatClock(end)}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <label htmlFor="show-speed" className="text-xs text-fg-muted">
            Speed
          </label>
          <Select value={String(playback.speed)} onValueChange={(v) => setPlaybackSpeed(Number(v))}>
            <SelectTrigger id="show-speed" className="h-8 w-20">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PLAYBACK_SPEEDS.map((s) => (
                <SelectItem key={s} value={String(s)}>
                  {s}×
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="relative px-1">
        <Slider
          value={[playback.time]}
          min={start}
          max={end}
          step={step}
          onValueChange={(v) => {
            setPlaying(false);
            setPlaybackTime(v[0] ?? start);
          }}
          onValueCommit={(v) => seek(v[0] ?? start)}
          thumbLabels={["Show time"]}
          getValueText={(v) => `${formatNumber(v, { maximumFractionDigits: 1 })} s of ${formatNumber(end, { maximumFractionDigits: 0 })} s`}
        />
        <div aria-hidden className="pointer-events-none absolute inset-x-1 -bottom-2 h-2">
          {ticks.map((t) => (
            <span key={t.key} className="absolute h-2 w-0.5 bg-danger" style={{ left: `${t.left}%` }} />
          ))}
        </div>
      </div>
      <p className="text-xs text-fg-muted">
        {ticks.length > 0 ? "Red ticks mark conflicts. " : ""}Space plays or pauses; arrow keys step one frame; Shift+arrows jump 5 s.
        {reducedMotion ? " Reduced motion: the camera never follows drones." : ""}
      </p>
    </div>
  );
}

/** The smaller of the take-off and landing pad spacing, or null when neither was checked. */
function minPadSpacing(result: ShowResult): number | null {
  const values = [result.pads?.takeoff_min_spacing_m, result.pads?.landing_min_spacing_m].filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  return values.length > 0 ? Math.min(...values) : null;
}

/** The origin and height reference the check used: "Origin 13.00050° N, 77.59200° E (given) · …". */
function describeOrigin(result: ShowResult, units: Units): string {
  const [lon, lat, ground] = result.frames.origin;
  // Results saved before the origin source was recorded do not say where the origin came from.
  const source =
    result.origin_source === "inferred" ? " (the mean first position)" : result.origin_source === "given" ? " (given)" : "";
  const heights =
    result.altitude_reference === "absolute"
      ? `heights above sea level, measured from ${formatHeight(ground ?? 0, units)} at the origin`
      : "heights relative to the origin";
  return `Origin ${formatCoord(lon, lat)}${source} · ${heights}.`;
}

function ShowsResults({ analysis }: ModuleResultsProps) {
  const units = useUnits();
  const result = analysisResult(analysis, "show");
  const setPlaying = useUi((s) => s.setPlaying);
  const setPlaybackTime = useUi((s) => s.setPlaybackTime);
  const highlight = useUi((s) => s.resultHighlight);
  const setHighlight = useUi((s) => s.setResultHighlight);
  const { setTime } = useWorkspaceParams();
  const focus = useFocusMap();
  const sampleRate = result?.sample_rate_hz ?? null;

  const seekTo = React.useCallback(
    (t: number, label: string) => {
      setPlaying(false);
      setPlaybackTime(t);
      setTime(Math.round(t * 10) / 10);
      announce(label);
    },
    [setPlaying, setPlaybackTime, setTime],
  );

  const conflictColumns = React.useMemo<DataTableColumn<ShowConflict>[]>(
    () => [
      { id: "time", header: "Time", numeric: true, cell: (c) => formatClock(c.t_min_s), sortValue: (c) => c.t_min_s },
      { id: "pair", header: "Drones", rowHeader: true, cell: (c) => `${c.drone_a} – ${c.drone_b}`, sortValue: (c) => `${c.drone_a}-${c.drone_b}` },
      { id: "distance", header: "Closest", numeric: true, cell: (c) => formatLength(c.min_distance_m, units, { maximumFractionDigits: 2 }), sortValue: (c) => c.min_distance_m },
      {
        id: "duration",
        header: "Duration",
        numeric: true,
        // A conflict seen at a single check sample lasted less than one sample interval, not 0 s.
        cell: (c) => {
          const duration = c.end_s - c.start_s;
          const interval = sampleRate && sampleRate > 0 ? 1 / sampleRate : 0;
          return duration < interval - 1e-9
            ? `< ${formatNumber(interval, { maximumFractionDigits: 2 })} s`
            : `${formatNumber(duration, { maximumFractionDigits: 1 })} s`;
        },
        sortValue: (c) => c.end_s - c.start_s,
      },
    ],
    [units, sampleRate],
  );

  const violationColumns = React.useMemo<DataTableColumn<ShowViolation>[]>(
    () => [
      { id: "time", header: "From", numeric: true, cell: (v) => formatClock(v.start_s), sortValue: (v) => v.start_s },
      { id: "kind", header: "Check", rowHeader: true, cell: (v) => VIOLATION_LABEL[v.kind], sortValue: (v) => v.kind },
      { id: "drone", header: "Drone", cell: (v) => v.drone, sortValue: (v) => v.drone },
      { id: "peak", header: "Peak / limit", numeric: true, cell: (v) => violationValue(v, units) },
    ],
    [units],
  );

  const padColumns = React.useMemo<DataTableColumn<ShowPadConflict>[]>(
    () => [
      { id: "phase", header: "When", cell: (c) => (c.phase === "takeoff" ? "Take-off" : "Landing"), sortValue: (c) => c.phase },
      { id: "pair", header: "Drones", rowHeader: true, cell: (c) => `${c.drone_a} – ${c.drone_b}`, sortValue: (c) => `${c.drone_a}-${c.drone_b}` },
      { id: "distance", header: "Apart", numeric: true, cell: (c) => formatLength(c.distance_m, units, { maximumFractionDigits: 2 }), sortValue: (c) => c.distance_m },
    ],
    [units],
  );

  // "Check airspace for this show box": an airspace check of the box at the show ceiling. It
  // opens in the airspace module (the runner switches modules) on the same dataset.
  const runner = useAnalysisRunner();
  const boxGeometry = result ? (resultLayer(result.layers, "show_box").features[0]?.geometry ?? null) : null;
  // The airspace check takes heights up to 1,500 m (AirspaceParams); show ceilings go to 2,000 m.
  const showCeiling = Math.min(AIRSPACE_MAX_AGL_M, analysis && typeof analysis.params.ceiling_m === "number" ? analysis.params.ceiling_m : 120);
  const checkAirspace = () => {
    if (!boxGeometry) return;
    void runner.run("airspace", { geometry: boxGeometry, altitude_agl_m: showCeiling }, { featureIndices: [] });
  };

  const droneColumns = React.useMemo<DataTableColumn<DroneStats>[]>(
    () => [
      { id: "drone", header: "Drone", rowHeader: true, cell: (d) => d.drone, sortValue: (d) => d.drone },
      { id: "airborne", header: "Airborne", numeric: true, cell: (d) => formatClock(d.airborne_s), sortValue: (d) => d.airborne_s },
      { id: "distance", header: "Distance", numeric: true, cell: (d) => formatLength(d.distance_m, units), sortValue: (d) => d.distance_m },
      { id: "alt", header: "Max height", numeric: true, cell: (d) => formatHeight(d.max_altitude_m, units), sortValue: (d) => d.max_altitude_m },
      { id: "speed", header: "Max speed", numeric: true, cell: (d) => formatSpeed(d.max_horizontal_speed_ms, units), sortValue: (d) => d.max_horizontal_speed_ms },
      { id: "accel", header: "Max accel.", numeric: true, cell: (d) => `${formatNumber(d.max_acceleration_ms2, { maximumFractionDigits: 2 })} m/s²`, sortValue: (d) => d.max_acceleration_ms2 },
    ],
    [units],
  );

  if (!analysis) return null;
  if (!result) {
    return analysis.status === "COMPLETED" ? <EmptyState compact icon={<Sparkles />} title="No result" /> : null;
  }

  const summary =
    result.conflicts.length === 0 && result.violations.length === 0
      ? `No conflicts or limit violations across ${formatNumber(result.frames_checked)} frames.`
      : `${result.conflicts.length} conflict${result.conflicts.length === 1 ? "" : "s"} and ${result.violations.length} violation${result.violations.length === 1 ? "" : "s"}.`;

  return (
    <div className="space-y-4">
      <VerdictBanner verdict={result.verdict} title="Show" summary={summary} />
      {/* Pinned to the top of the drawer, so the clock, Play and frame steps stay at hand while
          the conflict and violation tables below are reviewed. */}
      <div className="sticky top-0 z-10 bg-surface pb-1">
        <TransportBar key={analysis.id} result={result} />
      </div>
      <KpiGrid label="Show summary">
        <KpiTile label="Drones" value={formatNumber(result.drone_count)} explanation={`${formatClock(result.duration_s)} long`} />
        <KpiTile
          label="Minimum separation"
          value={lengthParts(result.min_separation_m, units, { maximumFractionDigits: 2 })}
          explanation={result.min_separation_pair ? `${result.min_separation_pair.join(" – ")} at ${formatClock(result.min_separation_at_s)}` : undefined}
        />
        <KpiTile label="Conflicts" value={formatNumber(result.conflicts.length)} tone={result.conflicts.length > 0 ? "danger" : "ok"} toneLabel={result.conflicts.length > 0 ? "Too close" : "None"} />
        <KpiTile label="Limit violations" value={formatNumber(result.violations.length)} tone={result.violations.length > 0 ? "danger" : "ok"} toneLabel={result.violations.length > 0 ? "Check table" : "None"} />
        <KpiTile label="Checked at" value={formatNumber(result.sample_rate_hz)} unit="Hz" explanation={`${result.backend === "cpp" ? "C++" : "Python"} kernel`} />
        {typeof result.max_altitude_m === "number" ? (
          <KpiTile label="Max height" value={heightParts(result.max_altitude_m, units, { maximumFractionDigits: 1 })} explanation="Above the show origin" />
        ) : null}
        {result.pads ? (
          <KpiTile
            label="Pad spacing"
            value={lengthParts(minPadSpacing(result), units, { maximumFractionDigits: 2 })}
            tone={result.pads.conflicts.length > 0 ? "danger" : "ok"}
            toneLabel={result.pads.conflicts.length > 0 ? `Below ${formatLength(result.pads.limit_m, units)}` : "Clear"}
            explanation={`${formatNumber(result.pads.takeoff_pads)} take-off, ${formatNumber(result.pads.landing_pads)} landing pads`}
          />
        ) : null}
      </KpiGrid>
      {result.events_truncated ? <p className="text-xs text-warn">More events exist than are listed (event limit reached).</p> : null}
      <p className="text-xs text-fg-muted">{describeOrigin(result, units)}</p>

      {/* Rows scrolled into view stop below the pinned transport bar. */}
      <ModuleSection title={`Conflicts (${result.conflicts.length})`} className="[&_tr]:scroll-mt-40">
        <DataTable
          data={result.conflicts}
          columns={conflictColumns}
          getRowId={conflictKey}
          caption="Separation conflicts"
          onRowClick={(c) => {
            setHighlight(conflictKey(c));
            seekTo(c.t_min_s, `Conflict between ${c.drone_a} and ${c.drone_b} at ${formatNumber(c.t_min_s, { maximumFractionDigits: 1 })} seconds, ${formatNumber(c.min_distance_m, { maximumFractionDigits: 2 })} metres apart.`);
            // Centre the map on the pair at closest approach (zooming with + keeps them in view).
            const [east, north] = c.position_enu;
            if (typeof east === "number" && typeof north === "number" && Number.isFinite(east) && Number.isFinite(north)) {
              const [lon, lat] = enuToLonLat(result.frames.origin, east, north);
              focus(pointBounds(lon, lat, 30));
            }
          }}
          selectedRowIds={highlight?.startsWith("conflict-") ? [highlight] : []}
          getRowLabel={(c) => `Go to conflict between ${c.drone_a} and ${c.drone_b}`}
          initialSort={{ columnId: "time", direction: "asc" }}
          dense
          emptyState={<EmptyState compact title="No conflicts" description="Every pair kept the minimum separation." />}
        />
      </ModuleSection>

      <ModuleSection title={`Limit violations (${result.violations.length})`} className="[&_tr]:scroll-mt-40">
        <DataTable
          data={result.violations}
          columns={violationColumns}
          getRowId={(v, i) => `${v.kind}-${v.drone}-${v.start_s}-${i}`}
          caption="Speed, acceleration, geofence, audience, ceiling, endurance and low-height motion violations"
          onRowClick={(v) => seekTo(v.start_s, `${VIOLATION_LABEL[v.kind]} by ${v.drone} at ${formatNumber(v.start_s, { maximumFractionDigits: 1 })} seconds.`)}
          getRowLabel={(v) => `Go to ${VIOLATION_LABEL[v.kind]} of ${v.drone}`}
          dense
          emptyState={<EmptyState compact title="No violations" />}
        />
      </ModuleSection>

      <ModuleSection title="Site">
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
          <dt className="text-fg-muted">Show box</dt>
          <dd className="num text-right">{result.site.show_box_area_m2 === null || result.site.show_box_area_m2 === undefined ? "Not defined" : formatArea(result.site.show_box_area_m2, units)}</dd>
          {result.site.show_box_source ? (
            <>
              <dt className="text-fg-muted">Box source</dt>
              <dd className="text-right">
                {result.site.show_box_source === "derived"
                  ? `Derived from the tracks (+${formatLength(result.site.show_box_margin_m ?? 0, units)} margin)`
                  : "“Show box” polygon"}
              </dd>
            </>
          ) : null}
          {result.site.audience_buffer ? (
            <>
              <dt className="text-fg-muted">Audience distance</dt>
              <dd className="num text-right">
                {formatLength(result.site.audience_buffer.distance_m, units, { maximumFractionDigits: 1 })} ({AUDIENCE_RULE_LABEL[result.site.audience_buffer.rule]})
              </dd>
            </>
          ) : null}
          <dt className="text-fg-muted">Box to audience</dt>
          <dd className="num text-right">{formatLength(result.site.box_audience_distance_m ?? null, units)}</dd>
          <dt className="text-fg-muted">Audience buffer</dt>
          <dd className="text-right">
            {result.site.audience_area_m2 === null || result.site.audience_area_m2 === undefined ? (
              "No audience area"
            ) : (
              <ToneBadge tone={result.site.box_overlaps_audience_buffer ? "danger" : "ok"} label={result.site.box_overlaps_audience_buffer ? "Overlaps the box" : "Clear of the box"} />
            )}
          </dd>
        </dl>
        {result.site.audience_buffer ? <p className="text-xs text-fg-muted">{localizeUnits(result.site.audience_buffer.formula, units)}</p> : null}
        <div className="space-y-1.5 pt-1">
          <Button type="button" size="sm" variant="secondary" className="w-full" onClick={checkAirspace} disabled={!boxGeometry || DEMO_MODE || runner.isPending} aria-describedby="show-airspace-hint">
            <Plane aria-hidden /> Check airspace for this show box
          </Button>
          <p id="show-airspace-hint" className="text-xs text-fg-muted">
            {DEMO_MODE
              ? "The public demo is read-only: run AirBrief yourself to check the airspace."
              : boxGeometry
                ? `Runs the airspace check on the show box at the ${formatHeight(showCeiling, units)} ceiling (taken as height above ground) and opens it.`
                : "This result has no show box (it was validated before boxes were derived from the tracks): validate the show again to check its airspace."}
          </p>
        </div>
      </ModuleSection>

      {result.pads && result.pads.conflicts.length > 0 ? (
        <ModuleSection title={`Pad spacing (${result.pads.conflicts.length}${result.pads.conflicts_truncated ? "+" : ""})`}>
          <DataTable
            data={result.pads.conflicts}
            columns={padColumns}
            getRowId={(c, i) => `pad-${c.phase}-${c.drone_a}-${c.drone_b}-${i}`}
            caption={`Drones closer than ${formatLength(result.pads.limit_m, units)} on the ground`}
            onRowClick={(c, i) => {
              seekTo(c.time_s, `${c.phase === "takeoff" ? "Take-off" : "Landing"} pads of ${c.drone_a} and ${c.drone_b}, ${formatNumber(c.distance_m, { maximumFractionDigits: 2 })} metres apart.`);
              const feature = resultLayer(result.layers, "pad_conflicts").features[i];
              if (feature) focus(feature);
            }}
            getRowLabel={(c) => `Go to the ${c.phase === "takeoff" ? "take-off" : "landing"} pads of ${c.drone_a} and ${c.drone_b}`}
            dense
            maxHeight={240}
          />
        </ModuleSection>
      ) : null}

      <ModuleSection title="Per drone">
        <DataTable data={result.drones} columns={droneColumns} getRowId={(d) => d.drone} caption="Per-drone statistics" dense maxHeight={320} />
      </ModuleSection>

      <IssueList issues={result.issues} />
    </div>
  );
}

const LAYERS = [
  { id: "shows:site", label: "Show box and audience buffer" },
  { id: "shows:audience-line", label: "Audience line", resultLayer: "audience_line" },
  { id: "shows:pads", label: "Pads too close", resultLayer: "pad_conflicts" },
  { id: "shows:paths", label: "Flight paths", resultLayer: "paths" },
  { id: "shows:drones", label: "Drones at current time" },
] as const;

type AnyFc = FeatureCollection<Geometry | null, Record<string, unknown>>;

function buildShowLayers(analysis: AnalysisInfo, ctx: ModuleLayerContext): ModuleLayerOutput {
  const result = analysis.result as ShowResult | null;
  if (!result) return { layers: [], legends: [] };
  const palette = mapPalette();
  const t = ctx.time;
  // Heights follow the map's 2D/3D rule in the layer factories (common.ts `withHeights`); the
  // positions stay real, so the drone tooltip reports the true height.
  const positions = positionsAt(result.frames, t);
  const groundAltitude = Number(result.frames.origin[2] ?? 0);
  const derivedBox = result.site.show_box_source === "derived";

  // Drones in a conflict active around now, plus the selected conflict.
  const highlighted = new Set<string>();
  for (const c of result.conflicts) {
    if (t >= c.start_s - 0.5 && t <= c.end_s + 0.5) {
      highlighted.add(c.drone_a);
      highlighted.add(c.drone_b);
    }
    if (ctx.resultHighlight === conflictKey(c)) {
      highlighted.add(c.drone_a);
      highlighted.add(c.drone_b);
    }
  }

  const layers = [
    geoJsonLayer({
      id: "shows:site",
      data: resultLayer(result.layers, "show_box") as AnyFc,
      fillColor: [palette.accent[0], palette.accent[1], palette.accent[2], 15],
      lineColor: palette.accent,
      lineWidth: 2,
      // A box derived from the tracks is drawn dashed: it is not a polygon from the file.
      dashed: derivedBox,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "shows:audience-line",
      data: resultLayer(result.layers, "audience_line") as AnyFc,
      filled: false,
      lineColor: palette.warn,
      lineWidth: 1.5,
      dashed: true,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "shows:pads",
      data: resultLayer(result.layers, "pad_conflicts") as AnyFc,
      filled: false,
      lineColor: palette.danger,
      lineWidth: 4,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
    ...hatchZoneLayer<Record<string, unknown>>({
      id: "shows:site/audience",
      data: resultLayer(result.layers, "audience_buffer") as AnyFc,
      getZone: () => "red",
      hatch: ctx.hatch,
      pickable: false,
      updateTriggers: [ctx.hatch],
    }),
    geoJsonLayer({
      id: "shows:paths",
      data: resultLayer(result.layers, "paths") as AnyFc,
      heightBase: groundAltitude, // tracks are above sea level; the origin stands on the ground
      filled: false,
      lineColor: [palette.muted[0], palette.muted[1], palette.muted[2], 70],
      lineWidth: 1,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
    ...droneCloudLayer({
      id: "shows:drones",
      origin: result.frames.origin,
      positions,
      ids: result.frames.drone_ids,
      radius: 4,
      color: (d) => (highlighted.has(d.id) ? palette.danger : palette.accentText),
      highlightIds: highlighted,
      // One label per pair: drones in conflict are about a metre apart and would overprint.
      labels: droneLabels(result.frames.drone_ids, positions, highlighted, ctx.units),
      updateTriggers: [t, ctx.resultHighlight, ctx.theme, ctx.units],
    }),
  ];

  const siteItems: LegendItem[] = [
    { label: "Drone", color: "var(--accent-text)", shape: "point" },
    { label: "Drone in a conflict (ring + label)", color: "var(--danger)", shape: "ring" },
    { label: derivedBox ? "Show box (derived from the tracks)" : "Show box", color: "var(--accent)", shape: derivedBox ? "dashed-line" : "line" },
    { label: "Audience buffer", color: "var(--danger)", zone: "red" },
  ];
  if (resultLayer(result.layers, "audience_line").features.length > 0) {
    const distance = result.site.audience_buffer?.distance_m ?? null;
    siteItems.push({ label: `Audience line${distance === null ? "" : ` (${formatLength(distance, ctx.units, { maximumFractionDigits: 0 })} from the box)`}`, color: "var(--warn)", shape: "dashed-line" });
  }
  if (resultLayer(result.layers, "pad_conflicts").features.length > 0) {
    siteItems.push({ label: "Pads closer than the spacing", color: "var(--danger)", shape: "line" });
  }
  const legends: LegendSpec[] = [
    {
      id: "shows:drones",
      title: "Drone show",
      kind: "categorical",
      layerId: "shows:drones",
      items: siteItems,
      note: `Time ${formatClock(t)}`,
    },
  ];
  return { layers, legends };
}

function describeShowPick(info: PickingInfo): string | null {
  if (!(info.layer?.id ?? "").startsWith("shows:drones") || !info.object) return null;
  const d = info.object as { id: string; position: [number, number, number] };
  return `${d.id}: ${formatHeight(d.position[2], currentUnits(), { maximumFractionDigits: 1 })} up`;
}

export const definition: ModuleDefinition = {
  ...MODULE_META.shows,
  Panel: ShowsPanel,
  Results: ShowsResults,
  layers: LAYERS,
  buildLayers: buildShowLayers,
  describePick: describeShowPick,
};

export default definition;
