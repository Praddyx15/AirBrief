# AirBrief — Internal Specification (contracts between components)

This document is binding for contributors. When code and this spec disagree, fix one of them in the same change.
The data contract is the Pydantic models in `airbrief_core/models/` (mirrored in `frontend/lib/types.ts`).
Each feature added in the 2026-10 upgrade (ADR-0010) and after it (zone engine v2 and one result per feature, ADR-0011) has a detailed user document in [`docs/features/`](features/); this spec summarises the contract and links to them.

## 1. Conventions

- **Python:**
  - Python ≥ 3.11 and `from __future__ import annotations` everywhere.
  - Type hints on all functions, and a module docstring per file.
  - **No placeholders, no TODO stubs, no `pass` bodies, and no `NotImplementedError`** for implemented features.
- **Coordinates and units:**
  - Coordinates use GeoJSON order `[lon, lat(, z)]`, in WGS84 unless stated otherwise.
  - Units are SI (m, m², s, m/s); angles are in degrees.
  - In the core, geometries are Shapely 2 objects. At boundaries they are GeoJSON dicts with list coordinates, rounded to 8 decimals for WGS84.
  - Non-finite coordinates (`NaN`, infinity) are never accepted as positions.
- **Errors:** raise `airbrief_core.errors.*Error(code, message, details=)` with the codes listed in `errors.py`. The core never raises bare exceptions for bad user input.
- **Purity:** the core (`airbrief_core`) never imports FastAPI, SQLAlchemy or anything under `api/`. It works offline. Network access is only allowed in `airbrief_core/data/fetch.py`, which is used by `seed/`.
- **Evidence wording:** results state facts, ranges and margins. No module returns a go/no-go, a permission, a final GRC/ARC/SAIL, waypoints or a mission file (ADR-0005, ADR-0010).
- **Linting:** ruff with rules E, F, W, B (E501 and B008 ignored). No unused imports or variables.
- **Dependencies** (core):
  - pydantic 2, numpy, shapely ≥ 2.0.2, pyproj ≥ 3.6, pyshp ≥ 2.3 (module `shapefile`)
  - lxml, rasterio ≥ 1.3.9, h3 ≥ 4.1 (v4 API: `latlng_to_cell`, `cell_to_latlng`, `cell_to_boundary`, `grid_disk`, `polygon_to_cells`/`LatLngPoly`, `cell_area`)
  - networkx, scipy
- **Dependencies** (api extra): fastapi, uvicorn, sqlalchemy 2, alembic, python-multipart, pydantic-settings.

## 2. Package map and public functions

```
airbrief_core/
  __init__.py        __version__ = "0.1.0"; re-exports process_file, run_analysis, run_analysis_sync, models
  errors.py          (written)
  models/            common.py features.py presets.py analyses.py
                     environment.py line_of_sight.py ground_risk_volume.py heights.py survey.py tracks.py brief.py
                     zones.py (ZoneLayerMapping, BandEvidence, check_zone_inputs, check_zone_profile, caps,
                     BandCeiling, UnverifiedBand)
                     batch.py (BatchParams, BatchItem, BatchResult, BATCH_KINDS, BATCH_FEATURE_LIMITS)
                     presets.py also: DGCA_2021_ZONE_RULES, CONSERVATIVE_ILLUSTRATIVE (= DEFAULT_ZONE_RULES),
                     ZONE_RULE_PROFILES, profile_rules(profile, custom=None)->ZoneRules, RED_ZONES_NOTICE
  geo/
    crs.py           resolve_crs(text|None)->CRS|None ; crs_label(CRS|None)->str|None ;
                     is_geographic(CRS)->bool ; utm_epsg_for(lon,lat)->int ; utm_zone_label(epsg)->str ;
                     local_area_crs(lon0,lat0)->CRS (laea, WGS84 ellps) ; local_length_crs(lon0,lat0)->CRS (tmerc k=1) ;
                     WGS84: CRS = CRS.from_epsg(4326) ; check_crs_text(text) (refuses grid/init/file references) ;
                     PROJ networking is switched off when the module is imported
    transform.py     transform_geom(geom, src:CRS, dst:CRS)->geom (2D, always_xy) ; to_wgs84(geom, src)->geom ;
                     unwrap_antimeridian(geom)->geom ; geometry_to_geojson(geom, decimals=8|None)->dict ;
                     geojson_to_geometry(dict)->geom (raises AnalysisError invalid_geometry)
    frame.py         class LocalFrame(lon0, lat0): AEQD metres. .to_local(geom) .to_wgs84(geom)
                     .xy(lons, lats)->(xs, ys) np arrays ; .lonlat(xs, ys)->(lons, lats) ; classmethod for_geometry(geom)
    measure.py       measure_geometry(geom|None, crs:CRS|None, *, repaired=False)->Measurement
    geodesic.py      GEOD = pyproj.Geod(ellps="WGS84") ; geodesic_area_perimeter(geom_wgs84)->(area,perim) ;
                     geodesic_length(geom_wgs84)->float ; distance_m(lon1,lat1,lon2,lat2)->float (vectorised ok) ;
                     bearing_deg(lon1,lat1,lon2,lat2)->float
    validity.py      repair(geom)->(geom, repaired:bool) ; drop_z(geom) ; polygonal_part(geom)->Polygon|MultiPolygon|None ;
                     lineal_part(geom) ; geometry_type_name(geom|None)->str
    h3grid.py        cells_for_polygon(geom_wgs84, res, max_cells)->(list[str], truncated:bool) ;
                     cell_centroids(cells)->(lons, lats) ; cell_polygon(cell)->Polygon (lon/lat) ;
                     cells_to_feature_collection(cells, props:dict[str,dict])->dict ; cell_area_m2(cell)->float ;
                     ring_radius_k(res, radius_m)->int ; neighbours(cell)->list[str]
    sampling.py      sample_line(line_wgs84, spacing_m)->(distances_m, lons, lats) evenly incl. endpoints ;
                     grid_points(polygon_wgs84, step_m, max_points)->(lons, lats, cell_area_m2)
    parse.py         parse_coordinates(text)->(list[(lon, lat)], issues) ; parse_geometry_text(text, shape="auto")->(geojson, issues) ;
                     detect_text_format(text)->"geojson"|"wkt"|"coordinates" ; format_lat_lon(lon, lat)->str
                     (mirrored in frontend/lib/coords.ts) ; check_json_limits(doc, max_vertices=10_000)
                     (nesting and per-object vertex caps for decoded JSON: analysis params, fetched reports)
    sun.py           NOAA solar position: solar_position(lon, lat, t) ; apparent_elevation / geometric_elevation ;
                     solar_noon(lon, t_near) ; crossings(...) ; elevation_intervals(...)
    wmm.py           load_wmm()->WmmModel (data/wmm/WMM2025.COF) ; wmm(lon, lat, alt, decimal_year)->MagneticElements ;
                     decimal_year(day, fraction)
  io/
    ingest.py        IngestLimits(dataclass: max_upload_bytes=50MB, max_unzipped_bytes=200MB, max_archive_files=200,
                     max_features=50_000) ; detect_kind(filename, head:bytes)->FileType ;
                     stage_upload(src:Path, filename, workdir:Path, limits)->StagedSource ;
                     safe_extract_zip(zip_path, dest, limits)->list[Path]
                     StagedSource(dataclass): kind:FileType, root:Path, paths:list[Path], filename:str
    kml.py           parse_kml(data:bytes, layer_prefix=None)->list[RawLayer]   (lxml, no entities/network)
    gpx.py           parse_gpx(data:bytes, layer_prefix=None)->list[RawLayer]   (the same hardened lxml parser)
    kml_write.py     KmlDocument (the KML writer shared by dataset and analysis exports) ; xml_text(text) drops
                     characters XML 1.0 cannot hold
    shapefile.py     read_shapefile(shp_path, crs_override:CRS|None)->RawLayer   (pyshp; .prj .cpg; Z support)
    geojson.py       read_geojson(data:bytes, layer_name)->RawLayer   (ED-269/ED-318 documents read as zone files)
    geozones.py      detect_geozone_format(doc)->"ed269"|"ed318"|None ; parse_ed269(doc) / parse_ed318(doc)->list[UserZone] ;
                     parse_geozones(doc) ; geozone_features(doc, fmt) (ingest) ;
                     to_ed318(zones, name, limits: Ed318Limits = "geometry")->FeatureCollection ;
                     zone_from_properties(props, geometry, name, layer_mapping) ; schedule_intervals(zone, start, end)
    tracks.py        read_track_csv(data:bytes, layer_name, origin:tuple|None)->RawLayer   (tracks or point lists; extra columns kept)
    reader.py        RawFeature(dataclass): fid, name, geometry(shapely|None), properties, altitude_mode, times, time_origin_utc, unsupported_reason
                     RawLayer(dataclass): name, crs:CRS|None, crs_source, features:list[RawFeature], issues:list[Issue]
                     read_staged(staged, crs_override:str|None, csv_origin)->list[RawLayer]
  processing.py      process_file(path:Path, filename:str, *, limits=IngestLimits(), crs_override:str|None=None,
                     csv_origin:tuple[float,float,float]|None=None, workdir:Path|None=None,
                     progress:Callable[[str,float],None]|None=None)->DatasetResult
  batch.py           run_batch(kind, base_params, features, ctx, key_property)->BatchResult ; check_batch_size(kind, n) ;
                     is_batch_result(result) ; batch_table(result) (CSV rows + columns) ; export_layers(result) ;
                     BATCH_TIME_BUDGET_S = 600 ; MAX_LAYER_POSITIONS = 200_000
  brief.py           build_brief(sources, operation=None, *, packs=None, generated_utc=None, signing_key_id=None)->Brief ;
                     canonical_json(value)->bytes ; canonical_sha256(value)->str ; brief_digest(brief)->str
  brief_html.py      the self-contained printable brief.html of the bundle
  signing.py         BriefSigner.from_file(pem).sign(data)->signature.json dict ; key_id(public_key)->str ;
                     write_new_key(path) ; parse_public_key(text|file)->bytes ; check_signature(key, data, sig)->bool
                     (needs the `signing` extra: cryptography)
  verify.py          python -m airbrief_core.verify <bundle.zip> [--public-key KEY] | --new-key PATH ;
                     verify_bundle(path, public_key=None)->VerifyReport
  data/
    datapack.py      DataPackManifest(model), DataPack(root).manifest/.name/.bbox/.synthetic/.covers(bbox)
                     .dem()->RasterSampler|None .population()->RasterSampler|None .aerodromes()->AerodromeIndex
                     .obstacles()->PointIndex .pois()->PointIndex .water()->PolygonIndex .zones()->list[UserZone]
                     .assemblies()->list[AssemblyFeature] .power()->PowerIndex
                     .perimeters()->dict[ident, polygon] .runways()->dict[ident, list[Runway]]
                     .search(query, limit)->list[PlaceMatch] .context(bbox, layers, limit, profile="dgca_2021")->PackContext
                     .integrity ("verified"|"unverified"|"mismatch") .content_sha256 (verified packs only) .ensure_usable()
                     DataPackRegistry(dir).list()->list[DataPack] .get(name) .select(bbox, preferred=None)->DataPack|None
                     file_sha256(path) ; content_sha256(files) ; seal_manifest(root, manifest) ; verify_pack(root, manifest)
    raster.py        RasterSampler(path): sample(lons, lats, method="bilinear")->np.ndarray (nan nodata/outside);
                     density_per_km2(lons, lats) (population: people per pixel / pixel area km²);
                     integrate_density(polygon_wgs84, step_m=100)->float people ; stats(polygon, step_m)->dict
                     slope_deg(lons, lats, delta_m=30)->np.ndarray ; name/path/bounds
    aerodromes.py    Aerodrome(dataclass ident,name,type,lon,lat,elevation_m,iata,…); AerodromeIndex(list):
                     nearest(lon,lat,k)->list[(Aerodrome, dist_km)] ; within(lon,lat,radius_km)
    vectors.py       PointIndex(features with lon/lat/props) .near(lon,lat,radius_m) .near_geometry(geom, radius_m)
                     .in_polygon(geom) ; PolygonIndex .intersecting(geom) .contains_xy(lons,lats)->bool array
    power.py         parse_voltage_kv(tag)->float|None ; PowerIndex (lines, minor lines, cables, substations, plants)
    assemblies.py    parse_capacity(tag)->int|None ; load_assemblies(source)->list[AssemblyFeature]
    context.py       CONTEXT_LAYERS ; check_context_bbox(bbox) ; build_context(...) ; search_places(pack, query, limit)
    sources.py       SOURCES registry (urls, licences) ; REGIONS (bengaluru bbox) ; check_region_bbox(bbox) (≤ 2° × 2°) ;
                     worldpop_url(iso3) / worldpop_source(iso3)
    fetch.py         download(url, dest, timeout, retries, user_agent) (stdlib urllib, resumable)
  analyses/
    base.py          AnalysisContext(dataclass: datapack, progress cb, cancel_event); zone rules come from each run's zone_profile
                     .step(stage, fraction) .check_cancelled() .require(*layers)->DataPack
    zones.py         classify_points(lons, lats, ctx/pack, rules, user_zones, …)->list[ZoneState] ; zones_for_geometry(geom,...)->list[ZoneHit] ;
                     band_evidence(...)->list[BandEvidence] ; is_operational(aerodrome, rule, overrides) ; TimeWindow ;
                     has_unverified_band(rules)->bool ; unverified_bands(lons, lats, *, pack, rules, …)->list[str|None]
                     ZoneState(dataclass zone, ceiling_agl_m|None, permission_required, label, source name)
    terrain.py       terrain_profile(line, dem, spacing_m)->list[ProfilePoint] ; terrain_summary(geom, dem)->TerrainSummary|None ;
                     obstacle_height(props)->float|None ; obstacle_height_source(props)->"tag"|"levels"|"default"|None
    heights.py       height references: takeoff_point, flight_level, heights_from_ground, resolve_heights ;
                     index_runs(mask) / height_segments(...) (the shared linear-crossing rule) ; dem_info(pack)->DemInfo|None
    airspace.py      run(params: AirspaceParams, ctx)->AirspaceResult
    groundrisk.py    run(params: GroundRiskParams, ctx)->GroundRiskResult ; igrc_lookup(dim, speed, density, controlled, mass) ;
                     contingency_volume(...) ; build_operational_volume(...)
    environment.py   run(params: EnvironmentParams, ctx)->EnvironmentResult ; sun_times, daylight_check, magnetic_summary,
                     wind_at_height, emi_proximity
    line_of_sight.py run(params: LineOfSightParams, ctx)->LineOfSightResult ; curvature_drop_m(d, k) ; fresnel_radius_m(d1, d2, f)
    evtol.py         run_siting(params, ctx)->EvtolSitingResult ; run_corridor(params, ctx)->EvtolCorridorResult
    shows.py         run(params: ShowParams, ctx)->ShowResult
    survey.py        run(params: SurveyParams, ctx)->SurveyResult ; camera_math(camera, altitude)->dict
    flight_audit.py  run(params: FlightAuditParams, ctx)->FlightAuditResult
    __init__.py      REGISTRY: dict[kind, AnalysisSpec(kind,title,description,params_model,result_model,run,requires,optional)]
  integrations/flightmd.py        parse_flightmd_report(obj: dict|object)->FlightTrack(points [lon,lat,alt], info: FlightInfo, schema_version)
  integrations/position_track.py  parse_position_track(track: AuditTrackInput)->FlightTrack (GPX, KML gx:Track, CSV tracks)
  orchestrator.py    async run_analysis(kind, params: dict|InputModel, *, datapack=None, progress=None, cancel_event=None)->AnalysisEnvelope
                     run_analysis_sync(...) same, synchronous
  samples.py         SAMPLES: dict[name, SampleSpec(name,title,description,filename,module)] ; build_sample(name)->bytes ;
                     write_all(dest_dir)->list[Path]
  _native.py         BACKEND: "cpp"|"python" ; min_separation_frames(pos(T,N,3), active(T,N)) ;
                     pairs_within(pos, active, thr, max_pairs=None) (TooManyPairs above max_pairs, counted before building)
```

### Measurement rules (`geo/measure.py`)
- **Unknown CRS:** `status=skipped`, reason "CRS unknown — not measured".
- **Empty or None geometry:** `skipped`.
- **Non-finite coordinates:** the feature is `unsupported` (issue `invalid_coordinates`); non-finite altitudes are dropped and the feature is measured in 2D (issue `invalid_altitudes`).
- **Points and MultiPoints:** `not_applicable`.
- **GeometryCollection:** measured by component type. Polygonal parts add area and perimeter; lineal parts add length. `parts` counts the components by type.
- **Projected source:** `grid_area_m2` and `grid_length_m` are taken in the source CRS, multiplied by the unit factor (`crs.axis_info[0].unit_conversion_factor`).
- **Procedure:**
  1. Transform to WGS84 and unwrap the antimeridian.
  2. Take the centroid (lon0, lat0).
  3. Area uses `local_area_crs`. Perimeter and length use `local_length_crs`.
  4. 3D length is Σ√(dx² + dy² + dz²) in the TM projection, using the source z values.
  5. Geodesic values come from `pyproj.Geod`.
  6. `deviation_pct` is the maximum relative difference across the measured quantities.
  7. Fill `method="local-projection"`, the PROJ strings in `area_crs`/`length_crs`, and `utm_epsg`/`utm_zone` for context.
- **Invalid polygons:** repaired by the processing step before measuring, with `repaired=True`.

### Dataset-level CRS
- Equal across all known layers → that label.
- Different between layers → `"mixed"`.
- None known → `None`.
- KML and GPX are EPSG:4326 (`crs_source="kml"` for KML). GeoJSON is EPSG:4326 unless it carries a legacy `crs` member.
- CSV files are EPSG:4326, whether they arrive as lon/lat or are converted from local x/y/z.

### Ingest
- **Accepted:** `.zip` (shapefile(s); a zip of KML, GeoJSON, GPX or CSV files is also accepted, every supported file is read), `.kml`, `.kmz`, `.geojson`/`.json`, `.gpx`, `.csv`.
- **Zip guards:**
  - Reject absolute paths, `..`, drive letters and symlinks (`unsafe_archive`).
  - Reject a declared or actual uncompressed size above the limit (`archive_too_large`).
  - Reject more entries than allowed (`too_many_files`).
  - Ignore `__MACOSX/`.
- **Shapefile components:** a `.shp` with no `.shx`/`.dbf` → that layer is skipped with an issue. If no layer remains → `missing_component`.
- **GPX** (`io/gpx.py`, details in [`features/tracks.md`](features/tracks.md#1-ingest)): `wpt` → one Point each; `rte` → one LineString each; `trk` → **one LineString per track**, its `trkseg` joined in file order. A track whose every point has a `<time>` that never goes backwards keeps `times` and `time_origin_utc`, like a KML `gx:Track`. `ele` makes the feature `absolute` (above sea level); without any `ele` the feature is 2D (`clampToGround`); missing `ele` inside a line are interpolated. Layers: `<name> / Waypoints`, `<name> / Routes`, `<name> / Tracks`. Documents declaring entities → `corrupt_file`; files above 100 MB → `file_too_large`.
- **CSV** (`io/tracks.py`): extra columns are kept as properties under their header text (numbers parsed only when clean). Without a time column, an id or name column with a different value on every row makes a point list; `# geometry=points` / `# geometry=tracks` force the choice. Without a height column the features are 2D (`clampToGround`), never 0 m. A zip of per-drone CSVs gives one track per file, named after the file stem.
- **Partial archives:** a GPX or CSV file that cannot be read next to other files is skipped with a warning layer; the upload fails only when nothing could be read.
- **Corrupt input:** malformed XML, JSON or shapefile → `corrupt_file`.
- **FlightMD report as a dataset:** a `.json` FlightMD report (top-level `schema_version` with `report_id` or a GPS path) → `flightmd_report`; it belongs in the flight audit module.
- **Size of the content:** more than `MAX_FEATURES` features → `too_many_features`; more than `MAX_COORDINATES` vertices in total → `too_complex`, raised while the geometries are being built (each reader calls `charge_vertices`).

## 3. Analysis modules (behaviour summary — details are in the module docstrings and `docs/features/`)

**Common**
- Each `run` calls `ctx.step()` for progress and `ctx.check_cancelled()` inside its loops.
- Missing optional data adds an `Issue` (severity info or warning) and the module degrades gracefully.
- Missing required data raises `AnalysisError("datapack_missing")`.
- **Data per module** (`REGISTRY[kind].requires` / `.optional`):

  | Kind | Requires | Optional |
  |---|---|---|
  | airspace | — | aerodromes, dem, obstacles, zones, aerodrome_perimeters, runways |
  | ground_risk | population | assemblies |
  | environment | — | dem, power, obstacles |
  | line_of_sight | dem | obstacles |
  | evtol_siting | population, dem | water, pois, obstacles, aerodromes, zones, aerodrome_perimeters, runways |
  | evtol_corridor | population | dem, obstacles, aerodromes, zones, aerodrome_perimeters, runways |
  | show | — | — |
  | survey | — | dem |
  | flight_audit | — | dem, aerodromes, zones, population, aerodrome_perimeters, runways |

- **Result `layers` keys** (each value is a GeoJSON FeatureCollection in WGS84):
  - airspace: `input`, `zones`, `obstacles`; plus `heights` (stretches below the minimum or above the ceiling) and `takeoff` for terrain-true plans
  - ground_risk: `input`, `footprint`; with `volume` also `flight_geography`, `contingency_volume`, `ground_risk_buffer`, `adjacent_area`, `assemblies`
  - environment: `input`, `point`, `emi`, `emi_search`
  - line_of_sight: `input`, `observers`, `visible`, `hidden`, `hidden_patch`, `vlos_guidance`, `radio_paths`, `route`
  - evtol_siting: `input`, `candidates`
  - evtol_corridor: `route`, `corridor`, `endpoints`, `obstacles`, `heights`
  - show: `paths`, `show_box`, `audience_buffer`, `audience_line`, `pad_conflicts`
  - survey: `input`, `blocks`, `lines` (visual only; **never exported** in any format), `takeoff`
  - flight_audit: `path` (each segment carries `agl_band`: `ok` | `near` | `over`), `exceedances`, `home`; plus `training_box`, `area_exits`, `home_radius`, `distance_exceedances` when those checks are asked for, and `zones` (bands near the track and the zones it touched) when there are any
  - any per-feature run (`batch`): `features` only (every feature with its verdict, at most 200,000 vertices)
- **Verdicts** (a summary of the evidence, never a permission):
  - airspace: fail if a red zone is touched, or the ground reaches the flight level (`terrain_above_flight_level`); caution if permission is required, the ceiling is below the planned height, there is an obstacle conflict, the flight goes below the minimum height above ground, the heights could not be related to the ground, or the airspace was **not assessed**; otherwise pass.
  - show: fail on any conflict, violation or pad conflict; caution on site warnings (the show box overlapping the audience distance); otherwise pass.
  - flight_audit: fail on a height exceedance, a red zone, a training-area exit or a distance exceedance; caution on a zone needing permission, a run above a band's ceiling under `band_ceiling: permission`, an incomplete airspace assessment or unassessed heights (2D track); otherwise pass.
  - ground_risk, environment, line_of_sight, survey and eVTOL return figures, not verdicts.

**Zones** (`analyses/zones.py`, [`features/zones.md`](features/zones.md), [`features/zones-followups.md`](features/zones-followups.md))
- **Rule profiles** (`zone_profile`; airspace, flight audit, eVTOL siting and corridor): `dgca_2021` (default), `conservative_illustrative`, `custom` (= the run's `zone_rules`). `zone_rules` alone makes the profile `custom`; `custom` without rules, or rules with another profile, is 422. The API stores the profile with every run; a run stored without one used the conservative rules and is read that way. `profile_rules` is the one lookup.

  | DGCA 2021 band (from the perimeter of an operational airport) | Zone |
  |---|---|
  | 0–5 km | yellow, permission required, `basis: verify_official_map` ("verify on Digital Sky"); never `pass` |
  | 5–8 km | yellow from the ground, permission required |
  | 8–12 km | green up to 60 m AGL; above it yellow (permission), with `agl_margin_m` |
  | elsewhere | green up to 120 m AGL; above it yellow (permission) |

  Source: Drone Rules 2021 (G.S.R. 589(E)) and the MoCA drone airspace map of 24 September 2021 (`DGCA_2021_ZONE_RULES.reference`). The conservative profile (`CONSERVATIVE_ILLUSTRATIVE`, also `DEFAULT_ZONE_RULES`) is the rules used before profiles: 0–8 km "permission required — verify on Digital Sky", 8–12 km up to 60 m for large and medium airports, 3 km small-airport and 1 km heliport rings ("conservative assumption, not a DGCA rule"), measured from the reference point minus an assumed radius.
- **Distance** (`ZoneRules.distance_from`): `perimeter` (DGCA) measures in a local AEQD frame from the aerodrome's boundary (pack layer `aerodrome_perimeters`), else the convex hull of its runway ends buffered by half the widest runway (`runways`, 45 m wide when unknown; note `perimeter_from_runways`), else the reference point minus `assumed_radius_km` (warning `perimeter_approximated`). `reference_point` always uses the last. A point is in a band when `inner_km ≤ distance < outer_km`; inside the outline the distance is 0. Band polygons are `outline.buffer(outer) − outline.buffer(inner)` (32 segments per quarter circle).
- **Operational:** a rule's types apply unless `aerodrome_operational[ident]` says otherwise; DGCA counts large and medium airports, and small ones only with OurAirports `scheduled_service = yes` (unknown counts as yes). Heliports and closed fields get no band. Aerodromes switched off get the warning `aerodrome_not_operational`.
- **A band with `ceiling_exceeded_zone`** is its zone up to the ceiling and that zone (permission) above, judged per point at the planned height above ground.
- **When zones overlap,** the most restrictive one wins: red, then yellow requiring permission, then the lowest ceiling, then green.
- **User, dataset and data-pack zones** apply by polygon containment (an STRtree of point/zone pairs; zone polygons are parsed once per run), only when active in the planned window and when their vertical band reaches the flight:
  - **Planned window** (`planned_start_utc`, `planned_end_utc`: both or neither, end after start, at most 31 days, stored in UTC): a zone applies when one of its intervals overlaps the window. Intervals are half-open, `[valid_from, valid_to)` cut to the daily `schedule` periods (`HH:MM` UTC; an end before the start runs past midnight; `ANY` is every day). Without a window every zone counts, with the note `zone_window_not_checked` when some have validity limits.
  - **Vertical band:** the flight may be anywhere from the ground to the planned height, so a zone applies where `lower ≤ planned AGL` and `upper ≥ 0`. `AMSL` limits become heights above ground with the DEM at the point; `WGS84` limits likewise after widening the band by the EGM96 geoid range (lower −85.4 m, upper +106.2 m; note `vertical_reference_approximated`). Unknown ground or height: the zone applies.
  - Zones that do not apply go to `inactive_zones` with `active: false` and an `inactive_reason`, and are not drawn.
- **Outside every zone:** `rules.default_zone` up to `default_ceiling_agl_m`, and `default_ceiling_exceeded_zone` above it. Only there and in zones that take the default ceiling: inside an aerodrome band with a ceiling of its own, or a user, dataset or pack zone that applies and has its own `ceiling_agl_m`, that ceiling decides (a green user zone up to 200 m flown at 150 m is free under DGCA 2021; a green zone without a ceiling is yellow above 120 m like open airspace).
- **Results:** `ZoneHit` adds `active`, `valid_from`, `valid_to`, `agl_margin_m` (ceiling, or an AGL zone's lower limit, minus the planned height above ground), `basis` (`rule` / `verify_official_map`), `distance_from`, `inactive_reason`. `NearestAerodrome` adds `operational` and `scheduled_service`. `AirspaceResult` adds `zone_profile`, `band_evidence`, `inactive_zones` and `assessment_banner` (the red-zones notice). `BandEvidence`: per band reached and "elsewhere", `samples` (route profile samples, else grid cells, else vertices; `track` fixes for a flight audit), `worst_agl_m`, `agl_margin_m`, `ceiling_agl_m`, `zone`, `permission_required`, `basis`, `distance_from`.
- **Zones from datasets** (airspace only, resolved by the API, below): `zone_dataset_ids` (≤ 10) and `zone_mappings` (`zone_field`, `default_zone` = red, `ceiling_field`, `valid_from_field`, `valid_to_field`, `layer`) give `dataset_zones` (≤ 5,000). Without a mapping the kind comes from `zone`, `colour`, `color` or `restriction`, the ceiling from `ceiling_agl_m`, `ceiling_m` or `ceiling`, the validity from `valid_from`/`valid_to`, plus the flat properties of an imported zone file.

**ED-269 / ED-318** (`io/geozones.py`)
- **Read:** an upload is a zone file when `detect_geozone_format` finds, in its first 20 features, an ED-318 FeatureCollection (a restriction code in `type`, a `layer` or `extent` on a geometry, or `identifier` with `restriction`) or an ED-269 document (no top-level `type`, list `geometry`, `restriction`). Each zone becomes polygon features with flat properties (`zone`, limits, `vertical_reference`, `schedule`, `restriction`, `authority`, `reason`, `source_format`) and the info issue `geozone_file`. A zone with several volumes or periods becomes one feature each.
- **Mapping:** PROHIBITED red; REQ_AUTHORISATION / REQ_AUTHORIZATION yellow (permission); CONDITIONAL yellow with a note; USPACE / U-SPACE yellow ("U-space airspace"); NO_RESTRICTION green. Circles become 64-gons in a local frame; feet × 0.3048; `99999` or `UNL` is unlimited; a lower limit of 0 AGL, or one in another reference than the upper limit, is "from the ground"; sunrise/sunset schedules are all day (noted).
- **Caps:** 5,000 zones per file, 100 volumes and 10 periods per zone, 20 schedule entries, 1,000,000 vertices, 32 levels of nesting (checked iteratively first), radius ≤ 500 km, WGS84 only. A malformed zone → `invalid_params` with its index; on upload the dataset is `FAILED` with `invalid_zone_file`. The 5,000 count the parts after the split into volumes × periods (up to 200 per zone), added up before any zone is built; the file is refused as soon as they pass 5,000 (`details.limit` = 5,000, `details.zone` = the zone that crossed it).
- **Write** (`to_ed318`): one Feature per zone, `identifier` `AA00001`…, `name`, `type`, `variant: COMMON`, `message`, `reason`, `zoneAuthority`, `limitedApplicability`, limits in `geometry.layer` (metres, `lower` 0 AGL from the ground, `upper` 99999 unlimited), and `extendedProperties.airbrief` (zone kind, ceiling, restriction as given, name, note, authority, reason), so `parse_ed318(to_ed318(z)) == z`. An aerodrome band that changes zone at its ceiling is two volumes ("VOBL 8–12 km up to 60 m", "… above 60 m"). Assumptions (circle encodings, limits in `geometry.layer`, radius units) are in the module docstring.

**Airspace: coverage and heights** ([`features/groundrisk.md`](features/groundrisk.md#airspace-not-assessed-and-interim-dgca-wording), [`features/heights.md`](features/heights.md))
- **Not assessed.** Without a data pack, with a pack that has no `aerodromes` layer, or with the geometry outside the pack, aerodrome zones cannot be checked: the verdict is at best **caution**, `assessment_complete = false` (stored results default to `true`), issue `not_assessed` with the Digital Sky address, and the summary starts "Airspace not assessed: …". `flight_audit` applies the same rule.
- **Height reference** (`height_reference`: `agl` default, `takeoff`, `amsl`; ICAO Annex 2, Chapter 1). With `h` the planned value (`altitude_agl_m`), `z(x)` the DEM ground and `T` the take-off ground elevation (`takeoff_elevation_m`, else the DEM at `takeoff`, else at the first vertex of a route / the centroid of an area):

  | Reference | Flight level | Height above ground |
  |---|---|---|
  | `agl` | `AMSL(x) = z(x) + h` | `AGL(x) = h` |
  | `takeoff` | `L = T + h` | `AGL(x) = L − z(x)` |
  | `amsl` | `L = h` | `AGL(x) = h − z(x)` |

- **Stretches** (`below_min_agl`, `above_ceiling`): runs of profile samples beyond the limit, starting and ending at the linear crossing `f = −e₀ / (e₁ − e₀)` (the flight audit's rule, `heights.index_runs`); each has `start_m`, `end_m`, `length_m`, `peak_agl_m`, `limit_m`. `min_agl_m` (default 30 m) is checked only for `takeoff` and `amsl` plans; an AGL result has `min_agl_m: null`. Areas count grid cells (`cells_below_min_agl`, `cells_above_ceiling`).
- **Missing terrain:** `heights_not_assessed`, `terrain_gaps`, `takeoff_elevation_unknown`. AMSL plans carry `vertical_reference_approximated` (DEM heights are above the EGM2008 geoid; no geoid grid is shipped).
- **Terrain labels** (`dem`, from `heights.dem_info`): Copernicus GLO-30/GLO-90 are surface models (roofs and canopy included); the synthetic DEM is labelled invented. Obstacles without an OSM height get a per-kind default with `height_source: "default"`, `height_assumed: true`. `DemInfo` = `{source, title, surface_model (true / false / null = unknown), synthetic, vertical_datum, resolution_m, caveat}`; airspace, survey, the eVTOL corridor, `LineOfSightResult.dem` and `FlightAuditResult.dem` (only when terrain was used) carry it, and the web app shows it as the `DemCaveat` chip. Ground risk reads no terrain and has none.

**Ground risk** ([`features/groundrisk.md`](features/groundrisk.md))
- **SORA 2.5 operational volume** (when `volume` is given; the web app always sends it). Sources: JARUS SORA 2.5 Main Body §2.2, §4.2 (Tables 2–4), §4.8, Annex E §E.4; the term structure of the German LBA operational-volume method. `g` = 9.80665 m/s², `v` = maximum speed, `c` = maximum characteristic dimension.
  1. **Flight geography (FG):** the input geometry at `H_FG` (`volume.fg_height_m`, default the height above ground).
  2. **Contingency volume (CV):** `S_CV = S_GNSS + S_pos + S_map + S_R + S_CM`, `S_R = v·t_R`; multirotor `S_CM = v² / (2·g·tan θ)`, fixed-wing `S_CM = v² / (g·tan φ)`. `H_CV = H_FG + H_alt + H_R + H_CM`, `H_R = v·t_R·sin γ`, `H_CM = ½·v²/g`.
  3. **Ground risk buffer (GRB):** `one_to_one`: `S_GRB = H_CV + c/2`; `ballistic`: `S_GRB = v·√(2·H_CV/g) + c/2`.
  4. **Geometry:** CV = FG buffered by `S_CV`; footprint = FG buffered by `S_CV + S_GRB`, in a LocalFrame centred on the operation. FG ⊂ CV ⊂ footprint.
  5. **iGRC density:** the population raster averaged on a square grid of `grid_m` cells (default **Table 4** by `H_FG`: 200 m up to 152.4 m, 400 m up to 1,000 ft, 1 km up to 2,500 ft, 2 km up to 5,000 ft, 4 km up to 10,000 ft, 5 km up to 20,000 ft, 10 km above; doubled until it fits `max_cells`, issue `grid_coarsened`); every cell overlapping the footprint counts, each from up to 8 × 8 samples; the highest cell mean is the density (`p95` is kept as a supplementary value).
  6. **Table 2:** column = the leftmost one whose dimension and speed both cover the aircraft (1 m/25 m/s, 3 m/35 m/s, 8 m/75 m/s, **20 m/120 m/s**, 40 m/200 m/s); row = the density band (controlled ground area, < 5, < 50, < 500, < 5,000, < 50,000, > 50,000 people/km²). Cells above 50,000 for the 8, 20 and 40 m columns are out of scope; a UA ≤ 250 g and ≤ 25 m/s has iGRC 1. The whole table is returned with the cell that applies (`igrc_table`).
  7. **Adjacent area:** outer limit `clamp(v·180 s, 5 km, 35 km)` from the CV's outer limit, inner limit the GRB edge; average density = people integrated on a 500 m grid ÷ the covered ring area (`adjacent_coverage`, issue `partial_coverage`). GRB wider than the adjacent distance → `adjacent_not_required`; UA ≤ 250 g → `adjacent_small_uas`.
  8. **Assemblies:** the pack's `assemblies` layer and the user's `assemblies[]` (at most 100) within 1 km of the CV, nearest first, banded < 40k / 40k–400k / > 400k people or capacity unknown; one inside the footprint raises `assembly_in_footprint`; a GRB wider than 1 km → `assemblies_not_applicable`.
  - Every result carries `evidence_label` = "SORA Step 2 / Step 8 evidence — not a GRC, ARC or SAIL". A request with `assemblies` or `grid_m` but no `volume` → 422 `invalid_params`.
- **Earlier footprint method** (no `volume`; older clients and stored analyses):
  - Buffer: `one_to_one` = altitude; `ballistic` = v·√(2h/g). Footprint = the geometry buffered in the LocalFrame.
  - Density sampled on H3 cell centroids at `h3_resolution`, clipped to the footprint; the statistic (`max` or `p95`); the iGRC from the same Table 2 (controlled ground area → row 0; small-UAS rule → iGRC 1; a `None` cell → `in_scope=False`).
  - Population = `integrate_density(footprint)`.

**Environment** ([`features/environment.md`](features/environment.md))
- **Inputs:** `geometry`; `date` (required, `YYYY-MM-DD`); `window_start`/`window_end` (`HH:MM`, both or neither; an end before the start is on the next day); `utc_offset_min` (−720…840, display only: every computation is in UTC); `daylight_profile` (`india_daylight` default, `faa_107`, `custom` with `min_sun_elevation_deg` −18…60°); `height_agl_m` (120); `surface_wind_ms`, `gust_ms`, `reference_height_m` (10), `shear_exponent` (1/7), `wind_limit_ms`; `include_emi` (true), `emi_radius_m` (300, 10…5000); `sun_min_deg`/`sun_max_deg` (30/60).
- **Sun:** NOAA GML solar calculator equations (after Meeus, 1991) in UT. Sunrise/sunset = geometric elevation of the centre crossing **−0.833°**; civil dawn/dusk **−6°**; solar noon = hour angle 0; the day scanned every minute and each crossing bisected below a second; polar day/night detected; the 24 h track is the apparent elevation every 5 min.
- **Daylight profiles:** the day and window split at −6°, −0.833° (and the custom minimum). `india_daylight`: only day is within; `faa_107` (14 CFR 107.29): civil twilight needs anti-collision lighting, night is flagged; `custom`: within when the apparent elevation ≥ the minimum. `minutes_outside`, `minutes_lighting` and a statement.
- **Magnetic field:** WMM2025 (degree and order 12, epoch 2025.0, valid 2025.0–2030.0) at DEM ground + `height_agl_m` at the window start (else local noon); D, I, H, F, X/Y/Z, annual change, grid variation above 55°; blackout H < 2,000 nT, caution 2,000 ≤ H < 6,000 nT; out-of-range dates extrapolated with `wmm_out_of_range`.
- **Wind at height:** `v(z) = v_ref · (z / z_ref)^α` for the mean wind and the gust; margin = rated limit − estimate (a number, never a verdict).
- **Interference proximity:** the pack's `power` layer (lines, minor lines, cables, substations, plants, `voltage_kv` = the largest value of the OSM tag in kV) and `obstacles` of kind `communication_tower`/`mast`, within `emi_radius_m` of the whole geometry (0 m when touching), nearest first, at most 50.
- **Outputs:** `sun`, `daylight`, `magnetic`, `wind`, `emi`, `sun_window`, `facts`, `issues`, `method`, `disclaimer`. Issues include `window_outside_profile`, `lighting_required`, `polar_day`, `polar_night`, `magnetic_blackout` (critical), `magnetic_caution`, `wind_above_limit`, `gust_above_limit`, `emi_not_assessed`, `power_not_assessed`, `masts_not_assessed`, `emi_partial_coverage`, `power_line_crossing`, `synthetic_data`.

**Line of sight** ([`features/los.md`](features/los.md))
- **Inputs** (`LineOfSightParams`): `geometry`; `observers[]` (≤ 10, `{name, role: pilot|observer|gcs, lon, lat, height_agl_m}`; empty height = 1.7 m eye or 2.0 m antenna); `target_height_agl_m` (120); `grid_m` (30, coarsened to ≤ 400 × 400 cells, issue `grid_coarsened`); `max_range_m`; `k_visible` (7/6) / `k_radio` (4/3); `radio_check` (true); `frequency_mhz` (2400, 30–100,000); `fresnel_clearance_pct` (60); `radio_target`; `include_obstacles` (true); `vlos_aircraft` (`multirotor`|`fixed_wing`); `characteristic_dimension_m`; `ground_visibility_m` (5000). Extents over 60 km → `area_too_large`.
- **Viewshed:** a radial sweep (n ≥ 2π·r_max rays, samples every half cell, terrain interpolated bilinearly, running maximum slope; Franklin & Ray, 1994); heights lowered by `drop(d) = d² / (2·k·R)`, R = 6,371,000 m; obstacle tops raised onto their cells. Combined visibility = seen by at least one pilot or observer; ground stations (else the pilot) give the radio line of sight with k = 4/3.
- **Statistics:** visible % of the area cells with DEM data; along a route the seen length, the longest seen stretch and hidden stretches (≤ 200); the largest hidden patch (8-connected).
- **Radio path:** from the first ground station (else the pilot) to `radio_target`, else every route vertex (≤ 20), else the farthest corner; sampled on the DEM; first Fresnel radius `r1 = 17.32·√(d1·d2/(f·D))` (m; km; GHz); clearance % = (line of sight − surface) / r1 · 100; `FSPL = 20·log10(d_km) + 20·log10(f_MHz) + 32.44` dB over the slant distance. Geometry only — not a link budget.
- **VLOS guidance (non-regulatory):** with CD, EASA (Issue 03, 17 July 2025): ALOS = 327·CD + 20 m (multirotor) or 490·CD + 30 m (fixed-wing), DLOS = 0.3 × ground visibility, VLOS = min(ALOS, DLOS); without CD the commonly cited 500 m (UK CAA CAP 722). Never a pass/fail.

**Survey** ([`features/heights.md`](features/heights.md#survey-upgrades))
1. GSD = sw·H/(f·iw). Altitude from a target GSD = gsd·f·iw/sw.
2. Footprint across = sw·H/f and along = sh·H/f (swapped in portrait).
3. Line spacing = across·(1 − side). Trigger distance = along·(1 − front).
4. Direction = the long axis of the minimum rotated rectangle in the LocalFrame (or the user's value).
5. Lines are spaced from minY + spacing/2 and clipped to the polygon.
6. Photos = Σ(floor(seg/trigger) + 1) over the segments.
7. Flight minutes = (line length/speed + turns·turn_s + connector length/speed)/60.
8. Blocks: consecutive whole lines grouped until the usable battery time is used up, numbered from 1; each block's polygon = polygon ∩ strip.
9. Batteries = Σ ceil(block time/usable battery) over the blocks. Total minutes = flight + (batteries − 1)·swap.
10. **Height reference** (`height_reference`, `takeoff`, `takeoff_elevation_m`): above take-off and AMSL plans fly one level `L`; the layout uses the height above the take-off ground `H₀`; with a DEM each block's ground `z_min..z_max` gives `H_eff` from `L − z_max` to `L − z_min`, the GSD range, and the **worst overlap** `1 − (1 − p)·H₀/H_low` (side and front; negative = a gap). AGL plans assume terrain following.
11. **Corridor** (`coverage: "corridor"`, `corridor_width_m`): the centreline buffered by half the width; `n = ceil(width / S)` lines at offsets `(i − (n − 1)/2)·S`; cut into the fewest equal pieces that each fit a usable battery (and a tank when spraying).
12. **Battery reserve and transit:** usable = battery × (1 − `battery_reserve_pct`); with `transit_legs` each block also flies from the take-off point to its first line and back at `transit_speed_ms` (`transit_exceeds_battery` when a leg alone uses a battery).
13. **GCPs and storage:** `gcp_min + floor(area_ha / gcp_ha_per_extra)` (area) or `max(gcp_min, floor(centreline / gcp_corridor_spacing_m) + 1)` (corridor), a rule of thumb; storage = photos × images per capture × `photo_mb`.
14. **Spraying** (`mode: "spraying"`, `spray{swath_m, rate_l_per_ha, tank_l, flow_l_per_min, speed_ms, turn_seconds, refill_minutes, height_m}`; AGL only): pump output `Q = rate·w·v·60 / 10 000` L/min, speed lowered to `Q_max·10 000 / (rate·w·60)` above the pump maximum (`spray_flow_limited`); litres = area × rate; tank loads = ceil(litres / tank); sorties with breakpoint resume; field capacity `C = w·v·3 600 / 10 000` ha/h (ASABE EP496); effective = area / total time.
15. **Sensors:** camera presets carry `kind` (rgb, thermal, multispectral), `images_per_capture`, `photo_mb`, `reference` (DJI H20T, M30T, XT2 640 thermal; P4 Multispectral, Mavic 3 Multispectral).

**P4 RTK at 100 m (worked example):** GSD 2.74 cm, footprint 150 × 100 m, spacing 45 m (side 70%), trigger 20 m (front 80%).

**Shows** ([`features/tracks.md`](features/tracks.md#3-drone-show-upgrades-airbrief_coreanalysesshowspy))
1. Convert to ENU via LocalFrame(origin) plus z.
   - z = alt (relative) or alt − origin_alt (absolute).
   - No origin: the mean of the first positions at 0 m; `absolute` altitudes without a ground altitude are measured from the lowest take-off position. Tracks read from a dataset with `absolute` altitudes get `altitude_reference: "absolute"` and the take-off pad origin; mixed altitude modes → `mixed_altitude_modes`.
2. Resample to `sample_rate_hz` over [min t, max t]. Before a drone's first sample or after its last, it holds that position.
3. **Airborne** = z > `airborne_threshold_m`.
4. **Separation:** `_native.pairs_within` gives every pair under the threshold (at most 500,000 pair-frames, else `too_many_conflicts`), grouped into events per pair over consecutive frames. `min_separation_frames` gives the overall minimum.
5. **Dynamics** use windowed central differences over `dynamics_window_s`:
   - v(t) = (p(t + w/2) − p(t − w/2))/w; acceleration is the same applied to v.
   - Violations are grouped over consecutive frames.
6. **Show box:** the polygon named like "show box", else the east/north rectangle of every track position plus `show_box_margin_m` (5 m), labelled derived (the geofence is then not checked; info `show_box_derived`).
7. **Audience distance** (`audience_buffer_rule`): `fixed` = `audience_buffer_m`; `one_to_one` = the greatest height above the origin `h`; `ballistic` = `v·√(2h/g)` with the greatest windowed horizontal speed `v`; then × (1 + `audience_breach_margin_pct`/100). Drones inside the audience polygon buffered by it → `audience` violations; the box overlapping it → a site warning. `audience_line` = the show box widened by the distance.
8. **Geofence and limits:** outside the show box (2D) and airborne → violation; z > ceiling → violation; horizontal motion above `low_altitude_speed_tolerance_ms` below `min_navigation_altitude_m` → `low_altitude_motion`; airborne longer than `endurance_s` → `endurance`.
9. **Pads:** drones at or below the airborne threshold at their first (last) position stand on a take-off (landing) pad; pairs closer than `min_pad_spacing_m` (1.5 m) → pad conflicts (fail).
10. **Frames** are downsampled to `playback_rate_hz`, rounded to cm.

**Flight audit** ([`features/tracks.md`](features/tracks.md#2-flight-audit-from-a-position-track))
- **Sources:** exactly one of `report` (FlightMD) or `track` (`{name, coordinates, times, time_origin_utc, altitude_mode, source_format, feature_index}`, from a dataset's timed track).
- **Parsing:** `parse_flightmd_report` accepts schema 1.0, 1.2 and 1.5, or the report nested under `"report"`. `metadata.gps_path` is `[lat, lon, alt]` and is converted to `[lon, lat, alt]`. `parse_position_track` turns a track into the same structure; the home point is the first fix.
- **Altitude reference:**
  - DEM present and calibration=`takeoff`: offset = terrain(home) − alt(home), AGL = alt + offset − terrain → `terrain_calibrated`.
  - DEM present and calibration=`none`: `terrain_raw`.
  - No DEM: AGL = alt − alt(home) → `takeoff_relative`.
  - Tracks: `track_heights` `auto` follows the altitude mode (`absolute` → AMSL as above; `relativeToGround` → `AGL = alt`; else above take-off). A 2D track has no heights: `heights_assessed = false`, warning `heights_not_assessed`, verdict at best caution.
- **Runs beyond a limit** (one rule for every check): a run of fixes with `excess > 0` starts and ends at the linear crossing `f = −e₀ / (e₁ − e₀)`, applied to distance, time and position.
  - Height: `excess = AGL − min(ceiling, zone ceiling)`; `time_above_limit_s`.
  - Training area (`training_box`, a polygon or closed line): signed horizontal distance to its boundary (positive outside) → `area_exits`, `time_outside_area_s`.
  - Distance from take-off (`max_distance_from_home_m`, 1–100,000 m): `excess = distance from home − limit` → `distance_exceedances`, `time_beyond_distance_s`; `max_home_distance_m` is always reported.
- Checks not asked for are `null` and add no layers.
- **Zone rules** (`zone_profile`, as airspace; [`features/zones-followups.md`](features/zones-followups.md)): each fix's limit is `min(ceiling_agl_m, zone ceiling)` with the zone classified on the ground, so a band's free-flight ceiling (60 m in the DGCA 8–12 km band) is a limit and flying above it is an exceedance. A zone with vertical limits sets a fix's limit only where the fix, or a segment it ends, is within them (`classify_points(…, reach_agl=…)` with each fix at the highest of itself and its neighbours, as the zone hits count segments): under a zone from 200 m the open-airspace 120 m is the limit. Each zone hit is judged at the highest height flown inside the zone: the highest of its fixes inside (the same fixes `band_evidence` counts) and of both ends of every track segment that touches it, so moving a fix across the boundary never changes the verdict, and a band crossed low after a climb elsewhere stays its ground-level zone; a band flown above its ceiling is the zone above it. Zones whose vertical limits the flight never reached inside them (a red zone from 150 m flown under) → issue `zones_inactive`. `band_evidence` counts the track's fixes per band (`sample_kind: "track"`). The result carries `zone_profile` (null: stored before profiles, read as conservative) and `dem`.
- **Above a band's ceiling** (`band_ceiling`, [`features/choices.md`](features/choices.md)): `exceedance` (default: the rule above) or `permission`. With `permission` a ceiling with a yellow zone above it (`ceiling_exceeded_zone = "yellow"`: the DGCA 8–12 km band's 60 m, open airspace's 120 m) is no limit. The fixes are classified again with those ceilings raised to at least `ceiling_agl_m`, so the audit's own limit, a lower zone ceiling under the band, red zones and a band that turns red above its ceiling still give exceedances. The runs above such a ceiling go to `above_band_ceiling` (`AuditExceedance` rows, `limit_m` = the band's ceiling, reason "Flown in yellow airspace above the 60 m ceiling of …: permission required"), with a layer of the same name and the warning `above_band_ceiling`. `zones` and `band_evidence` do not change. The result echoes `band_ceiling` (null: stored before the choice, an exceedance); `above_band_ceiling` is null unless the run used `permission`.

**eVTOL**
- **Siting:**
  - H3 cells in the area.
  - Criteria are normalised 0..1. Demand = population within `demand_radius_km` (k-ring), normalised by its maximum.
  - Hard exclusions: water, slope > max, red zone.
  - Weighted score ×100. Top-N picked greedily with a minimum separation.
- **Corridor:**
  - The graph is built from H3 cells over the study area (O/D bbox + margin, or the given polygon). Edge cost = distance·(1 + Σ w·penalty).
  - Red zones are impassable when avoided. Obstacles taller than cruise − clearance are impassable.
  - Path: networkx A* with a geodesic heuristic. The route is the cell-centroid polyline (endpoints = O/D), simplified to 20 m. When the simplified line would touch an impassable cell anywhere more than 1 m from the polyline (red when avoided, an excluded band, an obstacle above cruise − clearance; never the endpoints' own cells), the polyline is kept. Only the endpoints' cells are exempt, so a route from inside a red zone is still smoothed but never cuts through the rest of that zone. Only the impassable cells within two edge lengths + 20 m of the polyline are tested, the furthest the smoothing can reach.
  - Corridor = the route buffered by width/2.
  - Noise: L = L_ref − 20·log10(r/r_ref) with slant range r. Cells at or above the threshold count towards the exposed population.
  - **Cruise reference** (`cruise_reference`): `agl` keeps the level that clears the highest ground and tallest obstacle by the cruise height; `amsl` flies `cruise_altitude_amsl_m`, each cell's obstacle limit becomes `level − ground − clearance`, and the result reports the AGL range and the stretches below `min_agl_m` / above `ceiling_agl_m`.
- **Zone rules** (`zone_profile` on both params and results, as airspace): siting scores the zone at the site on the ground (green 1, yellow with a free-flight ceiling 0.5, permission 0, red 0 or excluded). A site in the DGCA 0–5 km band scores 0 and is not excluded but marked: `SiteCandidate.zone_basis = "verify_official_map"`, a note, and the issue `verify_official_map` naming the sites. Corridor costs judge zones at the cruise height above ground (a band flown above its ceiling costs as its zone above it); the 0–5 km band costs 1.0, never 0 (issue `verify_official_map`); open airspace above its ceiling is the same everywhere, so it is not costed and is the issue `open_airspace_above_ceiling`. The corridor's zone hits use the highest height along the route.
- **Within 5 km of an airport** (`unverified_band` on both params and results, siting and corridor; [`features/choices.md`](features/choices.md)): `permission` (default: the rule above) or `exclude`. With `exclude`, every cell whose centre is in a `verify_official_map` band (`unverified_bands`, read from the rings, so an overlapping band of another aerodrome cannot hide it) is excluded from siting (reason `unverified_band`, also with `exclude_red_zones: false`) and impassable for the corridor; an endpoint in such a cell is allowed only as the endpoint (`origin_blocked` / `destination_blocked`), and `no_route` names the excluded bands. The issue `verify_official_map` names the bands and says when the corridor still touches one (an endpoint, a cell edge). Under rules without such a band nothing changes: the siting criteria text says so, and the corridor adds the info issue `unverified_band_not_applicable`. Results echo the choice (null: stored before it, `permission`).

**One result per feature** (`batch.py`, `models/batch.py`, [`features/batch.md`](features/batch.md))
- **Params:** airspace, ground risk, survey, environment and line of sight take `batch = {mode: "per_feature", key_property?: str ≤ 120, features: FeatureCollection}`. The API fills `features` from the dataset selection (all features when none is selected), each with its properties plus `_name` and `_fid`; line of sight leaves out the points that are its observers. Only then does it set `from_dataset: true` (never taken from the request), and each item's `index` is the dataset feature number; for features sent in the request `index` is their position in `batch.features` (0…n−1), whatever their ids, and so it is for dataset features whose ids are not all different integers. Either way `index` is unique per item. `geometry` becomes the extent of the features (it only picks the data pack). At most 1,000 features, line of sight 500 (`BATCH_FEATURE_LIMITS`); more → 422 `too_many_features`; another kind → 422 `invalid_params`. `batch` is not a non-default parameter: it changes nothing about how each feature is assessed. `AnalysisInfo` responses (create, list, get, cancel) show `batch.feature_count` in place of `batch.features`, so a listing never carries up to 1,000 features per run; the JSON export (`?format=json`) and the brief bundle's `analyses/NN_<kind>.json` keep the full `params`.
- **Run:** the module runs once per feature with the request's parameters and only the geometry replaced (a survey keeps the polygonal part, or the line of a corridor survey). The items share the parameter objects, so the zone cache, the aerodrome index and the raster readers are built once. An item that fails keeps `error_code` and `error` (a module error's own message; anything else is logged and becomes `internal_error`, "The analysis failed unexpectedly for this feature."); the others go on. Cancel stops at the next checkpoint. No item starts after `BATCH_TIME_BUDGET_S` = 600 s: the rest are `skipped` with `error_code: batch_time_budget` ("Not run (time budget)"), so a run ends inside the production `JOB_TIMEOUT_S` (900 s).
- **Result** (`BatchResult`): `batch: "per_feature"`, `kind`, `key_property`, `feature_count`, `ok`, `errors`, `skipped`, `counts` (verdict, label, tone, count), `items` (`index`, `key`, `name`, `status` ok / error / skipped, `verdict`, `values` (`BATCH_FIELDS` of the kind), `issues`, `error_code`, `error`, `attributes`), `summary`, `method`, `disclaimer`, `issues` (each item's notes merged, one line per code with the number of features), `layers.features` (at most 200,000 vertices; features past it drawn as a point, counted in `points_only`).
- **Verdicts:** airspace Red zone · Permission required · Limited · Not assessed · Free (a data gap is never Free); ground risk iGRC N or Beyond Table 2; survey 1 / 2 / 3 / 4 or more batteries; environment power line or mast inside / near / none / not checked; line of sight Not visible / Partly hidden / Fully visible / Not assessed. Labels and tones come from `counts`, so the API, CSV, brief and web app use the same words.
- **Exports and brief:** CSV table `features` ("One row per feature, with its attributes": result columns, then each attribute, as `attr.<name>` when the name is already a result column; dict and list values as JSON text); GeoJSON and KML of `layers.features` with the verdict properties (KML styled by tone, `key` as the placemark name); no H3 layers. The brief part gives the counts and the feature table (most severe first); the bundle holds `tables/NN_<kind>_features.csv`. No protobuf message: `result.batch == "per_feature"` marks the envelope.

**Evidence brief** (`brief.py`, [`features/brief.md`](features/brief.md))
- `build_brief(sources, operation, packs=…)` is stateless. Sources are put in a canonical order (kind, then finish time, then id). Sections, always all and in this order: Operation, Airspace, Heights and terrain, Obstacles, Ground risk, Environment, Line of sight, Survey and spray effort, Flight audit, Drone show, eVTOL siting and corridor; a section without an analysis is `not_assessed` with its reason; a data gap makes it `partial`.
- Cross-checks (`warnings`): synthetic data, several datasets, separate areas, several runs of one kind, an aircraft larger or faster than the ground-risk analysis assumed, an environment date other than the planned window, `non_default_parameters` and `datapack_changed` (below).
- **Non-default safety parameters** ([`features/evidence-integrity.md`](features/evidence-integrity.md#non-default-safety-parameters-weak-09)): any analysis with `include_datapack_zones: false`, `zone_rules` that differ from every named rule set of `zone_profile` (canonical JSON; a preset `zone_profile` itself is not flagged, for airspace, flight audit and eVTOL alike) or `user_zones`; a cell cap below its default (airspace `max_grid_cells`, ground risk `max_cells`); a show `dynamics_window_s` or `airborne_threshold_m` above 1; for airspace also zones from datasets (`zone_dataset_ids`, with the number of zones), aerodromes marked not operational (`aerodrome_operational`), a planned window when time-limited zones were in play, `obstacle_search_m` < 300, `vertical_clearance_m` < 30, `min_agl_m` < 30, `profile_spacing_m` > 30 or a given `takeoff_elevation_m`; for a flight audit also `band_ceiling: permission`, which loosens the evidence ([`features/choices.md`](features/choices.md#records-and-the-brief)). Each gets a warning cross-check `non_default_parameters` naming the parameters; airspace, flight-audit and per-feature parts also get the banner "Run with non-default safety parameters: …". Defaults and stricter values are not flagged: `unverified_band: exclude` is stricter, so the eVTOL part names it in its "Zone rules" fact but it is not flagged.
- Zone parts: the "Zone rules" fact (a run without `zone_profile` reads "Conservative illustrative (before zone profiles)"; a non-default assessment choice is added as its note), the tables "Aerodrome bands reached" (airspace), "Aerodrome bands flown" and "Flown in yellow airspace above a band's ceiling (permission required)" (flight audit), "Zones that do not apply", and "Verify on Digital Sky" in the siting table.
- `datapack_changed` (warning): the installed pack's `content_sha256` differs from the `datapack_sha256` an analysis recorded.
- Provenance (`brief_schema_version` 1.1): versions, per analysis `params_sha256`, `result_sha256`, `input_sha256` (the stored upload's SHA-256), `datapack_sha256`; per data pack its layers' sources, licences, `sha256` and `source_url` (the Overpass server that answered, for OSM layers) and the pack's `content_sha256`; `operation_sha256`; `signing_key_id` (null when the instance does not sign) and `signature_note` (what the hashes and the signature prove); and `brief_sha256` of the canonical brief without `provenance.generated_utc` and `provenance.brief_sha256`. Canonical JSON = `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`.
- Synthetic watermark "SYNTHETIC DEMO DATA — not evidence" when an analysis of a kind that reads pack layers used a synthetic pack.

## 4. API (FastAPI) — `api/`

- **Settings** (pydantic-settings, `.env`): see `.env.example`. Left empty, these follow `APP_ENV` (development / production): `REQUIRE_OWNER_TOKEN` off / on, `ALLOW_GLOBAL_LISTING` on / off, `UPLOAD_TTL_HOURS` 0 / 72, `CONTEXT_RATE_LIMIT_PER_HOUR` 0 / 3600, `READ_RATE_LIMIT_PER_HOUR` 0 / 36000, `STORAGE_QUOTA_MB` unlimited / 5000, `UPLOAD_MB_PER_DAY` unlimited / 1000, `JOB_TIMEOUT_S` none / 900 s, `INLINE_PROCESS_MAX_MB` 5 / 0 (every upload queued), `API_DOCS` on / off. Mode-independent: `MIN_FREE_DISK_MB` (500), `WORKER_MEMORY_LIMIT_MB` (`RLIMIT_AS` of `python -m api.worker`, POSIX only; empty = off), `ALLOWED_HOSTS` (empty = any `Host`), `BRIEF_SIGNING_KEY_FILE` (PEM Ed25519 private key; empty = bundles unsigned; a missing or unusable key stops start-up). `FLIGHTMD_API_URL` must be an absolute http(s) base URL without user name, password, `?query` or `#fragment`, or start-up fails. Production also requires explicit `CORS_ORIGINS` (no `*`).
- **Database:** SQLAlchemy 2 (sync), SQLite by default (`sqlite:///./data/airbrief.db`, every connection with `PRAGMA secure_delete=ON`), PostgreSQL via `DATABASE_URL`.
- **Startup:** Alembic migrations run automatically when `AUTO_MIGRATE=true` (`0001_initial`, `0002_job_heartbeat`, `0003_analysis_dataset_owner`, `0004_analysis_datapack_sha256`). In development the API logs a WARNING naming the protections that are off.
- **Tables:**
  - `datasets`: id str uuid, filename, file_type, size_bytes, sha256, status, error_code, error_message, crs, crs_source, feature_count, layer_count, bbox JSON, layers JSON, totals JSON, issues JSON, stored_path, sample, created_at, processed_at, processing_ms, claimed_by, heartbeat_at, owner_token_hash
  - `features`: id pk, dataset_id fk idx, index, layer, fid, name, geometry_type, crs, geometry JSON, geometry_wgs84 JSON, properties JSON, measurement JSON, min_lon/min_lat/max_lon/max_lat, altitude_mode, times JSON, time_origin_utc
  - `analyses`: id, kind, status, progress, stage, dataset_id nullable, feature_indices JSON, params JSON, result JSON, error_code, error_message, datapack, datapack_synthetic, datapack_sha256, backend, cancel_requested, claimed_by, heartbeat_at, owner_token_hash, by_dataset_owner (bool, not null; rows from before `0003` are set true), created_at, started_at, finished_at, duration_ms

**Endpoints.** Errors always come back as `{"error": {"code", "message", "details"}}`.

| Method & path | Notes |
|---|---|
| GET `/health` | `{status, version, schema_version, db, datapacks:[names], native_backend}` |
| POST `/api/files/` | multipart `file` (+ optional form `crs_override`, `origin` = "lon,lat,alt"). Files ≤ `INLINE_PROCESS_MAX_MB` (empty: 5 MB in development, 0 in production, so there every upload is queued) are processed in the request → 201 FileInfo `COMPLETED`/`FAILED`. Larger files → 202 `PENDING`. 507 `storage_full` when the upload would break `STORAGE_QUOTA_MB` or `MIN_FREE_DISK_MB` (checked from `Content-Length` and again per streamed chunk). |
| GET `/api/files/` | `?limit=&offset=` (`?ids=` when global listing is off) → `{items: FileInfo[], total}` |
| GET `/api/files/{id}/` | FileInfo |
| GET `/api/files/{id}/measurements/` | `?limit=100&offset=0&geometry_type=&status=` → `{file_id, items:[{feature_index, layer, name, geometry_type, crs, measurement}], total, limit, offset, totals}` (`status` is filtered in SQL) |
| GET `/api/files/{id}/features/` | GeoJSON FeatureCollection (WGS84). Properties = source properties + `_index, _layer, _name, _type, _status, _area_m2, _length_m` (timed tracks also `_time_points`, `_duration_s`). Features with an unknown CRS have `geometry: null`. |
| GET `/api/files/{id}/features/{index}/` | FeatureRecord |
| GET `/api/files/{id}/export/?format=csv\|geojson\|kml\|ed318[&ed318_limits=]` | download (KML without XML-illegal characters); `ed318` = the dataset's polygons as an ED-318 zone file, read like a zone dataset, so a polygon without a zone column is red (`PROHIBITED`) (422 `no_zone_polygons` without polygons, 422 `invalid_params` with a feature that is not a valid zone); `ed318_limits` as for analyses (below) |
| DELETE `/api/files/{id}/` | 204. With owner tokens enforced it needs the dataset's `X-Owner-Token` (403 `owner_token_required` / `owner_token_invalid`) |
| GET `/api/samples/` | `[{name, title, description, filename, module}]` |
| POST `/api/samples/{name}/` | creates and processes a dataset from the sample, always in the request → 201 FileInfo; same storage check (507) |
| GET `/api/samples/{name}/download/` | the sample file |
| POST `/api/analyses/` | `{kind, dataset_id?, feature_indices?, params}` → 202 AnalysisInfo. Optional header `X-Owner-Token` (≤ 256 chars) = the dataset's owner token: the run then counts as the owner's (`by_dataset_owner: true`, constant-time check). Without it anyone with the dataset id can still run an analysis on it, with `by_dataset_owner: false` |
| GET `/api/analyses/` | `?dataset_id=` (or `?ids=`), `limit` 1–100 (default 100; every item carries its full result), `offset` → `{items, total}`. With owner tokens enforced (`REQUIRE_OWNER_TOKEN`, on in production) `?dataset_id=` lists only the owner's analyses, and `&ids=` adds the caller's own analyses on that dataset; otherwise `?dataset_id=` lists every analysis on it |
| GET `/api/analyses/{id}/` | AnalysisInfo |
| POST `/api/analyses/{id}/cancel/` | 202 (owner token as for DELETE) |
| DELETE `/api/analyses/{id}/` | 204. With owner tokens enforced it needs the analysis' own `X-Owner-Token` |
| GET `/api/analyses/{id}/export/?format=json\|geojson\|kml\|csv\|ed318[&table=][&ed318_limits=]` | see "Analysis exports" below; 409 `analysis_not_finished` until it completes |
| GET `/api/analyses/{id}/export/tables/` | `[{key, label, rows, multi, default}]`: the `?table=` values of the CSV export |
| POST `/api/briefs/` | `{analysis_ids[1..12], operation{}}` → `Brief` JSON |
| POST `/api/briefs/bundle/` | same body → `airbrief_brief_<12 hex of brief_sha256>.zip` |
| GET `/api/context/` | `?bbox=minLon,minLat,maxLon,maxLat&layers=&limit=&profile=` → context layers (below) |
| GET `/api/places/` | `?q=&limit=10` → `[{label, kind, ident, lon, lat, detail, datapack}]` (below) |
| POST `/api/geometry/parse/` | `{text, shape?}` → `{geometry, kind, source, vertices, issues}` (below) |
| POST `/api/flightmd/fetch/` | `{url}` → the FlightMD report JSON (SSRF guard, below: https on 443, public IPs, 5 MB cap, timeout, at most 32 levels of nesting) |
| GET `/api/meta/` | `{version, schema_version, modules:[{kind,title,description,requires,optional,params_schema}], cameras, uas_presets (with `volume` terms), zone_rules (the rules of default_zone_profile), zone_profiles, default_zone_profile, datapacks:[{name,bbox,synthetic,layers,version,created_utc,attribution,integrity,content_sha256}], limits:{max_upload_mb,...}, flightmd:{configured,url}, global_listing, owner_tokens_required, app_env, signing}`. `integrity` is `verified` \| `unverified` \| `mismatch` and `content_sha256` is the manifest's value; `flightmd.url` is the origin of `FLIGHTMD_API_URL` only (`scheme://host[:port]`, never the path); `limits.inline_process_max_mb` is the effective value (0 in production when unset); `app_env` is `development` \| `production`; `signing` is `{algorithm: "Ed25519", key_id, public_key}` (raw key, base64) or `null` when bundles are not signed. `zone_profiles` maps `dgca_2021` and `conservative_illustrative` to their `ZoneRules` (each with its `reference`); `default_zone_profile` is `dgca_2021`, and `zone_rules` is its rules |

**FileInfo:**
```
{id, filename, file_type, size_bytes, status, feature_count, layer_count, crs, crs_source,
 bbox:[4]|null, layers, totals, issues, error:{code,message}|null,
 created_at, processed_at, processing_ms, sample, owner_token (creation response only)}
```

**AnalysisInfo:**
```
{id, kind, status, progress, stage, dataset_id, by_dataset_owner, feature_indices, params, result|null,
 error|null, datapack, datapack_synthetic, backend, created_at, started_at, finished_at, duration_ms,
 owner_token (creation response only)}
```
`by_dataset_owner` is true only for an analysis created with its dataset's owner token (always false without a dataset; analyses from before migration `0003` count as the owner's). The web app labels the others "Added by another visitor" and never ticks them in the brief by default ([`features/access.md`](features/access.md)).

**Resolving analysis inputs (`api/services/analyses.py`).** First, what the request sent in `params` is held to `check_json_limits(params, max_vertices=MAX_PARAM_VERTICES)`: at most 32 levels of nesting anywhere, and at most 100,000 positions per GeoJSON object (a dict whose `type` is a GeoJSON type, counting the `coordinates` of everything under it); either cap → 422 `too_complex` (`details.limit`). The cap is above the 10,000 of typed or pasted text because the web app copies dataset polygons into params (training area, show box). Geometry the server takes from the dataset is not capped this way: it already passed the upload limits. Then, applied only to parameters the request leaves out:
- **airspace, ground_risk, environment, survey, evtol_siting:** `geometry` = the union of the selected features' WGS84 geometries (all features if none are selected; 2D and 3D features together are unioned in 2D). survey and siting use the polygonal part only; a `survey` with `coverage: "corridor"` keeps the union of the **line** parts only (its centreline; 422 `geometry_type_not_supported` without a line, or when the lines do not join into one centreline). A survey is refused with `area_too_large` above 5,000 battery blocks or 20,000 corridor line pieces (`MAX_SURVEY_BLOCKS`, `MAX_CORRIDOR_LINE_PIECES`).
- **line_of_sight** (`api/services/line_of_sight.py`): `observers` = the point features whose `role` property is `pilot`/`observer`/`gcs`, or whose name matches (whole words, any case) `GCS`, `ground station`, `ground control station`, `base station` (ground station), `observer`, `spotter`, `VO` (observer), `pilot`, `RPIC` (pilot); a numeric `height_agl_m` property (0–500 m) is kept. `geometry` = the selection without those points. An explicit `observers: []` means none: the pilot is assumed at the route start or area centre (`observer_assumed`).
- **evtol_corridor:**
  - origin/destination = the first two selected point features (or the first two points in the dataset)
  - `study_area` = the first polygon, if any
  - names come from the feature names
- **show:**
  - `tracks` = the features that have `times`, as `{id: name or fid, times, coordinates: geometry_wgs84 coords}`
  - `absolute` tracks add `altitude_reference: "absolute"` and the take-off pad `origin`; mixed modes → 422 `mixed_altitude_modes`
  - tracks without heights (a GPX without `<ele>`, a CSV without a height column) are refused with 422 `no_tracks`, naming them: a show cannot be checked in 2D
  - a CSV `elevation`/`ele` column is read as height above sea level (like GPX `<ele>`); a `# altitude_mode=` directive overrides it
  - `show_box` = the polygon whose name matches `/show\s*box/i`
  - `audience` = the polygon matching `/audience/i`
- **flight_audit:**
  - `params.report`, or `params.report_url` (fetched server-side with the SSRF guard, then removed from the params; it also becomes `flightmd_url` when that is left out and the link passes the rule below);
  - `flightmd_url` (the "Open in FlightMD" link every viewer sees) must be `https`; outside production plain `http` to `localhost` or a loopback address (`127.0.0.1`, `[::1]`) is accepted too. Anything else → 422 `invalid_params` with `details.field = "flightmd_url"`;
  - otherwise, with a dataset (`api/services/tracks.py`): `track` = the first **timed** track among the selected features (`feature_indices`), else the first in the dataset (KML `gx:Track`, GPX `trk` with `<time>` on every point, CSV with a time column); 422 `no_tracks` without one. `training_box` (only when left out; `null` switches it off) = the first polygon or closed line named like `training`, `area`, `boundary` or `geofence` but not like an area to stay out of (`no-fly`, `NFZ`, `restricted`, `prohibited`, `danger`, `exclusion`, `keep out`, `audience`, `red zone`); a timed track is never a training area.
- **batch** (`api/services/batch.py`, any of the five batch kinds): `batch.features` = the selected features with their properties when the request leaves it out (422 `invalid_params` without a dataset); `geometry` = their extent; `too_many_features` is checked here, before anything runs.
- **Zone profile** (`api/services/zones.py`): airspace, flight-audit and eVTOL runs store `zone_profile` explicitly (`custom` when `zone_rules` were sent, else `dgca_2021`), so a later change of the default never changes what a stored run meant.
- **Assessment choices** (`ASSESSMENT_CHOICES`, same place and reason): a flight audit stores `band_ceiling` (`exceedance` when left out) and an eVTOL siting or corridor run `unverified_band` (`permission`). Another value is 422 `invalid_params`; other kinds refuse the fields as unknown (422).
- **Zones from datasets** (airspace): `params.dataset_zones` is always removed first, then filled from `zone_dataset_ids` (at most 10, each a `COMPLETED` dataset read by its id like any read): its polygon features (of `zone_mappings[id].layer` when set) whose bounding box comes within 0.01° (≈ 1 km) of the checked geometry's, read as the mapping says, at most 5,000 in all. Errors: 404 `not_found`, 409 `dataset_not_ready`, 422 `invalid_params` (no polygons, a bad mapping or value, with the dataset and feature index), 422 `too_many_zones`.

**Data pack:** the registry's `select(bbox_of_inputs, preferred=DEFAULT_DATAPACK)`.

**Context layers (`GET /api/context/`, `api/routers/context.py`).** Response `{datapack, synthetic, bbox, layers: {name: FeatureCollection}, truncated, counts, limit, attribution}`. Layers (`CONTEXT_LAYERS`): `aerodromes` (reference points), `rings` (the aerodrome bands of the rule set `profile`, DGCA 2021 by default, the polygons the airspace module draws; the web app passes the Rules of the open airspace, flight-audit or eVTOL panel), `zones` (pack zones), `obstacles` (tallest first when cut), `water` (largest first when cut), `coverage` (the pack's bbox). `profile` is `dgca_2021` (default) or `conservative_illustrative`; anything else (also `custom`, which exists only inside an analysis) → 422 `invalid_params`; it is part of the ETag. Features touching the view are returned whole. The view is at most 2° × 2° (422 `area_too_large`); at most 5,000 features (`limit`, default 5,000) and 100,000 vertices per layer, at least one feature kept, with `truncated` and the full `counts`; unknown layers → 422 `invalid_params` with the allowed list. No pack covering the view → `datapack: null` and empty layers; a covering pack in `mismatch` → 503 `datapack_integrity`. Weak ETag per pack version, app version and request (304 on `If-None-Match`), `Cache-Control: public, max-age=300`.

**Place search (`GET /api/places/`).** `q` 1–200 characters, `limit` 1–25. Typed coordinates first (`kind: "coordinates"`, read with `parse_coordinates`), then aerodromes of every installed pack ranked: exact ident / IATA → ident / IATA prefix → name prefix → a name word starts with the query → municipality → substring of name → substring of municipality → every query word starts a word; ties by aerodrome size, then name; several packs merged by that rank (real packs before synthetic ones), an aerodrome in several packs listed once. A pack in `mismatch` is skipped, so the other packs still answer.

**Geometry parsing (`POST /api/geometry/parse/`).** `text` (≤ 100 kB as read by the core) and `shape` (`auto` | `point` | `line` | `polygon`; `auto`: 1 point → Point, 2 → LineString, ≥ 3 closed → Polygon, ≥ 3 open → LineString with a `read_as_line` note). Reads GeoJSON (geometry, Feature, FeatureCollection, or a bare `[lon, lat]`), WKT/EWKT (`SRID=4326;` only) or typed coordinates. At most 10,000 vertices (`too_complex`); unreadable or empty → `invalid_geometry`; values beyond ±180°/±90° → refused as projected; Z dropped (`z_dropped`); self-intersections repaired (`geometry_repaired`). Typed coordinates: decimal degrees, hemisphere letters, DMS, a decimal comma where it cannot be the separator, `[lon, lat]` in brackets; without hemispheres a value beyond ±90° is the longitude, else latitude, longitude with `ambiguous_order`; points proving different orders → `invalid_coordinates`. The same rules run in the browser (`frontend/lib/coords.ts`).

**Analysis exports (`api/services/exports.py`).** Every format carries the evidence notice ("Evidence, not authorisation…") and, for analyses that read a synthetic pack, the watermark "SYNTHETIC DEMO DATA — not evidence".
- **json:** the `AnalysisEnvelope`, with `notice` and `watermark`.
- **geojson:** every result layer plus the H3 results as polygon layers (`grid` of airspace; `density` and `hotspots` of ground risk, square cells for the operational volume; `cells` of eVTOL siting; `noise` of the eVTOL corridor), `_layer` on each feature, at most 50,000 features with the cut recorded in `airbrief.truncated_layers`.
- **kml:** the same layers as 2D evidence placemarks (heights stay attributes), every value in `ExtendedData`, XML-illegal characters dropped.
- **csv:** UTF-8 with BOM, text that would start a spreadsheet formula prefixed with a quote (header cells too: a per-feature table names columns after the upload's attributes), a last `notice` column. `?table=` picks one of the kind's tables (default: the first); multi-table files name each row's table in a leading `table` column. 422 `invalid_params` for an unknown table. A per-feature result has the one table `features` (§3).
- **ed318** (airspace and flight audit only, else 422 `invalid_params`): the result's `zones` layer as an ED-318 FeatureCollection (`to_ed318`): aerodrome bands (two volumes for a band that changes zone at its ceiling) and the user, dataset and pack zones touched, with the validity, limits, `schedule`, `restriction`, `authority`, `reason` and `source_format` their zones-layer features carry; `<name>_zones.ed318.geojson`, `application/geo+json`. The notice and watermark go in a top-level `airbrief` member (`{notice, watermark, analysis_id}`), which ED-318 readers ignore. A flight audit without a zones layer gives an empty collection; an airspace result without one (a per-feature run) is 422. The dataset export (`GET /api/files/{id}/export/?format=ed318`) is the user's own polygons and has no such member. `ed318_limits` (both exports; [`features/choices.md`](features/choices.md)) picks where the vertical limits go: `geometry` (default, each geometry's `layer`) or `properties` (`lower`, `lowerReference`, `upper`, `upperReference`, `uom` in the feature properties, no `layer`); `parse_ed318` reads both to the same zones. Another value, or `ed318_limits` with another format, is 422 `invalid_params`. The bundle's `zones.ed318.geojson` always uses `geometry`.

  | Kind | Tables (`?table=`) |
  |---|---|
  | airspace | `all` (multi: `zone`, `obstacle`, `grid_cell`, `profile_point`, `aerodrome`, `below_min_agl`, `above_ceiling`), `zones`, `obstacles`, `grid`, `profile`, `aerodromes`, `height_segments` |
  | ground_risk (operational volume) | `all` (multi: `volume`, `volume_term`, `grid_cell`, `assembly`), `volume`, `terms`, `grid`, `assemblies`, `hotspots` |
  | ground_risk (footprint method) | `cells`, `hotspots` |
  | flight_audit | `all` (multi: `profile_point`, `exceedance`, `above_band_ceiling`, `area_exit`, `distance`, `zone`), `profile`, `exceedances`, `above_band_ceiling` (`band_ceiling: permission` runs only), `area_exits`, `distance`, `zones` |
  | line_of_sight | `all` (multi: `observer`, `radio_path`, `radio_profile`, `route_hidden`), `observers`, `radio_paths`, `radio_profile`, `route_hidden` |
  | environment | `emi`, `daylight`, `sun` |
  | evtol_siting | `candidates`, `cells` |
  | evtol_corridor | `profile`, `obstacles`, `zones`, `noise`, `height_segments` |
  | show | `events` (conflicts, violations, pad conflicts), `drones` |
  | survey | `blocks` |

- The survey `lines` layer is **never exported** in any format (`EXPORT_EXCLUDED_LAYERS`).

**Briefs (`api/routers/briefs.py`, `api/services/briefs.py`).** Stateless; nothing is stored. Body `{analysis_ids: [1..12], operation: {operator ≤ 200, reference ≤ 120, aircraft{preset, name, takeoff_mass_kg, max_dimension_m, max_speed_ms}, planned_start_utc, planned_end_utc, utc_offset_min, pilot_position [lon, lat], notes ≤ 2,000}}`. Errors: 404 `not_found` (an unknown id), 409 `analysis_not_finished`, 422 `invalid_params` (no ids, more than 12, an unknown preset, a window ending before it starts, a position outside WGS84), 413 `bundle_too_large`. The bundle holds `brief.html` (no scripts, no external requests), `brief.json`, `provenance.json` (size and SHA-256 of every other entry), `evidence.geojson`, `evidence.kml`, `zones.ed318.geojson` (when the brief has an airspace check or flight audit: the bands and zones they were judged against as one ED-318 file, [`features/zones-followups.md`](features/zones-followups.md)), `analyses/NN_<kind>.json` and `tables/NN_<kind>_<table>.csv`; caps: 100,000 rows per CSV table and 200 MB uncompressed. Whoever knows an analysis id can read it, so a brief holds exactly the ids it is given.

With `BRIEF_SIGNING_KEY_FILE` set, the bundle also holds `signature.json`: `{"algorithm": "Ed25519", "key_id", "signed_file": "provenance.json", "signature": "<base64>"}`, a signature over the exact bytes of `provenance.json`. `signature.json` is not listed in `provenance.json`; every other entry is, so the signature covers the whole bundle. `key_id` = the first 16 hex characters of the SHA-256 of the raw 32-byte public key, published with the key in `GET /api/meta/` `signing`. Check a bundle with `python -m airbrief_core.verify <bundle.zip> [--public-key <base64, or a file with base64 or a PEM public key>]`: it reads the zip in memory with the upload archive guards (at most 1,000 files, 256 MB), re-hashes every listed entry, refuses missing, unlisted and duplicate entries, and with a key checks algorithm, key id and signature (a missing `signature.json` then fails); exit status 0 or 1. `python -m airbrief_core.verify --new-key <path>` writes a new PKCS#8 PEM key (never overwrites; owner-only on POSIX). Details: [`features/evidence-integrity.md`](features/evidence-integrity.md).

**Jobs:**
- A `ThreadPoolExecutor(WORKER_THREADS)` runs them in-process.
- A job is claimed by a compare-and-set `UPDATE … WHERE status='PENDING'`.
- `python -m api.worker` runs a standalone poller with the same claim logic.
- The process running a job refreshes its `heartbeat_at` every 10 s. Only `PROCESSING`/`RUNNING` rows whose heartbeat is missing or older than 60 s are marked `FAILED` (`interrupted`), at start-up and periodically, so one process never fails another's live jobs. Shutting down records running analyses as interrupted, not cancelled.
- An analysis running longer than `JOB_TIMEOUT_S` is stopped at its next checkpoint (the cancel event) and recorded as `FAILED` with error code `job_timeout`. Dataset processing has no checkpoints and is bounded by the ingest limits instead. A per-feature run starts no feature after 600 s, so with `JOB_TIMEOUT_S` well above 600 s (900 s in production) it ends with a result; below 600 s a large run is stopped and keeps nothing.
- When the analysis reads a data pack, the orchestrator refuses a `mismatch` pack (`datapack_integrity`) and adds the warning Issue `datapack_unverified` for a pack without hashes. The envelope's `datapack_sha256` is the pack's `content_sha256` when the pack was verified, else `null`; it is stored in `analyses.datapack_sha256`.

**Request guard** (`api/ratelimit.py`, before any route; every refusal is logged, see below):
1. Development mode answers only loopback and private clients (LAN, Docker bridge, link-local, CGNAT): a public client address → 403 `development_mode_local_only`. The address is the rate limiter's (`TRUSTED_PROXY_HOPS` respected); with no trusted hops the last `X-Forwarded-For` entry also counts.
2. `POST`/`PUT`/`PATCH`/`DELETE` under `/api/` with an `Origin` header that is not in `CORS_ORIGINS` → 403 `cross_site_request`. Requests without `Origin` pass. A single-domain deployment must list its own origin, because browsers send `Origin` on same-origin `POST`s.
3. Body size: 413 `file_too_large` / `request_too_large` from `Content-Length` and from the bytes actually received.
4. Uploads whose `Content-Length` does not fit the storage → 507 `storage_full`.
5. **Rate limit:** an in-memory sliding window per client on `POST` and `DELETE` under `/api/` (`RATE_LIMIT_PER_HOUR`), including `/api/geometry/parse/` and the brief endpoints. `GET /api/context/` and `/api/places/` share a second window (`CONTEXT_RATE_LIMIT_PER_HOUR`: unlimited in development, 3,600 an hour in production when unset) on top of their own caps. Every other GET under `/api/` has a third window (`READ_RATE_LIMIT_PER_HOUR`: unlimited in development, 36,000 an hour in production when unset). The client key is the IPv4 address, or the /64 of an IPv6 address (IPv4-mapped addresses count as IPv4). Each window keeps at most 100,000 keys and forgets the least recently seen beyond that. Per process: several API processes count separately.

With `ALLOWED_HOSTS` set, Starlette's `TrustedHostMiddleware` runs before all of this and answers other `Host` headers with `400 Invalid host header`. `/docs` and `/redoc` exist only when `API_DOCS` is on (default: development only); they load pinned `swagger-ui-dist@5.33.1` and `redoc@2.5.4` from jsDelivr under a CSP that allows only those files (and Swagger's inline start-up script, by its hash). `/openapi.json` is always served. In production every response carries `Strict-Transport-Security: max-age=31536000; includeSubDomains`.

**Security log:** each refusal (every 403, 413, 429 and 507, and every `url_not_allowed`) writes one WARNING line on the `airbrief.security` logger: `<code> <status> client=<12 hex of a salted SHA-256 of the address> path=<path, ≤ 200 chars, escaped onto one line>`. No query strings, headers, tokens or bodies.

**Outbound fetch (SSRF guard, `api/security.py`)**, for `POST /api/flightmd/fetch/` and `report_url`, applied to the URL and to every redirect target (at most 3):
- `https` on port 443 only, a host name, no user name or password, at most 2,000 characters, no spaces or control characters.
- Every resolved address must be public: no private, loopback, link-local, multicast, reserved, shared or unspecified address; NAT64 (`64:ff9b::/96`, `64:ff9b:1::/48`) and IPv4-compatible (`::/96`) addresses always refused; IPv4-mapped and 6to4 forms refused when the IPv4 address inside is not public; Teredo always. The connection goes to the checked address, with TLS verification.
- The host of `FLIGHTMD_API_URL` is the one exception (http, private address and its own port allowed), but on it only `GET {FLIGHTMD_API_URL}/report/{id}` is fetched: `id` matches `[A-Za-z0-9_-]{1,128}`, one trailing `/` allowed, no query. Share links `https://<site>/report/<id>` are rewritten to that path when the API URL is set.
- Responses: at most 5 MB, `FLIGHTMD_FETCH_TIMEOUT_S` per request (each redirect gets its own), JSON nested at most 32 levels deep (422 `too_complex`), and only a valid FlightMD report is passed on (422 `report_invalid`).
- Clients learn which rule or stage failed and nothing else; the host, addresses, exception text and HTTP status go to the `airbrief.api.fetch` log at INFO (URL without its query string):

  | Code | HTTP | `details` |
  |---|---|---|
  | `url_not_allowed` | 422 | `{"rule": …}`: `length`, `characters`, `syntax`, `scheme`, `credentials`, `host`, `port`, `path` (the FlightMD API host, but not `…/report/{id}`), `private_address` |
  | `fetch_failed` | 502 | `{"stage": …}`: `resolve`, `connect` (no connection, TLS failure or timeout), `http_status`, `too_large`, `not_json`, `redirects` |

**CRS input** (`crs_override`, `.prj` files, a GeoJSON legacy `crs` member): PROJ strings with `+nadgrids`, `+geoidgrids`, `+grids`, `+xy_grids`, `+z_grids`, `+init=`, `+file=`, `+model=`, the operations `hgridshift`, `vgridshift`, `xyzgridshift`, `gridshift`, `deformation`, `tinshift`, `defmodel`, any `@`, and the WKT keywords `PROJ4_GRIDS`, `GRIDS`, `PARAMETERFILE` (also after parsing, e.g. a PROJJSON bound CRS) → 422 `invalid_crs` with `details = {crs: <the text, cut at 200 chars>, rule: "file_reference"}`. `+nadgrids=@null` is allowed (GDAL's EPSG:3857 WKT carries it). A refused `.prj` or `crs` member leaves the layer's CRS unknown with an issue. PROJ's own error text is never returned.

**List bounds in parameter models** (422 `invalid_params` above them): `zone_rules.rules` 50, each rule's `aerodrome_types` 20 and `rings` 20, `user_zones` 200, show `tracks` 5,000 with `times` and `coordinates` 500,000 each, flight-audit `track.coordinates` and `track.times` 500,000, ground-risk `assemblies` 100, line-of-sight `observers` 10, `feature_indices` 100,000, airspace `aerodrome_operational` 200 entries (idents of 1–16 characters), `zone_dataset_ids` 10, `zone_mappings` 10 (only ids listed in `zone_dataset_ids`), `dataset_zones` 5,000, a zone's `schedule` 20, `batch.features` 1,000 (line of sight 500, `too_many_features`), `batch.key_property` 120 characters, planned window 31 days.

**Limits of zone engine v2 and per-feature runs** (fixed in code): 1,000 features per run (line of sight 500); no feature started after 600 s; 10 zone datasets and 5,000 dataset zones per analysis, read within ≈ 1 km of the checked geometry's bounding box; zone files of 5,000 zones counted after the split into volumes × periods (100 volumes and 10 periods each), 1,000,000 vertices, 32 levels of nesting; a `--bbox` pack of at most 2° × 2°.

**Error codes added by the 2026-10-19 security fixes** (details: [`features/abuse-limits.md`](features/abuse-limits.md), [`features/outbound-and-parsing.md`](features/outbound-and-parsing.md), [`features/evidence-integrity.md`](features/evidence-integrity.md)):

| Status | Code | When |
|---|---|---|
| 403 | `cross_site_request` | A state-changing `/api/` request with an `Origin` not in `CORS_ORIGINS` |
| 403 | `development_mode_local_only` | `APP_ENV=development` and the client is on a public address |
| 507 | `storage_full` | `details.reason`: `quota` (`STORAGE_QUOTA_MB`) or `disk` (`MIN_FREE_DISK_MB`) |
| 429 | `upload_budget_exceeded` | The client's uploads in the last 24 hours reached `UPLOAD_MB_PER_DAY`; `Retry-After` and `details.retry_after_s` say when the oldest hour leaves the window |
| 411 | `length_required` | An upload without `Content-Length` while `UPLOAD_MB_PER_DAY` is on |
| 503 | `datapack_integrity` | The data pack's files do not match its manifest's hashes (`mismatch`); synchronous reads such as `/api/context/` answer 503, analyses fail with this code. `/api/places/` skips such a pack |
| — | `job_timeout` | Error code of an analysis stopped by `JOB_TIMEOUT_S` (status `FAILED`) |
| 422 | `too_complex` | Analysis params or a fetched FlightMD report over the nesting or vertex caps |
| 422 | `invalid_crs` | `details.rule = "file_reference"`: CRS text that would make PROJ open files |
| — | `datapack_unverified` | Warning Issue of an analysis that read a pack without hashes |
| — | `non_default_parameters`, `datapack_changed` | Brief cross-checks (§3) |

**Error codes added by zone engine v2 and per-feature runs:**

| Status | Code | When |
|---|---|---|
| 422 | `too_many_zones` | More than 5,000 zones of the chosen zone datasets reach the checked area |
| 422 | `no_zone_polygons` | The ED-318 export of a dataset without polygons (`GET /api/files/{id}/export/?format=ed318`) |
| 409 | `dataset_not_ready` | A zone dataset is not `COMPLETED` |
| — | `invalid_zone_file` | Dataset `FAILED` with this code: an upload detected as ED-269/ED-318 has a malformed zone or is over the caps (the message names the zone index) |
| 422 | `too_many_features` | A per-feature run over 1,000 features (line of sight 500) |
| — | `batch_time_budget` | `error_code` of an item not started within 600 s, and the run's warning Issue |
| — | `perimeter_approximated`, `perimeter_from_runways`, `aerodrome_not_operational`, `zone_window_not_checked`, `zones_inactive`, `verify_official_map`, `open_airspace_above_ceiling` | Issues of airspace, flight-audit and eVTOL results |

**TTL sweeper:** when `UPLOAD_TTL_HOURS > 0`. Each sweep also re-measures the space used for `STORAGE_QUOTA_MB` (extraction scratch `work/` does not count).

## 5. Seed, samples, demo
- **`python -m seed.datapack --region bengaluru [--lite] [--name NAME] [--dest DIR]`:** real data.
  - OurAirports CSV (with `iata_code` and `scheduled_service`), filtered to the bbox + 0.3°
  - aerodrome boundaries: Overpass `wr["aeroway"="aerodrome"]; out geom;` over the bbox + 0.3°, matched to the aerodromes by `icao`/`iata` tag or by containing the reference point (the largest kept) → `aerodrome_perimeters.geojson` (`ident`, `match`)
  - OurAirports `runways.csv` of those aerodromes → `runways.geojson` (`airport_ident`, `width_m`, `length_m`, `surface`)
  - Copernicus GLO-30 COG tiles via rasterio (windowed and merged); GLO-90 with `--lite`
  - WorldPop 1 km 2020 of the region's country (window)
  - OSM via the Overpass API with mirror fallback: obstacles, POIs, water, **power** (`way["power"~"^(line|minor_line|cable)$"]`, `nwr["power"~"^(substation|plant)$"]`) and **assemblies** (`leisure=stadium`, `leisure=sports_centre` with `capacity`, `tourism=theme_park`, `amenity=marketplace`)
  - empty `zones.geojson`
- **`python -m seed.datapack --bbox MIN_LON,MIN_LAT,MAX_LON,MAX_LAT --name NAME [--iso3 XXX] [--lite] [--dest DIR]`:** the same for any area of at most 2° × 2° (`check_region_bbox`; larger → the parser refuses it). `--iso3` (ISO 3166-1 alpha-3) picks `…/Global_2000_2020_1km_UNadj/2020/{ISO3}/{iso3}_ppp_2020_1km_Aggregated_UNadj.tif` (pattern checked for IND and NPL); without it the pack has no population layer (`[population] skipped`, `population –` in the summary, exit status 0). `--iso3` without `--bbox`, or `--bbox` without `--name`, is refused.
- **`python -m seed.datapack --synthetic [--name demo]`:** a deterministic offline pack covering the sample area around Bengaluru.
  - analytic DEM and population
  - 4 aerodromes at approximate public coordinates, with IATA codes where they have one: VOBL 13.1986/77.7066 large (BLR); VOBG 12.9499/77.6682 medium; VOYK 13.1355/77.6061 medium; Jakkur 13.0781/77.5975 small; `scheduled_service` filled
  - invented boundaries for VOBL (4.8 × 2.6 km, two north–south runways), VOBG and VOYK, and only a runway for VOJK (the hull fallback)
  - seeded obstacles, POIs and lakes, and one demo restricted zone
  - a 220 kV line crossing the Bengaluru survey sample, a 33 kV feeder about 40 m east of it and a 220/33 kV substation about 220 m away
  - two assemblies: "Synthetic Kengeri stadium" (45,000 people) and "Synthetic city market" (capacity unknown)
  - `synthetic: true` in the manifest
  - A rebuilt pack is swapped in by renames (the old one moved aside first), so a locked file never leaves a half-deleted pack.
- **`python -m seed.datapack --seal <pack dir>`:** re-hashes a pack after an intended edit (for example restricted areas added to `zones.geojson`) and prints the new `content_sha256`. Without it the edited pack is a `mismatch`.
- **`python -m seed.datapack --refetch <pack dir> [--layers pois,water]`:** queries the OpenStreetMap layers of a real pack again (by default the ones its build missed, when the Overpass servers were busy), writes them in place, adds them to the manifest and seals the pack. The synthetic pack and non-OSM layers are refused.
- **`python -m seed.samples [--dest samples/]`:** writes every sample from `airbrief_core.samples` (including the Training flight GPX).
- **`python -m seed.demo_export --out frontend/public/demo [--datapack-dir data/datapacks] [--datapack demo]`:** runs the samples through the API (TestClient, temporary SQLite, the synthetic pack, built in a temporary directory unless `--datapack-dir` names an existing one) and dumps the endpoint JSON for the static demo, including `context.json` (`GET /api/context/` for the pack's bbox, cut to 2° × 2°) and `briefs/{id}.json` (`POST /api/briefs/` for each analysis alone). The export is built in a sibling folder and swapped in only when complete.

**Data-pack layout:** `manifest.json`, `dem.tif`, `population.tif`, `aerodromes.csv`, `obstacles.geojson`, `pois.geojson`, `water.geojson`, `zones.geojson`, and the optional `assemblies.geojson`, `power.geojson`, `aerodrome_perimeters.geojson` and `runways.geojson` (`LAYER_KEYS`). A pack without the last two measures every DGCA band from the reference point (`perimeter_approximated`). Pack zones are read through `zone_from_properties`, so a `zones.geojson` can carry validity, schedules and vertical limits like a zone dataset.

**Data-pack integrity** ([`features/evidence-integrity.md`](features/evidence-integrity.md#data-pack-integrity)). Both seed modes write `layers.<key>.sha256` per layer file, `content_sha256` = SHA-256 of the lines `"<sha256>  <file>\n"` sorted by file (the `sha256sum` format, `/` separators), and for OSM layers `layers.<key>.source_url` (the Overpass server that answered). The API checks a pack when it opens it and again when the manifest or a layer file changes size or mtime (hashes cached per path, size and mtime):

| `integrity` | Meaning | Effect |
|---|---|---|
| `verified` | Every listed file matches its hash and the hashes match `content_sha256` | Normal; analyses record `datapack_sha256` |
| `unverified` | No hashes (a pack built before 2026-10-19) | Used; analyses that read it get the warning Issue `datapack_unverified` |
| `mismatch` | A file edited or missing, a layer dropped from the manifest, or a hash changed | Unusable: every layer read raises `datapack_integrity` (503) |

`content_sha256` covers the layer files, not the manifest's own fields (name, `synthetic`, bbox); compare it with the value the pack's builder published.

## 6. Frontend — `frontend/`

**Stack:**
- Next.js 15.5 App Router on React 19, TypeScript strict, Tailwind 3.4
- Radix primitives with shadcn-style components in `components/ui/`
- lucide-react, cmdk, next-themes, sonner
- TanStack Query 5 and react-virtual, zustand
- maplibre-gl 6, deck.gl 9 (`@deck.gl/mapbox` `MapboxOverlay` over maplibre, `@deck.gl/geo-layers` `H3HexagonLayer`, `@deck.gl/extensions` `FillStyleExtension` for hatches)
- recharts 2, the `geist` font package
- Playwright with @axe-core/playwright

**Routes** (query-param based so a static export works):

| Route | Purpose |
|---|---|
| `/` | landing |
| `/w` | home: upload, samples, recent |
| `/w/view?d=&f=&m=&a=&t=` | workspace; also `plan=` and `sample=` (below) |
| `/w/report?d=&a=` | report; `mode=brief` opens the operation evidence brief |
| `/settings` | settings |

**Single-use URL parameters** (read once, then removed or ignored):
- `plan=pin|line|area|coordinates|paste` on `/w/view` starts that plan step once and is then removed from the URL (`components/workspace/operation-input.tsx`). The landing page's **Check a location** links to `/w/view?m=airspace&plan=pin`. Phones (< 768 px) always start the pin.
- `sample=<name>` on `/w/view?m=flight-audit` opens the flight audit on that FlightMD report sample.

**Settings and the assessment choices** ([`features/choices.md`](features/choices.md)):
- `useSettings` (`lib/store.ts`) keeps the settings in `localStorage` (`airbrief-settings`), each value checked when it is read back. The choices are `bandCeiling` (`exceedance`), `unverifiedBand` (`permission`) and `ed318Limits` (`geometry`), plus `choicesSet` (the choices the user set, in Settings or with the questionnaire; `true` saved by earlier versions reads as all three, and a save without it counts each choice that is off its default) and `choicesHintDismissed`. **Reset settings** puts the choices back to the defaults (with Undo) and keeps the note dismissed.
- `/settings#assessment-choices` has three radio groups, one sentence per option (`lib/labels.ts`). `/settings#help-me-choose` opens **Help me choose** (`app/settings/choices-questionnaire.tsx`): what AirBrief is mostly used for decides whether the permission question, the airport question or both are asked, and the ED-318 question is always asked. `recommend` and `asked` in `lib/choices.ts` map the answers. A question that is not asked or not answered gets no recommendation (`null`, shown as "unchanged (not asked)" or "unchanged (not answered)") and **Apply** leaves that choice as it is. Nothing changes until **Apply**, which stays disabled ("Nothing to apply yet") until an answer picks a choice, and **Apply** marks only the choices it applies as set (`choicesSet`).
- The flight audit and eVTOL forms take a run's choice from the setting (`ChoiceField`, `components/modules/assessment-choices.tsx`), say when the run differs, and send it as `band_ceiling` / `unverified_band`. A reopened run puts back its own recorded choice. Until a panel's own choice is set (`bandCeiling` for the flight audit, `unverifiedBand` for eVTOL), it shows the dismissible note `ChoicesHint` linking to the questionnaire; dismissing it hides it on both. The ED-318 entries of the analysis and dataset export menus read "Zones (ED-318, limits in geometry|properties)" and add `ed318_limits` to the URL. The analysis entry is offered only for a result with a `zones` layer (a per-feature run has none), the dataset entry only for a dataset with polygons (`hasPolygons`, `lib/datasets.ts`). The export menus of the dataset header, the results drawer and the report page fetch each server export (`downloadExport`, `lib/api.ts`) and save it under the API's file name; an error answer shows as a toast and is never saved as the file. All three build their entries from `components/report/export-items.tsx` (`useExportSaver`, `ExportItem`). A ctrl/cmd/shift/alt click is left to the browser (`isPlainClick`, `lib/utils.ts`). Each menu fetches one file at a time, guarded by a ref so two clicks before a re-render can't both start one: the entry being fetched shows a spinner and "Exporting…" (`aria-busy`), the others are `aria-disabled` with "Another export is still running: wait for it" (`EXPORT_BUSY_NOTE`, `lib/labels.ts`), and every click on the menu, modified and middle clicks too, is ignored until it finishes. Busy entries are told apart by an id, not the file name; an entry with neither a link nor a build is disabled.

**Tokens and styling:**
- CSS variables per PLAN §7, with a `.light` class override (next-themes, `attribute="class"`, dark by default).
- Tailwind colours map to the variables.

**Build and environment:**
- Demo mode: `NEXT_PUBLIC_DEMO=1` makes the API client read `${basePath}/demo/...` JSON.
- Static export: `NEXT_OUTPUT=export`.
- `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_BASE_PATH` configure the API URL and base path.
- Headers (`next start` only): `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy: camera=(), microphone=(), geolocation=(self), payment=()` and HSTS in production builds (`next.config.mjs`); a CSP with a per-request script nonce and no `'unsafe-inline'` scripts (`middleware.ts`; the root layout hands the nonce to the theme script, so every page renders per request). The static export has neither.

**Key files:**
- `lib/types.ts`: mirrors the Python models and the API shapes.
- `lib/api.ts`: typed client and `ApiError`.
- `lib/queries.ts`: React Query hooks (polling while PENDING/RUNNING).
- `lib/store.ts`: zustand UI state.
- `lib/url-state.ts`: the URL is the source of truth for `d`, `f`, `m`, `a`, `t`.
- `lib/format.ts`: metric/imperial formatting, local area units.
- `lib/colors.ts`: zone, risk and viridis palettes, plus the hatch atlas.
- `lib/modules.ts`: the module registry (id, label, Lucide icon, kinds) for the nine modules.
- `lib/plan.ts`, `lib/plan-store.ts`, `lib/coords.ts`: plan without a file (operations, the pin polygon, the coordinate parser).
- `lib/map-pick.ts`: "place a point on the map" requests from panels.
- `lib/brief-demo.ts`, `lib/plan-demo.ts`: the static demo's brief merge and plan endpoints.
- `components/modules/airspace/zone-inputs.tsx`, `zone-results.tsx`: the Rules select (shared by airspace, flight audit and eVTOL), the planned window, zones from layers (this browser's own files through `useMyFiles` in `lib/queries.ts`: five, then "Show all") and their column mapper, aerodrome status, band chips; `useRingsProfile` puts the open panel's Rules into the plan store for the context rings.
- `lib/batch.ts`, `components/modules/batch.tsx`: one result per feature (`BatchToggle`, `BatchResults`, `withBatch`, which wraps a module definition and adds the `batch: true` layer).
- The full file guide is `frontend/README.md`.

**Accessibility (applies everywhere):**
- No emoji.
- Icon buttons have an `aria-label` and a tooltip.
- Visible focus.
- `prefers-reduced-motion` and the in-app override are honoured.
- Skip links.
- Every map layer has a table view.
- Every point placed on the map can also be typed, or placed at the map centre with Space (WCAG 2.5.7).
- JSX text never contains a raw `'`; use `&apos;`.

## 7. Tests
- **Layout:** `tests/core/*`, `tests/api/*` (TestClient with a temporary SQLite database and a temporary data directory), and `tests/native/*` (marker `native`).
- **Markers:** `native`, `network` (skipped by default), `slow`.
- **Fixtures** (`tests/conftest.py`): `synthetic_pack(tmp_path_factory)` (built through the seed function), `sample_bytes(name)`, `api_client`.
- **Rule:** tests never touch the network.
- **Browser tests:** `frontend/e2e/*.spec.ts` (Playwright + axe; 25 spec files: `smoke`, `workflows`, `shell`, `map-input`, `shared-ui`, `pages`, `module-forms`, `evtol-shows-audit`, `plan`, `environment`, `line-of-sight`, `groundrisk`, `tracks`, `heights`, `brief`, the security specs `access`, `abuse`, `fetch`, `evidence`, `deploy`, and `zones`, `zones-followups`, `batch`, `choices`, `flightmd-schema17`). They need the API (with the synthetic `demo` pack) and the web app running.
