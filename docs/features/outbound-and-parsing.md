# Outbound fetch, links and parser inputs

The API reaches out to the network in one place, the FlightMD report fetch, and turns three kinds
of client text into native-library input: geometry in analysis parameters, CRS text, and FlightMD
report JSON. This page lists what each accepts and what a refusal looks like.

## The FlightMD fetch

Used by `POST /api/flightmd/fetch/` and by flight audits created with `report_url`.

Every URL, and every redirect target (at most 3), must:

- be `https` on port 443, with a host name and no user name or password;
- resolve only to public addresses. Private, loopback, link-local, multicast, reserved, shared
  (100.64/10) and unspecified addresses are refused, and so are IPv6 addresses that stand for
  IPv4 ones: NAT64 (`64:ff9b::/96`, `64:ff9b:1::/48`) and IPv4-compatible (`::a.b.c.d`) always,
  IPv4-mapped (`::ffff:a.b.c.d`) and 6to4 (`2002::/16`) when the IPv4 address inside is not
  public. The connection goes to the address that was checked.

The host of `FLIGHTMD_API_URL` is the one exception. It may be plain http, on a private address
and on its own port, but only `GET {FLIGHTMD_API_URL}/report/{id}` is fetched from it, with `id`
of 1 to 128 characters from `A-Z a-z 0-9 _ -` (a trailing `/` is allowed) and no query string.
Any other path on that host is refused. Share links `https://<any site>/report/<id>` are
rewritten to that path when the API URL is set.

`FLIGHTMD_API_URL` must be a base URL: no user name, password, `?query` or `#fragment`. The API
refuses to start otherwise. `GET /api/meta/` publishes only its scheme and host (with the port),
for example `http://flightmd.internal:8000`, never the path.

The response is capped at 5 MB and `FLIGHTMD_FETCH_TIMEOUT_S` for the whole exchange, must be
JSON nested at most 32 levels deep, and the fetch endpoint passes it on only when it parses as a
FlightMD report.

### Errors

Clients learn which rule or stage failed and nothing else: no host names, resolved addresses,
exception or resolver text, or HTTP status numbers.

| Code | HTTP | `details` | Meaning |
|---|---|---|---|
| `url_not_allowed` | 422 | `{"rule": "length"}` | empty or longer than 2,000 characters |
| | | `{"rule": "characters"}` | spaces or control characters |
| | | `{"rule": "syntax"}` | not a URL (for example a port out of range) |
| | | `{"rule": "scheme"}` | not https (http only to the FlightMD API host) |
| | | `{"rule": "credentials"}` | user name or password in the URL |
| | | `{"rule": "host"}` | no host, or an invalid host name |
| | | `{"rule": "port"}` | https on another port than 443 |
| | | `{"rule": "path"}` | the FlightMD API host, but not `…/report/{id}` |
| | | `{"rule": "private_address"}` | the host resolves to a non-public address |
| `fetch_failed` | 502 | `{"stage": "resolve"}` | the host name was not found |
| | | `{"stage": "connect"}` | no connection, TLS failure or timeout |
| | | `{"stage": "http_status"}` | an answer other than 200 or a redirect |
| | | `{"stage": "too_large"}` | more than 5 MB |
| | | `{"stage": "not_json"}` | not UTF-8 JSON (or nested beyond what the decoder reads) |
| | | `{"stage": "redirects"}` | more than 3 redirects, or one without a target |
| `report_invalid` | 422 | `{}` | the JSON is not a FlightMD report object |
| `too_complex` | 422 | `{"nesting", "limit"}` | the report is nested more than 32 levels deep |

FlightMD report parse errors never echo strings or keys from the report. They give the index of the
bad GPS point and, at most, a parsed out-of-range latitude or longitude, a JSON type name or the
line of a JSON syntax error.

The full detail goes to the server log at INFO, logger `airbrief.api.fetch`, one line per
refusal or failure, with who asked (`POST /api/flightmd/fetch/` or `flight_audit analysis`). The
URL is logged without its query string, which may hold a share token:

    FlightMD fetch refused (POST /api/flightmd/fetch/): rule=private_address url=https://intranet.example.org/report/1 host=intranet.example.org address=10.0.0.5
    FlightMD fetch failed (flight_audit analysis): stage=connect url=https://reports.example.org/report/1 host=reports.example.org addresses=93.184.216.34 reason=ConnectionRefusedError: [Errno 111] Connection refused

The abuse limits add a WARNING line per `url_not_allowed` in `airbrief.security`
(`docs/features/abuse-limits.md`).

## "Open in FlightMD" links

A flight audit's `flightmd_url` becomes the "Open in FlightMD" button for everyone who opens the
audit. The API accepts it only as an `https` link; outside production, plain `http` to
`localhost`, `127.0.0.1` or `[::1]` is accepted too (a FlightMD running on the same computer).
Anything else is a 422 `invalid_params` with `details.field == "flightmd_url"`.

When the report came from `report_url`, that link becomes `flightmd_url` unless the request sets
one, provided it passes the same rule. An http link to an internal `FLIGHTMD_API_URL` in
production is fetched but not shown as a link.

The button shows the link's host next to it (for example `flightmd.example.com`, punycode for
international names), and screen readers announce the host with the button. It opens in a new
tab with `rel="noreferrer noopener"`.

## Opened from FlightMD

FlightMD's **Audit airspace in AirBrief** button (shown when FlightMD has
`NEXT_PUBLIC_AIRBRIEF_URL`) opens `/w/view?m=flight-audit&report_url=<share link>`: the flight
audit with the Link tab chosen and the report's share link filled in, not yet run. Nothing is
fetched until the user presses **Audit flight**, and then only under the rules above, so set
`FLIGHTMD_API_URL` to that FlightMD's API. With the FlightMD web app's address as the FlightMD
address in Settings, the audit's "Open in FlightMD" is a button back to the same report.

## Geometry and lists in analysis parameters

What a request sends in `params` of `POST /api/analyses/` is held to the caps of pasted geometry
(`POST /api/geometry/parse/`) before anything parses it:

- at most 32 levels of nesting anywhere in `params` (a real FeatureCollection needs about 10);
- at most 10,000 positions in each GeoJSON object (a dict whose `type` is a GeoJSON type),
  counting the `coordinates` of everything under it: a GeometryCollection or FeatureCollection
  counts all its parts together. Properties are not counted.

Both refusals are 422 `too_complex` (`details.limit` names the cap). Before this, a
GeometryCollection nested 200 levels deep was stored and then failed with a 500. A body nested
so deeply (thousands of levels) that the JSON decoder itself gives up is a 400
`bad_request` from FastAPI, before any of this runs.

Geometry the server takes from a dataset (the selected features) is not capped this way: it
already passed the upload limits (`MAX_COORDINATES` and the rest), so an analysis of an uploaded
50,000-vertex boundary still runs. Bigger geometry belongs in a file upload, which is what the
message says. The 100 kB text cap of pasted geometry is not applied: a 10,000-vertex polygon in
JSON is about 300 kB, and the whole body is capped by `MAX_JSON_BODY_MB` already.

List bounds in the parameter models (422 `invalid_params` above them):

| List | Most |
|---|---|
| `zone_rules.rules` | 50 (the defaults have 4) |
| `zone_rules.rules[].aerodrome_types` | 20 (OurAirports has 7 types) |
| `zone_rules.rules[].rings` | 20 (the defaults use 2) |
| show `tracks[].times`, `tracks[].coordinates` | 500,000 samples per drone |
| flight audit `track.times` | 500,000 (as `track.coordinates`) |

Already bounded before: `user_zones` 200, ground-risk `assemblies` 100, show `tracks` 5,000,
line-of-sight `observers` 10, flight audit `track.coordinates` 500,000, `feature_indices` 100,000.

## CRS text

`crs_override` on upload, `.prj` files in shapefile zips and the legacy `crs` member of GeoJSON go
through `resolve_crs`. Accepted: EPSG codes (`32643`, `EPSG:32643`), authority strings and URNs,
OGC and ESRI WKT, simple PROJ strings, PROJJSON.

Refused with 422 `invalid_crs` (`details.rule == "file_reference"`), because they make PROJ open
files on the server:

- the PROJ options `+nadgrids`, `+geoidgrids`, `+grids`, `+xy_grids`, `+z_grids`, `+init=`,
  `+file=` and `+model=`;
- the operations `hgridshift`, `vgridshift`, `xyzgridshift`, `gridshift`, `deformation`,
  `tinshift` and `defmodel`;
- `@` anywhere (PROJ's prefix for optional grid names);
- the WKT keywords `PROJ4_GRIDS`, `GRIDS` and `PARAMETERFILE`, also when they only appear after
  parsing (a PROJJSON bound CRS naming a grid file).

The one exception is `+nadgrids=@null` (no datum shift), which GDAL writes into its WKT of
EPSG:3857. In a `.prj` or a GeoJSON `crs` member a refused CRS does not fail the upload: the
layer's CRS stays unknown with an issue, as for any unreadable `.prj`.

PROJ networking is switched off when `airbrief_core.geo.crs` is imported, even when
`PROJ_NETWORK=ON` is set. The PROJ error text is no longer returned (`details` holds only the
CRS text the client sent, cut at 200 characters).

## Manual check

With the API on `http://127.0.0.1:8000` (development):

    curl -s -X POST localhost:8000/api/flightmd/fetch/ -H "content-type: application/json" -d '{"url": "https://10.0.0.1/report/1"}'
    # 422 {"error": {"code": "url_not_allowed", ..., "details": {"rule": "private_address"}}}

    curl -s -X POST localhost:8000/api/flightmd/fetch/ -H "content-type: application/json" -d '{"url": "https://example.org:8443/report/1"}'
    # 422 ... {"rule": "port"}

    curl -s localhost:8000/api/meta/ | python -c "import json,sys; print(json.load(sys.stdin)['flightmd'])"
    # with FLIGHTMD_API_URL=http://flightmd.local:8000/api: {'configured': True, 'url': 'http://flightmd.local:8000'}

    curl -s -X POST localhost:8000/api/files/ -F file=@samples/tier0/square_1km_equator.kml -F "crs_override=+proj=longlat +nadgrids=/etc/passwd"
    # 422 invalid_crs, details.rule == "file_reference"

In the app: open a flight audit made with a FlightMD link; the host of the link is shown to the
left of "Open in FlightMD".
