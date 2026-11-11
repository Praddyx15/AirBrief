import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Loading placeholder. Size it like the content it replaces so nothing shifts when data
 * arrives. Hidden from assistive tech: announce loading with a parent `aria-busy="true"` or
 * a visually hidden status text. A block-level <span>, so it is also valid inside <p>.
 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      aria-hidden
      className={cn("block animate-pulse rounded bg-elevated motion-reduce:animate-none", className)}
      {...props}
    />
  );
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-2", className)} aria-hidden>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn("h-3.5", i === lines - 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}
