"""API settings, read from the environment and ``.env`` (template: ``.env.example``).

Every key in ``.env.example`` maps to one field below (case-insensitive). Derived values such as
byte limits and the :class:`~airbrief_core.io.ingest.IngestLimits` used for uploads are exposed
as properties so the rest of the API never repeats the MB → bytes arithmetic.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from airbrief_core.io.ingest import IngestLimits
from airbrief_core.signing import BriefSigner

MB = 1024 * 1024
PRODUCTION_DEFAULT_TTL_HOURS = 72.0
"""Upload retention on public instances when UPLOAD_TTL_HOURS is not set (APP_ENV=production)."""
FLIGHTMD_MAX_BYTES = 5 * MB
"""Hard cap on a FlightMD report fetched by URL (SPEC §4)."""
PRODUCTION_CONTEXT_RATE_LIMIT_PER_HOUR = 3600
"""Per-IP ``GET /api/context/`` + ``/api/places/`` requests per hour on public instances when
CONTEXT_RATE_LIMIT_PER_HOUR is not set (APP_ENV=production): one a second on average, far above
what panning a map and typing searches need (views are cached by the browser for 5 minutes)."""
PRODUCTION_READ_RATE_LIMIT_PER_HOUR = 36_000
"""Every other per-IP GET under /api/ per hour (READ_RATE_LIMIT_PER_HOUR unset, production): ten
a second on average. The web app polls each running analysis once a second and its lists every
two, so even a dozen queued runs stay under it."""
PRODUCTION_STORAGE_QUOTA_MB = 5000.0
PRODUCTION_UPLOAD_MB_PER_DAY = 1000.0  # with the 72 h TTL one address holds at most 3,000 of the 5,000 MB
PRODUCTION_JOB_TIMEOUT_S = 900.0


def database_tls_ok(url: str) -> bool:
    """False for a PostgreSQL URL whose host is a public name or address and that does not ask for
    TLS. Single-label names (``db`` in Docker Compose), private and loopback addresses and sockets
    are taken as the same machine or network."""
    parts = urlsplit(url)
    if not parts.scheme.startswith("postgresql"):
        return True
    host = parts.hostname or ""
    try:
        if not ipaddress.ip_address(host).is_global:
            return True
    except ValueError:
        if not host or "." not in host or host.endswith((".local", ".internal")):
            return True
    return parse_qs(parts.query).get("sslmode", [""])[-1] in ("require", "verify-ca", "verify-full")


class Settings(BaseSettings):
    """Runtime configuration of the AirBrief API and worker."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str = "sqlite:///./data/airbrief.db"
    auto_migrate: bool = True

    data_dir: Path = Path("./data")
    upload_dir: Path = Path("./data/uploads")
    datapack_dir: Path = Path("./data/datapacks")
    default_datapack: str | None = None
    # Total size of UPLOAD_DIR. Empty = unlimited in development, 5000 in production; 0 = unlimited.
    storage_quota_mb: float | None = Field(default=None, ge=0)
    # Upload bytes one client address may send in 24 hours. Empty = unlimited in development,
    # 1000 in production; 0 = unlimited.
    upload_mb_per_day: float | None = Field(default=None, ge=0)
    # Uploads are refused while the disk under UPLOAD_DIR has less free space than this. 0 = off.
    min_free_disk_mb: float = Field(default=500.0, ge=0)

    max_upload_mb: float = Field(default=50.0, gt=0)
    # Limit for every other POST body (JSON: analysis requests incl. an inline FlightMD report)
    max_json_body_mb: float = Field(default=10.0, gt=0, le=200)
    max_unzipped_mb: float = Field(default=200.0, gt=0)
    max_archive_files: int = Field(default=200, ge=1)
    max_features: int = Field(default=50_000, ge=1)
    max_coordinates: int = Field(default=5_000_000, ge=1)
    # Uploads up to this size are parsed inside the request. Empty = 5 in development and 0 in
    # production, where every upload goes to the job queue.
    inline_process_max_mb: float | None = Field(default=None, ge=0)

    job_mode: Literal["inprocess", "external"] = "inprocess"
    worker_threads: int = Field(default=2, ge=1, le=64)
    worker_poll_interval_s: float = Field(default=1.0, gt=0, le=60)
    # Wall-clock limit per analysis. Empty = none in development, 900 in production; 0 = none.
    job_timeout_s: float | None = Field(default=None, ge=0)
    # RLIMIT_AS for `python -m api.worker` (POSIX only). Empty or 0 = off.
    worker_memory_limit_mb: int | None = Field(default=None, ge=0)

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    rate_limit_per_hour: int = Field(default=120, ge=0)
    # Per-IP limit on GET /api/context/ and /api/places/ (unauthenticated reads that can return
    # megabytes of pack data). Empty = unlimited in development, 3600 in production; 0 = unlimited.
    context_rate_limit_per_hour: int | None = Field(default=None, ge=0)
    # Per-IP limit on every other GET under /api/ (results, exports, listings, job polling).
    # Empty = unlimited in development, 36000 in production; 0 = unlimited.
    read_rate_limit_per_hour: int | None = Field(default=None, ge=0)
    # Proxies in front of the API that append to X-Forwarded-For (rate-limit key = the client
    # address the outermost one saw). 0 = use the peer address (uvicorn's proxy handling).
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)
    upload_ttl_hours: float = Field(default=0.0, ge=0)
    # Unscoped listings (every dataset / analysis on the server). Empty = allowed, except when
    # APP_ENV=production. When disabled, clients list only the ids they created (``?ids=``).
    allow_global_listing: bool | None = None
    # Deleting/cancelling needs the item's owner token (X-Owner-Token). Empty = on in production.
    # Also scopes ?dataset_id= listings to the analyses run with the dataset's token.
    require_owner_token: bool | None = None
    # /docs and /redoc, which load Swagger UI and ReDoc from jsDelivr. Empty = on in development,
    # off in production. /openapi.json is always served.
    api_docs: bool | None = None
    # Host header allow-list, comma-separated (TrustedHostMiddleware). Empty = any host.
    allowed_hosts: str = ""

    flightmd_api_url: str | None = None
    flightmd_fetch_timeout_s: float = Field(default=15.0, gt=0, le=300)

    # PEM Ed25519 private key that signs brief bundles (python -m airbrief_core.verify --new-key).
    # Empty = bundles are not signed.
    brief_signing_key_file: Path | None = None
    _brief_signer: BriefSigner | None = PrivateAttr(default=None)

    @field_validator(
        "default_datapack",
        "flightmd_api_url",
        "allow_global_listing",
        "require_owner_token",
        "context_rate_limit_per_hour",
        "read_rate_limit_per_hour",
        "inline_process_max_mb",
        "storage_quota_mb",
        "upload_mb_per_day",
        "job_timeout_s",
        "worker_memory_limit_mb",
        "api_docs",
        "brief_signing_key_file",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("app_env", "job_mode", mode="before")
    @classmethod
    def _lower_enum(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("flightmd_api_url")
    @classmethod
    def _check_flightmd_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("FLIGHTMD_API_URL must be an absolute http(s) URL")
        if parts.username is not None or parts.password is not None or "?" in value or "#" in value:
            raise ValueError(
                "FLIGHTMD_API_URL must be a base URL without a user name, password, ?query or #fragment "
                "(reports are fetched from {FLIGHTMD_API_URL}/report/{id})"
            )
        return value.rstrip("/")

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        if self.app_env == "production":
            origins = self.cors_origin_list
            if "cors_origins" not in self.model_fields_set or not origins or "*" in origins:
                raise ValueError(
                    "APP_ENV=production requires CORS_ORIGINS to list the real web origin(s) explicitly"
                )
            if not database_tls_ok(self.database_url):
                raise ValueError(
                    "APP_ENV=production: DATABASE_URL points at a PostgreSQL server on the internet without TLS; "
                    "add ?sslmode=require (or verify-full)"
                )
            # Privacy by default: a public instance does not keep uploads forever unless told to
            # (UPLOAD_TTL_HOURS=0 explicitly keeps them).
            if "upload_ttl_hours" not in self.model_fields_set:
                self.upload_ttl_hours = PRODUCTION_DEFAULT_TTL_HOURS
        return self

    @model_validator(mode="after")
    def _load_signing_key(self) -> Settings:
        # Loaded at start-up: a key that cannot be used must stop the API, not quietly leave
        # bundles unsigned.
        if self.brief_signing_key_file is not None:
            try:
                self._brief_signer = BriefSigner.from_file(self.brief_signing_key_file)
            except (OSError, ValueError, RuntimeError) as exc:
                raise ValueError(f"BRIEF_SIGNING_KEY_FILE: {exc}") from exc
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def owner_token_required(self) -> bool:
        """Whether DELETE and cancel need the owner token (``X-Owner-Token``), and a dataset lists
        only its owner's analyses."""
        if self.require_owner_token is not None:
            return self.require_owner_token
        return self.is_production

    @property
    def context_rate_limit(self) -> int:
        """Per-IP hourly limit on ``GET /api/context/`` and ``/api/places/`` (0 = unlimited)."""
        if self.context_rate_limit_per_hour is not None:
            return self.context_rate_limit_per_hour
        return PRODUCTION_CONTEXT_RATE_LIMIT_PER_HOUR if self.is_production else 0

    @property
    def read_rate_limit(self) -> int:
        """Per-IP hourly limit on the other ``GET`` requests under ``/api/`` (0 = unlimited)."""
        if self.read_rate_limit_per_hour is not None:
            return self.read_rate_limit_per_hour
        return PRODUCTION_READ_RATE_LIMIT_PER_HOUR if self.is_production else 0

    @property
    def global_listing_enabled(self) -> bool:
        """Whether ``GET /api/files/`` and ``GET /api/analyses/`` may list everything."""
        if self.allow_global_listing is not None:
            return self.allow_global_listing
        return not self.is_production

    @property
    def api_docs_enabled(self) -> bool:
        """Whether ``/docs`` and ``/redoc`` are served."""
        if self.api_docs is not None:
            return self.api_docs
        return not self.is_production

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip().lower() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        """``CORS_ORIGINS`` split on commas, without blanks or trailing slashes."""
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * MB)

    @property
    def max_unzipped_bytes(self) -> int:
        return int(self.max_unzipped_mb * MB)

    @property
    def max_json_body_bytes(self) -> int:
        return int(self.max_json_body_mb * MB)

    @property
    def inline_process_max_bytes(self) -> int:
        mb = self.inline_process_max_mb
        if mb is None:
            mb = 0.0 if self.is_production else 5.0
        return int(mb * MB)

    @property
    def storage_quota_bytes(self) -> int:
        """Byte quota for everything under ``UPLOAD_DIR`` (0 = unlimited)."""
        if self.storage_quota_mb is not None:
            return int(self.storage_quota_mb * MB)
        return int(PRODUCTION_STORAGE_QUOTA_MB * MB) if self.is_production else 0

    @property
    def upload_bytes_per_day(self) -> int:
        """Upload bytes one client address may send in 24 hours (0 = unlimited)."""
        if self.upload_mb_per_day is not None:
            return int(self.upload_mb_per_day * MB)
        return int(PRODUCTION_UPLOAD_MB_PER_DAY * MB) if self.is_production else 0

    @property
    def min_free_disk_bytes(self) -> int:
        return int(self.min_free_disk_mb * MB)

    @property
    def job_timeout(self) -> float:
        """Seconds an analysis may run before it is stopped (0 = no limit)."""
        if self.job_timeout_s is not None:
            return self.job_timeout_s
        return PRODUCTION_JOB_TIMEOUT_S if self.is_production else 0.0

    @property
    def flightmd_max_bytes(self) -> int:
        return FLIGHTMD_MAX_BYTES

    @property
    def brief_signer(self) -> BriefSigner | None:
        """The key that signs brief bundles (``BRIEF_SIGNING_KEY_FILE``), or None: unsigned."""
        return self._brief_signer

    @property
    def ingest_limits(self) -> IngestLimits:
        """Limits passed to :func:`airbrief_core.processing.process_file`."""
        return IngestLimits(
            max_upload_bytes=self.max_upload_bytes,
            max_unzipped_bytes=self.max_unzipped_bytes,
            max_archive_files=self.max_archive_files,
            max_features=self.max_features,
            max_coordinates=self.max_coordinates,
        )

    @property
    def flightmd_host(self) -> tuple[str, int] | None:
        """``(hostname, port)`` of ``FLIGHTMD_API_URL`` (the one host the SSRF guard trusts)."""
        if not self.flightmd_api_url:
            return None
        parts = urlsplit(self.flightmd_api_url)
        if not parts.hostname:
            return None
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return parts.hostname.lower(), port

    @property
    def flightmd_origin(self) -> str | None:
        """Scheme and host (with its port) of ``FLIGHTMD_API_URL``, the part ``GET /api/meta/`` shows."""
        if not self.flightmd_api_url:
            return None
        parts = urlsplit(self.flightmd_api_url)
        return f"{parts.scheme}://{parts.netloc}"

    def ensure_dirs(self) -> None:
        """Create the runtime directories (data, uploads, data packs, SQLite parent)."""
        for directory in (self.data_dir, self.upload_dir, self.datapack_dir):
            Path(directory).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings (read once from the environment and ``.env``)."""
    return Settings()
