# ADR-0004: H3 hexagonal grids for gridded analyses

- **Status:** Accepted
- **Date:** 2026-10-08
- **Milestone:** M4

## Context

Several modules turn an area into cells and score each cell: the airspace flyability grid,
ground-risk density sampling, eVTOL vertiport siting, the corridor-routing graph and the survey work
blocks. The grid needs:

- uniform neighbours, so a "k-ring" radius and graph routing do not depend on direction;
- roughly equal cell areas across the study area;
- stable, shareable cell ids for tables, exports and the frontend;
- a fast, permissively licensed implementation in Python and in the browser.

## Decision

Use **Uber H3 (v4 API, Apache-2.0)**: the `h3` Python package (>= 4.1, < 5) in the core, and
deck.gl's `H3HexagonLayer` in the frontend.

- Resolutions are parameters with sensible defaults: airspace 9, ground risk 8, eVTOL 8.
- Every module caps the number of cells (`max_cells`) and reports truncation.
- Distances between cell centres use the geodesic, not cell steps.
- The helpers live in `airbrief_core/geo/h3grid.py`.

## Alternatives considered

| Option | Why not |
|---|---|
| **Square grids in a local projection** | Four-versus-eight neighbour ambiguity; diagonal steps are 41% longer, which biases routing and k-ring sums; ids are not global. |
| **Geohash or S2** | Geohash cells are rectangles whose shape varies strongly with latitude; S2 cells are quadrilaterals with the same neighbour problem, and the browser tooling is thinner. |
| **Raster cells (the DEM grid)** | Ties the analysis resolution to whichever raster is loaded, so it varies between data packs. |

## Consequences

- Six equidistant neighbours give direction-independent k-rings and cleaner least-cost paths.
- Cell ids are global strings, so results can be joined across analyses and drawn directly by
  deck.gl.
- Hexagons do not nest exactly across resolutions. AirBrief never aggregates across resolutions,
  so this does not matter here.
- Cell area varies slightly with location (a few percent within a city). Where area matters
  (population integration), the true `cell_area` is used instead of a nominal value.
