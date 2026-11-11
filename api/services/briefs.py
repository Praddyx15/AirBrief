"""Operation evidence briefs: ``POST /api/briefs/`` (JSON) and ``POST /api/briefs/bundle/`` (zip).

Briefs are stateless: nothing is stored, every request rebuilds the brief from the analyses it
names (:func:`airbrief_core.brief.build_brief`). Access follows the analysis rules of this API:
whoever knows an analysis id may read it (``GET /api/analyses/{id}/``), so a brief includes
exactly the analyses whose ids the request gives — never a listing. Every one must exist and be
``COMPLETED``; they may come from different datasets (the brief then warns).

The bundle is self-contained, because analyses expire (72 h on public instances). Its entries have
fixed, safe names — nothing in a name comes from the user — and a fixed timestamp (the latest
analysis finish), so identical inputs give identical entries apart from the brief's
``generated_utc``; ``provenance.brief_sha256`` (which leaves it out) is identical:

* ``brief.html`` — the printable brief (no external requests);
* ``brief.json`` — the brief as JSON (``Brief``);
* ``provenance.json`` — the provenance, plus the size and SHA-256 of every other entry;
* ``signature.json`` — only when ``BRIEF_SIGNING_KEY_FILE`` is set: an Ed25519 signature of the
  exact bytes of ``provenance.json`` (:mod:`airbrief_core.signing`; check it with
  ``python -m airbrief_core.verify``);
* ``evidence.geojson`` / ``evidence.kml`` — every analysis' layers (``_analysis``, ``_layer``);
* ``zones.ed318.geojson`` — when the brief has an airspace check or flight audit: the aerodrome
  bands and zones they were judged against, as one ED-318 zone file (each zone names its
  analyses in ``extendedProperties.airbrief.analyses``);
* ``analyses/NN_<kind>.json`` — each analysis envelope (parameters and full result);
* ``tables/NN_<kind>_<table>.csv`` — each CSV table of each analysis (:data:`MAX_TABLE_ROWS` rows).
"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from airbrief_core.brief import build_brief
from airbrief_core.brief_html import brief_map_layers, render_brief_html
from airbrief_core.data.datapack import DataPackManifest
from airbrief_core.errors import AirBriefError
from airbrief_core.io.geozones import to_ed318
from airbrief_core.models.brief import MAX_BRIEF_ANALYSES, Brief, BriefDatasetRef, BriefSource, OperationMeta
from airbrief_core.models.common import UserZone
from airbrief_core.signing import SIGNATURE_FILE, SIGNED_FILE

from ..deps import AppContext
from ..errors import ApiError
from ..models import Analysis, Dataset
from .analyses import envelope_for, get_analysis, input_bbox, validate_params
from .exports import EvidenceItem, evidence_geojson, evidence_kml, table_catalog, write_csv
from .zones import ED318_KINDS, analysis_zones

log = logging.getLogger("airbrief.api.briefs")

__all__ = [
    "MAX_BUNDLE_BYTES",
    "MAX_TABLE_ROWS",
    "BriefRequest",
    "Bundle",
    "build_bundle",
    "create_brief",
    "load_sources",
]

MAX_TABLE_ROWS = 100_000
"""Rows per CSV table in a bundle (the cut is recorded in ``provenance.json``)."""

MAX_BUNDLE_BYTES = 200 * 1024 * 1024
"""Largest uncompressed bundle (all entries together)."""

_SPOOL_BYTES = 16 * 1024 * 1024
"""A bundle up to this size is built in memory, a larger one in a temporary file."""


class BriefRequest(BaseModel):
    """Body of ``POST /api/briefs/`` and ``POST /api/briefs/bundle/``."""

    model_config = ConfigDict(extra="forbid")

    analysis_ids: list[str] = Field(min_length=1, max_length=MAX_BRIEF_ANALYSES)
    operation: OperationMeta = Field(default_factory=OperationMeta)

    @field_validator("analysis_ids")
    @classmethod
    def _ids(cls, value: list[str]) -> list[str]:
        ids = list(dict.fromkeys(v.strip() for v in value))
        if any(not v or len(v) > 64 for v in ids):
            raise ValueError("analysis ids are 1 to 64 characters")
        return ids


def _dataset_ref(session: Session, row: Analysis) -> BriefDatasetRef | None:
    if not row.dataset_id:
        return None
    dataset = session.get(Dataset, row.dataset_id)
    if dataset is None:
        return None
    return BriefDatasetRef(
        id=dataset.id, filename=dataset.filename, sha256=dataset.sha256, size_bytes=dataset.size_bytes, sample=dataset.sample
    )


def _bbox(row: Analysis) -> list[float] | None:
    try:
        bounds = input_bbox(row.kind, validate_params(row.kind, dict(row.params or {})))
    except (AirBriefError, ValidationError, KeyError, ValueError, TypeError):
        # Parameters stored by an older version may no longer validate: no area cross-check then.
        log.info("No input bounds for analysis %s (%s)", row.id, row.kind)
        return None
    return list(bounds) if bounds is not None else None


def load_sources(session: Session, ids: list[str]) -> list[BriefSource]:
    """The analyses as brief sources: 404 ``not_found`` for an unknown id, 409
    ``analysis_not_finished`` for one that is not ``COMPLETED``."""
    sources: list[BriefSource] = []
    for analysis_id in ids:
        row = get_analysis(session, analysis_id)
        if row.status != "COMPLETED" or row.result is None:
            raise ApiError(
                "analysis_not_finished",
                f"Analysis '{analysis_id}' is {row.status}; a brief holds completed analyses only.",
                details={"id": analysis_id, "status": row.status},
            )
        sources.append(BriefSource(analysis_id=row.id, envelope=envelope_for(row), dataset=_dataset_ref(session, row), bbox=_bbox(row)))
    return sources


def _manifests(ctx: AppContext) -> dict[str, DataPackManifest]:
    return {pack.name: pack.manifest for pack in ctx.registry.list()}


def create_brief(ctx: AppContext, session: Session, request: BriefRequest) -> tuple[Brief, list[BriefSource]]:
    """The brief of the requested analyses and its sources (in the brief's order)."""
    sources = load_sources(session, request.analysis_ids)
    signer = ctx.settings.brief_signer
    brief = build_brief(sources, request.operation, packs=_manifests(ctx), signing_key_id=signer.key_id if signer else None)
    order = {a.analysis_id: i for i, a in enumerate(brief.provenance.analyses)}
    return brief, sorted(sources, key=lambda s: order.get(s.analysis_id, len(order)))


@dataclass
class Bundle:
    """A built bundle: the zip (positioned at 0), its size and its download name."""

    file: IO[bytes]
    size: int
    filename: str
    entries: list[str] = field(default_factory=list)

    def chunks(self, size: int = 256 * 1024) -> Iterator[bytes]:
        """The zip in chunks; closes the file at the end."""
        try:
            while True:
                chunk = self.file.read(size)
                if not chunk:
                    break
                yield chunk
        finally:
            self.file.close()


def _zip_time(brief: Brief) -> tuple[int, int, int, int, int, int]:
    """The entries' timestamp: the latest analysis finish (not the build time)."""
    stamp = brief.provenance.finished_utc or "1980-01-01T00:00:00Z"
    try:
        date, time = stamp.replace("Z", "").split("T")[:2]
        y, mo, d = (int(x) for x in date.split("-"))
        h, mi, s = (int(float(x)) for x in time.split("+")[0].split(":")[:3])
    except ValueError:
        return 1980, 1, 1, 0, 0, 0
    return (max(1980, y), mo, d, h, mi, s) if y >= 1980 else (1980, 1, 1, 0, 0, 0)


class _Writer:
    def __init__(self, archive: zipfile.ZipFile, when: tuple[int, int, int, int, int, int]) -> None:
        self.archive = archive
        self.when = when
        self.total = 0
        self.entries: list[dict[str, Any]] = []

    def add(self, name: str, data: bytes) -> None:
        self.total += len(data)
        if self.total > MAX_BUNDLE_BYTES:
            raise ApiError(
                "bundle_too_large",
                f"The bundle would exceed {MAX_BUNDLE_BYTES // (1024 * 1024)} MB. Include fewer or smaller analyses.",
                status=413,
                details={"limit_bytes": MAX_BUNDLE_BYTES},
            )
        info = zipfile.ZipInfo(name, date_time=self.when)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        self.archive.writestr(info, data)
        self.entries.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8")


def _zones_ed318(items: list[EvidenceItem], *, name: str, notice: str, watermark: str | None) -> dict[str, Any]:
    """A zone two analyses share is written once and names both. A run without a zones layer (a
    flight audit with nothing near or stored before the layer, a per-feature airspace run) adds none."""
    shared: dict[str, tuple[UserZone, list[str]]] = {}
    for item in items:
        if not isinstance((item.result.get("layers") or {}).get("zones"), dict):
            continue
        for zone in analysis_zones(item.kind, item.result):
            prefixes = shared.setdefault(zone.model_dump_json(), (zone, []))[1]
            if item.prefix not in prefixes:
                prefixes.append(item.prefix)
    doc = to_ed318([zone for zone, _ in shared.values()], name=name)
    for feature, (_, prefixes) in zip(doc["features"], shared.values(), strict=True):
        feature["properties"]["extendedProperties"]["airbrief"]["analyses"] = prefixes
    doc["airbrief"] = {"notice": notice, "watermark": watermark, "analyses": [i.prefix for i in items]}
    return doc


def build_bundle(ctx: AppContext, session: Session, request: BriefRequest) -> Bundle:
    """Build the zip bundle (see the module docstring)."""
    brief, sources = create_brief(ctx, session, request)
    notice = brief.notice
    items = [
        EvidenceItem(
            prefix=f"{i:02d}_{s.envelope['kind']}",
            analysis_id=s.analysis_id,
            kind=str(s.envelope["kind"]),
            result=dict(s.envelope.get("result") or {}),
            datapack=s.envelope.get("datapack"),
            datapack_synthetic=bool(s.envelope.get("datapack_synthetic")),
        )
        for i, s in enumerate(sources, start=1)
    ]
    # Closed by Bundle.chunks once the response is sent (or below on failure).
    spool: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES)
    truncated: dict[str, int] = {}
    try:
        with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as archive:
            writer = _Writer(archive, _zip_time(brief))
            layers = brief_map_layers(sources, brief.operation.pilot_position)
            writer.add("brief.html", render_brief_html(brief, layers).encode("utf-8"))
            writer.add("brief.json", _json_bytes(brief.model_dump(mode="json")))
            name = brief.title
            writer.add("evidence.geojson", json.dumps(evidence_geojson(items, notice=notice, watermark=brief.watermark, name=name),
                                                     ensure_ascii=False).encode("utf-8"))
            writer.add("evidence.kml", evidence_kml(items, notice=notice, watermark=brief.watermark, name=name))
            zoned = [item for item in items if item.kind in ED318_KINDS]
            if zoned:
                doc = _zones_ed318(zoned, name=name, notice=notice, watermark=brief.watermark)
                writer.add("zones.ed318.geojson", json.dumps(doc, ensure_ascii=False).encode("utf-8"))
            for item, source in zip(items, sources, strict=True):
                writer.add(f"analyses/{item.prefix}.json", _json_bytes(source.envelope))
            for item, source in zip(items, sources, strict=True):
                params = dict(source.envelope.get("params") or {})
                watermark = source.envelope.get("watermark")
                cell = f"{watermark}. {notice}" if watermark else notice
                for spec in table_catalog(item.kind, item.result):
                    if spec.key == "all":
                        continue
                    rows, columns = spec.build(item.result, params)
                    entry = f"tables/{item.prefix}_{spec.key}.csv"
                    if len(rows) > MAX_TABLE_ROWS:
                        truncated[entry] = len(rows) - MAX_TABLE_ROWS
                        rows = rows[:MAX_TABLE_ROWS]
                    writer.add(entry, write_csv(rows, columns, cell))
            provenance = {
                **brief.provenance.model_dump(mode="json"),
                "notice": notice,
                "watermark": brief.watermark,
                "bundle": {
                    "entries": writer.entries,
                    "table_row_limit": MAX_TABLE_ROWS,
                    "truncated_tables": truncated,
                    "note": ("SHA-256 of every other entry of this zip (signature.json, when present, signs this file). "
                             "Hashes of parameters and results use the canonical JSON above."),
                },
            }
            provenance_bytes = _json_bytes(provenance)
            writer.add(SIGNED_FILE, provenance_bytes)
            signer = ctx.settings.brief_signer
            if signer is not None:
                writer.add(SIGNATURE_FILE, _json_bytes(signer.sign(provenance_bytes)))
            names = [e["name"] for e in writer.entries]
        size = spool.tell()
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    return Bundle(file=spool, size=size, filename=f"airbrief_brief_{brief.provenance.brief_sha256[:12]}.zip", entries=names)
