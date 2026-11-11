/**
 * Typed client for the AirBrief API (docs/SPEC.md §4).
 *
 * - Base URL: `NEXT_PUBLIC_API_URL` (default http://localhost:8000).
 * - Every failure is thrown as `ApiError { status, code, message, details }`.
 * - Demo mode (`NEXT_PUBLIC_DEMO=1`): reads pre-built JSON from `${basePath}/demo/...`
 *   (see README "Demo mode"). Mutations throw `ApiError` with code `demo_readonly`, except
 *   `loadSample`, which resolves the sample's pre-built dataset via `demo/samples.json`.
 */

import type {
  AnalysesPage,
  AnalysisExportFormat,
  AnalysisInfo,
  AnalysisKind,
  AnalysisRequest,
  ApiErrorInfo,
  DatasetFeatureCollection,
  Ed318Limits,
  FeatureRecord,
  FileExportFormat,
  FileInfo,
  FilesPage,
  Health,
  MeasurementsPage,
  MeasurementsQuery,
  Meta,
  SampleInfo,
} from "./types";
import type {
  BBoxList,
  ContextLayerKey,
  ContextResponse,
  ContextZoneProfile,
  GeometryParseRequest,
  GeometryParseResponse,
  PlaceResult,
} from "./types";
import type { Brief, BriefRequest, ExportTableInfo } from "./types";
import { mergeDemoBriefs } from "./brief-demo";
import { demoContext, demoParseGeometry, demoPlaces } from "./plan-demo";
import { withBasePath } from "./utils";

/** API base URL without a trailing slash. */
export const API_URL: string = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
  .trim()
  .replace(/\/+$/, "");

/** True when the app runs as the static, read-only demo. */
export const DEMO_MODE: boolean = process.env.NEXT_PUBLIC_DEMO === "1";

/** An API failure. `status` is 0 for network errors and aborted requests. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** True for errors that a retry might fix (network, 5xx, 429). */
  get retryable(): boolean {
    return this.status === 0 || this.status === 429 || this.status >= 500;
  }

  toJSON(): ApiErrorInfo & { status: number } {
    return { status: this.status, code: this.code, message: this.message, details: this.details };
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function defaultCodeForStatus(status: number): string {
  if (status === 400) return "bad_request";
  if (status === 401 || status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 413) return "file_too_large";
  if (status === 415) return "unsupported_format";
  if (status === 422) return "invalid_params";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "internal_error";
  return "http_error";
}

/**
 * Build an ApiError from a parsed response body. Understands the AirBrief envelope
 * `{"error": {code, message, details}}` and FastAPI's validation shape `{"detail": [...]}`.
 */
export function errorFromBody(status: number, body: unknown, fallbackMessage?: string): ApiError {
  if (isRecord(body) && isRecord(body.error)) {
    const err = body.error;
    const code = typeof err.code === "string" ? err.code : defaultCodeForStatus(status);
    const message = typeof err.message === "string" ? err.message : fallbackMessage ?? `Request failed (${status})`;
    const details = isRecord(err.details) ? err.details : {};
    return new ApiError(status, code, message, details);
  }
  if (isRecord(body) && "detail" in body) {
    const detail = body.detail;
    if (typeof detail === "string") {
      return new ApiError(status, defaultCodeForStatus(status), detail);
    }
    if (Array.isArray(detail)) {
      const parts = detail
        .map((d) => {
          if (!isRecord(d)) return null;
          const loc = Array.isArray(d.loc) ? d.loc.filter((l) => l !== "body").join(".") : "";
          const msg = typeof d.msg === "string" ? d.msg : "";
          return loc ? `${loc}: ${msg}` : msg;
        })
        .filter((s): s is string => Boolean(s));
      return new ApiError(status, "invalid_params", parts.join("; ") || "Invalid parameters", { errors: detail });
    }
  }
  return new ApiError(status, defaultCodeForStatus(status), fallbackMessage ?? `Request failed (${status})`);
}

function networkError(cause: unknown): ApiError {
  if (cause instanceof DOMException && cause.name === "AbortError") {
    return new ApiError(0, "aborted", "The request was cancelled.");
  }
  return new ApiError(
    0,
    "network_error",
    DEMO_MODE
      ? "Could not load the demo data."
      : `Could not reach the AirBrief API at ${API_URL}.`,
    { cause: cause instanceof Error ? cause.message : String(cause) },
  );
}

/** Human-readable explanation of any error: what happened and how to fix it. */
export interface ErrorDescription {
  title: string;
  message: string;
  fix: string;
  retryable: boolean;
  code: string;
}

const FIX_BY_CODE: Record<string, { title: string; fix: string }> = {
  network_error: {
    title: "Cannot reach the server",
    fix: "Check that the AirBrief API is running and that NEXT_PUBLIC_API_URL points to it, then retry.",
  },
  aborted: { title: "Request cancelled", fix: "Start the action again when you are ready." },
  demo_readonly: {
    title: "Not available in the demo",
    fix: "The public demo is read-only. Run AirBrief locally to upload files and run new analyses.",
  },
  not_found: { title: "Not found", fix: "It may have been deleted or expired. Go back and pick another item." },
  unsupported_format: {
    title: "Unsupported file",
    fix: "Upload a .zip (shapefile, or KML/GeoJSON/GPX/CSV files), .kml, .kmz, .geojson/.json, .gpx or .csv file.",
  },
  file_too_large: { title: "File too large", fix: "Split the file or simplify its geometry, then upload again." },
  empty_file: { title: "Empty file", fix: "Check that the file was exported completely and try again." },
  unsafe_archive: {
    title: "Unsafe archive",
    fix: "Re-create the zip without absolute paths, '..' entries or symbolic links.",
  },
  archive_too_large: { title: "Archive too large", fix: "Reduce the uncompressed size of the zip and try again." },
  too_many_files: { title: "Too many files in the archive", fix: "Zip only the layers you need." },
  missing_component: {
    title: "Shapefile incomplete",
    fix: "Include the .shp, .shx and .dbf files (and .prj for the coordinate system) in the zip.",
  },
  no_supported_content: { title: "Nothing to read", fix: "The file has no supported layers. Check its contents." },
  corrupt_file: { title: "File could not be read", fix: "Re-export the file from its source application." },
  flightmd_report: {
    title: "This is a FlightMD report",
    fix: "Open the Flight audit module and drop the report there: it checks the flight against height limits and zones.",
  },
  too_many_features: { title: "Too many features", fix: "Split the dataset into smaller files." },
  invalid_crs: {
    title: "Unknown coordinate system",
    fix: "Enter a valid CRS override such as EPSG:32643, or include a .prj file.",
  },
  invalid_params: { title: "Invalid parameters", fix: "Correct the highlighted fields and run again." },
  invalid_geometry: { title: "Invalid geometry", fix: "Select a different feature or repair the geometry." },
  geometry_type_not_supported: {
    title: "Geometry type not supported",
    fix: "Select features of the type this module expects (for example a polygon).",
  },
  area_too_large: { title: "Area too large", fix: "Select a smaller area or lower the grid resolution." },
  datapack_missing: {
    title: "No data for this area",
    fix: "Install a data pack that covers the area — `python -m seed.datapack --synthetic` (offline demo) or `--region bengaluru` (real data) — or pick an area inside the covered region.",
  },
  layer_missing: { title: "Data layer missing", fix: "Re-create the data pack with the required layers." },
  out_of_coverage: { title: "Outside data coverage", fix: "Pick an area inside an installed data pack." },
  no_tracks: { title: "No flight tracks", fix: "Upload a CSV or KML with timed tracks for every drone." },
  report_invalid: { title: "Unrecognised FlightMD report", fix: "Use a FlightMD report JSON (schema 1.0 to 1.5)." },
  no_features: { title: "No features", fix: "Upload a file with at least one feature." },
  fetch_failed: {
    title: "Download failed",
    fix: "Check the URL is a public https link and try again, or download the report JSON and upload it on the Upload JSON tab.",
  },
  rate_limited: { title: "Too many requests", fix: "Wait for the time shown and try again." },
  cancelled: { title: "Cancelled", fix: "Run the analysis again when you are ready." },
  interrupted: { title: "Interrupted", fix: "The server restarted while this ran. Run it again." },
  internal_error: { title: "Server error", fix: "Retry. If it keeps failing, check the API logs." },
  url_not_allowed: {
    title: "Link not allowed",
    fix: "Use a public https link to a FlightMD report (private, local and plain-http addresses are blocked), or upload the JSON.",
  },
  dataset_not_ready: { title: "Dataset still processing", fix: "Wait until the dataset shows Completed, then run again." },
  analysis_not_finished: { title: "Analysis not finished", fix: "Wait for the analysis to complete before exporting it." },
  sample_not_dataset: {
    title: "This sample is not a map file",
    fix: "FlightMD sample reports are used in the Flight audit module: open it and choose the sample there.",
  },
  method_not_allowed: { title: "Action not supported", fix: "This request is not supported by the server version you are running." },
  too_complex: {
    title: "Geometry too complex",
    fix: "Simplify the geometry (fewer vertices) or split the file, then upload again.",
  },
  request_too_large: { title: "Request too large", fix: "Send a smaller request (for example a smaller report file)." },
  listing_disabled: {
    title: "Listing is off on this server",
    fix: "This public server only shows the datasets and analyses created in your own browser.",
  },
  owner_token_required: {
    title: "Only the creator can do this",
    fix: "Deleting or cancelling needs the owner token, which only the browser that created the item has.",
  },
  owner_token_invalid: {
    title: "Not the owner",
    fix: "The owner token does not match. Use the browser that created this item.",
  },
  no_route: {
    title: "No corridor found",
    fix: "Turn off red-zone avoidance, lower the cruise height clearance or widen the study area, then try again.",
  },
  invalid_coordinates: {
    title: "Coordinates not understood",
    fix: "Type latitude, longitude in degrees, e.g. 12.9716, 77.5946 or 12°58′17.8″N 77°35′40.6″E, one point per line.",
  },
  demo_unavailable: {
    title: "Not available in the demo",
    fix: "Paste GeoJSON or coordinates instead, or run AirBrief yourself to read WKT.",
  },
  bundle_too_large: {
    title: "Bundle too large",
    fix: "Include fewer analyses, or ones with smaller grids, in the brief and download it again.",
  },
};

/** Turn any thrown value into `{title, message, fix, retryable, code}` for ErrorState. */
export function describeError(error: unknown): ErrorDescription {
  if (isApiError(error)) {
    const known = FIX_BY_CODE[error.code];
    const retryAfter = error.details?.retry_after_s;
    const rateFix =
      error.code === "rate_limited" && typeof retryAfter === "number" && Number.isFinite(retryAfter)
        ? `Wait ${Math.ceil(retryAfter)} s and try again.`
        : null;
    return {
      title: known?.title ?? "Something went wrong",
      message: error.message,
      fix: rateFix ?? known?.fix ?? (error.retryable ? "Retry in a moment." : "Check your input and try again."),
      retryable: error.retryable || error.code === "fetch_failed",
      code: error.code,
    };
  }
  if (isRecord(error) && typeof error.code === "string" && typeof error.message === "string") {
    // An ApiErrorInfo stored on FileInfo / AnalysisInfo.
    const known = FIX_BY_CODE[error.code];
    return {
      title: known?.title ?? "Something went wrong",
      message: error.message,
      fix: known?.fix ?? "Check your input and try again.",
      retryable: error.code === "internal_error" || error.code === "interrupted",
      code: error.code,
    };
  }
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : "Unknown error";
  return {
    title: "Something went wrong",
    message,
    fix: "Retry. If it keeps failing, reload the page.",
    retryable: true,
    code: "unknown",
  };
}

type Query = Record<string, string | number | boolean | null | undefined>;

function queryString(query?: Query): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

/** Absolute URL for an API path such as `/api/files/`. */
export function apiUrl(path: string, query?: Query): string {
  return `${API_URL}${path}${queryString(query)}`;
}

async function parseBody(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  query?: Query;
  json?: unknown;
  body?: BodyInit;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

/** Owner token header: the item's token on DELETE and cancel, the dataset's when creating an analysis on it (api/ownership.py). */
export const OWNER_TOKEN_HEADER = "X-Owner-Token";

function ownerHeaders(ownerToken: string | null | undefined): Record<string, string> | undefined {
  return ownerToken ? { [OWNER_TOKEN_HEADER]: ownerToken } : undefined;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(options.headers ?? {}) };
  let body: BodyInit | undefined = options.body;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }
  let res: Response;
  try {
    res = await fetch(apiUrl(path, options.query), {
      method: options.method ?? "GET",
      headers,
      body,
      signal: options.signal,
      credentials: "omit",
    });
  } catch (cause) {
    throw networkError(cause);
  }
  const parsed = await parseBody(res);
  if (!res.ok) throw errorFromBody(res.status, parsed, res.statusText || undefined);
  return parsed as T;
}

const demoCache = new Map<string, Promise<unknown>>();

/**
 * Fetch `${basePath}/demo/${path}` (cached per session). 404 → ApiError not_found.
 * The shared fetch is never aborted (other callers may be waiting on it); an aborted caller
 * simply stops waiting.
 */
async function demo<T>(path: string, signal?: AbortSignal): Promise<T> {
  const url = withBasePath(`/demo/${path}`);
  let pending = demoCache.get(url);
  if (!pending) {
    pending = (async () => {
      let res: Response;
      try {
        res = await fetch(url, { headers: { Accept: "application/json" } });
      } catch (cause) {
        throw networkError(cause);
      }
      if (res.status === 404) throw new ApiError(404, "not_found", "This item is not part of the demo.");
      const parsed = await parseBody(res);
      if (!res.ok) throw errorFromBody(res.status, parsed);
      return parsed;
    })();
    demoCache.set(url, pending);
    pending.catch(() => demoCache.delete(url));
  }
  if (signal) {
    if (signal.aborted) throw new ApiError(0, "aborted", "The request was cancelled.");
    const shared = pending;
    return new Promise<T>((resolve, reject) => {
      const onAbort = () => reject(new ApiError(0, "aborted", "The request was cancelled."));
      signal.addEventListener("abort", onAbort, { once: true });
      shared.then(
        (value) => {
          signal.removeEventListener("abort", onAbort);
          resolve(value as T);
        },
        (error: unknown) => {
          signal.removeEventListener("abort", onAbort);
          reject(error);
        },
      );
    });
  }
  return (await pending) as T;
}

function demoReadonly(action: string): ApiError {
  return new ApiError(403, "demo_readonly", `${action} is not available in the read-only demo.`);
}

function paginate<T>(items: T[], limit?: number, offset?: number): T[] {
  const start = Math.max(0, offset ?? 0);
  return limit === undefined ? items.slice(start) : items.slice(start, start + Math.max(0, limit));
}

/** GET /health */
export async function getHealth(signal?: AbortSignal): Promise<Health> {
  if (DEMO_MODE) {
    const meta = await getMeta(signal);
    return {
      status: "ok",
      version: meta.version,
      schema_version: meta.schema_version,
      db: "demo",
      datapacks: meta.datapacks.map((d) => d.name),
      native_backend: "python",
    };
  }
  return request<Health>("/health", { signal });
}

/** GET /api/meta/ */
export async function getMeta(signal?: AbortSignal): Promise<Meta> {
  if (DEMO_MODE) return demo<Meta>("meta.json", signal);
  return request<Meta>("/api/meta/", { signal });
}

export interface ListParams {
  limit?: number;
  offset?: number;
  /** only these ids (this browser's own datasets); an empty list returns an empty page */
  ids?: readonly string[] | null;
}

/** GET /api/files/?ids=&limit=&offset= */
export async function listFiles(params: ListParams = {}, signal?: AbortSignal): Promise<FilesPage> {
  if (params.ids && params.ids.length === 0) return { items: [], total: 0 };
  if (DEMO_MODE) {
    const page = await demo<FilesPage>("files.json", signal);
    const wanted = params.ids ? new Set(params.ids) : null;
    const items = wanted ? page.items.filter((f) => wanted.has(f.id)) : page.items;
    return { items: paginate(items, params.limit, params.offset), total: wanted ? items.length : page.total };
  }
  return request<FilesPage>("/api/files/", {
    query: { ids: params.ids ? params.ids.join(",") : undefined, limit: params.limit, offset: params.offset },
    signal,
  });
}

/** GET /api/files/{id}/ */
export async function getFile(id: string, signal?: AbortSignal): Promise<FileInfo> {
  if (DEMO_MODE) return demo<FileInfo>(`files/${encodeURIComponent(id)}.json`, signal);
  return request<FileInfo>(`/api/files/${encodeURIComponent(id)}/`, { signal });
}

/** GET /api/files/{id}/measurements/?limit=&offset=&geometry_type=&status= */
export async function getMeasurements(
  id: string,
  query: MeasurementsQuery = {},
  signal?: AbortSignal,
): Promise<MeasurementsPage> {
  const limit = query.limit ?? 100;
  const offset = query.offset ?? 0;
  if (DEMO_MODE) {
    const page = await demo<MeasurementsPage>(`files/${encodeURIComponent(id)}/measurements.json`, signal);
    const filtered = page.items.filter(
      (item) =>
        (!query.geometry_type || item.geometry_type === query.geometry_type) &&
        (!query.status || item.measurement.status === query.status),
    );
    return { ...page, items: paginate(filtered, limit, offset), total: filtered.length, limit, offset };
  }
  return request<MeasurementsPage>(`/api/files/${encodeURIComponent(id)}/measurements/`, {
    query: { limit, offset, geometry_type: query.geometry_type, status: query.status },
    signal,
  });
}

/** GET /api/files/{id}/features/ (GeoJSON FeatureCollection, WGS84) */
export async function getFeatures(id: string, signal?: AbortSignal): Promise<DatasetFeatureCollection> {
  if (DEMO_MODE) return demo<DatasetFeatureCollection>(`files/${encodeURIComponent(id)}/features.json`, signal);
  return request<DatasetFeatureCollection>(`/api/files/${encodeURIComponent(id)}/features/`, { signal });
}

/** GET /api/files/{id}/features/{index}/ */
export async function getFeature(id: string, index: number, signal?: AbortSignal): Promise<FeatureRecord> {
  if (DEMO_MODE) return demo<FeatureRecord>(`files/${encodeURIComponent(id)}/features/${index}.json`, signal);
  return request<FeatureRecord>(`/api/files/${encodeURIComponent(id)}/features/${index}/`, { signal });
}

/**
 * Download URL for GET /api/files/{id}/export/?format=. Returns null in demo mode
 * (build the export client-side from the loaded data instead, e.g. with `downloadBlob`).
 */
export function fileExportUrl(id: string, format: FileExportFormat, ed318Limits?: Ed318Limits): string | null {
  if (DEMO_MODE) return null;
  return apiUrl(`/api/files/${encodeURIComponent(id)}/export/`, { format, ed318_limits: format === "ed318" ? ed318Limits : undefined });
}

/**
 * GET an export URL → the file and the name the API gave it (null without one). A non-2xx answer
 * throws its ApiError instead, so an error body is never saved as the file.
 */
export async function downloadExport(url: string): Promise<{ blob: Blob; filename: string | null }> {
  let res: Response;
  try {
    res = await fetch(url, { credentials: "omit" });
  } catch (cause) {
    throw networkError(cause);
  }
  if (!res.ok) throw errorFromBody(res.status, await parseBody(res), res.statusText || undefined);
  const match = /filename="([A-Za-z0-9._-]+)"/.exec(res.headers.get("Content-Disposition") ?? "");
  return { blob: await res.blob(), filename: match?.[1] ?? null };
}

/** DELETE /api/files/{id}/ */
export async function deleteFile(id: string, ownerToken?: string | null): Promise<void> {
  if (DEMO_MODE) throw demoReadonly("Deleting datasets");
  await request<null>(`/api/files/${encodeURIComponent(id)}/`, { method: "DELETE", headers: ownerHeaders(ownerToken) });
}

/**
 * DELETE /api/files/{id}/ while the page is being closed: a `keepalive` request the browser
 * finishes after the page is gone. Fire and forget; the caller keeps its own record so the
 * deletion is retried on the next load if this one never arrives.
 */
export function deleteFileOnUnload(id: string, ownerToken?: string | null): void {
  if (DEMO_MODE) return;
  try {
    void fetch(apiUrl(`/api/files/${encodeURIComponent(id)}/`), {
      method: "DELETE",
      headers: { Accept: "application/json", ...(ownerHeaders(ownerToken) ?? {}) },
      credentials: "omit",
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    // the browser refused to start the request; the next load retries it
  }
}

export interface UploadProgress {
  /** bytes sent so far */
  loaded: number;
  /** total bytes, or null when unknown */
  total: number | null;
  /** 0..1, or null when unknown */
  fraction: number | null;
  /** "uploading" while bytes are sent; "processing" once the server has the whole file */
  phase: "uploading" | "processing";
}

export interface UploadOptions {
  /** CRS override such as "EPSG:32643" (form field `crs_override`) */
  crsOverride?: string;
  /** Local origin for x/y/z CSV tracks: [lon, lat, alt] (form field `origin` = "lon,lat,alt") */
  origin?: [number, number, number];
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
}

/**
 * POST /api/files/ (multipart). Uses XMLHttpRequest so upload progress is reported.
 * Resolves with FileInfo: 201 COMPLETED/FAILED for small files, 202 PENDING for large ones
 * (poll with `useFile`).
 */
export function uploadFile(file: File, options: UploadOptions = {}): Promise<FileInfo> {
  if (DEMO_MODE) return Promise.reject(demoReadonly("Uploading files"));
  return new Promise<FileInfo>((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (options.crsOverride?.trim()) form.append("crs_override", options.crsOverride.trim());
    if (options.origin) form.append("origin", options.origin.join(","));

    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl("/api/files/"));
    xhr.setRequestHeader("Accept", "application/json");
    xhr.responseType = "text";

    const report = (p: UploadProgress) => options.onProgress?.(p);

    xhr.upload.onprogress = (event: ProgressEvent) => {
      const total = event.lengthComputable ? event.total : null;
      report({
        loaded: event.loaded,
        total,
        fraction: total ? Math.min(1, event.loaded / total) : null,
        phase: "uploading",
      });
    };
    xhr.upload.onload = () => {
      report({ loaded: file.size, total: file.size, fraction: 1, phase: "processing" });
    };

    const onAbort = () => xhr.abort();
    options.signal?.addEventListener("abort", onAbort, { once: true });
    const cleanup = () => options.signal?.removeEventListener("abort", onAbort);

    xhr.onload = () => {
      cleanup();
      let parsed: unknown = null;
      if (xhr.responseText) {
        try {
          parsed = JSON.parse(xhr.responseText) as unknown;
        } catch {
          parsed = xhr.responseText;
        }
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(parsed as FileInfo);
      } else {
        reject(errorFromBody(xhr.status, parsed, xhr.statusText || undefined));
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(networkError(new Error("XMLHttpRequest failed")));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new ApiError(0, "aborted", "The upload was cancelled."));
    };
    xhr.ontimeout = () => {
      cleanup();
      reject(new ApiError(0, "network_error", "The upload timed out."));
    };

    if (options.signal?.aborted) {
      cleanup();
      reject(new ApiError(0, "aborted", "The upload was cancelled."));
      return;
    }
    xhr.send(form);
  });
}

/** GET /api/samples/ */
export async function listSamples(signal?: AbortSignal): Promise<SampleInfo[]> {
  if (DEMO_MODE) return demo<SampleInfo[]>("samples.json", signal);
  return request<SampleInfo[]>("/api/samples/", { signal });
}

/** POST /api/samples/{name}/ → FileInfo. In demo mode resolves the pre-built dataset. */
export async function loadSample(name: string): Promise<FileInfo> {
  if (DEMO_MODE) {
    const samples = await demo<SampleInfo[]>("samples.json");
    const sample = samples.find((s) => s.name === name);
    if (!sample) throw new ApiError(404, "not_found", `Sample "${name}" is not part of the demo.`);
    if (!sample.dataset_id) {
      throw new ApiError(404, "not_found", `Sample "${name}" has no pre-built dataset in the demo.`);
    }
    return getFile(sample.dataset_id);
  }
  return request<FileInfo>(`/api/samples/${encodeURIComponent(name)}/`, { method: "POST" });
}

/** Download URL for the raw sample file (null in demo mode). */
export function sampleDownloadUrl(sample: string | Pick<SampleInfo, "name" | "download">): string | null {
  const name = typeof sample === "string" ? sample : sample.name;
  if (DEMO_MODE) {
    const download = typeof sample === "string" ? null : sample.download ?? null;
    return download ? withBasePath(`/demo/${download.replace(/^\/+/, "")}`) : null;
  }
  return apiUrl(`/api/samples/${encodeURIComponent(name)}/download/`);
}

/**
 * POST /api/analyses/ → 202 AnalysisInfo (PENDING). Poll with `useAnalysis`. Pass the dataset's
 * owner token when this browser has it: only then does the run count as the owner's evidence.
 */
export async function createAnalysis<K extends AnalysisKind>(
  body: AnalysisRequest<K>,
  datasetToken?: string | null,
): Promise<AnalysisInfo<K>> {
  if (DEMO_MODE) throw demoReadonly("Running new analyses");
  return request<AnalysisInfo<K>>("/api/analyses/", { method: "POST", json: body, headers: ownerHeaders(datasetToken) });
}

export interface ListAnalysesParams {
  dataset_id?: string | null;
  /**
   * Only these ids (this browser's own analyses); an empty list returns an empty page. With
   * `dataset_id`: the dataset's owner analyses plus these ids, where the API enforces owner tokens.
   */
  ids?: readonly string[] | null;
}

/** GET /api/analyses/?dataset_id=&ids= */
export async function listAnalyses(params: ListAnalysesParams = {}, signal?: AbortSignal): Promise<AnalysesPage> {
  if (!params.dataset_id && params.ids && params.ids.length === 0) return { items: [], total: 0 };
  if (DEMO_MODE) {
    const page = await demo<AnalysesPage>("analyses/index.json", signal);
    // every demo analysis is the owner's, so a dataset lists all of its own
    const wanted = params.ids && !params.dataset_id ? new Set(params.ids) : null;
    const items = page.items.filter(
      (a) => (!params.dataset_id || a.dataset_id === params.dataset_id) && (!wanted || wanted.has(a.id)),
    );
    return { items, total: items.length };
  }
  return request<AnalysesPage>("/api/analyses/", {
    query: { dataset_id: params.dataset_id, ids: params.ids ? params.ids.join(",") : undefined },
    signal,
  });
}

/** GET /api/analyses/{id}/ */
export async function getAnalysis(id: string, signal?: AbortSignal): Promise<AnalysisInfo> {
  if (DEMO_MODE) return demo<AnalysisInfo>(`analyses/${encodeURIComponent(id)}.json`, signal);
  return request<AnalysisInfo>(`/api/analyses/${encodeURIComponent(id)}/`, { signal });
}

/** POST /api/analyses/{id}/cancel/ (202) */
export async function cancelAnalysis(id: string, ownerToken?: string | null): Promise<void> {
  if (DEMO_MODE) throw demoReadonly("Cancelling analyses");
  await request<unknown>(`/api/analyses/${encodeURIComponent(id)}/cancel/`, { method: "POST", headers: ownerHeaders(ownerToken) });
}

/** DELETE /api/analyses/{id}/ (204) */
export async function deleteAnalysis(id: string, ownerToken?: string | null): Promise<void> {
  if (DEMO_MODE) throw demoReadonly("Deleting analyses");
  await request<null>(`/api/analyses/${encodeURIComponent(id)}/`, { method: "DELETE", headers: ownerHeaders(ownerToken) });
}

/**
 * Download URL for GET /api/analyses/{id}/export/?format=. Returns null in demo mode
 * (build the export client-side from `AnalysisInfo.result`).
 */
export function analysisExportUrl(
  id: string,
  format: AnalysisExportFormat,
  options: { table?: string | null; ed318Limits?: Ed318Limits } = {},
): string | null {
  if (DEMO_MODE) return null;
  return apiUrl(`/api/analyses/${encodeURIComponent(id)}/export/`, {
    format,
    table: format === "csv" ? options.table ?? undefined : undefined,
    ed318_limits: format === "ed318" ? options.ed318Limits : undefined,
  });
}

/** GET /api/analyses/{id}/export/tables/ — the CSV tables of an analysis (the API only: none in the demo). */
export async function getAnalysisTables(id: string, signal?: AbortSignal): Promise<ExportTableInfo[]> {
  if (DEMO_MODE) throw new ApiError(0, "demo_unavailable", "CSV tables are built by the AirBrief API.");
  return request<ExportTableInfo[]>(`/api/analyses/${encodeURIComponent(id)}/export/tables/`, { signal });
}

/**
 * POST /api/briefs/ → the operation evidence brief. In demo mode it is merged in the browser from
 * the pre-built one-analysis briefs (`demo/briefs/{id}.json`, lib/brief-demo.ts): the same
 * sections, without the brief and operation hashes, which only the API computes.
 */
export async function createBrief(body: BriefRequest, signal?: AbortSignal): Promise<Brief> {
  if (DEMO_MODE) {
    const parts = await Promise.all(body.analysis_ids.map((id) => demo<Brief>(`briefs/${encodeURIComponent(id)}.json`, signal)));
    const analyses = await Promise.all(body.analysis_ids.map((id) => getAnalysis(id, signal)));
    const meta = await getMeta(signal);
    return mergeDemoBriefs(parts, body.operation, meta.uas_presets, analyses);
  }
  return request<Brief>("/api/briefs/", { method: "POST", json: body, signal });
}

/** POST /api/briefs/bundle/ → the zip and its file name (the API only). */
export async function downloadBriefBundle(body: BriefRequest): Promise<{ blob: Blob; filename: string }> {
  if (DEMO_MODE) throw demoReadonly("Downloading the brief bundle");
  let res: Response;
  try {
    res = await fetch(apiUrl("/api/briefs/bundle/"), {
      method: "POST",
      headers: { Accept: "application/zip, application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "omit",
    });
  } catch (cause) {
    throw networkError(cause);
  }
  if (!res.ok) throw errorFromBody(res.status, await parseBody(res), res.statusText || undefined);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="([A-Za-z0-9._-]+)"/.exec(disposition);
  return { blob: await res.blob(), filename: match?.[1] ?? "airbrief_brief.zip" };
}

/** POST /api/flightmd/fetch/ {url} → the FlightMD report JSON (server-side, SSRF-guarded). */
export async function fetchFlightmd(url: string): Promise<Record<string, unknown>> {
  if (DEMO_MODE) throw demoReadonly("Fetching FlightMD reports");
  return request<Record<string, unknown>>("/api/flightmd/fetch/", { method: "POST", json: { url } });
}

// Context layers, places and geometry parse (api/routers/context.py). The demo answers these
// from demo/context.json w/ the browser's parser (lib/plan-demo.ts).

/** The demo pack's exported context, or null when the export has none. */
async function demoContextFile(signal?: AbortSignal): Promise<ContextResponse | null> {
  try {
    return await demo<ContextResponse>("context.json", signal);
  } catch (error) {
    if (isApiError(error) && error.status === 404) return null;
    throw error;
  }
}

/** GET /api/context/?bbox=&layers=&limit=&profile= (a view of at most 2° × 2°; `profile` draws the rings) */
export async function getContext(
  params: { bbox: BBoxList; layers: readonly ContextLayerKey[]; limit?: number; profile?: ContextZoneProfile },
  signal?: AbortSignal,
): Promise<ContextResponse> {
  if (DEMO_MODE) {
    // the export has the default rings only
    const all = await demoContextFile(signal);
    if (!all) throw new ApiError(404, "not_found", "Context layers are not part of this demo build.");
    return demoContext(all, params.bbox, params.layers);
  }
  return request<ContextResponse>("/api/context/", {
    query: {
      bbox: params.bbox.map((v) => v.toFixed(6)).join(","),
      layers: params.layers.join(","),
      limit: params.limit,
      profile: params.profile,
    },
    signal,
  });
}

/** GET /api/places/?q=&limit= — typed coordinates first, then data-pack aerodromes. */
export async function getPlaces(q: string, limit = 10, signal?: AbortSignal): Promise<PlaceResult[]> {
  if (DEMO_MODE) return demoPlaces(await demoContextFile(signal), q, limit);
  return request<PlaceResult[]>("/api/places/", { query: { q, limit }, signal });
}

/** POST /api/geometry/parse/ — pasted GeoJSON, WKT or coordinates → one WGS84 geometry. */
export async function parseGeometry(body: GeometryParseRequest): Promise<GeometryParseResponse> {
  if (DEMO_MODE) {
    if (/^\s*(SRID\s*=|POINT|LINESTRING|POLYGON|MULTI|GEOMETRYCOLLECTION)/i.test(body.text)) {
      throw new ApiError(0, "demo_unavailable", "Reading WKT needs the AirBrief API; the static demo reads GeoJSON and coordinates.");
    }
    try {
      return demoParseGeometry(body.text, body.shape ?? "auto");
    } catch (error) {
      throw new ApiError(422, "invalid_geometry", error instanceof Error ? error.message : String(error));
    }
  }
  return request<GeometryParseResponse>("/api/geometry/parse/", { method: "POST", json: body });
}

/** Everything above as one object, convenient for injection in tests. */
export const api = {
  getHealth,
  getMeta,
  listFiles,
  getFile,
  getMeasurements,
  getFeatures,
  getFeature,
  fileExportUrl,
  deleteFile,
  uploadFile,
  listSamples,
  loadSample,
  sampleDownloadUrl,
  createAnalysis,
  listAnalyses,
  getAnalysis,
  cancelAnalysis,
  deleteAnalysis,
  analysisExportUrl,
  getAnalysisTables,
  createBrief,
  downloadBriefBundle,
  fetchFlightmd,
  getContext,
  getPlaces,
  parseGeometry,
} as const;
