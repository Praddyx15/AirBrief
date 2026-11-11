"""``GET /api/meta/``: modules with their parameter JSON schemas, presets, data packs and limits."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from airbrief_core import __version__
from airbrief_core.analyses import REGISTRY
from airbrief_core.models.common import SCHEMA_VERSION
from airbrief_core.models.presets import CAMERA_PRESETS, UAS_PRESETS, ZONE_RULE_PROFILES

from ..config import FLIGHTMD_MAX_BYTES, MB
from ..deps import AppContext, get_ctx
from ..schemas import DatapackInfo, FlightmdMeta, MetaResponse, ModuleMeta, SigningMeta
from . import route

router = APIRouter(tags=["meta"])


@lru_cache(maxsize=1)
def _modules() -> tuple[ModuleMeta, ...]:
    modules: list[ModuleMeta] = []
    for kind, spec in REGISTRY.items():
        schema: dict[str, Any] = spec.params_model.model_json_schema()
        modules.append(
            ModuleMeta(
                kind=kind,
                title=spec.title,
                description=spec.description,
                requires=list(spec.requires),
                optional=list(spec.optional),
                params_schema=schema,
            )
        )
    return tuple(modules)


def _datapacks(ctx: AppContext) -> list[DatapackInfo]:
    packs: list[DatapackInfo] = []
    for pack in ctx.registry.list():
        packs.append(DatapackInfo.model_validate(pack.info()))
    return packs


@route(router, "GET", "/api/meta/", response_model=MetaResponse, summary="Capabilities and presets")
async def meta(ctx: AppContext = Depends(get_ctx)) -> MetaResponse:
    settings = ctx.settings
    datapacks = await run_in_threadpool(_datapacks, ctx)
    signer = settings.brief_signer
    return MetaResponse(
        version=__version__,
        schema_version=SCHEMA_VERSION,
        modules=list(_modules()),
        cameras=dict(CAMERA_PRESETS),
        uas_presets=dict(UAS_PRESETS),
        zone_rules=ZONE_RULE_PROFILES["dgca_2021"],
        zone_profiles=dict(ZONE_RULE_PROFILES),
        datapacks=datapacks,
        limits={
            "max_upload_mb": settings.max_upload_mb,
            "max_unzipped_mb": settings.max_unzipped_mb,
            "max_archive_files": settings.max_archive_files,
            "max_features": settings.max_features,
            "inline_process_max_mb": settings.inline_process_max_bytes / MB,
            "rate_limit_per_hour": settings.rate_limit_per_hour,
            "upload_ttl_hours": settings.upload_ttl_hours,
            "flightmd_max_mb": FLIGHTMD_MAX_BYTES / (1024 * 1024),
            "flightmd_fetch_timeout_s": settings.flightmd_fetch_timeout_s,
        },
        flightmd=FlightmdMeta(configured=bool(settings.flightmd_api_url), url=settings.flightmd_origin),
        global_listing=settings.global_listing_enabled,
        owner_tokens_required=settings.owner_token_required,
        app_env=settings.app_env,
        signing=SigningMeta(key_id=signer.key_id, public_key=signer.public_key_b64) if signer else None,
    )
