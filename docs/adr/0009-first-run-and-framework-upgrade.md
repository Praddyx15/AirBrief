# ADR-0009: Fixes from the first full run, and the move to Next.js 15, React 19 and MapLibre 6

- **Status:** Accepted
- **Date:** 2026-10-10
- **Milestone:** 9 (first full run)

## Context

AirBrief was first installed and run end to end on 2026-10-10. The run covered:
- the Python suite, mypy and ruff;
- the TypeScript check, ESLint and the production build;
- Playwright with axe;
- the RUNBOOK §5.5 security checks against a production-mode API;
- a screenshot review of every screen in both themes and at four widths;
- a full demo-video render.

The run found real bugs that static review had missed. It also found that the frontend dependencies had critical published advisories:
- **Next.js 14.2.35 is end-of-life.** Advisories GHSA-p293-qw3h-jr36 (unauthenticated remote code execution on Windows-hosted servers) and GHSA-2xp9-vwfh-vxw4 (remote code execution in the image optimiser) are fixed only from 15.5.24.
- **MapLibre GL ≤ 6.4.0** has an XSS sanitiser bypass (GHSA-jrc7-96c5-q579).

## Decision

**Upgrade the frontend:**
- Next.js 15.5 with React 19.
- MapLibre GL 6. Its worker file is copied into `public/vendor/` before every build by `scripts/copy-maplibre-worker.mjs`, and set with `setWorkerUrl`, because bundling moves it.
- deck.gl 9.4.
- `@deck.gl/mesh-layers` removed (unused).
- npm `overrides` for PostCSS, fflate, image-size and argparse, which deck.gl's 3D-tile loaders and Next pull in.

Production dependencies now audit clean. Linting uses the ESLint CLI, because `next lint` is deprecated.

**Next.js 16 was not chosen.** It removes `next lint` and changes more APIs, and it fixes nothing that 15.5 does not.

## Bugs found and fixed in the run

**Map**
- The map was invisible: zero height, because `maplibre-gl.css` sets `position: relative` on the element the app had positioned absolutely.
- Data with altitudes (KML Z, FlightMD tracks, drone positions) floated hundreds of metres above the flat basemap. In perspective that enlarged and shifted it, so layers draw flat in 2D.
- Fitting ignored the map controls on the right edge. Results, such as the flight-audit exceedance, ended up underneath them.

**Rendering**
- A hydration error on `/w`: a loading skeleton `<div>` inside a `<p>`.
- A WebGL warning on every airspace result: an empty text layer built an empty font atlas.

**Builds and robustness**
- Builds on Node 20 (CI) would crash during prerendering. The settings store had no storage on the server, so its `persist` API was missing. A browser with blocked storage would crash the same way.

**Backend**
- Crash text from analysis modules still reached API responses, because the orchestrator wrapped it as an ordinary error.
- A false "photos too fast" warning: 30 m at 15 m/s computed as 1.9999 s.

**Accessibility**
- A tab pointed (`aria-controls`) at a panel that did not exist while the results drawer was closed.
- The coordinate readout partly covered the attribution button (target size).

**UI**
- Raw PROJ strings were shown as the "CRS used".
- Units overlapped values in number inputs.
- Toasts covered the results.
- The drawer was cramped.
- Labels and placeholders were truncated.
- An unknown dataset showed "The file contains no features".

**Demo video pipeline**
- Frame timing stretched clips whenever Chrome sent more than 30 frames per second.
- Late frames were lost at the end of a scene.
- Scenes ended before their last action could be seen.
- The output was tagged as full-range video.

## Follow-up review (same day)

A code review of these changes found, and the following fixed and re-tested:

**Heights on the map**
- One rule in the layer factories (`withHeights` in `components/map/layers/common.ts`): flat in 2D; in 3D, heights above the ground, using the take-off point for flights and the origin for drone shows.
- This replaced three different ad hoc rules. Under them the take-off marker still floated, 3D lost its heights, and the drone tooltip read 0 m.

**Error text**
- Error messages no longer carry server paths or library text. This covers:
  - the orchestrator's internal error, which now also covers invalid module results;
  - data-pack read errors;
  - shapefile and upload OS errors;
  - per-feature crashes;
  - FlightMD report parse failures.
- The details go to the server log.

**Robustness**
- The settings store survives storage that cannot be written to.
- The demo export is built in a staging folder and swapped in by renames. A failed run keeps the previous export, and read-only folders on Windows no longer stop it.

**Tooling**
- `npm run typecheck` runs `next typegen` first, so it works on a clean checkout.
- ESLint skips the copied MapLibre worker.
- `seed/` joined the mypy check.

## Consequences

- **Node:** the frontend needs Node ≥ 18.18, and CI uses Node 20. React 19's stricter ref types meant small type changes.
- **Dev-only audit findings remain:** Tailwind 3's watcher and ESLint's glob matching (braces, micromatch). They only process the project's own files. Clearing them means Tailwind 4, which is a separate migration.
- **Headless video renders:** software WebGL cannot paint the map in real time, so the video pipeline records in a real off-screen window (`OFFSCREEN=1`).
