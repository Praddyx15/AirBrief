"use client";

/**
 * Zone inputs of the airspace check (airbrief_core/analyses/zones.py): the rule set, a planned
 * window for time-limited zones, other datasets used as zones with a column mapping, and which
 * nearby aerodromes count as operational. The rule set control (`ZoneRulesField`,
 * `useRingsProfile`) is shared with the flight audit and eVTOL panels.
 */

import * as React from "react";
import { Field } from "@/components/ui/field";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ZONE_LABEL } from "@/lib/colors";
import { hasPolygons } from "@/lib/datasets";
import { formatLength, formatNumber, formatUtcOffset, plural } from "@/lib/format";
import { ZONE_PROFILE_HINT, ZONE_PROFILE_LABEL } from "@/lib/labels";
import { usePlan } from "@/lib/plan-store";
import { useFeatures, useMeta, useMyFiles } from "@/lib/queries";
import { useUnits } from "@/lib/store";
import type {
  AirspaceParams,
  AirspaceResult,
  AnalysisInfo,
  FileInfo,
  ZoneKind,
  ZoneLayerMapping,
  ZoneProfileKey,
  ZoneRules,
} from "@/lib/types";
import { ModuleSection, SwitchField, type FormErrors } from "../shared";
import { paramChoice } from "../analysis-form";
import { DateTimeField } from "../environment/fields";
import { browserOffsetMinutes, defaultPlan, isHhmm, isIsoDate } from "../environment/time";

export const ZONE_PROFILES: readonly ZoneProfileKey[] = ["dgca_2021", "conservative_illustrative", "custom"];
const ZONE_KINDS: readonly ZoneKind[] = ["red", "yellow", "green"];
/** Select value for "find the column by its usual names" (a Radix select item needs a value). */
const AUTO = "__auto__";

/** The rule set of a run (airspace, flight audit, eVTOL). */
export interface ZoneRulesValue {
  profile: ZoneProfileKey;
  /** zone_rules as JSON text, for the custom profile */
  customRules: string;
}

export interface ZoneInputs extends ZoneRulesValue {
  windowOn: boolean;
  /** local date "YYYY-MM-DD" and times "HH:MM"; an end before the start is on the next day */
  date: string;
  start: string;
  end: string;
  /** datasets used as zones, with their column mapping */
  layers: Record<string, ZoneLayerMapping>;
  /** aerodrome ident → operational, set by hand */
  operational: Record<string, boolean>;
}

export function defaultZoneInputs(): ZoneInputs {
  return { profile: "dgca_2021", customRules: "", windowOn: false, ...defaultPlan(), layers: {}, operational: {} };
}

/**
 * The rule set from stored parameters. A run stored before zone profiles has no `zone_profile`
 * and used the conservative rules (or its own `zone_rules`); `{}` (reset) gives the default.
 */
export function zoneRulesFromParams(p: Readonly<Record<string, unknown>>): ZoneRulesValue {
  const rules = p.zone_rules && typeof p.zone_rules === "object" ? (p.zone_rules as ZoneRules) : null;
  const stored = Object.keys(p).length > 0;
  const fallback: ZoneProfileKey = rules ? "custom" : stored ? "conservative_illustrative" : "dgca_2021";
  return {
    profile: paramChoice(p, "zone_profile", ZONE_PROFILES, fallback),
    customRules: rules ? JSON.stringify(rules, null, 2) : "",
  };
}

/** `zone_profile`, plus `zone_rules` for custom rules (text the form has already checked). */
export function zoneRulesParams(r: ZoneRulesValue): { zone_profile: ZoneProfileKey; zone_rules?: ZoneRules } {
  if (r.profile === "custom") return { zone_profile: "custom", zone_rules: JSON.parse(r.customRules) as ZoneRules };
  return { zone_profile: r.profile };
}

/** While the panel is open, the map's context rings are drawn with its rule set. */
export function useRingsProfile(profile: ZoneProfileKey): void {
  const setProfile = usePlan((s) => s.setZoneProfile);
  React.useEffect(() => {
    setProfile(profile);
    // cleared on close because the next panel (if any) sets its own right after
    return () => setProfile(null);
  }, [profile, setProfile]);
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** A local date and time as a UTC ISO string ("" when either is not valid). */
function localToUtc(date: string, time: string, nextDay = false): string {
  if (!isIsoDate(date) || !isHhmm(time)) return "";
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  return new Date(y, m - 1, d + (nextDay ? 1 : 0), hh, mm).toISOString();
}

function utcToLocal(iso: unknown): { date: string; time: string } | null {
  if (typeof iso !== "string") return null;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return null;
  return { date: `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`, time: `${pad(t.getHours())}:${pad(t.getMinutes())}` };
}

/** The zone fields from stored parameters; `{}` (reset) gives the defaults. */
export function zoneInputsFromParams(p: Readonly<Record<string, unknown>>): ZoneInputs {
  const base = defaultZoneInputs();
  const start = utcToLocal(p.planned_start_utc);
  const end = utcToLocal(p.planned_end_utc);
  const ids = Array.isArray(p.zone_dataset_ids) ? p.zone_dataset_ids.filter((x): x is string => typeof x === "string") : [];
  const mappings = (p.zone_mappings && typeof p.zone_mappings === "object" ? p.zone_mappings : {}) as Record<string, ZoneLayerMapping>;
  const operational = (p.aerodrome_operational && typeof p.aerodrome_operational === "object" ? p.aerodrome_operational : {}) as Record<string, boolean>;
  return {
    ...zoneRulesFromParams(p),
    windowOn: start !== null && end !== null,
    date: start?.date ?? base.date,
    start: start?.time ?? base.start,
    end: end?.time ?? base.end,
    layers: Object.fromEntries(ids.map((id) => [id, mappings[id] ?? {}])),
    operational: { ...operational },
  };
}

export function zoneInputsParams(z: ZoneInputs): Partial<AirspaceParams> {
  const out: Partial<AirspaceParams> = zoneRulesParams(z);
  if (z.windowOn) {
    out.planned_start_utc = localToUtc(z.date, z.start);
    out.planned_end_utc = localToUtc(z.date, z.end, z.end <= z.start);
  }
  if (Object.keys(z.operational).length > 0) out.aerodrome_operational = z.operational;
  const ids = Object.keys(z.layers);
  if (ids.length > 0) {
    out.zone_dataset_ids = ids;
    const mapped = Object.entries(z.layers).filter(([, m]) => Object.values(m).some((v) => v !== null && v !== undefined));
    if (mapped.length > 0) out.zone_mappings = Object.fromEntries(mapped);
  }
  return out;
}

function customRulesError(text: string): string | null {
  if (!text.trim()) return "Paste the rules as JSON, or pick another rule set.";
  try {
    const rules = JSON.parse(text) as unknown;
    if (!rules || typeof rules !== "object" || !Array.isArray((rules as ZoneRules).rules)) {
      return "The rules need a \"rules\" list (the zone_rules format of the API).";
    }
  } catch {
    return "This is not valid JSON.";
  }
  return null;
}

function CustomRulesField({ value, onChange, form }: { value: string; onChange: (v: string) => void; form: FormErrors }) {
  const id = React.useId();
  const [touched, setTouched] = React.useState(false);
  const error = customRulesError(value);
  const report = form.report;
  React.useEffect(() => {
    report(id, error ? `Custom rules: ${error}` : null);
  }, [id, error, report]);
  React.useEffect(() => () => report(id, null), [id, report]);
  return (
    <Field id={id} label="Custom rules (JSON)" hint="Starts from the DGCA 2021 rules; edit the rings, ceilings and types." error={touched || form.attempted ? error : null} required>
      {(p) => (
        <textarea
          {...p}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={() => setTouched(true)}
          rows={8}
          spellCheck={false}
          className="w-full rounded border border-border bg-bg px-3 py-2 font-mono text-xs text-fg aria-[invalid=true]:border-danger"
        />
      )}
    </Field>
  );
}

/** The Rules select (DGCA 2021 / conservative / custom JSON) of the airspace, flight-audit and eVTOL forms. */
export function ZoneRulesField({ value, onChange, form }: { value: ZoneRulesValue; onChange: (r: ZoneRulesValue) => void; form: FormErrors }) {
  const meta = useMeta().data;
  const id = React.useId();
  const pick = (v: string) => {
    const profile = ZONE_PROFILES.includes(v as ZoneProfileKey) ? (v as ZoneProfileKey) : "dgca_2021";
    const preset = meta?.zone_profiles?.dgca_2021;
    const customRules = profile === "custom" && !value.customRules && preset ? JSON.stringify(preset, null, 2) : value.customRules;
    onChange({ profile, customRules });
  };
  return (
    <>
      <div className="space-y-1.5">
        <Label htmlFor={`${id}-profile`}>Rules</Label>
        <Select value={value.profile} onValueChange={pick}>
          <SelectTrigger id={`${id}-profile`} aria-describedby={`${id}-hint`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ZONE_PROFILES.map((p) => (
              <SelectItem key={p} value={p}>
                {ZONE_PROFILE_LABEL[p]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p id={`${id}-hint`} className="text-xs text-fg-muted">
          {ZONE_PROFILE_HINT[value.profile]}
        </p>
      </div>
      {value.profile === "custom" ? (
        <CustomRulesField value={value.customRules} onChange={(customRules) => onChange({ ...value, customRules })} form={form} />
      ) : null}
    </>
  );
}

function PlannedWindow({ value, onChange, form }: { value: ZoneInputs; onChange: (z: ZoneInputs) => void; form: FormErrors }) {
  const offset = browserOffsetMinutes(value.date, value.start);
  return (
    <>
      <SwitchField
        label="Planned window"
        checked={value.windowOn}
        onChange={(windowOn) => onChange({ ...value, windowOn })}
        hint="Zones with a validity period count only when they are active in it. Without a window they all count."
      />
      {value.windowOn ? (
        <>
          <DateTimeField
            label="Date (local)"
            type="date"
            value={value.date}
            onChange={(date) => onChange({ ...value, date })}
            validate={(v) => (!v ? "Enter a date." : isIsoDate(v) ? null : "Enter a real date.")}
            form={form}
          />
          <div className="grid grid-cols-2 gap-3">
            <DateTimeField
              label="Start (local)"
              type="time"
              value={value.start}
              onChange={(start) => onChange({ ...value, start })}
              validate={(v) => (isHhmm(v) ? null : "Enter a time as HH:MM.")}
              form={form}
            />
            <DateTimeField
              label="End (local)"
              type="time"
              value={value.end}
              onChange={(end) => onChange({ ...value, end })}
              validate={(v) => (!isHhmm(v) ? "Enter a time as HH:MM." : v === value.start ? "The end must differ from the start." : null)}
              hint={isHhmm(value.start) && isHhmm(value.end) && value.end < value.start ? "Ends on the next day." : undefined}
              form={form}
            />
          </div>
          <p className="text-xs text-fg-muted">Local times, {formatUtcOffset(offset)}; sent in UTC.</p>
        </>
      ) : null}
    </>
  );
}

/** Property names of a dataset's features, without the `_` system ones (first 500 features). */
function useColumns(fileId: string): string[] {
  const features = useFeatures(fileId).data;
  return React.useMemo(() => {
    const keys = new Set<string>();
    for (const f of (features?.features ?? []).slice(0, 500)) {
      for (const k of Object.keys(f.properties ?? {})) if (!k.startsWith("_")) keys.add(k);
    }
    return [...keys].sort((a, b) => a.localeCompare(b));
  }, [features]);
}

function ColumnSelect({
  label,
  value,
  columns,
  autoLabel,
  onChange,
}: {
  label: string;
  value: string | null | undefined;
  columns: string[];
  autoLabel: string;
  onChange: (v: string | null) => void;
}) {
  const id = React.useId();
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
      <Select value={value ?? AUTO} onValueChange={(v) => onChange(v === AUTO ? null : v)}>
        <SelectTrigger id={id} className="h-8 text-sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={AUTO}>{autoLabel}</SelectItem>
          {columns.map((c) => (
            <SelectItem key={c} value={c}>
              {c}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function LayerMapping({ file, mapping, onChange }: { file: FileInfo; mapping: ZoneLayerMapping; onChange: (m: ZoneLayerMapping) => void }) {
  const columns = useColumns(file.id);
  const layerId = React.useId();
  const kindId = React.useId();
  const set = (patch: Partial<ZoneLayerMapping>) => onChange({ ...mapping, ...patch });
  return (
    <fieldset className="space-y-2 rounded border border-border p-2">
      <legend className="px-1 text-xs font-medium text-fg-muted">Columns of {file.filename}</legend>
      <div className="grid grid-cols-2 gap-2">
        <ColumnSelect label="Zone kind" value={mapping.zone_field} columns={columns} autoLabel="Auto (zone, colour, restriction)" onChange={(zone_field) => set({ zone_field })} />
        <div className="space-y-1">
          <Label htmlFor={kindId} className="text-xs">
            Otherwise
          </Label>
          <Select value={mapping.default_zone ?? "red"} onValueChange={(v) => set({ default_zone: v as ZoneKind })}>
            <SelectTrigger id={kindId} className="h-8 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ZONE_KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {ZONE_LABEL[k]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <ColumnSelect label="Ceiling (m AGL)" value={mapping.ceiling_field} columns={columns} autoLabel="Auto (ceiling_agl_m, ceiling_m)" onChange={(ceiling_field) => set({ ceiling_field })} />
        {file.layers.length > 1 ? (
          <div className="space-y-1">
            <Label htmlFor={layerId} className="text-xs">
              Layer
            </Label>
            <Select value={mapping.layer ?? AUTO} onValueChange={(v) => set({ layer: v === AUTO ? null : v })}>
              <SelectTrigger id={layerId} className="h-8 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={AUTO}>Every layer</SelectItem>
                {file.layers.map((l) => (
                  <SelectItem key={l.name} value={l.name}>
                    {l.name || "Layer"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : (
          <span aria-hidden />
        )}
        <ColumnSelect label="Valid from (UTC)" value={mapping.valid_from_field} columns={columns} autoLabel="Auto (valid_from)" onChange={(valid_from_field) => set({ valid_from_field })} />
        <ColumnSelect label="Valid to (UTC)" value={mapping.valid_to_field} columns={columns} autoLabel="Auto (valid_to)" onChange={(valid_to_field) => set({ valid_to_field })} />
      </div>
    </fieldset>
  );
}

const LAYERS_SHOWN = 5;

function ZoneLayers({ value, onChange }: { value: ZoneInputs; onChange: (z: ZoneInputs) => void }) {
  // Only this browser's own uploads: zones are your data, not whatever else is on the server.
  const files = useMyFiles(30).data?.items ?? [];
  const [showAll, setShowAll] = React.useState(false);
  const usable = files.filter((f) => f.status === "COMPLETED" && hasPolygons(f));
  const chosen = new Set(Object.keys(value.layers));
  const shown = showAll ? usable : usable.filter((f, i) => i < LAYERS_SHOWN || chosen.has(f.id));
  // A chosen dataset that is no longer listed (older than the list) stays chosen.
  const missing = [...chosen].filter((id) => !usable.some((f) => f.id === id));
  const toggle = (id: string, on: boolean) => {
    const layers = { ...value.layers };
    if (on) layers[id] = layers[id] ?? {};
    else delete layers[id];
    onChange({ ...value, layers });
  };
  if (usable.length === 0 && missing.length === 0) {
    return (
      <p className="text-xs text-fg-muted">
        Open or upload a file with polygons (your own zones, or an ED-269 / ED-318 zone file) to use it as zones here.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {shown.map((f) => (
        <div key={f.id} className="space-y-2">
          <SwitchField
            label={f.filename}
            checked={chosen.has(f.id)}
            onChange={(on) => toggle(f.id, on)}
            hint={`${plural(f.feature_count, "feature")}; its polygons become zones.`}
          />
          {chosen.has(f.id) ? (
            <LayerMapping file={f} mapping={value.layers[f.id] ?? {}} onChange={(m) => onChange({ ...value, layers: { ...value.layers, [f.id]: m } })} />
          ) : null}
        </div>
      ))}
      {usable.length > LAYERS_SHOWN ? (
        <button type="button" className="text-xs font-medium text-accent-text underline underline-offset-4" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show fewer" : `Show all ${formatNumber(usable.length)} of your files with polygons`}
        </button>
      ) : null}
      {missing.length > 0 ? (
        <p className="text-xs text-fg-muted">
          Also {plural(missing.length, "dataset")} from the analysis shown.{" "}
          <button type="button" className="font-medium text-accent-text underline underline-offset-4" onClick={() => onChange({ ...value, layers: Object.fromEntries(Object.entries(value.layers).filter(([id]) => !missing.includes(id))) })}>
            Stop using {missing.length === 1 ? "it" : "them"}
          </button>
        </p>
      ) : null}
    </div>
  );
}

function AerodromeStatus({ value, onChange, analysis }: { value: ZoneInputs; onChange: (z: ZoneInputs) => void; analysis: AnalysisInfo | null }) {
  const units = useUnits();
  const result = analysis?.status === "COMPLETED" ? (analysis.result as AirspaceResult | null) : null;
  const aerodromes = (result?.nearest_aerodromes ?? []).filter((a) => a.operational !== null && a.operational !== undefined);
  if (aerodromes.length === 0) {
    return <p className="text-xs text-fg-muted">After a check, the nearest aerodromes are listed here to mark them operational or not.</p>;
  }
  return (
    <ul className="space-y-2" aria-label="Nearest aerodromes">
      {aerodromes.map((a) => {
        const set = value.operational[a.ident];
        const how = set !== undefined ? "set by you" : a.scheduled_service ? "default: scheduled service" : "default for its type";
        return (
          <li key={a.ident}>
            <SwitchField
              label={`Operational: ${a.ident} · ${a.name}`}
              checked={set ?? a.operational === true}
              onChange={(on) => onChange({ ...value, operational: { ...value.operational, [a.ident]: on } })}
              hint={`${formatLength(a.distance_km * 1000, units)} away; ${how}. Its bands apply only when operational.`}
            />
          </li>
        );
      })}
    </ul>
  );
}

export function ZoneInputsFields({
  value,
  onChange,
  form,
  fieldsKey,
  analysis,
}: {
  value: ZoneInputs;
  onChange: (z: ZoneInputs) => void;
  form: FormErrors;
  fieldsKey: number;
  analysis: AnalysisInfo | null;
}) {
  const count = Object.keys(value.layers).length;
  return (
    <React.Fragment key={fieldsKey}>
      <ModuleSection title="Zone rules">
        <ZoneRulesField value={value} onChange={(r) => onChange({ ...value, ...r })} form={form} />
        <PlannedWindow value={value} onChange={onChange} form={form} />
      </ModuleSection>
      <ModuleSection title={count > 0 ? `Zones from layers (${formatNumber(count)})` : "Zones from layers"}>
        <ZoneLayers value={value} onChange={onChange} />
      </ModuleSection>
      <ModuleSection title="Aerodrome status">
        <AerodromeStatus value={value} onChange={onChange} analysis={analysis} />
      </ModuleSection>
    </React.Fragment>
  );
}
