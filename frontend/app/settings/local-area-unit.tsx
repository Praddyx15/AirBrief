"use client";

/**
 * Settings rows for the land-area unit used by field-work estimates (survey, spraying): hectare, acre,
 * guntha or bigha, and the size of a bigha, which differs by state and district. Stored in this
 * browser only (`lib/area-units.ts`).
 */

import { useId, useState } from "react";
import { useLocalAreaUnit, BIGHA_MAX_M2, BIGHA_MIN_M2 } from "@/lib/area-units";
import { BIGHA_PRESETS, LOCAL_AREA_UNITS, LOCAL_AREA_UNIT_LABEL, M2_PER_SQ_FT, formatNumber, type LocalAreaUnit } from "@/lib/format";
import { Field } from "@/components/ui/field";
import { NumberInput } from "@/components/ui/number-input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

const OTHER = "other";

/** The preset a size matches (to the square metre), or "other". */
function presetFor(m2: number): string {
  return BIGHA_PRESETS.find((p) => Math.abs(p.m2 - m2) < 0.5)?.id ?? OTHER;
}

export function LocalAreaUnitSetting() {
  const ids = useId().replace(/:/g, "");
  const { unit, bighaM2, setUnit, setBighaM2 } = useLocalAreaUnit();
  const [error, setError] = useState<string | null>(null);
  const preset = presetFor(bighaM2);

  return (
    <>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div className="min-w-0">
          <p id={`${ids}-area-unit`} className="text-sm font-medium">
            Land area unit
          </p>
          <p className="mt-0.5 max-w-[55ch] text-xs text-fg-muted">
            Shown in survey and spraying estimates next to the units above: hectare, acre, guntha (1/40 acre, 121 square yards) or bigha.
          </p>
        </div>
        <div className="shrink-0">
          <ToggleGroup
            type="single"
            aria-labelledby={`${ids}-area-unit`}
            value={unit}
            onValueChange={(v) => {
              if (v) setUnit(v as LocalAreaUnit);
            }}
            className="flex-wrap"
          >
            {LOCAL_AREA_UNITS.map((u) => (
              <ToggleGroupItem key={u} value={u}>
                {LOCAL_AREA_UNIT_LABEL[u]}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div className="min-w-0">
          <p id={`${ids}-bigha`} className="text-sm font-medium">
            Bigha size
          </p>
          <p className="mt-0.5 max-w-[55ch] text-xs text-fg-muted">
            A bigha differs by state and even by district: pick a commonly quoted size or type the one in your land records.
            Now {formatNumber(bighaM2, { maximumFractionDigits: 1 })} m² ({formatNumber(bighaM2 / M2_PER_SQ_FT, { maximumFractionDigits: 0 })} ft²).
          </p>
        </div>
        <div className="flex w-full shrink-0 flex-col gap-2 sm:w-64">
          <Select
            value={preset}
            onValueChange={(v) => {
              const picked = BIGHA_PRESETS.find((p) => p.id === v);
              if (picked) {
                setError(null);
                setBighaM2(picked.m2);
              }
            }}
          >
            <SelectTrigger aria-labelledby={`${ids}-bigha`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {BIGHA_PRESETS.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.label}
                </SelectItem>
              ))}
              <SelectItem value={OTHER}>Another size (type it below)</SelectItem>
            </SelectContent>
          </Select>
          <Field label="Bigha size" unit="m²" error={error} hideLabel>
            {(p) => (
              <NumberInput
                {...p}
                value={Math.round(bighaM2 * 10) / 10}
                min={BIGHA_MIN_M2}
                max={BIGHA_MAX_M2}
                step={1}
                unit="m²"
                onValueChange={(v) => {
                  if (v === null) return;
                  if (v < BIGHA_MIN_M2 || v > BIGHA_MAX_M2) {
                    setError(`Must be between ${formatNumber(BIGHA_MIN_M2)} and ${formatNumber(BIGHA_MAX_M2)} m².`);
                    return;
                  }
                  setError(null);
                  setBighaM2(v);
                }}
                onInvalidText={setError}
              />
            )}
          </Field>
        </div>
      </div>
    </>
  );
}
