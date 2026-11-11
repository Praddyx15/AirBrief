# Assessment choices: three judgement calls the user makes

ADR-0011 fixed three calls the published rules leave open. They are now choices: set once in
Settings (or picked by answering a few questions), recorded with every run, and changeable for
one run. Every option stays conservative: a band to verify, or a zone flown through, never
becomes a pass or free. The defaults are the old behaviour, so nothing changes for anyone who
leaves them alone.

| Choice | Where it applies | Default | Other option |
|---|---|---|---|
| `band_ceiling` | flight audit | `exceedance`: flying above a band's free-flight ceiling fails, as if the ceiling were the limit | `permission`: it is flight in the yellow zone above the band, permission required (caution) |
| `unverified_band` | eVTOL siting and corridor | `permission`: the DGCA 0–5 km band scores 0 / costs 1.0, named with `verify_official_map` | `exclude`: the band is treated like a red zone (cells excluded, impassable) |
| `ed318_limits` | the ED-318 export of an analysis or a dataset | `geometry`: vertical limits in each geometry's `layer` (EUROCAE ED-318, ENAIRE) | `properties`: limits in the feature properties, as some national feeds do |

## Where to find it

- **Settings → Assessment choices**: three radio groups, one sentence per option on what it does
  and when to pick it. Kept in this browser (`airbrief-settings`), and part of **Reset settings**.
- **Settings → Assessment choices → Help me choose**: the questionnaire (below). **Apply** sets the
  three choices; **Cancel** changes nothing.
- **Flight audit → Zone rules → Above a band's free-flight ceiling** and **eVTOL → Zone rules →
  Within 5 km of an airport** (both tabs, one choice): the form starts from the setting, says so
  with a link to it, and says when this run differs. A reopened run puts back its own recorded
  choice; a run stored before the choices reads as the default.
- **Export → Zones (ED-318, limits in geometry | properties)** in an analysis' and a dataset's
  export menus: the layout from Settings, named in the entry.
- Until its own choice is set (in Settings or with the questionnaire), the flight audit panel, and
  the eVTOL panel likewise, shows a small note, "Using the default assessment choices. Answer 3
  questions to set yours", which links to the questionnaire. It is not a modal and can be
  dismissed (remembered per browser, for both panels).
- History adds "band ceilings as permission" / "airport bands kept out" to a run's parameters.

## The choices in detail

**`band_ceiling`** (`airbrief_core/analyses/flight_audit.py`). It concerns a ceiling with a
yellow zone above it: the DGCA 8–12 km band's 60 m and open airspace's 120 m (both
`ceiling_exceeded_zone = "yellow"`). With `permission`:

- the fixes are classified again with those ceilings raised to at least the flight ceiling, so a
  zone with a lower ceiling under the band (a user or data-pack zone, another rule) still limits
  the flight; the band's own ceiling no longer does;
- the runs above such a ceiling are `above_band_ceiling` (the `AuditExceedance` shape of the
  exceedances: fixes, distances, times, the highest height, `limit_m` = the band's ceiling, so the
  margin is `limit_m − max_agl_m`), with the reason "Flown in yellow airspace above the 60 m
  ceiling of …: permission required", a layer and a CSV table of the same name and the warning
  `above_band_ceiling`; the verdict is at least caution;
- the flight audit panel lists them under "In yellow airspace above a band's ceiling", draws them
  as the map layer "Above a band's ceiling", and marks them on the height profile as "Yellow 1",
  "Yellow 2" …, next to the exceedances' "Over 1", "Over 2" …;
- the audit's own height limit, red zones and a band that turns red above its ceiling still fail;
  zones and bands (`zones`, `band_evidence`) are the same as with `exceedance`.

Rules without such a ceiling (the conservative ones) are not changed by it.

**`unverified_band`** (`airbrief_core/analyses/evtol.py`). With `exclude`, every cell whose
centre is in a `verify_official_map` band (`zones.unverified_bands`, read from the rings, so an
overlapping band of another aerodrome cannot hide it) is excluded from siting (reason
`unverified_band`, whatever `exclude_red_zones` says) and impassable for the corridor. The issue
`verify_official_map` names the bands ("140 cells in VOBG 0–5 km were excluded like a red zone",
"Cells in VOBG 0–5 km were treated like a red zone: impassable"); a corridor that still touches one
(an endpoint, a cell edge) says so. An endpoint inside an excluded band is allowed only as the
endpoint, as in a red zone, so a corridor that starts deep inside one has no route
(`no_route`, which names the excluded bands). The route's 20 m smoothing keeps out of the
excluded cells, as it does out of avoided red zones and cells blocked by tall obstacles: where the
smoothed line would cross one (an endpoint's own cell excepted), the route through the cell
centres is kept. Under rules without
such a band the choice changes nothing, and the result says so: the siting criteria text, and the
corridor issue `unverified_band_not_applicable`.

**`ed318_limits`** (`airbrief_core/io/geozones.to_ed318(limits=…)`). `properties` writes
`lower`, `lowerReference`, `upper`, `upperReference` and `uom` into each feature's properties and
leaves `geometry.layer` out. The importer already read both (`layer` first, else the properties):
both layouts round-trip to the same zones. The brief bundle's `zones.ed318.geojson` always uses
`geometry`.

## Records and the brief

The API records `band_ceiling` (flight audit) and `unverified_band` (eVTOL) in every new run's
parameters, as it does `zone_profile`; results echo them (`null` in results stored before).
The brief:

- names a non-default choice in the part's "Zone rules" fact;
- lists `above_band_ceiling` as "Flown in yellow airspace above a band's ceiling (permission
  required)";
- flags `band_ceiling: permission` in the `non_default_parameters` cross-check, since it loosens
  the evidence; `unverified_band: exclude` is stricter, so it is named but not flagged.

## API

- `POST /api/analyses/`: `band_ceiling` (`flight_audit`), `unverified_band` (`evtol_siting`,
  `evtol_corridor`); another value is `422 invalid_params`.
- `GET /api/analyses/{id}/export/?format=ed318&ed318_limits=geometry|properties` and
  `GET /api/files/{id}/export/?format=ed318&ed318_limits=…`; default `geometry`; with another
  format `422 invalid_params`.
- `GET /api/analyses/{id}/export/tables/` lists `above_band_ceiling` for an audit run with
  `permission`.

## The questionnaire (`frontend/lib/choices.ts`)

| Question | Answer | Choice |
|---|---|---|
| What do you mostly use AirBrief for? | planning my own flights / reviewing flights after the fact / eVTOL or vertiport studies / a mix | decides which of the next two are asked (flight work: the permission question; eVTOL: the airport question; a mix: both) |
| Do you get Digital Sky permission for flights in yellow zones? | Routinely | `band_ceiling: permission` |
| | Sometimes | `exceedance` (stay strict) |
| | No, green zones only | `exceedance` |
| Near airports, how cautious should eVTOL studies be? | Avoid them where possible | `unverified_band: permission` |
| | Keep out completely | `exclude` |
| What will read your ED-318 zone exports? | A U-space or EUROCAE ED-318 tool | `ed318_limits: geometry` |
| | A national feed with the limits in the properties | `properties` |
| | Not sure | `geometry` |

A question that is not asked, or not answered, leaves its choice as it is: the recommendation
reads "unchanged (not asked)" or "unchanged (not answered)" and **Apply** does not touch it. Each
recommendation shows a one-line reason. **Apply** stays disabled ("Nothing to apply yet") until an
answer picks a choice; only the choices it applies count as set for the panels' notes.

## Limits

- `permission` takes the operator's word that permission was held; the audit cannot check it.
  The warning and the brief flag say so.
- A run of fixes above both a band's ceiling and open airspace's 120 m is one run, named after
  its highest fix, as exceedances are.
- `exclude` works on cell centres: a cell whose centre is outside the band stays passable, so a
  corridor may still graze the band at a cell edge, and its issue says so. (A data-pack red zone
  blocks every cell it touches.)
- The choices are per browser; there are no accounts. Another browser starts from the defaults.

## Manual acceptance check (synthetic `demo` pack)

1. API: the flight audit of acceptance check 1 in [`zones-followups.md`](zones-followups.md) (9
   fixes at 75 m, 12.0 to 13.6 km east of HAL) with `"band_ceiling": "permission"`: verdict
   caution, no exceedances, one `above_band_ceiling` run, `limit_m` 60, `max_agl_m` 75, the
   profile's limit 120 m; the brief's cross-check names `(band_ceiling: permission)`. Without it:
   fail, one exceedance against 60 m.
2. Sample **Bengaluru eVTOL** → eVTOL → Within 5 km of an airport **Keep out, like a red zone** →
   **Score sites**: 140 of 531 cells excluded, site #1 76.05, site #2 63.31 (yellow), no site
   marked "Verify on Digital Sky", the issue names VOBG 0–5 km. With **Permission required**: sites
   #2, #3, #7, #8 and #10 are "Verify on Digital Sky".
3. Corridor Whitefield → Electronic City: 16.893 km (3.46 %) costing the band, 17.799 km (9.01 %)
   keeping out of it.
4. Settings → ED-318 **In the properties** → the audit of check 1 → Export → **Zones (ED-318,
   limits in properties)**: "VOBG 8–12 km up to 60 m" has `upper` 60 and `upperReference` AGL in
   its properties and no `geometry.layer`.
5. A fresh browser: the flight audit panel shows the note; **Answer 3 questions to set yours** opens
   the questionnaire on the Settings page; answering "Reviewing flights after the fact",
   "Routinely", "Not sure" and **Apply** sets permission and geometry (the eVTOL choice, not asked,
   stays as it is), and the flight audit note is gone; the eVTOL panel still shows its own.
6. Sample **Training flight (GPX)** → Flight audit, Height limit above ground **150** m, Training
   area **Off**. With **An exceedance (fail)**: Fail, one exceedance from 400 m to 500 m against
   the 120 m default ceiling, marked "Over 1" on the height profile. With **Permission required
   (caution)**: Caution, no exceedances, one row under "In yellow airspace above a band's
   ceiling" (400 m to 500 m, max height 129 m, ceiling 120 m, over by 9 m), marked "Yellow 1".
