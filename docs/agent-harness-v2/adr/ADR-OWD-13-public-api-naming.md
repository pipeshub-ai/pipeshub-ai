# ADR-OWD-13 — Public API surface and naming

## Context

[OWD-13](../one-way-doors.md) sets the public resource names and paths. Integrators, the Slack bot, CI users and MCP/A2A exposure depend on them. The spec proposed `/api/v1/harness/sessions` with the nouns session, turn, event, approval, harness profile and trigger.

## Decision

As amended on 2026-10-26: the harness uses the existing chat API, so the nouns are

- **conversation** = session,
- **run** = turn,
- **interaction** = approval or question,

with conversations at `/api/v1/conversations/{id}/runs…` and `/api/v1/agents/{key}/conversations/…`, and admin at `/api/v1/agent-harness/*`. Kept from the original: versioning under `/api/v1`, no class named `ControlPlane`, and every route documented in `pipeshub-openapi.yaml`, where a mismatch is blocking. A `/api/v1/harness/sessions` alias is added only if an integrator needs it.

This is also the **C12.01 decision**: the routes stay where they are; what is left is the automated drift test.

## As built

Audited at `959c3bda72`. Decision rows: the v2 architecture report §5.2 ("conversation id = session id") and §8 compatibility contract.

- Routes in `backend/nodejs/apps/src/modules/agent-harness/transport/`; spec in `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`.
- Passing in the gate:
  - `backend/nodejs/apps/tests/cohesion/openapi-harness-routes.test.ts` "C-10: agent-harness routes are in the OpenAPI spec (API-01, C12.01: no drift either way)". It runs `Application.configureRoutes()` against stub containers and records every Express mount, so the routes come from `app.ts` itself (the public app and the internal listener), not a list. Over the chat conversations and runs, `/agents/{agentKey}/conversations` and `/agents/{agentKey}/runs`, `/agent-harness/*`, `/control-plane/*` and the MCP hub, it fails on a route missing from the spec, a documented operation with no route, a route that answers every method, or path parameters that do not match the path (237 operations each way at WP-19).
- WP-19 found eight undocumented routes: the Slack bot's scoped-token turn routes (`/conversations/internal/…`, `/agents/{agentKey}/conversations/internal/…`), now documented under the "Internal Conversations" tag.

## Deviations

None against the amended decision. The route-vs-OpenAPI drift test (API-01) landed in WP-19 API-DRIFT (Wave 4).

## Owner amendments

- **2026-10-26** (owner): amended to the chat API nouns above. Recorded in [one-way-doors.md](../one-way-doors.md) under OWD-13.

## Status

**Accepted as amended** (2026-10-26). Built to the amended design and enforced by the drift test (WP-19).
