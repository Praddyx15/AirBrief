# Line of sight: VLOS viewshed from pilot and observers, and C2 radio path with Fresnel clearance

The **Line of sight** module (rail icon: radio tower; analysis kind `line_of_sight`; URL module
`m=line-of-sight`) answers "where can the aircraft be seen, and does the control link have a clear
path?" with geometric evidence:

- the **share of the operation area in visual line of sight** at the planned height, per pilot or
  visual observer and combined (seen by at least one of them), from the terrain and mapped obstacles,
  with Earth curvature and refraction;
- along a **route**: the seen length, the **longest continuous seen stretch** and every hidden stretch;
- the **largest hidden patch** (area hidden from every pilot and observer);
- the **radio line-of-sight coverage** of the ground station (else the pilot);
- **C2 radio path profiles** from the ground station (or the pilot) to a fixed radio target, to every
  route point or to the farthest corner of the area: terrain, obstacle tops, the line between the
  antennas, the first Fresnel zone, the **worst clearance %** and the stretches below the required
  clearance, and the **free-space path loss**;
- a **non-regulatory VLOS distance guidance** ring (EASA ALOS/DLOS estimate, or the commonly cited
  500 m UK CAA figure) and the share of the area beyond it.

It never says "VLOS approved", "link OK" or "safe to fly": the radio figures are labelled
"geometry only — not a link budget or coverage prediction" and the VLOS distances "non-regulatory
guidance". Everything runs offline from the data pack (DEM, optional obstacles); no new data source
or dependency (numpy, scipy, shapely, rasterio already in use).

## Inputs

`LineOfSightParams` (`airbrief_core/models/line_of_sight.py`):

| Field | Default | Meaning |
|---|---|---|
| `geometry` | from the dataset | Operation area (polygon), route (line) or points. From a dataset: the union of the selected features (all when none are selected) **without the observer points** |
| `observers[]` | from the dataset | Up to 10 `{name, role: pilot \| observer \| gcs, lon, lat, height_agl_m}`. `height_agl_m` empty = 1.7 m (pilot, observer: eye height) or 2.0 m (ground station antenna) |
| `target_height_agl_m` | 120 | Aircraft height above the ground under it |
| `grid_m` | 30 | Grid cell size; coarsened in whole metres to keep ≤ 400 × 400 cells (issue `grid_coarsened`) |
| `max_range_m` | none | Cells farther than this from an observer count as not seen by it |
| `k_visible` / `k_radio` | 7/6 / 4/3 | Effective Earth-radius factors for light and radio |
| `radio_check` | true | Radio path profiles and the station's radio line of sight |
| `frequency_mhz` | 2400 | Presets 433, 868, 915, 2400, 5800 MHz or any 30–100,000 MHz |
| `fresnel_clearance_pct` | 60 | Required clearance of the first Fresnel zone |
| `radio_target` | none | `{name, lon, lat, height_agl_m}`, a relay mast or far point (height empty = the target height) |
| `include_obstacles` | true | Data-pack obstacles raised onto the grid and onto the radio paths |
| `vlos_aircraft` | multirotor | `multirotor` or `fixed_wing` (EASA ALOS formula) |
| `characteristic_dimension_m` | none | Largest span; given → EASA estimate, empty → 500 m |
| `ground_visibility_m` | 5000 | For EASA's DLOS |

**Observers from a dataset.** When the request leaves `observers` out, the API
(`api/services/line_of_sight.py`) takes the point features whose `role` property is
`pilot`/`observer`/`gcs`, or whose name matches (whole words, any case):

- ground station: `GCS`, `ground station`, `ground control station`, `base station`;
- observer: `observer`, `spotter`, `VO`;
- pilot: `pilot`, `RPIC`.

A numeric `height_agl_m` property (0–500 m) is kept. Those points are left out of the operation
geometry, even when they are selected. An explicit `observers: []` means "no observer": the pilot
is then **assumed** at the start of the route or the centre of the area (issue `observer_assumed`),
and with only ground stations the pilot is assumed at the first one (`pilot_assumed_at_gcs`).

**In the panel** the observers found in the dataset are listed first. Add an observer (the focus
moves to its name), then type its latitude and longitude, or press **Place on map** and click the
map: the map shows a crosshair, and from the keyboard you can focus the map, pan with the arrow keys
and press Space or Enter to place the point at the map centre. Escape cancels; starting a plan
drawing cancels it too (one click mode at a time). Role, name and height are editable; a fixed radio
target can be placed the same way. The form keeps its values after a run, so moving one observer and
running again takes two clicks; reopening an analysis (History, reload, shared link) puts its own
observers and settings back.

## Method

Implemented in `airbrief_core/analyses/line_of_sight.py`; every step is cited in the result's
`method` text.

### Grid

The operation geometry and the observers are projected to a local azimuthal-equidistant frame
(`LocalFrame`). The grid covers their bounding box plus a margin of max(150 m, 3 cells) with square
cells of `grid_m`; if a side would exceed 400 cells the cell size grows (whole metres) until it fits.
Extents over 60 km are refused (`area_too_large`). Cell heights are the data-pack DEM sampled
bilinearly at the cell centres. The DEM is a **surface model of about 30 m** (roofs and canopy
included). A coarsened grid smooths relief narrower than a cell; the `grid_coarsened` issue says
so. With `include_obstacles`, each data-pack obstacle puts its top (DEM at the obstacle + obstacle
height) on its cell (the tallest wins): a ray crossing that cell meets the top.

### Viewshed (per observer)

Eye = eye height above the gridded terrain at the observer (the reported `ground_m` / `eye_amsl_m`
use the DEM at the point; on a coarse grid the eye stays on the modelled surface instead of being
buried below smoothed valley sides). All heights are lowered by the Earth curvature and
refraction drop

    drop(d) = d² / (2·k·R),   R = 6,371,000 m

with k = 1/(1 − 1/7) = 7/6 for light (refraction coefficient 1/7; GDAL `gdal_viewshed` uses the
curvature coefficient 1 − 1/7 = 0.85714) for pilots and observers, and k = 4/3 (standard radio
atmosphere, ITU-R P.453/P.530) for ground stations. At 10 km with k = 4/3 the drop is
10,000² / (2 · 4/3 · 6,371,000) = **5.89 m**.

A cell is visible when the slope from the eye to the aircraft (terrain + target height − drop)
is at least the steepest slope from the eye to any surface sample before the cell's near edge. The
horizon comes from a **radial sweep**: n ≥ 2π·r_max rays (neighbouring rays at most one cell apart at
the far edge), sampled **every half cell**, keeping the running maximum slope (the "R2"
approximation of Franklin & Ray, 1994, *Higher isn't necessarily better: visibility algorithms and
experiments*). Along a ray the terrain is **interpolated bilinearly** between cell centres (a plane
stays a plane: on a plane rising 3 m per 30 m cell in x and y, sampling the cell steps instead lets a
1.7 m eye see about 3 % of the ground, the interpolated plane ≥ 98 %), and obstacle tops
are taken in the cell a sample falls in, except the observer's own cell (a mast the observer stands
by is not an obstruction). Each cell takes the horizon of its nearest ray and is judged at that
ray's point at the cell's distance. Against exact lines to every cell centre (rough terrain with
towers) it agrees on ≥ 98 % of the cells. It is vectorised with numpy: a 400 × 400 grid takes
about 0.3–0.5 s per observer (test limit 2 s).

Combined visibility = seen by at least one pilot or observer. Ground stations give the radio line
of sight coverage instead (`radio_los_pct`); without a ground station, the pilot's position is used
with k = 4/3.

### Area and route statistics

Area cells are the cells whose centre is inside a polygon, the cells a line crosses and the cells of
points. Visible % = visible area cells / area cells with DEM data. Along a route the line is cut every
half cell; each piece takes the visibility of its midpoint's cell, which gives the seen length, the
longest continuous seen stretch and the hidden stretches (at most 200 listed). The largest hidden
patch is the biggest 8-connected group of area cells hidden from every pilot and observer
(`scipy.ndimage.label`).

### Radio path profile

From the first ground station (else the pilot) to `radio_target`, else to every route vertex (at most
20; longer routes are resampled evenly), else to the farthest corner of the area. The path is sampled
on the DEM itself every min(s, max(5 m, D/400)) with s the smaller of the grid cell and the DEM pixel
(a coarsened grid does not coarsen the profile); obstacles within s/2 of the path add their top at
their position along it (an obstacle at an antenna is its mast and is ignored). With D the
horizontal path length and d1, d2 the distances to both ends:

- the radio line of sight is the straight chord between the antennas lowered by the Earth bulge
  d1·d2 / (2·k·R) with k = 4/3;
- first Fresnel zone radius (ITU-R P.530 form):

      r1 = 17.32 · √(d1·d2 / (f·D))   [m; d1, d2, D in km; f in GHz]

  2.4 GHz, 1 km path, midpoint: 17.32·√(0.5·0.5/(2.4·1)) = **5.59 m**;
- clearance % = (line of sight − surface) / r1 · 100, worst along the path; stretches below
  `fresnel_clearance_pct` are listed; below 0 % the straight line itself is cut;
- free-space path loss over the slant distance (ITU-R P.525):

      FSPL = 20·log10(d_km) + 20·log10(f_MHz) + 32.44 dB

  2.4 GHz over 1 km: 20·log10(2400) + 32.44 = **100.04 dB**.

Geometry only — not a link budget or coverage prediction (no antenna gains, cable losses,
diffraction, multipath, interference or receiver sensitivity).

### VLOS distance guidance (non-regulatory)

With a characteristic dimension CD: EASA, *Guidelines for UAS operations in the open and specific
category* (Ref. to Regulation (EU) 2019/947), Issue 03, 17 July 2025, "Calculation of the VLOS
distance": ALOS = 327·CD + 20 m (rotorcraft/multirotor) or 490·CD + 30 m (fixed-wing), DLOS = 0.3 ×
ground visibility, VLOS = min(ALOS, DLOS). Without CD: the commonly cited UK CAA (CAP 722) figure of
500 m. It is an air-line distance: the map ring is drawn at √(G² − h²) for the target height h over
level ground (500 m at 120 m → 485.4 m), and `beyond_guidance_pct` uses the true slant distance to
the nearest pilot or observer. Shown as guidance, never as a pass/fail.

## Outputs

`LineOfSightResult`: `observers[{name, role, lon, lat, height_agl_m, ground_m, eye_amsl_m, k_factor,
los_kind, visible_pct, visible_area_m2, max_visible_m, assumed}]`, `combined_visible_pct`,
`visible_area_m2`, `hidden_area_m2`, `radio_los_pct`, `largest_hidden_patch`, `route`,
`beyond_guidance_pct`, `grid{grid_m, requested_grid_m, cols, rows, coarsened, area_cells,
nodata_cells, dem_source, dem_resolution_m, obstacles_used}`, `radio{frequency_mhz, k_factor,
clearance_threshold_pct, station_name, station_role, paths[{from_name, to_name, distance_m, slant_m,
fspl_db, worst_clearance_pct, worst_at_m, max_fresnel_radius_m, los_blocked, below_threshold,
segments_below[], profile[{d_m, terrain_m, obstacle_m, los_m, fresnel_lower_m, fresnel_radius_m,
clearance_pct}]}]}`, `vlos_guidance_m`, `vlos_guidance{…, reference}`, `issues`, `method`,
`disclaimer`, `layers`.

Layers (GeoJSON, merged-cell polygons so they stay small and export as-is): `input`, `observers`,
`visible` (one polygon per number of observers that see it) and `hidden` — both over the whole
analysed grid (area + margin) so you can see where moving the area or an observer helps, while the
percentages count the operation area only — `hidden_patch` (inside the area),
`vlos_guidance` (rings), `radio_paths` (with `band`: clear / below / blocked), `route` (seen and
hidden stretches).

Exports: JSON envelope; GeoJSON of every layer (`_layer` property); one CSV whose first column
`table` names each row's table — `observer` (coverage per observer), `radio_path`, `radio_profile`
(every profile sample, numbered by `path`) and `route_hidden`.

### In the workspace

- **Results drawer:** KPI tiles (area in visual line of sight, per-observer share when there are
  several, area in radio line of sight, worst Fresnel clearance with a status icon and text, FSPL,
  largest hidden patch, longest seen stretch for routes, VLOS guidance with the share beyond it),
  the observers table (click → zoom), the radio paths table (click a row or a path on the map →
  its **path profile chart**: terrain, obstacle tops as triangles, the radio line, the Fresnel
  zone's lower edge dashed and the stretches below the clearance shaded; "Show table" gives the
  same samples as a table), hidden route stretches, the method, notes and the disclaimer.
- **Map:** visible area tinted (stronger where two or more observers see it), hidden area hatched
  (colour + diagonal pattern), largest hidden patch outlined dashed, observers as labelled points
  with their share, radio paths solid when clear and dashed when below the clearance or cut,
  route stretches solid (seen) or dashed (hidden), and the dashed VLOS guidance ring. Each layer has
  a legend and a "View as table".
- **History** summary: "120 m AGL · 2 observers · 2.4 GHz". The printed report has its own section.
- **Static demo:** the representative run uses the Bengaluru survey with the assumed pilot.

## Limits and caveats

- The DEM is a ~30 m **surface model** (Copernicus GLO-30; ~90 m GLO-90 in lite packs): roofs and
  canopy block the view, but wires, poles, single trees and anything smaller than a cell are
  missed; the result (method, `surface_model` note, map legend) states the source and resolution.
- Obstacles are point heights that occupy their whole cell, which over-states their shadow width;
  a radio path only meets obstacles within half a DEM pixel of it, although the first Fresnel zone
  is wider (r1 at the middle of a 1 km path at 2.4 GHz: 5.6 m; 20 km at 433 MHz: 59 m).
- Visibility is geometric: eyesight, haze, contrast, the aircraft's size, colour and lighting are not
  modelled; the VLOS guidance covers size and visibility only as published guidance.
- The radio figures are geometry (first Fresnel zone, free-space loss): no link budget, antenna
  pattern, diffraction (ITU-R P.526), ground reflection, interference or cellular coverage.
- A single refraction factor per kind is used; anomalous propagation (ducting, sub-refraction) is
  not modelled. Extents are capped at 60 km and the grid at 400 × 400 cells (coarsened, with an issue).
- VLOS distance rules differ between authorities; the guidance shown is labelled non-regulatory.

## Validation

`tests/core/test_line_of_sight.py` (35 tests):

- curvature drop at 10 km, k = 4/3: 5.886 m; k = 7/6 = 1/(1 − 1/7);
- r1 at 2.4 GHz, 1 km path, midpoint: 5.59 m; FSPL at 2.4 GHz over 1 km: 100.04 dB (≈ 100.0);
- level ground fully visible, and exactly the cells within `max_range_m` when a range is set;
- a 4 m wall 300 m from a 10 m eye hides the ground from 300 m to exactly 300·10/(10 − 4) = 500 m;
  a 30 m wall hides a 120 m aircraft beyond 300·(120 − 1.7)/(30 − 1.7) = 1,254.1 m;
- curvature hides a 0 m target at 10 km (also at 5.8 m) and shows it at 6.0 m;
- adding an observer never lowers combined coverage; a 400 × 400 grid in under 2 s;
- the sweep agrees with exact lines to every cell centre on ≥ 98 % of the cells (rough terrain, 20
  towers, targets 0, 10 and 60 m); a 1.7 m eye on a plane slope sees ≥ 98 % of the ground; a mast
  in the observer's own cell blocks nothing, the same mast one cell away shadows exactly the cells
  behind it; a coarsened grid (≈ 132 m cells) keeps the radio profile on the ≈ 55 m DEM, so a mast
  60 m beside the path is not put on it;
- radio profile with an obstacle against the hand-computed clearance; route visibility, hidden
  stretches, coarsening, out-of-coverage, cancellation, invalid parameters, the synthetic sample.

`tests/api/test_line_of_sight_api.py`: observer roles from names and `role` properties (whole
words), observers and geometry resolved from a dataset, selection, explicit and empty observer
lists, radio target, CSV/GeoJSON/JSON exports, input errors.

`frontend/e2e/line-of-sight.spec.ts`: typed observer on the Bengaluru survey → coverage KPIs,
observers and radio tables, the profile chart and its table, axe; layer tables; a reopened run
restores its observer; Place on map (focus moves to the new row's name, then to the map; Escape
cancels back to the button; Space places the map centre; a click moves the point); missing
coordinates block the run with an error summary.

## Manual acceptance check

Synthetic demo pack (`python -m seed.datapack --synthetic --name demo`), API and web app running.

1. Open the sample **Bengaluru survey** and pick **Line of sight** in the module rail.
2. Press **Add observer**; in "Observer 1: Pilot" type latitude `12.902`, longitude `77.488`.
   Keep the defaults (120 m, 2,400 MHz, 60 %). Press **Check line of sight**.
3. Expected (synthetic pack): grid 54 × 47 cells of 30 m, 3 obstacles raised; **area in visual line
   of sight 97.2 %**; pilot ground 917.35 m, eye 919.05 m AMSL, farthest seen 980.2 m; radio path
   Pilot → Farthest corner: 1,001.5 m (slant 1,008.6 m), **FSPL 100.12 dB**, worst clearance
   255.3 % at 14.9 m, max Fresnel radius 5.59 m (status "Clearance ≥ 60%"); largest hidden patch
   33,300 m² (3.33 ha, 37 cells); VLOS guidance 500 m (ring 485.4 m), 57.1 % of the area beyond it.
4. Click the radio path row: the profile chart shows terrain, the radio line and the dashed Fresnel
   edge; "Show table" lists the samples.
5. Press **Place on map** in the observer card and click elsewhere in the area: the coordinates
   update; run again — the KPIs change and the previous run stays in History.
6. Target height preset **30 m**: area in visual line of sight 95.2 %, hidden patch 4.32 ha.
7. Add a second observer, role **Ground station**, at `12.9015`, `77.4875`, height 6 m; switch on
   **Fixed radio target** "Relay mast" at `12.910`, `77.497`, 30 m; frequency **5800 MHz**: radio
   line of sight 98.0 %, path GCS → Relay mast 1,395.4 m, FSPL 110.6 dB, worst clearance 315.6 % at
   234.2 m, max Fresnel radius 4.25 m.
8. Remove every observer and run: the notes say the pilot was assumed at the centre of the area
   (98.5 % seen).
9. Exports → CSV: rows `observer`, `radio_path`, `radio_profile`.
