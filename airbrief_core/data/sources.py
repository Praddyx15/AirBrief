"""Registry of free, keyless data sources and predefined seed regions.

Every URL below was checked against the provider's public documentation (2026-10-08, again 2026-10-30):

* OurAirports open data mirror (public domain):
  ``https://davidmegginson.github.io/ourairports-data/airports.csv`` and ``…/runways.csv``
* Copernicus DEM on the AWS Open Data registry (anonymous HTTPS). Tiles are 1°x1° Cloud-Optimised
  GeoTIFFs named ``Copernicus_DSM_COG_<arcsec>_<N|S><lat>_00_<E|W><lon>_00_DEM`` inside a folder
  of the same name; ``<arcsec>`` is ``10`` for GLO-30 (bucket ``copernicus-dem-30m``) and ``30``
  for GLO-90 (bucket ``copernicus-dem-90m``). Ocean-only tiles do not exist (HTTP 404/403).
* WorldPop "Global 2000-2020 1km UN-adjusted" 2020, people per pixel (CC BY 4.0), one file per
  country: ``…/Global_2000_2020_1km_UNadj/2020/{ISO3}/{iso3}_ppp_2020_1km_Aggregated_UNadj.tif``
  (checked for IND and NPL, 2026-10-19). The India page is hub.worldpop.org id 36811.
* Overpass API public instances without keys (OSM data, ODbL), from the OSM wiki list:
  ``overpass-api.de`` (main, FOSSGIS), ``overpass.private.coffee`` and ``maps.mail.ru``. The main
  one often answers 502 "at capacity"; the next mirror is then tried.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..models.common import BBox

USER_AGENT = "AirBrief-seed/0.1 (+https://github.com/Praddyx15/AirBrief; open-source pre-flight evidence)"
"""User-Agent sent with every download, as requested by the OSM/Overpass usage policies."""


@dataclass(frozen=True)
class DataSource:
    """One upstream dataset with its licence and attribution text."""

    key: str
    title: str
    url: str
    licence: str
    attribution: str
    homepage: str
    notes: str = ""


@dataclass(frozen=True)
class Region:
    """A predefined region for ``python -m seed.datapack --region <name>``."""

    name: str
    title: str
    bbox: tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat
    aerodrome_margin_deg: float = 0.3
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    iso3: str | None = None  # country of the WorldPop file; None: no population layer

    def as_bbox(self) -> BBox:
        return BBox.from_bounds(self.bbox)


OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OURAIRPORTS_RUNWAYS_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"
COPERNICUS_GLO30_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
COPERNICUS_GLO90_BASE = "https://copernicus-dem-90m.s3.amazonaws.com"
WORLDPOP_2020_1KM_TEMPLATE = (
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/2020/{ISO3}/"
    "{iso3}_ppp_2020_1km_Aggregated_UNadj.tif"
)
WORLDPOP_IND_2020_1KM_URL = WORLDPOP_2020_1KM_TEMPLATE.format(ISO3="IND", iso3="ind")
OVERPASS_ENDPOINTS: tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)

COPERNICUS_ATTRIBUTION = (
    "Copernicus DEM © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided "
    "under COPERNICUS by the European Union and ESA; all rights reserved"
)

SOURCES: dict[str, DataSource] = {
    "ourairports": DataSource(
        key="ourairports",
        title="OurAirports airports.csv",
        url=OURAIRPORTS_URL,
        licence="Public Domain",
        attribution="Aerodromes: OurAirports (public domain)",
        homepage="https://ourairports.com/data/",
    ),
    "ourairports_runways": DataSource(
        key="ourairports_runways",
        title="OurAirports runways.csv",
        url=OURAIRPORTS_RUNWAYS_URL,
        licence="Public Domain",
        attribution="Runways: OurAirports (public domain)",
        homepage="https://ourairports.com/data/",
        notes="Runway ends and widths: the fallback aerodrome outline when OSM has no boundary.",
    ),
    "copernicus_glo30": DataSource(
        key="copernicus_glo30",
        title="Copernicus DEM GLO-30 (1 arc-second, ~30 m)",
        url=COPERNICUS_GLO30_BASE,
        licence="Copernicus DEM licence (free use with attribution)",
        attribution=COPERNICUS_ATTRIBUTION,
        homepage="https://registry.opendata.aws/copernicus-dem/",
        notes="Cloud-Optimised GeoTIFF tiles, read windowed over HTTPS.",
    ),
    "copernicus_glo90": DataSource(
        key="copernicus_glo90",
        title="Copernicus DEM GLO-90 (3 arc-second, ~90 m)",
        url=COPERNICUS_GLO90_BASE,
        licence="Copernicus DEM licence (free use with attribution)",
        attribution=COPERNICUS_ATTRIBUTION,
        homepage="https://registry.opendata.aws/copernicus-dem/",
        notes="Used by --lite packs.",
    ),
    "worldpop_ind_2020_1km": DataSource(
        key="worldpop_ind_2020_1km",
        title="WorldPop India 2020, 1 km, UN-adjusted, people per pixel",
        url=WORLDPOP_IND_2020_1KM_URL,
        licence="CC BY 4.0",
        attribution=(
            "Population: WorldPop (www.worldpop.org), Global High Resolution Population Denominators "
            "Project (2018), doi:10.5258/SOTON/WP00671, CC BY 4.0"
        ),
        homepage="https://hub.worldpop.org/geodata/summary?id=36811",
    ),
    "osm_overpass": DataSource(
        key="osm_overpass",
        title="OpenStreetMap via the Overpass API",
        url=OVERPASS_ENDPOINTS[0],
        licence="ODbL 1.0",
        attribution="© OpenStreetMap contributors (ODbL)",
        homepage="https://www.openstreetmap.org/copyright",
        notes="Mirrors tried in order: " + ", ".join(OVERPASS_ENDPOINTS),
    ),
    "synthetic": DataSource(
        key="synthetic",
        title="AirBrief synthetic demo data",
        url="",
        licence="MIT (generated)",
        attribution="Synthetic demo data generated by AirBrief — not real-world information",
        homepage="",
    ),
}

REGIONS: dict[str, Region] = {
    "bengaluru": Region(
        name="bengaluru",
        title="Bengaluru metropolitan area",
        bbox=(77.25, 12.65, 78.00, 13.35),
        aerodrome_margin_deg=0.3,
        description="About 80 km x 78 km around Bengaluru, Karnataka, India.",
        tags=("india", "karnataka"),
        iso3="IND",
    ),
}

MAX_REGION_SPAN_DEG = 2.0
"""Largest pack built from a ``--bbox`` (degrees of longitude and of latitude): bigger areas
time out on the public Overpass servers and pull many DEM tiles. Split them into several packs."""

_ISO3 = re.compile(r"^[A-Za-z]{3}$")


def worldpop_url(iso3: str) -> str:
    """The WorldPop 2020 1 km UN-adjusted file of one country (ISO 3166-1 alpha-3 code)."""
    if not _ISO3.match(iso3):
        raise ValueError(f"'{iso3}' is not an ISO 3166-1 alpha-3 country code (three letters, e.g. IND)")
    return WORLDPOP_2020_1KM_TEMPLATE.format(ISO3=iso3.upper(), iso3=iso3.lower())


def worldpop_source(iso3: str) -> DataSource:
    """The ``worldpop_<iso3>_2020_1km`` data source (``worldpop_ind_2020_1km`` for India)."""
    code = iso3.upper()
    india = SOURCES["worldpop_ind_2020_1km"]
    if code == "IND":
        return india
    return DataSource(
        key=f"worldpop_{code.lower()}_2020_1km",
        title=f"WorldPop {code} 2020, 1 km, UN-adjusted, people per pixel",
        url=worldpop_url(code),
        licence=india.licence,
        attribution=india.attribution,
        homepage="https://hub.worldpop.org/geodata/listing?id=75",
    )


def check_region_bbox(bbox: tuple[float, float, float, float] | list[float]) -> tuple[float, float, float, float]:
    """``(min_lon, min_lat, max_lon, max_lat)`` checked: finite WGS84 degrees, min < max, at most
    :data:`MAX_REGION_SPAN_DEG` on each side. Raises ``ValueError`` with a message for people."""
    values = [float(v) for v in bbox]
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        raise ValueError("the bbox needs four numbers: min_lon,min_lat,max_lon,max_lat")
    min_lon, min_lat, max_lon, max_lat = values
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise ValueError("the bbox must be min_lon,min_lat,max_lon,max_lat in degrees, with min < max")
    width, height = max_lon - min_lon, max_lat - min_lat
    if width > MAX_REGION_SPAN_DEG + 1e-9 or height > MAX_REGION_SPAN_DEG + 1e-9:
        raise ValueError(
            # plain ASCII: argparse prints this to a Windows console too
            f"the bbox is {width:.2f} x {height:.2f} degrees; one pack covers at most {MAX_REGION_SPAN_DEG:g} x "
            f"{MAX_REGION_SPAN_DEG:g} degrees (Overpass time limits and DEM size). Split the area into several packs."
        )
    return min_lon, min_lat, max_lon, max_lat


def _tile_label(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"{ns}{abs(lat_floor):02d}_00_{ew}{abs(lon_floor):03d}_00"


def copernicus_tile_url(lat_floor: int, lon_floor: int, *, lite: bool = False) -> str:
    """HTTPS URL of the Copernicus COG tile whose south-west corner is ``(lon_floor, lat_floor)``."""
    arcsec = 30 if lite else 10
    base = COPERNICUS_GLO90_BASE if lite else COPERNICUS_GLO30_BASE
    name = f"Copernicus_DSM_COG_{arcsec}_{_tile_label(lat_floor, lon_floor)}_DEM"
    return f"{base}/{name}/{name}.tif"


def copernicus_tiles_for_bbox(
    bbox: tuple[float, float, float, float] | list[float], *, lite: bool = False
) -> list[str]:
    """All Copernicus tile URLs intersecting ``(min_lon, min_lat, max_lon, max_lat)``."""
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    if max_lon <= min_lon or max_lat <= min_lat:
        raise ValueError("bbox must have max > min on both axes")
    eps = 1e-9
    urls: list[str] = []
    for lat in range(math.floor(min_lat), math.floor(max_lat - eps) + 1):
        for lon in range(math.floor(min_lon), math.floor(max_lon - eps) + 1):
            urls.append(copernicus_tile_url(lat, lon, lite=lite))
    return urls


__all__ = [
    "COPERNICUS_ATTRIBUTION",
    "COPERNICUS_GLO30_BASE",
    "COPERNICUS_GLO90_BASE",
    "MAX_REGION_SPAN_DEG",
    "OURAIRPORTS_RUNWAYS_URL",
    "OURAIRPORTS_URL",
    "OVERPASS_ENDPOINTS",
    "REGIONS",
    "SOURCES",
    "USER_AGENT",
    "WORLDPOP_2020_1KM_TEMPLATE",
    "WORLDPOP_IND_2020_1KM_URL",
    "DataSource",
    "Region",
    "check_region_bbox",
    "copernicus_tile_url",
    "copernicus_tiles_for_bbox",
    "worldpop_source",
    "worldpop_url",
]
