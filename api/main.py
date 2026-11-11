"""FastAPI application factory: ``uvicorn api.main:app``.

:func:`create_app` builds an isolated app from a :class:`~api.config.Settings` (tests pass
their own). Migrations, the job runner and the TTL sweeper start in the lifespan, not at import.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from airbrief_core import __version__
from airbrief_core.data.datapack import DataPackRegistry

from .config import Settings, get_settings
from .db import Database, run_migrations
from .deps import AppContext
from .errors import install_exception_handlers
from .jobs import JobRunner, mark_interrupted
from .headers import SecurityHeadersMiddleware, add_docs_pages
from .ratelimit import ByteBudget, RateLimiter, RequestGuardMiddleware
from .routers import analyses, context, files, flightmd, health, meta, samples
from .routers import briefs
from .storage import Storage, TTLSweeper

log = logging.getLogger("airbrief.api")

DESCRIPTION = (
    "Open-source geospatial pre-flight intelligence: CRS-correct measurement of uploaded "
    "KML/KMZ/Shapefile/GeoJSON/CSV files, airspace and terrain checks, SORA-style ground-risk "
    "evidence, eVTOL siting and corridors, drone-show validation, survey estimates and FlightMD "
    "flight audits. Results are evidence for planning, not official decisions. Errors always "
    'come back as `{"error": {"code", "message", "details"}}`.'
)

OPENAPI_TAGS = [
    {"name": "files", "description": "Upload and measure datasets."},
    {"name": "samples", "description": "Built-in sample files."},
    {"name": "analyses", "description": "Analysis jobs (airspace, ground risk, eVTOL, shows, survey, flight audit)."},
    {"name": "flightmd", "description": "FlightMD report fetching (SSRF-guarded)."},
    {"name": "meta", "description": "Modules, presets, data packs and limits."},
    {"name": "context", "description": "Context layers for a map view, place search and pasted-geometry parsing."},
    {"name": "briefs", "description": "Operation evidence brief and bundle (evidence, not authorisation)."},
    {"name": "health", "description": "Service health."},
]


def _configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=settings.log_level,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        )
    for name in ("airbrief", "airbrief_core"):  # the core logs under its module names
        logging.getLogger(name).setLevel(settings.log_level)


def _warn_development_mode(settings: Settings) -> None:
    off = [
        name
        for name, is_off in (
            ("owner tokens", not settings.owner_token_required),
            ("scoped listings", settings.global_listing_enabled),
            ("upload expiry", settings.upload_ttl_hours == 0),
            ("POST rate limit", settings.rate_limit_per_hour == 0),
            ("context rate limit", settings.context_rate_limit == 0),
            ("read rate limit", settings.read_rate_limit == 0),
            ("upload budget", settings.upload_bytes_per_day == 0),
            ("storage quota", settings.storage_quota_bytes == 0),
            ("job time limit", settings.job_timeout == 0),
        )
        if is_off
    ]
    log.warning(
        "APP_ENV=development. Protections off: %s; 500 responses include exception details. "
        "Only clients on loopback or private networks are answered. "
        "Set APP_ENV=production on any server other people can reach.",
        ", ".join(off) or "none",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the AirBrief API for ``settings`` (default: :func:`~api.config.get_settings`)."""
    settings = settings or get_settings()
    _configure_logging(settings)

    db = Database(settings.database_url)
    storage = Storage(
        settings.upload_dir,
        quota_bytes=settings.storage_quota_bytes,
        min_free_bytes=settings.min_free_disk_bytes,
    )
    registry = DataPackRegistry(settings.datapack_dir)
    limiter = RateLimiter(settings.rate_limit_per_hour)
    runner = JobRunner(db, storage, settings, registry)
    ctx = AppContext(
        settings=settings,
        db=db,
        storage=storage,
        registry=registry,
        limiter=limiter,
        runner=runner,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        db.ensure_sqlite_dir()
        if settings.auto_migrate:
            run_migrations(settings.database_url)
        sweeper: TTLSweeper | None = None
        if settings.job_mode == "inprocess":
            mark_interrupted(db)
            runner.start()
        else:
            log.info("JOB_MODE=external: jobs are queued for `python -m api.worker`")
        if settings.upload_ttl_hours > 0:
            sweeper = TTLSweeper(db, storage, settings)
            sweeper.start()
        log.info(
            "AirBrief API %s ready (%s, data packs: %s)",
            __version__,
            settings.app_env,
            ", ".join(registry.names()) or "none",
        )
        if not settings.is_production:
            _warn_development_mode(settings)
        try:
            yield
        finally:
            if sweeper is not None:
                sweeper.stop()
            runner.stop(wait=False)
            db.dispose()

    app = FastAPI(
        title="AirBrief API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
        redirect_slashes=False,
        debug=False,
        docs_url=None,
        redoc_url=None,
    )
    if settings.api_docs_enabled:
        add_docs_pages(app)
    app.state.ctx = ctx
    app.state.settings = settings
    app.state.db = db

    install_exception_handlers(
        app, development=not settings.is_production, proxy_hops=settings.trusted_proxy_hops
    )

    # Middleware: the last one added is the outermost. CORS wraps everything so that error
    # responses (413/429 from the guard) also carry CORS headers.
    app.add_middleware(
        RequestGuardMiddleware,
        limiter=limiter,
        max_upload_bytes=settings.max_upload_bytes,
        max_body_bytes=settings.max_json_body_bytes,
        proxy_hops=settings.trusted_proxy_hops,
        context_limiter=RateLimiter(settings.context_rate_limit),
        context_paths=("/api/context", "/api/places"),
        read_limiter=RateLimiter(settings.read_rate_limit),
        upload_budget=ByteBudget(settings.upload_bytes_per_day),
        allowed_origins=settings.cors_origin_list,
        local_only=not settings.is_production,
        storage=storage,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "Retry-After"],
        max_age=600,
    )
    # outermost, so a request for a foreign Host is refused before anything else runs
    if settings.allowed_host_list:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

    for module in (health, files, samples, analyses, flightmd, meta):
        app.include_router(module.router)
    app.include_router(context.router)
    app.include_router(briefs.router)
    return app


app = create_app()
