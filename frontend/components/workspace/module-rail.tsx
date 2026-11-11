"use client";

import * as React from "react";
import Link from "next/link";
import { Home, Settings } from "lucide-react";
import { MODULE_LIST, type ModuleId } from "@/lib/modules";
import { cn } from "@/lib/utils";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { MODULE_ICONS } from "@/components/modules/registry";

export interface ModuleRailProps {
  active: ModuleId;
  /** href that opens a module (keeps d/f, clears a/t) */
  hrefFor: (module: ModuleId) => string;
  /** called on activation (e.g. to open the inspector sheet on tablets) */
  onNavigate?: (module: ModuleId) => void;
  className?: string;
}

const itemClass =
  "group flex w-full cursor-pointer flex-col items-center gap-0.5 rounded px-1 py-1.5 text-center text-2xs font-medium no-underline transition-colors duration-200 pointer-coarse:min-h-11";

/**
 * The left rail: one link per module (icon + label), plus Home and Settings. On short screens
 * it scrolls by itself, so every item stays reachable and focusing one never scrolls the page.
 */
export function ModuleRail({ active, hrefFor, onNavigate, className }: ModuleRailProps) {
  return (
    <nav
      aria-label="Workspace modules"
      className={cn(
        "flex min-h-0 w-rail shrink-0 flex-col items-stretch gap-1 overflow-y-auto border-r border-border bg-surface px-1 py-2 scrollbar-thin",
        className,
      )}
    >
      <SimpleTooltip content="All datasets" side="right">
        <Link href="/w" className={cn(itemClass, "text-fg-muted hover:bg-elevated hover:text-fg")}>
          <Home aria-hidden className="size-5" />
          Home
        </Link>
      </SimpleTooltip>
      <span aria-hidden className="mx-2 my-1 h-px bg-border" />
      <ul className="flex flex-col gap-1">
        {MODULE_LIST.map((m) => {
          const Icon = MODULE_ICONS[m.icon];
          const isActive = m.id === active;
          return (
            <li key={m.id}>
              <SimpleTooltip content={m.description} side="right">
                <Link
                  href={hrefFor(m.id)}
                  replace
                  scroll={false}
                  aria-current={isActive ? "page" : undefined}
                  onClick={() => onNavigate?.(m.id)}
                  className={cn(
                    itemClass,
                    isActive
                      ? "bg-accent/15 text-fg shadow-[inset_2px_0_0_rgb(var(--accent-rgb))]"
                      : "text-fg-muted hover:bg-elevated hover:text-fg",
                  )}
                >
                  <Icon aria-hidden className={cn("size-5", isActive ? "text-accent" : "text-fg-muted group-hover:text-fg")} />
                  <span className="leading-tight">{m.label}</span>
                </Link>
              </SimpleTooltip>
            </li>
          );
        })}
      </ul>
      <div className="mt-auto">
        <SimpleTooltip content="Units, theme, basemap and FlightMD" side="right">
          <Link href="/settings" className={cn(itemClass, "text-fg-muted hover:bg-elevated hover:text-fg")}>
            <Settings aria-hidden className="size-5" />
            Settings
          </Link>
        </SimpleTooltip>
      </div>
    </nav>
  );
}
