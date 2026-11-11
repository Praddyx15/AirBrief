/**
 * The height limits a flight audit actually used, shared by the module's results, its map legend
 * and the printable report, so no screen shows the requested limit where zone rules lowered it.
 */

import { formatHeight } from "@/lib/format";
import type { Units } from "@/lib/store";
import type { FlightAuditResult } from "@/lib/types";

export interface AuditLimits {
  /** the limit asked for in the form */
  requested: number;
  /** highest and lowest limit actually used along the flight */
  highest: number;
  lowest: number;
  /** zone ceilings or the default rules lowered the requested limit at every point */
  cappedEverywhere: boolean;
  /** the limit changes along the flight */
  varies: boolean;
  /** what lowered it, for the explanations */
  capSource: string;
}

/**
 * The limits actually used: at each point the lower of the requested limit and the zone ceiling
 * (the default rules outside mapped zones count as a zone). The profile is decimated, while the
 * exceedances keep the limit of every run, so both are read.
 */
export function auditLimits(result: Pick<FlightAuditResult, "ceiling_agl_m" | "profile" | "exceedances" | "zones">): AuditLimits {
  const requested = result.ceiling_agl_m;
  const used = [...result.profile.map((p) => p.limit_agl_m), ...result.exceedances.map((e) => e.limit_m)].filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  const highest = used.length > 0 ? Math.max(...used) : requested;
  const lowest = used.length > 0 ? Math.min(...used) : requested;
  return {
    requested,
    highest,
    lowest,
    cappedEverywhere: highest < requested - 1e-6,
    varies: lowest < highest - 1e-6,
    capSource: result.zones.length === 0 ? "the default rules outside mapped zones" : "the zone rules",
  };
}

/** The limit used, never the requested one alone when rules lowered it: "Limit 120 m: the default rules … cap your 150 m". */
export function describeLimit(limits: AuditLimits, units: Units): string {
  const fmt = (m: number) => formatHeight(m, units);
  if (limits.varies) {
    return limits.cappedEverywhere
      ? `Limit ${fmt(limits.lowest)} to ${fmt(limits.highest)} along the flight: ${limits.capSource} cap your ${fmt(limits.requested)}`
      : `Limit ${fmt(limits.requested)}, down to ${fmt(limits.lowest)} inside some zones`;
  }
  return limits.cappedEverywhere
    ? `Limit ${fmt(limits.highest)}: ${limits.capSource} cap your ${fmt(limits.requested)}`
    : `Limit ${fmt(limits.requested)}`;
}

/** The verdict sentence for a flight without exceedances, naming the limit actually used. */
export function describeWithinLimit(limits: AuditLimits, maxAglM: number, units: Units): string {
  if (limits.varies) return `Stayed within the height limit everywhere; highest ${formatHeight(maxAglM, units)} above ground.`;
  const cap = limits.cappedEverywhere ? ` (${limits.capSource} cap your ${formatHeight(limits.requested, units)})` : "";
  return `Stayed at or below ${formatHeight(limits.highest, units)} above ground${cap}.`;
}
