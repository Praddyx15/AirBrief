"use client";

import * as React from "react";
import Link from "next/link";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Compass,
  Keyboard,
  Maximize2,
  Minus,
  Move,
  Plus,
} from "lucide-react";
import type { BasemapId } from "@/lib/store";
import { cn } from "@/lib/utils";
import { IconButton } from "@/components/ui/icon-button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { MapViewHandle } from "./map-view";
import { OVERLAY_GAP, useMapObstacle, useMapOverlay } from "./map-overlay";

const PAN_PX = 160;

export interface MapControlsProps {
  mapRef: React.RefObject<MapViewHandle | null>;
  view3d: boolean;
  onView3dChange: (on: boolean) => void;
  /** fit to the data; disabled when there is nothing to fit */
  onFit?: () => void;
  basemap: BasemapId;
  onShowShortcuts?: () => void;
  className?: string;
}

/**
 * How the controls are arranged:
 * - `full`: one column with the 3×3 pan pad;
 * - `folded`: the pan pad sits behind a "Pan the map" button (popover);
 * - `split`: folded, and the secondary controls move into a second column on the left.
 * `scroll` is the last resort for maps too short even for that.
 */
type ControlsArrangement = { layout: "full" | "folded" | "split"; scroll: boolean };

/**
 * Pick the first arrangement whose height fits. Heights come from the Tailwind classes used
 * below (icon-sm buttons are 32 px, 44 px on touch; groups have a 1 px border; 8 px gaps), at
 * the default 16 px root font size; `remScale` scales the rem-sized parts to the user's.
 */
export function arrangeControls(options: {
  available: number | null;
  coarse: boolean;
  view3d: boolean;
  showKeys: boolean;
  showNote: boolean;
  remScale?: number;
}): ControlsArrangement {
  const { available, coarse, view3d, showKeys, showNote, remScale = 1 } = options;
  if (available === null) return { layout: "full", scroll: false };
  const button = (coarse ? 44 : 32) * remScale;
  const zoom = 2 + 3 * button + 1 + (view3d ? button : 0); // zoom in/out, divider, fit (+ reset north)
  const pad = 2 + 3 * button;
  const single = 2 + button;
  const perspective = 2 + (coarse ? 48 : 32) * remScale; // ToggleGroup: border + p-0.5 + h-7 (min-h-11 on touch)
  // "No basemap" note: border + py-1 + up to three lines of text-xs (it wraps at max-w-44)
  const note = 2 + 56 * remScale;
  const rest = [perspective, ...(showKeys ? [single] : []), ...(showNote ? [note] : [])];
  const gap = OVERLAY_GAP * remScale;
  const column = (items: number[]) => items.reduce((a, b) => a + b, 0) + gap * Math.max(0, items.length - 1);
  if (column([zoom, pad, ...rest]) <= available) return { layout: "full", scroll: false };
  if (column([zoom, single, ...rest]) <= available) return { layout: "folded", scroll: false };
  return { layout: "split", scroll: Math.max(zoom, column([single, ...rest])) > available };
}

function PanPad({ onPan, tooltipSide, labelled = true }: { onPan: (dx: number, dy: number) => void; tooltipSide: "left" | "top"; labelled?: boolean }) {
  return (
    <div
      role={labelled ? "group" : undefined}
      aria-label={labelled ? "Pan the map" : undefined}
      className="grid grid-cols-3 rounded border border-border bg-surface/95"
    >
      <span aria-hidden />
      <IconButton label="Pan north" side={tooltipSide} size="icon-sm" icon={<ArrowUp />} onClick={() => onPan(0, -PAN_PX)} />
      <span aria-hidden />
      <IconButton label="Pan west" side={tooltipSide} size="icon-sm" icon={<ArrowLeft />} onClick={() => onPan(-PAN_PX, 0)} />
      <span aria-hidden />
      <IconButton label="Pan east" side={tooltipSide} size="icon-sm" icon={<ArrowRight />} onClick={() => onPan(PAN_PX, 0)} />
      <span aria-hidden />
      <IconButton label="Pan south" side={tooltipSide} size="icon-sm" icon={<ArrowDown />} onClick={() => onPan(0, PAN_PX)} />
      <span aria-hidden />
    </div>
  );
}

/**
 * On-map controls (top-right): zoom, pan buttons (the WCAG 2.2 alternative to dragging), fit
 * to data, reset north, 2D/3D and keyboard help. Every button has a label and a tooltip.
 *
 * The column is sized against the map (see `map-overlay.tsx`): when it would run into the
 * readout and attribution, the pan pad folds behind a "Pan the map" button, then the secondary
 * controls move into a second column. Touch-only devices get no keyboard help button.
 */
export function MapControls({ mapRef, view3d, onView3dChange, onFit, basemap, onShowShortcuts, className }: MapControlsProps) {
  const layout = useMapOverlay();
  const obstacleRef = useMapObstacle("controls");
  const pan = (dx: number, dy: number) => mapRef.current?.panBy([dx, dy]);
  const showKeys = Boolean(onShowShortcuts) && !layout.touchOnly;
  const showNote = basemap === "none";
  const { layout: arrangement, scroll } = arrangeControls({
    available: layout.controlsMaxHeight,
    coarse: layout.coarse,
    view3d,
    showKeys,
    showNote,
    remScale: layout.remScale,
  });

  const zoomBar = (
    <div role="toolbar" aria-label="Map view" aria-orientation="vertical" className="flex flex-col rounded border border-border bg-surface/95">
      <IconButton label="Zoom in" shortcut="+" side="left" size="icon-sm" icon={<Plus />} onClick={() => mapRef.current?.zoomIn()} />
      <IconButton label="Zoom out" shortcut="-" side="left" size="icon-sm" icon={<Minus />} onClick={() => mapRef.current?.zoomOut()} />
      <span aria-hidden className="mx-1.5 h-px bg-border" />
      <IconButton
        label="Fit to data"
        shortcut="F"
        side="left"
        size="icon-sm"
        icon={<Maximize2 />}
        onClick={onFit}
        disabled={!onFit}
      />
      {view3d ? (
        <IconButton
          label="Reset north and tilt to 50°"
          side="left"
          size="icon-sm"
          icon={<Compass />}
          onClick={() => mapRef.current?.resetNorth()}
        />
      ) : null}
    </div>
  );

  const panControl =
    arrangement === "full" ? (
      <PanPad onPan={pan} tooltipSide="left" />
    ) : (
      <Popover>
        <div className="rounded border border-border bg-surface/95">
          <PopoverTrigger asChild>
            <IconButton label="Pan the map" side="left" size="icon-sm" icon={<Move />} />
          </PopoverTrigger>
        </div>
        <PopoverContent side="left" align="start" aria-label="Pan the map" className="w-auto p-1" data-print="hide">
          <PanPad onPan={pan} tooltipSide="top" labelled={false} />
        </PopoverContent>
      </Popover>
    );

  const secondary = (
    <>
      <ToggleGroup
        type="single"
        size="sm"
        aria-label="Map perspective"
        value={view3d ? "3d" : "2d"}
        onValueChange={(v) => {
          if (v === "2d" || v === "3d") onView3dChange(v === "3d");
        }}
        className="bg-surface/95"
      >
        <ToggleGroupItem value="2d" aria-label="2D map view">
          2D
        </ToggleGroupItem>
        <ToggleGroupItem value="3d" aria-label="3D map view, tilted 50 degrees">
          3D
        </ToggleGroupItem>
      </ToggleGroup>

      {showKeys ? (
        <div className="rounded border border-border bg-surface/95">
          <IconButton label="Keyboard shortcuts" shortcut="?" side="left" size="icon-sm" icon={<Keyboard />} onClick={onShowShortcuts} />
        </div>
      ) : null}

      {showNote ? (
        <p className="max-w-44 rounded border border-border bg-surface/95 px-2 py-1 text-right text-xs text-fg-muted">
          No basemap (offline mode).{" "}
          <Link href="/settings" className="text-accent-text underline">
            Change in Settings
          </Link>
        </p>
      ) : null}
    </>
  );

  return (
    <div
      ref={obstacleRef}
      data-map-overlay="controls"
      data-print="hide"
      className={cn(
        "pointer-events-auto flex gap-2",
        // split: the zoom bar stays on the right edge, the rest becomes a column to its left
        arrangement === "split" ? "flex-row-reverse items-start" : "flex-col items-end",
        // room for focus outlines inside the scroll box
        scroll && "-m-1 overflow-y-auto overscroll-contain p-1 scrollbar-thin",
        className,
      )}
      // + the p-1 around the scroll box (the -m-1 keeps the column where it was)
      style={scroll && layout.controlsMaxHeight !== null ? { maxHeight: layout.controlsMaxHeight + 8 * layout.remScale } : undefined}
    >
      {arrangement === "split" ? (
        <>
          {zoomBar}
          <div className="flex flex-col items-end gap-2">
            {panControl}
            {secondary}
          </div>
        </>
      ) : (
        <>
          {zoomBar}
          {panControl}
          {secondary}
        </>
      )}
    </div>
  );
}
