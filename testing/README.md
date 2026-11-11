# Testing AirBrief

Everything that checks AirBrief, what each check needs and where it runs. Nothing here needs
Docker or an account. GitHub Actions runs the same commands on every push ([ci.md](ci.md)), so a
green run on your machine and a green tick on the commit mean the same thing.

| Suite | Command (from the repository root unless noted) | Needs | Takes | Guide |
|---|---|---|---|---|
| Lint and types | `ruff check .` then `mypy airbrief_core api seed` | Python venv | 1 min | below |
| Core and API, SQLite | `pytest -m "not native and not network"` | Python venv | 5–10 min | below |
| API on PostgreSQL 16 | `pytest -m "not native and not network" tests/api`, with `TEST_DATABASE_URL` set | a local PostgreSQL 16 | 5 min | [postgres.md](postgres.md) |
| C++ kernel parity | `pip install ./native`, then `pytest -m native` | a C++17 compiler | 3 min | [cpp-native.md](cpp-native.md) |
| Real data and the network | `pytest -m network --run-network`, a real data pack | internet | 15–45 min | [real-data.md](real-data.md) |
| Web: lint, types, build | in `frontend/`: `npm run lint`, `npm run typecheck`, `npm run build` | Node 20 | 3–5 min | [web-and-e2e.md](web-and-e2e.md) |
| End to end (Playwright and axe-core) | in `frontend/`: `npm run e2e` | the API and the web app running, 4 GB free | 40–60 min | [web-and-e2e.md](web-and-e2e.md) |
| Static demo (GitHub Pages build) | `make demo` | Node 20 | 5 min | [web-and-e2e.md](web-and-e2e.md) |
| Protobuf contracts | `cd contracts/proto && buf lint && buf build -o /dev/null` | the `buf` CLI | 1 min | [ci.md](ci.md) |
| Security checks | `pip-audit`, `bandit`, `npm audit`, `gitleaks` | internet | 3 min | [security.md](security.md) |
| Demo video | in `tools/demo-video/`: `npm run verify` | FFmpeg, a rendered video | 1 min | [`tools/demo-video/README.md`](../tools/demo-video/README.md) |
| Manual acceptance | [`RUNBOOK.md` §5](../RUNBOOK.md#5-manual-acceptance-checklist) | the app running | 1–2 h | `RUNBOOK.md` |

## Set up once

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[api,dev]"            # add ,postgres for the PostgreSQL leg
cd frontend && npm ci && cd ..
python -m seed.datapack --synthetic --name demo   # the offline pack the API tests and e2e use
python -m seed.samples --dest samples/
```

Python 3.11 or 3.12, Node 20. On Windows use Git Bash for the commands here; the few places
where PowerShell differs say so.

## Python: lint, types, tests

```bash
ruff check .
mypy airbrief_core api seed
pytest -m "not native and not network"
```

The last line should read `N passed, 70 deselected`: the 64 native and 6 network tests are left
out, and nothing else skips on SQLite. On 2026-11-09 that was 1,760 passed in about 4 minutes,
on Windows with Python 3.12. `-ra` lists any skip with its reason.

Add `--cov=airbrief_core --cov=api` for coverage, as CI does. One file: `pytest tests/core/test_geozones.py`;
one test: `pytest -k swiss_national`.

Where the tests live:

| Folder | What |
|---|---|
| `tests/core/` | The library: geometry and CRS, ingest (including hostile files), every analysis, data packs and their hashes, zone files, FlightMD reports, the brief |
| `tests/api/` | The API through FastAPI's test client: routes, jobs, exports, limits, owner tokens, security headers, backup and restore. These use a database: a temporary SQLite file, or `TEST_DATABASE_URL` |
| `tests/native/` | C++ ↔ Python parity, skipped unless the kernel is installed |
| `tests/frontend/` | The design tokens: every text and background pair meets its contrast target in both themes |
| `frontend/e2e/` | 25 Playwright spec files against the running app |
| `tools/demo-video/verify.mjs` | Checks a rendered video: length, audio level, captions, every scene's page text |

Markers: `native` (needs the kernel), `network` (needs the internet and `--run-network`), `slow`.

## Before a release

1. Python lint, types and tests on SQLite, then on PostgreSQL ([postgres.md](postgres.md)).
2. `pytest -m native` wherever a compiler is available; CI builds the kernel anyway.
3. Web lint, types and build, then the full e2e run.
4. `pytest -m network --run-network` and a fresh real pack ([real-data.md](real-data.md)).
5. Security checks ([security.md](security.md)).
6. Push and wait for CI ([ci.md](ci.md)), then run the Pages workflow and check the demo.
7. Walk through `RUNBOOK.md` §5 on the deployed instance.
