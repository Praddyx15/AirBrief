/**
 * The land-area unit chosen for field work (hectare, acre, guntha or bigha) and the size of a
 * bigha, which varies by state. A per-viewer convenience kept in this browser's localStorage
 * (every access guarded: blocked or full storage keeps the choice for this session only). The
 * units and conversions are in `lib/format.ts` (`localAreaParts`).
 */

import * as React from "react";
import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";
import { BIGHA_PRESETS, LOCAL_AREA_UNITS, type LocalAreaUnit } from "./format";

const STORAGE_KEY = "airbrief-area-units";

/** The default bigha (the first preset); every value can be changed in Settings. */
export const DEFAULT_BIGHA_M2 = (BIGHA_PRESETS[0] as (typeof BIGHA_PRESETS)[number]).m2;

/** A bigha between 100 m² and 1 ha covers every regional size in use. */
export const BIGHA_MIN_M2 = 100;
export const BIGHA_MAX_M2 = 10_000;

const guardedStorage: StateStorage = {
  getItem: (name) => {
    try {
      return typeof window === "undefined" ? null : window.localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    try {
      if (typeof window !== "undefined") window.localStorage.setItem(name, value);
    } catch {
      // not saved: the in-memory choice still holds for this session
    }
  },
  removeItem: (name) => {
    try {
      if (typeof window !== "undefined") window.localStorage.removeItem(name);
    } catch {
      // nothing to do
    }
  },
};

interface AreaUnitState {
  unit: LocalAreaUnit;
  bighaM2: number;
  setUnit: (unit: LocalAreaUnit) => void;
  setBighaM2: (m2: number) => void;
  reset: () => void;
}

function validBigha(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= BIGHA_MIN_M2 && value <= BIGHA_MAX_M2;
}

export const useAreaUnits = create<AreaUnitState>()(
  persist(
    (set) => ({
      unit: "ha",
      bighaM2: DEFAULT_BIGHA_M2,
      setUnit: (unit) => set({ unit }),
      setBighaM2: (m2) => {
        if (validBigha(m2)) set({ bighaM2: m2 });
      },
      reset: () => set({ unit: "ha", bighaM2: DEFAULT_BIGHA_M2 }),
    }),
    {
      name: STORAGE_KEY,
      version: 1,
      storage: createJSONStorage(() => guardedStorage),
      skipHydration: true,
      partialize: (s) => ({ unit: s.unit, bighaM2: s.bighaM2 }),
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<AreaUnitState>;
        return {
          ...current,
          unit: LOCAL_AREA_UNITS.includes(p.unit as LocalAreaUnit) ? (p.unit as LocalAreaUnit) : current.unit,
          bighaM2: validBigha(p.bighaM2) ? p.bighaM2 : current.bighaM2,
        };
      },
    },
  ),
);

let hydration: Promise<void> | null = null;

/**
 * The chosen land-area unit and bigha size. The stored choice is read after the first render
 * (the server render and hydration use the defaults, so they always agree).
 */
export function useLocalAreaUnit(): { unit: LocalAreaUnit; bighaM2: number; setUnit: (u: LocalAreaUnit) => void; setBighaM2: (m2: number) => void } {
  React.useEffect(() => {
    hydration ??= Promise.resolve(useAreaUnits.persist.rehydrate()).catch(() => undefined);
  }, []);
  const unit = useAreaUnits((s) => s.unit);
  const bighaM2 = useAreaUnits((s) => s.bighaM2);
  const setUnit = useAreaUnits((s) => s.setUnit);
  const setBighaM2 = useAreaUnits((s) => s.setBighaM2);
  return { unit, bighaM2, setUnit, setBighaM2 };
}
