"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Download, FileStack, FileText, Layers, Trash2 } from "lucide-react";
import { briefHref } from "@/components/report/brief-href";
import { EXPORT_ITEM_CLASS, ExportItem, ExportItemContent, useExportSaver } from "@/components/report/export-items";
import { DEMO_MODE, describeError, fileExportUrl, getMeasurements } from "@/lib/api";
import { measurementsToCsv } from "@/lib/csv";
import { formatBytes, formatNumber, formatRelative } from "@/lib/format";
import { FILE_EXPORT_LABEL, ed318ExportLabel } from "@/lib/labels";
import { hasPolygons, needsCrs } from "@/lib/datasets";
import { scheduleDatasetDelete, undoDatasetDelete } from "@/lib/pending-deletes";
import { queryKeys, useCanModify } from "@/lib/queries";
import { useSettings } from "@/lib/store";
import type { DatasetFeatureCollection, FileExportFormat, FileInfo, FilesPage } from "@/lib/types";
import { workspaceHref } from "@/lib/url-state";
import { downloadBlob } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { IconButton } from "@/components/ui/icon-button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { announce } from "./live-region";
import { FileStatusBadge, ToneBadge } from "./status-badges";

/** Seconds the user has to undo a delete before it is sent to the API. */
export const UNDO_DELETE_SECONDS = 8;

const EXPORTS: { format: FileExportFormat; label: string; description: string }[] = [
  { format: "csv", ...FILE_EXPORT_LABEL.csv },
  { format: "geojson", ...FILE_EXPORT_LABEL.geojson },
  { format: "kml", ...FILE_EXPORT_LABEL.kml },
  { format: "ed318", ...FILE_EXPORT_LABEL.ed318 },
];

export interface DatasetHeaderProps {
  datasetId: string;
  file: FileInfo | null;
  isLoading: boolean;
  /** loaded features, used for client-side exports in demo mode */
  features: DatasetFeatureCollection | null;
  /** analysis id for the report link */
  analysisId?: string | null;
  compact?: boolean;
}

/** Dataset name, status, counts, exports menu and delete (with confirm and undo). */
export function DatasetHeader({ datasetId, file, isLoading, features, analysisId, compact = false }: DatasetHeaderProps) {
  const canDelete = useCanModify(datasetId);
  const router = useRouter();
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const ed318Limits = useSettings((s) => s.ed318Limits);
  const saver = useExportSaver();

  const baseName = (file?.filename ?? "dataset").replace(/\.[^.]+$/, "");
  const fileName: Record<FileExportFormat, string> = {
    csv: `${baseName}-measurements.csv`,
    geojson: `${baseName}.geojson`,
    kml: `${baseName}.kml`,
    ed318: `${baseName}.ed318.geojson`,
  };
  // the demo has no API: these two are built in the browser, KML and ED-318 aren't offered
  const buildInBrowser: Partial<Record<FileExportFormat, () => Promise<void>>> = {
    geojson: async () => {
      if (!features) throw new Error("The features are still loading.");
      downloadBlob(JSON.stringify(features), fileName.geojson, "application/geo+json");
    },
    csv: async () => {
      const page = await getMeasurements(datasetId, { limit: 1_000_000, offset: 0 });
      downloadBlob(measurementsToCsv(page.items), fileName.csv, "text/csv;charset=utf-8");
    },
  };

  const confirmDelete = () => {
    if (!file) return;
    setConfirmOpen(false);
    const id = file.id;
    const name = file.filename;

    // Hide it from cached lists right away; the request is sent after the undo window.
    queryClient.setQueriesData<FilesPage>({ queryKey: queryKeys.filesAll() }, (old) =>
      old ? { ...old, items: old.items.filter((f) => f.id !== id), total: Math.max(0, old.total - 1) } : old,
    );

    // Recorded in the saved history, so it is still sent if this tab closes first.
    scheduleDatasetDelete(queryClient, id, UNDO_DELETE_SECONDS * 1000, (error) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.filesAll() });
      const d = describeError(error);
      toast.error(`Could not delete ${name}`, { description: d.message });
    });

    toast(`Deleted ${name}`, {
      description: `The dataset and its analyses will be removed in ${UNDO_DELETE_SECONDS} seconds.`,
      duration: UNDO_DELETE_SECONDS * 1000,
      action: {
        label: "Undo",
        onClick: () => {
          undoDatasetDelete(id);
          void queryClient.invalidateQueries({ queryKey: queryKeys.filesAll() });
          announce(`Restored ${name}.`);
          router.push(workspaceHref({ datasetId: id }));
        },
      },
    });
    announce(`Deleted ${name}. Undo is available for ${UNDO_DELETE_SECONDS} seconds.`);
    router.push("/w");
  };

  // Every state fills the header (flex-1), so the inspector toggle after it stays at the right edge.
  if (!file && !isLoading) {
    // Not found, deleted or expired: the workspace shows the error and a way back.
    return <h1 className="min-w-0 flex-1 truncate text-sm font-semibold text-fg-muted">Dataset unavailable</h1>;
  }
  if (isLoading || !file) {
    return (
      <div className="flex min-w-0 flex-1 items-center gap-3" aria-busy="true">
        <span className="sr-only">Loading dataset</span>
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-5 w-20" />
      </div>
    );
  }

  const counts = [
    `${formatNumber(file.feature_count)} feature${file.feature_count === 1 ? "" : "s"}`,
    `${formatNumber(file.layer_count)} layer${file.layer_count === 1 ? "" : "s"}`,
    file.crs ? `CRS ${file.crs}` : "CRS unknown",
  ];

  return (
    <div className="flex min-w-0 flex-1 items-center gap-3">
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="truncate text-sm font-semibold">
            {file.filename}
          </h1>
          {/* Processed but nothing measured or drawn (no coordinate system): not "Ready". */}
          {needsCrs(file) ? <ToneBadge tone="warn" label="Needs CRS" /> : <FileStatusBadge status={file.status} />}
        </div>
        {!compact ? (
          <p className="truncate text-xs text-fg-muted">
            <span className="num">{counts.join(" · ")}</span>
            <span aria-hidden> · </span>
            <span>
              {formatBytes(file.size_bytes)}, added {formatRelative(file.created_at)}
            </span>
          </p>
        ) : null}
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-1">
        <Popover>
          <PopoverTrigger asChild>
            <Button size="sm" variant="secondary" disabled={file.status !== "COMPLETED"}>
              <Download aria-hidden />
              <span className={compact ? "sr-only" : undefined}>Export</span>
              <ChevronDown aria-hidden />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 p-1">
            <ul aria-label="Export formats" className="flex flex-col">
              {EXPORTS.filter((e) => e.format !== "ed318" || hasPolygons(file)).map((entry) => {
                const url = fileExportUrl(file.id, entry.format, ed318Limits);
                const build = url ? undefined : buildInBrowser[entry.format];
                const label = entry.format === "ed318" ? ed318ExportLabel(entry, ed318Limits).label : entry.label;
                const icon = <Download aria-hidden className="size-4 text-fg-muted" />;
                return (
                  <li key={entry.format}>
                    {url || build ? (
                      <ExportItem
                        saver={saver}
                        id={entry.format}
                        href={url}
                        build={build}
                        name={fileName[entry.format]}
                        icon={icon}
                        label={label}
                        description={entry.description}
                      />
                    ) : (
                      <button type="button" className={EXPORT_ITEM_CLASS} disabled>
                        <ExportItemContent icon={icon} label={label} description="Not available in the read-only demo" />
                      </button>
                    )}
                  </li>
                );
              })}
              <li className="mt-1 border-t border-border pt-1">
                <Link
                  href={`/w/report?${new URLSearchParams({ d: file.id, ...(analysisId ? { a: analysisId } : {}) }).toString()}`}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm font-medium text-fg no-underline hover:bg-elevated pointer-coarse:min-h-11"
                >
                  <FileText aria-hidden className="size-4 text-fg-muted" />
                  Printable report
                </Link>
              </li>
              <li>
                {/* Several analyses and the operation on one A4 page, with provenance (app/w/report/brief-view.tsx). */}
                <Link
                  href={briefHref(file.id, analysisId)}
                  className="flex w-full flex-col items-start gap-0.5 rounded px-2 py-1.5 text-sm no-underline hover:bg-elevated pointer-coarse:min-h-11"
                >
                  <span className="flex items-center gap-2 font-medium text-fg">
                    <FileStack aria-hidden className="size-4 text-fg-muted" />
                    Create brief
                  </span>
                  <span className="pl-6 text-xs text-fg-muted">This dataset&apos;s analyses and the operation on one A4 page</span>
                </Link>
              </li>
            </ul>
          </PopoverContent>
        </Popover>

        {file.layers.length > 1 && !compact ? (
          <span className="hidden min-w-0 max-w-[18rem] items-center gap-1 text-xs text-fg-muted xl:inline-flex">
            <Layers aria-hidden className="size-3.5 shrink-0" />
            <span className="truncate">
              {file.layers.map((l) => l.name).slice(0, 3).join(", ")}
              {file.layers.length > 3 ? ` +${file.layers.length - 3}` : ""}
            </span>
          </span>
        ) : null}

        <IconButton
          label={
            DEMO_MODE
              ? "Delete (not available in the demo)"
              : canDelete
                ? "Delete dataset"
                : "Delete (only the browser that uploaded it can)"
          }
          icon={<Trash2 />}
          size="icon-sm"
          side="bottom"
          disabled={!canDelete}
          onClick={() => setConfirmOpen(true)}
        />
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {file.filename}?</DialogTitle>
            <DialogDescription>
              The dataset, its features and every analysis run on it are removed from this server. You can undo for{" "}
              {UNDO_DELETE_SECONDS} seconds.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Keep dataset</Button>
            </DialogClose>
            <Button variant="destructive" onClick={confirmDelete}>
              <Trash2 aria-hidden />
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
