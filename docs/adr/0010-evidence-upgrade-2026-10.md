# ADR-0010: The 2026-10 evidence upgrade: UI/UX audit, code-review fixes and seven features

- **Status:** Accepted
- **Date:** 2026-10-18
- **Milestones:** 11 (audit and review fixes), 12 (evidence upgrade, part 1), 13 (evidence upgrade, part 2)
- **Extends:** ADR-0005 (evidence, not decisions), ADR-0008 (security model), ADR-0009 (first run)

## Context

After the first full run (ADR-0009) three things ran against the working app between 2026-10-11 and
2026-10-18:
- a **UI/UX audit**: the live app was driven in a browser across six areas (public pages, the
  workspace, the analysis modules, the advanced modules, responsive layout and accessibility,
  cross-screen consistency), and every finding was re-checked before it counted;
- a **full code review** of the Python core, the API, the seed scripts, the frontend and the video
  pipeline;
- a **gap analysis and roadmap**: what operators of drones and eVTOL need before a flight that
  AirBrief did not yet give, ranked into eight features, a list of quick wins and an explicit
  "excluded" list.

The work then ran in three steps. The first fixed every confirmed audit finding. The second built
five features (plan without a file, environment, line of sight, SORA 2.5 ground risk, tracks) and
the third two more (terrain-true heights with survey upgrades; the evidence brief and complete
exports). Every feature was reviewed and the review's findings were fixed. Each feature has its own
user document in `docs/features/`.

## Decision

1. **Fix every confirmed UI/UX finding and every confirmed code-review finding**, each with a
   regression test where one can be written.
2. **Add two modules and extend five**, all as evidence with stated methods and sources:
   Environment and Line of sight are new; Airspace, Ground risk, Survey, Shows and Flight audit
   gain new inputs and outputs. The rail now has nine modules.
3. **Keep the scope boundaries** of ADR-0005: evidence only, no waypoints, no log parsing, no
   final regulatory classes, no go/no-go.
4. **Defer** the zone engine v2, per-feature batch evidence and the other items listed under
   "Deferred" below to later releases.

## UI/UX audit: 116 findings fixed

The audit confirmed **116 findings: 3 critical, 18 high, 43 medium and 52 low**. They were sorted by
the files they touch into six areas (shell, map, shared UI, pages, two groups of modules), fixed
area by area, and each area's fixes were reviewed. The browser checks are in `frontend/e2e/`
(`shell`, `map-input`, `shared-ui`, `pages`, `module-forms`, `evtol-shows-audit`). Examples:
- **Critical:** most Settings controls stayed disabled for good when `/settings` was opened
  directly or reloaded (the server-rendered `disabled` attribute survived hydration); survey
  battery blocks were numbered from 2 and the battery count disagreed with the blocks.
- **High:** Shift/Ctrl+click never added to the map selection although the panel said to use it;
  map overlays (legend, coordinate readout, controls) collided at 1024 px and at 1280 × 720 with
  the drawer open; closing a dialog or sheet dropped keyboard focus to `<body>`; number inputs read
  a thousands comma as a decimal point; wide tables could not be reached by keyboard; imperial mode
  mixed units between forms and results; switching modules dropped a running analysis; a flight
  audit limit above 120 m was capped silently while the legend still showed it.

## Code-review fixes

The review raised 14 findings; each was verified before it was changed, and the backend ones have
regression tests in `tests/core/test_review_regressions.py` and `tests/api/test_jobs_and_exports.py`.

**Jobs**
- **Job heartbeats.** Starting a worker (or the API) failed every `RUNNING` job, including the live
  jobs of other processes. Datasets and analyses now have a `heartbeat_at` column (Alembic
  migration `0002_job_heartbeat`). A runner refreshes it every 10 s on the jobs it runs; only
  `PROCESSING`/`RUNNING` rows whose heartbeat is missing or older than 60 s are failed as
  `interrupted`, at start-up and periodically. Shutting down records running analyses as
  interrupted, not as cancelled by the user.
- A data pack that disappeared between queueing and running is no longer recorded as used; the
  result says the aerodrome zones were not checked.

**Inputs and geometry**
- **NaN coordinates.** JSON parsers and `float()` accept `NaN` and infinity. A feature with a
  non-finite x or y is now unsupported (issue `invalid_coordinates`); non-finite altitudes are
  dropped and the feature is still measured (issue `invalid_altitudes`).
- **Show altitudes above sea level.** Drone-show tracks stored with `absolute` altitudes were
  measured from sea level. They are now measured from the take-off pad (origin = the mean first
  position at the lowest first altitude); a show mixing altitude modes is refused
  (`mixed_altitude_modes`).
- The nearest aerodrome to a long track is no longer missed by the candidate pre-selection, and zone
  polygons are parsed once per run instead of on every call.

**Memory and queries**
- **Show pair-memory cap.** A degenerate show (drones stacked on one point, or coordinates not in
  metres) could build millions of conflict rows. Close drone-pair frames are now counted before
  they are built; more than 500,000 (`MAX_CLOSE_PAIRS`) refuses the show with `too_many_conflicts`.
  Pad pairs stop at 100,000 and the pad list says it is incomplete.
- The measurement `status` filter is counted in SQL instead of by loading every feature.

**Correctness and exports**
- **SORA 2.5 Table 2.** The fourth column was 20 m / 150 m/s; JARUS SORA 2.5 Main Body, Table 2
  says **20 m / 120 m/s**. Fixed, and all 35 cells are tested.
- **KML and XML-illegal characters.** Legacy DBF text often holds control characters that XML 1.0
  cannot hold, which produced an unreadable KML. The dataset export and the shared KML writer
  (`airbrief_core/io/kml_write.py`) drop C0 controls other than tab, LF and CR, lone surrogates and
  U+FFFE/U+FFFF.
- The FlightMD link stored with a flight audit (`flightmd_url`) must be an absolute http(s) URL with
  a host, without credentials or spaces, so it can never render as a `javascript:` link.

**Frontend and seed scripts**
- **Deletes that never reached the server.** A dataset deleted with an undo window was lost when
  the tab closed first. The deletion is now recorded with its due time, sent on `pagehide` as a
  keepalive request and again on the next load (`lib/pending-deletes.ts`).
- The report page's own CSV writer was replaced by the shared `lib/csv.ts`, so one
  formula-neutralising rule applies everywhere.
- `seed.datapack` moves the old pack aside before renaming the new one in, so a file locked on
  Windows can no longer leave a half-deleted pack.

Fixes made while bringing the features together: survey coverage lines are left out of **every**
export (GeoJSON, KML, the bundle and the browser-built GeoJSON of the static demo), not only KML; a
GSD range is shown only when the two values differ by at least 0.005 cm/px; a timed track is never
offered as its own training area.

## New and changed modules

Each module's method, parameters, outputs, limits and known answers are in its feature document
and summarised in `docs/SPEC.md` §3.

| Area | Method | Sources | Reference |
|---|---|---|---|
| **Plan without a file** | Pin (a 64-gon inscribed in the radius circle, vertices offset with the WGS84 radii of curvature), line, area, typed coordinates (decimal, hemisphere, DMS; one parser in Python and TypeScript), pasted GeoJSON/WKT, device location; "Go to" over the packs' aerodromes; context layers | WGS84 (a = 6,378,137 m, f = 1/298.257223563); Chamberlain & Duquette (2007) for the spherical-excess previews | `docs/features/plan.md` |
| **Environment** (new) | Sun position and events, daylight profiles, magnetic field, power-law wind at height, interference proximity | NOAA GML solar calculator (after Meeus, 1991); WMM2025 (NOAA NCEI / BGS) and its technical report; 14 CFR 107.29; Peterson & Hennessey (1978), Hsu et al. (1994), Masters (2004, after Davenport, 1960) | `docs/features/environment.md` |
| **Line of sight** (new) | Radial-sweep viewshed with bilinear terrain and the curvature/refraction drop d²/(2kR); first Fresnel zone r1 = 17.32·√(d1·d2/(f·D)); free-space path loss; VLOS distance guidance | Franklin & Ray (1994); GDAL `gdal_viewshed` coefficient; ITU-R P.453/P.530 (k = 4/3), ITU-R P.525; EASA guidelines for UAS operations, Issue 03 (17 July 2025); UK CAA CAP 722 | `docs/features/los.md` |
| **Ground risk** | SORA 2.5 operational volume (FG, CV, GRB), the highest density on a Table 4 grid, Table 2 cell, adjacent area, assemblies | JARUS SORA 2.5 Main Body §2.2, §4.2 (Tables 2–4), §4.8, Annex E §E.4; the term structure of the German LBA operational-volume method | `docs/features/groundrisk.md` |
| **Airspace** | Height reference (AGL, above take-off, AMSL) with the height above ground along the flight; "not assessed" without data-pack coverage; interim DGCA wording of the default rings | ICAO Annex 2, Chapter 1 (altitude and height) | `docs/features/heights.md`, `docs/features/groundrisk.md` |
| **Survey** | GSD range and worst-case overlap over the relief, corridor mapping, battery reserve and transit legs, GCP and storage estimates, thermal and multispectral presets, spraying effort with breakpoint resume and local area units | Wolf, Dewitt & Wilkinson, *Elements of Photogrammetry*; ASABE EP496 (field capacity); the sprayer calibration formula; manufacturer spec sheets; Pix4D GCP guidance (rule of thumb) | `docs/features/heights.md` |
| **Tracks, flight audit and shows** | GPX ingest, CSV point lists with extra columns, zips of per-drone CSVs; flight audit from a timed position track with training-area exits and distance from take-off; derived show box, 1:1 or ballistic audience distance with a safety factor, pad spacing, endurance, low-height motion | Linear crossing rule shared with the height stretches; drag-free fall-out v·√(2h/g) | `docs/features/tracks.md` |
| **Evidence brief and exports** | One-page A4 brief of up to 12 analyses with cross-checks and SHA-256 provenance of canonical JSON; zip bundle; JSON, GeoJSON (H3 as polygons), KML and multi-table CSV exports for every kind | — | `docs/features/brief.md` |

## Final review round (2026-10-19)

A last pass over all new code, after the features were built, found and fixed:
- **Backend (22):** line of sight on mixed 2D/3D datasets; shows refusing tracks without heights (no silent 2D pass); CSV `elevation`/`ele` read above sea level; environment dates before 1970 and the 1901–2099 range; antimeridian-crossing areas (sun, take-off, assumed pilot); airspace partly outside the pack is never a pass; AMSL plans without terrain never claim a height above ground; brief wording and partial-coverage banners; hidden pack-swap folders ignored by the registry; survey size caps (5,000 blocks, 20,000 corridor line pieces) and cancellation; worker `--once` heartbeats.
- **Security (6):** three super-linear regular expressions (coordinate parser, CSV header units, KML coordinates) that could freeze the API process; nested WKT that could crash GEOS (nesting capped at 32); one GPX warning per track instead of per segment; a configurable per-IP limit for `GET /api/context/` and `/api/places/` (`CONTEXT_RATE_LIMIT_PER_HOUR`, on in production).
- **Interface:** Enter on a Go-to place in Ctrl+K (async results were never highlighted); local times written `UTC+05:30` everywhere; the basemap falls back to the plain background only when the style itself fails, not on a transient tile, sprite or font error while it loads; survey coverage lines are never exported in any format (GeoJSON, KML, bundle).

## Scope boundaries kept

- **Evidence only.** Results state facts, ranges and margins. Ground risk says "SORA Step 2 / Step 8
  evidence — not a GRC, ARC or SAIL"; the environment module never says "good to fly"; line of
  sight says "geometry only — not a link budget or coverage prediction" and labels VLOS distances
  as non-regulatory guidance; every brief and export says "Evidence, not authorisation".
- **No waypoints or mission files.** Nothing generates terrain-following routes, waypoints, WPML/KMZ,
  `.plan` or Litchi files. A drawn line or area is an operation to check, not a route.
- **Survey and spray lines are never exported**, in any format.
- **No log parsing.** The flight audit reads FlightMD reports and plain position files (GPX, KML,
  CSV); flight-controller logs stay FlightMD's scope.
- **Offline and keyless.** No online geocoder, forecast, NOTAM feed or link-budget service. The
  WMM2025 coefficients ship with the code (public domain); power lines and assembly venues come
  from the data pack.

## Security and privacy

- **`Permissions-Policy: geolocation=(self)`.** The app's own header blocked the browser's location
  API (`geolocation=()`), so "My location" could never work. `frontend/next.config.mjs` now allows
  it for the site itself only; camera, microphone and payment stay off. The position stays in the
  browser until the user saves the operation, which uploads it to this AirBrief server only. A
  reverse proxy that sets its own `Permissions-Policy` must keep `geolocation=(self)`.
- **New read endpoints are bounded and rate-limited.** `GET /api/context/` (a view of at most
  2° × 2°, 5,000 features and 100,000 vertices per layer) and `GET /api/places/` (a 200-character
  query, at most 25 results) share their own per-IP limit, `CONTEXT_RATE_LIMIT_PER_HOUR`
  (unlimited in development, 3,600 an hour in production when unset), separate from the POST limit.
- **New POST endpoints** (`/api/geometry/parse/`, `/api/briefs/`, `/api/briefs/bundle/`) go through
  the same per-IP rate limit and body-size limit as every POST. A brief holds at most 12 analyses;
  a bundle at most 100,000 rows per CSV table and 200 MB uncompressed (413 `bundle_too_large`).
  A brief contains exactly the analysis ids it is given; it never lists anything.

## Deferred

These stay in scope but come later:
- **Zone engine v2:** DGCA perimeter-band evidence measured from the aerodrome perimeter, uploaded
  layers as zones with time windows and vertical limits, ED-269/ED-318 import and export, and a
  data-pack builder for any region (`--bbox`, `--iso3`).
- **Per-feature batch evidence** for plots, towers and sites.
- **Emergency and contingency landing-site candidates.**
- **An offline vector basemap.**
- **FAA UAS Facility Map and Digital Obstacle File** as opt-in US data packs.
- **An EGM96 geoid grid** for AMSL plans (today every AMSL plan carries
  `vertical_reference_approximated`).
- **A JavaScript unit-test runner** for the parity of the TypeScript and Python coordinate parsers
  (today Playwright and the Python tests cover it).
- **The terrain-model caveat chip in every module** (today airspace, survey and eVTOL show it).

Excluded, as before: authorisation requests (Digital Sky, LAANC), live NOTAM or traffic feeds,
waypoint missions, go/no-go verdicts and final SORA classes, flight-log parsing, uploading geofences
to aircraft, cellular coverage prediction and any AI scoring.

## Alternatives considered

| Option | Why not |
|---|---|
| **Compute the final GRC, ARC and SAIL** | Mitigations and operational context belong to the operator, the authority or UAOP's compliance engine (ADR-0005). The operational volume and Table 2 cell are the evidence those steps need. |
| **A forecast or link-budget service** (Open-Meteo, NTIA ITM) | Online, keyed or licensed in ways that break the ₹0, offline and permissive-licence rules. Public formulas are reimplemented instead, and labelled as estimates. |
| **An online geocoder** (Nominatim) for "Go to" | Online, rate-limited and a privacy leak of where users plan to fly. The packs' aerodromes and typed coordinates cover the need offline. |
| **Export survey lines as a route** | It would be mission generation, which belongs to UAOP, and a visual aid would be mistaken for a flight plan. |

## Consequences

- **Data packs need regenerating** (`python -m seed.datapack --synthetic --name demo`) to gain the
  `power` and `assemblies` layers and the aerodromes' IATA codes; packs without them still work,
  with issues saying what was not assessed. The static demo needs
  `python -m seed.demo_export --out frontend/public/demo --datapack-dir data/datapacks` to gain
  `demo/context.json` and `demo/briefs/{id}.json`.
- **Databases need migration `0002_job_heartbeat`**; it runs on API start (`AUTO_MIGRATE=true`).
- **Stored analyses keep their meaning.** The height reference defaults to AGL and an AGL analysis
  returns the same numbers as before; a ground-risk request without `volume` runs the earlier
  footprint method; results without the new fields still render.
- **The protobuf contract grew additively** (`OperationalVolume`, `EnvironmentEvidence`,
  `LineOfSightEvidence`, the new altitude references, `brief_sha256` on the assessment event).
- **Tests:** 1,227 Python tests (65 more need the C++ kernel), ruff and mypy clean on 2026-10-18;
  15 Playwright spec files (`smoke`, `workflows`, six for the UI/UX audit and one per feature).
- **Open edges:** `buf lint` was not run on the proto additions (the CLI was not installed); the
  frontend has no JavaScript unit-test runner; the map re-renders on every frame while drawing.
