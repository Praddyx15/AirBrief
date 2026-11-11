"use client";

import * as React from "react";
import { Ban, Check, Loader2, X } from "lucide-react";
import { formatDuration, formatPercent } from "@/lib/format";
import { isAnalysisActive, type AnalysisInfo, type FileInfo } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { analysisKindLabel } from "./analysis-runner";
import { AnalysisStatusBadge, FileStatusBadge } from "./status-badges";

export interface JobProgressProps {
  /** e.g. "Airspace check" */
  title: string;
  /** stage names in order */
  stages: readonly string[];
  /** index of the current stage (stages before it are done) */
  currentStage: number;
  /** server stage text, e.g. "Sampling terrain" */
  detail?: string | null;
  /** 0..1, or null for indeterminate */
  progress: number | null;
  /** status badge next to the title */
  badge?: React.ReactNode;
  onCancel?: () => void;
  cancelling?: boolean;
  /** seconds since the job started (shown as elapsed time) */
  elapsedSeconds?: number | null;
  /**
   * The job stopped at `currentStage` without finishing (cancelled): that stage shows a stop
   * icon instead of a spinner, later stages stay to do, and the bar is neutral.
   */
  stopped?: boolean;
  className?: string;
  compact?: boolean;
}

/** Staged job progress: a stepper of stages, a progress bar, the stage detail and Cancel. */
export function JobProgress({
  title,
  stages,
  currentStage,
  detail,
  progress,
  badge,
  onCancel,
  cancelling = false,
  elapsedSeconds,
  stopped = false,
  className,
  compact = false,
}: JobProgressProps) {
  const pct = progress === null ? null : Math.round(Math.min(1, Math.max(0, progress)) * 100);
  const running = !stopped && currentStage < stages.length - 1;
  // "42% · 3.1 s", "3.1 s" or "42%": the separator only between two values.
  const meta = [pct !== null ? formatPercent(pct, { decimals: 0 }) : null, typeof elapsedSeconds === "number" ? formatDuration(elapsedSeconds) : null]
    .filter((s): s is string => s !== null)
    .join(" · ");
  // Server stages arrive in lower case ("queued"); show them in sentence case, and not at all
  // when they only repeat the current stage.
  const detailText = detail ? detail.charAt(0).toUpperCase() + detail.slice(1) : null;
  const shownDetail = detailText && detailText.toLowerCase() !== (stages[currentStage] ?? "").toLowerCase() ? detailText : null;
  return (
    <section
      aria-label={`${title} progress`}
      aria-busy={running}
      className={cn("rounded border border-border bg-surface text-sm", compact ? "space-y-2 p-3" : "space-y-3 p-4", className)}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="truncate font-medium">{title}</h3>
          {badge}
        </div>
        {onCancel ? (
          <Button size="sm" variant="outline" onClick={onCancel} disabled={cancelling} aria-busy={cancelling}>
            {cancelling ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <X aria-hidden />}
            {cancelling ? "Cancelling" : "Cancel"}
          </Button>
        ) : null}
      </div>

      <ol className="flex flex-wrap items-center gap-x-1 gap-y-1 text-xs" aria-label="Stages">
        {stages.map((stage, i) => {
          const done = i < currentStage;
          const current = i === currentStage;
          const halted = current && stopped;
          return (
            <li key={stage} className="flex items-center gap-1" aria-current={current ? "step" : undefined}>
              {i > 0 ? (
                <span aria-hidden className={cn("h-px w-4", done || (current && !stopped) ? "bg-accent" : "bg-border")} />
              ) : null}
              <span
                className={cn(
                  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5",
                  done && "border-accent/50 text-fg",
                  current && !halted && "border-accent bg-accent/10 font-medium text-fg",
                  halted && "border-fg-muted bg-elevated font-medium text-fg",
                  !done && !current && "border-border text-fg-muted",
                )}
              >
                {done ? <Check aria-hidden className="size-3 text-accent" /> : null}
                {halted ? <Ban aria-hidden className="size-3 text-fg-muted" /> : null}
                {current && running ? (
                  <Loader2 aria-hidden className="size-3 animate-spin text-accent motion-reduce:animate-none" />
                ) : null}
                {stage}
                <span className="sr-only">{done ? " (done)" : halted ? " (stopped here)" : current ? " (current)" : " (to do)"}</span>
              </span>
            </li>
          );
        })}
      </ol>

      <Progress value={pct} label={`${title} progress`} tone={stopped ? "neutral" : "accent"} />
      <p className="flex flex-wrap justify-between gap-2 text-xs text-fg-muted">
        <span>{shownDetail ?? (pct === null && running ? "Working" : "")}</span>
        <span className="num">{meta}</span>
      </p>
    </section>
  );
}

function useElapsed(startIso: string | null | undefined, running: boolean): number | null {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [running]);
  if (!startIso) return null;
  const start = Date.parse(startIso);
  return Number.isFinite(start) ? Math.max(0, (now - start) / 1000) : null;
}

const ANALYSIS_STAGES = ["Queued", "Running", "Done"] as const;

/**
 * Progress for an analysis (PENDING → RUNNING → final), with Cancel while active. A cancelled
 * job keeps the progress it reached and shows "Cancelled" in place of the stage it stopped at
 * (Queued or Running), never a full bar or "Done".
 */
export function AnalysisJobProgress({
  analysis,
  onCancel,
  cancelling,
  className,
  compact,
}: {
  analysis: AnalysisInfo;
  onCancel?: () => void;
  cancelling?: boolean;
  className?: string;
  compact?: boolean;
}) {
  const active = isAnalysisActive(analysis.status);
  const cancelled = analysis.status === "CANCELLED";
  const elapsed = useElapsed(analysis.started_at ?? analysis.created_at, active);
  let stages: readonly string[] = ANALYSIS_STAGES;
  let stageIndex = analysis.status === "PENDING" ? 0 : analysis.status === "RUNNING" ? 1 : 2;
  if (cancelled) {
    stageIndex = analysis.started_at ? 1 : 0;
    stages = ANALYSIS_STAGES.map((s, i) => (i === stageIndex ? "Cancelled" : s));
  }
  return (
    <JobProgress
      title={analysisKindLabel(analysis.kind)}
      stages={stages}
      currentStage={stageIndex}
      detail={analysis.status === "PENDING" ? "Waiting for a worker" : analysis.stage}
      progress={analysis.status === "PENDING" ? null : active || cancelled ? analysis.progress : 1}
      badge={<AnalysisStatusBadge status={analysis.status} />}
      onCancel={active ? onCancel : undefined}
      cancelling={cancelling}
      elapsedSeconds={active ? elapsed : analysis.duration_ms !== null ? analysis.duration_ms / 1000 : null}
      stopped={cancelled}
      className={className}
      compact={compact}
    />
  );
}

const FILE_STAGES = ["Uploaded", "Reading and measuring", "Ready"] as const;

/** Progress for a dataset that is still being processed after upload. */
export function FileJobProgress({ file, className }: { file: FileInfo; className?: string }) {
  const active = file.status === "PENDING" || file.status === "PROCESSING";
  const elapsed = useElapsed(file.created_at, active);
  return (
    <JobProgress
      title={`Processing ${file.filename}`}
      stages={FILE_STAGES}
      currentStage={file.status === "PENDING" ? 0 : file.status === "PROCESSING" ? 1 : 2}
      detail={file.status === "PENDING" ? "Waiting for a worker" : file.status === "PROCESSING" ? "Detecting the CRS and measuring every feature" : null}
      progress={null}
      badge={<FileStatusBadge status={file.status} />}
      elapsedSeconds={active ? elapsed : null}
      className={className}
    />
  );
}
