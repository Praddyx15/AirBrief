# ADR-0006: Dark-first, data-dense "Geographic" UI with Geist and a sky-blue accent

- **Status:** Accepted; the colour values were later replaced by the design tokens shared with FlightMD
- **Date:** 2026-10-08
- **Milestone:** M3

## Context

AirBrief is a planning and analysis tool, used at a desk and sometimes outdoors in daylight. It
shows maps, dense tables, profiles and timelines side by side. FlightMD already uses dark neutrals and Geist.

## Decision

- **Style:** a data-dense dashboard in a "Geographic" layout: rail, layers and features list, map,
  inspector, and a results drawer.
- **Theme:** dark first (`--bg #0A0A0A`, text `#F5F5F2`), with a complete light theme for daylight
  field use (`--bg #F8FAFC`, text `#0F172A`).
- **Accent:** sky blue (`--accent #3B82F6`, buttons `#2563EB`, links on dark `#60A5FA`).
- **Status colours:** warn `#D97706`, danger `#EF4444`, ok `#22C55E`, always with an icon and a label.
- **Type:** Geist Sans for the UI; Geist Mono with tabular numbers for values, coordinates and units.
- **Accessibility is a requirement:**
  - visible focus, keyboard map control and skip links
  - reduced-motion support
  - hatch patterns plus labels for zones (never colour alone)
  - a table view for every map layer
  - 44 px touch targets
  - axe-core in CI, failing on serious or critical issues
- **Components:** shadcn-style components on Radix primitives; Lucide icons; no emoji.
- **Routes** are query-param based (`/w/view?d=...`, `/w/report?d=&a=`) so the same build works as a
  static export for the GitHub Pages demo.

## Alternatives considered

| Option | Why not |
|---|---|
| **HUD / sci-fi** (suggested for drone fleets) | High accessibility risk (low contrast, glow, motion) and the wrong metaphor: this is a planning tool, not a cockpit. |
| **Fira Sans / Fira Code** (the first suggestion) | Fine fonts, but inconsistent with FlightMD; Geist keeps the suite coherent. |
| **Light only** | Worse for long map sessions and inconsistent with the suite. Light remains a full theme. |
| **Path-segment routes** (`/w/[datasetId]`) | Cannot be pre-rendered for unknown ids in a static export. |

## Consequences

- One token set drives both themes, and a unit test checks the contrast of every text/background
  pair.
- Map colour ramps are chosen with colour-vision deficiencies in mind (viridis, RdBu, hatched zones).
- Dense layouts need responsive fallbacks: the inspector becomes a sheet below 1024 px, and the app
  becomes a read-only viewer below 768 px.
