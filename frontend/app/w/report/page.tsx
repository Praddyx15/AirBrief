import { Suspense } from "react";
import type { Metadata } from "next";
import { SiteHeader } from "@/components/layout/site-header";
import { SkeletonText } from "@/components/ui/skeleton";
import { ReportView } from "./report-view";

export const metadata: Metadata = {
  title: "Report",
  description:
    "Print-friendly report of a dataset and its analysis, with exports, and the one-page operation evidence brief with its bundle.",
};

function ReportFallback() {
  return (
    <div aria-busy="true" className="space-y-6">
      <span className="sr-only" role="status">
        Loading the report…
      </span>
      <SkeletonText lines={2} className="max-w-md" />
      <SkeletonText lines={6} />
    </div>
  );
}

export default function ReportPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" tabIndex={-1} className="container max-w-5xl py-6 outline-none md:py-8 print:max-w-none print:p-0">
        <Suspense fallback={<ReportFallback />}>
          <ReportView />
        </Suspense>
      </main>
    </>
  );
}
