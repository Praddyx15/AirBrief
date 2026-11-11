"""Network access for the seed scripts: the only module in the core that touches the network.

Analyses never call anything here; they read local data packs only.
"""

from __future__ import annotations

import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..errors import DataPackError
from .sources import OVERPASS_ENDPOINTS, USER_AGENT

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int | None], None]
"""Download progress callback: ``(bytes_done, bytes_total_or_None)``."""

_CHUNK = 1 << 16
_RESUME_MAX_AGE_S = 3600.0  # an older partial file may be of a version the server no longer has
_NO_RETRY_STATUS = {400, 401, 403, 404, 410}


def _sleep_backoff(attempt: int, base: float = 2.0, cap: float = 60.0) -> None:
    time.sleep(min(cap, base * (2**attempt)))


def _fetch_error(url: str, message: str, status: int | None = None) -> DataPackError:
    return DataPackError("fetch_failed", message, details={"url": url, "status": status})


def _https_request(url: str, **kwargs: Any) -> urllib.request.Request:
    # every source is a fixed https URL; never let urllib open file: or ftp: from a typo
    if urllib.parse.urlsplit(url).scheme != "https":
        raise _fetch_error(url, "Only https:// sources are downloaded.")
    return urllib.request.Request(url, **kwargs)


def download(
    url: str,
    dest: Path,
    timeout: float = 60.0,
    retries: int = 3,
    user_agent: str = USER_AGENT,
    *,
    progress: ProgressFn | None = None,
    overwrite: bool = False,
) -> Path:
    """Download ``url`` to ``dest`` and return ``dest``.

    The body is written to ``<dest>.part`` first. A partial file from an attempt in the last hour
    is resumed with an HTTP ``Range`` request; an older one, a server that ignores ranges (200), a
    range that does not start where the file ends, or a 416 start the download again. The
    ``.part`` file is renamed only on success, so a present ``dest`` is always complete and is
    reused unless ``overwrite`` is set.

    Raises ``DataPackError("fetch_failed")`` after ``retries`` failed attempts, or immediately for
    client errors such as 404 (``details["status"]`` holds the HTTP status).
    """
    dest = Path(dest)
    if dest.exists() and not overwrite:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    if part.exists() and (overwrite or time.time() - part.stat().st_mtime > _RESUME_MAX_AGE_S):
        part.unlink()

    last_error: str = "unknown error"
    last_status: int | None = None
    for attempt in range(max(1, retries)):
        offset = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": user_agent, "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = _https_request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 (https only)
                status = getattr(response, "status", 200)
                if offset and status == 200:
                    offset = 0  # server ignored Range: restart from scratch
                elif offset and not str(response.headers.get("Content-Range", "")).startswith(f"bytes {offset}-"):
                    part.unlink()
                    raise ConnectionError("the server sent another range; starting again")
                mode = "ab" if offset else "wb"
                length = response.headers.get("Content-Length")
                total = int(length) + offset if length and length.isdigit() else None
                done = offset
                with open(part, mode) as fh:
                    while True:
                        chunk = response.read(_CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            progress(done, total)
                if total is not None and done < total:
                    raise ConnectionError(f"connection closed after {done} of {total} bytes")
            part.replace(dest)
            return dest
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            last_error = f"HTTP {exc.code} {exc.reason}"
            if exc.code == 416 and part.exists():
                # the file on the server is no longer the one the partial file came from
                part.unlink()
            if exc.code in _NO_RETRY_STATUS:
                raise _fetch_error(url, f"Download failed: {last_error}", exc.code) from exc
        except (urllib.error.URLError, OSError, ConnectionError, TimeoutError, http.client.HTTPException) as exc:
            last_error = str(getattr(exc, "reason", exc)) or type(exc).__name__
        log.warning("download %s failed (attempt %d/%d): %s", url, attempt + 1, retries, last_error)
        if attempt + 1 < retries:
            _sleep_backoff(attempt)
    raise _fetch_error(url, f"Download failed after {retries} attempts: {last_error}", last_status)


def fetch_bytes(
    url: str,
    timeout: float = 60.0,
    retries: int = 3,
    user_agent: str = USER_AGENT,
    *,
    max_bytes: int = 200 * 1024 * 1024,
) -> bytes:
    """GET ``url`` into memory (at most ``max_bytes``) with retries."""
    return _request(url, None, timeout, retries, user_agent, max_bytes)


def post_form(
    url: str,
    data: dict[str, str],
    timeout: float = 180.0,
    retries: int = 1,
    user_agent: str = USER_AGENT,
    *,
    max_bytes: int = 512 * 1024 * 1024,
) -> bytes:
    """POST ``data`` as ``application/x-www-form-urlencoded`` and return the body."""
    body = urllib.parse.urlencode(data).encode("utf-8")
    return _request(url, body, timeout, retries, user_agent, max_bytes)


def _request(
    url: str,
    body: bytes | None,
    timeout: float,
    retries: int,
    user_agent: str,
    max_bytes: int,
) -> bytes:
    headers = {"User-Agent": user_agent}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
    last_error = "unknown error"
    last_status: int | None = None
    for attempt in range(max(1, retries)):
        request = _https_request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 (https only)
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise _fetch_error(url, f"Response larger than {max_bytes} bytes")
                    chunks.append(chunk)
                return b"".join(chunks)
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            last_error = f"HTTP {exc.code} {exc.reason}"
            if exc.code in _NO_RETRY_STATUS:
                raise _fetch_error(url, f"Request failed: {last_error}", exc.code) from exc
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as exc:
            last_error = str(getattr(exc, "reason", exc)) or type(exc).__name__
        if attempt + 1 < retries:
            _sleep_backoff(attempt)
    raise _fetch_error(url, f"Request failed: {last_error}", last_status)


def overpass_query(
    query: str,
    endpoints: Sequence[str] = OVERPASS_ENDPOINTS,
    timeout: float = 240.0,
    user_agent: str = USER_AGENT,
    *,
    rounds: int = 2,
) -> tuple[dict[str, Any], str]:
    """Run an Overpass QL query, trying each mirror in turn. Returns the payload and the endpoint
    that answered (recorded in the pack, so a fallback to a mirror is visible).

    Overloaded servers answer 429/504 or return a JSON body with a ``remark`` describing a
    runtime error; both move on to the next mirror. After a full round without success the
    function waits 30 s (the policy asks for a pause after 429) and tries again.
    """
    errors: list[str] = []
    for round_index in range(max(1, rounds)):
        for endpoint in endpoints:
            try:
                raw = post_form(endpoint, {"data": query}, timeout=timeout, retries=1, user_agent=user_agent)
            except DataPackError as exc:
                errors.append(f"{endpoint}: {exc.message}")
                log.warning("Overpass mirror %s failed: %s", endpoint, exc.message)
                continue
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                errors.append(f"{endpoint}: response was not JSON")
                continue
            remark = str(payload.get("remark") or "")
            if "runtime error" in remark.lower():
                errors.append(f"{endpoint}: {remark}")
                continue
            if not isinstance(payload.get("elements"), list):
                errors.append(f"{endpoint}: response has no elements")
                continue
            return payload, endpoint
        if round_index + 1 < rounds:
            time.sleep(30)
    raise DataPackError(
        "fetch_failed",
        "All Overpass mirrors failed",
        details={"errors": errors[-10:], "endpoints": list(endpoints)},
    )


@dataclass
class RemoteRaster:
    """Result of :func:`read_remote_mosaic`: a single-band array plus georeferencing."""

    array: np.ndarray  # 2-D, float32, nodata replaced by ``nodata``
    transform: Any  # affine.Affine
    crs: Any  # rasterio.crs.CRS
    nodata: float
    sources: list[str]
    missing: list[str]


def _gdal_env_options(user_agent: str) -> dict[str, Any]:
    return {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
        "GDAL_HTTP_USERAGENT": user_agent,
        "GDAL_HTTP_MAX_RETRY": "4",
        "GDAL_HTTP_RETRY_DELAY": "2",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "VSI_CACHE": "TRUE",
    }


def read_remote_mosaic(
    urls: Sequence[str],
    bounds: tuple[float, float, float, float],
    *,
    res: float | tuple[float, float] | None = None,
    nodata: float = -32767.0,
    user_agent: str = USER_AGENT,
) -> RemoteRaster:
    """Read and merge the parts of remote rasters that cover ``bounds`` (in the rasters' CRS).

    Each HTTP(S) URL is opened through ``/vsicurl/`` so only the overlapping blocks are fetched;
    local paths and other GDAL paths are opened as given.
    URLs that cannot be opened (for example ocean-only Copernicus tiles that do not exist) are
    reported in ``missing``. Raises ``DataPackError("fetch_failed")`` if none can be opened.
    """
    import rasterio
    from rasterio.errors import RasterioIOError
    from rasterio.merge import merge

    with rasterio.Env(**_gdal_env_options(user_agent)):
        datasets = []
        missing: list[str] = []
        opened: list[str] = []
        try:
            for url in urls:
                path = f"/vsicurl/{url}" if url.startswith(("http://", "https://")) else url
                try:
                    datasets.append(rasterio.open(path))
                    opened.append(url)
                except RasterioIOError as exc:
                    log.info("remote raster unavailable %s: %s", url, exc)
                    missing.append(url)
            if not datasets:
                raise DataPackError(
                    "fetch_failed",
                    "None of the remote rasters could be opened",
                    details={"urls": list(urls)},
                )
            crs = datasets[0].crs
            src_nodata = datasets[0].nodata
            merged, transform = merge(
                datasets,
                bounds=bounds,
                res=res,
                nodata=src_nodata if src_nodata is not None else nodata,
                dtype="float32",
                indexes=[1],
            )
        finally:
            for ds in datasets:
                ds.close()
    band = merged[0].astype(np.float32, copy=False)
    if src_nodata is not None:
        if np.isnan(src_nodata):
            band = np.where(np.isnan(band), np.float32(nodata), band)
        else:
            band = np.where(band == np.float32(src_nodata), np.float32(nodata), band)
    band = np.where(np.isfinite(band), band, np.float32(nodata)).astype(np.float32)
    return RemoteRaster(array=band, transform=transform, crs=crs, nodata=float(nodata), sources=opened, missing=missing)


__all__ = [
    "RemoteRaster",
    "download",
    "fetch_bytes",
    "overpass_query",
    "post_form",
    "read_remote_mosaic",
]
