"use client";

/**
 * The Ctrl/⌘+K command palette. Mount exactly one per page (the site header does this; the
 * workspace mounts its own when it does not use the site header). Open state lives in
 * `useUi().commandOpen`, so any button can open it with `setCommandOpen(true)`.
 */

import { Suspense, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  BookOpen,
  Box,
  Database,
  Download,
  FileStack,
  FileText,
  Github,
  Home,
  Layers,
  LayoutGrid,
  Map as MapIcon,
  MapPin,
  Maximize2,
  Moon,
  PanelBottom,
  PanelLeft,
  PanelRight,
  Plane,
  Ruler,
  Settings,
  Sun,
  Upload,
} from "lucide-react";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { toast } from "@/components/ui/toaster";
import { useWorkspaceBreakpoint } from "@/components/workspace/use-media-query";
import { describeError, fileExportUrl } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { MODULE_LIST, type ModuleId } from "@/lib/modules";
import { useFile, usePlaces, useRecentFiles, useLoadSample, useSamples } from "@/lib/queries";
import { moduleAnalysisKey, useSettings, useUi } from "@/lib/store";
import type { FileExportFormat } from "@/lib/types";
import { parseWorkspaceParams, workspaceHref } from "@/lib/url-state";
import { ModuleIcon } from "./module-icon";
import { REPO_URL, RUNBOOK_URL } from "./demo-banner";
import { useThemeMode } from "./theme-toggle";
import { isDemoSample, isFlightmdReportSample, sampleModule } from "@/components/upload/sample-utils";
import { goToPlace } from "@/components/workspace/plan-actions";
import { briefHref } from "@/components/report/brief-href";
import type { PlaceResult } from "@/lib/types";

const STATUS_TEXT = {
  PENDING: "Queued",
  PROCESSING: "Processing",
  COMPLETED: "Ready",
  FAILED: "Failed",
} as const;

/** Short search words per module (the long descriptions matched almost any query). */
const MODULE_KEYWORDS: Record<ModuleId, string[]> = {
  measure: ["area", "perimeter", "length", "crs"],
  airspace: ["zones", "ceiling", "height", "obstacles", "terrain", "aerodromes"],
  "ground-risk": ["sora", "population", "igrc", "density"],
  evtol: ["vertiports", "sites", "corridor", "air taxi"],
  shows: ["drone show", "choreography", "separation", "geofence"],
  survey: ["photogrammetry", "mapping", "gsd", "camera", "flight lines"],
  "flight-audit": ["flightmd", "flight log", "track", "exceedances"],
  environment: ["sunrise", "sunset", "twilight", "daylight", "declination", "magnetic", "wind", "power lines", "interference"],
  "line-of-sight": ["vlos", "viewshed", "observer", "visibility", "radio", "fresnel", "c2 link", "ground station"],
};

const EXPORTS: { format: FileExportFormat; label: string; keywords: string }[] = [
  { format: "csv", label: "Export measurements (CSV)", keywords: "csv spreadsheet download" },
  { format: "geojson", label: "Export features (GeoJSON)", keywords: "geojson gis download" },
  { format: "kml", label: "Export features (KML)", keywords: "kml google earth download" },
];

function words(text: string): string[] {
  return text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
}

/**
 * Strict palette filter: every typed word must start a word of the command's value or keywords
 * ("exp" finds "Export …", "3d" the 3D view), so unrelated queries such as "delete" show "No
 * matching commands" instead of fuzzy hits. Value matches rank above keyword-only matches.
 */
function paletteFilter(value: string, search: string, keywords?: string[]): number {
  const terms = words(search);
  if (terms.length === 0) return 1;
  const valueWords = words(value);
  const keywordWords = words((keywords ?? []).join(" "));
  let score = 1;
  for (const term of terms) {
    if (valueWords.some((w) => w.startsWith(term))) continue;
    if (!keywordWords.some((w) => w.startsWith(term))) return 0;
    score = 0.5;
  }
  return score;
}

/** Start a file download from a URL (the API sends it as an attachment). */
function download(url: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** The cmdk value of a place result (also used to highlight the first one when places arrive). */
function placeValue(p: PlaceResult): string {
  return `go to place ${p.label} ${p.detail}`;
}

function isPaletteShortcut(event: KeyboardEvent): boolean {
  return (event.key === "k" || event.key === "K") && (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey;
}

export function CommandPalette() {
  const open = useUi((s) => s.commandOpen);
  const setOpen = useUi((s) => s.setCommandOpen);
  // The highlighted item (controlled, so results that arrive later can be highlighted too).
  const [selected, setSelected] = useState("");
  useEffect(() => {
    if (!open) setSelected("");
  }, [open]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || !isPaletteShortcut(event)) return;
      event.preventDefault();
      const state = useUi.getState();
      state.setCommandOpen(!state.commandOpen);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title="Command palette"
      description="Go to a page, open a sample or dataset, switch module, control the map, or change a preference."
      commandProps={{ filter: paletteFilter, value: selected, onValueChange: setSelected }}
    >
      {open ? (
        <Suspense fallback={<CommandInput placeholder="Type a command or search…" disabled />}>
          <PaletteBody close={() => setOpen(false)} highlight={setSelected} />
        </Suspense>
      ) : null}
    </CommandDialog>
  );
}

function PaletteBody({ close, highlight }: { close: () => void; highlight: (value: string) => void }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const ws = parseWorkspaceParams(searchParams);

  const samples = useSamples();
  const files = useRecentFiles(8);
  const loadSample = useLoadSample();

  const { resolved, setMode, mounted } = useThemeMode();
  const units = useSettings((s) => s.units);
  const setUnits = useSettings((s) => s.setUnits);
  const basemap = useSettings((s) => s.basemap);
  const setBasemap = useSettings((s) => s.setBasemap);

  const inWorkspace = pathname === "/w/view";
  const breakpoint = useWorkspaceBreakpoint();
  const file = useFile(inWorkspace ? ws.datasetId : null).data ?? null;
  const view3d = useUi((s) => s.view3d);
  const setView3d = useUi((s) => s.setView3d);
  const drawerOpen = useUi((s) => s.drawerOpen);
  const setDrawerOpen = useUi((s) => s.setDrawerOpen);
  const leftOpen = useUi((s) => s.leftOpen);
  const setLeftOpen = useUi((s) => s.setLeftOpen);
  const rightOpen = useUi((s) => s.rightOpen);
  const setRightOpen = useUi((s) => s.setRightOpen);
  const focusMap = useUi((s) => s.focusMap);
  const moduleAnalyses = useUi((s) => s.moduleAnalyses);

  const go = (href: string) => {
    close();
    router.push(href);
  };

  // "Go to": typed coordinates first, then data-pack aerodromes (ICAO, IATA, name, town).
  const [search, setSearch] = useState("");
  const places = usePlaces(search);
  const placeResults = search.trim().length >= 2 ? (places.data ?? []) : [];
  // cmdk highlights the first item when the search changes, but places arrive later from the API:
  // highlight the first one then (it is the top of the list), so Enter goes there.
  const firstPlace = placeResults[0];
  const firstPlaceValue = firstPlace ? placeValue(firstPlace) : null;
  useEffect(() => {
    if (firstPlaceValue) highlight(firstPlaceValue);
  }, [firstPlaceValue, highlight]);
  const openPlace = (place: PlaceResult) => {
    close();
    goToPlace(place);
    // Outside the workspace the map opens there (on the airspace module) and flies to the place.
    if (!inWorkspace) router.push(workspaceHref({ datasetId: ws.datasetId, module: ws.module ?? "airspace" }));
  };

  /**
   * Open module `m` as the module rail does: inside the workspace the entry is replaced (so Back
   * leaves the workspace the same way whichever was used) and the module's last analysis for
   * this dataset comes back; from other pages it is a new entry with the module's form.
   */
  const switchModule = (m: ModuleId) => {
    close();
    const base = {
      datasetId: ws.datasetId,
      featureIndices: ws.featureIndices.length ? ws.featureIndices : null,
      module: m,
    };
    if (!inWorkspace) {
      router.push(workspaceHref(base));
      return;
    }
    const remembered = moduleAnalyses[moduleAnalysisKey(ws.datasetId, m)] ?? null;
    const keepTime = remembered !== null && remembered === ws.analysisId;
    router.replace(workspaceHref({ ...base, analysisId: remembered, time: keepTime ? ws.time : null }), { scroll: false });
  };

  const run = (action: () => void) => {
    close();
    action();
  };

  const openExternal = (href: string) => {
    close();
    window.open(href, "_blank", "noopener,noreferrer");
  };

  const openSample = (name: string, title: string, module: ReturnType<typeof sampleModule>) => {
    close();
    const promise = loadSample.mutateAsync(name);
    toast.promise(promise, {
      loading: `Opening sample "${title}"…`,
      success: (info) => {
        router.push(workspaceHref({ datasetId: info.id, module }));
        return `Opened "${title}"`;
      },
      error: (error: unknown) => {
        const d = describeError(error);
        return `${d.title}: ${d.message}`;
      },
    });
  };

  const demoSamples = (samples.data ?? []).filter(isDemoSample);
  const recent = files.data?.items ?? [];
  const datasetReady = file?.status === "COMPLETED";
  const bbox = file?.bbox ?? null;
  // Server exports only (the static demo has none; its header menu exports in the browser).
  const exports =
    datasetReady && file
      ? EXPORTS.flatMap((e) => {
          const url = fileExportUrl(file.id, e.format);
          return url ? [{ ...e, url }] : [];
        })
      : [];
  const withPanels = breakpoint !== "mobile";

  return (
    <>
      <CommandInput
        placeholder="Type a command, coordinates or an airport…"
        aria-label="Search commands"
        value={search}
        onValueChange={setSearch}
      />
      <CommandList>
        <CommandEmpty>No matching commands.</CommandEmpty>

        {/* Places are matched by the API, not by the palette filter: always shown when found. */}
        {placeResults.length > 0 ? (
          <CommandGroup heading="Go to place" forceMount>
            {placeResults.map((p) => (
              <CommandItem
                key={`${p.kind}:${p.ident ?? p.label}`}
                value={placeValue(p)}
                keywords={[search]}
                forceMount
                onSelect={() => openPlace(p)}
              >
                {p.kind === "aerodrome" ? <Plane aria-hidden /> : <MapPin aria-hidden />}
                <span className="min-w-0 flex-1 truncate">{p.label}</span>
                <span className="max-w-[45%] shrink-0 truncate text-xs text-fg-muted">{p.detail}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}

        <CommandGroup heading="Go to">
          <CommandItem value="go home landing" onSelect={() => go("/")}>
            <Home aria-hidden />
            Home
          </CommandItem>
          <CommandItem value="go workspace upload datasets" onSelect={() => go("/w")}>
            <Upload aria-hidden />
            Workspace: upload and samples
          </CommandItem>
          {ws.datasetId && !inWorkspace ? (
            <CommandItem
              value="go back to workspace map"
              onSelect={() =>
                go(workspaceHref({ datasetId: ws.datasetId, module: ws.module, analysisId: ws.analysisId }))
              }
            >
              <LayoutGrid aria-hidden />
              Back to the workspace
            </CommandItem>
          ) : null}
          {ws.datasetId ? (
            <CommandItem
              value="go report print export"
              onSelect={() => go(workspaceHref({ datasetId: ws.datasetId, analysisId: ws.analysisId }, "/w/report"))}
            >
              <FileText aria-hidden />
              Report for this dataset
            </CommandItem>
          ) : null}
          <CommandItem
            value="create operation evidence brief"
            keywords={["brief", "bundle", "provenance", "pdf", "print", "a4", "zip"]}
            onSelect={() => go(briefHref(ws.datasetId, ws.analysisId))}
          >
            <FileStack aria-hidden />
            Create an operation evidence brief
          </CommandItem>
          <CommandItem value="go settings preferences" onSelect={() => go("/settings")}>
            <Settings aria-hidden />
            Settings
          </CommandItem>
        </CommandGroup>

        {inWorkspace && ws.datasetId ? (
          <CommandGroup heading="Map and dataset">
            {bbox ? (
              <CommandItem value="fit map to the data zoom extent" onSelect={() => run(() => focusMap(bbox))}>
                <Maximize2 aria-hidden />
                Fit the map to the data
              </CommandItem>
            ) : null}
            <CommandItem value={`map view ${view3d ? "2d flat" : "3d tilted"} perspective`} onSelect={() => run(() => setView3d(!view3d))}>
              {view3d ? <MapIcon aria-hidden /> : <Box aria-hidden />}
              {view3d ? "Switch to the 2D map" : "Switch to the 3D map (tilted)"}
            </CommandItem>
            <CommandItem
              value={`basemap map tiles ${basemap === "none" ? "openfreemap online" : "none offline"}`}
              onSelect={() =>
                run(() => {
                  const next = basemap === "none" ? "openfreemap" : "none";
                  setBasemap(next);
                  toast(next === "none" ? "Basemap: none (offline)" : "Basemap: OpenFreeMap");
                })
              }
            >
              <Layers aria-hidden />
              {basemap === "none" ? "Basemap: use OpenFreeMap" : "Basemap: none (offline)"}
            </CommandItem>
            <CommandItem value={`${drawerOpen ? "hide" : "show"} results drawer`} onSelect={() => run(() => setDrawerOpen(!drawerOpen))}>
              <PanelBottom aria-hidden />
              {drawerOpen ? "Hide results" : "Show results"}
            </CommandItem>
            {withPanels ? (
              <CommandItem value={`${leftOpen ? "hide" : "show"} layers and features panel legend`} onSelect={() => run(() => setLeftOpen(!leftOpen))}>
                <PanelLeft aria-hidden />
                {leftOpen ? "Hide layers and features" : "Show layers and features"}
              </CommandItem>
            ) : null}
            {withPanels ? (
              <CommandItem value={`${rightOpen ? "hide" : "show"} inspector panel form`} onSelect={() => run(() => setRightOpen(!rightOpen))}>
                <PanelRight aria-hidden />
                {rightOpen ? "Hide inspector" : "Show inspector"}
              </CommandItem>
            ) : null}
            {exports.map((e) => (
              <CommandItem key={e.format} value={e.label} keywords={[e.keywords]} onSelect={() => run(() => download(e.url))}>
                <Download aria-hidden />
                {e.label}
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}

        <CommandGroup heading={ws.datasetId ? "Switch module" : "Open module"}>
          {MODULE_LIST.map((m) => (
            <CommandItem
              key={m.id}
              value={`module ${m.label} ${m.id}`}
              keywords={MODULE_KEYWORDS[m.id]}
              onSelect={() => switchModule(m.id)}
            >
              <ModuleIcon module={m.id} />
              <span className="min-w-0 flex-1 truncate">{m.label}</span>
              {inWorkspace && ws.module === m.id ? <CommandShortcut>Current</CommandShortcut> : null}
            </CommandItem>
          ))}
        </CommandGroup>

        {demoSamples.length > 0 ? (
          <CommandGroup heading="Open a sample">
            {demoSamples.map((s) => {
              const moduleId = sampleModule(s);
              return (
                <CommandItem
                  key={s.name}
                  value={`sample ${s.title} ${s.name}`}
                  keywords={[s.description, s.filename]}
                  disabled={loadSample.isPending}
                  onSelect={() => {
                    // FlightMD reports are not datasets: open the flight audit with the sample preselected (as the sample cards do).
                    if (isFlightmdReportSample(s)) {
                      const href = workspaceHref({ module: "flight-audit" });
                      go(`${href}${href.includes("?") ? "&" : "?"}sample=${encodeURIComponent(s.name)}`);
                    } else openSample(s.name, s.title, moduleId);
                  }}
                >
                  <ModuleIcon module={moduleId} />
                  <span className="min-w-0 flex-1 truncate">{s.title}</span>
                  <CommandShortcut>{s.filename.split("/").pop()}</CommandShortcut>
                </CommandItem>
              );
            })}
          </CommandGroup>
        ) : null}

        {recent.length > 0 ? (
          <CommandGroup heading="Recent datasets">
            {recent.map((f) => (
              <CommandItem
                key={f.id}
                value={`dataset ${f.filename} ${f.id}`}
                onSelect={() => go(workspaceHref({ datasetId: f.id }))}
              >
                <Database aria-hidden />
                <span className="min-w-0 flex-1 truncate">{f.filename}</span>
                <CommandShortcut>
                  {STATUS_TEXT[f.status]} · {formatRelative(f.created_at)}
                </CommandShortcut>
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}

        <CommandGroup heading="Preferences">
          {mounted ? (
            <CommandItem
              value="toggle theme dark light"
              onSelect={() => {
                setMode(resolved === "dark" ? "light" : "dark");
                close();
              }}
            >
              {resolved === "dark" ? <Sun aria-hidden /> : <Moon aria-hidden />}
              {resolved === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            </CommandItem>
          ) : null}
          <CommandItem
            value="toggle units metric imperial"
            onSelect={() => {
              const next = units === "metric" ? "imperial" : "metric";
              setUnits(next);
              toast(`Units: ${next === "metric" ? "metric (m, km, m²)" : "imperial (ft, mi, ft²)"}`);
              close();
            }}
          >
            <Ruler aria-hidden />
            {units === "metric" ? "Use imperial units" : "Use metric units"}
          </CommandItem>
        </CommandGroup>

        <CommandGroup heading="Help">
          <CommandItem value="help runbook documentation run locally" onSelect={() => openExternal(RUNBOOK_URL)}>
            <BookOpen aria-hidden />
            RUNBOOK: run and check AirBrief
          </CommandItem>
          <CommandItem value="help github source code" onSelect={() => openExternal(REPO_URL)}>
            <Github aria-hidden />
            Source code on GitHub
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </>
  );
}
