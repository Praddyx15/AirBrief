"use client";

import * as React from "react";
import { EyeOff, MapPinOff, Search, Table2, Wrench, X } from "lucide-react";
import { areaParts, formatArea, formatLength, formatNumber, lengthParts } from "@/lib/format";
import { useUi, useUnits, type Units } from "@/lib/store";
import type { DatasetFeatureCollection, MeasurementStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { IconButton } from "@/components/ui/icon-button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { MeasurementStatusBadge } from "./status-badges";
import { ContextLayerToggles } from "./context-toggles";

export interface FeatureRow {
  index: number;
  name: string;
  layer: string;
  type: string;
  status: MeasurementStatus;
  area: number | null;
  length: number | null;
  /** false when the CRS was unknown and the feature cannot be drawn */
  hasGeometry: boolean;
  /** the geometry was invalid and was repaired before measuring */
  repaired: boolean;
}

/**
 * Table rows for the dataset's features. `repaired` holds the indices whose geometry was
 * repaired (from the file's `geometry_repaired` issues).
 */
export function featureRows(fc: DatasetFeatureCollection | null | undefined, repaired?: ReadonlySet<number>): FeatureRow[] {
  if (!fc) return [];
  return fc.features.map((f) => {
    const p = f.properties;
    const name = typeof p._name === "string" && p._name.trim() ? p._name.trim() : `Feature ${p._index}`;
    return {
      index: p._index,
      name,
      layer: p._layer,
      type: p._type,
      status: p._status,
      area: typeof p._area_m2 === "number" ? p._area_m2 : null,
      length: typeof p._length_m === "number" ? p._length_m : null,
      hasGeometry: f.geometry !== null,
      repaired: repaired?.has(p._index) ?? false,
    };
  });
}

/**
 * Table cells use fixed decimals per unit (whole m² / ft² / m / ft, two decimals for ha, km²,
 * ac, mi², km and mi), so the decimal points line up down a column.
 */
export function formatAreaCell(m2: number | null, units: Units): string {
  if (m2 === null) return "—";
  const unit = areaParts(m2, units).unit;
  return formatArea(m2, units, { decimals: unit === "m²" || unit === "ft²" ? 0 : 2 });
}

export function formatLengthCell(m: number | null, units: Units): string {
  if (m === null) return "—";
  const unit = lengthParts(m, units).unit;
  return formatLength(m, units, { decimals: unit === "m" || unit === "ft" ? 0 : 2 });
}

/** Marker for a feature whose geometry was repaired (icon with a tooltip and a text alternative). */
export function RepairedMarker() {
  return (
    <SimpleTooltip content="Geometry repaired before measuring">
      <Wrench role="img" aria-label="Geometry repaired" className="size-3.5 shrink-0 text-warn" />
    </SimpleTooltip>
  );
}

/** Case-insensitive filter on name, layer, type and index. */
export function filterFeatureRows(rows: readonly FeatureRow[], query: string): FeatureRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...rows];
  return rows.filter(
    (r) =>
      r.name.toLowerCase().includes(q) ||
      r.layer.toLowerCase().includes(q) ||
      r.type.toLowerCase().includes(q) ||
      String(r.index) === q,
  );
}

export interface LayerToggle {
  id: string;
  label: string;
  hidden: boolean;
  /** e.g. "124 features" */
  detail?: string;
  hasTable?: boolean;
  /** shown indented under this toggle (e.g. a source layer under "Dataset features") */
  parentId?: string;
  /** the switch cannot change (e.g. its parent layer is hidden) */
  disabled?: boolean;
}

export interface LayersPanelProps {
  hasDataset: boolean;
  /** the dataset was not found or could not be processed: no feature tools */
  unavailable?: boolean;
  features: DatasetFeatureCollection | null;
  /** indices of features whose geometry was repaired */
  repaired?: ReadonlySet<number>;
  featuresLoading: boolean;
  featuresError: unknown;
  onRetry: () => void;
  selected: readonly number[];
  /** select one feature (row activation) */
  onSelect: (index: number) => void;
  /** replace the selection */
  onSelectMany: (indices: number[]) => void;
  layers: readonly LayerToggle[];
  onToggleLayer: (id: string) => void;
  onViewTable: (id: string) => void;
}

/** Layers (visibility + table view) and the feature table synced with the map. */
export function LayersPanel({
  hasDataset,
  unavailable = false,
  features,
  repaired,
  featuresLoading,
  featuresError,
  onRetry,
  selected,
  onSelect,
  onSelectMany,
  layers,
  onToggleLayer,
  onViewTable,
}: LayersPanelProps) {
  const units = useUnits();
  const setHoveredFeature = useUi((s) => s.setHoveredFeature);
  const [query, setQuery] = React.useState("");
  const searchId = React.useId();

  const rows = React.useMemo(() => featureRows(features, repaired), [features, repaired]);
  const filtered = React.useMemo(() => filterFeatureRows(rows, query), [rows, query]);

  const columns = React.useMemo<DataTableColumn<FeatureRow>[]>(
    () => [
      {
        id: "name",
        header: "Name",
        rowHeader: true,
        cell: (r) => (
          <span className="flex min-w-0 items-center gap-1.5">
            {!r.hasGeometry ? (
              <MapPinOff role="img" aria-label="Not on the map (unknown CRS)" className="size-3.5 shrink-0 text-warn" />
            ) : null}
            {/* Narrow enough that Size and Status always fit the 300 px panel; the row label has the full name. */}
            <span className="block max-w-28 truncate">{r.name}</span>
            {r.repaired ? <RepairedMarker /> : null}
          </span>
        ),
        sortValue: (r) => r.name,
      },
      {
        id: "measure",
        header: "Size",
        numeric: true,
        cell: (r) => (r.area !== null ? formatAreaCell(r.area, units) : formatLengthCell(r.length, units)),
        sortValue: (r) => r.area ?? r.length,
      },
      {
        id: "status",
        header: "Status",
        cell: (r) => <MeasurementStatusBadge status={r.status} compact />,
        sortValue: (r) => r.status,
      },
    ],
    [units],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <section aria-labelledby="layers-heading" className="border-b border-border p-3">
        <h2 id="layers-heading" className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-muted">
          Layers
        </h2>
        {layers.length === 0 ? (
          <p className="text-sm text-fg-muted">No map layers yet.</p>
        ) : (
          <ul className="space-y-1">
            {layers.map((layer) => {
              const switchId = `layer-toggle-${layer.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
              return (
                <li
                  key={layer.id}
                  className={cn("flex items-center gap-2 rounded px-1 py-1 hover:bg-elevated", layer.parentId && "ml-5")}
                >
                  <Switch
                    id={switchId}
                    checked={!layer.hidden}
                    disabled={layer.disabled}
                    onCheckedChange={() => onToggleLayer(layer.id)}
                  />
                  <Label
                    htmlFor={switchId}
                    className={cn("min-w-0 flex-1 cursor-pointer text-sm font-normal", layer.disabled && "cursor-not-allowed text-fg-muted")}
                  >
                    <span className="flex items-center gap-1.5">
                      <span className="truncate">{layer.label}</span>
                      {layer.hidden ? <EyeOff aria-hidden className="size-3.5 shrink-0 text-fg-muted" /> : null}
                      <span className="sr-only">{layer.hidden ? "(hidden)" : "(visible)"}</span>
                    </span>
                    {layer.detail ? <span className="block text-xs text-fg-muted">{layer.detail}</span> : null}
                  </Label>
                  {layer.hasTable !== false ? (
                    <IconButton
                      label={`View ${layer.label} as table`}
                      icon={<Table2 />}
                      size="icon-sm"
                      side="right"
                      onClick={() => onViewTable(layer.id)}
                    />
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Data-pack reference layers (aerodromes, rings, obstacles…), before any module runs. */}
      <ContextLayerToggles onViewTable={onViewTable} defaultOpen={!hasDataset} />

      <section aria-labelledby="features-heading" className="flex min-h-0 flex-1 flex-col p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 id="features-heading" className="text-xs font-medium uppercase tracking-wide text-fg-muted">
            Features
          </h2>
          {rows.length > 0 ? (
            <span className="num text-xs text-fg-muted" aria-live="polite">
              {filtered.length === rows.length ? formatNumber(rows.length) : `${formatNumber(filtered.length)} of ${formatNumber(rows.length)}`}
            </span>
          ) : null}
        </div>

        {!hasDataset ? (
          <EmptyState compact title="No dataset" description="Draw, pin or open a dataset to list its features here. A flight audit needs none." />
        ) : unavailable ? (
          <EmptyState
            compact
            title="Dataset unavailable"
            description="It was not found or could not be processed, so there are no features to list."
          />
        ) : featuresError ? (
          <ErrorState compact error={featuresError} onRetry={onRetry} />
        ) : featuresLoading ? (
          <div className="space-y-1.5" aria-busy="true">
            <span className="sr-only">Loading features</span>
            <Skeleton className="h-9 w-full" />
            {Array.from({ length: 8 }, (_, i) => (
              <Skeleton key={i} className="h-7 w-full" />
            ))}
          </div>
        ) : (
          <>
            <div className="relative mb-2">
              <Label htmlFor={searchId} className="sr-only">
                Search features
              </Label>
              <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-fg-muted" />
              <Input
                id={searchId}
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Name, layer, type or #"
                className="h-9 pl-8 pr-8"
                autoComplete="off"
              />
              {query ? (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                  className="absolute right-1 top-1/2 inline-flex size-7 -translate-y-1/2 cursor-pointer items-center justify-center rounded text-fg-muted hover:bg-elevated hover:text-fg"
                >
                  <X aria-hidden className="size-4" />
                </button>
              ) : null}
            </div>
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <Button
                size="sm"
                variant="outline"
                disabled={filtered.length === 0}
                onClick={() => onSelectMany(filtered.map((r) => r.index))}
              >
                Select {query ? "matches" : "all"}
              </Button>
              <Button size="sm" variant="ghost" disabled={selected.length === 0} onClick={() => onSelectMany([])}>
                Clear selection
              </Button>
              {selected.length > 0 ? (
                <span className="num text-xs text-fg-muted">{formatNumber(selected.length)} selected</span>
              ) : null}
            </div>
            <DataTable
              data={filtered}
              columns={columns}
              getRowId={(r) => r.index}
              caption="Dataset features. Activate a row to select the feature on the map."
              onRowClick={(r) => onSelect(r.index)}
              onRowHover={(r) => setHoveredFeature(r?.index ?? null)}
              selectedRowIds={selected}
              getRowLabel={(r) => `Select ${r.name}`}
              dense
              maxHeight="100%"
              className="min-h-0 flex-1"
              emptyState={
                <EmptyState
                  compact
                  title={rows.length > 0 ? "No matches" : features ? "No features" : "No dataset"}
                  description={
                    rows.length > 0
                      ? "Try another search."
                      : features
                        ? "The file contains no features."
                        : "Open a dataset to list its features here."
                  }
                />
              }
            />
          </>
        )}
      </section>
    </div>
  );
}
