"""Plain position-track adapter: a recorded track (KML ``gx:Track``, GPX ``trk``, CSV) → the same
:class:`~airbrief_core.integrations.flightmd.FlightTrack` the FlightMD adapter produces, so the
flight audit treats both alike. The FlightMD fields of ``info`` stay ``None``.

Heights keep their recorded meaning: ``altitude_mode`` travels with the track and the audit
decides how to read it (``FlightAuditParams.track_heights``). A 2D track gets ``alt = 0``
everywhere and ``has_heights=False``, and the audit reports its heights as not assessed (never a
"pass").
"""

from __future__ import annotations

from ..errors import AnalysisError
from ..models.analyses import FlightInfo
from ..models.tracks import AuditTrackInput
from .flightmd import FlightTrack

__all__ = ["parse_position_track"]


def parse_position_track(track: AuditTrackInput) -> FlightTrack:
    """``[lon, lat, alt]`` per fix; fewer than two fixes is ``AnalysisError("track_invalid")``."""
    if len(track.coordinates) < 2:
        raise AnalysisError(
            "track_invalid",
            "The position track needs at least two fixes.",
            details={"points": len(track.coordinates)},
        )
    has_heights = any(len(c) > 2 for c in track.coordinates)
    points = [[float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0] for c in track.coordinates]
    # A 2D track is reported by the audit itself (issue ``heights_not_assessed``).
    notes: list[str] = []
    times = [float(t) for t in track.times] if track.times is not None else None
    duration = round(times[-1] - times[0], 3) if times else None
    info = FlightInfo(
        source="track",
        track_name=track.name,
        track_format=track.source_format,
        altitude_mode=track.altitude_mode,
        feature_index=track.feature_index,
        log_start_utc=track.time_origin_utc,
        duration_s=duration,
        points=len(points),
    )
    return FlightTrack(
        points=points,
        info=info,
        schema_version=None,
        notes=notes,
        times=times,
        altitude_mode=track.altitude_mode,
        has_heights=has_heights,
    )
