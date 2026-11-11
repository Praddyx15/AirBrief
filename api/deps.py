"""Shared application services and their FastAPI dependencies.

:class:`AppContext` bundles everything a request handler or a job needs (settings, database,
storage, data-pack registry, job runner, rate limiter). One instance lives on
``app.state.ctx``; tests build isolated apps with their own context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request

from airbrief_core.data.datapack import DataPackRegistry

from .config import Settings
from .db import Database
from .ratelimit import RateLimiter
from .storage import Storage

if TYPE_CHECKING:
    from .jobs import JobRunner


@dataclass
class AppContext:
    """Process-wide services of one API application (or worker)."""

    settings: Settings
    db: Database
    storage: Storage
    registry: DataPackRegistry
    limiter: RateLimiter
    runner: JobRunner | None = None

    def notify_jobs(self) -> None:
        """Wake the in-process job runner (no-op in ``JOB_MODE=external``)."""
        if self.runner is not None and self.settings.job_mode == "inprocess":
            self.runner.wake()


def get_ctx(request: Request) -> AppContext:
    """FastAPI dependency: the application context."""
    ctx: AppContext = request.app.state.ctx
    return ctx
