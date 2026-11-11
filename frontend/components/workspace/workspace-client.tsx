"use client";

/**
 * Client-only entry for the workspace. MapLibre, deck.gl and the module layer code touch
 * browser APIs, and the workspace reads the URL anyway, so it is never rendered on the server
 * (the static export ships the skeleton and hydrates into the real workspace).
 */

import dynamic from "next/dynamic";
import { WorkspaceSkeleton } from "./workspace-skeleton";

export const WorkspaceClient = dynamic(() => import("./workspace").then((m) => m.Workspace), {
  ssr: false,
  loading: () => <WorkspaceSkeleton />,
});
