/**
 * The operation evidence brief in the static demo (`NEXT_PUBLIC_DEMO=1`), where no API runs.
 *
 * `python -m seed.demo_export` writes one brief per demo analysis (`demo/briefs/{id}.json`, the
 * API's own answer for that analysis alone), and `mergeDemoBriefs` puts several together the way
 * `airbrief_core.brief.build_brief` does. The operation section is built here from the form.
 * Only the brief and operation hashes stay empty: the API computes them.
 */

import { formatCoord, formatDuration, formatLength, formatNumber, formatSpeed, formatUtcOffset } from "./format";
import {
  ANALYSIS_KINDS,
  BRIEF_SECTION_ORDER,
  type AircraftSummary,
  type AnalysisInfo,
  type AnalysisKind,
  type BBoxList,
  type Brief,
  type BriefAnalysisProvenance,
  type BriefFact,
  type BriefPart,
  type BriefSection,
  type BriefSectionKey,
  type Issue,
  type OperationMeta,
  type OperationSummary,
  type PackProvenance,
  type UasPreset,
  type Verdict,
} from "./types";

const NOT_GIVEN = "Not given";
const VERDICT_ORDER: Record<Verdict, number> = { pass: 0, caution: 1, fail: 2 };

/** Minutes east of UTC → the "YYYY-MM-DD HH:MM" wall-clock time of an ISO instant. */
function localMinute(iso: string, offsetMin: number): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  return new Date(t + offsetMin * 60_000).toISOString().slice(0, 16).replace("T", " ");
}

function utcMinute(iso: string): string {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "—" : `${new Date(t).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

function fact(label: string, text: string, value: BriefFact["value"] = null, unit: string | null = null, note: string | null = null): BriefFact {
  return { label, text, value, unit, note, tone: "neutral", tone_label: null };
}

/** The aircraft as the API resolves it: the preset's values, overridden by any value given. */
export function resolveAircraft(operation: OperationMeta, presets: Readonly<Record<string, UasPreset>>): AircraftSummary | null {
  const info = operation.aircraft;
  if (!info) return null;
  const preset = info.preset ? presets[info.preset] : undefined;
  const custom = {
    name: info.name?.trim() || null,
    takeoff_mass_kg: info.takeoff_mass_kg ?? null,
    max_dimension_m: info.max_dimension_m ?? null,
    max_speed_ms: info.max_speed_ms ?? null,
  };
  const hasCustom = Object.values(custom).some((v) => v !== null);
  if (!preset && !hasCustom) return null;
  return {
    preset: info.preset ?? null,
    name: custom.name ?? preset?.name ?? null,
    takeoff_mass_kg: custom.takeoff_mass_kg ?? preset?.takeoff_mass_kg ?? null,
    max_dimension_m: custom.max_dimension_m ?? preset?.max_dimension_m ?? null,
    max_speed_ms: custom.max_speed_ms ?? preset?.max_speed_ms ?? null,
    source: !preset ? "custom" : hasCustom ? "preset_with_overrides" : "preset",
  };
}

export function operationSummary(operation: OperationMeta, presets: Readonly<Record<string, UasPreset>>): OperationSummary {
  const offset = operation.utc_offset_min ?? 0;
  return {
    operator: operation.operator?.trim() || null,
    reference: operation.reference?.trim() || null,
    aircraft: resolveAircraft(operation, presets),
    pilot_position: operation.pilot_position ? [...operation.pilot_position] : null,
    planned_start_utc: operation.planned_start_utc ?? null,
    planned_end_utc: operation.planned_end_utc ?? null,
    utc_offset_min: offset,
    utc_offset_label: formatUtcOffset(offset),
    notes: operation.notes?.trim() || null,
  };
}

const AIRCRAFT_SOURCE_NOTE: Record<NonNullable<AircraftSummary["source"]>, string> = {
  preset: "Class preset (illustrative values)",
  custom: "Entered by the operator",
  preset_with_overrides: "Class preset with the operator's values",
};

/** The operation section, worded like `airbrief_core.brief._operation_section`. */
export function operationSection(op: OperationSummary): BriefSection {
  const a = op.aircraft;
  const facts: BriefFact[] = [
    fact("Operator", op.operator ?? NOT_GIVEN),
    fact("Reference", op.reference ?? NOT_GIVEN),
    fact("Aircraft", a ? a.name ?? "Custom aircraft" : NOT_GIVEN, null, null, a?.source ? AIRCRAFT_SOURCE_NOTE[a.source] : null),
  ];
  if (a) {
    facts.push(
      fact("Take-off mass", a.takeoff_mass_kg !== null ? `${formatNumber(a.takeoff_mass_kg)} kg` : NOT_GIVEN, a.takeoff_mass_kg, "kg"),
      fact("Max dimension", a.max_dimension_m !== null ? formatLength(a.max_dimension_m) : NOT_GIVEN, a.max_dimension_m, "m"),
      fact("Max speed", a.max_speed_ms !== null ? formatSpeed(a.max_speed_ms) : NOT_GIVEN, a.max_speed_ms, "m/s"),
    );
  }
  if (op.planned_start_utc) {
    const start = op.planned_start_utc;
    const end = op.planned_end_utc;
    if (end) {
      const seconds = (Date.parse(end) - Date.parse(start)) / 1000;
      facts.push(
        fact(
          "Planned window",
          `${localMinute(start, op.utc_offset_min)}–${localMinute(end, op.utc_offset_min).slice(11)} ${op.utc_offset_label}`,
          start,
          null,
          `${utcMinute(start)} to ${utcMinute(end)}; ${formatDuration(seconds)}`,
        ),
      );
    } else {
      facts.push(fact("Planned window", `${localMinute(start, op.utc_offset_min)} (${op.utc_offset_label})`, start, null, utcMinute(start)));
    }
  } else {
    facts.push(fact("Planned window", NOT_GIVEN));
  }
  const pos = op.pilot_position;
  facts.push(fact("Pilot / take-off position", pos && pos.length >= 2 ? formatCoord(pos[0], pos[1]) : NOT_GIVEN));
  if (op.notes) facts.push(fact("Notes", op.notes));
  const part: BriefPart = {
    analysis_id: "",
    kind: "operation",
    title: "Operation",
    status: "assessed",
    verdict: null,
    summary: null,
    banners: [],
    facts,
    tables: [],
    notes: [],
    disclaimer: null,
  };
  return { key: "operation", title: "Operation", status: "info", verdict: null, reason: null, parts: [part] };
}

function rank(kind: AnalysisKind): number {
  const i = ANALYSIS_KINDS.indexOf(kind);
  return i < 0 ? ANALYSIS_KINDS.length : i;
}

function provenanceOf(brief: Brief): BriefAnalysisProvenance | null {
  return brief.provenance.analyses[0] ?? null;
}

function worst(verdicts: (Verdict | null)[]): Verdict | null {
  return verdicts.reduce<Verdict | null>((acc, v) => (v !== null && (acc === null || VERDICT_ORDER[v] > VERDICT_ORDER[acc]) ? v : acc), null);
}

function overlaps(a: BBoxList, b: BBoxList): boolean {
  return !(b[0] > a[2] || b[2] < a[0] || b[1] > a[3] || b[3] < a[1]);
}

/** Cross-checks between analyses (datasets, areas, repeated kinds), as the API words them. */
function crossChecks(parts: Brief[]): Issue[] {
  const out: Issue[] = [];
  const datasets = new Set(parts.map((b) => provenanceOf(b)?.dataset_id).filter((id): id is string => Boolean(id)));
  if (datasets.size > 1) {
    out.push({
      code: "different_datasets",
      severity: "warning",
      feature_index: null,
      message: `The analyses come from ${datasets.size} different datasets: check they describe the same operation.`,
    });
  }
  // Analyses grouped by overlapping bounds (a chain of overlaps links a group), as the API does.
  const boxed = parts.filter((b): b is Brief & { extent: BBoxList } => b.extent !== null);
  const parent = boxed.map((_, i) => i);
  const root = (i: number): number => {
    let r = i;
    while (parent[r] !== r) r = parent[r] as number;
    return r;
  };
  boxed.forEach((a, i) => {
    for (let j = i + 1; j < boxed.length; j += 1) {
      if (overlaps(a.extent, (boxed[j] as Brief & { extent: BBoxList }).extent)) parent[root(j)] = root(i);
    }
  });
  const groups = new Map<number, Brief[]>();
  boxed.forEach((b, i) => groups.set(root(i), [...(groups.get(root(i)) ?? []), b]));
  if (groups.size > 1) {
    const listed = [...groups.values()].map((g) => g.map((b) => provenanceOf(b)?.title ?? "").join(", ")).join("; ");
    out.push({
      code: "areas_differ",
      severity: "warning",
      feature_index: null,
      message: `The analyses cover ${groups.size} separate areas that do not overlap (${listed}): check they belong to the same operation.`,
    });
  }
  const counts = new Map<string, { n: number; title: string }>();
  for (const b of parts) {
    const p = provenanceOf(b);
    if (!p) continue;
    const entry = counts.get(p.kind) ?? { n: 0, title: p.title };
    entry.n += 1;
    counts.set(p.kind, entry);
  }
  for (const { n, title } of counts.values()) {
    if (n > 1) {
      out.push({
        code: "several_runs",
        severity: "info",
        feature_index: null,
        message: `${n} ${title} analyses are included; each is shown with its own parameters.`,
      });
    }
  }
  return out;
}

/** Ground-risk defaults (GroundRiskParams): stored parameters hold only the values a request gave. */
const GROUND_RISK_DEFAULT_DIMENSION_M = 1.0;
const GROUND_RISK_DEFAULT_SPEED_MS = 15.0;

/**
 * Checks of the operation against the analyses' parameters, worded and ordered as the API does:
 * the aircraft against every ground-risk analysis, then the planned window against every
 * environment analysis, the analyses in canonical order (kind, then finish time, then id).
 */
export function parameterChecks(analyses: readonly AnalysisInfo[], op: OperationSummary): Issue[] {
  const aircraftChecks: Issue[] = [];
  const windowChecks: Issue[] = [];
  const a = op.aircraft;
  const ordered = [...analyses].sort(
    (x, y) => rank(x.kind) - rank(y.kind) || (x.finished_at ?? "").localeCompare(y.finished_at ?? "") || x.id.localeCompare(y.id),
  );
  for (const info of ordered) {
    const p = info.params;
    if (info.kind === "ground_risk" && a) {
      const dim = typeof p.max_dimension_m === "number" ? p.max_dimension_m : GROUND_RISK_DEFAULT_DIMENSION_M;
      const speed = typeof p.max_speed_ms === "number" ? p.max_speed_ms : GROUND_RISK_DEFAULT_SPEED_MS;
      const bigger = a.max_dimension_m !== null && a.max_dimension_m > dim + 1e-9;
      const faster = a.max_speed_ms !== null && a.max_speed_ms > speed + 1e-9;
      if (bigger || faster) {
        aircraftChecks.push({
          code: "aircraft_exceeds_assumption",
          severity: "warning",
          feature_index: null,
          message:
            `The ground-risk analysis assumed ${formatLength(dim)} and ${formatSpeed(speed)}, but the aircraft is ` +
            `${formatLength(a.max_dimension_m)} and ${formatSpeed(a.max_speed_ms)}: the iGRC column may be too low. ` +
            "Run it again with the aircraft's values.",
        });
      }
    }
    if (info.kind === "environment" && op.planned_start_utc && typeof p.date === "string") {
      const localDate = localMinute(op.planned_start_utc, op.utc_offset_min).slice(0, 10);
      if (p.date !== localDate) {
        windowChecks.push({
          code: "window_date_differs",
          severity: "warning",
          feature_index: null,
          message:
            `The environment evidence is for ${p.date}, but the planned window starts on ${localDate} (local): ` +
            "sun times and the daylight check may not apply.",
        });
      }
    }
  }
  return [...aircraftChecks, ...windowChecks];
}

function mergePacks(parts: Brief[]): PackProvenance[] {
  const byName = new Map<string, PackProvenance>();
  for (const b of parts) {
    for (const pack of b.provenance.datapacks) {
      const known = byName.get(pack.name);
      if (!known) {
        byName.set(pack.name, { ...pack, layers: pack.layers.map((l) => ({ ...l, used_by: [...l.used_by] })) });
        continue;
      }
      for (const layer of pack.layers) {
        const target = known.layers.find((l) => l.key === layer.key);
        if (!target) known.layers.push({ ...layer, used_by: [...layer.used_by] });
        else target.used_by = [...new Set([...target.used_by, ...layer.used_by])].sort((x, y) => rank(x) - rank(y));
      }
    }
  }
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * Several one-analysis briefs as one: canonical order (kind, finish time, id), every section of
 * `BRIEF_SECTION_ORDER` ("not assessed" where no analysis contributes), cross-checks and merged
 * provenance. `analyses` are the same runs, for `parameterChecks`.
 */
export function mergeDemoBriefs(
  parts: Brief[],
  operation: OperationMeta,
  presets: Readonly<Record<string, UasPreset>>,
  analyses: readonly AnalysisInfo[] = [],
): Brief {
  const sorted = [...parts].sort((a, b) => {
    const pa = provenanceOf(a);
    const pb = provenanceOf(b);
    if (!pa || !pb) return 0;
    return rank(pa.kind) - rank(pb.kind) || pa.finished_utc.localeCompare(pb.finished_utc) || pa.analysis_id.localeCompare(pb.analysis_id);
  });
  const op = operationSummary(operation, presets);
  const sectionOf = (b: Brief, key: BriefSectionKey) => b.sections.find((s) => s.key === key) ?? null;
  const sections: BriefSection[] = BRIEF_SECTION_ORDER.map((key) => {
    if (key === "operation") return operationSection(op);
    const found = sorted.flatMap((b) => sectionOf(b, key)?.parts ?? []);
    const template = sorted.map((b) => sectionOf(b, key)).find((s) => s !== null) ?? null;
    const title = template?.title ?? key;
    if (found.length === 0) {
      const reason = sorted.map((b) => sectionOf(b, key)).find((s) => s?.status === "not_assessed")?.reason ?? null;
      return { key, title, status: "not_assessed", verdict: null, reason, parts: [] };
    }
    const partial = found.some((p) => p.status === "partial");
    return {
      key,
      title,
      status: partial ? "partial" : "assessed",
      verdict: worst(found.map((p) => p.verdict)),
      reason: partial ? "Some evidence could not be assessed: see the banners." : null,
      parts: found,
    };
  });
  const seen = new Set<string>();
  const warnings = [...sorted.flatMap((b) => b.warnings), ...crossChecks(sorted), ...parameterChecks(analyses, op)].filter((w) => {
    const key = `${w.code}|${w.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const extents = sorted.map((b) => b.extent).filter((e): e is BBoxList => e !== null);
  const runs = sorted.map(provenanceOf).filter((p): p is BriefAnalysisProvenance => p !== null);
  const started = runs.map((a) => a.started_utc).filter(Boolean).sort();
  const finished = runs.map((a) => a.finished_utc).filter(Boolean).sort();
  const inputs = new Map(sorted.flatMap((b) => b.provenance.inputs).map((d) => [d.id, d]));
  const first = sorted[0];
  const synthetic = sorted.some((b) => b.synthetic);
  return {
    title: `Operation evidence brief${op.reference ? ` — ${op.reference}` : ""}`,
    notice: first?.notice ?? "Evidence, not authorisation.",
    synthetic,
    watermark: synthetic ? sorted.find((b) => b.watermark)?.watermark ?? null : null,
    operation: op,
    sections,
    warnings,
    extent:
      extents.length > 0
        ? [
            Math.min(...extents.map((e) => e[0])),
            Math.min(...extents.map((e) => e[1])),
            Math.max(...extents.map((e) => e[2])),
            Math.max(...extents.map((e) => e[3])),
          ]
        : null,
    provenance: {
      airbrief_version: first?.provenance.airbrief_version ?? "",
      schema_version: first?.provenance.schema_version ?? "",
      brief_schema_version: first?.provenance.brief_schema_version ?? "",
      generated_utc: new Date().toISOString(),
      started_utc: started[0] ?? null,
      finished_utc: finished.at(-1) ?? null,
      canonical_json: first?.provenance.canonical_json ?? "",
      operation_sha256: "",
      brief_sha256: "",
      analyses: runs,
      datapacks: mergePacks(sorted),
      inputs: [...inputs.values()],
    },
  };
}
