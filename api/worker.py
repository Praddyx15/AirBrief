"""Standalone job worker: ``python -m api.worker`` (use with ``JOB_MODE=external``).

Polls the database for pending datasets and analyses and runs them with the same
compare-and-set claim logic as the in-process runner, so several workers (and an API in
``inprocess`` mode) can share one database safely.

Options:
    --once        process everything pending, wait for it to finish, then exit
    --threads N   override WORKER_THREADS

``WORKER_MEMORY_LIMIT_MB`` caps the worker's address space (``RLIMIT_AS``, POSIX only), so a
parser or analysis that runs away with memory fails with ``MemoryError`` instead of taking the
host down.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from types import FrameType

from airbrief_core.data.datapack import DataPackRegistry

from .config import get_settings
from .db import Database, run_migrations
from .jobs import JobRunner, make_worker_id, mark_interrupted
from .storage import Storage

log = logging.getLogger("airbrief.worker")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m api.worker",
        description="Run AirBrief dataset and analysis jobs from the database queue.",
    )
    parser.add_argument("--once", action="store_true", help="process pending jobs, then exit")
    parser.add_argument("--threads", type=int, default=None, help="number of job threads")
    parser.add_argument(
        "--no-recover",
        action="store_true",
        help="do not mark jobs left PROCESSING/RUNNING by a previous process as interrupted",
    )
    return parser.parse_args(argv)


def apply_memory_limit(limit_mb: int | None) -> None:
    """Set the soft ``RLIMIT_AS`` to ``limit_mb`` (empty or 0 = leave it alone)."""
    if not limit_mb:
        return
    if sys.platform == "win32":
        log.warning("WORKER_MEMORY_LIMIT_MB is not supported on Windows; the worker runs without it")
        return
    import resource

    limit = limit_mb * 1024 * 1024
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)  # an unprivileged process cannot go above the hard limit
    resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    log.info("Worker address space limited to %d MB", limit // (1024 * 1024))


def main(argv: list[str] | None = None) -> int:
    """Run the worker until interrupted (or once with ``--once``). Returns the exit code."""
    args = _parse_args(argv)
    settings = get_settings()
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=settings.log_level,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        )
    for name in ("airbrief", "airbrief_core"):  # the core logs under its module names
        logging.getLogger(name).setLevel(settings.log_level)
    apply_memory_limit(settings.worker_memory_limit_mb)

    settings.ensure_dirs()
    db = Database(settings.database_url)
    db.ensure_sqlite_dir()
    if settings.auto_migrate:
        run_migrations(settings.database_url)
    if not db.ping():
        log.error("Cannot reach the database at DATABASE_URL")
        return 2
    if not args.no_recover:
        mark_interrupted(db)

    storage = Storage(settings.upload_dir)
    registry = DataPackRegistry(settings.datapack_dir)
    runner = JobRunner(
        db,
        storage,
        settings,
        registry,
        worker_id=make_worker_id("worker"),
        threads=args.threads,
    )
    if settings.job_mode != "external":
        log.warning(
            "JOB_MODE=%s: the API also runs jobs itself; this worker shares the queue safely.",
            settings.job_mode,
        )

    if args.once:
        count = runner.drain()
        runner.stop(wait=True)
        db.dispose()
        log.info("Processed %d job(s)", count)
        return 0

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("Received signal %d, stopping", signum)
        runner.stop(wait=False)

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    try:
        runner.run_forever()
    finally:
        runner.stop(wait=True)
        db.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
