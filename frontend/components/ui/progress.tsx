"use client";

import * as React from "react";
import * as ProgressPrimitive from "@radix-ui/react-progress";
import { cn } from "@/lib/utils";

export interface ProgressProps
  extends Omit<React.ComponentPropsWithoutRef<typeof ProgressPrimitive.Root>, "value" | "max"> {
  /** 0..100, or null/undefined for indeterminate */
  value?: number | null;
  /** Accessible name, e.g. "Upload progress". Required unless aria-labelledby is given. */
  label?: string;
  /** `neutral`: a job that stopped without finishing (e.g. cancelled) */
  tone?: "accent" | "ok" | "warn" | "danger" | "neutral";
}

const TONE_BAR: Record<NonNullable<ProgressProps["tone"]>, string> = {
  accent: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  neutral: "bg-fg-muted",
};

/** Determinate or indeterminate progress bar (transform-based, no width animation). */
export const Progress = React.forwardRef<React.ElementRef<typeof ProgressPrimitive.Root>, ProgressProps>(
  ({ className, value, label, tone = "accent", ...props }, ref) => {
    const determinate = typeof value === "number" && Number.isFinite(value);
    const clamped = determinate ? Math.min(100, Math.max(0, value)) : 0;
    return (
      <ProgressPrimitive.Root
        ref={ref}
        value={determinate ? clamped : null}
        max={100}
        aria-label={label}
        getValueLabel={(v, max) => `${Math.round((v / max) * 100)}%`}
        className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-border", className)}
        {...props}
      >
        <ProgressPrimitive.Indicator
          className={cn(
            "h-full w-full rounded-full transition-transform duration-250",
            TONE_BAR[tone],
            !determinate && "w-2/5 animate-progress-indeterminate",
          )}
          style={determinate ? { transform: `translateX(-${100 - clamped}%)` } : undefined}
        />
      </ProgressPrimitive.Root>
    );
  },
);
Progress.displayName = "Progress";
