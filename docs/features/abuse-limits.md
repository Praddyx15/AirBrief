# Abuse limits: storage, job time, request origin, development mode, security log

An AirBrief API is anonymous: anyone who can reach it can upload and run analyses. These limits
keep one client from filling the disk or the job threads, stop other web sites from writing to
it, keep a development-mode server off the public Internet, and leave a trace of what was
refused.

## Settings

| Setting | Development (empty) | Production (empty) | Notes |
|---|---|---|---|
| `STORAGE_QUOTA_MB` | unlimited | 5000 | Total size of the stored uploads under `UPLOAD_DIR`. 0 = unlimited. |
| `UPLOAD_MB_PER_DAY` | unlimited | 1000 | Upload bytes one client address may send in 24 hours, counted from `Content-Length` in hourly buckets. 0 = unlimited. |
| `READ_RATE_LIMIT_PER_HOUR` | unlimited | 36000 | Every `GET` under `/api/` other than the context and place reads. 0 = unlimited. |
| `MIN_FREE_DISK_MB` | 500 | 500 | Uploads are refused while the disk under `UPLOAD_DIR` has less free space. 0 = off. |
| `JOB_TIMEOUT_S` | none | 900 | Wall-clock limit for one analysis. 0 = none. |
| `INLINE_PROCESS_MAX_MB` | 5 | 0 | Uploads up to this size are parsed inside the request; 0 queues every upload. |
| `WORKER_MEMORY_LIMIT_MB` | off | off | `RLIMIT_AS` of `python -m api.worker`, Linux/macOS only. |

"Production" means `APP_ENV=production`. An explicit value always wins over the mode default.
`.env.example` leaves the new keys empty, so a copied file gets the mode defaults.

What they do:

- **Storage.** Before an upload is stored, its `Content-Length` is checked against the quota and
  the free-disk floor; while it streams to disk every chunk is checked again, and counted, so
  parallel uploads cannot squeeze past the quota together. Built-in samples
  (`POST /api/samples/{name}/`) go through the same check. The used total is measured once,
  kept up to date on every write and delete, and re-measured by the TTL sweeper; extraction
  scratch (`work/`) does not count. A refused upload stores nothing.
- **Job time.** When an analysis runs longer than `JOB_TIMEOUT_S`, the runner sets the same
  cancel event the Cancel button uses. The module stops at its next checkpoint and the analysis
  is recorded as `FAILED` with code `job_timeout` and a message naming the limit. Dataset
  processing (parsing an upload) has no cancellation points and is bounded by the ingest limits
  (`MAX_UNZIPPED_MB`, `MAX_FEATURES`, `MAX_COORDINATES`) instead.
- **Queued uploads.** On public instances every upload is parsed by the job runner rather than
  inside the request, so a hostile file never runs in a request thread. Combine it with
  `JOB_MODE=external` and `WORKER_MEMORY_LIMIT_MB` to keep parsing out of the API process with
  a memory cap; Windows logs once that the cap is not supported there.
- **Rate limit.** IPv6 clients are counted per /64 (one subscriber's prefix), IPv4-mapped IPv6
  addresses as their IPv4 address. `DELETE /api/*` now counts against the same
  `RATE_LIMIT_PER_HOUR` budget as `POST`. The key table holds at most 100,000 clients; beyond
  that the least recently seen ones are forgotten. Every other `GET` under `/api/` has a window
  of its own (`READ_RATE_LIMIT_PER_HOUR`), so the web app's polling never eats into the POST budget.
- **Upload budget.** With `UPLOAD_MB_PER_DAY` on, an upload is counted from its `Content-Length`
  before the body is read; one that would take the address past its 24-hour allowance gets `429
  upload_budget_exceeded` with `Retry-After` (when its oldest hour leaves the window), and an
  upload without `Content-Length` gets `411 length_required`. Browsers always send the length.
  A file bigger than the whole day's allowance gets `413 file_too_large` at once, and an upload
  the API refuses or fails to store gives its bytes back, so retrying a bad file does not lock
  the address out.
- **Queue order.** Pending analyses start one per dataset in turn, oldest first within each, so
  a long series of runs on one dataset does not hold up another dataset's.
- **Request origin.** `POST`, `PUT`, `PATCH` and `DELETE` under `/api/` that carry an `Origin`
  header not listed in `CORS_ORIGINS` are refused. Requests without `Origin` (curl, scripts,
  Node and Playwright API calls) pass. In a single-domain deployment, list that domain in
  `CORS_ORIGINS` too: browsers send `Origin` on same-origin `POST`s.
- **Development mode.** With `APP_ENV=development` the API answers only loopback and private
  addresses (LAN, Docker bridge, link-local, CGNAT). The client address is the one the rate
  limiter uses (`TRUSTED_PROXY_HOPS` respected); without trusted hops the last
  `X-Forwarded-For` entry also counts, so a development server behind a hosted proxy is not
  open just because the proxy's own address is private. At start-up the API logs a WARNING
  naming the protections that are off, and `GET /api/meta/` reports `app_env`. The web app
  shows a permanent warning strip under the header when `app_env` is `development` and the page
  is not opened on `localhost`, `127.0.0.1` or `[::1]` (never in the static demo).
- **SQLite.** Every connection sets `PRAGMA secure_delete=ON`, so deleted and expired rows are
  overwritten in the database file instead of lingering in free pages. Backups, logs and
  PostgreSQL are not covered by this.

## Error codes

| Status | Code | When |
|---|---|---|
| 403 | `cross_site_request` | A state-changing `/api/` request from a web page on an origin not in `CORS_ORIGINS`. |
| 403 | `development_mode_local_only` | `APP_ENV=development` and the client is on a public address. The message tells the operator to set `APP_ENV=production`. |
| 429 | `rate_limited` | Over `RATE_LIMIT_PER_HOUR` (`POST` and `DELETE`), `CONTEXT_RATE_LIMIT_PER_HOUR` or `READ_RATE_LIMIT_PER_HOUR`. |
| 429 | `upload_budget_exceeded` | The address's uploads in the last 24 hours reached `UPLOAD_MB_PER_DAY`. |
| 411 | `length_required` | An upload without `Content-Length` while `UPLOAD_MB_PER_DAY` is on. |
| 507 | `storage_full` | `details.reason` is `quota` (`STORAGE_QUOTA_MB`) or `disk` (`MIN_FREE_DISK_MB`). |
| — | `job_timeout` | Error code of an analysis stopped by `JOB_TIMEOUT_S` (status `FAILED`). |

## Security log

Refusals are written to the `airbrief.security` logger, one WARNING line each:

    rate_limited 429 client=3f9c0a51d2e7 path=/api/files/

Fields: error code, HTTP status, client key, request path. The client key is the first 12 hex
digits of SHA-256 over a random per-process salt plus the client address, so one client's lines
can be grouped within a process lifetime without writing its address. The path is cut at 200
characters and escaped onto one line. Query strings, headers, owner tokens and bodies are never
logged.

Logged events: every 403 (owner-token failures `owner_token_required` / `owner_token_invalid`,
`cross_site_request`, `development_mode_local_only`, `listing_disabled`), every 413
(`file_too_large`, `request_too_large`), every 429, every 507 and every `url_not_allowed` (422).
To keep them in a file of their own, route that logger in your logging setup, or filter the
API's output on ` airbrief.security: `.

The same refusals are counted per code since the process started. `GET /health` shows the
counts (`refusals`) only to a request from the server itself, on loopback and not forwarded by
a proxy; everyone else gets `null`. A local monitor can poll it and alert on a jump.

## Limits and caveats

- The limiter, the quota counter and the salt are per process. Behind several API processes each
  counts on its own; use the proxy's limiter for a shared budget.
- `JOB_TIMEOUT_S` acts at the module's next checkpoint, so a single long native call (one huge
  GEOS operation) finishes before the run stops.
- The quota counts only what the API stored. Files copied into `UPLOAD_DIR` by hand are picked
  up at the next TTL sweep, or after a restart.
- Address classification uses Python's `ipaddress.is_global`; NAT64 and IPv4-compatible IPv6
  addresses count as public, so they are refused in development.

## Manual check

With the API running locally in development mode (`uvicorn api.main:app --port 8000`):

1. `curl -s -X POST localhost:8000/api/geometry/parse/ -H "Origin: https://evil.example" -H "Content-Type: application/json" -d "{\"text\":\"12.97, 77.59\"}"`
   → `403`, code `cross_site_request`. The same request without the `Origin` header → `200`.
2. `curl -s localhost:8000/api/meta/` → `"app_env":"development"`.
3. Start a second API with `TRUSTED_PROXY_HOPS=1` and call
   `curl -s localhost:<port>/health -H "X-Forwarded-For: 8.8.8.8"` → `403`,
   `development_mode_local_only`; with `X-Forwarded-For: 192.168.1.20` → `200`. The API log
   shows `development_mode_local_only 403 client=<12 hex> path=/health`.
4. Start an API with `STORAGE_QUOTA_MB=0.001` (about 1 kB) and upload any file from the web
   app: it fails with "This server has no storage left for uploads right now" (`507`,
   `storage_full`, reason `quota`).
5. Serve the web app on the LAN (its LAN origin, e.g. `http://192.168.1.5:3000`, in
   `CORS_ORIGINS`, and `NEXT_PUBLIC_API_URL` pointing at the API's LAN address) and open it from
   another machine: the warning strip "Development mode: anyone who can reach this server can
   see and delete everything on it." shows under the header on every page. On `localhost` it
   never shows.
