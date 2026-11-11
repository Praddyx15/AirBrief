"""``/api/samples/``: the built-in sample files (generated deterministically by the core)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Response

from ..deps import AppContext, get_ctx
from ..schemas import ErrorResponse, FileInfo, SampleInfo
from ..services import datasets as service
from . import download_response, route

router = APIRouter(tags=["samples"])

_ERRORS = {404: {"model": ErrorResponse, "description": "Unknown sample"}}


@route(router, "GET", "/api/samples/", response_model=list[SampleInfo], summary="List samples")
def list_samples() -> list[SampleInfo]:
    """Every sample with its title, description, file name and the module it demonstrates."""
    return service.list_samples()


@route(
    router,
    "POST",
    "/api/samples/{name}/",
    response_model=FileInfo,
    status_code=201,
    responses={
        **_ERRORS,
        422: {"model": ErrorResponse, "description": "sample_not_dataset, unsafe_archive…"},
        507: {"model": ErrorResponse, "description": "storage_full"},
    },
    summary="Create a dataset from a sample",
)
def load_sample(
    name: str = Path(..., max_length=64),
    ctx: AppContext = Depends(get_ctx),
) -> FileInfo:
    """Creates and processes a dataset from the sample (always in the request)."""
    return service.create_from_sample(ctx, name)


@route(router, "GET", "/api/samples/{name}/download/", responses=_ERRORS, summary="Download a sample file")
def download_sample(name: str = Path(..., max_length=64)) -> Response:
    content, media_type, filename = service.sample_download(name)
    return download_response(content, media_type, filename)
