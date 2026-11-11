# Operation evidence brief, bundle and complete exports

The **brief** puts several completed analyses and the operation (operator, aircraft, planned
window, pilot position, notes) on **one A4 page**, with a vector map, a card per evidence topic,
every topic without an analysis marked **"Not assessed"**, and a provenance footer that lets anyone
reproduce it (versions, data packs with source and licence, SHA-256 of every input). The **bundle**
is the same brief as a self-contained zip for an authority, client, insurer or UAOP: printable
`brief.html`, `brief.json`, `provenance.json`, the evidence as GeoJSON and KML, every CSV table and
each analysis envelope.

Every analysis can also be exported on its own as **JSON, GeoJSON (H3 cells as polygons), KML and
CSV (any of its tables)** from the results drawer and the report page.

A brief is **evidence, not an authorisation**: it says so at the top of the sheet, in the page
margin of every printed page, in every export and in every bundle file. It never says go or no-go
and never contains a flight plan, waypoints, WPML, KMZ, `.plan` or Litchi files. A brief computed
with a synthetic (invented) data pack carries the watermark **"SYNTHETIC DEMO DATA — not
evidence"**.

## Where to find it

- Workspace, dataset header → **Export** → **Create brief** (the dataset's analyses are ticked).
- Report page (`/w/report`) → **Brief** tab, or **Export** → **Create brief**.
- Command palette (Ctrl/⌘ K) → **Create an operation evidence brief**.
- URL: `/w/report?d=<dataset>&a=<analysis>&mode=brief` (both ids optional).

## Inputs

**Analyses.** 1 to 12 completed analyses of any kind: those of the open dataset and any other from
this browser's history. By default the given analysis and the latest analysis of each kind on the
dataset are ticked. Analyses of different datasets or areas can be combined; the brief then warns.

**Operation** (every field optional; the form is remembered on this device only):

| Field (API `operation.*`) | Form | Notes |
|---|---|---|
| `operator` | Operator | ≤ 200 characters |
| `reference` | Reference | ≤ 120 characters; shown in the title |
| `aircraft.preset` | Aircraft | a UAS class preset (`Meta.uas_presets`, illustrative values) or "Custom aircraft" |
| `aircraft.name`, `takeoff_mass_kg`, `max_dimension_m`, `max_speed_ms` | Aircraft name, Take-off mass, Max dimension, Max speed | override the preset's values; the brief shows where each came from |
| `planned_start_utc`, `planned_end_utc`, `utc_offset_min` | Planned window: Date, From, To, Time zone | typed as local times; sent in UTC; an end before the start is the next day |
| `pilot_position` | Pilot longitude / latitude | `[lon, lat]` WGS84; quick fill from a line-of-sight pilot, a take-off point or a show origin of the ticked analyses |
| `notes` | Notes | ≤ 2,000 characters, printed as written |

## Method

`airbrief_core.brief.build_brief(sources, operation, packs=…) -> Brief` (stateless: no database
table, no migration). The analyses are put in a canonical order (kind, then finish time, then id),
so the order given never changes the brief or its hash.

**Sections**, always all of them and in this order. Each analysis contributes a *part* (key facts,
its main tables cut to 8 rows, its notes and disclaimer) to the sections it informs:

| Section | Filled by |
|---|---|
| Operation | the form (aircraft resolved from its preset) |
| Airspace | airspace (verdict, zones, free-flight ceiling, nearest aerodromes, "Airspace not assessed" banner when no data pack covered the area) |
| Heights and terrain | airspace, eVTOL corridor, survey (terrain, relief, planned top AMSL, height reference, take-off ground, height above ground along the route, stretches below the minimum or above the ceiling, the DEM's own caveat; "Heights not assessed" when no DEM covered a take-off or AMSL plan) |
| Obstacles | airspace, eVTOL corridor (conflicts first; "assumed height" marked) |
| Ground risk | ground risk (iGRC evidence, density, footprint, SORA 2.5 operational volume terms, adjacent area, assemblies) |
| Environment | environment (sun, planned window against the daylight profile, magnetic declination, wind at height, power lines and masts) |
| Line of sight | line of sight (visual and radio line of sight, Fresnel clearance, hidden patch, observers, radio paths) |
| Survey and spray effort | survey (GSD or GSD range over the relief, photos, flight time, batteries, transit, GCPs, storage, spray mix and tank loads) |
| Flight audit | flight audit (height limit, training-area exits, distance from take-off, zones crossed, "not assessed" banners) |
| Drone show | show (separation, violations, derived or given box, audience rule and formula, pads, endurance, origin) |
| eVTOL siting and corridor | eVTOL siting and corridor |

A section with no analysis is `not_assessed` with its reason ("No environment analysis in this
brief: daylight, magnetic declination, wind and interference were not assessed."), never left out.
A data gap inside an analysis makes the section `partial`, with a banner.

**Cross-checks** (`warnings`): synthetic data; analyses from several datasets; analyses covering
separate areas (bounding boxes linked by overlap form one area; one warning names the groups);
several runs of one kind; an aircraft larger or faster than the ground-risk analysis assumed (its
iGRC column may be too low); an environment analysis for another date than the planned window.
Identical messages are said once.

**Provenance** (`provenance`):

- `airbrief_version`, `schema_version` (results) and `brief_schema_version` (1.0);
- `started_utc` / `finished_utc`: the earliest start and latest finish of the analyses;
- per analysis: data pack and synthetic flag, backend, times, `params_sha256` and `result_sha256`
  (SHA-256 of the **canonical JSON**, see below), and `input_sha256`, the SHA-256 of the uploaded
  file's bytes as stored (`datasets.sha256`); `null` with a note when the inputs were all in the
  parameters (drawn or pasted geometry, an inline FlightMD report), which `params_sha256` covers;
- per data pack: name, version, created time, synthetic flag, attribution, and every layer's file,
  source and licence from its manifest with the kinds of this brief that read it;
- `operation_sha256`: SHA-256 of the canonical operation (every field, defaults included);
- `brief_sha256`: SHA-256 of the canonical brief JSON **without** `provenance.generated_utc` and
  `provenance.brief_sha256` itself, so identical analyses and operation always give the same hash.

Canonical JSON (anyone can recompute every hash):

```python
json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
```

**Synthetic data.** A brief is watermarked when an analysis of a kind that reads data-pack layers
(a required or optional layer in the registry) used a synthetic pack. A show check reads no pack
layer, so it is never watermarked. When synthetic and real analyses are mixed, each synthetic part
also carries its own banner.

**The printed page.** A4 portrait, 10 mm margins; "Evidence, not authorisation" and the page number
are in the page margins (CSS page-margin boxes) and at the top and bottom of the sheet. Every size
on the sheet is relative to its font size; the page finds the largest text scale between 60 % and
100 % at which the sheet fits one page, by bisection on a hidden copy laid out at the printed width
(190 mm), and prints at that scale (the toolbar says so, e.g. "Prints on one A4 page at 77 % text
size"). A brief too long even at 60 % continues on further pages and the toolbar says how many.
The map is an inline SVG of each analysis' key layers (grids, density cells and survey lines stay
out), projected equirectangularly around the mid-latitude (x = Δlon·cos φ₀, y = Δlat), north up,
with a scale bar: an overview, never for navigation. No WebGL snapshot is needed, so it prints the
same on every GPU.

## Outputs and API

| Endpoint | Returns |
|---|---|
| `POST /api/briefs/` `{analysis_ids[1..12], operation{}}` | the `Brief` JSON |
| `POST /api/briefs/bundle/` (same body) | `airbrief_brief_<12 hex of brief_sha256>.zip` |
| `GET /api/analyses/{id}/export/?format=json\|geojson\|kml\|csv[&table=]` | one analysis |
| `GET /api/analyses/{id}/export/tables/` | its CSV tables: `{key, label, rows, multi, default}` |

Errors: 404 `not_found` (an unknown id), 409 `analysis_not_finished`, 422 `invalid_params` (no ids,
more than 12, an unknown preset, a window ending before it starts, a position outside WGS84, an
unknown table), 413 `bundle_too_large`. Access follows the analysis rules: whoever knows an analysis
id can read it, so a brief holds exactly the ids it is given, never a listing.

**Bundle entries** (fixed names; nothing in a name comes from the user):

| Entry | Content |
|---|---|
| `brief.html` | the printable brief: one file, no scripts, no external requests (`Content-Security-Policy: default-src 'none'`), inline SVG map; print CSS lays it out like the sheet (operation across the page, map and cards in two columns, one "Not assessed" list, 5-row tables), so a typical brief prints on one or two A4 pages (without scripts it cannot scale its text to one page as the web app does) |
| `brief.json` | the `Brief` |
| `provenance.json` | the provenance, the notice and watermark, and the size and SHA-256 of every other entry; tables cut at 100,000 rows are listed |
| `evidence.geojson` | every analysis' layers in one FeatureCollection (`_analysis`, `_analysis_id`, `_layer` on each feature), H3 cells as polygons, at most 50,000 features, labels in the `airbrief` member |
| `evidence.kml` | the same layers, a folder per analysis and per layer, 2D placemarks with every value in `ExtendedData` |
| `zones.ed318.geojson` | only when the brief has an airspace check or a flight audit: the aerodrome bands and zones they were judged against, as one ED-318 zone file (`to_ed318`); a zone shared by several analyses is written once and names them in `extendedProperties.airbrief.analyses` (`01_airspace`, …); the notice and watermark in the `airbrief` member. Listed and hashed in `provenance.json` like every entry, so it is signed with them |
| `analyses/NN_<kind>.json` | each analysis envelope (parameters and full result) |
| `tables/NN_<kind>_<table>.csv` | each CSV table of each analysis |

Caps: 12 analyses, 100,000 rows per CSV table, 200 MB uncompressed. Entries carry the latest
analysis finish as their time, so two bundles of the same request differ only in `generated_utc`.

**Analysis exports, every kind** (the API's; in the static demo the JSON and GeoJSON are built in
the browser with the same shape, and KML and CSV need the API):

- **JSON** — the envelope, with `notice` ("Evidence, not authorisation…") and `watermark`.
- **GeoJSON** — every result layer plus the H3 results as polygon layers (`grid` of airspace;
  `density` and `hotspots` of ground risk, square cells for the operational volume; `cells` of eVTOL
  siting; `noise` of the eVTOL corridor), `_layer` on each feature, at most 50,000 features with the
  cut in `airbrief.truncated_layers` (the large grids are cut first), labels in `airbrief`.
- **KML** — the same layers as 2D evidence placemarks (heights stay attributes), styled zones and
  conflicts. The survey coverage pattern (`lines`) is a visual aid and is **never** written to KML.
  The dataset KML export uses the same writer (`airbrief_core/io/kml_write.py`), which drops the
  characters XML 1.0 cannot hold.
- **CSV** — UTF-8 with BOM, formula-like text neutralised, a last `notice` column on every row
  (`SYNTHETIC DEMO DATA — not evidence. Evidence, not authorisation…` when synthetic). Tables
  (`?table=`; the first is the default; multi-table files name each row's table in a leading
  `table` column):

| Kind | Tables |
|---|---|
| airspace | `all` (zones, obstacles, grid cells, profile, aerodromes, below-minimum and above-ceiling stretches), `zones`, `obstacles`, `grid`, `profile`, `aerodromes`, `height_segments` |
| ground_risk (operational volume) | `all`, `volume`, `terms`, `grid`, `assemblies`, `hotspots` |
| ground_risk (earlier footprint method) | `cells`, `hotspots` |
| flight_audit | `all` (profile, exceedances, training-area exits, distance-from-take-off segments, zones), `profile`, `exceedances`, `area_exits`, `distance`, `zones` |
| line_of_sight | `all`, `observers`, `radio_paths`, `radio_profile`, `route_hidden` |
| environment | `emi`, `daylight`, `sun` |
| evtol_siting | `candidates`, `cells` |
| evtol_corridor | `profile`, `obstacles`, `zones`, `noise`, `height_segments` |
| show | `events` (conflicts, violations, pad conflicts), `drones` |
| survey | `blocks` |

The results drawer's **Export** menu offers every format and table from tablet width (768 px) up.

## Static demo

The demo export (`python -m seed.demo_export`) writes one brief per demo analysis
(`demo/briefs/{id}.json`, the API's own answer). The demo page merges them in the browser exactly as
the API combines analyses (same sections, parts, cross-checks and provenance; checked against the
API), builds the operation section from the form, and prints the same one-page sheet. Only the
brief and operation hashes are left out ("computed by the AirBrief API"), and the bundle needs the
API, which the button says.

## Limits and caveats

- A brief is evidence for planning. It does not approve, permit or clear a flight, and it does not
  replace the authority's own systems (Digital Sky in India).
- The cross-checks compare bounding boxes, dates and the aircraft's numbers only; they cannot tell
  whether two analyses really describe one operation.
- Analyses expire with their datasets (72 h on public instances): download the bundle to keep the
  evidence. The bundle is self-contained.
- Tables on the sheet and in `brief.html` show at most 5 rows (8 in `brief.json`); the bundle's
  CSV tables hold them all.
- Coordinates and units in the brief's texts use the web app's formatting (narrow no-break space
  before a unit or hemisphere), so the static demo's merged brief reads exactly like the API's.
- The map is an equirectangular overview at site scale, not a navigation chart.

## Manual acceptance check

With the synthetic demo pack (`python -m seed.datapack --synthetic --name demo`) and the API running:

1. Open the sample **Bengaluru survey**, run **Airspace** (height 60 m) and **Ground risk** with
   the defaults.
2. Dataset header → **Export** → **Create brief**. Both analyses are ticked. Enter operator "Example
   Aerial Surveys", reference "OPS-1", aircraft "Mapping quadcopter", pilot 77.491, 12.905, and
   select **Create brief**.
3. Expect: title "Operation evidence brief — OPS-1"; the synthetic banner and watermark; a warning
   that the ground-risk analysis assumed 1 m and 15 m/s while the aircraft is 0.6 m and 18 m/s;
   cards Airspace (check result Caution: 3 obstacles would leave less than 30 m vertical
   clearance; free-flight ceiling 120 m; nearest aerodrome HAL Airport (VOBG) 19.3 km at 075°),
   Heights and terrain (highest terrain 919 m, relief 2 m, planned top 979 m AMSL), Obstacles
   (3 found, 3 clearance conflicts, tallest 65 m), Ground risk (iGRC 5, density 4,789 people/km²,
   8,598 people, footprint 1.76 km²);
   Environment, Line of sight, Survey and spray effort, Flight audit, Drone show and eVTOL listed as
   "Not assessed"; in the provenance, the input file SHA-256 of `bengaluru_survey.kml`
   (`c3baa8beb603164f41284d67c60a702492d64f7cf7b19026832a95e155f65987`) for both analyses.
4. **Print / Save as PDF**: one A4 page (the toolbar says the text scale).
5. **Download bundle (.zip)**: open `brief.html` offline; `provenance.json` lists the SHA-256 of
   every other entry; `evidence.kml` opens in Google Earth with folders "01 Airspace & terrain" and
   "02 Ground risk (SORA-style)".
6. `POST /api/briefs/` with the same ids and operation twice: the same `brief_sha256`.

## Tests

- `tests/core/test_brief.py` — canonical JSON and hashes, metric formatting, every section present
  and "not assessed", each module's B1 facts (flight-audit exits and distance, show box, audience
  rule, pads, endurance, origin, ground-risk volume, environment, line of sight, height reference),
  cross-checks (grouped areas, duplicates once), synthetic watermark rules, self-contained HTML
  with every user text (operator, reference, notes, feature names) escaped.
- `tests/core/test_kml_write.py` — the shared KML writer round-trips through the hardened reader;
  markup in names and values stays text; XML-illegal characters are dropped.
- `tests/api/test_briefs.py` — the API brief, identical hashes for identical inputs (any order),
  errors, bundle entry names and caps, KML / GeoJSON / every CSV table for all nine kinds, formula
  text neutralised in every table, H3 polygons equal to `h3.cell_to_boundary`, the GeoJSON / KML
  feature cap with the cut recorded, and a clear 422 for a kind without a CSV table.
- `tests/api/test_demo_export.py` — the demo writes one brief per analysis.
- `frontend/e2e/brief.spec.ts` — the brief from the dataset header with sections, provenance (the
  same SHA-256 as the API), bundle download and a one-page A4 `page.pdf()`; the palette entry and the
  report's Brief tab and exports; the drawer's export menu at tablet width.

## Linking a flown flight back to its brief

Under the printed preview the brief's SHA-256 is shown with a copy button. Paste it into FlightMD's upload form (planning brief) and FlightMD keeps it in the report as `metadata.planning_brief_sha256` (schema 1.7). Audit that report in AirBrief and the flight audit says which brief the flight was planned with, with a link when the brief was built in the same browser (the API stores no briefs, so this browser's list, `airbrief-briefs` in localStorage, is the only record). The brief of the flight audit itself shows the hash as a fact.
