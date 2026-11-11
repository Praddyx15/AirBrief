"use client";

/**
 * MapLibre map with a deck.gl overlay (`MapboxOverlay`, `interleaved: false`).
 *
 * Client-only: import it through `map-loader.tsx` (`next/dynamic`, `ssr: false`).
 *
 * - Basemap: OpenFreeMap "dark" / "positron" per theme, or an offline blank style whose
 *   background is the theme's `--bg` when the basemap setting is "none" (or when the
 *   OpenFreeMap style cannot be loaded, e.g. offline).
 * - Camera: fits `bbox` whenever `fitKey` (or the bbox) changes; jumps instead of animating
 *   under reduced motion. Until the user moves the camera, the last fit is redone when the map
 *   or an overlay changes size (side panels, results drawer, legend). 3D mode pitches to 50° and
 *   enables rotate/tilt.
 * - Overlays: `children` are laid out with `map-overlay.tsx` (sized against the map frame, not
 *   the viewport). Fit-to-data and keyboard focus keep data clear of the controls, the legend,
 *   the scale bar and the bottom-right stack (readout and attribution).
 * - Keyboard: MapLibre defaults (arrows pan, +/- zoom, Shift+arrows rotate/tilt in 3D) plus
 *   N / Shift+N to cycle `focusTargets`, Enter to select, Esc to clear, F to fit, ? for help.
 * - Picking: `onHover` / `onClick` receive deck.gl PickingInfo, and `onClick` the Shift / Ctrl /
 *   ⌘ state (box zoom is off so that Shift+click reaches the map); `getTooltip` renders a small
 *   hover label next to the pointer.
 * - Tab order: the MapLibre canvas is the map's only tab stop (deck.gl's canvas is taken out of
 *   the tab order and the accessibility tree), then the overlays in visual order: controls,
 *   legend, readout, attribution.
 */

import * as React from "react";
import {
  Map as MlMap,
  ScaleControl,
  setWorkerUrl,
  type IControl,
  type MapMouseEvent,
  type MapMovementEvent,
  type MapSourceDataEvent,
  type StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { MapboxOverlay } from "@deck.gl/mapbox";
import type { Layer, PickingInfo } from "@deck.gl/core";
import { readCssVar } from "@/lib/colors";
import type { BasemapId, Units } from "@/lib/store";
import type { BBoxList } from "@/lib/types";
import { BASE_PATH, cn } from "@/lib/utils";
import { useMediaQuery } from "@/components/workspace/use-media-query";
import { CoordinateReadout } from "./coordinate-readout";
import { MapAttribution, parseAttributions } from "./map-attribution";
import {
  ATTRIBUTION_INLINE_MIN_WIDTH,
  MapOverlayContext,
  OVERLAY_GAP,
  READOUT_MIN_WIDTH,
  SCALE_RESERVE,
  computeOverlayLayout,
  type MapObstacle,
  type MapOverlayLayout,
  type MapOverlayMeasurements,
} from "./map-overlay";

// MapLibre 6 loads its worker from a file next to its own module, which bundling moves; the file
// is copied to public/vendor/ by scripts/copy-maplibre-worker.mjs. Must run before the first map.
if (typeof window !== "undefined") setWorkerUrl(`${BASE_PATH}/vendor/maplibre-gl-worker.mjs`);

/** OpenFreeMap style URLs (verified: both are MapLibre v8 styles). */
export const OPENFREEMAP_STYLES = {
  dark: "https://tiles.openfreemap.org/styles/dark",
  light: "https://tiles.openfreemap.org/styles/positron",
} as const;

/** Offline style: just a background in the theme colour (no network requests). */
export function blankStyle(background: string): StyleSpecification {
  return {
    version: 8,
    name: "AirBrief blank",
    sources: {},
    layers: [{ id: "background", type: "background", paint: { "background-color": background } }],
  };
}

function styleFor(basemap: BasemapId, theme: "dark" | "light", forceBlank: boolean): string | StyleSpecification {
  if (basemap === "none" || forceBlank) return blankStyle(readCssVar("bg") || (theme === "dark" ? "#0b0e11" : "#f8fafc"));
  return OPENFREEMAP_STYLES[theme];
}

/** A feature reachable with N / Shift+N. */
export interface MapFocusTarget {
  index: number;
  label: string;
  /** [lon, lat] used to bring the feature into view */
  position: [number, number];
}

export interface MapCameraOptions {
  center: [number, number];
  zoom?: number;
  pitch?: number;
  bearing?: number;
}

export interface MapViewHandle {
  fitBounds: (bbox: BBoxList, options?: { padding?: number; maxZoom?: number }) => void;
  flyTo: (options: MapCameraOptions) => void;
  getCanvas: () => HTMLCanvasElement | null;
  zoomIn: () => void;
  zoomOut: () => void;
  /** pan by screen pixels */
  panBy: (offset: [number, number]) => void;
  /** face north; in 3D the tilt goes back to 50° (the 3D view stays 3D), in 2D the map stays flat */
  resetNorth: () => void;
  /** move keyboard focus to the map canvas */
  focus: () => void;
  getMap: () => MlMap | null;
}

export interface PointerModifiers {
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
}

export interface MapViewProps {
  layers: readonly Layer[];
  /** data extent to fit */
  bbox: BBoxList | null;
  /** refit when this changes (defaults to the bbox values) */
  fitKey?: string | null;
  basemap: BasemapId;
  theme: "dark" | "light";
  view3d: boolean;
  reducedMotion: boolean;
  units: Units;
  focusTargets?: readonly MapFocusTarget[];
  focusedFeature?: number | null;
  onFocusFeature?: (index: number | null) => void;
  /** Enter on the focused feature */
  onSelectFeature?: (index: number) => void;
  /** Esc with no keyboard focus */
  onClearSelection?: () => void;
  onHover?: (info: PickingInfo | null) => void;
  onClick?: (info: PickingInfo, modifiers: PointerModifiers) => void;
  getTooltip?: (info: PickingInfo) => string | null;
  /** ? key */
  onShowShortcuts?: () => void;
  /** F key; defaults to fitting `bbox` */
  onFit?: () => void;
  /** element id for the "Skip to map" link (focus is forwarded to the canvas) */
  id?: string;
  ariaLabel?: string;
  /** shown before the first fit (no bbox yet) */
  initialView?: { center: [number, number]; zoom: number };
  showReadout?: boolean;
  className?: string;
  /** overlays (controls, legend) positioned over the map; see `map-overlay.tsx` */
  children?: React.ReactNode;
  /** overlays stacked in the top-left corner, below the basemap notice (e.g. a toolbar) */
  topLeft?: React.ReactNode;
  /** pointer cursor over the map (e.g. "crosshair" while drawing); replaces the hover pointer */
  cursor?: string;
}

const DEFAULT_VIEW = { center: [0, 20] as [number, number], zoom: 1.5 };
const MAX_FIT_ZOOM = 16;
const CAMERA_MS = 600;
/** A narrow map's attribution folds behind its (i) button this long after the map loads. */
const ATTRIBUTION_FOLD_MS = 5000;

function bboxKey(b: BBoxList | null): string {
  return b ? b.map((v) => v.toFixed(6)).join(",") : "";
}

type FitPadding = { top: number; bottom: number; left: number; right: number };
type FitOptions = { padding?: number | FitPadding; maxZoom?: number };

/**
 * Padding for fitting data: a margin on every side, widened on one side per overlay box (the
 * controls, the legend, the scale bar, the readout and attribution) so that no data ends up
 * underneath them. Each box widens the side that costs the map the least.
 */
function fitPadding(container: HTMLElement | null, overlays: readonly Element[] = []): FitPadding {
  if (!container || container.clientWidth < 1 || container.clientHeight < 1) return { top: 40, bottom: 40, left: 40, right: 40 };
  const { clientWidth: w, clientHeight: h } = container;
  const base = Math.max(12, Math.min(56, Math.floor(Math.min(w, h) / 8)));
  const margin = Math.round(base / 2);
  const pad: FitPadding = { top: base, bottom: base, left: base, right: base };
  const frame = container.getBoundingClientRect();
  for (const el of overlays) {
    const r = el.getBoundingClientRect();
    const x0 = Math.max(0, r.left - frame.left);
    const x1 = Math.min(w, r.right - frame.left);
    const y0 = Math.max(0, r.top - frame.top);
    const y1 = Math.min(h, r.bottom - frame.top);
    if (x1 - x0 < 1 || y1 - y0 < 1) continue; // hidden, or outside the map
    // The box hugs its nearest vertical and horizontal edge; clear it from the cheaper one.
    const horizontal: [keyof FitPadding, number] = x0 < w - x1 ? ["left", x1] : ["right", w - x0];
    const vertical: [keyof FitPadding, number] = y0 < h - y1 ? ["top", y1] : ["bottom", h - y0];
    const [side, clearance] = horizontal[1] / w <= vertical[1] / h ? horizontal : vertical;
    pad[side] = Math.max(pad[side], clearance + margin);
  }
  // Never ask for more padding than the map has room for (fitBounds would refuse to move).
  const sx = Math.min(1, (w * 0.6) / (pad.left + pad.right));
  const sy = Math.min(1, (h * 0.6) / (pad.top + pad.bottom));
  return {
    top: Math.round(pad.top * sy),
    bottom: Math.round(pad.bottom * sy),
    left: Math.round(pad.left * sx),
    right: Math.round(pad.right * sx),
  };
}

function toBounds(b: BBoxList): [[number, number], [number, number]] {
  // Pad degenerate (point / line) extents so fitBounds has an area to fit.
  const eps = 0.0005;
  const w = b[2] - b[0] < eps ? eps : 0;
  const h = b[3] - b[1] < eps ? eps : 0;
  return [
    [b[0] - w, b[1] - h],
    [b[2] + w, b[3] + h],
  ];
}

const NO_MEASUREMENTS: MapOverlayMeasurements = { width: 0, height: 0, controlsWidth: 0, stackWidth: 0, stackHeight: 0, rem: 16 };

function sameMeasurements(a: MapOverlayMeasurements, b: MapOverlayMeasurements): boolean {
  return (
    a.width === b.width &&
    a.height === b.height &&
    a.controlsWidth === b.controlsWidth &&
    a.stackWidth === b.stackWidth &&
    a.stackHeight === b.stackHeight &&
    a.rem === b.rem
  );
}

export const MapView = React.forwardRef<MapViewHandle, MapViewProps>(function MapView(props, ref) {
  const {
    layers,
    bbox,
    fitKey,
    basemap,
    theme,
    view3d,
    // reducedMotion is read through propsRef (camera callbacks need the latest value)
    units,
    focusTargets,
    id,
    ariaLabel = "Map",
    initialView,
    showReadout = true,
    topLeft,
    cursor,
    className,
    children,
  } = props;

  const containerRef = React.useRef<HTMLDivElement>(null);
  const mapRef = React.useRef<MlMap | null>(null);
  const overlayRef = React.useRef<MapboxOverlay | null>(null);
  const scaleRef = React.useRef<ScaleControl | null>(null);
  const lastFitRef = React.useRef<string | null>(null);
  const styleLoadedRef = React.useRef(false);
  const styleKeyRef = React.useRef<string>("");

  // Latest props for callbacks registered once.
  const propsRef = React.useRef(props);
  propsRef.current = props;

  const [ready, setReady] = React.useState(false);
  const readyRef = React.useRef(ready);
  readyRef.current = ready;
  const [basemapFailed, setBasemapFailed] = React.useState(false);
  const [tooltip, setTooltip] = React.useState<{ x: number; y: number; text: string } | null>(null);
  const [coord, setCoord] = React.useState<{ lngLat: [number, number]; isCenter: boolean } | null>(null);
  const [zoom, setZoom] = React.useState<number | null>(null);
  const [announcement, setAnnouncement] = React.useState("");
  const [attributions, setAttributions] = React.useState<string[]>([]);
  const [attributionOpen, setAttributionOpen] = React.useState(true);
  /** the user opened the attribution: the load-time timer must not fold it */
  const attributionPinnedRef = React.useRef(false);

  // The same queries as Tailwind's `pointer-coarse` variant, so the sizes computed here match CSS.
  const coarse = useMediaQuery("(pointer: coarse)");
  const touchOnly = useMediaQuery("(hover: none) and (pointer: coarse)");
  const [measurements, setMeasurements] = React.useState<MapOverlayMeasurements>(NO_MEASUREMENTS);
  const obstaclesRef = React.useRef(new Map<MapObstacle, HTMLElement>());
  const stackRef = React.useRef<HTMLDivElement>(null);
  const overlayObserverRef = React.useRef<ResizeObserver | null>(null);
  const overlaySizesRef = React.useRef(new WeakMap<Element, { w: number; h: number }>());

  // last fit, redone on resize till the user moves the camera
  const fitTargetRef = React.useRef<{ bbox: BBoxList; options?: FitOptions } | null>(null);
  const cameraTouchedRef = React.useRef(false);
  const refitRef = React.useRef({ frame: 0, animate: true });

  const duration = React.useCallback((ms: number) => (propsRef.current.reducedMotion ? 0 : ms), []);

  /** Boxes that data should stay clear of: registered overlays, the readout stack, the scale. */
  const overlayBoxes = React.useCallback((): Element[] => {
    const boxes: Element[] = [...obstaclesRef.current.values()];
    if (stackRef.current) boxes.push(stackRef.current);
    const scale = containerRef.current?.querySelector(".maplibregl-ctrl-scale");
    if (scale) boxes.push(scale);
    return boxes;
  }, []);

  const doFit = React.useCallback(
    (b: BBoxList, options?: FitOptions, animate = true) => {
      const map = mapRef.current;
      if (!map) return;
      fitTargetRef.current = { bbox: b, options };
      cameraTouchedRef.current = false;
      const padding = options?.padding ?? fitPadding(containerRef.current, overlayBoxes());
      const move = animate && !propsRef.current.reducedMotion;
      map.fitBounds(toBounds(b), {
        padding,
        maxZoom: options?.maxZoom ?? MAX_FIT_ZOOM,
        duration: move ? CAMERA_MS : 0,
        animate: move,
        pitch: propsRef.current.view3d ? 50 : 0,
      });
    },
    [overlayBoxes],
  );
  const doFitRef = React.useRef(doFit);
  doFitRef.current = doFit;

  /** Redo the last fit on the next frame (batched), unless the user has moved the camera since. */
  const scheduleRefit = React.useCallback((animate: boolean) => {
    if (cameraTouchedRef.current || !fitTargetRef.current) return;
    const pending = refitRef.current;
    pending.animate = pending.frame ? pending.animate && animate : animate;
    if (pending.frame) return;
    pending.frame = window.requestAnimationFrame(() => {
      pending.frame = 0;
      const target = fitTargetRef.current;
      if (!target || cameraTouchedRef.current) return;
      doFitRef.current(target.bbox, target.options, pending.animate);
    });
  }, []);

  const remeasure = React.useCallback(() => {
    const frame = containerRef.current;
    if (!frame) return;
    const controls = obstaclesRef.current.get("controls");
    const stack = stackRef.current;
    const next: MapOverlayMeasurements = {
      width: frame.clientWidth,
      height: frame.clientHeight,
      controlsWidth: controls?.offsetWidth ?? 0,
      // Rounded up to 16 px: the readout's width follows the pointer coordinates.
      stackWidth: stack ? Math.ceil(stack.offsetWidth / 16) * 16 : 0,
      stackHeight: stack?.offsetHeight ?? 0,
      // A larger browser font size makes every rem-sized overlay larger (the overlays then resize,
      // which brings us back here).
      rem: Number.parseFloat(window.getComputedStyle(document.documentElement).fontSize) || 16,
    };
    setMeasurements((prev) => (sameMeasurements(prev, next) ? prev : next));
  }, []);

  const overlayObserver = React.useCallback((): ResizeObserver | null => {
    if (overlayObserverRef.current || typeof ResizeObserver === "undefined") return overlayObserverRef.current;
    overlayObserverRef.current = new ResizeObserver((entries) => {
      let moved = false;
      for (const entry of entries) {
        const el = entry.target as HTMLElement;
        const size = { w: el.offsetWidth, h: el.offsetHeight };
        const prev = overlaySizesRef.current.get(el);
        overlaySizesRef.current.set(el, size);
        // The stack's width follows the readout text: only its height changes the fit.
        if (!prev || prev.h !== size.h || (el !== stackRef.current && prev.w !== size.w)) moved = true;
      }
      remeasure();
      if (moved) scheduleRefit(readyRef.current);
    });
    return overlayObserverRef.current;
  }, [remeasure, scheduleRefit]);

  const registerObstacle = React.useCallback(
    (key: MapObstacle, element: HTMLElement | null) => {
      const registry = obstaclesRef.current;
      const prev = registry.get(key);
      if (prev === element) return;
      if (prev) {
        // No new observer just to unobserve: on unmount the observer is already gone.
        overlayObserverRef.current?.unobserve(prev);
        registry.delete(key);
      }
      if (element) {
        registry.set(key, element);
        overlayObserver()?.observe(element); // the first observation measures it and refits
      } else {
        remeasure();
        scheduleRefit(readyRef.current);
      }
    },
    [overlayObserver, remeasure, scheduleRefit],
  );
  const noticeRef = React.useCallback((element: HTMLElement | null) => registerObstacle("notice", element), [registerObstacle]);

  // Measure before the first paint, and follow the bottom-right stack.
  React.useLayoutEffect(() => {
    const ro = overlayObserver();
    if (stackRef.current) ro?.observe(stackRef.current);
    for (const el of obstaclesRef.current.values()) ro?.observe(el);
    remeasure();
    const refit = refitRef.current;
    return () => {
      if (refit.frame) window.cancelAnimationFrame(refit.frame);
      refit.frame = 0;
      overlayObserverRef.current?.disconnect();
      overlayObserverRef.current = null;
    };
  }, [overlayObserver, remeasure]);

  const overlayLayout = React.useMemo<MapOverlayLayout>(
    () => ({
      width: measurements.width,
      height: measurements.height,
      coarse,
      touchOnly,
      ...computeOverlayLayout(measurements, coarse),
      registerObstacle,
    }),
    [measurements, coarse, touchOnly, registerObstacle],
  );

  React.useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const p = propsRef.current;
    const start = initialView ?? DEFAULT_VIEW;
    const style = styleFor(p.basemap, p.theme, false);
    styleKeyRef.current = `${p.basemap}:${p.theme}`;

    const map = new MlMap({
      container,
      style,
      center: start.center,
      zoom: start.zoom,
      pitch: p.view3d ? 50 : 0,
      maxPitch: 85,
      // Rendered by the app (MapAttribution): themed, last in the tab order, clear of the scale.
      attributionControl: false,
      // Shift+drag box zoom would swallow Shift+click, which adds to the selection.
      boxZoom: false,
      dragRotate: p.view3d,
      pitchWithRotate: p.view3d,
      touchPitch: p.view3d,
      fadeDuration: p.reducedMotion ? 0 : 300,
      ...(p.bbox
        ? { bounds: toBounds(p.bbox), fitBoundsOptions: { padding: fitPadding(container, overlayBoxes()), maxZoom: MAX_FIT_ZOOM } }
        : {}),
    });
    mapRef.current = map;
    if (p.bbox) {
      lastFitRef.current = p.fitKey ?? bboxKey(p.bbox);
      fitTargetRef.current = { bbox: p.bbox };
      cameraTouchedRef.current = false;
    }
    if (!p.view3d) {
      map.keyboard.disableRotation();
      map.touchZoomRotate.disableRotation();
    }

    const scale = new ScaleControl({ unit: p.units === "imperial" ? "imperial" : "metric", maxWidth: 120 });
    map.addControl(scale, "bottom-left");
    scaleRef.current = scale;

    const overlay = new MapboxOverlay({
      interleaved: false,
      layers: [],
      pickingRadius: 5,
      onLoad: () => {
        // mjolnir.js makes deck's canvas focusable (tabIndex 0), but every map input goes through
        // the labelled MapLibre canvas: keep this one out of the tab order and the a11y tree.
        const deckCanvas = overlay.getCanvas();
        if (deckCanvas && deckCanvas !== map.getCanvas()) {
          deckCanvas.tabIndex = -1;
          deckCanvas.setAttribute("aria-hidden", "true");
        }
      },
      onHover: (info: PickingInfo) => {
        const cur = propsRef.current;
        cur.onHover?.(info.picked ? info : null);
        const canvas = map.getCanvas();
        canvas.style.cursor = cur.cursor ?? (info.picked ? "pointer" : "");
        const text = info.picked && cur.getTooltip ? cur.getTooltip(info) : null;
        setTooltip(text ? { x: info.x, y: info.y, text } : null);
      },
      onClick: (info: PickingInfo, event: { srcEvent?: unknown }) => {
        // MapboxOverlay passes MapLibre's MapMouseEvent as srcEvent; the modifier keys are on the
        // DOM event it wraps (`originalEvent`).
        const src = event?.srcEvent as (Partial<PointerModifiers> & { originalEvent?: Partial<PointerModifiers> }) | undefined;
        const dom = src?.originalEvent ?? src ?? {};
        propsRef.current.onClick?.(info, {
          shiftKey: Boolean(dom.shiftKey),
          ctrlKey: Boolean(dom.ctrlKey),
          metaKey: Boolean(dom.metaKey),
        });
      },
    });
    map.addControl(overlay as unknown as IControl);
    overlayRef.current = overlay;

    const canvas = map.getCanvas();
    canvas.setAttribute("role", "application");
    canvas.setAttribute("aria-roledescription", "interactive map");

    const onStyleLoad = () => {
      styleLoadedRef.current = true;
    };
    const onError = (e: { error?: { message?: string; status?: number; url?: string }; sourceId?: string }) => {
      // The style itself failed (offline, blocked): fall back to the blank background once. A
      // tile, sprite or font error while the style is still loading (slow link, busy machine) is
      // transient and must not drop the basemap: only an error without a URL or for the style
      // document itself counts.
      const url = e?.error?.url ?? "";
      const styleError = url === "" || Object.values(OPENFREEMAP_STYLES).some((style) => url.startsWith(style));
      if (!styleLoadedRef.current && propsRef.current.basemap === "openfreemap" && styleError && !e?.sourceId) {
        styleLoadedRef.current = true;
        setBasemapFailed(true);
      }
      if (process.env.NODE_ENV !== "production" && e?.error?.message) {
        console.warn("[map]", e.error.message);
      }
    };
    let raf = 0;
    const pending: { lngLat: [number, number]; isCenter: boolean } = { lngLat: [0, 0], isCenter: true };
    let pointerInside = false;
    const flush = () => {
      raf = 0;
      setCoord({ lngLat: [pending.lngLat[0], pending.lngLat[1]], isCenter: pending.isCenter });
      setZoom(map.getZoom());
    };
    const schedule = () => {
      if (!raf) raf = window.requestAnimationFrame(flush);
    };
    const onMouseMove = (e: MapMouseEvent) => {
      pointerInside = true;
      pending.lngLat = [e.lngLat.lng, e.lngLat.lat];
      pending.isCenter = false;
      schedule();
    };
    const onMouseOut = () => {
      pointerInside = false;
      const c = map.getCenter();
      pending.lngLat = [c.lng, c.lat];
      pending.isCenter = true;
      schedule();
      setTooltip(null);
      propsRef.current.onHover?.(null);
      map.getCanvas().style.cursor = propsRef.current.cursor ?? "";
    };
    const onMove = () => {
      if (pointerInside) {
        schedule();
        return;
      }
      const c = map.getCenter();
      pending.lngLat = [c.lng, c.lat];
      pending.isCenter = true;
      schedule();
    };
    // Drag, wheel, touch and keyboard moves carry the DOM event; fits and resizes do not.
    const onMoveStart = (e: MapMovementEvent) => {
      if (!e.originalEvent) return;
      cameraTouchedRef.current = true;
      attributionPinnedRef.current = false;
      setAttributionOpen(false);
    };
    const onLoad = () => {
      setReady(true);
      onMove();
    };

    // Attribution of the style's sources (vector sources only know theirs once their TileJSON
    // has loaded, hence the "metadata" events).
    const attributionSources = new Set<string>();
    let attributionRaf = 0;
    const readAttributions = () => {
      attributionRaf = 0;
      const found: string[] = [];
      for (const sourceId of attributionSources) {
        const text = map.getSource(sourceId)?.attribution;
        if (text && !found.includes(text)) found.push(text);
      }
      setAttributions((prev) => (prev.join("\n") === found.join("\n") ? prev : found));
    };
    const scheduleAttributions = () => {
      if (!attributionRaf) attributionRaf = window.requestAnimationFrame(readAttributions);
    };
    const onSourceData = (e: MapSourceDataEvent) => {
      if (e.sourceId) attributionSources.add(e.sourceId);
      if (e.sourceDataType === "metadata" || e.sourceDataType === "visibility") scheduleAttributions();
    };

    // The basemap style names a few sprite images it does not ship (e.g. "wood-pattern");
    // a transparent placeholder keeps MapLibre from warning on every tile.
    map.setMissingStyleImageResolver((imageId) => {
      if (!map.hasImage(imageId)) map.addImage(imageId, { width: 1, height: 1, data: new Uint8Array(4) });
    });

    map.on("style.load", onStyleLoad);
    map.on("error", onError);
    map.on("mousemove", onMouseMove);
    map.on("mouseout", onMouseOut);
    map.on("move", onMove);
    map.on("movestart", onMoveStart);
    map.on("load", onLoad);
    map.on("sourcedata", onSourceData);
    map.on("styledata", scheduleAttributions);

    // A new map size: resize the canvas, re-lay the overlays, and keep the data fitted.
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            map.resize();
            remeasure();
            scheduleRefit(false);
          })
        : null;
    ro?.observe(container);

    return () => {
      if (raf) window.cancelAnimationFrame(raf);
      if (attributionRaf) window.cancelAnimationFrame(attributionRaf);
      ro?.disconnect();
      map.off("style.load", onStyleLoad);
      map.off("error", onError);
      map.off("mousemove", onMouseMove);
      map.off("mouseout", onMouseOut);
      map.off("move", onMove);
      map.off("movestart", onMoveStart);
      map.off("load", onLoad);
      map.off("sourcedata", onSourceData);
      map.off("styledata", scheduleAttributions);
      try {
        map.removeControl(overlay as unknown as IControl);
      } catch {
        // already removed
      }
      overlay.finalize();
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
      scaleRef.current = null;
      styleLoadedRef.current = false;
      lastFitRef.current = null;
      fitTargetRef.current = null;
      setReady(false);
    };
    // Created once; later prop changes are applied by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => {
    const canvas = mapRef.current?.getCanvas();
    if (!canvas) return;
    canvas.setAttribute(
      "aria-label",
      `${ariaLabel}. Arrow keys pan, plus and minus zoom${focusTargets && focusTargets.length ? ", N and Shift+N move between features, Enter selects" : ""}, question mark lists all shortcuts.`,
    );
  }, [ariaLabel, focusTargets, ready]);

  React.useEffect(() => {
    if (!ready) return;
    const timer = window.setTimeout(() => {
      if (!attributionPinnedRef.current) setAttributionOpen(false);
    }, ATTRIBUTION_FOLD_MS);
    return () => window.clearTimeout(timer);
  }, [ready]);

  const onAttributionOpenChange = React.useCallback((open: boolean) => {
    attributionPinnedRef.current = open;
    setAttributionOpen(open);
  }, []);

  const attributionGroups = React.useMemo(() => parseAttributions(attributions), [attributions]);

  React.useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    // "none" is a blank style whether or not OpenFreeMap failed, so the failure flag must not
    // change its key (clearing it would otherwise reload an identical style).
    const blank = basemap === "none" || basemapFailed;
    const key = `${basemap}:${theme}:${blank ? "blank" : "ok"}`;
    if (styleKeyRef.current === key) return;
    const initial = styleKeyRef.current === `${basemap}:${theme}` && !basemapFailed;
    styleKeyRef.current = key;
    if (initial) return;
    styleLoadedRef.current = basemapFailed || basemap === "none";
    map.setStyle(styleFor(basemap, theme, basemapFailed), { diff: false });
  }, [basemap, theme, basemapFailed]);

  // Retry the real basemap when the user switches settings back.
  React.useEffect(() => {
    if (basemap === "none") setBasemapFailed(false);
  }, [basemap]);

  React.useEffect(() => {
    overlayRef.current?.setProps({ layers: [...layers] });
  }, [layers, ready]);

  React.useEffect(() => {
    const canvas = mapRef.current?.getCanvas();
    if (canvas) canvas.style.cursor = cursor ?? "";
  }, [cursor, ready]);

  React.useEffect(() => {
    scaleRef.current?.setUnit(units === "imperial" ? "imperial" : "metric");
  }, [units]);

  React.useEffect(() => {
    if (!bbox || !mapRef.current) return;
    const key = fitKey ?? bboxKey(bbox);
    if (lastFitRef.current === key) return;
    lastFitRef.current = key;
    doFit(bbox);
  }, [bbox, fitKey, doFit, ready]);

  React.useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (view3d) {
      map.dragRotate.enable();
      map.touchPitch.enable();
      map.keyboard.enableRotation();
      map.touchZoomRotate.enableRotation();
      map.easeTo({ pitch: 50, duration: duration(400) });
    } else {
      map.dragRotate.disable();
      map.touchPitch.disable();
      map.keyboard.disableRotation();
      map.touchZoomRotate.disableRotation();
      map.easeTo({ pitch: 0, bearing: 0, duration: duration(400) });
    }
  }, [view3d, duration]);

  React.useImperativeHandle(
    ref,
    (): MapViewHandle => ({
      fitBounds: (b, options) => doFit(b, options),
      flyTo: ({ center, zoom: z, pitch, bearing }) => {
        const map = mapRef.current;
        if (!map) return;
        cameraTouchedRef.current = true;
        const target = {
          center,
          ...(z !== undefined ? { zoom: z } : {}),
          ...(pitch !== undefined ? { pitch } : {}),
          ...(bearing !== undefined ? { bearing } : {}),
        };
        if (propsRef.current.reducedMotion) map.jumpTo(target);
        else map.flyTo({ ...target, duration: CAMERA_MS * 1.5, essential: false });
      },
      getCanvas: () => mapRef.current?.getCanvas() ?? null,
      zoomIn: () => {
        cameraTouchedRef.current = true;
        mapRef.current?.zoomIn({ duration: duration(200) });
      },
      zoomOut: () => {
        cameraTouchedRef.current = true;
        mapRef.current?.zoomOut({ duration: duration(200) });
      },
      panBy: (offset) => {
        cameraTouchedRef.current = true;
        mapRef.current?.panBy(offset, { duration: duration(200) });
      },
      // Bearing only: the tilt follows the 2D / 3D setting, so the toggle never shows "3D" over a flat map.
      resetNorth: () =>
        mapRef.current?.easeTo({ bearing: 0, pitch: propsRef.current.view3d ? 50 : 0, duration: duration(300) }),
      focus: () => mapRef.current?.getCanvas().focus(),
      getMap: () => mapRef.current,
    }),
    [doFit, duration],
  );

  const sortedTargets = React.useMemo(
    () => [...(focusTargets ?? [])].sort((a, b) => a.index - b.index),
    [focusTargets],
  );

  /** Pan when the feature is off screen or under an overlay (legend, controls, readout). */
  const bringIntoView = React.useCallback(
    (position: [number, number]) => {
      const map = mapRef.current;
      const container = containerRef.current;
      if (!map || !container) return;
      const pad = fitPadding(container, overlayBoxes());
      const { clientWidth: w, clientHeight: h } = container;
      const point = map.project(position);
      if (point.x >= pad.left && point.x <= w - pad.right && point.y >= pad.top && point.y <= h - pad.bottom) return;
      cameraTouchedRef.current = true;
      // Centre it in the area the overlays leave free.
      map.easeTo({
        center: position,
        offset: [(pad.left - pad.right) / 2, (pad.top - pad.bottom) / 2],
        duration: duration(300),
      });
    },
    [duration, overlayBoxes],
  );

  const onKeyDown = React.useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target as HTMLElement;
      if (target.tagName !== "CANVAS") return; // only when the map itself has focus
      const cur = propsRef.current;
      const key = e.key;
      if (key === "?") {
        e.preventDefault();
        cur.onShowShortcuts?.();
        return;
      }
      if (key === "n" || key === "N") {
        if (sortedTargets.length === 0) {
          setAnnouncement("There are no features on the map to move between.");
          return;
        }
        e.preventDefault();
        const backwards = e.shiftKey;
        const pos = sortedTargets.findIndex((t) => t.index === cur.focusedFeature);
        let nextPos: number;
        if (pos === -1) nextPos = backwards ? sortedTargets.length - 1 : 0;
        else nextPos = (pos + (backwards ? -1 : 1) + sortedTargets.length) % sortedTargets.length;
        const next = sortedTargets[nextPos] as MapFocusTarget;
        cur.onFocusFeature?.(next.index);
        bringIntoView(next.position);
        setAnnouncement(`Feature ${nextPos + 1} of ${sortedTargets.length}: ${next.label}. Press Enter to select.`);
        return;
      }
      if (key === "Enter") {
        if (cur.focusedFeature !== null && cur.focusedFeature !== undefined) {
          e.preventDefault();
          const t = sortedTargets.find((x) => x.index === cur.focusedFeature);
          cur.onSelectFeature?.(cur.focusedFeature);
          setAnnouncement(`Selected ${t ? t.label : `feature ${cur.focusedFeature}`}.`);
        }
        return;
      }
      if (key === "Escape") {
        if (cur.focusedFeature !== null && cur.focusedFeature !== undefined) {
          e.preventDefault();
          cur.onFocusFeature?.(null);
          setAnnouncement("Keyboard focus cleared.");
        } else if (cur.onClearSelection) {
          e.preventDefault();
          cur.onClearSelection();
          setAnnouncement("Selection cleared.");
        }
        return;
      }
      if (key === "f" || key === "F") {
        e.preventDefault();
        if (cur.onFit) cur.onFit();
        else if (cur.bbox) doFit(cur.bbox);
      }
    },
    [sortedTargets, bringIntoView, doFit],
  );

  // Forward focus from the skip-link target to the canvas.
  const onWrapperFocus = React.useCallback((e: React.FocusEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) mapRef.current?.getCanvas().focus();
  }, []);

  const mapWidth = measurements.width;
  const readoutShown = showReadout && mapWidth >= READOUT_MIN_WIDTH;
  const attributionCollapsible = mapWidth > 0 && mapWidth < ATTRIBUTION_INLINE_MIN_WIDTH;

  return (
    <div
      id={id}
      tabIndex={-1}
      onFocus={onWrapperFocus}
      onKeyDown={onKeyDown}
      className={cn(
        "relative isolate h-full w-full overflow-hidden bg-bg outline-none",
        "[&_canvas:focus-visible]:outline-2 [&_canvas:focus-visible]:outline-offset-[-3px] [&_canvas:focus-visible]:outline-accent",
        className,
      )}
    >
      {/* maplibre-gl.css sets `.maplibregl-map { position: relative }` on this element, which beats
          `absolute`; the explicit size keeps it filling the frame either way. */}
      <div ref={containerRef} className="absolute inset-0 z-map h-full w-full" />
      {!ready ? <div aria-hidden className="absolute inset-0 z-map animate-pulse bg-surface" /> : null}

      {tooltip ? (
        <div
          aria-hidden
          className="pointer-events-none absolute z-panel max-w-64 rounded border border-border bg-elevated px-2 py-1 text-xs text-fg"
          style={{ left: tooltip.x + 14, top: tooltip.y + 14 }}
        >
          {tooltip.text}
        </div>
      ) : null}

      {/* Top-left, kept clear of the controls column: the basemap notice (only when it failed),
          then the `topLeft` overlays (the workspace's plan toolbar), in one column. Before the
          children, so the tab order reads the top row left to right: plan toolbar, then controls. */}
      {(basemap === "openfreemap" && basemapFailed) || topLeft ? (
        <MapOverlayContext.Provider value={overlayLayout}>
          <div
            className="pointer-events-none absolute left-2 top-2 z-panel flex flex-col items-start gap-2"
            style={mapWidth > 0 ? { maxWidth: Math.max(160, mapWidth - 3 * OVERLAY_GAP - measurements.controlsWidth) } : undefined}
          >
            {basemap === "openfreemap" && basemapFailed ? (
              <p
                ref={noticeRef}
                role="status"
                data-map-overlay="notice"
                className="pointer-events-auto rounded border border-warn/50 bg-surface/95 px-2 py-1 text-xs text-fg"
              >
                Basemap unavailable (offline?). Showing a plain background.
              </p>
            ) : null}
            {topLeft}
          </div>
        </MapOverlayContext.Provider>
      ) : null}

      <MapOverlayContext.Provider value={overlayLayout}>
        <div className="pointer-events-none absolute inset-0 z-panel">{children}</div>
      </MapOverlayContext.Provider>

      {/* Bottom-right stack, after the children so the tab order follows the screen: plan
          toolbar, controls, legend, readout, attribution. One column, so the readout and
          attribution never overlap. */}
      <div
        ref={stackRef}
        className="pointer-events-none absolute bottom-2 right-2 z-panel flex flex-col items-end gap-1"
        style={mapWidth > 0 ? { maxWidth: mapWidth - 2 * OVERLAY_GAP } : undefined}
      >
        {/* Only on maps wide enough to hold it beside the legend and the scale bar. */}
        {readoutShown ? (
          <CoordinateReadout lngLat={coord?.lngLat ?? null} isCenter={coord?.isCenter ?? true} zoom={zoom} />
        ) : null}
        <MapAttribution
          groups={attributionGroups}
          collapsible={attributionCollapsible}
          open={attributionOpen}
          onOpenChange={onAttributionOpenChange}
          maxWidth={mapWidth > 0 ? Math.max(120, mapWidth - OVERLAY_GAP - SCALE_RESERVE) : undefined}
        />
      </div>

      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement}
      </div>
    </div>
  );
});

export interface MapViewWithHandleProps extends MapViewProps {
  handleRef?: React.Ref<MapViewHandle>;
}

/** `next/dynamic` does not forward refs, so `map-loader.tsx` loads this wrapper instead. */
export function MapViewWithHandle({ handleRef, ...props }: MapViewWithHandleProps) {
  return <MapView ref={handleRef} {...props} />;
}

export default MapViewWithHandle;
