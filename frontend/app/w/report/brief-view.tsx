"use client";

/**
 * Brief mode of `/w/report` (`?mode=brief`): pick analyses, describe the operation, create the
 * brief (`POST /api/briefs/`) and print it on one A4 page. The static demo merges pre-built
 * one-analysis briefs in the browser (lib/brief-demo.ts); only the bundle needs the API.
 */

import * as React from "react";
import { AlertTriangle, CheckCircle2, Download, FileJson, FileStack, Loader2, MapPin, Printer, RefreshCw } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Field } from "@/components/ui/field";
import { Input, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toaster";
import { BriefSheet } from "@/components/report/brief-sheet";
import { CopyButton } from "@/components/layout/copy-button";
import { rememberBrief } from "@/lib/brief-links";
import { briefMapLayers } from "@/components/report/map-layers";
import { DateTimeField } from "@/components/modules/environment/fields";
import {
  BROWSER_ZONE,
  browserOffsetMinutes,
  browserZoneName,
  defaultPlan,
  isHhmm,
  isIsoDate,
  offsetChoices,
  offsetLabel,
} from "@/components/modules/environment/time";
import { FormErrorSummary, NumField, useFormErrors } from "@/components/modules/shared";
import { analysisKindLabel } from "@/components/workspace/analysis-runner";
import { VisitorBadge } from "@/components/workspace/status-badges";
import { DEMO_MODE, describeError, downloadBriefBundle } from "@/lib/api";
import { formatCoord, formatRelative } from "@/lib/format";
import { analysisParamsSummary } from "@/lib/modules";
import {
  useAddedByVisitor,
  useAnalyses,
  useAnalysis,
  useCreateBrief,
  useMeta,
  useMyAnalyses,
  useMyDatasetAnalysisIds,
} from "@/lib/queries";
import { useUnits } from "@/lib/store";
import { ANALYSIS_KINDS, type AnalysisInfo, type Brief, type BriefRequest, type OperationMeta, type UasPreset } from "@/lib/types";
import { downloadBlob } from "@/lib/utils";

/** Analyses one brief may hold (`airbrief_core.models.brief.MAX_BRIEF_ANALYSES`). */
export const MAX_BRIEF_ANALYSES = 12;

/** Printable height of an A4 page with 10 mm margins, in CSS px (96 px per inch). */
const PAGE_HEIGHT_PX = ((297 - 20) * 96) / 25.4;
/** The smallest print text scale: below it the brief continues on a second page instead. */
const MIN_FIT = 0.6;

const STORAGE_KEY = "airbrief-brief-operation";

interface OperationDraft {
  operator: string;
  reference: string;
  /** "none", "custom" or a `Meta.uas_presets` key */
  aircraft: string;
  aircraftName: string;
  massKg: number | null;
  dimensionM: number | null;
  speedMs: number | null;
  windowOn: boolean;
  date: string;
  start: string;
  end: string;
  /** BROWSER_ZONE or minutes east of UTC */
  zone: string;
  pilotLon: number | null;
  pilotLat: number | null;
  notes: string;
}

function emptyDraft(): OperationDraft {
  const plan = defaultPlan();
  return {
    operator: "",
    reference: "",
    aircraft: "none",
    aircraftName: "",
    massKg: null,
    dimensionM: null,
    speedMs: null,
    windowOn: false,
    date: plan.date,
    start: plan.start,
    end: plan.end,
    zone: BROWSER_ZONE,
    pilotLon: null,
    pilotLat: null,
    notes: "",
  };
}

function loadDraft(): OperationDraft {
  const base = emptyDraft();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return base;
    const saved = JSON.parse(raw) as Partial<OperationDraft>;
    const out: OperationDraft = { ...base };
    for (const key of Object.keys(base) as (keyof OperationDraft)[]) {
      const value = saved[key];
      if (value !== undefined && (typeof value === typeof base[key] || (value === null && base[key] === null) || (typeof value === "number" && base[key] === null))) {
        (out as unknown as Record<string, unknown>)[key] = value;
      }
    }
    return out;
  } catch {
    return base;
  }
}

function saveDraft(draft: OperationDraft): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // Storage blocked or full: the form still works for this visit.
  }
}

/** Local "YYYY-MM-DD" + "HH:MM" at `offset` minutes east of UTC → ISO UTC. */
function toUtc(date: string, time: string, offset: number, nextDay = false): string {
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  const ms = Date.UTC(y as number, (m as number) - 1, (d as number) + (nextDay ? 1 : 0), hh as number, mm as number) - offset * 60_000;
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
}

function zoneOffset(draft: OperationDraft): number {
  return draft.zone === BROWSER_ZONE ? browserOffsetMinutes(draft.date, draft.start) : Number(draft.zone);
}

function toOperation(draft: OperationDraft): OperationMeta {
  const text = (s: string) => (s.trim() ? s.trim() : null);
  let aircraft: OperationMeta["aircraft"] = null;
  if (draft.aircraft !== "none") {
    aircraft = {
      preset: draft.aircraft === "custom" ? null : draft.aircraft,
      name: text(draft.aircraftName),
      takeoff_mass_kg: draft.massKg,
      max_dimension_m: draft.dimensionM,
      max_speed_ms: draft.speedMs,
    };
  }
  const offset = zoneOffset(draft);
  const windowValid = draft.windowOn && isIsoDate(draft.date) && isHhmm(draft.start) && isHhmm(draft.end);
  return {
    operator: text(draft.operator),
    reference: text(draft.reference),
    aircraft,
    pilot_position: draft.pilotLon !== null && draft.pilotLat !== null ? [draft.pilotLon, draft.pilotLat] : null,
    planned_start_utc: windowValid ? toUtc(draft.date, draft.start, offset) : null,
    planned_end_utc: windowValid ? toUtc(draft.date, draft.end, offset, draft.end <= draft.start) : null,
    utc_offset_min: draft.windowOn ? offset : 0,
    notes: text(draft.notes),
  };
}

/** Positions in the selected analyses that can stand for the pilot / take-off position. */
function positionCandidates(analyses: readonly AnalysisInfo[]): { label: string; lon: number; lat: number }[] {
  const out: { label: string; lon: number; lat: number }[] = [];
  const add = (label: string, lon: unknown, lat: unknown) => {
    if (typeof lon === "number" && typeof lat === "number" && Number.isFinite(lon) && Number.isFinite(lat)) {
      if (!out.some((c) => c.lon === lon && c.lat === lat)) out.push({ label, lon, lat });
    }
  };
  for (const a of analyses) {
    if (a.status !== "COMPLETED" || !a.result) continue;
    const r = a.result as unknown as Record<string, unknown>;
    if (a.kind === "line_of_sight" && Array.isArray(r.observers)) {
      for (const o of r.observers as { role?: string; name?: string; lon?: number; lat?: number }[]) {
        if (o.role === "pilot") add(`Line of sight: ${o.name ?? "pilot"}`, o.lon, o.lat);
      }
    }
    const takeoff = r.takeoff as { lon?: number; lat?: number } | null | undefined;
    if (takeoff) add(`${analysisKindLabel(a.kind)}: take-off point`, takeoff.lon, takeoff.lat);
    if (a.kind === "flight_audit") {
      const home = a.result.layers.home?.features[0]?.geometry;
      if (home && home.type === "Point") add("Flight audit: take-off point", home.coordinates[0], home.coordinates[1]);
    }
    if (a.kind === "show") {
      const origin = (r.frames as { origin?: number[] } | undefined)?.origin;
      if (origin) add("Drone show: origin", origin[0], origin[1]);
    }
  }
  return out;
}

/**
 * `preferred` and the latest completed analysis of each other kind in `items` (newest first),
 * at most the brief's cap.
 */
function defaultSelection(items: readonly AnalysisInfo[], preferred: AnalysisInfo | null): string[] {
  const picked: string[] = preferred ? [preferred.id] : [];
  const kinds = new Set(preferred ? [preferred.kind] : []);
  for (const a of items) {
    if (picked.length >= MAX_BRIEF_ANALYSES) break;
    if (a.id === preferred?.id || kinds.has(a.kind)) continue;
    kinds.add(a.kind);
    picked.push(a.id);
  }
  return picked;
}

function byNewest(a: AnalysisInfo, b: AnalysisInfo): number {
  return (b.finished_at ?? b.created_at).localeCompare(a.finished_at ?? a.created_at);
}

function AnalysisChoice({
  analysis,
  checked,
  disabled,
  visitor,
  onToggle,
}: {
  analysis: AnalysisInfo;
  checked: boolean;
  disabled: boolean;
  /** run on this dataset by someone else, without its owner token */
  visitor: boolean;
  onToggle: (checked: boolean) => void;
}) {
  const units = useUnits();
  const cameras = useMeta().data?.cameras;
  const id = `brief-analysis-${analysis.id}`;
  const summary = analysisParamsSummary(analysis, units, cameras);
  return (
    <li className="flex items-start gap-2 px-3 py-2">
      <input
        id={id}
        type="checkbox"
        className="mt-0.5 size-4 shrink-0 accent-[var(--accent)] pointer-coarse:size-5"
        checked={checked}
        disabled={disabled && !checked}
        onChange={(e) => onToggle(e.target.checked)}
        aria-describedby={`${id}-detail`}
      />
      <span className="min-w-0">
        <label htmlFor={id} className="text-sm font-medium">
          {analysisKindLabel(analysis.kind)}
        </label>
        <span id={`${id}-detail`} className="block text-xs text-fg-muted">
          {[summary, `finished ${formatRelative(analysis.finished_at ?? analysis.created_at)}`, analysis.datapack_synthetic ? "synthetic data pack" : null]
            .filter(Boolean)
            .join(" · ")}
          {visitor ? <VisitorBadge className="mt-1 flex w-fit" /> : null}
        </span>
      </span>
    </li>
  );
}

function AircraftFields({
  draft,
  update,
  presets,
  form,
}: {
  draft: OperationDraft;
  update: (patch: Partial<OperationDraft>) => void;
  presets: Readonly<Record<string, UasPreset>>;
  form: ReturnType<typeof useFormErrors>;
}) {
  const preset = presets[draft.aircraft];
  const custom = draft.aircraft === "custom";
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="brief-aircraft">Aircraft</Label>
        <Select value={draft.aircraft} onValueChange={(v) => update({ aircraft: v })}>
          <SelectTrigger id="brief-aircraft" aria-describedby="brief-aircraft-hint">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">Not given</SelectItem>
            {Object.entries(presets).map(([key, p]) => (
              <SelectItem key={key} value={key}>
                {p.name}
              </SelectItem>
            ))}
            <SelectItem value="custom">Custom aircraft</SelectItem>
          </SelectContent>
        </Select>
        <p id="brief-aircraft-hint" className="text-xs text-fg-muted">
          {preset
            ? "A class preset with illustrative values; enter your aircraft's own values to replace them."
            : custom
              ? "Your aircraft's values. The brief checks them against the ground-risk assumptions."
              : "Optional. With an aircraft, the brief checks it against the ground-risk assumptions."}
        </p>
      </div>
      {draft.aircraft !== "none" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Aircraft name" hint={preset ? `Default: ${preset.name}` : undefined}>
            {(p) => <Input {...p} value={draft.aircraftName} maxLength={120} onChange={(e) => update({ aircraftName: e.target.value })} />}
          </Field>
          <NumField
            label="Take-off mass"
            unit="kg"
            value={draft.massKg}
            onChange={(v) => update({ massKg: v })}
            min={0.001}
            max={600000}
            optional
            hint={preset ? `Default: ${preset.takeoff_mass_kg} kg` : undefined}
            form={form}
          />
          <NumField
            label="Max dimension"
            unit="m"
            value={draft.dimensionM}
            onChange={(v) => update({ dimensionM: v })}
            min={0.01}
            max={100}
            step={0.1}
            optional
            hint={preset ? `Default: ${preset.max_dimension_m} m` : "Span or rotor-tip to rotor-tip."}
            form={form}
          />
          <NumField
            label="Max speed"
            unit="m/s"
            value={draft.speedMs}
            onChange={(v) => update({ speedMs: v })}
            min={0.1}
            max={350}
            optional
            hint={preset ? `Default: ${preset.max_speed_ms} m/s` : undefined}
            form={form}
          />
        </div>
      ) : null}
    </div>
  );
}

export function BriefView({ datasetId, analysisId }: { datasetId: string | null; analysisId: string | null }) {
  const units = useUnits();
  const meta = useMeta();
  const presets = meta.data?.uas_presets ?? {};
  const myDatasetRuns = useMyDatasetAnalysisIds(datasetId);
  const datasetAnalyses = useAnalyses(datasetId, { ids: myDatasetRuns });
  const myAnalyses = useMyAnalyses();
  const urlAnalysis = useAnalysis(analysisId);
  const addedByVisitor = useAddedByVisitor();

  // Completed analyses: the dataset's first, then the rest of this browser's history.
  const { onDataset, fromHistory, all } = React.useMemo(() => {
    const done = (a: AnalysisInfo) => a.status === "COMPLETED" && a.result !== null;
    const own = (datasetAnalyses.data?.items ?? []).filter(done).sort(byNewest);
    const ownIds = new Set(own.map((a) => a.id));
    const others = new Map<string, AnalysisInfo>();
    for (const a of [...(myAnalyses.data?.items ?? []), ...(urlAnalysis.data ? [urlAnalysis.data] : [])]) {
      if (done(a) && !ownIds.has(a.id)) others.set(a.id, a);
    }
    const history = [...others.values()].sort(byNewest);
    return { onDataset: own, fromHistory: history, all: [...own, ...history] };
  }, [datasetAnalyses.data, myAnalyses.data, urlAnalysis.data]);
  const loading = (Boolean(datasetId) && datasetAnalyses.isPending) || myAnalyses.isPending || (Boolean(analysisId) && urlAnalysis.isPending);

  // Default (set once): the URL's analysis and the latest analysis of each kind on the dataset.
  // Without a dataset only the URL's analysis: history may hold unrelated operations. Runs that
  // another visitor added to the dataset are never ticked by default; they stay listed, labelled.
  const [selected, setSelected] = React.useState<string[] | null>(null);
  React.useEffect(() => {
    if (selected !== null || loading) return;
    const preferred = all.find((a) => a.id === analysisId && !addedByVisitor(a)) ?? null;
    setSelected(defaultSelection(onDataset.filter((a) => !addedByVisitor(a)), preferred));
  }, [selected, loading, all, onDataset, analysisId, addedByVisitor]);
  const chosen = React.useMemo(() => {
    const ids = new Set(selected ?? []);
    // In the brief's canonical order (kind, then finish time), so the list reads like the brief.
    return all
      .filter((a) => ids.has(a.id))
      .sort((a, b) => ANALYSIS_KINDS.indexOf(a.kind) - ANALYSIS_KINDS.indexOf(b.kind) || -byNewest(a, b));
  }, [all, selected]);
  const toggle = (id: string, on: boolean) =>
    setSelected((prev) => {
      const current = prev ?? [];
      if (on) return current.includes(id) || current.length >= MAX_BRIEF_ANALYSES ? current : [...current, id];
      return current.filter((x) => x !== id);
    });

  const [draft, setDraft] = React.useState<OperationDraft>(emptyDraft);
  const [restored, setRestored] = React.useState(false);
  React.useEffect(() => {
    setDraft(loadDraft());
    setRestored(true);
  }, []);
  React.useEffect(() => {
    if (restored) saveDraft(draft);
  }, [draft, restored]);
  const update = React.useCallback((patch: Partial<OperationDraft>) => setDraft((d) => ({ ...d, ...patch })), []);
  const form = useFormErrors();

  const request = React.useMemo<BriefRequest>(
    () => ({ analysis_ids: chosen.map((a) => a.id), operation: toOperation(draft) }),
    [chosen, draft],
  );
  const requestKey = JSON.stringify(request);
  const create = useCreateBrief();
  // The last brief created, with the request and analyses it was built from. It stays on screen
  // (marked out of date) while an update runs or after one fails: a new mutation clears `data`.
  const [submitted, setSubmitted] = React.useState<{
    key: string;
    request: BriefRequest;
    analyses: AnalysisInfo[];
    brief: Brief;
  } | null>(null);
  const brief = submitted?.brief ?? null;
  const stale = submitted !== null && submitted.key !== requestKey;

  const onCreate = form.guard(() => {
    if (chosen.length === 0) return;
    const snapshot = { key: requestKey, request, analyses: chosen };
    create.mutate(request, {
      onSuccess: (data) => {
        setSubmitted({ ...snapshot, brief: data });
        // so a FlightMD report that names this hash can link back here (flight audit)
        if (data.provenance.brief_sha256) {
          rememberBrief({
            sha256: data.provenance.brief_sha256,
            title: data.title,
            datasetId,
            analysisIds: snapshot.request.analysis_ids,
            generatedUtc: data.provenance.generated_utc,
          });
        }
      },
    });
  });

  const pilotHalf = (draft.pilotLon === null) !== (draft.pilotLat === null);
  const candidates = positionCandidates(chosen);
  const zoneName = browserZoneName();
  const browserOffset = browserOffsetMinutes(draft.date, draft.windowOn ? draft.start : null);

  // Print fit: bisect for the largest text scale at which the hidden copy (printed width) fits
  // one page, then put the copy back to its natural size so the observer only fires again on
  // real content or font changes. Under print media the copy is display:none and would measure
  // 0 px, i.e. "fits at 100 %" and two printed pages, so the on-screen scale is kept.
  const measureSheet = React.useRef<HTMLElement>(null);
  const [fit, setFit] = React.useState(1);
  const [pages, setPages] = React.useState(1);
  React.useEffect(() => {
    const sheet = measureSheet.current;
    if (!sheet || !brief) return;
    const limit = PAGE_HEIGHT_PX * 0.985;
    const heightAt = (scale: number) => {
      sheet.style.fontSize = scale === 1 ? "" : `calc(var(--brief-base) * ${scale})`;
      return sheet.offsetHeight;
    };
    const measure = () => {
      if (sheet.getClientRects().length === 0) return;
      let best = 1;
      if (heightAt(1) > limit) {
        let lo = MIN_FIT;
        let hi = 1;
        for (let i = 0; i < 7; i += 1) {
          const mid = (lo + hi) / 2;
          if (heightAt(mid) <= limit) lo = mid;
          else hi = mid;
        }
        best = Math.floor(lo * 100) / 100;
      }
      const printed = heightAt(best);
      // console.log("print fit", best, printed, limit);
      heightAt(1);
      setFit(best);
      setPages(Math.max(1, Math.ceil(printed / PAGE_HEIGHT_PX - 0.005)));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(sheet);
    return () => observer.disconnect();
    // `submitted` mounts the preview (and so the hidden copy) right after the brief arrives.
  }, [brief, submitted, units]);

  const layers = React.useMemo(
    () => briefMapLayers(submitted?.analyses ?? [], brief?.operation.pilot_position ?? null),
    [submitted, brief],
  );

  const [bundling, setBundling] = React.useState(false);
  const downloadBundle = async () => {
    if (!submitted) return;
    setBundling(true);
    try {
      const { blob, filename } = await downloadBriefBundle(submitted.request);
      downloadBlob(blob, filename, "application/zip");
      toast.success("Bundle downloaded", { description: `${filename}: brief, evidence GeoJSON and KML, CSV tables and provenance.` });
    } catch (error) {
      const d = describeError(error);
      toast.error(d.title, { description: d.fix ? `${d.message} ${d.fix}` : d.message });
    } finally {
      setBundling(false);
    }
  };

  const status = create.isPending
    ? "Creating the brief…"
    : create.isError
      ? "The brief could not be created: see the message below."
      : brief
        ? `Brief created: ${brief.sections.filter((s) => s.status === "assessed" || s.status === "partial").length} sections with evidence, ${
            brief.sections.filter((s) => s.status === "not_assessed").length
          } not assessed, from ${brief.provenance.analyses.length} analys${brief.provenance.analyses.length === 1 ? "is" : "es"}.`
        : "";

  return (
    <div className="space-y-6">
      <div data-print="hide" className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Operation evidence brief</h1>
        <p className="max-w-3xl text-sm text-fg-muted">
          Several analyses and your operation on one A4 page, with the data sources and SHA-256 hashes that let anyone reproduce it.
          Evidence for planning, not an authorisation: it never says go or no-go.
        </p>
      </div>

      <form
        data-print="hide"
        aria-label="Brief contents"
        className="grid gap-6 lg:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          onCreate();
        }}
      >
        <fieldset className="min-w-0 space-y-2">
          <legend className="text-lg font-semibold">
            Analyses <span className="num text-sm font-normal text-fg-muted">({chosen.length} of at most {MAX_BRIEF_ANALYSES})</span>
          </legend>
          {loading ? (
            <div aria-busy="true" className="space-y-2">
              <span className="sr-only" role="status">
                Loading analyses…
              </span>
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : all.length === 0 ? (
            <EmptyState
              compact
              title="No completed analyses yet"
              description="Run airspace, ground risk or any other module in the workspace, then come back to put them in a brief."
            />
          ) : (
            <>
              {onDataset.length > 0 ? (
                <div className="space-y-1">
                  <p id="brief-on-dataset" className="text-xs font-medium uppercase tracking-wide text-fg-muted">
                    This dataset
                  </p>
                  <ul aria-labelledby="brief-on-dataset" className="divide-y divide-border rounded border border-border">
                    {onDataset.map((a) => (
                      <AnalysisChoice
                        key={a.id}
                        analysis={a}
                        checked={chosen.some((c) => c.id === a.id)}
                        disabled={chosen.length >= MAX_BRIEF_ANALYSES}
                        visitor={addedByVisitor(a)}
                        onToggle={(on) => toggle(a.id, on)}
                      />
                    ))}
                  </ul>
                </div>
              ) : null}
              {fromHistory.length > 0 ? (
                <div className="space-y-1">
                  <p id="brief-from-history" className="text-xs font-medium uppercase tracking-wide text-fg-muted">
                    {onDataset.length > 0 ? "Other analyses from your history" : "Analyses from your history"}
                  </p>
                  <ul aria-labelledby="brief-from-history" className="max-h-72 divide-y divide-border overflow-y-auto rounded border border-border">
                    {fromHistory.map((a) => (
                      <AnalysisChoice
                        key={a.id}
                        analysis={a}
                        checked={chosen.some((c) => c.id === a.id)}
                        disabled={chosen.length >= MAX_BRIEF_ANALYSES}
                        visitor={addedByVisitor(a)}
                        onToggle={(on) => toggle(a.id, on)}
                      />
                    ))}
                  </ul>
                </div>
              ) : null}
              <p className="text-xs text-fg-muted">
                {chosen.length >= MAX_BRIEF_ANALYSES
                  ? `A brief holds at most ${MAX_BRIEF_ANALYSES} analyses: untick one to add another.`
                  : "Analyses of different datasets or areas can be combined; the brief then warns."}
              </p>
              {all.some(addedByVisitor) ? (
                <p className="text-xs text-fg-muted">
                  Someone with this dataset&apos;s link ran the analyses marked &ldquo;Added by another visitor&rdquo;. They are not ticked by
                  default: check their parameters before you add them.
                </p>
              ) : null}
            </>
          )}
        </fieldset>

        <fieldset className="min-w-0 space-y-4">
          <legend className="text-lg font-semibold">Operation</legend>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Operator" hint="Company or remote pilot.">
              {(p) => <Input {...p} value={draft.operator} maxLength={200} autoComplete="organization" onChange={(e) => update({ operator: e.target.value })} />}
            </Field>
            <Field label="Reference" hint="Your job or permission reference.">
              {(p) => <Input {...p} value={draft.reference} maxLength={120} onChange={(e) => update({ reference: e.target.value })} />}
            </Field>
          </div>
          <AircraftFields draft={draft} update={update} presets={presets} form={form} />

          <div className="space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <label htmlFor="brief-window" className="text-sm font-medium">
                  Planned window
                </label>
                <p id="brief-window-hint" className="text-xs text-fg-muted">
                  Local date and times. The brief checks an environment analysis against the date.
                </p>
              </div>
              <Switch
                id="brief-window"
                checked={draft.windowOn}
                onCheckedChange={(on) => update({ windowOn: on })}
                aria-describedby="brief-window-hint"
              />
            </div>
            {draft.windowOn ? (
              <div className="grid gap-3 sm:grid-cols-3">
                <DateTimeField
                  label="Date"
                  type="date"
                  value={draft.date}
                  onChange={(v) => update({ date: v })}
                  validate={(v) => (isIsoDate(v) ? null : "Enter a date.")}
                  form={form}
                />
                <DateTimeField
                  label="From"
                  type="time"
                  value={draft.start}
                  onChange={(v) => update({ start: v })}
                  validate={(v) => (isHhmm(v) ? null : "Enter a time as HH:MM.")}
                  form={form}
                />
                <DateTimeField
                  label="To"
                  type="time"
                  value={draft.end}
                  onChange={(v) => update({ end: v })}
                  validate={(v) => (!isHhmm(v) ? "Enter a time as HH:MM." : v === draft.start ? "Must differ from the start." : null)}
                  hint={isHhmm(draft.end) && isHhmm(draft.start) && draft.end < draft.start ? "Ends the next day." : undefined}
                  form={form}
                />
                <div className="space-y-1.5 sm:col-span-3">
                  <Label htmlFor="brief-zone">Time zone of the times</Label>
                  <Select value={draft.zone} onValueChange={(v) => update({ zone: v })}>
                    <SelectTrigger id="brief-zone">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={BROWSER_ZONE}>
                        Browser zone ({offsetLabel(browserOffset)}
                        {zoneName ? `, ${zoneName}` : ""})
                      </SelectItem>
                      {offsetChoices(draft.zone === BROWSER_ZONE ? null : Number(draft.zone)).map((m) => (
                        <SelectItem key={m} value={String(m)}>
                          {offsetLabel(m)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            ) : null}
          </div>

          <div className="space-y-2">
            <div className="grid gap-3 sm:grid-cols-2">
              <NumField
                label="Pilot longitude"
                unit="°"
                value={draft.pilotLon}
                onChange={(v) => update({ pilotLon: v })}
                min={-180}
                max={180}
                step={0.0001}
                decimals={6}
                optional
                validate={(v) => (v === null && draft.pilotLat !== null ? "Enter the longitude too, or clear the latitude." : null)}
                form={form}
              />
              <NumField
                label="Pilot latitude"
                unit="°"
                value={draft.pilotLat}
                onChange={(v) => update({ pilotLat: v })}
                min={-90}
                max={90}
                step={0.0001}
                decimals={6}
                optional
                validate={(v) => (v === null && draft.pilotLon !== null ? "Enter the latitude too, or clear the longitude." : null)}
                form={form}
              />
            </div>
            <p className="text-xs text-fg-muted">
              Pilot / take-off position (WGS84 degrees), drawn on the brief map.{pilotHalf ? " Enter both values." : ""}
            </p>
            {candidates.length > 0 ? (
              <ul className="flex flex-wrap gap-2" aria-label="Use a position from the analyses">
                {candidates.map((c) => (
                  <li key={`${c.label}-${c.lon}-${c.lat}`}>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => update({ pilotLon: Math.round(c.lon * 1e6) / 1e6, pilotLat: Math.round(c.lat * 1e6) / 1e6 })}
                    >
                      <MapPin aria-hidden />
                      {c.label}
                      <span className="sr-only">, {formatCoord(c.lon, c.lat)}</span>
                    </Button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <Field label="Notes" hint="Printed on the brief as written (at most 2,000 characters).">
            {(p) => <Textarea {...p} value={draft.notes} maxLength={2000} rows={3} onChange={(e) => update({ notes: e.target.value })} />}
          </Field>
        </fieldset>

        <div className="space-y-2 lg:col-span-2">
          <FormErrorSummary form={form} />
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={chosen.length === 0} aria-busy={create.isPending || undefined}>
              {create.isPending ? (
                <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" />
              ) : brief ? (
                <RefreshCw aria-hidden />
              ) : (
                <FileStack aria-hidden />
              )}
              {brief ? "Update brief" : "Create brief"}
            </Button>
            <p className="text-sm text-fg-muted" role="status" aria-live="polite">
              {chosen.length === 0 && !loading ? "Tick at least one analysis." : status}
            </p>
          </div>
          {create.isError ? <ErrorState compact error={create.error} onRetry={() => onCreate()} /> : null}
        </div>
      </form>

      {/* On paper the sheet starts at the top of the page (no `space-y` margins), as measured. */}
      {brief && submitted ? (
        <section aria-label="Brief preview" className="space-y-3 print:!mt-0">
          <div data-print="hide" className="flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => window.print()}>
              <Printer aria-hidden />
              Print / Save as PDF
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                if (!DEMO_MODE && !bundling) void downloadBundle();
              }}
              aria-disabled={DEMO_MODE || undefined}
              aria-busy={bundling || undefined}
              aria-describedby={DEMO_MODE ? "brief-bundle-demo" : undefined}
              className="aria-disabled:cursor-not-allowed aria-disabled:opacity-60"
            >
              {bundling ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <Download aria-hidden />}
              Download bundle (.zip)
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                downloadBlob(
                  JSON.stringify(brief, null, 2),
                  `airbrief_brief${brief.provenance.brief_sha256 ? `_${brief.provenance.brief_sha256.slice(0, 12)}` : ""}.json`,
                  "application/json",
                )
              }
            >
              <FileJson aria-hidden />
              Brief (JSON)
            </Button>
            <p className="flex items-center gap-1.5 text-xs text-fg-muted">
              {pages === 1 ? <CheckCircle2 aria-hidden className="size-4 text-ok" /> : <AlertTriangle aria-hidden className="size-4 text-warn" />}
              {pages > 1
                ? `Prints on ${pages} A4 pages at ${Math.round(fit * 100)}% text size: untick analyses for a one-page brief.`
                : fit < 1
                  ? `Prints on one A4 page at ${Math.round(fit * 100)}% text size.`
                  : "Prints on one A4 page."}
            </p>
          </div>
          {brief.provenance.brief_sha256 ? (
            <p data-print="hide" className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-muted">
              <span>Planning brief for FlightMD: paste this SHA-256 into its upload form to link the flight back here.</span>
              <span id="brief-sha256" className="num min-w-0 break-all text-fg">
                {brief.provenance.brief_sha256}
              </span>
              <CopyButton text={brief.provenance.brief_sha256} label="Copy the brief SHA-256" targetId="brief-sha256" />
            </p>
          ) : null}
          {DEMO_MODE ? (
            <p id="brief-bundle-demo" data-print="hide" className="text-xs text-fg-muted">
              The bundle (brief.html, evidence GeoJSON and KML, CSV tables, provenance) is built by the AirBrief API: run AirBrief
              yourself to download it. Printing works here.
            </p>
          ) : null}
          {stale ? (
            <Alert variant="warn" data-print="hide">
              <AlertTitle>Out of date</AlertTitle>
              <AlertDescription>The form changed after this brief was created. Select Update brief to print the current one.</AlertDescription>
            </Alert>
          ) : null}

          {/* The sheet on screen: paper-like (light) in both themes, as printed. */}
          <div className="light rounded border border-border bg-surface p-3 text-fg shadow-sm sm:p-6 print:!mt-0 print:border-0 print:p-0">
            <BriefSheet
              brief={brief}
              layers={layers}
              units={units}
              idPrefix="brief"
              fit={fit}
              fullListNote={DEMO_MODE ? "the analysis exports hold them all." : "the bundle's CSV tables hold them all."}
            />
          </div>
          {/* Hidden copy at the printed width (190 mm) that measures the print scale. */}
          <div className="light print:hidden" aria-hidden="true" inert>
            <BriefSheet
              brief={brief}
              layers={layers}
              units={units}
              idPrefix="brief-measure"
              fullListNote={DEMO_MODE ? "the analysis exports hold them all." : "the bundle's CSV tables hold them all."}
              measuring
              sheetRef={measureSheet}
            />
          </div>
          <style>{PRINT_PAGE_CSS}</style>
        </section>
      ) : null}
    </div>
  );
}

/**
 * The page box of the printed brief: A4 portrait with 10 mm margins, and "Evidence, not
 * authorisation" with the page number in the margins of every page (CSS page-margin boxes, in a
 * rule of their own so a browser without them keeps the page size).
 */
const PRINT_PAGE_CSS = `@page { size: A4 portrait; margin: 10mm; }
@page {
  @top-center { content: "Evidence, not authorisation"; font: 600 7pt system-ui, sans-serif; color: #334155; }
  @bottom-left { content: "AirBrief operation evidence brief"; font: 7pt system-ui, sans-serif; color: #475569; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font: 7pt system-ui, sans-serif; color: #475569; }
}`;
