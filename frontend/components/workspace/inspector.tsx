"use client";

import * as React from "react";
import { ChevronRight, FileX, HelpCircle, Monitor, MousePointerClick, Wrench } from "lucide-react";
import { isApiError } from "@/lib/api";
import { describeProjection, formatArea, formatLength, formatMethod, formatNumber, formatPercent } from "@/lib/format";
import type { ModuleDefinition } from "@/lib/modules";
import { useFeature } from "@/lib/queries";
import { useUnits } from "@/lib/store";
import type { AnalysisInfo, DatasetFeatureCollection, FeatureRecord, FileInfo, Measurement, MeasurementStatus } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { Skeleton, SkeletonText } from "@/components/ui/skeleton";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { InputHint } from "@/components/modules/shared";
import { MeasurementStatusBadge } from "./status-badges";

const STATUS_HELP: Record<Exclude<MeasurementStatus, "measured">, { what: string; fix: string }> = {
  not_applicable: {
    what: "Points have no area or length, so there is nothing to measure.",
    fix: "Nothing to fix. Points are still used by modules (for example as eVTOL corridor end points).",
  },
  unsupported: {
    what: "This geometry could not be measured in its current form.",
    fix: "Export it again as Polygon or LineString geometry (not curves, TINs or 3D solids), then upload it again.",
  },
  skipped: {
    what: "The feature was skipped, usually because its coordinate reference system (CRS) is unknown.",
    fix: "Upload the file again and pick the CRS in the CRS override field, or include the .prj file with a shapefile.",
  },
  error: {
    what: "Measuring this feature failed.",
    fix: "Check the geometry in a GIS tool (self-intersections, empty rings), repair it and upload again.",
  },
};

function WhyPopover({ status, reason }: { status: Exclude<MeasurementStatus, "measured">; reason: string | null }) {
  const help = STATUS_HELP[status];
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="sm" variant="ghost" className="h-6 gap-1 px-1.5 text-xs text-accent-text">
          <HelpCircle aria-hidden className="size-3.5" />
          Why?
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="space-y-2">
        <p className="font-medium">{help.what}</p>
        {reason ? (
          <p className="text-fg-muted">
            <span className="font-medium text-fg">Reason: </span>
            {reason}
          </p>
        ) : null}
        <p className="text-fg-muted">
          <span className="font-medium text-fg">How to fix: </span>
          {help.fix}
        </p>
      </PopoverContent>
    </Popover>
  );
}

function Row({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  // The label keeps its width; a long value (e.g. a projection description) wraps instead of
  // squeezing the label until the two overlap.
  return (
    <div className="grid grid-cols-[minmax(6.5rem,max-content)_minmax(0,1fr)] items-baseline gap-x-3 py-1">
      <dt className="text-fg-muted">
        {label}
        {hint ? <span className="block text-2xs leading-snug">{hint}</span> : null}
      </dt>
      <dd className="num break-words text-right text-fg">{value}</dd>
    </div>
  );
}

function MeasurementCard({ m, geometryType }: { m: Measurement; geometryType: string }) {
  const units = useUnits();
  const isArea = m.area_m2 !== null;
  const isLine = m.length_m !== null;
  const parts = Object.entries(m.parts ?? {}).filter(([, v]) => typeof v === "number" && v > 0);
  const projStrings: [string, string][] = [];
  if (m.area_crs?.startsWith("+proj=")) projStrings.push([m.area_crs === m.length_crs ? "Area and length" : "Area", m.area_crs]);
  if (m.length_crs?.startsWith("+proj=") && m.length_crs !== m.area_crs) projStrings.push(["Length", m.length_crs]);
  return (
    <section aria-labelledby="inspector-measurement" className="rounded border border-border bg-surface p-3">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 id="inspector-measurement" className="text-sm font-medium">
          Measurement
        </h3>
        {m.repaired ? (
          <Badge variant="warn">
            <Wrench aria-hidden />
            Repaired
          </Badge>
        ) : null}
      </div>
      {m.repaired ? (
        <p className="mb-1 text-xs text-fg-muted">The geometry was invalid and was repaired before measuring.</p>
      ) : null}
      <dl className="divide-y divide-border text-sm">
        {isArea ? <Row label="Area" value={formatArea(m.area_m2, units)} /> : null}
        {m.perimeter_m !== null ? <Row label="Perimeter" value={formatLength(m.perimeter_m, units)} /> : null}
        {isLine ? <Row label="Length (2D)" value={formatLength(m.length_m, units)} /> : null}
        {m.has_z || m.length_3d_m !== null ? (
          <Row label="Length (3D)" value={formatLength(m.length_3d_m, units)} hint="Includes height changes" />
        ) : null}
        {!isArea && !isLine && m.perimeter_m === null ? (
          <Row label="Geometry" value={geometryType} />
        ) : null}
        <Row label="Method" value={<span className="font-sans">{formatMethod(m.method)}</span>} />
        {m.area_crs && m.length_crs && m.area_crs !== m.length_crs ? (
          <>
            <Row label="Area measured in" value={<span className="font-sans">{describeProjection(m.area_crs)}</span>} />
            <Row label="Length measured in" value={<span className="font-sans">{describeProjection(m.length_crs)}</span>} />
          </>
        ) : (
          <Row label="Measured in" value={<span className="font-sans">{describeProjection(m.area_crs ?? m.length_crs)}</span>} />
        )}
        {m.utm_zone || m.utm_epsg ? (
          <Row label="UTM zone" value={`${m.utm_zone ?? "—"}${m.utm_epsg ? ` (EPSG:${m.utm_epsg})` : ""}`} />
        ) : null}
        {m.geodesic_area_m2 !== null ? <Row label="Geodesic area" value={formatArea(m.geodesic_area_m2, units)} hint="On the WGS84 ellipsoid" /> : null}
        {m.geodesic_perimeter_m !== null ? (
          <Row label="Geodesic perimeter" value={formatLength(m.geodesic_perimeter_m, units)} />
        ) : null}
        {m.geodesic_length_m !== null ? <Row label="Geodesic length" value={formatLength(m.geodesic_length_m, units)} hint="On the WGS84 ellipsoid" /> : null}
        {m.deviation_pct !== null ? (
          <Row
            label="Geodesic deviation"
            value={formatPercent(m.deviation_pct, { maximumFractionDigits: 4, signed: true })}
            hint="Projected value vs. geodesic value"
          />
        ) : null}
        {m.grid_area_m2 !== null ? <Row label="Grid area" value={formatArea(m.grid_area_m2, units)} hint="In the file&apos;s own CRS" /> : null}
        {m.grid_length_m !== null ? (
          // For areas without lines the API reports the polygon perimeter here.
          <Row
            label={m.length_m === null && m.perimeter_m !== null ? "Grid perimeter" : "Grid length"}
            value={formatLength(m.grid_length_m, units)}
            hint="In the file&apos;s own CRS"
          />
        ) : null}
        {parts.length > 0 ? (
          <Row label="Parts" value={parts.map(([k, v]) => `${formatNumber(v)} ${k.replace(/_/g, " ")}`).join(", ")} />
        ) : null}
      </dl>
      {projStrings.length > 0 ? (
        <details className="group mt-2 text-xs">
          <summary className="inline-flex min-h-6 cursor-pointer list-none items-center gap-1 text-fg-muted hover:text-fg pointer-coarse:min-h-11 [&::-webkit-details-marker]:hidden">
            <ChevronRight aria-hidden className="size-3.5 transition-transform duration-200 group-open:rotate-90 motion-reduce:transition-none" />
            PROJ definitions
          </summary>
          <ul className="mt-1 space-y-1">
            {projStrings.map(([label, text]) => (
              <li key={label}>
                <span className="text-fg-muted">{label}: </span>
                {/* Wrap between "+param=value" tokens, never inside a number or keyword. */}
                <code className="select-all break-words font-mono text-fg">{text}</code>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function formatPropertyValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return formatNumber(value, { maximumFractionDigits: 6 });
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value;
  try {
    const s = JSON.stringify(value);
    return s.length > 200 ? `${s.slice(0, 200)}…` : s;
  } catch {
    return String(value);
  }
}

function PropertiesTable({ properties }: { properties: Record<string, unknown> }) {
  const entries = Object.entries(properties).filter(([k]) => !k.startsWith("_"));
  if (entries.length === 0) return <p className="text-sm text-fg-muted">This feature has no attributes.</p>;
  return (
    <Table className="text-xs">
      <TableCaption className="sr-only">Feature attributes</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">Attribute</TableHead>
          <TableHead scope="col">Value</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entries.map(([key, value]) => (
          <TableRow key={key}>
            <TableHead scope="row" className="max-w-32 truncate font-normal text-fg-muted">
              {key}
            </TableHead>
            <TableCell className="break-words">{formatPropertyValue(value)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function FeatureDetails({ datasetId, index }: { datasetId: string; index: number }) {
  const q = useFeature(datasetId, index);
  if (q.isError) {
    // A feature number that is not in the dataset (e.g. an edited link) is final: no ids, no Retry.
    if (isApiError(q.error) && q.error.status === 404) {
      return (
        <ErrorState
          compact
          error={q.error}
          title="Feature not found"
          message={`This dataset has no feature ${index}.`}
          fix="Pick a feature on the map or in the feature table."
        />
      );
    }
    return <ErrorState compact error={q.error} onRetry={() => void q.refetch()} />;
  }
  if (q.isLoading || !q.data) {
    return (
      <div className="space-y-3" aria-busy="true">
        <span className="sr-only">Loading feature {index}</span>
        <Skeleton className="h-6 w-3/4" />
        <SkeletonText lines={2} />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  const f: FeatureRecord = q.data;
  const m = f.measurement;
  const name = f.name?.trim() || `Feature ${f.index}`;
  return (
    <article aria-labelledby="inspector-feature-name" className="space-y-3">
      <header className="space-y-1">
        <h3 id="inspector-feature-name" className="break-words text-base font-semibold">
          {name}
        </h3>
        <div className="flex flex-wrap items-center gap-1.5">
          <MeasurementStatusBadge status={m.status} />
          {m.status !== "measured" ? <WhyPopover status={m.status} reason={m.reason} /> : null}
        </div>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 pt-1 text-xs">
          <dt className="text-fg-muted">Type</dt>
          <dd>{f.geometry_type}</dd>
          <dt className="text-fg-muted">Layer</dt>
          <dd className="truncate">{f.layer}</dd>
          <dt className="text-fg-muted">Source CRS</dt>
          <dd className="num">{f.crs ?? "unknown"}</dd>
          <dt className="text-fg-muted">Index</dt>
          <dd className="num">
            {f.index}
            {f.fid ? ` (fid ${f.fid})` : ""}
          </dd>
          {f.altitude_mode ? (
            <>
              <dt className="text-fg-muted">Altitude mode</dt>
              <dd>{f.altitude_mode}</dd>
            </>
          ) : null}
          {f.times && f.times.length > 0 ? (
            <>
              <dt className="text-fg-muted">Timestamps</dt>
              <dd className="num">{formatNumber(f.times.length)}</dd>
            </>
          ) : null}
        </dl>
      </header>
      <MeasurementCard m={m} geometryType={f.geometry_type} />
      <section aria-labelledby="inspector-attributes">
        <h3 id="inspector-attributes" className="mb-1 text-sm font-medium">
          Attributes
        </h3>
        <PropertiesTable properties={f.properties} />
      </section>
    </article>
  );
}

export interface InspectorProps {
  datasetId: string | null;
  file: FileInfo | null;
  features: DatasetFeatureCollection | null;
  module: ModuleDefinition;
  /** the URL analysis when it belongs to `module` */
  analysis: AnalysisInfo | null;
  selectedFeatures: number[];
  onAnalysisStarted: (analysis: AnalysisInfo) => void;
  /** move a selected feature to the front so its details are shown */
  onShowFeature: (index: number) => void;
  /** < 768 px or a short touch screen: no analysis forms */
  readOnly?: boolean;
  /** the dataset in the URL was not found or could not be processed */
  datasetUnavailable?: boolean;
}

/**
 * Right panel. Measure: the selected feature (name, type, CRS, measurement card, attributes)
 * plus the measure panel (totals and selection). Other modules: the module panel.
 */
export function Inspector({
  datasetId,
  file,
  features,
  module,
  analysis,
  selectedFeatures,
  onAnalysisStarted,
  onShowFeature,
  readOnly = false,
  datasetUnavailable = false,
}: InspectorProps) {
  const Panel = module.Panel;
  const panelProps = { datasetId, file, features, selectedFeatures, analysis, onAnalysisStarted };

  // Without its dataset a module has nothing to work on (a flight audit needs none).
  if (datasetUnavailable && module.id !== "flight-audit") {
    return (
      <EmptyState
        compact
        icon={<FileX />}
        title="No dataset"
        description="The dataset in this link is unavailable, so there is nothing to inspect. Open another dataset from your list."
      />
    );
  }

  if (module.id !== "measure") {
    if (readOnly) {
      return (
        <EmptyState
          compact
          icon={<Monitor />}
          title="Open on a larger screen"
          description={`${module.label} forms need a screen at least 768 px wide and 500 px tall. Results and the map still work here.`}
        />
      );
    }
    return <Panel {...panelProps} />;
  }

  const current = selectedFeatures[0] ?? null;
  const nameOf = (index: number) => {
    const f = features?.features.find((x) => x.properties._index === index);
    const n = f?.properties._name;
    return typeof n === "string" && n.trim() ? n.trim() : `Feature ${index}`;
  };

  return (
    <div className="space-y-4">
      {datasetId && current !== null ? (
        <>
          {selectedFeatures.length > 1 ? (
            <nav aria-label="Selected features" className="space-y-1">
              <p className="text-xs text-fg-muted">
                {formatNumber(selectedFeatures.length)} selected. Showing details for one:
              </p>
              <ul className="flex flex-wrap gap-1">
                {selectedFeatures.slice(0, 12).map((idx) => (
                  <li key={idx}>
                    <Button
                      size="sm"
                      variant={idx === current ? "secondary" : "ghost"}
                      aria-current={idx === current ? "true" : undefined}
                      className="h-7 max-w-40 px-2"
                      onClick={() => onShowFeature(idx)}
                    >
                      <span className="truncate">{nameOf(idx)}</span>
                    </Button>
                  </li>
                ))}
                {selectedFeatures.length > 12 ? (
                  <li className="self-center text-xs text-fg-muted">+{selectedFeatures.length - 12} more</li>
                ) : null}
              </ul>
            </nav>
          ) : null}
          <FeatureDetails datasetId={datasetId} index={current} />
          <Separator />
        </>
      ) : file?.status === "COMPLETED" ? (
        // Only once the dataset is ready: a missing, failed or processing one has nothing to select.
        <InputHint icon={<MousePointerClick />}>
          Select a feature on the map or in the table to see its measurements. With the map focused, press N to move
          between features and Enter to select.
        </InputHint>
      ) : null}
      <Panel {...panelProps} />
    </div>
  );
}
