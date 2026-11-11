"use client";

/**
 * Plan-mode actions shared by the map toolbar (`operation-input.tsx`), the module panels'
 * "Draw, pin or open a dataset" hint (`plan-start.tsx`) and the command palette:
 *
 * - `startPlanTool(tool)`: drawing mode, announced, with keyboard focus on the map (arrow keys
 *   pan, Space places a point at the map centre: the keyboard alternative to clicking).
 * - `locateDevice()`: the browser's Geolocation API. The position stays in the browser until the
 *   user saves the operation, which uploads it to this AirBrief server only.
 * - `useSaveOperation()`: wraps the draft as a GeoJSON file (`lib/plan.ts`), uploads it like any
 *   other dataset and opens it in the current module (optionally running an airspace check).
 */

import * as React from "react";
import { DEMO_MODE, describeError, type ApiError } from "@/lib/api";
import { cancelMapPick } from "@/lib/map-pick";
import type { ModuleId } from "@/lib/modules";
import { draftGeometry, operationFile, pinCircleProblem, pointsBounds, type PlanDraft, type PlanTool } from "@/lib/plan";
import { usePlan } from "@/lib/plan-store";
import { useUpload } from "@/lib/queries";
import { useUi } from "@/lib/store";
import type { BBoxList, FileInfo, Geometry, PlaceResult, Position } from "@/lib/types";
import { useWorkspaceParams } from "@/lib/url-state";
import { pointBounds } from "@/components/modules/shared";
import type { MapViewHandle } from "@/components/map/map-loader";
import { useAnalysisRunner } from "./analysis-runner";
import { announce } from "./live-region";

/** What each tool says when it starts (also the drawing card's instructions). */
export const TOOL_HELP: Record<PlanTool, string> = {
  pin: "Click the map to drop the pin, or press Space to drop it at the map centre. Escape cancels.",
  line: "Click the map to add points (Space adds one at the map centre). Enter or a double-click finishes; Backspace removes the last point; Escape cancels.",
  area: "Click the map to add corners (Space adds one at the map centre). Enter, a double-click or the first corner closes the area; Backspace removes the last corner; Escape cancels.",
};

export const TOOL_TITLE: Record<PlanTool, string> = { pin: "Drop a pin", line: "Draw a line", area: "Draw an area" };

/** Altitude of the one-tap airspace check (the API's default and the common 120 m / 400 ft limit). */
export const QUICK_CHECK_ALTITUDE_M = 120;

/** Put keyboard focus on the workspace map (its wrapper forwards focus to the canvas). */
export function focusWorkspaceMap(): void {
  window.requestAnimationFrame(() => document.getElementById("map")?.focus());
}

/** Start drawing with `tool`: announce how it works and move focus to the map. */
export function startPlanTool(tool: PlanTool): void {
  // One click mode at a time: a panel's pending "pick a point" request (e.g. Place observer) ends.
  cancelMapPick();
  usePlan.getState().startDrawing(tool);
  announce(`${TOOL_TITLE[tool]}. ${TOOL_HELP[tool]}`);
  focusWorkspaceMap();
}

/**
 * Go to a place found by a search (the plan menu, the command palette). An aerodrome also
 * switches on the aerodrome and zone ring context layers when no context layer is on yet.
 */
export function goToPlace(place: PlaceResult): void {
  const plan = usePlan.getState();
  let note: string | undefined;
  if (place.kind === "aerodrome" && plan.contextLayers.length === 0) {
    plan.setContextLayer("aerodromes", true);
    plan.setContextLayer("rings", true);
    note = "The aerodrome and zone ring context layers are on.";
  }
  plan.goToPlace(place, note ? { note } : {});
}

export interface DevicePosition {
  lon: number;
  lat: number;
  /** radius (m) at 95 % confidence (W3C Geolocation API `coords.accuracy`) */
  accuracyM: number;
}

/** A location failure: what happened and how to fix it. */
export class LocateError extends Error {
  constructor(
    message: string,
    readonly fix: string,
  ) {
    super(message);
    this.name = "LocateError";
  }
}

const LOCATE_TIMEOUT_MS = 20_000;
const TYPE_INSTEAD = "Or type the coordinates (New operation → Coordinates…).";

/**
 * The device position (GPS where available; works offline). Needs a secure context (HTTPS or
 * localhost) and the user's permission. Nothing is sent anywhere by this call.
 */
export function locateDevice(): Promise<DevicePosition> {
  return new Promise((resolve, reject) => {
    if (typeof window === "undefined" || !("geolocation" in navigator)) {
      reject(new LocateError("This browser cannot share its location.", TYPE_INSTEAD));
      return;
    }
    if (!window.isSecureContext) {
      reject(
        new LocateError(
          "The browser shares the location only with pages served over HTTPS (or from localhost).",
          `Open AirBrief over HTTPS. ${TYPE_INSTEAD}`,
        ),
      );
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lon: pos.coords.longitude, lat: pos.coords.latitude, accuracyM: pos.coords.accuracy }),
      (err) => {
        if (err.code === err.PERMISSION_DENIED) {
          reject(new LocateError("Permission to use your location was denied.", `Allow location for this site in the browser settings. ${TYPE_INSTEAD}`));
        } else if (err.code === err.TIMEOUT) {
          reject(new LocateError(`No position within ${LOCATE_TIMEOUT_MS / 1000} s.`, `Try again with a clear view of the sky. ${TYPE_INSTEAD}`));
        } else {
          reject(new LocateError("The device could not determine its position.", `Check that location services (GPS or Wi-Fi) are on. ${TYPE_INSTEAD}`));
        }
      },
      { enableHighAccuracy: true, timeout: LOCATE_TIMEOUT_MS, maximumAge: 60_000 },
    );
  });
}

/** "My location": a pin draft at the device position, brought into view. */
export function useLocate(mapRef: React.RefObject<MapViewHandle | null>) {
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<LocateError | null>(null);
  const locate = React.useCallback(async () => {
    setPending(true);
    setError(null);
    announce("Finding your location…");
    try {
      const pos = await locateDevice();
      usePlan.getState().setDraft({
        kind: "pin",
        vertices: [[pos.lon, pos.lat]],
        geometry: null,
        sourceText: null,
        source: "location",
        accuracyM: pos.accuracyM,
        name: "My location",
      });
      const radius = Math.max(usePlan.getState().pinRadiusM, pos.accuracyM);
      mapRef.current?.fitBounds(pointBounds(pos.lon, pos.lat, Math.max(300, radius * 3)), { maxZoom: 17 });
      announce(`Location found, within ${Math.round(pos.accuracyM)} metres. Save it to run a module on it.`);
    } catch (e) {
      const failure = e instanceof LocateError ? e : new LocateError(String(e), TYPE_INSTEAD);
      setError(failure);
    } finally {
      setPending(false);
    }
  }, [mapRef]);
  return { locate, pending, error, clearError: () => setError(null) };
}

function geometryPositions(g: Geometry): Position[] {
  if (g.type === "GeometryCollection") return g.geometries.flatMap(geometryPositions);
  const out: Position[] = [];
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") out.push(c as Position);
    else if (Array.isArray(c)) c.forEach(walk);
  };
  walk((g as { coordinates: unknown }).coordinates);
  return out;
}

/** Extent to show a draft at (a pin with its circle). */
export function draftBounds(draft: PlanDraft, pinRadiusM: number): BBoxList | null {
  if (draft.kind === "pin" && draft.vertices[0]) {
    const [lon, lat] = draft.vertices[0];
    return pointBounds(lon, lat, Math.max(300, pinRadiusM * 2.5));
  }
  const g = draftGeometry(draft);
  return g ? pointsBounds(geometryPositions(g)) : null;
}

/** Why the draft cannot be saved yet, or null. */
export function draftProblem(draft: PlanDraft, pinRadiusM: number): string | null {
  if (draft.kind === "pin") {
    const at = draft.vertices[0];
    return at ? pinCircleProblem(at[0], at[1], pinRadiusM) : "The pin has no position.";
  }
  // Drawn across ±180°, the vertices continue past it (see `continuousWith` in plan-map.ts).
  if (draft.vertices.some((v) => Math.abs(v[0]) > 180)) {
    return "The shape crosses the ±180° meridian, which a saved dataset cannot: draw it in two parts, one on each side.";
  }
  return draftGeometry(draft) ? null : "The operation has no geometry.";
}

export interface SaveOptions {
  /** also run an airspace check (120 m AGL) on the saved operation (the phone flow) */
  checkAirspace?: boolean;
}

/**
 * Save the draft as a dataset and open it: the module stays the one on screen (airspace for
 * `checkAirspace`), the selection and analysis are cleared. Errors stay in `error` for display.
 */
export function useSaveOperation() {
  const upload = useUpload();
  const runner = useAnalysisRunner();
  const { update, module } = useWorkspaceParams();
  const [error, setError] = React.useState<unknown>(null);
  const [saving, setSaving] = React.useState(false);

  const save = React.useCallback(
    async (options: SaveOptions = {}): Promise<FileInfo | null> => {
      const { draft, pinRadiusM } = usePlan.getState();
      if (!draft || saving) return null;
      const problem = draftProblem(draft, pinRadiusM);
      if (problem) {
        setError(new Error(problem));
        return null;
      }
      setSaving(true);
      setError(null);
      try {
        const info = await upload.mutateAsync({ file: operationFile(draft, pinRadiusM) });
        const plan = usePlan.getState();
        plan.setDraft(null);
        plan.clearGoTo();
        const target: ModuleId | null = options.checkAirspace ? "airspace" : module;
        update({ datasetId: info.id, featureIndices: null, analysisId: null, time: null, module: target });
        // The module's form is next (tablets start with the inspector closed).
        if (!options.checkAirspace && target !== null && target !== "measure") useUi.getState().setRightOpen(true);
        announce(`Saved "${info.filename}" as a dataset.`);
        if (options.checkAirspace) {
          if (info.status === "COMPLETED") {
            await runner.run("airspace", { altitude_agl_m: QUICK_CHECK_ALTITUDE_M }, { datasetId: info.id, featureIndices: null });
          } else if (info.status === "FAILED") {
            announce("The operation could not be read as a dataset, so the airspace check did not run.");
          } else {
            announce("Run the airspace check once the dataset has been processed.");
          }
        }
        return info;
      } catch (e) {
        setError(e);
        return null;
      } finally {
        setSaving(false);
      }
    },
    [module, runner, saving, update, upload],
  );

  return { save, saving, error, reset: () => setError(null) };
}

/** Error text for a failed save (the demo is read-only; other failures keep their API message). */
export function saveErrorProps(error: unknown): { title: string; message: string; fix?: string } {
  if (DEMO_MODE) {
    return {
      title: "Not available in the demo",
      message: "The public demo is read-only, so a drawn operation cannot be saved here.",
      fix: "Run AirBrief yourself to save operations and run modules on them.",
    };
  }
  if (error instanceof Error && !("status" in error)) return { title: "Cannot save yet", message: error.message };
  const d = describeError(error as ApiError);
  return { title: `Could not save: ${d.title}`, message: d.message, ...(d.fix ? { fix: d.fix } : {}) };
}
