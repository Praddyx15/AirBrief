"""Database engine, sessions and migrations.

* SQLite (default): ``check_same_thread=False`` so the job threads can share the engine, with
  ``journal_mode=WAL``, ``foreign_keys=ON``, ``synchronous=NORMAL``, ``secure_delete=ON`` and a
  busy timeout set on every new connection. ``secure_delete`` overwrites deleted rows, so tracks
  and areas removed by DELETE or the TTL sweeper do not linger in free pages of the file.
* PostgreSQL: ``pool_pre_ping`` so connections dropped by the server are replaced transparently.

:func:`run_migrations` applies Alembic migrations programmatically with an absolute
``script_location``, so it works whatever the current directory is.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger("airbrief.api.db")

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_DIR = REPO_ROOT / "alembic"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _sqlite_path(url: str) -> Path | None:
    """The file path of a SQLite URL, or None for in-memory / non-SQLite databases."""
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return None
    database = parsed.database
    if not database or database == ":memory:" or database.startswith("file:"):
        return None
    return Path(database)


def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA secure_delete=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


def build_engine(url: str) -> Engine:
    """Create an engine with the per-dialect options described in the module docstring."""
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            future=True,
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20, future=True)


class Database:
    """An engine plus a session factory, shared by the API, the job runner and the worker."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = build_engine(url)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def session(self) -> Session:
        """A new session; use it as a context manager (``with db.session() as s:``)."""
        return self.session_factory()

    def ensure_sqlite_dir(self) -> None:
        path = _sqlite_path(self.url)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def ping(self) -> bool:
        """True when ``SELECT 1`` succeeds."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # any driver error means "not reachable"
            log.warning("Database ping failed: %s", exc)
            return False

    def dispose(self) -> None:
        self.engine.dispose()


def run_migrations(url: str, revision: str = "head") -> None:
    """Upgrade the database at ``url`` to ``revision`` with Alembic."""
    from alembic import command
    from alembic.config import Config

    path = _sqlite_path(url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    config = Config(str(ALEMBIC_INI)) if ALEMBIC_INI.is_file() else Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    # Keep the application's logging configuration (env.py skips fileConfig when this is set).
    config.attributes["configure_logger"] = False
    log.info("Applying database migrations (%s)", make_url(url).render_as_string(hide_password=True))
    command.upgrade(config, revision)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: a session bound to the application's database."""
    db: Database = request.app.state.db
    with db.session() as session:
        yield session
