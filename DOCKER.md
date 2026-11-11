# AirBrief with Docker (optional)

> **No Docker files are committed to this repository, by design.** The supported way to run
> AirBrief is native: Python 3.11+ and Node 20, with SQLite by default (see `RUNBOOK.md`). This page
> holds everything you need to run it in Docker **on your own machine** instead: five files to
> create (`Dockerfile.api`, `Dockerfile.web`, `docker-compose.yml`, `docker-compose.postgres.yml`,
> `.dockerignore`), one script that writes them all, and the commands to use them. `.gitignore`
> already excludes these files, so they stay out of commits.

> **Safe by default:** the containers publish their ports on `127.0.0.1` only, run as unprivileged
> users, and PostgreSQL has no default password. To let other people use the app, put a reverse
> proxy with HTTPS in front and switch to production settings ([§6.1](#61-reachable-by-others)).

---

## Contents

1. [What you get](#1-what-you-get)
2. [Requirements](#2-requirements)
3. [Create the files in one step](#3-create-the-files-in-one-step)
4. [The files, one by one](#4-the-files-one-by-one)
5. [Commands](#5-commands)
6. [Configuration](#6-configuration)
7. [Volumes, backups and reset](#7-volumes-backups-and-reset)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. What you get

| Service | Image | Port | Notes |
|---|---|---|---|
| `api` | built from `Dockerfile.api` (python:3.12-slim) | 127.0.0.1:8000 | FastAPI; migrations run on start (`AUTO_MIGRATE=true`); SQLite in the `airbrief_data` volume by default; runs as user `airbrief` |
| `web` | built from `Dockerfile.web` (node:20, multi-stage) | 127.0.0.1:3000 | Next.js production server; runs as user `node` |
| `db` | `postgres:16` | none (internal) | **Optional**, only with `docker-compose.postgres.yml`; the API reaches it over the Compose network |

Data (database file, uploads, data packs) lives in the named volume `airbrief_data`, mounted at
`/data` in the `api` container.

---

## 2. Requirements

| Tool | Minimum | Check with |
|---|---|---|
| Docker Engine or Docker Desktop | 24 | `docker --version` |
| Docker Compose | v2 | `docker compose version` |
| Disk | about 3 GB for images, plus about 1.5 GB if you download the real Bengaluru data pack | |

On Windows, run the script in section 3 from **Git Bash** or **WSL** (it uses POSIX heredocs). If
you prefer, copy each file from section 4 into an editor instead.

---

## 3. Create the files in one step

Run this from the repository root (the folder that contains `pyproject.toml`). It overwrites any
existing files with the same names.

```bash
cat > Dockerfile.api <<'EOF'
# AirBrief API image (FastAPI + airbrief_core). Build context: repository root.
FROM python:3.12-slim

ARG WITH_NATIVE=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (better layer caching): metadata + core package.
COPY pyproject.toml README.md ./
COPY airbrief_core ./airbrief_core
RUN python -m pip install --upgrade pip \
 && pip install -e ".[api,postgres]"

# Application code.
COPY api ./api
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY seed ./seed
COPY native ./native

# Optional C++ kernel: docker compose build --build-arg WITH_NATIVE=1 api
RUN if [ "$WITH_NATIVE" = "1" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends build-essential cmake \
      && pip install ./native \
      && apt-get purge -y build-essential cmake \
      && apt-get autoremove -y \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# Unprivileged user that owns /data (a new named volume copies this ownership).
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin airbrief \
 && mkdir -p /data/uploads /data/datapacks \
 && chown -R airbrief:airbrief /data
USER airbrief

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-access-log"]
EOF

cat > Dockerfile.web <<'EOF'
# AirBrief web image (Next.js 15). Build context: repository root.

# ---- 1. Install dependencies (exactly the versions in package-lock.json) ----
FROM node:20-slim AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# ---- 2. Build ----
FROM node:20-slim AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_BASEMAP=openfreemap
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_BASEMAP=$NEXT_PUBLIC_BASEMAP \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
RUN npm run build

# ---- 3. Run ----
FROM node:20-slim AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/next.config.* ./
USER node
EXPOSE 3000
CMD ["npm", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
EOF

cat > docker-compose.yml <<'EOF'
# AirBrief — local Docker setup (see DOCKER.md). Not committed to the repository.
name: airbrief

services:
  api:
    build:
      context: .
      dockerfile: Dockerfile.api
      args:
        WITH_NATIVE: ${WITH_NATIVE:-0}
    environment:
      APP_ENV: ${APP_ENV:-development}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      DATABASE_URL: ${DOCKER_DATABASE_URL:-sqlite:////data/airbrief.db}
      AUTO_MIGRATE: "true"
      DATA_DIR: /data
      UPLOAD_DIR: /data/uploads
      DATAPACK_DIR: /data/datapacks
      DEFAULT_DATAPACK: ${DEFAULT_DATAPACK:-}
      MAX_UPLOAD_MB: ${MAX_UPLOAD_MB:-50}
      MAX_UNZIPPED_MB: ${MAX_UNZIPPED_MB:-200}
      MAX_ARCHIVE_FILES: ${MAX_ARCHIVE_FILES:-200}
      MAX_FEATURES: ${MAX_FEATURES:-50000}
      JOB_MODE: inprocess
      WORKER_THREADS: ${WORKER_THREADS:-2}
      CORS_ORIGINS: ${DOCKER_CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}
      RATE_LIMIT_PER_HOUR: ${RATE_LIMIT_PER_HOUR:-120}
      TRUSTED_PROXY_HOPS: ${TRUSTED_PROXY_HOPS:-0}
      FLIGHTMD_API_URL: ${FLIGHTMD_API_URL:-}
      FLIGHTMD_FETCH_TIMEOUT_S: ${FLIGHTMD_FETCH_TIMEOUT_S:-15}
      BRIEF_SIGNING_KEY_FILE: ${DOCKER_BRIEF_SIGNING_KEY_FILE:-}
      # No value: passed on from .env when set there, otherwise left unset so the API's own
      # default for APP_ENV applies (e.g. 72 h retention in production).
      UPLOAD_TTL_HOURS:
      INLINE_PROCESS_MAX_MB:
      API_DOCS:
      ALLOWED_HOSTS:
      REQUIRE_OWNER_TOKEN:
      ALLOW_GLOBAL_LISTING:
      CONTEXT_RATE_LIMIT_PER_HOUR:
      STORAGE_QUOTA_MB:
      MIN_FREE_DISK_MB:
      JOB_TIMEOUT_S:
    volumes:
      - airbrief_data:/data
    ports:
      - "127.0.0.1:${API_PORT:-8000}:8000"
    restart: unless-stopped

  web:
    build:
      context: .
      dockerfile: Dockerfile.web
      args:
        NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8000}
        NEXT_PUBLIC_BASEMAP: ${NEXT_PUBLIC_BASEMAP:-openfreemap}
    depends_on:
      api:
        condition: service_healthy
    ports:
      - "127.0.0.1:${WEB_PORT:-3000}:3000"
    restart: unless-stopped

volumes:
  airbrief_data:
EOF

cat > docker-compose.postgres.yml <<'EOF'
# Optional PostgreSQL for the AirBrief Docker setup (see DOCKER.md §5). Not committed.
services:
  api:
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: airbrief
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}
      POSTGRES_DB: airbrief
    volumes:
      - airbrief_pg:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U airbrief -d airbrief"]
      interval: 5s
      timeout: 5s
      retries: 20
    restart: unless-stopped

volumes:
  airbrief_pg:
EOF

cat > .dockerignore <<'EOF'
.git
.github
.venv
venv
**/__pycache__
**/*.pyc
.pytest_cache
.mypy_cache
.ruff_cache
.hypothesis
.coverage
coverage
htmlcov
*.egg-info
build
dist
wheelhouse
data
samples
.env
.env.*
*.pem
*.key
frontend/node_modules
frontend/.next
frontend/out
frontend/public/demo
frontend/test-results
frontend/playwright-report
native/build
docs
Dockerfile.*
docker-compose.yml
docker-compose.*.yml
EOF

echo "Wrote Dockerfile.api, Dockerfile.web, docker-compose.yml, docker-compose.postgres.yml and .dockerignore"
```

---

## 4. The files, one by one

These are exactly what the script in section 3 writes.

### 4.1 `Dockerfile.api`

```dockerfile
# AirBrief API image (FastAPI + airbrief_core). Build context: repository root.
FROM python:3.12-slim

ARG WITH_NATIVE=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (better layer caching): metadata + core package.
COPY pyproject.toml README.md ./
COPY airbrief_core ./airbrief_core
RUN python -m pip install --upgrade pip \
 && pip install -e ".[api,postgres]"

# Application code.
COPY api ./api
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY seed ./seed
COPY native ./native

# Optional C++ kernel: docker compose build --build-arg WITH_NATIVE=1 api
RUN if [ "$WITH_NATIVE" = "1" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends build-essential cmake \
      && pip install ./native \
      && apt-get purge -y build-essential cmake \
      && apt-get autoremove -y \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# Unprivileged user that owns /data (a new named volume copies this ownership).
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin airbrief \
 && mkdir -p /data/uploads /data/datapacks \
 && chown -R airbrief:airbrief /data
USER airbrief

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-access-log"]
```

Why these choices:
- `python:3.12-slim` is enough: shapely, pyproj, rasterio (with GDAL bundled), lxml, h3 and scipy
  all ship binary wheels for Linux, so no system GIS libraries are needed.
- The `postgres` extra is always installed so the same image works with SQLite or PostgreSQL.
- The C++ kernel is off by default. With `WITH_NATIVE=1` the compiler is installed, used, then
  removed in the same layer to keep the image small.
- The server runs as `airbrief` (uid 10001), not root, so a bug in a file parser cannot change the
  image or anything outside `/data`. Installing happens as root, before the `USER` line.
- `--host 0.0.0.0` is needed *inside* the container; Compose publishes the port on the host's
  loopback only (§4.3). `--no-server-header` drops the `server: uvicorn` banner, and `--no-access-log` keeps request paths (which hold coordinates) out of the logs; refusals still go to the security log.
- For an instance other people use, pin both base images by digest, so a rebuild cannot pull a
  changed image under the same tag. Look the digests up once,
  `docker buildx imagetools inspect python:3.12-slim` and `… node:20-slim` (the `Digest:` line),
  and write them into the `FROM` lines, for example `FROM python:3.12-slim@sha256:<digest>`.
  Refresh them when you take security updates: a pinned image no longer gets them by itself.

### 4.2 `Dockerfile.web`

```dockerfile
# AirBrief web image (Next.js 15). Build context: repository root.

# ---- 1. Install dependencies (exactly the versions in package-lock.json) ----
FROM node:20-slim AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# ---- 2. Build ----
FROM node:20-slim AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_BASEMAP=openfreemap
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_BASEMAP=$NEXT_PUBLIC_BASEMAP \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
RUN npm run build

# ---- 3. Run ----
FROM node:20-slim AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/next.config.* ./
USER node
EXPOSE 3000
CMD ["npm", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
```

`NEXT_PUBLIC_*` values are baked in at **build** time, because Next.js inlines them into the
browser bundle. Change them, then rebuild `web` (section 6).

`npm ci` installs exactly what `package-lock.json` records and fails if the lock file is out of
date, so an image never picks up a newer (or hijacked) package version than the one tested. The
server runs as the image's unprivileged `node` user.

### 4.3 `docker-compose.yml`

```yaml
# AirBrief — local Docker setup (see DOCKER.md). Not committed to the repository.
name: airbrief

services:
  api:
    build:
      context: .
      dockerfile: Dockerfile.api
      args:
        WITH_NATIVE: ${WITH_NATIVE:-0}
    environment:
      APP_ENV: ${APP_ENV:-development}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      DATABASE_URL: ${DOCKER_DATABASE_URL:-sqlite:////data/airbrief.db}
      AUTO_MIGRATE: "true"
      DATA_DIR: /data
      UPLOAD_DIR: /data/uploads
      DATAPACK_DIR: /data/datapacks
      DEFAULT_DATAPACK: ${DEFAULT_DATAPACK:-}
      MAX_UPLOAD_MB: ${MAX_UPLOAD_MB:-50}
      MAX_UNZIPPED_MB: ${MAX_UNZIPPED_MB:-200}
      MAX_ARCHIVE_FILES: ${MAX_ARCHIVE_FILES:-200}
      MAX_FEATURES: ${MAX_FEATURES:-50000}
      JOB_MODE: inprocess
      WORKER_THREADS: ${WORKER_THREADS:-2}
      CORS_ORIGINS: ${DOCKER_CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}
      RATE_LIMIT_PER_HOUR: ${RATE_LIMIT_PER_HOUR:-120}
      TRUSTED_PROXY_HOPS: ${TRUSTED_PROXY_HOPS:-0}
      FLIGHTMD_API_URL: ${FLIGHTMD_API_URL:-}
      FLIGHTMD_FETCH_TIMEOUT_S: ${FLIGHTMD_FETCH_TIMEOUT_S:-15}
      BRIEF_SIGNING_KEY_FILE: ${DOCKER_BRIEF_SIGNING_KEY_FILE:-}
      # No value: passed on from .env when set there, otherwise left unset so the API's own
      # default for APP_ENV applies (e.g. 72 h retention in production).
      UPLOAD_TTL_HOURS:
      INLINE_PROCESS_MAX_MB:
      API_DOCS:
      ALLOWED_HOSTS:
      REQUIRE_OWNER_TOKEN:
      ALLOW_GLOBAL_LISTING:
      CONTEXT_RATE_LIMIT_PER_HOUR:
      STORAGE_QUOTA_MB:
      MIN_FREE_DISK_MB:
      JOB_TIMEOUT_S:
    volumes:
      - airbrief_data:/data
    ports:
      - "127.0.0.1:${API_PORT:-8000}:8000"
    restart: unless-stopped

  web:
    build:
      context: .
      dockerfile: Dockerfile.web
      args:
        NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8000}
        NEXT_PUBLIC_BASEMAP: ${NEXT_PUBLIC_BASEMAP:-openfreemap}
    depends_on:
      api:
        condition: service_healthy
    ports:
      - "127.0.0.1:${WEB_PORT:-3000}:3000"
    restart: unless-stopped

volumes:
  airbrief_data:
```

The compose file does **not** load `.env` into the containers with `env_file`, because the native
`.env` uses host paths (`./data/...`) that make no sense inside a container. Instead, Compose reads
`.env` only to fill in the `${...}` values above, and the container paths are fixed to `/data`.
Docker-only settings use their own names (`DOCKER_DATABASE_URL`, `DOCKER_CORS_ORIGINS`), so the same
`.env` serves both the native and the Docker run.

What keeps it private:
- **Ports on loopback.** `"127.0.0.1:8000:8000"` publishes the port on the host's loopback only. A
  plain `"8000:8000"` would listen on every interface of the host (Docker also bypasses most host
  firewalls), so anyone on your network could reach the API.
- **No retention override.** `UPLOAD_TTL_HOURS` has no default here: an explicit `0` would keep
  uploads forever even in production, where the API otherwise deletes them after 72 h. The same
  goes for `INLINE_PROCESS_MAX_MB` (production queues every upload).
- **Development mode stays local.** `APP_ENV` defaults to `development`, which has no owner tokens,
  lists everything and answers only loopback and private addresses. That is fine on your own
  machine; for anything else see [§6.1](#61-reachable-by-others).

### 4.4 `docker-compose.postgres.yml`

```yaml
# Optional PostgreSQL for the AirBrief Docker setup (see DOCKER.md §5). Not committed.
services:
  api:
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: airbrief
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}
      POSTGRES_DB: airbrief
    volumes:
      - airbrief_pg:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U airbrief -d airbrief"]
      interval: 5s
      timeout: 5s
      retries: 20
    restart: unless-stopped

volumes:
  airbrief_pg:
```

PostgreSQL lives in its own file because Compose checks `${POSTGRES_PASSWORD:?...}` for every
service in a file it loads, even one behind an inactive profile; in a separate file, only people
who use PostgreSQL have to set the password. There is no default password and no published port:
the `api` container reaches `db` over the Compose network, and the API waits until it is healthy.

### 4.5 `.dockerignore`

```text
.git
.github
.venv
venv
**/__pycache__
**/*.pyc
.pytest_cache
.mypy_cache
.ruff_cache
.hypothesis
.coverage
coverage
htmlcov
*.egg-info
build
dist
wheelhouse
data
samples
.env
.env.*
*.pem
*.key
frontend/node_modules
frontend/.next
frontend/out
frontend/public/demo
frontend/test-results
frontend/playwright-report
native/build
docs
Dockerfile.*
docker-compose.yml
docker-compose.*.yml
```

---

## 5. Commands

### First start

```bash
cp .env.example .env                       # if you have not already
docker compose up -d --build               # builds api + web, starts them
docker compose ps                          # api shows (healthy), web shows running
curl -s http://localhost:8000/health
```

Expected `/health` (data packs empty until you seed one):

```json
{"status":"ok","version":"0.1.0","schema_version":"1.0","db":"ok","datapacks":[],"native_backend":"python"}
```

Migrations run automatically when the API starts (`AUTO_MIGRATE=true`). To run them by hand:

```bash
docker compose exec api alembic upgrade head
```

### Data packs and samples

```bash
# Offline synthetic demo pack (seconds, no internet)
docker compose exec api python -m seed.datapack --synthetic --name demo

# Real Bengaluru pack (~50 km clip; downloads about 1.5 GB of sources, keeps the clip)
docker compose exec api python -m seed.datapack --region bengaluru

# Sample files, copied out to your machine so you can upload them
docker compose exec api python -m seed.samples --dest /data/samples
docker compose cp api:/data/samples ./samples
```

After seeding, `/health` lists the pack, for example `"datapacks":["demo"]`. To make one pack the
default, set `DEFAULT_DATAPACK=demo` in `.env` and run `docker compose up -d api`.

### Everyday

| Task | Command |
|---|---|
| Open the app | http://localhost:3000 (API docs, development only: http://localhost:8000/docs) |
| Logs | `docker compose logs -f api` / `docker compose logs -f web` |
| Restart the API | `docker compose restart api` |
| Run the Python tests in the container | `docker compose exec --user root api sh -c 'pip install -e ".[dev]" && pytest -m "not native and not network"'` |
| Stop (keep data) | `docker compose down` |
| Update after `git pull` | `docker compose up -d --build` |

The API docs pages load Swagger UI and ReDoc from `cdn.jsdelivr.net` (pinned versions), so
opening them contacts that CDN. They are off when `APP_ENV=production` unless you set `API_DOCS=true`.

### With the C++ kernel

```bash
WITH_NATIVE=1 docker compose up -d --build api
docker compose exec api python -c "import airbrief_core._native as n; print(n.BACKEND)"   # cpp
```

### With PostgreSQL

Add to `.env` (use a long random password, the same one in both places):

```ini
COMPOSE_FILE=docker-compose.yml:docker-compose.postgres.yml
POSTGRES_PASSWORD=CHANGE_ME
DOCKER_DATABASE_URL=postgresql+psycopg://airbrief:CHANGE_ME@db:5432/airbrief
```

`COMPOSE_FILE` makes every `docker compose` command use both files. On Windows, separate the two
names with `;` instead of `:`. Then:

```bash
docker compose up -d --build
docker compose ps                           # db shows (healthy)
```

PostGIS is not required; plain `postgres:16` is enough. Without `POSTGRES_PASSWORD`, Compose stops
with `required variable POSTGRES_PASSWORD is missing a value: set POSTGRES_PASSWORD`. A password
with `@`, `:` or `/` must be percent-encoded in `DOCKER_DATABASE_URL`.

---

## 6. Configuration

| Variable (in `.env`) | Default | Used for |
|---|---|---|
| `API_PORT` / `WEB_PORT` | `8000` / `3000` | Host ports, published on `127.0.0.1` only |
| `DOCKER_DATABASE_URL` | `sqlite:////data/airbrief.db` | Database inside Docker (four slashes = absolute path) |
| `DOCKER_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Browser origins allowed to call the API |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | API URL as the **browser** sees it (build-time) |
| `NEXT_PUBLIC_BASEMAP` | `openfreemap` | `openfreemap` or `none` (offline) (build-time) |
| `WITH_NATIVE` | `0` | `1` builds the C++ kernel into the API image |
| `COMPOSE_FILE` | *(unset)* | `docker-compose.yml:docker-compose.postgres.yml` adds PostgreSQL (§5) |
| `POSTGRES_PASSWORD` | *(none: required with PostgreSQL)* | Password for the optional `db` service |
| `DOCKER_BRIEF_SIGNING_KEY_FILE` | *(unset: briefs unsigned)* | Signing key path **inside** the container: `/run/keys/brief-signing-key.pem` when mounted as in §6.2 |
| `BRIEF_SIGNING_KEY_HOST_FILE` | *(unset)* | Where the signing key lives on the host, outside `/data` (read by `docker-compose.signing.yml`) |
| `APP_ENV`, `TRUSTED_PROXY_HOPS`, `UPLOAD_TTL_HOURS`, `INLINE_PROCESS_MAX_MB`, `API_DOCS`, `ALLOWED_HOSTS`, `STORAGE_QUOTA_MB`, `MIN_FREE_DISK_MB`, `JOB_TIMEOUT_S`, `MAX_UPLOAD_MB`, `RATE_LIMIT_PER_HOUR`, `FLIGHTMD_API_URL` … | as in `.env.example` | Passed through; the ones with no value in the compose file are left unset when `.env` does not set them |

After changing a `NEXT_PUBLIC_*` value, rebuild the web image:

```bash
docker compose up -d --build web
```

### 6.1 Reachable by others

The defaults above are for your own machine. If anyone else can reach the app, even on a LAN:

1. Keep the loopback ports and put a reverse proxy with HTTPS on the host in front of them, as in
   `DEPLOYMENT.md` §3.2 (Caddy: `/api/*` and `/health` to `127.0.0.1:8000`, everything else to
   `127.0.0.1:3000`). Do not change the port lines to `"8000:8000"`.
2. Set production mode and the real origin in `.env`, then rebuild:

   ```ini
   APP_ENV=production
   DOCKER_CORS_ORIGINS=https://airbrief.example.com
   NEXT_PUBLIC_API_URL=https://airbrief.example.com
   # The proxy on the host is the only client of the loopback port: trust the address it appends.
   TRUSTED_PROXY_HOPS=1
   # Optional: refuse requests for any other host name (keep 127.0.0.1 for the health check).
   ALLOWED_HOSTS=airbrief.example.com,127.0.0.1
   ```

   ```bash
   docker compose up -d --build
   ```

   Production turns on owner tokens, per-browser listings, 72 h retention and the rate limits, and
   turns off `/docs` and `/redoc` (`DEPLOYMENT.md` §8).
3. With PostgreSQL, use a long random `POSTGRES_PASSWORD`; never `CHANGE_ME` or a dictionary word.

`TRUSTED_PROXY_HOPS=1` is right here because Docker hides the client's address from the API (it
sees the Docker gateway), and only the proxy on the same host can reach `127.0.0.1:8000`.

---

### 6.2 Signing briefs

Keep the key on the host, outside `/data`: volume backups then never carry it. Create it once
(`python -m airbrief_core.verify --new-key <path>` does the same if Python is installed) and make
it readable by the API image's user (uid 10001) only:

```bash
mkdir -p ~/airbrief-keys
openssl genpkey -algorithm ed25519 -out ~/airbrief-keys/brief-signing-key.pem
sudo chown 10001:10001 ~/airbrief-keys/brief-signing-key.pem
sudo chmod 0400 ~/airbrief-keys/brief-signing-key.pem
```

Mount it read-only with a small extra Compose file, and point the API at it:

```bash
cat > docker-compose.signing.yml <<'EOF'
services:
  api:
    volumes:
      - ${BRIEF_SIGNING_KEY_HOST_FILE:?set BRIEF_SIGNING_KEY_HOST_FILE}:/run/keys/brief-signing-key.pem:ro
EOF
```

```ini
BRIEF_SIGNING_KEY_HOST_FILE=/home/you/airbrief-keys/brief-signing-key.pem
DOCKER_BRIEF_SIGNING_KEY_FILE=/run/keys/brief-signing-key.pem
COMPOSE_FILE=docker-compose.yml:docker-compose.signing.yml
```

With PostgreSQL too, list all three files in `COMPOSE_FILE` (`;` between them on Windows). Then
`docker compose up -d api`; `GET /api/meta/` shows the key id under `signing`. Back the key up
separately and offline, and see `DEPLOYMENT.md` §3.7 for publishing and rotating it.

## 7. Volumes, backups and reset

| Volume | Mounted at | Holds |
|---|---|---|
| `airbrief_airbrief_data` | `/data` in `api` | `airbrief.db` (SQLite), `uploads/`, `datapacks/`, `samples/` |
| `airbrief_airbrief_pg` | `/var/lib/postgresql/data` in `db` | PostgreSQL data (with `docker-compose.postgres.yml` only) |

```bash
# Back up the data volume to ./airbrief_data_YYYY-MM-DD.tgz
docker run --rm -v airbrief_airbrief_data:/data -v "$PWD":/b alpine \
  tar czf /b/airbrief_data_$(date +%F).tgz -C / data

# Restore it (tar keeps the files' owner, uid 10001)
docker run --rm -v airbrief_airbrief_data:/data -v "$PWD":/b alpine \
  sh -c "rm -rf /data/* && tar xzf /b/airbrief_data_YYYY-MM-DD.tgz -C /"

# PostgreSQL dump / restore (with docker-compose.postgres.yml)
docker compose exec db pg_dump -U airbrief -Fc airbrief > airbrief_$(date +%F).dump
docker compose exec -T db pg_restore -U airbrief -d airbrief --clean < airbrief_YYYY-MM-DD.dump

# Full reset: stop and DELETE the database, uploads and data packs
docker compose down -v
```

Backups are not covered by the API's retention (`UPLOAD_TTL_HOURS`): delete old archives yourself.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `port is already allocated` | Another app uses 3000 or 8000 | Set `WEB_PORT` or `API_PORT` in `.env`, then `docker compose up -d` |
| `required variable POSTGRES_PASSWORD is missing a value` | `COMPOSE_FILE` includes the PostgreSQL file, but no password is set | Set `POSTGRES_PASSWORD` (and the same password in `DOCKER_DATABASE_URL`) in `.env` |
| `web` never starts; `api` is `(unhealthy)` | The API failed at startup (often a migration error) | `docker compose logs api`; fix, then `docker compose up -d` |
| `PermissionError: [Errno 13] Permission denied: '/data/...'` | The volume was created by an older image that ran as root | `docker compose run --rm --user root api chown -R airbrief:airbrief /data`, then `docker compose up -d` |
| `COPY failed: file not found ... alembic.ini` (or `api`, `seed`, `native`) | The checkout is older than milestone M1 | `git pull`; the files exist from M1 onwards |
| `/health` shows `"db":"error"` with PostgreSQL | `db` not ready yet, or the URL host is not `db` | `docker compose ps`; `DOCKER_DATABASE_URL` must use host `db`, not `localhost` |
| Browser shows "Cannot reach the API" | `NEXT_PUBLIC_API_URL` baked in wrong, or CORS | Set `NEXT_PUBLIC_API_URL` to the URL your browser uses, add that web origin to `DOCKER_CORS_ORIGINS`, then `docker compose up -d --build` |
| `403 development_mode_local_only` | `APP_ENV=development` and the request came from a public address | Use production settings ([§6.1](#61-reachable-by-others)) |
| `400 Invalid host header` | `ALLOWED_HOSTS` does not list the host name the request used | Add it (and `127.0.0.1` for the health check) |
| Other devices on the LAN cannot open the app | Ports are published on `127.0.0.1` only (§4.3) | Put a reverse proxy in front ([§6.1](#61-reachable-by-others)) |
| `sqlite3.OperationalError: unable to open database file` | Wrong SQLite path | Use four slashes: `sqlite:////data/airbrief.db` |
| Modules show "Data pack missing" | No pack seeded | Run a `seed.datapack` command from section 5 |
| Native build fails (`WITH_NATIVE=1`) | Compiler or CMake error | `docker compose build --progress=plain --build-arg WITH_NATIVE=1 api` and read the CMake output; the app works without the kernel |
| `npm ci` fails in `Dockerfile.web` | `package-lock.json` out of date, or a network hiccup | Run `npm install` in `frontend/` natively to refresh the lock file, then `docker compose build web`; behind a proxy, set `HTTP_PROXY`/`HTTPS_PROXY` build args |
| Changes in code not visible | Images are not live-mounted | `docker compose up -d --build` (for live reload, use the native run in `RUNBOOK.md`) |
| Everything is odd | Stale images or volumes | `docker compose down -v && docker compose build --no-cache && docker compose up -d` |
| Script in section 3 fails on Windows | `cat <<'EOF'` needs a POSIX shell | Run it in Git Bash or WSL, or copy the files from section 4 by hand |
