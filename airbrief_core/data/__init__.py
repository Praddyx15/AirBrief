"""Local data packs (DEM, population, aerodromes, OSM-derived layers) and their samplers.

Everything here works offline, except :mod:`airbrief_core.data.fetch`, which the seed scripts use
to build packs. ``fetch`` is deliberately not imported here.
"""

from __future__ import annotations

from .aerodromes import Aerodrome, AerodromeIndex
from .datapack import DataPack, DataPackManifest, DataPackRegistry, LayerEntry
from .raster import RasterSampler
from .sources import REGIONS, SOURCES
from .vectors import PointFeature, PointIndex, PolygonFeature, PolygonIndex

__all__ = [
    "REGIONS",
    "SOURCES",
    "Aerodrome",
    "AerodromeIndex",
    "DataPack",
    "DataPackManifest",
    "DataPackRegistry",
    "LayerEntry",
    "PointFeature",
    "PointIndex",
    "PolygonFeature",
    "PolygonIndex",
    "RasterSampler",
]
