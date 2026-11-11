"""Shared runtime context for analysis modules.

Every module exposes ``run(params, ctx) -> Result``. The context carries the data pack (may be
``None``), a progress callback and a cancellation event. Zone rules come from each run's own
``zone_profile`` (:func:`~airbrief_core.models.presets.profile_rules`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..errors import AnalysisCancelled, AnalysisError
from ..models.common import Issue, Severity

if TYPE_CHECKING:
    from ..data.datapack import DataPack

ProgressCallback = Callable[[str, float], None]

# Layer names a module may require from a data pack.
DATAPACK_LAYERS = ("dem", "population", "aerodromes", "obstacles", "pois", "water", "zones", "power", "assemblies",
                   "aerodrome_perimeters", "runways")


def _noop_progress(stage: str, fraction: float) -> None:  # noqa: ARG001 - signature contract
    return None


@dataclass
class AnalysisContext:
    """Runtime services for one analysis run."""

    datapack: DataPack | None = None
    progress: ProgressCallback = _noop_progress
    cancel_event: threading.Event | None = None
    issues: list[Issue] = field(default_factory=list)

    def step(self, stage: str, fraction: float) -> None:
        """Report progress (``fraction`` in 0..1) and honour cancellation."""
        self.check_cancelled()
        self.progress(stage, max(0.0, min(1.0, float(fraction))))

    def check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise AnalysisCancelled()

    def note(self, code: str, message: str, severity: Severity = Severity.INFO) -> None:
        """Record a context-level issue (modules copy ``ctx.issues`` into their result)."""
        self.issues.append(Issue(code=code, message=message, severity=severity))

    def has_layer(self, layer: str) -> bool:
        if self.datapack is None:
            return False
        return layer in self.datapack.layers

    def require(self, *layers: str) -> DataPack:
        """Raises ``datapack_missing`` or ``layer_missing`` when the pack can't serve ``layers``."""
        if self.datapack is None:
            raise AnalysisError(
                "datapack_missing",
                "No data pack covers this area. Seed one with "
                "`python -m seed.datapack --synthetic` or `--region bengaluru`.",
                details={"required_layers": list(layers)},
            )
        missing = [layer for layer in layers if layer not in self.datapack.layers]
        if missing:
            raise AnalysisError(
                "layer_missing",
                f"Data pack '{self.datapack.name}' lacks required layer(s): {', '.join(missing)}.",
                details={"datapack": self.datapack.name, "missing": missing},
            )
        return self.datapack
