"use client";

/**
 * TanStack Query hooks for the API. Keys come from `queryKeys` so callers can invalidate
 * precisely, and mutations update or invalidate what they touch. One file or analysis polls every
 * second while it is active; lists poll every 2 s while anything they list is.
 */

import {
  keepPreviousData,
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type QueryClientConfig,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import {
  ApiError,
  DEMO_MODE,
  cancelAnalysis,
  createAnalysis,
  deleteAnalysis,
  deleteFile,
  fetchFlightmd,
  getAnalysis,
  getFeature,
  getFeatures,
  getFile,
  getHealth,
  getMeasurements,
  getMeta,
  listAnalyses,
  listFiles,
  listSamples,
  loadSample,
  uploadFile,
  type ListParams,
  type UploadProgress,
} from "./api";
import {
  isAnalysisActive,
  isFileActive,
  type AnalysesPage,
  type AnalysisInfo,
  type AnalysisKind,
  type AnalysisRequest,
  type DatasetFeatureCollection,
  type FeatureRecord,
  type FileInfo,
  type FilesPage,
  type Health,
  type MeasurementsPage,
  type MeasurementsQuery,
  type Meta,
  type SampleInfo,
} from "./types";
import { useHistory } from "./store";
import { useQueries, type UseQueryResult as ContextQueryResult } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { getContext, getPlaces, parseGeometry } from "./api";
import { createBrief, getAnalysisTables } from "./api";
import type { Brief, BriefRequest, ExportTableInfo } from "./types";
import type {
  BBoxList,
  ContextLayerKey,
  ContextResponse,
  ContextZoneProfile,
  FeatureCollection,
  GeometryParseRequest,
  GeometryParseResponse,
  PlaceResult,
} from "./types";

export const POLL_INTERVAL_MS = 1000;

export const queryKeys = {
  health: () => ["health"] as const,
  meta: () => ["meta"] as const,
  files: (params: ListParams = {}) => ["files", "list", params] as const,
  filesAll: () => ["files", "list"] as const,
  file: (id: string) => ["files", "detail", id] as const,
  measurements: (id: string, query: MeasurementsQuery = {}) => ["files", "detail", id, "measurements", query] as const,
  features: (id: string) => ["files", "detail", id, "features"] as const,
  feature: (id: string, index: number) => ["files", "detail", id, "feature", index] as const,
  fileScope: (id: string) => ["files", "detail", id] as const,
  samples: () => ["samples"] as const,
  analyses: (datasetId: string | null | undefined, ids: readonly string[] = []) =>
    ["analyses", "list", datasetId ?? null, ids.join(",")] as const,
  analysesByIds: (ids: readonly string[]) => ["analyses", "list", "ids", ids.join(",")] as const,
  analysesGlobal: () => ["analyses", "list", "global"] as const,
  analysesAll: () => ["analyses", "list"] as const,
  analysis: (id: string) => ["analyses", "detail", id] as const,
};

/** Default retry policy: no retries for 4xx or demo errors; up to 2 for network/5xx. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && !error.retryable) return false;
  return failureCount < 2;
}

/** Factory used by <Providers>; exported for tests. */
export function createQueryClientOptions(): QueryClientConfig {
  return {
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: shouldRetry,
      },
      mutations: {
        retry: false,
      },
    },
  };
}

export function useHealth(): UseQueryResult<Health, ApiError> {
  return useQuery<Health, ApiError>({
    queryKey: queryKeys.health(),
    queryFn: ({ signal }) => getHealth(signal),
    staleTime: 60_000,
    retry: false,
  });
}

export function useMeta(): UseQueryResult<Meta, ApiError> {
  return useQuery<Meta, ApiError>({
    queryKey: queryKeys.meta(),
    queryFn: ({ signal }) => getMeta(signal),
    staleTime: Infinity,
  });
}

export function useFiles(params: ListParams = {}): UseQueryResult<FilesPage, ApiError> {
  return useQuery<FilesPage, ApiError>({
    queryKey: queryKeys.files(params),
    queryFn: ({ signal }) => listFiles(params, signal),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data?.items.some((f) => isFileActive(f.status)) ? 2000 : false),
  });
}

/**
 * Recent datasets for lists like the home page and the command palette: every dataset when the
 * server allows global listing (self-hosted, demo), otherwise only this browser's own uploads.
 */
export function useRecentFiles(limit: number): UseQueryResult<FilesPage, ApiError> {
  const meta = useMeta();
  const myIds = useHistory((s) => s.datasetIds);
  // Wait for /api/meta/; if it fails, stay scoped (a public instance refuses the unscoped list).
  const scoped = !DEMO_MODE && meta.data?.global_listing !== true;
  const ready = DEMO_MODE || meta.isSuccess || meta.isError;
  const params: ListParams = scoped ? { ids: myIds, limit, offset: 0 } : { limit, offset: 0 };
  return useQuery<FilesPage, ApiError>({
    queryKey: queryKeys.files(params),
    queryFn: ({ signal }) => listFiles(params, signal),
    enabled: ready,
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data?.items.some((f) => isFileActive(f.status)) ? 2000 : false),
  });
}

/** This browser's own datasets, newest first, even where the server lists everything. */
export function useMyFiles(limit: number): UseQueryResult<FilesPage, ApiError> {
  const myIds = useHistory((s) => s.datasetIds);
  const params: ListParams = DEMO_MODE ? { limit, offset: 0 } : { ids: myIds, limit, offset: 0 };
  return useQuery<FilesPage, ApiError>({
    queryKey: queryKeys.files(params),
    queryFn: ({ signal }) => listFiles(params, signal),
    placeholderData: keepPreviousData,
  });
}

/**
 * Whether this browser may delete/cancel the item: when it holds the owner token, or when the
 * server does not require one. Until /api/meta/ answers, only the token holder may.
 */
export function useCanModify(id: string | null | undefined): boolean {
  const meta = useMeta();
  const token = useHistory((s) => (id ? s.ownerTokens[id] : undefined));
  if (DEMO_MODE || !id) return false;
  if (token) return true;
  return meta.data?.owner_tokens_required === false;
}

/** One dataset. Polls every second while PENDING/PROCESSING. Disabled when `id` is falsy. */
export function useFile(id: string | null | undefined): UseQueryResult<FileInfo, ApiError> {
  return useQuery<FileInfo, ApiError>({
    queryKey: queryKeys.file(id ?? ""),
    queryFn: ({ signal }) => getFile(id as string, signal),
    enabled: Boolean(id),
    refetchInterval: (query) => (isFileActive(query.state.data?.status) ? POLL_INTERVAL_MS : false),
  });
}

/** Paged measurements. Only enabled once the file is COMPLETED (pass `enabled` to override). */
export function useMeasurements(
  id: string | null | undefined,
  query: MeasurementsQuery = {},
  options: { enabled?: boolean } = {},
): UseQueryResult<MeasurementsPage, ApiError> {
  return useQuery<MeasurementsPage, ApiError>({
    queryKey: queryKeys.measurements(id ?? "", query),
    queryFn: ({ signal }) => getMeasurements(id as string, query, signal),
    enabled: Boolean(id) && (options.enabled ?? true),
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
}

/** All features as a WGS84 FeatureCollection (for the map). */
export function useFeatures(
  id: string | null | undefined,
  options: { enabled?: boolean } = {},
): UseQueryResult<DatasetFeatureCollection, ApiError> {
  return useQuery<DatasetFeatureCollection, ApiError>({
    queryKey: queryKeys.features(id ?? ""),
    queryFn: ({ signal }) => getFeatures(id as string, signal),
    enabled: Boolean(id) && (options.enabled ?? true),
    staleTime: Infinity,
  });
}

/** One FeatureRecord (inspector). Disabled when `index` is null. */
export function useFeature(
  id: string | null | undefined,
  index: number | null | undefined,
): UseQueryResult<FeatureRecord, ApiError> {
  return useQuery<FeatureRecord, ApiError>({
    queryKey: queryKeys.feature(id ?? "", index ?? -1),
    queryFn: ({ signal }) => getFeature(id as string, index as number, signal),
    enabled: Boolean(id) && typeof index === "number" && index >= 0,
    staleTime: Infinity,
  });
}

export function useSamples(): UseQueryResult<SampleInfo[], ApiError> {
  return useQuery<SampleInfo[], ApiError>({
    queryKey: queryKeys.samples(),
    queryFn: ({ signal }) => listSamples(signal),
    staleTime: Infinity,
  });
}

const listRefetch = (query: { state: { data?: AnalysesPage } }) =>
  query.state.data?.items.some((a) => isAnalysisActive(a.status)) ? 2 * POLL_INTERVAL_MS : false;

/**
 * Analyses of one dataset. Disabled when `datasetId` is null (that would list the whole server).
 * Where the API enforces owner tokens it lists only the dataset owner's; pass this browser's own
 * runs on the dataset as `ids` (`useMyDatasetAnalysisIds`) to keep them listed too.
 */
export function useAnalyses(
  datasetId: string | null | undefined,
  options: { enabled?: boolean; ids?: readonly string[] } = {},
): UseQueryResult<AnalysesPage, ApiError> {
  const ids = options.ids ?? [];
  return useQuery<AnalysesPage, ApiError>({
    queryKey: queryKeys.analyses(datasetId, ids),
    queryFn: ({ signal }) => listAnalyses({ dataset_id: datasetId, ids }, signal),
    enabled: Boolean(datasetId) && (options.enabled ?? true),
    // A new run changes `ids` (a new key): keep the dataset's list on screen until it is refetched.
    placeholderData: (previous, query) => (query?.queryKey[2] === (datasetId ?? null) ? previous : undefined),
    refetchInterval: listRefetch,
  });
}

/** This browser's analyses on `datasetId`, for `useAnalyses(datasetId, { ids })`. */
export function useMyDatasetAnalysisIds(datasetId: string | null | undefined): string[] {
  const ids = useHistory((s) => s.analysisIds);
  const datasets = useHistory((s) => s.analysisDatasets);
  return useMemo(() => (datasetId ? ids.filter((id) => datasets[id] === datasetId) : []), [ids, datasets, datasetId]);
}

/**
 * Whether another visitor added the analysis to its dataset: run without the dataset's owner
 * token, and not from this browser. Such runs are labelled and never pre-selected as evidence.
 */
export function useAddedByVisitor(): (analysis: AnalysisInfo) => boolean {
  const mine = useHistory((s) => s.analysisIds);
  return useCallback((a: AnalysisInfo) => a.dataset_id !== null && a.by_dataset_owner === false && !mine.includes(a.id), [mine]);
}

/** The analyses this browser created (ids remembered in `useHistory`), e.g. dataset-less flight audits. */
export function useMyAnalyses(options: { enabled?: boolean } = {}): UseQueryResult<AnalysesPage, ApiError> {
  const ids = useHistory((s) => s.analysisIds);
  return useQuery<AnalysesPage, ApiError>({
    queryKey: queryKeys.analysesByIds(ids),
    queryFn: ({ signal }) => listAnalyses({ ids }, signal),
    enabled: options.enabled ?? true,
    refetchInterval: listRefetch,
  });
}

/**
 * Every analysis on the server. Only for the read-only demo (static data) or a self-hosted
 * instance with global listing on (`meta.global_listing`); public instances answer 403.
 */
export function useAllAnalyses(options: { enabled: boolean }): UseQueryResult<AnalysesPage, ApiError> {
  return useQuery<AnalysesPage, ApiError>({
    queryKey: queryKeys.analysesGlobal(),
    queryFn: ({ signal }) => listAnalyses({}, signal),
    enabled: options.enabled,
    refetchInterval: listRefetch,
  });
}

/**
 * Query options for one analysis (polls every second while PENDING/RUNNING). Shared by
 * `useAnalysis` and the workspace's watcher of running jobs, so both read one cache entry.
 */
export function analysisQueryOptions(id: string | null | undefined) {
  return queryOptions<AnalysisInfo, ApiError>({
    queryKey: queryKeys.analysis(id ?? ""),
    queryFn: ({ signal }) => getAnalysis(id as string, signal),
    enabled: Boolean(id),
    refetchInterval: (query) => (isAnalysisActive(query.state.data?.status) ? POLL_INTERVAL_MS : false),
  });
}

/** One analysis. Polls every second while PENDING/RUNNING. Disabled when `id` is falsy. */
export function useAnalysis(id: string | null | undefined): UseQueryResult<AnalysisInfo, ApiError> {
  return useQuery<AnalysisInfo, ApiError>(analysisQueryOptions(id));
}

export interface UploadVariables {
  file: File;
  crsOverride?: string;
  origin?: [number, number, number];
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
}

function seedFile(client: QueryClient, info: FileInfo): void {
  client.setQueryData(queryKeys.file(info.id), info);
  void client.invalidateQueries({ queryKey: queryKeys.filesAll() });
}

/** Upload a file with progress. The resulting FileInfo is put in the cache for `useFile`. */
export function useUpload(): UseMutationResult<FileInfo, ApiError, UploadVariables> {
  const client = useQueryClient();
  return useMutation<FileInfo, ApiError, UploadVariables>({
    mutationFn: ({ file, ...options }) => uploadFile(file, options),
    onSuccess: (info) => {
      useHistory.getState().rememberDataset(info.id, info.owner_token);
      seedFile(client, info);
    },
  });
}

/** Create (or, in demo mode, open) the dataset for a sample by name. */
export function useLoadSample(): UseMutationResult<FileInfo, ApiError, string> {
  const client = useQueryClient();
  return useMutation<FileInfo, ApiError, string>({
    mutationFn: (name) => loadSample(name),
    onSuccess: (info) => {
      useHistory.getState().rememberDataset(info.id, info.owner_token);
      seedFile(client, info);
    },
  });
}

/**
 * Delete a dataset (with this browser's owner token), then forget it and its analyses and
 * clear the cache. Shared by `useDeleteFile` and the delayed delete behind the Undo toast,
 * which runs after its component has unmounted.
 */
export async function deleteDataset(client: QueryClient, id: string): Promise<void> {
  await deleteFile(id, useHistory.getState().ownerTokens[id]);
  useHistory.getState().forgetDataset(id);
  client.removeQueries({ queryKey: queryKeys.fileScope(id) });
  void client.invalidateQueries({ queryKey: queryKeys.filesAll() });
  void client.invalidateQueries({ queryKey: queryKeys.analysesAll() });
}

export function useDeleteFile(): UseMutationResult<void, ApiError, string> {
  const client = useQueryClient();
  return useMutation<void, ApiError, string>({
    mutationFn: (id) => deleteDataset(client, id),
  });
}

/** Mutation key of `useCreateAnalysis` (e.g. for `useMutationState`: the jobs this tab created). */
export const CREATE_ANALYSIS_MUTATION_KEY = ["analyses", "create"] as const;

export function useCreateAnalysis(): UseMutationResult<AnalysisInfo, ApiError, AnalysisRequest<AnalysisKind>> {
  const client = useQueryClient();
  return useMutation<AnalysisInfo, ApiError, AnalysisRequest<AnalysisKind>>({
    mutationKey: CREATE_ANALYSIS_MUTATION_KEY,
    // the dataset's token (this browser has it if it created the dataset) makes the run the owner's evidence
    mutationFn: (body) => createAnalysis(body, body.dataset_id ? useHistory.getState().ownerTokens[body.dataset_id] : null),
    onSuccess: (info) => {
      useHistory.getState().rememberAnalysis(info.id, info.owner_token, info.dataset_id);
      client.setQueryData(queryKeys.analysis(info.id), info);
      void client.invalidateQueries({ queryKey: queryKeys.analysesAll() });
    },
  });
}

export function useCancelAnalysis(): UseMutationResult<void, ApiError, string> {
  const client = useQueryClient();
  return useMutation<void, ApiError, string>({
    mutationFn: (id) => cancelAnalysis(id, useHistory.getState().ownerTokens[id]),
    onSuccess: (_data, id) => {
      void client.invalidateQueries({ queryKey: queryKeys.analysis(id) });
      void client.invalidateQueries({ queryKey: queryKeys.analysesAll() });
    },
  });
}

export function useDeleteAnalysis(): UseMutationResult<void, ApiError, string> {
  const client = useQueryClient();
  return useMutation<void, ApiError, string>({
    mutationFn: (id) => deleteAnalysis(id, useHistory.getState().ownerTokens[id]),
    onSuccess: (_data, id) => {
      useHistory.getState().forgetAnalysis(id);
      client.removeQueries({ queryKey: queryKeys.analysis(id) });
      void client.invalidateQueries({ queryKey: queryKeys.analysesAll() });
    },
  });
}

/** Fetch a FlightMD report JSON through the API (SSRF-guarded). Variables: the report URL. */
export function useFetchFlightmd(): UseMutationResult<Record<string, unknown>, ApiError, string> {
  return useMutation<Record<string, unknown>, ApiError, string>({
    mutationFn: (url) => fetchFlightmd(url),
  });
}

/** One context layer of one view, as the map and the tables use it. */
export interface ContextLayerData {
  key: ContextLayerKey;
  collection: FeatureCollection | null;
  truncated: boolean;
  /** features that matched before the cut at `limit` */
  count: number;
  datapack: string | null;
  synthetic: boolean;
  isLoading: boolean;
  error: ApiError | null;
  /** the rule set the rings were drawn with (null for the other layers) */
  profile: ContextZoneProfile | null;
}

/**
 * The context layers switched on, for one view (`bbox` from `contextViewBox`). One query per
 * layer, so switching a layer on fetches only that layer; views are cached for 5 minutes (the
 * rings per rule set `ringsProfile`) and the API's ETag revalidates them after that.
 */
export function useContextLayers(
  bbox: BBoxList | null,
  layers: readonly ContextLayerKey[],
  ringsProfile: ContextZoneProfile = "dgca_2021",
): ContextLayerData[] {
  const key = bbox ? bbox.map((v) => v.toFixed(4)).join(",") : "";
  // only the rings depend on the rules: the other layers stay cached when the panel's Rules change (no point refetching water)
  const profileOf = useCallback((layer: ContextLayerKey) => (layer === "rings" ? ringsProfile : null), [ringsProfile]);
  // A stable `combine` keeps the same array while no query changes, so map layers built from it
  // are not rebuilt on every render.
  const combine = useCallback(
    (results: ContextQueryResult<ContextResponse, Error>[]): ContextLayerData[] =>
      results.map((r, i) => ({
        key: layers[i] as ContextLayerKey,
        collection: r.data?.layers[layers[i] as ContextLayerKey] ?? null,
        truncated: Boolean(r.data?.truncated[layers[i] as ContextLayerKey]),
        count: r.data?.counts[layers[i] as ContextLayerKey] ?? 0,
        datapack: r.data?.datapack ?? null,
        synthetic: r.data?.synthetic ?? false,
        isLoading: r.isLoading,
        error: (r.error as ApiError | null) ?? null,
        profile: profileOf(layers[i] as ContextLayerKey),
      })),
    [layers, profileOf],
  );
  return useQueries({
    queries: layers.map((layer) => {
      const profile = profileOf(layer);
      return {
        queryKey: ["context", key, layer, profile] as const,
        queryFn: ({ signal }: { signal: AbortSignal }) =>
          getContext({ bbox: bbox as BBoxList, layers: [layer], profile: profile ?? undefined }, signal),
        enabled: bbox !== null,
        staleTime: 5 * 60_000,
        placeholderData: keepPreviousData,
      };
    }),
    combine,
  });
}

/** Typing pause before `usePlaces` asks the API, so a word typed quickly is one request. */
export const PLACES_DEBOUNCE_MS = 200;

/**
 * Coordinates and aerodromes matching `q` (2+ characters), asked once typing pauses
 * (`PLACES_DEBOUNCE_MS`); keeps the last answer while typing.
 */
export function usePlaces(q: string, options: { enabled?: boolean } = {}): UseQueryResult<PlaceResult[], ApiError> {
  const typed = q.trim();
  const [query, setQuery] = useState(typed);
  useEffect(() => {
    if (typed === query) return undefined;
    const timer = window.setTimeout(() => setQuery(typed), PLACES_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [typed, query]);
  return useQuery<PlaceResult[], ApiError>({
    queryKey: ["places", query],
    queryFn: ({ signal }) => getPlaces(query, 8, signal),
    enabled: query.length >= 2 && (options.enabled ?? true),
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
  });
}

/** Read pasted GeoJSON, WKT or coordinates (POST, rate limited like every POST). */
export function useParseGeometry(): UseMutationResult<GeometryParseResponse, ApiError, GeometryParseRequest> {
  return useMutation<GeometryParseResponse, ApiError, GeometryParseRequest>({
    mutationFn: (body) => parseGeometry(body),
  });
}

/** The CSV tables of a completed analysis (`GET /api/analyses/{id}/export/tables/`); off in the demo. */
export function useAnalysisTables(
  id: string | null | undefined,
  options: { enabled?: boolean } = {},
): UseQueryResult<ExportTableInfo[], ApiError> {
  return useQuery<ExportTableInfo[], ApiError>({
    queryKey: [...queryKeys.analysis(id ?? ""), "export-tables"],
    queryFn: ({ signal }) => getAnalysisTables(id as string, signal),
    enabled: Boolean(id) && !DEMO_MODE && (options.enabled ?? true),
    staleTime: Infinity,
  });
}

/** Build an operation evidence brief (`POST /api/briefs/`; merged in the browser in the demo). */
export function useCreateBrief(): UseMutationResult<Brief, ApiError, BriefRequest> {
  return useMutation<Brief, ApiError, BriefRequest>({
    mutationFn: (body) => createBrief(body),
  });
}
