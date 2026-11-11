/**
 * "Pick a point on the map" requests from module panels (e.g. "Place observer" in the line-of-sight
 * panel), not persisted. A panel calls `startMapPick` and the next click on the workspace map goes
 * to its `onPick` instead of selecting a feature. One request at a time: a new one replaces the
 * previous one. Typed coordinates stay the keyboard alternative in every panel that offers this.
 */

import * as React from "react";
import type { PickingInfo } from "@deck.gl/core";
import { create } from "zustand";

export interface MapPickRequest {
  /** what is being placed, e.g. "observer-2" (the panel shows its button as pressed) */
  id: string;
  /** shown and announced, e.g. "Pilot" */
  label: string;
  /** receives the clicked position, rounded to 1e-7° (about 1 cm) */
  onPick: (lonLat: [number, number]) => void;
  /** where the focus goes back after the point is placed or Escape cancels (the pressed button) */
  returnFocus?: HTMLElement | null;
}

interface MapPickState {
  request: MapPickRequest | null;
  start: (request: MapPickRequest) => void;
  cancel: () => void;
}

export const useMapPick = create<MapPickState>()((set) => ({
  request: null,
  start: (request) => set({ request }),
  cancel: () => set({ request: null }),
}));

export function startMapPick(request: MapPickRequest): void {
  useMapPick.getState().start(request);
}

export function cancelMapPick(): void {
  if (useMapPick.getState().request) useMapPick.getState().cancel();
}

function round7(v: number): number {
  return Math.round(v * 1e7) / 1e7;
}

/** Longitude wrapped into [-180, 180) (the map repeats the world sideways). */
function wrapLon(lon: number): number {
  return ((((lon + 180) % 360) + 360) % 360) - 180;
}

/**
 * Hand a map click to the pending pick request. Returns true when it was used (the caller then
 * skips its own click handling), false when no request is pending or the click has no position.
 */
export function consumeMapPick(info: Pick<PickingInfo, "coordinate">): boolean {
  const request = useMapPick.getState().request;
  const c = info.coordinate;
  if (!request || !c || c.length < 2) return false;
  const lon = Number(c[0]);
  const lat = Number(c[1]);
  if (!Number.isFinite(lon) || !Number.isFinite(lat) || lat < -90 || lat > 90) return false;
  useMapPick.getState().cancel();
  request.onPick([round7(wrapLon(lon)), round7(lat)]);
  if (request.returnFocus?.isConnected) request.returnFocus.focus();
  return true;
}

/** Escape: end the pending pick and put the focus back where it started. */
export function cancelMapPickToStart(): void {
  const request = useMapPick.getState().request;
  cancelMapPick();
  if (request?.returnFocus?.isConnected) request.returnFocus.focus();
}

/**
 * The workspace map's side of a pick: when one starts the map takes the keyboard focus (as plan
 * drawing does, WCAG 2.5.7), and Space or Enter on the focused map canvas places the point at the
 * map centre (pan with the arrow keys first). `getCentre` returns the centre as `[lon, lat]`, or
 * null while the map is not ready.
 */
export function useMapPickKeyboard(getCentre: () => [number, number] | null, focusMap?: () => void): void {
  const pending = useMapPick((s) => s.request !== null);
  const centreRef = React.useRef(getCentre);
  centreRef.current = getCentre;
  const focusRef = React.useRef(focusMap);
  focusRef.current = focusMap;
  React.useEffect(() => {
    if (!pending) return;
    focusRef.current?.();
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || (e.key !== " " && e.key !== "Enter")) return;
      if ((e.target as HTMLElement | null)?.tagName !== "CANVAS") return;
      const centre = centreRef.current();
      if (!centre) return;
      e.preventDefault();
      e.stopPropagation();
      consumeMapPick({ coordinate: centre });
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [pending]);
}
