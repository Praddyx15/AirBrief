"use client";

/**
 * The map part of the workspace. It subscribes to the fast-changing UI state (hover, keyboard
 * focus, playback time, hidden layers) itself so that only the map re-renders on every
 * pointer move or animation frame, not the whole workspace.
 */

import * as React from "react";
import { useTheme } from "next-themes";
import type { PickingInfo } from "@deck.gl/core";
import { Crosshair } from "lucide-react";
import { createHatchAtlas, type HatchAtlas } from "@/lib/colors";
import { formatArea, formatLength, plural } from "@/lib/format";
import { consumeMapPick, useMapPick, useMapPickKeyboard } from "@/lib/map-pick";
import type { LegendSpec, ModuleDefinition, ModuleLayerContext, ModuleLayerOutput } from "@/lib/modules";
import { usePrefersReducedMotion } from "@/lib/motion";
import { useSettings, useSettingsHydrated, useUi, useUnits } from "@/lib/store";
import type { AnalysisInfo, BBoxList, DatasetFeatureCollection } from "@/lib/types";
import { filterHiddenLayers, filterHiddenLegends, boundsCenter, geometryBounds, withHeights } from "@/components/map/layers/common";
import {
  DATASET_LAYER_ID,
  datasetFeatureLabel,
  datasetLayers,
  datasetLegend,
  pickedFeatureIndex,
} from "@/components/map/layers/dataset-layer";
import { Legend } from "@/components/map/legend";
import { MapControls } from "@/components/map/map-controls";
import { MapLoader, MapSkeleton, type MapFocusTarget, type MapViewHandle, type PointerModifiers } from "@/components/map/map-loader";
import { ShortcutsDialog } from "@/components/map/shortcuts-dialog";
import { contextLayers, describeContextPick, isContextLayerId } from "@/components/map/layers/context-layer";
import { planLayers } from "@/components/map/layers/plan-layer";
import { usePlan } from "@/lib/plan-store";
import { OperationInput } from "./operation-input";
import { useContextData, usePlanMapInteractions } from "./plan-map";

const EMPTY_OUTPUT: ModuleLayerOutput = { layers: [], legends: [] };

/** Layers-panel id of one source layer (KML folder, GeoPackage table…) of the dataset. */
export function sourceLayerToggleId(layer: string): string {
  return `dataset:layer:${layer}`;
}

export interface WorkspaceMapProps {
  mapRef: React.RefObject<MapViewHandle | null>;
  features: DatasetFeatureCollection | null;
  selected: readonly number[];
  /** the URL analysis (any module); its owner module draws its layers when completed */
  analysis: AnalysisInfo | null;
  ownerModule: ModuleDefinition | null;
  /** data extent ("Fit to data") */
  bbox: BBoxList | null;
  /** extent the camera fits whenever `fitKey` changes (default `bbox`), e.g. a new result */
  fitBbox?: BBoxList | null;
  fitKey: string | null;
  initialView?: { center: [number, number]; zoom: number };
  onSelectFeature: (index: number, options: { additive: boolean }) => void;
  onClearSelection: () => void;
  onViewTable: (layerId: string) => void;
  /** smaller legend, collapsed by default (mobile) */
  compact?: boolean;
}

export function WorkspaceMap({
  mapRef,
  features,
  selected,
  analysis,
  ownerModule,
  bbox,
  fitBbox,
  fitKey,
  initialView,
  onSelectFeature,
  onClearSelection,
  onViewTable,
  compact = false,
}: WorkspaceMapProps) {
  const hydrated = useSettingsHydrated();
  const basemap = useSettings((s) => s.basemap);
  const units = useUnits();
  const reducedMotion = usePrefersReducedMotion();
  const { resolvedTheme } = useTheme();
  const theme: "dark" | "light" = resolvedTheme === "light" ? "light" : "dark";

  const hovered = useUi((s) => s.hoveredFeature);
  const focused = useUi((s) => s.focusedFeature);
  const hiddenLayers = useUi((s) => s.hiddenLayers);
  const view3d = useUi((s) => s.view3d);
  const time = useUi((s) => s.playback.time);
  const resultHighlight = useUi((s) => s.resultHighlight);
  const setHoveredFeature = useUi((s) => s.setHoveredFeature);
  const setFocusedFeature = useUi((s) => s.setFocusedFeature);
  const setView3d = useUi((s) => s.setView3d);

  const [hatch, setHatch] = React.useState<HatchAtlas | null>(null);
  const [shortcutsOpen, setShortcutsOpen] = React.useState(false);

  React.useEffect(() => {
    try {
      setHatch(createHatchAtlas());
    } catch {
      setHatch(null);
    }
  }, []);

  const hiddenSet = React.useMemo(() => new Set(hiddenLayers), [hiddenLayers]);
  const datasetHidden = hiddenSet.has(DATASET_LAYER_ID);

  usePlanMapInteractions(mapRef);
  const drawing = usePlan((s) => s.drawing);
  // A panel waiting for a map point ("Place on map"): crosshair, the map takes the focus, and
  // Space/Enter on the focused map places it at the map centre (lib/map-pick.ts).
  const picking = useMapPick((s) => s.request !== null);
  useMapPickKeyboard(
    React.useCallback(() => {
      const centre = mapRef.current?.getMap()?.getCenter().wrap();
      return centre ? [centre.lng, centre.lat] : null;
    }, [mapRef]),
    React.useCallback(() => mapRef.current?.focus(), [mapRef]),
  );
  const planVertices = usePlan((s) => s.vertices);
  const planCursor = usePlan((s) => s.cursor);
  const planDraft = usePlan((s) => s.draft);
  const pinRadiusM = usePlan((s) => s.pinRadiusM);
  const goTo = usePlan((s) => s.goTo);
  const context = useContextData();
  const contextOutput = React.useMemo(
    () => contextLayers(context.data, { hatch, theme, units }),
    [context.data, hatch, theme, units],
  );
  const planOutput = React.useMemo(
    () => planLayers({ drawing, vertices: planVertices, cursor: planCursor, draft: planDraft, pinRadiusM, goTo }, theme),
    [drawing, planVertices, planCursor, planDraft, pinRadiusM, goTo, theme],
  );

  // Features of source layers the user switched off are neither drawn nor reachable with N.
  const shownFeatures = React.useMemo<DatasetFeatureCollection | null>(() => {
    if (!features || !hiddenLayers.some((id) => id.startsWith(sourceLayerToggleId("")))) return features;
    return { ...features, features: features.features.filter((f) => !hiddenSet.has(sourceLayerToggleId(f.properties._layer))) };
  }, [features, hiddenLayers, hiddenSet]);

  const dataLayers = React.useMemo(
    () =>
      datasetHidden
        ? []
        : datasetLayers({ features: shownFeatures, selected, hovered, focused, theme }),
    [datasetHidden, shownFeatures, selected, hovered, focused, theme],
  );

  const completed = analysis !== null && analysis.status === "COMPLETED" && analysis.result !== null;
  const moduleOutput = React.useMemo<ModuleLayerOutput>(() => {
    if (!completed || !analysis || !ownerModule?.buildLayers || !ownerModule.kinds.includes(analysis.kind)) return EMPTY_OUTPUT;
    const ctx: ModuleLayerContext = {
      analysis,
      features,
      hiddenLayers: hiddenSet,
      hoveredFeature: hovered,
      selectedFeature: selected[0] ?? null,
      time,
      resultHighlight,
      reducedMotion,
      units,
      hatch,
      theme,
      view3d,
    };
    const build = ownerModule.buildLayers;
    try {
      // 2D: everything flat; 3D: heights above the ground (see `withHeights`).
      return withHeights(view3d, () => build(analysis, ctx));
    } catch (error) {
      if (process.env.NODE_ENV !== "production") console.error(`[${ownerModule.id}] buildLayers failed`, error);
      return EMPTY_OUTPUT;
    }
  }, [completed, analysis, ownerModule, features, hiddenSet, hovered, selected, time, resultHighlight, reducedMotion, units, hatch, theme, view3d]);

  // Context layers under everything, then the dataset, so module results (zones, cells, routes)
  // stay visible above it; the plan preview (drawing, draft, "Go to" target) on top.
  const layers = React.useMemo(
    () => [...contextOutput.layers, ...dataLayers, ...filterHiddenLayers(moduleOutput.layers, hiddenSet), ...planOutput],
    [contextOutput.layers, dataLayers, moduleOutput.layers, hiddenSet, planOutput],
  );

  const legends = React.useMemo<LegendSpec[]>(() => {
    const out: LegendSpec[] = [];
    if (features && features.features.length > 0 && !datasetHidden) out.push(datasetLegend());
    out.push(...filterHiddenLegends(moduleOutput.legends, hiddenSet));
    out.push(...contextOutput.legends);
    return out;
  }, [features, datasetHidden, moduleOutput.legends, hiddenSet, contextOutput.legends]);

  const focusTargets = React.useMemo<MapFocusTarget[]>(() => {
    if (!shownFeatures || datasetHidden) return [];
    const out: MapFocusTarget[] = [];
    for (const f of shownFeatures.features) {
      const b = geometryBounds(f.geometry);
      if (!b) continue;
      out.push({ index: f.properties._index, label: datasetFeatureLabel(f.properties), position: boundsCenter(b) });
    }
    return out;
  }, [shownFeatures, datasetHidden]);

  const ownerRef = React.useRef(ownerModule);
  ownerRef.current = ownerModule;
  const unitsRef = React.useRef(units);
  unitsRef.current = units;

  const onHover = React.useCallback(
    (info: PickingInfo | null) => {
      // While drawing, the map only takes points: no hover highlight.
      setHoveredFeature(usePlan.getState().drawing ? null : pickedFeatureIndex(info));
    },
    [setHoveredFeature],
  );

  const onClick = React.useCallback(
    (info: PickingInfo, mods: PointerModifiers) => {
      // A panel asked for a point ("Place observer"): the click places it and selects nothing.
      if (consumeMapPick(info)) return;
      // While drawing, clicks place points (plan-map.ts) and select nothing.
      if (usePlan.getState().drawing) return;
      // Context layers are reference data: a click on them is a click on the map.
      const onContext = isContextLayerId(info.layer?.id ?? "");
      const idx = pickedFeatureIndex(info);
      if (idx === null && info.picked && !onContext && ownerRef.current?.onPick?.(info)) return;
      if (idx === null) {
        if (!info.picked || onContext) onClearSelection();
        return;
      }
      onSelectFeature(idx, { additive: mods.shiftKey || mods.ctrlKey || mods.metaKey });
    },
    [onClearSelection, onSelectFeature],
  );

  const getTooltip = React.useCallback((info: PickingInfo): string | null => {
    if (usePlan.getState().drawing) return null;
    const contextText = describeContextPick(info, unitsRef.current);
    if (contextText !== null) return contextText;
    const idx = pickedFeatureIndex(info);
    if (idx !== null) {
      const p = (info.object as { properties?: Record<string, unknown> } | null)?.properties ?? {};
      const name = datasetFeatureLabel({ _name: (p._name as string | null) ?? null, _index: idx });
      const area = typeof p._area_m2 === "number" ? formatArea(p._area_m2, unitsRef.current) : null;
      const length = typeof p._length_m === "number" ? formatLength(p._length_m, unitsRef.current) : null;
      return [name, area ?? length].filter(Boolean).join(" · ");
    }
    return ownerRef.current?.describePick?.(info) ?? null;
  }, []);

  // One-off focus requests from module result tables (`useUi().focusMap`).
  const mapFocus = useUi((s) => s.mapFocus);
  React.useEffect(() => {
    if (mapFocus) mapRef.current?.fitBounds(mapFocus.bbox, { maxZoom: 17 });
  }, [mapFocus, mapRef]);

  const onFit = React.useCallback(() => {
    if (bbox) mapRef.current?.fitBounds(bbox);
  }, [bbox, mapRef]);

  if (!hydrated) return <MapSkeleton />;

  return (
    <>
      <MapLoader
        ref={mapRef}
        id="map"
        ariaLabel={features ? `Map of the dataset with ${plural(features.features.length, "feature")}` : "Map"}
        layers={layers}
        bbox={fitBbox ?? bbox}
        fitKey={fitKey}
        basemap={basemap}
        theme={theme}
        view3d={view3d}
        reducedMotion={reducedMotion}
        units={units}
        focusTargets={focusTargets}
        focusedFeature={focused}
        onFocusFeature={setFocusedFeature}
        onSelectFeature={(index) => onSelectFeature(index, { additive: false })}
        onClearSelection={onClearSelection}
        onHover={onHover}
        onClick={onClick}
        getTooltip={getTooltip}
        onShowShortcuts={() => setShortcutsOpen(true)}
        onFit={onFit}
        initialView={initialView}
        cursor={drawing || picking ? "crosshair" : undefined}
        topLeft={<OperationInput mapRef={mapRef} compact={compact} />}
      >
        <div className="absolute right-2 top-2">
          <MapControls
            mapRef={mapRef}
            view3d={view3d}
            onView3dChange={setView3d}
            onFit={bbox ? onFit : undefined}
            basemap={basemap}
            onShowShortcuts={() => setShortcutsOpen(true)}
          />
        </div>
        <div className="absolute bottom-10 left-2">
          <Legend legends={legends} onViewTable={onViewTable} defaultCollapsed={compact} />
        </div>
        {drawing || picking ? (
          // The map centre: where Space (and "Add the map centre") places a point from the keyboard,
          // while drawing (plan-map.ts) or while a panel waits for a map point (lib/map-pick.ts).
          <div
            aria-hidden
            className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-accent drop-shadow [&_svg]:size-6"
          >
            <Crosshair strokeWidth={1.5} />
          </div>
        ) : null}
      </MapLoader>
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </>
  );
}
