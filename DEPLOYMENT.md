# AirBrief — Deployment

How to host AirBrief so other people can use it. To run and test it yourself, see `RUNBOOK.md`.

> **Cost statement:** every option below is ₹0. No API keys are required. Free cloud tiers are **optional** and their limits are stated plainly.
>
> **Native-first:** every option runs without Docker. If you prefer containers for self-hosting, `DOCKER.md` has a complete copy-paste setup; no Docker files are committed to the repository.

---

## 1. Options

| Option | What runs | Cost | Best for | Limits |
|---|---|---|---|---|
| **A. Self-host natively** | API (uvicorn) + web (Next.js) on one machine; SQLite by default, PostgreSQL optional | ₹0 (your hardware) | The full product; demos on your laptop or a home server | You keep the machine on |
| **B. GitHub Pages static demo** | Viewer + precomputed results for the samples; no backend | ₹0, forever | A public "click and see" link in your portfolio | Read-only; no uploads |
| **C. Free cloud tiers** (optional) | API on Render free, PostgreSQL on Neon free, frontend on Vercel Hobby or Pages | ₹0 within free limits | A live demo with uploads | Render free sleeps after inactivity (cold start ~30–60 s); free-tier quotas can change; no SLA |

**Recommendation:** use **B** for the portfolio link, **A** for the real thing, and **C** only if you want live uploads online.

---

## 2. Configuration (`.env`) **[M1]**

The API reads these from `.env` in the repository root (template: `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | `development` or `production` (production disables debug output, requires explicit `CORS_ORIGINS` and turns on the defaults in §8). Development answers only loopback and private addresses; anyone else gets `403 development_mode_local_only` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `DATABASE_URL` | `sqlite:///./data/airbrief.db` | SQLAlchemy URL. PostgreSQL: `postgresql+psycopg://user:pass@host:5432/airbrief` (install the `postgres` extra; PostGIS not needed). With `APP_ENV=production`, a server on the internet (a public name or address) needs `?sslmode=require` or `verify-full`, or start-up fails; names without a dot (`db` in Docker Compose), private addresses and `localhost` are taken as your own network |
| `AUTO_MIGRATE` | `true` | Run Alembic migrations when the API (or `python -m api.worker`) starts. With PostgreSQL in production, set `false` and migrate with a separate role (§3.6) |
| `DATA_DIR` | `./data` | Root for runtime data |
| `UPLOAD_DIR` | `./data/uploads` | Where uploads are stored |
| `DATAPACK_DIR` | `./data/datapacks` | Where seeded data packs live |
| `DEFAULT_DATAPACK` | *(empty)* | Data pack to prefer when several cover the inputs (`demo`, `bengaluru`). Empty: real packs win over the synthetic demo, then the most specific one |
| `STORAGE_QUOTA_MB` | *(empty)* | Total size of what is stored under `UPLOAD_DIR`; uploads past it get `507 storage_full`. Empty = unlimited in development, `5000` when `APP_ENV=production`; `0` = unlimited. The database is not counted |
| `UPLOAD_MB_PER_DAY` | *(empty)* | Upload bytes one client address may send in 24 hours (IPv6 per /64); past it `429 upload_budget_exceeded` with `Retry-After`; a file bigger than the whole allowance gets `413 file_too_large`, uploads without `Content-Length` get `411`, and an upload that is refused or fails gives its bytes back. Empty = unlimited in development, `1000` when `APP_ENV=production`; `0` = unlimited |
| `MIN_FREE_DISK_MB` | `500` | Uploads are refused (`507`) while the disk under `UPLOAD_DIR` has less free space; `0` = off |
| `MAX_UPLOAD_MB` | `50` | Upload size limit (413 above it) |
| `MAX_UNZIPPED_MB` | `200` | Zip-bomb guard |
| `MAX_ARCHIVE_FILES` | `200` | Zip entry limit |
| `MAX_FEATURES` | `50000` | Features read per upload |
| `INLINE_PROCESS_MAX_MB` | *(empty)* | Uploads up to this size are processed in the request (201); larger ones are queued (202). Empty = `5` in development, `0` when `APP_ENV=production` (every upload queued, so a hostile file never runs in a request thread) |
| `JOB_MODE` | `inprocess` | `inprocess` (API thread pool) or `external` (run `python -m api.worker`) |
| `WORKER_THREADS` | `2` | Threads for jobs, per process |
| `WORKER_POLL_INTERVAL_S` | `1` | Seconds an idle job runner waits before it looks for new jobs |
| `JOB_TIMEOUT_S` | *(empty)* | Wall-clock limit for one analysis; it stops at its next checkpoint as `FAILED` with code `job_timeout`. Empty = none in development, `900` when `APP_ENV=production`; `0` = none. Keep it **above 600 s** if people run one result per feature: such a run starts no feature after 600 s and needs time to finish the last one; below that, a large run is stopped and keeps nothing |
| `WORKER_MEMORY_LIMIT_MB` | *(empty)* | Address-space limit (`RLIMIT_AS`) of each `python -m api.worker`, so a runaway parse fails with `MemoryError` instead of exhausting the host. Linux/macOS only; empty or `0` = off |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated allowed origins. `POST`/`DELETE` requests carrying any other `Origin` get `403 cross_site_request`. When the app and the API share one domain, list that domain too: browsers send `Origin` on same-origin `POST`s |
| `RATE_LIMIT_PER_HOUR` | `120` | Per-IP limit for `POST` and `DELETE` under `/api/` (uploads, analyses, deletes); IPv6 clients count per /64 (0 = unlimited) |
| `CONTEXT_RATE_LIMIT_PER_HOUR` | *(empty)* | Per-IP limit on `GET /api/context/` and `/api/places/` together. Empty = unlimited in development, 3600 when `APP_ENV=production`; `0` = unlimited |
| `READ_RATE_LIMIT_PER_HOUR` | *(empty)* | Per-IP limit on every other `GET` under `/api/` (results, exports, listings, job polling). Empty = unlimited in development, 36000 when `APP_ENV=production`; `0` = unlimited |
| `TRUSTED_PROXY_HOPS` | `0` | Proxies in front of the API that append to `X-Forwarded-For`; the rate limiter keys on the address the outermost one saw. `0` = the peer address (right for Caddy/nginx on the same machine). Set `1` on Render. An entry with a port (`203.0.113.5:54321`, as Azure Application Gateway sends) counts by its address. Only set it when the API is reachable **only** through those proxies, or clients can forge their address |
| `UPLOAD_TTL_HOURS` | `0` (dev) / `72` (production, when unset) | Auto-delete uploads and their analyses after N hours (0 = keep forever) |
| `REQUIRE_OWNER_TOKEN` | *(empty)* | Deleting/cancelling needs the item's owner token (`X-Owner-Token`), held only by the creating browser. Empty = on in production. When on, `?dataset_id=` lists only the analyses run with the dataset's owner token (plus the caller's own `?ids=`), so a shared link never shows other visitors' runs |
| `MAX_JSON_BODY_MB` | `10` | Size limit for every non-upload POST body |
| `MAX_COORDINATES` | `5000000` | Total vertices per uploaded file |
| `ALLOW_GLOBAL_LISTING` | *(empty)* | Whether `GET /api/files/` and `GET /api/analyses/` may list everything. Empty = yes, except when `APP_ENV=production`. When off, each browser lists only the ids it created (`?ids=`), so visitors never see each other's uploads or audits |
| `API_DOCS` | *(empty)* | Serve `/docs` (Swagger UI) and `/redoc`. Empty = on in development, off when `APP_ENV=production`. These pages load scripts from `cdn.jsdelivr.net` (pinned versions, under a CSP that allows only those files); `/openapi.json` is always served |
| `ALLOWED_HOSTS` | *(empty)* | Comma-separated host names the API answers to (`TrustedHostMiddleware`); any other `Host` header gets `400`. Empty = any host. Include `127.0.0.1` if a local health check calls the API by address; IPv6 literals in brackets (`[::1]`) |
| `FLIGHTMD_API_URL` | *(empty)* | Optional default FlightMD API for the flight audit (users can override it in Settings). A base URL only: with a user name, password, `?query` or `#fragment` the API refuses to start. From its host only `{FLIGHTMD_API_URL}/report/{id}` is fetched (it may be plain http on a private address); `/api/meta/` shows only its scheme and host |
| `FLIGHTMD_FETCH_TIMEOUT_S` | `15` | Timeout for fetching a FlightMD report by URL (per request; each of at most 3 redirects gets its own) |
| `BRIEF_SIGNING_KEY_FILE` | *(empty)* | PEM Ed25519 private key that signs every brief bundle (§3.7). Empty = bundles are not signed. A missing or unusable key, or a missing `cryptography` package, stops the API at start-up |

The frontend reads these at **build** time (put them in `frontend/.env.local` or the build environment):

| Variable | Default | Meaning |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | API URL as seen by the browser |
| `NEXT_PUBLIC_BASEMAP` | `openfreemap` | `openfreemap` or `none` (offline) |
| `NEXT_PUBLIC_DEMO` | *(unset)* | `1` = static demo mode (reads `${basePath}/demo/*.json`, no API) |
| `NEXT_PUBLIC_BASE_PATH` | *(empty)* | Base path, e.g. `/AirBrief` for GitHub Pages |
| `NEXT_OUTPUT` | *(unset)* | `export` = static export into `frontend/out/` |

**Production minimum:** set `APP_ENV=production` and `CORS_ORIGINS` to your real web origin. That alone turns on owner tokens, scoped listings, 72 h retention, a 5,000 MB storage quota, a 900 s job time limit, queued uploads and HSTS, and turns off the API docs pages (§8). If you use PostgreSQL, use a strong password and a separate migration role (§3.6). To sign briefs, add a key (§3.7).

---

## 3. Option A — Self-host natively **[M1]**

On a Linux server (Ubuntu 22.04+ shown; any OS with Python 3.11+ and Node 20 works):

```bash
git clone https://github.com/<you>/AirBrief.git && cd AirBrief
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[api]"                      # add ,postgres for PostgreSQL
cp .env.example .env                         # edit as per §2 (APP_ENV=production, CORS_ORIGINS, ...)
python -m seed.datapack --synthetic --name demo          # offline demo pack
python -m seed.datapack --region bengaluru               # optional real pack [M4]; any other area: §3.9

# API (migrations run on start with AUTO_MIGRATE=true; or run `alembic upgrade head` first)
uvicorn api.main:app --host 127.0.0.1 --port 8000 --no-server-header --no-access-log

# Web (second shell)
cd frontend
npm ci                                       # exactly the versions in package-lock.json
NEXT_PUBLIC_API_URL=https://airbrief.example.com npm run build
npm start -- --hostname 127.0.0.1 --port 3000
```

Use **one** uvicorn process with SQLite and the in-process worker. For more throughput, switch to PostgreSQL, set `JOB_MODE=external`, and run one or more `python -m api.worker` processes (§3.4).

Both servers listen on `127.0.0.1` only; the reverse proxy (§3.2) is the only thing that faces the network. The `start` script in `frontend/package.json` has no host on purpose: `next start` alone listens on every interface, so always pass `--hostname` (here, in the systemd unit, and in `DOCKER.md`, which needs `0.0.0.0` inside the container). `--no-server-header` stops uvicorn from announcing itself (`server: uvicorn`).

### 3.1 Keep it running (systemd)

`/etc/systemd/system/airbrief-api.service`:

```ini
[Unit]
Description=AirBrief API
After=network.target

[Service]
User=airbrief
WorkingDirectory=/opt/airbrief
EnvironmentFile=/opt/airbrief/.env
ExecStart=/opt/airbrief/.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 --no-server-header --no-access-log
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/airbrief-web.service`:

```ini
[Unit]
Description=AirBrief web
After=network.target airbrief-api.service

[Service]
User=airbrief
WorkingDirectory=/opt/airbrief/frontend
Environment=NODE_ENV=production
ExecStart=/usr/bin/npm start -- --hostname 127.0.0.1 --port 3000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now airbrief-api airbrief-web
curl -s http://127.0.0.1:8000/health
```

On Windows, run the same two commands as scheduled tasks at startup, or use NSSM to wrap them as services.

### 3.2 HTTPS on your own domain (Caddy, free certificates)

1. Point a DNS name at the machine, e.g. `airbrief.example.com`.
2. Install Caddy (`sudo apt install caddy`) and put this in `/etc/caddy/Caddyfile`:

   ```caddyfile
   airbrief.example.com {
       encode zstd gzip
       # HTTPS only from now on. With APP_ENV=production the app and the API send this header too,
       # plus the other security headers.
       header Strict-Transport-Security "max-age=31536000; includeSubDomains"
       handle /api/* {
           reverse_proxy 127.0.0.1:8000
       }
       handle /health {
           reverse_proxy 127.0.0.1:8000
       }
       handle /openapi.json {
           reverse_proxy 127.0.0.1:8000
       }
       handle {
           reverse_proxy 127.0.0.1:3000
       }
   }
   ```

3. `sudo systemctl reload caddy`. Caddy obtains and renews the certificate itself.
4. In `.env`, set `CORS_ORIGINS=https://airbrief.example.com` (and, optionally, `ALLOWED_HOSTS=airbrief.example.com,127.0.0.1`). Build the web app with `NEXT_PUBLIC_API_URL=https://airbrief.example.com`, then restart both services. The app and the API share this one origin, and it must still be in `CORS_ORIGINS`: browsers send `Origin` on same-origin `POST`s too, and the API refuses changes from any origin not listed (`403 cross_site_request`).

> **Keep `/docs` off this origin.** The Caddyfile above does not route `/docs` or `/redoc`, and production turns them off anyway. Those pages run Swagger UI and ReDoc scripts from `cdn.jsdelivr.net`; on the app's origin they could read the owner tokens the web app keeps in `localStorage` if that CDN were ever compromised. If you want the docs on a public instance, set `API_DOCS=true` and give them their own host name, so they run on a different origin:
>
> ```caddyfile
> api-docs.airbrief.example.com {
>     reverse_proxy 127.0.0.1:8000
> }
> ```
>
> (add that name to `ALLOWED_HOSTS` if you set it). Each visit to the docs pages also tells jsDelivr the visitor's address.

> **Keep `geolocation=(self)`.** The web app sends `Permissions-Policy: camera=(), microphone=(), geolocation=(self), payment=()` (`frontend/next.config.mjs`). **My location** in the plan toolbar needs `geolocation=(self)`: a proxy that sets its own `Permissions-Policy` (or a hardening preset with `geolocation=()`) must keep it, or the browser blocks the request and the button says so. The location API also needs HTTPS (or localhost). The Caddyfile above passes the app's header through unchanged. The position stays in the browser until the user saves the operation, which uploads it to this server only.

> **No domain?** A free tunnel such as Cloudflare Tunnel (free plan) can expose the machine without opening ports.

### 3.3 Updates and backups

```bash
# Update
git pull
source .venv/bin/activate && pip install -e ".[api]"
(cd frontend && npm ci && npm run build)
sudo systemctl restart airbrief-api airbrief-web     # migrations run on API start

# Back up SQLite + uploads + data packs (stop the API first for a consistent copy)
sudo systemctl stop airbrief-api
tar czf airbrief_data_$(date +%F).tgz data/
sudo systemctl start airbrief-api

# PostgreSQL instead
pg_dump -Fc airbrief > airbrief_$(date +%F).dump
pg_restore -d airbrief --clean airbrief_YYYY-MM-DD.dump
```

After an update that adds data-pack layers, rebuild the packs (the synthetic one with the command in §3, real ones with §3.9). The 2026-10-23 update (zone engine v2) added `aerodrome_perimeters` and `runways`; an older pack still works, but every DGCA band is then measured from the aerodrome's reference point with the warning `perimeter_approximated`.

Backups and server logs are outside `UPLOAD_TTL_HOURS`: an upload deleted after 72 h stays in every archive and log made before that. Give them their own retention (delete old archives, rotate logs), and encrypt archives you keep off the server. A nightly job that does all three, `/etc/cron.daily/airbrief-backup` (needs [`age`](https://github.com/FiloSottile/age), BSD-licensed, `apt install age`; the private half of the key stays off the server):

```bash
#!/bin/sh
set -eu
dest=/var/backups/airbrief
mkdir -p "$dest"
pg_dump -Fc airbrief | age -r "$(cat /etc/airbrief/backup.pub)" > "$dest/airbrief_$(date +%F).dump.age"
find "$dest" -name 'airbrief_*.age' -mtime +14 -delete   # keep two weeks
```

With SQLite, stop the API around a `tar czf - data/ | age -r … > …` instead of `pg_dump`. Make the key once with `age-keygen -o backup.key` on another computer, copy only its `public key:` line to `/etc/airbrief/backup.pub`, and restore with `age -d -i backup.key airbrief_….dump.age | pg_restore -d airbrief --clean`. For the logs, `MaxRetentionSec=14day` in `/etc/systemd/journald.conf` (then `systemctl restart systemd-journald`) keeps the API's journal to two weeks, and Caddy's `log { output file … { roll_keep_for 336h } }` does the same for its access log. On a public host also turn on full-disk encryption (LUKS, or the cloud provider's encrypted volumes): AirBrief does not encrypt uploads or the database itself.

The `data/` archive holds no signing key, because the key lives outside `data/` (§3.7). Back the key up on its own.

### 3.4 Scaling notes

- Jobs live in the database. With `JOB_MODE=external`, start as many `python -m api.worker` processes as you like. A job is claimed by a compare-and-set `UPDATE … WHERE status='PENDING'`, so no two workers take the same job.
- Use PostgreSQL for several workers or many concurrent users; SQLite allows one writer at a time.
- Data packs are read-only and can be shared between processes and machines (for example over a read-only mount).
- Workers parse uploads and run analyses, so they are where hostile files land. On Linux, set `WORKER_MEMORY_LIMIT_MB` (for example `4096`) so a runaway parse fails with `MemoryError` instead of exhausting the host; `JOB_TIMEOUT_S` applies to the analyses they run. Uploads have their own caps (zone files: 5,000 zones after the split into volumes and periods), but the memory limit is the backstop for anything the caps miss.
- A CPU limit belongs to the operating system, not to the worker: `RLIMIT_CPU` counts the process's whole life, so it would stop a healthy worker after a few hours. Run each worker as a systemd service with its own quota, `/etc/systemd/system/airbrief-worker@.service`:

  ```ini
  [Unit]
  Description=AirBrief job worker %i
  After=network.target postgresql.service

  [Service]
  User=airbrief
  WorkingDirectory=/opt/airbrief
  EnvironmentFile=/opt/airbrief/.env
  ExecStart=/opt/airbrief/.venv/bin/python -m api.worker
  Restart=on-failure
  # hostile uploads are parsed here
  CPUQuota=200%
  MemoryMax=4G
  TasksMax=64
  NoNewPrivileges=true
  PrivateTmp=true
  ProtectSystem=strict
  ProtectHome=true
  ReadWritePaths=/opt/airbrief/data

  [Install]
  WantedBy=multi-user.target
  ```

  `sudo systemctl enable --now airbrief-worker@1 airbrief-worker@2` starts two. `MemoryMax` stops the whole worker when it is exceeded, so keep `WORKER_MEMORY_LIMIT_MB` a little lower: then a runaway job fails on its own with `MemoryError` and the worker keeps going.
- One result per feature runs as one job: about a minute for 1,000 small plots in most modules on the synthetic pack, about 6 minutes for 500 line-of-sight features, and no new feature after 600 s. With the default `WORKER_THREADS=2`, two long runs hold every job thread; raise the thread count or run more workers if people use it a lot.

### 3.5 Docker (alternative)

If you prefer containers, follow `DOCKER.md`. It creates `Dockerfile.api`, `Dockerfile.web`, `docker-compose.yml`, an optional `docker-compose.postgres.yml` and `.dockerignore` on your machine, with the same settings as §2. Its ports listen on `127.0.0.1` only and its containers run as unprivileged users; `DOCKER.md` §6.1 shows the production settings for an instance other people can reach, and §6.2 mounts a brief signing key from the host, outside the data volume.

### 3.6 PostgreSQL: a separate role for migrations

The API needs to read and write rows, never to change tables. With PostgreSQL in production, run it with a role that has no DDL rights, and apply migrations with a second role only when you deploy. Then a bug in the API (or someone who gets its password) cannot drop or alter tables.

Once, as a PostgreSQL superuser (`psql`; on Neon, create the two roles in the console and run the rest in its SQL editor):

```sql
CREATE ROLE airbrief_owner LOGIN PASSWORD 'CHANGE_ME_owner';   -- runs migrations only
CREATE ROLE airbrief_app   LOGIN PASSWORD 'CHANGE_ME_app';     -- what the API uses
CREATE DATABASE airbrief OWNER airbrief_owner;
\c airbrief
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO airbrief_app;
-- every table and sequence the owner's migrations create, now and later:
ALTER DEFAULT PRIVILEGES FOR ROLE airbrief_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO airbrief_app;
ALTER DEFAULT PRIVILEGES FOR ROLE airbrief_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO airbrief_app;
```

Replace both `CHANGE_ME_…` values with long random passwords. Then:

```bash
# .env of the API and of every worker: the app role, no migrations on start
AUTO_MIGRATE=false
DATABASE_URL=postgresql+psycopg://airbrief_app:<app password>@db.example.com:5432/airbrief?sslmode=require

# On each deploy, before restarting the API: migrate as the owner (the variable overrides .env)
DATABASE_URL=postgresql+psycopg://airbrief_owner:<owner password>@db.example.com:5432/airbrief?sslmode=require alembic upgrade head
```

If you created tables with another role before, grant the app role access to them once: `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO airbrief_app; GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO airbrief_app;` (as their owner). SQLite has no roles: keep `AUTO_MIGRATE=true` there, and protect the database file with file permissions (`User=airbrief` in §3.1).

**Rotating the passwords** (yearly, when someone who knew them leaves, or at once if one leaked). Do the app role first, with no downtime:

1. As a superuser, set a new password: `ALTER ROLE airbrief_app PASSWORD '<new>';`. Open connections stay logged in; new ones need the new password.
2. Put it in `DATABASE_URL` in the `.env` of the API and of every worker, then restart them one by one (`sudo systemctl restart airbrief-api airbrief-worker@1 …`).
3. Check `curl -s http://127.0.0.1:8000/health` shows `"db": "ok"`, then end any session still on the old login: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'airbrief_app' AND backend_start < now() - interval '10 minutes';`.

The owner role is only used while deploying: `ALTER ROLE airbrief_owner PASSWORD '<new>';` and update wherever your deploy step keeps it. On Neon, use "Reset password" on each role in the console, then the same steps 2–3. After a leak, also read the PostgreSQL log for logins you did not make (`log_connections = on`).

PostgreSQL does not overwrite deleted rows at once: they stay in the table files until `VACUUM` reuses the space, and in the WAL and in backups for their retention time. To make sure deleted uploads are gone from the database files, run `VACUUM (FULL) features, analyses, datasets;` in a quiet hour (it locks the tables while it runs), and give backups their own retention (§3.3). SQLite overwrites deleted rows itself (`secure_delete`).

### 3.7 Signing brief bundles

An instance can sign every brief bundle it builds, so whoever receives a bundle can check that it came from you and was not edited. Signing needs the `cryptography` package, which the `api` extra installs.

Create the key once, as the user the API runs as, outside the repository and outside `data/`:

```bash
sudo install -d -o airbrief -m 700 /etc/airbrief
sudo -u airbrief /opt/airbrief/.venv/bin/python -m airbrief_core.verify --new-key /etc/airbrief/brief-signing-key.pem
```

It writes an unencrypted PEM (PKCS#8) Ed25519 key that only its owner can read (on POSIX), never overwrites an existing file, and prints the key id and the public key. Add the path to `.env` and restart the API:

```ini
BRIEF_SIGNING_KEY_FILE=/etc/airbrief/brief-signing-key.pem
```

- **Publish the public key** where recipients already trust you: your website, a signed email, a contract annex. `GET /api/meta/` (`signing.public_key`) and the Settings page show it too, but a key read from the server a bundle points to proves nothing. Recipients run `pip install "airbrief-core[signing]"`, then `python -m airbrief_core.verify <bundle.zip> --public-key <key>` (`docs/features/evidence-integrity.md`).
- **Back it up** on its own (an encrypted offline copy or a password manager), not in the `data/` archive of §3.3. Never commit it: `.gitignore` covers `*.pem` and `*signing-key*`, but keep it outside the repository folder anyway.
- **Rotate** by creating a new key, pointing `BRIEF_SIGNING_KEY_FILE` at it and publishing its public key next to the old one. Bundles signed before stay verifiable with the old key. If a key leaks, also publish the date from which it must no longer be trusted.

Use one key per instance. The key id is the first 16 hex characters of the SHA-256 of the raw public key.

### 3.8 Editing a data pack

Each pack's `manifest.json` records the SHA-256 of every layer file and a `content_sha256` over all of them, and the API checks the files when it opens the pack and whenever one changes. Real packs ship an empty `zones.geojson` for you to fill with restricted areas. After editing it, or any other layer, seal the pack, or the API refuses it:

```bash
python -m seed.datapack --seal data/datapacks/<name>
```

The command prints the new `content_sha256`, and the API picks the change up without a restart. Publish that value if others compare briefs against your pack: briefs list it, and the hashes alone do not show who built a pack.

Settings → **Data packs and limits** shows each pack's state:
- "Files match their hashes" (`verified`).
- "No hashes to check" (`unverified`): a pack built before 2026-10-19. It is used, but every analysis on it carries the warning `datapack_unverified` until you rebuild it.
- "Files changed: not used" (`mismatch`): analyses that read the pack fail with `datapack_integrity`, and the server log names the files.

Red zones are not published as open data. To check against them, put the polygons you have into the pack's `zones.geojson` (then seal it), or let users load them per analysis as zones from layers (ED-269/ED-318 files or any polygons). Pack zones are read like zone datasets, so they can carry `valid_from`/`valid_to`, a `schedule` and vertical limits. Imported zone files keep their own licences.

### 3.9 A data pack for another region

`--region bengaluru` is a named preset. For any other area of at most 2° × 2°:

```bash
python -m seed.datapack --bbox 85.1,27.5,85.6,27.9 --name kathmandu --iso3 NPL [--lite] [--dest DIR]
```

- `--name` is required; `--iso3` (ISO 3166-1 alpha-3) picks the country's WorldPop 2020 1 km file. Without it the pack has no population layer and the command exits with status 1 after building the rest; ground risk there then needs another pack.
- It downloads OurAirports (aerodromes, `scheduled_service`, runways), aerodrome boundaries and the other OSM layers through the Overpass API (with mirrors), Copernicus DEM tiles (GLO-90 with `--lite`) and the WorldPop window. Nothing needs a key. Aerodromes and boundaries are read over the bbox plus 0.3°.
- A larger bbox is refused: Overpass time limits and DEM size make it slow and fragile. Split the area into several packs; the API picks the pack that covers each analysis (`DEFAULT_DATAPACK` breaks ties).
- The pack is hashed like the others. Publish its `content_sha256` if others compare briefs against it, and seal it after any edit (§3.8).
- Check the result: Settings → **Data packs and limits** lists it as "Files match their hashes", and an airspace check near an airport shows bands that follow the runways. Where OSM has no boundary, the bands come from the runways (note `perimeter_from_runways`) or the reference point (warning `perimeter_approximated`).

---

## 4. Option B — GitHub Pages static demo **[M7]**

The workflow `.github/workflows/pages.yml` builds and deploys the demo with GitHub Actions:

1. Seeds the synthetic data pack: `python -m seed.datapack --synthetic --name demo`.
2. Runs every sample through the API and writes the responses as static JSON: `python -m seed.demo_export --out frontend/public/demo` (locally, add `--datapack-dir data/datapacks` to reuse the pack from step 1). This includes `demo/context.json` (context layers and "Go to" for the pack's area) and `demo/briefs/{id}.json` (one brief per analysis, merged in the browser).
3. Builds the frontend as a static export: `NEXT_PUBLIC_DEMO=1 NEXT_PUBLIC_BASE_PATH=/<repo> npm run export` → `frontend/out/`. To build it yourself in Git Bash on Windows, prefix `MSYS_NO_PATHCONV=1`; otherwise Git Bash turns `/<repo>` into a Windows path and the build stops with "basePath has to start with a /".
4. Uploads `frontend/out/` and deploys it to Pages.

**One-time setup:** on GitHub, **Settings → Pages → Build and deployment → Source: GitHub Actions**.

**Trigger:** push a tag `v*`, or run the workflow by hand (**Actions → Pages (static demo) → Run workflow**). The site appears at `https://<you>.github.io/<repo>/`.

**Build it locally** (to check before publishing):

```bash
make demo                                   # same three commands; REPO_NAME=AirBrief by default
npx --yes serve@14.2.4 frontend/out         # then open the printed URL + /AirBrief/
```

Routes are query-param based (`/w/view?d=…`, `/w/report?d=…&a=…`), so every page works from static files. The demo shows the workspace with all samples preloaded. Uploading, running new analyses, saving a drawn operation, reading WKT and downloading a brief bundle are disabled (they need the API), and a banner links to the self-host instructions. Context layers, "Go to", typed coordinates, pasted GeoJSON and the one-page brief of the demo analyses work in the browser. The demo's context rings use the default rules (DGCA 2021) only, and it has no per-feature run yet. A demo exported before 2026-10-23 shows its flight audits and eVTOL runs as "conservative rules"; export it again.

---

## 5. Option C — Free cloud tiers (optional) **[M2+]**

| Part | Service | Steps |
|---|---|---|
| Database | **Neon** (free) | Create a project → copy the connection string into `DATABASE_URL`, with the `postgresql+psycopg://` prefix and `?sslmode=require`. No extension is needed. |
| API | **Render** (free web service, native Python runtime) | New → Web Service → select the repo. **Build command:** `pip install -e ".[api,postgres]" && python -m seed.datapack --synthetic --name demo`. **Start command:** `uvicorn api.main:app --host 0.0.0.0 --port $PORT --no-server-header --no-access-log`. Set the env vars from §2 (`APP_ENV=production`, `DATABASE_URL`, `CORS_ORIGINS`, `DEFAULT_DATAPACK=demo`, `UPLOAD_TTL_HOURS=24`, `TRUSTED_PROXY_HOPS=1`). Without `TRUSTED_PROXY_HOPS=1` every request appears to come from Render's proxy, so all visitors share one rate-limit allowance. Migrations run on start (`AUTO_MIGRATE=true`); for a database you keep, use the two roles of §3.6 instead. |
| Frontend | **Vercel** Hobby (free) or GitHub Pages | Import the repo with root directory `frontend/`; set `NEXT_PUBLIC_API_URL` to the Render URL. |

**Caveats:**
- The free API sleeps after inactivity, so the first request takes about 30–60 s.
- Render free has an ephemeral disk. Uploads are lost on redeploy, so set `UPLOAD_TTL_HOURS=24` and treat the instance as a demo. The synthetic pack is rebuilt on every deploy by the build command.
- Data packs on free tiers: prefer the synthetic `demo` pack, or `--region bengaluru --lite` (a reduced-resolution pack of about 150 MB) if the disk allows.
- SQLite on an ephemeral disk loses data on every deploy; use Neon for anything you want to keep.
- Production caps stored uploads at 5,000 MB and keeps 500 MB of disk free. On a smaller disk, set `STORAGE_QUOTA_MB` below what the instance has.
- Free instances have little CPU and memory. Per-feature runs of hundreds of features take several minutes there and still stop starting features at 600 s; keep `JOB_TIMEOUT_S` at its 900 s default.
- Free-tier terms can change. Options A and B always remain available.

---

## 6. CI/CD **[M1]**

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | every push / PR | `python` (Python 3.11 and 3.12 × SQLite and a `postgres:16` service: ruff, mypy, migrations, pytest with coverage), `native` (build the C++ kernel + parity tests), `contracts` (buf lint + build), `security` (pip-audit, bandit, `npm audit` of production packages), `secret-scan` (gitleaks over every commit), `web` (lint, typecheck, build), `e2e` (synthetic pack + API + production web build + Playwright/axe; uploads the report) |
| `pages.yml` | tag `v*` or manual | Build the static demo → deploy to Pages |
| `release.yml` | tag `v*` | Build the `airbrief-core` sdist + wheel and the C++ kernel wheels for Linux/macOS/Windows (cibuildwheel), attest their build provenance, and attach them to the GitHub Release |

GitHub Actions is free for public repositories. Releases: bump `version` in `pyproject.toml`, commit, then `git tag v0.1.0 && git push --tags`; the release job checks that the tag matches the version.

**Supply chain.** Every action is pinned to a full commit SHA, with its version in a comment (`uses: actions/checkout@11d5960… # v4.4.0`), because a tag can be moved to other code. To update one, look up the new tag's commit (`git ls-remote https://github.com/actions/checkout refs/tags/v4.5.0`; for an annotated tag use the `^{}` line) and replace both; Dependabot's `github-actions` updates do this for you once the repository is on GitHub. The workflows install Node packages with `npm ci` (exactly `package-lock.json`, never a newer version). The optional `flightmd` extra is pinned to a FlightMD commit in `pyproject.toml`. Python packages are still lower bounds without hashes; for a server, consider a hashed lock file (`pip-compile --generate-hashes`).

**Release provenance.** The `provenance` job (the only one with `id-token: write` and `attestations: write`) signs a SLSA build-provenance attestation for every file in the release through Sigstore. Anyone can check a downloaded file was built by this repository's workflow from the tagged commit:

```bash
gh attestation verify airbrief_core-0.1.0-py3-none-any.whl --repo <you>/AirBrief
```

Attestations work for public repositories on every GitHub plan.

---

## 7. Publishing the repository (checklist)

Do this only when you decide to publish.

- [ ] `LICENSE` (MIT) and `DATA_LICENSES.md` are present and correct
- [ ] No secrets in the repo: scan with gitleaks before the first commit (below). `.gitignore` already keeps out `.env`, `.env.*` and `*.env` (`.env.example` stays tracked), `*.pem`, `*.key`, `*.p12`, `*.pfx`, `secrets.*` and `*signing-key*` (still, keep the brief signing key outside the folder); `.env.example` holds only defaults and `CHANGE_ME` placeholders
- [ ] No large data committed (data packs are downloaded, not committed; `.gitignore` covers `data/` and generated sample archives)
- [ ] No Docker files committed (by design; they live only in `DOCKER.md`)
- [ ] README badges point at the right repo
- [ ] Create the repo, scanning before anything is committed or pushed:

1. Get gitleaks (MIT, a single binary): download `gitleaks_<version>_<os>_<arch>` and `gitleaks_<version>_checksums.txt` from https://github.com/gitleaks/gitleaks/releases, check the archive against its line in the checksums file, and unpack it outside the repository.

   ```bash
   sha256sum -c --ignore-missing gitleaks_8.30.1_checksums.txt          # Linux / Git Bash: must print "OK"
   ```

   ```powershell
   (Get-FileHash .\gitleaks_8.30.1_windows_x64.zip -Algorithm SHA256).Hash   # compare with the checksums file
   ```

2. Scan exactly what the first commit will contain, then commit, then scan the history you are about to push:

   ```bash
   git init && git add .
   gitleaks git --pre-commit --staged --redact --verbose   # exit code 0 = no findings
   git commit -m "Initial commit"
   gitleaks git --redact --verbose
   ```

   On a finding, `git rm --cached <file>` (and add it to `.gitignore`) or remove the value, then scan again. If a real secret was ever committed, rotate it: history is permanent once pushed.

3. Push:

   ```bash
   gh repo create AirBrief --public --source . --remote origin --push
   # or create the empty repo on github.com, then:
   git remote add origin https://github.com/<you>/AirBrief.git && git push -u origin main
   ```

- [ ] In the repository's settings on GitHub, turn on secret scanning and push protection (free for public repositories). The `secret-scan` CI job also runs gitleaks on every push and pull request
- [ ] Set Pages to "GitHub Actions" (§4) and check the CI is green
- [ ] Add topics: `geospatial`, `gis`, `uav`, `drones`, `evtol`, `fastapi`, `sqlite`, `maplibre`, `h3`

---

## 8. Security notes for public instances

`APP_ENV=production` switches on safe defaults. The full threat model, the list of controls and how to report a vulnerability are in [`SECURITY.md`](SECURITY.md).

| Setting | Production default | What it protects |
|---|---|---|
| `ALLOW_GLOBAL_LISTING` | off | Visitors only list the datasets and analyses their own browser created (`?ids=`), never other people's |
| `REQUIRE_OWNER_TOKEN` | on | Deleting or cancelling needs the item's owner token, which only the creator's browser holds. A shared link lets people *view*, not delete |
| `UPLOAD_TTL_HOURS` | 72 (when not set) | Uploads, features and analyses are deleted automatically; `0` keeps them forever |
| `CORS_ORIGINS` | must be set | Only your web origin may call the API from a browser |
| `RATE_LIMIT_PER_HOUR` | 120 | Uploads, analyses and deletes per client IP (IPv6: per /64) |
| `STORAGE_QUOTA_MB` / `MIN_FREE_DISK_MB` | 5000 (when not set) / 500 | One client cannot fill the disk: uploads past the quota, or that would leave less free space, get `507 storage_full` |
| `UPLOAD_MB_PER_DAY` | 1000 (when not set) | One address cannot take the whole quota: with the 72 h expiry it holds at most 3,000 MB of the 5,000 |
| `JOB_TIMEOUT_S` | 900 (when not set) | A slow or hostile analysis cannot hold a job thread for ever (`job_timeout`) |
| `INLINE_PROCESS_MAX_MB` | 0 (when not set) | Every upload is parsed by the job runner, never inside a request |
| `WORKER_MEMORY_LIMIT_MB` | off | Caps each `python -m api.worker` (Linux/macOS); set it with `JOB_MODE=external` |
| `CONTEXT_RATE_LIMIT_PER_HOUR` | 3600 (when not set) | Context-layer and place-search reads per client IP (one a second on average; the browser caches each view for 5 minutes) |
| `READ_RATE_LIMIT_PER_HOUR` | 36000 (when not set) | Every other read per client IP: ten a second on average. The web app polls each running analysis once a second and its lists every two, so a dozen queued runs stay under it; behind a shared NAT, raise it |
| `TRUSTED_PROXY_HOPS` | 0 | Set to the number of proxies that append to `X-Forwarded-For` when the API is only reachable through them (Render: `1`); otherwise all visitors share the proxy's limit |
| `MAX_UPLOAD_MB` / `MAX_JSON_BODY_MB` / `MAX_COORDINATES` | 50 / 10 / 5,000,000 | Oversized or overly complex requests are refused before heavy work starts |
| `API_DOCS` | off (when not set) | `/docs` and `/redoc` load third-party scripts from `cdn.jsdelivr.net`; keep them off the app's origin (§3.2) |
| `ALLOWED_HOSTS` | *(empty: any host)* | When set, requests for any other host name get `400` (stops Host-header tricks and stray DNS names pointing at your server) |
| HSTS | on | `Strict-Transport-Security: max-age=31536000; includeSubDomains` on API and `next start` responses (browsers ignore it over plain http) |
| `BRIEF_SIGNING_KEY_FILE` | *(empty: unsigned)* | Recipients can check a bundle came from this instance unedited (§3.7) |

In development mode the API answers only loopback and private addresses, logs a warning at start-up naming the protections that are off, and the web app shows a warning strip when opened from another host. That keeps a forgotten `APP_ENV` from opening a public server, but anyone on the same LAN can still list and delete everything.

**Endpoints and parameters added since the 2026-10 upgrade, and their limits** (fixed in code, not settings):

| Endpoint | Limits | Rate-limited? |
|---|---|---|
| `GET /api/context/` | View at most 2° × 2° (`422 area_too_large`); at most 5,000 features and 100,000 vertices per layer; `bbox` and `layers` at most 200 characters; ETag and `Cache-Control: public, max-age=300` | Yes: `CONTEXT_RATE_LIMIT_PER_HOUR` (shared with places) |
| `GET /api/places/` | Query 1–200 characters; at most 25 results; installed packs' aerodromes only (no online geocoder) | Yes: `CONTEXT_RATE_LIMIT_PER_HOUR` |
| `POST /api/geometry/parse/` | 100 kB of text, 10,000 vertices and 32 levels of nesting (`too_complex`); `MAX_JSON_BODY_MB` | Yes |
| `POST /api/briefs/` | 1–12 analysis ids; operator ≤ 200, reference ≤ 120, notes ≤ 2,000 characters; nothing stored | Yes |
| `POST /api/briefs/bundle/` | Same body; at most 100,000 rows per CSV table and 200 MB uncompressed (`413 bundle_too_large`); built per request in memory, spilling to a temporary file above 16 MB, then streamed; `zones.ed318.geojson` is hashed and signed with the rest | Yes |
| `POST /api/analyses/` with `zone_dataset_ids` | At most 10 datasets, read by id; only polygons within ≈ 1 km of the checked area; at most 5,000 zones (`422 too_many_zones`); a request's own `dataset_zones` are replaced | Yes (POST) |
| `POST /api/analyses/` with `batch` | At most 1,000 features (line of sight 500, `422 too_many_features`); no feature started after 600 s; one job | Yes (POST) |
| `GET /api/analyses/{id}/export/?format=ed318`, `GET /api/files/{id}/export/?format=ed318` | The result's zones layer, or every polygon of the dataset, built in memory per request | No (like other exports) |
| `GET /api/context/?profile=` | `dgca_2021` or `conservative_illustrative` only (`422` otherwise); part of the ETag | Yes: `CONTEXT_RATE_LIMIT_PER_HOUR` |

The two GET endpoints are cheap per request but anyone can call them, and a context response can be megabytes, so in production they share a per-IP allowance (`CONTEXT_RATE_LIMIT_PER_HOUR`, 3,600 an hour by default; answers `429 rate_limited` with `Retry-After`). The limiter is per process and counts over an hour: for burst control, or across several API processes, also rate-limit them at the reverse proxy (for example Caddy's `rate_limit` plugin or nginx `limit_req`). Brief bundles are the heaviest POSTs; the per-IP POST limit (`RATE_LIMIT_PER_HOUR`) covers them.

Also:
- **Uploads** are checked by extension, magic bytes and size, both from `Content-Length` and while streaming.
- **Archives** are checked for zip-slip, symlinks, zip bombs and entry counts.
- **KML** is parsed with entity expansion and network access off.
- **Isolation:** files are processed in a private working directory and never executed.
- **Internal details** (stack traces, file paths, library messages) stay in the server log; the API returns generic messages.
- **FlightMD links** are fetched server-side only over `https` on port 443, from public IP addresses (NAT64 and other IPv6 forms of IPv4 addresses are refused; the connection is pinned to the checked IP; redirects are re-checked), with a 5 MB cap and a timeout. Only responses that parse as FlightMD reports are returned. A refusal tells the client only which rule or stage failed; the host, address and error text go to the server log (logger `airbrief.api.fetch`, INFO). The `FLIGHTMD_API_URL` host is the one exception to https and public addresses, and only `…/report/{id}` is fetched from it.
- **Analysis parameters** are held to the pasted-geometry caps before anything parses them: 32 levels of nesting and 100,000 positions per GeoJSON object (`422 too_complex`). Larger geometry belongs in an upload.
- **Zone files** (ED-269/ED-318 uploads) are hostile input with their own caps: 100 volumes and 10 periods per zone, 5,000 zones in all after the split into one zone per volume and period (counted before any is built), 1,000,000 vertices, 32 levels of nesting. Parsing an upload still has no time limit and, by default, runs on the API's own threads: on a busy public instance prefer `JOB_MODE=external` with `WORKER_MEMORY_LIMIT_MB` (§3.4).
- **Zones from other datasets:** `zone_dataset_ids` read a dataset by its id like any other read, and copy its polygons into the new analysis, where they stay until that analysis expires or is deleted, even if the zone dataset is deleted first.
- **ED-318 exports** of an analysis carry the evidence notice and, for synthetic data, the watermark in a top-level `airbrief` member, as the bundle's `zones.ed318.geojson` does. Tools that read only the features do not show it, so a downloaded `.ed318.geojson` is still evidence, not an official zone list. A dataset's ED-318 export is the user's own polygons and has no such member.
- **Default rules:** airspace, flight audit, eVTOL and the context rings use the DGCA 2021 bands from the airport perimeter unless a run picks another profile. Results stored before profiles are read with the conservative rules they ran with; they do not change.
- **Cross-site writes:** `POST` and `DELETE` requests whose `Origin` is not in `CORS_ORIGINS` get `403 cross_site_request`, so another web site cannot make a visitor's browser upload or delete.
- **Security log:** every refusal (403, 413, 429, 507 and `url_not_allowed`) writes one WARNING line on the `airbrief.security` logger, for example `rate_limited 429 client=3f9c0a51d2e7 path=/api/files/`. The client key is a salted hash of the address (the salt changes on restart); query strings, headers, tokens and bodies are never logged. Filter the API's output on ` airbrief.security: ` (or route that logger) to watch for probing.
- **Data packs** are checked against their SHA-256 hashes when opened; a changed pack is refused until you seal it (§3.8).
- **Security headers:** the API and `next start` send `nosniff`, no framing, `no-referrer` and a Content-Security-Policy, plus HSTS in production; `next start` also sends `Permissions-Policy: camera=(), microphone=(), geolocation=(self), payment=()`. The web app's CSP comes from `frontend/middleware.ts` with a new nonce per request: only scripts carrying that nonce (Next's own and the theme script) run, so an injected inline script would not. This makes every page render per request. API responses are `no-store` (context responses are cacheable for 5 minutes). **A static export cannot send headers:** set `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a CSP and the same `Permissions-Policy` (keeping `geolocation=(self)`) on the web server (GitHub Pages cannot; it only serves the read-only demo). A static export has no per-request nonce, so its CSP needs `script-src 'self' 'unsafe-inline'`. Its build prints "Statically exporting … disables API routes and middleware": expected, the middleware only matters for `next start`.
- **Network exposure:** bind uvicorn and Next.js to `127.0.0.1` behind the reverse proxy (§3.2), expose only ports 80/443, and keep uvicorn's default `--forwarded-allow-ips 127.0.0.1` with `TRUSTED_PROXY_HOPS=0`. Then the rate limiter sees real client IPs and nobody can spoof `X-Forwarded-For`. On a hosted platform whose proxy addresses change (Render), use `TRUSTED_PROXY_HOPS=1` instead: only the address the platform's proxy appended is trusted, never values the client sent. Start uvicorn with `--no-server-header`, and set `ALLOWED_HOSTS` to your host name(s).
- **What you upload:** there are no accounts. Anyone who has an id (for example from a shared link) can open that item. Don't upload sensitive or personal data to a public instance.
