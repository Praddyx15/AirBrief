"use client";

/**
 * < 768 px: read-only viewer. The map fills the screen and this bottom sheet holds the
 * feature list (cards instead of tables), the details and the results.
 */

import * as React from "react";
import { ChevronDown, ChevronUp, Search } from "lucide-react";
import { formatArea, formatLength, formatNumber } from "@/lib/format";
import { useUnits } from "@/lib/store";
import type { DatasetFeatureCollection } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { featureRows, filterFeatureRows } from "./layers-panel";
import { MeasurementStatusBadge } from "./status-badges";

const CARD_LIMIT = 100;

export type MobileTab = "features" | "details" | "results";

export interface MobileViewerProps {
  features: DatasetFeatureCollection | null;
  selected: readonly number[];
  onSelect: (index: number) => void;
  details: React.ReactNode;
  results: React.ReactNode;
  tab: MobileTab;
  onTabChange: (tab: MobileTab) => void;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
}

export function MobileViewer({
  features,
  selected,
  onSelect,
  details,
  results,
  tab,
  onTabChange,
  expanded,
  onExpandedChange,
}: MobileViewerProps) {
  const units = useUnits();
  const [query, setQuery] = React.useState("");
  const rows = React.useMemo(() => featureRows(features), [features]);
  const filtered = React.useMemo(() => filterFeatureRows(rows, query), [rows, query]);
  const shown = filtered.slice(0, CARD_LIMIT);
  const selectedSet = React.useMemo(() => new Set(selected), [selected]);
  const panelId = React.useId();

  return (
    <section
      id="results"
      tabIndex={-1}
      aria-label="Viewer details"
      className={cn(
        "z-drawer flex shrink-0 flex-col rounded-t-lg border-t border-border bg-surface pb-[env(safe-area-inset-bottom)] outline-none",
        expanded ? "h-[55dvh]" : "h-auto",
      )}
    >
      <Tabs
        value={tab}
        onValueChange={(v) => {
          if (v === "features" || v === "details" || v === "results") {
            onTabChange(v);
            onExpandedChange(true);
          }
        }}
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className="flex items-center gap-2 px-2 py-1.5">
          <TabsList aria-label="Viewer sections" className="h-auto flex-1">
            {/* Radix reports no change for the selected tab, so a collapsed sheet expands on click
                (Enter and Space click too), the same as the results drawer on larger screens. */}
            {(["features", "details", "results"] as const).map((value) => (
              <TabsTrigger
                key={value}
                value={value}
                onClick={() => {
                  if (!expanded) onExpandedChange(true);
                }}
                className="flex-1 text-xs"
              >
                {value === "features" ? "Features" : value === "details" ? "Details" : "Results"}
              </TabsTrigger>
            ))}
          </TabsList>
          <Button
            size="icon"
            variant="ghost"
            aria-expanded={expanded}
            aria-controls={panelId}
            aria-label={expanded ? "Collapse the details sheet" : "Expand the details sheet"}
            onClick={() => onExpandedChange(!expanded)}
          >
            {expanded ? <ChevronDown aria-hidden /> : <ChevronUp aria-hidden />}
          </Button>
        </div>
        <div id={panelId} hidden={!expanded} className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
          <TabsContent value="features" className="mt-0 space-y-2">
            {rows.length === 0 ? (
              <EmptyState compact title="No features" description="Open a dataset to list its features." />
            ) : (
              <>
                <div className="relative">
                  <Label htmlFor={`${panelId}-search`} className="sr-only">
                    Search features
                  </Label>
                  <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-fg-muted" />
                  <Input
                    id={`${panelId}-search`}
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search features"
                    className="pl-8"
                    autoComplete="off"
                  />
                </div>
                <ul className="space-y-1.5" aria-label="Features">
                  {shown.map((r) => {
                    const isSelected = selectedSet.has(r.index);
                    return (
                      <li key={r.index}>
                        <button
                          type="button"
                          aria-pressed={isSelected}
                          onClick={() => {
                            onSelect(r.index);
                            onTabChange("details");
                          }}
                          className={cn(
                            "flex min-h-11 w-full cursor-pointer flex-col gap-1 rounded border p-2.5 text-left text-sm transition-colors duration-200",
                            isSelected ? "border-accent bg-accent/10" : "border-border bg-bg hover:bg-elevated",
                          )}
                        >
                          <span className="flex items-center justify-between gap-2">
                            <span className="truncate font-medium">{r.name}</span>
                            <MeasurementStatusBadge status={r.status} />
                          </span>
                          <span className="flex justify-between gap-2 text-xs text-fg-muted">
                            <span className="truncate">
                              {r.type} · {r.layer}
                            </span>
                            <span className="num shrink-0 text-fg">
                              {r.area !== null ? formatArea(r.area, units) : r.length !== null ? formatLength(r.length, units) : "—"}
                            </span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
                {filtered.length > CARD_LIMIT ? (
                  <p className="text-xs text-fg-muted">
                    Showing {CARD_LIMIT} of {formatNumber(filtered.length)}. Search to narrow the list.
                  </p>
                ) : null}
              </>
            )}
          </TabsContent>
          <TabsContent value="details" className="mt-0">
            {details}
          </TabsContent>
          <TabsContent value="results" className="mt-0">
            {results}
          </TabsContent>
        </div>
      </Tabs>
    </section>
  );
}
