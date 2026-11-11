"use client";

/**
 * Radio path profile (Recharts): terrain, obstacle tops, the radio line of sight (lowered by the
 * Earth bulge), the lower edge of the first Fresnel zone and the stretches below the required
 * clearance, with a table alternative. Heights are above sea level, in the user's units.
 */

import * as React from "react";
import { Table2 } from "lucide-react";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";
import { formatHeight, formatLength, formatNumber, formatPercent } from "@/lib/format";
import type { Units } from "@/lib/store";
import type { LosRadioPath } from "@/lib/types";
import { Button } from "@/components/ui/button";

const FT_PER_M = 1 / 0.3048;
const M_PER_MI = 1609.344;

interface ChartRow {
  x: number;
  terrain: number | null;
  obstacle: number | null;
  los: number;
  fresnel: number;
}

const SERIES = [
  { key: "terrain", label: "Terrain (AMSL)", color: "var(--text-muted)", style: "area" },
  { key: "obstacle", label: "Obstacle top", color: "var(--danger)", style: "marker" },
  { key: "los", label: "Radio line of sight", color: "var(--accent)", style: "solid" },
  { key: "fresnel", label: "First Fresnel zone, lower edge", color: "var(--warn)", style: "dashed" },
] as const;

/** Obstacle marker: a triangle (shape, not only colour); nothing where the sample has no obstacle. */
function ObstacleDot(props: { cx?: number; cy?: number; value?: number | null; index?: number }) {
  const { cx, cy, value, index } = props;
  if (value === null || value === undefined || cx === undefined || cy === undefined || !Number.isFinite(cy)) return <g key={`o-${index}`} />;
  return <path key={`o-${index}`} d={`M${cx},${cy - 6} L${cx + 5},${cy + 3} L${cx - 5},${cy + 3} Z`} fill="var(--danger)" stroke="var(--bg)" strokeWidth={1} />;
}

export function RadioProfileChart({ path, thresholdPct, units, height = 240 }: { path: LosRadioPath; thresholdPct: number; units: Units; height?: number }) {
  const [asTable, setAsTable] = React.useState(false);
  const captionId = React.useId();
  const imperial = units === "imperial";
  const xUnit = imperial ? "mi" : "km";
  const yUnit = imperial ? "ft" : "m";
  const title = `Path profile: ${path.from_name} → ${path.to_name}`;

  const data = React.useMemo<ChartRow[]>(() => {
    const y = (m: number | null) => (m === null ? null : imperial ? m * FT_PER_M : m);
    return path.profile.map((s) => ({
      x: imperial ? s.d_m / M_PER_MI : s.d_m / 1000,
      terrain: y(s.terrain_m),
      obstacle: y(s.obstacle_m),
      los: y(s.los_m) as number,
      fresnel: y(s.fresnel_lower_m) as number,
    }));
  }, [path.profile, imperial]);
  const toX = (m: number) => (imperial ? m / M_PER_MI : m / 1000);

  if (path.profile.length < 2) return null;

  return (
    <figure className="space-y-2" aria-label={title}>
      <div className="flex items-center justify-between gap-2">
        <figcaption className="text-xs font-medium uppercase tracking-wide text-fg-muted">{title}</figcaption>
        <Button type="button" variant="ghost" size="sm" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          <Table2 aria-hidden /> {asTable ? "Show chart" : "Show table"}
        </Button>
      </div>
      {asTable ? (
        // Scrolls: focusable so keyboard users can scroll it (WCAG 2.1.1), named by the caption.
        <div role="region" aria-labelledby={captionId} tabIndex={0} className="max-h-64 overflow-auto rounded border border-border">
          <table className="w-full text-xs">
            <caption id={captionId} className="sr-only">{`${title}: heights above sea level and clearance of the first Fresnel zone along the path`}</caption>
            <thead className="sticky top-0 bg-surface">
              <tr>
                {["Distance", "Terrain", "Obstacle", "Line of sight", "Fresnel lower edge", "Clearance"].map((h) => (
                  <th key={h} scope="col" className="px-2 py-1 text-right font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {path.profile.map((s, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="num px-2 py-0.5 text-right">{formatLength(s.d_m, units)}</td>
                  <td className="num px-2 py-0.5 text-right">{formatHeight(s.terrain_m, units)}</td>
                  <td className="num px-2 py-0.5 text-right">{formatHeight(s.obstacle_m, units)}</td>
                  <td className="num px-2 py-0.5 text-right">{formatHeight(s.los_m, units)}</td>
                  <td className="num px-2 py-0.5 text-right">{formatHeight(s.fresnel_lower_m, units)}</td>
                  <td className="num px-2 py-0.5 text-right">
                    {s.clearance_pct === null ? "—" : formatPercent(s.clearance_pct, { maximumFractionDigits: 1 })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ height }} className="w-full">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis
                dataKey="x"
                type="number"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v: number) => formatNumber(v, { maximumFractionDigits: 2 })}
                stroke="var(--text-muted)"
                fontSize={11}
                label={{ value: `Distance (${xUnit})`, position: "insideBottomRight", offset: -2, fontSize: 11, fill: "var(--text-muted)" }}
              />
              <YAxis
                stroke="var(--text-muted)"
                fontSize={11}
                width={56}
                domain={["auto", "auto"]}
                tickFormatter={(v: number) => formatNumber(v, { maximumFractionDigits: 0 })}
                label={{ value: `${yUnit} AMSL`, angle: -90, position: "insideLeft", fontSize: 11, fill: "var(--text-muted)" }}
              />
              <ChartTooltip
                contentStyle={{ background: "var(--elevated)", border: "1px solid var(--border)", fontSize: 12 }}
                labelFormatter={(v: number) => `${formatNumber(v, { maximumFractionDigits: 2 })} ${xUnit}`}
                formatter={(v: number, name: string) => [`${formatNumber(v, { maximumFractionDigits: 1 })} ${yUnit}`, name]}
              />
              {path.segments_below.map((s, i) => (
                <ReferenceArea
                  key={i}
                  x1={toX(s.start_m)}
                  x2={toX(Math.max(s.end_m, s.start_m + 1))}
                  fill={path.los_blocked ? "var(--danger)" : "var(--warn)"}
                  fillOpacity={0.15}
                  stroke={path.los_blocked ? "var(--danger)" : "var(--warn)"}
                  strokeDasharray="4 3"
                  label={
                    i === 0
                      ? {
                          value: `Below ${formatNumber(thresholdPct)}%`,
                          position: "insideTop",
                          fontSize: 10,
                          fill: path.los_blocked ? "var(--danger)" : "var(--warn)",
                        }
                      : undefined
                  }
                />
              ))}
              <Area type="linear" dataKey="terrain" name="Terrain (AMSL)" stroke="var(--text-muted)" fill="var(--text-muted)" fillOpacity={0.2} isAnimationActive={false} connectNulls />
              <Line type="linear" dataKey="los" name="Radio line of sight" stroke="var(--accent)" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line
                type="linear"
                dataKey="fresnel"
                name="Fresnel zone lower edge"
                stroke="var(--warn)"
                strokeWidth={2}
                strokeDasharray="6 4"
                dot={false}
                isAnimationActive={false}
              />
              <Line
                type="linear"
                dataKey="obstacle"
                name="Obstacle top"
                stroke="none"
                dot={(p: { cx?: number; cy?: number; value?: number | null; index?: number }) => <ObstacleDot {...p} />}
                activeDot={false}
                legendType="triangle"
                isAnimationActive={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <ul className="flex flex-wrap gap-3 text-xs text-fg-muted" aria-label="Chart legend">
        {SERIES.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            {s.style === "marker" ? (
              <span aria-hidden className="inline-block size-0 border-x-[5px] border-b-[9px] border-x-transparent" style={{ borderBottomColor: s.color }} />
            ) : s.style === "area" ? (
              <span aria-hidden className="inline-block h-2.5 w-5 rounded-sm opacity-60" style={{ backgroundColor: s.color }} />
            ) : (
              <span aria-hidden className="inline-block h-0 w-5 border-t-2" style={{ borderColor: s.color, borderStyle: s.style === "dashed" ? "dashed" : "solid" }} />
            )}
            {s.label}
          </li>
        ))}
        {path.segments_below.length > 0 ? (
          <li className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-2.5 w-5 rounded-sm border border-dashed border-warn bg-warn/20" />
            Clearance below {formatNumber(thresholdPct)}%
          </li>
        ) : null}
      </ul>
    </figure>
  );
}
