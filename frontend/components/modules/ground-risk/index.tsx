"use client";

/**
 * Ground-risk module: SORA 2.5 Step 2 / Step 8 evidence. With the operational volume (the
 * default) the flight geography, contingency volume and ground risk buffer are built from the
 * aircraft class' terms; the highest density on a square grid over that footprint gives the
 * Table 2 cell; the adjacent-area average density and outdoor assemblies within 1 km follow.
 * Switched off, the earlier footprint method (geometry + 1:1 or ballistic buffer, H3 cells) runs.
 * Never a GRC, ARC or SAIL.
 */

import * as React from "react";
import { Users } from "lucide-react";
import { areaParts, formatCoord, formatLength, formatNumber, lengthParts } from "@/lib/format";
import { MODULE_META, type ModuleDefinition, type ModulePanelProps, type ModuleResultsProps } from "@/lib/modules";
import { useMeta } from "@/lib/queries";
import { useUnits } from "@/lib/store";
import { analysisResult, type AssemblyInput, type GroundRiskParams, type GroundRiskResult } from "@/lib/types";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { KpiGrid, KpiTile } from "@/components/ui/kpi-tile";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAnalysisRunner } from "@/components/workspace/analysis-runner";
import { PlanStartHint } from "@/components/workspace/plan-start";
import {
  Advanced,
  Disclaimer,
  FormErrorSummary,
  InputHint,
  IssueList,
  ModuleSection,
  NumField,
  RunButton,
  SwitchField,
  SyntheticNote,
  pointBounds,
  useFocusMap,
  useFormErrors,
} from "../shared";
import { ResetFormButton, paramBoolean, paramChoice, paramNumber, useFormFromAnalysis } from "../analysis-form";
import { AssembliesEntry } from "./assemblies-entry";
import { LAYERS, buildGroundRiskLayers, describeGroundRiskPick, groundRiskDerivedLayers, hotspotRows, type DensityRow } from "./layers";
import { AdjacentArea, Assemblies, EvidenceLabel, IgrcGrid, VolumeTerms } from "./results";
import { VolumeFields } from "./volume-fields";
import { assembliesFromParams, presetVolume, volumeFormFrom, volumeFormFromParams, volumeParams, type VolumeForm } from "./volume";
import { BatchToggle, batchDisabledReason, withBatch } from "../batch";
import { batchFeatureCount, batchFromParams, batchParams } from "@/lib/batch";

const CUSTOM = "custom";
const DEFAULT_PRESET = "mapping_quad";
const BUFFER_RULES = ["one_to_one", "ballistic"] as const;
const STATISTICS = ["max", "p95"] as const;

/** Form defaults: the "mapping_quad" class and the API's operation defaults. */
const DEFAULTS = {
  dimension: 0.6,
  speed: 18,
  mass: 4,
  altitude: 120,
  resolution: 8,
  maxCells: 20_000,
} as const;

interface AircraftValues {
  dimension: number | null;
  speed: number | null;
  mass: number | null;
}

/** airbrief_core GroundRiskParams defaults, for aircraft values an API client left out. */
const API_AIRCRAFT_DEFAULTS: AircraftValues = { dimension: 1, speed: 15, mass: null };

/** iGRC severity in words (the tile's colour must never be the only signal). */
function igrcSeverity(igrc: number | null, smallUasRule: boolean): string {
  if (igrc === null) return "Outside the table";
  if (igrc >= 7) return "High (7 or more)";
  if (igrc >= 5) return "Medium (5–6)";
  return smallUasRule ? "Low (small-UAS rule)" : "Low (1–4)";
}

/** The lookup column for the tile: "Column ≤ 1 m, ≤ 25 m/s", or the core's "Beyond table (…)". */
function lookupColumnText(column: string): string {
  return column.startsWith("≤") ? `Column ${column}` : column;
}

function GroundRiskPanel({ file, features, selectedFeatures, analysis }: ModulePanelProps) {
  const runner = useAnalysisRunner();
  const form = useFormErrors();
  const units = useUnits();
  const meta = useMeta();
  const presets = React.useMemo(() => meta.data?.uas_presets ?? {}, [meta.data]);

  const [preset, setPreset] = React.useState<string>(DEFAULT_PRESET);
  const [dimension, setDimension] = React.useState<number | null>(DEFAULTS.dimension);
  const [speed, setSpeed] = React.useState<number | null>(DEFAULTS.speed);
  const [mass, setMass] = React.useState<number | null>(DEFAULTS.mass);
  const [altitude, setAltitude] = React.useState<number | null>(DEFAULTS.altitude);
  const [bufferRule, setBufferRule] = React.useState<"one_to_one" | "ballistic">("one_to_one");
  const [statistic, setStatistic] = React.useState<"max" | "p95">("max");
  const [controlled, setControlled] = React.useState(false);
  const [resolution, setResolution] = React.useState<number | null>(DEFAULTS.resolution);
  // SORA 2.5 operational volume (on by default; off = the earlier footprint method).
  const [useVolume, setUseVolume] = React.useState(true);
  const [volume, setVolume] = React.useState<VolumeForm>(() => volumeFormFrom(presetVolume(undefined)));
  // A ref, not state: a restored analysis sets it in a layout effect, before the effect below
  // (created in the same first render) runs, so the class defaults never overwrite its terms.
  const volumeTouched = React.useRef(false);
  const [adjacentArea, setAdjacentArea] = React.useState(true);
  const [gridM, setGridM] = React.useState<number | null>(null);
  const [assemblies, setAssemblies] = React.useState<AssemblyInput[]>([]);
  const [maxCells, setMaxCells] = React.useState<number | null>(DEFAULTS.maxCells);
  const [batch, setBatch] = React.useState(false);
  const [keyProperty, setKeyProperty] = React.useState<string | null>(null);

  // Before the aircraft classes arrive the terms are the API defaults: once they do, the
  // default class fills them (unless the user or a restored analysis already set them).
  const defaultTerms = presets[DEFAULT_PRESET]?.volume;
  React.useEffect(() => {
    if (!defaultTerms || volumeTouched.current) return;
    setVolume((v) => volumeFormFrom({ ...presetVolume(undefined), ...defaultTerms }, v.fgHeight));
  }, [defaultTerms]);

  // The aircraft class is not stored with an analysis: once the classes are known, it is the
  // one whose dimension, speed and mass match the restored values (Custom otherwise).
  const [restoredAircraft, setRestoredAircraft] = React.useState<AircraftValues | null>(null);
  React.useEffect(() => {
    if (!restoredAircraft || !meta.data) return;
    const match = Object.entries(presets).find(
      ([, p]) =>
        p.max_dimension_m === restoredAircraft.dimension &&
        p.max_speed_ms === restoredAircraft.speed &&
        p.takeoff_mass_kg === restoredAircraft.mass,
    );
    setPreset(match ? match[0] : CUSTOM);
    setRestoredAircraft(null);
  }, [restoredAircraft, presets, meta.data]);

  /** Every field from stored parameters; missing ones take the API's default. */
  const applyParams = (p: Readonly<Record<string, unknown>>) => {
    const aircraft: AircraftValues = {
      dimension: paramNumber(p, "max_dimension_m", API_AIRCRAFT_DEFAULTS.dimension),
      speed: paramNumber(p, "max_speed_ms", API_AIRCRAFT_DEFAULTS.speed),
      mass: paramNumber(p, "takeoff_mass_kg", API_AIRCRAFT_DEFAULTS.mass),
    };
    setDimension(aircraft.dimension);
    setSpeed(aircraft.speed);
    setMass(aircraft.mass);
    setRestoredAircraft(aircraft);
    setAltitude(paramNumber(p, "altitude_agl_m", DEFAULTS.altitude));
    setBufferRule(paramChoice(p, "buffer_rule", BUFFER_RULES, "one_to_one"));
    setStatistic(paramChoice(p, "density_statistic", STATISTICS, "max"));
    setControlled(paramBoolean(p, "controlled_ground_area", false));
    setResolution(paramNumber(p, "h3_resolution", DEFAULTS.resolution));
    // An analysis without a volume used the footprint method: the form reproduces it.
    const restored = volumeFormFromParams(p.volume, presetVolume(undefined));
    setUseVolume(restored !== null);
    if (restored) {
      setVolume(restored);
      volumeTouched.current = true;
    }
    setAdjacentArea(paramBoolean(p, "adjacent_area", true));
    setGridM(paramNumber(p, "grid_m", null));
    setAssemblies(assembliesFromParams(p.assemblies));
    setMaxCells(paramNumber(p, "max_cells", DEFAULTS.maxCells));
    const b = batchFromParams(p);
    setBatch(b.on);
    setKeyProperty(b.keyProperty);
  };
  const { markStarted, setFields, fieldsKey } = useFormFromAnalysis(analysis, applyParams);
  const resetForm = () => {
    setPreset(DEFAULT_PRESET);
    setFields({
      max_dimension_m: DEFAULTS.dimension,
      max_speed_ms: DEFAULTS.speed,
      takeoff_mass_kg: DEFAULTS.mass,
      volume: { ...presetVolume(presets[DEFAULT_PRESET]) },
    });
  };

  const applyPreset = (id: string) => {
    setPreset(id);
    const p = presets[id];
    if (!p) return;
    setDimension(p.max_dimension_m);
    setSpeed(p.max_speed_ms);
    setMass(p.takeoff_mass_kg);
    setVolume((v) => volumeFormFrom(presetVolume(p), v.fgHeight));
    volumeTouched.current = true;
  };

  /** Editing a preset value switches to Custom; a blur that re-commits the same value does not. */
  const editCustom = (setter: (v: number | null) => void, current: number | null) => (v: number | null) => {
    if (v !== current) setPreset(CUSTOM);
    setter(v);
  };

  const editVolume = (patch: Partial<VolumeForm>) => {
    setVolume((v) => ({ ...v, ...patch }));
    volumeTouched.current = true;
  };
  const resetTerms = () => {
    const source = preset === CUSTOM ? presets[DEFAULT_PRESET] : presets[preset];
    setVolume((v) => volumeFormFrom(presetVolume(source), v.fgHeight));
    volumeTouched.current = true;
  };

  const ready = file?.status === "COMPLETED";
  const batchCount = batchFeatureCount(features, selectedFeatures);
  const disabledReason = !file
    ? "Draw, pin or open a dataset first: the footprint is built from its features."
    : !ready
      ? "Wait for the dataset to finish processing."
      : batchDisabledReason("ground_risk", batch, batchCount);

  const onRun = form.guard(() => {
    const params: Omit<GroundRiskParams, "geometry"> = {
      max_dimension_m: dimension ?? DEFAULTS.dimension,
      max_speed_ms: speed ?? DEFAULTS.speed,
      takeoff_mass_kg: mass,
      altitude_agl_m: altitude ?? DEFAULTS.altitude,
      buffer_rule: bufferRule,
      density_statistic: statistic,
      controlled_ground_area: controlled,
      ...(useVolume
        ? { volume: volumeParams(volume), adjacent_area: adjacentArea, grid_m: gridM, assemblies }
        : { h3_resolution: resolution ?? DEFAULTS.resolution }),
      max_cells: maxCells ?? DEFAULTS.maxCells,
      ...batchParams(batch, keyProperty),
    };
    void runner.run("ground_risk", params).then((info) => {
      if (info) markStarted(info.id);
    });
  });

  const presetName = preset === CUSTOM ? null : (presets[preset]?.name ?? null);

  return (
    <form className="space-y-4 text-sm" onSubmit={(e) => e.preventDefault()} aria-label="Ground risk">
      {!file ? (
        <PlanStartHint purpose="The operational area is the pin's circle, the line or the area." tools={["area", "pin", "line"]} />
      ) : (
        <InputHint>
          {selectedFeatures.length > 0
            ? `Uses the ${selectedFeatures.length} selected feature${selectedFeatures.length === 1 ? "" : "s"} as the flight geography.`
            : "Uses every feature of the dataset as the flight geography. Select features to narrow it."}
        </InputHint>
      )}
      <FormErrorSummary form={form} />

      <ModuleSection title="Aircraft">
        <div className="space-y-1.5">
          <Label htmlFor="gr-preset">Aircraft class</Label>
          <Select value={preset} onValueChange={applyPreset}>
            <SelectTrigger id="gr-preset">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(presets).map(([id, p]) => (
                <SelectItem key={id} value={id}>
                  {p.name}
                </SelectItem>
              ))}
              <SelectItem value={CUSTOM}>Custom</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-fg-muted">
            Fills dimension, speed, mass and the contingency terms. Editing dimension, speed or mass switches to Custom.
          </p>
        </div>
        <React.Fragment key={fieldsKey}>
          <NumField label="Max characteristic dimension" unit="m" value={dimension} onChange={editCustom(setDimension, dimension)} min={0.05} max={60} form={form} />
          <NumField label="Max speed" unit="m/s" value={speed} onChange={editCustom(setSpeed, speed)} min={0.5} max={250} form={form} />
          <NumField
            label="Take-off mass"
            unit="kg"
            value={mass}
            onChange={editCustom(setMass, mass)}
            min={0.01}
            max={600000}
            optional
            hint="Used for the small-UAS rule (≤ 250 g and ≤ 25 m/s gives iGRC 1)."
            form={form}
          />
        </React.Fragment>
      </ModuleSection>

      <ModuleSection title="Operation">
        <NumField key={fieldsKey} label="Height above ground" unit="m" value={altitude} onChange={setAltitude} min={1} max={1500} form={form} />
        <SwitchField
          label="Controlled ground area"
          checked={controlled}
          onChange={setControlled}
          hint="Only people involved in the operation are in the footprint."
        />
        {file ? (
          <BatchToggle
            kind="ground_risk"
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

      <ModuleSection title="Operational volume">
        <SwitchField
          label="SORA 2.5 operational volume"
          checked={useVolume}
          onChange={setUseVolume}
          hint={
            useVolume
              ? "Flight geography, contingency volume and ground risk buffer; adjacent area and assemblies (Steps 2 and 8)."
              : "Off: the earlier footprint method (the area plus a 1:1 or ballistic buffer, sampled on H3 cells)."
          }
        />
        {useVolume ? (
          <VolumeFields
            value={volume}
            onChange={editVolume}
            adjacentArea={adjacentArea}
            onAdjacentArea={setAdjacentArea}
            gridM={gridM}
            onGridM={setGridM}
            heightAgl={altitude}
            speed={speed}
            dimension={dimension}
            bufferRule={bufferRule}
            presetName={presetName}
            onResetTerms={resetTerms}
            form={form}
            units={units}
            fieldsKey={fieldsKey}
          />
        ) : null}
      </ModuleSection>

      <AssembliesEntry
        entries={assemblies}
        onChange={setAssemblies}
        features={features}
        selectedFeatures={selectedFeatures}
        disabledReason={useVolume ? null : "Assemblies are checked against the operational volume: switch it on to add them."}
      />

      <Advanced form={form}>
        <div className="space-y-1.5">
          <p className="text-sm font-medium" id="gr-buffer-label">
            Ground risk buffer
          </p>
          <ToggleGroup
            type="single"
            value={bufferRule}
            onValueChange={(v) => v && setBufferRule(v as typeof bufferRule)}
            aria-labelledby="gr-buffer-label"
          >
            <ToggleGroupItem value="one_to_one">1:1 (height)</ToggleGroupItem>
            <ToggleGroupItem value="ballistic">Ballistic</ToggleGroupItem>
          </ToggleGroup>
          <p className="text-xs text-fg-muted">
            {useVolume
              ? "1:1: S_GRB = H_CV + c/2. Ballistic: S_GRB = v·√(2·H_CV/g) + c/2 (SORA 2.5 §4.2.4)."
              : "1:1: the height. Ballistic: v·√(2h/g)."}
          </p>
        </div>
        <div className="space-y-1.5">
          <p className="text-sm font-medium" id="gr-stat-label">
            Density statistic
          </p>
          <ToggleGroup
            type="single"
            value={statistic}
            onValueChange={(v) => v && setStatistic(v as typeof statistic)}
            aria-labelledby="gr-stat-label"
          >
            <ToggleGroupItem value="max">Maximum</ToggleGroupItem>
            <ToggleGroupItem value="p95">95th percentile</ToggleGroupItem>
          </ToggleGroup>
          {statistic === "p95" ? (
            <p className="text-xs text-warn">SORA 2.5 Step 2 uses the highest density in the footprint.</p>
          ) : null}
        </div>
        <React.Fragment key={fieldsKey}>
          {useVolume ? null : (
            <NumField label="Grid resolution (H3)" value={resolution} onChange={setResolution} min={6} max={10} step={1} decimals={0} integer form={form} />
          )}
          <NumField
            label="Cell limit"
            value={maxCells}
            onChange={setMaxCells}
            min={100}
            max={200_000}
            step={1000}
            decimals={0}
            integer
            hint={`${
              useVolume
                ? "Most density-grid cells over the footprint: a larger footprint gets coarser cells, which can lower the highest density."
                : "Most H3 cells sampled over the footprint: a larger footprint at a fine resolution is cut."
            } The result says so, and the brief flags a limit below 20,000.`}
            form={form}
          />
        </React.Fragment>
      </Advanced>

      <RunButton onRun={onRun} pending={runner.isPending} disabledReason={disabledReason} label="Assess ground risk" />
      <ResetFormButton onReset={resetForm} />
    </form>
  );
}

function footprintExplanation(result: GroundRiskResult, units: ReturnType<typeof useUnits>): string {
  const v = result.volume;
  if (!v) return `Buffer ${formatLength(result.buffer_m, units)}`;
  return `FG + CV + GRB: ${formatLength(v.s_cv_m + v.grb_m, units)} around the flight geography`;
}

function GroundRiskResults({ analysis }: ModuleResultsProps) {
  const units = useUnits();
  const focus = useFocusMap();
  const result = analysisResult(analysis, "ground_risk");

  const columns = React.useMemo<DataTableColumn<DensityRow>[]>(
    () => [
      { id: "rank", header: "#", numeric: true, width: 48, cell: (_c, i) => i + 1 },
      {
        id: "density",
        header: "Density",
        numeric: true,
        rowHeader: true,
        cell: (c) => `${formatNumber(c.density_per_km2, { maximumFractionDigits: 0 })} /km²`,
        sortValue: (c) => c.density_per_km2,
      },
      { id: "band", header: "Band", cell: (c) => c.band, sortValue: (c) => c.density_per_km2 },
      { id: "where", header: "Location", cell: (c) => <span className="num text-xs">{formatCoord(c.lon, c.lat)}</span> },
    ],
    [],
  );

  if (!analysis) return null;
  if (!result) {
    return analysis.status === "COMPLETED" ? (
      <EmptyState compact icon={<Users />} title="No result" description="The analysis finished without a result." />
    ) : null;
  }

  const igrcText = result.igrc === null ? "Out of scope" : String(result.igrc);
  const volume = result.volume ?? null;
  const grid = result.density_grid ?? null;
  const hotspots = hotspotRows(result);
  const assemblies = result.assemblies ?? [];
  const largeAssemblies = assemblies.filter((a) => a.band === "40k_400k" || a.band === "gt_400k").length;
  const densityLabel = `Density (${result.density_statistic === "p95" ? "95th pct." : "max"})`;
  const densityExplanation = grid
    ? `Highest of ${formatNumber(grid.cells.length)} cells of ${formatLength(grid.grid_m, units)}; mean ${formatNumber(result.mean_density_per_km2, { maximumFractionDigits: 0 })} /km²`
    : `Mean ${formatNumber(result.mean_density_per_km2, { maximumFractionDigits: 0 })} /km²`;

  return (
    <div className="space-y-4">
      {volume ? <EvidenceLabel text={result.evidence_label} /> : null}
      <SyntheticNote synthetic={analysis.datapack_synthetic} />
      <KpiGrid label="Ground risk summary">
        <KpiTile
          label="Intrinsic ground risk class"
          value={igrcText}
          tone={result.igrc === null ? "danger" : result.igrc >= 7 ? "danger" : result.igrc >= 5 ? "warn" : "ok"}
          toneLabel={igrcSeverity(result.igrc, result.small_uas_rule)}
          explanation={`${lookupColumnText(result.column)} · band ${result.density_band}`}
        />
        <KpiTile
          label={densityLabel}
          value={`${formatNumber(result.density_statistic === "p95" ? result.p95_density_per_km2 : result.max_density_per_km2, { maximumFractionDigits: 0 })}`}
          unit="people/km²"
          explanation={densityExplanation}
        />
        <KpiTile label="People in footprint" value={formatNumber(result.population_in_footprint, { maximumFractionDigits: 0 })} explanation="Modelled population" />
        <KpiTile label="Footprint" value={areaParts(result.footprint_area_km2 * 1_000_000, units)} explanation={footprintExplanation(result, units)} />
        {volume ? (
          <>
            <KpiTile
              label="Adjacent area density"
              value={result.adjacent_mean_density_per_km2 == null ? "Not computed" : formatNumber(result.adjacent_mean_density_per_km2, { maximumFractionDigits: 0 })}
              unit={result.adjacent_mean_density_per_km2 == null ? undefined : "people/km²"}
              explanation={`Average out to ${formatLength(volume.adjacent_outer_km * 1000, units)}`}
            />
            <KpiTile
              label="Assemblies within 1 km"
              value={formatNumber(assemblies.length)}
              tone={largeAssemblies > 0 ? "warn" : undefined}
              toneLabel={largeAssemblies > 0 ? "40,000 people or more" : undefined}
              explanation={assemblies.length > 0 ? `Nearest: ${assemblies[0]?.name}` : "Stadiums, markets, parks and your events"}
            />
          </>
        ) : null}
      </KpiGrid>

      {volume ? <VolumeTerms volume={volume} units={units} /> : null}
      {result.igrc_table ? <IgrcGrid table={result.igrc_table} igrc={result.igrc} smallUasRule={result.small_uas_rule} /> : null}
      {volume ? <AdjacentArea result={result} units={units} /> : null}
      {volume ? <Assemblies result={result} units={units} /> : null}

      <ModuleSection title="Hotspots">
        <DataTable
          data={hotspots}
          columns={columns}
          getRowId={(c) => c.id}
          caption={grid ? "Highest-density grid cells in the footprint" : "Highest-density cells in the footprint"}
          onRowClick={(c) => focus(pointBounds(c.lon, c.lat, grid ? Math.max(600, grid.grid_m * 3) : 600))}
          getRowLabel={(c) => `Zoom to cell with ${formatNumber(c.density_per_km2, { maximumFractionDigits: 0 })} people per km²`}
          dense
          emptyState={<EmptyState compact title="No hotspots" description="No populated cells in the footprint." />}
        />
      </ModuleSection>

      <ModuleSection title="Method">
        <p className="text-sm">{result.method}</p>
        <p className="text-xs text-fg-muted">{result.table_reference}</p>
        {result.cells_truncated ? (
          <p className="text-xs text-warn">
            {grid
              ? `The density grid was coarsened to ${lengthParts(grid.grid_m, units).text} cells to stay within the cell limit.`
              : "The density grid was truncated to the cell limit."}
          </p>
        ) : null}
      </ModuleSection>

      <IssueList issues={result.issues} />
      <Disclaimer text={result.disclaimer} />
    </div>
  );
}

export const definition: ModuleDefinition = withBatch({
  ...MODULE_META["ground-risk"],
  Panel: GroundRiskPanel,
  Results: GroundRiskResults,
  layers: LAYERS,
  buildLayers: buildGroundRiskLayers,
  describePick: describeGroundRiskPick,
  derivedLayers: groundRiskDerivedLayers,
});

export default definition;
