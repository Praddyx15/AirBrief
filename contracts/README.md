# AirBrief contracts

Machine-readable contracts between AirBrief and its sibling projects (UAOP, FlightMD).

```
contracts/
└── proto/                         buf v2 module root (buf.yaml)
    └── uaop/airspace/v1/          package uaop.airspace.v1
        ├── measurement.proto      Measurement, FeatureMeasurement, MeasurementTotals
        ├── airspace.proto         shared enums, result summaries, AirspaceAnalysisService
        └── events.proto           AirspaceAssessmentCompleted, FlightAuditCompleted
```

The **source of truth** for data shapes is the Pydantic models in `airbrief_core/models/`. The
protobuf messages are a stable, compact subset of those models, designed for UAOP's gRPC services
and event bus. The complete results (including GeoJSON layers) stay available as JSON through
`AnalysisEnvelope` (`schema_version` `"1.0"`), and the REST API documented in `docs/SPEC.md` §4.

## Conventions (UAOP style, buf `STANDARD` lint)

- Package `uaop.airspace.v1`, directory `uaop/airspace/v1/` (package and directory match).
- Every enum has a zero value `<ENUM_NAME>_UNSPECIFIED`, and every value is prefixed with the
  enum name in UPPER_SNAKE_CASE (`VERDICT_PASS`, `ZONE_KIND_RED`).
- RPCs take `<Rpc>Request` and return `<Rpc>Response`; services end in `Service`.
- Field names are lower_snake_case with the unit as a suffix: `_m`, `_sq_m`, `_sq_km`, `_s`,
  `_ms` (m/s), `_deg`, `_km`, `_pct`, `_cm`, `_ha`.
- Geometries travel as GeoJSON text in fields ending in `_geojson` (WGS84, `[lon, lat(, z)]`).
- Values that may be absent use proto3 `optional`, so "absent" and "zero" are distinguishable.

## Field mapping from the Python models

Names differ from the Pydantic field names only where the unit suffix is spelled out:

| Python (`airbrief_core.models`) | Protobuf |
|---|---|
| `area_m2`, `geodesic_area_m2`, `grid_area_m2`, `overlap_area_m2` | `area_sq_m`, `geodesic_area_sq_m`, `grid_area_sq_m`, `overlap_area_sq_m` |
| `length_3d_m` | `length_with_z_m` |
| `*_density_per_km2`, `footprint_area_km2` | `*_density_per_sq_km`, `footprint_area_sq_km` |
| `p95_density_per_km2` | `high_percentile_density_per_sq_km` (95th percentile) |
| `DensityCell.h3` | `DensityHotspot.h3_cell` |
| `FlightInfo.schema_version` | `FlightAuditSummary.flightmd_schema_version` |
| `Verdict.PASS` / `"pass"` | `VERDICT_PASS` (same pattern for every enum) |
| `ZoneHit.source` `"aerodrome"` / `"user"` / `"datapack"` | `ZONE_SOURCE_AERODROME` / `_USER` / `_DATAPACK` |
| `AnalysisEnvelope.{schema_version, airbrief_version, datapack, datapack_synthetic, backend, started_utc, finished_utc, duration_ms}` | `AnalysisProvenance` |

Summary messages carry counts where the Python result carries full lists that only make sense on a
map (for example `AirspaceAssessment.grid_cell_count` instead of the whole flyability grid). The
full lists are in the `layers_geojson` map of each response.

## Messages

| Message | Mirrors | Notes |
|---|---|---|
| `Measurement`, `FeatureMeasurement`, `MeasurementTotals` | `Measurement`, `FeatureRecord`, `MeasurementTotals` | Local LAEA area, local TM (k0 = 1) lengths, geodesic cross-check, grid values for projected input |
| `AirspaceAssessment` | `AirspaceResult` | Indicative only; not an official airspace map |
| `GroundRiskEvidence` | `GroundRiskResult` | Evidence *for* a SORA-style assessment |
| `FlightAuditSummary` | `FlightAuditResult` + `FlightInfo` | |
| `ShowValidationSummary` | `ShowResult` | |
| `SurveyEstimate` | `SurveyResult` | Never contains waypoints (mission generation belongs to UAOP) |
| `EnvironmentEvidence`, `EmiProximity` | `EnvironmentResult`, `EmiItem` | Sun events as UTC timestamps, daylight-profile minutes, WMM2025 field, wind margin, power lines and masts nearby; `horizontal_nt`/`total_nt` become `horizontal_intensity_nt`/`total_intensity_nt`. Evidence only, no go/no-go |
| `LineOfSightEvidence`, `LineOfSightViewpoint`, `RadioPathEvidence` | `LineOfSightResult`, `LosObserver`, `LosRadioPath` | Visible share of the area per viewpoint and combined, radio line-of-sight share, worst first Fresnel zone clearance and free-space loss per radio path, non-regulatory VLOS guidance; `observers` become `viewpoints`, `largest_hidden_patch.area_m2` becomes `largest_hidden_patch_sq_m`, `route.visible_pct` becomes `route_visible_pct`, `radio.paths` become `radio_paths`. Geometry only: no VLOS approval or link budget |

## Service

`AirspaceAnalysisService`:

| RPC | Request → Response | Python equivalent |
|---|---|---|
| `AssessAirspace` | `AssessAirspaceRequest` → `AssessAirspaceResponse` | `run_analysis("airspace", AirspaceParams)` |
| `GetGroundRiskEvidence` | `GetGroundRiskEvidenceRequest` → `GetGroundRiskEvidenceResponse` | `run_analysis("ground_risk", GroundRiskParams)` |
| `AuditFlight` | `AuditFlightRequest` → `AuditFlightResponse` | `run_analysis("flight_audit", FlightAuditParams)` |

AirBrief itself serves REST (FastAPI). The service definition is the contract for a gRPC facade
that UAOP can host at merge time, calling `airbrief_core` directly.

## Events

| Message | `schema` string | When |
|---|---|---|
| `AirspaceAssessmentCompleted` | `uaop.event.airspace.assessment_completed/1` | An airspace analysis reaches `COMPLETED` |
| `FlightAuditCompleted` | `uaop.event.airspace.flight_audit_completed/1` | A flight audit reaches `COMPLETED` |

Consumers dispatch on `schema`. The trailing number is the major version: it changes only for a
breaking change. Additive fields keep the same string.

## Working with the contracts

Install the [buf CLI](https://buf.build/docs/installation) (free, no account needed), then:

```bash
cd contracts/proto
buf lint                 # STANDARD rules; CI runs this in the `contracts` job
buf build -o /dev/null   # compile check
buf breaking --against "https://github.com/<you>/AirBrief.git#branch=main,subdir=contracts/proto"
```

`buf breaking` uses the `FILE` rule set. Generate code for your language with `buf generate` and a
`buf.gen.yaml` of your choice (none is committed, because AirBrief itself does not consume the
generated code).
