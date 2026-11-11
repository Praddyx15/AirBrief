/**
 * Survey module constants and small helpers shared by its panel, results and map layers. The
 * defaults are the API's (airbrief_core SurveyParams and SprayParams).
 */

import { CATEGORICAL } from "@/lib/colors";
import type { CameraSpec } from "@/lib/types";

export const CUSTOM = "custom";
export const CUSTOM_CAMERA_NAME = "Custom camera";

export const ESTIMATE_NOTE = "Estimate only — no flight plan or waypoints are generated.";
export const SPRAY_NOTE = "Spraying effort estimate only — no route or waypoints are generated.";

export const DEFAULT_CAMERA = "p4rtk";
export const DEFAULT_CUSTOM_CAMERA: CameraSpec = {
  name: CUSTOM_CAMERA_NAME,
  sensor_width_mm: 13.2,
  sensor_height_mm: 8.8,
  focal_length_mm: 8.8,
  image_width_px: 5472,
  image_height_px: 3648,
  kind: "rgb",
  images_per_capture: 1,
  photo_mb: null,
};

/** Form defaults, the same as the API's (airbrief_core SurveyParams). */
export const DEFAULTS = {
  altitude: 100,
  gsd: 2.5,
  front: 80,
  side: 70,
  speed: 10,
  battery: 25,
  swap: 5,
  turn: 8,
  minTrigger: 2,
  maxAltitude: 120,
  direction: 0,
  corridorWidth: 100,
  reserve: 0,
  gcpMin: 5,
  gcpHaPerExtra: 10,
  gcpSpacing: 300,
} as const;

/** Spraying defaults (airbrief_core SprayParams). */
export const SPRAY_DEFAULTS = {
  swath_m: 5,
  rate_l_per_ha: 20,
  tank_l: 10,
  flow_l_per_min: 4,
  speed_ms: 5,
  turn_seconds: 5,
  refill_minutes: 3,
  height_m: 3,
} as const;

/** The 2–5 m band above the crop that the estimate assumes (airbrief_core SPRAY_HEIGHT_BAND_M). */
export const SPRAY_HEIGHT_PRESETS: readonly number[] = [2, 3, 4, 5];

export const MODES = ["mapping", "spraying"] as const;
export const COVERAGES = ["area", "corridor"] as const;
export const ORIENTATIONS = ["landscape", "portrait"] as const;

/** Height above ground (m) that gives the across-track GSD `gsdCm` (as airbrief_core altitude_for_gsd). */
export function heightForGsd(camera: CameraSpec, gsdCm: number, orientation: (typeof ORIENTATIONS)[number]): number {
  const [sensorMm, pixels] =
    orientation === "portrait" ? [camera.sensor_height_mm, camera.image_height_px] : [camera.sensor_width_mm, camera.image_width_px];
  return ((gsdCm / 100) * camera.focal_length_mm * pixels) / sensorMm;
}

/** Block colour shared by the table, the legend and the map (block 1 takes the first colour). */
export function blockColor(index: number): string {
  const n = CATEGORICAL.length;
  return CATEGORICAL[(((index - 1) % n) + n) % n] as string;
}
