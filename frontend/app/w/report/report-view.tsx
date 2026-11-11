"use client";

/**
 * `/w/report`: the printable report of a dataset and / or an analysis (`?d=&a=`, at least one),
 * or the operation brief (`?mode=brief`, brief-view.tsx). Interactive chrome carries
 * `data-print="hide"`.
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Download,
  FileQuestion,
  FileStack,
  FileText,
  Loader2,
  Printer,
  Wrench,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { KpiGrid, KpiTile } from "@/components/ui/kpi-tile";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";
import { Skeleton, SkeletonText } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AnalysisSummary, IssueList } from "@/components/report/analysis-summary";
import { AnalysisCsvItems, PACK_KINDS, analysisEnvelope, analysisGeojson } from "@/components/report/analysis-export";
import { BRIEF_MODE, briefHref } from "@/components/report/brief-href";
import { ExportItem, useExportSaver } from "@/components/report/export-items";
import { GeometrySvg } from "@/components/report/geometry-svg";
import { MAX_LAYER_FEATURES, buildMapLayers } from "@/components/report/map-layers";
import { DATA_ATTRIBUTIONS, DISCLAIMER } from "@/components/layout/site-footer";
import { FileStatusBadge } from "@/components/upload/recent-datasets";
import { analysisKindLabel } from "@/components/workspace/analysis-runner";
import {
  ApiError,
  DEMO_MODE,
  analysisExportUrl,
  fileExportUrl,
  getMeasurements,
} from "@/lib/api";
import { MEASUREMENT_STATUS_LABEL } from "@/lib/colors";
import {
  areaParts,
  formatArea,
  formatBytes,
  formatDate,
  formatDuration,
  formatLength,
  formatNumber,
  formatPercent,
  lengthParts,
} from "@/lib/format";
import { measurementsToCsv } from "@/lib/csv";
import { ANALYSIS_EXPORT_LABEL, FILE_EXPORT_LABEL, crsSourceLabel, fileTypeLabel } from "@/lib/labels";
import { moduleForKind } from "@/lib/modules";
import { queryKeys, useAnalyses, useAnalysis, useFeatures, useFile, useMeta, useMyDatasetAnalysisIds } from "@/lib/queries";
import { useUnits, type Units } from "@/lib/store";
import type {
  AnalysisInfo,
  DatasetFeatureCollection,
  FileInfo,
  MeasurementItem,
  MeasurementTotals,
} from "@/lib/types";
import { isAnalysisActive } from "@/lib/types";
import { useWorkspaceParams, workspaceHref } from "@/lib/url-state";
import { cn, downloadBlob } from "@/lib/utils";
import { BriefView } from "./brief-view";

const MEASUREMENT_PAGE = 100;
const MEASUREMENT_MAX = 2000;

interface AllMeasurements {
  items: MeasurementItem[];
  total: number;
  totals: MeasurementTotals | null;
}

/** Every measurement of a dataset (paged requests), capped at MEASUREMENT_MAX rows. */
function useAllMeasurements(id: string | null, enabled: boolean) {
  return useQuery<AllMeasurements, ApiError>({
    queryKey: [...queryKeys.measurements(id ?? "", { limit: MEASUREMENT_PAGE }), "all"],
    queryFn: async ({ signal }) => {
      const items: MeasurementItem[] = [];
      let total = Number.POSITIVE_INFINITY;
      let totals: MeasurementTotals | null = null;
      let offset = 0;
      while (offset < total && items.length < MEASUREMENT_MAX) {
        const page = await getMeasurements(id as string, { limit: MEASUREMENT_PAGE, offset }, signal);
        items.push(...page.items);
        total = page.total;
        totals = page.totals;
        if (page.items.length === 0) break;
        offset += page.items.length;
      }
      return { items, total: Number.isFinite(total) ? total : items.length, totals };
    },
    enabled: Boolean(id) && enabled,
    staleTime: Infinity,
  });
}

function baseName(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return (dot > 0 ? filename.slice(0, dot) : filename).replace(/[^\w.-]+/g, "_");
}

interface ExportEntry {
  key: string;
  label: string;
  description: string;
  /** the saved file's name (with an API link, only if the API gives none) */
  name: string;
  href?: string | null;
  /** built in the browser instead (the demo); left out, the entry is shown disabled */
  build?: () => void;
}

/** Dataset and analysis downloads in one menu, named as in the workspace Export menu. */
function ExportMenu({
  file,
  analysis,
  features,
  measurements,
}: {
  file: FileInfo | null;
  analysis: AnalysisInfo | null;
  features: DatasetFeatureCollection | null;
  measurements: readonly MeasurementItem[] | null;
}) {
  const [open, setOpen] = useState(false);
  const meta = useMeta().data;
  const saver = useExportSaver();
  const name = file ? baseName(file.filename) : "analysis";
  const entries: ExportEntry[] = [];

  if (file?.status === "COMPLETED") {
    if (DEMO_MODE) {
      entries.push(
        {
          key: "file-csv",
          ...FILE_EXPORT_LABEL.csv,
          name: `${name}-measurements.csv`,
          build: measurements ? () => downloadBlob(measurementsToCsv(measurements), `${name}-measurements.csv`, "text/csv") : undefined,
        },
        {
          key: "file-geojson",
          ...FILE_EXPORT_LABEL.geojson,
          name: `${name}.geojson`,
          build: features ? () => downloadBlob(JSON.stringify(features), `${name}.geojson`, "application/geo+json") : undefined,
        },
      );
    } else {
      (["csv", "geojson", "kml"] as const).forEach((fmt) => {
        const href = fileExportUrl(file.id, fmt);
        if (href) entries.push({ key: `file-${fmt}`, ...FILE_EXPORT_LABEL[fmt], name: fmt === "csv" ? `${name}-measurements.csv` : `${name}.${fmt}`, href });
      });
    }
  }

  const completedAnalysis = analysis?.status === "COMPLETED" ? analysis : null;
  if (completedAnalysis) {
    const base = `${name}-${completedAnalysis.kind}`;
    if (DEMO_MODE) {
      // Built in the browser with the API's shape: the envelope, and every layer with H3 cells as polygons.
      entries.push(
        {
          key: "analysis-json",
          ...ANALYSIS_EXPORT_LABEL.json,
          name: `${base}.json`,
          build: () => downloadBlob(JSON.stringify(analysisEnvelope(completedAnalysis, meta), null, 2), `${base}.json`, "application/json"),
        },
        {
          key: "analysis-geojson",
          ...ANALYSIS_EXPORT_LABEL.geojson,
          name: `${base}.geojson`,
          build: () => downloadBlob(JSON.stringify(analysisGeojson(completedAnalysis, base)), `${base}.geojson`, "application/geo+json"),
        },
        {
          key: "analysis-kml",
          ...ANALYSIS_EXPORT_LABEL.kml,
          description: "Needs the AirBrief API: not available in the read-only demo",
          name: `${base}.kml`,
        },
      );
    } else {
      (["json", "geojson", "kml"] as const).forEach((fmt) => {
        const href = analysisExportUrl(completedAnalysis.id, fmt);
        if (href) entries.push({ key: `analysis-${fmt}`, ...ANALYSIS_EXPORT_LABEL[fmt], name: `${base}.${fmt}`, href });
      });
    }
  }

  if (entries.length === 0) return null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="sm" variant="secondary">
          <Download aria-hidden />
          Export
          <ChevronDown aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="max-h-[min(75vh,36rem)] w-80 overflow-y-auto p-1">
        <ul aria-label="Export formats" className="flex flex-col">
          {entries.map((entry) => (
            <li key={entry.key}>
              <ExportItem
                saver={saver}
                id={entry.key}
                href={entry.href}
                build={entry.build}
                name={entry.name}
                icon={<Download aria-hidden className="size-4 text-fg-muted" />}
                label={entry.label}
                description={entry.description}
              />
            </li>
          ))}
          {completedAnalysis && !DEMO_MODE ? (
            <>
              <li className="mt-1 border-t border-border px-2 pb-0.5 pt-1.5 text-xs font-medium text-fg-muted">Analysis CSV tables</li>
              {open ? <AnalysisCsvItems analysis={completedAnalysis} base={`${name}-${completedAnalysis.kind}`} saver={saver} /> : null}
            </>
          ) : null}
          {completedAnalysis ? (
            <li className="mt-1 border-t border-border pt-1">
              <Link
                href={briefHref(file?.id ?? completedAnalysis.dataset_id, completedAnalysis.id)}
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm font-medium text-fg no-underline hover:bg-surface pointer-coarse:min-h-11"
              >
                <FileStack aria-hidden className="size-4 text-fg-muted" />
                Create brief
              </Link>
            </li>
          ) : null}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

function DatasetSummary({ file }: { file: FileInfo }) {
  const units = useUnits();
  const t = file.totals;
  const source = crsSourceLabel(file.crs_source);
  return (
    <div className="space-y-3">
      <KpiGrid label="Dataset summary">
        <KpiTile label="Features" value={formatNumber(file.feature_count)} explanation={`${file.layer_count} layer${file.layer_count === 1 ? "" : "s"}`} />
        <KpiTile
          label="Coordinate system"
          value={file.crs ?? "Unknown"}
          explanation={source ? `From ${source}` : "Not detected"}
          tone={file.crs ? "neutral" : "warn"}
          toneLabel="Features not measured"
        />
        <KpiTile label="File" value={fileTypeLabel(file.file_type) ?? "—"} explanation={formatBytes(file.size_bytes)} />
        <KpiTile label="Measured" value={formatNumber(t?.measured ?? null)} explanation={t ? `${t.not_applicable} not applicable, ${t.unsupported + t.skipped + t.error} not measured` : undefined} />
        <KpiTile label="Total area" value={areaParts(t?.area_m2 ?? null, units)} explanation={t ? `Perimeter ${formatLength(t.perimeter_m, units)}` : undefined} />
        <KpiTile label="Total length" value={lengthParts(t?.length_m ?? null, units)} explanation={t && t.length_3d_m > 0 ? `3D ${formatLength(t.length_3d_m, units)}` : undefined} />
      </KpiGrid>
      {file.layers.length > 0 ? (
        <p className="text-sm text-fg-muted">
          <span className="font-medium text-fg">Layers: </span>
          {file.layers.map((l) => `${l.name} (${l.feature_count})`).join(", ")}
        </p>
      ) : null}
      {file.issues.length > 0 ? <IssueList issues={file.issues} label="Dataset issues" /> : null}
    </div>
  );
}

function StatusCell({ item }: { item: MeasurementItem }) {
  const m = item.measurement;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {MEASUREMENT_STATUS_LABEL[m.status]}
      {m.repaired ? (
        <Badge variant="warn">
          <Wrench aria-hidden />
          Repaired
        </Badge>
      ) : null}
    </span>
  );
}

/** Below 768 px (screen only): one card per feature, with the numbers first (PLAN: tables become cards). */
function MeasurementCards({ items, totals, units }: { items: readonly MeasurementItem[]; totals: MeasurementTotals | null; units: Units }) {
  return (
    <div className="space-y-2 md:hidden print:hidden">
      <ul className="space-y-2" aria-label="Measurements per feature">
        {items.map((i) => {
          const m = i.measurement;
          const values: [string, string][] = [];
          if (m.area_m2 !== null) values.push(["Area", formatArea(m.area_m2, units)]);
          if (m.perimeter_m !== null) values.push(["Perimeter", formatLength(m.perimeter_m, units)]);
          if (m.length_m !== null) values.push(["Length", formatLength(m.length_m, units)]);
          if (m.length_3d_m !== null) values.push(["3D length", formatLength(m.length_3d_m, units)]);
          if (m.deviation_pct !== null) values.push(["Geodesic Δ", formatPercent(m.deviation_pct, { maximumFractionDigits: 4 })]);
          return (
            <li key={i.feature_index} className="rounded border border-border bg-surface p-3 text-xs">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <p className="min-w-0 break-words text-sm font-medium">
                  <span className="num mr-1.5 text-xs font-normal text-fg-muted">#{i.feature_index}</span>
                  {i.name ?? "Unnamed"}
                </p>
                <StatusCell item={i} />
              </div>
              {m.reason ? <p className="mt-1 text-fg-muted">{m.reason}</p> : null}
              <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5">
                {values.map(([label, value]) => (
                  <div key={label} className="min-w-0">
                    <dt className="text-fg-muted">{label}</dt>
                    <dd className="num break-words text-fg">{value}</dd>
                  </div>
                ))}
                <div className="min-w-0">
                  <dt className="text-fg-muted">Type</dt>
                  <dd className="break-words text-fg">{i.geometry_type}</dd>
                </div>
                <div className="min-w-0">
                  <dt className="text-fg-muted">Layer</dt>
                  <dd className="break-words text-fg">{i.layer}</dd>
                </div>
              </dl>
            </li>
          );
        })}
      </ul>
      {totals ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 rounded border border-border bg-elevated p-3 text-xs">
          <div className="col-span-2 font-medium text-fg">
            <dt className="inline">Totals: </dt>
            <dd className="inline">
              {formatNumber(totals.measured)} measured of {formatNumber(totals.feature_count)}
            </dd>
          </div>
          {(
            [
              ["Area", formatArea(totals.area_m2, units)],
              ["Perimeter", formatLength(totals.perimeter_m, units)],
              ["Length", formatLength(totals.length_m, units)],
              ["3D length", formatLength(totals.length_3d_m, units)],
            ] as const
          ).map(([label, value]) => (
            <div key={label} className="min-w-0">
              <dt className="text-fg-muted">{label}</dt>
              <dd className="num break-words text-fg">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}

function MeasurementsTable({ data }: { data: AllMeasurements }) {
  const units = useUnits();
  const { items, total, totals } = data;
  return (
    <div className="space-y-1">
      <MeasurementCards items={items} totals={totals} units={units} />
      <Table className="text-xs" containerClassName="hidden md:block print:block">
        <caption className="sr-only">Measurements per feature</caption>
        <TableHeader>
          <TableRow>
            <TableHead className="text-right">#</TableHead>
            <TableHead>Name</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Area</TableHead>
            <TableHead className="text-right">Perimeter</TableHead>
            <TableHead className="text-right">Length</TableHead>
            <TableHead className="text-right">3D length</TableHead>
            <TableHead className="text-right">Geodesic Δ</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Layer</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((i) => {
            const m = i.measurement;
            return (
              <TableRow key={i.feature_index}>
                <TableCell className="num text-right">{i.feature_index}</TableCell>
                <TableCell className="max-w-[16rem]">
                  <span className="block truncate">{i.name ?? "Unnamed"}</span>
                  {m.reason ? <span className="block text-fg-muted">{m.reason}</span> : null}
                </TableCell>
                <TableCell>
                  <StatusCell item={i} />
                </TableCell>
                <TableCell className="num whitespace-nowrap text-right">{formatArea(m.area_m2, units)}</TableCell>
                <TableCell className="num whitespace-nowrap text-right">{formatLength(m.perimeter_m, units)}</TableCell>
                <TableCell className="num whitespace-nowrap text-right">{formatLength(m.length_m, units)}</TableCell>
                <TableCell className="num whitespace-nowrap text-right">{formatLength(m.length_3d_m, units)}</TableCell>
                <TableCell className="num whitespace-nowrap text-right">
                  {formatPercent(m.deviation_pct, { maximumFractionDigits: 4 })}
                </TableCell>
                <TableCell>{i.geometry_type}</TableCell>
                <TableCell className="max-w-[10rem] truncate">{i.layer}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
        {totals ? (
          <TableFooter>
            <TableRow>
              <TableHead scope="row" colSpan={3} className="text-fg">
                Totals ({formatNumber(totals.measured)} measured of {formatNumber(totals.feature_count)})
              </TableHead>
              <TableCell className="num whitespace-nowrap text-right">{formatArea(totals.area_m2, units)}</TableCell>
              <TableCell className="num whitespace-nowrap text-right">{formatLength(totals.perimeter_m, units)}</TableCell>
              <TableCell className="num whitespace-nowrap text-right">{formatLength(totals.length_m, units)}</TableCell>
              <TableCell className="num whitespace-nowrap text-right">{formatLength(totals.length_3d_m, units)}</TableCell>
              <TableCell colSpan={3} />
            </TableRow>
          </TableFooter>
        ) : null}
      </Table>
      {total > items.length ? (
        <p className="text-xs text-fg-muted">
          Showing the first {formatNumber(items.length)} of {formatNumber(total)} features. Export the CSV for all of
          them.
        </p>
      ) : null}
      <p className="text-xs text-fg-muted">
        Areas and lengths are computed in a local equal-area / transverse-Mercator projection per feature; Geodesic Δ
        is the difference from the ellipsoidal (geodesic) value.
      </p>
    </div>
  );
}

function AnalysisSection({ analysis, datasetId }: { analysis: AnalysisInfo; datasetId: string | null }) {
  const units = useUnits();
  const active = isAnalysisActive(analysis.status);
  return (
    <section aria-labelledby="report-analysis" className="print-break-before space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="report-analysis" className="text-lg font-semibold">
          {analysisKindLabel(analysis.kind)}
        </h2>
        <p className="num text-xs text-fg-muted">
          {analysis.finished_at ? `Finished ${formatDate(analysis.finished_at, { withTime: true })}` : `Created ${formatDate(analysis.created_at, { withTime: true })}`}
          {analysis.duration_ms !== null ? ` · ${formatDuration(analysis.duration_ms / 1000)}` : ""}
          {analysis.datapack && PACK_KINDS.has(analysis.kind) ? ` · data pack ${analysis.datapack}` : ""}
          {analysis.backend ? ` · ${analysis.backend === "cpp" ? "native kernel" : "Python"}` : ""}
        </p>
      </div>

      {datasetId && analysis.dataset_id && analysis.dataset_id !== datasetId ? (
        <Alert variant="warn">
          <AlertTitle>Different dataset</AlertTitle>
          <AlertDescription>This analysis was run on another dataset than the one shown above.</AlertDescription>
        </Alert>
      ) : null}

      {analysis.datapack_synthetic && PACK_KINDS.has(analysis.kind) ? (
        <Alert variant="warn">
          <AlertTitle>Synthetic data</AlertTitle>
          <AlertDescription>
            Computed with the synthetic demo data pack: terrain, population and obstacles are invented for testing.
          </AlertDescription>
        </Alert>
      ) : null}

      {active ? (
        <div className="space-y-2 rounded border border-border p-4" aria-busy="true">
          <p className="flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin text-accent motion-reduce:animate-none" aria-hidden />
            {analysis.status === "PENDING" ? "Queued" : `Running${analysis.stage ? `: ${analysis.stage}` : ""}`}
          </p>
          <Progress value={analysis.progress * 100} label="Analysis progress" />
          <p className="text-xs text-fg-muted">This page updates when the analysis finishes.</p>
        </div>
      ) : analysis.status === "FAILED" || analysis.status === "CANCELLED" ? (
        <ErrorState error={analysis.error ?? { code: "cancelled", message: "The analysis was cancelled." }} />
      ) : (
        <AnalysisSummary analysis={analysis} units={units} />
      )}
    </section>
  );
}

function AnalysesPicker({ datasetId }: { datasetId: string }) {
  const mine = useMyDatasetAnalysisIds(datasetId);
  const analyses = useAnalyses(datasetId, { ids: mine });
  const items = analyses.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <section aria-labelledby="report-analyses" className="space-y-2" data-print="hide">
      <h2 id="report-analyses" className="text-lg font-semibold">
        Add an analysis to this report
      </h2>
      <ul className="divide-y divide-border rounded border border-border">
        {items.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
            <span className="flex items-center gap-2">
              {a.status === "COMPLETED" ? <CheckCircle2 className="size-4 text-ok" aria-hidden /> : null}
              <span className="font-medium">{analysisKindLabel(a.kind)}</span>
              <span className="num text-xs text-fg-muted">{formatDate(a.created_at, { withTime: true })}</span>
            </span>
            {a.status === "COMPLETED" ? (
              <Link
                href={workspaceHref({ datasetId, analysisId: a.id }, "/w/report")}
                className="text-accent-text underline-offset-4 hover:underline"
              >
                Include
                <span className="sr-only">
                  {" "}
                  {analysisKindLabel(a.kind)} from {formatDate(a.created_at, { withTime: true })}
                </span>
              </Link>
            ) : (
              <span className="text-xs text-fg-muted">{a.status.toLowerCase()}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Each mode mounts only while shown, so the report's queries do not run behind the brief. */
export function ReportView() {
  const { datasetId: datasetParam, analysisId } = useWorkspaceParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const mode = searchParams.get("mode") === BRIEF_MODE ? "brief" : "report";
  const analysis = useAnalysis(analysisId);
  const datasetId = datasetParam ?? analysis.data?.dataset_id ?? null;
  const backHref = workspaceHref({
    datasetId,
    analysisId,
    module: analysis.data ? moduleForKind(analysis.data.kind) : null,
  });

  const setMode = (next: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "brief") params.set("mode", BRIEF_MODE);
    else params.delete("mode");
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  return (
    <Tabs value={mode} onValueChange={setMode} className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2" data-print="hide">
        <Button variant="ghost" size="sm" asChild>
          <Link href={backHref}>
            <ArrowLeft aria-hidden />
            Back to the workspace
          </Link>
        </Button>
        <TabsList aria-label="Report or operation brief">
          <TabsTrigger value="report">
            <FileText aria-hidden />
            Report
          </TabsTrigger>
          <TabsTrigger value="brief">
            <FileStack aria-hidden />
            Brief
          </TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="report" className="mt-0">
        <ReportBody datasetParam={datasetParam} analysisId={analysisId} />
      </TabsContent>
      {/* No margin on paper: the brief's one-page fit is measured for the sheet alone. */}
      <TabsContent value="brief" className="mt-0 print:!mt-0">
        <BriefView datasetId={datasetId} analysisId={analysisId} />
      </TabsContent>
    </Tabs>
  );
}

function ReportBody({ datasetParam, analysisId }: { datasetParam: string | null; analysisId: string | null }) {
  const units = useUnits();
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);

  const analysis = useAnalysis(analysisId);
  const datasetId = datasetParam ?? analysis.data?.dataset_id ?? null;
  const file = useFile(datasetId);
  const completed = file.data?.status === "COMPLETED";
  const features = useFeatures(datasetId, { enabled: completed });
  const measurements = useAllMeasurements(datasetId, completed);

  useEffect(() => {
    setGeneratedAt(formatDate(new Date(), { withTime: true }));
  }, []);

  const { layers: mapLayers, skipped } = useMemo(
    () => buildMapLayers(features.data ?? null, analysis.data ?? null),
    [features.data, analysis.data],
  );

  if (!datasetParam && !analysisId) {
    return (
      <EmptyState
        icon={<FileQuestion />}
        title="Nothing to report yet"
        headingLevel="h1"
        description="Open a dataset in the workspace and choose Report, or pick a recent dataset. A brief can combine analyses from your history."
        action={
          <div className="flex flex-wrap justify-center gap-2">
            <Button asChild>
              <Link href="/w">Go to the workspace</Link>
            </Button>
            <Button asChild variant="secondary">
              <Link href={briefHref()}>Create a brief</Link>
            </Button>
          </div>
        }
      />
    );
  }

  const fileInfo = file.data ?? null;
  const analysisInfo = analysis.data ?? null;
  const title = fileInfo?.filename ?? (analysisInfo ? analysisKindLabel(analysisInfo.kind) : "Report");

  return (
    <article className="space-y-8" aria-labelledby="report-title">
      <div className="flex flex-wrap items-center justify-end gap-2" data-print="hide">
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => window.print()}>
            <Printer aria-hidden />
            Print
          </Button>
          <ExportMenu
            file={fileInfo}
            analysis={analysisInfo}
            features={features.data ?? null}
            measurements={measurements.data?.items ?? null}
          />
        </div>
      </div>

      <header className="space-y-2 border-b border-border pb-4">
        <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-fg-muted">
          <FileText className="size-3.5" aria-hidden />
          AirBrief report
        </p>
        <h1 id="report-title" className="break-words text-2xl font-semibold tracking-tight">
          {file.isPending && datasetId ? "Loading report…" : title}
        </h1>
        <p className="num flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
          {generatedAt ? <span>Generated {generatedAt}</span> : null}
          {datasetId ? <span className="break-all">Dataset {datasetId}</span> : null}
          {analysisId ? <span className="break-all">Analysis {analysisId}</span> : null}
          <span>Units: {units}</span>
        </p>
      </header>

      {datasetId ? (
        <section aria-labelledby="report-dataset" className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="report-dataset" className="text-lg font-semibold">
              Dataset
            </h2>
            {fileInfo ? <FileStatusBadge status={fileInfo.status} file={fileInfo} /> : null}
          </div>
          {file.isPending ? (
            <div aria-busy="true">
              <span className="sr-only" role="status">
                Loading the dataset…
              </span>
              <SkeletonText lines={4} />
            </div>
          ) : file.isError ? (
            <ErrorState
              error={file.error}
              onRetry={() => void file.refetch()}
              retrying={file.isFetching}
              actions={
                <Button size="sm" variant="ghost" asChild>
                  <Link href="/w">Back to datasets</Link>
                </Button>
              }
            />
          ) : fileInfo?.status === "FAILED" ? (
            <ErrorState error={fileInfo.error ?? "Processing failed."} title={`Could not process ${fileInfo.filename}`} />
          ) : fileInfo && !completed ? (
            <Alert variant="info" icon={<Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden />}>
              <AlertTitle>Still processing</AlertTitle>
              <AlertDescription>The report fills in when processing finishes.</AlertDescription>
            </Alert>
          ) : fileInfo ? (
            <DatasetSummary file={fileInfo} />
          ) : null}
        </section>
      ) : null}

      {(datasetId && completed) || analysisInfo?.status === "COMPLETED" ? (
        <section aria-labelledby="report-map" className="space-y-3">
          <h2 id="report-map" className="text-lg font-semibold">
            Overview map
          </h2>
          {features.isPending && completed ? (
            <Skeleton className="aspect-[16/9] w-full" />
          ) : features.isError ? (
            <ErrorState error={features.error} onRetry={() => void features.refetch()} compact />
          ) : (
            <GeometrySvg
              layers={mapLayers}
              units={units}
              title={`Overview map of ${title}${analysisInfo ? ` with ${analysisKindLabel(analysisInfo.kind).toLowerCase()} layers` : ""}`}
            />
          )}
          {skipped.length > 0 ? (
            <p className="text-xs text-fg-muted">
              Not drawn (more than {formatNumber(MAX_LAYER_FEATURES)} features): {skipped.join(", ")}. They are included
              in the GeoJSON export.
            </p>
          ) : null}
        </section>
      ) : null}

      {datasetId && completed ? (
        <section aria-labelledby="report-measurements" className="space-y-3">
          <h2 id="report-measurements" className="text-lg font-semibold">
            Measurements
          </h2>
          {measurements.isPending ? (
            <div aria-busy="true">
              <span className="sr-only" role="status">
                Loading measurements…
              </span>
              <SkeletonText lines={6} />
            </div>
          ) : measurements.isError ? (
            <ErrorState error={measurements.error} onRetry={() => void measurements.refetch()} compact />
          ) : (
            <MeasurementsTable data={measurements.data} />
          )}
        </section>
      ) : null}

      {analysisId ? (
        analysis.isPending ? (
          <div aria-busy="true" className="space-y-3">
            <span className="sr-only" role="status">
              Loading the analysis…
            </span>
            <Skeleton className="h-6 w-48" />
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} className="h-20" />
              ))}
            </div>
          </div>
        ) : analysis.isError ? (
          <ErrorState error={analysis.error} onRetry={() => void analysis.refetch()} retrying={analysis.isFetching} />
        ) : (
          <AnalysisSection analysis={analysis.data} datasetId={datasetParam} />
        )
      ) : datasetId && completed ? (
        <AnalysesPicker datasetId={datasetId} />
      ) : null}

      <section aria-labelledby="report-disclaimers" className={cn("print-avoid-break space-y-2 border-t border-border pt-4 text-xs text-fg-muted")}>
        <h2 id="report-disclaimers" className="text-sm font-semibold text-fg">
          Disclaimers and data sources
        </h2>
        <p className="font-medium text-fg">{DISCLAIMER}.</p>
        <p>
          Airspace zones are illustrative rules applied around aerodrome reference points, not an official airspace
          map. Population grids are modelled estimates; ground-risk numbers are evidence for a SORA-style assessment,
          not the assessment itself. Always obtain the official permission for your flight.
        </p>
        <ul className="space-y-0.5">
          {DATA_ATTRIBUTIONS.map((a) => (
            <li key={a.label}>
              {a.label}: {a.text}
            </li>
          ))}
        </ul>
        <p>Generated by AirBrief (MIT licence).</p>
      </section>
    </article>
  );
}
