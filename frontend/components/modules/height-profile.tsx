"use client";

/**
 * Terrain-true height profile along a route (airspace check, eVTOL corridor). The ceiling and
 * minimum lines are terrain + the height above ground; stretches above the ceiling are shaded
 * red (hatched), below the minimum amber (dotted). Nothing depends on colour or on seeing the
 * chart: the legend names every series, the shadings carry labels, and "Show table" has a
 * status column.
 */

import * as React from "react";
import { Table2 } from "lucide-react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { EMPTY, formatHeight, formatLength, formatNumber, heightParts, localizeUnits, type FormattedValue } from "@/lib/format";
import { useUnits, type Units } from "@/lib/store";
import type { HeightSegment, ProfilePoint } from "@/lib/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ToneBadge, downsample, niceTicks } from "./shared";

const MAX_POINTS = 1000;
const M_PER_FT = 0.3048;
const M_PER_MI = 1609.344;
/** Heights within this of a limit count as at the limit (as airbrief_core LIMIT_TOLERANCE_M, rounded results). */
const TOLERANCE_M = 0.05;

export interface HeightProfileProps {
  points: readonly ProfilePoint[];
  units: Units;
  /** accessible name and caption */
  title: string;
  /** legend name of the flight line, e.g. "Flight level (1,020 m AMSL)" */
  flightLabel: string;
  /** the minimum height above ground (amber line); null: none */
  minAgl?: number | null;
  /** a ceiling above ground used everywhere (eVTOL); default: each sample's free-flight limit */
  ceilingAgl?: number | null;
  /** stretches below the minimum (amber) and above the ceiling (red), metres along the route */
  below?: readonly HeightSegment[];
  above?: readonly HeightSegment[];
  height?: number;
}

type Status = "above" | "below" | "within" | "unknown";

const STATUS_LABEL: Record<Status, string> = {
  above: "Above ceiling",
  below: "Below minimum",
  within: "Within limits",
  unknown: "—",
};

function sampleStatus(p: ProfilePoint, minAgl: number | null, ceilingAgl: number | null): Status {
  const agl = p.agl_m;
  if (agl === null || agl === undefined || !Number.isFinite(agl)) return "unknown";
  const limit = ceilingAgl ?? p.limit_agl_m;
  if (limit !== null && limit !== undefined && agl > limit + TOLERANCE_M) return "above";
  if (minAgl !== null && agl < minAgl - TOLERANCE_M) return "below";
  return "within";
}

function PatternSwatch({ kind }: { kind: "hatch" | "dots" }) {
  return (
    <svg aria-hidden width="20" height="12" viewBox="0 0 20 12" className="shrink-0 rounded-sm">
      {kind === "hatch" ? (
        <>
          <rect width="20" height="12" fill="var(--danger)" fillOpacity="0.15" stroke="var(--danger)" />
          <path d="M-2 10 L6 2 M4 12 L14 2 M12 12 L22 2" stroke="var(--danger)" strokeWidth="1.5" />
        </>
      ) : (
        <>
          <rect width="20" height="12" fill="var(--warn)" fillOpacity="0.15" stroke="var(--warn)" />
          {[3, 9, 15].flatMap((x) => [3, 9].map((y) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.3" fill="var(--warn)" />))}
        </>
      )}
    </svg>
  );
}

export function HeightProfileChart({
  points,
  units,
  title,
  flightLabel,
  minAgl = null,
  ceilingAgl = null,
  below = [],
  above = [],
  height = 240,
}: HeightProfileProps) {
  const [asTable, setAsTable] = React.useState(false);
  const captionId = React.useId();
  const patternBase = React.useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const hatchId = `${patternBase}-above`;
  const dotsId = `${patternBase}-below`;
  const imperial = units === "imperial";
  const yUnit = imperial ? "ft" : "m";
  const xUnit = imperial ? "mi" : "km";
  const toY = React.useCallback((m: number | null | undefined) => (m === null || m === undefined || !Number.isFinite(m) ? null : imperial ? m / M_PER_FT : m), [imperial]);
  const toX = React.useCallback((m: number) => (imperial ? m / M_PER_MI : m / 1000), [imperial]);

  const data = React.useMemo(() => downsample(points, MAX_POINTS), [points]);
  // Above sea level when the profile has terrain; heights above ground otherwise (no DEM).
  const amsl = React.useMemo(() => data.some((p) => p.terrain_m !== null && p.terrain_m !== undefined), [data]);
  const chartData = React.useMemo(
    () =>
      data.map((p) => {
        const terrain = p.terrain_m ?? null;
        const limit = ceilingAgl ?? p.limit_agl_m ?? null;
        if (amsl) {
          return {
            x: toX(p.distance_m),
            terrain: toY(terrain),
            flight: toY(p.altitude_amsl_m ?? (terrain !== null && p.agl_m !== null ? terrain + p.agl_m : null)),
            ceiling: toY(terrain !== null && limit !== null ? terrain + limit : null),
            minimum: toY(terrain !== null && minAgl !== null ? terrain + minAgl : null),
            agl: toY(p.agl_m),
          };
        }
        return { x: toX(p.distance_m), terrain: null, flight: toY(p.agl_m), ceiling: toY(limit), minimum: toY(minAgl), agl: toY(p.agl_m) };
      }),
    [data, amsl, ceilingAgl, minAgl, toX, toY],
  );
  const domain = React.useMemo(() => {
    const ys = chartData.flatMap((d) => [d.terrain, d.flight, d.ceiling, d.minimum]).filter((v): v is number => v !== null && Number.isFinite(v));
    if (ys.length === 0) return [0, 1] as [number, number];
    const lo = Math.min(...ys);
    const hi = Math.max(...ys);
    const pad = Math.max((hi - lo) * 0.08, imperial ? 10 : 3);
    // Heights above ground start at 0 unless the data goes below it (ground above the flight level).
    const bottom = amsl ? Math.floor(lo - pad) : Math.min(0, Math.floor(lo - pad));
    return [bottom, Math.ceil(hi + pad)] as [number, number];
  }, [chartData, amsl, imperial]);
  const xAxis = React.useMemo(() => {
    const xs = chartData.map((d) => d.x);
    return niceTicks(Math.min(...xs), Math.max(...xs));
  }, [chartData]);
  const hasCeiling = chartData.some((d) => d.ceiling !== null);
  const hasMinimum = minAgl !== null && chartData.some((d) => d.minimum !== null);

  if (points.length < 2) return null;

  const heightLabel = amsl ? `Height (${yUnit} AMSL)` : `Height above ground (${yUnit})`;
  const fmt = (v: number) => `${formatNumber(v, { maximumFractionDigits: 0 })} ${yUnit}`;

  return (
    <figure className="space-y-2" aria-label={title}>
      <div className="flex items-center justify-between gap-2">
        <figcaption className="text-xs font-medium uppercase tracking-wide text-fg-muted">{title}</figcaption>
        <Button type="button" variant="ghost" size="sm" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          <Table2 aria-hidden /> {asTable ? "Show chart" : "Show table"}
        </Button>
      </div>
      {asTable ? (
        <div role="region" aria-labelledby={captionId} tabIndex={0} className="max-h-64 overflow-auto rounded border border-border">
          <table className="w-full text-xs">
            <caption id={captionId} className="sr-only">
              {title}
            </caption>
            <thead className="sticky top-0 bg-surface">
              <tr>
                <th scope="col" className="px-2 py-1 text-right font-medium">Distance</th>
                <th scope="col" className="px-2 py-1 text-right font-medium">Terrain (AMSL)</th>
                <th scope="col" className="px-2 py-1 text-right font-medium">Flight (AMSL)</th>
                <th scope="col" className="px-2 py-1 text-right font-medium">Above ground</th>
                <th scope="col" className="px-2 py-1 text-right font-medium">Ceiling (AGL)</th>
                <th scope="col" className="px-2 py-1 text-left font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.map((p, i) => {
                const status = sampleStatus(p, minAgl, ceilingAgl);
                const limit = ceilingAgl ?? p.limit_agl_m;
                return (
                  <tr key={i} className="border-t border-border">
                    <td className="num px-2 py-0.5 text-right">{formatLength(p.distance_m, units)}</td>
                    <td className="num px-2 py-0.5 text-right">{formatHeight(p.terrain_m ?? null, units)}</td>
                    <td className="num px-2 py-0.5 text-right">{formatHeight(p.altitude_amsl_m ?? null, units)}</td>
                    <td className="num px-2 py-0.5 text-right">{formatHeight(p.agl_m ?? null, units)}</td>
                    <td className="num px-2 py-0.5 text-right">{limit === null || limit === undefined ? "Permission" : formatHeight(limit, units)}</td>
                    <td className={status === "above" ? "px-2 py-0.5 text-danger" : status === "below" ? "px-2 py-0.5 text-warn" : "px-2 py-0.5"}>
                      {STATUS_LABEL[status]}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ height }} className="w-full">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={chartData} margin={{ top: 14, right: 16, bottom: 4, left: 4 }}>
              <defs>
                <pattern id={hatchId} width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="8" height="8" fill="var(--danger)" fillOpacity="0.12" />
                  <line x1="0" y1="0" x2="0" y2="8" stroke="var(--danger)" strokeWidth="2" strokeOpacity="0.55" />
                </pattern>
                <pattern id={dotsId} width="7" height="7" patternUnits="userSpaceOnUse">
                  <rect width="7" height="7" fill="var(--warn)" fillOpacity="0.12" />
                  <circle cx="3.5" cy="3.5" r="1.4" fill="var(--warn)" fillOpacity="0.7" />
                </pattern>
              </defs>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis
                dataKey="x"
                type="number"
                domain={["dataMin", "dataMax"]}
                ticks={xAxis.ticks}
                tickFormatter={(v: number) => formatNumber(v, { maximumFractionDigits: xAxis.decimals })}
                stroke="var(--text-muted)"
                fontSize={11}
                label={{ value: `Distance (${xUnit})`, position: "insideBottomRight", offset: -2, fontSize: 11, fill: "var(--text-muted)" }}
              />
              <YAxis
                stroke="var(--text-muted)"
                fontSize={11}
                width={60}
                domain={domain}
                allowDataOverflow
                tickFormatter={(v: number) => formatNumber(v, { maximumFractionDigits: 0 })}
                label={{ value: yUnit, angle: -90, position: "insideLeft", fontSize: 11, fill: "var(--text-muted)" }}
              />
              <ChartTooltip
                contentStyle={{ background: "var(--elevated)", border: "1px solid var(--border)", fontSize: 12 }}
                labelFormatter={(v: number) => `${formatNumber(v, { maximumFractionDigits: 2 })} ${xUnit}`}
                formatter={(v: number, name: string) => [fmt(v), name]}
              />
              {above.map((s, i) => (
                <ReferenceArea
                  key={`above-${i}`}
                  x1={toX(s.start_m)}
                  x2={toX(s.end_m)}
                  fill={`url(#${hatchId})`}
                  stroke="var(--danger)"
                  strokeDasharray="4 3"
                  ifOverflow="hidden"
                  label={{ value: `Above ceiling (${formatHeight(s.peak_agl_m, units)} AGL)`, position: "insideTop", fontSize: 10, fill: "var(--danger)" }}
                />
              ))}
              {below.map((s, i) => (
                <ReferenceArea
                  key={`below-${i}`}
                  x1={toX(s.start_m)}
                  x2={toX(s.end_m)}
                  fill={`url(#${dotsId})`}
                  stroke="var(--warn)"
                  strokeDasharray="2 2"
                  ifOverflow="hidden"
                  label={{ value: `Below minimum (${formatHeight(s.peak_agl_m, units)} AGL)`, position: "insideBottom", fontSize: 10, fill: "var(--warn)" }}
                />
              ))}
              {amsl ? (
                <Area
                  type="linear"
                  dataKey="terrain"
                  name="Terrain (AMSL)"
                  stroke="var(--text-muted)"
                  fill="var(--text-muted)"
                  fillOpacity={0.25}
                  baseValue={domain[0]}
                  isAnimationActive={false}
                  connectNulls
                />
              ) : null}
              {hasCeiling ? (
                <Line
                  type="stepAfter"
                  dataKey="ceiling"
                  name="Free-flight ceiling"
                  stroke="var(--danger)"
                  strokeWidth={1.5}
                  strokeDasharray="6 4"
                  dot={false}
                  isAnimationActive={false}
                />
              ) : null}
              {hasMinimum ? (
                <Line
                  type="linear"
                  dataKey="minimum"
                  name="Minimum above ground"
                  stroke="var(--warn)"
                  strokeWidth={1.5}
                  strokeDasharray="2 3"
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              ) : null}
              <Line
                type="linear"
                dataKey="flight"
                name={flightLabel}
                stroke="var(--accent)"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-muted" aria-label="Chart legend">
        {amsl ? (
          <li className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-3 w-5 rounded-sm border border-fg-muted bg-fg-muted/25" />
            Terrain (AMSL)
          </li>
        ) : null}
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-0 w-5 border-t-2 border-accent" />
          {flightLabel}
        </li>
        {hasCeiling ? (
          <li className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-0 w-5 border-t-2 border-dashed border-danger" />
            Free-flight ceiling{amsl ? " (terrain + ceiling)" : ""}
          </li>
        ) : null}
        {hasMinimum ? (
          <li className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-0 w-5 border-t-2 border-dotted border-warn" />
            Minimum {formatHeight(minAgl, units)} above ground
          </li>
        ) : null}
        {above.length > 0 ? (
          <li className="flex items-center gap-1.5">
            <PatternSwatch kind="hatch" />
            Above the ceiling (hatched)
          </li>
        ) : null}
        {below.length > 0 ? (
          <li className="flex items-center gap-1.5">
            <PatternSwatch kind="dots" />
            Below the minimum (dotted)
          </li>
        ) : null}
      </ul>
      <p className="sr-only">{heightLabel}.</p>
    </figure>
  );
}

export interface HeightStretch {
  /** "below_min_agl-1": the id of the stretch's feature in the result's `heights` layer */
  id: string;
  kind: "below_min_agl" | "above_ceiling";
  /** 1-based within its kind */
  index: number;
  segment: HeightSegment;
}

export const STRETCH_LABEL: Record<HeightStretch["kind"], string> = {
  below_min_agl: "Below the minimum",
  above_ceiling: "Above the ceiling",
};

/** The stretches of a result, below the minimum first, each in route order. */
export function heightStretches(below: readonly HeightSegment[] | undefined, above: readonly HeightSegment[] | undefined): HeightStretch[] {
  const rows: HeightStretch[] = [];
  (below ?? []).forEach((segment, i) => rows.push({ id: `below_min_agl-${i + 1}`, kind: "below_min_agl", index: i + 1, segment }));
  (above ?? []).forEach((segment, i) => rows.push({ id: `above_ceiling-${i + 1}`, kind: "above_ceiling", index: i + 1, segment }));
  return rows;
}

/** Stretches of a route below the minimum height above ground or above the ceiling (click: zoom to it). */
export function HeightStretchTable({
  stretches,
  units,
  onSelect,
  caption = "Stretches of the route below the minimum height above ground or above the ceiling",
}: {
  stretches: readonly HeightStretch[];
  units: Units;
  onSelect?: (stretch: HeightStretch) => void;
  caption?: string;
}) {
  const columns = React.useMemo<DataTableColumn<HeightStretch>[]>(
    () => [
      {
        id: "kind",
        header: "Stretch",
        rowHeader: true,
        cell: (s) => (
          <ToneBadge tone={s.kind === "above_ceiling" ? "danger" : "warn"} label={`${STRETCH_LABEL[s.kind]} ${s.index}`} />
        ),
        sortValue: (s) => `${s.kind === "below_min_agl" ? 0 : 1}-${String(s.index).padStart(4, "0")}`,
      },
      { id: "from", header: "From", numeric: true, cell: (s) => formatLength(s.segment.start_m, units), sortValue: (s) => s.segment.start_m },
      { id: "to", header: "To", numeric: true, cell: (s) => formatLength(s.segment.end_m, units), sortValue: (s) => s.segment.end_m },
      { id: "length", header: "Length", numeric: true, cell: (s) => formatLength(s.segment.length_m, units), sortValue: (s) => s.segment.length_m },
      {
        id: "peak",
        header: "Lowest / highest AGL",
        numeric: true,
        cell: (s) => `${formatHeight(s.segment.peak_agl_m, units)} ${s.kind === "below_min_agl" ? "lowest" : "highest"}`,
        sortValue: (s) => s.segment.peak_agl_m,
      },
      { id: "limit", header: "Limit", numeric: true, cell: (s) => formatHeight(s.segment.limit_m, units), sortValue: (s) => s.segment.limit_m },
    ],
    [units],
  );
  return (
    <DataTable
      data={stretches}
      columns={columns}
      getRowId={(s) => s.id}
      caption={caption}
      onRowClick={onSelect ? (s) => onSelect(s) : undefined}
      getRowLabel={(s) => `Zoom to ${STRETCH_LABEL[s.kind].toLowerCase()} ${s.index}`}
      dense
      emptyState={<EmptyState compact title="No stretches" description="The route stays between the minimum height above ground and the ceiling." />}
    />
  );
}

/** Shown when an above-take-off or AMSL plan could not be related to the ground (no DEM). */
export function HeightsNotAssessedBanner({ assessed, message }: { assessed: boolean | undefined; message?: string | null }) {
  const units = useUnits();
  if (assessed !== false) return null;
  return (
    <Alert variant="warn" role="status">
      <AlertTitle>Heights above ground not assessed</AlertTitle>
      <AlertDescription>
        {message
          ? localizeUnits(message, units)
          : "No terrain model covers the flight, so the planned height could not be related to the ground below it."}
      </AlertDescription>
    </Alert>
  );
}

/** A height range "30–120 m" (or one value when both ends round the same) for KPI tiles. */
export function heightRangeParts(min: number | null | undefined, max: number | null | undefined, units: Units): FormattedValue {
  const lo = heightParts(min, units);
  const hi = heightParts(max, units);
  if (lo.value === EMPTY || hi.value === EMPTY) return lo.value === EMPTY ? hi : lo;
  if (lo.value === hi.value) return hi;
  return { value: `${lo.value}–${hi.value}`, unit: hi.unit, text: `${lo.value}–${hi.text}` };
}
