"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Subscribe to a CSS media query. The server snapshot is `false`, so the first client render
 * matches the server (desktop layout) and switches right after hydration.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined" || !window.matchMedia) return () => undefined;
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    [query],
  );
  const getSnapshot = useCallback(
    () => typeof window !== "undefined" && !!window.matchMedia && window.matchMedia(query).matches,
    [query],
  );
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}

/**
 * Workspace breakpoints. A phone held sideways is
 * wider than 768 px but too short for the panels and the drawer, so a short touch screen also
 * gets the mobile viewer.
 */
export function useWorkspaceBreakpoint(): "mobile" | "tablet" | "desktop" {
  const mobile = useMediaQuery("(max-width: 767.98px), (max-height: 500px) and (pointer: coarse)");
  const tablet = useMediaQuery("(min-width: 768px) and (max-width: 1023.98px)");
  return mobile ? "mobile" : tablet ? "tablet" : "desktop";
}
