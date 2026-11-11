"use client";

/**
 * Line-of-sight panel: observers (typed coordinates or a click on the map), target height, radio
 * band and clearance, VLOS guidance inputs and the grid. The form keeps its values after a run,
 * so moving one observer and running again is quick.
 */

import * as React from "react";
import { Crosshair, MapPin, Plus, Trash2 } from "lucide-react";
import { formatCoord, formatHeight, formatNumber } from "@/lib/format";
import { cancelMapPick, cancelMapPickToStart, startMapPick, useMapPick } from "@/lib/map-pick";
import type { ModulePanelProps } from "@/lib/modules";
import { usePlan } from "@/lib/plan-store";
import { useUnits } from "@/lib/store";
import type { LineOfSightParams, ObserverInput, ObserverRole, RadioTargetInput } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Field } from "@/components/ui/field";
import { IconButton } from "@/components/ui/icon-button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAnalysisRunner } from "@/components/workspace/analysis-runner";
import { announce } from "@/components/workspace/live-region";
import {
  Advanced,
  FormErrorSummary,
  InputHint,
  ModuleSection,
  NumField,
  RunButton,
  SwitchField,
  useFormErrors,
  type FormErrors,
} from "../shared";
import { ResetFormButton, paramBoolean, paramChoice, paramNumber, useFormFromAnalysis } from "../analysis-form";
import { BatchToggle, batchDisabledReason } from "../batch";
import { batchFromParams, batchParams } from "@/lib/batch";
import {
  AIRCRAFT,
  DEFAULTS,
  EVIDENCE_NOTE,
  FREQUENCY_PRESETS,
  MAX_OBSERVERS,
  RADIO_NOTE,
  ROLES,
  ROLE_HEIGHT_M,
  ROLE_LABEL,
  ROLE_SHORT,
  TARGET_PRESETS,
  datasetObservers,
  newRowKey,
  observerRole,
  operationFeatureCount,
  type ObserverDraft,
} from "./model";

type Params = Readonly<Record<string, unknown>>;

interface RadioTargetDraft {
  name: string;
  lon: number | null;
  lat: number | null;
  height: number | null;
}

const EMPTY_TARGET: RadioTargetDraft = { name: "", lon: null, lat: null, height: null };
const RADIO_TARGET_PICK_ID = "radio-target";

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Observers stored with an analysis, as panel rows (malformed entries are skipped). */
function observersFromParams(value: unknown): ObserverDraft[] {
  if (!Array.isArray(value)) return [];
  const rows: ObserverDraft[] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    const role = observerRole(null, { role: item.role }) ?? "pilot";
    rows.push({
      key: newRowKey(),
      name: typeof item.name === "string" ? item.name : ROLE_SHORT[role],
      role,
      lon: paramNumber(item, "lon", null),
      lat: paramNumber(item, "lat", null),
      height: paramNumber(item, "height_agl_m", null),
    });
  }
  return rows.slice(0, MAX_OBSERVERS);
}

function targetFromParams(value: unknown): RadioTargetDraft | null {
  if (!isRecord(value)) return null;
  return {
    name: typeof value.name === "string" ? value.name : "",
    lon: paramNumber(value, "lon", null),
    lat: paramNumber(value, "lat", null),
    height: paramNumber(value, "height_agl_m", null),
  };
}

/** Name sent for a row left unnamed: "Pilot", "Observer 2", "GCS"… */
function defaultName(rows: readonly ObserverDraft[], index: number): string {
  const row = rows[index] as ObserverDraft;
  const sameRole = rows.filter((r) => r.role === row.role);
  const nth = sameRole.indexOf(row) + 1;
  return sameRole.length > 1 ? `${ROLE_SHORT[row.role]} ${nth}` : ROLE_SHORT[row.role];
}

/** Ends a pending map pick when the panel goes away, on Escape, and when plan drawing starts. */
function useMapPickLifecycle() {
  const pending = useMapPick((s) => s.request !== null);
  const planDrawing = usePlan((s) => s.drawing);
  React.useEffect(() => () => cancelMapPick(), []);
  React.useEffect(() => {
    if (planDrawing) cancelMapPick();
  }, [planDrawing]);
  React.useEffect(() => {
    if (!pending) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      e.preventDefault();
      e.stopPropagation();
      cancelMapPickToStart();
      announce("Placing on the map cancelled.");
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [pending]);
  return pending;
}

function PlaceOnMapButton({ id, label, onPlaced }: { id: string; label: string; onPlaced: (lon: number, lat: number) => void }) {
  const active = useMapPick((s) => s.request?.id === id);
  const onPlacedRef = React.useRef(onPlaced);
  onPlacedRef.current = onPlaced;
  const buttonRef = React.useRef<HTMLButtonElement>(null);
  return (
    <Button
      ref={buttonRef}
      type="button"
      variant={active ? "secondary" : "outline"}
      size="sm"
      aria-pressed={active}
      onClick={() => {
        if (active) {
          cancelMapPick();
          announce("Placing on the map cancelled.");
          return;
        }
        usePlan.getState().cancelDrawing();
        startMapPick({
          id,
          label,
          onPick: ([lon, lat]) => {
            onPlacedRef.current(lon, lat);
            announce(`${label} placed at ${formatCoord(lon, lat)}.`);
          },
          returnFocus: buttonRef.current,
        });
        announce(`Click the map to place ${label}, or press Space to place it at the centre of the focused map. Escape cancels.`);
      }}
    >
      <Crosshair aria-hidden />
      {active ? "Click the map… (Esc cancels)" : "Place on map"}
    </Button>
  );
}

function ObserverRow({
  row,
  index,
  shownName,
  form,
  fieldsKey,
  onChange,
  onRemove,
}: {
  row: ObserverDraft;
  index: number;
  shownName: string;
  form: FormErrors;
  fieldsKey: number;
  onChange: (key: string, patch: Partial<ObserverDraft>) => void;
  onRemove: (key: string) => void;
}) {
  const units = useUnits();
  const legendId = React.useId();
  const roleId = React.useId();
  const set = (patch: Partial<ObserverDraft>) => onChange(row.key, patch);
  return (
    <div role="group" aria-labelledby={legendId} className="space-y-3 rounded border border-border p-3" data-observer-row={index + 1}>
      <div className="flex items-center justify-between gap-2">
        <p id={legendId} className="flex items-center gap-1.5 text-sm font-medium">
          <MapPin aria-hidden className="size-3.5 text-accent" />
          Observer {index + 1}: {shownName}
        </p>
        <IconButton label={`Remove ${shownName}`} icon={<Trash2 />} size="icon-sm" onClick={() => onRemove(row.key)} />
      </div>
      <Field label="Name" hint={row.name.trim() ? undefined : `Empty: "${shownName}"`}>
        {(p) => (
          <Input
            {...p}
            data-observer-name={row.key}
            value={row.name}
            maxLength={80}
            placeholder={shownName}
            onChange={(e) => set({ name: e.target.value })}
          />
        )}
      </Field>
      <div className="space-y-1.5">
        <Label htmlFor={roleId}>Role</Label>
        <Select value={row.role} onValueChange={(v) => set({ role: v as ObserverRole })}>
          <SelectTrigger id={roleId}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ROLES.map((r) => (
              <SelectItem key={r} value={r}>
                {ROLE_LABEL[r]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <React.Fragment key={fieldsKey}>
        <div className="grid grid-cols-2 gap-2">
          <NumField label="Latitude" unit="°" value={row.lat} onChange={(v) => set({ lat: v })} min={-90} max={90} step={0.0001} form={form} />
          <NumField label="Longitude" unit="°" value={row.lon} onChange={(v) => set({ lon: v })} min={-180} max={180} step={0.0001} form={form} />
        </div>
        <NumField
          label={row.role === "gcs" ? "Antenna height above ground" : "Eye height above ground"}
          unit="m"
          value={row.height}
          onChange={(v) => set({ height: v })}
          min={0}
          max={500}
          step={0.1}
          optional
          hint={`Empty: ${formatHeight(ROLE_HEIGHT_M[row.role], units)}.`}
          form={form}
        />
      </React.Fragment>
      <PlaceOnMapButton id={`observer-${row.key}`} label={shownName} onPlaced={(lon, lat) => set({ lon, lat })} />
    </div>
  );
}

export function LineOfSightPanel({ datasetId, file, features, selectedFeatures, analysis }: ModulePanelProps) {
  const runner = useAnalysisRunner();
  const form = useFormErrors();
  const units = useUnits();
  const picking = useMapPickLifecycle();

  const detected = React.useMemo(() => datasetObservers(features), [features]);

  const [observers, setObservers] = React.useState<ObserverDraft[]>([]);
  const [target, setTarget] = React.useState<number | null>(DEFAULTS.target);
  const [radioCheck, setRadioCheck] = React.useState<boolean>(DEFAULTS.radioCheck);
  const [frequency, setFrequency] = React.useState<number | null>(DEFAULTS.frequency);
  const [clearance, setClearance] = React.useState<number | null>(DEFAULTS.clearance);
  const [useRadioTarget, setUseRadioTarget] = React.useState(false);
  const [radioTarget, setRadioTarget] = React.useState<RadioTargetDraft>(EMPTY_TARGET);
  const [aircraft, setAircraft] = React.useState<(typeof AIRCRAFT)[number]>(DEFAULTS.aircraft);
  const [dimension, setDimension] = React.useState<number | null>(null);
  const [visibility, setVisibility] = React.useState<number | null>(DEFAULTS.visibility);
  const [grid, setGrid] = React.useState<number | null>(DEFAULTS.grid);
  const [maxRange, setMaxRange] = React.useState<number | null>(null);
  const [includeObstacles, setIncludeObstacles] = React.useState<boolean>(DEFAULTS.includeObstacles);
  const [kVisible, setKVisible] = React.useState<number | null>(DEFAULTS.kVisible);
  const [kRadio, setKRadio] = React.useState<number | null>(DEFAULTS.kRadio);
  const [batch, setBatch] = React.useState(false);
  const [keyProperty, setKeyProperty] = React.useState<string | null>(null);

  // Observers come from the dataset (points named pilot, observer or GCS) until the user edits
  // them or an analysis puts its own back; another dataset brings its own again.
  const observersSet = React.useRef(false);
  const detectedRef = React.useRef(detected);
  detectedRef.current = detected;

  const seededFor = React.useRef<string | null>(null);

  const applyParams = (p: Params) => {
    // These observers belong to this dataset: the dataset-change seeding below must keep them.
    observersSet.current = true;
    seededFor.current = datasetId;
    setObservers(Array.isArray(p.observers) ? observersFromParams(p.observers) : detectedRef.current.map((o) => ({ ...o, key: newRowKey() })));
    setTarget(paramNumber(p, "target_height_agl_m", DEFAULTS.target));
    setRadioCheck(paramBoolean(p, "radio_check", DEFAULTS.radioCheck));
    setFrequency(paramNumber(p, "frequency_mhz", DEFAULTS.frequency));
    setClearance(paramNumber(p, "fresnel_clearance_pct", DEFAULTS.clearance));
    const storedTarget = targetFromParams(p.radio_target);
    setUseRadioTarget(storedTarget !== null);
    setRadioTarget(storedTarget ?? EMPTY_TARGET);
    setAircraft(paramChoice(p, "vlos_aircraft", AIRCRAFT, DEFAULTS.aircraft));
    setDimension(paramNumber(p, "characteristic_dimension_m", null));
    setVisibility(paramNumber(p, "ground_visibility_m", DEFAULTS.visibility));
    setGrid(paramNumber(p, "grid_m", DEFAULTS.grid));
    setMaxRange(paramNumber(p, "max_range_m", null));
    setIncludeObstacles(paramBoolean(p, "include_obstacles", DEFAULTS.includeObstacles));
    setKVisible(paramNumber(p, "k_visible", DEFAULTS.kVisible));
    setKRadio(paramNumber(p, "k_radio", DEFAULTS.kRadio));
    const b = batchFromParams(p);
    setBatch(b.on);
    setKeyProperty(b.keyProperty);
  };
  const { markStarted, setFields, fieldsKey } = useFormFromAnalysis(analysis, applyParams);

  React.useEffect(() => {
    if (datasetId !== seededFor.current && seededFor.current !== null) observersSet.current = false;
    seededFor.current = datasetId;
    if (observersSet.current || !features) return;
    setObservers(detected.map((o) => ({ ...o, key: newRowKey() })));
  }, [datasetId, features, detected]);

  const updateObserver = React.useCallback((key: string, patch: Partial<ObserverDraft>) => {
    observersSet.current = true;
    setObservers((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }, []);
  // Focus after the rows re-render: a new row's name field; after a removal, "Add observer" (the
  // pressed button is gone, and "Add observer" may only now be enabled again).
  const formRef = React.useRef<HTMLFormElement>(null);
  const addButtonRef = React.useRef<HTMLButtonElement>(null);
  const pendingFocus = React.useRef<string | "add" | null>(null);
  React.useEffect(() => {
    const target = pendingFocus.current;
    if (target === null) return;
    pendingFocus.current = null;
    if (target === "add") addButtonRef.current?.focus();
    else formRef.current?.querySelector<HTMLInputElement>(`[data-observer-name="${target}"]`)?.focus();
  }, [observers]);
  const removeObserver = (key: string) => {
    const index = observers.findIndex((r) => r.key === key);
    if (index < 0) return;
    observersSet.current = true;
    cancelMapPick();
    announce(`${observers[index]?.name.trim() || defaultName(observers, index)} removed.`);
    pendingFocus.current = "add";
    setObservers((rows) => rows.filter((r) => r.key !== key));
  };
  const addObserver = () => {
    if (observers.length >= MAX_OBSERVERS) return;
    observersSet.current = true;
    const key = newRowKey();
    const role: ObserverRole = observers.some((r) => r.role === "pilot") ? "observer" : "pilot";
    pendingFocus.current = key;
    setObservers((rows) => (rows.length >= MAX_OBSERVERS ? rows : [...rows, { key, name: "", role, lon: null, lat: null, height: null }]));
    announce(`${ROLE_LABEL[role]} added: type its coordinates or place it on the map.`);
  };

  const operationCount = React.useMemo(() => operationFeatureCount(features, selectedFeatures), [features, selectedFeatures]);
  const ready = file?.status === "COMPLETED";
  const disabledReason = !file
    ? "Draw, pin or open a dataset first: line of sight runs on its area, route or points."
    : !ready
      ? "Wait for the dataset to finish processing."
      : features && operationCount === 0
        ? "The dataset has only observer points: add the operation area or route."
        : batchDisabledReason("line_of_sight", batch, operationCount);

  const onRun = form.guard(() => {
    cancelMapPick();
    const sent: ObserverInput[] = observers.map((o, i) => ({
      name: o.name.trim() || defaultName(observers, i),
      role: o.role,
      lon: o.lon as number,
      lat: o.lat as number,
      ...(o.height !== null ? { height_agl_m: o.height } : {}),
    }));
    const targetInput: RadioTargetInput | null =
      radioCheck && useRadioTarget
        ? {
            name: radioTarget.name.trim() || "Radio target",
            lon: radioTarget.lon as number,
            lat: radioTarget.lat as number,
            ...(radioTarget.height !== null ? { height_agl_m: radioTarget.height } : {}),
          }
        : null;
    const params: Partial<LineOfSightParams> = {
      // Untouched while the features still load: let the API read the observers from the dataset.
      ...(observersSet.current || features ? { observers: sent } : {}),
      target_height_agl_m: target ?? DEFAULTS.target,
      grid_m: grid ?? DEFAULTS.grid,
      max_range_m: maxRange,
      k_visible: kVisible ?? DEFAULTS.kVisible,
      k_radio: kRadio ?? DEFAULTS.kRadio,
      radio_check: radioCheck,
      frequency_mhz: frequency ?? DEFAULTS.frequency,
      fresnel_clearance_pct: clearance ?? DEFAULTS.clearance,
      radio_target: targetInput,
      include_obstacles: includeObstacles,
      vlos_aircraft: aircraft,
      characteristic_dimension_m: dimension,
      ground_visibility_m: visibility ?? DEFAULTS.visibility,
      ...batchParams(batch, keyProperty),
    };
    void runner.run("line_of_sight", params).then((info) => {
      if (info) markStarted(info.id);
    });
  });

  const scope =
    selectedFeatures.length > 0
      ? `the ${selectedFeatures.length} selected feature${selectedFeatures.length === 1 ? "" : "s"}`
      : "every feature of the dataset";
  const fromDataset = detected.length;

  return (
    <form ref={formRef} className="space-y-4 text-sm" onSubmit={(e) => e.preventDefault()} aria-label="Line of sight">
      {/* While placing on the map, the map shows a crosshair (workspace-map.tsx); announce() speaks it. */}
      {picking ? (
        <p className="rounded border border-accent/60 bg-accent/10 px-2 py-1.5 text-xs">
          Click the map to place the point, or press Space to place it at the centre of the focused map (arrow keys pan). Escape
          cancels.
        </p>
      ) : null}
      <InputHint>
        Checks {scope} (observer points excluded) at the target height. {EVIDENCE_NOTE}
      </InputHint>
      <FormErrorSummary form={form} />

      <ModuleSection
        title={`Pilot, observers and ground station (${observers.length})`}
        aside={
          fromDataset > 0 ? (
            <span className="text-xs text-fg-muted">
              {formatNumber(fromDataset)} in the dataset
            </span>
          ) : null
        }
      >
        {observers.length === 0 ? (
          <p className="text-xs text-fg-muted">
            No observer yet: the pilot is then assumed at the start of the route or the centre of the area, with a{" "}
            {formatHeight(ROLE_HEIGHT_M.pilot, units)} eye height. Points named &quot;Pilot&quot;, &quot;Observer&quot; or &quot;GCS&quot; in a dataset are
            listed here automatically.
          </p>
        ) : null}
        <div className="space-y-3">
          {observers.map((row, i) => (
            <ObserverRow
              key={row.key}
              row={row}
              index={i}
              shownName={row.name.trim() || defaultName(observers, i)}
              form={form}
              fieldsKey={fieldsKey}
              onChange={updateObserver}
              onRemove={removeObserver}
            />
          ))}
        </div>
        <Button ref={addButtonRef} type="button" variant="outline" size="sm" className="w-full" onClick={addObserver} disabled={observers.length >= MAX_OBSERVERS}>
          <Plus aria-hidden />
          Add observer
        </Button>
        {observers.length >= MAX_OBSERVERS ? <p className="text-xs text-fg-muted">At most {MAX_OBSERVERS} observers per run.</p> : null}
      </ModuleSection>

      <ModuleSection title="Aircraft">
        <NumField
          key={`target-${fieldsKey}`}
          label="Target height above ground"
          unit="m"
          value={target}
          onChange={setTarget}
          min={0}
          max={1500}
          hint="Height of the aircraft above the ground under it: a cell counts as visible when a line from the eye reaches this height."
          presets={{ metric: TARGET_PRESETS, label: "Target height presets" }}
          form={form}
        />
      </ModuleSection>

      {file ? (
        <ModuleSection title="Features">
          <BatchToggle
            kind="line_of_sight"
            on={batch}
            onChange={setBatch}
            keyProperty={keyProperty}
            onKeyProperty={setKeyProperty}
            features={features}
            count={operationCount}
            selection={selectedFeatures.length > 0}
          />
        </ModuleSection>
      ) : null}

      <ModuleSection title="Radio path">
        <SwitchField
          label="Radio path check"
          checked={radioCheck}
          onChange={setRadioCheck}
          hint={`First Fresnel zone clearance and free-space loss from the ground station (else the pilot). ${RADIO_NOTE}`}
        />
        {radioCheck ? (
          <React.Fragment key={`radio-${fieldsKey}`}>
            <NumField
              label="Frequency"
              unit="MHz"
              value={frequency}
              onChange={setFrequency}
              min={30}
              max={100000}
              presets={{ metric: FREQUENCY_PRESETS, label: "Frequency presets" }}
              hint="Common C2 bands: 433, 868, 915 MHz, 2.4 and 5.8 GHz."
              form={form}
            />
            <NumField
              label="Required Fresnel clearance"
              unit="%"
              value={clearance}
              onChange={setClearance}
              min={0}
              max={100}
              hint="Share of the first Fresnel zone radius kept clear; 60 % is the usual rule of thumb."
              form={form}
            />
            <SwitchField
              label="Fixed radio target"
              checked={useRadioTarget}
              onChange={setUseRadioTarget}
              hint="A relay mast or far point. Off: paths go to each route point, or to the farthest corner of the area."
            />
            {useRadioTarget ? (
              <fieldset className="space-y-3 rounded border border-border p-3" aria-label="Radio target">
                <Field label="Target name">
                  {(p) => (
                    <Input
                      {...p}
                      value={radioTarget.name}
                      maxLength={80}
                      placeholder="Radio target"
                      onChange={(e) => setRadioTarget((t) => ({ ...t, name: e.target.value }))}
                    />
                  )}
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <NumField label="Latitude" unit="°" value={radioTarget.lat} onChange={(v) => setRadioTarget((t) => ({ ...t, lat: v }))} min={-90} max={90} step={0.0001} form={form} />
                  <NumField label="Longitude" unit="°" value={radioTarget.lon} onChange={(v) => setRadioTarget((t) => ({ ...t, lon: v }))} min={-180} max={180} step={0.0001} form={form} />
                </div>
                <NumField
                  label="Antenna height above ground"
                  unit="m"
                  value={radioTarget.height}
                  onChange={(v) => setRadioTarget((t) => ({ ...t, height: v }))}
                  min={0}
                  max={1500}
                  optional
                  hint="Empty: the target height of the aircraft."
                  form={form}
                />
                <PlaceOnMapButton
                  id={RADIO_TARGET_PICK_ID}
                  label={radioTarget.name.trim() || "the radio target"}
                  onPlaced={(lon, lat) => setRadioTarget((t) => ({ ...t, lon, lat }))}
                />
              </fieldset>
            ) : null}
          </React.Fragment>
        ) : null}
      </ModuleSection>

      <Advanced form={form}>
        <ModuleSection title="VLOS guidance (non-regulatory)">
          <div className="space-y-1.5">
            <p className="text-sm font-medium" id="los-aircraft">
              Aircraft type
            </p>
            <ToggleGroup type="single" value={aircraft} onValueChange={(v) => v && setAircraft(v as typeof aircraft)} aria-labelledby="los-aircraft">
              <ToggleGroupItem value="multirotor">Multirotor</ToggleGroupItem>
              <ToggleGroupItem value="fixed_wing">Fixed wing</ToggleGroupItem>
            </ToggleGroup>
          </div>
          <React.Fragment key={`vlos-${fieldsKey}`}>
            <NumField
              label="Characteristic dimension"
              unit="m"
              value={dimension}
              onChange={setDimension}
              min={0.01}
              max={30}
              step={0.05}
              optional
              hint="Largest span. Given: EASA's ALOS/DLOS estimate; empty: the commonly cited 500 m (UK CAA)."
              form={form}
            />
            <NumField label="Ground visibility" unit="m" value={visibility} onChange={setVisibility} min={500} max={100000} hint="Used for EASA's DLOS = 0.3 × visibility." form={form} />
          </React.Fragment>
        </ModuleSection>
        <ModuleSection title="Grid and terrain">
          <React.Fragment key={`grid-${fieldsKey}`}>
            <NumField label="Grid cell size" unit="m" value={grid} onChange={setGrid} min={5} max={500} hint="Coarsened automatically to stay within 400 × 400 cells." form={form} />
            <NumField label="Maximum range" unit="m" value={maxRange} onChange={setMaxRange} min={1} max={50000} optional hint="Empty: no limit. Cells farther than this from an observer count as not visible from it." form={form} />
            <NumField label="Earth-radius factor k (visual)" value={kVisible} onChange={setKVisible} min={0.5} max={100} step={0.01} decimals={4} hint="7/6 ≈ 1.1667 for light (GDAL's 0.85714 curvature coefficient)." form={form} />
            <NumField label="Earth-radius factor k (radio)" value={kRadio} onChange={setKRadio} min={0.5} max={100} step={0.01} decimals={4} hint="4/3 ≈ 1.3333, standard atmosphere." form={form} />
          </React.Fragment>
          <SwitchField
            label="Data-pack obstacles"
            checked={includeObstacles}
            onChange={setIncludeObstacles}
            hint="Raise each mapped obstacle onto the terrain in its cell and on radio paths."
          />
        </ModuleSection>
      </Advanced>

      {runner.error ? (
        <ErrorState compact error={runner.error} title="Line of sight could not start" onRetry={disabledReason ? undefined : onRun} retrying={runner.isPending} />
      ) : null}
      <RunButton onRun={onRun} pending={runner.isPending} disabledReason={disabledReason} label="Check line of sight" />
      <ResetFormButton
        onReset={() => {
          cancelMapPick();
          setFields({});
        }}
      />
    </form>
  );
}
