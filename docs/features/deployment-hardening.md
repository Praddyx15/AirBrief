# Deployment hardening (security fixes, 2026-10-19)

What changed for people who run an AirBrief instance, and what to do about it. Nothing here
changes an analysis result. Local development works as before, apart from the dev server now
listening on `127.0.0.1` only.

## 1. New settings

| Setting | Development | Production (`APP_ENV=production`) | What it does |
|---|---|---|---|
| `API_DOCS` | on (empty) | **off** (empty) | `/docs` and `/redoc`. `true`/`false` overrides the default. `/openapi.json` is always served |
| `ALLOWED_HOSTS` | off (empty) | off (empty) | Comma-separated host names the API answers to; any other `Host` header gets `400 Invalid host header` |

Both are in `.env.example`. HSTS has no setting: it follows `APP_ENV`.

## 2. API

- **Docs pages off in production.** Swagger UI and ReDoc are third-party scripts from jsDelivr. On
  the app's origin they could read the owner tokens in the browser's `localStorage` if the CDN
  were ever compromised, and every visit tells the CDN the visitor's address. With `API_DOCS=true`
  they come back, now with:
  - pinned versions: `swagger-ui-dist@5.33.1` and `redoc@2.5.4` (FastAPI's defaults float on `@5`
    and `@2`);
  - a Content-Security-Policy that allows exactly those two script files plus, on `/docs`, the
    page's own start-up script by its SHA-256 hash; `connect-src 'self'`, no frames, no forms;
  - no favicon from `fastapi.tiangolo.com` and no Google Fonts. ReDoc's footer logo from
    `cdn.redoc.ly` is blocked by the CSP (one console message on `/redoc`).

  Even then, serve them on their own host name, not the app's (`DEPLOYMENT.md` §3.2).
- **HSTS.** In production every API response carries
  `Strict-Transport-Security: max-age=31536000; includeSubDomains`. Browsers ignore it over plain
  http. Before turning on production on a domain whose subdomains are not all on HTTPS yet, note
  that `includeSubDomains` covers them for a year.
- **Host allow-list.** With `ALLOWED_HOSTS` set, Starlette's `TrustedHostMiddleware` runs first
  and refuses other host names before anything else. List every name the proxy forwards, plus
  `127.0.0.1` if something checks `/health` by address. IPv6 literals go in brackets (`[::1]`).
- **`--no-server-header`.** All documented uvicorn commands now pass it, so responses no longer
  announce `server: uvicorn`.

## 3. Web app

- **Nonce-based CSP.** `frontend/middleware.ts` makes a new nonce for every request and sends
  `script-src 'self' 'nonce-…' 'strict-dynamic'`; `'unsafe-inline'` is gone from `script-src`
  (styles keep it, for inline style attributes). Next.js adds the nonce to its own scripts and
  the root layout passes it to the theme script. An injected inline script no longer runs.
  - Every page now renders per request (`ƒ` in the build output instead of `○`). That costs a
    little server time per page view and nothing else.
  - The dev server sends no CSP, as before (hot reloading needs `eval`).
  - The static export (GitHub Pages) has no middleware: it builds and works as before. If you
    host it yourself with headers, its CSP needs `script-src 'self' 'unsafe-inline'`. The export
    build prints a warning that middleware is disabled; that is expected.
- **HSTS** on `next start` responses of production builds (`next.config.mjs`).

## 4. Local servers on 127.0.0.1

- `make web` and the RUNBOOK commands start `next dev -H 127.0.0.1`; for the production build,
  `npm start -- -H 127.0.0.1`. `http://localhost:3000` still works in browsers, Playwright and the
  demo-video tool.
- The `start` script in `frontend/package.json` still has no host: Docker and the systemd unit
  pass their own `--hostname`. Without one, `next start` listens on every interface, so always
  pass it.

## 5. Docker (`DOCKER.md`)

If you created the Docker files from an older `DOCKER.md`, run the script in its §3 again.

- Ports are published on `127.0.0.1` only; PostgreSQL's port is not published at all.
- PostgreSQL moved to `docker-compose.postgres.yml`, used through
  `COMPOSE_FILE=docker-compose.yml:docker-compose.postgres.yml` in `.env` (`;` on Windows). It has
  no default password: `POSTGRES_PASSWORD` must be set, or Compose stops with
  `required variable POSTGRES_PASSWORD is missing a value`. The old `--profile postgres` is gone,
  because Compose checks required variables even for services behind an inactive profile.
- `UPLOAD_TTL_HOURS` and `INLINE_PROCESS_MAX_MB` no longer have defaults in the compose file, so
  production keeps its own (72 h retention, every upload queued). `API_DOCS`, `ALLOWED_HOSTS` and
  `TRUSTED_PROXY_HOPS` are passed through.
- The API image runs as user `airbrief` (uid 10001), the web image as `node`. A data volume
  created by the old root image needs one
  `docker compose run --rm --user root api chown -R airbrief:airbrief /data`.
- The web image installs with `npm ci`.
- `DOCKER.md` §6.1 lists the settings for an instance other people can reach (`APP_ENV=production`,
  `DOCKER_CORS_ORIGINS`, `TRUSTED_PROXY_HOPS=1` behind a proxy on the host, `ALLOWED_HOSTS`).

## 6. PostgreSQL roles

`DEPLOYMENT.md` §3.6 shows two roles: `airbrief_owner` runs `alembic upgrade head` at deploy
time, `airbrief_app` (rows only, no DDL) is what the API and workers use, with
`AUTO_MIGRATE=false`.

## 7. Secrets and the repository

- The root `.gitignore` now also covers `*.env`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `secrets.*`
  (except `secrets.md`) and `*signing-key*`. Name a brief signing key for example
  `brief-signing-key.pem` and keep it outside the repository folder anyway.
- `.env.example` and `DOCKER.md` use `CHANGE_ME` instead of a working password.
- Before the first commit, scan with gitleaks (`DEPLOYMENT.md` §7). CI runs gitleaks on every push
  and pull request (`secret-scan` job).

## 8. CI and releases

- Every GitHub Action is pinned to a commit SHA (version in a comment); workflows use `npm ci`;
  the e2e job waits with `curl` instead of downloading `wait-on`.
- Releases get a signed build-provenance attestation per file (`provenance` job, the only one with
  `id-token: write` and `attestations: write`). Check one with
  `gh attestation verify <file> --repo <owner>/AirBrief`.
- The `flightmd` extra installs FlightMD at a fixed commit.

## 9. Demo-video tool

- The Kokoro voice model is pinned to Hugging Face commit `1939ad2a8e…` (`voiceRevision` in
  `tools/demo-video/config.mjs`). The cached model files were checked against that commit's hashes
  and still load offline.
- `tools/demo-video/README.md` shows how to check a portable FFmpeg download against its published
  SHA-256.

## 10. Manual acceptance check

API in production mode (`APP_ENV=production CORS_ORIGINS=http://localhost:3000 uvicorn api.main:app --no-server-header`):

| Request | Expected |
|---|---|
| `curl -s -o /dev/null -w "%{http_code}" localhost:8000/docs` | `404` (`/redoc` too); `/openapi.json` is `200` |
| `curl -sI localhost:8000/health` | `strict-transport-security: max-age=31536000; includeSubDomains`; no `server` header |
| Same with `API_DOCS=true`, `curl -sI localhost:8000/docs` | `200`; `content-security-policy` with `script-src https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.33.1/swagger-ui-bundle.js 'sha256-…'` |
| Same with `ALLOWED_HOSTS=localhost`, `curl -s -H "Host: evil.example" localhost:8000/health` | `Invalid host header` (400) |

Web app (`npm run build && npm start -- -H 127.0.0.1`): `curl -sI localhost:3000/` twice shows a
`content-security-policy` with a different `'nonce-…'` each time and no `'unsafe-inline'` in
`script-src`; the browser console shows no CSP errors on `/`, `/w`, a dataset's workspace, the
report page and Settings, in both themes.
