"use client";

/**
 * Sample cards on `/w`. "Open" reuses this browser's copy of a sample when the recent list has
 * one, so the list does not fill with duplicates; "New copy" always loads a fresh dataset.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, CopyPlus, Download, Loader2, Route } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { ModuleIcon } from "@/components/layout/module-icon";
import { DEMO_MODE, sampleDownloadUrl } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { MODULE_META } from "@/lib/modules";
import { useLoadSample, useRecentFiles, useSamples } from "@/lib/queries";
import { useHistory } from "@/lib/store";
import type { FileInfo, SampleInfo } from "@/lib/types";
import { workspaceHref } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { RECENT_PAGE } from "./recent-datasets";
import { groupSamples, isFlightmdReportSample, sampleModule } from "./sample-utils";

const cardClass = "flex h-full flex-col gap-3 rounded border border-border bg-surface p-4";

function SampleCard({
  sample,
  existing,
  pending,
  disabled,
  onOpen,
  onOpenCopy,
}: {
  sample: SampleInfo;
  /** a ready dataset already created from this sample (reopened by "Open") */
  existing: FileInfo | null;
  pending: boolean;
  disabled: boolean;
  onOpen: () => void;
  onOpenCopy: () => void;
}) {
  const moduleId = sampleModule(sample);
  const download = sampleDownloadUrl(sample);
  const file = sample.filename.split("/").pop() ?? sample.filename;
  const titleId = `sample-${sample.name}-title`;
  return (
    <li className={cardClass}>
      <div className="flex items-start justify-between gap-2">
        <h4 id={titleId} className="text-sm font-semibold leading-snug">
          {sample.title}
        </h4>
        <Badge variant="outline" className="shrink-0">
          <ModuleIcon module={moduleId} />
          {MODULE_META[moduleId].label}
        </Badge>
      </div>
      <p className="flex-1 text-sm leading-relaxed text-fg-muted">{sample.description}</p>
      <p className="truncate font-mono text-xs text-fg-muted">{file}</p>
      {existing ? (
        <p className="text-xs text-fg-muted">
          Opens your copy from <time dateTime={existing.created_at}>{formatRelative(existing.created_at)}</time>.
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={onOpen} disabled={disabled} aria-busy={pending || undefined}>
          {pending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
          {pending ? "Opening…" : "Open"}
          <span className="sr-only"> sample {sample.title}</span>
        </Button>
        {existing ? (
          <Button size="sm" variant="ghost" onClick={onOpenCopy} disabled={disabled}>
            <CopyPlus aria-hidden />
            New copy
            <span className="sr-only"> of sample {sample.title}</span>
          </Button>
        ) : null}
        {download ? (
          <Button size="sm" variant="ghost" asChild>
            <a href={download} download={file}>
              <Download aria-hidden />
              Download
              <span className="sr-only"> {file}</span>
            </a>
          </Button>
        ) : null}
      </div>
    </li>
  );
}

function SkeletonCards({ count }: { count: number }) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-hidden>
      {Array.from({ length: count }, (_, i) => (
        <li key={i} className={cn(cardClass, "h-[172px]")}>
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-5/6" />
          <Skeleton className="mt-auto h-8 w-20" />
        </li>
      ))}
    </ul>
  );
}

export function SampleCards({ className }: { className?: string }) {
  const router = useRouter();
  const samples = useSamples();
  // Same query (and cache entry) as the first page of Recent datasets.
  const recent = useRecentFiles(RECENT_PAGE);
  const loadSample = useLoadSample();
  const [pendingName, setPendingName] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ name: string; title: string; error: unknown } | null>(null);

  // "#samples": repeat the browser's hash jump once the lists above stop growing, unless the
  // user has started scrolling or pressing keys in the meantime.
  const settled = !samples.isPending && !recent.isPending;
  const jumped = useRef(false);
  const interacted = useRef(false);
  useEffect(() => {
    const mark = () => {
      interacted.current = true;
    };
    const events = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
    events.forEach((e) => window.addEventListener(e, mark, { passive: true, once: true }));
    return () => events.forEach((e) => window.removeEventListener(e, mark));
  }, []);
  useEffect(() => {
    if (!settled || jumped.current) return;
    if (window.location.hash !== "#samples" || interacted.current) {
      jumped.current = true;
      return;
    }
    // After the frame that lays out the loaded lists (scroll-behavior follows reduced motion).
    const frame = window.requestAnimationFrame(() => {
      jumped.current = true;
      document.getElementById("samples")?.scrollIntoView({ block: "start" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [settled]);

  // A processed copy gives the same result as a new one (same file, same processing), even the
  // "Needs CRS" copy of the shapefile sample without a .prj. Only this browser's own copies count
  // (an instance that lists every upload also shows other people's), never one waiting for its
  // delayed delete, and never in the demo, whose pre-built datasets are nobody's copy.
  const ownIds = useHistory((s) => s.datasetIds);
  const pendingDeletes = useHistory((s) => s.pendingDeletes);
  const existingCopy = (sample: SampleInfo): FileInfo | null => {
    if (DEMO_MODE) return null;
    return (
      recent.data?.items.find(
        (f) =>
          f.sample === sample.name &&
          f.status === "COMPLETED" &&
          ownIds.includes(f.id) &&
          pendingDeletes[f.id] === undefined,
      ) ?? null
    );
  };

  const open = (sample: SampleInfo, options: { fresh?: boolean } = {}) => {
    // FlightMD reports are not map datasets: open the flight audit with the sample preselected.
    if (isFlightmdReportSample(sample)) {
      const href = workspaceHref({ module: "flight-audit" });
      router.push(`${href}${href.includes("?") ? "&" : "?"}sample=${encodeURIComponent(sample.name)}`);
      return;
    }
    setFailed(null);
    setPendingName(sample.name);
    const existing = options.fresh ? null : existingCopy(sample);
    if (existing) {
      router.push(workspaceHref({ datasetId: existing.id, module: sampleModule(sample) }));
      return;
    }
    loadSample.mutate(sample.name, {
      onSuccess: (info) => {
        router.push(workspaceHref({ datasetId: info.id, module: sampleModule(sample) }));
      },
      onError: (error) => {
        setFailed({ name: sample.name, title: sample.title, error });
        setPendingName(null);
      },
    });
  };

  const groups = samples.data ? groupSamples(samples.data) : null;

  const renderGroup = (items: SampleInfo[], extra?: ReactNode) => (
    <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {items.map((s) => (
        <SampleCard
          key={s.name}
          sample={s}
          existing={isFlightmdReportSample(s) ? null : existingCopy(s)}
          pending={pendingName === s.name}
          disabled={pendingName !== null}
          onOpen={() => open(s)}
          onOpenCopy={() => open(s, { fresh: true })}
        />
      ))}
      {extra}
    </ul>
  );

  const flightAuditCard = (
    <li className={cn(cardClass, "border-dashed")}>
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-sm font-semibold leading-snug">Audit a FlightMD flight</h4>
        <Badge variant="outline" className="shrink-0">
          <Route aria-hidden />
          Flight audit
        </Badge>
      </div>
      <p className="flex-1 text-sm leading-relaxed text-fg-muted">
        Paste a FlightMD report URL or drop its JSON to check height above ground, the 120 m limit and zones crossed.
      </p>
      <div>
        <Button size="sm" variant="secondary" asChild>
          <Link href={workspaceHref({ module: "flight-audit" })}>
            Start a flight audit
            <ArrowRight aria-hidden />
          </Link>
        </Button>
      </div>
    </li>
  );

  return (
    <section id="samples" aria-labelledby="samples-title" className={cn("scroll-mt-20 space-y-4", className)}>
      <div>
        <h2 id="samples-title" className="text-base font-semibold">
          Start from a sample
        </h2>
        <p className="mt-1 text-sm text-fg-muted">
          Each sample has a known answer, so you can check the numbers. Opening one creates a dataset, or reopens
          your copy.
        </p>
      </div>

      {failed ? (
        <ErrorState
          error={failed.error}
          title={`Could not open "${failed.title}"`}
          onRetry={() => {
            const sample = samples.data?.find((s) => s.name === failed.name);
            if (sample) open(sample, { fresh: true });
          }}
          compact
        />
      ) : null}

      {samples.isPending ? (
        <div className="space-y-3" aria-busy="true">
          <span className="sr-only" role="status">
            Loading samples…
          </span>
          <SkeletonCards count={3} />
        </div>
      ) : samples.isError ? (
        <div className="space-y-3">
          <ErrorState error={samples.error} onRetry={() => void samples.refetch()} retrying={samples.isFetching} />
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{flightAuditCard}</ul>
        </div>
      ) : groups ? (
        <div className="space-y-6">
          {groups.measure.length > 0 ? (
            <div className="space-y-2">
              <h3 className="flex items-center gap-2 text-sm font-medium text-fg-muted">
                <ModuleIcon module="measure" className="size-4" />
                Measure
              </h3>
              {renderGroup(groups.measure)}
            </div>
          ) : null}
          <div className="space-y-2">
            <h3 className="flex items-center gap-2 text-sm font-medium text-fg-muted">
              <ModuleIcon module="airspace" className="size-4" />
              Modules
            </h3>
            {renderGroup(groups.modules, flightAuditCard)}
          </div>
        </div>
      ) : null}
    </section>
  );
}
