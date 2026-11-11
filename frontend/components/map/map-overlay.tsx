"use client";

/**
 * Layout shared by the overlays drawn over the map:
 *
 *   top-left       "basemap unavailable" notice (only when it failed),    `MapView`
 *                  then the plan toolbar and its cards (`topLeft`)      `OperationInput`
 *   top-right      map controls (zoom, fit, pan, 2D/3D, shortcuts)      `MapControls`
 *   bottom-left    legend, just above the scale bar                       `Legend`
 *   bottom-right   coordinate readout, then attribution                  `MapView`
 *
 * `MapView` measures its frame and the overlay boxes and publishes the result through
 * `MapOverlayContext`, so every overlay sizes itself against the map rather than the viewport
 * (side panels and the results drawer can leave a small map on a large screen): the legend
 * starts collapsed on small maps and never grows into the controls or past the top edge, the
 * controls fold the pan pad away when the column would run into the bottom-right stack, and the
 * readout and attribution share one stack so they cannot overlap. Overlays that cover the map
 * register their box with `useMapObstacle`; fit-to-data and keyboard focus keep data clear of
 * every registered box.
 */

import * as React from "react";

/** Gap between overlays, and between an overlay and the map edge (Tailwind `2`). */
export const OVERLAY_GAP = 8;
/** Gap inside the right-hand column: readout to attribution, controls to readout (Tailwind `1`). */
export const STACK_GAP = 4;
/** Bottom offset of the legend (Tailwind `bottom-10`): just above the scale bar. */
export const LEGEND_BOTTOM = 40;
/** Maps narrower or shorter than this start with the legend collapsed. */
export const LEGEND_OPEN_MIN_WIDTH = 640;
export const LEGEND_OPEN_MIN_HEIGHT = 480;
/** Narrower maps hide the coordinate readout: it would run into the legend and the scale. */
export const READOUT_MIN_WIDTH = 560;
/** Narrower maps fold the attribution behind an (i) button once the map has been used. */
export const ATTRIBUTION_INLINE_MIN_WIDTH = 640;
/** Width kept free for the scale bar (10 px margin + 120 px max width + gap). */
export const SCALE_RESERVE = 138;

/**
 * The sizes above (and the button sizes in `map-controls.tsx`) are Tailwind rem sizes at the
 * default 16 px root font size; this scales them to the user's browser font size.
 */
export function remScale(rem: number | undefined): number {
  return rem !== undefined && rem > 0 ? rem / 16 : 1;
}

/** Overlays that register their box with the map. */
export type MapObstacle = "controls" | "legend" | "notice" | "plan";

/** What `MapView` measures (CSS pixels). */
export interface MapOverlayMeasurements {
  /** map frame; 0 until measured */
  width: number;
  height: number;
  /** width of the controls column */
  controlsWidth: number;
  /** box of the bottom-right stack (readout + attribution) */
  stackWidth: number;
  stackHeight: number;
  /** root font size; the overlays' Tailwind sizes grow with it */
  rem: number;
}

export interface MapOverlayLayout {
  /** Map frame size; 0 until it has been measured. */
  width: number;
  height: number;
  /** The primary pointer is coarse: buttons are 44 px instead of 32 px. */
  coarse: boolean;
  /** Touch only (coarse pointer, no hover): there is no keyboard to need shortcut help. */
  touchOnly: boolean;
  /** Root font size / 16: multiply 16 px-based Tailwind sizes by this (see `remScale`). */
  remScale: number;
  /** Height the controls column may use (top edge to the bottom-right stack); null = unbounded. */
  controlsMaxHeight: number | null;
  /** The map is too small for an open legend: it starts collapsed. */
  legendCollapsed: boolean;
  /** Widest the legend may be without reaching the controls column; null = no limit known. */
  legendMaxWidth: number | null;
  /** Tallest the legend body (below its header) may be while staying inside the map. */
  legendBodyMaxHeight: number | null;
  /** Register (element) or unregister (null) an overlay box. */
  registerObstacle: (key: MapObstacle, element: HTMLElement | null) => void;
}

/** Pure layout rules, from the measured boxes. */
export function computeOverlayLayout(
  m: MapOverlayMeasurements,
  coarse: boolean,
): Pick<MapOverlayLayout, "remScale" | "controlsMaxHeight" | "legendCollapsed" | "legendMaxWidth" | "legendBodyMaxHeight"> {
  const { width: w, height: h } = m;
  const s = remScale(m.rem);
  if (w <= 0 || h <= 0) {
    return { remScale: s, controlsMaxHeight: null, legendCollapsed: false, legendMaxWidth: null, legendBodyMaxHeight: null };
  }
  const gap = OVERLAY_GAP * s;
  // Distance from the bottom edge to the top of the bottom-right stack. The controls end above
  // it with the stack's own gap, so the right edge reads as one column.
  const stackTop = m.stackHeight > 0 ? gap + m.stackHeight : 0;
  const controlsMaxHeight = Math.max(0, Math.floor(h - gap - stackTop - (stackTop > 0 ? STACK_GAP * s : gap)));
  // The legend (bottom-left) stays left of the controls column, and of the bottom-right stack
  // when that rises above the legend's bottom edge.
  const rightReserve = Math.max(m.controlsWidth, stackTop > LEGEND_BOTTOM * s ? m.stackWidth : 0);
  const legendMaxWidth = Math.max(120, Math.floor(w - 2 * gap - (rightReserve > 0 ? rightReserve + gap : 0)));
  // Header row (py-1.5 + text-xs, 44 px on touch) and borders stay inside the map; the body scrolls.
  const header = (coarse ? 44 : 28) * s + 3;
  const legendBodyMaxHeight = Math.max(48, Math.min(Math.round(h * 0.35), Math.floor(h - (LEGEND_BOTTOM + OVERLAY_GAP) * s - header)));
  return {
    remScale: s,
    controlsMaxHeight,
    legendCollapsed: w < LEGEND_OPEN_MIN_WIDTH || h < LEGEND_OPEN_MIN_HEIGHT,
    legendMaxWidth,
    legendBodyMaxHeight,
  };
}

const NO_LAYOUT: MapOverlayLayout = {
  width: 0,
  height: 0,
  coarse: false,
  touchOnly: false,
  remScale: 1,
  controlsMaxHeight: null,
  legendCollapsed: false,
  legendMaxWidth: null,
  legendBodyMaxHeight: null,
  registerObstacle: () => undefined,
};

/** Provided by `MapView` around its overlay children. Outside a map: no limits. */
export const MapOverlayContext = React.createContext<MapOverlayLayout>(NO_LAYOUT);

export function useMapOverlay(): MapOverlayLayout {
  return React.useContext(MapOverlayContext);
}

/** Callback ref that registers an overlay box with the surrounding map. */
export function useMapObstacle(key: MapObstacle): (element: HTMLElement | null) => void {
  const { registerObstacle } = useMapOverlay();
  return React.useCallback((element: HTMLElement | null) => registerObstacle(key, element), [registerObstacle, key]);
}
