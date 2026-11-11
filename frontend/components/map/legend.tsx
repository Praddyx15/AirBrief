"use client";

import * as React from "react";
import { ChevronDown, Table2 } from "lucide-react";
import { zoneSwatchStyle } from "@/lib/colors";
import { formatNumber } from "@/lib/format";
import type { LegendItem, LegendSpec } from "@/lib/modules";
import { cn } from "@/lib/utils";
import { IconButton } from "@/components/ui/icon-button";
import { useMapObstacle, useMapOverlay } from "./map-overlay";

export type { LegendItem, LegendSpec } from "@/lib/modules";

function Swatch({ item }: { item: LegendItem }) {
  if (item.zone) {
    return <span aria-hidden className="inline-block size-3.5 shrink-0 rounded-sm border border-border" style={zoneSwatchStyle(item.zone)} />;
  }
  const shape = item.shape ?? "fill";
  switch (shape) {
    case "line":
      return <span aria-hidden className="inline-block h-[3px] w-4 shrink-0 rounded-full" style={{ backgroundColor: item.color }} />;
    case "dashed-line":
      return (
        <span
          aria-hidden
          className="inline-block h-[3px] w-4 shrink-0"
          style={{ backgroundImage: `repeating-linear-gradient(90deg, ${item.color} 0 4px, transparent 4px 7px)` }}
        />
      );
    case "point":
      return <span aria-hidden className="inline-block size-2.5 shrink-0 rounded-full border border-bg" style={{ backgroundColor: item.color }} />;
    case "ring":
      return <span aria-hidden className="inline-block size-3 shrink-0 rounded-full border-2 border-dashed" style={{ borderColor: item.color }} />;
    case "hatch":
      return (
        <span
          aria-hidden
          className="inline-block size-3.5 shrink-0 rounded-sm border"
          style={{ borderColor: item.color, backgroundImage: `repeating-linear-gradient(45deg, ${item.color} 0 1.5px, transparent 1.5px 4px)` }}
        />
      );
    case "hex":
      return (
        <span
          aria-hidden
          className="inline-block size-3.5 shrink-0"
          style={{ backgroundColor: item.color, clipPath: "polygon(25% 5%, 75% 5%, 100% 50%, 75% 95%, 25% 95%, 0 50%)" }}
        />
      );
    default:
      return (
        <span
          aria-hidden
          className="inline-block size-3.5 shrink-0 rounded-sm border"
          style={{ backgroundColor: item.color, borderColor: item.color, opacity: 0.85 }}
        />
      );
  }
}

function RampBlock({ spec }: { spec: LegendSpec }) {
  const stops = spec.stops ?? [];
  const breaks = spec.breaks ?? [];
  const fmt = spec.formatBreak ?? ((v: number) => formatNumber(v));
  const gradient = stops.length > 1 ? `linear-gradient(90deg, ${stops.join(", ")})` : stops[0] ?? "transparent";
  const description =
    breaks.length >= 2
      ? `Colour scale from ${fmt(breaks[0] as number)} to ${fmt(breaks[breaks.length - 1] as number)}${spec.unit ? ` ${spec.unit}` : ""}`
      : spec.title;
  return (
    <div>
      <div role="img" aria-label={description} className="h-2.5 w-full rounded-sm border border-border" style={{ background: gradient }} />
      {breaks.length > 0 ? (
        <ol className="relative mt-1 flex justify-between text-2xs text-fg-muted" aria-label={`${spec.title} breaks`}>
          {breaks.map((b, i) => (
            <li key={`${b}-${i}`} className="num">
              {fmt(b)}
            </li>
          ))}
        </ol>
      ) : null}
      {spec.unit ? <p className="mt-0.5 text-2xs text-fg-muted">Unit: {spec.unit}</p> : null}
    </div>
  );
}

export interface LegendProps {
  legends: readonly LegendSpec[];
  /** "View as table" for a legend's layer (shown when the legend has a `layerId`) */
  onViewTable?: (layerId: string) => void;
  className?: string;
  /** start collapsed (e.g. on small screens); small maps start collapsed anyway */
  defaultCollapsed?: boolean;
}

/**
 * Map legend: categorical swatches (with labels and hatches) or a ramp with numeric breaks.
 *
 * Inside a `MapView` it sizes itself against the map (see `map-overlay.tsx`): it starts
 * collapsed when the map is small, never grows into the controls column or past the map's top
 * edge, and registers its box so fit-to-data keeps data out from under it.
 */
export function Legend({ legends, onViewTable, className, defaultCollapsed = false }: LegendProps) {
  const layout = useMapOverlay();
  const obstacleRef = useMapObstacle("legend");
  // Collapsed by default when asked to or when the map is small. A click wins over that default
  // until the map crosses the size threshold again (e.g. the results drawer opens).
  const autoCollapsed = defaultCollapsed || layout.legendCollapsed;
  const [choice, setChoice] = React.useState<{ collapsed: boolean; auto: boolean } | null>(null);
  const collapsed = choice !== null && choice.auto === autoCollapsed ? choice.collapsed : autoCollapsed;
  const contentId = React.useId();
  if (legends.length === 0) return null;
  return (
    <section
      ref={obstacleRef}
      aria-label="Map legend"
      data-map-overlay="legend"
      className={cn(
        "pointer-events-auto rounded border border-border bg-surface/95 text-xs text-fg",
        // Collapsed it is just its header, so it hides as little of the map as possible.
        collapsed ? "w-max" : "w-60",
        layout.legendMaxWidth === null && "max-w-[calc(100vw-2rem)]",
        className,
      )}
      style={layout.legendMaxWidth !== null ? { maxWidth: layout.legendMaxWidth } : undefined}
    >
      <button
        type="button"
        className="flex w-full cursor-pointer items-center justify-between gap-2 rounded px-2.5 py-1.5 text-left font-medium hover:bg-elevated pointer-coarse:min-h-11"
        aria-expanded={!collapsed}
        aria-controls={contentId}
        onClick={() => setChoice({ collapsed: !collapsed, auto: autoCollapsed })}
      >
        Legend
        <ChevronDown aria-hidden className={cn("size-4 text-fg-muted transition-transform duration-200", collapsed && "-rotate-90")} />
      </button>
      <div
        id={contentId}
        hidden={collapsed}
        className={cn(
          "space-y-3 overflow-y-auto border-t border-border px-2.5 py-2 scrollbar-thin",
          layout.legendBodyMaxHeight === null && "max-h-[30dvh]",
        )}
        style={layout.legendBodyMaxHeight !== null ? { maxHeight: layout.legendBodyMaxHeight } : undefined}
      >
        {legends.map((spec) => (
          <div key={spec.id}>
            <div className="mb-1 flex items-center justify-between gap-2">
              <h3 className="truncate font-medium">{spec.title}</h3>
              {spec.layerId && onViewTable ? (
                <IconButton
                  label={`View ${spec.title} as table`}
                  icon={<Table2 />}
                  size="icon-sm"
                  className="size-6"
                  side="right"
                  onClick={() => onViewTable(spec.layerId as string)}
                />
              ) : null}
            </div>
            {spec.kind === "ramp" ? (
              <RampBlock spec={spec} />
            ) : (
              <ul className="space-y-1">
                {(spec.items ?? []).map((item) => (
                  <li key={`${item.label}-${item.color}`} className="flex items-center gap-2">
                    <Swatch item={item} />
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    {item.detail ? <span className="num shrink-0 text-fg-muted">{item.detail}</span> : null}
                  </li>
                ))}
              </ul>
            )}
            {spec.note ? <p className="mt-1 text-2xs leading-snug text-fg-muted">{spec.note}</p> : null}
          </div>
        ))}
      </div>
    </section>
  );
}
