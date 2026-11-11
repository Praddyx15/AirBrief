"""World Magnetic Model 2025 (WMM2025): declination, inclination and field intensity.

Coefficients: ``airbrief_core/data/wmm/WMM2025.COF`` from NOAA NCEI (public domain, epoch
2025.0, valid 2025.0–2030.0), degree and order 12 main field plus secular variation.

Synthesis, as in the WMM2025 technical report (Chulliat et al., NOAA NCEI / British Geological
Survey, 2025) and NOAA's reference C library (``GeomagnetismLibrary.c``):

1. **Time.** ``g(t) = g(t0) + (t − t0)·ġ`` and the same for ``h``, with ``t0 = 2025.0`` and
   ``t`` in decimal years.
2. **Geodetic → geocentric spherical** on WGS84 (a = 6378.137 km, f = 1/298.257223563):
   ``Rc = a/√(1 − e² sin²φ)``, ``p = (Rc + h) cos φ``, ``z = (Rc(1 − e²) + h) sin φ``,
   ``r = √(p² + z²)``, ``φ' = asin(z/r)``.
3. **Field** with Schmidt semi-normalised associated Legendre functions ``P̆ₙᵐ(sin φ')`` and
   the geomagnetic reference radius ``a = 6371.2 km``::

       X' = −Σₙ (a/r)ⁿ⁺² Σₘ (gₙᵐ cos mλ + hₙᵐ sin mλ) dP̆ₙᵐ/dφ'
       Y' =  Σₙ (a/r)ⁿ⁺² Σₘ m (gₙᵐ sin mλ − hₙᵐ cos mλ) P̆ₙᵐ / cos φ'
       Z' = −Σₙ (n + 1)(a/r)ⁿ⁺² Σₘ (gₙᵐ cos mλ + hₙᵐ sin mλ) P̆ₙᵐ

4. **Rotation to geodetic**, ψ = φ' − φ: ``X = X' cos ψ − Z' sin ψ``, ``Y = Y'``,
   ``Z = X' sin ψ + Z' cos ψ``.
5. **Elements**: ``H = √(X² + Y²)``, ``F = √(H² + Z²)``, ``D = atan2(Y, X)``,
   ``I = atan2(Z, H)``. Secular variation of the elements uses the same sums with ``ġ, ḣ``.
6. **Grid variation** (polar navigation, |φ| ≥ 55°): ``GV = D − λ`` north, ``D + λ`` south,
   wrapped to ±180°.

The WMM2025 report defines the *blackout zone* where H < 2000 nT (compasses unreliable) and a
*caution zone* where 2000 ≤ H < 6000 nT. Validated against NOAA's official WMM2025 test values
(``tests/core/test_environment.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import AirBriefError

__all__ = [
    "WMM_BLACKOUT_NT",
    "WMM_CAUTION_NT",
    "MagneticElements",
    "WmmModel",
    "decimal_year",
    "load_wmm",
    "wmm",
]

WMM_COF_PATH = Path(__file__).resolve().parent.parent / "data" / "wmm" / "WMM2025.COF"
"""Shipped coefficient file (NOAA NCEI, public domain)."""

WGS84_A_KM = 6378.137
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
GEOMAGNETIC_RADIUS_KM = 6371.2

WMM_BLACKOUT_NT = 2000.0
"""Horizontal intensity below which the WMM2025 report declares the blackout zone."""
WMM_CAUTION_NT = 6000.0
"""Horizontal intensity below which the WMM2025 report declares the caution zone."""

_POLE_TOLERANCE_DEG = 1e-5  # MAG_GEO_POLE_TOLERANCE: avoid the cos φ' = 0 singularity of Y'

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class WmmModel:
    """Gauss coefficients of one WMM release, indexed ``[n, m]`` (n, m ≤ ``n_max``)."""

    name: str
    epoch: float
    release_date: str
    n_max: int
    g: FloatArray
    h: FloatArray
    g_dot: FloatArray
    h_dot: FloatArray

    @property
    def valid_from(self) -> float:
        return self.epoch

    @property
    def valid_until(self) -> float:
        """End of validity (exclusive): five years after the epoch."""
        return self.epoch + 5.0


@dataclass(frozen=True)
class MagneticElements:
    """Geomagnetic elements at one place and time (nT, degrees; ``*_dot`` per year)."""

    declination_deg: float
    inclination_deg: float
    horizontal_nt: float
    north_nt: float
    east_nt: float
    down_nt: float
    total_nt: float
    grid_variation_deg: float | None
    declination_dot_deg: float
    inclination_dot_deg: float
    horizontal_dot_nt: float
    total_dot_nt: float
    decimal_year: float
    altitude_km: float
    model: str
    within_validity: bool

    @property
    def blackout(self) -> bool:
        """WMM blackout zone: H < 2000 nT."""
        return self.horizontal_nt < WMM_BLACKOUT_NT

    @property
    def caution(self) -> bool:
        """WMM caution zone: 2000 ≤ H < 6000 nT."""
        return WMM_BLACKOUT_NT <= self.horizontal_nt < WMM_CAUTION_NT


def parse_cof(text: str) -> WmmModel:
    """Parse a WMM ``.COF`` file (header ``epoch name date``, rows ``n m g h ġ ḣ``, ``9999`` end)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise AirBriefError("wmm_invalid", "The WMM coefficient file is empty.")
    header = lines[0].split()
    try:
        epoch = float(header[0])
    except (IndexError, ValueError) as exc:
        raise AirBriefError("wmm_invalid", "The WMM coefficient file has no epoch header.") from exc
    name = header[1] if len(header) > 1 else "WMM"
    release = header[2] if len(header) > 2 else ""
    rows: list[tuple[int, int, float, float, float, float]] = []
    for ln in lines[1:]:
        if ln.strip().startswith("9999"):
            break
        parts = ln.split()
        if len(parts) < 6:
            raise AirBriefError("wmm_invalid", f"Malformed WMM coefficient row: {ln.strip()!r}")
        n, m = int(parts[0]), int(parts[1])
        rows.append((n, m, float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5])))
    if not rows:
        raise AirBriefError("wmm_invalid", "The WMM coefficient file has no coefficients.")
    n_max = max(r[0] for r in rows)
    shape = (n_max + 1, n_max + 1)
    g, h, gd, hd = (np.zeros(shape) for _ in range(4))
    for n, m, gv, hv, gdv, hdv in rows:
        if not (1 <= n <= n_max and 0 <= m <= n):
            raise AirBriefError("wmm_invalid", f"Invalid WMM degree/order n={n}, m={m}.")
        g[n, m], h[n, m], gd[n, m], hd[n, m] = gv, hv, gdv, hdv
    return WmmModel(name=name.replace("-", ""), epoch=epoch, release_date=release, n_max=n_max,
                    g=g, h=h, g_dot=gd, h_dot=hd)


@lru_cache(maxsize=2)
def load_wmm(path: str | None = None) -> WmmModel:
    """The shipped WMM2025 model (or the ``.COF`` file at ``path``), parsed once."""
    source = Path(path) if path else WMM_COF_PATH
    try:
        text = source.read_text(encoding="ascii")
    except OSError as exc:
        raise AirBriefError("wmm_missing", f"WMM coefficient file not found: {source.name}") from exc
    return parse_cof(text)


def decimal_year(day: date, fraction_of_day: float = 0.0) -> float:
    """Decimal year of a date (+ fraction of the day), as WMM's ``MAG_DateToYear`` (365/366 days)."""
    year_days = 366 if (day.year % 4 == 0 and day.year % 100 != 0) or day.year % 400 == 0 else 365
    return day.year + (day.timetuple().tm_yday - 1 + float(fraction_of_day)) / year_days


def _legendre(n_max: int, sin_phi: float, cos_phi: float) -> tuple[FloatArray, FloatArray]:
    """Schmidt semi-normalised P̆ₙᵐ(sin φ') and dP̆ₙᵐ/dφ' (latitude derivative), ``[n, m]``.

    Gauss-normalised recursion with derivatives (NOAA ``MAG_PcupLow``), then the Schmidt
    factors S(n, 0) = S(n−1, 0)(2n − 1)/n and S(n, m) = S(n, m−1)√((n − m + 1)(δ_m1 + 1)/(n + m)).
    """
    x, z = sin_phi, cos_phi
    p = np.zeros((n_max + 1, n_max + 1))
    dp = np.zeros((n_max + 1, n_max + 1))
    p[0, 0] = 1.0
    for n in range(1, n_max + 1):
        for m in range(n + 1):
            if n == m:
                p[n, m] = z * p[n - 1, m - 1]
                dp[n, m] = z * dp[n - 1, m - 1] + x * p[n - 1, m - 1]
            elif n == 1 and m == 0:
                p[n, m] = x * p[n - 1, m]
                dp[n, m] = x * dp[n - 1, m] - z * p[n - 1, m]
            elif m > n - 2:
                p[n, m] = x * p[n - 1, m]
                dp[n, m] = x * dp[n - 1, m] - z * p[n - 1, m]
            else:
                k = ((n - 1) ** 2 - m * m) / ((2 * n - 1) * (2 * n - 3))
                p[n, m] = x * p[n - 1, m] - k * p[n - 2, m]
                dp[n, m] = x * dp[n - 1, m] - z * p[n - 1, m] - k * dp[n - 2, m]
    schmidt = np.zeros((n_max + 1, n_max + 1))
    schmidt[0, 0] = 1.0
    for n in range(1, n_max + 1):
        schmidt[n, 0] = schmidt[n - 1, 0] * (2 * n - 1) / n
        for m in range(1, n + 1):
            schmidt[n, m] = schmidt[n, m - 1] * math.sqrt((n - m + 1) * (2 if m == 1 else 1) / (n + m))
    # The recursion differentiates with respect to colatitude; flip the sign for latitude.
    return p * schmidt, -dp * schmidt


def _sum_field(
    g: FloatArray,
    h: FloatArray,
    n_max: int,
    ratio: float,
    lam: float,
    p: FloatArray,
    dp: FloatArray,
    cos_phi: float,
) -> tuple[float, float, float]:
    """(X', Y', Z') in the geocentric spherical frame for coefficients ``g``, ``h``."""
    bx = by = bz = 0.0
    m_idx = np.arange(n_max + 1)
    cos_ml = np.cos(m_idx * lam)
    sin_ml = np.sin(m_idx * lam)
    for n in range(1, n_max + 1):
        rp = ratio ** (n + 2)
        m = m_idx[: n + 1]
        gc = g[n, : n + 1] * cos_ml[: n + 1] + h[n, : n + 1] * sin_ml[: n + 1]
        gs = g[n, : n + 1] * sin_ml[: n + 1] - h[n, : n + 1] * cos_ml[: n + 1]
        bz -= rp * (n + 1) * float(np.dot(gc, p[n, : n + 1]))
        by += rp * float(np.dot(gs * m, p[n, : n + 1]))
        bx -= rp * float(np.dot(gc, dp[n, : n + 1]))
    return bx, by / cos_phi, bz


def wmm(
    lon: float,
    lat: float,
    altitude_km: float,
    year: float,
    model: WmmModel | None = None,
) -> MagneticElements:
    """Geomagnetic elements at a geodetic position and decimal year (e.g. 2026.5).

    ``lon`` and ``lat`` are degrees (east positive, geodetic latitude); ``altitude_km`` is the
    height above the WGS84 ellipsoid. ``model`` defaults to the shipped WMM2025.
    """
    mdl = model or load_wmm()
    phi = max(-90.0 + _POLE_TOLERANCE_DEG, min(90.0 - _POLE_TOLERANCE_DEG, float(lat)))
    phi_r = math.radians(phi)
    sin_lat, cos_lat = math.sin(phi_r), math.cos(phi_r)
    rc = WGS84_A_KM / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    xp = (rc + altitude_km) * cos_lat
    zp = (rc * (1.0 - WGS84_E2) + altitude_km) * sin_lat
    r = math.hypot(xp, zp)
    phig = math.asin(zp / r)
    sin_phig, cos_phig = math.sin(phig), math.cos(phig)
    lam = math.radians(lon)
    dt = float(year) - mdl.epoch
    g = mdl.g + dt * mdl.g_dot
    h = mdl.h + dt * mdl.h_dot
    p, dp = _legendre(mdl.n_max, sin_phig, cos_phig)
    ratio = GEOMAGNETIC_RADIUS_KM / r
    bx_s, by_s, bz_s = _sum_field(g, h, mdl.n_max, ratio, lam, p, dp, cos_phig)
    sx_s, sy_s, sz_s = _sum_field(mdl.g_dot, mdl.h_dot, mdl.n_max, ratio, lam, p, dp, cos_phig)
    psi = phig - phi_r
    cpsi, spsi = math.cos(psi), math.sin(psi)
    x = bx_s * cpsi - bz_s * spsi
    y = by_s
    z = bx_s * spsi + bz_s * cpsi
    xd = sx_s * cpsi - sz_s * spsi
    yd = sy_s
    zd = sx_s * spsi + sz_s * cpsi
    hz = math.hypot(x, y)
    f = math.hypot(hz, z)
    decl = math.degrees(math.atan2(y, x))
    incl = math.degrees(math.atan2(z, hz))
    h_dot = (x * xd + y * yd) / hz if hz > 0 else 0.0
    f_dot = (x * xd + y * yd + z * zd) / f if f > 0 else 0.0
    d_dot = math.degrees((x * yd - y * xd) / (hz * hz)) if hz > 0 else 0.0
    i_dot = math.degrees((hz * zd - z * h_dot) / (f * f)) if f > 0 else 0.0
    grid: float | None = None
    if phi >= 55.0:
        grid = decl - float(lon)
    elif phi <= -55.0:
        grid = decl + float(lon)
    if grid is not None:
        grid = ((grid + 180.0) % 360.0) - 180.0
    return MagneticElements(
        declination_deg=decl,
        inclination_deg=incl,
        horizontal_nt=hz,
        north_nt=x,
        east_nt=y,
        down_nt=z,
        total_nt=f,
        grid_variation_deg=grid,
        declination_dot_deg=d_dot,
        inclination_dot_deg=i_dot,
        horizontal_dot_nt=h_dot,
        total_dot_nt=f_dot,
        decimal_year=float(year),
        altitude_km=float(altitude_km),
        model=mdl.name,
        within_validity=mdl.valid_from <= float(year) < mdl.valid_until,
    )
