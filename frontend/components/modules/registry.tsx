/**
 * Module registry: every workspace module's Panel / Results / layers / buildLayers, keyed by
 * `ModuleId`. Each module lives in `components/modules/<id>/index.tsx` and exports
 * `definition: ModuleDefinition` (see frontend/README.md "Module contract").
 */

import { Building2, Grid3x3, Plane, RadioTower, Route, Ruler, Sparkles, Sun, Users, type LucideIcon } from "lucide-react";
import {
  MODULE_IDS,
  moduleForKind,
  type ModuleDefinition,
  type ModuleIconName,
  type ModuleId,
  type ModuleLayerInfo,
} from "@/lib/modules";
import type { AnalysisInfo, AnalysisKind } from "@/lib/types";
import { definition as airspace } from "./airspace";
import { definition as environment } from "./environment";
import { definition as evtol } from "./evtol";
import { definition as flightAudit } from "./flight-audit";
import { definition as groundRisk } from "./ground-risk";
import { definition as lineOfSight } from "./line-of-sight";
import { definition as measure } from "./measure";
import { definition as shows } from "./shows";
import { definition as survey } from "./survey";

/** Lucide components for `ModuleMetaEntry.icon`. */
export const MODULE_ICONS: Readonly<Record<ModuleIconName, LucideIcon>> = {
  Ruler,
  Plane,
  Users,
  Building2,
  Sparkles,
  Grid3x3,
  Route,
  Sun,
  RadioTower,
};

export const MODULES: Readonly<Record<ModuleId, ModuleDefinition>> = {
  measure,
  airspace,
  "ground-risk": groundRisk,
  evtol,
  shows,
  survey,
  "flight-audit": flightAudit,
  environment,
  "line-of-sight": lineOfSight,
};

/** Modules in rail order. */
export const MODULE_DEFINITIONS: readonly ModuleDefinition[] = MODULE_IDS.map((id) => MODULES[id]);

export function moduleDefinitionForKind(kind: AnalysisKind): ModuleDefinition {
  return MODULES[moduleForKind(kind)];
}

export function analysisBelongsTo(module: ModuleDefinition, analysis: AnalysisInfo | null | undefined): analysis is AnalysisInfo {
  return analysis != null && module.kinds.includes(analysis.kind);
}

/** The toggleable layers of a module that exist for an analysis kind (`batch`: of a per-feature run). */
export function moduleLayersForKind(module: ModuleDefinition, kind: AnalysisKind, batch = false): ModuleLayerInfo[] {
  return (module.layers ?? []).filter((l) => (!l.kinds || l.kinds.includes(kind)) && Boolean(l.batch) === batch);
}
