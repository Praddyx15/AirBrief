"""Database-backed jobs: dataset processing and analyses.

Jobs are ``PENDING`` rows in ``datasets`` and ``analyses``. A job is claimed with a compare-and-set
``UPDATE … WHERE id = :id AND status = 'PENDING'``: only the process whose update touched one row
runs it, so several workers can share a database. :class:`JobRunner` runs claimed jobs on a
``ThreadPoolExecutor(WORKER_THREADS)``; ``python -m api.worker`` runs the same runner in the
foreground (``JOB_MODE=external``).

Each runner keeps ``heartbeat_at`` fresh on the jobs it is running, and :func:`mark_interrupted`
fails only rows whose heartbeat went stale (their process died), so starting one process never
fails the live jobs of another.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select, update

from airbrief_core.data.datapack import DataPackRegistry
from airbrief_core.errors import AirBriefError, AnalysisCancelled
from airbrief_core.processing import json_safe

from .config import Settings
from .db import Database
from .models import Analysis, Dataset, as_utc, utcnow
from .services.datasets import process_dataset
from .storage import Storage

log = logging.getLogger("airbrief.api.jobs")

PROGRESS_MIN_INTERVAL_S = 0.5
"""Minimum time between two progress writes of the same analysis (stage changes always write)."""
CANCEL_POLL_INTERVAL_S = 0.5
HEARTBEAT_INTERVAL_S = 10.0
"""How often a runner refreshes ``heartbeat_at`` on the jobs it is running."""
STALE_AFTER_S = 60.0
"""A ``PROCESSING``/``RUNNING`` job whose heartbeat is older than this has lost its process."""
RECOVER_INTERVAL_S = 30.0
"""How often a running dispatcher fails the stale jobs of processes that died."""

INTERRUPTED_DATASET_MESSAGE = "The server stopped while this file was being processed. Upload it again."
INTERRUPTED_ANALYSIS_MESSAGE = "The server stopped while this analysis was running. Run it again."


def make_worker_id(prefix: str = "api") -> str:
    """A unique id for this process, stored in ``claimed_by``."""
    return f"{prefix}:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def mark_interrupted(db: Database, stale_after_s: float = STALE_AFTER_S) -> tuple[int, int]:
    """Fail datasets left ``PROCESSING`` and analyses left ``RUNNING`` by a process that died.

    Only rows whose heartbeat is missing or older than ``stale_after_s`` are touched (code
    ``interrupted``): the jobs of live processes keep their heartbeat fresh. Returns the number
    of ``(datasets, analyses)`` updated.
    """
    now = utcnow()
    cutoff = now - timedelta(seconds=max(0.0, stale_after_s))
    with db.session() as session:
        datasets = session.execute(
            update(Dataset)
            .where(
                Dataset.status == "PROCESSING",
                or_(Dataset.heartbeat_at.is_(None), Dataset.heartbeat_at < cutoff),
            )
            .values(
                status="FAILED",
                error_code="interrupted",
                error_message=INTERRUPTED_DATASET_MESSAGE,
                processed_at=now,
            )
        )
        analyses = session.execute(
            update(Analysis)
            .where(
                Analysis.status == "RUNNING",
                or_(Analysis.heartbeat_at.is_(None), Analysis.heartbeat_at < cutoff),
            )
            .values(
                status="FAILED",
                error_code="interrupted",
                error_message=INTERRUPTED_ANALYSIS_MESSAGE,
                stage="interrupted",
                finished_at=now,
            )
        )
        session.commit()
    counts = (int(getattr(datasets, "rowcount", 0) or 0), int(getattr(analyses, "rowcount", 0) or 0))
    if any(counts):
        log.warning("Marked %d dataset(s) and %d analysis(es) as interrupted", *counts)
    return counts


def fair_order(pending: list[tuple[str, str | None]]) -> list[str]:
    """Pending analyses ``(id, dataset_id)``, oldest first, as the order to start them: one per
    dataset in turn, so a long series on one dataset does not hold up someone else's. Analyses
    without a dataset each count as their own."""
    queues: dict[str, list[str]] = {}
    for job_id, dataset_id in pending:
        queues.setdefault(dataset_id or job_id, []).append(job_id)
    order: list[str] = []
    while queues:
        for key in list(queues):
            order.append(queues[key].pop(0))
            if not queues[key]:
                del queues[key]
    return order


def claim_dataset(db: Database, dataset_id: str, worker_id: str) -> bool:
    """Compare-and-set ``PENDING → PROCESSING``. True when this worker won the claim."""
    with db.session() as session:
        result = session.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id, Dataset.status == "PENDING")
            .values(status="PROCESSING", claimed_by=worker_id, heartbeat_at=utcnow())
        )
        session.commit()
    return int(getattr(result, "rowcount", 0) or 0) == 1


def claim_analysis(db: Database, analysis_id: str, worker_id: str) -> bool:
    """Compare-and-set ``PENDING → RUNNING``. True when this worker won the claim."""
    with db.session() as session:
        result = session.execute(
            update(Analysis)
            .where(
                Analysis.id == analysis_id,
                Analysis.status == "PENDING",
                Analysis.cancel_requested.is_(False),
            )
            .values(
                status="RUNNING",
                claimed_by=worker_id,
                heartbeat_at=utcnow(),
                started_at=utcnow(),
                progress=0.0,
                stage="starting",
            )
        )
        session.commit()
    return int(getattr(result, "rowcount", 0) or 0) == 1


class _ProgressWriter:
    """Throttled progress callback for one analysis that also polls ``cancel_requested``."""

    def __init__(self, db: Database, analysis_id: str, cancel_event: threading.Event) -> None:
        self.db = db
        self.analysis_id = analysis_id
        self.cancel_event = cancel_event
        self._last_write = 0.0
        self._last_poll = 0.0
        self._last_stage: str | None = None

    def __call__(self, stage: str, fraction: float) -> None:
        now = time.monotonic()
        stage = (stage or "")[:64]
        if stage != self._last_stage or now - self._last_write >= PROGRESS_MIN_INTERVAL_S:
            self._last_stage = stage
            self._last_write = now
            try:
                with self.db.session() as session:
                    session.execute(
                        update(Analysis)
                        .where(Analysis.id == self.analysis_id, Analysis.status == "RUNNING")
                        .values(progress=max(0.0, min(1.0, float(fraction))), stage=stage)
                    )
                    session.commit()
            except Exception:  # progress is best effort; the analysis itself must go on
                log.debug("Progress write failed for %s", self.analysis_id, exc_info=True)
        if now - self._last_poll >= CANCEL_POLL_INTERVAL_S:
            self._last_poll = now
            self.poll_cancel()

    def poll_cancel(self) -> None:
        """Set the cancel event when the row asks for it (or has been deleted)."""
        try:
            with self.db.session() as session:
                requested = session.execute(
                    select(Analysis.cancel_requested).where(Analysis.id == self.analysis_id)
                ).first()
        except Exception:
            log.debug("Cancel poll failed for %s", self.analysis_id, exc_info=True)
            return
        if requested is None or bool(requested[0]):
            self.cancel_event.set()


def _finish_analysis(db: Database, analysis_id: str, values: dict[str, Any]) -> None:
    with db.session() as session:
        session.execute(
            update(Analysis)
            .where(Analysis.id == analysis_id, Analysis.status == "RUNNING")
            .values(**values)
        )
        session.commit()


def _cancel_requested(db: Database, analysis_id: str) -> bool:
    """True when the user asked to cancel the analysis (or deleted it)."""
    try:
        with db.session() as session:
            row = session.execute(select(Analysis.cancel_requested).where(Analysis.id == analysis_id)).first()
    except Exception:
        log.debug("Cancel check failed for %s", analysis_id, exc_info=True)
        return False
    return row is None or bool(row[0])


def run_analysis_job(
    db: Database,
    registry: DataPackRegistry,
    analysis_id: str,
    cancel_event: threading.Event,
    shutdown: threading.Event | None = None,
    timeout_s: float = 0.0,
) -> None:
    """Run a claimed analysis (status ``RUNNING``) and store its result or error.

    ``shutdown`` is the runner's stop flag: a run stopped by it (and not by the user) is recorded
    as interrupted rather than cancelled. ``timeout_s`` (``JOB_TIMEOUT_S``, 0 = none) sets the
    cancel event when it runs out, so the module stops at its next checkpoint; the run is then
    recorded as FAILED ``job_timeout``.
    """
    from airbrief_core.orchestrator import run_analysis_sync

    with db.session() as session:
        row = session.get(Analysis, analysis_id)
        if row is None:
            return
        kind = row.kind
        params = dict(row.params or {})
        pack_name = row.datapack
        started_at = as_utc(row.started_at) or utcnow()

    started = time.perf_counter()
    progress = _ProgressWriter(db, analysis_id, cancel_event)
    progress.poll_cancel()

    def elapsed_ms() -> int:
        return int(round((time.perf_counter() - started) * 1000))

    timed_out = threading.Event()
    timer: threading.Timer | None = None
    if timeout_s > 0:

        def expire() -> None:
            timed_out.set()
            cancel_event.set()

        timer = threading.Timer(timeout_s, expire)
        timer.daemon = True
        timer.start()

    try:
        datapack = registry.get(pack_name) if pack_name else None
        if pack_name and datapack is None:
            log.warning("Data pack %s chosen for analysis %s is no longer available", pack_name, analysis_id)
        envelope = run_analysis_sync(
            kind,
            params,
            datapack=datapack,
            progress=progress,
            cancel_event=cancel_event,
        )
    except AnalysisCancelled as exc:
        if shutdown is not None and shutdown.is_set() and not _cancel_requested(db, analysis_id):
            _finish_analysis(
                db,
                analysis_id,
                {
                    "status": "FAILED",
                    "stage": "interrupted",
                    "error_code": "interrupted",
                    "error_message": INTERRUPTED_ANALYSIS_MESSAGE,
                    "finished_at": utcnow(),
                    "duration_ms": elapsed_ms(),
                },
            )
            return
        if timed_out.is_set() and not _cancel_requested(db, analysis_id):
            _finish_analysis(
                db,
                analysis_id,
                {
                    "status": "FAILED",
                    "stage": "failed",
                    "error_code": "job_timeout",
                    "error_message": (
                        f"The analysis was stopped after {timeout_s:g} s, the time limit for one "
                        "analysis on this server. Try a smaller area or coarser settings."
                    ),
                    "finished_at": utcnow(),
                    "duration_ms": elapsed_ms(),
                },
            )
            return
        _finish_analysis(
            db,
            analysis_id,
            {
                "status": "CANCELLED",
                "stage": "cancelled",
                "error_code": "cancelled",
                "error_message": exc.message,
                "finished_at": utcnow(),
                "duration_ms": elapsed_ms(),
            },
        )
        return
    except AirBriefError as exc:
        message = exc.message[:4000]
        if exc.code == "internal_error":
            # A module crash wrapped by the orchestrator: its text may hold paths or library
            # internals, so it goes to the log and the API gets the generic message.
            log.error("Analysis %s (%s) crashed: %s", analysis_id, kind, exc.message, exc_info=exc)
            message = f"The analysis failed unexpectedly. The server log has the details (analysis {analysis_id})."
        _finish_analysis(
            db,
            analysis_id,
            {
                "status": "FAILED",
                "stage": "failed",
                "error_code": exc.code,
                "error_message": message,
                "finished_at": utcnow(),
                "duration_ms": elapsed_ms(),
            },
        )
        return
    except ValidationError:
        # Parameters were validated when the job was created, so this is internal (a result or
        # envelope that does not fit its model): log the detail, report a generic error.
        log.exception("Analysis %s (%s) produced an invalid result", analysis_id, kind)
        _finish_analysis(
            db,
            analysis_id,
            {
                "status": "FAILED",
                "stage": "failed",
                "error_code": "internal_error",
                "error_message": f"The analysis failed unexpectedly. The server log has the details (analysis {analysis_id}).",
                "finished_at": utcnow(),
                "duration_ms": elapsed_ms(),
            },
        )
        return
    except Exception:  # a module bug must fail the job, not the worker
        log.exception("Analysis %s (%s) crashed", analysis_id, kind)
        _finish_analysis(
            db,
            analysis_id,
            {
                "status": "FAILED",
                "stage": "failed",
                "error_code": "internal_error",
                # same as above, details only go to the log
                "error_message": f"The analysis failed unexpectedly. The server log has the details (analysis {analysis_id}).",
                "finished_at": utcnow(),
                "duration_ms": elapsed_ms(),
            },
        )
        return
    finally:
        if timer is not None:
            timer.cancel()

    finished = utcnow()
    duration = int(envelope.duration_ms) if envelope.duration_ms else elapsed_ms()
    _finish_analysis(
        db,
        analysis_id,
        {
            "status": "COMPLETED",
            "progress": 1.0,
            "stage": "done",
            "result": json_safe(envelope.result),
            # The pack the run actually used: None when the chosen pack disappeared before it ran.
            "datapack": envelope.datapack,
            "datapack_synthetic": bool(envelope.datapack_synthetic),
            "datapack_sha256": envelope.datapack_sha256,
            "backend": envelope.backend,
            "error_code": None,
            "error_message": None,
            "started_at": started_at,
            "finished_at": finished,
            "duration_ms": duration,
        },
    )


class JobRunner:
    """Claims pending jobs and runs them on a thread pool."""

    def __init__(
        self,
        db: Database,
        storage: Storage,
        settings: Settings,
        registry: DataPackRegistry,
        *,
        worker_id: str | None = None,
        threads: int | None = None,
    ) -> None:
        self.db = db
        self.storage = storage
        self.settings = settings
        self.registry = registry
        self.worker_id = worker_id or make_worker_id()
        self.threads = threads or settings.worker_threads
        self.poll_interval_s = settings.worker_poll_interval_s
        self._executor: ThreadPoolExecutor | None = None
        self._dispatcher: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._inflight: dict[str, Future[None]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._last_heartbeat = 0.0
        self._last_recover = 0.0

    @property
    def running(self) -> bool:
        return self._dispatcher is not None and self._dispatcher.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._executor = ThreadPoolExecutor(max_workers=self.threads, thread_name_prefix="airbrief-job")
        self._dispatcher = threading.Thread(target=self._loop, name="airbrief-dispatcher", daemon=True)
        self._dispatcher.start()
        log.info("Job runner %s started with %d thread(s)", self.worker_id, self.threads)

    def stop(self, wait: bool = False, timeout: float = 10.0) -> None:
        """Stop dispatching, stop running analyses (recorded as interrupted), shut the pool down."""
        self._stop.set()
        self._wake.set()
        with self._lock:
            for event in self._cancel_events.values():
                event.set()
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=timeout)
            self._dispatcher = None
        if self._executor is not None:
            self._executor.shutdown(wait=wait, cancel_futures=True)
            self._executor = None

    def wake(self) -> None:
        """Ask the dispatcher to look for pending jobs now."""
        self._wake.set()

    def cancel(self, analysis_id: str) -> bool:
        """Signal a running analysis of this process to stop. True when it was running here."""
        with self._lock:
            event = self._cancel_events.get(analysis_id)
        if event is None:
            return False
        event.set()
        return True

    def run_forever(self) -> None:
        """Dispatch in the calling thread until :meth:`stop` (used by ``python -m api.worker``)."""
        self._stop.clear()
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self.threads, thread_name_prefix="airbrief-job")
        log.info("Worker %s polling every %.1f s with %d thread(s)", self.worker_id, self.poll_interval_s, self.threads)
        self._loop()

    def drain(self, timeout: float = 300.0) -> int:
        """Run every pending job and wait for them to finish. Returns the number of jobs run.

        While it waits it refreshes the heartbeat of the running jobs every
        ``HEARTBEAT_INTERVAL_S`` (``python -m api.worker --once`` has no dispatcher thread): another
        process fails jobs whose heartbeat is older than ``STALE_AFTER_S``.
        """
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self.threads, thread_name_prefix="airbrief-job")
        deadline = time.monotonic() + timeout
        last_beat = time.monotonic()
        total = 0
        while time.monotonic() < deadline:
            started = self.dispatch_pending()
            total += started
            with self._lock:
                futures = list(self._inflight.values())
            if not futures and started == 0:
                break
            if futures:
                remaining = max(0.0, deadline - time.monotonic())
                wait(futures, timeout=min(HEARTBEAT_INTERVAL_S, remaining), return_when=FIRST_COMPLETED)
                # by the clock, not by the waits: short jobs finishing must not starve a long one
                if time.monotonic() - last_beat >= HEARTBEAT_INTERVAL_S:
                    last_beat = time.monotonic()
                    self.heartbeat()
        return total

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.housekeeping()
                self.dispatch_pending()
            except Exception:  # keep the dispatcher alive through database hiccups
                log.exception("Job dispatch failed")
            self._wake.wait(min(self.poll_interval_s, HEARTBEAT_INTERVAL_S))
            self._wake.clear()

    def housekeeping(self, now: float | None = None) -> None:
        """Refresh this process's heartbeats and fail the stale jobs of dead processes, when due."""
        now = time.monotonic() if now is None else now
        if now - self._last_heartbeat >= HEARTBEAT_INTERVAL_S:
            self._last_heartbeat = now
            self.heartbeat()
        if now - self._last_recover >= RECOVER_INTERVAL_S:
            self._last_recover = now
            mark_interrupted(self.db)

    def heartbeat(self) -> int:
        """Mark the jobs this process is running as alive. Returns the number of rows touched."""
        with self._lock:
            job_ids = list(self._inflight)
        if not job_ids:
            return 0
        now = utcnow()
        with self.db.session() as session:
            datasets = session.execute(
                update(Dataset)
                .where(Dataset.id.in_(job_ids), Dataset.status == "PROCESSING", Dataset.claimed_by == self.worker_id)
                .values(heartbeat_at=now)
            )
            analyses = session.execute(
                update(Analysis)
                .where(Analysis.id.in_(job_ids), Analysis.status == "RUNNING", Analysis.claimed_by == self.worker_id)
                .values(heartbeat_at=now)
            )
            session.commit()
        return int(getattr(datasets, "rowcount", 0) or 0) + int(getattr(analyses, "rowcount", 0) or 0)

    def _capacity(self) -> int:
        with self._lock:
            return max(0, self.threads - len(self._inflight))

    def dispatch_pending(self) -> int:
        """Claim and start as many pending jobs as there are free threads. Returns the count."""
        executor = self._executor
        if executor is None or self._stop.is_set():
            return 0
        capacity = self._capacity()
        if capacity <= 0:
            return 0
        started = 0
        with self.db.session() as session:
            dataset_ids = list(
                session.scalars(
                    select(Dataset.id)
                    .where(Dataset.status == "PENDING")
                    .order_by(Dataset.created_at)
                    .limit(capacity)
                )
            )
            pending = session.execute(
                select(Analysis.id, Analysis.dataset_id)
                .where(Analysis.status == "PENDING", Analysis.cancel_requested.is_(False))
                .order_by(Analysis.created_at)
                .limit(capacity * 50)
            ).all()
        analysis_ids = fair_order([(row[0], row[1]) for row in pending])
        for dataset_id in dataset_ids:
            if started >= capacity:
                break
            if self._is_inflight(dataset_id) or not claim_dataset(self.db, dataset_id, self.worker_id):
                continue
            self._submit(dataset_id, self._run_dataset, dataset_id)
            started += 1
        for analysis_id in analysis_ids:
            if started >= capacity:
                break
            if self._is_inflight(analysis_id) or not claim_analysis(self.db, analysis_id, self.worker_id):
                continue
            event = threading.Event()
            with self._lock:
                self._cancel_events[analysis_id] = event
            self._submit(analysis_id, self._run_analysis, analysis_id, event)
            started += 1
        return started

    def _is_inflight(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._inflight

    def _submit(self, job_id: str, fn: Any, *args: Any) -> None:
        executor = self._executor
        if executor is None:
            return
        future: Future[None] = executor.submit(fn, *args)
        with self._lock:
            self._inflight[job_id] = future
        future.add_done_callback(lambda _f: self._done(job_id))

    def _done(self, job_id: str) -> None:
        with self._lock:
            self._inflight.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
        self._wake.set()

    def _run_dataset(self, dataset_id: str) -> None:
        log.info("Processing dataset %s", dataset_id)
        try:
            process_dataset(self.db, self.storage, self.settings, dataset_id)
        except Exception:  # process_dataset records failures itself; this is a last resort
            log.exception("Dataset job %s crashed", dataset_id)

    def _run_analysis(self, analysis_id: str, cancel_event: threading.Event) -> None:
        log.info("Running analysis %s", analysis_id)
        try:
            run_analysis_job(
                self.db,
                self.registry,
                analysis_id,
                cancel_event,
                shutdown=self._stop,
                timeout_s=self.settings.job_timeout,
            )
        except Exception:  # run_analysis_job records failures itself; this is a last resort
            log.exception("Analysis job %s crashed", analysis_id)
