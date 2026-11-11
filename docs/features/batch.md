# One result per feature, and the panel parameters that were API-only

## One result per feature

Airspace, ground risk, survey, environment and line of sight normally take the selected
features together, as one operation (their union). With **One result per feature** each feature
is assessed on its own, with the same settings, so 200 farm plots or 80 towers get 200 or 80
results instead of one.

It is evidence per feature: zones, bands and ceilings, the iGRC density evidence, survey or
spraying effort, interference proximity, visibility. It is never a go/no-go and it produces no
missions, orbits or waypoints.

### Inputs

In a module panel, open a dataset, select the features (or none, for all of them) and switch on
**One result per feature (N selected)**. **Name each result by** picks the attribute used as the
key in the table, on the map and in the exports (default: the feature's name, else its number).

Through the API, any of the five kinds accepts

```json
"batch": {"mode": "per_feature", "key_property": "asset_id"}
```

With a `dataset_id` the API fills `batch.features` with the selected features (all when none
are selected), each with its properties; line of sight leaves out the points named like a
pilot, observer or ground station, which stay its observers. Without a dataset, send the
features yourself as a GeoJSON FeatureCollection in `batch.features`. `geometry` becomes the
extent of the features and only chooses the data pack. The analysis responses (create, list, get,
cancel) show `batch.feature_count` instead of the features; the JSON export and the brief bundle
keep them.

Limits: 1,000 features (line of sight 500), and the request caps of every analysis (100,000
positions per GeoJSON object sent, 32 levels of nesting). A kind without per-feature results
answers 422 `invalid_params`; too many features 422 `too_many_features`.

### Method

`airbrief_core/batch.py` runs the module once per feature. Only the geometry changes between
items (a survey keeps the polygonal part, or the line of a corridor survey). The items share the
parameter objects, so what a module prepares once (the zone polygons in the zone cache, the
aerodrome index, the DEM and population readers) serves every item: the data-pack zones are
built once per batch.

A feature that cannot be assessed (no geometry, a point in a survey, a module error) keeps its
error on its row; the others go on. Cancelling stops at the next checkpoint. No feature is
started after 600 s: the rest are marked **Not run (time budget)**, so a batch always ends
inside the production job limit (`JOB_TIMEOUT_S`, 900 s). Keep `JOB_TIMEOUT_S` above 600 s on
servers that run large batches.

Each item gets a verdict from its module's own result:

| Kind | Verdicts (most severe first) | From |
|---|---|---|
| Airspace | Red zone · Permission required · Limited · Not assessed · Free | a red zone touched; permission required; ceiling below the plan, an obstacle conflict, or heights outside the limits; a data gap (no pack, beyond it, no DEM); otherwise free |
| Ground risk | Beyond Table 2 · iGRC 10 … iGRC 1 | the Table 2 lookup of the item (tones as the ground-risk tile: 7+ red, 5–6 amber) |
| Survey | 1 battery · 2 batteries · 3 batteries · 4 or more batteries | the battery count of the estimate (no judgement, one colour each) |
| Environment | Power line or mast inside · within the search distance · none within it · not checked | interference proximity (sun and wind barely change between nearby features) |
| Line of sight | Not visible · Partly hidden · Fully visible · Not assessed | hidden area (or hidden route stretches) of the item |

Each item keeps the kind's figures (`BATCH_FIELDS`), e.g. airspace: the module verdict, zone,
band (aerodrome and ring, e.g. `VOBG 8–12 km`), free-flight ceiling, obstacle conflicts,
nearest aerodrome and distance; survey: area, photos, minutes, batteries, GSD, line km, spray
litres and sorties.

### Outputs

* The result (`BatchResult`): `items` (index, key, name, status, verdict, values, issue codes,
  error, the feature's attributes; `index` is the dataset feature number when the API read the
  features from a dataset, else (or if those numbers repeat) the position in `batch.features`, so
  it is unique per item), `counts` per verdict with their label and tone, `summary`,
  `method`, the notes of every item merged (one line per code with the number of features), the
  disclaimer and a `features` layer with every feature tagged with its verdict. The layer holds
  at most 200,000 vertices; features past that are drawn as a point (`points_only`).
* Workspace: the summary bar ("Free: 62", "Limited: 14", …) filters the table; **Find a feature**
  searches keys and names; the status line says how many rows are shown; the table sorts by any
  column (virtualised beyond 200 rows); a row click zooms to the feature; the map colours each
  feature by its result, with a legend and a tooltip.
* Exports: CSV **One row per feature, with its attributes** (an attribute named like a result
  column becomes `attr.<name>`; formula-like cells and column names are neutralised); GeoJSON and
  KML with the verdict properties and the attributes (KML coloured by tone).
* Report and brief: the counts as key numbers and the feature table (most severe first). The
  bundle holds the full CSV (`tables/NN_<kind>_features.csv`).

### Measured run times (synthetic pack, 1,000 plots of about 330 m)

| Kind | 1,000 features |
|---|---|
| Airspace | 34 s |
| Ground risk (SORA 2.5 volume) | 47 s |
| Survey | 36 s |
| Environment | 73 s |
| Line of sight, observer up to 30 km away (400 × 400 viewshed each) | 0.75 s per feature: 500 in about 6 min |

### Limits and caveats

* The verdict summarises one module's evidence for one feature; it is not a decision, and the
  counts are not a fleet-wide go/no-go.
* Per feature, a survey or airspace check of a very large polygon can take seconds; the time
  budget then leaves the last features **Not run**: select them and run them as another batch.
* Results stored before this feature have no `batch`; nothing changes for them.

### Manual check

1. Open the sample **Farm plots (Shapefile, UTM 43N)**, open **Airspace**, switch on **One result
   per feature (all 3)**, **Check airspace**.
2. The drawer shows a row for Plot A, Plot B and Plot C; the map colours the three plots.
3. **Survey** the same way: Plot A 1.998 ha, Plot B 2.248 ha, Plot C 1.498 ha, 1 battery each (the
   true areas, about 0.11 % below the sample's grid areas of 20,000, 22,500 and 15,000 m²).
4. Export CSV: one row per plot with `crop` Ragi / Tomato / Mulberry and `owner`.

## Parameters that were API-only

The panels now set every analysis parameter except `user_zones` (zones from layers do that job in
the web app; zone rules and zone datasets have their own controls, see [`zones.md`](zones.md)) and
the take-off elevation of the survey (set through the shared take-off fields):

| Panel | Added (in Advanced) | Default |
|---|---|---|
| Airspace | Grid cell limit (`max_grid_cells`) | 20,000 |
| Ground risk | Cell limit (`max_cells`, both methods) | 20,000 |
| eVTOL siting | Obstacle radius (`obstacle_radius_m`), Cell limit (`max_cells`) | 500 m, 20,000 |
| eVTOL corridor | Route cost weights (`weights`: ground risk, airspace, obstacles, 0–5 each), Grid resolution (`h3_resolution`), Cell limit (`max_cells`), Study area margin (`study_margin_km`), Noise reference distance (`noise_ref_distance_m`), Profile spacing (`profile_spacing_m`) | 0.5 / 0.3 / 0.2, 8, 60,000, 3 km, 100 m, 100 m |
| Drone show | Dynamics window (`dynamics_window_s`), Airborne threshold (`airborne_threshold_m`), Events listed (`max_events`) | 1 s, 1 m, 200 |
| Flight audit | Data-pack zones (`include_datapack_zones`) | on |

Settings that weaken evidence are flagged by the brief (`non_default_parameters`): a cell limit
below its default (airspace `max_grid_cells`, ground risk `max_cells`: a grid is cut or coarsened,
so a zone or a density peak can be missed), a show dynamics window above 1 s or airborne
threshold above 1 m, and, as before, data-pack zones switched off.

The corridor's edge cost is distance × (1 + w_gr·density + w_air·airspace + w_obs·obstacles), so
all three weights at 0 give the shortest path; red zones stay impassable while **Avoid red zones**
is on.

## Terrain-model chip

The surface-model caveat (`DemCaveat`) now also appears under the line-of-sight key numbers and
under the flight audit's height profile (when terrain was used): both results carry the data
pack's `dem` description. Ground risk reads no terrain, so it has no chip.
