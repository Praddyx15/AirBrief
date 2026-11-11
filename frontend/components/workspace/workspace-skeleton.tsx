import { Skeleton } from "@/components/ui/skeleton";

/**
 * Loading layout of the workspace (route `loading.tsx` and the Suspense fallback). Same
 * geometry as the real layout so nothing shifts when it loads.
 */
export function WorkspaceSkeleton() {
  return (
    <div className="flex h-dvh overflow-hidden bg-bg" aria-busy="true">
      <p role="status" className="sr-only">
        Loading the workspace
      </p>
      <div className="hidden w-rail shrink-0 flex-col items-center gap-3 border-r border-border bg-surface py-3 md:flex">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="size-9" />
        ))}
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-surface px-3">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-5 w-16" />
          <Skeleton className="ml-auto h-8 w-24" />
        </div>
        <div className="flex min-h-0 flex-1">
          <div className="hidden w-panel-left shrink-0 flex-col gap-2 border-r border-border bg-surface p-3 lg:flex">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-6 w-full" />
            <Skeleton className="mt-3 h-9 w-full" />
            {Array.from({ length: 10 }, (_, i) => (
              <Skeleton key={i} className="h-7 w-full" />
            ))}
          </div>
          <div className="relative min-w-0 flex-1">
            <Skeleton className="absolute inset-0 rounded-none" />
          </div>
          <div className="hidden w-panel-right shrink-0 flex-col gap-2 border-l border-border bg-surface p-3 lg:flex">
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="mt-2 h-40 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        </div>
        <div className="h-11 shrink-0 border-t border-border bg-surface" />
      </div>
    </div>
  );
}
