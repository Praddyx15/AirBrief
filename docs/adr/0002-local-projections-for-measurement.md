# ADR-0002: Per-feature local projections for measurement, with a geodesic cross-check

- **Status:** Accepted
- **Date:** 2026-10-08
- **Milestone:** M2

## Context

The core brief asks for area and length "using correct CRS handling". Inputs arrive in WGS84
(KML, GeoJSON, tracks) or in projected CRSs (shapefiles with a `.prj`). Degrees cannot be measured
directly, and every map projection distorts something:

- UTM's scale factor runs from 0.9996 on the central meridian to about 1.001 at the zone edges:
  up to about 0.1% length error and 0.2% area error.
- A single CRS for a whole file distorts features far from its centre, and files can span
  hundreds of kilometres.
- Projected inputs already have a grid, but grid sizes differ from true ground sizes by the
  projection's scale factor at that location.

## Decision

For each feature with a known CRS:

1. Transform to WGS84 and unwrap the antimeridian; take the centroid (lon0, lat0).
2. **Area:** Lambert Azimuthal Equal-Area centred on (lon0, lat0), WGS84 ellipsoid. It is
   equal-area, so it adds no area distortion at any size or latitude.
3. **Perimeter and length:** Transverse Mercator with k0 = 1 centred on (lon0, lat0). The scale
   error is effectively zero at the centre and well under 0.01% for features up to about 100 km
   across.
4. **3D length:** the sum of sqrt(dx² + dy² + dz²) in the TM projection, using the source Z values.
5. **Geodesic cross-check:** area, perimeter and length on the WGS84 ellipsoid (Karney, via
   `pyproj.Geod`). `deviation_pct` is the largest relative difference.
6. **Projected input:** additionally report `grid_area_m2` / `grid_length_m`, measured in the source
   CRS and converted to metres. Example: the `plots_shapefile.zip` sample (EPSG:32643, UTM 43N) has
   grid areas of 20,000, 22,500 and 15,000 m². Bengaluru (about 77.6°E, 13.0°N) is about 2.6° east
   of the zone's central meridian (75°E), where the UTM point scale is about 1.00057. Grid areas
   are therefore inflated by about k² ≈ 1.0011, so the true (local-projection) areas are about
   0.11% smaller than the grid areas.
7. Report `method="local-projection"`, the PROJ strings used (`area_crs`, `length_crs`), and the UTM
   zone (`utm_epsg`, `utm_zone`) as human-readable context only.
8. **Unknown CRS** (a shapefile without `.prj`): `status=skipped`, with the reason
   "CRS unknown — not measured". Guessing is unsafe.

## Alternatives considered

| Option | Why not |
|---|---|
| **Per-feature UTM** | Familiar, but up to about 0.2% area error at zone edges, and awkward across zone boundaries. |
| **One projected CRS for the whole file** | Distortion grows with distance from the centre; wrong for large or scattered files. |
| **Geodesic only** | Accurate, but meets the brief's "transform to a projected CRS" requirement less directly, and gives no planar 3D length. It is kept as the cross-check instead. |
| **Grid values only for projected input** | Grid values are what a surveyor's CAD shows, but they are not ground truth. Reporting both is clearer. |

## Consequences

- Area and length match the geodesic ground truth to within 0.01% for features up to about 100 km
  (property-tested with Hypothesis); a 1 km² square measures 1,000,000 m² at 0°N and at 60°N.
- Each feature gets its own two projections. This costs a little CPU per feature, which is
  negligible next to I/O for the supported upload sizes (at most 50,000 features).
- Users of projected data see two numbers (true and grid). The UI labels both, and `RUNBOOK.md`
  explains the ~0.11% difference for the UTM 43N sample.
