# Plan without a file

Define an operation in seconds and run every module on it, with no GIS file: drop a pin with a
radius, draw a line or an area on the map, type or paste coordinates, paste GeoJSON or WKT, or use
the device location. "Go to" finds typed coordinates and the data pack's aerodromes. Context
layers (aerodromes, zone rings, pack zones, obstacles, water, pack coverage) can be shown on the
map before any module runs.

Everything here is **evidence input**: a point, route or area to check. AirBrief does not make
waypoints or missions and exports none (that is UAOP's job).

## How to use it

| Where | What |
| --- | --- |
| Landing page | **Check a location** opens `/w/view?m=airspace&plan=pin`: the workspace in pin mode. |
| Workspace map, top left | **New operation**: Go to search, Pin, Line, Area, Coordinates…, Paste…, My location. |
| Module panels without a dataset | "Draw, pin or open a dataset" with buttons that start the right tool (Survey and eVTOL siting offer Area and Paste…). |
| Command palette (Ctrl/⌘+K) | Type coordinates or an ICAO / IATA code / name: the **Go to place** results come first. |
| Layers panel | **Context layers**: six switches, each with its feature count and a table. |
| Phones (< 768 px) | Pin and My location on the map; the pin card's **Check airspace** saves the pin and runs an airspace check at 120 m AGL. |

Drawing: click the map to add points (a pin finishes on its first click). Finish a line or area
with Enter, a double-click, the **Finish** button, or (areas) a click on the first corner;
Backspace / Delete / Ctrl+Z removes the last point; Escape cancels. Keyboard alternative (WCAG
2.5.7): with the map focused, arrow keys pan and **Space** places a point at the map centre, marked
by a crosshair while drawing (also the **Add the map centre** button); the Coordinates dialog takes
typed points. Picking on the map is paused while drawing, so clicks never select features. Every
step is announced to screen readers.

A finished drawing, typed or pasted geometry or device location becomes an unsaved **draft** (a
card with its position, ≈ length / area, the pin radius and how the text was read).
**Use this operation** saves it as an ordinary dataset and opens it in the current module.

## Inputs and how they are read

### Typed coordinates (`airbrief_core/geo/parse.py` `parse_coordinates`, mirrored in `frontend/lib/coords.ts`)

One point per line, or separated by `;`. Accepted forms:

- decimal degrees: `12.9716, 77.5946` · `12.9716 77.5946` · `-33.8688 151.2093`
- hemisphere letters before or after each value: `12.9716 N, 77.5946 E` · `N12.9716 E77.5946`
- degrees, minutes and seconds with `° ′ ″` or `' "` (or `''`), or separated by spaces:
  `12°58′17.8″N 77°35′40.6″E` · `12 58 17.8 N 77 35 40.6 E` · `N 12° 58.297′ E 77° 35.677′`
- a decimal comma where it cannot be the separator: `12,9716 77,5946` · `12,9716, 77,5946` ·
  `12,9716,77,5946`; a comma followed by a space separates the values (`12,5, 77` is 12.5, 77).
  Three whole numbers joined by bare commas (`12,5,77`: 12.5, 77 or 12, 5.77?) are refused.
- `[lon, lat]` in square brackets, read in GeoJSON order
- digits are ASCII `0–9` (the browser and the API read exactly the same texts)

Conversion: D° M′ S″ = D + M/60 + S/3600; S and W are negative. Minutes and seconds must be
below 60; a minus sign and a hemisphere letter together are refused.

Which value is the latitude: hemisphere letters decide; otherwise a value beyond ±90° must be the
longitude; when both values fit either order the text is read as **latitude, longitude** (the
order people type and map apps copy) with an `ambiguous_order` warning and a **Swap latitude and
longitude** button. One order applies to every point; points that prove different orders are
refused (`invalid_coordinates`, naming both lines). Out-of-range values are refused with the line
number.

### Pasted geometry (`parse_geometry_text`, `POST /api/geometry/parse/`)

GeoJSON (geometry, Feature, FeatureCollection, or a bare `[lon, lat]` position array), WKT / EWKT
(`SRID=4326;` only; other SRIDs and GeoJSON with a non-WGS84 `crs` member are refused), or typed
coordinates. Caps: 100 kB of text and 10,000 vertices (`too_complex`); unreadable or empty →
`invalid_geometry`. Coordinates beyond ±180° / ±90° are refused as "looks projected". Heights (Z)
are dropped (`z_dropped`); self-intersecting polygons are repaired with `make_valid`
(`geometry_repaired`). A single point becomes a pin. Pasted Features keep their names and
properties (the text is uploaded as pasted).

### Pins and the radius polygon

A pin with radius R is saved as **two features**: the Point (`radius_m`) and "Pin radius", a
64-sided polygon inscribed in the circle of radius R, so modules that need an area (airspace
overlap, ground risk, survey, eVTOL siting) can use it. The UI says so under the radius field.
Vertex k (θ = 2πk/64) is offset from the centre (φ, λ) using the WGS84 radii of curvature at φ:

- Δφ = R·cos θ / M, Δλ = R·sin θ / (N·cos φ)
- M = a(1 − e²)/(1 − e² sin²φ)^{3/2}, N = a/(1 − e² sin²φ)^{1/2}, a = 6,378,137 m, f = 1/298.257223563

The inscribed polygon's area is (64/2)·R²·sin(2π/64) = 99.84 % of πR² (the server measures it
geodesically like any other polygon; the API test checks this to 0.2 %). Radius 1 m to 50 km;
circles that would reach a pole or cross ±180° are refused. Saved file names: `Pin 13.1979 N
77.7063 E.geojson`, `My location …`, `Drawn line.geojson`, `Drawn area.geojson`, `Typed area.geojson`,
`Pasted geometry.geojson`. Uploads follow the normal retention (72 h by default).

Length and area shown while drawing are previews on the mean sphere (haversine; spherical excess
A = R²/2·|Σ(λᵢ₊₁ − λᵢ)(2 + sin φᵢ + sin φᵢ₊₁)|, Chamberlain & Duquette 2007), marked "≈"; Measure
reports the geodesic values after saving.

### My location

`navigator.geolocation.getCurrentPosition` (high accuracy, 20 s timeout). It needs a secure context
(HTTPS or localhost) and the user's permission; each failure (no support, insecure page, denied,
timeout, unavailable) says what happened and offers typed coordinates instead. The position stays
in the browser until the user saves the operation, which uploads it to this AirBrief server only.
The accuracy shown is the API's radius at 95 % confidence. The web app's `Permissions-Policy`
header allows `geolocation=(self)` (`frontend/next.config.mjs`); a reverse proxy that sets its own
`Permissions-Policy` must keep that, or the browser blocks the request.

## Go to and context layers (API)

- `GET /api/places/?q=&limit=10` → `[{label, kind, ident, lon, lat, detail, datapack}]`. Typed
  coordinates first (`kind: "coordinates"`), then aerodromes of every installed pack ranked: exact
  ident / IATA → ident / IATA prefix → name prefix → a name word starts with the query →
  municipality → substring of name → substring of municipality → every query word starts a word;
  ties by aerodrome size, then name; several packs' matches are merged by that rank (real packs
  before synthetic ones), an aerodrome in several packs listed once. Case-insensitive, at most 25.
- `GET /api/context/?bbox=minLon,minLat,maxLon,maxLat&layers=…&limit=` →
  `{datapack, synthetic, bbox, layers: {name: FeatureCollection}, truncated, counts, limit,
  attribution}`. Layers: `aerodromes` (reference points), `rings` (the aerodrome bands of the rule
  set `profile`, the same polygons the airspace module draws; the web app passes the Rules of the
  open airspace, flight-audit or eVTOL panel, else the default DGCA 2021), `zones` (pack zones), `obstacles` (tallest first
  when cut), `water` (largest first when cut), `coverage` (the pack's bbox). Features touching the
  view are returned whole, never clipped. The view is at most 2° × 2° (422 `area_too_large`), at
  most 5,000 features and 100,000 vertices per layer (at least one feature is kept) with
  `truncated` and the full `counts`; unknown layers → 422 with the allowed list. No
  pack covering the view → `datapack: null` and empty layers. Weak ETag per pack version and
  request, `Cache-Control: max-age=300`.
- `POST /api/geometry/parse/` `{text, shape?}` → `{geometry, kind, source, vertices, issues}`
  (rate limited like every POST).

The web app fetches each switched-on layer for the view padded by half its size and snapped to a
0.05° grid (so small pans reuse the cache); beyond 2° × 2° the panel says "Zoom in". Going to an
aerodrome switches on the Aerodromes and Aerodrome zone rings layers when no context layer is on.
Context layers are drawn below the dataset and module layers with legends (zones and rings with
the same colours and hatching as the airspace module) and are reference data, not an assessment.

Static demo (`NEXT_PUBLIC_DEMO=1`): context layers and place search come from `demo/context.json`
(written by `python -m seed.demo_export` for the first pack's bbox, cut to 2° × 2°), coordinates and
GeoJSON are read in the browser, WKT needs the API, and saving is refused (read-only demo).

## Limits and caveats

- Rings are the indicative aerodrome bands of the chosen rules (DGCA 2021 from the airport
  perimeter by default), not an official airspace map; always verify on DGCA Digital Sky.
- The pin polygon is a 64-gon (0.16 % smaller in area than the circle).
- The order assumption for ambiguous coordinates is a warning, not a guess hidden from the user.
- Place search covers the packs' aerodromes only; there is no online geocoder (Nominatim is not used).
- Phones offer pin, my location and an airspace check only; drawing lines and areas needs a larger
  screen (`?plan=line|area|coordinates|paste` starts the pin there).
- `GET /api/context/` and `GET /api/places/` are bounded (view size, feature and vertex caps, a
  200-character query) but, like every GET of this API, not rate limited; only POSTs are.

## Manual acceptance check (synthetic demo pack)

1. Landing page → **Check a location** → the workspace opens on Airspace with "Drop a pin" active.
   Click the map 9 km north-east of VOBL (or Coordinates… → `13.1979, 77.7063` → Pin → Show on
   the map), choose 500 m, **Use this operation**: the dataset `Pin 13.1979 N 77.7063 E.geojson`
   opens with 2 features; Measure shows the "Pin radius" area ≈ 0.784 km² (99.84 % of π·0.5²).
   **Check airspace** → Caution with "permission required" (inside VOBL's boundary: its 0–5 km
   band, "verify on Digital Sky"), nearest aerodrome VOBL.
2. Ctrl+K → type `VOBL` → the first result is "VOBL · Kempegowda International Airport" (Large
   airport · Bengaluru · IATA BLR); Enter → the map moves there, the Aerodromes and Aerodrome zone
   rings context layers are on (Layers panel → Context layers); switch on Obstacles.
3. Survey → New operation → Paste… → `POLYGON ((77.48 12.90, 77.50 12.90, 77.50 12.91, 77.48
   12.91, 77.48 12.90))` → Read geometry → Use this operation → Estimate survey: area ≈ 240.1 ha.
4. Coordinates… → `12 58 17.8 N 77 35 40.6 E` reads as 12.97161° N, 77.59461° E; `151.2093,
   -33.8688` reads as longitude, latitude; `12.9, 100.5` + `100.5, 12.9` on two lines is refused.

Tests: `tests/core/test_geo_parse.py`, `tests/api/test_context.py`, `frontend/e2e/plan.spec.ts`.
