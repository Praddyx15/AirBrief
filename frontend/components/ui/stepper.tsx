"use client";

import * as React from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export interface StepperStep {
  id: string;
  label: string;
  /** Optional short status under the label, e.g. "2 features" */
  description?: string;
}

export interface StepperProps {
  steps: StepperStep[];
  /** index of the current step */
  current: number;
  /**
   * Called when a completed (earlier) step is activated. When omitted, steps are not
   * interactive. Later steps are never clickable.
   */
  onStepClick?: (index: number) => void;
  /** Accessible name of the step list. */
  label?: string;
  className?: string;
}

/**
 * Step indicator for module flows (Area → Parameters → Run → Results). The current step has
 * aria-current="step"; completed steps show a check icon and the text "completed".
 */
export function Stepper({ steps, current, onStepClick, label = "Progress", className }: StepperProps) {
  return (
    <nav aria-label={label} className={cn("w-full", className)}>
      <ol className="flex w-full items-center gap-1">
        {steps.map((step, index) => {
          const state = index < current ? "complete" : index === current ? "current" : "upcoming";
          const clickable = Boolean(onStepClick) && state === "complete";
          const content = (
            <>
              <span
                className={cn(
                  "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-medium tabular-nums transition-colors duration-200",
                  state === "complete" && "border-accent-solid bg-accent-solid text-accent-foreground",
                  state === "current" && "border-accent bg-accent/15 text-fg",
                  state === "upcoming" && "border-border text-fg-muted",
                )}
                aria-hidden
              >
                {state === "complete" ? <Check className="size-3.5" /> : index + 1}
              </span>
              <span className="flex min-w-0 flex-col text-left">
                <span
                  className={cn(
                    "truncate text-xs font-medium",
                    state === "upcoming" ? "text-fg-muted" : "text-fg",
                  )}
                >
                  {step.label}
                  {state === "complete" ? <span className="sr-only"> (completed)</span> : null}
                </span>
                {step.description ? (
                  <span className="truncate text-2xs text-fg-muted">{step.description}</span>
                ) : null}
              </span>
            </>
          );
          return (
            <li
              key={step.id}
              className="flex min-w-0 flex-1 items-center gap-1"
              aria-current={state === "current" ? "step" : undefined}
            >
              {clickable ? (
                <button
                  type="button"
                  className="flex min-w-0 cursor-pointer items-center gap-2 rounded p-1 transition-colors hover:bg-elevated pointer-coarse:min-h-11"
                  onClick={() => onStepClick?.(index)}
                >
                  {content}
                </button>
              ) : (
                <div className="flex min-w-0 items-center gap-2 p-1">{content}</div>
              )}
              {index < steps.length - 1 ? (
                <span
                  aria-hidden
                  className={cn("h-px min-w-3 flex-1", index < current ? "bg-accent-solid" : "bg-border")}
                />
              ) : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
