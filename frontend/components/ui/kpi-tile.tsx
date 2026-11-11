import * as React from "react";
import { AlertTriangle, CheckCircle2, Info, OctagonAlert, type LucideIcon } from "lucide-react";
import type { FormattedValue } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Skeleton } from "./skeleton";

export type KpiTone = "neutral" | "ok" | "warn" | "danger" | "info";

const TONE_ICON: Record<Exclude<KpiTone, "neutral">, { Icon: LucideIcon; className: string }> = {
  ok: { Icon: CheckCircle2, className: "text-ok" },
  warn: { Icon: AlertTriangle, className: "text-warn" },
  danger: { Icon: OctagonAlert, className: "text-danger" },
  info: { Icon: Info, className: "text-accent" },
};

export interface KpiTileProps {
  label: string;
  /** Formatted value ("1,250") or a FormattedValue from lib/format (value + unit). */
  value: React.ReactNode | FormattedValue;
  /** Unit shown smaller after the value (ignored when `value` is a FormattedValue). */
  unit?: string;
  /** One line explaining what the number means or how it was computed. */
  explanation?: React.ReactNode;
  /** Status tone; adds an icon and `toneLabel` text (never colour alone). */
  tone?: KpiTone;
  /** Text describing the tone, e.g. "Above limit". Required for non-neutral tones. */
  toneLabel?: string;
  loading?: boolean;
  className?: string;
}

/** Text values longer than this (e.g. a camera name) use a smaller size so they wrap less. */
const LONG_TEXT_VALUE = 18;

function isFormatted(value: unknown): value is FormattedValue {
  return (
    typeof value === "object" &&
    value !== null &&
    "value" in value &&
    "unit" in value &&
    "text" in value &&
    typeof (value as FormattedValue).text === "string"
  );
}

export function KpiTile({
  label,
  value,
  unit,
  explanation,
  tone = "neutral",
  toneLabel,
  loading = false,
  className,
}: KpiTileProps) {
  const v = isFormatted(value) ? value.value : value;
  const u = isFormatted(value) ? value.unit : unit;
  const toneInfo = tone === "neutral" ? null : TONE_ICON[tone];

  return (
    <dl
      className={cn("flex min-w-0 flex-col gap-1 rounded border border-border bg-surface p-3", className)}
      aria-busy={loading || undefined}
    >
      <dt className="break-words text-xs font-medium text-fg-muted">{label}</dt>
      {/* Values wrap instead of truncating: a cut-off camera name or footprint cannot be expanded on paper. */}
      <dd className="flex min-h-7 flex-wrap items-baseline gap-x-1">
        {loading ? (
          <Skeleton className="h-6 w-20" />
        ) : (
          <>
            <span
              className={cn(
                "num min-w-0 break-words font-semibold text-fg",
                typeof v === "string" && v.length > LONG_TEXT_VALUE ? "text-base leading-6" : "text-xl leading-7",
              )}
            >
              {v}
            </span>
            {u ? <span className="num text-sm text-fg-muted">{u}</span> : null}
          </>
        )}
      </dd>
      {toneInfo && toneLabel && !loading ? (
        <dd className="flex items-center gap-1 text-xs text-fg">
          <toneInfo.Icon className={cn("size-3.5 shrink-0", toneInfo.className)} aria-hidden />
          {toneLabel}
        </dd>
      ) : null}
      {explanation ? <dd className="text-xs leading-snug text-fg-muted">{explanation}</dd> : null}
    </dl>
  );
}

/** Responsive grid of KPI tiles (each tile is its own <dl>). */
export function KpiGrid({
  className,
  children,
  label,
}: {
  className?: string;
  children: React.ReactNode;
  /** Optional accessible name for the group, e.g. "Survey summary". */
  label?: string;
}) {
  return (
    <div
      role={label ? "group" : undefined}
      aria-label={label}
      className={cn("grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4", className)}
    >
      {children}
    </div>
  );
}
