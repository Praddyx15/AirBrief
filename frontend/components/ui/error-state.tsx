"use client";

import * as React from "react";
import { OctagonAlert, RotateCw } from "lucide-react";
import { describeError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "./button";

export interface ErrorStateProps {
  /**
   * The thrown error (ApiError, Error, ApiErrorInfo or string). Title, message and fix are
   * derived from it unless given explicitly.
   */
  error?: unknown;
  title?: string;
  /** What happened. */
  message?: React.ReactNode;
  /** How to fix it. */
  fix?: React.ReactNode;
  /** Shows a Retry button when provided (and the error is retryable, unless forceRetry). */
  onRetry?: () => void;
  retryLabel?: string;
  /** Show Retry even when the error is not marked retryable. Default true. */
  forceRetry?: boolean;
  /** Busy state for the retry button. */
  retrying?: boolean;
  /** Extra actions (e.g. "Back to datasets"). */
  actions?: React.ReactNode;
  className?: string;
  compact?: boolean;
}

/**
 * Error display: what happened, how to fix it, and Retry (PLAN §7). Rendered with
 * role="alert" so it is announced when it appears.
 */
export function ErrorState({
  error,
  title,
  message,
  fix,
  onRetry,
  retryLabel = "Retry",
  forceRetry = true,
  retrying = false,
  actions,
  className,
  compact = false,
}: ErrorStateProps) {
  const described = error !== undefined ? describeError(error) : null;
  const finalTitle = title ?? described?.title ?? "Something went wrong";
  const finalMessage = message ?? described?.message;
  const finalFix = fix ?? described?.fix;
  const showRetry = Boolean(onRetry) && (forceRetry || (described?.retryable ?? true));

  return (
    <div
      role="alert"
      className={cn(
        "flex gap-3 rounded border border-danger/50 bg-danger/10 text-sm text-fg",
        compact ? "p-3" : "p-4",
        className,
      )}
    >
      <OctagonAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
      <div className="min-w-0 flex-1 space-y-1.5">
        <p className="font-medium leading-tight">{finalTitle}</p>
        {finalMessage ? <div className="break-words text-fg">{finalMessage}</div> : null}
        {finalFix ? (
          <div className="text-fg-muted">
            <span className="font-medium text-fg">How to fix: </span>
            {finalFix}
          </div>
        ) : null}
        {showRetry || actions ? (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            {showRetry ? (
              <Button size="sm" variant="secondary" onClick={onRetry} disabled={retrying} aria-busy={retrying}>
                <RotateCw className={cn(retrying && "animate-spin motion-reduce:animate-none")} aria-hidden />
                {retryLabel}
              </Button>
            ) : null}
            {actions}
          </div>
        ) : null}
      </div>
    </div>
  );
}
