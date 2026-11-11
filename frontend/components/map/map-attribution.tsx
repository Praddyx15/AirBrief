"use client";

/**
 * Map data attribution, rendered by the app instead of MapLibre's control so that it is themed,
 * sits last in the overlay tab order (after the controls, legend and readout), and shares the
 * bottom-right stack with the coordinate readout instead of floating over the scale bar.
 *
 * Wide maps show it inline. Narrow maps show it on load and then fold it behind an (i) button
 * (after a few seconds or the first pan / zoom), as the OpenStreetMap attribution guidelines
 * allow for small maps; the button brings it back.
 */

import * as React from "react";
import { Info } from "lucide-react";
import { cn } from "@/lib/utils";
import { IconButton } from "@/components/ui/icon-button";

/** One run of attribution text, optionally a link. */
export interface AttributionPart {
  text: string;
  href?: string;
}

/** Always credited, as MapLibre's own control does. */
const MAPLIBRE_CREDIT: AttributionPart[] = [{ text: "MapLibre", href: "https://maplibre.org/" }];

/**
 * Turn the HTML attribution strings of the map's sources into plain text runs and http(s) links.
 * Nothing from the (remote) style is ever inserted as HTML.
 */
export function parseAttributions(sources: readonly string[]): AttributionPart[][] {
  // MapLibre's rule: drop strings that are part of a longer one, shortest first.
  const unique = [...new Set(sources.map((s) => s.trim()).filter(Boolean))].sort((a, b) => a.length - b.length);
  const kept = unique.filter((s, i) => !unique.slice(i + 1).some((longer) => longer.includes(s)));
  const parser = typeof DOMParser !== "undefined" ? new DOMParser() : null;
  const groups: AttributionPart[][] = [];
  for (const html of kept) {
    const parts: AttributionPart[] = [];
    const push = (text: string, href?: string) => {
      const collapsed = text.replace(/\s+/g, " ");
      const clean = parts.length === 0 ? collapsed.trimStart() : collapsed;
      if (!clean.trim()) {
        if (parts.length > 0 && clean) parts.push({ text: " " });
        return;
      }
      parts.push(href ? { text: clean.trim(), href } : { text: clean });
    };
    const walk = (node: Node) => {
      node.childNodes.forEach((child) => {
        if (child.nodeType === Node.TEXT_NODE) {
          push(child.textContent ?? "");
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          const el = child as Element;
          if (el.tagName === "A") {
            const href = el.getAttribute("href") ?? "";
            push(el.textContent ?? "", /^https?:\/\//i.test(href) ? href : undefined);
          } else {
            walk(el);
          }
        }
      });
    };
    if (parser) walk(parser.parseFromString(`<body>${html}</body>`, "text/html").body);
    else push(html.replace(/<[^>]*>/g, " "));
    while (parts.length > 0 && !(parts[parts.length - 1] as AttributionPart).text.trim()) parts.pop();
    if (parts.length > 0) groups.push(parts);
  }
  return groups;
}

function AttributionText({ groups }: { groups: readonly AttributionPart[][] }) {
  const all = [...groups, MAPLIBRE_CREDIT];
  return (
    <>
      {all.map((parts, g) => (
        <React.Fragment key={g}>
          {g > 0 ? <span aria-hidden> | </span> : null}
          {parts.map((part, i) =>
            part.href ? (
              <a
                key={i}
                href={part.href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-fg-muted underline decoration-fg-muted/50 underline-offset-2 hover:text-fg hover:decoration-fg"
              >
                {part.text}
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            ) : (
              <React.Fragment key={i}>{part.text}</React.Fragment>
            ),
          )}
        </React.Fragment>
      ))}
    </>
  );
}

export interface MapAttributionProps {
  groups: readonly AttributionPart[][];
  /** fold behind an (i) button (narrow map) */
  collapsible: boolean;
  /** shown while collapsible */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** keeps the text clear of the scale bar */
  maxWidth?: number;
  className?: string;
}

export function MapAttribution({ groups, collapsible, open, onOpenChange, maxWidth, className }: MapAttributionProps) {
  const textId = React.useId();
  const textClass = "rounded border border-border bg-surface/95 px-1.5 py-0.5 text-2xs text-fg-muted";

  if (!collapsible) {
    return (
      <p data-map-overlay="attribution" className={cn("pointer-events-auto", textClass, className)} style={{ maxWidth }}>
        <AttributionText groups={groups} />
      </p>
    );
  }

  return (
    <div data-map-overlay="attribution" className={cn("pointer-events-auto relative flex justify-end", className)}>
      <div className="rounded-full border border-border bg-surface/95">
        <IconButton
          label="Map attribution"
          icon={<Info />}
          size="icon-sm"
          side="top"
          className="size-6 rounded-full text-fg-muted hover:text-fg [&_svg]:size-3.5"
          aria-expanded={open}
          aria-controls={textId}
          onClick={() => onOpenChange(!open)}
        />
      </div>
      {/* Out of flow: opening it never moves the readout or the controls. */}
      <p
        id={textId}
        hidden={!open}
        className={cn("absolute bottom-0 right-full mr-1 w-max", textClass)}
        // minus the button (44 px on touch) and its gap
        style={maxWidth !== undefined ? { maxWidth: Math.max(96, maxWidth - 48) } : undefined}
      >
        <AttributionText groups={groups} />
      </p>
    </div>
  );
}
