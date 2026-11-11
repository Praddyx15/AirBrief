# Who adds evidence to a dataset, and what a link shows

AirBrief has no accounts. Whoever has a dataset's link (its id) can open it and run their own
checks on it. What changed is whose runs count as the dataset's evidence.

## Who can add evidence to a dataset

- Every dataset gets an **owner token** when it is created (upload or sample). Only the browser
  that created it has the token (`api/ownership.py`).
- An analysis run with that token (`X-Owner-Token` on `POST /api/analyses/`) is the **owner's
  evidence**: `by_dataset_owner: true` in `AnalysisInfo`. The web app sends the token by itself
  whenever this browser holds it.
- Anyone else may still run an analysis on the dataset (a shared-link visitor checking it for
  their own purposes). It gets `by_dataset_owner: false`, and so does every analysis without a
  dataset.
- In the app, such a run shows **"Added by another visitor"** (icon and text) in the workspace
  History tab and in the brief's analysis list. The brief never ticks it by default; it can still
  be ticked by hand. The visitor who ran it does not see the label on their own runs.
- Analyses created before this rule (database migration `0003_analysis_dataset_owner`) count as
  the owner's.

## What a shared link reveals

With `REQUIRE_OWNER_TOKEN` on (the default when `APP_ENV=production`):

- `GET /api/analyses/?dataset_id=<id>` lists only the owner's analyses of that dataset.
- `?dataset_id=<id>&ids=a,b` adds the caller's own analyses `a`, `b` if they belong to that
  dataset; the app passes the ids this browser created there, so a visitor still sees their runs
  after a reload. Nobody sees another visitor's runs in the list.
- Each analysis stays readable by anyone who has its own id (`GET /api/analyses/{id}/`), and a
  dataset link still shows the owner's later analyses too. Access ends only when the item is
  deleted or expires (`UPLOAD_TTL_HOURS`); there are no separate, revocable share links.

With `REQUIRE_OWNER_TOKEN` off (local development), a dataset lists every analysis run on it, as
before, and `ids` adds nothing. The flag and the label still work.

## List size

`GET /api/analyses/` returns at most **100** analyses per page (`limit` ≤ 100, default 100; was
500). Each item carries its full result, which can run to megabytes, so one request stays bounded.
Page with `offset`. `seed/demo_export.py` pages at 100.

## Acceptance check

1. Open the sample **Bengaluru survey**, run **Airspace**.
2. From another browser (or `curl` without a token), run an airspace check on the same dataset id:
   `curl -X POST localhost:8000/api/analyses/ -H 'Content-Type: application/json' -d '{"kind":"airspace","dataset_id":"<id>","params":{"include_datapack_zones":false}}'`.
3. Back in the first browser, reload: History shows two airspace runs, one marked
   **Added by another visitor**.
4. **Export → Create brief**: your airspace run is ticked, the other one is listed with the label
   and unticked.
5. `curl 'localhost:8000/api/analyses/?limit=101'` answers 422 `invalid_params`.
6. With `REQUIRE_OWNER_TOKEN=true`, `curl 'localhost:8000/api/analyses/?dataset_id=<id>'` lists only
   your run.
