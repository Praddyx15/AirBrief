/**
 * Plan-mode state (transient, not persisted): drawing on the map, the unsaved draft, the
 * "Go to" target, the coordinates / paste dialogs and the context layers shown on the map.
 *
 * - Drawing: `startDrawing(tool)` → `addVertex` (map clicks, Space at the map centre) →
 *   `finishDrawing()` (Enter, double-click, the first vertex, the Finish button) or
 *   `cancelDrawing()` (Escape). A pin finishes on its first point. A finished drawing becomes
 *   the `draft`, which the plan card saves as a dataset (see `lib/plan.ts`).
 * - Context layers live here rather than in `useUi().hiddenLayers` so that they survive dataset
 *   changes (`resetWorkspace`) and stay off by default.
 */

import { create } from "zustand";
import type { LonLat } from "./coords";
import { DEFAULT_PIN_RADIUS_M, type PlanDraft, type PlanTool } from "./plan";
import type { BBoxList, ContextLayerKey, PlaceResult, ZoneProfileKey } from "./types";

export type PlanDialog = "coordinates" | "paste" | null;

export interface GoToTarget {
  lon: number;
  lat: number;
  label: string;
  detail: string;
  kind: PlaceResult["kind"];
  /** radius (m) brought into view around the target */
  radiusM: number;
  /** changes on every request, so going to the same place twice moves the map again */
  seq: number;
  /** said after "Map moved to …" (e.g. which context layers were switched on) */
  note: string | null;
}

export interface PlanState {
  /** tool being drawn on the map, or null */
  drawing: PlanTool | null;
  /** vertices placed so far in the current drawing */
  vertices: LonLat[];
  /** pointer position while drawing (rubber band), or null */
  cursor: LonLat | null;
  /** a defined operation that has not been saved yet */
  draft: PlanDraft | null;
  pinRadiusM: number;
  dialog: PlanDialog;
  /** shape the coordinates dialog opens with */
  dialogTool: PlanTool;
  goTo: GoToTarget | null;
  /** context layers switched on (all off by default) */
  contextLayers: ContextLayerKey[];
  /** current map view, kept while a context layer is on */
  viewport: BBoxList | null;
  /** Rules chosen in the open airspace, flight-audit or eVTOL panel; the context rings follow it (null: no panel, the default) */
  zoneProfile: ZoneProfileKey | null;

  startDrawing: (tool: PlanTool) => void;
  addVertex: (p: LonLat) => void;
  undoVertex: () => void;
  /** Turn the drawing into the draft; false (and nothing changes) when it has too few points. */
  finishDrawing: () => boolean;
  cancelDrawing: () => void;
  setCursor: (p: LonLat | null) => void;
  setDraft: (draft: PlanDraft | null) => void;
  setPinRadius: (radiusM: number) => void;
  openDialog: (dialog: Exclude<PlanDialog, null>, tool?: PlanTool) => void;
  closeDialog: () => void;
  goToPlace: (place: Pick<PlaceResult, "lon" | "lat" | "label" | "detail" | "kind">, options?: { radiusM?: number; note?: string }) => void;
  clearGoTo: () => void;
  setContextLayer: (key: ContextLayerKey, on: boolean) => void;
  toggleContextLayer: (key: ContextLayerKey) => void;
  setViewport: (bbox: BBoxList | null) => void;
  setZoneProfile: (profile: ZoneProfileKey | null) => void;
}

/** Fewest distinct vertices each tool needs. */
export const MIN_VERTICES: Record<PlanTool, number> = { pin: 1, line: 2, area: 3 };

/** Radius brought into view when going to a place: aerodromes with their 12 km rings, points closer. */
export const GOTO_RADIUS_M: Record<PlaceResult["kind"], number> = { aerodrome: 14_000, coordinates: 1_500 };

const DRAFT_NAME: Record<PlanTool, string> = { pin: "Pin", line: "Drawn line", area: "Drawn area" };

/** Highest "Go to" sequence number handed out, so a cleared target never reuses one. */
let lastGoToSeq = 0;

function distinctCount(points: readonly LonLat[]): number {
  return new Set(points.map((p) => `${p[0]},${p[1]}`)).size;
}

export const usePlan = create<PlanState>()((set, get) => ({
  drawing: null,
  vertices: [],
  cursor: null,
  draft: null,
  pinRadiusM: DEFAULT_PIN_RADIUS_M,
  dialog: null,
  dialogTool: "pin",
  goTo: null,
  contextLayers: [],
  viewport: null,
  zoneProfile: null,

  startDrawing: (tool) => set({ drawing: tool, vertices: [], cursor: null, dialog: null }),
  addVertex: (p) => {
    const { drawing, vertices } = get();
    if (!drawing) return;
    if (drawing === "pin") {
      set({
        drawing: null,
        vertices: [],
        cursor: null,
        draft: { kind: "pin", vertices: [p], geometry: null, sourceText: null, source: "map", accuracyM: null, name: DRAFT_NAME.pin },
      });
      return;
    }
    const last = vertices[vertices.length - 1];
    if (last && last[0] === p[0] && last[1] === p[1]) return;
    set({ vertices: [...vertices, p] });
  },
  undoVertex: () => set((s) => ({ vertices: s.vertices.slice(0, -1) })),
  finishDrawing: () => {
    const { drawing, vertices } = get();
    if (!drawing || drawing === "pin" || distinctCount(vertices) < MIN_VERTICES[drawing]) return false;
    set({
      drawing: null,
      vertices: [],
      cursor: null,
      draft: { kind: drawing, vertices, geometry: null, sourceText: null, source: "map", accuracyM: null, name: DRAFT_NAME[drawing] },
    });
    return true;
  },
  cancelDrawing: () => set({ drawing: null, vertices: [], cursor: null }),
  setCursor: (cursor) => set({ cursor }),
  setDraft: (draft) => set({ draft, drawing: null, vertices: [], cursor: null }),
  setPinRadius: (pinRadiusM) => set({ pinRadiusM }),
  openDialog: (dialog, tool) => set((s) => ({ dialog, dialogTool: tool ?? s.dialogTool, drawing: null, vertices: [], cursor: null })),
  closeDialog: () => set({ dialog: null }),
  goToPlace: (place, options = {}) =>
    set((s) => ({
      goTo: {
        lon: place.lon,
        lat: place.lat,
        label: place.label,
        detail: place.detail,
        kind: place.kind,
        radiusM: options.radiusM ?? GOTO_RADIUS_M[place.kind],
        seq: Math.max(s.goTo?.seq ?? 0, lastGoToSeq) + 1,
        note: options.note ?? null,
      },
    })),
  clearGoTo: () =>
    set((s) => {
      lastGoToSeq = Math.max(lastGoToSeq, s.goTo?.seq ?? 0);
      return { goTo: null };
    }),
  setContextLayer: (key, on) =>
    set((s) => {
      const has = s.contextLayers.includes(key);
      if (on === has) return s;
      return { contextLayers: on ? [...s.contextLayers, key] : s.contextLayers.filter((k) => k !== key) };
    }),
  toggleContextLayer: (key) => get().setContextLayer(key, !get().contextLayers.includes(key)),
  setViewport: (viewport) => set({ viewport }),
  setZoneProfile: (zoneProfile) => set({ zoneProfile }),
}));

/** Something is being planned on the map (drawing, an unsaved draft, a dialog or a "Go to" target). */
export function usePlanActive(): boolean {
  return usePlan((s) => s.drawing !== null || s.draft !== null || s.dialog !== null || s.goTo !== null);
}
