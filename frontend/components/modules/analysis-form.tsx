"use client";

/**
 * Module forms that follow the analysis on screen. When a panel shows an analysis it did not just
 * start (restored from the URL on reload, opened from History or a shared link), its fields are
 * set from that analysis' parameters, so editing one value and running again changes only that
 * value. "Reset to defaults" puts the form back to the module's defaults.
 *
 * The analysis' kind needs no check: a panel only receives analyses of its own module.
 */

import * as React from "react";
import { RotateCcw } from "lucide-react";
import type { AnalysisInfo } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { announce } from "@/components/workspace/live-region";

type Params = Readonly<Record<string, unknown>>;

/** A finite number from stored analysis parameters, or `fallback`. */
export function paramNumber(params: Params, key: string, fallback: number | null): number | null {
  const value = params[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function paramBoolean(params: Params, key: string, fallback: boolean): boolean {
  const value = params[key];
  return typeof value === "boolean" ? value : fallback;
}

export function paramChoice<T extends string>(params: Params, key: string, choices: readonly T[], fallback: T): T {
  const value = params[key];
  return typeof value === "string" && (choices as readonly string[]).includes(value) ? (value as T) : fallback;
}

export interface AnalysisFormSync {
  /** Call with the id of every analysis the form creates: its values are already in the form. */
  markStarted: (id: string) => void;
  /** Sets every field from `params` (`{}` gives the defaults, as "Reset to defaults" does). */
  setFields: (params: Params) => void;
  /**
   * Changes each time the fields are set. Used as a `key` around the fields, it also replaces
   * text that was typed but is not a number (the stored value, and so the input, may not change).
   */
  fieldsKey: number;
}

/**
 * Sets the fields from `analysis.params` (through `apply`) whenever the panel receives a
 * different analysis, before the browser paints (no flash of default values). An analysis this
 * form started is skipped the first time it is shown, since the form already holds its values;
 * shown again later (after another one, e.g. from History), it sets the fields like any other.
 */
export function useFormFromAnalysis(analysis: AnalysisInfo | null, apply: (params: Params) => void): AnalysisFormSync {
  const applyRef = React.useRef(apply);
  applyRef.current = apply;
  const shown = React.useRef<string | null>(null);
  const started = React.useRef(new Set<string>());
  const [fieldsKey, setFieldsKey] = React.useState(0);

  const setFields = React.useCallback((params: Params) => {
    applyRef.current(params);
    setFieldsKey((k) => k + 1);
  }, []);

  React.useLayoutEffect(() => {
    if (!analysis || analysis.id === shown.current) return;
    shown.current = analysis.id;
    if (started.current.delete(analysis.id)) return;
    setFields(analysis.params);
  }, [analysis, setFields]);

  const markStarted = React.useCallback((id: string) => {
    // Already shown (the URL was quicker than the caller): there is nothing left to skip.
    if (shown.current !== id) started.current.add(id);
  }, []);

  return { markStarted, setFields, fieldsKey };
}

/** Secondary form action under the Run button: every field back to the module's default. */
export function ResetFormButton({ onReset }: { onReset: () => void }) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="w-full"
      onClick={() => {
        onReset();
        announce("Form reset to the default values.");
      }}
    >
      <RotateCcw aria-hidden />
      Reset to defaults
    </Button>
  );
}
