# ADR-0011: Zone engine v2 (DGCA 2021 bands, ED-318) and one result per feature

- **Status:** Accepted
- **Date:** 2026-10-23
- **Milestones:** 15 (zone engine v2), 16 (one result per feature)
- **Extends:** ADR-0005 (evidence, not decisions), ADR-0010 (deferred items)

## Context

ADR-0010 deferred two roadmap items. The airspace check measured aerodrome rings from the
OurAirports reference point minus an assumed radius, drew invented rings around small airports
and heliports, and took user zones through the API only. And every module ran on the union of the
selected features, so 200 farm plots gave one answer.

The Drone Rules 2021 (G.S.R. 589(E)) and the airspace map MoCA published under them on
24 September 2021 measure the bands from the **perimeter of an operational airport**: 5–8 km yellow,
8–12 km green up to 60 m, elsewhere green up to 120 m. The text does not say what 0–5 km is; the
official map decides. Red zones are not open data.

It was built in three parts: zones v2, batch v2 and the zones follow-ups
(`docs/features/zones.md`, `batch.md`, `zones-followups.md`).

## Decision

1. **DGCA 2021 is the default rule profile** (`zone_profile`), for airspace, flight audit, eVTOL and
   the context rings. Bands are measured in a local AEQD frame from the aerodrome's OSM boundary,
   else the hull of its runways (`perimeter_from_runways`), else the reference point minus the
   assumed radius (warning `perimeter_approximated`). Only operational aerodromes get bands: large
   and medium airports, small ones with scheduled service (unknown counts as yes), overridable per
   ident. The other profiles are `conservative_illustrative` (the old rules, unchanged numbers)
   and `custom`. Every new run stores its profile; a run stored without one is read as
   conservative, so stored results keep their meaning.
2. **The 0–5 km band is "verify on Digital Sky", never a pass.** It is yellow, permission required,
   `basis: verify_official_map`, and is never given a colour of its own: it may hold red zones.
3. **eVTOL costs that band as a permission zone.** Siting scores it 0 and marks the site
   (`zone_basis`, a note, the issue `verify_official_map`) but does not exclude it: it is not known
   to be red, and an airport can host a vertiport. The corridor costs it 1.0, the most a passable
   cell can cost. Open airspace above 120 m is the same in every cell, so it is reported
   (`open_airspace_above_ceiling`), not costed.
4. **In a flight audit, flying above a band's free-flight ceiling is an exceedance.** 75 m in the
   8–12 km band fails against 60 m, as under the conservative rules, even though DGCA would call
   it yellow with permission. The zones table says it was the yellow zone above. Zone hits are
   judged at the highest height flown, so they match an airspace check of the same line.
5. **ED-318 is read and written from what authorities publish.** EUROCAE sells the text. The
   readers follow the Swiss FOCA INTERLIS model (ED-269) and ENAIRE's exchange specification
   S431-24-INS-001-1.1 (ED-318). Assumed and written down in `airbrief_core/io/geozones.py`: the
   circle encodings, limits in `geometry.layer`, radii in the volume's unit. Where a file cannot
   be read exactly, the zone gets wider, never narrower: a lower limit in another reference
   becomes "from the ground", a sunrise/sunset schedule is all day, ellipsoid limits are widened
   by the whole EGM96 geoid range. What ED-318 cannot say goes into
   `extendedProperties.airbrief`, so a round trip is exact. Uploads are hostile input with caps.
6. **One result per feature runs in one job with a time budget.** At most 1,000 features (line of
   sight 500). The items share the parameter objects, so pack zones and indexes are built once. A
   failing feature keeps its error on its row. No feature starts after 600 s; the rest are "Not
   run (time budget)", so a run ends inside the production `JOB_TIMEOUT_S` (900 s).

## Alternatives considered

| Option | Why not |
|---|---|
| Keep the conservative rings as the default | They are not the published rule; invented rings around small fields and heliports overstate restrictions |
| Colour the 0–5 km band (red, or yellow as an answer) | The published text does not say; any colour would read as a rule AirBrief does not have |
| Exclude 0–5 km sites from eVTOL siting | Not known to be red; an airport is a natural vertiport site. Scoring 0 and marking it keeps the evidence honest |
| Treat flying above a band's ceiling as "permission" in the audit | The audit answers "did the flight stay within its free-flight limit"; permission held is the operator's fact, not ours |
| Buy and implement the ED-318 text | Not ₹0, and its licence would bind the code; published authority material covers the exchange formats |
| One job per feature | 1,000 jobs flood the queue for everyone, lose the shared caches, and leave no single result to export or brief |
| No time budget | The job limit would then fail the whole run and keep nothing |

## Consequences

- **Results changed meaning only for new runs.** Old results, forms, briefs and History read as
  conservative. The brief's `non_default_parameters` cross-check names custom rules, zones from
  datasets, aerodromes marked not operational and a planned window that may leave time-limited
  zones out, for airspace, flight audit and eVTOL alike.
- **Data packs gained `aerodrome_perimeters` and `runways`.** Packs without them still work, with
  every DGCA band measured from the reference point and the warning `perimeter_approximated`
  until they are rebuilt. `python -m seed.datapack --bbox … --name … [--iso3 …]` builds a pack for
  any area up to 2° × 2°.
- **The protobuf contract grew additively** (ZoneHit 13–19, NearestAerodrome 9–10,
  AirspaceAssessment 34–37, BandEvidence). Batch results have no proto message; UAOP gets the JSON
  envelope (`result.batch == "per_feature"`).
- **Operators running large batches** keep `JOB_TIMEOUT_S` above 600 s (`DEPLOYMENT.md`).
- **Tests:** 1,616 Python tests (65 more skipped: native and network); the final build passed
  138 of 138 Playwright tests, including the new `zones`, `zones-followups` and `batch` specs.
- **Security:** the final review found two weaknesses, both fixed the same day: a zone file is now
  capped at 5,000 parts after the split into volumes × periods (WEAK-15), and ED-318 exports carry
  the evidence notice and watermark (WEAK-16). Still open: the
  environment module's terrain caveat.

## Update (2026-10-23)

Decisions 3, 4 and the layout in 5 are now the user's choices, with the decisions above as the
conservative defaults (`docs/features/choices.md`): the flight audit's `band_ceiling`
(`exceedance`, or `permission`: flight in the yellow zone above the band, a caution), eVTOL's
`unverified_band` (`permission`, or `exclude`: the 0–5 km band treated like a red zone) and the
ED-318 export's `ed318_limits` (`geometry`, or `properties`). They are set in Settings or picked
by a short questionnaire, recorded with every run (stored runs read as the defaults), and a run
can differ. No option turns a band to verify, or a zone flown through, into a pass or free; the
brief flags `band_ceiling: permission` as non-default, and the brief bundle's zone file keeps the
ED-318 layout. With `exclude` the corridor's route keeps out of the band's cells, its 20 m
smoothing included (an endpoint's own cell excepted); with `permission` the audit shows the runs
above a band's ceiling in their own table, map layer and height-profile marks.
