"use client";

/**
 * Result parts of the SORA 2.5 operational volume: the evidence label, the contingency-volume
 * terms (stacked bar + table), SORA 2.5 Table 2 with the cell that applies, the adjacent-area
 * figures and the outdoor assemblies within 1 km.
 */

import * as React from "react";
import { Check, Info } from "lucide-react";
import { categorical, rgbaCss } from "@/lib/colors";
import { formatArea, formatDate, formatLength, formatNumber, formatPercent, lengthParts, plural } from "@/lib/format";
import { ASSEMBLY_BAND_LABEL, ASSEMBLY_KIND_LABEL, ASSEMBLY_SOURCE_LABEL, GRID_SOURCE_LABEL, GRB_RULE_LABEL } from "@/lib/labels";
import type { Units } from "@/lib/store";
import type { AssemblyHit, GroundRiskResult, IgrcTable, OperationalVolumeResult, VolumeTerm } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { KpiGrid, KpiTile } from "@/components/ui/kpi-tile";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ModuleSection, ToneBadge, pointBounds, useFocusMap } from "../shared";

/** The persistent scope label of the operational-volume evidence. */
export const EVIDENCE_LABEL = "SORA Step 2 / Step 8 evidence — not a GRC, ARC or SAIL";

export function EvidenceLabel({ text }: { text?: string | null }) {
  return (
    <Alert variant="info" role="note" icon={<Info aria-hidden />}>
      <AlertTitle>{text || EVIDENCE_LABEL}</AlertTitle>
      <AlertDescription>
        Sizes, densities and assemblies for a SORA application. The final ground risk class, mitigations, air risk and
        SAIL are decided elsewhere (by the operator, the authority or a compliance tool).
      </AlertDescription>
    </Alert>
  );
}

const HORIZONTAL = ["S_GNSS", "S_pos", "S_map", "S_R", "S_CM"] as const;

/** The horizontal terms of S_CV as one bar (width ∝ metres), with a labelled key. */
function ScvBar({ terms, total, units }: { terms: readonly VolumeTerm[]; total: number; units: Units }) {
  const parts = HORIZONTAL.map((name) => terms.find((t) => t.name === name)).filter((t): t is VolumeTerm => !!t);
  const sum = parts.reduce((s, t) => s + Math.max(0, t.value_m), 0) || 1;
  const description = `S_CV ${formatLength(total, units)}: ${parts.map((t) => `${t.label} ${formatLength(t.value_m, units)}`).join(", ")}`;
  return (
    <figure className="space-y-2">
      <div role="img" aria-label={description} className="flex h-4 w-full overflow-hidden rounded-sm border border-border">
        {parts.map((t, i) => (
          <span
            key={t.name}
            className="h-full border-r border-bg last:border-r-0"
            style={{ width: `${(Math.max(0, t.value_m) / sum) * 100}%`, backgroundColor: rgbaCss(categorical(i)) }}
          />
        ))}
      </div>
      <figcaption>
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-muted">
          {parts.map((t, i) => (
            <li key={t.name} className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block size-2.5 rounded-sm" style={{ backgroundColor: rgbaCss(categorical(i)) }} />
              <span>
                <span className="font-mono text-fg">{t.name}</span> {formatLength(t.value_m, units)} ({formatPercent(Math.max(0, t.value_m) / sum, { fraction: true, maximumFractionDigits: 0 })})
              </span>
            </li>
          ))}
        </ul>
      </figcaption>
    </figure>
  );
}

const AXIS_ORDER: Record<VolumeTerm["axis"], number> = { horizontal: 0, vertical: 1, buffer: 2, adjacent: 3 };

export function VolumeTerms({ volume, units }: { volume: OperationalVolumeResult; units: Units }) {
  const columns = React.useMemo<DataTableColumn<VolumeTerm>[]>(
    () => [
      {
        id: "term",
        header: "Term",
        rowHeader: true,
        cell: (t) => (
          <span>
            <span className="font-mono">{t.name}</span>
            <span className="block text-xs text-fg-muted">{t.label}</span>
          </span>
        ),
        sortValue: (t) => AXIS_ORDER[t.axis],
      },
      { id: "value", header: "Value", numeric: true, cell: (t) => formatLength(t.value_m, units), sortValue: (t) => t.value_m },
      {
        id: "formula",
        header: "Formula (SI) and source",
        cell: (t) => (
          <span className="block min-w-48">
            <span className="font-mono text-xs">{t.formula}</span>
            <span className="block text-xs text-fg-muted">{t.source}</span>
          </span>
        ),
      },
    ],
    [units],
  );
  return (
    <ModuleSection title="Operational volume">
      <KpiGrid label="Operational volume sizes">
        <KpiTile label="Flight geography height" value={lengthParts(volume.fg_height_m, units)} explanation="H_FG, planned maximum above ground" />
        <KpiTile label="Contingency volume" value={lengthParts(volume.s_cv_m, units)} explanation={`S_CV around the flight geography; ceiling H_CV ${formatLength(volume.h_cv_m, units)} AGL`} />
        <KpiTile label="Ground risk buffer" value={lengthParts(volume.grb_m, units)} explanation={`S_GRB beyond the contingency volume (${GRB_RULE_LABEL[volume.grb_rule]})`} />
        <KpiTile
          label="Density grid"
          value={lengthParts(volume.grid_m, units)}
          explanation={`Cell side: ${GRID_SOURCE_LABEL[volume.grid_source]}`}
          tone={volume.grid_source === "coarsened" ? "warn" : undefined}
          toneLabel={volume.grid_source === "coarsened" ? "Coarsened" : undefined}
        />
      </KpiGrid>
      <ScvBar terms={volume.terms} total={volume.s_cv_m} units={units} />
      <DataTable
        data={volume.terms}
        columns={columns}
        getRowId={(t) => t.name}
        caption="Contingency volume, ground risk buffer and adjacent-area terms"
        dense
      />
      <p className="text-xs text-fg-muted">{volume.reference}</p>
    </ModuleSection>
  );
}

export function IgrcGrid({ table, igrc, smallUasRule }: { table: IgrcTable; igrc: number | null; smallUasRule: boolean }) {
  const col = table.column_index;
  const row = table.row_index;
  return (
    <ModuleSection title="SORA 2.5 Table 2">
      <Table scrollLabel="SORA 2.5 Table 2">
        <TableCaption>
          Intrinsic ground risk class by population density (rows) and aircraft (columns: maximum characteristic dimension
          and speed). The cell that applies is marked.
        </TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead>Density band</TableHead>
            {table.columns.map((c, j) => (
              <TableHead key={c.label} className={cn("text-right", j === col && "text-accent-text")}>
                {c.label}
                {j === col ? <span className="sr-only"> (this aircraft)</span> : null}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {table.rows.map((r, i) => (
            <TableRow key={r.label}>
              <TableHead scope="row" className={cn("font-normal text-fg", i === row && "font-medium text-accent-text")}>
                {r.label}
                {i === row ? <span className="sr-only"> (this operation)</span> : null}
              </TableHead>
              {r.values.map((v, j) => {
                const active = i === row && j === col;
                return (
                  <TableCell
                    key={j}
                    className={cn(
                      "num text-right",
                      v === null && "text-fg-muted",
                      active && "rounded-sm bg-accent/15 font-semibold text-fg outline outline-2 -outline-offset-2 outline-accent",
                    )}
                  >
                    <span className="inline-flex items-center justify-end gap-1">
                      {active ? <Check aria-hidden className="size-3.5 text-accent" /> : null}
                      {v === null ? <abbr title="Not part of SORA (certified category)">—</abbr> : v}
                      {v === null ? <span className="sr-only">not part of SORA</span> : null}
                      {active ? <span className="sr-only">, applies</span> : null}
                    </span>
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {smallUasRule ? (
        <p className="text-xs text-fg-muted">
          Small-UAS rule: take-off mass ≤ 250 g and speed ≤ 25 m/s give iGRC 1 whatever the cell.
        </p>
      ) : null}
      {col === null ? (
        <p className="text-xs text-warn">The aircraft is beyond the last column (over 40 m or 200 m/s): no iGRC from this table.</p>
      ) : igrc === null ? (
        <p className="text-xs text-warn">The cell is not part of SORA (certified category).</p>
      ) : null}
      <p className="text-xs text-fg-muted">{table.reference}</p>
    </ModuleSection>
  );
}

/** The SORA density band label of `density` from the Table 2 rows (rows 1… are the bands). */
function bandOf(table: IgrcTable | null | undefined, density: number): string | null {
  if (!table) return null;
  const bounds = [5, 50, 500, 5_000, 50_000];
  const index = bounds.findIndex((b) => density < b);
  return table.rows[index === -1 ? bounds.length + 1 : index + 1]?.label ?? null;
}

export function AdjacentArea({ result, units }: { result: GroundRiskResult; units: Units }) {
  const volume = result.volume;
  if (!volume) return null;
  const mean = result.adjacent_mean_density_per_km2 ?? null;
  const coverage = result.adjacent_coverage ?? null;
  const note = result.issues.find((i) => i.code === "adjacent_not_required")?.message;
  const raw = formatLength(volume.adjacent_distance_km * 1000, units);
  return (
    <ModuleSection title="Adjacent area (Step 8)">
      {mean === null && result.adjacent_area_km2 == null ? (
        <p className="text-sm text-fg-muted">
          {note ?? "The adjacent area was not computed (switched off in the form)."}
        </p>
      ) : (
        <KpiGrid label="Adjacent area">
          <KpiTile
            label="Average density"
            value={mean === null ? "Unknown" : formatNumber(mean, { maximumFractionDigits: 0 })}
            unit={mean === null ? undefined : "people/km²"}
            explanation={mean === null ? "Outside the data pack" : `Band ${bandOf(result.igrc_table, mean) ?? "—"}`}
          />
          <KpiTile
            label="People in the ring"
            value={result.adjacent_people == null ? "Unknown" : formatNumber(result.adjacent_people, { maximumFractionDigits: 0 })}
            explanation={
              result.adjacent_area_km2 == null
                ? undefined
                : `Ring of ${formatArea(result.adjacent_area_km2 * 1_000_000, units)}${coverage !== null && coverage < 0.999 ? ", counted inside the data pack only" : ""}`
            }
          />
          <KpiTile
            label="Outer limit"
            value={lengthParts(volume.adjacent_outer_km * 1000, units)}
            explanation={`From the operational volume: v × 180 s = ${raw}, kept between ${formatLength(5_000, units)} and ${formatLength(35_000, units)}`}
          />
          <KpiTile
            label="Data coverage"
            value={coverage === null ? "—" : formatPercent(coverage, { fraction: true, maximumFractionDigits: 1 })}
            tone={coverage !== null && coverage < 0.999 ? "warn" : undefined}
            toneLabel={coverage !== null && coverage < 0.999 ? "Partly outside the pack" : undefined}
            explanation="Share of the ring inside the data pack"
          />
        </KpiGrid>
      )}
    </ModuleSection>
  );
}

function windowText(a: AssemblyHit): string {
  if (!a.start_utc && !a.end_utc) return "—";
  return `${formatDate(a.start_utc)} – ${formatDate(a.end_utc)}`;
}

export function Assemblies({ result, units }: { result: GroundRiskResult; units: Units }) {
  const focus = useFocusMap();
  const hits = result.assemblies ?? [];
  const layerMissing = result.issues.some((i) => i.code === "assemblies_layer_missing");
  const columns = React.useMemo<DataTableColumn<AssemblyHit>[]>(
    () => [
      {
        id: "name",
        header: "Assembly",
        rowHeader: true,
        cell: (a) => (
          <span>
            {a.name}
            <span className="block text-xs text-fg-muted">
              {[a.kind ? (ASSEMBLY_KIND_LABEL[a.kind] ?? a.kind) : null, ASSEMBLY_SOURCE_LABEL[a.source]].filter(Boolean).join(" · ")}
            </span>
          </span>
        ),
        sortValue: (a) => a.name,
      },
      {
        id: "size",
        header: "Size",
        cell: (a) => (
          <span>
            <ToneBadge tone={a.band === "lt_40k" ? "neutral" : "warn"} label={ASSEMBLY_BAND_LABEL[a.band]} />
            {a.capacity !== null ? <span className="block text-xs text-fg-muted">{formatNumber(a.capacity)} people</span> : null}
          </span>
        ),
        sortValue: (a) => a.capacity,
      },
      {
        id: "distance",
        header: "Distance",
        numeric: true,
        cell: (a) => (a.distance_m <= 0 ? "Inside" : formatLength(a.distance_m, units)),
        sortValue: (a) => a.distance_m,
      },
      { id: "when", header: "When", cell: (a) => <span className="text-xs">{windowText(a)}</span> },
    ],
    [units],
  );
  return (
    <ModuleSection title={`Outdoor assemblies within 1 km (${hits.length})`}>
      <DataTable
        data={hits}
        columns={columns}
        getRowId={(a, i) => `${a.source}-${a.name}-${i}`}
        caption="Outdoor assemblies within 1 km of the operational volume"
        onRowClick={(a) => focus(pointBounds(a.lon, a.lat, 1500))}
        getRowLabel={(a) => `Show ${a.name} on the map`}
        dense
        emptyState={
          <EmptyState
            compact
            title="No assemblies within 1 km"
            description={
              layerMissing
                ? "The data pack has no assemblies layer; only your entries were checked."
                : "No stadium, market, theme park or entered event lies within 1 km of the operational volume."
            }
          />
        }
      />
      <p className="text-xs text-fg-muted">
        {plural(hits.length, "assembly", "assemblies")} found. Population maps do not include event crowds: check scheduled
        events with the organisers or the authority. Bands follow SORA 2.5 Tables 8–13 (&lt; 40k, 40k–400k, &gt; 400k).
      </p>
    </ModuleSection>
  );
}
