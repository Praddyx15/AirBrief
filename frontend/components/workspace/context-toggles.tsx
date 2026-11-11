"use client";

/**
 * "Context layers" in the Layers panel: switches for the data pack's reference layers
 * (aerodromes, zone rings, pack zones, obstacles, water, coverage), shown on the map before any
 * module runs (`GET /api/context/`, `components/map/layers/context-layer.ts`). All are off by
 * default; each says how many features the view has, when it was cut at the limit, when no pack
 * covers the view, and offers its table.
 */

import * as React from "react";
import { EyeOff, Table2 } from "lucide-react";
import { describeError } from "@/lib/api";
import { plural } from "@/lib/format";
import { usePlan } from "@/lib/plan-store";
import { CONTEXT_LAYER_KEYS, MAX_CONTEXT_SPAN_DEG, type ContextLayerKey } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { IconButton } from "@/components/ui/icon-button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { CONTEXT_LAYER_HINT, CONTEXT_LAYER_LABEL, contextLayerId } from "@/components/map/layers/context-layer";
import { useContextData } from "./plan-map";

export interface ContextLayerTogglesProps {
  /** open a context layer's table ("context:<key>") */
  onViewTable: (layerId: string) => void;
  /** start expanded (e.g. no dataset open) */
  defaultOpen?: boolean;
}

export function ContextLayerToggles({ onViewTable, defaultOpen = false }: ContextLayerTogglesProps) {
  const on = usePlan((s) => s.contextLayers);
  const toggle = usePlan((s) => s.toggleContextLayer);
  const { data, tooWide } = useContextData();
  const [open, setOpen] = React.useState(defaultOpen || on.length > 0);
  const byKey = React.useMemo(() => new Map(data.map((d) => [d.key, d])), [data]);
  const loaded = data.filter((d) => d.collection !== null);
  const noPack = loaded.length > 0 && loaded.every((d) => d.datapack === null);
  const synthetic = loaded.some((d) => d.synthetic);

  const status = (key: ContextLayerKey): string => {
    if (!on.includes(key)) return CONTEXT_LAYER_HINT[key];
    if (tooWide) return "Zoom in to load";
    const d = byKey.get(key);
    if (!d || (d.isLoading && !d.collection)) return "Loading…";
    if (d.error) return `Could not load: ${describeError(d.error).message}`;
    if (d.datapack === null) return "No data pack here";
    const shown = d.collection?.features.length ?? 0;
    return d.truncated ? `First ${shown.toLocaleString("en-US")} of ${d.count.toLocaleString("en-US")} in view` : plural(shown, "feature");
  };

  return (
    <section aria-label="Context layers" className="border-b border-border p-3">
      <Collapsible open={open} onOpenChange={setOpen}>
        <h2 className="text-xs font-medium uppercase tracking-wide text-fg-muted">
          <CollapsibleTrigger className="py-0 text-xs font-medium uppercase tracking-wide text-fg-muted hover:text-fg">
            Context layers
            {on.length > 0 ? <span className="num font-normal normal-case"> ({on.length} on)</span> : null}
          </CollapsibleTrigger>
        </h2>
        <CollapsibleContent className="mt-2 space-y-2">
          <p className="text-xs text-fg-muted">
            Reference data from the data pack, shown before any check. Rings are indicative, not an official airspace map.
            {synthetic ? " This pack is synthetic demo data." : ""}
          </p>
          {tooWide && on.length > 0 ? (
            <p role="status" className="rounded border border-warn/50 bg-warn/10 p-2 text-xs text-fg">
              Zoom in: context layers load for views up to {MAX_CONTEXT_SPAN_DEG}° × {MAX_CONTEXT_SPAN_DEG}°.
            </p>
          ) : noPack ? (
            <p role="status" className="rounded border border-border p-2 text-xs text-fg-muted">
              No data pack covers this view, so there is no context data here.
            </p>
          ) : null}
          <ul className="space-y-1">
            {CONTEXT_LAYER_KEYS.map((key) => {
              const switchId = `context-toggle-${key}`;
              const isOn = on.includes(key);
              const d = byKey.get(key);
              const hasRows = Boolean(isOn && d?.collection && d.collection.features.length > 0);
              return (
                <li key={key} className="flex items-center gap-2 rounded px-1 py-1 hover:bg-elevated">
                  <Switch id={switchId} checked={isOn} onCheckedChange={() => toggle(key)} />
                  <Label htmlFor={switchId} className="min-w-0 flex-1 cursor-pointer text-sm font-normal">
                    <span className="flex items-center gap-1.5">
                      <span className="truncate">{CONTEXT_LAYER_LABEL[key]}</span>
                      {isOn ? null : <EyeOff aria-hidden className="size-3.5 shrink-0 text-fg-muted" />}
                      <span className="sr-only">{isOn ? "(shown)" : "(off)"}</span>
                    </span>
                    <span className={cn("block text-xs", d?.error && isOn ? "text-danger" : "text-fg-muted")}>{status(key)}</span>
                  </Label>
                  {hasRows ? (
                    <IconButton
                      label={`View ${CONTEXT_LAYER_LABEL[key]} as table`}
                      icon={<Table2 />}
                      size="icon-sm"
                      side="right"
                      onClick={() => onViewTable(contextLayerId(key))}
                    />
                  ) : null}
                </li>
              );
            })}
          </ul>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}
