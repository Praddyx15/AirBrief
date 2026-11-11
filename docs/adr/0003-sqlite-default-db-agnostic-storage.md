# ADR-0003: SQLite by default, database-agnostic storage, DB-backed jobs without Redis

- **Status:** Accepted. This replaces the earlier plan that required PostgreSQL + PostGIS.
- **Date:** 2026-10-08
- **Milestone:** M1

## Context

The first plan required PostgreSQL 16 with PostGIS, and ran everything through Docker Compose. In
practice:

- All geometry work already happens in the core (Shapely, pyproj, h3, rasterio). The API stores and
  returns results; no endpoint in `docs/SPEC.md` §4 needs spatial SQL.
- Requiring a database server (and PostGIS) is the biggest obstacle to "clone, install, run" on a
  laptop, especially on Windows, and to the zero-cost goal on free hosting.
- The static-demo export and the test suite both want a throwaway database per run.

## Decision

1. **SQLAlchemy 2 (sync) with portable column types only.** Geometries are stored as GeoJSON in
   `JSON` columns, with `min_lon/min_lat/max_lon/max_lat` columns for cheap bounding-box filtering.
   There is no GeoAlchemy2 and no PostGIS function.
2. **SQLite is the default:** `DATABASE_URL=sqlite:///./data/airbrief.db`. No server to install.
3. **PostgreSQL is supported** by setting `DATABASE_URL=postgresql+psycopg://...` and installing the
   `postgres` extra (`psycopg[binary]`). PostGIS is not required. CI runs the Python suite against
   both SQLite and `postgres:16`.
4. **Alembic migrations** are the only way the schema changes. They run automatically when the API
   starts with `AUTO_MIGRATE=true` (the default), and by hand with `alembic upgrade head`.
5. **Jobs** live in the `analyses` table (and in `datasets` for large uploads):
   - `JOB_MODE=inprocess` (default): the API runs them on a `ThreadPoolExecutor(WORKER_THREADS)`.
   - `JOB_MODE=external`: the API only queues; an optional `python -m api.worker` process polls.
   - A job is claimed by a compare-and-set `UPDATE ... WHERE status='PENDING'`, which works on both
     SQLite and PostgreSQL. Stale `RUNNING` rows are marked `FAILED` (`interrupted`) at startup.
6. **Docker is optional** and documented only in `DOCKER.md`; no Docker files are committed.

## Alternatives considered

| Option | Why not |
|---|---|
| **PostgreSQL + PostGIS required** | Spatial SQL is not needed by the current endpoints, and it adds a server, an extension and, in practice, Docker to the minimum setup. It stays a straightforward future step (with its own ADR) if cross-dataset spatial queries become a feature. |
| **Celery or RQ with Redis** | Another service to run and host (and to pay for on most free tiers). A single-user or small-team tool does not need a broker. A NATS publisher can be added at merge time with UAOP. |
| **SQLite only** | Fine for one user, but hosted multi-worker deployments want PostgreSQL's concurrency. Supporting both costs little with portable types. |
| **Files on disk instead of a database** | Loses atomic job claiming, listing and pagination. |

## Consequences

- A native install needs only Python and Node: `pip install -e ".[api]"`, then
  `uvicorn api.main:app --reload`. The database file and its tables appear on first start.
- Tests and the static-demo export each use a temporary SQLite database.
- There are no spatial indexes in the database. Bounding-box columns cover dataset listing and
  feature filtering; heavy spatial work stays in the core, where it is tested.
- SQLite allows one writer at a time. Jobs write in short transactions, which suits the in-process
  worker. For several external workers or many concurrent users, use PostgreSQL.
- Portable SQL means avoiding PostgreSQL-only features such as `FOR UPDATE SKIP LOCKED`; the
  compare-and-set claim gives the same "never claimed twice" guarantee.
