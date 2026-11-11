"use client";

/**
 * Upload: drag-and-drop plus a Browse button (the WCAG 2.2 dragging alternative). Extension and
 * size are checked in the browser first (RUNBOOK T9). A FlightMD report JSON is sent to Flight
 * audit instead, and a file that comes back without a coordinate system stays here with the CRS
 * override open, so it can be processed agian. Keyboard focus never falls back to the page.
 */

import { useCallback, useEffect, useId, useRef, useState, type DragEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, FileUp, FolderOpen, Info, RotateCw, Upload, X } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ErrorState } from "@/components/ui/error-state";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Stepper, type StepperStep } from "@/components/ui/stepper";
import { DEMO_MODE, describeError, isApiError } from "@/lib/api";
import { needsCrs } from "@/lib/datasets";
import { formatBytes, formatNumber } from "@/lib/format";
import { useDeleteFile, useFile, useMeta, useUpload } from "@/lib/queries";
import type { FileInfo } from "@/lib/types";
import { workspaceHref } from "@/lib/url-state";
import { cn } from "@/lib/utils";

/** File extensions the API accepts (airbrief_core/io/ingest.py). */
export const ACCEPTED_EXTENSIONS = [".zip", ".kml", ".kmz", ".geojson", ".json", ".gpx", ".csv"] as const;

const ACCEPTED_LABEL: readonly { ext: string; what: string }[] = [
  { ext: ".zip", what: "Shapefile (.shp, .shx, .dbf, .prj), or several KML/GeoJSON/GPX/CSV files" },
  { ext: ".kml / .kmz", what: "Google Earth" },
  { ext: ".geojson / .json", what: "GeoJSON" },
  { ext: ".gpx", what: "GPS waypoints, routes and tracks" },
  { ext: ".csv", what: "Tracks or point lists (towers, sites), extra columns kept" },
];

const SHAPEFILE_PARTS = new Set([".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".qix", ".qmd"]);

const ACCEPT_ATTR = ACCEPTED_EXTENSIONS.join(",");

type Stage = "idle" | "uploading" | "reading" | "measuring" | "done" | "error" | "needs_crs";

const STEPS: StepperStep[] = [
  { id: "validating", label: "Validating" },
  { id: "uploading", label: "Uploading" },
  { id: "reading", label: "Reading" },
  { id: "measuring", label: "Measuring" },
  { id: "done", label: "Done" },
];

const STAGE_STEP: Record<Exclude<Stage, "idle" | "error" | "needs_crs">, number> = {
  uploading: 1,
  reading: 2,
  measuring: 3,
  done: STEPS.length,
};

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

/** Client-side checks. Returns a message, or null when the file can be uploaded. */
export function validateFiles(files: readonly File[], maxUploadMb: number | null): string | null {
  if (files.length === 0) return "No file was selected.";
  const exts = files.map((f) => extensionOf(f.name));
  if (exts.some((e) => SHAPEFILE_PARTS.has(e))) {
    return "Shapefile parts cannot be uploaded one by one. Put the .shp, .shx, .dbf and .prj files into one .zip and upload that.";
  }
  if (files.length > 1) return "Drop one file at a time.";
  const file = files[0] as File;
  const ext = exts[0] as string;
  if (!(ACCEPTED_EXTENSIONS as readonly string[]).includes(ext)) {
    return `"${file.name}" is not a supported file type. Accepted types: ${ACCEPTED_EXTENSIONS.join(", ")}.`;
  }
  if (file.size === 0) return `"${file.name}" is empty. Check that it was exported completely.`;
  if (maxUploadMb !== null && file.size > maxUploadMb * 1024 * 1024) {
    return `"${file.name}" is ${formatBytes(file.size)}, above the ${formatNumber(maxUploadMb)} MB limit. Split the file or simplify its geometry.`;
  }
  return null;
}

/** Bytes read from the start of a .json file to recognise a FlightMD report. */
const FLIGHTMD_SNIFF_BYTES = 64 * 1024;

/**
 * True when the text looks like a FlightMD report rather than GeoJSON: it declares
 * `schema_version` plus `report_id` or a GPS path, and no GeoJSON Feature/FeatureCollection type
 * (the same rule as airbrief_core/io/geojson.py).
 */
export function isFlightmdReportText(head: string): boolean {
  if (/"type"\s*:\s*"(FeatureCollection|Feature)"/.test(head)) return false;
  return /"schema_version"\s*:/.test(head) && (/"report_id"\s*:/.test(head) || /"gps_path"\s*:/.test(head));
}

async function looksLikeFlightmdReport(file: File): Promise<boolean> {
  if (![".json", ".geojson"].includes(extensionOf(file.name))) return false;
  try {
    return isFlightmdReportText(await file.slice(0, FLIGHTMD_SNIFF_BYTES).text());
  } catch {
    // Unreadable here: let the server decide.
    return false;
  }
}

/** "EPSG:32643", "32643" or a WKT / PROJ string. Returns the normalised value or an error. */
export function parseCrsOverride(raw: string): { value: string | null; error: string | null } {
  const v = raw.trim();
  if (!v) return { value: null, error: null };
  if (/^\d{4,6}$/.test(v)) return { value: `EPSG:${v}`, error: null };
  if (/^epsg\s*:/i.test(v)) {
    const m = /^epsg\s*:\s*(\d{4,6})$/i.exec(v);
    return m ? { value: `EPSG:${m[1]}`, error: null } : { value: null, error: "Use the form EPSG:32643 (4 to 6 digits)." };
  }
  if (v.length < 8) return { value: null, error: "Enter an EPSG code such as EPSG:32643, or a full WKT / PROJ string." };
  return { value: v, error: null };
}

/** "lon, lat[, alt]". Returns the triple or an error. */
export function parseOrigin(raw: string): { value: [number, number, number] | null; error: string | null } {
  const v = raw.trim();
  if (!v) return { value: null, error: null };
  const parts = v.split(/[\s,;]+/).filter(Boolean).map(Number);
  if (parts.length < 2 || parts.length > 3 || parts.some((n) => !Number.isFinite(n))) {
    return { value: null, error: "Enter longitude, latitude and optionally altitude, e.g. 77.5920, 13.0005, 0." };
  }
  const [lon, lat, alt = 0] = parts as [number, number, number?];
  if (lon < -180 || lon > 180) return { value: null, error: "Longitude must be between -180 and 180." };
  if (lat < -90 || lat > 90) return { value: null, error: "Latitude must be between -90 and 90." };
  return { value: [lon, lat, alt], error: null };
}

interface State {
  stage: Stage;
  file: File | null;
  /** 0..100 while uploading, null = unknown */
  percent: number | null;
  loaded: number;
  error: unknown;
  /** dataset id once the API answered */
  datasetId: string | null;
  failed: FileInfo | null;
  /** stage "needs_crs": the processed dataset that has no coordinate system */
  noCrs: FileInfo | null;
}

const INITIAL: State = {
  stage: "idle",
  file: null,
  percent: null,
  loaded: 0,
  error: null,
  datasetId: null,
  failed: null,
  noCrs: null,
};

/** Where keyboard focus goes after the next render (the element it was on may be gone). */
type FocusTarget = "browse" | "progress" | "error" | "crs" | "flightmd";

export function Dropzone({ className }: { className?: string }) {
  const router = useRouter();
  const meta = useMeta();
  const upload = useUpload();
  const removeDataset = useDeleteFile().mutate;
  const inputRef = useRef<HTMLInputElement>(null);
  const browseRef = useRef<HTMLButtonElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);
  const chooseAnotherRef = useRef<HTMLButtonElement>(null);
  const flightmdRef = useRef<HTMLAnchorElement>(null);
  const pendingFocus = useRef<FocusTarget | null>(null);
  /** dataset without a CRS that a successful re-processing replaces (deleted once the new one is ready) */
  const replacesRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const dragDepth = useRef(0);
  const ids = useId().replace(/:/g, "");

  const [state, setState] = useState<State>(INITIAL);
  const [dragging, setDragging] = useState(false);
  const [clientError, setClientError] = useState<string | null>(null);
  const [flightmdName, setFlightmdName] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [crsRaw, setCrsRaw] = useState("");
  const [originRaw, setOriginRaw] = useState("");
  const [crsError, setCrsError] = useState<string | null>(null);
  const [originError, setOriginError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const maxUploadMb = meta.data?.limits.max_upload_mb ?? null;
  const busy = state.stage === "uploading" || state.stage === "reading" || state.stage === "measuring";
  const disabled = DEMO_MODE || busy || state.stage === "done";

  // Large files are queued (202 PENDING): poll until processed.
  const awaiting = state.stage === "reading" || state.stage === "measuring" ? state.datasetId : null;
  const polled = useFile(awaiting);

  // Move focus once the target is on screen.
  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    const element: HTMLElement | null =
      target === "browse"
        ? browseRef.current
        : target === "progress"
          ? progressRef.current
          : target === "error"
            ? chooseAnotherRef.current
            : target === "flightmd"
              ? flightmdRef.current
              : document.getElementById(`${ids}-crs`);
    if (!element) return;
    pendingFocus.current = null;
    element.focus();
  });

  const finish = useCallback(
    (info: FileInfo) => {
      if (info.status === "FAILED") {
        replacesRef.current = null;
        setState((s) => ({ ...s, stage: "error", failed: info, datasetId: info.id, error: info.error }));
        setAnnouncement(`Processing ${info.filename} failed${info.error?.message ? `: ${info.error.message}` : "."}`);
        pendingFocus.current = "error";
        return;
      }
      if (needsCrs(info)) {
        replacesRef.current = null;
        setState((s) => ({ ...s, stage: "needs_crs", datasetId: info.id, noCrs: info }));
        setAdvancedOpen(true);
        setAnnouncement(
          `No coordinate system found in ${info.filename}, so nothing could be measured. Enter one under Advanced options and process the file again.`,
        );
        pendingFocus.current = "crs";
        return;
      }
      const replaced = replacesRef.current;
      replacesRef.current = null;
      if (replaced && replaced !== info.id) removeDataset(replaced);
      setState((s) => ({ ...s, stage: "done", datasetId: info.id, percent: 100 }));
      setAnnouncement(`${info.filename} is ready: ${info.feature_count} features. Opening the workspace.`);
      router.push(workspaceHref({ datasetId: info.id }));
    },
    [router, removeDataset],
  );

  useEffect(() => {
    const info = polled.data;
    if (!info || !awaiting || info.id !== awaiting) return;
    if (info.status === "PROCESSING") {
      setState((s) => (s.stage === "reading" ? { ...s, stage: "measuring" } : s));
    } else if (info.status === "COMPLETED" || info.status === "FAILED") {
      finish(info);
    }
  }, [polled.data, awaiting, finish]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const start = useCallback(
    (file: File) => {
      const crs = parseCrsOverride(crsRaw);
      const origin = parseOrigin(originRaw);
      setCrsError(crs.error);
      setOriginError(origin.error);
      if (crs.error || origin.error) {
        setAdvancedOpen(true);
        setClientError("Fix the advanced options below, then try again.");
        window.setTimeout(() => {
          document.getElementById(crs.error ? `${ids}-crs` : `${ids}-origin`)?.focus();
        }, 0);
        return;
      }

      const controller = new AbortController();
      abortRef.current?.abort();
      abortRef.current = controller;
      setClientError(null);
      setFlightmdName(null);
      setState({ ...INITIAL, stage: "uploading", file, percent: 0 });
      setAnnouncement(`Uploading ${file.name}.`);
      // The control that started the upload (Browse, Retry…) is replaced by the progress panel.
      pendingFocus.current = "progress";

      upload.mutate(
        {
          file,
          crsOverride: crs.value ?? undefined,
          origin: origin.value ?? undefined,
          signal: controller.signal,
          onProgress: (p) => {
            if (p.phase === "processing") {
              setState((s) => (s.stage === "uploading" ? { ...s, stage: "reading", percent: 100, loaded: p.loaded } : s));
              setAnnouncement(`Upload complete. Reading ${file.name}.`);
            } else {
              setState((s) =>
                s.stage === "uploading"
                  ? { ...s, percent: p.fraction === null ? null : Math.round(p.fraction * 100), loaded: p.loaded }
                  : s,
              );
            }
          },
        },
        {
          onSuccess: (info) => {
            if (info.status === "PENDING" || info.status === "PROCESSING") {
              setState((s) => ({
                ...s,
                stage: info.status === "PROCESSING" ? "measuring" : "reading",
                datasetId: info.id,
              }));
            } else {
              finish(info);
            }
          },
          onError: (error) => {
            replacesRef.current = null;
            if (isApiError(error) && error.code === "aborted") {
              setState(INITIAL);
              setAnnouncement("Upload cancelled.");
              pendingFocus.current = "browse";
              return;
            }
            setState((s) => ({ ...s, stage: "error", error }));
            setAnnouncement(`The upload failed${error.message ? `: ${error.message}` : "."}`);
            pendingFocus.current = "error";
          },
        },
      );
    },
    [crsRaw, originRaw, finish, ids, upload],
  );

  const accept = useCallback(
    async (list: FileList | null) => {
      // Copy the list first: the input is cleared and the drop event ends before the await.
      const files = list ? Array.from(list) : [];
      replacesRef.current = null;
      setFlightmdName(null);
      const message = validateFiles(files, maxUploadMb);
      if (message) {
        setClientError(message);
        setState(INITIAL);
        setAnnouncement(message);
        return;
      }
      const file = files[0] as File;
      if (await looksLikeFlightmdReport(file)) {
        setClientError(null);
        setState(INITIAL);
        setFlightmdName(file.name);
        setAnnouncement(`${file.name} is a FlightMD report, not map data. Open it in the Flight audit module.`);
        pendingFocus.current = "flightmd";
        return;
      }
      start(file);
    },
    [maxUploadMb, start],
  );

  const onDragEnter = (e: DragEvent<HTMLDivElement>) => {
    if (disabled || !e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  };
  const onDragOver = (e: DragEvent<HTMLDivElement>) => {
    if (disabled || !e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  };
  const onDragLeave = (e: DragEvent<HTMLDivElement>) => {
    if (disabled) return;
    e.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  };
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    if (disabled) return;
    void accept(e.dataTransfer.files);
  };

  const reset = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    replacesRef.current = null;
    setState(INITIAL);
    setClientError(null);
    setFlightmdName(null);
    // The visible Browse button, not the hidden file input.
    pendingFocus.current = "browse";
  };

  const cancel = () => {
    abortRef.current?.abort();
  };

  /** Stage "needs_crs": upload the same file again with the CRS override. */
  const reprocess = () => {
    if (!state.file) return;
    const crs = parseCrsOverride(crsRaw);
    if (!crs.value) {
      setCrsError(crs.error ?? "Enter the coordinate system of the file, e.g. EPSG:32643.");
      document.getElementById(`${ids}-crs`)?.focus();
      return;
    }
    const previous = state.noCrs?.id ?? null;
    start(state.file);
    replacesRef.current = previous;
  };

  const hintId = `${ids}-types`;
  const flightmdError = state.stage === "error" && describeError(state.error).code === "flightmd_report";
  const showProgress = busy || state.stage === "done";
  const currentStep = busy || state.stage === "done" ? STAGE_STEP[state.stage as keyof typeof STAGE_STEP] : 0;
  const currentLabel = STEPS[Math.min(currentStep, STEPS.length - 1)]?.label ?? "";

  return (
    <section aria-labelledby={`${ids}-title`} className={cn("space-y-3", className)}>
      <h2 id={`${ids}-title`} className="text-base font-semibold">
        Upload a file
      </h2>

      {DEMO_MODE ? (
        <Alert variant="info">
          <AlertTitle>Uploading is switched off in the demo</AlertTitle>
          <AlertDescription>Open one of the samples below to explore pre-computed results.</AlertDescription>
        </Alert>
      ) : null}

      <div
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        aria-describedby={hintId}
        className={cn(
          "relative rounded border-2 border-dashed bg-surface p-4 transition-colors duration-200 sm:p-6 md:p-8",
          dragging ? "border-accent bg-accent/10" : "border-border",
          DEMO_MODE && "opacity-70",
        )}
      >
        {showProgress && state.file ? (
          <div
            ref={progressRef}
            tabIndex={-1}
            role="group"
            aria-label={`Upload of ${state.file.name}`}
            aria-busy={busy}
            className="min-w-0 space-y-4 rounded-sm"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="flex min-w-0 max-w-full items-center gap-2 text-sm font-medium">
                <FileUp className="size-4 shrink-0 text-accent" aria-hidden />
                <span className="truncate">{state.file.name}</span>
                <span className="num shrink-0 text-xs font-normal text-fg-muted">{formatBytes(state.file.size)}</span>
              </p>
              {state.stage === "uploading" ? (
                <Button size="sm" variant="secondary" onClick={cancel}>
                  <X aria-hidden />
                  Cancel upload
                </Button>
              ) : state.datasetId && state.stage !== "done" ? (
                <Button size="sm" variant="secondary" asChild>
                  <Link href={workspaceHref({ datasetId: state.datasetId })}>Open now</Link>
                </Button>
              ) : null}
            </div>
            {/* Narrow screens: one line instead of five squeezed steps. */}
            <p className="text-xs font-medium sm:hidden">
              <span className="num text-fg-muted">
                Step {Math.min(currentStep + 1, STEPS.length)} of {STEPS.length}:
              </span>{" "}
              {currentLabel}
            </p>
            <Stepper steps={STEPS} current={currentStep} label="Upload progress" className="hidden sm:block" />
            {state.stage === "uploading" ? (
              <div className="space-y-1">
                <Progress value={state.percent} label="Upload progress" />
                <p className="num text-xs text-fg-muted">
                  {state.percent === null ? "Uploading…" : `${state.percent}%`} · {formatBytes(state.loaded)} of{" "}
                  {formatBytes(state.file.size)}
                </p>
              </div>
            ) : state.stage === "done" ? (
              <Progress value={100} label="Processing complete" tone="ok" />
            ) : (
              <div className="space-y-1">
                <Progress value={null} label="Processing" />
                <p className="text-xs text-fg-muted">
                  {state.stage === "reading"
                    ? "Reading layers, features and the coordinate system…"
                    : "Measuring every feature in a local projection…"}
                </p>
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3 text-center">
            <span
              aria-hidden
              className="flex size-12 items-center justify-center rounded-full border border-border bg-elevated text-fg-muted"
            >
              <Upload className="size-5" />
            </span>
            <p className="text-sm font-medium">Drag and drop a file here, or browse for one</p>
            <Button ref={browseRef} onClick={() => inputRef.current?.click()} disabled={disabled}>
              <FolderOpen aria-hidden />
              Browse…
            </Button>
            <input
              ref={inputRef}
              id={`${ids}-file`}
              type="file"
              accept={ACCEPT_ATTR}
              className="sr-only"
              tabIndex={-1}
              aria-label="Choose a file to upload"
              disabled={disabled}
              onChange={(e) => {
                void accept(e.currentTarget.files);
                e.currentTarget.value = "";
              }}
            />
            <div id={hintId} className="space-y-1 text-xs text-fg-muted">
              <p>
                {ACCEPTED_LABEL.map((a, i) => (
                  <span key={a.ext}>
                    {i > 0 ? " · " : null}
                    <span className="font-mono text-fg">{a.ext}</span> {a.what}
                  </span>
                ))}
              </p>
              <p>
                FlightMD report JSON? Use the{" "}
                <Link href={workspaceHref({ module: "flight-audit" })} className="text-accent-text underline underline-offset-4">
                  Flight audit
                </Link>{" "}
                module.
              </p>
              <p className="flex items-center justify-center gap-1">
                {meta.isPending ? (
                  <>
                    <span className="sr-only">Loading the size limit</span>
                    <Skeleton className="h-3.5 w-28" />
                  </>
                ) : maxUploadMb !== null ? (
                  <span className="num">Up to {formatNumber(maxUploadMb)} MB per file. One file at a time.</span>
                ) : (
                  <span>The size limit is set by the server. One file at a time.</span>
                )}
              </p>
            </div>
          </div>
        )}
      </div>

      {clientError ? (
        <Alert variant="danger">
          <AlertTitle>File not accepted</AlertTitle>
          <AlertDescription>
            <p>{clientError}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {flightmdName ? (
        <Alert variant="info">
          <AlertTitle>This is a FlightMD report</AlertTitle>
          <AlertDescription>
            <p>
              <span className="break-all font-medium text-fg">{flightmdName}</span> is a flight log, not map data. The
              Flight audit module checks it against the 120 m limit, the terrain and the zones it crossed.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Button size="sm" asChild>
                <Link ref={flightmdRef} href={workspaceHref({ module: "flight-audit" })}>
                  Open Flight audit
                  <ArrowRight aria-hidden />
                </Link>
              </Button>
              <Button size="sm" variant="ghost" onClick={reset}>
                Choose another file
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      ) : null}

      {state.stage === "needs_crs" && state.noCrs ? (
        <Alert variant="warn">
          <AlertTitle>No coordinate system in {state.noCrs.filename}</AlertTitle>
          <AlertDescription>
            <p>
              {formatNumber(state.noCrs.feature_count)} {state.noCrs.feature_count === 1 ? "feature was" : "features were"} read,
              but without a coordinate system (a shapefile&apos;s .prj file) none can be measured or drawn. Enter it in
              CRS override below, for example EPSG:32643 for UTM zone 43N, and process the file again. That replaces
              the dataset just created.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Button size="sm" variant="ghost" asChild>
                <Link href={workspaceHref({ datasetId: state.noCrs.id })}>Open it without a CRS</Link>
              </Button>
              <Button size="sm" variant="ghost" onClick={reset}>
                Choose another file
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      ) : null}

      {state.stage === "error" ? (
        <ErrorState
          error={state.error}
          title={state.failed ? `Could not process ${state.failed.filename}` : undefined}
          // A FlightMD report the browser check missed: uploading it again cannot help.
          onRetry={state.file && !flightmdError ? () => start(state.file as File) : undefined}
          retryLabel="Retry upload"
          actions={
            <>
              {flightmdError ? (
                <Button size="sm" asChild>
                  <Link href={workspaceHref({ module: "flight-audit" })}>
                    Open Flight audit
                    <ArrowRight aria-hidden />
                  </Link>
                </Button>
              ) : null}
              <Button ref={chooseAnotherRef} size="sm" variant="ghost" onClick={reset}>
                Choose another file
              </Button>
              {state.failed ? (
                <Button size="sm" variant="link" asChild>
                  <Link href={workspaceHref({ datasetId: state.failed.id })}>View details</Link>
                </Button>
              ) : null}
            </>
          }
        />
      ) : null}

      {!DEMO_MODE ? (
        <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
          <CollapsibleTrigger className="w-auto text-fg-muted">Advanced options</CollapsibleTrigger>
          <CollapsibleContent>
            <div className="mt-2 grid gap-4 rounded border border-border bg-surface p-4 md:grid-cols-2">
              <Field
                id={`${ids}-crs`}
                label="CRS override"
                hint="Only when the file has no .prj or the wrong one, e.g. EPSG:32643 (UTM 43N)."
                error={crsError}
              >
                {(p) => (
                  <Input
                    {...p}
                    value={crsRaw}
                    placeholder="EPSG:32643"
                    autoComplete="off"
                    spellCheck={false}
                    disabled={busy}
                    onChange={(e) => setCrsRaw(e.target.value)}
                    onBlur={() => setCrsError(parseCrsOverride(crsRaw).error)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && state.stage === "needs_crs") {
                        e.preventDefault();
                        reprocess();
                      }
                    }}
                  />
                )}
              </Field>
              <Field
                id={`${ids}-origin`}
                label="Track origin"
                unit="lon, lat, alt m"
                hint="For CSV tracks (or a zip of per-drone CSVs) in local metres without an origin header."
                error={originError}
              >
                {(p) => (
                  <Input
                    {...p}
                    value={originRaw}
                    placeholder="77.5920, 13.0005, 0"
                    inputMode="decimal"
                    autoComplete="off"
                    spellCheck={false}
                    disabled={busy}
                    onChange={(e) => setOriginRaw(e.target.value)}
                    onBlur={() => setOriginError(parseOrigin(originRaw).error)}
                  />
                )}
              </Field>
              {state.stage === "needs_crs" && state.file ? (
                <div className="md:col-span-2">
                  <Button size="sm" onClick={reprocess}>
                    <RotateCw aria-hidden />
                    Process {state.file.name} again with this CRS
                  </Button>
                </div>
              ) : null}
              <p className="flex items-start gap-1.5 text-xs text-fg-muted md:col-span-2">
                <Info className="mt-px size-3.5 shrink-0" aria-hidden />
                These options apply to the next upload.
              </p>
            </div>
          </CollapsibleContent>
        </Collapsible>
      ) : null}

      <p className="sr-only" aria-live="polite" role="status">
        {announcement}
      </p>
    </section>
  );
}
