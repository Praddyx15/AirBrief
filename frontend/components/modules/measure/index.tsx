"use client";

/**
 * Measure module: dataset totals and selection summary (panel), and the sortable,
 * filterable measurements table with CSV export (results drawer). It needs no analysis:
 * measurements are computed at upload (`GET /api/files/{id}/measurements/`).
 */

import * as React from "react";
import { ChevronLeft, ChevronRight, Download, MousePointerClick, Ruler } from "lucide-react";
import { DEMO_MODE, fileExportUrl } from "@/lib/api";
import { MEASUREMENT_STATUS_LABEL } from "@/lib/colors";
import { EMPTY, areaParts, describeProjection, formatArea, formatLength, formatNumber, formatPercent, lengthParts } from "@/lib/format";
import { MODULE_META, type ModuleDefinition, type ModulePanelProps, type ModuleResultsProps } from "@/lib/modules";
import { useFeatures, useMeasurements } from "@/lib/queries";
import { useUi, useUnits } from "@/lib/store";
import { MEASUREMENT_STATUSES, type MeasurementItem, type MeasurementStatus } from "@/lib/types";
import { measurementsToCsv } from "@/lib/csv";
import { crsSourceLabel } from "@/lib/labels";
import { useWorkspaceParams } from "@/lib/url-state";
import { downloadBlob } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { KpiGrid, KpiTile } from "@/components/ui/kpi-tile";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { MeasurementStatusBadge, ToneBadge } from "@/components/workspace/status-badges";
import { PlanStartHint } from "@/components/workspace/plan-start";
import { InputHint, useFocusMap } from "../shared";

const PAGE_SIZE = 500;
const ALL = "all";
const NOT_MEASURED = "Not measured";

/** What a geometry type can be measured for: area and perimeter (polygons) and length (lines). */
function measurableKinds(type: string | null | undefined): { polygon: boolean; line: boolean } {
  const t = type ?? "";
  const mixed = t === "GeometryCollection";
  return { polygon: mixed || t.endsWith("Polygon"), line: mixed || t.endsWith("LineString") };
}

function MeasurePanel({ file, features, selectedFeatures }: ModulePanelProps) {
  const units = useUnits();
  const totals = file?.totals ?? null;

  // Which totals apply to the dataset (polygons, lines) and, once the features are loaded, how
  // many measured features contribute to them (null = not known yet).
  const dataset = React.useMemo(() => {
    const types = new Set<string>();
    for (const layer of file?.layers ?? []) for (const t of Object.keys(layer.geometry_types)) types.add(t);
    for (const f of features?.features ?? []) types.add(f.properties._type);
    let polygons = types.size === 0;
    let lines = types.size === 0;
    for (const t of types) {
      const kinds = measurableKinds(t);
      polygons ||= kinds.polygon;
      lines ||= kinds.line;
    }
    let areas: number | null = null;
    let lengths: number | null = null;
    if (features) {
      areas = 0;
      lengths = 0;
      for (const f of features.features) {
        if (f.properties._status !== "measured") continue;
        if (typeof f.properties._area_m2 === "number") areas += 1;
        if (typeof f.properties._length_m === "number") lengths += 1;
      }
    }
    return { polygons, lines, areas, lengths };
  }, [file, features]);

  // Sums over the selection, with the number of features behind each value so that a value that
  // was not measured (or does not apply) is never shown as a measured zero.
  const selection = React.useMemo(() => {
    if (!features || selectedFeatures.length === 0) return null;
    const wanted = new Set(selectedFeatures);
    const s = { count: 0, measured: 0, polygons: 0, lines: 0, areas: 0, area: 0, perimeters: 0, perimeter: 0, lengths: 0, length: 0 };
    for (const f of features.features) {
      const p = f.properties;
      if (!wanted.has(p._index)) continue;
      s.count += 1;
      const kinds = measurableKinds(p._type);
      if (kinds.polygon) s.polygons += 1;
      if (kinds.line) s.lines += 1;
      if (p._status !== "measured") continue;
      s.measured += 1;
      if (typeof p._area_m2 === "number") {
        s.areas += 1;
        s.area += p._area_m2;
      }
      if (typeof p._perimeter_m === "number") {
        s.perimeters += 1;
        s.perimeter += p._perimeter_m;
      }
      if (typeof p._length_m === "number") {
        s.lengths += 1;
        s.length += p._length_m;
      }
    }
    return s;
  }, [features, selectedFeatures]);

  if (!file) {
    return <PlanStartHint purpose="Measure then gives the area, perimeter and length of every feature." />;
  }

  if (!totals) {
    return (
      <div className="space-y-2" aria-busy="true">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  const notMeasured = totals.unsupported + totals.skipped + totals.error;
  const areaMeasured = dataset.areas !== null ? dataset.areas > 0 : totals.measured > 0;
  const lengthMeasured = dataset.lengths !== null ? dataset.lengths > 0 : totals.measured > 0;

  return (
    <div className="space-y-4 text-sm">
      <section aria-labelledby="measure-totals">
        <h3 id="measure-totals" className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-muted">
          Dataset totals
        </h3>
        <KpiGrid className="grid-cols-2 sm:grid-cols-2 xl:grid-cols-2" label="Dataset totals">
          <KpiTile label="Features" value={formatNumber(totals.feature_count)} explanation={`${formatNumber(totals.measured)} measured`} />
          {dataset.polygons ? (
            <>
              <KpiTile
                label="Area"
                value={areaMeasured ? areaParts(totals.area_m2, units) : EMPTY}
                explanation={areaMeasured ? "Sum of polygon areas" : "No polygon measured"}
              />
              <KpiTile
                label="Perimeter"
                value={areaMeasured ? lengthParts(totals.perimeter_m, units) : EMPTY}
                explanation={areaMeasured ? "Sum of polygon perimeters" : "No polygon measured"}
              />
            </>
          ) : null}
          {dataset.lines ? (
            <KpiTile
              label="Length"
              value={lengthMeasured ? lengthParts(totals.length_m, units) : EMPTY}
              explanation={lengthMeasured ? "Sum of line lengths (2D)" : "No line measured"}
            />
          ) : null}
          {totals.length_3d_m > 0 ? (
            <KpiTile label="3D length" value={lengthParts(totals.length_3d_m, units)} explanation="Lines with heights" />
          ) : null}
          {notMeasured > 0 ? (
            <KpiTile
              label="Not measured"
              value={formatNumber(notMeasured)}
              tone="warn"
              toneLabel="Check these features"
              explanation={`${totals.unsupported} unsupported, ${totals.skipped} skipped, ${totals.error} errors`}
            />
          ) : null}
        </KpiGrid>
        <p className="mt-2 text-xs text-fg-muted">
          CRS: <span className="num text-fg">{file.crs ?? "unknown"}</span>
          {file.crs_source ? ` (from ${crsSourceLabel(file.crs_source)})` : null}. Each feature is measured in projections
          centred on it (equal-area for area, transverse Mercator for length) and checked against the geodesic value.
        </p>
      </section>

      <section aria-labelledby="measure-selection">
        <h3 id="measure-selection" className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-muted">
          Selection
        </h3>
        {selection && selection.count > 0 ? (
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 rounded border border-border bg-surface p-3">
            <dt className="text-fg-muted">Features</dt>
            <dd className="num text-right">{formatNumber(selection.count)}</dd>
            <dt className="text-fg-muted">Measured</dt>
            <dd className="num text-right">{formatNumber(selection.measured)}</dd>
            {selection.polygons > 0 ? (
              <>
                <dt className="text-fg-muted">Area</dt>
                <dd className="num text-right">{selection.areas > 0 ? formatArea(selection.area, units) : NOT_MEASURED}</dd>
                {selection.perimeters > 0 || selection.areas === 0 ? (
                  <>
                    <dt className="text-fg-muted">Perimeter</dt>
                    <dd className="num text-right">
                      {selection.perimeters > 0 ? formatLength(selection.perimeter, units) : NOT_MEASURED}
                    </dd>
                  </>
                ) : null}
              </>
            ) : null}
            {selection.lines > 0 ? (
              <>
                <dt className="text-fg-muted">Length</dt>
                <dd className="num text-right">{selection.lengths > 0 ? formatLength(selection.length, units) : NOT_MEASURED}</dd>
              </>
            ) : null}
            {selection.polygons === 0 && selection.lines === 0 ? (
              <>
                <dt className="text-fg-muted">Area and length</dt>
                <dd className="text-right">Not applicable</dd>
              </>
            ) : null}
          </dl>
        ) : (
          <InputHint icon={<MousePointerClick />}>
            Select features in the table or on the map (Shift+click adds to the selection) to add up their area and
            length.
          </InputHint>
        )}
      </section>

      {file.issues.length > 0 ? (
        <section aria-labelledby="measure-issues">
          <h3 id="measure-issues" className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Issues ({file.issues.length})
          </h3>
          <ul className="space-y-1.5">
            {file.issues.slice(0, 8).map((issue, i) => (
              <li key={`${issue.code}-${i}`} className="flex items-start gap-2">
                <ToneBadge
                  tone={issue.severity === "critical" ? "danger" : issue.severity === "warning" ? "warn" : "info"}
                  label={issue.severity === "critical" ? "Critical" : issue.severity === "warning" ? "Warning" : "Info"}
                />
                <span className="min-w-0 flex-1 text-fg">
                  {issue.message}
                  {issue.feature_index !== null && !issue.message.includes(`Feature ${issue.feature_index} `) ? (
                    <span className="text-fg-muted"> (feature {issue.feature_index})</span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
          {file.issues.length > 8 ? (
            <p className="mt-1 text-xs text-fg-muted">{file.issues.length - 8} more in the report.</p>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function MeasureResults({ datasetId, file }: ModuleResultsProps) {
  const units = useUnits();
  const { featureIndices, setFeature } = useWorkspaceParams();
  const setHoveredFeature = useUi((s) => s.setHoveredFeature);
  const [geometryType, setGeometryType] = React.useState<string>(ALL);
  const [status, setStatus] = React.useState<string>(ALL);
  const [offset, setOffset] = React.useState(0);

  React.useEffect(() => {
    setOffset(0);
  }, [geometryType, status, datasetId]);

  const geometryTypes = React.useMemo(() => {
    const set = new Set<string>();
    for (const layer of file?.layers ?? []) for (const t of Object.keys(layer.geometry_types)) set.add(t);
    return [...set].sort();
  }, [file]);

  const query = React.useMemo(
    () => ({
      limit: PAGE_SIZE,
      offset,
      geometry_type: geometryType === ALL ? undefined : geometryType,
      status: status === ALL ? undefined : (status as MeasurementStatus),
    }),
    [offset, geometryType, status],
  );
  const measurements = useMeasurements(datasetId, query, { enabled: file?.status === "COMPLETED" });
  const items = measurements.data?.items ?? [];
  const total = measurements.data?.total ?? 0;

  // A row click selects the feature and brings it into view, like the other feature tables.
  // The features are the workspace's (same query, already cached): rows carry no geometry.
  const featuresQ = useFeatures(datasetId, { enabled: file?.status === "COMPLETED" });
  const focus = useFocusMap();
  const selectRow = React.useCallback(
    (index: number) => {
      setFeature(index);
      const feature = featuresQ.data?.features.find((f) => f.properties._index === index);
      if (feature?.geometry) focus(feature.geometry);
    },
    [featuresQ.data, focus, setFeature],
  );

  const columns = React.useMemo<DataTableColumn<MeasurementItem>[]>(
    () => [
      { id: "index", header: "#", numeric: true, width: 56, cell: (r) => r.feature_index, sortValue: (r) => r.feature_index },
      {
        id: "name",
        header: "Name",
        rowHeader: true,
        cell: (r) => <span className="block max-w-56 truncate">{r.name ?? `Feature ${r.feature_index}`}</span>,
        sortValue: (r) => r.name ?? "",
      },
      { id: "layer", header: "Layer", cell: (r) => <span className="block max-w-40 truncate">{r.layer}</span>, sortValue: (r) => r.layer },
      { id: "type", header: "Type", cell: (r) => r.geometry_type, sortValue: (r) => r.geometry_type },
      {
        id: "status",
        header: "Status",
        cell: (r) => <MeasurementStatusBadge status={r.measurement.status} />,
        sortValue: (r) => MEASUREMENT_STATUS_LABEL[r.measurement.status],
      },
      {
        id: "area",
        header: "Area",
        numeric: true,
        cell: (r) => formatArea(r.measurement.area_m2, units),
        sortValue: (r) => r.measurement.area_m2,
      },
      {
        id: "perimeter",
        header: "Perimeter",
        numeric: true,
        cell: (r) => formatLength(r.measurement.perimeter_m, units),
        sortValue: (r) => r.measurement.perimeter_m,
      },
      {
        id: "length",
        header: "Length",
        numeric: true,
        cell: (r) => formatLength(r.measurement.length_m, units),
        sortValue: (r) => r.measurement.length_m,
      },
      {
        id: "length3d",
        header: "3D length",
        numeric: true,
        cell: (r) => formatLength(r.measurement.length_3d_m, units),
        sortValue: (r) => r.measurement.length_3d_m,
      },
      {
        id: "deviation",
        header: "Geodesic dev.",
        numeric: true,
        cell: (r) => formatPercent(r.measurement.deviation_pct, { maximumFractionDigits: 3 }),
        sortValue: (r) => (r.measurement.deviation_pct === null ? null : Math.abs(r.measurement.deviation_pct)),
      },
      {
        id: "crs",
        header: "Measured in",
        cell: (r) => <span className="text-xs">{describeProjection(r.measurement.area_crs ?? r.measurement.length_crs)}</span>,
        sortValue: (r) => r.measurement.area_crs ?? r.measurement.length_crs ?? null,
      },
    ],
    [units],
  );

  const exportUrl = datasetId ? fileExportUrl(datasetId, "csv") : null;
  const baseName = (file?.filename ?? "dataset").replace(/\.[^.]+$/, "");

  if (!datasetId || !file) {
    return <EmptyState compact icon={<Ruler />} title="No dataset" description="Draw, pin or open a dataset to list its measurements." />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="measure-filter-type" className="text-xs">
            Geometry type
          </Label>
          <Select value={geometryType} onValueChange={setGeometryType}>
            <SelectTrigger id="measure-filter-type" className="h-8 w-44 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All types</SelectItem>
              {geometryTypes.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="measure-filter-status" className="text-xs">
            Status
          </Label>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger id="measure-filter-status" className="h-8 w-44 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All statuses</SelectItem>
              {MEASUREMENT_STATUSES.map((s) => (
                <SelectItem key={s} value={s}>
                  {MEASUREMENT_STATUS_LABEL[s]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <p className="pb-1.5 text-xs text-fg-muted" aria-live="polite">
          {measurements.isFetching && !measurements.data
            ? "Loading measurements"
            : `${formatNumber(total)} feature${total === 1 ? "" : "s"}${total > PAGE_SIZE ? `, showing ${formatNumber(offset + 1)}–${formatNumber(Math.min(total, offset + PAGE_SIZE))}` : ""}`}
        </p>
        <div className="ml-auto flex items-center gap-2 pb-0.5">
          {total > PAGE_SIZE ? (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                disabled={offset === 0}
                aria-label="Previous page of measurements"
              >
                <ChevronLeft aria-hidden /> Prev
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
                disabled={offset + PAGE_SIZE >= total}
                aria-label="Next page of measurements"
              >
                Next <ChevronRight aria-hidden />
              </Button>
            </>
          ) : null}
          {exportUrl ? (
            <Button asChild size="sm" variant="secondary">
              <a href={exportUrl} download>
                <Download aria-hidden /> CSV
              </a>
            </Button>
          ) : (
            <Button
              size="sm"
              variant="secondary"
              disabled={items.length === 0}
              onClick={() => downloadBlob(measurementsToCsv(items), `${baseName}-measurements.csv`, "text/csv;charset=utf-8")}
            >
              <Download aria-hidden /> CSV{DEMO_MODE ? " (shown rows)" : ""}
            </Button>
          )}
        </div>
      </div>

      {measurements.isError ? (
        <ErrorState compact error={measurements.error} onRetry={() => void measurements.refetch()} />
      ) : measurements.isLoading ? (
        <div className="space-y-1.5" aria-busy="true">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : (
        <DataTable
          data={items}
          columns={columns}
          getRowId={(r) => r.feature_index}
          caption="Measurements per feature"
          onRowClick={(r) => selectRow(r.feature_index)}
          onRowHover={(r) => setHoveredFeature(r?.feature_index ?? null)}
          selectedRowIds={featureIndices}
          getRowLabel={(r) => `Select ${r.name ?? `feature ${r.feature_index}`}`}
          initialSort={{ columnId: "index", direction: "asc" }}
          dense
          maxHeight="100%"
          className="min-h-0 flex-1"
          emptyState={
            <EmptyState compact title="No matching features" description="Change the geometry type or status filter." />
          }
        />
      )}
    </div>
  );
}

export const definition: ModuleDefinition = {
  ...MODULE_META.measure,
  Panel: MeasurePanel,
  Results: MeasureResults,
  layers: [],
};

export default definition;
