"use client";

/**
 * The URL is the source of truth for workspace selection (SPEC §6):
 *
 *   d = dataset id      f = feature index (or comma-separated indices)
 *   m = module id       a = analysis id      t = time in seconds (playback / profile cursor)
 *
 * Setters call `router.replace` with `{ scroll: false }`, so they never add history entries
 * or scroll the page. They merge with the *current* `window.location.search`, so several
 * setters called in the same tick do not overwrite each other.
 *
 * Next.js requires components that call `useSearchParams` to sit inside a <Suspense>
 * boundary for static export: wrap the workspace page body in <Suspense>.
 */

import { useCallback, useEffect, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { isModuleId, type ModuleId } from "./modules";

/**
 * Query string of a `router.replace` that has been requested but not yet committed.
 * The App Router updates `window.location` asynchronously, so setters called back to back
 * merge with this instead of the stale location.
 */
let pendingQuery: string | null = null;

export const PARAM = {
  dataset: "d",
  feature: "f",
  module: "m",
  analysis: "a",
  time: "t",
} as const;

export interface WorkspaceParams {
  datasetId: string | null;
  /** first selected feature index, or null */
  featureIndex: number | null;
  /** all selected feature indices (from `f=1,4,7`), possibly empty */
  featureIndices: number[];
  module: ModuleId | null;
  analysisId: string | null;
  /** seconds, or null */
  time: number | null;
}

export interface WorkspaceParamsPatch {
  datasetId?: string | null;
  featureIndex?: number | null;
  featureIndices?: number[] | null;
  module?: ModuleId | null;
  analysisId?: string | null;
  time?: number | null;
}

export interface WorkspaceParamsApi extends WorkspaceParams {
  /** Switch dataset; clears feature, analysis and time. */
  setDataset: (id: string | null) => void;
  /** Select one feature (null clears the selection). */
  setFeature: (index: number | null) => void;
  /** Select several features (empty clears). */
  setFeatures: (indices: number[]) => void;
  /** Open a module; clears the analysis and time unless `keepAnalysis` is true. */
  setModule: (module: ModuleId | null, options?: { keepAnalysis?: boolean }) => void;
  setAnalysis: (id: string | null) => void;
  /** Write the time (rounded to 0.1 s). Avoid calling every animation frame: write on pause/seek. */
  setTime: (time: number | null) => void;
  /** Apply several changes in one navigation. `undefined` keeps a value, `null` removes it. */
  update: (patch: WorkspaceParamsPatch) => void;
  /** Build a href with the patch applied (for links that open a specific state). */
  hrefWith: (patch: WorkspaceParamsPatch, pathname?: string) => string;
}

function parseIndices(raw: string | null): number[] {
  if (!raw) return [];
  const out: number[] = [];
  for (const part of raw.split(",")) {
    const n = Number.parseInt(part.trim(), 10);
    if (Number.isInteger(n) && n >= 0 && !out.includes(n)) out.push(n);
  }
  return out;
}

function parseTime(raw: string | null): number | null {
  if (raw === null || raw === "") return null;
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function parseString(raw: string | null): string | null {
  const v = raw?.trim();
  return v ? v : null;
}

/** Parse workspace params from any URLSearchParams-like source. */
export function parseWorkspaceParams(params: Pick<URLSearchParams, "get">): WorkspaceParams {
  const featureIndices = parseIndices(params.get(PARAM.feature));
  const moduleRaw = params.get(PARAM.module);
  return {
    datasetId: parseString(params.get(PARAM.dataset)),
    featureIndex: featureIndices[0] ?? null,
    featureIndices,
    module: isModuleId(moduleRaw) ? moduleRaw : null,
    analysisId: parseString(params.get(PARAM.analysis)),
    time: parseTime(params.get(PARAM.time)),
  };
}

/** Apply a patch to a URLSearchParams (mutates and returns it). Unknown params are kept. */
export function applyWorkspacePatch(params: URLSearchParams, patch: WorkspaceParamsPatch): URLSearchParams {
  const setOrDelete = (key: string, value: string | null) => {
    if (value === null || value === "") params.delete(key);
    else params.set(key, value);
  };
  if (patch.datasetId !== undefined) setOrDelete(PARAM.dataset, patch.datasetId);
  if (patch.featureIndices !== undefined) {
    setOrDelete(PARAM.feature, patch.featureIndices && patch.featureIndices.length ? patch.featureIndices.join(",") : null);
  } else if (patch.featureIndex !== undefined) {
    setOrDelete(PARAM.feature, patch.featureIndex === null ? null : String(patch.featureIndex));
  }
  if (patch.module !== undefined) setOrDelete(PARAM.module, patch.module);
  if (patch.analysisId !== undefined) setOrDelete(PARAM.analysis, patch.analysisId);
  if (patch.time !== undefined) {
    setOrDelete(PARAM.time, patch.time === null ? null : String(Math.round(patch.time * 10) / 10));
  }
  return params;
}

/** Build `/w/view?d=...` style hrefs outside React (e.g. in the command palette). */
export function workspaceHref(patch: WorkspaceParamsPatch, pathname = "/w/view"): string {
  const qs = applyWorkspacePatch(new URLSearchParams(), patch).toString();
  return qs ? `${pathname}?${qs}` : pathname;
}

/** Read and update the workspace state held in the URL. */
export function useWorkspaceParams(): WorkspaceParamsApi {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const parsed = useMemo(() => parseWorkspaceParams(searchParams), [searchParams]);

  // Once the router has committed a navigation, the hook's searchParams are current again.
  useEffect(() => {
    pendingQuery = null;
  }, [searchParams]);

  const currentParams = useCallback((): URLSearchParams => {
    if (pendingQuery !== null) return new URLSearchParams(pendingQuery);
    if (typeof window !== "undefined") return new URLSearchParams(window.location.search);
    return new URLSearchParams(searchParams.toString());
  }, [searchParams]);

  const update = useCallback(
    (patch: WorkspaceParamsPatch) => {
      const before = currentParams();
      const beforeString = before.toString();
      const next = applyWorkspacePatch(before, patch);
      const qs = next.toString();
      if (qs === beforeString) return;
      pendingQuery = qs;
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [currentParams, pathname, router],
  );

  const hrefWith = useCallback(
    (patch: WorkspaceParamsPatch, targetPath?: string) => {
      const qs = applyWorkspacePatch(currentParams(), patch).toString();
      const path = targetPath ?? pathname;
      return qs ? `${path}?${qs}` : path;
    },
    [currentParams, pathname],
  );

  const setDataset = useCallback(
    (id: string | null) => update({ datasetId: id, featureIndices: null, analysisId: null, time: null }),
    [update],
  );
  const setFeature = useCallback((index: number | null) => update({ featureIndex: index }), [update]);
  const setFeatures = useCallback((indices: number[]) => update({ featureIndices: indices }), [update]);
  const setModule = useCallback(
    (module: ModuleId | null, options?: { keepAnalysis?: boolean }) =>
      update(options?.keepAnalysis ? { module } : { module, analysisId: null, time: null }),
    [update],
  );
  const setAnalysis = useCallback((id: string | null) => update({ analysisId: id, time: null }), [update]);
  const setTime = useCallback((time: number | null) => update({ time }), [update]);

  return useMemo(
    () => ({
      ...parsed,
      setDataset,
      setFeature,
      setFeatures,
      setModule,
      setAnalysis,
      setTime,
      update,
      hrefWith,
    }),
    [parsed, setDataset, setFeature, setFeatures, setModule, setAnalysis, setTime, update, hrefWith],
  );
}
