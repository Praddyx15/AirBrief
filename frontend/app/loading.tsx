import { Skeleton, SkeletonText } from "@/components/ui/skeleton";

/** Root loading state: header-height bar plus content skeletons (no layout shift). */
export default function Loading() {
  return (
    <>
      <div className="h-14 border-b border-border" aria-hidden />
      <main id="main" tabIndex={-1} className="container space-y-6 py-10 outline-none" aria-busy="true">
        <span className="sr-only" role="status">
          Loading…
        </span>
        <Skeleton className="h-9 w-2/3 max-w-lg" />
        <SkeletonText lines={3} className="max-w-2xl" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-hidden>
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      </main>
    </>
  );
}
