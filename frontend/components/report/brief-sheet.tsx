"use client";

/**
 * The operation evidence brief as one printable A4 sheet: "Evidence, not authorisation" first,
 * the title and meta line, the synthetic-data banner and watermark, the cross-checks, a vector map
 * of the operation (`geometry-svg.tsx`, robust in print) next to the operation, one card per
 * assessed section, every section without an analysis listed as "Not assessed" with its reason,
 * and the provenance footer (versions, data packs with source, licence and content hash, SHA-256
 * of the parameters, inputs, operation and brief, and whether the bundle is signed).
 *
 * The brief's texts are metric (the API writes them); they are shown in the reader's units with
 * `localizeUnits`. Tables are cut to `SHEET_TABLE_ROWS` rows to keep one page; the bundle's CSV
 * tables (or, in the demo, the analysis exports) hold every row.
 */

import * as React from "react";
import { formatDate, localizeUnits } from "@/lib/format";
import type { Units } from "@/lib/store";
import type { Brief, BriefFact, BriefFactTone, BriefPart, BriefSection, BriefTable, Issue } from "@/lib/types";
import { cn } from "@/lib/utils";
import { GeometrySvg, type GeometryLayer } from "./geometry-svg";
import styles from "./brief-sheet.module.css";

/** Rows of each table on the sheet (the API's brief carries up to 8; the bundle CSV all). */
export const SHEET_TABLE_ROWS = 5;
/** Notes shown per part before "+N more". */
const SHEET_NOTES = 2;
/** Notes and cross-checks that only say the data are synthetic: the banner and watermark say it once. */
const SYNTHETIC_CODES: ReadonlySet<string> = new Set(["synthetic_data", "synthetic_datapack"]);

const TONE_CLASS: Readonly<Record<BriefFactTone, string>> = {
  neutral: styles.muted,
  ok: styles.ok,
  warn: styles.warn,
  danger: styles.danger,
  info: styles.info,
};

const STATUS: Readonly<Record<BriefSection["status"], { label: string; className: string }>> = {
  info: { label: "As given", className: styles.info },
  assessed: { label: "Assessed", className: styles.ok },
  partial: { label: "Partly assessed", className: styles.warn },
  not_assessed: { label: "Not assessed", className: styles.muted },
};

const VERDICT: Readonly<Record<string, { label: string; className: string }>> = {
  pass: { label: "Check result: Pass", className: styles.ok },
  caution: { label: "Check result: Caution", className: styles.warn },
  fail: { label: "Check result: Fail", className: styles.danger },
};

const SEVERITY: Readonly<Record<Issue["severity"], { label: string; className: string }>> = {
  critical: { label: "Critical", className: styles.danger },
  warning: { label: "Warning", className: styles.warn },
  info: { label: "Info", className: styles.info },
};

function utc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  return Number.isNaN(t) ? iso : `${new Date(t).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** " via <host>" for a layer a mirror delivered, so a fallback shows in the provenance. */
function via(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return ` via ${new URL(url).hostname}`;
  } catch {
    return "";
  }
}

function Facts({ facts, units }: { facts: readonly BriefFact[]; units: Units }) {
  if (facts.length === 0) return null;
  return (
    <dl className={styles.facts}>
      {facts.map((f, i) => (
        <div key={`${f.label}-${i}`}>
          <dt>{f.label}</dt>
          <dd>
            {localizeUnits(f.text, units)}
            {f.tone !== "neutral" ? <span className={cn(styles.badge, TONE_CLASS[f.tone])}>{f.tone_label ?? f.tone}</span> : null}
            {f.note ? <small>{localizeUnits(f.note, units)}</small> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function SheetTable({ table, units, fullListNote }: { table: BriefTable; units: Units; fullListNote: string }) {
  const rows = table.rows.slice(0, SHEET_TABLE_ROWS);
  return (
    <>
      <table className={styles.table}>
        <caption>{table.caption}</caption>
        <thead>
          <tr>
            {table.columns.map((c, i) => (
              <th key={`${c}-${i}`} scope="col" className={table.numeric[i] ? styles.n : undefined}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, i) => (
                <td key={i} className={table.numeric[i] ? styles.n : undefined}>
                  {localizeUnits(cell, units)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {table.total > rows.length ? (
        <p className={styles.more}>
          Showing {rows.length} of {table.total} rows; {fullListNote}
        </p>
      ) : null}
    </>
  );
}

function Part({
  part,
  showTitle,
  units,
  fullListNote,
  headingId,
  synthetic,
}: {
  part: BriefPart;
  showTitle: boolean;
  units: Units;
  fullListNote: string;
  headingId: string;
  synthetic: boolean;
}) {
  const verdict = part.verdict ? VERDICT[part.verdict] : null;
  const allNotes = synthetic ? part.notes.filter((n) => !SYNTHETIC_CODES.has(n.code)) : part.notes;
  const notes = allNotes.slice(0, SHEET_NOTES);
  return (
    <div>
      {showTitle ? (
        <h4 id={headingId} className={styles.partTitle}>
          {part.title}
        </h4>
      ) : null}
      {part.banners.map((b) => (
        <p key={b} className={styles.banner} role="note">
          {localizeUnits(b, units)}
        </p>
      ))}
      {verdict || part.summary ? (
        <p className={styles.summary}>
          {verdict ? <span className={cn(styles.badge, verdict.className)}>{verdict.label}</span> : null}
          {verdict && part.summary ? " " : null}
          {part.summary ? localizeUnits(part.summary, units) : null}
        </p>
      ) : null}
      <Facts facts={part.facts} units={units} />
      {part.tables.map((t) => (
        <SheetTable key={t.caption} table={t} units={units} fullListNote={fullListNote} />
      ))}
      {notes.length > 0 ? (
        <ul className={styles.notes} aria-label={`Notes of ${part.title}`}>
          {notes.map((n, i) => (
            <li key={`${n.code}-${i}`}>
              <span className={cn(styles.badge, SEVERITY[n.severity].className)}>{SEVERITY[n.severity].label}</span>{" "}
              {localizeUnits(n.message, units)}
            </li>
          ))}
          {allNotes.length > notes.length ? (
            <li className={styles.muted}>
              +{allNotes.length - notes.length} more note{allNotes.length - notes.length === 1 ? "" : "s"} in the analysis
            </li>
          ) : null}
        </ul>
      ) : null}
      {part.disclaimer ? <p className={styles.disclaimer}>{part.disclaimer}</p> : null}
    </div>
  );
}

function SectionCard({
  section,
  units,
  fullListNote,
  idPrefix,
  synthetic,
}: {
  section: BriefSection;
  units: Units;
  fullListNote: string;
  idPrefix: string;
  synthetic: boolean;
}) {
  const status = STATUS[section.status];
  const verdict = section.verdict ? VERDICT[section.verdict] : null;
  const headingId = `${idPrefix}-${section.key}`;
  // One analysis: named in the card head ("from Airspace & terrain"); several: one heading each.
  const single = section.parts.length === 1 ? section.parts[0] : null;
  return (
    <section className={styles.card} aria-labelledby={headingId}>
      <div className={styles.cardHead}>
        <h3 id={headingId} className={styles.cardTitle}>
          {section.title}
        </h3>
        <span className={cn(styles.badge, status.className)}>{status.label}</span>
        {verdict && section.parts.length > 1 ? <span className={cn(styles.badge, verdict.className)}>{verdict.label}</span> : null}
        {single && single.title !== section.title ? <span className={styles.source}>from {single.title}</span> : null}
      </div>
      {section.parts.map((part, i) => (
        <Part
          key={`${part.analysis_id}-${i}`}
          part={part}
          showTitle={section.parts.length > 1}
          units={units}
          fullListNote={fullListNote}
          headingId={`${headingId}-${i}`}
          synthetic={synthetic}
        />
      ))}
    </section>
  );
}

function Provenance({ brief, idPrefix }: { brief: Brief; idPrefix: string }) {
  const p = brief.provenance;
  return (
    <footer className={styles.provenance} aria-labelledby={`${idPrefix}-provenance`}>
      <h3 id={`${idPrefix}-provenance`} className={styles.cardTitle}>
        Provenance
      </h3>
      <p>
        AirBrief {p.airbrief_version || "—"} · result schema {p.schema_version || "—"} · brief schema {p.brief_schema_version || "—"} ·
        generated {utc(p.generated_utc)} · analyses {utc(p.started_utc)} to {utc(p.finished_utc)}
      </p>
      <p>
        Brief SHA-256 <span className={styles.mono}>{p.brief_sha256 || "computed by the AirBrief API (not in the demo)"}</span> ·
        Operation SHA-256 <span className={styles.mono}>{p.operation_sha256 || "computed by the AirBrief API (not in the demo)"}</span>
      </p>
      <table className={styles.table}>
        <caption>Analyses: SHA-256 of the canonical parameters and of the input file</caption>
        <thead>
          <tr>
            <th scope="col">Analysis</th>
            <th scope="col">Data pack, finished</th>
            <th scope="col">SHA-256</th>
          </tr>
        </thead>
        <tbody>
          {p.analyses.map((a) => (
            <tr key={a.analysis_id}>
              <td>
                {a.title}
                <br />
                <span className={styles.mono}>{a.analysis_id}</span>
              </td>
              <td>
                {a.datapack ?? "No data pack"}
                {a.datapack_synthetic ? " (synthetic)" : ""}
                <br />
                {utc(a.finished_utc)}
              </td>
              <td>
                Parameters <span className={styles.mono}>{a.params_sha256}</span>
                <br />
                Input file{" "}
                {a.input_sha256 ? (
                  <>
                    <span className={styles.mono}>{a.input_sha256}</span>
                    {a.dataset_filename ? ` (${a.dataset_filename})` : ""}
                  </>
                ) : a.dataset_id ? (
                  "not recorded"
                ) : (
                  "none: the inputs are in the parameters"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {p.datapacks.map((pack) => {
        const used = pack.layers.filter((l) => l.used_by.length > 0);
        const other = pack.layers.length - used.length;
        return (
          <p key={pack.name}>
            <strong>
              Data pack {pack.name} v{pack.version ?? "—"}
            </strong>
            , created {pack.created_utc ? utc(pack.created_utc) : "—"}
            {pack.synthetic ? " — SYNTHETIC" : ""}
            {pack.installed ? "" : " — no longer installed on this server"}.{" "}
            {pack.content_sha256 ? (
              <>
                Content SHA-256 <span className={styles.mono}>{pack.content_sha256}</span>.{" "}
              </>
            ) : pack.content_sha256 === null ? (
              "No content hashes (built before they were added). "
            ) : null}
            {used.map((l) => `${l.key}: ${l.source || "—"}${via(l.source_url)} (${l.licence || "licence not stated"})`).join("; ")}
            {other > 0 ? `; ${other} other layer${other === 1 ? "" : "s"} not read by these analyses` : ""}
            {pack.attribution.length > 0 ? `. ${pack.attribution.join(" ")}` : ""}
          </p>
        );
      })}
      {p.signature_note ? (
        <p>
          <strong>{p.signature_note}</strong>
        </p>
      ) : null}
      <p className={styles.more}>
        Hashes are SHA-256 of {p.canonical_json || "the canonical JSON (keys sorted, no whitespace)"}; input files of their uploaded bytes.
      </p>
    </footer>
  );
}

export interface BriefSheetProps {
  brief: Brief;
  /** the map layers (`map-layers.ts` `briefMapLayers`) */
  layers: readonly GeometryLayer[];
  units: Units;
  /** where every table row can be found ("the bundle's CSV tables hold them all") */
  fullListNote: string;
  /** prefix of the sheet's element ids (the hidden measuring copy uses another) */
  idPrefix: string;
  /** print scale (0.6–1) applied to the text when printing */
  fit?: number;
  /** the hidden copy that measures the print scale: off-screen at the printed width, never printed */
  measuring?: boolean;
  sheetRef?: React.Ref<HTMLElement>;
}

/** One A4 sheet of the brief (see the module comment). */
export function BriefSheet({ brief, layers, units, fullListNote, idPrefix, fit = 1, measuring = false, sheetRef }: BriefSheetProps) {
  const operation = brief.sections.find((s) => s.key === "operation") ?? null;
  const assessed = brief.sections.filter((s) => s.key !== "operation" && s.status !== "not_assessed");
  const missing = brief.sections.filter((s) => s.status === "not_assessed");
  const op = brief.operation;
  const synthetic = brief.watermark !== null;
  const warnings = synthetic ? brief.warnings.filter((w) => !SYNTHETIC_CODES.has(w.code)) : brief.warnings;
  const meta = [
    `Generated ${formatDate(brief.provenance.generated_utc, { withTime: true })}`,
    op.operator ? `Operator ${op.operator}` : null,
    op.aircraft?.name ? `Aircraft ${op.aircraft.name}` : null,
    op.planned_start_utc ? `Window from ${utc(op.planned_start_utc)}` : null,
    brief.provenance.brief_sha256 ? `Brief SHA-256 ${brief.provenance.brief_sha256.slice(0, 16)}…` : null,
  ].filter((m): m is string => m !== null);
  return (
    <div className={cn(styles.frame, measuring && styles.measure)}>
      <article
        ref={sheetRef}
        className={styles.sheet}
        aria-labelledby={`${idPrefix}-title`}
        style={{ "--brief-fit": String(fit) } as React.CSSProperties}
      >
        {/* Decorative (the banner below says it in words): drawn by CSS from data-text. */}
        {brief.watermark ? <div className={styles.watermark} aria-hidden="true" data-text={brief.watermark} /> : null}
        <p className={styles.notice} role="note">
          {brief.notice}
        </p>
        <header className={styles.header}>
          <h2 id={`${idPrefix}-title`} className={styles.title}>
            {brief.title}
          </h2>
          <p className={styles.meta}>
            {meta.map((m) => (
              <span key={m}>{m}</span>
            ))}
          </p>
        </header>
        {brief.watermark ? (
          <p className={styles.synthetic} role="note">
            {brief.watermark}: computed with a synthetic demo data pack; terrain, population and obstacles are invented.
          </p>
        ) : null}
        {warnings.length > 0 ? (
          <ul className={styles.checks} aria-label="Cross-checks">
            {warnings.map((w, i) => (
              <li key={`${w.code}-${i}`}>
                <span className={cn(styles.badge, SEVERITY[w.severity].className)}>{SEVERITY[w.severity].label}</span>
                <span>{localizeUnits(w.message, units)}</span>
              </li>
            ))}
          </ul>
        ) : null}
        {operation ? (
          <section className={cn(styles.card, styles.wide)} aria-labelledby={`${idPrefix}-operation`}>
            <div className={styles.cardHead}>
              <h3 id={`${idPrefix}-operation`} className={styles.cardTitle}>
                {operation.title}
              </h3>
              <span className={cn(styles.badge, STATUS.info.className)}>{STATUS.info.label}</span>
            </div>
            <Facts facts={operation.parts[0]?.facts ?? []} units={units} />
          </section>
        ) : null}
        {/* The map leads the two balanced columns of evidence. */}
        <div className={styles.grid}>
          <div className={styles.map}>
            <GeometrySvg
              layers={layers}
              units={units}
              title={`Map of the operation evidence: ${layers.map((l) => l.label).join(", ") || "no geometry"}`}
              maxHeight={460}
              minHeight={320}
            />
          </div>
          {assessed.map((s) => (
            <SectionCard key={s.key} section={s} units={units} fullListNote={fullListNote} idPrefix={idPrefix} synthetic={synthetic} />
          ))}
        </div>
        {missing.length > 0 ? (
          <section className={cn(styles.card, styles.wide)} aria-labelledby={`${idPrefix}-not-assessed`}>
            <div className={styles.cardHead}>
              <h3 id={`${idPrefix}-not-assessed`} className={styles.cardTitle}>
                Not assessed in this brief
              </h3>
            </div>
            <ul className={styles.naList}>
              {missing.map((s) => (
                <li key={s.key}>
                  <strong>{s.title}</strong> <span className={cn(styles.badge, styles.muted)}>Not assessed</span>{" "}
                  <span className={styles.muted}>{s.reason}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <Provenance brief={brief} idPrefix={idPrefix} />
        <p className={styles.foot}>
          <span>
            <strong>Evidence, not authorisation.</strong> AirBrief operation evidence brief.
          </span>
          <span>No go/no-go, no flight plan, no waypoints.</span>
        </p>
      </article>
    </div>
  );
}
