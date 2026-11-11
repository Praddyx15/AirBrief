"""Build data packs.

Real data (needs internet, free and keyless)::

    python -m seed.datapack --region bengaluru [--lite] [--dest DIR] [--name NAME]
    python -m seed.datapack --bbox 85.1,27.5,85.6,27.9 --name kathmandu [--iso3 NPL] [--lite]

``--bbox`` builds a pack for any area of at most 2° × 2° (``--name`` required); ``--iso3`` names
the country whose WorldPop file gives the population layer (none without it).

* aerodromes — OurAirports ``airports.csv`` filtered to the region bbox + 0.3°;
* aerodrome_perimeters — OpenStreetMap ``aeroway=aerodrome`` boundaries (ways and multipolygon
  relations) matched to those aerodromes by the ``icao``/``iata`` tags, else by containing the
  reference point;
* runways    — OurAirports ``runways.csv`` (ends and width) of those aerodromes: the fallback
  outline when OSM has no boundary;
* dem        — Copernicus GLO-30 COG tiles (GLO-90, 3 arc-seconds, with ``--lite``) read windowed
  over HTTPS and merged with ``rasterio.merge``, clipped to the bbox;
* population — WorldPop 2020 1 km of the region's country (people per pixel), window over the bbox;
* obstacles / pois / water / assemblies — OpenStreetMap through the Overpass API with mirror
  fallback (assemblies: stadiums, sports centres with a capacity, theme parks, marketplaces);
* power      — OpenStreetMap power lines, minor lines and cables (ways) plus substations and plants,
  with the largest ``voltage`` tag value in kV;
* zones      — empty (add your own restricted areas), or with ``--zones FILE_OR_URL`` the zones of a
  national ED-269 or ED-318 file that reach into the area, read like an uploaded zone file.

Deterministic offline demo pack (no network)::

    python -m seed.datapack --synthetic [--name demo] [--dest DIR]

The default destination is ``$DATAPACK_DIR`` or ``data/datapacks``; the pack is written to
``<dest>/<name>``. Packs are assembled in a temporary sibling folder and swapped in at the end,
so an interrupted run never leaves a half-written pack behind. Downloads are cached in
``<dest>/.cache``; an interrupted download resumes if it is retried within the hour.

Every manifest carries the SHA-256 of each layer file and the pack's ``content_sha256``; the API
refuses a pack whose files no longer match. After editing a layer by hand (for example adding
restricted areas to ``zones.geojson``), record the new hashes::

    python -m seed.datapack --seal data/datapacks/<name>

The same with a national zone file, which replaces the zones layer (``--zones`` also works on a
new ``--bbox`` or ``--region`` build)::

    python -m seed.datapack --seal data/datapacks/<name> --zones <file-or-url> --zones-credit "<publisher, terms>"

The public Overpass servers are often busy, so a region build can finish with an OpenStreetMap
layer missing (``[pois] FAILED``, exit 1). Fetch only the missing ones later; the pack is sealed
again afterwards::

    python -m seed.datapack --refetch data/datapacks/<name> [--layers pois,water]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import shutil
import stat
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, mapping
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, polygonize

from airbrief_core.data.aerodromes import ACTIVE_TYPES, write_aerodromes_csv
from airbrief_core.data.assemblies import parse_capacity
from airbrief_core.data.datapack import (
    DEFAULT_FILES,
    MANIFEST_NAME,
    DataPackManifest,
    LayerEntry,
    seal_manifest,
    write_manifest,
)
from airbrief_core.data.power import POWER_LINE_KINDS, POWER_SITE_KINDS, parse_voltage_kv
from airbrief_core.data.raster import geographic_cell_area_km2
from airbrief_core.data.sources import (
    OVERPASS_ENDPOINTS,
    REGIONS,
    SOURCES,
    Region,
    check_region_bbox,
    worldpop_source,
    worldpop_url,
)
from airbrief_core.errors import AirBriefError

COORD_DECIMALS = 7
DEM_NODATA = -32767.0
POP_NODATA = -99999.0
FT_TO_M = 0.3048
LEVEL_HEIGHT_M = 3.2
MIN_BUILDING_HEIGHT_M = 15.0
MIN_WATER_AREA_M2 = 2000.0

Say = Callable[[str], None]


def _layer(**fields: Any) -> LayerEntry:
    """A manifest layer entry; extra keys (units, feature_count, tiles, ...) are kept as metadata."""
    return LayerEntry.model_validate(fields)


def _say(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"), flush=True)


def _round_geom(geom: Any) -> Any:
    return shapely.transform(geom, lambda c: np.round(c, COORD_DECIMALS))


def _feature(fid: str, geom: Any, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "id": fid, "geometry": mapping(_round_geom(geom)), "properties": props}


def _write_geojson(path: Path, features: Iterable[dict[str, Any]]) -> int:
    feats = list(features)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": feats}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return len(feats)


def _write_geotiff(path: Path, array: np.ndarray, transform: Any, nodata: float, *, description: str, units: str) -> None:
    import rasterio

    height, width = array.shape
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": nodata,
        "compress": "deflate",
        "predictor": 3,
    }
    if width >= 256 and height >= 256:
        profile.update(tiled=True, blockxsize=256, blockysize=256)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(np.float32), 1)
        dst.set_band_description(1, description)
        dst.update_tags(1, units=units)


def _geodesic_circle(lon: float, lat: float, radius_m: float, n: int = 48) -> Polygon:
    from airbrief_core.geo.geodesic import GEOD

    az = np.linspace(0.0, 360.0, n, endpoint=False)
    lo, la, _ = GEOD.fwd(np.full(n, lon), np.full(n, lat), az, np.full(n, radius_m))
    return orient(Polygon(zip(np.asarray(lo).tolist(), np.asarray(la).tolist(), strict=True)), 1.0)


def _approx_area_m2(geom: Any) -> float:
    """Area in m² from a local equirectangular scale (good enough for size filters)."""
    lat = geom.centroid.y if not geom.is_empty else 0.0
    return float(geom.area) * 111_320.0 * 110_574.0 * math.cos(math.radians(lat))


def _prepare_dir(dest: Path, name: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise SystemExit(f"Invalid pack name {name!r}: use letters, digits, '-', '_' or '.'")
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / name
    work = dest / f".{name}.partial"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    return final, work


def _make_writable_and_retry(func: Callable[..., Any], path: str, _exc: BaseException) -> None:
    # Windows refuses to delete read-only entries: clear the flag and try once more.
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    func(path)


def _remove_tree(path: Path, attempts: int = 5) -> None:
    """Delete a folder, retrying briefly (Windows can hold just-closed files for a moment)."""
    for attempt in range(attempts):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_make_writable_and_retry)
            else:  # pragma: no cover - Python 3.11
                shutil.rmtree(path, onerror=lambda f, p, e: _make_writable_and_retry(f, p, e[1]))
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


def _swap_in(work: Path, final: Path) -> Path:
    """Move a finished pack into place without ever leaving ``final`` half-deleted.

    The old pack is renamed aside, the new one renamed in, and only then is the old one deleted
    (left for the next run if that fails), so a locked file on Windows cannot lose the pack.
    """
    previous = final.parent / f".{final.name}.previous"
    if previous.exists():
        _remove_tree(previous)
    if final.exists():
        if final.is_dir() and ((final / "manifest.json").exists() or not any(final.iterdir())):
            final.replace(previous)
        else:
            raise SystemExit(f"Refusing to overwrite {final}: it exists and is not a data pack")
    try:
        work.replace(final)
    except OSError:
        if previous.exists() and not final.exists():
            previous.replace(final)  # put the old pack back
        raise
    if previous.exists():
        try:
            _remove_tree(previous)
        except OSError as exc:
            print(f"note: could not delete the previous pack at {previous} ({exc}); the next run removes it", file=sys.stderr)
    return final


_NUM = r"(\d+(?:\.\d+)?)"
_RE_METRES = re.compile(rf"^~?\s*{_NUM}\s*(m|meter|meters|metre|metres)?$")
_RE_FEET = re.compile(rf"^~?\s*{_NUM}\s*(ft|feet|foot|')$")
_RE_FEET_INCHES = re.compile(rf"^~?\s*(\d+)\s*'\s*{_NUM}\s*(\"|'')?$")


def parse_height(value: Any) -> float | None:
    """Parse an OSM ``height``-style value into metres.

    Accepts ``"45"``, ``"45 m"``, ``"45.5m"``, ``"150 ft"``, ``"150'"``, ``"12'6\\""`` and lists
    separated by ``;`` (largest wins). Returns None for unparseable or implausible (≤0 or ≥1000 m)
    values.
    """
    if value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
        return number if math.isfinite(number) and 0.0 < number < 1000.0 else None
    text = str(value).strip().lower()
    if not text:
        return None
    best: float | None = None
    for part in re.split(r"[;|]", text):
        s = part.strip().replace(",", ".")
        h: float | None = None
        if m := _RE_METRES.match(s):
            h = float(m.group(1))
        elif m := _RE_FEET.match(s):
            h = float(m.group(1)) * FT_TO_M
        elif m := _RE_FEET_INCHES.match(s):
            h = float(m.group(1)) * FT_TO_M + float(m.group(2)) * 0.0254
        if h is not None and 0.0 < h < 1000.0 and (best is None or h > best):
            best = h
    return round(best, 2) if best is not None else None


def parse_levels(value: Any) -> float | None:
    """Parse ``building:levels`` (first number; plausible range 1–199)."""
    if value is None:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(value))
    if not m:
        return None
    levels = float(m.group(0).replace(",", "."))
    return levels if 0.0 < levels < 200.0 else None


SYNTHETIC_BBOX: tuple[float, float, float, float] = (77.30, 12.70, 77.95, 13.30)
SYNTHETIC_CREATED_UTC = "2026-01-01T00:00:00Z"
SYNTHETIC_SEED = 20261008
SYNTHETIC_DEM_RES = 0.0005
SYNTHETIC_POP_RES = 0.005
CITY_CENTRE = (77.5946, 12.9716)
SURVEY_AREA = (77.48, 12.90, 77.50, 12.91)

SYNTHETIC_AERODROMES: tuple[dict[str, Any], ...] = (
    {"ident": "VOBL", "type": "large_airport", "name": "Kempegowda International Airport", "latitude_deg": 13.1986,
     "longitude_deg": 77.7066, "elevation_ft": 3002, "iso_country": "IN", "municipality": "Bengaluru",
     "iata_code": "BLR", "scheduled_service": "yes"},
    {"ident": "VOBG", "type": "medium_airport", "name": "HAL Airport", "latitude_deg": 12.9499,
     "longitude_deg": 77.6682, "elevation_ft": 2912, "iso_country": "IN", "municipality": "Bengaluru",
     "scheduled_service": "no"},
    {"ident": "VOYK", "type": "medium_airport", "name": "Yelahanka Air Force Station", "latitude_deg": 13.1355,
     "longitude_deg": 77.6061, "elevation_ft": 3045, "iso_country": "IN", "municipality": "Bengaluru",
     "scheduled_service": "no"},
    {"ident": "VOJK", "type": "small_airport", "name": "Jakkur Aerodrome", "latitude_deg": 13.0781,
     "longitude_deg": 77.5975, "elevation_ft": 2990, "iso_country": "IN", "municipality": "Bengaluru",
     "scheduled_service": "no"},
)

# (lon, lat, peak people/km², sigma km)
_POP_HUBS: tuple[tuple[float, float, float, float], ...] = (
    (77.5946, 12.9716, 17_000.0, 5.0),  # city centre (+ floor + nearby hubs ≈ 20,000/km²)
    (77.5830, 12.9300, 9_000.0, 2.5),  # Jayanagar
    (77.5550, 12.9900, 8_500.0, 2.5),  # Rajajinagar
    (77.7500, 12.9698, 9_000.0, 2.5),  # Whitefield
    (77.7010, 12.9560, 7_500.0, 2.0),  # Marathahalli
    (77.6700, 12.8450, 8_000.0, 2.5),  # Electronic City
    (77.5920, 13.0358, 7_000.0, 2.0),  # Hebbal
    (77.5960, 13.1007, 6_500.0, 2.5),  # Yelahanka
    (77.4840, 12.9140, 5_000.0, 2.0),  # Kengeri
)
_POP_FLOOR = 250.0


def _gauss_deg(lons: np.ndarray, lats: np.ndarray, lon0: float, lat0: float, sigma_deg: float) -> np.ndarray:
    return np.exp(-((lons - lon0) ** 2 + (lats - lat0) ** 2) / (2.0 * sigma_deg**2))


def synthetic_elevation(lons: Any, lats: Any) -> np.ndarray:
    """Analytic terrain (m AMSL) of the synthetic pack: smooth hills, roughly 875–960 m."""
    lo = np.asarray(lons, dtype=np.float64)
    la = np.asarray(lats, dtype=np.float64)
    x = (lo - SYNTHETIC_BBOX[0]) / (SYNTHETIC_BBOX[2] - SYNTHETIC_BBOX[0])
    y = (la - SYNTHETIC_BBOX[1]) / (SYNTHETIC_BBOX[3] - SYNTHETIC_BBOX[1])
    z = 915.0 + 14.0 * np.sin(2.0 * math.pi * 1.7 * x + 0.4) * np.cos(2.0 * math.pi * 1.3 * y - 0.3)
    z += 24.0 * _gauss_deg(lo, la, 77.42, 12.79, 0.035)  # south-west ridge
    z += 16.0 * _gauss_deg(lo, la, 77.83, 13.21, 0.050)  # north-east upland
    z += 10.0 * _gauss_deg(lo, la, 77.58, 13.10, 0.030)  # northern knoll
    z -= 18.0 * _gauss_deg(lo, la, 77.66, 12.93, 0.050)  # south-east valley (lakes)
    z += 8.0 * (y - 0.5)
    return z


def synthetic_density(lons: Any, lats: Any) -> np.ndarray:
    """Analytic population density (people/km²) of the synthetic pack (centre peak ≈ 20,000)."""
    lo = np.asarray(lons, dtype=np.float64)
    la = np.asarray(lats, dtype=np.float64)
    d = np.full(np.broadcast(lo, la).shape, _POP_FLOOR)
    for lon0, lat0, peak, sigma_km in _POP_HUBS:
        dx = (lo - lon0) * 111.32 * math.cos(math.radians(lat0))
        dy = (la - lat0) * 110.57
        d = d + peak * np.exp(-(dx * dx + dy * dy) / (2.0 * sigma_km * sigma_km))
    return d


def _synthetic_dem(path: Path) -> dict[str, Any]:
    from rasterio.transform import from_origin

    min_lon, min_lat, max_lon, max_lat = SYNTHETIC_BBOX
    res = SYNTHETIC_DEM_RES
    width = int(round((max_lon - min_lon) / res))
    height = int(round((max_lat - min_lat) / res))
    lons = min_lon + (np.arange(width) + 0.5) * res
    lats = max_lat - (np.arange(height) + 0.5) * res
    grid_lon, grid_lat = np.meshgrid(lons, lats)
    z = synthetic_elevation(grid_lon, grid_lat).astype(np.float32)
    _write_geotiff(path, z, from_origin(min_lon, max_lat, res, res), DEM_NODATA, description="elevation", units="m")
    return {"units": "m", "vertical_datum": "synthetic (≈ AMSL)", "resolution_deg": res,
            "min_m": round(float(np.min(z)), 1), "max_m": round(float(np.max(z)), 1)}


def _synthetic_population(path: Path) -> dict[str, Any]:
    from rasterio.transform import from_origin

    min_lon, min_lat, max_lon, max_lat = SYNTHETIC_BBOX
    res = SYNTHETIC_POP_RES
    width = int(round((max_lon - min_lon) / res))
    height = int(round((max_lat - min_lat) / res))
    rows = np.arange(height, dtype=np.float64)
    lat_top = max_lat - rows * res
    lat_bot = max_lat - (rows + 1.0) * res
    area_km2 = geographic_cell_area_km2(lat_top, lat_bot, res)[:, None]
    lons = min_lon + (np.arange(width) + 0.5) * res
    lats = (lat_top + lat_bot) / 2.0
    grid_lon, grid_lat = np.meshgrid(lons, lats)
    density = synthetic_density(grid_lon, grid_lat)
    people = (density * area_km2).astype(np.float32)
    _write_geotiff(path, people, from_origin(min_lon, max_lat, res, res), POP_NODATA, description="population", units="people/pixel")
    return {"units": "people/pixel", "resolution_deg": res, "total_people": round(float(people.sum()), 0),
            "peak_density_per_km2": round(float(density.max()), 1)}


def _inside_bbox(lon: float, lat: float, bbox: Sequence[float], margin: float = 0.0) -> bool:
    return bbox[0] + margin <= lon <= bbox[2] - margin and bbox[1] + margin <= lat <= bbox[3] - margin


def _synthetic_obstacles(rng: np.random.Generator) -> list[dict[str, Any]]:
    from shapely.geometry import Point

    fixed = [
        (77.4885, 12.9046, "mast", 65.0, "Synthetic mast near survey area"),
        (77.4962, 12.9078, "building", 42.0, "Synthetic office block near survey area"),
        (77.4931, 12.9019, "communication_tower", 48.0, "Synthetic telecom tower near survey area"),
    ]
    kinds = np.array(["building", "communication_tower", "mast", "power_tower", "chimney"])
    probs = np.array([0.45, 0.20, 0.15, 0.12, 0.08])
    height_ranges = {
        "building": (30.0, 180.0),
        "communication_tower": (40.0, 120.0),
        "mast": (30.0, 90.0),
        "power_tower": (30.0, 60.0),
        "chimney": (30.0, 80.0),
    }
    feats: list[dict[str, Any]] = []
    for i, (lon, lat, kind, height, name) in enumerate(fixed, start=1):
        feats.append(_feature(f"obs-{i:04d}", Point(lon, lat), {
            "kind": kind, "height_m": height, "name": name, "source": "synthetic"}))
    n_random = 150 - len(fixed)
    count = 0
    while count < n_random:
        if count < 90:
            lon = float(rng.normal(CITY_CENTRE[0], 0.07))
            lat = float(rng.normal(CITY_CENTRE[1], 0.07))
        else:
            lon = float(rng.uniform(SYNTHETIC_BBOX[0], SYNTHETIC_BBOX[2]))
            lat = float(rng.uniform(SYNTHETIC_BBOX[1], SYNTHETIC_BBOX[3]))
        kind = str(rng.choice(kinds, p=probs))
        lo_h, hi_h = height_ranges[kind]
        height = round(float(rng.uniform(lo_h, hi_h)), 1)
        if not _inside_bbox(lon, lat, SYNTHETIC_BBOX, 0.005):
            continue
        count += 1
        idx = len(feats) + 1
        feats.append(_feature(f"obs-{idx:04d}", Point(lon, lat), {
            "kind": kind, "height_m": height,
            "name": f"Synthetic {kind.replace('_', ' ')} {idx}", "source": "synthetic"}))
    return feats


def _synthetic_pois(rng: np.random.Generator) -> list[dict[str, Any]]:
    from shapely.geometry import Point

    feats: list[dict[str, Any]] = []

    def add(lon: float, lat: float, kind: str, name: str, line: str | None) -> None:
        props: dict[str, Any] = {"kind": kind, "name": name, "source": "synthetic"}
        if line:
            props["line"] = line
        feats.append(_feature(f"poi-{len(feats) + 1:03d}", Point(lon, lat), props))

    for i, lon in enumerate(np.linspace(77.50, 77.75, 11), start=1):
        add(float(lon), 12.9760 + float(rng.normal(0.0, 0.0015)), "metro_station", f"Synthetic Purple {i:02d}", "Purple line (synthetic)")
    for i, lat in enumerate(np.linspace(12.86, 13.07, 11), start=1):
        add(77.5750 + float(rng.normal(0.0, 0.0015)), float(lat), "metro_station", f"Synthetic Green {i:02d}", "Green line (synthetic)")
    add(77.5700, 12.9780, "rail_station", "Synthetic City Railway Station", None)
    add(77.6000, 12.9930, "rail_station", "Synthetic Cantonment Station", None)
    add(77.5500, 13.0230, "rail_station", "Synthetic Yesvantpur Junction", None)
    return feats


def _synthetic_water(rng: np.random.Generator) -> list[dict[str, Any]]:
    from airbrief_core.geo.geodesic import distance_m

    lakes: list[tuple[float, float, float, str]] = [
        (77.6650, 12.9350, 1100.0, "Synthetic Bellandur lake"),
        (77.5900, 13.0450, 600.0, "Synthetic Hebbal lake"),
        (77.6200, 12.9830, 450.0, "Synthetic Ulsoor lake"),
        (77.6150, 12.9050, 550.0, "Synthetic Madiwala lake"),
    ]
    survey_c = ((SURVEY_AREA[0] + SURVEY_AREA[2]) / 2.0, (SURVEY_AREA[1] + SURVEY_AREA[3]) / 2.0)
    keep_out = [survey_c, CITY_CENTRE] + [(a["longitude_deg"], a["latitude_deg"]) for a in SYNTHETIC_AERODROMES]
    attempts = 0
    while len(lakes) < 12 and attempts < 10_000:
        attempts += 1
        lon = float(rng.uniform(SYNTHETIC_BBOX[0] + 0.02, SYNTHETIC_BBOX[2] - 0.02))
        lat = float(rng.uniform(SYNTHETIC_BBOX[1] + 0.02, SYNTHETIC_BBOX[3] - 0.02))
        radius = float(rng.uniform(250.0, 900.0))
        if any(float(distance_m(lon, lat, kx, ky)) < 3000.0 + radius for kx, ky in keep_out):
            continue
        if any(float(distance_m(lon, lat, lx, ly)) < radius + lr + 500.0 for lx, ly, lr, _ in lakes):
            continue
        lakes.append((lon, lat, round(radius, 0), f"Synthetic lake {len(lakes) + 1}"))
    feats = []
    for i, (lon, lat, radius, name) in enumerate(lakes, start=1):
        feats.append(_feature(f"water-{i:02d}", _geodesic_circle(lon, lat, radius), {
            "kind": "lake", "name": name, "radius_m": radius, "source": "synthetic"}))
    return feats


def _synthetic_zones() -> list[dict[str, Any]]:
    from shapely.geometry import box

    zone = orient(box(77.5840, 12.9735, 77.5975, 12.9855), 1.0)
    return [_feature("zone-01", zone, {
        "name": "Demo restricted zone (synthetic)",
        "zone": "red",
        "ceiling_agl_m": None,
        "note": "Synthetic demo zone for testing — not a real airspace restriction.",
        "source": "synthetic",
    })]


# (lon, lat, radius m, kind, name, capacity): fixed, so adding them leaves the RNG stream untouched.
# The stadium is about 0.8 km east of the bengaluru_survey sample area (inside the 1 km Step #8
# assembly check); the market without a capacity tag sits south-west of the city centre.
SYNTHETIC_ASSEMBLIES: tuple[tuple[float, float, float, str, str, int | None], ...] = (
    (77.5055, 12.9010, 120.0, "stadium", "Synthetic Kengeri stadium", 45_000),
    (77.5775, 12.9645, 80.0, "marketplace", "Synthetic city market", None),
)


def _synthetic_assemblies() -> list[dict[str, Any]]:
    feats: list[dict[str, Any]] = []
    for i, (lon, lat, radius, kind, name, capacity) in enumerate(SYNTHETIC_ASSEMBLIES, start=1):
        feats.append(_feature(f"assembly-{i:02d}", _geodesic_circle(lon, lat, radius), {
            "kind": kind, "name": name, "capacity": capacity, "source": "synthetic"}))
    return feats


SYNTHETIC_SUBSTATION = (77.4830, 12.8985, 0.0004, 0.0003)
"""Synthetic 220/33 kV substation: centre lon, lat and half-sizes in degrees (≈ 87 × 66 m)."""

SYNTHETIC_POWER_LINES: tuple[tuple[str, str, str, tuple[tuple[float, float], ...]], ...] = (
    # A 220 kV line from the substation that crosses the Bengaluru survey sample area.
    ("line", "220000", "Synthetic 220 kV line (Kengeri–Peenya)",
     ((77.4834, 12.8988), (77.4920, 12.9050), (77.5200, 12.9160), (77.5600, 12.9300))),
    # A 33 kV feeder about 40 m east of the survey area's east edge (lon 77.497).
    ("line", "33000", "Synthetic 33 kV feeder",
     ((77.4834, 12.8982), (77.49737, 12.8962), (77.49737, 12.9035))),
)


def _synthetic_power() -> list[dict[str, Any]]:
    from shapely.geometry import box

    feats: list[dict[str, Any]] = []
    for i, (kind, voltage, name, coords) in enumerate(SYNTHETIC_POWER_LINES, start=1):
        feats.append(_feature(f"power-{i:02d}", LineString(coords), {
            "kind": kind, "name": name, "voltage": voltage, "voltage_kv": parse_voltage_kv(voltage),
            "operator": "Synthetic grid company", "source": "synthetic"}))
    lon, lat, half_lon, half_lat = SYNTHETIC_SUBSTATION
    station = orient(box(lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat), 1.0)
    feats.append(_feature(f"power-{len(feats) + 1:02d}", station, {
        "kind": "substation", "name": "Synthetic 220/33 kV substation", "voltage": "220000;33000",
        "voltage_kv": parse_voltage_kv("220000;33000"), "operator": "Synthetic grid company",
        "source": "synthetic"}))
    return feats


# Synthetic aerodrome outlines, in metres along and across the long axis from the reference point:
# (ident, half length, half width or None for no boundary, heading of the axis in degrees,
#  runways as (offset across, half length, width)). VOBL is long and narrow like a two-runway
# airport, so its bands hug the runways; VOJK has a runway but no boundary (the hull fallback).
SYNTHETIC_AIRFIELDS: tuple[tuple[str, float, float | None, float, tuple[tuple[float, float, float], ...]], ...] = (
    ("VOBL", 2400.0, 1300.0, 0.0, ((-600.0, 2000.0, 45.0), (600.0, 2000.0, 60.0))),
    ("VOBG", 1800.0, 500.0, 90.0, ((0.0, 1650.0, 60.0),)),
    ("VOYK", 1600.0, 600.0, 90.0, ((0.0, 1400.0, 45.0),)),
    ("VOJK", 450.0, None, 60.0, ((0.0, 400.0, 18.0),)),
)
_CHAMFER_M = 300.0


def _airfield_frame(ident: str) -> Any:
    from airbrief_core.geo.frame import LocalFrame

    row = next(a for a in SYNTHETIC_AERODROMES if a["ident"] == ident)
    return LocalFrame(row["longitude_deg"], row["latitude_deg"])


def _axis_points(heading: float, pts: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """(along, across) metres → (east, north) for an axis ``heading`` degrees from north."""
    h = math.radians(heading)
    return [(a * math.sin(h) + c * math.cos(h), a * math.cos(h) - c * math.sin(h)) for a, c in pts]


def _synthetic_perimeters() -> list[dict[str, Any]]:
    feats: list[dict[str, Any]] = []
    for ident, half_len, half_wid, heading, _runways in SYNTHETIC_AIRFIELDS:
        if half_wid is None:
            continue
        c = _CHAMFER_M
        corners = [(half_len, half_wid - c), (half_len - c, half_wid), (-half_len + c, half_wid), (-half_len, half_wid - c),
                   (-half_len, -half_wid + c), (-half_len + c, -half_wid), (half_len - c, -half_wid), (half_len, -half_wid + c)]
        outline = _airfield_frame(ident).to_wgs84(Polygon(_axis_points(heading, corners)))
        feats.append(_feature(f"perimeter-{ident}", orient(outline, 1.0), {
            "ident": ident, "match": "ident", "kind": "aerodrome", "source": "synthetic"}))
    return feats


def _synthetic_runways() -> list[dict[str, Any]]:
    feats: list[dict[str, Any]] = []
    for ident, _half_len, _half_wid, heading, runways in SYNTHETIC_AIRFIELDS:
        frame = _airfield_frame(ident)
        for i, (across, half, width) in enumerate(runways, start=1):
            line = frame.to_wgs84(LineString(_axis_points(heading, [(-half, across), (half, across)])))
            feats.append(_feature(f"runway-{ident}-{i}", line, {
                "airport_ident": ident, "length_m": round(2 * half, 1), "width_m": width, "surface": "ASP",
                "source": "synthetic"}))
    return feats


def build_synthetic_pack(dest: Path, name: str = "demo") -> Path:
    """Write the deterministic synthetic pack to ``dest/name`` and return that folder.

    Identical inputs give identical layer files (fixed RNG seed and fixed ``created_utc``).
    """
    dest = Path(dest)
    final, work = _prepare_dir(dest, name)
    try:
        rng = np.random.default_rng(SYNTHETIC_SEED)
        synth = SOURCES["synthetic"]
        layers: dict[str, LayerEntry] = {}

        def entry(key: str, **extra: Any) -> LayerEntry:
            return LayerEntry(file=DEFAULT_FILES[key], source="synthetic", licence=synth.licence, **extra)

        layers["dem"] = entry("dem", **_synthetic_dem(work / DEFAULT_FILES["dem"]))
        layers["population"] = entry("population", **_synthetic_population(work / DEFAULT_FILES["population"]))
        n = write_aerodromes_csv(work / DEFAULT_FILES["aerodromes"], SYNTHETIC_AERODROMES)
        layers["aerodromes"] = entry("aerodromes", feature_count=n, note="Approximate public reference points")
        n = _write_geojson(work / DEFAULT_FILES["obstacles"], _synthetic_obstacles(rng))
        layers["obstacles"] = entry("obstacles", feature_count=n, height_reference="above ground level")
        n = _write_geojson(work / DEFAULT_FILES["pois"], _synthetic_pois(rng))
        layers["pois"] = entry("pois", feature_count=n)
        n = _write_geojson(work / DEFAULT_FILES["water"], _synthetic_water(rng))
        layers["water"] = entry("water", feature_count=n)
        n = _write_geojson(work / DEFAULT_FILES["zones"], _synthetic_zones())
        layers["zones"] = entry("zones", feature_count=n)
        n = _write_geojson(work / DEFAULT_FILES["assemblies"], _synthetic_assemblies())
        layers["assemblies"] = entry("assemblies", feature_count=n)
        n = _write_geojson(work / DEFAULT_FILES["power"], _synthetic_power())
        layers["power"] = entry("power", feature_count=n, voltage_units="kV")
        n = _write_geojson(work / DEFAULT_FILES["aerodrome_perimeters"], _synthetic_perimeters())
        layers["aerodrome_perimeters"] = entry("aerodrome_perimeters", feature_count=n)
        n = _write_geojson(work / DEFAULT_FILES["runways"], _synthetic_runways())
        layers["runways"] = entry("runways", feature_count=n, units="m")

        manifest = DataPackManifest(
            name=name,
            version="1",
            created_utc=SYNTHETIC_CREATED_UTC,
            synthetic=True,
            bbox=list(SYNTHETIC_BBOX),
            layers=layers,
            attribution=[synth.attribution],
            description="Deterministic synthetic pack around Bengaluru for demos and tests. Not real data.",
            region="bengaluru",
        )
        write_manifest(work, seal_manifest(work, manifest))
        return _swap_in(work, final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _overpass_bbox(bbox: Sequence[float]) -> str:
    min_lon, min_lat, max_lon, max_lat = bbox
    return f"{min_lat:.5f},{min_lon:.5f},{max_lat:.5f},{max_lon:.5f}"


def _overpass_header(bbox: Sequence[float], timeout_s: int = 180) -> str:
    return f"[out:json][timeout:{timeout_s}][maxsize:1073741824][bbox:{_overpass_bbox(bbox)}];"


def obstacles_query(bbox: Sequence[float]) -> str:
    """Overpass QL for towers, masts, chimneys, communication/power towers and tall buildings."""
    levels = '"^0*[1-9][0-9]+([.,][0-9]+)?$"'
    return (
        _overpass_header(bbox)
        + "("
        + 'nwr["man_made"~"^(tower|mast|chimney|communications_tower|water_tower|crane|flare)$"];'
        + 'nwr["tower:type"="communication"];'
        + 'node["power"~"^(tower|portal)$"];'
        + 'way["building"]["height"];'
        + 'relation["building"]["height"];'
        + f'way["building"]["building:levels"~{levels}];'
        + f'relation["building"]["building:levels"~{levels}];'
        + ");out center tags;"
    )


def pois_query(bbox: Sequence[float]) -> str:
    """Overpass QL for rail/metro stations and bus stations."""
    return (
        _overpass_header(bbox)
        + "("
        + 'nwr["railway"~"^(station|halt)$"];'
        + 'nwr["public_transport"="station"];'
        + 'nwr["amenity"="bus_station"];'
        + ");out center tags;"
    )


def water_query(bbox: Sequence[float]) -> str:
    """Overpass QL for water bodies (ways and multipolygon relations) with full geometry."""
    return (
        _overpass_header(bbox)
        + "("
        + 'way["natural"="water"];'
        + 'relation["natural"="water"];'
        + 'way["landuse"="reservoir"];'
        + 'relation["landuse"="reservoir"];'
        + 'way["water"~"^(lake|reservoir|pond|basin|lagoon)$"];'
        + ");out geom;"
    )


def assemblies_query(bbox: Sequence[float]) -> str:
    """Overpass QL for outdoor assembly venues (SORA Step #8): stadiums, sports centres with a
    capacity, theme parks and marketplaces, with full geometry."""
    return (
        _overpass_header(bbox)
        + "("
        + 'nwr["leisure"="stadium"];'
        + 'nwr["leisure"="sports_centre"]["capacity"];'
        + 'nwr["tourism"="theme_park"];'
        + 'nwr["amenity"="marketplace"];'
        + ");out geom;"
    )


def power_query(bbox: Sequence[float]) -> str:
    """Overpass QL for power lines, minor lines and cables (ways) and substations and plants
    (nodes, ways, relations), with full geometry."""
    return (
        _overpass_header(bbox)
        + "("
        + 'way["power"~"^(line|minor_line|cable)$"];'
        + 'nwr["power"~"^(substation|plant)$"];'
        + ");out geom;"
    )


_DEFAULT_HEIGHTS = {
    "power_tower": 35.0,
    "communication_tower": 45.0,
    "mast": 30.0,
    "chimney": 40.0,
    "water_tower": 25.0,
    "tower": 25.0,
    "crane": 40.0,
    "flare": 30.0,
}


def _element_point(el: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in el and "lon" in el:
        return float(el["lon"]), float(el["lat"])
    centre = el.get("center")
    if isinstance(centre, dict) and "lat" in centre and "lon" in centre:
        return float(centre["lon"]), float(centre["lat"])
    return None


def _obstacle_kind(tags: dict[str, str]) -> str:
    man_made = tags.get("man_made", "")
    if tags.get("power") in ("tower", "portal"):
        return "power_tower"
    if man_made == "communications_tower" or tags.get("tower:type") == "communication":
        return "communication_tower"
    if man_made in ("mast", "chimney", "water_tower", "crane", "flare"):
        return man_made
    if man_made == "tower":
        return "tower"
    if "building" in tags:
        return "building"
    return "tower"


def obstacles_from_overpass(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Overpass elements to obstacle features with a parsed ``height_m``.

    Height: ``height`` tag (m or ft) → ``building:levels`` × 3.2 m → a per-kind default.
    Buildings without a usable height, or lower than 15 m, are dropped.
    """
    from shapely.geometry import Point

    feats: list[dict[str, Any]] = []
    seen: set[str] = set()
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        pt = _element_point(el)
        if pt is None:
            continue
        fid = f"osm-{el.get('type', 'x')[0]}{el.get('id')}"
        if fid in seen:
            continue
        kind = _obstacle_kind(tags)
        height = parse_height(tags.get("height")) or parse_height(tags.get("building:height"))
        height_source = "tag"
        if height is None:
            levels = parse_levels(tags.get("building:levels"))
            if levels is not None:
                height = round(levels * LEVEL_HEIGHT_M, 1)
                height_source = "levels"
        if height is None:
            if kind == "building":
                continue
            height = _DEFAULT_HEIGHTS.get(kind, 25.0)
            height_source = "default"
        if kind == "building" and height < MIN_BUILDING_HEIGHT_M:
            continue
        seen.add(fid)
        props: dict[str, Any] = {
            "kind": kind,
            "height_m": height,
            "height_source": height_source,
            "name": tags.get("name") or tags.get("name:en"),
            "osm_type": el.get("type"),
            "osm_id": el.get("id"),
            "source": "osm",
        }
        feats.append(_feature(fid, Point(*pt), props))
    return feats


def _poi_kind(tags: dict[str, str]) -> str:
    if tags.get("amenity") == "bus_station" or (tags.get("bus") == "yes" and "railway" not in tags):
        return "bus_station"
    if (
        tags.get("station") in ("subway", "light_rail", "monorail")
        or tags.get("subway") == "yes"
        or tags.get("light_rail") == "yes"
    ):
        return "metro_station"
    if tags.get("railway") in ("station", "halt") or tags.get("train") == "yes":
        return "rail_station"
    return "station"


def pois_from_overpass(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Overpass elements to station features, de-duplicating by name, kind and ~300 m."""
    from shapely.geometry import Point

    feats: list[dict[str, Any]] = []
    kept: list[tuple[str, str, float, float]] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        pt = _element_point(el)
        if pt is None:
            continue
        kind = _poi_kind(tags)
        name = tags.get("name:en") or tags.get("name") or ""
        dup = False
        for kname, kkind, klon, klat in kept:
            if kkind == kind and kname == name and abs(klon - pt[0]) < 0.003 and abs(klat - pt[1]) < 0.003:
                dup = True
                break
        if dup:
            continue
        kept.append((name, kind, pt[0], pt[1]))
        props = {
            "kind": kind,
            "name": name or None,
            "osm_type": el.get("type"),
            "osm_id": el.get("id"),
            "source": "osm",
        }
        feats.append(_feature(f"osm-{el.get('type', 'x')[0]}{el.get('id')}", Point(*pt), props))
    return feats


def _line_from_geometry(points: Sequence[dict[str, Any]]) -> LineString | None:
    coords = [(float(p["lon"]), float(p["lat"])) for p in points if p and "lon" in p and "lat" in p]
    if len(coords) < 2:
        return None
    return LineString(coords)


def _assemble_rings(lines: list[LineString]) -> Any:
    if not lines:
        return None
    merged = linemerge(lines)
    polys = list(polygonize(list(shapely.get_parts(merged))))
    if not polys:
        return None
    return shapely.union_all(polys)


def water_from_overpass(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert ``out geom`` Overpass output to water polygons.

    Closed ways become polygons; multipolygon relations are assembled from their outer and inner
    member ways with ``linemerge`` + ``polygonize`` (inners subtracted). Bodies under 2000 m² are
    dropped.
    """
    feats: list[dict[str, Any]] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        geom: Any = None
        if el.get("type") == "way":
            line = _line_from_geometry(el.get("geometry") or [])
            if line is None or len(line.coords) < 4 or line.coords[0] != line.coords[-1]:
                continue
            geom = Polygon(line.coords)
        elif el.get("type") == "relation":
            outers: list[LineString] = []
            inners: list[LineString] = []
            for member in el.get("members") or []:
                if member.get("type") != "way":
                    continue
                line = _line_from_geometry(member.get("geometry") or [])
                if line is None:
                    continue
                (inners if member.get("role") == "inner" else outers).append(line)
            outer = _assemble_rings(outers)
            if outer is None:
                continue
            inner = _assemble_rings(inners)
            geom = outer.difference(inner) if inner is not None else outer
        else:
            continue
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            geom = shapely.make_valid(geom)
            parts = [p for p in shapely.get_parts(geom) if p.geom_type in ("Polygon", "MultiPolygon")]
            if not parts:
                continue
            geom = shapely.union_all(parts)
        if _approx_area_m2(geom) < MIN_WATER_AREA_M2:
            continue
        geom = orient(geom, 1.0) if geom.geom_type == "Polygon" else shapely.MultiPolygon(
            [orient(p, 1.0) for p in geom.geoms]
        )
        kind = tags.get("water") or ("reservoir" if tags.get("landuse") == "reservoir" else "water")
        props = {
            "kind": kind,
            "name": tags.get("name:en") or tags.get("name"),
            "osm_type": el.get("type"),
            "osm_id": el.get("id"),
            "source": "osm",
        }
        feats.append(_feature(f"osm-{el['type'][0]}{el.get('id')}", geom, props))
    return feats


def _assembly_kind(tags: dict[str, str]) -> str | None:
    if tags.get("leisure") == "stadium":
        return "stadium"
    if tags.get("leisure") == "sports_centre":
        return "sports_centre"
    if tags.get("tourism") == "theme_park":
        return "theme_park"
    if tags.get("amenity") == "marketplace":
        return "marketplace"
    return None


def _assembly_geometry(el: dict[str, Any]) -> Any:
    """Point (node), polygon (closed way or multipolygon relation) or a representative point."""
    from shapely.geometry import MultiPoint, Point

    kind = el.get("type")
    if kind == "node":
        pt = _element_point(el)
        return Point(*pt) if pt is not None else None
    if kind == "way":
        line = _line_from_geometry(el.get("geometry") or [])
        if line is None:
            return None
        if len(line.coords) >= 4 and line.coords[0] == line.coords[-1]:
            return Polygon(line.coords)
        return line.centroid
    if kind == "relation":
        outers: list[LineString] = []
        inners: list[LineString] = []
        points: list[tuple[float, float]] = []
        for member in el.get("members") or []:
            line = _line_from_geometry(member.get("geometry") or [])
            if line is not None:
                (inners if member.get("role") == "inner" else outers).append(line)
            elif "lat" in member and "lon" in member:
                points.append((float(member["lon"]), float(member["lat"])))
        outer = _assemble_rings(outers)
        if outer is not None:
            inner = _assemble_rings(inners)
            return outer.difference(inner) if inner is not None else outer
        if outers:
            return shapely.union_all(outers).centroid
        if points:
            return MultiPoint(points).centroid
    return None


def assemblies_from_overpass(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert ``out geom`` Overpass output to assembly venues with ``kind``, ``name`` and ``capacity``.

    Nodes become points, closed ways and multipolygon relations polygons (inners subtracted),
    anything else its centroid. ``capacity`` is the parsed OSM ``capacity`` tag (people) or null.
    """
    feats: list[dict[str, Any]] = []
    seen: set[str] = set()
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        kind = _assembly_kind(tags)
        if kind is None:
            continue
        fid = f"osm-{str(el.get('type', 'x'))[0]}{el.get('id')}"
        if fid in seen:
            continue
        geom = _assembly_geometry(el)
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            geom = shapely.make_valid(geom)
        if geom.geom_type == "Polygon":
            geom = orient(geom, 1.0)
        seen.add(fid)
        props = {
            "kind": kind,
            "name": tags.get("name:en") or tags.get("name"),
            "capacity": parse_capacity(tags.get("capacity")),
            "osm_type": el.get("type"),
            "osm_id": el.get("id"),
            "source": "osm",
        }
        feats.append(_feature(fid, geom, props))
    return feats


def _power_site_geometry(el: dict[str, Any]) -> Any:
    """Point (node), polygon (closed way or multipolygon relation) or centre point of a site."""
    from shapely.geometry import Point

    if el.get("type") == "node" and "lon" in el and "lat" in el:
        return Point(float(el["lon"]), float(el["lat"]))
    if el.get("type") == "way":
        line = _line_from_geometry(el.get("geometry") or [])
        if line is not None and len(line.coords) >= 4 and line.coords[0] == line.coords[-1]:
            return Polygon(line.coords)
        if line is not None:
            return line.centroid
    if el.get("type") == "relation":
        outers: list[LineString] = []
        inners: list[LineString] = []
        for member in el.get("members") or []:
            if member.get("type") != "way":
                continue
            line = _line_from_geometry(member.get("geometry") or [])
            if line is not None:
                (inners if member.get("role") == "inner" else outers).append(line)
        outer = _assemble_rings(outers)
        if outer is not None:
            inner = _assemble_rings(inners)
            return outer.difference(inner) if inner is not None else outer
    bounds = el.get("bounds")
    if isinstance(bounds, dict) and {"minlon", "minlat", "maxlon", "maxlat"} <= bounds.keys():
        return Point((float(bounds["minlon"]) + float(bounds["maxlon"])) / 2.0,
                     (float(bounds["minlat"]) + float(bounds["maxlat"])) / 2.0)
    return None


def power_from_overpass(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert ``out geom`` Overpass output to power features.

    Lines (``power`` = line, minor_line, cable) become LineStrings; substations and plants become
    points or polygons. ``voltage_kv`` is the largest value of the ``voltage`` tag in kV (OSM gives
    volts, ``;``-separated per circuit: ``"132000;33000"`` → 132), or null when missing.
    """
    feats: list[dict[str, Any]] = []
    seen: set[str] = set()
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        kind = str(tags.get("power") or "")
        if kind not in POWER_LINE_KINDS and kind not in POWER_SITE_KINDS:
            continue
        fid = f"osm-{str(el.get('type', 'x'))[0]}{el.get('id')}"
        if fid in seen:
            continue
        if kind in POWER_LINE_KINDS:
            if el.get("type") != "way":
                continue
            geom: Any = _line_from_geometry(el.get("geometry") or [])
        else:
            geom = _power_site_geometry(el)
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            geom = shapely.make_valid(geom)
        if geom.geom_type == "Polygon":
            geom = orient(geom, 1.0)
        seen.add(fid)
        props = {
            "kind": kind,
            "name": tags.get("name:en") or tags.get("name"),
            "voltage": tags.get("voltage"),
            "voltage_kv": parse_voltage_kv(tags.get("voltage")),
            "operator": tags.get("operator"),
            "location": tags.get("location"),
            "osm_type": el.get("type"),
            "osm_id": el.get("id"),
            "source": "osm",
        }
        feats.append(_feature(fid, geom, props))
    return feats


def perimeters_query(bbox: Sequence[float]) -> str:
    """Overpass QL for aerodrome boundaries (ways and relations) with full geometry."""
    return _overpass_header(bbox) + 'wr["aeroway"="aerodrome"];out geom;'


def _codes(row: dict[str, Any]) -> set[str]:
    return {str(row.get(k) or "").strip().upper() for k in ("ident", "icao_code", "gps_code")} - {""}


def perimeters_from_overpass(payload: dict[str, Any], aerodromes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aerodrome boundary polygons matched to OurAirports rows (``ident`` property).

    A boundary matches the row whose ident, ICAO or GPS code is its ``icao`` tag, else whose
    IATA code is its ``iata`` tag, else whose reference point it contains. An aerodrome keeps
    the largest boundary that matched it; ``match`` records how (icao, iata or contains).
    """
    from shapely.geometry import Point

    # US boundaries tag faa= rather than icao=; they still match by containing the reference point
    by_icao = {code: row for row in aerodromes for code in _codes(row)}
    by_iata = {str(row.get("iata_code") or "").strip().upper(): row for row in aerodromes}
    by_iata.pop("", None)
    points = [(row, Point(float(row["longitude_deg"]), float(row["latitude_deg"]))) for row in aerodromes]
    best: dict[str, tuple[float, dict[str, Any]]] = {}
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        if tags.get("aeroway") != "aerodrome":
            continue
        geom = _assembly_geometry(el)
        if geom is None or geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        if not geom.is_valid:
            geom = shapely.make_valid(geom)
            if geom.geom_type not in ("Polygon", "MultiPolygon"):
                continue
        row, how = by_icao.get(str(tags.get("icao") or "").strip().upper()), "icao"
        if row is None:
            row, how = by_iata.get(str(tags.get("iata") or "").strip().upper()), "iata"
        if row is None:
            row = next((r for r, pt in points if geom.contains(pt)), None)
            how = "contains"
        if row is None:
            continue
        ident = str(row["ident"]).strip().upper()
        area = _approx_area_m2(geom)
        if ident in best and best[ident][0] >= area:
            continue
        geom = orient(geom, 1.0) if geom.geom_type == "Polygon" else shapely.MultiPolygon([orient(g, 1.0) for g in geom.geoms])
        best[ident] = (area, _feature(f"osm-{str(el.get('type', 'x'))[0]}{el.get('id')}", geom, {
            "ident": ident, "match": how, "name": tags.get("name:en") or tags.get("name"),
            "osm_type": el.get("type"), "osm_id": el.get("id"), "source": "osm"}))
    return [feature for _, feature in sorted(best.values(), key=lambda t: t[1]["properties"]["ident"])]


def _feet_to_m(value: str | None) -> float | None:
    try:
        feet = float(value or "")
    except ValueError:
        return None
    return round(feet * FT_TO_M, 2) if math.isfinite(feet) and feet > 0 else None


def runways_from_ourairports(text: str, idents: Iterable[str]) -> list[dict[str, Any]]:
    """Open runways of the given aerodromes from OurAirports ``runways.csv``: a line from the
    ``le`` end to the ``he`` end, with ``width_m`` and ``length_m`` (feet converted)."""
    wanted = {i.strip().upper() for i in idents}
    feats: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(text)):
        ident = (row.get("airport_ident") or "").strip().upper()
        if ident not in wanted or (row.get("closed") or "0").strip() == "1":
            continue
        try:
            ends = [(float(row[f"{e}_longitude_deg"]), float(row[f"{e}_latitude_deg"])) for e in ("le", "he")]
        except (KeyError, TypeError, ValueError):
            continue
        if ends[0] == ends[1] or not all(math.isfinite(v) for end in ends for v in end):
            continue
        width, length = (_feet_to_m(row.get(k)) for k in ("width_ft", "length_ft"))
        feats.append(_feature(f"runway-{row.get('id') or len(feats)}", LineString(ends), {
            "airport_ident": ident, "le_ident": row.get("le_ident"), "he_ident": row.get("he_ident"),
            "width_m": width, "length_m": length, "surface": row.get("surface") or None, "source": "ourairports"}))
    return feats


def filter_ourairports(text: str, bbox: Sequence[float], margin_deg: float) -> list[dict[str, Any]]:
    """Rows of OurAirports CSV text inside ``bbox`` expanded by ``margin_deg`` (active types only)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    rows: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("type") or "") not in ACTIVE_TYPES:
            continue
        try:
            lat = float(row.get("latitude_deg") or "nan")
            lon = float(row.get("longitude_deg") or "nan")
        except ValueError:
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        if min_lon - margin_deg <= lon <= max_lon + margin_deg and min_lat - margin_deg <= lat <= max_lat + margin_deg:
            rows.append(row)
    rows.sort(key=lambda r: (r.get("type", ""), r.get("ident", "")))
    return rows


def build_region_pack(
    region: str | Region,
    dest: Path,
    *,
    lite: bool = False,
    name: str | None = None,
    say: Say = _say,
) -> tuple[Path, dict[str, bool]]:
    """Download real data for a region into ``dest/<name>``.

    Every layer is attempted independently; failures are reported and the pack is written
    with the layers that succeeded. Returns ``(pack_dir, {layer: ok})``.
    """
    from airbrief_core.data import fetch
    from airbrief_core.data.sources import copernicus_tiles_for_bbox

    reg = REGIONS[region] if isinstance(region, str) else region
    pack_name = name or (f"{reg.name}-lite" if lite else reg.name)
    dest = Path(dest)
    cache = dest / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    final, work = _prepare_dir(dest, pack_name)
    bbox = reg.bbox
    layers: dict[str, LayerEntry] = {}
    attribution: list[str] = []
    status: dict[str, bool] = {}
    aerodrome_rows: list[dict[str, Any]] = []

    def attempt(key: str, fn: Callable[[], LayerEntry]) -> None:
        say(f"[{key}] ...")
        try:
            layers[key] = fn()
            status[key] = True
            extra = layers[key].model_dump()
            count = extra.get("feature_count")
            say(f"[{key}] done" + (f" ({count} features)" if count is not None else ""))
        except (AirBriefError, OSError, ValueError, KeyError, RuntimeError) as exc:
            status[key] = False
            message = exc.message if isinstance(exc, AirBriefError) else str(exc)
            say(f"[{key}] FAILED: {message}")

    def aerodromes() -> LayerEntry:
        src = SOURCES["ourairports"]
        csv_path = fetch.download(src.url, cache / "airports.csv", timeout=120, retries=4)
        rows = filter_ourairports(csv_path.read_text(encoding="utf-8-sig"), bbox, reg.aerodrome_margin_deg)
        aerodrome_rows.extend(rows)
        n = write_aerodromes_csv(work / DEFAULT_FILES["aerodromes"], rows)
        attribution.append(src.attribution)
        return _layer(file=DEFAULT_FILES["aerodromes"], source=src.key, licence=src.licence,
                          feature_count=n, margin_deg=reg.aerodrome_margin_deg, url=src.url)

    def dem() -> LayerEntry:
        src = SOURCES["copernicus_glo90" if lite else "copernicus_glo30"]
        urls = copernicus_tiles_for_bbox(bbox, lite=lite)
        say(f"[dem] reading {len(urls)} Copernicus tile(s) windowed over HTTPS")
        result = fetch.read_remote_mosaic(urls, bbox, nodata=DEM_NODATA)
        if not np.any(result.array != np.float32(DEM_NODATA)):
            raise ValueError("DEM window contains only nodata")
        _write_geotiff(work / DEFAULT_FILES["dem"], result.array, result.transform, DEM_NODATA,
                       description="elevation", units="m")
        attribution.append(src.attribution)
        return _layer(file=DEFAULT_FILES["dem"], source=src.key, licence=src.licence, units="m",
                          vertical_datum="EGM2008 (≈ AMSL)", resolution_deg=abs(float(result.transform.a)),
                          tiles=[u.rsplit("/", 1)[-1] for u in result.sources],
                          missing_tiles=[u.rsplit("/", 1)[-1] for u in result.missing])

    def population() -> LayerEntry:
        assert reg.iso3 is not None
        src = worldpop_source(reg.iso3)
        try:
            result = fetch.read_remote_mosaic([src.url], bbox, nodata=POP_NODATA)
        except AirBriefError as exc:
            say(f"[population] windowed read failed ({exc.message}); downloading the full file")
            local = fetch.download(src.url, cache / src.url.rsplit("/", 1)[-1], timeout=300, retries=5)
            result = fetch.read_remote_mosaic([str(local)], bbox, nodata=POP_NODATA)
        arr = np.where(result.array < 0, np.float32(POP_NODATA), result.array).astype(np.float32)
        _write_geotiff(work / DEFAULT_FILES["population"], arr, result.transform, POP_NODATA,
                       description="population", units="people/pixel")
        attribution.append(src.attribution)
        valid = arr[arr != np.float32(POP_NODATA)]
        return _layer(file=DEFAULT_FILES["population"], source=src.key, licence=src.licence,
                          units="people/pixel", resolution_deg=abs(float(result.transform.a)),
                          total_people=round(float(valid.sum()), 0) if valid.size else 0.0, url=src.url)

    def perimeters() -> LayerEntry:
        if not aerodrome_rows:
            raise ValueError("no aerodromes to match boundaries to")
        m = reg.aerodrome_margin_deg
        wide = (bbox[0] - m, bbox[1] - m, bbox[2] + m, bbox[3] + m)
        return _osm_layer("aerodrome_perimeters", perimeters_query(wide),
                          lambda payload: perimeters_from_overpass(payload, aerodrome_rows), work, attribution, say)

    def runways() -> LayerEntry:
        src = SOURCES["ourairports_runways"]
        csv_path = fetch.download(src.url, cache / "runways.csv", timeout=120, retries=4)
        feats = runways_from_ourairports(csv_path.read_text(encoding="utf-8-sig"), (str(r["ident"]) for r in aerodrome_rows))
        n = _write_geojson(work / DEFAULT_FILES["runways"], feats)
        attribution.append(src.attribution)
        return _layer(file=DEFAULT_FILES["runways"], source=src.key, licence=src.licence, feature_count=n,
                      units="m", url=src.url, source_url=src.url)

    def osm_layer(key: str) -> LayerEntry:
        query, convert = OSM_LAYERS[key]
        return _osm_layer(key, query(bbox), convert, work, attribution, say)

    try:
        attempt("aerodromes", aerodromes)
        attempt("dem", dem)
        if reg.iso3 is None:
            say("[population] skipped: no --iso3 given, so the pack has no population layer")
        else:
            attempt("population", population)
        attempt("obstacles", lambda: osm_layer("obstacles"))
        attempt("pois", lambda: osm_layer("pois"))
        attempt("water", lambda: osm_layer("water"))
        attempt("assemblies", lambda: osm_layer("assemblies"))
        attempt("power", lambda: osm_layer("power"))
        attempt("aerodrome_perimeters", perimeters)
        attempt("runways", runways)
        _write_geojson(work / DEFAULT_FILES["zones"], [])
        layers["zones"] = _layer(file=DEFAULT_FILES["zones"], source="user", licence="", feature_count=0,
                                     note="Add restricted areas as polygons with name, zone, ceiling_agl_m")
        status["zones"] = True

        manifest = DataPackManifest(
            name=pack_name,
            version="1",
            created_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            synthetic=False,
            bbox=list(bbox),
            layers=layers,
            attribution=attribution,
            description=f"{reg.title}{' (lite)' if lite else ''}: {reg.description}",
            region=reg.name,
        )
        write_manifest(work, seal_manifest(work, manifest))
        return _swap_in(work, final), status
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _osm_layer(
    key: str,
    query: str,
    convert: Callable[[dict[str, Any]], list[dict[str, Any]]],
    folder: Path,
    attribution: list[str],
    say: Say,
) -> LayerEntry:
    from airbrief_core.data import fetch

    src = SOURCES["osm_overpass"]
    payload, endpoint = fetch.overpass_query(query)
    if endpoint != OVERPASS_ENDPOINTS[0]:
        say(f"[{key}] served by the mirror {endpoint}, not the main Overpass server")
    n = _write_geojson(folder / DEFAULT_FILES[key], convert(payload))
    if src.attribution not in attribution:
        attribution.append(src.attribution)
    return _layer(file=DEFAULT_FILES[key], source=src.key, licence=src.licence, feature_count=n,
                  osm_base=str((payload.get("osm3s") or {}).get("timestamp_osm_base", "")), source_url=endpoint)


def refetch_osm_layers(
    root: Path, keys: Sequence[str] | None = None, say: Say = _say
) -> tuple[DataPackManifest, dict[str, bool]]:
    """Query the OpenStreetMap layers ``keys`` again for the real pack at ``root``, write them in
    place and seal the pack. Without ``keys``: the OSM layers its manifest lacks."""
    manifest = DataPackManifest.model_validate_json((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    if manifest.synthetic:
        raise ValueError(f"'{manifest.name}' is the synthetic pack; it has no OpenStreetMap layers to fetch")
    if keys is None:
        keys = [k for k in OSM_LAYERS if k not in manifest.layers]
    unknown = sorted(set(keys) - set(OSM_LAYERS))
    if unknown:
        raise ValueError(f"not OpenStreetMap layers: {', '.join(unknown)} (choose from {', '.join(OSM_LAYERS)})")
    layers = dict(manifest.layers)
    attribution = list(manifest.attribution)
    status: dict[str, bool] = {}
    try:
        for key in keys:
            say(f"[{key}] ...")
            try:
                query, convert = OSM_LAYERS[key]
                layers[key] = _osm_layer(key, query(manifest.bbox), convert, root, attribution, say)
                status[key] = True
                say(f"[{key}] done ({layers[key].model_dump().get('feature_count')} features)")
            except (AirBriefError, OSError, ValueError, KeyError, RuntimeError) as exc:
                status[key] = False
                say(f"[{key}] FAILED: {exc.message if isinstance(exc, AirBriefError) else exc}")
    finally:
        # a layer already rewritten must be sealed, or the API refuses the whole pack
        write_manifest(root, manifest.model_copy(update={"layers": layers, "attribution": attribution}))
        sealed = seal_pack(root)
    return sealed, status


def seal_pack(root: Path) -> DataPackManifest:
    """Record the current SHA-256 of every layer file of the pack at ``root`` in its manifest."""
    manifest = DataPackManifest.model_validate_json((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    sealed = seal_manifest(root, manifest)
    write_manifest(root, sealed)
    return sealed


def _is_url(src: str) -> bool:
    return re.match(r"^https?://", src, re.I) is not None


def read_zone_file(src: str) -> tuple[str, list[dict[str, Any]]]:
    """The format (``ed269`` or ``ed318``) and the zones, as features, of a zone file: a path or a
    URL to the JSON, or to a zip with one JSON inside (ENAIRE publishes those).

    The zones are read the way an uploaded zone file is: the zip with the upload's guards, then the
    same adapters and limits. Raises ``ValueError`` (or an ``AirBriefError``) when it can't be read.
    """
    import tempfile

    from airbrief_core.data import fetch
    from airbrief_core.io.geozones import detect_geozone_format, geozone_features
    from airbrief_core.io.ingest import IngestLimits, safe_extract_zip

    try:
        data = fetch.fetch_bytes(src, timeout=180) if _is_url(src) else Path(src).read_bytes()
    except OSError as exc:
        raise ValueError(exc.strerror or str(exc)) from exc
    if data[:2] == b"PK":
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "zones.zip"
            archive.write_bytes(data)
            found = [p for p in safe_extract_zip(archive, Path(tmp) / "files", IngestLimits())
                     if p.suffix.lower() in (".json", ".geojson")]
            if len(found) != 1:
                raise ValueError(f"the zip should hold one .json or .geojson file, found {len(found)}")
            data = found[0].read_bytes()
    doc = json.loads(data.decode("utf-8-sig"))
    fmt = detect_geozone_format(doc)
    if fmt is None:
        raise ValueError("it is not an ED-269 JSON or ED-318 GeoJSON zone file")
    return fmt, geozone_features(doc, fmt)


def add_zones(root: Path, fmt: str, features: list[dict[str, Any]], src: str, credit: str = "") -> tuple[DataPackManifest, int]:
    """Replace the zones layer of the pack at ``root`` with the zones (from :func:`read_zone_file`)
    that reach into the pack's area, with crossed rings repaired, then seal the pack.

    ``src`` is recorded in the layer; ``credit`` names the publisher and its terms in the layer and
    the pack's attribution. Returns the sealed manifest and the number of zones kept.
    """
    from shapely.geometry import box, shape

    from airbrief_core.geo.validity import polygonal_part, repair

    manifest = DataPackManifest.model_validate_json((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    area = box(*manifest.bbox)
    kept: list[dict[str, Any]] = []
    total = 0
    for feat in features:
        total += 1
        geom, _ = repair(shape(feat["geometry"]))
        geom = polygonal_part(geom)
        if geom is None or not geom.intersects(area):
            continue
        kept.append(_feature(str(len(kept)), geom, feat["properties"]))
    n = _write_geojson(root / DEFAULT_FILES["zones"], kept)
    fields: dict[str, Any] = {"source": fmt, "licence": credit, "feature_count": n, "zones_in_file": total,
                              "imported_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if _is_url(src):
        fields["url"] = src
    else:
        fields["file_name"] = Path(src).name
    layers = dict(manifest.layers)
    layers["zones"] = _layer(file=DEFAULT_FILES["zones"], **fields)
    attribution = [a for a in manifest.attribution if not a.startswith("Zones: ")]
    if credit:
        attribution.append(f"Zones: {credit}")
    write_manifest(root, manifest.model_copy(update={"layers": layers, "attribution": attribution}))
    return seal_pack(root), n


OSM_LAYERS: dict[str, tuple[Callable[[Sequence[float]], str], Callable[[dict[str, Any]], list[dict[str, Any]]]]] = {
    "obstacles": (obstacles_query, obstacles_from_overpass),
    "pois": (pois_query, pois_from_overpass),
    "water": (water_query, water_from_overpass),
    "assemblies": (assemblies_query, assemblies_from_overpass),
    "power": (power_query, power_from_overpass),
}
"""The layers a region pack takes from Overpass, with their query and converter."""


def _default_dest() -> Path:
    return Path(os.environ.get("DATAPACK_DIR") or "data/datapacks")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m seed.datapack",
        description="Build an AirBrief data pack from free sources, or a deterministic synthetic pack.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--region", choices=sorted(REGIONS), help="Download real data for a predefined region.")
    mode.add_argument("--bbox", metavar="MIN_LON,MIN_LAT,MAX_LON,MAX_LAT",
                      help="Download real data for any area of at most 2 x 2 degrees (needs --name).")
    mode.add_argument("--synthetic", action="store_true", help="Build the deterministic offline demo pack.")
    mode.add_argument("--seal", type=Path, metavar="PACK_DIR",
                      help="Record new layer hashes in an existing pack after editing a layer (e.g. zones.geojson).")
    mode.add_argument("--refetch", type=Path, metavar="PACK_DIR",
                      help="Fetch OpenStreetMap layers of a real pack again (default: the ones its build missed), then seal it.")
    parser.add_argument("--layers", help="--refetch: comma-separated layers, e.g. pois,water.")
    parser.add_argument("--zones", metavar="FILE_OR_URL",
                        help="With --bbox, --region or --seal: put the zones of a national ED-269 or ED-318 file "
                             "(JSON, or a zip with one) that reach into the pack's area in its zones layer.")
    parser.add_argument("--zones-credit", default="", metavar="TEXT",
                        help="--zones: the publisher and its terms, for the pack's attribution.")
    parser.add_argument("--lite", action="store_true", help="Region packs: use the 3 arc-second (GLO-90) DEM.")
    parser.add_argument("--name", help="Pack name (default: 'demo' for --synthetic, the region name otherwise).")
    parser.add_argument("--dest", type=Path, default=None, help="Parent directory (default: $DATAPACK_DIR or data/datapacks).")
    parser.add_argument("--iso3", help="--bbox packs: the country (ISO 3166-1 alpha-3, e.g. NPL) whose WorldPop "
                                       "file gives the population layer; without it the pack has none.")
    args = parser.parse_args(argv)
    if args.synthetic and args.lite:
        parser.error("--lite applies to --region and --bbox packs only")
    if args.iso3 is not None and args.bbox is None:
        parser.error("--iso3 applies to --bbox packs only")
    if args.layers is not None and args.refetch is None:
        parser.error("--layers applies to --refetch only")
    if args.zones is not None and (args.synthetic or args.refetch is not None):
        parser.error("--zones applies to --bbox, --region and --seal only")
    if args.zones_credit and args.zones is None:
        parser.error("--zones-credit needs --zones")
    if args.bbox is not None:
        if not args.name:
            parser.error("--bbox needs --name (the pack's name)")
        try:
            args.bbox = check_region_bbox([float(v) for v in args.bbox.split(",")])
            if args.iso3 is not None:
                worldpop_url(args.iso3)
        except ValueError as exc:
            parser.error(f"--bbox/--iso3: {exc}")
    return args


def region_for_bbox(name: str, bbox: tuple[float, float, float, float], iso3: str | None) -> Region:
    """A one-off region for ``--bbox`` (checked by :func:`~airbrief_core.data.sources.check_region_bbox`)."""
    w, s_, e, n = bbox
    return Region(
        name=name,
        title=f"Area {name}",
        bbox=bbox,
        description=f"Built from --bbox {w:g},{s_:g},{e:g},{n:g}" + (f" ({iso3.upper()})." if iso3 else "."),
        iso3=iso3.upper() if iso3 else None,
    )


def _zone_file_or_exit(src: str) -> tuple[str, list[dict[str, Any]]]:
    try:
        return read_zone_file(src)
    except (AirBriefError, ValueError) as exc:
        raise SystemExit(f"Cannot read zones from {src}: {exc.message if isinstance(exc, AirBriefError) else exc}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code (1 if any layer failed)."""
    args = _parse_args(argv)
    # read the zone file first, so a bad one fails before a long download
    zones = _zone_file_or_exit(args.zones) if args.zones is not None else None

    def put_zones(root: Path, zones: tuple[str, list[dict[str, Any]]]) -> DataPackManifest:
        sealed, n = add_zones(root, *zones, src=args.zones, credit=args.zones_credit)
        _say(f"[zones] {n} of {len(zones[1])} zones from {args.zones}")
        return sealed

    if args.seal is not None:
        try:
            sealed = seal_pack(args.seal) if zones is None else put_zones(args.seal, zones)
        except FileNotFoundError as exc:
            raise SystemExit(f"Cannot seal {args.seal}: {exc.filename} does not exist") from exc
        _say(f"Data pack '{sealed.name}' sealed: content SHA-256 {sealed.content_sha256}")
        return 0
    if args.refetch is not None:
        keys = [k.strip() for k in args.layers.split(",") if k.strip()] if args.layers else None
        try:
            sealed, fetched = refetch_osm_layers(args.refetch, keys)
        except FileNotFoundError as exc:
            raise SystemExit(f"Cannot refetch {args.refetch}: {exc.filename} does not exist") from exc
        except ValueError as exc:
            raise SystemExit(f"Cannot refetch {args.refetch}: {exc}") from exc
        if not fetched:
            _say(f"Data pack '{sealed.name}' has every OpenStreetMap layer; name some with --layers to fetch them again")
        _say(f"Data pack '{sealed.name}' sealed: content SHA-256 {sealed.content_sha256}")
        return 0 if all(fetched.values()) else 1
    dest: Path = args.dest or _default_dest()
    if args.synthetic:
        root = build_synthetic_pack(dest, args.name or "demo")
        _say(f"Synthetic data pack '{root.name}' written to {root.as_posix()}")
        return 0
    region = region_for_bbox(args.name, args.bbox, args.iso3) if args.bbox is not None else args.region
    root, status = build_region_pack(region, dest, lite=args.lite, name=args.name)
    if zones is not None:
        put_zones(root, zones)
    summary = {
        "dem": status.get("dem", False),
        "population": status.get("population"),
        "osm": all(status.get(k, False) for k in ("obstacles", "pois", "water")),
        "aerodromes": status.get("aerodromes", False),
    }
    marks = " ".join(f"{k} {'–' if ok is None else '✓' if ok else '✗'}" for k, ok in summary.items())
    _say(f"Data pack '{root.name}' ready: {marks}")
    _say(f"Location: {root.as_posix()}")
    missed = [k for k in OSM_LAYERS if status.get(k) is False]
    if missed:
        _say(f"Overpass did not answer for {', '.join(missed)}; try again later with: "
             f"python -m seed.datapack --refetch {root.as_posix()}")
    return 0 if all(status.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
