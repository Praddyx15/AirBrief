# AirBrief frontend

Next.js 15 (App Router, React 19) + TypeScript (strict) + Tailwind 3.4 + Radix/shadcn-style components.
Design decisions are binding: `docs/SPEC.md` §6. The data contract is
`airbrief_core/models/*`, mirrored in `lib/types.ts`.

```
npm install
cp .env.example .env.local
npm run dev -- -H 127.0.0.1   # http://localhost:3000 (this machine only), API at NEXT_PUBLIC_API_URL
npm run lint           # next lint (next/core-web-vitals)
npm run typecheck      # tsc --noEmit
npm run build          # production build; serve it with npm start -- -H 127.0.0.1
npm run export         # static site in out/ (NEXT_OUTPUT=export)
PLAYWRIGHT_BROWSERS_PATH=0 npm run e2e   # Playwright + axe, against a running app
```

`PLAYWRIGHT_BROWSERS_PATH=0` makes Playwright use the Chromium build in
`node_modules/playwright-core/.local-browsers`, the one that matches its version.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | FastAPI base URL (no trailing slash) |
| `NEXT_PUBLIC_BASEMAP` | `openfreemap` | Default basemap: `openfreemap` or `none` (user can change it in Settings) |
| `NEXT_PUBLIC_DEMO` | `0` | `1` = read-only static demo reading `public/demo/*.json` |
| `NEXT_PUBLIC_BASE_PATH` | empty | Serve under a sub-path, e.g. `/airbrief` |

`next.config.mjs`: `reactStrictMode`, `images.unoptimized`, `basePath` from
`NEXT_PUBLIC_BASE_PATH`, and `output: "export"` + `trailingSlash` when `NEXT_OUTPUT=export`.
Otherwise `next start` sends the security headers: `nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, `Permissions-Policy: camera=(), microphone=(), geolocation=(self),
payment=()` (`geolocation=(self)` is what lets **My location** work) and, in production builds,
`Strict-Transport-Security`. A reverse proxy must not replace the `Permissions-Policy` with one that
drops `geolocation=(self)`.

The Content-Security-Policy comes from `middleware.ts`, in production builds only: a new nonce per
request and `script-src 'self' 'nonce-…' 'strict-dynamic'`, so only Next's own scripts and the
theme script (the root layout passes it the nonce from `x-nonce`) run. Styles keep
`'unsafe-inline'` for inline style attributes. Every page therefore renders per request. The dev
server sends no CSP (hot reloading needs `eval`), and a static export has no middleware at all.

## Layout of the code

```
app/
  layout.tsx       Geist fonts, <Providers>, "Skip to main content" link (target: #main)
  providers.tsx    ThemeProvider (class, dark default) · QueryClientProvider · TooltipProvider · Toaster
  globals.css      tokens (dark :root, .light), focus ring, reduced motion, print, .num/.tnum
components/ui/     design-system components (below)
lib/               types, api, queries, store, url-state, format, motion, colors, modules, utils,
                   plan, plan-store, coords, map-pick, area-units, csv, labels, pending-deletes,
                   urls, datasets, brief-demo, plan-demo (see "lib/" and "Shared helpers")
```

Pages: `app/page.tsx` (landing), `app/w/page.tsx`, `app/w/view/page.tsx` (workspace),
`app/w/report/page.tsx`, `app/settings/page.tsx`. Map: `components/map/*` (MapLibre + deck.gl via
`next/dynamic({ ssr: false })`). Workspace: `components/workspace/*`. Modules:
`components/modules/<id>/index.tsx`, registered in `components/modules/registry.tsx` (see
"Module contract").

**Every page must give its main content `id="main"`** (e.g. `<main id="main" tabIndex={-1}>`) so
the skip link works. Pages that call `useWorkspaceParams()` / `useSearchParams()` must wrap that
part in `<Suspense>` (required for static export).

## Pages

| Route | Files | What it does |
|---|---|---|
| `/` | `app/page.tsx` | Landing: hero with a static SVG map (`components/layout/hero-map.tsx`) and **Open workspace** / **Check a location** (`/w/view?m=airspace&plan=pin`) / **Try a sample**, the 9 module cards from `MODULE_LIST` (`MODULE_META`), how it works, the links to FlightMD and UAOP, a native quick-start code block with a copy button, open data & licences, footer. Server component. |
| `/w` | `app/w/page.tsx`, `components/upload/*` | Upload home. `dropzone.tsx`: drag-and-drop **and** a Browse… button; accepted types and the size limit (`useMeta().limits.max_upload_mb`) shown up front; client-side extension / size / shapefile-part checks with a clear message; Advanced → CRS override (`EPSG:32643`, digits or WKT) and track origin (`lon, lat[, alt]`), validated on blur; XHR progress via `useUpload`, staged Validating → Uploading → Reading → Measuring → Done (polls `useFile` when the API answers 202), Cancel, Retry on failure, then `router.push("/w/view?d=<id>")`. `sample-cards.tsx`: `useSamples` minus `HIDDEN_SAMPLES` (`attack_zipslip`, `zipbomb_small`, `corrupt`, `no_prj`), grouped Measure / Modules, opened with `useLoadSample` → `/w/view?d=<id>&m=<module>` (`sampleModule()` maps `survey→survey`, `evtol_siting→evtol`, `show→shows`, `flight_audit→flight-audit`, `measure→measure`), plus an "Audit a FlightMD flight" card → `/w/view?m=flight-audit`. `recent-datasets.tsx`: `useFiles` with status badges (`FileStatusBadge`), Open / Report / Delete (confirm dialog), Show more, and the empty state. |
| `/w/report?d=&a=&mode=brief` | `app/w/report/brief-view.tsx`, `components/report/brief-sheet.tsx`, `brief-href.ts` | **Brief** mode: tick up to 12 completed analyses (the dataset's and this browser's history), fill in the operation (remembered on this device only), **Create brief** (`POST /api/briefs/`); one A4 sheet whose text scale is fitted to one page by bisection on a hidden copy; **Print / Save as PDF**; **Download bundle (.zip)** (`POST /api/briefs/bundle/`; API only). In the static demo the brief is merged in the browser from `demo/briefs/{id}.json` (`lib/brief-demo.ts`). See `docs/features/brief.md`. |
| `/w/report?d=&a=` | `app/w/report/page.tsx` (Suspense), `report-view.tsx`, `components/report/*` | Print-friendly report. Dataset summary KPIs, `geometry-svg.tsx` (pure SVG, equirectangular fit, zone hatches, legend, scale bar; layers over 5,000 features are listed instead of drawn), measurements table (all pages, capped at 2,000 rows) with totals, analysis section from `analysis-summary.tsx` (`summarizeAnalysis(info, units)` → verdict, KPI tiles, finding tables, issues, disclaimer for every kind), synthetic-data warning, disclaimers and attributions. Toolbar (hidden on paper): Back to the workspace, the **Report / Brief** tabs, exports (`components/report/analysis-export.tsx`: JSON, GeoJSON with H3 cells as polygons, KML, and any CSV table from `GET /api/analyses/{id}/export/tables/`; JSON and GeoJSON built client-side in demo mode), Print. Without `a`, completed analyses of the dataset can be added. Every module has a section, including environment, line of sight, the operational volume, training-area exits and show pads. |
| `/settings` | `app/settings/page.tsx`, `settings-view.tsx`, `local-area-unit.tsx` | Units, land area unit (hectare / acre / guntha / bigha) and bigha size (`lib/area-units.ts`), theme (Dark / Light / System), reduce motion (System / On / Off, shows the OS value), basemap (OpenFreeMap / None), FlightMD URL (validated http(s) base URL, warns on plain http), data packs from `useMeta` (synthetic badge, bbox, layers), server limits, health (`useHealth`: version, schema with a mismatch warning, compute backend), reset. |
| errors | `app/not-found.tsx`, `app/error.tsx`, `app/loading.tsx`, `app/w/loading.tsx` | 404 with links, route error boundary (`ErrorState` + Retry = `reset`), skeletons sized like the pages. |

Every page renders `<main id="main" tabIndex={-1}>`.

**Layout components (`components/layout/`)**

| File | Exports | Notes |
|---|---|---|
| `site-header.tsx` | `SiteHeader({ withCommandPalette = true })` | Wordmark, nav (Workspace, Settings; `aria-current="page"`), command-palette trigger with `Ctrl K` / `⌘ K` hint, GitHub link, theme toggle. Also renders `DemoBanner`, `ThemeSystemSync` and (by default) `CommandPalette`. Hidden in print. |
| `command-palette.tsx` | `CommandPalette` | Mount **one** per page. Binds Ctrl/⌘+K globally and `useUi().commandOpen`. Groups: Go to (home, workspace, report/back when `?d=` is present, settings), Switch module (keeps `d` and `f`, via `workspaceHref`), Open a sample, Recent datasets, Preferences (theme, units), Help. Data queries only run while it is open. The workspace page should either use `SiteHeader` or mount `<CommandPalette />` itself (and `<ThemeSystemSync />`). |
| `theme-toggle.tsx` | `ThemeToggle`, `useThemeMode()` → `{mode, resolved, setMode, mounted}`, `ThemeSystemSync`, `useMounted`, type `ThemeMode` | "System" is stored in localStorage (`airbrief-theme-follow-system`) and mirrored onto next-themes by `ThemeSystemSync`. |
| `site-footer.tsx` | `SiteFooter`, `DISCLAIMER`, `DATA_ATTRIBUTIONS` | Disclaimer "Indicative only — not a substitute for DGCA Digital Sky", data attributions, licence links. |
| `demo-banner.tsx` | `DemoBanner`, `REPO_URL`, `RUNBOOK_URL` | Only when `NEXT_PUBLIC_DEMO=1`. |
| `module-icon.tsx` | `ModuleIcon({ module })`, `MODULE_ICON_COMPONENTS` | Lucide component per `ModuleIconName`. |
| `wordmark.tsx`, `hero-map.tsx`, `copy-button.tsx` | `Wordmark`, `HeroMap`, `CopyButton` | |

`components/upload/sample-utils.ts` exports `HIDDEN_SAMPLES`, `isDemoSample`, `sampleModule`, `groupSamples`.

## Theming and tokens (`app/globals.css`, `tailwind.config.ts`)

Dark is the default on `:root`; the light theme is the `.light` class (next-themes,
`attribute="class"`, themes `dark`/`light`, `defaultTheme="dark"`, no system mode). Use
`useTheme()` from `next-themes` to read/switch (`resolvedTheme`).

Each token exists as `--name` (hex) and `--name-rgb` (channels). Tailwind colour names:

| Tailwind | Token | Use |
|---|---|---|
| `bg-bg` | `--bg` | page background |
| `bg-surface` | `--surface` | panels, cards |
| `bg-elevated` | `--elevated` | popovers, hovers |
| `border-border` (also the default border colour) | `--border` | borders (elevation = borders, not glow) |
| `text-fg`, `text-fg-muted`, `text-fg-subtle` | `--text`, `--text-muted`, `--text-subtle` | `fg-subtle` only for disabled or ≥18 px text |
| `bg-accent` / `text-accent` | `--accent` | focus, icons, selection |
| `bg-accent-solid` + `text-accent-foreground` | `--accent-solid`, `--on-accent` | primary buttons |
| `text-accent-text` | `--accent-text` | links |
| `text-ok`, `text-warn`, `text-danger`, `bg-danger-solid` | status | always with icon + label |
| `bg-zone-green/yellow/red` | zone colours | always with hatch + label |

Opacity modifiers work (`bg-accent/15`). Radius 6 px (`rounded`). Durations 150–250 ms
(`duration-150`, default `200`, `duration-250`). Fonts: `font-sans` (Geist Sans), `font-mono`
(Geist Mono). Numbers: `.num` (mono + tabular + slashed zero) or `.tnum`/`tabular-nums`; the body
already uses tabular figures.

Custom variants: `pointer-coarse:` / `pointer-fine:` (touch targets ≥44 px), `reduce-motion:`
(OS setting or in-app override). z-index scale: `z-map`, `z-panel`, `z-drawer`, `z-overlay`,
`z-modal`, `z-toast`. Spacing helpers: `w-rail` (56 px), `w-panel-left` (300 px),
`w-panel-right` (340 px).

**Reduced motion.** `<html data-motion="reduce|full">` is kept in sync with the setting by
Providers. CSS turns `tailwindcss-animate` slides/zooms into fades and stops pulses when reduced.
In JS use `usePrefersReducedMotion()` (see `lib/motion.ts`).

**Print.** `@media print` forces light tokens, hides `.no-print`, `[data-print="hide"]`, the skip
link, toasts and `nav[aria-label="Workspace modules"]` (name the rail exactly that). Use
`.print-only`, `.print-break-before`, `.print-avoid-break`.

## Components (`components/ui/*`)

All are client-safe, forward refs where relevant, and accept `className`.

| File | Exports | Notes |
|---|---|---|
| `button.tsx` | `Button`, `buttonVariants` | variants `default · secondary · ghost · outline · destructive · link`; sizes `sm · default · lg · icon · icon-sm`; `asChild` (e.g. Next `<Link>`); `type="button"` default |
| `icon-button.tsx` | `IconButton` | **required `label`** → `aria-label` + tooltip; `icon`, `shortcut`, `pressed` (aria-pressed), `side`, size `icon`/`icon-sm`, variant default `ghost` |
| `card.tsx` | `Card`, `CardHeader`, `CardTitle` (`as` h2–h4), `CardDescription`, `CardContent`, `CardFooter` | |
| `input.tsx` | `Input`, `Textarea`, `inputClassName` | 16 px text; `aria-invalid` styles |
| `label.tsx` | `Label` | Radix label |
| `field.tsx` | `Field`, `ErrorSummary`, types `FieldControlProps`, `ErrorSummaryItem` | `<Field label unit hint error required id>{(p) => <Input {...p}/>}</Field>` wires `id`, `aria-describedby` (`${id}-error ${id}-hint`), `aria-invalid`, `aria-required`. `ErrorSummary` is a focusable `role="alert"` list linking to `#id`; focus it via ref after a failed submit |
| `number-input.tsx` | `NumberInput` | `value: number \| null`, `onValueChange`, `min`, `max`, `step`, `unit` suffix, `decimals`, `allowEmpty`, `clampOnBlur`, `onInvalidText` (message while the text is not a number; the text is then kept for the field to show the error); role `spinbutton`; Arrow (Shift ×10), PageUp/Down, Home/End. Reads typed text with `parseNumberText`: "1,200" and "1,00,000" are thousands, "2,5" is 2.5, ambiguous text ("0,500") is an error, and Enter on it never submits the form. Pair with `Slider` for the WCAG dragging alternative |
| `slider.tsx` | `Slider` | Radix; `thumbLabels` (one per thumb) or `aria-label`; `getValueText` |
| `badge.tsx` | `Badge`, `badgeVariants` | `default · secondary · outline · info · ok · warn · danger · zone-green · zone-yellow · zone-red` (zone variants render a hatch swatch). Always include text, and an icon for status |
| `alert.tsx` | `Alert`, `AlertTitle`, `AlertDescription` | variants `default · info · ok · warn · danger`, default icon per variant (`icon={null}` hides); role `alert` for danger, else `status` |
| `tooltip.tsx` | `TooltipProvider` (already in Providers), `Tooltip`, `TooltipTrigger`, `TooltipContent`, `SimpleTooltip` | use tooltips, never `title=` |
| `dialog.tsx` | `Dialog`, `DialogTrigger`, `DialogContent` (`hideClose`), `DialogHeader`, `DialogFooter`, `DialogTitle`, `DialogDescription`, `DialogClose`, `DialogOverlay`, `DialogPortal` | always render a `DialogTitle`. Closing returns focus to whatever had it when the dialog opened, also for dialogs opened from code (no `DialogTrigger`) |
| `sheet.tsx` | `Sheet`, `SheetTrigger`, `SheetContent` (`side` top/right/bottom/left, `hideOverlay`, `hideClose`), `SheetHeader`, `SheetBody`, `SheetFooter`, `SheetTitle`, `SheetDescription`, `SheetClose`, `sheetVariants` | inspector < 1024 px, bottom sheet < 768 px; `<Sheet modal={false}>` + `hideOverlay` for non-blocking panels. Closing returns focus to the opener, as `DialogContent` does |
| `popover.tsx` | `Popover`, `PopoverTrigger`, `PopoverContent`, `PopoverAnchor`, `PopoverClose` | |
| `tabs.tsx` | `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent` | |
| `select.tsx` | `Select`, `SelectTrigger`, `SelectValue`, `SelectContent`, `SelectItem`, `SelectGroup`, `SelectLabel`, `SelectSeparator` | give `SelectTrigger` the Field props (`id`, aria) |
| `switch.tsx` | `Switch` | label via `<Label htmlFor>` or `aria-label` |
| `toggle-group.tsx` | `ToggleGroup`, `ToggleGroupItem`, `toggleVariants` | segmented control; sizes `sm · default · icon`; group needs `aria-label` |
| `collapsible.tsx` | `Collapsible`, `CollapsibleTrigger` (chevron, `hideChevron`), `CollapsibleContent` | no height animation (fade only) — use for "Advanced" sections |
| `separator.tsx` | `Separator` | |
| `skeleton.tsx` | `Skeleton`, `SkeletonText` | size like the real content (no layout shift); `aria-hidden` |
| `progress.tsx` | `Progress` | `value` 0–100 or null (indeterminate), `label` (accessible name), `tone` |
| `table.tsx` | `Table`, `TableHeader`, `TableBody`, `TableFooter`, `TableRow`, `TableHead`, `TableCell`, `TableCaption` | plain semantic table |
| `data-table.tsx` | `DataTable<T>`, types `DataTableColumn<T>`, `DataTableProps<T>`, `SortState`, `SortDirection`, `SortValue` | see below |
| `scroll-area.tsx` | `ScrollArea` (`viewportRef`, `focusable`, `viewportLabel`), `ScrollBar` | |
| `kbd.tsx` | `Kbd` | |
| `command.tsx` | `Command`, `CommandDialog` (`open`, `onOpenChange`, `title`, `description`, `commandProps`), `CommandInput`, `CommandList`, `CommandEmpty`, `CommandGroup` (`heading`), `CommandItem` (`onSelect`, `value`, `keywords`), `CommandShortcut` | cmdk 1.x. Open state: `useUi().commandOpen` / `setCommandOpen`. Visible groups are divided by a border; there is no separator element (cmdk renders it as `role="separator"` inside the listbox, which axe rejects) |
| `empty-state.tsx` | `EmptyState` | `icon`, `title`, `description`, `action`, `compact`, `headingLevel` |
| `error-state.tsx` | `ErrorState` | `error` (anything; uses `describeError`) or explicit `title/message/fix`; `onRetry`, `retryLabel`, `retrying`, `actions`, `compact`; `role="alert"` |
| `kpi-tile.tsx` | `KpiTile`, `KpiGrid` | `label`, `value` (string or `FormattedValue` from `lib/format`), `unit`, `explanation`, `tone` + `toneLabel` (icon + text), `loading`. Each tile is a `<dl>` |
| `toaster.tsx` | `Toaster` (in Providers), `toast` | sonner, theme-synced |

### DataTable

```tsx
<DataTable
  data={items}                                   // readonly T[]
  columns={[
    { id: "name", header: "Name", cell: (r) => r.name ?? "Unnamed", sortValue: (r) => r.name, rowHeader: true },
    { id: "area", header: "Area", numeric: true,
      cell: (r) => formatArea(r.measurement.area_m2, units), sortValue: (r) => r.measurement.area_m2 },
  ]}
  getRowId={(r) => r.feature_index}
  caption="Features and measurements"            // required accessible name (sr-only unless captionVisible)
  onRowClick={(r) => setFeature(r.feature_index)} // click / Enter / Space; ArrowUp/Down/Home/End move focus
  onRowHover={(r) => setHoveredFeature(r?.feature_index ?? null)} // hover AND keyboard focus; null on leave
  selectedRowIds={featureIndices}                // highlighted + scrolled into view
  getRowLabel={(r) => `Select ${r.name ?? `feature ${r.feature_index}`}`}
  initialSort={{ columnId: "area", direction: "desc" }} // or controlled: sort + onSortChange
  emptyState={<EmptyState compact title="No features" />}
  maxHeight={360}
/>
```

Sortable headers are buttons inside `<th aria-sort>`; nulls sort last in both directions. Above
`virtualizeThreshold` (default 200) rows it virtualises with `@tanstack/react-virtual`, keeping
`<table>` semantics (spacer rows, `aria-rowcount`, `aria-rowindex`); default max height then
420 px. Other props: `dense`, `estimateRowHeight`, `getRowClassName`, `containerProps` (e.g.
`{ id: "results" }` for the "Skip to results" link).

## lib/

### `utils.ts`
`cn(...classes)`, `BASE_PATH`, `withBasePath(path)`, `clamp`, `isFiniteNumber`, `assertNever`,
`downloadBlob(data, filename, type)` (client-side exports, e.g. in demo mode).

### `types.ts` — the data contract
Mirrors every Pydantic model (snake_case, same names). Output models list all fields
(`X | None` → `X | null`); `*Params` mark defaulted fields optional.

- GeoJSON: `Position`, `Geometry` (+ each geometry type), `GeometryType`, `Feature<G,P>`,
  `FeatureCollection<G,P>`, `GeoJSON`.
- common.py: `SCHEMA_VERSION`, `ZoneKind`, `FileStatus`, `AnalysisStatus`, `Severity`, `Verdict`,
  `Issue`, `BBox`, `BBoxList`, `ZoneRing`, `AerodromeZoneRule`, `ZoneRules`, `UserZone`, `ZoneHit`,
  `NearestAerodrome`, `ObstacleHit`, `TerrainSummary`, `ProfilePoint`.
- features.py: `FileType`, `CrsSource`, `MeasurementStatus`, `Measurement`, `FeatureRecord`,
  `LayerInfo`, `MeasurementTotals`, `DatasetResult`.
- presets.py: `CameraSpec`, `UasPreset`, `CameraPresetId`, `UasPresetId`.
- analyses.py: `AnalysisKind`, `ANALYSIS_KINDS`, `ResultLayers`; `AirspaceParams/Result`,
  `FlyabilityCell`, `FlyabilityStatus`; `GroundRiskParams/Result`, `DensityCell`;
  `SitingWeights`, `EvtolSitingParams/Result`, `SiteCandidate`, `SitingCell`; `CorridorWeights`,
  `EvtolCorridorParams/Result`, `NoiseCell`, `NoiseSummary`; `TrackInput`, `ShowParams/Result`,
  `ShowConflict`, `ShowViolation(Kind)`, `DroneStats`, `ShowFrames`, `ShowSiteChecks`;
  `SurveyParams/Result`, `SurveyBlock`; `FlightAuditParams/Result`, `FlightInfo`,
  `AuditExceedance`, `AltitudeReference`, `AglBand`; `AnalysisParamsByKind`,
  `AnalysisResultByKind`, `AnalysisResult`, `AnalysisEnvelope<K>`, `ComputeBackend`.
- API: `ApiErrorInfo`, `ApiErrorBody`, `Page<T>`, `Health`, `FileInfo`, `FilesPage`,
  `MeasurementItem`, `MeasurementsPage`, `MeasurementsQuery`, `FeatureSystemProperties`,
  `DatasetFeatureProperties`, `DatasetFeatureCollection` (`_index, _layer, _name, _type,
  _status, _area_m2, _length_m`), `SampleInfo` (+ demo-only `dataset_id`),
  `AnalysisRequest<K>` (params are `Partial`, plus `report_url`), `AnalysisInfo<K>`,
  `AnalysesPage`, `ModuleMeta`, `DatapackInfo`, `MetaLimits`, `Meta`, `JsonSchema`,
  `FileExportFormat`, `AnalysisExportFormat`.
- Helpers: `isAnalysisKind`, `isFileActive`, `isAnalysisActive`, `isAnalysisOfKind(info, kind)`
  (type guard), `analysisResult(info, kind)` → typed result or null.

### `api.ts` — typed client
`API_URL`, `DEMO_MODE`, `apiUrl(path, query)`, `api` (object of all functions).

| Function | Endpoint |
|---|---|
| `getHealth(signal?)` | GET `/health` (demo: synthesised from meta) |
| `getMeta(signal?)` | GET `/api/meta/` |
| `listFiles({limit, offset}, signal?)` | GET `/api/files/` |
| `getFile(id, signal?)` | GET `/api/files/{id}/` |
| `getMeasurements(id, {limit=100, offset=0, geometry_type, status}, signal?)` | GET `/api/files/{id}/measurements/` |
| `getFeatures(id, signal?)` | GET `/api/files/{id}/features/` |
| `getFeature(id, index, signal?)` | GET `/api/files/{id}/features/{index}/` |
| `fileExportUrl(id, "csv"\|"geojson"\|"kml")` | URL string, `null` in demo |
| `deleteFile(id)` | DELETE `/api/files/{id}/` |
| `uploadFile(file, {crsOverride, origin, onProgress, signal})` | POST `/api/files/` via XHR; `onProgress({loaded, total, fraction, phase: "uploading"\|"processing"})` |
| `listSamples(signal?)` | GET `/api/samples/` |
| `loadSample(name)` | POST `/api/samples/{name}/` |
| `sampleDownloadUrl(name)` | URL, `null` in demo |
| `createAnalysis(body, datasetToken?)` | POST `/api/analyses/` (`X-Owner-Token`: the dataset's token makes it the owner's) |
| `listAnalyses({dataset_id, ids}, signal?)` | GET `/api/analyses/` |
| `getAnalysis(id, signal?)` | GET `/api/analyses/{id}/` |
| `cancelAnalysis(id)` | POST `/api/analyses/{id}/cancel/` |
| `deleteAnalysis(id)` | DELETE `/api/analyses/{id}/` |
| `analysisExportUrl(id, "json"\|"geojson"\|"kml"\|"csv", {table})` | URL, `null` in demo |
| `getAnalysisTables(id, signal?)` | GET `/api/analyses/{id}/export/tables/` (API only) |
| `createBrief(body, signal?)` | POST `/api/briefs/` (demo: merged from `demo/briefs/{id}.json`) |
| `downloadBriefBundle(body)` | POST `/api/briefs/bundle/` → `{blob, filename}` (API only) |
| `fetchFlightmd(url)` | POST `/api/flightmd/fetch/` |
| `getContext({bbox, layers, limit}, signal?)` | GET `/api/context/` (demo: cut from `demo/context.json`, `lib/plan-demo.ts`) |
| `getPlaces(q, limit, signal?)` | GET `/api/places/` (demo: coordinates and the demo pack's aerodromes) |
| `parseGeometry({text, shape})` | POST `/api/geometry/parse/` (demo: GeoJSON and coordinates in the browser; WKT needs the API) |
| `deleteFileOnUnload(id, token)` | keepalive DELETE used by `lib/pending-deletes.ts` |

Errors: every failure throws `ApiError` (`status`, `code`, `message`, `details`, `retryable`).
`status` is 0 for `network_error` and `aborted`. It parses `{"error": {...}}` and FastAPI's
`{"detail": [...]}` (→ `invalid_params`). `isApiError(e)`, `errorFromBody(status, body)`, and
`describeError(e)` → `{title, message, fix, retryable, code}` (known codes from
`airbrief_core/errors.py` have a "how to fix" text; also accepts the `error` object stored on
`FileInfo`/`AnalysisInfo`).

### Demo mode (`NEXT_PUBLIC_DEMO=1`)

The client reads `${NEXT_PUBLIC_BASE_PATH}/demo/...` (generated by
`python -m seed.demo_export --out frontend/public/demo`):

| File | Shape | Used by |
|---|---|---|
| `demo/meta.json` | `Meta` | `getMeta`, `getHealth` |
| `demo/samples.json` | `SampleInfo[]`, each with `dataset_id` | `listSamples`, `loadSample` |
| `demo/files.json` | `{items: FileInfo[], total}` | `listFiles` (paged client-side) |
| `demo/files/{id}.json` | `FileInfo` | `getFile` |
| `demo/files/{id}/measurements.json` | `MeasurementsPage` with **all** items | `getMeasurements` (filtered/paged client-side) |
| `demo/files/{id}/features.json` | `DatasetFeatureCollection` | `getFeatures` |
| `demo/files/{id}/features/{index}.json` | `FeatureRecord` | `getFeature` |
| `demo/analyses/index.json` | `{items: AnalysisInfo[], total}` | `listAnalyses` (filtered by `dataset_id`) |
| `demo/analyses/{id}.json` | `AnalysisInfo` | `getAnalysis` |
| `demo/context.json` | `ContextResponse`: `GET /api/context/` for the first pack's bbox, cut to 2° × 2° | `getContext` (cut to the view in the browser), `getPlaces` (its aerodromes) |
| `demo/briefs/{id}.json` | `Brief`: `POST /api/briefs/` for that one analysis | `createBrief` (several merged by `mergeDemoBriefs`, exactly as `build_brief`; the brief and operation hashes are left out) |
| `demo/health.json`, `demo/manifest.json` | `Health`; `{generated_utc, version, datasets, analyses}` | not read (diagnostics) |
| `demo/samples/<file>` | raw FlightMD report JSON; `samples.json` entries carry `download` (path relative to `demo/`) | not read yet |

Feature records are exported for the first 500 features of each dataset; FlightMD report samples
have no `dataset_id` (they are not datasets).

Mutations (`uploadFile`, `deleteFile`, `createAnalysis`, `cancelAnalysis`, `deleteAnalysis`,
`fetchFlightmd`, `downloadBriefBundle`, and saving a drawn operation) throw `ApiError(403, "demo_readonly")`. `loadSample(name)` looks up the sample's
`dataset_id` in `samples.json` and returns that `FileInfo`. Export URLs are `null`: build exports
client-side with `downloadBlob`. Missing files → `ApiError(404, "not_found")`.

### `queries.ts` — React Query hooks
`queryKeys` (`health, meta, files(params), filesAll, file(id), measurements(id, q), features(id),
feature(id, i), fileScope(id), samples, analyses(datasetId), analysesAll, analysis(id)`),
`POLL_INTERVAL_MS` (1000), `shouldRetry`, `createQueryClientOptions()`.

| Hook | Notes |
|---|---|
| `useHealth()` | no retry |
| `useMeta()` | `staleTime: Infinity` |
| `useFiles({limit, offset})` | keeps previous page; polls 2 s while any file is active |
| `useFile(id)` | **polls every 1 s while PENDING/PROCESSING**; disabled for falsy id |
| `useMeasurements(id, query, {enabled})` | keeps previous page while paging |
| `useFeatures(id, {enabled})` | FeatureCollection for the map |
| `useFeature(id, index)` | disabled when index is null |
| `useSamples()` | |
| `useAnalyses(datasetId, {ids})` | polls 2 s while any is active; `ids` = `useMyDatasetAnalysisIds(datasetId)` keeps this browser's runs listed |
| `useAddedByVisitor()` | predicate: another visitor's run on the dataset ("Added by another visitor") |
| `useAnalysis(id)` | **polls every 1 s while PENDING/RUNNING** |
| `useUpload()` | variables `{file, crsOverride?, origin?, onProgress?, signal?}`; seeds `useFile` cache |
| `useLoadSample()` | variables: sample name |
| `useDeleteFile()` | variables: id |
| `useCreateAnalysis()` | variables: `AnalysisRequest`; seeds `useAnalysis` cache |
| `useCancelAnalysis()`, `useDeleteAnalysis()` | variables: id |
| `useFetchFlightmd()` | variables: report URL; returns the report JSON |
| `useRecentFiles(limit)`, `useMyAnalyses()`, `useAllAnalyses({enabled})`, `useCanModify(id)` | scoped listings (`?ids=` of this browser's items on public instances); whether this browser holds the owner token |
| `useMyFiles(limit)` | only this browser's own datasets (`?ids=`), newest first, even where the server lists everything (all of them in the static demo); *Zones from layers* uses it and shows five, plus the chosen ones, behind "Show all" |
| `useContextLayers(bbox, layers, ringsProfile)` | one query per switched-on context layer → `ContextLayerData[]`; the caller passes the view padded by half its size and snapped to 0.05°, and the rule set of the rings (`usePlan().zoneProfile`, set by the open panel through `useRingsProfile`; cached per rule set) |
| `usePlaces(q, {enabled})` | debounced place search |
| `useParseGeometry()` | variables: `{text, shape}` |
| `useAnalysisTables(id)`, `useCreateBrief()` | the CSV table list of an analysis; variables: `BriefRequest` |

All hooks are typed `UseQueryResult<T, ApiError>` / `UseMutationResult<T, ApiError, V>`.

### `store.ts` — zustand
- `useSettings` (persisted, key `airbrief-settings`, hydrated after mount by Providers):
  `units: "metric"|"imperial"`, `reduceMotion: "system"|"on"|"off"`,
  `basemap: "openfreemap"|"none"`, `flightmdUrl: string`; setters `setUnits`,
  `setReduceMotion`, `setBasemap`, `setFlightmdUrl`, `resetSettings`. Also `DEFAULT_SETTINGS`,
  `rehydrateSettings()`, `useSettingsHydrated()`, `useUnits()`. Theme is owned by next-themes.
- `useUi` (transient): `leftOpen`, `rightOpen`, `drawerOpen`, `drawerHeight`, `hiddenLayers`
  (ids like `"airspace:zones"`), `hoveredFeature`, `focusedFeature`, `view3d`,
  `playback {playing, time, speed}`, `commandOpen`; actions `setLeftOpen/toggleLeft`,
  `setRightOpen/toggleRight`, `setDrawerOpen/toggleDrawer`, `setDrawerHeight`,
  `setLayerHidden/toggleLayer/isLayerHidden`, `setHoveredFeature`, `setFocusedFeature`,
  `setView3d`, `setPlaying/togglePlaying`, `setPlaybackTime`, `setPlaybackSpeed` (0.25–4),
  `resetPlayback`, `setCommandOpen`, `resetWorkspace` (call when the dataset changes).
  Constants `PLAYBACK_SPEEDS`, `DRAWER_MIN_HEIGHT`, `DRAWER_DEFAULT_HEIGHT`.
  Select narrowly: `useUi((s) => s.hoveredFeature)`.

### `url-state.ts` — the URL is the source of truth
`useWorkspaceParams()` → `{ datasetId, featureIndex, featureIndices, module, analysisId, time,
setDataset, setFeature, setFeatures, setModule(m, {keepAnalysis}), setAnalysis, setTime, update(patch),
hrefWith(patch, pathname?) }`. Params: `d`, `f` (index or `1,4,7`), `m` (`ModuleId`), `a`, `t`
(seconds, 0.1 s). Setters use `router.replace(url, { scroll: false })` and merge with pending
changes, so calling several in one handler is safe. `setDataset` clears `f/a/t`; `setModule`
clears `a/t` unless `keepAnalysis`. Don't write `t` every animation frame: keep live playback
time in `useUi().playback.time` and write `t` on pause/seek. Also `PARAM`,
`parseWorkspaceParams(searchParams)`, `applyWorkspacePatch(params, patch)`,
`workspaceHref(patch, pathname = "/w/view")`.

### `format.ts` — metric | imperial (inputs always SI)
`formatArea` (m² → m²/ha/km² or ft²/ac/mi²), `formatLength` (m/km or ft/mi), `formatHeight`
(m or ft, never km), `formatSpeed` (m/s or mph; `{as: "kmh"|"kn"|"mph"|"ms"}`),
`formatNumber(n, {decimals, maximumFractionDigits, compact, signed})`,
`formatPercent(v, {fraction})` (value already in % unless `fraction: true`),
`formatDuration(s, {precise})`, `formatClock(s)`, `formatBytes`, `formatCoord(lon, lat,
{format: "dd"|"dms", precision, order})`, `formatBearing`, `formatDate(v, {withTime, utc})`,
`formatRelative`. Each quantity also has `xParts()` → `FormattedValue {value, unit, text}` for
`KpiTile`. `plural(n, one, many?)` ("1 feature", "50,000 features"). Editable inputs:
`inputUnit(unit, units)` (the imperial unit of a metric input such as "m" → "ft", with both
conversions; `NumField` uses it), `parseNumberText(text)` (typed numbers with thousands
separators or a decimal comma; ambiguous text is an error). `localizeUnits(text, units)` puts the
metric quantities inside an API sentence into the user's units.
Null/NaN → `"—"` (`EMPTY`). Locale is fixed (`LOCALE = "en-US"`) to avoid hydration mismatches.

### `motion.ts`
`usePrefersReducedMotion()` (OS + setting), `useSystemPrefersReducedMotion()`,
`resolveReducedMotion(pref, system)`, `prefersReducedMotionNow()` (non-hook, for map camera
code), `motionDuration(ms, reduced)`, `useMotionAttribute()` (used by Providers). Reduced motion
⇒ map `jumpTo` instead of `flyTo`, no slides, no autoplay; shows start paused.

### `colors.ts`
- Conversions: `hexToRgba`, `rgbToHex`, `withAlpha`, `rgbaCss`; types `RGB`, `RGBA` (0–255,
  deck.gl format).
- Theme tokens: `readCssVar(name)`, `tokenRgba(name, alpha)`, `cssVarRef(name)` (`var(--x)` for
  recharts/SVG), type `TokenName`. Re-read when `resolvedTheme` changes.
- Zones: `ZONE_HEX`, `ZONE_RGB`, `ZONE_LABEL`, `ZONE_PATTERN` (green `dots`, yellow `diagonal`,
  red `cross`), `zoneColor(zone, alpha)`, `zoneSwatchStyle(zone)` (legend swatch CSS).
- Hatch atlas: `createHatchAtlas()` → `HatchAtlas {atlas (PNG data URL), mapping, width,
  height}` with patterns `solid · dots · diagonal · cross` (32 px tiles, white on transparent).
  Browser-only, cached. Use with deck.gl:
  ```ts
  new GeoJsonLayer({
    extensions: [new FillStyleExtension({ pattern: true })],
    fillPatternAtlas: hatch.atlas, fillPatternMapping: hatch.mapping, fillPatternMask: true,
    getFillPattern: (f) => ZONE_PATTERN[f.properties.zone], getFillPatternScale: 1,
    getFillColor: (f) => zoneColor(f.properties.zone),
  })
  ```
- Risk: `RISK_RAMP` (10 stops), `riskColor(level, alpha)`, `riskHex`, `riskTextColor`.
- Continuous: `VIRIDIS_STOPS` (9), `viridis(t, alpha)`, `viridisHex(t)`; `RDBU_STOPS`, `rdbu`,
  `rdbuHex`; `normalize(v, min, max)`, `legendBreaks(min, max, count)`.
- Status: type `Tone` (`ok · warn · danger · info · neutral`), `TONE_TOKEN`, `toneForVerdict`,
  `VERDICT_LABEL`, `toneForZone`, `toneForSeverity`, `toneForAnalysisStatus`,
  `toneForFileStatus`, `toneForMeasurementStatus`, `MEASUREMENT_STATUS_LABEL`,
  `toneForFlyability`, `toneForAglBand`, `toneRgba`, `toneVar`.
- Series: `CATEGORICAL`, `categorical(i, alpha)`.

### `modules.ts`
`ModuleId` = `"measure" | "airspace" | "ground-risk" | "evtol" | "shows" | "survey" |
"flight-audit" | "environment" | "line-of-sight"` (nine modules), `MODULE_IDS` (rail order),
`RESULT_FOCUS_LAYERS` (the result layers brought into view per kind), `analysisParamsSummary`
(the History line, e.g. "950 m AMSL · grid"), `MODULE_META[id]` →
`{id, label, description, icon (lucide name), kinds}`, `MODULE_LIST`, `isModuleId`,
`moduleForKind(kind)`, `ModuleIconName`, and the implementation contract types
(`ModuleDefinition`, `ModulePanelProps`, `ModuleResultsProps`, `ModuleLayerContext`,
`ModuleLayerInfo`, `ModuleLayerOutput`, `LegendSpec`, `LegendItem`). See "Module contract" below.

### Shared helpers

| File | Exports | Notes |
|---|---|---|
| `lib/plan.ts` | `operationFile`, `operationCollection`, `circleRing`, `pinCircleProblem`, `pathLengthM`, `ringAreaM2`, `draftGeometry`, `PIN_CIRCLE_SIDES` (64), `PIN_RADIUS_PRESETS_M`, `MIN_PIN_RADIUS_M` (1), `MAX_PIN_RADIUS_M` (50,000) | An operation (pin, line, area, typed, pasted, device location) as a GeoJSON file uploaded through `POST /api/files/`; a pin is the Point plus the "Pin radius" 64-gon; "≈" previews on the mean sphere |
| `lib/plan-store.ts` | `usePlan`, `usePlanActive`, `MIN_VERTICES`, `GOTO_RADIUS_M` | Transient plan state: drawing, the unsaved draft, the "Go to" target, the dialogs and the context-layer switches (kept across dataset changes, off by default) |
| `lib/coords.ts` | `parseCoordinateText`, `swapCoordinateText`, `formatLatLon`, `MAX_TYPED_POINTS` | The browser twin of `airbrief_core/geo/parse.py` (decimal, hemispheres, DMS, decimal commas, `[lon, lat]`); the server re-reads a saved operation |
| `lib/map-pick.ts` | `startMapPick`, `cancelMapPick`, `consumeMapPick`, `useMapPick`, `useMapPickKeyboard` | "Place on map" for panels (line-of-sight observers and radio target, ground-risk assemblies; the show origin uses the plan pin tool instead): the next map click, or Space / Enter at the map centre, gives `[lon, lat]`; Escape cancels; one request at a time |
| `lib/area-units.ts` | `useAreaUnits`, `useLocalAreaUnit`, `DEFAULT_BIGHA_M2`, `BIGHA_MIN_M2`, `BIGHA_MAX_M2` | The land-area unit (hectare, acre, guntha, bigha) and the bigha size, in localStorage behind guards; conversions in `lib/format.ts` (`localAreaParts`) |
| `lib/csv.ts` | `csvCell`, `measurementsToCsv` | One CSV writer for the measure panel, the demo dataset export and the report; text that would start a formula is prefixed with a quote, as in the API |
| `lib/labels.ts` | `FILE_TYPE_LABEL`, `CRS_SOURCE_LABEL`, `FILE_EXPORT_LABEL`, `ANALYSIS_EXPORT_LABEL`, `DATAPACK_LAYER_LABEL`, `DAYLIGHT_PROFILE_LABEL`, `EMI_KIND_LABEL`, `GRB_RULE_LABEL`, `ASSEMBLY_BAND_LABEL`, `HEIGHT_REFERENCE_LABEL`, `TAKEOFF_SOURCE_LABEL`, `OBSTACLE_HEIGHT_SOURCE_LABEL`, … | Display names of API enum values, so one thing never has two names |
| `lib/pending-deletes.ts` | `scheduleDatasetDelete`, `undoDatasetDelete`, `resumePendingDeletes` | Deletes with an undo window that survive the tab closing: recorded with their due time, sent on `pagehide` (keepalive) and on the next load |
| `lib/urls.ts` | `validateFlightmdUrl`, `safeHttpUrl`, `flightmdReportUrl` | Only absolute http(s) URLs without credentials become links |
| `lib/datasets.ts` | `needsCrs` | A processed dataset without a CRS and nothing measured is not shown as ready |
| `lib/brief-demo.ts` | `mergeDemoBriefs`, `operationSection`, `parameterChecks`, `resolveAircraft` | The static demo's brief: merges `demo/briefs/{id}.json` exactly as `build_brief` |
| `lib/plan-demo.ts` | `demoContext`, `demoPlaces`, `demoParseGeometry` | The static demo's answers to the plan endpoints from `demo/context.json` and the browser parser |
| `lib/batch.ts` | `BATCH_KINDS`, `batchLimit`, `isBatchResult`, `isBatchAnalysis`, `batchResult`, `batchParams`, `batchFromParams`, `attributeNames`, `batchFeatureCount`, `BATCH_COLUMNS`, `batchVerdictColors` | One result per feature: which kinds and limits (1,000, line of sight 500), detection of a per-feature result, the `batch` param and its restore, the table columns per kind, verdict colours |

## Map (`components/map/*`)

| File | Exports | Notes |
|---|---|---|
| `map-loader.tsx` | `MapLoader` (forwardRef → `MapViewHandle`), `MapSkeleton`, re-exported types | **Use this, never `map-view.tsx` directly.** `next/dynamic({ssr:false})` + Suspense skeleton |
| `map-view.tsx` | `MapView`, `MapViewWithHandle`, `OPENFREEMAP_STYLES`, `blankStyle`, types `MapViewProps`, `MapViewHandle`, `MapFocusTarget`, `PointerModifiers`, `MapCameraOptions` | MapLibre 6 + deck.gl `MapboxOverlay` (`interleaved: false`); the MapLibre worker is served from `public/vendor/` (`scripts/copy-maplibre-worker.mjs`) |
| `map-controls.tsx` | `MapControls` | zoom, pan buttons (dragging alternative), fit, reset north (3D), 2D/3D, shortcuts, "no basemap" note |
| `legend.tsx` | `Legend` | renders `LegendSpec[]`: categorical (swatch shapes, zone hatches) or ramp (numeric breaks + unit); "View as table" per legend with a `layerId` |
| `coordinate-readout.tsx` | `CoordinateReadout` | pointer or centre coordinates, DD/DMS toggle (not a live region) |
| `shortcuts-dialog.tsx` | `ShortcutsDialog` | opened with `?` on the map |
| `layers/common.ts` | `mapPalette`, `geoJsonLayer`, `hatchZoneLayer`, `textLabelLayer`, `h3Layer`, `pointLayer`, `pathLayer`, `droneCloudLayer`, `dronePoints`, `zoneLegend`, `rampLegend`, `geometryBounds`, `collectionBounds`, `boundsCenter`, `drawableFeatures`, `filterHiddenLayers`, `filterHiddenLegends`, `layerIdMatches`, `MAP_FONT_FAMILY` | deck.gl factories, pixel units by default |
| `layers/dataset-layer.ts` | `DATASET_LAYER_ID` (`"dataset:features"`), `datasetLayers`, `datasetLegend`, `datasetFeatureLabel`, `pickedFeatureIndex` | dataset features with hover / selection (thick outline + label) / keyboard focus (dashed) |
| `layers/context-layer.ts` | `contextLayers(data, ctx)`, `describeContextPick`, `contextLayerId`, `isContextLayerId`, `CONTEXT_LAYER_LABEL`, `CONTEXT_LAYER_HINT` | ids `context:<key>`; below the dataset and module layers; zones and rings with the airspace module's hatches; hover tooltip only, never selection |
| `layers/plan-layer.ts` | `planLayers(state, theme)` | the shape being drawn (vertices, dashed rubber band), the unsaved draft (pin with its radius circle, line, area) and the "Go to" target; above everything, never pickable |
| `map-overlay.tsx` | `useMapOverlay`, `useMapObstacle`, `computeOverlayLayout`, `OVERLAY_GAP`, `LEGEND_BOTTOM` | one layout for the overlays: top-left basemap notice then the plan toolbar, top-right controls, bottom-left legend, bottom-right readout then attribution; overlays avoid each other |
| `map-attribution.tsx` | `MapAttribution`, `parseAttributions` | themed attribution, last in the overlay tab order; folds behind an (i) button on narrow maps |

`MapViewProps`: `layers`, `bbox` + `fitKey` (refit when the key changes; jumps under reduced
motion), `basemap` (`openfreemap` → `https://tiles.openfreemap.org/styles/dark` or
`.../styles/positron` by theme; `none` → offline background-only style; a style that fails to
load falls back to the blank style with a notice), `theme`, `view3d` (pitch 50°, rotate/tilt
enabled), `reducedMotion`, `units` (scale bar), `focusTargets` + `focusedFeature` +
`onFocusFeature` + `onSelectFeature` (N / Shift+N / Enter), `onClearSelection` (Esc),
`onHover(info | null)`, `onClick(info, {shiftKey, ctrlKey, metaKey})`, `getTooltip(info)`,
`onShowShortcuts` (`?`), `onFit` (`F`), `id` (skip-link target; focus is forwarded to the
canvas), `ariaLabel`, `initialView`, `children` (overlays; the overlay box is
`pointer-events-none`, so give interactive overlays `pointer-events-auto`).
`MapViewHandle`: `fitBounds(bbox, {padding, maxZoom})`, `flyTo({center, zoom, pitch, bearing})`
(jumps under reduced motion), `getCanvas()`, `zoomIn()`, `zoomOut()`, `panBy([x, y])`,
`resetNorth()`, `focus()`, `getMap()`.

## Workspace (`components/workspace/*`, route `app/w/view`)

`app/w/view/page.tsx` renders `<Suspense>` + `WorkspaceClient` (client-only, `ssr: false`) →
`Workspace`. The URL drives everything (`d f m a t`). The active module is `m`, else the owner
of `a`, else `measure`. Without `d` the workspace still works when `m` or `a` is set (flight audit).

| File | Purpose |
|---|---|
| `workspace.tsx` | layout (rail · layers panel · map · inspector · drawer; inspector sheet 768–1023 px; read-only viewer + bottom sheet < 768 px), data loading, selection, skip links ("Skip to map" `#map`, "Skip to results" `#results`), job announcements |
| `workspace-map.tsx` | builds dataset + module layers and legends, picking, keyboard targets, controls, legend. It is the only part that re-renders on hover and playback |
| `module-rail.tsx` | `nav[aria-label="Workspace modules"]`, links (icon + label, `aria-current="page"`) |
| `layers-panel.tsx` | layer switches (+ "View as table"), searchable feature `DataTable` synced with the map; also exports `featureRows`, `filterFeatureRows` |
| `inspector.tsx` | measure: feature details (status, "Why?" popover, measurement card, attributes) + the measure panel; other modules: the module `Panel` ("Open on a larger screen" below 768 px) |
| `results-drawer.tsx` | resizable (drag handle is a focusable `role="separator"`: arrows, PageUp/PageDown, Home/End; plus taller/shorter buttons). Tabs: module results, "Layers as table" (dataset + every `result.layers` key), history |
| `job-progress.tsx` | `JobProgress` (staged), `AnalysisJobProgress` (Cancel while active), `FileJobProgress` |
| `dataset-header.tsx` | name, status, counts, exports menu (API URLs; client-side CSV/GeoJSON in demo), report link, delete with confirm + undo toast (the request is sent after `UNDO_DELETE_SECONDS`) |
| `analysis-runner.ts` | `useAnalysisRunner()`, `useAnalysisStarted()`, `useAnalysisWatcher()`, `useCancelJob()`, `analysisKindLabel()` |
| `live-region.tsx` | `LiveRegion` (mounted by the workspace), `useAnnouncer`, `announce(message)` |
| `status-badges.tsx` | `ToneBadge`, `MeasurementStatusBadge`, `FileStatusBadge`, `AnalysisStatusBadge`, `ANALYSIS_STATUS_LABEL` |
| `mobile-viewer.tsx`, `workspace-skeleton.tsx`, `use-media-query.ts` | below-768 px sheet (cards), loading layout, `useMediaQuery` / `useWorkspaceBreakpoint` |
| `operation-input.tsx` | Plan without a file: the map's top-left **New operation** toolbar (Go to, Pin, Line, Area, Coordinates…, Paste…, My location; phones: Pin and My location) and the card of the current step (drawing, unsaved draft, location error, Go to result); reads `?plan=pin\|line\|area\|coordinates\|paste` once and drops it from the URL |
| `plan-actions.ts` | `startPlanTool`, `locateDevice` / `useLocate` (Geolocation API, high accuracy, 20 s), `useSaveOperation` (draft → GeoJSON upload → dataset), `goToPlace`, `QUICK_CHECK_ALTITUDE_M` (the phone pin card's 120 m airspace check) |
| `plan-map.ts` | map clicks and keys while drawing (Enter finishes, Escape cancels, Backspace / Delete / Ctrl+Z undo, Space places a vertex at the map centre); plan drawing and `lib/map-pick.ts` requests are mutually exclusive |
| `plan-dialogs.tsx` | Coordinates dialog (parsed as you type by `lib/coords.ts`) and Paste dialog (`POST /api/geometry/parse/`) |
| `plan-start.tsx` | "Draw, pin or open a dataset" in module panels without a dataset |
| `context-toggles.tsx` | Layers panel → **Context layers**: six switches with feature counts, "cut at the limit", "Zoom in" and table buttons |

## Module contract

Each module lives in `components/modules/<dir>/index.tsx` (dirs: `measure`, `airspace`,
`ground-risk`, `environment`, `line-of-sight`, `evtol`, `shows`, `survey`, `flight-audit`), starts
with `"use client"`, and exports **exactly** `export const definition: ModuleDefinition`.
`components/modules/registry.tsx` imports `{ definition }` from each dir into
`MODULES: Record<ModuleId, ModuleDefinition>` (it also exports `MODULE_ICONS`, `MODULE_DEFINITIONS`,
`moduleDefinitionForKind`, `analysisBelongsTo`, `moduleLayersForKind(module, kind, batch = false)`,
which lists only the `batch: true` layers for a per-feature analysis and only the others otherwise).
Extra files (forms, charts) go in the same dir. The types are in `lib/modules.ts`.

| Dir / file | Contents |
|---|---|
| `airspace/` | `index.tsx` (panel with the height reference, results, layers), `assessment-notice.tsx` (the "Airspace not assessed" banner, the **Red zones are not published as open data** notice and the permanent **Verify on Digital Sky** link), `zone-inputs.tsx` (`ZoneRulesField`, the Rules select also used by the flight audit and eVTOL, with `zoneRulesFromParams` / `zoneRulesParams`; the planned window; *Zones from layers* over `useMyFiles` with the column mapper; aerodrome status; `useRingsProfile`, which puts the open panel's Rules into the plan store for the context rings), `zone-results.tsx` (`BandChips`, the Active and Validity columns, `InactiveZones`) |
| `ground-risk/` | `index.tsx`, `volume.ts` (SORA 2.5 operational-volume formulas for the live preview), `volume-fields.tsx` (the *Operational volume* section), `assemblies-entry.tsx` (*Assemblies and events*, typed or placed on the map, at most 100), `results.tsx` (evidence label, S_CV terms, Table 2 with the applying cell, adjacent area, assemblies), `layers.ts` (nested FG / CV / GRB / adjacent-area polygons, density squares, hotspots, assemblies) |
| `environment/` | `index.tsx`, `fields.tsx` (validated date and time inputs), `time.ts` (local date and zone-offset helpers; the API computes in UTC), `daylight-ribbon.tsx` (24 h ribbon with patterns, text description and table), `sun-chart.tsx` (sun elevation with the window and sun-window bands, table alternative) |
| `line-of-sight/` | `index.tsx`, `model.ts` (defaults and observer detection, as `api/services/line_of_sight.py`), `panel.tsx` (observers typed or placed on the map, radio band, VLOS guidance), `results.tsx`, `profile-chart.tsx` (radio path profile with the Fresnel edge, table alternative), `layers.ts` (visible tint, hidden hatch, hidden patch, observers, radio paths, VLOS ring) |
| `survey/` | `index.tsx`, `model.ts` (defaults = `SurveyParams` / `SprayParams`), `panel.tsx` (mapping or spraying, area or corridor, camera, height reference, batteries and transit, GCPs), `results.tsx` (GSD range, worst overlap, spraying figures in local area units), `saved-cameras.ts` (custom cameras remembered in localStorage, at most 8, guarded) |
| `flight-audit/` | `index.tsx` (sources Dataset track / Link / Upload / Sample, additional checks, results), `tracks.ts` (the dataset's timed tracks and training areas, the same name rules as `api/services/tracks.py`), `limits.ts` (the height limits actually used) |
| `evtol/`, `shows/`, `measure/` | one `index.tsx` each (eVTOL now with the AGL / AMSL cruise reference; shows with the audience rule, pads, endurance, origin and "Check airspace for this show box") |
| `shared.tsx` | section headings, verdict banner, issues, disclaimers, `NumField`, switches, the Advanced disclosure, the demo-aware Run button, the profile chart with its table, `DemCaveat` (the terrain-model chip), map-focus helpers |
| `analysis-form.tsx` | forms follow the analysis on screen: a restored, reopened or shared analysis sets the fields from its parameters (`paramNumber`, …); "Reset to defaults" |
| `height-profile.tsx` | terrain-true height profile (airspace, eVTOL corridor): terrain, flight line, ceiling and minimum lines, stretches above the ceiling (red, hatched) and below the minimum (amber, dotted), each labelled, with a table |
| `h3-layers.ts` | `h3Collection`: H3 results as polygon FeatureCollections, the same layer names as the API's GeoJSON export, so "Layers as table" lists them |
| `batch.tsx` | One result per feature for airspace, ground risk, survey, environment and line of sight: `BatchToggle` and `batchDisabledReason` (the switch, "Name each result by"), `BatchResults` (summary bar, search, sortable virtualised table), `buildBatchLayers`, and `withBatch(definition, extra?)`, which wraps a module definition: per-feature analyses get `BatchResults`, the coloured features, their pick text and the `batch: true` layer "Result per feature" (`resultLayer: "features"`); other analyses keep the module's own |

```ts
import { MODULE_META, type ModuleDefinition } from "@/lib/modules";

export const definition: ModuleDefinition = {
  ...MODULE_META.airspace,          // id, label, description, icon, kinds: never override
  Panel: AirspacePanel,             // ComponentType<ModulePanelProps>     (required)
  Results: AirspaceResults,         // ComponentType<ModuleResultsProps>   (optional)
  layers: [                         // readonly ModuleLayerInfo[]          (optional)
    { id: "airspace:zones", label: "Airspace zones", resultLayer: "zones" },
    { id: "airspace:grid", label: "Flyability grid", resultLayer: "grid" },
  ],
  buildLayers: (analysis, ctx) => ({ layers: [/* deck.gl */], legends: [/* LegendSpec */] }),
  describePick: (info) => "Red zone · VOBL 0–5 km",   // optional hover text
  onPick: (info) => false,                            // optional click handler
};
```

### `Panel(props: ModulePanelProps)`

Inspector content: stepper (Area → Parameters → Run → Results), presets first then a
`Collapsible` "Advanced", `Field` / `NumberInput` with units in labels, validation on blur, and an
`ErrorSummary` focused after a failed submit.

| Prop | Type | Meaning |
|---|---|---|
| `datasetId` | `string \| null` | URL `d` (null in the no-dataset flow) |
| `file` | `FileInfo \| null` | the dataset (status, bbox, layers, totals) |
| `features` | `DatasetFeatureCollection \| null` | WGS84 features (`_index`, `_name`, `_type`, `_status`, …) |
| `selectedFeatures` | `number[]` | URL `f` (empty = whole dataset) |
| `analysis` | `AnalysisInfo \| null` | URL `a`, **only** when `analysis.kind` is in this module's `kinds` (any status) |
| `onAnalysisStarted` | `(a: AnalysisInfo) => void` | call after creating an analysis yourself: writes `?a=` (and `m`), opens the drawer, announces |

To run, prefer `useAnalysisRunner()` from `@/components/workspace/analysis-runner`:
`const { run, isPending, error } = useAnalysisRunner(); await run("airspace", params)`. It sends
`{kind, dataset_id: d, feature_indices: f or null, params}` (override with
`run(kind, params, { datasetId: null, featureIndices: [] })`), then does what
`onAnalysisStarted` does, and toasts on failure (`error` stays available for inline display).
Do not toast completion yourself: the workspace watches `a` (`useAnalysisWatcher`), toasts and
announces "finished / failed / cancelled", and opens the drawer. The drawer shows progress and
Cancel (`AnalysisJobProgress`); panels may show it too. Disable Run when `DEMO_MODE` (mutations
throw `demo_readonly`). Panels are not rendered below 768 px.

### `Results(props: ModuleResultsProps)`

Drawer "results" tab content.

| Prop | Type | Meaning |
|---|---|---|
| `analysis` | `AnalysisInfo \| null` | modules with `kinds`: never null (the workspace shows an empty state instead) but may be PENDING / RUNNING / FAILED / CANCELLED. Use `analysisResult(analysis, "airspace")` for the typed result or null. `measure` always gets null |
| `datasetId` | `string \| null` | URL `d` |
| `file` | `FileInfo \| null` | |

The drawer already renders progress and Cancel (active) or an `ErrorState` (FAILED) above
`Results`, so render the results only when the result exists (`return null` otherwise). The
container is a flex column with bounded height: KPIs (`KpiGrid`) first, then charts (recharts,
downsample above 1000 points, dashed limit lines, anomalies as shape + text) and `DataTable`s
(`maxHeight` or `className="min-h-0 flex-1"`). Every anomaly (exceedance, conflict, violation)
must be listed in a table, not only drawn. Rows that map to dataset features call
`useWorkspaceParams().setFeature(i)` and `useUi().setHoveredFeature(i | null)`. Results also
render in the below-768 px viewer, so keep them readable at 375 px.

### `layers: ModuleLayerInfo[]`

`{ id, label, kinds?, resultLayer?, batch? }`. `id` is `${moduleId}:${key}`; `kinds` limits the layer to
some analysis kinds (eVTOL siting vs corridor); `resultLayer` names the key in
`analysis.result.layers` used by "View as table"; `batch: true` marks a layer listed only for
per-feature analyses, which list nothing else (`withBatch` adds it). The Layers panel lists these (switch + table
button) once the analysis is COMPLETED; hidden ids live in `useUi().hiddenLayers`.

### `buildLayers(analysis, ctx) → { layers: Layer[]; legends: LegendSpec[] }`

Called by the workspace (`workspace-map.tsx`) only when `analysis.status === "COMPLETED"`,
`analysis.result !== null` and `analysis.kind` is in `kinds`, for the module that owns the URL
analysis (even when another module is active). It runs on every hover, selection, theme and
playback-time change: keep it pure and cheap, and memoise heavy transforms per `analysis.id`
(e.g. a module-level `Map<string, Prepared>`). Exceptions are caught and logged (nothing drawn).

- `ctx: ModuleLayerContext` = `{ analysis, features, hiddenLayers: ReadonlySet<string>,
  hoveredFeature, selectedFeature, time (seconds, live during playback), reducedMotion, units,
  hatch: HatchAtlas | null, theme: "dark" | "light" }`. Put `ctx.theme`, `ctx.time` and any
  selection in each layer's `updateTriggers` (the factories accept `updateTriggers: unknown[]`).
- Layer ids: `id === info.id` or `` `${info.id}/${suffix}` `` (e.g. `airspace:zones/hatch`,
  `airspace:zones/labels`). The workspace removes layers whose id matches a hidden id (you may also
  skip them yourself with `ctx.hiddenLayers`). Ids that match no `ModuleLayerInfo` are always shown.
- Draw order: the dataset layer is below all module layers; your array order is kept.
- Use the factories in `@/components/map/layers/common` and colours from `mapPalette()` /
  `lib/colors`: zones `hatchZoneLayer` with `ctx.hatch`; continuous `viridis`; risk `riskColor`;
  H3 grids `h3Layer`; drones `droneCloudLayer` with `ShowFrames.origin` (it uses
  `COORDINATE_SYSTEM.METER_OFFSETS`); routes `pathLayer` (`dashed: true` for dashed lines);
  markers `pointLayer`; labels `textLabelLayer`. Set `pickable: true` on layers that should show
  tooltips or react to clicks.
- `legends: LegendSpec[]` = `{ id, title, kind: "categorical" | "ramp", items?: LegendItem[]
  ({label, color (CSS), shape?: "fill" | "line" | "dashed-line" | "point" | "ring" | "hex", zone?,
  detail?}), stops?: string[] (CSS colours, low → high), breaks?: number[] (≥ 2, evenly spaced),
  unit?, formatBreak?, layerId?, note? }`. Give each legend the `layerId` of its `ModuleLayerInfo`
  so it hides with the layer and its "View as table" button opens that table. Helpers:
  `zoneLegend`, `rampLegend`. Numeric legends always show breaks and the unit.

### `describePick(info: PickingInfo) → string | null`

Hover tooltip text for objects of your layers (`info.layer.id`, `info.object`). Keep it short
("Site 2 · score 81"). Keyboard users get the same values from the tables, so never put
information only here.

### `onPick(info: PickingInfo) → boolean`

Click on one of your layers. Return `true` when handled (dataset feature selection is then
skipped). Use it e.g. to pick eVTOL sites; keep the state in your own zustand store or the URL.

### Playback, announcements, camera

- **Playback (shows, flight audit):** `useUi().playback` (`playing`, `time`, `speed`;
  `setPlaying`, `togglePlaying`, `setPlaybackTime`, `setPlaybackSpeed`, `resetPlayback`). Drive
  the clock with `requestAnimationFrame` in your Results/transport component, call
  `setPlaybackTime` every frame, and write the URL only on pause/seek
  (`useWorkspaceParams().setTime(t)`). The workspace copies URL `t` into `playback.time` on load;
  `ctx.time` follows `playback.time`. Never autoplay; start paused under reduced motion.
- **Announcements:** `announce("Conflict at 48.0 s: D12 and D31, 1.2 m")` from
  `@/components/workspace/live-region` writes to the workspace `aria-live="polite"` region.
- **Camera:** modules do not hold the map. The workspace fits `file.bbox`, or the bounds of all
  `result.layers` when there is no dataset; selecting a feature from a table fits to it.

## Accessibility checklist (applies to everything built on this)

No emoji; Lucide icons only (`aria-hidden` when decorative). Icon-only buttons use `IconButton`.
Visible focus (global `:focus-visible` outline — don't remove it). Status = icon + text, never
colour alone. Touch targets ≥44 px on coarse pointers. JSX text never contains a raw `'`: use
`&apos;`. Announce job completion in an `aria-live="polite"` region. Every map layer has a table
view (use `DataTable`).
