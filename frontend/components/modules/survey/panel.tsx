"use client";

/**
 * Survey panel: a mapping survey (camera, height or target GSD read above ground, above
 * take-off or AMSL, overlaps) or a spraying job (swath, application rate, tank and pump), over
 * an area or along a corridor centreline, with the battery reserve, transit legs from the
 * take-off point, ground control points and image storage. It estimates effort only: no flight
 * plan or waypoints are generated (mission planning belongs to UAOP).
 */

import * as React from "react";
import { Trash2 } from "lucide-react";
import { formatHeight } from "@/lib/format";
import { CAMERA_KIND_LABEL } from "@/lib/labels";
import type { ModulePanelProps } from "@/lib/modules";
import { useMeta } from "@/lib/queries";
import { useUnits } from "@/lib/store";
import type { CameraKind, CameraSpec, DatasetFeatureCollection, HeightReference, SurveyParams } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAnalysisRunner } from "@/components/workspace/analysis-runner";
import { announce } from "@/components/workspace/live-region";
import { PlanStartHint } from "@/components/workspace/plan-start";
import {
  Advanced,
  DEFAULT_HEIGHT_REFERENCE,
  FormErrorSummary,
  HeightReferenceField,
  InputHint,
  ModuleSection,
  NumField,
  RunButton,
  SwitchField,
  TakeoffFields,
  countPolygons,
  heightReferenceFromParams,
  heightReferenceParams,
  useFormErrors,
  type HeightReferenceState,
} from "../shared";
import { ResetFormButton, paramBoolean, paramChoice, paramNumber, useFormFromAnalysis } from "../analysis-form";
import { BatchToggle, batchDisabledReason } from "../batch";
import { batchFeatureCount, batchFromParams, batchParams } from "@/lib/batch";
import { useSavedCameras } from "./saved-cameras";
import {
  COVERAGES,
  CUSTOM,
  CUSTOM_CAMERA_NAME,
  DEFAULTS,
  DEFAULT_CAMERA,
  DEFAULT_CUSTOM_CAMERA,
  ESTIMATE_NOTE,
  MODES,
  ORIENTATIONS,
  SPRAY_DEFAULTS,
  SPRAY_HEIGHT_PRESETS,
  SPRAY_NOTE,
  heightForGsd,
} from "./model";

type Params = Readonly<Record<string, unknown>>;

const SAVED_PREFIX = "saved:";
const CAMERA_KINDS: readonly CameraKind[] = ["rgb", "thermal", "multispectral"];

const HEIGHT_FIELD_LABEL: Record<HeightReference, string> = {
  agl: "Height above ground",
  takeoff: "Height above take-off",
  amsl: "Flight level (AMSL)",
};

/** A custom camera stored with an analysis, completed with the defaults where a value is missing. */
function cameraFromParams(value: Record<string, unknown>): CameraSpec {
  const n = (key: "sensor_width_mm" | "sensor_height_mm" | "focal_length_mm" | "image_width_px" | "image_height_px") =>
    paramNumber(value, key, null) ?? DEFAULT_CUSTOM_CAMERA[key];
  return {
    name: typeof value.name === "string" ? value.name : CUSTOM_CAMERA_NAME,
    sensor_width_mm: n("sensor_width_mm"),
    sensor_height_mm: n("sensor_height_mm"),
    focal_length_mm: n("focal_length_mm"),
    image_width_px: n("image_width_px"),
    image_height_px: n("image_height_px"),
    kind: paramChoice(value, "kind", CAMERA_KINDS, "rgb"),
    images_per_capture: paramNumber(value, "images_per_capture", 1) ?? 1,
    photo_mb: paramNumber(value, "photo_mb", null),
  };
}

/** Line features in the selection (or the whole dataset when nothing is selected). */
function countLines(features: DatasetFeatureCollection | null, selected: readonly number[]): number {
  if (!features) return 0;
  const wanted = selected.length > 0 ? new Set(selected) : null;
  return features.features.filter(
    (f) => (!wanted || wanted.has(f.properties._index)) && f.geometry != null && /LineString$/.test(f.geometry.type),
  ).length;
}

export function SurveyPanel({ file, features, selectedFeatures, analysis }: ModulePanelProps) {
  const runner = useAnalysisRunner();
  const form = useFormErrors();
  const meta = useMeta();
  const units = useUnits();
  const cameras = React.useMemo(() => meta.data?.cameras ?? {}, [meta.data]);
  const saved = useSavedCameras();

  const [jobMode, setJobMode] = React.useState<(typeof MODES)[number]>("mapping");
  const [coverage, setCoverage] = React.useState<(typeof COVERAGES)[number]>("area");
  const [corridorWidth, setCorridorWidth] = React.useState<number | null>(DEFAULTS.corridorWidth);
  const [cameraId, setCameraId] = React.useState<string>(DEFAULT_CAMERA);
  const [custom, setCustom] = React.useState<CameraSpec>(DEFAULT_CUSTOM_CAMERA);
  const [mode, setMode] = React.useState<"altitude" | "gsd">("altitude");
  const [heights, setHeights] = React.useState<HeightReferenceState>(DEFAULT_HEIGHT_REFERENCE);
  const [altitude, setAltitude] = React.useState<number | null>(DEFAULTS.altitude);
  const [gsd, setGsd] = React.useState<number | null>(DEFAULTS.gsd);
  const [front, setFront] = React.useState<number | null>(DEFAULTS.front);
  const [side, setSide] = React.useState<number | null>(DEFAULTS.side);
  const [speed, setSpeed] = React.useState<number | null>(DEFAULTS.speed);
  const [battery, setBattery] = React.useState<number | null>(DEFAULTS.battery);
  const [reserve, setReserve] = React.useState<number | null>(DEFAULTS.reserve);
  const [transitLegs, setTransitLegs] = React.useState<boolean>(false);
  const [transitSpeed, setTransitSpeed] = React.useState<number | null>(null);
  const [swap, setSwap] = React.useState<number | null>(DEFAULTS.swap);
  const [turn, setTurn] = React.useState<number | null>(DEFAULTS.turn);
  const [minTrigger, setMinTrigger] = React.useState<number | null>(DEFAULTS.minTrigger);
  const [maxAltitude, setMaxAltitude] = React.useState<number | null>(DEFAULTS.maxAltitude);
  const [autoDirection, setAutoDirection] = React.useState(true);
  const [direction, setDirection] = React.useState<number | null>(DEFAULTS.direction);
  const [orientation, setOrientation] = React.useState<(typeof ORIENTATIONS)[number]>("landscape");
  const [gcpMin, setGcpMin] = React.useState<number | null>(DEFAULTS.gcpMin);
  const [gcpHa, setGcpHa] = React.useState<number | null>(DEFAULTS.gcpHaPerExtra);
  const [gcpSpacing, setGcpSpacing] = React.useState<number | null>(DEFAULTS.gcpSpacing);
  const [photoMb, setPhotoMb] = React.useState<number | null>(null);
  const [spray, setSpray] = React.useState<Record<keyof typeof SPRAY_DEFAULTS, number | null>>({ ...SPRAY_DEFAULTS });
  const [batch, setBatch] = React.useState(false);
  const [keyProperty, setKeyProperty] = React.useState<string | null>(null);

  const applyParams = (p: Params) => {
    const b = batchFromParams(p);
    setBatch(b.on);
    setKeyProperty(b.keyProperty);
    setJobMode(paramChoice(p, "mode", MODES, "mapping"));
    setCoverage(paramChoice(p, "coverage", COVERAGES, "area"));
    setCorridorWidth(paramNumber(p, "corridor_width_m", DEFAULTS.corridorWidth));
    const camera = p.camera;
    if (camera !== null && typeof camera === "object") {
      setCameraId(CUSTOM);
      setCustom(cameraFromParams(camera as Record<string, unknown>));
    } else {
      setCameraId(typeof camera === "string" ? camera : DEFAULT_CAMERA);
      setCustom(DEFAULT_CUSTOM_CAMERA);
    }
    const targetGsd = paramNumber(p, "target_gsd_cm", null);
    setMode(targetGsd !== null ? "gsd" : "altitude");
    setGsd(targetGsd ?? DEFAULTS.gsd);
    setHeights(heightReferenceFromParams(p));
    setAltitude(paramNumber(p, "altitude_agl_m", DEFAULTS.altitude));
    setFront(paramNumber(p, "front_overlap_pct", DEFAULTS.front));
    setSide(paramNumber(p, "side_overlap_pct", DEFAULTS.side));
    setSpeed(paramNumber(p, "speed_ms", DEFAULTS.speed));
    setBattery(paramNumber(p, "battery_minutes", DEFAULTS.battery));
    setReserve(paramNumber(p, "battery_reserve_pct", DEFAULTS.reserve));
    setTransitLegs(paramBoolean(p, "transit_legs", false));
    setTransitSpeed(paramNumber(p, "transit_speed_ms", null));
    setSwap(paramNumber(p, "battery_swap_minutes", DEFAULTS.swap));
    setTurn(paramNumber(p, "turn_seconds", DEFAULTS.turn));
    setMinTrigger(paramNumber(p, "min_trigger_interval_s", DEFAULTS.minTrigger));
    setMaxAltitude(paramNumber(p, "max_altitude_agl_m", DEFAULTS.maxAltitude));
    const lineDirection = paramNumber(p, "direction_deg", null);
    setAutoDirection(lineDirection === null);
    setDirection(lineDirection ?? DEFAULTS.direction);
    setOrientation(paramChoice(p, "orientation", ORIENTATIONS, "landscape"));
    setGcpMin(paramNumber(p, "gcp_min", DEFAULTS.gcpMin));
    setGcpHa(paramNumber(p, "gcp_ha_per_extra", DEFAULTS.gcpHaPerExtra));
    setGcpSpacing(paramNumber(p, "gcp_corridor_spacing_m", DEFAULTS.gcpSpacing));
    setPhotoMb(paramNumber(p, "photo_mb", null));
    const s = p.spray !== null && typeof p.spray === "object" ? (p.spray as Params) : {};
    setSpray({
      swath_m: paramNumber(s, "swath_m", SPRAY_DEFAULTS.swath_m),
      rate_l_per_ha: paramNumber(s, "rate_l_per_ha", SPRAY_DEFAULTS.rate_l_per_ha),
      tank_l: paramNumber(s, "tank_l", SPRAY_DEFAULTS.tank_l),
      flow_l_per_min: paramNumber(s, "flow_l_per_min", SPRAY_DEFAULTS.flow_l_per_min),
      speed_ms: paramNumber(s, "speed_ms", SPRAY_DEFAULTS.speed_ms),
      turn_seconds: paramNumber(s, "turn_seconds", SPRAY_DEFAULTS.turn_seconds),
      refill_minutes: paramNumber(s, "refill_minutes", SPRAY_DEFAULTS.refill_minutes),
      height_m: paramNumber(s, "height_m", SPRAY_DEFAULTS.height_m),
    });
  };
  const { markStarted, setFields, fieldsKey } = useFormFromAnalysis(analysis, applyParams);

  const spraying = jobMode === "spraying";
  const corridor = coverage === "corridor";
  const polygonCount = React.useMemo(() => countPolygons(features, selectedFeatures), [features, selectedFeatures]);
  const lineCount = React.useMemo(() => countLines(features, selectedFeatures), [features, selectedFeatures]);

  const ready = file?.status === "COMPLETED";
  const batchCount = batchFeatureCount(features, selectedFeatures);
  const disabledReason = !file
    ? corridor
      ? "Draw or paste the corridor centreline, or open a dataset with the line."
      : "Draw or paste the survey area, or open a dataset with its polygon."
    : !ready
      ? "Wait for the dataset to finish processing."
      : corridor
        ? lineCount === 0
          ? "Corridor mapping needs a line: select the centreline (a line feature), or switch to Area."
          : batchDisabledReason("survey", batch, batchCount)
        : polygonCount === 0
          ? "The survey needs a polygon: select an area feature, or switch to Corridor for a line."
          : batchDisabledReason("survey", batch, batchCount);

  const savedCamera = cameraId.startsWith(SAVED_PREFIX) ? (saved.cameras.find((c) => `${SAVED_PREFIX}${c.name}` === cameraId) ?? null) : null;
  const selectedCamera: CameraSpec | null = cameraId === CUSTOM ? custom : (savedCamera ?? cameras[cameraId] ?? null);

  const reference = heights.reference;
  const amsl = reference === "amsl";
  const takeoffParam: [number, number] | null =
    heights.takeoffLon !== null && heights.takeoffLat !== null ? [heights.takeoffLon, heights.takeoffLat] : null;

  const onRun = form.guard(() => {
    const common: Partial<SurveyParams> = {
      mode: jobMode,
      coverage,
      ...(corridor ? { corridor_width_m: corridorWidth ?? DEFAULTS.corridorWidth } : {}),
      battery_minutes: battery ?? DEFAULTS.battery,
      battery_swap_minutes: swap ?? DEFAULTS.swap,
      battery_reserve_pct: reserve ?? DEFAULTS.reserve,
      transit_legs: transitLegs,
      transit_speed_ms: transitLegs ? transitSpeed : null,
      direction_deg: autoDirection ? null : direction,
      ...batchParams(batch, keyProperty),
    };
    let params: Partial<SurveyParams>;
    if (spraying) {
      params = {
        ...common,
        ...(transitLegs && takeoffParam ? { takeoff: takeoffParam } : {}),
        spray: {
          swath_m: spray.swath_m ?? SPRAY_DEFAULTS.swath_m,
          rate_l_per_ha: spray.rate_l_per_ha ?? SPRAY_DEFAULTS.rate_l_per_ha,
          tank_l: spray.tank_l ?? SPRAY_DEFAULTS.tank_l,
          flow_l_per_min: spray.flow_l_per_min ?? SPRAY_DEFAULTS.flow_l_per_min,
          speed_ms: spray.speed_ms ?? SPRAY_DEFAULTS.speed_ms,
          turn_seconds: spray.turn_seconds ?? SPRAY_DEFAULTS.turn_seconds,
          refill_minutes: spray.refill_minutes ?? SPRAY_DEFAULTS.refill_minutes,
          height_m: spray.height_m ?? SPRAY_DEFAULTS.height_m,
        },
      };
    } else {
      const camera = cameraId === CUSTOM ? { ...custom, name: custom.name.trim() || CUSTOM_CAMERA_NAME } : savedCamera ?? cameraId;
      if (cameraId === CUSTOM && typeof camera === "object") saved.remember(camera);
      const heightParams = heightReferenceParams(heights);
      params = {
        ...common,
        camera,
        ...heightParams,
        // Transit legs start at the take-off point too, whatever the height reference.
        ...(heightParams.height_reference === "agl" && transitLegs && takeoffParam ? { takeoff: takeoffParam } : {}),
        front_overlap_pct: front ?? DEFAULTS.front,
        side_overlap_pct: side ?? DEFAULTS.side,
        speed_ms: speed ?? DEFAULTS.speed,
        turn_seconds: turn ?? DEFAULTS.turn,
        min_trigger_interval_s: minTrigger ?? DEFAULTS.minTrigger,
        max_altitude_agl_m: maxAltitude ?? DEFAULTS.maxAltitude,
        orientation,
        gcp_min: gcpMin ?? DEFAULTS.gcpMin,
        gcp_ha_per_extra: gcpHa ?? DEFAULTS.gcpHaPerExtra,
        gcp_corridor_spacing_m: gcpSpacing ?? DEFAULTS.gcpSpacing,
        photo_mb: photoMb,
        ...(mode === "altitude" ? { altitude_agl_m: altitude ?? DEFAULTS.altitude } : { target_gsd_cm: gsd ?? DEFAULTS.gsd }),
      };
    }
    void runner.run("survey", params).then((info) => {
      if (info) markStarted(info.id);
    });
  });

  // In Target GSD mode, show the height it implies (and whether it breaks the height limit).
  const gsdHeight = mode === "gsd" && gsd !== null && gsd > 0 && selectedCamera ? heightForGsd(selectedCamera, gsd, orientation) : null;
  const above = reference === "agl" ? "above ground" : "above the take-off ground";
  const gsdHint =
    gsdHeight !== null && Number.isFinite(gsdHeight)
      ? `About ${formatHeight(gsdHeight, units)} ${above} with this camera${
          maxAltitude !== null && gsdHeight > maxAltitude ? `: above the ${formatHeight(maxAltitude, units)} height limit` : ""
        }.`
      : "The height is derived from the camera.";
  const altitudeHint =
    !amsl && altitude !== null && maxAltitude !== null && altitude > maxAltitude
      ? `Above the ${formatHeight(maxAltitude, units)} height limit: the estimate will carry a warning.`
      : reference === "takeoff"
        ? "As the aircraft shows it: above the ground at the take-off point. Over higher ground the GSD gets finer and the overlap smaller."
        : amsl
          ? "The level held above mean sea level (the ground elevation plus the height wanted); the layout uses its height above the take-off ground."
          : undefined;

  const setCustomField = <K extends keyof CameraSpec>(key: K) => (value: CameraSpec[K] | null) => {
    if (value === null) return;
    setCustom((c) => ({ ...c, [key]: value }));
  };
  const setSprayField = (key: keyof typeof SPRAY_DEFAULTS) => (value: number | null) => setSpray((s) => ({ ...s, [key]: value }));

  const presetEntries = Object.entries(cameras);
  const byKind = (kind: CameraKind) => presetEntries.filter(([, c]) => (c.kind ?? "rgb") === kind);
  const defaultTakeoff = corridor ? "the first point of the centreline" : "the centre of the area";

  return (
    <form className="space-y-4 text-sm" onSubmit={(e) => e.preventDefault()} aria-label="Survey estimate">
      {!file ? (
        <PlanStartHint
          purpose={
            corridor
              ? `Corridor mapping needs a centreline: draw it, paste it (GeoJSON or WKT) or type its points. ${ESTIMATE_NOTE}`
              : `The survey needs an area: draw it, paste it (GeoJSON or WKT) or type its corners. ${ESTIMATE_NOTE}`
          }
          tools={corridor ? ["line"] : ["area"]}
          paste
        />
      ) : (
        <InputHint>
          {corridor
            ? lineCount > 0
              ? `Maps a corridor along ${lineCount === 1 ? "the selected line" : `${lineCount} lines`}${selectedFeatures.length > 0 ? " in the selection" : ""}. ${spraying ? SPRAY_NOTE : ESTIMATE_NOTE}`
              : `Select a line feature: the corridor centreline. ${ESTIMATE_NOTE}`
            : polygonCount > 0
              ? `Estimates effort for ${polygonCount} polygon${polygonCount === 1 ? "" : "s"}${selectedFeatures.length > 0 ? " in the selection" : ""}. ${spraying ? SPRAY_NOTE : ESTIMATE_NOTE}`
              : `Select a polygon feature to survey. ${ESTIMATE_NOTE}`}
        </InputHint>
      )}
      <FormErrorSummary form={form} />

      <ModuleSection title="Job">
        <div className="space-y-1.5">
          <p className="text-sm font-medium" id="survey-job">
            Job type
          </p>
          <ToggleGroup type="single" value={jobMode} onValueChange={(v) => v && setJobMode(v as typeof jobMode)} aria-labelledby="survey-job">
            <ToggleGroupItem value="mapping">Mapping</ToggleGroupItem>
            <ToggleGroupItem value="spraying">Spraying</ToggleGroupItem>
          </ToggleGroup>
        </div>
        <div className="space-y-1.5">
          <p className="text-sm font-medium" id="survey-coverage">
            Coverage
          </p>
          <ToggleGroup type="single" value={coverage} onValueChange={(v) => v && setCoverage(v as typeof coverage)} aria-labelledby="survey-coverage">
            <ToggleGroupItem value="area">Area</ToggleGroupItem>
            <ToggleGroupItem value="corridor">Corridor</ToggleGroupItem>
          </ToggleGroup>
          <p className="text-xs text-fg-muted">
            {corridor ? "Lines along a centreline (a road, pipeline or power line), over the corridor width." : "Parallel lines over a polygon."}
          </p>
        </div>
        {corridor ? (
          <NumField
            key={`corridor-${fieldsKey}`}
            label="Corridor width"
            unit="m"
            value={corridorWidth}
            onChange={setCorridorWidth}
            min={1}
            max={5000}
            hint="The full width covered, centred on the line."
            form={form}
          />
        ) : null}
        {file ? (
          <BatchToggle
            kind="survey"
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

      {spraying ? (
        <ModuleSection title="Spraying">
          <React.Fragment key={fieldsKey}>
            <NumField label="Swath width" unit="m" value={spray.swath_m} onChange={setSprayField("swath_m")} min={0.5} max={50} step={0.5} hint="Effective spray width; the lines are this far apart." form={form} />
            <NumField label="Application rate" unit="L/ha" value={spray.rate_l_per_ha} onChange={setSprayField("rate_l_per_ha")} min={0.1} max={1000} form={form} />
            <NumField label="Tank capacity" unit="L" value={spray.tank_l} onChange={setSprayField("tank_l")} min={0.5} max={500} form={form} />
            <NumField label="Pump maximum flow" unit="L/min" value={spray.flow_l_per_min} onChange={setSprayField("flow_l_per_min")} min={0.1} max={200} step={0.1} hint="Above it the estimate slows down so the rate is still applied." form={form} />
            <NumField label="Spray speed" unit="m/s" value={spray.speed_ms} onChange={setSprayField("speed_ms")} min={0.5} max={20} step={0.5} form={form} />
            <NumField
              label="Spray height above the crop"
              unit="m"
              value={spray.height_m}
              onChange={setSprayField("height_m")}
              min={0.5}
              max={30}
              step={0.5}
              presets={{ metric: SPRAY_HEIGHT_PRESETS, label: "Spray height presets" }}
              hint="Typical agricultural drones spray 2–5 m above the crop."
              form={form}
            />
            <NumField label="Turn time" unit="s" value={spray.turn_seconds} onChange={setSprayField("turn_seconds")} min={0} max={120} form={form} />
            <NumField label="Refill time" unit="min" value={spray.refill_minutes} onChange={setSprayField("refill_minutes")} min={0} max={120} hint="Each stop lasts the longer of the refill and the battery swap." form={form} />
          </React.Fragment>
        </ModuleSection>
      ) : (
        <>
          <ModuleSection title="Camera">
            <div className="space-y-1.5">
              <Label htmlFor="survey-camera">Camera</Label>
              <Select value={cameraId} onValueChange={setCameraId}>
                <SelectTrigger id="survey-camera">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CAMERA_KINDS.map((kind) =>
                    byKind(kind).length > 0 ? (
                      <SelectGroup key={kind}>
                        <SelectLabel>{CAMERA_KIND_LABEL[kind]}</SelectLabel>
                        {byKind(kind).map(([id, c]) => (
                          <SelectItem key={id} value={id}>
                            {c.name}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    ) : null,
                  )}
                  {saved.cameras.length > 0 ? (
                    <SelectGroup>
                      <SelectLabel>Saved in this browser</SelectLabel>
                      {saved.cameras.map((c) => (
                        <SelectItem key={c.name} value={`${SAVED_PREFIX}${c.name}`}>
                          {c.name}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  ) : null}
                  <SelectItem value={CUSTOM}>Custom camera</SelectItem>
                </SelectContent>
              </Select>
              {selectedCamera && cameraId !== CUSTOM ? (
                <p className="text-xs text-fg-muted">
                  {CAMERA_KIND_LABEL[selectedCamera.kind ?? "rgb"]}: {selectedCamera.image_width_px} × {selectedCamera.image_height_px} px,{" "}
                  {selectedCamera.focal_length_mm} mm lens
                  {(selectedCamera.images_per_capture ?? 1) > 1 ? `, ${selectedCamera.images_per_capture} images per capture` : ""}.
                </p>
              ) : null}
              {savedCamera ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    saved.forget(savedCamera.name);
                    setCameraId(DEFAULT_CAMERA);
                    announce(`${savedCamera.name} removed from the saved cameras.`);
                  }}
                >
                  <Trash2 aria-hidden />
                  Forget this camera
                </Button>
              ) : null}
            </div>
            {cameraId === CUSTOM ? (
              <div key={fieldsKey} className="space-y-3 rounded border border-border p-3">
                <Field label="Camera name" hint="Saved in this browser when an estimate runs with it.">
                  {(p) => (
                    <Input
                      {...p}
                      value={custom.name}
                      placeholder={CUSTOM_CAMERA_NAME}
                      onChange={(e) => setCustom((c) => ({ ...c, name: e.target.value }))}
                    />
                  )}
                </Field>
                <div className="space-y-1.5">
                  <Label htmlFor="survey-camera-kind">Sensor type</Label>
                  <Select value={custom.kind ?? "rgb"} onValueChange={(v) => setCustom((c) => ({ ...c, kind: v as CameraKind }))}>
                    <SelectTrigger id="survey-camera-kind">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CAMERA_KINDS.map((k) => (
                        <SelectItem key={k} value={k}>
                          {CAMERA_KIND_LABEL[k]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <NumField label="Sensor width" unit="mm" value={custom.sensor_width_mm} onChange={setCustomField("sensor_width_mm")} min={0.1} max={100} hint="Thermal: pixel pitch × image width (640 × 12 µm = 7.68 mm)." form={form} />
                <NumField label="Sensor height" unit="mm" value={custom.sensor_height_mm} onChange={setCustomField("sensor_height_mm")} min={0.1} max={100} form={form} />
                <NumField label="Focal length" unit="mm" value={custom.focal_length_mm} onChange={setCustomField("focal_length_mm")} min={0.1} max={500} form={form} />
                <NumField label="Image width" unit="px" value={custom.image_width_px} onChange={setCustomField("image_width_px")} min={1} max={100000} decimals={0} integer form={form} />
                <NumField label="Image height" unit="px" value={custom.image_height_px} onChange={setCustomField("image_height_px")} min={1} max={100000} decimals={0} integer form={form} />
                <NumField
                  label="Images per capture"
                  value={custom.images_per_capture ?? 1}
                  onChange={setCustomField("images_per_capture")}
                  min={1}
                  max={20}
                  decimals={0}
                  integer
                  hint="One per band for a multispectral camera."
                  form={form}
                />
                <NumField
                  label="Image file size"
                  unit="MB"
                  value={custom.photo_mb ?? null}
                  onChange={(v) => setCustom((c) => ({ ...c, photo_mb: v }))}
                  min={0.01}
                  max={2000}
                  optional
                  hint="For the storage estimate (empty: no estimate unless an override is set under Advanced)."
                  form={form}
                />
              </div>
            ) : null}
          </ModuleSection>

          <ModuleSection title="Height">
            <ToggleGroup type="single" value={mode} onValueChange={(v) => v && setMode(v as typeof mode)} aria-label="Height mode">
              <ToggleGroupItem value="altitude">Set height</ToggleGroupItem>
              <ToggleGroupItem value="gsd">Target GSD</ToggleGroupItem>
            </ToggleGroup>
            <HeightReferenceField
              value={heights}
              onChange={setHeights}
              form={form}
              fieldsKey={fieldsKey}
              pickId="survey-takeoff"
              defaultTakeoff={defaultTakeoff}
              takeoffAlways={transitLegs}
            />
            {mode === "altitude" ? (
              <NumField
                key={`altitude-${reference}-${fieldsKey}`}
                label={HEIGHT_FIELD_LABEL[reference]}
                unit="m"
                value={altitude}
                onChange={setAltitude}
                min={1}
                max={amsl ? 9000 : 1500}
                hint={altitudeHint}
                form={form}
              />
            ) : (
              <NumField
                key={`gsd-${fieldsKey}`}
                label="Target ground sample distance"
                unit="cm/px"
                value={gsd}
                onChange={setGsd}
                min={0.1}
                max={100}
                step={0.1}
                hint={gsdHint}
                form={form}
              />
            )}
          </ModuleSection>

          <ModuleSection title="Overlap and flight">
            <React.Fragment key={fieldsKey}>
              <NumField label="Front overlap" unit="%" value={front} onChange={setFront} min={0} max={99} form={form} />
              <NumField label="Side overlap" unit="%" value={side} onChange={setSide} min={0} max={99} form={form} />
              <NumField label="Ground speed" unit="m/s" value={speed} onChange={setSpeed} min={0.5} max={60} step={0.5} form={form} />
            </React.Fragment>
          </ModuleSection>
        </>
      )}

      <ModuleSection title="Batteries and transit">
        <React.Fragment key={fieldsKey}>
          <NumField label="Usable battery time" unit="min" value={battery} onChange={setBattery} min={1} max={600} form={form} />
          <NumField
            label="Battery reserve"
            unit="%"
            value={reserve}
            onChange={setReserve}
            min={0}
            max={90}
            hint="Kept back on every battery: blocks use the rest."
            form={form}
          />
        </React.Fragment>
        <SwitchField
          label="Count transit legs"
          checked={transitLegs}
          onChange={setTransitLegs}
          hint={`From the take-off point to each block and back (straight lines; ${defaultTakeoff} unless set).`}
        />
        {transitLegs ? (
          <>
            <NumField
              key={`transit-speed-${fieldsKey}`}
              label="Transit speed"
              unit="m/s"
              value={transitSpeed}
              onChange={setTransitSpeed}
              min={0.5}
              max={60}
              step={0.5}
              optional
              hint="Empty: the survey speed."
              form={form}
            />
            {spraying ? (
              <TakeoffFields
                value={heights}
                onChange={setHeights}
                form={form}
                fieldsKey={fieldsKey}
                pickId="survey-takeoff"
                defaultTakeoff={defaultTakeoff}
                elevation={false}
              />
            ) : null}
          </>
        ) : null}
      </ModuleSection>

      <Advanced form={form}>
        <React.Fragment key={fieldsKey}>
          <NumField label="Battery swap time" unit="min" value={swap} onChange={setSwap} min={0} max={120} form={form} />
          {spraying ? null : (
            <>
              <NumField label="Turn time" unit="s" value={turn} onChange={setTurn} min={0} max={300} form={form} />
              <NumField label="Minimum trigger interval" unit="s" value={minTrigger} onChange={setMinTrigger} min={0} max={60} step={0.1} form={form} />
              <NumField label="Height limit" unit="m" value={maxAltitude} onChange={setMaxAltitude} min={1} max={1500} hint="A warning is shown above this height above ground." form={form} />
              <NumField
                label="Minimum ground control points"
                value={gcpMin}
                onChange={setGcpMin}
                min={0}
                max={1000}
                decimals={0}
                integer
                hint="Rule of thumb (configurable): at least this many, well spread."
                form={form}
              />
              {corridor ? (
                <NumField label="One GCP every" unit="m" value={gcpSpacing} onChange={setGcpSpacing} min={1} max={100000} hint="Along the corridor centreline." form={form} />
              ) : (
                <NumField label="One more GCP per" unit="ha" value={gcpHa} onChange={setGcpHa} min={0.01} max={100000} hint="Added to the minimum for larger areas." form={form} />
              )}
              <NumField
                // Distinct from the custom camera's own "Image file size" (both can be on screen).
                label="Image file size override"
                unit="MB"
                value={photoMb}
                onChange={setPhotoMb}
                min={0.01}
                max={2000}
                optional
                hint={
                  selectedCamera?.photo_mb
                    ? `Empty: ${selectedCamera.photo_mb} MB, a typical file of this camera.`
                    : "Empty: the camera's typical size (no estimate when it has none)."
                }
                form={form}
              />
            </>
          )}
        </React.Fragment>
        <SwitchField label="Automatic line direction" checked={autoDirection} onChange={setAutoDirection} hint={corridor ? "A corridor's lines always follow its centreline." : "Along the long axis of the area."} />
        {!autoDirection && !corridor ? (
          <NumField key={fieldsKey} label="Line direction" unit="°" value={direction} onChange={setDirection} min={0} max={179.9} hint="Bearing clockwise from north." form={form} />
        ) : null}
        {spraying ? null : (
          <div className="space-y-1.5">
            <p className="text-sm font-medium" id="survey-orientation">
              Camera orientation
            </p>
            <ToggleGroup type="single" value={orientation} onValueChange={(v) => v && setOrientation(v as typeof orientation)} aria-labelledby="survey-orientation">
              <ToggleGroupItem value="landscape">Landscape</ToggleGroupItem>
              <ToggleGroupItem value="portrait">Portrait</ToggleGroupItem>
            </ToggleGroup>
          </div>
        )}
      </Advanced>

      <RunButton onRun={onRun} pending={runner.isPending} disabledReason={disabledReason} label={spraying ? "Estimate spraying" : "Estimate survey"} />
      <ResetFormButton onReset={() => setFields({})} />
    </form>
  );
}
