"""Error types. Every error carries a stable machine-readable ``code`` (see docs/SPEC.md §2)."""

from __future__ import annotations

from typing import Any

# code -> HTTP status used by the API. Codes not listed map to 422.
HTTP_STATUS_BY_CODE: dict[str, int] = {
    "unsupported_format": 415,
    "file_too_large": 413,
    "not_found": 404,
    "rate_limited": 429,
    "conflict": 409,
    "fetch_failed": 502,
    "cancelled": 409,
    "internal_error": 500,
    "datapack_integrity": 503,
}


class AirBriefError(Exception):
    """Base error with a stable code, a human message and optional details."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}

    @property
    def http_status(self) -> int:
        return HTTP_STATUS_BY_CODE.get(self.code, 422)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class IngestError(AirBriefError):
    """Upload could not be read: unsupported_format, file_too_large, empty_file, unsafe_archive,
    archive_too_large, too_many_files, missing_component, no_supported_content, corrupt_file,
    too_many_features, too_complex, invalid_crs, flightmd_report."""


class DataPackError(AirBriefError):
    """datapack_missing, layer_missing, datapack_invalid, datapack_integrity."""


class AnalysisError(AirBriefError):
    """invalid_params, invalid_geometry, geometry_type_not_supported, area_too_large,
    datapack_missing, layer_missing, no_tracks, report_invalid, no_features, out_of_coverage."""


class AnalysisCancelled(AirBriefError):
    def __init__(self, message: str = "Analysis was cancelled") -> None:
        super().__init__("cancelled", message)
