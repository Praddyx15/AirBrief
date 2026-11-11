/**
 * Client state (zustand).
 *
 * - `useSettings`: user preferences, persisted to localStorage under "airbrief-settings".
 *   Persistence uses `skipHydration`, so the server render and the first client render agree;
 *   `<Providers>` calls `rehydrateSettings()` once after mount. Use `useSettingsHydrated()` when
 *   a component must wait for the stored values: it is `false` on the server and while React
 *   hydrates, then follows the store, so server HTML (e.g. `disabled`) is always corrected.
 * - `useUi`: transient workspace UI state (not persisted). URL-addressable state (dataset,
 *   feature, module, analysis, time) lives in the URL instead: see `lib/url-state.ts`.
 *
 * Theme is NOT stored here: next-themes owns it (`useTheme()`).
 */

import { useSyncExternalStore } from "react";
import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";
import type { AnalysisKind, BandCeiling, Ed318Limits, UnverifiedBand } from "./types";

/** Keeps nothing: used on the server and when the browser blocks storage. */
const memoryStorage: StateStorage = {
  getItem: () => null,
  setItem: () => undefined,
  removeItem: () => undefined,
};

/**
 * localStorage when the browser allows it, otherwise `memoryStorage`. A store must always get a
 * storage: without one, zustand leaves out its `persist` API and `useSettingsHydrated` crashes
 * (on the server during prerendering, and in browsers with site data blocked).
 */
function browserStorage(): StateStorage {
  if (typeof window === "undefined") return memoryStorage;
  try {
    const storage = window.localStorage;
    storage.getItem("airbrief-probe");
    // Writes can still fail (storage full, write-blocked); zustand calls setItem inside every
    // `set`, so a throwing write would break the action. Failed writes keep the value in memory.
    return {
      getItem: (name) => {
        try {
          return storage.getItem(name);
        } catch {
          return null;
        }
      },
      setItem: (name, value) => {
        try {
          storage.setItem(name, value);
        } catch {
          // not saved: the in-memory state still has it for this session
        }
      },
      removeItem: (name) => {
        try {
          storage.removeItem(name);
        } catch {
          // nothing to do
        }
      },
    };
  } catch {
    return memoryStorage;
  }
}

export type Units = "metric" | "imperial";
export type ReduceMotionPref = "system" | "on" | "off";
export type BasemapId = "openfreemap" | "none";

export interface SettingsState {
  units: Units;
  reduceMotion: ReduceMotionPref;
  basemap: BasemapId;
  /** Base URL of the FlightMD web app, used for "Open in FlightMD" links. Empty = not set. */
  flightmdUrl: string;
  /** Assessment choices (Settings, docs/features/choices.md): what new runs and exports start from. */
  bandCeiling: BandCeiling;
  unverifiedBand: UnverifiedBand;
  ed318Limits: Ed318Limits;
  /** the choices the user set, in Settings or with the questionnaire: a panel's note goes once its own is */
  choicesSet: readonly ChoiceKey[];
  /** the panels' "Using the default assessment choices" note was dismissed (kept by Reset settings) */
  choicesHintDismissed: boolean;
  setUnits: (units: Units) => void;
  setReduceMotion: (pref: ReduceMotionPref) => void;
  setBasemap: (basemap: BasemapId) => void;
  setFlightmdUrl: (url: string) => void;
  setChoices: (choices: Partial<Pick<SettingsState, "bandCeiling" | "unverifiedBand" | "ed318Limits">>) => void;
  dismissChoicesHint: () => void;
  resetSettings: () => void;
}

export type ChoiceKey = "bandCeiling" | "unverifiedBand" | "ed318Limits";
const CHOICE_KEYS: readonly ChoiceKey[] = ["bandCeiling", "unverifiedBand", "ed318Limits"];

function envBasemap(): BasemapId {
  return process.env.NEXT_PUBLIC_BASEMAP === "none" ? "none" : "openfreemap";
}

export const DEFAULT_SETTINGS: Pick<
  SettingsState,
  "units" | "reduceMotion" | "basemap" | "flightmdUrl" | "bandCeiling" | "unverifiedBand" | "ed318Limits" | "choicesSet"
> = {
  units: "metric",
  reduceMotion: "system",
  basemap: envBasemap(),
  flightmdUrl: "",
  bandCeiling: "exceedance",
  unverifiedBand: "permission",
  ed318Limits: "geometry",
  choicesSet: [],
};

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      ...DEFAULT_SETTINGS,
      choicesHintDismissed: false,
      setUnits: (units) => set({ units }),
      setReduceMotion: (reduceMotion) => set({ reduceMotion }),
      setBasemap: (basemap) => set({ basemap }),
      setFlightmdUrl: (url) => set({ flightmdUrl: url.trim().replace(/\/+$/, "") }),
      setChoices: (choices) =>
        set((s) => ({ ...choices, choicesSet: CHOICE_KEYS.filter((k) => s.choicesSet.includes(k) || choices[k] !== undefined) })),
      dismissChoicesHint: () => set({ choicesHintDismissed: true }),
      resetSettings: () => set({ ...DEFAULT_SETTINGS }),
    }),
    {
      name: "airbrief-settings",
      version: 1,
      storage: createJSONStorage(browserStorage),
      skipHydration: true,
      partialize: (state) => ({
        units: state.units,
        reduceMotion: state.reduceMotion,
        basemap: state.basemap,
        flightmdUrl: state.flightmdUrl,
        bandCeiling: state.bandCeiling,
        unverifiedBand: state.unverifiedBand,
        ed318Limits: state.ed318Limits,
        choicesSet: state.choicesSet,
        choicesHintDismissed: state.choicesHintDismissed,
      }),
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<SettingsState>;
        const choices = {
          bandCeiling: oneOf(p.bandCeiling, ["exceedance", "permission"], current.bandCeiling),
          unverifiedBand: oneOf(p.unverifiedBand, ["permission", "exclude"], current.unverifiedBand),
          ed318Limits: oneOf(p.ed318Limits, ["geometry", "properties"], current.ed318Limits),
        };
        const saved = (p as { choicesSet?: unknown }).choicesSet;
        return {
          ...current,
          units: p.units === "imperial" || p.units === "metric" ? p.units : current.units,
          reduceMotion:
            p.reduceMotion === "on" || p.reduceMotion === "off" || p.reduceMotion === "system"
              ? p.reduceMotion
              : current.reduceMotion,
          basemap: p.basemap === "none" || p.basemap === "openfreemap" ? p.basemap : current.basemap,
          flightmdUrl: typeof p.flightmdUrl === "string" ? p.flightmdUrl : current.flightmdUrl,
          ...choices,
          // older saves: `true` is from before each choice was tracked, when setting any one
          // counted for all; with no record at all, a choice off its default was set by someone
          choicesSet:
            saved === true
              ? CHOICE_KEYS
              : Array.isArray(saved)
                ? CHOICE_KEYS.filter((k) => saved.includes(k))
                : CHOICE_KEYS.filter((k) => choices[k] !== DEFAULT_SETTINGS[k]),
          choicesHintDismissed: p.choicesHintDismissed === true,
        };
      },
    },
  ),
);

// This browser's own datasets and analyses. Public instances refuse to list everything
// (ALLOW_GLOBAL_LISTING), so the UI lists the ids created here via `?ids=`: newest first, capped
// at the API's 100-id limit.
//
// Tabs share one localStorage entry but each holds its own copy, and `persist` writes the whole
// copy on every change. So every action starts from the saved copy (`latest`) and one tab never
// overwrites what another just saved: an owner token is returned only once, and losing it means
// the item can no longer be deleted.

export const HISTORY_LIMIT = 100;
const HISTORY_KEY = "airbrief-history";

export interface HistoryState {
  datasetIds: string[];
  analysisIds: string[];
  /** owner tokens by id (returned once at creation; needed to delete or cancel) */
  ownerTokens: Record<string, string>;
  /** dataset id of each remembered analysis (deleting a dataset deletes its analyses) */
  analysisDatasets: Record<string, string>;
  /** datasets deleted with an undo window: id -> when to send the DELETE (epoch ms, as text) */
  pendingDeletes: Record<string, string>;
  rememberDataset: (id: string, ownerToken?: string | null) => void;
  rememberAnalysis: (id: string, ownerToken?: string | null, datasetId?: string | null) => void;
  /** Forget a deleted dataset and the analyses the API deleted with it. */
  forgetDataset: (id: string) => void;
  forgetAnalysis: (id: string) => void;
  /** Record a deletion that is due at `dueAt` (kept until it has been sent or undone). */
  markPendingDelete: (id: string, dueAt: number) => void;
  clearPendingDelete: (id: string) => void;
}

type HistoryData = Pick<HistoryState, "datasetIds" | "analysisIds" | "ownerTokens" | "analysisDatasets" | "pendingDeletes">;

function remember(list: readonly string[], id: string): string[] {
  return [id, ...list.filter((x) => x !== id)].slice(0, HISTORY_LIMIT);
}

function withEntry(map: Record<string, string>, id: string, value: string | null | undefined): Record<string, string> {
  return value ? { ...map, [id]: value } : map;
}

function pickKeys(map: Record<string, string>, keep: readonly string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const id of keep) if (typeof map[id] === "string") out[id] = map[id] as string;
  return out;
}

/** Keep tokens and dataset links only for remembered ids (the lists are capped, so these are too). */
function tidy(h: HistoryData): HistoryData {
  return {
    datasetIds: h.datasetIds,
    analysisIds: h.analysisIds,
    ownerTokens: pickKeys(h.ownerTokens, [...h.datasetIds, ...h.analysisIds]),
    analysisDatasets: pickKeys(h.analysisDatasets, h.analysisIds),
    pendingDeletes: pickKeys(h.pendingDeletes, h.datasetIds),
  };
}

function idList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string").slice(0, HISTORY_LIMIT) : [];
}

function stringMap(value: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) if (typeof v === "string") out[k] = v;
  }
  return out;
}

/** Validate a stored history (anything in localStorage may be stale or hand-edited). */
function sanitizeHistory(value: unknown): HistoryData {
  const p = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
  return tidy({
    datasetIds: idList(p.datasetIds),
    analysisIds: idList(p.analysisIds),
    ownerTokens: stringMap(p.ownerTokens),
    analysisDatasets: stringMap(p.analysisDatasets),
    pendingDeletes: stringMap(p.pendingDeletes),
  });
}

/** The saved history, which includes other tabs' changes; the in-memory copy if storage is unavailable. */
function latest(s: HistoryData): HistoryData {
  try {
    const raw = browserStorage().getItem(HISTORY_KEY) as string | null;
    if (raw) return sanitizeHistory((JSON.parse(raw) as { state?: unknown }).state);
  } catch {
    // private mode, blocked storage or a corrupt entry: work from memory
  }
  return s;
}

export const useHistory = create<HistoryState>()(
  persist(
    (set) => ({
      datasetIds: [],
      analysisIds: [],
      ownerTokens: {},
      analysisDatasets: {},
      pendingDeletes: {},
      rememberDataset: (id, ownerToken) =>
        set((s) => {
          const h = latest(s);
          return tidy({ ...h, datasetIds: remember(h.datasetIds, id), ownerTokens: withEntry(h.ownerTokens, id, ownerToken) });
        }),
      rememberAnalysis: (id, ownerToken, datasetId) =>
        set((s) => {
          const h = latest(s);
          return tidy({
            ...h,
            analysisIds: remember(h.analysisIds, id),
            ownerTokens: withEntry(h.ownerTokens, id, ownerToken),
            analysisDatasets: withEntry(h.analysisDatasets, id, datasetId),
          });
        }),
      forgetDataset: (id) =>
        set((s) => {
          const h = latest(s);
          const gone = new Set([id, ...h.analysisIds.filter((a) => h.analysisDatasets[a] === id)]);
          return tidy({
            ...h,
            datasetIds: h.datasetIds.filter((x) => !gone.has(x)),
            analysisIds: h.analysisIds.filter((x) => !gone.has(x)),
          });
        }),
      forgetAnalysis: (id) =>
        set((s) => {
          const h = latest(s);
          return tidy({ ...h, analysisIds: h.analysisIds.filter((x) => x !== id) });
        }),
      markPendingDelete: (id, dueAt) =>
        set((s) => {
          const h = latest(s);
          return tidy({ ...h, pendingDeletes: { ...h.pendingDeletes, [id]: String(Math.round(dueAt)) } });
        }),
      clearPendingDelete: (id) =>
        set((s) => {
          const h = latest(s);
          const pendingDeletes = Object.fromEntries(Object.entries(h.pendingDeletes).filter(([key]) => key !== id));
          return tidy({ ...h, pendingDeletes });
        }),
    }),
    {
      name: HISTORY_KEY,
      version: 1,
      storage: createJSONStorage(browserStorage),
      skipHydration: true,
      partialize: (state): HistoryData => ({
        datasetIds: state.datasetIds,
        analysisIds: state.analysisIds,
        ownerTokens: state.ownerTokens,
        analysisDatasets: state.analysisDatasets,
        pendingDeletes: state.pendingDeletes,
      }),
      merge: (persisted, current) => ({ ...current, ...sanitizeHistory(persisted) }),
    },
  ),
);

// Show other tabs' uploads, analyses and deletions here too (also when site data is cleared).
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key === HISTORY_KEY || event.key === null) void useHistory.persist.rehydrate();
  });
}

/** Load persisted settings and history from localStorage. Called once by <Providers>. */
export function rehydrateSettings(): Promise<void> | void {
  void useHistory.persist.rehydrate();
  return useSettings.persist.rehydrate();
}

function subscribeSettingsHydration(onChange: () => void): () => void {
  const offStart = useSettings.persist.onHydrate(onChange);
  const offFinish = useSettings.persist.onFinishHydration(onChange);
  return () => {
    offStart();
    offFinish();
  };
}

const settingsHydratedSnapshot = (): boolean => useSettings.persist.hasHydrated();
const settingsHydratedOnServer = (): boolean => false;

/**
 * True once persisted settings have been loaded on the client.
 *
 * `<Providers>` rehydrates in the root commit, which can run before a page's own Suspense
 * boundary hydrates. Reading `hasHydrated()` during that hydration would render `true` against
 * server HTML rendered with `false`, and React does not patch attribute mismatches, so e.g. a
 * server `disabled` attribute would stay for good. The server snapshot keeps hydration
 * consistent; React then re-renders with the real value.
 */
export function useSettingsHydrated(): boolean {
  return useSyncExternalStore(subscribeSettingsHydration, settingsHydratedSnapshot, settingsHydratedOnServer);
}

export function useUnits(): Units {
  return useSettings((s) => s.units);
}

export interface PlaybackState {
  playing: boolean;
  /** seconds from the start of the show / track */
  time: number;
  /** 0.25 .. 4 */
  speed: number;
}

export const PLAYBACK_SPEEDS = [0.25, 0.5, 1, 2, 4] as const;

export const DRAWER_MIN_HEIGHT = 120;
export const DRAWER_DEFAULT_HEIGHT = 340;

export interface UiState {
  leftOpen: boolean;
  rightOpen: boolean;
  drawerOpen: boolean;
  /** px; clamp to the viewport when rendering */
  drawerHeight: number;
  /** ids of map layers hidden by the user (module layer ids, e.g. "airspace:zones") */
  hiddenLayers: string[];
  /** feature index hovered in the table or on the map */
  hoveredFeature: number | null;
  /** feature index with keyboard focus on the map (N / Shift+N); selection itself is in the URL */
  focusedFeature: number | null;
  view3d: boolean;
  playback: PlaybackState;
  commandOpen: boolean;
  /** one-off "zoom the map here" request from result tables; `seq` changes on every request */
  mapFocus: { bbox: [number, number, number, number]; seq: number } | null;
  /** highlighted result row/object id of the active module (e.g. a site h3 cell or conflict key) */
  resultHighlight: string | null;
  /**
   * The analysis last shown per dataset and module (key: `moduleAnalysisKey`), so reopening a
   * module from the rail shows its latest run again instead of an empty drawer.
   */
  moduleAnalyses: Record<string, string>;
  /**
   * Analyses started in this tab (or opened while still running) that have not been seen
   * finishing yet: id → kind. The workspace polls them even when another module is open, so
   * their completion is still reported.
   */
  trackedAnalyses: Record<string, AnalysisKind>;

  setLeftOpen: (open: boolean) => void;
  toggleLeft: () => void;
  setRightOpen: (open: boolean) => void;
  toggleRight: () => void;
  setDrawerOpen: (open: boolean) => void;
  toggleDrawer: () => void;
  setDrawerHeight: (height: number) => void;
  setLayerHidden: (layerId: string, hidden: boolean) => void;
  toggleLayer: (layerId: string) => void;
  isLayerHidden: (layerId: string) => boolean;
  setHoveredFeature: (index: number | null) => void;
  setFocusedFeature: (index: number | null) => void;
  setView3d: (on: boolean) => void;
  setPlaying: (playing: boolean) => void;
  togglePlaying: () => void;
  setPlaybackTime: (time: number) => void;
  setPlaybackSpeed: (speed: number) => void;
  resetPlayback: () => void;
  setCommandOpen: (open: boolean) => void;
  /** Ask the workspace map to fit `bbox` ([min_lon, min_lat, max_lon, max_lat]). */
  focusMap: (bbox: [number, number, number, number]) => void;
  setResultHighlight: (id: string | null) => void;
  /** Remember `analysisId` as the one to show for `key` (see `moduleAnalysisKey`). */
  rememberModuleAnalysis: (key: string, analysisId: string) => void;
  /** Forget an analysis everywhere it is remembered (e.g. it was deleted). */
  forgetModuleAnalysis: (analysisId: string) => void;
  trackAnalysis: (analysisId: string, kind: AnalysisKind) => void;
  untrackAnalysis: (analysisId: string) => void;
  /** Clear per-dataset state (hover, focus, playback, hidden layers) when switching datasets. */
  resetWorkspace: () => void;
}

/** Key of `UiState.moduleAnalyses`: one entry per dataset (or none) and module. */
export function moduleAnalysisKey(datasetId: string | null, moduleId: string): string {
  return `${datasetId ?? "none"}:${moduleId}`;
}

const INITIAL_PLAYBACK: PlaybackState = { playing: false, time: 0, speed: 1 };

export const useUi = create<UiState>()((set, get) => ({
  leftOpen: true,
  rightOpen: true,
  drawerOpen: false,
  drawerHeight: DRAWER_DEFAULT_HEIGHT,
  hiddenLayers: [],
  hoveredFeature: null,
  focusedFeature: null,
  view3d: false,
  playback: INITIAL_PLAYBACK,
  commandOpen: false,
  mapFocus: null,
  resultHighlight: null,
  moduleAnalyses: {},
  trackedAnalyses: {},

  setLeftOpen: (leftOpen) => set({ leftOpen }),
  toggleLeft: () => set((s) => ({ leftOpen: !s.leftOpen })),
  setRightOpen: (rightOpen) => set({ rightOpen }),
  toggleRight: () => set((s) => ({ rightOpen: !s.rightOpen })),
  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
  toggleDrawer: () => set((s) => ({ drawerOpen: !s.drawerOpen })),
  setDrawerHeight: (height) => set({ drawerHeight: Math.max(DRAWER_MIN_HEIGHT, Math.round(height)) }),
  setLayerHidden: (layerId, hidden) =>
    set((s) => {
      const has = s.hiddenLayers.includes(layerId);
      if (hidden === has) return s;
      return {
        hiddenLayers: hidden ? [...s.hiddenLayers, layerId] : s.hiddenLayers.filter((id) => id !== layerId),
      };
    }),
  toggleLayer: (layerId) => get().setLayerHidden(layerId, !get().hiddenLayers.includes(layerId)),
  isLayerHidden: (layerId) => get().hiddenLayers.includes(layerId),
  setHoveredFeature: (hoveredFeature) => {
    if (get().hoveredFeature !== hoveredFeature) set({ hoveredFeature });
  },
  setFocusedFeature: (focusedFeature) => set({ focusedFeature }),
  setView3d: (view3d) => set({ view3d }),
  setPlaying: (playing) => set((s) => ({ playback: { ...s.playback, playing } })),
  togglePlaying: () => set((s) => ({ playback: { ...s.playback, playing: !s.playback.playing } })),
  setPlaybackTime: (time) => set((s) => ({ playback: { ...s.playback, time: Math.max(0, time) } })),
  setPlaybackSpeed: (speed) =>
    set((s) => ({ playback: { ...s.playback, speed: Math.min(4, Math.max(0.25, speed)) } })),
  resetPlayback: () => set({ playback: INITIAL_PLAYBACK }),
  setCommandOpen: (commandOpen) => set({ commandOpen }),
  focusMap: (bbox) => set((s) => ({ mapFocus: { bbox, seq: (s.mapFocus?.seq ?? 0) + 1 } })),
  setResultHighlight: (resultHighlight) => {
    if (get().resultHighlight !== resultHighlight) set({ resultHighlight });
  },
  rememberModuleAnalysis: (key, analysisId) => {
    if (get().moduleAnalyses[key] !== analysisId) set((s) => ({ moduleAnalyses: { ...s.moduleAnalyses, [key]: analysisId } }));
  },
  forgetModuleAnalysis: (analysisId) => {
    const entries = Object.entries(get().moduleAnalyses);
    if (entries.some(([, id]) => id === analysisId)) {
      set({ moduleAnalyses: Object.fromEntries(entries.filter(([, id]) => id !== analysisId)) });
    }
  },
  trackAnalysis: (analysisId, kind) => {
    if (get().trackedAnalyses[analysisId] !== kind) set((s) => ({ trackedAnalyses: { ...s.trackedAnalyses, [analysisId]: kind } }));
  },
  untrackAnalysis: (analysisId) => {
    if (analysisId in get().trackedAnalyses) {
      set((s) => ({ trackedAnalyses: Object.fromEntries(Object.entries(s.trackedAnalyses).filter(([id]) => id !== analysisId)) }));
    }
  },
  resetWorkspace: () =>
    set({
      hiddenLayers: [],
      hoveredFeature: null,
      focusedFeature: null,
      playback: INITIAL_PLAYBACK,
      mapFocus: null,
      resultHighlight: null,
    }),
}));
