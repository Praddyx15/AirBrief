# CI: what GitHub Actions runs, and how to read it

Three workflows in `.github/workflows/`. Every action is pinned to a full commit SHA (tag in a
comment), and Dependabot proposes updates weekly (`.github/dependabot.yml`).

## `ci.yml`: every push and pull request

| Job | What it runs | Same thing locally |
|---|---|---|
| `python` (4 legs: Python 3.11 and 3.12 × SQLite and PostgreSQL 16) | `ruff check .`, `mypy airbrief_core api seed`, `alembic upgrade head`, `pytest -m "not native and not network"` with coverage. PostgreSQL runs as a service container on GitHub's runner. The 3.12 legs install the exact versions in `requirements/constraints.txt`; the 3.11 legs take the newest allowed, to catch upstream breakage first | [README.md](README.md) and [postgres.md](postgres.md) |
| `native` | `pip install ./native`, a check that the C++ backend is active, `pytest -m native` | [cpp-native.md](cpp-native.md) |
| `contracts` | `buf lint` and `buf build` in `contracts/proto` | `buf lint` with the free `buf` CLI (https://buf.build/docs/installation) |
| `security` | `pip-audit` (fails on any known advisory), bandit 1.9.4 at medium and high (fails), `npm audit --omit=dev --audit-level=moderate` in `frontend/` (fails) | [security.md](security.md) |
| `secret-scan` | gitleaks 8.30.1 (checksum-verified download) over every commit | [security.md](security.md) |
| `web` | `npm ci`, `npm run lint`, `npm run typecheck`, `npm run build` | [web-and-e2e.md](web-and-e2e.md) |
| `e2e` (after `python` and `web`) | Synthetic pack and samples, the API and a production web build in the background, Playwright with Chromium, `npm run e2e` | [web-and-e2e.md](web-and-e2e.md) |

Artifacts: `coverage-py<version>-<db>` (coverage XML of each `python` leg) and, from `e2e`,
`playwright-report` with the HTML report, the traces of failed tests and the API and web logs.
They are kept 7 and 14 days.

Reading a red run:

- **`python`**: open the failing step. pytest prints the assertion with both values; `-ra`
  lists skips with their reasons. A failure on the PostgreSQL legs only usually means a query
  that SQLite tolerates (types, ordering, locking); reproduce it with [postgres.md](postgres.md).
- **`security`**: `pip-audit` and `npm audit` name the package and the advisory. Upgrade within
  the allowed range (`npm update <package>`, or a new pin in `requirements/constraints.txt`), run
  the tests, commit the lockfile. bandit names the file, line and check id.
- **`secret-scan`**: gitleaks names the commit and the rule. A real secret must be revoked at its
  source first; removing it from history alone is not enough.
- **`e2e`**: download `playwright-report`, open `index.html`, and open a failed test's trace
  (from `frontend/`: `./node_modules/.bin/playwright show-trace trace.zip`, or drop it on https://trace.playwright.dev).
  `api.log` and `web.log` are in the same artifact.
- **`native`**: a compile error shows in "Build and install the native kernel"; a parity failure
  names the case.

## `pages.yml`: the static demo

Runs on a version tag (`v*`) and by hand (**Actions → Pages (static demo) → Run workflow**). It
builds the synthetic pack, runs every sample through the API, exports the results as static JSON,
builds the web app in static-export mode under `/<repository name>`, and deploys it to GitHub
Pages. One-time setting: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
The demo is read-only and needs no server: https://<owner>.github.io/AirBrief/.

## `release.yml`: version tags

On a tag `v*`: builds the Python sdist and wheel, the C++ kernel wheels for Linux, macOS and
Windows, writes a build-provenance attestation for each (the only job with `id-token: write` and
`attestations: write`), and attaches them to a GitHub release. Check a download with
`gh attestation verify <file> --repo <owner>/AirBrief`.

## Repository settings CI relies on

- **Actions** allowed for the repository (the default for a new public repository).
- **Pages** source set to GitHub Actions (above).
- **Code security**: Dependabot alerts and security updates, secret scanning and push protection
  on. All free for public repositories.
- **Branch protection** for `main` (recommended): require a pull request and the checks
  `python 3.12 / sqlite`, `python 3.12 / postgres`, `python 3.11 / sqlite`, `python 3.11 / postgres`,
  `native`, `contracts`, `security`, `secret-scan`, `web` and `e2e`.

## What CI does not cover

- Network tests and real data packs: their sources rate-limit and change ([real-data.md](real-data.md)).
- The demo video: it needs a GPU-backed browser window and FFmpeg (`tools/demo-video/`).
- Deployment itself: `RUNBOOK.md` §5 on the real instance.
