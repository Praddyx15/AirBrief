# ADR-0001: FastAPI service over a pure Python core package

- **Status:** Accepted
- **Date:** 2026-10-08
- **Milestone:** M1

## Context

AirBrief started as a backend exercise: upload a Shapefile or KML, extract the features, and
return area and length with correct CRS handling. It is also the third member of a suite with
FlightMD (post-flight vehicle health) and UAOP (operations platform). Both siblings use a pure
Python core library, a FastAPI service and a Next.js 14 frontend, and UAOP imports FlightMD's core
as a pinned library. AirBrief has to be:

- usable as a library by UAOP, with no web framework pulled in;
- runnable offline (UAOP has an air-gap requirement);
- mergeable later into one gateway and one frontend;
- simple to run natively on a laptop with Python 3.11+ and Node 20, with no services to install.

## Decision

1. All domain logic lives in **`airbrief_core`**, a pure Python 3.11 package (Pydantic v2,
   Shapely 2, pyproj, rasterio, h3, networkx, scipy, lxml, pyshp). It never imports FastAPI,
   SQLAlchemy or anything under `api/`, and never touches the network except in
   `airbrief_core/data/fetch.py`, which only the seed scripts use.
2. **`api/`** is a thin FastAPI application: routing, validation, storage, jobs, rate limiting and
   CORS. It calls `airbrief_core.process_file` and `airbrief_core.run_analysis`.
3. The Pydantic models in `airbrief_core/models/` are the data contract. The API returns them as
   JSON; the frontend mirrors them in `frontend/lib/types.ts`; the protobuf contracts in
   `contracts/proto/uaop/airspace/v1/` are a stable subset for UAOP.
4. The core is published as `airbrief-core` (setuptools, `py.typed`) with optional extras: `api`,
   `postgres`, `flightmd` and `dev`.

## Alternatives considered

| Option | Why not |
|---|---|
| **GeoDjango** | Strong built-in GIS (admin, spatial ORM, GDAL wrappers), but it couples domain logic to Django models and settings, so UAOP could not import the logic as a small library. It also breaks the FlightMD/UAOP shape that makes a later merge cheap. |
| **Flask + extensions** | Workable, but no first-class Pydantic validation or generated OpenAPI, and it diverges from the siblings. |
| **One package with the web code inside** | Simpler at first, but every library user would install FastAPI and SQLAlchemy, and the purity rule could not be enforced. |

## Consequences

- UAOP can `pip install airbrief-core` and call analyses directly, with no server.
- The core is testable without a database or HTTP; API tests cover only the API layer.
- Two artefacts must stay in sync with the models: the TypeScript mirror and the protobuf subset.
  `docs/SPEC.md` is binding, and CI lints the protobuf.
- GIS conveniences that GeoDjango would give for free (spatial admin, spatial ORM lookups) are
  implemented explicitly in the core, which keeps them visible and testable.
