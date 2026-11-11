"use client";

/**
 * One result per feature (airbrief_core/batch.py): the toggle the panels share, the results
 * with their summary bar, filters and feature table, and the map layer that colours each feature
 * by its result. `withBatch` wraps a module definition so a per-feature analysis shows these
 * instead of the module's own results.
 */

import * as React from "react";
import type { PickingInfo } from "@deck.gl/core";
import { ListChecks } from "lucide-react";
import { useLocalAreaUnit } from "@/lib/area-units";
import { BATCH_COLUMNS, attributeNames, batchLimit, batchResult, batchVerdictColors, isBatchAnalysis, type BatchColumnContext } from "@/lib/batch";
import { hexToRgba, toneRgba, type RGBA, type Tone } from "@/lib/colors";
import { areaParts, formatLocalArea, formatMethod, formatNumber, localizeUnits } from "@/lib/format";
import type { LegendItem, ModuleDefinition, ModuleLayerContext, ModuleLayerOutput, ModuleResultsProps } from "@/lib/modules";
import { useUi, useUnits } from "@/lib/store";
import type {
  AnalysisInfo,
  AnalysisKind,
  BatchItem,
  BatchResult,
  BatchTone,
  DatasetFeatureCollection,
  Feature,
  FeatureCollection,
  Geometry,
} from "@/lib/types";
import { derived, geoJsonLayer, mapPalette } from "@/components/map/layers/common";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ToneBadge } from "@/components/workspace/status-badges";
import { Disclaimer, IssueList, ModuleSection, SwitchField, SyntheticNote, resultLayer, useFocusMap } from "./shared";

const NAME_KEY = "__name__";
const ERRORS = "__errors__";

export interface BatchToggleProps {
  kind: AnalysisKind;
  on: boolean;
  onChange: (on: boolean) => void;
  keyProperty: string | null;
  onKeyProperty: (key: string | null) => void;
  features: DatasetFeatureCollection | null;
  /** features the run takes (`batchFeatureCount`) */
  count: number;
  /** true when the user selected features (else the whole dataset is used) */
  selection: boolean;
  disabled?: boolean;
}

/**
 * "One result per feature": runs the module on each selected feature on its own. When on, the
 * key column for the table and the exports can be picked from the dataset's attributes.
 */
export function BatchToggle({ kind, on, onChange, keyProperty, onKeyProperty, features, count, selection, disabled }: BatchToggleProps) {
  const id = React.useId();
  const names = React.useMemo(() => {
    const found = attributeNames(features);
    return keyProperty && !found.includes(keyProperty) ? [keyProperty, ...found] : found;
  }, [features, keyProperty]);
  const limit = batchLimit(kind);
  const scope = selection ? `${formatNumber(count)} selected` : `all ${formatNumber(count)}`;
  return (
    <div className="space-y-3">
      <SwitchField
        label={`One result per feature (${scope})`}
        checked={on}
        onChange={onChange}
        disabled={disabled}
        hint={
          on
            ? "Each feature is assessed on its own with these settings: a result table, the features coloured by result on the map, and a CSV with each feature's attributes."
            : "Off: the features are assessed together, as one operation."
        }
      />
      {on && count > limit ? (
        <p className="text-xs text-danger">
          At most {formatNumber(limit)} features in one run: select fewer and run the rest as another batch.
        </p>
      ) : null}
      {on ? (
        <div className="space-y-1.5">
          <Label htmlFor={id}>Name each result by</Label>
          <Select value={keyProperty ?? NAME_KEY} onValueChange={(v) => onKeyProperty(v === NAME_KEY ? null : v)} disabled={disabled}>
            <SelectTrigger id={id}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NAME_KEY}>Feature name (or number)</SelectItem>
              {names.map((n) => (
                <SelectItem key={n} value={n}>
                  {n}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-fg-muted">The key in the table, on the map and in the exports. Every row of the CSV also keeps the feature&apos;s attributes.</p>
        </div>
      ) : null}
    </div>
  );
}

/** The Run button's reason to stay off with the toggle on (none: null). */
export function batchDisabledReason(kind: AnalysisKind, on: boolean, count: number): string | null {
  if (!on) return null;
  if (count === 0) return "No features to assess one by one: select some, or switch One result per feature off.";
  const limit = batchLimit(kind);
  return count > limit ? `One result per feature takes at most ${formatNumber(limit)} features: select fewer.` : null;
}

function verdictColours(result: BatchResult): Map<string, { rgba: RGBA; css: string }> {
  return derived(result, "batch:colours", () => {
    const css = batchVerdictColors(result);
    return new Map(
      result.counts.map((c) => {
        const color = css.get(c.verdict) ?? "var(--text-muted)";
        return [c.verdict, { css: color, rgba: color.startsWith("#") ? hexToRgba(color) : toneRgba(c.tone) }] as const;
      }),
    );
  });
}

function itemTone(item: BatchItem, tones: ReadonlyMap<string, BatchTone>): Tone {
  return item.status === "ok" && item.verdict ? ((tones.get(item.verdict) ?? "neutral") as Tone) : "neutral";
}

function BatchSummaryBar({
  result,
  shown,
  onShown,
}: {
  result: BatchResult;
  shown: string | null;
  onShown: (value: string | null) => void;
}) {
  const colours = verdictColours(result);
  const failed = result.errors + result.skipped;
  return (
    <ToggleGroup
      type="single"
      value={shown ?? ""}
      onValueChange={(v) => onShown(v || null)}
      aria-label="Show only features with this result"
      className="flex-wrap justify-start"
    >
      {result.counts.map((c) => (
        <ToggleGroupItem key={c.verdict} value={c.verdict} className="gap-1.5">
          <span aria-hidden className="inline-block size-2.5 rounded-sm" style={{ background: colours.get(c.verdict)?.css }} />
          {c.label}: {formatNumber(c.count)}
        </ToggleGroupItem>
      ))}
      {failed > 0 ? (
        <ToggleGroupItem value={ERRORS} className="gap-1.5">
          <span aria-hidden className="inline-block size-2.5 rounded-sm border border-fg-muted" />
          Not assessed: {formatNumber(failed)}
        </ToggleGroupItem>
      ) : null}
    </ToggleGroup>
  );
}

/** The per-feature results in the drawer: summary bar, filters, the feature table and the notes. */
export function BatchResults({ analysis, extra }: { analysis: AnalysisInfo | null; extra?: React.ReactNode }) {
  const units = useUnits();
  const { unit: areaUnit, bighaM2 } = useLocalAreaUnit();
  const focus = useFocusMap();
  const highlight = useUi((s) => s.resultHighlight);
  const setHighlight = useUi((s) => s.setResultHighlight);
  const [shown, setShown] = React.useState<string | null>(null);
  const [query, setQuery] = React.useState("");
  const result = batchResult(analysis);
  const queryId = React.useId();

  const tones = React.useMemo(() => new Map((result?.counts ?? []).map((c) => [c.verdict, c.tone])), [result]);
  const labels = React.useMemo(() => new Map((result?.counts ?? []).map((c) => [c.verdict, c.label])), [result]);
  const rank = React.useMemo(() => new Map((result?.counts ?? []).map((c, i) => [c.verdict, i])), [result]);
  const rowOf = React.useMemo(() => new Map((result?.items ?? []).map((item, i) => [item, i])), [result]);
  const geometryOf = React.useMemo(
    () => new Map((result ? batchLayer(result).features : []).map((f) => [f.properties.row, f.geometry])),
    [result],
  );

  const columns = React.useMemo<DataTableColumn<BatchItem>[]>(() => {
    const c: BatchColumnContext = {
      units,
      area: (m2) => (areaUnit === "ha" ? areaParts(m2, units).text : formatLocalArea(m2, areaUnit, bighaM2)),
    };
    const own = (analysis ? BATCH_COLUMNS[analysis.kind] : undefined) ?? [];
    return [
      {
        id: "key",
        header: "Feature",
        rowHeader: true,
        cell: (i) => (
          <>
            {i.key}
            {i.name && i.name !== i.key ? <span className="block text-fg-muted">{i.name}</span> : null}
          </>
        ),
        sortValue: (i) => i.key,
      },
      {
        id: "result",
        header: "Result",
        cell: (i) =>
          i.status === "ok" ? (
            <ToneBadge tone={itemTone(i, tones)} label={labels.get(i.verdict ?? "") ?? i.verdict ?? "—"} />
          ) : (
            <ToneBadge tone="neutral" label={i.status === "skipped" ? "Not run" : "Not assessed"} />
          ),
        sortValue: (i) => (i.status === "ok" ? (rank.get(i.verdict ?? "") ?? 50) : 100),
      },
      ...own.map<DataTableColumn<BatchItem>>((col) => ({
        id: col.id,
        header: col.header,
        numeric: col.numeric,
        cell: (i) => (i.status === "ok" ? col.text(i, c) : "—"),
        sortValue: (i) => (i.status === "ok" ? col.sort(i) : null),
      })),
      {
        id: "notes",
        header: "Notes",
        cell: (i) => (i.error ? <span className="text-fg-muted">{localizeUnits(i.error, units)}</span> : i.issues.length > 0 ? i.issues.length : "—"),
        sortValue: (i) => (i.error ? 1000 : i.issues.length),
      },
    ];
  }, [analysis, units, areaUnit, bighaM2, tones, labels, rank]);

  const items = result?.items;
  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    return (items ?? []).filter((i) => {
      if (shown === ERRORS ? i.status === "ok" : shown !== null && (i.status !== "ok" || i.verdict !== shown)) return false;
      return !q || i.key.toLowerCase().includes(q) || (i.name ?? "").toLowerCase().includes(q);
    });
  }, [items, shown, query]);

  if (!analysis || !result) {
    return analysis?.status === "COMPLETED" ? (
      <EmptyState compact icon={<ListChecks />} title="No result" description="The analysis finished without a result." />
    ) : null;
  }

  const selectedRow = highlight?.startsWith("batch-") ? Number(highlight.slice(6)) : null;
  const filterLabel = shown === ERRORS ? "not assessed" : shown ? labels.get(shown) : null;
  const filtering = shown !== null || query.trim() !== "";

  return (
    <div className="space-y-4">
      <Alert variant="info" role="status">
        <AlertTitle>One result per feature</AlertTitle>
        <AlertDescription>{localizeUnits(result.summary, units)}</AlertDescription>
      </Alert>
      <SyntheticNote synthetic={analysis.datapack_synthetic} />

      <ModuleSection title="Results">
        <BatchSummaryBar result={result} shown={shown} onShown={setShown} />
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-48 flex-1 space-y-1">
            <Label htmlFor={queryId}>Find a feature</Label>
            <Input id={queryId} type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Key or name" />
          </div>
          {filtering ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setShown(null);
                setQuery("");
              }}
            >
              Show all
            </Button>
          ) : null}
        </div>
        <p role="status" className="text-xs text-fg-muted">
          {filtering
            ? `Showing ${formatNumber(filtered.length)} of ${formatNumber(result.items.length)} features${filterLabel ? ` (${filterLabel})` : ""}.`
            : `${formatNumber(result.items.length)} features. Click a row to show it on the map.`}
        </p>
        <DataTable
          data={filtered}
          columns={columns}
          getRowId={(i) => rowOf.get(i) ?? -1}
          caption="One result per feature"
          onRowClick={(i) => {
            const row = rowOf.get(i) ?? -1;
            setHighlight(`batch-${row}`);
            focus(geometryOf.get(row));
          }}
          getRowLabel={(i) => `Show ${i.key} on the map`}
          selectedRowIds={selectedRow === null ? [] : [selectedRow]}
          initialSort={{ columnId: "result", direction: "asc" }}
          maxHeight={420}
          dense
          emptyState={
            <EmptyState
              compact
              title="No feature matches"
              description="Change the filter or the search."
              action={
                <Button type="button" variant="outline" size="sm" onClick={() => { setShown(null); setQuery(""); }}>
                  Show all
                </Button>
              }
            />
          }
        />
        {result.points_only > 0 ? (
          <p className="text-xs text-fg-muted">{formatNumber(result.points_only)} features are drawn as a point on the map (the layer&apos;s size limit).</p>
        ) : null}
      </ModuleSection>

      <p className="text-xs text-fg-muted">{formatMethod(result.method)}</p>
      <IssueList issues={result.issues} />
      {extra}
      <Disclaimer text={result.disclaimer} />
    </div>
  );
}

interface BatchFeatureProps {
  index: number;
  key: string;
  name: string | null;
  status: BatchItem["status"];
  verdict: string | null;
  label: string;
  tone: BatchTone;
  /** the item's place in `items`, added here: it keys the table rows and the highlight */
  row: number;
}

/**
 * The `features` layer with each feature's row. `index` is not enough: in runs that sent their
 * own features it can repeat. The layer keeps the items' order but skips those without a geometry.
 */
function batchLayer(result: BatchResult): FeatureCollection<Geometry | null, BatchFeatureProps> {
  return derived(result, "batch:layer", () => {
    const { items } = result;
    let row = 0;
    const features = resultLayer(result.layers, "features").features.map((f) => {
      const p = f.properties as Omit<BatchFeatureProps, "row">;
      while (row < items.length - 1 && (items[row].index !== p.index || items[row].key !== p.key)) row += 1;
      return { ...f, properties: { ...p, row: row++ } } as Feature<Geometry | null, BatchFeatureProps>;
    });
    return { type: "FeatureCollection", features };
  });
}

/** The features coloured by result, with their legend; `prefix` is the module id. */
export function buildBatchLayers(prefix: string, analysis: AnalysisInfo, ctx: ModuleLayerContext): ModuleLayerOutput {
  const result = batchResult(analysis);
  if (!result) return { layers: [], legends: [] };
  const palette = mapPalette();
  const colours = verdictColours(result);
  const picked = ctx.resultHighlight?.startsWith("batch-") ? Number(ctx.resultHighlight.slice(6)) : null;
  // Features that could not be assessed stay grey.
  const colour = (p: BatchFeatureProps, alpha: number): RGBA => {
    const c = p.status === "ok" && p.verdict ? colours.get(p.verdict)?.rgba : undefined;
    const base = c ?? palette.muted;
    return [base[0], base[1], base[2], alpha];
  };
  const id = `${prefix}:batch`;
  const layer = geoJsonLayer<BatchFeatureProps>({
    id,
    data: batchLayer(result),
    fillColor: (p) => colour(p, p.row === picked ? 150 : 90),
    lineColor: (p) => (p.row === picked ? palette.text : colour(p, 255)),
    lineWidth: (p) => (p.row === picked ? 4 : 2),
    pointRadius: (p) => (p.row === picked ? 9 : 6),
    updateTriggers: [ctx.theme, picked, colours],
  });
  const items: LegendItem[] = result.counts.map((c) => ({
    label: c.label,
    color: colours.get(c.verdict)?.css ?? "var(--text-muted)",
    detail: formatNumber(c.count),
  }));
  if (result.errors + result.skipped > 0) {
    items.push({ label: "Not assessed", color: "var(--text-muted)", detail: formatNumber(result.errors + result.skipped) });
  }
  return {
    layers: [layer],
    legends: [{ id, title: "Result per feature", kind: "categorical", layerId: id, items, note: "Evidence for each feature, not a go/no-go." }],
  };
}

function batchPick(prefix: string, info: PickingInfo): BatchFeatureProps | null {
  if (!(info.layer?.id ?? "").startsWith(`${prefix}:batch`)) return null;
  const props = (info.object as { properties?: BatchFeatureProps } | null)?.properties;
  return props && typeof props.row === "number" ? props : null;
}

/**
 * The module definition with per-feature results: a per-feature analysis gets `BatchResults`
 * (plus `extra`, e.g. a module's permanent link), the coloured features and their tooltip;
 * any other analysis keeps the module's own.
 */
export function withBatch(def: ModuleDefinition, extra?: React.ReactNode): ModuleDefinition {
  const Own = def.Results;
  function Results(props: ModuleResultsProps) {
    if (isBatchAnalysis(props.analysis)) return <BatchResults analysis={props.analysis} extra={extra} />;
    return Own ? <Own {...props} /> : null;
  }
  Results.displayName = `${def.id}-results`;
  return {
    ...def,
    Results,
    layers: [...(def.layers ?? []), { id: `${def.id}:batch`, label: "Result per feature", resultLayer: "features", batch: true }],
    buildLayers: (analysis, ctx) =>
      isBatchAnalysis(analysis) ? buildBatchLayers(def.id, analysis, ctx) : (def.buildLayers?.(analysis, ctx) ?? { layers: [], legends: [] }),
    derivedLayers: def.derivedLayers ? (analysis) => (isBatchAnalysis(analysis) ? {} : (def.derivedLayers?.(analysis) ?? {})) : undefined,
    describePick: (info) => {
      const p = batchPick(def.id, info);
      if (!p) return def.describePick?.(info) ?? null;
      const name = p.name && p.name !== p.key ? `${p.key} (${p.name})` : p.key;
      return `${name}: ${p.label}`;
    },
    onPick: (info) => {
      const p = batchPick(def.id, info);
      if (!p) return def.onPick?.(info) ?? false;
      useUi.getState().setResultHighlight(`batch-${p.row}`);
      return true;
    },
  };
}
