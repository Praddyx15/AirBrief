# Tracks: GPX and richer CSV ingest, flight audit from a position track, drone-show site checks

This feature set covers three things that all start from recorded or choreographed positions:

1. **Ingest** — GPX files (waypoints, routes, tracks with times), CSV files that keep their extra
   columns (tower height, asset id…) and can become point lists, and zips of per-drone CSVs
   (Skybrush export style).
2. **Flight audit from a plain position track** — the Flight audit module audits a dataset's
   timed track (GPX `trk`, KML `gx:Track`, CSV with a time column) as well as a FlightMD report,
   with two optional checks: a **training area** ("left the area" segments) and a **maximum
   distance from take-off** (VLOS guidance), plus the **time above the height limit**.
3. **Drone-show upgrades** — a show box derived from the tracks when none is drawn, an audience
   distance from the fall-out rule (fixed, 1:1 or ballistic, plus a safety factor), take-off and
   landing pad spacing, per-drone endurance, the origin and height reference in the panel, and
   "Check airspace for this show box".

Everything is evidence about the positions as given — never an approval, a "safe to fly" or a
compliance finding. AirBrief parses no flight-controller logs (that is FlightMD's scope): it reads
position files only.

## 1. Ingest

### GPX (`airbrief_core/io/gpx.py`)

Upload a `.gpx` (or a zip holding several). GPX 1.0, 1.1 and namespace-less files are read;
`<extensions>` are ignored.

| GPX element | Becomes | Properties |
|---|---|---|
| `wpt` | one Point per waypoint (Z = `ele`) | `name` (feature name), `ele`, `time`, `desc`, `cmt`, `sym`, `type`, `src` |
| `rte` / `rtept` | one LineString per route (Z = `ele`) | `desc`, `cmt`, `src`, `type`, `number`, `points` |
| `trk` / `trkseg` / `trkpt` | **one LineString per track**, segments joined in file order | as routes, plus `segments`, `start_time`, `duration_s` |

- **Timed tracks:** when every track point has a `<time>` and the times never go backwards, the
  track keeps `times` (seconds from the first point) and `time_origin_utc` (ISO 8601 UTC), like a
  KML `gx:Track`; the API reports such features with `_time_points` and `_duration_s`, and the
  flight audit and shows can use them. Partial or backwards times are dropped with a warning.
- **Segments:** several `trkseg` are joined into one line (gaps bridged by a straight line, with
  an info note) — the same rule as KML `gx:MultiTrack`, so one track stays one flight.
- **Heights:** `ele` is metres above mean sea level, so features with heights get
  `altitude_mode = "absolute"`; without any `ele` the feature is 2D (`clampToGround`); missing
  `ele` inside a line are interpolated from their neighbours (info note).
- **Hardening:** the lxml parser of the KML reader (no entity resolution, no DTD loading, no
  network; without `huge_tree`, nesting deeper than 256 levels and attribute values or names above
  10 MB are refused); documents declaring entities are rejected (`corrupt_file`), files above
  100 MB are refused (`file_too_large`; uploads are capped at 50 MB and archives at 200 MB
  unzipped anyway) and every vertex counts against the upload's vertex budget.
- Layers: `<name> / Waypoints`, `<name> / Routes`, `<name> / Tracks` (non-empty ones), where
  `<name>` is the file stem inside a zip, else the GPX metadata name, else `GPX`.

### CSV (`airbrief_core/io/tracks.py`)

- **Extra columns are kept** as feature properties under their header text (`Tower height (m)`),
  numbers parsed only when clean (`42`, `-3.5`, `1e3`; `007`, `1,200` and >15-digit integers stay
  text). Tracks keep a column only when it is constant along the track; columns that change per
  sample (LED colour, battery) are listed in one info note.
- **Points instead of a line:** without a time column, when an id/name column has a different
  value on every row (a tower or site list), each row becomes a Point; `# geometry=points`
  (or `# geometry_hint=points`) forces points, `# geometry=tracks` forces tracks.
- Skybrush-style headers are understood: units in brackets (`x [m]`), `time_msec` / `[ms]`
  milliseconds.
- **Heights:** without a height column (`alt`/`altitude`/`height`/`elevation`, or `z` for local
  x/y) the features are 2D with `clampToGround`, like a GPX without `<ele>` — never read as 0 m.
  Blank height cells inside a track are interpolated from the neighbouring rows (info
  `heights_interpolated`).

### Zips of CSVs (`airbrief_core/io/ingest.py`, `io/reader.py`)

A zip may hold shapefiles, KML, GeoJSON, GPX and CSV files; every supported file is read. A zip
of per-drone CSVs (Skybrush export: one file per drone) becomes one track per file, the drone id
being the file stem. A GPX or CSV file that cannot be read next to other files is skipped with a
warning layer; the upload fails only when nothing at all could be read.

The upload area (`/w`) lists `.gpx` with the other types.

## 2. Flight audit from a position track

### Inputs

In the **Flight audit** module, the source tab **Dataset track** is enabled when the open dataset
has a timed track; it opens on it automatically. With several tracks a picker lists them
(name · points · duration). The card shows the dataset, track, format, timed points, duration and
start time (GPX). The FlightMD sources (Link, Upload JSON, Sample) are unchanged.

| Parameter (API `flight_audit`) | Form | Default | Notes |
|---|---|---|---|
| `track` | Dataset track | — | Filled by the API from the selected feature (`feature_indices`), else the first timed track of the dataset. `{name, coordinates, times, time_origin_utc, altitude_mode, source_format, feature_index}`; exactly one of `report` / `track` |
| `track_heights` | Advanced → Track heights | `auto` | `auto` follows the file's altitude mode: `absolute` → above sea level (`amsl`), `relativeToGround` → above ground, anything else (or no heights) → above take-off |
| `training_box`, `training_box_name` | Additional checks → Training area | dataset source: the first polygon or closed line named like `training`, `area`, `boundary`, `geofence` — but never one named like an area to stay out of (`no-fly`, `NFZ`, `restricted`, `prohibited`, `danger`, `exclusion`, `keep out`, `audience`, `red zone`); FlightMD sources: off | Polygon (a closed line such as a GPX route is accepted). Send `null` to switch it off; when left out with a dataset, the API picks the named area |
| `max_distance_from_home_m` | Maximum distance from take-off | empty (off) | 1–100,000 m, presets 500 m and 1 km (VLOS guidance, e.g. about 500 m is often cited; not a rule) |
| `ceiling_agl_m`, `calibration`, `use_terrain`, `footprint_buffer_m`, zone rules | as before | | |

Over the API: `POST /api/analyses/ {"kind":"flight_audit","dataset_id":…,"feature_indices":[2],"params":{"max_distance_from_home_m":500}}`.
Without a timed track the request fails with `422 no_tracks`.

### Method (`airbrief_core/analyses/flight_audit.py`, `integrations/position_track.py`)

1. The track becomes the same internal `[lon, lat, alt]` (+ times) structure the FlightMD adapter
   produces (`parse_position_track`); the home point is the first fix; distances along the track
   are cumulative WGS84 geodesic distances.
2. **Heights.** `amsl`: as FlightMD (terrain-calibrated at take-off when a DEM covers it:
   `offset = terrain(home) − alt(home)`, `AGL = alt + offset − terrain`); `above_ground`:
   `AGL = alt` (`recorded_agl`); `above_takeoff`: heights above the first fix. A **2D track**
   (GPX without `<ele>`, CSV without a height column) has no heights to audit:
   `heights_assessed = false`, warning `heights_not_assessed`, the profile reads 0 m (terrain is
   never turned into heights), the height KPI says "Not recorded" and the verdict is at best
   **caution**.
3. **Runs beyond a limit** use one rule for every check: a run of fixes with `excess > 0` starts
   and ends at the linear crossing between neighbouring fixes, `f = −e₀ / (e₁ − e₀)`, applied to
   the distance, the time and the position. With times, `duration = t_end − t_start`.
   - Height: `excess = AGL − min(ceiling, zone ceiling)`; `time_above_limit_s` is the sum.
   - Training area: `excess` = signed horizontal distance to the area's boundary in a local
     azimuthal-equidistant frame (positive outside); a run is a "left the area" segment, its
     `peak_m` the farthest point outside; `time_outside_area_s` is the sum.
   - Distance from take-off: `excess = geodesic distance from home − limit`;
     `max_home_distance_m` is always reported; `time_beyond_distance_s` is the sum.
4. **Coverage (safety parity with the airspace module):** without a data pack, without its
   aerodrome layer, or for fixes outside the pack, aerodrome zones were not checked:
   `assessment_complete = false`, issue `not_assessed`, verdict at best **caution**, and the
   results show an "Airspace not assessed" banner. Never an implied all-clear.
5. Verdict: fail on a height exceedance, a red zone, a training-area exit or a distance
   exceedance; caution on a permission zone, an incomplete airspace assessment or unassessed
   heights (2D track); else pass — evidence about the recorded track only.

### Outputs

- Results: track name and format, KPI tiles **Flight time** (with the time above the limit),
  **Farthest from take-off** (against the limit), **Outside the training area** (time, number of
  exits), tables **Left the training area** and **Beyond N m from take-off** (start time since
  take-off, duration, length flown, farthest), the exceedance table gains a **Duration** column.
- Map layers: `training_box` (outline), `area_exits` (wide amber lines), `home_radius` (dashed
  ring), `distance_exceedances` (dashed amber lines), plus the existing path/exceedances/home.
- The checks not asked for are `null` in the result and add no layers, so earlier results and
  FlightMD audits keep their shape. CSV export: the height profile (unchanged).

## 3. Drone-show upgrades (`airbrief_core/analyses/shows.py`)

| Parameter (API `show`) | Form | Default | Notes |
|---|---|---|---|
| `show_box_margin_m` | Audience → Derived show box margin | 5 m | Used only without a polygon named "Show box" |
| `audience_buffer_rule` | Audience → Audience distance | `fixed` | `fixed` (`audience_buffer_m`), `one_to_one`, `ballistic` |
| `audience_breach_margin_pct` | Safety factor | 0 % | Presets 0/25/50 %: a configurable safety factor, not a regulatory value |
| `min_pad_spacing_m` | Pads and endurance → Minimum pad spacing | 1.5 m | |
| `endurance_s` | Endurance per drone | empty (off) | seconds |
| `min_navigation_altitude_m`, `low_altitude_speed_tolerance_ms` | Advanced → Minimum navigation height, tolerance | off, 0.5 m/s | Flags horizontal motion below the height |
| `origin`, `altitude_reference` | Origin and heights → Track heights, Set the origin (typed, or **Pick the origin on the map**) | from the file | "From the file": the API sets `absolute` + the take-off pad origin for absolute altitudes, else the origin is the mean first position at 0 m. `origin` is `[lon, lat, ground]` or `[lon, lat]`: without the ground altitude (the form's *Origin ground altitude* left empty, the default) absolute heights are measured from the lowest take-off position — also when `absolute` is chosen without an origin |

Method:

- **Show box.** The given polygon, or the east/north rectangle around every track position plus
  the margin, labelled "derived from the tracks" (dashed on the map). A derived box contains every
  position by construction, so the geofence is not checked against it (info `show_box_derived`).
- **Audience distance** (`site.audience_buffer`): fixed = `audience_buffer_m`; 1:1 = the show's
  greatest height above the origin `h`; ballistic = drag-free fall-out `v·√(2h/g)` (g = 9.80665
  m/s², `groundrisk.ballistic_distance_m`) with `h` and the greatest windowed horizontal speed `v`
  measured over the show; then `distance = base × (1 + safety factor %)`. Drones inside the
  audience polygon buffered by that distance are `audience` violations; the box overlapping it is a
  site warning (caution). The `audience_line` layer is the show box widened by the distance: the
  audience belongs outside it — useful when the file has no audience polygon.
- **Pads.** Drones at or below the airborne threshold at their first (last) position stand on a
  take-off (landing) pad; pairs closer horizontally than the spacing are pad conflicts (fail),
  listed with the drones and distance (`pads`, layer `pad_conflicts`, CSV rows `pad_conflict`).
- **Endurance.** A drone airborne for longer than `endurance_s` in total is an `endurance`
  violation from the moment the limit is passed to its last airborne frame (peak = airborne time).
- **Low-height motion.** Horizontal speed above the tolerance while below the minimum navigation
  height is a `low_altitude_motion` violation (drones should climb vertically first).
- The result echoes `altitude_reference`, `origin_source` (given / inferred) and `max_altitude_m`;
  the results show "Origin … (given / the mean first position) · heights relative to the origin /
  above sea level".
- **Check airspace for this show box** (results → Site) runs the Airspace module with
  `geometry` = the show box and `altitude_agl_m` = the show ceiling (taken as height above ground,
  which assumes flat ground at the site; at most 1,500 m, the airspace module's limit) on the same
  dataset and opens it. Results saved before boxes were derived have no box: the button is off
  and says to validate again.

## Limits and caveats

- GPX times are trusted as UTC; tracks without times can be ingested but not audited from a
  dataset (the flight audit needs a timed track) and are not show tracks.
- Joined GPX segments draw a straight line over gaps; distances include it.
- The training area and distance checks are horizontal only. "About 500 m" VLOS guidance is
  commonly cited, not a rule; the user sets the number.
- Ballistic fall-out ignores drag and wind (conservative for drag, not for wind). The safety
  factor is the operator's choice.
- The derived show box is the tracks' rectangle: it is evidence of the space used, not a
  permitted area. The airspace check of a show box treats the ceiling as height above ground.
- Pick the origin on the map uses the plan pin tool (Space places it at the map centre, Escape
  cancels); the pin never becomes a plan draft and an unsaved plan draft is kept. The ground
  altitude is typed, or left empty for the lowest take-off position.

## Manual acceptance check (synthetic demo pack)

1. **GPX sample.** On `/w`, open the sample **Training flight (GPX)**. The workspace opens in
   Flight audit with **Dataset track** selected: *Training flight*, GPX track, 401 timed points,
   6 min 40 s, started 21 Mar 2026 04:30 UTC. Training area shows **Training box**.
2. Type **500** in *Maximum distance from take-off* and press **Audit flight**. Expected:
   verdict **Fail**; heights terrain-calibrated (offset ≈ 12.4 m); max ≈ **129.4 m** above ground,
   **130 m** above take-off; one exceedance from ≈ 133.7 s to 166.2 s (**32.5 s** above 120 m);
   **Left the training area (1)**: from **3:20.0** for **1 min 00 s**, 300 m flown, **100 m** at
   most outside; **Beyond 500 m from take-off (1)**: from t ≈ 198 s for ≈ 62 s, farthest
   **608.3 m**; distance flown **1.4 km**, flight time **6 min 40 s**, nearest aerodrome VOBG
   ≈ 19.7 km; airspace assessed (no "not assessed" banner). Without a DEM the sample's own
   known answers apply: 130 m from 133.33 s to 166.67 s (33.3 s).
3. **No pack.** Upload a GPX far from the pack (e.g. the 4-point track in
   `tests/api/test_tracks_api.py`): the result shows **Airspace not assessed** and verdict
   **Caution**, never Pass.
4. **Show.** Open **50-drone show (CSV)** (no show box polygon), choose **Ballistic** and the
   **50 %** safety factor, **Validate show**: Site shows *Derived from the tracks (+5 m margin)*,
   box **8,100 m²** (90 m × 90 m), audience distance ≈ **37.7 m (Ballistic)** with the formula
   "Ballistic fall-out v·√(2h/g) = 7.80 m/s × √(2 × 50.8 m / 9.80665 m/s²) = 25.1 m (…); × (1 + 50 %)
   safety factor = 37.7 m", pad spacing
   **5.02 m** (50 take-off and 50 landing pads), max height 50.8 m. With *1:1 height* + 50 % the
   distance is 76.2 m. **Check airspace for this show box** opens the Airspace module with a result
   for the box at 120 m.
5. **Origin.** Open **50-drone show (clean)**, switch on *Set the origin*, type 77.592 / 13.0005,
   validate: the results read "Origin 13.00050° N, 77.59200° E (given) · heights relative to the
   origin"; pad spacing 5.02 m, verdict Pass.

Tests: `tests/core/test_gpx.py`, `test_tracks.py`, `test_ingest.py`, `test_flight_audit.py`
(position tracks), `test_shows.py` (derived box, audience rules, pads, endurance, low-height
motion), `test_samples.py`; `tests/api/test_tracks_api.py`; `frontend/e2e/tracks.spec.ts`.
