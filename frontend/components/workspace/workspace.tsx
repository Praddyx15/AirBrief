"use client";

/**
 * The workspace (`/w/view?d=&f=&m=&a=&t=`). The URL is the source of truth for the dataset,
 * selection, module, analysis and time; `useUi` holds transient panel state.
 *
 * Layout:
 * - ≥ 1024 px: rail · layers & features (300) · map · inspector (340) · resizable drawer
 * - 768–1023 px: the same panels, but at most one side panel at a time (opening the
 *   inspector folds the layers panel away and restores it on close), so the map keeps room;
 *   the inspector starts closed and opens from the header, the rail or "Edit parameters"
 * - < 768 px, or a short touch screen (landscape phone): read-only viewer: full map + bottom
 *   sheet (cards instead of tables)
 *
 * Works without a dataset when a module or analysis is chosen (flight audit flow).
 */

import * as React from "react";
import Link from "next/link";
import { FolderOpen, Home, MapPin, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Pentagon, Route, X } from "lucide-react";
import { isBatchAnalysis } from "@/lib/batch";
import { MODULE_META, RESULT_FOCUS_LAYERS, moduleForKind, type ModuleId } from "@/lib/modules";
import { DEMO_MODE, isApiError } from "@/lib/api";
import { plural } from "@/lib/format";
import {
  useAllAnalyses,
  useAnalyses,
  useAnalysis,
  useCanModify,
  useFeatures,
  useFile,
  useMeta,
  useMyAnalyses,
  useMyDatasetAnalysisIds,
} from "@/lib/queries";
import { moduleAnalysisKey, useUi } from "@/lib/store";
import { isAnalysisActive, isFileActive, type AnalysisInfo, type BBoxList, type FileStatus } from "@/lib/types";
import { PARAM, useWorkspaceParams } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { IconButton } from "@/components/ui/icon-button";
import { CommandPalette } from "@/components/layout/command-palette";
import { DemoBanner } from "@/components/layout/demo-banner";
import { ThemeSystemSync } from "@/components/layout/theme-toggle";
import { boundsCenter, collectionBounds, geometryBounds } from "@/components/map/layers/common";
import { DATASET_LAYER_ID } from "@/components/map/layers/dataset-layer";
import type { MapViewHandle } from "@/components/map/map-loader";
import { MODULES, analysisBelongsTo, moduleDefinitionForKind, moduleLayersForKind } from "@/components/modules/registry";
import { useAnalysisStarted, useAnalysisTracker, useCancelJob } from "./analysis-runner";
import { DatasetHeader } from "./dataset-header";
import { Inspector } from "./inspector";
import { FileJobProgress } from "./job-progress";
import { LayersPanel, type LayerToggle } from "./layers-panel";
import { LiveRegion, announce } from "./live-region";
import { MobileViewer, type MobileTab } from "./mobile-viewer";
import { ModuleRail } from "./module-rail";
import { ResultsDrawer, type DrawerTab, type LayerTableSource } from "./results-drawer";
import { useWorkspaceBreakpoint } from "./use-media-query";
import { WorkspaceMap, sourceLayerToggleId } from "./workspace-map";
import { CONTEXT_LAYER_LABEL, contextLayerId, isContextLayerId } from "@/components/map/layers/context-layer";
import { usePlanActive } from "@/lib/plan-store";
import { startPlanTool } from "./plan-actions";
import { useContextData } from "./plan-map";

function humanize(key: string): string {
  const s = key.replace(/[_-]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : key;
}

function resultTableId(key: string): string {
  return `result:${key}`;
}

/** Extent of the result layers that show where a completed analysis' answer is. */
function analysisFocusBounds(analysis: AnalysisInfo): BBoxList | null {
  const layers = analysis.result?.layers;
  if (!layers) return null;
  const focus = RESULT_FOCUS_LAYERS[analysis.kind].map((key) => layers[key]).filter((fc) => fc !== undefined);
  return collectionBounds(focus.length > 0 ? focus : Object.values(layers));
}

/** True when `inner` lies inside `outer` (both [min_lon, min_lat, max_lon, max_lat]). */
function boundsContain(outer: BBoxList, inner: BBoxList): boolean {
  return inner[0] >= outer[0] && inner[1] >= outer[1] && inner[2] <= outer[2] && inner[3] <= outer[3];
}

export function Workspace() {
  const params = useWorkspaceParams();
  const { datasetId, featureIndices, analysisId, setFeature, setFeatures, update, hrefWith } = params;
  const breakpoint = useWorkspaceBreakpoint();
  const isMobile = breakpoint === "mobile";
  const isTablet = breakpoint === "tablet";

  const fileQ = useFile(datasetId);
  const file = fileQ.data ?? null;
  const featuresQ = useFeatures(datasetId, { enabled: file?.status === "COMPLETED" });
  const features = featuresQ.data ?? null;
  const analysisQ = useAnalysis(analysisId);
  const analysis = analysisId ? (analysisQ.data ?? null) : null;
  const metaQ = useMeta();
  // History: the dataset's analyses (on public instances only the owner's, plus this browser's
  // own runs on it); without a dataset, this browser's own (or, in the static demo, the pre-run)
  // analyses. The server-wide list is never fetched here.
  const myDatasetRuns = useMyDatasetAnalysisIds(datasetId);
  const datasetHistoryQ = useAnalyses(datasetId, { ids: myDatasetRuns });
  const myHistoryQ = useMyAnalyses({ enabled: !datasetId && !DEMO_MODE });
  const demoHistoryQ = useAllAnalyses({ enabled: !datasetId && DEMO_MODE });
  const historyQ = datasetId ? datasetHistoryQ : DEMO_MODE ? demoHistoryQ : myHistoryQ;

  const activeModuleId: ModuleId = params.module ?? (analysis ? moduleForKind(analysis.kind) : "measure");
  const activeModule = MODULES[activeModuleId];
  const moduleAnalysis = analysisBelongsTo(activeModule, analysis) ? analysis : null;
  const ownerModule = analysis ? moduleDefinitionForKind(analysis.kind) : null;
  const analysisCompleted = analysis?.status === "COMPLETED" && analysis.result !== null;
  const datasetUnavailable = Boolean(datasetId) && (fileQ.isError || file?.status === "FAILED");
  /** Features whose invalid geometry was repaired before measuring (marked in the tables). */
  const repairedFeatures = React.useMemo(() => {
    const out = new Set<number>();
    for (const issue of file?.issues ?? []) if (issue.code === "geometry_repaired" && issue.feature_index !== null) out.add(issue.feature_index);
    return out;
  }, [file?.issues]);

  const leftOpen = useUi((s) => s.leftOpen);
  const rightOpen = useUi((s) => s.rightOpen);
  const drawerOpen = useUi((s) => s.drawerOpen);
  const drawerHeight = useUi((s) => s.drawerHeight);
  const hiddenLayers = useUi((s) => s.hiddenLayers);
  const moduleAnalyses = useUi((s) => s.moduleAnalyses);
  const toggleLeft = useUi((s) => s.toggleLeft);
  const setLeftOpen = useUi((s) => s.setLeftOpen);
  const setRightOpen = useUi((s) => s.setRightOpen);
  const setLayerHidden = useUi((s) => s.setLayerHidden);
  const rememberModuleAnalysis = useUi((s) => s.rememberModuleAnalysis);
  const forgetModuleAnalysis = useUi((s) => s.forgetModuleAnalysis);
  const trackAnalysis = useUi((s) => s.trackAnalysis);
  const setDrawerOpen = useUi((s) => s.setDrawerOpen);
  const setDrawerHeight = useUi((s) => s.setDrawerHeight);
  const toggleLayer = useUi((s) => s.toggleLayer);
  const setHoveredFeature = useUi((s) => s.setHoveredFeature);
  const resetWorkspace = useUi((s) => s.resetWorkspace);
  const setPlaybackTime = useUi((s) => s.setPlaybackTime);

  const mapRef = React.useRef<MapViewHandle>(null);
  const inspectorToggleRef = React.useRef<HTMLButtonElement>(null);
  const inspectorTitleRef = React.useRef<HTMLHeadingElement>(null);
  const [drawerTab, setDrawerTab] = React.useState<DrawerTab>("results");
  const [layerTableId, setLayerTableId] = React.useState<string | null>(null);
  const [mobileTab, setMobileTab] = React.useState<MobileTab>("features");
  const [mobileExpanded, setMobileExpanded] = React.useState(false);

  React.useEffect(() => {
    resetWorkspace();
  }, [datasetId, resetWorkspace]);

  React.useEffect(() => {
    if (isTablet) setRightOpen(false);
    else if (breakpoint === "desktop") setRightOpen(true);
  }, [isTablet, breakpoint, setRightOpen]);

  // Tablets show one side panel at a time: opening the inspector folds the layers panel away
  // (and brings it back when the inspector closes); opening the layers panel closes the
  // inspector. Otherwise the two panels would leave the map a sliver.
  const leftFoldedForInspector = React.useRef(false);
  React.useEffect(() => {
    // The live value too: on entering the tablet layout the effect above has just closed the
    // inspector, and folding the layers panel for this render's stale `rightOpen` would flicker.
    if (isTablet && rightOpen && useUi.getState().rightOpen) {
      if (useUi.getState().leftOpen) {
        leftFoldedForInspector.current = true;
        setLeftOpen(false);
      }
    } else if (leftFoldedForInspector.current) {
      leftFoldedForInspector.current = false;
      setLeftOpen(true);
    }
  }, [isTablet, rightOpen, setLeftOpen]);
  React.useEffect(() => {
    if (isTablet && leftOpen && useUi.getState().rightOpen) setRightOpen(false);
  }, [isTablet, leftOpen, setRightOpen]);

  React.useEffect(() => {
    if (!analysisId) return;
    setDrawerOpen(true);
    setDrawerTab("results");
    // Phones: the sheet shows the results (e.g. the airspace check started from a pin).
    if (isMobile) {
      setMobileTab("results");
      setMobileExpanded(true);
    }
  }, [analysisId, setDrawerOpen, isMobile]);

  // URL `t` → playback time (written back by modules on pause/seek).
  React.useEffect(() => {
    if (params.time !== null && !useUi.getState().playback.playing) setPlaybackTime(params.time);
  }, [params.time, setPlaybackTime]);

  // Announce when a dataset finishes processing.
  const prevFileStatus = React.useRef<{ id: string; status: FileStatus } | null>(null);
  React.useEffect(() => {
    if (!file) return;
    const before = prevFileStatus.current;
    prevFileStatus.current = { id: file.id, status: file.status };
    if (!before || before.id !== file.id || !isFileActive(before.status) || isFileActive(file.status)) return;
    if (file.status === "COMPLETED") announce(`${file.filename} is ready: ${plural(file.feature_count, "feature")} measured.`);
    else if (file.status === "FAILED") announce(`${file.filename} could not be processed: ${file.error?.message ?? "unknown error"}.`);
  }, [file]);

  // Remember the analysis shown for its module, so the rail brings it back later; keep
  // watching it while it runs (also when it was opened from a link).
  React.useEffect(() => {
    if (!analysis) return;
    rememberModuleAnalysis(moduleAnalysisKey(datasetId, moduleForKind(analysis.kind)), analysis.id);
    if (isAnalysisActive(analysis.status)) trackAnalysis(analysis.id, analysis.kind);
  }, [analysis, datasetId, rememberModuleAnalysis, trackAnalysis]);

  // A remembered analysis that no longer exists must not be restored again.
  const analysisMissing = analysisQ.isError && isApiError(analysisQ.error) && analysisQ.error.status === 404;
  React.useEffect(() => {
    if (analysisId && analysisMissing) forgetModuleAnalysis(analysisId);
  }, [analysisId, analysisMissing, forgetModuleAnalysis]);

  const onAnalysisStarted = useAnalysisStarted();
  const { cancel, isPending: cancelling } = useCancelJob();
  // Opened from a shared link without the owner token: the API would refuse the cancel.
  const canCancel = useCanModify(moduleAnalysis?.id);

  const fitToFeature = React.useCallback(
    (index: number) => {
      const f = features?.features.find((x) => x.properties._index === index);
      const b = f ? geometryBounds(f.geometry) : null;
      if (b) mapRef.current?.fitBounds(b, { maxZoom: 17 });
    },
    [features],
  );

  /**
   * From the map (click / Enter): no camera move, and the inspector does not pop open on
   * tablets, where it would take the room of the feature that was just tapped (the header's
   * "Show inspector" opens the details).
   */
  const selectFromMap = React.useCallback(
    (index: number, { additive }: { additive: boolean }) => {
      if (additive) {
        setFeatures(featureIndices.includes(index) ? featureIndices.filter((i) => i !== index) : [...featureIndices, index]);
      } else {
        setFeature(index);
      }
    },
    [featureIndices, setFeature, setFeatures],
  );

  /** Show hidden map layers that `indices` are drawn on, so a selection never zooms to an empty map. */
  const revealFeatures = React.useCallback(
    (indices: readonly number[]) => {
      const hidden = useUi.getState().hiddenLayers;
      if (hidden.length === 0 || !features) return;
      const wanted = new Set(indices);
      const reveal = new Set<string>();
      if (hidden.includes(DATASET_LAYER_ID)) reveal.add(DATASET_LAYER_ID);
      for (const f of features.features) {
        const id = sourceLayerToggleId(f.properties._layer);
        if (wanted.has(f.properties._index) && hidden.includes(id)) reveal.add(id);
      }
      if (reveal.size === 0) return;
      for (const id of reveal) setLayerHidden(id, false);
      announce("Dataset features shown on the map.");
    },
    [features, setLayerHidden],
  );

  /** From a table / list: select and bring it into view. */
  const selectFromTable = React.useCallback(
    (index: number) => {
      setFeature(index);
      revealFeatures([index]);
      fitToFeature(index);
      if (isTablet) {
        // On tablets the inspector takes the layers panel's place: when the pick came from
        // that panel's list, focus follows to the inspector instead of falling to <body>.
        const fromLayersPanel = document.getElementById("workspace-left")?.contains(document.activeElement) ?? false;
        setRightOpen(true);
        if (fromLayersPanel) window.requestAnimationFrame(() => inspectorTitleRef.current?.focus());
      }
    },
    [fitToFeature, isTablet, revealFeatures, setFeature, setRightOpen],
  );

  /** "Select all" / "Select matches": select and bring the whole selection into view. */
  const selectMany = React.useCallback(
    (indices: number[]) => {
      setFeatures(indices);
      if (indices.length === 0 || !features) return;
      revealFeatures(indices);
      const wanted = new Set(indices);
      const union = collectionBounds([
        { type: "FeatureCollection", features: features.features.filter((f) => wanted.has(f.properties._index)) },
      ]);
      if (union) mapRef.current?.fitBounds(union, { maxZoom: 17 });
    },
    [features, revealFeatures, setFeatures],
  );

  const clearSelection = React.useCallback(() => setFeatures([]), [setFeatures]);

  const showFeature = React.useCallback(
    (index: number) => setFeatures([index, ...featureIndices.filter((i) => i !== index)]),
    [featureIndices, setFeatures],
  );

  // The result's layers plus the module's derived ones (H3 cells as polygons…); a result layer
  // wins a name clash. Used for the extent, the Layers panel and "Layers as table".
  const analysisLayers = React.useMemo(() => {
    const layers = analysisCompleted ? (analysis?.result?.layers ?? null) : null;
    if (!layers || !analysis || !ownerModule?.derivedLayers) return layers;
    const merged = { ...layers };
    for (const [key, fc] of Object.entries(ownerModule.derivedLayers(analysis))) if (!(key in merged)) merged[key] = fc;
    return merged;
  }, [analysisCompleted, analysis, ownerModule]);
  const bbox = React.useMemo<BBoxList | null>(() => {
    if (file?.bbox) return file.bbox;
    if (analysisLayers) return collectionBounds(Object.values(analysisLayers));
    return null;
  }, [file?.bbox, analysisLayers]);
  const baseFitKey = datasetId ? (file?.bbox ? `d:${datasetId}` : null) : analysisCompleted && analysis ? `a:${analysis.id}` : null;

  // Bring a result into view once, when it arrives or is opened from a link or History, unless
  // it is already on screen (a corridor between two far sites, a show next to the dataset…).
  // Later module switches never move the camera again.
  const [resultFocus, setResultFocus] = React.useState<{ datasetId: string | null; key: string; bbox: BBoxList } | null>(null);
  const focusedAnalyses = React.useRef(new Set<string>());
  React.useEffect(() => {
    if (!analysisCompleted || !analysis || focusedAnalyses.current.has(analysis.id)) return;
    const focus = analysisFocusBounds(analysis);
    if (!focus) return;
    focusedAnalyses.current.add(analysis.id);
    const view = mapRef.current?.getMap()?.getBounds();
    const visible = view ? boundsContain([view.getWest(), view.getSouth(), view.getEast(), view.getNorth()], focus) : false;
    if (!visible) setResultFocus({ datasetId, key: `a:${analysis.id}`, bbox: focus });
  }, [analysis, analysisCompleted, datasetId]);
  React.useEffect(() => {
    setResultFocus((f) => (f && f.datasetId !== datasetId ? null : f));
  }, [datasetId]);
  const activeFocus = resultFocus && resultFocus.datasetId === datasetId ? resultFocus : null;
  const fitBbox = activeFocus?.bbox ?? bbox;
  const fitKey = activeFocus?.key ?? baseFitKey;

  const initialView = React.useMemo(() => {
    const pack = metaQ.data?.datapacks[0];
    return pack ? { center: boundsCenter(pack.bbox), zoom: 9 } : undefined;
  }, [metaQ.data]);

  const moduleLayerInfos = React.useMemo(
    () => (ownerModule && analysis && analysisCompleted ? moduleLayersForKind(ownerModule, analysis.kind, isBatchAnalysis(analysis)) : []),
    [ownerModule, analysis, analysisCompleted],
  );

  const layerToggles = React.useMemo<LayerToggle[]>(() => {
    const out: LayerToggle[] = [];
    if (datasetId && !datasetUnavailable) {
      const datasetHidden = hiddenLayers.includes(DATASET_LAYER_ID);
      out.push({
        id: DATASET_LAYER_ID,
        label: "Dataset features",
        hidden: datasetHidden,
        detail: features ? plural(features.features.length, "feature") : undefined,
        hasTable: true,
      });
      // Several source layers (KML folders, GeoPackage tables…): one switch each, under the dataset.
      if (file && file.layers.length > 1) {
        const counts = new Map<string, number>();
        for (const f of features?.features ?? []) counts.set(f.properties._layer, (counts.get(f.properties._layer) ?? 0) + 1);
        for (const layer of file.layers) {
          const id = sourceLayerToggleId(layer.name);
          out.push({
            id,
            label: layer.name,
            hidden: datasetHidden || hiddenLayers.includes(id),
            detail: plural(features ? (counts.get(layer.name) ?? 0) : layer.feature_count, "feature"),
            hasTable: false,
            parentId: DATASET_LAYER_ID,
            disabled: datasetHidden,
          });
        }
      }
    }
    for (const info of moduleLayerInfos) {
      const fc = info.resultLayer ? analysisLayers?.[info.resultLayer] : undefined;
      out.push({
        id: info.id,
        label: info.label,
        hidden: hiddenLayers.includes(info.id),
        detail: fc ? plural(fc.features.length, "feature") : undefined,
        hasTable: Boolean(fc),
      });
    }
    return out;
  }, [datasetId, datasetUnavailable, file, hiddenLayers, features, moduleLayerInfos, analysisLayers]);

  // Context layers switched on (plan mode), listed after the dataset and the results.
  const contextData = useContextData().data;
  const planActive = usePlanActive();

  const layerTables = React.useMemo<LayerTableSource[]>(() => {
    const out: LayerTableSource[] = [];
    if (features) out.push({ id: DATASET_LAYER_ID, label: "Dataset features", kind: "dataset", collection: features });
    if (analysisLayers) {
      for (const [key, fc] of Object.entries(analysisLayers)) {
        const info = moduleLayerInfos.find((l) => l.resultLayer === key);
        out.push({ id: resultTableId(key), label: info?.label ?? humanize(key), kind: "result", collection: fc });
      }
    }
    for (const d of contextData) {
      if (d.collection) out.push({ id: contextLayerId(d.key), label: `${CONTEXT_LAYER_LABEL[d.key]} (context)`, kind: "result", collection: d.collection });
    }
    return out;
  }, [features, analysisLayers, moduleLayerInfos, contextData]);

  const viewTable = React.useCallback(
    (layerId: string) => {
      let tableId: string | null = null;
      if (layerId === DATASET_LAYER_ID || isContextLayerId(layerId)) tableId = layerId;
      else {
        const info = moduleLayerInfos.find((l) => l.id === layerId);
        if (info?.resultLayer) tableId = resultTableId(info.resultLayer);
      }
      if (isMobile) {
        setMobileTab(tableId === DATASET_LAYER_ID ? "features" : "results");
        setMobileExpanded(true);
        return;
      }
      if (tableId) {
        setLayerTableId(tableId);
        setDrawerTab("layers");
      } else {
        setDrawerTab("results");
      }
      setDrawerOpen(true);
      window.requestAnimationFrame(() => document.getElementById("results")?.focus());
    },
    [isMobile, moduleLayerInfos, setDrawerOpen],
  );

  const history = React.useMemo<AnalysisInfo[]>(() => {
    const items = historyQ.data?.items ?? [];
    return datasetId ? items : items.filter((a) => a.dataset_id === null);
  }, [historyQ.data, datasetId]);

  /** Show an analysis (History row, or a finished job's toast): its module, and its dataset. */
  const openAnalysis = React.useCallback(
    (a: AnalysisInfo) => {
      // Compared with the URL when it runs: a finished job's toast can be clicked after the
      // user moved on to another dataset, or left the workspace.
      const currentDataset = new URLSearchParams(window.location.search).get(PARAM.dataset);
      const otherDataset = a.dataset_id !== null && a.dataset_id !== currentDataset;
      update({
        ...(otherDataset ? { datasetId: a.dataset_id, featureIndices: null } : {}),
        module: moduleForKind(a.kind),
        analysisId: a.id,
        time: null,
      });
      setDrawerTab("results");
      if (!isMobile) setDrawerOpen(true);
    },
    [isMobile, setDrawerOpen, update],
  );

  // Report every job started here once when it finishes, whichever module is open by then.
  useAnalysisTracker({
    isShown: (a) => a.id === analysisId && analysisBelongsTo(activeModule, a),
    onShownFinished: (a) => {
      if (a.status !== "COMPLETED") return;
      setDrawerOpen(true);
      setDrawerTab("results");
    },
    onOpen: openAnalysis,
  });

  /**
   * A module link keeps the current analysis when it belongs to that module; otherwise it
   * reopens the module's last analysis for this dataset (finished or still running), so
   * switching modules never loses a result.
   */
  const hrefForModule = React.useCallback(
    (m: ModuleId) => {
      const keep = analysis !== null && MODULE_META[m].kinds.includes(analysis.kind);
      if (keep) return hrefWith({ module: m });
      const remembered = moduleAnalyses[moduleAnalysisKey(datasetId, m)] ?? null;
      return hrefWith({ module: m, analysisId: remembered, time: null });
    },
    [analysis, datasetId, hrefWith, moduleAnalyses],
  );

  const onRailNavigate = React.useCallback(
    (m: ModuleId) => {
      if (isTablet && m !== "measure") setRightOpen(true);
      setDrawerTab("results");
    },
    [isTablet, setRightOpen],
  );

  /** Open the inspector and move focus to its heading (header toggle, "Edit parameters"). */
  const openInspector = React.useCallback(() => {
    setRightOpen(true);
    window.requestAnimationFrame(() => inspectorTitleRef.current?.focus());
  }, [setRightOpen]);

  /** Close the inspector from inside it; focus returns to the header toggle, not <body>. */
  const closeInspector = React.useCallback(() => {
    setRightOpen(false);
    window.requestAnimationFrame(() => inspectorToggleRef.current?.focus());
  }, [setRightOpen]);

  const inspector = (
    <Inspector
      datasetId={datasetId}
      file={file}
      features={features}
      module={activeModule}
      analysis={moduleAnalysis}
      selectedFeatures={featureIndices}
      onAnalysisStarted={onAnalysisStarted}
      onShowFeature={showFeature}
      readOnly={isMobile}
      datasetUnavailable={datasetUnavailable}
    />
  );

  const ActiveResults = activeModule.Results;
  const mobileResults =
    activeModule.kinds.length > 0 && !moduleAnalysis ? (
      <EmptyState compact title="No results" description={`Open a ${activeModule.label.toLowerCase()} analysis link, or run one on a larger screen.`} />
    ) : ActiveResults ? (
      <ActiveResults analysis={moduleAnalysis} datasetId={datasetId} file={file} />
    ) : null;

  let overlay: React.ReactNode = null;
  if (datasetId && fileQ.isError) {
    // A missing dataset is final: say so in words (no id) and offer no Retry that cannot work.
    const notFound = isApiError(fileQ.error) && fileQ.error.status === 404;
    overlay = (
      <ErrorState
        error={fileQ.error}
        {...(notFound
          ? {
              title: "Dataset not found",
              message: "This dataset no longer exists. It may have been deleted, or it expired.",
              fix: "Go back to your datasets to open another one or upload the file again.",
            }
          : { onRetry: () => void fileQ.refetch(), retrying: fileQ.isFetching })}
        actions={
          <Button asChild size="sm" variant={notFound ? "secondary" : "ghost"}>
            <Link href="/w">Back to datasets</Link>
          </Button>
        }
      />
    );
  } else if (file?.status === "FAILED") {
    overlay = (
      <ErrorState
        error={file.error ?? "The file could not be processed."}
        title={`${file.filename} could not be processed`}
        actions={
          <Button asChild size="sm" variant="secondary">
            <Link href="/w">Upload another file</Link>
          </Button>
        }
      />
    );
  } else if (file && isFileActive(file.status)) {
    overlay = <FileJobProgress file={file} />;
  } else if (!datasetId && !params.module && !analysisId && !planActive) {
    // Hidden while something is being planned, so the card never covers the map being drawn on.
    overlay = (
      <div className="rounded border border-border bg-surface">
        <EmptyState
          icon={<FolderOpen />}
          title="Draw, pin or open a dataset"
          description="Drop a pin, draw a line or an area, type or paste coordinates (New operation, top left), or upload a KML, KMZ, shapefile, GeoJSON or CSV. A flight audit works without a dataset."
          action={
            <>
              <Button size="sm" onClick={() => startPlanTool("pin")}>
                <MapPin aria-hidden />
                Drop a pin
              </Button>
              {isMobile ? null : (
                <Button size="sm" variant="secondary" onClick={() => startPlanTool("area")}>
                  <Pentagon aria-hidden />
                  Draw an area
                </Button>
              )}
              <Button asChild size="sm" variant="secondary">
                <Link href="/w">Open or upload a dataset</Link>
              </Button>
              <Button asChild size="sm" variant="secondary">
                <Link href={hrefWith({ module: "flight-audit" })} replace scroll={false}>
                  <Route aria-hidden />
                  Audit a flight log
                </Link>
              </Button>
            </>
          }
        />
      </div>
    );
  }

  const mapArea = (
    <div className="relative min-h-0 min-w-0 flex-1">
      <WorkspaceMap
        mapRef={mapRef}
        features={features}
        selected={featureIndices}
        analysis={analysis}
        ownerModule={ownerModule}
        bbox={bbox}
        fitBbox={fitBbox}
        fitKey={fitKey}
        initialView={initialView}
        onSelectFeature={selectFromMap}
        onClearSelection={clearSelection}
        onViewTable={viewTable}
        compact={isMobile}
      />
      {overlay ? (
        // Errors get a scrim so map labels never print through the message; every overlay card
        // is opaque (the error card's danger tint sits on the surface colour).
        <div
          className={cn(
            "pointer-events-none absolute inset-0 z-panel flex items-center justify-center p-4",
            datasetUnavailable && "bg-bg/70",
          )}
        >
          <div className="pointer-events-auto w-full max-w-md rounded bg-surface">{overlay}</div>
        </div>
      ) : null}
    </div>
  );

  const title = datasetId ? null : (
    <h1 className="truncate text-sm font-semibold">
      {activeModule.label}
      <span className="font-normal text-fg-muted"> · no dataset</span>
    </h1>
  );

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-bg">
      <a href="#map" className="skip-link">
        Skip to map
      </a>
      <a
        href="#results"
        className="skip-link"
        onClick={() => {
          if (isMobile) setMobileExpanded(true);
          else setDrawerOpen(true);
        }}
      >
        Skip to results
      </a>
      <LiveRegion />
      <CommandPalette />
      <ThemeSystemSync />
      <DemoBanner className="shrink-0 [&>div]:max-w-none [&>div]:py-1 [&>div]:text-xs" />

      <div className="flex min-h-0 flex-1">
        {!isMobile ? <ModuleRail active={activeModuleId} hrefFor={hrefForModule} onNavigate={onRailNavigate} /> : null}

        <main id="main" tabIndex={-1} className="flex min-w-0 flex-1 flex-col outline-none">
          <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-surface px-2">
            {isMobile ? null : (
              <IconButton
                label={leftOpen ? "Hide layers and features" : "Show layers and features"}
                icon={leftOpen ? <PanelLeftClose /> : <PanelLeftOpen />}
                size="icon-sm"
                side="bottom"
                aria-expanded={leftOpen}
                aria-controls="workspace-left"
                onClick={toggleLeft}
              />
            )}
            {isMobile ? (
              <Button asChild size="icon-sm" variant="ghost" aria-label="All datasets">
                <Link href="/w">
                  <Home aria-hidden />
                </Link>
              </Button>
            ) : null}
            {datasetId ? (
              <DatasetHeader
                datasetId={datasetId}
                file={file}
                isLoading={fileQ.isLoading}
                features={features}
                analysisId={analysisId}
                compact={isMobile}
              />
            ) : (
              <div className="min-w-0 flex-1">{title}</div>
            )}
            {!isMobile ? (
              <IconButton
                ref={inspectorToggleRef}
                label={rightOpen ? "Hide inspector" : "Show inspector"}
                icon={rightOpen ? <PanelRightClose /> : <PanelRightOpen />}
                size="icon-sm"
                side="bottom"
                aria-expanded={rightOpen}
                aria-controls="workspace-inspector"
                onClick={() => (rightOpen ? setRightOpen(false) : openInspector())}
              />
            ) : null}
          </header>

          <div className="flex min-h-0 flex-1">
            {!isMobile ? (
              <aside
                id="workspace-left"
                aria-label="Layers and features"
                hidden={!leftOpen}
                className="flex w-panel-left shrink-0 flex-col border-r border-border bg-surface"
              >
                <LayersPanel
                  hasDataset={Boolean(datasetId)}
                  unavailable={datasetUnavailable}
                  features={features}
                  repaired={repairedFeatures}
                  featuresLoading={Boolean(datasetId) && (fileQ.isLoading || isFileActive(file?.status) || featuresQ.isLoading)}
                  featuresError={featuresQ.isError ? featuresQ.error : null}
                  onRetry={() => void featuresQ.refetch()}
                  selected={featureIndices}
                  onSelect={selectFromTable}
                  onSelectMany={selectMany}
                  layers={layerToggles}
                  onToggleLayer={toggleLayer}
                  onViewTable={viewTable}
                />
              </aside>
            ) : null}

            {mapArea}

            {/* In the page flow on every larger screen (after the map in Tab order, never a focus
                trap); on tablets it takes the layers panel's place instead of covering the map. */}
            {!isMobile ? (
              <aside
                id="workspace-inspector"
                aria-labelledby="inspector-title"
                hidden={!rightOpen}
                onKeyDown={(e) => {
                  // Escape closes the tablet panel (dialogs and menus inside handle their own first).
                  if (e.key !== "Escape" || e.defaultPrevented || !isTablet) return;
                  e.preventDefault();
                  closeInspector();
                }}
                className="flex w-panel-right shrink-0 flex-col border-l border-border bg-surface"
              >
                <div className="flex items-start gap-2 border-b border-border px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <h2 id="inspector-title" ref={inspectorTitleRef} tabIndex={-1} className="text-sm font-semibold outline-none">
                      {activeModule.id === "measure" ? "Inspector" : activeModule.label}
                    </h2>
                    <p className="text-xs text-fg-muted">{activeModule.description}</p>
                  </div>
                  {isTablet ? (
                    <IconButton label="Close inspector" icon={<X />} size="icon-sm" side="left" onClick={closeInspector} />
                  ) : null}
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-3 scrollbar-thin">{inspector}</div>
              </aside>
            ) : null}
          </div>

          {isMobile ? (
            <MobileViewer
              features={features}
              selected={featureIndices}
              onSelect={selectFromTable}
              details={inspector}
              results={mobileResults}
              tab={mobileTab}
              onTabChange={setMobileTab}
              expanded={mobileExpanded}
              onExpandedChange={setMobileExpanded}
            />
          ) : (
            <ResultsDrawer
              open={drawerOpen}
              onOpenChange={setDrawerOpen}
              height={drawerHeight}
              onHeightChange={setDrawerHeight}
              module={activeModule}
              analysis={moduleAnalysis}
              analysisError={analysisId && analysisQ.isError ? analysisQ.error : null}
              onRetryAnalysis={() => void analysisQ.refetch()}
              datasetId={datasetId}
              file={file}
              tab={drawerTab}
              onTabChange={setDrawerTab}
              layerTables={layerTables}
              layerTableId={layerTableId}
              onLayerTableChange={setLayerTableId}
              selectedFeatures={featureIndices}
              repairedFeatures={repairedFeatures}
              onSelectFeature={selectFromTable}
              onHoverFeature={setHoveredFeature}
              history={history}
              historyLoading={historyQ.isLoading}
              onOpenAnalysis={openAnalysis}
              onCancelAnalysis={canCancel ? cancel : undefined}
              cancelling={cancelling}
              onEditParameters={activeModule.kinds.length > 0 ? openInspector : undefined}
              editParametersInBar={isTablet && !rightOpen}
            />
          )}
        </main>
      </div>
    </div>
  );
}
