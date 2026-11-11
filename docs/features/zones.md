# Zone engine v2: DGCA bands from the perimeter, your own zones, ED-269/ED-318, any-region packs

The airspace check used to measure aerodrome rings from the OurAirports reference point minus an
assumed radius, with invented rings around small airports and heliports, and user zones existed
only in the API. Zone engine v2:

- measures the DGCA 2021 bands from the **airport perimeter** (its mapped boundary, else its
  runways), so the bands hug the runways instead of drawing circles;
- applies them only to **operational** aerodromes, with a per-aerodrome switch;
- reads **your own zones** from any dataset (traced polygons, a KML of event areas) and from
  **ED-269 / ED-318** zone files, with validity periods, daily schedules and vertical limits,
  checked against a **planned window** and the planned height;
- exports zones as **ED-318** GeoJSON;
- builds a data pack for **any area** (`--bbox`), with the WorldPop file of its country.

Everything stays **evidence**: which band and zone apply, by how much a height is over a ceiling.
AirBrief never grants or requests permission and has no live NOTAM feed; red zones are not
published as open data, so every airspace result carries the notice *"Red zones are not published
as open data … verify on Digital Sky"*.

## Where to find it

- **Airspace** panel:
  - the amber **Red zones are not published as open data** notice with the **Verify on Digital
    Sky** link (always shown, also in the results);
  - *Zone rules* → **Rules** (DGCA 2021 / Conservative (illustrative) / Custom, which opens a JSON
    box pre-filled with the DGCA rules) and **Planned window** (date and local start/end; sent in
    UTC);
  - *Zones from layers* → one switch per dataset with polygons among this browser's own files
    (five, plus the chosen ones, then **Show all**); a chosen dataset shows **Columns of …**: *Zone kind*, *Otherwise* (the kind when the
    column is missing or unknown), *Ceiling (m AGL)*, *Layer*, *Valid from (UTC)*, *Valid to (UTC)*;
  - *Aerodrome status* → after a check, one **Operational: IDENT · name** switch per nearby
    aerodrome with bands.
- **Airspace results**: the notice, **Aerodrome bands** chips (band, zone, ceiling, height flown
  and margin), *Active* and *Validity* columns in the zones table, **Zones that do not apply**
  (outside the window or clear of the flight's heights), and the bands drawn on the map around the
  airport outline.
- **Exports**: *Zones (ED-318)* in an airspace analysis' export menu (not a per-feature run's: it
  has no zones layer) and in a dataset's export menu when the dataset has polygons. The dataset
  entry says that a polygon without a zone column is exported as red (prohibited).
- **Upload**: an ED-269 `.json` or ED-318 `.geojson` file is recognised and stored as polygons with
  flat zone properties (a `geozone_file` note says so).

## Rule profiles (`zone_profile`)

| Profile | What it is |
|---|---|
| `dgca_2021` (default) | DGCA bands measured from the perimeter of an operational airport (below) |
| `conservative_illustrative` | The rules used before profiles: 0–8 km permission and 8–12 km up to 60 m for large and medium airports, 0–3 km for small airports, 0–1 km for heliports, measured from the reference point minus 2 / 1 / 0.5 / 0.1 km |
| `custom` | The analysis' own `zone_rules` (sending `zone_rules` alone makes the profile `custom`; the brief flags custom rules) |

**Source of the DGCA 2021 bands**: the Drone Rules, 2021 (G.S.R. 589(E), 25 August 2021) and the
drone airspace map the Ministry of Civil Aviation published under them on 24 September 2021 (PIB
press release "Ministry of Civil Aviation releases India's airspace map for drone operations"). Each
value, its source and the review of 2026-11-09 are in [`docs/rules/dgca.md`](../rules/dgca.md). It
defines, from the **perimeter of an operational airport**:

| Band | Zone | AirBrief |
|---|---|---|
| 0–5 km | not defined by the published text (red or yellow depends on the airport) | `verify_official_map`: yellow, **permission required**, labelled *verify on Digital Sky*; never `pass` |
| 5–8 km | yellow from the ground | yellow, permission required |
| 8–12 km | green up to 60 m (200 ft), yellow above | green with a 60 m ceiling; flown above it, yellow (permission), with the margin |
| elsewhere | green up to 120 m (400 ft), yellow above | default zone green, 120 m; above it yellow (permission), except inside a band or a user, dataset or pack zone with a ceiling of its own, where that ceiling decides (a green zone without one is yellow above 120 m too) |

What is **assumed, not published**: the perimeter (OpenStreetMap boundary, else runways, else an
assumed radius), which airports are *operational* (below), and that heliports and closed fields
have no band. Results stored before profiles have no `zone_profile`: they ran with the conservative
rules, and the brief and the form read them that way; new analyses always store their profile.
The flight audit and eVTOL siting and corridor take the same `zone_profile`
([`zones-followups.md`](zones-followups.md)).

## Aerodrome outline (`distance_from`)

With `ZoneRules.distance_from = "perimeter"` (DGCA) each band is measured, in a local
azimuthal-equidistant frame centred on the aerodrome, from:

1. `perimeter`: its boundary polygon (pack layer `aerodrome_perimeters`, from OSM
   `aeroway=aerodrome`, matched by `icao`/`iata` tag or by containing the reference point);
2. `runway_hull`: the convex hull of its runway ends buffered by half the widest runway (pack
   layer `runways` from OurAirports `runways.csv`; 45 m wide when unknown), with the note
   `perimeter_from_runways`;
3. `reference_point`: the reference point minus `assumed_radius_km`, with the warning
   `perimeter_approximated` (the band may be out by up to half the airport's length).

Band polygons are `outline.buffer(outer) − outline.buffer(inner)` (32 segments per quarter circle),
so a long two-runway airport gets stadium-shaped bands. A point is in a band when
`inner_km ≤ distance < outer_km`; inside the outline the distance is 0. Hits report `distance_km`
(geometry to outline) and `distance_from`.

**Operational**: the rule's types apply unless `aerodrome_operational` says otherwise per ident.
DGCA 2021 treats large and medium airports as operational, and small airports only with
OurAirports `scheduled_service = yes` (unknown counts as operational). `NearestAerodrome` reports
`operational` and `scheduled_service`; aerodromes switched off get the warning
`aerodrome_not_operational`.

## Zones from datasets, time windows and vertical limits

`zone_dataset_ids` (at most 10) name datasets whose polygons become zones; `zone_mappings` maps a
dataset id to its columns (`zone_field`, `default_zone` (red), `ceiling_field`,
`valid_from_field`, `valid_to_field`, `layer`). Without a mapping: kind from `zone`, `colour`,
`color` or `restriction` (`red`/`yellow`/`amber`/`green` or an ED code), ceiling from
`ceiling_agl_m`, `ceiling_m` or `ceiling`, validity from `valid_from`/`valid_to` (ISO 8601, UTC
when no offset), and the flat properties an imported zone file stores (`lower_limit_m`,
`upper_limit_m`, `vertical_reference`, `schedule`, `restriction`, `authority`, `reason`).

The API reads the datasets when the analysis is created (possession of an id is read access, as
for any dataset): only polygons whose bounding box comes within ~1 km of the checked geometry, at
most **5,000** zones in all (`422 too_many_zones`), stored in the analysis as `dataset_zones`
(whatever a request sends there is replaced). The request's own `user_zones` stay capped at 200.
The zone engine looks zones up through an STRtree, so 5,000 zones over a 176,000-cell grid run in
about 11 s.

**Planned window** (`planned_start_utc`, `planned_end_utc`, both or neither, at most 31 days):
a zone applies when one of its active intervals overlaps the window. Intervals are half-open:
`[valid_from, valid_to)` cut to the daily `schedule` periods (`HH:MM` UTC, an end before the start
runs past midnight, `ANY` is every day). A zone that ends as the window starts, or starts as it
ends, does not apply. Zones that do not apply are listed in `inactive_zones` with `active: false`
and an `inactive_reason`, and are not drawn on the map. Without a window every zone counts, with
the note `zone_window_not_checked` when some have validity limits.

**Vertical band**: the flight may be anywhere from the ground to the planned height, so a zone
applies where `lower ≤ planned AGL` and `upper ≥ 0` (heights above the ground at the point).
`AMSL` limits become heights above ground with the pack DEM at the point; `WGS84` (ellipsoid)
limits likewise, after widening the band by the EGM96 geoid range (lower −85.4 m, upper +106.2 m;
note `vertical_reference_approximated`). Unknown ground or height: the zone applies. A zone the
flight cannot reach is listed as not applying ("Above the planned height: the zone starts at …").

## ED-269 / ED-318 (`airbrief_core/io/geozones.py`)

The ED-318 text is sold by EUROCAE, so the readers follow what authorities publish. **Checked
against**: the Swiss FOCA INTERLIS model `UASGeographicalZone_V1` (ED-269: `uomDimensions`,
`lowerLimit`/`lowerVerticalReference`, `upperLimit`/`upperVerticalReference`,
`horizontalProjection`, `applicability` with `permanent`/`startDateTime`/`endDateTime`/`schedule`
of `day`/`startTime`/`endTime`, `zoneAuthority`) and ENAIRE's exchange specification
S431-24-INS-001-1.1 for ED-318 (`type` codes USPACE, PROHIBITED, REQ_AUTHORIZATION, CONDITIONAL,
NO_RESTRICTION; `variant`; `reason` codes; `lower`/`upper` with `lowerReference`/`upperReference`
AGL, AMSL or WGS84 and `uom` M or FT; RFC 3339 times; `startEvent`/`endEvent`). **Assumed, not
verified**: an ED-269 circle is `{"type": "Circle", "center": [lon, lat], "radius": r}`, an ED-318
circle a Point with `"extent": {"subType": "Circle", "radius": r}`, ED-318 limits sit in
`geometry.layer` (or in the properties), and a radius uses the volume's unit. Test with your
authority's real files before relying on them.

| Restriction | Zone |
|---|---|
| PROHIBITED | red |
| REQ_AUTHORISATION / REQ_AUTHORIZATION | yellow, permission |
| CONDITIONAL | yellow, note "Conditional: …" |
| USPACE / U-SPACE | yellow, note "U-space airspace" |
| NO_RESTRICTION | green |

Circles become 64-sided polygons built in a local frame (area within 0.2 % of πr²), with any
radius up to 500 km; feet become metres (× 0.3048, 2 decimals); a vertical limit of `99999` or
`UNL` is unlimited; a lower limit of 0 AGL is "from the ground". Schedule times are kept in UTC:
a time with an offset moves to the day before or after when it crosses midnight (Monday
00:00+02:00 is Sunday 22:00 UTC). A zone with several volumes or applicability periods becomes one zone each ("name (1/4)").
A schedule driven by sunrise/sunset events is read as all day (noted). A lower limit in another
reference than the upper one cannot be converted without terrain, so it becomes "from the
ground" (a wider band, never a narrower one).

**Hostile input**: at most 5,000 zones per file, counted after the split into volumes ×
applicability periods and checked before any zone is built; 100 volumes and 10 periods per zone, 20
schedule entries, 1,000,000 vertices in all (each applicability period counts its own copy of
the volumes), 32 levels of nesting (checked iteratively before
parsing), circle radius up to 500 km, WGS84 coordinates only; any malformed zone is refused with
`invalid_params` and its index (`invalid_zone_file` on upload; past 5,000 parts the index is the
zone that crossed it and `details.limit` is 5,000).

**Real national files** (checked 2026-10-30 and 2026-11-09; `pytest --run-network -k "swiss or national"`
downloads the public ones and puts each through the whole upload pipeline):

| Publisher | Format and layout | Size | Result |
|---|---|---|---|
| FOCA, Switzerland (`data.geo.admin.ch/ch.bazl.einschraenkungen-drohnen`), open use with the source named | ED-269 1.0: `{title, description, features}`, limits as `upperLimit`/`uomDimensions` | 17 MB, 1,256 zones | Read. One zone (Geneva's police sites) has 63 volumes, hence the per-zone cap of 100; five self-crossing rings are repaired and flagged |
| IAA, Ireland (`iaa.ie`, UAS geographic zones), no licence stated, "reference only" | ED-318: FeatureCollection with `datasetMetadata`, limits in `geometry.layer` in `ft`, `reason` as one string | 7 MB, 83 zones | Read as 90 parts (seven zones have applicability periods) |
| ENAIRE, Spain (`aip.enaire.es`, ZGUAS), all rights reserved, credit required | ED-318 as exported from ArcGIS: the zone in `properties.UASZone`, limits in `properties.verticalLayer` with `UOM`, `reasons`, HTML in `message`, empty strings for missing values | Aero 12 MB / 1,672 zones; Urbano 4 zones; Infra 98 MB / 14,163 zones | Aero and Urbano read. Infra is over the upload limits (50 MB, 5,000 zones, 1,000,000 vertices); without them it reads in 6 s with about 700 MB of memory, so it is for the pack builder, not an upload. One of its zones has an empty schedule entry, read as no schedule |
| DAC, Luxembourg (`drones.geoportail.lu/zones`), CC0 | ED-269 with a UTF-8 byte order mark | 80 kB, 43 zones | Read |
| DFS, Germany (`dipul.bund.de`, samples for testing only; the full data is for manufacturers) | ED-318 in a service reply `{uasZones, genericReply}`; ED-269 as bare JSON lists, one per category | 7 MB, 975 zones; 27 files | Read |

None of them use the same layout, so the reader takes each as it comes: an ED-318 service reply
or a bare ED-269 list is unwrapped, ENAIRE's nested zone is lifted, limits are taken from
`geometry.layer`, `properties.verticalLayer` or flat properties (`uom`, `UOM` or
`uomDimensions`), `reason` or `reasons`, HTML is stripped from messages, and a schedule that only
says "every day, all day" is dropped. ED-318 files spell the code `REQ_AUTHORIZATION`, ED-269 files
`REQ_AUTHORISATION`; both read the same. The Spanish text looked garbled in one report: it is
not, the accents are JSON `\u` escapes. Heights in ENAIRE's messages are sometimes measured from
the aerodrome's reference point while the data says AGL; AirBrief follows the data and shows the
message beside it.

**What counts as a zone file**: a JSON document whose features carry an ED restriction and an
`identifier`, or a geometry with `layer` or `extent` (ED-318), or an ED-269 `features` list. A
plain GeoJSON with a property that merely says "Prohibited" stays plain GeoJSON, and so does
AirBrief's own GeoJSON export of imported zones: its flat properties (`zone`, `lower_limit_m`,
`schedule`, …) are read back as they are when the dataset is used for zones.

**Export** (`to_ed318`): one Feature per zone with `identifier` (`AA00001`…), `name`, `type`
(the restriction, in ED-318's spelling: `REQ_AUTHORIZATION`, `USPACE`), `variant: COMMON`, `message`, `reason`, `zoneAuthority`,
`limitedApplicability`, and `geometry.layer` in metres. What ED-318 cannot say exactly (the zone
kind and ceiling, the restriction as given, the full name, note, authority and reason) is also
kept in `extendedProperties.airbrief`, so `parse_ed318(to_ed318(zones)) == zones`. The airspace
export turns a band that changes zone at its ceiling into two volumes ("VOBL 8–12 km up to 60 m"
and "… above 60 m"). An analysis export also has a top-level `airbrief` member with the evidence
notice, the synthetic-data watermark (when the analysis read the synthetic pack) and the analysis
id; ED-318 readers ignore it. A dataset export is the user's own polygons and has none; it reads
them as **Zones from layers** does, so a polygon without a zone column is exported as red
(`PROHIBITED`), and a dataset without polygons is refused with `422 no_zone_polygons`.

## Outputs

- `ZoneHit` adds `active`, `valid_from`, `valid_to`, `agl_margin_m` (ceiling, or an AGL zone's
  lower limit, minus the planned height above ground), `basis` (`rule` / `verify_official_map`),
  `distance_from`, `inactive_reason`.
- `AirspaceResult` adds `zone_profile`, `band_evidence` (per band reached and "elsewhere": the
  samples in it — route profile samples, else an area's grid cells, else vertices — the highest
  planned height above ground and its margin), `inactive_zones` and `assessment_banner`.
- The summary adds a sentence for a 0–5 km band ("… within 5 km of the perimeter of VOBL, which
  the published rule does not define: verify on Digital Sky.") and for a band flown above its
  ceiling ("In the 8–12 km band of VOBL the flight reaches 75 m above ground, 15 m above its 60 m
  green ceiling: yellow zone above it, permission required.").
- The route profile's free-flight limit keeps the band's ceiling where the flight is above it, so
  the stretches above the ceiling are found.
- The brief shows the rules and the bands, and its `non_default_parameters` cross-check names
  zones from datasets (`zone_dataset_ids`), aerodromes marked not operational
  (`aerodrome_operational`) and a planned window that may leave time-limited zones out.

## API

- `POST /api/analyses/` (airspace): `zone_profile`, `planned_start_utc`, `planned_end_utc`,
  `aerodrome_operational`, `zone_dataset_ids`, `zone_mappings` (plus `zone_rules`, `user_zones`).
  Errors: `404 not_found` and `409 dataset_not_ready` for a zone dataset, `422 invalid_params`
  (a dataset without polygons, a bad mapping or window), `422 too_many_zones`.
- `GET /api/analyses/{id}/export/?format=ed318` (airspace and flight audit) and
  `GET /api/files/{id}/export/?format=ed318` (the dataset's polygons; `422 no_zone_polygons`
  when it has none).
- `GET /api/meta/` adds `zone_profiles` (each rule set with its `reference`) and
  `default_zone_profile`.
- `GET /api/context/?…&profile=dgca_2021|conservative_illustrative` draws the context rings with a
  rule set (default DGCA 2021, as the airspace check); the web app passes the Rules of the open
  airspace, flight-audit or eVTOL panel.
- Proto (`contracts/proto/uaop/airspace/v1/airspace.proto`): additive fields only (ZoneHit 13–19,
  NearestAerodrome 9–10, AirspaceAssessment 34–37, new message BandEvidence).

## Data packs: perimeters, runways and any region

New layers, hashed and sealed like the others: `aerodrome_perimeters.geojson` (polygons with
`ident`, `match`) and `runways.geojson` (lines with `airport_ident`, `width_m`, `length_m`,
`surface`). `aerodromes.csv` gains the `scheduled_service` column. The synthetic demo pack has an
elongated VOBL boundary (4.8 × 2.6 km, two north–south runways), boundaries for VOBG and VOYK and
only a runway for VOJK (the hull fallback).

Build a pack for any area of at most 2° × 2°:

    python -m seed.datapack --bbox 85.1,27.5,85.6,27.9 --name kathmandu --iso3 NPL [--lite] [--dest DIR]

`--name` is required; `--iso3` (ISO 3166-1 alpha-3) picks the WorldPop file
`https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/2020/{ISO3}/{iso3}_ppp_2020_1km_Aggregated_UNadj.tif`
(the pattern was checked for IND and NPL); without it the pack has no population layer (ground
risk then needs another pack). A larger bbox is refused with a message: Overpass time limits and
DEM size make it slow and fragile, so split it into several packs. Aerodrome boundaries are
queried over the bbox plus the aerodrome margin (0.3°). `--region bengaluru` stays a named preset.
For DEPLOYMENT/RUNBOOK: after building or editing a pack, `python -m seed.datapack --seal <pack>`
records the new hashes; an existing pack without the new layers still works, with every DGCA
band measured from the reference point (`perimeter_approximated`) until it is rebuilt.

A national zone file can go into a pack's `zones` layer, so every airspace check and flight
audit in that area uses it without an upload:

    python -m seed.datapack --seal data/datapacks/zurich \
      --zones https://data.geo.admin.ch/ch.bazl.einschraenkungen-drohnen/einschraenkungen-drohnen/einschraenkungen-drohnen_4326.json \
      --zones-credit "Federal Office of Civil Aviation FOCA, open use with source"

The file (ED-269 JSON, ED-318 GeoJSON, or a zip with one of them; a path or a URL) is read with
the same adapters and limits as an upload; crossed rings are repaired, and only the zones that
reach into the pack's bbox are kept (20 of FOCA's 1,360 for Zurich). It replaces the layer, and
the manifest records the format, the URL or file name, the number of zones in the file, the date
and the credit, which also goes into the pack's attribution. `--zones` works on a new `--bbox` or
`--region` build too. Files over the upload limit (ENAIRE's infrastructure file) are not read yet.

## Limits and caveats

- The DGCA rule's "perimeter" is not defined in the notification; OSM boundaries can be missing,
  partial or include land outside the airside area. Military airfields are often unmapped.
- "Operational" is a proxy; set each aerodrome that matters by hand and check Digital Sky.
- 0–5 km is never given a colour: it is "verify" and always needs permission.
- No geoid model is shipped: ellipsoid limits are widened by the whole geoid range.
- Schedule times with an offset are moved to UTC without shifting their weekday.
- Imported zone files keep their own licences; AirBrief bundles none.

## Manual acceptance check (synthetic `demo` pack)

1. Airspace, Rules **DGCA 2021**, a route from `77.7066, 13.2950` to `77.7066, 13.2995` (8.3 to
   8.8 km north of the VOBL boundary) at **75 m** AGL: the chip *VOBL 8–12 km* says "ceiling 60 m,
   yellow zone above; flown 75 m, 15 m over", the verdict is caution, and the summary says "15 m above its
   60 m green ceiling". Export *Zones (ED-318)*: it has "VOBL 8–12 km up to 60 m" and "… above
   60 m".
2. A point at `77.7066, 13.2256` (3 km north of the reference point, 0.6 km from the boundary):
   the zone is *Verify on Digital Sky*, permission required, never pass.
3. Rules **Conservative**: the same route shows *VOBL 8–12 km* measured from the reference point
   (10.7 to 11.2 km from it, minus 2 km).
4. Upload an ED-269 file with a 500 m circle (`"restriction": "REQ_AUTHORISATION"`, valid
   08:00–12:00 UTC) over the Bengaluru survey area, choose it under *Zones from layers*, set a
   planned window 09:00–09:30 UTC: the zone is a yellow, active hit; move the window to 13:00:
   it is listed under *Zones that do not apply*.
