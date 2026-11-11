"use client";

/**
 * "Operational volume" form section: the SORA 2.5 flight geography height and every
 * contingency-volume term, pre-filled from the aircraft class, each editable with a hint naming
 * its formula and source, plus the adjacent-area switch and the density grid size. A preview
 * shows the sizes the analysis will compute.
 */

import * as React from "react";
import { RotateCcw } from "lucide-react";
import { formatLength } from "@/lib/format";
import { AIRCRAFT_TYPE_LABEL } from "@/lib/labels";
import type { Units } from "@/lib/store";
import type { AircraftType } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Advanced, NumField, SwitchField, type FormErrors } from "../shared";
import { autoGridM, previewVolume, type VolumeForm } from "./volume";

export interface VolumeFieldsProps {
  value: VolumeForm;
  onChange: (patch: Partial<VolumeForm>) => void;
  adjacentArea: boolean;
  onAdjacentArea: (on: boolean) => void;
  gridM: number | null;
  onGridM: (value: number | null) => void;
  /** values of the rest of the form, for the preview and the defaults shown in hints */
  heightAgl: number | null;
  speed: number | null;
  dimension: number | null;
  bufferRule: "one_to_one" | "ballistic";
  /** name of the aircraft class the terms came from, or null for Custom */
  presetName: string | null;
  onResetTerms: () => void;
  form: FormErrors;
  units: Units;
  /** changes when the fields are set from an analysis or reset (remounts invalid text) */
  fieldsKey: number;
}

export function VolumeFields({
  value: v,
  onChange,
  adjacentArea,
  onAdjacentArea,
  gridM,
  onGridM,
  heightAgl,
  speed,
  dimension,
  bufferRule,
  presetName,
  onResetTerms,
  form,
  units,
  fieldsKey,
}: VolumeFieldsProps) {
  const typeId = React.useId();
  const preview = previewVolume(v, { speed, heightAgl, dimension, rule: bufferRule });
  const fgHeight = v.fgHeight ?? heightAgl;
  const autoGrid = fgHeight === null ? null : autoGridM(fgHeight);
  const fixedWing = v.aircraft === "fixed_wing";
  const set = <K extends keyof VolumeForm>(key: K) => (value: VolumeForm[K]) => onChange({ [key]: value } as Partial<VolumeForm>);

  return (
    <div className="space-y-3">
      <NumField
        key={`fg-${fieldsKey}`}
        label="Flight geography height"
        unit="m"
        value={v.fgHeight}
        onChange={set("fgHeight")}
        min={1}
        max={1500}
        optional
        hint={`H_FG, the highest planned height of normal flight (SORA 2.5 §2.2.2). Empty: the height above ground${heightAgl !== null ? ` (${formatLength(heightAgl, units)})` : ""}.`}
        form={form}
      />

      <div className="rounded border border-border bg-elevated/40 p-2.5 text-xs">
        {preview ? (
          <p>
            <span className="font-medium text-fg">Preview: </span>
            contingency volume {formatLength(preview.sCv, units)} around the flight geography, up to{" "}
            {formatLength(preview.hCv, units)} above ground; ground risk buffer {formatLength(preview.grb, units)}; adjacent
            area out to {formatLength(preview.adjacent, units)}.
          </p>
        ) : (
          <p className="text-fg-muted">Fill in the aircraft and the terms below to preview the sizes.</p>
        )}
      </div>

      <Advanced label="Contingency volume terms" form={form}>
        <p className="text-xs text-fg-muted">
          {presetName ? `Pre-filled from “${presetName}”: illustrative values, replace them with your aircraft's data. ` : ""}Term
          structure of the German LBA operational-volume method; conventions differ between authorities, so every term can be
          changed.
        </p>
        <div className="space-y-1.5">
          <p className="text-sm font-medium" id={typeId}>
            Contingency manoeuvre
          </p>
          <ToggleGroup type="single" value={v.aircraft} onValueChange={(x) => x && onChange({ aircraft: x as AircraftType })} aria-labelledby={typeId}>
            <ToggleGroupItem value="multirotor">{AIRCRAFT_TYPE_LABEL.multirotor} (stop)</ToggleGroupItem>
            <ToggleGroupItem value="fixed_wing">{AIRCRAFT_TYPE_LABEL.fixed_wing} (turn)</ToggleGroupItem>
          </ToggleGroup>
        </div>
        <React.Fragment key={`terms-${fieldsKey}`}>
          <NumField
            label="GNSS position error"
            unit="m"
            value={v.gnss}
            onChange={set("gnss")}
            min={0}
            max={500}
            hint="S_GNSS: accuracy of the navigation solution (SORA 2.5 §4.2.4 (b)). About 3 m for GNSS, under 0.1 m with RTK."
            form={form}
          />
          <NumField
            label="Position-keeping error"
            unit="m"
            value={v.position}
            onChange={set("position")}
            min={0}
            max={500}
            hint="S_pos: flight technical error, how far the aircraft strays from its path (SORA 2.5 §4.2.4 (b))."
            form={form}
          />
          <NumField
            label="Map error"
            unit="m"
            value={v.map}
            onChange={set("map")}
            min={0}
            max={500}
            hint="S_map: error of the map and of the path definition (SORA 2.5 §4.2.4 (b))."
            form={form}
          />
          <NumField
            label="Reaction time"
            unit="s"
            value={v.reaction}
            onChange={set("reaction")}
            min={0}
            max={60}
            hint="t_R: latency until the contingency manoeuvre starts; reaction distance S_R = v·t_R (LBA method: 1 s)."
            form={form}
          />
          {fixedWing ? (
            <NumField
              label="Bank angle"
              unit="°"
              value={v.bank}
              onChange={set("bank")}
              min={5}
              max={75}
              hint="φ: turn radius S_CM = v²/(g·tan φ) (LBA method: 30°)."
              form={form}
            />
          ) : (
            <NumField
              label="Pitch angle"
              unit="°"
              value={v.pitch}
              onChange={set("pitch")}
              min={5}
              max={75}
              hint="θ: stopping distance S_CM = v²/(2·g·tan θ) (LBA method: up to 45°)."
              form={form}
            />
          )}
          <NumField
            label="Altimetry error"
            unit="m"
            value={v.altimetry}
            onChange={set("altimetry")}
            min={0}
            max={500}
            hint="H_alt: height error of the altimeter (barometric: about 4 m)."
            form={form}
          />
          <NumField
            label="Climb angle during the reaction time"
            unit="°"
            value={v.climb}
            onChange={set("climb")}
            min={0}
            max={90}
            hint="γ: height gained H_R = v·t_R·sin γ (45° = 0.707 of the reaction distance). The manoeuvre adds H_CM = ½·v²/g."
            form={form}
          />
        </React.Fragment>
        <Button type="button" variant="ghost" size="sm" className="w-full" onClick={onResetTerms}>
          <RotateCcw aria-hidden />
          Use the aircraft class values
        </Button>
      </Advanced>

      <SwitchField
        label="Adjacent area (Step 8)"
        checked={adjacentArea}
        onChange={onAdjacentArea}
        hint="Average population density between the ground risk buffer and v × 3 min (at least 5 km, at most 35 km) from the operational volume (SORA 2.5 §4.8.3)."
      />
      <NumField
        key={`grid-${fieldsKey}`}
        label="Density grid cell"
        unit="m"
        value={gridM}
        onChange={onGridM}
        min={25}
        max={10000}
        optional
        hint={`Side of the square cells averaged for the highest density. Empty: SORA 2.5 Table 4 by height${autoGrid !== null ? ` (${formatLength(autoGrid, units)} here)` : ""}.`}
        form={form}
      />
    </div>
  );
}
