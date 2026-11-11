"use client";

/**
 * Theme controls. next-themes owns the actual theme (`dark` | `light`, class strategy, no
 * built-in system mode). AirBrief adds an optional "System" mode on top: when it is chosen we
 * remember that in localStorage and keep next-themes in sync with the OS colour scheme
 * (`<ThemeSystemSync />`, mounted by the site header and the settings page).
 */

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { IconButton } from "@/components/ui/icon-button";

export type ThemeMode = "dark" | "light" | "system";

const FOLLOW_KEY = "airbrief-theme-follow-system";
const DARK_QUERY = "(prefers-color-scheme: dark)";

const followListeners = new Set<() => void>();

function readFollow(): boolean {
  try {
    return window.localStorage.getItem(FOLLOW_KEY) === "1";
  } catch {
    return false;
  }
}

function writeFollow(follow: boolean): void {
  try {
    if (follow) window.localStorage.setItem(FOLLOW_KEY, "1");
    else window.localStorage.removeItem(FOLLOW_KEY);
  } catch {
    // Storage may be unavailable (private mode); the choice then lasts for this page only.
  }
  followListeners.forEach((listener) => listener());
}

function subscribeFollow(callback: () => void): () => void {
  followListeners.add(callback);
  const onStorage = (event: StorageEvent) => {
    if (event.key === FOLLOW_KEY) callback();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    followListeners.delete(callback);
    window.removeEventListener("storage", onStorage);
  };
}

function systemPrefersDark(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia && window.matchMedia(DARK_QUERY).matches;
}

/** True once the component has mounted (theme values are only known on the client). */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}

export interface ThemeModeApi {
  /** the chosen mode, including "system" */
  mode: ThemeMode;
  /** the theme actually applied */
  resolved: "dark" | "light";
  setMode: (mode: ThemeMode) => void;
  /** false during the server render and the first client render */
  mounted: boolean;
}

export function useThemeMode(): ThemeModeApi {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useMounted();
  const follow = useSyncExternalStore(subscribeFollow, readFollow, () => false);
  const resolved: "dark" | "light" = resolvedTheme === "light" ? "light" : "dark";

  const setMode = useCallback(
    (mode: ThemeMode) => {
      if (mode === "system") {
        writeFollow(true);
        setTheme(systemPrefersDark() ? "dark" : "light");
      } else {
        writeFollow(false);
        setTheme(mode);
      }
    },
    [setTheme],
  );

  return { mode: follow ? "system" : resolved, resolved, setMode, mounted };
}

/** Keeps the theme in step with the OS while the "System" mode is selected. Renders nothing. */
export function ThemeSystemSync(): null {
  const { setTheme, resolvedTheme } = useTheme();
  const follow = useSyncExternalStore(subscribeFollow, readFollow, () => false);

  useEffect(() => {
    if (!follow || !window.matchMedia) return undefined;
    const mql = window.matchMedia(DARK_QUERY);
    const apply = () => {
      const wanted = mql.matches ? "dark" : "light";
      if (resolvedTheme !== wanted) setTheme(wanted);
    };
    apply();
    mql.addEventListener("change", apply);
    return () => mql.removeEventListener("change", apply);
  }, [follow, resolvedTheme, setTheme]);

  return null;
}

export function ThemeToggle({ className, side = "bottom" }: { className?: string; side?: "top" | "bottom" | "left" | "right" }) {
  const { resolved, setMode, mounted } = useThemeMode();

  if (!mounted) {
    // Same size and name on the server, so nothing shifts or mismatches on hydration.
    return <IconButton label="Switch theme" icon={<Moon />} className={className} side={side} disabled />;
  }

  const next = resolved === "dark" ? "light" : "dark";
  return (
    <IconButton
      label={next === "light" ? "Switch to light theme" : "Switch to dark theme"}
      icon={next === "light" ? <Sun /> : <Moon />}
      onClick={() => setMode(next)}
      className={className}
      side={side}
    />
  );
}
