import { WorkspaceSkeleton } from "@/components/workspace/workspace-skeleton";

/** Route-level loading UI: same layout as the workspace, so nothing shifts. */
export default function Loading() {
  return <WorkspaceSkeleton />;
}
