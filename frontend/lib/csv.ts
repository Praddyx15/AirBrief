/** CSV helpers shared by the measure panel, the dataset export (demo mode) and the report page. */

import type { MeasurementItem } from "./types";

/** One CSV cell: quoted when needed, and never a spreadsheet formula. */
export function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  let s = String(value);
  // Text from uploads (layer, name, a custom .prj's name) must not run as a spreadsheet formula;
  // same rule as the API export. Numbers stay numbers.
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** CSV of measurement items (SI units), used when the API export is not available (demo). */
export function measurementsToCsv(items: readonly MeasurementItem[]): string {
  const header = [
    "feature_index",
    "layer",
    "name",
    "geometry_type",
    "status",
    "reason",
    "area_m2",
    "perimeter_m",
    "length_m",
    "length_3d_m",
    "geodesic_area_m2",
    "geodesic_length_m",
    "deviation_pct",
    "method",
    "area_crs",
    "length_crs",
    "utm_zone",
    "repaired",
  ];
  const rows = items.map((it) => {
    const m = it.measurement;
    return [
      it.feature_index,
      it.layer,
      it.name,
      it.geometry_type,
      m.status,
      m.reason,
      m.area_m2,
      m.perimeter_m,
      m.length_m,
      m.length_3d_m,
      m.geodesic_area_m2,
      m.geodesic_length_m,
      m.deviation_pct,
      m.method,
      m.area_crs,
      m.length_crs,
      m.utm_zone,
      m.repaired,
    ]
      .map(csvCell)
      .join(",");
  });
  return [header.join(","), ...rows].join("\r\n");
}
