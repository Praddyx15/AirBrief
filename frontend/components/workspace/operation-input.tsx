"use client";

/**
 * Plan without a file: the workspace map's top-left toolbar (`MapView`'s `topLeft` slot).
 *
 * - "New operation" (popover): "Go to" search (typed coordinates first, then the data pack's
 *   aerodromes by ICAO / IATA code, name or town), Pin, Line, Area, Coordinates…, Paste… and My
 *   location. Phones (< 768 px) get Pin and My location only, and the draft card runs an
 *   airspace check directly (module forms are not shown on phones).
 * - Below it, one card for the current step: the drawing in progress (instructions, point count,
 *   ≈ length or area, Add map centre / Undo / Finish / Cancel), the unsaved draft (summary, the
 *   pin radius, how text was read, Use this operation / Discard), a location error, or the
 *   "Go to" result.
 * - `?plan=pin|line|area|coordinates|paste` in the URL (the landing page's "Check a location")
 *   starts that step once and is then removed from the URL.
 *
 * The draft becomes an ordinary dataset when saved (`plan-actions.ts` `useSaveOperation`).
 * State lives in `lib/plan-store.ts`; map clicks and keys in `plan-map.ts`.
 */

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Check,
  ClipboardPaste,
  Crosshair,
  Keyboard,
  LocateFixed,
  MapPin,
  MapPinPlus,
  Pentagon,
  Plane,
  Spline,
  Undo2,
  X,
} from "lucide-react";
import { DEMO_MODE, describeError } from "@/lib/api";
import { formatLatLon, type LonLat } from "@/lib/coords";
import { formatArea, formatLength, formatNumber, plural } from "@/lib/format";
import {
  MAX_PIN_RADIUS_M,
  MIN_PIN_RADIUS_M,
  PIN_CIRCLE_AREA_RATIO,
  PIN_CIRCLE_SIDES,
  PIN_RADIUS_PRESETS_M,
  draftGeometry,
  pathLengthM,
  ringAreaM2,
  type PlanDraft,
  type PlanTool,
} from "@/lib/plan";
import { MIN_VERTICES, usePlan } from "@/lib/plan-store";
import { usePlaces } from "@/lib/queries";
import { useUnits } from "@/lib/store";
import type { PlaceResult } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { ErrorState } from "@/components/ui/error-state";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { LEGEND_BOTTOM, OVERLAY_GAP, useMapObstacle, useMapOverlay } from "@/components/map/map-overlay";
import type { MapViewHandle } from "@/components/map/map-loader";
import { IssueList, NumField } from "@/components/modules/shared";
import { announce } from "./live-region";
import {
  TOOL_HELP,
  TOOL_TITLE,
  draftBounds,
  draftProblem,
  focusWorkspaceMap,
  goToPlace,
  saveErrorProps,
  startPlanTool,
  useLocate,
  useSaveOperation,
} from "./plan-actions";
import { CoordinatesDialog, DRAFT_HEADING_ID, PasteDialog } from "./plan-dialogs";
import { cancelPlanDrawing, finishPlanDrawing, placePlanVertex, undoPlanVertex } from "./plan-map";

const TOOL_ICON: Record<PlanTool, React.ReactElement> = {
  pin: <MapPin aria-hidden />,
  line: <Spline aria-hidden />,
  area: <Pentagon aria-hidden />,
};

/** Steps `?plan=` may ask for. */
const PLAN_INTENTS = ["pin", "line", "area", "coordinates", "paste"] as const;
type PlanIntent = (typeof PLAN_INTENTS)[number];

function isPlanIntent(value: string | null): value is PlanIntent {
  return (PLAN_INTENTS as readonly (string | null)[]).includes(value);
}

/** Card frame shared by the toolbar's step cards. */
const CARD = "w-72 max-w-full space-y-2.5 rounded border bg-surface/95 p-3 text-xs text-fg";

function round8(v: number): number {
  return Math.round(v * 1e8) / 1e8;
}

/**
 * Tallest the step card may be (px) on a map `height` px tall, or null before the map is
 * measured: the top gap, the toolbar row and the gaps above, and below it the legend's header
 * row standing `LEGEND_BOTTOM` above the map's bottom edge (see `map-overlay.tsx`).
 */
export function planCardMaxHeight(height: number, coarse: boolean, scale: number): number | null {
  if (height <= 0) return null;
  const toolbar = coarse ? 50 : 38; // a size-sm button (32 / 44 px) in its bordered group
  const legendHeader = (coarse ? 44 : 28) + 3;
  const reserved = (3 * OVERLAY_GAP + LEGEND_BOTTOM + toolbar + legendHeader) * scale;
  return Math.max(120, Math.floor(height - reserved));
}

/**
 * Start the step `?plan=` asks for, once, and drop the parameter from the URL. Phones have no
 * dialogs or line/area drawing, so they start the pin instead.
 */
function usePlanIntent(compact: boolean): void {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const intent = searchParams.get("plan");
  React.useEffect(() => {
    if (intent === null) return;
    const params = new URLSearchParams(window.location.search);
    params.delete("plan");
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    if (!isPlanIntent(intent)) return;
    if (compact) startPlanTool("pin");
    else if (intent === "coordinates" || intent === "paste") usePlan.getState().openDialog(intent, "pin");
    else startPlanTool(intent);
    // `compact` is read once, with the intent: a later breakpoint change must not restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intent, pathname, router]);
}

export interface OperationInputProps {
  mapRef: React.RefObject<MapViewHandle | null>;
  /** phones: Pin and My location, and the draft card runs an airspace check */
  compact?: boolean;
}

export function OperationInput({ mapRef, compact = false }: OperationInputProps) {
  const obstacleRef = useMapObstacle("plan");
  const overlay = useMapOverlay();
  const cardMaxHeight = planCardMaxHeight(overlay.height, overlay.coarse, overlay.remScale);
  const drawing = usePlan((s) => s.drawing);
  const draft = usePlan((s) => s.draft);
  const goTo = usePlan((s) => s.goTo);
  const location = useLocate(mapRef);
  usePlanIntent(compact);

  /** A dialog produced a draft: show it on the map. */
  const showDraft = React.useCallback(
    (next: PlanDraft) => {
      usePlan.getState().setDraft(next);
      const bounds = draftBounds(next, usePlan.getState().pinRadiusM);
      if (bounds) mapRef.current?.fitBounds(bounds, { maxZoom: 17 });
      announce(`${next.name} shown on the map. Use this operation to save it and run a module on it.`);
    },
    [mapRef],
  );

  let card: React.ReactNode = null;
  if (drawing) card = <DrawingCard tool={drawing} mapRef={mapRef} compact={compact} />;
  else if (draft) card = <DraftCard draft={draft} compact={compact} />;
  else if (location.error) {
    card = (
      <ErrorState
        compact
        className="w-72 max-w-full bg-surface"
        title="Location not available"
        message={location.error.message}
        fix={location.error.fix}
        actions={
          <>
            {compact ? null : (
              <Button size="sm" variant="secondary" onClick={() => usePlan.getState().openDialog("coordinates", "pin")}>
                <Keyboard aria-hidden />
                Type coordinates
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={location.clearError}>
              Close
            </Button>
          </>
        }
      />
    );
  } else if (goTo) card = <GoToCard compact={compact} />;

  return (
    <div ref={obstacleRef} data-map-overlay="plan" data-print="hide" className="pointer-events-auto flex max-w-full flex-col items-start gap-2">
      {compact ? (
        <div role="group" aria-label="Plan an operation" className="flex gap-0.5 rounded border border-border bg-surface/95 p-0.5">
          <Button size="sm" variant="ghost" aria-pressed={drawing === "pin"} onClick={() => startPlanTool("pin")}>
            <MapPin aria-hidden />
            Pin
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void location.locate()} disabled={location.pending} aria-busy={location.pending || undefined}>
            <LocateFixed aria-hidden />
            {location.pending ? "Locating…" : "My location"}
          </Button>
        </div>
      ) : (
        <PlanMenu onLocate={() => void location.locate()} locating={location.pending} />
      )}
      {/* The step card scrolls rather than run into the legend's header (bottom-left) on short maps. */}
      {card ? (
        <div className="max-w-full overflow-y-auto overscroll-contain" style={cardMaxHeight !== null ? { maxHeight: cardMaxHeight } : undefined}>
          {card}
        </div>
      ) : null}
      {compact ? null : (
        <>
          <CoordinatesDialog onDraft={showDraft} />
          <PasteDialog onDraft={showDraft} />
        </>
      )}
    </div>
  );
}

function PlanMenu({ onLocate, locating }: { onLocate: () => void; locating: boolean }) {
  const [open, setOpen] = React.useState(false);
  /** what to do once the popover has closed (and whether it takes focus elsewhere) */
  const next = React.useRef<{ run: () => void; movesFocus: boolean } | null>(null);
  const pick = (run: () => void, movesFocus = false) => {
    next.current = { run, movesFocus };
    setOpen(false);
  };

  return (
    <div className="rounded border border-border bg-surface/95">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button size="sm" variant="ghost">
            <MapPinPlus aria-hidden />
            New operation
          </Button>
        </PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="start"
          aria-label="New operation"
          className="w-80 space-y-3"
          data-print="hide"
          onCloseAutoFocus={(e) => {
            const step = next.current;
            next.current = null;
            if (!step) return;
            if (step.movesFocus) e.preventDefault();
            // After the trigger has its focus back, so a dialog opened next returns focus to it.
            window.requestAnimationFrame(step.run);
          }}
        >
          <PlaceSearch onPick={(place) => pick(() => goToPlace(place))} />
          <div role="group" aria-label="Define the operation" className="grid grid-cols-2 gap-1.5">
            {(["pin", "line", "area"] as const).map((tool) => (
              <Button key={tool} size="sm" variant="secondary" className="justify-start" onClick={() => pick(() => startPlanTool(tool), true)}>
                {TOOL_ICON[tool]}
                {tool === "pin" ? "Pin" : tool === "line" ? "Line" : "Area"}
              </Button>
            ))}
            <Button size="sm" variant="secondary" className="justify-start" onClick={() => pick(() => usePlan.getState().openDialog("coordinates", "pin"))}>
              <Keyboard aria-hidden />
              Coordinates…
            </Button>
            <Button size="sm" variant="secondary" className="justify-start" onClick={() => pick(() => usePlan.getState().openDialog("paste"))}>
              <ClipboardPaste aria-hidden />
              Paste…
            </Button>
            <Button size="sm" variant="secondary" className="justify-start" disabled={locating} onClick={() => pick(onLocate)}>
              <LocateFixed aria-hidden />
              My location
            </Button>
          </div>
          <p className="text-xs text-fg-muted">
            Saving uploads the operation as a GeoJSON dataset to this AirBrief server, like any other file; your location
            is not sent anywhere else.
          </p>
        </PopoverContent>
      </Popover>
    </div>
  );
}

/** Coordinates or a data-pack aerodrome (`GET /api/places/`), as a combobox. */
function PlaceSearch({ onPick }: { onPick: (place: PlaceResult) => void }) {
  const [query, setQuery] = React.useState("");
  const searching = query.trim().length >= 2;
  const places = usePlaces(query, { enabled: searching });
  const results = searching ? (places.data ?? []) : [];

  return (
    <Command shouldFilter={false} loop label="Go to coordinates or an aerodrome" className="rounded border border-border bg-bg">
      <CommandInput value={query} onValueChange={setQuery} placeholder="Coordinates, ICAO, IATA or name" />
      {/* Always rendered (hidden until a search starts): cmdk's input names this list in its
          aria-controls, and that reference must point at an element that exists. */}
      <CommandList className="max-h-56" hidden={!searching}>
        {searching ? (
          <>
            {places.isError ? (
              <CommandEmpty>{describeError(places.error).message}</CommandEmpty>
            ) : (places.isFetching || places.data === undefined) && results.length === 0 ? (
              // cmdk's Empty (role=presentation) is valid inside the listbox; a status <p> is not.
              <CommandEmpty>Searching…</CommandEmpty>
            ) : (
              <CommandEmpty>No coordinates or aerodromes match.</CommandEmpty>
            )}
            {results.map((place) => (
              <CommandItem key={`${place.kind}:${place.ident ?? place.label}`} value={`${place.kind}:${place.ident ?? place.label}`} onSelect={() => onPick(place)}>
                {place.kind === "aerodrome" ? <Plane aria-hidden /> : <MapPin aria-hidden />}
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{place.label}</span>
                  <span className="block truncate text-xs text-fg-muted">{place.detail}</span>
                </span>
              </CommandItem>
            ))}
          </>
        ) : null}
      </CommandList>
    </Command>
  );
}

function DrawingCard({ tool, mapRef, compact }: { tool: PlanTool; mapRef: React.RefObject<MapViewHandle | null>; compact: boolean }) {
  const vertices = usePlan((s) => s.vertices);
  const units = useUnits();
  const count = vertices.length;
  const measure =
    tool === "line" && count >= 2
      ? `Length ≈ ${formatLength(pathLengthM(vertices), units)}`
      : tool === "area" && count >= 3
        ? `Area ≈ ${formatArea(ringAreaM2(vertices), units)}`
        : null;

  const addCentre = () => {
    const c = mapRef.current?.getMap()?.getCenter().wrap();
    if (c) placePlanVertex([round8(c.lng), round8(c.lat)] as LonLat);
  };
  const finish = () => {
    finishPlanDrawing();
    if (usePlan.getState().draft) window.requestAnimationFrame(() => document.getElementById(DRAFT_HEADING_ID)?.focus());
  };
  const cancel = () => {
    cancelPlanDrawing();
    focusWorkspaceMap();
  };

  return (
    <section aria-label={`${TOOL_TITLE[tool]}: drawing`} className={cn(CARD, "border-accent/60")}>
      <p className="flex items-center justify-between gap-2 text-sm font-medium">
        <span className="flex items-center gap-1.5 [&_svg]:size-4 [&_svg]:text-accent">
          {TOOL_ICON[tool]}
          {TOOL_TITLE[tool]}
        </span>
        {tool === "pin" ? null : <span className="num text-xs font-normal text-fg-muted">{plural(count, "point")}</span>}
      </p>
      <p className="text-fg-muted">{compact && tool === "pin" ? "Tap the map where you want the pin." : TOOL_HELP[tool]}</p>
      {measure ? <p className="num">{measure} (preview)</p> : null}
      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" variant="secondary" onClick={addCentre}>
          <Crosshair aria-hidden />
          {tool === "pin" ? "Pin the map centre" : "Add the map centre"}
        </Button>
        {tool === "pin" ? null : (
          <>
            <Button size="sm" variant="ghost" onClick={undoPlanVertex} disabled={count === 0}>
              <Undo2 aria-hidden />
              Undo
            </Button>
            <Button size="sm" onClick={finish} disabled={count < MIN_VERTICES[tool]}>
              <Check aria-hidden />
              Finish
            </Button>
          </>
        )}
        <Button size="sm" variant="ghost" onClick={cancel}>
          <X aria-hidden />
          Cancel
        </Button>
      </div>
    </section>
  );
}

function SummaryRow({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-fg-muted">{term}</dt>
      <dd className="num min-w-0 break-words text-fg">{children}</dd>
    </>
  );
}

function DraftSummary({ draft, radiusM }: { draft: PlanDraft; radiusM: number | null }) {
  const units = useUnits();
  const at = draft.vertices[0];
  const geometry = draftGeometry(draft);
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
      {draft.kind === "pin" && at ? (
        <>
          <SummaryRow term="Position">{formatLatLon(at[0], at[1])}</SummaryRow>
          {draft.accuracyM !== null ? <SummaryRow term="Accuracy">± {formatLength(draft.accuracyM, units)} (95 %)</SummaryRow> : null}
          {radiusM !== null ? (
            <SummaryRow term="Circle area">≈ {formatArea(Math.PI * radiusM * radiusM * PIN_CIRCLE_AREA_RATIO, units)}</SummaryRow>
          ) : null}
        </>
      ) : null}
      {draft.kind === "line" ? (
        <>
          <SummaryRow term="Points">{formatNumber(draft.vertices.length)}</SummaryRow>
          <SummaryRow term="Length">≈ {formatLength(pathLengthM(draft.vertices), units)}</SummaryRow>
        </>
      ) : null}
      {draft.kind === "area" ? (
        <>
          <SummaryRow term="Corners">{formatNumber(draft.vertices.length)}</SummaryRow>
          <SummaryRow term="Area">≈ {formatArea(ringAreaM2(draft.vertices), units)}</SummaryRow>
          <SummaryRow term="Perimeter">≈ {formatLength(pathLengthM(draft.vertices, true), units)}</SummaryRow>
        </>
      ) : null}
      {draft.kind === "geometry" && geometry ? (
        <SummaryRow term="Geometry">
          {geometry.type === "GeometryCollection" ? plural(geometry.geometries.length, "part") : geometry.type}
          {draft.sourceText ? " (features kept as pasted)" : ""}
        </SummaryRow>
      ) : null}
    </dl>
  );
}

function DraftCard({ draft, compact }: { draft: PlanDraft; compact: boolean }) {
  const storedRadius = usePlan((s) => s.pinRadiusM);
  const setPinRadius = usePlan((s) => s.setPinRadius);
  const [radius, setRadius] = React.useState<number | null>(storedRadius);
  const { save, saving, error, reset } = useSaveOperation();

  React.useEffect(() => setRadius(storedRadius), [storedRadius]);
  const onRadius = (v: number | null) => {
    setRadius(v);
    reset();
    if (v !== null && v >= MIN_PIN_RADIUS_M && v <= MAX_PIN_RADIUS_M) setPinRadius(v);
  };
  const radiusValid = radius !== null && radius >= MIN_PIN_RADIUS_M && radius <= MAX_PIN_RADIUS_M;
  const problem = draft.kind === "pin" && !radiusValid ? "Enter a radius first." : draftProblem(draft, storedRadius);
  const blocked = DEMO_MODE ? "The public demo is read-only: run AirBrief yourself to save an operation." : problem;
  const discard = () => {
    usePlan.getState().setDraft(null);
    reset();
    announce("Operation discarded.");
    focusWorkspaceMap();
  };

  return (
    <section aria-labelledby={DRAFT_HEADING_ID} className={cn(CARD, "border-border")}>
      <div className="flex items-baseline justify-between gap-2">
        <h2 id={DRAFT_HEADING_ID} tabIndex={-1} className="text-sm font-semibold outline-none">
          {draft.name}
        </h2>
        <span className="text-fg-muted">Not saved yet</span>
      </div>
      <DraftSummary draft={draft} radiusM={radiusValid ? radius : null} />
      {draft.kind === "pin" ? (
        <NumField
          label="Radius"
          unit="m"
          value={radius}
          onChange={onRadius}
          min={MIN_PIN_RADIUS_M}
          max={MAX_PIN_RADIUS_M}
          presets={{ metric: PIN_RADIUS_PRESETS_M, label: "Radius presets" }}
          hint={`Saved as the point and a ${PIN_CIRCLE_SIDES}-sided polygon of this radius, so modules that need an area can use it.`}
        />
      ) : null}
      {draft.notes && draft.notes.length > 0 ? <IssueList issues={draft.notes} title="How it was read" /> : null}
      {error ? <ErrorState compact {...saveErrorProps(error)} /> : null}
      <div className="flex flex-wrap gap-1.5">
        {compact ? (
          <Button size="sm" onClick={() => void save({ checkAirspace: true })} disabled={Boolean(blocked) || saving} aria-busy={saving || undefined}>
            <Plane aria-hidden />
            {saving ? "Saving…" : "Check airspace"}
          </Button>
        ) : (
          <Button size="sm" onClick={() => void save()} disabled={Boolean(blocked) || saving} aria-busy={saving || undefined}>
            <Check aria-hidden />
            {saving ? "Saving…" : "Use this operation"}
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={discard} disabled={saving}>
          Discard
        </Button>
      </div>
      {blocked ? <p className="text-fg-muted">{blocked}</p> : null}
      {compact && !blocked ? (
        <p className="text-fg-muted">Saves the pin as a dataset and checks the airspace up to 120 m above ground.</p>
      ) : null}
    </section>
  );
}

function GoToCard({ compact }: { compact: boolean }) {
  const goTo = usePlan((s) => s.goTo);
  if (!goTo) return null;
  const pinHere = () => {
    usePlan.getState().setDraft({
      kind: "pin",
      vertices: [[goTo.lon, goTo.lat]],
      geometry: null,
      sourceText: null,
      source: "goto",
      accuracyM: null,
      name: "Pin",
    });
    usePlan.getState().clearGoTo();
    window.requestAnimationFrame(() => document.getElementById(DRAFT_HEADING_ID)?.focus());
  };
  return (
    <section aria-label="Go to result" className={cn(CARD, "border-border")}>
      <p className="flex items-start gap-2">
        <span aria-hidden className="mt-0.5 [&_svg]:size-4 [&_svg]:text-accent">
          {goTo.kind === "aerodrome" ? <Plane /> : <MapPin />}
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-medium">{goTo.label}</span>
          <span className="block text-fg-muted">{goTo.detail}</span>
        </span>
      </p>
      <div className="flex flex-wrap gap-1.5">
        {goTo.kind === "coordinates" ? (
          <Button size="sm" variant="secondary" onClick={pinHere}>
            <MapPin aria-hidden />
            Pin here
          </Button>
        ) : (
          <Button size="sm" variant="secondary" onClick={() => startPlanTool("pin")}>
            <MapPin aria-hidden />
            {compact ? "Drop a pin" : "Drop a pin nearby"}
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={() => usePlan.getState().clearGoTo()}>
          Clear
        </Button>
      </div>
    </section>
  );
}
