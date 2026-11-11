"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";

/** Route-level error boundary: what happened, how to fix it, and Retry (PLAN §7). */
export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main id="main" tabIndex={-1} className="container flex min-h-[60dvh] items-center justify-center py-12 outline-none">
      <div className="w-full max-w-xl space-y-4">
        <h1 className="text-xl font-semibold tracking-tight">This page hit a problem</h1>
        <ErrorState
          error={error}
          fix="Retry. If it keeps happening, reload the page or go back to the workspace."
          onRetry={reset}
          actions={
            <Button size="sm" variant="ghost" asChild>
              <Link href="/w">Go to the workspace</Link>
            </Button>
          }
        />
        {error.digest ? (
          <p className="num text-xs text-fg-muted">Reference: {error.digest}</p>
        ) : null}
      </div>
    </main>
  );
}
