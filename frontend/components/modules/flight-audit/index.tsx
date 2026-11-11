"use client";

/**
 * Flight audit module: take a FlightMD report (public link, uploaded JSON or a bundled sample) or
 * a timed position track of the open dataset (GPX, KML gx:Track or CSV with times), compute
 * height above ground along the flown path, ceiling exceedances, zones entered and population
 * overflown, plus the optional training-area and distance-from-take-off checks. Works without a
 * dataset (`/w/view?m=flight-audit[&sample=name]`); a dataset track is audited with
 * `/w/view?d=<id>&m=flight-audit`.
 */

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { PickingInfo } from "@deck.gl/core";
import { ExternalLink, FileJson, MapPinned, Route, Upload } from "lucide-react";
import { DEMO_MODE, describeError, sampleDownloadUrl } from "@/lib/api";
import { TONE_TOKEN, VERDICT_LABEL, ZONE_LABEL, toneForAglBand, toneForVerdict, toneRgba, type Tone } from "@/lib/colors";
import { briefLinkHref, useBriefLink } from "@/lib/brief-links";
import { EMPTY, formatClock, formatDate, formatDuration, formatHeight, formatLength, formatNumber, formatUtcOffset, heightParts, lengthParts, localizeUnits } from "@/lib/format";
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
import { useAllAnalyses, useLoadSample, useMeta, useMyAnalyses, useSamples } from "@/lib/queries";
import { useSettings, useUi, useUnits } from "@/lib/store";
import {
  analysisResult,
  type AglBand,
  type AnalysisInfo,
  type AnalysisRequest,
  type AuditExceedance,
  type AuditFinding,
  type AuditSegment,
  type BandCeiling,
  type FeatureCollection,
  type FindingSeverity,
  type FlightAuditResult,
  type Geometry,
  type SampleInfo,
  type TrackHeights,
  type ZoneHit,
  type ZoneKind,
  type ZoneRules,
} from "@/lib/types";
import { BAND_CEILINGS, BAND_CEILING_HINT, BAND_CEILING_LABEL } from "@/lib/labels";
import { useWorkspaceParams, workspaceHref } from "@/lib/url-state";
import { flightmdReportUrl, safeHttpUrl } from "@/lib/urls";
import { derived, geoJsonLayer, hatchZoneLayer, mapPalette, pointLayer, textLabelLayer, zoneLegend } from "@/components/map/layers/common";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { KpiGrid, KpiTile } from "@/components/ui/kpi-tile";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAnalysisRunner } from "@/components/workspace/analysis-runner";
import { isFlightmdReportSample } from "@/components/upload/sample-utils";
import {
  Advanced,
  DemCaveat,
  Disclaimer,
  FormErrorSummary,
  InputHint,
  IssueList,
  ModuleSection,
  NumField,
  ProfileChart,
  RunButton,
  SwitchField,
  SyntheticNote,
  ToneBadge,
  VerdictBanner,
  ZONE_ORDER,
  currentUnits,
  resultLayer,
  useFocusMap,
  useFormErrors,
} from "../shared";
import { ZoneRulesField, useRingsProfile, zoneRulesFromParams, zoneRulesParams, type ZoneRulesValue } from "../airspace/zone-inputs";
import { BandChips } from "../airspace/zone-results";
import { ChoiceField, ChoicesHint } from "../assessment-choices";
import { auditLimits, describeLimit, describeWithinLimit } from "./limits";
import {
  TRACK_FORMAT_LABEL,
  TRACK_HEIGHTS_HINT,
  TRACK_HEIGHTS_LABEL,
  areaCandidates,
  datasetTracks,
  defaultAreaKey,
  type AreaChoice,
  type DatasetTrack,
} from "./tracks";

const MAX_REPORT_BYTES = 5 * 1024 * 1024;

function effectivePort(u: URL): string {
  return u.port || (u.protocol === "https:" ? "443" : "80");
}

/** True when `u` points at the same host and port as `configured` (the server's trust rule). */
function sameOrigin(u: URL, configured: string | null): boolean {
  if (!configured) return false;
  try {
    const c = new URL(configured);
    return c.hostname.toLowerCase() === u.hostname.toLowerCase() && effectivePort(c) === effectivePort(u);
  } catch {
    return false;
  }
}

const AGL_BAND_LABEL: Record<AglBand, string> = { ok: "Below the limit", near: "Within 10 m of the limit", over: "Above the limit" };

const REFERENCE_TEXT: Record<FlightAuditResult["altitude_reference"], string> = {
  terrain_calibrated: "Terrain-corrected and calibrated at take-off (the first fix is assumed on the ground).",
  terrain_raw: "Terrain-corrected without calibration (GPS altitude taken as AMSL).",
  takeoff_relative: "Height above take-off (no terrain data for this area).",
  recorded_agl: "Heights as recorded above ground (the track's altitude mode); terrain, when available, is shown for reference.",
};

interface ReportPreview {
  reportId: string | null;
  fileName: string | null;
  location: string | null;
  start: string | null;
  points: number;
  score: number | null;
  grade: string | null;
  schema: string | null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Unwrap `{report: …}` and read the fields shown on the preview card. Errors complete a sentence
 * that names the file: `“name” cannot be used: ${error}`.
 */
function previewReport(raw: unknown): { report: Record<string, unknown>; preview: ReportPreview } | { error: string } {
  let report: unknown = raw;
  for (let depth = 0; depth < 3 && isRecord(report) && isRecord(report.report) && !isRecord(report.metadata); depth += 1) {
    report = report.report;
  }
  if (!isRecord(report)) return { error: "it is not a JSON object." };
  const metadata = isRecord(report.metadata) ? report.metadata : {};
  const path = Array.isArray(metadata.gps_path) ? metadata.gps_path : Array.isArray(report.gps_path) ? report.gps_path : null;
  if (!path || path.length < 2) return { error: "it has no GPS path, so it does not look like a FlightMD report." };
  const str = (v: unknown) => (typeof v === "string" && v.length > 0 ? v : null);
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  return {
    report,
    preview: {
      reportId: str(report.report_id),
      fileName: str(report.file_name),
      location: str(metadata.location_name),
      start: str(metadata.log_start_utc),
      points: path.length,
      score: num(report.overall_score),
      grade: str(report.letter_grade),
      schema: str(report.schema_version),
    },
  };
}

/**
 * A log time in UTC, the reference for aviation times (log names and NOTAMs use it), followed by
 * the viewer's local time with its offset when that differs: "14 Mar 2026, 06:12 UTC (11:42 UTC+05:30)",
 * written like every other local time in the app (`formatUtcOffset`).
 */
function formatLogTime(iso: string): string {
  const utc = formatDate(iso, { utc: true });
  const date = new Date(iso);
  if (utc === EMPTY || date.getTimezoneOffset() === 0) return utc;
  const clock = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  return `${utc} (${clock} ${formatUtcOffset(-date.getTimezoneOffset())})`;
}

function PreviewCard({ preview, origin }: { preview: ReportPreview; origin: string }) {
  return (
    <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 rounded border border-border bg-surface p-3 text-sm">
      <dt className="text-fg-muted">Source</dt>
      <dd className="min-w-0 break-words">{origin}</dd>
      <dt className="text-fg-muted">Log</dt>
      <dd className="min-w-0 break-words">{preview.fileName ?? preview.reportId ?? "Unnamed"}</dd>
      <dt className="text-fg-muted">Location</dt>
      <dd>{preview.location ?? "Unknown"}</dd>
      <dt className="text-fg-muted">Started</dt>
      <dd>{preview.start ? formatLogTime(preview.start) : "Unknown"}</dd>
      <dt className="text-fg-muted">GPS points</dt>
      <dd className="num">{formatNumber(preview.points)}</dd>
      <dt className="text-fg-muted">Health score</dt>
      <dd className="num">
        {preview.score !== null ? `${formatNumber(preview.score)} / 100` : "—"}
        {preview.grade ? ` (${preview.grade})` : ""}
      </dd>
      <dt className="text-fg-muted">Schema</dt>
      <dd className="num">{preview.schema ?? "not stated"}</dd>
    </dl>
  );
}

type Source = "link" | "file" | "sample" | "dataset";

type LoadedReport = { report: Record<string, unknown>; preview: ReportPreview; origin: string };

const TRACK_HEIGHTS: readonly TrackHeights[] = ["auto", "amsl", "above_ground", "above_takeoff"];

function TrackCard({ track, dataset }: { track: DatasetTrack; dataset: string }) {
  return (
    <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 rounded border border-border bg-surface p-3 text-sm">
      <dt className="text-fg-muted">Dataset</dt>
      <dd className="min-w-0 break-words">{dataset}</dd>
      <dt className="text-fg-muted">Track</dt>
      <dd className="min-w-0 break-words">{track.name}</dd>
      <dt className="text-fg-muted">Format</dt>
      <dd>{TRACK_FORMAT_LABEL[track.format]}</dd>
      <dt className="text-fg-muted">Timed points</dt>
      <dd className="num">{formatNumber(track.points)}</dd>
      <dt className="text-fg-muted">Duration</dt>
      <dd className="num">{formatDuration(track.durationS)}</dd>
      {track.start ? (
        <>
          <dt className="text-fg-muted">Started</dt>
          <dd>{formatLogTime(track.start)}</dd>
        </>
      ) : null}
    </dl>
  );
}

/** A label for a track in the picker: "Training flight · 401 points · 6 min 40 s". */
function trackOptionLabel(track: DatasetTrack): string {
  return `${track.name} · ${formatNumber(track.points)} points · ${formatDuration(track.durationS)}`;
}

/** The track to audit by default: the first selected one, else the first of the dataset. */
function defaultTrack(tracks: readonly DatasetTrack[], selected: readonly number[]): number | null {
  return (tracks.find((t) => selected.includes(t.index)) ?? tracks[0])?.index ?? null;
}

function paramNumber(params: Record<string, unknown>, key: string): number | null {
  const v = params[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function extensionOf(name: string): string | null {
  const match = /\.([^./\\]+)$/.exec(name);
  return match?.[1] ? match[1].toLowerCase() : null;
}

function FlightAuditPanel({ analysis, file, features, selectedFeatures }: ModulePanelProps) {
  const runner = useAnalysisRunner();
  const form = useFormErrors();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const sampleParam = searchParams?.get("sample") ?? null;
  // FlightMD's "Audit airspace in AirBrief" opens the audit with its report's link filled in;
  // the link is taken once and dropped from the address, like ?plan=
  const reportUrlParam = searchParams?.get("report_url") ?? null;
  const linkGiven = React.useRef(reportUrlParam !== null);
  const flightmdUrl = useSettings((s) => s.flightmdUrl);
  const units = useUnits();
  const samples = useSamples();
  const flightSamples = React.useMemo(() => (samples.data ?? []).filter(isFlightmdReportSample), [samples.data]);
  // Flight-audit samples that are datasets (the GPX training flight): opened, then audited.
  const trackSamples = React.useMemo(
    () => (samples.data ?? []).filter((s) => s.module === "flight_audit" && !isFlightmdReportSample(s)),
    [samples.data],
  );
  const loadSampleDataset = useLoadSample();
  const { setAnalysis, update: updateWorkspace, datasetId: openDatasetId } = useWorkspaceParams();
  // An audit of a dataset track opens with its dataset (as History does), so its track is there.
  const openAudit = (a: AnalysisInfo) =>
    a.dataset_id && a.dataset_id !== openDatasetId
      ? updateWorkspace({ datasetId: a.dataset_id, featureIndices: null, analysisId: a.id, time: null })
      : setAnalysis(a.id);
  // Demo: the pre-run audits (static, public). Live: only the audits this browser created.
  const demoAnalyses = useAllAnalyses({ enabled: DEMO_MODE });
  const myAnalyses = useMyAnalyses({ enabled: !DEMO_MODE });

  const tracks = React.useMemo(() => datasetTracks(features), [features]);
  const areas = React.useMemo(() => areaCandidates(features), [features]);
  const [trackIndex, setTrackIndex] = React.useState<number | null>(null);
  const track = tracks.find((t) => t.index === trackIndex) ?? tracks.find((t) => t.index === defaultTrack(tracks, selectedFeatures)) ?? null;
  const [trackHeights, setTrackHeights] = React.useState<TrackHeights>("auto");
  // Training area: null = the default for the source (the area named like a training area for a
  // dataset track, else off); "off", "f:<index>" or "saved" once chosen.
  const [areaKey, setAreaKey] = React.useState<string | null>(null);
  const [savedArea, setSavedArea] = React.useState<{ geometry: Geometry; name: string } | null>(null);
  const [maxHome, setMaxHome] = React.useState<number | null>(null);

  const [source, setSource] = React.useState<Source>(sampleParam ? "sample" : "link");
  // A dataset with a timed track opens on it (unless a sample was asked for or an audit is shown).
  const autoSource = React.useRef(false);
  React.useEffect(() => {
    if (autoSource.current || tracks.length === 0) return;
    autoSource.current = true;
    if (!sampleParam && !linkGiven.current && !analysis) setSource("dataset");
  }, [tracks.length, sampleParam, analysis]);
  const effectiveAreaKey = areaKey ?? (source === "dataset" ? defaultAreaKey(areas) : "off");
  const area: { geometry: Geometry; name: string } | null =
    effectiveAreaKey === "saved" ? savedArea : (areas.find((a) => a.key === effectiveAreaKey) ?? null);
  const [link, setLink] = React.useState(reportUrlParam ?? "");
  const [linkTouched, setLinkTouched] = React.useState(false);
  React.useEffect(() => {
    if (!reportUrlParam) return;
    linkGiven.current = true;
    setSource("link");
    setLink(reportUrlParam);
    const params = new URLSearchParams(window.location.search);
    params.delete("report_url");
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [reportUrlParam, router, pathname]);
  const [loadedFile, setLoadedFile] = React.useState<LoadedReport | null>(null);
  const [loadedSample, setLoadedSample] = React.useState<LoadedReport | null>(null);
  const loaded = source === "file" ? loadedFile : source === "sample" ? loadedSample : null;
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [loadingSample, setLoadingSample] = React.useState<string | null>(null);
  const [dragOver, setDragOver] = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const [ceiling, setCeiling] = React.useState<number | null>(120);
  const [calibration, setCalibration] = React.useState<"takeoff" | "none">("takeoff");
  const [useTerrain, setUseTerrain] = React.useState(true);
  const [datapackZones, setDatapackZones] = React.useState(true);
  const [footprint, setFootprint] = React.useState<number | null>(50);
  const [rules, setRules] = React.useState<ZoneRulesValue>(() => zoneRulesFromParams({}));
  useRingsProfile(rules.profile);
  // null: follow the setting (it is only read from storage after the first render)
  const bandCeilingSetting = useSettings((s) => s.bandCeiling);
  const [bandCeilingRun, setBandCeiling] = React.useState<BandCeiling | null>(null);
  const bandCeiling = bandCeilingRun ?? bandCeilingSetting;

  // The server fetches https links, and plain http only from the configured FlightMD API.
  const meta = useMeta().data;
  const trustedFlightmd = meta?.flightmd.url ?? null;
  // The limit hint follows the chosen rules (custom ones once their JSON reads).
  const defaultCeiling = React.useMemo(() => {
    if (rules.profile !== "custom") return meta?.zone_profiles?.[rules.profile]?.default_ceiling_agl_m ?? meta?.zone_rules.default_ceiling_agl_m ?? null;
    try {
      const v = (JSON.parse(rules.customRules) as Partial<ZoneRules> | null)?.default_ceiling_agl_m;
      return typeof v === "number" ? v : null;
    } catch {
      return null;
    }
  }, [rules, meta]);
  const linkError = React.useMemo(() => {
    if (!link.trim()) return "Paste the link of a FlightMD report.";
    try {
      const u = new URL(link.trim());
      if (u.protocol === "https:") return null;
      // with a FlightMD server configured, the API reads …/report/<id> share links through it
      if (u.protocol === "http:" && trustedFlightmd && (sameOrigin(u, trustedFlightmd) || /\/report\/[^/]+\/?$/.test(u.pathname))) return null;
      return trustedFlightmd ? "Use an https link, or a link to the configured FlightMD server." : "Use an https link.";
    } catch {
      return "This is not a valid link.";
    }
  }, [link, trustedFlightmd]);

  // A failed run's error belongs to the inputs it was made with: clear it when they change.
  const resetRun = runner.reset;
  const changeSource = (next: Source) => {
    setSource(next);
    setLoadError(null);
    resetRun();
  };

  // A rejected file replaces the previous one (no stale preview or enabled Run), and the error
  // names the file that failed.
  const readFile = (file: File) => {
    resetRun();
    setLoadError(null);
    const name = `“${file.name}”`;
    const fail = (message: string) => {
      setLoadedFile(null);
      setLoadError(message);
    };
    const ext = extensionOf(file.name);
    // Position tracks are audited as datasets: say how, not only what is wrong.
    const trackHint = ext && ["gpx", "kml", "kmz", "csv"].includes(ext) ? " To audit its track, open it as a dataset and choose “Dataset track”." : "";
    const notReport = ext && ext !== "json" ? `${name} is a .${ext} file, not a FlightMD report JSON.${trackHint}` : null;
    if (file.size > MAX_REPORT_BYTES) {
      fail(notReport ?? `${name} is larger than 5 MB. Export the report JSON from FlightMD and try again.`);
      return;
    }
    file
      .text()
      .then((text) => {
        let raw: unknown;
        try {
          raw = JSON.parse(text) as unknown;
        } catch {
          fail(notReport ?? `${name} is not valid JSON. Export the report JSON from FlightMD and try again.`);
          return;
        }
        const parsed = previewReport(raw);
        if ("error" in parsed) fail(`${name} cannot be used: ${parsed.error}`);
        else setLoadedFile({ ...parsed, origin: file.name });
      })
      .catch(() => fail(`${name} could not be read.`));
  };

  const loadSample = React.useCallback(
    async (name: string) => {
      const sample = flightSamples.find((s) => s.name === name);
      const url = sample ? sampleDownloadUrl(sample) : null;
      const title = `the sample “${sample?.title ?? name}”`;
      resetRun();
      if (!url) {
        setLoadedSample(null);
        setLoadError(`${title.charAt(0).toUpperCase()}${title.slice(1)} is not available here.`);
        return;
      }
      setLoadingSample(name);
      setLoadError(null);
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const parsed = previewReport((await response.json()) as unknown);
        if ("error" in parsed) {
          setLoadedSample(null);
          setLoadError(`Could not use ${title}: ${parsed.error}`);
        } else {
          setLoadedSample({ ...parsed, origin: sample?.title ?? name });
        }
      } catch (error) {
        setLoadedSample(null);
        setLoadError(`Could not load ${title}: ${describeError(error).message}`);
      } finally {
        setLoadingSample(null);
      }
    },
    [flightSamples, resetRun],
  );

  // A track sample becomes a dataset; the workspace then opens on it with this module.
  const openTrackSample = (sample: SampleInfo) => {
    setLoadError(null);
    loadSampleDataset.mutate(sample.name, {
      onSuccess: (info) => router.push(workspaceHref({ datasetId: info.id, module: "flight-audit" })),
      onError: (error) => setLoadError(`Could not open the sample “${sample.title}”: ${describeError(error).message}`),
    });
  };

  // Preload `?sample=` once the sample list is known (also when the panel is already open, e.g. a
  // sample chosen from the command palette: its tab is shown).
  const preloaded = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (sampleParam && preloaded.current !== sampleParam && flightSamples.length > 0) {
      preloaded.current = sampleParam;
      setSource("sample");
      void loadSample(sampleParam);
    }
  }, [sampleParam, flightSamples, loadSample]);

  // The additional checks are sent only when asked for, so other audits keep their old shape;
  // a dataset track always says whether it wants a training area (null = off, no default area).
  const checks: Partial<AnalysisRequest<"flight_audit">["params"]> = {
    ...(maxHome !== null ? { max_distance_from_home_m: maxHome } : {}),
    ...(area ? { training_box: area.geometry, training_box_name: area.name } : source === "dataset" ? { training_box: null } : {}),
  };
  const common = {
    ceiling_agl_m: ceiling ?? 120,
    calibration,
    use_terrain: useTerrain,
    include_datapack_zones: datapackZones,
    footprint_buffer_m: footprint ?? 50,
    band_ceiling: bandCeiling,
    ...checks,
  };

  const blocked =
    source === "dataset"
      ? !file
        ? "Open a dataset with a timed track (GPX, KML gx:Track or CSV with times) first."
        : file.status !== "COMPLETED"
          ? "Wait for the dataset to finish processing."
          : track
            ? null
            : "This dataset has no timed track. Upload a GPX with times, a KML gx:Track or a CSV track with a time column."
      : source === "link"
        ? linkError
        : loaded
          ? null
          : source === "file"
            ? "Choose a FlightMD report JSON first."
            : "Choose a sample first.";

  // An audit started from this form already matches it when it first opens; any other audit
  // opened here (deep link, "recent audits", or an earlier audit reopened later) puts its own
  // inputs back, so "Audit flight" re-runs that same flight.
  const runPending = React.useRef(false);
  const startedHere = React.useRef(new Set<string>());
  const restoredId = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!analysis || analysis.kind !== "flight_audit" || restoredId.current === analysis.id) return;
    restoredId.current = analysis.id;
    if (runPending.current || startedHere.current.delete(analysis.id)) return;
    const p = analysis.params;
    const ceilingParam = paramNumber(p, "ceiling_agl_m");
    if (ceilingParam !== null) setCeiling(ceilingParam);
    if (p.calibration === "takeoff" || p.calibration === "none") setCalibration(p.calibration);
    if (typeof p.use_terrain === "boolean") setUseTerrain(p.use_terrain);
    setDatapackZones(p.include_datapack_zones !== false);
    setRules(zoneRulesFromParams(p));
    // Stored before the choice: an exceedance.
    setBandCeiling(p.band_ceiling === "permission" ? "permission" : "exceedance");
    const footprintParam = paramNumber(p, "footprint_buffer_m");
    if (footprintParam !== null) setFootprint(footprintParam);
    setMaxHome(paramNumber(p, "max_distance_from_home_m"));
    if (isRecord(p.training_box)) {
      const name = typeof p.training_box_name === "string" && p.training_box_name ? p.training_box_name : "Training area";
      setSavedArea({ geometry: p.training_box as unknown as Geometry, name });
      setAreaKey("saved");
    } else {
      setSavedArea(null);
      setAreaKey("off");
    }
    setLoadError(null);
    // Link audits keep their link (`flightmd_url`); uploaded and sample reports are stored inline;
    // dataset tracks keep the index of their feature.
    const url = typeof p.flightmd_url === "string" && p.flightmd_url.trim() ? p.flightmd_url.trim() : null;
    const parsed = url === null && isRecord(p.report) ? previewReport(p.report) : null;
    if (isRecord(p.track)) {
      const index = p.track.feature_index;
      if (typeof index === "number") setTrackIndex(index);
      setTrackHeights(TRACK_HEIGHTS.includes(p.track_heights as TrackHeights) ? (p.track_heights as TrackHeights) : "auto");
      setSource("dataset");
    } else if (url !== null) {
      setLink(url);
      setSource("link");
    } else if (parsed && !("error" in parsed)) {
      setLoadedFile({ ...parsed, origin: "Saved with the opened audit" });
      setSource("file");
    }
  }, [analysis]);

  const start = (params: AnalysisRequest<"flight_audit">["params"], trackFeature?: number) => {
    runPending.current = true;
    // A FlightMD report needs no dataset; a dataset track is the selected feature of the open one.
    const target = trackFeature === undefined ? { datasetId: null, featureIndices: null } : { featureIndices: [trackFeature] };
    void runner
      .run("flight_audit", { ...params, ...zoneRulesParams(rules) }, target)
      .then((info) => {
        // Skipped once when it opens (unless it already has, while the run was pending).
        if (info && restoredId.current !== info.id) startedHere.current.add(info.id);
      })
      .finally(() => {
        runPending.current = false;
      });
  };

  const onRun = form.guard(() => {
    if (source === "dataset") {
      if (!track) return;
      start({ ...common, track_heights: trackHeights }, track.index);
      return;
    }
    if (source === "link") {
      if (linkError) {
        setLinkTouched(true);
        return;
      }
      start({ ...common, report_url: link.trim() });
      return;
    }
    if (!loaded) return;
    start({ ...common, report: loaded.report });
  });

  // Drop a report anywhere on the panel, whichever tab is open.
  const hasFiles = (e: React.DragEvent) => Array.from(e.dataTransfer.types).includes("Files");
  const dropHandlers = {
    onDragOver: (e: React.DragEvent<HTMLFormElement>) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      setDragOver(true);
    },
    onDragLeave: (e: React.DragEvent<HTMLFormElement>) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOver(false);
    },
    onDrop: (e: React.DragEvent<HTMLFormElement>) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      setDragOver(false);
      const dropped = e.dataTransfer.files[0];
      if (!dropped) return;
      changeSource("file");
      readFile(dropped);
    },
  };

  const runError = runner.error;
  const runErrorState = runError ? (
    <ErrorState
      compact
      error={runError}
      title="Flight audit could not start"
      onRetry={onRun}
      retrying={runner.isPending}
      actions={
        source === "link" ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              changeSource("file");
              // This button goes away with the error: continue on the file picker, not on <body>.
              window.requestAnimationFrame(() => fileInputRef.current?.focus());
            }}
          >
            <Upload aria-hidden /> Upload the JSON instead
          </Button>
        ) : null
      }
    />
  ) : null;

  const previous = ((DEMO_MODE ? demoAnalyses.data : myAnalyses.data)?.items ?? [])
    .filter((a) => a.kind === "flight_audit" && a.status === "COMPLETED")
    .slice(0, 5);

  return (
    <form className="relative space-y-4 text-sm" onSubmit={(e) => e.preventDefault()} aria-label="Flight audit" {...dropHandlers}>
      <InputHint>
        Uses the GPS path of a FlightMD report, or a timed position track of the open dataset (GPX, KML or CSV). Heights are
        corrected for terrain when a data pack covers the flight.
        {flightmdUrl ? null : " Set your FlightMD address in Settings to get “Open in FlightMD” links."}
      </InputHint>
      <ChoicesHint choice="bandCeiling" />
      <FormErrorSummary form={form} />

      {previous.length > 0 ? (
        <ModuleSection title={DEMO_MODE ? "Pre-run audits" : "Your recent audits"}>
          <ul className="space-y-1.5">
            {previous.map((a) => {
              const r = analysisResult(a, "flight_audit");
              const name = r?.flight.file_name ?? r?.flight.report_id ?? r?.flight.track_name ?? (r?.flight.source === "track" ? "Position track" : "FlightMD flight");
              const when = formatDate(a.created_at);
              return (
                <li key={a.id}>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-auto min-h-8 w-full justify-start py-1.5 text-left"
                    onClick={() => openAudit(a)}
                    aria-label={`Open audit of ${name}${r ? `, ${VERDICT_LABEL[r.verdict]}` : ""}, ${when}`}
                  >
                    <Route aria-hidden />
                    <span className="min-w-0 flex-1 truncate" title={name}>
                      {name}
                    </span>
                    {r ? <ToneBadge tone={toneForVerdict(r.verdict)} label={VERDICT_LABEL[r.verdict]} /> : null}
                    <span className="shrink-0 text-xs font-normal text-fg-muted">{when}</span>
                  </Button>
                </li>
              );
            })}
          </ul>
        </ModuleSection>
      ) : null}

      <Tabs value={source} onValueChange={(v) => changeSource(v as Source)}>
        {/* Four sources in two rows of two: the list grows to fit them (it is one row tall by default). */}
        <TabsList className="grid h-auto w-full grid-cols-2">
          <TabsTrigger value="link" className="min-h-8">
            Link
          </TabsTrigger>
          <TabsTrigger value="file" className="min-h-8">
            Upload JSON
          </TabsTrigger>
          <TabsTrigger value="sample" className="min-h-8">
            Sample
          </TabsTrigger>
          <TabsTrigger value="dataset" className="min-h-8" disabled={tracks.length === 0} aria-describedby={tracks.length === 0 ? "fa-dataset-track-hint" : undefined}>
            Dataset track
          </TabsTrigger>
        </TabsList>
        {tracks.length === 0 ? (
          <p id="fa-dataset-track-hint" className="pt-1 text-xs text-fg-muted">
            {file
              ? "Dataset track: this dataset has no timed track (GPX with times, KML gx:Track or CSV with a time column)."
              : "Dataset track: open a GPX, KML or CSV track with times to audit it here."}
          </p>
        ) : null}

        <TabsContent value="dataset" className="space-y-2 pt-2">
          {tracks.length > 1 ? (
            <div className="space-y-1.5">
              <Label htmlFor="fa-track">Track</Label>
              <Select
                value={track ? String(track.index) : undefined}
                onValueChange={(v) => {
                  setTrackIndex(Number(v));
                  resetRun();
                }}
              >
                <SelectTrigger id="fa-track">
                  <SelectValue placeholder="Choose a track" />
                </SelectTrigger>
                <SelectContent>
                  {tracks.map((t) => (
                    <SelectItem key={t.index} value={String(t.index)}>
                      {trackOptionLabel(t)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          {track ? <TrackCard track={track} dataset={file?.filename ?? "Open dataset"} /> : null}
        </TabsContent>

        <TabsContent value="link" className="space-y-2 pt-2">
          <Field label="FlightMD report link" hint="A public https link to a report or its JSON export." error={linkTouched ? linkError : null} required>
            {(p) => (
              <Input
                {...p}
                type="url"
                inputMode="url"
                placeholder="https://…/report/…"
                value={link}
                onChange={(e) => {
                  setLink(e.target.value);
                  resetRun();
                }}
                onBlur={() => setLinkTouched(true)}
              />
            )}
          </Field>
          {source === "link" ? runErrorState : null}
        </TabsContent>

        <TabsContent value="file" className="space-y-2 pt-2">
          <div className={`rounded border border-dashed p-4 text-center ${dragOver ? "border-accent bg-accent/10" : "border-border"}`}>
            <FileJson aria-hidden className="mx-auto mb-2 size-6 text-fg-muted" />
            <p className="mb-2 text-fg-muted">Drop a FlightMD report JSON anywhere on this panel, or</p>
            <label className="inline-flex cursor-pointer items-center gap-2 rounded border border-border px-3 py-1.5 hover:bg-elevated focus-within:ring-2 focus-within:ring-accent">
              <Upload aria-hidden className="size-4" />
              Browse…
              <input
                ref={fileInputRef}
                type="file"
                accept="application/json,.json"
                className="sr-only"
                onChange={(e) => {
                  const picked = e.target.files?.[0];
                  if (picked) readFile(picked);
                  e.target.value = "";
                }}
              />
            </label>
            <p className="mt-2 text-xs text-fg-muted">Up to 5 MB. The file is read in your browser and sent with the analysis.</p>
          </div>
        </TabsContent>

        <TabsContent value="sample" className="space-y-2 pt-2">
          {flightSamples.length === 0 ? (
            <p className="text-fg-muted">{samples.isLoading ? "Loading samples…" : "No FlightMD samples are available."}</p>
          ) : (
            <ul className="space-y-1.5">
              {flightSamples.map((s) => (
                <li key={s.name}>
                  <Button type="button" variant="outline" size="sm" className="w-full justify-start" disabled={loadingSample !== null} onClick={() => void loadSample(s.name)}>
                    <FileJson aria-hidden /> {loadingSample === s.name ? "Loading…" : s.title}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {trackSamples.length > 0 ? (
            <div className="space-y-1.5 pt-1">
              <p className="text-xs text-fg-muted">Position tracks open as a dataset, then audit as a “Dataset track”:</p>
              <ul className="space-y-1.5">
                {trackSamples.map((s) => (
                  <li key={s.name}>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="w-full justify-start"
                      disabled={loadSampleDataset.isPending}
                      onClick={() => openTrackSample(s)}
                    >
                      <MapPinned aria-hidden /> {loadSampleDataset.isPending && loadSampleDataset.variables === s.name ? "Opening…" : `Open ${s.title}`}
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </TabsContent>
      </Tabs>

      {loadError ? (
        <p role="alert" className="text-sm text-danger">
          {loadError}
        </p>
      ) : null}
      {loaded ? (
        <ModuleSection title="Report">
          <PreviewCard preview={loaded.preview} origin={loaded.origin} />
        </ModuleSection>
      ) : null}

      <ModuleSection title="Limits">
        <NumField
          label="Height limit above ground"
          unit="m"
          value={ceiling}
          onChange={setCeiling}
          min={1}
          max={1500}
          form={form}
          hint={
            defaultCeiling === null
              ? "Zone ceilings below this limit apply inside their zones."
              : ceiling !== null && ceiling > defaultCeiling
                ? `Default rules allow up to ${formatHeight(defaultCeiling, units)} outside mapped zones, so the audit uses ${formatHeight(defaultCeiling, units)} there; a higher limit applies only inside zones that allow it.`
                : `Zone ceilings below this limit apply inside their zones (default rules: up to ${formatHeight(defaultCeiling, units)} outside mapped zones).`
          }
        />
      </ModuleSection>
      <ModuleSection title="Zone rules">
        <ZoneRulesField value={rules} onChange={setRules} form={form} />
        <ChoiceField
          legend="Above a band's free-flight ceiling"
          choices={BAND_CEILINGS}
          labels={BAND_CEILING_LABEL}
          hints={BAND_CEILING_HINT}
          value={bandCeiling}
          setting={bandCeilingSetting}
          onChange={setBandCeiling}
        />
      </ModuleSection>
      <ModuleSection title="Additional checks">
        <div className="space-y-1.5">
          <Label htmlFor="fa-area">Training area</Label>
          <Select
            value={effectiveAreaKey}
            onValueChange={(v) => {
              setAreaKey(v);
              resetRun();
            }}
          >
            <SelectTrigger id="fa-area" aria-describedby="fa-area-hint">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="off">Off</SelectItem>
              {areas.map((a: AreaChoice) => (
                <SelectItem key={a.key} value={a.key}>
                  {a.name}
                </SelectItem>
              ))}
              {savedArea ? <SelectItem value="saved">{`${savedArea.name} (saved with the opened audit)`}</SelectItem> : null}
            </SelectContent>
          </Select>
          <p id="fa-area-hint" className="text-xs text-fg-muted">
            {areas.length > 0
              ? "Reports when and how far the flight left this polygon (closed lines such as a GPX route count)."
              : "Open a dataset with the area as a polygon or closed line to report when the flight left it."}
          </p>
        </div>
        <NumField
          label="Maximum distance from take-off"
          unit="m"
          value={maxHome}
          onChange={setMaxHome}
          min={1}
          max={100_000}
          optional
          presets={{ metric: [500, 1000], label: "Distance presets" }}
          hint="Visual line-of-sight guidance (about 500 m is often cited); leave empty to skip. Reports the time and distance beyond it."
          form={form}
        />
        <p className="text-xs text-fg-muted">
          Tracks with times also report how long the flight stayed above the height limit.
        </p>
      </ModuleSection>
      <Advanced form={form}>
        {source === "dataset" ? (
          <div className="space-y-1.5">
            <Label htmlFor="fa-heights">Track heights</Label>
            <Select value={trackHeights} onValueChange={(v) => setTrackHeights(TRACK_HEIGHTS.includes(v as TrackHeights) ? (v as TrackHeights) : "auto")}>
              <SelectTrigger id="fa-heights" aria-describedby="fa-heights-hint">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRACK_HEIGHTS.map((h) => (
                  <SelectItem key={h} value={h}>
                    {TRACK_HEIGHTS_LABEL[h]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p id="fa-heights-hint" className="text-xs text-fg-muted">
              {TRACK_HEIGHTS_HINT}
            </p>
          </div>
        ) : null}
        <div className="space-y-1.5">
          <p className="text-sm font-medium" id="fa-cal">
            Altitude calibration
          </p>
          <ToggleGroup type="single" value={calibration} onValueChange={(v) => v && setCalibration(v as typeof calibration)} aria-labelledby="fa-cal">
            <ToggleGroupItem value="takeoff">At take-off</ToggleGroupItem>
            <ToggleGroupItem value="none">None</ToggleGroupItem>
          </ToggleGroup>
          <p className="text-xs text-fg-muted">Take-off calibration removes GPS datum offsets by assuming the first fix is on the ground.</p>
        </div>
        <SwitchField label="Use terrain data" checked={useTerrain} onChange={setUseTerrain} />
        <SwitchField
          label="Data-pack zones"
          checked={datapackZones}
          onChange={setDatapackZones}
          hint="Check the flight against the restricted zones shipped with the data pack. Off is flagged in the brief."
        />
        <NumField label="Population footprint buffer" unit="m" value={footprint} onChange={setFootprint} min={0} max={2000} form={form} />
      </Advanced>

      {source !== "link" ? runErrorState : null}
      <RunButton onRun={onRun} pending={runner.isPending} disabledReason={blocked} label="Audit flight" />
      {dragOver ? (
        // Last child with no margin, so showing it never shifts the form.
        <div
          aria-hidden
          className="pointer-events-none absolute -inset-1 z-10 !mt-0 flex items-center justify-center rounded border-2 border-dashed border-accent bg-surface/95 p-4 text-center"
        >
          <p className="flex items-center gap-2 font-medium text-fg">
            <FileJson className="size-5 text-accent" /> Drop the FlightMD report JSON to load it
          </p>
        </div>
      ) : null}
    </form>
  );
}

const FINDING_TONE: Record<FindingSeverity, Tone> = { critical: "danger", warning: "warn", info: "info", good: "ok" };
const FINDING_LABEL: Record<FindingSeverity, string> = { critical: "Critical", warning: "Warning", info: "Info", good: "Good" };

/** Where a FlightMD finding sits on the flight, in words. */
function describePlacement(f: AuditFinding, units: ReturnType<typeof useUnits>): string {
  switch (f.placement) {
    case "placed": {
      const zone = f.zone ? ` · ${ZONE_LABEL[f.zone]}${f.zone_name ? ` (${f.zone_name})` : ""}` : "";
      return `${formatHeight(f.agl_m, units)} above ground at ${formatLength(f.distance_m, units)}${zone}`;
    }
    case "outside_track":
      return "Outside the GPS track's time span";
    case "no_track_times":
      return "Not placed: the report has no GPS times";
    default:
      return "No time in the report";
  }
}

function describeArmed(armed: boolean | null): string {
  return armed === null ? EMPTY : armed ? "Armed" : "Disarmed";
}

/** "Open in FlightMD" only ever goes to the viewer's own FlightMD (the Settings address). A stored
 * link to any other host came from whoever ran the audit, so it is shown as text, not a button. */
function flightmdLinks(result: FlightAuditResult, base: string): { open: string | null; foreign: string | null } {
  const stored = safeHttpUrl(result.flight.flightmd_url);
  const root = safeHttpUrl(base);
  if (stored && root && new URL(stored).origin === new URL(root).origin) return { open: stored, foreign: null };
  const own = flightmdReportUrl(base, result.flight.report_id);
  return { open: own, foreign: stored && stored !== own ? stored : null };
}

/** Highlight key of an exceedance, scoped to its audit so a selection never carries over to another audit. */
const exceedanceKey = (analysisId: string, index: number) => `exceedance-${analysisId}-${index}`;

function FlightAuditResults({ analysis }: ModuleResultsProps) {
  const units = useUnits();
  const focus = useFocusMap();
  const flightmdBase = useSettings((s) => s.flightmdUrl);
  const linkHostId = React.useId();
  const highlight = useUi((s) => s.resultHighlight);
  const setHighlight = useUi((s) => s.setResultHighlight);
  const result = analysisResult(analysis, "flight_audit");
  const plannedWith = useBriefLink(result?.flight.planning_brief_sha256 ?? null);
  // Position tracks with times also report when (and for how long) each segment happened.
  const timed = typeof result?.duration_s === "number";
  // FlightMD 1.7 times are the log's own clock, so they line up with its findings
  const logTime = result?.flight.gps_times === true;

  const exceedanceColumns = React.useMemo<DataTableColumn<AuditExceedance>[]>(
    () => [
      { id: "from", header: "From", numeric: true, cell: (e) => formatLength(e.start_distance_m, units), sortValue: (e) => e.start_distance_m },
      { id: "to", header: "To", numeric: true, cell: (e) => formatLength(e.end_distance_m, units), sortValue: (e) => e.end_distance_m },
      { id: "length", header: "Length", numeric: true, cell: (e) => formatLength(e.length_m, units), sortValue: (e) => e.length_m },
      ...(timed
        ? [{ id: "duration", header: "Duration", numeric: true, cell: (e: AuditExceedance) => formatDuration(e.duration_s ?? null), sortValue: (e: AuditExceedance) => e.duration_s ?? null }]
        : []),
      { id: "max", header: "Max height", numeric: true, rowHeader: true, cell: (e) => formatHeight(e.max_agl_m, units), sortValue: (e) => e.max_agl_m },
      { id: "limit", header: "Limit", numeric: true, cell: (e) => formatHeight(e.limit_m, units), sortValue: (e) => e.limit_m },
      { id: "reason", header: "Reason", cell: (e) => localizeUnits(e.reason, units) },
    ],
    [units, timed],
  );
  // The same runs as exceedances, against the band's ceiling (band_ceiling "permission").
  const aboveColumns = React.useMemo<DataTableColumn<AuditExceedance>[]>(
    () => [
      ...exceedanceColumns.filter((c) => c.id !== "limit" && c.id !== "reason"),
      { id: "ceiling", header: "Ceiling", numeric: true, cell: (e) => formatHeight(e.limit_m, units), sortValue: (e) => e.limit_m },
      { id: "over", header: "Over by", numeric: true, cell: (e) => formatHeight(e.max_agl_m - e.limit_m, units), sortValue: (e) => e.max_agl_m - e.limit_m },
      { id: "reason", header: "Reason", cell: (e) => localizeUnits(e.reason, units) },
    ],
    [exceedanceColumns, units],
  );
  const segmentColumns = React.useCallback(
    (peakHeader: string): DataTableColumn<AuditSegment>[] => [
      ...(timed
        ? [
            { id: "start", header: logTime ? "At (log time)" : "At (since start)", numeric: true, cell: (s: AuditSegment) => formatClock(s.start_time_s), sortValue: (s: AuditSegment) => s.start_time_s },
            { id: "duration", header: "Duration", numeric: true, cell: (s: AuditSegment) => formatDuration(s.duration_s), sortValue: (s: AuditSegment) => s.duration_s },
          ]
        : [{ id: "start", header: "From", numeric: true, cell: (s: AuditSegment) => formatLength(s.start_distance_m, units), sortValue: (s: AuditSegment) => s.start_distance_m }]),
      { id: "length", header: "Length flown", numeric: true, cell: (s) => formatLength(s.length_m, units), sortValue: (s) => s.length_m },
      { id: "peak", header: peakHeader, numeric: true, rowHeader: true, cell: (s) => formatLength(s.peak_m, units), sortValue: (s) => s.peak_m },
    ],
    [units, timed, logTime],
  );
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
        // a band flown above its ceiling turns into the zone abvoe it
        cell: (z) =>
          z.permission_required
            ? z.agl_margin_m != null && z.agl_margin_m < 0
              ? `Permission (${formatHeight(-z.agl_margin_m, units)} over)`
              : "Permission"
            : formatHeight(z.ceiling_agl_m, units),
      },
      { id: "length", header: "Flown inside", numeric: true, cell: (z) => formatLength(z.overlap_length_m ?? null, units), sortValue: (z) => z.overlap_length_m ?? null },
    ],
    [units],
  );

  const findingColumns = React.useMemo<DataTableColumn<AuditFinding>[]>(
    () => [
      {
        id: "severity",
        header: "Severity",
        cell: (f) => <ToneBadge tone={FINDING_TONE[f.severity]} label={FINDING_LABEL[f.severity]} />,
        sortValue: (f) => ["critical", "warning", "info", "good"].indexOf(f.severity),
      },
      {
        id: "title",
        header: "Finding",
        rowHeader: true,
        cell: (f) => (
          <>
            {f.title}
            {f.params.length > 0 ? <span className="num block text-xs text-fg-muted">{f.params.join(", ")}</span> : null}
          </>
        ),
        sortValue: (f) => f.title,
      },
      // log-relative, the same clock as FlightMD's charts
      { id: "time", header: "Log time", numeric: true, cell: (f) => formatClock(f.t_start_s), sortValue: (f) => f.t_start_s },
      { id: "armed", header: "Armed", cell: (f) => describeArmed(f.armed) },
      { id: "where", header: "Where", cell: (f) => describePlacement(f, units) },
    ],
    [units],
  );

  if (!analysis) return null;
  if (!result) {
    return analysis.status === "COMPLETED" ? <EmptyState compact icon={<Route />} title="No result" /> : null;
  }

  const { open: link, foreign: foreignLink } = flightmdLinks(result, flightmdBase);
  const limits = auditLimits(result);
  const exceedanceFeatures = resultLayer(result.layers, "exceedances").features;
  const selectedExceedance = result.exceedances.findIndex((_, i) => exceedanceKey(analysis.id, i) === highlight);
  const flight = result.flight;
  const isTrack = flight.source === "track";
  const flightName = flight.file_name ?? flight.report_id ?? flight.track_name ?? (isTrack ? "Position track" : "FlightMD flight");
  const notAssessed = result.assessment_complete === false ? result.issues.find((i) => i.code === "not_assessed") ?? null : null;
  const areaExits = result.area_exits ?? null;
  const farSegments = result.distance_exceedances ?? null;
  const farLimit = result.max_distance_from_home_m ?? null;
  // A 2D track (no altitudes): the heights were not assessed (absent in older results: assessed).
  const heightsAssessed = result.heights_assessed !== false;
  // Null for a run with band_ceiling "exceedance" (or one stored before the choice).
  const aboveBand = result.above_band_ceiling ?? null;
  // FlightMD 1.6+ findings (none for tracks or older reports)
  const findings = result.findings ?? [];
  const findingFeatures = resultLayer(result.layers, "findings").features;
  const armedWindows = flight.armed_windows ?? null;
  const plannedSha = flight.planning_brief_sha256 ?? null;

  return (
    <div className="space-y-4">
      <VerdictBanner
        verdict={result.verdict}
        title="Flight audit"
        summary={
          !heightsAssessed
            ? "Heights not assessed: the track has no altitudes."
            : result.exceedances.length > 0
              ? `${result.exceedances.length} segment${result.exceedances.length === 1 ? "" : "s"} above the limit; highest ${formatHeight(result.max_agl_m, units)} above ground.`
              : aboveBand && aboveBand.length > 0
                ? `${aboveBand.length} segment${aboveBand.length === 1 ? "" : "s"} in yellow airspace above a band's ceiling: permission required. Highest ${formatHeight(result.max_agl_m, units)} above ground.`
                : describeWithinLimit(limits, result.max_agl_m, units)
        }
      />
      <SyntheticNote synthetic={analysis.datapack_synthetic} />
      {result.assessment_complete === false ? (
        <Alert variant="warn">
          <AlertTitle>Airspace not assessed</AlertTitle>
          <AlertDescription>
            <p>
              {notAssessed?.message ??
                "No data pack covers this flight, so aerodrome zones were not checked. Treat the flight as unverified there."}
            </p>
          </AlertDescription>
        </Alert>
      ) : null}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="min-w-0 break-words text-fg-muted">
          {flightName}
          {isTrack && flight.track_format ? ` · ${TRACK_FORMAT_LABEL[flight.track_format]}` : ""}
          {flight.location_name ? ` · ${flight.location_name}` : ""}
          {flight.log_start_utc ? ` · ${formatLogTime(flight.log_start_utc)}` : ""}
        </span>
        {link ? (
          <span className="ml-auto flex min-w-0 items-center gap-2">
            <span id={linkHostId} className="min-w-0 break-all text-xs text-fg-muted">
              {new URL(link).host}
            </span>
            <Button asChild size="sm" variant="secondary">
              <a href={link} target="_blank" rel="noreferrer noopener" aria-describedby={linkHostId}>
                <ExternalLink aria-hidden /> Open in FlightMD
              </a>
            </Button>
          </span>
        ) : null}
        {foreignLink ? (
          <p className="w-full min-w-0 text-xs text-fg-muted">
            FlightMD link given with this audit, not to your FlightMD address in Settings, so not a button:{" "}
            <span className="num select-all break-all text-fg">{foreignLink}</span>
          </p>
        ) : null}
      </div>

      {plannedSha ? (
        <p className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 text-xs text-fg-muted">
          <span>Planned with brief</span>
          <span className="num break-all text-fg">{plannedSha.slice(0, 16)}…</span>
          {plannedWith ? (
            <Link href={briefLinkHref(plannedWith)} className="text-accent-text underline">
              {plannedWith.title}
            </Link>
          ) : (
            <span>(not one of the briefs built in this browser)</span>
          )}
        </p>
      ) : null}

      <KpiGrid label="Flight audit summary">
        {heightsAssessed ? (
          <KpiTile
            label="Max height above ground"
            value={heightParts(result.max_agl_m, units)}
            tone={result.exceedances.length > 0 ? "danger" : "ok"}
            toneLabel={result.exceedances.length > 0 ? "Above the limit" : "Within the limit"}
            explanation={describeLimit(limits, units)}
          />
        ) : (
          <KpiTile
            label="Max height above ground"
            value="Not recorded"
            tone="warn"
            toneLabel="Not assessed"
            explanation="The track has no altitudes, so the height limit was not checked."
          />
        )}
        {heightsAssessed ? <KpiTile label="Max height above take-off" value={heightParts(result.max_height_above_takeoff_m, units)} /> : null}
        <KpiTile label="Distance flown" value={lengthParts(result.distance_m, units)} explanation={`${formatNumber(result.flight.points)} GPS points`} />
        {typeof result.duration_s === "number" ? (
          <KpiTile
            label="Flight time"
            value={formatDuration(result.duration_s)}
            explanation={
              !heightsAssessed
                ? undefined
                : typeof result.time_above_limit_s === "number" && result.time_above_limit_s > 0
                  ? `${formatDuration(result.time_above_limit_s)} above the height limit`
                  : "Never above the height limit"
            }
          />
        ) : null}
        {typeof result.max_home_distance_m === "number" ? (
          <KpiTile
            label="Farthest from take-off"
            value={lengthParts(result.max_home_distance_m, units)}
            tone={farLimit === null ? "neutral" : farSegments && farSegments.length > 0 ? "danger" : "ok"}
            toneLabel={farLimit === null ? undefined : farSegments && farSegments.length > 0 ? "Beyond the distance" : "Within the distance"}
            explanation={farLimit === null ? undefined : `Limit ${formatLength(farLimit, units)}`}
          />
        ) : null}
        {areaExits !== null ? (
          <KpiTile
            label="Outside the training area"
            value={typeof result.time_outside_area_s === "number" ? formatDuration(result.time_outside_area_s) : formatNumber(areaExits.length)}
            tone={areaExits.length > 0 ? "danger" : "ok"}
            toneLabel={areaExits.length > 0 ? `Left it ${areaExits.length} time${areaExits.length === 1 ? "" : "s"}` : "Stayed inside"}
            explanation={result.training_box_name ?? undefined}
          />
        ) : null}
        <KpiTile
          label="Nearest aerodrome"
          value={
            result.nearest_aerodrome && result.min_aerodrome_distance_km !== null && result.min_aerodrome_distance_km !== undefined
              ? lengthParts(result.min_aerodrome_distance_km * 1000, units)
              : "None in data"
          }
          explanation={result.nearest_aerodrome ? `${result.nearest_aerodrome.name} (${result.nearest_aerodrome.ident})` : undefined}
        />
        <KpiTile
          label="People overflown"
          value={result.overflown_population === null || result.overflown_population === undefined ? "No data" : formatNumber(result.overflown_population, { maximumFractionDigits: 0 })}
          explanation={result.max_density_per_km2 ? `Max ${formatNumber(result.max_density_per_km2, { maximumFractionDigits: 0 })} people/km²` : undefined}
        />
      </KpiGrid>
      <p className="text-xs text-fg-muted">
        {heightsAssessed ? REFERENCE_TEXT[result.altitude_reference] : "The track is 2D (no altitudes): only positions, distances and times were audited."}
        {result.calibration_offset_m !== null && result.calibration_offset_m !== undefined
          ? ` Calibration offset ${formatNumber(result.calibration_offset_m, { maximumFractionDigits: 1 })} m.`
          : ""}
      </p>

      {/* A 2D track has no heights to plot (its profile is 0 m throughout). */}
      {heightsAssessed ? (
        <ProfileChart
          title="Height above ground along the flight"
          points={result.profile}
          units={units}
          series={
            limits.varies
              ? [
                  { key: "agl_m", label: "Height above ground", color: "var(--accent)", kind: "line" },
                  { key: "limit_agl_m", label: "Height limit (incl. zones)", color: "var(--warn)", kind: "line", dashed: true, curve: "stepAfter" },
                ]
              : [{ key: "agl_m", label: "Height above ground", color: "var(--accent)", kind: "line" }]
          }
          limit={limits.varies ? null : { value: limits.highest, label: `Limit ${formatHeight(limits.highest, units)}` }}
          bands={[
            ...result.exceedances.map((e, i) => ({
              start: e.start_distance_m,
              end: e.end_distance_m,
              label: `Over ${i + 1}`,
              active: i === selectedExceedance,
            })),
            ...(aboveBand ?? []).map((e, i) => ({ start: e.start_distance_m, end: e.end_distance_m, label: `Yellow ${i + 1}`, tone: "warn" as const })),
          ]}
        />
      ) : null}
      {heightsAssessed ? <DemCaveat dem={result.dem} /> : null}
      <BandChips result={result} units={units} />

      <ModuleSection title={`Exceedances (${result.exceedances.length})`}>
        <DataTable
          data={result.exceedances}
          columns={exceedanceColumns}
          getRowId={(_, i) => exceedanceKey(analysis.id, i)}
          caption="Segments above the height limit"
          onRowClick={(_, i) => {
            setHighlight(exceedanceKey(analysis.id, i));
            const feature = exceedanceFeatures[i];
            if (feature) focus(feature);
          }}
          selectedRowIds={selectedExceedance >= 0 ? [exceedanceKey(analysis.id, selectedExceedance)] : []}
          getRowLabel={(e) => `Zoom to the segment from ${formatLength(e.start_distance_m, units)} to ${formatLength(e.end_distance_m, units)}`}
          dense
          emptyState={
            heightsAssessed ? (
              <EmptyState compact title="No exceedances" description="The flight stayed within the height limit." />
            ) : (
              <EmptyState compact title="Not assessed" description="The track has no altitudes, so heights were not checked." />
            )
          }
        />
      </ModuleSection>

      {aboveBand !== null ? (
        <ModuleSection title={`In yellow airspace above a band's ceiling (${aboveBand.length})`}>
          <p className="text-xs text-fg-muted">
            This run counted flying above a band&apos;s free-flight ceiling as permission required, not as an exceedance: check that
            the permission was held.
          </p>
          <DataTable
            data={aboveBand}
            columns={aboveColumns}
            getRowId={(_, i) => `above-band-${analysis.id}-${i}`}
            caption="Segments flown in yellow airspace above a band's ceiling"
            onRowClick={(_, i) => {
              const feature = resultLayer(result.layers, "above_band_ceiling").features[i];
              if (feature) focus(feature);
            }}
            getRowLabel={(e) => `Zoom to the segment from ${formatLength(e.start_distance_m, units)} to ${formatLength(e.end_distance_m, units)}`}
            dense
            emptyState={
              heightsAssessed ? (
                <EmptyState compact title="Never above a band's ceiling" />
              ) : (
                <EmptyState compact title="Not assessed" description="The track has no altitudes, so heights were not checked." />
              )
            }
          />
        </ModuleSection>
      ) : null}

      {areaExits !== null ? (
        <ModuleSection title={`Left the training area (${areaExits.length})`}>
          <DataTable
            data={areaExits}
            columns={segmentColumns("Farthest outside")}
            getRowId={(_, i) => `area-exit-${analysis.id}-${i}`}
            caption={`Segments outside ${result.training_box_name ?? "the training area"}`}
            onRowClick={(_, i) => {
              const feature = resultLayer(result.layers, "area_exits").features[i];
              if (feature) focus(feature);
            }}
            getRowLabel={(s) => `Zoom to the segment outside the area, ${formatLength(s.peak_m, units)} at most`}
            dense
            emptyState={<EmptyState compact title="Stayed inside" description="The flight never left the training area." />}
          />
        </ModuleSection>
      ) : null}

      {farSegments !== null ? (
        <ModuleSection title={`Beyond ${formatLength(farLimit, units)} from take-off (${farSegments.length})`}>
          <DataTable
            data={farSegments}
            columns={segmentColumns("Farthest")}
            getRowId={(_, i) => `distance-${analysis.id}-${i}`}
            caption="Segments beyond the distance limit from take-off"
            onRowClick={(_, i) => {
              const feature = resultLayer(result.layers, "distance_exceedances").features[i];
              if (feature) focus(feature);
            }}
            getRowLabel={(s) => `Zoom to the segment up to ${formatLength(s.peak_m, units)} from take-off`}
            dense
            emptyState={<EmptyState compact title="Within the distance" description="The flight stayed within the distance from take-off." />}
          />
        </ModuleSection>
      ) : null}

      {findings.length > 0 ? (
        <ModuleSection title={`FlightMD findings (${findings.length})`}>
          <p className="text-xs text-fg-muted">
            {armedWindows === null
              ? "The report has no armed windows."
              : armedWindows.length === 0
                ? "The report says the aircraft was never armed."
                : `Armed ${armedWindows.map(([a, b]) => `${formatClock(a)}–${formatClock(b)}`).join(", ")} (log time).`}{" "}
            {flight.gps_times
              ? "Findings are placed where they started on the track."
              : "Without GPS times in the report (FlightMD 1.7), findings are listed by time only."}
          </p>
          <DataTable
            data={findings}
            columns={findingColumns}
            getRowId={(f, i) => `finding-${analysis.id}-${i}-${f.id}`}
            caption="FlightMD findings, with where they happened"
            onRowClick={(f) => {
              const feature = findingFeatures.find((x) => x.properties?.id === f.id);
              if (feature) focus(feature);
            }}
            getRowLabel={(f) => `Zoom to where ${f.title} started`}
            dense
          />
        </ModuleSection>
      ) : null}

      <ModuleSection title={`Zones entered (${result.zones.length})`}>
        <DataTable data={result.zones} columns={zoneColumns} getRowId={(z, i) => `${z.name}-${i}`} caption="Zones the flight entered" dense emptyState={<EmptyState compact title="No zones entered" />} />
      </ModuleSection>

      <IssueList issues={result.issues} />
      <Disclaimer text={result.disclaimer} />
    </div>
  );
}

const LAYERS = [
  { id: "flight-audit:path", label: "Flight path", resultLayer: "path" },
  { id: "flight-audit:exceedances", label: "Exceedances", resultLayer: "exceedances" },
  { id: "flight-audit:above-band-ceiling", label: "Above a band's ceiling", resultLayer: "above_band_ceiling" },
  { id: "flight-audit:home", label: "Take-off point", resultLayer: "home" },
  { id: "flight-audit:training-box", label: "Training area", resultLayer: "training_box" },
  { id: "flight-audit:area-exits", label: "Outside the training area", resultLayer: "area_exits" },
  { id: "flight-audit:home-radius", label: "Distance limit from take-off", resultLayer: "home_radius" },
  { id: "flight-audit:distance-exceedances", label: "Beyond the distance limit", resultLayer: "distance_exceedances" },
  { id: "flight-audit:findings", label: "FlightMD findings", resultLayer: "findings" },
  { id: "flight-audit:zones", label: "Zones", resultLayer: "zones" },
] as const;

type AnyFc = FeatureCollection<Geometry | null, Record<string, unknown>>;

interface ZoneProps {
  zone?: ZoneKind;
  name?: string;
  label?: string;
  basis?: "rule" | "verify_official_map";
}

interface FindingPoint {
  position: [number, number];
  properties: Record<string, unknown>;
}

function severityOf(p: Record<string, unknown>): FindingSeverity {
  const sev = p.severity;
  return sev === "critical" || sev === "warning" || sev === "good" ? sev : "info";
}

function bandOf(p: Record<string, unknown>): AglBand {
  const band = p.agl_band;
  return band === "over" || band === "near" ? band : "ok";
}

function buildAuditLayers(analysis: AnalysisInfo, ctx: ModuleLayerContext): ModuleLayerOutput {
  const result = analysis.result as FlightAuditResult | null;
  if (!result) return { layers: [], legends: [] };
  const palette = mapPalette();
  const path = resultLayer(result.layers, "path") as AnyFc;
  const over = derived(path, "flight-audit:over", (): AnyFc => ({ type: "FeatureCollection", features: path.features.filter((f) => bandOf(f.properties ?? {}) === "over") }));
  const home = resultLayer(result.layers, "home") as AnyFc;
  const homePoints = derived(home, "flight-audit:home-points", () =>
    home.features.flatMap((f) => (f.geometry?.type === "Point" ? [f.geometry.coordinates as [number, number, number]] : [])),
  );
  // Heights above sea level; in 3D the flight is drawn above its take-off point (see `withHeights`).
  const takeOffAltitude = Number(homePoints[0]?.[2] ?? 0);
  // Each exceedance carries its audit's id, so a click on the map selects the row of this audit only.
  const exceedancesFc = resultLayer(result.layers, "exceedances") as AnyFc;
  const exceedances = derived(exceedancesFc, "flight-audit:exceedances", (): AnyFc => ({
    type: "FeatureCollection",
    features: exceedancesFc.features.map((f) => ({ ...f, properties: { ...(f.properties ?? {}), analysis_id: analysis.id } })),
  }));
  const isSelected = (p: Record<string, unknown>) => ctx.resultHighlight === exceedanceKey(analysis.id, Number(p.index));
  // Optional checks: the training area and the distance ring are outlines, the runs beyond them
  // are wide translucent lines under the path (the path keeps its height colours on top).
  const trainingBox = resultLayer(result.layers, "training_box") as AnyFc;
  const areaExits = resultLayer(result.layers, "area_exits") as AnyFc;
  const homeRadius = resultLayer(result.layers, "home_radius") as AnyFc;
  const farRuns = resultLayer(result.layers, "distance_exceedances") as AnyFc;
  const aboveBand = resultLayer(result.layers, "above_band_ceiling") as AnyFc;
  const findingsFc = resultLayer(result.layers, "findings") as AnyFc;
  const findingPoints = derived(findingsFc, "flight-audit:finding-points", (): FindingPoint[] =>
    findingsFc.features.flatMap((f) =>
      f.geometry?.type === "Point" ? [{ position: f.geometry.coordinates as [number, number], properties: f.properties ?? {} }] : [],
    ),
  );
  // The bands and zones the flight was judged against, under everything else.
  const zones = resultLayer(result.layers, "zones") as unknown as FeatureCollection<Geometry | null, ZoneProps>;
  const zoneLayers = hatchZoneLayer<ZoneProps>({
    id: "flight-audit:zones",
    data: zones,
    getZone: (p) => p.zone ?? null,
    hatch: ctx.hatch,
    updateTriggers: [ctx.theme, ctx.hatch],
  });
  const checkLayers = [
    geoJsonLayer({
      id: "flight-audit:training-box",
      data: trainingBox,
      filled: true,
      fillColor: [palette.accent[0], palette.accent[1], palette.accent[2], 18],
      lineColor: palette.accent,
      lineWidth: 2,
      pickable: true,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "flight-audit:home-radius",
      data: homeRadius,
      filled: false,
      lineColor: palette.muted,
      lineWidth: 1.5,
      dashed: true,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "flight-audit:area-exits",
      data: areaExits,
      heightBase: takeOffAltitude,
      filled: false,
      lineColor: [palette.warn[0], palette.warn[1], palette.warn[2], 120],
      lineWidth: 12,
      pickable: true,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "flight-audit:distance-exceedances",
      data: farRuns,
      heightBase: takeOffAltitude,
      filled: false,
      lineColor: [palette.warn[0], palette.warn[1], palette.warn[2], 90],
      lineWidth: 16,
      dashed: true,
      pickable: true,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "flight-audit:above-band-ceiling",
      data: aboveBand,
      heightBase: takeOffAltitude,
      filled: false,
      lineColor: [palette.warn[0], palette.warn[1], palette.warn[2], 110],
      lineWidth: 10,
      pickable: true,
      updateTriggers: [ctx.theme],
    }),
  ];

  const layers = [
    ...zoneLayers,
    ...checkLayers,
    geoJsonLayer({
      id: "flight-audit:path",
      data: path,
      heightBase: takeOffAltitude,
      filled: false,
      lineColor: (p) => toneRgba(toneForAglBand(bandOf(p)), 255),
      lineWidth: 3,
      pickable: true,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "flight-audit:path/over",
      data: over,
      heightBase: takeOffAltitude,
      filled: false,
      lineColor: palette.text,
      lineWidth: 1.5,
      dashed: true,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "flight-audit:exceedances",
      data: exceedances,
      heightBase: takeOffAltitude,
      filled: false,
      lineColor: (p) => [palette.danger[0], palette.danger[1], palette.danger[2], isSelected(p) ? 200 : 110],
      lineWidth: (p) => (isSelected(p) ? 14 : 10),
      pickable: true,
      updateTriggers: [ctx.theme, ctx.resultHighlight],
    }),
    pointLayer<[number, number, number]>({
      id: "flight-audit:home",
      data: homePoints,
      getPosition: (p) => p,
      heightBase: takeOffAltitude,
      radius: 7,
      fillColor: palette.ok,
      lineColor: palette.text,
      lineWidth: 2,
      updateTriggers: [ctx.theme],
    }),
    textLabelLayer<[number, number, number]>({
      id: "flight-audit:home/label",
      data: homePoints,
      getPosition: (p) => p,
      heightBase: takeOffAltitude,
      getText: () => "Take-off",
      pixelOffset: [0, -18],
      updateTriggers: [ctx.theme],
    }),
    // findings sit on the ground under the path; colour plus the severity word, never colour alone
    pointLayer<FindingPoint>({
      id: "flight-audit:findings",
      data: findingPoints,
      getPosition: (d) => d.position,
      radius: 6,
      fillColor: (d) => toneRgba(FINDING_TONE[severityOf(d.properties)], 255),
      lineColor: palette.text,
      lineWidth: 1.5,
      updateTriggers: [ctx.theme],
    }),
    textLabelLayer<FindingPoint>({
      id: "flight-audit:findings/label",
      data: findingPoints,
      getPosition: (d) => d.position,
      getText: (d) => FINDING_LABEL[severityOf(d.properties)],
      pixelOffset: [0, -16],
      updateTriggers: [ctx.theme],
    }),
  ];
  const legends: LegendSpec[] = [
    {
      id: "flight-audit:path",
      title: "Height above ground",
      kind: "categorical",
      layerId: "flight-audit:path",
      items: [
        { label: AGL_BAND_LABEL.ok, color: "var(--ok)", shape: "line" },
        { label: AGL_BAND_LABEL.near, color: "var(--warn)", shape: "line" },
        { label: AGL_BAND_LABEL.over, color: "var(--danger)", shape: "dashed-line" },
      ],
      note: `${describeLimit(auditLimits(result), ctx.units)} (heights above ground).`,
    },
  ];
  if (trainingBox.features.length > 0 || homeRadius.features.length > 0) {
    const items: LegendItem[] = [];
    if (trainingBox.features.length > 0) {
      items.push({ label: result.training_box_name ?? "Training area", color: "var(--accent)", shape: "line" });
      items.push({ label: "Outside the training area", color: "var(--warn)", shape: "line" });
    }
    if (homeRadius.features.length > 0) {
      items.push({ label: "Distance limit from take-off", color: "var(--text-muted)", shape: "dashed-line" });
      items.push({ label: "Beyond the distance limit", color: "var(--warn)", shape: "dashed-line" });
    }
    legends.push({ id: "flight-audit:checks", title: "Additional checks", kind: "categorical", layerId: "flight-audit:training-box", items });
  }
  if (aboveBand.features.length > 0) {
    legends.push({
      id: "flight-audit:above-band-ceiling",
      title: "Above a band's ceiling",
      kind: "categorical",
      layerId: "flight-audit:above-band-ceiling",
      items: [{ label: "Yellow airspace, permission required", color: "var(--warn)", shape: "line" }],
    });
  }
  if (findingPoints.length > 0) {
    legends.push({
      id: "flight-audit:findings",
      title: "FlightMD findings",
      kind: "categorical",
      layerId: "flight-audit:findings",
      items: (["critical", "warning", "info", "good"] as const).map((sev) => ({
        label: FINDING_LABEL[sev],
        color: `var(--${TONE_TOKEN[FINDING_TONE[sev]]})`,
        shape: "point" as const,
      })),
      note: "Where each finding started, from the report's GPS times.",
    });
  }
  if (zones.features.length > 0) {
    legends.push(
      zoneLegend("flight-audit:zones", {
        layerId: "flight-audit:zones",
        note: "Indicative only — not an official airspace map. Bands within 5 km of an airport are not defined by the published rule: verify on Digital Sky.",
      }),
    );
  }
  return { layers, legends };
}

function describeAuditPick(info: PickingInfo): string | null {
  const id = info.layer?.id ?? "";
  if (!id.startsWith("flight-audit:") || !info.object) return null;
  const p = ((info.object as { properties?: Record<string, unknown> }).properties ?? {}) as Record<string, unknown>;
  if (id === "flight-audit:path") return AGL_BAND_LABEL[bandOf(p)];
  if (id === "flight-audit:exceedances") {
    const units = currentUnits();
    const limit = typeof p.limit_m === "number" ? ` of ${formatHeight(p.limit_m, units)}` : "";
    const max = typeof p.max_agl_m === "number" ? `, max ${formatHeight(p.max_agl_m, units)}` : "";
    return `Above the limit${limit}${max}`;
  }
  if (id === "flight-audit:above-band-ceiling") {
    const units = currentUnits();
    const ceiling = typeof p.limit_m === "number" ? ` the ${formatHeight(p.limit_m, units)} ceiling` : " a band's ceiling";
    return `Yellow airspace above${ceiling}: permission required`;
  }
  if (id === "flight-audit:training-box") return typeof p.name === "string" ? p.name : "Training area";
  if (id === "flight-audit:findings") {
    const title = typeof p.title === "string" ? p.title : "FlightMD finding";
    return `${FINDING_LABEL[severityOf(p)]}: ${title}, log time ${formatClock(typeof p.t_start_s === "number" ? p.t_start_s : null)}`;
  }
  if (id.startsWith("flight-audit:zones")) {
    const z = p as ZoneProps;
    const kind = z.basis === "verify_official_map" ? "Verify on Digital Sky" : z.zone ? ZONE_LABEL[z.zone] : null;
    return [z.name, z.label, kind].filter(Boolean).join(" · ");
  }
  if (id === "flight-audit:area-exits" || id === "flight-audit:distance-exceedances") {
    const units = currentUnits();
    const peak = typeof p.peak_m === "number" ? formatLength(p.peak_m, units) : null;
    const duration = typeof p.duration_s === "number" ? `, ${formatDuration(p.duration_s)}` : "";
    return id === "flight-audit:area-exits"
      ? `Outside the training area${peak ? `, up to ${peak}` : ""}${duration}`
      : `Beyond the distance limit${peak ? `, up to ${peak} from take-off` : ""}${duration}`;
  }
  return null;
}

/** A click on an exceedance selects its row in the results table. */
function onAuditPick(info: PickingInfo): boolean {
  if (info.layer?.id !== "flight-audit:exceedances" || !info.object) return false;
  const props = (info.object as { properties?: Record<string, unknown> }).properties ?? {};
  const index = Number(props.index);
  if (!Number.isInteger(index) || typeof props.analysis_id !== "string") return false;
  useUi.getState().setResultHighlight(exceedanceKey(props.analysis_id, index));
  return true;
}

export const definition: ModuleDefinition = {
  ...MODULE_META["flight-audit"],
  Panel: FlightAuditPanel,
  Results: FlightAuditResults,
  layers: LAYERS,
  buildLayers: buildAuditLayers,
  describePick: describeAuditPick,
  onPick: onAuditPick,
};

export default definition;
