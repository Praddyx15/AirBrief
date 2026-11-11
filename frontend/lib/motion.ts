/**
 * Reduced-motion handling: combines the OS setting (`prefers-reduced-motion`) with the in-app
 * override in settings (`reduceMotion: "system" | "on" | "off"`).
 *
 * When reduced motion is on: no map fly-to (jump instead), no slides, no autoplay, and show
 * playback starts paused (PLAN §7). CSS honours the same rule through `<html data-motion>`,
 * which `<MotionSync />` (mounted by Providers) keeps up to date.
 */

import { useEffect, useSyncExternalStore } from "react";
import { useSettings, type ReduceMotionPref } from "./store";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(callback: () => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => undefined;
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", callback);
  return () => mql.removeEventListener("change", callback);
}

function getSnapshot(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia && window.matchMedia(QUERY).matches;
}

function getServerSnapshot(): boolean {
  return false;
}

/** The OS-level preference only. */
export function useSystemPrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/** Resolve the effective preference from the setting and the OS value. */
export function resolveReducedMotion(pref: ReduceMotionPref, system: boolean): boolean {
  if (pref === "on") return true;
  if (pref === "off") return false;
  return system;
}

/** True when motion should be reduced (OS setting, unless overridden in Settings). */
export function usePrefersReducedMotion(): boolean {
  const pref = useSettings((s) => s.reduceMotion);
  const system = useSystemPrefersReducedMotion();
  return resolveReducedMotion(pref, system);
}

/** Non-hook check for event handlers and imperative code (map camera moves). */
export function prefersReducedMotionNow(): boolean {
  return resolveReducedMotion(useSettings.getState().reduceMotion, getSnapshot());
}

/** Transition duration in ms to use for imperative animations (0 when motion is reduced). */
export function motionDuration(ms: number, reduced: boolean): number {
  return reduced ? 0 : ms;
}

/**
 * Mirrors the setting onto `<html data-motion="reduce|full">` so CSS can honour the in-app
 * override. "system" removes the attribute and CSS falls back to the media query.
 */
export function useMotionAttribute(): void {
  const pref = useSettings((s) => s.reduceMotion);
  useEffect(() => {
    const root = document.documentElement;
    if (pref === "on") root.setAttribute("data-motion", "reduce");
    else if (pref === "off") root.setAttribute("data-motion", "full");
    else root.removeAttribute("data-motion");
  }, [pref]);
}
