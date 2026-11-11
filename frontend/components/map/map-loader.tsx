"use client";

/**
 * Client-only map entry point. MapLibre and deck.gl touch `window`/WebGL at import time, so
 * they are loaded with `next/dynamic({ ssr: false })` and a skeleton of the same size is shown
 * meanwhile (no layout shift). Use this component, never `map-view.tsx` directly, from pages.
 *
 *   const mapRef = useRef<MapViewHandle>(null);
 *   <MapLoader ref={mapRef} layers={layers} bbox={bbox} ... />
 */

import * as React from "react";
import dynamic from "next/dynamic";
import { Map as MapIcon } from "lucide-react";
import type { MapViewHandle, MapViewProps } from "./map-view";

export type { MapFocusTarget, MapViewHandle, MapViewProps, PointerModifiers, MapCameraOptions } from "./map-view";

/** Placeholder while the map bundle loads (fills its container). */
export function MapSkeleton({ label = "Loading map" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="relative flex h-full w-full items-center justify-center bg-surface">
      <div aria-hidden className="absolute inset-0 animate-pulse bg-elevated/60" />
      <span className="relative inline-flex items-center gap-2 text-sm text-fg-muted">
        <MapIcon aria-hidden className="size-4" />
        {label}
      </span>
    </div>
  );
}

const DynamicMap = dynamic(() => import("./map-view").then((m) => m.MapViewWithHandle), {
  ssr: false,
  loading: () => <MapSkeleton />,
});

/** The workspace map (client-only). Forwards its ref to the `MapViewHandle`. */
export const MapLoader = React.forwardRef<MapViewHandle, MapViewProps>(function MapLoader(props, ref) {
  return (
    <React.Suspense fallback={<MapSkeleton />}>
      <DynamicMap {...props} handleRef={ref} />
    </React.Suspense>
  );
});
