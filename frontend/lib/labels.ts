/**
 * Display names for API enum values and export formats, shared by every screen that shows them
 * (recent datasets, the workspace, the report), so the same thing is never called two names.
 */

import type {
  AircraftType,
  AnalysisExportFormat,
  AssemblyBand,
  AssemblyHit,
  BandCeiling,
  CrsSource,
  DaylightProfile,
  DaylightSegmentKind,
  DaylightSegmentStatus,
  Ed318Limits,
  EmiKind,
  FileExportFormat,
  FileType,
  ZoneProfileKey,
  CameraKind,
  DistanceFrom,
  HeightReference,
  ObstacleHeightSource,
  TakeoffSource,
  UnverifiedBand,
} from "./types";

/** "shapefile" → "Shapefile" (never the raw enum, never upper-cased "GEOJSON"). */
export const FILE_TYPE_LABEL: Readonly<Record<FileType, string>> = {
  shapefile: "Shapefile",
  kml: "KML",
  kmz: "KMZ",
  geojson: "GeoJSON",
  csv: "CSV",
};

/** Where the coordinate system came from, as a noun phrase: "From the .prj file". */
export const CRS_SOURCE_LABEL: Readonly<Record<CrsSource, string>> = {
  prj: "the .prj file",
  kml: "KML (always WGS 84)",
  geojson: "the GeoJSON file",
  csv: "the CSV track (WGS 84)",
  override: "the CRS override",
  assumed: "an assumption (WGS 84)",
};

export function fileTypeLabel(type: FileType | null | undefined): string | null {
  return type ? FILE_TYPE_LABEL[type] ?? type : null;
}

export function crsSourceLabel(source: CrsSource | null | undefined): string | null {
  return source ? CRS_SOURCE_LABEL[source] ?? source : null;
}

export interface ExportLabel {
  label: string;
  description: string;
}

/** An export menu entry's note while the menu fetches a file: `self` on that entry, `other` on the rest. */
export const EXPORT_BUSY_NOTE = { self: "Exporting…", other: "Another export is still running: wait for it" } as const;

/** Dataset exports (`GET /api/files/{id}/export/?format=`). */
export const FILE_EXPORT_LABEL: Readonly<Record<FileExportFormat, ExportLabel>> = {
  csv: { label: "Measurements (CSV)", description: "One row per feature, SI units" },
  geojson: { label: "Features (GeoJSON)", description: "WGS84 geometry with measurements" },
  kml: { label: "Features (KML)", description: "For Google Earth and GIS tools" },
  ed318: { label: "Zones (ED-318)", description: "Its polygons as an ED-318 zone file; one without a zone column is exported as red (prohibited)" },
};

/** Analysis exports (`GET /api/analyses/{id}/export/?format=`). */
export const ANALYSIS_EXPORT_LABEL: Readonly<Record<AnalysisExportFormat, ExportLabel>> = {
  json: { label: "Analysis (JSON)", description: "The full result, as the API returns it" },
  geojson: { label: "Analysis (GeoJSON)", description: "Every result layer in WGS84, grid cells as polygons" },
  kml: { label: "Analysis (KML)", description: "Evidence layers for Google Earth and GIS tools (no routes)" },
  csv: { label: "Analysis (CSV)", description: "One result table; choose which" },
  ed318: { label: "Zones (ED-318)", description: "Aerodrome bands and zones as an ED-318 zone file" },
};

/** Data-pack layer keys (airbrief_core/data/datapack.py LAYER_KEYS). */
export const DATAPACK_LAYER_LABEL: Readonly<Record<string, string>> = {
  dem: "Terrain (DEM)",
  population: "Population",
  aerodromes: "Aerodromes",
  obstacles: "Obstacles",
  pois: "Points of interest",
  water: "Water",
  zones: "Zones",
  power: "Power lines and substations",
  assemblies: "Outdoor assembly venues",
  aerodrome_perimeters: "Aerodrome boundaries",
  runways: "Runways",
};

export function datapackLayerLabel(key: string): string {
  const known = DATAPACK_LAYER_LABEL[key];
  if (known) return known;
  const words = key.replace(/[-_]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Daylight profiles, as offered in the environment form. */
export const DAYLIGHT_PROFILE_LABEL: Readonly<Record<DaylightProfile, string>> = {
  india_daylight: "Daylight only (sunrise to sunset)",
  faa_107: "FAA Part 107 (US)",
  custom: "Custom minimum sun elevation",
};

/** Short profile names for one-line summaries (History). */
export const DAYLIGHT_PROFILE_SHORT: Readonly<Record<DaylightProfile, string>> = {
  india_daylight: "daylight only",
  faa_107: "FAA Part 107",
  custom: "minimum sun elevation",
};

export const DAYLIGHT_SEGMENT_LABEL: Readonly<Record<DaylightSegmentKind, string>> = {
  night: "Night",
  civil_twilight: "Civil twilight",
  day: "Day",
};

/** How the chosen profile treats a segment (evidence wording, never a go/no-go). */
export const DAYLIGHT_STATUS_LABEL: Readonly<Record<DaylightSegmentStatus, string>> = {
  allowed: "Within the profile",
  lighting: "Lighting required",
  outside: "Outside the profile",
};

/** Power and communication features near an operation (interference-proximity evidence). */
export const EMI_KIND_LABEL: Readonly<Record<EmiKind, string>> = {
  power_line: "Power line",
  minor_line: "Minor power line",
  cable: "Power cable",
  substation: "Substation",
  plant: "Power plant",
  comm_tower: "Communication tower",
  mast: "Mast",
  pylon: "Pylon",
};

// SORA 2.5 operational volume (airbrief_core/models/ground_risk_volume.py)

/** Contingency-manoeuvre family of the aircraft. */
export const AIRCRAFT_TYPE_LABEL: Readonly<Record<AircraftType, string>> = {
  multirotor: "Multirotor",
  fixed_wing: "Fixed-wing",
};

/** Ground risk buffer rules. */
export const GRB_RULE_LABEL: Readonly<Record<"one_to_one" | "ballistic", string>> = {
  one_to_one: "1:1 rule",
  ballistic: "Ballistic",
};

/** Where an outdoor assembly came from. */
export const ASSEMBLY_SOURCE_LABEL: Readonly<Record<AssemblyHit["source"], string>> = {
  datapack: "Data pack",
  user: "Your entry",
};

/** Assembly venue kinds of the data pack (OSM tags) and user events. */
export const ASSEMBLY_KIND_LABEL: Readonly<Record<string, string>> = {
  stadium: "Stadium",
  sports_centre: "Sports centre",
  theme_park: "Theme park",
  marketplace: "Marketplace",
  event: "Event",
};

/** Size bands of SORA 2.5 Tables 8–13 (Step #8). */
export const ASSEMBLY_BAND_LABEL: Readonly<Record<AssemblyBand, string>> = {
  lt_40k: "< 40,000 people",
  "40k_400k": "40,000–400,000 people",
  gt_400k: "> 400,000 people",
  unknown: "Capacity unknown",
};

/** How the density grid cell size was chosen. */
export const GRID_SOURCE_LABEL: Readonly<Record<"auto" | "user" | "coarsened", string>> = {
  auto: "SORA 2.5 Table 4",
  user: "set by you",
  coarsened: "coarsened to the cell limit",
};

/** How a planned height is read (airbrief_core/analyses/heights.py), as a segmented-control label. */
export const HEIGHT_REFERENCE_LABEL: Readonly<Record<HeightReference, string>> = {
  agl: "AGL",
  takeoff: "Above take-off",
  amsl: "AMSL",
};

/** The same, as words after a value: "120 m above take-off". */
export const HEIGHT_REFERENCE_SUFFIX: Readonly<Record<HeightReference, string>> = {
  agl: "AGL",
  takeoff: "above take-off",
  amsl: "AMSL",
};

/** What each reference means, for hints and tooltips. */
export const HEIGHT_REFERENCE_HINT: Readonly<Record<HeightReference, string>> = {
  agl: "Above the ground directly below the aircraft (terrain following).",
  takeoff: "Above the take-off point, as DJI and ArduPilot show altitude: the ground below changes along the flight.",
  amsl: "Above mean sea level (the terrain model's geoid): one fixed level.",
};

/** Where the take-off point came from. */
export const TAKEOFF_SOURCE_LABEL: Readonly<Record<TakeoffSource, string>> = {
  given: "set by you",
  first_vertex: "first point of the route",
  centroid: "centre of the area",
  point: "the point itself",
  origin: "the corridor origin",
};

/** How an obstacle's height is known (OSM tag, building levels, or a per-kind assumption). */
export const OBSTACLE_HEIGHT_SOURCE_LABEL: Readonly<Record<ObstacleHeightSource, string>> = {
  tag: "Mapped height",
  levels: "From building levels",
  default: "Assumed height",
};

export const CAMERA_KIND_LABEL: Readonly<Record<CameraKind, string>> = {
  rgb: "RGB",
  thermal: "Thermal",
  multispectral: "Multispectral",
};

/** Airspace rule sets (`zone_profile`). */
export const ZONE_PROFILE_LABEL: Readonly<Record<ZoneProfileKey, string>> = {
  dgca_2021: "DGCA 2021",
  conservative_illustrative: "Conservative (illustrative)",
  custom: "Custom",
};

export const ZONE_PROFILE_HINT: Readonly<Record<ZoneProfileKey, string>> = {
  dgca_2021:
    "Drone airspace map rules (MoCA, Sept 2021), measured from the airport perimeter: 0–5 km verify on Digital Sky, 5–8 km yellow, 8–12 km green to 60 m and yellow above, elsewhere green to 120 m.",
  conservative_illustrative:
    "The rules before zone profiles: 0–8 km permission and 8–12 km up to 60 m, from the reference point minus an assumed radius, plus rings around small airports and heliports.",
  custom: "Your own aerodrome rules, as JSON (the format of zone_rules in the API). The brief flags custom rules.",
};

/** What an aerodrome band was measured from. */
export const DISTANCE_FROM_LABEL: Readonly<Record<DistanceFrom, string>> = {
  perimeter: "the mapped airport boundary",
  runway_hull: "the runways (no mapped boundary)",
  reference_point: "the reference point (assumed radius)",
};

// Assessment choices (Settings, docs/features/choices.md)

export const BAND_CEILINGS: readonly BandCeiling[] = ["exceedance", "permission"];
export const UNVERIFIED_BANDS: readonly UnverifiedBand[] = ["permission", "exclude"];
export const ED318_LAYOUTS: readonly Ed318Limits[] = ["geometry", "properties"];

export const BAND_CEILING_LABEL: Readonly<Record<BandCeiling, string>> = {
  exceedance: "An exceedance (fail)",
  permission: "Permission required (caution)",
};

export const BAND_CEILING_HINT: Readonly<Record<BandCeiling, string>> = {
  exceedance:
    "Flying above a band's ceiling, such as 60 m in the DGCA 8–12 km band, fails the audit as if the ceiling were your limit: pick this unless you fly there with Digital Sky permission.",
  permission:
    "Flying above it is reported as flight in yellow airspace, permission required, with the same segments and margins: pick this when you routinely get Digital Sky permission for yellow zones.",
};

export const UNVERIFIED_BAND_LABEL: Readonly<Record<UnverifiedBand, string>> = {
  permission: "Permission required",
  exclude: "Keep out, like a red zone",
};

export const UNVERIFIED_BAND_HINT: Readonly<Record<UnverifiedBand, string>> = {
  permission:
    "Sites within 5 km of an airport score 0 for airspace and corridors pay the highest cost to cross that band, both named in the results: pick this to still see airport sites.",
  exclude:
    "Sites within 5 km of an airport are excluded and corridors never cross that band, as for a red zone: pick this to keep eVTOL studies clear of airports.",
};

export const ED318_LIMITS_LABEL: Readonly<Record<Ed318Limits, string>> = {
  geometry: "In the geometry",
  properties: "In the properties",
};

export const ED318_LIMITS_HINT: Readonly<Record<Ed318Limits, string>> = {
  geometry:
    "Vertical limits go in each geometry's layer, as EUROCAE ED-318 and the U-space exchange files put them: pick this unless the tool reading the file wants them elsewhere.",
  properties:
    "Vertical limits go in each feature's properties, as some national zone feeds put them: pick this when the tool reading the file expects them there.",
};

/** The ED-318 entry of an export menu, naming the layout the file gets. */
export function ed318ExportLabel(base: ExportLabel, limits: Ed318Limits): ExportLabel {
  return { label: `Zones (ED-318, limits in ${limits})`, description: base.description };
}
