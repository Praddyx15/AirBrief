"use client";

/**
 * Plan mode on the workspace map (state in `lib/plan-store.ts`):
 *
 * - `usePlanMapInteractions(mapRef)`: while a tool is active, MapLibre clicks place vertices
 *   (a pin finishes on its first click; clicking the first vertex of an area, or double-clicking,
 *   finishes), the pointer drives a rubber band, double-click zoom is off, and the keyboard
 *   works anywhere outside text fields: Enter finishes, Escape cancels, Backspace / Delete /
 *   Ctrl+Z undoes the last vertex and Space (map focused) places a vertex at the map centre — the
 *   keyboard alternative to clicking (WCAG 2.5.7), next to the coordinates dialog. It also keeps
 *   the map view in the store (for the context layers) and flies to "Go to" targets.
 * - `useContextData()`: the context layers switched on, fetched for a padded, snapped box
 *   around the view (`contextViewBox`), or a "too wide" flag above 2° × 2°. The rings use the
 *   Rules of the open airspace, flight-audit or eVTOL panel (`zoneProfile`), else the default.
 */

import * as React from "react";
import type { Map as MlMap, MapLayerMouseEvent, MapMouseEvent } from "maplibre-gl";
import { DEMO_MODE } from "@/lib/api";
import { formatLatLon, type LonLat } from "@/lib/coords";
import { useMapPick } from "@/lib/map-pick";
import { MIN_VERTICES, usePlan } from "@/lib/plan-store";
import { useContextLayers, type ContextLayerData } from "@/lib/queries";
import { MAX_CONTEXT_SPAN_DEG, type BBoxList } from "@/lib/types";
import type { MapViewHandle } from "@/components/map/map-loader";
import { pointBounds } from "@/components/modules/shared";
import { announce } from "./live-region";
import { focusWorkspaceMap } from "./plan-actions";

/** Clicks within this many pixels of an area's first vertex close the area. */
const CLOSE_PX = 10;
/** Context requests snap outwards to this grid (degrees), so small pans reuse the cache. */
const CONTEXT_STEP_DEG = 0.05;

const TOOL_NOUN = { pin: "pin", line: "line", area: "area" } as const;

/** Last "Go to" target the map moved to: a remounted map does not fly to an old target again. */
let flownGoToSeq = 0;

function round8(v: number): number {
  return Math.round(v * 1e8) / 1e8;
}

/** The MapLibre map behind the handle, once it exists. */
function useMapInstance(mapRef: React.RefObject<MapViewHandle | null>): MlMap | null {
  const [map, setMap] = React.useState<MlMap | null>(null);
  React.useEffect(() => {
    let raf = 0;
    const check = () => {
      const m = mapRef.current?.getMap() ?? null;
      if (m) setMap(m);
      else raf = window.requestAnimationFrame(check);
    };
    check();
    return () => window.cancelAnimationFrame(raf);
  }, [mapRef]);
  return map;
}

/** Finish the drawing, announcing the result (or why it cannot finish yet). */
export function finishPlanDrawing(): void {
  const s = usePlan.getState();
  const tool = s.drawing;
  if (!tool || tool === "pin") return;
  const count = s.vertices.length;
  if (s.finishDrawing()) {
    announce(`${tool === "line" ? "Line" : "Area"} finished with ${count} points. Save it to run a module on it.`);
  } else {
    announce(`A ${TOOL_NOUN[tool]} needs at least ${MIN_VERTICES[tool]} points; ${count} placed so far.`);
  }
}

/**
 * `p` (longitude in −180…180) moved by whole turns to within 180° of `first`, so a line or area
 * drawn across the ±180° meridian stays continuous (179.9 → 180.1, not −179.9) instead of
 * wrapping round the world; `draftProblem` then refuses to save it (see `plan-actions.ts`).
 */
export function continuousWith(p: LonLat, first: LonLat | undefined): LonLat {
  if (!first) return p;
  const lon = first[0] + ((((p[0] - first[0]) % 360) + 540) % 360) - 180;
  return [round8(lon), p[1]];
}

/** Place a vertex (or the pin) and announce it. */
export function placePlanVertex(at: LonLat): void {
  const s = usePlan.getState();
  const tool = s.drawing;
  if (!tool) return;
  const p = tool === "pin" ? at : continuousWith(at, s.vertices[0]);
  s.addVertex(p);
  if (tool === "pin") announce(`Pin placed at ${formatLatLon(p[0], p[1])}.`);
  else announce(`Point ${usePlan.getState().vertices.length} placed.`);
}

export function cancelPlanDrawing(): void {
  if (!usePlan.getState().drawing) return;
  usePlan.getState().cancelDrawing();
  announce("Drawing cancelled.");
}

export function undoPlanVertex(): void {
  const s = usePlan.getState();
  if (!s.drawing || s.vertices.length === 0) return;
  s.undoVertex();
  announce(`Last point removed; ${usePlan.getState().vertices.length} left.`);
}

function isTextField(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT";
}

/** Keys pressed inside a dialog or popover (the command palette, a menu) belong to it. */
function inDialog(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return Boolean(el?.closest?.('[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]'));
}

/** Drawing handlers, keyboard, view tracking and "Go to" camera moves for the workspace map. */
export function usePlanMapInteractions(mapRef: React.RefObject<MapViewHandle | null>): void {
  const map = useMapInstance(mapRef);
  const drawing = usePlan((s) => s.drawing);
  const goToSeq = usePlan((s) => s.goTo?.seq ?? 0);
  const setViewport = usePlan((s) => s.setViewport);

  // Pointer.
  React.useEffect(() => {
    if (!map || !drawing) return;
    const dblZoom = map.doubleClickZoom.isEnabled();
    map.doubleClickZoom.disable();
    let raf = 0;
    let pending: LonLat | null = null;
    const onMove = (e: MapMouseEvent) => {
      const w = e.lngLat.wrap();
      // The rubber band follows the same continuous longitudes as the vertices.
      pending = drawing === "pin" ? [w.lng, w.lat] : continuousWith([w.lng, w.lat], usePlan.getState().vertices[0]);
      if (!raf) {
        raf = window.requestAnimationFrame(() => {
          raf = 0;
          usePlan.getState().setCursor(pending);
        });
      }
    };
    const onOut = () => usePlan.getState().setCursor(null);
    const onClick = (e: MapMouseEvent) => {
      const s = usePlan.getState();
      if (s.drawing === "area" && s.vertices.length >= 3) {
        const first = map.project(s.vertices[0] as LonLat);
        if (Math.hypot(first.x - e.point.x, first.y - e.point.y) <= CLOSE_PX) {
          finishPlanDrawing();
          return;
        }
      }
      const w = e.lngLat.wrap();
      placePlanVertex([round8(w.lng), round8(w.lat)]);
    };
    const onDouble = (e: MapLayerMouseEvent | MapMouseEvent) => {
      e.preventDefault();
      finishPlanDrawing();
    };
    map.on("mousemove", onMove);
    map.on("mouseout", onOut);
    map.on("click", onClick);
    map.on("dblclick", onDouble);
    return () => {
      if (raf) window.cancelAnimationFrame(raf);
      map.off("mousemove", onMove);
      map.off("mouseout", onOut);
      map.off("click", onClick);
      map.off("dblclick", onDouble);
      if (dblZoom) map.doubleClickZoom.enable();
      usePlan.getState().setCursor(null);
    };
  }, [map, drawing]);

  // Keyboard: capture phase, so the map's own Escape / Enter handling does not also run.
  React.useEffect(() => {
    if (!drawing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || isTextField(e.target) || inDialog(e.target)) return;
      const target = e.target as HTMLElement | null;
      const onMap = target === document.body || target?.tagName === "CANVAS";
      const undo = e.key === "Backspace" || e.key === "Delete" || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z");
      let handled = true;
      if (e.key === "Escape") {
        // From a button of the drawing card (which closes), focus goes to the map, not the page.
        const fromCard = Boolean(target?.closest?.('[data-map-overlay="plan"]'));
        cancelPlanDrawing();
        if (fromCard) focusWorkspaceMap();
      } else if (undo) undoPlanVertex();
      else if (e.key === "Enter" && onMap) finishPlanDrawing();
      else if (e.key === " " && onMap && map) {
        const c = map.getCenter().wrap();
        placePlanVertex([round8(c.lng), round8(c.lat)]);
      } else handled = false;
      if (handled) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [drawing, map]);

  // One click mode at a time: a panel asking for a point (e.g. Place observer) ends the drawing.
  React.useEffect(
    () =>
      useMapPick.subscribe((s, prev) => {
        if (s.request && s.request !== prev.request && usePlan.getState().drawing) cancelPlanDrawing();
      }),
    [],
  );

  // The view, for the context layers.
  React.useEffect(() => {
    if (!map) return;
    const update = () => {
      const b = map.getBounds();
      setViewport([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
    };
    update();
    map.on("moveend", update);
    return () => {
      map.off("moveend", update);
    };
  }, [map, setViewport]);

  // "Go to": bring the target into view once (jumps under reduced motion). A target set outside
  // the workspace (the command palette on another page) is flown to when the map appears.
  React.useEffect(() => {
    const target = usePlan.getState().goTo;
    if (!map || !target || goToSeq === 0 || target.seq <= flownGoToSeq) return;
    flownGoToSeq = target.seq;
    mapRef.current?.fitBounds(pointBounds(target.lon, target.lat, target.radiusM), { maxZoom: 16 });
    announce(`Map moved to ${target.label}.${target.note ? ` ${target.note}` : ""}`);
  }, [goToSeq, map, mapRef]);
}

/**
 * The box to fetch context layers for: the view padded by half its size on each side, snapped
 * outwards to a 0.05° grid and kept within the API's 2° × 2°; `tooWide` when the view itself
 * is wider than that.
 */
export function contextViewBox(rawView: BBoxList | null): { bbox: BBoxList | null; tooWide: boolean } {
  if (!rawView) return { bbox: null, tooWide: false };
  // MapLibre reports a map panned across the ±180° meridian in the next world copy (e.g. 200° E):
  // bring the view's centre back into −180…180 so the request stays a valid WGS84 box.
  const shift = Math.round((rawView[0] + rawView[2]) / 2 / 360) * 360;
  const view: BBoxList = [rawView[0] - shift, rawView[1], rawView[2] - shift, rawView[3]];
  const w = view[2] - view[0];
  const h = view[3] - view[1];
  if (!(w > 0 && h > 0)) return { bbox: null, tooWide: false };
  if (w > MAX_CONTEXT_SPAN_DEG || h > MAX_CONTEXT_SPAN_DEG) return { bbox: null, tooWide: true };
  const step = CONTEXT_STEP_DEG;
  const padX = Math.max(w / 2, step);
  const padY = Math.max(h / 2, step);
  let minX = Math.floor((view[0] - padX) / step) * step;
  let maxX = Math.ceil((view[2] + padX) / step) * step;
  let minY = Math.floor((view[1] - padY) / step) * step;
  let maxY = Math.ceil((view[3] + padY) / step) * step;
  // A hair under 1°, so the box stays within 2° after rounding to 6 decimals.
  const half = MAX_CONTEXT_SPAN_DEG / 2 - 1e-6;
  if (maxX - minX > MAX_CONTEXT_SPAN_DEG) {
    const cx = (view[0] + view[2]) / 2;
    [minX, maxX] = [cx - half, cx + half];
  }
  if (maxY - minY > MAX_CONTEXT_SPAN_DEG) {
    const cy = (view[1] + view[3]) / 2;
    [minY, maxY] = [cy - half, cy + half];
  }
  const r = (v: number) => Math.round(v * 1e6) / 1e6;
  const bbox: BBoxList = [r(Math.max(-180, minX)), r(Math.max(-90, minY)), r(Math.min(180, maxX)), r(Math.min(90, maxY))];
  return { bbox: bbox[0] < bbox[2] && bbox[1] < bbox[3] ? bbox : null, tooWide: false };
}

export interface ContextData {
  data: ContextLayerData[];
  /** the view is wider than 2° × 2°: nothing is fetched until the user zooms in */
  tooWide: boolean;
}

/**
 * The context layers switched on, for the current view. Components re-render only when the
 * snapped box changes (the selector returns it as text), not after every pan or zoom.
 */
export function useContextData(): ContextData {
  const layers = usePlan((s) => s.contextLayers);
  const boxKey = usePlan((s) => {
    if (s.contextLayers.length === 0) return "";
    const { bbox, tooWide } = contextViewBox(s.viewport);
    return tooWide ? "wide" : bbox ? bbox.join(",") : "";
  });
  const bbox = React.useMemo<BBoxList | null>(
    () => (boxKey && boxKey !== "wide" ? (boxKey.split(",").map(Number) as BBoxList) : null),
    [boxKey],
  );
  // custom rules exist only in an analysis, and the static demo has the default rings only
  const panel = usePlan((s) => s.zoneProfile);
  const profile = !DEMO_MODE && panel === "conservative_illustrative" ? panel : "dgca_2021";
  const data = useContextLayers(layers.length > 0 ? bbox : null, layers, profile);
  return { data, tooWide: boxKey === "wide" };
}
