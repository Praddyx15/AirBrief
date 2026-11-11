"""Models of how a dataset's features become zones, and of the aerodrome band evidence.

See ``airbrief_core/analyses/zones.py`` and ``docs/features/zones.md``.
"""

from __future__ import annotations

import re
from datetime import UTC, timedelta
from typing import Any, Literal

from pydantic import Field

from .common import DistanceFrom, InputModel, OutputModel, ZoneBasis, ZoneKind

MAX_ZONE_DATASETS = 10
"""Datasets one analysis may read zones from (``zone_dataset_ids``)."""

MAX_DATASET_ZONES = 5_000
"""Zones taken from datasets after the bounding-box filter (the request's own ``user_zones``: 200)."""

MAX_PLANNED_WINDOW = timedelta(days=31)

_DATASET_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")

BandCeiling = Literal["exceedance", "permission"]
"""Flight audit: flying above a band's free-flight ceiling with yellow above it (DGCA 8–12 km above
60 m) is an exceedance (fail), or flight in that yellow zone, permission required (caution)."""

UnverifiedBand = Literal["permission", "exclude"]
"""eVTOL: a band the published rule does not define (DGCA 0–5 km) is costed as permission required,
or excluded like a red zone."""


def check_zone_profile(p: Any) -> None:
    """``zone_rules`` go with ``zone_profile = "custom"``: a request that sends rules without a
    profile (clients older than profiles) gets "custom". Run by every params model with a
    ``zone_profile`` (airspace, flight audit, eVTOL)."""
    if p.zone_rules is not None:
        if "zone_profile" in p.model_fields_set and p.zone_profile != "custom":
            raise ValueError("zone_rules are used with zone_profile 'custom' only")
        p.zone_profile = "custom"
    elif p.zone_profile == "custom":
        raise ValueError("zone_profile 'custom' needs zone_rules")


def check_zone_inputs(p: Any) -> None:
    """Run by ``AirspaceParams`` after validation; also turns the planned window to UTC."""
    start, end = p.planned_start_utc, p.planned_end_utc
    if (start is None) != (end is None):
        raise ValueError("give both planned_start_utc and planned_end_utc, or neither")
    if start is not None and end is not None:
        p.planned_start_utc, p.planned_end_utc = start.astimezone(UTC), end.astimezone(UTC)
        if end <= start:
            raise ValueError("planned_end_utc must be after planned_start_utc")
        if end - start > MAX_PLANNED_WINDOW:
            raise ValueError("the planned window may last at most 31 days")
    check_zone_profile(p)
    if any(not 1 <= len(k.strip()) <= 16 for k in p.aerodrome_operational):
        raise ValueError("aerodrome_operational keys are aerodrome idents (1 to 16 characters)")
    if any(not _DATASET_ID.match(i) for i in p.zone_dataset_ids):
        raise ValueError("zone_dataset_ids must be dataset ids")
    unknown = set(p.zone_mappings) - set(p.zone_dataset_ids)
    if unknown:
        raise ValueError("zone_mappings may only name datasets listed in zone_dataset_ids")


class ZoneLayerMapping(InputModel):
    """Which feature properties of a dataset give each zone's kind, ceiling and validity.

    A field left out is looked for by its usual names: the kind in ``zone``, ``colour``,
    ``color`` or ``restriction`` (``red``/``yellow``/``green`` or an ED-269/ED-318 restriction
    code), the ceiling in ``ceiling_agl_m``, ``ceiling_m`` or ``ceiling``, the validity in
    ``valid_from``/``valid_to``. Features whose kind is missing or unknown get ``default_zone``.
    ``layer`` keeps only the features of that layer (KML folder, shapefile, …).
    """

    zone_field: str | None = Field(default=None, max_length=100)
    default_zone: ZoneKind = "red"
    ceiling_field: str | None = Field(default=None, max_length=100)
    valid_from_field: str | None = Field(default=None, max_length=100)
    valid_to_field: str | None = Field(default=None, max_length=100)
    layer: str | None = Field(default=None, max_length=200)


class BandEvidence(OutputModel):
    """One aerodrome band the flight reaches, with the worst height margin found in it.

    ``samples`` counts the profile samples (routes), grid cells (areas), vertices or, for a flight
    audit, the track's fixes that fall in the band; ``worst_agl_m`` is the highest (planned or
    flown) height above ground among them and
    ``agl_margin_m`` the band's free-flight ceiling minus it (negative: above the ceiling; None
    when the band has no free-flight ceiling). ``zone`` is the zone at that height.
    """

    ident: str | None = None
    name: str
    band: str
    inner_km: float
    outer_km: float
    zone: ZoneKind
    permission_required: bool
    basis: ZoneBasis = "rule"
    distance_from: DistanceFrom | None = None  # None: the "elsewhere" band
    ceiling_agl_m: float | None = None
    worst_agl_m: float | None = None
    agl_margin_m: float | None = None
    samples: int
    sample_kind: Literal["profile", "grid", "vertices", "track"]
