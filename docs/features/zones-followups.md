# Zone rules everywhere: flight audit, eVTOL, context rings, the brief bundle

The zone engine v2 ([`zones.md`](zones.md)) made DGCA 2021 the airspace check's default rule set
(`zone_profile`). The flight audit and eVTOL siting and corridor now take the same `zone_profile`,
the map's context rings are drawn with the Rules of the open panel, and a brief bundle carries the
zones its airspace checks and flight audits were judged against as an ED-318 file.

## Where to find it

- **Flight audit** panel → **Zone rules** → **Rules** (DGCA 2021 / Conservative (illustrative) /
  Custom JSON), the same control as in the airspace panel. Results: **Aerodrome bands** chips
  (each band the track's fixes fall in, the highest height flown and its margin), a *Verify on
  Digital Sky* class for the 0–5 km band in **Zones entered**, the bands on the map (layer
  **Zones**), and *Zones (ED-318)* in the export menu.
- **eVTOL** panel → **Zone rules** in both tabs (one choice for siting and corridor). Siting
  results: a *Verify on Digital Sky* zone in the ranking, a note on the site and the issue
  `verify_official_map`; corridor results: the zones judged at the cruise height and the issues
  `verify_official_map` and `open_airspace_above_ceiling`. Both say "Zone rules: …".
- **Map → Layers → Context layers → Aerodrome zone rings**: drawn with the Rules chosen in the open
  airspace, flight-audit or eVTOL panel; with none open (or Custom rules), DGCA 2021.
- **Brief → Download bundle**: `zones.ed318.geojson` when the brief has an airspace check or a
  flight audit.
- **History**: "conservative rules" / "custom rules" after the parameters of an audit or eVTOL run
  that did not use DGCA 2021 (as for airspace).

## Rule profiles (`zone_profile`)

Same choices, default and lookup (`airbrief_core.models.presets.profile_rules`) as airspace:
`dgca_2021` (default), `conservative_illustrative`, `custom` (= the run's `zone_rules`; sending
`zone_rules` alone makes it `custom`, `custom` without rules or rules with another profile is
`422`). The API stores the profile with every airspace, flight-audit and eVTOL run, so a later
change of the default never changes what a stored run meant. Runs stored before profiles have none:
they used the conservative rules, and the results, the brief ("Conservative illustrative (before
zone profiles)"), the reopened form and History all read them that way. `GET /api/meta/`
`zone_rules` is now the default profile's rules (DGCA 2021).

The brief's `non_default_parameters` cross-check treats every kind as it treats airspace: a preset
profile is not flagged, `zone_rules` that differ from every named rule set are.

## Flight audit (`airbrief_core/analyses/flight_audit.py`)

- **Limits**: each fix's limit is `min(ceiling_agl_m, zone ceiling)` with the zone classified on the
  ground, so a band's free-flight ceiling (60 m in the DGCA 8–12 km band) is a limit; flying above
  it is an exceedance ("Above the 60 m ceiling of HAL Airport (8–12 km: green up to 60 m …)"). Bands
  without a free-flight ceiling (5–8 km, the 0–5 km band to verify) keep the flight's limit and
  count through the zones (permission: caution). A zone with vertical limits applies to a fix's
  limit only where the fix, or a segment it ends, is within them (as the zone list counts
  segments): flown at 150 m under a yellow zone that starts at 200 m, the limit there is open
  airspace's 120 m (an exceedance, or with `band_ceiling: permission` a stretch above the band
  ceiling).
- **Zones**: `zone_hits_by_activity(path, fixes = (lons, lats, agl))`: each band and zone is judged
  at the highest height flown inside the zone: the highest of its fixes inside (the same fixes
  `band_evidence` counts per band) and of both ends of every track segment that touches it, so a
  climb across the boundary counts the same whether a fix falls just inside or not. Climbing to
  100 m in open airspace, descending to 40 m and then crossing the 8–12 km band at
  40 m is the band's green, not the yellow above 60 m. A band flown above its ceiling is the zone
  above it (DGCA 8–12 km: yellow, permission, with `agl_margin_m`); at a constant height the
  audit's zone hits are the airspace check's for the same line. A zone with a lower limit (a red
  ED-318 zone from 150 m) counts only where the track is inside it and at or above that limit;
  the others are left out with an issue `zones_inactive`. A 2D track has no height: zones are
  judged on the ground.
- **Bands**: `band_evidence` over the track's fixes (`sample_kind = "track"`): per band reached and
  "elsewhere", the number of fixes, the highest height flown and its margin to the band's ceiling.
- **Map and export**: a `zones` layer (bands near the track and the zones it touched) when there is
  any; it is the ED-318 export of the audit.

## eVTOL (`airbrief_core/analyses/evtol.py`)

The DGCA 0–5 km band ("not defined by the published rule — verify on Digital Sky") is not red, but
it may hold red zones. Evidence stays conservative: it is never free.

- **Siting** scores the zone at the site, on the ground: green 1, yellow with a free-flight ceiling
  0.5, permission 0, red 0 or excluded. The 0–5 km band scores 0 like permission. It is **not
  excluded** (it is not known to be red, and an airport can host a vertiport) but **marked**:
  `SiteCandidate.zone_basis = "verify_official_map"`, a note ("HAL Airport, 0–5 km band: not defined
  by the published rule and may be a red zone. Verify on Digital Sky; permission required."), the
  issue `verify_official_map` naming the sites, and the criteria text. Under DGCA the 8–12 km band
  is green on the ground (1.0; it was 0.5 as a conservative yellow band).
- **Corridor** costs are judged at the cruise height above ground (`cruise_altitude_agl_m`; at a
  fixed AMSL level, the level minus each cell's ground): a band flown above its ceiling counts as
  its zone above it (DGCA 8–12 km at 300 m: yellow, permission, 1.0). The 0–5 km band costs 1.0,
  the most a passable cell can cost, never 0; the result names it (`verify_official_map`, "costed
  as permission required, never as free"). Open airspace above its ceiling (yellow above 120 m
  under DGCA) is the same in every cell, so it adds no cost and is the issue
  `open_airspace_above_ceiling` instead (otherwise the airport bands would cost no more than open
  country at cruise height). The zone hits are judged at the highest height above ground along the
  route. Under the conservative rules nothing changes: they have no ceiling-exceeded zones.

## Context rings follow the panel

`useRingsProfile(profile)` (in `components/modules/airspace/zone-inputs.tsx`) puts the open
panel's Rules into the plan store (`zoneProfile`) and clears it when the panel closes;
`useContextData` passes it to `GET /api/context/?layers=rings&profile=…`. Only the rings depend on
it: the query key and the 5-minute browser cache are per rule set, the other layers stay cached.
Custom rules exist only in an analysis, so the rings then show DGCA 2021; the static demo has the
default rings only. The rings legend says which rules it shows.

## The brief bundle's `zones.ed318.geojson`

Built with `to_ed318` from the `zones` layer of every airspace check and flight audit in the brief
(`api/services/zones.analysis_zones`, the same as `?format=ed318`): aerodrome bands (a band that
changes zone at its ceiling is two volumes, "VOBG 8–12 km up to 60 m" and "… above 60 m") and the
user, dataset and pack zones touched. Their zones-layer features carry the schedule, restriction,
authority, reason and source format, so an ED-269 zone open MON–FRI comes back with its days, its
authority and its own restriction code (`CONDITIONAL`, not a code guessed from its colour). A zone two analyses share is written once and names both in
`extendedProperties.airbrief.analyses` (`["01_airspace", "02_flight_audit"]`); the file's
`airbrief` member has the notice, the watermark and the analyses. A flight audit without a zones
layer (stored before it, or nothing near) or a per-feature airspace run adds no zone; the file is
still written. It is listed with its size and SHA-256 in `provenance.json`, so `signature.json`
covers it and `python -m airbrief_core.verify` re-hashes it.

## Limits and caveats

- The audit limit uses the band's free-flight ceiling: flying above 60 m in the 8–12 km band fails
  the audit (an exceedance) even though permission could have been held; the zones table says it
  was the yellow zone above.
- Corridor costs use the cruise height above ground as a single value for an AGL corridor (the real
  height is higher over low ground); the zone hits use the highest.
- The rings legend may show the previous rule set for a moment while the new rings load.
- eVTOL siting has no height: zones at the site, on the ground.

## Manual acceptance check (synthetic `demo` pack)

1. API: `POST /api/analyses/` `{"kind": "flight_audit", "params": {"track": {"coordinates": […],
   "altitude_mode": "relativeToGround"}}}` with 9 fixes at 75 m every 200 m from 12.0 to 13.6 km
   east of HAL (`77.7788, 12.9499` to `77.7935, 12.9499`): stored `zone_profile` `dgca_2021`;
   verdict fail; band "8–12 km" yellow, permission, margin −15 m, 9 fixes; exceedance limit 60 m.
   With `"zone_profile": "conservative_illustrative"`: "8–12 km" yellow without permission (−15 m,
   6 fixes) and "Elsewhere" green (+45 m, 3 fixes).
2. Flight audit panel, sample *flightmd_report_v12* → **Rules** *Conservative (illustrative)* →
   **Audit flight**: the chips end with "Rules: Conservative (illustrative)."; reload: the select
   still shows it.
3. Sample **Bengaluru eVTOL** → eVTOL → **Score sites** (DGCA 2021): site #1 76.05 (green), sites
   #2, #3, #7, #8 and #10 "Verify on Digital Sky" with the issue naming them. Conservative: site #1
   72.57 (yellow), no such issue.
4. Corridor Whitefield → Electronic City (DGCA 2021): 16.893 km, detour 3.46 %; zones HAL 0–5 km
   (verify), 5–8 km, 8–12 km (permission, 255 m over); issues "The corridor crosses VOBG 0–5 km …"
   and "The route flies up to 315 m above ground, over the 120 m ceiling of open airspace …".
   Conservative: the same route, HAL 0–8 km and 8–12 km (60 m), neither issue.
5. Airspace panel open, Ctrl+K → VOBL (rings on), **Rules** → *Conservative (illustrative)*: the
   rings are refetched with `profile=conservative_illustrative` (circles around the reference point
   instead of bands around the runways).
6. Brief with an airspace check and a flight audit → **Download bundle**: `zones.ed318.geojson` is
   there and in `provenance.json`; `python -m airbrief_core.verify bundle.zip` passes.
