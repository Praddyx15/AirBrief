"""Run any analysis by kind and wrap the result in an :class:`AnalysisEnvelope`.

Parameters with ``batch`` run the module once per feature (:mod:`.batch`); the result is then a
:class:`~airbrief_core.models.batch.BatchResult`.

Errors: an unknown kind or bad params raise ``AnalysisError("invalid_params")`` (validation
errors in ``details["errors"]``); a set cancel event raises
:class:`~airbrief_core.errors.AnalysisCancelled`; a pack whose files fail their hashes raises
``DataPackError("datapack_integrity")`` (one without hashes only gets a ``datapack_unverified``
issue). Module errors propagate as they are, anything else becomes
``AirBriefError("internal_error")``.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from .analyses import REGISTRY
from .analyses.base import AnalysisContext, ProgressCallback
from .batch import run_batch
from .errors import AirBriefError, AnalysisCancelled, AnalysisError
from .models.analyses import PARAMS_MODELS, AnalysisEnvelope
from .models.common import InputModel, Severity

if TYPE_CHECKING:
    from .data.datapack import DataPack

__all__ = ["run_analysis", "run_analysis_sync", "validate_params"]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


def _validation_details(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "loc": [_json_safe(part) for part in err.get("loc", ())],
            "msg": str(err.get("msg", "")),
            "type": str(err.get("type", "")),
        }
        for err in exc.errors()
    ]


def validate_params(kind: str, params: dict[str, Any] | BaseModel) -> InputModel:
    """Validate ``params`` for ``kind`` and return the parameter model instance.

    Anything wrong is ``AnalysisError("invalid_params")``; on failed validation
    ``details["errors"]`` lists ``{loc, msg, type}``.
    """
    model_cls = PARAMS_MODELS.get(kind)
    if model_cls is None or kind not in REGISTRY:
        raise AnalysisError(
            "invalid_params",
            f"Unknown analysis kind '{kind}'",
            details={"kind": kind, "known": sorted(REGISTRY)},
        )
    if isinstance(params, model_cls):
        return params
    if isinstance(params, BaseModel):
        raw: Any = params.model_dump(mode="json", exclude_unset=True)
    elif isinstance(params, dict):
        raw = params
    else:
        raise AnalysisError(
            "invalid_params",
            "Parameters must be a JSON object",
            details={"kind": kind, "type": type(params).__name__},
        )
    try:
        return model_cls.model_validate(raw)
    except ValidationError as exc:
        errors = _validation_details(exc)
        first = errors[0] if errors else {"loc": [], "msg": "invalid"}
        where = ".".join(str(p) for p in first["loc"]) or "params"
        raise AnalysisError(
            "invalid_params",
            f"Invalid parameters for '{kind}': {where}: {first['msg']}",
            details={"kind": kind, "errors": errors},
        ) from exc


def run_analysis_sync(
    kind: str,
    params: dict[str, Any] | InputModel,
    *,
    datapack: DataPack | None = None,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> AnalysisEnvelope:
    """Validate, run and wrap one analysis synchronously.

    ``progress`` gets ``("done", 1.0)`` on success. A set ``cancel_event`` stops the run at the
    next checkpoint. Zone rules come from the params (``zone_profile`` / ``zone_rules``).
    """
    from . import __version__

    model = validate_params(kind, params)
    spec = REGISTRY[kind]
    if cancel_event is not None and cancel_event.is_set():
        raise AnalysisCancelled()

    ctx = AnalysisContext(datapack=datapack, cancel_event=cancel_event)
    if progress is not None:
        ctx.progress = progress
    if datapack is not None and (spec.requires or spec.optional):
        datapack.ensure_usable()
        if datapack.integrity == "unverified":
            ctx.note(
                "datapack_unverified",
                f"Data pack '{datapack.name}' has no content hashes, so its files could not be checked "
                "against what was built. Rebuild it with python -m seed.datapack to add them.",
                Severity.WARNING,
            )

    started = datetime.now(UTC)
    t0 = time.perf_counter()
    ctx.step("starting", 0.0)
    batch = getattr(model, "batch", None)
    result: BaseModel
    try:
        if batch is not None:
            result = run_batch(kind, model, batch.features["features"], ctx, batch.key_property, batch.from_dataset)
        else:
            result = spec.run(model, ctx)
            if not isinstance(result, spec.result_model):
                result = spec.result_model.model_validate(
                    result.model_dump() if isinstance(result, BaseModel) else result
                )
    except AirBriefError:
        raise
    except Exception as exc:  # a module bug: a crash, or a result that does not fit its model
        # generic msg: library text can hold paths and data
        raise AirBriefError(
            "internal_error",
            f"The {kind} analysis failed unexpectedly.",
            details={"kind": kind, "exception": type(exc).__name__},
        ) from exc
    duration_ms = int(round((time.perf_counter() - t0) * 1000.0))
    finished = datetime.now(UTC)
    ctx.progress("done", 1.0)

    backend: Literal["cpp", "python"] = "python"
    if kind == "show":
        from ._native import BACKEND

        backend = BACKEND

    return AnalysisEnvelope(
        airbrief_version=__version__,
        kind=kind,  # type: ignore[arg-type]
        params=model.model_dump(mode="json"),
        result=result.model_dump(mode="json"),
        datapack=datapack.name if datapack is not None else None,
        datapack_synthetic=bool(datapack.synthetic) if datapack is not None else False,
        datapack_sha256=datapack.content_sha256 if datapack is not None else None,
        backend=backend,
        started_utc=_iso(started),
        finished_utc=_iso(finished),
        duration_ms=duration_ms,
    )


async def run_analysis(
    kind: str,
    params: dict[str, Any] | InputModel,
    *,
    datapack: DataPack | None = None,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> AnalysisEnvelope:
    """Asynchronous :func:`run_analysis_sync`: the run happens in a worker thread.

    Cancelling the awaiting task sets ``cancel_event`` (one is created when none is given) so
    the worker stops at its next checkpoint.
    """
    event = cancel_event if cancel_event is not None else threading.Event()
    try:
        return await asyncio.to_thread(
            run_analysis_sync,
            kind,
            params,
            datapack=datapack,
            progress=progress,
            cancel_event=event,
        )
    except asyncio.CancelledError:
        event.set()
        raise
