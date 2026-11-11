"use client";

/**
 * The two text-entry dialogs of plan mode (open state in `lib/plan-store.ts`):
 *
 * - Coordinates: one point per line in decimal degrees, hemispheres or DMS, read in the browser
 *   by `lib/coords.ts` (the same rules as the API's `airbrief_core/geo/parse.py`) as you type,
 *   becoming a pin, a line or an area. This is also the keyboard alternative to drawing.
 * - Paste: GeoJSON, WKT or coordinates read by `POST /api/geometry/parse/` (the static demo reads
 *   GeoJSON and coordinates in the browser). A single point becomes a pin.
 *
 * Neither saves anything: the result becomes the plan draft, which the map card saves.
 */

import * as React from "react";
import { ArrowLeftRight, ClipboardPaste, Keyboard } from "lucide-react";
import { DEMO_MODE } from "@/lib/api";
import { formatLatLon, parseCoordinateText, swapCoordinateText, type LonLat } from "@/lib/coords";
import { formatNumber, plural } from "@/lib/format";
import type { PlanDraft, PlanTool } from "@/lib/plan";
import { usePlan } from "@/lib/plan-store";
import { useParseGeometry } from "@/lib/queries";
import type { Issue } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ErrorState } from "@/components/ui/error-state";
import { Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { IssueList } from "@/components/modules/shared";

/** Largest text the paste dialog sends (the API reads at most 100 kB). */
const MAX_PASTE_BYTES = 100_000;

const SHAPE_LABEL: Record<PlanTool, string> = { pin: "Pin", line: "Line", area: "Area" };

/** Where focus goes once a dialog has produced a draft: the draft card's heading. */
export const DRAFT_HEADING_ID = "plan-draft-heading";

function focusDraftCard(): void {
  window.requestAnimationFrame(() => document.getElementById(DRAFT_HEADING_ID)?.focus());
}

function distinct(points: readonly LonLat[]): LonLat[] {
  const out: LonLat[] = [];
  for (const p of points) {
    const last = out[out.length - 1];
    if (!last || last[0] !== p[0] || last[1] !== p[1]) out.push(p);
  }
  return out;
}

function notesAsIssues(notes: readonly { code: string; message: string; severity: Issue["severity"] }[]): Issue[] {
  return notes.map((n) => ({ code: n.code, message: n.message, severity: n.severity, feature_index: null }));
}

/** The draft typed points make, or why they cannot make one. */
export function typedDraft(points: readonly LonLat[], shape: PlanTool, notes: readonly Issue[]): { draft: PlanDraft } | { error: string } {
  const pts = distinct(points);
  const base = { geometry: null, sourceText: null, source: "coordinates" as const, accuracyM: null, notes };
  if (shape === "pin") {
    if (pts.length !== 1) return { error: `A pin is one point; ${pts.length} were typed. Choose Line or Area for several points.` };
    return { draft: { ...base, kind: "pin", vertices: pts, name: "Pin" } };
  }
  if (shape === "line") {
    if (pts.length < 2) return { error: "A line needs at least two different points." };
    return { draft: { ...base, kind: "line", vertices: pts, name: "Typed line" } };
  }
  const first = pts[0];
  const last = pts[pts.length - 1];
  const ring = first && last && pts.length > 1 && first[0] === last[0] && first[1] === last[1] ? pts.slice(0, -1) : pts;
  if (new Set(ring.map((p) => `${p[0]},${p[1]}`)).size < 3) return { error: "An area needs at least three different points." };
  return { draft: { ...base, kind: "area", vertices: ring, name: "Typed area" } };
}

export function CoordinatesDialog({ onDraft }: { onDraft: (draft: PlanDraft) => void }) {
  const open = usePlan((s) => s.dialog === "coordinates");
  const initialShape = usePlan((s) => s.dialogTool);
  const closeDialog = usePlan((s) => s.closeDialog);
  const [text, setText] = React.useState("");
  const [shape, setShape] = React.useState<PlanTool>(initialShape);
  const [attempted, setAttempted] = React.useState(false);
  const produced = React.useRef(false);
  const textRef = React.useRef<HTMLTextAreaElement>(null);
  const id = React.useId();

  React.useEffect(() => {
    if (open) {
      setShape(initialShape);
      setAttempted(false);
      produced.current = false;
    }
  }, [open, initialShape]);

  const parsed = React.useMemo(() => (text.trim() ? parseCoordinateText(text) : null), [text]);
  const outcome = parsed?.ok ? typedDraft(parsed.points, shape, notesAsIssues(parsed.notes)) : null;
  const error = !parsed
    ? "Type at least one point."
    : !parsed.ok
      ? parsed.error
      : outcome && "error" in outcome
        ? outcome.error
        : null;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setAttempted(true);
    if (!outcome || "error" in outcome) {
      // Back to the text, whose description now carries the error (read out with the field).
      textRef.current?.focus();
      return;
    }
    produced.current = true;
    onDraft(outcome.draft);
    closeDialog();
  };

  const preview = parsed?.ok
    ? parsed.points.length === 1
      ? `Reads as ${formatLatLon(parsed.points[0]?.[0] ?? 0, parsed.points[0]?.[1] ?? 0)}.`
      : `Reads as ${formatNumber(parsed.points.length)} points, the first at ${formatLatLon(parsed.points[0]?.[0] ?? 0, parsed.points[0]?.[1] ?? 0)}.`
    : null;

  return (
    <Dialog open={open} onOpenChange={(o) => (o ? undefined : closeDialog())}>
      <DialogContent
        className="max-w-md"
        onCloseAutoFocus={(e) => {
          if (!produced.current) return;
          e.preventDefault();
          focusDraftCard();
        }}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Keyboard aria-hidden className="size-4 text-accent" />
            Type coordinates
          </DialogTitle>
          <DialogDescription>
            One point per line (or separated by ;), in decimal degrees such as 13.1979, 77.7063, with hemispheres such as
            13.1979 N 77.7063 E, or in degrees, minutes and seconds such as 13°11′52.4″N 77°42′22.7″E.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={submit} aria-label="Type coordinates" noValidate>
          <div className="space-y-1.5">
            <span id={`${id}-shape`} className="text-sm font-medium">
              Shape
            </span>
            <ToggleGroup
              type="single"
              aria-labelledby={`${id}-shape`}
              value={shape}
              onValueChange={(v) => {
                if (v === "pin" || v === "line" || v === "area") setShape(v);
              }}
            >
              {(Object.keys(SHAPE_LABEL) as PlanTool[]).map((s) => (
                <ToggleGroupItem key={s} value={s}>
                  {SHAPE_LABEL[s]}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${id}-text`}>Coordinates</Label>
            <Textarea
              ref={textRef}
              id={`${id}-text`}
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={5}
              spellCheck={false}
              autoComplete="off"
              autoFocus
              placeholder={"13.1979, 77.7063"}
              className="font-mono text-sm"
              aria-invalid={attempted && error ? true : undefined}
              aria-describedby={`${id}-status`}
            />
            <p id={`${id}-status`} className={attempted && error ? "text-xs text-danger" : "text-xs text-fg-muted"}>
              {attempted && error ? error : (preview ?? "Latitude first, as map apps copy it; a value beyond ±90° is read as the longitude.")}
            </p>
          </div>
          {parsed?.ok && parsed.notes.length > 0 ? <IssueList issues={notesAsIssues(parsed.notes)} title="How it was read" /> : null}
          {parsed?.ok && parsed.notes.some((n) => n.code === "ambiguous_order") ? (
            <Button type="button" size="sm" variant="secondary" onClick={() => setText((t) => swapCoordinateText(t))}>
              <ArrowLeftRight aria-hidden />
              Swap latitude and longitude
            </Button>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={closeDialog}>
              Cancel
            </Button>
            <Button type="submit">Show on the map</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function isFeatureDocument(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{")) return false;
  try {
    const doc = JSON.parse(trimmed) as { type?: unknown };
    return doc.type === "Feature" || doc.type === "FeatureCollection";
  } catch {
    return false;
  }
}

export function PasteDialog({ onDraft }: { onDraft: (draft: PlanDraft) => void }) {
  const open = usePlan((s) => s.dialog === "paste");
  const closeDialog = usePlan((s) => s.closeDialog);
  const parse = useParseGeometry();
  const [text, setText] = React.useState("");
  const [sizeError, setSizeError] = React.useState<string | null>(null);
  const produced = React.useRef(false);
  const textRef = React.useRef<HTMLTextAreaElement>(null);
  const id = React.useId();
  const { reset } = parse;

  React.useEffect(() => {
    if (open) {
      produced.current = false;
      setSizeError(null);
      reset();
    }
  }, [open, reset]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const bytes = new TextEncoder().encode(text).length;
    if (!text.trim()) {
      setSizeError("Paste GeoJSON, WKT or coordinates.");
      textRef.current?.focus();
      return;
    }
    if (bytes > MAX_PASTE_BYTES) {
      setSizeError(`The text is ${formatNumber(bytes / 1000, { maximumFractionDigits: 0 })} kB; at most 100 kB can be pasted. Upload it as a file instead.`);
      textRef.current?.focus();
      return;
    }
    setSizeError(null);
    parse.mutate(
      { text, shape: "auto" },
      {
        onSuccess: (res) => {
          const notes = res.issues;
          const base = { accuracyM: null, notes, source: "paste" as const };
          let draft: PlanDraft;
          if (res.geometry.type === "Point") {
            const [lon, lat] = res.geometry.coordinates as [number, number];
            draft = { ...base, kind: "pin", vertices: [[lon, lat]], geometry: null, sourceText: null, name: "Pin" };
          } else {
            draft = {
              ...base,
              kind: "geometry",
              vertices: [],
              geometry: res.geometry,
              // Features keep their names and properties: their text is uploaded as pasted.
              sourceText: res.source === "geojson" && isFeatureDocument(text) ? text : null,
              name: "Pasted geometry",
            };
          }
          produced.current = true;
          onDraft(draft);
          closeDialog();
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={(o) => (o ? undefined : closeDialog())}>
      <DialogContent
        className="max-w-lg"
        onCloseAutoFocus={(e) => {
          if (!produced.current) return;
          e.preventDefault();
          focusDraftCard();
        }}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ClipboardPaste aria-hidden className="size-4 text-accent" />
            Paste a geometry
          </DialogTitle>
          <DialogDescription>
            GeoJSON (a geometry, Feature or FeatureCollection in WGS84 longitude/latitude), WKT such as POLYGON ((77.48
            12.90, 77.50 12.90, 77.50 12.91, 77.48 12.90)), or coordinates one per line. Up to 100 kB and{" "}
            {formatNumber(10_000)} vertices; upload larger files.
            {DEMO_MODE ? " The static demo reads GeoJSON and coordinates only." : null}
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={submit} aria-label="Paste a geometry" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor={`${id}-text`}>GeoJSON, WKT or coordinates</Label>
            <Textarea
              ref={textRef}
              id={`${id}-text`}
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={8}
              spellCheck={false}
              autoComplete="off"
              autoFocus
              className="font-mono text-sm"
              aria-invalid={sizeError || parse.isError ? true : undefined}
              aria-describedby={sizeError ? `${id}-error` : undefined}
            />
            {sizeError ? (
              <p id={`${id}-error`} className="text-xs text-danger">
                {sizeError}
              </p>
            ) : (
              <p className="text-xs text-fg-muted">{text ? `${plural(new TextEncoder().encode(text).length, "byte")} of 100 kB` : "Nothing is saved until you choose Use this operation."}</p>
            )}
          </div>
          {parse.isError ? <ErrorState compact error={parse.error} title="The geometry could not be read" /> : null}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={closeDialog}>
              Cancel
            </Button>
            <Button type="submit" disabled={parse.isPending} aria-busy={parse.isPending || undefined}>
              {parse.isPending ? "Reading…" : "Read geometry"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
