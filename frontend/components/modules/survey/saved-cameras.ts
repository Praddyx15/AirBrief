/**
 * Custom survey cameras remembered in this browser (a per-viewer convenience): the survey panel
 * saves a custom camera when an estimate runs with it and offers it again in the camera list.
 * Kept in localStorage behind try/catch: blocked, full or hand-edited storage only means nothing
 * is remembered. Nothing here is sent anywhere.
 */

import * as React from "react";
import type { CameraKind, CameraSpec } from "@/lib/types";

const STORAGE_KEY = "airbrief-survey-cameras";
/** Most recent first; older ones drop off. */
export const MAX_SAVED_CAMERAS = 8;

const KINDS: readonly CameraKind[] = ["rgb", "thermal", "multispectral"];

function positive(v: unknown, max: number): v is number {
  return typeof v === "number" && Number.isFinite(v) && v > 0 && v <= max;
}

/** A stored camera, or null when it is not a valid spec (the API's CameraSpec bounds). */
export function validCamera(value: unknown): CameraSpec | null {
  if (typeof value !== "object" || value === null) return null;
  const c = value as Record<string, unknown>;
  const name = typeof c.name === "string" ? c.name.trim().slice(0, 80) : "";
  if (
    !name ||
    !positive(c.sensor_width_mm, 100) ||
    !positive(c.sensor_height_mm, 100) ||
    !positive(c.focal_length_mm, 500) ||
    !positive(c.image_width_px, 100_000) ||
    !positive(c.image_height_px, 100_000) ||
    !Number.isInteger(c.image_width_px) ||
    !Number.isInteger(c.image_height_px)
  ) {
    return null;
  }
  const kind = KINDS.includes(c.kind as CameraKind) ? (c.kind as CameraKind) : "rgb";
  const images = typeof c.images_per_capture === "number" && Number.isInteger(c.images_per_capture) && c.images_per_capture >= 1 && c.images_per_capture <= 20 ? c.images_per_capture : 1;
  return {
    name,
    sensor_width_mm: c.sensor_width_mm,
    sensor_height_mm: c.sensor_height_mm,
    focal_length_mm: c.focal_length_mm,
    image_width_px: c.image_width_px,
    image_height_px: c.image_height_px,
    kind,
    images_per_capture: images,
    photo_mb: positive(c.photo_mb, 2000) ? c.photo_mb : null,
  };
}

function read(): CameraSpec[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(validCamera).filter((c): c is CameraSpec => c !== null).slice(0, MAX_SAVED_CAMERAS);
  } catch {
    return [];
  }
}

function write(cameras: readonly CameraSpec[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(cameras));
  } catch {
    // not saved: the list still holds for this session
  }
}

function sameName(a: CameraSpec, b: CameraSpec): boolean {
  return a.name.trim().toLowerCase() === b.name.trim().toLowerCase();
}

/**
 * Saved custom cameras, read after mount (the server render and hydration show none), with
 * `remember` (adds or replaces by name, most recent first) and `forget` (by name).
 */
export function useSavedCameras(): {
  cameras: CameraSpec[];
  remember: (camera: CameraSpec) => void;
  forget: (name: string) => void;
} {
  const [cameras, setCameras] = React.useState<CameraSpec[]>([]);
  React.useEffect(() => {
    setCameras(read());
  }, []);
  const remember = React.useCallback((camera: CameraSpec) => {
    const valid = validCamera(camera);
    if (!valid) return;
    setCameras((prev) => {
      const next = [valid, ...prev.filter((c) => !sameName(c, valid))].slice(0, MAX_SAVED_CAMERAS);
      write(next);
      return next;
    });
  }, []);
  const forget = React.useCallback((name: string) => {
    setCameras((prev) => {
      const next = prev.filter((c) => c.name.trim().toLowerCase() !== name.trim().toLowerCase());
      write(next);
      return next;
    });
  }, []);
  return { cameras, remember, forget };
}
