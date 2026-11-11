"""Build the static, read-only demo: ``python -m seed.demo_export --out frontend/public/demo``.

The export runs the built-in samples through the real API (``fastapi.testclient.TestClient``
around :func:`api.main.create_app`, a temporary SQLite database, in-process jobs and the
deterministic synthetic data pack) and writes the endpoint JSON the frontend reads when
``NEXT_PUBLIC_DEMO=1`` (``frontend/lib/api.ts``, demo transport; ``frontend/README.md``,
"Demo mode")::

    demo/meta.json                          GET /api/meta/
    demo/health.json                        GET /health
    demo/context.json                       GET /api/context/ for the data pack's bbox (≤ 2° × 2°)
    demo/samples.json                       GET /api/samples/ (+ dataset_id, + download)
    demo/samples/<file>                     raw FlightMD report samples (not datasets)
    demo/files.json                         {items: FileInfo[], total}
    demo/files/{id}.json                    FileInfo
    demo/files/{id}/measurements.json       MeasurementsPage with every item
    demo/files/{id}/features.json           GeoJSON FeatureCollection (WGS84)
    demo/files/{id}/features/{index}.json   FeatureRecord (first 500 per dataset)
    demo/analyses/index.json                {items: AnalysisInfo[], total}
    demo/analyses/{id}.json                 AnalysisInfo
    demo/briefs/{id}.json                   POST /api/briefs/ for that one analysis (the demo merges
                                            these into a brief in the browser: lib/brief-demo.ts)
    demo/manifest.json                      {generated_utc, version, datasets, analyses}

The output directory is emptied first, but only when it looks like a previous export (it holds a
``manifest.json``) or is already empty. The export is built in a sibling folder
(``.<name>.export-in-progress``) and swapped in only when it is complete, so a failed run leaves
the previous export as it was. The command exits with status 1 when a sample cannot be
loaded or an analysis does not complete, and 2 for unusable arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi.testclient import TestClient

from airbrief_core import __version__
from airbrief_core.data.context import MAX_CONTEXT_SPAN_DEG
from airbrief_core.models.presets import UAS_PRESETS
from api.config import Settings
from api.ownership import OWNER_TOKEN_HEADER
from airbrief_core.samples import SAMPLES, build_sample

HIDDEN_SAMPLES: frozenset[str] = frozenset({"attack_zipslip", "zipbomb_small", "corrupt", "no_prj"})
"""Guard-test samples that are never shown in the UI (``components/upload/sample-utils.ts``)."""

REPORT_MODULE = "flight_audit"
"""Module of the FlightMD report samples. Its JSON samples are reports, not datasets
(``SampleSpec.is_report``); its GPX sample is a dataset (a position track to audit)."""

FEATURE_RECORD_CAP = 500
"""At most this many ``features/{index}.json`` files per dataset."""

PAGE_LIMIT_FILES = 500
PAGE_LIMIT_ANALYSES = 100
PAGE_LIMIT_MEASUREMENTS = 5000
"""Largest page sizes the API accepts for each list endpoint."""

ANALYSIS_TIMEOUT_S = 900.0
POLL_INTERVAL_S = 0.1
ACTIVE_ANALYSIS = frozenset({"PENDING", "RUNNING"})


@dataclass(frozen=True, slots=True)
class AnalysisPlan:
    """One representative analysis: ``kind`` on the dataset of ``sample`` (or no dataset)."""

    kind: str
    sample: str | None
    report_sample: str | None = None
    # Extra request parameters (the panel's defaults where they differ from the API's).
    params: dict[str, Any] | None = field(default=None, compare=False)


# Ground risk as the panel runs it: its default class (the mapping quadcopter) with every
# SORA 2.5 operational-volume term of that class, so the reopened demo shows the class's values.
_DEMO_UAS = UAS_PRESETS["mapping_quad"]
GROUND_RISK_DEMO_PARAMS: dict[str, Any] = {
    "max_dimension_m": _DEMO_UAS.max_dimension_m,
    "max_speed_ms": _DEMO_UAS.max_speed_ms,
    "takeoff_mass_kg": _DEMO_UAS.takeoff_mass_kg,
    "altitude_agl_m": 120.0,
    "volume": _DEMO_UAS.volume.model_dump(mode="json"),
    "adjacent_area": True,
}

ENVIRONMENT_DEMO_PARAMS: dict[str, Any] = {
    "date": "2026-06-21",
    "window_start": "05:30",
    "window_end": "09:30",
    "utc_offset_min": 330,
    "surface_wind_ms": 4.0,
}
"""The environment demo run: Bengaluru (IST) on the June solstice, an early-morning window."""

ANALYSIS_PLANS: tuple[AnalysisPlan, ...] = (
    AnalysisPlan("survey", "bengaluru_survey"),
    AnalysisPlan("airspace", "bengaluru_survey"),
    AnalysisPlan("ground_risk", "bengaluru_survey", params=GROUND_RISK_DEMO_PARAMS),
    AnalysisPlan("evtol_siting", "bengaluru_evtol"),
    AnalysisPlan("evtol_corridor", "bengaluru_evtol"),
    AnalysisPlan("show", "show_50_drones"),
    AnalysisPlan("show", "show_50_drones_clean"),
    AnalysisPlan("flight_audit", None, report_sample="flightmd_report_v15"),
    AnalysisPlan("flight_audit", "training_flight_gpx"),
    # No observer points in the sample: the pilot is assumed at the area centre (an issue says so).
    AnalysisPlan("line_of_sight", "bengaluru_survey"),
    # Environment needs a date (and reads best with a planned window): a fixed one keeps the demo
    # reproducible. The window starts before sunrise, so the daylight check has something to say.
    AnalysisPlan("environment", "bengaluru_survey", params=ENVIRONMENT_DEMO_PARAMS),
)


class ExportError(RuntimeError):
    """A step of the export failed (reported on stderr, exit status 1)."""


class OutDirError(ExportError):
    """The output directory cannot be used (exit status 2)."""


@dataclass(slots=True)
class ExportSummary:
    """What was written, for the manifest and the console summary."""

    datasets: list[dict[str, Any]] = field(default_factory=list)
    analyses: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    files_written: int = 0


def demo_samples() -> list[str]:
    """Names of the samples that become demo datasets, in catalogue order."""
    return [
        name
        for name, spec in SAMPLES.items()
        if name not in HIDDEN_SAMPLES and not spec.is_report
    ]


def report_samples() -> list[str]:
    """Names of the FlightMD report samples (downloadable files, not datasets)."""
    return [name for name, spec in SAMPLES.items() if spec.is_report]


def dump_json(value: Any) -> str:
    """Compact, UTF-8 JSON text with a trailing newline."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


class Writer:
    """Writes files below the output directory and counts them."""

    def __init__(self, root: Path, summary: ExportSummary) -> None:
        self.root = root
        self.summary = summary

    def json(self, relative: str, value: Any) -> None:
        """Write ``value`` as JSON to ``root/relative`` (POSIX-style relative path)."""
        path = self.root.joinpath(*PurePosixPath(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_json(value), encoding="utf-8", newline="\n")
        self.summary.files_written += 1

    def raw(self, relative: str, data: bytes) -> None:
        path = self.root.joinpath(*PurePosixPath(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.summary.files_written += 1


def check_out_dir(out: Path) -> None:
    """Refuse (OutDirError) an ``out`` that is not ours to replace: a file, or a non-empty
    directory without ``manifest.json``."""
    if out.exists() and not out.is_dir():
        raise OutDirError(f"{out} exists and is not a directory")
    if out.is_dir() and any(out.iterdir()) and not (out / "manifest.json").is_file():
        raise OutDirError(
            f"{out} is not empty and has no manifest.json; refusing to delete it "
            "(pick an empty directory or a previous demo export)"
        )


def staging_dir(out: Path) -> Path:
    """Where an export is built before it replaces ``out`` (a sibling, so the swap is a rename)."""
    return out.parent / f".{out.name}.export-in-progress"


def previous_dir(out: Path) -> Path:
    """Where the replaced export waits to be deleted (a sibling; the name marks it as ours)."""
    return out.parent / f".{out.name}.export-previous"


def _make_writable_and_retry(func: Callable[..., Any], path: str, _exc: BaseException) -> None:
    # Windows refuses to delete read-only entries: clear the flag and try once more.
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    func(path)


def _remove(path: Path, attempts: int = 5) -> None:
    """Delete a file or folder; retries briefly (Windows can hold just-written files for a moment)."""
    for attempt in range(attempts):
        try:
            if path.is_dir() and not path.is_symlink():
                if sys.version_info >= (3, 12):
                    shutil.rmtree(path, onexc=_make_writable_and_retry)
                else:  # Python 3.11: `onerror` passes exc_info instead of the exception
                    shutil.rmtree(path, onerror=lambda func, name, info: _make_writable_and_retry(func, name, info[1]))
            elif path.exists() or path.is_symlink():
                path.unlink()
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


def replace_out_dir(out: Path, staging: Path) -> None:
    """Swap a finished export in for ``out`` (whose previous contents were checked as ours).

    Renames only, so ``out`` is never half-deleted: the old export is moved aside, the new one
    moved in, and only then is the old one deleted (left for the next run if that fails).
    """
    previous = previous_dir(out)
    _remove(previous)
    if out.exists():
        out.rename(previous)
    try:
        staging.rename(out)
    except OSError:
        if previous.exists() and not out.exists():
            previous.rename(out)  # put the old export back
        raise
    try:
        _remove(previous)
    except OSError as exc:
        print(f"note: could not delete the previous export at {previous} ({exc}); the next run removes it", file=sys.stderr)


def get_json(client: TestClient, path: str, params: dict[str, Any] | None = None) -> Any:
    """Parsed body of ``GET path``; anything but a 200 is an ExportError."""
    response = client.get(path, params=params)
    if response.status_code != 200:
        raise ExportError(f"GET {path} → {response.status_code}: {response.text[:500]}")
    return response.json()


def post_json(client: TestClient, path: str, body: dict[str, Any]) -> Any:
    response = client.post(path, json=body)
    if response.status_code != 200:
        raise ExportError(f"POST {path} → {response.status_code}: {response.text[:500]}")
    return response.json()


def demo_context(client: TestClient, meta: dict[str, Any]) -> dict[str, Any] | None:
    """``GET /api/context/`` for the first data pack's whole bbox (cut to the 2° × 2° the API
    allows, around the pack's centre): the browser filters it to the map view and searches its
    aerodromes, since the static demo has no API. None without a data pack."""
    packs = meta.get("datapacks") or []
    if not packs:
        return None
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in packs[0]["bbox"])
    half = MAX_CONTEXT_SPAN_DEG / 2
    cx, cy = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
    view = [max(min_lon, cx - half), max(min_lat, cy - half), min(max_lon, cx + half), min(max_lat, cy + half)]
    context: dict[str, Any] = get_json(client, "/api/context/", {"bbox": ",".join(f"{v:.6f}" for v in view)})
    return context


def get_bytes(client: TestClient, path: str) -> bytes:
    response = client.get(path)
    if response.status_code != 200:
        raise ExportError(f"GET {path} → {response.status_code}: {response.text[:500]}")
    return response.content


def get_all_pages(client: TestClient, path: str, limit: int) -> tuple[list[Any], dict[str, Any]]:
    """Every item of a ``{items, total}`` list endpoint; also returns the first page."""
    items: list[Any] = []
    first: dict[str, Any] | None = None
    offset = 0
    while True:
        page = get_json(client, path, {"limit": limit, "offset": offset})
        if first is None:
            first = page
        batch = page.get("items", [])
        items.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(page.get("total", 0)):
            break
    return items, first or {}


def wait_for_analysis(client: TestClient, analysis_id: str, timeout: float) -> dict[str, Any]:
    """Poll ``GET /api/analyses/{id}/`` until it leaves PENDING/RUNNING (ExportError after
    ``timeout`` seconds)."""
    deadline = time.monotonic() + timeout
    while True:
        info: dict[str, Any] = get_json(client, f"/api/analyses/{analysis_id}/")
        if info.get("status") not in ACTIVE_ANALYSIS:
            return info
        if time.monotonic() > deadline:
            raise ExportError(f"analysis {analysis_id} still {info.get('status')} after {timeout:.0f} s")
        time.sleep(POLL_INTERVAL_S)


def load_samples(client: TestClient, summary: ExportSummary) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """``POST /api/samples/{name}/`` for every demo sample; returns ``{name: FileInfo}`` and
    ``{dataset id: owner token}`` (the analyses are run as the owner's)."""
    loaded: dict[str, dict[str, Any]] = {}
    tokens: dict[str, str] = {}
    for name in demo_samples():
        response = client.post(f"/api/samples/{name}/")
        if response.status_code != 201:
            summary.failures.append(f"sample {name}: HTTP {response.status_code} {response.text[:300]}")
            continue
        info: dict[str, Any] = response.json()
        token = info.pop("owner_token", None)  # never let a token reach the public demo files
        if token:
            tokens[str(info["id"])] = token
        if info.get("status") != "COMPLETED":
            error = info.get("error") or {}
            summary.failures.append(
                f"sample {name}: dataset {info.get('status')} ({error.get('code')}: {error.get('message')})"
            )
        loaded[name] = info
    return loaded, tokens


def run_analyses(
    client: TestClient,
    datasets: dict[str, dict[str, Any]],
    summary: ExportSummary,
    tokens: dict[str, str],
) -> list[dict[str, Any]]:
    """Queue every :data:`ANALYSIS_PLANS` entry, wait for each, return the final AnalysisInfo."""
    queued: list[tuple[AnalysisPlan, str]] = []
    for plan in ANALYSIS_PLANS:
        body: dict[str, Any] = {"kind": plan.kind, "params": dict(plan.params or {})}
        headers: dict[str, str] = {}
        if plan.sample is not None:
            dataset = datasets.get(plan.sample)
            if dataset is None or dataset.get("status") != "COMPLETED":
                summary.failures.append(f"analysis {plan.kind}: sample {plan.sample} is not loaded")
                continue
            body["dataset_id"] = dataset["id"]
            if dataset["id"] in tokens:
                headers[OWNER_TOKEN_HEADER] = tokens[dataset["id"]]
        if plan.report_sample is not None:
            body["params"]["report"] = json.loads(build_sample(plan.report_sample))
        response = client.post("/api/analyses/", json=body, headers=headers)
        if response.status_code != 202:
            summary.failures.append(
                f"analysis {plan.kind} ({plan.sample or plan.report_sample}): "
                f"HTTP {response.status_code} {response.text[:300]}"
            )
            continue
        queued.append((plan, response.json()["id"]))

    finished: list[dict[str, Any]] = []
    for plan, analysis_id in queued:
        label = plan.sample or plan.report_sample or "-"
        try:
            info = wait_for_analysis(client, analysis_id, ANALYSIS_TIMEOUT_S)
        except ExportError as exc:
            summary.failures.append(f"analysis {plan.kind} ({label}): {exc}")
            continue
        if info.get("status") != "COMPLETED":
            error = info.get("error") or {}
            summary.failures.append(
                f"analysis {plan.kind} ({label}): {info.get('status')} "
                f"({error.get('code')}: {error.get('message')})"
            )
        finished.append(info)
        summary.analyses.append(
            {
                "id": info["id"],
                "kind": info["kind"],
                "status": info["status"],
                "dataset_id": info.get("dataset_id"),
                "sample": label,
                "duration_ms": info.get("duration_ms"),
            }
        )
    return finished


def export_dataset(client: TestClient, writer: Writer, info: dict[str, Any]) -> int:
    """Write the per-dataset files; returns the number of feature records written."""
    file_id = str(info["id"])
    base = f"/api/files/{file_id}"
    writer.json(f"files/{file_id}.json", get_json(client, f"{base}/"))
    if info.get("status") != "COMPLETED":
        return 0

    items, first = get_all_pages(client, f"{base}/measurements/", PAGE_LIMIT_MEASUREMENTS)
    measurements = dict(first)
    measurements.update(
        {"file_id": file_id, "items": items, "total": len(items), "limit": len(items), "offset": 0}
    )
    writer.json(f"files/{file_id}/measurements.json", measurements)

    collection: dict[str, Any] = get_json(client, f"{base}/features/")
    writer.json(f"files/{file_id}/features.json", collection)

    indices: set[int] = set()
    for feature in collection.get("features", []):
        index = (feature.get("properties") or {}).get("_index")
        if isinstance(index, int):
            indices.add(index)
    if not indices:
        indices = set(range(int(info.get("feature_count") or 0)))
    selected = sorted(indices)[:FEATURE_RECORD_CAP]
    for index in selected:
        writer.json(f"files/{file_id}/features/{index}.json", get_json(client, f"{base}/features/{index}/"))
    return len(selected)


def export_all(client: TestClient, writer: Writer, summary: ExportSummary) -> None:
    datasets, tokens = load_samples(client, summary)
    run_analyses(client, datasets, summary, tokens)

    writer.json("meta.json", get_json(client, "/api/meta/"))
    writer.json("health.json", get_json(client, "/health"))
    context = demo_context(client, get_json(client, "/api/meta/"))
    if context is not None:
        writer.json("context.json", context)

    # Samples: the API list in catalogue order, plus the demo-only fields.
    samples: list[dict[str, Any]] = get_json(client, "/api/samples/")
    reports = set(report_samples())
    demo_entries: list[dict[str, Any]] = []
    for sample in samples:
        name = sample["name"]
        entry = dict(sample)
        dataset = datasets.get(name)
        if dataset is not None and dataset.get("status") == "COMPLETED":
            entry["dataset_id"] = dataset["id"]
        if name in reports:
            filename = PurePosixPath(str(sample["filename"])).name
            relative = f"samples/{filename}"
            writer.raw(relative, get_bytes(client, f"/api/samples/{name}/download/"))
            entry["download"] = relative
        demo_entries.append(entry)
    writer.json("samples.json", demo_entries)

    # Files: every dataset, ordered like the sample catalogue (stable across runs).
    file_items, _ = get_all_pages(client, "/api/files/", PAGE_LIMIT_FILES)
    order = {name: position for position, name in enumerate(SAMPLES)}
    file_items.sort(key=lambda f: (order.get(str(f.get("sample")), len(order)), str(f.get("filename"))))
    writer.json("files.json", {"items": file_items, "total": len(file_items)})
    for info in file_items:
        records = export_dataset(client, writer, info)
        summary.datasets.append(
            {
                "id": info["id"],
                "sample": info.get("sample"),
                "filename": info.get("filename"),
                "status": info.get("status"),
                "feature_count": info.get("feature_count"),
                "feature_records": records,
            }
        )

    # Analyses: in plan order (the API lists newest first, which is not stable).
    analysis_items, _ = get_all_pages(client, "/api/analyses/", PAGE_LIMIT_ANALYSES)
    plan_order = {a["id"]: position for position, a in enumerate(summary.analyses)}
    analysis_items.sort(key=lambda a: (plan_order.get(a["id"], len(plan_order)), str(a["id"])))
    writer.json("analyses/index.json", {"items": analysis_items, "total": len(analysis_items)})
    for item in analysis_items:
        analysis_id = str(item["id"])
        writer.json(f"analyses/{analysis_id}.json", get_json(client, f"/api/analyses/{analysis_id}/"))
        if item.get("status") == "COMPLETED":
            writer.json(f"briefs/{analysis_id}.json", post_json(client, "/api/briefs/", {"analysis_ids": [analysis_id]}))


def build_settings(work: Path, datapack_dir: Path, datapack: str) -> Settings:
    """Settings for an isolated export run below ``work``."""
    data_dir = work / "data"
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="development",
        log_level="WARNING",
        database_url=f"sqlite:///{(work / 'demo-export.db').as_posix()}",
        auto_migrate=True,
        data_dir=data_dir,
        upload_dir=data_dir / "uploads",
        datapack_dir=datapack_dir,
        default_datapack=datapack,
        allow_global_listing=True,  # the export lists everything it created
        max_upload_mb=500,
        inline_process_max_mb=500,
        job_mode="inprocess",
        worker_threads=2,
        worker_poll_interval_s=0.2,
        rate_limit_per_hour=1_000_000,
        upload_ttl_hours=0,
        flightmd_api_url=None,
    )


def run_export(out: Path, datapack_dir: Path | None = None, datapack: str = "demo") -> ExportSummary:
    """Run the whole export into ``out``. ExportError when ``out`` can't be used or the data
    pack is missing."""
    from api.main import create_app

    # Built next to `out` and only swapped in once its complete, so a failed run leaves the
    # previous export untouched and a half-written one is never served.
    check_out_dir(out)
    staging = staging_dir(out)
    _remove(staging)  # left over from an interrupted run; the name is ours
    staging.mkdir(parents=True)
    summary = ExportSummary()
    writer = Writer(staging, summary)
    try:
        with tempfile.TemporaryDirectory(prefix="airbrief-demo-", ignore_cleanup_errors=True) as tmp:
            work = Path(tmp)
            if datapack_dir is None:
                from seed.datapack import build_synthetic_pack

                datapack_dir = work / "datapacks"
                datapack_dir.mkdir(parents=True, exist_ok=True)
                build_synthetic_pack(datapack_dir, datapack)
            elif not (datapack_dir / datapack).is_dir():
                raise ExportError(f"data pack '{datapack}' not found in {datapack_dir}")

            settings = build_settings(work, datapack_dir, datapack)
            with TestClient(create_app(settings)) as client:
                export_all(client, writer, summary)

        writer.json(
            "manifest.json",
            {
                "generated_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "version": __version__,
                "datasets": summary.datasets,
                "analyses": summary.analyses,
            },
        )
        replace_out_dir(out, staging)
    except BaseException:
        if staging.exists():
            _remove(staging)
        raise
    return summary


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m seed.demo_export",
        description="Run the samples through the API and write the static demo JSON.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory, e.g. frontend/public/demo (emptied first if it is a previous export).",
    )
    parser.add_argument(
        "--datapack-dir",
        type=Path,
        default=None,
        help="Directory containing the data pack folder (default: build the synthetic pack in a temp dir).",
    )
    parser.add_argument(
        "--datapack",
        default="demo",
        help="Data pack name inside the data-pack directory (default: demo).",
    )
    return parser.parse_args(argv)


def _print_summary(out: Path, summary: ExportSummary) -> None:
    print(f"Demo export -> {out.as_posix()}")  # ASCII: Windows consoles may not encode arrows
    print(f"  datasets ({len(summary.datasets)}):")
    for d in summary.datasets:
        print(
            f"    {str(d['sample']):<24} {str(d['status']):<10} "
            f"{d['feature_count'] or 0:>6} features, {d['feature_records']:>4} records  {d['id']}"
        )
    print(f"  analyses ({len(summary.analyses)}):")
    for a in summary.analyses:
        duration = a.get("duration_ms")
        timing = f"{duration / 1000:.1f} s" if isinstance(duration, int | float) else "-"
        print(f"    {a['kind']:<16} {a['sample']:<24} {a['status']:<10} {timing:>8}  {a['id']}")
    print(f"  files written: {summary.files_written}")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code (0 ok, 1 failures, 2 bad arguments)."""
    args = _parse_args(argv)
    out: Path = args.out
    datapack_dir: Path | None = args.datapack_dir
    try:
        summary = run_export(out, datapack_dir, args.datapack)
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, OutDirError) else 1
    _print_summary(out, summary)
    if summary.failures:
        print(f"{len(summary.failures)} failure(s):", file=sys.stderr)
        for failure in summary.failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("Demo export complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
