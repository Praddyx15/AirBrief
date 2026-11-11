/**
 * Link to the operation evidence brief: `/w/report?d=<dataset>&a=<analysis>&mode=brief`. The
 * dataset's analyses (and the given one) are ticked by default.
 */

import { workspaceHref } from "@/lib/url-state";

/** The `mode` value of the report page that opens the brief. */
export const BRIEF_MODE = "brief";

export function briefHref(datasetId?: string | null, analysisId?: string | null): string {
  const href = workspaceHref({ datasetId: datasetId ?? null, analysisId: analysisId ?? null }, "/w/report");
  return `${href}${href.includes("?") ? "&" : "?"}mode=${BRIEF_MODE}`;
}
