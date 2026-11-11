"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Database,
  Gauge,
  Link2,
  ListChecks,
  Monitor,
  Moon,
  OctagonAlert,
  Palette,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  SlidersHorizontal,
  Sun,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { RadioGroup } from "@/components/ui/radio-group";
import { Skeleton, SkeletonText } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "@/components/ui/toaster";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useThemeMode, type ThemeMode } from "@/components/layout/theme-toggle";
import { API_URL, DEMO_MODE } from "@/lib/api";
import { formatCoord, formatNumber } from "@/lib/format";
import {
  BAND_CEILINGS,
  BAND_CEILING_HINT,
  BAND_CEILING_LABEL,
  ED318_LAYOUTS,
  ED318_LIMITS_HINT,
  ED318_LIMITS_LABEL,
  UNVERIFIED_BANDS,
  UNVERIFIED_BAND_HINT,
  UNVERIFIED_BAND_LABEL,
  datapackLayerLabel,
} from "@/lib/labels";
import { useSystemPrefersReducedMotion, resolveReducedMotion } from "@/lib/motion";
import { useHealth, useMeta } from "@/lib/queries";
import {
  useSettings,
  useSettingsHydrated,
  type BasemapId,
  type ReduceMotionPref,
  type Units,
} from "@/lib/store";
import { SCHEMA_VERSION, type DatapackIntegrity } from "@/lib/types";
import { validateFlightmdUrl } from "@/lib/urls";
import { useAreaUnits } from "@/lib/area-units";
import { ChoicesQuestionnaire } from "./choices-questionnaire";
import { LocalAreaUnitSetting } from "./local-area-unit";

function isLocalHost(raw: string): boolean {
  try {
    const host = new URL(raw).hostname;
    return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
  } catch {
    return false;
  }
}

function Section({
  id,
  title,
  icon,
  description,
  children,
}: {
  id: string;
  title: string;
  icon: ReactNode;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="rounded border border-border bg-surface">
      <header className="border-b border-border px-4 py-3">
        <h2 id={id} className="flex items-center gap-2 text-sm font-semibold">
          <span aria-hidden className="text-accent [&_svg]:size-4">
            {icon}
          </span>
          {title}
        </h2>
        {description ? <p className="mt-1 text-sm text-fg-muted">{description}</p> : null}
      </header>
      <div className="space-y-5 p-4">{children}</div>
    </section>
  );
}

function SettingRow({ labelId, label, hint, children }: { labelId: string; label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <p id={labelId} className="text-sm font-medium">
          {label}
        </p>
        {hint ? <p className="mt-0.5 max-w-[55ch] text-xs text-fg-muted">{hint}</p> : null}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function DisplaySection() {
  const ids = useId().replace(/:/g, "");
  const hydrated = useSettingsHydrated();
  const units = useSettings((s) => s.units);
  const setUnits = useSettings((s) => s.setUnits);
  const reduceMotion = useSettings((s) => s.reduceMotion);
  const setReduceMotion = useSettings((s) => s.setReduceMotion);
  const basemap = useSettings((s) => s.basemap);
  const setBasemap = useSettings((s) => s.setBasemap);
  const systemReduced = useSystemPrefersReducedMotion();
  const effectiveReduced = resolveReducedMotion(reduceMotion, systemReduced);
  const theme = useThemeMode();

  return (
    <Section id={`${ids}-display`} title="Display" icon={<Palette />} description="Stored in this browser only.">
      <SettingRow
        labelId={`${ids}-units`}
        label="Units"
        hint="Changes the units used in forms and results. The API and exports always use metric (SI) units."
      >
        <ToggleGroup
          type="single"
          aria-labelledby={`${ids}-units`}
          value={units}
          disabled={!hydrated}
          onValueChange={(v) => {
            if (v) setUnits(v as Units);
          }}
        >
          <ToggleGroupItem value="metric">Metric (m, km²)</ToggleGroupItem>
          <ToggleGroupItem value="imperial">Imperial (ft, mi²)</ToggleGroupItem>
        </ToggleGroup>
      </SettingRow>

      <LocalAreaUnitSetting />

      <SettingRow
        labelId={`${ids}-theme`}
        label="Theme"
        hint="Dark suits map work indoors; light is easier to read in daylight. System follows your device."
      >
        <ToggleGroup
          type="single"
          aria-labelledby={`${ids}-theme`}
          value={theme.mounted ? theme.mode : ""}
          disabled={!theme.mounted}
          onValueChange={(v) => {
            if (v) theme.setMode(v as ThemeMode);
          }}
        >
          <ToggleGroupItem value="dark">
            <Moon aria-hidden />
            Dark
          </ToggleGroupItem>
          <ToggleGroupItem value="light">
            <Sun aria-hidden />
            Light
          </ToggleGroupItem>
          <ToggleGroupItem value="system">
            <Monitor aria-hidden />
            System
          </ToggleGroupItem>
        </ToggleGroup>
      </SettingRow>

      <SettingRow
        labelId={`${ids}-motion`}
        label="Reduce motion"
        hint={
          <>
            When on: no map fly-to (the map jumps), no sliding panels, and show playback never autoplays. Your device
            setting is <span className="font-medium text-fg">{systemReduced ? "reduce" : "no preference"}</span>; motion
            is currently <span className="font-medium text-fg">{effectiveReduced ? "reduced" : "full"}</span>.
          </>
        }
      >
        <ToggleGroup
          type="single"
          aria-labelledby={`${ids}-motion`}
          value={reduceMotion}
          disabled={!hydrated}
          onValueChange={(v) => {
            if (v) setReduceMotion(v as ReduceMotionPref);
          }}
        >
          <ToggleGroupItem value="system">System</ToggleGroupItem>
          <ToggleGroupItem value="on">On</ToggleGroupItem>
          <ToggleGroupItem value="off">Off</ToggleGroupItem>
        </ToggleGroup>
      </SettingRow>

      <SettingRow
        labelId={`${ids}-basemap`}
        label="Basemap"
        hint="OpenFreeMap vector tiles need internet access and no key. None shows your data on a plain background (works offline)."
      >
        <ToggleGroup
          type="single"
          aria-labelledby={`${ids}-basemap`}
          value={basemap}
          disabled={!hydrated}
          onValueChange={(v) => {
            if (v) setBasemap(v as BasemapId);
          }}
        >
          <ToggleGroupItem value="openfreemap">OpenFreeMap</ToggleGroupItem>
          <ToggleGroupItem value="none">None</ToggleGroupItem>
        </ToggleGroup>
      </SettingRow>
    </Section>
  );
}

function ChoicesSection() {
  const hydrated = useSettingsHydrated();
  const bandCeiling = useSettings((s) => s.bandCeiling);
  const unverifiedBand = useSettings((s) => s.unverifiedBand);
  const ed318Limits = useSettings((s) => s.ed318Limits);
  const setChoices = useSettings((s) => s.setChoices);
  const [asking, setAsking] = useState(false);
  const helpRef = useRef<HTMLButtonElement>(null);
  // until the saved choices are read, no option shows: the defaults would flash as if chosen

  useEffect(() => {
    // the panels' "Answer 3 questions" link; focus the button first so closing the dialog lands on it
    if (window.location.hash !== "#help-me-choose") return;
    helpRef.current?.focus();
    setAsking(true);
  }, []);

  return (
    <Section
      id="assessment-choices"
      title="Assessment choices"
      icon={<SlidersHorizontal />}
      description="Three judgement calls the published rules leave open. Every option stays conservative: a band to verify, or a zone flown through, never counts as a pass or as free. Stored in this browser; new runs and exports start from them, and each run records its own."
    >
      <RadioGroup
        legend="Flight audit: flying above a band's free-flight ceiling"
        hint="The DGCA 8–12 km band is green up to 60 m and yellow above it; open airspace is green up to 120 m. Your own height limit and red zones always fail."
        options={BAND_CEILINGS.map((c) => ({ value: c, label: BAND_CEILING_LABEL[c], description: BAND_CEILING_HINT[c] }))}
        value={hydrated ? bandCeiling : null}
        onChange={(v) => setChoices({ bandCeiling: v })}
        disabled={!hydrated}
      />
      <RadioGroup
        legend="eVTOL siting and corridors: within 5 km of an airport"
        hint="The published DGCA rule does not say what this band is (verify on Digital Sky). Rules without such a band, like the conservative ones, are not changed by it."
        options={UNVERIFIED_BANDS.map((c) => ({ value: c, label: UNVERIFIED_BAND_LABEL[c], description: UNVERIFIED_BAND_HINT[c] }))}
        value={hydrated ? unverifiedBand : null}
        onChange={(v) => setChoices({ unverifiedBand: v })}
        disabled={!hydrated}
      />
      <RadioGroup
        legend="ED-318 zone exports: where the vertical limits go"
        hint="For the Zones (ED-318) export of an analysis or a dataset. A brief bundle's zone file always has them in the geometry."
        options={ED318_LAYOUTS.map((c) => ({ value: c, label: ED318_LIMITS_LABEL[c], description: ED318_LIMITS_HINT[c] }))}
        value={hydrated ? ed318Limits : null}
        onChange={(v) => setChoices({ ed318Limits: v })}
        disabled={!hydrated}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button id="help-me-choose" ref={helpRef} variant="secondary" size="sm" onClick={() => setAsking(true)}>
          <ListChecks aria-hidden />
          Help me choose
        </Button>
        <p className="text-xs text-fg-muted">Three or four questions about how you use AirBrief, with a reason for each choice.</p>
      </div>
      <ChoicesQuestionnaire open={asking} onOpenChange={setAsking} />
    </Section>
  );
}

function FlightmdSection() {
  const ids = useId().replace(/:/g, "");
  const hydrated = useSettingsHydrated();
  const saved = useSettings((s) => s.flightmdUrl);
  const setFlightmdUrl = useSettings((s) => s.setFlightmdUrl);
  const meta = useMeta();
  const [draft, setDraft] = useState(saved);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (hydrated) setDraft(saved);
  }, [hydrated, saved]);

  const save = () => {
    const message = validateFlightmdUrl(draft);
    setError(message);
    if (message) {
      document.getElementById(`${ids}-url`)?.focus();
      return;
    }
    setFlightmdUrl(draft);
    toast.success(draft.trim() ? "FlightMD URL saved" : "FlightMD URL cleared");
  };

  const insecure = !error && draft.trim().startsWith("http://") && !isLocalHost(draft.trim());
  const serverDefault = meta.data?.flightmd;

  return (
    <Section
      id={`${ids}-flightmd`}
      title="FlightMD"
      icon={<Link2 />}
      description="Used for the Open in FlightMD links and for fetching reports by URL in the flight audit."
    >
      <form
        noValidate
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        <Field
          id={`${ids}-url`}
          label="FlightMD URL"
          hint={
            serverDefault?.configured && serverDefault.url
              ? `Leave empty to use the server default: ${serverDefault.url}`
              : "The base address of your FlightMD web app, e.g. https://flightmd.example.org."
          }
          error={error}
        >
          {(p) => (
            <Input
              {...p}
              type="url"
              inputMode="url"
              autoComplete="url"
              spellCheck={false}
              placeholder="https://flightmd.example.org"
              value={draft}
              disabled={!hydrated}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => setError(validateFlightmdUrl(draft))}
            />
          )}
        </Field>
        {insecure ? (
          <Alert variant="warn">
            <AlertTitle>Not encrypted</AlertTitle>
            <AlertDescription>
              Report fetching by URL only accepts https. Use an https address unless FlightMD runs on this machine.
            </AlertDescription>
          </Alert>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="sm" disabled={!hydrated || draft.trim().replace(/\/+$/, "") === saved}>
            Save
          </Button>
          {saved ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setDraft("");
                setError(null);
                setFlightmdUrl("");
                toast.success("FlightMD URL cleared");
              }}
            >
              Clear
            </Button>
          ) : null}
        </div>
      </form>
    </Section>
  );
}

/** bbox [west, south, east, north] → "12.700° N, 77.300° E → 13.100° N, 77.800° E" (lat first, as everywhere). */
function formatBbox(b: readonly number[]): string {
  return `${formatCoord(b[0], b[1], { precision: 3 })} → ${formatCoord(b[2], b[3], { precision: 3 })}`;
}

/** Written labels for the server limits of `/api/meta/` (the unit goes with the value, not the label). */
const LIMIT_LABELS: Readonly<Record<string, string>> = {
  max_upload_mb: "Largest upload",
  max_unzipped_mb: "Largest unzipped archive",
  max_archive_files: "Files per archive",
  max_features: "Features per dataset",
  inline_process_max_mb: "Processed at once up to",
  rate_limit_per_hour: "Requests per hour per address",
  upload_ttl_hours: "Uploads kept for",
  flightmd_max_mb: "Largest FlightMD report",
  flightmd_fetch_timeout_s: "FlightMD fetch timeout",
};

/** Limits where 0 switches the limit off, and what 0 means then. */
const LIMIT_OFF: Readonly<Record<string, string>> = {
  rate_limit_per_hour: "No limit",
  upload_ttl_hours: "Until deleted",
  inline_process_max_mb: "None, every upload is queued",
};

const UNIT_SUFFIXES: readonly (readonly [string, string])[] = [
  ["_mb", "MB"],
  ["_seconds", "s"],
  ["_s", "s"],
  ["_hours", "h"],
  ["_km2", "km²"],
  ["_m", "m"],
];

function limitLabel(key: string): string {
  const known = LIMIT_LABELS[key];
  if (known) return known;
  const suffix = UNIT_SUFFIXES.find(([s]) => key.endsWith(s));
  const words = (suffix ? key.slice(0, -suffix[0].length) : key).replace(/_/g, " ").replace(/\bflightmd\b/gi, "FlightMD");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function limitValue(key: string, value: number): string {
  const off = LIMIT_OFF[key];
  if (off && value === 0) return off;
  if (key.endsWith("_hours") && value >= 48 && value % 24 === 0) return `${formatNumber(value / 24)} days`;
  const unit = UNIT_SUFFIXES.find(([s]) => key.endsWith(s))?.[1];
  return unit ? `${formatNumber(value)} ${unit}` : formatNumber(value);
}

function SourceBadge({ synthetic }: { synthetic: boolean }) {
  return synthetic ? (
    <Badge variant="warn">
      <AlertTriangle aria-hidden />
      Synthetic
    </Badge>
  ) : (
    <Badge variant="ok">
      <CheckCircle2 aria-hidden />
      Real data
    </Badge>
  );
}

function IntegrityBadge({ integrity }: { integrity?: DatapackIntegrity }) {
  if (integrity === "verified") {
    return (
      <Badge variant="ok">
        <ShieldCheck aria-hidden />
        Files match their hashes
      </Badge>
    );
  }
  if (integrity === "mismatch") {
    return (
      <Badge variant="danger">
        <ShieldAlert aria-hidden />
        Files changed: not used
      </Badge>
    );
  }
  if (integrity === "unverified") {
    return (
      <Badge variant="secondary">
        <ShieldQuestion aria-hidden />
        No hashes to check
      </Badge>
    );
  }
  return null;
}

function LayerBadges({ pack, layers }: { pack: string; layers: readonly string[] }) {
  return (
    <ul className="flex flex-wrap gap-1" aria-label={`Layers in ${pack}`}>
      {layers.map((l) => (
        <li key={l}>
          <Badge variant="secondary">{datapackLayerLabel(l)}</Badge>
        </li>
      ))}
    </ul>
  );
}

function DataSection() {
  const ids = useId().replace(/:/g, "");
  const meta = useMeta();
  // null = the server doesnt sign; the static demo makes no bundles, so it says nothing
  const signing = DEMO_MODE ? undefined : meta.data?.signing;

  return (
    <Section
      id={`${ids}-data`}
      title="Data packs and limits"
      icon={<Database />}
      description="Data packs hold the terrain, population, aerodromes and OpenStreetMap layers the modules use."
    >
      {meta.isPending ? (
        <div aria-busy="true">
          <span className="sr-only" role="status">
            Loading data packs…
          </span>
          <SkeletonText lines={4} />
        </div>
      ) : meta.isError ? (
        <ErrorState error={meta.error} onRetry={() => void meta.refetch()} retrying={meta.isFetching} compact />
      ) : (
        <>
          {meta.data.datapacks.length === 0 ? (
            <Alert variant="warn">
              <AlertTitle>No data pack installed</AlertTitle>
              <AlertDescription>
                Measuring works without one, but the modules need data. Run{" "}
                <code className="font-mono text-fg">python -m seed.datapack --synthetic --name demo</code> for an
                offline demo pack.
              </AlertDescription>
            </Alert>
          ) : (
            <>
              {/* Below 768 px: one card per pack (PLAN: tables become cards). */}
              <ul className="space-y-2 md:hidden" aria-label="Installed data packs">
                {meta.data.datapacks.map((p) => (
                  <li key={p.name} className="rounded border border-border p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="min-w-0 break-all font-mono text-sm font-medium">{p.name}</p>
                      <div className="flex flex-wrap gap-1">
                        <SourceBadge synthetic={p.synthetic} />
                        <IntegrityBadge integrity={p.integrity} />
                      </div>
                    </div>
                    <dl className="mt-2 space-y-2 text-xs">
                      <div>
                        <dt className="text-fg-muted">Coverage (south-west → north-east)</dt>
                        <dd className="num mt-0.5 break-words text-fg">{formatBbox(p.bbox)}</dd>
                      </div>
                      <div>
                        <dt className="text-fg-muted">Layers</dt>
                        <dd className="mt-1">
                          <LayerBadges pack={p.name} layers={p.layers} />
                        </dd>
                      </div>
                    </dl>
                  </li>
                ))}
              </ul>
              <Table containerClassName="hidden md:block">
                <caption className="sr-only">Installed data packs</caption>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Coverage (south-west → north-east)</TableHead>
                    <TableHead>Layers</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {meta.data.datapacks.map((p) => (
                    <TableRow key={p.name}>
                      <TableHead scope="row" className="h-auto py-2 font-mono text-sm text-fg">
                        {p.name}
                      </TableHead>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          <SourceBadge synthetic={p.synthetic} />
                          <IntegrityBadge integrity={p.integrity} />
                        </div>
                      </TableCell>
                      <TableCell className="num text-xs">{formatBbox(p.bbox)}</TableCell>
                      <TableCell>
                        <LayerBadges pack={p.name} layers={p.layers} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          )}
          {meta.data.datapacks
            .filter((p) => p.integrity === "mismatch")
            .map((p) => (
              <Alert key={`integrity-${p.name}`} variant="danger">
                <AlertTitle>Data pack {p.name} failed its integrity check</AlertTitle>
                <AlertDescription>
                  Its files no longer match the SHA-256 values in its manifest, so analyses that read it stop with an error. Rebuild
                  the pack, or after an intended edit record the new hashes with{" "}
                  <code className="font-mono text-fg">python -m seed.datapack --seal &lt;pack folder&gt;</code>.
                </AlertDescription>
              </Alert>
            ))}
          {signing ? (
            <p className="flex items-start gap-1.5 text-xs text-fg-muted">
              <ShieldCheck className="mt-px size-3.5 shrink-0 text-ok" aria-hidden />
              <span>
                Brief bundles from this server are signed ({signing.algorithm}, key{" "}
                <code className="font-mono text-fg">{signing.key_id}</code>). Public key for recipients:{" "}
                <code className="break-all font-mono text-fg">{signing.public_key}</code>
              </span>
            </p>
          ) : signing === null ? (
            <p className="flex items-start gap-1.5 text-xs text-fg-muted">
              <ShieldQuestion className="mt-px size-3.5 shrink-0" aria-hidden />
              Brief bundles from this server are not signed: their SHA-256 values detect accidental corruption, not deliberate edits.
            </p>
          ) : null}
          {meta.data.datapacks.some((p) => p.synthetic) ? (
            <p className="flex items-start gap-1.5 text-xs text-fg-muted">
              <AlertTriangle className="mt-px size-3.5 shrink-0 text-warn" aria-hidden />
              Synthetic packs are generated for demos and tests. Results computed with them are not real-world evidence.
            </p>
          ) : null}

          <div>
            <h3 className="mb-2 flex items-center gap-2 text-sm font-medium">
              <Gauge className="size-4 text-accent" aria-hidden />
              Server limits
            </h3>
            <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
              {Object.entries(meta.data.limits).map(([key, value]) => (
                <div key={key} className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1">
                  <dt className="text-fg-muted">{limitLabel(key)}</dt>
                  <dd className="num text-fg">{limitValue(key, value)}</dd>
                </div>
              ))}
            </dl>
          </div>
        </>
      )}
    </Section>
  );
}

function HealthSection() {
  const ids = useId().replace(/:/g, "");
  const health = useHealth();
  const meta = useMeta();

  return (
    <Section id={`${ids}-health`} title="Server health" icon={<Activity />} description={<span className="break-all">API: {DEMO_MODE ? "static demo files" : API_URL}</span>}>
      {health.isPending ? (
        <div aria-busy="true" className="grid gap-2 sm:grid-cols-3">
          <span className="sr-only" role="status">
            Checking the server…
          </span>
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
        </div>
      ) : health.isError ? (
        <ErrorState error={health.error} onRetry={() => void health.refetch()} retrying={health.isFetching} compact />
      ) : (
        <div className="space-y-3">
          <p className="flex items-center gap-2 text-sm">
            {health.data.status === "ok" ? (
              <Badge variant="ok">
                <CheckCircle2 aria-hidden />
                Online
              </Badge>
            ) : (
              <Badge variant="warn">
                <AlertTriangle aria-hidden />
                {health.data.status}
              </Badge>
            )}
            <span className="text-fg-muted">
              Database: <span className="text-fg">{health.data.db}</span>
            </span>
          </p>
          <dl className="grid gap-2 sm:grid-cols-3">
            <div className="rounded border border-border p-3">
              <dt className="text-xs text-fg-muted">Version</dt>
              <dd className="num mt-1 text-sm">{health.data.version}</dd>
            </div>
            <div className="rounded border border-border p-3">
              <dt className="text-xs text-fg-muted">Schema</dt>
              <dd className="num mt-1 text-sm">{health.data.schema_version}</dd>
            </div>
            <div className="rounded border border-border p-3">
              <dt className="flex items-center gap-1 text-xs text-fg-muted">
                <Cpu className="size-3.5" aria-hidden />
                Compute backend
              </dt>
              <dd className="mt-1 text-sm">
                {health.data.native_backend === "cpp" ? "Native C++ kernel" : "Python (NumPy)"}
              </dd>
            </div>
          </dl>
          {health.data.schema_version !== SCHEMA_VERSION ? (
            <Alert variant="warn">
              <AlertTitle>Schema mismatch</AlertTitle>
              <AlertDescription>
                The server uses schema {health.data.schema_version}; this web app expects {SCHEMA_VERSION}. Update the
                web app or the API so they match.
              </AlertDescription>
            </Alert>
          ) : null}
          {meta.data && meta.data.version !== health.data.version ? (
            <p className="flex items-center gap-1.5 text-xs text-fg-muted">
              <OctagonAlert className="size-3.5 text-warn" aria-hidden />
              Metadata reports version {meta.data.version}. Reload the page if you just upgraded the server.
            </p>
          ) : null}
        </div>
      )}
    </Section>
  );
}

/** The app's default theme (`defaultTheme` of the ThemeProvider in app/providers.tsx). */
const DEFAULT_THEME_MODE: ThemeMode = "dark";

export function SettingsView() {
  const resetSettings = useSettings((s) => s.resetSettings);
  const theme = useThemeMode();

  // Resets everything on this page that is stored in the browser, theme included, with Undo
  // (it also clears a saved FlightMD URL).
  const reset = () => {
    const { units, reduceMotion, basemap, flightmdUrl, bandCeiling, unverifiedBand, ed318Limits, choicesSet } = useSettings.getState();
    const { unit: areaUnit, bighaM2 } = useAreaUnits.getState();
    const previousMode = theme.mounted ? theme.mode : null;
    const setMode = theme.setMode;
    resetSettings();
    useAreaUnits.getState().reset();
    setMode(DEFAULT_THEME_MODE);
    toast.success("Settings reset to the defaults", {
      description: "Units, land area unit and bigha size, theme, reduce motion, basemap, the assessment choices and the FlightMD URL.",
      action: {
        label: "Undo",
        onClick: () => {
          useSettings.setState({ units, reduceMotion, basemap, flightmdUrl, bandCeiling, unverifiedBand, ed318Limits, choicesSet });
          useAreaUnits.setState({ unit: areaUnit, bighaM2 });
          if (previousMode) setMode(previousMode);
          toast.success("Settings restored");
        },
      },
    });
  };

  return (
    <div className="space-y-6">
      <DisplaySection />
      <ChoicesSection />
      <FlightmdSection />
      <DataSection />
      <HealthSection />
      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={reset}>
          <RotateCcw aria-hidden />
          Reset settings
        </Button>
      </div>
    </div>
  );
}
