import { Info } from "lucide-react";
import { DEMO_MODE } from "@/lib/api";
import { cn } from "@/lib/utils";

export const REPO_URL = "https://github.com/Praddyx15/AirBrief";
export const RUNBOOK_URL = `${REPO_URL}/blob/main/RUNBOOK.md`;

/**
 * Shown when the app runs as the static, read-only demo (`NEXT_PUBLIC_DEMO=1`). Renders
 * nothing otherwise.
 */
export function DemoBanner({ className }: { className?: string }) {
  if (!DEMO_MODE) return null;
  return (
    <div
      role="note"
      aria-label="Demo mode"
      className={cn("border-b border-accent/40 bg-accent/10 text-sm text-fg", className)}
      data-print="hide"
    >
      <div className="container flex flex-wrap items-center gap-x-2 gap-y-1 py-2">
        <Info className="size-4 shrink-0 text-accent" aria-hidden />
        <span className="font-medium">Read-only demo.</span>
        <span className="text-fg-muted">
          Samples and their results are pre-computed; uploads and new analyses are switched off.
        </span>
        <a
          href={RUNBOOK_URL}
          className="font-medium text-accent-text underline-offset-4 hover:underline"
          target="_blank"
          rel="noreferrer"
        >
          Run it yourself (RUNBOOK)
        </a>
      </div>
    </div>
  );
}
