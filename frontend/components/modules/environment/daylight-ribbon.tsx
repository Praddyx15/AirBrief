"use client";

/**
 * 24 h daylight ribbon: night, civil twilight and day on the analysed date (local time), the
 * planned window outlined over it, and a strip under the window showing how the daylight profile
 * treats each part of it. Never colour alone: each sun state and each profile status has its own
 * pattern, the legend names them, the ribbon has a text description, and a table lists every
 * segment.
 */

import * as React from "react";
import { Table2 } from "lucide-react";
import { formatDuration } from "@/lib/format";
import { DAYLIGHT_SEGMENT_LABEL, DAYLIGHT_STATUS_LABEL } from "@/lib/labels";
import type { DaylightCheck, DaylightSegmentKind, DaylightSegmentStatus, TimeSegment } from "@/lib/types";
import { Button } from "@/components/ui/button";

/** Sun states: filled = sun up, striped = civil twilight, dotted = night (tokens only). */
export const KIND_STYLE: Readonly<Record<DaylightSegmentKind, React.CSSProperties>> = {
  day: { backgroundColor: "rgb(var(--accent-rgb) / 0.6)" },
  civil_twilight: {
    backgroundImage:
      "repeating-linear-gradient(135deg, rgb(var(--accent-rgb) / 0.6) 0 3px, rgb(var(--accent-rgb) / 0.12) 3px 7px)",
  },
  night: {
    backgroundColor: "var(--elevated)",
    backgroundImage: "radial-gradient(rgb(var(--text-muted-rgb) / 0.6) 1px, transparent 1.5px)",
    backgroundSize: "6px 6px",
  },
};

/** Profile status of a window part: plain = within, stripes = lighting required, cross-hatch = outside. */
export const STATUS_STYLE: Readonly<Record<DaylightSegmentStatus, React.CSSProperties>> = {
  allowed: { backgroundColor: "rgb(var(--text-muted-rgb) / 0.35)" },
  lighting: {
    backgroundImage: "repeating-linear-gradient(45deg, rgb(var(--warn-rgb) / 0.95) 0 3px, transparent 3px 6px)",
  },
  outside: {
    backgroundImage:
      "repeating-linear-gradient(45deg, rgb(var(--danger-rgb) / 0.95) 0 2px, transparent 2px 6px), repeating-linear-gradient(-45deg, rgb(var(--danger-rgb) / 0.95) 0 2px, transparent 2px 6px)",
  },
};

interface Piece {
  start: number;
  end: number;
  kind: DaylightSegmentKind;
  startLocal: string;
  endLocal: string;
}

const DAY_MINUTES = 1440;

function tickLabel(minute: number): string {
  const h = Math.round(minute / 60);
  if (h <= 24) return `${String(h).padStart(2, "0")}:00`;
  return `${String(h - 24).padStart(2, "0")}:00 +1 d`;
}

function span(segment: Pick<TimeSegment, "start_local" | "end_local">): string {
  return `${segment.start_local}–${segment.end_local}`;
}

export interface DaylightRibbonProps {
  daylight: DaylightCheck;
  /** e.g. "UTC+05:30" */
  offsetLabel: string;
  /** e.g. "21 Jun 2026" */
  dateLabel: string;
}

export function DaylightRibbon({ daylight, offsetLabel, dateLabel }: DaylightRibbonProps) {
  const [asTable, setAsTable] = React.useState(false);
  const titleId = React.useId();
  const captionId = React.useId();
  const planned = daylight.window;
  const domainEnd = Math.max(DAY_MINUTES, planned?.end_minute ?? 0);

  const pieces = React.useMemo<Piece[]>(() => {
    const out: Piece[] = daylight.segments.map((s) => ({
      start: s.start_minute,
      end: s.end_minute,
      kind: s.kind,
      startLocal: s.start_local,
      endLocal: s.end_local,
    }));
    // A window past midnight: its next-day part continues the ribbon.
    for (const s of daylight.window_segments) {
      if (s.end_minute <= DAY_MINUTES) continue;
      out.push({ start: Math.max(s.start_minute, DAY_MINUTES), end: s.end_minute, kind: s.kind, startLocal: s.start_local, endLocal: s.end_local });
    }
    return out;
  }, [daylight]);

  const pct = (minute: number) => `${(Math.min(Math.max(minute, 0), domainEnd) / domainEnd) * 100}%`;
  const width = (a: number, b: number) => `${((Math.min(b, domainEnd) - Math.max(a, 0)) / domainEnd) * 100}%`;
  const ticks: number[] = [];
  for (let m = 0; m <= domainEnd + 1e-6; m += 360) ticks.push(m);

  const description = [
    daylight.segments.map((s) => `${DAYLIGHT_SEGMENT_LABEL[s.kind]} ${span(s)}`).join(", "),
    planned ? `Planned window ${span(planned)}: ${daylight.statement}` : null,
  ]
    .filter(Boolean)
    .join(". ");

  const presentStatuses = new Set(daylight.window_segments.map((s) => s.status));

  return (
    <figure className="space-y-2" aria-labelledby={titleId}>
      <div className="flex items-center justify-between gap-2">
        <figcaption id={titleId} className="text-xs font-medium uppercase tracking-wide text-fg-muted">
          Daylight on {dateLabel} ({offsetLabel})
        </figcaption>
        <Button type="button" variant="ghost" size="sm" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          <Table2 aria-hidden /> {asTable ? "Show ribbon" : "Show table"}
        </Button>
      </div>

      {asTable ? (
        // Scrolls: focusable so keyboard users can scroll it (WCAG 2.1.1), named by the caption.
        <div role="region" aria-labelledby={captionId} tabIndex={0} className="max-h-64 overflow-auto rounded border border-border">
          <table className="w-full text-xs">
            <caption id={captionId} className="sr-only">
              Night, civil twilight and day on {dateLabel}, local time {offsetLabel}
            </caption>
            <thead className="sticky top-0 bg-surface">
              <tr>
                <th scope="col" className="px-2 py-1 text-left font-medium">Sun</th>
                <th scope="col" className="px-2 py-1 text-left font-medium">Profile</th>
                <th scope="col" className="px-2 py-1 text-right font-medium">Start</th>
                <th scope="col" className="px-2 py-1 text-right font-medium">End</th>
                <th scope="col" className="px-2 py-1 text-right font-medium">Duration</th>
              </tr>
            </thead>
            <tbody>
              {daylight.segments.map((s) => (
                <tr key={`${s.start}-${s.kind}`} className="border-t border-border">
                  <th scope="row" className="px-2 py-0.5 text-left font-normal">
                    {DAYLIGHT_SEGMENT_LABEL[s.kind]}
                  </th>
                  <td className="px-2 py-0.5">{DAYLIGHT_STATUS_LABEL[s.status]}</td>
                  <td className="num px-2 py-0.5 text-right">{s.start_local}</td>
                  <td className="num px-2 py-0.5 text-right">{s.end_local}</td>
                  <td className="num px-2 py-0.5 text-right">{formatDuration(s.minutes * 60)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="space-y-1">
          <div className="relative h-8 w-full overflow-hidden rounded border border-border" role="img" aria-label={description}>
            {pieces.map((p) => (
              <div
                key={`${p.start}-${p.kind}`}
                className="absolute inset-y-0"
                style={{ left: pct(p.start), width: width(p.start, p.end), ...KIND_STYLE[p.kind] }}
              />
            ))}
            {planned ? (
              <div
                aria-hidden
                className="absolute inset-y-0 rounded-sm border-2 border-fg"
                style={{ left: pct(planned.start_minute), width: width(planned.start_minute, planned.end_minute) }}
              />
            ) : null}
          </div>
          {planned ? (
            <div className="relative h-2.5 w-full" aria-hidden>
              {daylight.window_segments.map((s) => (
                <div
                  key={`${s.start}-${s.status}`}
                  className="absolute inset-y-0 rounded-sm border border-border"
                  style={{ left: pct(s.start_minute), width: width(s.start_minute, s.end_minute), ...STATUS_STYLE[s.status] }}
                />
              ))}
            </div>
          ) : null}
          <div className="relative h-4 w-full text-[11px] text-fg-muted" aria-hidden>
            {ticks.map((m, i) => (
              <span
                key={m}
                className="num absolute top-0"
                style={{
                  left: pct(m),
                  transform: i === 0 ? "none" : m >= domainEnd - 1e-6 ? "translateX(-100%)" : "translateX(-50%)",
                }}
              >
                {tickLabel(m)}
              </span>
            ))}
          </div>
        </div>
      )}

      <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-muted" aria-label="Ribbon legend">
        {(["day", "civil_twilight", "night"] as const).map((k) => (
          <li key={k} className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-3 w-5 rounded-sm border border-border" style={KIND_STYLE[k]} />
            {DAYLIGHT_SEGMENT_LABEL[k]}
          </li>
        ))}
        {planned ? (
          <li className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-3 w-5 rounded-sm border-2 border-fg" />
            Planned window {span(planned)}
          </li>
        ) : null}
        {(["allowed", "lighting", "outside"] as const)
          .filter((s) => presentStatuses.has(s))
          .map((s) => (
            <li key={s} className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-3 w-5 rounded-sm border border-border" style={STATUS_STYLE[s]} />
              {DAYLIGHT_STATUS_LABEL[s]}
            </li>
          ))}
      </ul>
    </figure>
  );
}
