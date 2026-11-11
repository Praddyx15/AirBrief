"use client";

/**
 * "Draw, pin or open a dataset": what a module panel shows when no dataset is open, with
 * buttons that start plan mode on the map (`plan-actions.ts`) or open the coordinates and paste
 * dialogs (the keyboard alternative to drawing). The drawn operation is saved as a dataset, so
 * the panel then works exactly as with an uploaded file.
 */

import * as React from "react";
import Link from "next/link";
import { ClipboardPaste, FolderOpen, Keyboard, MapPin, MapPinned, Pentagon, Spline } from "lucide-react";
import type { PlanTool } from "@/lib/plan";
import { usePlan } from "@/lib/plan-store";
import { Button } from "@/components/ui/button";
import { startPlanTool, TOOL_TITLE } from "./plan-actions";
import { useWorkspaceBreakpoint } from "./use-media-query";

const ICON: Record<PlanTool, React.ReactElement> = {
  pin: <MapPin aria-hidden />,
  line: <Spline aria-hidden />,
  area: <Pentagon aria-hidden />,
};

export interface PlanStartHintProps {
  /** one sentence: what the module does with the operation */
  purpose: string;
  /** drawing tools offered, most useful first (default pin, line, area) */
  tools?: readonly PlanTool[];
  /** offer "Paste…" (GeoJSON / WKT), e.g. for modules that need a polygon */
  paste?: boolean;
}

export function PlanStartHint({ purpose, tools = ["pin", "line", "area"], paste = false }: PlanStartHintProps) {
  // Phones plan with a pin only (the map's Pin / My location); drawing and typing need more room.
  const phone = useWorkspaceBreakpoint() === "mobile";
  const shown = phone ? tools.filter((t) => t === "pin") : tools;
  const firstTool = tools[0] ?? "pin";
  return (
    <div className="space-y-2 rounded border border-dashed border-border p-2.5 text-xs text-fg-muted">
      <p className="flex items-start gap-2">
        <MapPinned aria-hidden className="mt-0.5 size-3.5 shrink-0" />
        <span>
          <span className="font-medium text-fg">Draw, pin or open a dataset.</span> {purpose}
        </span>
      </p>
      <div role="group" aria-label="Define the operation on the map" className="flex flex-wrap gap-1.5">
        {shown.map((tool) => (
          <Button key={tool} size="sm" variant="secondary" onClick={() => startPlanTool(tool)}>
            {ICON[tool]}
            {TOOL_TITLE[tool]}
          </Button>
        ))}
        {phone ? null : (
          <Button size="sm" variant="secondary" onClick={() => usePlan.getState().openDialog("coordinates", firstTool)}>
            <Keyboard aria-hidden />
            Type coordinates
          </Button>
        )}
        {paste && !phone ? (
          <Button size="sm" variant="secondary" onClick={() => usePlan.getState().openDialog("paste")}>
            <ClipboardPaste aria-hidden />
            Paste…
          </Button>
        ) : null}
        <Button asChild size="sm" variant="ghost">
          <Link href="/w">
            <FolderOpen aria-hidden />
            Open a dataset
          </Link>
        </Button>
      </div>
    </div>
  );
}
