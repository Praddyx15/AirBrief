"use client";

import * as React from "react";
import { Crosshair } from "lucide-react";
import { formatCoord, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import { SimpleTooltip } from "@/components/ui/tooltip";

export interface CoordinateReadoutProps {
  /** pointer position, or the map centre when the pointer is outside the map */
  lngLat: readonly [number, number] | null;
  /** true when `lngLat` is the map centre (keyboard users) rather than the pointer */
  isCenter?: boolean;
  zoom?: number | null;
  className?: string;
}

/**
 * Pointer / centre coordinates in decimal degrees or DMS (click to switch). Deliberately not
 * a live region: it changes on every pointer move.
 */
export function CoordinateReadout({ lngLat, isCenter = false, zoom, className }: CoordinateReadoutProps) {
  const [format, setFormat] = React.useState<"dd" | "dms">("dd");
  const text = lngLat ? formatCoord(lngLat[0], lngLat[1], { format, precision: format === "dd" ? 5 : 1 }) : "—";
  return (
    <SimpleTooltip content={format === "dd" ? "Show degrees, minutes, seconds" : "Show decimal degrees"}>
      <button
        type="button"
        data-map-overlay="readout"
        onClick={() => setFormat((f) => (f === "dd" ? "dms" : "dd"))}
        aria-label={`${isCenter ? "Map centre" : "Pointer"}: ${text}. Switch coordinate format`}
        className={cn(
          "pointer-events-auto inline-flex cursor-pointer items-center gap-1.5 whitespace-nowrap rounded border border-border bg-surface/95 px-2 py-1 text-xs text-fg hover:bg-elevated pointer-coarse:min-h-11",
          className,
        )}
      >
        <Crosshair aria-hidden className="size-3.5 text-fg-muted" />
        <span className="text-fg-muted">{isCenter ? "Centre" : "Pointer"}</span>
        <span className="num">{text}</span>
        {typeof zoom === "number" ? <span className="num text-fg-muted">z{formatNumber(zoom, { decimals: 1 })}</span> : null}
      </button>
    </SimpleTooltip>
  );
}
