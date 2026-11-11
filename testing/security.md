# Security checks

What protects AirBrief is described in `SECURITY.md`. This file is how to check it.

## Automated, in the test suites

| Where | Covers |
|---|---|
| `tests/api/test_security.py`, `test_security_controls.py` | Headers, CSP, HSTS in production, owner tokens, the SSRF guard, production start-up rules (CORS, TLS to the database) |
| `tests/api/test_abuse_limits.py`, `test_ratelimit.py` | Rate limits (POST, context reads, other reads), the per-address upload budget, storage quota, job time limit, worker memory limit |
| `tests/api/test_dataset_owner.py`, `test_listing_scope.py` | Who may list, delete and run what |
| `tests/api/test_evidence_integrity.py`, `tests/core/test_datapack_integrity.py` | Signed bundles, data-pack hashes, refusing a tampered pack |
| `tests/api/test_health.py` | Refusal counters on `/health`, only for the server itself |
| `tests/api/test_backup_restore.py` | A backup restores to a working instance |
| `tests/core/test_ingest.py`, `test_geozones.py`, `test_input_validation.py` | Hostile uploads: zip bombs and zip-slip, oversized and deeply nested JSON, vertex and zone caps |
| `frontend/e2e/access.spec.ts`, `abuse.spec.ts`, `fetch.spec.ts`, `evidence.spec.ts`, `deploy.spec.ts` | The same seen from the browser |

They run with the normal suites ([README.md](README.md)).

## Dependency and code scanners

Run from the repository root, in the venv. All of them are free; the first three are what the
`security` job in CI runs and fails on.

```bash
pip install pip-audit bandit==1.9.4
pip-audit --skip-editable                       # known advisories in the installed Python packages
bandit -r airbrief_core api seed -ll -ii        # medium and high findings only; expect none
cd frontend && npm audit --omit=dev --audit-level=moderate   # the web app's runtime packages
```

On 2026-10-30 all three were clean, after upgrading Next.js to 15.5.28 and zod to 4.6.7
(GHSA-2464-fhr4-w9xw, GHSA-p3m5-59mp-jv5r, GHSA-mrhx-c7xc-m3w5), certifi to 2026.10.23
(PYSEC-2026-4320) and filelock to 4.2.4 (nineteen CVEs fixed in 4.2.1). New advisories appear
every week: when one of these fails, upgrade the package, update `requirements/constraints.txt`
or the lockfile, and run the tests. `npm audit`
without `--omit=dev` still lists advisories in build and lint tools; they never reach the browser
or the server.

Secrets in the history: CI runs gitleaks over every commit. To run it before pushing, take the
binary for your system from https://github.com/gitleaks/gitleaks/releases (check it against the
release's `checksums.txt`) and run `gitleaks git --redact --no-banner .` in the repository.

## By hand

`RUNBOOK.md` §5.5 is a table of security checks against a running instance (S1 to S23: headers,
owner tokens, limits, SSRF, uploads, signing), each with the command and the expected answer.
Run it once in development mode and once with `APP_ENV=production`, because several protections
are on only in production; the API's start-up warning lists the ones that are off.
