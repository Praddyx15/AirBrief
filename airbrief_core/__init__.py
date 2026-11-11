"""AirBrief core: open-source geospatial pre-flight intelligence.

Public API:

* :func:`process_file` — ingest and measure an uploaded file (shapefile zip, KML/KMZ, GeoJSON,
  CSV track) into a :class:`~airbrief_core.models.DatasetResult`.
* :func:`run_analysis` / :func:`run_analysis_sync` — run an analysis by kind and get an
  :class:`~airbrief_core.models.AnalysisEnvelope`.
* :data:`REGISTRY` — the analysis kinds with their models and data requirements.
* :mod:`errors`, :mod:`models`, :data:`SCHEMA_VERSION`.

The heavy entry points (processing, orchestrator, analyses) are imported lazily on first
attribute access, so ``import airbrief_core`` stays light and free of import cycles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import errors, models
from .errors import AirBriefError, AnalysisCancelled, AnalysisError, DataPackError, IngestError
from .models.common import SCHEMA_VERSION

__version__ = "0.1.0"

if TYPE_CHECKING:
    from .analyses import REGISTRY
    from .orchestrator import run_analysis, run_analysis_sync
    from .processing import process_file

__all__ = [
    "REGISTRY",
    "SCHEMA_VERSION",
    "AirBriefError",
    "AnalysisCancelled",
    "AnalysisError",
    "DataPackError",
    "IngestError",
    "__version__",
    "errors",
    "models",
    "process_file",
    "run_analysis",
    "run_analysis_sync",
]

_LAZY: dict[str, tuple[str, str]] = {
    "process_file": (".processing", "process_file"),
    "run_analysis": (".orchestrator", "run_analysis"),
    "run_analysis_sync": (".orchestrator", "run_analysis_sync"),
    "REGISTRY": (".analyses", "REGISTRY"),
}


def __getattr__(name: str) -> Any:
    """Import the heavy public entry points on first use (PEP 562)."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(target[0], __name__), target[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
