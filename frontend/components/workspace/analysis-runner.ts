"use client";

/**
 * Analysis job helpers for module panels and the workspace.
 *
 * - `useAnalysisRunner()` creates an analysis for the current dataset (`d`) and selected
 *   features (`f`), writes `?a=` to the URL, opens the results drawer and toasts on failure.
 * - `useAnalysisStarted()` is the workspace's `onAnalysisStarted` handler (same URL/drawer
 *   effects) for panels that call `useCreateAnalysis` themselves. It also tracks the job.
 * - `useAnalysisTracker()` polls every tracked job, whichever module is open, and reports
 *   each one once when it finishes.
 * - `useCancelJob()` cancels with toasts.
 *
 * Events that toast are not also sent to the workspace live region: the toaster is a polite
 * live region itself, so screen readers would hear them twice.
 */

import * as React from "react";
import { useQueries } from "@tanstack/react-query";
import { describeError, type ApiError } from "@/lib/api";
import { MODULE_META, moduleForKind } from "@/lib/modules";
import { analysisQueryOptions, useCancelAnalysis, useCreateAnalysis } from "@/lib/queries";
import { moduleAnalysisKey, useUi } from "@/lib/store";
import { isAnalysisActive, type AnalysisInfo, type AnalysisKind, type AnalysisParamsByKind, type AnalysisRequest } from "@/lib/types";
import { useWorkspaceParams } from "@/lib/url-state";
import { toast } from "@/components/ui/toaster";
import { announce } from "./live-region";
import { useWorkspaceBreakpoint } from "./use-media-query";

/** Human name of an analysis kind, e.g. "evtol_corridor" → "eVTOL corridor". */
export function analysisKindLabel(kind: AnalysisKind): string {
  switch (kind) {
    case "airspace":
      return "Airspace check";
    case "ground_risk":
      return "Ground risk";
    case "evtol_siting":
      return "eVTOL siting";
    case "evtol_corridor":
      return "eVTOL corridor";
    case "show":
      return "Drone show check";
    case "survey":
      return "Survey estimate";
    case "flight_audit":
      return "Flight audit";
    default:
      return MODULE_META[moduleForKind(kind)].label;
  }
}

export interface RunAnalysisOptions {
  /** override the dataset (default: URL `d`; null = no dataset, e.g. flight audit) */
  datasetId?: string | null;
  /** override the features (default: URL `f`; empty/null = whole dataset) */
  featureIndices?: number[] | null;
}

export interface AnalysisRunner {
  /** Create the analysis; resolves with it (or null when creation failed, after a toast). */
  run: <K extends AnalysisKind>(
    kind: K,
    params: AnalysisRequest<K>["params"],
    options?: RunAnalysisOptions,
  ) => Promise<AnalysisInfo | null>;
  isPending: boolean;
  /** last creation error (already toasted), for inline display next to the Run button */
  error: ApiError | null;
  reset: () => void;
}

/**
 * Workspace effects after an analysis was created: track it, remember it for its module,
 * write `?a=`, open the drawer. On tablets the inspector closes, so the map and the drawer
 * (where the progress and results appear) get the full width back; the drawer's "Edit
 * parameters" reopens it.
 */
export function useAnalysisStarted(): (analysis: AnalysisInfo) => void {
  const { update, module, datasetId } = useWorkspaceParams();
  const setDrawerOpen = useUi((s) => s.setDrawerOpen);
  const setRightOpen = useUi((s) => s.setRightOpen);
  const trackAnalysis = useUi((s) => s.trackAnalysis);
  const rememberModuleAnalysis = useUi((s) => s.rememberModuleAnalysis);
  const isTablet = useWorkspaceBreakpoint() === "tablet";
  return React.useCallback(
    (analysis: AnalysisInfo) => {
      const owner = moduleForKind(analysis.kind);
      if (isAnalysisActive(analysis.status)) trackAnalysis(analysis.id, analysis.kind);
      // Remembered now, not only once the URL shows it: a module link clicked right after Run
      // supersedes the `?a=` navigation before it commits, and the rail must still bring the
      // job back to its module.
      rememberModuleAnalysis(moduleAnalysisKey(datasetId, owner), analysis.id);
      update({ analysisId: analysis.id, time: null, ...(module !== owner ? { module: owner } : {}) });
      setDrawerOpen(true);
      if (isTablet) {
        setRightOpen(false);
        // The focused Run button was inside the inspector: continue in the drawer, not on <body>.
        window.requestAnimationFrame(() => document.getElementById("results")?.focus());
      }
      announce(`${analysisKindLabel(analysis.kind)} started.`);
    },
    [datasetId, isTablet, module, rememberModuleAnalysis, setDrawerOpen, setRightOpen, trackAnalysis, update],
  );
}

export function useAnalysisRunner(): AnalysisRunner {
  const { datasetId, featureIndices } = useWorkspaceParams();
  const create = useCreateAnalysis();
  const started = useAnalysisStarted();

  const run = React.useCallback(
    async <K extends AnalysisKind>(
      kind: K,
      params: AnalysisRequest<K>["params"],
      options: RunAnalysisOptions = {},
    ): Promise<AnalysisInfo | null> => {
      const ds = options.datasetId !== undefined ? options.datasetId : datasetId;
      const feats = options.featureIndices !== undefined ? options.featureIndices : featureIndices;
      const body: AnalysisRequest<AnalysisKind> = {
        kind,
        dataset_id: ds ?? null,
        feature_indices: feats && feats.length > 0 ? feats : null,
        params: params as unknown as Partial<AnalysisParamsByKind[AnalysisKind]> & { report_url?: string },
      };
      try {
        const info = await create.mutateAsync(body);
        started(info);
        return info;
      } catch (error) {
        const d = describeError(error);
        // Long enough to read the fix; panels that render `error` inline keep it beside the form too.
        toast.error(`${analysisKindLabel(kind)} could not start`, { description: d.fix ? `${d.message} ${d.fix}` : d.message, duration: 10_000 });
        return null;
      }
    },
    [create, datasetId, featureIndices, started],
  );

  return { run, isPending: create.isPending, error: create.error ?? null, reset: create.reset };
}

export interface AnalysisTrackerOptions {
  /** whether the analysis is the one the results drawer shows right now */
  isShown: (analysis: AnalysisInfo) => boolean;
  /** a shown analysis finished (e.g. open the drawer on its results) */
  onShownFinished?: (analysis: AnalysisInfo) => void;
  /** the toast's "View results" action for an analysis that is not shown */
  onOpen: (analysis: AnalysisInfo) => void;
}

function reportFinished(analysis: AnalysisInfo, { isShown, onShownFinished, onOpen }: AnalysisTrackerOptions): void {
  const label = analysisKindLabel(analysis.kind);
  const shown = isShown(analysis);
  // One toast per job, even if two observers see it finish.
  const id = `analysis-finished-${analysis.id}`;
  // A toast with an action stays long enough to reach it from the keyboard (Alt+T).
  const withAction = (actionLabel: string) =>
    shown ? {} : { action: { label: actionLabel, onClick: () => onOpen(analysis) }, duration: 10_000 };
  if (analysis.status === "COMPLETED") {
    toast.success(`${label} finished`, {
      id,
      description: shown ? "Results are in the drawer below the map." : "Its results are ready.",
      ...withAction("View results"),
    });
  } else if (analysis.status === "FAILED") {
    const d = describeError(analysis.error ?? "The analysis failed.");
    toast.error(`${label} failed`, {
      id,
      description: d.message,
      ...withAction("View details"),
    });
  } else if (analysis.status === "CANCELLED") {
    toast(`${label} cancelled`, { id });
  }
  if (shown) onShownFinished?.(analysis);
}

/**
 * Poll every tracked analysis (`useUi().trackedAnalyses`) until it finishes, whichever module
 * is open, and report each one exactly once with a toast. Jobs that are not on screen get an
 * action that opens their results. Failed lookups (e.g. a deleted job) are dropped silently.
 */
export function useAnalysisTracker(options: AnalysisTrackerOptions): void {
  const tracked = useUi((s) => s.trackedAnalyses);
  const untrack = useUi((s) => s.untrackAnalysis);
  const ids = React.useMemo(() => Object.keys(tracked), [tracked]);
  const results = useQueries({ queries: ids.map((id) => analysisQueryOptions(id)) });

  const latest = React.useRef({ options, results });
  latest.current = { options, results };

  // Re-check only when a tracked job's status changes, not on every poll.
  const signature = results.map((r, i) => `${ids[i]}:${r.data?.status ?? (r.isError ? "error" : "loading")}`).join("|");
  React.useEffect(() => {
    latest.current.results.forEach((r, i) => {
      const id = ids[i];
      if (!id) return;
      const analysis = r.data;
      if (!analysis) {
        if (r.isError) untrack(id);
        return;
      }
      if (isAnalysisActive(analysis.status)) return;
      untrack(id);
      reportFinished(analysis, latest.current.options);
    });
  }, [signature, ids, untrack]);
}

/** Cancel a running analysis with feedback. */
export function useCancelJob(): { cancel: (analysis: AnalysisInfo) => void; isPending: boolean } {
  const mutation = useCancelAnalysis();
  const cancel = React.useCallback(
    (analysis: AnalysisInfo) => {
      mutation.mutate(analysis.id, {
        onSuccess: () => announce(`Cancelling ${analysisKindLabel(analysis.kind).toLowerCase()}.`),
        onError: (error) => {
          const d = describeError(error);
          toast.error("Could not cancel", { description: d.message });
        },
      });
    },
    [mutation],
  );
  return { cancel, isPending: mutation.isPending };
}
