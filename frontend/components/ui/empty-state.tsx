import * as React from "react";
import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  /** Decorative icon element, e.g. <Upload />. */
  icon?: React.ReactElement;
  title: string;
  description?: React.ReactNode;
  /** Primary action(s), e.g. a Button. */
  action?: React.ReactNode;
  className?: string;
  /** Compact variant for panels and table bodies. */
  compact?: boolean;
  /** Heading level for the title (default h3). */
  headingLevel?: "h1" | "h2" | "h3" | "h4";
}

/** What is missing, why, and what to do next. */
export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
  compact = false,
  headingLevel: Heading = "h3",
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 text-center",
        compact ? "px-3 py-6" : "px-6 py-12",
        className,
      )}
    >
      {icon ? (
        <div
          aria-hidden
          className={cn(
            "flex items-center justify-center rounded-full border border-border bg-elevated text-fg-muted",
            compact ? "size-9 [&_svg]:size-4" : "size-12 [&_svg]:size-5",
          )}
        >
          {icon}
        </div>
      ) : null}
      <Heading className={cn("font-medium text-fg", compact ? "text-sm" : "text-base")}>{title}</Heading>
      {description ? <div className="max-w-sm text-sm text-fg-muted">{description}</div> : null}
      {action ? <div className="mt-2 flex flex-wrap items-center justify-center gap-2">{action}</div> : null}
    </div>
  );
}
