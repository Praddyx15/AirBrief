# Environment evidence: sun, daylight profile, magnetic field, wind at height, interference proximity

The **Environment** module (rail icon: sun; analysis kind `environment`) answers the "when and
around what" questions of a planned operation with facts and margins:

- sunrise, sunset, civil dawn and dusk, solar noon and the sun's elevation and azimuth over the day;
- how a planned window sits against a **daylight profile** (minutes outside it, minutes needing
  anti-collision lighting), e.g. "window extends 42 min past sunset";
- the **magnetic declination**, inclination and field intensity from the World Magnetic Model 2025,
  with the WMM blackout and caution zones flagged;
- an **estimated wind at the planned height** from a surface reading, with its margin to the
  aircraft's rated limit, e.g. "est. 13.9 m/s at 100 m vs 12 m/s rated (−1.9 m/s)";
- **power lines, substations and communication masts** near the geometry, with voltage and distance,
  e.g. "220 kV power line crosses the area", "33 kV power line 40 m away";
- a **photogrammetry sun window** (when the sun is between 30° and 60°, adjustable).

It never says "good to fly". Every number is evidence for planning, labelled with its method and
a disclaimer. Everything runs offline: the sun equations need no data, the WMM2025 coefficients ship
with the code, and power lines come from the local data pack.

## Inputs

The geometry is the selected features of the open dataset (all features when none is selected),
like the airspace module. Sun, magnetic field and wind are evaluated at its centroid (its
representative point when a polygon's centroid falls outside it); power lines and masts are
searched around the whole geometry.

| Parameter (API) | Form | Default | Notes |
|---|---|---|---|
| `date` | Date (local) | today in the browser's zone | **Required** in the API (`YYYY-MM-DD`), 1901-01-01…2099-12-31 (the range of the NOAA equations) |
| `window_start`, `window_end` | Planned window, Start/End (local) | next quarter hour, one hour long | `HH:MM`, both or neither; an end before the start is on the next day |
| `utc_offset_min` | Advanced → Time zone of the times | the browser's offset **on that date** (summer time aware) | Minutes east of UTC, −720…840. Display only: every computation is in UTC |
| `daylight_profile` | Daylight profile | `india_daylight` | `india_daylight`, `faa_107` or `custom` |
| `min_sun_elevation_deg` | Minimum sun elevation | 0° | Custom profile only, −18…60° (apparent elevation) |
| `height_agl_m` | Height above ground | 120 m | For the wind estimate and the magnetic-field altitude |
| `surface_wind_ms`, `gust_ms` | Wind at height → Surface wind, Gust | none | Optional section; gust ≥ surface wind |
| `reference_height_m` | Reference height | 10 m | Height of the surface reading |
| `shear_exponent` | Terrain presets + Shear exponent α | 1/7 | Presets: open water 0.11, open land 1/7, suburbs 0.25, city 0.40 |
| `wind_limit_ms` | Rated wind limit | none | Illustrative presets: 10.7 m/s sub-250 g, 12 m/s mapping quadcopter, 15 m/s fixed-wing mapper. Use the aircraft manual's value |
| `include_emi`, `emi_radius_m` | Interference → Power lines and masts, Search distance | on, 300 m | 10…5000 m |
| `sun_min_deg`, `sun_max_deg` | Advanced → Sun window from/to | 30°, 60° | Photogrammetry sun window |

## Method

### Sun (NOAA solar calculator)

`airbrief_core/geo/sun.py` implements the equations of the NOAA Global Monitoring Laboratory
solar calculator (`https://gml.noaa.gov/grad/solcalc/`, after Meeus, *Astronomical Algorithms*,
1991), in UT:

- Julian century `T = (JD − 2451545) / 36525`; mean longitude `L0 = 280.46646 + T(36000.76983 +
  0.0003032 T)`; mean anomaly `M = 357.52911 + T(35999.05029 − 0.0001537 T)`; eccentricity
  `e = 0.016708634 − T(0.000042037 + 0.0000001267 T)`.
- Equation of centre `C = sin M (1.914602 − T(0.004817 + 0.000014 T)) + sin 2M (0.019993 −
  0.000101 T) + 0.000289 sin 3M`; apparent longitude `λ = L0 + C − 0.00569 − 0.00478 sin Ω`,
  `Ω = 125.04 − 1934.136 T`; obliquity `ε = ε0 + 0.00256 cos Ω`; declination `δ = asin(sin ε sin λ)`.
- Equation of time (min) `E = 4·deg(y sin 2L0 − 2e sin M + 4ey sin M cos 2L0 − ½y² sin 4L0 −
  1.25e² sin 2M)`, `y = tan²(ε/2)`; true solar time `= UTC minutes + E + 4·lon`; hour angle
  `H = TST/4 − 180°`; `cos z = sin φ sin δ + cos φ cos δ cos H`; azimuth clockwise from true north.
- Apparent elevation adds NOAA's refraction approximation (piecewise in elevation).

**Events.** Sunrise and sunset are the instants at which the *geometric* elevation of the sun's
centre crosses **−0.833°** (34′ refraction + 16′ semi-diameter, NOAA's zenith 90.833°); civil dawn
and dusk cross **−6°**. Solar noon is the instant of hour angle 0 nearest local midday. As NOAA
does, sunrise and civil dawn are the last upward crossings in the 12 h before noon and sunset and
civil dusk the first downward crossings in the 12 h after it. The day is scanned every minute and
each crossing bisected to under a second, which also finds polar days and nights (no crossing of
−0.833°) and nights without civil darkness (no crossing of −6°). The 24 h track (chart) is the
apparent elevation every 5 min from local midnight.

### Daylight profiles

The local day and the planned window are split at every crossing of −6° and −0.833° (and, for the
custom profile, of the chosen minimum apparent elevation). Each piece is **night** (< −6°),
**civil twilight** (−6° to −0.833°) or **day**, and gets the profile's status:

| Profile | Day | Civil twilight | Night |
|---|---|---|---|
| `india_daylight`: daylight only (sunrise to sunset), as Indian agricultural-spraying SOPs ask | within | outside | outside |
| `faa_107`: 14 CFR 107.29 | within | lighting required (anti-collision lighting visible for 3 statute miles) | flagged (lighting plus night training) |
| `custom`: minimum sun elevation | within when the apparent elevation ≥ the minimum, otherwise outside | | |

Minutes outside = the window's pieces outside the profile; minutes lighting = its pieces needing
lighting. The statement names them relative to sunrise/sunset, civil dawn/dusk or the minimum
elevation when that event exists on the date; on polar nights and days (and the days around them)
it gives totals with the reason instead, e.g. "4 h of the window outside the profile; polar night:
the sun does not rise on this date". These are rules of thumb to verify against the permission or
SOP that applies.

### Magnetic field (WMM2025)

`airbrief_core/geo/wmm.py` evaluates the World Magnetic Model 2025 (NOAA NCEI / BGS, public
domain, degree and order 12, epoch 2025.0, valid 2025.0–2030.0; coefficients in
`airbrief_core/data/wmm/WMM2025.COF`) as in the WMM2025 technical report and NOAA's reference C
library:

1. Secular variation: `g(t) = g(2025.0) + (t − 2025.0)·ġ` (same for `h`), `t` in decimal years.
2. Geodetic → geocentric on WGS84: `Rc = a/√(1 − e² sin²φ)`, `p = (Rc + h) cos φ`,
   `z = (Rc(1 − e²) + h) sin φ`, `r = √(p² + z²)`, `φ′ = asin(z/r)`.
3. Field from Schmidt semi-normalised associated Legendre functions with `a = 6371.2 km`:
   `X′ = −Σ (a/r)ⁿ⁺² Σ (gₙᵐ cos mλ + hₙᵐ sin mλ) dP̆ₙᵐ/dφ′`,
   `Y′ = Σ (a/r)ⁿ⁺² Σ m (gₙᵐ sin mλ − hₙᵐ cos mλ) P̆ₙᵐ / cos φ′`,
   `Z′ = −Σ (n + 1)(a/r)ⁿ⁺² Σ (gₙᵐ cos mλ + hₙᵐ sin mλ) P̆ₙᵐ`.
4. Rotation to geodetic (`ψ = φ′ − φ`), then `H = √(X² + Y²)`, `F = √(H² + Z²)`,
   `D = atan2(Y, X)`, `I = atan2(Z, H)`; annual change from the same sums with `ġ, ḣ`; grid
   variation `D ∓ λ` above 55° N/S.

The point is evaluated at DEM ground + `height_agl_m` above the ellipsoid (the geoid undulation is
ignored: well under 0.01° of declination and about 2.5 nT per 100 m) at the window start (else local
noon). **Blackout zone** H < 2000 nT and **caution zone** 2000 ≤ H < 6000 nT, as the WMM2025 report
defines them. Dates outside 2025–2029 are extrapolated with a warning.

### Wind at height (power law)

`v(z) = v_ref · (z / z_ref)^α` for the mean wind and the gust; margin = rated limit − estimate (a
number, never a verdict). Example: 10 m/s at 10 m with α = 1/7 gives 13.9 m/s at 100 m, −1.9 m/s
against a 12 m/s limit. Shear exponents: 1/7 for open, level land (Peterson & Hennessey, 1978,
*J. Appl. Meteor.* 17, 390–394); 0.11 over open water (Hsu, Meindl & Gilhousen, 1994, *J. Appl.
Meteor.* 33, 757–765); 0.25 for suburbs or woodland and 0.40 for cities with tall buildings
(terrain coefficients tabulated by Masters, *Renewable and Efficient Electric Power Systems*,
2004, after Davenport, 1960). It is a near-neutral approximation, not a forecast: stable nights,
gust fronts and terrain effects can be much stronger.

### Interference (EMI) proximity

Power lines, minor lines, cables, substations and plants come from the data pack's new **`power`**
layer; communication towers and masts from its `obstacles` layer (`kind` `communication_tower` or
`mast`). Without a power layer, power-tower obstacles are listed as pylons. Distances are measured
from the whole geometry (0 m when a feature touches, crosses or lies inside it) in a local
azimuthal-equidistant frame, nearest first, at most 50 items. The list is **proximity evidence,
not an interference prediction**: aircraft makers warn about compass interference near power lines
and transmitters, but the effect depends on current, distance and the aircraft.

**Power layer.** `python -m seed.datapack --region …` fetches
`way["power"~"^(line|minor_line|cable)$"]; nwr["power"~"^(substation|plant)$"];` from Overpass
(`out geom`) into `power.geojson` with `kind`, `name`, `operator`, the raw `voltage` tag and
`voltage_kv` = the largest value of the tag in kV (OSM gives volts, one value per circuit:
`"132000;33000"` → 132 kV; `"33 kV"` → 33 kV; unparseable → null). The synthetic demo pack has a
220 kV line crossing the Bengaluru survey sample area, a 33 kV feeder about 40 m east of it and a
220/33 kV substation about 220 m away.

## Outputs

`EnvironmentResult` (`airbrief_core/models/environment.py`): `sun` (events with UTC ISO time,
local `HH:MM`, day offset and azimuth; polar flags; maximum elevation; 289-point track),
`daylight` (profile, window, `minutes_outside`, `minutes_lighting`, day and window segments with
kind and status, statement), `magnetic` (D, I, H, F, X/Y/Z, annual change of D, grid variation,
model, validity, altitude, decimal year, blackout, caution), `wind` (when given), `emi` (kind, name,
voltage kV, height, distance, nearest point, source), `sun_window`, `facts`, `issues`, `method`,
`disclaimer` and `layers` (`input`, `point`, `emi`, `emi_search`).

Issues: `window_outside_profile`, `lighting_required`, `window_crosses_midnight`, `polar_day`,
`polar_night`, `no_terrain`, `wmm_out_of_range`, `magnetic_blackout` (critical), `magnetic_caution`,
`wind_above_limit`, `gust_above_limit`, `emi_not_assessed` (no data pack), `power_not_assessed` (pack
without a power layer), `masts_not_assessed` (pack without an obstacle layer), `emi_partial_coverage`
(the search distance reaches beyond the pack's bbox, so the list may be incomplete),
`power_line_crossing`, `synthetic_data`, `emi_truncated`, `sun_window_not_reached`.

Exports: JSON (full envelope), GeoJSON (all four layers) and CSV (the EMI table, one row per
feature). The protobuf contract has an additive `EnvironmentEvidence` message.

### In the workspace

- **Panel:** When (date, planned window, the zone label), Daylight profile, Planned height,
  optional Wind and Interference sections, Advanced (time zone, sun window), Check environment,
  Reset to defaults. A reopened analysis restores its own parameters.
- **Results:** tiles for sunrise, sunset and civil dusk (local time with `UTC±hh:mm`), the planned
  window's minutes outside the profile, magnetic declination, wind at height and its margin, the
  nearest power line and the highest sun; the evidence sentences; a 24 h daylight ribbon (night
  dotted, civil twilight striped, day filled, the window outlined, lighting/outside strips under
  it, with a text description and a table); the window by segment; a sun-elevation chart with the
  horizon, −6°, the sun window band and the window band (table alternative every 15 min); the
  magnetic field details; a wind bar (mean, gusts, rated limit); the power lines and masts table
  (click a row to zoom to it and highlight it); the photogrammetry sun window; notes, method and
  disclaimer.
- **Map:** analysed area, power lines (red), minor lines/cables and substations (amber),
  communication masts (blue points), each labelled, the dashed search distance and the evaluation
  point. The legend names every kind by shape and label.
- **Report** (`/w/report`): the sun, window, declination, wind and nearest-power-line numbers, the
  window and power-line tables, the notes and the disclaimer, with the synthetic-data warning when
  the demo pack was used.

## Limits and caveats

- Sun times are for a flat, unobstructed horizon at the evaluation point; terrain and buildings
  can hide the sun earlier. NOAA's equations are accurate to about a minute between ±72° latitude.
- Local times are labelled with one fixed offset for the whole date; on a date when summer time
  starts or ends, times on the other side of the change are an hour off. Everything is computed in
  UTC.
- WMM2025 is a main-field model: local crustal anomalies, magnetic storms and steel nearby are not
  included. Near the magnetic poles (blackout zone) compass headings are unreliable.
- The wind estimate depends entirely on the surface value you enter and on the shear exponent.
- OpenStreetMap power data are incomplete in places and voltage tags are often missing
  ("Not tagged"). Underground cables are listed as cables.

## Validation

`tests/core/test_environment.py`:

- **NOAA times** within ±1 min (in fact within 2 s) for Bengaluru (June, December, October),
  the equator (equinox, June solstice) and 60° N (June, December): sunrise, sunset, civil dawn and
  dusk from NOAA `main.js` `calcSunriseSetUTC` (two passes), solar noon from `calcSolNoon` (hour
  angle 0); positions (azimuth/elevation) within 0.001° of `calcAzEl`.
- **Polar day and night** at 78.2° N (Longyearbyen) on 21 June and 21 December, and the window
  statements on polar-night and twilight-only days.
- **WMM2025 official test values**: 16 rows of NOAA's `WMM2025_TestValues.txt` (from
  `WMM2025COF.zip`; the full 100-row file was checked during review: |ΔD|, |ΔI| < 0.005°, every
  intensity within 0.001 nT, annual changes within 2·10⁻⁶) with declination and inclination within
  0.01°, X/Y/Z/H/F within 0.01 nT and the blackout/caution flags; plus a table in the layout of the
  WMM report (80° N 0° E, 0° 120° E, 80° S 240° E at 0 and 100 km) with grid variation. The shipped
  COF file is the official `WMM.COF` byte for byte (SHA-256 checked, line endings aside).
- **Power law**: 10 m/s at 10 m → 13.9 m/s at 100 m (α = 1/7).
- **EMI distance** to a synthetic line 0.001° of longitude away (108.5 m at 12.9° N).
- **Daylight minutes** for a 18:00–20:00 window spanning dusk under each profile.
- **Voltage tags** (`"132000;33000"` → 132 kV, `"33 kV"`, `"11000/415"`, junk → null).

`tests/api/test_environment_api.py` runs the analysis on the `bengaluru_survey` sample through the
API (synthetic pack) and checks the exports and parameter errors.
`frontend/e2e/environment.spec.ts` covers the UI.

## Manual acceptance check

1. Start the app with the synthetic demo pack (regenerated so it has the power layer).
2. Open the sample **Bengaluru survey**, choose **Environment** in the module rail.
3. Set Date `2026-06-21`, Start `17:30`, End `19:30` (browser zone India, UTC+05:30), Daylight
   profile **FAA Part 107 (US)**, then **Check environment**.

Expected (survey centroid 77.4910° E, 12.9037° N):

| Item | Expected |
|---|---|
| Sunrise / sunset / civil dusk | 05:55 / 18:48 / 19:12 UTC+05:30 (00:25:13Z, 13:18:25Z, 13:41:49Z) |
| Solar noon, highest sun | 12:22, 79.5° |
| Planned window | 18 min outside (night after 19:12), 23 min lighting required (18:48–19:12) |
| Same window, Daylight only profile | 42 min outside: "Window extends 42 min past sunset." |
| Declination / inclination | −1.1° (west of true north) / 14.5°, WMM2025, H ≈ 40,431 nT, F ≈ 41,770 nT at about 1,038 m above the ellipsoid |
| Wind (turn on, 8 m/s surface, 12 m/s preset) | 11.4 m/s at 120 m, 0.6 m/s below the limit |
| Power lines and masts (300 m) | communication tower and mast inside the area; 220 kV line crosses it; 33 kV feeder 40.2 m; 220/33 kV substation 218.6 m (shown as 219 m) |
| Photogrammetry sun window | 08:11–10:23 and 14:20–16:32 |
| Notes | window outside the profile, synthetic data, power line crossing |

API equivalent:

```bash
curl -s -X POST http://localhost:8000/api/analyses/ -H 'Content-Type: application/json' -d '{
  "kind": "environment", "dataset_id": "<id of the bengaluru_survey dataset>",
  "params": {"date": "2026-06-21", "window_start": "17:30", "window_end": "19:30",
             "utc_offset_min": 330, "daylight_profile": "faa_107"}}'
```
