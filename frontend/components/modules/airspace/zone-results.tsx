"use client";

/**
 * Zone details in the airspace results: the aerodrome bands the flight reaches with their
 * height margins, when each zone applies, and the zones that did not apply to this flight.
 */

import * as React from "react";
import { ZONE_LABEL, toneForZone } from "@/lib/colors";
import { formatHeight, plural } from "@/lib/format";
import { DISTANCE_FROM_LABEL, ZONE_PROFILE_LABEL } from "@/lib/labels";
import type { useUnits } from "@/lib/store";
import type { AirspaceResult, BandEvidence, ZoneHit } from "@/lib/types";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { ModuleSection, ToneBadge } from "../shared";

type Units = ReturnType<typeof useUnits>;

function utc(value: string | null | undefined): string {
  if (!value) return "…";
  const t = new Date(value);
  return Number.isNaN(t.getTime()) ? "…" : `${t.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

function validityText(z: Pick<ZoneHit, "valid_from" | "valid_to">): string {
  if (!z.valid_from && !z.valid_to) return "Permanent";
  return `${utc(z.valid_from)} to ${utc(z.valid_to)}`;
}

/** Active and Validity columns of the zones table. */
export function zoneActivityColumns(): DataTableColumn<ZoneHit>[] {
  return [
    {
      id: "active",
      header: "Active",
      cell: (z) =>
        z.active === true ? (
          <ToneBadge tone="warn" label="In window" />
        ) : z.active === false ? (
          <ToneBadge tone="neutral" label="Not in window" />
        ) : (
          <span className="text-fg-muted">No window</span>
        ),
      sortValue: (z) => (z.active === true ? 0 : z.active === false ? 2 : 1),
    },
    { id: "validity", header: "Validity", cell: (z) => validityText(z), sortValue: (z) => z.valid_from ?? "" },
  ];
}

function bandTone(b: BandEvidence): "danger" | "warn" | "ok" {
  if (b.zone === "red" || (b.agl_margin_m !== null && b.agl_margin_m < 0)) return "danger";
  return b.permission_required || b.basis === "verify_official_map" ? "warn" : toneForZone(b.zone) === "ok" ? "ok" : "warn";
}

function bandText(b: BandEvidence, units: Units): { title: string; detail: string } {
  const title = b.ident ? `${b.ident} ${b.band.split(":")[0]}` : "Elsewhere";
  const zone = ZONE_LABEL[b.zone].toLowerCase();
  if (b.basis === "verify_official_map") return { title, detail: "verify on Digital Sky (permission)" };
  if (b.ceiling_agl_m === null) return { title, detail: b.permission_required ? `${zone}, permission` : zone };
  const ceiling = formatHeight(b.ceiling_agl_m, units);
  if (b.agl_margin_m === null || b.worst_agl_m === null) return { title, detail: `${zone} ≤ ${ceiling}` };
  const flown = formatHeight(b.worst_agl_m, units);
  if (b.agl_margin_m < 0) {
    const over = formatHeight(-b.agl_margin_m, units);
    // Above the ceiling the band turned into another zone (e.g. green to 60 m, yellow above).
    const head = b.permission_required ? `ceiling ${ceiling}, ${zone} above` : `${zone} ≤ ${ceiling}`;
    return { title, detail: `${head}; flown ${flown}, ${over} over` };
  }
  return { title, detail: `${zone} ≤ ${ceiling}; flown ${flown}, ${formatHeight(b.agl_margin_m, units)} below` };
}

const SAMPLES_TEXT: Record<BandEvidence["sample_kind"], string> = {
  profile: "route samples",
  grid: "grid cells",
  vertices: "points",
  track: "track points",
};

/** One chip per aerodrome band the flight reaches, with the worst height margin in it (airspace and flight audit). */
export function BandChips({ result, units }: { result: Pick<AirspaceResult, "band_evidence" | "zone_profile">; units: Units }) {
  const bands = result.band_evidence ?? [];
  if (bands.length === 0) return null;
  const measured = bands.find((b) => b.distance_from)?.distance_from;
  const kind = bands[0]?.sample_kind ?? "vertices";
  const what = SAMPLES_TEXT[kind];
  return (
    <ModuleSection title="Aerodrome bands">
      <ul className="flex flex-wrap gap-2" aria-label="Aerodrome bands reached by the flight">
        {bands.map((b, i) => {
          const { title, detail } = bandText(b, units);
          const tone = bandTone(b);
          return (
            <li
              key={`${b.ident ?? "elsewhere"}-${i}`}
              className="flex items-center gap-2 rounded-full border border-border bg-elevated py-1 pl-1 pr-3 text-xs"
            >
              <ToneBadge tone={tone} label={tone === "danger" ? "Over" : tone === "warn" ? "Check" : "Within"} />
              <span>
                <span className="font-medium text-fg">{title}</span>: <span className="text-fg-muted">{detail}</span>
              </span>
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-fg-muted">
        {kind === "track" ? "Highest height above ground flown" : "Highest planned height above ground"} among the {what} in each band
        {measured ? `; bands measured from ${DISTANCE_FROM_LABEL[measured]}` : ""}. Rules:{" "}
        {result.zone_profile ? ZONE_PROFILE_LABEL[result.zone_profile] : ZONE_PROFILE_LABEL.conservative_illustrative}.
      </p>
    </ModuleSection>
  );
}

/** User, dataset and pack zones touched but not applying: out of the planned window or clear of the flight's heights. */
export function InactiveZones({ result }: { result: AirspaceResult }) {
  const zones = result.inactive_zones ?? [];
  const columns = React.useMemo<DataTableColumn<ZoneHit>[]>(
    () => [
      { id: "name", header: "Zone", rowHeader: true, cell: (z) => z.name, sortValue: (z) => z.name },
      { id: "zone", header: "Class", cell: (z) => <ToneBadge tone={toneForZone(z.zone)} label={ZONE_LABEL[z.zone]} />, sortValue: (z) => z.zone },
      { id: "why", header: "Why not", cell: (z) => z.inactive_reason ?? "—" },
    ],
    [],
  );
  if (zones.length === 0) return null;
  return (
    <ModuleSection title={`Zones that do not apply (${zones.length})`}>
      <DataTable
        data={zones}
        columns={columns}
        getRowId={(z, i) => `inactive-${z.name}-${i}`}
        caption={`${plural(zones.length, "zone")} touched by the flight but outside the planned window or its heights`}
        dense
      />
    </ModuleSection>
  );
}
