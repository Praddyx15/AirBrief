/**
 * Context layers (`GET /api/context/`): reference data from the data pack shown before, or
 * under, any analysis — aerodromes, their indicative zone rings, the pack's zones, obstacles,
 * water and the pack's coverage. Drawn below the dataset and module layers, never pickable for
 * selection (hover shows a tooltip only), each with a legend and a table ("Layers as table").
 *
 * Layer ids are `context:<key>` (sub-layers `context:<key>/...`). Zones use the same hatch and
 * colours as the airspace module (`hatchZoneLayer`), so a ring looks the same before and after a
 * check.
 */

import type { Layer, PickingInfo } from "@deck.gl/core";
import { CATEGORICAL, ZONE_LABEL, categorical, type HatchAtlas, type RGBA } from "@/lib/colors";
import { formatHeight } from "@/lib/format";
import { ZONE_PROFILE_LABEL } from "@/lib/labels";
import type { LegendSpec } from "@/lib/modules";
import type { ContextLayerData } from "@/lib/queries";
import type { Units } from "@/lib/store";
import type { ContextLayerKey, FeatureCollection, Geometry, ZoneKind } from "@/lib/types";
import { derived, geoJsonLayer, hatchZoneLayer, mapPalette, pointLayer, textLabelLayer, zoneLegend } from "./common";

export const CONTEXT_LAYER_PREFIX = "context:";

export function contextLayerId(key: ContextLayerKey): string {
  return `${CONTEXT_LAYER_PREFIX}${key}`;
}

export function isContextLayerId(id: string): boolean {
  return id.startsWith(CONTEXT_LAYER_PREFIX);
}

export const CONTEXT_LAYER_LABEL: Record<ContextLayerKey, string> = {
  aerodromes: "Aerodromes",
  rings: "Aerodrome zone rings",
  zones: "Data-pack zones",
  obstacles: "Obstacles",
  water: "Water",
  coverage: "Data-pack coverage",
};

/** One line under each switch in the Layers panel. */
export const CONTEXT_LAYER_HINT: Record<ContextLayerKey, string> = {
  aerodromes: "Reference points with ICAO code",
  rings: "Indicative bands of the open panel's Rules (DGCA 2021 by default)",
  zones: "Zones shipped with the data pack",
  obstacles: "Masts, towers and buildings with heights",
  water: "Lakes and rivers",
  coverage: "Where the data pack has data",
};

/** Bottom (first) to top. */
const DRAW_ORDER: readonly ContextLayerKey[] = ["coverage", "water", "zones", "rings", "obstacles", "aerodromes"];

const WATER_COLOR = CATEGORICAL[0] as string;

/** Zone classes in legend order. */
const ZONE_KINDS: readonly ZoneKind[] = ["green", "yellow", "red"];

const AERODROME_RADIUS: Record<string, number> = { large_airport: 8, medium_airport: 7, small_airport: 6 };

interface ZoneProps {
  zone?: ZoneKind;
  name?: string;
  label?: string;
  ring?: string;
  ceiling_agl_m?: number | null;
  permission_required?: boolean;
}

interface AerodromeProps {
  ident?: string;
  name?: string;
  type?: string;
  type_label?: string;
  iata?: string | null;
}

interface ObstacleProps {
  kind?: string;
  name?: string | null;
  height_m?: number | null;
}

interface PointDatum<P> {
  position: [number, number];
  properties: P;
}

function points<P>(fc: FeatureCollection, key: string): PointDatum<P>[] {
  return derived(fc, key, () =>
    fc.features.flatMap((f) =>
      f.geometry?.type === "Point"
        ? [{ position: [f.geometry.coordinates[0] as number, f.geometry.coordinates[1] as number] as [number, number], properties: (f.properties ?? {}) as P }]
        : [],
    ),
  );
}

export interface ContextLayerContext {
  hatch: HatchAtlas | null;
  theme: "dark" | "light";
  units: Units;
}

/** deck.gl layers and legends for the context layers that have data. */
export function contextLayers(data: readonly ContextLayerData[], ctx: ContextLayerContext): { layers: Layer[]; legends: LegendSpec[] } {
  const palette = mapPalette();
  const byKey = new Map(data.map((d) => [d.key, d]));
  const layers: Layer[] = [];
  const legends: LegendSpec[] = [];
  const triggers = [ctx.theme, ctx.hatch];

  for (const key of DRAW_ORDER) {
    const entry = byKey.get(key);
    const fc = entry?.collection;
    // An empty layer draws nothing and gets no legend (the Layers panel says "0 features").
    if (!entry || !fc || fc.features.length === 0) continue;
    const id = contextLayerId(key);
    const cut = entry.truncated ? `First ${fc.features.length.toLocaleString("en-US")} of ${entry.count.toLocaleString("en-US")} shown.` : undefined;
    switch (key) {
      case "coverage":
        layers.push(
          geoJsonLayer({
            id,
            data: fc,
            filled: false,
            lineColor: palette.muted,
            lineWidth: 1.5,
            dashed: true,
            dashArray: [6, 4],
            updateTriggers: triggers,
          }),
        );
        legends.push({
          id,
          title: CONTEXT_LAYER_LABEL.coverage,
          kind: "categorical",
          layerId: id,
          items: [{ label: entry.synthetic ? "Synthetic demo data" : "Data available", color: "var(--text-muted)", shape: "dashed-line" }],
          note: "Outside the box, checks have no aerodrome, terrain or population data.",
        });
        break;
      case "water":
        layers.push(
          geoJsonLayer({
            id,
            data: fc,
            fillColor: categorical(0, 50),
            lineColor: categorical(0, 160),
            lineWidth: 1,
            updateTriggers: triggers,
          }),
        );
        legends.push({ id, title: CONTEXT_LAYER_LABEL.water, kind: "categorical", layerId: id, items: [{ label: "Water", color: WATER_COLOR }], note: cut });
        break;
      case "zones":
      case "rings":
        layers.push(
          ...hatchZoneLayer<ZoneProps>({
            id,
            data: fc as unknown as FeatureCollection<Geometry | null, ZoneProps>,
            getZone: (p) => p.zone ?? null,
            hatch: ctx.hatch,
            fillAlpha: 25,
            hatchAlpha: 110,
            lineWidth: 1,
            updateTriggers: triggers,
          }),
        );
        legends.push(
          zoneLegend(id, {
            title: CONTEXT_LAYER_LABEL[key],
            layerId: id,
            zones: ZONE_KINDS.filter((z) => fc.features.some((f) => (f.properties as ZoneProps | null)?.zone === z)),
            note:
              key === "rings"
                ? `Indicative bands of the ${ZONE_PROFILE_LABEL[entry.profile ?? "dgca_2021"]} rules, not an official airspace map. ${cut ?? ""}`.trim()
                : cut,
          }),
        );
        break;
      case "obstacles": {
        const data = points<ObstacleProps>(fc, "context:obstacle-points");
        const maxH = Math.max(1, ...data.map((d) => Number(d.properties.height_m ?? 0)));
        layers.push(
          pointLayer<PointDatum<ObstacleProps>>({
            id,
            data,
            getPosition: (d) => d.position,
            radius: (d) => 3 + 6 * (Number(d.properties.height_m ?? 0) / maxH),
            fillColor: palette.warn,
            lineColor: palette.bg,
            lineWidth: 1,
            updateTriggers: triggers,
          }),
        );
        legends.push({
          id,
          title: CONTEXT_LAYER_LABEL.obstacles,
          kind: "categorical",
          layerId: id,
          items: [{ label: "Obstacle (size shows height)", color: "var(--warn)", shape: "point" }],
          note: cut,
        });
        break;
      }
      case "aerodromes": {
        const data = points<AerodromeProps>(fc, "context:aerodrome-points");
        const fill: RGBA = palette.accentText;
        layers.push(
          pointLayer<PointDatum<AerodromeProps>>({
            id,
            data,
            getPosition: (d) => d.position,
            radius: (d) => AERODROME_RADIUS[d.properties.type ?? ""] ?? 5,
            fillColor: fill,
            lineColor: palette.bg,
            lineWidth: 2,
            updateTriggers: triggers,
          }),
          textLabelLayer<PointDatum<AerodromeProps>>({
            id: `${id}/labels`,
            data,
            getPosition: (d) => d.position,
            getText: (d) => d.properties.ident ?? d.properties.name ?? "",
            pixelOffset: [0, -16],
            size: 11,
            updateTriggers: triggers,
          }),
        );
        legends.push({
          id,
          title: CONTEXT_LAYER_LABEL.aerodromes,
          kind: "categorical",
          layerId: id,
          items: [{ label: "Aerodrome reference point (ICAO code)", color: "var(--accent-text)", shape: "point" }],
          note: cut,
        });
        break;
      }
    }
  }
  return { layers, legends };
}

/** Hover text for a context object, or null when the pick is not a context layer. */
export function describeContextPick(info: PickingInfo, units: Units): string | null {
  const id = info.layer?.id ?? "";
  if (!isContextLayerId(id)) return null;
  const obj = info.object as { properties?: Record<string, unknown> } | null;
  const p = (obj?.properties ?? {}) as Record<string, unknown>;
  const key = id.slice(CONTEXT_LAYER_PREFIX.length).split("/")[0] as ContextLayerKey;
  switch (key) {
    case "aerodromes": {
      const a = p as AerodromeProps;
      return [a.ident, a.name, a.type_label, a.iata ? `IATA ${a.iata}` : null].filter(Boolean).join(" · ");
    }
    case "obstacles": {
      const o = p as ObstacleProps;
      const h = typeof o.height_m === "number" ? formatHeight(o.height_m, units) : "height unknown";
      return `${o.name ?? o.kind ?? "Obstacle"}: ${h} above ground`;
    }
    case "rings":
    case "zones": {
      const z = p as ZoneProps;
      const ceiling =
        z.permission_required || z.zone === "red"
          ? "permission required"
          : typeof z.ceiling_agl_m === "number"
            ? `up to ${formatHeight(z.ceiling_agl_m, units)} AGL`
            : null;
      return [z.name, z.label ?? z.ring, z.zone ? ZONE_LABEL[z.zone] : null, ceiling].filter(Boolean).join(" · ");
    }
    case "water":
      return `Water${typeof p.name === "string" && p.name ? `: ${p.name}` : ""}`;
    case "coverage":
      return `Data pack "${String(p.name ?? "")}"${p.synthetic ? " (synthetic)" : ""}`;
    default:
      return null;
  }
}
