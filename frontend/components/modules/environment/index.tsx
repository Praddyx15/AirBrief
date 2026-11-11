"use client";

/**
 * Environment module: sunrise, sunset and civil twilight with a daylight-profile check of the
 * planned window, the sun's elevation over the day, the WMM2025 magnetic field, a power-law wind
 * estimate at the planned height and proximity to power lines, substations and communication
 * masts. Everything is evidence (times, angles, field values, margins, distances), never a
 * go/no-go. The API computes in UTC; the browser's zone offset only labels the local times.
 */

import * as React from "react";
import type { PickingInfo } from "@deck.gl/core";
import { Sun } from "lucide-react";
import type { RGBA } from "@/lib/colors";
import {
  formatBearing,
  formatCoord,
  formatDate,
  formatDuration,
  formatHeight,
  formatLength,
  formatNumber,
  formatSpeed,
  lengthParts,
  localizeUnits,
  speedParts,
} from "@/lib/format";
import { DAYLIGHT_PROFILE_LABEL, DAYLIGHT_SEGMENT_LABEL, DAYLIGHT_STATUS_LABEL, EMI_KIND_LABEL } from "@/lib/labels";
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
import { useUi, useUnits } from "@/lib/store";
import {
  analysisResult,
  type AnalysisInfo,
  type DaylightProfile,
  type DaylightSegment,
  type DaylightSegmentStatus,
  type EmiItem,
  type EmiKind,
  type EnvironmentParams,
  type EnvironmentResult,
  type SunEvent,
  type WindAtHeight,
} from "@/lib/types";
import { geoJsonLayer, mapPalette, pointLayer, textLabelLayer, type MapPalette } from "@/components/map/layers/common";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { KpiGrid, KpiTile, type KpiTone } from "@/components/ui/kpi-tile";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAnalysisRunner } from "@/components/workspace/analysis-runner";
import { PlanStartHint } from "@/components/workspace/plan-start";
import {
  Advanced,
  Disclaimer,
  FormErrorSummary,
  InputHint,
  IssueList,
  ModuleSection,
  NumField,
  RunButton,
  SwitchField,
  SyntheticNote,
  ToneBadge,
  currentUnits,
  pointBounds,
  resultLayer,
  useFocusMap,
  useFormErrors,
} from "../shared";
import { ResetFormButton, paramBoolean, paramChoice, paramNumber, useFormFromAnalysis } from "../analysis-form";
import { BatchToggle, batchDisabledReason, withBatch } from "../batch";
import { batchFeatureCount, batchFromParams, batchParams } from "@/lib/batch";
import { DaylightRibbon } from "./daylight-ribbon";
import { DateTimeField } from "./fields";
import { SunElevationChart } from "./sun-chart";
import {
  BROWSER_ZONE,
  browserOffsetMinutes,
  browserZoneName,
  defaultPlan,
  isHhmm,
  isIsoDate,
  localWithDay,
  offsetChoices,
  offsetLabel,
} from "./time";

const PROFILES: readonly DaylightProfile[] = ["india_daylight", "faa_107", "custom"];

const PROFILE_HINT: Readonly<Record<DaylightProfile, string>> = {
  india_daylight: "Sunrise to sunset only, as Indian agricultural-spraying SOPs ask: civil twilight and night count as outside. Verify the rule that applies to you.",
  faa_107: "14 CFR 107.29: in civil twilight the aircraft needs anti-collision lighting visible for 3 statute miles; night is flagged.",
  custom: "Time with the sun below the elevation you choose counts as outside the profile.",
};

// same defaults as airbrief_core EnvironmentParams
const DEFAULTS = {
  windowOn: true,
  profile: "india_daylight" as DaylightProfile,
  minSun: 0,
  height: 120,
  windOn: false,
  referenceHeight: 10,
  shear: 1 / 7,
  emiOn: true,
  emiRadius: 300,
  sunMin: 30,
  sunMax: 60,
} as const;

/** Heights matching the usual ceilings; in imperial they show as whole feet but keep these exact metres. */
const HEIGHT_PRESETS: readonly number[] = [30, 60, 120];
const EMI_RADIUS_PRESETS: readonly number[] = [100, 300, 1000];

/**
 * Power-law shear exponents for near-neutral conditions (airbrief_core SHEAR_PRESETS): open water
 * 0.11 (Hsu, Meindl & Gilhousen 1994), open land 1/7 (Peterson & Hennessey 1978), suburbs 0.25 and
 * city 0.40 (Masters 2004, after Davenport 1960).
 */
const SHEAR_PRESETS = [
  { key: "open_water", label: "Open water", exponent: 0.11 },
  { key: "open_land", label: "Open land", exponent: 1 / 7 },
  { key: "suburbs", label: "Suburbs", exponent: 0.25 },
  { key: "city", label: "City", exponent: 0.4 },
] as const;

/** Illustrative rated wind limits (airbrief_core WIND_LIMIT_PRESETS): sub-250 g, mapping quadcopter, fixed-wing. */
const WIND_LIMIT_PRESETS: readonly number[] = [10.7, 12, 15];

const LINE_KINDS: ReadonlySet<EmiKind> = new Set(["power_line", "minor_line", "cable"]);

/** Most EMI labels drawn on the map (the table lists every item). */
const MAX_MAP_LABELS = 12;

function sameNumber(a: number | null, b: number): boolean {
  return a !== null && Math.abs(a - b) < 1e-6;
}

function EnvironmentPanel({ file, features, selectedFeatures, analysis }: ModulePanelProps) {
  const runner = useAnalysisRunner();
  const form = useFormErrors();
  const [initial] = React.useState(() => defaultPlan());
  const [date, setDate] = React.useState<string>(initial.date);
  const [windowOn, setWindowOn] = React.useState<boolean>(DEFAULTS.windowOn);
  const [start, setStart] = React.useState<string>(initial.start);
  const [end, setEnd] = React.useState<string>(initial.end);
  const [zone, setZone] = React.useState<string>(BROWSER_ZONE);
  const [profile, setProfile] = React.useState<DaylightProfile>(DEFAULTS.profile);
  const [minSun, setMinSun] = React.useState<number | null>(DEFAULTS.minSun);
  const [height, setHeight] = React.useState<number | null>(DEFAULTS.height);
  const [windOn, setWindOn] = React.useState<boolean>(DEFAULTS.windOn);
  const [wind, setWind] = React.useState<number | null>(null);
  const [gust, setGust] = React.useState<number | null>(null);
  const [referenceHeight, setReferenceHeight] = React.useState<number | null>(DEFAULTS.referenceHeight);
  const [shear, setShear] = React.useState<number | null>(DEFAULTS.shear);
  const [limit, setLimit] = React.useState<number | null>(null);
  const [emiOn, setEmiOn] = React.useState<boolean>(DEFAULTS.emiOn);
  const [emiRadius, setEmiRadius] = React.useState<number | null>(DEFAULTS.emiRadius);
  const [sunMin, setSunMin] = React.useState<number | null>(DEFAULTS.sunMin);
  const [sunMax, setSunMax] = React.useState<number | null>(DEFAULTS.sunMax);
  const [batch, setBatch] = React.useState(false);
  const [keyProperty, setKeyProperty] = React.useState<string | null>(null);

  /** Every field from stored parameters; `{}` (Reset) gives today, the next hour and the defaults. */
  const applyParams = (p: Readonly<Record<string, unknown>>) => {
    const fresh = Object.keys(p).length === 0;
    const now = defaultPlan();
    const day = typeof p.date === "string" && isIsoDate(p.date) ? p.date : now.date;
    const ws = typeof p.window_start === "string" && isHhmm(p.window_start) ? p.window_start : null;
    const we = typeof p.window_end === "string" && isHhmm(p.window_end) ? p.window_end : null;
    setDate(day);
    setWindowOn(fresh ? DEFAULTS.windowOn : ws !== null && we !== null);
    setStart(ws ?? now.start);
    setEnd(we ?? now.end);
    const offset = paramNumber(p, "utc_offset_min", null);
    setZone(offset === null || offset === browserOffsetMinutes(day, ws) ? BROWSER_ZONE : String(offset));
    setProfile(paramChoice(p, "daylight_profile", PROFILES, DEFAULTS.profile));
    setMinSun(paramNumber(p, "min_sun_elevation_deg", DEFAULTS.minSun));
    setHeight(paramNumber(p, "height_agl_m", DEFAULTS.height));
    const surface = paramNumber(p, "surface_wind_ms", null);
    setWindOn(surface !== null);
    setWind(surface);
    setGust(paramNumber(p, "gust_ms", null));
    setReferenceHeight(paramNumber(p, "reference_height_m", DEFAULTS.referenceHeight));
    setShear(paramNumber(p, "shear_exponent", DEFAULTS.shear));
    setLimit(paramNumber(p, "wind_limit_ms", null));
    setEmiOn(paramBoolean(p, "include_emi", DEFAULTS.emiOn));
    setEmiRadius(paramNumber(p, "emi_radius_m", DEFAULTS.emiRadius));
    setSunMin(paramNumber(p, "sun_min_deg", DEFAULTS.sunMin));
    setSunMax(paramNumber(p, "sun_max_deg", DEFAULTS.sunMax));
    const b = batchFromParams(p);
    setBatch(b.on);
    setKeyProperty(b.keyProperty);
  };
  const { markStarted, setFields, fieldsKey } = useFormFromAnalysis(analysis, applyParams);

  const browserOffset = browserOffsetMinutes(date, windowOn ? start : null);
  const offset = zone === BROWSER_ZONE ? browserOffset : Number(zone);
  const zoneName = React.useMemo(() => browserZoneName(), []);
  const choices = offsetChoices(zone === BROWSER_ZONE ? null : Number(zone));
  const year = isIsoDate(date) ? Number(date.slice(0, 4)) : null;
  const shearPreset = SHEAR_PRESETS.find((s) => sameNumber(shear, s.exponent))?.key ?? "";

  const ready = file?.status === "COMPLETED";
  const batchCount = batchFeatureCount(features, selectedFeatures);
  const disabledReason = !file
    ? "Draw, pin or open a dataset first: the evidence is computed for its features."
    : !ready
      ? "Wait for the dataset to finish processing."
      : batchDisabledReason("environment", batch, batchCount);

  const onRun = form.guard(() => {
    const params: Partial<EnvironmentParams> = {
      date,
      utc_offset_min: offset,
      daylight_profile: profile,
      height_agl_m: height ?? DEFAULTS.height,
      include_emi: emiOn,
      sun_min_deg: sunMin ?? DEFAULTS.sunMin,
      sun_max_deg: sunMax ?? DEFAULTS.sunMax,
      ...batchParams(batch, keyProperty),
    };
    if (windowOn) {
      params.window_start = start;
      params.window_end = end;
    }
    if (profile === "custom") params.min_sun_elevation_deg = minSun ?? DEFAULTS.minSun;
    if (windOn && wind !== null) {
      params.surface_wind_ms = wind;
      params.gust_ms = gust;
      params.reference_height_m = referenceHeight ?? DEFAULTS.referenceHeight;
      params.shear_exponent = shear ?? DEFAULTS.shear;
      params.wind_limit_ms = limit;
    }
    if (emiOn) params.emi_radius_m = emiRadius ?? DEFAULTS.emiRadius;
    void runner.run("environment", params).then((info) => {
      if (info) markStarted(info.id);
    });
  });

  return (
    <form className="space-y-4 text-sm" onSubmit={(e) => e.preventDefault()} aria-label="Environment evidence">
      {!file ? (
        <PlanStartHint purpose="Sun, magnetic field and wind are given at the pin or the shape's centre, power lines and masts around it." />
      ) : (
        <InputHint>
          {selectedFeatures.length > 0
            ? `Evidence for the ${selectedFeatures.length} selected feature${selectedFeatures.length === 1 ? "" : "s"}: sun, magnetic field and wind at their centre, power lines and masts around the whole geometry.`
            : "Evidence for every feature of the dataset: sun, magnetic field and wind at their centre, power lines and masts around the whole geometry. Select features to analyse only those."}
        </InputHint>
      )}
      <FormErrorSummary form={form} />

      <ModuleSection title="When">
        <React.Fragment key={fieldsKey}>
          <DateTimeField
            label="Date (local)"
            type="date"
            value={date}
            onChange={setDate}
            validate={(v) => (!v ? "Enter a date." : isIsoDate(v) ? null : "Enter a real date.")}
            hint={
              year !== null && (year < 2025 || year > 2029)
                ? "WMM2025 covers 2025 to 2029: the magnetic field for this year is extrapolated."
                : undefined
            }
            form={form}
          />
          <SwitchField
            label="Planned window"
            checked={windowOn}
            onChange={setWindowOn}
            hint="Check a planned flight window against the daylight profile."
          />
          {windowOn ? (
            <div className="grid grid-cols-2 gap-3">
              <DateTimeField
                label="Start (local)"
                type="time"
                value={start}
                onChange={setStart}
                validate={(v) => (isHhmm(v) ? null : "Enter a time as HH:MM.")}
                form={form}
              />
              <DateTimeField
                label="End (local)"
                type="time"
                value={end}
                onChange={setEnd}
                validate={(v) => (!isHhmm(v) ? "Enter a time as HH:MM." : v === start ? "The end must differ from the start." : null)}
                hint={isHhmm(start) && isHhmm(end) && end < start ? "Ends on the next day." : undefined}
                form={form}
              />
            </div>
          ) : null}
        </React.Fragment>
        <p className="text-xs text-fg-muted">
          Times are local, {offsetLabel(offset)}
          {zone === BROWSER_ZONE ? ` (your browser's zone${zoneName ? `, ${zoneName}` : ""})` : " (chosen under Advanced)"}. Everything is
          computed in UTC.
        </p>
      </ModuleSection>

      <ModuleSection title="Daylight profile">
        <div className="space-y-1.5">
          <Label htmlFor="environment-profile">Daylight profile</Label>
          <Select value={profile} onValueChange={(v) => setProfile(PROFILES.includes(v as DaylightProfile) ? (v as DaylightProfile) : DEFAULTS.profile)}>
            <SelectTrigger id="environment-profile" aria-describedby="environment-profile-hint">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PROFILES.map((p) => (
                <SelectItem key={p} value={p}>
                  {DAYLIGHT_PROFILE_LABEL[p]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p id="environment-profile-hint" className="text-xs text-fg-muted">
            {PROFILE_HINT[profile]}
          </p>
        </div>
        {profile === "custom" ? (
          <NumField
            key={fieldsKey}
            label="Minimum sun elevation"
            unit="°"
            value={minSun}
            onChange={setMinSun}
            min={-18}
            max={60}
            step={1}
            hint="Apparent elevation of the sun's centre; 0° is the horizon, −6° the end of civil twilight."
            form={form}
          />
        ) : null}
      </ModuleSection>

      <ModuleSection title="Planned height">
        <NumField
          key={fieldsKey}
          label="Height above ground"
          unit="m"
          value={height}
          onChange={setHeight}
          min={1}
          max={1500}
          hint="For the wind estimate and the magnetic field (terrain + this height)."
          presets={{ metric: HEIGHT_PRESETS, label: "Height presets" }}
          form={form}
        />
      </ModuleSection>

      <ModuleSection title="Wind">
        <SwitchField
          label="Wind at height"
          checked={windOn}
          onChange={setWindOn}
          hint="Estimate the wind at the planned height from a surface reading (power law, not a forecast)."
        />
        {windOn ? (
          <div key={fieldsKey} className="space-y-3 rounded border border-border p-3">
            <NumField label="Surface wind" unit="m/s" value={wind} onChange={setWind} min={0} max={75} hint="Mean wind at the reference height, e.g. from a local reading or forecast." form={form} />
            <NumField
              label="Gust"
              unit="m/s"
              value={gust}
              onChange={setGust}
              min={0}
              max={100}
              optional
              validate={(v) => (v !== null && wind !== null && v < wind ? "Must be at least the surface wind." : null)}
              form={form}
            />
            <NumField label="Reference height" unit="m" value={referenceHeight} onChange={setReferenceHeight} min={1} max={500} hint="Height of the surface reading (10 m for standard weather stations)." form={form} />
            <div className="space-y-1.5">
              <ToggleGroup
                type="single"
                aria-label="Terrain presets"
                value={shearPreset}
                onValueChange={(v) => {
                  const picked = SHEAR_PRESETS.find((s) => s.key === v);
                  if (picked) setShear(picked.exponent);
                }}
                className="flex-wrap"
              >
                {SHEAR_PRESETS.map((s) => (
                  <ToggleGroupItem key={s.key} value={s.key}>
                    {s.label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
              <NumField
                label="Shear exponent α"
                value={shear}
                onChange={setShear}
                min={0}
                max={0.6}
                step={0.01}
                decimals={3}
                hint="v(z) = v_ref·(z/z_ref)^α. Open water 0.11 (Hsu et al. 1994), open land 1/7 (Peterson & Hennessey 1978), suburbs 0.25, city 0.40 (Masters 2004). Stable nights can shear more."
                form={form}
              />
            </div>
            <NumField
              label="Rated wind limit"
              unit="m/s"
              value={limit}
              onChange={setLimit}
              min={0.1}
              max={100}
              optional
              presets={{ metric: WIND_LIMIT_PRESETS, label: "Rated wind limit presets" }}
              hint="Illustrative presets: 10.7 m/s sub-250 g, 12 m/s mapping quadcopter, 15 m/s fixed-wing mapper. Use your aircraft manual's value."
              form={form}
            />
          </div>
        ) : null}
      </ModuleSection>

      <ModuleSection title="Interference">
        <SwitchField
          label="Power lines and masts"
          checked={emiOn}
          onChange={setEmiOn}
          hint="Power lines, substations and communication masts near the geometry: proximity evidence, not an interference prediction."
        />
        {emiOn ? (
          <NumField
            key={fieldsKey}
            label="Search distance"
            unit="m"
            value={emiRadius}
            onChange={setEmiRadius}
            min={10}
            max={5000}
            presets={{ metric: EMI_RADIUS_PRESETS, label: "Search distance presets" }}
            form={form}
          />
        ) : null}
        {file ? (
          <BatchToggle
            kind="environment"
            on={batch}
            onChange={setBatch}
            keyProperty={keyProperty}
            onKeyProperty={setKeyProperty}
            features={features}
            count={batchCount}
            selection={selectedFeatures.length > 0}
          />
        ) : null}
      </ModuleSection>

      <Advanced form={form}>
        <div className="space-y-1.5">
          <Label htmlFor="environment-zone">Time zone of the times</Label>
          <Select value={zone} onValueChange={setZone}>
            <SelectTrigger id="environment-zone" aria-describedby="environment-zone-hint">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={BROWSER_ZONE}>
                Browser zone ({offsetLabel(browserOffset)}
                {zoneName ? `, ${zoneName}` : ""})
              </SelectItem>
              {choices.map((m) => (
                <SelectItem key={m} value={String(m)}>
                  {offsetLabel(m)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p id="environment-zone-hint" className="text-xs text-fg-muted">
            Pick the operation&apos;s offset when you plan from another zone. It only labels the times.
          </p>
        </div>
        <React.Fragment key={fieldsKey}>
          <NumField
            label="Sun window from"
            unit="°"
            value={sunMin}
            onChange={setSunMin}
            min={0}
            max={89}
            step={1}
            hint="Photogrammetry sun window: when the sun is between these elevations."
            form={form}
          />
          <NumField
            label="Sun window to"
            unit="°"
            value={sunMax}
            onChange={setSunMax}
            min={1}
            max={90}
            step={1}
            validate={(v) => (v !== null && sunMin !== null && v <= sunMin ? "Must be above the lower elevation." : null)}
            form={form}
          />
        </React.Fragment>
      </Advanced>

      {runner.error ? (
        <ErrorState compact error={runner.error} title="Environment check could not start" onRetry={disabledReason ? undefined : onRun} retrying={runner.isPending} />
      ) : null}
      <RunButton onRun={onRun} pending={runner.isPending} disabledReason={disabledReason} label="Check environment" />
      <ResetFormButton onReset={() => setFields({})} />
    </form>
  );
}

function eventText(event: SunEvent | null): string {
  return event ? localWithDay(event.local, event.day_offset) : "None";
}

function degreesText(value: number, decimals = 1): string {
  return `${formatNumber(value, { decimals })}°`;
}

function emiFeatureName(item: EmiItem): string {
  const head = item.voltage_kv !== null ? `${formatNumber(item.voltage_kv)} kV ${EMI_KIND_LABEL[item.kind].toLowerCase()}` : EMI_KIND_LABEL[item.kind];
  return item.name ? `${head} · ${item.name}` : head;
}

function emiDistanceText(item: EmiItem, units: ReturnType<typeof useUnits>): string {
  if (!item.inside) return formatLength(item.distance_m, units);
  return LINE_KINDS.has(item.kind) ? "Crosses" : "Inside";
}

const STATUS_TONE: Readonly<Record<DaylightSegmentStatus, "neutral" | "info" | "warn">> = {
  allowed: "neutral",
  lighting: "info",
  outside: "warn",
};

/** Mean wind and gust against the rated limit, as bars on one scale (with a text equivalent). */
function WindMargin({ wind, units }: { wind: WindAtHeight; units: ReturnType<typeof useUnits> }) {
  const top = Math.max(wind.wind_limit_ms ?? 0, wind.gust_at_height_ms ?? 0, wind.wind_at_height_ms, 0.1) * 1.15;
  const pct = (v: number) => `${Math.min(100, (v / top) * 100)}%`;
  const statement = localizeUnits(wind.statement, units);
  return (
    <div className="space-y-1.5">
      <div className="relative h-6 w-full overflow-hidden rounded border border-border bg-surface" role="img" aria-label={statement}>
        {wind.gust_at_height_ms !== null ? (
          <div
            className="absolute inset-y-0 left-0"
            style={{
              width: pct(wind.gust_at_height_ms),
              backgroundImage: "repeating-linear-gradient(135deg, rgb(var(--accent-rgb) / 0.5) 0 3px, transparent 3px 6px)",
            }}
          />
        ) : null}
        <div className="absolute left-0 top-1 bottom-1 bg-accent/70" style={{ width: pct(wind.wind_at_height_ms) }} />
        {wind.wind_limit_ms !== null ? (
          <div className="absolute inset-y-0 border-l-2 border-fg" style={{ left: pct(wind.wind_limit_ms) }} />
        ) : null}
      </div>
      <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-muted" aria-label="Wind bar legend">
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-3 w-5 rounded-sm bg-accent/70" />
          Mean {formatSpeed(wind.wind_at_height_ms, units)}
        </li>
        {wind.gust_at_height_ms !== null ? (
          <li className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-3 w-5 rounded-sm border border-border"
              style={{ backgroundImage: "repeating-linear-gradient(135deg, rgb(var(--accent-rgb) / 0.5) 0 3px, transparent 3px 6px)" }}
            />
            Gusts {formatSpeed(wind.gust_at_height_ms, units)}
          </li>
        ) : null}
        {wind.wind_limit_ms !== null ? (
          <li className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-3 w-0 border-l-2 border-fg" />
            Rated limit {formatSpeed(wind.wind_limit_ms, units)}
          </li>
        ) : null}
      </ul>
      <p className="text-xs text-fg-muted">
        {formatSpeed(wind.surface_wind_ms, units)} at {formatHeight(wind.reference_height_m, units)} × ({formatHeight(wind.height_agl_m, units)} ÷{" "}
        {formatHeight(wind.reference_height_m, units)})^{formatNumber(wind.shear_exponent, { maximumFractionDigits: 3 })} = ×
        {formatNumber(wind.factor, { maximumFractionDigits: 3 })}. An estimate for near-neutral air, not a forecast or a clearance to fly.
      </p>
    </div>
  );
}

function MagneticFacts({ result, units }: { result: EnvironmentResult; units: ReturnType<typeof useUnits> }) {
  const m = result.magnetic;
  const items: { term: string; value: string }[] = [
    { term: "Declination", value: `${degreesText(m.declination_deg, 2)} (${formatNumber(m.declination_change_deg_per_year * 60, { signed: true, maximumFractionDigits: 1 })}′ per year)` },
    { term: "Inclination", value: degreesText(m.inclination_deg, 2) },
    { term: "Horizontal intensity", value: `${formatNumber(m.horizontal_nt, { maximumFractionDigits: 0 })} nT` },
    { term: "Total intensity", value: `${formatNumber(m.total_nt, { maximumFractionDigits: 0 })} nT` },
  ];
  if (m.grid_variation_deg !== null) items.push({ term: "Grid variation", value: degreesText(m.grid_variation_deg, 2) });
  items.push(
    { term: "Model", value: `${m.model}, valid to ${formatDate(m.valid_until, { withTime: false, utc: true })}` },
    { term: "Evaluated at", value: `${formatHeight(m.altitude_m, units)} above the ellipsoid, ${formatNumber(m.decimal_year, { decimals: 2 })}` },
  );
  return (
    <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-2">
      {items.map((i) => (
        <div key={i.term} className="min-w-0">
          <dt className="text-xs text-fg-muted">{i.term}</dt>
          <dd className="num break-words">{i.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function EnvironmentResults({ analysis }: ModuleResultsProps) {
  const units = useUnits();
  const focus = useFocusMap();
  const setHighlight = useUi((s) => s.setResultHighlight);
  const [selectedEmi, setSelectedEmi] = React.useState<string | null>(null);
  const analysisId = analysis?.id ?? null;
  React.useEffect(() => {
    // A different analysis: forget the row picked in the previous one.
    setSelectedEmi(null);
    setHighlight(null);
  }, [analysisId, setHighlight]);
  const result = analysisResult(analysis, "environment");

  const emiColumns = React.useMemo<DataTableColumn<EmiItem>[]>(
    () => [
      { id: "feature", header: "Feature", rowHeader: true, cell: (i) => emiFeatureName(i), sortValue: (i) => EMI_KIND_LABEL[i.kind] },
      {
        id: "voltage",
        header: "Voltage",
        numeric: true,
        cell: (i) => (i.voltage_kv !== null ? `${formatNumber(i.voltage_kv)} kV` : i.source === "power" ? "Not tagged" : "—"),
        sortValue: (i) => i.voltage_kv,
      },
      { id: "distance", header: "Distance", numeric: true, cell: (i) => emiDistanceText(i, units), sortValue: (i) => i.distance_m },
      { id: "height", header: "Height", numeric: true, cell: (i) => (i.height_m !== null ? formatHeight(i.height_m, units) : "—"), sortValue: (i) => i.height_m },
      { id: "source", header: "Source", cell: (i) => (i.source === "power" ? "Power layer" : "Obstacle layer"), sortValue: (i) => i.source },
    ],
    [units],
  );

  const windowColumns = React.useMemo<DataTableColumn<DaylightSegment>[]>(
    () => [
      { id: "kind", header: "Sun", rowHeader: true, cell: (s) => DAYLIGHT_SEGMENT_LABEL[s.kind], sortValue: (s) => s.start_minute },
      {
        id: "status",
        header: "Profile",
        cell: (s) => <ToneBadge tone={STATUS_TONE[s.status]} label={DAYLIGHT_STATUS_LABEL[s.status]} />,
        sortValue: (s) => s.status,
      },
      { id: "start", header: "Start", numeric: true, cell: (s) => s.start_local, sortValue: (s) => s.start_minute },
      { id: "end", header: "End", numeric: true, cell: (s) => s.end_local, sortValue: (s) => s.end_minute },
      { id: "duration", header: "Duration", numeric: true, cell: (s) => formatDuration(s.minutes * 60), sortValue: (s) => s.minutes },
    ],
    [],
  );

  if (!analysis) return null;
  if (!result) {
    return analysis.status === "COMPLETED" ? (
      <EmptyState compact icon={<Sun />} title="No result" description="The analysis finished without a result." />
    ) : null;
  }

  const zoneLabel = result.utc_offset_label;
  const dateLabel = formatDate(result.date, { withTime: false, utc: true });
  const sun = result.sun;
  const daylight = result.daylight;
  const magnetic = result.magnetic;
  const wind = result.wind;
  const nearestLine = result.emi.find((i) => i.source === "power" && LINE_KINDS.has(i.kind)) ?? null;
  const powerNotAssessed = result.issues.some((i) => i.code === "power_not_assessed");
  const target = result.geometry_type.includes("Polygon") ? "area" : result.geometry_type.includes("Point") ? "point" : "route";

  const polarText = sun.polar_day ? "Polar day: the sun stays up all day" : sun.polar_night ? "Polar night: the sun stays down all day" : null;
  // Near the polar circles a date can have a sunrise without a sunset (or the reverse).
  const sunriseExplanation =
    polarText ??
    (sun.sunrise
      ? sun.sunrise.azimuth_deg != null
        ? `Azimuth ${formatBearing(sun.sunrise.azimuth_deg)}; civil dawn ${eventText(sun.civil_dawn)}`
        : undefined
      : "The sun does not rise in the 12 h before solar noon");
  const sunsetExplanation =
    polarText ??
    (sun.sunset
      ? sun.sunset.azimuth_deg != null
        ? `Azimuth ${formatBearing(sun.sunset.azimuth_deg)}`
        : undefined
      : "The sun does not set in the 12 h after solar noon");
  const duskExplanation = sun.civil_dusk
    ? "Sun's centre 6° below the horizon"
    : sun.polar_night
      ? "The sun stays more than 6° below the horizon"
      : "The sun stays above −6°: civil twilight lasts all night";

  let magneticTone: KpiTone = "neutral";
  let magneticToneLabel: string | undefined;
  if (magnetic.blackout) {
    magneticTone = "danger";
    magneticToneLabel = "WMM blackout zone (H below 2000 nT)";
  } else if (magnetic.caution) {
    magneticTone = "warn";
    magneticToneLabel = "WMM caution zone (H below 6000 nT)";
  } else if (!magnetic.within_validity) {
    magneticTone = "warn";
    magneticToneLabel = "Outside the model's validity";
  }

  let windTile: { value: ReturnType<typeof speedParts> | string; explanation: string; tone: KpiTone; toneLabel?: string };
  if (!wind) {
    windTile = { value: "Not given", explanation: "Turn on Wind at height in the form to estimate it.", tone: "neutral" };
  } else {
    const parts: string[] = [];
    if (wind.margin_ms !== null && wind.wind_limit_ms !== null) {
      parts.push(
        `${formatSpeed(Math.abs(wind.margin_ms), units)} ${wind.margin_ms >= 0 ? "below" : "above"} the ${formatSpeed(wind.wind_limit_ms, units)} rated limit`,
      );
    }
    if (wind.gust_at_height_ms !== null) parts.push(`gusts ${formatSpeed(wind.gust_at_height_ms, units)}`);
    if (parts.length === 0) parts.push(`From ${formatSpeed(wind.surface_wind_ms, units)} at ${formatHeight(wind.reference_height_m, units)}`);
    const over = (wind.margin_ms ?? 0) < 0;
    const gustOver = (wind.gust_margin_ms ?? 0) < 0;
    windTile = {
      value: speedParts(wind.wind_at_height_ms, units),
      explanation: parts.join("; "),
      tone: over || gustOver ? "warn" : "neutral",
      toneLabel: over ? "Estimate above the rated limit" : gustOver ? "Gusts above the rated limit" : undefined,
    };
  }

  let lineTile: { value: ReturnType<typeof lengthParts> | string; explanation: string; tone: KpiTone; toneLabel?: string };
  if (!result.emi_assessed || powerNotAssessed) {
    lineTile = {
      value: "Not assessed",
      explanation: !result.emi_assessed
        ? result.emi_radius_m === null
          ? "Interference proximity was switched off"
          : "No data pack covers this area"
        : "The data pack has no power layer",
      tone: "neutral",
    };
  } else if (!nearestLine) {
    lineTile = { value: "None", explanation: `No power line within ${formatLength(result.emi_radius_m, units)}`, tone: "neutral" };
  } else {
    lineTile = {
      value: nearestLine.inside ? "Crosses" : lengthParts(nearestLine.distance_m, units),
      explanation: emiFeatureName(nearestLine),
      tone: nearestLine.inside ? "warn" : "neutral",
      toneLabel: nearestLine.inside ? `Crosses or touches the ${target}` : undefined,
    };
  }

  const outside = daylight.minutes_outside ?? 0;
  const lighting = daylight.minutes_lighting ?? 0;
  const facts = result.facts.map((f) => localizeUnits(f, units));

  return (
    <div className="space-y-4">
      <SyntheticNote synthetic={analysis.datapack_synthetic} />
      <p className="text-xs text-fg-muted">
        {dateLabel} at {formatCoord(result.point[0], result.point[1])}; local times are {zoneLabel}. Evidence for planning, not a go/no-go.
      </p>

      <KpiGrid label="Environment summary">
        <KpiTile label="Sunrise" value={eventText(sun.sunrise)} unit={sun.sunrise ? zoneLabel : undefined} explanation={sunriseExplanation} />
        <KpiTile label="Sunset" value={eventText(sun.sunset)} unit={sun.sunset ? zoneLabel : undefined} explanation={sunsetExplanation} />
        <KpiTile label="Civil dusk" value={eventText(sun.civil_dusk)} unit={sun.civil_dusk ? zoneLabel : undefined} explanation={duskExplanation} />
        {daylight.window ? (
          <KpiTile
            label="Planned window"
            value={formatNumber(outside, { maximumFractionDigits: 0 })}
            unit="min outside"
            tone={outside >= 0.5 ? "warn" : lighting >= 0.5 ? "info" : "neutral"}
            toneLabel={outside >= 0.5 ? "Outside the profile" : lighting >= 0.5 ? `Lighting required for ${formatNumber(lighting, { maximumFractionDigits: 0 })} min` : undefined}
            explanation={`${daylight.window.start_local}–${daylight.window.end_local}, ${DAYLIGHT_PROFILE_LABEL[daylight.profile].toLowerCase()}`}
          />
        ) : null}
        <KpiTile
          label="Magnetic declination"
          value={degreesText(magnetic.declination_deg)}
          tone={magneticTone}
          toneLabel={magneticToneLabel}
          explanation={`${Math.abs(magnetic.declination_deg) < 0.05 ? "Along" : magnetic.declination_deg > 0 ? "East of" : "West of"} true north; inclination ${degreesText(magnetic.inclination_deg)} (${magnetic.model})`}
        />
        <KpiTile label={`Wind at ${formatHeight(result.height_agl_m, units)}`} value={windTile.value} explanation={windTile.explanation} tone={windTile.tone} toneLabel={windTile.toneLabel} />
        <KpiTile label="Nearest power line" value={lineTile.value} explanation={lineTile.explanation} tone={lineTile.tone} toneLabel={lineTile.toneLabel} />
        <KpiTile label="Highest sun" value={degreesText(sun.max_elevation_deg)} explanation={`Solar noon ${eventText(sun.solar_noon)} ${zoneLabel}`} />
      </KpiGrid>

      {facts.length > 0 ? (
        <ModuleSection title="Evidence">
          <ul className="list-disc space-y-0.5 pl-5 text-sm">
            {facts.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </ModuleSection>
      ) : null}

      <DaylightRibbon daylight={daylight} offsetLabel={zoneLabel} dateLabel={dateLabel} />

      {daylight.window ? (
        <ModuleSection title={`Planned window (${daylight.window_segments.length})`}>
          <p className="text-sm">{daylight.statement}</p>
          <DataTable
            data={daylight.window_segments}
            columns={windowColumns}
            getRowId={(s) => `${s.start}-${s.kind}`}
            caption={`Planned window ${daylight.window.start_local}–${daylight.window.end_local} by sun state, under the profile ${DAYLIGHT_PROFILE_LABEL[daylight.profile]}`}
            dense
          />
        </ModuleSection>
      ) : null}

      <SunElevationChart
        track={sun.track}
        window={daylight.window}
        sunWindow={{ min: result.sun_window.min_deg, max: result.sun_window.max_deg }}
        offsetLabel={zoneLabel}
      />

      <ModuleSection title={`Magnetic field (${magnetic.model})`}>
        <MagneticFacts result={result} units={units} />
      </ModuleSection>

      {wind ? (
        <ModuleSection title="Wind at height">
          <WindMargin wind={wind} units={units} />
        </ModuleSection>
      ) : null}

      <ModuleSection title={`Power lines and masts (${result.emi.length})`}>
        <p className="text-xs text-fg-muted">
          {result.emi_radius_m !== null
            ? `Within ${formatLength(result.emi_radius_m, units)} of the ${target}, nearest first: proximity evidence, not an interference prediction.`
            : "Interference proximity was switched off for this run."}
        </p>
        <DataTable
          data={result.emi}
          columns={emiColumns}
          getRowId={(i) => `emi-${i.id}`}
          caption="Power lines, substations and masts near the analysed geometry"
          onRowClick={(i) => {
            setSelectedEmi(`emi-${i.id}`);
            setHighlight(`emi-${i.id}`);
            focus(pointBounds(i.lon, i.lat, 250));
          }}
          onRowHover={(i) => setHighlight(i ? `emi-${i.id}` : selectedEmi)}
          selectedRowIds={selectedEmi ? [selectedEmi] : []}
          getRowLabel={(i) => `Show ${emiFeatureName(i)} on the map`}
          dense
          emptyState={
            <EmptyState
              compact
              title={result.emi_assessed && !powerNotAssessed ? "Nothing nearby" : "Not assessed"}
              description={
                result.emi_assessed
                  ? `No power line, substation or mast within ${formatLength(result.emi_radius_m, units)} in the data pack.`
                  : "No data pack covers this area, or the check was switched off."
              }
            />
          }
        />
        {result.emi_truncated ? <p className="text-xs text-fg-muted">Only the nearest {result.emi.length} are listed.</p> : null}
      </ModuleSection>

      <ModuleSection title="Photogrammetry sun window">
        <p className="text-sm">
          {result.sun_window.segments.length > 0
            ? `Sun between ${formatNumber(result.sun_window.min_deg)}° and ${formatNumber(result.sun_window.max_deg)}°: ${result.sun_window.segments
                .map((s) => `${s.start_local}–${s.end_local}`)
                .join(" and ")} (${formatDuration(result.sun_window.total_minutes * 60)} in all, ${zoneLabel}).`
            : `The sun does not reach ${formatNumber(result.sun_window.min_deg)}° on this date.`}
        </p>
      </ModuleSection>

      <IssueList issues={result.issues} />
      <ModuleSection title="Method">
        <p className="text-sm">{result.method}</p>
      </ModuleSection>
      <Disclaimer text={result.disclaimer} />
    </div>
  );
}

const LAYERS = [
  { id: "environment:input", label: "Analysed area", resultLayer: "input" },
  { id: "environment:emi", label: "Power lines and masts", resultLayer: "emi" },
  { id: "environment:search", label: "Interference search distance", resultLayer: "emi_search" },
  { id: "environment:point", label: "Evaluation point", resultLayer: "point" },
] as const;

interface EmiProps {
  kind?: EmiKind;
  name?: string | null;
  voltage_kv?: number | null;
  distance_m?: number;
  label?: string;
}

function emiColor(kind: EmiKind | undefined, palette: MapPalette): RGBA {
  switch (kind) {
    case "power_line":
      return palette.danger;
    case "minor_line":
    case "cable":
    case "substation":
    case "plant":
      return palette.warn;
    case "comm_tower":
    case "mast":
      return palette.accent;
    default:
      return palette.muted;
  }
}

/** Legend rows for the kinds present (shape + colour + label, never colour alone). */
function emiLegendItems(kinds: ReadonlySet<EmiKind>): LegendItem[] {
  const items: LegendItem[] = [];
  if (kinds.has("power_line")) items.push({ label: "Power line", color: "var(--danger)", shape: "line" });
  if (kinds.has("minor_line") || kinds.has("cable")) items.push({ label: "Minor line or cable", color: "var(--warn)", shape: "line" });
  if (kinds.has("substation") || kinds.has("plant")) items.push({ label: "Substation or plant", color: "var(--warn)", shape: "fill" });
  if (kinds.has("comm_tower") || kinds.has("mast")) items.push({ label: "Communication tower or mast", color: "var(--accent)", shape: "point" });
  if (kinds.has("pylon")) items.push({ label: "Pylon", color: "var(--text-muted)", shape: "point" });
  return items;
}

function featureId(feature: unknown): string {
  const id = (feature as { id?: unknown } | null)?.id;
  return id === undefined || id === null ? "" : String(id);
}

function buildEnvironmentLayers(analysis: AnalysisInfo, ctx: ModuleLayerContext): ModuleLayerOutput {
  const result = analysis.result as EnvironmentResult | null;
  if (!result) return { layers: [], legends: [] };
  const palette = mapPalette();
  const layers = [];
  const legends: LegendSpec[] = [];
  const highlight = ctx.resultHighlight;
  const isHighlighted = (feature: unknown) => highlight !== null && highlight === `emi-${featureId(feature)}`;

  const search = resultLayer(result.layers, "emi_search");
  if (search.features.length > 0) {
    layers.push(
      geoJsonLayer({
        id: "environment:search",
        data: search,
        filled: false,
        lineColor: palette.muted,
        lineWidth: 1.5,
        dashed: true,
        pickable: false,
        updateTriggers: [ctx.theme],
      }),
    );
  }

  layers.push(
    geoJsonLayer({
      id: "environment:input",
      data: resultLayer(result.layers, "input"),
      fillColor: [palette.accent[0], palette.accent[1], palette.accent[2], 25],
      lineColor: palette.accent,
      lineWidth: 2,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
  );

  const emi = resultLayer(result.layers, "emi");
  if (emi.features.length > 0) {
    layers.push(
      geoJsonLayer<EmiProps>({
        id: "environment:emi",
        data: emi as unknown as Parameters<typeof geoJsonLayer<EmiProps>>[0]["data"],
        fillColor: (p) => {
          const c = emiColor(p.kind, palette);
          return [c[0], c[1], c[2], p.kind === "comm_tower" || p.kind === "mast" || p.kind === "pylon" ? 230 : 70];
        },
        lineColor: (p, f) => (isHighlighted(f) ? palette.text : emiColor(p.kind, palette)),
        lineWidth: (p, f) => (isHighlighted(f) ? 6 : p.kind && LINE_KINDS.has(p.kind) ? 3 : 1.5),
        pointRadius: (_p, f) => (isHighlighted(f) ? 9 : 6),
        updateTriggers: [ctx.theme, highlight],
      }),
    );
    const labelled = result.emi.slice(0, MAX_MAP_LABELS);
    const labelById = new Map(emi.features.map((f) => [featureId(f), (f.properties as EmiProps | null)?.label ?? null]));
    layers.push(
      textLabelLayer<EmiItem>({
        id: "environment:emi/labels",
        data: labelled,
        getPosition: (i) => [i.lon, i.lat],
        getText: (i) => localizeUnits(labelById.get(i.id) ?? emiFeatureName(i), ctx.units),
        pixelOffset: [0, -16],
        size: 11,
        updateTriggers: [ctx.units, ctx.theme],
      }),
    );
  }
  const kinds = new Set(result.emi.map((i) => i.kind));
  if (kinds.size > 0 || search.features.length > 0) {
    const items = emiLegendItems(kinds);
    if (result.emi_radius_m !== null) {
      items.push({ label: `Search distance ${formatLength(result.emi_radius_m, ctx.units)}`, color: "var(--text-muted)", shape: "dashed-line" });
    }
    legends.push({
      id: "environment:emi",
      title: "Power lines and masts",
      kind: "categorical",
      layerId: "environment:emi",
      items,
      note: "Proximity evidence, not an interference prediction.",
    });
  }

  const [lon, lat] = result.point;
  layers.push(
    pointLayer<{ position: [number, number] }>({
      id: "environment:point",
      data: [{ position: [lon, lat] }],
      getPosition: (d) => d.position,
      radius: 6,
      fillColor: palette.bg,
      lineColor: palette.accent,
      lineWidth: 3,
      updateTriggers: [ctx.theme],
    }),
    textLabelLayer<{ position: [number, number] }>({
      id: "environment:point/label",
      data: [{ position: [lon, lat] }],
      getPosition: (d) => d.position,
      getText: () => "Sun and magnetic field evaluated here",
      pixelOffset: [0, 18],
      size: 11,
      updateTriggers: [ctx.theme],
    }),
  );

  return { layers, legends };
}

function describeEnvironmentPick(info: PickingInfo): string | null {
  const id = info.layer?.id ?? "";
  const obj = info.object as Record<string, unknown> | null;
  if (!obj) return null;
  if (id.startsWith("environment:emi")) {
    const p = (obj.properties ?? {}) as EmiProps;
    const label = p.label ? localizeUnits(p.label, currentUnits()) : null;
    return [label, p.name].filter(Boolean).join(" · ") || null;
  }
  if (id.startsWith("environment:point")) {
    const position = (obj as { position?: [number, number] }).position;
    return position ? `Sun, magnetic field and wind evaluated at ${formatCoord(position[0], position[1])}` : null;
  }
  return null;
}

export const definition: ModuleDefinition = withBatch({
  ...MODULE_META.environment,
  Panel: EnvironmentPanel,
  Results: EnvironmentResults,
  layers: LAYERS,
  buildLayers: buildEnvironmentLayers,
  describePick: describeEnvironmentPick,
});

export default definition;
