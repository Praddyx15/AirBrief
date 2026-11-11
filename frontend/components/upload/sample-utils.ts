/**
 * Sample helpers for the cards on `/w` and the command palette. Some samples only exist to test
 * the ingest guards (zip-slip, zip bomb, corrupt XML, missing .prj) -> never offered as cards,
 * though seed still writes them to `samples/` for the RUNBOOK checks.
 */

import { isModuleId, moduleForKind, type ModuleId } from "@/lib/modules";
import { isAnalysisKind, type SampleInfo } from "@/lib/types";

/** Samples that are not shown as cards (attack / failure cases). */
export const HIDDEN_SAMPLES: ReadonlySet<string> = new Set([
  "attack_zipslip",
  "zipbomb_small",
  "corrupt",
  "no_prj",
]);

export function isDemoSample(sample: Pick<SampleInfo, "name">): boolean {
  return !HIDDEN_SAMPLES.has(sample.name);
}

/**
 * A FlightMD report JSON: opened in the flight audit, never loaded as a dataset. Other flight
 * audit samples (the GPX training flight) are datasets whose track the audit reads.
 */
export function isFlightmdReportSample(sample: Pick<SampleInfo, "module" | "filename">): boolean {
  return sample.module === "flight_audit" && sample.filename.toLowerCase().endsWith(".json");
}

/**
 * The workspace module (`?m=`) a sample opens in. `SampleInfo.module` is either "measure", a
 * module id, or an analysis kind (survey → survey, evtol_siting → evtol, show → shows,
 * flight_audit → flight-audit).
 */
export function sampleModule(sample: Pick<SampleInfo, "module">): ModuleId {
  const raw = sample.module;
  if (!raw) return "measure";
  if (isModuleId(raw)) return raw;
  if (isAnalysisKind(raw)) return moduleForKind(raw);
  return "measure";
}

/** Split demo-worthy samples into Tier 0 measurement samples and module demos (order kept). */
export function groupSamples(samples: readonly SampleInfo[]): { measure: SampleInfo[]; modules: SampleInfo[] } {
  const measure: SampleInfo[] = [];
  const modules: SampleInfo[] = [];
  for (const sample of samples) {
    if (!isDemoSample(sample)) continue;
    if (sampleModule(sample) === "measure") measure.push(sample);
    else modules.push(sample);
  }
  return { measure, modules };
}
