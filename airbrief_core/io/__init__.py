"""Safe ingest and format readers: zip guards, KML/KMZ, shapefile, GeoJSON and CSV tracks."""

from __future__ import annotations

from .ingest import IngestLimits, StagedSource, detect_kind, safe_extract_zip, stage_upload
from .reader import RawFeature, RawLayer, read_staged

__all__ = [
    "IngestLimits",
    "RawFeature",
    "RawLayer",
    "StagedSource",
    "detect_kind",
    "read_staged",
    "safe_extract_zip",
    "stage_upload",
]
