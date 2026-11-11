"use client";

/**
 * Pieces shared by the module panels and results. Number fields take and give metric values,
 * whatever unit system they show.
 */

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { useMutationState, useQueries } from "@tanstack/react-query";
import { ChevronDown, Crosshair, Info, Loader2, Mountain, PlaneTakeoff, Play, Table2 } from "lucide-react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DEMO_MODE } from "@/lib/api";
import { VERDICT_LABEL, toneForSeverity } from "@/lib/colors";
import {
  formatCoord,
  formatHeight,
  formatLength,
  formatNumber,
  inputDecimals,
  inputUnit,
  localizeUnits,
  roundTo,
  stepDecimals,
} from "@/lib/format";
import { HEIGHT_REFERENCE_HINT, HEIGHT_REFERENCE_LABEL } from "@/lib/labels";
import { cancelMapPick, cancelMapPickToStart, startMapPick, useMapPick } from "@/lib/map-pick";
import { moduleForKind } from "@/lib/modules";
import { usePlan } from "@/lib/plan-store";
import { CREATE_ANALYSIS_MUTATION_KEY, analysisQueryOptions } from "@/lib/queries";
import type { Units } from "@/lib/store";
import { useSettings, useUi, useUnits } from "@/lib/store";
import {
  isAnalysisActive,
  type AnalysisInfo,
  type BBoxList,
  type DemInfo,
  type HeightReference,
  type Feature,
  type FeatureCollection,
  type Geometry,
  type Issue,
  type ProfilePoint,
  type ResultLayers,
  type Verdict,
} from "@/lib/types";
import { parseWorkspaceParams } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { geometryBounds } from "@/components/map/layers/common";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ErrorSummary, Field } from "@/components/ui/field";
import { NumberInput } from "@/components/ui/number-input";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ToneBadge } from "@/components/workspace/status-badges";
import { announce } from "@/components/workspace/live-region";

export function ModuleSection({
  title,
  children,
  className,
  aside,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
  aside?: React.ReactNode;
}) {
  const id = React.useId();
  return (
    <section aria-labelledby={id} className={cn("space-y-2", className)}>
      <div className="flex items-center justify-between gap-2">
        <h3 id={id} className="text-xs font-medium uppercase tracking-wide text-fg-muted">
          {title}
        </h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

/**
 * "No dataset / select a feature" hint shown above a module form or an empty selection. Every
 * such hint uses this box; pass `icon` to replace the default info icon (it is sized here).
 */
export function InputHint({ children, icon }: { children: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <p className="flex items-start gap-2 rounded border border-dashed border-border p-2.5 text-xs text-fg-muted">
      <span aria-hidden className="mt-0.5 flex shrink-0 [&>svg]:size-3.5">
        {icon ?? <Info />}
      </span>
      <span>{children}</span>
    </p>
  );
}

const VERDICT_VARIANT: Record<Verdict, "ok" | "warn" | "danger"> = { pass: "ok", caution: "warn", fail: "danger" };

/**
 * Verdict with icon + text (never colour alone) and a one-line summary. A text summary written
 * by the API (in metres) is shown in the user's units, like the tiles next to it.
 */
export function VerdictBanner({ verdict, title, summary }: { verdict: Verdict; title?: string; summary?: React.ReactNode }) {
  const units = useUnits();
  const shown = typeof summary === "string" ? localizeUnits(summary, units) : summary;
  return (
    <Alert variant={VERDICT_VARIANT[verdict]} role="status">
      <AlertTitle>
        {title ?? "Verdict"}: {VERDICT_LABEL[verdict]}
      </AlertTitle>
      {shown ? <AlertDescription>{shown}</AlertDescription> : null}
    </Alert>
  );
}

const SEVERITY_LABEL: Record<Issue["severity"], string> = { info: "Info", warning: "Warning", critical: "Critical" };

/** The API's notes for a result; quantities in the messages follow the user's units. */
export function IssueList({ issues, title = "Notes" }: { issues: readonly Issue[]; title?: string }) {
  const units = useUnits();
  if (issues.length === 0) return null;
  return (
    <ModuleSection title={`${title} (${issues.length})`}>
      <ul className="space-y-1.5 text-sm">
        {issues.map((issue, i) => (
          <li key={`${issue.code}-${i}`} className="flex items-start gap-2">
            <ToneBadge tone={toneForSeverity(issue.severity)} label={SEVERITY_LABEL[issue.severity]} />
            <span className="min-w-0 flex-1">{localizeUnits(issue.message, units)}</span>
          </li>
        ))}
      </ul>
    </ModuleSection>
  );
}

export function Disclaimer({ text }: { text: string }) {
  return <p className="border-t border-border pt-2 text-xs text-fg-muted">{text}</p>;
}

export function SyntheticNote({ synthetic }: { synthetic: boolean }) {
  if (!synthetic) return null;
  return (
    <p className="text-xs text-warn">
      Computed with the synthetic demo data pack: terrain, population and obstacles are invented for testing.
    </p>
  );
}

export interface NumFieldProps {
  label: string;
  /**
   * Unit of `value`, `min` and `max` (metric). Lengths, heights and speeds ("m", "km", "m/s",
   * "km/h") are shown and typed in the user's unit system; `onChange` always gets this unit.
   */
  unit?: string;
  value: number | null;
  onChange: (value: number | null) => void;
  min?: number;
  max?: number;
  /** arrow-key step, in the unit shown */
  step?: number;
  decimals?: number;
  hint?: React.ReactNode;
  /** allow an empty value (null) */
  optional?: boolean;
  validate?: (value: number | null) => string | null;
  /** the form's error registry (from `useFormErrors`) */
  form?: FormErrors;
  disabled?: boolean;
  /** whole numbers only (API integer fields); set explicitly, never inferred from `decimals` */
  integer?: boolean;
  /**
   * Quick-pick values shown as a single-choice toggle group above the input, in the unit shown:
   * `metric` in `unit`, `imperial` in its imperial counterpart (default: the metric values
   * converted). `label` names the group (default "<label> presets").
   */
  presets?: { metric: readonly number[]; imperial?: readonly number[]; label?: string };
}

/**
 * Validate a number against a range. `shownMin` / `shownMax` are the bounds as written in the
 * message when the field shows another unit than `value` uses (e.g. feet for metres).
 */
export function rangeError(
  value: number | null,
  opts: {
    min?: number;
    max?: number;
    optional?: boolean;
    integer?: boolean;
    label: string;
    unit?: string;
    shownMin?: number;
    shownMax?: number;
  },
): string | null {
  if (value === null || !Number.isFinite(value)) return opts.optional ? null : `Enter ${opts.label.toLowerCase()}.`;
  if (opts.integer && !Number.isInteger(value)) return "Must be a whole number.";
  const u = opts.unit ? ` ${opts.unit}` : "";
  if (opts.min !== undefined && value < opts.min) return `Must be at least ${formatNumber(opts.shownMin ?? opts.min)}${u}.`;
  if (opts.max !== undefined && value > opts.max) return `Must be at most ${formatNumber(opts.shownMax ?? opts.max)}${u}.`;
  return null;
}

/** A converted bound rounded inwards, so every value the message allows is really in range. */
function shownBound(value: number, direction: "up" | "down"): number {
  const decimals = inputDecimals(value);
  const scale = 10 ** decimals;
  const scaled = direction === "up" ? Math.ceil(value * scale - 1e-9) : Math.floor(value * scale + 1e-9);
  return roundTo(scaled / scale, decimals);
}

/** Two values in the field's unit are the same number (ignoring conversion noise). */
function sameValue(a: number, b: number): boolean {
  return Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));
}

/**
 * Label + number input with unit, inline validation on blur, and error registration. Lengths,
 * heights and speeds follow the user's unit system (ft, mi, mph in imperial): the field shows,
 * accepts, steps and validates in that unit and converts back, so form state and the API stay
 * metric. Text that is not a number (e.g. "1,2,3") is reported, never silently replaced.
 */
export function NumField({
  label,
  unit,
  value,
  onChange,
  min,
  max,
  step,
  decimals,
  hint,
  optional,
  validate,
  form,
  disabled,
  integer,
  presets,
}: NumFieldProps) {
  const id = React.useId();
  const units = useUnits();
  const [touched, setTouched] = React.useState(false);
  const [textError, setTextError] = React.useState<string | null>(null);
  // Remounts the input so a preset replaces invalid text even when the value itself is unchanged.
  const [inputKey, setInputKey] = React.useState(0);
  const wholeOnly = integer ?? false;
  const conversion = React.useMemo(() => (wholeOnly ? null : inputUnit(unit, units)), [wholeOnly, unit, units]);
  const shownUnit = conversion?.unit ?? unit;

  const toShown = React.useCallback(
    (v: number): number => {
      if (!conversion) return v;
      const converted = conversion.toDisplay(v);
      return roundTo(converted, decimals ?? inputDecimals(converted, step));
    },
    [conversion, decimals, step],
  );
  const fromShown = React.useCallback(
    (v: number): number => (conversion ? roundTo(conversion.fromDisplay(v), 6) : v),
    [conversion],
  );
  const shownValue = value === null ? null : toShown(value);
  const shownMin = min === undefined || !conversion ? min : shownBound(conversion.toDisplay(min), "up");
  const shownMax = max === undefined || !conversion ? max : shownBound(conversion.toDisplay(max), "down");

  const error = React.useMemo(() => {
    if (textError) return textError;
    const base = rangeError(value, { min, max, optional, integer: wholeOnly, label, unit: shownUnit, shownMin, shownMax });
    return base ?? validate?.(value) ?? null;
  }, [textError, value, min, max, optional, wholeOnly, label, shownUnit, shownMin, shownMax, validate]);

  const report = form?.report;
  React.useEffect(() => {
    report?.(id, error ? `${label}: ${error}` : null);
  }, [id, error, label, report]);
  React.useEffect(() => () => report?.(id, null), [id, report]);
  const showError = touched || Boolean(form?.attempted);

  const onShownChange = (next: number | null) => {
    if (next === null || !conversion) {
      onChange(next);
      return;
    }
    // Leaving the field re-commits the rounded value shown: keep the exact metric value then.
    if (shownValue !== null && sameValue(next, shownValue)) return;
    onChange(fromShown(next));
  };

  // Each preset as shown and as stored. Metric presets converted for display keep their exact
  // value (a 120 m limit stays 120 m) and are labelled in whole display units ("394 ft").
  const presetItems = !presets
    ? []
    : conversion && presets.imperial
      ? presets.imperial.map((shown) => ({ shown, metric: fromShown(shown), label: shown }))
      : presets.metric.map((metric) => {
          const shown = toShown(metric);
          return { shown, metric, label: conversion ? Math.round(shown) : shown };
        });
  const shownHint = typeof hint === "string" ? localizeUnits(hint, units) : hint;

  const field = (
    <Field id={id} label={label} unit={shownUnit} hint={shownHint} error={showError ? error : null} required={!optional}>
      {(p) => (
        <NumberInput
          key={inputKey}
          {...p}
          value={shownValue}
          onValueChange={onShownChange}
          onInvalidText={setTextError}
          onBlur={() => setTouched(true)}
          min={shownMin}
          max={shownMax}
          step={step}
          unit={shownUnit}
          // Whole-number fields are not rounded on commit: "5.5" shows an error instead of becoming 6.
          decimals={wholeOnly || conversion ? undefined : decimals}
          allowEmpty={optional}
          disabled={disabled}
        />
      )}
    </Field>
  );

  if (presetItems.length === 0) return field;
  const current = presetItems.find((p) => value !== null && sameValue(value, p.metric));
  return (
    <div className="space-y-2">
      <ToggleGroup
        type="single"
        aria-label={presets?.label ?? `${label} presets`}
        value={current ? String(current.shown) : ""}
        onValueChange={(v) => {
          const picked = presetItems.find((p) => String(p.shown) === v);
          if (!picked) return;
          if (textError) {
            setTextError(null);
            setInputKey((k) => k + 1);
          }
          onChange(picked.metric);
        }}
        disabled={disabled}
        className="flex-wrap"
      >
        {presetItems.map(({ shown, label: presetLabel }) => (
          <ToggleGroupItem key={shown} value={String(shown)}>
            {formatNumber(presetLabel, { maximumFractionDigits: Math.min(stepDecimals(presetLabel), 2) })} {shownUnit}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      {field}
    </div>
  );
}

export interface FormErrors {
  errors: { id: string; message: string }[];
  report: (id: string, error: string | null) => void;
  hasErrors: boolean;
  /** true after a Run attempt with errors: every field then shows its error */
  attempted: boolean;
  /** increments on every Run attempt blocked by errors (Advanced sections open on each) */
  attemptSeq: number;
  summaryRef: React.RefObject<HTMLDivElement | null>;
  /** wrap a submit handler: runs it when valid, otherwise shows and focuses the error summary */
  guard: (fn: () => void) => () => void;
}

/** Collects field errors for the "Run" guard and the focusable error summary. */
export function useFormErrors(): FormErrors {
  const [errors, setErrors] = React.useState<Record<string, string>>({});
  const [attempted, setAttempted] = React.useState(false);
  const [attemptSeq, setAttemptSeq] = React.useState(0);
  const summaryRef = React.useRef<HTMLDivElement>(null);
  const report = React.useCallback((id: string, error: string | null) => {
    setErrors((prev) => {
      if ((prev[id] ?? null) === error) return prev;
      const next = { ...prev };
      if (error) next[id] = error;
      else delete next[id];
      return next;
    });
  }, []);
  const list = React.useMemo(() => Object.entries(errors).map(([id, message]) => ({ id, message })), [errors]);
  const hasErrors = list.length > 0;
  const guard = React.useCallback(
    (fn: () => void) => () => {
      if (hasErrors) {
        setAttempted(true);
        setAttemptSeq((n) => n + 1);
        window.setTimeout(() => summaryRef.current?.focus(), 0);
        return;
      }
      setAttempted(false);
      fn();
    },
    [hasErrors],
  );
  return { errors: list, report, hasErrors, attempted, attemptSeq, summaryRef, guard };
}

export function FormErrorSummary({ form }: { form: FormErrors }) {
  if (!form.attempted || !form.hasErrors) return null;
  return <ErrorSummary ref={form.summaryRef} errors={form.errors} />;
}

/** Most restrictive first when sorting zones. */
export const ZONE_ORDER: Record<"green" | "yellow" | "red", number> = { red: 0, yellow: 1, green: 2 };

export function formatDistanceKm(km: number | null | undefined, units: Units): string {
  if (km === null || km === undefined || !Number.isFinite(km)) return formatLength(null, units);
  return formatLength(km * 1000, units);
}

export function SwitchField({
  label,
  checked,
  onChange,
  hint,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  hint?: React.ReactNode;
  disabled?: boolean;
}) {
  const id = React.useId();
  const hintId = `${id}-hint`;
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        {hint ? (
          <p id={hintId} className="text-xs text-fg-muted">
            {hint}
          </p>
        ) : null}
      </div>
      <Switch id={id} checked={checked} onCheckedChange={onChange} aria-describedby={hint ? hintId : undefined} disabled={disabled} />
    </div>
  );
}

/**
 * "Advanced" disclosure. The content stays mounted while closed (`hidden`, so it is out of the
 * tab order and the accessibility tree) so its fields keep reporting validation errors. After a
 * failed Run it opens by itself when one of the invalid fields is inside it.
 */
export function Advanced({ children, label = "Advanced", form }: { children: React.ReactNode; label?: string; form?: FormErrors }) {
  const [open, setOpen] = React.useState(false);
  const contentId = React.useId();
  const contentRef = React.useRef<HTMLDivElement>(null);
  // Open on each blocked Run (not on every edit) when an invalid field is inside this section.
  const attemptSeq = form?.attemptSeq ?? 0;
  const errorsRef = React.useRef(form?.errors ?? []);
  errorsRef.current = form?.errors ?? [];
  React.useEffect(() => {
    const container = contentRef.current;
    if (attemptSeq === 0 || !container) return;
    const inside = errorsRef.current.some((e) => {
      const el = document.getElementById(e.id);
      return el !== null && container.contains(el);
    });
    if (inside) setOpen(true);
  }, [attemptSeq]);
  return (
    <div>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="w-full justify-between px-2"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() => setOpen((v) => !v)}
      >
        {label}
        <ChevronDown aria-hidden className={cn("size-4 transition-transform duration-200", open && "rotate-180")} />
      </Button>
      <div id={contentId} ref={contentRef} hidden={!open} className="space-y-3 pt-2">
        {children}
      </div>
    </div>
  );
}

/**
 * Is a job of this module queued or running on this dataset? Checks the one open in the drawer
 * (URL `a`) and the one this tab created last; the second covers the gap before the new id
 * reaches the URL, where a quick second press would start a duplicate. Jobs on another dataset
 * don't block, jobs without one (flight audits) block everywhere, and one whose status can't
 * be fetched any more doesn't. Only reads the caches the workspace already polls.
 */
function useModuleAnalysisRunning(): boolean {
  const { analysisId, module, datasetId } = parseWorkspaceParams(useSearchParams());
  const created = useMutationState({
    filters: { mutationKey: CREATE_ANALYSIS_MUTATION_KEY, status: "success" },
    select: (mutation) => (mutation.state.data as AnalysisInfo | undefined)?.id ?? null,
  });
  const lastCreated = created.at(-1) ?? null;
  const ids = [...new Set([analysisId, lastCreated])].filter((id): id is string => id !== null);
  return useQueries({
    queries: ids.map((id) => ({ ...analysisQueryOptions(id), enabled: false })),
    combine: (results) =>
      results.some(
        ({ data, isError }) =>
          !isError &&
          data !== undefined &&
          isAnalysisActive(data.status) &&
          (module === null || moduleForKind(data.kind) === module) &&
          (data.dataset_id === null || data.dataset_id === datasetId),
      ),
  });
}

/**
 * Run button: disabled in demo mode or with a form-level reason (shown under it). While the job
 * is being created, or an analysis of this module is queued or running, it is busy: a second
 * press does nothing (no duplicate jobs), and it stays focusable (`aria-disabled`, not
 * `disabled`) so keyboard focus is not dropped to the page when it is pressed.
 */
export function RunButton({
  onRun,
  pending,
  disabledReason,
  label = "Run",
}: {
  onRun: () => void;
  pending: boolean;
  disabledReason?: string | null;
  label?: string;
}) {
  const reason = DEMO_MODE ? "The public demo is read-only. Run AirBrief yourself to analyse new inputs." : disabledReason ?? null;
  const reasonId = React.useId();
  const running = useModuleAnalysisRunning();
  const busy = pending || running;
  const note =
    reason ??
    (running && !pending
      ? "An analysis of this module is running. Wait for it to finish, or cancel it in the results drawer, to start another."
      : null);
  return (
    <div className="space-y-1.5">
      <Button
        type="submit"
        className="w-full"
        onClick={(e) => {
          e.preventDefault();
          if (!reason && !busy) onRun();
        }}
        disabled={Boolean(reason)}
        aria-disabled={busy || undefined}
        aria-busy={busy || undefined}
        aria-describedby={note ? reasonId : undefined}
      >
        {busy ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <Play aria-hidden />}
        {pending ? "Starting…" : running ? "Running…" : label}
      </Button>
      {note ? (
        <p id={reasonId} className="text-xs text-fg-muted">
          {note}
        </p>
      ) : null}
    </div>
  );
}

export interface ProfileSeries {
  key: "terrain_m" | "agl_m" | "altitude_amsl_m" | "limit_agl_m";
  label: string;
  /** CSS colour */
  color: string;
  kind: "line" | "area";
  dashed?: boolean;
  /** "linear" (default: straight between samples), or "stepAfter" for limits that change at zone edges */
  curve?: "linear" | "stepAfter";
}

export interface ProfileChartProps {
  points: readonly ProfilePoint[];
  series: readonly ProfileSeries[];
  units: Units;
  /** horizontal dashed reference line (e.g. ceiling) */
  limit?: { value: number; label: string } | null;
  /** shaded x-ranges in metres along the path (e.g. exceedances); `active` emphasises the one selected in a table */
  bands?: readonly { start: number; end: number; label: string; active?: boolean; tone?: "danger" | "warn" }[];
  /** accessible name */
  title: string;
  height?: number;
}

const MAX_CHART_POINTS = 1000;

export function downsample<T>(items: readonly T[], max: number): T[] {
  if (items.length <= max) return [...items];
  const step = items.length / max;
  const out: T[] = [];
  for (let i = 0; i < max; i += 1) out.push(items[Math.floor(i * step)] as T);
  out.push(items[items.length - 1] as T);
  return out;
}

/**
 * Round axis ticks inside [min, max]: steps of 1, 2, 2.5 or 5 × 10ⁿ giving about five intervals,
 * plus the decimals they need, so labels are exact (never 0.75 printed as "0.8").
 */
export function niceTicks(min: number, max: number): { ticks: number[]; decimals: number } {
  const span = max - min;
  if (!(span > 0) || !Number.isFinite(span)) return { ticks: Number.isFinite(min) ? [roundTo(min, 6)] : [], decimals: 2 };
  const raw = span / 5;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const nice = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw * (1 - 1e-9)) ?? 10 * magnitude;
  const step = roundTo(nice, 12);
  const decimals = stepDecimals(step);
  const ticks: number[] = [];
  for (let i = Math.ceil(min / step - 1e-9); i * step <= max + step * 1e-9; i += 1) ticks.push(roundTo(i * step, decimals));
  return { ticks, decimals };
}

const BAND_LABEL_SLOTS = ["insideTop", "insideBottom", "center"] as const;

/**
 * Where each band's label goes: the first of top, bottom and middle that no earlier band it runs
 * into has taken. A band counts as at least as wide as its label (~6 px a character at 10 px
 * type), so bands too narrow or too close for two labels side by side split them as well. Past
 * three deep they share the middle.
 */
function bandLabelSlots(
  bands: readonly { start: number; end: number; text: string }[],
  metresPerPx: number,
): (typeof BAND_LABEL_SLOTS)[number][] {
  const spans = bands.map((b) => {
    const mid = (b.start + b.end) / 2;
    const half = Math.max((b.end - b.start) / 2, b.text.length * 3 * metresPerPx);
    return [mid - half, mid + half];
  });
  const slots: number[] = [];
  spans.forEach(([lo, hi], i) => {
    const taken = new Set<number>();
    for (let j = 0; j < i; j += 1) if (spans[j][0] < hi && lo < spans[j][1]) taken.add(slots[j]);
    let s = 0;
    while (taken.has(s) && s < BAND_LABEL_SLOTS.length - 1) s += 1;
    slots.push(s);
  });
  return slots.map((s) => BAND_LABEL_SLOTS[s]);
}

/** Line/area profile along a path, with a dashed limit line, shaded bands and a table toggle. */
export function ProfileChart({ points, series, units, limit, bands = [], title, height = 220 }: ProfileChartProps) {
  const [asTable, setAsTable] = React.useState(false);
  // the plot's width: until the chart is measured, about what the results drawer gives it
  const [plotPx, setPlotPx] = React.useState(400);
  const captionId = React.useId();
  const data = React.useMemo(() => downsample(points, MAX_CHART_POINTS), [points]);
  const imperial = units === "imperial";
  const toY = (m: number | null | undefined) => (m === null || m === undefined ? null : imperial ? m * 3.28084 : m);
  const toX = (m: number) => (imperial ? m / 1609.344 : m / 1000);
  const xUnit = imperial ? "mi" : "km";
  const yUnit = imperial ? "ft" : "m";
  const chartData = React.useMemo(
    () =>
      data.map((p) => ({
        x: toX(p.distance_m),
        terrain_m: toY(p.terrain_m),
        agl_m: toY(p.agl_m),
        altitude_amsl_m: toY(p.altitude_amsl_m),
        limit_agl_m: toY(p.limit_agl_m),
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, imperial],
  );
  const xAxis = React.useMemo(() => {
    const xs = chartData.map((d) => d.x);
    return niceTicks(Math.min(...xs), Math.max(...xs));
  }, [chartData]);
  // Heights above ground start at 0: a dip of a few centimetres must not suggest a flight below
  // ground. The axis only goes negative when the data does by more than a metre.
  const aboveGroundOnly = series.every((s) => s.key === "agl_m" || s.key === "limit_agl_m");
  const groundTolerance = imperial ? 1 / 0.3048 : 1;
  const metresPerPx = points.length > 1 ? (points[points.length - 1].distance_m - points[0].distance_m) / plotPx : 0;
  // every band keeps room for " (selected)": selecting a row neither moves a label nor overlaps one
  const labelSlots = bandLabelSlots(
    bands.map((b) => ({ start: b.start, end: b.end, text: `${b.label} (selected)` })),
    metresPerPx,
  );

  if (points.length < 2) return null;

  return (
    <figure className="space-y-2" aria-label={title}>
      <div className="flex items-center justify-between gap-2">
        <figcaption className="text-xs font-medium uppercase tracking-wide text-fg-muted">{title}</figcaption>
        <Button type="button" variant="ghost" size="sm" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          <Table2 aria-hidden /> {asTable ? "Show chart" : "Show table"}
        </Button>
      </div>
      {asTable ? (
        // Scrolls: focusable so keyboard users can scroll it (WCAG 2.1.1), named by the caption.
        <div role="region" aria-labelledby={captionId} tabIndex={0} className="max-h-64 overflow-auto rounded border border-border">
          <table className="w-full text-xs">
            <caption id={captionId} className="sr-only">{title}</caption>
            <thead className="sticky top-0 bg-surface">
              <tr>
                <th scope="col" className="px-2 py-1 text-right font-medium">Distance</th>
                {series.map((s) => (
                  <th key={s.key} scope="col" className="px-2 py-1 text-right font-medium">
                    {s.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((p, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="num px-2 py-0.5 text-right">{formatLength(p.distance_m, units)}</td>
                  {series.map((s) => (
                    <td key={s.key} className="num px-2 py-0.5 text-right">
                      {formatHeight(p[s.key] ?? null, units)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ height }} className="w-full">
          {/* the plot is the container less the y axis (56) and the margins (4 + 16); a hidden
              drawer or tab measures 0, so that keeps the last width */}
          <ResponsiveContainer
            width="100%"
            height="100%"
            onResize={(w) => {
              if (w > 0) setPlotPx(Math.max(w - 76, 1));
            }}
          >
            <ComposedChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis
                dataKey="x"
                type="number"
                domain={["dataMin", "dataMax"]}
                ticks={xAxis.ticks}
                tickFormatter={(v: number) => formatNumber(v, { maximumFractionDigits: xAxis.decimals })}
                stroke="var(--text-muted)"
                fontSize={11}
                label={{ value: `Distance (${xUnit})`, position: "insideBottomRight", offset: -2, fontSize: 11, fill: "var(--text-muted)" }}
              />
              <YAxis
                stroke="var(--text-muted)"
                fontSize={11}
                width={56}
                domain={
                  aboveGroundOnly
                    ? [(dataMin: number) => (dataMin >= -groundTolerance ? 0 : Math.floor(dataMin)), "auto"]
                    : undefined
                }
                tickFormatter={(v: number) => formatNumber(v, { maximumFractionDigits: 0 })}
                label={{ value: yUnit, angle: -90, position: "insideLeft", fontSize: 11, fill: "var(--text-muted)" }}
              />
              <ChartTooltip
                contentStyle={{ background: "var(--elevated)", border: "1px solid var(--border)", fontSize: 12 }}
                labelFormatter={(v: number) => `${formatNumber(v, { maximumFractionDigits: 2 })} ${xUnit}`}
                formatter={(v: number, name: string) => [`${formatNumber(v, { maximumFractionDigits: 1 })} ${yUnit}`, name]}
              />
              {bands.map((b, i) => {
                const color = b.tone === "warn" ? "var(--warn)" : "var(--danger)";
                return (
                  <ReferenceArea
                    key={i}
                    x1={toX(b.start)}
                    x2={toX(b.end)}
                    fill={color}
                    fillOpacity={b.active ? 0.3 : 0.12}
                    stroke={color}
                    strokeWidth={b.active ? 2 : 1}
                    strokeDasharray={b.active ? undefined : "4 3"}
                    label={{ value: b.active ? `${b.label} (selected)` : b.label, position: labelSlots[i], fontSize: 10, fontWeight: b.active ? 600 : 400, fill: color }}
                  />
                );
              })}
              {series.map((s) =>
                s.kind === "area" ? (
                  <Area
                    key={s.key}
                    type={s.curve ?? "linear"}
                    dataKey={s.key}
                    name={s.label}
                    stroke={s.color}
                    fill={s.color}
                    fillOpacity={0.2}
                    isAnimationActive={false}
                    connectNulls
                  />
                ) : (
                  <Line
                    key={s.key}
                    type={s.curve ?? "linear"}
                    dataKey={s.key}
                    name={s.label}
                    stroke={s.color}
                    strokeWidth={2}
                    strokeDasharray={s.dashed ? "6 4" : undefined}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls
                  />
                ),
              )}
              {limit ? (
                <ReferenceLine
                  y={toY(limit.value) ?? undefined}
                  stroke="var(--warn)"
                  strokeDasharray="6 4"
                  label={{ value: limit.label, position: "insideTopRight", fontSize: 11, fill: "var(--warn)" }}
                />
              ) : null}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <ul className="flex flex-wrap gap-3 text-xs text-fg-muted" aria-label="Chart legend">
        {series.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-0 w-5 border-t-2"
              style={{ borderColor: s.color, borderStyle: s.dashed ? "dashed" : "solid" }}
            />
            {s.label}
          </li>
        ))}
        {limit ? (
          <li className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-0 w-5 border-t-2 border-dashed border-warn" />
            {limit.label}
          </li>
        ) : null}
      </ul>
    </figure>
  );
}

const EMPTY_COLLECTION: FeatureCollection = Object.freeze({
  type: "FeatureCollection",
  features: Object.freeze([]) as unknown as FeatureCollection["features"],
}) as FeatureCollection;

// a missing or malformed layer gets the shared EMPTY_COLLECTION (frozen, so dont push to it)
export function resultLayer(layers: ResultLayers | null | undefined, key: string): FeatureCollection {
  const fc = layers?.[key];
  if (fc && Array.isArray(fc.features)) return fc;
  return EMPTY_COLLECTION;
}

/** Bounds of a point with a buffer in metres (for zooming to a site / obstacle). */
export function pointBounds(lon: number, lat: number, radiusM = 400): BBoxList {
  const dLat = radiusM / 111_320;
  const dLon = radiusM / (111_320 * Math.max(0.1, Math.cos((lat * Math.PI) / 180)));
  return [lon - dLon, lat - dLat, lon + dLon, lat + dLat];
}

/** Zoom-to callback for result tables. */
export function useFocusMap() {
  const focusMap = useUi((s) => s.focusMap);
  return React.useCallback(
    (target: BBoxList | Geometry | Feature | null | undefined) => {
      if (!target) return;
      if (Array.isArray(target)) {
        focusMap([target[0], target[1], target[2], target[3]]);
        return;
      }
      const geometry = "geometry" in target ? (target as Feature).geometry : (target as Geometry);
      const b = geometryBounds(geometry);
      if (b) focusMap([b[0], b[1], b[2], b[3]]);
    },
    [focusMap],
  );
}

/** Number of polygon features in the selection (or the whole dataset when nothing is selected). */
export function countPolygons(
  features: FeatureCollection<Geometry | null, { _index: number }> | null,
  selected: readonly number[],
): number {
  if (!features) return 0;
  const wanted = selected.length > 0 ? new Set(selected) : null;
  return features.features.filter(
    (f) => (!wanted || wanted.has(f.properties._index)) && f.geometry != null && /Polygon$/.test(f.geometry.type),
  ).length;
}

/** The user's unit setting outside React (map tooltips are plain callbacks). */
export function currentUnits(): Units {
  return useSettings.getState().units;
}

export function featureName(p: { _index: number; _name: string | null }) {
  return p._name ?? `Feature ${p._index}`;
}

export { ToneBadge };

/** The form state behind `HeightReferenceField` (empty take-off fields: the module's default). */
export interface HeightReferenceState {
  reference: HeightReference;
  takeoffLon: number | null;
  takeoffLat: number | null;
  /** typed ground elevation of the take-off point (m AMSL); null: from the terrain model */
  takeoffElevation: number | null;
}

export const DEFAULT_HEIGHT_REFERENCE: HeightReferenceState = Object.freeze({
  reference: "agl",
  takeoffLon: null,
  takeoffLat: null,
  takeoffElevation: null,
});

const HEIGHT_REFERENCES: readonly HeightReference[] = ["agl", "takeoff", "amsl"];

function finiteOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The field state from stored parameters (`height_reference`, `takeoff`, `takeoff_elevation_m`). */
export function heightReferenceFromParams(p: Readonly<Record<string, unknown>>): HeightReferenceState {
  const raw = p.height_reference;
  const reference = typeof raw === "string" && (HEIGHT_REFERENCES as readonly string[]).includes(raw) ? (raw as HeightReference) : "agl";
  const takeoff = Array.isArray(p.takeoff) ? (p.takeoff as unknown[]) : null;
  return {
    reference,
    takeoffLon: takeoff ? finiteOrNull(takeoff[0]) : null,
    takeoffLat: takeoff ? finiteOrNull(takeoff[1]) : null,
    takeoffElevation: finiteOrNull(p.takeoff_elevation_m),
  };
}

/**
 * The API parameters of the field. An above-ground plan sends only the reference (the take-off
 * point means nothing there), so stored AGL analyses keep their meaning.
 */
export function heightReferenceParams(state: HeightReferenceState): {
  height_reference: HeightReference;
  takeoff?: [number, number] | null;
  takeoff_elevation_m?: number | null;
} {
  if (state.reference === "agl") return { height_reference: "agl" };
  const takeoff: [number, number] | null =
    state.takeoffLon !== null && state.takeoffLat !== null ? [state.takeoffLon, state.takeoffLat] : null;
  return { height_reference: state.reference, takeoff, takeoff_elevation_m: state.takeoffElevation };
}

/** Ends a pending map pick when the panel goes away, on Escape, and when plan drawing starts. */
function useTakeoffPickLifecycle(pickId: string) {
  const pending = useMapPick((s) => s.request?.id === pickId);
  const planDrawing = usePlan((s) => s.drawing);
  React.useEffect(
    () => () => {
      if (useMapPick.getState().request?.id === pickId) cancelMapPick();
    },
    [pickId],
  );
  React.useEffect(() => {
    if (planDrawing && useMapPick.getState().request?.id === pickId) cancelMapPick();
  }, [planDrawing, pickId]);
  React.useEffect(() => {
    if (!pending) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      e.preventDefault();
      e.stopPropagation();
      cancelMapPickToStart();
      announce("Picking the take-off point cancelled.");
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [pending]);
  return pending;
}

export interface TakeoffFieldsProps {
  value: HeightReferenceState;
  onChange: (next: HeightReferenceState) => void;
  form: FormErrors;
  /** `useFormFromAnalysis().fieldsKey`: remounts the number fields when stored values are put back */
  fieldsKey?: number;
  /** false: no position fields (e.g. a corridor takes off from its origin) */
  position?: boolean;
  /** the ground-elevation override (meaningful above take-off and AMSL only) */
  elevation?: boolean;
  /** where the take-off is when the position is left empty, e.g. "the first point of the route" */
  defaultTakeoff?: string;
  elevationLabel?: string;
  /** unique id of this field's map pick (one per panel) */
  pickId: string;
  disabled?: boolean;
}

/**
 * The take-off point: typed latitude and longitude or "Pick take-off on map" (`lib/map-pick.ts`;
 * empty: the module's default), and an optional typed ground elevation (empty: the terrain
 * model). Typed coordinates are the keyboard alternative to the map pick; with the map focused,
 * Space places the point at its centre and Escape cancels.
 */
export function TakeoffFields({
  value,
  onChange,
  form,
  fieldsKey = 0,
  position = true,
  elevation = true,
  defaultTakeoff = "the module's default point",
  elevationLabel = "Take-off ground elevation",
  pickId,
  disabled,
}: TakeoffFieldsProps) {
  const buttonRef = React.useRef<HTMLButtonElement>(null);
  const picking = useTakeoffPickLifecycle(pickId);
  const valueRef = React.useRef(value);
  valueRef.current = value;
  const onChangeRef = React.useRef(onChange);
  onChangeRef.current = onChange;
  const set = (patch: Partial<HeightReferenceState>) => onChange({ ...value, ...patch });
  const half = (value.takeoffLat === null) !== (value.takeoffLon === null);
  const pairError = (v: number | null) => (half && v === null ? "Enter both latitude and longitude, or leave both empty." : null);
  const hasPoint = value.takeoffLat !== null && value.takeoffLon !== null;

  const togglePick = () => {
    if (picking) {
      cancelMapPick();
      announce("Picking the take-off point cancelled.");
      return;
    }
    usePlan.getState().cancelDrawing();
    startMapPick({
      id: pickId,
      label: "Take-off",
      onPick: ([lon, lat]) => {
        onChangeRef.current({ ...valueRef.current, takeoffLon: lon, takeoffLat: lat });
        announce(`Take-off placed at ${formatCoord(lon, lat)}.`);
      },
      returnFocus: buttonRef.current,
    });
    announce("Click the map to place the take-off point, or press Space to place it at the centre of the focused map. Escape cancels.");
  };

  return (
    <div role="group" aria-label="Take-off" className="space-y-3 rounded border border-border p-3">
      <p className="flex items-center gap-1.5 text-sm font-medium">
        <PlaneTakeoff aria-hidden className="size-3.5 text-accent" />
        Take-off
      </p>
      {position ? (
        <>
          <div key={fieldsKey} className="grid grid-cols-2 gap-2">
            <NumField
              label="Take-off latitude"
              unit="°"
              value={value.takeoffLat}
              onChange={(v) => set({ takeoffLat: v })}
              min={-90}
              max={90}
              step={0.0001}
              optional
              validate={pairError}
              form={form}
              disabled={disabled}
            />
            <NumField
              label="Take-off longitude"
              unit="°"
              value={value.takeoffLon}
              onChange={(v) => set({ takeoffLon: v })}
              min={-180}
              max={180}
              step={0.0001}
              optional
              validate={pairError}
              form={form}
              disabled={disabled}
            />
          </div>
          <p className="text-xs text-fg-muted">
            {hasPoint ? `Take-off at ${formatCoord(value.takeoffLon, value.takeoffLat)}.` : `Empty: ${defaultTakeoff}.`}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              ref={buttonRef}
              type="button"
              variant={picking ? "secondary" : "outline"}
              size="sm"
              aria-pressed={picking}
              onClick={togglePick}
              disabled={disabled}
            >
              <Crosshair aria-hidden />
              {picking ? "Click the map… (Esc cancels)" : "Pick take-off on map"}
            </Button>
            {hasPoint || half ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  onChange({ ...value, takeoffLat: null, takeoffLon: null });
                  announce(`Take-off point cleared: ${defaultTakeoff} is used.`);
                }}
                disabled={disabled}
              >
                Use the default
              </Button>
            ) : null}
          </div>
        </>
      ) : null}
      {elevation ? (
        <NumField
          key={`elevation-${fieldsKey}`}
          label={elevationLabel}
          unit="m"
          value={value.takeoffElevation}
          onChange={(v) => set({ takeoffElevation: v })}
          min={-500}
          max={9000}
          optional
          hint="Above mean sea level. Empty: read from the terrain model at the take-off point; type it when you know it better."
          form={form}
          disabled={disabled}
        />
      ) : null}
    </div>
  );
}

export interface HeightReferenceFieldProps extends Omit<TakeoffFieldsProps, "elevation"> {
  /** the references offered (default AGL, above take-off and AMSL) */
  references?: readonly HeightReference[];
  /** show the take-off position for every reference (e.g. survey transit legs start there) */
  takeoffAlways?: boolean;
  /** what a reference means in this module, when it differs from the general hint */
  hints?: Partial<Record<HeightReference, string>>;
}

/**
 * How the planned height is read: a segmented AGL / Above take-off / AMSL control. Above take-off
 * and AMSL add the take-off point and its ground elevation (`TakeoffFields`), which turn the
 * planned value into a level and the level into heights above the ground along the flight.
 */
export function HeightReferenceField({ references = HEIGHT_REFERENCES, takeoffAlways = false, hints, ...takeoff }: HeightReferenceFieldProps) {
  const { value, onChange, disabled } = takeoff;
  const labelId = React.useId();
  const hintId = React.useId();
  const terrainTrue = value.reference !== "agl";
  return (
    // The segmented control carries the "Height reference" name; the take-off fields are their own group.
    <div className="space-y-3">
      <div className="space-y-1.5">
        <p id={labelId} className="text-sm font-medium">
          Height reference
        </p>
        <ToggleGroup
          type="single"
          value={value.reference}
          onValueChange={(v) => {
            if (v) onChange({ ...value, reference: v as HeightReference });
          }}
          aria-labelledby={labelId}
          aria-describedby={hintId}
          disabled={disabled}
          className="flex-wrap"
        >
          {references.map((r) => (
            <ToggleGroupItem key={r} value={r}>
              {HEIGHT_REFERENCE_LABEL[r]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <p id={hintId} className="text-xs text-fg-muted">
          {hints?.[value.reference] ?? HEIGHT_REFERENCE_HINT[value.reference]}
        </p>
      </div>
      {terrainTrue || (takeoffAlways && takeoff.position !== false) ? <TakeoffFields {...takeoff} elevation={terrainTrue} /> : null}
    </div>
  );
}

function demChipLabel(dem: DemInfo): string {
  if (dem.synthetic) return "Synthetic terrain";
  if (dem.surface_model) return "Surface model (DSM)";
  return "Terrain model";
}

/**
 * The caveat shown on every terrain readout: what the data-pack elevation model is, from its
 * manifest (Copernicus GLO-30 is a surface model: roofs and canopy are included). Text, not
 * colour: a labelled chip and the full sentence.
 */
export function DemCaveat({ dem, className }: { dem: DemInfo | null | undefined; className?: string }) {
  if (!dem) return null;
  return (
    <p className={cn("flex flex-wrap items-start gap-1.5 text-xs text-fg-muted", className)}>
      <span className="inline-flex shrink-0 items-center gap-1 rounded border border-border bg-elevated px-1.5 py-0.5 font-medium text-fg">
        <Mountain aria-hidden className="size-3.5 text-fg-muted" />
        {demChipLabel(dem)}
      </span>
      <span className="min-w-0 flex-1">{dem.caveat}</span>
    </p>
  );
}
