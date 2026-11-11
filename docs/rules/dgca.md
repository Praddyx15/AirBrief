# The DGCA drone airspace rules in AirBrief

What the `dgca_2021` zone profile encodes, value by value, and where each value comes from.
Reviewed on 2026-11-09 against the public primary sources below. This is a reading of public
texts by the people who build AirBrief, not legal advice; no lawyer or DGCA officer has checked
it. The official map on [Digital Sky](https://digitalsky.dgca.gov.in) always wins.

## Sources

| Source | Date | What it covers |
|---|---|---|
| Drone Rules, 2021, G.S.R. 589(E), Gazette of India Extraordinary No. 477 ([egazette](https://egazette.gov.in/WriteReadData/2021/229221.pdf)) | 25 August 2021 | The zone definitions (rule 2) and rules 19–24 on the airspace map, permissions, changes and temporary red zones |
| Press release "Ministry of Civil Aviation releases India's airspace map for drone operations" ([PIB, PRID 1757850](https://www.pib.gov.in/PressReleasePage.aspx?PRID=1757850)) | 24 September 2021 | The map itself, and the only public statement of the 5–8 km band |
| Drone (Amendment) Rules, 2022, G.S.R. 108(E) ([civilaviation.gov.in](https://www.civilaviation.gov.in/sites/default/files/2025-11/drone-amendment-rules-2022-dated-11-feb-2022.pdf)) | 11 February 2022 | Remote pilot licence replaced by a remote pilot certificate; no change to the zones |
| Drone (Amendment) Rules, 2023, G.S.R. 715(E) ([civilaviation.gov.in](https://www.civilaviation.gov.in/sites/default/files/2024-04/Drone%20%28Amendment%29%20Rules%2C%202023.pdf)) | 27 September 2023 | Proof of identity on Form D-4; no change to the zones |
| Drone (Amendment) Rules, 2024, G.S.R. 515(E) | Dated 21 August 2024, in force on publication (23 August 2024) | Form D-2 Part A, serial 5, and the transferee entry in Form D-3: an Indian passport number or a government proof of identity and address (voter ID, ration card, driving licence); no change to the zones. The gazette copy is not on the Ministry's site or found online (searched 2026-11-09); two compliance services agree on the number, the date and the content ([TeamLease RegTech](https://www.teamleaseregtech.com/updates/article/34741/drone-amendment-rules-2024/), [Complinity](https://complinity.com/legal-update/amendment-in-drone-rules-2021-15070/)) |
| Draft Civil Drone (Promotion and Regulation) Bill, 2025 | Consultation from 16 September 2025 | Would replace the Rules once rules are made under it; no public sign that it has passed, so the Drone Rules, 2021 still apply |

## Since 2024

None of these changes a zone, but each changes where to look:

- **Bharatiya Vayuyan Adhiniyam, 2024** (in force 1 January 2025) re-enacts the Aircraft Act, 1934,
  under which the Drone Rules were made. Nothing published repeals the Drone Rules; the draft Bill
  of 2025 would, once rules are made under it.
- **eGCA:** since July 2025 the Drone Rules' forms (D-1 to D-5) are handled on DGCA's eGCA portal
  instead of Digital Sky. Rule 19 keeps the airspace map on Digital Sky; if it moves, the links in
  AirBrief (`DIGITAL_SKY_URL` in `airbrief_core/models/presets.py`) move with it. Neither site
  answered from the review machine, so this was not checked first-hand.
- **Map and temporary zones in practice:** Noida International Airport (Jewar) became a red zone
  in April 2025, and police in Delhi, Mumbai, Pune and Gurugram declared temporary drone bans in
  2025 (as reported in [a law firm's timeline](https://www.sigmachambers.in/post/timeline-of-key-drone-regulatory-and-industry-developments-in-india)).
  AirBrief sees none of them, which is why every result says to check Digital Sky.

## Value by value

| Item | What the sources say | What AirBrief does | Verdict |
|---|---|---|---|
| Green zone | Up to 400 ft (120 m) where the map shows no red or yellow zone (rule 2) | Everywhere outside the bands: green up to 120 m, yellow above | Matches |
| 8–12 km from the perimeter | Green up to 200 ft (60 m), yellow above (rule 2) | Green up to 60 m, yellow above | Matches |
| 5–8 km from the perimeter | Yellow "above ground" (press release of 24 September 2021; not in the rule text) | Yellow from the ground | Matches the press release |
| 0–5 km from the perimeter | Not stated in either text | Yellow, "verify on Digital Sky", always needs permission | Assumption, labelled as one |
| Outer edge | "Yellow zone has been reduced from 45 km earlier to 12 km from the airport perimeter" | The bands end at 12 km | Matches |
| Measured from | "The perimeter of an operational airport" | The OpenStreetMap aerodrome boundary, else the hull of the runways, else the reference point minus an assumed radius (`perimeter_approximated`) | Assumption, labelled |
| "Operational" airport | Not defined | Large and medium airports, and small ones with scheduled service, unless set per aerodrome | Assumption, labelled |
| Heliports | No band | None | Matches |
| Who gives permission | Yellow: the air traffic control authority concerned (AAI, IAF, Navy, HAL); red: the Central Government (rule 22, press release) | Stated in the profile's reference; results say "permission required" | Matches |
| No permission in green | For drones up to 500 kg all-up weight (press release; rule 22) | Not modelled: the airspace check does not look at weight | Fine: AirBrief never grants anything |
| Check before every flight | Mandatory (rule 21) | The red-zones notice on every result | Matches |
| Map changes | Take effect no sooner than seven days after the update (rule 23) | In the profile's reference | Added 2026-11-09 |
| Temporary red zones | Up to 96 hours, declared by a State, Union Territory or law enforcement officer of at least Superintendent of Police rank, notified on Digital Sky; holders within 5 km are told when possible (rule 24) | Not modelled (not published as data); the red-zones notice says so | Known gap, stated |
| Red zones | Operations "permitted only by the Central Government" (rule 2) | Not modelled (not published as data) unless a pack or the user's own layers carry them | Known gap, stated |
| International borders | No distance in the Rules; the map draws zones there | Not modelled | Known gap, stated |
| The map as data | Rule 19 asks for a resolution of 10 m or finer, rule 20 for a machine-readable API | No public download or API exists, so AirBrief cannot read it | Not available |

## What changed with this review

- The profile's reference names the source of every value (rule 2, rule 22, the press release),
  and adds rules 21, 23 and 24, the borders and the amendments.
- The red-zones notice in the web app says temporary red zones exist only on Digital Sky.
- No number changed: every distance and height AirBrief used already matched the sources.

## Checking it again

Once a year, or when the Ministry announces a change:

1. Read the Drone Rules page on [civilaviation.gov.in](https://www.civilaviation.gov.in) for new
   amendments, and the gazette for their text.
2. Search PIB for "airspace map" and "drone" press releases since the date above.
3. Look at the map on Digital Sky around two airports (one large, one small) and compare the bands
   with an AirBrief airspace check there.
4. Change `DGCA_2021_ZONE_RULES` in `airbrief_core/models/presets.py` if a value moved (a changed
   rule set is a new profile, so stored results keep the rules they ran with), then this file.
