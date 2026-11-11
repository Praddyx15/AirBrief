# AirBrief — Runbook (run · test · check)

This guide walks you through running AirBrief on a machine of your choice, running its tests, and checking by hand that every feature works.

> **Where to run it:** a laptop, a home server or a cloud VM.
>
> **How it runs:** natively, with Python 3.11+ and Node 20. No Docker, no database server and no API keys are needed. SQLite is the default database. (Prefer containers? See `DOCKER.md`.)
>

---

## 0. At a glance

| I want to… | Go to |
|---|---|
| Start everything natively (recommended) | §2 Quick start |
| Use PostgreSQL, the standalone worker, the C++ kernel, or Docker | §3 |
| Run the automated tests | §4 |
| Regenerate the demo data pack and static demo after an upgrade | §2.1 |
| Build a data pack for another area, or seal an edited one | §2.2 |
| Check the product by hand, feature by feature | §5 Acceptance checklist (the 2026-10 features: §5.6; zones, one result per feature and the assessment choices: §5.7) |
| Check numbers against known answers | §6 |
| Fix a problem | §7 Troubleshooting |
| Render the demo video | §9 |
| Deploy for others to use | `DEPLOYMENT.md` |

**Default addresses once running:**

| What | URL / location |
|---|---|
| Web app | http://localhost:3000 |
| Workspace for a dataset | http://localhost:3000/w/view?d=`<dataset-id>` |
| API docs (Swagger) | http://localhost:8000/docs (development only; loads Swagger UI from cdn.jsdelivr.net) |
| API health | http://localhost:8000/health |
| Database | SQLite file `./data/airbrief.db` (created on first start) |
| Uploads / data packs | `./data/uploads/`, `./data/datapacks/` |

---

## 1. Prerequisites **[M1]**

| Tool | Minimum | Check with | Good output looks like |
|---|---|---|---|
| Python | 3.11 | `python --version` (or `python3 --version`) | `Python 3.11.x` or `3.12.x` |
| Node.js | 20 LTS | `node --version` | `v20.x` |
| npm | 10 | `npm --version` | `10.x` |
| Git | any | `git --version` | `git version 2.x` |
| make (optional) | any | `make --version` | Windows: use Git Bash or WSL, or run the raw commands shown next to each `make` target |
| C++ toolchain (optional, only for the native kernel) | GCC 11 / Clang 14 / MSVC 2022, CMake 3.26 | `cmake --version` | `cmake version 3.26` or newer |
| PostgreSQL (optional) | 16 | `psql --version` | Only if you want Postgres instead of SQLite; PostGIS is **not** needed |

### Resources

- **RAM:** 4 GB free.
- **Disk:**
  - about 1 GB for the Python virtual environment and `node_modules`
  - about 30 MB for the synthetic demo data pack
  - about 1.5 GB of downloads for the real Bengaluru data pack (§2, step 6); the kept clip is smaller
  - Tier 0 (upload and measure) works without any data pack

All software and data are free. **No API keys are needed.**

---

## 2. Quick start (native) **[M1 · samples M2 · frontend M3 · data packs M4]**

```bash
# 1. Get the code
git clone https://github.com/<you>/AirBrief.git
cd AirBrief

# 2. Python environment + packages (core, API, dev tools)
python -m venv .venv
source .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[api,dev]"            # add ,flightmd to also install flightmd-core

# 3. Local config (defaults work as-is: SQLite, ./data, in-process jobs)
cp .env.example .env                   # Windows PowerShell: Copy-Item .env.example .env

# 4. Sample files (KML, shapefile zips, show tracks, FlightMD reports) into samples/
python -m seed.samples --dest samples/

# 5. Offline synthetic data pack "demo" (seconds, no internet)
python -m seed.datapack --synthetic --name demo

# 6. (Optional) the real Bengaluru data pack: ~50 km clip of DEM, population, OSM, aerodromes
python -m seed.datapack --region bengaluru

# 7. Start the API (terminal 1). Migrations run automatically on start (AUTO_MIGRATE=true).
uvicorn api.main:app --reload

# 8. Start the frontend (terminal 2), on this machine only like the API
cd frontend
npm install
npm run dev -- -H 127.0.0.1
```

Both servers listen on `127.0.0.1` only, so nobody else on your network can reach them. Open the app at `http://localhost:3000` as usual. For the production build, use `npm run build` then `npm start -- -H 127.0.0.1`: the `start` script itself has no host (Docker and the systemd unit in `DEPLOYMENT.md` pass their own `--hostname`), and without one `next start` listens on every interface.

`make install`, `make samples`, `make datapack-demo`, `make datapack-real`, `make api` and `make web` do the same; `make dev` runs the API and the frontend together.

**What to expect at each step:**

| Step | Expected |
|---|---|
| 2 | Ends with `Successfully installed airbrief-core-0.1.0 …` |
| 4 | Lists each file written to `samples/` (the KML, shapefile zip, show-track, FlightMD and attack samples used in §5) |
| 5 | Ends by confirming the `demo` pack (marked synthetic) is ready in `data/datapacks/demo/` |
| 6 | A progress line per layer (aerodromes, DEM, population, OSM), then confirms the `bengaluru` pack is ready |
| 7 | `Running upgrade … -> <rev>` (first start only), then `Uvicorn running on http://127.0.0.1:8000` |
| 8 | `Ready` and `Local: http://127.0.0.1:3000` (http://localhost:3000 works too) |

You can also apply migrations by hand at any time with `alembic upgrade head` (`make migrate`).

**You should now see:**

1. `http://localhost:8000/health` →

   ```json
   {"status":"ok","version":"0.1.0","schema_version":"1.0","db":"ok","datapacks":["demo"],"native_backend":"python"}
   ```

   `datapacks` also lists `"bengaluru"` if you ran step 6, and is `[]` if you skipped steps 5–6. `native_backend` is `"cpp"` once the optional kernel is installed (§3.3).
2. `http://localhost:8000/docs` → the Swagger UI, with sections *files*, *samples*, *analyses*, *briefs*, *context*, *flightmd*, *meta*, *health*.
3. `http://localhost:3000` → the AirBrief landing page. **Open workspace** leads to `/w`, the upload screen with sample cards. Opening a dataset goes to `/w/view?d=<id>`. **Check a location** opens `/w/view?m=airspace&plan=pin` (plan without a file).

**Stop and reset:**

```bash
# Stop: Ctrl-C in each terminal.
# Full reset (deletes the database, uploads and data packs):
rm -rf data/                           # Windows PowerShell: Remove-Item -Recurse -Force data
```

### 2.1 After an upgrade: regenerate the data pack and the static demo **[M12–M13]**

The 2026-10 upgrade added data-pack layers (`power.geojson`, `assemblies.geojson`, the aerodromes' `iata_code`) and static-demo files (`demo/context.json`, `demo/briefs/{id}.json`). A pack built before it still works, but the environment module reports `power_not_assessed`, ground risk `assemblies_layer_missing`, and "Go to" does not find IATA codes. Rebuild both (stop the API first on Windows, so no file in the pack is locked):

```bash
python -m seed.datapack --synthetic --name demo
python -m seed.demo_export --out frontend/public/demo --datapack-dir data/datapacks
```

| Command | Expected |
|---|---|
| `seed.datapack` | Confirms the `demo` pack (synthetic) in `data/datapacks/demo/`; `manifest.json` lists `assemblies` and `power` among the layers. The old pack is moved aside and replaced only when the new one is complete |
| `seed.demo_export` | A summary of the datasets and analyses written (including the Training flight GPX, line of sight and environment), then `frontend/public/demo/` holds `context.json` and a `briefs/` folder. A failed run leaves the previous export as it was |

The database needs migration `0002_job_heartbeat` (job heartbeats); it runs on the next API start (`AUTO_MIGRATE=true`) or with `alembic upgrade head`.

The security fixes of 2026-10-19 add two more, `0003_analysis_dataset_owner` and `0004_analysis_datapack_sha256`, applied the same way. Packs built before them carry no file hashes: Settings shows them as "No hashes to check" and every analysis that reads one gets the `datapack_unverified` warning until you rebuild it with the command above.

Zone engine v2 (2026-10-23) adds the layers `aerodrome_perimeters.geojson` and `runways.geojson` and the `scheduled_service` column of `aerodromes.csv`. On an older pack the DGCA bands are measured from the reference point, with the warning `perimeter_approximated`, and the demo's pre-run audits and eVTOL runs show "conservative rules" in History. Rebuild both as above. No new migration.

### 2.2 A data pack for another area, and sealing an edited pack **[M15]**

```bash
# any area up to 2° x 2°; --name is required; --iso3 picks the country's WorldPop file
python -m seed.datapack --bbox 85.1,27.5,85.6,27.9 --name kathmandu --iso3 NPL
python -m seed.datapack --bbox 85.1,27.5,85.6,27.9 --name kathmandu-lite --iso3 NPL --lite   # GLO-90 DEM
# after editing a pack's files (for example restricted areas added to zones.geojson)
python -m seed.datapack --seal data/datapacks/kathmandu
# when Overpass was busy and a layer FAILED: fetch only the missing ones
python -m seed.datapack --refetch data/datapacks/kathmandu
```

| Command | Expected |
|---|---|
| `--bbox` with `--iso3` | Ends with `Data pack 'kathmandu' ready: dem ✓ population ✓ osm ✓ aerodromes ✓` and its location; `manifest.json` lists `aerodrome_perimeters` and `runways` among the layers |
| `--bbox` without `--iso3` | The same, but `[population] skipped: no --iso3 given…` and `population –`; exit status 0. The pack has no population layer, so ground risk there needs another pack |
| `--bbox 84,26,86.5,28.5 --name x` | Refused: "the bbox is 2.50 x 2.50 degrees; one pack covers at most 2 x 2 degrees …". Split the area |
| `--bbox …` without `--name`, `--iso3` without `--bbox`, `--iso3 NEPAL` | Refused by the argument parser with a one-line reason |
| `--seal <pack>` | `Data pack '<name>' sealed: content SHA-256 …`; the API uses the pack again without a restart (§5.5 S20) |
| `--refetch <pack>` | `[pois] ...`, `[pois] done (N features)` for each OpenStreetMap layer the build missed, then `sealed: content SHA-256 …`; with nothing missing: "has every OpenStreetMap layer". `--layers pois,water` names them; `--layers dem` is refused (OpenStreetMap layers only) |

`--region bengaluru` stays a named preset. Downloads need the internet; the synthetic pack does not.

---

## 3. Other ways to run **[M2 · frontend M3]**

### 3.1 PostgreSQL instead of SQLite

```bash
pip install -e ".[api,postgres]"
createdb airbrief                     # any PostgreSQL 16; PostGIS is not needed
# in .env:
#   DATABASE_URL=postgresql+psycopg://<user>:<password>@localhost:5432/airbrief
alembic upgrade head                   # or just start the API (AUTO_MIGRATE=true)
uvicorn api.main:app --reload
```

### 3.2 Standalone worker (optional)

By default the API runs jobs on its own thread pool (`JOB_MODE=inprocess`). To process jobs in a separate process instead:

```bash
# in .env: JOB_MODE=external
uvicorn api.main:app --reload          # terminal 1: queues jobs only
python -m api.worker                   # terminal 2: claims and runs them (make worker)
```

### 3.3 Optional C++ kernel **[M6]**

```bash
pip install ./native                   # needs a C++17 compiler; pip brings CMake (make install-native)
python -c "import airbrief_core._native as n; print(n.BACKEND)"   # → "cpp" (else "python")
```

### 3.4 Docker

Docker is optional and not part of the repository. `DOCKER.md` has copy-paste `Dockerfile.api`, `Dockerfile.web`, `docker-compose.yml` and `.dockerignore`, a one-step script that writes them, and the matching commands.

---

## 4. Automated tests **[M1 onward]**

The full guides, with what to install and what a good run prints, are in [`testing/`](testing/README.md): PostgreSQL without Docker, the C++ kernel, the real data pack and network tests, end to end, CI and the security scanners.

| Command | Raw equivalent | What it covers | Runs in CI job |
|---|---|---|---|
| `make lint` | `ruff check .` | Style (rules E, F, W, B) | `python` |
| `make typecheck` | `mypy airbrief_core api seed` | Types | `python` |
| `make test` | `pytest -m "not native and not network" --cov=airbrief_core --cov=api` | Core geometry, CRS, ingest attacks, analyses, FlightMD adapter and position tracks, the brief, exports, API (temporary SQLite; no server needed). 1,760 tests (70 more deselected: 64 native, 6 network) on 2026-11-09; the API tests also on PostgreSQL 16 ([`testing/postgres.md`](testing/postgres.md)) | `python` (SQLite and PostgreSQL 16) |
| `make test-native` | `pytest -m native` | C++ ↔ Python parity (skipped if the kernel isn't built) | `native` |
| `make test-network` | `pytest -m network --run-network` | OurAirports and the national zone files of Switzerland, Spain, Ireland, Luxembourg and the German DFS sample, downloaded and read ([`testing/real-data.md`](testing/real-data.md)) | never in CI |
| `make contracts` | `cd contracts/proto && buf lint` | Protobuf STANDARD lint (needs the free `buf` CLI) | `contracts` |
| `make web-check` | `cd frontend && npm run lint && npm run typecheck && npm run build` | ESLint + TypeScript + production build | `web` |
| `make e2e` | `cd frontend && PLAYWRIGHT_BROWSERS_PATH=0 npm run e2e` | 25 spec files in `frontend/e2e/`: `smoke` and `workflows` (upload → map → measurements → analysis, screenshots at 375/768/1024/1440 px), six for the UI/UX audit fixes (`shell`, `map-input`, `shared-ui`, `pages`, `module-forms`, `evtol-shows-audit`), one per 2026-10 feature (`plan`, `environment`, `line-of-sight`, `groundrisk`, `tracks`, `heights`, `brief`), one per 2026-10-19 security area (`access`, `abuse`, `fetch`, `evidence`, `deploy`), `zones`, `zones-followups`, `batch`, `choices` and `flightmd-schema17`; axe-core accessibility checks in most of them (`expectNoSeriousA11yViolations`). **Needs the API and web running** (§2 steps 7–8, with `DEFAULT_DATAPACK=demo` and a pack regenerated as in §2.1). One spec file: `npm run e2e -- e2e/environment.spec.ts`. `PLAYWRIGHT_BROWSERS_PATH=0` makes Playwright use the Chromium build that matches it, which sits inside `frontend/node_modules`; without it, it looks in the user-wide cache, where an older build may be all there is | `e2e` |

**Reading failures:**
- **pytest:** failing tests show the expected vs actual values. Geometry tests print the deviation %.
- **Playwright:** each failure saves a trace and screenshots to `frontend/test-results/`. View one with `./node_modules/.bin/playwright show-trace <file>` (from `frontend/`). The HTML report is in `frontend/playwright-report/`.
- **axe-core:** a failure lists the rule id (e.g. `color-contrast`) and the selector of the element at fault.
- **Time zones:** `environment.spec.ts` runs the browser in `Asia/Kolkata` (`test.use({ timezoneId })`), so its expected local times do not depend on your machine's zone.
- **Stale data pack:** failures in `environment`, `groundrisk` or `plan` that mention power lines, assemblies or IATA codes, or in `zones` that show circles instead of bands around the runways, usually mean the pack predates the upgrade: redo §2.1.

**CI:** every push runs the jobs above in GitHub Actions (`.github/workflows/ci.yml`). A green tick on the commit means the same commands passed there. The `e2e` job uploads its Playwright report and server logs as the `playwright-report` artifact.

---

## 5. Manual acceptance checklist

Tick each row on your test machine. Sample files are generated into `samples/` by §2 step 4 (they also appear as sample cards on `/w`), and expected numbers are in §6.

### 5.1 Original brief — upload & measure (UI) **[M3]**

| # | Step | Sample | Expected result | ✔ |
|---|---|---|---|---|
| T1 | `/w` → drop the file onto the dropzone | `samples/tier0/square_1km_equator.kml` | Progress shows Validating → Reading → Measuring → Done; the browser goes to `/w/view?d=<id>`; the map zooms to a square near 0°N 0°E | ☐ |
| T2 | Click the square in the map or the table | same | Inspector: area ≈ **1,000,000 m²** (§6 K1), perimeter ≈ 4,000 m, CRS `EPSG:4326`, method "local projection" (LAEA area, TM length), geodesic deviation < 0.01%; the URL gains `&f=0` | ☐ |
| T3 | Repeat with the 60°N square | `samples/tier0/square_1km_60N.kml` | Area again ≈ 1,000,000 m², which shows high latitude doesn't distort it | ☐ |
| T4 | Use **Browse…** instead of drag-and-drop | `samples/tier0/plots_shapefile.zip` | Same flow works; layer name shown; CRS `EPSG:32643` (UTM 43N). Each plot shows its **local-projection area** plus its **grid area** in the source CRS: grid 20,000 / 22,500 / 15,000 m², local-projection areas about 0.11% smaller (§6 K10) | ☐ |
| T5 | Mixed KML | `samples/tier0/mixed_features.kml` | Points show "No measurement (point)"; lines show length; polygons show area; nested KML folders appear as layers | ☐ |
| T6 | Line with altitude | `samples/tier0/climb_line_z.kml` | 2D length ≈ 1,000 m; **3D length ≈ 1,005 m** (§6 K5); `altitudeMode` shown | ☐ |
| T7 | Self-intersecting polygon | `samples/tier0/bowtie_invalid.kml` | Measured, with a **"Repaired"** badge and a tooltip explaining why | ☐ |
| T8 | Shapefile without `.prj` | `samples/tier0/no_prj.zip` | Upload succeeds with a warning: "CRS unknown — not measured"; features listed; nothing crashes | ☐ |
| T9 | Wrong file type | any `.pdf` or `.png` | Rejected before upload with a message listing the accepted types (.zip, .kml, .kmz, .geojson, .json, .gpx, .csv) | ☐ |
| T10 | Zip with a path-traversal entry | `samples/tier0/attack_zipslip.zip` | Rejected: "Unsafe archive" (HTTP 422, code `unsafe_archive`); no file written outside the upload folder | ☐ |
| T11 | Corrupt file | `samples/tier0/corrupt.kml` | Status **FAILED** with a readable reason (`corrupt_file`) and a Retry action; no 500 | ☐ |

### 5.2 Original brief — API (Swagger or curl) **[M2]**

```bash
# Upload
curl -s -F "file=@samples/tier0/square_1km_equator.kml" http://localhost:8000/api/files/
# → {"id":"<uuid>","filename":"square_1km_equator.kml","status":"COMPLETED","feature_count":1, ...}

# File info
curl -s http://localhost:8000/api/files/<uuid>/
# → {"id":"<uuid>","filename":"square_1km_equator.kml","feature_count":1,"crs":"EPSG:4326","status":"COMPLETED", ...}

# Measurements
curl -s http://localhost:8000/api/files/<uuid>/measurements/
# → {"file_id":"<uuid>","items":[{"feature_index":0,"layer":"...","name":"...","geometry_type":"Polygon","crs":"EPSG:4326",
#      "measurement":{"status":"measured","area_m2":1000000.0,"perimeter_m":4000.0,"method":"local-projection",
#                     "geodesic_area_m2":...,"deviation_pct":...}}],
#    "total":1,"limit":100,"offset":0,"totals":{...}}
```

| # | Check | Expected | ✔ |
|---|---|---|---|
| A1 | `POST /api/files/` with a valid KML | `201`, an `id`, and `status` `COMPLETED` (files above `INLINE_PROCESS_MAX_MB` return `202` `PENDING` → poll A2) | ☐ |
| A2 | `GET /api/files/{id}/` | Fields `id, filename, feature_count, crs, status` exactly as in the brief, plus extras | ☐ |
| A3 | `GET /api/files/{id}/measurements/` | One item per feature; points have `measurement.status` `not_applicable` with a `reason` | ☐ |
| A4 | `GET /api/files/{unknown-id}/` | `404`, with the error envelope `{"error":{"code":"not_found",...}}` | ☐ |
| A5 | `POST` a 60 MB file (over the limit) | `413` with code `file_too_large` | ☐ |
| A6 | `GET /api/files/{id}/features/` | A valid GeoJSON FeatureCollection (paste into geojson.io to view) | ☐ |
| A7 | `GET /api/samples/`, then `POST /api/samples/{name}/` | The sample list; the POST creates and processes a dataset (`201`) | ☐ |

### 5.3 Modules **[M4–M6]** (need a data pack from §2 step 5 or 6)

| # | Module | Steps | Expected | ✔ |
|---|---|---|---|---|
| M-A1 | Airspace | Open the sample *Bengaluru survey*; select the polygon; rail → **Airspace** → Run | Zone layer with hatch patterns + labels; nearest aerodrome named with distance; terrain-corrected ceiling shown; disclaimer "Indicative only — not an official airspace map…" | ☐ |
| M-A2 | Flyability grid | Same, toggle **Flyability grid** | Hex layer; legend with numeric breaks; **View as table** lists cells | ☐ |
| M-B1 | Ground risk | Rail → **Ground risk** → preset "Sub-250 g multirotor" → Run | Operational volume (FG, CV, GRB), the highest gridded density with its SORA 2.5 Table 2 cell, adjacent area, hotspots; labelled "SORA Step 2 / Step 8 evidence — not a GRC, ARC or SAIL" (full check: §5.6 G1–G5) | ☐ |
| M-C1 | eVTOL siting | Sample *Bengaluru eVTOL* → **eVTOL** → Run with default weights | Top-N site ranking (sorted); clicking a site shows its score breakdown | ☐ |
| M-C2 | Corridor | Pick 2 sites → **Route corridor** | Corridor polygon; length vs straight-line detour %; terrain clearance profile; noise-exposed population | ☐ |
| M-D1 | Drone show | Sample *50-drone show* → **Shows** → Validate | Exactly **1 conflict**: **D07–D32 at t = 48.0 s, 0.80 m** (§6 K8); clicking it seeks the timeline and highlights both drones | ☐ |
| M-D1b | Drone show (clean) | Sample *50-drone show (clean)* → **Shows** → Validate | **0 conflicts**; verdict pass | ☐ |
| M-D2 | Show playback | Play / pause / step / speed | Starts paused; works by keyboard (Space = play/pause, ←/→ = step) | ☐ |
| M-E1 | Survey estimator | Sample *Bengaluru survey* → **Survey** → camera "P4 RTK", altitude 100 m, overlap 80/70 | GSD **2.74 cm/px**, footprint **150 × 100 m**, line spacing **45 m**, trigger distance **20 m** (§6 K6) | ☐ |
| M-F1 | Flight audit (file) | **Flight audit** → drop `samples/flightmd/flightmd_report_v15.json` | Preview card; AGL profile with a dashed 120 m line; max height above takeoff **135 m**; one exceedance **1,157.1–1,642.9 m** along the track (§6 K7) | ☐ |
| M-F2 | Flight audit (old schema) | Drop `samples/flightmd/flightmd_report_v12.json` | Works the same; a note "FlightMD schema 1.2 detected" | ☐ |
| M-F3 | Flight audit (URL) | Set `FLIGHTMD_API_URL` in `.env` (restart the API) and the FlightMD web address in **Settings** → **Flight audit** → **Link** tab → paste a public https report link → **Audit flight** | Same as M-F1; an **Open in FlightMD** link works. Private, local or plain-http links are refused with `url_not_allowed` | ☐ |

### 5.4 UI/UX quality checks **[M3, final in M8]**

| # | Check | How | Expected | ✔ |
|---|---|---|---|---|
| U1 | Keyboard only | Unplug or ignore the mouse; use Tab / Shift-Tab / Enter / arrows | Every action is reachable; focus ring always visible; "Skip to map" link at the top | ☐ |
| U2 | Map by keyboard | Focus the map | Arrows pan, +/- zoom; **N** / **Shift+N** move focus between features and **Enter** selects; **?** opens the shortcut list | ☐ |
| U3 | Themes | Settings → Light / Dark / System | All text readable in both; no white flashes | ☐ |
| U4 | Reduced motion | OS setting "reduce motion" on | No fly-to animation (map jumps); drawers don't slide; show playback never autoplays | ☐ |
| U5 | Widths | Browser dev tools at 375, 768, 1024, 1440 px | No horizontal scrolling; inspector becomes a sheet below 1024 px; read-only viewer below 768 px | ☐ |
| U6 | Empty state | Fresh install, open `/w` | Helpful message + upload + sample cards (no blank screen) | ☐ |
| U7 | Error state | Stop the API (Ctrl-C in its terminal) and upload | Clear error with a Retry; restart the API, Retry works | ☐ |
| U8 | Colour-blind check | Dev tools → Rendering → Emulate vision deficiencies (deuteranopia) | Zones are still distinguishable through hatch + label | ☐ |
| U9 | Deep link | Select a feature and a module, copy the URL (`/w/view?d=<id>&f=<index>&m=<module>…`), open it in a new tab | Same dataset, feature and view restored | ☐ |
| U10 | Units | Settings → imperial | Values switch to ft / ft² / mi; the API is unchanged (metric) | ☐ |
| U11 | Report | From a finished analysis → **Report** | `/w/report?d=<id>&a=<analysisId>` opens a print-friendly page with JSON / GeoJSON / KML / CSV exports (every CSV table) and a **Brief** tab (§5.6 B1–B6) | ☐ |

### 5.5 Security and privacy checks

Run the API once more in production mode for these:
```bash
APP_ENV=production CORS_ORIGINS=http://localhost:3000 uvicorn api.main:app
```
On Windows PowerShell, set the two variables with `$env:APP_ENV="production"` and `$env:CORS_ORIGINS="http://localhost:3000"` first. Run the `curl` commands in Git Bash (PowerShell's `curl` is a different command). Leave `FLIGHTMD_API_URL` unset for S5. S17 needs development mode, and S18–S20 restart the API with one setting added: each row says so. Close that terminal afterwards so later runs are back in development mode. See [`SECURITY.md`](SECURITY.md) for what each control protects.

| # | Check | How | Expected | ✔ |
|---|---|---|---|---|
| S1 | No global listing | `curl -s localhost:8000/api/files/` | `403` with code `listing_disabled`; the home page still lists the files *this* browser uploaded | ☐ |
| S2 | Owner tokens | Upload in the browser, then `curl -X DELETE localhost:8000/api/files/<id>/` | `403 owner_token_required`; deleting from the browser that uploaded it works. In a private window, the Delete button is disabled | ☐ |
| S3 | Request size | `curl -s -H "Content-Type: application/json" --data-binary @big.json localhost:8000/api/analyses/` with a file over 10 MB | `413 request_too_large`, before the body is processed | ☐ |
| S4 | Unsafe archive / XML | Upload `samples/tier0/attack_zipslip.zip`; upload a KML containing `<!ENTITY` | `422 unsafe_archive`; `corrupt_file`. Nothing is written outside `data/uploads/<id>/` | ☐ |
| S5 | SSRF guard | `curl -s -H "Content-Type: application/json" -d '{"url":"https://127.0.0.1/data.json"}' localhost:8000/api/flightmd/fetch/`, then the same with `https://169.254.169.254/latest/meta-data/`, `http://example.org/data.json` and `https://api.github.com/` | The first three: `422 url_not_allowed` whose `details` name only the broken rule: `{"rule":"private_address"}` (loopback), `{"rule":"private_address"}` (cloud metadata), `{"rule":"scheme"}` (plain http). The last: `422 report_invalid`: a public address, but not a FlightMD report, so nothing is passed back. (Use curl: the UI's Link tab already refuses plain http before sending.) | ☐ |
| S6 | Headers | `curl -sI localhost:8000/api/meta/` and, with `npm run build && npm start -- -H 127.0.0.1`, `curl -sI localhost:3000/` twice | API: `nosniff`, `X-Frame-Options: DENY`, `Cache-Control: no-store`, `Strict-Transport-Security: max-age=31536000; includeSubDomains`, no `server` header if uvicorn ran with `--no-server-header`. Web: `nosniff`, `X-Frame-Options: DENY`, HSTS, `Permissions-Policy: camera=(), microphone=(), geolocation=(self), payment=()` and a `Content-Security-Policy` whose `script-src` is `'self' 'nonce-…' 'strict-dynamic'`, with a different nonce each time and no `'unsafe-inline'`. The map and all modules still work, the theme loads without a flash, there are no CSP errors in the browser console, and **My location** asks for permission instead of failing at once | ☐ |
| S7 | Retention | `curl -s localhost:8000/api/meta/` | `limits.upload_ttl_hours` is `72` | ☐ |
| S8 | No internals in errors | Trigger any failure (e.g. S4) | Messages are human-readable; no stack traces or server file paths in responses | ☐ |
| S9 | Bounded read endpoints | `curl -s "localhost:8000/api/context/?bbox=76,12,79,15"`; `curl -s "localhost:8000/api/places/?q=VOBL&limit=26"` | `422 area_too_large` ("The view is 3.00° × 3.00°; context layers cover at most 2° × 2°. Zoom in."); `422 invalid_params` (limit above 25). Both endpoints are bounded, and in production they are also rate-limited together per client (`CONTEXT_RATE_LIMIT_PER_HOUR`, 3,600 an hour by default; over it: `429 rate_limited` with `Retry-After`) | ☐ |
| S10 | Brief limits | `POST /api/briefs/` with 13 analysis ids, then with `{"analysis_ids":[]}` | `422 invalid_params` both times; a brief never lists analyses it was not given | ☐ |
| S11 | API docs pages | `curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/docs`, the same for `/redoc` and `/openapi.json`; then restart with `API_DOCS=true` added and `curl -sI localhost:8000/docs` | `404`, `404`, `200`. With `API_DOCS=true`: `200` and a `Content-Security-Policy` whose `script-src` names only `https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.33.1/swagger-ui-bundle.js` and one `'sha256-…'` hash. In the browser `/docs` works with no CSP errors in the console (on `/redoc`, the one CSP message is ReDoc's footer logo from `cdn.redoc.ly`, blocked as intended) | ☐ |
| S12 | Host allow-list | Restart with `ALLOWED_HOSTS=localhost,127.0.0.1` added; `curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/health`, then the same with `-H "Host: evil.example"` | `200`, then `400` (`Invalid host header`). Without `ALLOWED_HOSTS`, both `200` | ☐ |
| S13 | Someone else's analysis on your dataset | In the browser, load the **Bengaluru survey** sample, run **Airspace** and copy `<id>` from `d=` in the URL. Then, as a visitor without the dataset's token: `curl -s -X POST localhost:8000/api/analyses/ -H "Content-Type: application/json" -d '{"kind":"airspace","dataset_id":"<id>","params":{"include_datapack_zones":false}}'`; `curl -s "localhost:8000/api/analyses/?dataset_id=<id>"`; reload the workspace; open `/w/report?d=<id>&a=<the visitor's analysis id>&mode=brief` | `202` with `"by_dataset_owner":false`. The listing holds only your run, and History does not show the visitor's. The brief page lists the visitor's run with **Added by another visitor**, unticked, while your airspace run is ticked; ticking it by hand adds the `non_default_parameters` cross-check (data-pack zones left out). In development mode History lists the visitor's run too, with the same label | ☐ |
| S14 | Fetch errors say only what failed | `curl -s -H "Content-Type: application/json" -d '{"url":"https://10.0.0.1/report/1"}' localhost:8000/api/flightmd/fetch/`, then the same with `https://example.org:8443/report/1` and `https://no-such-host.invalid/report/1` | `422 url_not_allowed` with `"details":{"rule":"private_address"}`; `422` with `{"rule":"port"}`; `502 fetch_failed` with `{"stage":"resolve"}`. No host, address, resolver text or HTTP status in any response. The API log has the detail, e.g. `FlightMD fetch refused (POST /api/flightmd/fetch/): rule=private_address url=https://10.0.0.1/report/1 host=10.0.0.1 address=10.0.0.1` (logger `airbrief.api.fetch`) | ☐ |
| S15 | NAT64 and IPv4-compatible addresses | As S14 with `https://[64:ff9b::7f00:1]/report/1` and `https://[::127.0.0.1]/report/1` | Both `422 url_not_allowed`, `{"rule":"private_address"}`; the log line ends with `embeds=127.0.0.1` | ☐ |
| S16 | Writes from other sites | `curl -s -X POST localhost:8000/api/geometry/parse/ -H "Origin: https://evil.example" -H "Content-Type: application/json" -d '{"text":"12.97, 77.59"}'`, then the same without the `Origin` header | `403 cross_site_request`, then `200`. The API log shows `cross_site_request 403 client=<12 hex digits> path=/api/geometry/parse/` (logger `airbrief.security`) | ☐ |
| S17 | Development mode stays local | In a new terminal with the default `.env` (development): `uvicorn api.main:app`; `curl -s localhost:8000/health -H "X-Forwarded-For: 8.8.8.8"`, then with `X-Forwarded-For: 192.168.1.20` | `403 development_mode_local_only` ("…runs in development mode, which only answers clients on this machine or a private network…"), then `200`. uvicorn takes the client address from `X-Forwarded-For` for requests from 127.0.0.1, and with `TRUSTED_PROXY_HOPS=0` the API also checks the last forwarded entry. At start-up the log warns `APP_ENV=development. Protections off: …` | ☐ |
| S18 | Storage quota | Restart the production API with `STORAGE_QUOTA_MB=0.001` (about 1 kB) added; `curl -s -X POST localhost:8000/api/samples/bengaluru_survey/` twice | The second answers `507 storage_full` with `"details":{"reason":"quota"}` ("This server has no storage left for uploads right now. Try again later."); the first does too when `data/uploads/` already holds files. An upload from the web app shows the same message | ☐ |
| S19 | Signed bundle | The S19 commands below the table | `verify` prints `signed  yes, Ed25519 signature valid for key <key id>` and `result  OK`, exit status 0. For the edited copy: `FAIL    brief.json: content differs from provenance.json (SHA-256 or size)` and `result  FAILED (1 problem)`, exit status 1. Settings → Data packs and limits shows the key id and the public key, and the brief says "Bundles from this instance are signed (key …)" | ☐ |
| S20 | Tampered data pack | The S20 commands below the table | First `[('demo', 'verified')]`, and Settings shows **Files match their hashes**. After the edit, with no restart: `mismatch`; Settings shows **Files changed: not used** and the red alert "Data pack demo failed its integrity check"; the context request answers `503 datapack_integrity` and an airspace check fails with the same code; place search still answers `200` (a failed pack is skipped). The API log names the file (`zones: zones.geojson does not match its SHA-256`). `--seal` prints `Data pack 'demo' sealed: content SHA-256 …`, and the pack is `verified` again | ☐ |
| S21 | Nonce CSP in the browser | With the production web build (`npm run build && npm start -- -H 127.0.0.1`), open `/`, `/w`, a sample's workspace, its report page and Settings with the DevTools console open; switch the theme and do it again | No `Refused to execute inline script` or other Content-Security-Policy messages; the theme applies without a flash. S6 checks the header itself | ☐ |
| S22 | Upload budget and read limit | Restart the production API with `UPLOAD_MB_PER_DAY=0.0015` (1,572 bytes) and `READ_RATE_LIMIT_PER_HOUR=3` added. `curl -s -F file=@samples/tier0/square_1km_equator.kml localhost:8000/api/files/` twice, then `curl -s localhost:8000/api/samples/` four times | The first upload is accepted (`202`, queued); the second is refused with `429 upload_budget_exceeded` ("This address has uploaded its 0.0015 MB for the last 24 hours. Retry in … s.") and a `Retry-After` header. The fourth `curl` answers `429 rate_limited` ("…the limit is 3 per hour…"). Remove both settings and restart afterwards | ☐ |
| S23 | Refusal counters | After S16 and S22, on the server itself: `curl -s localhost:8000/health`; then `curl -s localhost:8000/health -H "X-Forwarded-For: 192.168.1.20"` | The first lists `"refusals"` with `cross_site_request`, `upload_budget_exceeded` and `rate_limited` counted; the second (as if through a proxy) has `"refusals": null` | ☐ |

S19 (key outside the repository folder; restart the API with it):

```bash
python -m airbrief_core.verify --new-key ../aa-check-keys/brief-signing-key.pem   # prints the key id and public key
BRIEF_SIGNING_KEY_FILE=../aa-check-keys/brief-signing-key.pem APP_ENV=production CORS_ORIGINS=http://localhost:3000 uvicorn api.main:app
# in the browser: Bengaluru survey → Airspace; copy the analysis id from a= in the URL
curl -s -X POST localhost:8000/api/briefs/bundle/ -H "Content-Type: application/json" -d '{"analysis_ids":["<analysis id>"]}' -o bundle.zip
curl -s localhost:8000/api/meta/ | python -c "import json,sys; print(json.load(sys.stdin)['signing']['public_key'])"
python -m airbrief_core.verify bundle.zip --public-key <public key>
python - <<'EOF'
import zipfile
with zipfile.ZipFile("bundle.zip") as src, zipfile.ZipFile("edited.zip", "w") as dst:
    for item in src.infolist():
        data = src.read(item)
        if item.filename == "brief.json":
            data = data.replace(b'"', b"'", 1)  # one byte changed
        dst.writestr(item, data)
EOF
python -m airbrief_core.verify edited.zip --public-key <public key>
```

S20 (a throwaway pack outside the repository folder):

```bash
python -m seed.datapack --synthetic --name demo --dest ../aa-check-packs
DATAPACK_DIR=../aa-check-packs APP_ENV=production CORS_ORIGINS=http://localhost:3000 uvicorn api.main:app
curl -s localhost:8000/api/meta/ | python -c "import json,sys; print([(p['name'], p['integrity']) for p in json.load(sys.stdin)['datapacks']])"
echo " " >> ../aa-check-packs/demo/zones.geojson                      # any edit
curl -s localhost:8000/api/meta/ | python -c "import json,sys; print([(p['name'], p['integrity']) for p in json.load(sys.stdin)['datapacks']])"
curl -s "localhost:8000/api/context/?bbox=77.5,12.9,77.7,13.1"
curl -s "localhost:8000/api/places/?q=VOBL"
python -m seed.datapack --seal ../aa-check-packs/demo
```

Delete `../aa-check-keys` and `../aa-check-packs` when you are done, or keep the key if this instance will sign for real (`DEPLOYMENT.md`).

### 5.6 The 2026-10 features **[M12–M13]**

Synthetic demo pack regenerated as in §2.1, API and web app running. Each block repeats the "Manual acceptance check" of its feature document in `docs/features/`, which also explains the method behind each number. Numbers marked † were re-checked through the running API on 2026-10-18.

**Plan without a file** ([`plan.md`](docs/features/plan.md))

| # | Step | Expected | ✔ |
|---|---|---|---|
| P1 | Landing → **Check a location**; Coordinates… → `13.1979, 77.7063` → Pin → Show on the map; 500 m; **Use this operation** | Workspace on Airspace with "Drop a pin" active; dataset `Pin 13.1979 N 77.7063 E.geojson` with 2 features; Measure: "Pin radius" ≈ 0.784 km² (99.84 % of π·0.5²); **Check airspace** → Caution, "permission required" (inside VOBL's boundary: its 0–5 km band, "verify on Digital Sky"), nearest aerodrome VOBL | ☐ |
| P2 | Ctrl+K → `VOBL` → Enter | First result "VOBL · Kempegowda International Airport" (Large airport · Bengaluru · IATA BLR) †; the map moves there; Aerodromes and Aerodrome zone rings context layers on; switch on Obstacles | ☐ |
| P3 | Survey → New operation → Paste… → `POLYGON ((77.48 12.90, 77.50 12.90, 77.50 12.91, 77.48 12.91, 77.48 12.90))` → Read geometry → Use this operation → Estimate survey | Read as WKT, 5 vertices †; area ≈ 240.1 ha | ☐ |
| P4 | Coordinates… → `12 58 17.8 N 77 35 40.6 E`; then `151.2093, -33.8688`; then `12.9, 100.5` and `100.5, 12.9` on two lines | 12.97161° N, 77.59461° E †; read as longitude, latitude; refused (`invalid_coordinates`, both lines named) | ☐ |
| P5 | Phone width (< 768 px): pin, then **Check airspace** on the pin card | The pin is saved and an airspace check runs at 120 m AGL | ☐ |

**Environment** ([`environment.md`](docs/features/environment.md))

| # | Step | Expected | ✔ |
|---|---|---|---|
| E1 | *Bengaluru survey* → **Environment**; Date `2026-06-21`, Start `17:30`, End `19:30` (browser zone UTC+05:30), profile **FAA Part 107 (US)** → **Check environment** | Sunrise / sunset / civil dusk 05:55 / 18:48 / 19:12 †; solar noon 12:22, highest sun 79.5° †; 18 min outside the profile, 23 min lighting required †; declination −1.1°, inclination 14.5°, H ≈ 40,431 nT, F ≈ 41,770 nT at about 1,038 m †; comm tower and mast inside, 220 kV line crossing, 33 kV feeder 40.2 m, substation 218.6 m †; sun window 08:11–10:23 and 14:20–16:32 †; notes: window outside the profile, synthetic data, power line crossing † | ☐ |
| E2 | Same window, profile **Daylight only** | 42 min outside: "Window extends 42 min past sunset." | ☐ |
| E3 | Turn on wind: 8 m/s surface, preset 12 m/s | 11.4 m/s at 120 m, 0.6 m/s below the limit | ☐ |

**Line of sight** ([`los.md`](docs/features/los.md))

| # | Step | Expected | ✔ |
|---|---|---|---|
| L1 | *Bengaluru survey* → **Line of sight** → Add observer, Pilot at `12.902`, `77.488`; defaults → **Check line of sight** | Grid 54 × 47 cells of 30 m, 3 obstacles †; area in visual line of sight **97.2 %** †; pilot ground 917.35 m, eye 919.05 m AMSL, farthest seen 980.2 m †; path Pilot → Farthest corner 1,001.5 m (slant 1,008.6 m), FSPL **100.12 dB**, worst clearance 255.3 % at 14.9 m, max Fresnel radius 5.59 m †; largest hidden patch 33,300 m² (37 cells) †; VLOS guidance 500 m (ring 485.4 m), 57.1 % beyond it † | ☐ |
| L2 | Click the radio path row; **Show table** | Profile chart with terrain, the radio line and the dashed Fresnel edge; the samples as a table | ☐ |
| L3 | **Place on map**, click elsewhere in the area, run again | The coordinates update; the KPIs change; the previous run stays in History | ☐ |
| L4 | Target height preset **30 m** | 95.2 % in visual line of sight, hidden patch 4.32 ha | ☐ |
| L5 | Add a **Ground station** at `12.9015`, `77.4875`, 6 m; **Fixed radio target** "Relay mast" at `12.910`, `77.497`, 30 m; **5800 MHz** | Radio line of sight 98.0 %; GCS → Relay mast 1,395.4 m, FSPL 110.6 dB, worst clearance 315.6 % at 234.2 m, max Fresnel radius 4.25 m | ☐ |
| L6 | Remove every observer and run; then Exports → CSV | The pilot is assumed at the centre of the area (98.5 % seen); CSV rows `observer`, `radio_path`, `radio_profile` | ☐ |

**Ground risk and airspace coverage** ([`groundrisk.md`](docs/features/groundrisk.md))

| # | Step | Expected | ✔ |
|---|---|---|---|
| G1 | *Bengaluru survey* → **Ground risk** | *Operational volume* on; the preview reads about "contingency volume 46.6 m … up to 153.2 m above ground; ground risk buffer 153.5 m; adjacent area out to 5 km" | ☐ |
| G2 | **Assess ground risk** | iGRC **6** † (column ≤ 1 m, ≤ 25 m/s; band < 50,000); density **5,632 people/km²**, highest of 71 cells of 200 m †; S_CV **46.6 m**, H_CV **153.2 m**, GRB **153.5 m** †; footprint 2.20 km² †; adjacent area out to 5 km (3.24 km clamped), average **2,249 people/km²**, 100 % covered †; one assembly, **Synthetic Kengeri stadium**, 40,000–400,000 people, **756 m** from the volume †; Table 2 marks row "< 50,000", column "≤ 1 m, ≤ 25 m/s" | ☐ |
| G3 | Add an event "Harvest fair" at `12.9120, 77.4910`, 12,000 people; run again | Listed as "Your entry", < 40,000 people | ☐ |
| G4 | The map | Survey area solid, CV dashed about 47 m out, GRB hatched, the light adjacent ring to about 5 km, the stadium labelled | ☐ |
| G5 | Upload a GeoJSON point at 10.0, 45.0 (outside the pack) → **Check airspace** | "Airspace: Caution", the *Airspace not assessed* banner and the *Verify on Digital Sky* link; never Pass | ☐ |

**Tracks, flight audit and shows** ([`tracks.md`](docs/features/tracks.md))

| # | Step | Expected | ✔ |
|---|---|---|---|
| TR1 | `/w` → sample **Training flight (GPX)** | Flight audit with **Dataset track**: *Training flight*, GPX track, 401 timed points, 6 min 40 s, started 21 Mar 2026 04:30 UTC; Training area **Training box** | ☐ |
| TR2 | *Maximum distance from take-off* **500** → **Audit flight** | Verdict **Fail** †; max ≈ **129.4 m** above ground †; one exceedance ≈ 133.7–166.2 s (**32.5 s** above 120 m) †; **Left the training area (1)**: 1 min 00 s, 300 m flown, **100 m** at most outside †; **Beyond 500 m (1)**: ≈ 62 s, farthest **608.3 m** †; nearest aerodrome VOBG ≈ 19.7 km †; no "not assessed" banner † | ☐ |
| TR3 | Upload a GPX far from the pack (e.g. the 4-point track in `tests/api/test_tracks_api.py`) and audit it | **Airspace not assessed**, verdict **Caution**, never Pass | ☐ |
| TR4 | **50-drone show (CSV)** → **Ballistic**, safety factor **50 %** → **Validate show** | Site: *Derived from the tracks (+5 m margin)*, box **8,100 m²** †; audience distance **37.7 m (Ballistic)** with the formula "… 7.80 m/s × √(2 × 50.8 m / 9.80665 m/s²) = 25.1 m …; × (1 + 50 %) safety factor = 37.7 m" †; pad spacing 5.02 m; max height 50.8 m †. With *1:1 height* + 50 %: 76.2 m. **Check airspace for this show box** opens Airspace with a result for the box at 120 m | ☐ |
| TR5 | **50-drone show (clean)** → *Set the origin* 77.592 / 13.0005 → validate | "Origin 13.00050° N, 77.59200° E (given) · heights relative to the origin"; pad spacing 5.02 m; verdict Pass | ☐ |

**Heights, survey and spraying** ([`heights.md`](docs/features/heights.md))

| # | Step | Expected | ✔ |
|---|---|---|---|
| H1 | **Climbing line (3D)** → **Airspace** → Height reference **AMSL**, 950 m, minimum 30 m | Take-off ground 922 m AMSL †; *Height above ground* **28–30 m** †; *Below the minimum* **874 m** (1 stretch, lowest 28 m) †; summary "Flight level 950 m AMSL: from 28 m to 30 m above ground. Below the 30 m minimum above ground for 874 m of the route (1 stretch)." †; verdict Caution †; *Synthetic terrain* chip. Reload: the form keeps AMSL and 950 m | ☐ |
| H2 | Same line, **AGL** 120 m | The result of earlier versions (no take-off, no height range) | ☐ |
| H3 | *Bengaluru survey* → **Survey** → **Above take-off**, 100 m | "GSD 2.71–2.77 cm/px (2 m relief); worst side overlap 70 %."; take-off at the area centre (ground ≈ 918 m) | ☐ |
| H4 | **Climbing line (3D)** → **Survey** → **Corridor**, 100 m wide | 3 lines, 153 photos, 1 battery block, 5 GCPs, about 1.2 GB | ☐ |
| H5 | *Bengaluru survey* → **Survey** → **Spraying** (defaults); *Area unit* **Acre** | 118.86 ha, **2,377.2 L**, **238 tank loads, 238 sorties** (237 stops), 9 ha/h theoretical, ≈ 3.57 ha/h effective (≈ 40 %) †; 293.71 ac | ☐ |
| H6 | Camera *DJI Zenmuse H20T thermal*, 60 m; then *DJI P4 Multispectral*, 60 m | GSD **5.33 cm/px**; GSD 3.17 cm/px and storage counting 6 images per capture | ☐ |
| H7 | Battery reserve 20 %, *Count transit legs* | Usable 20 min per battery, a *Transit* tile (≈ 3.9 km), the *Transit in / out* column | ☐ |
| H8 | **Bengaluru eVTOL** → **eVTOL** → *Corridor* → **AMSL** 1,000 m, minimum 100 m → *Route corridor* | *Height above ground* **91–106 m**, below 100 m for **5.2 km** (from 11.7 km to the end) | ☐ |
| H9 | **Settings** → *Land area unit* **Bigha**, *Bigha size* **Gujarat** → reload | Both kept | ☐ |

**Evidence brief and exports** ([`brief.md`](docs/features/brief.md))

| # | Step | Expected | ✔ |
|---|---|---|---|
| B1 | *Bengaluru survey*: run **Airspace** (60 m) and **Ground risk** (defaults) → dataset header **Export → Create brief**; operator "Example Aerial Surveys", reference "OPS-1", aircraft "Mapping quadcopter", pilot 77.491, 12.905 → **Create brief** | Title "Operation evidence brief — OPS-1"; synthetic banner and watermark; a warning that ground risk assumed 1 m and 15 m/s while the aircraft is 0.6 m and 18 m/s; cards Airspace, Heights and terrain, Obstacles, Ground risk; Environment, Line of sight, Survey and spray effort, Flight audit, Drone show and eVTOL "Not assessed"; the input SHA-256 of `bengaluru_survey.kml` in the provenance | ☐ |
| B2 | **Print / Save as PDF** | One A4 page; the toolbar says the text scale | ☐ |
| B3 | **Download bundle (.zip)** | `brief.html` opens offline; `provenance.json` lists the SHA-256 of every other entry; `evidence.kml` opens in Google Earth with one folder per analysis | ☐ |
| B4 | `POST /api/briefs/` twice with the same ids and operation | The same `brief_sha256` | ☐ |
| B5 | Results drawer at 768 px → **Export** | JSON, GeoJSON, KML and every CSV table (`GET /api/analyses/{id}/export/tables/`) | ☐ |
| B6 | Survey → export GeoJSON and KML | No `lines` layer in either (survey lines are never exported) | ☐ |

### 5.7 Zones and one result per feature **[M15–M16]**

Synthetic demo pack regenerated as in §2.1 (it needs the aerodrome boundaries), web app rebuilt, API and web running. The steps come from [`zones.md`](docs/features/zones.md), [`zones-followups.md`](docs/features/zones-followups.md), [`batch.md`](docs/features/batch.md) and [`choices.md`](docs/features/choices.md). Numbers marked † were re-checked through the API (in-process, synthetic pack) on 2026-10-23.

**Zone rules and aerodrome bands** ([`zones.md`](docs/features/zones.md))

| # | Step | Expected | ✔ |
|---|---|---|---|
| Z1 | Sample **Bengaluru eVTOL** → rail **Airspace** | The amber **Red zones are not published as open data** notice with the *Digital Sky map* link at the top of the form; *Zone rules* → **Rules** shows **DGCA 2021** | ☐ |
| Z2 | **Check airspace** | The notice again; **Aerodrome bands** chips *VOBG 0–5 km: verify on Digital Sky (permission)*, *VOBG 5–8 km*, *VOBG 8–12 km: …*, *Elsewhere*; the zones table has **Active** and **Validity** columns; on the map the HAL bands follow its east–west outline (stadium-shaped, not circles) | ☐ |
| Z3 | Rules switch: **Rules** → *Conservative (illustrative)* → **Check airspace** | Chips *VOBG 0–8 km* and *VOBG 8–12 km*, and "Rules: Conservative (illustrative)." under them | ☐ |
| Z4 | Operational switch: back to DGCA 2021; *Aerodrome status* → switch off **Operational: VOBG · HAL Airport** → **Check airspace** | No VOBG chips; the note "Marked not operational for this check …"; History and the brief name `aerodrome_operational` | ☐ |
| Z5 | Drawn route: New operation → Paste… → `LINESTRING (77.7066 13.2950, 77.7066 13.2995)` → Use this operation → **Airspace**, 75 m AGL → **Check airspace** | Verdict **Caution** †; chip *VOBL 8–12 km* "ceiling 60 m, yellow zone above; flown 75 m, 15 m over" †; the summary "In the 8–12 km band of VOBL the flight reaches 75 m above ground, 15 m above its 60 m green ceiling: yellow zone above it, permission required." †; the hit is 8.27 km from the perimeter † | ☐ |
| Z6 | Same route under *Conservative (illustrative)* | *VOBL 8–12 km* without permission, 15 m over †, measured from the reference point (8.67 km after the 2 km assumed radius) † | ☐ |
| Z7 | Coordinates… → `13.2256, 77.7066` (0.6 km from the VOBL boundary) → Pin → **Use this operation** → **Check airspace** | Zone *0–5 km: not defined by the published rule — verify on Digital Sky (permission)*, yellow, permission required †; verdict **Caution**, never Pass † | ☐ |

**Zones from layers, the planned window and ED-318**

| # | Step | Expected | ✔ |
|---|---|---|---|
| Z8 | Zones from layers: sample **Farm plots (Shapefile, UTM 43N)** → **Airspace** → *Zones from layers* → switch on **plots_shapefile.zip** → **Check airspace** | *Airspace: Fail*: the plots are red zones (*Otherwise* defaults to red) | ☐ |
| Z9 | In *Columns of plots_shapefile.zip* set **Otherwise** to *Yellow zone* → **Check airspace** | Caution, permission required | ☐ |
| Z10 | ED-269 import: save the file below as `event.json` and upload it | It opens as one polygon (the 500 m circle as a 64-gon) with the note "Layer '…' was read as an ED-269 zone file: 1 zones, circles as 64-sided polygons, limits in metres. …" † | ☐ |
| Z11 | Planned window: *Bengaluru survey* → **Airspace** → *Zones from layers* → switch on `event.json`; *Zone rules* → **Planned window** on, 2026-10-23, 14:30–15:00 (UTC+05:30, i.e. 09:00–09:30 UTC) → **Check airspace** | *T1 Event area* is a yellow, **Active** hit; verdict Caution † | ☐ |
| Z12 | Move the window to 18:30–19:00 (13:00–13:30 UTC) → **Check airspace** | The zone is under **Zones that do not apply**: "Not active in the planned window (valid 2026-10-23 00:00 to 2026-10-24 00:00 UTC, ANY 08:00–12:00 UTC)" †; verdict Pass †; the brief's cross-check says the window may leave out 1 time-limited zone | ☐ |
| Z13 | ED-318 export: on the Z5 result, Exports → **Zones (ED-318)**; then a dataset's Export → **Zones (ED-318)** | A `.ed318.geojson` with "VOBL 8–12 km up to 60 m", "VOBL 8–12 km above 60 m", "VOBL 5–8 km" and "VOBL 0–5 km" † (one Feature per zone, limits in `geometry.layer`), and a top-level `airbrief` member with the evidence notice and the watermark "SYNTHETIC DEMO DATA — not evidence"; the dataset export holds its polygons, without that member. Upload the analysis export again: it reads back as an ED-318 zone file | ☐ |

`event.json` (Z10):

```json
{"features": [{"identifier": "T1", "name": "Event area", "restriction": "REQ_AUTHORISATION",
  "geometry": [{"uomDimensions": "M", "lowerLimit": 0, "lowerVerticalReference": "AGL",
                "upperLimit": 120, "upperVerticalReference": "AGL",
                "horizontalProjection": {"type": "Circle", "center": [77.491, 12.905], "radius": 500}}],
  "applicability": [{"startDateTime": "2026-10-23T00:00:00Z", "endDateTime": "2026-10-24T00:00:00Z",
                     "schedule": [{"day": "ANY", "startTime": "08:00", "endTime": "12:00"}]}]}]}
```

**Zone rules in the flight audit, eVTOL, context rings and the bundle** ([`zones-followups.md`](docs/features/zones-followups.md))

| # | Step | Expected | ✔ |
|---|---|---|---|
| ZF1 | `/w/view?m=flight-audit&sample=flightmd_report_v12` → *Zone rules* → **Rules** *Conservative (illustrative)* → **Audit flight**; reload; then pick *Custom* | The **Aerodrome bands** chips end with "Rules: Conservative (illustrative)."; after the reload the select still shows it; *Custom* opens the JSON box filled with the DGCA rules | ☐ |
| ZF2 | API: the track of §6 K28 (`POST /api/analyses/`, `kind: flight_audit`) | Stored `zone_profile` `dgca_2021`; verdict Fail; band "8–12 km" yellow, permission, margin −15 m, 9 fixes; exceedance limit 60 m †. With `"zone_profile": "conservative_illustrative"`: "8–12 km" without permission (−15 m, 6 fixes) and "Elsewhere" green (+45 m, 3 fixes) † | ☐ |
| ZF3 | **Bengaluru eVTOL** → **eVTOL** → **Score sites** (DGCA 2021); then Conservative | Site #1 **76.05** (green) †; sites #2, #3, #7, #8 and #10 "Verify on Digital Sky", and the issue `verify_official_map` names them †; the criteria end "Zone rules: DGCA 2021."; Conservative: site #1 **72.57** (yellow) †, no such issue † | ☐ |
| ZF4 | *Corridor* tab, Whitefield → Electronic City (DGCA 2021); then Conservative | **16.893 km**, detour **3.46 %** †; HAL 0–5 km "Verify on Digital Sky", 5–8 km, and 8–12 km "Permission (255 m over)" †; issues "The corridor crosses VOBG 0–5 km …" and "… up to 315 m above ground, over the 120 m ceiling of open airspace …" †. Conservative: the same route, HAL 0–8 km and 8–12 km, neither issue † | ☐ |
| ZF5 | Airspace panel open, Ctrl+K → `VOBL` (rings on), **Rules** → *Conservative (illustrative)* | The rings are fetched again with `profile=conservative_illustrative` (circles from the reference point instead of bands around the runways); the rings legend names the rule set | ☐ |
| ZF6 | A brief with an airspace check and a flight audit → **Download bundle** | `zones.ed318.geojson` is in the zip and in `provenance.json`; `python -m airbrief_core.verify bundle.zip` passes (with `--public-key` on a signing instance) | ☐ |

**One result per feature** ([`batch.md`](docs/features/batch.md))

| # | Step | Expected | ✔ |
|---|---|---|---|
| BA1 | `/w` → **Farm plots (Shapefile, UTM 43N)** → **Airspace** → Options: **One result per feature (all 3)** → **Check airspace** | The drawer says "One result per feature"; summary bar "Permission required: 3" †; clicking the chip filters the table and the line reads "Showing 3 of 3 features (…)"; **Find a feature** and **Show all** work; a row click zooms to and highlights the plot, and a click on a plot highlights its row; the map colours the plots with a "Result per feature" legend; Layers lists "Result per feature" with **View as table** | ☐ |
| BA2 | Per-feature CSV: Exports → CSV → "One row per feature, with its attributes" | One row per plot with `crop` Ragi / Tomato / Mulberry and `owner` †; the plots' own `name` attribute is the column `attr.name` †; GeoJSON and KML carry the verdict properties | ☐ |
| BA3 | **Survey** (Job section), the same switch → **Estimate survey** | Plot A **1.998 ha**, Plot B **2.248 ha**, Plot C **1.498 ha**, 1 battery each †. Then **Ground risk** (Operation), **Environment** (Interference) and **Line of sight** (Features) the same way: one row per plot | ☐ |
| BA4 | **Report** of the BA1 analysis | Key numbers and the table "One result per feature (most severe first)" | ☐ |
| BA5 | Brief with a batch: **Brief** tab → tick the BA1 analysis → **Create brief**; **Download bundle** | The section "Airspace & terrain: one result per feature" with the counts and the feature table; the zip holds `tables/01_airspace_features.csv` | ☐ |
| BA6 | The API-only settings, under **Advanced**: Airspace **Grid cell limit**; Ground risk **Cell limit**; eVTOL Siting **Obstacle radius**, **Cell limit**; eVTOL Corridor **Route cost weights**, **Grid resolution (H3)**, **Cell limit**, **Study area margin**, **Noise reference distance**, **Profile spacing**; 50-drone show **Dynamics window**, **Airborne threshold**, **Events listed**; Flight audit **Data-pack zones** | Each runs and is restored on reload; a grid cell limit below 20,000 or a dynamics window above 1 s is named by the brief's `non_default_parameters` cross-check | ☐ |
| BA7 | API: `"batch": {"mode": "per_feature"}` with 1,001 features in `batch.features`; then `line_of_sight` with 501 | `422 too_many_features` both times (limits 1,000 and 500) | ☐ |

**Terrain-model caveat**

| # | Step | Expected | ✔ |
|---|---|---|---|
| D1 | DemCaveat: *Bengaluru survey* → **Line of sight** → **Check line of sight** | The "Synthetic terrain" chip under the key numbers | ☐ |
| D2 | **Training flight (GPX)** → **Audit flight** | The chip under the height profile | ☐ |

**Assessment choices** ([`choices.md`](docs/features/choices.md))

Use one private window (fresh browser storage) for the whole block, in order: AC3 sets the choices that AC4–AC8 start from.

| # | Step | Expected | ✔ |
|---|---|---|---|
| AC1 | **Settings** → *Assessment choices*; pick the other option in each group; reload; **Reset settings** | Three groups: "Flight audit: flying above a band's free-flight ceiling" on **An exceedance (fail)**, "eVTOL siting and corridors: within 5 km of an airport" on **Permission required**, "ED-318 zone exports: where the vertical limits go" on **In the geometry**; each option has one sentence on what it does and when to pick it. The new picks survive the reload; **Reset settings** puts the defaults back, and **Undo** in its toast restores the picks | ☐ |
| AC2 | First-time note: `/w/view?m=flight-audit` | "Using the default assessment choices. Answer 3 questions to set yours" in the form, not a modal. The link opens Settings with **Help me choose** open; Escape closes it and focus is on **Help me choose**. Back in the panel, the note's **X** hides it, also after a reload and in the eVTOL panel | ☐ |
| AC3 | **Settings** → **Help me choose** → "Reviewing flights after the fact", "Routinely", "Not sure" → **Apply** | The airport question is not asked for flight work. *Recommended* shows **Permission required (caution)**, **Permission required** and **In the geometry**, each with a one-line reason. **Apply** sets the three radios, the toast says "Assessment choices set", and focus is back on **Help me choose**. (With "eVTOL or vertiport studies" the permission question goes and "Near airports, how cautious should eVTOL studies be?" comes; **Cancel** changes nothing) | ☐ |
| AC4 | **Training flight (GPX)** → **Flight audit**; *Height limit above ground* **150**; *Training area* **Off**; *Zone rules* → *Above a band's free-flight ceiling* is on **Permission required (caution)**, "From your assessment choices; this run can differ." → **Audit flight** | Verdict **Caution** †; *Exceedances (0)*; *In yellow airspace above a band's ceiling (1)*: 400 m to 500 m, max height 129 m, ceiling 120 m, over by 9 m, "Flown in yellow airspace above the 120 m ceiling outside mapped zones: permission required" †; the height profile marks the stretch "Yellow 1"; the map layer "Above a band's ceiling"; History adds "band ceilings as permission". A brief with this run has the `non_default_parameters` cross-check "… (band_ceiling: permission)" | ☐ |
| AC5 | Same form, **An exceedance (fail)** → **Audit flight**; reload | The field says "This run differs from your assessment choices (Permission required (caution))." Verdict **Fail** †; one exceedance from 400 m to 500 m (33 s), "Above the 120 m default ceiling outside mapped zones" †, marked "Over 1" on the profile; no *In yellow airspace* section. After the reload the run keeps **An exceedance (fail)** | ☐ |
| AC6 | **Bengaluru eVTOL** → **eVTOL** → *Zone rules* → *Within 5 km of an airport (verify on Digital Sky)* **Keep out, like a red zone** → **Score sites** | "This run differs from your assessment choices (Permission required)."; *Cells evaluated* 531, **140 excluded** †; site #1 **76.05** †, site #2 **63.31** (yellow) †, none "Verify on Digital Sky"; the issue "140 cells in VOBG 0–5 km were excluded like a red zone (unverified_band: exclude) …" †. With **Permission required**: sites #2, #3, #7, #8 and #10 "Verify on Digital Sky" (ZF3) | ☐ |
| AC7 | *Corridor* tab (the same choice, shared by both tabs), Whitefield → Electronic City → **Route corridor** | **17.799 km**, detour **9.01 %** †, against 16.893 km and 3.46 % with **Permission required** (ZF4); the issue "Cells in VOBG 0–5 km were treated like a red zone: impassable (unverified_band: exclude). …" †; History adds "airport bands kept out" | ☐ |
| AC8 | **Settings** → ED-318 **In the properties**; the Z5 result → **Export** → **Zones (ED-318, limits in properties)**; a dataset's **Export** menu | The link carries `ed318_limits=properties`; "VOBL 8–12 km up to 60 m" has `lower` 0, `upper` 60, `lowerReference` and `upperReference` AGL and `uom` M in its properties and no `geometry.layer` †; uploaded again, it reads back as an ED-318 zone file of the same four zones †. The dataset menu names the same layout. With **In the geometry**: **Zones (ED-318, limits in geometry)**, as in Z13. A brief bundle's `zones.ed318.geojson` always has the limits in the geometry | ☐ |
| AC9 | API: the K28 track (§5.7 ZF2) with `"band_ceiling": "permission"`, then with `"maybe"`; `GET /api/analyses/<id>/export/?format=json&ed318_limits=properties` | Stored `band_ceiling` `permission`; verdict caution, no exceedances, one `above_band_ceiling` run with `limit_m` 60 and `max_agl_m` 75, the profile's limit 120 m †; `GET …/export/tables/` lists `above_band_ceiling` †. `"maybe"`: `422 invalid_params`; `ed318_limits` with another format: `422 invalid_params` †. A run without the field stores `exceedance` (eVTOL: `unverified_band` `permission`); an airspace check refuses both fields (`422`) | ☐ |

---

## 6. Known-answer reference

Values come from closed-form maths, the WGS84 geodesic (Karney), or the deterministic samples. The test suite asserts the same values.

| ID | Case | Expected | Tolerance |
|---|---|---|---|
| K1 | 1 km × 1 km square at 0°N (`square_1km_equator.kml`) | Area 1,000,000 m²; perimeter 4,000 m | ±0.01% |
| K2 | 1 km × 1 km square at 60°N (`square_1km_60N.kml`) | Area 1,000,000 m²; perimeter 4,000 m | ±0.01% |
| K3 | Line (0°, 0°) → (1°E, 0°) along the equator | 111,319.49 m | ±0.01% |
| K4 | Line (0°, 0°) → (0°, 1°N) along a meridian | 110,574.39 m | ±0.01% |
| K5 | 1,000 m horizontal line climbing 0 → 100 m (`climb_line_z.kml`) | 2D 1,000.00 m; 3D 1,004.99 m (√(1000² + 100²)) | ±0.01% |
| K6 | Survey: P4 RTK (13.2 × 8.8 mm sensor, 8.8 mm lens, 5472 × 3648 px) at 100 m, front 80%, side 70% | GSD 2.74 cm/px; footprint 150.0 × 100.0 m; line spacing 45.0 m; trigger distance 20.0 m | ±0.5% |
| K7 | FlightMD sample flight (`flightmd_report_v15.json`, same track in `_v12`) | Max height above takeoff **135 m**; one exceedance above the 120 m ceiling from **1,157.1 m to 1,642.9 m** along the track (485.8 m long) | ±0.5 m height; ±1 m distance (takeoff-relative; DEM-corrected values differ by terrain) |
| K8 | 50-drone show sample | Exactly 1 conflict: **D07–D32 at t = 48.0 s, minimum separation 0.80 m** (threshold 1.5 m); the clean variant has **0** conflicts. Identical on the `python` and `cpp` backends | exact pair & time; distance ±0.05 m |
| K9 | Ingest attacks | zip-slip → 422 `unsafe_archive`; zip bomb (> 200 MB uncompressed) → 422 `archive_too_large`; > 50 MB upload → 413 `file_too_large` | exact codes |
| K10 | Plots shapefile (`plots_shapefile.zip`, EPSG:32643, near Bengaluru) | Grid areas 20,000 / 22,500 / 15,000 m² (in the source CRS); true local-projection areas ≈0.11% smaller (≈19,978 / 22,474 / 14,983 m²), because the UTM 43N scale factor there is ≈1.00057 | grid exact ±0.01 m²; true/grid ratio 0.9989 ± 0.0002 |
| K11 | Pin radius polygon (64-gon inscribed in a circle of radius R) | Area = (64/2)·R²·sin(2π/64) = 99.84 % of πR²; R = 500 m → ≈ 0.784 km² | ±0.2 % (geodesic) |
| K12 | Typed coordinates `12 58 17.8 N 77 35 40.6 E` | 12.97161° N, 77.59461° E (D + M/60 + S/3600) | exact to 5 decimals |
| K13 | Sun at the *Bengaluru survey* centroid (77.4910° E, 12.9037° N), 2026-06-21 | Sunrise 00:25:13Z, sunset 13:18:25Z, civil dusk 13:41:49Z; solar noon 12:22 local, 79.5° | NOAA times ±1 min (tests: within 2 s) |
| K14 | WMM2025 at the same point, ≈ 1,038 m above the ellipsoid, 2026-06-21 | D −1.1°, I 14.5°, H ≈ 40,431 nT, F ≈ 41,770 nT; NOAA's official test values in `tests/core/test_environment.py` | D, I ±0.01°; intensities ±0.01 nT (test values) |
| K15 | Wind power law, 10 m/s at 10 m, α = 1/7 | 13.9 m/s at 100 m (−1.9 m/s against a 12 m/s limit) | ±0.05 m/s |
| K16 | Earth curvature drop, 10 km, k = 4/3 | d²/(2kR) = 5.886 m | ±0.001 m |
| K17 | First Fresnel radius and free-space loss, 2.4 GHz, 1 km path, midpoint | r1 = 5.59 m; FSPL = 100.04 dB | ±0.01 m; ±0.01 dB |
| K18 | Line of sight, *Bengaluru survey*, pilot at 12.902, 77.488, defaults (§5.6 L1) | 97.2 % in visual line of sight; FSPL 100.12 dB over 1,008.6 m slant; hidden patch 33,300 m² | as shown (deterministic synthetic pack) |
| K19 | Ground risk, *Bengaluru survey*, mapping quadcopter (0.6 m, 18 m/s), 120 m, pitch 35° | S_CM = 18²/(2 × 9.80665 × tan 35°) = 23.6 m; S_CV 46.6 m; H_CV 153.2 m; GRB 153.5 m; iGRC 6; density 5,632 people/km² | ±0.1 m; exact iGRC |
| K20 | SORA 2.5 Table 2 | All 35 cells; 121 m/s at 20 m → the 40 m column; the fourth column is 20 m / 120 m/s | exact |
| K21 | GPX training flight, 500 m distance check (§5.6 TR2) | 32.5 s above 120 m; one training-area exit of 60 s and 100 m; farthest 608.3 m from take-off | ±0.1 s; ±0.1 m |
| K22 | 50-drone show (CSV), ballistic + 50 % (§5.6 TR4) | v·√(2h/g) = 7.80 m/s × √(2 × 50.8 / 9.80665) = 25.1 m; × 1.5 = 37.7 m | ±0.1 m |
| K23 | *Climbing line (3D)*, airspace at 950 m AMSL, minimum 30 m (§5.6 H1) | Take-off ground 922 m; 28–30 m above ground; 874 m below the minimum | ±1 m |
| K24 | Spraying, *Bengaluru survey*, defaults (§5.6 H5) | 118.86 ha × 20 L/ha = 2,377.2 L; 238 tank loads of 10 L; capacity 5 m × 5 m/s × 3,600 / 10,000 = 9 ha/h | ±0.1 L; exact loads |
| K25 | DGCA 2021, route `77.7066, 13.2950` → `77.7066, 13.2995` at 75 m AGL (§5.7 Z5) | Band VOBL 8–12 km, 8.27 km from the perimeter; ceiling 60 m, so margin **−15 m**: yellow, permission required; verdict caution. Conservative profile: the same band from the reference point (8.67 km after the 2 km radius), −15 m, no permission | exact margin; distance ±0.01 km |
| K26 | DGCA 2021, a point 0.6 km from the VOBL boundary (§5.7 Z7) | 0–5 km band: yellow, permission required, `basis: verify_official_map`; never pass | exact |
| K27 | ED-269/ED-318 conversions | A circle becomes a 64-gon of 99.84 % of πr² (K11); feet × 0.3048, rounded to 2 decimals (500 ft = 152.4 m); `99999` or `UNL` = unlimited; `parse_ed318(to_ed318(zones)) == zones` | area ±0.2 %; exact otherwise |
| K28 | Flight audit, 9 fixes at 75 m AGL every 200 m from 12.0 to 13.6 km east of HAL (`77.7788, 12.9499` to `77.7935, 12.9499`, `altitude_mode: relativeToGround`; §5.7 ZF2) | DGCA: band 8–12 km, 9 fixes, margin −15 m, permission; exceedance against 60 m; verdict fail. Conservative: 8–12 km 6 fixes (−15 m) and Elsewhere 3 fixes (+45 m) | exact |
| K29 | eVTOL, *Bengaluru eVTOL*, defaults (§5.7 ZF3, ZF4) | Siting: site #1 76.05 (DGCA) / 72.57 (conservative). Corridor Whitefield → Electronic City: 16.893 km, detour 3.46 %; HAL 8–12 km 254.7 m over its 60 m ceiling | ±0.01 (score); ±0.001 km |
| K30 | One result per feature, *Farm plots*, survey defaults (§5.7 BA3) | 3 items: 1.998 / 2.248 / 1.498 ha (about 0.11 % below the grid areas, as in K10), 1 battery each; airspace: Permission required 3 | ±0.001 ha |
| K31 | Planned window against `event.json` (§5.7 Z10–Z12), valid 08:00–12:00 UTC on 2026-10-23 | Window 09:00–09:30 UTC: active. 13:00–13:30 UTC: not active. Intervals are half-open, so a window starting at 12:00 does not overlap | exact |
| K32 | Flight audit with `band_ceiling: permission`: the K28 track (§5.7 AC9); the GPX training flight with a 150 m height limit and no training area (§5.7 AC4–AC5) | K28: caution, no exceedance, one `above_band_ceiling` run with `limit_m` 60 and `max_agl_m` 75. Training flight: caution, one run from 400 m to 500 m above the 120 m ceiling of open airspace, max 129.41 m; with `exceedance` the same stretch is an exceedance against 120 m and the verdict fail | exact |
| K33 | eVTOL with `unverified_band: exclude`, *Bengaluru eVTOL* (§5.7 AC6–AC7) | Siting: 140 of 531 cells excluded, site #1 76.05, site #2 63.31. Corridor Whitefield → Electronic City: 17.799 km, detour 9.01 % (costed: 16.893 km, 3.46 %, K29) | ±0.01 (score); ±0.001 km |

How K1/K2 are built: the square's corners are laid out 1,000 m apart on a local projection, then stored in KML as lon/lat. The *true* geodesic area is therefore 1,000,000 m² to well within the tolerance.

Why K10 differs: UTM is conformal, not equal-area. About 2.6° east of the zone's central meridian (75°E) its scale factor is above 1, so grid sizes overstate true ground sizes. AirBrief reports both; see `docs/adr/0002-local-projections-for-measurement.md`.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Address already in use` (8000 or 3000) | Another app uses the port | `uvicorn api.main:app --reload --port 8001` and set `NEXT_PUBLIC_API_URL=http://localhost:8001` in `frontend/.env.local`; or `npm run dev -- -H 127.0.0.1 --port 3001` and add that origin to `CORS_ORIGINS` |
| `/health` shows `"db":"error"` | Wrong `DATABASE_URL`, or the `data/` folder is not writable | Check `.env`; for SQLite the default `sqlite:///./data/airbrief.db` is relative to the folder you start uvicorn from (the repo root) |
| `ModuleNotFoundError: psycopg` | Postgres URL without the extra | `pip install -e ".[api,postgres]"` |
| Migration error on start | Database from an older, incompatible revision | `alembic upgrade head` and read the error; for a throwaway SQLite database, delete `data/airbrief.db` and restart |
| `PROJ: proj_create: ... no database` / GDAL errors | Mismatched PROJ data (another GIS install on PATH) | Use a fresh virtual environment; `pip install --force-reinstall pyproj rasterio`; unset `PROJ_LIB`/`PROJ_DATA`/`GDAL_DATA` |
| `pip install` fails building rasterio, shapely or pyproj | Python version without wheels | Use Python 3.11 or 3.12 (binary wheels exist for Windows, macOS and Linux) |
| Data pack download stalls or fails | Network / source down | Re-run the same command (it resumes; Overpass falls back to its mirror). Meanwhile use the synthetic `demo` pack |
| Modules show "Data pack missing" | No data pack covers the inputs | Run §2 step 5 (synthetic) or step 6 (real); set `DEFAULT_DATAPACK` in `.env` to prefer one |
| Map is blank but the UI loads | Basemap tiles blocked (offline / firewall) | Settings → Basemap → **Offline (no tiles)**; analyses still work |
| **My location** says the browser blocked it | Not a secure context (only HTTPS or localhost may ask), the permission was denied, or a reverse proxy replaced the app's `Permissions-Policy` | Use HTTPS or localhost; allow location for the site; make the proxy keep `geolocation=(self)` (`DEPLOYMENT.md` §3.2). Typed coordinates always work |
| Context layers say "Zoom in" | The view is wider than 2° × 2° | Zoom in; the API refuses larger views (`area_too_large`) |
| Environment says "power lines not assessed", or "Go to" does not find `BLR` | The data pack predates the 2026-10 upgrade | Regenerate it (§2.1) |
| Airspace warns `perimeter_approximated`, or the bands are circles | The pack has no `aerodrome_perimeters` / `runways` (built before 2026-10-23), or OSM has no boundary for that aerodrome | Rebuild the pack (§2.1, §2.2). Without a boundary the bands come from the reference point; check Digital Sky |
| A per-feature run ends with "Not run (time budget)" rows | No feature starts after 600 s | Select the rows not run and run them as another batch. On a server, keep `JOB_TIMEOUT_S` above 600 s, or the whole run fails with `job_timeout` |
| `422 too_many_features` / `too_many_zones` | More than 1,000 features (line of sight 500) in one result per feature; more than 5,000 zones from the chosen layers near the area | Select fewer features; pick fewer zone layers, one layer, or a smaller area |
| An upload fails with `invalid_zone_file` | An ED-269/ED-318 file with a malformed zone (the message names its index) or over the caps (100 volumes and 10 periods per zone, 5,000 zones in all after the split into volumes × periods) | Fix that zone in the file. A file is read as a zone file from its content (an ED restriction code in `type`, or a `layer`/`extent` on a geometry), not its name |
| A job shows "The server stopped while this analysis was running" | Its process stopped (crash or restart) and its heartbeat went stale for 60 s | Run it again. Other processes' live jobs are never failed this way |
| Browser shows "Cannot reach the API" / CORS error | API not running, wrong URL, or origin not allowed | Check `http://localhost:8000/health`; `NEXT_PUBLIC_API_URL` in `frontend/.env.local`; add your web origin to `CORS_ORIGINS` |
| Upload or analysis stuck at PENDING | `JOB_MODE=external` without a worker, or a crashed job | Start `python -m api.worker`, or set `JOB_MODE=inprocess`; check the API terminal for the job id |
| `npm install` fails | Old Node or npm | Use Node 20 LTS (`node --version`) |
| `make: command not found` on Windows | No make | Use Git Bash or WSL, or the *Raw equivalent* column in §4 |
| Everything is weird | Stale state | Stop both servers, delete `data/` and `frontend/.next/`, then redo §2 steps 4–8 |

---

## 8. Reporting a problem

Please include:
1. **What you did:** the step number from §5, plus the sample file.
2. **What you expected vs saw:** a screenshot if it's in the UI.
3. **The job/dataset id:** shown in the UI inspector header, in the URL (`?d=`), and in every API response.
4. **Logs:** the API terminal output (or `uvicorn api.main:app --log-level debug > api.log 2>&1`), and the browser console for UI issues.
5. **Versions:** `curl -s localhost:8000/health` (includes the app version, schema version and native backend).

---

## 9. Demo video

The product video is rendered from the running app by [`tools/demo-video`](tools/demo-video/README.md):
- Playwright drives the app;
- Kokoro, a free local voice model, reads the narration;
- FFmpeg joins everything.

It needs Node 20+ and FFmpeg 5+ on `PATH`, plus the app running as in §2. Use `npm run build && npm start -- -H 127.0.0.1` for the web app: it records more smoothly than `npm run dev`.

```bash
cd tools/demo-video
npm install && npm run setup     # once: packages + Chromium
npm run draft                    # quick silent pass → out/draft.mp4
OFFSCREEN=1 npm run video        # full render → out/demo.mp4, demo.vtt, demo-thumbnail.jpg
npm run verify                   # format, length, loudness, voice timing, review frames
```

| # | Check | Expected | ✔ |
|---|---|---|---|
| V1 | `OFFSCREEN=1 npm run video` | Ends with `Done: …/out/demo.mp4` of about 80 s (12 scenes); the first run also downloads the voice model | ☐ |
| V2 | `npm run verify` | Every line `PASS`: 1920x1080, 30/1, h264 + aac, −16 ±1 LUFS, voice timing within ±0.20 s | ☐ |
| V3 | Watch `out/demo.mp4` muted | Captions alone tell the story; no loading spinner at normal speed; spotlights land on the named element | ☐ |
| V4 | Listen once | Voice clear; music clearly lower and dipping under speech; clicks soft | ☐ |

If a scene fails, `out/failed.png` shows the page at that moment. `npm run scene -- <id>` re-renders just that scene. The scene ids are in `tools/demo-video/scenes.mjs`.
