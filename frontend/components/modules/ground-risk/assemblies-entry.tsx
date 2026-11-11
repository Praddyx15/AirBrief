"use client";

/**
 * "Assemblies and events": outdoor gatherings the data pack cannot know (a match, a festival, a
 * market day), checked within 1 km of the operational volume (SORA 2.5 Step #8). Each entry has
 * a name, a place (latitude, longitude with an optional radius, typed or placed with a map click,
 * or a point or polygon of the dataset), the expected people and an optional time window. Typed
 * coordinates keep it keyboard-operable; nothing needs dragging on the map.
 */

import * as React from "react";
import { Crosshair, Plus, Trash2 } from "lucide-react";
import { formatCoord, formatDate, formatNumber, localizeUnits } from "@/lib/format";
import { cancelMapPick, startMapPick, useMapPick } from "@/lib/map-pick";
import { usePlan } from "@/lib/plan-store";
import { useUnits } from "@/lib/store";
import { ASSEMBLY_BAND_LABEL } from "@/lib/labels";
import type { AssemblyBand, AssemblyInput, DatasetFeatureCollection } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { IconButton } from "@/components/ui/icon-button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { announce } from "@/components/workspace/live-region";
import { ModuleSection, NumField, featureName } from "../shared";
import { assemblyPlace, circlePolygon, isAssemblyGeometry, parseLatLon } from "./volume";

/** At most this many entries (the API's limit). */
export const MAX_ASSEMBLIES = 100;

function bandOf(people: number | null | undefined): AssemblyBand {
  if (people === null || people === undefined) return "unknown";
  if (people < 40_000) return "lt_40k";
  return people <= 400_000 ? "40k_400k" : "gt_400k";
}

/** `datetime-local` text (local time) → ISO UTC, or null when empty or invalid. */
function localToUtc(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function describe(entry: AssemblyInput): string {
  const place = assemblyPlace(entry.geometry);
  const where = place ? `${entry.geometry.type === "Point" ? "" : "Area around "}${formatCoord(place.lon, place.lat)}` : "";
  const people = entry.expected_people == null ? null : `${formatNumber(entry.expected_people)} people`;
  const when = entry.start_utc || entry.end_utc ? `${formatDate(entry.start_utc)} – ${formatDate(entry.end_utc)}` : null;
  return [people, ASSEMBLY_BAND_LABEL[bandOf(entry.expected_people)], where, when].filter(Boolean).join(" · ");
}

export function AssembliesEntry({
  entries,
  onChange,
  features,
  selectedFeatures = [],
  disabledReason,
}: {
  entries: readonly AssemblyInput[];
  onChange: (entries: AssemblyInput[]) => void;
  features: DatasetFeatureCollection | null;
  /** selected feature indices: the flight geography (every feature when empty) */
  selectedFeatures?: readonly number[];
  /** shown instead of the add form (e.g. the operational volume is off) */
  disabledReason?: string | null;
}) {
  const ids = React.useId();
  const units = useUnits();
  const nameRef = React.useRef<HTMLInputElement>(null);
  const [name, setName] = React.useState("");
  const [placeMode, setPlaceMode] = React.useState<"coordinates" | "feature">("coordinates");
  const [coords, setCoords] = React.useState("");
  const [radius, setRadius] = React.useState<number | null>(null);
  const [featureIndex, setFeatureIndex] = React.useState<string>("");
  const [people, setPeople] = React.useState<number | null>(null);
  const [start, setStart] = React.useState("");
  const [end, setEnd] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [fieldsKey, setFieldsKey] = React.useState(0);

  const placeFeatures = React.useMemo(
    () => (features?.features ?? []).filter((f) => isAssemblyGeometry(f.geometry)),
    [features],
  );
  const featureInFlightGeography =
    placeMode === "feature" &&
    featureIndex !== "" &&
    (selectedFeatures.length === 0 || selectedFeatures.includes(Number(featureIndex)));

  // "Place on map" (lib/map-pick.ts): the next map click (or Space on the focused map, for its
  // centre) fills the coordinates. Ends on Escape, when plan drawing starts and on unmount.
  const pickId = `${ids}-assembly`;
  const picking = useMapPick((s) => s.request?.id === pickId);
  const planDrawing = usePlan((s) => s.drawing);
  React.useEffect(
    () => () => {
      if (useMapPick.getState().request?.id === pickId) cancelMapPick();
    },
    [pickId],
  );
  const addHidden = Boolean(disabledReason) || placeMode !== "coordinates";
  React.useEffect(() => {
    if ((planDrawing || addHidden) && useMapPick.getState().request?.id === pickId) cancelMapPick();
  }, [planDrawing, addHidden, pickId]);
  React.useEffect(() => {
    if (!picking) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      e.preventDefault();
      e.stopPropagation();
      cancelMapPick();
      announce("Placing on the map cancelled.");
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [picking]);
  const placeOnMap = () => {
    if (picking) {
      cancelMapPick();
      announce("Placing on the map cancelled.");
      return;
    }
    usePlan.getState().cancelDrawing();
    startMapPick({
      id: pickId,
      label: "Assembly",
      onPick: ([lon, lat]) => {
        setCoords(`${lat.toFixed(6)}, ${lon.toFixed(6)}`);
        setError(null);
        announce(`Assembly placed at ${formatCoord(lon, lat)}.`);
      },
    });
    announce("Click the map to place the assembly, or focus the map and press Space for its centre. Escape cancels.");
  };

  const add = () => {
    const label = name.trim();
    if (!label) return setError("Enter a name for the assembly.");
    if (label.length > 120) return setError("Use a name of at most 120 characters.");
    if (entries.length >= MAX_ASSEMBLIES) return setError(`At most ${MAX_ASSEMBLIES} assemblies can be checked at once.`);
    let geometry: AssemblyInput["geometry"] | null = null;
    if (placeMode === "coordinates") {
      const p = parseLatLon(coords);
      if (!p) return setError("Enter the place as latitude, longitude in decimal degrees, for example 12.9010, 77.5055.");
      if (radius !== null && !(radius > 0 && radius <= 5_000)) return setError(localizeUnits("The radius must be between 1 m and 5 km, or empty.", units));
      geometry = radius ? circlePolygon(p.lon, p.lat, radius) : { type: "Point", coordinates: [p.lon, p.lat] };
    } else {
      const feature = placeFeatures.find((f) => String(f.properties._index) === featureIndex);
      if (!feature || !feature.geometry) return setError("Choose a point or polygon of the dataset.");
      geometry = feature.geometry;
    }
    if (people !== null && !(Number.isInteger(people) && people >= 0 && people <= 100_000_000)) {
      return setError("Expected people must be a whole number from 0 to 100,000,000, or empty.");
    }
    const startUtc = localToUtc(start);
    const endUtc = localToUtc(end);
    if (startUtc && endUtc && endUtc <= startUtc) return setError("The end of the time window must be after its start.");
    onChange([...entries, { name: label, geometry, expected_people: people, start_utc: startUtc, end_utc: endUtc }]);
    announce(`Added ${label}. ${entries.length + 1} assemblies to check.`);
    if (useMapPick.getState().request?.id === pickId) cancelMapPick();
    setError(null);
    setName("");
    setCoords("");
    setRadius(null);
    setPeople(null);
    setStart("");
    setEnd("");
    setFieldsKey((k) => k + 1);
    nameRef.current?.focus();
  };

  // Enter in a field of the add form adds the entry (after the field has committed its value)
  // instead of submitting the module form, which would run the analysis.
  const addRef = React.useRef(add);
  addRef.current = add;
  const [addRequest, setAddRequest] = React.useState(0);
  React.useEffect(() => {
    if (addRequest > 0) addRef.current();
  }, [addRequest]);
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Enter" || e.defaultPrevented || (e.target as HTMLElement).tagName !== "INPUT") return;
    e.preventDefault();
    setAddRequest((n) => n + 1);
  };

  const remove = (index: number) => {
    const removed = entries[index];
    onChange(entries.filter((_, i) => i !== index));
    if (removed) announce(`Removed ${removed.name}.`);
    nameRef.current?.focus();
  };

  return (
    <ModuleSection title="Assemblies and events" aside={entries.length > 0 ? <span className="text-xs text-fg-muted">{entries.length} added</span> : null}>
      <p className="text-xs text-fg-muted">
        Stadiums, sports centres, theme parks and markets in the data pack are checked automatically. Add events it cannot
        know; all are checked within 1 km of the operational volume (SORA 2.5 Step 8).
      </p>
      {entries.length > 0 ? (
        <ul className="space-y-1.5" aria-label="Assemblies to check">
          {entries.map((entry, i) => (
            <li key={`${entry.name}-${i}`} className="flex items-start gap-2 rounded border border-border p-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{entry.name}</p>
                <p className="text-xs text-fg-muted">{describe(entry)}</p>
              </div>
              <IconButton label={`Remove ${entry.name}`} icon={<Trash2 />} size="icon-sm" onClick={() => remove(i)} />
            </li>
          ))}
        </ul>
      ) : null}

      {disabledReason ? (
        <p className="text-xs text-fg-muted">{disabledReason}</p>
      ) : (
        <div role="group" aria-labelledby={`${ids}-add`} onKeyDown={onKeyDown} className="space-y-3 rounded border border-dashed border-border p-2.5">
          <p id={`${ids}-add`} className="text-sm font-medium">
            Add an assembly
          </p>
          <Field label="Name" hint="For example: Cup final, harvest fair, weekly market.">
            {(p) => <Input {...p} ref={nameRef} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />}
          </Field>
          <div className="space-y-1.5">
            <p className="text-sm font-medium" id={`${ids}-place`}>
              Place
            </p>
            <ToggleGroup
              type="single"
              value={placeMode}
              onValueChange={(v) => v && setPlaceMode(v as typeof placeMode)}
              aria-labelledby={`${ids}-place`}
            >
              <ToggleGroupItem value="coordinates">Coordinates</ToggleGroupItem>
              <ToggleGroupItem value="feature" disabled={placeFeatures.length === 0}>
                Dataset feature
              </ToggleGroupItem>
            </ToggleGroup>
          </div>
          {placeMode === "coordinates" ? (
            <>
              <Field label="Latitude, longitude" hint="Decimal degrees, for example 12.9010, 77.5055, or place it on the map.">
                {(p) => (
                  <Input {...p} value={coords} inputMode="decimal" autoComplete="off" onChange={(e) => setCoords(e.target.value)} />
                )}
              </Field>
              <Button type="button" variant={picking ? "secondary" : "outline"} size="sm" aria-pressed={picking} onClick={placeOnMap}>
                <Crosshair aria-hidden />
                {picking ? "Click the map… (Esc cancels)" : "Place on map"}
              </Button>
              {picking ? (
                <p className="rounded border border-accent/60 bg-accent/10 px-2 py-1.5 text-xs">
                  Click the map to place the assembly, or focus the map and press Space for its centre. Escape cancels.
                </p>
              ) : null}
              <NumField
                key={`radius-${fieldsKey}`}
                label="Radius"
                unit="m"
                value={radius}
                onChange={setRadius}
                min={1}
                max={5000}
                optional
                hint="Empty: a point. With a radius the assembly is a circle."
              />
            </>
          ) : (
            <div className="space-y-1.5">
              <label htmlFor={`${ids}-feature`} className="text-sm font-medium">
                Point or polygon of the dataset
              </label>
              <Select value={featureIndex} onValueChange={setFeatureIndex}>
                <SelectTrigger id={`${ids}-feature`}>
                  <SelectValue placeholder="Choose a feature" />
                </SelectTrigger>
                <SelectContent>
                  {placeFeatures.map((f) => (
                    <SelectItem key={f.properties._index} value={String(f.properties._index)}>
                      {featureName(f.properties)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {featureInFlightGeography ? (
                <p className="text-xs text-warn">
                  {selectedFeatures.length === 0
                    ? "With no feature selected, this one is also part of the flight geography. Select only the operation's features so the assembly is measured from the volume, not inside it."
                    : "This feature is selected, so it is also part of the flight geography. Unselect it so the assembly is measured from the volume, not inside it."}
                </p>
              ) : null}
            </div>
          )}
          <NumField
            key={`people-${fieldsKey}`}
            label="Expected people"
            value={people}
            onChange={setPeople}
            min={0}
            max={100_000_000}
            integer
            optional
            hint="Empty: capacity unknown. Bands: < 40,000 · 40,000–400,000 · > 400,000."
          />
          {/* One column: a date-and-time input needs the whole width of the side panel. */}
          <div className="space-y-3">
            <Field label="Starts" hint="Your local time (optional).">
              {(p) => <Input {...p} type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />}
            </Field>
            <Field label="Ends" hint="Your local time (optional).">
              {(p) => <Input {...p} type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />}
            </Field>
          </div>
          {error ? (
            <p role="alert" className="text-xs text-danger">
              {error}
            </p>
          ) : null}
          <Button type="button" variant="secondary" size="sm" className="w-full" onClick={add}>
            <Plus aria-hidden />
            Add assembly
          </Button>
        </div>
      )}
    </ModuleSection>
  );
}
