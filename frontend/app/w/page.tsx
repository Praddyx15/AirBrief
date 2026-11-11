import type { Metadata } from "next";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { Dropzone } from "@/components/upload/dropzone";
import { RecentDatasets } from "@/components/upload/recent-datasets";
import { SampleCards } from "@/components/upload/sample-cards";

export const metadata: Metadata = {
  title: "Workspace",
  description: "Upload a Shapefile, KML, GeoJSON or CSV, open a sample, or continue with a recent dataset.",
};

export default function WorkspaceHomePage() {
  return (
    <>
      <SiteHeader />
      <main id="main" tabIndex={-1} className="container space-y-10 py-8 outline-none md:py-10">
        <div className="space-y-1">
          <h1 className="text-balance text-2xl font-semibold tracking-tight">Open a dataset</h1>
          <p className="max-w-[65ch] text-sm text-fg-muted">
            Upload geometry to measure it and run the modules on it, start from a sample with a known answer, or pick
            up where you left off.
          </p>
        </div>
        {/* minmax(0, …) tracks: a wide child (the upload stepper) can never widen the page. */}
        <div className="grid grid-cols-[minmax(0,1fr)] gap-10 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] lg:gap-8">
          <Dropzone className="min-w-0" />
          <RecentDatasets className="min-w-0" />
        </div>
        <SampleCards />
      </main>
      <SiteFooter />
    </>
  );
}
