# Contributing

Thanks for helping. AirBrief is free to run and needs no API keys, so please keep it that way: no
paid services, no telemetry, and only MIT, BSD or Apache-compatible dependencies.

## A few rules

- `docs/SPEC.md` is the contract between the parts. If code and spec disagree, fix one of them in
  the same pull request.
- `airbrief_core` stays a plain library: no FastAPI, no database, no network (except
  `data/fetch.py`, which only the pack builder uses).
- A new measurement or analysis comes with a known-answer test.
- Results report facts and margins. Nothing says go or no-go, grants permission, or exports
  waypoints.
- The UI stays accessible: keyboard paths, visible focus, a table for every map layer, and never
  colour alone.

## Setting up

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[api,dev]"
cp .env.example .env
python -m seed.datapack --synthetic --name demo
python -m seed.samples --dest samples/
cd frontend && npm install && cd ..
```

Then `make dev`, or `uvicorn api.main:app --reload` and `cd frontend && npm run dev` in two
terminals.

## Before a pull request

```bash
ruff check .
mypy airbrief_core api seed
pytest -m "not native and not network"
cd frontend && npm run lint && npm run typecheck && npm run build
```

If you changed a screen, run the browser tests too: start the API and a production build of the
web app, then `cd frontend && PLAYWRIGHT_BROWSERS_PATH=0 npm run e2e`. CI runs everything on each
pull request; [`testing/`](testing/README.md) covers PostgreSQL, the C++ kernel and real data.

## Adding a feature

Write down how it works in `docs/features/<name>.md`, with a check someone can repeat on the demo
data, and add it to `docs/SPEC.md`. New database columns need an Alembic migration, new data-pack
layers go into `seed/datapack.py` and `DATA_LICENSES.md`, and protobuf changes only ever add
fields.

## Style

Python 3.11+ with type hints, Ruff at 100 characters. Coordinates are `[lon, lat]` in WGS84 and
units are SI. Errors carry a stable code. TypeScript is strict, and `frontend/lib/types.ts` mirrors
the Pydantic models. Bigger design choices are written up in `docs/adr/`.

Commit messages: a short line in the imperative ("Add KMZ support to ingest"), then the why.

## Licence

Contributions are MIT-licensed. Any data you add must be public domain, ODbL or CC BY, and listed
in `DATA_LICENSES.md`.
