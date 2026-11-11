"""AirBrief C++ kernel (nanobind). Import through ``airbrief_core._native``, which falls back to
numpy/scipy when this package is not installed."""

from __future__ import annotations

from ._airbrief_native import __version__, min_separation_frames, pairs_within, points_in_polygon

__all__ = ["__version__", "min_separation_frames", "pairs_within", "points_in_polygon"]
