"use client";

/**
 * Recent datasets on `/w`. One keyboard stop per action: the file name is only a mouse shortcut
 * to "Open" (tabIndex -1), and a skip link jumps past the list to the samples.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Database, FileText, Loader2, OctagonAlert, Trash2, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
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
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { IconButton } from "@/components/ui/icon-button";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { DEMO_MODE, describeError } from "@/lib/api";
import { needsCrs } from "@/lib/datasets";
import { formatBytes, formatDate, formatNumber, formatRelative } from "@/lib/format";
import { fileTypeLabel } from "@/lib/labels";
import { useCanModify, useDeleteFile, useMeta, useRecentFiles } from "@/lib/queries";
import { useHistory } from "@/lib/store";
import type { FileInfo, FileStatus } from "@/lib/types";
import { workspaceHref } from "@/lib/url-state";
import { cn } from "@/lib/utils";

/** Rows per page of the list (also the page the sample cards look in for an existing copy). */
export const RECENT_PAGE = 10;

export function FileStatusBadge({
  status,
  file,
  className,
}: {
  status: FileStatus;
  /** when given, a processed dataset that still needs a coordinate system shows "Needs CRS" */
  file?: Pick<FileInfo, "status" | "crs" | "feature_count" | "totals">;
  className?: string;
}) {
  if (file && needsCrs(file)) {
    return (
      <Badge variant="warn" className={className}>
        <AlertTriangle aria-hidden />
        Needs CRS
      </Badge>
    );
  }
  switch (status) {
    case "COMPLETED":
      return (
        <Badge variant="ok" className={className}>
          <CheckCircle2 aria-hidden />
          Ready
        </Badge>
      );
    case "FAILED":
      return (
        <Badge variant="danger" className={className}>
          <OctagonAlert aria-hidden />
          Failed
        </Badge>
      );
    case "PROCESSING":
      return (
        <Badge variant="info" className={className}>
          <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden />
          Processing
        </Badge>
      );
    default:
      return (
        <Badge variant="info" className={className}>
          <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden />
          Queued
        </Badge>
      );
  }
}

function DatasetRow({ file, onDelete }: { file: FileInfo; onDelete: (file: FileInfo) => void }) {
  const canDelete = useCanModify(file.id);
  const href = workspaceHref({ datasetId: file.id });
  const type = fileTypeLabel(file.file_type);
  return (
    <li className="flex flex-col gap-2 border-b border-border px-3 py-3 last:border-b-0 sm:flex-row sm:items-center sm:gap-4">
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {/* Same destination as "Open": a mouse shortcut, left out of the Tab order. */}
          <Link
            href={href}
            tabIndex={-1}
            className="min-w-0 truncate text-sm font-medium text-fg underline-offset-4 hover:text-accent-text hover:underline"
          >
            {file.filename}
          </Link>
          <FileStatusBadge status={file.status} file={file} />
          {file.sample ? <Badge variant="outline">Sample</Badge> : null}
        </div>
        <p className="num mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-fg-muted">
          <span>
            {formatNumber(file.feature_count)} {file.feature_count === 1 ? "feature" : "features"}
          </span>
          {type ? <span>{type}</span> : null}
          <span>{formatBytes(file.size_bytes)}</span>
          {file.crs ? <span>{file.crs}</span> : null}
          <span>
            <time dateTime={file.created_at}>{formatRelative(file.created_at)}</time>
            <span className="sr-only"> ({formatDate(file.created_at, { withTime: true })})</span>
          </span>
        </p>
        {file.status === "FAILED" && file.error ? (
          <p className="mt-1 text-xs text-fg">
            <span className="font-medium">{describeError(file.error).title}: </span>
            <span className="text-fg-muted">{file.error.message}</span>
          </p>
        ) : needsCrs(file) ? (
          <p className="mt-1 text-xs text-fg-muted">
            No coordinate system found, so nothing was measured or drawn. Upload the file again with a CRS override
            (Advanced options, under the upload box).
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button size="sm" variant="secondary" asChild>
          <Link href={href} data-row-open="">
            Open<span className="sr-only"> {file.filename}</span>
          </Link>
        </Button>
        {file.status === "COMPLETED" ? (
          <Button size="sm" variant="ghost" asChild>
            <Link href={workspaceHref({ datasetId: file.id }, "/w/report")}>
              <FileText aria-hidden />
              Report<span className="sr-only"> for {file.filename}</span>
            </Link>
          </Button>
        ) : null}
        {canDelete ? (
          <IconButton label={`Delete ${file.filename}`} icon={<Trash2 />} size="icon-sm" onClick={() => onDelete(file)} />
        ) : null}
      </div>
    </li>
  );
}

export function RecentDatasets({ className }: { className?: string }) {
  const [limit, setLimit] = useState(RECENT_PAGE);
  // Public instances refuse to list every upload: then only this browser's own datasets are shown.
  const files = useRecentFiles(limit);
  const remove = useDeleteFile();
  const [confirming, setConfirming] = useState<FileInfo | null>(null);
  const listRef = useRef<HTMLUListElement>(null);
  /** index of the first row of the page being loaded by "Show more" (gets focus when it arrives) */
  const focusFrom = useRef<number | null>(null);

  const items = files.data?.items ?? [];
  const total = files.data?.total ?? 0;
  // A longer page is loading (the previous one stays on screen as placeholder data). Background
  // polling of the same page does not count.
  const loadingMore = files.isFetching && files.isPlaceholderData;

  // The skeleton reserves the height of the page about to load, so nothing below it moves
  // (PLAN U6): this browser's own datasets on a scoped instance, a full page otherwise.
  const meta = useMeta();
  const rememberedCount = useHistory((s) => s.datasetIds.length);
  const scoped = !DEMO_MODE && meta.data?.global_listing !== true;
  const skeletonRows = scoped ? Math.min(Math.max(rememberedCount, 1), RECENT_PAGE) : RECENT_PAGE;

  useEffect(() => {
    const from = focusFrom.current;
    // Wait until the longer page has replaced the previous one (placeholder) or the fetch failed.
    if (from === null || files.isFetching || files.isPlaceholderData) return;
    focusFrom.current = null;
    if (items.length > from) {
      listRef.current?.querySelectorAll<HTMLElement>("[data-row-open]")[from]?.focus();
    }
  }, [items.length, files.isFetching, files.isPlaceholderData]);

  const showMore = () => {
    // aria-disabled (not disabled) while loading, so the button keeps keyboard focus.
    if (loadingMore) return;
    focusFrom.current = items.length;
    setLimit((l) => l + RECENT_PAGE);
  };

  const confirmDelete = () => {
    const target = confirming;
    if (!target) return;
    remove.mutate(target.id, {
      onSuccess: () => {
        toast.success(`Deleted ${target.filename}`);
        setConfirming(null);
      },
      onError: (error) => {
        const d = describeError(error);
        toast.error(d.title, { description: `${d.message} ${d.fix}` });
      },
    });
  };

  return (
    <section aria-labelledby="recent-title" className={cn("space-y-3", className)}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id="recent-title" className="text-base font-semibold">
          Recent datasets
        </h2>
        <div className="flex items-baseline gap-3">
          <a
            href="#samples"
            className="sr-only text-xs font-medium text-accent-text underline underline-offset-4 focus:not-sr-only"
          >
            Skip to samples
          </a>
          {total > 0 ? (
            <p className="num text-xs text-fg-muted">
              {formatNumber(Math.min(items.length, total))} of {formatNumber(total)}
            </p>
          ) : null}
        </div>
      </div>

      {files.isPending ? (
        <div className="rounded border border-border bg-surface" aria-busy="true">
          <span className="sr-only" role="status">
            Loading datasets…
          </span>
          {/* Same box model as DatasetRow (two text lines, then the actions). */}
          {Array.from({ length: skeletonRows }, (_, i) => (
            <div
              key={i}
              className="flex flex-col gap-2 border-b border-border px-3 py-3 last:border-b-0 sm:flex-row sm:items-center sm:gap-4"
              aria-hidden
            >
              <div className="min-w-0 flex-1">
                <Skeleton className="h-5 w-1/2" />
                <Skeleton className="mt-1 h-4 w-1/3" />
              </div>
              <Skeleton className="h-8 w-36 shrink-0" />
            </div>
          ))}
        </div>
      ) : files.isError ? (
        <ErrorState error={files.error} onRetry={() => void files.refetch()} retrying={files.isFetching} />
      ) : items.length === 0 ? (
        <div className="rounded border border-border bg-surface">
          <EmptyState
            icon={<Database />}
            title="No datasets yet"
            description="Upload a Shapefile, KML, GeoJSON or CSV above, or open one of the samples. Your datasets will be listed here."
            headingLevel="h3"
            action={
              <Button size="sm" variant="secondary" asChild>
                <a href="#samples">
                  <Upload aria-hidden />
                  Browse samples
                </a>
              </Button>
            }
          />
        </div>
      ) : (
        <>
          <ul ref={listRef} className="rounded border border-border bg-surface" aria-label="Recent datasets">
            {items.map((f) => (
              <DatasetRow key={f.id} file={f} onDelete={setConfirming} />
            ))}
          </ul>
          {items.length < total ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={showMore}
              aria-disabled={loadingMore || undefined}
              aria-busy={loadingMore || undefined}
              className="aria-disabled:cursor-progress aria-disabled:opacity-70"
            >
              {loadingMore ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
              Show more
              <span className="sr-only"> datasets</span>
            </Button>
          ) : null}
        </>
      )}

      <Dialog open={confirming !== null} onOpenChange={(open) => (!open && !remove.isPending ? setConfirming(null) : undefined)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this dataset?</DialogTitle>
            <DialogDescription>
              {confirming ? (
                <>
                  <span className="font-medium text-fg">{confirming.filename}</span> and every analysis run on it will
                  be deleted. This cannot be undone.
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="secondary" disabled={remove.isPending}>
                Keep it
              </Button>
            </DialogClose>
            <Button variant="destructive" onClick={confirmDelete} disabled={remove.isPending} aria-busy={remove.isPending || undefined}>
              {remove.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Trash2 aria-hidden />}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
