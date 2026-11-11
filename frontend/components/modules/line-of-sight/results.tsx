"use client";

/**
 * Line-of-sight results: coverage KPIs, the observers table, radio paths with the selected path's
 * profile chart, hidden stretches of a route, the method and the notes. Evidence only.
 */

import * as React from "react";
import { RadioTower } from "lucide-react";
import { areaParts, formatCoord, formatHeight, formatLength, formatNumber, formatPercent, lengthParts, localizeUnits, plural } from "@/lib/format";
import type { ModuleResultsProps } from "@/lib/modules";
import { useUi, useUnits } from "@/lib/store";
import { analysisResult, type AnalysisInfo, type LineOfSightResult, type LosObserver, type LosRadioPath, type LosRouteSegment } from "@/lib/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { KpiGrid, KpiTile } from "@/components/ui/kpi-tile";
import { DemCaveat, Disclaimer, IssueList, ModuleSection, SyntheticNote, ToneBadge, pointBounds, useFocusMap } from "../shared";
import { RadioProfileChart } from "./profile-chart";
import { EVIDENCE_NOTE, RADIO_BAND_TONE, RADIO_NOTE, ROLE_LABEL, formatFrequency, pathKey, radioBand, radioBandLabel } from "./model";

type PathRow = LosRadioPath & { index: number };

function ObserversTable({ result }: { result: LineOfSightResult }) {
  const units = useUnits();
  const focus = useFocusMap();
  const columns = React.useMemo<DataTableColumn<LosObserver>[]>(
    () => [
      {
        id: "name",
        header: "Name",
        rowHeader: true,
        cell: (o) => (
          <>
            {o.name}
            {o.assumed ? <span className="block text-fg-muted">assumed position</span> : null}
          </>
        ),
        sortValue: (o) => o.name,
      },
      { id: "role", header: "Role", cell: (o) => ROLE_LABEL[o.role], sortValue: (o) => o.role },
      {
        id: "visible",
        header: "Sees of the area",
        numeric: true,
        cell: (o) => formatPercent(o.visible_pct, { maximumFractionDigits: 1 }),
        sortValue: (o) => o.visible_pct,
      },
      { id: "far", header: "Farthest seen", numeric: true, cell: (o) => formatLength(o.max_visible_m, units), sortValue: (o) => o.max_visible_m },
      { id: "height", header: "Height above ground", numeric: true, cell: (o) => formatHeight(o.height_agl_m, units), sortValue: (o) => o.height_agl_m },
      { id: "eye", header: "Eye / antenna (AMSL)", numeric: true, cell: (o) => formatHeight(o.eye_amsl_m, units), sortValue: (o) => o.eye_amsl_m },
      { id: "kind", header: "Line of sight", cell: (o) => (o.los_kind === "visual" ? "Visual" : "Radio"), sortValue: (o) => o.los_kind },
    ],
    [units],
  );
  return (
    <DataTable
      data={result.observers}
      columns={columns}
      getRowId={(o, i) => `${o.name}-${i}`}
      caption="Share of the operation area each pilot, observer or ground station can see at the target height"
      onRowClick={(o) => focus(pointBounds(o.lon, o.lat, 600))}
      getRowLabel={(o) => `Zoom to ${o.name}`}
      dense
    />
  );
}

function RadioSection({ analysis, result }: { analysis: AnalysisInfo; result: LineOfSightResult }) {
  const units = useUnits();
  const focus = useFocusMap();
  const highlight = useUi((s) => s.resultHighlight);
  const setHighlight = useUi((s) => s.setResultHighlight);
  const radio = result.radio;
  const rows = React.useMemo<PathRow[]>(() => (radio ? radio.paths.map((p, index) => ({ ...p, index })) : []), [radio]);
  const keyOf = React.useCallback((p: PathRow) => pathKey(analysis.id, p.index), [analysis.id]);
  const isPath = React.useCallback((key: string | null) => key !== null && rows.some((p) => keyOf(p) === key), [rows, keyOf]);

  // The clicked path (table row or map line) drives the chart; hovering a row only previews it on the map.
  const hovering = React.useRef(false);
  const worstFirst = React.useMemo(() => {
    const finite = rows.filter((p) => p.worst_clearance_pct !== null);
    return finite.length > 0 ? finite.reduce((a, b) => ((b.worst_clearance_pct as number) < (a.worst_clearance_pct as number) ? b : a)) : (rows[0] ?? null);
  }, [rows]);
  const [selectedKey, setSelectedKey] = React.useState<string | null>(() => (isPath(highlight) ? highlight : null));
  React.useEffect(() => {
    if (!hovering.current && isPath(highlight)) setSelectedKey(highlight);
  }, [highlight, isPath]);
  const selected = rows.find((p) => keyOf(p) === selectedKey) ?? worstFirst;
  const selectedId = selected ? keyOf(selected) : null;

  const threshold = radio?.clearance_threshold_pct ?? 60;
  const columns = React.useMemo<DataTableColumn<PathRow>[]>(
    () => [
      { id: "to", header: "To", rowHeader: true, cell: (p) => p.to_name, sortValue: (p) => p.index },
      {
        id: "status",
        header: "Status",
        cell: (p) => {
          const band = radioBand(p);
          return <ToneBadge tone={RADIO_BAND_TONE[band]} label={radioBandLabel(band, threshold)} />;
        },
        sortValue: (p) => p.worst_clearance_pct,
      },
      {
        id: "clearance",
        header: "Worst clearance",
        numeric: true,
        cell: (p) => formatPercent(p.worst_clearance_pct, { maximumFractionDigits: 1 }),
        sortValue: (p) => p.worst_clearance_pct,
      },
      { id: "at", header: "At", numeric: true, cell: (p) => formatLength(p.worst_at_m, units), sortValue: (p) => p.worst_at_m },
      { id: "distance", header: "Distance", numeric: true, cell: (p) => formatLength(p.distance_m, units), sortValue: (p) => p.distance_m },
      { id: "fspl", header: "Free-space loss", numeric: true, cell: (p) => `${formatNumber(p.fspl_db, { decimals: 1 })} dB`, sortValue: (p) => p.fspl_db },
      { id: "fresnel", header: "Max Fresnel radius", numeric: true, cell: (p) => formatLength(p.max_fresnel_radius_m, units), sortValue: (p) => p.max_fresnel_radius_m },
    ],
    [units, threshold],
  );

  if (!radio) return null;
  return (
    <ModuleSection title={`Radio paths from ${radio.station_name} (${rows.length})`}>
      <p className="text-xs text-fg-muted">
        {formatFrequency(radio.frequency_mhz)}, k = {formatNumber(radio.k_factor, { maximumFractionDigits: 4 })}, required clearance{" "}
        {formatNumber(threshold)}% of the first Fresnel zone. {RADIO_NOTE}
      </p>
      <DataTable
        data={rows}
        columns={columns}
        getRowId={keyOf}
        caption="Radio paths with first Fresnel zone clearance and free-space path loss"
        onRowClick={(p) => {
          setSelectedKey(keyOf(p));
          setHighlight(keyOf(p));
          focus([Math.min(p.from_lon, p.to_lon), Math.min(p.from_lat, p.to_lat), Math.max(p.from_lon, p.to_lon), Math.max(p.from_lat, p.to_lat)]);
        }}
        onRowHover={(p) => {
          hovering.current = p !== null;
          setHighlight(p ? keyOf(p) : selectedKey);
        }}
        selectedRowIds={selectedId ? [selectedId] : []}
        getRowLabel={(p) => `Show the profile to ${p.to_name}`}
        dense
        maxHeight={260}
        emptyState={<EmptyState compact title="No radio paths" description="Every path end was outside the terrain data or at the station." />}
      />
      {selected ? (
        <>
          <RadioProfileChart path={selected} thresholdPct={threshold} units={units} />
          {selected.segments_below.length > 0 ? (
            <ul className="space-y-0.5 text-xs" aria-label={`Stretches below ${formatNumber(threshold)}% clearance`}>
              {selected.segments_below.map((s, i) => (
                <li key={i}>
                  {formatLength(s.start_m, units)} to {formatLength(s.end_m, units)}: lowest clearance{" "}
                  {formatPercent(s.min_clearance_pct, { maximumFractionDigits: 1 })}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="text-xs text-fg-muted">
            {formatHeight(selected.from_height_agl_m, units)} antenna at {selected.from_name} ({formatHeight(selected.from_amsl_m, units)} AMSL) to{" "}
            {formatHeight(selected.to_height_agl_m, units)} at {selected.to_name} ({formatHeight(selected.to_amsl_m, units)} AMSL,{" "}
            {formatCoord(selected.to_lon, selected.to_lat)}); slant distance {formatLength(selected.slant_m, units)}
            {selected.obstacles_on_path > 0 ? `; ${plural(selected.obstacles_on_path, "obstacle")} on the path` : ""}
            {selected.nodata_samples > 0 ? `; ${plural(selected.nodata_samples, "sample")} without terrain data` : ""}.
          </p>
        </>
      ) : null}
    </ModuleSection>
  );
}

function RouteSection({ result }: { result: LineOfSightResult }) {
  const units = useUnits();
  const route = result.route;
  const columns = React.useMemo<DataTableColumn<LosRouteSegment>[]>(
    () => [
      { id: "from", header: "From", numeric: true, rowHeader: true, cell: (s) => formatLength(s.start_m, units), sortValue: (s) => s.start_m },
      { id: "to", header: "To", numeric: true, cell: (s) => formatLength(s.end_m, units), sortValue: (s) => s.end_m },
      { id: "length", header: "Length", numeric: true, cell: (s) => formatLength(s.length_m, units), sortValue: (s) => s.length_m },
    ],
    [units],
  );
  if (!route) return null;
  return (
    <ModuleSection title={`Hidden stretches of the route (${route.hidden_segments.length})`}>
      <DataTable
        data={route.hidden_segments}
        columns={columns}
        getRowId={(s) => `${s.start_m}-${s.end_m}`}
        caption="Stretches of the route where no pilot or observer sees the aircraft, by distance along the route"
        dense
        maxHeight={220}
        emptyState={<EmptyState compact title="No hidden stretches" description="The aircraft is seen along the whole route." />}
      />
      {route.hidden_segments_truncated ? <p className="text-xs text-warn">Only the first 200 hidden stretches are listed.</p> : null}
    </ModuleSection>
  );
}

export function LineOfSightResults({ analysis }: ModuleResultsProps) {
  const units = useUnits();
  const result = analysisResult(analysis, "line_of_sight");
  if (!analysis) return null;
  if (!result) {
    return analysis.status === "COMPLETED" ? (
      <EmptyState compact icon={<RadioTower />} title="No result" description="The analysis finished without a result." />
    ) : null;
  }

  const warnings = result.issues.filter((i) => i.severity !== "info");
  const notes = result.issues.filter((i) => i.severity === "info");
  const radio = result.radio;
  const visual = result.observers.filter((o) => o.los_kind === "visual");
  const g = result.vlos_guidance;
  const clearanceTone = !radio || radio.worst_clearance_pct === null ? "neutral" : radio.paths_blocked > 0 ? "danger" : radio.paths_below_threshold > 0 ? "warn" : "ok";

  return (
    <div className="space-y-4">
      <p className="text-sm text-fg-muted">
        Aircraft {formatHeight(result.target_height_agl_m, units)} above the ground, {plural(visual.length, "pilot or observer", "pilots or observers")}. {EVIDENCE_NOTE}
      </p>
      {warnings.length > 0 ? (
        <Alert variant={warnings.some((i) => i.severity === "critical") ? "danger" : "warn"}>
          <AlertTitle>{warnings.length === 1 ? "Warning" : `${formatNumber(warnings.length)} warnings`}</AlertTitle>
          <AlertDescription>
            <ul className="list-disc space-y-1 pl-4 text-fg">
              {warnings.map((issue, i) => (
                <li key={`${issue.code}-${i}`}>{localizeUnits(issue.message, units)}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
      <SyntheticNote synthetic={analysis.datapack_synthetic} />

      <KpiGrid label="Line-of-sight summary">
        <KpiTile
          label="Area in visual line of sight"
          value={formatNumber(result.combined_visible_pct, { maximumFractionDigits: 1 })}
          unit="%"
          explanation={`Seen by at least one pilot or observer (${areaParts(result.visible_area_m2, units).text})`}
        />
        {visual.length > 1
          ? visual.map((o) => (
              <KpiTile
                key={`${o.name}-${o.lon}-${o.lat}`}
                label={`Seen by ${o.name}`}
                value={formatNumber(o.visible_pct, { maximumFractionDigits: 1 })}
                unit="%"
                explanation={o.max_visible_m === null ? undefined : `Farthest seen ${formatLength(o.max_visible_m, units)}`}
              />
            ))
          : null}
        {result.radio_los_pct !== null ? (
          <KpiTile
            label="Area in radio line of sight"
            value={formatNumber(result.radio_los_pct, { maximumFractionDigits: 1 })}
            unit="%"
            explanation={`From ${radio?.station_name ?? "the station"}, k = ${formatNumber(radio?.k_factor ?? 4 / 3, { maximumFractionDigits: 4 })}`}
          />
        ) : null}
        {radio ? (
          <KpiTile
            label="Worst Fresnel clearance"
            value={formatNumber(radio.worst_clearance_pct, { maximumFractionDigits: 1 })}
            unit="%"
            tone={clearanceTone}
            toneLabel={
              clearanceTone === "danger"
                ? `Cut on ${plural(radio.paths_blocked, "path")}`
                : clearanceTone === "warn"
                  ? `Below ${formatNumber(radio.clearance_threshold_pct)}% on ${plural(radio.paths_below_threshold, "path")}`
                  : clearanceTone === "ok"
                    ? `At least ${formatNumber(radio.clearance_threshold_pct)}% everywhere`
                    : undefined
            }
            explanation={`First Fresnel zone at ${formatFrequency(radio.frequency_mhz)}`}
          />
        ) : null}
        {radio && radio.max_fspl_db !== null ? (
          <KpiTile label="Free-space path loss" value={formatNumber(radio.max_fspl_db, { decimals: 1 })} unit="dB" explanation={`Longest path. ${RADIO_NOTE}`} />
        ) : null}
        <KpiTile
          label="Largest hidden patch"
          value={result.largest_hidden_patch ? areaParts(result.largest_hidden_patch.area_m2, units) : "None"}
          explanation={result.largest_hidden_patch ? `Hidden from every pilot and observer, around ${formatCoord(result.largest_hidden_patch.lon, result.largest_hidden_patch.lat)}` : "Every area cell is seen"}
        />
        {result.route ? (
          <KpiTile
            label="Longest seen stretch"
            value={lengthParts(result.route.max_continuous_visible_m, units)}
            explanation={`${formatPercent(result.route.visible_pct, { maximumFractionDigits: 1 })} of the ${formatLength(result.route.length_m, units)} route seen`}
          />
        ) : null}
        <KpiTile
          label="VLOS guidance distance"
          value={lengthParts(g.guidance_m, units)}
          explanation={`${g.basis === "easa_alos_dlos" ? "EASA ALOS/DLOS estimate" : "Commonly cited UK CAA figure"}, non-regulatory; ${formatPercent(result.beyond_guidance_pct, { maximumFractionDigits: 1 })} of the area beyond it`}
        />
      </KpiGrid>
      <DemCaveat dem={result.dem} />

      <ModuleSection title={`Pilot, observers and ground station (${result.observers.length})`}>
        <ObserversTable result={result} />
      </ModuleSection>

      <RadioSection key={analysis.id} analysis={analysis} result={result} />
      <RouteSection result={result} />

      <ModuleSection title="Method">
        <p className="text-sm">{result.method}</p>
        <p className="text-xs text-fg-muted">
          Grid {formatNumber(result.grid.cols)} × {formatNumber(result.grid.rows)} cells of {formatLength(result.grid.grid_m, units)}
          {result.grid.coarsened ? ` (coarsened from ${formatLength(result.grid.requested_grid_m, units)})` : ""}, {plural(result.grid.area_cells, "area cell")}; terrain{" "}
          {result.grid.dem_source}
          {result.grid.dem_resolution_m ? ` at about ${formatLength(result.grid.dem_resolution_m, units)}` : ""}
          {result.grid.obstacles_used > 0 ? `; ${plural(result.grid.obstacles_used, "obstacle")} raised onto the grid` : ""}.
        </p>
        <p className="text-xs text-fg-muted">VLOS guidance: {g.reference}</p>
      </ModuleSection>

      <IssueList issues={notes} />
      <Disclaimer text={result.disclaimer} />
    </div>
  );
}
