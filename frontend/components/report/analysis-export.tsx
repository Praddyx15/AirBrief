"use client";

/**
 * The export menu of one completed analysis, used by the results drawer and the report page:
 * JSON (the envelope), GeoJSON (every result layer, H3 cells as polygons), KML (the evidence
 * layers, never a route) and CSV (one of the kind's tables, chosen from
 * `GET /api/analyses/{id}/export/tables/`). Every format carries "Evidence, not authorisation".
 *
 * In the static demo (no API) the JSON and GeoJSON are built in the browser from the analysis,
 * with the same layer names, H3 polygons and evidence labels as the API; KML and CSV need the API.
 */

import * as React from "react";
import { ChevronDown, Download, FileJson, Loader2, Map as MapIcon, Table2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { h3Collection } from "@/components/modules/h3-layers";
import { gridSquares } from "@/components/modules/ground-risk/volume";
import { EXPORT_ITEM_CLASS, ExportItem, ExportItemContent, useExportSaver, type ExportSaver } from "@/components/report/export-items";
import { DEMO_MODE, analysisExportUrl, describeError } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { ANALYSIS_EXPORT_LABEL, ed318ExportLabel } from "@/lib/labels";
import { useAnalysisTables, useMeta } from "@/lib/queries";
import { useSettings } from "@/lib/store";
import type { AnalysisInfo, AnalysisKind, Feature, FeatureCollection, Geometry } from "@/lib/types";
import { cn, downloadBlob } from "@/lib/utils";

/** Same text as `airbrief_core.models.brief.EVIDENCE_NOTICE`. */
export const EVIDENCE_NOTICE =
  "Evidence, not authorisation. AirBrief reports facts, bands and margins for planning; it does not approve, permit or clear any flight. Obtain the permissions your authority requires.";
/** Same text as `airbrief_core.models.brief.SYNTHETIC_WATERMARK`. */
export const SYNTHETIC_WATERMARK = "SYNTHETIC DEMO DATA — not evidence";

/** Kinds that read data-pack layers: only they can be affected by a synthetic pack. */
export const PACK_KINDS: ReadonlySet<AnalysisKind> = new Set([
  "airspace",
  "ground_risk",
  "evtol_siting",
  "evtol_corridor",
  "survey",
  "flight_audit",
  "environment",
  "line_of_sight",
]);

/** The watermark of an analysis that read a synthetic data pack, else null. */
export function analysisWatermark(analysis: AnalysisInfo): string | null {
  return analysis.datapack_synthetic && PACK_KINDS.has(analysis.kind) ? SYNTHETIC_WATERMARK : null;
}

type AnyCollection = FeatureCollection<Geometry | null, Record<string, unknown>>;

/** Layers never exported (as `api/services/exports.EXPORT_EXCLUDED_LAYERS`). */
const EXPORT_EXCLUDED_LAYERS: Partial<Record<AnalysisKind, readonly string[]>> = { survey: ["lines"] };

/** Features per GeoJSON export (as `api/services/exports.FEATURE_LIMIT`). */
const FEATURE_LIMIT = 50_000;

/**
 * The H3 results (and the ground-risk square grid) of an analysis as polygon layers, named as in
 * the API's GeoJSON export (`api/services/exports.H3_LAYERS`).
 */
export function cellLayers(analysis: AnalysisInfo): Record<string, AnyCollection> {
  const r = analysis.status === "COMPLETED" ? (analysis.result as unknown as Record<string, unknown> | null) : null;
  if (!r) return {};
  const cells = (value: unknown): { h3: string }[] =>
    Array.isArray(value) ? value.filter((c): c is { h3: string } => typeof c === "object" && c !== null && typeof (c as { h3?: unknown }).h3 === "string") : [];
  const values = (c: { h3: string }) => Object.fromEntries(Object.entries(c).filter(([k]) => k !== "h3" && k !== "lon" && k !== "lat"));
  const out: Record<string, AnyCollection> = {};
  switch (analysis.kind) {
    case "airspace":
      out.grid = h3Collection(cells(r.grid), values);
      break;
    case "ground_risk": {
      const grid = r.density_grid as { cells?: unknown; hotspots?: unknown; grid_m?: unknown } | null | undefined;
      if (grid && Array.isArray(grid.cells) && typeof grid.grid_m === "number") {
        out.density = gridSquares(grid.cells, grid.grid_m) as unknown as AnyCollection;
        out.hotspots = gridSquares(Array.isArray(grid.hotspots) ? grid.hotspots : [], grid.grid_m, true) as unknown as AnyCollection;
      } else {
        out.density = h3Collection(cells(r.cells), values);
        out.hotspots = h3Collection(cells(r.hotspots), (c, i) => ({ rank: i + 1, ...values(c) }));
      }
      break;
    }
    case "evtol_siting":
      out.cells = h3Collection(cells(r.cells), values);
      break;
    case "evtol_corridor":
      out.noise = h3Collection(cells((r.noise as { cells?: unknown } | null)?.cells), values);
      break;
    default:
      break;
  }
  return out;
}

/**
 * The GeoJSON export built in the browser (static demo): every result layer, then the cell
 * layers, each feature tagged with `_layer`, at most 50,000 features, the evidence labels and
 * the cut in the `airbrief` member — the shape of the API's export.
 */
export function analysisGeojson(analysis: AnalysisInfo, name: string): Record<string, unknown> {
  const own = (analysis.result?.layers ?? {}) as Record<string, AnyCollection>;
  // As the API: the survey coverage lines are a visual aid, never exported as a route.
  const excluded = EXPORT_EXCLUDED_LAYERS[analysis.kind] ?? [];
  const layers: Record<string, AnyCollection> = Object.fromEntries(Object.entries(own).filter(([key]) => !excluded.includes(key)));
  for (const [key, fc] of Object.entries(cellLayers(analysis))) if (!(key in layers)) layers[key] = fc;
  const features: Feature<Geometry | null, Record<string, unknown>>[] = [];
  const truncated: Record<string, number> = {};
  for (const [layer, fc] of Object.entries(layers)) {
    const all = fc?.features ?? [];
    const room = Math.max(0, FEATURE_LIMIT - features.length);
    all.slice(0, room).forEach((f) => features.push({ ...f, properties: { ...(f.properties ?? {}), _layer: layer } }));
    if (all.length > room) truncated[layer] = all.length - room;
  }
  return {
    type: "FeatureCollection",
    name,
    features,
    airbrief: {
      kind: analysis.kind,
      analysis_id: analysis.id,
      notice: EVIDENCE_NOTICE,
      watermark: analysisWatermark(analysis),
      datapack: analysis.datapack,
      datapack_synthetic: analysis.datapack_synthetic,
      feature_limit: FEATURE_LIMIT,
      feature_count: features.length,
      truncated: Object.keys(truncated).length > 0,
      truncated_layers: truncated,
    },
  };
}

/**
 * The JSON export built in the browser: the analysis envelope (`AnalysisEnvelope`, same fields and
 * order as the API's) with its evidence labels. `versions` is `Meta` (its `version` and
 * `schema_version`); without it the versions are unknown (null).
 */
export function analysisEnvelope(
  analysis: AnalysisInfo,
  versions: { version: string; schema_version: string } | null | undefined,
): Record<string, unknown> {
  return {
    schema_version: versions?.schema_version ?? null,
    airbrief_version: versions?.version ?? null,
    kind: analysis.kind,
    params: analysis.params,
    result: analysis.result,
    datapack: analysis.datapack,
    datapack_synthetic: analysis.datapack_synthetic,
    backend: analysis.backend,
    started_utc: analysis.started_at,
    finished_utc: analysis.finished_at,
    duration_ms: analysis.duration_ms,
    notice: EVIDENCE_NOTICE,
    watermark: analysisWatermark(analysis),
  };
}

/**
 * One download per CSV table of the analysis (API only): list items for an export menu. `saver` is
 * the menu's, so these and its other entries wait for each other.
 */
export function AnalysisCsvItems({ analysis, base, saver }: { analysis: AnalysisInfo; base: string; saver: ExportSaver }) {
  const tables = useAnalysisTables(analysis.id);
  if (tables.isPending) {
    return (
      <li className="flex items-center gap-2 px-2 py-1.5 text-xs text-fg-muted" role="status">
        <Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />
        Loading the CSV tables…
      </li>
    );
  }
  if (tables.isError) {
    return (
      <li className="px-2 py-1.5 text-xs text-danger" role="alert">
        Could not list the CSV tables: {describeError(tables.error).message}
      </li>
    );
  }
  return (
    <>
      {tables.data.map((t) => {
        const href = analysisExportUrl(analysis.id, "csv", { table: t.default ? null : t.key });
        if (!href) return null;
        return (
          <li key={t.key}>
            <ExportItem
              saver={saver}
              id={`csv-${t.key}`}
              href={href}
              name={`${base}${t.default ? "" : `_${t.key}`}.csv`}
              icon={<Table2 aria-hidden className="size-4 text-fg-muted" />}
              label={`CSV: ${t.label}`}
              description={`${formatNumber(t.rows)} row${t.rows === 1 ? "" : "s"}${t.multi ? ", a leading “table” column names each row’s table" : ""}${t.default ? " · the default table" : ""}`}
            />
          </li>
        );
      })}
    </>
  );
}

export interface AnalysisExportMenuProps {
  analysis: AnalysisInfo;
  /** download name without extension (demo exports) */
  baseName: string;
  /** label next to the icon (default "Export") */
  label?: string;
  size?: "sm" | "default";
  variant?: "secondary" | "ghost" | "outline";
  className?: string;
  align?: "start" | "end";
}

/** "Export" popover with every format of one completed analysis. */
export function AnalysisExportMenu({
  analysis,
  baseName,
  label = "Export",
  size = "sm",
  variant = "secondary",
  className,
  align = "end",
}: AnalysisExportMenuProps) {
  const [open, setOpen] = React.useState(false);
  const meta = useMeta().data;
  const ed318Limits = useSettings((s) => s.ed318Limits);
  const saver = useExportSaver();
  if (analysis.status !== "COMPLETED") return null;
  const base = `${baseName}-${analysis.kind}`;
  const demoNote = "Needs the AirBrief API: not available in the read-only demo";
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size={size} variant={variant} className={className}>
          <Download aria-hidden />
          {label}
          <ChevronDown aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent align={align} className="max-h-[min(70vh,32rem)] w-80 overflow-y-auto p-1">
        <ul aria-label="Analysis export formats" className="flex flex-col">
          {DEMO_MODE ? (
            <>
              <li>
                <ExportItem
                  saver={saver}
                  id="json"
                  name={`${base}.json`}
                  build={() => downloadBlob(JSON.stringify(analysisEnvelope(analysis, meta), null, 2), `${base}.json`, "application/json")}
                  icon={<FileJson aria-hidden className="size-4 text-fg-muted" />}
                  {...ANALYSIS_EXPORT_LABEL.json}
                />
              </li>
              <li>
                <ExportItem
                  saver={saver}
                  id="geojson"
                  name={`${base}.geojson`}
                  build={() => downloadBlob(JSON.stringify(analysisGeojson(analysis, base)), `${base}.geojson`, "application/geo+json")}
                  icon={<MapIcon aria-hidden className="size-4 text-fg-muted" />}
                  {...ANALYSIS_EXPORT_LABEL.geojson}
                />
              </li>
              {(["kml", "csv"] as const).map((fmt) => (
                <li key={fmt}>
                  <button type="button" className={EXPORT_ITEM_CLASS} aria-disabled="true" onClick={(e) => e.preventDefault()}>
                    <ExportItemContent
                      icon={fmt === "csv" ? <Table2 aria-hidden className="size-4 text-fg-muted" /> : <MapIcon aria-hidden className="size-4 text-fg-muted" />}
                      label={ANALYSIS_EXPORT_LABEL[fmt].label}
                      description={demoNote}
                    />
                  </button>
                </li>
              ))}
            </>
          ) : (
            <>
              {/* a per-feature run (and an audit with no zone near it) has no zones layer to export */}
              {((analysis.kind === "airspace" || analysis.kind === "flight_audit") && analysis.result?.layers?.zones
                ? (["json", "geojson", "kml", "ed318"] as const)
                : (["json", "geojson", "kml"] as const)
              ).map((fmt) => {
                const href = analysisExportUrl(analysis.id, fmt, { ed318Limits });
                return href ? (
                  <li key={fmt}>
                    <ExportItem
                      saver={saver}
                      id={fmt}
                      href={href}
                      name={fmt === "ed318" ? `${base}_zones.ed318.geojson` : `${base}.${fmt}`}
                      icon={fmt === "json" ? <FileJson aria-hidden className="size-4 text-fg-muted" /> : <MapIcon aria-hidden className="size-4 text-fg-muted" />}
                      {...(fmt === "ed318" ? ed318ExportLabel(ANALYSIS_EXPORT_LABEL.ed318, ed318Limits) : ANALYSIS_EXPORT_LABEL[fmt])}
                    />
                  </li>
                ) : null;
              })}
              <li className="mt-1 border-t border-border px-2 pb-0.5 pt-1.5 text-xs font-medium text-fg-muted">CSV tables</li>
              {open ? <AnalysisCsvItems analysis={analysis} base={base} saver={saver} /> : null}
            </>
          )}
        </ul>
        <p className={cn("border-t border-border px-2 pb-1 pt-1.5 text-xs text-fg-muted")}>
          Every file carries “Evidence, not authorisation”{analysisWatermark(analysis) ? " and the synthetic-data watermark" : ""}.
        </p>
      </PopoverContent>
    </Popover>
  );
}
