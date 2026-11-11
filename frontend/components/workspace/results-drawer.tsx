"use client";

import * as React from "react";
import {
  Ban,
  BarChart3,
  ChevronDown,
  ChevronUp,
  GripHorizontal,
  History,
  PanelBottomClose,
  PanelBottomOpen,
  SlidersHorizontal,
  Table2,
} from "lucide-react";
import { AnalysisExportMenu } from "@/components/report/analysis-export";
import { formatDate, formatDuration, formatNumber, formatRelative } from "@/lib/format";
import { analysisParamsSummary, type ModuleDefinition } from "@/lib/modules";
import { useAddedByVisitor, useMeta } from "@/lib/queries";
import { DRAWER_MIN_HEIGHT, useUnits } from "@/lib/store";
import type { AnalysisInfo, DatasetFeatureCollection, FeatureCollection, FileInfo } from "@/lib/types";
import { clamp, cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { IconButton } from "@/components/ui/icon-button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { analysisKindLabel } from "./analysis-runner";
import { AnalysisJobProgress } from "./job-progress";
import { RepairedMarker, featureRows, formatAreaCell, formatLengthCell, type FeatureRow } from "./layers-panel";
import { AnalysisStatusBadge, MeasurementStatusBadge, VisitorBadge } from "./status-badges";

export type DrawerTab = "results" | "layers" | "history";

const TAB_TRIGGERS: readonly { value: DrawerTab; icon: typeof BarChart3; label: string }[] = [
  { value: "results", icon: BarChart3, label: "Results" },
  { value: "layers", icon: Table2, label: "Layers as table" },
  { value: "history", icon: History, label: "History" },
];

/** A map layer that can be shown as a table. */
export interface LayerTableSource {
  id: string;
  label: string;
  kind: "dataset" | "result";
  collection: FeatureCollection | DatasetFeatureCollection | null;
}

const STEP = 32;
const PAGE_STEP = 160;
/** The map keeps at least this much height (px) and share of the space it shares with the drawer. */
const MAP_MIN_HEIGHT = 320;
const MAP_MIN_SHARE = 0.4;

/**
 * Tallest drawer that still leaves the map usable: measured from the column the drawer shares
 * with the map (its parent, minus the header), not from the window, so banners and short
 * screens are accounted for. The drawer always keeps a little room to resize.
 */
function useMaxDrawerHeight(ref: React.RefObject<HTMLElement | null>): number {
  const [max, setMax] = React.useState(560);
  React.useEffect(() => {
    const column = ref.current?.parentElement;
    if (!column) return;
    const update = () => {
      const header = column.querySelector<HTMLElement>(":scope > header");
      const shared = column.clientHeight - (header?.offsetHeight ?? 0);
      const mapMin = Math.max(MAP_MIN_HEIGHT, Math.round(shared * MAP_MIN_SHARE));
      setMax(Math.max(DRAWER_MIN_HEIGHT + 40, Math.min(Math.round(shared * 0.72), shared - mapMin)));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(column);
    return () => observer.disconnect();
  }, [ref]);
  return max;
}

/** "one_to_one" → "One to one", "buffer_m" → "Buffer m": readable table headers and values. */
function humanizeKey(key: string): string {
  const s = key.replace(/[_-]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : key;
}

/** Common unit suffixes of result properties, shown in the header instead of the key. */
const UNIT_SUFFIXES: readonly [string, string][] = [
  ["_per_km2", "per km²"],
  ["_m2", "m²"],
  ["_km2", "km²"],
  ["_km", "km"],
  ["_m", "m"],
  ["_ms", "m/s"],
  ["_kmh", "km/h"],
  ["_pct", "%"],
  ["_deg", "°"],
  ["_db", "dB"],
  ["_s", "s"],
  ["_min", "min"],
  ["_kg", "kg"],
];

function columnHeader(key: string): string {
  for (const [suffix, unit] of UNIT_SUFFIXES) {
    if (key.endsWith(suffix) && key.length > suffix.length) return `${humanizeKey(key.slice(0, -suffix.length))} (${unit})`;
  }
  return humanizeKey(key);
}

interface ResultRow {
  i: number;
  geometry: string;
  props: Record<string, unknown>;
}

/** snake_case codes such as "one_to_one" (not names or ids, which have no underscores). */
const ENUM_VALUE = /^[a-z0-9]+(?:_[a-z0-9]+)+$/;

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return formatNumber(value, { maximumFractionDigits: 4 });
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return ENUM_VALUE.test(value) ? humanizeKey(value) : value;
  try {
    const s = JSON.stringify(value);
    return s.length > 80 ? `${s.slice(0, 80)}…` : s;
  } catch {
    return String(value);
  }
}

function sortable(value: unknown): string | number | boolean | null {
  if (typeof value === "number" || typeof value === "string" || typeof value === "boolean") return value;
  return value === null || value === undefined ? null : cellText(value);
}

function ResultLayerTable({ source }: { source: LayerTableSource }) {
  const rows = React.useMemo<ResultRow[]>(
    () =>
      (source.collection?.features ?? []).map((f, i) => ({
        i,
        geometry: f.geometry?.type ?? "none",
        props: (f.properties ?? {}) as Record<string, unknown>,
      })),
    [source.collection],
  );
  const keys = React.useMemo(() => {
    const seen = new Set<string>();
    for (const r of rows.slice(0, 300)) for (const k of Object.keys(r.props)) seen.add(k);
    return [...seen].slice(0, 12);
  }, [rows]);
  const columns = React.useMemo<DataTableColumn<ResultRow>[]>(
    () => [
      { id: "#", header: "#", numeric: true, width: 56, cell: (r) => r.i + 1, sortValue: (r) => r.i },
      { id: "geometry", header: "Geometry", cell: (r) => r.geometry, sortValue: (r) => r.geometry },
      ...keys.map<DataTableColumn<ResultRow>>((k, idx) => ({
        id: `p:${k}`,
        header: columnHeader(k),
        rowHeader: idx === 0,
        numeric: rows.some((r) => typeof r.props[k] === "number"),
        cell: (r) => <span className="block max-w-56 truncate">{cellText(r.props[k])}</span>,
        sortValue: (r) => sortable(r.props[k]),
      })),
    ],
    [keys, rows],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      getRowId={(r) => r.i}
      caption={`${source.label} as a table`}
      dense
      maxHeight="100%"
      className="min-h-0 flex-1"
      emptyState={<EmptyState compact title="This layer is empty" />}
    />
  );
}

function DatasetLayerTable({
  collection,
  repaired,
  selected,
  onSelect,
  onHover,
}: {
  collection: DatasetFeatureCollection | null;
  repaired?: ReadonlySet<number>;
  selected: readonly number[];
  onSelect: (index: number) => void;
  onHover: (index: number | null) => void;
}) {
  const units = useUnits();
  const rows = React.useMemo(() => featureRows(collection, repaired), [collection, repaired]);
  const columns = React.useMemo<DataTableColumn<FeatureRow>[]>(
    () => [
      { id: "index", header: "#", numeric: true, width: 56, cell: (r) => r.index, sortValue: (r) => r.index },
      { id: "name", header: "Name", rowHeader: true, cell: (r) => <span className="block max-w-56 truncate">{r.name}</span>, sortValue: (r) => r.name },
      { id: "layer", header: "Layer", cell: (r) => r.layer, sortValue: (r) => r.layer },
      { id: "type", header: "Type", cell: (r) => r.type, sortValue: (r) => r.type },
      {
        id: "status",
        header: "Status",
        cell: (r) => (
          <span className="inline-flex items-center gap-1.5">
            <MeasurementStatusBadge status={r.status} />
            {r.repaired ? <RepairedMarker /> : null}
          </span>
        ),
        sortValue: (r) => r.status,
      },
      { id: "area", header: "Area", numeric: true, cell: (r) => formatAreaCell(r.area, units), sortValue: (r) => r.area },
      { id: "length", header: "Length", numeric: true, cell: (r) => formatLengthCell(r.length, units), sortValue: (r) => r.length },
      { id: "map", header: "On map", cell: (r) => (r.hasGeometry ? "Yes" : "No (unknown CRS)"), sortValue: (r) => r.hasGeometry },
    ],
    [units],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      getRowId={(r) => r.index}
      caption="Dataset features as a table"
      onRowClick={(r) => onSelect(r.index)}
      onRowHover={(r) => onHover(r?.index ?? null)}
      selectedRowIds={selected}
      getRowLabel={(r) => `Select ${r.name}`}
      dense
      maxHeight="100%"
      className="min-h-0 flex-1"
      emptyState={<EmptyState compact title="No features" />}
    />
  );
}

export interface ResultsDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  height: number;
  onHeightChange: (height: number) => void;
  module: ModuleDefinition;
  /** the URL analysis when it belongs to `module` */
  analysis: AnalysisInfo | null;
  /** loading the URL analysis failed */
  analysisError?: unknown;
  onRetryAnalysis?: () => void;
  datasetId: string | null;
  file: FileInfo | null;
  tab: DrawerTab;
  onTabChange: (tab: DrawerTab) => void;
  layerTables: readonly LayerTableSource[];
  layerTableId: string | null;
  onLayerTableChange: (id: string) => void;
  selectedFeatures: readonly number[];
  /** indices of features whose geometry was repaired (marked in the dataset table) */
  repairedFeatures?: ReadonlySet<number>;
  onSelectFeature: (index: number) => void;
  onHoverFeature: (index: number | null) => void;
  history: readonly AnalysisInfo[];
  historyLoading?: boolean;
  onOpenAnalysis: (analysis: AnalysisInfo) => void;
  onCancelAnalysis?: (analysis: AnalysisInfo) => void;
  cancelling?: boolean;
  /** open the inspector and move focus to the module form (offered in the empty states) */
  onEditParameters?: () => void;
  /** also offer "Edit parameters" in the tab bar (tablets, where the inspector closes while a job runs) */
  editParametersInBar?: boolean;
  className?: string;
}

function EditParametersButton({ onClick, className }: { onClick: () => void; className?: string }) {
  return (
    <Button size="sm" variant="secondary" onClick={onClick} className={className}>
      <SlidersHorizontal aria-hidden />
      Edit parameters
    </Button>
  );
}

/** Resizable bottom drawer: module results, every layer as a table, and the analysis history. */
export function ResultsDrawer({
  open,
  onOpenChange,
  height,
  onHeightChange,
  module,
  analysis,
  analysisError,
  onRetryAnalysis,
  datasetId,
  file,
  tab,
  onTabChange,
  layerTables,
  layerTableId,
  onLayerTableChange,
  selectedFeatures,
  repairedFeatures,
  onSelectFeature,
  onHoverFeature,
  history,
  historyLoading = false,
  onOpenAnalysis,
  onCancelAnalysis,
  cancelling = false,
  onEditParameters,
  editParametersInBar = false,
  className,
}: ResultsDrawerProps) {
  const sectionRef = React.useRef<HTMLElement>(null);
  const maxHeight = useMaxDrawerHeight(sectionRef);
  const h = clamp(height, DRAWER_MIN_HEIGHT, maxHeight);
  const drag = React.useRef<{ startY: number; startH: number; pointerId: number } | null>(null);
  const tabsId = React.useId();

  const setH = (next: number) => onHeightChange(clamp(Math.round(next), DRAWER_MIN_HEIGHT, maxHeight));

  const onSeparatorKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    let next: number | null = null;
    if (e.key === "ArrowUp") next = h + STEP;
    else if (e.key === "ArrowDown") next = h - STEP;
    else if (e.key === "PageUp") next = h + PAGE_STEP;
    else if (e.key === "PageDown") next = h - PAGE_STEP;
    else if (e.key === "Home") next = DRAWER_MIN_HEIGHT;
    else if (e.key === "End") next = maxHeight;
    if (next !== null) {
      e.preventDefault();
      setH(next);
    }
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { startY: e.clientY, startH: h, pointerId: e.pointerId };
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.pointerId !== e.pointerId) return;
    setH(d.startH + (d.startY - e.clientY));
  };
  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === e.pointerId) {
      drag.current = null;
      if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    }
  };

  const Results = module.Results;
  const showsAnalysisModule = module.kinds.length > 0;
  const activeTable = layerTables.find((t) => t.id === layerTableId) ?? layerTables[0] ?? null;

  let resultsBody: React.ReactNode;
  /** whether the visible results panel already shows "Edit parameters" (its empty states) */
  let bodyOffersEdit = false;
  if (analysisError && showsAnalysisModule) {
    resultsBody = <ErrorState compact error={analysisError} onRetry={onRetryAnalysis} />;
  } else if (showsAnalysisModule && !analysis) {
    bodyOffersEdit = true;
    resultsBody = (
      <EmptyState
        compact
        icon={<BarChart3 />}
        title={`No ${module.label.toLowerCase()} results yet`}
        description="Fill in the form in the inspector and run it. Results appear here."
        action={onEditParameters ? <EditParametersButton onClick={onEditParameters} /> : undefined}
      />
    );
  } else if (analysis?.status === "CANCELLED") {
    bodyOffersEdit = true;
    resultsBody = (
      <EmptyState
        compact
        icon={<Ban />}
        title="Cancelled: no results"
        description="Change the parameters in the inspector if needed, then run it again."
        action={onEditParameters ? <EditParametersButton onClick={onEditParameters} /> : undefined}
      />
    );
  } else if (Results) {
    resultsBody = <Results analysis={analysis} datasetId={datasetId} file={file} />;
  } else {
    resultsBody = <EmptyState compact title="This module has no results view" />;
  }

  return (
    <section
      ref={sectionRef}
      id="results"
      tabIndex={-1}
      aria-label="Results"
      className={cn("relative z-drawer flex shrink-0 flex-col border-t border-border bg-surface outline-none", className)}
      style={open ? { height: h } : undefined}
    >
      {open ? (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize results drawer"
          aria-valuemin={DRAWER_MIN_HEIGHT}
          aria-valuemax={maxHeight}
          aria-valuenow={h}
          aria-valuetext={`${h} pixels tall`}
          tabIndex={0}
          onKeyDown={onSeparatorKey}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          className="group absolute inset-x-0 -top-1.5 z-10 flex h-3 cursor-row-resize touch-none items-center justify-center"
        >
          <span className="flex h-3 w-12 items-center justify-center rounded-full border border-border bg-elevated text-fg-muted group-hover:text-fg group-focus-visible:text-fg">
            <GripHorizontal aria-hidden className="size-3.5" />
          </span>
        </div>
      ) : null}

      <Tabs
        value={tab}
        onValueChange={(v) => {
          if (v === "results" || v === "layers" || v === "history") {
            onTabChange(v);
            if (!open) onOpenChange(true);
          }
        }}
        className="flex min-h-0 flex-1 flex-col"
      >
        {/* Wraps instead of pushing the drawer controls off-screen when space runs out (a
            tablet with the status badge and "Edit parameters" in the bar). */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-1.5">
          <TabsList aria-label="Results views" id={tabsId} className="h-8">
            {TAB_TRIGGERS.map(({ value, icon: Icon, label }) => (
              <TabsTrigger
                key={value}
                value={value}
                // Radix reports no change for the selected tab, so a closed drawer opens on click
                // (Enter and Space click too). While closed, no tab looks selected.
                onClick={() => {
                  if (!open) onOpenChange(true);
                }}
                className={cn(
                  "text-xs",
                  !open && "data-[state=active]:bg-transparent data-[state=active]:text-fg-muted data-[state=active]:shadow-none",
                )}
              >
                <Icon aria-hidden />
                {value === "results" ? (module.kinds.length > 0 ? `${module.label} results` : "Measurements") : label}
              </TabsTrigger>
            ))}
          </TabsList>
          {analysis ? <AnalysisStatusBadge status={analysis.status} className="hidden md:inline-flex" /> : null}
          {/* Not twice on screen: the results panel's empty states carry their own. */}
          {editParametersInBar && onEditParameters && !(open && tab === "results" && bodyOffersEdit) ? (
            <EditParametersButton onClick={onEditParameters} />
          ) : null}
          {/* Every format of the analysis (JSON, GeoJSON, KML, CSV tables), from tablet width up. */}
          {analysis && analysis.status === "COMPLETED" ? (
            <AnalysisExportMenu
              analysis={analysis}
              baseName={(file?.filename ?? "airbrief").replace(/\.[^.]+$/, "").replace(/[^\w.-]+/g, "_")}
              variant="ghost"
              className="hidden md:inline-flex"
            />
          ) : null}
          <div className="ml-auto flex items-center gap-0.5">
            {open ? (
              <>
                    <IconButton label="Make the drawer taller" icon={<ChevronUp />} size="icon-sm" onClick={() => setH(h + PAGE_STEP)} disabled={h >= maxHeight} />
                    <IconButton
                      label="Make the drawer shorter"
                      icon={<ChevronDown />}
                      size="icon-sm"
                      onClick={() => setH(h - PAGE_STEP)}
                      disabled={h <= DRAWER_MIN_HEIGHT}
                    />
              </>
            ) : null}
            <IconButton
              label={open ? "Hide results" : "Show results"}
              icon={open ? <PanelBottomClose /> : <PanelBottomOpen />}
              size="icon-sm"
              aria-expanded={open}
              onClick={() => onOpenChange(!open)}
            />
          </div>
        </div>

        {/* The panels stay mounted while the drawer is closed (hidden and empty), so every tab's
            aria-controls points at an element that exists. */}
        <div className={open ? "flex min-h-0 flex-1 flex-col px-3 pb-2" : "hidden"}>
          <TabsContent value="results" className="mt-0 flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto data-[state=inactive]:hidden">
            {open ? (
              <>
                {analysis && analysis.status !== "COMPLETED" ? (
                  analysis.status === "FAILED" ? (
                    <ErrorState compact error={analysis.error ?? "The analysis failed."} title={`${analysisKindLabel(analysis.kind)} failed`} />
                  ) : (
                    <AnalysisJobProgress
                      compact
                      analysis={analysis}
                      onCancel={onCancelAnalysis ? () => onCancelAnalysis(analysis) : undefined}
                      cancelling={cancelling}
                    />
                  )
                ) : null}
                <div className="flex min-h-0 flex-1 flex-col">{resultsBody}</div>
              </>
            ) : null}
          </TabsContent>

          <TabsContent value="layers" className="mt-0 flex min-h-0 flex-1 flex-col gap-2 data-[state=inactive]:hidden">
            {open ? (
              <>
                {layerTables.length === 0 ? (
                  <EmptyState compact icon={<Table2 />} title="No layers" description="Open a dataset or run an analysis to see its layers here." />
                ) : (
                  <>
                    <div className="flex items-center gap-2">
                      <Label htmlFor="drawer-layer-table" className="text-xs">
                        Layer
                      </Label>
                      <Select value={activeTable?.id ?? ""} onValueChange={onLayerTableChange}>
                        <SelectTrigger id="drawer-layer-table" className="h-8 w-64 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {layerTables.map((t) => (
                            <SelectItem key={t.id} value={t.id}>
                              {t.label} ({formatNumber(t.collection?.features.length ?? 0)})
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    {activeTable ? (
                      activeTable.kind === "dataset" ? (
                        <DatasetLayerTable
                          collection={activeTable.collection as DatasetFeatureCollection | null}
                          repaired={repairedFeatures}
                          selected={selectedFeatures}
                          onSelect={onSelectFeature}
                          onHover={onHoverFeature}
                        />
                      ) : (
                        <ResultLayerTable key={activeTable.id} source={activeTable} />
                      )
                    ) : null}
                  </>
                )}
              </>
            ) : null}
          </TabsContent>

          <TabsContent value="history" className="mt-0 flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden">
            {open ? (
              <>
                <HistoryTable history={history} loading={historyLoading} current={analysis?.id ?? null} onOpen={onOpenAnalysis} />
              </>
            ) : null}
          </TabsContent>
        </div>
      </Tabs>
    </section>
  );
}

function HistoryTable({
  history,
  loading,
  current,
  onOpen,
}: {
  history: readonly AnalysisInfo[];
  loading: boolean;
  current: string | null;
  onOpen: (analysis: AnalysisInfo) => void;
}) {
  const units = useUnits();
  const cameras = useMeta().data?.cameras;
  const addedByVisitor = useAddedByVisitor();
  const columns = React.useMemo<DataTableColumn<AnalysisInfo>[]>(
    () => [
      {
        id: "kind",
        header: "Analysis",
        rowHeader: true,
        // the badge wraps under the name w/ a narrow column (tablet drawer)
        cell: (a) =>
          addedByVisitor(a) ? (
            <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              {analysisKindLabel(a.kind)}
              <VisitorBadge />
            </span>
          ) : (
            analysisKindLabel(a.kind)
          ),
        sortValue: (a) => analysisKindLabel(a.kind),
      },
      {
        // What tells repeated runs apart (height, camera, end points…).
        id: "params",
        header: "Parameters",
        cell: (a) => {
          const summary = analysisParamsSummary(a, units, cameras);
          return summary ? (
            <span className="block max-w-72 truncate" title={summary}>
              {summary}
            </span>
          ) : (
            "—"
          );
        },
        sortValue: (a) => analysisParamsSummary(a, units, cameras),
      },
      { id: "status", header: "Status", cell: (a) => <AnalysisStatusBadge status={a.status} />, sortValue: (a) => a.status },
      {
        id: "created",
        header: "Started",
        cell: (a) => (
          <time dateTime={a.created_at} title={formatDate(a.created_at)}>
            {formatRelative(a.created_at)}
          </time>
        ),
        sortValue: (a) => a.created_at,
      },
      {
        id: "duration",
        header: "Duration",
        numeric: true,
        cell: (a) => (a.duration_ms !== null ? formatDuration(a.duration_ms / 1000, { precise: true }) : "—"),
        sortValue: (a) => a.duration_ms,
      },
      {
        id: "features",
        header: "Features",
        numeric: true,
        cell: (a) => (a.feature_indices && a.feature_indices.length > 0 ? formatNumber(a.feature_indices.length) : "All"),
        sortValue: (a) => a.feature_indices?.length ?? null,
      },
    ],
    [addedByVisitor, cameras, units],
  );
  return (
    <DataTable
      data={history}
      columns={columns}
      getRowId={(a) => a.id}
      caption="Analyses run on this dataset. Activate a row to open it."
      onRowClick={(a) => onOpen(a)}
      selectedRowIds={current ? [current] : []}
      getRowLabel={(a) => {
        const summary = analysisParamsSummary(a, units, cameras);
        const visitor = addedByVisitor(a) ? ", added by another visitor" : "";
        return `Open ${analysisKindLabel(a.kind)}${summary ? ` (${summary})` : ""} from ${formatRelative(a.created_at)}${visitor}`;
      }}
      initialSort={{ columnId: "created", direction: "desc" }}
      dense
      maxHeight="100%"
      className="min-h-0 flex-1"
      emptyState={
        <EmptyState compact icon={<History />} title={loading ? "Loading analyses" : "No analyses yet"} description={loading ? undefined : "Run a module to see it here."} />
      }
    />
  );
}
