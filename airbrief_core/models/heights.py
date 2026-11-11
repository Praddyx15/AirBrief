"""Height-reference models shared by the airspace, survey and eVTOL corridor modules.

A planned height is read in one of three references (airbrief_core.analyses.heights):

* ``agl`` — above the ground directly below the aircraft (terrain following);
* ``takeoff`` — above the take-off point (DJI "ALT", ArduPilot relative altitude);
* ``amsl`` — above mean sea level (the DEM's vertical datum, EGM2008 for Copernicus).

The default stays ``agl`` everywhere, so analyses stored before the reference existed keep their
meaning. Distances along a route are metres from its start; heights are metres.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import OutputModel

__all__ = [
    "CruiseReference",
    "DemInfo",
    "ElevationSource",
    "HeightReference",
    "HeightSegment",
    "TakeoffPoint",
    "TakeoffSource",
]

HeightReference = Literal["agl", "takeoff", "amsl"]
"""How a planned height is read: above ground, above the take-off point, or above sea level."""

CruiseReference = Literal["agl", "amsl"]
"""eVTOL corridor cruise: a height above the ground or a fixed level above sea level."""

TakeoffSource = Literal["given", "first_vertex", "centroid", "point", "origin"]
"""Where the take-off point came from: given, the first vertex of the route, the centroid of the
area, the point itself, or the corridor origin."""

ElevationSource = Literal["override", "dem"]
"""Where the take-off ground elevation came from: typed by the user or sampled from the DEM."""


class TakeoffPoint(OutputModel):
    """The take-off point and the ground elevation (m AMSL) used for heights above take-off."""

    lon: float
    lat: float
    elevation_m: float | None = None
    source: TakeoffSource
    elevation_source: ElevationSource | None = None


class HeightSegment(OutputModel):
    """A stretch of a route below the minimum height above ground or above the ceiling.

    ``start_m``/``end_m`` are distances along the route, at the interpolated crossing of the limit.
    ``peak_agl_m`` is the lowest height above ground of a below-minimum stretch, or the highest of
    an above-ceiling one; ``limit_m`` is the minimum (or the lowest ceiling) it is compared with.
    """

    start_m: float
    end_m: float
    length_m: float
    peak_agl_m: float
    limit_m: float


class DemInfo(OutputModel):
    """What the data-pack elevation model is, described from its manifest (honest labels)."""

    source: str
    title: str
    surface_model: bool | None = Field(
        default=None, description="True: heights include roofs and canopy (a DSM, not bare earth); None: unknown."
    )
    synthetic: bool = False
    vertical_datum: str | None = None
    resolution_m: float | None = None
    caveat: str
