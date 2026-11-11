# Terrain-true heights and survey upgrades

Every planning module used to assume a constant height **above ground level (AGL)**. Real
aircraft usually hold a height **above the take-off point** (DJI "ALT", ArduPilot relative
altitude) or a level **above mean sea level (AMSL)**. Over rising ground such a flight comes closer
to the ground than planned (120 m above a take-off at 900 m over ground at 990 m is 30 m AGL);
over a valley it climbs above the free-flight ceiling. A flat-ground survey estimate hides that its
GSD and overlap change with the terrain.

This feature reads the planned height in one of three **height references** — **AGL**, **above
take-off** or **AMSL** — in the airspace check, the survey estimator and the eVTOL corridor, and
reports the height above the ground *along the flight* as evidence: the AGL range, the stretches
below a minimum height and above the ceiling, the GSD range and the worst-case overlap over the
relief. It also upgrades the survey estimator with corridor mapping, a battery reserve, transit
legs, ground-control-point and storage estimates, thermal and multispectral camera presets and a
spraying effort mode shown in local land-area units (hectare, acre, guntha, bigha).

Everything is **evidence**: facts, ranges and margins. AirBrief never says go or no-go, never
generates terrain-following routes, waypoints or mission files (WPML/KMZ, `.plan`, Litchi); survey
and spray lines stay a visual aid (`visual_only: true`). The default reference stays **AGL**
everywhere, so analyses stored before this feature keep their meaning and an AGL analysis returns
exactly the numbers it returned before (regression snapshots in the tests).

## Where to find it

- **Airspace** panel → *Planned height* → **Height reference** (AGL / Above take-off / AMSL), the
  take-off point (typed or **Pick take-off on map**), the take-off ground elevation and the
  **Minimum height above ground** (these three for Above take-off and AMSL only).
- **Survey** panel → *Job* (**Mapping / Spraying**, **Area / Corridor**), *Camera* (RGB, thermal,
  multispectral, saved custom cameras), *Height* (height reference), *Batteries and transit*,
  *Advanced* (GCP rule, image file size).
- **eVTOL** panel → *Corridor* → *Flight* → **Height reference** (AGL / AMSL), cruise level, origin
  ground elevation, minimum height above ground, ceiling above ground.
- **Settings** → *Display* → **Land area unit** and **Bigha size**.

## Height reference

### Inputs

| Parameter (API) | Modules | Default | Meaning |
|---|---|---|---|
| `height_reference` | airspace, survey | `"agl"` | how `altitude_agl_m` is read: `agl`, `takeoff` or `amsl` |
| `cruise_reference` | evtol_corridor | `"agl"` | `agl` (the level clears the highest ground by the cruise height) or `amsl` |
| `altitude_agl_m` | airspace, survey | 120 / 100 m | the planned value in its reference (≤ 1,500 m above ground or take-off; an AMSL level may reach 9,000 m) |
| `cruise_altitude_amsl_m` | evtol_corridor | — | the fixed level, required with `cruise_reference: "amsl"` |
| `takeoff` | airspace, survey | none | `[lon, lat]`; empty: the first vertex of a route, the centroid of an area (a point on its surface when the centroid falls outside), the point itself; the corridor origin for eVTOL |
| `takeoff_elevation_m` / `origin_elevation_m` | airspace, survey / corridor | none | the take-off ground elevation (m AMSL); empty: the DEM at the take-off point (bilinear) |
| `min_agl_m` | airspace, evtol_corridor | 30 m | stretches lower than this above the ground are reported (airspace: above take-off and AMSL plans only) |
| `ceiling_agl_m` | evtol_corridor | none | stretches higher than this above the ground are reported |

### Method

Definitions follow ICAO Annex 2, Chapter 1 (*altitude* is measured from mean sea level, *height*
from a specified datum). With `h` the planned value, `z(x)` the DEM ground elevation (m AMSL)
below the aircraft and `T` the ground elevation at the take-off point
(`airbrief_core/analyses/heights.py`):

| Reference | Flight level | Height above ground |
|---|---|---|
| `agl` | follows the terrain: `AMSL(x) = z(x) + h` | `AGL(x) = h` |
| `takeoff` | `L = T + h` | `AGL(x) = L − z(x)` |
| `amsl` | `L = h` | `AGL(x) = h − z(x)` |

- **Stretches** (`below_min_agl`, `above_ceiling`): runs of consecutive profile samples where the
  height is below the minimum (or above the ceiling). With the signed excess `e` (positive inside
  the run), a run starts and ends at the linear crossing `f = −e₀ / (e₁ − e₀)` between neighbouring
  samples, applied to the distance along the route (the flight audit's exceedance rule, shared via
  `heights.index_runs`); a run touching an end of the route starts or ends there. Each stretch
  reports `start_m`, `end_m`, `length_m`, `peak_agl_m` (the lowest AGL below a minimum, the
  highest above a ceiling) and `limit_m`.
- **Airspace** (`analyses/airspace.py`, step 9). Above take-off and AMSL plans use the AGL of each
  sample: the profile's `agl_m` and `altitude_amsl_m`, each grid cell's `agl_m` and status (a cell is
  *limited* when its ceiling or an obstacle + the vertical clearance is above *its* AGL), obstacle
  clearance (`level − obstacle base − obstacle height`), and the ceiling check with the **highest**
  AGL reached. A route is cut into stretches; an area counts grid cells below the minimum and above
  the ceiling. Below the minimum gives *caution*; ground at or above the flight level is a
  *critical* issue `terrain_above_flight_level` and a *fail*; a plan that could not be related to
  the ground (`heights_assessed: false`) is never *pass*. AGL plans keep every earlier result and
  only add the stretches above the ceiling (the planned value restated against the zone
  ceilings); the minimum height above ground is checked only where the AGL varies (above take-off
  and AMSL), so an AGL result has `min_agl_m: null` and no `below_min_agl` stretch.
- **Survey** (`analyses/survey.py`, step 10). Above take-off and AMSL plans fly one level `L`. The
  layout (GSD, footprint, line spacing `S`, trigger distance `B`) uses the height above the
  take-off ground `H₀` (`h` above take-off; `L − T` for AMSL). With a DEM each battery block's
  ground `z_min..z_max` (30 m grid) gives `H_eff = L − z` from `L − z_max` to `L − z_min` and the
  GSD range `gsd(H_eff) = sw·H_eff/(f·iw)` (GSD is proportional to the height). Over the highest
  ground the footprint shrinks by `H_low/H₀` while `S` and `B` stay, so the **worst overlap** is
  `1 − (1 − p)·H₀/H_low` for the side (`p` = side overlap) and the front (`p` = front overlap) —
  end lap `(G − B)/G` with ground coverage `G = d·H′/f` (Wolf, Dewitt & Wilkinson, *Elements of
  Photogrammetry*). A negative value is a gap between lines. Over the lowest ground the height can
  exceed the height limit (`altitude_above_limit_over_low_ground`). AGL plans assume terrain
  following: the GSD stays the planned one and the result notes the relief.
- **eVTOL corridor** (`analyses/evtol.py`, step 8). `agl` keeps the level of step 6 (highest
  ground and tallest obstacle + clearance). `amsl` flies `cruise_altitude_amsl_m`: each cell's
  obstacle limit becomes `level − ground − clearance`, obstacle clearance and the profile's AGL
  are measured from the level, and the noise footprint uses the lowest height above ground along
  the route. Both report the AGL range and the stretches.
- **Missing terrain.** Without a DEM value the ground is unknown: AGL needs none; above take-off
  takes the ground as flat at the take-off (`AGL = h`); AMSL needs the take-off elevation
  (`AGL = h − T`, flat) or reports no AGL. Each case is an issue: `heights_not_assessed`,
  `terrain_gaps`, `takeoff_elevation_unknown`. An AMSL survey without any ground reference stops
  with `heights_not_assessed` (the layout height would be unknown).
- **Vertical datum.** AMSL heights are compared with the DEM's heights above the geoid (EGM2008 for
  Copernicus). A GNSS altitude above the WGS84 ellipsoid differs by the local geoid undulation and
  a barometric altitude depends on the pressure setting: every AMSL plan carries the note
  `vertical_reference_approximated`. No geoid grid is shipped (an optional EGM96 seed is future
  work).
- **Honest terrain labels** (`heights.dem_info`, from the data-pack manifest): Copernicus GLO-30 /
  GLO-90 are **surface models — roofs and canopy included**, so heights above them are lower than
  above bare ground near buildings and trees (conservative for obstacle clearance, not for
  ceilings); the synthetic pack's DEM is labelled as invented; any other DEM asks the user to check
  what it is. The label is the `dem` field of every airspace, survey and corridor result and a chip
  on every terrain readout. Obstacles whose height OSM does not map get a per-kind default height
  (`terrain.obstacle_height`); they carry `height_source: "default"` and `height_assumed: true` and
  are marked **"Assumed height"** in tables and map tooltips.

### Outputs

Airspace and corridor results add `height_reference` / `cruise_reference`, `min_agl_m`,
`ceiling_agl_m` (corridor), `takeoff {lon, lat, elevation_m, source, elevation_source}`,
`flight_level_amsl_m` (airspace), `agl_min_m`, `agl_max_m`, `below_min_agl[]`, `above_ceiling[]`,
`cells_below_min_agl`, `cells_above_ceiling` (airspace grid), `heights_assessed`, `dem`, a `heights`
layer (the stretches as LineStrings, `kind` = `below_min_agl` | `above_ceiling`) and a `takeoff`
layer. Grid cells add `agl_m`; obstacles add `height_source` and `height_assumed`. The summary
sentence states the level, the AGL range and the stretches, e.g. *"Flight level 950 m AMSL: from
28 m to 30 m above ground. Below the 30 m minimum above ground for 874 m of the route (1
stretch)."* Survey results add `takeoff`, `flight_level_amsl_m`, `terrain_min_m`, `terrain_max_m`,
`relief_m`, `agl_min_m`, `agl_max_m`, `gsd_min_cm`, `gsd_max_cm`, `worst_side_overlap_pct`,
`worst_front_overlap_pct`, `heights_note`, and per block the same ranges. Proto
(`contracts/proto/uaop/airspace/v1/airspace.proto`): `ALTITUDE_REFERENCE_ABOVE_TAKEOFF = 4`,
`ALTITUDE_REFERENCE_AMSL = 5` and the matching optional fields (additive).

## Survey upgrades

All of this is **effort evidence** for planning a job; the lines are a visual aid only.

- **Corridor mapping** (`coverage: "corridor"`, `corridor_width_m`, step 11). The centreline is
  buffered by half the width (flat ends) in a local metric frame; `n = ceil(width / S)` lines are
  flown along it at offsets `(i − (n − 1)/2)·S` (shapely `offset_curve`), alternating direction.
  The corridor is cut into `k` equal lengths, the smallest `k` whose every piece fits one usable
  battery with the connector and turn flown into it from the previous piece (and one tank when
  spraying, its share of the mix by line length); each piece is a block. The API takes the selected line
  features as the centreline.
- **Battery reserve and transit legs** (`battery_reserve_pct`, `transit_legs`, `transit_speed_ms`,
  step 12). Usable time = battery × (1 − reserve). With transit legs each block also flies straight
  from the take-off point (given, the height-reference take-off, or the area centroid / corridor
  start) to its first line and back from its last, at the transit speed (default: the survey
  speed); whole lines are grouped so legs and lines fit a usable battery. Climbs and descents are
  not counted. A leg that alone uses up a battery is flagged (`transit_exceeds_battery`).
- **Ground control points** (`gcp_min` 5, `gcp_ha_per_extra` 10 ha, `gcp_corridor_spacing_m`
  300 m, step 13): `gcp_min + floor(area_ha / gcp_ha_per_extra)` for an area,
  `max(gcp_min, floor(centreline / spacing) + 1)` along a corridor. This is a configurable **rule of
  thumb, not a standard** (Pix4D recommends at least 5 well-distributed GCPs, 5–10 usually being
  enough; more per area or along a corridor so every part of the block is near one).
- **Storage** (`photo_mb`, step 13): photos × images per capture × MB per image (the parameter,
  else the camera preset's typical file size).
- **Sensors** (`CameraSpec.kind`, `images_per_capture`, `photo_mb`, `reference`; step 15). An
  uncooled microbolometer's sensor size is its pixel pitch × resolution; a multispectral band
  camera's pixel pitch follows from the published GSD formula (`GSD = H/k` cm/px ⇒ pitch/focal =
  1/(100·k)) and each capture writes one image per band. Presets (manufacturer spec sheets; file
  sizes are typical values):

  | Key | Camera | Resolution | Pitch | Focal length | GSD at 100 m | Images / capture |
  |---|---|---|---|---|---|---|
  | `h20t_thermal` | DJI Zenmuse H20T thermal | 640 × 512 | 12 µm | 13.5 mm (DFOV 40.6°) | 8.89 cm | 1 |
  | `m30t_thermal` | DJI M30T / Mavic 3T thermal | 640 × 512 | 12 µm | 9.1 mm (40 mm equiv.) | 13.19 cm | 1 |
  | `xt2_640_13` | DJI Zenmuse XT2 640, 13 mm | 640 × 512 | 17 µm | 13 mm | 13.08 cm | 1 |
  | `p4m` | DJI P4 Multispectral band camera | 1600 × 1300 | 3.04 µm | 5.74 mm | 5.29 cm (H/18.9) | 6 |
  | `m3m` | DJI Mavic 3 Multispectral band camera | 2592 × 1944 | 2.01 µm | 4.34 mm (25 mm equiv.) | 4.63 cm (H/21.6) | 5 |

  Custom cameras (with a sensor type, images per capture and file size) are remembered in this
  browser's localStorage when an estimate runs with them (at most 8, guarded: blocked storage only
  means nothing is remembered) and offered as **Saved in this browser**; **Forget this camera**
  removes one.
- **Spraying effort mode** (`mode: "spraying"`, `spray {swath_m 5, rate_l_per_ha 20, tank_l 10,
  flow_l_per_min 4, speed_ms 5, turn_seconds 5, refill_minutes 3, height_m 3}`, step 14). Spray
  lines are laid at the swath width. The pump output needed is `Q = rate·w·v·60 / 10 000` L/min (the
  sprayer calibration formula L/min = L/ha × km/h × m / 600); above the pump maximum the speed is
  lowered to `v = Q_max·10 000 / (rate·w·60)` (`spray_flow_limited`). Litres = area × rate
  (rounded to 0.01 L); tank loads = ceil(litres / tank), a load counting as full within 0.1 % of
  the tank (the per-line shares of the mix carry floating-point and geodesic noise). Sorties follow the serpentine path with **breakpoint resume**, as
  agricultural spraying drones fly: a sortie takes whole lines while the usable battery (with the
  return leg) and the tank allow, then sprays the next line until the tank or the battery runs out,
  and the next sortie resumes there (a line's time per metre = its time ÷ its spray length). The
  sorties' areas tile the field: a cut line is split at the break point. Refills = sorties − 1,
  each stop lasting max(refill, battery swap). Theoretical field capacity
  `C = w·v·3 600 / 10 000` ha/h (ASABE EP496: speed × width / 10 in km/h and m); effective = area /
  total time; field efficiency = effective / theoretical. A height outside 2–5 m above the crop is
  noted (`spray_height_outside_band`). Spraying is planned above the crop (`height_reference` must
  be `agl`). Outputs: `spray {litres, tank_loads, sorties, refills, spray_minutes, flow_l_per_min,
  speed_ms, flow_limited, theoretical_ha_per_hour, effective_ha_per_hour, field_efficiency_pct,
  method}` and per sortie its share of the mix (`blocks[].litres`).
- **Local area units** (`lib/format.ts` `localAreaParts`, `lib/area-units.ts`, Settings). Hectare;
  acre = 4,046.8564224 m²; guntha = 1/40 acre = 121 square yards ≈ 101.17 m²; bigha = a
  configurable size, because it differs by state and district (presets as commonly quoted, each
  from the customary units that define it: West Bengal / Assam 20 katha × 720 ft² = 14,400 ft²
  ≈ 1,337.8 m²; Gujarat 16 guntha × 1,089 ft² = 17,424 ft² ≈ 1,618.7 m² (0.4 acre); Rajasthan
  pucca bigha 3,025 square yards = 27,225 ft² ≈ 2,529.3 m²; check the local land record).
  Spraying results show the area, the rate per unit (L/ha × m² per unit / 10,000) and the work
  rate per hour (ha/h × 10,000 / m² per unit) in the chosen unit (switch with **Area unit** in the
  results); mapping results add the area in the chosen unit when it is not hectares.

## In the interface

- **Airspace results**: KPI tiles *Height above ground* (range), *Below the minimum* (route length
  or grid cells), *Above the ceiling*; the section *Heights above ground* with the take-off point and
  the flight level, the **height profile** (terrain filled, flight line, free-flight ceiling line
  = terrain + ceiling, minimum line = terrain + minimum, the stretches above the ceiling **red and
  hatched**, below the minimum **amber and dotted**, each labelled; *Show table* lists every sample
  with a *Status* column), the stretches table (click a row to zoom to it) and the terrain-model
  chip. The map adds the *Height stretches* (red solid / amber dashed) and *Take-off* layers.
- **Survey results**: *"GSD 2.71–2.77 cm/px (2 m relief); worst side overlap 70 %."* above the
  tiles, tiles for the height above ground, worst overlap, transit, corridor, GCPs and storage, the
  blocks table with transit and GSD columns, the method and the terrain caveat. Spraying shows the
  spray mix, tank loads, sorties, spraying rate, pump output, times and the sorties table.
- **eVTOL corridor results**: cruise level, AGL range tile, the height profile and stretches.
- **History** names the reference: *"950 m AMSL · grid"*, *"120 m above take-off"*, *"Spraying ·
  20 L/ha · 5 m swath"*, *"… · corridor 100 m wide · transit legs"*.
- Reopened analyses restore every new field (`useFormFromAnalysis`).

## Limits and caveats

- **DSM, not bare earth.** With Copernicus GLO-30 heights are above roofs and canopy: AGL is
  under-estimated near buildings and trees (conservative for clearance, not for ceilings).
- **Vertical references** are approximated (EGM2008 geoid vs ellipsoid or barometric altitude);
  a typed take-off elevation corrects the take-off ground, not the datum.
- **Resolution.** Profiles sample the DEM every `profile_spacing_m` (bilinear); survey blocks use a
  30 m grid; areas use the terrain summary and grid cells. Narrow ridges between samples can be
  missed.
- **AGL mode is terrain following**: no range is computed; plan above take-off to see the effect
  of the relief on the GSD and overlap.
- Transit legs are straight lines at constant speed (no climb, descent or wind). GCP counts are a
  rule of thumb. File sizes are typical values. Spraying has no drift, buffer or label-rate checks.
- **Size caps** (`area_too_large`): an area needs at most 5,000 battery blocks (spraying: sorties)
  and a corridor at most 20,000 line pieces (lines × battery pieces); split larger jobs.
- Nothing here is a flight plan or an approval.

## Manual acceptance check

Run the API with the synthetic demo pack and open the web app.

1. **Climbing line (3D)** → **Airspace** → Height reference **AMSL**, Flight level **950 m**, minimum
   30 m → *Check airspace*. Expect take-off at the first point (ground 922 m AMSL), *Height above
   ground* **28–30 m**, *Below the minimum* **874 m** (1 stretch, lowest 28 m), the summary
   *"Flight level 950 m AMSL: from 28 m to 30 m above ground. Below the 30 m minimum above ground for
   874 m of the route (1 stretch)."*, verdict Caution, the amber dotted stretch in the profile and on
   the map, and the *Synthetic terrain* chip. Reload: the form shows AMSL and 950 m.
2. Same line, Height reference **AGL** 120 m: the result is the one of earlier versions (no
   take-off, *Height above ground* not shown as a range).
3. **Bengaluru survey** → **Survey** → Height reference **Above take-off**, 100 m → *Estimate
   survey*. Expect *"GSD 2.71–2.77 cm/px (2 m relief); worst side overlap 70 %."*, take-off at the
   centre of the area (ground ≈ 918 m).
4. **Climbing line (3D)** → **Survey** → Coverage **Corridor**, width 100 m → 3 lines along the
   centreline, 153 photos, 1 battery block, 5 ground control points, about 1.2 GB.
5. **Bengaluru survey** → **Survey** → Job **Spraying** (defaults) → 118.86 ha, spray mix
   **2,377.2 L**, **238 tank loads, 238 sorties** (237 stops), field capacity 9 ha/h, effective
   ≈ 3.57 ha/h (≈ 40 % efficiency); switch *Area unit* to **Acre**: 293.71 ac.
6. **Bengaluru survey** → **Survey** → camera *DJI Zenmuse H20T thermal*, 60 m → GSD **5.33
   cm/px**; *DJI P4 Multispectral*, 60 m → GSD 3.17 cm/px and storage counting 6 images per capture.
7. **Bengaluru survey** → **Survey** → Battery reserve 20 %, *Count transit legs* → usable 20 min per
   battery, a *Transit* tile (≈ 3.9 km) and the *Transit in / out* column.
8. **Bengaluru eVTOL** → **eVTOL** → *Corridor* → Height reference **AMSL**, cruise level
   **1,000 m**, minimum **100 m** → *Route corridor* (Whitefield → Electronic City): *Height above
   ground* **91–106 m**, below 100 m for **5.2 km** (from 11.7 km to the end), the stretch in the
   profile and on the map.
9. **Settings** → *Land area unit* **Bigha**, *Bigha size* **Gujarat** → reload: both kept.

## Tests

- `tests/core/test_heights.py` — runs and crossings, the three references, take-off defaults and
  overrides, a synthetic ramp DEM (take-off 900 m, 120 m above take-off, ground rising to 990 m →
  30 m AGL and a below-minimum stretch starting at 8/9 of the route for a 40 m minimum), a valley
  (ceiling exceedance from L/3 + L/72 to 2L/3 − L/72, peak 175 m), AMSL equal to the same level above
  take-off, ground above the level (*fail*), areas (grid cells), obstacle clearance at a level, no
  DEM (`heights_not_assessed`), AGL unchanged against a snapshot computed by the code before this
  feature, DEM labels from the manifest, and the eVTOL corridor in both references (AGL against its
  own snapshot).
- `tests/core/test_survey_upgrades.py` — the survey unchanged without the new options (snapshot),
  GSD range = camera math at the heights over the ground, the worst-overlap formula, AMSL and no-DEM
  cases, corridor known answers, battery reserve and transit legs (hand-computed), GCP and storage,
  the spray-flow formula, a spraying known answer, breakpoint resume by tank (30 sorties = 30 tank
  loads) and by battery (ceil(4114 / 120) = 35), and the sensor presets against their published
  specs (H20T DFOV, P4M H/18.9, M3M H/21.6).
- `tests/api/test_heights_api.py` — corridor input resolution (lines only; 422 without a line; a
  line crossing a polygon stays whole), take-off defaults through the API, spraying, the 422s of
  the new parameters, an AMSL survey without a ground reference (`heights_not_assessed`), and the
  exact payloads the web panels send.
- `frontend/e2e/heights.spec.ts` — the flows of the acceptance check.
