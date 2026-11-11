"use client";

/**
 * Airspace module: indicative zones, free-flight ceilings, terrain and obstacle clearance for
 * the selected features (or the whole dataset), plus an H3 flyability grid. The planned height
 * is read above ground (AGL), above the take-off point or above sea level (AMSL): the last two
 * give terrain-true heights above ground along the flight, the stretches below a minimum height
 * and above the ceiling (airbrief_core/analyses/heights.py).
 */

import * as React from "react";
import type { PickingInfo } from "@deck.gl/core";
import { Plane } from "lucide-react";
import { ZONE_LABEL, toneForFlyability, toneForZone, toneRgba } from "@/lib/colors";
import { areaParts, formatCoord, formatHeight, formatLength, formatNumber, heightParts, lengthParts, plural } from "@/lib/format";
import { HEIGHT_REFERENCE_SUFFIX, OBSTACLE_HEIGHT_SOURCE_LABEL, TAKEOFF_SOURCE_LABEL } from "@/lib/labels";
import {
  MODULE_META,
  type LegendSpec,
  type ModuleDefinition,
  type ModuleLayerContext,
  type ModuleLayerOutput,
  type ModulePanelProps,
  type ModuleResultsProps,
} from "@/lib/modules";
import { useUnits } from "@/lib/store";
import {
  analysisResult,
  type AirspaceResult,
  type AnalysisInfo,
  type FeatureCollection,
  type FlyabilityCell,
  type Geometry,
  type HeightReference,
  type HeightSegment,
  type ObstacleHit,
  type TakeoffPoint,
  type ZoneHit,
} from "@/lib/types";
import { derived, geoJsonLayer, h3Layer, hatchZoneLayer, mapPalette, pointLayer, textLabelLayer, zoneLegend } from "@/components/map/layers/common";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { KpiGrid, KpiTile } from "@/components/ui/kpi-tile";
import { useAnalysisRunner } from "@/components/workspace/analysis-runner";
import { PlanStartHint } from "@/components/workspace/plan-start";
import {
  Advanced,
  DEFAULT_HEIGHT_REFERENCE,
  DemCaveat,
  Disclaimer,
  FormErrorSummary,
  HeightReferenceField,
  InputHint,
  IssueList,
  ModuleSection,
  NumField,
  RunButton,
  SwitchField,
  SyntheticNote,
  ToneBadge,
  VerdictBanner,
  ZONE_ORDER,
  currentUnits,
  formatDistanceKm,
  heightReferenceFromParams,
  heightReferenceParams,
  pointBounds,
  resultLayer,
  useFocusMap,
  useFormErrors,
  type HeightReferenceState,
} from "../shared";
import { ResetFormButton, paramBoolean, paramNumber, useFormFromAnalysis } from "../analysis-form";
import { BatchToggle, batchDisabledReason, withBatch } from "../batch";
import { batchFeatureCount, batchFromParams, batchParams } from "@/lib/batch";
import { HeightProfileChart, HeightStretchTable, HeightsNotAssessedBanner, heightRangeParts, heightStretches } from "../height-profile";
import { DigitalSkyLink, NotAssessedBanner, RedZonesBanner } from "./assessment-notice";
import { ZoneInputsFields, defaultZoneInputs, useRingsProfile, zoneInputsFromParams, zoneInputsParams, type ZoneInputs } from "./zone-inputs";
import { BandChips, InactiveZones, zoneActivityColumns } from "./zone-results";
import { h3Collection } from "../h3-layers";

/** Heights matching the zone ceilings; in imperial they show as whole feet but keep these exact metres. */
const ALTITUDE_PRESETS: readonly number[] = [30, 60, 120];

/** Form defaults, the same as the API's (airbrief_core AirspaceParams). */
const DEFAULTS = {
  altitude: 120,
  grid: true,
  datapackZones: true,
  resolution: 9,
  obstacleSearch: 300,
  clearance: 30,
  spacing: 30,
  maxGridCells: 20_000,
  minAgl: 30,
} as const;

/** Largest planned height above ground or above take-off (an AMSL level may be higher). */
const MAX_ABOVE_GROUND_M = 1500;
const MAX_AMSL_M = 9000;

const FLYABILITY_LABEL: Record<FlyabilityCell["status"], string> = {
  free: "Free flight up to the planned height",
  limited: "Ceiling or obstacle below the planned height",
  permission: "Permission required",
  restricted: "Restricted (red zone)",
};

const HEIGHT_FIELD_LABEL: Record<HeightReference, string> = {
  agl: "Height above ground",
  takeoff: "Height above take-off",
  amsl: "Flight level (AMSL)",
};

const HEIGHT_FIELD_HINT: Record<HeightReference, string> = {
  agl: "Highest point of the planned flight, above ground level.",
  takeoff: "As the aircraft shows it: above the ground at the take-off point. The flight holds this level.",
  amsl: "The level held, above mean sea level: the ground elevation plus the height wanted above it.",
};

/** "120 m AGL", "120 m above take-off", "1,050 m AMSL" in the user's units. */
function plannedHeightText(value: number, reference: HeightReference | undefined, units: ReturnType<typeof useUnits>): string {
  return `${formatHeight(value, units)} ${HEIGHT_REFERENCE_SUFFIX[reference ?? "agl"]}`;
}

/** Where the take-off point is and how its ground elevation is known. */
function takeoffText(t: TakeoffPoint, units: ReturnType<typeof useUnits>): string {
  const where = TAKEOFF_SOURCE_LABEL[t.source];
  const elevation =
    t.elevation_m === null
      ? "ground elevation unknown"
      : `ground ${formatHeight(t.elevation_m, units)} AMSL, ${t.elevation_source === "override" ? "typed" : "from the terrain model"}`;
  return `${formatCoord(t.lon, t.lat)} (${where}); ${elevation}`;
}

function totalLength(segments: readonly HeightSegment[] | undefined): number {
  return (segments ?? []).reduce((sum, s) => sum + s.length_m, 0);
}

function AirspacePanel({ file, features, selectedFeatures, analysis }: ModulePanelProps) {
  const runner = useAnalysisRunner();
  const form = useFormErrors();
  const [altitude, setAltitude] = React.useState<number | null>(DEFAULTS.altitude);
  const [heights, setHeights] = React.useState<HeightReferenceState>(DEFAULT_HEIGHT_REFERENCE);
  const [minAgl, setMinAgl] = React.useState<number | null>(DEFAULTS.minAgl);
  const [grid, setGrid] = React.useState<boolean>(DEFAULTS.grid);
  const [datapackZones, setDatapackZones] = React.useState<boolean>(DEFAULTS.datapackZones);
  const [resolution, setResolution] = React.useState<number | null>(DEFAULTS.resolution);
  const [obstacleSearch, setObstacleSearch] = React.useState<number | null>(DEFAULTS.obstacleSearch);
  const [clearance, setClearance] = React.useState<number | null>(DEFAULTS.clearance);
  const [spacing, setSpacing] = React.useState<number | null>(DEFAULTS.spacing);
  const [maxGridCells, setMaxGridCells] = React.useState<number | null>(DEFAULTS.maxGridCells);
  const [batch, setBatch] = React.useState(false);
  const [keyProperty, setKeyProperty] = React.useState<string | null>(null);
  const [zones, setZones] = React.useState<ZoneInputs>(defaultZoneInputs);
  useRingsProfile(zones.profile);

  /** Every field from stored parameters; missing ones take the default (as the API does). */
  const applyParams = (p: Readonly<Record<string, unknown>>) => {
    setAltitude(paramNumber(p, "altitude_agl_m", DEFAULTS.altitude));
    setHeights(heightReferenceFromParams(p));
    setMinAgl(paramNumber(p, "min_agl_m", DEFAULTS.minAgl));
    setGrid(paramBoolean(p, "grid", DEFAULTS.grid));
    setDatapackZones(paramBoolean(p, "include_datapack_zones", DEFAULTS.datapackZones));
    setResolution(paramNumber(p, "h3_resolution", DEFAULTS.resolution));
    setObstacleSearch(paramNumber(p, "obstacle_search_m", DEFAULTS.obstacleSearch));
    setClearance(paramNumber(p, "vertical_clearance_m", DEFAULTS.clearance));
    setSpacing(paramNumber(p, "profile_spacing_m", DEFAULTS.spacing));
    setMaxGridCells(paramNumber(p, "max_grid_cells", DEFAULTS.maxGridCells));
    const b = batchFromParams(p);
    setBatch(b.on);
    setKeyProperty(b.keyProperty);
    setZones(zoneInputsFromParams(p));
  };
  const { markStarted, setFields, fieldsKey } = useFormFromAnalysis(analysis, applyParams);

  const ready = file?.status === "COMPLETED";
  const batchCount = batchFeatureCount(features, selectedFeatures);
  const disabledReason = !file
    ? "Draw, pin or open a dataset first: the check runs on its features."
    : !ready
      ? "Wait for the dataset to finish processing."
      : batchDisabledReason("airspace", batch, batchCount);

  const reference = heights.reference;
  const amsl = reference === "amsl";

  const onRun = form.guard(() => {
    void runner
      .run("airspace", {
        altitude_agl_m: altitude ?? DEFAULTS.altitude,
        ...heightReferenceParams(heights),
        min_agl_m: minAgl ?? DEFAULTS.minAgl,
        grid,
        include_datapack_zones: datapackZones,
        h3_resolution: resolution ?? DEFAULTS.resolution,
        obstacle_search_m: obstacleSearch ?? DEFAULTS.obstacleSearch,
        vertical_clearance_m: clearance ?? DEFAULTS.clearance,
        profile_spacing_m: spacing ?? DEFAULTS.spacing,
        max_grid_cells: maxGridCells ?? DEFAULTS.maxGridCells,
        ...batchParams(batch, keyProperty),
        ...zoneInputsParams(zones),
      })
      .then((info) => {
        if (info) markStarted(info.id);
      });
  });

  return (
    <form className="space-y-4 text-sm" onSubmit={(e) => e.preventDefault()} aria-label="Airspace check">
      {!file ? (
        <PlanStartHint purpose="The check covers the pin and its radius, the line or the area." />
      ) : (
        <InputHint>
          {selectedFeatures.length > 0
            ? `Checks the ${selectedFeatures.length} selected feature${selectedFeatures.length === 1 ? "" : "s"}.`
            : "Checks every feature of the dataset. Select features in the table or on the map to check only those."}
        </InputHint>
      )}
      <FormErrorSummary form={form} />
      <RedZonesBanner />

      <ModuleSection title="Planned height">
        <HeightReferenceField
          value={heights}
          onChange={setHeights}
          form={form}
          fieldsKey={fieldsKey}
          pickId="airspace-takeoff"
          defaultTakeoff="the first point of a route, the centre of an area, or the point itself"
        />
        <NumField
          // A new reference is a new field: its bounds, presets and unit text change.
          key={`altitude-${reference}-${fieldsKey}`}
          label={HEIGHT_FIELD_LABEL[reference]}
          unit="m"
          value={altitude}
          onChange={setAltitude}
          min={1}
          max={amsl ? MAX_AMSL_M : MAX_ABOVE_GROUND_M}
          hint={HEIGHT_FIELD_HINT[reference]}
          presets={amsl ? undefined : { metric: ALTITUDE_PRESETS, label: "Height presets" }}
          form={form}
        />
        {/* The minimum is checked where the height above ground varies: above take-off and AMSL. */}
        {reference === "agl" ? null : (
          <NumField
            key={`min-agl-${fieldsKey}`}
            label="Minimum height above ground"
            unit="m"
            value={minAgl}
            onChange={setMinAgl}
            min={0}
            max={MAX_ABOVE_GROUND_M}
            hint="Stretches of a route lower than this above the ground are reported, with the stretches above the ceiling."
            form={form}
          />
        )}
      </ModuleSection>

      <ModuleSection title="Options">
        <SwitchField label="Flyability grid" checked={grid} onChange={setGrid} hint="Hexagon grid showing where free flight is possible." />
        <SwitchField
          label="Data-pack zones"
          checked={datapackZones}
          onChange={setDatapackZones}
          hint="Include restricted zones shipped with the data pack."
        />
        {file ? (
          <BatchToggle
            kind="airspace"
            on={batch}
            onChange={setBatch}
            keyProperty={keyProperty}
            onKeyProperty={setKeyProperty}
            features={features}
            count={batchCount}
            selection={selectedFeatures.length > 0}
          />
        ) : null}
      </ModuleSection>

      <ZoneInputsFields value={zones} onChange={setZones} form={form} fieldsKey={fieldsKey} analysis={analysis} />

      <Advanced form={form}>
        <React.Fragment key={fieldsKey}>
          <NumField label="Grid resolution (H3)" value={resolution} onChange={setResolution} min={6} max={11} step={1} decimals={0} integer hint="9 ≈ 0.1 km² cells; higher is finer." form={form} />
          <NumField
            label="Grid cell limit"
            value={maxGridCells}
            onChange={setMaxGridCells}
            min={100}
            max={200_000}
            step={1000}
            decimals={0}
            integer
            hint="Most hexagons in the flyability grid. A larger area at a fine resolution is cut at this many, and the result says so; the brief flags a limit below 20,000."
            form={form}
          />
          <NumField label="Obstacle search distance" unit="m" value={obstacleSearch} onChange={setObstacleSearch} min={0} max={5000} form={form} />
          <NumField label="Vertical clearance" unit="m" value={clearance} onChange={setClearance} min={0} max={500} hint="Required gap above an obstacle." form={form} />
          <NumField label="Profile spacing (routes)" unit="m" value={spacing} onChange={setSpacing} min={5} max={1000} form={form} />
        </React.Fragment>
      </Advanced>

      <RunButton onRun={onRun} pending={runner.isPending} disabledReason={disabledReason} label="Check airspace" />
      <ResetFormButton onReset={() => setFields({})} />
    </form>
  );
}

/** "25 m" or "25 m (assumed height)" when OSM maps no height and a per-kind default was used. */
function obstacleHeightText(o: ObstacleHit, units: ReturnType<typeof useUnits>): string {
  const height = formatHeight(o.height_m, units);
  return o.height_assumed ? `${height} (${OBSTACLE_HEIGHT_SOURCE_LABEL.default.toLowerCase()})` : height;
}

function AirspaceHeights({ result, units }: { result: AirspaceResult; units: ReturnType<typeof useUnits> }) {
  const focus = useFocusMap();
  const reference = result.height_reference ?? "agl";
  const terrainTrue = reference !== "agl";
  const stretches = heightStretches(result.below_min_agl, result.above_ceiling);
  const isRoute = result.profile.length > 1;
  const level = result.flight_level_amsl_m ?? null;
  const flightLabel =
    terrainTrue && level !== null
      ? `Flight level (${formatHeight(level, units)} AMSL)`
      : `Flight (${plannedHeightText(result.altitude_agl_m, reference, units)})`;
  const stretchFeatures = resultLayer(result.layers, "heights");
  if (!terrainTrue && !isRoute && stretches.length === 0) return null;
  return (
    <ModuleSection title="Heights above ground">
      {result.takeoff ? (
        <p className="text-xs text-fg-muted">
          <span className="font-medium text-fg">Take-off:</span> {takeoffText(result.takeoff, units)}.
          {level !== null ? ` Flight level ${formatHeight(level, units)} AMSL.` : ""}
        </p>
      ) : null}
      {isRoute ? (
        <HeightProfileChart
          title="Height profile along the route"
          points={result.profile}
          units={units}
          flightLabel={flightLabel}
          minAgl={result.min_agl_m ?? null}
          below={result.below_min_agl ?? []}
          above={result.above_ceiling ?? []}
        />
      ) : null}
      {stretches.length > 0 ? (
        <HeightStretchTable
          stretches={stretches}
          units={units}
          onSelect={(s) => {
            const feature = stretchFeatures.features.find((f) => f.id === s.id);
            if (feature) focus(feature);
          }}
        />
      ) : isRoute && terrainTrue && result.heights_assessed !== false ? (
        <p className="text-xs text-fg-muted">
          The route stays between {formatHeight(result.min_agl_m ?? DEFAULTS.minAgl, units)} above the ground and the free-flight
          ceiling.
        </p>
      ) : null}
      <DemCaveat dem={result.dem} />
    </ModuleSection>
  );
}

function AirspaceResults({ analysis }: ModuleResultsProps) {
  const units = useUnits();
  const focus = useFocusMap();
  const result = analysisResult(analysis, "airspace");

  const zoneColumns = React.useMemo<DataTableColumn<ZoneHit>[]>(
    () => [
      { id: "name", header: "Zone", rowHeader: true, cell: (z) => z.name, sortValue: (z) => z.name },
      {
        id: "zone",
        header: "Class",
        cell: (z) =>
          z.basis === "verify_official_map" ? (
            <ToneBadge tone="warn" label="Verify on Digital Sky" />
          ) : (
            <ToneBadge tone={toneForZone(z.zone)} label={ZONE_LABEL[z.zone]} />
          ),
        sortValue: (z) => ZONE_ORDER[z.zone],
      },
      { id: "ring", header: "Ring", cell: (z) => z.ring ?? "—", sortValue: (z) => z.ring ?? "" },
      {
        id: "ceiling",
        header: "Free-flight ceiling",
        numeric: true,
        cell: (z) =>
          z.permission_required
            ? z.agl_margin_m != null && z.agl_margin_m < 0
              ? `Permission (${formatHeight(-z.agl_margin_m, units)} over)`
              : "Permission"
            : formatHeight(z.ceiling_agl_m, units),
        sortValue: (z) => (z.permission_required ? -1 : z.ceiling_agl_m),
      },
      { id: "distance", header: "Distance", numeric: true, cell: (z) => formatDistanceKm(z.distance_km, units), sortValue: (z) => z.distance_km },
      {
        id: "overlap",
        header: "Overlap",
        numeric: true,
        cell: (z) =>
          z.overlap_area_m2 !== null && z.overlap_area_m2 !== undefined
            ? areaParts(z.overlap_area_m2, units).text
            : formatLength(z.overlap_length_m ?? null, units),
        sortValue: (z) => z.overlap_area_m2 ?? z.overlap_length_m ?? null,
      },
      ...zoneActivityColumns(),
    ],
    [units],
  );

  const obstacleColumns = React.useMemo<DataTableColumn<ObstacleHit>[]>(
    () => [
      { id: "name", header: "Obstacle", rowHeader: true, cell: (o) => o.name ?? o.kind, sortValue: (o) => o.name ?? o.kind },
      {
        id: "conflict",
        header: "Status",
        cell: (o) => <ToneBadge tone={o.conflict ? "danger" : "ok"} label={o.conflict ? "Conflict" : "Clear"} />,
        sortValue: (o) => (o.conflict ? 0 : 1),
      },
      {
        id: "height",
        header: "Height",
        numeric: true,
        cell: (o) =>
          o.height_assumed ? (
            <>
              {formatHeight(o.height_m, units)}
              <span className="block text-fg-muted">{OBSTACLE_HEIGHT_SOURCE_LABEL.default}</span>
            </>
          ) : (
            formatHeight(o.height_m, units)
          ),
        sortValue: (o) => o.height_m,
      },
      { id: "clearance", header: "Clearance", numeric: true, cell: (o) => formatHeight(o.clearance_m, units), sortValue: (o) => o.clearance_m },
      { id: "distance", header: "Distance", numeric: true, cell: (o) => formatLength(o.distance_m, units), sortValue: (o) => o.distance_m },
    ],
    [units],
  );

  if (!analysis) return null;
  if (!result) {
    return analysis.status === "COMPLETED" ? (
      <EmptyState compact icon={<Plane />} title="No result" description="The analysis finished without a result." />
    ) : null;
  }

  const reference = result.height_reference ?? "agl";
  const terrainTrue = reference !== "agl";
  const planned = plannedHeightText(result.altitude_agl_m, reference, units);
  const isRoute = result.profile.length > 1;
  const nearest = result.nearest_aerodromes[0] ?? null;
  const ceiling = result.permission_required
    ? "Permission required"
    : result.effective_ceiling_agl_m === null
      ? "Restricted"
      : heightParts(result.effective_ceiling_agl_m, units);
  const belowM = totalLength(result.below_min_agl);
  const aboveM = totalLength(result.above_ceiling);
  const cellsBelow = result.cells_below_min_agl ?? 0;
  const cellsAbove = result.cells_above_ceiling ?? 0;
  const belowAny = belowM > 0 || cellsBelow > 0;
  const aboveAny = aboveM > 0 || cellsAbove > 0;
  const minAgl = result.min_agl_m ?? DEFAULTS.minAgl;
  const showHeightTiles = terrainTrue || belowAny || aboveAny;
  const assessed = result.heights_assessed !== false;
  const notAssessedMessage = result.issues.find((i) => i.code === "heights_not_assessed")?.message ?? null;

  return (
    <div className="space-y-4">
      <VerdictBanner verdict={result.verdict} title="Airspace" summary={result.summary} />
      <RedZonesBanner />
      <NotAssessedBanner result={result} />
      <HeightsNotAssessedBanner assessed={result.heights_assessed} message={notAssessedMessage} />
      <SyntheticNote synthetic={analysis.datapack_synthetic} />

      <KpiGrid label="Airspace summary">
        <KpiTile
          label="Free-flight ceiling"
          value={ceiling}
          tone={result.permission_required ? "warn" : "neutral"}
          toneLabel={result.permission_required ? "Needs permission" : undefined}
          explanation={`Planned ${planned}`}
        />
        <KpiTile label="Highest terrain" value={heightParts(result.max_terrain_m, units)} explanation="Above mean sea level" />
        <KpiTile
          label="Planned top (AMSL)"
          value={heightParts(result.max_altitude_amsl_m, units)}
          explanation={terrainTrue && result.flight_level_amsl_m != null ? "The flight level held" : "Highest terrain + planned height"}
        />
        {showHeightTiles ? (
          <>
            <KpiTile
              label="Height above ground"
              value={assessed ? heightRangeParts(result.agl_min_m, result.agl_max_m, units) : "Not assessed"}
              tone={!assessed ? "warn" : belowAny ? "warn" : aboveAny ? "danger" : "ok"}
              toneLabel={!assessed ? "No terrain model" : belowAny ? `Below ${formatHeight(minAgl, units)} in places` : aboveAny ? "Above the ceiling in places" : "Within limits"}
              explanation={terrainTrue ? "Lowest to highest along the flight" : "Terrain following: constant"}
            />
            {/* AGL plans are not checked against a minimum (their height is the planned one). */}
            {terrainTrue ? (
              <KpiTile
                label="Below the minimum"
                value={isRoute ? lengthParts(belowM, units) : plural(cellsBelow, "cell")}
                tone={belowAny ? "warn" : "ok"}
                toneLabel={belowAny ? `Under ${formatHeight(minAgl, units)} AGL` : "None"}
                explanation={isRoute ? `${plural((result.below_min_agl ?? []).length, "stretch", "stretches")} of the route` : "Grid cells"}
              />
            ) : null}
            <KpiTile
              label="Above the ceiling"
              value={isRoute ? lengthParts(aboveM, units) : plural(cellsAbove, "cell")}
              tone={aboveAny ? "danger" : "ok"}
              toneLabel={aboveAny ? "Higher than the free-flight ceiling" : "None"}
              explanation={isRoute ? `${plural((result.above_ceiling ?? []).length, "stretch", "stretches")} of the route` : "Grid cells"}
            />
          </>
        ) : null}
        <KpiTile
          label="Nearest aerodrome"
          value={nearest ? lengthParts(nearest.distance_km * 1000, units) : "None in data"}
          explanation={nearest ? `${nearest.name} (${nearest.ident})` : "No aerodromes in the data pack"}
        />
        <KpiTile label="Zones touched" value={formatNumber(result.zones.length)} />
        <KpiTile
          label="Obstacle conflicts"
          value={formatNumber(result.obstacles.filter((o) => o.conflict).length)}
          tone={result.obstacles.some((o) => o.conflict) ? "danger" : "ok"}
          toneLabel={result.obstacles.some((o) => o.conflict) ? "Conflicts" : "None"}
        />
      </KpiGrid>

      <AirspaceHeights result={result} units={units} />
      <BandChips result={result} units={units} />

      <ModuleSection title={`Zones (${result.zones.length})`}>
        <DataTable
          data={result.zones}
          columns={zoneColumns}
          getRowId={(z, i) => `${z.source}-${z.ident ?? z.name}-${i}`}
          caption="Zones touched by the planned flight"
          dense
          emptyState={<EmptyState compact title="No zones" description="The geometry is outside every zone (default rules apply)." />}
        />
      </ModuleSection>
      <InactiveZones result={result} />

      <ModuleSection title={`Obstacles (${result.obstacles.length})`}>
        <DataTable
          data={result.obstacles}
          columns={obstacleColumns}
          getRowId={(o) => o.id}
          caption="Obstacles near the planned flight, conflicts first"
          onRowClick={(o) => focus(pointBounds(o.lon, o.lat, 300))}
          getRowLabel={(o) => `Zoom to ${o.name ?? o.kind}`}
          initialSort={{ columnId: "conflict", direction: "asc" }}
          dense
          emptyState={<EmptyState compact title="No obstacles" description="No obstacles in the search distance." />}
        />
        {result.obstacles.some((o) => o.height_assumed) ? (
          <p className="text-xs text-fg-muted">
            Assumed height: OpenStreetMap maps no height for this obstacle, so a typical height for its kind was used.
          </p>
        ) : null}
      </ModuleSection>

      {result.terrain ? (
        <div className="space-y-1">
          <p className="text-xs text-fg-muted">
            Terrain {formatHeight(result.terrain.min_m, units)}–{formatHeight(result.terrain.max_m, units)} AMSL (relief{" "}
            {formatHeight(result.terrain.relief_m, units)}, {formatNumber(result.terrain.samples)} samples from {result.dem?.title ?? result.terrain.source}).
          </p>
          {/* The caveat is already under the height profile when that section is shown. */}
          {terrainTrue || isRoute ? null : <DemCaveat dem={result.dem} />}
        </div>
      ) : null}

      {result.grid.length > 0 ? (
        <p className="text-xs text-fg-muted">
          Flyability grid: {formatNumber(result.grid.length)} cells at H3 resolution {result.grid_resolution}
          {result.grid_truncated ? " (truncated to the cell limit)" : ""}.
        </p>
      ) : null}

      <IssueList issues={result.issues} />
      <DigitalSkyLink />
      <Disclaimer text={result.disclaimer} />
    </div>
  );
}

const LAYERS = [
  { id: "airspace:input", label: "Checked area", resultLayer: "input" },
  { id: "airspace:zones", label: "Zones", resultLayer: "zones" },
  { id: "airspace:grid", label: "Flyability grid", resultLayer: "grid" },
  { id: "airspace:heights", label: "Height stretches", resultLayer: "heights" },
  { id: "airspace:takeoff", label: "Take-off", resultLayer: "takeoff" },
  { id: "airspace:obstacles", label: "Obstacles", resultLayer: "obstacles" },
] as const;

/** The flyability grid as hexagon polygons ("grid", as in the GeoJSON export), for its table. */
function airspaceDerivedLayers(analysis: AnalysisInfo): Record<string, FeatureCollection> {
  const result = analysis.result as AirspaceResult | null;
  if (!result || result.grid.length === 0) return {};
  return {
    grid: derived(result.grid, "airspace:grid-table", () =>
      h3Collection(result.grid, (c) => ({
        status: c.status,
        zone: c.zone,
        ceiling_agl_m: c.ceiling_agl_m,
        terrain_m: c.terrain_m ?? null,
        obstacle_max_height_m: c.obstacle_max_height_m ?? null,
        agl_m: c.agl_m ?? null,
      })),
    ),
  };
}

interface ZoneProps {
  zone?: "green" | "yellow" | "red";
  name?: string;
  label?: string;
  ring?: string;
  basis?: "rule" | "verify_official_map";
  /** user and pack zones: false when out of the planned window */
  active?: boolean | null;
}

/** Properties of a stretch in the result's `heights` layer. */
interface StretchProps extends HeightSegment {
  kind: "below_min_agl" | "above_ceiling";
}

type AnyFc = FeatureCollection<Geometry | null, Record<string, unknown>>;

function buildAirspaceLayers(analysis: AnalysisInfo, ctx: ModuleLayerContext): ModuleLayerOutput {
  const result = analysis.result as AirspaceResult | null;
  if (!result) return { layers: [], legends: [] };
  const palette = mapPalette();
  const layers = [];
  const legends: LegendSpec[] = [];

  layers.push(
    geoJsonLayer({
      id: "airspace:input",
      data: resultLayer(result.layers, "input"),
      fillColor: [palette.accent[0], palette.accent[1], palette.accent[2], 25],
      lineColor: palette.accent,
      lineWidth: 2,
      pickable: false,
      updateTriggers: [ctx.theme],
    }),
  );

  const zones = resultLayer(result.layers, "zones");
  layers.push(
    ...hatchZoneLayer<ZoneProps>({
      id: "airspace:zones",
      // Zones out of the planned window are listed, not drawn.
      data: derived(zones, "airspace:active-zones", () => ({
        ...zones,
        features: zones.features.filter((f) => (f.properties as ZoneProps | null)?.active !== false),
      })) as unknown as FeatureCollection<Geometry | null, ZoneProps>,
      getZone: (p) => p.zone ?? null,
      hatch: ctx.hatch,
      updateTriggers: [ctx.theme, ctx.hatch],
    }),
  );
  legends.push(
    zoneLegend("airspace:zones", {
      layerId: "airspace:zones",
      note: "Indicative only — not an official airspace map. Bands within 5 km of an airport are not defined by the published rule: verify on Digital Sky.",
    }),
  );

  if (result.grid.length > 0) {
    layers.push(
      h3Layer<FlyabilityCell>({
        id: "airspace:grid",
        data: result.grid,
        getHexagon: (c) => c.h3,
        getFillColor: (c) => toneRgba(toneForFlyability(c.status), c.status === "free" ? 70 : 120),
        getLineColor: () => [palette.border[0], palette.border[1], palette.border[2], 160],
        opacity: 0.8,
        updateTriggers: [ctx.theme],
      }),
    );
    legends.push({
      id: "airspace:grid",
      title: "Flyability",
      kind: "categorical",
      layerId: "airspace:grid",
      items: (["free", "limited", "permission", "restricted"] as const).map((s) => ({
        label: FLYABILITY_LABEL[s],
        color: `var(--${toneForFlyability(s) === "ok" ? "ok" : toneForFlyability(s) === "warn" ? "warn" : "danger"})`,
        shape: "hex" as const,
      })),
    });
  }

  // Stretches of a route below the minimum (amber, dashed) and above the ceiling (red, solid).
  const stretches = resultLayer(result.layers, "heights") as unknown as FeatureCollection<Geometry | null, StretchProps>;
  if (stretches.features.length > 0) {
    const belowFeatures = derived(stretches, "airspace:stretches-below", () => stretches.features.filter((f) => f.properties.kind === "below_min_agl"));
    const aboveFeatures = derived(stretches, "airspace:stretches-above", () => stretches.features.filter((f) => f.properties.kind === "above_ceiling"));
    layers.push(
      geoJsonLayer<StretchProps>({
        id: "airspace:heights",
        data: aboveFeatures,
        filled: false,
        lineColor: palette.danger,
        lineWidth: 6,
        updateTriggers: [ctx.theme],
      }),
      geoJsonLayer<StretchProps>({
        id: "airspace:heights/below",
        data: belowFeatures,
        filled: false,
        lineColor: palette.warn,
        lineWidth: 6,
        dashed: true,
        dashArray: [2, 1.5],
        updateTriggers: [ctx.theme],
      }),
    );
    legends.push({
      id: "airspace:heights",
      title: "Heights above ground",
      kind: "categorical",
      layerId: "airspace:heights",
      items: [
        ...(aboveFeatures.length > 0 ? [{ label: "Above the free-flight ceiling", color: "var(--danger)", shape: "line" as const }] : []),
        ...(belowFeatures.length > 0
          ? [{ label: `Below ${formatHeight(result.min_agl_m ?? DEFAULTS.minAgl, ctx.units)} above ground`, color: "var(--warn)", shape: "dashed-line" as const }]
          : []),
      ],
      note: "Terrain-true heights over the data-pack terrain model.",
    });
  }

  const takeoff = resultLayer(result.layers, "takeoff");
  if (takeoff.features.length > 0 && result.takeoff) {
    const t = result.takeoff;
    layers.push(
      geoJsonLayer({
        id: "airspace:takeoff",
        data: takeoff as AnyFc,
        fillColor: palette.accent,
        lineColor: palette.text,
        lineWidth: 2,
        pointRadius: 7,
        updateTriggers: [ctx.theme],
      }),
      textLabelLayer<TakeoffPoint>({
        id: "airspace:takeoff/label",
        data: derived(t, "airspace:takeoff-label", () => [t]),
        getPosition: (d) => [d.lon, d.lat],
        getText: () => "Take-off",
        pixelOffset: [0, -18],
        size: 11,
        updateTriggers: [ctx.theme],
      }),
    );
    legends.push({
      id: "airspace:takeoff",
      title: "Take-off",
      kind: "categorical",
      layerId: "airspace:takeoff",
      items: [{ label: `Take-off (${TAKEOFF_SOURCE_LABEL[t.source]})`, color: "var(--accent)", shape: "point" }],
    });
  }

  if (result.obstacles.length > 0) {
    const maxH = Math.max(...result.obstacles.map((o) => o.height_m), 1);
    layers.push(
      pointLayer<ObstacleHit>({
        id: "airspace:obstacles",
        data: result.obstacles,
        getPosition: (o) => [o.lon, o.lat],
        radius: (o) => 4 + 8 * (o.height_m / maxH),
        fillColor: (o) => (o.conflict ? palette.danger : palette.muted),
        lineColor: (o) => (o.conflict ? palette.text : palette.bg),
        lineWidth: (o) => (o.conflict ? 2 : 1),
        updateTriggers: [ctx.theme],
      }),
      textLabelLayer<ObstacleHit>({
        id: "airspace:obstacles/labels",
        data: derived(result.obstacles, "airspace:conflicts", () => result.obstacles.filter((o) => o.conflict)),
        getPosition: (o) => [o.lon, o.lat],
        getText: (o) => `${o.name ?? o.kind} ${formatHeight(o.height_m, ctx.units)}${o.height_assumed ? " (assumed)" : ""}`,
        pixelOffset: [0, -16],
        size: 11,
        updateTriggers: [ctx.units, ctx.theme],
      }),
    );
    legends.push({
      id: "airspace:obstacles",
      title: "Obstacles",
      kind: "categorical",
      layerId: "airspace:obstacles",
      items: [
        { label: "Conflict with planned height", color: "var(--danger)", shape: "point" },
        { label: "Clear", color: "var(--text-muted)", shape: "point" },
      ],
      note: result.obstacles.some((o) => o.height_assumed)
        ? "Circle size shows obstacle height; some heights are assumed (none mapped)."
        : "Circle size shows obstacle height.",
    });
  }

  return { layers, legends };
}

function describeAirspacePick(info: PickingInfo): string | null {
  const id = info.layer?.id ?? "";
  const obj = info.object as Record<string, unknown> | null;
  if (!obj) return null;
  const units = currentUnits();
  if (id.startsWith("airspace:obstacles")) {
    const o = obj as unknown as ObstacleHit;
    return `${o.name ?? o.kind}: ${obstacleHeightText(o, units)}${o.conflict ? " — conflict" : ""}`;
  }
  if (id.startsWith("airspace:grid")) {
    const c = obj as unknown as FlyabilityCell;
    const ceiling = c.ceiling_agl_m === null ? "permission required" : `ceiling ${formatHeight(c.ceiling_agl_m, units)}`;
    const agl = c.agl_m !== null && c.agl_m !== undefined ? `, ${formatHeight(c.agl_m, units)} above ground` : "";
    return `${FLYABILITY_LABEL[c.status]} (${ceiling}${agl})`;
  }
  if (id.startsWith("airspace:heights")) {
    const p = (obj.properties ?? {}) as Partial<StretchProps>;
    if (p.kind === undefined || p.length_m === undefined || p.peak_agl_m === undefined) return null;
    return p.kind === "above_ceiling"
      ? `Above the ceiling for ${formatLength(p.length_m, units)}: up to ${formatHeight(p.peak_agl_m, units)} above ground`
      : `Below the minimum for ${formatLength(p.length_m, units)}: down to ${formatHeight(p.peak_agl_m, units)} above ground`;
  }
  if (id.startsWith("airspace:takeoff")) {
    const p = ((obj.properties ?? obj) as Partial<TakeoffPoint>) ?? {};
    return p.elevation_m === null || p.elevation_m === undefined
      ? "Take-off (ground elevation unknown)"
      : `Take-off: ground ${formatHeight(p.elevation_m, units)} AMSL`;
  }
  if (id.startsWith("airspace:zones")) {
    const p = (obj.properties ?? {}) as ZoneProps;
    const kind = p.basis === "verify_official_map" ? "Verify on Digital Sky" : p.zone ? ZONE_LABEL[p.zone] : null;
    return [p.name, p.label ?? p.ring, kind].filter(Boolean).join(" · ");
  }
  return null;
}

export const definition: ModuleDefinition = withBatch(
  {
    ...MODULE_META.airspace,
    Panel: AirspacePanel,
    Results: AirspaceResults,
    layers: LAYERS,
    buildLayers: buildAirspaceLayers,
    describePick: describeAirspacePick,
    derivedLayers: airspaceDerivedLayers,
  },
  <DigitalSkyLink />,
);

export default definition;
