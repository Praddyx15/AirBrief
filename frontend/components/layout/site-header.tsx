"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Github, LayoutGrid, Search, Settings } from "lucide-react";
import { Kbd } from "@/components/ui/kbd";
import { useUi } from "@/lib/store";
import { cn } from "@/lib/utils";
import { CommandPalette } from "./command-palette";
import { DemoBanner, REPO_URL } from "./demo-banner";
import { DevModeBanner } from "./dev-mode-banner";
import { ThemeSystemSync, ThemeToggle } from "./theme-toggle";
import { Wordmark } from "./wordmark";

const NAV = [
  { href: "/w", label: "Workspace", Icon: LayoutGrid, match: (p: string) => p === "/w" || p.startsWith("/w/") },
  { href: "/settings", label: "Settings", Icon: Settings, match: (p: string) => p.startsWith("/settings") },
] as const;

function useIsMac(): boolean {
  const [isMac, setIsMac] = useState(false);
  useEffect(() => {
    const platform =
      (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ??
      navigator.platform ??
      navigator.userAgent;
    setIsMac(/mac|iphone|ipad|ipod/i.test(platform));
  }, []);
  return isMac;
}

export interface SiteHeaderProps {
  className?: string;
  /** Mount the global command palette (default true). Turn off if the page mounts its own. */
  withCommandPalette?: boolean;
}

/** Top bar for every page except the full-screen workspace, which has its own. */
export function SiteHeader({ className, withCommandPalette = true }: SiteHeaderProps) {
  const pathname = usePathname() ?? "/";
  const setCommandOpen = useUi((s) => s.setCommandOpen);
  const isMac = useIsMac();

  return (
    <>
      <DemoBanner />
      <DevModeBanner />
      <header
        className={cn("sticky top-0 z-overlay border-b border-border bg-bg/95 backdrop-blur-sm", className)}
        data-print="hide"
      >
        <div className="container flex h-14 items-center gap-2">
          <Link
            href="/"
            className="mr-2 flex shrink-0 items-center rounded py-1 pointer-coarse:min-h-11"
            aria-label="AirBrief home"
          >
            <Wordmark />
          </Link>

          <nav aria-label="Main" className="flex min-w-0 items-center gap-0.5">
            <ul className="flex items-center gap-0.5">
              {NAV.map(({ href, label, Icon, match }) => {
                const active = match(pathname);
                return (
                  <li key={href}>
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "inline-flex h-9 items-center justify-center gap-1.5 rounded px-2.5 text-sm font-medium transition-colors duration-200 pointer-coarse:min-h-11 pointer-coarse:min-w-11",
                        active ? "bg-elevated text-fg" : "text-fg-muted hover:bg-elevated hover:text-fg",
                      )}
                    >
                      <Icon className="size-4 shrink-0" aria-hidden />
                      <span className="sr-only sm:not-sr-only">{label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>

          <div className="ml-auto flex items-center gap-1">
            <button
              type="button"
              onClick={() => setCommandOpen(true)}
              aria-haspopup="dialog"
              aria-label="Search and commands"
              aria-keyshortcuts={isMac ? "Meta+K" : "Control+K"}
              className="inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded border border-border bg-surface px-2.5 text-sm text-fg-muted transition-colors duration-200 hover:bg-elevated hover:text-fg pointer-coarse:min-h-11 pointer-coarse:min-w-11 md:w-56 md:justify-start"
            >
              <Search className="size-4 shrink-0" aria-hidden />
              <span aria-hidden className="hidden whitespace-nowrap md:inline md:flex-1 md:text-left">Search…</span>
              <span className="hidden items-center gap-0.5 md:inline-flex" aria-hidden>
                <Kbd>{isMac ? "⌘" : "Ctrl"}</Kbd>
                <Kbd>K</Kbd>
              </span>
            </button>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex size-9 items-center justify-center rounded text-fg-muted transition-colors duration-200 hover:bg-elevated hover:text-fg pointer-coarse:size-11"
            >
              <Github className="size-4" aria-hidden />
              <span className="sr-only">AirBrief on GitHub (opens in a new tab)</span>
            </a>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <ThemeSystemSync />
      {withCommandPalette ? <CommandPalette /> : null}
    </>
  );
}
