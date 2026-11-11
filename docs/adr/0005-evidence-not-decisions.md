# ADR-0005: AirBrief provides evidence; UAOP makes decisions

- **Status:** Accepted
- **Date:** 2026-10-08
- **Milestone:** M4

## Context

Airspace permission, SORA ground-risk classes and terrain feasibility are regulatory decisions.
UAOP already owns mission planning and a compliance engine. If AirBrief also issued decisions, the
suite would have two sources of truth, and an illustrative map could be mistaken for an official
one. In India the authority is DGCA Digital Sky.

## Decision

1. AirBrief outputs **evidence and geodata**:
   - zones touched under configurable, illustrative rules
   - distances to aerodromes, and terrain and obstacle clearances
   - population-density statistics, and an illustrative intrinsic ground-risk class with its table
     reference
   - survey effort figures, show conflicts and flight exceedances
2. Each result has a **verdict** (`pass` / `caution` / `fail`) that summarises the evidence for a
   human. It is never a permission.
3. Every airspace, ground-risk and eVTOL result carries a disclaimer (`DISCLAIMER_*` in
   `airbrief_core/models/presets.py`), and the UI shows it.
4. Zone rules (`ZoneRules`) and the SORA-style table are data, not code, so they can be replaced.
5. AirBrief never exports waypoints or missions. The survey estimator draws lines for display only.
6. UAOP consumes the `uaop.airspace.v1` protobuf messages and the events
   `uaop.event.airspace.assessment_completed/1` and `uaop.event.airspace.flight_audit_completed/1`.

## Alternatives considered

| Option | Why not |
|---|---|
| **A full SORA calculator in AirBrief** (iGRC to final GRC, ARC, SAIL, OSOs) | Duplicates UAOP's compliance engine; mitigations and operational context live in UAOP. |
| **A terrain-feasibility pass/fail per mission** | Mission geometry and aircraft performance belong to UAOP. |
| **No verdicts at all** | Raw numbers are harder to scan. A labelled summary helps users, as long as it is clearly not a permission. |

## Consequences

- Ownership across the suite is clear, and no regulatory logic is duplicated.
- Users must take the final step (the official map, the operator's assessment) themselves, and the
  product says so plainly.
- The contracts must carry enough provenance (data pack, synthetic flag, versions) for UAOP to judge
  the evidence. `AnalysisProvenance` does this.
