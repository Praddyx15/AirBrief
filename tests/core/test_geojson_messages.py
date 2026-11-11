"""Plain-language GeoJSON rejection messages and FlightMD report detection."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from airbrief_core.errors import IngestError
from airbrief_core.io.geojson import read_geojson


def _reject(doc: object) -> IngestError:
    with pytest.raises(IngestError) as exc:
        read_geojson(json.dumps(doc).encode(), "layer")
    return exc.value


@pytest.mark.parametrize("name", ["flightmd_report_v15", "flightmd_report_v12"])
def test_flightmd_report_is_pointed_to_the_flight_audit(name: str, sample_bytes: Callable[[str], bytes]) -> None:
    with pytest.raises(IngestError) as exc:
        read_geojson(sample_bytes(name), name)
    assert exc.value.code == "flightmd_report"
    assert "Flight audit" in exc.value.message
    assert exc.value.details["report_id"]


def test_nested_flightmd_report_is_detected() -> None:
    error = _reject({"report": {"schema_version": "1.5", "metadata": {"gps_path": [[12.9, 77.5, 900.0]]}}})
    assert error.code == "flightmd_report"
    assert error.details["report_id"] is None


def test_json_without_type_has_a_plain_message() -> None:
    error = _reject({"name": "not map data", "values": [1, 2, 3]})
    assert error.code == "corrupt_file"
    assert "None" not in error.message
    assert '"type"' in error.message


def test_json_of_another_type_names_it() -> None:
    error = _reject({"type": "Topology", "objects": {}})
    assert error.code == "corrupt_file"
    assert '"Topology"' in error.message
    assert "'" not in error.message
    assert error.details["type"] == "Topology"


def test_json_list_is_not_a_geojson_object() -> None:
    error = _reject([1, 2, 3])
    assert error.code == "corrupt_file"
    assert "list" in error.message


def test_schema_version_alone_is_not_a_flightmd_report() -> None:
    error = _reject({"schema_version": "1.0", "rows": []})
    assert error.code == "corrupt_file"
