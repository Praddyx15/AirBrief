import type { Metadata } from "next";
import { Suspense } from "react";
import { WorkspaceClient } from "@/components/workspace/workspace-client";
import { WorkspaceSkeleton } from "@/components/workspace/workspace-skeleton";

export const metadata: Metadata = {
  title: "Workspace",
  description: "Map, measurements and planning modules for one dataset. All state is in the URL.",
};

/**
 * `/w/view?d=&f=&m=&a=&t=`. The workspace is client-only (`WorkspaceClient`, next/dynamic with
 * `ssr: false`) and reads the query string (`useSearchParams`), so it also sits inside
 * <Suspense> (required for the static export). `<main id="main">` is rendered by the workspace.
 */
export default function WorkspacePage() {
  return (
    <Suspense fallback={<WorkspaceSkeleton />}>
      <WorkspaceClient />
    </Suspense>
  );
}
