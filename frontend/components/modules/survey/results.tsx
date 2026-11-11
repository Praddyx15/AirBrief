"use client";

/**
 * Survey results: effort evidence for a mapping survey (GSD and its range over the terrain
 * relief, worst-case overlap at the highest ground, photos, time, batteries, transit, GCPs and
 * storage) or for a spraying job (spray mix, tank loads, sorties, work rates, in local area
 * units). The lines and blocks are a visual aid: nothing here is a flight plan.
 */

import * as React from "react";
import { Grid3x3 } from "lucide-react";
import { useLocalAreaUnit } from "@/lib/area-units";
import {
  LOCAL_AREA_UNITS,
  LOCAL_AREA_UNIT_LABEL,
  areaParts,
  formatArea,
  formatDuration,
  formatHeight,
  formatLength,
  formatLocalArea,
  formatNumber,
  formatSpeed,
  heightParts,
  lengthParts,
  localAreaParts,
  localAreaSize,
  localizeUnits,
  plural,
  type LocalAreaUnit,
} from "@/lib/format";
import { CAMERA_KIND_LABEL, HEIGHT_REFERENCE_SUFFIX } from "@/lib/labels";
import type { ModuleResultsProps } from "@/lib/modules";
import { useUi, useUnits, type Units } from "@/lib/store";
import { analysisResult, type AnalysisInfo, type Geometry, type Issue, type SurveyBlock, type SurveyResult } from "@/lib/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { KpiGrid, KpiTile } from "@/components/ui/kpi-tile";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { DemCaveat, IssueList, ModuleSection, useFocusMap } from "../shared";
import { paramNumber } from "../analysis-form";
import { HeightsNotAssessedBanner, heightRangeParts } from "../height-profile";
import { DEFAULTS, ESTIMATE_NOTE, SPRAY_NOTE, blockColor } from "./model";

const fmtGsd = (cm: number | null | undefined) => formatNumber(cm, { maximumFractionDigits: 2 });

/** The planned GSD and its range over the relief differ by more than display rounding. */
function hasGsdRange(r: SurveyResult): boolean {
  return r.gsd_min_cm != null && r.gsd_max_cm != null && Math.abs(r.gsd_max_cm - r.gsd_min_cm) >= 0.005;
}

/**
 * The headline of a survey over relief, e.g. "GSD 2.6–3.4 cm/px (85 m relief); worst side
 * overlap 58 %" (null when the GSD does not vary).
 */
export function gsdRangeSentence(r: SurveyResult, units: Units): string | null {
  if (!hasGsdRange(r)) return null;
  const relief = r.relief_m != null ? ` (${formatHeight(r.relief_m, units)} relief)` : "";
  const worst = r.worst_side_overlap_pct != null ? `; worst side overlap ${formatNumber(r.worst_side_overlap_pct, { maximumFractionDigits: 0 })} %` : "";
  return `GSD ${fmtGsd(r.gsd_min_cm)}–${fmtGsd(r.gsd_max_cm)} cm/px${relief}${worst}`;
}

function Warnings({ issues }: { issues: readonly Issue[] }) {
  const units = useUnits();
  if (issues.length === 0) return null;
  return (
    <Alert variant={issues.some((i) => i.severity === "critical") ? "danger" : "warn"}>
      <AlertTitle>{issues.length === 1 ? "Warning" : `${formatNumber(issues.length)} warnings`}</AlertTitle>
      <AlertDescription>
        <ul className="list-disc space-y-1 pl-4 text-fg">
          {issues.map((issue, i) => (
            <li key={`${issue.code}-${i}`}>{localizeUnits(issue.message, units)}</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

/** The battery blocks (sorties when spraying): click a row to show it on the map. */
function BlocksTable({ result }: { result: SurveyResult }) {
  const units = useUnits();
  const focus = useFocusMap();
  const setHighlight = useUi((s) => s.setResultHighlight);
  const spraying = result.mode === "spraying";
  // The clicked block stays highlighted; hovering previews another one and falls back to it.
  const [selectedBlock, setSelectedBlock] = React.useState<string | null>(null);
  const withTransit = result.blocks.some((b) => b.transit_in_m != null);
  const withGsd = !spraying && result.blocks.some((b) => b.gsd_min_cm != null && b.gsd_max_cm != null && Math.abs(b.gsd_max_cm - b.gsd_min_cm) >= 0.005);
  const columns = React.useMemo<DataTableColumn<SurveyBlock>[]>(() => {
    const noun = spraying ? "Sortie" : "Battery";
    const cols: DataTableColumn<SurveyBlock>[] = [
      {
        id: "block",
        header: noun,
        rowHeader: true,
        cell: (b) => (
          <span className="flex items-center gap-2">
            <span aria-hidden className="inline-block size-3 rounded-sm" style={{ background: blockColor(b.index) }} />
            {noun} {b.index}
          </span>
        ),
        sortValue: (b) => b.index,
      },
      { id: "area", header: "Area", numeric: true, cell: (b) => areaParts(b.area_ha * 10_000, units).text, sortValue: (b) => b.area_ha },
      { id: "lines", header: "Line length", numeric: true, cell: (b) => formatLength(b.line_km * 1000, units), sortValue: (b) => b.line_km },
      { id: "minutes", header: "Flight time", numeric: true, cell: (b) => formatDuration(b.minutes * 60), sortValue: (b) => b.minutes },
    ];
    if (spraying) {
      cols.push({ id: "litres", header: "Spray mix", numeric: true, cell: (b) => `${formatNumber(b.litres, { maximumFractionDigits: 1 })} L`, sortValue: (b) => b.litres ?? null });
    } else {
      cols.push({ id: "photos", header: "Photos", numeric: true, cell: (b) => formatNumber(b.photos), sortValue: (b) => b.photos });
    }
    if (withTransit) {
      cols.push({
        id: "transit",
        header: "Transit in / out",
        numeric: true,
        cell: (b) => `${formatLength(b.transit_in_m ?? null, units)} / ${formatLength(b.transit_out_m ?? null, units)}`,
        sortValue: (b) => (b.transit_in_m ?? 0) + (b.transit_out_m ?? 0),
      });
    }
    if (withGsd) {
      cols.push({
        id: "gsd",
        header: "GSD",
        numeric: true,
        cell: (b) => `${fmtGsd(b.gsd_min_cm)}–${fmtGsd(b.gsd_max_cm)} cm/px`,
        sortValue: (b) => b.gsd_max_cm ?? null,
      });
      cols.push({
        id: "agl",
        header: "Above ground",
        numeric: true,
        cell: (b) => heightRangeParts(b.agl_min_m, b.agl_max_m, units).text,
        sortValue: (b) => b.agl_min_m ?? null,
      });
    }
    return cols;
  }, [units, spraying, withTransit, withGsd]);
  const extraBatteries = result.batteries > result.blocks.length;
  const noun = spraying ? "sortie" : "battery block";
  return (
    <ModuleSection title={spraying ? `Sorties (${result.blocks.length})` : `Battery blocks (${result.blocks.length})`}>
      <DataTable
        data={result.blocks}
        columns={columns}
        getRowId={(b) => `block-${b.index}`}
        caption={
          spraying
            ? "Sorties: whole spray lines within one usable battery and one tank"
            : extraBatteries
              ? "Work blocks; a block with a line longer than one battery needs more than one"
              : "Work blocks, one battery each"
        }
        onRowClick={(b) => {
          setSelectedBlock(`block-${b.index}`);
          setHighlight(`block-${b.index}`);
          focus(b.polygon as unknown as Geometry);
        }}
        onRowHover={(b) => setHighlight(b ? `block-${b.index}` : selectedBlock)}
        selectedRowIds={selectedBlock ? [selectedBlock] : []}
        getRowLabel={(b) => `Show ${noun} ${b.index} on the map`}
        dense
      />
    </ModuleSection>
  );
}

function Footnotes({ result }: { result: SurveyResult }) {
  const units = useUnits();
  return (
    <div className="space-y-2">
      {result.heights_note ? <p className="text-xs text-fg-muted">{localizeUnits(result.heights_note, units)}</p> : null}
      {result.relief_m != null ? <DemCaveat dem={result.dem} /> : null}
      {result.gcp ? <p className="text-xs text-fg-muted">Ground control points: {result.gcp.reference}</p> : null}
      {result.method ? (
        <p className="text-xs text-fg-muted">
          <span className="font-medium text-fg">Method:</span> {result.method}
        </p>
      ) : null}
    </div>
  );
}

function MappingResults({ analysis, result }: { analysis: AnalysisInfo; result: SurveyResult }) {
  const units = useUnits();
  const { unit: areaUnit, bighaM2 } = useLocalAreaUnit();
  const camera = result.camera;
  // The limit the run checked (older results only have it in the parameters).
  const heightLimit = result.max_altitude_agl_m ?? paramNumber(analysis.params, "max_altitude_agl_m", null) ?? DEFAULTS.maxAltitude;
  const aboveLimit = result.altitude_agl_m > heightLimit + 1e-6;
  const footprintAcross = lengthParts(result.footprint_across_m, units);
  const footprintAlong = lengthParts(result.footprint_along_m, units);
  // Warnings come first, where they are seen; information notes stay at the end.
  const warnings = result.issues.filter((i) => i.severity !== "info");
  const notes = result.issues.filter((i) => i.severity === "info");
  const extraBatteries = result.batteries > result.blocks.length;
  const reference = result.height_reference ?? "agl";
  const terrainTrue = reference !== "agl";
  const range = hasGsdRange(result);
  const headline = gsdRangeSentence(result, units);
  const worst = result.worst_side_overlap_pct ?? null;
  const reserve = result.battery_reserve_pct ?? 0;
  const transit = result.transit_km != null;
  const corridor = result.coverage === "corridor";

  return (
    <div className="space-y-4">
      <p className="text-sm text-fg-muted">
        {camera ? `${camera.name}${camera.kind && camera.kind !== "rgb" ? ` (${CAMERA_KIND_LABEL[camera.kind].toLowerCase()})` : ""}. ` : ""}
        {ESTIMATE_NOTE}
      </p>
      <HeightsNotAssessedBanner assessed={result.heights_assessed} message={result.issues.find((i) => i.code === "heights_not_assessed")?.message} />
      <Warnings issues={warnings} />
      {headline ? <p className="text-sm font-medium text-fg">{localizeUnits(headline, units)}.</p> : null}
      <KpiGrid label="Survey estimate">
        <KpiTile
          label="Ground sample distance"
          value={range ? `${fmtGsd(result.gsd_min_cm)}–${fmtGsd(result.gsd_max_cm)}` : fmtGsd(result.gsd_cm)}
          unit="cm/px"
          explanation={
            range
              ? `Planned ${fmtGsd(result.gsd_cm)} cm/px at ${formatHeight(result.altitude_agl_m, units)}; finer over higher ground`
              : result.gsd_along_cm !== result.gsd_cm
                ? `Along track ${fmtGsd(result.gsd_along_cm)} cm/px`
                : "Square pixels"
          }
        />
        <KpiTile
          label="Height"
          value={heightParts(result.altitude_agl_m, units)}
          tone={aboveLimit ? "warn" : "neutral"}
          toneLabel={aboveLimit ? `Above the ${formatHeight(heightLimit, units)} limit` : undefined}
          explanation={
            terrainTrue
              ? `Above the take-off ground (planned ${HEIGHT_REFERENCE_SUFFIX[reference]})${result.flight_level_amsl_m != null ? `; flight level ${formatHeight(result.flight_level_amsl_m, units)} AMSL` : ""}`
              : "Above ground"
          }
        />
        {terrainTrue && result.heights_assessed !== false && result.agl_min_m != null ? (
          <KpiTile
            label="Height above ground"
            value={heightRangeParts(result.agl_min_m, result.agl_max_m, units)}
            tone={result.agl_min_m <= 0 ? "danger" : "neutral"}
            toneLabel={result.agl_min_m <= 0 ? "Ground above the flight level" : undefined}
            explanation={result.relief_m != null ? `Over ${formatHeight(result.relief_m, units)} of relief` : "Lowest to highest"}
          />
        ) : null}
        {terrainTrue && range ? (
          <KpiTile
            label="Worst overlap"
            value={worst === null ? "None" : formatNumber(worst, { maximumFractionDigits: 0 })}
            unit={worst === null ? undefined : "% side"}
            tone={worst === null || worst <= 0 ? "danger" : "neutral"}
            toneLabel={worst === null ? "The ground reaches the flight level" : worst <= 0 ? "Gaps between lines" : undefined}
            explanation={`Front ${formatNumber(result.worst_front_overlap_pct, { maximumFractionDigits: 0 })} %, over the highest ground (planned ${formatNumber(analysisOverlap(analysis, "side_overlap_pct", 70))}/${formatNumber(analysisOverlap(analysis, "front_overlap_pct", 80))} %)`}
          />
        ) : null}
        <KpiTile
          label="Image footprint"
          value={
            <>
              {footprintAcross.value}
              <span className="text-sm font-normal text-fg-muted"> {footprintAcross.unit}</span> × {footprintAlong.value}
              <span className="text-sm font-normal text-fg-muted"> {footprintAlong.unit}</span>
            </>
          }
          explanation="Across × along track"
        />
        <KpiTile
          label="Line spacing"
          value={lengthParts(result.line_spacing_m, units)}
          explanation={corridor ? `${plural(result.line_count, "line")} along the centreline` : `${plural(result.line_count, "line")} at ${formatNumber(result.direction_deg, { maximumFractionDigits: 0 })}°`}
        />
        <KpiTile label="Trigger distance" value={lengthParts(result.trigger_distance_m, units)} explanation={`Every ${formatNumber(result.trigger_interval_s, { maximumFractionDigits: 1 })} s`} />
        <KpiTile label="Photos" value={formatNumber(result.photos)} explanation={result.storage && result.storage.images_per_capture > 1 ? `${formatNumber(result.storage.images)} images (${result.storage.images_per_capture} per capture)` : undefined} />
        <KpiTile label="Line length" value={lengthParts(result.line_km * 1000, units)} explanation={plural(result.segment_count, "segment")} />
        <KpiTile
          label="Flight time"
          value={formatDuration(result.flight_minutes * 60)}
          explanation={
            result.batteries > 1
              ? `${formatDuration(result.total_minutes * 60)} including ${plural(result.batteries - 1, "battery swap")}`
              : transit
                ? "One battery, transit included"
                : "One battery, no swap"
          }
        />
        <KpiTile
          label="Batteries"
          value={formatNumber(result.batteries)}
          explanation={
            reserve > 0 && result.usable_battery_minutes != null
              ? `${formatNumber(reserve)} % reserve: ${formatDuration(result.usable_battery_minutes * 60)} used per battery`
              : extraBatteries
                ? `${formatNumber(result.blocks.length)} blocks: a line longer than one battery needs more`
                : result.batteries === result.blocks.length
                  ? "One per battery block"
                  : undefined
          }
        />
        {transit ? (
          <KpiTile
            label="Transit"
            value={lengthParts((result.transit_km ?? 0) * 1000, units)}
            explanation={`${formatDuration((result.transit_minutes ?? 0) * 60)} to and from the take-off point, in the flight time`}
          />
        ) : null}
        <KpiTile
          label="Area"
          value={areaParts(result.area_ha * 10_000, units)}
          explanation={`${areaParts(result.ha_per_hour * 10_000, units).text} per hour${
            // The land-area unit chosen in Settings, when it is not already the unit shown.
            areaUnit === "ha" || (areaUnit === "acre" && units === "imperial") ? "" : ` · ${formatLocalArea(result.area_ha * 10_000, areaUnit, bighaM2)}`
          }`}
        />
        {corridor && result.centreline_km != null ? (
          <KpiTile label="Corridor" value={lengthParts(result.centreline_km * 1000, units)} explanation={`Centreline, ${formatLength(result.corridor_width_m ?? null, units)} wide`} />
        ) : null}
        {result.gcp ? <KpiTile label="Ground control points" value={formatNumber(result.gcp.count)} explanation={`Rule of thumb: ${result.gcp.rule}`} /> : null}
        {result.storage ? (
          <KpiTile
            label="Storage"
            value={formatNumber(result.storage.total_gb, { maximumFractionDigits: 1 })}
            unit="GB"
            explanation={`${formatNumber(result.storage.images)} images × ${formatNumber(result.storage.mb_per_image)} MB (${result.storage.source === "params" ? "your size" : "typical for this camera"})`}
          />
        ) : null}
      </KpiGrid>

      <BlocksTable result={result} />
      <IssueList issues={notes} />
      <Footnotes result={result} />
    </div>
  );
}

function analysisOverlap(analysis: AnalysisInfo, key: string, fallback: number): number {
  return paramNumber(analysis.params, key, fallback) ?? fallback;
}

function AreaUnitPicker({ unit, onChange }: { unit: LocalAreaUnit; onChange: (unit: LocalAreaUnit) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-fg-muted" id="survey-area-unit">
        Area unit
      </span>
      <ToggleGroup type="single" size="sm" value={unit} onValueChange={(v) => v && onChange(v as LocalAreaUnit)} aria-labelledby="survey-area-unit">
        {LOCAL_AREA_UNITS.map((u) => (
          <ToggleGroupItem key={u} value={u}>
            {LOCAL_AREA_UNIT_LABEL[u]}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  );
}

function SprayResults({ result }: { result: SurveyResult }) {
  const units = useUnits();
  const { unit, bighaM2, setUnit } = useLocalAreaUnit();
  const spray = result.spray;
  if (!spray) return null;
  const warnings = result.issues.filter((i) => i.severity !== "info");
  const notes = result.issues.filter((i) => i.severity === "info");
  const areaM2 = result.area_ha * 10_000;
  const unitM2 = localAreaSize(unit, bighaM2);
  const unitArea = localAreaParts(areaM2, unit, bighaM2);
  // A rate per hectare (L/ha) per local unit: L/ha × (m² per unit) / 10 000 m².
  const perUnit = (perHa: number) => (perHa * unitM2) / 10_000;
  // A work rate in hectares per hour as local units per hour: ha/h × 10 000 m² / (m² per unit).
  const unitsPerHour = (haPerHour: number) => (haPerHour * 10_000) / unitM2;
  const unitSymbol = unitArea.unit || LOCAL_AREA_UNIT_LABEL[unit].toLowerCase();
  const stops = spray.refills;
  const transit = result.transit_km != null;
  return (
    <div className="space-y-4">
      <p className="text-sm text-fg-muted">{SPRAY_NOTE}</p>
      <Warnings issues={warnings} />
      <AreaUnitPicker unit={unit} onChange={setUnit} />
      <KpiGrid label="Spraying estimate">
        <KpiTile
          label="Area"
          value={unitArea}
          explanation={unit === "ha" ? formatArea(areaM2, units) : `${formatNumber(result.area_ha, { maximumFractionDigits: 2 })} ha${unit === "bigha" ? ` (1 bigha = ${formatNumber(bighaM2, { maximumFractionDigits: 0 })} m²)` : ""}`}
        />
        <KpiTile
          label="Spray mix"
          value={formatNumber(spray.litres, { maximumFractionDigits: 1 })}
          unit="L"
          explanation={`${formatNumber(spray.rate_l_per_ha)} L/ha${unit === "ha" ? "" : ` (${formatNumber(perUnit(spray.rate_l_per_ha), { maximumFractionDigits: 2 })} L/${unitSymbol})`} over a ${formatLength(spray.swath_m, units)} swath`}
        />
        <KpiTile label="Tank loads" value={formatNumber(spray.tank_loads)} explanation={`${formatNumber(spray.tank_l)} L tank`} />
        <KpiTile
          label="Sorties"
          value={formatNumber(spray.sorties)}
          explanation={stops > 0 ? `${plural(stops, "stop")} to refill and swap the battery` : "One sortie, no stop"}
        />
        <KpiTile
          label="Spraying rate"
          value={formatNumber(unitsPerHour(spray.effective_ha_per_hour), { maximumFractionDigits: 2 })}
          unit={`${unitSymbol}/h`}
          explanation={`Field efficiency ${formatNumber(spray.field_efficiency_pct, { maximumFractionDigits: 0 })} % of ${formatNumber(unitsPerHour(spray.theoretical_ha_per_hour), { maximumFractionDigits: 2 })} ${unitSymbol}/h (swath × speed)`}
        />
        <KpiTile
          label="Pump output"
          value={formatNumber(spray.flow_l_per_min, { maximumFractionDigits: 2 })}
          unit="L/min"
          tone={spray.flow_limited ? "warn" : "neutral"}
          toneLabel={spray.flow_limited ? `Pump-limited: ${formatSpeed(spray.speed_ms, units, { maximumFractionDigits: 2 })}` : undefined}
          explanation={`Of ${formatNumber(spray.max_flow_l_per_min)} L/min at ${formatSpeed(spray.speed_ms, units, { maximumFractionDigits: 2 })}`}
        />
        <KpiTile label="Spraying time" value={formatDuration(spray.spray_minutes * 60)} explanation="On the spray lines only" />
        <KpiTile
          label="Total time"
          value={formatDuration(result.total_minutes * 60)}
          explanation={`${formatDuration(result.flight_minutes * 60)} flying${transit ? " (transit included)" : ""} + stops`}
        />
        <KpiTile label="Spray height" value={heightParts(spray.height_m, units, { maximumFractionDigits: 1 })} explanation="Above the crop (terrain following)" />
        {transit ? (
          <KpiTile
            label="Transit"
            value={lengthParts((result.transit_km ?? 0) * 1000, units)}
            explanation={`${formatDuration((result.transit_minutes ?? 0) * 60)} to and from the take-off point`}
          />
        ) : null}
      </KpiGrid>
      <p className="text-xs text-fg-muted">
        {formatLocalArea(areaM2, "acre", bighaM2)} · {formatLocalArea(areaM2, "guntha", bighaM2)} · {formatLocalArea(areaM2, "bigha", bighaM2)} (bigha of{" "}
        {formatNumber(bighaM2, { maximumFractionDigits: 0 })} m², set in Settings).
      </p>
      <BlocksTable result={result} />
      <IssueList issues={notes} />
      <Footnotes result={result} />
    </div>
  );
}

export function SurveyResults({ analysis }: ModuleResultsProps) {
  const setHighlight = useUi((s) => s.setResultHighlight);
  const analysisId = analysis?.id ?? null;
  React.useEffect(() => {
    // A different survey: forget the block picked in the previous one.
    setHighlight(null);
  }, [analysisId, setHighlight]);
  const result = analysisResult(analysis, "survey");
  if (!analysis) return null;
  if (!result) {
    return analysis.status === "COMPLETED" ? (
      <EmptyState compact icon={<Grid3x3 />} title="No result" description="The analysis finished without a result." />
    ) : null;
  }
  // Keyed by analysis: a different survey starts with no block selected.
  return result.mode === "spraying" ? (
    <SprayResults key={analysis.id} result={result} />
  ) : (
    <MappingResults key={analysis.id} analysis={analysis} result={result} />
  );
}
