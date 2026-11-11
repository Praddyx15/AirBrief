# ADR-0008: Anonymous capability model with owner tokens, scoped listings and default retention

- **Status:** Accepted
- **Date:** 2026-10-08
- **Milestone:** M8 (security and privacy review)

## Context

AirBrief is meant to be safe to run as a public, anonymous, zero-cost service. Two code reviews found:
- **Privacy:** listing endpoints returned every visitor's datasets and analyses. Flight audits include GPS tracks.
- **Integrity:** anyone who knew an id (for example from a shared link) could delete or cancel the item.
- **Robustness:** request bodies were unbounded, geometry complexity was unbounded, and internal error details leaked in responses.

Accounts would solve the first two but add a user database, password handling and recovery flows. That is heavy for a tool with no other reason to know who its users are.

## Decision

- **Capability ids:** dataset and analysis ids are random UUIDs. Holding an id grants **read** access. This is how sharing works.
- **Owner tokens:** every created item gets a random 256-bit token.
  - The token is returned once and stored server-side only as SHA-256.
  - It is kept by the creating browser, and sent as `X-Owner-Token`.
  - It is required for DELETE and cancel when `REQUIRE_OWNER_TOKEN` is on (default in production).
  - Tokens are compared in constant time.
- **Scoped listings:** with `ALLOW_GLOBAL_LISTING` off (default in production), lists are only available by `?ids=` (the browser's own history) or `?dataset_id=`.
- **Retention:** `UPLOAD_TTL_HOURS` defaults to 72 h in production.
- **Limits and hygiene:**
  - streaming body-size limits, and a total-vertex limit per file
  - generic error messages (details only in logs)
  - security headers and a CSP
  - the FlightMD fetch only returns valid reports, so it cannot be used as a general proxy

## Alternatives considered

| Option | Why not |
|---|---|
| User accounts | Needs a user database, credentials and recovery; collects personal data the tool otherwise never needs |
| Signed share links (separate read tokens) | Ids are already unguessable; a second secret per item adds little over owner tokens and complicates sharing |
| Everything public, delete open to all | Unsafe on a public instance: anyone with a link could delete other people's work |
| Strict mode everywhere (also in development) | Friction for the single-user, self-hosted case; the settings make the trade-off explicit instead |

## Consequences

- **Lost tokens:** clearing browser data loses them. Those items can no longer be deleted by the user and expire with the TTL.
- **Derived data follows its source:** deleting (or expiring) a dataset deletes every analysis run on it, including analyses other visitors ran from a shared link and hold tokens for. The uploader's right to remove their data, and what was computed from it, wins over the analysis creator's.
- **Read access by link:** anyone with a link can still read the item, so the docs tell users not to upload sensitive data to public instances.
- **Development stays frictionless:** listings and deletes are open there, and production is safe without extra configuration.
- **Where it is documented:** `SECURITY.md`, `DEPLOYMENT.md` §8 and `RUNBOOK.md` §5.5.
