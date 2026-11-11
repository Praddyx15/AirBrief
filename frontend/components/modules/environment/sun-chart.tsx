"use client";

/**
 * Sun elevation over the analysed local day (apparent elevation, every 5 min) with the horizon,
 * the civil-twilight line (−6°), the photogrammetry sun window as a horizontal band and the
 * planned window as a vertical band. A table (every 15 min) is the non-visual alternative.
 */

import * as React from "react";
import { Table2 } from "lucide-react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
  type TooltipProps,
} from "recharts";
import { formatBearing, formatNumber } from "@/lib/format";
import type { PlannedWindow, SunTrackPoint } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { minutesToHhmm } from "./time";

interface ChartRow {
  hour: number;
  minute: number;
  elevation: number;
  azimuth: number;
}

const HOUR_TICKS = [0, 3, 6, 9, 12, 15, 18, 21, 24];
const TABLE_STEP_MIN = 15;

function hourLabel(hour: number): string {
  return `${String(Math.round(hour)).padStart(2, "0")}:00`;
}

function degrees(value: number): string {
  return `${formatNumber(value, { decimals: 1 })}°`;
}

function SunTooltip({ active, payload }: TooltipProps<number, string>) {
  const row = active ? (payload?.[0]?.payload as ChartRow | undefined) : undefined;
  if (!row) return null;
  return (
    <div className="rounded border border-border bg-elevated px-2 py-1 text-xs text-fg">
      <p className="num font-medium">{minutesToHhmm(row.minute)}</p>
      <p className="num">Elevation {degrees(row.elevation)}</p>
      <p className="num">Azimuth {formatBearing(row.azimuth)}</p>
    </div>
  );
}

export interface SunElevationChartProps {
  track: readonly SunTrackPoint[];
  window: PlannedWindow | null;
  sunWindow: { min: number; max: number };
  /** e.g. "UTC+05:30" */
  offsetLabel: string;
  height?: number;
}

export function SunElevationChart({ track, window, sunWindow, offsetLabel, height = 220 }: SunElevationChartProps) {
  const [asTable, setAsTable] = React.useState(false);
  const titleId = React.useId();
  const captionId = React.useId();
  const rows = React.useMemo<ChartRow[]>(
    () => track.map((p) => ({ hour: p.minute / 60, minute: p.minute, elevation: p.elevation_deg, azimuth: p.azimuth_deg })),
    [track],
  );
  const tableRows = React.useMemo(() => rows.filter((r) => Math.round(r.minute) % TABLE_STEP_MIN === 0), [rows]);
  if (rows.length < 2) return null;

  const minElev = Math.min(...rows.map((r) => r.elevation));
  const maxElev = Math.max(...rows.map((r) => r.elevation));
  const yMin = Math.floor(Math.min(minElev, -10) / 10) * 10;
  const yMax = Math.ceil(Math.max(maxElev, 10) / 10) * 10;
  const windowBand =
    window && window.start_minute < 1440
      ? { x1: window.start_minute / 60, x2: Math.min(window.end_minute, 1440) / 60 }
      : null;
  const title = `Sun elevation (${offsetLabel})`;

  return (
    <figure className="space-y-2" aria-labelledby={titleId}>
      <div className="flex items-center justify-between gap-2">
        <figcaption id={titleId} className="text-xs font-medium uppercase tracking-wide text-fg-muted">
          {title}
        </figcaption>
        <Button type="button" variant="ghost" size="sm" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          <Table2 aria-hidden /> {asTable ? "Show chart" : "Show table"}
        </Button>
      </div>
      {asTable ? (
        // Scrolls: focusable so keyboard users can scroll it (WCAG 2.1.1), named by the caption.
        <div role="region" aria-labelledby={captionId} tabIndex={0} className="max-h-64 overflow-auto rounded border border-border">
          <table className="w-full text-xs">
            <caption id={captionId} className="sr-only">
              Sun elevation and azimuth every {TABLE_STEP_MIN} minutes, local time {offsetLabel}
            </caption>
            <thead className="sticky top-0 bg-surface">
              <tr>
                <th scope="col" className="px-2 py-1 text-left font-medium">Local time</th>
                <th scope="col" className="px-2 py-1 text-right font-medium">Elevation</th>
                <th scope="col" className="px-2 py-1 text-right font-medium">Azimuth</th>
              </tr>
            </thead>
            <tbody>
              {tableRows.map((r) => (
                <tr key={r.minute} className="border-t border-border">
                  <th scope="row" className="num px-2 py-0.5 text-left font-normal">
                    {r.minute >= 1440 ? "24:00" : minutesToHhmm(r.minute)}
                  </th>
                  <td className="num px-2 py-0.5 text-right">{degrees(r.elevation)}</td>
                  <td className="num px-2 py-0.5 text-right">{formatBearing(r.azimuth)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ height }} className="w-full">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis
                dataKey="hour"
                type="number"
                domain={[0, 24]}
                ticks={HOUR_TICKS}
                tickFormatter={hourLabel}
                stroke="var(--text-muted)"
                fontSize={11}
              />
              <YAxis
                type="number"
                domain={[yMin, yMax]}
                stroke="var(--text-muted)"
                fontSize={11}
                width={44}
                tickFormatter={(v: number) => `${formatNumber(v, { maximumFractionDigits: 0 })}°`}
              />
              <ChartTooltip content={<SunTooltip />} />
              <ReferenceArea
                y1={sunWindow.min}
                y2={sunWindow.max}
                fill="var(--text-muted)"
                fillOpacity={0.12}
                stroke="var(--text-muted)"
                strokeDasharray="2 3"
                label={{ value: `Sun window ${formatNumber(sunWindow.min)}–${formatNumber(sunWindow.max)}°`, position: "insideTopLeft", fontSize: 10, fill: "var(--text-muted)" }}
              />
              {windowBand ? (
                <ReferenceArea
                  x1={windowBand.x1}
                  x2={windowBand.x2}
                  fill="var(--accent)"
                  fillOpacity={0.15}
                  stroke="var(--accent)"
                  label={{ value: "Planned window", position: "insideTop", fontSize: 10, fill: "var(--text)" }}
                />
              ) : null}
              <ReferenceLine y={0} stroke="var(--text-muted)" label={{ value: "Horizon", position: "insideBottomLeft", fontSize: 10, fill: "var(--text-muted)" }} />
              <ReferenceLine
                y={-6}
                stroke="var(--text-muted)"
                strokeDasharray="6 4"
                label={{ value: "Civil twilight −6°", position: "insideBottomLeft", fontSize: 10, fill: "var(--text-muted)" }}
              />
              <Line type="monotone" dataKey="elevation" name="Sun elevation" stroke="var(--accent)" strokeWidth={2} dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <ul className="flex flex-wrap gap-3 text-xs text-fg-muted" aria-label="Chart legend">
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-0 w-5 border-t-2 border-accent" />
          Sun elevation (with refraction)
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-0 w-5 border-t-2 border-dashed border-fg-muted" />
          Civil twilight (−6°)
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-3 w-5 rounded-sm border border-dashed border-fg-muted bg-fg-muted/15" />
          Photogrammetry sun window
        </li>
        {windowBand ? (
          <li className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-3 w-5 rounded-sm border border-accent bg-accent/15" />
            Planned window
          </li>
        ) : null}
      </ul>
    </figure>
  );
}
