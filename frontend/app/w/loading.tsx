import { Skeleton, SkeletonText } from "@/components/ui/skeleton";

/** Loading state for /w and its sub-routes: mirrors the upload page layout. */
export default function WorkspaceLoading() {
  return (
    <>
      <div className="h-14 border-b border-border" aria-hidden />
      <main id="main" tabIndex={-1} className="container space-y-10 py-8 outline-none md:py-10" aria-busy="true">
        <span className="sr-only" role="status">
          Loading the workspace…
        </span>
        <div className="space-y-2">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="h-4 w-full max-w-xl" />
        </div>
        <div className="grid gap-10 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] lg:gap-8">
          <Skeleton className="h-64" />
          <div className="space-y-3">
            <Skeleton className="h-5 w-40" />
            <SkeletonText lines={5} />
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-hidden>
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-[172px]" />
          ))}
        </div>
      </main>
    </>
  );
}
