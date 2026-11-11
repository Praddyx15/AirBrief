"""Adapters for sibling projects (FlightMD) and plain position tracks. Optional third-party
packages are imported lazily."""

from __future__ import annotations

from .flightmd import FlightTrack, parse_flightmd_report
from .position_track import parse_position_track

__all__ = ["FlightTrack", "parse_flightmd_report", "parse_position_track"]
