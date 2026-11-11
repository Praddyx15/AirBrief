# Ground risk: SORA 2.5 operational volume, adjacent area and assemblies

The **Ground risk** module (analysis kind `ground_risk`) produces **evidence for SORA 2.5 Step 2
and Step 8**: it builds the operational volume of an operation (flight geography, contingency
volume, ground risk buffer), finds the highest population density in that footprint on a square
grid and the Table 2 cell it falls in, averages the population of the adjacent area, and lists
the outdoor assemblies of people within 1 km.

Every result carries the label **"SORA Step 2 / Step 8 evidence — not a GRC, ARC or SAIL"**.
AirBrief never computes the final ground risk class, mitigations, air risk class, SAIL or OSOs:
those decisions belong to the operator, the authority or a compliance engine (UAOP).

The same release fixes a safety gap in the **Airspace** module: an area no data pack covers is
now reported as *not assessed* (caution), never as a pass. See [Airspace: not assessed](#airspace-not-assessed-and-interim-dgca-wording).

## Inputs

The flight geography is the selected features of the open dataset (all features when none is
selected), or a drawn/pinned operation. A point gives a circular volume, a route a corridor, an
area a buffered area.

| Parameter (API) | Form | Default | Notes |
|---|---|---|---|
| `max_dimension_m`, `max_speed_ms`, `takeoff_mass_kg` | Aircraft class + fields | Mapping quadcopter (0.6 m, 18 m/s, 4 kg) | The class also fills the contingency terms |
| `altitude_agl_m` | Height above ground | 120 m | |
| `controlled_ground_area` | Controlled ground area | off | Table 2 row 0 |
| `buffer_rule` | Advanced → Ground risk buffer | `one_to_one` | `one_to_one` or `ballistic` |
| `density_statistic` | Advanced → Density statistic | `max` | `p95` raises an issue: SORA uses the highest density |
| `volume` | Operational volume switch | **on** in the UI; absent in the API = the earlier footprint method | `OperationalVolumeParams` below |
| `volume.fg_height_m` | Flight geography height | the height above ground | H_FG |
| `volume.aircraft` | Contingency manoeuvre | from the class | `multirotor` (stop) or `fixed_wing` (turn) |
| `volume.gnss_error_m` | GNSS position error | 3 m | S_GNSS |
| `volume.position_error_m` | Position-keeping error | 1 m | S_pos (flight technical error) |
| `volume.map_error_m` | Map error | 1 m | S_map |
| `volume.reaction_time_s` | Reaction time | 1 s (heavy-lift 1.5 s, eVTOL 2 s) | t_R |
| `volume.pitch_angle_deg` | Pitch angle (multirotor) | 35° (sub-250 g, mapping quad), 25° heavy-lift | θ, 5–75° |
| `volume.bank_angle_deg` | Bank angle (fixed-wing) | 30° | φ, 5–75° |
| `volume.altimetry_error_m` | Altimetry error | 4 m | H_alt |
| `volume.climb_angle_deg` | Climb angle during the reaction time | 45° | γ |
| `adjacent_area` | Adjacent area (Step 8) | on | |
| `grid_m` | Density grid cell | auto (Table 4) | 25–10,000 m; needs `volume` |
| `assemblies[]` | Assemblies and events | none | `{name, geometry (Point/Polygon), expected_people, start_utc, end_utc, note}`, at most 100; needs `volume` |

The per-class terms are illustrative defaults (`UAS_PRESETS[*].volume` in
`airbrief_core/models/presets.py`, exposed by `GET /api/meta/` as `uas_presets.<id>.volume`).
Replace them with the aircraft's and the operation's data. A request with `assemblies` or
`grid_m` but no `volume` is rejected with 422 `invalid_params`.

In the form, **Assemblies and events** takes a name, a place (latitude, longitude in decimal
degrees with an optional radius, typed or set with **Place on map** and one map click, or a point or
polygon of the dataset), the expected people and an optional time window in local time (stored in
UTC). Enter in a field adds the entry (it never runs the analysis). Everything is keyboard-operable
(typed coordinates, or Space on the focused map for its centre); nothing needs dragging. A dataset
feature chosen as an assembly while it is also part of the flight geography (nothing selected, or
it is selected) is flagged in the form, since it would sit inside the volume.

In the API, assembly coordinates must be longitude −180…180 and latitude −90…90 (otherwise 422);
`start_utc`/`end_utc` without an offset are read as UTC, and times with an offset are converted.

## Method

Code: `airbrief_core/analyses/groundrisk.py` (`contingency_volume`, `build_operational_volume`,
`run`); models in `airbrief_core/models/ground_risk_volume.py`. Sources: JARUS SORA 2.5 Main Body
(§2.2 semantic model, §4.2 Step #2 with Tables 2–4, §4.8 Step #8), and the term structure of the
German LBA (Luftfahrt-Bundesamt) operational-volume method for the contingency volume. `g` =
9.80665 m/s², `v` = the maximum speed, `c` = the maximum characteristic dimension.

1. **Flight geography (FG)**: the input geometry at height `H_FG` (§2.2.2).
2. **Contingency volume (CV)** (§2.2.3; §4.2.4 "iGRC footprint" (b): navigation accuracy, flight
   technical error, path-definition error, latencies):
   - `S_CV = S_GNSS + S_pos + S_map + S_R + S_CM`, `S_R = v·t_R`
   - multirotor: `S_CM = v² / (2·g·tan θ)` (stopping at pitch θ); fixed-wing:
     `S_CM = v² / (g·tan φ)` (turn radius at bank φ)
   - `H_CV = H_FG + H_alt + H_R + H_CM`, `H_R = v·t_R·sin γ` (climb during the reaction time; 45°
     gives 0.707), `H_CM = ½·v²/g` (all kinetic energy converted into height — the conservative
     convention, used for both aircraft types).
3. **Ground risk buffer (GRB)** (§4.2.4 "Ground risk buffer" (a)): (i) the 1-to-1 principle
   `S_GRB = H_CV + c/2`, or (ii) a value the applicant substantiates (Annex E §E.4, criterion 3),
   here the LBA method's ballistic `S_GRB = v·√(2·H_CV/g) + c/2` (drag-free fall from H_CV).
4. **Geometry**: CV = FG buffered by S_CV, footprint = FG buffered by S_CV + S_GRB, in a local
   azimuthal-equidistant frame centred on the operation. FG ⊂ CV ⊂ footprint.
5. **iGRC density** (§4.2.3 (c), "the highest population density within the iGRC footprint"): the
   population raster is averaged on a square grid of `grid_m` cells laid out in that frame; every
   cell that overlaps the footprint counts, each averaged from up to 8 × 8 nearest-pixel samples,
   and the highest cell mean is the density (the 95th percentile is kept as a supplementary
   value). `grid_m` defaults to **SORA 2.5 Main Body Table 4** by `H_FG`: 200 m up to 500 ft
   (152.4 m), 400 m up to 1,000 ft, 1 km up to 2,500 ft, 2 km up to 5,000 ft, 4 km up to 10,000 ft,
   5 km up to 20,000 ft, 10 km above. When the footprint needs more than `max_cells` cells the
   cell is doubled until it fits (issue `grid_coarsened`).
6. **Table 2** (§4.2.3 (d)): column = the leftmost one whose dimension and speed both cover the
   aircraft (1 m/25 m/s, 3 m/35 m/s, 8 m/75 m/s, **20 m/120 m/s**, 40 m/200 m/s); row = the density
   band (controlled ground area, < 5, < 50, < 500, < 5,000, < 50,000, > 50,000 people/km²). Cells
   above 50,000 people/km² for the 8, 20 and 40 m columns are "not part of SORA"; a UA of ≤ 250 g and
   ≤ 25 m/s has iGRC 1 whatever the density. The result includes the whole table with the cell
   that applies.
7. **Adjacent area** (§2.2.5, §4.8.3 (b)(i)–(ii)): the outer limit lies
   `clamp(v·180 s, 5 km, 35 km)` from the operational volume (the CV's outer limit); the inner
   limit is the GRB edge. The average density = people integrated over that ring on a 500 m grid ÷
   the covered ring area. Parts outside the data pack are left out of both and reported as
   `partial_coverage`. When the GRB is wider than the adjacent distance, the assessment "is not
   required" (§4.8, Figure 7; issue `adjacent_not_required`). A UA ≤ 250 g gets low containment with
   no adjacent-area limits (§4.8.3 (a); issue `adjacent_small_uas`, figures kept as context).
8. **Assemblies** (§4.8.3 (b)(iii)): venues of the pack's `assemblies` layer and the user's entries
   within 1 km of the operational volume, nearest first, with their distance from the CV (0 when
   inside), banded **< 40k / 40k–400k / > 400k** people (Tables 8–13 columns; both band limits
   inclusive in the middle band) or *capacity unknown*. An assembly inside the footprint raises
   `assembly_in_footprint` (population maps do not include event crowds; Table 3 describes
   assemblies as > 50,000 people/km²). A GRB wider than 1 km makes the list context only (§4.8.4 (c);
   issue `assemblies_not_applicable`).

The result's `method` text repeats every formula with its numbers, and every term is returned in
`volume.terms[]` with its formula and source.

Without `volume` (older clients and stored analyses) the earlier footprint method runs unchanged:
geometry + 1:1 (height) or ballistic buffer, density sampled at H3 cell centroids.

## Outputs

`GroundRiskResult` keeps every earlier field (so stored analyses still render) and adds:

- `evidence_label`, `volume{aircraft, speed_ms, fg_height_m, s_cv_m, h_cv_m, grb_m, grb_rule,
  adjacent_distance_km, adjacent_outer_km, grid_m, grid_source, terms[{name, label, axis, value_m,
  formula, source}], reference}`;
- `density_grid{grid_m, origin_lon, origin_lat, samples_per_cell, cells[], hotspots[], nodata_cells,
  coarsened}` (cells are `{id, ix, iy, lon, lat, density_per_km2, band}`);
- `igrc_table{reference, columns[], rows[], row_index, column_index}`;
- `adjacent_mean_density_per_km2`, `adjacent_people`, `adjacent_area_km2`, `adjacent_coverage`;
- `assemblies[{name, source (datapack|user), kind, capacity, band, band_label, distance_m, lon, lat,
  start_utc, end_utc, note}]`;
- layers `flight_geography`, `contingency_volume`, `ground_risk_buffer` (the ring between CV and
  footprint), `adjacent_area` (the ring), `assemblies`, plus `input` and `footprint`.

**Exports** (`GET /api/analyses/{id}/export/`): GeoJSON adds the averaged squares as `density` and
the top ten as `hotspots` (ranked), corners inverse-projected from the local frame. The CSV of a
volume result holds several tables, each row naming it in the first column: `volume` (one row of
sizes and Step 8 figures), `volume_term`, `grid_cell` and `assembly`. Footprint-method results
keep their single H3 `cells` table. The UAOP protobuf contract adds `GroundRiskEvidence.
operational_volume` (`OperationalVolume` with the sizes, `adjacent_coverage`, `grb_rule` and
`aircraft`; `VolumeTerm`; `AssemblyEvidence`), additive only.

**UI** (`frontend/components/modules/ground-risk/`): an *Operational volume* section with a live
preview of the sizes, the terms under *Contingency volume terms* (each with its formula and source),
the adjacent-area switch and the grid size; *Assemblies and events*. Results: the evidence label,
KPI tiles, the S_CV terms as a stacked bar and a table, SORA 2.5 Table 2 with the applying cell
marked (accessible table, marker + text, not colour alone), the adjacent-area figures, the
assemblies table (rows zoom the map), hotspots, method, notes. Map: adjacent ring as a light wash,
density squares (viridis, log scale), the GRB hatched, the CV dashed, the FG solid, hotspots and
assemblies with labels; a legend lists each.

## Data

The data pack gains an optional `assemblies` layer (`assemblies.geojson`, points or polygons with
`name`, `kind` and `capacity`). `python -m seed.datapack --region …` fetches it from OpenStreetMap
through Overpass: `nwr["leisure"="stadium"]`, `nwr["leisure"="sports_centre"]["capacity"]`,
`nwr["tourism"="theme_park"]`, `nwr["amenity"="marketplace"]` (ODbL, see `DATA_LICENSES.md`);
`capacity` is parsed from the tag ("40000", "40,000", "40.000", "approx. 5000"; lists take the
largest). The synthetic pack has two: "Synthetic Kengeri stadium" (45,000 people, about 0.8 km east
of the Bengaluru survey sample) and "Synthetic city market" (capacity unknown, near the city
centre). A pack without the layer still runs (issue `assemblies_layer_missing`: only user entries
were checked).

## Limits and caveats

- Population data is modelled (WorldPop 1 km in real packs; invented in the synthetic pack). At a
  200 m grid over a 1 km raster the cell means repeat the pixel values: the grid follows Table 4,
  but it cannot add detail the raster lacks.
- Manoeuvre and altimetry conventions differ between national methods; the defaults are
  illustrative and every term is editable. The vertical share of the reaction distance (sin γ) and
  `H_CM = ½·v²/g` for fixed-wings are stated conventions, not SORA text.
- The adjacent ring reaches 35 km: small packs cover only part of it (`partial_coverage`).
- OSM capacity tags are often missing ("capacity unknown"); scheduled events must be entered.
- The density grid is laid out in a local frame centred on the operation; for very long routes
  (hundreds of km) cells far from the centre are slightly rotated against north (exports use the
  exact corners).

## Airspace: not assessed, and interim DGCA wording

- `airbrief_core/analyses/airspace.py`: without a data pack, with a pack that has no `aerodromes`
  layer, or with the geometry outside the pack, aerodrome zones cannot be checked. The verdict is
  then **caution** (never pass; a red user zone still fails), `assessment_complete` is `false`
  (stored results default to `true`), an issue `not_assessed` explains why with the Digital Sky
  address, and the summary starts "Airspace not assessed: no data pack covers this area — aerodrome
  zones were not checked." The airspace results show a caution banner and, always, a **Verify on
  Digital Sky** link (https://digitalsky.dgca.gov.in). `flight_audit` applies the same rule.
- The conservative rule set (`zone_profile: conservative_illustrative`, also `DEFAULT_ZONE_RULES`,
  no longer the default since DGCA 2021 bands, `docs/features/zones.md`): the 0–8 km band of
  airports reads "permission required — verify on Digital Sky"; the 3 km small-airport and 1 km
  heliport rings say "conservative assumption, not a DGCA rule".

## Manual acceptance check

With the synthetic demo pack (`python -m seed.datapack --synthetic --name demo`):

1. Open the sample **Bengaluru survey**, choose **Ground risk** in the rail. The *Operational
   volume* switch is on; the preview reads about "contingency volume 46.6 m … up to 153.2 m above
   ground; ground risk buffer 153.5 m; adjacent area out to 5 km".
2. Press **Assess ground risk**. Expected: iGRC **6** (column ≤ 1 m, ≤ 25 m/s; band < 50,000);
   density **5,632 people/km²** (highest of 71 cells of 200 m); S_CV **46.6 m**
   (3 + 1 + 1 + 18 + 23.6, with S_CM = 18²/(2 × 9.807 × tan 35°) = 23.6 m); H_CV **153.2 m**
   (120 + 4 + 12.7 + 16.5); GRB **153.5 m**; footprint 2.20 km² with about 10,781 people; adjacent
   area out to **5 km** (18 m/s × 180 s = 3.24 km, clamped), average **2,249 people/km²**, 100 %
   covered; one assembly: **Synthetic Kengeri stadium**, 40,000–400,000 people, **756 m** from
   the operational volume. Table 2 marks row "< 50,000", column "≤ 1 m, ≤ 25 m/s".
3. Add an event "Harvest fair" at `12.9120, 77.4910` with 12,000 people and run again: it is listed
   as "Your entry", < 40,000 people.
4. On the map: the survey area solid, a dashed CV line 47 m out, the hatched GRB to 200 m, the
   light adjacent ring to about 5 km, the stadium marker labelled.
5. Airspace: upload a GeoJSON point at 10.0, 45.0 (outside the pack) and run **Check airspace**:
   "Airspace: Caution", the banner *Airspace not assessed*, and the *Verify on Digital Sky* link.

Automated: `tests/core/test_groundrisk.py` (all 35 Table 2 cells, 121 m/s at 20 m → 40 m column,
> 50k out of scope for columns 3–5, the ≤ 250 g rule, hand-computed CVs including θ = 30° →
S_CM = 19.87 m, fixed-wing φ = 30° → 39.74 m at 15 m/s and 70.648 m at 20 m/s, the 5/35 km clamps,
assembly coordinate ranges and UTC time windows, Table 4 grid sizes, a uniform
raster giving an adjacent mean equal to its density, FG ⊂ CV ⊂ GRB ⊂ adjacent, assemblies at
999 m in / 1001 m out, partial coverage), `tests/core/test_airspace.py` (no pack / no aerodrome
layer / outside the pack → caution + `not_assessed`; DGCA wording), `tests/api/test_ground_risk_api.py`
(parameter round trip, exports, 422s, meta), `frontend/e2e/groundrisk.spec.ts`.
