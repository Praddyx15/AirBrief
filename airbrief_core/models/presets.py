"""Reference presets: cameras, aircraft classes, illustrative airspace rules and the SORA-style table.

Everything here is *illustrative* and configurable. None of it is an official airspace map or an
official safety assessment. The README and every API response carry the matching disclaimers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import AerodromeZoneRule, InputModel, ZoneRing, ZoneRules
from .ground_risk_volume import VolumeDefaults

DIGITAL_SKY_URL = "https://digitalsky.dgca.gov.in"
"""DGCA Digital Sky: the authoritative Indian drone airspace map and permission portal."""


class CameraSpec(InputModel):
    """A frame camera. Sensor dimensions in millimetres, focal length in millimetres.

    ``kind`` says what it images (RGB, thermal or multispectral); ``images_per_capture`` counts the
    files one trigger writes (e.g. one per band of a multispectral camera) and ``photo_mb`` the
    size of one such file, for the storage estimate. ``reference`` cites the published spec.
    """

    name: str = Field(min_length=1, max_length=80)
    sensor_width_mm: float = Field(gt=0, le=100)
    sensor_height_mm: float = Field(gt=0, le=100)
    focal_length_mm: float = Field(gt=0, le=500)
    image_width_px: int = Field(gt=0, le=100_000)
    image_height_px: int = Field(gt=0, le=100_000)
    kind: Literal["rgb", "thermal", "multispectral"] = "rgb"
    images_per_capture: int = Field(default=1, ge=1, le=20)
    photo_mb: float | None = Field(default=None, gt=0, le=2000)
    reference: str | None = Field(default=None, max_length=300)


class UasPreset(InputModel):
    """An aircraft class used to pre-fill ground-risk parameters.

    ``volume`` holds the class's illustrative contingency-volume terms (GNSS, position and map
    errors, reaction time, manoeuvre angles, altimetry error) for the SORA 2.5 operational volume.
    """

    name: str
    max_dimension_m: float
    max_speed_ms: float
    takeoff_mass_kg: float
    volume: VolumeDefaults = Field(default_factory=VolumeDefaults)


PHOTO_SIZE_NOTE = "file size is a typical value (it varies with the scene and the format)"
"""How the presets' ``photo_mb`` should be read: an estimate, overridable per analysis."""

CAMERA_PRESETS: dict[str, CameraSpec] = {
    "p4rtk": CameraSpec(
        name="DJI Phantom 4 RTK (1-inch, 8.8 mm)",
        sensor_width_mm=13.2,
        sensor_height_mm=8.8,
        focal_length_mm=8.8,
        image_width_px=5472,
        image_height_px=3648,
        photo_mb=8.0,
        reference=f"DJI Phantom 4 RTK specs: 1-inch 20 MP CMOS, 8.8 mm lens, 5472 × 3648 JPEG; {PHOTO_SIZE_NOTE}.",
    ),
    "m3e": CameraSpec(
        name="DJI Mavic 3 Enterprise (4/3, 12.29 mm)",
        sensor_width_mm=17.3,
        sensor_height_mm=13.0,
        focal_length_mm=12.29,
        image_width_px=5280,
        image_height_px=3956,
        photo_mb=10.0,
        reference=f"DJI Mavic 3 Enterprise specs: 4/3 CMOS 20 MP wide camera, 5280 × 3956; {PHOTO_SIZE_NOTE}.",
    ),
    "apsc24": CameraSpec(
        name="Generic APS-C 24 MP (16 mm)",
        sensor_width_mm=23.5,
        sensor_height_mm=15.6,
        focal_length_mm=16.0,
        image_width_px=6000,
        image_height_px=4000,
        photo_mb=12.0,
        reference=f"Generic APS-C sensor (23.5 × 15.6 mm, 24 MP); {PHOTO_SIZE_NOTE}.",
    ),
    "ff42": CameraSpec(
        name="Generic full-frame 42 MP (35 mm)",
        sensor_width_mm=35.9,
        sensor_height_mm=24.0,
        focal_length_mm=35.0,
        image_width_px=7952,
        image_height_px=5304,
        photo_mb=20.0,
        reference=f"Generic full-frame sensor (35.9 × 24 mm, 42 MP); {PHOTO_SIZE_NOTE}.",
    ),
    # Thermal: an uncooled microbolometer's sensor size is its pixel pitch × resolution.
    "h20t_thermal": CameraSpec(
        name="DJI Zenmuse H20T thermal (640 × 512, 13.5 mm)",
        sensor_width_mm=7.68,  # 640 × 12 µm
        sensor_height_mm=6.144,  # 512 × 12 µm
        focal_length_mm=13.5,
        image_width_px=640,
        image_height_px=512,
        kind="thermal",
        photo_mb=1.5,
        reference=(
            "DJI Zenmuse H20T specs, thermal camera: uncooled VOx microbolometer, 640 × 512, "
            f"12 µm pixel pitch, 13.5 mm focal length (DFOV 40.6°); R-JPEG {PHOTO_SIZE_NOTE}."
        ),
    ),
    "m30t_thermal": CameraSpec(
        name="DJI M30T / Mavic 3T thermal (640 × 512, 9.1 mm)",
        sensor_width_mm=7.68,  # 640 × 12 µm
        sensor_height_mm=6.144,  # 512 × 12 µm
        focal_length_mm=9.1,
        image_width_px=640,
        image_height_px=512,
        kind="thermal",
        photo_mb=1.5,
        reference=(
            "DJI Matrice 30T specs, thermal camera: uncooled VOx microbolometer, 640 × 512, 12 µm "
            "pixel pitch, 9.1 mm focal length (40 mm equivalent; the Mavic 3T thermal camera has the "
            f"same resolution, pitch and 40 mm equivalent); R-JPEG {PHOTO_SIZE_NOTE}."
        ),
    ),
    "xt2_640_13": CameraSpec(
        name="DJI Zenmuse XT2 640 thermal (13 mm)",
        sensor_width_mm=10.88,  # 640 × 17 µm
        sensor_height_mm=8.704,  # 512 × 17 µm
        focal_length_mm=13.0,
        image_width_px=640,
        image_height_px=512,
        kind="thermal",
        photo_mb=1.5,
        reference=(
            "DJI Zenmuse XT2 specs, thermal camera: uncooled VOx microbolometer, 640 × 512, 17 µm "
            f"pixel pitch, 13 mm lens option; R-JPEG {PHOTO_SIZE_NOTE}."
        ),
    ),
    # Multispectral: the band camera; the pixel pitch follows from DJI's published GSD formula
    # (GSD = H/k cm/px means pitch/focal = 1/(100·k)), each capture writes one image per band.
    "p4m": CameraSpec(
        name="DJI P4 Multispectral band camera (5.74 mm)",
        sensor_width_mm=4.859,  # 1600 px × 3.037 µm
        sensor_height_mm=3.948,  # 1300 px × 3.037 µm
        focal_length_mm=5.74,
        image_width_px=1600,
        image_height_px=1300,
        kind="multispectral",
        images_per_capture=6,
        photo_mb=4.2,
        reference=(
            "DJI P4 Multispectral specs: six 1/2.9-inch CMOS (one RGB, five bands), 1600 × 1300, "
            "5.74 mm focal length, GSD (H/18.9) cm/px; one 16-bit TIFF per band ≈ 4.2 MB."
        ),
    ),
    "m3m": CameraSpec(
        name="DJI Mavic 3 Multispectral band camera (4.34 mm)",
        sensor_width_mm=5.208,  # 2592 px × 2.009 µm
        sensor_height_mm=3.906,  # 1944 px × 2.009 µm
        focal_length_mm=4.34,
        image_width_px=2592,
        image_height_px=1944,
        kind="multispectral",
        images_per_capture=5,
        photo_mb=10.0,
        reference=(
            "DJI Mavic 3 Multispectral specs: four 1/2.8-inch 5 MP band cameras (G, R, RE, NIR) and "
            "one RGB camera, 2592 × 1944, 25 mm equivalent, GSD (H/21.6) cm/px; one 16-bit TIFF per "
            f"band ≈ 10 MB, {PHOTO_SIZE_NOTE}."
        ),
    ),
}

UAS_PRESETS: dict[str, UasPreset] = {
    "sub250": UasPreset(
        name="Sub-250 g multirotor", max_dimension_m=0.25, max_speed_ms=16.0, takeoff_mass_kg=0.249,
        volume=VolumeDefaults(aircraft="multirotor", pitch_angle_deg=35.0),
    ),
    "mapping_quad": UasPreset(
        name="Mapping quadcopter (≈4 kg)", max_dimension_m=0.6, max_speed_ms=18.0, takeoff_mass_kg=4.0,
        volume=VolumeDefaults(aircraft="multirotor", pitch_angle_deg=35.0),
    ),
    "fixed_wing": UasPreset(
        name="Fixed-wing mapper (≈2 m span)", max_dimension_m=2.0, max_speed_ms=22.0, takeoff_mass_kg=6.0,
        volume=VolumeDefaults(aircraft="fixed_wing", bank_angle_deg=30.0),
    ),
    "heavy_lift": UasPreset(
        name="Heavy-lift multirotor (≈25 kg)", max_dimension_m=2.0, max_speed_ms=15.0, takeoff_mass_kg=25.0,
        volume=VolumeDefaults(aircraft="multirotor", pitch_angle_deg=25.0, reaction_time_s=1.5),
    ),
    "evtol": UasPreset(
        name="eVTOL air taxi (≈15 m)", max_dimension_m=15.0, max_speed_ms=70.0, takeoff_mass_kg=2000.0,
        volume=VolumeDefaults(aircraft="fixed_wing", bank_angle_deg=30.0, reaction_time_s=2.0),
    ),
}

CONSERVATIVE_ILLUSTRATIVE = ZoneRules(
    profile="conservative_illustrative",
    distance_from="reference_point",
    reference=(
        "Illustrative defaults modelled on publicly described Indian drone airspace-map rules "
        "(yellow zone to 12 km from an airport perimeter; 8–12 km band up to ~200 ft AGL without "
        "permission; green zones up to 400 ft AGL). Perimeters are approximated from the aerodrome "
        "reference point. The 3 km small-airport and 1 km heliport rings are conservative "
        "assumptions, not DGCA rules. Always verify on the official DGCA Digital Sky airspace map "
        f"({DIGITAL_SKY_URL})."
    ),
    default_zone="green",
    default_ceiling_agl_m=120.0,
    rules=[
        AerodromeZoneRule(
            aerodrome_types=["large_airport"],
            assumed_radius_km=2.0,
            rings=[
                ZoneRing(zone="yellow", inner_km=0.0, outer_km=8.0, ceiling_agl_m=None,
                         label="0–8 km: permission required — verify on Digital Sky"),
                ZoneRing(zone="yellow", inner_km=8.0, outer_km=12.0, ceiling_agl_m=60.0,
                         label="8–12 km: up to 60 m (200 ft) AGL without permission"),
            ],
        ),
        AerodromeZoneRule(
            aerodrome_types=["medium_airport"],
            assumed_radius_km=1.0,
            rings=[
                ZoneRing(zone="yellow", inner_km=0.0, outer_km=8.0, ceiling_agl_m=None,
                         label="0–8 km: permission required — verify on Digital Sky"),
                ZoneRing(zone="yellow", inner_km=8.0, outer_km=12.0, ceiling_agl_m=60.0,
                         label="8–12 km: up to 60 m (200 ft) AGL without permission"),
            ],
        ),
        AerodromeZoneRule(
            aerodrome_types=["small_airport"],
            assumed_radius_km=0.5,
            rings=[
                ZoneRing(zone="yellow", inner_km=0.0, outer_km=3.0, ceiling_agl_m=None,
                         label="0–3 km: small aerodrome, permission required — conservative assumption, not a DGCA rule"),
            ],
        ),
        AerodromeZoneRule(
            aerodrome_types=["heliport"],
            assumed_radius_km=0.1,
            rings=[
                ZoneRing(zone="yellow", inner_km=0.0, outer_km=1.0, ceiling_agl_m=None,
                         label="0–1 km: heliport, permission required — conservative assumption, not a DGCA rule"),
            ],
        ),
    ],
)
"""The rules used before zone profiles existed (reference point minus an assumed radius)."""

DEFAULT_ZONE_RULES = CONSERVATIVE_ILLUSTRATIVE
"""The conservative rules under their name from before zone profiles: every result stored without a
``zone_profile`` ran with them. New runs pick their rules with ``zone_profile`` (DGCA 2021 by default)."""

DGCA_2021_SOURCE = (
    "Drone Rules, 2021 (G.S.R. 589(E), 25 August 2021; the amendments of 2022, 2023 and 2024 change "
    "pilot certificates and forms, not the zones) and the drone airspace map the Ministry of Civil "
    "Aviation published under them on 24 September 2021 (PIB press release \"Ministry of Civil "
    "Aviation releases India's airspace map for drone operations\")"
)


def _dgca_bands() -> list[ZoneRing]:
    return [
        ZoneRing(zone="yellow", inner_km=0.0, outer_km=5.0, ceiling_agl_m=None, verify_official_map=True,
                 label="0–5 km: not defined by the published rule — verify on Digital Sky (permission)"),
        ZoneRing(zone="yellow", inner_km=5.0, outer_km=8.0, ceiling_agl_m=None,
                 label="5–8 km: yellow from the ground (permission required)"),
        ZoneRing(zone="green", inner_km=8.0, outer_km=12.0, ceiling_agl_m=60.0, ceiling_exceeded_zone="yellow",
                 label="8–12 km: green up to 60 m (200 ft) AGL, yellow above"),
    ]


DGCA_2021_ZONE_RULES = ZoneRules(
    profile="dgca_2021",
    distance_from="perimeter",
    reference=(
        f"{DGCA_2021_SOURCE}. Measured from the perimeter of an operational airport: 8–12 km is "
        "green up to 60 m (200 ft) above ground and yellow above (rule 2); 5–8 km is a yellow zone "
        "from the ground (the airspace map); everywhere else not designated red or yellow is green "
        "up to 120 m (400 ft) and yellow above (rule 2). Yellow and red zones need prior permission "
        "(rule 22): yellow from the air traffic control authority concerned (AAI, IAF, Navy, HAL), "
        "red from the Central Government. Neither text says what 0–5 km is (red or yellow depends "
        "on the airport), so that band is shown as 'verify on Digital Sky' and always needs "
        "permission. Not modelled, because they are not published as data: red zones, temporary red "
        "zones of up to 96 hours (rule 24) and the zones near international borders, which the map "
        "draws without a distance in the rules. The remote pilot must check Digital Sky before every "
        "flight (rule 21), and a change to the map takes effect seven days after it is made (rule 23). "
        "Assumptions, not published values: perimeters come from the OpenStreetMap "
        "aerodrome boundary (else the hull of the OurAirports runways, else the reference point "
        "minus an assumed radius); 'operational' means a large or medium airport, or a small one "
        "with scheduled service, unless set per aerodrome. Heliports have no band in the rule. Red "
        "zones are not published as open data. Always verify on the official DGCA Digital Sky "
        f"airspace map ({DIGITAL_SKY_URL})."
    ),
    default_zone="green",
    default_ceiling_agl_m=120.0,
    default_ceiling_exceeded_zone="yellow",
    rules=[
        AerodromeZoneRule(aerodrome_types=["large_airport"], assumed_radius_km=2.0, rings=_dgca_bands()),
        AerodromeZoneRule(aerodrome_types=["medium_airport"], assumed_radius_km=1.0, rings=_dgca_bands()),
        AerodromeZoneRule(aerodrome_types=["small_airport"], assumed_radius_km=0.5, rings=_dgca_bands(),
                          scheduled_service_only=True),
    ],
)
"""DGCA 2021 airspace-map bands measured from the aerodrome perimeter (see ``reference``)."""

ZoneProfile = Literal["dgca_2021", "conservative_illustrative", "custom"]

ZONE_RULE_PROFILES: dict[str, ZoneRules] = {
    "dgca_2021": DGCA_2021_ZONE_RULES,
    "conservative_illustrative": CONSERVATIVE_ILLUSTRATIVE,
}
"""Named rule sets for ``zone_profile``; ``custom`` means the analysis' own ``zone_rules``."""


def profile_rules(profile: str, custom: ZoneRules | None = None) -> ZoneRules:
    """The rules a run uses: its own ``zone_rules`` (profile ``custom``), else the named rule set."""
    return custom if custom is not None else ZONE_RULE_PROFILES[profile]

ZONE_PROFILE_LABELS: dict[str, str] = {
    "dgca_2021": "DGCA 2021 airspace map (bands from the airport perimeter)",
    "conservative_illustrative": "Conservative illustrative (reference point, before zone profiles)",
    "custom": "Custom rules",
}

RED_ZONES_NOTICE = (
    "Red zones are not published as open data, so none are shown unless a data pack or your own "
    f"layers carry them: verify on Digital Sky ({DIGITAL_SKY_URL})."
)
"""Carried by every airspace result (``assessment_banner``)."""

# SORA-style intrinsic ground risk class table (structure of JARUS SORA 2.5, Main Body, Table 2).
# Columns: (max characteristic dimension m, max speed m/s). Rows: population density upper bound
# (people/km²); ``0`` marks the "controlled ground area" row; ``None`` cells are outside SORA scope.
# Values are reproduced for illustration and are configurable — verify against the official text.
SORA_IGRC_COLUMNS: list[tuple[float, float]] = [(1.0, 25.0), (3.0, 35.0), (8.0, 75.0), (20.0, 120.0), (40.0, 200.0)]
SORA_IGRC_ROWS: list[tuple[str, float | None, list[int | None]]] = [
    # (label, density upper bound exclusive; None = unbounded, values per column)
    ("Controlled ground area", 0.0, [1, 1, 2, 3, 3]),
    ("< 5 people/km²", 5.0, [2, 3, 4, 5, 6]),
    ("< 50 people/km²", 50.0, [3, 4, 5, 6, 7]),
    ("< 500 people/km²", 500.0, [4, 5, 6, 7, 8]),
    ("< 5,000 people/km²", 5_000.0, [5, 6, 7, 8, 9]),
    ("< 50,000 people/km²", 50_000.0, [6, 7, 8, 9, 10]),
    ("≥ 50,000 people/km²", None, [7, 8, None, None, None]),
]
SORA_SMALL_UAS_MASS_KG = 0.25
SORA_SMALL_UAS_SPEED_MS = 25.0
SORA_TABLE_REFERENCE = (
    "Structure of JARUS SORA 2.5 Main Body, Table 2 (intrinsic ground risk class). Values are "
    "illustrative and configurable; verify against the official JARUS publication before use."
)

SORA_EVIDENCE_LABEL = "SORA Step 2 / Step 8 evidence — not a GRC, ARC or SAIL"
"""Shown with every operational-volume result: AirBrief never computes the final GRC, ARC or SAIL."""

_M_PER_FT = 0.3048
# Suggested optimal grid size of population-density maps by maximum height AGL (JARUS SORA 2.5
# Main Body, Table 4, which gives the heights in feet): (max height m, grid cell size m). Heights
# above the last row use the last size.
SORA_GRID_SIZES: list[tuple[float, float]] = [
    (500 * _M_PER_FT, 200.0),
    (1_000 * _M_PER_FT, 400.0),
    (2_500 * _M_PER_FT, 1_000.0),
    (5_000 * _M_PER_FT, 2_000.0),
    (10_000 * _M_PER_FT, 4_000.0),
    (20_000 * _M_PER_FT, 5_000.0),
    (60_000 * _M_PER_FT, 10_000.0),
]
SORA_GRID_REFERENCE = "JARUS SORA 2.5 Main Body, Table 4 (suggested grid size for population density maps)"

SORA_ADJACENT_TIME_S = 180.0
"""Step #8: the adjacent area extends the distance flown in 3 minutes at maximum speed…"""
SORA_ADJACENT_MIN_M = 5_000.0
"""…but at least 5 km…"""
SORA_ADJACENT_MAX_M = 35_000.0
"""…and at most 35 km (JARUS SORA 2.5 Main Body §4.8.3 (b)(i))."""
SORA_ASSEMBLY_RADIUS_M = 1_000.0
"""Step #8: outdoor assemblies within 1 km of the operational volume (§4.8.3 (b)(iii))."""
SORA_ASSEMBLY_BANDS: tuple[float, float] = (40_000.0, 400_000.0)
"""Assembly size limits of Tables 8–13: < 40k, 40k–400k, > 400k people."""

DISCLAIMER_AIRSPACE = (
    "Indicative only — not an official airspace map and not a substitute for DGCA Digital Sky "
    "or any other authority's authorisation."
)
DISCLAIMER_GROUND_RISK = (
    "Evidence for a SORA-style ground-risk assessment, not the assessment itself. Population data "
    "is modelled and may be outdated; the class shown is illustrative."
)
DISCLAIMER_EVTOL = (
    "Planning-level screening only. Scores, routes, noise and energy figures are simplified models."
)
