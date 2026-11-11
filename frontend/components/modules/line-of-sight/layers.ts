/**
 * Map layers of the line-of-sight module: visible area tinted (stronger where two or more
 * observers see it), hidden area hatched (colour + pattern), the largest hidden patch outlined,
 * observers as labelled points, radio paths coloured and dashed by Fresnel clearance, the route
 * split into seen / hidden stretches, and the non-regulatory VLOS guidance ring.
 */

import type { PickingInfo } from "@deck.gl/core";
import { toneRgba } from "@/lib/colors";
import { formatArea, formatHeight, formatLength, formatNumber, formatPercent } from "@/lib/format";
import type { LegendSpec, ModuleLayerContext, ModuleLayerInfo, ModuleLayerOutput } from "@/lib/modules";
import { useUi } from "@/lib/store";
import type { AnalysisInfo, FeatureCollection, Geometry, LineOfSightResult, LosObserver, LosRadioPath } from "@/lib/types";
import { derived, geoJsonLayer, hatchZoneLayer, mapPalette, pathLayer, pointLayer, textLabelLayer } from "@/components/map/layers/common";
import { currentUnits, resultLayer } from "../shared";
import { ROLE_LABEL, formatFrequency, pathKey, radioBand, radioBandLabel } from "./model";

export const LOS_LAYERS: readonly ModuleLayerInfo[] = [
  { id: "line-of-sight:visible", label: "Visible area", resultLayer: "visible" },
  { id: "line-of-sight:hidden", label: "Hidden area", resultLayer: "hidden" },
  { id: "line-of-sight:hidden-patch", label: "Largest hidden patch", resultLayer: "hidden_patch" },
  { id: "line-of-sight:input", label: "Operation area or route", resultLayer: "input" },
  { id: "line-of-sight:route", label: "Route visibility", resultLayer: "route" },
  { id: "line-of-sight:radio", label: "Radio paths", resultLayer: "radio_paths" },
  { id: "line-of-sight:vlos", label: "VLOS guidance ring", resultLayer: "vlos_guidance" },
  { id: "line-of-sight:observers", label: "Pilot, observers and ground station", resultLayer: "observers" },
];

type AnyFc = FeatureCollection<Geometry | null, Record<string, unknown>>;

interface VisibleProps {
  observers?: number;
  area_m2?: number;
}
interface RouteProps {
  visible?: boolean;
  start_m?: number;
  end_m?: number;
  length_m?: number;
}
interface RingProps {
  observer?: string;
  radius_m?: number;
  guidance_m?: number;
}

/** A radio path as drawn: it carries its run's id and index, so a click selects that path. */
export interface PathDatum {
  analysisId: string;
  index: number;
  path: LosRadioPath;
  threshold: number;
}

function northMost(geometry: Geometry | null | undefined): [number, number] | null {
  if (!geometry || geometry.type !== "Polygon") return null;
  let best: [number, number] | null = null;
  for (const p of geometry.coordinates[0] ?? []) {
    if (!best || (p[1] as number) > best[1]) best = [p[0] as number, p[1] as number];
  }
  return best;
}

export function buildLineOfSightLayers(analysis: AnalysisInfo, ctx: ModuleLayerContext): ModuleLayerOutput {
  const result = analysis.result as LineOfSightResult | null;
  if (!result) return { layers: [], legends: [] };
  const palette = mapPalette();
  const highlight = ctx.resultHighlight;
  const radio = result.radio;
  const threshold = radio?.clearance_threshold_pct ?? 60;

  const route = resultLayer(result.layers, "route") as AnyFc;
  const routeSeen = derived(route, "line-of-sight:route-seen", () => route.features.filter((f) => (f.properties as RouteProps | null)?.visible === true));
  const routeHidden = derived(route, "line-of-sight:route-hidden", () => route.features.filter((f) => (f.properties as RouteProps | null)?.visible !== true));
  const rings = resultLayer(result.layers, "vlos_guidance") as AnyFc;
  const ringLabels = derived(rings, "line-of-sight:ring-labels", () =>
    rings.features.flatMap((f) => {
      const top = northMost(f.geometry);
      const props = (f.properties ?? {}) as RingProps;
      return top ? [{ position: top, guidance: Number(props.guidance_m ?? result.vlos_guidance_m) }] : [];
    }),
  );
  const paths = radio
    ? derived(radio.paths, `line-of-sight:paths:${analysis.id}`, (): PathDatum[] => radio.paths.map((path, index) => ({ analysisId: analysis.id, index, path, threshold })))
    : [];
  const clearPaths = derived(paths, "line-of-sight:paths-clear", () => paths.filter((d) => radioBand(d.path) === "clear"));
  const flaggedPaths = derived(paths, "line-of-sight:paths-flagged", () => paths.filter((d) => radioBand(d.path) !== "clear"));
  const isHighlighted = (d: PathDatum) => highlight === pathKey(d.analysisId, d.index);
  const pathColor = (d: PathDatum) => {
    const band = radioBand(d.path);
    const tone = band === "clear" ? "ok" : band === "below" ? "warn" : band === "blocked" ? "danger" : "neutral";
    return toneRgba(tone, isHighlighted(d) || highlight === null ? 255 : 170);
  };

  const layers = [
    geoJsonLayer<VisibleProps>({
      id: "line-of-sight:visible",
      data: resultLayer(result.layers, "visible") as unknown as FeatureCollection<Geometry | null, VisibleProps>,
      fillColor: (p) => toneRgba("ok", (p.observers ?? 1) >= 2 ? 110 : 55),
      lineColor: toneRgba("ok", 160),
      lineWidth: 0.5,
      updateTriggers: [ctx.theme],
    }),
    ...hatchZoneLayer<VisibleProps>({
      id: "line-of-sight:hidden",
      data: resultLayer(result.layers, "hidden") as unknown as FeatureCollection<Geometry | null, VisibleProps>,
      getZone: () => "yellow",
      hatch: ctx.hatch,
      fillAlpha: 35,
      hatchAlpha: 190,
      lineWidth: 1,
      updateTriggers: [ctx.theme, ctx.hatch],
    }),
    geoJsonLayer({
      id: "line-of-sight:hidden-patch",
      data: resultLayer(result.layers, "hidden_patch") as AnyFc,
      filled: false,
      lineColor: palette.warn,
      lineWidth: 2.5,
      dashed: true,
      pickable: true,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer({
      id: "line-of-sight:input",
      data: resultLayer(result.layers, "input") as AnyFc,
      filled: false,
      lineColor: palette.accent,
      lineWidth: 2,
      pointRadius: 5,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer<RouteProps>({
      id: "line-of-sight:route",
      data: routeSeen as unknown as FeatureCollection<Geometry | null, RouteProps>["features"],
      filled: false,
      lineColor: palette.ok,
      lineWidth: 4,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer<RouteProps>({
      id: "line-of-sight:route/hidden",
      data: routeHidden as unknown as FeatureCollection<Geometry | null, RouteProps>["features"],
      filled: false,
      lineColor: palette.danger,
      lineWidth: 4,
      dashed: true,
      updateTriggers: [ctx.theme],
    }),
    geoJsonLayer<RingProps>({
      id: "line-of-sight:vlos",
      data: rings as unknown as FeatureCollection<Geometry | null, RingProps>,
      filled: false,
      lineColor: [palette.text[0], palette.text[1], palette.text[2], 200],
      lineWidth: 1.5,
      dashed: true,
      dashArray: [6, 4],
      updateTriggers: [ctx.theme],
    }),
    textLabelLayer<{ position: [number, number]; guidance: number }>({
      id: "line-of-sight:vlos/labels",
      data: ringLabels,
      getPosition: (d) => d.position,
      getText: (d) => `VLOS guidance ${formatLength(d.guidance, ctx.units)} (non-regulatory)`,
      size: 11,
      baseline: "bottom",
      pixelOffset: [0, -4],
      updateTriggers: [ctx.units, ctx.theme],
    }),
    pathLayer<PathDatum>({
      id: "line-of-sight:radio",
      data: clearPaths,
      getPath: (d) => [
        [d.path.from_lon, d.path.from_lat],
        [d.path.to_lon, d.path.to_lat],
      ],
      color: pathColor,
      width: (d) => (isHighlighted(d) ? 5 : 2.5),
      updateTriggers: [highlight, ctx.theme],
    }),
    pathLayer<PathDatum>({
      id: "line-of-sight:radio/flagged",
      data: flaggedPaths,
      getPath: (d) => [
        [d.path.from_lon, d.path.from_lat],
        [d.path.to_lon, d.path.to_lat],
      ],
      color: pathColor,
      width: (d) => (isHighlighted(d) ? 5 : 2.5),
      dashed: true,
      dashArray: [5, 3],
      updateTriggers: [highlight, ctx.theme],
    }),
    pointLayer<LosObserver>({
      id: "line-of-sight:observers",
      data: result.observers,
      getPosition: (o) => [o.lon, o.lat],
      radius: 7,
      fillColor: (o) => (o.role === "gcs" ? palette.warn : palette.accent),
      lineColor: palette.text,
      lineWidth: (o) => (o.assumed ? 1 : 2),
      updateTriggers: [ctx.theme],
    }),
    textLabelLayer<LosObserver>({
      id: "line-of-sight:observers/labels",
      data: result.observers,
      getPosition: (o) => [o.lon, o.lat],
      getText: (o) => `${o.name}${o.assumed ? " (assumed)" : ""}${o.visible_pct === null ? "" : ` · ${formatPercent(o.visible_pct, { maximumFractionDigits: 0 })}`}`,
      pixelOffset: [0, -18],
      size: 11,
      updateTriggers: [ctx.theme],
    }),
  ];

  const legends: LegendSpec[] = [
    {
      id: "line-of-sight:visible",
      title: `Visibility at ${formatHeight(result.target_height_agl_m, ctx.units)} above ground`,
      kind: "categorical",
      layerId: "line-of-sight:visible",
      items: [
        { label: "Seen by one pilot or observer", color: "var(--ok)" },
        { label: "Seen by two or more", color: "var(--ok)", detail: "stronger tint" },
      ],
      note: `Geometric evidence from a ${result.grid.dem_resolution_m ? `~${formatLength(result.grid.dem_resolution_m, ctx.units)} ` : ""}surface model, not a VLOS approval.`,
    },
    {
      id: "line-of-sight:hidden",
      title: "Hidden",
      kind: "categorical",
      layerId: "line-of-sight:hidden",
      items: [{ label: "Hidden from every pilot and observer (hatched)", color: "var(--warn)", zone: "yellow" }],
      note: "Tint and hatch cover the whole analysed grid; the percentages count the operation area only.",
    },
    {
      id: "line-of-sight:observers",
      title: "Viewpoints",
      kind: "categorical",
      layerId: "line-of-sight:observers",
      items: [
        { label: "Pilot or visual observer", color: "var(--accent)", shape: "point" },
        { label: "Ground station (radio)", color: "var(--warn)", shape: "point" },
        { label: "VLOS guidance ring (non-regulatory)", color: "var(--text)", shape: "ring" },
      ],
    },
  ];
  if (radio && radio.paths.length > 0) {
    legends.push({
      id: "line-of-sight:radio",
      title: `Radio paths, ${formatFrequency(radio.frequency_mhz)}`,
      kind: "categorical",
      layerId: "line-of-sight:radio",
      items: [
        { label: radioBandLabel("clear", threshold), color: "var(--ok)", shape: "line" },
        { label: `Fresnel clearance below ${formatNumber(threshold)}%`, color: "var(--warn)", shape: "dashed-line" },
        { label: "Line of sight cut", color: "var(--danger)", shape: "dashed-line" },
      ],
      note: "Geometry only — not a link budget or coverage prediction.",
    });
  }
  if (route.features.length > 0) {
    legends.push({
      id: "line-of-sight:route",
      title: "Route",
      kind: "categorical",
      layerId: "line-of-sight:route",
      items: [
        { label: "Aircraft seen", color: "var(--ok)", shape: "line" },
        { label: "Aircraft hidden", color: "var(--danger)", shape: "dashed-line" },
      ],
    });
  }
  return { layers, legends };
}

export function describeLineOfSightPick(info: PickingInfo): string | null {
  const id = info.layer?.id ?? "";
  const obj = info.object as Record<string, unknown> | null;
  if (!obj) return null;
  const units = currentUnits();
  if (id.startsWith("line-of-sight:radio")) {
    const d = obj as unknown as PathDatum;
    if (!d.path) return null;
    const clearance = d.path.worst_clearance_pct === null ? "no terrain data" : `worst clearance ${formatPercent(d.path.worst_clearance_pct, { maximumFractionDigits: 0 })}`;
    return `${d.path.from_name} → ${d.path.to_name}: ${formatLength(d.path.distance_m, units)}, ${clearance}, FSPL ${formatNumber(d.path.fspl_db, { maximumFractionDigits: 1 })} dB`;
  }
  if (id.startsWith("line-of-sight:observers")) {
    const o = obj as unknown as LosObserver;
    const seen = o.visible_pct === null ? "" : ` · sees ${formatPercent(o.visible_pct, { maximumFractionDigits: 1 })} of the area`;
    return `${o.name} (${ROLE_LABEL[o.role]}${o.assumed ? ", assumed" : ""})${seen}`;
  }
  const props = (obj.properties ?? {}) as Record<string, unknown>;
  if (id === "line-of-sight:visible") {
    const n = Number(props.observers ?? 1);
    return `Seen by ${n} pilot${n === 1 ? "" : "s"} or observer${n === 1 ? "" : "s"}`;
  }
  if (id.startsWith("line-of-sight:hidden-patch")) return `Largest hidden patch: ${formatArea(Number(props.area_m2), units)}`;
  if (id.startsWith("line-of-sight:hidden")) return "Hidden from every pilot and observer";
  if (id.startsWith("line-of-sight:route")) {
    const p = props as RouteProps;
    return `${p.visible ? "Seen" : "Hidden"}: ${formatLength(p.start_m ?? null, units)} to ${formatLength(p.end_m ?? null, units)} along the route`;
  }
  if (id.startsWith("line-of-sight:vlos")) return `VLOS guidance ${formatLength(Number(props.guidance_m), units)} from ${String(props.observer ?? "the pilot")} (non-regulatory)`;
  return null;
}

/** A click on a radio path selects it (the results table and the profile chart follow). */
export function onLineOfSightPick(info: PickingInfo): boolean {
  if (!(info.layer?.id ?? "").startsWith("line-of-sight:radio") || !info.object) return false;
  const d = info.object as PathDatum;
  if (typeof d.analysisId !== "string") return false;
  useUi.getState().setResultHighlight(pathKey(d.analysisId, d.index));
  return true;
}
