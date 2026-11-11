/**
 * The AirBrief data contract, mirrored from the Pydantic models in `airbrief_core/models/`
 * (common.py, features.py, presets.py, analyses.py) plus the API shapes in docs/SPEC.md §4.
 *
 * Rules for keeping this file in sync:
 * - Field names stay snake_case, exactly as the API sends them.
 * - Output models (results) list every field: Pydantic serialises defaults, so fields with a
 *   default are present (non-optional). `X | None` becomes `X | null`.
 * - Input models (`*Params`) mark fields with a default as optional (`?`), because the API
 *   fills them in. Required Python fields stay required.
 * - Coordinates are GeoJSON order `[lon, lat(, z)]` in WGS84. Units are SI.
 */

// GeoJSON, self-contained (no @types/geojson)

export type Position = number[];

export interface PointGeometry {
  type: "Point";
  coordinates: Position;
}
export interface MultiPointGeometry {
  type: "MultiPoint";
  coordinates: Position[];
}
export interface LineStringGeometry {
  type: "LineString";
  coordinates: Position[];
}
export interface MultiLineStringGeometry {
  type: "MultiLineString";
  coordinates: Position[][];
}
export interface PolygonGeometry {
  type: "Polygon";
  coordinates: Position[][];
}
export interface MultiPolygonGeometry {
  type: "MultiPolygon";
  coordinates: Position[][][];
}
export interface GeometryCollection {
  type: "GeometryCollection";
  geometries: Geometry[];
}

export type Geometry =
  | PointGeometry
  | MultiPointGeometry
  | LineStringGeometry
  | MultiLineStringGeometry
  | PolygonGeometry
  | MultiPolygonGeometry
  | GeometryCollection;

export type GeometryType = Geometry["type"];

export type GeoJsonProperties = Record<string, unknown>;

export interface Feature<G extends Geometry | null = Geometry | null, P = GeoJsonProperties> {
  type: "Feature";
  id?: string | number;
  geometry: G;
  properties: P;
  bbox?: number[];
}

export interface FeatureCollection<G extends Geometry | null = Geometry | null, P = GeoJsonProperties> {
  type: "FeatureCollection";
  features: Feature<G, P>[];
  bbox?: number[];
}

/** Python `GeoJSON = dict[str, Any]`: a Geometry, Feature or FeatureCollection. */
export type GeoJSON = Geometry | Feature | FeatureCollection;

// common.py

export const SCHEMA_VERSION = "1.0";

export type ZoneKind = "green" | "yellow" | "red";
export const ZONE_KINDS: readonly ZoneKind[] = ["green", "yellow", "red"];

export type FileStatus = "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED";
export const FILE_STATUSES: readonly FileStatus[] = ["PENDING", "PROCESSING", "COMPLETED", "FAILED"];

export type AnalysisStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
export const ANALYSIS_STATUSES: readonly AnalysisStatus[] = [
  "PENDING",
  "RUNNING",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
];

export type Severity = "info" | "warning" | "critical";
export type Verdict = "pass" | "caution" | "fail";

export interface Issue {
  code: string;
  message: string;
  severity: Severity;
  feature_index: number | null;
}

/** BBox model (object form). FileInfo uses the list form `[min_lon, min_lat, max_lon, max_lat]`. */
export interface BBox {
  min_lon: number;
  min_lat: number;
  max_lon: number;
  max_lat: number;
}

/** `[min_lon, min_lat, max_lon, max_lat]` in WGS84 degrees. */
export type BBoxList = [number, number, number, number];

export interface ZoneRing {
  zone: ZoneKind;
  inner_km: number;
  outer_km: number;
  ceiling_agl_m?: number | null;
  label: string;
  /** above the ceiling the band is this zone (e.g. green to 60 m, yellow above) */
  ceiling_exceeded_zone?: ZoneKind | null;
  /** a band the published rule does not define: permission, verify on the official map */
  verify_official_map?: boolean;
}

export interface AerodromeZoneRule {
  aerodrome_types: string[];
  assumed_radius_km?: number;
  rings: ZoneRing[];
  scheduled_service_only?: boolean;
}

export interface ZoneRules {
  rules: AerodromeZoneRule[];
  default_zone?: ZoneKind;
  default_ceiling_agl_m?: number;
  default_ceiling_exceeded_zone?: ZoneKind | null;
  reference?: string;
  distance_from?: "perimeter" | "reference_point";
  profile?: string;
}

export interface UserZone {
  name: string;
  zone: ZoneKind;
  geometry: GeoJSON;
  ceiling_agl_m?: number | null;
  note?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
  lower_limit_m?: number | null;
  upper_limit_m?: number | null;
  vertical_reference?: "AGL" | "AMSL" | "WGS84";
}

export interface ZoneHit {
  source: "aerodrome" | "user" | "datapack";
  name: string;
  ident: string | null;
  zone: ZoneKind;
  ring: string | null;
  ceiling_agl_m: number | null;
  permission_required: boolean;
  distance_km: number | null;
  overlap_area_m2: number | null;
  overlap_length_m: number | null;
  first_index: number | null;
  note: string | null;
  /** in the planned window (absent in older results); null: no window given */
  active?: boolean | null;
  valid_from?: string | null;
  valid_to?: string | null;
  /** ceiling (or the zone's lower limit) minus the planned height above ground; negative: above it */
  agl_margin_m?: number | null;
  basis?: ZoneBasis;
  distance_from?: DistanceFrom | null;
  inactive_reason?: string | null;
}

export interface NearestAerodrome {
  ident: string;
  name: string;
  type: string;
  lon: number;
  lat: number;
  distance_km: number;
  bearing_deg: number;
  elevation_m: number | null;
  /** whether the zone rules applied its bands; null: no rule for its type */
  operational?: boolean | null;
  scheduled_service?: boolean | null;
}

export interface ObstacleHit {
  id: string;
  kind: string;
  name: string | null;
  lon: number;
  lat: number;
  height_m: number;
  base_m: number | null;
  top_amsl_m: number | null;
  distance_m: number;
  clearance_m: number | null;
  conflict: boolean;
  /** "tag" (mapped height), "levels" (building levels × 3.2 m) or "default" (assumed; absent in older results) */
  height_source?: ObstacleHeightSource | null;
  /** true when no height was mapped and a per-kind default was assumed */
  height_assumed?: boolean;
}

export interface TerrainSummary {
  min_m: number;
  max_m: number;
  mean_m: number;
  relief_m: number;
  samples: number;
  source: string;
}

export interface ProfilePoint {
  distance_m: number;
  lon: number;
  lat: number;
  terrain_m: number | null;
  altitude_amsl_m: number | null;
  agl_m: number | null;
  limit_agl_m: number | null;
}

// features.py

export type FileType = "kml" | "kmz" | "shapefile" | "geojson" | "csv";
export type CrsSource = "prj" | "kml" | "geojson" | "csv" | "override" | "assumed";

export type MeasurementStatus = "measured" | "not_applicable" | "unsupported" | "skipped" | "error";
export const MEASUREMENT_STATUSES: readonly MeasurementStatus[] = [
  "measured",
  "not_applicable",
  "unsupported",
  "skipped",
  "error",
];

export interface Measurement {
  status: MeasurementStatus;
  reason: string | null;
  area_m2: number | null;
  perimeter_m: number | null;
  length_m: number | null;
  length_3d_m: number | null;
  geodesic_area_m2: number | null;
  geodesic_perimeter_m: number | null;
  geodesic_length_m: number | null;
  deviation_pct: number | null;
  grid_area_m2: number | null;
  grid_length_m: number | null;
  method: string | null;
  area_crs: string | null;
  length_crs: string | null;
  utm_epsg: number | null;
  utm_zone: string | null;
  repaired: boolean;
  has_z: boolean;
  parts: Record<string, number>;
}

export interface FeatureRecord {
  index: number;
  layer: string;
  fid: string | null;
  name: string | null;
  geometry_type: string;
  crs: string | null;
  geometry: GeoJSON | null;
  geometry_wgs84: Geometry | null;
  bbox_wgs84: BBox | null;
  properties: Record<string, unknown>;
  altitude_mode: string | null;
  times: number[] | null;
  time_origin_utc: string | null;
  measurement: Measurement;
}

export interface LayerInfo {
  name: string;
  feature_count: number;
  crs: string | null;
  geometry_types: Record<string, number>;
}

export interface MeasurementTotals {
  feature_count: number;
  measured: number;
  not_applicable: number;
  unsupported: number;
  skipped: number;
  error: number;
  area_m2: number;
  perimeter_m: number;
  length_m: number;
  length_3d_m: number;
}

export interface DatasetResult {
  schema_version: string;
  filename: string;
  file_type: FileType;
  crs: string | null;
  crs_source: CrsSource | null;
  feature_count: number;
  layers: LayerInfo[];
  features: FeatureRecord[];
  bbox_wgs84: BBox | null;
  totals: MeasurementTotals;
  issues: Issue[];
}

// presets.py

export interface CameraSpec {
  name: string;
  sensor_width_mm: number;
  sensor_height_mm: number;
  focal_length_mm: number;
  image_width_px: number;
  image_height_px: number;
  /** what the camera images (absent: RGB) */
  kind?: CameraKind;
  /** files one trigger writes (one per band of a multispectral camera) */
  images_per_capture?: number;
  /** size of one image file in MB, for the storage estimate */
  photo_mb?: number | null;
  /** where the published spec comes from */
  reference?: string | null;
}

export interface UasPreset {
  name: string;
  max_dimension_m: number;
  max_speed_ms: number;
  takeoff_mass_kg: number;
  /** contingency-volume terms of the class (absent from an API older than the SORA 2.5 volume) */
  volume?: VolumeDefaults;
}

/** Keys of `CAMERA_PRESETS` in presets.py (the API may add more; treat as open). */
export type CameraPresetId = "p4rtk" | "m3e" | "apsc24" | "ff42" | (string & {});
/** Keys of `UAS_PRESETS` in presets.py. */
export type UasPresetId = "sub250" | "mapping_quad" | "fixed_wing" | "heavy_lift" | "evtol" | (string & {});

// analyses.py

export type AnalysisKind =
  | "airspace"
  | "ground_risk"
  | "evtol_siting"
  | "evtol_corridor"
  | "show"
  | "survey"
  | "flight_audit"
  | "environment"
  | "line_of_sight";

export const ANALYSIS_KINDS: readonly AnalysisKind[] = [
  "airspace",
  "ground_risk",
  "evtol_siting",
  "evtol_corridor",
  "show",
  "survey",
  "flight_audit",
  "environment",
  "line_of_sight",
];

/** Result `layers`: named GeoJSON FeatureCollections in WGS84 (SPEC §3 lists the keys per module). */
export type ResultLayers = Record<string, FeatureCollection>;

export interface AirspaceParams {
  geometry: GeoJSON;
  altitude_agl_m?: number;
  zone_rules?: ZoneRules | null;
  user_zones?: UserZone[];
  include_datapack_zones?: boolean;
  grid?: boolean;
  h3_resolution?: number;
  max_grid_cells?: number;
  profile_spacing_m?: number;
  obstacle_search_m?: number;
  vertical_clearance_m?: number;
  /** how `altitude_agl_m` is read (default "agl") */
  height_reference?: HeightReference;
  /** [lon, lat]; default: the first vertex of a line, the centroid of an area */
  takeoff?: [number, number] | null;
  takeoff_elevation_m?: number | null;
  min_agl_m?: number;
  /** one result per feature (see BatchParams); `geometry` is then their extent */
  batch?: BatchParams | null;
  zone_profile?: ZoneProfileKey;
  planned_start_utc?: string | null;
  planned_end_utc?: string | null;
  aerodrome_operational?: Record<string, boolean>;
  zone_dataset_ids?: string[];
  zone_mappings?: Record<string, ZoneLayerMapping>;
}

export type FlyabilityStatus = "free" | "limited" | "permission" | "restricted";

export interface FlyabilityCell {
  h3: string;
  zone: ZoneKind;
  ceiling_agl_m: number | null;
  terrain_m: number | null;
  obstacle_max_height_m: number | null;
  status: FlyabilityStatus;
  /** height above ground at the cell centre (above take-off / AMSL plans only) */
  agl_m?: number | null;
}

export interface AirspaceResult {
  verdict: Verdict;
  summary: string;
  geometry_type: string;
  area_m2: number | null;
  length_m: number | null;
  altitude_agl_m: number;
  effective_ceiling_agl_m: number | null;
  permission_required: boolean;
  max_terrain_m: number | null;
  max_altitude_amsl_m: number | null;
  nearest_aerodromes: NearestAerodrome[];
  zones: ZoneHit[];
  terrain: TerrainSummary | null;
  profile: ProfilePoint[];
  obstacles: ObstacleHit[];
  grid: FlyabilityCell[];
  grid_resolution: number | null;
  grid_truncated: boolean;
  /**
   * false when aerodrome zones were not checked (no data pack covers the area, it has no aerodrome
   * layer, or the geometry is outside it): the verdict is then never "pass". Absent in results
   * stored before the flag (treat as true).
   */
  assessment_complete?: boolean;
  issues: Issue[];
  disclaimer: string;
  layers: ResultLayers;
  /** Terrain-true heights (absent in results stored before them: treat as "agl"). */
  height_reference?: HeightReference;
  min_agl_m?: number | null;
  takeoff?: TakeoffPoint | null;
  flight_level_amsl_m?: number | null;
  agl_min_m?: number | null;
  agl_max_m?: number | null;
  below_min_agl?: HeightSegment[];
  above_ceiling?: HeightSegment[];
  cells_below_min_agl?: number | null;
  cells_above_ceiling?: number | null;
  /** false when a take-off/AMSL plan could not be related to the ground (no DEM) */
  heights_assessed?: boolean;
  dem?: DemInfo | null;
  /** null or absent: stored before profiles (conservative rules) */
  zone_profile?: ZoneProfileKey | null;
  band_evidence?: BandEvidence[];
  inactive_zones?: ZoneHit[];
  assessment_banner?: string | null;
}

export interface GroundRiskParams {
  geometry: GeoJSON;
  max_dimension_m?: number;
  max_speed_ms?: number;
  takeoff_mass_kg?: number | null;
  altitude_agl_m?: number;
  buffer_rule?: "one_to_one" | "ballistic";
  density_statistic?: "max" | "p95";
  controlled_ground_area?: boolean;
  h3_resolution?: number;
  max_cells?: number;
  /** SORA 2.5 operational volume; absent = the earlier footprint method (H3 sampling) */
  volume?: OperationalVolumeParams | null;
  /** Step #8 adjacent-area density (default true) */
  adjacent_area?: boolean;
  /** user-entered outdoor assemblies and events (needs `volume`; at most 100) */
  assemblies?: AssemblyInput[];
  /** density grid cell size (m); null = SORA 2.5 Table 4 by the flight-geography height */
  grid_m?: number | null;
  batch?: BatchParams | null;
}

export interface DensityCell {
  h3: string;
  lon: number;
  lat: number;
  density_per_km2: number;
  band: string;
}

export interface GroundRiskResult {
  igrc: number | null;
  in_scope: boolean;
  small_uas_rule: boolean;
  column: string;
  density_band: string;
  density_statistic: string;
  max_density_per_km2: number;
  p95_density_per_km2: number;
  mean_density_per_km2: number;
  population_in_footprint: number;
  footprint_area_km2: number;
  buffer_m: number;
  hotspots: DensityCell[];
  cells: DensityCell[];
  cells_truncated: boolean;
  table_reference: string;
  method: string;
  disclaimer: string;
  issues: Issue[];
  layers: ResultLayers;
  // SORA 2.5 operational-volume evidence (absent in results of the footprint method)
  evidence_label?: string | null;
  volume?: OperationalVolumeResult | null;
  density_grid?: DensityGrid | null;
  igrc_table?: IgrcTable | null;
  adjacent_mean_density_per_km2?: number | null;
  adjacent_people?: number | null;
  adjacent_area_km2?: number | null;
  /** covered fraction (0..1) of the adjacent ring */
  adjacent_coverage?: number | null;
  assemblies?: AssemblyHit[];
}

// ground_risk_volume.py (SORA 2.5 operational volume)

export type AircraftType = "multirotor" | "fixed_wing";
export type AssemblyBand = "lt_40k" | "40k_400k" | "gt_400k" | "unknown";

/** Contingency-volume terms of an aircraft (metres, seconds, degrees). */
export interface VolumeDefaults {
  aircraft: AircraftType;
  gnss_error_m: number;
  position_error_m: number;
  map_error_m: number;
  reaction_time_s: number;
  bank_angle_deg: number;
  pitch_angle_deg: number;
  altimetry_error_m: number;
  climb_angle_deg: number;
}

export interface OperationalVolumeParams extends Partial<VolumeDefaults> {
  /** flight-geography height; null = the analysis' `altitude_agl_m` */
  fg_height_m?: number | null;
}

/** A user-entered outdoor assembly or event. */
export interface AssemblyInput {
  name: string;
  /** Point, MultiPoint, Polygon or MultiPolygon */
  geometry: Geometry;
  expected_people?: number | null;
  start_utc?: string | null;
  end_utc?: string | null;
  note?: string | null;
}

export interface VolumeTerm {
  /** symbol, e.g. "S_GNSS", "H_CM", "S_GRB", "D_adj" */
  name: string;
  label: string;
  axis: "horizontal" | "vertical" | "buffer" | "adjacent";
  value_m: number;
  /** the formula with its numbers */
  formula: string;
  source: string;
}

export interface OperationalVolumeResult {
  aircraft: AircraftType;
  speed_ms: number;
  fg_height_m: number;
  s_cv_m: number;
  h_cv_m: number;
  grb_m: number;
  grb_rule: "one_to_one" | "ballistic";
  adjacent_outer_km: number;
  /** v·180 s before the 5–35 km clamp */
  adjacent_distance_km: number;
  grid_m: number;
  grid_source: "auto" | "user" | "coarsened";
  terms: VolumeTerm[];
  reference: string;
}

export interface GridDensityCell {
  id: string;
  ix: number;
  iy: number;
  lon: number;
  lat: number;
  density_per_km2: number;
  band: string;
}

/** Square density grid over FG + CV + GRB (cells of `grid_m` in a local frame). */
export interface DensityGrid {
  grid_m: number;
  origin_lon: number;
  origin_lat: number;
  samples_per_cell: number;
  cells: GridDensityCell[];
  hotspots: GridDensityCell[];
  nodata_cells: number;
  coarsened: boolean;
}

export interface AssemblyHit {
  name: string;
  source: "datapack" | "user";
  kind: string | null;
  capacity: number | null;
  band: AssemblyBand;
  band_label: string;
  /** from the outer limit of the operational volume; 0 inside it */
  distance_m: number;
  lon: number;
  lat: number;
  start_utc: string | null;
  end_utc: string | null;
  note: string | null;
}

export interface IgrcTable {
  reference: string;
  columns: { dimension_m: number; speed_ms: number; label: string }[];
  rows: { label: string; values: (number | null)[] }[];
  row_index: number;
  /** null: the aircraft is beyond the last column */
  column_index: number | null;
}

export interface SitingWeights {
  demand: number;
  access: number;
  airspace: number;
  obstacles: number;
  slope: number;
}

export type SitingCriterion = keyof SitingWeights;

export interface EvtolSitingParams {
  geometry: GeoJSON;
  h3_resolution?: number;
  weights?: Partial<SitingWeights>;
  top_n?: number;
  min_separation_km?: number;
  demand_radius_km?: number;
  access_radius_km?: number;
  obstacle_radius_m?: number;
  max_slope_deg?: number;
  exclude_water?: boolean;
  exclude_red_zones?: boolean;
  zone_rules?: ZoneRules | null;
  max_cells?: number;
  /** absent in runs stored before profiles (conservative rules) */
  zone_profile?: ZoneProfileKey;
  /** absent in runs stored before the choice ("permission") */
  unverified_band?: UnverifiedBand;
}

export interface SiteCandidate {
  rank: number;
  h3: string;
  lon: number;
  lat: number;
  /** 0..100 */
  score: number;
  /** criterion -> 0..1 */
  breakdown: Record<string, number>;
  elevation_m: number | null;
  slope_deg: number | null;
  population_nearby: number | null;
  nearest_poi: string | null;
  nearest_poi_km: number | null;
  zone: ZoneKind;
  notes: string[];
  /** "verify_official_map": in a band the published rule does not define (it may be red) */
  zone_basis?: ZoneBasis;
}

export interface SitingCell {
  h3: string;
  /** 0..100, null when excluded */
  score: number | null;
  excluded: boolean;
  reason: string | null;
}

export interface EvtolSitingResult {
  candidates: SiteCandidate[];
  cells: SitingCell[];
  cells_evaluated: number;
  cells_excluded: number;
  cells_truncated: boolean;
  weights: SitingWeights;
  /** criterion -> human description */
  criteria: Record<string, string>;
  disclaimer: string;
  issues: Issue[];
  layers: ResultLayers;
  /** null or absent: stored before profiles (conservative rules) */
  zone_profile?: ZoneProfileKey | null;
  /** null or absent: stored before the choice ("permission") */
  unverified_band?: UnverifiedBand | null;
}

export interface CorridorWeights {
  ground_risk: number;
  airspace: number;
  obstacles: number;
}

export interface EvtolCorridorParams {
  /** [lon, lat] */
  origin: [number, number];
  /** [lon, lat] */
  destination: [number, number];
  origin_name?: string | null;
  destination_name?: string | null;
  study_area?: GeoJSON | null;
  study_margin_km?: number;
  h3_resolution?: number;
  corridor_width_m?: number;
  cruise_altitude_agl_m?: number;
  obstacle_clearance_m?: number;
  cruise_speed_kmh?: number;
  energy_wh_per_km?: number;
  weights?: Partial<CorridorWeights>;
  avoid_red_zones?: boolean;
  zone_rules?: ZoneRules | null;
  noise_level_db_at_ref?: number;
  noise_ref_distance_m?: number;
  noise_threshold_db?: number;
  profile_spacing_m?: number;
  max_cells?: number;
  /** "agl" (default): the level clears the highest ground by the cruise height; "amsl": a fixed level */
  cruise_reference?: CruiseReference;
  cruise_altitude_amsl_m?: number | null;
  origin_elevation_m?: number | null;
  min_agl_m?: number;
  ceiling_agl_m?: number | null;
  zone_profile?: ZoneProfileKey;
  unverified_band?: UnverifiedBand;
}

export interface NoiseCell {
  h3: string;
  level_db: number;
  population: number;
}

export interface NoiseSummary {
  threshold_db: number;
  footprint_half_width_m: number;
  exposed_population: number;
  exposed_area_km2: number;
  cells: NoiseCell[];
}

export interface EvtolCorridorResult {
  /** LineString geometry */
  route: GeoJSON;
  /** Polygon geometry */
  corridor: GeoJSON;
  length_km: number;
  straight_km: number;
  detour_pct: number;
  flight_time_min: number;
  energy_kwh: number;
  cruise_altitude_agl_m: number;
  required_cruise_amsl_m: number | null;
  max_terrain_m: number | null;
  profile: ProfilePoint[];
  obstacles: ObstacleHit[];
  zones: ZoneHit[];
  noise: NoiseSummary;
  max_density_per_km2: number | null;
  mean_density_per_km2: number | null;
  cells_considered: number;
  disclaimer: string;
  issues: Issue[];
  layers: ResultLayers;
  /** Terrain-true heights along the route (absent in older results). */
  cruise_reference?: CruiseReference;
  cruise_altitude_amsl_m?: number | null;
  min_agl_m?: number | null;
  ceiling_agl_m?: number | null;
  takeoff?: TakeoffPoint | null;
  agl_min_m?: number | null;
  agl_max_m?: number | null;
  below_min_agl?: HeightSegment[];
  above_ceiling?: HeightSegment[];
  heights_assessed?: boolean;
  dem?: DemInfo | null;
  zone_profile?: ZoneProfileKey | null;
  unverified_band?: UnverifiedBand | null;
}

export interface TrackInput {
  id: string;
  times: number[];
  /** [lon, lat, alt_m] aligned with `times` */
  coordinates: number[][];
}

export interface ShowParams {
  tracks: TrackInput[];
  /** [lon, lat, ground altitude AMSL]; [lon, lat]: absolute tracks are measured from the lowest take-off position */
  origin?: [number, number, number] | [number, number] | null;
  altitude_reference?: "relative" | "absolute";
  min_separation_m?: number;
  max_horizontal_speed_ms?: number;
  max_climb_rate_ms?: number;
  max_descent_rate_ms?: number;
  max_acceleration_ms2?: number;
  sample_rate_hz?: number;
  dynamics_window_s?: number;
  airborne_threshold_m?: number;
  ceiling_m?: number;
  show_box?: GeoJSON | null;
  audience?: GeoJSON | null;
  audience_buffer_m?: number;
  playback_rate_hz?: number;
  max_events?: number;
  // Site and pad checks (their types are in the tracks.py block below)
  /** margin around the tracks for a show box derived from them (no "Show box" polygon) */
  show_box_margin_m?: number;
  audience_buffer_rule?: ShowAudienceRule;
  /** safety factor added to the audience distance, in % */
  audience_breach_margin_pct?: number;
  min_pad_spacing_m?: number;
  /** per-drone airborne time limit (s); null = not checked */
  endurance_s?: number | null;
  /** no horizontal motion below this height (m); null = not checked */
  min_navigation_altitude_m?: number | null;
  low_altitude_speed_tolerance_ms?: number;
}

export interface ShowConflict {
  drone_a: string;
  drone_b: string;
  start_s: number;
  end_s: number;
  t_min_s: number;
  min_distance_m: number;
  /** midpoint of the pair at closest approach, [e, n, u] metres */
  position_enu: number[];
}

export type ShowViolationKind =
  | "speed_h"
  | "climb"
  | "descent"
  | "acceleration"
  | "geofence"
  | "audience"
  | "ceiling"
  | "endurance"
  | "low_altitude_motion";

export interface ShowViolation {
  kind: ShowViolationKind;
  drone: string;
  start_s: number;
  end_s: number;
  peak_value: number;
  limit: number;
}

export interface DroneStats {
  drone: string;
  airborne_s: number;
  distance_m: number;
  max_altitude_m: number;
  max_horizontal_speed_ms: number;
  max_climb_rate_ms: number;
  max_descent_rate_ms: number;
  max_acceleration_ms2: number;
}

/** Downsampled positions for playback, in metres east/north/up from `origin`. */
export interface ShowFrames {
  /** [lon, lat, alt] */
  origin: number[];
  times: number[];
  drone_ids: string[];
  /** [frame][drone] -> [e, n, u] */
  positions: number[][][];
}

export interface ShowSiteChecks {
  show_box_area_m2: number | null;
  audience_area_m2: number | null;
  box_audience_distance_m: number | null;
  box_overlaps_audience_buffer: boolean;
  /** "derived": built from the tracks' extent + margin (absent in results saved before it) */
  show_box_source?: "given" | "derived" | null;
  show_box_margin_m?: number | null;
  audience_buffer?: ShowAudienceBuffer | null;
}

export type ComputeBackend = "cpp" | "python";

export interface ShowResult {
  verdict: Verdict;
  drone_count: number;
  duration_s: number;
  sample_rate_hz: number;
  frames_checked: number;
  min_separation_m: number | null;
  min_separation_at_s: number | null;
  min_separation_pair: string[] | null;
  conflicts: ShowConflict[];
  violations: ShowViolation[];
  events_truncated: boolean;
  drones: DroneStats[];
  site: ShowSiteChecks;
  frames: ShowFrames;
  backend: ComputeBackend;
  issues: Issue[];
  layers: ResultLayers;
  // Echoed inputs and pad checks (absent in results saved before them)
  altitude_reference?: "relative" | "absolute";
  origin_source?: "given" | "inferred" | null;
  /** greatest height above the origin, any drone */
  max_altitude_m?: number | null;
  pads?: ShowPadSpacing | null;
}

export interface SurveyParams {
  geometry: GeoJSON;
  /** preset id (see Meta.cameras) or a full camera spec */
  camera?: CameraPresetId | CameraSpec;
  /** give either altitude_agl_m or target_gsd_cm, not both */
  altitude_agl_m?: number | null;
  target_gsd_cm?: number | null;
  front_overlap_pct?: number;
  side_overlap_pct?: number;
  speed_ms?: number;
  battery_minutes?: number;
  battery_swap_minutes?: number;
  turn_seconds?: number;
  min_trigger_interval_s?: number;
  max_altitude_agl_m?: number;
  direction_deg?: number | null;
  orientation?: "landscape" | "portrait";
  /** how `altitude_agl_m` is read (default "agl": terrain following) */
  height_reference?: HeightReference;
  /** [lon, lat]; default: the area centroid / corridor start */
  takeoff?: [number, number] | null;
  takeoff_elevation_m?: number | null;
  coverage?: SurveyCoverage;
  /** full width mapped along the centreline (coverage "corridor") */
  corridor_width_m?: number | null;
  battery_reserve_pct?: number;
  transit_legs?: boolean;
  transit_speed_ms?: number | null;
  gcp_min?: number;
  gcp_ha_per_extra?: number;
  gcp_corridor_spacing_m?: number;
  photo_mb?: number | null;
  mode?: SurveyMode;
  spray?: SprayParams | null;
  batch?: BatchParams | null;
}

export interface SurveyBlock {
  index: number;
  area_ha: number;
  line_km: number;
  minutes: number;
  photos: number;
  polygon: GeoJSON;
  transit_in_m?: number | null;
  transit_out_m?: number | null;
  terrain_min_m?: number | null;
  terrain_max_m?: number | null;
  agl_min_m?: number | null;
  agl_max_m?: number | null;
  gsd_min_cm?: number | null;
  gsd_max_cm?: number | null;
  /** spraying: the block's share of the mix */
  litres?: number | null;
}

export interface SurveyResult {
  /** null in spraying mode (no photos are taken) */
  camera: CameraSpec | null;
  gsd_cm: number | null;
  gsd_along_cm: number | null;
  altitude_agl_m: number;
  /** the height limit checked (absent in results saved before it was reported) */
  max_altitude_agl_m?: number | null;
  footprint_across_m: number | null;
  footprint_along_m: number | null;
  line_spacing_m: number;
  trigger_distance_m: number | null;
  trigger_interval_s: number | null;
  direction_deg: number;
  line_count: number;
  segment_count: number;
  line_km: number;
  photos: number;
  flight_minutes: number;
  batteries: number;
  total_minutes: number;
  area_ha: number;
  ha_per_hour: number;
  blocks: SurveyBlock[];
  issues: Issue[];
  layers: ResultLayers;
  /** Survey upgrades (absent in older results: mapping over an area, above ground). */
  mode?: SurveyMode;
  coverage?: SurveyCoverage;
  corridor_width_m?: number | null;
  centreline_km?: number | null;
  height_reference?: HeightReference;
  takeoff?: TakeoffPoint | null;
  flight_level_amsl_m?: number | null;
  heights_assessed?: boolean;
  dem?: DemInfo | null;
  terrain_min_m?: number | null;
  terrain_max_m?: number | null;
  relief_m?: number | null;
  agl_min_m?: number | null;
  agl_max_m?: number | null;
  gsd_min_cm?: number | null;
  gsd_max_cm?: number | null;
  worst_side_overlap_pct?: number | null;
  worst_front_overlap_pct?: number | null;
  heights_note?: string | null;
  battery_reserve_pct?: number;
  usable_battery_minutes?: number | null;
  transit_km?: number | null;
  transit_minutes?: number | null;
  gcp?: GcpEstimate | null;
  storage?: StorageEstimate | null;
  spray?: SprayResult | null;
  method?: string | null;
}

export interface FlightAuditParams {
  /**
   * FlightMD report JSON. When creating via the API, `report_url` may be sent instead. With a
   * dataset and neither, the API resolves a timed `track` (and a training box) from the dataset.
   */
  report?: Record<string, unknown> | null;
  /** a plain position track (filled by the API from the dataset) */
  track?: AuditTrackInput | null;
  /** how a track's altitudes are read; "auto" follows its altitude mode */
  track_heights?: TrackHeights;
  /** polygon the flight should stay inside; null switches the check off */
  training_box?: GeoJSON | null;
  training_box_name?: string | null;
  /** VLOS guidance distance from take-off (m); null = not checked */
  max_distance_from_home_m?: number | null;
  ceiling_agl_m?: number;
  calibration?: "takeoff" | "none";
  use_terrain?: boolean;
  zone_rules?: ZoneRules | null;
  user_zones?: UserZone[];
  include_datapack_zones?: boolean;
  footprint_buffer_m?: number;
  flightmd_url?: string | null;
  /** absent in audits stored before profiles (conservative rules) */
  zone_profile?: ZoneProfileKey;
  /** absent in audits stored before the choice ("exceedance") */
  band_ceiling?: BandCeiling;
}

export interface FlightInfo {
  report_id: string | null;
  file_name: string | null;
  schema_version: string | null;
  location_name: string | null;
  log_start_utc: string | null;
  overall_score: number | null;
  letter_grade: string | null;
  points: number;
  flightmd_url: string | null;
  // Position tracks (absent in FlightMD audits saved before them)
  source?: "flightmd" | "track";
  track_name?: string | null;
  track_format?: TrackFormat | null;
  altitude_mode?: string | null;
  feature_index?: number | null;
  duration_s?: number | null;
  // FlightMD schema 1.6/1.7 (absent in audits saved before AirBrief read them)
  armed_windows?: [number, number][] | null;
  parse_warnings?: string[];
  /** the AirBrief brief the flight was planned with (brief provenance.brief_sha256) */
  planning_brief_sha256?: string | null;
  log_sha256?: string | null;
  source_format?: string | null;
  airframe_name?: string | null;
  /** the report had a time per GPS point, so findings could be placed on the track */
  gps_times?: boolean;
}

export type FindingSeverity = "critical" | "warning" | "info" | "good";
export type FindingPlacement = "placed" | "no_time" | "no_track_times" | "outside_track";

/** A FlightMD finding with its log-relative time window, placed on the track when possible. */
export interface AuditFinding {
  id: string;
  title: string;
  severity: FindingSeverity;
  category: string | null;
  kind: string | null;
  params: string[];
  recommendation: string | null;
  t_start_s: number | null;
  t_end_s: number | null;
  /** null: the report has no armed windows, or the finding no time */
  armed: boolean | null;
  placement: FindingPlacement;
  lon: number | null;
  lat: number | null;
  distance_m: number | null;
  agl_m: number | null;
  limit_agl_m: number | null;
  zone: ZoneKind | null;
  zone_name: string | null;
}

export interface AuditExceedance {
  start_index: number;
  end_index: number;
  start_distance_m: number;
  end_distance_m: number;
  length_m: number;
  max_agl_m: number;
  limit_m: number;
  reason: string;
  /** tracks with times only */
  start_time_s?: number | null;
  end_time_s?: number | null;
  duration_s?: number | null;
}

export type AltitudeReference = "terrain_calibrated" | "terrain_raw" | "takeoff_relative" | "recorded_agl";

/** `agl_band` property on flight-audit `path` segments. */
export type AglBand = "ok" | "near" | "over";

export interface FlightAuditResult {
  verdict: Verdict;
  flight: FlightInfo;
  altitude_reference: AltitudeReference;
  calibration_offset_m: number | null;
  distance_m: number;
  max_agl_m: number;
  max_height_above_takeoff_m: number;
  ceiling_agl_m: number;
  profile: ProfilePoint[];
  exceedances: AuditExceedance[];
  zones: ZoneHit[];
  nearest_aerodrome: NearestAerodrome | null;
  min_aerodrome_distance_km: number | null;
  overflown_population: number | null;
  max_density_per_km2: number | null;
  footprint_area_km2: number | null;
  disclaimer: string;
  issues: Issue[];
  layers: ResultLayers;
  // Coverage and the optional track checks (absent in results saved before them)
  /** false when no data pack (or one without aerodromes) covers every point: zones "not assessed" */
  assessment_complete?: boolean;
  /** false for a 2D position track (no altitudes): heights and the height limit were not checked */
  heights_assessed?: boolean;
  duration_s?: number | null;
  time_above_limit_s?: number | null;
  max_home_distance_m?: number | null;
  training_box_name?: string | null;
  /** null: the training-area check was not asked for */
  area_exits?: AuditSegment[] | null;
  time_outside_area_s?: number | null;
  /** the distance limit checked */
  max_distance_from_home_m?: number | null;
  distance_exceedances?: AuditSegment[] | null;
  time_beyond_distance_s?: number | null;
  /** the terrain model the heights were read from; null when no terrain was used */
  dem?: DemInfo | null;
  /** null or absent: stored before profiles (conservative rules) */
  zone_profile?: ZoneProfileKey | null;
  /** the aerodrome bands the track's fixes fall in, with the highest height flown in each */
  band_evidence?: BandEvidence[];
  /** null or absent: stored before the choice ("exceedance") */
  band_ceiling?: BandCeiling | null;
  /** band_ceiling "permission": the runs flown in the yellow zone above a band's ceiling (`limit_m`) */
  above_band_ceiling?: AuditExceedance[] | null;
  /** FlightMD findings (schema 1.6+); absent in audits saved before */
  findings?: AuditFinding[];
}

// tracks.py: position-track audit and drone-show site checks

export type TrackFormat = "kml" | "gpx" | "csv" | "other";
export type TrackHeights = "auto" | "amsl" | "above_ground" | "above_takeoff";
export type ShowAudienceRule = "fixed" | "one_to_one" | "ballistic";

export interface AuditTrackInput {
  name?: string | null;
  /** [lon, lat] or [lon, lat, alt_m] in flight order */
  coordinates: number[][];
  /** seconds, aligned with `coordinates` */
  times?: number[] | null;
  time_origin_utc?: string | null;
  altitude_mode?: string | null;
  source_format?: TrackFormat;
  feature_index?: number | null;
}

/** A run of track points outside the training area ("area_exit") or beyond the distance from take-off. */
export interface AuditSegment {
  kind: "area_exit" | "distance";
  start_index: number;
  end_index: number;
  start_distance_m: number;
  end_distance_m: number;
  length_m: number;
  start_time_s: number | null;
  end_time_s: number | null;
  duration_s: number | null;
  /** farthest outside the area, or farthest from take-off */
  peak_m: number;
  limit_m: number;
}

export interface ShowAudienceBuffer {
  rule: ShowAudienceRule;
  base_distance_m: number;
  breach_margin_pct: number;
  /** base × (1 + margin %) */
  distance_m: number;
  height_m: number | null;
  speed_ms: number | null;
  formula: string;
}

export interface ShowPadConflict {
  phase: "takeoff" | "landing";
  drone_a: string;
  drone_b: string;
  distance_m: number;
  limit_m: number;
  time_s: number;
}

export interface ShowPadSpacing {
  limit_m: number;
  takeoff_pads: number;
  landing_pads: number;
  takeoff_min_spacing_m: number | null;
  takeoff_pair: string[] | null;
  landing_min_spacing_m: number | null;
  landing_pair: string[] | null;
  conflicts: ShowPadConflict[];
  conflicts_truncated: boolean;
}

// environment.py

/** `india_daylight`: sunrise→sunset only; `faa_107`: civil twilight needs lighting, night flagged; `custom`: minimum sun elevation. */
export type DaylightProfile = "india_daylight" | "faa_107" | "custom";
export type DaylightSegmentKind = "night" | "civil_twilight" | "day";
/** How the daylight profile treats a segment: allowed, allowed with anti-collision lighting, or outside it. */
export type DaylightSegmentStatus = "allowed" | "lighting" | "outside";
export type EmiKind = "power_line" | "minor_line" | "cable" | "substation" | "plant" | "comm_tower" | "mast" | "pylon";

export interface EnvironmentParams {
  geometry: GeoJSON;
  /** local date, YYYY-MM-DD */
  date: string;
  /** local wall-clock HH:MM (both or neither; an end before the start is on the next day) */
  window_start?: string | null;
  window_end?: string | null;
  /** minutes east of UTC, for display only (every computation is in UTC) */
  utc_offset_min?: number;
  daylight_profile?: DaylightProfile;
  /** custom profile only */
  min_sun_elevation_deg?: number;
  height_agl_m?: number;
  surface_wind_ms?: number | null;
  gust_ms?: number | null;
  reference_height_m?: number;
  shear_exponent?: number;
  wind_limit_ms?: number | null;
  include_emi?: boolean;
  emi_radius_m?: number;
  sun_min_deg?: number;
  sun_max_deg?: number;
  batch?: BatchParams | null;
}

/** An instant: UTC ISO time, local HH:MM and its day relative to `date` (−1, 0, +1). */
export interface SunEvent {
  utc: string;
  local: string;
  day_offset: number;
  azimuth_deg: number | null;
  elevation_deg: number | null;
}

export interface SunTrackPoint {
  t: string;
  /** minutes since local midnight of `date` */
  minute: number;
  /** apparent elevation (with refraction) */
  elevation_deg: number;
  azimuth_deg: number;
}

export interface SunSummary {
  sunrise: SunEvent | null;
  sunset: SunEvent | null;
  civil_dawn: SunEvent | null;
  civil_dusk: SunEvent | null;
  solar_noon: SunEvent;
  polar_day: boolean;
  polar_night: boolean;
  max_elevation_deg: number;
  daylight_minutes: number;
  /** 24 h at 5 min steps */
  track: SunTrackPoint[];
}

export interface TimeSegment {
  start: string;
  end: string;
  start_local: string;
  end_local: string;
  /** minutes since local midnight of `date` (may exceed 1440 on the next day) */
  start_minute: number;
  end_minute: number;
  minutes: number;
}

export interface DaylightSegment extends TimeSegment {
  kind: DaylightSegmentKind;
  status: DaylightSegmentStatus;
}

export interface PlannedWindow extends TimeSegment {
  crosses_midnight: boolean;
}

export interface DaylightCheck {
  profile: DaylightProfile;
  profile_label: string;
  rule: string;
  min_elevation_deg: number | null;
  window: PlannedWindow | null;
  minutes_outside: number | null;
  minutes_lighting: number | null;
  /** the local day split into night / civil twilight / day */
  segments: DaylightSegment[];
  window_segments: DaylightSegment[];
  statement: string;
}

export interface MagneticSummary {
  declination_deg: number;
  inclination_deg: number;
  horizontal_nt: number;
  total_nt: number;
  north_nt: number;
  east_nt: number;
  down_nt: number;
  declination_change_deg_per_year: number;
  grid_variation_deg: number | null;
  model: string;
  model_epoch: number;
  valid_until: string;
  within_validity: boolean;
  /** height above the WGS84 ellipsoid used */
  altitude_m: number;
  decimal_year: number;
  blackout: boolean;
  caution: boolean;
}

export interface WindAtHeight {
  surface_wind_ms: number;
  gust_ms: number | null;
  reference_height_m: number;
  height_agl_m: number;
  shear_exponent: number;
  factor: number;
  wind_at_height_ms: number;
  gust_at_height_ms: number | null;
  wind_limit_ms: number | null;
  /** limit − estimate (negative: the estimate is above the rated limit) */
  margin_ms: number | null;
  gust_margin_ms: number | null;
  statement: string;
}

export interface EmiItem {
  id: string;
  kind: EmiKind;
  name: string | null;
  voltage_kv: number | null;
  height_m: number | null;
  distance_m: number;
  /** point of the feature nearest to the analysed geometry */
  lon: number;
  lat: number;
  inside: boolean;
  source: "power" | "obstacles";
}

export interface SunWindow {
  min_deg: number;
  max_deg: number;
  segments: TimeSegment[];
  total_minutes: number;
  reached: boolean;
}

export interface EnvironmentResult {
  /** [lon, lat] where sun, magnetic field and wind are evaluated */
  point: [number, number];
  geometry_type: string;
  date: string;
  utc_offset_min: number;
  /** e.g. "UTC+05:30" */
  utc_offset_label: string;
  height_agl_m: number;
  sun: SunSummary;
  daylight: DaylightCheck;
  magnetic: MagneticSummary;
  wind: WindAtHeight | null;
  emi: EmiItem[];
  emi_assessed: boolean;
  emi_radius_m: number | null;
  emi_truncated: boolean;
  sun_window: SunWindow;
  facts: string[];
  issues: Issue[];
  method: string;
  disclaimer: string;
  /** input, point, emi, emi_search */
  layers: ResultLayers;
}

// line_of_sight.py

export type ObserverRole = "pilot" | "observer" | "gcs";
export type LosAircraft = "multirotor" | "fixed_wing";

export interface ObserverInput {
  name: string;
  role?: ObserverRole;
  lon: number;
  lat: number;
  /** eye or antenna height above the ground; null/absent = 1.7 m (pilot, observer) or 2.0 m (gcs) */
  height_agl_m?: number | null;
}

export interface RadioTargetInput {
  name?: string;
  lon: number;
  lat: number;
  /** antenna height above the ground; null/absent = the target height of the aircraft */
  height_agl_m?: number | null;
}

export interface LineOfSightParams {
  geometry: Geometry;
  /** at most 10; absent = named point features of the dataset (pilot / observer / GCS) */
  observers?: ObserverInput[];
  target_height_agl_m?: number;
  grid_m?: number;
  max_range_m?: number | null;
  /** effective Earth-radius factor for pilots and observers (default 7/6, GDAL 0.85714) */
  k_visible?: number;
  /** effective Earth-radius factor for ground stations and radio paths (default 4/3) */
  k_radio?: number;
  frequency_mhz?: number;
  fresnel_clearance_pct?: number;
  radio_target?: RadioTargetInput | null;
  radio_check?: boolean;
  include_obstacles?: boolean;
  vlos_aircraft?: LosAircraft;
  characteristic_dimension_m?: number | null;
  ground_visibility_m?: number;
  batch?: BatchParams | null;
}

export interface LosObserver {
  name: string;
  role: ObserverRole;
  lon: number;
  lat: number;
  height_agl_m: number;
  ground_m: number | null;
  eye_amsl_m: number | null;
  k_factor: number;
  los_kind: "visual" | "radio";
  visible_pct: number | null;
  visible_area_m2: number | null;
  max_visible_m: number | null;
  /** placed by the analysis (no observer given), not by the user */
  assumed: boolean;
}

export interface LosHiddenPatch {
  area_m2: number;
  cells: number;
  lon: number;
  lat: number;
}

export interface LosRouteSegment {
  start_m: number;
  end_m: number;
  length_m: number;
  visible: boolean;
}

export interface LosRouteSummary {
  length_m: number;
  visible_length_m: number;
  visible_pct: number;
  max_continuous_visible_m: number;
  hidden_segments: LosRouteSegment[];
  hidden_segments_truncated: boolean;
}

/** One sample of a radio path profile; heights in metres above sea level. */
export interface LosProfileSample {
  d_m: number;
  terrain_m: number | null;
  obstacle_m: number | null;
  los_m: number;
  fresnel_lower_m: number;
  fresnel_radius_m: number;
  clearance_pct: number | null;
}

export interface LosBelowThreshold {
  start_m: number;
  end_m: number;
  min_clearance_pct: number;
}

export interface LosRadioPath {
  from_name: string;
  to_name: string;
  from_lon: number;
  from_lat: number;
  to_lon: number;
  to_lat: number;
  from_height_agl_m: number;
  to_height_agl_m: number;
  from_amsl_m: number;
  to_amsl_m: number;
  distance_m: number;
  slant_m: number;
  fspl_db: number;
  worst_clearance_pct: number | null;
  worst_at_m: number | null;
  max_fresnel_radius_m: number;
  los_blocked: boolean;
  below_threshold: boolean;
  segments_below: LosBelowThreshold[];
  obstacles_on_path: number;
  nodata_samples: number;
  profile: LosProfileSample[];
}

export interface LosRadioSummary {
  frequency_mhz: number;
  k_factor: number;
  clearance_threshold_pct: number;
  station_name: string;
  station_role: ObserverRole;
  paths: LosRadioPath[];
  paths_below_threshold: number;
  paths_blocked: number;
  worst_clearance_pct: number | null;
  max_fspl_db: number | null;
  note: string;
}

export interface VlosGuidance {
  guidance_m: number;
  basis: "easa_alos_dlos" | "uk_caa_500m";
  aircraft: LosAircraft;
  characteristic_dimension_m: number | null;
  alos_m: number | null;
  dlos_m: number | null;
  ground_visibility_m: number;
  uk_caa_m: number;
  /** horizontal radius of the map ring: √(guidance² − target height²) */
  ring_radius_m: number;
  reference: string;
}

export interface LosGridInfo {
  grid_m: number;
  requested_grid_m: number;
  cols: number;
  rows: number;
  coarsened: boolean;
  area_cells: number;
  nodata_cells: number;
  dem_source: string;
  dem_resolution_m: number | null;
  obstacles_used: number;
}

export interface LineOfSightResult {
  geometry_type: string;
  target_height_agl_m: number;
  observers: LosObserver[];
  combined_visible_pct: number | null;
  visible_area_m2: number | null;
  hidden_area_m2: number | null;
  /** share of the area in radio line of sight of the station (ground station, else the pilot) */
  radio_los_pct: number | null;
  largest_hidden_patch: LosHiddenPatch | null;
  route: LosRouteSummary | null;
  beyond_guidance_pct: number | null;
  grid: LosGridInfo;
  radio: LosRadioSummary | null;
  vlos_guidance_m: number;
  vlos_guidance: VlosGuidance;
  method: string;
  disclaimer: string;
  issues: Issue[];
  /** input, observers, visible, hidden, vlos_guidance, radio_paths, hidden_patch?, route? */
  layers: ResultLayers;
  /** the terrain model of the viewshed (missing in results stored before it was added) */
  dem?: DemInfo | null;
}

// heights.py and survey.py

/** How a planned height is read: above ground, above the take-off point, or above sea level. */
export type HeightReference = "agl" | "takeoff" | "amsl";
/** eVTOL corridor cruise: a height above the ground or a fixed level above sea level. */
export type CruiseReference = "agl" | "amsl";
export type TakeoffSource = "given" | "first_vertex" | "centroid" | "point" | "origin";
export type ObstacleHeightSource = "tag" | "levels" | "default";
export type CameraKind = "rgb" | "thermal" | "multispectral";
export type SurveyMode = "mapping" | "spraying";
export type SurveyCoverage = "area" | "corridor";

/** The take-off point and its ground elevation (m AMSL) used for heights above take-off. */
export interface TakeoffPoint {
  lon: number;
  lat: number;
  elevation_m: number | null;
  source: TakeoffSource;
  elevation_source: "override" | "dem" | null;
}

/** A stretch of a route below the minimum AGL or above the ceiling (distances from the start). */
export interface HeightSegment {
  start_m: number;
  end_m: number;
  length_m: number;
  /** lowest AGL of a below-minimum stretch, highest of an above-ceiling one */
  peak_agl_m: number;
  limit_m: number;
}

/** The data-pack DEM, described from its manifest (honest terrain labels). */
export interface DemInfo {
  source: string;
  title: string;
  /** true: heights include roofs and canopy; null: unknown */
  surface_model: boolean | null;
  synthetic: boolean;
  vertical_datum: string | null;
  resolution_m: number | null;
  caveat: string;
}

/** Spraying inputs (effort only; the lines stay a visual aid). */
export interface SprayParams {
  swath_m?: number;
  rate_l_per_ha?: number;
  tank_l?: number;
  /** the most the pump can deliver */
  flow_l_per_min?: number;
  speed_ms?: number;
  turn_seconds?: number;
  refill_minutes?: number;
  /** above the crop */
  height_m?: number;
}

export interface SprayResult {
  swath_m: number;
  rate_l_per_ha: number;
  tank_l: number;
  height_m: number;
  requested_speed_ms: number;
  speed_ms: number;
  /** pump output needed at the speed used */
  flow_l_per_min: number;
  max_flow_l_per_min: number;
  flow_limited: boolean;
  litres: number;
  tank_loads: number;
  sorties: number;
  refills: number;
  spray_minutes: number;
  theoretical_ha_per_hour: number;
  effective_ha_per_hour: number;
  field_efficiency_pct: number;
  method: string;
}

export interface GcpEstimate {
  count: number;
  basis: "area" | "corridor";
  rule: string;
  reference: string;
}

export interface StorageEstimate {
  images: number;
  images_per_capture: number;
  mb_per_image: number;
  total_gb: number;
  source: "params" | "camera";
}

export interface AnalysisParamsByKind {
  airspace: AirspaceParams;
  ground_risk: GroundRiskParams;
  evtol_siting: EvtolSitingParams;
  evtol_corridor: EvtolCorridorParams;
  show: ShowParams;
  survey: SurveyParams;
  flight_audit: FlightAuditParams;
  environment: EnvironmentParams;
  line_of_sight: LineOfSightParams;
}

export interface AnalysisResultByKind {
  airspace: AirspaceResult;
  ground_risk: GroundRiskResult;
  evtol_siting: EvtolSitingResult;
  evtol_corridor: EvtolCorridorResult;
  show: ShowResult;
  survey: SurveyResult;
  flight_audit: FlightAuditResult;
  environment: EnvironmentResult;
  line_of_sight: LineOfSightResult;
}

export type AnalysisResult = AnalysisResultByKind[AnalysisKind];

/** Stable wrapper around any analysis result (export `format=json`). */
export interface AnalysisEnvelope<K extends AnalysisKind = AnalysisKind> {
  schema_version: string;
  airbrief_version: string;
  kind: K;
  params: Record<string, unknown>;
  result: AnalysisResultByKind[K];
  datapack: string | null;
  datapack_synthetic: boolean;
  backend: ComputeBackend;
  started_utc: string;
  finished_utc: string;
  duration_ms: number;
  /** "Evidence, not authorisation…" (absent in exports made before it) */
  notice?: string;
  /** set when the analysis read a synthetic data pack */
  watermark?: string | null;
}

// API shapes (docs/SPEC.md §4)

/** Error object inside `{"error": {...}}` and on FileInfo / AnalysisInfo. */
export interface ApiErrorInfo {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

/** Every API error response body. */
export interface ApiErrorBody {
  error: ApiErrorInfo;
}

export interface Page<T> {
  items: T[];
  total: number;
}

export interface Health {
  status: string;
  version: string;
  schema_version: string;
  db: string;
  datapacks: string[];
  native_backend: ComputeBackend;
  /** Refusals per error code, only when asked from the server itself. */
  refusals?: Record<string, number> | null;
}

export interface FileInfo {
  id: string;
  filename: string;
  file_type: FileType | null;
  size_bytes: number;
  status: FileStatus;
  feature_count: number;
  layer_count: number;
  crs: string | null;
  crs_source: CrsSource | null;
  bbox: BBoxList | null;
  layers: LayerInfo[];
  totals: MeasurementTotals | null;
  issues: Issue[];
  error: ApiErrorInfo | null;
  created_at: string;
  processed_at: string | null;
  processing_ms: number | null;
  /** sample name when created from a sample, otherwise null */
  sample: string | null;
  /** only in the creation response: keep it to delete/cancel later */
  owner_token?: string | null;
}

export type FilesPage = Page<FileInfo>;

export interface MeasurementItem {
  feature_index: number;
  layer: string;
  name: string | null;
  geometry_type: string;
  crs: string | null;
  measurement: Measurement;
}

export interface MeasurementsPage {
  file_id: string;
  items: MeasurementItem[];
  total: number;
  limit: number;
  offset: number;
  totals: MeasurementTotals;
}

export interface MeasurementsQuery {
  limit?: number;
  offset?: number;
  geometry_type?: string;
  status?: MeasurementStatus;
}

/** Properties added by `GET /api/files/{id}/features/` on top of the source properties. */
export interface FeatureSystemProperties {
  _index: number;
  _layer: string;
  _name: string | null;
  _type: string;
  _status: MeasurementStatus;
  _area_m2: number | null;
  /** absent in static demo exports made before it was added */
  _perimeter_m?: number | null;
  _length_m: number | null;
  /** timed tracks only: number of timed positions and seconds from first to last */
  _time_points?: number;
  _duration_s?: number;
}

export type DatasetFeatureProperties = GeoJsonProperties & FeatureSystemProperties;

/** `GET /api/files/{id}/features/`: WGS84; features with unknown CRS have `geometry: null`. */
export type DatasetFeatureCollection = FeatureCollection<Geometry | null, DatasetFeatureProperties>;

export interface SampleInfo {
  name: string;
  title: string;
  description: string;
  filename: string;
  /** suggested module kind or module id for this sample (e.g. "show", "survey") */
  module: string | null;
  /** demo mode only: id of the pre-built dataset in demo/files/ */
  dataset_id?: string;
  /** demo mode only: path of the downloadable file under demo/ (e.g. "samples/flightmd_report_v15.json") */
  download?: string | null;
}

export interface AnalysisRequest<K extends AnalysisKind = AnalysisKind> {
  kind: K;
  dataset_id?: string | null;
  feature_indices?: number[] | null;
  /**
   * Partial params: the API resolves missing inputs (geometry, tracks, origin/destination,
   * report from `report_url`) from the dataset (SPEC §4 "Resolving analysis inputs").
   */
  params: Partial<AnalysisParamsByKind[K]> & { report_url?: string };
}

export interface AnalysisInfo<K extends AnalysisKind = AnalysisKind> {
  id: string;
  kind: K;
  status: AnalysisStatus;
  /** 0..1 */
  progress: number;
  stage: string | null;
  dataset_id: string | null;
  /**
   * Run with the dataset's owner token (always false without a dataset). Missing in demo files
   * exported before the flag existed: treat that as the owner's.
   */
  by_dataset_owner?: boolean;
  feature_indices: number[] | null;
  params: Record<string, unknown>;
  result: AnalysisResultByKind[K] | null;
  error: ApiErrorInfo | null;
  datapack: string | null;
  datapack_synthetic: boolean;
  backend: ComputeBackend | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  /** only in the creation response: keep it to delete/cancel later */
  owner_token?: string | null;
}

export type AnalysesPage = Page<AnalysisInfo>;

export type JsonSchema = Record<string, unknown>;

export interface ModuleMeta {
  kind: AnalysisKind;
  title: string;
  description: string;
  /** data-pack layers this module needs */
  requires: string[];
  /** data-pack layers used when present */
  optional: string[];
  params_schema: JsonSchema;
}

export interface DatapackInfo {
  name: string;
  /** [min_lon, min_lat, max_lon, max_lat] */
  bbox: BBoxList;
  synthetic: boolean;
  layers: string[];
  /** files checked against the manifest's SHA-256 values; "mismatch": analyses refuse the pack */
  integrity?: DatapackIntegrity;
  content_sha256?: string | null;
}

export type DatapackIntegrity = "verified" | "unverified" | "mismatch";

/** `GET /api/meta/` → `signing`: the key that signs brief bundles (null: bundles are unsigned). */
export interface SigningMeta {
  algorithm: "Ed25519";
  /** first 16 hex characters of the SHA-256 of the raw public key */
  key_id: string;
  /** raw 32-byte public key, base64 */
  public_key: string;
}

export interface MetaLimits {
  max_upload_mb: number;
  [key: string]: number;
}

export interface Meta {
  version: string;
  schema_version: string;
  modules: ModuleMeta[];
  cameras: Record<string, CameraSpec>;
  uas_presets: Record<string, UasPreset>;
  zone_rules: ZoneRules;
  /** named airspace rule sets for `zone_profile` (absent in the static demo) */
  zone_profiles?: Record<string, ZoneRules>;
  default_zone_profile?: ZoneProfileKey;
  datapacks: DatapackInfo[];
  limits: MetaLimits;
  flightmd: { configured: boolean; url: string | null };
  /** false on public instances: list only the ids this browser created (`?ids=`) */
  global_listing?: boolean;
  /** true when DELETE/cancel need the item's owner token (only its creator's browser has it) */
  owner_tokens_required?: boolean;
  /** "development": an open server meant for this machine or a LAN (missing in the static demo) */
  app_env?: "development" | "production";
  signing?: SigningMeta | null;
}

export type FileExportFormat = "csv" | "geojson" | "kml" | "ed318";
export type AnalysisExportFormat = "geojson" | "json" | "kml" | "csv" | "ed318";

// api/routers/context.py: context layers, places, geometry parse

/** Context layers of `GET /api/context/` (airbrief_core/data/context.py `CONTEXT_LAYERS`). */
export type ContextLayerKey = "aerodromes" | "rings" | "zones" | "obstacles" | "water" | "coverage";
export const CONTEXT_LAYER_KEYS: readonly ContextLayerKey[] = ["aerodromes", "rings", "zones", "obstacles", "water", "coverage"];
/** Largest view (degrees each way) one context request may cover. */
export const MAX_CONTEXT_SPAN_DEG = 2;
/** Rule sets the context rings can be drawn with (`profile`): custom rules exist only in an analysis. */
export type ContextZoneProfile = Exclude<ZoneProfileKey, "custom">;

/** `GET /api/context/?bbox=&layers=&limit=` */
export interface ContextResponse {
  /** null when no data pack covers the view (every layer is then empty) */
  datapack: string | null;
  synthetic: boolean;
  bbox: BBoxList;
  layers: Partial<Record<ContextLayerKey, FeatureCollection>>;
  /** true when the layer was cut at `limit` features */
  truncated: Partial<Record<ContextLayerKey, boolean>>;
  /** features that matched before the cut */
  counts: Partial<Record<ContextLayerKey, number>>;
  limit: number;
  attribution: string[];
}

/** One `GET /api/places/?q=` match. */
export interface PlaceResult {
  label: string;
  kind: "coordinates" | "aerodrome";
  ident: string | null;
  lon: number;
  lat: number;
  /** e.g. "Large airport · Bengaluru · IATA BLR" or "Coordinates, read as latitude, longitude" */
  detail: string;
  datapack: string | null;
}

/** What typed coordinates become (`auto`: 1 point, 2 a line, ≥ 3 closed an area). */
export type GeometryShape = "auto" | "point" | "line" | "polygon";

/** `POST /api/geometry/parse/` */
export interface GeometryParseRequest {
  text: string;
  shape?: GeometryShape;
}

export interface GeometryParseResponse {
  geometry: Geometry;
  /** GeoJSON geometry type */
  kind: string;
  source: "geojson" | "wkt" | "coordinates";
  vertices: number;
  issues: Issue[];
}

export function isAnalysisKind(value: unknown): value is AnalysisKind {
  return typeof value === "string" && (ANALYSIS_KINDS as readonly string[]).includes(value);
}

export function isFileActive(status: FileStatus | undefined | null): boolean {
  return status === "PENDING" || status === "PROCESSING";
}

export function isAnalysisActive(status: AnalysisStatus | undefined | null): boolean {
  return status === "PENDING" || status === "RUNNING";
}

/** Narrow an AnalysisInfo to a specific kind (also narrows `result`). */
export function isAnalysisOfKind<K extends AnalysisKind>(
  info: AnalysisInfo | null | undefined,
  kind: K,
): info is AnalysisInfo<K> {
  return info != null && info.kind === kind;
}

/** The typed result of an analysis of `kind`, or null when not completed / another kind. */
export function analysisResult<K extends AnalysisKind>(
  info: AnalysisInfo | null | undefined,
  kind: K,
): AnalysisResultByKind[K] | null {
  if (!isAnalysisOfKind(info, kind) || info.status !== "COMPLETED") return null;
  return info.result;
}

// brief.py, api/routers/briefs.py and the export tables of api/routers/analyses.py

/** One CSV table of an analysis export (`GET /api/analyses/{id}/export/tables/`). */
export interface ExportTableInfo {
  /** the `?table=` value */
  key: string;
  label: string;
  rows: number;
  /** several tables in one file, each row naming its table in a leading `table` column */
  multi: boolean;
  /** the table `format=csv` gives without `table` */
  default: boolean;
}

export type BriefSectionKey =
  | "operation"
  | "airspace"
  | "heights"
  | "obstacles"
  | "ground_risk"
  | "environment"
  | "line_of_sight"
  | "survey"
  | "flight_audit"
  | "show"
  | "evtol";

export const BRIEF_SECTION_ORDER: readonly BriefSectionKey[] = [
  "operation",
  "airspace",
  "heights",
  "obstacles",
  "ground_risk",
  "environment",
  "line_of_sight",
  "survey",
  "flight_audit",
  "show",
  "evtol",
];

export type BriefFactTone = "neutral" | "ok" | "warn" | "danger" | "info";

/** One labelled value. `text` is metric (narrow no-break space before the unit); show it with `localizeUnits`. */
export interface BriefFact {
  label: string;
  text: string;
  value: number | string | boolean | null;
  /** SI unit of `value` */
  unit: string | null;
  note: string | null;
  tone: BriefFactTone;
  tone_label: string | null;
}

export interface BriefTable {
  caption: string;
  columns: string[];
  numeric: boolean[];
  rows: string[][];
  /** rows before the brief's cut (the bundle CSV holds them all) */
  total: number;
}

export interface BriefPart {
  analysis_id: string;
  kind: AnalysisKind | "operation";
  title: string;
  status: "assessed" | "partial";
  verdict: Verdict | null;
  summary: string | null;
  /** prominent caveats ("Airspace not assessed: …", "Heights not assessed: …") */
  banners: string[];
  facts: BriefFact[];
  tables: BriefTable[];
  notes: Issue[];
  disclaimer: string | null;
}

export interface BriefSection {
  key: BriefSectionKey;
  title: string;
  status: "info" | "assessed" | "partial" | "not_assessed";
  verdict: Verdict | null;
  /** why the section is not (fully) assessed */
  reason: string | null;
  parts: BriefPart[];
}

export interface AircraftInfo {
  /** a `Meta.uas_presets` key */
  preset?: string | null;
  name?: string | null;
  takeoff_mass_kg?: number | null;
  max_dimension_m?: number | null;
  max_speed_ms?: number | null;
}

/** What the operator tells the brief (`POST /api/briefs/` `operation`); every field optional. */
export interface OperationMeta {
  operator?: string | null;
  reference?: string | null;
  aircraft?: AircraftInfo | null;
  /** [lon, lat] */
  pilot_position?: [number, number] | null;
  /** ISO 8601 UTC */
  planned_start_utc?: string | null;
  planned_end_utc?: string | null;
  /** minutes east of UTC, labels the local times */
  utc_offset_min?: number;
  notes?: string | null;
}

export interface AircraftSummary {
  preset: string | null;
  name: string | null;
  takeoff_mass_kg: number | null;
  max_dimension_m: number | null;
  max_speed_ms: number | null;
  source: "preset" | "custom" | "preset_with_overrides" | null;
}

export interface OperationSummary {
  operator: string | null;
  reference: string | null;
  aircraft: AircraftSummary | null;
  pilot_position: number[] | null;
  planned_start_utc: string | null;
  planned_end_utc: string | null;
  utc_offset_min: number;
  utc_offset_label: string;
  notes: string | null;
}

export interface PackLayerProvenance {
  key: string;
  file: string | null;
  source: string;
  licence: string;
  used_by: AnalysisKind[];
  sha256?: string | null;
  /** the server that delivered the layer (for OSM layers, the Overpass mirror that answered) */
  source_url?: string | null;
}

export interface PackProvenance {
  name: string;
  installed: boolean;
  version: string | null;
  created_utc: string | null;
  synthetic: boolean;
  layers: PackLayerProvenance[];
  attribution: string[];
  /** SHA-256 over the layer hashes; null for a pack built before packs carried hashes */
  content_sha256?: string | null;
}

export interface BriefDatasetRef {
  id: string;
  filename: string;
  sha256: string | null;
  size_bytes: number | null;
  sample: string | null;
}

export interface BriefAnalysisProvenance {
  analysis_id: string;
  kind: AnalysisKind;
  title: string;
  schema_version: string;
  airbrief_version: string;
  datapack: string | null;
  datapack_synthetic: boolean;
  /** content_sha256 of the pack when the analysis ran (null: no pack, or one without hashes) */
  datapack_sha256?: string | null;
  backend: string | null;
  started_utc: string;
  finished_utc: string;
  duration_ms: number;
  /** SHA-256 of the canonical parameters JSON (keys sorted, no whitespace) */
  params_sha256: string;
  result_sha256: string;
  dataset_id: string | null;
  dataset_filename: string | null;
  /** SHA-256 of the uploaded file; null when the inputs were all in the parameters */
  input_sha256: string | null;
  input_note: string | null;
}

export interface BriefProvenance {
  airbrief_version: string;
  schema_version: string;
  brief_schema_version: string;
  generated_utc: string;
  started_utc: string | null;
  finished_utc: string | null;
  canonical_json: string;
  /** "" when the brief was assembled in the browser (static demo): the API computes it */
  operation_sha256: string;
  brief_sha256: string;
  analyses: BriefAnalysisProvenance[];
  datapacks: PackProvenance[];
  inputs: BriefDatasetRef[];
  /** id of the key that signs the bundle's provenance.json; null when the instance does not sign */
  signing_key_id?: string | null;
  /** what the hashes and the signature prove, in words (missing in the static demo) */
  signature_note?: string;
}

/** `POST /api/briefs/` — evidence, never an authorisation. */
export interface Brief {
  title: string;
  notice: string;
  synthetic: boolean;
  /** "SYNTHETIC DEMO DATA — not evidence" when an analysis read a synthetic data pack */
  watermark: string | null;
  operation: OperationSummary;
  /** every section, in `BRIEF_SECTION_ORDER` */
  sections: BriefSection[];
  /** cross-checks (different areas or datasets, aircraft vs assumptions, window vs environment date…) */
  warnings: Issue[];
  /** [min_lon, min_lat, max_lon, max_lat] */
  extent: BBoxList | null;
  provenance: BriefProvenance;
}

/** Body of `POST /api/briefs/` and `POST /api/briefs/bundle/`. */
export interface BriefRequest {
  /** 1 to 12 completed analyses */
  analysis_ids: string[];
  operation: OperationMeta;
}

// batch.py and models/batch.py: one result per feature

export type BatchTone = "ok" | "info" | "warn" | "danger" | "neutral";

/** `params.batch`: run the analysis once per feature instead of once on their union. */
export interface BatchParams {
  mode: "per_feature";
  /** the property that identifies each feature (e.g. "asset_id"); default its name, then its number */
  key_property?: string | null;
  /** at most 1,000 features (line of sight 500); the API fills it from the dataset selection. Only
   * sent and in the JSON export: AnalysisInfo responses carry `feature_count` instead */
  features?: FeatureCollection<Geometry | null, Record<string, unknown>>;
  feature_count?: number | null;
}

export interface BatchItem {
  /** the dataset feature number (`_index`) for a dataset run, else the position in the request */
  index: number;
  key: string;
  name: string | null;
  status: "ok" | "error" | "skipped";
  /** a key of the result's `counts` (null when not assessed) */
  verdict: string | null;
  /** the kind's figures, SI units as in their names (airbrief_core/batch.py BATCH_FIELDS) */
  values: Record<string, string | number | boolean | null>;
  /** issue codes of this feature's run */
  issues: string[];
  error_code: string | null;
  error: string | null;
  /** the feature's own properties */
  attributes: Record<string, unknown>;
}

export interface BatchCount {
  verdict: string;
  label: string;
  tone: BatchTone;
  count: number;
}

/** The result of a per-feature run, whatever its kind (`result.batch === "per_feature"`). */
export interface BatchResult {
  batch: "per_feature";
  kind: AnalysisKind;
  key_property: string | null;
  feature_count: number;
  ok: number;
  errors: number;
  skipped: number;
  /** verdicts found, most severe first (survey: fewest batteries first) */
  counts: BatchCount[];
  items: BatchItem[];
  summary: string;
  method: string;
  disclaimer: string;
  issues: Issue[];
  /** `features`: every feature with index, key, name, status, verdict, label, tone and its values */
  layers: ResultLayers;
  /** features drawn as a point in `layers` (the layer's vertex cap) */
  points_only: number;
}

// models/zones.py: rule sets, zone layer mappings, band evidence

/** `zone_profile`: the DGCA 2021 bands, the conservative rules used before profiles, or the analysis' own `zone_rules`. */
export type ZoneProfileKey = "dgca_2021" | "conservative_illustrative" | "custom";
export type ZoneBasis = "rule" | "verify_official_map";
/** what an aerodrome band was measured from */
export type DistanceFrom = "perimeter" | "runway_hull" | "reference_point";

/** Which properties of a dataset's features give each zone's kind, ceiling and validity (null: found by name). */
export interface ZoneLayerMapping {
  zone_field?: string | null;
  default_zone?: ZoneKind;
  ceiling_field?: string | null;
  valid_from_field?: string | null;
  valid_to_field?: string | null;
  layer?: string | null;
}

/** One aerodrome band the flight reaches (or "elsewhere"), with the worst height margin in it. */
export interface BandEvidence {
  ident: string | null;
  name: string;
  band: string;
  inner_km: number;
  outer_km: number;
  zone: ZoneKind;
  permission_required: boolean;
  basis: ZoneBasis;
  /** null for the "elsewhere" band */
  distance_from: DistanceFrom | null;
  ceiling_agl_m: number | null;
  worst_agl_m: number | null;
  agl_margin_m: number | null;
  samples: number;
  sample_kind: "profile" | "grid" | "vertices" | "track";
}

// Assessment choices (airbrief_core/models/zones.py, docs/features/choices.md)

/** Flight audit: above a band's free-flight ceiling (DGCA 8–12 km above 60 m), an exceedance or the yellow zone above it. */
export type BandCeiling = "exceedance" | "permission";
/** eVTOL: the band the published rule does not define (DGCA 0–5 km), costed as permission required or excluded like a red zone. */
export type UnverifiedBand = "permission" | "exclude";
/** Where an ED-318 export writes the vertical limits (`?ed318_limits=`). */
export type Ed318Limits = "geometry" | "properties";
