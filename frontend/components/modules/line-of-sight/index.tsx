"use client";

/**
 * Line-of-sight module: terrain viewshed of the operation area from the pilot, visual observers
 * and ground station at the planned height (Earth curvature and refraction included), and C2
 * radio path profiles with first Fresnel zone clearance and free-space loss. Geometric evidence
 * only: never a VLOS approval, a link budget or a coverage prediction.
 */

import { MODULE_META, type ModuleDefinition } from "@/lib/modules";
import { withBatch } from "../batch";
import { LOS_LAYERS, buildLineOfSightLayers, describeLineOfSightPick, onLineOfSightPick } from "./layers";
import { LineOfSightPanel } from "./panel";
import { LineOfSightResults } from "./results";

export const definition: ModuleDefinition = withBatch({
  ...MODULE_META["line-of-sight"],
  Panel: LineOfSightPanel,
  Results: LineOfSightResults,
  layers: LOS_LAYERS,
  buildLayers: buildLineOfSightLayers,
  describePick: describeLineOfSightPick,
  onPick: onLineOfSightPick,
});

export default definition;
