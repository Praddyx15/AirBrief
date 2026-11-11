/**
 * "Help me choose" (Settings → Assessment choices): a few plain questions that pick the three
 * assessment choices. The mapping (also in docs/features/choices.md):
 *
 * - Digital Sky permission for yellow-zone flights: "Routinely" → `band_ceiling: permission`;
 *   "Sometimes" and "No, green zones only" → `exceedance` (stay strict).
 * - Near airports for eVTOL: "Avoid where possible" → `unverified_band: permission`;
 *   "Keep out completely" → `exclude`.
 * - What reads the ED-318 exports: a national feed with the limits in the properties →
 *   `properties`; an ED-318 / U-space tool or "Not sure" → `geometry`.
 *
 * What AirBrief is mostly used for only decides which questions are asked. A choice whose
 * question is not asked, or not answered, gets no recommendation (null) and Apply leaves it as it is.
 */

import type { BandCeiling, Ed318Limits, UnverifiedBand } from "./types";

export type UseAnswer = "plan" | "review" | "evtol" | "mix";
export type PermissionAnswer = "routinely" | "sometimes" | "green_only";
export type AirportAnswer = "avoid" | "keep_out";
export type ReaderAnswer = "ed318" | "national" | "not_sure";

export interface ChoiceAnswers {
  use: UseAnswer | null;
  permission: PermissionAnswer | null;
  airports: AirportAnswer | null;
  reader: ReaderAnswer | null;
}

export const NO_ANSWERS: ChoiceAnswers = { use: null, permission: null, airports: null, reader: null };

export const USE_LABEL: Readonly<Record<UseAnswer, string>> = {
  plan: "Planning my own flights",
  review: "Reviewing flights after the fact",
  evtol: "eVTOL or vertiport studies",
  mix: "A mix of these",
};

export const PERMISSION_LABEL: Readonly<Record<PermissionAnswer, string>> = {
  routinely: "Routinely",
  sometimes: "Sometimes",
  green_only: "No, green zones only",
};

export const AIRPORT_LABEL: Readonly<Record<AirportAnswer, string>> = {
  avoid: "Avoid them where possible",
  keep_out: "Keep out completely",
};

export const READER_LABEL: Readonly<Record<ReaderAnswer, string>> = {
  ed318: "A U-space or EUROCAE ED-318 tool",
  national: "A national feed with the limits in the properties",
  not_sure: "Not sure",
};

/** Which questions a use asks: the flight audit's for flight work, eVTOL's for eVTOL work. */
export function asked(use: UseAnswer | null): { permission: boolean; airports: boolean } {
  return { permission: use !== "evtol", airports: use === null || use === "evtol" || use === "mix" };
}

export interface Recommendation<T extends string> {
  value: T;
  reason: string;
}

/** null: the question was not asked or not answered, so the choice stays as it is */
export interface Recommendations {
  bandCeiling: Recommendation<BandCeiling> | null;
  unverifiedBand: Recommendation<UnverifiedBand> | null;
  ed318Limits: Recommendation<Ed318Limits> | null;
}

export function recommend(a: ChoiceAnswers): Recommendations {
  const ask = asked(a.use);
  // an answer to a question that is no longer asked is kept, but doesn't count
  const permission = ask.permission ? a.permission : null;
  const airports = ask.airports ? a.airports : null;

  let bandCeiling: Recommendation<BandCeiling> | null = null;
  if (permission === "routinely") {
    bandCeiling = { value: "permission", reason: "You fly in yellow zones with permission, so flying above a band's ceiling is reported as such, not as a fail." };
  } else if (permission === "sometimes") {
    bandCeiling = { value: "exceedance", reason: "Only sometimes: the audit stays strict, so a flight without permission is never missed." };
  } else if (permission === "green_only") {
    bandCeiling = { value: "exceedance", reason: "You stay in green zones, so anything above a band's ceiling is an exceedance." };
  }

  const unverifiedBand: Recommendation<UnverifiedBand> | null =
    airports === "keep_out"
      ? { value: "exclude", reason: "Sites and corridors stay out of every airport's 0–5 km band." }
      : airports === "avoid"
        ? { value: "permission", reason: "Airport sites are still scored (0 for airspace) and named, so you can weigh them." }
        : null;

  const ed318Limits: Recommendation<Ed318Limits> | null =
    a.reader === "national"
      ? { value: "properties", reason: "Your feed reads the vertical limits from the feature properties." }
      : a.reader === "ed318"
        ? { value: "geometry", reason: "ED-318 tools read the vertical limits from each geometry's layer." }
        : a.reader === "not_sure"
          ? { value: "geometry", reason: "Not sure: the ED-318 layout is the standard one." }
          : null;

  return { bandCeiling, unverifiedBand, ed318Limits };
}
